"""Tests for travel.py's Shortest Path handshake.

Everything here is about the handshake itself -- correlating a result with the request that
asked for it, deciding when the pathfinder is worth asking at all, what transport policy goes
out with the request, and refusing to walk through a transport edge we physically cannot use.
The walking machinery underneath (_hop_to, click_entity, the Arduino) is stubbed out; none of
these tests touch a bridge or a serial port.
"""

import os
import sys

import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import travel


TARGET = {"x": 3200, "y": 3200, "plane": 0}


def _result(done=True, steps=None, targets=None, reached=True, reason="TARGET_REACHED",
            active=True, start=None):
    """A /shortest-path/result payload shaped the way ShortestPathProvider returns it."""
    data = {
        "active": active,
        "done": done,
        "steps": steps if steps is not None else [{"x": 3100, "y": 3100, "plane": 0}],
        "reached": reached,
        "terminationReason": reason,
    }
    if targets is not None:
        data["targets"] = targets
    if start is not None:
        data["start"] = start
    return data


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr(travel.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def no_live_bridge(request, monkeypatch):
    """Keep the suite hermetic. Both of these reach the RUNNING CLIENT over HTTP when a test
    does not stub them, so results depended on where the character happened to be standing --
    MEASURED 2026-08-29: two door tests passed alone and failed in a full run, and the same test
    flipped between runs as the live player moved between houses.

    The defaults are the documented "client reports nothing" answers, which are also the safe
    ones: _routable_goal returns its goal untouched, and _request_computed_path returns no route.
    Tests that want either behaviour override them, exactly as several already do."""
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=2: [])
    if request.node.get_closest_marker("real_bridge_helper"):
        return                     # this test exercises the controller helper itself
    monkeypatch.setattr(controller, "request_shortest_path",
                        lambda x, y, plane, config=None: {"requested": False})
    monkeypatch.setattr(controller, "get_shortest_path_result",
                        lambda: {"active": False, "done": False, "steps": []})


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = type("S", (), {"data": {}})()


# --- transport policy ---------------------------------------------------------------

def test_baseline_policy_disables_every_transport_we_cannot_execute():
    """The whole point of sending an explicit baseline: a teleport edge in the route is one
    travel.py can't click, so it must never be enabled by inheriting the user's own Shortest
    Path panel settings."""
    config = travel.transport_config()
    assert config["useTeleportationItems"] == "None"
    for key in ("useTeleportationSpells", "useTeleportationSpellsHome", "useFairyRings",
                "useSpiritTrees", "useGnomeGliders", "useHotAirBalloons", "useMagicCarpets",
                "useMagicMushtrees", "useTeleportationLevers", "useTeleportationPortals",
                "useTeleportationMinigames", "useWildernessObelisks", "useQuetzals"):
        assert config[key] is False, f"{key} should be off in the baseline"


def test_baseline_policy_disables_the_transports_that_need_a_destination_ui():
    """Canoes, charter ships and minecarts are clickable objects but not one-click ones -- each
    opens a destination picker _use_transport has no idea how to drive (see its own docstring).
    Being an object is not the same as being executable."""
    config = travel.transport_config()
    for key in ("useCanoes", "useCharterShips", "useMinecarts"):
        assert config[key] is False, f"{key} needs a destination UI, so it can't be in the baseline"


def test_baseline_policy_enables_the_object_transports_we_can_click():
    """Doors, ladders, stairs and boat captains are ordinary single menu clicks -- _use_transport
    already handles them, so disabling them would throw away working routes."""
    config = travel.transport_config()
    for key in ("useBoats", "useShips", "useAgilityShortcuts", "useGrappleShortcuts"):
        assert config[key] is True, f"{key} should be on in the baseline"


def test_per_call_overrides_win_over_the_baseline():
    config = travel.transport_config({"useFairyRings": True})
    assert config["useFairyRings"] is True
    assert config["useTeleportationItems"] == "None"  # untouched keys keep the baseline


def test_transport_config_never_mutates_the_baseline():
    """A leaked mutation here would silently change the policy for every later walk in the
    process, which is exactly the stickiness problem the explicit map exists to avoid."""
    travel.transport_config({"useBoats": False})
    assert travel.TRANSPORT_POLICY["useBoats"] is True


def test_an_unknown_override_key_is_rejected():
    """Shortest Path ignores config keys it doesn't recognise, so a typo would silently leave
    the baseline in force while the caller believed it had opted in."""
    with pytest.raises(ValueError, match="useTeleportItems"):
        travel.transport_config({"useTeleportItems": "Inventory"})


def test_every_baseline_key_is_a_real_shortest_path_config_key():
    """Guards the baseline itself against the same typo -- a bogus key is silently dropped by
    the plugin, so nothing would fail loudly at runtime."""
    for key in travel.TRANSPORT_POLICY:
        assert key in travel.SHORTEST_PATH_CONFIG_KEYS


# --- distance gate ------------------------------------------------------------------

def test_a_short_same_plane_hop_skips_the_pathfinder(monkeypatch):
    """Asking the plugin costs a message plus up to PATH_REQUEST_TIMEOUT_S of polling; for a
    20-tile walk that dwarfs the walk itself."""
    monkeypatch.setattr(travel.controller, "get_player",
                        lambda: {"x": 3190, "y": 3195, "plane": 0})
    monkeypatch.setattr(travel, "_walk_greedy", lambda ctx, target, tolerance: True)

    def _boom(*_a, **_k):
        raise AssertionError("should not have asked Shortest Path for a 12-tile hop")

    monkeypatch.setattr(travel, "_request_computed_path", _boom)

    assert travel.walk_to(_Ctx(), TARGET) is True


def test_a_long_walk_still_asks_the_pathfinder(monkeypatch):
    monkeypatch.setattr(travel.controller, "get_player",
                        lambda: {"x": 2500, "y": 3400, "plane": 0})
    asked = []
    monkeypatch.setattr(travel, "_request_computed_path",
                        lambda target, config: asked.append(target) or (None, False, None))
    monkeypatch.setattr(travel, "_walk_greedy", lambda ctx, target, tolerance: True)

    travel.walk_to(_Ctx(), TARGET)

    assert asked == [TARGET]


def test_a_short_hop_to_another_plane_still_asks_the_pathfinder(monkeypatch):
    """Straight-line distance is meaningless across planes -- getting upstairs needs the
    staircase transport the route knows about."""
    monkeypatch.setattr(travel.controller, "get_player",
                        lambda: {"x": 3198, "y": 3198, "plane": 1})
    asked = []
    monkeypatch.setattr(travel, "_request_computed_path",
                        lambda target, config: asked.append(target) or (None, False, None))
    monkeypatch.setattr(travel, "_walk_greedy", lambda ctx, target, tolerance: True)

    travel.walk_to(_Ctx(), TARGET)

    assert asked == [TARGET]


def test_the_distance_gate_threshold_is_overridable_per_call(monkeypatch):
    monkeypatch.setattr(travel.controller, "get_player",
                        lambda: {"x": 3190, "y": 3195, "plane": 0})
    asked = []
    monkeypatch.setattr(travel, "_request_computed_path",
                        lambda target, config: asked.append(target) or (None, False, None))
    monkeypatch.setattr(travel, "_walk_greedy", lambda ctx, target, tolerance: True)

    travel.walk_to(_Ctx(), TARGET, min_path_tiles=5)

    assert asked == [TARGET]


# --- request/result correlation -----------------------------------------------------

def test_a_finished_path_to_a_different_target_is_not_mistaken_for_ours(monkeypatch):
    """The plugin swaps in a new Pathfinder asynchronously, so the first poll after our request
    can still be reading the PREVIOUS walk's finished path. Without the target check we would
    happily follow a route to wherever the last routine was going."""
    stale = _result(steps=[{"x": 1, "y": 1, "plane": 0}],
                    targets=[{"x": 2999, "y": 3999, "plane": 0}])
    ours = _result(steps=[{"x": 3100, "y": 3100, "plane": 0}], targets=[TARGET])
    monkeypatch.setattr(travel.controller, "request_shortest_path",
                        lambda x, y, plane, config=None: {"requested": True})
    monkeypatch.setattr(travel.controller, "get_shortest_path_result",
                        _sequence([stale, stale, ours]))

    steps, reached, _reason = travel._request_computed_path(TARGET, travel.transport_config())

    assert steps == [{"x": 3100, "y": 3100, "plane": 0}]
    assert reached is True


def test_a_stale_result_that_never_resolves_times_out_rather_than_being_followed(monkeypatch):
    stale = _result(targets=[{"x": 2999, "y": 3999, "plane": 0}])
    monkeypatch.setattr(travel.controller, "request_shortest_path",
                        lambda x, y, plane, config=None: {"requested": True})
    monkeypatch.setattr(travel.controller, "get_shortest_path_result", lambda: stale)

    steps, _reached, _reason = travel._request_computed_path(TARGET, travel.transport_config())

    assert steps is None


def test_a_result_without_target_info_is_accepted_as_before(monkeypatch):
    """An older Shortest Path build has no getTargets() for the provider to echo. Degrade to
    the old done-is-enough behaviour rather than refusing to path at all."""
    monkeypatch.setattr(travel.controller, "request_shortest_path",
                        lambda x, y, plane, config=None: {"requested": True})
    monkeypatch.setattr(travel.controller, "get_shortest_path_result", lambda: _result())

    steps, _reached, _reason = travel._request_computed_path(TARGET, travel.transport_config())

    assert steps == [{"x": 3100, "y": 3100, "plane": 0}]


def test_the_transport_config_is_sent_with_the_request(monkeypatch):
    sent = {}
    monkeypatch.setattr(travel.controller, "request_shortest_path",
                        lambda x, y, plane, config=None: sent.update(config or {}) or {"requested": True})
    monkeypatch.setattr(travel.controller, "get_shortest_path_result",
                        lambda: _result(targets=[TARGET]))

    travel._request_computed_path(TARGET, travel.transport_config({"useFairyRings": True}))

    assert sent["useFairyRings"] is True
    assert sent["useTeleportationItems"] == "None"


def test_an_unreachable_target_reports_why(monkeypatch):
    """A route that stops short is followed today as if it had arrived. Surfacing the plugin's
    own termination reason is what makes that visible in the log."""
    monkeypatch.setattr(travel.controller, "request_shortest_path",
                        lambda x, y, plane, config=None: {"requested": True})
    monkeypatch.setattr(travel.controller, "get_shortest_path_result",
                        lambda: _result(targets=[TARGET], reached=False, reason="SEARCH_EXHAUSTED"))

    steps, reached, reason = travel._request_computed_path(TARGET, travel.transport_config())

    assert steps  # still walked -- getting closer beats not moving
    assert reached is False
    assert reason == "SEARCH_EXHAUSTED"


def test_no_pathfinder_running_gives_up_immediately(monkeypatch):
    monkeypatch.setattr(travel.controller, "request_shortest_path",
                        lambda x, y, plane, config=None: {"requested": True})
    monkeypatch.setattr(travel.controller, "get_shortest_path_result",
                        lambda: {"active": False, "done": False, "steps": []})

    steps, _reached, _reason = travel._request_computed_path(TARGET, travel.transport_config())

    assert steps is None


def _sequence(results):
    """Returns a zero-arg callable yielding each result in turn, then repeating the last."""
    remaining = list(results)

    def _next():
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return _next


# --- unexecutable transport guard ---------------------------------------------------

def test_a_teleport_edge_is_recognised_as_unexecutable():
    assert travel._transport_is_executable(
        {"type": "TELEPORTATION_ITEM", "isTeleport": True, "displayInfo": "Varrock teleport"}
    ) is False


def test_an_object_edge_is_recognised_as_executable():
    assert travel._transport_is_executable(
        {"type": "TRANSPORT", "isTeleport": False, "objectId": 9398,
         "objectName": "Door", "objectOption": "Open"}
    ) is True


def test_walking_a_route_refuses_to_hop_across_an_unusable_teleport(monkeypatch):
    """Before the guard, an unusable transport fell through to _hop_to on the tile PAST it --
    for a teleport that is a minimap click at a destination on the other side of the map."""
    steps = [{"x": 3000 + i, "y": 3000, "plane": 0} for i in range(20)]
    steps[12] = {"x": 2440, "y": 3090, "plane": 0,
                 "transport": {"type": "TELEPORTATION_ITEM", "isTeleport": True,
                               "displayInfo": "Falador teleport"}}
    monkeypatch.setattr(travel.controller, "get_player",
                        lambda: {"x": 3000, "y": 3000, "plane": 0})
    hops = []
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane: hops.append((x, y)) or True)

    assert travel._walk_along_path(_Ctx(), steps, TARGET) is False
    assert (2440, 3090) not in hops


def test_walking_a_route_still_walks_through_an_object_transport_it_failed_to_click(monkeypatch):
    """A door we couldn't find on screen is a different case from a teleport: walking into it
    is a legitimate way to get through, so the old fallback stays for object transports."""
    steps = [{"x": 3000 + i, "y": 3000, "plane": 0} for i in range(20)]
    steps[12] = {"x": 3012, "y": 3000, "plane": 0,
                 "transport": {"type": "TRANSPORT", "isTeleport": False, "objectId": 9398,
                               "objectName": "Door", "objectOption": "Open"}}
    monkeypatch.setattr(travel.controller, "get_player",
                        lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(travel, "_use_transport", lambda ctx, transport, waypoint: False)
    hops = []
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, plane: hops.append((x, y)) or True)

    travel._walk_along_path(_Ctx(), steps, TARGET)

    assert (3012, 3000) in hops


# --- controller bridge helper -------------------------------------------------------

@pytest.mark.real_bridge_helper
def test_request_shortest_path_prefixes_config_keys(monkeypatch):
    """ShortestPathProvider reads config overrides off cfg.-prefixed query params so they can't
    collide with x/y/plane."""
    seen = {}
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None, **kw: seen.update(params or {}) or {"requested": True})

    controller.request_shortest_path(3200, 3200, 0, config={"useFairyRings": True,
                                                            "useTeleportationItems": "None"})

    assert seen["x"] == 3200
    assert seen["cfg.useFairyRings"] == "true"
    assert seen["cfg.useTeleportationItems"] == "None"


@pytest.mark.real_bridge_helper
def test_request_shortest_path_without_config_sends_no_cfg_params(monkeypatch):
    seen = {}
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None, **kw: seen.update(params or {}) or {"requested": True})

    controller.request_shortest_path(3200, 3200, 0)

    assert not [k for k in seen if k.startswith("cfg.")]


# --- walk_to_exact_tile's non-blocking mode (2026-08-23) -----------------------------
#
# Added so a routine can click its target while still walking, which is what a human does --
# MEASURED at 0.6-1.0s between the walk click and the object click, four times out of four, in
# recordings/2026-08-23_194302_golem-carving. Blocking for exact arrival first is safe but
# structurally slower than a human, and the block is what makes an over-tight confirmation
# timeout destructive rather than merely slow. See CLAUDE.md's "Sub-tick data and clicking while
# moving".


def _stub_walk_click(monkeypatch, player_tile, arrival_tile=None):
    """Stubs the bridge/serial calls walk_to_exact_tile makes. Returns the get_player call log;
    `arrival_tile` (if given) is what get_player reports from the 2nd call on, simulating arrival."""
    calls = []
    seq = {"n": 0}

    def fake_get_player():
        seq["n"] += 1
        calls.append(seq["n"])
        if arrival_tile is not None and seq["n"] > 1:
            return dict(arrival_tile)
        return dict(player_tile)

    monkeypatch.setattr(controller, "get_player", fake_get_player)
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": True, "screenX": 100, "screenY": 100})
    monkeypatch.setattr(controller, "get_ground_walk_screen_pos", lambda x, y, p: (100, 100))
    monkeypatch.setattr(controller, "key_down", lambda ser, k: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(travel.time, "sleep",
                        lambda d: pytest.fail(f"non-blocking walk must not sleep (slept {d}s)"))
    return calls


class _Ctx:
    """Minimal stand-in for controller.Context -- walk_to_exact_tile only ever reads ctx.ser."""
    ser = None


def test_walk_to_exact_tile_can_fire_and_forget_without_waiting_for_arrival(monkeypatch):
    """The whole point: return as soon as the click is out, so the caller can issue its target
    click while the player is still walking."""
    calls = _stub_walk_click(monkeypatch, {"x": 10, "y": 10, "plane": 0})

    assert travel.walk_to_exact_tile(_Ctx(), 12, 12, 0, wait_for_arrival=False) is True
    assert len(calls) == 1, \
        "only the already-standing-there pre-check may read the player; no arrival poll"


def test_the_non_blocking_walk_still_early_outs_when_already_standing_there(monkeypatch):
    """The pre-check is the one get_player read that must survive -- clicking a walk to the tile
    you are already on is wasted motion whether or not the caller intends to block."""
    clicks = []
    _stub_walk_click(monkeypatch, {"x": 12, "y": 12, "plane": 0})
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicks.append(e) or True)

    assert travel.walk_to_exact_tile(None, 12, 12, 0, wait_for_arrival=False) is True
    assert clicks == [], "no click may go out when the player is already on the target tile"


def test_the_non_blocking_walk_still_reports_an_unprojectable_tile(monkeypatch):
    """False still means "can't be clicked at all", which is what the caller's miss counter and
    hop-closer fallback key off -- that contract must not change with the new mode."""
    _stub_walk_click(monkeypatch, {"x": 10, "y": 10, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": False})

    assert travel.walk_to_exact_tile(None, 12, 12, 0, wait_for_arrival=False) is False


def test_walk_to_exact_tile_uses_the_ground_tile_refresh_target_factory(monkeypatch):
    """Regression: walk_to_exact_tile used to hand-roll its own lambda duplicating
    ground_tile_refresh_target's body instead of calling the factory. Assert the factory itself
    gets called so a future edit can't silently reintroduce the duplicate."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                         lambda x, y, plane: {"onScreen": True})
    monkeypatch.setattr(controller, "key_down", lambda ser, key: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, key: None)

    factory_calls = []
    real_factory = controller.ground_tile_refresh_target
    def _spy_factory(x, y, plane):
        factory_calls.append((x, y, plane))
        return real_factory(x, y, plane)
    monkeypatch.setattr(controller, "ground_tile_refresh_target", _spy_factory)

    click_kwargs = []
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, e, **kw: click_kwargs.append(kw) or True)

    travel.walk_to_exact_tile(_Ctx(), 12, 12, 0, wait_for_arrival=False)
    assert factory_calls == [(12, 12, 0)]
    assert click_kwargs and click_kwargs[0].get("refresh_target") is not None


# --- closed doors -------------------------------------------------------------------
#
# ROOT CAUSE these cover (verified 2026-08-28 against both the human recordings and the live
# client): Shortest Path's precomputed collision map marks ordinary door tiles as PASSABLE --
# decoding collision-map.zip at the nine door tiles the Mahogany Homes recordings show a human
# opening found seven fully passable on every edge, including Falador's (3037,3347), read live
# while the player stood beside that very door. So the pathfinder routes straight through a shut
# door, and none of those door ids appear in transports.tsv either, so no transport step is
# emitted for _use_transport to click. The walker then hop-clicks past the door, the player wedges
# against it, and nothing in the stack ever clicks "Open".

def _door(x, y, actions=("Open",), name="Door", object_type="WallObject", distance=1):
    obj = {"id": 24050, "name": name, "objectType": object_type,
           "x": x, "y": y, "plane": 0, "distance": distance,
           "screenX": 100, "screenY": 100}
    if actions is not None:
        obj["actions"] = list(actions)
    return obj


def test_finds_a_closed_door_standing_between_the_player_and_the_target(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3037, 3347)])
    door = travel._find_blocking_door({"x": 3037, "y": 3360, "plane": 0})
    assert door is not None and (door["x"], door["y"]) == (3037, 3347)


def test_an_already_open_door_is_never_touched(monkeypatch):
    """The safety property. An open door's only action is "Close", and clicking it would shut the
    door we just came through -- turning a working walk into a wedged one. Distinguishing the two
    is the whole reason ObjectProvider gained an `actions` field: a closed door and an open one
    are both WallObjects named "Door" (Falador 24050 closed / 24051 open), so neither `name` nor
    `id` can tell them apart without a hardcoded per-door id table."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3037, 3347, actions=("Close",))])
    assert travel._find_blocking_door({"x": 3037, "y": 3360, "plane": 0}) is None


def test_a_door_behind_us_is_ignored(monkeypatch):
    """Opening a door that leads away from the target is pure noise, and would let the recovery
    ping-pong between two doors instead of making progress."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3037, 3343)])
    assert travel._find_blocking_door({"x": 3037, "y": 3360, "plane": 0}) is None


def test_a_door_on_another_plane_is_ignored(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    upstairs = _door(3037, 3347)
    upstairs["plane"] = 1
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [upstairs])
    assert travel._find_blocking_door({"x": 3037, "y": 3360, "plane": 0}) is None


def test_no_actions_field_means_no_door_click(monkeypatch):
    """Against a client that hasn't been rebuilt, /objects carries no `actions` at all. Guessing
    from the name alone would left-click open doors shut, so an absent field must read as "can't
    tell" and do nothing -- the routine is then exactly as stuck as it is today, never worse."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3037, 3347, actions=None)])
    assert travel._find_blocking_door({"x": 3037, "y": 3360, "plane": 0}) is None


def test_a_non_door_with_an_open_action_is_ignored(monkeypatch):
    """Chests, cupboards and drawers all offer "Open" and are all over a Mahogany Homes house --
    the live scan beside the Falador door returned Drawers and a Cupboard within 7 tiles. Opening
    one wastes an action and never unblocks anything."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_door(3037, 3347, name="Drawers", object_type="GameObject")])
    assert travel._find_blocking_door({"x": 3037, "y": 3360, "plane": 0}) is None


def test_greedy_walk_opens_a_door_when_a_hop_makes_no_headway(monkeypatch):
    monkeypatch.setattr(travel, "_request_computed_path",
                        lambda target, config: ([{"x": 3037, "y": 3347, "plane": 0}], True, "TARGET_REACHED"))
    """End to end: the player never moves (wedged against the shut door), so a later pass through
    the loop must stop re-clicking the same hop and click the door instead."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3037, 3347)])
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, p: True)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.append(entity) or True)

    travel._walk_greedy(_Ctx(), {"x": 3037, "y": 3360, "plane": 0})

    assert clicked, "a stalled greedy walk never tried to open the blocking door"
    assert (clicked[0]["x"], clicked[0]["y"]) == (3037, 3347)


def test_a_door_on_the_players_own_tile_still_counts(monkeypatch):
    """A WallObject reports the tile it is anchored to, so the door a player is pressed right up
    against is often reported on the player's own tile -- tying the player's own distance to the
    goal exactly. Excluding ties would blind the recovery to the likeliest blocker."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3037, 3345)])
    assert travel._find_blocking_door({"x": 3037, "y": 3360, "plane": 0}) is not None


def test_computed_route_also_opens_a_door_when_the_player_stops_moving(monkeypatch):
    """A route can run straight through a shut door with no transport step attached, because the
    collision map thinks the tile is passable. `index` marches on regardless of whether the player
    moved, so a stalled route walk must recover the same way the greedy walk does."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3037, 3347)])
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, p: True)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.append(entity) or True)

    steps = [{"x": 3037, "y": 3345 + i, "plane": 0} for i in range(20)]
    travel._walk_along_path(_Ctx(), steps, {"x": 3037, "y": 3364, "plane": 0})

    assert clicked, "a stalled route walk never tried to open the blocking door"
    assert (clicked[0]["x"], clicked[0]["y"]) == (3037, 3347)


def test_door_opening_is_capped_so_a_wedge_that_is_not_a_door_still_fails(monkeypatch):
    monkeypatch.setattr(travel, "_request_computed_path",
                        lambda target, config: ([{"x": 3037, "y": 3347, "plane": 0}], True, "TARGET_REACHED"))
    """If the obstruction isn't a door, the walk must still give up rather than wander a building
    opening every door it can reach. The cap is what bounds that."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3037, 3347)])
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, p: True)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.append(entity) or True)

    assert travel._walk_greedy(_Ctx(), {"x": 3037, "y": 3360, "plane": 0}) is False
    assert len(clicked) == travel.DOOR_MAX_OPENS_PER_WALK


# --- hop distance -------------------------------------------------------------------

def test_hop_distance_stays_inside_the_measured_minimap_range():
    """MEASURED live 2026-08-28 from (3037,3345): /minimap/walk reported onMinimap out to 28
    tiles in all eight directions, failing first on the DIAGONALS at 30 (a corner is sqrt(2)x
    further from the centre than a cardinal at the same tile distance). So the safe bound for a
    hop that must work in any direction is 28, and the max must keep real margin under it -- the
    minimap's reach shrinks if the player zooms it in."""
    assert travel.WALK_HOP_MAX_TILES <= 24
    assert travel.WALK_HOP_MIN_TILES < travel.WALK_HOP_MAX_TILES


def test_an_unreachable_hop_point_is_pulled_in_rather_than_abandoning_the_walk(monkeypatch):
    """Raising WALK_HOP_MAX_TILES makes every walk lean harder on the minimap's reach, which is a
    user setting we don't control. Before, a point off both screen and minimap aborted the whole
    walk. Pulling the aim in toward the player and retrying costs one poll and keeps the walk
    alive, so a zoomed-in minimap degrades to shorter hops instead of a failed trip."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": False})
    seen = []

    def minimap(x, y, p):
        seen.append((x, y))
        # Only points within 12 tiles of the player are on this (zoomed-in) minimap.
        near = max(abs(x - 3000), abs(y - 3000)) <= 12
        return {"onMinimap": near, "screenX": 50, "screenY": 50}

    monkeypatch.setattr(controller, "get_minimap_walk_point", minimap)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)

    assert travel._hop_to(_Ctx(), 3024, 3000, 0) is True
    assert len(seen) > 1, "gave up on the first off-minimap point instead of pulling it in"
    assert max(abs(seen[-1][0] - 3000), abs(seen[-1][1] - 3000)) <= 12


def test_a_hop_point_that_is_unreachable_at_every_distance_still_gives_up(monkeypatch):
    """The pull-in must stay bounded -- an unreachable target has to fail, not loop."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point", lambda x, y, p: {"onScreen": False})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: {"onMinimap": False})
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)

    assert travel._hop_to(_Ctx(), 3024, 3000, 0) is False


# --- doors found via the ROUTE, not by distance ---------------------------------------
#
# LIVE RUN 2026-08-28. Stuck at (1771,3622) outside Mariah's house, 2 tiles from the Table
# hotspot at (1769,3622) but on the far side of a wall. Three closed doors were within 6 tiles,
# and _find_blocking_door rejected ALL of them: its rule was "the door must be no further from
# the goal than the player is", and when you are walled off two tiles from your goal, the door
# that actually gets you there is FURTHER away in a straight line, not nearer.
#
# The pathfinder already knows the answer. Shortest Path routed (1771,3622) -> (1766,3621) in 12
# steps straight THROUGH the door tile (1765,3624) -- carrying no transport, because its
# collision map believes the door is passable. So the door to open is the closed door standing on
# the route, and no distance heuristic is needed or wanted.

def test_a_door_on_the_route_is_found_even_though_it_is_further_from_the_goal(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1771, "y": 3622, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _door(1775, 3618), _door(1765, 3618), _door(1765, 3624),
    ])
    route = [{"x": 1771, "y": 3623, "plane": 0}, {"x": 1771, "y": 3624, "plane": 0},
             {"x": 1768, "y": 3624, "plane": 0}, {"x": 1765, "y": 3624, "plane": 0}]
    goal = {"x": 1766, "y": 3621, "plane": 0}

    door = travel._find_blocking_door(goal, route=route)
    assert door is not None, "the route ran through a closed door and it was not found"
    assert (door["x"], door["y"]) == (1765, 3624)


def test_a_route_door_that_is_already_open_is_still_skipped(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1771, "y": 3622, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_door(1765, 3624, actions=("Close",))])
    route = [{"x": 1765, "y": 3624, "plane": 0}]
    assert travel._find_blocking_door({"x": 1766, "y": 3621, "plane": 0}, route=route) is None


def test_a_closed_door_nowhere_near_the_route_is_ignored(monkeypatch):
    """The route is what makes the wider search radius safe -- without this the bigger radius
    would just open whatever door happened to be lying around."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1771, "y": 3622, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(1779, 3630)])
    route = [{"x": 1771, "y": 3623, "plane": 0}, {"x": 1765, "y": 3624, "plane": 0}]
    assert travel._find_blocking_door({"x": 1766, "y": 3621, "plane": 0}, route=route) is None


def test_a_stalled_greedy_walk_asks_for_a_route_to_locate_the_door(monkeypatch):
    """The failing walk was only 5 tiles, so walk_to went straight to _walk_greedy and there was
    no route to consult. On a stall it has to ask for one -- that is what turns an unreachable
    straight line into a door it can open."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1771, "y": 3622, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(1765, 3624)])
    monkeypatch.setattr(travel, "_hop_to", lambda ctx, x, y, p: True)
    asked = []
    monkeypatch.setattr(travel, "_request_computed_path",
                        lambda target, config: asked.append(target) or (
                            [{"x": 1765, "y": 3624, "plane": 0}], True, "TARGET_REACHED"))
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.append(entity) or True)

    travel._walk_greedy(_Ctx(), {"x": 1766, "y": 3621, "plane": 0})

    assert asked, "stalled greedy walk never asked the pathfinder where the door was"
    assert clicked and (clicked[0]["x"], clicked[0]["y"]) == (1765, 3624)


# --- the client's own collision entry decides which door is shut ----------------------
#
# LIVE 2026-08-28 at Barbara's house, Hosidius: stalled at (1750,3536) with underRoof false, one
# tile from the Broken Grandfather Clock at (1749,3536) but walled off. Two shut doors were in
# range -- Door 7452 at (1748,3537) and at (1746,3534), both reporting actions ['Open'] -- and the
# routeless fallback rejected BOTH, because its rule is "no further from the goal than the player"
# and the goal (1750,3534) is 2 tiles away while the doors are 3 and 4. Same wrong rule that the
# route branch was built to escape; the route just was not available that tick.
#
# Per the user: the game already knows. ObjectProvider now reports `collisionFlags` -- the client's
# own CollisionData entry for the object's tile, carrying both the movement bits and the
# line-of-sight bits. A shut door blocks its edge; an open one does not. That is direct evidence
# rather than a distance guess, and it also says WHICH of several doors is actually in the way.

def _door_with_flags(x, y, flags, actions=("Open",)):
    door = _door(x, y, actions=actions)
    if flags is not None:
        door["collisionFlags"] = flags
    return door


def test_routeless_fallback_opens_the_door_across_our_path(monkeypatch):
    """Barbara's outer doorway, exactly as measured: standing outside at (1744,3534) with the
    goal inside at (1750,3534), shut Door 7452 at (1746,3534) reading MOVEMENT_EAST|LOS_EAST.
    The old rule ("no further from the goal than the player") is gone entirely -- what selects
    this door is that its blocked edge, the 1746|1747 crossing, lies between us and the goal."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1744, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _door_with_flags(1746, 3534,
                         travel.BLOCK_MOVEMENT_EAST | travel.BLOCK_LINE_OF_SIGHT_EAST),
    ])

    door = travel._find_blocking_door({"x": 1750, "y": 3534, "plane": 0})
    assert door is not None and (door["x"], door["y"]) == (1746, 3534)


def test_a_shut_door_obstructing_nothing_is_not_the_one_in_the_way(monkeypatch):
    # Flags of 0 mean that tile blocks no edge, so whatever else this door is, it is not what is
    # stopping us. NOTE this is NOT an open/shut test -- see the next test for why it cannot be.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1750, "y": 3536, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door_with_flags(1748, 3537, 0)])
    assert travel._find_blocking_door({"x": 1750, "y": 3534, "plane": 0}) is None


def test_an_open_doors_leaf_still_carries_flags_and_is_still_skipped(monkeypatch):
    """An open door is NOT flag-free: MEASURED live 2026-08-28 by opening Barbara's outer door,
    the leaf comes to rest blocking a perpendicular edge (Door 7453 at (1747,3534), flags 16416 =
    MOVEMENT_SOUTH|LOS_SOUTH) even though the doorway it vacated went to 0. So "has flags" must
    never be read as "shut" -- `actions` decides that, and the edge test decides relevance.

    (An earlier pass compared a shut door at Lumbridge against a DIFFERENT open one, saw both
    reading 1026, and wrongly concluded the flags carried no state at all. Comparing the same
    doorway before and after is what settled it.)"""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3233, "y": 3205, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _door_with_flags(3233, 3203, 1026, actions=("Close",)),
    ])
    assert travel._find_blocking_door({"x": 3233, "y": 3201, "plane": 0}) is None


def test_a_floor_decoration_is_not_treated_as_an_obstruction():
    """BLOCK_MOVEMENT_FULL is a COMPOSITE constant (2359552 = FLOOR | FLOOR_DECORATION | OBJECT),
    so including it in the mask made a bare floor decoration read as blocking -- caught live on
    Lumbridge's Trapdoor, flags 262144, a hole in the floor that blocks no edge at all."""
    assert travel._door_blocks({"collisionFlags": 262144}) is False
    assert travel._door_blocks({"collisionFlags": 2097152}) is False
    assert travel._door_blocks({"collisionFlags": travel.BLOCK_MOVEMENT_NORTH}) is True
    assert travel._door_blocks({}) is None


def test_the_nearest_separating_door_wins_when_several_cross_our_path(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1744, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _door_with_flags(1745, 3534, travel.BLOCK_MOVEMENT_EAST),   # crossing 1745|1746
        _door_with_flags(1748, 3534, travel.BLOCK_MOVEMENT_EAST),   # crossing 1748|1749
    ])

    door = travel._find_blocking_door({"x": 1750, "y": 3534, "plane": 0})
    assert (door["x"], door["y"]) == (1745, 3534), "open the one we reach first"


def test_a_route_door_must_also_be_blocking_when_the_client_reports_flags(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1771, "y": 3622, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_door_with_flags(1765, 3624, 0)])
    route = [{"x": 1765, "y": 3624, "plane": 0}]
    assert travel._find_blocking_door({"x": 1766, "y": 3621, "plane": 0}, route=route) is None


def test_an_unrebuilt_client_keeps_the_old_conservative_distance_rule(monkeypatch):
    """No collisionFlags field at all means "cannot tell" -- fall back to what shipped, so an
    un-rebuilt client behaves exactly as before and never worse."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3037, "y": 3345, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_door(3037, 3347)])
    door = travel._find_blocking_door({"x": 3037, "y": 3360, "plane": 0})
    assert door is not None and (door["x"], door["y"]) == (3037, 3347)


# --- which EDGE a shut door blocks, and whether it separates us from the goal ----------
#
# MEASURED live 2026-08-28 at Barbara's house, Hosidius, standing at (1744,3534) with the goal
# inside at (1750,3534):
#
#   Door 7452 (1746,3534) SHUT  flags 4104  = MOVEMENT_EAST|LOS_EAST
#     its east neighbour (1747,3534) flags 65664 = MOVEMENT_WEST|LOS_WEST   <- reciprocal
#   Door 7452 (1748,3537) SHUT  flags 16416 = MOVEMENT_SOUTH|LOS_SOUTH
#     its south neighbour (1748,3536) flags 1026 = MOVEMENT_NORTH|LOS_NORTH <- reciprocal
#
# So a blocked edge is recorded on BOTH tiles as opposite directions -- it is a real edge, not a
# property of a tile -- and movement and line of sight are always set together for that same edge.
# The wall segments either side of the first door read 4104 too, which is why collision can never
# IDENTIFY a door (that is what `actions` is for); what it can do is say which edge a known-shut
# door blocks, and therefore whether that door stands between us and where we are going.

def test_the_door_whose_blocked_edge_separates_us_from_the_goal_is_chosen(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1744, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        # blocks SOUTH, but player and goal are both south of it -- crosses nothing
        _door_with_flags(1748, 3537, travel.BLOCK_MOVEMENT_SOUTH | travel.BLOCK_LINE_OF_SIGHT_SOUTH),
        # blocks EAST, and the goal is east of it while we are west -- this is the doorway
        _door_with_flags(1746, 3534, travel.BLOCK_MOVEMENT_EAST | travel.BLOCK_LINE_OF_SIGHT_EAST),
    ])

    door = travel._find_blocking_door({"x": 1750, "y": 3534, "plane": 0})

    assert door is not None
    assert (door["x"], door["y"]) == (1746, 3534), \
        "must pick the door whose blocked edge actually lies between us and the goal"


def test_a_door_blocking_an_edge_we_never_have_to_cross_is_not_chosen(monkeypatch):
    """VERIFIED live 2026-08-28: with Barbara's outer door open and the way in genuinely clear,
    the old "fall back to the nearest door that obstructs anything" rule still returned the INNER
    door 3 tiles away. That is a wasted click AND a True return, which resets the walker's stall
    tracking and hides why it is really stuck. When the client has told us about collision and
    nothing crosses our path, the honest answer is None."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1744, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _door_with_flags(1748, 3537, travel.BLOCK_MOVEMENT_SOUTH),
    ])
    assert travel._door_separates(
        {"x": 1748, "y": 3537, "collisionFlags": travel.BLOCK_MOVEMENT_SOUTH},
        1744, 3534, 1750, 3534) is False
    assert travel._find_blocking_door({"x": 1750, "y": 3534, "plane": 0}) is None


def test_a_blocked_edge_is_recognised_from_either_side(monkeypatch):
    # The edge is symmetric, so approaching the same doorway from the far side must still match.
    door = {"x": 1746, "y": 3534, "collisionFlags": travel.BLOCK_MOVEMENT_EAST}
    assert travel._door_separates(door, 1744, 3534, 1750, 3534) is True   # west -> east
    assert travel._door_separates(door, 1750, 3534, 1744, 3534) is True   # east -> west
    assert travel._door_separates(door, 1744, 3534, 1745, 3534) is False  # both west of it


# --- the edge must be one we actually cross, not merely one on the right axis ----------
#
# MEASURED live 2026-08-29 outside NORMAN's house, Falador. Player (3034,3341), goal (3038,3344),
# two shut doors in range:
#   Door 24057 (3027,3344) MOVEMENT_SOUTH  -- SEVEN tiles west, a different building entirely
#   Door 24050 (3037,3347) MOVEMENT_SOUTH  -- the one beside the goal
# The first reported separates=True purely because its y-crossing (3343|3344) sits between our y
# and the goal's. A blocked edge spans ONE tile, so being on the right axis is not enough: the
# door's other coordinate has to lie in the span we actually travel through.

def test_a_door_far_off_to_the_side_does_not_separate_us():
    door = {"x": 3027, "y": 3344, "collisionFlags": travel.BLOCK_MOVEMENT_SOUTH}
    assert travel._door_separates(door, 3034, 3341, 3038, 3344) is False


def test_a_door_on_the_column_we_travel_through_does_separate_us():
    door = {"x": 3037, "y": 3344, "collisionFlags": travel.BLOCK_MOVEMENT_SOUTH}
    assert travel._door_separates(door, 3034, 3341, 3038, 3344) is True


def test_the_perpendicular_span_is_inclusive_of_a_small_margin():
    # A doorway one tile outside the straight-line box is still the way through -- the walker
    # does not travel a perfect diagonal.
    door = {"x": 3039, "y": 3344, "collisionFlags": travel.BLOCK_MOVEMENT_SOUTH}
    assert travel._door_separates(door, 3034, 3341, 3038, 3344) is True


def test_doors_are_searched_far_enough_out_to_find_a_real_one(monkeypatch):
    """DOOR_SEARCH_RADIUS was 4 while the real doors at NORMAN's stood 6 and 7 tiles away, so the
    routeless branch had no candidates at all and returned None -- the actual reason nothing was
    ever opened there. The tight radius dated from when selection was a distance GUESS; with the
    collision edge test the candidate has to prove it crosses our path, so the radius can cover
    the width of a house without opening anything unrelated."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3034, "y": 3341, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _door_with_flags(3037, 3347, travel.BLOCK_MOVEMENT_SOUTH),   # 6 tiles away, as measured
    ])
    door = travel._find_blocking_door({"x": 3038, "y": 3348, "plane": 0})
    assert door is not None and (door["x"], door["y"]) == (3037, 3347)


# --- doors are resolved BEFORE the walk, not after it stalls --------------------------
#
# User directive 2026-08-29: "the priority should be determine door situation - then go in."
# Waiting for a stall was always backwards. A stall costs a full hop timeout to detect, the
# player spends it wedged against the door in plain view, and at NORMAN's the stall recovery
# never even ran because walk_to reported arrival outside. We can now KNOW the doorway is shut
# before taking a step -- the client's collision entry says so -- so check first and walk second.

def test_walk_to_opens_a_shut_doorway_before_it_starts_walking(monkeypatch):
    order = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3034, "y": 3341, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _door_with_flags(3037, 3344, travel.BLOCK_MOVEMENT_SOUTH),
    ])
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: order.append(("door", entity["x"], entity["y"])))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(travel, "_is_short_hop", lambda t, m: True)
    monkeypatch.setattr(travel, "_walk_greedy", lambda ctx, t, tol: order.append("walk") or True)

    travel.walk_to(_Ctx(), {"x": 3038, "y": 3348, "plane": 0})

    assert order and order[0][0] == "door", f"door must be handled before walking: {order}"
    assert "walk" in order, "and the walk must still happen"


def test_walk_to_does_not_touch_anything_when_no_door_is_in_the_way(monkeypatch):
    clicks = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3034, "y": 3341, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: clicks.append(entity))
    monkeypatch.setattr(travel, "_is_short_hop", lambda t, m: True)
    monkeypatch.setattr(travel, "_walk_greedy", lambda ctx, t, tol: True)

    assert travel.walk_to(_Ctx(), {"x": 3038, "y": 3348, "plane": 0}) is True
    assert clicks == [], "no door in the way must cost nothing"


# --- ground walk clicks must force-walk with SHIFT -------------------------------------
#
# CLAUDE.md has carried this rule since golem_crafting hit it in 2026-08: a plain click on a
# ground tile near any interactive object can resolve to that OBJECT's action instead of walking,
# because their clickable areas overlap on screen. Only walk_to_exact_tile was obeying it.
#
# CONFIRMED live 2026-08-29 (recordings/2026-08-29_012522_mh-live-run12): _hop_to PREFERS a
# viewport ground click over the minimap whenever the tile is rendered, so most hops took the
# unshifted path -- and two of them landed as "Chop down" ("You need an axe...") and "Cook"
# ("You haven't got anything to cook."). A hijacked hop leaves the player somewhere it did not
# intend, the next tick recomputes and walks back, and that is the back-and-forth the user saw.

def _hop_harness(monkeypatch, on_screen=True):
    keys = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": on_screen, "screenX": 10, "screenY": 10})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: {"onMinimap": True, "screenX": 20, "screenY": 20})
    monkeypatch.setattr(controller, "key_down", lambda ser, k: keys.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: keys.append(("up", k)))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: keys.append(("click", entity)) or True)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return keys


def test_a_viewport_hop_click_is_shift_clicked(monkeypatch):
    keys = _hop_harness(monkeypatch, on_screen=True)
    travel._hop_to(_Ctx(), 3203, 3200, 0)      # short enough for the viewport path
    kinds = [k[0] for k in keys]
    assert kinds[:3] == ["down", "click", "up"], f"ground hop must force-walk: {kinds[:4]}"
    assert keys[0][1] == "SHIFT" and keys[2][1] == "SHIFT"


def test_a_minimap_hop_click_needs_no_shift(monkeypatch):
    # The minimap has no world objects on it to hijack the click, and SHIFT there means nothing.
    keys = _hop_harness(monkeypatch, on_screen=False)
    travel._hop_to(_Ctx(), 3203, 3200, 0)      # short enough for the viewport path
    assert [k[0] for k in keys][0] == "click", f"minimap hop should click directly: {keys}"


# --- minimap for distance, viewport only for short precise hops ------------------------
#
# User directive 2026-08-29: "use minimap for walking long distances instead of viewport walk,
# only small distance accurate clicks should be viewport."
#
# _hop_to used to prefer the viewport whenever the tile was rendered at all, with the minimap
# only as a fallback for tiles beyond render distance. With WALK_HOP_MIN/MAX_TILES at 16-24 that
# meant nearly every hop of a long route took a viewport click into a scene full of interactive
# scenery -- see the "Chop down" / "Cook" hijacks measured the same day. The minimap has nothing
# on it to hijack a click; precision is what it lacks, and precision is only wanted at the end.

def test_a_long_hop_prefers_the_minimap_even_when_the_tile_is_rendered(monkeypatch):
    asked = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: asked.append("ground") or {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: asked.append("minimap") or {"onMinimap": True, "screenX": 2, "screenY": 2})
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicked.append(e) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: clicked.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: clicked.append(("up", k)))
    monkeypatch.setattr(time, "sleep", lambda s: None)

    travel._hop_to(_Ctx(), 3220, 3200, 0)          # 20 tiles -- a normal route hop

    points = [c for c in clicked if isinstance(c, dict)]
    assert points and points[0].get("onMinimap"), (
        f"a 20-tile hop must go via the minimap, not the viewport: {clicked[:3]}")


def test_a_short_hop_still_uses_the_viewport_for_precision(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point",
                        lambda x, y, p: {"onMinimap": True, "screenX": 2, "screenY": 2})
    seq = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: seq.append(("click", e)) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: seq.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: seq.append(("up", k)))
    monkeypatch.setattr(time, "sleep", lambda s: None)

    travel._hop_to(_Ctx(), 3203, 3200, 0)          # 3 tiles -- precision matters

    kinds = [s[0] for s in seq]
    assert kinds[:3] == ["down", "click", "up"], f"short hop should shift-click the viewport: {kinds}"
    assert seq[1][1].get("onScreen") is True


def test_a_long_hop_falls_back_to_the_viewport_off_the_minimap(monkeypatch):
    # A zoomed-in minimap shrinks its reach; a rendered tile is still better than giving up.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3200, "plane": 0})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, p: {"onScreen": True, "screenX": 1, "screenY": 1})
    monkeypatch.setattr(controller, "get_minimap_walk_point", lambda x, y, p: {"onMinimap": False})
    seq = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: seq.append(("click", e)) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: seq.append(("down", k)))
    monkeypatch.setattr(controller, "key_up", lambda ser, k: seq.append(("up", k)))
    monkeypatch.setattr(time, "sleep", lambda s: None)

    travel._hop_to(_Ctx(), 3220, 3200, 0)

    assert [s[0] for s in seq][:3] == ["down", "click", "up"]


# --- confirm the door actually opened, don't sleep and hope -----------------------------
#
# The id a door reports IS its state -- it is a WallObject whose id changes when it swings
# (user, 2026-08-29). Every pair measured so far is shut -> shut+1: Barbara's outer and inner
# doors both 7452 -> 7453, Falador's 24050 -> 24051. That is exactly why a door cannot be
# tracked by id across a state change, and why `actions` is what the state test keys on.
#
# It also means the open is CONFIRMABLE rather than something to sleep off: re-read the doorway
# and the blocked edge is gone. A fixed DOOR_OPEN_WAIT_S both wasted time when the door opened
# fast and silently continued when it never opened at all.

def test_opening_a_door_returns_as_soon_as_the_edge_clears(monkeypatch):
    states = [
        [_door_with_flags(1746, 3534, travel.BLOCK_MOVEMENT_EAST)],   # shut
        [_door_with_flags(1747, 3534, travel.BLOCK_MOVEMENT_SOUTH, actions=("Close",))],  # open
    ]
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1744, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: states[0] if len(states) > 1 else states[-1])
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: states.pop(0) or True)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    assert travel._open_blocking_door(_Ctx(), {"x": 1750, "y": 3534, "plane": 0}) is True
    assert sum(slept) < travel.DOOR_OPEN_WAIT_S, \
        f"should stop waiting once the doorway is clear, slept {sum(slept)}s"


def test_a_door_that_never_opens_is_reported_not_silently_accepted(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1744, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_door_with_flags(1746, 3534, travel.BLOCK_MOVEMENT_EAST)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    logged = []
    monkeypatch.setattr(travel.debug_log, "log", lambda cat, msg: logged.append(msg))

    travel._open_blocking_door(_Ctx(), {"x": 1750, "y": 3534, "plane": 0})

    assert any("still shut" in m for m in logged), f"a failed open must say so: {logged}"


# --- when doors are present but none is on the straight line, ASK for a route -----------
#
# MEASURED live 2026-08-29 at Barbara's, from the exact tile the routine kept sticking on:
#   player (1750,3536) -> hotspot (1749,3536)   ONE tile west, through a wall
#   Door 7452 (1748,3537) MOVEMENT_SOUTH -> crossing 3536|3537, both of us south -> not separating
#   Door 7452 (1746,3534) MOVEMENT_EAST  -> crossing 1746|1747, both of us east  -> not separating
# Both answers are CORRECT: neither door lies between two adjacent tiles. The way in is a detour,
# and a single-axis edge test structurally cannot see a detour. The pathfinder can -- its
# collision map treats doors as passable, so it routes straight through the doorway.
#
# So: shut doors nearby but none across the straight line is the signal to ask for a route. It is
# a narrow trigger on purpose -- a walk with no doors around it never pays for a path request.

def test_a_walled_off_adjacent_goal_escalates_to_a_route(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1750, "y": 3536, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _door_with_flags(1748, 3537, travel.BLOCK_MOVEMENT_SOUTH),
        _door_with_flags(1746, 3534, travel.BLOCK_MOVEMENT_EAST),
    ])
    route = [{"x": 1750, "y": 3537, "plane": 0}, {"x": 1749, "y": 3537, "plane": 0},
             {"x": 1748, "y": 3537, "plane": 0}, {"x": 1748, "y": 3536, "plane": 0}]
    asked = []
    monkeypatch.setattr(travel, "_request_computed_path",
                        lambda target, cfg: asked.append(target) or (route, True, "TARGET_REACHED"))
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicked.append(e) or True)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    opened = travel._open_blocking_door(_Ctx(), {"x": 1749, "y": 3536, "plane": 0})

    assert asked, "shut doors nearby but none on the straight line -- must ask for a route"
    assert opened is True
    assert clicked and (clicked[0]["x"], clicked[0]["y"]) == (1748, 3537)


def test_no_doors_nearby_never_costs_a_path_request(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1750, "y": 3536, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    asked = []
    monkeypatch.setattr(travel, "_request_computed_path",
                        lambda target, cfg: asked.append(target) or ([], False, None))

    assert travel._open_blocking_door(_Ctx(), {"x": 1749, "y": 3536, "plane": 0}) is False
    assert asked == [], "no doors around means nothing to escalate for"


# --- an unroutable goal tile is retargeted to a walkable neighbour ---------------------
#
# MEASURED live 2026-08-29 outside TAU's house, Falador. The routine sat at (3045,3348) unable to
# get in, and it was NOT a door:
#   standingLocation (3047,3345)  flags mOBJ           -> Shortest Path: 0 steps
#   hotspot          (3048,3345)  flags mOBJ|sFULL     -> Shortest Path: 0 steps
#   (3047,3346)      one tile north, flags 0           -> 4 steps, TARGET_REACHED
# Furniture stands on the home's own standing tile, so the pathfinder can never route to it,
# walk_to falls through to the obstacle-blind greedy walker, and it wedges on the wall. The door
# escalation cannot rescue that either -- it needs a route, and there will never be one.
#
# BLOCK_MOVEMENT_OBJECT (256) and BLOCK_MOVEMENT_FLOOR (2097152) mean the TILE cannot be stood
# on. FLOOR_DECORATION (262144) does not -- see the Lumbridge trapdoor.

def test_a_tile_holding_an_object_is_not_stood_on():
    assert travel._tile_blocked(travel.BLOCK_MOVEMENT_OBJECT) is True
    assert travel._tile_blocked(travel.BLOCK_MOVEMENT_FLOOR) is True
    assert travel._tile_blocked(262144) is False        # floor decoration: a trapdoor
    assert travel._tile_blocked(travel.BLOCK_MOVEMENT_EAST) is False   # an EDGE, not the tile
    assert travel._tile_blocked(0) is False
    assert travel._tile_blocked(None) is None           # "cannot tell"


def test_walk_to_retargets_a_blocked_goal_onto_a_walkable_neighbour(monkeypatch):
    grid = {(3047, 3345): travel.BLOCK_MOVEMENT_OBJECT, (3047, 3346): 0, (3046, 3345): 0}
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3045, "y": 3348, "plane": 0})
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=2: [
                            {"x": tx, "y": ty, "plane": 0, "flags": f} for (tx, ty), f in grid.items()])

    goal = travel._routable_goal({"x": 3047, "y": 3345, "plane": 0})

    assert (goal["x"], goal["y"]) != (3047, 3345), "the goal tile holds an object"
    assert (goal["x"], goal["y"]) in grid and grid[(goal["x"], goal["y"])] == 0


def test_a_clear_goal_is_left_exactly_alone(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3045, "y": 3348, "plane": 0})
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=2: [{"x": 3047, "y": 3346, "plane": 0, "flags": 0}])
    goal = {"x": 3047, "y": 3346, "plane": 0}
    assert travel._routable_goal(goal) == goal


def test_an_unreported_collision_grid_leaves_the_goal_untouched(monkeypatch):
    # Older client build, or the tile is outside the loaded scene: never guess.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3045, "y": 3348, "plane": 0})
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [])
    goal = {"x": 3047, "y": 3345, "plane": 0}
    assert travel._routable_goal(goal) == goal


# --- a door is not always a WallObject --------------------------------------------------
#
# MEASURED live 2026-08-29 outside TAU's house, Falador, from the tile the routine was stuck on.
# The entrance the player actually needs is a GAME object:
#   Door id=24050 type=GameObject (3046,3348) d=1 actions=['Open'] flags=131328 (mOBJ|sFULL)
#   Door id=24050 type=WallObject (3046,3353) d=4 actions=['Open'] flags=4104   (mE|sE)
# Same id, different object type. DOOR_OBJECT_TYPES was ("WallObject",), so the door ONE TILE
# from the player was invisible to every branch of the search while an unrelated wall door four
# tiles north was the only candidate considered.
#
# Its collision is different in kind too: a WallObject door blocks an EDGE (mE), while this one
# carries BLOCK_MOVEMENT_OBJECT and blocks the whole TILE. The edge test cannot see that, so a
# full-tile blocker separates us from the goal when its TILE lies on the way -- which is exactly
# what the route said: (3045,3349) -> (3046,3348) -> (3046,3347) -> (3046,3346), straight through
# the door tile, because Shortest Path's collision map believes doors are open.

def _game_door(x, y, flags=131328, actions=("Open",)):
    return {"id": 24050, "name": "Door", "objectType": "GameObject", "x": x, "y": y, "plane": 0,
            "distance": 1, "screenX": 100, "screenY": 100,
            "actions": list(actions), "collisionFlags": flags}


def test_a_door_that_is_a_game_object_is_still_a_door(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_game_door(3046, 3348)])
    doors = travel._shut_doors_in_range(3045, 3349, 0, travel.DOOR_SEARCH_RADIUS)
    assert doors and doors[0]["objectType"] == "GameObject", \
        "TAU's entrance is a GameObject and was invisible to the whole door search"


def test_a_full_tile_blocker_separates_when_its_tile_is_on_the_way():
    door = _game_door(3046, 3348)
    # player (3045,3349) -> goal (3046,3346): the door's tile sits between them
    assert travel._door_separates(door, 3045, 3349, 3046, 3346) is True
    # and a full-tile door well off to the side does not
    assert travel._door_separates(_game_door(3060, 3390), 3045, 3349, 3046, 3346) is False


def test_a_game_object_door_is_selected_from_the_stuck_tile(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3045, "y": 3349, "plane": 0})
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=2: [])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _game_door(3046, 3348),                                            # the real entrance
        _door_with_flags(3046, 3353, travel.BLOCK_MOVEMENT_EAST),          # unrelated wall door
    ])
    door = travel._find_blocking_door({"x": 3046, "y": 3346, "plane": 0})
    assert door is not None and (door["x"], door["y"]) == (3046, 3348)


# --- a full-tile blocker on the tile we are STANDING on is not in our way ----------------
#
# CONFIRMED live 2026-08-29 in JESS's house, upstairs. The routine looped: 5 clicks on hotspot
# 40171 -> walk -> "opening the Door at (2617,3294)" -> still shut after 2s -> repeat. (2617,3294)
# was the PLAYER'S OWN TILE.
#
# Cause, and it is mine: `collisionFlags` is the aggregate for an object's TILE, not for that
# object alone. A wall door standing on a tile that also holds something else reads
# BLOCK_MOVEMENT_OBJECT, so the full-tile branch added for TAU's GameObject door claimed it -- and
# `_within_span` is inclusive of both endpoints, so a door at the player's own position trivially
# "lies between" the player and anything. You cannot be blocked by the tile you are standing on.

def test_a_full_tile_blocker_on_our_own_tile_is_not_in_the_way():
    door = _game_door(2617, 3294)
    assert travel._door_separates(door, 2617, 3294, 2612, 3296) is False


def test_a_full_tile_blocker_on_the_goal_tile_is_not_in_the_way():
    # The goal tile being furniture is the normal case (_routable_goal retargets it); the door
    # search must not then claim that furniture as the door standing in our way.
    door = _game_door(2612, 3296)
    assert travel._door_separates(door, 2617, 3294, 2612, 3296) is False


def test_a_full_tile_blocker_genuinely_between_us_still_counts():
    # TAU's real entrance, which is what the full-tile branch exists for.
    assert travel._door_separates(_game_door(3046, 3348), 3045, 3349, 3046, 3346) is True


# --- the confirmation must cover the WALK to the door, not just the swing ----------------
#
# USER 2026-08-29, on the loop at ROSS's house: "we clicked away before waiting for us to walk to
# the door and open it". A door is opened by walking to it and THEN opening it, so a flat 2s
# budget expires before the player has even arrived at a door a few tiles off. MEASURED that run:
# the door was 4 tiles away (~2.4s of walking at one tile per 0.6s tick) and the recorder proves
# the click was good -- it registered as `Open Door` on id 40178. The two retries that followed
# resolved to `Walk here`, because by then the player had moved off the pixel we re-aimed at. A
# working click reported as a failure, then interrupted by its own retry, three times over.

def test_the_open_budget_scales_with_the_distance_to_the_door(monkeypatch):
    # Same geometry as test_a_door_that_never_opens_* (the edge test resolves it outright, so no
    # route request and no live bridge call), but SIX tiles back instead of two.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1740, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_door_with_flags(1746, 3534, travel.BLOCK_MOVEMENT_EAST)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    travel._open_blocking_door(_Ctx(), {"x": 1750, "y": 3534, "plane": 0})

    # 6 tiles of walking is ~4.2s on its own -- the old flat 2.0s expired before arrival.
    assert sum(slept) > travel.DOOR_OPEN_WAIT_S,         f"a door 6 tiles off needs the walk budgeted too, slept only {sum(slept)}s"
    assert sum(slept) >= 6 * travel.DOOR_WALK_ALLOWANCE_S,         f"slept {sum(slept)}s, less than the walk alone"


def test_the_open_budget_is_capped_so_a_stuck_door_still_fails_fast(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1700, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_door_with_flags(1746, 3534, travel.BLOCK_MOVEMENT_EAST)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    travel._open_blocking_door(_Ctx(), {"x": 1750, "y": 3534, "plane": 0})
    assert sum(slept) <= travel.DOOR_OPEN_WAIT_MAX_S + travel.DOOR_CONFIRM_POLL_S,         f"the budget must be capped, slept {sum(slept)}s"


def test_a_door_underfoot_still_confirms_quickly(monkeypatch):
    # Zero distance means no walk to budget -- the fast path must stay fast.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1745, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_door_with_flags(1746, 3534, travel.BLOCK_MOVEMENT_EAST)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    travel._open_blocking_door(_Ctx(), {"x": 1750, "y": 3534, "plane": 0})
    # One tile away: the walk allowance adds almost nothing, so this stays the fast path.
    assert sum(slept) <= travel.DOOR_OPEN_WAIT_S + travel.DOOR_WALK_ALLOWANCE_S + 0.31


# --- last-resort door opening, only from a path that has proven it is stuck ---------------
#
# MEASURED live 2026-08-29 outside ROSS's house: player (2614,3319) on the grass north of the
# building, the Broken Range at (2617,3318) inside it, and the only way in a door at (2610,3316)
# whose blocked edge (2610|2611) has BOTH of us east of it. `_door_separates` answers "not between
# us" -- correct, and useless. The route branch that normally catches a detour returned no route.

def test_open_nearest_shut_door_takes_a_detour_door_the_edge_test_cannot_see(monkeypatch):
    door = _door_with_flags(2610, 3316, travel.BLOCK_MOVEMENT_EAST)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2614, "y": 3319, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door])
    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicked.setdefault("xy", (e["x"], e["y"])))

    goal = {"x": 2617, "y": 3318, "plane": 0}
    # The strict test finds nothing -- that is the whole premise.
    assert travel._find_blocking_door(goal) is None
    # ...and the last-resort one still opens the door in sight.
    assert travel.open_nearest_shut_door(_Ctx(), goal) is True
    assert clicked["xy"] == (2610, 3316)


def test_open_nearest_shut_door_does_nothing_when_no_door_is_shut(monkeypatch):
    openable = _door_with_flags(2610, 3316, travel.BLOCK_MOVEMENT_EAST, actions=("Close",))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2614, "y": 3319, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [openable])
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: pytest.fail("nothing is shut -- must not click"))

    assert travel.open_nearest_shut_door(_Ctx(), {"x": 2617, "y": 3318, "plane": 0}) is False


def test_open_nearest_shut_door_picks_the_one_nearest_US(monkeypatch):
    near = _door_with_flags(2612, 3319, travel.BLOCK_MOVEMENT_EAST)
    far = _door_with_flags(2608, 3316, travel.BLOCK_MOVEMENT_EAST)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2614, "y": 3319, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [far, near])
    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicked.setdefault("xy", (e["x"], e["y"])))

    travel.open_nearest_shut_door(_Ctx(), {"x": 2617, "y": 3318, "plane": 0})
    # Nearest to US: we have to reach it before we can open it.
    assert clicked["xy"] == (2612, 3319)


# --- reachability decided up front, instead of discovered by failing five clicks ----------
#
# MEASURED over one full ROSS contract 2026-08-29: 15 of 27 hotspot clicks -- 56% -- achieved
# nothing, across three stuck events of five wasted clicks each. USER: "every time we have to walk
# through a door now we have to get stuck on the outside 5 clicks in a row this is unacceptable."

def _grid(cells):
    """cells: {(x, y): flags} -> a /collision style response."""
    return [{"x": x, "y": y, "plane": 0, "flags": f} for (x, y), f in cells.items()]


def test_reachable_set_stops_at_a_blocked_edge(monkeypatch):
    # A wall along y=1|2: every y=2 tile blocks south, every y=1 tile blocks north.
    cells = {}
    for x in range(0, 4):
        cells[(x, 2)] = travel.BLOCK_MOVEMENT_SOUTH
        cells[(x, 1)] = travel.BLOCK_MOVEMENT_NORTH
        cells[(x, 3)] = 0
        cells[(x, 0)] = 0
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))

    reach = travel.reachable_set(0, 3, 0)
    assert (3, 2) in reach, "north of the wall is reachable"
    assert (0, 1) not in reach and (0, 0) not in reach, "south of the wall is not"


def test_reachable_set_is_empty_when_the_client_cannot_tell(monkeypatch):
    # Empty must read as "don't know" at every call site, never as "nothing is reachable".
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: [])
    assert travel.reachable_set(0, 0, 0) == set()


def test_can_reach_counts_standing_NEXT_TO_the_goal(monkeypatch):
    # A hotspot's own tile is furniture -- unwalkable -- so adjacency is what matters.
    cells = {(0, 0): 0, (1, 0): 0, (2, 0): travel.BLOCK_MOVEMENT_OBJECT}
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    assert travel.can_reach({"x": 2, "y": 0, "plane": 0}) is True


def test_can_reach_returns_None_when_unknown(monkeypatch):
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: [])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    assert travel.can_reach({"x": 5, "y": 5, "plane": 0}) is None


def test_open_frontier_door_finds_the_door_out_even_when_it_is_a_detour(monkeypatch):
    # The ROSS geometry: player and goal BOTH east of the door's blocked edge, so the
    # single-axis test says "not between us" -- correct and useless.
    cells = {}
    for x in range(0, 5):
        for y in range(0, 3):
            cells[(x, y)] = 0
    # Wall along y=1|2 for x>=1, with the only gap at x=0 guarded by a shut door.
    for x in range(1, 5):
        cells[(x, 2)] = travel.BLOCK_MOVEMENT_SOUTH
        cells[(x, 1)] = travel.BLOCK_MOVEMENT_NORTH
    cells[(0, 2)] = travel.BLOCK_MOVEMENT_SOUTH
    cells[(0, 1)] = travel.BLOCK_MOVEMENT_NORTH
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3, "y": 2, "plane": 0})

    door = _door_with_flags(0, 2, travel.BLOCK_MOVEMENT_SOUTH)
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [door])
    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicked.setdefault("xy", (e["x"], e["y"])))

    assert travel.open_frontier_door(_Ctx(), {"x": 4, "y": 0, "plane": 0}) is True
    assert clicked["xy"] == (0, 2), "the frontier door is the way out, detour or not"


def test_open_frontier_door_ignores_a_door_entirely_inside_our_own_area(monkeypatch):
    # A shut door with BOTH sides already reachable is not a way out of anywhere.
    cells = {(x, y): 0 for x in range(0, 4) for y in range(0, 3)}
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_door_with_flags(1, 1, travel.BLOCK_MOVEMENT_EAST)])
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: pytest.fail("that door leads nowhere new"))

    assert travel.open_frontier_door(_Ctx(), {"x": 3, "y": 2, "plane": 0}) is False


def test_can_reach_counts_DIAGONAL_adjacency(monkeypatch):
    # MEASURED live 2026-08-29 upstairs at JEFF's: player at (3241,3447), hotspot at (3242,3446),
    # every CARDINAL neighbour of the hotspot furniture. A cardinal-only test called it
    # unreachable and work_phase refused to click a thing the character was standing beside.
    # USER: "wtf it is reachable hes standing next to it".
    cells = {(3241, 3447): 0,
             (3241, 3446): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3447): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3445): travel.BLOCK_MOVEMENT_OBJECT,
             (3243, 3446): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3446): travel.BLOCK_MOVEMENT_OBJECT}
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3241, "y": 3447, "plane": 1})

    assert travel.can_reach({"x": 3242, "y": 3446, "plane": 1}) is True


# --- adjacency is NOT reachability: the EDGE decides -------------------------------------
#
# Two live geometries from 2026-08-29 that look identical by tile distance and must resolve
# oppositely. Distance alone cannot separate them; the collision edge can.

def test_can_reach_is_false_through_a_wall_even_at_distance_one(monkeypatch):
    # ROSS's exterior wall: player (2617,3319) north of it, Broken Range (2617,3318) south.
    # Chebyshev distance 1. USER: "stuck outside ross again... this is absurd".
    cells = {}
    for x in (2616, 2617, 2618):
        cells[(x, 3319)] = travel.BLOCK_MOVEMENT_SOUTH
        cells[(x, 3318)] = travel.BLOCK_MOVEMENT_NORTH
        cells[(x, 3320)] = 0
    cells[(2617, 3318)] = travel.BLOCK_MOVEMENT_NORTH | travel.BLOCK_MOVEMENT_OBJECT
    cells[(2618, 3318)] = travel.BLOCK_MOVEMENT_NORTH | travel.BLOCK_MOVEMENT_WEST
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2617, "y": 3319, "plane": 0})

    reach = travel.reachable_set(2617, 3319, 0)
    assert (2617, 3319) in reach and (2617, 3318) not in reach
    assert travel.can_reach({"x": 2617, "y": 3318, "plane": 0}, reach) is False, \
        "a wall on the shared edge means unreachable, however close we stand"


def test_can_reach_is_true_diagonally_when_a_corner_route_is_open(monkeypatch):
    # JEFF's upstairs: player (3241,3447), hotspot (3242,3446) whose cardinal neighbours are all
    # furniture -- unwalkable, but with no wall on the edges, so the corner route reaches.
    # USER: "wtf it is reachable hes standing next to it".
    cells = {(3241, 3447): 0,
             (3241, 3446): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3447): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3445): travel.BLOCK_MOVEMENT_OBJECT,
             (3243, 3446): travel.BLOCK_MOVEMENT_OBJECT,
             (3242, 3446): travel.BLOCK_MOVEMENT_OBJECT}
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3241, "y": 3447, "plane": 1})

    reach = travel.reachable_set(3241, 3447, 1)
    assert travel.can_reach({"x": 3242, "y": 3446, "plane": 1}, reach) is True


def test_a_diagonal_behind_a_wall_does_not_count(monkeypatch):
    # The permissive version of the diagonal rule was wrong at ROSS's: (2616,3319)/(2618,3319)
    # are diagonal to the hotspot but on the far side of the wall.
    cells = {}
    for x in (2616, 2617, 2618):
        cells[(x, 3319)] = travel.BLOCK_MOVEMENT_SOUTH
        cells[(x, 3318)] = travel.BLOCK_MOVEMENT_NORTH
    cells[(2617, 3318)] = travel.BLOCK_MOVEMENT_NORTH | travel.BLOCK_MOVEMENT_OBJECT
    cells[(2618, 3318)] = travel.BLOCK_MOVEMENT_NORTH | travel.BLOCK_MOVEMENT_WEST
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: _grid(cells))
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2618, "y": 3319, "plane": 0})

    reach = travel.reachable_set(2618, 3319, 0)
    assert travel.can_reach({"x": 2617, "y": 3318, "plane": 0}, reach) is False


# --- route-based door detection ------------------------------------------------------------

BLOCK_MOVEMENT_NORTH = 2
BLOCK_MOVEMENT_EAST = 8
BLOCK_MOVEMENT_SOUTH = 32
BLOCK_MOVEMENT_WEST = 128
BLOCK_MOVEMENT_OBJECT = 256


def _route_door(x, y, name="Door", actions=("Open",), object_type="WallObject",
                 flags=BLOCK_MOVEMENT_NORTH, plane=0):
    return {"id": 7452, "name": name, "objectType": object_type, "actions": list(actions),
            "collisionFlags": flags, "x": x, "y": y, "plane": plane}


def _open_grid(tiles, blocked=()):
    """Flags for a set of tiles, all freely walkable except where stated."""
    grid = {tile: 0 for tile in tiles}
    for tile, value in blocked:
        grid[tile] = value
    return grid


def test_finds_the_edge_door_standing_on_the_blocked_crossing():
    """Barbara's shape: the crossing between two route tiles is shut, and the door object sits
    on one of them. MEASURED live -- a shut door records its blocked edge on BOTH tiles."""
    route = [(1746, 3533), (1746, 3534), (1746, 3535)]
    flags = _open_grid(route, blocked=[((1746, 3534), BLOCK_MOVEMENT_NORTH)])
    door = _route_door(1746, 3534)
    assert travel.first_door_on_route(route, 0, [door], flags) is door


def test_finds_the_full_tile_door_standing_on_a_route_tile():
    """Falador TAU's, MEASURED 2026-08-29: a GameObject door with flags 131328 -- LOS-full plus
    BLOCK_MOVEMENT_OBJECT and NO edge bits at all. Caught because you cannot step onto its
    tile, not because any edge is marked."""
    route = [(3046, 3349), (3046, 3348), (3046, 3347)]
    flags = _open_grid(route, blocked=[((3046, 3348), BLOCK_MOVEMENT_OBJECT)])
    door = _route_door(3046, 3348, object_type="GameObject", flags=131328)
    assert travel.first_door_on_route(route, 0, [door], flags) is door


def test_ignores_an_open_door_because_clicking_one_CLOSES_it():
    """The correction we keep over Vitalite's name-only match. A shut door and an open one are
    both WallObjects named Door (Falador 24050 shut / 24051 open); only the action separates
    them, and left-clicking the open one turns a working walk into a wedged one."""
    route = [(1746, 3533), (1746, 3534), (1746, 3535)]
    flags = _open_grid(route, blocked=[((1746, 3534), BLOCK_MOVEMENT_NORTH)])
    open_door = _route_door(1746, 3534, actions=("Close",))
    assert travel.first_door_on_route(route, 0, [open_door], flags) is None


def test_finds_a_curtain():
    """Taken from Vitalite's name list, which we were missing."""
    route = [(1746, 3533), (1746, 3534)]
    flags = _open_grid(route, blocked=[((1746, 3533), BLOCK_MOVEMENT_NORTH),
                                       ((1746, 3534), BLOCK_MOVEMENT_SOUTH)])
    curtain = _route_door(1746, 3534, name="Curtain")
    assert travel.first_door_on_route(route, 0, [curtain], flags) is curtain


def test_ignores_furniture_that_also_offers_open():
    """Drawers and cupboards fill every Mahogany Homes house and all offer Open. A live scan
    beside Falador's door returned Drawers and a Cupboard within 7 tiles."""
    route = [(1746, 3533), (1746, 3534)]
    flags = _open_grid(route, blocked=[((1746, 3533), BLOCK_MOVEMENT_NORTH),
                                       ((1746, 3534), BLOCK_MOVEMENT_SOUTH)])
    drawers = _route_door(1746, 3534, name="Drawers")
    assert travel.first_door_on_route(route, 0, [drawers], flags) is None


def test_returns_none_when_the_whole_route_is_clear():
    route = [(1746, 3533), (1746, 3534), (1746, 3535)]
    flags = _open_grid(route)
    assert travel.first_door_on_route(route, 0, [_route_door(1746, 3534)], flags) is None


def test_ignores_a_door_on_another_plane():
    route = [(2616, 3317), (2617, 3317)]
    flags = _open_grid(route, blocked=[((2616, 3317), BLOCK_MOVEMENT_EAST)])
    downstairs = _route_door(2616, 3317, plane=0)
    assert travel.first_door_on_route(route, 1, [downstairs], flags) is None


def test_unknown_flags_never_invent_a_door():
    """An absent collision reading means "cannot tell", never "blocked" -- an un-rebuilt or
    out-of-scene answer must behave as before, not conjure a door to click."""
    route = [(1746, 3533), (1746, 3534)]
    assert travel.first_door_on_route(route, 0, [_route_door(1746, 3534)], {}) is None


def test_a_half_known_crossing_yields_no_door():
    """Deliberately strict: when only ONE side of a crossing has a collision reading we decline
    to name a door. An unknown reading must never produce a click -- left-clicking an open door
    CLOSES it, turning a working walk into a wedged one."""
    route = [(1746, 3533), (1746, 3534)]
    flags = {(1746, 3533): BLOCK_MOVEMENT_NORTH}      # (1746,3534) unknown
    assert travel.first_door_on_route(route, 0, [_route_door(1746, 3534)], flags) is None


def test_blocked_crossing_reports_the_first_one_only():
    route = [(10, 10), (10, 11), (10, 12), (10, 13)]
    flags = _open_grid(route, blocked=[((10, 11), BLOCK_MOVEMENT_NORTH),
                                       ((10, 12), BLOCK_MOVEMENT_NORTH)])
    assert travel.blocked_crossing_on_route(route, flags) == ((10, 11), (10, 12))
