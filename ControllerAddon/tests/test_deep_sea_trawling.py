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


def _stub_burst_io(monkeypatch, clicks, net_fish=lambda: 10, moved=lambda: False,
                   pump_on_wait=False):
    """pump_on_wait=True installs a tick_pause stub that also pumps the real EventStream before
    advancing the fake clock -- i.e. it behaves like the real wait_ticks() (which pumps mid-wait)
    instead of the bare-sleep default. Only the mark/since regression test needs this; every
    other test's plain bare-sleep stub is faithful enough (see that test's own docstring)."""
    monkeypatch.setattr(controller, "click_human", lambda ser: clicks.append(1))
    monkeypatch.setattr(controller, "hover_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "get_trawling", lambda: {"netFish": net_fish()})
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 1581 if moved() else 1580, "y": 3365, "plane": 0})
    monkeypatch.setattr(dst, "_walk_to_net", lambda ctx, net: True)
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
    `len(hovers) >= 2` also passes for the brief's original buggy `(None, None)` sentinel, which
    fires a spurious extra re-aim on the very first iteration (tile != (None, None) is always
    true). Pinning the exact sequence [0, 3] -- the pre-loop aim, then the one re-aim on the
    tick the tile is first observed to have changed -- fails against that buggy version and
    passes against the current code."""
    ctx = _burst_ctx(monkeypatch, [[XP_SAIL]] * 60)
    clicks = []
    hovers = []
    _stub_burst_io(monkeypatch, clicks, moved=lambda: len(clicks) >= 3)
    monkeypatch.setattr(controller, "hover_entity",
                        lambda *a, **k: hovers.append(len(clicks)) or True)
    monkeypatch.setattr(dst, "BURST_MAX_S", 6.0)
    dst.trawl_burst(ctx, CFG, NET_OBJ)
    assert hovers == [0, 3]


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
    monkeypatch.setattr(dst, "_answer_collect_dialogue", lambda ctx, cfg: fish.__setitem__("n", 0))
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


def test_a_lost_snapshot_is_a_safety_stop_naming_the_boat(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: True)
    assert dst.tick_with_snapshot(ctx, CFG, _snap(net=None)) is False
    assert ctx.state.safety_stopped
    assert "boat" in ctx.state.safety_reason.lower()


def test_repeated_out_of_range_bursts_eventually_stop_the_run(monkeypatch):
    ctx = _burst_ctx(monkeypatch, [[]])
    monkeypatch.setattr(dst, "trawl_burst", lambda c, cfg, net: "out_of_range")
    for _ in range(dst.APPROACH_MAX_TRIES):
        dst.trawl_phase(ctx, CFG, _snap())
    assert ctx.state.safety_stopped


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
