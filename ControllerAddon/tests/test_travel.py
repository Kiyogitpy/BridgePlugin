"""Tests for travel.py's Shortest Path handshake.

Everything here is about the handshake itself -- correlating a result with the request that
asked for it, deciding when the pathfinder is worth asking at all, what transport policy goes
out with the request, and refusing to walk through a transport edge we physically cannot use.
The walking machinery underneath (_hop_to, click_entity, the Arduino) is stubbed out; none of
these tests touch a bridge or a serial port.
"""

import os
import sys

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


def test_request_shortest_path_without_config_sends_no_cfg_params(monkeypatch):
    seen = {}
    monkeypatch.setattr(controller, "api_get",
                        lambda path, params=None, **kw: seen.update(params or {}) or {"requested": True})

    controller.request_shortest_path(3200, 3200, 0)

    assert not [k for k in seen if k.startswith("cfg.")]
