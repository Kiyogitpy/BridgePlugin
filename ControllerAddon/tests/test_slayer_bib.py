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
