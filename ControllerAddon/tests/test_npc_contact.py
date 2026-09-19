import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import dialogue
import npc_contact
import slayer_state

CFG = {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10}
NO_TASK = {"count": 0, "hasTask": False, "streak": 12, "points": 24, "taskId": 52}
BEARS = {"count": 15, "hasTask": True, "taskName": "Bears", "taskId": 13, "streak": 12, "points": 24, "masterId": 1}


class _Ctx:
    def __init__(self):
        self.ser = None

        class _S:
            data = {}
            stops = []

            def trigger_safety_stop(self, reason):
                self.stops.append(reason)
        self.state = _S()
        self.state.data, self.state.stops = {}, []
        self.events = None


def _row(index, action, y=300):
    return {"group": 75, "child": 4, "index": index, "screenX": 690, "screenY": y, "canvasY": y - 20,
            "height": 40, "actions": [action]}


def _quiet(monkeypatch):
    monkeypatch.setattr(npc_contact.time, "sleep", lambda s: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


def test_the_master_row_is_found_by_its_action_never_by_slot():
    """MEASURED: a picker row's text and name are EMPTY; only its action names the master. The
    slots seen (Aya 18, Kuradal 33) are not used -- they move when a master is unlocked."""
    dump = [_row(33, "Contact Kuradal", 419), _row(18, "Contact Aya", 306), _row(2, "Contact Honest Jimmy", 120),
            {"group": 218, "child": 113, "index": -1, "screenX": 1, "screenY": 1, "actions": ["Cast"]}]
    assert npc_contact.find_master_row(dump, "AYA")["index"] == 18
    assert npc_contact.find_master_row(dump, "KURADAL")["index"] == 33
    assert npc_contact.find_master_row(dump, "KONAR") is None


def test_the_other_name_of_the_same_master_matches_too():
    """Aya is Turael's Varlamore twin and Kuradal is Duradel's; which name the picker shows
    depends on the account, so both are accepted."""
    assert npc_contact.find_master_row([_row(9, "Contact Turael")], "AYA")["index"] == 9
    assert npc_contact.find_master_row([_row(9, "Contact Duradel")], "KURADAL")["index"] == 9
    assert npc_contact.find_master_row([_row(9, "Contact Ayatollah")], "AYA") is None, "whole name, not a prefix"


def test_get_task_refuses_aya_while_a_task_is_active_before_touching_anything(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS))
    touched = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: touched.append(key))
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: touched.append("click"))
    with pytest.raises(slayer_state.StreakGuardError):
        npc_contact.get_task(_Ctx(), "AYA", CFG)
    assert touched == []


def test_get_task_does_nothing_when_the_slayer_state_is_unavailable(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: None)
    touched = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: touched.append(key))
    with pytest.raises(slayer_state.StreakGuardError):
        npc_contact.get_task(_Ctx(), "KURADAL", CFG)
    assert touched == []


def _dialogue_script(monkeypatch, stages, slayer_after):
    """`stages` is a list of dialogue dicts served one per read; the last repeats. The slayer
    state flips to `slayer_after` once the stage whose npcText contains 'new task' has been read."""
    seen = {"i": 0, "assigned": False}

    def read():
        stage = stages[min(seen["i"], len(stages) - 1)]
        if "new task" in (stage.get("npcText") or ""):
            seen["assigned"] = True
        return stage
    monkeypatch.setattr(dialogue, "read", read)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(slayer_after if seen["assigned"] else NO_TASK))
    keys = []

    def key_tap(ser, key):
        keys.append(key)
        seen["i"] += 1
    monkeypatch.setattr(controller, "key_tap", key_tap)
    return keys


def test_the_recorded_aya_dialogue_is_walked_by_text(monkeypatch):
    _quiet(monkeypatch)
    stages = [
        {"npcName": "Aya", "npcText": "'Ello, and what are you after then?", "options": []},
        {"options": ["Select an option", "I need another assignment.", "Have you any rewards for me?", "Er... nothing..."]},
        {"playerText": "I need another assignment.", "options": []},
        {"npcName": "Aya", "npcText": "You're actually very strong, are you sure you don't want Vannaka under Edgeville to assign you a task?", "options": []},
        {"options": ["Select an option", "No that's okay, I'll take a task from you.", "Oh okay then, I'll go talk to Vannaka."]},
        {"npcName": "Aya", "npcText": "Excellent, you're doing great. Your new task is to kill 15 Bears.", "options": []},
    ]
    keys = _dialogue_script(monkeypatch, stages, BEARS)
    state = npc_contact.walk_to_assignment(_Ctx())
    assert state is not None and state.task == "bears" and state.count == 15
    assert keys[:5] == ["SPACE", "1", "SPACE", "SPACE", "1"]
    assert keys[-1] == "ESC", "once /slayer shows the task the rest of the dialogue is just closed"


def test_the_assignment_option_is_chosen_by_text_when_it_is_not_first(monkeypatch):
    _quiet(monkeypatch)
    stages = [
        {"options": ["Select an option", "Have you any rewards for me?", "I need another assignment."]},
        {"npcName": "Kuradal", "npcText": "Your new task is to kill 130 Bloodveld.", "options": []},
    ]
    keys = _dialogue_script(monkeypatch, stages, dict(BEARS, taskName="Bloodveld", count=130))
    assert npc_contact.walk_to_assignment(_Ctx()).task == "bloodveld"
    assert keys[0] == "2"


def test_an_unexpected_menu_is_escaped_never_guessed(monkeypatch):
    """The hard rule: a menu with no expected option is ESC + failure. Never '1', never Space."""
    _quiet(monkeypatch)
    stages = [{"options": ["Select an option", "Yes, replace my task.", "No, I'll keep it."]}]
    keys = _dialogue_script(monkeypatch, stages, BEARS)
    ctx = _Ctx()
    assert npc_contact.walk_to_assignment(ctx) is None
    assert keys == ["ESC"]
    assert "replace my task" in ctx.state.data["npc_contact_unknown_menu"]


def test_a_master_who_says_you_still_have_a_task_is_a_failure_not_a_loop(monkeypatch):
    _quiet(monkeypatch)
    stages = [
        {"options": ["Select an option", "I need another assignment."]},
        {"npcName": "Kuradal", "npcText": "You're still hunting nechryael; you have 92 to go.", "options": []},
        {},
    ]
    keys = _dialogue_script(monkeypatch, stages, NO_TASK)
    assert npc_contact.walk_to_assignment(_Ctx()) is None
    assert keys.count("1") == 1


def test_missing_requirements_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"spellbook": "STANDARD"})
    assert "Lunar" in npc_contact.missing_requirements("AYA")
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"spellbook": "LUNAR"})
    monkeypatch.setattr(controller, "get_skills", lambda: {"Magic": {"level": 89}})
    monkeypatch.setattr(controller, "get_equipment", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"carried": True, "runes": [
        {"name": "Astral rune", "quantity": 500}, {"name": "Cosmic rune", "quantity": 500}]})
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Air rune", "quantity": 1}])
    assert "air" in npc_contact.missing_requirements("AYA").lower(), "2 air runes per cast, 1 held"
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Air rune", "quantity": 40}])
    assert npc_contact.missing_requirements("AYA") is None
    monkeypatch.setattr(controller, "get_skills", lambda: {"Magic": {"level": 60}})
    assert "67" in npc_contact.missing_requirements("AYA")


def test_an_unreadable_spellbook_does_not_block_the_cast(monkeypatch):
    """None = the reader is unavailable (un-rebuilt client). The cast itself then proves it:
    a non-Lunar book has no clickable 218.113."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(controller, "get_equipment", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        {"name": "Astral rune", "quantity": 9}, {"name": "Cosmic rune", "quantity": 9}, {"name": "Air rune", "quantity": 9}])
    assert npc_contact.missing_requirements("AYA") is None


def test_contact_clicks_the_spell_then_the_row_with_the_hover_gate(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    monkeypatch.setattr(controller, "get_active_tab", lambda: 6)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None:
                        {"found": True, "clickable": True, "screenX": 1270, "screenY": 676})
    dumps = iter([[], [], [_row(18, "Contact Aya", 306), _row(33, "Contact Kuradal", 419)]])
    last = {}

    def get_widgets_dump(scan=False):
        last["d"] = next(dumps, last.get("d", []))
        return last["d"]
    monkeypatch.setattr(controller, "get_widgets_dump", get_widgets_dump)
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append((entity.get("index"), kw.get("expected_action"), kw.get("check_ui_blockers"), kw.get("offset")))
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(dialogue, "read", lambda: {"npcName": "Aya", "npcText": "'Ello, and what are you after then?"})
    assert npc_contact.contact(_Ctx(), "AYA", CFG) is True
    assert clicks[0] == (None, None, False, (0, 0)), "the spell: an interface click, no hover gate"
    assert clicks[1] == (18, "Contact Aya", False, (0, 0))


def test_contact_fails_cleanly_when_the_spell_is_not_clickable(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    monkeypatch.setattr(controller, "get_active_tab", lambda: 6)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: {"found": True, "clickable": False})
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert npc_contact.contact(_Ctx(), "AYA", CFG) is False
    assert clicked == []


def _w(group, child, index=-1, text=None, actions=None, x=500, y=300):
    entry = {"group": group, "child": child, "index": index, "screenX": x, "screenY": y}
    if text:
        entry["text"] = text
    if actions:
        entry["actions"] = actions
    return entry


def test_the_cancel_control_is_found_by_word_inside_the_rewards_interface_only():
    dump = [_w(426, 14, 6, actions=["Tasks"]), _w(426, 26, 3, text="Cancel task", actions=["Cancel"]),
            _w(162, 5, text="Cancel"), _w(426, 30, 1, text="Block task", actions=["Block"])]
    assert npc_contact.find_cancel_control(dump)["child"] == 26
    assert npc_contact.find_cancel_control([_w(162, 5, text="Cancel")]) is None


def test_two_cancel_candidates_is_a_refusal_not_a_coin_toss():
    dump = [_w(426, 26, 3, text="Cancel task"), _w(426, 40, 0, text="Cancel block")]
    assert npc_contact.find_cancel_control(dump) is None


def test_cancel_task_is_refused_without_the_points(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS, points=24))
    touched = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: touched.append(key))
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: touched.append("click") or True)
    assert npc_contact.cancel_task(_Ctx(), "KURADAL", CFG) is False
    assert touched == []


def test_cancel_task_never_goes_through_the_filler_master(monkeypatch):
    """Rewards from Aya is harmless, but the DIALOGUE to get there passes her assignment option.
    With a task active (always true for a cancel) she is simply not contacted."""
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS, points=300))
    with pytest.raises(slayer_state.StreakGuardError):
        npc_contact.cancel_task(_Ctx(), "AYA", CFG)


def test_cancel_task_confirms_from_the_slayer_state_not_from_the_click(monkeypatch):
    _quiet(monkeypatch)
    state = {"s": dict(BEARS, points=300)}
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(state["s"]))
    monkeypatch.setattr(npc_contact, "contact", lambda ctx, master, cfg: True)
    monkeypatch.setattr(dialogue, "read", lambda: {"options": ["Select an option", "I need another assignment.",
                                                               "Have you any rewards for me?"]})
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None:
                        {"found": True, "clickable": True, "screenX": 614, "screenY": 214})
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False:
                        [_w(426, 14, 6, actions=["Tasks"]), _w(426, 26, 3, text="Cancel task", actions=["Cancel"])])
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append(entity.get("child", "tab"))
        if entity.get("child") == 26:
            state["s"] = dict(NO_TASK, points=270)
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    assert npc_contact.cancel_task(_Ctx(), "KURADAL", CFG) is True
    assert 26 in clicks


def test_cancel_task_is_false_when_the_task_survives_the_click(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(npc_contact, "CANCEL_BUDGET_S", 0.05)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS, points=300))
    monkeypatch.setattr(npc_contact, "contact", lambda ctx, master, cfg: True)
    monkeypatch.setattr(dialogue, "read", lambda: {"options": ["Select an option", "Have you any rewards for me?"]})
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None:
                        {"found": True, "clickable": True, "screenX": 614, "screenY": 214})
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [_w(426, 26, 3, text="Cancel task")])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    assert npc_contact.cancel_task(_Ctx(), "KURADAL", CFG) is False
