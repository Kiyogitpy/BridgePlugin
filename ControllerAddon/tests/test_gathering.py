"""The gathering engine (gathering.py) driven through the Mining profile.

Was tests/test_mining.py until 2026-09-07, when the loop was extracted out of routines/mining.py
so routines/woodcutting.py could share it. Every test below is the mining one it was, re-pointed
at gathering.*; the woodcutting-specific behaviour lives in test_woodcutting.py.
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import bridge_events
import controller
import gathering
import mining

P = mining.PROFILE


def _rock(x, y, obj_id=11364, name="Iron rocks", dist=3, actions=("Mine",), **over):
    d = {"id": obj_id, "name": name, "x": x, "y": y, "plane": 0, "distance": dist,
         "visible": True, "screenX": 100 + x, "screenY": 100 + y}
    if actions is not None:
        d["actions"] = list(actions)
    d.update(over)
    return d


def pick(objs, cfg=None, **kw):
    return gathering.pick_target(P, objs, cfg if cfg is not None else CFG, **kw)


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


# --- pick_target --------------------------------------------------------------

CFG = {"rockNames": ["Iron rocks"], "maxRockDistance": 10}


def test_pick_target_nearest_matching_name():
    objs = [_rock(1, 1, dist=5), _rock(2, 2, dist=2), _rock(3, 3, name="Coal rocks", dist=1)]
    assert pick(objs)["x"] == 2


def test_pick_target_ignores_case_and_distance_and_visibility():
    objs = [_rock(1, 1, name="iron ROCKS", dist=4), _rock(2, 2, dist=11),
            _rock(3, 3, dist=1, visible=False)]
    assert pick(objs)["x"] == 1


def test_pick_target_skips_objects_without_the_profiles_action():
    # A depleted variant can share a display name; the Mine action is what says it is mineable.
    objs = [_rock(1, 1, dist=1, actions=("Examine",)), _rock(2, 2, dist=5)]
    assert pick(objs)["x"] == 2
    # ...but an object with no actions reported at all is still accepted (older client build).
    assert pick([_rock(1, 1, dist=1, actions=None)])["x"] == 1


def test_pick_target_excludes_tiles():
    objs = [_rock(1, 1, dist=1), _rock(2, 2, dist=5)]
    assert pick(objs, exclude=[(1, 1, 0)])["x"] == 2
    assert pick(objs, exclude=[(1, 1, 0), (2, 2, 0)]) is None


# --- gather -------------------------------------------------------------------

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
    """`readings` is a list of (object_present, skill_xp, is_idle) snapshots. Index 0 is the
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
        self.xp_field = "miningXp"

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
    monkeypatch.setattr(gathering, "_now", lambda: sc.now)

    def _sleep(s):
        sc.now += s
    monkeypatch.setattr(gathering, "_sleep", _sleep)
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
                        lambda: {sc.xp_field: sc.cur()[1], "x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": sc.cur()[2]})
    monkeypatch.setattr(controller, "get_inventory", lambda: list(sc.inventory))
    return sc


def make_ctx(events=None, config=None):
    state = controller.ControllerState()
    state.config = config or dict(mining.DEFAULT_CONFIG, rockNames=["Iron rocks"],
                                  maxRockDistance=10)
    return types.SimpleNamespace(ser=None, state=state, events=events)


def _xp(delta=35):
    return ("XP_GAINED", {"skill": "Mining", "xp": 5035, "delta": delta})


def _despawn(x, y):
    return ("OBJECT_DESPAWNED", {"id": 11364, "x": x, "y": y, "plane": 0})


def test_gather_depleted_on_xp_event_confirmed_by_http(scene):
    a, b = _rock(1, 1, dist=1), _rock(2, 2, dist=3)
    scene.objects = [a, b]
    scene.readings = [(True, 5000, False), (False, 5035, False)]
    ev = scene.events([_xp()])
    ctx = make_ctx(ev)
    outcome, nxt = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "depleted"
    assert scene.clicks == [(1, 1, 11364)]
    assert nxt["x"] == 2                      # the next target is still resolved up front
    # ...but NOT pre-hovered: this rock was still being walked to and then went straight to
    # depleted, so the player never stood still. See test_the_prehover_waits_for_stillness.
    assert scene.hovers == []
    assert scene.now - 1000.0 < 1.0           # reacted on the event, not a poll cadence


def test_gather_despawn_event_matches_only_its_own_tile(scene):
    a, b = _rock(1, 1, dist=1), _rock(2, 2, dist=3)
    scene.objects = [a, b]
    scene.readings = [(True, 5000, False), (False, 5000, False)]
    # a neighbour's rock despawns first (no match, so no HTTP read at all), then ours
    ev = scene.events([_despawn(2, 2), _despawn(1, 1)])
    ctx = make_ctx(ev)
    outcome, _ = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "depleted"
    assert scene.idx == 1                     # one HTTP confirm: our own despawn only


def test_gather_xp_without_depletion_keeps_working(scene):
    # Gem rocks / amethyst (and any tree that has not fallen yet): a resource lands but the
    # object stays. Keep waiting on the SAME one; the deadline resets so a slow multi-resource
    # object never times out mid-way.
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    # One HTTP confirm per matched signal (a quiet pump reads nothing): ore-with-rock-up, then gone.
    scene.readings = [(True, 5000, False), (True, 5035, False), (False, 5070, False)]
    ev = scene.events([_xp(), None, _xp()])
    ctx = make_ctx(ev)
    outcome, _ = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "depleted"
    assert scene.clicks == [(1, 1, 11364)]     # never re-clicked
    assert scene.now - 1000.0 < 1.0            # both reactions on the event, no poll cadence


def test_gather_idle_with_no_progress_means_the_click_failed(scene):
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, False), (True, 5000, True)]
    ev = scene.events([None] * 200)
    ctx = make_ctx(ev)
    outcome, _ = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "idle"
    elapsed = scene.now - 1000.0
    assert gathering.IDLE_GRACE_S <= elapsed < gathering.GATHER_BUDGET_S


def test_gather_idle_grace_budgets_the_walk(scene):
    # 6 tiles away: an "idle" read before the walk registers is NOT a failed click.
    a = _rock(1, 1, dist=6)
    scene.objects = [a]
    scene.readings = [(True, 5000, True), (True, 5000, True), (True, 5000, False),
                      (False, 5035, False)]
    ev = scene.events([None, None, _xp()])
    ctx = make_ctx(ev)
    outcome, _ = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "depleted"


def test_gather_times_out(scene):
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, False)]
    ctx = make_ctx(scene.events([]))
    outcome, _ = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "timeout"
    assert scene.now - 1000.0 >= gathering.GATHER_BUDGET_S


def test_gather_no_click(scene, monkeypatch):
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: False)
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, False)]
    ctx = make_ctx(scene.events([]))
    assert gathering.gather(P, ctx, a, ctx.state.config) == ("no_click", None)
    assert scene.hovers == []


def test_gather_without_event_stream_polls_http(scene):
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, False), (True, 5000, False), (False, 5035, False)]
    ctx = make_ctx(None)
    outcome, _ = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "depleted"


# --- tick: chaining -----------------------------------------------------------

def test_tick_chains_onto_the_prehovered_target(scene):
    a, b = _rock(1, 1, dist=1), _rock(2, 2, dist=3)
    scene.objects = [a, b]
    scene.readings = [(True, 5000, True),      # before the click: idle, both rocks up
                      (False, 5035, False),    # a depletes
                      (True, 5035, False),     # b revalidated before its click
                      (False, 5070, False)]    # b depletes; nothing left to mine
    ctx = make_ctx(scene.events([_xp(), _xp(), None]))
    mining.tick(ctx)
    assert scene.clicks == [(1, 1, 11364), (2, 2, 11364)]
    # The chain still runs off the resolved next target; neither rock was ever stood at long
    # enough to pre-hover (both deplete while the walk is still budgeted).
    assert scene.hovers == []


def test_tick_idle_when_nothing_is_selected(scene):
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


# --- a full inventory (2026-09-04) ------------------------------------------------

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
    monkeypatch.setattr(gathering.banker, "find_nearby_bank",
                        lambda cfg: calls.append(("find", cfg)) or {"name": "Bank chest", "distance": 12})
    monkeypatch.setattr(gathering.banker, "open_bank", lambda ctx, bank: calls.append(("open", bank["name"])) or True)

    def _deposit(ctx, cfg):
        calls.append(("deposit", cfg["keepItems"]))
        scene.inventory = [{"name": "Rune pickaxe"}]
    monkeypatch.setattr(gathering.banker, "deposit_inventory", _deposit)
    monkeypatch.setattr(gathering.banker, "close_bank", lambda ctx: calls.append(("close",)))
    monkeypatch.setattr(gathering.travel, "walk_to", lambda ctx, t, **kw: calls.append(("walk", t["x"], t["y"], kw)) or True)
    ctx = make_ctx()
    ctx.state.data["mining_stand_tile"] = {"x": 3010, "y": 3000, "plane": 0}   # 10 tiles from the bank
    mining.tick(ctx)
    assert [c[0] for c in calls] == ["find", "open", "deposit", "close", "walk"]
    assert calls[0][1]["maxBankDistance"] == gathering.BANK_MAX_DISTANCE
    assert calls[2][1] == ["pickaxe"]
    assert calls[4][1:3] == (3010, 3000) and calls[4][3] == {"goal_adjacent": False}
    assert scene.clicks == []                  # no rock click on a banking tick
    assert not ctx.state.safety_stopped


def test_tick_stops_when_full_and_no_bank_is_in_range(scene, monkeypatch):
    scene.inventory = _full_inventory()
    scene.objects = [_rock(1, 1)]
    scene.readings = [(True, 5000, True)]
    monkeypatch.setattr(gathering.banker, "find_nearby_bank", lambda cfg: None)
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


def test_arrival_needs_us_adjacent_not_merely_close(scene, monkeypatch):
    """REGRESSION 2026-09-14. ARRIVAL_TILES was 2, so a vein TWO tiles away already counted as
    arrived at the instant it was clicked -- MEASURED live as "arrived at Ore vein (0.24s after
    the click)" on a dist=2 vein, which is not a walk, it is no walk at all. The pre-hover then
    parked the cursor while the player was still moving and the projection slid out from under
    it, which is exactly what the arrival test exists to prevent. Gathering happens ADJACENT."""
    a = _rock(1, 1, dist=2)
    scene.objects = [a]
    scene.readings = [(True, 5000, True)]
    # Two tiles away and NOT moving yet: the click has been issued but the walk has not begun.
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"miningXp": 5000, "x": 3, "y": 1, "moving": False})
    ctx = make_ctx(scene.events([None] * 40))
    hovered = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda *a, **k: hovered.append(1) or True)
    gathering.gather(P, ctx, a, ctx.state.config)
    # The walk budget (2 tiles) must elapse before anything calls this arrival.
    assert scene.now - 1000.0 >= 2 * gathering.WALK_S_PER_TILE


def test_arrival_at_a_far_target_rebases_the_idle_grace(scene, monkeypatch):
    # 6 tiles away (3.6s walk budget). PLAYER_STOPPED + /player.moving false = arrived, after
    # which an idle player with nothing gained is a failed click IDLE_GRACE_S later -- not
    # walk + grace.
    a = _rock(1, 1, dist=6)
    scene.objects = [a]
    scene.readings = [(True, 5000, True), (True, 5000, True)]
    # The player is WALKING on the first read and stopped from then on. It used to report
    # moving=False on every read while still firing PLAYER_STOPPED -- a player that stops
    # without ever having moved, which the game cannot produce. That mock is what let the
    # premature-arrival bug through (see gather's confirm()): with it, "moving is False" on the
    # very first read looked like arrival, which is exactly what happened live at 0.21s into a
    # 6-tile walk.
    # Several reads, because _outcome() reads the player too and would otherwise eat the only
    # moving=True on its own before confirm() ever sees one.
    # ...and ends up STANDING NEXT TO the rock at (1,1), which is what arrival actually means.
    # It used to report x=3000,y=3000 -- nowhere near the target it had supposedly arrived at.
    reads = {"n": 0}

    def _player():
        reads["n"] += 1
        return {"miningXp": scene.cur()[1], "x": 2, "y": 1, "moving": reads["n"] <= 2}

    monkeypatch.setattr(controller, "get_player", _player)
    # The stop lands a little way in, not on the very first pump: a 6-tile walk cannot already
    # be over one poll after the click, and pretending it is was what made the old premature
    # arrival look correct here.
    ctx = make_ctx(scene.events([None] * 40
                                + [("PLAYER_STOPPED", {"x": 3005, "y": 3000, "plane": 0})]
                                + [None] * 300))
    outcome, _ = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "idle"
    elapsed = scene.now - 1000.0
    # Rebased on ARRIVAL (~0.8s in, where the stop lands): grace + confirm run from there, so
    # ~5.3s. NOT from click + the whole 3.6s walk budget, which would come to 8.1s. The 1.0s of
    # slack is the walk before the stop; the point of the bound is to sit well under 8.1.
    assert gathering.IDLE_GRACE_S <= elapsed < (gathering.IDLE_GRACE_S
                                                + gathering.IDLE_CONFIRM_S + 1.0)


# --- the fullInventory policy (2026-09-07) ----------------------------------------

def test_a_drop_policy_drops_everything_but_the_keep_items(scene, monkeypatch):
    scene.inventory = _full_inventory()
    scene.objects = [_rock(1, 1)]
    scene.readings = [(True, 5000, True)]
    dropped = []
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: dropped.extend(i["name"] for i in items) or True)
    monkeypatch.setattr(gathering.banker, "find_nearby_bank",
                        lambda cfg: pytest.fail("a drop policy must not go banking"))
    ctx = make_ctx(config=dict(mining.DEFAULT_CONFIG, rockNames=["Iron rocks"],
                               fullInventory="drop"))
    # The drop empties the inventory, exactly as the game would.
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [] if dropped else list(scene.inventory))
    mining.tick(ctx)
    assert dropped == ["Iron ore"] * 27        # the Rune pickaxe stays
    assert scene.clicks == []                  # the tick was spent on the inventory
    assert not ctx.state.safety_stopped


def test_a_drop_that_frees_nothing_stops_the_run(scene, monkeypatch):
    scene.inventory = _full_inventory()
    scene.objects = [_rock(1, 1)]
    scene.readings = [(True, 5000, True)]
    tried = []
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: tried.append(len(items)) or False)
    monkeypatch.setattr(gathering.banker, "find_nearby_bank",
                        lambda cfg: pytest.fail("a drop policy must not go banking"))
    ctx = make_ctx(config=dict(mining.DEFAULT_CONFIG, rockNames=["Iron rocks"],
                               fullInventory="drop"))
    mining.tick(ctx)
    assert tried == [27]                       # it did try, and the inventory is still full
    assert ctx.state.safety_stopped


def test_a_stop_policy_stops_without_looking_for_a_bank(scene, monkeypatch):
    scene.inventory = _full_inventory()
    scene.objects = [_rock(1, 1)]
    scene.readings = [(True, 5000, True)]
    monkeypatch.setattr(gathering.banker, "find_nearby_bank",
                        lambda cfg: pytest.fail("a stop policy must not go banking"))
    ctx = make_ctx(config=dict(mining.DEFAULT_CONFIG, rockNames=["Iron rocks"],
                               fullInventory="stop"))
    mining.tick(ctx)
    assert ctx.state.safety_stopped


def test_the_policy_defaults_to_the_profiles_own_when_the_config_omits_it(scene, monkeypatch):
    # Mining's profile says "bank", and a config with no fullInventory key must still bank.
    scene.inventory = _full_inventory()
    scene.objects = [_rock(1, 1)]
    scene.readings = [(True, 5000, True)]
    looked = []
    monkeypatch.setattr(gathering.banker, "find_nearby_bank",
                        lambda cfg: looked.append(cfg) or None)
    cfg = dict(mining.DEFAULT_CONFIG, rockNames=["Iron rocks"])
    cfg.pop("fullInventory")
    ctx = make_ctx(config=cfg)
    mining.tick(ctx)
    assert looked and ctx.state.safety_stopped


def test_an_animation_trough_is_not_a_failed_click(scene, monkeypatch):
    """MEASURED live 2026-09-07 in the Motherlode Mine: over 45s of uninterrupted, XP-earning
    mining, /combat.isIdle read true in 12 separate troughs -- median 1.2s, p90 1.28s, MAX
    2.45s -- so IDLE_GRACE_S (1.5s) sits INSIDE the normal distribution of a working action.
    Before IDLE_CONFIRM_S every spent vein was reported as "the click did not take", which then
    fed motherlode_mine's failure blacklist and set good veins aside.

    Driven off the clock rather than the reading list, because the trough is a DURATION."""
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, False)]
    start = scene.now
    # Idle for the 2.45s measured worst case, then swinging again; the rock goes at 6s.
    monkeypatch.setattr(controller, "get_combat",
                        lambda: {"isIdle": start + 1.0 <= scene.now < start + 3.45})
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, plane, obj_id, radius=None:
                        None if scene.now >= start + 6.0 else dict(a))
    ctx = make_ctx(scene.events([None] * 400))
    outcome, _ = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "depleted"


def test_sustained_idle_is_still_a_failed_click(scene):
    a = _rock(1, 1, dist=1)
    scene.objects = [a]
    scene.readings = [(True, 5000, True)]
    ctx = make_ctx(scene.events([None] * 200))
    outcome, _ = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "idle"
    # ...and it costs the grace plus the confirmation window, no more.
    elapsed = scene.now - 1000.0
    assert gathering.IDLE_CONFIRM_S <= elapsed < gathering.GATHER_BUDGET_S


def test_the_prehover_waits_for_stillness(scene):
    """USER 2026-09-07: "the hover function isnt doing anything if we hover before movement is
    finished".

    The click is issued from a distance on purpose -- the game queues the interaction and
    performs it on arrival -- so hovering the moment the click goes out parks the cursor while
    the player is still walking, and the projection then slides out from under it. MEASURED in
    that run: 0.58s to 2.22s of walking after each click, against a hover that has to still be
    valid ~40s later.

    Here the rock takes an XP drop while still standing, which proves the player has arrived:
    the pre-hover fires then, and not before.
    """
    a, b = _rock(1, 1, dist=4), _rock(2, 2, dist=3)
    scene.objects = [a, b]
    scene.readings = [(True, 5000, False),     # walking to a
                      (True, 5035, False),     # first ore: we are standing at it now
                      (False, 5070, False)]    # a depletes
    ctx = make_ctx(scene.events([_xp(), _xp()]))
    outcome, nxt = gathering.gather(P, ctx, a, ctx.state.config)
    assert outcome == "depleted"
    assert scene.hovers == [(2, 2)]            # exactly once, and only once standing still
    assert nxt["x"] == 2


def test_the_prehover_uses_the_callers_chooser(scene):
    """motherlode_mine picks its next vein through its own floor/anchor/blacklist filters and
    discards this function's, so without pick_next the cursor parked on a DIFFERENT object --
    MEASURED live, and 19 of the 66 veins in the default pool were on the floor below."""
    a, b, c = _rock(1, 1, dist=4), _rock(2, 2, dist=3), _rock(3, 3, dist=5)
    scene.objects = [a, b, c]
    scene.readings = [(True, 5000, False), (True, 5035, False), (False, 5070, False)]
    ctx = make_ctx(scene.events([_xp(), _xp()]))
    outcome, nxt = gathering.gather(P, ctx, a, ctx.state.config,
                                    pick_next=lambda excl: c)
    assert outcome == "depleted"
    assert scene.hovers == [(3, 3)]            # the caller's pick, not the nearest
    assert nxt["x"] == 3
