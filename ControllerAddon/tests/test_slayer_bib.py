"""Gearing up from the installed Slayer Best in Bank plugin's own bank view.

The plugin decides the loadout; we click its button and withdraw what it shows. So there is very
little of OUR logic to test -- which is the point. What IS ours, and can be silently wrong, is the
split between the loadout sections and the Tier 2/3 alternatives: getting that wrong drags a
second and third weapon into a 28-slot inventory, and nothing would error.

Geometry under test comes from TieredBankLayout.java (commit 3c7040b5): loadout sections lay out
EQUIP_PATH_ITEMS_PER_ROW = 4 per row, Tier 2/3 lay out ITEMS_PER_ROW = 8, item pitch 48 x 36.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import slayer_bib


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


# Screen coordinates, NOT the widget-relative 51/99/147/195 in the Java: /bank reports
# canvasToScreen positions, so the origin is arbitrary and the pitch carries the stretch scale.
# These fixtures use a deliberately odd origin and a stretched pitch to prove the split does not
# depend on either.
ORIGIN_X, ORIGIN_Y, PITCH_X, PITCH_Y = 1337, 402, 62, 47


def _item(name, column, row):
    return {"name": name, "qty": 1,
            "screenX": ORIGIN_X + column * PITCH_X,
            "screenY": ORIGIN_Y + row * PITCH_Y}


def _four_wide(names, first_row=0):
    """The loadout sections' layout: 4 columns, odd rows reversed (the withdraw zigzag)."""
    out = []
    for index, name in enumerate(names):
        row, column = divmod(index, 4)
        if row % 2:
            column = 3 - column
        out.append(_item(name, column, first_row + row))
    return out


def _eight_wide(names, first_row):
    return [_item(name, index % 8, first_row + index // 8) for index, name in enumerate(names)]


def test_a_four_column_view_is_all_loadout():
    """The common case: with SBIB's alternatives-per-slot at 1 there are no Tier 2/3 sections at
    all, and everything shown is meant to be taken."""
    items = _four_wide(["Slayer helmet (i)", "Fire cape", "Amulet of fury", "Dragon scimitar",
                        "Fighter torso", "Dragon defender", "Proselyte tasset", "Barrows gloves"])
    loadout, alternatives = slayer_bib._split_loadout(items)
    assert [i["name"] for i in loadout] == [i["name"] for i in items]
    assert alternatives == []


def test_a_fifth_column_marks_where_the_alternatives_begin():
    """Only Tier 2/3 lay out 8 wide, so a 5th distinct column can only be an alternative -- and
    everything from that ROW down belongs to those sections."""
    loadout_items = _four_wide(["Slayer helmet (i)", "Fire cape", "Amulet of fury",
                                "Dragon scimitar", "Shark", "Prayer potion(4)"])
    tier_two = _eight_wide(["Helm of neitiznot", "Obsidian cape", "Amulet of glory",
                            "Abyssal whip", "Fighter torso", "Rune defender"], first_row=2)
    loadout, alternatives = slayer_bib._split_loadout(loadout_items + tier_two)
    assert [i["name"] for i in loadout] == [i["name"] for i in loadout_items]
    assert [i["name"] for i in alternatives] == [i["name"] for i in tier_two]


def test_alternatives_sharing_the_first_four_columns_still_go_with_their_row():
    """The dangerous case: a Tier 2 item at column 0 looks exactly like a loadout item by x alone.
    The row boundary is what catches it, which is why the split is by Y and not by column."""
    loadout_items = _four_wide(["Slayer helmet (i)", "Dragon scimitar"])
    tier_two = _eight_wide(["Helm of neitiznot", "Abyssal whip", "Rune platebody", "Rune kiteshield",
                            "Amulet of glory", "Climbing boots"], first_row=1)
    loadout, alternatives = slayer_bib._split_loadout(loadout_items + tier_two)
    assert "Helm of neitiznot" not in [i["name"] for i in loadout]
    assert len(alternatives) == 6


def test_items_with_no_drawn_position_are_never_silently_dropped():
    """A recommendation we cannot place must stay in the loadout rather than vanish: _withdraw
    refuses an unreachable slot loudly, which is the behaviour we want over a quiet omission."""
    items = [{"name": "Dragon scimitar", "qty": 1}]
    loadout, alternatives = slayer_bib._split_loadout(items)
    assert [i["name"] for i in loadout] == ["Dragon scimitar"]
    assert alternatives == []


def test_an_empty_view_splits_without_raising():
    assert slayer_bib._split_loadout([]) == ([], [])


def test_view_active_distinguishes_a_recommendation_set_from_a_real_bank(monkeypatch):
    """This is how we read the toggle's state -- the button's own action text is not exposed by
    WidgetProvider, so the BANK is the source of truth."""
    monkeypatch.setattr(slayer_bib, "bank_items", lambda: [{"name": "x"}] * 12)
    assert slayer_bib.view_is_active() is True
    monkeypatch.setattr(slayer_bib, "bank_items", lambda: [{"name": "x"}] * 903)
    assert slayer_bib.view_is_active() is False


def test_a_shut_bank_is_not_an_active_view_and_is_not_an_error(monkeypatch):
    monkeypatch.setattr(slayer_bib, "bank_items", lambda: None)
    assert slayer_bib.view_is_active() is False


def test_an_empty_bank_is_not_an_active_view(monkeypatch):
    """0 items means the bank is drawing nothing, not that the plugin recommends nothing --
    treating it as an active view would make gear_up withdraw nothing and report success."""
    monkeypatch.setattr(slayer_bib, "bank_items", lambda: [])
    assert slayer_bib.view_is_active() is False


def test_the_button_is_matched_by_name_never_by_index(monkeypatch):
    """createChild(-1, ...) APPENDS, and other installed plugins inject buttons into the same
    12.1 container, so whichever loaded first takes the lower index."""
    children = [
        {"index": 0, "name": "View required materials", "itemId": 1234,
         "screenX": 10, "screenY": 10, "clickable": True},
        {"index": 1, "name": slayer_bib.BUTTON_NAME, "itemId": slayer_bib.BUTTON_ITEM_ID,
         "screenX": 20, "screenY": 20, "clickable": True},
    ]
    monkeypatch.setattr(controller, "api_get",
                        lambda path, **k: {"found": True, "children": children})
    assert slayer_bib.find_button()["index"] == 1


def test_the_item_id_is_the_fallback_when_the_button_is_renamed(monkeypatch):
    children = [{"index": 3, "name": "Something else entirely",
                 "itemId": slayer_bib.BUTTON_ITEM_ID,
                 "screenX": 20, "screenY": 20, "clickable": True}]
    monkeypatch.setattr(controller, "api_get",
                        lambda path, **k: {"found": True, "children": children})
    assert slayer_bib.find_button()["index"] == 3


def test_a_shut_bank_yields_no_button(monkeypatch):
    monkeypatch.setattr(controller, "api_get", lambda path, **k: {"found": False})
    assert slayer_bib.find_button() is None


# ---------------- plan_loadout: one pick per slot, placeholders, what is already on ----------------
#
# Fixtures are the REAL trips in logs/slayer_auto_14.log (2026-09-21). Trip 1 withdrew Rada's, War
# AND Peaceful blessing -- three ammo-slot items -- and War replaced Rada's on the way out. Trips 2-4
# "withdrew" Rada's blessing, the tasset and the boots again while they were already worn, and
# equipped nothing: the clicks landed on the placeholders those worn items leave in the bank.

def _bib(name, slot=None, qty=1, two_handed=False):
    item = {"name": name, "qty": qty, "screenX": 650, "screenY": 400}
    if slot:
        item["equipSlot"] = slot
    if two_handed:
        item["twoHanded"] = True
    return item


TRIP_ONE = [
    _bib("Super combat potion(3)"),
    _bib("Rada's blessing 3", "ammo"),
    _bib("Proselyte tasset", "legs"),
    _bib("War blessing", "ammo"),
    _bib("Prayer potion(4)"),
    _bib("Rune boots", "boots"),
    _bib("Peaceful blessing", "ammo"),
    _bib("Manta ray"),
    _bib("Teleport crystal (3)"),
]


def _names(items):
    return [i["name"] for i in items]


def test_the_first_pick_for_a_slot_wins_and_the_rest_stay_banked():
    plan = slayer_bib.plan_loadout(TRIP_ONE, worn=set(), carried=set())
    assert "Rada's blessing 3" in _names(plan.withdraw)
    assert "War blessing" not in _names(plan.withdraw)
    assert "Peaceful blessing" not in _names(plan.withdraw)
    assert plan.wear == ["rada's blessing 3", "proselyte tasset", "rune boots"]


def test_consumables_are_withdrawn_and_never_worn():
    plan = slayer_bib.plan_loadout(TRIP_ONE, worn=set(), carried=set())
    for name in ("Super combat potion(3)", "Prayer potion(4)", "Manta ray", "Teleport crystal (3)"):
        assert name in _names(plan.withdraw)
        assert name.lower() not in plan.wear


def test_a_placeholder_claims_its_slot_but_is_never_clicked():
    """Trip 2: Rada's blessing is WORN, so the bank shows its placeholder (qty 0). It must still
    claim the ammo slot, or War blessing behind it would be withdrawn and worn over it."""
    trip = [dict(i) for i in TRIP_ONE]
    trip[1]["qty"] = 0
    plan = slayer_bib.plan_loadout(trip, worn={"rada's blessing 3"}, carried=set())
    assert "Rada's blessing 3" not in _names(plan.withdraw)
    assert "War blessing" not in _names(plan.withdraw)
    assert "rada's blessing 3" not in plan.wear


def test_a_placeholder_for_a_consumable_is_skipped():
    plan = slayer_bib.plan_loadout([_bib("Prayer potion(4)", qty=0)], worn=set(), carried=set())
    assert plan.withdraw == []


def test_a_pick_already_carried_is_worn_without_another_withdraw():
    """Trip 1's Rune boots: withdrawn, never worn. Equipping from the plan -- not from what the
    trip newly added to the inventory -- puts on a pick that was already carried."""
    plan = slayer_bib.plan_loadout([_bib("Rune boots", "boots")], worn=set(), carried={"rune boots"})
    assert plan.withdraw == []
    assert plan.wear == ["rune boots"]


def test_a_pick_already_worn_is_left_alone_even_if_the_bank_has_a_spare():
    plan = slayer_bib.plan_loadout([_bib("Rune boots", "boots")], worn={"rune boots"}, carried=set())
    assert plan.withdraw == []
    assert plan.wear == []


def test_the_weapon_is_worn_before_anything_else():
    """A shield put on before its one-handed weapon lands in a two-hander's shadow; weapon first."""
    items = [_bib("Dragon defender", "shield"), _bib("Slayer helmet (i)", "head"),
             _bib("Abyssal whip", "weapon")]
    plan = slayer_bib.plan_loadout(items, worn=set(), carried=set())
    assert plan.wear[0] == "abyssal whip"


def test_a_two_handed_pick_closes_the_shield_slot():
    """Equipping a shield would knock the two-hander straight back off."""
    items = [_bib("Toxic blowpipe", "weapon", two_handed=True), _bib("Dragon defender", "shield")]
    plan = slayer_bib.plan_loadout(items, worn=set(), carried=set())
    assert _names(plan.withdraw) == ["Toxic blowpipe"]
    assert plan.wear == ["toxic blowpipe"]


def test_every_skip_says_why():
    plan = slayer_bib.plan_loadout(TRIP_ONE, worn=set(), carried=set())
    reasons = dict(plan.skipped)
    assert "ammo" in reasons["War blessing"] and "Rada's blessing 3" in reasons["War blessing"]


# ---------------- an un-rebuilt client: no equipSlot anywhere ----------------

def test_without_slot_data_everything_is_withdrawn_as_before():
    """An un-rebuilt bridge sends no equipSlot. Nothing may be LOST relative to the old behaviour:
    every non-placeholder is still withdrawn."""
    legacy = [{k: v for k, v in i.items() if k != "equipSlot"} for i in TRIP_ONE]
    plan = slayer_bib.plan_loadout(legacy, worn=set(), carried=set())
    assert _names(plan.withdraw) == _names(TRIP_ONE)


def test_without_slot_data_obvious_consumables_are_never_worn():
    legacy = [{k: v for k, v in i.items() if k != "equipSlot"} for i in TRIP_ONE]
    legacy[7]["isFood"] = True                           # Manta ray, as /bank flags it
    plan = slayer_bib.plan_loadout(legacy, worn=set(), carried=set())
    for name in ("super combat potion(3)", "prayer potion(4)", "manta ray"):
        assert name not in plan.wear
    assert "rada's blessing 3" in plan.wear


def test_without_slot_data_placeholders_are_still_skipped():
    legacy = [{"name": "Rada's blessing 3", "qty": 0, "screenX": 1, "screenY": 1}]
    assert slayer_bib.plan_loadout(legacy, worn=set(), carried=set()).withdraw == []


# ---------------- gear_up wiring and the toggle confirm ----------------

class _Ctx:
    ser = None
    events = None


def test_gear_up_withdraws_only_the_plan_and_wears_from_it(monkeypatch):
    clicks, equips = [], []
    monkeypatch.setattr(slayer_bib, "toggle", lambda ctx, want=True: True)
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {"items": TRIP_ONE})
    monkeypatch.setattr(slayer_bib.banker, "set_quantity_mode", lambda ctx, mode: None)
    monkeypatch.setattr(slayer_bib, "_withdraw", lambda ctx, slot, q: clicks.append(slot["name"]) or True)
    carried = set()
    monkeypatch.setattr(slayer_bib.gear, "worn_names", lambda: set())
    monkeypatch.setattr(slayer_bib.gear, "carried_names",
                        lambda: {c.lower() for c in clicks} | carried)
    monkeypatch.setattr(slayer_bib.gear, "is_worn", lambda name, alternatives=(): name in equips)
    monkeypatch.setattr(slayer_bib.gear, "equip_from_inventory",
                        lambda ctx, name: equips.append(name) or True)
    result = slayer_bib.gear_up(_Ctx(), quantities={"manta ray": 12})
    assert "War blessing" not in clicks and "Peaceful blessing" not in clicks
    assert equips == ["rada's blessing 3", "proselyte tasset", "rune boots"]
    assert result["ok"] and result["equipped"] == equips


def test_the_toggle_confirm_returns_as_soon_as_the_view_flips(monkeypatch):
    """It used to spend its whole 3s budget waiting for a WIDGET_LOADED that never comes."""
    reads = iter([[{"name": "x"}] * 900, [{"name": "x"}] * 9])
    state = {"items": None}

    def items():
        state["items"] = next(reads, state["items"])
        return state["items"]
    monkeypatch.setattr(slayer_bib, "bank_items", items)
    monkeypatch.setattr(slayer_bib, "TOGGLE_POLL_S", 0.0)
    import time
    start = time.time()
    assert slayer_bib._confirm_changed(_Ctx(), 900, True) is True
    assert time.time() - start < 1.0
