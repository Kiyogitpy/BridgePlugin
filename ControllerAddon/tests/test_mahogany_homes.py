import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import mahogany_homes as mh
import controller
import banker


def _hotspot(varb, object_id, value, needs_attention):
    return {"varb": varb, "objectId": object_id, "value": value, "needsAttention": needs_attention}


def _object(obj_id, distance=1, x=0, y=0, plane=0):
    return {"id": obj_id, "distance": distance, "x": x, "y": y, "plane": plane,
            "screenX": 100, "screenY": 100}


def _item(name, quantity=1):
    return {"name": name, "quantity": quantity}


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


def test_count_item_uses_quantity_field_when_present():
    inventory = [_item("Varrock teleport", 8)]
    assert mh.count_item(inventory, "Varrock teleport") == 8


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


# --- bank_phase -----------------------------------------------------------------------

def test_bank_phase_breaks_home_teleport_when_no_bank_and_no_teleport_leaves_safety_stopped(monkeypatch):
    monkeypatch.setattr(banker, "find_nearby_bank", lambda config: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})
    # No bank in range and no home teleport tablet in inventory -- genuine dead end.
    assert ctx.state.safety_stopped is True


def test_bank_phase_withdraws_up_to_target_and_advances_phase(monkeypatch):
    withdrawn = []

    monkeypatch.setattr(banker, "find_nearby_bank", lambda config: {"id": 1, "name": "Bank booth"})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, bank_obj: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx: None)

    def fake_withdraw_loadout(ctx, config, fresh=False):
        withdrawn.append(config["withdraw"][0])

    monkeypatch.setattr(banker, "withdraw_loadout", fake_withdraw_loadout)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    names = {w["name"] for w in withdrawn}
    assert mh.DEFAULT_CONFIG["plankName"] in names
    assert "Steel bar" in names
    assert ctx.state.data["mh_phase"] == "travel"
