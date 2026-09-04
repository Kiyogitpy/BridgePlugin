import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import bridge_events
import controller
import mining


def _rock(x, y, obj_id=11364, name="Iron rocks", dist=3, actions=("Mine",), **over):
    d = {"id": obj_id, "name": name, "x": x, "y": y, "plane": 0, "distance": dist,
         "visible": True, "screenX": 100 + x, "screenY": 100 + y}
    if actions is not None:
        d["actions"] = list(actions)
    d.update(over)
    return d


# --- config -------------------------------------------------------------------

def test_get_config_reads_bridge_and_fills_defaults(monkeypatch):
    monkeypatch.setattr(controller, "get_mining_config",
                        lambda: {"enabled": True, "rockNames": ["Iron rocks"], "maxRockDistance": 12})
    cfg = mining.get_config(None)
    assert cfg["rockNames"] == ["Iron rocks"]
    assert cfg["maxRockDistance"] == 12
    assert cfg["breaks"] == mining.DEFAULT_CONFIG["breaks"]   # missing keys come from the defaults


def test_get_config_falls_back_when_bridge_is_down(monkeypatch):
    monkeypatch.setattr(controller, "get_mining_config", lambda: None)
    assert mining.get_config(None) == mining.DEFAULT_CONFIG


def test_get_config_keeps_an_empty_panel_selection(monkeypatch):
    # A user who cleared every rock in the panel gets an idle routine, NOT the defaults' rocks.
    monkeypatch.setattr(controller, "get_mining_config", lambda: {"rockNames": []})
    assert mining.get_config(None)["rockNames"] == []


# --- pick_rock ----------------------------------------------------------------

CFG = {"rockNames": ["Iron rocks"], "maxRockDistance": 10}


def test_pick_rock_nearest_matching_name():
    objs = [_rock(1, 1, dist=5), _rock(2, 2, dist=2), _rock(3, 3, name="Coal rocks", dist=1)]
    assert mining.pick_rock(objs, CFG)["x"] == 2


def test_pick_rock_ignores_case_and_distance_and_visibility():
    objs = [_rock(1, 1, name="iron ROCKS", dist=4), _rock(2, 2, dist=11),
            _rock(3, 3, dist=1, visible=False)]
    assert mining.pick_rock(objs, CFG)["x"] == 1


def test_pick_rock_skips_objects_without_a_mine_action():
    # A depleted variant can share a display name; the Mine action is what says it is mineable.
    objs = [_rock(1, 1, dist=1, actions=("Examine",)), _rock(2, 2, dist=5)]
    assert mining.pick_rock(objs, CFG)["x"] == 2
    # ...but an object with no actions reported at all is still accepted (older client build).
    assert mining.pick_rock([_rock(1, 1, dist=1, actions=None)], CFG)["x"] == 1


def test_pick_rock_excludes_tiles():
    objs = [_rock(1, 1, dist=1), _rock(2, 2, dist=5)]
    assert mining.pick_rock(objs, CFG, exclude=[(1, 1, 0)])["x"] == 2
    assert mining.pick_rock(objs, CFG, exclude=[(1, 1, 0), (2, 2, 0)]) is None


# --- mine_rock ----------------------------------------------------------------

class _Events:
    """Scripted REAL EventStream: controller.poll_udp is patched so every pump() pops the next
    scripted item -- a (type, payload) pair or None (nothing arrived). Time is the scene clock,
    which await_outcome's injected sleep_fn advances, so a test never really sleeps."""

    def __init__(self, script, clock, monkeypatch):
        self.script = list(script)
        self.clock = clock
        self.pumps = 0

        def _poll(sock):
            self.pumps += 1
            if not self.script:
                return []
            item = self.script.pop(0)
            return [] if item is None else [item]
        monkeypatch.setattr(controller, "poll_udp", _poll)
        self.stream = bridge_events.EventStream(object())


class _Scene:
    """`readings` is a list of (rock_present, mining_xp, is_idle) snapshots. Index 0 is the
    world BEFORE the click; every get_object_at call (one per poll) advances to the next
    snapshot, and the xp/idle reads in the same poll see that same snapshot."""

    def __init__(self):
        self.clicks = []
        self.hovers = []
        self.objects = []
        self.readings = []
        self.idx = 0
        self.now = 1000.0
        self.inventory = []
        self.monkeypatch = None

    def cur(self):
        return self.readings[min(self.idx, len(self.readings) - 1)]

    def advance(self):
        self.idx += 1

    def events(self, script):
        return _Events(script, self, self.monkeypatch).stream


@pytest.fixture
def scene(monkeypatch):
    sc = _Scene()
    sc.monkeypatch = monkeypatch
    monkeypatch.setattr(mining, "_now", lambda: sc.now)

    def _sleep(s):
        sc.now += s
    monkeypatch.setattr(mining, "_sleep", _sleep)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: _sleep(a))

    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: sc.clicks.append((ent["x"], ent["y"], ent["id"])) or True)
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, ent, **kw: sc.hovers.append((ent["x"], ent["y"])) or True)
    monkeypatch.setattr(controller, "object_refresh_target",
                        lambda x, y, plane, obj_id, radius=None: (lambda: (x + 100, y + 100)))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: list(sc.objects))

    def _get_object_at(x, y, plane, obj_id, radius=None):
        sc.advance()
        if sc.cur()[0]:
            for o in sc.objects:
                if (o["x"], o["y"], o["plane"], o["id"]) == (x, y, plane, obj_id):
                    return dict(o)
        return None
    monkeypatch.setattr(controller, "get_object_at", _get_object_at)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"miningXp": sc.cur()[1], "x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": sc.cur()[2]})
    monkeypatch.setattr(controller, "get_inventory", lambda: list(sc.inventory))
    return sc


def make_ctx(events=None):
    state = controller.ControllerState()
    state.config = dict(mining.DEFAULT_CONFIG, rockNames=["Iron rocks"], maxRockDistance=10)
    return types.SimpleNamespace(ser=None, state=state, events=events)


def _xp(delta=35):
    return ("XP_GAINED", {"skill": "Mining", "xp": 5035, "delta": delta})


def _despawn(x, y):
    return ("OBJECT_DESPAWNED", {"id": 11364, "x": x, "y": y, "plane": 0})


def test_mine_rock_depleted_on_xp_event_confirmed_by_http(scene):
    a, b = _rock(1, 1, dist=1), _rock(2, 2, dist=3)
    scene.objects = [a, b]
    scene.readings = [(True, 5000, False), (False, 5035, False)]
    ev = scene.events([_xp()])
    ctx = make_ctx(ev)
    outcome, nxt = mining.mine_rock(ctx, a, ctx.state.config)
    assert outcome == "depleted"
    assert scene.clicks == [(1, 1, 11364)]
    assert scene.hovers == [(2, 2)]           # pre-hovered the next rock while mining
    assert nxt["x"] == 2
    assert scene.now - 1000.0 < 1.0           # reacted on the event, not a poll cadence


def test_mine_rock_despawn_event_matches_only_its_own_tile(scene):
    a, b = _rock(1, 1, dist=1), _rock(2, 2, dist=3)
    scene.objects = [a, b]
    scene.readings = [(True, 5000, False), (False, 5000, False)]
    # a neighbour's rock despawns first (no match, so no HTTP read at all), then ours
    ev = scene.events([_despawn(2, 2), _despawn(1, 1)])
    ctx = make_ctx(ev)
    outcome, _ = mining.mine_rock(ctx, a, ctx.state.config)
    assert outcome == "depleted"
    assert scene.idx == 1                     # one HTTP confirm: our own despawn only


def test_mine_rock_xp_without_depletion_keeps_mining(scene):
    # Gem rocks / amethyst: ore lands but the rock stays. Keep waiting on the SAME rock;
    # the deadline resets so a slow multi-ore rock never times out mid-way.
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    # One HTTP confirm per matched signal (a quiet pump reads nothing): ore-with-rock-up, then gone.
    scene.readings = [(True, 5000, False), (True, 5035, False), (False, 5070, False)]
    ev = scene.events([_xp(), None, _xp()])
    ctx = make_ctx(ev)
    outcome, _ = mining.mine_rock(ctx, a, ctx.state.config)
    assert outcome == "depleted"
    assert scene.clicks == [(1, 1, 11364)]     # never re-clicked
    assert scene.now - 1000.0 < 1.0            # both reactions on the event, no poll cadence


def test_mine_rock_idle_with_no_progress_means_the_click_failed(scene):
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, False), (True, 5000, True)]
    ev = scene.events([None] * 200)
    ctx = make_ctx(ev)
    outcome, _ = mining.mine_rock(ctx, a, ctx.state.config)
    assert outcome == "idle"
    elapsed = scene.now - 1000.0
    assert mining.IDLE_GRACE_S <= elapsed < mining.ROCK_BUDGET_S


def test_mine_rock_idle_grace_budgets_the_walk(scene):
    # 6 tiles away: an "idle" read before the walk registers is NOT a failed click.
    a = _rock(1, 1, dist=6)
    scene.objects = [a]
    scene.readings = [(True, 5000, True), (True, 5000, True), (True, 5000, False),
                      (False, 5035, False)]
    ev = scene.events([None, None, _xp()])
    ctx = make_ctx(ev)
    outcome, _ = mining.mine_rock(ctx, a, ctx.state.config)
    assert outcome == "depleted"


def test_mine_rock_times_out(scene):
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, False)]
    ctx = make_ctx(scene.events([]))
    outcome, _ = mining.mine_rock(ctx, a, ctx.state.config)
    assert outcome == "timeout"
    assert scene.now - 1000.0 >= mining.ROCK_BUDGET_S


def test_mine_rock_no_click(scene, monkeypatch):
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: False)
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, False)]
    ctx = make_ctx(scene.events([]))
    assert mining.mine_rock(ctx, a, ctx.state.config) == ("no_click", None)
    assert scene.hovers == []


def test_mine_rock_without_event_stream_polls_http(scene):
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, False), (True, 5000, False), (False, 5035, False)]
    ctx = make_ctx(None)
    outcome, _ = mining.mine_rock(ctx, a, ctx.state.config)
    assert outcome == "depleted"


# --- tick: chaining -----------------------------------------------------------

def test_tick_chains_onto_the_prehovered_rock(scene):
    a, b = _rock(1, 1, dist=1), _rock(2, 2, dist=3)
    scene.objects = [a, b]
    scene.readings = [(True, 5000, True),      # before the click: idle, both rocks up
                      (False, 5035, False),    # a depletes
                      (True, 5035, False),     # b revalidated before its click
                      (False, 5070, False)]    # b depletes; nothing left to mine
    ctx = make_ctx(scene.events([_xp(), _xp(), None]))
    mining.tick(ctx)
    assert scene.clicks == [(1, 1, 11364), (2, 2, 11364)]
    assert scene.hovers == [(2, 2)]            # nothing left to pre-hover after b


def test_tick_idle_when_no_rock_selected(scene):
    ctx = make_ctx()
    ctx.state.config["rockNames"] = []
    scene.objects = [_rock(1, 1)]
    scene.readings = [(True, 5000, True)]
    mining.tick(ctx)
    assert scene.clicks == []


def test_tick_does_not_reclick_mid_swing(scene):
    ctx = make_ctx()
    scene.objects = [_rock(1, 1)]
    scene.readings = [(True, 5000, False)]     # not idle: already swinging
    mining.tick(ctx)
    assert scene.clicks == []


def test_tick_hands_back_on_a_non_depleted_outcome(scene):
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, True), (True, 5000, True)]
    ctx = make_ctx(scene.events([None] * 400))
    mining.tick(ctx)
    assert scene.clicks == [(1, 1, 11364)]     # one attempt, then the engine loop's turn


# --- banking (2026-09-04) ---------------------------------------------------------

def _full_inventory():
    return [{"name": "Rune pickaxe"}] + [{"name": "Iron ore"}] * 27


def test_get_config_hands_the_inventory_full_stop_to_the_routine(monkeypatch):
    monkeypatch.setattr(controller, "get_mining_config",
                        lambda: {"rockNames": ["Iron rocks"], "safety": {"stopOnInventoryFull": True}})
    assert mining.get_config(None)["safety"]["stopOnInventoryFull"] is False
    assert mining.DEFAULT_CONFIG["safety"]["stopOnInventoryFull"] is False


def test_tick_banks_a_full_inventory_at_a_nearby_bank_and_walks_back(scene, monkeypatch):
    scene.inventory = _full_inventory()
    scene.objects = [_rock(1, 1)]
    scene.readings = [(True, 5000, True)]
    calls = []
    monkeypatch.setattr(mining.banker, "find_nearby_bank",
                        lambda cfg: calls.append(("find", cfg)) or {"name": "Bank chest", "distance": 12})
    monkeypatch.setattr(mining.banker, "open_bank", lambda ctx, bank: calls.append(("open", bank["name"])) or True)

    def _deposit(ctx, cfg):
        calls.append(("deposit", cfg["keepItems"]))
        scene.inventory = [{"name": "Rune pickaxe"}]
    monkeypatch.setattr(mining.banker, "deposit_inventory", _deposit)
    monkeypatch.setattr(mining.banker, "close_bank", lambda ctx: calls.append(("close",)))
    monkeypatch.setattr(mining.travel, "walk_to", lambda ctx, t, **kw: calls.append(("walk", t["x"], t["y"], kw)) or True)
    ctx = make_ctx()
    ctx.state.data["mining_stand_tile"] = {"x": 3010, "y": 3000, "plane": 0}   # 10 tiles from the bank
    mining.tick(ctx)
    assert [c[0] for c in calls] == ["find", "open", "deposit", "close", "walk"]
    assert calls[0][1]["maxBankDistance"] == mining.BANK_MAX_DISTANCE
    assert calls[2][1] == ["pickaxe"]
    assert calls[4][1:3] == (3010, 3000) and calls[4][3] == {"goal_adjacent": False}
    assert scene.clicks == []                  # no rock click on a banking tick
    assert not ctx.state.safety_stopped


def test_tick_stops_when_full_and_no_bank_is_in_range(scene, monkeypatch):
    scene.inventory = _full_inventory()
    scene.objects = [_rock(1, 1)]
    scene.readings = [(True, 5000, True)]
    monkeypatch.setattr(mining.banker, "find_nearby_bank", lambda cfg: None)
    ctx = make_ctx()
    mining.tick(ctx)
    assert ctx.state.safety_stopped
    assert scene.clicks == []


def test_chain_stops_before_clicking_with_a_full_inventory(scene):
    a, b = _rock(1, 1, dist=1), _rock(2, 2, dist=3)
    scene.objects = [a, b]
    scene.readings = [(True, 5000, True), (False, 5035, False), (True, 5035, False)]
    ctx = make_ctx(scene.events([_xp(), None]))
    # The ore from `a` fills the last slot.
    real_get_object_at = controller.get_object_at

    def _fill_then(x, y, plane, obj_id, radius=None):
        out = real_get_object_at(x, y, plane, obj_id, radius)
        scene.inventory = _full_inventory()
        return out
    scene.monkeypatch.setattr(controller, "get_object_at", _fill_then)
    mining.tick(ctx)
    assert scene.clicks == [(1, 1, 11364)]     # b is never clicked
    assert ctx.state.data["mining_stand_tile"] == {"x": 3000, "y": 3000, "plane": 0}


def test_arrival_at_a_far_rock_rebases_the_idle_grace(scene, monkeypatch):
    # 6 tiles away (3.6s walk budget). PLAYER_STOPPED + /player.moving false = arrived, after
    # which an idle player with no ore is a failed click IDLE_GRACE_S later -- not walk + grace.
    a = _rock(1, 1, dist=6)
    scene.objects = [a]
    scene.readings = [(True, 5000, True), (True, 5000, True)]
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"miningXp": scene.cur()[1], "x": 3000, "y": 3000, "moving": False})
    ctx = make_ctx(scene.events([("PLAYER_STOPPED", {"x": 3005, "y": 3000, "plane": 0})] + [None] * 300))
    outcome, _ = mining.mine_rock(ctx, a, ctx.state.config)
    assert outcome == "idle"
    elapsed = scene.now - 1000.0
    assert mining.IDLE_GRACE_S <= elapsed < 6 * mining.WALK_S_PER_TILE + mining.IDLE_GRACE_S
