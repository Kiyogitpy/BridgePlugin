import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bank_memory
import controller


def _reply(items, seen=True, captured_at=1000.0, source="live", item_count=None):
    return {
        "accountHash": "acct",
        "seen": seen,
        "capturedAt": int(captured_at * 1000),
        "capturedTick": 42,
        "source": source,
        "itemCount": (len(items) if items is not None else 0) if item_count is None else item_count,
        "items": items,
    }


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Hermetic by default -- an unstubbed test must never reach the running client."""
    monkeypatch.setattr(controller, "get_bank_memory",
                        lambda names=None: pytest.fail("test reached the live bridge"))


def _stub(monkeypatch, reply):
    seen = {}

    def fake(names=None):
        seen["names"] = names
        return reply

    monkeypatch.setattr(controller, "get_bank_memory", fake)
    return seen


def test_never_seen_is_not_an_empty_bank(monkeypatch):
    """The m8aq lesson in CLAUDE.md, applied here: 'we have never opened this bank' and 'the
    bank is empty' are different answers. Collapsing them lets a routine conclude it owns no
    teleport when it simply hasn't looked yet -- and then walk home across the map."""
    _stub(monkeypatch, _reply(None, seen=False))
    mem = bank_memory.load()

    assert not mem.seen
    assert not mem.has("varrock teleport")
    # ...and the caller can tell the two apart, which is the whole point.
    assert mem.quantity("varrock teleport") is None

    _stub(monkeypatch, _reply([]))
    empty = bank_memory.load()
    assert empty.seen
    assert empty.quantity("varrock teleport") == 0


def test_bridge_unavailable_reads_as_never_seen_not_as_empty(monkeypatch):
    """An old client build has no /bank/memory at all; api_get answers None. That must not
    become 'your bank is empty'."""
    _stub(monkeypatch, None)
    mem = bank_memory.load()
    assert not mem.seen
    assert mem.quantity("shark") is None


def test_has_and_quantity_match_case_insensitive_substring(monkeypatch):
    """Same convention as banker.py's keepItems, so the two agree about what a name means."""
    _stub(monkeypatch, _reply([
        {"id": 8007, "name": "Varrock teleport", "quantity": 112},
        {"id": 385, "name": "Shark", "quantity": 3},
    ]))
    mem = bank_memory.load()

    assert mem.has("varrock teleport")
    assert mem.has("VARROCK")
    assert mem.quantity("shark") == 3
    assert mem.quantity("Falador teleport") == 0
    assert not mem.has("falador teleport")


def test_find_returns_the_matching_rows_for_a_whole_candidate_list(monkeypatch):
    """The teleport case: ask about a dozen names in ONE call rather than a dozen calls."""
    _stub(monkeypatch, _reply([
        {"id": 8007, "name": "Varrock teleport", "quantity": 112},
        {"id": 8009, "name": "Falador teleport", "quantity": 5},
        {"id": 385, "name": "Shark", "quantity": 3},
    ]))
    mem = bank_memory.load()

    hits = mem.find(["falador teleport", "varrock teleport", "camelot teleport"])
    assert [h["name"] for h in hits] == ["Varrock teleport", "Falador teleport"]


def test_load_with_names_pushes_the_filter_to_the_bridge(monkeypatch):
    """~950 items is 60-80KB; a narrow poll shouldn't drag the whole bank across the wire."""
    seen = _stub(monkeypatch, _reply(
        [{"id": 8007, "name": "Varrock teleport", "quantity": 1}], item_count=947))
    mem = bank_memory.load(names=["varrock teleport", "falador teleport"])

    assert seen["names"] == ["varrock teleport", "falador teleport"]
    assert mem.has("varrock teleport")
    # itemCount is the WHOLE bank, so a filtered reply can't be mistaken for a tiny bank.
    assert mem.item_count == 947


def test_age_is_reported_so_callers_can_set_their_own_staleness_policy(monkeypatch):
    _stub(monkeypatch, _reply([], captured_at=1000.0))
    monkeypatch.setattr(bank_memory, "_now", lambda: 1030.0)

    mem = bank_memory.load()
    assert mem.age_s == pytest.approx(30.0)


def test_age_is_none_when_nothing_was_ever_captured(monkeypatch):
    _stub(monkeypatch, _reply(None, seen=False))
    assert bank_memory.load().age_s is None


def test_an_unrebuilt_client_falls_through_to_slash_bank_and_still_reads_as_never_seen(monkeypatch):
    """MEASURED against the live bridge 2026-09-07, before the rebuild: GET /bank/memory
    returned {"open": false} -- com.sun.net.httpserver matches by LONGEST PREFIX, so with no
    /bank/memory context registered the request falls through to /bank's handler and answers
    200 with a bank-shaped payload rather than a 404.

    (/widget and /widget/children were confirmed coexisting on the same live bridge, so once
    /bank/memory is registered it wins the match and this stops happening.)

    The important part is that the wrong-shaped 200 must not read as an empty bank."""
    _stub(monkeypatch, {"open": False})
    mem = bank_memory.load()

    assert not mem.seen
    assert mem.quantity("varrock teleport") is None
    assert mem.items == []
