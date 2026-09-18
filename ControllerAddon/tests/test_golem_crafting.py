import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import golem_crafting as gc
import controller


@pytest.fixture(autouse=True)
def _no_live_last_action(monkeypatch):
    """Keep the /last-action echo check out of every test that isn't about it.

    Two jobs. It stops craft_phase's echo check reaching a REAL bridge over HTTP (these tests ran
    against a live client for 42s before this existed), and it returns an UNCHANGING action so the
    verdict is always None -- "not processed yet" -- which is precisely the state that leaves
    craft_phase's behaviour identical to before the check was added. Tests that are about the
    check override both this and GOLEM_CLICK_ECHO_TIMEOUT_S."""
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 0.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": 1, "option": "Walk here", "target": "", "id": 0})
    # Same reasoning: the menu-escalation route would otherwise right-click through a real bridge.
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: False)
    # And the same again for the inventory: several phases read it, and a test that never needed
    # get_inventory would otherwise reach the live client through it (2026-08-24). Tests that
    # care about inventory contents override this.
    monkeypatch.setattr(controller, "get_inventory", lambda: [])


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Keep the suite hermetic. wait_until_player_idle calls get_player over HTTP when a test
    does not stub it, so the idle check's result depended on the character's actual live state --
    a moving player caused the check to time out, taking a different code path and breaking
    pre-hover assertions. MEASURED 2026-08-29: test_mine_phase_hovers_a_different_ready_rock_
    after_a_successful_click passed alone (character happened to be idle) and failed in a full
    run (character was moving across the map during other tests).

    Returns a stable idle position (unchanged localX/localY) so wait_until_player_idle returns
    True immediately. Tests that need a specific position override this."""
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"localX": 10000, "localY": 10000, "x": 100, "y": 100})


def _station(name="north", x=2596, y=2257, insert_side="south", progress=0, done=()):
    """A /golem station dict with the real tile geometry, `done` naming carved sides."""
    offsets = {"north": (0, 1), "east": (1, 0), "south": (0, -1), "west": (-1, 0)}
    return {
        "name": name, "objectId": 62351 if name == "north" else 62352,
        "x": x, "y": y, "plane": 0, "progress": progress, "insertSide": insert_side,
        "sides": {
            side: {"done": side in done, "progress": 10 if side in done else 0,
                   "x": x + dx, "y": y + dy, "plane": 0}
            for side, (dx, dy) in offsets.items()
        },
    }


# --- side_order / can_end_on_insert -------------------------------------------------

@pytest.mark.parametrize("insert_side", ["south", "north"])
@pytest.mark.parametrize("start", ["east", "west"])
def test_east_west_starts_end_on_the_insert_side(start, insert_side):
    assert gc.side_order(start, insert_side)[-1] == insert_side


@pytest.mark.parametrize("insert_side", ["south", "north"])
def test_starting_on_or_opposite_the_insert_side_cannot_end_there(insert_side):
    opposite = {"north": "south", "south": "north"}[insert_side]
    assert not gc.can_end_on_insert(insert_side, insert_side)
    assert not gc.can_end_on_insert(opposite, insert_side)


def test_only_east_and_west_are_valid_starts_for_either_plinth():
    """The constraint that justifies restricting the start side at all: a four-step rotation
    lands on start+/-1, so start must neighbour the insert side. Asserted directly so a later
    'simplification' cannot quietly reintroduce a walk before Insert-core."""
    for insert_side in ("south", "north"):
        valid = [s for s in gc.SIDES if gc.can_end_on_insert(s, insert_side)]
        assert sorted(valid) == ["east", "west"]


@pytest.mark.parametrize("start", gc.SIDES)
@pytest.mark.parametrize("insert_side", gc.SIDES)
def test_side_order_covers_all_four_sides_exactly_once(start, insert_side):
    """PARAMETRIZED 2026-08-20 (FINDING F, whole-branch review): previously checked only the
    single (east, south) combination -- widened across every (start, insert_side) pair, same
    @pytest.mark.parametrize style already used above for the east/west-start tests, so a future
    regression specific to some other combination (e.g. a start side equal to insert_side) can't
    slip through unnoticed."""
    assert sorted(gc.side_order(start, insert_side)) == sorted(gc.SIDES)


# --- choose_start_side --------------------------------------------------------------

def test_choose_start_side_picks_the_nearer_of_east_and_west():
    station = _station()  # golem tile (2596, 2257); east (2597, 2257), west (2595, 2257)
    assert gc.choose_start_side(station, 2600, 2257) == "east"
    assert gc.choose_start_side(station, 2590, 2257) == "west"


def test_choose_start_side_picks_the_nearer_side_even_when_the_player_is_north_or_south():
    """REWRITTEN 2026-08-20 (FINDING F, whole-branch review): the deleted version of this test
    (test_choose_start_side_never_returns_a_north_south_side) was structurally vacuous -- the
    candidate set inside choose_start_side is already filtered to east/west (see
    can_end_on_insert) before any distance comparison runs, so "the result is in (east, west)"
    holds for ANY distance function, including a broken one that e.g. always returns "east". This
    version asserts the SPECIFIC side actually chosen: with the player due north or south of the
    golem tile (equidistant in x from both east and west, see _station's geometry), max(|dx|,
    |dy|) is dominated by |dy| for both candidates, so both distances tie and min() falls through
    to picking whichever candidate sorts/iterates first -- east, per SIDES' ordering. Moving the
    player noticeably closer to the west tile's x instead breaks that tie in west's favor,
    pinning that the distance comparison itself is doing real work rather than accepting whatever
    order can_end_on_insert happens to build."""
    station = _station()  # golem tile (2596, 2257); east (2597, 2257), west (2595, 2257)
    assert gc.choose_start_side(station, 2596, 2270) == "east"
    assert gc.choose_start_side(station, 2596, 2240) == "east"
    assert gc.choose_start_side(station, 2593, 2257) == "west"


# --- carve_plan ---------------------------------------------------------------------

def test_carve_plan_on_a_fresh_golem_lists_four_sides_ending_on_insert():
    station = _station()
    plan = gc.carve_plan(station, 2600, 2257)
    assert len(plan) == 4
    assert plan[-1] == "south"


def test_carve_plan_skips_already_done_sides():
    station = _station(done=("east", "north"))
    plan = gc.carve_plan(station, 2600, 2257)
    assert "east" not in plan and "north" not in plan
    assert sorted(plan) == ["south", "west"]


def test_carve_plan_returns_empty_when_all_sides_done():
    station = _station(done=gc.SIDES)
    assert gc.carve_plan(station, 2600, 2257) == []


def test_carve_plan_resumed_with_insert_side_already_done_does_not_end_on_it():
    """A resumed golem may not admit an east/west start. This is legal, not an error --
    craft_phase walks to insertSide afterwards."""
    station = _station(done=("south",))
    plan = gc.carve_plan(station, 2600, 2257)
    assert "south" not in plan
    assert len(plan) == 3


# --- choose_station -----------------------------------------------------------------

def test_choose_station_prefers_a_station_already_in_progress():
    idle = _station("north", progress=0)
    active = _station("south", 2595, 2253, "north", progress=2)
    assert gc.choose_station([idle, active], None, 2596, 2257)["name"] == "south"


def test_choose_station_prefers_the_more_advanced_when_both_in_progress():
    a = _station("north", progress=1)
    b = _station("south", 2595, 2253, "north", progress=3)
    assert gc.choose_station([a, b], None, 2596, 2257)["name"] == "south"


def test_choose_station_alternates_away_from_the_last_one_when_both_idle():
    north = _station("north")
    south = _station("south", 2595, 2253, "north")
    assert gc.choose_station([north, south], "north", 2596, 2257)["name"] == "south"
    assert gc.choose_station([north, south], "south", 2596, 2257)["name"] == "north"


def test_choose_station_falls_back_to_nearest_without_a_last_station():
    north = _station("north")
    south = _station("south", 2595, 2253, "north")
    assert gc.choose_station([north, south], None, 2596, 2257)["name"] == "north"
    assert gc.choose_station([north, south], None, 2595, 2250)["name"] == "south"


def test_choose_station_returns_none_for_an_empty_list():
    assert gc.choose_station([], None, 2596, 2257) is None


# --- has_start_materials ------------------------------------------------------------

def test_start_materials_needs_four_sunstone_not_merely_one():
    """The bug being fixed: the old gate checked existence, so 1-3 raw fired a doomed click."""
    assert not gc.has_start_materials(3, 1)
    assert gc.has_start_materials(4, 1)


def test_start_materials_needs_a_core():
    assert not gc.has_start_materials(20, 0)
    assert gc.has_start_materials(20, 1)


# --- controller.get_golem ------------------------------------------------------------

def test_get_golem_returns_none_when_the_endpoint_is_missing(monkeypatch):
    """A 404 (client not rebuilt with GolemProvider) must surface as None, not an empty dict --
    craft_phase safety-stops on None rather than silently degrading to a blind path."""
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: None)
    assert controller.get_golem() is None


def test_get_golem_passes_through_the_payload(monkeypatch):
    payload = {"inArea": True, "stations": []}
    monkeypatch.setattr(controller, "api_get", lambda path, **kw: payload)
    assert controller.get_golem() == payload


# --- craft_phase regressions (2026-08-20 code review round 1) -----------------------
#
# These stub controller.get_golem/get_player/get_objects (and, per test, walk_to_exact_tile or
# _click_station) rather than hitting a real bridge -- craft_phase itself is an integration
# function, but the specific bug class under test (a failure path that returns with no counter
# incremented and nothing else changed) is entirely deterministic given those stubs, so a real
# bridge/client isn't needed to reproduce or prove the fix.

def _ctx():
    return controller.Context(ser=None, state=controller.ControllerState())


def _stub_golem_area(monkeypatch, station):
    """Stubs get_golem/get_player/get_objects so craft_phase clears the "in area, station
    visible" gate and reaches the carve-plan step for `station` (a single in-progress station,
    progress > 0, so the fresh-golem material checks are skipped entirely)."""
    monkeypatch.setattr(controller, "get_golem", lambda: {"inArea": True, "stations": [station]})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": station["x"], "y": station["y"]})
    monkeypatch.setattr(
        controller, "get_objects",
        lambda radius=None: [
            {"id": station["objectId"], "x": station["x"], "y": station["y"], "plane": 0}
        ],
    )


def test_unprojectable_tile_does_not_spin_forever_and_eventually_safety_stops(monkeypatch):
    """Regression for CRITICAL 1 (2026-08-20 review): walk_to_exact_tile returning False (the
    target tile can't be projected onto the viewport at all) used to make craft_phase return
    having done NOTHING -- no counter, no hop, no phase change, no safety stop -- so the very next
    tick was byte-identical, forever. Drives craft_phase with a permanently-unprojectable side
    tile for GOLEM_APPROACH_MISS_LIMIT calls and asserts every one of them makes observable
    progress (a hop attempt, an incremented miss counter) short of the limit, then safety-stops
    on the limit-th call instead of continuing to spin."""
    station = _station("north", progress=5, done=())  # in-progress -- skips the material gates
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: False)
    hops = []
    monkeypatch.setattr(
        gc, "_hop_toward_point",
        lambda ctx, x, y, plane, **kw: hops.append((x, y, plane)) or False,
    )

    ctx = _ctx()
    for i in range(1, gc.GOLEM_APPROACH_MISS_LIMIT):
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
        assert not ctx.state.safety_stopped, f"safety-stopped too early on attempt {i}"
        assert ctx.state.data.get("gc_side_walk_misses") == i, "miss counter must advance every attempt"
        assert len(hops) == i, "each failed walk must attempt a hop, never a silent no-op"

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped, "unbounded walk failures must eventually safety-stop, not spin forever"
    # The counter is CLEARED when the limit is reached (2026-08-24), because reaching it no longer
    # necessarily ends the run: on a fresh plinth it backs off instead, and a back-off has to start
    # its next approach from a clean slate. This station is part-carved (progress 5), so it still
    # halts -- which is the property this test exists to guard, asserted directly above.
    assert ctx.state.data["gc_side_walk_misses"] == 0


def test_click_failure_does_not_spin_forever_and_eventually_safety_stops(monkeypatch):
    """Regression for IMPORTANT 2: _click_station failing was the same unbounded shape as
    CRITICAL 1's walk failure -- both call sites returned with no counter incremented. Walk
    succeeds here so the click step is actually reached; _click_station itself is stubbed to fail
    every time, and _click_station_tracked must bound that the same way the walk step is bounded."""
    station = _station("north", progress=5, done=())
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(gc, "_click_station", lambda ctx, station, objects, **kw: False)

    ctx = _ctx()
    for i in range(1, gc.GOLEM_APPROACH_MISS_LIMIT):
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
        assert not ctx.state.safety_stopped, f"safety-stopped too early on attempt {i}"
        assert ctx.state.data.get("gc_side_click_misses") == i

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped, "unbounded click failures must eventually safety-stop, not spin forever"


def test_single_golem_state_miss_is_tolerated_not_stopped(monkeypatch):
    """Regression for IMPORTANT 3: api_get (and therefore get_golem) returns None on ANY
    exception, including its own 0.3s timeout -- CLAUDE.md documents this exact routine blowing
    that timeout under load. A single None must be tolerated as a likely transient blip, not
    immediately safety-stopped as "rebuild the client"."""
    ctx = _ctx()
    monkeypatch.setattr(controller, "get_golem", lambda: None)

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert not ctx.state.safety_stopped
    assert ctx.state.data["gc_golem_state_misses"] == 1


def test_golem_state_miss_counter_resets_on_a_later_success(monkeypatch):
    """A transient miss followed by a real response must not leave a stale near-threshold count
    sitting around to combine with a later, unrelated gap."""
    ctx = _ctx()
    calls = {"n": 0}

    def fake_get_golem():
        calls["n"] += 1
        return None if calls["n"] == 1 else {"inArea": True, "stations": []}

    monkeypatch.setattr(controller, "get_golem", fake_get_golem)

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)  # miss 1
    assert ctx.state.data["gc_golem_state_misses"] == 1

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)  # a real response this time
    assert ctx.state.data["gc_golem_state_misses"] == 0
    assert not ctx.state.safety_stopped


def test_n_consecutive_golem_state_misses_eventually_safety_stops(monkeypatch):
    """The other half of IMPORTANT 3's fix: tolerance must still have a ceiling --
    GOLEM_STATE_MISS_LIMIT consecutive Nones must safety-stop rather than retry forever."""
    ctx = _ctx()
    monkeypatch.setattr(controller, "get_golem", lambda: None)

    for i in range(1, gc.GOLEM_STATE_MISS_LIMIT):
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
        assert not ctx.state.safety_stopped, f"safety-stopped too early on miss {i}"

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped
    assert ctx.state.data["gc_golem_state_misses"] == gc.GOLEM_STATE_MISS_LIMIT


# --- gem-bag loot handling (2026-08-20) ----------------------------------------------

class _FakeState:
    def __init__(self):
        self.data = {}

    def trigger_safety_stop(self, reason):
        self.data["_stopped"] = reason


class _FakeCtx:
    def __init__(self):
        self.state = _FakeState()
        self.ser = None


def _loot_event(ctx, destination):
    gc.on_event(ctx, "CHAT_MESSAGE", {
        "message": f"As you complete the golem it leaves a gift {destination} for you: 1 x Uncut ruby."
    })


def test_gem_bag_destination_does_not_flag_a_full_bag():
    ctx = _FakeCtx()
    _loot_event(ctx, "in your gem bag")
    assert not ctx.state.data.get("gc_gem_bag_full")


def test_ground_destination_flags_a_full_gem_bag():
    """The only route by which loot can now be permanently lost, so it must be detected from the
    message rather than by scanning the ground after every golem."""
    ctx = _FakeCtx()
    _loot_event(ctx, "on the ground")
    assert ctx.state.data["gc_gem_bag_full"]


def test_ground_destination_parses_the_item_name_for_the_later_pickup_filter():
    """craft_phase's pickup (run one call later -- see below) filters by name as well as
    position; on_event is the only place that ever sees the raw chat message, so it must parse
    the item name out of it here rather than the pickup guessing at a hardcoded gem list."""
    ctx = _FakeCtx()
    _loot_event(ctx, "on the ground")
    assert ctx.state.data["gc_gem_bag_full_item"] == "uncut ruby"


def test_gem_sack_destination_does_not_flag_a_full_bag():
    ctx = _FakeCtx()
    _loot_event(ctx, "in your gem sack")
    assert not ctx.state.data.get("gc_gem_bag_full")


# --- craft_phase gem-bag pickup timing (2026-08-20 code review round 2) --------------
#
# CRITICAL, established as fact by the reviewer (controller.py:2070-2080): on_event only drains
# queued UDP events once per main-loop iteration, strictly BEFORE tick() runs, and
# _await_golem_complete blocks *inside craft_phase itself* on a plain time.sleep poll that never
# drains UDP. So the "leaves a gift on the ground" message announcing a golem's own completion
# cannot reach on_event -- and therefore cannot set gc_gem_bag_full -- until the NEXT craft_phase
# call, one iteration after the completion that set gc_last_station. By then choose_station has
# already alternated away from the plinth that actually completed, so the pickup must search
# gc_last_station's coordinates, not whatever station a same-call read would have used. These
# tests model that real two-call sequence directly instead of (incorrectly) presetting the flag
# before a call that also completes a golem, which cannot happen in practice.

def _two_station_golem(monkeypatch, north, south, ground_items, click_calls):
    """Stubs get_golem/get_player/get_objects to always report both plinths (as GolemProvider
    really does, unconditionally -- see _any_station_visible's own docstring), and records every
    click_menu_action call (rather than a bare True/False stub) so a test can assert exactly
    which ground item, at which position, was actually targeted.

    UPDATED 2026-08-20 (FINDING C fix): get_ground_items is now a live view over a mutable copy
    of `ground_items`, and click_menu_action's stub removes the clicked entity from it -- the same
    "the click actually landed and picked it up" simulation a real bridge would report on the next
    fresh re-read. This is what lets _collect_golem_gift's retry-until-gone loop exit after a
    single successful click in tests, the same as it would live, rather than retrying
    GOLEM_GEM_PICKUP_ATTEMPTS times against a ground list a stub never actually changes."""
    monkeypatch.setattr(
        controller, "get_golem",
        lambda: {"inArea": True, "stations": [north, south]},
    )
    monkeypatch.setattr(controller, "get_player", lambda: {"x": north["x"], "y": north["y"]})
    monkeypatch.setattr(
        controller, "get_objects",
        lambda radius=None: [
            {"id": north["objectId"], "x": north["x"], "y": north["y"], "plane": 0},
            {"id": south["objectId"], "x": south["x"], "y": south["y"], "plane": 0},
        ],
    )
    ground_state = list(ground_items)
    monkeypatch.setattr(controller, "get_ground_items", lambda fresh=False: list(ground_state))

    def _fake_click_menu_action(ser, entity, option, target_hint=None, offset=(0, 0), **kw):
        click_calls.append(entity)
        if entity in ground_state:
            ground_state.remove(entity)
        return True

    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


def _complete_golem_at(monkeypatch, ctx, station, other_station):
    """Drives craft_phase through a single-call Insert-core completion on `station`, the same
    setup test_gem_bag_full_path_still_runs_end_of_golem_bookkeeping (round 1) used -- this
    leaves ctx.state.data["gc_last_station"] set to `station`, matching what a real completed
    golem does before on_event has had any chance to drain the loot message."""
    monkeypatch.setattr(
        controller, "get_golem",
        lambda: {"inArea": True, "stations": [station, other_station]},
    )
    monkeypatch.setattr(controller, "get_player", lambda: {"x": station["x"], "y": station["y"]})
    monkeypatch.setattr(
        controller, "get_objects",
        lambda radius=None: [{"id": station["objectId"], "x": station["x"], "y": station["y"], "plane": 0}],
    )
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(gc, "_await_golem_complete", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone core"}])
    ctx.state.data["gc_fur_stock"] = 5
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.data["gc_last_station"] == station["name"]
    assert not ctx.state.data.get("gc_gem_bag_full"), "flag can't be set within the completing call itself"


def test_gem_bag_full_path_still_runs_end_of_golem_bookkeeping(monkeypatch):
    """Regression for a defect caught in round 1's review: the gem-bag-full branch's early
    `return` used to skip the fur decrement / insert-miss reset that every other
    golem-completion exit path runs. A completed golem consumes one fur whether or not its gem
    fit in the bag -- gc_fur_stock is a blind local estimate with no game-state fallback (that's
    exactly why gc_fur_exhausted exists), so a skipped decrement never self-corrects and
    eventually causes an Insert-core attempt with no fur left. gc_insert_misses carrying over
    would likewise drift toward a false safety-stop on the next golem.

    REWORKED for round 2's fix (the gem-bag check moved to the top of craft_phase): modeled now
    as the real two-call sequence -- call one completes the golem and sets gc_last_station (this
    is where the fur/insert-miss bookkeeping under test actually runs); the loot message then
    arrives via on_event, exactly as it would between main-loop iterations; call two observes
    the now-set flag. The original single-call version (preset the flag, then call craft_phase
    once) can no longer happen in practice, since the flag can never become true within the same
    call that completes a golem -- see this section's own header comment."""
    north = _station("north", 2596, 2257, "south", progress=5, done=gc.SIDES)
    south = _station("south", 2595, 2253, "north", progress=0)
    ctx = _ctx()

    _complete_golem_at(monkeypatch, ctx, north, south)
    assert ctx.state.data["gc_fur_stock"] == 4, "a completed golem must still consume one fur even when its gem overflowed to the ground"
    assert ctx.state.data["gc_insert_misses"] == 0, "insert-miss count must still reset on a confirmed completion"

    gc.on_event(ctx, "CHAT_MESSAGE", {
        "message": "As you complete the golem it leaves a gift on the ground for you: 1 x Uncut ruby.",
    })
    assert ctx.state.data["gc_gem_bag_full"]

    click_calls = []
    _two_station_golem(monkeypatch, north, south, ground_items=[], click_calls=click_calls)
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.data["gc_gem_bag_full"] is False
    assert ctx.state.data["gc_phase"] == "bank"
    # The bookkeeping from call one must still stand -- the second call's gem-bag branch must not
    # have touched gc_fur_stock/gc_insert_misses again.
    assert ctx.state.data["gc_fur_stock"] == 4
    assert ctx.state.data["gc_insert_misses"] == 0


def test_gem_bag_pickup_targets_gc_last_station_not_the_freshly_chosen_one(monkeypatch):
    """Pins the actual CRITICAL fix: with the flag set (as on_event would have left it, one call
    after completion), the pickup must search around gc_last_station (north, where the gem
    actually fell) even though choose_station would pick south next. A decoy item of the exact
    same name sits at south to prove this is a position check, not merely a name match."""
    north = _station("north", 2596, 2257, "south", progress=0)
    south = _station("south", 2595, 2253, "north", progress=0)
    ctx = _ctx()
    ctx.state.data["gc_last_station"] = "north"
    ctx.state.data["gc_gem_bag_full"] = True
    ctx.state.data["gc_gem_bag_full_item"] = "uncut ruby"

    real_gem = {"id": 1, "name": "Uncut ruby", "x": north["x"], "y": north["y"]}
    decoy = {"id": 2, "name": "Uncut ruby", "x": south["x"], "y": south["y"]}
    click_calls = []
    _two_station_golem(monkeypatch, north, south, ground_items=[real_gem, decoy], click_calls=click_calls)

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert click_calls == [real_gem], "must pick up the item at gc_last_station (north), not the newly-chosen station (south)"
    assert ctx.state.data["gc_gem_bag_full"] is False
    assert ctx.state.data["gc_gem_bag_full_item"] is None
    assert ctx.state.data["gc_phase"] == "bank"


def test_gem_bag_pickup_never_searches_the_freshly_chosen_station(monkeypatch):
    """The other half of the same fix, isolated: an item sitting ONLY near the station
    choose_station would pick next (south) -- not gc_last_station (north) -- must never be
    picked up, even though it's the only candidate on the ground. Confirms the wrong-plinth
    search genuinely cannot happen, rather than merely being deprioritized."""
    north = _station("north", 2596, 2257, "south", progress=0)
    south = _station("south", 2595, 2253, "north", progress=0)
    ctx = _ctx()
    ctx.state.data["gc_last_station"] = "north"
    ctx.state.data["gc_gem_bag_full"] = True
    ctx.state.data["gc_gem_bag_full_item"] = "uncut ruby"

    decoy_only = {"id": 2, "name": "Uncut ruby", "x": south["x"], "y": south["y"]}
    click_calls = []
    _two_station_golem(monkeypatch, north, south, ground_items=[decoy_only], click_calls=click_calls)

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert click_calls == [], "an item near the freshly-chosen station must never be picked up as a substitute"
    assert ctx.state.data["gc_gem_bag_full"] is False
    assert ctx.state.data["gc_phase"] == "bank"


def test_gem_bag_full_without_a_last_station_clears_the_flag_without_a_pickup(monkeypatch):
    """gc_last_station can be absent (a fresh start, or a process restart that lost
    ctx.state.data after the flag was effectively set server-side) -- must clear the flag and
    still route to bank rather than crash on a None station or spin forever unable to search."""
    north = _station("north", 2596, 2257, "south", progress=0)
    south = _station("south", 2595, 2253, "north", progress=0)
    ctx = _ctx()
    ctx.state.data["gc_gem_bag_full"] = True
    ctx.state.data["gc_gem_bag_full_item"] = "uncut ruby"
    # Deliberately no gc_last_station.

    click_calls = []
    _two_station_golem(monkeypatch, north, south, ground_items=[], click_calls=click_calls)

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert click_calls == []
    assert ctx.state.data["gc_gem_bag_full"] is False
    assert ctx.state.data["gc_phase"] == "bank"


# --- craft_phase /objects de-duplication (2026-08-20 code review) --------------------
#
# controller.py:280-283 documents /objects measuring 390-420ms in Wyrmscraig (this routine's own
# location, ~2900 objects in radius) versus under 20ms for every other bridge endpoint -- so a
# second, redundant fetch on every single carve tick directly worked against this routine's
# single biggest user complaint (speed). craft_phase must resolve the object list once per call
# and thread that one result through both the visibility gate (_any_station_visible) and the
# click path (_click_station/_find_station_object) instead of each fetching its own.

def test_craft_phase_fetches_objects_exactly_once_per_call(monkeypatch):
    station = _station("north", progress=5, done=())
    _stub_golem_area(monkeypatch, station)
    calls = {"n": 0}

    def counting_get_objects(radius=None):
        calls["n"] += 1
        return [{"id": station["objectId"], "x": station["x"], "y": station["y"], "plane": 0}]

    monkeypatch.setattr(controller, "get_objects", counting_get_objects)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: True)

    ctx = _ctx()
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert calls["n"] == 1, f"expected exactly one /objects fetch per craft_phase call, got {calls['n']}"


# --- _await_side_done / _await_golem_complete (2026-08-20 whole-branch review) -------
#
# These two confirmation helpers had zero direct tests despite being repeatedly patched -- every
# existing craft_phase test above simply stubs them out wholesale. They're pure given a stubbed
# controller.get_golem, so tested directly here rather than only indirectly through craft_phase.
# `fake_clock` monkeypatches gc.time.time/sleep so a deadline/extension test doesn't cost real
# wall-clock seconds (GOLEM_SIDE_MAX_WAIT_S alone is 15s) -- each fake sleep(s) just advances a
# counter that gc.time.time() reads back, so the polling loops run to completion near-instantly
# while the deadline math inside them is exercised exactly as it would be live.

@pytest.fixture
def fake_clock(monkeypatch):
    state = {"t": 1_000_000.0}
    monkeypatch.setattr(gc.time, "time", lambda: state["t"])
    monkeypatch.setattr(gc.time, "sleep", lambda s: state.__setitem__("t", state["t"] + s))
    return state


def test_await_side_done_succeeds_on_a_genuine_transition(monkeypatch, fake_clock):
    calls = {"n": 0}

    def fake_get_golem():
        calls["n"] += 1
        flipped = calls["n"] >= 2
        return {"stations": [_station("north", done=("east",) if flipped else ())]}

    monkeypatch.setattr(controller, "get_golem", fake_get_golem)
    assert gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=False) is True


def test_await_side_done_pre_click_already_done_is_not_evidence_of_success(monkeypatch, fake_clock):
    """FINDING A: if the side already reads done BEFORE the click (already_done=True, the
    caller's own pre-click snapshot), a post-click poll matching that same reading is not
    evidence the click did anything -- must not return True merely by observing an already-true
    state it never actually caused."""
    monkeypatch.setattr(controller, "get_golem", lambda: {"stations": [_station("north", done=("east",))]})
    assert gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=True) is False


def test_await_side_done_tolerates_a_none_mid_poll(monkeypatch, fake_clock):
    calls = {"n": 0}

    def fake_get_golem():
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return {"stations": [_station("north", done=("east",))]}

    monkeypatch.setattr(controller, "get_golem", fake_get_golem)
    assert gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=False) is True


def test_await_side_done_times_out_when_the_side_never_flips(monkeypatch, fake_clock):
    monkeypatch.setattr(controller, "get_golem", lambda: {"stations": [_station("north", done=())]})
    assert gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=False) is False


def test_await_side_done_extends_the_deadline_while_progress_is_climbing(monkeypatch, fake_clock):
    """FINDING D: a side genuinely mid-carve keeps its own `progress` climbing every poll -- this
    must not be declared a miss just because elapsed time exceeds GOLEM_SIDE_TIMEOUT_S, as long as
    it's still making progress, up to the GOLEM_SIDE_MAX_WAIT_S cap.

    The climb rate is DERIVED from those two constants rather than hardcoded (2026-08-23): it used
    to be 3 polls per step, picked to beat the then-4.0s timeout, and when the timeout was
    re-measured to 8.0s the simulated carve finished at ~6.0s and this test silently stopped
    proving anything -- it still passed, but no longer crossed the un-extended deadline at all.
    `polls_per_step` now targets the midpoint between the timeout and the hard cap, so it stays
    meaningful whatever those become, and the two assertions below fail loudly if it ever
    doesn't."""
    steps = 10
    target_s = (gc.GOLEM_SIDE_TIMEOUT_S + gc.GOLEM_SIDE_MAX_WAIT_S) / 2.0
    polls_per_step = max(1, int(target_s / (steps * gc.GOLEM_CONFIRM_POLL_S)))
    calls = {"n": 0}

    def fake_get_golem():
        calls["n"] += 1
        progress = min(calls["n"] // polls_per_step, steps)
        done = progress >= steps
        station = _station("north", done=("east",) if done else ())
        station["sides"]["east"]["progress"] = progress
        return {"stations": [station]}

    monkeypatch.setattr(controller, "get_golem", fake_get_golem)
    result = gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=False)
    assert result is True
    elapsed = fake_clock["t"] - 1_000_000.0
    assert elapsed > gc.GOLEM_SIDE_TIMEOUT_S, \
        "test is only meaningful if it actually ran past the un-extended timeout"
    assert elapsed < gc.GOLEM_SIDE_MAX_WAIT_S, \
        "and only meaningful if the hard cap didn't end it -- that would prove the cap, not the " \
        "extension"


def test_await_side_done_declares_a_miss_when_progress_stalls(monkeypatch, fake_clock):
    """The other half of FINDING D: progress that stops changing (genuinely stuck, not mid-carve)
    must still time out at the original GOLEM_SIDE_TIMEOUT_S, not be extended forever."""
    station = _station("north", done=())
    station["sides"]["east"]["progress"] = 3
    monkeypatch.setattr(controller, "get_golem", lambda: {"stations": [station]})
    result = gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=False)
    assert result is False
    elapsed = fake_clock["t"] - 1_000_000.0
    assert elapsed < gc.GOLEM_SIDE_MAX_WAIT_S, "a stalled (non-climbing) side must not get the extension at all"


def test_await_side_done_caps_extension_at_the_max_wait(monkeypatch, fake_clock):
    """Progress keeps climbing forever but `done` never flips -- extension must still terminate at
    GOLEM_SIDE_MAX_WAIT_S rather than waiting indefinitely on a side that's genuinely stuck (not
    slow)."""
    calls = {"n": 0}

    def fake_get_golem():
        calls["n"] += 1
        station = _station("north", done=())
        station["sides"]["east"]["progress"] = calls["n"]  # always climbing, never done
        return {"stations": [station]}

    monkeypatch.setattr(controller, "get_golem", fake_get_golem)
    result = gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=False)
    assert result is False
    elapsed = fake_clock["t"] - 1_000_000.0
    assert elapsed <= gc.GOLEM_SIDE_MAX_WAIT_S + gc.GOLEM_CONFIRM_POLL_S, \
        "must be bounded by GOLEM_SIDE_MAX_WAIT_S, not infinite"


def test_await_golem_complete_succeeds_when_total_crafted_increments(monkeypatch, fake_clock):
    monkeypatch.setattr(
        controller, "get_golem",
        lambda: {"totalCrafted": 6, "stations": [_station("north", progress=4)]},
    )
    assert gc._await_golem_complete("north", 6.0, before_total_crafted=5, before_progress=4) is True


def test_await_golem_complete_succeeds_when_progress_returns_to_zero(monkeypatch, fake_clock):
    monkeypatch.setattr(
        controller, "get_golem",
        lambda: {"totalCrafted": 5, "stations": [_station("north", progress=0)]},
    )
    assert gc._await_golem_complete("north", 6.0, before_total_crafted=5, before_progress=4) is True


def test_await_golem_complete_pre_click_absolute_state_is_not_evidence(monkeypatch, fake_clock):
    """FINDING A -- the exact bug: _await_golem_complete's first poll can still observe pre-click
    state. If before_progress was already 0 (nothing to transition FROM) and totalCrafted never
    increments, a station reading progress==0 must NOT count as a completion just because it
    matches the absolute target state."""
    monkeypatch.setattr(
        controller, "get_golem",
        lambda: {"totalCrafted": 5, "stations": [_station("north", progress=0)]},
    )
    assert gc._await_golem_complete("north", 0.5, before_total_crafted=5, before_progress=0) is False


def test_await_golem_complete_tolerates_a_none_mid_poll(monkeypatch, fake_clock):
    calls = {"n": 0}

    def fake_get_golem():
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return {"totalCrafted": 6, "stations": [_station("north", progress=0)]}

    monkeypatch.setattr(controller, "get_golem", fake_get_golem)
    assert gc._await_golem_complete("north", 6.0, before_total_crafted=5, before_progress=4) is True


def test_await_golem_complete_times_out_when_nothing_changes(monkeypatch, fake_clock):
    monkeypatch.setattr(
        controller, "get_golem",
        lambda: {"totalCrafted": 5, "stations": [_station("north", progress=4)]},
    )
    assert gc._await_golem_complete("north", 1.0, before_total_crafted=5, before_progress=4) is False


# --- FINDING B: fresh_golem + empty plan is a contradiction --------------------------

def test_fresh_golem_with_empty_plan_safety_stops_without_a_click(monkeypatch):
    """FINDING B: fresh_golem (progress == 0) and an empty carve plan (every side already reads
    done) are mutually exclusive by design -- if this contradictory combination is ever observed,
    craft_phase must safety-stop with a diagnosable record rather than falling through to a click
    that would resolve to Start-golem in this state, silently spending 4 raw sunstone on a
    phantom action."""
    station = _station("north", progress=0, done=gc.SIDES)  # contradictory: fresh, but all done
    _stub_golem_area(monkeypatch, station)
    click_calls = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: click_calls.append(a) or True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        {"name": "Sunstone"}, {"name": "Sunstone"}, {"name": "Sunstone"}, {"name": "Sunstone"},
        {"name": "Sunstone core"},
    ])
    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = 5  # clear the fur gate so the contradiction is actually reached

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.safety_stopped
    assert click_calls == [], "must not click the plinth in this contradictory state"


# --- FINDING C: gem-bag pickup must not silently lose loot ---------------------------

def test_gem_bag_pickup_falls_back_to_position_when_name_filter_matches_nothing(monkeypatch):
    """FINDING C: the parsed item name can legitimately mismatch the ground item's actual name
    (e.g. the golem's message says "2 x Uncut rubies", the ground item is "Uncut ruby" singular)
    -- a name filter yielding zero candidates must not just leave the loop body empty and
    silently bank with the gem still sitting there. Confirms the position-only fallback still
    picks it up."""
    north = _station("north", 2596, 2257, "south", progress=0)
    south = _station("south", 2595, 2253, "north", progress=0)
    ctx = _ctx()
    ctx.state.data["gc_last_station"] = "north"
    ctx.state.data["gc_gem_bag_full"] = True
    ctx.state.data["gc_gem_bag_full_item"] = "uncut rubies"  # parsed plural -- won't exact-match

    gem = {"id": 1, "name": "Uncut ruby", "x": north["x"], "y": north["y"]}  # actual name: singular
    click_calls = []
    _two_station_golem(monkeypatch, north, south, ground_items=[gem], click_calls=click_calls)

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert click_calls == [gem], "a name-filter miss must fall back to a position-only match, not silently drop the pickup"
    assert ctx.state.data["gc_gem_bag_full"] is False
    assert ctx.state.data["gc_phase"] == "bank"


def test_gem_bag_pickup_gives_up_after_max_attempts_but_still_clears_the_flag(monkeypatch):
    """A click that never actually lands (e.g. the ~4.5% MISTAKE_MISCLICK_CHANCE near-miss -- see
    CLAUDE.md) must not hang the routine forever retrying the same pickup. After
    GOLEM_GEM_PICKUP_ATTEMPTS genuinely-failed attempts it gives up (logged loudly) and still
    clears the flag / routes to bank, rather than spinning on a lost cause."""
    north = _station("north", 2596, 2257, "south", progress=0)
    south = _station("south", 2595, 2253, "north", progress=0)
    ctx = _ctx()
    ctx.state.data["gc_last_station"] = "north"
    ctx.state.data["gc_gem_bag_full"] = True
    ctx.state.data["gc_gem_bag_full_item"] = "uncut ruby"

    gem = {"id": 1, "name": "Uncut ruby", "x": north["x"], "y": north["y"]}
    click_calls = []
    monkeypatch.setattr(
        controller, "get_golem",
        lambda: {"inArea": True, "stations": [north, south]},
    )
    monkeypatch.setattr(controller, "get_player", lambda: {"x": north["x"], "y": north["y"]})
    monkeypatch.setattr(
        controller, "get_objects",
        lambda radius=None: [
            {"id": north["objectId"], "x": north["x"], "y": north["y"], "plane": 0},
            {"id": south["objectId"], "x": south["x"], "y": south["y"], "plane": 0},
        ],
    )
    # Deliberately never removes the item -- every click "misses", the same as a real
    # MISTAKE_MISCLICK_CHANCE near-miss that lands nowhere useful.
    monkeypatch.setattr(controller, "get_ground_items", lambda fresh=False: [gem])
    monkeypatch.setattr(
        controller, "click_menu_action",
        lambda ser, entity, option, target_hint=None, offset=(0, 0), **kw: click_calls.append(entity) or True,
    )
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert len(click_calls) == gc.GOLEM_GEM_PICKUP_ATTEMPTS, "must retry up to the attempt cap, not give up after one click"
    assert ctx.state.data["gc_gem_bag_full"] is False, "must still clear the flag once retries are exhausted, to avoid spinning forever on a lost cause"
    assert ctx.state.data["gc_phase"] == "bank"


def test_gem_pickup_menu_click_passes_refresh_target(monkeypatch):
    """_collect_golem_gift's click_menu_action Take should re-resolve the gift's live screen
    position, same as every other ground-item menu click in this codebase (see slayer.py's
    maybe_loot for the reference pattern)."""
    station = _station("north", 2596, 2257)
    item = {"x": station["x"], "y": station["y"], "plane": 0, "id": 5, "name": "Uncut sapphire"}
    monkeypatch.setattr(controller, "get_ground_items", lambda fresh=False: [item])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    gc._collect_golem_gift(_ctx(), station, "Uncut sapphire")
    assert seen.get("refresh_target") is not None


# --- FINDING E: walk_to_exact_tile early-out / the branch's headline saving ----------

def test_walk_to_exact_tile_early_outs_when_already_standing_there(monkeypatch):
    """FINDING E, core fix: if the player already reports exactly (x, y, plane), walk_to_exact_
    tile must return True immediately without clicking anything or touching Shift."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2596, "y": 2257, "plane": 0})
    calls = []
    monkeypatch.setattr(controller, "get_ground_walk_point", lambda x, y, plane: calls.append("walk_point") or {"onScreen": True})
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: calls.append("click") or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, key: calls.append("key_down"))
    monkeypatch.setattr(controller, "key_up", lambda ser, key: calls.append("key_up"))

    ctx = _ctx()
    assert gc.walk_to_exact_tile(ctx, 2596, 2257, 0) is True
    assert calls == [], "already standing on the exact tile must short-circuit before any click machinery runs"


def test_walk_to_exact_tile_still_walks_when_not_already_there(monkeypatch):
    """The other half: the early-out must not swallow a genuine walk -- different (x, y) still
    goes through the normal click path."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2590, "y": 2250, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point", lambda x, y, plane: {"onScreen": True, "screenX": 100, "screenY": 100})
    monkeypatch.setattr(controller, "get_ground_walk_screen_pos", lambda x, y, plane: (100, 100))
    click_calls = []
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: click_calls.append(a) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, key: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, key: None)
    monkeypatch.setattr(gc.time, "sleep", lambda s: None)

    ctx = _ctx()
    assert gc.walk_to_exact_tile(ctx, 2596, 2257, 0) is True
    assert len(click_calls) == 1, "a genuine walk to a different tile must still click"


def test_insert_side_walk_is_a_no_op_after_the_full_carve(monkeypatch):
    """Pins the branch's headline saving (see craft_phase's own docstring: "the carve order ends
    on the insert side -- no extra walk before Insert-core"). Unlike every other craft_phase test
    in this file, walk_to_exact_tile is NOT replaced with a constant -- it's wrapped so its calls
    are both recorded AND actually executed, so this proves the insert-side walk is a genuine
    no-op (FINDING E: no click emitted) rather than merely a walk call nothing ever inspected."""
    station = _station("north", 2596, 2257, "south", progress=4, done=gc.SIDES)  # fully carved
    insert_tile = station["sides"]["south"]
    player_pos = {"x": insert_tile["x"], "y": insert_tile["y"], "plane": insert_tile["plane"]}

    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(controller, "get_player", lambda: dict(player_pos))

    walk_calls = []
    real_walk = gc.walk_to_exact_tile

    def spy_walk(ctx, x, y, plane, **kw):
        walk_calls.append((x, y, plane))
        return real_walk(ctx, x, y, plane, **kw)

    monkeypatch.setattr(gc, "walk_to_exact_tile", spy_walk)

    click_entity_calls = []
    monkeypatch.setattr(
        controller, "click_entity",
        lambda ser, entity, **kw: click_entity_calls.append(entity) or True,
    )
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone core"}])
    monkeypatch.setattr(gc, "_await_golem_complete", lambda *a, **k: True)

    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = 5

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert walk_calls == [(insert_tile["x"], insert_tile["y"], insert_tile["plane"])], \
        "the insert step must still call walk_to_exact_tile once, to confirm/no-op the position"
    ground_point_clicks = [c for c in click_entity_calls if "id" not in c]
    assert ground_point_clicks == [], \
        "FINDING E: no click may be emitted for the insert-side walk once the player is already standing there"
    station_clicks = [c for c in click_entity_calls if c.get("id") == station["objectId"]]
    assert len(station_clicks) == 1, "the Insert-core click itself must still happen"


# --- mine_phase: one tile, three rocks, swap on DEPLETION (2026-09-14) -----------------
#
# USER: "use the tile i stand on right now and keep mining the three adjacent nodes. exactly like
# we did with mining_guild iron"; "we click after each xp drop right now in mining we should just
# await node despawn like mining_guild". These pin the two rules that whole rewrite rests on:
# the player never leaves the stand tile, and a rock is mined until it is GONE rather than
# re-clicked on every XP drop (mining auto-repeats on its own, so those clicks bought nothing).
#
# The measured facts behind the numbers -- 2 ores per rock with expert gloves, 1.8s apart, a 5.4s
# respawn against a ~10.8s three-rock rotation -- are in MINING_TARGET_NAMES's comment block, read
# out of recordings/2026-09-14_164323_golem-carve-baseline.jsonl.

STAND = (2598, 2244, 0)
WEST = {"x": 2597, "y": 2244, "plane": 0, "id": 62393}
NORTH = {"x": 2598, "y": 2245, "plane": 0, "id": 62394}
EAST = {"x": 2599, "y": 2244, "plane": 0, "id": 62393}


class _FakeSock:
    """Identity token for bridge_events.EventStream -- controller.poll_udp is stubbed."""


def _events(monkeypatch, batches):
    """A real EventStream (not a mock) whose poll_udp yields each batch in turn, then nothing.
    Same idiom as tests/test_bridge_events.py's own _stream helper."""
    import bridge_events
    queue = list(batches)
    monkeypatch.setattr(controller, "poll_udp", lambda sock: queue.pop(0) if queue else [])
    return bridge_events.EventStream(_FakeSock())


def _obj(tile, oid, **extra):
    o = {"x": tile["x"], "y": tile["y"], "plane": tile.get("plane", 0), "id": oid,
         "name": "Sunstone rocks" if oid in gc.SUNSTONE_ROCK_IDS else "Rocks",
         "screenX": 100, "screenY": 100}
    o.update(extra)
    return o


# --- discovery: the rocks come from the stand tile, never a hardcoded list ---------------

def test_discover_rocks_takes_the_three_cardinal_neighbours():
    """CONFIRMED live 2026-09-14 off /objects: standing on (2598, 2244) puts rocks west, north and
    east, and nothing on the south tile."""
    objects = [_obj(WEST, 62393), _obj(NORTH, 62394), _obj(EAST, 62393)]
    rocks = gc.discover_rocks(objects, STAND)
    assert sorted((r["x"], r["y"], r["id"]) for r in rocks) == [
        (2597, 2244, 62393), (2598, 2245, 62394), (2599, 2244, 62393)]


def test_discover_rocks_ignores_diagonals_and_rocks_further_out():
    """A diagonal rock cannot be mined from the stand tile, and a rock two tiles away would
    reintroduce the walking this rewrite exists to delete."""
    objects = [
        _obj(WEST, 62393),
        _obj({"x": 2597, "y": 2245, "plane": 0}, 62393),   # diagonal
        _obj({"x": 2601, "y": 2242, "plane": 0}, 62393),   # three tiles away
    ]
    assert [(r["x"], r["y"]) for r in gc.discover_rocks(objects, STAND)] == [(2597, 2244)]


def test_discover_rocks_does_not_let_scenery_claim_a_rocks_tile():
    """Two of the three rock tiles also carry a null-named decoration, and the scene lists it
    first on one of them. Skipping a non-rock object must NOT mark the tile as decided."""
    scenery = {"x": WEST["x"], "y": WEST["y"], "plane": 0, "id": 3710, "name": None}
    rocks = gc.discover_rocks([scenery, _obj(WEST, 62393)], STAND)
    assert [(r["x"], r["y"], r["id"]) for r in rocks] == [(2597, 2244, 62393)]


def test_a_rock_that_was_spent_at_discovery_has_no_id_yet():
    """11390 is the spent form of BOTH live variants, so it cannot say which one this tile
    carries -- the tile is still registered, its id is filled in later."""
    rocks = gc.discover_rocks([_obj(NORTH, gc.SPENT_ROCK_ID)], STAND)
    assert [(r["x"], r["y"], r["id"]) for r in rocks] == [(2598, 2245, None)]


def test_resolve_rock_ids_fills_in_a_rock_that_has_since_respawned(monkeypatch):
    rocks = [dict(NORTH, id=None)]
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [_obj(NORTH, 62394)])
    assert gc._resolve_rock_ids(rocks)
    assert rocks[0]["id"] == 62394


def test_resolve_rock_ids_costs_nothing_once_every_id_is_known(monkeypatch):
    def boom(radius=None):
        raise AssertionError("must not re-scan the scene when every rock id is already known")
    monkeypatch.setattr(controller, "get_objects", boom)
    assert gc._resolve_rock_ids([dict(WEST), dict(NORTH)])


# --- the rotation ------------------------------------------------------------------------

def test_pick_rock_prefers_a_rock_other_than_the_one_just_spent():
    rocks = [dict(WEST), dict(NORTH), dict(EAST)]
    picked = gc.pick_rock(rocks, up=lambda r: True, last=(WEST["x"], WEST["y"]), spent_at={})
    assert (picked["x"], picked["y"]) != (WEST["x"], WEST["y"])


def test_pick_rock_takes_the_one_spent_longest_ago():
    rocks = [dict(WEST), dict(NORTH), dict(EAST)]
    spent_at = {(WEST["x"], WEST["y"]): 100.0, (NORTH["x"], NORTH["y"]): 50.0,
                (EAST["x"], EAST["y"]): 300.0}
    picked = gc.pick_rock(rocks, up=lambda r: True, last=None, spent_at=spent_at)
    assert (picked["x"], picked["y"]) == (NORTH["x"], NORTH["y"])


def test_pick_rock_returns_the_last_one_when_it_is_the_only_one_up():
    """With three rocks on a 3.6s cycle against a 5.4s respawn this should not happen -- but if a
    second player leaves only the rock we just finished, re-taking it beats standing still."""
    rocks = [dict(WEST), dict(NORTH)]
    up = lambda r: (r["x"], r["y"]) == (WEST["x"], WEST["y"])
    picked = gc.pick_rock(rocks, up=up, last=(WEST["x"], WEST["y"]), spent_at={})
    assert (picked["x"], picked["y"]) == (WEST["x"], WEST["y"])


def test_pick_rock_is_none_when_every_rock_is_spent():
    assert gc.pick_rock([dict(WEST), dict(NORTH)], up=lambda r: False, last=None, spent_at={}) is None


# --- the batch budget --------------------------------------------------------------------

def test_the_batch_counts_raw_and_cores_together():
    """CORRECTED 2026-08-08 live: chiselling converts raw into cores 1-for-1 and never changes the
    sum, so a trip resuming with unspent cores must mine only the difference."""
    assert gc.mine_batch_done(gc.SUNSTONE_PER_TRIP - 4, 4, 21)
    assert not gc.mine_batch_done(gc.SUNSTONE_PER_TRIP - 5, 4, 20)


def test_a_full_inventory_ends_the_batch_regardless_of_the_count():
    assert gc.mine_batch_done(1, 0, controller.INVENTORY_SIZE)


# --- Surplus fur, and the livelock it caused (2026-09-15, live) -------------------------------
#
# USER: "furr bag got deposit start again". A fur trip ends the moment fur SPILLS, which by
# definition means the pouch is FULL and there is loose fur beside it. That surplus rode home,
# survived the deposit (BANK_KEEP_ITEMS keeps "fur", for a different case), could not be packed
# into an already-full pouch, and left the inventory full at 2 sunstone + 5 cores.

def test_surplus_fur_is_only_surplus_once_the_pouch_is_full():
    inv = [{"name": "Dark kebbit fur"}, {"name": "Large fur pouch (open)"}, {"name": "Chisel"}]
    assert gc.surplus_loose_fur(inv, gc.FUR_POUCH_CAPACITY) == [{"name": "Dark kebbit fur"}]
    assert gc.surplus_loose_fur(inv, gc.FUR_POUCH_CAPACITY - 1) == [], \
        "with room left the loose fur is about to be packed, not surplus"


def test_the_pouch_itself_is_never_counted_as_surplus_fur():
    """Its name contains "fur" -- a substring rule that does not exclude it banks the pouch."""
    inv = [{"name": "Large fur pouch (open)"}]
    assert gc.surplus_loose_fur(inv, gc.FUR_POUCH_CAPACITY) == []


def test_the_surplus_deposit_keeps_the_pouch_and_the_batch(monkeypatch):
    import banker
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Dark kebbit fur"}] * 20)
    seen = {}
    monkeypatch.setattr(banker, "deposit_inventory",
                        lambda ctx, cfg: seen.update(cfg) or None)
    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = gc.FUR_POUCH_CAPACITY

    assert gc._deposit_surplus_fur(ctx) == 20
    keep = seen["keepItems"]
    assert "fur" not in keep, "the loose fur must become depositable"
    assert banker.FUR_POUCH_NAME in keep, "the pouch must not go with it"
    assert gc.RAW_SUNSTONE_NAME.lower() in keep and gc.CHISEL_NAME.lower() in keep


def test_a_full_inventory_with_an_unfinished_batch_banks_instead_of_chiselling(monkeypatch):
    """The livelock. mine flipped to chisel, which had its cores and flipped to craft, which had
    too little raw and flipped back to mine -- for 23 minutes. The stall watchdog could not see it
    because _progress_signature includes the phase, and the phase changed every iteration."""
    full = ([{"name": "Dark kebbit fur"}] * 21 + [{"name": "Sunstone"}] * 2
            + [{"name": "Sunstone core"}] * 5)
    assert len(full) == controller.INVENTORY_SIZE
    mined = _stub_chain(monkeypatch, full, ["depleted"])

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.data["gc_phase"] == "bank", "must shed the surplus, not cycle through chisel"
    assert mined == [], "and must not click a rock with no room for the ore"


def _no_fur_bank(monkeypatch, inventory=None):
    """An open bank holding no fur at all -- same stubs as the existing kit-up test."""
    import banker
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory or []))
    # Fully geared, so _withdraw_missing_mining_gear stays out of the way.
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": {"name": "Rune pickaxe"},
                                 "gloves": {"name": "Expert mining gloves"},
                                 "shield": {"name": "Imcando hammer (off-hand)"}})
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": []})
    monkeypatch.setattr(banker, "deposit_inventory", lambda ctx, cfg: None)
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    # "the bank genuinely holds no fur" -- without this the veto added 2026-09-15 consults the
    # real bridge's bank memory and (correctly) refuses to spend a trip.
    monkeypatch.setattr(gc, "_remembered_bank_fur", lambda: None)
    kitted = []
    monkeypatch.setattr(gc, "_withdraw_kit", lambda ctx, config: kitted.append(True) or True)
    return kitted


def test_an_empty_bank_does_not_burn_a_trip_before_the_pouch_is_ever_counted(monkeypatch):
    """A pouch's contents are not readable anywhere, so gc_fur_stock is an estimate that resets on
    restart while the pouch does not. CONFIRMED live 2026-09-15: restarting beside a FULL pouch
    would have kitted up for a five-minute hunt because the bank held no loose fur."""
    _fake_clock(monkeypatch)
    kitted = _no_fur_bank(monkeypatch)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    assert "gc_fur_stock" not in ctx.state.data

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)

    assert kitted == [], "must not start a fur trip on an uncounted pouch"
    assert ctx.state.data["gc_phase"] == "mine"


def test_an_empty_bank_does_start_a_trip_once_the_game_has_said_we_are_out(monkeypatch):
    """The game's own rejection message is the authoritative signal, and it must still win."""
    _fake_clock(monkeypatch)
    kitted = _no_fur_bank(monkeypatch)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    ctx.state.data["gc_fur_exhausted"] = True

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)

    assert kitted == [True]
    assert ctx.state.data["gc_phase"] == "fur_trip"


def test_an_empty_bank_starts_a_trip_once_a_count_has_been_established(monkeypatch):
    """A run that HAS counted the pouch and knows it is empty must still go hunting."""
    _fake_clock(monkeypatch)
    kitted = _no_fur_bank(monkeypatch)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    ctx.state.data["gc_fur_stock"] = 0

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)

    assert kitted == [True]
    assert ctx.state.data["gc_phase"] == "fur_trip"


def test_a_genuinely_full_batch_still_goes_to_chisel(monkeypatch):
    """The ordinary end of mining must be untouched by that guard."""
    inv = [{"name": "Sunstone"}] * 20 + [{"name": "Sunstone core"}] * 5
    mined = _stub_chain(monkeypatch, inv, ["depleted"])
    monkeypatch.setattr(gc, "_step_toward_plinth", lambda ctx, config: None)

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.data["gc_phase"] == "chisel"
    assert mined == []


# --- one rock, mined until it is gone ------------------------------------------------------

def _stub_one_rock(monkeypatch, live_after):
    """`live_after` is a list of /object/at answers consumed in order: a dict while the rock still
    stands, None once it has depleted."""
    answers = list(live_after)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, plane, oid, **kw: answers.pop(0) if answers else None)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "hover_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": False})
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)


def test_a_rock_is_mined_until_it_despawns_not_until_an_xp_drop(monkeypatch):
    """The user's own correction. The first ore leaves the rock standing (expert gloves), and the
    game keeps swinging on its own -- so that XP drop must NOT end the wait or produce a click."""
    _fake_clock(monkeypatch)          # the ore/grace budgets are real seconds otherwise
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)
    xp = [1000, 1000, 1023, 1023]     # base, still nothing, first ore, ...
    monkeypatch.setattr(controller, "get_player", lambda: {"miningXp": xp.pop(0) if len(xp) > 1 else xp[0]})
    _stub_one_rock(monkeypatch, [_obj(WEST, 62393), _obj(WEST, 62393), None])

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[], [], []])
    assert gc.mine_rock(ctx, dict(WEST), _obj(WEST, 62393)) == "depleted"
    assert len(clicks) == 1, "one click per rock -- the XP drop must not produce another"


def test_a_click_that_produced_nothing_is_reported_as_failed(monkeypatch):
    """No swing animation and no ore past MINE_CLICK_GRACE_S: the click did not take (another
    player on the pixel, the rock taken as we clicked)."""
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"miningXp": 1000})
    monkeypatch.setattr(controller, "get_object_at",
                        lambda x, y, plane, oid, **kw: _obj(WEST, 62393))
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": True})
    monkeypatch.setattr(gc.time, "sleep", lambda s: None)

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [])
    monkeypatch.setattr(gc, "MINE_CLICK_GRACE_S", 0.0)
    assert gc.mine_rock(ctx, dict(WEST), _obj(WEST, 62393)) == "failed"


def test_the_rock_click_never_simulates_a_misclick(monkeypatch):
    """MEASURED in mining_guild_iron (mgi_live6): a deliberate near-miss beside an ADJACENT rock
    is a Walk-here onto a neighbouring tile, and one step off the stand tile leaves the other two
    rocks out of reach. This is the single-tile design's load-bearing exception to
    simulate_mistakes."""
    _fake_clock(monkeypatch)
    seen = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: seen.update(kw) or True)
    monkeypatch.setattr(controller, "get_player", lambda: {"miningXp": 1000})
    _stub_one_rock(monkeypatch, [None])

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_rock(ctx, dict(WEST), _obj(WEST, 62393))
    assert seen.get("simulate_mistakes") is False


def test_the_next_rock_is_hovered_while_the_current_one_is_still_being_mined(monkeypatch):
    """The swap has to be a click IN PLACE, not a flight -- so the cursor is parked on the next
    rock during the current rock's ~3.6s of mining. The next rock is usually still SPENT at that
    moment (5.4s respawn), which is why the hover resolves the 11390 standing on its tile."""
    _fake_clock(monkeypatch)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"miningXp": 1000})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": False})

    def object_at(x, y, plane, oid, **kw):
        if (x, y) == (WEST["x"], WEST["y"]):
            return None                                  # the rock we clicked: already gone
        return _obj(NORTH, oid, screenX=200, screenY=150) if oid == gc.SPENT_ROCK_ID else None
    monkeypatch.setattr(controller, "get_object_at", object_at)
    hovers = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, entity, **kw: hovers.append(entity) or True)

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_rock(ctx, dict(WEST), _obj(WEST, 62393), hover_next=dict(NORTH))
    assert [(h["x"], h["y"]) for h in hovers] == [(NORTH["x"], NORTH["y"])]
    assert hovers[0]["screenX"] == 200, "the hover must use the SPENT rock's live screen position"


# --- the chain ----------------------------------------------------------------------------

def _stub_chain(monkeypatch, inventory, outcomes, up=None):
    """Drives mine_phase's chain with mine_rock stubbed: `outcomes` is consumed one per rock, and
    the rock it was called with is recorded. `up` decides which tiles report as ready."""
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": STAND[0], "y": STAND[1], "plane": STAND[2]})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [
        _obj(WEST, 62393), _obj(NORTH, 62394), _obj(EAST, 62393)])
    monkeypatch.setattr(controller, "get_object_at", lambda x, y, plane, oid, **kw:
                        _obj({"x": x, "y": y, "plane": plane}, oid)
                        if (up is None or up((x, y))) else None)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    monkeypatch.setattr(gc, "_park_on_rock", lambda ctx, rock: True)
    mined = []
    queue = list(outcomes)

    def fake_mine_rock(ctx, rock, target, hover_next=None, stop_when=None):
        mined.append((rock["x"], rock["y"]))
        return queue.pop(0) if queue else "no_click"
    monkeypatch.setattr(gc, "mine_rock", fake_mine_rock)
    return mined


def test_the_chain_rotates_through_the_three_rocks_without_ever_walking(monkeypatch):
    """The whole point (USER: "by the time we finished mining two one has already respawned
    meaning no walking"). Three depletions in a row must hit three DIFFERENT tiles, and nothing
    may walk."""
    walks = []
    monkeypatch.setattr(gc, "walk_to_exact_tile",
                        lambda ctx, x, y, plane, **kw: walks.append((x, y)) or True)
    monkeypatch.setattr(gc.travel, "walk_to", lambda *a, **kw: walks.append(a) or True)
    mined = _stub_chain(monkeypatch, [], ["depleted", "depleted", "depleted", "no_click"])

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert len(mined) == 4
    assert len(set(mined[:3])) == 3, f"must rotate across all three rocks, got {mined}"
    assert walks == [], "the stand tile is never left while rocks are in reach"


def test_the_chain_keeps_mining_across_rocks_instead_of_handing_tick_back(monkeypatch):
    """MEASURED in mining_guild_iron (mgi_live2): every hand-back to the engine loop cost 1.0-1.5s,
    which against a 3.6s rock is a third of the mining rate."""
    mined = _stub_chain(monkeypatch, [], ["depleted"] * 5 + ["no_click"])
    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(mined) == 6, "one call to mine_phase must mine many rocks, not one"


def test_the_chain_stops_and_moves_on_once_the_batch_is_full(monkeypatch):
    inventory = [{"name": "Sunstone"}] * gc.SUNSTONE_PER_TRIP
    mined = _stub_chain(monkeypatch, inventory, ["depleted"])
    steps = []
    monkeypatch.setattr(gc, "_step_toward_plinth", lambda ctx, config: steps.append(True))

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert mined == [], "the budget is checked BEFORE clicking, every iteration"
    assert ctx.state.data["gc_phase"] == "chisel"
    assert steps == [True], "the step toward the plinth is what breaks the mining animation"


def test_the_chain_hands_tick_back_when_a_break_comes_due(monkeypatch):
    """A break must not wait out a whole 25-stone batch. The gap between rocks is the safe place
    to take it -- nothing is in flight."""
    mined = _stub_chain(monkeypatch, [], ["depleted"] * 5)
    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    calls = {"n": 0}

    def break_due(cfg):
        calls["n"] += 1
        return calls["n"] > 2
    ctx.state.break_due = break_due

    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(mined) == 2, f"must stop at the break instead of mining the batch out, got {mined}"
    assert ctx.state.data.get("gc_phase") != "chisel", "the batch is unfinished -- stay in mine"


def test_the_chain_waits_out_the_respawn_rather_than_walking_to_another_rock(monkeypatch):
    """Every rock spent at once means another player is on the spot. The old behaviour -- go find
    whichever rock is nearest -- is exactly what this rewrite removes: 5.4s of waiting beats a
    walk that ends with the three adjacent rocks out of reach."""
    mined = _stub_chain(monkeypatch, [], ["depleted"], up=lambda tile: False)
    sleeps = []
    monkeypatch.setattr(gc.time, "sleep", lambda s: sleeps.append(s))
    walks = []
    monkeypatch.setattr(gc, "walk_to_exact_tile",
                        lambda ctx, x, y, plane, **kw: walks.append((x, y)) or True)
    monkeypatch.setattr(gc, "MINE_CHAIN_MAX_ROCKS", 3)

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert mined == [], "nothing is clickable, so nothing may be clicked"
    assert sleeps and all(s == gc.MINE_RESPAWN_WAIT_S for s in sleeps)
    assert walks == []


def test_the_chain_walks_back_onto_the_stand_tile_when_it_is_off_it(monkeypatch):
    """Arriving from the bank or a fur trip, and the one recovery this design actually needs: a
    stray Walk-here leaves the other two rocks out of reach, so position is re-checked every
    iteration."""
    _stub_chain(monkeypatch, [], ["depleted"])
    here = {"x": 2590, "y": 2240, "plane": 0}
    monkeypatch.setattr(controller, "get_player", lambda: dict(here))
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane, **kw: {"onScreen": True})
    walks = []

    def walk(ctx, x, y, plane, **kw):
        walks.append((x, y, plane))
        here.update(x=x, y=y, plane=plane)      # this walk actually arrives
        return True
    monkeypatch.setattr(gc, "walk_to_exact_tile", walk)

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert walks == [STAND], "must walk back to the exact stand tile, not near it"


def test_the_walk_back_retries_inside_one_call_instead_of_one_try_per_tick(monkeypatch):
    """MEASURED live 2026-09-14 on both bank trips: one attempt per call, then a hand-back to the
    engine, cost ~25s to get home from the bank -- three attempts got there, so the walk worked
    and the asking-once was the slow part."""
    _stub_chain(monkeypatch, [], ["depleted"])
    here = {"x": 2590, "y": 2240, "plane": 0}
    monkeypatch.setattr(controller, "get_player", lambda: dict(here))
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane, **kw: {"onScreen": True})
    walks = []

    def walk(ctx, x, y, plane, **kw):
        walks.append((x, y, plane))
        if len(walks) >= 3:                     # the first two fall short, as they did live
            here.update(x=x, y=y, plane=plane)
        return True
    monkeypatch.setattr(gc, "walk_to_exact_tile", walk)

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert len(walks) == 3, f"must keep trying within the call, got {walks}"
    assert not ctx.state.safety_stopped


def test_the_walk_back_is_paced_so_a_no_op_walk_cannot_spin(monkeypatch):
    """walk_to_exact_tile returns immediately when the tile cannot be projected at all. Without a
    pause between attempts the retry loop spun 1.6 MILLION times against its budget -- caught by
    this test, not in production."""
    _stub_chain(monkeypatch, [], ["depleted"])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2590, "y": 2240, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane, **kw: {"onScreen": True})
    walks = []
    monkeypatch.setattr(gc, "walk_to_exact_tile",
                        lambda ctx, x, y, plane, **kw: walks.append((x, y)) or True)
    pauses = []
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: pauses.append(a))

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert len(walks) <= gc.MINE_RETURN_MAX_ATTEMPTS, f"unbounded retries: {len(walks)}"
    assert gc.MINE_RETURN_RETRY_PAUSE_S in pauses, "retries must be paced"


def test_the_mining_anchor_is_the_tile_the_rocks_are_discovered_from():
    """Moving the spot is a one-line config change -- the three rocks are never hardcoded."""
    assert gc._mine_stand(gc.DEFAULT_CONFIG) == STAND


# --- The fur trip strips the mining gear; something has to put it back (2026-09-14) ----------
#
# USER: "Both weapon Slot and gloves need to be uneqiped and after route collected fur we need to
# reequip". falconry._ensure_hands_free empties the weapon, shield and glove slots because
# falconry needs both hands free -- correct going out, and never undone coming back. Mining with
# no pickaxe does nothing at all, and without the Expert gloves a rock gives ONE ore instead of
# two, which halves the whole activity.

def _gear_world(monkeypatch, equipment, inventory):
    monkeypatch.setattr(controller, "get_equipment", lambda: equipment)
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory))
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.append(entity.get("name")) or True)
    return clicked


def test_the_pickaxe_is_worn_back_after_the_trip(monkeypatch):
    clicked = _gear_world(monkeypatch, equipment={"weapon": {}, "gloves": {}, "shield": {}},
                          inventory=[{"name": "Rune pickaxe", "screenX": 1, "screenY": 1}])
    assert gc.ensure_mining_gear(_ctx()) is True
    assert clicked == ["Rune pickaxe"]


def test_the_mining_gloves_are_worn_back_too(monkeypatch):
    clicked = _gear_world(
        monkeypatch,
        equipment={"weapon": {"name": "Rune pickaxe"}, "gloves": {}, "shield": {}},
        inventory=[{"name": "Expert mining gloves", "screenX": 1, "screenY": 1}])
    assert gc.ensure_mining_gear(_ctx()) is True
    assert clicked == ["Expert mining gloves"]


def test_nothing_is_worn_back_when_every_slot_is_already_filled(monkeypatch):
    clicked = _gear_world(
        monkeypatch,
        equipment={"weapon": {"name": "Rune pickaxe"}, "gloves": {"name": "Expert mining gloves"},
                   "shield": {"name": "Imcando hammer (off-hand)"}},
        inventory=[{"name": "Rune pickaxe", "screenX": 1, "screenY": 1}])
    assert gc.ensure_mining_gear(_ctx()) is False
    assert clicked == []


def test_an_empty_slot_with_nothing_to_fill_it_is_left_alone(monkeypatch):
    """Only ever acts on an empty slot with the item actually carried, so a deliberately
    different setup is never overridden and a missing item never loops."""
    clicked = _gear_world(monkeypatch, equipment={"weapon": {}, "gloves": {}, "shield": {}},
                          inventory=[{"name": "Sunstone", "screenX": 1, "screenY": 1}])
    assert gc.ensure_mining_gear(_ctx()) is False
    assert clicked == []


def test_the_bank_keeps_the_mining_gear_it_is_about_to_need():
    """CONFIRMED live 2026-09-14 (USER: "Oh shit we just deposited our rune pickaxe. And also our
    gloves"). The fur trip leaves the gear LOOSE in the inventory, and the bank trip home
    deposited it because the keep list never named a pickaxe or mining gloves -- "hammer" was
    there, which is exactly why the Imcando hammer came back and the other two did not."""
    keep = [k.lower() for k in gc.BANK_KEEP_ITEMS]
    for _slot, needle in gc.MINING_GEAR:
        assert needle in keep, f"the bank would deposit the {needle}"


def test_missing_gear_is_what_is_neither_worn_nor_carried():
    worn = {"weapon": {"name": "Rune pickaxe"}, "shield": {}, "gloves": {}}
    carried = [{"name": "Expert mining gloves"}]
    assert gc.missing_mining_gear(worn, carried) == ["imcando hammer"]
    assert gc.missing_mining_gear(
        {"weapon": {"name": "Rune pickaxe"}, "gloves": {"name": "Expert mining gloves"},
         "shield": {"name": "Imcando hammer (off-hand)"}}, []) == []


def test_missing_gear_is_withdrawn_at_the_open_bank(monkeypatch):
    """The recovery for an account this already happened to, and for a pickaxe lost any other
    way -- the fixed keep list only stops it recurring."""
    import banker
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": {}, "shield": {"name": "Imcando hammer (off-hand)"},
                                 "gloves": {}})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    pulled = []
    monkeypatch.setattr(banker, "search_and_withdraw",
                        lambda ctx, name, qty, contains=False: pulled.append(name) or True)

    gc._withdraw_missing_mining_gear(_ctx())

    assert pulled == ["pickaxe", "mining gloves"]


def test_mine_phase_puts_the_gear_on_before_it_mines_anything(monkeypatch):
    mined = _stub_chain(monkeypatch, [], ["depleted"])
    monkeypatch.setattr(gc, "ensure_mining_gear", lambda ctx: True)
    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[]])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)
    assert mined == [], "must not click a rock while the pickaxe is still in the inventory"


# --- The meat pouch (2026-09-14) --------------------------------------------------------------

# --- A restart mid-trip resumes from the live position (2026-09-14, live) --------------------
#
# ctx.state.data resets on every restart and routine reload; the character's position does not.
# CONFIRMED live: restarting while standing at Piscatoris defaulted to "bank", spent six walk
# attempts hunting for the Wyrmscraig bank a thousand tiles away, and safety-stopped.

def test_a_fresh_start_at_wyrmscraig_banks_as_before():
    assert gc.resume_phase({"regionId": 10275, "y": 2244}, {}) == "bank"


def test_a_fresh_start_at_the_piscatoris_landing_resumes_the_trip():
    assert gc.resume_phase({"regionId": falconry.REGION_FAIRY_LANDING, "y": 3619}, {}) == "fur_trip"


def test_a_fresh_start_in_the_cavern_resumes_the_trip():
    assert gc.resume_phase({"regionId": 10275, "y": 8650}, {}) == "fur_trip"


def test_a_fresh_start_somewhere_unrecognised_falls_back_to_bank():
    """bank_phase's own walk-to-the-anchor recovery beats a trip leg that would have to guess."""
    assert gc.resume_phase({"regionId": 12582, "y": 2447}, {}) == "bank"


def test_tick_picks_the_starting_phase_from_position(monkeypatch):
    calls = []
    ctx = _tick_world(monkeypatch, {"fur_trip": lambda c, cfg: calls.append("fur_trip")})
    # AFTER _tick_world, which stubs get_player itself -- the last setattr wins.
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"regionId": falconry.REGION_FAIRY_LANDING, "y": 3619})
    ctx.state.data.pop("gc_phase", None)

    gc.tick(ctx)

    assert calls == ["fur_trip"], "a restart mid-trip must not start by looking for the bank"


# --- Wyrmscraig's collision map is unusable; shortcuts are the real connections ---------------
#
# MEASURED live 2026-09-14 across the whole area: pockets of 1 to 841 tiles. The cathedral the
# cavern exit lands beside is 123 tiles, the MINING tile's own component is 41, and the bank
# anchor is a 1-tile island -- so travel.walk_to, which refuses without a route, cannot cross the
# region. USER, stuck outside the cathedral: "It should take the agility shortcut just ahead".

CATHEDRAL = {"x": 2530, "y": 2205, "plane": 0}
BANK_ANCHOR = {"x": 2587, "y": 2260, "plane": 0}
ROCKS = {"id": 62265, "name": "Rocks", "x": 2551, "y": 2209, "plane": 0, "distance": 21,
         "actions": ["Climb"]}
CAVE_BEHIND = {"id": 62219, "name": "Cave", "x": 2530, "y": 2206, "plane": 0, "distance": 1,
               "actions": ["Enter"]}


def _routes_from(*open_tiles):
    """A find_path that only succeeds from the given tiles -- i.e. only those are on a component
    that reaches the goal."""
    allowed = set(open_tiles)
    return lambda sx, sy, plane, gx, gy, **kw: ["step"] if (sx, sy) in allowed else []


def test_the_rocks_shortcut_is_taken_because_its_far_side_opens_a_route():
    """The real geometry, MEASURED: from (2542, 2213) the tiles just east of the Rocks --
    (2554, 2209) and (2555, 2209) -- are in a 190-tile bank-routable component, and nothing on
    our side is."""
    here = {"x": 2542, "y": 2213, "plane": 0}
    found = gc.find_shortcut_toward([CAVE_BEHIND, ROCKS], here, BANK_ANCHOR,
                                    find_path=_routes_from((2554, 2209), (2555, 2209)))
    assert found is ROCKS


def test_the_rocks_are_taken_even_though_they_score_WORSE_on_distance():
    """The regression this replaced. Chebyshev from (2542, 2213) to the bank is 47; from the
    Rocks it is 51 -- they sit north of the goal line -- so a closer-is-better rule rejected the
    one shortcut that works and hopped into the wall instead."""
    here = {"x": 2542, "y": 2213, "plane": 0}
    assert max(abs(here["x"] - BANK_ANCHOR["x"]), abs(here["y"] - BANK_ANCHOR["y"])) == 47
    assert max(abs(ROCKS["x"] - BANK_ANCHOR["x"]), abs(ROCKS["y"] - BANK_ANCHOR["y"])) == 51
    assert gc.find_shortcut_toward([ROCKS], here, BANK_ANCHOR,
                                   find_path=_routes_from((2554, 2209))) is ROCKS


def test_duplicate_objects_do_not_crowd_out_the_shortcut_that_works():
    """CONFIRMED live 2026-09-15: /objects reports one object once per model it is built from, so
    the cathedral Cave came back THREE times at distance 1. With the candidate list truncated
    those copies ate the probe budget and the Rocks at distance 20 -- the only way out of that
    pocket -- were never looked at. The trip hopped at a wall until the stall watchdog fired."""
    here = {"x": 2531, "y": 2205, "plane": 0}
    objects = [CAVE_BEHIND, dict(CAVE_BEHIND), dict(CAVE_BEHIND),
               {"id": 62254, "name": "Staircase", "x": 2541, "y": 2213, "plane": 0,
                "distance": 10, "actions": ["Climb-down"]},
               {"id": 62250, "name": "Church pew", "x": 2538, "y": 2216, "plane": 0,
                "distance": 11, "actions": ["Climb"]},
               {"id": 62250, "name": "Church pew", "x": 2538, "y": 2216, "plane": 0,
                "distance": 11, "actions": ["Climb"]},
               dict(ROCKS, distance=20)]

    found = gc.find_shortcut_toward(objects, here, BANK_ANCHOR,
                                    find_path=_routes_from((2554, 2209), (2555, 2209)))

    assert found is not None and found["name"] == "Rocks"


def test_the_cave_we_just_came_out_of_is_not_taken():
    """One tile away and carrying "Enter", so it wins on distance -- but its own side of the
    world is the pocket we are trying to leave, so no probe tile routes anywhere."""
    assert gc.find_shortcut_toward([CAVE_BEHIND], CATHEDRAL, BANK_ANCHOR,
                                   find_path=_routes_from()) is None


def test_an_object_with_no_shortcut_action_is_not_one():
    tree = {"id": 1, "name": "Tree", "x": 2560, "y": 2230, "plane": 0, "distance": 5,
            "actions": ["Chop down"]}
    assert gc.find_shortcut_toward([tree], CATHEDRAL, BANK_ANCHOR,
                                   find_path=_routes_from((2561, 2231))) is None


def _missing_target_world(monkeypatch, positions):
    """Drives handle_missing_target past its miss tolerance so every call reaches the walk step,
    with the player standing at `positions[i]` on attempt i."""
    seq = list(positions)
    state = {"i": 0}

    def get_player():
        p = seq[min(state["i"], len(seq) - 1)]
        return {"x": p[0], "y": p[1], "plane": 0}
    monkeypatch.setattr(controller, "get_player", get_player)
    walks = []

    def travel_toward(ctx, target):
        walks.append(state["i"])
        state["i"] += 1
        return True
    monkeypatch.setattr(gc, "_travel_toward", travel_toward)
    return walks


def test_a_walk_that_is_actually_moving_does_not_exhaust_the_attempt_budget(monkeypatch):
    """CONFIRMED live: the walk home takes an agility shortcut, and each attempt spent one of the
    six tries -- so the run safety-stopped while standing on the far side of the rocks, having
    just made exactly the progress the cap exists to detect the ABSENCE of."""
    moving = [(2530 + i * 6, 2205 + i * 2) for i in range(gc.MAX_WALK_ATTEMPTS + 6)]
    walks = _missing_target_world(monkeypatch, moving)
    ctx = _ctx()
    for _ in range(gc.MAX_WALK_ATTEMPTS + 5):
        ctx.state.data["gc_mine_misses"] = gc.MAX_CONSECUTIVE_MISSES
        assert gc.handle_missing_target(ctx, gc.DEFAULT_CONFIG, "bank",
                                        "gc_mine_misses", "gc_mine_walks") is True
    assert len(walks) == gc.MAX_WALK_ATTEMPTS + 5, "a moving walk must keep being allowed"


def test_a_walk_that_never_moves_still_gives_up(monkeypatch):
    """The cap must still bite when nothing is changing -- that is what it is for."""
    stuck = [(2530, 2205)] * 40
    _missing_target_world(monkeypatch, stuck)
    ctx = _ctx()
    results = []
    for _ in range(gc.MAX_WALK_ATTEMPTS + 3):
        ctx.state.data["gc_mine_misses"] = gc.MAX_CONSECUTIVE_MISSES
        results.append(gc.handle_missing_target(ctx, gc.DEFAULT_CONFIG, "bank",
                                                "gc_mine_misses", "gc_mine_walks"))
    assert results[-1] is False, "a target that never gets closer must still be given up on"


def test_the_probe_ring_walks_outward_from_the_object():
    ring = list(gc._probe_ring(10, 10, 2))
    assert (11, 10) in ring and (12, 12) in ring
    assert (10, 10) not in ring, "the object's own tile is not its far side"
    assert ring.index((11, 10)) < ring.index((12, 12)), "nearest ring first"


def test_travel_toward_routes_when_the_map_actually_has_a_route(monkeypatch):
    """The fallbacks must not displace an ordinary walk where routing works."""
    monkeypatch.setattr(controller, "get_player", lambda: dict(CATHEDRAL))
    monkeypatch.setattr(gc, "_nearest_walkable_tile", lambda t: t)
    monkeypatch.setattr(gc.worldmap, "find_path", lambda *a, **kw: ["step"])
    routed = []
    monkeypatch.setattr(gc.travel, "walk_to",
                        lambda ctx, t, **kw: routed.append((t["x"], t["y"])) or True)
    hops = []
    monkeypatch.setattr(gc, "_hop_toward_point", lambda ctx, x, y, p: hops.append((x, y)) or True)

    assert gc._travel_toward(_ctx(), BANK_ANCHOR) is True
    assert routed == [(2587, 2260)] and hops == []


def test_travel_toward_walks_adjacent_when_the_goal_tile_itself_is_sealed(monkeypatch):
    """CONFIRMED live: the bank anchor (2587, 2260) is a 1-TILE ISLAND on the map. Testing
    routability with goal_adjacent=True and then walking with goal_adjacent=False made the
    routine decide a route existed while travel.walk_to answered "no walking route" once a tick,
    standing still until the attempt cap stopped it."""
    monkeypatch.setattr(controller, "get_player", lambda: dict(CATHEDRAL))
    monkeypatch.setattr(gc, "_nearest_walkable_tile", lambda t: t)
    monkeypatch.setattr(gc.worldmap, "find_path",
                        lambda sx, sy, p, gx, gy, **kw: ["step"] if kw.get("goal_adjacent") else [])
    walked = []
    monkeypatch.setattr(gc.travel, "walk_to",
                        lambda ctx, t, **kw: walked.append(kw.get("goal_adjacent")) or True)

    assert gc._travel_toward(_ctx(), BANK_ANCHOR) is True
    assert walked == [True], "must walk with the adjacency that actually had a route"


def test_travel_toward_takes_a_shortcut_when_there_is_no_route(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(CATHEDRAL))
    monkeypatch.setattr(gc, "_nearest_walkable_tile", lambda t: t)
    # No route from where we stand, but the far side of the Rocks is on a routable component --
    # the real measured shape of the cathedral pocket.
    monkeypatch.setattr(gc.worldmap, "find_path", _routes_from((2554, 2209), (2555, 2209)))
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [CAVE_BEHIND, ROCKS])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: None)
    monkeypatch.setattr(gc, "_await_player_stopped", lambda ctx, s: True)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicked.append(e.get("name")) or True)
    hops = []
    monkeypatch.setattr(gc, "_hop_toward_point", lambda ctx, x, y, p: hops.append((x, y)) or True)

    assert gc._travel_toward(_ctx(), BANK_ANCHOR) is True
    assert clicked == ["Rocks"], "the shortcut must be preferred over a blind hop"
    assert hops == []


def test_the_scan_radius_reaches_the_rocks_from_the_cavern_exit():
    """MEASURED live 2026-09-15 from the tile the cavern exit drops you on, (2530, 2205): the
    Rocks out of the cathedral pocket are 21 tiles away and the far pair 23. A radius-20 scan
    returned Cave, Church pew and Staircase only, so the one shortcut that works was never even a
    candidate. The previous stall was diagnosed one tile east, where the same rocks measure
    exactly 20 -- that margin was luck."""
    exit_tile = (2530, 2205)
    rocks = (2551, 2209)
    gap = max(abs(exit_tile[0] - rocks[0]), abs(exit_tile[1] - rocks[1]))
    assert gap == 21
    assert gc.SHORTCUT_SCAN_RADIUS > gap, "the way out has to be inside the scan"
    assert gc.SHORTCUT_SCAN_RADIUS >= 23, "the far Rocks (23) should be reachable too"


def test_a_failed_shortcut_search_reports_what_it_saw(monkeypatch):
    """Three stalls in one pocket, three different causes, and every one of them logged the same
    opaque line a few hundred times. The candidates it found are what tells them apart."""
    monkeypatch.setattr(controller, "get_player", lambda: dict(CATHEDRAL))
    monkeypatch.setattr(gc, "_nearest_walkable_tile", lambda t: t)
    monkeypatch.setattr(gc.worldmap, "find_path", lambda *a, **kw: [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [CAVE_BEHIND])
    monkeypatch.setattr(gc, "_hop_toward_point", lambda ctx, x, y, p: True)
    logged = []
    monkeypatch.setattr(gc.debug_log, "log", lambda cat, msg: logged.append(msg))

    ctx = _ctx()
    gc._travel_toward(ctx, BANK_ANCHOR)
    assert any("Cave@(2530,2206)" in m for m in logged), f"got {logged}"

    # Same situation again -- one line, not one per tick.
    before = len(logged)
    gc._travel_toward(ctx, BANK_ANCHOR)
    assert len(logged) == before, "an unchanged situation must not re-log"


def test_travel_toward_falls_back_to_a_hop_with_no_route_and_no_shortcut(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(CATHEDRAL))
    monkeypatch.setattr(gc, "_nearest_walkable_tile", lambda t: t)
    monkeypatch.setattr(gc.worldmap, "find_path", lambda *a, **kw: [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])
    hops = []
    monkeypatch.setattr(gc, "_hop_toward_point", lambda ctx, x, y, p: hops.append((x, y)) or True)

    assert gc._travel_toward(_ctx(), BANK_ANCHOR) is True
    assert hops == [(2587, 2260)]


# --- The slayer ring's destination dialogue is PAGED (2026-09-14, live) ----------------------
#
# USER: "On slayer ring there is dialog options to tp or a second menu that expand when hovering
# tele". MEASURED live off /api/ui/dialogue -- Wyrmscraig is not on page one:
#   page 1: 1 Stronghold Slayer Cave | 2 Morytania Slayer Tower | 3 Fremennik Slayer Dungeon
#           4 More options...
#   page 2: 1 Tarn's Lair | 2 Dark Beasts | 3 the Wyrmscraig Cavern | 4 Previous options...
# The single-page scan looked for an entry that was never going to be on the page it read, and
# the return leg retried once a tick with the fur pouch already full.

PAGE_1 = ["Teleport to the Stronghold Slayer Cave", "Teleport to the Morytania Slayer Tower",
          "Teleport to the Fremennik Slayer Dungeon", "More options..."]
PAGE_2 = ["Teleport to Tarn's Lair", "Teleport to Dark Beasts",
          "Teleport to the Wyrmscraig Cavern", "Previous options..."]


def _paged_dialogue(monkeypatch, pages):
    """A dialogue whose 'More options...' key advances to the next page."""
    state = {"page": 0}
    keys = []

    def options():
        return pages[state["page"]]

    def key_tap(ser, key):
        keys.append(key)
        chosen = pages[state["page"]][int(key) - 1]
        if "more option" in chosen.lower():
            state["page"] = min(state["page"] + 1, len(pages) - 1)
    monkeypatch.setattr(gc, "_dialogue_options", options)
    monkeypatch.setattr(controller, "key_tap", key_tap)
    # Deliberately NOT stubbing sleep: _fake_clock's sleep is what ADVANCES the fake clock, so a
    # no-op here freezes time and the bounded wait can never time out. That hung the suite once.
    return keys


def test_the_wyrmscraig_entry_is_found_on_the_second_page(monkeypatch):
    _fake_clock(monkeypatch)
    keys = _paged_dialogue(monkeypatch, [PAGE_1, PAGE_2])

    assert gc._choose_dialogue_option(_ctx(), "wyrmscraig") is True
    assert keys == ["4", "3"], "must take 'More options...' (4) then Wyrmscraig (3)"


def test_a_first_page_hit_does_not_page_at_all(monkeypatch):
    _fake_clock(monkeypatch)
    keys = _paged_dialogue(monkeypatch, [PAGE_2, PAGE_1])
    assert gc._choose_dialogue_option(_ctx(), "wyrmscraig") is True
    assert keys == ["3"]


def test_a_dialogue_that_never_advances_cannot_spin(monkeypatch):
    """A page-forward that answers with the same options must not be pressed forever."""
    _fake_clock(monkeypatch)
    keys = _paged_dialogue(monkeypatch, [PAGE_1])          # 'More options...' goes nowhere

    assert gc._choose_dialogue_option(_ctx(), "wyrmscraig") is False
    assert keys.count("4") <= gc.DIALOGUE_MAX_PAGES, f"paged forever: {keys}"


def test_find_dialogue_option_is_a_case_insensitive_substring():
    assert gc.find_dialogue_option(PAGE_2, "wyrmscraig") == 2
    assert gc.find_dialogue_option(PAGE_1, "wyrmscraig") is None


def test_the_meat_pouch_is_carried_exactly_when_the_kebbit_drops_meat():
    """USER: "we forgot a meat bad / pouch meaning we are going to have to drop a bunch extra
    losing alot of time" -- CONFIRMED in this account's bank memory as "Large meat pouch (open)".
    Then the hunt switched to the Dark kebbit, which MEASURED live drops no meat at all (16
    retrieves, 16 bone drops, zero meat), so the pouch would be a withdrawal and a slot spent on
    nothing. The rule, not the current species, is what is pinned here."""
    wanted = falconry.MEAT_NAME is not None
    assert (gc.MEAT_POUCH_NAME in gc.KIT_ITEMS) is wanted


def test_a_missing_meat_pouch_does_not_stop_the_trip(monkeypatch):
    """It is an optimisation with a working fallback (drop the meat, as before); only the travel
    items strand the character."""
    _fake_clock(monkeypatch)
    import banker
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Ardougne cloak 4"}, {"name": "Slayer ring (8)"},
                                 {"name": "Coins", "qty": 5000}])
    monkeypatch.setattr(banker, "search_and_withdraw",
                        lambda ctx, name, qty, contains=False: False)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()

    gc._withdraw_kit(ctx, gc.DEFAULT_CONFIG)

    assert not ctx.state.safety_stopped, "a missing meat pouch must not strand the run"


# --- The container deposit empties the fur pouch (2026-09-16) -----------------------------------
#
# USER: "when it comes back from fur gathering trip it deposits all fur but never picks it up then
# goes mining". banker.deposit_inventory ends with deposit_all_containers(), the bank's own
# "Deposit Containers" button, which empties EVERY gatherer container -- including our fur pouch.
# MEASURED live, the game chat said it outright: "You empty all of your containers into the bank."
# / "Your fur pouch is empty." On ordinary visits the withdraw-all below hid it; on a fur-trip
# return the "fur already stocked" short-circuit skipped the withdrawal, so the pouch was emptied
# and never refilled and the run went mining against a tracked 28 that did not exist.

def _bank_with_fur(monkeypatch, *, stock, bank_fur=29):
    import banker
    _fake_clock(monkeypatch)
    monkeypatch.setattr(banker, "deposit_inventory", lambda ctx, cfg: None)
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx, m: True)
    monkeypatch.setattr(gc, "_deposit_spare_chisel", lambda ctx: False)
    monkeypatch.setattr(gc, "_deposit_surplus_fur", lambda ctx: 0)
    monkeypatch.setattr(gc, "_withdraw_missing_mining_gear", lambda ctx: None)
    monkeypatch.setattr(gc, "_make_room_for_fur", lambda ctx: False)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Dark kebbit fur", "qty": 1}])
    monkeypatch.setattr(controller, "get_bank",
                        lambda **kw: {"items": [{"name": "Dark kebbit fur", "qty": bank_fur,
                                                 "screenX": 5, "screenY": 5}]})
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicks.append(e.get("name")) or True)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    ctx.state.data["gc_fur_stock"] = stock
    return ctx, clicks


def test_a_fur_trip_return_re_withdraws_the_fur_the_deposit_just_took(monkeypatch):
    """The reported bug: stock reads a full pouch, so the withdrawal was skipped entirely."""
    ctx, clicks = _bank_with_fur(monkeypatch, stock=gc.FUR_POUCH_CAPACITY)

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)

    assert "Dark kebbit fur" in clicks, (
        "the container deposit emptied the pouch, so the fur MUST be withdrawn again")
    assert ctx.state.data["gc_phase"] == "mine"


def test_the_tracked_stock_never_survives_a_container_deposit(monkeypatch):
    """gc_fur_stock is an estimate the deposit invalidates; trusting it is what sent the run
    mining against an empty pouch."""
    ctx, _ = _bank_with_fur(monkeypatch, stock=gc.FUR_POUCH_CAPACITY)
    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)
    # Recounted from what actually ARRIVED in the inventory (1 here, per the stub) -- never the
    # bank's stack size of 29, and never the stale 28 the emptied pouch was still being tracked
    # at. That recount rule is itself a measured fix (2026-09-14): Withdraw-All moves only as many
    # as there are free slots.
    assert ctx.state.data["gc_fur_stock"] == 1
    assert ctx.state.data["gc_fur_stock"] != gc.FUR_POUCH_CAPACITY


# --- "Bank to shed it" must be able to shed something (2026-09-16) ------------------------------
#
# MEASURED LIVE (USER: "full inventory with sunstone open bank close bank click fur bag open bank
# again looping forever"): 24 Sunstone + gem sack + Chisel + Jeweller's chisel + fur pouch = 28/28.
# FOUR kit slots, not the three SUNSTONE_PER_TRIP=25 was sized against, so 25 is unreachable and
# mine_phase's full-inventory guard fired every lap. The bank deposit shed nothing -- the keep list
# protects sunstone, cores, and matches the second chisel on "chisel" -- so the remedy re-triggered
# its own symptom about every 6 seconds.

def _kit_only_full_inventory(stones=24):
    return ([{"name": gc.RAW_SUNSTONE_NAME} for _ in range(stones)]
            + [{"name": "Open gem sack"}, {"name": "Chisel"},
               {"name": "Jeweller's chisel"}, {"name": "Large fur pouch (open)"}])


def test_the_spare_chisel_is_the_plain_one_when_both_are_carried():
    """USER 2026-09-16: "It had gotten an extra chisel from the golem drop list ... and because
    chisel are protected it kept both chisel and started looping". The Jeweller's is the keeper --
    chisel_burst_order prefers it and falls back to the plain Chisel only when it is absent."""
    inv = [{"name": gc.JEWELLERS_CHISEL_NAME}, {"name": gc.CHISEL_NAME},
           {"name": gc.RAW_SUNSTONE_NAME}]
    spare = gc.spare_chisel(inv)
    assert spare is not None and spare["name"] == gc.CHISEL_NAME


def test_one_chisel_alone_is_never_banked():
    for only in (gc.CHISEL_NAME, gc.JEWELLERS_CHISEL_NAME):
        assert gc.spare_chisel([{"name": only}, {"name": gc.RAW_SUNSTONE_NAME}]) is None, only


def test_spare_chisel_matches_exact_names_not_substrings():
    """"Chisel" is a SUBSTRING of "Jeweller's chisel" -- which is exactly how one needle on the
    substring-matched keep list came to protect two different items."""
    assert gc.spare_chisel([{"name": gc.JEWELLERS_CHISEL_NAME}]) is None, (
        "a lone Jeweller's chisel must not be read as 'both chisels carried'")


def test_an_inventory_of_nothing_but_kit_sheds_nothing():
    inv = _kit_only_full_inventory()
    assert len(inv) == controller.INVENTORY_SIZE, "the measured loop state"
    assert gc.sheddable_slots(inv) == 0, (
        "every slot is on BANK_KEEP_ITEMS -- a bank trip cannot make this inventory smaller")


def test_surplus_loose_fur_is_still_sheddable():
    """The 2026-09-15 case this guard was written for must keep working: _deposit_surplus_fur
    drops 'fur' from the keep list for exactly this, so the two have to agree."""
    inv = ([{"name": gc.RAW_SUNSTONE_NAME} for _ in range(2)]
           + [{"name": gc.SUNSTONE_CORE_NAME} for _ in range(5)]
           + [{"name": "Dark kebbit fur"} for _ in range(18)]
           + [{"name": "Chisel"}, {"name": "Open gem sack"}, {"name": "Large fur pouch (open)"}])
    assert gc.sheddable_slots(inv) == 18


def test_junk_the_bank_would_take_is_sheddable():
    inv = [{"name": gc.RAW_SUNSTONE_NAME}, {"name": "Bones"}, {"name": "Bird nest"}]
    assert gc.sheddable_slots(inv) == 2


def test_a_kit_only_full_inventory_takes_the_batch_instead_of_banking(monkeypatch):
    """The loop breaker: with nothing depositable, 24 IS the full batch for this loadout."""
    assert gc.mine_batch_done(24, 0, controller.INVENTORY_SIZE) is True
    assert 24 < gc.SUNSTONE_PER_TRIP, "the target really is unreachable here"
    # sheddable == 0 is what routes this to chisel rather than to a no-op bank trip
    assert gc.sheddable_slots(_kit_only_full_inventory()) == 0


# --- A search box that refuses keystrokes stops the run, it does not spin (2026-09-16) ----------
#
# MEASURED LIVE: the Arduino's KEYBOARD HID interface died while its mouse and serial stayed
# healthy (host-level probe: KD SHIFT sent, GetAsyncKeyState never saw it; serial answered "HB 3"
# throughout, so the startup firmware probe cannot catch it). Nine keystrokes moved a 948-item
# bank not at all. Once banker.search_bank began VERIFYING the query landed, its False return made
# this previously-dead branch reachable and the routine cycled bank -> mine -> craft -> bank with
# no progress (USER: "We are in a loop again, bank loop").

def _search_always_fails(monkeypatch):
    import banker
    _fake_clock(monkeypatch)
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: False)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    monkeypatch.setattr(banker, "deposit_inventory", lambda ctx, cfg: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": []})
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": {"name": "Rune pickaxe"},
                                 "gloves": {"name": "Expert mining gloves"},
                                 "shield": {"name": "Imcando hammer (off-hand)"}})
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    ctx.state.data["gc_fur_stock"] = 0
    return ctx


def test_a_refused_search_stops_instead_of_looping(monkeypatch):
    ctx = _search_always_fails(monkeypatch)
    for lap in range(gc.FUR_SEARCH_FAIL_LAPS - 1):
        gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)
        assert not ctx.state.safety_stopped, f"stopped too early on lap {lap + 1}"
        assert ctx.state.data["gc_phase"] == "mine", "a lap or two of carrying on is fine"
    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped, "a search that never takes must not cycle forever"


def test_the_stop_names_the_keyboard_not_the_bank(monkeypatch):
    """The whole cost of this class of bug is the message pointing at the wrong subsystem: a dead
    keyboard previously surfaced as 'bank has none of the usable fur types' and bought a 13-minute
    hunting trip."""
    ctx = _search_always_fails(monkeypatch)
    for _ in range(gc.FUR_SEARCH_FAIL_LAPS):
        gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)
    reason = (ctx.state.safety_reason or "").lower()
    assert "keyboard" in reason and "keystrokes" in reason
    assert "none of the usable fur" not in reason


def test_a_search_that_works_clears_the_failure_count(monkeypatch):
    """Otherwise three unlucky laps spread over hours eventually stop a healthy run."""
    import banker
    ctx = _search_always_fails(monkeypatch)
    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.data.get("gc_fur_search_fails") == 1
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(gc, "_remembered_bank_fur", lambda: None)
    monkeypatch.setattr(gc, "_withdraw_kit", lambda ctx, config: False)
    ctx.state.data["gc_fur_exhausted"] = True
    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)
    assert "gc_fur_search_fails" not in ctx.state.data


# --- "No fur on screen" is never "no fur in the bank" (2026-09-15) ------------------------------
#
# MEASURED LIVE (USER: "He forgot to pickup furs after getting to the bank"): "fur" was typed into
# the bank search at 17:34:24.0 and the bank read at 17:34:25.8 found nothing -- while the bank
# held 29 Dark kebbit fur. /bank is a BULK endpoint behind TickCache and the search filters LIVE,
# so a read within a tick of typing can still describe the pre-search bank. The routine kitted up
# and spent ~13 minutes hunting fur it already owned.

def test_the_fur_search_reads_the_bank_fresh(monkeypatch):
    """The stale read IS the bug -- a cached /bank cannot see a search typed 1.8s ago."""
    import banker
    _fake_clock(monkeypatch)
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    monkeypatch.setattr(banker, "deposit_inventory", lambda ctx, cfg: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": {"name": "Rune pickaxe"},
                                 "gloves": {"name": "Expert mining gloves"},
                                 "shield": {"name": "Imcando hammer (off-hand)"}})
    reads = []

    def get_bank(**kw):
        reads.append(kw.get("fresh"))
        return {"items": []}
    monkeypatch.setattr(controller, "get_bank", get_bank)
    monkeypatch.setattr(gc, "_remembered_bank_fur", lambda: None)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    ctx.state.data["gc_fur_stock"] = 0
    ctx.state.data["gc_fur_exhausted"] = True
    monkeypatch.setattr(gc, "_withdraw_kit", lambda ctx, config: False)

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)

    assert True in reads, "the post-search bank read must bypass TickCache"


def test_bank_memory_vetoes_a_fur_trip_when_the_bank_actually_holds_fur(monkeypatch):
    """Cost asymmetry: re-searching costs one tick, believing a stale read costs ~13 minutes."""
    import banker
    _fake_clock(monkeypatch)
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": []})   # interface shows none
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(gc, "_remembered_bank_fur", lambda: "Dark kebbit fur")
    kitted = []
    monkeypatch.setattr(gc, "_withdraw_kit", lambda ctx, config: kitted.append(True) or True)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    ctx.state.data["gc_fur_stock"] = 0
    ctx.state.data["gc_fur_exhausted"] = True

    for _ in range(gc.FUR_SEARCH_RETRIES):
        gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)
        assert kitted == [], "must re-search rather than spend a trip on fur we already own"
        # The phase is left UNSET on purpose: bank_phase re-enters next tick and searches again.
        assert ctx.state.data.get("gc_phase") != "fur_trip"

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)
    assert kitted, "but a genuinely unsurfaceable fur must not block the run forever"


def test_a_surfaced_fur_clears_the_search_retry_budget(monkeypatch):
    import banker
    _fake_clock(monkeypatch)
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx, m: True)
    monkeypatch.setattr(controller, "get_bank",
                        lambda **kw: {"items": [{"name": "Dark kebbit fur", "qty": 29,
                                                 "screenX": 5, "screenY": 5}]})
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Dark kebbit fur", "qty": 1}])
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    ctx.state.data["gc_fur_stock"] = 0
    ctx.state.data["gc_fur_exhausted"] = True
    ctx.state.data["gc_fur_search_retries"] = 2

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)

    assert "gc_fur_search_retries" not in ctx.state.data


def test_remembered_bank_fur_fails_open_when_memory_cannot_answer(monkeypatch):
    """It only ever VETOES a trip, so an unavailable memory must leave the old behaviour intact."""
    import bank_memory

    class _Unseen:
        seen = False

        def quantity(self, name):
            return None
    monkeypatch.setattr(bank_memory, "load", lambda names=None: _Unseen())
    assert gc._remembered_bank_fur() is None
    monkeypatch.setattr(bank_memory, "load",
                        lambda names=None: (_ for _ in ()).throw(RuntimeError("bridge down")))
    assert gc._remembered_bank_fur() is None


# --- The fur withdrawal makes room first (2026-09-15) -------------------------------------------
#
# MEASURED LIVE (USER: "It's looping"): bank -> mine -> chisel -> craft -> bank every ~40s forever,
# logging `withdraw-all ... didn't land in inventory` then `fur ran out before inserting` each lap.
# The inventory was 28/28: 20 Sunstone + 5 Sunstone core + chisel + gem sack + fur pouch. Withdraw-
# All only moves into FREE slots, so it moved nothing. BANK_KEEP_ITEMS builds the trap itself -- it
# protects a full batch through a bank trip, and a full batch plus the three containers IS 28.

def _full_bank_inventory(stones=20, cores=5):
    return ([{"name": gc.RAW_SUNSTONE_NAME, "screenX": 10, "screenY": 10} for _ in range(stones)]
            + [{"name": gc.SUNSTONE_CORE_NAME} for _ in range(cores)]
            + [{"name": "Chisel"}, {"name": "Open gem sack"}, {"name": "Large fur pouch (open)"}])


def test_a_full_inventory_sheds_raw_sunstone_before_withdrawing_fur(monkeypatch):
    _fake_clock(monkeypatch)
    import banker
    inventory = _full_bank_inventory()
    assert len(inventory) == controller.INVENTORY_SIZE, "the measured deadlock state"
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory)
    modes, clicks = [], []
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx, m: modes.append(m) or True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicks.append(e.get("name")) or True)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()

    assert gc._make_room_for_fur(ctx) is True
    assert clicks == [gc.RAW_SUNSTONE_NAME], "sheds raw sunstone, never the cores or a container"
    assert modes == [gc.FUR_WITHDRAW_SHED_MODE]


def test_room_is_not_made_when_there_already_is_some(monkeypatch):
    _fake_clock(monkeypatch)
    import banker
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: _full_bank_inventory(stones=10))
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicks.append(e) or True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx, m: True)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    assert gc._make_room_for_fur(ctx) is False
    assert clicks == []


def test_a_full_inventory_with_nothing_sheddable_says_so_instead_of_clicking(monkeypatch):
    """Without this it reports a bank problem for an inventory problem -- the exact misreading
    that made the live loop hard to diagnose."""
    _fake_clock(monkeypatch)
    import banker
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Sunstone core"}] * controller.INVENTORY_SIZE)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicks.append(e) or True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx, m: True)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    assert gc._make_room_for_fur(ctx) is False
    assert clicks == []


def test_the_shed_leaves_enough_room_for_a_trips_worth_of_fur():
    assert gc.FUR_WITHDRAW_MIN_FREE_SLOTS >= gc.CORES_PER_TRIP


# --- A failed kit withdrawal is retried, and never reported as a missing item (2026-09-15) ------
#
# MEASURED LIVE: the run stopped on "'ardougne cloak' is not in the bank" and idled 52 MINUTES
# while the bank held 7 of them. The client was disconnecting; widget positions went junk, the aim
# landed off-window ("Skipping click ... aim point is outside the RuneLite window"), and one failed
# click was read as a statement about the bank's contents. login.py recovered the session 30
# minutes later, but nothing clears a safety stop.

def _kit_bank(monkeypatch, *, withdraw_ok, memory=None):
    import banker
    _fake_clock(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(banker, "search_and_withdraw",
                        lambda ctx, name, qty, contains=False: withdraw_ok)
    monkeypatch.setattr(gc, "_withdraw_coins", lambda ctx, amount: True)
    import bank_memory
    monkeypatch.setattr(bank_memory, "load", lambda names=None: memory)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    return ctx


class _Memory:
    def __init__(self, seen, qty):
        self.seen, self._qty = seen, qty

    def quantity(self, name):
        return self._qty if self.seen else None


def test_a_failed_kit_withdrawal_retries_before_it_stops_the_run(monkeypatch):
    ctx = _kit_bank(monkeypatch, withdraw_ok=False, memory=_Memory(True, 7))
    for attempt in range(gc.KIT_WITHDRAW_TRIES - 1):
        assert gc._withdraw_kit(ctx, gc.DEFAULT_CONFIG) is False
        assert not ctx.state.safety_stopped, f"stopped on attempt {attempt + 1}"
    gc._withdraw_kit(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped, "must still give up eventually"


def test_the_stop_message_says_the_bank_HAS_it_when_bank_memory_says_so(monkeypatch):
    """The old message asserted the opposite of the truth, which is what made the real failure so
    hard to read: it named the stock while the fault was the click."""
    ctx = _kit_bank(monkeypatch, withdraw_ok=False, memory=_Memory(True, 7))
    for _ in range(gc.KIT_WITHDRAW_TRIES):
        gc._withdraw_kit(ctx, gc.DEFAULT_CONFIG)
    reason = (ctx.state.safety_reason or "").lower()
    assert "is in the bank" in reason and "7" in reason
    assert "not in the bank" not in reason


def test_the_stop_message_falls_back_to_not_in_the_bank_when_memory_cannot_answer(monkeypatch):
    """Never-seen memory is not evidence of an empty bank, but it is genuinely all we know."""
    ctx = _kit_bank(monkeypatch, withdraw_ok=False, memory=_Memory(False, None))
    for _ in range(gc.KIT_WITHDRAW_TRIES):
        gc._withdraw_kit(ctx, gc.DEFAULT_CONFIG)
    assert "not in the bank" in (ctx.state.safety_reason or "").lower()


def test_a_successful_withdrawal_clears_the_retry_count(monkeypatch):
    """Otherwise a run that fails twice an hour apart eventually stops on an unrelated blip."""
    ctx = _kit_bank(monkeypatch, withdraw_ok=False, memory=_Memory(True, 7))
    gc._withdraw_kit(ctx, gc.DEFAULT_CONFIG)
    assert any(k.startswith("gc_kit_tries_") for k in ctx.state.data)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Ardougne cloak 3"}, {"name": "Slayer ring (8)"},
                                 {"name": "Coins", "qty": 5000}])
    assert gc._withdraw_kit(ctx, gc.DEFAULT_CONFIG) is True
    assert not any(k.startswith("gc_kit_tries_") and ctx.state.data[k]
                   for k in list(ctx.state.data))


# --- craft_phase carves at human cadence (2026-08-23) ---------------------------------
#
# Root cause of the 2026-08-23 live failure, established from a hand-played recording rather than
# guesswork (recordings/2026-08-23_194302_golem-carving): ONE SIDE TAKES ~11.8s, and the first
# observable `progress` bump lands +2.1s after the click even when the player is already standing
# on the tile. GOLEM_SIDE_TIMEOUT_S was 4.0s. With a walk still resolving, the first bump landed
# just PAST the deadline, and a timeout here is not a harmless extra wait -- carve_plan re-picks a
# side from the player's new position and walks away, interrupting the carve that had genuinely
# started and resetting its progress to 0. Forever.

# Measured from the recording: click -> "You finish crafting the golem from this angle.", for all
# four sides (11.9 / 11.9 / 11.7 / 11.7s). Asserted against so a later "optimization" cannot
# quietly restore a timeout shorter than the action it is timing.
MEASURED_SIDE_CARVE_S = 11.7
MEASURED_FIRST_PROGRESS_BUMP_S = 2.1


def test_the_side_hard_cap_covers_a_full_carve_plus_the_walk_that_precedes_it():
    """The cap must cover the worst realistic case, not the bare carve: the walk is now fired
    without waiting, so arrival time lands INSIDE this window too. ~11.8s carve + a few tiles of
    walk + the click flight is already ~15s, and the cap only bites when progress has stopped
    climbing entirely, so headroom here costs nothing in the normal case."""
    assert gc.GOLEM_SIDE_MAX_WAIT_S > MEASURED_SIDE_CARVE_S + 8.0, (
        "a side takes ~11.8s from the click, plus the in-flight walk -- a cap near that total "
        "charges a miss against carves that were about to succeed"
    )


def test_the_side_timeout_allows_for_a_walk_plus_the_first_progress_bump():
    """The timeout is a 'time to first evidence' budget, not a carve duration -- but the evidence
    cannot arrive before the player has walked there and the game has ticked the first bump."""
    assert gc.GOLEM_SIDE_TIMEOUT_S > MEASURED_FIRST_PROGRESS_BUMP_S + 2.0, (
        "must cover walk-in-flight plus the measured +2.1s to the first progress bump"
    )


def test_carving_fires_the_walk_without_waiting_to_arrive(monkeypatch):
    """Human cadence: the walk is fire-and-forget and the plinth click goes out while the player
    is still moving. The game queues the interaction and carves the side you walked TO."""
    station = _station("south", 2595, 2253, "north", progress=1, done=("north",))
    _stub_golem_area(monkeypatch, station)

    walk_kwargs = []
    monkeypatch.setattr(
        gc, "walk_to_exact_tile",
        lambda ctx, x, y, plane, **kw: walk_kwargs.append(kw) or True,
    )
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)

    gc.craft_phase(_ctx(), gc.DEFAULT_CONFIG)

    assert walk_kwargs, "the carve step must still walk toward the next side"
    assert walk_kwargs[0].get("wait_for_arrival") is False, \
        "the carve walk must not block for arrival -- that is the latency this removes"


def test_the_plinth_click_re_aims_across_multiple_legs_while_the_player_moves(monkeypatch):
    """Clicking mid-walk is only safe if the aim is corrected in flight against un-cached reads.
    refresh_target alone re-aims once; refresh_legs is what makes it track a moving player."""
    station = _station("south", 2595, 2253, "north", progress=1, done=("north",))
    _stub_golem_area(monkeypatch, station)

    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)

    click_kwargs = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: click_kwargs.append(kw) or True)

    gc.craft_phase(_ctx(), gc.DEFAULT_CONFIG)

    assert click_kwargs, "the carve step must still click the plinth"
    kw = click_kwargs[0]
    assert kw.get("refresh_target") is not None, "must re-resolve the plinth's live screen position"
    assert kw.get("refresh_legs", 0) > 1, \
        "a single mid-flight re-aim is not enough while the player is walking"


# --- fast-negative: a hijacked plinth click (2026-08-23) ------------------------------
#
# CONFIRMED live 2026-08-23 with the session recorder running alongside the routine
# (recordings/2026-08-23_201416_routine-carve-drops): a bare left-click on the plinth resolves to
# whatever the game puts on top of that pixel, and another PLAYER standing on the aim point makes
# that "Walk here" on them, not Shape-golem:
#
#     20:14:34.056  Walk here    ->                        <- the fire-and-forget walk
#     20:14:35.034  Walk here    -> DoveHorn (level-115)   <- the PLINTH click, hijacked
#
# The routine then sat out the whole GOLEM_SIDE_TIMEOUT_S before retrying. It cannot be a slow
# carve: _await_side_done resets its deadline on every `progress` bump and bumps arrive every
# ~2.4s, so a real carve can never reach the timeout -- every timeout is a click that produced
# ZERO progress. /last-action reports what a click actually resolved to, from the game's own
# MenuOptionClicked, so the wrong action is provable in ~1s instead of inferred after 8s.
#
# Deliberately fails fast ONLY on positive evidence of a wrong action. An unchanged /last-action
# means "not processed yet", never "failed" -- guessing there would re-introduce the destructive
# early-abort this whole session existed to remove.


def test_a_click_that_resolved_to_the_expected_golem_option_is_accepted():
    before = {"tick": 100, "option": "Walk here", "target": "DoveHorn", "id": 0}
    after = {"tick": 104, "option": "Shape-golem", "target": "Unfinished golem", "id": 62352}
    assert gc._click_resolved_wrongly(before, after, 62352) is False


def test_a_click_hijacked_onto_another_player_is_detected_immediately():
    """The live signature: same click, but the game gave it to a player standing on the pixel."""
    before = {"tick": 100, "option": "Shape-golem", "target": "Unfinished golem", "id": 62352}
    after = {"tick": 104, "option": "Walk here", "target": "DoveHorn  (level-115)", "id": 0}
    assert gc._click_resolved_wrongly(before, after, 62352) is True


def test_an_unchanged_last_action_is_not_treated_as_a_failure():
    """The click may simply not be processed yet. Reporting a miss here would abort carves that
    were about to succeed -- the exact destructive retry this session removed."""
    before = {"tick": 100, "option": "Shape-golem", "target": "Unfinished golem", "id": 62352}
    assert gc._click_resolved_wrongly(before, dict(before), 62352) is None


def test_a_stale_repeat_of_the_previous_carve_is_not_mistaken_for_this_one():
    """/last-action holds only the most recent click, so a successful PREVIOUS carve looks
    identical to this one succeeding. Only a changed `tick` proves it is a new action -- the same
    transition-not-absolute-state discipline _await_side_done already applies (FINDING A)."""
    before = {"tick": 100, "option": "Shape-golem", "target": "Unfinished golem", "id": 62352}
    after = {"tick": 100, "option": "Shape-golem", "target": "Unfinished golem", "id": 62352}
    assert gc._click_resolved_wrongly(before, after, 62352) is None


def test_a_click_landing_on_the_other_plinth_counts_as_wrong():
    """Object id is checked, not just the option name -- the two plinths share every option."""
    before = {"tick": 100, "option": "Walk here", "target": "", "id": 0}
    after = {"tick": 104, "option": "Shape-golem", "target": "Unfinished golem", "id": 62351}
    assert gc._click_resolved_wrongly(before, after, 62352) is True


def test_every_golem_option_the_routine_issues_is_accepted():
    """Start-golem / Shape-golem / Insert-core are all legitimate outcomes of clicking a plinth;
    craft_phase does not know which the game will pick for a given click, so none may be rejected."""
    before = {"tick": 100, "option": "Walk here", "target": "", "id": 0}
    for option in ("Start-golem", "Shape-golem", "Insert-core"):
        after = {"tick": 104, "option": option, "target": "Unfinished golem", "id": 62352}
        assert gc._click_resolved_wrongly(before, after, 62352) is False, option


def test_a_hijacked_carve_click_retries_without_waiting_out_the_side_timeout(monkeypatch):
    """The saving: ~8s per hijack, and hijacks were ~29% of carve attempts in the live run."""
    station = _station("south", 2595, 2253, "north", progress=1, done=("north",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)

    # The live signature: the click became "Walk here" on another player.
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    actions = iter([{"tick": 100, "option": "Shape-golem", "target": "Unfinished golem", "id": 62352},
                    {"tick": 104, "option": "Walk here", "target": "DoveHorn  (level-115)", "id": 0}])
    monkeypatch.setattr(controller, "get_last_action", lambda: next(actions, {"tick": 104, "option": "Walk here", "id": 0}))

    awaited = []
    monkeypatch.setattr(gc, "_await_side_done",
                        lambda *a, **k: awaited.append(a) or False)

    gc.craft_phase(_ctx(), gc.DEFAULT_CONFIG)

    assert awaited == [], \
        "a provably hijacked click must not wait out the side timeout at all"


def test_a_single_hijack_costs_nothing_against_the_side_miss_safety_stop(monkeypatch):
    """The fast retry does not consult _await_side_done at all, so the one hijack it absorbs
    cannot charge a miss. Consecutive hijacks are a different matter -- they fall through to the
    slow path deliberately (see the regression test below) and are bounded there, which is the
    pre-existing behaviour rather than a second, parallel way to safety-stop."""
    station = _station("south", 2595, 2253, "north", progress=1, done=("north",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)
    ticks = iter(range(200, 400))
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": next(ticks), "option": "Walk here", "target": "X", "id": 0})
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: False)

    ctx = _ctx()
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert not ctx.state.safety_stopped
    assert ctx.state.data.get("gc_side_misses", 0) == 0,         "the absorbed hijack must not be charged as a side miss"


def test_endless_hijacks_still_terminate_via_the_ordinary_miss_limit(monkeypatch):
    """A plinth that can never be clicked must still stop the routine. After the one-shot fast
    retry it degrades to the normal confirmation path, so GOLEM_SIDE_MISS_LIMIT terminates it --
    no second bespoke limit needed."""
    station = _station("south", 2595, 2253, "north", progress=1, done=("north",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)
    ticks = iter(range(200, 600))
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": next(ticks), "option": "Walk here", "target": "X", "id": 0})
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: False)

    ctx = _ctx()
    # Each charged miss can cost up to two ticks: one spent on the fast retry (which deliberately
    # charges nothing) and one on the real attempt. Derived rather than hardcoded so adding
    # another cost-free tier later cannot silently make this loop too short to prove termination.
    for _ in range(2 * gc.GOLEM_SIDE_MISS_LIMIT + 2):
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.safety_stopped, "an unclickable plinth must still terminate the routine"


def test_a_second_consecutive_hijack_on_a_side_falls_back_to_the_slow_path(monkeypatch):
    """REGRESSION 2026-08-23, caught live: retrying a hijack immediately re-clicks essentially the
    same pixel, because nothing about the geometry has changed in 1.6s. The routine locked into a
    20-attempt loop on the north plinth's east side, clicking (1093,766) every time while the
    plinth's clickbox centre sat at (1100,759) -- and the SAME tile and object had carved fine
    three minutes earlier. The old 8s wait was accidentally load-bearing: it let the camera and
    the world drift, so each retry aimed somewhere slightly different and eventually connected.

    So the fast path is allowed exactly ONE retry per side. A second consecutive hijack falls back
    to the ordinary confirmation wait, which is both the proven-good behaviour and the thing that
    supplies the drift. This caps the cost of a hijack at one extra click and makes a spin
    structurally impossible."""
    station = _station("north", 2596, 2257, "south", progress=1, done=("south",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)
    ticks = iter(range(200, 400))
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": next(ticks), "option": "Walk here", "target": "", "id": 0})

    waits = []
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: waits.append(a) or False)

    ctx = _ctx()
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert waits == [], "the FIRST hijack may still take the fast path"

    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: True)
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(waits) == 1, \
        "a second consecutive hijack must stop fast-retrying and go through the ordinary wait -- " \
        "now reached via the menu route rather than another bare click"


def test_the_fast_path_is_rearmed_once_a_side_registers(monkeypatch):
    """The one-shot budget is per run of consecutive failures, not per session -- otherwise the
    fast path would be spent after the first hijack of the hour and never help again."""
    station = _station("north", 2596, 2257, "south", progress=1, done=("south",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)
    ticks = iter(range(200, 400))
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": next(ticks), "option": "Walk here", "target": "", "id": 0})
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: True)   # the side registers

    ctx = _ctx()
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)          # hijack -> fast path, budget spent
    ctx.state.data["gc_side_hijacks"] = 0           # what a successful carve does
    waits = []
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: waits.append(a) or False)
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert waits == [], "after a success the fast path must be available again"


def test_a_second_consecutive_hijack_escalates_to_a_menu_selection(monkeypatch):
    """LIVE HALT 2026-08-23 21:04: the retry mitigation was not enough. Another player stood on
    the south plinth's east tile (players do not block each other, so they stack on OUR tile),
    every bare left-click resolved to them, and after five consecutive failures the routine
    safety-stopped and stayed down. Waiting for the world to drift only works if the world
    actually drifts.

    So the second consecutive hijack escalates to click_menu_action, which right-clicks and picks
    the option BY NAME out of /menu -- immune to whatever is drawn on top. That is precisely the
    case its own docstring was written for, and the same reason chinchompa_hunting.py uses it to
    take ground items off tiles chinchompas wander across."""
    station = _station("south", 2595, 2253, "north", progress=1, done=("north",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ticks = iter(range(200, 400))
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": next(ticks), "option": "Walk here", "target": "X", "id": 0})
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: False)

    menu_calls = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu_calls.append(option) or True)

    ctx = _ctx()
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert menu_calls == [], "the FIRST hijack still uses the cheap bare click"

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(menu_calls) == 1, "the second consecutive hijack must escalate to the menu"
    assert set(GOLEM_OPTS := set(gc.GOLEM_PLINTH_OPTIONS)) == set(menu_calls[0]), \
        "every plinth option must be offered -- craft_phase does not choose which the game picks"


def test_a_failed_menu_selection_degrades_instead_of_asserting(monkeypatch):
    """click_menu_action returns False when the menu never opened or nothing matched. That must
    fall back to the ordinary retry accounting, not blow up or count as success."""
    station = _station("south", 2595, 2253, "north", progress=1, done=("north",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ticks = iter(range(200, 400))
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": next(ticks), "option": "Walk here", "target": "X", "id": 0})
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: False)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: False)

    ctx = _ctx()
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)   # escalates, menu fails
    assert not ctx.state.safety_stopped, "a failed menu selection is a retry, not an anomaly"


def test_the_menu_escalation_waits_for_stillness_before_resolving_the_plinth(monkeypatch):
    """LIVE 2026-08-23 21:24: a menu escalation right-clicked bare tile despite re-resolving the
    plinth first, because the player was STILL WALKING -- the position was resolved at 21:24:47.571
    and the cursor landed at 21:24:47.771, 200ms and ~a third of a tile later, 6px off the object.
    The bare-click path survives that via refresh_legs re-aiming at checkpoints mid-flight;
    click_menu_action has no such mechanism, so the only way to make its single aim trustworthy is
    to stop moving first. This is the deliberate slow path already, so the wait costs nothing on
    the fast one.

    Order matters and is asserted: idle FIRST, then resolve, so the position is read after the
    movement that would invalidate it has finished."""
    station = _station("south", 2595, 2253, "north", progress=1, done=("north",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ticks = iter(range(200, 400))
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": next(ticks), "option": "Walk here", "target": "X", "id": 0})
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: False)

    order = []
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda *a, **k: order.append("idle") or True)
    monkeypatch.setattr(controller, "get_object_at",
                        lambda *a, **k: order.append("resolve") or {
                            "id": 62352, "x": 2595, "y": 2253, "plane": 0,
                            "screenX": 1100, "screenY": 759, "name": "Empty plinth"})
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: order.append("rightclick") or True)

    ctx = _ctx()
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)   # first hijack -> fast retry, no menu
    assert order == [], "the fast path must not pay for any of this"

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)   # second -> escalate
    assert order == ["idle", "resolve", "rightclick"], \
        f"menu escalation must settle, then resolve, then right-click -- got {order}"


def test_a_menu_miss_falls_back_to_the_bare_click_instead_of_counting_a_hard_miss(monkeypatch):
    """LIVE HALT 2026-08-23 22:04: five consecutive menu misses safety-stopped a routine whose
    bare-click path was working fine -- it had just produced 22 golems in 36 minutes.

    The plinth is ringed by four unnamed clickable marker objects, one per side tile (62359-62362
    around the north plinth), and the one on OUR tile competes for the click; a frame dump with
    both centres marked showed the empty plinth's own reported clickbox centre landing on bare
    ground at that camera angle, contributing only the marker's 'Examine' to the menu.

    The menu route is a best-effort ESCALATION. When it cannot find the option it must degrade to
    the ordinary click that usually works, not convert a recoverable situation into a hard miss
    that ends the session."""
    station = _station("south", 2595, 2253, "north", progress=1, done=("north",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone"}] * 8)
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    ticks = iter(range(200, 400))
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": next(ticks), "option": "Walk here", "target": "X", "id": 0})
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: False)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: False)

    bare = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: bare.append(e) or True)

    ctx = _ctx()
    for _ in range(gc.GOLEM_APPROACH_MISS_LIMIT + 2):
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert bare, "a failed menu selection must still put an ordinary click out"
    assert not ctx.state.safety_stopped, \
        "menu misses alone must not safety-stop a routine whose bare click works"


def test_dropping_junk_retries_whatever_survived_the_first_pass(monkeypatch):
    """USER REPORT 2026-08-24: wooden spikes still in the inventory after a fur session.

    The drop was a single fire-and-forget pass with no verification, and two things routinely
    defeat it: click_entity's deliberate MISTAKE_MISCLICK_CHANCE near-miss (~4.5% per click, so a
    4-spike drop misses one about one time in six -- observed at 22:38, 4 clicked and 1 survived),
    and any interruption mid-pass (a disconnect landed in exactly that window at 01:28:06). What
    survives is never retried, so it just accumulates until the next hunt happens to clear it.

    Re-reading the inventory and retrying is the fix. Bounded, because an item that genuinely
    cannot be dropped must not spin."""
    dropped = []
    inv = [{"name": "Wooden spikes", "screenX": 1, "screenY": 1},
           {"name": "Wooden spikes", "screenX": 2, "screenY": 2},
           {"name": "Hammer", "screenX": 3, "screenY": 3}]

    def fake_click(ser, item, **kw):
        # First click misses (the deliberate near-miss); everything after it lands.
        if len(dropped) == 0:
            dropped.append(None)
            return True
        dropped.append(item)
        if item in inv:
            inv.remove(item)
        return True

    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv))
    monkeypatch.setattr(controller, "click_entity", fake_click)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)

    gc._drop_all(_ctx(), "Wooden spikes")

    assert not [i for i in inv if i["name"] == "Wooden spikes"], \
        "a spike that survived the first pass must be retried"
    assert any(i["name"] == "Hammer" for i in inv), "must never drop anything else"


def test_dropping_junk_gives_up_rather_than_spinning_on_an_undroppable_item(monkeypatch):
    """If an item cannot be dropped at all, the retry must terminate -- this runs unattended."""
    inv = [{"name": "Wooden spikes", "screenX": 1, "screenY": 1}]
    calls = []
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, item, **kw: calls.append(item) or True)   # never removes it
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)

    gc._drop_all(_ctx(), "Wooden spikes")

    assert 0 < len(calls) <= gc.DROP_MAX_PASSES, \
        f"must be bounded by DROP_MAX_PASSES, got {len(calls)} clicks"


# --- avoiding a start side that cannot be clicked from (2026-08-24) -------------------
#
# THREE live halts, all the same shape: "starting golem from east side of south plinth" at
# progress 0, five failures, safety stop (21:04, 22:04, 02:00). With the game frozen at the third:
# our player on the east tile, /object/at reporting the plinth clickable:true with a clickbox at
# screen (1158,771) -- and a right-click there returning only ground options
# (['Cancel','Mark Safespot','Examine','Walk here']). The reported clickbox CENTRE simply is not
# on the clickable shape from that angle, so every tier of the escalation chain re-aims at the
# same unusable pixel and fails identically.
#
# The escalation chain cannot fix a bad aim point. Standing somewhere else can -- and on a FRESH
# golem the start side is a free choice between east and west (see can_end_on_insert), which is
# precisely where all three halts happened.


def test_a_start_side_that_keeps_failing_is_avoided_in_favour_of_the_other(monkeypatch):
    station = _station("south", 2595, 2253, "north", progress=0, done=())
    # East is nearer, so it wins normally...
    assert gc.choose_start_side(station, 2596, 2253) == "east"
    # ...but not once it is known bad.
    assert gc.choose_start_side(station, 2596, 2253, avoid={"east"}) == "west"


def test_avoiding_every_candidate_still_returns_a_usable_side():
    """Never return None: if both start sides have failed, carving from a bad one still beats not
    carving at all, and the ordinary miss limits remain the backstop."""
    station = _station("south", 2595, 2253, "north", progress=0, done=())
    assert gc.choose_start_side(station, 2596, 2253, avoid={"east", "west"}) in ("east", "west")


def test_carve_plan_threads_the_avoid_set_through():
    station = _station("south", 2595, 2253, "north", progress=0, done=())
    assert gc.carve_plan(station, 2596, 2253)[0] == "east"
    assert gc.carve_plan(station, 2596, 2253, avoid={"east"})[0] == "west"


def test_a_side_is_only_avoided_after_repeated_failure_not_one_miss():
    """One failure is ordinary -- a deliberate misclick, a player wandering through. Switching
    start side on a single miss would thrash between the two for no reason."""
    assert not gc._sides_to_avoid({"south:east": 1}, "south")
    assert gc._sides_to_avoid({"south:east": gc.SIDE_AVOID_THRESHOLD}, "south") == {"east"}


def test_avoidance_is_scoped_to_the_plinth_that_failed():
    """The two plinths have independent geometry; a bad angle on one says nothing about the
    other."""
    assert gc._sides_to_avoid({"south:east": 9}, "north") == set()


def test_repeated_side_failures_make_craft_phase_switch_start_side(monkeypatch):
    """End to end: the side that keeps failing stops being chosen, so the routine physically
    stands somewhere else instead of re-clicking an unusable pixel until it safety-stops."""
    station = _station("south", 2595, 2253, "north", progress=0, done=())
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2596, "y": 2253})  # on the east tile
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Sunstone"}] * 8 + [{"name": "Sunstone core"}] * 2)
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: False)   # nothing ever registers

    sides = []
    real_log = gc.debug_log.log
    def spy_log(cat, msg):
        m = re.search(r"golem from (\w+) side", msg)
        if m: sides.append(m.group(1))
        real_log(cat, msg)
    monkeypatch.setattr(gc.debug_log, "log", spy_log)

    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = 20
    for _ in range(2 * gc.SIDE_AVOID_THRESHOLD + 4):
        if ctx.state.safety_stopped:
            break
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert "east" in sides, "it should still try the near side first"
    assert "west" in sides, \
        f"after {gc.SIDE_AVOID_THRESHOLD} failures on east it must try the other start side -- got {sides}"


def test_a_hijacked_insert_core_click_escalates_the_same_way_a_carve_does(monkeypatch):
    """LIVE HALT 2026-08-24 02:17: with the carve step fixed, the SAME hijack simply moved to
    Insert-core, which never got the treatment. The signature is unmistakable -- the click
    positions alternated between (1118,653) and (1217,764) across five attempts, i.e. the click
    resolved to "Walk here", the player moved, the next tick walked back, and it oscillated until
    the miss limit ran out. All four sides were carved and progress was 5; only the final step
    failed, so a whole golem's materials sat there unfinished.

    Insert-core goes through the same _click_station_tracked on the same object as a carve, so it
    gets the same escalation: prove the wrong resolution from /last-action, retry once, then pick
    the option out of the right-click menu."""
    station = _station("south", 2595, 2253, "north", progress=5, done=gc.SIDES)  # fully carved
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2595, "y": 2254})
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone core"}])
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_object_at", lambda *a, **k: None)
    monkeypatch.setattr(gc, "_await_golem_complete", lambda *a, **k: False)
    ticks = iter(range(300, 500))
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": next(ticks), "option": "Walk here", "target": "X", "id": 0})

    menu_calls = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu_calls.append(option) or True)

    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = 5
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert menu_calls == [], "the first hijacked insert still gets the cheap retry"

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(menu_calls) == 1, \
        "a second consecutive hijacked Insert-core must escalate to the right-click menu"


# --- a plinth that won't start is backed off from, not fatal (2026-08-24) --------------
#
# SEVEN hypotheses were killed by measurement for the "starting golem ... never registered" halts
# (bad aim point, missing materials, another player on the plinth, wrong start side, full
# inventory, a leftover Golem NPC, stale station choice). The cause is still unknown.
#
# What IS established: every one of those halts recovered on a plain restart, which produced
# several more golems before recurring. So the condition is TRANSIENT. The routine simply never
# gets to find that out, because it safety-stops rather than walking away and coming back --
# turning a temporary refusal into a dead unattended session. Backing off is justified by that
# alone, and it also generates the evidence that would settle whether waiting is sufficient.


def test_a_plinth_that_will_not_start_no_longer_ends_the_session(monkeypatch):
    """SUPERSEDED 2026-08-24 by the real cause (no fur -- see
    test_a_start_refusal_with_materials_in_hand_is_treated_as_out_of_fur). This originally asserted
    a generic back-off, written while the cause was still unknown. The back-off was the right
    instinct for an unknown transient but the wrong response to this specific one: the routine now
    goes and restocks instead, which actually resolves it. Kept, narrowed to the property that
    still matters and that regressed twice tonight -- a refusal must never end an unattended
    session."""
    station = _station("south", 2595, 2253, "north", progress=0, done=())
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Sunstone"}] * 8 + [{"name": "Sunstone core"}] * 2)
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: False)

    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = 20
    for _ in range(3 * gc.GOLEM_SIDE_MISS_LIMIT):
        if ctx.state.data.get("gc_phase") in ("bank", "mine"):
            break
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert not ctx.state.safety_stopped, "a start refusal must never end an unattended session"
    assert ctx.state.data.get("gc_phase") == "bank"


def test_choose_station_avoids_a_blocked_plinth():
    north = _station("north", 2596, 2257, "south")
    south = _station("south", 2595, 2253, "north")
    # Nearer to south, but south is blocked -> take north anyway.
    assert gc.choose_station([north, south], None, 2595, 2253, blocked={"south"})["name"] == "north"


def test_choose_station_ignores_blocking_when_every_plinth_is_blocked():
    north = _station("north", 2596, 2257, "south")
    south = _station("south", 2595, 2253, "north")
    assert gc.choose_station([north, south], None, 2595, 2253,
                             blocked={"north", "south"}) is not None


def test_endless_backoff_on_both_plinths_still_terminates(monkeypatch):
    """Backing off must not become its own infinite loop -- if nothing can ever be started, the
    run still has to stop rather than cycle mine<->craft forever."""
    ctx = _ctx()
    for i in range(gc.STATION_BLOCK_LIMIT + 2):
        gc._block_station(ctx, _station("south", 2595, 2253, "north"))
    assert ctx.state.safety_stopped, \
        "repeated back-offs with no progress must eventually terminate"


def test_an_unreachable_approach_tile_backs_off_on_a_fresh_plinth(monkeypatch):
    """LIVE HALT 2026-08-24 03:37: "couldn't reach a tile near north plinth after 5 attempts;
    target (2597,2257)". walk_to_exact_tile returns False only when the tile cannot be projected
    onto the viewport at all -- and checking the instant the routine stopped, /ground/walk reported
    that same tile onScreen at (1150,621). So it was transient, exactly like the start refusals,
    and exactly like them it ended the session outright.

    Same rule as those: on a FRESH plinth nothing is committed, so back off and come back."""
    station = _station("north", 2596, 2257, "south", progress=0, done=())
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Sunstone"}] * 8 + [{"name": "Sunstone core"}] * 2)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: False)
    monkeypatch.setattr(gc, "_hop_toward_point", lambda *a, **k: True)

    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = 20
    for _ in range(3 * gc.GOLEM_APPROACH_MISS_LIMIT):
        if ctx.state.data.get("gc_phase") == "mine":
            break
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert not ctx.state.safety_stopped, \
        "an unreachable approach tile on a fresh plinth is transient -- back off, do not halt"
    assert ctx.state.data.get("gc_blocked_stations", {}).get("north")


def test_an_unreachable_approach_tile_still_halts_on_a_part_carved_golem(monkeypatch):
    """A part-carved golem has 4 sunstone committed -- walking away destroys them, so that case
    must still stop loudly rather than quietly abandon materials."""
    station = _station("north", 2596, 2257, "south", progress=2, done=("east",))
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Sunstone core"}])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: False)
    monkeypatch.setattr(gc, "_hop_toward_point", lambda *a, **k: True)

    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = 20
    for _ in range(3 * gc.GOLEM_APPROACH_MISS_LIMIT):
        if ctx.state.safety_stopped:
            break
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.safety_stopped, "committed materials must not be silently abandoned"


def test_blocking_a_plinth_sends_the_routine_to_do_something_useful():
    """_block_station now serves ONE caller: an approach tile that cannot be projected onto the
    viewport (_handle_approach_exhausted). Mining is the right fallback there -- if the plinth
    cannot be walked to, moving elsewhere is exactly what is needed, and the walk back re-resolves
    the geometry.

    An earlier version of this test asserted the opposite (that blocking should stay in the craft
    phase and switch plinths). That was written for the start-refusal case, which no longer blocks
    at all now the cause is known to be missing fur."""
    ctx = _ctx()
    gc._block_station(ctx, _station("north", 2596, 2257, "south"))

    assert ctx.state.data.get("gc_phase") == "mine"
    assert "north" in gc._blocked_station_names(ctx)


def test_mining_is_the_fallback_only_when_every_plinth_is_blocked(monkeypatch):
    """With nowhere to craft, mining is productive rather than spinning on a blocked plinth."""
    north = _station("north", 2596, 2257, "south", progress=0, done=())
    south = _station("south", 2595, 2253, "north", progress=0, done=())
    monkeypatch.setattr(controller, "get_golem",
                        lambda: {"inArea": True, "stations": [north, south]})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2596, "y": 2257})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Sunstone"}] * 8 + [{"name": "Sunstone core"}] * 2)

    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = 20
    gc._block_station(ctx, north)
    gc._block_station(ctx, south)
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.data.get("gc_phase") == "mine", \
        "with both plinths blocked, go do something useful instead of retrying a blocked one"


def test_a_start_refusal_with_materials_in_hand_is_treated_as_out_of_fur(monkeypatch):
    """ROOT CAUSE, identified by the user 2026-08-24 after seven of my hypotheses died:
    **Start-golem is not offered at all when you have no fur.**

    Everything fits. Fur is inventory-wide, so BOTH plinths refuse at once. The game gives no
    rejection message, so the option is simply absent from the menu -- which is exactly what the
    right-click showed every time (['Cancel','Mark Safespot','Examine','Walk here']). It happens
    only at progress 0, because a part-carved golem can still be shaped without fur. And a restart
    appears to "fix" it only because restarting forces a bank trip that refills fur.

    has_start_materials checks sunstone and cores and NEVER fur, and gc_fur_stock is a local
    estimate this file's own comments describe as able to "read >0 while the pouch is genuinely
    empty" -- the only authoritative fur signal, GOLEM_CHAT_NOT_ENOUGH_FUR, fires on Insert-core,
    never here. So the correct response to a start refusal is to go restock, not to back off."""
    station = _station("north", 2596, 2257, "south", progress=0, done=())
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Sunstone"}] * 8 + [{"name": "Sunstone core"}] * 2)
    monkeypatch.setattr(gc, "_await_side_done", lambda *a, **k: False)

    ctx = _ctx()
    ctx.state.data["gc_fur_stock"] = 20      # the stale estimate that caused the whole mess
    for _ in range(3 * gc.GOLEM_SIDE_MISS_LIMIT):
        if ctx.state.data.get("gc_phase") == "bank":
            break
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.data.get("gc_phase") == "bank", \
        "a start refusal with sunstone+cores in hand means no fur -- go restock"
    assert ctx.state.data.get("gc_fur_stock") == 0, \
        "the stale local fur estimate must be corrected, or bank_phase will think it is stocked"


def test_menu_escalation_plinth_click_passes_refresh_target(monkeypatch):
    """The use_menu=True escalation right-clicks the plinth via click_menu_action -- now that
    click_menu_action can re-aim mid-flight (2026-08-27), the escalation should hand it a
    refresh_target factory instead of a bare screen position, same as every other click site."""
    station = _station("north", 2596, 2257)
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(
        controller, "get_object_at",
        lambda x, y, plane, obj_id, radius=None: {
            "id": obj_id, "x": x, "y": y, "plane": plane, "name": "Shape-golem",
        },
    )
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    objects = [{"id": station["objectId"], "x": station["x"], "y": station["y"], "plane": 0}]
    assert gc._click_station(_ctx(), station, objects, use_menu=True) is True
    assert seen.get("refresh_target") is not None, \
        "menu escalation must re-resolve the plinth's live screen position"


# --- Event-driven outcomes (2026-09-06, docs/superpowers/specs/2026-09-06-golem-crafting-events-
# and-falconry-design.md). A REAL bridge_events.EventStream fed by a scripted controller.poll_udp,
# so await_outcome's mark/since semantics are what is exercised. The fake clock patches the
# `time` module's time/sleep, which gc and outcome_waits both read at call time.

import bridge_events


class _EvCtx:
    def __init__(self, events):
        self.events = events
        self.ser = object()
        self.state = None


def _event_stream(monkeypatch, batches):
    """batches: list of lists of (type, payload, then) -- one batch per poll_udp call."""
    queue = list(batches)

    def poll_udp(_sock):
        if not queue:
            return []
        out = []
        for ev in queue.pop(0):
            if len(ev) > 2 and ev[2] is not None:
                ev[2]()
            out.append((ev[0], ev[1]))
        return out
    monkeypatch.setattr(controller, "poll_udp", poll_udp)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    return _EvCtx(bridge_events.EventStream(sock=None))


def _fake_clock(monkeypatch):
    state = {"t": 5000.0}
    monkeypatch.setattr(gc.time, "time", lambda: state["t"])
    monkeypatch.setattr(gc.time, "sleep", lambda s: state.__setitem__("t", state["t"] + s))
    return state


def test_varbit_subscriptions_cover_both_plinths_and_the_falcon():
    subs = set(gc.VARBIT_SUBSCRIPTIONS)
    for vid in range(15728, 15747):
        assert vid in subs
    assert 14192 in subs
    assert gc.VB_SIDE_STATE["south"]["west"] == 15737
    assert gc.VB_SIDE_PROGRESS["north"]["east"] == 15740


def test_await_side_done_reads_golem_the_instant_the_side_varbit_lands(monkeypatch):
    clock = _fake_clock(monkeypatch)
    done = {"v": False}
    reads = []

    def get_golem():
        reads.append(clock["t"])
        return {"stations": [_station("north", done=("east",) if done["v"] else ())]}
    monkeypatch.setattr(controller, "get_golem", get_golem)
    ctx = _event_stream(monkeypatch, [
        [("VARBIT_CHANGED", {"varbitId": 15730, "value": 1, "tick": 3},
          lambda: done.__setitem__("v", True))],
    ])
    start = clock["t"]

    assert gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=False,
                               ctx=ctx) is True
    # The event arrived on the first pump, so the confirming read happened without spending a
    # whole GOLEM_CONFIRM_POLL_S first.
    assert reads and reads[0] - start < gc.GOLEM_CONFIRM_POLL_S


def test_await_side_done_without_events_still_polls_exactly_as_before(monkeypatch):
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(controller, "get_golem",
                        lambda: {"stations": [_station("north", done=("east",))]})
    start = clock["t"]
    assert gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=False) is True
    assert abs((clock["t"] - start) - gc.GOLEM_CONFIRM_POLL_S) < 1e-9


def test_await_click_echo_takes_the_menu_clicked_verdict(monkeypatch):
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.5)
    before = {"tick": 1, "option": "Walk here", "target": "", "id": 0}
    after = {"tick": 1, "option": "Walk here", "target": "", "id": 0}
    monkeypatch.setattr(controller, "get_last_action", lambda: dict(after))
    ctx = _event_stream(monkeypatch, [
        [("MENU_CLICKED", {"option": "Shape-golem", "target": "Unfinished golem", "id": 62351, "tick": 7},
          lambda: after.update({"tick": 7, "option": "Shape-golem", "id": 62351}))],
    ])
    start = clock["t"]

    assert gc._await_click_echo(before, 62351, ctx=ctx) is False       # provably RIGHT
    assert clock["t"] - start < gc.GOLEM_CLICK_ECHO_POLL_S


def test_await_click_echo_still_returns_none_at_the_budget_with_events(monkeypatch):
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(gc, "GOLEM_CLICK_ECHO_TIMEOUT_S", 1.0)
    monkeypatch.setattr(controller, "get_last_action",
                        lambda: {"tick": 1, "option": "Walk here", "target": "", "id": 0})
    ctx = _event_stream(monkeypatch, [])
    assert gc._await_click_echo({"tick": 1, "option": "Walk here", "target": "", "id": 0}, 62351,
                                ctx=ctx) is None
    assert clock["t"] >= 5001.0


def test_await_golem_complete_reads_on_the_count_varbit(monkeypatch):
    clock = _fake_clock(monkeypatch)
    total = {"v": 5}
    monkeypatch.setattr(controller, "get_golem",
                        lambda: {"totalCrafted": total["v"], "stations": [_station("north", progress=4)]})
    ctx = _event_stream(monkeypatch, [
        [("VARBIT_CHANGED", {"varbitId": 15738, "value": 6, "tick": 9},
          lambda: total.__setitem__("v", 6))],
    ])
    start = clock["t"]
    assert gc._await_golem_complete("north", 6.0, before_total_crafted=5, before_progress=4,
                                    ctx=ctx) is True
    assert clock["t"] - start < gc.GOLEM_CONFIRM_POLL_S


# --- Task 3: chisel chain, bank confirm, player-stopped await ------------------------------

# The 2026-09-14 burst (USER: "we can do that while moving and significantly faster. basicly 1
# chisel per game tick"). The mechanic is MEASURED record-by-record in the recording -- see
# golem_crafting's CHISEL_XP_PER_CORE comment block. What the tests below pin:
#   * the isIdle entry gate is GONE, because `!moving` is part of it and that is precisely what
#     kept the burst from running during the walk to the plinth;
#   * slots are resolved ONCE and the pairs fire with no HTTP between them (the stone converts in
#     its own slot, so nothing goes stale mid-burst);
#   * the stones nearest the chisel are spent first -- two flights per core across that gap is
#     the difference between one core per tick and one per two;
#   * chiselling WHILE WALKING is the user's claim and is in no recording here, so the first
#     moving burst probes with one pair and remembers the answer.

CHISEL_PT = (1123, 637)          # slot 1, the chisel -- real geometry from a live /inventory


def _slot_pt(slot):
    """The real inventory grid measured off the live bridge: 42px columns, 36px rows, slot 0 at
    (1081, 637)."""
    return (1081 + 42 * (slot % 4), 637 + 36 * (slot // 4))


def _chisel_world(monkeypatch, raw=20, pause_after=None, moving=False, convert=True):
    """A chisel in slot 1 and `raw` sunstones from slot 4 on. A timed_click on a stone's point
    converts THAT stone in place, which is what the recording shows (usedSlots never moved)."""
    inventory = [{"name": "Chisel", "slot": 1, "screenX": CHISEL_PT[0], "screenY": CHISEL_PT[1]}]
    for n in range(raw):
        slot = 4 + n
        pt = _slot_pt(slot)
        inventory.append({"name": "Sunstone", "slot": slot, "screenX": pt[0], "screenY": pt[1]})
    clicks = []
    ctx = _ctx()

    def click_entity(ser, entity, **kw):
        point = (int(entity["screenX"]), int(entity["screenY"]))
        clicks.append(point)
        if point == CHISEL_PT or not convert:
            return True
        for item in inventory:
            if (item.get("screenX"), item.get("screenY")) == point and item["name"] == "Sunstone":
                item["name"] = "Sunstone core"
                break
        cores = sum(1 for it in inventory if it["name"] == "Sunstone core")
        if pause_after is not None and cores >= pause_after:
            ctx.state.paused = True
        return True

    monkeypatch.setattr(controller, "get_inventory", lambda: [dict(i) for i in inventory])
    monkeypatch.setattr(controller, "get_player", lambda: {"moving": moving})
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    monkeypatch.setattr(controller, "click_entity", click_entity)
    return ctx, inventory, clicks


def _stones_clicked(clicks):
    return [c for c in clicks if c != CHISEL_PT]


def test_chisel_phase_chisels_the_whole_batch_in_one_call(monkeypatch):
    _fake_clock(monkeypatch)
    ctx, inventory, clicks = _chisel_world(monkeypatch)
    gc.chisel_phase(ctx, gc.DEFAULT_CONFIG)
    assert sum(1 for it in inventory if it["name"] == "Sunstone core") == gc.CORES_PER_TRIP
    assert len(_stones_clicked(clicks)) == gc.CORES_PER_TRIP
    assert ctx.state.data["gc_phase"] == "craft"


def test_every_core_is_a_chisel_click_then_a_stone_click(monkeypatch):
    """Selecting the chisel is client-side and sends no packet, so it is re-selected for every
    core -- there is no repeat action to lean on (MEASURED: the human re-clicked it all five
    times)."""
    _fake_clock(monkeypatch)
    ctx, inventory, clicks = _chisel_world(monkeypatch)
    gc.chisel_phase(ctx, gc.DEFAULT_CONFIG)
    assert clicks[0::2] == [CHISEL_PT] * gc.CORES_PER_TRIP, "every core starts by selecting the chisel"
    assert CHISEL_PT not in clicks[1::2], "every second click is the stone the chisel is used on"


def test_the_burst_spends_the_stones_nearest_the_chisel_first(monkeypatch):
    """Each core costs two flights across the chisel->stone gap; the fitted reach model puts 42px
    at 86ms against 205ms at 120px, so the order is what decides one core per tick vs one per
    two."""
    inventory = [{"name": "Chisel", "slot": 1, "screenX": CHISEL_PT[0], "screenY": CHISEL_PT[1]},
                 {"name": "Sunstone", "slot": 24, "screenX": _slot_pt(24)[0], "screenY": _slot_pt(24)[1]},
                 {"name": "Sunstone", "slot": 5, "screenX": _slot_pt(5)[0], "screenY": _slot_pt(5)[1]},
                 {"name": "Sunstone", "slot": 12, "screenX": _slot_pt(12)[0], "screenY": _slot_pt(12)[1]}]
    chisel_pt, stones = gc.chisel_burst_order(
        inventory, (gc.JEWELLERS_CHISEL_NAME, gc.CHISEL_NAME), gc.RAW_SUNSTONE_NAME, 3)
    assert chisel_pt == CHISEL_PT
    assert stones == [_slot_pt(5), _slot_pt(12), _slot_pt(24)]


def test_the_burst_never_takes_more_stones_than_the_batch_still_needs(monkeypatch):
    inventory = [{"name": "Chisel", "slot": 1, "screenX": CHISEL_PT[0], "screenY": CHISEL_PT[1]}]
    inventory += [{"name": "Sunstone", "slot": 4 + n,
                   "screenX": _slot_pt(4 + n)[0], "screenY": _slot_pt(4 + n)[1]} for n in range(20)]
    _, stones = gc.chisel_burst_order(
        inventory, (gc.CHISEL_NAME,), gc.RAW_SUNSTONE_NAME, 2)
    assert len(stones) == 2


def test_chisel_phase_stops_chaining_when_paused(monkeypatch):
    _fake_clock(monkeypatch)
    ctx, inventory, clicks = _chisel_world(monkeypatch, pause_after=1)
    gc.chisel_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(_stones_clicked(clicks)) == 1
    assert ctx.state.data.get("gc_phase") != "craft"


def test_chisel_phase_retries_next_tick_when_a_core_does_not_appear(monkeypatch):
    """A pair that converted nothing (a click that missed its slot) must NOT be retried inside
    this call -- the next tick re-resolves the slots first. Re-clicking against the stale list is
    the 2026-08-08 chisel-on-an-already-converted-stone bug."""
    _fake_clock(monkeypatch)
    ctx, inventory, clicks = _chisel_world(monkeypatch, convert=False)
    gc.chisel_phase(ctx, gc.DEFAULT_CONFIG)
    assert sum(1 for it in inventory if it["name"] == "Sunstone core") == 0
    assert ctx.state.data.get("gc_phase") != "craft"


def test_chisel_phase_no_longer_waits_for_the_player_to_stop_moving(monkeypatch):
    """The regression this rewrite exists for. isIdle = !moving && anim == -1 && no target
    (CombatProvider), so the old entry gate returned every tick for the whole walk to the plinth
    and the first core only went out on arrival."""
    _fake_clock(monkeypatch)
    ctx, inventory, clicks = _chisel_world(monkeypatch, moving=True)
    ctx.state.data["gc_chisel_moving_ok"] = True      # already probed this process
    monkeypatch.setattr(controller, "get_combat",
                        lambda: (_ for _ in ()).throw(AssertionError("must not gate on isIdle")))
    gc.chisel_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(_stones_clicked(clicks)) == gc.CORES_PER_TRIP


def test_the_first_moving_burst_probes_with_one_pair_before_spending_the_rest(monkeypatch):
    """Chiselling while walking is the user's claim and is in no recording here, so it is proven
    once, cheaply, before the whole burst is committed to it."""
    _fake_clock(monkeypatch)
    ctx, inventory, clicks = _chisel_world(monkeypatch, moving=True, convert=False)
    monkeypatch.setattr(gc.outcome_waits, "await_pre", lambda *a, **kw: None)
    gc.chisel_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(_stones_clicked(clicks)) == 1, "a failed probe must cost ONE pair, not a burst"
    assert ctx.state.data["gc_chisel_moving_ok"] is False


def test_a_probe_that_took_bursts_the_rest_immediately(monkeypatch):
    _fake_clock(monkeypatch)
    ctx, inventory, clicks = _chisel_world(monkeypatch, moving=True)
    monkeypatch.setattr(gc.outcome_waits, "await_pre", lambda *a, **kw: True)
    gc.chisel_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.data["gc_chisel_moving_ok"] is True
    assert len(_stones_clicked(clicks)) == gc.CORES_PER_TRIP


def test_a_failed_probe_is_remembered_and_the_burst_waits_for_arrival(monkeypatch):
    _fake_clock(monkeypatch)
    ctx, inventory, clicks = _chisel_world(monkeypatch, moving=True)
    ctx.state.data["gc_chisel_moving_ok"] = False
    gc.chisel_phase(ctx, gc.DEFAULT_CONFIG)
    assert clicks == [], "nothing may be clicked while walking once the probe said it does not take"


def test_bank_trip_confirms_the_fur_withdrawal_on_the_inventory_event(monkeypatch):
    _fake_clock(monkeypatch)
    inventory = []
    fur_slot = {"name": falconry.FUR_NAME, "qty": 17, "screenX": 300, "screenY": 300}
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory))
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": [fur_slot]})
    import banker
    monkeypatch.setattr(banker, "deposit_inventory", lambda ctx, cfg: None)
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx, mode: True)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()

    def click_entity(ser, entity, **kw):
        inventory.append({"name": falconry.FUR_NAME, "qty": 17})
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.data["gc_fur_stock"] == 17
    assert ctx.state.data["gc_phase"] == "mine"


def test_await_player_stopped_uses_the_pushed_event_when_the_client_reports_moving(monkeypatch):
    clock = _fake_clock(monkeypatch)
    moving = {"v": True}
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 1, "moving": moving["v"]})
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("polled instead of awaiting")))
    ctx = _event_stream(monkeypatch, [
        [("PLAYER_STOPPED", {"tick": 4}, lambda: moving.__setitem__("v", False))],
    ])
    assert gc._await_player_stopped(ctx, 2.0) is True
    assert clock["t"] - 5000.0 < 0.5


def test_await_player_stopped_falls_back_to_the_stillness_poll_on_an_old_client(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 1})   # no `moving`
    calls = []
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda **kw: calls.append(kw) or True)
    ctx = _event_stream(monkeypatch, [])
    assert gc._await_player_stopped(ctx, 2.0) is True
    assert calls and calls[0]["timeout_s"] == 2.0


# --- Task 4: stall watch and phase chaining ------------------------------------------------

def _quiet_world(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Chisel"}])
    monkeypatch.setattr(controller, "get_golem",
                        lambda: {"totalCrafted": 5, "inArea": True, "stations": [_station("north")]})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2595, "y": 2253, "regionId": 10275})


def test_stuck_watch_stops_a_run_with_no_progress(monkeypatch):
    clock = _fake_clock(monkeypatch)
    _quiet_world(monkeypatch)
    ctx = _ctx()
    ctx.state.data["gc_phase"] = "craft"
    gc._stuck_watch(ctx)
    clock["t"] += gc.STALL_STOP_S + 1
    gc._stuck_watch(ctx)
    assert ctx.state.safety_stopped
    assert "no progress" in (ctx.state.safety_reason or ctx.state.stop_reason or "no progress")


def test_stuck_watch_counts_a_golem_progress_change_as_progress(monkeypatch):
    clock = _fake_clock(monkeypatch)
    _quiet_world(monkeypatch)
    ctx = _ctx()
    ctx.state.data["gc_phase"] = "craft"
    gc._stuck_watch(ctx)
    clock["t"] += gc.STALL_STOP_S - 1
    monkeypatch.setattr(controller, "get_golem",
                        lambda: {"totalCrafted": 5, "inArea": True,
                                 "stations": [_station("north", progress=2)]})
    gc._stuck_watch(ctx)
    clock["t"] += gc.STALL_STOP_S - 1
    gc._stuck_watch(ctx)
    assert not ctx.state.safety_stopped


def test_stuck_watch_counts_walking_as_progress(monkeypatch):
    clock = _fake_clock(monkeypatch)
    _quiet_world(monkeypatch)
    ctx = _ctx()
    ctx.state.data["gc_phase"] = "fur_trip"
    gc._stuck_watch(ctx)
    clock["t"] += gc.STALL_STOP_S - 1
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2600, "y": 2253, "regionId": 10275})
    gc._stuck_watch(ctx)
    clock["t"] += gc.STALL_STOP_S - 1
    gc._stuck_watch(ctx)
    assert not ctx.state.safety_stopped


def _tick_world(monkeypatch, handlers):
    _quiet_world(monkeypatch)
    monkeypatch.setattr(gc, "PHASES", handlers)
    monkeypatch.setattr(gc, "_stuck_watch", lambda ctx: None)
    ctx = _ctx()
    ctx.state.config = gc.DEFAULT_CONFIG
    ctx.state.data["gc_level_checked"] = True
    return ctx


def test_tick_chains_chisel_into_craft(monkeypatch):
    calls = []

    def chisel(c, cfg):
        calls.append("chisel")
        c.state.data["gc_phase"] = "craft"

    def craft(c, cfg):
        calls.append("craft")
    ctx = _tick_world(monkeypatch, {"chisel": chisel, "craft": craft})
    ctx.state.data["gc_phase"] = "chisel"
    gc.tick(ctx)
    assert calls == ["chisel", "craft"]


def test_tick_chains_bank_into_its_successor(monkeypatch):
    calls = []

    def bank(c, cfg):
        calls.append("bank")
        c.state.data["gc_phase"] = "mine"

    def mine(c, cfg):
        calls.append("mine")
    ctx = _tick_world(monkeypatch, {"bank": bank, "mine": mine})
    ctx.state.data["gc_phase"] = "bank"
    gc.tick(ctx)
    assert calls == ["bank", "mine"]


def test_tick_chains_straight_from_mine_into_the_chisel_burst(monkeypatch):
    """CHANGED 2026-09-14: mine used to be excluded from TICK_CHAIN_FROM because it was one
    bounded swing per call. It now ends by clicking a hop toward the plinth, and the whole point
    of the chisel burst is that it runs DURING that walk -- an engine-loop iteration in between
    spends ~1s of a ~3-5s walk standing at the rocks."""
    calls = []

    def mine(c, cfg):
        calls.append("mine")
        c.state.data["gc_phase"] = "chisel"

    def chisel(c, cfg):
        calls.append("chisel")
    ctx = _tick_world(monkeypatch, {"mine": mine, "chisel": chisel})
    ctx.state.data["gc_phase"] = "mine"
    gc.tick(ctx)
    assert calls == ["mine", "chisel"]


def test_tick_chain_stops_when_paused(monkeypatch):
    calls = []

    def chisel(c, cfg):
        calls.append("chisel")
        c.state.data["gc_phase"] = "craft"
        c.state.paused = True

    def craft(c, cfg):
        calls.append("craft")
    ctx = _tick_world(monkeypatch, {"chisel": chisel, "craft": craft})
    ctx.state.data["gc_phase"] = "chisel"
    gc.tick(ctx)
    assert calls == ["chisel"]


# --- Task 6: the falconry fur trip (2026-09-06) ------------------------------------------

import falconry


def _player_at(region, x=0, y=0):
    return {"regionId": region, "x": x, "y": y, "plane": 0}


def test_trip_leg_dispatch_table():
    fresh = {}
    kitted = {"kit": True}
    done = {"kit": True, "done": True}
    W = 10275
    assert gc.trip_leg(_player_at(W, 2587, 2260), fresh) == "kit"
    assert gc.trip_leg(_player_at(W, 2587, 2260), kitted) == "depart"
    assert gc.trip_leg(_player_at(W, 2587, 2260), done) == "home"
    assert gc.trip_leg(_player_at(falconry.REGION_MONASTERY), kitted) == "fairy_ring"
    assert gc.trip_leg(_player_at(falconry.REGION_FAIRY_RING), kitted) == "fairy_ring"
    assert gc.trip_leg(_player_at(falconry.REGION_FAIRY_LANDING), kitted) == "walk_in"
    assert gc.trip_leg(_player_at(falconry.REGION_FALCONRY, 2370, 3622), kitted) == "walk_in"
    assert gc.trip_leg(_player_at(falconry.REGION_FALCONRY, 2388, 3590), kitted) == "hunt"
    assert gc.trip_leg(_player_at(falconry.REGION_FALCONRY, 2388, 3590), done) == "return"
    assert gc.trip_leg(_player_at(10374, 2590, 8615), done) == "cavern"
    assert gc.trip_leg(_player_at(10374, 2590, 8615), fresh) == "cavern"   # restart underground
    assert gc.trip_leg(_player_at(falconry.REGION_FALCONRY, 2388, 3590), fresh) == "hunt"  # restart mid-hunt
    assert gc.trip_leg(_player_at(9776), kitted) == "unknown"


def test_default_config_has_no_goat_pit_and_a_fur_trip_block():
    assert "goatPit" not in gc.DEFAULT_CONFIG["locations"]
    assert "spikesSupply" not in gc.DEFAULT_CONFIG["locations"]
    assert gc.DEFAULT_CONFIG["locations"]["cavernExit"] is None
    assert gc.DEFAULT_CONFIG["furTrip"]["returnTeleport"] == "slayer ring"
    assert "fur_trip" in gc.PHASES and "hunt" not in gc.PHASES
    assert not hasattr(gc, "hunt_phase")


def test_get_config_forces_the_full_inventory_stop_off(monkeypatch):
    monkeypatch.setattr(controller, "get_golem_crafting_config",
                        lambda: {"enabled": True, "safety": {"stopOnInventoryFull": True}})
    cfg = gc.get_config(None)
    assert cfg["safety"]["stopOnInventoryFull"] is False
    assert cfg["furTrip"]["hireCoins"] == 2000


def test_withdraw_kit_stops_naming_a_missing_item(monkeypatch):
    _fake_clock(monkeypatch)
    import banker
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Ardougne cloak 4"}, {"name": "Coins", "qty": 5000}])
    monkeypatch.setattr(banker, "search_and_withdraw", lambda ctx, name, qty, contains=False: False)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    # Since 2026-09-15 the stop comes only after KIT_WITHDRAW_TRIES: one failed click is not
    # evidence about the bank's contents (a disconnect mid-withdrawal produced exactly that, and
    # cost 52 minutes). The item is still named when it does stop, which is what this pins.
    for _ in range(gc.KIT_WITHDRAW_TRIES):
        assert gc._withdraw_kit(ctx, gc.DEFAULT_CONFIG) is False
    assert ctx.state.safety_stopped
    assert "slayer ring" in ctx.state.safety_reason


def test_withdraw_kit_confirms_each_item_and_tops_up_coins(monkeypatch):
    _fake_clock(monkeypatch)
    import banker
    inventory = [{"name": "Coins", "qty": 100}]
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory))

    def search_and_withdraw(ctx, name, qty, contains=False):
        inventory.append({"name": name.title(), "qty": 1})
        return True
    monkeypatch.setattr(banker, "search_and_withdraw", search_and_withdraw)
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": [{"name": "Coins", "qty": 100000, "screenX": 1, "screenY": 1}]})
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, e, option, **kw: True)
    typed = []

    def type_digits(ser, text):
        typed.append(text)
        inventory[0] = {"name": "Coins", "qty": 100 + int(text)}
    monkeypatch.setattr(controller, "type_digits_and_enter", type_digits)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()

    assert gc._withdraw_kit(ctx, gc.DEFAULT_CONFIG) is True
    names = {i["name"].lower() for i in inventory}
    assert "ardougne cloak" in names and "slayer ring" in names
    assert "dramen staff" not in names      # fairy rings need no staff (USER 2026-09-06)
    assert typed == ["1900"]


def test_hunt_done_on_spilled_fur_sets_a_full_pouch(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": falconry.FUR_NAME}])
    ctx = _ctx()
    trip = {"started": 0.0, "catches": 3}
    assert gc._hunt_done(ctx, gc.DEFAULT_CONFIG["furTrip"], trip) is True
    assert ctx.state.data["gc_fur_stock"] == gc.FUR_POUCH_CAPACITY


def test_hunt_done_on_the_time_cap_keeps_the_catch_count(monkeypatch):
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _ctx()
    trip = {"started": clock["t"] - 26 * 60, "catches": 9}
    assert gc._hunt_done(ctx, gc.DEFAULT_CONFIG["furTrip"], trip) is True
    assert ctx.state.data["gc_fur_stock"] == 9
    trip = {"started": clock["t"] - 60, "catches": 2}
    assert gc._hunt_done(ctx, gc.DEFAULT_CONFIG["furTrip"], trip) is False


def test_teleport_home_picks_the_wyrmscraig_dialogue_option(monkeypatch):
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Slayer ring (8)", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    # The left-click is withheld (hover says "Wear": no swap on this ring) -> menu fallback.
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: controller.CLICK_WITHHELD)
    options_seen = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, option, **kw: options_seen.append(option) or option != "Wyrmscraig")
    dialogue = {"options": ["Select an Option", "Stronghold Slayer Cave", "Slayer Tower",
                            "Fremennik Slayer Dungeon", "Tarn's Lair", "Dark Beasts",
                            "Wyrmscraig Cavern", "", ""]}
    monkeypatch.setattr(controller, "get_api", lambda group, reader: dialogue)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    player = {"regionId": 9528, "x": 2388, "y": 3590}
    monkeypatch.setattr(controller, "get_player", lambda: dict(player))
    ctx = _event_stream(monkeypatch, [
        [("PLAYER_STOPPED", {"tick": 1}, lambda: player.update({"regionId": 10374, "x": 2590, "y": 8615}))],
    ])
    ctx.state = controller.ControllerState()

    assert gc._teleport_home(ctx, gc.DEFAULT_CONFIG["furTrip"]) is True
    assert keys == ["6"]
    assert options_seen[-1] == ("Teleport", "Rub")


def _menu_forbidden(*a, **kw):
    raise AssertionError("the right-click menu must not be used here")


def test_teleport_home_left_clicks_a_swapped_ring_and_answers_the_dialogue(monkeypatch):
    """USER 2026-09-06: inventory teleports are a plain left-click now. The hover-verified
    left-click opens the destination dialogue; no right-click menu is read at all."""
    _fake_clock(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Slayer ring (8)", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicks.append(kw.get("expected_action")) or True)
    monkeypatch.setattr(controller, "click_menu_action", _menu_forbidden)
    dialogue = {"options": ["Select an Option", "Stronghold Slayer Cave", "Wyrmscraig Cavern", ""]}
    monkeypatch.setattr(controller, "get_api", lambda group, reader: dialogue)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    player = {"regionId": 9528, "x": 2388, "y": 3590}
    monkeypatch.setattr(controller, "get_player", lambda: dict(player))
    ctx = _event_stream(monkeypatch, [
        [("PLAYER_STOPPED", {"tick": 1}, lambda: player.update({"regionId": 10374, "x": 2590, "y": 8615}))],
    ])
    ctx.state = controller.ControllerState()

    assert gc._teleport_home(ctx, gc.DEFAULT_CONFIG["furTrip"]) is True
    assert keys == ["2"]
    assert clicks and "Wyrmscraig" in clicks[0]


def test_leave_cavern_clicks_the_exit_and_records_its_tile(monkeypatch):
    _fake_clock(monkeypatch)
    exit_obj = {"name": "Cave", "actions": ["Exit"], "id": 62220, "x": 2600, "y": 8640, "plane": 0,
                "distance": 6, "screenX": 400, "screenY": 300}
    monkeypatch.setattr(controller, "get_objects",
                        lambda radius=None, fresh=False, interactable=False: [exit_obj, {"name": "Cave", "actions": [], "id": 1}])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    player = {"regionId": 10374, "x": 2590, "y": 8615}
    monkeypatch.setattr(controller, "get_player", lambda: dict(player))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _event_stream(monkeypatch, [
        [("PLAYER_STOPPED", {"tick": 1}, lambda: player.update({"regionId": 10275, "x": 2600, "y": 2240}))],
    ])
    ctx.state = controller.ControllerState()
    trip = {}
    assert gc._leave_cavern(ctx, gc.DEFAULT_CONFIG, trip) is True
    assert ctx.state.data["gc_cavern_exit"] == {"x": 2600, "y": 8640, "plane": 0}


def test_leave_cavern_walks_to_a_recorded_exit_when_none_is_visible(monkeypatch):
    _fake_clock(monkeypatch)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    walks = []
    import travel
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walks.append(target) or True)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    ctx.state.data["gc_cavern_exit"] = {"x": 2600, "y": 8640, "plane": 0}
    assert gc._leave_cavern(ctx, gc.DEFAULT_CONFIG, {}) is False
    assert walks == [{"x": 2600, "y": 8640, "plane": 0}]


def test_leave_cavern_gives_up_after_the_scan_limit(monkeypatch):
    _fake_clock(monkeypatch)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    trip = {}
    for _ in range(gc.CAVERN_SCAN_LIMIT):
        gc._leave_cavern(ctx, gc.DEFAULT_CONFIG, trip)
    assert ctx.state.safety_stopped


def test_run_bank_trip_with_no_fur_kits_up_and_starts_the_trip(monkeypatch):
    _fake_clock(monkeypatch)
    import banker
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": []})
    deposits = []
    monkeypatch.setattr(banker, "deposit_inventory", lambda ctx, cfg: deposits.append(cfg))
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    monkeypatch.setattr(gc, "_remembered_bank_fur", lambda: None)   # bank genuinely holds none
    monkeypatch.setattr(gc, "_withdraw_kit", lambda ctx, config: True)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    # An established count of zero, i.e. this run KNOWS the pouch is empty. Added 2026-09-15:
    # without it this exercises the never-counted case instead, which now deliberately declines
    # to spend a five-minute trip on an unread pouch (see the tests beside _no_fur_bank).
    ctx.state.data["gc_fur_stock"] = 0

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.data["gc_phase"] == "fur_trip"
    assert ctx.state.data["gc_trip"]["kit"] is True
    assert "sunstone" not in deposits[-1]["keepItems"]


def test_run_bank_trip_searches_for_fur_even_when_the_pouch_WAS_stocked(monkeypatch):
    """REVERSED 2026-09-16, and the reversal is the bug fix. This used to assert the fur search
    was SKIPPED when gc_fur_stock said the pouch was full -- an optimisation that was correct only
    if the pouch survived the bank visit. It does not: banker.deposit_inventory ends with
    deposit_all_containers(), the bank's "Deposit Containers" button, which empties the fur pouch
    along with every other container. So the skip meant "empty the pouch, withdraw nothing, go
    mining" (USER: "it deposits all fur but never picks it up then goes mining")."""
    _fake_clock(monkeypatch)
    import banker
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    # Fully geared, so the gear-recovery step added 2026-09-14 is a no-op here and the only search
    # that can show up is the FUR one, which is what this test is about.
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": {"name": "Rune pickaxe"},
                                 "gloves": {"name": "Expert mining gloves"},
                                 "shield": {"name": "Imcando hammer (off-hand)"}})
    monkeypatch.setattr(banker, "deposit_inventory", lambda ctx, cfg: None)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx: True)
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": []})
    monkeypatch.setattr(gc, "_remembered_bank_fur", lambda: None)
    monkeypatch.setattr(gc, "_withdraw_kit", lambda ctx, config: False)
    searched = []
    monkeypatch.setattr(banker, "search_bank", lambda ctx, q: searched.append(q) or True)
    ctx = _event_stream(monkeypatch, [])
    ctx.state = controller.ControllerState()
    ctx.state.data["gc_fur_stock"] = gc.FUR_POUCH_CAPACITY

    gc._run_bank_trip(ctx, gc.DEFAULT_CONFIG)

    assert searched == ["fur"], "the pouch did not survive the deposit, so the fur must be re-taken"
    assert ctx.state.data["gc_fur_stock"] == 0, "and the stale count must not persist"


def test_bank_phase_underground_resumes_the_cavern_leg(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"regionId": 10374, "x": 2590, "y": 8615})
    ctx = _ctx()
    gc.bank_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.data["gc_phase"] == "fur_trip"
    assert ctx.state.data["gc_trip"]["done"] is True


def test_fur_trip_hunt_leg_counts_retrieves_and_ends_on_spill(monkeypatch):
    _fake_clock(monkeypatch)
    inventory = [{"name": "Large fur pouch (open)"}]
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory))
    monkeypatch.setattr(controller, "get_player", lambda: {"regionId": falconry.REGION_FALCONRY, "x": 2388, "y": 3590})
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Falconer's glove"}, "shield": None, "gloves": None})
    steps = iter(["retrieved", "missed", "retrieved"])
    monkeypatch.setattr(falconry, "hunt_step", lambda ctx, cfg, drop_meat=False: next(steps))
    ctx = _ctx()
    ctx.state.data["gc_phase"] = "fur_trip"
    ctx.state.data["gc_trip"] = {"kit": True, "done": False, "started": 5000.0, "catches": 0}
    for _ in range(3):
        gc.fur_trip_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.data["gc_trip"]["catches"] == 2
    inventory.append({"name": falconry.FUR_NAME})
    gc.fur_trip_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.data["gc_trip"]["done"] is True
    assert ctx.state.data["gc_fur_stock"] == gc.FUR_POUCH_CAPACITY


def test_fur_trip_home_leg_hands_back_to_the_bank_when_one_is_near(monkeypatch):
    import banker
    monkeypatch.setattr(controller, "get_player", lambda: {"regionId": 10275, "x": 2587, "y": 2262})
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: {"name": "Bank chest"})
    ctx = _ctx()
    ctx.state.data["gc_phase"] = "fur_trip"
    ctx.state.data["gc_trip"] = {"kit": True, "done": True}
    gc.fur_trip_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.data["gc_phase"] == "bank"
    assert ctx.state.data["gc_trip"] == {}
