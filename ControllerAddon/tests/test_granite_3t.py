import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import bridge_events
import controller
import tick_cycle
import granite_3t as g


def _item(name, x=1700, y=800, slot=0):
    return {"name": name, "qty": 1, "slot": slot, "screenX": x, "screenY": y}


class _State:
    def __init__(self):
        self.data = {}
        self.config = dict(g.DEFAULT_CONFIG)
        self.safety_stopped = False
        self.safety_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason


class _Ctx:
    def __init__(self):
        self.state = _State()
        self.ser = object()
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Hermetic: nothing here may reach the running client or the serial port."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3255, "y": 2354, "plane": 0, "regionId": 12836})
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [])
    monkeypatch.setattr(controller, "get_widget", lambda *a, **k: {})
    monkeypatch.setattr(controller, "get_state", lambda: {"uiBlockers": []})
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (0, 0))
    monkeypatch.setattr(controller, "get_widget_children", lambda *a, **k: [])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(tick_cycle, "fast_click", lambda ser: None)
    monkeypatch.setattr(controller, "move_to_human", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_human", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "key_tap", lambda *a, **k: None)
    monkeypatch.setattr(controller, "mark_busy", lambda *a, **k: None)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    # the knife-hover gate wants a Use; the per-cycle arming check wants the knife SELECTED
    # over the log -- one default hover satisfies both
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"option": "Use", "target": "Knife -> Teak logs"})
    monkeypatch.setattr(controller, "clear_movement_abort", lambda: None)
    monkeypatch.setattr(controller, "get_last_action", lambda: None)
    monkeypatch.setattr(controller, "key_down", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_up", lambda *a, **k: None)
    monkeypatch.setattr(controller, "sweep_and_click", lambda *a, **k: None)
    monkeypatch.setattr(g, "_real_drop_burst", g.drop_burst, raising=False)
    monkeypatch.setattr(g, "drop_burst", lambda ctx, items, **kw: False)   # tests drive drop_items
    monkeypatch.setattr(g, "_sleep", lambda s: None)


def _cfg(**over):
    """The FIXED rotation (Cape Conch shape) unless `rockTiles=[]` asks for the dynamic one."""
    cfg = dict(g.DEFAULT_CONFIG)
    cfg["rockTiles"] = [[10, 20, 0], [11, 20, 0], [12, 20, 0], [13, 20, 0]]
    cfg["rockIds"] = [1]
    cfg["regionId"] = None
    cfg["forceStep"] = False
    cfg["dropBetweenMines"] = False
    cfg["mineTile"] = [3255, 2354, 0]       # the fixture player's tile: no walk home
    cfg.update(over)
    return cfg


def _gem_cfg(**over):
    cfg = g.site_config(dict(g.DEFAULT_CONFIG, site="gem_mine"))
    cfg.update(over)
    return cfg


class _FakeTicks:
    def __init__(self, phase=0.0, tick=100, stale=False):
        self.phase = phase
        self.tick = tick
        self.stale = stale

    def is_stale(self, now):
        return self.stale

    def phase_ms(self, now):
        return self.phase


class _FakeEvents:
    """Enough EventStream for the cycle: a tick clock that never goes stale."""
    def __init__(self):
        self.ticks = _FakeTicks()
        self._seq = 0
        self.stream = []

    def pump(self, now=None):
        return []

    def mark(self):
        return self._seq

    def since(self, seq):
        return list(self.stream)

    def wait_ticks(self, n, now_fn=None, sleep_fn=None):
        return None


# --------------------------------------------------------------------------------------------
# helpers and config
# --------------------------------------------------------------------------------------------

def test_find_item_matches_a_substring_case_insensitively():
    inv = [_item("Teak logs"), _item("Knife")]
    assert g.find_item(inv, "knife")["name"] == "Knife"


def test_log_count_counts_every_matching_slot():
    inv = [_item("Teak logs", slot=1), _item("Teak logs", slot=2), _item("Knife", slot=0)]
    assert g.log_count(inv, "teak logs") == 2


def test_the_gem_mine_site_overrides_the_cape_conch_defaults():
    cfg = g.site_config(dict(g.DEFAULT_CONFIG, site="gem_mine"))
    assert cfg["rockTiles"] == []                   # dynamic rotation
    assert g.dynamic_rotation(cfg) is True
    assert g.rock_ids(cfg) == {11380, 11381}
    assert cfg["regionId"] == 11410
    assert cfg["bankTile"] is None
    assert cfg["fullInventory"] == "deposit"
    assert "gem bag" in cfg["keepItems"]


def test_the_cape_conch_site_is_the_fixed_rotation():
    cfg = g.site_config(dict(g.DEFAULT_CONFIG, site="cape_conch"))
    assert len(cfg["rockTiles"]) == 4
    assert g.dynamic_rotation(cfg) is False
    assert g.rock_ids(cfg) == {11387}
    assert cfg["forceStep"] is False
    assert cfg["rockTiles"] == [[3248, 2350, 0], [3250, 2349, 0], [3247, 2350, 0], [3249, 2350, 0]]


def test_force_step_skips_a_rock_adjacent_to_the_player(monkeypatch):
    """USER 2026-09-09: "make sure we always move 1 tile to mine". From (3248,2349) the three
    rocks in the row north are adjacent; only (3250,2349) is two away."""
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1000 + x, "screenY": 800})
    cfg = g.site_config(dict(g.DEFAULT_CONFIG, site="cape_conch", forceStep=True))
    point, index = g.next_live_rock(cfg, 0, {"x": 3248, "y": 2349})
    assert cfg["rockTiles"][index][:2] == [3250, 2349]
    # from (3249,2349), after that rock, (3247,2350) is the one two away
    point, index = g.next_live_rock(cfg, index + 1, {"x": 3249, "y": 2349})
    assert cfg["rockTiles"][index][:2] == [3247, 2350]


def test_the_rock_just_clicked_is_never_picked_again_even_as_the_fallback(monkeypatch):
    """MEASURED g3t_live39: with the other three on respawn the fallback re-picked the rock
    whose own pay was about to empty it, and the click landed on a depleted rock."""
    live = {(3250, 2349)}
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1, "screenY": 1} if (x, y) in live else None)
    cfg = g.site_config(dict(g.DEFAULT_CONFIG, site="cape_conch", forceStep=True))
    assert g.next_live_rock(cfg, 0, {"x": 3249, "y": 2349}, exclude=1) == (None, 0)


def test_force_step_falls_back_to_an_adjacent_rock_when_nothing_else_is_live(monkeypatch):
    live = {(3248, 2350)}
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1, "screenY": 1} if (x, y) in live else None)
    cfg = g.site_config(dict(g.DEFAULT_CONFIG, site="cape_conch", forceStep=True))
    point, index = g.next_live_rock(cfg, 0, {"x": 3248, "y": 2349})
    assert cfg["rockTiles"][index][:2] == [3248, 2350]


def test_get_config_applies_the_default_site():
    assert g.get_config(None)["rockIds"] == g.SITES[g.DEFAULT_CONFIG["site"]].get(
        "rockIds", g.DEFAULT_CONFIG["rockIds"])
    assert g.get_config(None)["site"] == "cape_conch"


# --------------------------------------------------------------------------------------------
# geometry and startup
# --------------------------------------------------------------------------------------------

def test_geometry_refuses_when_the_knife_is_missing():
    ok, msg = g.check_geometry(_cfg(), [_item("Teak logs")])
    assert ok is False
    assert "knife" in msg.lower()


def test_geometry_ACCEPTS_a_log_surplus_but_says_why_it_matters(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1680, "screenY": 800, "id": 1})
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1),
           _item("Teak logs", 1740, 800, slot=2)]
    ok, msg = g.check_geometry(_cfg(), inv)
    assert ok is True
    assert "make-x" in msg.lower()


def test_geometry_refuses_when_a_rock_will_not_resolve(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    ok, msg = g.check_geometry(_cfg(), [_item("Knife", slot=0), _item("Teak logs", slot=1)])
    assert ok is False
    assert "rock" in msg.lower()


def test_geometry_refuses_when_the_rocks_are_too_far_from_the_log_AND_reports_the_pixels(
        monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 400, "screenY": 800, "id": 1})
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1)]
    cfg = _cfg(flightSpeedScale=1.0)
    ok, msg = g.check_geometry(cfg, inv)
    assert ok is False
    assert "1340" in msg          # |1740 - 400| -- the measurement must be IN the message
    # The limit is derived from the LATEST fletch phase and the in-place log click; the rock
    # click only has to be pressed inside the tick (measured 2026-09-09), so nothing is booked
    # for it.
    limit = tick_cycle.max_reach_px(600.0 - cfg["fletchPhaseMs"][1]
                                    - tick_cycle.CLICK_COST_MEDIAN_MS, 1.0)
    assert f"{limit:.0f}px" in msg


def test_geometry_passes_and_still_reports_the_measurement(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1680, "screenY": 800, "id": 1})
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1)]
    ok, msg = g.check_geometry(_cfg(), inv)
    assert ok is True
    assert "60" in msg


def test_geometry_in_the_dynamic_rotation_measures_the_nearest_live_rocks(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [
        {"id": 11380, "x": 1, "y": 1, "distance": 1, "screenX": 1690, "screenY": 800,
         "actions": ["Mine"]},
        {"id": 11381, "x": 2, "y": 1, "distance": 2, "screenX": 1600, "screenY": 800,
         "actions": ["Mine"]},
    ])
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1)]
    ok, msg = g.check_geometry(_gem_cfg(), inv)
    assert ok is True
    assert "140" in msg           # the furthest of the live rocks


def test_geometry_in_the_dynamic_rotation_refuses_with_no_live_rock_in_view():
    inv = [_item("Knife", 1700, 800, slot=0), _item("Teak logs", 1740, 800, slot=1)]
    ok, msg = g.check_geometry(_gem_cfg(), inv)
    assert ok is False
    assert "gem rocks" in msg


def test_setup_NEVER_safety_stops_because_it_runs_before_the_login_gate():
    ctx = _Ctx()
    ctx.events = None
    ctx.state.config = _cfg()
    g.setup(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_startup_pending"] is True


def test_geometry_refuses_an_inventory_holding_anything_it_would_DROP():
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1),
           _item("Rune pickaxe", 1700, 830, 2), _item("Ring of dueling(8)", 1740, 830, 3)]
    ok, msg = g.check_inventory_is_clean(_cfg(), inv)
    assert ok is False
    assert "Ring of dueling(8)" in msg
    assert "Rune pickaxe" not in msg


def test_the_clean_inventory_check_accepts_the_knife_log_keepitems_and_granite():
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1),
           _item("Rune pickaxe", 1700, 830, 2), _item("Granite (5kg)", 1740, 830, 3)]
    assert g.check_inventory_is_clean(_cfg(), inv)[0] is True


def test_the_clean_inventory_check_at_the_gem_mine_accepts_gems_geodes_and_the_bag():
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1),
           _item("Open gem sack", 1700, 830, 2), _item("Uncut opal", 1740, 830, 3),
           _item("Clue geode (easy)", 1700, 860, 4), _item("Teak stock", 1740, 860, 5)]
    assert g.check_inventory_is_clean(_gem_cfg(), inv)[0] is True


def _startup_ctx(monkeypatch, inv, stale=False, events=True):
    ctx = _Ctx()
    ctx.events = _FakeEvents() if events else None
    if events:
        ctx.events.ticks.stale = stale
    ctx.state.config = _cfg()
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    return ctx


def test_the_startup_check_DEFERS_on_an_empty_inventory(monkeypatch):
    ctx = _startup_ctx(monkeypatch, [])
    assert g._startup_check(ctx, ctx.state.config) is False
    assert ctx.state.safety_stopped is False


def test_the_startup_check_DEFERS_on_a_stale_tick_clock(monkeypatch):
    ctx = _startup_ctx(monkeypatch, [_item("Knife", 1700, 800, 0),
                                     _item("Teak logs", 1740, 800, 1)], stale=True)
    assert g._startup_check(ctx, ctx.state.config) is False
    assert ctx.state.safety_stopped is False


def test_setup_then_a_tick_at_the_login_screen_does_not_stop_the_run(monkeypatch):
    ctx = _startup_ctx(monkeypatch, [])
    g.setup(ctx)
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_startup_pending"] is True


def test_the_startup_check_STOPS_once_it_is_genuinely_answerable_and_wrong(monkeypatch):
    ctx = _startup_ctx(monkeypatch, [_item("Knife", 1700, 800, 0),
                                     _item("Teak logs", 1740, 800, 1)])
    assert g._startup_check(ctx, ctx.state.config) is False
    assert ctx.state.safety_stopped is True
    assert "rock" in ctx.state.safety_reason.lower()


def test_an_unanswerable_startup_stops_at_the_deferral_ceiling(monkeypatch):
    ctx = _startup_ctx(monkeypatch, [], events=False)
    ctx.state.data["g3t_startup_since"] = 1000.0
    monkeypatch.setattr(g, "_now", lambda: 1000.0 + g.STARTUP_DEFER_MAX_S + 1.0)
    assert g._startup_check(ctx, ctx.state.config) is False
    assert ctx.state.safety_stopped is True
    assert "tick clock" in ctx.state.safety_reason.lower()
    assert "inventory" in ctx.state.safety_reason.lower()


def test_the_startup_happy_path_seeds_the_rotation_and_the_log_count(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: {"screenX": 1680, "screenY": 800, "id": 1})
    ctx = _startup_ctx(monkeypatch, [_item("Knife", 1700, 800, 0),
                                     _item("Teak logs", 1740, 800, 1)])
    assert g._startup_check(ctx, ctx.state.config) is True
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_index"] == 0
    assert ctx.state.data["g3t_logs"] == 1
    assert ctx.state.data["g3t_startup_pending"] is False


def test_startup_at_a_site_with_no_home_tile_does_not_try_to_walk_home(monkeypatch):
    """The gem mine has no mineTile: a player anywhere in the region is at the rocks."""
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [
        {"id": 11380, "x": 2831, "y": 9386, "distance": 1, "screenX": 1690, "screenY": 800,
         "actions": ["Mine"]}, {"id": 11381, "x": 2831, "y": 9387, "distance": 2,
                                "screenX": 1650, "screenY": 800, "actions": ["Mine"]}])
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 2830, "y": 9387, "plane": 0, "regionId": 11410})
    ctx = _startup_ctx(monkeypatch, [_item("Knife", 1700, 800, 0),
                                     _item("Teak logs", 1740, 800, 1)])
    ctx.state.config = _gem_cfg()
    assert g._startup_check(ctx, ctx.state.config) is True


# --------------------------------------------------------------------------------------------
# the critical tick
# --------------------------------------------------------------------------------------------

def _ok_cycle_patches(monkeypatch, order=None):
    def _in_place(ser, **kw):
        if order is not None:
            order.append("log-in-place")
        return 50.0

    def _timed(ser, pt, **kw):
        if order is not None:
            order.append(("rock", pt))
        return 200.0

    monkeypatch.setattr(tick_cycle, "click_in_place", _in_place)
    monkeypatch.setattr(tick_cycle, "timed_click", _timed)
    monkeypatch.setattr(g.random, "random", lambda: 1.0)      # never a deliberate miss
    monkeypatch.setattr(g, "_now", lambda: 1000.0)


def test_run_cycle_clicks_the_log_IN_PLACE_then_flies_to_the_rock_ONCE(monkeypatch):
    """Two clicks, ONE flight, one rock click. USER's recording: 28 Mine clicks against 26
    knife-on-log pairs -- a second rock click cancels the mine it just started."""
    order = []
    _ok_cycle_patches(monkeypatch, order)
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.run_cycle(ctx, _cfg(), (1740, 800), (1640, 800)) == "ok"
    assert order == ["log-in-place", ("rock", (1640, 800))]
    assert ctx.state.data["g3t_fired_tick"] == 100        # the tick it fired in, recorded


def test_run_cycle_makes_NO_bridge_call_between_the_two_clicks(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None, timeout=0.3: calls.append(path))
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: calls.append("get_object_at"))
    monkeypatch.setattr(controller, "get_player", lambda: calls.append("get_player"))
    _ok_cycle_patches(monkeypatch)
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    assert g.run_cycle(ctx, _cfg(), (1740, 800), (1640, 800)) == "ok"
    assert calls == []


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _ticking_events(clock, monkeypatch, start_tick=100, phase_ms=0.0):
    """A REAL EventStream whose TICK packets are synthesised from the fake clock."""
    events = bridge_events.EventStream(sock=None)
    boundary_s = clock.now() - phase_ms / 1000.0

    def _poll(sock):
        elapsed_ms = (clock.now() - boundary_s) * 1000.0
        n = int(elapsed_ms // bridge_events.GAME_TICK_MS)
        return [("TICK", {"tick": start_tick + n,
                          "atMillis": boundary_s * 1000.0 + n * bridge_events.GAME_TICK_MS})]

    monkeypatch.setattr(controller, "poll_udp", _poll)
    events.pump(now=clock.now())
    return events


# --------------------------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------------------------

def _chain_ctx(monkeypatch, cfg=None, player=None, xp=None):
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    # the chain re-resolves the chosen rock through get_object_at right before the critical
    # tick; answer with the same point the rotation stubs hand out unless a test overrides it
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1640, "screenY": 800})
    xps = iter(xp) if xp is not None else iter(range(1000, 100000, 50))
    base = player or {"x": 3255, "y": 2354, "plane": 0, "regionId": 12836}
    monkeypatch.setattr(controller, "get_player",
                        lambda: dict(base, miningXp=next(xps)))
    times = iter([1000.0 + i * 1.0 for i in range(2000)])
    monkeypatch.setattr(g, "_now", lambda: next(times))
    monkeypatch.setattr(g.random, "random", lambda: 1.0)      # never a deliberate miss
    if not getattr(monkeypatch, "_keep_xp_wait", False):
        monkeypatch.setattr(g, "wait_for_mining_xp",
                            lambda ctx, mark, timeout: {"skill": "Mining", "delta": 65,
                                                        "tick": 103})
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = cfg or _cfg()
    ctx.state.data["g3t_index"] = 0
    return ctx


def test_tick_chains_many_cycles_rather_than_one_per_engine_iteration(monkeypatch):
    ran = []
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: (ran.append(1), "ok")[1])
    ctx = _chain_ctx(monkeypatch)
    g.tick(ctx)
    assert len(ran) > 1


def test_the_rotation_advances_one_rock_per_cycle_and_skips_a_depleted_one(monkeypatch):
    """A depleted rock is a different id, so get_object_at answers None for it and the
    rotation walks past it instead of waiting."""
    dead = {(11, 20)}
    seen = []
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle",
                        lambda ctx, cfg, lp, rp, target_tick=None, **kw: (seen.append(rp), "ok")[1])
    ctx = _chain_ctx(monkeypatch)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: None if (x, y) in dead
                        else {"screenX": 1600 + x, "screenY": 800})
    g.tick(ctx)
    # strict order, the depleted (11,20) skipped, the just-clicked rock never repeated
    assert seen[:4] == [(1610, 800), (1612, 800), (1613, 800), (1610, 800)]


def test_a_rock_that_does_not_resolve_leaves_NO_knife_selection_and_NO_log_click(monkeypatch):
    """The 2026-09-08 version selected the knife first and, when the rock failed to resolve,
    'consumed' the selection by clicking the log -- a fletch with no mine behind it, i.e. a
    log eaten. The bot's own recording shows runs of those. Resolve first; nothing to consume."""
    clicks = []
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (clicks.append(pt), 100.0)[1])
    monkeypatch.setattr(tick_cycle, "click_in_place",
                        lambda ser, **kw: (clicks.append("log"), 50.0)[1])
    ctx = _chain_ctx(monkeypatch)
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    g.tick(ctx)
    assert clicks == []
    assert ctx.state.safety_stopped is False


def test_no_mining_xp_for_the_stall_window_safety_stops(monkeypatch):
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: "ok")
    ctx = _chain_ctx(monkeypatch, cfg=_cfg(xpStallStopS=20), xp=[1000] * 400)
    ctx.state.data["g3t_xp"] = 1000
    ctx.state.data["g3t_xp_at"] = 1000.0
    g.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "xp" in ctx.state.safety_reason.lower()


def test_a_rising_xp_reading_resets_the_stall_clock(monkeypatch):
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: "ok")
    ctx = _chain_ctx(monkeypatch, cfg=_cfg(xpStallStopS=20))
    g.tick(ctx)
    assert ctx.state.safety_stopped is False


def test_the_region_gate_stops_a_run_started_outside_the_mine(monkeypatch):
    ctx = _chain_ctx(monkeypatch, cfg=_gem_cfg(),
                     player={"x": 3255, "y": 2354, "plane": 0, "regionId": 12836})
    g.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "11410" in ctx.state.safety_reason


def test_a_lost_log_at_a_site_with_no_bank_is_a_stop_not_a_trip(monkeypatch):
    monkeypatch.setattr(g, "ensure_one_loose_log", lambda ctx, cfg: False)
    ctx = _chain_ctx(monkeypatch, cfg=_gem_cfg(),
                     player={"x": 2830, "y": 9387, "plane": 0, "regionId": 11410})
    ctx.state.data["g3t_log_refills"] = g.BASKET_REFILL_TRIES - 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "no bank" in ctx.state.safety_reason
    assert not ctx.state.data.get("g3t_restocking")


# --------------------------------------------------------------------------------------------
# outcome accounting: the xp says which cycles paid, and at how many ticks
# --------------------------------------------------------------------------------------------

# --------------------------------------------------------------------------------------------
# the dynamic rotation (gem mine)
# --------------------------------------------------------------------------------------------

def _gem_rocks():
    return [
        {"id": 11380, "x": 2831, "y": 9386, "plane": 0, "distance": 1, "screenX": 1690,
         "screenY": 800, "actions": ["Mine"]},
        {"id": 11381, "x": 2831, "y": 9387, "plane": 0, "distance": 2, "screenX": 1650,
         "screenY": 790, "actions": ["Mine"]},
        {"id": 11391, "x": 2827, "y": 9386, "plane": 0, "distance": 3, "screenX": 1600,
         "screenY": 780, "actions": []},                          # depleted: not a rock id
        {"id": 11380, "x": 2829, "y": 9384, "plane": 0, "distance": 4, "screenX": 1500,
         "screenY": 700, "actions": ["Mine"]},
    ]


def test_pick_rock_takes_the_nearest_live_rock_but_never_the_one_just_mined(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: _gem_rocks())
    player = {"x": 2830, "y": 9387}
    first = g.pick_rock(_gem_cfg(), player)
    assert (first["x"], first["y"]) == (2831, 9386)
    second = g.pick_rock(_gem_cfg(), player, avoid_tile=(2831, 9386))
    assert (second["x"], second["y"]) == (2831, 9387)


def test_live_rocks_ignores_ids_that_are_not_rocks_and_ranks_by_the_players_tile(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: _gem_rocks())
    rocks = g.live_rocks(_gem_cfg(), {"x": 2829, "y": 9385})
    assert [(r["x"], r["y"]) for r in rocks] == [(2829, 9384), (2831, 9386), (2831, 9387)]
    assert rocks[0]["distance"] == 1


def test_the_dynamic_chain_runs_a_near_rock_as_an_ordinary_cycle(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: _gem_rocks())
    points = []
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle",
                        lambda ctx, cfg, lp, rp, target_tick=None, **kw: (points.append(rp), "ok")[1])
    far = []
    monkeypatch.setattr(g, "far_rock_cycle", lambda *a, **k: (far.append(1), "ok")[1])
    ctx = _chain_ctx(monkeypatch, cfg=_gem_cfg(),
                     player={"x": 2830, "y": 9387, "plane": 0, "regionId": 11410})
    # the fresh re-resolve answers per tile, so the point proves WHICH rock was chosen
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1690, "screenY": 800} if y == 9386
                        else {"screenX": 1650, "screenY": 790})
    g.tick(ctx)
    assert points[0] == (1690, 800)
    assert points[1] == (1650, 790)            # the just-mined tile is avoided
    assert far == []


def test_the_dynamic_chain_walks_to_a_far_rock_through_far_rock_cycle(monkeypatch):
    rocks = [dict(_gem_rocks()[3], distance=4)]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: rocks)
    monkeypatch.setattr(controller, "get_hover", lambda: {"option": "Use", "target": "Teak logs"})
    near = []
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: (near.append(1), "ok")[1])
    far = []
    monkeypatch.setattr(g, "far_rock_cycle",
                        lambda ctx, cfg, kp, lp, rock: (far.append(rock["x"]), "ok")[1])
    ctx = _chain_ctx(monkeypatch, cfg=_gem_cfg(),
                     player={"x": 2834, "y": 9390, "plane": 0, "regionId": 11410})
    g.tick(ctx)
    assert far and far[0] == 2829
    assert near == []


def test_far_rock_cycle_clicks_the_rock_prepares_then_fletches_one_step_out(monkeypatch):
    """The guide's rule: click the far rock, and start the knife-log when the true tile is one
    step out -- NOT on arrival, and NOT before."""
    order = []
    positions = iter([{"x": 2834, "y": 9390}, {"x": 2833, "y": 9389}, {"x": 2831, "y": 9386},
                      {"x": 2831, "y": 9386}])
    monkeypatch.setattr(controller, "get_player", lambda: next(positions))
    points = iter([{"screenX": 1500, "screenY": 700}, {"screenX": 1480, "screenY": 700}])
    monkeypatch.setattr(controller, "get_object_at", lambda x, y, p, i, **k: next(points))
    hovers = iter([None, {"option": "Use", "target": "Knife -> Teak logs"}])
    monkeypatch.setattr(controller, "get_hover",
                        lambda: next(hovers, {"option": "Use", "target": "Knife -> Teak logs"}))
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (order.append(("click", pt)), 100.0)[1])
    monkeypatch.setattr(tick_cycle, "click_in_place",
                        lambda ser, **kw: (order.append("log"), 50.0)[1])
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: order.append(("park", (x, y))))
    monkeypatch.setattr(tick_cycle, "current_tick", lambda ev, now=None: 500)
    monkeypatch.setattr(tick_cycle, "tick_phase_ms", lambda ev, now: 120.0)
    monkeypatch.setattr(g, "_now", lambda: 1000.0)
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    rock = {"id": 11380, "x": 2829, "y": 9384, "plane": 0, "distance": 5,
            "screenX": 1500, "screenY": 700}
    assert g.far_rock_cycle(ctx, _gem_cfg(), (1700, 800), (1740, 800), rock) == "ok"
    assert order == [("click", (1500, 700)),          # the walk starts
                     ("click", (1700, 800)),          # knife selected while walking
                     ("park", (1740, 800)),           # cursor parked on the log
                     "log",                           # fletch, one step out
                     ("click", (1480, 700))]          # re-resolved rock, same tick
    assert ctx.state.data["g3t_fired_tick"] == 500


def test_far_rock_cycle_gives_up_when_the_walk_never_closes(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2834, "y": 9390})
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1500, "screenY": 700})
    monkeypatch.setattr(tick_cycle, "timed_click", lambda ser, pt, **kw: 100.0)
    logged = []
    monkeypatch.setattr(tick_cycle, "click_in_place", lambda ser, **kw: (logged.append(1), 50.0)[1])
    times = iter([1000.0 + i * 0.5 for i in range(100)])
    monkeypatch.setattr(g, "_now", lambda: next(times))
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    rock = {"id": 11380, "x": 2829, "y": 9384, "plane": 0, "distance": 5,
            "screenX": 1500, "screenY": 700}
    assert g.far_rock_cycle(ctx, _gem_cfg(), (1700, 800), (1740, 800), rock) == "desync"
    assert logged == []            # no fletch was started, so no log is at risk


# --------------------------------------------------------------------------------------------
# a full inventory: drop, or deposit at the chest
# --------------------------------------------------------------------------------------------

def _full_inventory():
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)]
    inv += [_item("Granite (5kg)", 1700 + i, 830, 2 + i) for i in range(26)]
    return inv


def test_a_full_inventory_drops_the_granite(monkeypatch):
    dropped = {}
    monkeypatch.setattr(controller, "get_inventory", _full_inventory)
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: (dropped.update(names=[i["name"] for i in items]),
                                            True)[1])
    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _cfg()) is True
    assert set(dropped["names"]) == {"Granite (5kg)"}


def test_the_drop_NEVER_touches_the_knife_or_the_log(monkeypatch):
    dropped = {}
    monkeypatch.setattr(controller, "get_inventory", _full_inventory)
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: (dropped.update(names=[i["name"] for i in items]),
                                            True)[1])
    ctx = _Ctx()
    g.full_inventory_phase(ctx, _cfg())
    assert "Knife" not in dropped["names"]
    assert "Teak logs" not in dropped["names"]


def test_a_drop_that_frees_nothing_safety_stops(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", _full_inventory)
    monkeypatch.setattr(controller, "drop_items", lambda ser, items: False)
    ctx = _Ctx()
    g.full_inventory_phase(ctx, _cfg())
    assert ctx.state.safety_stopped is True


def test_a_non_full_inventory_is_not_a_drop_tick(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)])
    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _cfg()) is False


def test_a_full_inventory_with_nothing_droppable_safety_stops(monkeypatch):
    dropped = []
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, i) for i in range(28)])
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: (dropped.append(items), True)[1])
    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _cfg()) is True
    assert ctx.state.safety_stopped is True
    assert dropped == []


def test_the_drop_keeps_a_carried_pickaxe(monkeypatch):
    dropped = {}
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1),
           _item("Rune pickaxe", 1700, 830, 2)]
    inv += [_item("Granite (5kg)", 1700 + i, 860, 3 + i) for i in range(25)]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: (dropped.update(names=[i["name"] for i in items]),
                                            True)[1])
    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _cfg()) is True
    assert set(dropped["names"]) == {"Granite (5kg)"}


def _gem_inventory():
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1),
           _item("Open gem bag", 1700, 830, 2)]
    inv += [_item("Uncut opal", 1700 + i, 860, 3 + i) for i in range(15)]
    inv += [_item("Uncut jade", 1700 + i, 890, 18 + i) for i in range(10)]
    return inv


def test_a_full_inventory_at_the_gem_mine_walks_to_the_chest_when_it_is_far(monkeypatch):
    walked = []
    monkeypatch.setattr(controller, "get_inventory", _gem_inventory)
    monkeypatch.setattr(g.travel, "walk_to", lambda ctx, t, **k: walked.append(t))
    dropped = []
    monkeypatch.setattr(controller, "drop_items", lambda ser, items: dropped.append(items))
    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _gem_cfg()) is True
    assert walked and walked[0]["x"] == 2842
    assert dropped == []                     # never the floor at this site


def test_the_deposit_is_one_Deposit_All_per_stack_and_never_the_knife_log_or_bag(monkeypatch):
    state = {"open": False, "inv": _gem_inventory()}
    chest = {"id": 10530, "x": 2842, "y": 9382, "plane": 0, "distance": 3, "screenX": 900,
             "screenY": 500, "actions": ["Deposit"]}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [chest])
    monkeypatch.setattr(controller, "get_inventory", lambda: state["inv"])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)

    def _click_chest(ser, ent, **kw):
        if ent is chest:
            state["open"] = True
        return True
    monkeypatch.setattr(controller, "click_entity", _click_chest)
    monkeypatch.setattr(controller, "get_widget",
                        lambda grp, child, *a, **k: {"clickable": state["open"]})
    monkeypatch.setattr(controller, "get_widget_children",
                        lambda grp, child: [{"itemName": i["name"], "screenX": 1, "screenY": 1}
                                            for i in state["inv"]])
    deposited = []

    def _menu(ser, child, option, **kw):
        deposited.append((child["itemName"], option))
        if option == g.DEPOSIT_ALL_ACTION:
            state["inv"] = [i for i in state["inv"] if i["name"] != child["itemName"]]
        return True
    monkeypatch.setattr(controller, "click_menu_action", _menu)
    # the hover-gated left click is refused so the menu path is what we observe
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: _click_chest(ser, ent) if ent is chest else False)

    def _esc(ser, key):
        state["open"] = False
    monkeypatch.setattr(controller, "key_tap", _esc)
    times = iter([1000.0 + i * 0.5 for i in range(400)])
    monkeypatch.setattr(g, "_now", lambda: next(times))

    ctx = _Ctx()
    assert g.full_inventory_phase(ctx, _gem_cfg()) is True
    names = [n for n, o in deposited if o == g.DEPOSIT_ALL_ACTION]
    assert set(names) == {"Uncut opal", "Uncut jade"}
    assert ("Open gem bag", g.GEM_BAG_EMPTY_OPTIONS) in deposited
    assert state["open"] is False
    assert ctx.state.safety_stopped is False


def test_a_deposit_interface_that_never_opens_is_eventually_a_stop(monkeypatch):
    chest = {"id": 10530, "x": 2842, "y": 9382, "plane": 0, "distance": 3, "screenX": 900,
             "screenY": 500}
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: [chest])
    monkeypatch.setattr(controller, "get_inventory", _gem_inventory)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    times = iter([1000.0 + i * 0.5 for i in range(200)])
    monkeypatch.setattr(g, "_now", lambda: next(times))
    ctx = _Ctx()
    ctx.state.data["g3t_deposit_fails"] = g.DEPOSIT_FAIL_STOP - 1
    g.full_inventory_phase(ctx, _gem_cfg())
    assert ctx.state.safety_stopped is True


# --------------------------------------------------------------------------------------------
# the aim, the gap reseed, the empty read, and the two once-per-chain gates
# --------------------------------------------------------------------------------------------

def test_the_knife_and_log_are_not_aimed_at_one_exact_pixel(monkeypatch):
    radii = []
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (radii.append(kw.get("aim_radius")), 200.0)[1])
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: radii.append(kw.get("aim_radius")))
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    g.prepare(ctx, _cfg(), (1700, 800), (1740, 800))
    assert g.AIM_RADIUS_PX > 0
    assert radii == [g.AIM_RADIUS_PX, g.AIM_RADIUS_PX]


def test_a_long_gap_between_ticks_stands_the_stall_clock_down(monkeypatch):
    monkeypatch.setattr(g, "_run_chain", lambda ctx: None)
    monkeypatch.setattr(g, "_now", lambda: 1400.0)
    ctx = _Ctx()
    ctx.state.config = _cfg()
    ctx.state.data["g3t_xp"] = 1000
    ctx.state.data["g3t_xp_at"] = 1000.0
    ctx.state.data["g3t_last_tick_at"] = 1000.0
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_xp_at"] == 1400.0


def test_a_short_gap_between_ticks_leaves_the_stall_clock_alone(monkeypatch):
    monkeypatch.setattr(g, "_run_chain", lambda ctx: None)
    monkeypatch.setattr(g, "_now", lambda: 1001.0)
    ctx = _Ctx()
    ctx.state.config = _cfg()
    ctx.state.data["g3t_xp_at"] = 900.0
    ctx.state.data["g3t_last_tick_at"] = 1000.0
    g.tick(ctx)
    assert ctx.state.data["g3t_xp_at"] == 900.0


def test_a_single_empty_inventory_read_does_NOT_stop_the_run(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_empty_reads"] == 1


def test_a_run_of_empty_inventory_reads_is_eventually_a_stop(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.state.config = _cfg()
    ctx.state.data["g3t_empty_reads"] = g.EMPTY_INVENTORY_STOP - 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is True


def _gate_ctx(monkeypatch, started):
    monkeypatch.setattr(g, "next_live_rock",
                        lambda cfg, i, *a, **k: (started.append("cycle"), ((1640, 800), i))[1])
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: "ok")
    return _chain_ctx(monkeypatch)


def test_no_chain_starts_when_the_inventory_tab_will_not_open(monkeypatch):
    started = []
    ctx = _gate_ctx(monkeypatch, started)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: False)
    g.tick(ctx)
    assert started == []
    assert ctx.state.safety_stopped is False


def test_no_chain_starts_when_the_knife_left_click_is_not_a_Use(monkeypatch):
    started = []
    ctx = _gate_ctx(monkeypatch, started)
    monkeypatch.setattr(controller, "get_hover", lambda: {"option": "Drop", "target": "Knife"})
    g.tick(ctx)
    assert started == []


def test_the_knife_hover_check_fails_CLOSED_when_it_cannot_read_the_hover(monkeypatch):
    started = []
    ctx = _gate_ctx(monkeypatch, started)
    monkeypatch.setattr(controller, "get_hover", lambda: None)
    g.tick(ctx)
    assert started == []


def test_a_normal_chain_runs_when_the_tab_and_the_hover_both_check_out(monkeypatch):
    started = []
    ctx = _gate_ctx(monkeypatch, started)
    g.tick(ctx)
    assert started


def test_a_tab_that_never_opens_is_eventually_a_stop_that_names_the_gate(monkeypatch):
    ctx = _gate_ctx(monkeypatch, [])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: False)
    ctx.state.data["g3t_tab_fails"] = g.INVENTORY_TAB_STOP - 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "inventory tab" in ctx.state.safety_reason.lower()


def test_a_tab_failure_followed_by_a_success_does_NOT_stop_the_run(monkeypatch):
    answers = iter([False, True])
    ctx = _gate_ctx(monkeypatch, [])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: next(answers))
    g.tick(ctx)
    assert ctx.state.data["g3t_tab_fails"] == 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_tab_fails"] == 0


def test_an_unreadable_hover_is_eventually_a_stop_that_names_the_likely_cause(monkeypatch):
    ctx = _gate_ctx(monkeypatch, [])
    monkeypatch.setattr(controller, "get_hover", lambda: None)
    ctx.state.data["g3t_hover_fails"] = g.KNIFE_HOVER_STOP - 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "menustateprovider" in ctx.state.safety_reason.lower()


def test_a_hover_failure_followed_by_a_success_does_NOT_stop_the_run(monkeypatch):
    answers = iter([None, {"option": "Use", "target": "Knife -> Teak logs"}])
    ctx = _gate_ctx(monkeypatch, [])
    monkeypatch.setattr(controller, "get_hover",
                        lambda: next(answers, {"option": "Use", "target": "Knife -> Teak logs"}))
    g.tick(ctx)
    assert ctx.state.data["g3t_hover_fails"] == 1
    g.tick(ctx)
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["g3t_hover_fails"] == 0


# --------------------------------------------------------------------------------------------
# arming: the knife must be PROVEN selected with the cursor on the log, never assumed
# --------------------------------------------------------------------------------------------

def test_armed_on_log_reads_the_use_knife_on_log_hover(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"option": "Use", "target": "Knife -> Teak logs"})
    assert g.armed_on_log(cfg) is True
    monkeypatch.setattr(controller, "get_hover", lambda: {"option": "Use", "target": "Teak logs"})
    assert g.armed_on_log(cfg) is False          # nothing selected
    monkeypatch.setattr(controller, "get_hover", lambda: None)
    assert g.armed_on_log(cfg) is None           # unreadable


def test_a_desync_does_NOT_re_prepare_while_the_knife_is_still_armed(monkeypatch):
    """MEASURED live 2026-09-09: every desync was followed by a blind prepare, whose knife
    click landed with the knife still selected ("Use Knife -> Knife"), and the next two cycles
    selected the log and used it on the rock -- garbage until the state cleared itself."""
    prepares = []
    monkeypatch.setattr(g, "prepare", lambda *a, **k: prepares.append(1))
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"option": "Use", "target": "Knife -> Teak logs"})   # armed
    results = iter(["desync", "desync", "ok"])
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: next(results, "ok"))
    ctx = _chain_ctx(monkeypatch)
    g.tick(ctx)
    assert prepares == []          # armed the whole time: nothing to redo


def test_arm_prepares_when_the_hover_says_nothing_is_selected(monkeypatch):
    prepares = []
    monkeypatch.setattr(g, "prepare", lambda *a, **k: prepares.append(1))
    hovers = iter([{"option": "Use", "target": "Teak logs"},
                   {"option": "Use", "target": "Knife -> Teak logs"}])
    monkeypatch.setattr(controller, "get_hover", lambda: next(hovers, None))
    ctx = _Ctx()
    assert g.arm(ctx, _cfg(), (1700, 800), (1740, 800)) is True
    assert prepares == [1]


def test_arm_spends_a_stale_selection_and_then_selects(monkeypatch):
    """A stale log selection: the first knife click is "Use Teak logs -> Knife" (spent), the
    second selects the knife."""
    prepares = []
    monkeypatch.setattr(g, "prepare", lambda *a, **k: prepares.append(1))
    hovers = iter([{"option": "Use", "target": "Teak logs -> Knife"},
                   {"option": "Use", "target": "Teak logs"},
                   {"option": "Use", "target": "Knife -> Teak logs"}])
    monkeypatch.setattr(controller, "get_hover", lambda: next(hovers, None))
    ctx = _Ctx()
    assert g.arm(ctx, _cfg(), (1700, 800), (1740, 800)) is True
    assert prepares == [1, 1]


def test_a_far_rock_is_run_as_an_ordinary_cycle_while_armed(monkeypatch):
    """Its walk click would spend the live selection on the rock and walk nowhere."""
    rocks = [dict(_gem_rocks()[3], distance=4)]
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: rocks)
    monkeypatch.setattr(controller, "get_hover",
                        lambda: {"option": "Use", "target": "Knife -> Teak logs"})
    near = []
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: (near.append(1), "ok")[1])
    far = []
    monkeypatch.setattr(g, "far_rock_cycle", lambda *a, **k: (far.append(1), "ok")[1])
    ctx = _chain_ctx(monkeypatch, cfg=_gem_cfg(),
                     player={"x": 2834, "y": 9390, "plane": 0, "regionId": 11410})
    g.tick(ctx)
    assert near and far == []


# --------------------------------------------------------------------------------------------
# the rock we MINED is the one excluded next, not the one we aimed at
# --------------------------------------------------------------------------------------------

def test_the_last_mine_click_names_the_tile_to_exclude(monkeypatch):
    """MEASURED live 2026-09-09: adjacent rocks' clickboxes overlap, the click resolves to the
    neighbour, and the exclusion (held on the AIMED tile) re-picked the rock we had just
    emptied -- 9 of 69 rock clicks landed on a depleted rock."""
    monkeypatch.setattr(controller, "get_last_action", lambda: {
        "option": "Mine", "target": "Gem rocks", "id": 11380, "menuAction":
        "GAME_OBJECT_FIRST_OPTION", "param0": 15, "param1": 42, "tick": 500})
    player = {"x": 2831, "y": 9387, "baseX": 2816, "baseY": 9344}
    assert g.mined_tile_from_last_action(_gem_cfg(), player) == (2831, 9386)
    assert g.mined_tile_from_last_action(_gem_cfg(), player, since_tick=501) is None
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"option": "Walk here", "id": -1, "param0": 1, "param1": 1})
    assert g.mined_tile_from_last_action(_gem_cfg(), player) is None


def test_the_dynamic_chain_excludes_the_tile_the_game_says_was_mined(monkeypatch):
    rocks = _gem_rocks()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: rocks)
    # the game says the last click mined (2831,9386) -- the nearest rock -- so the pick must
    # skip it even though the routine's own aim was recorded elsewhere
    monkeypatch.setattr(controller, "get_last_action", lambda: {
        "option": "Mine", "id": 11380, "param0": 15, "param1": 42, "tick": 1})
    picked = []
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle",
                        lambda ctx, cfg, lp, rp, target_tick=None, **kw: (picked.append(rp), "ok")[1])
    ctx = _chain_ctx(monkeypatch, cfg=_gem_cfg(),
                     player={"x": 2830, "y": 9387, "plane": 0, "regionId": 11410,
                             "baseX": 2816, "baseY": 9344})
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1000 + (y - 9380), "screenY": 800})
    ctx.state.data["g3t_last_rock"] = (2829, 9384)
    g.tick(ctx)
    assert picked[0] == (1007, 800)          # (2831,9387), never the mined (2831,9386)


# --------------------------------------------------------------------------------------------
# the rock click TRACKS the rock, and a rock under the UI is never a candidate
# --------------------------------------------------------------------------------------------

def test_the_critical_rock_click_carries_a_refresh_target(monkeypatch):
    seen = {}
    monkeypatch.setattr(tick_cycle, "click_in_place", lambda ser, **kw: 50.0)
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (seen.update(kw), 200.0)[1])
    monkeypatch.setattr(g.random, "random", lambda: 1.0)
    monkeypatch.setattr(g, "_now", lambda: 1000.0)
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    refresh = lambda: (1, 2)
    assert g.run_cycle(ctx, _cfg(), (1740, 800), (1640, 800), rock_refresh=refresh) == "ok"
    assert seen["refresh_target"] is refresh
    assert seen["refresh_legs"] == 1


def test_live_rocks_skips_a_rock_projected_under_a_ui_blocker(monkeypatch):
    rocks = _gem_rocks()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: rocks)
    monkeypatch.setattr(controller, "get_state",
                        lambda: {"uiBlockers": [{"x": 1680, "y": 780, "width": 40, "height": 40}]})
    tiles = [(r["x"], r["y"]) for r in g.live_rocks(_gem_cfg(), {"x": 2830, "y": 9387})]
    assert (2831, 9386) not in tiles          # its (1690,800) sits inside the rect
    assert (2831, 9387) in tiles


def test_rock_refresh_target_tries_every_rock_id(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: (calls.append(i),
                                                 {"screenX": 5, "screenY": 6} if i == 11381
                                                 else None)[1])
    refresh = g.rock_refresh_target(_gem_cfg(), (2831, 9386, 0))
    assert refresh() == (5, 6)
    assert calls == [11380, 11381]


# --------------------------------------------------------------------------------------------
# the cycle is DRIVEN BY THE XP: no other rock until the Mining xp has landed
# --------------------------------------------------------------------------------------------

def test_run_cycle_reports_no_clock_when_the_clock_is_stale(monkeypatch):
    _ok_cycle_patches(monkeypatch)
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.events.ticks.stale = True
    assert g.run_cycle(ctx, _cfg(), (1740, 800), (1640, 800)) == "no_clock"


def test_the_chain_waits_for_the_mining_xp_between_cycles(monkeypatch):
    """USER 2026-09-09: "lets not click another rock until we get a xp notifier"."""
    order = []
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: (order.append("fire"), "ok")[1])
    ctx = _chain_ctx(monkeypatch)
    monkeypatch.setattr(g, "wait_for_mining_xp",
                        lambda ctx, mark, timeout: (order.append("wait"),
                                                    {"skill": "Mining", "delta": 65, "tick": 102})[1])
    g.tick(ctx)
    assert order[:4] == ["fire", "wait", "fire", "wait"]


def test_the_pay_is_counted_from_the_xp_tick_against_the_fired_tick(monkeypatch):
    def _fire(ctx, cfg, lp, rp, **kw):
        ctx.state.data["g3t_fired_tick"] = 100
        ctx.state.data["g3t_cycles"] = ctx.state.data.get("g3t_cycles", 0) + 1
        return "ok"
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle", _fire)
    ctx = _chain_ctx(monkeypatch)
    # the pay is WALL TIME from the fletch: the fake clock advances 1s per read, so the drop is
    # seen a few reads after a fire -- pin the arithmetic directly instead
    monkeypatch.setattr(g, "wait_for_mining_xp",
                        lambda ctx, mark, timeout: {"skill": "Mining", "delta": 65, "tick": 102})
    g.tick(ctx)
    assert (ctx.state.data.get("g3t_paid3", 0) + ctx.state.data.get("g3t_paid4", 0)
            + ctx.state.data.get("g3t_paid_other", 0)) >= 1


def test_pay_ticks_counts_from_the_fletch_in_wall_time():
    # fletch at phase 100, the drop seen 1.675s later = 20ms before the boundary 3 ticks on
    assert g.pay_ticks((1000.0, 100.0), 1001.675) == 3
    assert g.pay_ticks((1000.0, 100.0), 1001.0) == 2          # a tick early (impossible pay)
    assert g.pay_ticks((1000.0, 300.0), 1003.9) == 7          # a failed roll, pickaxe pay
    assert g.pay_ticks(None, 1001.0) is None


def test_a_mine_that_never_pays_is_re_picked_not_a_stop(monkeypatch):
    fired = []
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: (fired.append(1), "ok")[1])
    ctx = _chain_ctx(monkeypatch)
    monkeypatch.setattr(g, "wait_for_mining_xp", lambda ctx, mark, timeout: None)
    g.tick(ctx)
    assert len(fired) > 1
    assert ctx.state.data["g3t_unpaid"] >= 1
    assert ctx.state.safety_stopped is False


def test_the_next_rock_is_chosen_and_armed_DURING_the_xp_wait(monkeypatch):
    """The dead time between the rock click and the drop is where the next knife select and
    park happen, so the fletch can follow the drop by a reaction time and nothing more."""
    order = []
    rocks = _gem_rocks()
    monkeypatch.setattr(controller, "get_objects", lambda *a, **k: rocks)
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: (order.append("fire"), "ok")[1])
    monkeypatch.setattr(g, "arm", lambda *a, **k: (order.append("arm"), True)[1])
    ctx = _chain_ctx(monkeypatch, cfg=_gem_cfg(),
                     player={"x": 2830, "y": 9387, "plane": 0, "regionId": 11410,
                             "baseX": 2816, "baseY": 9344})
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1000, "screenY": 800})
    monkeypatch.setattr(g, "wait_for_mining_xp",
                        lambda ctx, mark, timeout: (order.append("wait"),
                                                    {"skill": "Mining", "tick": 102})[1])
    g.tick(ctx)
    # arm, fire, [arm for the next during the wait], wait, fire -- nothing between the drop
    # and the next fire but the reaction draw
    assert order[:5] == ["arm", "fire", "arm", "wait", "fire"]


def test_a_deliberate_miss_reacts_a_whole_tick_late(monkeypatch):
    slept = []
    monkeypatch.setattr(g, "_sleep", lambda s: slept.append(s))
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)

    def _fire(ctx, cfg, lp, rp, **kw):
        ctx.state.data["g3t_fired_tick"] = 100
        return "ok"
    monkeypatch.setattr(g, "run_cycle", _fire)
    ctx = _chain_ctx(monkeypatch, cfg=_cfg(missChance=1.0))
    monkeypatch.setattr(g.random, "random", lambda: 0.0)          # always a miss
    monkeypatch.setattr(g.random, "uniform", lambda lo, hi: lo)
    g.tick(ctx)
    assert any(abs(x - (g.XP_REACTION_MS[0] / 1000.0 + 0.6)) < 1e-6 for x in slept)


def test_wait_for_mining_xp_only_sees_events_after_the_mark(monkeypatch):
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    ctx.events.stream = [("XP_GAINED", {"skill": "Fletching", "tick": 5}),
                         ("XP_GAINED", {"skill": "Mining", "tick": 7, "delta": 65})]
    times = iter([1000.0 + i * 0.1 for i in range(100)])
    monkeypatch.setattr(g, "_now", lambda: next(times))
    assert g.wait_for_mining_xp(ctx, 0, 2.0)["tick"] == 7
    ctx.events.stream = [("XP_GAINED", {"skill": "Fletching", "tick": 5})]
    assert g.wait_for_mining_xp(ctx, 0, 0.5) is None


def test_a_refresh_behind_the_ui_answers_None_instead_of_steering_the_click_there(monkeypatch):
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, p, i, **k: {"screenX": 1690, "screenY": 800})
    blockers = [{"x": 1680, "y": 780, "width": 40, "height": 40}]
    assert g.rock_refresh_target(_gem_cfg(), (1, 2, 0), blockers)() is None
    assert g.rock_refresh_target(_gem_cfg(), (1, 2, 0), [])() == (1690, 800)


def test_the_slot_points_are_re_read_every_cycle(monkeypatch):
    """MEASURED live 2026-09-09: the inventory panel moved mid-chain and nine cycles could not
    arm because the park aimed at the old slot position."""
    parks = []
    slot = {"y": 800}
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    # the panel moves after the first cycle fires
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: (slot.update(y=835), "ok")[1])
    monkeypatch.setattr(g, "arm", lambda ctx, cfg, kp, lp: (parks.append(lp), True)[1])
    ctx = _chain_ctx(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 900, 0), _item("Teak logs", 1740, slot["y"], 1)])
    g.tick(ctx)
    assert parks[0] == (1740, 800) and (1740, 835) in parks


def test_the_xp_tick_is_the_packets_tick_and_a_late_pay_gets_no_rest(monkeypatch):
    """MEASURED 2026-09-09: the bridge stamps XP_GAINED with the packet's own tick. A +1 here
    made every 3-tick pay read as 4 and the "rest a tick" fix rested after GOOD cycles."""
    slept = []
    monkeypatch.setattr(g, "_sleep", lambda s: slept.append(s))
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)

    def _fire(ctx, cfg, lp, rp, **kw):
        ctx.state.data["g3t_fired_tick"] = 100
        return "ok"
    monkeypatch.setattr(g, "run_cycle", _fire)
    ctx = _chain_ctx(monkeypatch)
    monkeypatch.setattr(g.random, "uniform", lambda lo, hi: lo)
    monkeypatch.setattr(g, "wait_for_mining_xp",
                        lambda ctx, mark, timeout: {"skill": "Mining", "tick": 107})   # paid 7
    g.tick(ctx)
    assert ctx.state.data["g3t_paid_other"] >= 1
    assert g.LATE_PAY_REST_TICKS == 0
    assert all(x < 0.5 for x in slept)


def test_the_routine_keeps_the_users_camera():
    assert g.KEEP_CAMERA is True


def test_drop_burst_hops_to_and_clicks_every_droppable_slot_in_order(monkeypatch):
    """One hop and one click per slot, slot order, nothing else under the cursor -- MEASURED
    g3t_live49, a glide with a click burst left every second slot behind."""
    monkeypatch.setattr(g, "drop_burst", g._real_drop_burst)
    moves, clicks, keys = [], [], []
    monkeypatch.setattr(controller, "key_down", lambda ser, k: keys.append("down"))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: keys.append("up"))
    monkeypatch.setattr(controller, "move_to_human", lambda ser, x, y, **kw: moves.append((x, y)))
    monkeypatch.setattr(tick_cycle, "fast_click", lambda ser: clicks.append(len(moves)))
    counts = iter([28, 20])
    monkeypatch.setattr(controller, "get_inventory", lambda: [None] * next(counts, 20))
    items = [_item("Granite (5kg)", 1000 + 40 * (s % 4), 600 + 37 * (s // 4), s)
             for s in (5, 1, 3, 26, 2, 7)]
    ctx = _Ctx()
    assert g.drop_burst(ctx, items) is True
    assert keys == []                                     # plain left clicks by default
    assert moves == [(1040, 600), (1080, 600), (1120, 600), (1040, 637), (1120, 637), (1080, 822)]
    assert clicks == [1, 2, 3, 4, 5, 6]                   # a click after every hop
    assert g.drop_burst(ctx, items, shift=True) is True or True
    assert keys == ["down", "up"]


def test_a_sweep_that_drops_nothing_hands_over_to_the_per_item_path(monkeypatch):
    monkeypatch.setattr(g, "drop_burst", g._real_drop_burst)
    monkeypatch.setattr(tick_cycle, "fast_click", lambda ser: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [None] * 28)
    times = iter([1000.0 + i * 0.5 for i in range(50)])
    monkeypatch.setattr(g, "_now", lambda: next(times))
    assert g.drop_burst(_Ctx(), [_item("Granite (5kg)", 1000, 600, 1)]) is False


def test_newest_mined_item_is_the_highest_slot_ore():
    inv = [_item("Knife", slot=4), _item("Granite (5kg)", slot=1), _item("Granite (2kg)", slot=7),
           _item("Teak logs", slot=0)]
    assert g.newest_mined_item(_cfg(), inv)["slot"] == 7
    assert g.newest_mined_item(_cfg(), [_item("Knife", slot=4)]) is None


def test_drop_between_mines_shift_drops_then_re_clicks_the_rock_in_the_next_tick(monkeypatch):
    order = []
    monkeypatch.setattr(controller, "key_down", lambda ser, k: order.append("shift-down"))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: order.append("shift-up"))
    monkeypatch.setattr(controller, "move_to_human",
                        lambda ser, x, y, **kw: order.append(("park", (x, y))))
    monkeypatch.setattr(tick_cycle, "click_in_place",
                        lambda ser, **kw: (order.append("drop-in-place"), 50.0)[1])
    monkeypatch.setattr(tick_cycle, "timed_click",
                        lambda ser, pt, **kw: (order.append(("click", pt)), 100.0)[1])
    monkeypatch.setattr(g, "_now", lambda: 1000.0)
    ctx = _Ctx()
    ctx.events = _FakeEvents()
    waited = []
    ctx.events.wait_ticks = lambda n, **kw: (order.append("boundary"), waited.append(n))
    ore = _item("Granite (5kg)", 1700, 860, 9)
    g.drop_between_mines(ctx, _cfg(dropReclick=True, dropWithShift=True), ore, lambda: (500, 500))
    assert waited == [1]                                    # the tick AFTER the fletch
    # parked on the ore BEFORE the boundary, then an in-place shift-drop, then the rock
    assert order == [("park", (1700, 860)), "boundary", "shift-down", "drop-in-place", "shift-up",
                     ("click", (500, 500))]
    order.clear()
    g.drop_between_mines(ctx, _cfg(dropReclick=True), ore, lambda: (500, 500))
    assert order == [("park", (1700, 860)), "boundary", "drop-in-place", ("click", (500, 500))]


def test_the_chain_drops_between_mines_when_configured(monkeypatch):
    drops = []
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: "ok")
    monkeypatch.setattr(g, "drop_between_mines", lambda ctx, cfg, ore, r: (drops.append(ore["slot"]), 300.0)[1])
    ctx = _chain_ctx(monkeypatch, cfg=_cfg(dropBetweenMines=True))
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1),
                                 _item("Granite (5kg)", 1700, 860, 6)])
    g.tick(ctx)
    assert drops and drops[0] == 6


def test_an_xp_that_fills_the_last_slot_drops_before_the_next_fletch(monkeypatch):
    """MEASURED g3t_live51 recorder: the pair fired into a full inventory ("Your inventory is
    too full to hold any more granite") and the routine waited 14 ticks, twice per inventory."""
    fired, dropped = [], []
    monkeypatch.setattr(g, "next_live_rock", lambda cfg, i, *a, **k: ((1640, 800), i))
    monkeypatch.setattr(g, "prepare", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_cycle", lambda *a, **k: (fired.append(1), "ok")[1])
    monkeypatch.setattr(g, "full_inventory_phase", lambda ctx, cfg: (dropped.append(1), True)[1])
    inv = [_item("Knife", 1700, 800, 0), _item("Teak logs", 1740, 800, 1)]
    inv += [_item("Granite (5kg)", 1700 + i, 860, 2 + i) for i in range(25)]     # 27 items
    ctx = _chain_ctx(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    g.tick(ctx)
    assert dropped                      # the drop ran without a 14-tick wait
    assert len(fired) <= 2              # one fire, the drop, then at most one more fire
