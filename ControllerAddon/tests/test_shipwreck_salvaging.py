"""Hermetic tests for routines/shipwreck_salvaging.py and sailing.py (added 2026-09-03).

Every bridge read is stubbed -- nothing here touches the running client or the serial port.
The pinned facts (animation ids, object ids, chat text) come from the offline OSRS corpus:
runelite-api 1.12.38's AnimationID, the game-cache loc dump (shipwreck/hook ids), and the
Shipwreck salvaging wiki page (revision 15290733).
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import controller
import sailing
import shipwreck_salvaging as ss


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = type("S", (), {"data": {}, "config": dict(ss.DEFAULT_CONFIG)})()
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Default every bridge read to 'nothing' so a forgotten stub can never reach the client."""
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(controller, "get_sailing", lambda: {})
    monkeypatch.setattr(controller, "get_state", lambda: {})
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_tap", lambda *a, **k: None)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_hover", lambda: None)
    monkeypatch.setattr(controller, "hover_entity", lambda ser, ent, **kw: True)
    monkeypatch.setattr(controller, "get_widget", lambda *a, **k: {"found": False})
    monkeypatch.setattr(controller, "get_widget_children", lambda *a, **k: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "continue_box_open", lambda: False)
    monkeypatch.setattr(controller, "dismiss_continue_box", lambda ser: True)


def _boat_player(x=3000, y=3000, w=4, h=6):
    return {"x": x, "y": y, "plane": 0, "boatX": x, "boatY": y, "boatPlane": 0,
            "boatWidth": w, "boatHeight": h}


def _wreck(x, y, obj_id=60464, name="Small shipwreck"):
    return {"id": obj_id, "name": name, "x": x, "y": y, "plane": 0, "visible": True,
            "screenX": 500, "screenY": 500}


# --- channel animation coverage (runelite-api 1.12.38 AnimationID) ------------------------

@pytest.mark.parametrize("anim", [
    13569, 13570, 13571,   # SAILING_HUMAN_SALVAGE_HOOK_KANDARIN_1X3_{DROP01,IDLE01,RESET01}
    13576, 13577, 13578,   # ..._2X5_{DROP01,IDLE01,RESET01} (the only three the routine had)
    13583, 13584, 13585,   # ..._3X8_{DROP01,IDLE01,RESET01}
    13591, 13592, 13594,   # HUMAN_SAILING_SALVAGE01_LARGE01_{DROP01,IDLE01,RESET01}
])
def test_channel_animations_cover_every_hook_size(anim):
    assert anim in ss.SALVAGE_CHANNEL_ANIMATIONS


@pytest.mark.parametrize("anim", [
    -1,
    13352,   # HUMAN_SAILING_ALPHA_HELM_SMALL01_ACTIVE01_LOOP -- still standing at the helm
    13599,   # SAILING_HUMAN_SALVAGE_HOOK_KANDARIN_1X3_INTERACT01 -- Sort-salvage, not the hook
    13593,   # HUMAN_SAILING_SALVAGE01_LARGE01_INTERACT01 -- Sort-salvage
])
def test_non_channel_animations_are_not_channeling(anim):
    assert anim not in ss.SALVAGE_CHANNEL_ANIMATIONS


def test_helm_animation_is_recognised():
    assert 13352 in ss.HELM_ANIMATIONS


# --- level check fails OPEN when /skills cannot be read ----------------------------------

def test_boosted_level_is_none_when_skills_unreadable():
    assert ss._boosted_sailing_level() is None


def test_unreadable_level_does_not_filter_wrecks():
    assert ss._wreck_is_salvageable(_wreck(0, 0, obj_id=60478), None)


def test_readable_level_still_filters(monkeypatch):
    monkeypatch.setattr(controller, "get_skills", lambda: {"Sailing": {"boosted": 20}})
    assert ss._wreck_is_salvageable(_wreck(0, 0, obj_id=60464), ss._boosted_sailing_level())
    assert not ss._wreck_is_salvageable(_wreck(0, 0, obj_id=60466), ss._boosted_sailing_level())


# --- find_wreck_in_hook_range honours an exclude set --------------------------------------

def test_hook_range_search_skips_excluded_wreck(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player())
    near, other = _wreck(3003, 3003), _wreck(3006, 2995)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [near, other])
    cfg = ss.DEFAULT_CONFIG
    assert ss.find_wreck_in_hook_range(cfg)["x"] == 3003
    picked = ss.find_wreck_in_hook_range(cfg, exclude=frozenset({(3003, 3003, 0)}))
    assert picked is not None and picked["x"] == 3006


# --- approach point is an integer tile (ocean_path membership needs ints) -----------------

def test_approach_point_is_integer_tile():
    p = ss._approach_point_clear_of_wreck(3000, 3000, _wreck(3010, 3007), _boat_player())
    assert isinstance(p["x"], int) and isinstance(p["y"], int)


# --- drop threshold ----------------------------------------------------------------------

def _inventory(n, name="Small salvage"):
    return [{"name": name, "screenX": 1900 + i, "screenY": 900, "slot": i} for i in range(n)]


def test_tick_stows_before_the_inventory_is_completely_full(monkeypatch):
    """Task 8 update of the old test_tick_drops_before_the_inventory_is_completely_full: with
    stowSalvage defaulting True, a nearly-full salvage inventory now derives phase "stow" (see
    derive_phase's bullet 3) and tick() dispatches to the real stow_phase instead of dropping
    on the deck directly. sortWhenIdle is turned off so derive_phase's higher-ranked "idle gap
    -> sort" bullet (which fires whenever there is salvage to sort and no wreck in range,
    regardless of how full the inventory is) does not pre-empt the stow this test is checking."""
    ctx = _Ctx()
    ctx.state.config = _cfg(sortWhenIdle=False)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(controller.INVENTORY_SIZE - 1))
    calls = []
    monkeypatch.setattr(ss, "stow_phase", lambda c: calls.append(True) or True)
    ss.tick(ctx)
    assert calls, "one free slot left of salvage should stow, not drop, once stowSalvage is on"


def test_tick_still_drops_before_the_inventory_is_completely_full_when_stow_salvage_is_off(monkeypatch):
    """Regression guard for a real bug the reviewer found and ruled on, 2026-09-07:
    derive_phase used to gate its "stow" clause on stowSalvage too -- a DOUBLE gate, since
    stow_phase already branches on stowSalvage on its own (falling back to drop_salvage). Because
    tick() only ever reaches stow_phase (and so only ever reaches drop_salvage) from the
    phase == "stow" branch, that double gate meant a nearly-full salvage inventory was NEVER
    disposed of at all with stowSalvage=False -- contradicting the spec's own "stowSalvage:
    False restores today's behaviour exactly: drop_salvage on the deck."

    Exercises the REAL path end to end, with no derive_phase stub (the previous version of this
    test had to force the dispatch with a stub, which was itself the tell that something was
    wrong -- see git history). sortWhenIdle is off so derive_phase's higher-ranked idle-sort
    clause doesn't pre-empt the stow clause this test is checking (same reasoning as
    test_tick_stows_before_the_inventory_is_completely_full); with stowSalvage off, the real
    (unmocked) derive_phase now still returns "stow" (see
    test_stow_salvage_off_still_reaches_the_stow_phase), and the real (unmocked) stow_phase then
    falls back to drop_salvage."""
    ctx = _Ctx()
    ctx.state.config = _cfg(sortWhenIdle=False, stowSalvage=False)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(controller.INVENTORY_SIZE - 1))
    calls = []
    monkeypatch.setattr(ss, "drop_salvage", lambda c: calls.append(True))
    ss.tick(ctx)
    assert calls, "tick() must still reach drop_salvage (via the real stow_phase) when stowSalvage is off"


def test_tick_does_not_drop_or_stow_with_plenty_of_room(monkeypatch):
    """Task 8 update of the old test_tick_does_not_drop_with_plenty_of_room, which protected a
    comfortable inventory from triggering any disposal path. Under the new derive_phase
    dispatch a 20/28 inventory alone is not "wait" by default (sortWhenIdle's idle-sort bullet
    would claim it, since 20 items of salvage is still "something to sort"), so this pins the
    config/state that makes "wait" the actually-correct phase for a comfortable inventory:
    sortWhenIdle off, no wreck in range (default get_objects stub), and a hold already known
    empty. Neither drop_salvage nor stow_phase must fire."""
    ctx = _Ctx()
    ctx.state.config = _cfg(sortWhenIdle=False)
    ctx.state.data["hold_has_salvage"] = False
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(20))
    drop_calls = []
    stow_calls = []
    monkeypatch.setattr(ss, "drop_salvage", lambda c: drop_calls.append(True))
    monkeypatch.setattr(ss, "stow_phase", lambda c: stow_calls.append(True) or True)
    ss.tick(ctx)
    assert not drop_calls
    assert not stow_calls


def test_drop_salvage_never_simulates_a_misclick(monkeypatch):
    """I6 changed drop_items to verify the FIRST click against a shrinking inventory (see the
    dedicated I6 tests below for the not-shrinking/fallback case) -- so the mock inventory here
    must actually shrink in response to a click, or drop_works reads False on item 0 and every
    later item goes through click_menu_action instead, which this test isn't about."""
    ctx = _Ctx()
    inv = _inventory(3) + [{"name": "Chisel", "screenX": 1, "screenY": 1}]
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv))
    seen = []

    def _click(ser, ent, **kw):
        seen.append((ent["name"], kw))
        inv.remove(ent)
        return True
    monkeypatch.setattr(controller, "click_entity", _click)
    ss.drop_salvage(ctx)
    assert [n for n, _ in seen] == ["Small salvage"] * 3
    assert all(kw.get("simulate_mistakes") is False for _, kw in seen)


# --- chat signals ------------------------------------------------------------------------

def test_sunk_message_clears_the_hook_cooldown():
    ctx = _Ctx()
    ctx.state.data["last_deploy_click_at"] = 10 ** 12
    ss.on_event(ctx, "CHAT_MESSAGE", {"type": "GAMEMESSAGE",
                                      "message": "You salvage all you can from the shipwreck before it is reclaimed by the sea."})
    assert "last_deploy_click_at" not in ctx.state.data


def test_hazardous_waters_blacklists_the_current_wreck():
    ctx = _Ctx()
    ctx.state.data["salvage_current_wreck_key"] = (3003, 3003, 0)
    ss.on_event(ctx, "CHAT_MESSAGE", {"type": "GAMEMESSAGE",
                                      "message": "It's not safe to salvage shipwrecks that are in hazardous waters."})
    d = ctx.state.data
    assert d["hook_engage_giveup_key"] == (3003, 3003, 0)
    assert d["far_wreck_giveup_key"] == (3003, 3003, 0)
    assert d["hook_engage_giveup_until"] > __import__("time").time() + 600
    assert d["far_wreck_giveup_until"] > __import__("time").time() + 600


def test_unrelated_chat_is_ignored():
    ctx = _Ctx()
    ctx.state.data["last_deploy_click_at"] = 5
    ss.on_event(ctx, "CHAT_MESSAGE", {"type": "PUBLICCHAT", "message": "reclaimed by the sea lol"})
    assert ctx.state.data["last_deploy_click_at"] == 5


# --- far branch: a landing outside hook range repositions instead of re-deploying ----------

def test_far_branch_repositions_when_landing_is_out_of_hook_range(monkeypatch):
    """This is legacy far-wreck-reposition machinery, predating Task 8's massWorld gate --
    massWorld now defaults True and skips the far-wreck branch entirely (see the massWorld
    gating tests below), and with no wreck yet in hook range the default sortWhenIdle=True
    would otherwise pre-empt with the sort phase. Opt out of both to keep exercising the
    branch this test is actually about."""
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=False, sortWhenIdle=False)
    player = _boat_player(3000, 3000)
    far = _wreck(3030, 3000)
    monkeypatch.setattr(controller, "get_player", lambda: player)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [far])
    monkeypatch.setattr(controller, "get_skills", lambda: {"Sailing": {"boosted": 64}})
    # First tick: sail_to_pair "succeeds" but the boat did not move into hook range.
    monkeypatch.setattr(ss, "sail_to_pair", lambda c, cfg, w: True)
    sails, deploys = [], []
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: sails.append(t) or True)
    monkeypatch.setattr(sailing, "deploy_hook", lambda c, n: deploys.append(n) or True)
    ss.tick(ctx)
    assert sails and not deploys, "out of range after the sail: reposition, do not deploy"
    assert abs(sails[0]["x"] - far["x"]) < ss.WRECK_SQUARE_HALF


def test_far_branch_deploys_when_landing_is_in_hook_range(monkeypatch):
    """Same massWorld/sortWhenIdle opt-out as the test above -- see its docstring."""
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=False, sortWhenIdle=False)
    far = _wreck(3030, 3000)
    # get_player answers "far away" until the sail happens, then "in range".
    boat = {"pos": _boat_player(3000, 3000)}
    monkeypatch.setattr(controller, "get_player", lambda: boat["pos"])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [far])
    monkeypatch.setattr(controller, "get_skills", lambda: {"Sailing": {"boosted": 64}})

    def _sailed(c, cfg, w):
        boat["pos"] = _boat_player(3026, 3000)
        return True
    monkeypatch.setattr(ss, "sail_to_pair", _sailed)
    deploys = []
    monkeypatch.setattr(sailing, "sail_to", lambda c, t: True)
    monkeypatch.setattr(sailing, "deploy_hook", lambda c, n: deploys.append(n) or True)
    ss.tick(ctx)
    assert deploys


# --- sailing.py: toggle/fixture clicks never get the simulated misclick --------------------

def _sailing_state_open(speed=0.0):
    return {"facilitiesTabOpen": True, "sailToggle": {"screenX": 50, "screenY": 60},
            "boatSpeed": speed, "navigating": False}


def test_stop_boat_clicks_toggle_without_simulated_misclick(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_sailing", _sailing_state_open)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 1})
    monkeypatch.setattr(sailing.time, "sleep", lambda s: None)
    seen = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: seen.append(kw) or True)
    assert sailing.stop_boat(ctx)
    assert seen and seen[0].get("simulate_mistakes") is False
    # The panel lives in the sidebar, which /state now reports as a UI blocker -- an interface
    # click must opt out of that check or click_entity rotates the camera instead of clicking.
    assert seen[0].get("check_ui_blockers") is False


def test_stop_boat_retaps_q_until_the_tab_opens(monkeypatch):
    ctx = _Ctx()
    states = iter([{"facilitiesTabOpen": False}, {"facilitiesTabOpen": False},
                   _sailing_state_open(), _sailing_state_open()] + [_sailing_state_open()] * 20)
    monkeypatch.setattr(controller, "get_sailing", lambda: next(states))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 1})
    monkeypatch.setattr(sailing.time, "sleep", lambda s: None)
    taps = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: taps.append(key))
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: True)
    assert sailing.stop_boat(ctx)
    assert taps.count(sailing.SAILING_OPTIONS_TAB_KEY) >= 2


def test_stop_boat_waits_for_two_consecutive_still_reads(monkeypatch):
    ctx = _Ctx()
    # Reads in order: the panel-open check, the pre-toggle snapshot, then one per settle poll.
    speeds = iter([1.2, 1.2, 0.0, 0.7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(controller, "get_sailing", lambda: _sailing_state_open(next(speeds)))
    reads = []
    monkeypatch.setattr(controller, "get_player", lambda: reads.append(1) or {"x": 1, "y": 1})
    monkeypatch.setattr(sailing.time, "sleep", lambda s: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: True)
    assert sailing.stop_boat(ctx)
    # start read + at least 4 polls: the lone 0.0 on the first poll (and the one after the 0.7)
    # must not have been accepted on its own. The old code returned after 2 polls.
    assert len(reads) >= 5


def test_deploy_hook_and_helm_clicks_skip_simulated_misclick(monkeypatch):
    ctx = _Ctx()
    hook = {"name": "Steel salvaging hook", "visible": True, "screenX": 1, "screenY": 1,
            "actions": ["Deploy", "Modify"]}
    helm = {"name": "Helm", "visible": True, "screenX": 2, "screenY": 2,
            "actions": ["Navigate", "Escape"]}
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player())
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [hook, helm])
    seen = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: seen.append(kw) or True)
    assert sailing.deploy_hook(ctx, "salvaging hook")
    monkeypatch.setattr(controller, "get_sailing", lambda: {"navigating": True})
    monkeypatch.setattr(sailing.time, "sleep", lambda s: None)
    assert sailing.use_helm(ctx)
    assert len(seen) == 2 and all(kw.get("simulate_mistakes") is False for kw in seen)


# --- 2026-09-04 pass: minimap hops, braking, hover-verified toggle, XP watchdog ------------

def test_minimap_hop_click_skips_the_ui_blocker_check(monkeypatch):
    # /state has listed the minimap rects as UI blockers since the 2026-09-02 rebuild; a minimap
    # click that still runs the blocker check is refused and turns the camera instead.
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: {"onMinimap": True, "screenX": 20, "screenY": 20})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000})
    monkeypatch.setattr(sailing.time, "sleep", lambda s: None)
    seen = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: seen.append(kw) or True)
    assert sailing._hop_to(ctx, 3006, 3000, 0)
    assert seen and seen[0].get("check_ui_blockers") is False


def test_a_hop_point_off_the_minimap_is_pulled_in_rather_than_abandoned(monkeypatch):
    # The minimap's reach is a user setting (zooming it in shrinks it); travel._hop_to already
    # pulls an unreachable aim point in toward the player instead of failing the whole trip.
    ctx = _Ctx()
    asked = []

    def _mm(x, y, p):
        asked.append((x, y))
        return {"onMinimap": len(asked) >= 2, "screenX": 20, "screenY": 20}
    monkeypatch.setattr(controller, "get_minimap_walk_point", _mm)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000})
    monkeypatch.setattr(sailing.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: clicks.append(ent) or True)
    assert sailing._hop_to(ctx, 3010, 3000, 0)
    assert len(asked) == 2 and asked[1][0] < 3010, asked
    assert len(clicks) == 1


def test_a_hop_point_off_the_minimap_at_every_distance_still_gives_up(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: {"onMinimap": False})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000})
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: clicks.append(ent) or True)
    assert not sailing._hop_to(ctx, 3010, 3000, 0)
    assert not clicks


def test_brake_distance_includes_the_stop_reaction_time(monkeypatch):
    monkeypatch.setattr(controller, "get_sailing", lambda: {"boatSpeed": 2.0})
    assert sailing._dynamic_brake_tiles() == 2.0 * (sailing.STOP_REACTION_TICKS + sailing.BRAKE_LOOKAHEAD_TICKS)
    monkeypatch.setattr(controller, "get_sailing", lambda: {"boatSpeed": 0.0})
    assert sailing._dynamic_brake_tiles() == sailing.SAIL_ARRIVAL_TOLERANCE
    monkeypatch.setattr(controller, "get_sailing", lambda: {})
    assert sailing._dynamic_brake_tiles() == sailing.SAIL_BRAKE_TRIGGER_TILES


def test_sail_to_opens_the_sailing_options_panel_before_the_final_leg(monkeypatch):
    ctx = _Ctx()
    order = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_sailing", lambda: {"boatSpeed": 0.0, "facilitiesTabOpen": False})
    monkeypatch.setattr(sailing, "use_helm", lambda c: True)
    monkeypatch.setattr(sailing.ocean_path, "find_ocean_route",
                        lambda s, t: [{"x": 3010, "y": 3000, "plane": 0}, t])
    monkeypatch.setattr(sailing, "steer_toward",
                        lambda c, leg, arrival_tolerance=None: order.append(("leg", leg["x"])) or True)
    monkeypatch.setattr(sailing, "open_sailing_options_tab", lambda c: order.append(("panel", None)))
    monkeypatch.setattr(sailing, "stop_boat",
                        lambda c, t=None, brake_started_at=None: order.append(("stop", brake_started_at)) or True)
    assert sailing.sail_to(ctx, {"x": 3030, "y": 3000, "plane": 0})
    kinds = [k for k, _ in order]
    assert kinds == ["leg", "panel", "leg", "stop"], kinds
    assert order[-1][1] is not None, "stop_boat must be told when braking began"


def test_stop_boat_skips_the_toggle_when_the_sails_are_already_down(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_sailing", _sailing_state_open)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 1})
    monkeypatch.setattr(controller, "get_hover", lambda: {"option": "Set sails", "target": ""})
    monkeypatch.setattr(sailing.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: clicks.append(kw) or True)
    assert sailing.stop_boat(ctx)
    assert not clicks, "clicking 'Set sails' on a stopped boat would send it off again"


@pytest.mark.parametrize("hover", [None, {"option": "Un-set sails"}, {"option": "Walk here"},
                                   {"menuOpen": True, "option": "Set sails"}])
def test_stop_boat_toggles_unless_the_hover_proves_the_sails_are_down(monkeypatch, hover):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_sailing", _sailing_state_open)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 1})
    monkeypatch.setattr(controller, "get_hover", lambda: hover)
    monkeypatch.setattr(sailing.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: clicks.append(kw) or True)
    assert sailing.stop_boat(ctx)
    assert len(clicks) == 1


def test_channel_without_sailing_xp_for_too_long_forces_a_reclick(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player())
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3003, 3003)])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": 13577})
    monkeypatch.setattr(controller, "get_skills", lambda: {"Sailing": {"boosted": 64, "xp": 1000}})
    deploys = []
    # skip=() default: deploy_hook (Task 9) now takes a skip set and tick() reads .get() off
    # its return, so the stub must accept the kwarg and return a dict, not a bare True.
    monkeypatch.setattr(sailing, "deploy_hook",
                        lambda c, n, skip=(): deploys.append(n) or {"x": 1, "y": 1, "plane": 0})
    ss.tick(ctx)
    assert not deploys, "a channel that is animating is left alone at first"
    ctx.state.data["sailing_xp_changed_at"] -= ss.CHANNEL_XP_STALL_S + 1
    ss.tick(ctx)
    assert deploys, "no XP for CHANNEL_XP_STALL_S while 'channeling' means the hook is not working"


def test_sailing_xp_gain_resets_the_stall_clock(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player())
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3003, 3003)])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": 13577})
    xp = {"v": 1000}
    monkeypatch.setattr(controller, "get_skills", lambda: {"Sailing": {"boosted": 64, "xp": xp["v"]}})
    deploys = []
    # skip=() default -- see the same note in test_channel_without_sailing_xp_for_too_long_forces_a_reclick.
    monkeypatch.setattr(sailing, "deploy_hook",
                        lambda c, n, skip=(): deploys.append(n) or {"x": 1, "y": 1, "plane": 0})
    ss.tick(ctx)
    ctx.state.data["sailing_xp_changed_at"] -= ss.CHANNEL_XP_STALL_S + 1
    xp["v"] = 1010
    ss.tick(ctx)
    assert not deploys


# --- find_facility: our own boat vs. the neighbours' -------------------------------------
# Geometry MEASURED live 2026-09-07 on a mass world: 26 "Salvaging station" objects from ~13
# overlapping boats, three of them on the player's exact tile, all clickable, screen positions
# ~20px apart. Distance cannot separate them; boatViewId can.

def _station(boat_view_id, x=2681, y=2545, screen=(1209, 652)):
    return {"id": 59701, "name": "Salvaging station", "x": x, "y": y, "plane": 0,
            "visible": True, "clickable": True, "distance": 0,
            "screenX": screen[0], "screenY": screen[1],
            "actions": ["Sort-salvage", "Modify"], "boatViewId": boat_view_id}


def test_find_facility_picks_our_own_boats_station(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(_boat_player(2681, 2545),
                                                               boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _station(2001, screen=(1214, 672)),
        _station(1036, screen=(1209, 652)),
        _station(2002, screen=(1191, 648)),
    ])
    found = sailing.find_facility("salvaging station", action="Sort-salvage")
    assert found is not None
    assert found["boatViewId"] == 1036
    assert found["screenX"] == 1209


def test_find_facility_returns_none_when_only_other_boats_have_one(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(_boat_player(),
                                                               boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_station(2001), _station(2002)])
    assert sailing.find_facility("salvaging station") is None


def test_find_facility_filters_on_the_action_when_given(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(_boat_player(),
                                                               boatViewId=1036))
    hold = {"id": 60279, "name": "Mahogany cargo hold", "x": 2680, "y": 2545, "plane": 0,
            "visible": True, "screenX": 100, "screenY": 100,
            "actions": ["Open", "Deposit-all", "Modify"], "boatViewId": 1036}
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_station(1036), hold])
    assert sailing.find_facility("cargo hold", action="Open")["id"] == 60279
    assert sailing.find_facility("cargo hold", action="Sort-salvage") is None


def test_find_facility_falls_back_to_nearest_when_boat_view_id_is_absent(monkeypatch):
    """An un-rebuilt client serves no boatViewId. Behave exactly as before rather than
    refusing to work -- degrade gracefully, never fail closed."""
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(2681, 2545))
    near = {"id": 59701, "name": "Salvaging station", "x": 2681, "y": 2545, "plane": 0,
            "visible": True, "screenX": 1, "screenY": 1, "actions": ["Sort-salvage"]}
    far = {"id": 59701, "name": "Salvaging station", "x": 2690, "y": 2560, "plane": 0,
           "visible": True, "screenX": 2, "screenY": 2, "actions": ["Sort-salvage"]}
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [far, near])
    assert sailing.find_facility("salvaging station")["screenX"] == 1


def test_find_facility_ignores_invisible_objects(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(_boat_player(),
                                                               boatViewId=1036))
    hidden = dict(_station(1036), visible=False)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [hidden])
    assert sailing.find_facility("salvaging station") is None


# --- keep-list ---------------------------------------------------------------------------

def test_hard_keep_survives_an_empty_keep_list():
    """keepItems is a list a human edits between sessions; a typo must never be able to drop
    the tools."""
    for tool in ["Chisel", "Rune pouch", "Coins", "Gem bag", "Plank sack", "Log basket"]:
        assert ss.keep_item(tool, []) is True


def test_hard_keep_cannot_be_overridden_by_an_empty_config():
    assert ss.keep_item("Chisel", []) is True
    assert ss.keep_item("Gold ring", []) is False


def test_salvage_is_always_kept():
    """Unsorted salvage is the routine's WORK -- it gets stowed or sorted, never dropped by the
    post-sort disposal."""
    assert ss.keep_item("Plundered salvage", []) is True
    assert ss.keep_item("Small salvage", []) is True


def test_keep_list_matches_case_insensitive_substrings():
    assert ss.keep_item("Diamond bracelet", ["diamond"]) is True
    assert ss.keep_item("Diamond ring", ["diamond"]) is True
    assert ss.keep_item("Ruby bracelet", ["RUBY"]) is True
    assert ss.keep_item("Emerald ring", ["diamond", "ruby"]) is False


def test_the_inert_amulet_is_matched_without_relying_on_the_apostrophe():
    """Cache obj 32398 is named "Sailors' amulet (inert)". The default list keys on
    "amulet (inert)" precisely so a differing apostrophe cannot silently drop it."""
    assert "amulet (inert)" in ss.DEFAULT_CONFIG["keepItems"]
    assert ss.keep_item("Sailors' amulet (inert)", ss.DEFAULT_CONFIG["keepItems"]) is True
    assert ss.keep_item("Sailors’ amulet (inert)", ss.DEFAULT_CONFIG["keepItems"]) is True


def test_the_default_keep_list_drops_what_the_user_asked_to_toss():
    keep = ss.DEFAULT_CONFIG["keepItems"]
    for tossed in ["Casket", "Oyster pearls", "Gold ring", "Sapphire ring",
                   "Emerald bracelet", "Mithril scimitar", "Mithril cannonball",
                   "Adamant cannonball"]:
        assert ss.keep_item(tossed, keep) is False, tossed


def test_the_default_keep_list_keeps_what_the_user_asked_to_keep():
    keep = ss.DEFAULT_CONFIG["keepItems"]
    for kept in ["Facility bottle (empty)", "Mahogany repair kit", "Diamond ring",
                 "Diamond bracelet", "Ruby bracelet", "Dragon cannonball",
                 "Rune scimitar", "Rune cannonball", "Sailors' amulet (inert)"]:
        assert ss.keep_item(kept, keep) is True, kept


# --- drop_items --------------------------------------------------------------------------

def test_drop_items_drops_only_what_the_predicate_selects(monkeypatch):
    """I8: the mock inventory must actually shrink in response to a click, or the confirmed-delta
    return (see the I8 tests below) reads 0 regardless of what the predicate selected -- this
    test is about the predicate filter, not the confirmation, so give it a working drop."""
    inv = [{"name": "Gold ring", "screenX": 1, "screenY": 1},
           {"name": "Chisel", "screenX": 2, "screenY": 2},
           {"name": "Rune scimitar", "screenX": 3, "screenY": 3}]
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv))
    clicked = []
    def _click(ser, item, **kw):
        clicked.append(item["name"])
        inv.remove(item)
        return True
    monkeypatch.setattr(controller, "click_entity", _click)
    ctx = _Ctx()
    dropped = ss.drop_items(ctx, lambda name: name == "Gold ring")
    assert clicked == ["Gold ring"]
    assert dropped == 1


def test_drop_items_never_simulates_a_misclick(monkeypatch):
    """A simulated near-miss lands on the NEIGHBOURING slot -- which is a tool."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Gold ring", "screenX": 1, "screenY": 1}])
    seen = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, item, **kw: seen.update(kw) or True)
    ss.drop_items(_Ctx(), lambda name: True)
    assert seen.get("simulate_mistakes") is False
    assert seen.get("check_ui_blockers") is False


# --- I6: drop_items verifies the first drop and falls back to the menu ---------------------

def test_drop_items_falls_back_to_the_menu_when_the_first_drop_frees_nothing(monkeypatch):
    """A forced-Drop swap that has gone missing or is mis-set on a ring/bracelet/scimitar would
    otherwise leave click_entity resolving to Wear/Wield -- a truthful-looking 'dropped' count
    with nothing actually leaving the inventory, which drives straight into the C2 deadlock."""
    monkeypatch.setattr(ss, "DROP_CONFIRM_S", 0.02)  # I8 poll -- keep the "never shrinks" path fast
    inv = [{"name": "Gold ring", "screenX": 1, "screenY": 1},
           {"name": "Sapphire ring", "screenX": 2, "screenY": 2}]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)   # static: nothing ever drops
    entity_clicks = []
    menu_clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: entity_clicks.append(ent["name"]) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **kw: menu_clicks.append((ent["name"], option)) or True)
    dropped = ss.drop_items(_Ctx(), lambda name: True)
    # Only the (failed) first attempt goes through click_entity -- everything after the verify
    # fails, including the retry of item 0 itself, goes through the menu.
    assert entity_clicks == ["Gold ring"]
    assert menu_clicks == [("Gold ring", "Drop"), ("Sapphire ring", "Drop")]
    # I8: both click_entity and click_menu_action "succeed" (return True) here, but the static
    # inventory never actually shrinks -- so the CONFIRMED delta sort_phase relies on as its
    # progress signal must read 0, not the number of clicks issued. This is the regression guard
    # for SORT_FAIL_LIMIT: a click that lands on nothing (e.g. a stray interface eating it) must
    # never be reported as progress.
    assert dropped == 0


def test_drop_items_logs_the_names_it_dropped(monkeypatch):
    inv = [{"name": "Gold ring", "screenX": 1, "screenY": 1},
           {"name": "Gold ring", "screenX": 2, "screenY": 2}]
    calls = list(inv)
    def _click(ser, ent, **kw):
        calls.remove(ent)
        return True
    monkeypatch.setattr(controller, "get_inventory", lambda: list(calls))
    monkeypatch.setattr(controller, "click_entity", _click)
    logged = []
    monkeypatch.setattr(ss.debug_log, "log", lambda cat, msg: logged.append((cat, msg)))
    ss.drop_items(_Ctx(), lambda name: True)
    assert any("Gold ring" in msg and "dropped:" in msg for _, msg in logged)


def test_drop_items_dismisses_a_stray_continue_box_on_entry(monkeypatch):
    """Minor fix: drop_items' own blind spot -- a continue box already up when it's ENTERED,
    not only one raised by a click issued from inside this call."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    calls = []
    monkeypatch.setattr(controller, "dismiss_continue_box", lambda ser: calls.append(True) or True)
    ss.drop_items(_Ctx(), lambda name: True)
    assert calls == [True]


# --- I8: drop_items returns the CONFIRMED delta, not the attempt count --------------------

def test_drop_items_returns_the_confirmed_delta_not_the_attempt_count(monkeypatch):
    """Concrete failure this guards: a stray interface open over the inventory (this codebase
    records exactly that class of bug for motherlode_mine.py -- 'an open interface eats the
    first world click') swallows every click. Both click_entity and click_menu_action still
    report success, but nothing leaves the inventory. sort_phase treats drop_items' return
    value as its progress signal (the input to SORT_FAIL_LIMIT), so a wrong count here defeats
    the ceiling that stops the sort rung right-clicking a full inventory forever, unattended."""
    monkeypatch.setattr(ss, "DROP_CONFIRM_S", 0.02)  # I8 poll -- keep the "never shrinks" path fast
    inv = [{"name": "Small salvage", "screenX": 1, "screenY": 1},
           {"name": "Small salvage", "screenX": 2, "screenY": 2},
           {"name": "Small salvage", "screenX": 3, "screenY": 3}]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)  # static: nothing ever leaves
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **kw: True)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, ent, option, **kw: True)
    dropped = ss.drop_items(_Ctx(), lambda name: True)
    assert dropped == 0


def test_drop_items_confirmed_after_a_delay_still_counts_as_working(monkeypatch):
    """A drop is processed on the NEXT server tick (0-600ms), so the inventory shrinking on the
    third /inventory read rather than the first is a perfectly good drop, not a broken swap --
    drop_items must not fall back to the right-click menu or log the 'swap may be missing'
    warning over ordinary tick latency."""
    inv = [{"name": "Small salvage", "screenX": 1, "screenY": 1},
           {"name": "Small salvage", "screenX": 2, "screenY": 2}]
    reads = {"n": 0}

    def _get_inventory():
        reads["n"] += 1
        if reads["n"] < 3:
            return list(inv)          # first two reads: drop hasn't landed on the server yet
        return list(inv)[1:]          # third read onward: the server processed it

    monkeypatch.setattr(controller, "get_inventory", _get_inventory)
    entity_clicks = []
    menu_clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: entity_clicks.append(ent["name"]) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **kw: menu_clicks.append((ent["name"], option)) or True)
    logged = []
    monkeypatch.setattr(ss.debug_log, "log", lambda cat, msg: logged.append(msg))
    ss.drop_items(_Ctx(), lambda name: True)
    assert menu_clicks == []
    assert not any("swap may be missing" in msg for msg in logged)


# --- derive_phase ------------------------------------------------------------------------

def _cfg(**over):
    cfg = dict(ss.DEFAULT_CONFIG)
    cfg.update(over)
    return cfg


def _phase(**kw):
    args = {"wreck_in_range": False, "hold_full": False, "inventory_salvage": 0,
            "free_slots": 20, "hold_has_salvage": False}
    cfg = kw.pop("config", _cfg())
    args.update(kw)
    return ss.derive_phase(cfg, **args)


def test_a_wreck_in_range_means_hook():
    assert _phase(wreck_in_range=True) == "hook"


def test_nothing_in_range_and_nothing_to_sort_means_wait():
    assert _phase() == "wait"


def test_a_full_inventory_of_salvage_means_stow():
    assert _phase(wreck_in_range=True, inventory_salvage=27, free_slots=1) == "stow"


def test_a_full_hold_outranks_a_wreck_in_range():
    """With the hold full there is nowhere for new salvage to go, so hooking would only fill
    the inventory and stall. This is the user's explicit exception to the interrupt rule."""
    assert _phase(wreck_in_range=True, hold_full=True, hold_has_salvage=True) == "sort"


def test_a_wreck_rising_mid_sort_returns_to_the_hook():
    """The user's interrupt rule: hooking out-earns sorting, so abandon the sort."""
    assert _phase(wreck_in_range=True, hold_has_salvage=True, hold_full=False) == "hook"


def test_an_idle_gap_with_salvage_in_the_hold_means_sort():
    assert _phase(wreck_in_range=False, hold_has_salvage=True) == "sort"


def test_an_idle_gap_with_salvage_in_the_inventory_means_sort():
    assert _phase(wreck_in_range=False, inventory_salvage=5) == "sort"


def test_sort_when_idle_off_leaves_an_idle_gap_as_wait():
    assert _phase(config=_cfg(sortWhenIdle=False), hold_has_salvage=True) == "wait"


def test_sort_when_idle_off_still_sorts_a_full_hold():
    """A full hold is not an optimisation, it is a blockage -- sortWhenIdle must not gate it."""
    assert _phase(config=_cfg(sortWhenIdle=False), hold_full=True,
                  hold_has_salvage=True) == "sort"


def test_an_unknown_hold_is_tried_once_rather_than_assumed_empty():
    """hold_has_salvage None = never read this session. Assuming 'no' means a restart during a
    respawn gap never sorts until the next stow; one read settles it."""
    assert _phase(hold_has_salvage=None) == "sort"


def test_stow_salvage_off_still_reaches_the_stow_phase():
    """Fixed 2026-09-07 (real regression, ruled by the reviewer): this test used to assert
    "hook" here, on the theory that stowSalvage should also gate derive_phase's stow clause.
    That was wrong -- stow_phase ALREADY branches on stowSalvage (falling back to drop_salvage
    when it's off), and tick() only ever reaches stow_phase/drop_salvage via phase == "stow", so
    double-gating it here meant a nearly-full inventory with stowSalvage=False was NEVER
    disposed of at all, contradicting the spec's own "stowSalvage: False restores today's
    behaviour exactly: drop_salvage on the deck." The phase name "stow" describes the SLOT
    condition (salvage present, at or below dropAtFreeSlots free) -- not which disposal method
    is used, which is stow_phase's call alone."""
    assert _phase(config=_cfg(stowSalvage=False), wreck_in_range=True,
                  inventory_salvage=27, free_slots=1) == "stow"


def test_stow_needs_actual_salvage_not_just_a_full_inventory():
    """An inventory full of sorted output is a disposal problem, not a stow one."""
    assert _phase(wreck_in_range=True, inventory_salvage=0, free_slots=0) == "hook"


# --- stow_phase --------------------------------------------------------------------------

class _FakeHold:
    def __init__(self, usage=(12, 160), deposit=True, rows=("Plundered salvage",)):
        self.usage, self.deposit_ok, self.rows = usage, deposit, rows
        self.calls = []

    def open_hold(self, ctx):
        self.calls.append("open"); return True

    def deposit_salvage(self, ctx):
        self.calls.append("deposit"); return self.deposit_ok

    def read_usage(self):
        return self.usage

    def salvage_rows(self):
        return [{"itemName": n} for n in self.rows]

    def close_hold(self, ctx):
        self.calls.append("close"); return True


def _install_hold(monkeypatch, fake):
    for name in ("open_hold", "deposit_salvage", "read_usage", "salvage_rows", "close_hold"):
        monkeypatch.setattr(ss.cargo_hold, name, getattr(fake, name))


def test_stow_deposits_and_records_the_hold_usage(monkeypatch):
    fake = _FakeHold(usage=(40, 160))
    _install_hold(monkeypatch, fake)
    ctx = _Ctx()
    assert ss.stow_phase(ctx) is True
    assert fake.calls == ["open", "deposit", "close"]
    assert ctx.state.data["hold_used"] == 40
    assert ctx.state.data["hold_capacity"] == 160
    assert ctx.state.data["hold_has_salvage"] is True
    assert ctx.state.data["hold_full"] is False


def test_stow_marks_the_hold_full_at_the_configured_margin(monkeypatch):
    _install_hold(monkeypatch, _FakeHold(usage=(157, 160)))   # holdFullSlots default 4
    ctx = _Ctx()
    ss.stow_phase(ctx)
    assert ctx.state.data["hold_full"] is True


def test_salvage_left_behind_marks_the_hold_full_whatever_the_numbers_say(monkeypatch):
    """The authoritative signal: a declared capacity is a ceiling, not a promise."""
    _install_hold(monkeypatch, _FakeHold(usage=(10, 160), deposit=False))
    ctx = _Ctx()
    assert ss.stow_phase(ctx) is False
    assert ctx.state.data["hold_full"] is True


def test_an_unreadable_usage_does_not_claim_the_hold_has_room(monkeypatch):
    """Unreadable means 'cannot tell', never 'empty'."""
    _install_hold(monkeypatch, _FakeHold(usage=(None, None)))
    ctx = _Ctx()
    ss.stow_phase(ctx)
    assert ctx.state.data.get("hold_full") is False
    assert ctx.state.data.get("hold_used") is None


def test_stow_falls_back_to_dropping_when_the_hold_cannot_be_opened(monkeypatch):
    fake = _FakeHold()
    fake.open_hold = lambda ctx: False
    _install_hold(monkeypatch, fake)
    dropped = []
    monkeypatch.setattr(ss, "drop_salvage", lambda ctx: dropped.append(True) or 1)
    assert ss.stow_phase(_Ctx()) is True
    assert dropped == [True]


def test_stow_salvage_off_drops_instead(monkeypatch):
    called = []
    monkeypatch.setattr(ss, "drop_salvage", lambda ctx: called.append(True) or 1)
    _install_hold(monkeypatch, _FakeHold())
    ctx = _Ctx()
    ctx.state.config = _cfg(stowSalvage=False)
    assert ss.stow_phase(ctx) is True
    assert called == [True]


# --- sort_phase --------------------------------------------------------------------------

def test_sort_animations_are_separate_from_the_hook_channel():
    """Merging them would make the hook's idle-reclick logic treat sorting as hooking."""
    assert 13599 in ss.SORT_ANIMATIONS and 13593 in ss.SORT_ANIMATIONS
    assert not (ss.SORT_ANIMATIONS & ss.SALVAGE_CHANNEL_ANIMATIONS)


def test_sort_rate_is_the_measured_one():
    assert ss.SORT_SECONDS_PER_SALVAGE == 1.79


def _station_obj():
    return {"id": 59701, "name": "Salvaging station", "x": 1, "y": 1, "plane": 0,
            "visible": True, "screenX": 9, "screenY": 9,
            "actions": ["Sort-salvage", "Modify"], "boatViewId": 1036}


def test_sort_disposes_before_withdrawing(monkeypatch):
    """The deadlock: enter sort holding a full inventory of the PREVIOUS batch's output, find
    no salvage to sort, and try to withdraw into an inventory with no free slots."""
    order = []
    _install_hold(monkeypatch, _FakeHold())
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage",
                        lambda ctx: order.append("withdraw") or True)
    monkeypatch.setattr(ss, "drop_items",
                        lambda ctx, pred: order.append("dispose") or 1)
    monkeypatch.setattr(sailing, "find_facility", lambda *a, **k: _station_obj())
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    ss.sort_phase(_Ctx())
    assert order[0] == "dispose"
    assert "withdraw" in order


def test_sort_clicks_our_own_stations_sort_salvage(monkeypatch):
    _install_hold(monkeypatch, _FakeHold())
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: True)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    seen = {}
    monkeypatch.setattr(sailing, "find_facility",
                        lambda name, **kw: seen.update({"name": name, **kw}) or _station_obj())
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage", "screenX": 1, "screenY": 1}])
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicked.append(e.get("id")) or True)
    monkeypatch.setattr(ss, "_await_sort_done", lambda ctx, count: True)
    assert ss.sort_phase(_Ctx()) is True
    assert seen["action"] == "Sort-salvage"
    assert clicked == [59701]


def test_sort_stops_when_no_salvage_is_anywhere(monkeypatch):
    _install_hold(monkeypatch, _FakeHold(rows=()))
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: False)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    assert ss.sort_phase(ctx) is False
    assert ctx.state.data["hold_has_salvage"] is False


def test_sort_disposal_respects_the_keep_list(monkeypatch):
    _install_hold(monkeypatch, _FakeHold())
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: True)
    monkeypatch.setattr(sailing, "find_facility", lambda *a, **k: _station_obj())
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(ss, "_await_sort_done", lambda ctx, count: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage", "screenX": 1, "screenY": 1}])
    predicates = []
    monkeypatch.setattr(ss, "drop_items",
                        lambda ctx, pred: predicates.append(pred) or 0)
    ss.sort_phase(_Ctx())
    pred = predicates[0]
    assert pred("Gold ring") is True          # not on the default keep-list
    assert pred("Rune scimitar") is False     # on it
    assert pred("Chisel") is False            # hard-keep
    assert pred("Plundered salvage") is False # never dropped by disposal


def test_sort_reports_a_missing_station(monkeypatch):
    _install_hold(monkeypatch, _FakeHold())
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: True)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    monkeypatch.setattr(sailing, "find_facility", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage", "screenX": 1, "screenY": 1}])
    assert ss.sort_phase(_Ctx()) is False


# --- massWorld gating --------------------------------------------------------------------

def _explode(*a, **k):
    raise AssertionError("massWorld must never sail")


def test_mass_world_never_sails_to_a_far_wreck(monkeypatch):
    monkeypatch.setattr(sailing, "sail_to", _explode)
    monkeypatch.setattr(ss, "sail_to_pair", _explode)
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    # A far wreck exists and nothing is in hook range.
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3040, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ss.tick(ctx)   # must not raise


def test_mass_world_off_still_sails(monkeypatch):
    called = []
    monkeypatch.setattr(ss, "sail_to_pair", lambda ctx, cfg, w: called.append(True) or False)
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3040, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=False, sortWhenIdle=False, stowSalvage=False)
    ss.tick(ctx)
    assert called == [True]


def test_mass_world_does_not_reposition_when_the_hook_will_not_engage(monkeypatch):
    monkeypatch.setattr(sailing, "sail_to", _explode)
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": -1})
    deployed = []
    # skip=() default and a dict return -- see the note in
    # test_channel_without_sailing_xp_for_too_long_forces_a_reclick (Task 9 changed both).
    monkeypatch.setattr(sailing, "deploy_hook",
                        lambda ctx, name, skip=(): deployed.append(True) or {"x": 1, "y": 1, "plane": 0})
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    # Force the engage timeout to have already expired.
    ctx.state.data["hook_engage_attempt_at"] = 0.0
    ss.tick(ctx)   # must not raise
    assert deployed


def test_tick_dispatches_to_the_sort_phase_when_idle(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player())
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    called = []
    monkeypatch.setattr(ss, "sort_phase", lambda ctx: called.append(True) or True)
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=True)
    ctx.state.data["hold_has_salvage"] = True
    ss.tick(ctx)
    assert called == [True]


def test_tick_dispatches_to_the_stow_phase_when_the_inventory_fills(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player())
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Plundered salvage"}] * 27)
    called = []
    monkeypatch.setattr(ss, "stow_phase", lambda ctx: called.append(True) or True)
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ctx.state.data["hold_has_salvage"] = False
    ss.tick(ctx)
    assert called == [True]


# --- two hooks on one boat, one of them manned by the crewmate ----------------------------
# MEASURED live 2026-09-07: the user's boat carries TWO Adamant salvaging hooks (60505, 60506)
# and a crewmate mans one. Nothing in Python can SEE which -- /npcs returns 0 on a sailing world
# because NPCProvider reads only the top-level WorldView -- so the rotation is empirical.

def _hook(obj_id, x, y, view=1036):
    return {"id": obj_id, "name": "Adamant salvaging hook", "x": x, "y": y, "plane": 0,
            "visible": True, "screenX": obj_id, "screenY": obj_id,
            "actions": ["Deploy", "Modify"], "boatViewId": view}


def test_find_facilities_returns_every_matching_hook_nearest_first(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: dict(_boat_player(2680, 2546), boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _hook(60505, 2681, 2544),          # distance 2
        _hook(60506, 2681, 2546),          # distance 1
        _hook(60505, 2690, 2560, view=99),  # a neighbour's boat
    ])
    found = sailing.find_facilities("salvaging hook", action="Deploy")
    assert [h["id"] for h in found] == [60506, 60505]


def test_find_facility_still_returns_the_nearest_one(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: dict(_boat_player(2680, 2546), boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _hook(60505, 2681, 2544), _hook(60506, 2681, 2546)])
    assert sailing.find_facility("salvaging hook", action="Deploy")["id"] == 60506


def test_deploy_hook_skips_a_hook_on_the_skip_list(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: dict(_boat_player(2680, 2546), boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _hook(60505, 2681, 2544), _hook(60506, 2681, 2546)])
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicked.append(e["id"]) or True)
    nearest_key = (2681, 2546, 0)
    used = sailing.deploy_hook(_Ctx(), "salvaging hook", skip={nearest_key})
    assert clicked == [60505]
    assert used["id"] == 60505


def test_deploy_hook_returns_the_hook_it_clicked(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: dict(_boat_player(2680, 2546), boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_hook(60506, 2681, 2546)])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    used = sailing.deploy_hook(_Ctx(), "salvaging hook")
    assert used is not None and used["id"] == 60506


def test_deploy_hook_returns_all_skipped_when_every_hook_is_skipped(monkeypatch):
    """The caller must be able to tell 'all set aside' (ALL_SKIPPED) from 'a click missed'
    (None) -- it clears the skip list on the former, and must NOT on the latter (see the
    regression guard test below)."""
    monkeypatch.setattr(controller, "get_player",
                        lambda: dict(_boat_player(2680, 2546), boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_hook(60506, 2681, 2546)])
    result = sailing.deploy_hook(_Ctx(), "salvaging hook", skip={(2681, 2546, 0)})
    assert result is sailing.ALL_SKIPPED
    assert not result, "must stay falsy so an existing plain truthiness check is unaffected"


def test_deploy_hook_returns_none_not_all_skipped_when_a_click_misses(monkeypatch):
    """A hook WAS available (nothing to skip) but click_entity itself came back falsy -- an
    incidental miss (off-screen aim, a withheld click). This must be None, not ALL_SKIPPED, or
    the caller would wrongly clear its whole skip list over a single missed click."""
    monkeypatch.setattr(controller, "get_player",
                        lambda: dict(_boat_player(2680, 2546), boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_hook(60506, 2681, 2546)])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: False)
    result = sailing.deploy_hook(_Ctx(), "salvaging hook")
    assert result is None
    assert result is not sailing.ALL_SKIPPED


def test_deploy_hook_returns_none_when_nothing_matches(monkeypatch):
    """No hook at all (nothing skipped, nothing found) is also None, not ALL_SKIPPED."""
    monkeypatch.setattr(controller, "get_player",
                        lambda: dict(_boat_player(2680, 2546), boatViewId=1036))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    result = sailing.deploy_hook(_Ctx(), "salvaging hook")
    assert result is None
    assert result is not sailing.ALL_SKIPPED


def test_a_missed_click_does_not_wipe_the_skip_list(monkeypatch):
    """Regression guard for the Task 9 review finding: deploy_hook returning None for an
    incidental click miss must leave hook_skip alone -- clearing it here would discard the
    cooldown just placed on the crewmate's hook over nothing more than a missed click, defeating
    the whole rotation."""
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": -1})
    monkeypatch.setattr(sailing, "deploy_hook", lambda ctx, name, skip=(): None)
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ctx.state.data["hook_skip"] = {(9001, 9000, 0): 4102444800.0}  # far future (year 2100)
    ss.tick(ctx)
    assert (9001, 9000, 0) in ctx.state.data["hook_skip"]


def test_hook_skip_expires(monkeypatch):
    data = {"hook_skip": {(1, 2, 0): 100.0, (3, 4, 0): 200.0}}
    assert ss._hook_skip(data, 150.0) == {(3, 4, 0)}
    assert ss._hook_skip(data, 250.0) == set()


def test_hook_skip_is_empty_when_nothing_was_ever_set_aside():
    assert ss._hook_skip({}, 100.0) == set()


def test_the_engage_timeout_sets_the_failing_hook_aside(monkeypatch):
    """The whole point: a hook that will not engage is skipped so the OTHER one is tried next.
    Nothing can see which hook the crewmate is on, so this is decided by outcome."""
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": -1})
    monkeypatch.setattr(sailing, "deploy_hook",
                        lambda ctx, name, skip=(): _hook(60506, 3001, 3000))
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ctx.state.data["hook_engage_attempt_at"] = 0.0
    # In real (multi-tick) operation, last_hook_key is already populated by the FIRST tick's
    # deploy call, long before a 12s timeout can fire -- see the deploy_hook call at the end of
    # this same branch, which is what sets it. This single-tick test forces the timeout to fire
    # immediately, so it must seed the precondition that tick would otherwise have built up on
    # its own: the hook we've been retrying.
    ctx.state.data["last_hook_key"] = (3001, 3000, 0)
    ss.tick(ctx)
    assert (3001, 3000, 0) in ctx.state.data.get("hook_skip", {})


def test_a_deployed_hook_is_recorded_so_it_can_be_set_aside_later(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": -1})
    monkeypatch.setattr(sailing, "deploy_hook",
                        lambda ctx, name, skip=(): _hook(60505, 3002, 3000))
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ss.tick(ctx)
    assert ctx.state.data.get("last_hook_key") == (3002, 3000, 0)


# --- minor: ALL_SKIPPED never starts the reclick cooldown, a miss clears last_hook_key ------

def test_all_skipped_does_not_set_the_deploy_cooldown(monkeypatch):
    """No click was actually issued on the ALL_SKIPPED branch -- starting the cooldown there
    would just delay the very next retry after the skip list is cleared, for nothing."""
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": -1})
    monkeypatch.setattr(sailing, "deploy_hook", lambda ctx, name, skip=(): sailing.ALL_SKIPPED)
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ss.tick(ctx)
    assert "last_deploy_click_at" not in ctx.state.data


def test_a_missed_deploy_clears_last_hook_key(monkeypatch):
    """A miss (or nothing matching) must not leave a STALE last_hook_key from an earlier
    successful click sitting around for a later engage timeout to wrongly blame."""
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": -1})
    monkeypatch.setattr(sailing, "deploy_hook", lambda ctx, name, skip=(): None)
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ctx.state.data["last_hook_key"] = (9, 9, 0)   # stale, from an earlier successful click
    ss.tick(ctx)
    assert "last_hook_key" not in ctx.state.data
    assert "last_deploy_click_at" in ctx.state.data, "a miss still starts the reclick cooldown"


# --- I5: non-hook phases clear the hook trackers --------------------------------------------

def test_non_hook_phases_clear_the_hook_trackers(monkeypatch):
    """A stale engage timer must not survive a sort/stow gap and fire on the FIRST tick of a
    newly-risen wreck, setting aside a hook that was WORKING -- the sort/stow early returns in
    tick() used to bypass the near_wreck branch's own cleanup entirely."""
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=True)
    ctx.state.data.update({
        "hook_engage_attempt_at": 12345.0,
        "hook_engage_reposition_count": 2,
        "salvage_current_wreck_key": (1, 2, 0),
        "hold_has_salvage": True,
    })
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player())
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])   # nothing in hook range
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(ss, "sort_phase", lambda c: True)
    ss.tick(ctx)
    assert "hook_engage_attempt_at" not in ctx.state.data
    assert "hook_engage_reposition_count" not in ctx.state.data
    assert "salvage_current_wreck_key" not in ctx.state.data


def test_hook_phase_keeps_the_hook_trackers(monkeypatch):
    """The mirror case: while phase == "hook", the near_wreck branch owns these keys and I5
    must not interfere with its own management of them. hook_engage_attempt_at is seeded with a
    timestamp taken JUST before tick() so the branch's own HOOK_ENGAGE_TIMEOUT_S logic (which
    legitimately pops it once genuinely stale) does not fire and confound the assertion -- what
    is being tested is I5's unconditional pop, not the timeout."""
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=False)
    ctx.state.data["hook_engage_attempt_at"] = time.time()
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": -1})
    monkeypatch.setattr(sailing, "deploy_hook", lambda ctx, name, skip=(): None)
    ss.tick(ctx)
    assert "hook_engage_attempt_at" in ctx.state.data
    # salvage_current_wreck_key is unconditionally SET by the near_wreck branch -- the positive
    # signal that phase == "hook" actually took the "keep going" path, not the "clear and bail"
    # one I5 added.
    assert ctx.state.data.get("salvage_current_wreck_key") == (3000, 3000, 0)


# --- I3: a hold that never opens safety-stops rather than dropping forever -----------------

def test_hold_open_failure_drops_for_the_first_few_attempts(monkeypatch):
    fake = _FakeHold()
    fake.open_hold = lambda ctx: False
    _install_hold(monkeypatch, fake)
    dropped = []
    monkeypatch.setattr(ss, "drop_salvage", lambda ctx: dropped.append(True) or 1)
    ctx = _Ctx()
    stops = []
    ctx.state.trigger_safety_stop = lambda reason: stops.append(reason)
    for _ in range(ss.HOLD_OPEN_FAIL_LIMIT - 1):
        ss.stow_phase(ctx)
    assert len(dropped) == ss.HOLD_OPEN_FAIL_LIMIT - 1
    assert not stops, "must not stop before the limit is reached"


def test_hold_open_failure_reaching_the_limit_safety_stops(monkeypatch):
    fake = _FakeHold()
    fake.open_hold = lambda ctx: False
    _install_hold(monkeypatch, fake)
    dropped = []
    monkeypatch.setattr(ss, "drop_salvage", lambda ctx: dropped.append(True) or 1)
    ctx = _Ctx()
    stops = []
    ctx.state.trigger_safety_stop = lambda reason: stops.append(reason)
    for _ in range(ss.HOLD_OPEN_FAIL_LIMIT):
        ss.stow_phase(ctx)
    assert stops, "must safety-stop once the hold has failed to open HOLD_OPEN_FAIL_LIMIT times"
    assert len(dropped) == ss.HOLD_OPEN_FAIL_LIMIT - 1, "the limit tick itself must not drop -- stop instead"


def test_hold_open_success_resets_the_fail_counter(monkeypatch):
    fake = _FakeHold()
    calls = {"n": 0}

    def _open(ctx):
        calls["n"] += 1
        return calls["n"] >= 2   # fails once, then succeeds
    fake.open_hold = _open
    _install_hold(monkeypatch, fake)
    monkeypatch.setattr(ss, "drop_salvage", lambda ctx: 1)
    ctx = _Ctx()
    ctx.state.trigger_safety_stop = lambda reason: (_ for _ in ()).throw(
        AssertionError("must not stop -- the hold recovered before the limit"))
    ss.stow_phase(ctx)   # fails (1)
    ss.stow_phase(ctx)   # succeeds (2), resets the counter
    for _ in range(ss.HOLD_OPEN_FAIL_LIMIT):
        # open_hold's own fake always returns True from here on (calls["n"] stays >= 3), so this
        # loop is only here to prove no stale counter lingers and cause a stop of its own.
        ss.stow_phase(ctx)
    assert ctx.state.data.get("hold_open_fail_count") == 0


# --- C1: the sort rung parks after SORT_FAIL_LIMIT no-progress passes ----------------------

def test_derive_phase_sort_parked_skips_both_sort_clauses():
    """Both the full-hold clause and the idle-sort clause must be skipped while parked -- a
    wreck in range goes to hook, no wreck goes to wait, never sort."""
    assert ss.derive_phase(
        _cfg(),
        wreck_in_range=True, hold_full=True, inventory_salvage=0, free_slots=20,
        hold_has_salvage=True, sort_parked=True,
    ) == "hook"
    assert ss.derive_phase(
        _cfg(),
        wreck_in_range=False, hold_full=True, inventory_salvage=0, free_slots=20,
        hold_has_salvage=True, sort_parked=True,
    ) == "wait"


def test_derive_phase_sort_parked_still_allows_stow():
    """Parked only skips the SORT clauses -- a salvage-laden near-full inventory must still
    stow so the routine keeps earning."""
    assert ss.derive_phase(
        ss.DEFAULT_CONFIG,
        wreck_in_range=True, hold_full=True, inventory_salvage=27, free_slots=1,
        hold_has_salvage=True, sort_parked=True,
    ) == "stow"


def test_sort_parks_after_the_fail_limit_of_no_progress_passes(monkeypatch):
    """Every pass here achieves nothing at all: no salvage anywhere, disposal drops nothing."""
    _install_hold(monkeypatch, _FakeHold(rows=()))
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: False)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    for _ in range(ss.SORT_FAIL_LIMIT):
        ss.sort_phase(ctx)
    assert ctx.state.data.get("sort_parked_until", 0) > time.time()
    assert ctx.state.data.get("sort_fail_count") == 0, "resets once the park fires"


def test_progress_resets_the_fail_counter(monkeypatch):
    """A withdraw that DOES move salvage in counts as progress and must not let the counter
    creep toward the park threshold across an otherwise-unproductive run."""
    _install_hold(monkeypatch, _FakeHold(rows=()))
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    withdraw_results = iter([False, False, True, False, False])
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: next(withdraw_results))
    ctx = _Ctx()
    for _ in range(5):
        ss.sort_phase(ctx)
    # Three no-progress passes total (1, 2, 4/5 counted separately), never SORT_FAIL_LIMIT (4)
    # in a row -- the counter must never reach the park threshold.
    assert "sort_parked_until" not in ctx.state.data


def test_a_parked_sort_rung_falls_through_to_hook(monkeypatch):
    """End-to-end: once parked, tick() must dispatch to hook (a wreck in range) rather than
    calling sort_phase again."""
    ctx = _Ctx()
    ctx.state.config = _cfg(massWorld=True, sortWhenIdle=True)
    ctx.state.data["sort_parked_until"] = time.time() + 60
    ctx.state.data["hold_has_salvage"] = True
    monkeypatch.setattr(controller, "get_player", lambda: _boat_player(3000, 3000))
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_wreck(3000, 3000)])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"animation": -1})
    sort_calls = []
    monkeypatch.setattr(ss, "sort_phase", lambda c: sort_calls.append(True) or True)
    deploys = []
    monkeypatch.setattr(sailing, "deploy_hook",
                        lambda ctx, name, skip=(): deploys.append(True) or {"x": 1, "y": 1, "plane": 0})
    ss.tick(ctx)
    assert not sort_calls, "parked -- sort_phase must not be called"
    assert deploys, "must fall through to hooking instead"


# --- C2: keepers are stowed into the hold, HARD_KEEP is never selected, and a full inventory
#         of keepers the hold refuses safety-stops rather than looping ---------------------

def test_keeper_names_excludes_hard_keep_even_when_it_matches_keepitems():
    """A pathological keepItems entry that happens to overlap a HARD_KEEP word must still be
    excluded -- the exclusion is by HARD_KEEP membership, not by whether keepItems asked for it."""
    inv = [{"name": "Chisel"}, {"name": "Rune pouch"}, {"name": "Coins"},
           {"name": "Rune scimitar"}]
    names = ss._keeper_names(inv, ["chisel", "rune", "coins", "scimitar"])
    assert names == {"Rune scimitar"}


def test_stow_keepers_never_calls_deposit_item_with_a_hard_keep_name(monkeypatch):
    inv = [{"name": "Chisel"}, {"name": "Rune pouch"}, {"name": "Coins"},
           {"name": "Gem bag"}, {"name": "Plank sack"}, {"name": "Log basket"},
           {"name": "Rune scimitar"}]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(ss.cargo_hold, "open_hold", lambda ctx: True)
    monkeypatch.setattr(ss.cargo_hold, "close_hold", lambda ctx: True)
    deposited = []
    monkeypatch.setattr(ss.cargo_hold, "deposit_item",
                        lambda ctx, name: deposited.append(name) or True)
    ctx = _Ctx()
    freed = ss.stow_keepers(ctx)
    assert deposited == ["Rune scimitar"]
    for hard in ["Chisel", "Rune pouch", "Coins", "Gem bag", "Plank sack", "Log basket"]:
        assert hard not in deposited
    assert freed == 1


def test_stow_keepers_returns_zero_when_nothing_qualifies(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Chisel"}])

    def _fail_open(ctx):
        raise AssertionError("must not open the hold with nothing to deposit")
    monkeypatch.setattr(ss.cargo_hold, "open_hold", _fail_open)
    assert ss.stow_keepers(_Ctx()) == 0


def test_sort_phase_stows_keepers_when_free_slots_run_low(monkeypatch):
    """C2 step 2: sort_phase itself must call stow_keepers once free slots fall below
    KEEPER_STOW_FREE_SLOTS, before it tries to withdraw more salvage."""
    inv = ([{"name": "Rune scimitar", "screenX": i, "screenY": i}
            for i in range(controller.INVENTORY_SIZE - 2)]
           + [{"name": "Plundered salvage", "screenX": 900, "screenY": 900}])
    assert controller.INVENTORY_SIZE - len(inv) < ss.KEEPER_STOW_FREE_SLOTS
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    stow_calls = []
    monkeypatch.setattr(ss, "stow_keepers", lambda ctx: stow_calls.append(True) or 0)
    monkeypatch.setattr(sailing, "find_facility", lambda *a, **k: _station_obj())
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(ss, "_await_sort_done", lambda ctx, count: True)
    ss.sort_phase(_Ctx())
    assert stow_calls


def test_withdraw_is_skipped_with_zero_free_slots_and_hold_has_salvage_is_untouched(monkeypatch):
    """The free-slot guard (C2, mandatory): must not attempt a withdraw with no room, and must
    NOT record hold_has_salvage=False over it -- a full INVENTORY says nothing about the hold."""
    full_inv = [{"name": "Rune scimitar", "screenX": i, "screenY": i}
                for i in range(controller.INVENTORY_SIZE)]
    monkeypatch.setattr(controller, "get_inventory", lambda: full_inv)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    withdraw_calls = []
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage",
                        lambda ctx: withdraw_calls.append(True) or True)
    monkeypatch.setattr(ss.cargo_hold, "open_hold", lambda ctx: True)
    monkeypatch.setattr(ss.cargo_hold, "close_hold", lambda ctx: True)
    monkeypatch.setattr(ss.cargo_hold, "deposit_item", lambda ctx, name: False)  # can't free room
    ctx = _Ctx()
    ctx.state.data["hold_has_salvage"] = True   # a prior read said the hold DOES have salvage
    ctx.state.trigger_safety_stop = lambda reason: None
    ss.sort_phase(ctx)
    assert not withdraw_calls, "must not attempt a withdraw with zero free slots"
    assert ctx.state.data.get("hold_has_salvage") is True


def test_terminal_stop_fires_when_the_inventory_is_full_of_keepers_the_hold_wont_take(monkeypatch):
    inv = [{"name": "Rune scimitar", "screenX": i, "screenY": i}
           for i in range(controller.INVENTORY_SIZE)]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)  # every item is a keeper
    monkeypatch.setattr(ss.cargo_hold, "open_hold", lambda ctx: True)
    monkeypatch.setattr(ss.cargo_hold, "close_hold", lambda ctx: True)
    monkeypatch.setattr(ss.cargo_hold, "deposit_item", lambda ctx, name: False)
    ctx = _Ctx()
    stopped = []
    ctx.state.trigger_safety_stop = lambda reason: stopped.append(reason)
    ss.sort_phase(ctx)
    assert stopped, "a full inventory of keepers the hold refuses must safety-stop, not loop"
    assert "kept items" in stopped[0]


def test_no_terminal_stop_when_the_inventory_is_full_but_still_has_something_droppable(monkeypatch):
    """A full inventory alone is not the stuck condition -- only "full AND nothing left to
    sort/stow/drop" is."""
    inv = ([{"name": "Rune scimitar", "screenX": i, "screenY": i}
            for i in range(controller.INVENTORY_SIZE - 1)]
           + [{"name": "Gold ring", "screenX": 900, "screenY": 900}])   # droppable, not kept
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)   # disposal itself failed/no-oped
    monkeypatch.setattr(ss.cargo_hold, "open_hold", lambda ctx: True)
    monkeypatch.setattr(ss.cargo_hold, "close_hold", lambda ctx: True)
    monkeypatch.setattr(ss.cargo_hold, "deposit_item", lambda ctx, name: False)
    ctx = _Ctx()
    stopped = []
    ctx.state.trigger_safety_stop = lambda reason: stopped.append(reason)
    ss.sort_phase(ctx)
    assert not stopped


# --- I7: stow_phase's forced_full must not fold HOLD_UNKNOWN into "full" -------------------

def test_deposit_unknown_does_not_force_the_hold_full(monkeypatch):
    fake = _FakeHold(usage=(12, 160))
    _install_hold(monkeypatch, fake)
    monkeypatch.setattr(ss.cargo_hold, "deposit_salvage", lambda ctx: ss.cargo_hold.HOLD_UNKNOWN)
    ctx = _Ctx()
    ss.stow_phase(ctx)
    assert ctx.state.data["hold_full"] is False


# --- minor: a close_hold failure is logged, not silently swallowed -------------------------

def test_stow_phase_logs_when_the_hold_does_not_close(monkeypatch):
    fake = _FakeHold()
    fake.close_hold = lambda ctx: False
    _install_hold(monkeypatch, fake)
    logged = []
    monkeypatch.setattr(ss.debug_log, "log", lambda cat, msg: logged.append(msg))
    ss.stow_phase(_Ctx())
    assert any("did not close" in m for m in logged)


def test_stow_keepers_logs_when_the_hold_does_not_close(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Rune scimitar"}])
    monkeypatch.setattr(ss.cargo_hold, "open_hold", lambda ctx: True)
    monkeypatch.setattr(ss.cargo_hold, "deposit_item", lambda ctx, name: True)
    monkeypatch.setattr(ss.cargo_hold, "close_hold", lambda ctx: False)
    logged = []
    monkeypatch.setattr(ss.debug_log, "log", lambda cat, msg: logged.append(msg))
    ss.stow_keepers(_Ctx())
    assert any("did not close" in m for m in logged)


def test_sort_phase_logs_when_the_hold_does_not_close_after_withdrawing(monkeypatch):
    fake = _FakeHold()
    fake.close_hold = lambda ctx: False
    _install_hold(monkeypatch, fake)
    monkeypatch.setattr(ss.cargo_hold, "withdraw_salvage", lambda ctx: False)
    monkeypatch.setattr(ss, "drop_items", lambda ctx, pred: 0)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    logged = []
    monkeypatch.setattr(ss.debug_log, "log", lambda cat, msg: logged.append(msg))
    ss.sort_phase(_Ctx())
    assert any("did not close" in m for m in logged)
