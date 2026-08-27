import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import time

import pytest

import mahogany_homes as mh
import controller
import banker
import travel


def _hotspot(varb, object_id, value, needs_attention, material="PLANK", quantity=2):
    # material/quantity mirror MahoganyHomesProvider's per-slot HotspotType fields, which
    # work_phase reads to price the ONE hotspot it is about to click.
    return {"varb": varb, "objectId": object_id, "value": value,
            "needsAttention": needs_attention, "material": material, "quantity": quantity}


def _object(obj_id, distance=1, x=0, y=0, plane=0):
    return {"id": obj_id, "distance": distance, "x": x, "y": y, "plane": plane,
            "screenX": 100, "screenY": 100}


def _item(name, quantity=1):
    # "qty" is what GET /inventory actually emits (InventoryProvider.java) -- NOT "quantity".
    return {"name": name, "qty": quantity}


@pytest.fixture(autouse=True)
def _stub_widgets_dump(monkeypatch):
    """tick() now calls _accept_tea_if_offered unconditionally, which reads /widgets/dump. Stub
    it out by default so no test accidentally makes a real (bridge-less) HTTP call; tests that
    care about the tea dialogue re-patch it themselves."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [])


# --- tier_number ---------------------------------------------------------------------

def test_tier_number_recognizes_all_four_tiers():
    assert mh.tier_number("beginner") == 1
    assert mh.tier_number("Novice") == 2
    assert mh.tier_number("ADEPT") == 3
    assert mh.tier_number("expert") == 4


def test_tier_number_returns_zero_for_unknown():
    assert mh.tier_number("bogus") == 0


# --- pick_next_hotspot ----------------------------------------------------------------

def test_pick_next_hotspot_returns_first_needing_attention():
    hotspots = [
        _hotspot(10554, 39989, 0, False),
        _hotspot(10555, 39990, 3, True),
        _hotspot(10556, 39991, 3, True),
    ]
    assert mh.pick_next_hotspot(hotspots)["objectId"] == 39990


def test_pick_next_hotspot_returns_none_when_all_done():
    hotspots = [_hotspot(10554, 39989, 0, False), _hotspot(10555, 39990, 0, False)]
    assert mh.pick_next_hotspot(hotspots) is None


def test_pick_next_hotspot_returns_none_for_empty_list():
    assert mh.pick_next_hotspot([]) is None


# --- find_object_by_id ------------------------------------------------------------------

def test_find_object_by_id_matches_exact_id():
    objects = [_object(1111, distance=5), _object(39990, distance=3)]
    found = mh.find_object_by_id(objects, 39990)
    assert found is not None and found["id"] == 39990


def test_find_object_by_id_picks_nearest_when_duplicated():
    objects = [_object(39990, distance=9), _object(39990, distance=2)]
    assert mh.find_object_by_id(objects, 39990)["distance"] == 2


def test_find_object_by_id_returns_none_when_absent():
    assert mh.find_object_by_id([_object(1111)], 39990) is None


# --- deficit --------------------------------------------------------------------------

def test_deficit_positive_when_short():
    assert mh.deficit(5, 10) == 5


def test_deficit_zero_when_sufficient():
    assert mh.deficit(10, 5) == 0


def test_deficit_zero_when_exact():
    assert mh.deficit(5, 5) == 0


# --- count_item -------------------------------------------------------------------------

def test_count_item_sums_matching_quantities_case_insensitively():
    inventory = [_item("Teak plank", 1), _item("teak plank", 1), _item("Steel bar", 5)]
    assert mh.count_item(inventory, "Teak plank") == 2


def test_count_item_uses_qty_field_when_present():
    # GET /inventory emits stack sizes under "qty" (InventoryProvider.java), not "quantity" --
    # reading the wrong key silently reported 1 of any stack.
    inventory = [{"name": "Astral rune", "qty": 300}]
    assert mh.count_item(inventory, "Astral rune") == 300


def test_count_item_ignores_a_quantity_key_the_bridge_never_sends():
    # Defensive: a dict carrying only "quantity" is not something /inventory produces, and must
    # not be silently trusted as a stack size.
    assert mh.count_item([{"name": "Astral rune", "quantity": 300}], "Astral rune") == 1


def test_count_item_returns_zero_when_absent():
    assert mh.count_item([_item("Hammer")], "Teak plank") == 0


# --- rune_pouch_counts ------------------------------------------------------------------

def test_rune_pouch_counts_builds_lowercase_name_map():
    pouch = {"runes": [{"slot": 0, "type": 1, "quantity": 500, "name": "Astral rune"},
                        {"slot": 1, "type": 2, "quantity": 300, "name": "Cosmic rune"}]}
    counts = mh.rune_pouch_counts(pouch)
    assert counts == {"astral rune": 500, "cosmic rune": 300}


def test_rune_pouch_counts_skips_unresolved_slots():
    pouch = {"runes": [{"slot": 0, "type": 0, "quantity": 0}]}
    assert mh.rune_pouch_counts(pouch) == {}


def test_rune_pouch_counts_handles_empty_pouch():
    assert mh.rune_pouch_counts({"runes": []}) == {}


# --- needs_bank_trip --------------------------------------------------------------------

class _State:
    def __init__(self):
        self.data = {}
        self.config = mh.DEFAULT_CONFIG
        self.safety_stopped = False
        self.safety_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = _State()


def test_needs_bank_trip_true_when_planks_low(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 2)])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    ctx = _Ctx()
    state = {"hasContract": False}
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, state) is True


def test_needs_bank_trip_true_when_a_required_rune_low(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], mh.DEFAULT_CONFIG["restock"]["targetPlanks"]),
        _item("Steel bar", mh.DEFAULT_CONFIG["restock"]["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8),
        _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 5},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    ctx = _Ctx()
    state = {"hasContract": False}
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, state) is True


def test_needs_bank_trip_false_when_everything_sufficient(monkeypatch):
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8),
        _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    ctx = _Ctx()
    state = {"hasContract": False}
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, state) is False


def test_needs_bank_trip_counts_loose_runes_not_just_the_pouch(monkeypatch):
    # bank_phase withdraws LOOSE runes (it builds no pouch-filling machinery), so a pouch-only
    # check would force a bank trip on a player already carrying hundreds in hand.
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 3), _item("Ardougne teleport", 3), _item("Falador teleport", 3),
        _item("Astral rune", 500), _item("Cosmic rune", 500),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})

    ctx = _Ctx()
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, {"hasContract": False}) is False


def test_available_runes_sums_inventory_and_pouch():
    inventory = [_item("Astral rune", 120)]
    pouch = {"astral rune": 80}
    assert mh.available_runes(inventory, pouch, "Astral rune") == 200


# --- bank_phase -----------------------------------------------------------------------

def test_bank_phase_breaks_home_teleport_when_no_bank_and_no_teleport_leaves_safety_stopped(monkeypatch):
    monkeypatch.setattr(banker, "find_nearby_bank", lambda config: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})
    # No bank in range and no home teleport tablet in inventory -- genuine dead end.
    assert ctx.state.safety_stopped is True


def _mock_bank(monkeypatch, inventory=None, pouch=None, restocked=True):
    """Common bank_phase harness: an open bank, recording stubs for every banker call, and
    control over whether the post-trip needs_bank_trip re-check reports success."""
    record = {"withdrawn": [], "shift_withdrawn": [], "deposit": None}

    monkeypatch.setattr(banker, "find_nearby_bank", lambda config: {"id": 1, "name": "Bank booth"})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, bank_obj: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx: None)
    monkeypatch.setattr(banker, "deposit_inventory",
                        lambda ctx, config: record.__setitem__("deposit", config))
    monkeypatch.setattr(banker, "withdraw_loadout",
                        lambda ctx, config, fresh=False: record["withdrawn"].append(config["withdraw"][0]))
    monkeypatch.setattr(banker, "withdraw_all_shift",
                        lambda ctx, name, fresh=False: record["shift_withdrawn"].append(name))
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory or []))
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: pouch or {"runes": []})
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(mh, "needs_bank_trip", lambda ctx, config, state: not restocked)
    return record


def test_bank_phase_withdraws_up_to_target_and_advances_phase(monkeypatch):
    record = _mock_bank(monkeypatch)

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    names = {w["name"] for w in record["withdrawn"]}
    assert mh.DEFAULT_CONFIG["plankName"] in names
    assert "Steel bar" in names
    assert ctx.state.data["mh_phase"] == "travel"


def test_bank_phase_deposits_before_withdrawing_and_keeps_the_loadout(monkeypatch):
    record = _mock_bank(monkeypatch)

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert record["deposit"] is not None, "bank_phase must deposit before restocking"
    keep = [k.lower() for k in record["deposit"]["keepItems"]]
    for required in ["hammer", "saw", "rune pouch", "xeric's talisman",
                     "varrock teleport", "steel bar",
                     mh.DEFAULT_CONFIG["plankName"].lower(), "astral rune"]:
        assert required in keep, f"{required} must survive the deposit"
    # The rune pouch is a keepItem precisely because we want its contents -- the generic
    # "empty every container into the bank" step must not run and undo that.
    assert record["deposit"]["skipContainerDeposit"] is True


def test_bank_phase_shift_withdraws_stacking_runes(monkeypatch):
    # Runes must NOT go through withdraw_loadout's per-unit click loop -- 300 astral runes would
    # be 300 clicks. One shift-click takes the whole stack into a single slot.
    record = _mock_bank(monkeypatch)

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert set(record["shift_withdrawn"]) == {"astral rune", "cosmic rune"}
    assert not any(w["name"] in ("astral rune", "cosmic rune") for w in record["withdrawn"])


def test_bank_phase_skips_runes_already_covered_by_the_pouch(monkeypatch):
    record = _mock_bank(monkeypatch, pouch={"runes": [
        {"name": "Astral rune", "quantity": 1000},
        {"name": "Cosmic rune", "quantity": 1000},
    ]})

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert record["shift_withdrawn"] == []


def test_bank_phase_counts_a_failed_restock_and_stays_in_bank(monkeypatch):
    _mock_bank(monkeypatch, restocked=False)

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert ctx.state.data["mh_bank_stuck"] == 1
    assert ctx.state.data["mh_phase"] == "bank"   # must NOT advance on a failed restock
    assert ctx.state.safety_stopped is False


def test_bank_phase_safety_stops_after_repeated_failed_restocks(monkeypatch):
    _mock_bank(monkeypatch, restocked=False)

    ctx = _Ctx()
    ctx.state.data["mh_bank_stuck"] = mh.STUCK_TICK_LIMIT - 1
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert ctx.state.safety_stopped is True
    assert "bank" in ctx.state.safety_reason.lower()


def test_bank_phase_releases_the_teleported_latch_so_travel_can_teleport_back(monkeypatch):
    # A bank trip ends somewhere else entirely, so travel_phase must be allowed to teleport
    # again -- otherwise it would try to WALK from the bank back to the house.
    _mock_bank(monkeypatch)

    ctx = _Ctx()
    ctx.state.data["mh_teleported_this_contract"] = True
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert ctx.state.data["mh_teleported_this_contract"] is False
    assert ctx.state.data["mh_phase"] == "travel"


def test_bank_phase_resets_the_stuck_counter_on_a_good_trip(monkeypatch):
    _mock_bank(monkeypatch)

    ctx = _Ctx()
    ctx.state.data["mh_bank_stuck"] = 7
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert ctx.state.data["mh_bank_stuck"] == 0


# --- request_contract_phase -----------------------------------------------------------

def test_request_contract_phase_skips_when_already_has_contract():
    ctx = _Ctx()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})
    assert "mh_contact_cast_at" not in ctx.state.data


def test_request_contract_phase_casts_npc_contact_when_no_contract(monkeypatch):
    clicked = {}

    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget",
                         lambda group, child, index=None: {"clickable": True, "screenX": 1, "screenY": 2})

    def fake_click(ser, entity, **kw):
        clicked["entity"] = entity
        return True

    monkeypatch.setattr(controller, "click_entity", fake_click)

    ctx = _Ctx()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert clicked["entity"] == {"clickable": True, "screenX": 1, "screenY": 2}
    assert "mh_contact_cast_at" in ctx.state.data


def test_request_contract_phase_respects_cooldown(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: calls.append(key))

    ctx = _Ctx()
    ctx.state.data["mh_contact_cast_at"] = time.time()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert calls == []  # still on cooldown -- must not re-cast


def test_request_contract_phase_counts_an_unclickable_widget(monkeypatch):
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": False})

    ctx = _Ctx()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert ctx.state.data["mh_contact_stuck"] == 1
    assert ctx.state.safety_stopped is False


def test_request_contract_phase_safety_stops_when_widget_stays_unclickable(monkeypatch):
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget", lambda group, child, index=None: None)

    ctx = _Ctx()
    ctx.state.data["mh_contact_stuck"] = mh.STUCK_TICK_LIMIT - 1
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert ctx.state.safety_stopped is True
    assert "npc contact" in ctx.state.safety_reason.lower()


def test_request_contract_phase_resets_stuck_counter_once_clickable(monkeypatch):
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": True, "screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_contact_stuck"] = 9
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert ctx.state.data["mh_contact_stuck"] == 0


# --- travel_phase ---------------------------------------------------------------

def test_travel_phase_teleports_then_walks(monkeypatch):
    clicked = {}

    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Varrock teleport", 8)])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    def fake_click(ser, entity, **kw):
        clicked["entity"] = entity

    monkeypatch.setattr(controller, "click_entity", fake_click)
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)

    ctx = _Ctx()
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert clicked["entity"]["name"] == "Varrock teleport"
    assert ctx.state.data["mh_teleported_this_contract"] is True


def test_travel_phase_walks_to_standing_tile_after_teleport_and_advances_to_work(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3448, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_last_home"] = "JEFF"
    ctx.state.data["mh_teleported_this_contract"] = True
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data["mh_phase"] == "work"


def test_travel_phase_banks_when_teleport_item_missing(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])

    ctx = _Ctx()
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data["mh_phase"] == "bank"


def test_travel_phase_resets_teleport_flag_on_new_home(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Falador teleport", 8)])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: None)

    ctx = _Ctx()
    ctx.state.data["mh_last_home"] = "JEFF"
    ctx.state.data["mh_teleported_this_contract"] = True
    state = {"homeName": "TAU", "standingLocation": {"x": 3047, "y": 3345, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    # New home -> the flag must have been reset before being set again by this same call.
    assert ctx.state.data["mh_last_home"] == "TAU"
    assert ctx.state.data["mh_teleported_this_contract"] is True


def test_travel_phase_uses_the_menu_for_xerics_talisman(monkeypatch):
    # The talisman's LEFT-CLICK default is not the destination we want -- "Xeric's Glade" has to
    # be picked by name off the right-click menu. Tablets ("Break") are fine bare-clicked.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Xeric's talisman")])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    bare_clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: bare_clicks.append(entity))
    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(entity=entity, option=option))

    ctx = _Ctx()
    state = {"homeName": "LEELA", "standingLocation": {"x": 1785, "y": 3592, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert menu["option"] == "Xeric's Glade"
    assert menu["entity"]["name"] == "Xeric's talisman"
    assert bare_clicks == []


def test_city_teleports_are_name_option_pairs():
    for home, entry in mh.CITY_TELEPORTS.items():
        assert isinstance(entry, tuple) and len(entry) == 2, home
        name, option = entry
        assert isinstance(name, str) and name
        # Only the talisman needs a menu option; every tablet's default action already teleports.
        assert option == ("Xeric's Glade" if name == "xeric's talisman" else None), home


def _work_state(hotspots, required=None, **extra):
    state = {
        "hotspots": hotspots,
        "requiredMaterials": required or {"planks": 3, "steelBars": 0},
        "standingLocation": {"x": 3239, "y": 3450, "plane": 0},
        "homeName": "JEFF",
        "ladderObjectIds": [11789, 11793],
    }
    state.update(extra)
    return state


def test_work_phase_advances_to_turn_in_when_nothing_left():
    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 0, False)],
                                                      {"planks": 0, "steelBars": 0}))
    assert ctx.state.data["mh_phase"] == "turn_in"


def test_work_phase_banks_when_the_current_hotspot_is_unaffordable(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 1)])
    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([_hotspot(10554, 39989, 3, True, quantity=3)]))
    assert ctx.state.data["mh_phase"] == "bank"


def test_work_phase_works_a_hotspot_it_can_afford_even_when_the_contract_total_cannot(monkeypatch):
    # THE C3 REGRESSION: comparing against requiredMaterials (the whole remaining contract) sent
    # us to the bank without touching any of the hotspots we could already pay for.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 3)])
    monkeypatch.setattr(controller, "get_objects", lambda: [_object(39989, distance=2)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.setdefault("entity", entity))

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([_hotspot(10554, 39989, 3, True, quantity=2)],
                              required={"planks": 14, "steelBars": 0}))

    assert clicked["entity"]["id"] == 39989
    assert ctx.state.data.get("mh_phase") != "bank"


def test_work_phase_prices_a_steel_bar_hotspot_against_bars_not_planks(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20)])   # planks aplenty, zero bars
    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([_hotspot(10554, 39989, 3, True, material="STEEL_BAR", quantity=1)]))
    assert ctx.state.data["mh_phase"] == "bank"


def test_work_phase_falls_back_to_the_contract_total_when_per_hotspot_cost_is_missing(monkeypatch):
    # material/quantity only exist on a REBUILT client. Defaulting a missing quantity to 0 would
    # read as "this hotspot is free" -- fail-OPEN, clicking hotspots we cannot pay for. An absent
    # field must fall back to the old (merely conservative) whole-contract check instead.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 2)])
    stale = _hotspot(10554, 39989, 3, True)
    del stale["material"], stale["quantity"]

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([stale], required={"planks": 9, "steelBars": 0}))
    assert ctx.state.data["mh_phase"] == "bank"


def test_work_phase_fallback_still_works_when_the_contract_total_is_affordable(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 20)])
    monkeypatch.setattr(controller, "get_objects", lambda: [_object(39989, distance=2)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.setdefault("entity", entity))
    stale = _hotspot(10554, 39989, 3, True)
    del stale["material"], stale["quantity"]

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([stale], required={"planks": 9, "steelBars": 0}))
    assert clicked["entity"]["id"] == 39989


def test_work_phase_walks_closer_when_object_not_yet_visible(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda: [])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3100, "y": 3300, "plane": 0})
    walked = {}
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.setdefault("target", target))

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
    assert walked["target"] == {"x": 3239, "y": 3450, "plane": 0}
    assert ctx.state.data["mh_work_stuck"] == 1


def test_work_phase_climbs_a_ladder_when_at_the_house_but_the_hotspot_is_missing(monkeypatch):
    # /objects only reports the player's CURRENT plane, and most homes put hotspots upstairs
    # while standingLocation is always plane 0 -- "not found" here means "wrong floor".
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda: [_object(11789, distance=3)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: pytest.fail("must not walk"))

    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(entity=entity, option=option))

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))

    assert menu["entity"]["id"] == 11789
    assert list(menu["option"]) == ["Climb-up", "Climb-down"]
    assert "mh_last_ladder_click" in ctx.state.data


def test_work_phase_does_not_re_climb_within_the_ladder_cooldown(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda: [_object(11789, distance=3)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 0})
    clicks = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: clicks.append(option))

    ctx = _Ctx()
    ctx.state.data["mh_last_ladder_click"] = time.time()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
    assert clicks == []


def test_work_phase_safety_stops_when_a_hotspot_stays_unreachable(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda: [])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_work_stuck"] = mh.STUCK_TICK_LIMIT - 1
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))

    assert ctx.state.safety_stopped is True
    assert "39989" in ctx.state.safety_reason


def test_work_phase_clicks_the_hotspot_object(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda: [_object(39989, distance=2)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))

    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: clicked.setdefault("entity", entity))

    ctx = _Ctx()
    ctx.state.data["mh_work_stuck"] = 6
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
    assert clicked["entity"]["id"] == 39989
    assert "mh_last_hotspot_click" in ctx.state.data
    assert ctx.state.data["mh_work_stuck"] == 0     # progress resets the stuck counter


def test_work_phase_respects_click_cooldown(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda: [_object(39989, distance=2)])

    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: clicked.append(entity))

    ctx = _Ctx()
    ctx.state.data["mh_last_hotspot_click"] = time.time()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
    assert clicked == []


def test_turn_in_phase_talks_to_homeowner(monkeypatch):
    monkeypatch.setattr(controller, "get_npc_nearest",
                         lambda npc_id: {"index": 5, "screenX": 10, "screenY": 20})
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [])

    talked = {}
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda ser, entity, option, **kw: talked.setdefault("option", option))

    ctx = _Ctx()
    state = {"npcId": 10415, "npcName": "Jeff"}
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert talked["option"] == "Talk-to"


def test_tick_accepts_offered_tea_whatever_phase_it_is_in(monkeypatch):
    # The completion chat line and the tea offer arrive on the SAME tick, so by the time the
    # dialogue is up hasContract has already flipped false and tick() has left "turn_in". A tea
    # check sited inside turn_in_phase could therefore never fire for the tick it mattered on.
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    monkeypatch.setattr(controller, "get_widgets_dump",
                         lambda: [{"text": "Yes, I'd love a cuppa.", "screenX": 40, "screenY": 50}])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank", lambda ctx, config, state: None)

    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: clicked.setdefault("entity", entity))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.tick(ctx)
    assert clicked["entity"]["screenX"] == 40


def test_turn_in_phase_no_longer_reads_widgets_itself(monkeypatch):
    # tick() owns the tea check now; turn_in_phase must not duplicate it.
    monkeypatch.setattr(controller, "get_npc_nearest", lambda npc_id: {"index": 5})
    monkeypatch.setattr(controller, "get_widgets_dump",
                         lambda: pytest.fail("turn_in_phase must not dump widgets"))
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: True)

    ctx = _Ctx()
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, {"npcId": 10415, "npcName": "Jeff"})


def test_tick_starts_in_bank_phase_when_stock_is_low(monkeypatch):
    # PHASE_HANDLERS holds direct function references captured at module-load time, so
    # patching mh.bank_phase itself would NOT change what tick() actually calls through the
    # dict -- monkeypatch.setitem on the dict is the correct way to intercept it.
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank",
                         lambda ctx, config, state: ctx.state.data.setdefault("called", "bank"))

    ctx = _Ctx()
    mh.tick(ctx)
    assert ctx.state.data["mh_phase"] == "bank"
    assert ctx.state.data["called"] == "bank"


def test_tick_moves_to_request_contract_once_stocked(monkeypatch):
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8), _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "request_contract",
                         lambda ctx, config, state: ctx.state.data.setdefault("called", "request"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.tick(ctx)
    assert ctx.state.data["mh_phase"] == "request_contract"
    assert ctx.state.data["called"] == "request"


def test_tick_resets_to_request_contract_when_contract_disappears_mid_flow(monkeypatch):
    # Stock must read as sufficient here -- otherwise needs_bank_trip (which calls the real
    # controller.get_inventory/get_rune_pouch when unmocked) sees an empty inventory against a
    # bridge-less test environment and diverts to "bank" before this test ever gets to exercise
    # the request_contract reset it's actually checking.
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8), _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "request_contract",
                         lambda ctx, config, state: ctx.state.data.setdefault("called", "request"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "turn_in"
    mh.tick(ctx)
    assert ctx.state.data["mh_phase"] == "request_contract"
    assert ctx.state.data["called"] == "request"


def test_tick_does_not_overwrite_work_phases_own_bank_diversion(monkeypatch):
    # THE C3 REGRESSION: `elif phase in ("bank", "request_contract"): phase = "travel"` promoted
    # a "bank" diversion straight back to "travel" the instant hasContract was true, so
    # bank_phase never ran once and work_phase re-diverted every tick -- a livelock.
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 3})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank",
                        lambda ctx, config, state: ctx.state.data.setdefault("called", "bank"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.tick(ctx)

    assert ctx.state.data["mh_phase"] == "bank"
    assert ctx.state.data["called"] == "bank"


def test_tick_still_promotes_request_contract_to_travel_once_a_contract_exists(monkeypatch):
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 3})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "travel",
                        lambda ctx, config, state: ctx.state.data.setdefault("called", "travel"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "request_contract"
    mh.tick(ctx)

    assert ctx.state.data["mh_phase"] == "travel"
    assert ctx.state.data["called"] == "travel"


def test_tick_refuses_to_work_a_contract_of_the_wrong_tier(monkeypatch):
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 1, "homeName": "BOB"})
    for phase in mh.PHASE_HANDLERS:
        monkeypatch.setitem(mh.PHASE_HANDLERS, phase,
                            lambda ctx, config, state: ctx.state.data.setdefault("called", "any"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "travel"
    mh.tick(ctx)

    assert ctx.state.data["mh_phase"] == "request_contract"
    assert "called" not in ctx.state.data


def test_tick_works_a_contract_whose_tier_is_unreported(monkeypatch):
    # contractTier 0 means the provider could not parse a tier (older client build) -- be
    # permissive rather than deadlocking on a field that isn't being reported.
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 0})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "travel",
                        lambda ctx, config, state: ctx.state.data.setdefault("called", "travel"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "request_contract"
    mh.tick(ctx)

    assert ctx.state.data["called"] == "travel"


def test_tick_does_nothing_when_disabled():
    ctx = _Ctx()
    ctx.state.config = dict(mh.DEFAULT_CONFIG)
    ctx.state.config["enabled"] = False
    mh.tick(ctx)
    assert "mh_phase" not in ctx.state.data
