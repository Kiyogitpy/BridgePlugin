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


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """HERMETIC, enforced. 2026-09-19: a test that forgot to fake dialogue.read() read the LIVE
    client -- where Aya's greeting really was open -- and 'passed' or failed by what was on screen."""
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


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


def _picker(monkeypatch, rows=None, children=None, box=None, open_after=0):
    """Fake the picker as the bridge really serves it: the container 75.4 and a row BY INDEX
    through /widget, and /widget/children (which on the current build lists no blank rows)."""
    calls = {"n": 0}
    box = box or {"found": True, "clickable": True, "screenX": 690, "screenY": 360, "height": 300}

    def get_widget(group, child, index=None):
        if (group, child) == npc_contact.SPELL:
            return {"found": True, "clickable": True, "screenX": 1270, "screenY": 676}
        if (group, child) == npc_contact.PICKER_LIST:
            if index is None:
                calls["n"] += 1
                return box if calls["n"] > open_after else {"found": True, "clickable": False}
            return (rows or {}).get(index, {"found": False})
        return {"found": False}
    monkeypatch.setattr(controller, "get_widget", get_widget)
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: list(children or []))


def test_live_run_1_the_row_is_addressed_by_index_because_the_picker_is_not_in_the_root_walk(monkeypatch):
    """LIVE 2026-09-19: three pickers opened, /widgets/dump listed none of them. Rows have no text,
    so the measured slot is the aim and the hover gate is the proof."""
    rows = {18: {"found": True, "clickable": True, "screenX": 691, "screenY": 306},
            33: {"found": True, "clickable": True, "screenX": 684, "screenY": 419}}
    _picker(monkeypatch, rows=rows)
    assert npc_contact.find_master_row("AYA")["index"] == 18
    assert npc_contact.find_master_row("Turael")["index"] == 18
    assert npc_contact.find_master_row("KURADAL")["screenY"] == 419
    assert npc_contact.find_master_row("KONAR") is None, "no measured slot and no actions: refuse"


def test_a_slot_scrolled_out_of_the_visible_list_is_refused(monkeypatch):
    rows = {33: {"found": True, "clickable": True, "screenX": 684, "screenY": 900}}
    _picker(monkeypatch, rows=rows)
    assert npc_contact.find_master_row("KURADAL") is None


def test_a_bridge_that_reports_actions_finds_the_row_by_action_and_ignores_the_hints(monkeypatch):
    children = [{"index": 7, "screenX": 1, "screenY": 300, "actions": ["Contact Duradel"]},
                {"index": 4, "screenX": 1, "screenY": 200, "actions": ["Contact Ayatollah"]}]
    _picker(monkeypatch, children=children)
    assert npc_contact.find_master_row("KURADAL")["index"] == 7
    assert npc_contact.find_master_row("AYA") is None, "whole name, not a prefix; and no row at the hint"

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
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Arclight", "qty": 1}, "shield": {"name": "Dragon defender", "qty": 1}})
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"carried": True, "runes": [
        {"name": "Astral rune", "quantity": 500}, {"name": "Cosmic rune", "quantity": 500}]})
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Air rune", "qty": 1}])
    assert "air" in npc_contact.missing_requirements("AYA").lower(), "2 air runes per cast, 1 held"
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Air rune", "qty": 40}])
    assert npc_contact.missing_requirements("AYA") is None
    monkeypatch.setattr(controller, "get_skills", lambda: {"Magic": {"level": 60}})
    assert "67" in npc_contact.missing_requirements("AYA")


def test_an_unreadable_spellbook_does_not_block_the_cast(monkeypatch):
    """None = the reader is unavailable (un-rebuilt client). The cast itself then proves it:
    a non-Lunar book has no clickable 218.113."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Arclight", "qty": 1}, "shield": {"name": "Dragon defender", "qty": 1}})
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        {"name": "Astral rune", "qty": 9}, {"name": "Cosmic rune", "qty": 9}, {"name": "Air rune", "qty": 9}])
    assert npc_contact.missing_requirements("AYA") is None


def _speaker_after_row_click(monkeypatch, name="Aya"):
    """dialogue.read() is silent until a picker ROW (an entity with an index) has been clicked."""
    said = {"open": False}
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append((entity.get("index"), kw.get("expected_action"), kw.get("check_ui_blockers"), kw.get("offset")))
        if entity.get("index") is not None:
            said["open"] = True
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(dialogue, "read", lambda: {"npcName": name} if said["open"] else {})
    return clicks


def test_contact_clicks_the_spell_then_the_row_with_the_hover_gate(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    monkeypatch.setattr(controller, "get_active_tab", lambda: 6)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    _picker(monkeypatch, rows={18: {"found": True, "clickable": True, "screenX": 691, "screenY": 306}}, open_after=3)
    clicks = _speaker_after_row_click(monkeypatch)
    assert npc_contact.contact(_Ctx(), "AYA", CFG) is True
    assert clicks[0] == (None, None, False, (0, 0)), "the spell: an interface click, no hover gate"
    assert clicks[1] == (18, ("contact aya", "contact turael"), False, (0, 0))


def test_an_already_open_picker_is_not_cast_into_again(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    _picker(monkeypatch, rows={18: {"found": True, "clickable": True, "screenX": 691, "screenY": 306}})
    clicks = _speaker_after_row_click(monkeypatch)
    ctx = _Ctx()
    assert npc_contact.contact(ctx, "AYA", CFG) is True
    assert [c[0] for c in clicks] == [18] and ctx.state.data.get("npc_contact_casts", 0) == 0


def test_live_run_2_a_cast_that_goes_straight_to_the_master_is_success(monkeypatch):
    """LIVE 2026-09-19: the spell's left-click re-contacts the LAST NPC. Three casts each opened
    Aya's greeting and were thrown away waiting for a picker that was never coming."""
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    monkeypatch.setattr(controller, "get_active_tab", lambda: 6)
    _picker(monkeypatch, open_after=10 ** 9)                       # the picker NEVER opens
    cast = {"done": False}
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: cast.update(done=True) or True)
    monkeypatch.setattr(dialogue, "read", lambda: {"npcName": "Aya", "npcText": "'Ello"} if cast["done"] else {})
    ctx = _Ctx()
    assert npc_contact.contact(ctx, "AYA", CFG) is True
    assert ctx.state.data["npc_contact_casts"] == 1


def test_a_masters_dialogue_that_is_already_open_costs_no_cast(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    monkeypatch.setattr(dialogue, "read", lambda: {"npcName": "Aya"})
    touched = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: touched.append(1) or True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: touched.append(key))
    assert npc_contact.contact(_Ctx(), "AYA", CFG) is True
    assert touched == []


def test_a_cast_that_recontacts_the_wrong_npc_is_closed_and_the_picker_forced(monkeypatch):
    """Task 20 wants Kuradal while the last contact was Aya: left-click reaches Aya, so her
    dialogue is ESCAPED untouched and the spell's right-click 'Cast' opens the picker."""
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    monkeypatch.setattr(controller, "get_active_tab", lambda: 6)
    stage = {"s": "idle"}
    rows = {33: {"found": True, "clickable": True, "screenX": 684, "screenY": 419}}

    def get_widget(group, child, index=None):
        if (group, child) == npc_contact.SPELL:
            return {"found": True, "clickable": True, "screenX": 1270, "screenY": 676}
        if (group, child) == npc_contact.PICKER_LIST:
            if stage["s"] != "picker":
                return {"found": False}
            return rows.get(index, {"found": False}) if index is not None else \
                {"found": True, "clickable": True, "screenX": 690, "screenY": 360, "height": 300}
        return {"found": False}
    monkeypatch.setattr(controller, "get_widget", get_widget)
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [])
    events = []

    def click_entity(ser, entity, **kw):
        if entity.get("index") is None:
            events.append("left-cast")
            stage["s"] = "aya"
        else:
            events.append(f"row {entity['index']}")
            stage["s"] = "kuradal"
        return True

    def click_menu_action(ser, entity, option, **kw):
        events.append(f"menu {option}")
        stage["s"] = "picker"
        return True

    def key_tap(ser, key):
        events.append(key)
        if key == "ESC":
            stage["s"] = "idle"
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(controller, "click_menu_action", click_menu_action)
    monkeypatch.setattr(controller, "key_tap", key_tap)
    monkeypatch.setattr(dialogue, "read", lambda: {"aya": {"npcName": "Aya"}, "kuradal": {"npcName": "Kuradal"}}.get(stage["s"], {}))
    assert npc_contact.contact(_Ctx(), "KURADAL", CFG) is True
    assert events == ["left-cast", "ESC", "menu Cast", "row 33"]


def test_contact_fails_cleanly_when_the_spell_is_not_clickable(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(NO_TASK))
    monkeypatch.setattr(controller, "get_active_tab", lambda: 6)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: {"found": True, "clickable": False})
    monkeypatch.setattr(dialogue, "read", lambda: {})
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


def test_a_menu_that_is_still_on_screen_is_not_answered_twice(monkeypatch):
    """Task-2 review: a slow tick leaves the answered menu up; a second '1' would land on the
    NEXT menu, whatever it is."""
    _quiet(monkeypatch)
    menu = {"options": ["Select an option", "I need another assignment.", "Er... nothing..."]}
    reads = {"n": 0}

    def read():
        reads["n"] += 1
        return menu if reads["n"] <= 4 else {"npcName": "Aya", "npcText": "Your new task is to kill 15 Bears."}
    monkeypatch.setattr(dialogue, "read", read)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS if reads["n"] > 5 else NO_TASK))
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    assert npc_contact.walk_to_assignment(_Ctx()).task == "bears"
    assert keys.count("1") == 1


def test_a_loose_from_you_never_matches(monkeypatch):
    _quiet(monkeypatch)
    stages = [{"options": ["Select an option", "Yes, I'd like a new task from you.", "No thanks."]}]
    keys = _dialogue_script(monkeypatch, stages, BEARS)
    assert npc_contact.walk_to_assignment(_Ctx()) is None
    assert keys == ["ESC"]


def test_no_master_is_asked_for_a_task_while_one_is_active(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(BEARS))
    touched = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: touched.append(key))
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: touched.append("click"))
    assert npc_contact.get_task(_Ctx(), "KURADAL", CFG) is None
    assert touched == []


def test_a_worn_air_staff_stands_in_for_air_runes_with_the_real_equipment_shape(monkeypatch):
    """FINAL REVIEW, reproduced live: /equipment is a dict keyed by slot, and iterating it as a
    list killed the engine on the first tick."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"spellbook": "LUNAR"})
    monkeypatch.setattr(controller, "get_skills", lambda: {"Magic": {"level": 89}})
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Astral rune", "qty": 5}, {"name": "Cosmic rune", "qty": 5}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Mystic air staff", "qty": 1}})
    assert npc_contact.missing_requirements("AYA") is None
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Arclight", "qty": 1}})
    assert "air" in npc_contact.missing_requirements("AYA").lower()
    monkeypatch.setattr(controller, "get_equipment", lambda: None)
    assert "air" in npc_contact.missing_requirements("AYA").lower()


def test_the_cancel_is_confirmed_by_the_button_inside_the_rewards_interface(monkeypatch):
    """Microbot-Hub's open-source slayer script: Tasks -> Cancel -> CONFIRM, all in interface 426.
    The first draft waited for a chat menu that never comes and would have timed out three times."""
    _quiet(monkeypatch)
    state = {"s": dict(BEARS, points=300), "overlay": False}
    monkeypatch.setattr(controller, "get_slayer_task", lambda: dict(state["s"]))
    monkeypatch.setattr(npc_contact, "contact", lambda ctx, master, cfg: True)
    monkeypatch.setattr(dialogue, "read", lambda: {})
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None:
                        {"found": True, "clickable": True, "screenX": 614, "screenY": 214})

    def dump(scan=False):
        if state["overlay"]:
            return [_w(426, 50, 0, text="Confirm", actions=["Confirm"]), _w(426, 51, 0, text="Back")]
        return [_w(426, 14, 6, actions=["Tasks"]), _w(426, 26, 3, text="Cancel task", actions=["Cancel"])]
    monkeypatch.setattr(controller, "get_widgets_dump", dump)
    clicks = []

    def click_entity(ser, entity, **kw):
        clicks.append(entity.get("child", "tab"))
        if entity.get("child") == 26:
            state["overlay"] = True
        if entity.get("child") == 50:
            state["s"] = dict(NO_TASK, points=270)
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    assert npc_contact.cancel_task(_Ctx(), "KURADAL", CFG) is True
    assert clicks == ["tab", 26, 50]


def test_a_rune_pouch_that_is_not_carried_supplies_no_runes(monkeypatch):
    """FOUND LIVE 2026-09-20: GET /rune-pouch answered {"carried": false, "runes": [... Cosmic
    rune x8949 ...]} -- the contents are VARBITS and are reported wherever the pouch is. With the
    pouch in the bank the check passed and the cast would have failed. The real shape, verbatim."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"carried": False, "total": 26072, "runes": [
        {"quantity": 8949, "name": "Cosmic rune", "slot": 4, "id": 564, "type": 9},
        {"quantity": 500, "name": "Astral rune", "slot": 1, "id": 9075, "type": 14}]})
    assert npc_contact._rune_counts() == {}
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"carried": True, "runes": [
        {"quantity": 8949, "name": "Cosmic rune", "slot": 4, "id": 564, "type": 9}]})
    assert npc_contact._rune_counts() == {"cosmic rune": 8949}
