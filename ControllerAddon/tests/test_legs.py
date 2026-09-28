import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import crossfloor
import legs
import poh
import travel


import pytest


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """HERMETIC, enforced: a forgotten fake must fail loudly, not read the user's live client."""
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)
    # walk_across reads what the account can prove (legs.requirement_values). By default a test
    # proves nothing: no varbit, no inventory. A test about those reads overrides these.
    monkeypatch.setattr(controller, "get_varbit", lambda vid: None)
    monkeypatch.setattr(controller, "get_varp", lambda vid: None)
    monkeypatch.setattr(controller, "get_vars", lambda ids, kind="varbit": {})
    monkeypatch.setattr(controller, "get_inventory", lambda: None)
    monkeypatch.setattr(controller, "get_equipment", lambda: None)
    monkeypatch.setattr(controller, "get_api", lambda group, reader: None)


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


def test_house_builders_produce_the_flat_dict_shape():
    assert legs.house() == {
        "kind": "poh_enter", "display": "house tablet", "dest": None,
        "item": None, "option": None, "origin": None, "object_id": None, "object_info": ""}
    assert legs.pool() == {
        "kind": "poh_pool", "display": "house pool", "dest": None,
        "item": None, "option": None, "origin": None, "object_id": None, "object_info": ""}
    box = legs.house_box("Cooking Guild", (3143, 3442, 0))
    assert box["kind"] == "poh_box" and box["display"] == "jewellery box: Cooking Guild"
    assert box["option"] == "Cooking Guild" and box["dest"] == [3143, 3442, 0]
    ring = legs.house_ring("AJQ", "Dorgesh", dest=(2735, 5221, 0))
    assert ring["kind"] == "poh_ring" and ring["display"] == "house fairy ring AJQ"
    assert ring["code"] == "AJQ" and ring["expect"] == "Dorgesh" and ring["dest"] == [2735, 5221, 0]
    assert legs.house_ring("AJQ", "Dorgesh")["dest"] is None


def test_a_poh_enter_leg_delegates_to_poh_enter(monkeypatch):
    seen = []
    monkeypatch.setattr(poh, "enter", lambda ctx, log: seen.append(log) or True)
    assert legs.run_next(_Ctx(), [legs.house()], 0) == (legs.DONE, 1)
    assert seen == ["travel"]
    monkeypatch.setattr(poh, "enter", lambda ctx, log: False)
    assert legs.run_next(_Ctx(), [legs.house()], 0) == (legs.FAILED, 0)


def test_a_poh_pool_leg_delegates_to_poh_drink_pool_and_never_fails_when_it_returns_true(monkeypatch):
    seen = []
    monkeypatch.setattr(poh, "drink_pool", lambda ctx, log: seen.append(log) or True)
    assert legs.run_next(_Ctx(), [legs.pool()], 0) == (legs.DONE, 1)
    assert seen == ["travel"]
    monkeypatch.setattr(poh, "drink_pool", lambda ctx, log: False)
    assert legs.run_next(_Ctx(), [legs.pool()], 0) == (legs.FAILED, 0)


IN_HOUSE = {"x": 1933, "y": 7105, "plane": 1, "instPlane": 0, "inInstance": True,
            "instX": 11669, "instY": 1449, "regionId": 7791}          # MEASURED 2026-09-20


@pytest.fixture
def in_the_house(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(IN_HOUSE))


def test_a_poh_box_leg_confirms_arrival_after_use_box_succeeds(monkeypatch, in_the_house):
    seen = {}
    monkeypatch.setattr(poh, "use_box", lambda ctx, option, log: seen.setdefault("option", option) or True)
    monkeypatch.setattr(poh, "await_left_house", lambda budget_s: seen.setdefault("budget", budget_s) or True)
    leg = legs.house_box("Cooking Guild", (3143, 3442, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert seen == {"option": "Cooking Guild", "budget": poh.ENTER_BUDGET_S}, \
        "confirmed by LEAVING THE HOUSE, not by a landing tile our own tables disagree about"


def test_a_poh_box_leg_fails_when_use_box_fails(monkeypatch, in_the_house):
    monkeypatch.setattr(poh, "use_box", lambda ctx, option, log: False)
    leg = legs.house_box("Cooking Guild", (3143, 3442, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0)


def test_a_poh_box_leg_fails_when_the_landing_never_confirms(monkeypatch, in_the_house):
    monkeypatch.setattr(poh, "use_box", lambda ctx, option, log: True)
    monkeypatch.setattr(poh, "await_left_house", lambda budget_s: False)
    leg = legs.house_box("Cooking Guild", (3143, 3442, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0)


def test_a_house_hop_that_landed_after_the_wait_is_done_on_the_retry_not_failed(monkeypatch):
    """Phase-B fix-wave re-review: the landing outran the wait, the leg FAILED, and on the retry
    the box is not in the scene out here -- three FAILs and a safety stop after a GOOD teleport.
    The mounted pendant leg has the same shape."""
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3143, "y": 3442, "plane": 0, "inInstance": False})
    used = []
    monkeypatch.setattr(poh, "use_box", lambda ctx, option, log: used.append(1) or False)
    monkeypatch.setattr(poh, "use_mounted", lambda ctx, name, action, log: used.append(2) or False)
    assert legs.run_next(_Ctx(), [legs.house_box("Cooking Guild", (3143, 3442, 0))], 0) == (legs.DONE, 1)
    assert legs.run_next(_Ctx(), [legs.house_mounted("digsite pendant", "Digsite", (3341, 3445, 0))], 0) \
        == (legs.DONE, 1)
    assert used == [], "no second click on an object that is not there"


def test_a_house_hop_leg_on_an_empty_player_read_does_not_count_as_hopped(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(poh, "use_box", lambda ctx, option, log: False)
    assert legs.run_next(_Ctx(), [legs.house_box("Cooking Guild", (3143, 3442, 0))], 0) == (legs.FAILED, 0)


def test_a_tablet_landing_still_pending_is_progress_not_a_failure(monkeypatch):
    """Phase-B fix-wave re-review: enter() refuses a second tablet for 60 s, so a slow landing
    used to spend all three FAILED retries in about a second."""
    monkeypatch.setattr(poh, "enter", lambda ctx, log: False)
    monkeypatch.setattr(poh, "landing_pending", lambda ctx: True)
    assert legs.run_next(_Ctx(), [legs.house()], 0) == (legs.PROGRESS, 0)
    monkeypatch.setattr(poh, "landing_pending", lambda ctx: False)
    assert legs.run_next(_Ctx(), [legs.house()], 0) == (legs.FAILED, 0)


def test_a_poh_ring_leg_reports_progress_while_the_hop_is_underway(monkeypatch):
    answers = iter([False, False, True])
    seen = []
    monkeypatch.setattr(poh, "use_ring", lambda ctx, code, expect, log: seen.append((code, expect)) or next(answers))
    leg = legs.house_ring("AJQ", "Dorgesh")
    ctx = _Ctx()
    assert legs.run_next(ctx, [leg], 0) == (legs.PROGRESS, 0)
    assert legs.run_next(ctx, [leg], 0) == (legs.PROGRESS, 0)
    assert legs.run_next(ctx, [leg], 0) == (legs.DONE, 1)
    assert seen == [("AJQ", "Dorgesh")] * 3


def test_a_poh_ring_leg_with_a_dest_confirms_the_landing(monkeypatch):
    monkeypatch.setattr(poh, "use_ring", lambda ctx, code, expect, log: True)
    seen = []
    monkeypatch.setattr(legs, "await_arrival",
                        lambda dest, budget_s=legs.ITEM_BUDGET_S: seen.append((dest, budget_s)) or True)
    leg = legs.house_ring("AJQ", "Dorgesh", dest=(2735, 5221, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert seen == [([2735, 5221, 0], legs.FAIRY_BUDGET_S)]


def test_a_poh_ring_leg_with_a_dest_fails_when_the_landing_never_confirms(monkeypatch):
    monkeypatch.setattr(poh, "use_ring", lambda ctx, code, expect, log: True)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=legs.ITEM_BUDGET_S: False)
    leg = legs.house_ring("AJQ", "Dorgesh", dest=(2735, 5221, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0)


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


def test_landing_on_a_fairy_rings_own_tile_steps_off_before_any_routing(monkeypatch):
    """PHASE-A REVIEW, proven on the REAL map: the player lands ON the AJQ exit ring (2735,5221),
    a one-tile flood. crossfloor plans from its walkable neighbour, but travel.walk_to routes from
    the LIVE tile and found nothing -- every fairy journey would have stranded at the exit ring."""
    position = {"x": 2735, "y": 5221, "plane": 0}
    monkeypatch.setattr(controller, "get_player", lambda: dict(position))
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    calls = []

    def walk_to_exact_tile(ctx, x, y, plane, wait_for_arrival=True):
        calls.append(("step", x, y))
        position.update(x=x, y=y)
        return True
    monkeypatch.setattr(travel, "walk_to_exact_tile", walk_to_exact_tile)
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, tolerance=3, goal_adjacent=True:
                        calls.append(("walk", target["x"], target["y"])) or True)
    assert legs.walk_across(_Ctx(), (2730, 5234, 0)) is True
    assert calls[0][0] == "step" and max(abs(calls[0][1] - 2735), abs(calls[0][2] - 5221)) <= crossfloor.START_TOLERANCE
    assert calls[1] == ("walk", 2730, 5234)


def test_a_tile_that_never_becomes_routable_gives_up_instead_of_clicking_forever(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2735, "y": 5221, "plane": 0})   # never moves
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    steps = []
    monkeypatch.setattr(travel, "walk_to_exact_tile", lambda ctx, x, y, plane, wait_for_arrival=True: steps.append(1) or True)
    monkeypatch.setattr(travel, "walk_to", lambda *a, **k: True)
    assert legs.walk_across(_Ctx(), (2730, 5234, 0)) is False
    assert len(steps) == legs.STEP_OFF_MAX


# --- 2026-09-20: walk_across proves what it can about the account before planning -------------

def test_requirement_values_come_from_the_bridge_and_unknown_stays_unknown(monkeypatch):
    """Fixture shapes are the REAL GETs of 2026-09-20: /inventory is a list of {id, qty, name},
    /equipment a dict keyed by slot. A varbit the bridge cannot read is LEFT OUT (unknown fails
    closed in transports.usable); it is never written as 0."""
    monkeypatch.setattr(legs.crossfloor, "requirement_ids", lambda: ({3759, 487}, {11}))
    # ONE read per kind since 2026-09-21 (controller.get_vars). An id the client cannot read is
    # ABSENT from the reply -- 487 below -- and must stay absent, never become 0.
    monkeypatch.setattr(controller, "get_vars",
                        lambda ids, kind="varbit": {3759: 3} if kind == "varbit" else {11: 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        {"qty": 1, "name": "Dusty key", "slot": 0, "id": 1590},
        {"qty": 2500, "name": "Coins", "slot": 1, "id": 995}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {
        "ring": {"qty": 1, "name": "Ring of shadows (uncharged)", "id": 28329}})
    values = legs.requirement_values()
    assert values["varbit_values"] == {3759: 3}
    assert values["varplayer_values"] == {11: 0}, "0 is a real answer for a varp"
    assert values["owned_item_ids"] == {1590, 995, 28329}
    assert values["currency_held"] == {"Coins": 2500}


def test_requirement_values_with_no_inventory_read_prove_no_items(monkeypatch):
    monkeypatch.setattr(legs.crossfloor, "requirement_ids", lambda: (set(), set()))
    monkeypatch.setattr(controller, "get_inventory", lambda: None)
    monkeypatch.setattr(controller, "get_equipment", lambda: None)
    values = legs.requirement_values()
    assert values["owned_item_ids"] is None and values["currency_held"] is None


def test_walk_across_hands_the_requirement_values_to_the_search(monkeypatch):
    seen = {}
    monkeypatch.setattr(legs, "skill_levels", lambda: {"Agility": 50})
    monkeypatch.setattr(legs, "requirement_values", lambda: {
        "varbit_values": {3759: 3}, "varplayer_values": {}, "owned_item_ids": {1590},
        "currency_held": {"Coins": 10}})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(legs.crossfloor, "walkable_start", lambda here, toward=None: here)

    def find_route(start, goal, **kwargs):
        seen.update(kwargs)
        return None
    monkeypatch.setattr(legs.crossfloor, "find_route", find_route)
    assert legs.walk_across(None, (3200, 9200, 0)) is False
    assert seen["varbit_values"] == {3759: 3} and seen["owned_item_ids"] == {1590}
    assert seen["currency_held"] == {"Coins": 10} and seen["skill_levels"] == {"Agility": 50}


# --- 2026-09-20: an item whose destination is chosen in a chat menu (the slayer ring) ----------
#
# The pattern is golem_crafting._teleport_home's, PROVEN LIVE there (2026-09-06 / 09-14): a
# verified left-click, else the item's own menu, then the PAGED destination dialogue picked by
# text. Cache obj 11866: iop2=Rub iop3=Teleport, sub-options Stronghold / Slayer Tower /
# Fremennik Dungeon / Tarn's Lair / Dark Beasts / Wyrmscraig Cavern.

RING = {"name": "Slayer ring (6)", "id": 11868, "slot": 3, "qty": 1, "screenX": 10, "screenY": 10}


def _ring_world(monkeypatch, left_click, menu=None, choose=True, arrived=True):
    calls = {"click": [], "menu": [], "choose": []}
    monkeypatch.setattr(controller, "get_inventory", lambda: [RING])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: None)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: calls["click"].append(kw.get("expected_action")) or left_click)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, action, **kw: calls["menu"].append(action) or (menu or {}).get(str(action), False))
    monkeypatch.setattr(legs.dialogue, "choose",
                        lambda ctx, needle, budget_s=None, log="dialogue": calls["choose"].append(needle) or choose)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=legs.ITEM_BUDGET_S: arrived)
    monkeypatch.setattr(legs, "_standing_near", lambda dest: False)
    return calls


def test_a_dialogue_item_leg_left_clicks_then_picks_the_destination_by_text(monkeypatch):
    calls = _ring_world(monkeypatch, left_click=True)
    leg = legs.dialogue_item("slayer ring", "Stronghold", "stronghold", (2431, 3422, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert calls["click"] == [("Stronghold", "Teleport", "Rub")], "the hover gate accepts any of the three"
    assert calls["choose"] == ["stronghold"] and calls["menu"] == []


def test_a_left_click_that_teleports_directly_skips_the_dialogue(monkeypatch):
    calls = _ring_world(monkeypatch, left_click=True)
    monkeypatch.setattr(legs, "_standing_near", lambda dest: True)
    leg = legs.dialogue_item("slayer ring", "Stronghold", "stronghold", (2431, 3422, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert calls["choose"] == []


def test_a_withheld_left_click_falls_back_to_the_items_own_menu(monkeypatch):
    calls = _ring_world(monkeypatch, left_click=False, menu={"('Teleport', 'Rub')": True})
    leg = legs.dialogue_item("slayer ring", "Slayer Tower", "slayer tower", (3421, 3537, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert calls["menu"] == ["Slayer Tower", ("Teleport", "Rub")], "the direct sub-option first, then the menu"
    assert calls["choose"] == ["slayer tower"]


def test_a_dialogue_that_never_offers_the_destination_fails_the_leg(monkeypatch):
    _ring_world(monkeypatch, left_click=False, menu={"('Teleport', 'Rub')": True}, choose=False)
    leg = legs.dialogue_item("slayer ring", "Slayer Tower", "slayer tower", (3421, 3537, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0)


def test_a_dialogue_item_leg_without_the_item_or_without_a_landing_fails(monkeypatch):
    _ring_world(monkeypatch, left_click=True, arrived=False)
    leg = legs.dialogue_item("slayer ring", "Stronghold", "stronghold", (2431, 3422, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0), "clicked, never landed"
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0), "no ring carried"


# --- 2026-09-20: a teleport SPELL, cast the way npc_contact casts NPC Contact ------------------
#
# npc_contact.cast is proven live (2026-09-19): magic tab, GET /widget for the spell, one click at
# offset (0,0). Moonclan Teleport is child 116 of the same interface 218 -- read from the cache,
# which gives NPC Contact as 218:113, the very constant measured live, so the mapping is checked.

def _spell_world(monkeypatch, tab=6, widget=None, click=True, arrived=True):
    calls = {"keys": [], "click": []}
    monkeypatch.setattr(controller, "get_active_tab", lambda: tab)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: calls["keys"].append(key))
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: widget)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: calls["click"].append(kw) or click)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=legs.ITEM_BUDGET_S: arrived)
    return calls


SPELL_WIDGET = {"found": True, "clickable": True, "screenX": 700, "screenY": 300}


def test_a_spell_leg_opens_the_magic_tab_and_casts_with_one_gated_click(monkeypatch):
    import npc_contact
    calls = _spell_world(monkeypatch, tab=0, widget=SPELL_WIDGET)
    leg = legs.spell("Moonclan Teleport", (218, 116), (2113, 3915, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert calls["keys"] == [npc_contact.MAGIC_TAB_KEY], "the tab was not the magic tab"
    assert calls["click"][0]["offset"] == (0, 0) and calls["click"][0]["expected_action"] == "Cast"


def test_a_spell_leg_does_not_touch_the_tab_key_when_the_book_is_already_open(monkeypatch):
    import npc_contact
    calls = _spell_world(monkeypatch, tab=npc_contact.MAGIC_TAB_INDEX, widget=SPELL_WIDGET)
    assert legs.run_next(_Ctx(), [legs.spell("Moonclan Teleport", (218, 116), (2113, 3915, 0))], 0)[0] == legs.DONE
    assert calls["keys"] == []


def test_a_spell_that_is_not_clickable_or_never_lands_fails_the_leg(monkeypatch):
    """Wrong spellbook, a filtered spell, or no runes: the click does nothing and no landing comes."""
    leg = legs.spell("Moonclan Teleport", (218, 116), (2113, 3915, 0))
    _spell_world(monkeypatch, widget={"found": True, "clickable": False})
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0)
    _spell_world(monkeypatch, widget=None)
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0)
    _spell_world(monkeypatch, widget=SPELL_WIDGET, arrived=False)
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0)


def test_a_mounted_house_item_leg_is_confirmed_by_leaving_the_house(monkeypatch, in_the_house):
    seen = []
    monkeypatch.setattr(poh, "use_mounted", lambda ctx, name, action, log: seen.append((name, action)) or True)
    monkeypatch.setattr(poh, "await_left_house", lambda budget_s: True)
    leg = legs.house_mounted("digsite pendant", "Digsite", (3341, 3445, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert seen == [("digsite pendant", "Digsite")]
    monkeypatch.setattr(poh, "await_left_house", lambda budget_s: False)
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0)
    monkeypatch.setattr(poh, "use_mounted", lambda ctx, name, action, log: False)
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0)


# --- 2026-09-20: the gnome glider (USER: "for glider pathing cant you check microbot?") --------
#
# Microbot's Rs2Walker.handleGlider: interact the pilot with the action `Glider`, wait for
# interface 138, click the destination widget. Its packed ids decode as (138 << 16) | child:
# Sindarpos 9043975 -> 138.7. The pilot (Captain Errdo 10467 at (2465,3501,3)) and the landing
# (2850,3498,0) are our own vendored gnome_gliders.tsv.

ERRDO = {"name": "Captain Errdo", "id": 10467, "index": 4412, "clickable": True,
         "screenX": 500, "screenY": 300, "x": 2465, "y": 3501, "plane": 3}
MAP_ENTRY = {"found": True, "clickable": True, "screenX": 320, "screenY": 210}


def _glider_world(monkeypatch, npcs, widget_answers, arrived=True):
    calls = {"menu": [], "click": []}
    answers = iter(widget_answers)
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: npcs)
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda index: f"npc{index}")
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: next(answers, None))
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, action, **kw: calls["menu"].append((e.get("name"), action, kw.get("refresh_target"))) or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: calls["click"].append(kw) or True)
    monkeypatch.setattr(legs.time, "sleep", lambda s: None)
    monkeypatch.setattr(legs, "await_arrival", lambda dest, budget_s=legs.ITEM_BUDGET_S: arrived)
    return calls


def test_a_glider_leg_talks_to_the_pilot_then_clicks_the_destination_on_the_map(monkeypatch):
    calls = _glider_world(monkeypatch, [ERRDO], [None, None, MAP_ENTRY])
    leg = legs.glider("Captain Errdo", "Sindarpos", (138, 7), (2850, 3498, 0))
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.DONE, 1)
    assert calls["menu"] == [("Captain Errdo", "Glider", "npc4412")], "the pilot, tracked by index"
    assert len(calls["click"]) == 1 and calls["click"][0]["offset"] == (0, 0)


def test_a_glider_map_already_open_is_not_reopened(monkeypatch):
    calls = _glider_world(monkeypatch, [ERRDO], [MAP_ENTRY, MAP_ENTRY])
    assert legs.run_next(_Ctx(), [legs.glider("Captain Errdo", "Sindarpos", (138, 7), (2850, 3498, 0))], 0)[0] == legs.DONE
    assert calls["menu"] == []


def test_a_glider_leg_fails_cleanly_without_the_pilot_the_map_or_the_landing(monkeypatch):
    leg = legs.glider("Captain Errdo", "Sindarpos", (138, 7), (2850, 3498, 0))
    _glider_world(monkeypatch, [], [None])
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0), "no pilot in view"
    _glider_world(monkeypatch, [ERRDO], [None] * 200)
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0), "the map never opened (The Grand Tree not done?)"
    _glider_world(monkeypatch, [ERRDO], [None, MAP_ENTRY], arrived=False)
    assert legs.run_next(_Ctx(), [leg], 0) == (legs.FAILED, 0), "clicked, never landed"


# --- 2026-09-20: the two tables can name ONE ladder by two object ids ---------------------------

def test_a_stale_object_id_falls_back_to_the_sibling_rows_id(monkeypatch):
    """MEASURED on the Grand Tree: every ladder is in the merged table twice -- 'Climb-up Ladder
    16683' from the jar and 'Climb-up Ladder 4458' from Microbot's newer fork, same origin, same
    destination. The search takes the first; if that id is the stale one, the object is 'not
    found', the walk fails, and every retry re-plans the same way until the routine stops. The
    sibling row's id names the same physical ladder."""
    import transports
    rows = [s for s in transports.load_links() if s.origin == (2466, 3494, 0) and s.dest == (2466, 3494, 1)]
    assert {r.object_id for r in rows} == {16683, 4458}, "the premise: two ids, one ladder"
    ours = next(r for r in rows if r.object_id == 16683)
    new_ladder = {"id": 4458, "name": "Ladder", "x": 2466, "y": 3495, "plane": 0, "screenX": 300, "screenY": 200}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [new_ladder])
    assert legs.find_transport_object(ours) is new_ladder
    # An unrelated object with some other id is still not a match.
    other = dict(new_ladder, id=999)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [other])
    assert legs.find_transport_object(ours) is None
    # And the row's OWN id still wins when it is there.
    own = dict(new_ladder, id=16683)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [new_ladder, own])
    assert legs.find_transport_object(ours) is own


# --- 2026-09-20: finished quests reach the search -----------------------------------------------
#
# 120 transports are gated on a quest NAME across 24 quests and nothing supplied them, so all 120
# failed closed. The bridge has a reader: GET /api/progression/quests. Its shape below is from the
# vendored SOURCE (Quests.State: quests[{id, name, state}], completedCount, ...; QuestState is an
# enum, which Gson writes by name) -- NOT from a live GET: on 2026-09-20 the running client's copy
# of that reader was dying with NoClassDefFoundError until a restart.

QUESTS = {"quests": [{"id": 1, "name": "Fishing Contest", "state": "FINISHED"},
                     {"id": 2, "name": "The Grand Tree", "state": "FINISHED"},
                     {"id": 3, "name": "Monkey Madness II", "state": "IN_PROGRESS"},
                     {"id": 4, "name": "Dragon Slayer II", "state": "NOT_STARTED"}],
          "completedCount": 2, "inProgressCount": 1, "notStartedCount": 1}


def test_finished_quests_are_read_by_name_and_only_finished_ones_count(monkeypatch):
    monkeypatch.setattr(legs.crossfloor, "requirement_ids", lambda: (set(), set()))
    monkeypatch.setattr(controller, "get_api", lambda group, reader: QUESTS if (group, reader) == ("progression", "quests") else None)
    assert legs.requirement_values()["completed_quests"] == {"Fishing Contest", "The Grand Tree"}


def test_an_unreadable_quest_list_proves_no_quest(monkeypatch):
    """None (no bridge), an {"reader", "error"} map (the reader died) and an EMPTY list (logged
    out: the reader answers quests=[]) all mean UNKNOWN -- never 'no quests done', and never a
    set that would let a quest gate pass."""
    monkeypatch.setattr(legs.crossfloor, "requirement_ids", lambda: (set(), set()))
    for answer in (None, {"reader": "Quests", "error": "NoClassDefFoundError: ..."}, {}, {"quests": []}):
        monkeypatch.setattr(controller, "get_api", lambda group, reader, a=answer: a)
        assert legs.requirement_values()["completed_quests"] is None, answer


def test_an_id_match_with_no_name_is_not_the_transport_and_falls_through(monkeypatch):
    """FOUND LIVE 2026-09-21 at the Slayer Tower. The merged table names that window TWICE, at the
    same origin and destination, with two ids -- 2173 (the jar's) and 57679 (Microbot's newer
    fork). The planner took the 2173 row.

    There IS an object with id 2173 in that scene, but it is NAMELESS scenery: the routine aimed
    at it, the hover gate refused, the right-click menu came back ['Cancel', 'Mark Safespot',
    'Walk here'], and because the id had "matched" the sibling fallback never ran. It looped until
    the give-up counter stopped it.

    A transport's object has a NAME -- that is how the nameless case is told apart from the real
    one, and it costs nothing when the id is right."""
    step = _step("Climb-through Broken window 2173", (3442, 3531, 0), (3444, 3533, 0))
    scene = [
        # MEASURED: the decoy's name is the literal string "null", not a JSON null -- a
        # non-empty check passes it, which is why the first attempt at this fix did nothing.
        {"id": 2173, "name": "null", "x": 3442, "y": 3531, "plane": 0, "screenX": 10, "screenY": 10},
        {"id": 57679, "name": "Broken window", "x": 3442, "y": 3531, "plane": 0,
         "screenX": 20, "screenY": 20},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda **k: scene)
    monkeypatch.setattr(legs.transports, "load_links", lambda **k: [])
    monkeypatch.setattr(legs.transports, "load_agility_shortcuts",
                        lambda **k: [_step("Climb-through Broken window 57679",
                                           (3442, 3531, 0), (3444, 3533, 0))])

    found = legs.find_transport_object(step)

    assert found is not None and found["id"] == 57679


def test_a_named_object_at_the_right_id_is_still_taken_directly(monkeypatch):
    """The guard must not cost anything when the table's id is the live one."""
    step = _step("Climb-through Broken window 57679", (3442, 3531, 0), (3444, 3533, 0))
    scene = [{"id": 57679, "name": "Broken window", "x": 3442, "y": 3531, "plane": 0,
              "screenX": 20, "screenY": 20}]
    monkeypatch.setattr(controller, "get_objects", lambda **k: scene)

    assert legs.find_transport_object(step)["id"] == 57679


def test_arrival_accepts_any_of_several_landings(monkeypatch):
    """FOUND LIVE 2026-09-21 on the rats task. The Varrock teleport BROKE and landed the player at
    the Grand Exchange (3162, 3479), not Varrock square (3213, 3424) -- this account has the
    Varrock diary reward that REDIRECTS that tablet. The table carries the un-redirected tile, so
    the leg judged a teleport that worked perfectly as a failure, retried, and burned another
    tablet each time.

    A redirect is a per-account setting, so the honest answer is to accept either landing rather
    than pick one and be wrong for somebody. The onward walk is proven from whichever tile we
    actually reach, so nothing downstream cares which it was."""
    monkeypatch.setattr(legs.time, "sleep", lambda *_: None)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3162, "y": 3479, "plane": 0})

    assert legs.await_arrival([(3213, 3424, 0), (3164, 3487, 0)]) is True
    assert legs.await_arrival((3213, 3424, 0), budget_s=0.2) is False, "one tile still means one"


def test_the_varrock_tablet_lists_its_diary_redirect():
    """The Grand Exchange landing, MEASURED live on this account."""
    import slayer_routes
    assert (3164, 3487, 0) in slayer_routes.LANDING_REDIRECTS["varrock teleport"]


def test_a_shut_two_state_object_is_OPENED_then_used(monkeypatch):
    """FOUND LIVE 2026-09-21 at the Varrock sewers. USER: "gotta right click it".

        No 'Climb-down' entry in menu for Manhole
        (['Cancel', 'Mark Safespot', 'Examine', 'Walk here', 'Open'])

    The manhole was SHUT. A shut manhole is id 881 offering only Open; the transport row names 882,
    the open one, with Climb-down. run_transport's own docstring already listed this as a known
    limit -- "a SHUT trapdoor is a different id with an `Open` action never matches its row".

    It is the door model this project already uses everywhere else: route through it, then open it
    when you arrive. One open, then the row's own option, and no retry loop if that fails."""
    clicks = []
    monkeypatch.setattr(legs.time, "sleep", lambda *_: None)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3236, "y": 3458, "plane": 0})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(legs, "await_landing", lambda step, budget: True)

    shut = {"id": 881, "name": "Manhole", "x": 3236, "y": 3458, "plane": 0,
            "screenX": 10, "screenY": 10, "actions": ["Open"]}
    monkeypatch.setattr(legs, "find_transport_object", lambda step: shut)

    def menu(ser, obj, option, **k):
        clicks.append(option)
        return option == "Open"          # the row's own option is not on a SHUT manhole

    monkeypatch.setattr(controller, "click_menu_action", menu)
    monkeypatch.setattr(controller, "click_entity",
                        lambda *a, **k: type("R", (), {"__bool__": lambda s: False,
                                                       "withheld": True})())

    step = _step("Climb-down Manhole 882", (3236, 3458, 0), (3237, 9858, 0))
    legs.run_transport(_Ctx(), step)

    assert "Open" in clicks, f"it never tried to open the manhole: {clicks}"
    assert clicks.index("Open") < len(clicks) - 1 or clicks.count("Climb-down") >= 1, clicks
