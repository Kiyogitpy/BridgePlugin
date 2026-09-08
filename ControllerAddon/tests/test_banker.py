import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import banker
import controller


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Hermetic by default (same posture as test_blast_furnace / test_mahogany_homes).

    ADDED 2026-09-07: banker's _owned_item_ids reads bank_memory.load(), which reaches the
    RUNNING CLIENT -- ~929 real items that change as the account is played, so planning
    outcomes drifted run to run and three unrelated tests failed once and passed on a verbatim
    re-run. A suite whose result depends on where the player is standing is not a suite.
    Tests that care re-patch these themselves.
    """
    import bank_memory
    monkeypatch.setattr(bank_memory, "load",
                        lambda names=None: bank_memory.BankMemory({"seen": False, "items": None}))
    monkeypatch.setattr(controller, "get_varbit", lambda vid: None)
    monkeypatch.setattr(controller, "get_equipment", lambda: {})


class _Ctx:
    def __init__(self, ser=None):
        self.ser = ser


def test_switch_bank_tab_skips_click_when_already_on_tab(monkeypatch):
    monkeypatch.setattr(controller, "get_bank",
                        lambda fresh=False: {"currentTab": 3, "tabs": []})
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1))
    assert banker.switch_bank_tab(_Ctx(), 3)
    assert clicked == []


def test_switch_bank_tab_returns_false_when_tab_button_not_found(monkeypatch):
    monkeypatch.setattr(controller, "get_bank",
                        lambda fresh=False: {"currentTab": 0, "tabs": []})
    assert not banker.switch_bank_tab(_Ctx(), 8)


def test_switch_bank_tab_retries_click_until_current_tab_confirms_the_switch(monkeypatch):
    """CONFIRMED bug this fixes: switch_bank_tab used to click once and return True
    unconditionally, with no confirmation the click actually landed. Here currentTab only
    flips to the target tab after the SECOND click -- a single-attempt caller would have
    wrongly reported success after the first (dropped/missed) click."""
    state = {"currentTab": 0, "calls": 0}

    def fake_get_bank(fresh=False):
        return {
            "currentTab": state["currentTab"],
            "tabs": [{"index": 8, "screenX": 500, "screenY": 500}],
        }

    def fake_click_entity(ser, entity, **kw):
        state["calls"] += 1
        if state["calls"] >= 2:
            state["currentTab"] = 8

    monkeypatch.setattr(controller, "get_bank", fake_get_bank)
    monkeypatch.setattr(controller, "click_entity", fake_click_entity)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)

    assert banker.switch_bank_tab(_Ctx(), 8)
    assert state["calls"] == 2


def test_switch_bank_tab_gives_up_after_repeated_misses(monkeypatch):
    """A genuine, persistent failure (not just one dropped click) must still surface as False
    -- the fix is retrying a bounded number of times, not trusting blindly forever."""
    monkeypatch.setattr(controller, "get_bank",
                        lambda fresh=False: {"currentTab": 0,
                                              "tabs": [{"index": 8, "screenX": 500, "screenY": 500}]})
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicks.append(1))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)

    assert not banker.switch_bank_tab(_Ctx(), 8)
    assert len(clicks) == 3


# --- open_bank approaches a distant bank instead of clicking at it -------------------
#
# CONFIRMED live 2026-08-28 (mahogany_homes run 4, safety-stopped on "failed to open the bank"):
# find_nearby_bank accepts a booth up to maxBankDistance (40) away, and open_bank then clicked
# it from wherever it stood. Standing inside Mariah's house in Hosidius, the booth 21 tiles away
# through a wall still reported onScreen -- projection has no notion of occlusion -- and its
# projected point landed on the chatbox tab strip at the bottom of the viewport, so all five
# clicks resolved to "Switch tab" on widget 162.19 instead of the bank. Two things made it a
# dead loop rather than a slow success: _refresh_bank_object only re-resolves within 15 tiles,
# so every retry re-aimed at the identical stale pixel; and nothing ever moved the player.

def _bank(distance, on_screen=True, x=1747, y=3598, plane=0):
    return {"id": 25808, "name": "Bank booth", "distance": distance, "onScreen": on_screen,
            "visible": True, "x": x, "y": y, "plane": plane, "screenX": 549, "screenY": 1358}


def _quiet(monkeypatch):
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda **kw: True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])


def test_open_bank_walks_toward_a_bank_that_is_too_far_to_click(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: {"onMinimap": True, "screenX": 70, "screenY": 80})
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)

    banker.open_bank(_Ctx(), _bank(distance=21), timeout_s=0.5)

    assert clicks, "should have done something"
    assert all(c.get("onMinimap") for c in clicks), \
        f"a bank 21 tiles away must be walked to, not clicked at: {clicks}"


def test_open_bank_clicks_a_bank_it_is_already_standing_at(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    walked = []
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: walked.append((x, y)) or {"onMinimap": True})
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)

    banker.open_bank(_Ctx(), _bank(distance=2), timeout_s=0.5)

    assert walked == [], "a bank 2 tiles away needs no approach walk"
    assert clicks and clicks[0]["id"] == 25808


def test_open_bank_still_clicks_a_far_bank_the_minimap_cannot_reach(monkeypatch):
    # The minimap only reaches ~28 tiles, and a bank beyond it must not deadlock the loop --
    # fall back to the old behaviour (click it and let the game walk there).
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: {"onMinimap": False})
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)

    banker.open_bank(_Ctx(), _bank(distance=35), timeout_s=0.5)

    assert clicks and clicks[0]["id"] == 25808


def test_open_bank_does_not_click_an_offscreen_bank_the_minimap_cannot_reach(monkeypatch):
    # Unchanged pre-existing behaviour: an off-screen aim point is outside the RuneLite window,
    # so clicking it is refused anyway -- spending the timeout is better than a guaranteed miss.
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, plane: {"onMinimap": False})
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)

    banker.open_bank(_Ctx(), _bank(distance=35, on_screen=False), timeout_s=0.5)

    assert clicks == []


# --- open_bank's click tracks the booth through the flight, and its resolve window
# --- budgets the walk ------------------------------------------------------------------
#
# USER 2026-08-30, watching a live bank trip (mh_live29 02:51:11): "very stale". The log shows
# the whole shape: booth clicked at (1122,591), the player then walks toward it, the 3.0s
# resolve window expires MID-WALK, the loop waits for idle and clicks the booth again at
# (1265,758) -- 140px from the first aim -- ~9 seconds for what should be one click. Two
# defects: the click flies with no refresh_target, so its aim is whatever the pre-flight
# snapshot said (the same trap as every other stale click this repo documents); and the flat
# 3.0s window cannot cover the walk the game performs before opening the bank, so the retry
# races -- and can interrupt -- a click that was about to work ("whenever you time a click, ask
# whether the player has to walk there first").

def test_open_bank_click_carries_a_refresh_target(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(banker, "is_open", lambda: False)
    kwargs_seen = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: kwargs_seen.append(kw) or True)

    banker.open_bank(_Ctx(), _bank(distance=4), timeout_s=0.5)

    assert kwargs_seen and kwargs_seen[0].get("refresh_target") is not None, \
        "the booth click must re-aim mid-flight -- a snapshot aim goes stale while walking"


def test_open_bank_resolve_budget_scales_with_the_walk():
    near = banker._click_resolve_budget(1)
    far = banker._click_resolve_budget(8)
    assert far > near, "a click from 8 tiles away must get more time than one from 1 tile"
    assert far >= 3.0 + 8 * 0.5, f"8 tiles of walking cannot resolve inside {far}s"
    assert banker._click_resolve_budget(40) <= 12.0, "a genuinely dead click must still fail fast"


# --- the first booth click happens while still running in ------------------------------
#
# USER 2026-08-30 (mh_live37 04:08:57-04:09:13, watching live): the walk to the bank ran its
# last ~10 tiles as THREE separate short viewport shift-clicks with flights between them, then
# an idle wait, then the booth click -- "weird clicks around the bank instead of just updating
# the bank booth while running there and clicking it". The human clicks the booth from a hop
# away and lets the game finish the approach. The wait-for-idle-before-EVERY-click rule dates
# to 2026-08-12 Mixology, when a click had no way to keep its aim true while moving;
# refresh_target does exactly that now, so the FIRST click goes out mid-run and only RETRIES
# (which mean something already went wrong) still wait for stillness.

def test_open_bank_first_click_does_not_wait_for_idle(monkeypatch):
    idles = []
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda **kw: idles.append(1) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])
    monkeypatch.setattr(banker, "is_open", lambda: False)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(len(idles)) or True)

    banker.open_bank(_Ctx(), _bank(distance=6), timeout_s=0.5)

    assert clicks, "should have clicked the booth"
    assert clicks[0] == 0, \
        f"the FIRST booth click must fly while still running in -- {clicks[0]} idle waits first"


def test_open_bank_retries_still_wait_for_stillness(monkeypatch):
    idles = []
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda **kw: idles.append(1) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])
    monkeypatch.setattr(banker, "is_open", lambda: False)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(len(idles)) or True)

    banker.open_bank(_Ctx(), _bank(distance=1), timeout_s=8.0)

    assert len(clicks) >= 2, "the harness never opens the bank, so a retry must happen"
    assert clicks[1] >= 1, "a RETRY means the first click failed -- stillness first, as before"


def test_bank_ui_buttons_never_simulate_a_misclick(monkeypatch):
    # A toggle clicked twice (misclick + corrective reclick) ends up where it started --
    # the search box opened and closed and "adam" was typed into nothing (gf_live30).
    seen = []
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {
        "open": True, "searchButton": {"screenX": 1123, "screenY": 1006},
        "quantityButtons": {"one": {"screenX": 719, "screenY": 1006}}})
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: seen.append(k.get("simulate_mistakes")) or True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "type_text", lambda ser, text: None)

    class _Ctx:
        ser = None
    banker.search_bank(_Ctx(), "adamantite")
    banker.clear_bank_search(_Ctx())
    banker.set_quantity_mode(_Ctx(), "one")
    assert seen == [False, False, False]



# MEASURED live 2026-09-05 at the Grand Exchange: two booth ids share the name "Grand Exchange
# booth"; 10061 offers Exchange/Collect (the clerk), 10060 offers Bank/Collect. Name-only
# matching picked the nearer Exchange booth and open_bank clicked "Exchange" forever.
def test_find_nearby_bank_skips_exchange_booth(monkeypatch):
    objs = [
        {"name": "Grand Exchange booth", "id": 10061, "distance": 2, "visible": True,
         "actions": ["Exchange", "Collect"]},
        {"name": "Grand Exchange booth", "id": 10060, "distance": 5, "visible": True,
         "actions": ["Bank", "Collect"]},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: objs)
    assert banker.find_nearby_bank({})["id"] == 10060


def test_find_nearby_bank_keeps_chest_and_actionless(monkeypatch):
    objs = [
        {"name": "Bank chest", "distance": 3, "visible": True, "actions": ["Use"]},
        {"name": "Bank booth", "distance": 1, "visible": True},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: objs)
    assert banker.find_nearby_bank({})["name"] == "Bank booth"
    objs.pop()
    assert banker.find_nearby_bank({})["name"] == "Bank chest"


# {"all": True} withdraw entries (2026-09-05, firemaking): one shift-click instead of a click
# per unit, and no quantity-mode click when every entry is shift-withdrawn.
def test_withdraw_loadout_all_entry_shift_withdraws_once(monkeypatch):
    calls = []
    monkeypatch.setattr(banker, "withdraw_all_shift", lambda ctx, name, fresh=False: calls.append(name) or True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx, mode: pytest.fail("quantity mode not expected"))
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: pytest.fail("per-unit click not expected"))
    banker.withdraw_loadout(None, {"withdraw": [{"name": "Willow logs", "quantity": 27, "all": True}]})
    assert calls == ["Willow logs"]


# --- bank trip return-to-spot -------------------------------------------------------------
#
# USER-REPORTED 2026-09-07: slayer "ends up stuck at Ferox" and never gets back to the spot it
# was fighting at. Three separate defects fed that, all traced statically before any fix:
#   1. bank_up's nearby-bank branch never recorded or restored a location at all -- the only
#      save/restore machinery in the file lived inside _bank_via_ferox.
#   2. FEROX_BANK_NAMES is a strict subset of DEFAULT_BANK_NAMES and the Java panel exports
#      neither bankNames nor maxBankDistance, so once a trip aborted AFTER the teleport landed
#      (no bank found yet, or open_bank failed), the next tick's bank_up matched the Ferox chest
#      on its FIRST branch -- skipping use_fountain and the walk home, and orphaning the saved
#      location forever.
#   3. the saved location was popped BEFORE the walk was attempted, so a walk that gave up
#      partway (WALK_MAX_STALLS is a cumulative budget of 4 across a whole cross-map route)
#      discarded the destination with no possibility of a retry.


class _State:
    def __init__(self, config=None):
        self.config = config or {}
        self.data = {}
        self.stops = []

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)


class _TripCtx:
    def __init__(self, config=None):
        self.ser = None
        self.state = _State(config)


def _stub_trip(monkeypatch, *, walk_results=None, teleport_ok=True):
    """Stub out every moving part of a bank trip, recording what it was asked to do."""
    import travel

    calls = {"walks": [], "teleports": 0, "fountains": 0, "deposits": 0, "opened": []}
    walk_results = list(walk_results or [])

    def fake_walk_to(ctx, target, **kw):
        calls["walks"].append(dict(target))
        return walk_results.pop(0) if walk_results else True

    def fake_teleport(ctx):
        calls["teleports"] += 1
        return teleport_ok

    monkeypatch.setattr(travel, "save_location", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", fake_walk_to)
    monkeypatch.setattr(travel, "teleport_to_ferox", fake_teleport)
    monkeypatch.setattr(travel, "use_fountain", lambda ctx: calls.__setitem__("fountains", calls["fountains"] + 1))
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj, **kw: calls["opened"].append(obj) or True)
    monkeypatch.setattr(banker, "deposit_inventory",
                        lambda ctx, cfg: calls.__setitem__("deposits", calls["deposits"] + 1))
    monkeypatch.setattr(banker, "withdraw_loadout", lambda ctx, cfg, **kw: None)
    monkeypatch.setattr(banker, "close_bank", lambda ctx: None)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3300, "y": 3300, "plane": 0})
    return calls


def test_bank_up_walks_back_to_the_tile_it_left_from_after_a_nearby_bank_trip(monkeypatch):
    """Defect 1: a bank inside maxBankDistance (40) was walked to and never walked back from,
    so the routine simply carried on fighting next to the bank."""
    calls = _stub_trip(monkeypatch)
    monkeypatch.setattr(banker, "_find_nearby_bank_retrying",
                        lambda cfg, **kw: {"name": "Bank booth", "distance": 22})
    # This test is about the WALK home, so no teleport is planned -- otherwise the graph
    # (correctly) finds a chain and there is no walk left to assert on.
    monkeypatch.setattr(banker, "_withdraw_return_teleport", lambda ctx, cfg: None)

    ctx = _TripCtx()
    assert banker.bank_up(ctx, {"enabled": True, "returnToSpot": True})
    assert calls["walks"] == [{"x": 3200, "y": 3200, "plane": 0}]


def test_bank_up_keeps_the_return_tile_when_the_walk_back_gives_up_partway(monkeypatch):
    """Defect 3: the tile was popped before the walk, so one failed walk lost it forever."""
    calls = _stub_trip(monkeypatch, walk_results=[False])
    monkeypatch.setattr(banker, "_find_nearby_bank_retrying",
                        lambda cfg, **kw: {"name": "Bank booth", "distance": 22})

    ctx = _TripCtx()
    banker.bank_up(ctx, {"enabled": True, "returnToSpot": True})
    assert ctx.state.data.get(banker.RETURN_LOCATION_KEY) == {"x": 3200, "y": 3200, "plane": 0}

    # ...and a later tick retries it, rather than the routine fighting on from wherever it stopped.
    assert banker.return_to_spot(ctx)
    assert calls["walks"][-1] == {"x": 3200, "y": 3200, "plane": 0}
    assert banker.RETURN_LOCATION_KEY not in ctx.state.data


def test_return_to_spot_gives_up_loudly_rather_than_retrying_the_walk_home_forever(monkeypatch):
    """Every loop needs a ceiling (CLAUDE.md). Being stranded is the reported bug, so the
    ceiling surfaces as a safety stop, not a silent shrug."""
    _stub_trip(monkeypatch, walk_results=[False] * 20)
    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3200, "y": 3200, "plane": 0}

    for _ in range(banker.RETURN_WALK_MAX_ATTEMPTS):
        assert not banker.return_to_spot(ctx)
    assert ctx.state.data.get(banker.RETURN_LOCATION_KEY) is None
    assert ctx.state.stops


def test_bank_up_stays_on_the_ferox_path_after_a_teleport_has_already_landed(monkeypatch):
    """Defect 2: the Ferox chest matches DEFAULT_BANK_NAMES, so a trip that aborted after the
    teleport landed got hijacked by bank_up's own nearby-bank branch on the next tick --
    banking without ever using the fountain or walking home."""
    calls = _stub_trip(monkeypatch)

    # First tick: nothing in range, teleport lands, but the chest isn't visible yet -> abort.
    monkeypatch.setattr(banker, "_find_nearby_bank_retrying", lambda cfg, **kw: None)
    ctx = _TripCtx()
    assert not banker.bank_up(ctx, {"enabled": True})
    assert calls["teleports"] == 1

    # Second tick: the chest is visible now, and it matches BOTH name lists.
    monkeypatch.setattr(banker, "_find_nearby_bank_retrying",
                        lambda cfg, **kw: {"name": "Bank chest", "distance": 4})
    assert banker.bank_up(ctx, {"enabled": True})

    assert calls["teleports"] == 1, "must not burn a second ring charge -- already at Ferox"
    assert calls["fountains"] == 1, "the Ferox path's fountain step was skipped by the hijack"
    assert calls["walks"][-1] == {"x": 3200, "y": 3200, "plane": 0}


def test_bank_up_does_not_add_a_walk_home_for_callers_that_never_had_one(monkeypatch):
    """routines/firemaking.py banks every inventory and then walks to a fixed starter tile of
    its own (advance_starter), so a walk back to the end of the last burn line would be pure
    waste. The nearby-bank walk home is therefore opt-in, and the default leaves every
    pre-2026-09-07 caller's trip exactly as it was."""
    calls = _stub_trip(monkeypatch)
    monkeypatch.setattr(banker, "_find_nearby_bank_retrying",
                        lambda cfg, **kw: {"name": "Bank booth", "distance": 22})

    ctx = _TripCtx()
    assert banker.bank_up(ctx, {"enabled": True})
    assert calls["walks"] == []
    assert banker.RETURN_LOCATION_KEY not in ctx.state.data


def test_the_ferox_path_still_walks_home_without_anyone_opting_in(monkeypatch):
    """The teleport path always walked back, and golem_crafting's own comment depends on it
    ("it walks the player straight back afterward"). Opting in must not have become a
    precondition for that."""
    calls = _stub_trip(monkeypatch)
    seen = {"n": 0}

    def fake_find(cfg, **kw):
        # 1: bank_up's own nearby check. 2: _bank_via_ferox's "are we already there" check.
        # 3: the post-teleport look for the chest.
        seen["n"] += 1
        return None if seen["n"] <= 2 else {"name": "Bank chest", "distance": 4}

    monkeypatch.setattr(banker, "_find_nearby_bank_retrying", fake_find)

    ctx = _TripCtx()
    assert banker.bank_up(ctx, {"enabled": True})
    assert calls["teleports"] == 1
    assert calls["walks"] == [{"x": 3200, "y": 3200, "plane": 0}]


# --- teleporting home from a bank trip ----------------------------------------------------
#
# USER 2026-09-07: "make sure slayer is using this to decide what method of teleportation is
# best to get back". The choice is made INSIDE the bank, because that is the only moment the
# winning item can be withdrawn; return_to_spot then uses it and walks whatever is left.


def _stub_teleport_planning(monkeypatch, *, bank_items=(), inventory=(), route_lens=None,
                            walk_len=400):
    """Everything _plan_return_teleport reads, so the choice is exercised and nothing else is."""
    import bank_memory
    import worldmap

    monkeypatch.setattr(bank_memory, "load",
                        lambda names=None: bank_memory.BankMemory(
                            {"seen": True, "items": list(bank_items), "itemCount": len(bank_items)}))
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory))
    monkeypatch.setattr(controller, "get_equipment", lambda: [])
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3300, "y": 3300, "plane": 0, "wildernessLevel": 0})

    route_lens = route_lens or {}

    def fake_find_path(sx, sy, plane, gx, gy, **kw):
        # The walk-the-whole-way case is keyed by the start tile; a teleport's remaining walk by
        # its landing tile.
        length = walk_len if (sx, sy) == (3300, 3300) else route_lens.get((sx, sy))
        return [None] * length if length is not None else None

    monkeypatch.setattr(worldmap, "find_path", fake_find_path)

    # The planner asks ONE flood from the goal for landing-spot distances (see
    # GOAL_FLOOD_RADIUS) and uses find_path only for approach-to-origin questions, so the two
    # stubs must agree or the test is measuring the real collision map.
    def fake_reachable_from(gx, gy, plane, **kw):
        reach = {(x, y): d for (x, y), d in route_lens.items()}
        if walk_len is not None:
            reach[(3300, 3300)] = walk_len
        return reach

    monkeypatch.setattr(worldmap, "reachable_from", fake_reachable_from)


def test_plan_return_teleport_picks_the_one_landing_nearest_the_anchor(monkeypatch):
    _stub_teleport_planning(
        monkeypatch,
        bank_items=[{"id": 2552, "name": "Ring of dueling(8)", "quantity": 1}],
        route_lens={(3151, 3635): 30},
        walk_len=400)

    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3151, "y": 3640, "plane": 0}

    legs = banker._plan_return_teleport(ctx, {"returnToSpot": True})
    assert legs, "expected a route home"
    # One hop is enough here, and chaining must not make the simple case worse.
    assert len(legs) == 1
    assert legs[0].display == "Ring of dueling: Ferox Enclave"


def test_plan_return_teleport_declines_when_walking_is_already_close(monkeypatch):
    """A tablet is worth more than a short walk -- no plan means the existing walk home runs
    exactly as it does today."""
    _stub_teleport_planning(
        monkeypatch,
        bank_items=[{"id": 2552, "name": "Ring of dueling(8)", "quantity": 1}],
        route_lens={(3151, 3635): 30},
        walk_len=45)

    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3151, "y": 3640, "plane": 0}
    assert banker._plan_return_teleport(ctx, {"returnToSpot": True}) is None


def test_plan_return_teleport_needs_an_anchor_and_an_opted_in_caller(monkeypatch):
    _stub_teleport_planning(monkeypatch,
                            bank_items=[{"id": 2552, "name": "Ring of dueling(8)", "quantity": 1}],
                            route_lens={(3151, 3635): 30})

    # No anchor recorded -> nothing to aim at.
    assert banker._plan_return_teleport(_TripCtx(), {"returnToSpot": True}) is None

    # Opted out (firemaking, blast_furnace) -> must not start withdrawing teleports.
    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3151, "y": 3640, "plane": 0}
    assert banker._plan_return_teleport(ctx, {}) is None


def test_plan_return_teleport_ignores_an_item_we_do_not_own(monkeypatch):
    _stub_teleport_planning(monkeypatch, bank_items=[], inventory=[],
                            route_lens={(3151, 3635): 30}, walk_len=400)
    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3151, "y": 3640, "plane": 0}
    assert not banker._plan_return_teleport(ctx, {"returnToSpot": True})


def test_a_teleport_that_does_not_fire_falls_back_to_walking(monkeypatch):
    """The whole safety argument for executing teleports we have never driven live: a failed
    teleport costs latency, never the trip. The anchor survives, so the walk still happens."""
    calls = _stub_trip(monkeypatch)
    monkeypatch.setattr(banker, "_use_return_teleport", lambda ctx: False)

    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3200, "y": 3200, "plane": 0}
    ctx.state.data[banker.RETURN_TELEPORT_KEY] = {
        "legs": [{"kind": "item", "item": "Ring of dueling", "option": "Ferox Enclave",
                  "display": "Ring of dueling: Ferox Enclave", "dest": [3151, 3635, 0]}]}

    assert banker.return_to_spot(ctx)
    assert calls["walks"] == [{"x": 3200, "y": 3200, "plane": 0}]


def test_return_to_spot_actually_reaches_the_teleport_it_planned(monkeypatch):
    """Regression: return_to_spot used to POP the plan before calling _use_return_teleport,
    which reads that same key -- so the teleport silently never happened. The fallback test
    above could not catch it, because it stubs the very function the bug was in."""
    calls = _stub_trip(monkeypatch)
    used = []
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"id": 2552, "name": "Ring of dueling(8)",
                                  "screenX": 10, "screenY": 10}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: None)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, item, **kw: used.append(kw.get("expected_action")) or True)
    # Landed on the first poll.
    monkeypatch.setattr(banker.time, "sleep", lambda s: None)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3151, "y": 3635, "plane": 0})

    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3151, "y": 3640, "plane": 0}
    ctx.state.data[banker.RETURN_TELEPORT_KEY] = {
        "legs": [{"kind": "item", "item": "Ring of dueling", "option": "Ferox Enclave",
                  "display": "Ring of dueling: Ferox Enclave", "dest": [3151, 3635, 0]}]}

    assert banker.return_to_spot(ctx)
    assert used == ["Ferox Enclave"], "the planned teleport was never clicked"
    # Consumed exactly once, so a later tick cannot re-fire it.
    assert banker.RETURN_TELEPORT_KEY not in ctx.state.data


# --- multi-leg teleport chains ------------------------------------------------------------
#
# USER 2026-09-07: "go ahead to 1+2+3" -- wire the transport graph into banker, execute a
# non-item leg, and read the POH config. A chain's first leg is normally an inventory item
# (withdrawn while the bank is open); later legs are objects standing in the POH.


def _leg(kind="item", **kw):
    d = {"kind": kind, "display": "Teleport to House tablet", "item": "Teleport to House",
         "option": None, "dest": [1923, 5709, 0], "origin": None, "object_id": None,
         "object_info": ""}
    d.update(kw)
    return d


def test_the_plan_stores_every_leg_not_just_the_first(monkeypatch):
    """The single-hop version stored one item name. A chain has to survive into
    return_to_spot intact or the second hop simply never happens."""
    import teleports
    calls = _stub_trip(monkeypatch)
    monkeypatch.setattr(controller, "get_varbit", lambda i: 0)
    monkeypatch.setattr(banker, "_owned_item_ids", lambda cfg: {8013})
    monkeypatch.setattr(banker, "_route_length", lambda *a: None)

    chain = [
        teleports.Transport("item", None, (1923, 5709, 0), frozenset({8013}), (), "",
                            "Teleport to House tablet", "Teleport to House tablet", None, 4, 20, True),
        teleports.Transport("box", (1870, 5698, 0), (3151, 3635, 0), frozenset(), (),
                            "Teleport Menu Ornate Jewellery Box 37520", "3: Ferox Enclave",
                            "3", "Ferox Enclave", 4, None, False),
    ]
    monkeypatch.setattr(teleports, "plan", lambda *a, **k: chain)
    monkeypatch.setattr(banker, "search_and_withdraw", lambda ctx, name, qty: True)

    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3151, "y": 3635, "plane": 0}
    banker._withdraw_return_teleport(ctx, {"returnToSpot": True, "poh": {"enabled": True,
                                                                        "boxTier": "ORNATE"}})

    stored = ctx.state.data.get(banker.RETURN_TELEPORT_KEY)
    assert stored and len(stored["legs"]) == 2
    assert stored["legs"][0]["kind"] == "item"
    assert stored["legs"][1]["kind"] == "box"
    assert stored["legs"][1]["object_id"] == 37520, "the object id must survive for the click"
    assert stored["legs"][1]["option"] == "Ferox Enclave"


def test_poh_config_comes_off_the_bridge_config_not_a_hardcoded_default():
    """The client can't see inside a house, so this is config. Reading it wrong means the graph
    silently never plans a POH route."""
    poh = banker._poh_config({"poh": {"enabled": True, "boxTier": "ORNATE", "portals": True,
                                      "mountedItems": True, "fairyRing": True}})
    assert poh.enabled and poh.box_tier == "ORNATE" and poh.portals and poh.mounted_items

    # Absent block = everything off, so an unconfigured account never routes through a house
    # it may not have.
    off = banker._poh_config({})
    assert not off.enabled and off.box_tier == "NONE"


def test_a_failed_leg_aborts_the_chain_and_leaves_the_walk_to_recover(monkeypatch):
    """Same safety argument as the single-hop version, extended: a chain that dies halfway
    must not keep clicking. The anchor survives, so return_to_spot still walks."""
    calls = _stub_trip(monkeypatch)
    used = []
    monkeypatch.setattr(banker, "_run_leg", lambda ctx, leg: used.append(leg["kind"]) or False)

    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3151, "y": 3635, "plane": 0}
    ctx.state.data[banker.RETURN_TELEPORT_KEY] = {"legs": [_leg(), _leg(kind="box")]}

    assert banker.return_to_spot(ctx)
    assert used == ["item"], "the second leg must not run after the first failed"
    assert calls["walks"] == [{"x": 3151, "y": 3635, "plane": 0}]


def test_every_leg_runs_in_order_when_they_succeed(monkeypatch):
    calls = _stub_trip(monkeypatch)
    used = []
    monkeypatch.setattr(banker, "_run_leg", lambda ctx, leg: used.append(leg["kind"]) or True)

    ctx = _TripCtx()
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3151, "y": 3635, "plane": 0}
    ctx.state.data[banker.RETURN_TELEPORT_KEY] = {"legs": [_leg(), _leg(kind="box")]}

    banker.return_to_spot(ctx)
    assert used == ["item", "box"]


def test_only_the_varbits_the_candidates_actually_reference_are_read(monkeypatch):
    """A /varbit read per candidate row would be ~300 HTTP calls. Only the ids that appear in a
    requirement are worth asking about."""
    asked = []
    # get_varbit returns the VALUE, not an envelope. A dict-returning stub is what let the
    # real "reads nothing" bug through on 2026-09-07 -- a stub that lies about a shape is
    # worse than no stub.
    monkeypatch.setattr(controller, "get_varbit",
                        lambda i: asked.append(i) or (3 if i == 2187 else 0))
    # Narrowed to rows whose items we hold: a house tablet, so 2187/4744 and little else.
    # Unnarrowed the whole table references 86 distinct varbits (~1.7s of round trips).
    values = banker._referenced_varbits(owned_ids={8013})
    assert 2187 in asked and 4744 in asked
    assert len(asked) < 20, f"read {len(asked)} varbits -- should only be the ones we could use"
    assert values[2187] == 3

    assert len(banker._referenced_varbits()) > len(values),         "unnarrowed must read strictly more, or the narrowing is a no-op"


# --- the log basket (2026-09-08) ---------------------------------------------------------
#
# USER: "need to add a option to empty wood basket too since we use that for cutting wood
# basicly doubling our capacity", then "it fills automaticly all we need to do is right click
# it in bank". So there is no Fill step to drive -- only the Empty at the bank.


def _inv(*names):
    return [{"name": n, "screenX": 10, "screenY": 20} for n in names]


def test_has_log_basket_matches_the_open_basket_too(monkeypatch):
    # MEASURED from the cache (config/obj): the item is named "Log basket" (28140) while shut
    # and "Open log basket" (28142) once opened, and only the OPEN one auto-collects logs --
    # so the shut name alone would miss the state this feature exists for.
    monkeypatch.setattr(controller, "get_inventory", lambda: _inv("Open log basket"))
    assert banker.has_log_basket()
    monkeypatch.setattr(controller, "get_inventory", lambda: _inv("Log basket"))
    assert banker.has_log_basket()
    monkeypatch.setattr(controller, "get_inventory", lambda: _inv("Yew logs", "Rune axe"))
    assert not banker.has_log_basket()


def test_empty_log_basket_selects_the_option_from_the_menu(monkeypatch):
    # Neither log_basket_closed (28140) nor log_basket_open (28142) has an "Empty" iop at all
    # -- Fill/Wear/Check/Open|Close/Destroy -- so the Empty is a BANK-CONTEXT option the bank
    # interface substitutes in, exactly like the fish barrel's Empty-to-bank. A bare left click
    # would resolve to whichever of Fill/Deposit-1/Wear won, which is why this is a named menu
    # selection (empty_fur_pouch's own live-confirmed lesson).
    monkeypatch.setattr(controller, "get_inventory", lambda: _inv("Open log basket"))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    calls = []

    def fake_menu(ser, entity, option, **kw):
        calls.append((entity["name"], option, kw))
        return True

    monkeypatch.setattr(controller, "click_menu_action", fake_menu)
    assert banker.empty_log_basket(_Ctx())

    name, option, kw = calls[0]
    assert name == "Open log basket"
    # A sequence, tried in priority order against ONE menu: the exact bank-context wording is
    # not knowable offline, and a wrong single guess would no-op forever.
    assert list(option) == ["empty-to-bank", "empty"]
    # Bank-interface click: the 2026-09-02 blocker rects cover the bank's own inventory panel.
    assert kw["check_ui_blockers"] is False
    assert kw["offset"] == (0, 0)


def test_empty_log_basket_calls_click_menu_action_as_it_is_actually_declared(monkeypatch):
    """The fake above accepts **kw, so it will cheerfully report back an argument the REAL
    function does not have -- which is exactly what happened: the first version of
    empty_log_basket passed simulate_mistakes=False (copied from click_entity, which does take
    it), the test asserted the fake had received it, the suite went green, and the routine died
    live on `TypeError: click_menu_action() got an unexpected keyword argument`. CLAUDE.md
    already carried the lesson -- "a stub that lies about a shape is worse than no stub" -- so
    this binds the call against the real signature, where a bad kwarg is a TypeError here
    instead of in the VM. (click_menu_action needs no simulate_mistakes: it hardcodes
    simulate_mistakes=False on its own right-click.)"""
    import inspect

    sig = inspect.signature(controller.click_menu_action)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inv("Open log basket"))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)

    def binding_menu(*args, **kwargs):
        sig.bind(*args, **kwargs)      # raises TypeError on an argument that does not exist
        return True

    monkeypatch.setattr(controller, "click_menu_action", binding_menu)
    assert banker.empty_log_basket(_Ctx())


def test_empty_log_basket_retries_on_a_stale_widget_position(monkeypatch):
    """MEASURED LIVE 2026-09-08, first real bank trip: the right-click landed on BARE GROUND
    (menu `['Cancel', 'Walk here']`) because /inventory reported the basket at screen (31,50),
    read 0.12s after the bank interface opened -- a pre-layout widget position during the
    interface transition. 2.8s later the very next item in the same panel resolved to a sane
    (1598,445), and with the bank shut the basket itself reads (1529,438). So the position is
    not wrong, it is EARLY, and a re-read is the whole fix.

    Each attempt must re-read the inventory: retrying with the same stale dict would re-aim at
    the same dead pixel forever.
    """
    reads = []

    def inventory():
        reads.append(1)
        return _inv("Open log basket")

    monkeypatch.setattr(controller, "get_inventory", inventory)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    attempts = []

    def flaky(ser, entity, option, **kw):
        attempts.append(1)
        return len(attempts) > 1        # the first right-click hits bare ground

    monkeypatch.setattr(controller, "click_menu_action", flaky)
    assert banker.empty_log_basket(_Ctx())
    assert len(attempts) == 2
    assert len(reads) == 2, "each attempt must re-read the inventory, not reuse a stale dict"


def test_empty_log_basket_gives_up_rather_than_spinning(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: _inv("Open log basket"))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    attempts = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda *a, **k: attempts.append(1) and False)
    assert not banker.empty_log_basket(_Ctx())
    assert len(attempts) == banker.EMPTY_BASKET_TRIES


def test_empty_log_basket_is_a_no_op_without_one(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: _inv("Yew logs"))
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda *a, **k: pytest.fail("clicked with no basket in the inventory"))
    assert not banker.empty_log_basket(_Ctx())


def test_deposit_inventory_empties_the_log_basket_before_depositing(monkeypatch):
    # ORDER IS THE WHOLE POINT: emptied first, so the logs reach the bank; and the basket is on
    # the keep list, so the deposit pass that follows does not bank the basket itself away.
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {"open": True})
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: _inv("Open log basket", "Yew logs", "Rune axe"))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_down", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_up", lambda *a, **k: None)
    monkeypatch.setattr(banker, "deposit_all_containers", lambda ctx: True)

    order = []
    monkeypatch.setattr(banker, "empty_log_basket",
                        lambda ctx: order.append("empty") or True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, item, **kw: order.append("deposit:" + item["name"]))

    banker.deposit_inventory(_Ctx(), {"keepItems": ["axe", "log basket"]})

    assert order[0] == "empty"
    assert "deposit:Yew logs" in order
    assert not any(o.startswith("deposit:Open log basket") for o in order)
    assert not any(o.startswith("deposit:Rune axe") for o in order)


def test_deposit_inventory_honours_skip_container_deposit_for_the_basket(monkeypatch):
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {"open": True})
    monkeypatch.setattr(controller, "get_inventory", lambda: _inv("Open log basket"))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_down", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_up", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: None)
    monkeypatch.setattr(banker, "empty_log_basket",
                        lambda ctx: pytest.fail("emptied under skipContainerDeposit"))
    banker.deposit_inventory(_Ctx(), {"keepItems": ["log basket"], "skipContainerDeposit": True})


# --- bank PIN keypad (MEASURED live in the VM 2026-09-08) ---------------------------------

_PIN_DUMP = [
    {"group": 213, "child": 7, "text": "Please enter your PIN using the buttons below.",
     "screenX": 300, "screenY": 100},
    {"group": 213, "child": 10, "text": "First click the FIRST digit.",
     "screenX": 300, "screenY": 130},
    {"group": 213, "child": 3, "text": "?", "screenX": 10, "screenY": 10},
    {"group": 213, "child": 16, "text": "2", "screenX": 320, "screenY": 200},
    {"group": 213, "child": 18, "text": "9", "screenX": 380, "screenY": 200},
    {"group": 213, "child": 20, "text": "4", "screenX": 440, "screenY": 200},
]


def test_pin_button_is_matched_by_text_not_position():
    """The keypad RESHUFFLES after every digit, so position means nothing."""
    assert banker._pin_button(_PIN_DUMP, "9")["child"] == 18
    assert banker._pin_button(_PIN_DUMP, "4")["child"] == 20
    assert banker._pin_button(_PIN_DUMP, "7") is None


def test_pin_button_never_matches_the_readout_placeholders():
    """Children 3..6 are the '?' readout, not buttons -- clicking one would do nothing."""
    dump = [{"group": 213, "child": 4, "text": "7", "screenX": 10, "screenY": 10}]
    assert banker._pin_button(dump, "7") is None


def test_enter_bank_pin_clicks_each_digit_and_stops_when_the_keypad_closes(monkeypatch):
    import bank_pin
    monkeypatch.setattr(bank_pin, "load", lambda: "1234")
    state = {"clicks": 0}

    def dump(scan=False):
        if state["clicks"] >= 4:
            return []
        return [{"group": 213, "child": 10, "text": "digit %d" % state["clicks"],
                 "screenX": 1, "screenY": 1}] + \
               [{"group": 213, "child": 16 + 2 * i, "text": d, "screenX": 10 * i, "screenY": 9}
                for i, d in enumerate("1234")]

    monkeypatch.setattr(controller, "get_widgets_dump", dump)
    monkeypatch.setattr(controller, "click_entity",
                        lambda *a, **k: state.__setitem__("clicks", state["clicks"] + 1) or True)
    assert banker.enter_bank_pin(_Ctx())
    assert state["clicks"] == 4


def test_enter_bank_pin_reports_failure_when_no_pin_is_stored(monkeypatch):
    """Never a crash and never a guess -- the caller stops with a message naming the fix."""
    import bank_pin
    monkeypatch.setattr(bank_pin, "load", lambda: None)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: _PIN_DUMP)
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert not banker.enter_bank_pin(_Ctx())
    assert clicked == []


def test_enter_bank_pin_never_simulates_a_misclick(monkeypatch):
    """A miss plus its corrective reclick would enter a WRONG DIGIT -- on a real account that
    is a lockout attempt, not a retry."""
    import bank_pin
    monkeypatch.setattr(bank_pin, "load", lambda: "2")
    kwargs = {}
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: _PIN_DUMP)

    def click(ser, ent, **k):
        kwargs.update(k)
        monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
        return True

    monkeypatch.setattr(controller, "click_entity", click)
    banker.enter_bank_pin(_Ctx())
    assert kwargs.get("simulate_mistakes") is False
    assert kwargs.get("check_ui_blockers") is False
