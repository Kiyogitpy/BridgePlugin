"""Tests for routines/deep_sea_trawling.py. Hermetic: no bridge, no serial, no client."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import controller
import trawling_routes

import deep_sea_trawling as dst


def _snap(**kw):
    base = dict(px=1580, py=3365, plane=0, boat_speed=0.0, navigating=True,
                net={"id": 59755, "x": 1580, "y": 3365, "plane": 0, "distance": 2},
                net_depth=1,
                shoal={"id": 59734, "x": 1580, "y": 3365, "plane": 0}, shoal_distance=3.0,
                net_fish=10, cargo_used=5, cargo_capacity=240,
                ticks_since_sailing_xp=0.0, in_route_area=True)
    base.update(kw)
    return dst.TrawlSnapshot(**base)


CFG = dict(dst.DEFAULT_CONFIG)


def test_derive_phase_truth_table():
    d = dst.derive_phase
    assert d(_snap(), CFG) == "trawl"
    # No net facility on the boat at all, or the container does not exist: we are not aboard.
    assert d(_snap(net=None), CFG) == "lost"
    assert d(_snap(net_fish=None), CFG) == "lost"
    # No shoal in the scene -> sail to the route.
    assert d(_snap(shoal=None, shoal_distance=None), CFG) == "travel"
    # Out of parking range, or still moving: reposition.
    assert d(_snap(shoal_distance=30.0), CFG) == "approach"
    assert d(_snap(boat_speed=1.4), CFG) == "approach"
    # In range and stopped, but the net is up.
    assert d(_snap(net_depth=0), CFG) == "lower"
    # Net at the collect threshold beats everything except lost/done.
    assert d(_snap(net_fish=CFG["netFullAt"]), CFG) == "collect"
    assert d(_snap(net_fish=CFG["netFullAt"], shoal_distance=30.0), CFG) == "collect"
    # Cargo hold full and nothing left in the nets -> done.
    assert d(_snap(cargo_used=240, net_fish=0), CFG) == "done"
    # Cargo hold full but fish still in the nets -> collect them first; the collect itself
    # discovers the hold is full and the next tick reads done.
    assert d(_snap(cargo_used=240, net_fish=10), CFG) == "collect"


def test_derive_phase_travel_outside_the_route_area_is_lost():
    assert dst.derive_phase(_snap(shoal=None, shoal_distance=None, in_route_area=False),
                            CFG) == "lost"


def test_get_config_ignores_the_bridge_config_and_forces_the_inventory_safety_off():
    cfg = dst.get_config({"anything": 1})
    assert cfg["shoal"] == "giant_krill"
    assert cfg["route"] == "great_sound"
    assert cfg["safety"]["stopOnInventoryFull"] is False


def test_shoal_ids_include_the_mixed_shoal():
    assert dst.shoal_ids(CFG) == (59734, 59740)


NET_OBJ = {"id": 59755, "name": "Rope trawling net", "x": 1580, "y": 3365, "plane": 0,
           "distance": 2, "screenX": 700, "screenY": 400}
SHOAL_OBJ = {"id": 59734, "name": "<col=FFFF00>Giant krill shoal</col>",
             "x": 1583, "y": 3367, "plane": 0, "screenX": 900, "screenY": 300}


def test_find_net_matches_by_id_not_by_name():
    """The name carries a colour tag on the shoal and could carry one anywhere, so ids come
    first; the name fragment is only the fallback for a net variant we have not tabulated."""
    assert dst.find_net([{"id": 1}, NET_OBJ]) is NET_OBJ
    odd = {"id": 99999, "name": "Driftwood trawling net", "x": 1, "y": 2, "plane": 0}
    assert dst.find_net([odd]) is odd
    assert dst.find_net([{"id": 1, "name": "Helm"}]) is None


def test_find_shoal_picks_the_nearest_matching_id_and_reports_its_distance():
    far = dict(SHOAL_OBJ, x=1620, y=3390)
    shoal, dist = dst.find_shoal([far, SHOAL_OBJ], CFG, 1580, 3365)
    assert shoal is SHOAL_OBJ
    assert round(dist, 3) == round(((3 ** 2) + (2 ** 2)) ** 0.5, 3)


def test_find_shoal_accepts_the_mixed_shoal_that_replaces_ours():
    mixed = dict(SHOAL_OBJ, id=59740, name="<col=FFFF00>Shimmering shoal</col>")
    shoal, _dist = dst.find_shoal([mixed], CFG, 1580, 3365)
    assert shoal is mixed


def test_find_shoal_ignores_another_shoal_type():
    other = dict(SHOAL_OBJ, id=59736, name="<col=FFFF00>Yellowfin shoal</col>")
    assert dst.find_shoal([other], CFG, 1580, 3365) == (None, None)


def test_read_snapshot_folds_the_bridge_into_one_record(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1580, "y": 3365, "plane": 0})
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None: {"boatSpeed": 0.0, "navigating": True}
                        if path == "/sailing" else None)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [NET_OBJ, SHOAL_OBJ])
    monkeypatch.setattr(controller, "get_trawling",
                        lambda: {"netFish": 12, "cargo": {"used": 3, "capacity": 240},
                                 "nets": [{"slot": 0, "depth": 1, "hotspotId": 3},
                                          {"slot": 1, "depth": 0, "hotspotId": 0}]})

    snap = dst.read_snapshot(object(), CFG, ticks_since_sailing_xp=2.0)
    assert snap.net is NET_OBJ
    assert snap.shoal is SHOAL_OBJ
    assert snap.net_fish == 12
    assert snap.net_depth == 1          # the built net's slot, not slot 0 blindly
    assert snap.cargo_used == 3
    assert snap.navigating is True
    assert snap.in_route_area is True
    assert snap.ticks_since_sailing_xp == 2.0
    assert dst.derive_phase(snap, CFG) == "trawl"


def test_read_snapshot_reports_none_not_zero_when_the_net_container_is_missing(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1580, "y": 3365, "plane": 0})
    monkeypatch.setattr(controller, "api_get", lambda path, params=None: {})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_trawling", lambda: {})
    snap = dst.read_snapshot(object(), CFG)
    assert snap.net_fish is None
    assert snap.net is None
    assert dst.derive_phase(snap, CFG) == "lost"


# --------------------------------------------------------------------------- trawl_burst

import bridge_events
import outcome_waits


class _State:
    def __init__(self):
        self.data = {}
        self.config = dict(dst.DEFAULT_CONFIG)
        self.safety_stopped = False
        self.safety_reason = None
        self.paused = False

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason

    def break_due(self, breaks_cfg):
        return False


class _Ctx:
    def __init__(self, events):
        self.events = events
        self.ser = object()
        self.state = _State()


def _burst_ctx(monkeypatch, tick_events):
    """A real EventStream fed by a scripted poll_udp: tick_events[i] is the list of
    (type, payload) delivered on the i-th poll. A real stream is used so await/since/mark
    semantics are what gets exercised, not a stand-in."""
    queue = list(tick_events)

    def poll_udp(_sock):
        return list(queue.pop(0)) if queue else []

    monkeypatch.setattr(controller, "poll_udp", poll_udp)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    clock = {"t": 1000.0}
    monkeypatch.setattr(dst, "_now", lambda: clock["t"])
    monkeypatch.setattr(dst, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    ctx = _Ctx(bridge_events.EventStream(sock=None))
    ctx.clock = clock
    return ctx


def _stub_burst_io(monkeypatch, clicks, net_fish=lambda: 10, player_reads=None,
                   pump_on_wait=False):
    """pump_on_wait=True installs a tick_pause stub that also pumps the real EventStream before
    advancing the fake clock -- i.e. it behaves like the real wait_ticks() (which pumps mid-wait)
    instead of the bare-sleep default. Only the mark/since regression test needs this; every
    other test's plain bare-sleep stub is faithful enough (see that test's own docstring)."""
    monkeypatch.setattr(controller, "click_human", lambda ser: clicks.append(1))
    monkeypatch.setattr(controller, "hover_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "get_trawling", lambda: {"netFish": net_fish()})

    def _get_player():
        if player_reads is not None:
            player_reads.append(1)
        return {"x": 1580, "y": 3365, "plane": 0}

    monkeypatch.setattr(controller, "get_player", _get_player)
    monkeypatch.setattr(dst, "_click_net_to_stand_at_it", lambda ctx, net: True)
    monkeypatch.setattr(dst, "_dismiss_dialogue_if_open", lambda ctx: False)

    if pump_on_wait:
        def _tick_pause_pumping(ctx, n=1, phase=None, **kw):
            events = getattr(ctx, "events", None)
            if events is not None:
                events.pump()
            dst._sleep(0.6 * n)
        monkeypatch.setattr(outcome_waits, "tick_pause", _tick_pause_pumping)
    else:
        monkeypatch.setattr(outcome_waits, "tick_pause",
                            lambda ctx, n=1, phase=None, **kw: dst._sleep(0.6 * n))


def _no_events_ctx(monkeypatch):
    """A burst context with NO EventStream at all -- the fallback mode Critical 1's review
    found had zero coverage."""
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    clock = {"t": 1000.0}
    monkeypatch.setattr(dst, "_now", lambda: clock["t"])
    monkeypatch.setattr(dst, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    return _Ctx(None)


XP_SAIL = ("XP_GAINED", {"skill": "Sailing", "xp": 7})
XP_FISH = ("XP_GAINED", {"skill": "Fishing", "xp": 112})
MOVING = ("PLAYER_MOVING", {})


def test_burst_clicks_once_per_tick_while_the_heartbeat_keeps_arriving(monkeypatch):
    # Sailing xp every other poll -- the 2-tick roll cadence a 1-tick click produces.
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL], [], [XP_SAIL, XP_FISH], [], [XP_SAIL], []] +
                     [[]] * 20)
    clicks = []
    _stub_burst_io(monkeypatch, clicks)
    monkeypatch.setattr(dst, "RANGE_LOST_TICKS", 6)

    result = dst.trawl_burst(ctx, CFG, NET_OBJ)

    assert result == "out_of_range"          # the heartbeat stops once the script runs dry
    assert len(clicks) >= 6                  # one per tick until then, not one per roll
    assert ctx.state.data["dst_catches"] >= 1


def test_burst_returns_net_full_at_the_configured_threshold(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL]] * 60)
    clicks = []
    _stub_burst_io(monkeypatch, clicks, net_fish=lambda: CFG["netFullAt"])
    assert dst.trawl_burst(ctx, CFG, NET_OBJ) == "net_full"


def test_burst_advances_its_event_mark_and_so_never_reads_the_same_drop_twice(monkeypatch):
    """The C2 rule: mark is taken ONCE before the loop and advanced after every read. If it were
    re-taken at the top of each iteration the events delivered during the tick wait would be
    skipped and the heartbeat would look dead on the very first idle poll.

    Uses pump_on_wait=True so tick_pause pumps the stream mid-wait, the same as the real
    wait_ticks() does -- a bare-sleep tick_pause stub (the default) never pumps between
    iterations at all, so it cannot exercise the "event arrived during the wait" case this test
    exists to pin (see the review finding this test was rewritten for)."""
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL, XP_SAIL], [XP_SAIL]] + [[]] * 20)
    clicks = []
    _stub_burst_io(monkeypatch, clicks, pump_on_wait=True)
    monkeypatch.setattr(dst, "RANGE_LOST_TICKS", 3)
    dst.trawl_burst(ctx, CFG, NET_OBJ)
    # Three Sailing drops were delivered; each must have been counted exactly once.
    assert ctx.state.data["dst_rolls"] == 3


def test_burst_with_no_event_stream_uses_stalled_net_count_as_out_of_range(monkeypatch):
    """Critical 1: with ctx.events is None the Sailing-xp heartbeat never arrives at all, so the
    burst needs its OWN out-of-range signal -- a net count that stops growing. Without this the
    no-events branch was a dead end that could only run to BURST_MAX_S and return "budget"."""
    ctx = _no_events_ctx(monkeypatch)
    clicks = []
    _stub_burst_io(monkeypatch, clicks, net_fish=lambda: 10)   # never grows
    monkeypatch.setattr(dst, "NO_EVENTS_STALL_TICKS", 24)
    result = dst.trawl_burst(ctx, CFG, NET_OBJ)
    assert result == "out_of_range"
    assert len(clicks) >= 24


def test_burst_with_no_event_stream_and_a_growing_net_count_keeps_going(monkeypatch):
    ctx = _no_events_ctx(monkeypatch)
    clicks = []
    count = {"n": 9}

    def growing():
        count["n"] += 1
        return count["n"]

    _stub_burst_io(monkeypatch, clicks, net_fish=growing)
    monkeypatch.setattr(dst, "NO_EVENTS_STALL_TICKS", 16)
    monkeypatch.setattr(dst, "BURST_MAX_S", 24.0)
    result = dst.trawl_burst(ctx, CFG, NET_OBJ)
    assert result == "budget"          # never falsely declared out_of_range
    assert len(clicks) >= 16


def test_burst_stops_for_a_safety_stop(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL]] * 60)
    clicks = []
    _stub_burst_io(monkeypatch, clicks)

    def stop_soon(ser):
        clicks.append(1)
        if len(clicks) == 4:
            ctx.state.trigger_safety_stop("test")
    monkeypatch.setattr(controller, "click_human", stop_soon)

    assert dst.trawl_burst(ctx, CFG, NET_OBJ) == "interrupted"
    assert len(clicks) <= 6


def test_burst_reaims_when_the_player_has_moved(monkeypatch):
    """Records the CLICK COUNT at the moment of each hover call, not just a tally -- a bare
    `len(hovers) >= 2` passes for far too much. The exact sequence [0, 3] is the pre-loop aim,
    then the ONE re-aim on the iteration whose event batch carried PLAYER_MOVING; iterations
    either side must not re-aim, and AIM_REFRESH_TICKS (12) is not reached inside this burst.

    The movement trigger is the PLAYER_MOVING / PLAYER_STOPPED event now, not a /player poll
    (F-I8) -- see the read-budget test below for why."""
    ctx = _burst_ctx(monkeypatch,
                     [[XP_SAIL], [XP_SAIL], [XP_SAIL, MOVING]] + [[XP_SAIL]] * 20)
    clicks = []
    hovers = []
    _stub_burst_io(monkeypatch, clicks)
    monkeypatch.setattr(controller, "hover_entity",
                        lambda *a, **k: hovers.append(len(clicks)) or True)
    monkeypatch.setattr(dst, "BURST_MAX_S", 6.0)
    dst.trawl_burst(ctx, CFG, NET_OBJ)
    assert hovers == [0, 3]


def test_burst_reaims_on_the_schedule_even_with_nothing_moving(monkeypatch):
    """The other half of the re-aim contract: with no movement event at all the camera can still
    drift, so AIM_REFRESH_TICKS re-aims regardless. Pinned exactly (pre-loop, then every 4th
    tick with AIM_REFRESH_TICKS forced to 4) so removing the schedule -- or making it fire every
    tick -- both fail."""
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL]] * 60)
    clicks = []
    hovers = []
    _stub_burst_io(monkeypatch, clicks)
    monkeypatch.setattr(controller, "hover_entity",
                        lambda *a, **k: hovers.append(len(clicks)) or True)
    monkeypatch.setattr(dst, "AIM_REFRESH_TICKS", 4)
    monkeypatch.setattr(dst, "BURST_MAX_S", 6.0)
    dst.trawl_burst(ctx, CFG, NET_OBJ)
    assert hovers == [0, 4, 8]


def test_burst_does_not_spend_an_http_player_read_every_tick(monkeypatch):
    """F-I8. The spec's own choice #4 is "the burst must not spend its tick budget on HTTP", and
    the burst then called /player (15-120ms measured) on EVERY iteration on top of the ~92ms
    click and its phase pad. Cross the 600ms boundary and wait_ticks(1) aligns to the NEXT one,
    skipping a click -- which drops that window's roll cadence from 2 ticks back toward 3, i.e.
    exactly the +50% this routine exists to capture.

    Ten ticks with the old per-iteration read made ten calls; the bound here is deliberately
    generous (one per AIM_REFRESH_TICKS boundary plus one) and still fails against that."""
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL]] * 60)
    clicks = []
    reads = []
    _stub_burst_io(monkeypatch, clicks, player_reads=reads)
    monkeypatch.setattr(dst, "BURST_MAX_S", 6.0)      # 10 ticks
    dst.trawl_burst(ctx, CFG, NET_OBJ)
    assert len(clicks) == 10
    assert len(reads) <= len(clicks) // dst.AIM_REFRESH_TICKS + 1


# --------------------------------------------------------------------------- phases / tick / break

import sailing


def test_lower_phase_clicks_the_menu_option_and_confirms_on_the_depth_varbit(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[("VARBIT_CHANGED", {"varbitId": 19206, "value": 1})]])
    calls = []
    depth = {"v": 0}
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "get_trawling",
                        lambda: {"nets": [{"slot": 0, "depth": depth["v"], "hotspotId": 3}]})

    def lower(ser, ent, option, **kw):
        calls.append(option)
        depth["v"] = 1
        return True
    monkeypatch.setattr(controller, "click_menu_action", lower)

    assert dst.lower_phase(ctx, CFG, _snap(net_depth=0)) is True
    assert calls == ["Lower"]


def test_lower_phase_stops_when_the_net_never_goes_down(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]] * 20)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_trawling",
                        lambda: {"nets": [{"slot": 0, "depth": 0, "hotspotId": 3}]})
    assert dst.lower_phase(ctx, CFG, _snap(net_depth=0)) is False
    assert ctx.state.safety_stopped


def test_collect_phase_takes_collect_from_and_confirms_the_net_emptied(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[("INVENTORY_CHANGED", {})]])
    fish = {"n": 240}
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "get_trawling", lambda: {"netFish": fish["n"]})
    monkeypatch.setattr(dst, "_answer_collect_dialogue",
                        lambda ctx, cfg, dest=None: fish.__setitem__("n", 0))
    seen = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **kw: seen.append(option) or True)

    assert dst.collect_phase(ctx, CFG, _snap(net_fish=240)) is True
    assert seen == ["Collect-from"]
    assert fish["n"] == 0


def test_approach_phase_sails_at_the_live_shoal_when_it_is_sitting_still(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    targets = []
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: targets.append(t) or True)
    snap = _snap(shoal_distance=30.0, shoal={"id": 59734, "x": 1574, "y": 3338, "plane": 0})
    assert dst.approach_phase(ctx, CFG, snap) is True
    assert targets == [{"x": 1574, "y": 3338, "plane": 0}]


def test_travel_phase_aims_at_the_stop_after_the_one_we_last_fished(monkeypatch):
    """The route table is an ACCELERATOR: with the shoal out of sight we head for where it is
    going next, so we arrive with it instead of trailing it."""
    ctx = _burst_ctx(monkeypatch, [[]])
    ctx.state.data["dst_last_stop_index"] = 3
    targets = []
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: targets.append(t) or True)
    assert dst.travel_phase(ctx, CFG, _snap(shoal=None, shoal_distance=None)) is True
    assert targets == [{"x": 1580, "y": 3379, "plane": 0}]   # Great Sound stop index 4


def test_travel_phase_with_no_history_aims_at_the_nearest_stop(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    targets = []
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: targets.append(t) or True)
    assert dst.travel_phase(ctx, CFG, _snap(shoal=None, shoal_distance=None,
                                            px=1576, py=3340)) is True
    assert targets == [{"x": 1574, "y": 3338, "plane": 0}]


def test_travel_phase_stops_off_the_route_with_the_message_that_names_it(monkeypatch):
    """F-M4's other half: this branch used to be DEAD, because derive_phase sent the off-route
    case to "lost" and its message talked about not being aboard a boat."""
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: True)
    assert dst.travel_phase(ctx, CFG, _snap(shoal=None, shoal_distance=None,
                                            in_route_area=False)) is False
    assert ctx.state.safety_stopped
    assert CFG["route"] in ctx.state.safety_reason


def test_travel_phase_moves_on_once_it_reaches_a_stop_with_no_shoal(monkeypatch):
    """F-M10: travel_phase never advanced dst_last_stop_index and had no target of its own, so
    with the wrong ids or route it re-sailed to the SAME already-reached stop forever -- camping
    one stop instead of walking the lap -- and only _stuck_watch's 420s caught it, and only
    because the boat happened to sit still."""
    ctx = _burst_ctx(monkeypatch, [[]])
    targets = []
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: targets.append(t) or True)
    ctx.state.data["dst_travel_target"] = 3            # heading for (1574, 3338)
    # ...and we are standing on it, with still no shoal in the scene.
    dst.travel_phase(ctx, CFG, _snap(shoal=None, shoal_distance=None, px=1574, py=3338))
    assert targets == [{"x": 1580, "y": 3379, "plane": 0}]     # stop 4, the next one round
    assert ctx.state.data["dst_travel_target"] == 4


def test_travel_phase_gives_up_after_a_full_lap_with_no_shoal(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: True)
    stops = trawling_routes.stops_world(CFG["shoal"], CFG["route"])
    for _ in range(dst.TRAVEL_MAX_STOPS * 2):
        if ctx.state.safety_stopped:
            break
        target = ctx.state.data.get("dst_travel_target")
        px, py = stops[target] if target is not None else (1576, 3340)
        dst.travel_phase(ctx, CFG, _snap(shoal=None, shoal_distance=None, px=px, py=py))
    assert ctx.state.safety_stopped
    assert CFG["route"] in ctx.state.safety_reason
    assert "Giant krill shoal" in ctx.state.safety_reason


def test_finding_a_shoal_clears_the_travel_lap_counter(monkeypatch):
    """Otherwise a run that legitimately travels between stops all day accumulates its way into
    the lap ceiling."""
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(dst, "trawl_burst", lambda c, cfg, net: "budget")
    ctx.state.data["dst_travel_stops"] = dst.TRAVEL_MAX_STOPS - 1
    ctx.state.data["dst_travel_target"] = 3
    dst.tick_with_snapshot(ctx, CFG, _snap())
    assert ctx.state.data["dst_travel_stops"] == 0
    assert ctx.state.data["dst_travel_target"] is None


def test_a_single_lost_read_does_not_stop_the_run(monkeypatch):
    """F-C2. controller.api_get has a 0.3s timeout and returns None on ANY failure, so ONE
    dropped /trawling or /objects response makes the world look empty and reads as "lost" --
    and "lost" used to be an unconditional, irreversible first-read safety stop, ending an
    eight-hour unattended run (and discarding the nets with the forced logout that follows).
    The standing doctrine here is the opposite: ride network drops out."""
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: True)
    assert dst.tick_with_snapshot(ctx, CFG, _snap(net=None)) is False
    assert ctx.state.safety_stopped is False


def test_three_consecutive_lost_reads_stop_the_run_naming_the_boat(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: True)
    for _ in range(dst.LOST_CONFIRM_READS - 1):
        dst.tick_with_snapshot(ctx, CFG, _snap(net=None))
        assert ctx.state.safety_stopped is False
    assert dst.tick_with_snapshot(ctx, CFG, _snap(net=None)) is False
    assert ctx.state.safety_stopped
    assert "boat" in ctx.state.safety_reason.lower()


def test_a_good_read_between_lost_reads_resets_the_streak(monkeypatch):
    """The property that keeps a long run alive: occasional dropped responses must not
    accumulate their way into a stop. Without the reset this sequence (1 lost, 1 good, 2 lost)
    totals three and would stop."""
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL]] * 60)
    monkeypatch.setattr(dst, "trawl_burst", lambda c, cfg, net: "budget")
    dst.tick_with_snapshot(ctx, CFG, _snap(net=None))
    dst.tick_with_snapshot(ctx, CFG, _snap())            # a healthy read
    dst.tick_with_snapshot(ctx, CFG, _snap(net=None))
    dst.tick_with_snapshot(ctx, CFG, _snap(net=None))
    assert ctx.state.safety_stopped is False


def test_an_unreadable_snapshot_neither_stops_nor_counts_as_lost(monkeypatch):
    """px is None means the CLIENT told us nothing (a LOADING frame, a dropped /player) -- it is
    "cannot tell", not a fact about the world, so it must not dispatch a phase and must not
    consume any of the lost budget."""
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: True)
    blank = _snap(px=None, py=None, net=None, net_fish=None, shoal=None, shoal_distance=None,
                  in_route_area=False)
    for _ in range(dst.LOST_CONFIRM_READS + 3):
        assert dst.tick_with_snapshot(ctx, CFG, blank) is False
    assert ctx.state.safety_stopped is False
    assert ctx.state.data.get("dst_lost_reads", 0) == 0


def test_out_of_range_bursts_that_are_catching_never_stop_the_run(monkeypatch):
    """F-C1 -- the finding that replaced a test which PINNED THE BUG.

    "out_of_range" is the DESIGNED, correct end of every single shoal stop: a Giant krill stop
    is 150 ticks (90s) and BURST_MAX_S is 120s, so the burst's normal exit is the shoal moving
    off and the heartbeat stopping. Counting those as failed approaches safety-stopped a healthy
    run after six stops (~10 minutes) claiming "the boat couldn't get back to the shoal" while
    it had been catching fish the whole time. The ceiling must count bursts that produced NO
    ROLLS, which is what its stop message actually claims."""
    ctx = _burst_ctx(monkeypatch, [[]])

    def catching_burst(c, cfg, net):
        c.state.data["dst_rolls"] = c.state.data.get("dst_rolls", 0) + 40
        return "out_of_range"

    monkeypatch.setattr(dst, "trawl_burst", catching_burst)
    for _ in range(dst.APPROACH_MAX_TRIES * 3):
        assert dst.trawl_phase(ctx, CFG, _snap()) is True
        assert ctx.state.safety_stopped is False


def test_out_of_range_bursts_with_no_rolls_stop_at_the_ceiling(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(dst, "trawl_burst", lambda c, cfg, net: "out_of_range")
    for _ in range(dst.APPROACH_MAX_TRIES - 1):
        dst.trawl_phase(ctx, CFG, _snap())
        assert ctx.state.safety_stopped is False       # and not one attempt earlier
    dst.trawl_phase(ctx, CFG, _snap())
    assert ctx.state.safety_stopped


def test_the_no_events_burst_reports_progress_by_the_net_count_growing(monkeypatch):
    """The ctx.events is None mode can never grow dst_rolls (there are no XP events at all), so
    keying the ceiling on rolls alone would re-create F-C1 for exactly that mode. trawl_burst
    records dst_burst_progress from the polled net count instead."""
    ctx = _burst_ctx(monkeypatch, [[]])

    def growing_but_out_of_range(c, cfg, net):
        c.state.data["dst_burst_progress"] = True
        return "out_of_range"

    monkeypatch.setattr(dst, "trawl_burst", growing_but_out_of_range)
    for _ in range(dst.APPROACH_MAX_TRIES * 2):
        dst.trawl_phase(ctx, CFG, _snap())
    assert ctx.state.safety_stopped is False


def test_burst_records_progress_when_the_net_count_grows_with_no_event_stream(monkeypatch):
    """The producer side of the flag above, driven through the real trawl_burst."""
    ctx = _no_events_ctx(monkeypatch)
    clicks = []
    count = {"n": 9}

    def growing():
        count["n"] += 1
        return count["n"]

    _stub_burst_io(monkeypatch, clicks, net_fish=growing)
    monkeypatch.setattr(dst, "NO_EVENTS_STALL_TICKS", 16)
    monkeypatch.setattr(dst, "BURST_MAX_S", 24.0)
    dst.trawl_burst(ctx, CFG, NET_OBJ)
    assert ctx.state.data["dst_burst_progress"] is True


def test_burst_records_no_progress_when_nothing_is_caught(monkeypatch):
    ctx = _no_events_ctx(monkeypatch)
    clicks = []
    _stub_burst_io(monkeypatch, clicks, net_fish=lambda: 10)     # never grows
    monkeypatch.setattr(dst, "NO_EVENTS_STALL_TICKS", 24)
    assert dst.trawl_burst(ctx, CFG, NET_OBJ) == "out_of_range"
    assert ctx.state.data["dst_burst_progress"] is False


def test_a_net_that_cannot_be_clicked_stops_at_its_own_ceiling(monkeypatch):
    """F-M11: a refused/withheld opening click used to return "interrupted", which trawl_phase
    neither counted nor acted on -- so a persistently unclickable net looped at ~1s an iteration
    until the 420s stall watch, under a message about nothing in particular."""
    ctx = _burst_ctx(monkeypatch, [[]])
    clicks = []
    _stub_burst_io(monkeypatch, clicks)
    monkeypatch.setattr(dst, "_click_net_to_stand_at_it", lambda ctx_, net: False)
    for _ in range(dst.NET_CLICK_TRIES - 1):
        assert dst.trawl_phase(ctx, CFG, _snap()) is False
        assert ctx.state.safety_stopped is False
    dst.trawl_phase(ctx, CFG, _snap())
    assert ctx.state.safety_stopped
    assert "net" in ctx.state.safety_reason.lower()
    # ...and a single failure among healthy bursts must not accumulate.
    ctx2 = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(dst, "trawl_burst", lambda c, cfg, net: "budget")
    for _ in range(dst.NET_CLICK_TRIES * 2):
        dst.trawl_phase(ctx2, CFG, _snap())
    assert ctx2.state.safety_stopped is False


def test_before_break_collects_the_nets_first(monkeypatch):
    """Fish in the nets are discarded on a forced logout, and a break is minutes long."""
    ctx = _burst_ctx(monkeypatch, [[]])
    collected = []
    monkeypatch.setattr(dst, "collect_phase", lambda c, cfg, s: collected.append(1) or True)
    monkeypatch.setattr(dst, "read_snapshot", lambda c, cfg, **kw: _snap(net_fish=17))
    dst.before_break(ctx)
    assert collected == [1]

    collected.clear()
    monkeypatch.setattr(dst, "read_snapshot", lambda c, cfg, **kw: _snap(net_fish=0))
    dst.before_break(ctx)
    assert collected == []

    # F-I6: netFish None is "cannot tell", not 0. Skipping the collect is the expensive
    # direction -- the fish are discarded if the break's idle logout fires -- so try it anyway.
    collected.clear()
    monkeypatch.setattr(dst, "read_snapshot", lambda c, cfg, **kw: _snap(net_fish=None))
    dst.before_break(ctx)
    assert collected == [1]


def test_the_default_config_carries_a_breaks_block_the_engine_can_read():
    """F-C3. The engine does `config.get("breaks", {})`, and both ControllerState.break_due and
    maybe_toggle_break return at their first line on an empty dict -- so with no "breaks" key
    this routine took NO breaks at all (an anti-detection regression on an hours-long activity)
    and before_break, the whole answer to the discard-on-forced-logout hazard, could never be
    called: its test guarded unreachable code. The key NAMES are the contract, not just the
    presence of the block."""
    breaks = dst.get_config({})["breaks"]
    assert breaks
    assert breaks["enabled"] is True
    for key in ("everyMinMinutes", "everyMaxMinutes",
                "durationMinMinutes", "durationMaxMinutes"):
        assert isinstance(breaks[key], (int, float)), key
    assert breaks["everyMinMinutes"] <= breaks["everyMaxMinutes"]
    assert breaks["durationMinMinutes"] <= breaks["durationMaxMinutes"]

    # And the engine's own predicate must actually be able to fire on it.
    class _S:
        break_until = None
        next_break_at = 0.0
    assert controller.ControllerState.break_due(_S(), breaks) is True


def test_the_collect_confirm_treats_a_missing_net_count_as_unconfirmed(monkeypatch):
    """F-I6: `(... or {}).get("netFish") or 0) == 0` collapsed None into 0 -- exactly the
    distinction TrawlingProvider preserves and controller.get_trawling's docstring warns about
    -- so one bridge hiccup inside the 6s window reported "nets emptied" with nothing moved."""
    monkeypatch.setattr(controller, "get_trawling", lambda: {"netFish": None})
    assert dst._net_emptied() is False
    monkeypatch.setattr(controller, "get_trawling", lambda: {})
    assert dst._net_emptied() is False
    monkeypatch.setattr(controller, "get_trawling", lambda: {"netFish": 0})
    assert dst._net_emptied() is True
    monkeypatch.setattr(controller, "get_trawling", lambda: {"netFish": 3})
    assert dst._net_emptied() is False


def test_click_confirms_budget_the_walk_to_the_net_and_cap(monkeypatch):
    """F-I5. The game performs a queued interaction ON ARRIVAL, and NET_OBJECT_IDS includes the
    3x8 port/starboard variants where the net is most of a deck away: at 0.6s/tile an 8-tile
    walk is 4.8s, past the old flat LOWER_CONFIRM_S of 4.0 -- so a click that lowered the net
    first time read as a failure, burned LOWER_TRIES and stopped the run."""
    near = dst._confirm_budget(dst.LOWER_CONFIRM_S, dst.LOWER_CONFIRM_MAX_S, {"distance": 1})
    far = dst._confirm_budget(dst.LOWER_CONFIRM_S, dst.LOWER_CONFIRM_MAX_S, {"distance": 8})
    assert far >= near + 4.0
    assert far > dst.LOWER_CONFIRM_S
    huge = dst._confirm_budget(dst.LOWER_CONFIRM_S, dst.LOWER_CONFIRM_MAX_S, {"distance": 500})
    assert huge == dst.LOWER_CONFIRM_MAX_S


def test_lower_phase_actually_passes_the_walk_budgeted_wait(monkeypatch):
    """The guard that the constant above is wired in, not just defined."""
    ctx = _burst_ctx(monkeypatch, [[]])
    budgets = []
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_trawling",
                        lambda: {"nets": [{"slot": 0, "depth": 0, "hotspotId": 3}]})
    monkeypatch.setattr(outcome_waits, "await_pre",
                        lambda ctx_, sig, budget, **kw: budgets.append(budget) or False)
    far_net = dict(NET_OBJ, distance=8)
    dst.lower_phase(ctx, CFG, _snap(net=far_net, net_depth=0))
    assert budgets and min(budgets) > dst.LOWER_CONFIRM_S


def test_stuck_watch_warns_then_stops(monkeypatch):
    """Exercises BOTH windows: a warn at STALL_WARN_S that does not stop, then a stop once
    STALL_STOP_S is crossed. (Fix round 1: the original version of this test jumped the clock
    straight past STALL_STOP_S and never actually drove the warn-only window or dst_warned.)"""
    ctx = _burst_ctx(monkeypatch, [[]])
    snap = _snap()
    dst._stuck_watch(ctx, CFG, snap, "trawl")
    assert ctx.state.safety_stopped is False

    ctx.clock["t"] += dst.STALL_WARN_S + 1
    dst._stuck_watch(ctx, CFG, snap, "trawl")
    assert ctx.state.safety_stopped is False
    assert ctx.state.data.get("dst_warned") is True

    ctx.clock["t"] += (dst.STALL_STOP_S - dst.STALL_WARN_S) + 1
    dst._stuck_watch(ctx, CFG, snap, "trawl")
    assert ctx.state.safety_stopped


def test_stuck_watch_never_stops_while_progress_keeps_changing(monkeypatch):
    """A changing signature must reset the stall clock every time, however long the routine
    runs in total. Mutating net_fish between calls while jumping the clock by more than half
    STALL_STOP_S each time: if _stuck_watch ever compared on anything narrower than the full
    signature (e.g. phase alone), the unmatched jumps would accumulate past STALL_STOP_S and
    this would incorrectly stop."""
    ctx = _burst_ctx(monkeypatch, [[]])
    for i in range(6):
        ctx.clock["t"] += dst.STALL_STOP_S * 0.6
        dst._stuck_watch(ctx, CFG, _snap(net_fish=i), "trawl")
        assert ctx.state.safety_stopped is False


def test_sail_failure_cap_triggers_a_safety_stop(monkeypatch):
    """No test previously drove sail_to to fail repeatedly and checked the cap actually fires --
    this is the ceiling most likely to matter on the first live run (an unreachable shoal)."""
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: False)
    snap = _snap(shoal_distance=30.0, shoal={"id": 59734, "x": 1574, "y": 3338, "plane": 0})
    for _ in range(dst.SAIL_MAX_TRIES - 1):
        dst.approach_phase(ctx, CFG, snap)
        assert ctx.state.safety_stopped is False
    dst.approach_phase(ctx, CFG, snap)
    assert ctx.state.safety_stopped is True


def test_sail_failure_counter_resets_on_a_success(monkeypatch):
    """The property that stops a healthy long run from accumulating its way into a false stop:
    an occasional failed sail followed by a successful one must not carry the failure count
    forward. Without the reset, this sequence (2 fails, 1 success, 2 fails) would total 4
    failures against a cap of 3 and stop wrongly."""
    ctx = _burst_ctx(monkeypatch, [[]])
    results = iter([False, False, True, False, False])
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: next(results))
    snap = _snap(shoal_distance=30.0, shoal={"id": 59734, "x": 1574, "y": 3338, "plane": 0})
    for _ in range(5):
        dst.approach_phase(ctx, CFG, snap)
    assert ctx.state.safety_stopped is False


def test_startup_checks_defers_when_the_scene_has_not_loaded_yet(monkeypatch):
    """px is None (the first logged-in tick after an auto-login) must DEFER, not stop -- this is
    the GOTR 'no pickaxe with a pickaxe equipped' bug shape. net=None too, on purpose: without
    the early return this would fall through to the next check and stop for the wrong reason,
    so this test fails if that early return is ever removed."""
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(dst, "read_snapshot", lambda c, cfg: _snap(px=None, py=None, net=None))
    dst._startup_checks(ctx, CFG)
    assert ctx.state.safety_stopped is False


def test_startup_checks_stops_without_a_net(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(dst, "read_snapshot", lambda c, cfg: _snap(net=None))
    dst._startup_checks(ctx, CFG)
    assert ctx.state.safety_stopped is True
    assert "boat" in ctx.state.safety_reason.lower()


def test_startup_checks_stops_without_a_crewmate_on_the_helm(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(dst, "read_snapshot", lambda c, cfg: _snap(navigating=False))
    dst._startup_checks(ctx, CFG)
    assert ctx.state.safety_stopped is True
    assert "helm" in ctx.state.safety_reason.lower()


def test_startup_checks_stops_outside_the_route_area(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(dst, "read_snapshot", lambda c, cfg: _snap(in_route_area=False))
    dst._startup_checks(ctx, CFG)
    assert ctx.state.safety_stopped is True
    assert CFG["route"] in ctx.state.safety_reason
