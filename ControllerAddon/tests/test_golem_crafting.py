import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import golem_crafting as gc
import controller


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
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane: False)
    hops = []
    monkeypatch.setattr(
        gc, "_hop_toward_point",
        lambda ctx, x, y, plane: hops.append((x, y, plane)) or False,
    )

    ctx = _ctx()
    for i in range(1, gc.GOLEM_APPROACH_MISS_LIMIT):
        gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
        assert not ctx.state.safety_stopped, f"safety-stopped too early on attempt {i}"
        assert ctx.state.data.get("gc_side_walk_misses") == i, "miss counter must advance every attempt"
        assert len(hops) == i, "each failed walk must attempt a hop, never a silent no-op"

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped, "unbounded walk failures must eventually safety-stop, not spin forever"
    assert ctx.state.data["gc_side_walk_misses"] == gc.GOLEM_APPROACH_MISS_LIMIT


def test_click_failure_does_not_spin_forever_and_eventually_safety_stops(monkeypatch):
    """Regression for IMPORTANT 2: _click_station failing was the same unbounded shape as
    CRITICAL 1's walk failure -- both call sites returned with no counter incremented. Walk
    succeeds here so the click step is actually reached; _click_station itself is stubbed to fail
    every time, and _click_station_tracked must bound that the same way the walk step is bounded."""
    station = _station("north", progress=5, done=())
    _stub_golem_area(monkeypatch, station)
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane: True)
    monkeypatch.setattr(gc, "_click_station", lambda ctx, station, objects: False)

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

    def _fake_click_menu_action(ser, entity, option, target_hint=None, offset=(0, 0)):
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
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane: True)
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
    monkeypatch.setattr(gc, "walk_to_exact_tile", lambda ctx, x, y, plane: True)
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
    must not be declared a miss just because elapsed time exceeds the short GOLEM_SIDE_TIMEOUT_S
    (4.0s), as long as it's still making progress, up to the GOLEM_SIDE_MAX_WAIT_S cap (15s).
    Progress here climbs by 1 every 3 polls (0.6s), reaching 10/done at poll 30 (~6.0s elapsed) --
    past the original 4.0s timeout, comfortably under the 15s hard cap, and only reachable at all
    if the extension logic is actually firing."""
    calls = {"n": 0}

    def fake_get_golem():
        calls["n"] += 1
        progress = min(calls["n"] // 3, 10)
        done = progress >= 10
        station = _station("north", done=("east",) if done else ())
        station["sides"]["east"]["progress"] = progress
        return {"stations": [station]}

    monkeypatch.setattr(controller, "get_golem", fake_get_golem)
    result = gc._await_side_done("north", "east", gc.GOLEM_SIDE_TIMEOUT_S, already_done=False)
    assert result is True
    assert fake_clock["t"] - 1_000_000.0 > gc.GOLEM_SIDE_TIMEOUT_S, \
        "test is only meaningful if it actually ran past the un-extended timeout"


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
        lambda ser, entity, option, target_hint=None, offset=(0, 0): click_calls.append(entity) or True,
    )
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    gc.craft_phase(ctx, gc.DEFAULT_CONFIG)

    assert len(click_calls) == gc.GOLEM_GEM_PICKUP_ATTEMPTS, "must retry up to the attempt cap, not give up after one click"
    assert ctx.state.data["gc_gem_bag_full"] is False, "must still clear the flag once retries are exhausted, to avoid spinning forever on a lost cause"
    assert ctx.state.data["gc_phase"] == "bank"


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

    def spy_walk(ctx, x, y, plane):
        walk_calls.append((x, y, plane))
        return real_walk(ctx, x, y, plane)

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
