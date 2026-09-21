import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import banker
import controller
import combat_core
slayer = combat_core   # the fixture and the 21 moved tests keep the short name


class _State:
    def __init__(self, config):
        self.config = config
        self.data = {}
        self.stops = []

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)


class _Ctx:
    def __init__(self, config):
        self.ser = None
        self.state = _State(config)


@pytest.fixture
def quiet_tick(monkeypatch):
    """Stub everything tick() touches except the thing under test, and record what ran."""
    ran = []
    monkeypatch.setattr(controller, "get_ammo_count", lambda: None)
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": True, "inCombat": False})
    monkeypatch.setattr(slayer, "maintain_defensive_prayer",
                        lambda ctx, combat: ran.append("defensive_prayer"))
    monkeypatch.setattr(slayer, "maintain_offensive_prayer",
                        lambda ctx, combat: ran.append("offensive_prayer"))
    monkeypatch.setattr(slayer, "maintain_prayer_potions", lambda ctx: ran.append("prayer_potions"))
    monkeypatch.setattr(slayer, "maintain_supplies", lambda ctx: ran.append("supplies"))
    monkeypatch.setattr(slayer, "maintain_bracelet", lambda ctx: ran.append("bracelet"))
    monkeypatch.setattr(slayer, "maybe_loot", lambda ctx: ran.append("loot"))
    monkeypatch.setattr(slayer, "maintain_safespot", lambda ctx: ran.append("safespot"))
    monkeypatch.setattr(slayer, "engage_target", lambda ctx, combat: ran.append("engage"))
    monkeypatch.setattr(banker, "return_to_spot",
                        lambda ctx: ran.append("return_to_spot") or True)
    return ran


def test_tick_spends_a_retry_on_the_walk_home_before_fighting_anything(quiet_tick):
    """USER-REPORTED 2026-09-07 ("ends up stuck at Ferox"). banker.bank_up walks home itself,
    but a walk that gives up partway now KEEPS its anchor rather than discarding it -- which is
    only worth anything if some later tick actually spends the retry. Without this the routine
    just fought on from wherever the walk stopped, which is the reported symptom."""
    ctx = _Ctx({"bank": {"enabled": False}, "loot": {"enabled": True}})
    ctx.state.data[banker.RETURN_LOCATION_KEY] = {"x": 3200, "y": 3200, "plane": 0}
    ctx.state.data["current_target_index"] = 41

    slayer.fight_tick(ctx)

    assert "return_to_spot" in quiet_tick
    # Food and prayer must not be starved by a return trip...
    assert "supplies" in quiet_tick and "defensive_prayer" in quiet_tick
    # ...but nothing commits us to a fight at the bank we're walking away from.
    assert "engage" not in quiet_tick and "safespot" not in quiet_tick
    assert ctx.state.data["current_target_index"] is None


def test_tick_is_unaffected_when_no_walk_home_is_owed(quiet_tick):
    """The common case: no anchor recorded, so the check costs nothing and the normal
    loot -> safespot -> engage ladder runs exactly as before."""
    ctx = _Ctx({"bank": {"enabled": False}, "loot": {"enabled": True}})

    slayer.fight_tick(ctx)

    assert "return_to_spot" not in quiet_tick
    assert quiet_tick == ["defensive_prayer", "offensive_prayer", "prayer_potions", "supplies", "bracelet",
                          "loot", "safespot", "engage"]


# --- pick_target: walking distance, not straight-line -------------------------------------------

def _worker(index, x, y, distance, **kw):
    npc = {"name": "Kalphite Worker", "id": 955, "index": index, "x": x, "y": y,
           "distance": distance, "attackable": True, "inCombat": False,
           "interactingPlayer": False}
    npc.update(kw)
    return npc


_TARGET_CFG = {"targetNames": ["Kalphite Worker"], "maxTargetDistance": 15}
_PLAYER = {"x": 100, "y": 100, "plane": 0, "inInstance": False}


@pytest.fixture
def walled_map(monkeypatch):
    """A wall along x=105 with one gap far to the north at y=150, so a worker just across the
    wall is a long walk round, the shape measured live in the Kalphite cave 2026-09-18."""
    def passable(x, y, plane, dx, dy):
        nx = x + dx
        crosses = (x < 105 <= nx) or (nx < 105 <= x)
        return not crosses or y == 150
    import worldmap
    monkeypatch.setattr(worldmap, "passable", passable)


def test_pick_target_prefers_the_short_walk_over_the_close_one_across_a_wall(walled_map):
    across_wall = _worker(1, 108, 100, distance=8)   # ~110 steps round via the gap
    same_side = _worker(2, 100, 118, distance=18)    # 17 steps, beyond maxTargetDistance
    assert slayer.pick_target([across_wall, same_side], _TARGET_CFG, _PLAYER)["index"] == 2


def test_pick_target_drops_everything_beyond_the_walk_budget(walled_map):
    assert slayer.pick_target([_worker(1, 108, 100, distance=8)], _TARGET_CFG, _PLAYER) is None


def test_pick_target_straight_line_without_a_player(walled_map):
    across_wall = _worker(1, 108, 100, distance=8)
    same_side = _worker(2, 100, 118, distance=18)
    assert slayer.pick_target([across_wall, same_side], _TARGET_CFG)["index"] == 1


def test_pick_target_straight_line_in_an_instance(walled_map):
    across_wall = _worker(1, 108, 100, distance=8)
    player = dict(_PLAYER, inInstance=True)
    assert slayer.pick_target([across_wall], _TARGET_CFG, player)["index"] == 1


def test_pick_target_straight_line_while_safespotting(walled_map):
    """Safespotting attacks from range, often across exactly this kind of obstacle."""
    across_wall = _worker(1, 108, 100, distance=8)
    cfg = dict(_TARGET_CFG, safespot={"enabled": True})
    assert slayer.pick_target([across_wall], cfg, _PLAYER)["index"] == 1


def test_pick_target_still_skips_someone_elses_fight(walled_map):
    busy = _worker(1, 100, 103, distance=3, inCombat=True)
    free = _worker(2, 100, 110, distance=10)
    assert slayer.pick_target([busy, free], _TARGET_CFG, _PLAYER)["index"] == 2


# --- maintain_bracelet ---------------------------------------------------------------------------

_EXPED = {"name": "Expeditious bracelet", "id": 21177, "slot": 0, "screenX": 10, "screenY": 10}
_SLAUGHTER = {"name": "Bracelet of slaughter", "id": 21183, "slot": 1, "screenX": 20, "screenY": 10}


@pytest.fixture
def bracelet_env(monkeypatch):
    env = {"gloves": {}, "inventory": [dict(_EXPED), dict(_SLAUGHTER)], "clicked": []}
    monkeypatch.setattr(controller, "get_equipment", lambda: {"gloves": env["gloves"]})
    monkeypatch.setattr(controller, "get_inventory", lambda: env["inventory"])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, item, **kw: env["clicked"].append((item["name"], kw.get("expected_action"))) or True)
    return env


def test_bracelet_crumble_message_is_recognised():
    assert slayer.is_bracelet_broken_message("Your bracelet of slaughter crumbles to dust.")
    # Both expeditious lines RECORDED live 2026-09-18.
    assert slayer.is_bracelet_broken_message(
        "Your expeditious bracelet helps you progress your slayer task faster. It then crumbles to dust.")
    assert not slayer.is_bracelet_broken_message(
        "Your expeditious bracelet helps you progress your slayer task faster. It has 1 charge left.")


def test_wears_a_spare_when_the_gloves_slot_is_empty(bracelet_env):
    ctx = _Ctx({})
    slayer.maintain_bracelet(ctx)
    assert bracelet_env["clicked"] == [("Expeditious bracelet", "Wear")]


def test_prefers_the_kind_that_just_broke(bracelet_env):
    ctx = _Ctx({})
    ctx.state.data["bracelet_worn_name"] = "bracelet of slaughter"
    slayer.maintain_bracelet(ctx)
    assert bracelet_env["clicked"] == [("Bracelet of slaughter", "Wear")]


def test_never_swaps_off_real_gloves(bracelet_env):
    bracelet_env["gloves"] = {"id": 7462, "name": "Barrows gloves"}
    slayer.maintain_bracelet(_Ctx({}))
    assert bracelet_env["clicked"] == []


def test_remembers_the_worn_bracelet_and_does_nothing(bracelet_env):
    bracelet_env["gloves"] = {"id": 21183, "name": "Bracelet of slaughter"}
    ctx = _Ctx({})
    slayer.maintain_bracelet(ctx)
    assert bracelet_env["clicked"] == []
    assert ctx.state.data["bracelet_worn_name"] == "bracelet of slaughter"


def test_no_spare_no_click(bracelet_env):
    bracelet_env["inventory"] = []
    slayer.maintain_bracelet(_Ctx({}))
    assert bracelet_env["clicked"] == []


def test_checks_are_throttled_until_a_crumble_message(bracelet_env):
    bracelet_env["gloves"] = {"id": 21177, "name": "Expeditious bracelet"}
    ctx = _Ctx({})
    slayer.maintain_bracelet(ctx)           # worn: schedules the next periodic check
    bracelet_env["gloves"] = {}             # it crumbles...
    slayer.maintain_bracelet(ctx)
    assert bracelet_env["clicked"] == []    # ...but the periodic check is not due yet
    slayer.on_event(ctx, "CHAT_MESSAGE", {"message": "Your expeditious bracelet crumbles to dust."})
    slayer.maintain_bracelet(ctx)
    assert bracelet_env["clicked"] == [("Expeditious bracelet", "Wear")]


def test_can_be_switched_off(bracelet_env):
    slayer.maintain_bracelet(_Ctx({"bracelets": {"reequip": False}}))
    assert bracelet_env["clicked"] == []


def test_spare_bracelets_are_never_banked():
    assert {"bracelet of slaughter", "expeditious bracelet"} <= slayer.protected_supply_names({})


# --- prayer potions: independent of the Use Prayer toggle ---------------------------------------

@pytest.fixture
def prayer_env(monkeypatch):
    env = {"player": {"prayer": 28, "prayerReal": 77, "hp": 78}, "drank": [], "consumables": {}}
    monkeypatch.setattr(controller, "get_player", lambda: env["player"])
    monkeypatch.setattr(controller, "get_consumables", lambda: env["consumables"])
    monkeypatch.setattr(controller, "consume_first",
                        lambda ser, cat: env["drank"].append(cat) or True)
    return env


def test_drinks_prayer_potion_with_use_prayer_off(prayer_env):
    """USER-REPORTED 2026-09-18, the live config: Use Prayer off, prayer potions on, 28/77."""
    ctx = _Ctx({"prayer": {"enabled": False, "restoreAt": 18}, "potions": {"prayerEnabled": True}})
    slayer.maintain_prayer_potions(ctx)
    # 28 sits inside the randomised window [18, 18 + spread] only for some rolls, so pin it.
    ctx.state.data["prayer_drink_threshold"] = 30
    slayer.maintain_prayer_potions(ctx)
    assert "prayer" in prayer_env["drank"]


def test_no_drink_above_threshold(prayer_env):
    prayer_env["player"]["prayer"] = 70
    ctx = _Ctx({"prayer": {"restoreAt": 18}, "potions": {"prayerEnabled": True}})
    ctx.state.data["prayer_drink_threshold"] = 30
    slayer.maintain_prayer_potions(ctx)
    assert prayer_env["drank"] == []


def test_no_drink_with_potions_off(prayer_env):
    ctx = _Ctx({"prayer": {"enabled": True, "restoreAt": 18}, "potions": {"prayerEnabled": False}})
    ctx.state.data["prayer_drink_threshold"] = 30
    slayer.maintain_prayer_potions(ctx)
    assert prayer_env["drank"] == []


def test_out_of_prayer_potions_banks_with_use_prayer_off(prayer_env):
    prayer_env["player"]["prayer"] = 10
    ctx = _Ctx({"prayer": {"enabled": False, "restoreAt": 18}, "potions": {"prayerEnabled": True}})
    assert slayer.out_of_critical_supplies(ctx)


def test_repeated_no_progress_bank_trips_end_in_a_safety_stop(monkeypatch):
    """BANK_NO_PROGRESS_MAX was used at the bank branch of the tick but never defined
    anywhere (found by the 2026-09-18 survey): two consecutive bank trips that did not
    restock raised NameError instead of stopping. Three no-progress trips must stop."""
    ctx = _Ctx({"bank": {"enabled": True}, "food": {"enabled": True, "eatAt": 40}})
    monkeypatch.setattr(controller, "get_ammo_count", lambda: None)
    monkeypatch.setattr(combat_core, "needs_banking", lambda ctx: True)
    monkeypatch.setattr(combat_core, "out_of_critical_supplies", lambda ctx: True)
    monkeypatch.setattr(banker, "bank_up", lambda ctx, cfg: True)

    for _ in range(combat_core.BANK_NO_PROGRESS_MAX):
        combat_core.fight_tick(ctx)

    assert ctx.state.stops, "no safety stop after repeated no-progress bank trips"
    assert "bank withdraw list" in ctx.state.stops[0]


def test_combat_config_substitutes_the_task_targets_without_touching_the_panel_config():
    base = {"targetNames": ["Cow"], "targets": [2805],
            "safespot": {"enabled": True, "marked": True, "x": 1, "y": 2, "plane": 0},
            "food": {"enabled": True}}
    cfg = combat_core.combat_config(base, ("Banshee", "Screaming banshee"))

    assert cfg["targetNames"] == ["Banshee", "Screaming banshee"]
    assert cfg["targets"] == [], "ids are only ever resolved for NPCs in the scene; names are the durable signal"
    assert cfg["safespot"]["enabled"] is False and cfg["safespot"]["marked"] is False
    assert cfg["safespot"]["x"] == 1, "other safespot fields survive"
    assert cfg["food"] == {"enabled": True}
    # The panel config the caller holds is untouched.
    assert base["targetNames"] == ["Cow"] and base["targets"] == [2805]
    assert base["safespot"]["enabled"] is True


def test_combat_config_targets_resolve_through_the_normal_singularizing_matcher():
    cfg = combat_core.combat_config({}, ("Banshees",))
    ids, names = combat_core.resolve_targets(cfg)
    assert ids == set()
    assert "banshee" in names and "banshees" in names


# --- two monsters, one name (FOUND LIVE 2026-09-21) -------------------------------------------
#
# USER, watching the cave bugs task: "Cave bugs that are significantly more powerful -- with a
# combat level of 96. Routine should skip these altogether and instead focus on the lower level
# ones." MEASURED in the game cache: 481 `swamp_cave_bug` is level 6 with 5 hitpoints, 483
# `molanisk_giant_cave_bug` is level 96 with 93 hitpoints and 84 defence -- both named exactly
# "Cave bug", both with an Attack option, both in the Dorgesh-Kaan South Dungeon. The session
# logs show the routine attacking both, and the strong one is nearly always the one it reached
# first because it is bigger and slower to kill.

CAVE_BUG_WEAK = {"id": 481, "name": "Cave bug", "attackable": True, "distance": 6}
CAVE_BUG_STRONG = {"id": 483, "name": "Cave bug", "attackable": True, "distance": 1}


def _unflooded(cfg):
    """pick_target without the walking flood, so these test the MATCH and not the pathfinder."""
    return dict(cfg, maxTargetWalkSteps=None)


def test_the_level_96_cave_bug_is_never_picked_even_when_it_is_closer():
    cfg = combat_core.combat_config({}, ("cave bugs",), task_npc_ids=(481,))
    picked = combat_core.pick_target([CAVE_BUG_STRONG, CAVE_BUG_WEAK], _unflooded(cfg))
    assert picked is CAVE_BUG_WEAK, "the name matches both; only the id tells them apart"


def test_without_the_allow_list_the_old_behaviour_stands():
    """Every Kuradal task, and combat.py, pass no ids -- they must be byte-for-byte unchanged."""
    cfg = combat_core.combat_config({}, ("cave bugs",))
    assert "onlyNpcIds" not in cfg, "absent, not empty: no caller should see a new key"
    picked = combat_core.pick_target([CAVE_BUG_STRONG, CAVE_BUG_WEAK], _unflooded(cfg))
    assert picked is CAVE_BUG_STRONG, "nearest wins, as it always did"


def test_the_allow_list_restricts_and_never_adds():
    """`targets` is additive; this one subtracts. A monster the NAMES never matched must not be
    dragged in by being on the id list."""
    cfg = combat_core.combat_config({}, ("cave bugs",), task_npc_ids=(481, 9999))
    other = {"id": 9999, "name": "Bloodveld", "attackable": True, "distance": 1}
    picked = combat_core.pick_target([other, CAVE_BUG_WEAK], _unflooded(cfg))
    assert picked is CAVE_BUG_WEAK


def test_an_allow_list_that_matches_nothing_present_finds_no_target():
    """Better no target -- which walks back, then stops with "nothing to fight" -- than the wrong
    one. The stop is safe; fighting a level 96 on a level 6 task is not."""
    cfg = combat_core.combat_config({}, ("cave bugs",), task_npc_ids=(481,))
    assert combat_core.pick_target([CAVE_BUG_STRONG], _unflooded(cfg)) is None


def test_combat_routine_is_a_thin_wrapper_over_combat_core():
    from routines import combat
    assert combat.tick is combat_core.fight_tick
    assert combat.on_event is combat_core.on_event


def test_combat_and_slayer_are_both_wired_into_routine_files_and_routine_choice():
    """Wiring a routine into ROUTINE_FILES AND RoutineChoice is a required step, not polish
    (CLAUDE.md). The split adds COMBAT and re-points SLAYER at the task orchestrator."""
    assert controller.ROUTINE_FILES["COMBAT"] == "routines/combat.py"
    assert controller.ROUTINE_FILES["SLAYER"] == "routines/slayer.py"
    java = open(os.path.join(os.path.dirname(__file__), "..", "..", "src", "main", "java", "com",
                             "example", "controller", "RoutineChoice.java"), encoding="utf-8").read()
    names = set(re.findall(r"^\s*([A-Z_0-9]+)\(\"", java, flags=re.M))
    assert "COMBAT" in names and "SLAYER" in names
    assert set(controller.ROUTINE_FILES) <= names, "every ROUTINE_FILES key must be a RoutineChoice"
    for rel in controller.ROUTINE_FILES.values():
        assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", rel)), rel


def test_the_slayer_routine_still_owns_its_dialogues():
    """The stub this used to test was replaced 2026-09-19 (tests/test_slayer_routine.py). The
    old test called tick() with nothing mocked, so it read the LIVE bridge when a client was up."""
    from routines import slayer
    assert slayer.SKIP_STRAY_DIALOG_CHECK is True


# --- 2026-09-21: the make-room hook (plan task D2) ----------------------------------------------
#
# ABSENT = today's behaviour, exactly. combat_core stays dumb: with a full inventory it asks the
# caller's callable whether to bother, and the caller owns every threshold and every click.

def _looting_world(monkeypatch, ground, inventory, loot_cfg):
    taken = []
    monkeypatch.setattr(controller, "get_ground_items", lambda: list(ground))
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory))
    monkeypatch.setattr(controller, "get_ground_item_at", lambda x, y, plane, item_id: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, item, action, **kw: taken.append(item.get("name")) or True)
    monkeypatch.setattr(controller, "ground_item_refresh_target", lambda *a, **k: None)
    ctx = _Ctx({"loot": loot_cfg})
    return ctx, taken


DROP = {"name": "Rune platebody", "id": 1127, "x": 3200, "y": 3200, "plane": 0, "qty": 1,
        "totalValue": 38000, "stackable": False, "clickable": True, "ownership": 0,
        "screenX": 100, "screenY": 100}
FULL = [{"name": f"Junk {i}", "id": 100 + i, "qty": 1} for i in range(28)]


def test_with_no_make_room_hook_a_full_inventory_behaves_exactly_as_before(monkeypatch):
    ctx, taken = _looting_world(monkeypatch, [DROP], FULL, {"minValue": 5000})
    combat_core.maybe_loot(ctx)
    assert taken == ["Rune platebody"], "unchanged: it still tries, as it always did"


def test_a_make_room_hook_that_says_no_leaves_the_drop(monkeypatch):
    asked = []
    ctx, taken = _looting_world(monkeypatch, [DROP], FULL,
                                {"minValue": 5000, "makeRoom": lambda item: asked.append(item["name"]) or False})
    combat_core.maybe_loot(ctx)
    assert taken == [] and asked == ["Rune platebody"]


def test_a_make_room_hook_that_says_yes_takes_it(monkeypatch):
    ctx, taken = _looting_world(monkeypatch, [DROP], FULL, {"minValue": 5000, "makeRoom": lambda item: True})
    combat_core.maybe_loot(ctx)
    assert taken == ["Rune platebody"]


def test_the_hook_is_not_consulted_when_there_is_room(monkeypatch):
    asked = []
    ctx, taken = _looting_world(monkeypatch, [DROP], FULL[:10],
                                {"minValue": 5000, "makeRoom": lambda item: asked.append(1) or False})
    combat_core.maybe_loot(ctx)
    assert taken == ["Rune platebody"] and asked == []


# --- 2026-09-21: a break is spent at the BANK, not next to the monster ---------------------------

def _break_world(monkeypatch, bank_enabled=True, banked_ok=True, spot=None, anchor=None):
    calls = {"bank": [], "saved": 0}
    monkeypatch.setattr(combat_core.travel, "save_location",
                        lambda: calls.__setitem__("saved", calls["saved"] + 1) or spot)
    monkeypatch.setattr(combat_core.banker, "bank_up",
                        lambda ctx, cfg: calls["bank"].append(cfg) or banked_ok)
    config = {"bank": {"enabled": bank_enabled, "keepItems": ["rune pouch"]},
              "food": {"enabled": True}, "potions": {"prayerEnabled": True}}
    ctx = _Ctx(config)
    ctx.state.data["current_target_index"] = 4412
    if anchor is not None:
        ctx.state.data[combat_core.banker.RETURN_LOCATION_KEY] = anchor
    return ctx, calls


SPOT = {"x": 2440, "y": 9790, "plane": 0}


def test_a_break_banks_first_and_stays_there_so_nothing_attacks_during_it(monkeypatch):
    """tick() is skipped for the WHOLE break (3-12 minutes), so nothing eats and nothing prays --
    standing among aggressive monsters for that long is a death. The bank is safe, and the trip
    also puts the loot away. returnToSpot is FALSE so the break is taken AT the bank; the anchor
    is put back afterwards, and combat_core's own per-tick return_to_spot walks home once the
    break ends."""
    ctx, calls = _break_world(monkeypatch, spot=SPOT)
    combat_core.bank_before_break(ctx)
    assert len(calls["bank"]) == 1
    cfg = calls["bank"][0]
    assert cfg["returnToSpot"] is False and cfg["enabled"] is True
    assert "rune pouch" in cfg["keepItems"], "the caller's own keep list survives"
    assert ctx.state.data.get("current_target_index") is None, "the fight is let go of first"
    assert ctx.state.data[combat_core.banker.RETURN_LOCATION_KEY] == SPOT, "the way home is remembered"
    assert ctx.state.data[combat_core.banker.RETURN_ATTEMPTS_KEY] == 0


def test_with_banking_switched_off_the_break_is_taken_where_we_stand(monkeypatch):
    """No bank trip is possible, so there is nothing safe to do -- exactly today's behaviour."""
    ctx, calls = _break_world(monkeypatch, bank_enabled=False, spot=SPOT)
    combat_core.bank_before_break(ctx)
    assert calls["bank"] == [] and calls["saved"] == 0


def test_a_break_during_a_return_trip_changes_nothing(monkeypatch):
    """Already walking home from a bank: we are not among the monsters, and a second trip would
    throw the anchor away."""
    ctx, calls = _break_world(monkeypatch, spot=SPOT, anchor={"x": 1, "y": 2, "plane": 0})
    combat_core.bank_before_break(ctx)
    assert calls["bank"] == []
    assert ctx.state.data[combat_core.banker.RETURN_LOCATION_KEY] == {"x": 1, "y": 2, "plane": 0}


def test_a_failed_bank_trip_leaves_no_anchor_to_walk_from(monkeypatch):
    """bank_up failed, so we are somewhere unknown -- inventing a way home from here would send
    the routine walking off after the break. It just fights on, as it does today."""
    ctx, calls = _break_world(monkeypatch, banked_ok=False, spot=SPOT)
    combat_core.bank_before_break(ctx)
    assert len(calls["bank"]) == 1
    assert combat_core.banker.RETURN_LOCATION_KEY not in ctx.state.data


def test_an_unrecorded_position_still_banks_but_promises_no_way_back(monkeypatch):
    ctx, calls = _break_world(monkeypatch, spot=None)
    combat_core.bank_before_break(ctx)
    assert len(calls["bank"]) == 1
    assert combat_core.banker.RETURN_LOCATION_KEY not in ctx.state.data


# --- 2026-09-21: holding a protection prayer on between kills ----------------------------------

def _prayer_ctx(monkeypatch, prayer_cfg, is_on):
    """maintain_defensive_prayer against fake prayer state. Returns (ctx, clicks)."""
    clicks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"prayerReal": 77, "prayer": 60})
    monkeypatch.setattr(controller, "get_prayers", lambda: {
        "active": {"PROTECT_FROM_MELEE": is_on},
        "icons": {"PROTECT_FROM_MELEE": {"screenX": 10, "screenY": 20}},
        "quickPrayerEnabled": False})
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: None)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, target, **kw: clicks.append(target))
    return _Ctx({"prayer": prayer_cfg}), clicks


def test_without_the_hold_the_prayer_still_flicks_off_with_no_target(monkeypatch):
    """combat.py's behaviour, unchanged: no key, no hold."""
    cfg = {"enabled": True, "defensivePrayer": "PROTECT_FROM_MELEE", "useQuickPrayers": False}
    ctx, clicks = _prayer_ctx(monkeypatch, cfg, is_on=True)

    combat_core.maintain_defensive_prayer(ctx, {"inCombat": False})

    assert len(clicks) == 1, "it turns the prayer off, exactly as before"


def test_holdDefensive_keeps_the_prayer_on_between_kills(monkeypatch):
    """USER 2026-09-21: "Leave the protection on for the duration of the task. since its a very
    short time between fights anyway." MEASURED in the run that prompted it: off at 07:16:23, the
    next gargoyle engaged at :24, back on at :26 -- 2.5 s unprotected at melee range every kill."""
    cfg = {"enabled": True, "defensivePrayer": "PROTECT_FROM_MELEE", "useQuickPrayers": False,
           "holdDefensive": True}
    ctx, clicks = _prayer_ctx(monkeypatch, cfg, is_on=True)

    combat_core.maintain_defensive_prayer(ctx, {"inCombat": False})

    assert clicks == [], "already on and staying on: nothing to click"


def test_holdDefensive_still_turns_the_prayer_ON_when_it_is_off(monkeypatch):
    """Holding is not "never touch it" -- an unprotected trip must still get the prayer up."""
    cfg = {"enabled": True, "defensivePrayer": "PROTECT_FROM_MELEE", "useQuickPrayers": False,
           "holdDefensive": True}
    ctx, clicks = _prayer_ctx(monkeypatch, cfg, is_on=False)

    combat_core.maintain_defensive_prayer(ctx, {"inCombat": False})

    assert clicks == [{"screenX": 10, "screenY": 20}]


# --- 2026-09-21 LIVE: a confirmed fight that ends without a death used to hang forever ----------

def _engaged(monkeypatch, combat, committed_s_ago, confirmed=True):
    """engage_target already committed to index 7, which is still in the scene and alive."""
    npcs = [{"name": "Gargoyle", "id": 1543, "index": 7, "x": 3432, "y": 9938, "plane": 3,
             "hpRatio": -1, "distance": 3},
            {"name": "Gargoyle", "id": 1543, "index": 8, "x": 3433, "y": 9939, "plane": 3,
             "hpRatio": -1, "distance": 3}]
    monkeypatch.setattr(controller, "get_npcs", lambda *a, **k: npcs)
    picked = []
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3433, "y": 9935, "plane": 3, "hp": 58, "hpReal": 93})
    monkeypatch.setattr(combat_core, "_pick_target", lambda *a, **k: picked.append(1) or None,
                        raising=False)
    ctx = _Ctx({"targetNames": ["gargoyles"], "maxTargetDistance": 15})
    ctx.state.data.update({"current_target_index": 7,
                           "engage_committed_at": time.time() - committed_s_ago,
                           "engage_ever_confirmed": confirmed})
    return ctx


def test_a_confirmed_fight_that_goes_completely_dead_releases_its_target(monkeypatch):
    """FOUND LIVE 2026-09-21 on gargoyles. USER: "why are we idle?" Last attack 07:31:03 on index
    29328; four minutes later the log held only idle drift, an eat and three prayer sips, while
    /npcs still listed 29328 three tiles away and ELEVEN gargoyles sat untouched within 13 tiles.

    engage_ever_confirmed latched True, and past ENGAGE_STUCK_WINDOW_S the code returned with no
    deadline at all for as long as the NPC existed -- so a fight ending WITHOUT the monster dying
    never released the commitment.
    """
    dead = {"inCombat": False, "isMoving": False, "animation": -1}
    ctx = _engaged(monkeypatch, dead, committed_s_ago=60)
    # First pass starts the idle clock; wind it back past the give-up threshold.
    combat_core.engage_target(ctx, dead)
    assert ctx.state.data["current_target_index"] == 7, "not released on the very first dead tick"
    ctx.state.data["engage_idle_since"] = time.time() - combat_core.ENGAGE_IDLE_GIVE_UP_S - 1

    combat_core.engage_target(ctx, dead)

    assert ctx.state.data["current_target_index"] is None, "the commitment is released"


def test_a_mid_fight_sip_does_not_release_the_target(monkeypatch):
    """The reason the stuck check was switched off past the window in the first place (2026-08-06):
    inCombat misreads for a tick or two during an eat or drink. Nowhere near the give-up clock,
    and the swing animation alone keeps the commitment anyway."""
    sipping = {"inCombat": False, "isMoving": False, "animation": 829}
    ctx = _engaged(monkeypatch, sipping, committed_s_ago=60)
    ctx.state.data["engage_idle_since"] = time.time() - combat_core.ENGAGE_IDLE_GIVE_UP_S - 1

    combat_core.engage_target(ctx, sipping)

    assert ctx.state.data["current_target_index"] == 7
    assert not ctx.state.data["engage_idle_since"], "any sign of life resets the clock"


def test_walking_to_a_distant_target_keeps_the_commitment(monkeypatch):
    moving = {"inCombat": False, "isMoving": True, "animation": -1}
    ctx = _engaged(monkeypatch, moving, committed_s_ago=60)
    ctx.state.data["engage_idle_since"] = time.time() - combat_core.ENGAGE_IDLE_GIVE_UP_S - 1

    combat_core.engage_target(ctx, moving)

    assert ctx.state.data["current_target_index"] == 7
