import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import legs
import travel


class _State:
    def __init__(self):
        self.data = {}
        self.stops = []

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)


class _Ctx:
    def __init__(self):
        self.ser = None
        self.state = _State()
        self.events = None


def test_builders_produce_the_flat_dict_shape_banker_already_stores():
    assert legs.item("slayer ring", "Slayer Tower", (3421, 3537, 0)) == {
        "kind": "item", "item": "slayer ring", "option": "Slayer Tower",
        "display": "slayer ring: Slayer Tower", "dest": [3421, 3537, 0],
        "origin": None, "object_id": None, "object_info": ""}
    assert legs.walk(3429, 3564, 0)["kind"] == "walk"
    assert legs.walk(3429, 3564, 0)["dest"] == [3429, 3564, 0]
    f = legs.fairy("cks", "Slayer Tower")
    assert f["kind"] == "fairy" and f["code"] == "cks" and f["expect"] == "Slayer Tower"


def test_run_next_advances_only_on_done_and_reports_finished_past_the_end(monkeypatch):
    calls = []
    monkeypatch.setattr(legs, "run_leg", lambda ctx, leg, log="travel": calls.append(leg["display"]) or True)
    chain = [legs.item("ring of dueling", "Ferox Enclave", (3151, 3635, 0)),
             legs.item("games necklace", "Burthorpe", (2898, 3553, 0))]
    ctx = _Ctx()

    assert legs.run_next(ctx, chain, 0) == (legs.DONE, 1)
    assert legs.run_next(ctx, chain, 1) == (legs.DONE, 2)
    assert legs.run_next(ctx, chain, 2) == (legs.FINISHED, 2)
    assert calls == ["ring of dueling: Ferox Enclave", "games necklace: Burthorpe"]


def test_a_failed_item_leg_keeps_the_cursor_so_the_caller_can_retry_or_stop(monkeypatch):
    monkeypatch.setattr(legs, "run_leg", lambda ctx, leg, log="travel": False)
    chain = [legs.item("slayer ring", "Slayer Tower", (3421, 3537, 0))]
    assert legs.run_next(_Ctx(), chain, 0) == (legs.FAILED, 0)


def test_a_fairy_leg_reports_progress_not_failure_while_the_hop_is_still_underway(monkeypatch):
    """travel.hop_fairy_ring is ONE bounded action per call: False means 'walked toward the
    ring / dialled, come back next tick', and a hard failure is its own safety stop."""
    answers = iter([False, False, True])
    seen = []
    monkeypatch.setattr(travel, "hop_fairy_ring",
                        lambda ctx, code, expect, **kw: seen.append((code, expect)) or next(answers))
    chain = [legs.fairy("cks", "Slayer Tower")]
    ctx = _Ctx()
    assert legs.run_next(ctx, chain, 0) == (legs.PROGRESS, 0)
    assert legs.run_next(ctx, chain, 0) == (legs.PROGRESS, 0)
    assert legs.run_next(ctx, chain, 0) == (legs.DONE, 1)
    assert seen == [("cks", "Slayer Tower")] * 3


def test_a_fairy_leg_with_a_dest_confirms_the_landing_before_reporting_done(monkeypatch):
    """Unlike a fairy leg with no dest, one built WITH a dest (legs.fairy(code, expect, dest))
    must not report DONE off the hop's Confirm click alone -- it has to confirm arrival."""
    monkeypatch.setattr(travel, "hop_fairy_ring", lambda ctx, code, expect, **kw: True)
    seen = []
    monkeypatch.setattr(legs, "await_arrival",
                        lambda dest, budget_s=legs.ITEM_BUDGET_S: seen.append((dest, budget_s)) or True)
    chain = [legs.fairy("cks", "Slayer Tower", dest=(3421, 3537, 0))]
    assert legs.run_next(_Ctx(), chain, 0) == (legs.DONE, 1)
    assert seen == [([3421, 3537, 0], legs.FAIRY_BUDGET_S)]


def test_a_fairy_leg_with_a_dest_fails_when_the_landing_never_confirms(monkeypatch):
    monkeypatch.setattr(travel, "hop_fairy_ring", lambda ctx, code, expect, **kw: True)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=legs.ITEM_BUDGET_S: False)
    chain = [legs.fairy("cks", "Slayer Tower", dest=(3421, 3537, 0))]
    assert legs.run_next(_Ctx(), chain, 0) == (legs.FAILED, 0)


def test_a_walk_leg_walks_to_the_exact_tile(monkeypatch):
    walks = []
    monkeypatch.setattr(travel, "walk_to",
                        lambda ctx, target, tolerance=3, goal_adjacent=True: walks.append((target, goal_adjacent)) or True)
    assert legs.run_next(_Ctx(), [legs.walk(3429, 3564, 0)], 0) == (legs.DONE, 1)
    assert walks == [({"x": 3429, "y": 3564, "plane": 0}, False)]


def test_await_arrival_polls_the_player_until_within_the_landing_radius(monkeypatch):
    reads = iter([{}, {"x": 3000, "y": 3000, "plane": 0}, {"x": 3421, "y": 3540, "plane": 0}])
    monkeypatch.setattr(controller, "get_player", lambda: next(reads))
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    assert legs.await_arrival((3421, 3537, 0), budget_s=2.0) is True


def test_await_arrival_gives_up_at_the_budget(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 1, "plane": 0})
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    assert legs.await_arrival((3421, 3537, 0), budget_s=1.0) is False
