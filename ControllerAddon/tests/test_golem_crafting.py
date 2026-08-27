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
    # And the same again for the inventory. _shed_pit_junk runs at the top of BOTH mine_phase and
    # craft_phase and reads the inventory to decide whether to drop leftover pit spikes -- so
    # every craft test that never needed get_inventory suddenly reached the live client through
    # it, and blew up on key_tap once the real character happened to be carrying spikes
    # (2026-08-24). Tests that care about inventory contents override this.
    monkeypatch.setattr(controller, "get_inventory", lambda: [])


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


def test_spikes_supply_menu_click_passes_refresh_target(monkeypatch):
    """_ensure_wooden_spikes's Take-5 click should re-resolve the supply's live screen
    position."""
    supply = {"x": 100, "y": 200, "plane": 0, "id": 42, "name": "Spikes supply"}
    monkeypatch.setattr(gc, "find_item", lambda name: None if name == gc.WOODEN_SPIKES_NAME
                         else {"name": name})
    monkeypatch.setattr(gc, "find_object", lambda name: supply)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    gc._ensure_wooden_spikes(_ctx(), gc.DEFAULT_CONFIG)
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


# --- mine_phase's reclick gate (XP-event driven, 2026-08-23) --------------------------
#
# The gate answers one question -- "has the swing I last clicked for actually resolved?" -- and
# used to answer it by inferring from raw sunstone appearing in the inventory. Mining XP is the
# game's own direct answer to it (pushed over UDP the moment the client's StatChanged fires), so
# XP is now the primary signal and the inventory count is the lossy-UDP fallback. Both paths are
# asserted here: the fallback is not decoration, it is what runs whenever a packet is dropped or
# ctx.events is None.


class _FakeSock:
    """Identity token for bridge_events.EventStream -- controller.poll_udp is stubbed."""


def _events(monkeypatch, batches):
    """A real EventStream (not a mock) whose poll_udp yields each batch in turn, then nothing.
    Same idiom as tests/test_bridge_events.py's own _stream helper."""
    import bridge_events
    queue = list(batches)
    monkeypatch.setattr(controller, "poll_udp", lambda sock: queue.pop(0) if queue else [])
    return bridge_events.EventStream(_FakeSock())


def test_a_mining_xp_gain_resolves_the_swing():
    assert not gc._mine_swing_pending(
        last_raw=3, raw_count=3, mining_xp_seen=True, last_click_time=1000.0, now=1000.1)


def test_raw_sunstone_appearing_resolves_the_swing_without_any_xp_event():
    """The fallback path: UDP is lossy and ctx.events can be None, so a swing whose XP packet
    never arrived must still resolve off the inventory read exactly as it did before."""
    assert not gc._mine_swing_pending(
        last_raw=3, raw_count=4, mining_xp_seen=False, last_click_time=1000.0, now=1000.1)


def test_neither_signal_holds_the_reclick_until_the_timeout():
    assert gc._mine_swing_pending(
        last_raw=3, raw_count=3, mining_xp_seen=False, last_click_time=1000.0, now=1000.1)


def test_a_swing_with_no_signal_at_all_is_reclicked_once_the_timeout_expires():
    """click_entity's deliberate simulated misclick lands nowhere useful, so neither signal is
    ever coming -- MINE_RECLICK_TIMEOUT_S is what stops that stalling the whole trip."""
    assert not gc._mine_swing_pending(
        last_raw=3, raw_count=3, mining_xp_seen=False,
        last_click_time=1000.0, now=1000.0 + gc.MINE_RECLICK_TIMEOUT_S + 0.01)


def test_nothing_is_pending_before_the_first_click_of_a_trip():
    """last_raw is None until mine_phase has clicked once -- the very first swing must not be
    held back waiting for a swing that was never issued."""
    assert not gc._mine_swing_pending(
        last_raw=None, raw_count=0, mining_xp_seen=False, last_click_time=0.0, now=1000.0)


def test_mining_xp_is_seen_when_the_event_is_in_the_buffer(monkeypatch):
    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[("XP_GAINED", {"skill": "Mining", "delta": 35})]])
    assert gc._mining_xp_seen(ctx)


def test_xp_in_another_skill_does_not_resolve_a_mining_swing(monkeypatch):
    """Crafting XP arrives constantly in this routine (chiselling, Insert-core) and the fur-pouch
    trip crosses other skills -- only Mining may satisfy the mining gate."""
    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[("XP_GAINED", {"skill": "Crafting", "delta": 50})]])
    assert not gc._mining_xp_seen(ctx)


def test_mining_xp_is_not_seen_when_no_event_arrived(monkeypatch):
    ctx = _ctx()
    ctx.events = _events(monkeypatch, [])
    assert not gc._mining_xp_seen(ctx)


def test_the_xp_check_tolerates_ctx_events_being_none():
    """The routine contract: ctx.events is absent whenever the engine started no listener, and
    every consumer must degrade to its HTTP fallback rather than raise."""
    ctx = _ctx()
    ctx.events = None
    assert not gc._mining_xp_seen(ctx)


def test_the_xp_check_does_not_block(monkeypatch):
    """A zero timeout peek at the buffer, not a wait: mine_phase runs one bounded action per
    call so the engine's safety/break checks keep their cadence. A sleeping peek would stall
    them for the whole timeout on every unresolved swing."""
    import bridge_events
    monkeypatch.setattr(bridge_events.time, "sleep",
                        lambda d: pytest.fail(f"the XP peek must not sleep (slept {d}s)"))
    ctx = _ctx()
    ctx.events = _events(monkeypatch, [])
    assert not gc._mining_xp_seen(ctx)


def _stub_mining_area(monkeypatch, inventory):
    """Stubs the bridge reads mine_phase makes so it reaches its click, and records every click.
    `inventory` is a live list the test mutates to simulate ore arriving (or not)."""
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory)
    monkeypatch.setattr(
        controller, "get_objects",
        lambda radius=None: [{"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20,
                              "plane": 0, "distance": 2, "visible": True}],
    )
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)
    return clicks


def test_a_mining_xp_event_frees_the_next_swing_before_the_inventory_read_would(monkeypatch):
    """The point of the rewrite: the swing is resolved by the game's own XP signal, so the next
    click goes out without waiting on raw sunstone showing up in an inventory poll -- and without
    burning MINE_RECLICK_TIMEOUT_S when it never will."""
    inventory = []
    clicks = _stub_mining_area(monkeypatch, inventory)
    ctx = _ctx()
    # Two empty batches first: mine_phase pumps once for its gate check and once more to drop
    # stale events immediately before clicking, so the XP has to land on the batch AFTER those
    # to represent a gain caused by the swing this click just issued.
    ctx.events = _events(monkeypatch,
                         [[], [], [("XP_GAINED", {"skill": "Mining", "delta": 35})]])

    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(clicks) == 1, "the first swing of the trip must click"

    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)
    assert len(clicks) == 2, \
        "a Mining XP gain proves the swing resolved -- the next one must not wait for the timeout"


def test_mine_phase_drops_a_stale_xp_event_before_issuing_its_click(monkeypatch):
    """The race the pump-before-clear closes: the PREVIOUS swing's XP packet can still be sitting
    unread in the socket while this click is being issued, and would otherwise resolve the swing
    we have not even made yet -- turning the gate back into the "clicks all over" noise it exists
    to prevent."""
    inventory = []
    _stub_mining_area(monkeypatch, inventory)
    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[("XP_GAINED", {"skill": "Mining", "delta": 35})]])

    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert not gc._mining_xp_seen(ctx), \
        "the XP event that predates this click must not still be visible to the next gate check"


# --- mine_phase pre-hovers the next rock while a swing resolves (2026-08-24) -----------
#
# User directive, informed by two recorded human mining sessions: Sunstone's "momentum" mechanic
# rewards fast, consistent click cadence, and the routine's own recorded cadence was ~2x the
# human's even though a target was always found -- the lost time was click_entity's own
# move-then-click travel, paid AFTER the swing was already confirmed done. Fix: rest the mouse on
# a different ready rock while the current swing plays out (controller.hover_entity, the same
# primitive routines/mastering_mixology.py's speedup functions already use this way), so the
# click that resolves the swing only has to fire, not travel.

# BUG, reported live 2026-08-24: "we hover before we stop moving so the cursor ends up wrong."
#
# The pre-hover fires straight after click_entity, while the player is usually still WALKING to
# the rock it just clicked. Two coupled defects made that unsafe:
#
#   1. hover_entity was called with no refresh_target, so it flew to the screen position captured
#      before the walk and the camera moved out from under it mid-flight.
#   2. gc_mine_hover_screen recorded next_rock["screenX"/"screenY"] -- where the cursor was
#      PREDICTED to land, never where it actually did. The next tick's staleness gate then
#      measured drift against a value nothing had verified, so a drift under HOVER_STALE_PX could
#      pass the gate while still sitting off the rock's clickbox, and click_human fired into bare
#      ground.

def test_pre_hover_waits_for_the_player_to_stop_before_hovering(monkeypatch):
    """Defect 1, and the user's own description of the bug: the hover was issued while the player
    was still walking to the rock it had just clicked, so the camera moved out from under it."""
    inventory = []
    rock_a = {"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20, "plane": 0, "distance": 1,
              "visible": True, "screenX": 100, "screenY": 100}
    rock_b = {"id": 2, "name": "Sunstone rocks", "x": 11, "y": 20, "plane": 0, "distance": 2,
              "visible": True, "screenX": 200, "screenY": 150}
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [rock_a, rock_b])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (100, 100))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "click_human", lambda ser: None)

    order = []
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda **kw: order.append("idle") or True)
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, entity, **kw: order.append("hover") or True)

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[], []])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert "hover" in order, "no pre-hover happened"
    assert order.index("idle") < order.index("hover"), \
        "must wait for the player to stop BEFORE hovering -- a walking player invalidates the " \
        "screen position continuously"


def test_pre_hover_is_skipped_when_the_player_never_stops(monkeypatch):
    """If the wait times out the player is still moving, so any position resolved now is already
    stale. Skipping costs nothing -- the next tick just does a normal move+click."""
    inventory = []
    rock_a = {"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20, "plane": 0, "distance": 1,
              "visible": True, "screenX": 100, "screenY": 100}
    rock_b = {"id": 2, "name": "Sunstone rocks", "x": 11, "y": 20, "plane": 0, "distance": 2,
              "visible": True, "screenX": 200, "screenY": 150}
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [rock_a, rock_b])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (100, 100))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "click_human", lambda ser: None)
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda **kw: False)
    hovers = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, entity, **kw: hovers.append(entity) or True)

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[], []])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert hovers == [], "must not hover while the player is still moving"
    assert ctx.state.data.get("gc_mine_hover_screen") is None


def test_pre_hover_records_where_the_cursor_actually_LANDED(monkeypatch):
    """Defect 2, and the one that actually mis-clicked.

    The stored anchor must be the real cursor position, not next_rock's predicted screen point.
    Here the cursor ends up at (177, 141) while the rock was predicted at (200, 150); recording
    the prediction would leave the next tick measuring staleness against a fiction.
    """
    inventory = []
    rock_a = {"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20, "plane": 0, "distance": 1,
              "visible": True, "screenX": 100, "screenY": 100}
    rock_b = {"id": 2, "name": "Sunstone rocks", "x": 11, "y": 20, "plane": 0, "distance": 2,
              "visible": True, "screenX": 200, "screenY": 150}
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [rock_a, rock_b])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "click_human", lambda ser: None)
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda **kw: True)
    # (100,100) while the click is decided; (177,141) once the hover has actually flown. Keyed on
    # the hover having happened rather than on a call count, so the test does not silently depend
    # on how many times mine_phase reads the cursor.
    hovered = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, entity, **kw: hovered.append(True) or True)
    monkeypatch.setattr(controller, "get_cursor_pos",
                        lambda: (177, 141) if hovered else (100, 100))

    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[], []])
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.data["gc_mine_hover_screen"] == (177, 141), (
        "must store the ACTUAL post-hover cursor position; storing the rock's predicted "
        "screenX/screenY is what let a drifted cursor pass the staleness gate")


def test_find_mining_target_excludes_the_given_position(monkeypatch):
    rock_a = {"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20, "distance": 1, "visible": True}
    rock_b = {"id": 2, "name": "Sunstone rocks", "x": 11, "y": 20, "distance": 2, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [rock_a, rock_b])
    assert gc.find_mining_target(exclude_pos=(10, 20)) is rock_b


def test_find_mining_target_returns_none_when_the_only_candidate_is_excluded(monkeypatch):
    rock_a = {"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20, "distance": 1, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [rock_a])
    assert gc.find_mining_target(exclude_pos=(10, 20)) is None


def _stub_mining_area_multi(monkeypatch, inventory, rocks, cursor_pos=(100, 100)):
    """Like _stub_mining_area but with a configurable list of ready rock objects (needed to
    exercise the pre-hover step, which requires more than one candidate to pick a 'next' rock
    from), and records hover_entity/click_human calls alongside click_entity.

    cursor_pos defaults to matching every test's hover screen position (100, 100) below -- pass a
    different point to simulate something else having moved the cursor since the hover."""
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: list(rocks))
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: cursor_pos)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)
    hovers = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, entity, **kw: hovers.append(entity) or True)
    bare_clicks = []
    monkeypatch.setattr(controller, "click_human", lambda ser: bare_clicks.append(True))
    return clicks, hovers, bare_clicks


def test_mine_phase_hovers_a_different_ready_rock_after_a_successful_click(monkeypatch):
    inventory = []
    rock_a = {"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20, "plane": 0, "distance": 1,
              "visible": True, "screenX": 100, "screenY": 100}
    rock_b = {"id": 2, "name": "Sunstone rocks", "x": 11, "y": 20, "plane": 0, "distance": 2,
              "visible": True, "screenX": 200, "screenY": 150}
    clicks, hovers, _ = _stub_mining_area_multi(monkeypatch, inventory, [rock_a, rock_b])
    ctx = _ctx()
    ctx.events = _events(monkeypatch, [[], []])

    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert clicks == [rock_a], "must click the sticky/closest rock first"
    assert hovers == [rock_b], "must pre-hover the OTHER ready rock, not the one just clicked"
    assert ctx.state.data["gc_mine_hover_pos"] == (11, 20)
    # (100, 100) is the stub's CURSOR position, not rock_b's screen point (200, 150). Before the
    # 2026-08-24 fix this asserted the latter -- i.e. it encoded the bug: the anchor recorded
    # where the cursor was predicted to land, never where it actually did.
    assert ctx.state.data["gc_mine_hover_screen"] == (100, 100)


def test_mine_phase_uses_the_fast_click_path_when_already_hovering_the_target(monkeypatch):
    """Once hovered, the swing that resolves fires click_human directly -- no further
    click_entity (a fresh move_to_human) call. This is the whole point of the pre-hover."""
    inventory = []
    rock_a = {"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20, "plane": 0, "distance": 1,
              "visible": True, "screenX": 100, "screenY": 100}
    clicks, hovers, bare_clicks = _stub_mining_area_multi(monkeypatch, inventory, [rock_a])
    ctx = _ctx()
    ctx.state.data["gc_mine_hover_pos"] = (10, 20)
    ctx.state.data["gc_mine_hover_screen"] = (100, 100)
    ctx.events = _events(monkeypatch, [[], []])

    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert bare_clicks == [True], "the fast path must fire a bare click_human"
    assert clicks == [], "click_entity must not run a fresh move+click when already hovering it"


def test_mine_phase_falls_back_to_a_fresh_click_when_the_hover_has_drifted_too_far(monkeypatch):
    """A stale hover (screen position drifted past HOVER_STALE_PX since it was recorded) must
    not fire a blind click_human -- fall back to click_entity's own move+click instead."""
    inventory = []
    rock_a = {"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20, "plane": 0, "distance": 1,
              "visible": True, "screenX": 100, "screenY": 100}
    clicks, hovers, bare_clicks = _stub_mining_area_multi(monkeypatch, inventory, [rock_a])
    ctx = _ctx()
    ctx.state.data["gc_mine_hover_pos"] = (10, 20)
    ctx.state.data["gc_mine_hover_screen"] = (100 + gc.HOVER_STALE_PX + 50, 100)
    ctx.events = _events(monkeypatch, [[], []])

    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert clicks == [rock_a], "a stale hover must fall back to click_entity"
    assert bare_clicks == [], "must not blind-click through a stale hover"


def test_mine_phase_falls_back_when_something_else_moved_the_cursor_since_the_hover(monkeypatch):
    """The rock not having drifted on screen is not enough on its own -- if the CURSOR itself
    moved away from the hover point (e.g. _shed_pit_junk dropping items, or an idle-mouse nudge),
    blind-clicking would miss even though the rock's own reported position is unchanged."""
    inventory = []
    rock_a = {"id": 1, "name": "Sunstone rocks", "x": 10, "y": 20, "plane": 0, "distance": 1,
              "visible": True, "screenX": 100, "screenY": 100}
    clicks, hovers, bare_clicks = _stub_mining_area_multi(
        monkeypatch, inventory, [rock_a], cursor_pos=(100 + gc.HOVER_STALE_PX + 50, 100))
    ctx = _ctx()
    ctx.state.data["gc_mine_hover_pos"] = (10, 20)
    ctx.state.data["gc_mine_hover_screen"] = (100, 100)
    ctx.events = _events(monkeypatch, [[], []])

    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert clicks == [rock_a], "the cursor having moved away must fall back to click_entity"
    assert bare_clicks == [], "must not blind-click from wherever the cursor actually ended up"


def test_mine_phase_clears_hover_state_once_the_budget_is_reached(monkeypatch):
    """A hover left over from the last swing of a trip must not leak into chisel_phase or the
    next trip's mining."""
    inventory = [{"name": "Sunstone"}] * gc.SUNSTONE_PER_TRIP
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory)
    ctx = _ctx()
    ctx.state.data["gc_mine_hover_pos"] = (10, 20)
    ctx.state.data["gc_mine_hover_screen"] = (100, 100)
    monkeypatch.setattr(gc, "_step_toward_plinth", lambda ctx, config: None)

    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert ctx.state.data["gc_mine_hover_pos"] is None
    assert ctx.state.data["gc_mine_hover_screen"] is None


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


def test_mine_phase_drops_pit_spikes_it_is_still_carrying(monkeypatch):
    """Wooden spikes are pit-only -- _ensure_wooden_spikes takes them from the supply AT the pit,
    and nothing outside hunt_phase ever uses them. Carrying leftovers into mining costs slots the
    20-sunstone batch needs.

    OBSERVED live 2026-08-24 02:27: 4 undropped spikes left the inventory at 28/28, so mine_phase
    hit its `len(inventory) >= INVENTORY_SIZE` cap at 15 sunstone instead of the 20 budget. The
    hunt-exit drop pass is the primary cleanup (see _drop_all); this is the backstop for whatever
    survived it, since that pass is a one-shot and the routine may not hunt again for an hour."""
    inv = [{"name": "Wooden spikes", "screenX": 1, "screenY": 1},
           {"name": "Sunstone", "screenX": 2, "screenY": 2}]
    dropped = []

    def fake_click(ser, item, **kw):
        dropped.append(item)
        if item in inv:
            inv.remove(item)
        return True

    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv))
    monkeypatch.setattr(controller, "click_entity", fake_click)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_tap", lambda *a, **k: None)
    monkeypatch.setattr(gc, "find_mining_target", lambda committed_pos=None: None)
    monkeypatch.setattr(gc, "handle_missing_target", lambda *a, **k: True)

    ctx = _ctx()
    gc.mine_phase(ctx, gc.DEFAULT_CONFIG)

    assert any((d.get("name") or "") == "Wooden spikes" for d in dropped), \
        "leftover pit spikes must be dropped before mining"
    assert not [i for i in inv if i["name"] == "Wooden spikes"]
    assert any(i["name"] == "Sunstone" for i in inv), "must never drop the ore"


def test_craft_phase_also_sheds_leftover_pit_spikes(monkeypatch):
    """The mine_phase cleanup is not enough on its own: craft_phase is reachable WITHOUT passing
    through mine_phase at all (has_start_materials only needs 4 raw + 1 core, which a part-used
    batch already satisfies), so leftovers ride straight into crafting.

    OBSERVED live 2026-08-24 02:30-02:34: restarted, banked, went straight to craft with 15
    sunstone + 4 cores + 4 leftover spikes = 28/28, and every Start-golem attempt failed with a
    menu offering no golem option at all -- plinth correctly rendered and correctly aimed at
    (verified by a frame dump), materials present, no other player nearby. A full inventory is the
    one condition distinguishing those attempts from the successful ones. NOTE: hypothesis under
    test, not a confirmed cause -- shedding pit-only junk before crafting is justified on
    inventory-budget grounds either way."""
    inv = [{"name": "Wooden spikes", "screenX": 1, "screenY": 1},
           {"name": "Sunstone core", "screenX": 2, "screenY": 2}]
    dropped = []

    def fake_click(ser, item, **kw):
        dropped.append(item)
        if item in inv:
            inv.remove(item)
        return True

    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv))
    monkeypatch.setattr(controller, "click_entity", fake_click)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "key_tap", lambda *a, **k: None)
    monkeypatch.setattr(controller, "get_golem", lambda: None)   # bail right after the cleanup

    ctx = _ctx()
    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert any((d.get("name") or "") == "Wooden spikes" for d in dropped), \
        "pit spikes must be shed before crafting too"
    assert any(i["name"] == "Sunstone core" for i in inv), "must never drop crafting materials"


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


def test_hunt_stops_promptly_and_precisely_when_the_spellbook_will_not_open(monkeypatch):
    """LIVE HALT 2026-08-24 04:38: 60 Grab casts over ~7 minutes, then the generic "Grab may not
    be resolving as expected". The real state was unambiguous and available the whole time --
    /spellbook reported visible:false and the inventory tab was open, i.e. the Magic tab never
    opened, so every "cast" clicked a spell icon that was not on screen.

    cast_telekinetic_grab ALREADY returns False for exactly this ("spellbook not open/wrong
    spellbook active"); hunt_phase simply discarded the return value. Checking it turns seven
    minutes of futile casting plus a misleading diagnosis into an immediate, accurate one."""
    monkeypatch.setattr(controller, "cast_telekinetic_grab", lambda ser, target: False)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)

    ctx = _ctx()
    for _ in range(gc.SPELL_FAILURE_LIMIT + 1):
        if ctx.state.safety_stopped:
            break
        gc._note_cast_failed(ctx)

    assert ctx.state.safety_stopped, "an unopenable spellbook must stop quickly, not burn 60 casts"
    assert "spellbook" in (ctx.state.safety_reason or "").lower(), \
        f"the reason must name the real cause, got: {ctx.state.safety_reason!r}"


def test_a_single_failed_cast_does_not_stop_the_hunt():
    """One False can be a transient miss or a tab still animating -- only a run of them means the
    spellbook is genuinely unreachable."""
    ctx = _ctx()
    gc._note_cast_failed(ctx)
    assert not ctx.state.safety_stopped


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


def test_a_landed_cast_clears_the_failure_streak():
    ctx = _ctx()
    for _ in range(gc.SPELL_FAILURE_LIMIT - 1):
        gc._note_cast_failed(ctx)
    gc._note_cast_landed(ctx)
    gc._note_cast_failed(ctx)
    assert not ctx.state.safety_stopped, "a successful cast must reset the streak"
