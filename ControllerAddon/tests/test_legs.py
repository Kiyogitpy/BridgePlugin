import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import crossfloor
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




def test_await_arrival_polls_the_player_until_within_the_landing_radius(monkeypatch):
    reads = iter([{}, {"x": 3000, "y": 3000, "plane": 0}, {"x": 3421, "y": 3540, "plane": 0}])
    monkeypatch.setattr(controller, "get_player", lambda: next(reads))
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    assert legs.await_arrival((3421, 3537, 0), budget_s=2.0) is True


def test_await_arrival_gives_up_at_the_budget(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 1, "plane": 0})
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    assert legs.await_arrival((3421, 3537, 0), budget_s=1.0) is False


def _route(*steps):
    return crossfloor.Route(steps, 0)


def test_walk_across_replans_from_the_live_position_after_every_transport(monkeypatch):
    """The plan is a hint, the player's real tile is the truth: after each transport the route is
    asked for again from wherever we actually landed."""
    cave = _step()
    positions = iter([{"x": 2431, "y": 3422, "plane": 0},      # at the ring landing
                      {"x": 2429, "y": 9824, "plane": 0},      # after the cave
                      {"x": 2420, "y": 9786, "plane": 0}])     # arrived
    current = {}

    def get_player():
        current.update(next(positions, current))
        return dict(current)
    monkeypatch.setattr(controller, "get_player", get_player)
    monkeypatch.setattr(controller, "get_skills", lambda: {"Agility": {"level": 60}})
    asked = []

    def find_route(start, goal, **kw):
        asked.append((start, kw.get("skill_levels")))
        if start[1] < 9000:
            return _route(crossfloor.Walk((2430, 3423, 0), 2), crossfloor.Link(cave, (2430, 3423, 0)),
                          crossfloor.Walk((2420, 9786, 0), 38))
        return _route(crossfloor.Walk((2420, 9786, 0), 38))
    monkeypatch.setattr(crossfloor, "find_route", find_route)
    ran = []
    monkeypatch.setattr(legs, "run_transport", lambda ctx, step, log="travel", approach=None: ran.append(step.object_info) or True)
    walks = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        walks.append((target, goal_adjacent)) or True)

    assert legs.walk_across(_Ctx(), (2420, 9785, 0)) is True
    assert ran == ["Enter Cave 26709"]
    assert [a[0] for a in asked] == [(2431, 3422, 0), (2429, 9824, 0)]
    assert asked[0][1] == {"Agility": 60}
    assert walks == [({"x": 2420, "y": 9786, "plane": 0}, False)], \
        "the pre-link walk is run_transport's job; the last stretch walks to crossfloor's own tile"


def test_a_walkable_goal_is_walked_to_exactly_on_the_real_map():
    """crossfloor only relocates a goal that is NOT walkable, so a walk leg to a walkable tile
    still means that exact tile (the task-3 review's concern, answered by the search itself)."""
    route = crossfloor.find_route((2899, 3553, 0), (2890, 3545, 0))
    assert route is not None and route.steps[-1].dest == (2890, 3545, 0)


def test_the_transport_is_handed_crossfloors_own_approach_tile(monkeypatch):
    """FINAL REVIEW: 87 usable transports have a solid origin with no walkable cardinal neighbour.
    crossfloor already knows a reachable tile beside each one; dropping it made those unwalkable."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3422, "plane": 0})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(crossfloor, "find_route", lambda start, goal, **kw:
                        _route(crossfloor.Link(_step(), (2431, 3423, 0)), crossfloor.Walk((2420, 9786, 0), 38)))
    seen = []
    monkeypatch.setattr(legs, "run_transport", lambda ctx, step, log="travel", approach=None: seen.append(approach) or False)
    legs.walk_across(_Ctx(), (2420, 9785, 0))
    assert seen == [(2431, 3423, 0)]


def test_run_transport_walks_to_the_approach_tile_first(monkeypatch):
    walks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2440, "y": 3420, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        walks.append((target["x"], target["y"])) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(legs, "await_landing", lambda step, budget_s=None: True)
    assert legs.run_transport(_Ctx(), _step(), approach=(2431, 3423, 0)) is True
    assert walks == [(2431, 3423)]


def test_standing_at_the_origin_of_a_short_hop_is_not_a_landing():
    """FINAL REVIEW, Critical: the Fremennik dungeon's Strange floor moves the player TWO tiles on
    one plane, so 'within 3 of the destination' was already true before the click."""
    hop = _step("Jump-over Strange floor 16544", origin=(2775, 10003, 0), dest=(2773, 10003, 0))
    assert legs.landed(hop, {"x": 2775, "y": 10003, "plane": 0}) is False      # still at the origin
    assert legs.landed(hop, {"x": 2776, "y": 10003, "plane": 0}) is False      # beside it, near side
    assert legs.landed(hop, {"x": 2774, "y": 10003, "plane": 0}) is False      # mid-jump: equidistant
    assert legs.landed(hop, {"x": 2773, "y": 10003, "plane": 0}) is True
    assert legs.landed(hop, {"x": 2772, "y": 10003, "plane": 0}) is True       # a step past it
    assert legs.landed(hop, {}) is False and legs.landed(hop, None) is False


def test_a_floor_change_lands_on_the_plane_and_within_three_tiles():
    ladder = _step("Climb-down Ladder 30191", origin=(3417, 3536, 0), dest=(3412, 9932, 3))
    assert legs.landed(ladder, {"x": 3412, "y": 9932, "plane": 3}) is True
    assert legs.landed(ladder, {"x": 3412, "y": 9932, "plane": 0}) is False
    assert legs.landed(ladder, {"x": 3420, "y": 9932, "plane": 3}) is False
    cave = _step()                                                              # same plane, 6400 north
    assert legs.landed(cave, {"x": 2429, "y": 9825, "plane": 0}) is True
    assert legs.landed(cave, {"x": 2430, "y": 3424, "plane": 0}) is False


def test_await_landing_polls_until_landed_or_the_budget(monkeypatch):
    hop = _step("Jump-over Strange floor 16544", origin=(2775, 10003, 0), dest=(2773, 10003, 0))
    reads = iter([{"x": 2775, "y": 10003, "plane": 0}, {"x": 2774, "y": 10003, "plane": 0},
                  {"x": 2773, "y": 10003, "plane": 0}])
    monkeypatch.setattr(controller, "get_player", lambda: next(reads))
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    assert legs.await_landing(hop, budget_s=2.0) is True
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2775, "y": 10003, "plane": 0})
    assert legs.await_landing(hop, budget_s=0.3) is False


def test_a_link_that_leads_straight_back_over_the_last_one_is_refused(monkeypatch):
    """A re-plan starts from scratch; a start snapped onto the wrong side of a short obstacle
    must not bounce the player back and forth until the cap."""
    there = _step("Jump-over Strange floor 16544", origin=(2775, 10003, 0), dest=(2773, 10003, 0))
    back = _step("Jump-over Strange floor 16544", origin=(2773, 10003, 0), dest=(2775, 10003, 0))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2775, "y": 10003, "plane": 0})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    plans = iter([_route(crossfloor.Link(there, (2775, 10003, 0)), crossfloor.Walk((2738, 10008, 0), 50)),
                  _route(crossfloor.Link(back, (2773, 10003, 0)), crossfloor.Walk((2738, 10008, 0), 50))])
    monkeypatch.setattr(crossfloor, "find_route", lambda start, goal, **kw: next(plans))
    ran = []
    monkeypatch.setattr(legs, "run_transport", lambda ctx, step, log="travel", approach=None: ran.append(step.dest) or True)
    assert legs.walk_across(_Ctx(), (2738, 10008, 0)) is False
    assert ran == [(2773, 10003, 0)], "the second, backward jump is never clicked"


def test_walk_across_is_false_when_there_is_no_route(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3221, "y": 3218, "plane": 0})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(crossfloor, "find_route", lambda start, goal, **kw: None)
    walks = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        walks.append(target) or False)
    assert legs.walk_across(_Ctx(), (3178, 2447, 0)) is False
    assert walks == [{"x": 3178, "y": 2447, "plane": 0}], "same plane: the plain walk still gets its try"


def test_no_route_to_another_floor_or_a_dungeon_never_tries_a_plain_walk(monkeypatch):
    """A dungeon is drawn 6400 tiles north on the SAME plane; a plain walk there is nonsense."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3422, "plane": 0})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(crossfloor, "find_route", lambda start, goal, **kw: None)
    walks = []
    monkeypatch.setattr(travel, "walk_to", lambda *a, **k: walks.append(1) or True)
    assert legs.walk_across(_Ctx(), (2420, 9785, 0)) is False
    assert legs.walk_across(_Ctx(), (2431, 3422, 1)) is False
    assert walks == []


def test_walk_across_stops_when_a_transport_fails(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3422, "plane": 0})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(crossfloor, "find_route", lambda start, goal, **kw:
                        _route(crossfloor.Link(_step(), (2430, 3423, 0)), crossfloor.Walk((2420, 9786, 0), 38)))
    monkeypatch.setattr(legs, "run_transport", lambda ctx, step, log="travel", approach=None: False)
    assert legs.walk_across(_Ctx(), (2420, 9785, 0)) is False


def test_walk_across_cannot_loop_forever(monkeypatch):
    """A transport that 'succeeds' but never moves us must not spin."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3422, "plane": 0})
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(crossfloor, "find_route", lambda start, goal, **kw:
                        _route(crossfloor.Link(_step(), (2430, 3423, 0)), crossfloor.Walk((2420, 9786, 0), 38)))
    ran = []
    monkeypatch.setattr(legs, "run_transport", lambda ctx, step, log="travel", approach=None: ran.append(1) or True)
    assert legs.walk_across(_Ctx(), (2420, 9785, 0)) is False
    assert len(ran) == legs.WALK_ACROSS_MAX_LINKS


def test_skill_levels_is_none_when_the_bridge_gave_nothing(monkeypatch):
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    assert legs.skill_levels() is None
    monkeypatch.setattr(controller, "get_skills", lambda: {"Agility": {"level": 71, "boosted": 74}, "Mining": {"level": 60}})
    assert legs.skill_levels() == {"Agility": 71, "Mining": 60}


def test_a_walk_leg_now_goes_through_walk_across(monkeypatch):
    seen = []
    monkeypatch.setattr(legs, "walk_across", lambda ctx, goal, log="travel": seen.append(tuple(goal)) or True)
    assert legs.run_next(_Ctx(), [legs.walk(3434, 9938, 3)], 0) == (legs.DONE, 1)
    assert seen == [(3434, 9938, 3)]


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
    monkeypatch.setattr(legs, "await_landing", lambda step, budget_s=None:
                        calls.append(("await", step.dest)) or True)

    assert legs.run_transport(_Ctx(), _step()) is True
    assert calls[0] == ("walk", {"x": 2430, "y": 3424, "plane": 0}, False)
    assert calls[1] == ("click", 26709, "Enter", "REFRESH", False)
    assert calls[2] == ("await", (2429, 9824, 0))


def test_run_transport_falls_back_to_goal_adjacent_when_the_origin_tile_is_solid(monkeypatch):
    walks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2440, "y": 3420, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        walks.append(goal_adjacent) or goal_adjacent)     # only the adjacent walk succeeds
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(legs, "await_landing", lambda step, budget_s=None: True)
    assert legs.run_transport(_Ctx(), _step()) is True
    assert walks == [False, True]


def test_run_transport_skips_the_walk_when_already_beside_the_origin(monkeypatch):
    walks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2431, "y": 3423, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda *a, **k: walks.append(1) or True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_cave_object()])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(legs, "await_landing", lambda step, budget_s=None: True)
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
    monkeypatch.setattr(legs, "await_landing", lambda step, budget_s=None: True)
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


def test_a_fairy_leg_carries_its_ring_tile_and_id_through_to_the_hop(monkeypatch):
    seen = {}

    def hop(ctx, code, expect, **kw):
        seen.update(kw, code=code, expect=expect)
        return True
    monkeypatch.setattr(travel, "hop_fairy_ring", hop)
    leg = legs.fairy("AJQ", "Dorgesh", ring_tile=(2740, 3351, 0), ring_id=29495)
    assert leg["ring_tile"] == [2740, 3351, 0] and leg["ring_id"] == 29495
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert seen["ring_tile"] == {"x": 2740, "y": 3351, "plane": 0} and seen["ring_ids"] == (29495,)


def test_a_plain_fairy_leg_still_calls_the_hop_exactly_as_before(monkeypatch):
    """banker and karambwan build fairy legs with no ring: nothing new may be passed."""
    seen = {}
    monkeypatch.setattr(travel, "hop_fairy_ring", lambda ctx, code, expect, **kw: seen.update(kw) or True)
    assert legs.run_next(_Ctx(), [legs.fairy("cks", "Slayer Tower")], 0) == (legs.DONE, 1)
    assert set(seen) == {"log"}
