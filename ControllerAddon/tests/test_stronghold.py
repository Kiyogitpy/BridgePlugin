"""The Stronghold of Security's question gates (FOUND LIVE 2026-09-21).

A gate that TALKS instead of swinging broke the minotaurs journey: travel._open_door clicked the
Gate of War seven times and the blocked edge never cleared, because the gate was waiting for an
answer to an account-security question.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import dialogue
import stronghold


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


class _Ctx:
    def __init__(self):
        self.ser = object()


# --- the table -----------------------------------------------------------------------------

def test_every_pair_normalises_to_something_matchable():
    for question, answer in stronghold.PAIRS:
        assert stronghold._norm(question), question
        assert stronghold._norm(answer), answer


def test_a_plain_question_picks_its_own_answer():
    q = ("To pass you must answer me this: Who can I give my<br>password to?")
    idx, how = stronghold.answer_index(q, ["My friend.", "Nobody.", "Jagex staff."])
    assert (idx, how) == (1, "the question")


def test_the_line_breaks_the_client_sends_do_not_matter():
    """The client wraps the question to its own width, so <br> placement is not a fact to match
    on -- only the words are."""
    q = "To pass you must answer me this: Who can I give my password to?"
    assert stronghold.answer_index(q, ["Nobody.", "My friend."])[0] == 0


def test_the_two_questions_ending_respond_are_told_apart_by_their_options():
    """Upstream keys a HashMap on the question text and has TWO entries keyed "respond?" with
    different answers, so one silently overwrites the other and that question can never be
    answered. Intersecting the candidates with the options on screen is unambiguous even though
    the question tail is not."""
    tail = "...he can help you make progress. How do you respond?"
    assert stronghold.answer_index(
        tail, ["Accept.", "Decline the offer and report that player.", "Ignore."])[0] == 1
    assert stronghold.answer_index(
        tail, ["Sign up.", "Nothing, it's a fake."])[0] == 1


def test_an_unrecognised_question_answers_nothing():
    """A wrong press is not destructive, but it is not informative either -- and the gate would
    just ask again. Report instead."""
    idx, why = stronghold.answer_index("what colour is the sky?", ["Blue.", "Green."])
    assert idx is None and "known answer" in why


def test_an_unknown_question_is_still_answered_when_exactly_one_option_is_a_known_answer():
    """Jagex re-words these questions periodically (the wiki records a 2016 rewrite). As long as
    the ANSWER text survives, a re-worded question is still answerable."""
    idx, how = stronghold.answer_index("some brand new wording entirely?",
                                       ["Maybe.", "Use the Account Recovery system."])
    assert idx == 1 and "known answer" in how


def test_two_known_answers_in_one_menu_is_a_refusal_not_a_coin_flip():
    idx, why = stronghold.answer_index("brand new wording?", ["Nobody.", "Me."])
    assert idx is None and "several" in why


# --- recognising the dialogue --------------------------------------------------------------

def test_the_greeting_is_recognised_by_its_text_even_under_an_unknown_name():
    d = {"npcName": "Some Gate We Have Not Listed",
         "npcText": "Greetings Adventurer. This place is kept safe by the<br>spirits within "
                    "the doors."}
    assert stronghold.is_gate_dialogue(d)


def test_an_ordinary_dialogue_is_not_a_gate():
    assert not stronghold.is_gate_dialogue({"npcName": "Aya", "npcText": "'Ello, and what are "
                                                                         "you after then?"})
    assert not stronghold.is_gate_dialogue({})


def test_the_question_is_read_off_the_menu_title():
    """dialogue.options() drops child 0 because number keys address the real options -- but for
    these gates child 0 IS the question."""
    d = {"options": ["To pass you must answer me this: Who can I give my password to?",
                     "Nobody.", "My friend."]}
    assert stronghold._title(d).startswith("To pass")
    assert dialogue.options(d) == ["Nobody.", "My friend."]
    assert stronghold.is_gate_dialogue(d)


# --- driving one gate ------------------------------------------------------------------------

def test_handle_spaces_through_the_greeting_then_answers_the_question(monkeypatch):
    stages = [
        {"npcName": "Gate of War", "npcText": "Greetings Adventurer. This place is kept safe by "
                                              "the<br>spirits within the doors."},
        {"npcName": "Gate of War",
         "options": ["To pass you must answer me this: Who can I give my<br>password to?",
                     "My friend.", "Nobody."]},
        {},
    ]
    monkeypatch.setattr(dialogue, "read", lambda: stages[0] if stages else {})
    keys = []

    def tap(ser, key):
        keys.append(key)
        stages.pop(0)
    monkeypatch.setattr(controller, "key_tap", tap)
    monkeypatch.setattr(stronghold.time, "sleep", lambda s: None)

    assert stronghold.handle(_Ctx()) is True
    assert keys == ["SPACE", "2"], "continue, then the number key of 'Nobody.'"


def test_handle_refuses_to_guess_and_says_what_it_saw(monkeypatch):
    d = {"npcName": "Gate of War",
         "options": ["To pass you must answer me this: A brand new question?",
                     "Left.", "Right."]}
    monkeypatch.setattr(dialogue, "read", lambda: d)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(stronghold.time, "sleep", lambda s: None)
    logged = []
    monkeypatch.setattr(stronghold.debug_log, "log", lambda tag, msg: logged.append(msg))

    assert stronghold.handle(_Ctx()) is False
    assert keys == [], "no key is pressed at a question we do not know"
    assert any("Left." in m and "Right." in m for m in logged), "the options are reported"


def test_handle_returns_at_once_when_there_is_no_gate(monkeypatch):
    monkeypatch.setattr(dialogue, "read", lambda: {"npcName": "Aya", "npcText": "Hello."})
    monkeypatch.setattr(controller, "key_tap",
                        lambda *a: pytest.fail("it must not press anything"))
    assert stronghold.handle(_Ctx()) is False


def test_handle_is_bounded_when_the_dialogue_never_advances(monkeypatch):
    """A gate stuck on a continue stage must cost a budget, not the run."""
    monkeypatch.setattr(dialogue, "read",
                        lambda: {"npcName": "Gate of War", "npcText": "Greetings Adventurer. "
                                                                      "This place is kept safe "
                                                                      "by the spirits within "
                                                                      "the doors."})
    monkeypatch.setattr(controller, "key_tap", lambda *a: None)
    clock = {"t": 0.0}
    monkeypatch.setattr(stronghold.time, "time", lambda: clock["t"])
    monkeypatch.setattr(stronghold.time, "sleep",
                        lambda s: clock.__setitem__("t", clock["t"] + max(s, 0.1)))
    assert stronghold.handle(_Ctx(), budget_s=5.0) is False
    assert clock["t"] >= 5.0
