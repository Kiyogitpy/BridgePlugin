import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import banker
import controller
import combat_core
slayer = combat_core   # the fixture and the 19 moved tests keep the short name


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


def test_combat_routine_is_a_thin_wrapper_over_combat_core():
    from routines import combat
    assert combat.tick is combat_core.fight_tick
    assert combat.on_event is combat_core.on_event
