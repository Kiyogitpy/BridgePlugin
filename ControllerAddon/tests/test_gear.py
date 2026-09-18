import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import gear


class _State:
    def __init__(self):
        self.data = {}


class _Ctx:
    def __init__(self):
        self.ser = None
        self.state = _State()
        self.events = None


def _equipment(**slots):
    """/equipment is a DICT KEYED BY SLOT whose values are item dicts (banker._item_rows)."""
    return {slot: {"id": 1, "name": name} for slot, name in slots.items()}


def test_worn_and_carried_names_are_lowercased_substring_friendly(monkeypatch):
    monkeypatch.setattr(controller, "get_equipment", lambda: _equipment(head="Slayer helmet", ring="Slayer ring (7)"))
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Earmuffs", "slot": 3}, {"name": "Shark", "slot": 4}])
    assert gear.worn_names() == {"slayer helmet", "slayer ring (7)"}
    assert gear.carried_names() == {"earmuffs", "shark"}
    assert gear.is_worn("slayer ring")
    assert gear.is_worn("earmuffs", alternatives=("slayer helmet",)), "a slayer helmet satisfies earmuffs"
    assert not gear.is_worn("earmuffs")
    assert gear.has("earmuffs") and gear.has("slayer ring") and not gear.has("nose peg")


def test_equip_from_inventory_clicks_wear_and_confirms_on_equipment(monkeypatch):
    worn = {"gloves": None}
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append((entity["name"], kw.get("expected_action"), kw.get("simulate_mistakes")))
        worn["gloves"] = {"id": 2, "name": entity["name"]}
        return True
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Expeditious bracelet", "slot": 5, "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {k: v for k, v in worn.items() if v})
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(gear.time, "sleep", lambda s: None)

    assert gear.equip_from_inventory(_Ctx(), "expeditious bracelet") is True
    assert clicks == [("Expeditious bracelet", gear.EQUIP_ACTIONS, False)]


def test_equip_from_inventory_is_false_when_the_click_never_shows_on_equipment(monkeypatch):
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Earmuffs", "slot": 5, "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(gear.time, "sleep", lambda s: None)
    monkeypatch.setattr(gear.time, "time", iter(range(0, 100)).__next__)   # each read is +1s
    assert gear.equip_from_inventory(_Ctx(), "earmuffs") is False


def test_equip_from_inventory_is_false_when_the_item_is_not_carried(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert gear.equip_from_inventory(_Ctx(), "earmuffs") is False
    assert not clicked


def test_ensure_worn_equips_what_is_missing_and_reports_what_still_is(monkeypatch):
    monkeypatch.setattr(controller, "get_equipment", lambda: _equipment(head="Slayer helmet"))
    attempted = []
    monkeypatch.setattr(gear, "equip_from_inventory", lambda ctx, name: attempted.append(name) or name == "rock hammer")
    missing = gear.ensure_worn(_Ctx(), [("earmuffs", ("slayer helmet",)), ("rock hammer", ()), ("mirror shield", ())])
    assert attempted == ["rock hammer", "mirror shield"], "earmuffs are satisfied by the helmet"
    assert missing == ["mirror shield"]
