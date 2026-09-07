"""Hermetic tests for routines/shipwreck_salvaging.py and sailing.py (added 2026-09-03).

Every bridge read is stubbed -- nothing here touches the running client or the serial port.
The pinned facts (animation ids, object ids, chat text) come from the offline OSRS corpus:
runelite-api 1.12.38's AnimationID, the game-cache loc dump (shipwreck/hook ids), and the
Shipwreck salvaging wiki page (revision 15290733).
"""

import os
import sys

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
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: _inventory(3) + [{"name": "Chisel", "screenX": 1, "screenY": 1}])
    seen = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **kw: seen.append((ent["name"], kw)) or True)
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
    monkeypatch.setattr(sailing, "deploy_hook", lambda c, n: deploys.append(n) or True)
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
    monkeypatch.setattr(sailing, "deploy_hook", lambda c, n: deploys.append(n) or True)
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
    inv = [{"name": "Gold ring", "screenX": 1, "screenY": 1},
           {"name": "Chisel", "screenX": 2, "screenY": 2},
           {"name": "Rune scimitar", "screenX": 3, "screenY": 3}]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, item, **kw: clicked.append(item["name"]) or True)
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
    monkeypatch.setattr(sailing, "deploy_hook",
                        lambda ctx, name: deployed.append(True) or True)
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
