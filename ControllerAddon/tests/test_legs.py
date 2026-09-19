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


import transports


def _step(info="Enter Cave 26709", origin=(2430, 3424, 0), dest=(2429, 9824, 0)):
    return transports.Step(origin, dest, info, {}, "", (), (), (), 0, "")


def _cave_object(**over):
    obj = {"id": 26709, "name": "Cave", "x": 2429, "y": 3425, "plane": 0, "screenX": 500, "screenY": 300}
    obj.update(over)
    return obj


def test_run_transport_walks_clicks_the_object_by_id_and_confirms_the_landing(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2440, "y": 3420, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        calls.append(("walk", target, goal_adjacent)) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [_cave_object(id=1, name="Tree"), _cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id, radius=None, inst=False: "REFRESH")

    def click_entity(ser, entity, **kw):
        calls.append(("click", entity["id"], kw.get("expected_action"), kw.get("refresh_target"), kw.get("simulate_mistakes")))
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=None, radius=None:
                        calls.append(("await", tuple(dest), budget_s, radius)) or True)

    assert legs.run_transport(_Ctx(), _step()) is True
    assert calls[0] == ("walk", {"x": 2430, "y": 3424, "plane": 0}, False)
    assert calls[1] == ("click", 26709, "Enter", "REFRESH", False)
    assert calls[2] == ("await", (2429, 9824, 0), legs.LINK_BUDGET_S, legs.LINK_ARRIVAL_TILES)


def test_run_transport_falls_back_to_goal_adjacent_when_the_origin_tile_is_solid(monkeypatch):
    walks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2440, "y": 3420, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        walks.append(goal_adjacent) or goal_adjacent)     # only the adjacent walk succeeds
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=None, radius=None: True)
    assert legs.run_transport(_Ctx(), _step()) is True
    assert walks == [False, True]


def test_run_transport_skips_the_walk_when_already_beside_the_origin(monkeypatch):
    walks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3423, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda *a, **k: walks.append(1) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=None, radius=None: True)
    assert legs.run_transport(_Ctx(), _step()) is True
    assert walks == []


def test_run_transport_uses_the_menu_when_the_left_click_is_withheld(monkeypatch):
    class _Withheld:
        withheld = True

        def __bool__(self):
            return False
    menu = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3423, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: _Withheld())
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: menu.append(option) or True)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=None, radius=None: True)
    assert legs.run_transport(_Ctx(), _step()) is True
    assert menu == ["Enter"]


def test_run_transport_fails_cleanly_when_the_object_is_not_there(monkeypatch):
    clicked = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3423, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: clicked.append(1) or True)
    assert legs.run_transport(_Ctx(), _step()) is False
    assert not clicked


def test_an_id_less_row_finds_its_object_by_name_nearest_the_origin(monkeypatch):
    far = {"id": 5, "name": "Staircase", "x": 2810, "y": 2793, "plane": 0, "screenX": 1, "screenY": 1}
    near = {"id": 6, "name": "Staircase", "x": 2795, "y": 2794, "plane": 0, "screenX": 2, "screenY": 2}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [far, near])
    step = _step("Climb-up Staircase", origin=(2795, 2793, 0), dest=(2795, 2797, 1))
    assert step.object_id is None
    assert legs.find_transport_object(step)["id"] == 6


def test_await_arrival_honours_a_tighter_radius(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2435, "y": 9824, "plane": 0})
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    assert legs.await_arrival((2429, 9824, 0), budget_s=0.6) is True          # default radius 12
    assert legs.await_arrival((2429, 9824, 0), budget_s=0.6, radius=3) is False
