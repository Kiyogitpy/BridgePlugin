import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import dialogue


# --- The slayer ring's destination dialogue is PAGED (2026-09-14, live; moved from golem) -----
#
# USER: "On slayer ring there is dialog options to tp or a second menu that expand when hovering
# tele". MEASURED live off /api/ui/dialogue -- Wyrmscraig is not on page one:
#   page 1: 1 Stronghold Slayer Cave | 2 Morytania Slayer Tower | 3 Fremennik Slayer Dungeon
#           4 More options...
#   page 2: 1 Tarn's Lair | 2 Dark Beasts | 3 the Wyrmscraig Cavern | 4 Previous options...

PAGE_1 = ["Teleport to the Stronghold Slayer Cave", "Teleport to the Morytania Slayer Tower",
          "Teleport to the Fremennik Slayer Dungeon", "More options..."]
PAGE_2 = ["Teleport to Tarn's Lair", "Teleport to Dark Beasts",
          "Teleport to the Wyrmscraig Cavern", "Previous options..."]


class _State:
    def __init__(self):
        self.data = {}


class _Ctx:
    def __init__(self):
        self.ser = None
        self.state = _State()
        self.events = None


def _fake_clock(monkeypatch):
    state = {"t": 5000.0}
    monkeypatch.setattr(dialogue.time, "time", lambda: state["t"])
    monkeypatch.setattr(dialogue.time, "sleep", lambda s: state.__setitem__("t", state["t"] + s))
    return state


def _paged_dialogue(monkeypatch, pages):
    """A dialogue whose 'More options...' key advances to the next page."""
    state = {"page": 0}
    keys = []

    def options(d=None):
        return pages[state["page"]]

    def key_tap(ser, key):
        keys.append(key)
        chosen = pages[state["page"]][int(key) - 1]
        if "more option" in chosen.lower():
            state["page"] = min(state["page"] + 1, len(pages) - 1)
    monkeypatch.setattr(dialogue, "options", options)
    monkeypatch.setattr(controller, "key_tap", key_tap)
    # Deliberately NOT stubbing sleep: _fake_clock's sleep is what ADVANCES the fake clock, so a
    # no-op here freezes time and the bounded wait can never time out. That hung the suite once.
    return keys


def test_the_wyrmscraig_entry_is_found_on_the_second_page(monkeypatch):
    _fake_clock(monkeypatch)
    keys = _paged_dialogue(monkeypatch, [PAGE_1, PAGE_2])
    assert dialogue.choose(_Ctx(), "wyrmscraig") is True
    assert keys == ["4", "3"], "must take 'More options...' (4) then Wyrmscraig (3)"


def test_a_first_page_hit_does_not_page_at_all(monkeypatch):
    _fake_clock(monkeypatch)
    keys = _paged_dialogue(monkeypatch, [PAGE_2, PAGE_1])
    assert dialogue.choose(_Ctx(), "wyrmscraig") is True
    assert keys == ["3"]


def test_a_dialogue_that_never_advances_cannot_spin(monkeypatch):
    """A page-forward that answers with the same options must not be pressed forever."""
    _fake_clock(monkeypatch)
    keys = _paged_dialogue(monkeypatch, [PAGE_1])          # 'More options...' goes nowhere
    assert dialogue.choose(_Ctx(), "wyrmscraig") is False
    assert keys.count("4") <= dialogue.MAX_PAGES, f"paged forever: {keys}"


def test_find_option_is_a_case_insensitive_substring():
    assert dialogue.find_option(PAGE_2, "wyrmscraig") == 2
    assert dialogue.find_option(PAGE_1, "wyrmscraig") is None


def test_options_drop_the_title_and_blank_trailing_children():
    """MEASURED live 2026-09-03 (Giants' Foundry): the m8aq reader returns the TITLE as child 0
    then blank trailing children. Counting keys over the raw list pressed the wrong option."""
    d = {"options": ["How many would you like to add?", "1", "3", "X", "All", "", ""]}
    assert dialogue.options(d) == ["1", "3", "X", "All"]
    assert dialogue.options({"options": [{"text": "Select an Option"}, {"text": "Yes"}]}) == ["Yes"]


def test_is_open_sees_a_continue_only_stage():
    """The reader has NO open/visible field; a 'click here to continue' stage has text but no
    options and used to read as CLOSED (giants_foundry re-clicked Kovac mid-dialogue)."""
    assert dialogue.is_open({"npcText": "Well done.", "options": []}) is True
    assert dialogue.is_open({"playerText": "Hi"}) is True
    assert dialogue.is_open({"itemText": "You feel refreshed."}) is True
    assert dialogue.is_open({"options": ["Select an Option", "Yes"]}) is True
    assert dialogue.is_open({}) is False
    assert dialogue.is_open({"options": [], "npcText": "", "playerText": None}) is False


def test_read_returns_an_empty_dict_on_a_failed_or_erroring_reader(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    assert dialogue.read() == {}
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"reader": "Dialogue", "error": "boom"})
    assert dialogue.read() == {}
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"npcName": "Aya", "options": []})
    assert dialogue.read() == {"npcName": "Aya", "options": []}


def test_close_presses_escape(monkeypatch):
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    dialogue.close(_Ctx())
    assert keys == ["ESC"]
