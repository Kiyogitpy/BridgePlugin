"""Firemaking's alternating GE starter tiles (USER, 2026-09-05): each inventory burns one
east-to-west line from a starter tile; after every bank the row alternates."""
import types

import pytest

import controller
import travel
from routines import firemaking as fm


def _ctx():
    state = types.SimpleNamespace(data={}, config=fm.DEFAULT_CONFIG, safety_stopped=False)
    return types.SimpleNamespace(ser=None, state=state, events=None)


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda *a, **k: True)
    monkeypatch.setattr(travel, "walk_to_exact_tile", lambda *a, **k: True)


def test_starter_alternates_after_each_bank():
    ctx = _ctx()
    assert fm.starter_tile(ctx) == (3179, 3475)
    fm.advance_starter(ctx)
    assert fm.starter_tile(ctx) == (3179, 3474)
    assert ctx.state.data["fm_positioned"] is False
    fm.advance_starter(ctx)
    assert fm.starter_tile(ctx) == (3179, 3475)


def test_positioned_once_standing_on_the_tile(monkeypatch):
    ctx = _ctx()
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3179, "y": 3475, "plane": 0})
    assert fm.go_to_starter_tile(ctx) is True
    assert ctx.state.data["fm_positioned"] is True


def test_near_tile_uses_exact_walk_and_defers(monkeypatch):
    ctx = _ctx()
    calls = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3175, "y": 3475, "plane": 0})
    monkeypatch.setattr(travel, "walk_to_exact_tile",
                        lambda c, x, y, p, wait_for_arrival=True: calls.append((x, y, p, wait_for_arrival)) or True)
    monkeypatch.setattr(travel, "walk_to", lambda *a, **k: pytest.fail("routed walk not expected"))
    assert fm.go_to_starter_tile(ctx) is False
    assert calls == [(3179, 3475, 0, False)]
    assert not ctx.state.data.get("fm_positioned")


def test_far_tile_routes_first(monkeypatch):
    ctx = _ctx()
    routed = []
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3165, "y": 3489, "plane": 0})  # GE bank
    monkeypatch.setattr(travel, "walk_to", lambda c, t, **k: routed.append((t, k)) or True)
    monkeypatch.setattr(travel, "walk_to_exact_tile", lambda *a, **k: pytest.fail("exact walk not expected"))
    assert fm.go_to_starter_tile(ctx) is False
    assert routed == [({"x": 3179, "y": 3475, "plane": 0}, {"goal_adjacent": False})]


def test_gives_up_positioning_after_tries(monkeypatch):
    ctx = _ctx()
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3175, "y": 3475, "plane": 0})
    for _ in range(fm.STARTER_WALK_TRIES):
        assert fm.go_to_starter_tile(ctx) is False
    assert fm.go_to_starter_tile(ctx) is True
    assert ctx.state.data["fm_positioned"] is True


def test_get_config_marks_withdraw_all(monkeypatch):
    bridge = {"logName": "Yew logs", "bank": {"enabled": True,
              "withdraw": [{"name": "Yew logs", "quantity": 27}]}}
    monkeypatch.setattr(controller, "get_firemaking_config", lambda: bridge)
    cfg = fm.get_config(None)
    assert cfg["bank"]["withdraw"] == [{"name": "Yew logs", "quantity": 27, "all": True}]
    assert bridge["bank"]["withdraw"][0].get("all") is None   # the bridge's dict is untouched
    monkeypatch.setattr(controller, "get_firemaking_config", lambda: None)
    assert fm.get_config(None)["bank"]["withdraw"][0]["all"] is True


# --- tick-perfect burn chain --------------------------------------------------------------

class _Chain:
    """Stubs for burn_chain: an inventory of N logs that shrinks on every successful light,
    XP that rises with it, and a record of every mouse primitive used."""

    def __init__(self, monkeypatch, logs=3, fail_first=0, idle_when_failing=True):
        self.logs = logs
        self.xp = 1000
        self.fail_left = fail_first
        self.calls = []
        self.t = [0.0]
        monkeypatch.setattr(fm, "_now", lambda: self.t[0])
        monkeypatch.setattr(fm, "_sleep", lambda s: self.t.__setitem__(0, self.t[0] + s))
        monkeypatch.setattr(controller, "get_inventory", self.inventory)
        monkeypatch.setattr(controller, "get_skills", lambda: {"Firemaking": {"xp": self.xp}})
        monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": idle_when_failing})
        monkeypatch.setattr(controller, "click_entity",
                            lambda ser, e, **k: self.calls.append(("click", e["name"])) or True)
        monkeypatch.setattr(controller, "click_human", lambda ser: self.calls.append(("in_place",)))
        monkeypatch.setattr(controller, "hover_entity",
                            lambda ser, e, **k: self.calls.append(("hover", e["name"])) or True)
        monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
        monkeypatch.setattr(fm, "step_west", lambda ctx: self.calls.append(("step_west",)))

        def await_outcome(events, signals, budget, confirm=None, **k):
            # The log click "lights" unless a failure is pending; the XP lands 4 ticks later.
            self.t[0] += 2.4
            if self.fail_left > 0:
                self.fail_left -= 1
                self.t[0] += budget
                return None
            self.logs -= 1
            self.xp += 90
            value = confirm()
            return types.SimpleNamespace(confirmed=value) if value else None
        monkeypatch.setattr(fm.bridge_events, "await_outcome", await_outcome)

    def inventory(self):
        return [{"name": "Tinderbox", "slot": 1}] + [{"name": "Willow logs", "slot": 2 + i}
                                                     for i in range(self.logs)]


def test_burn_chain_lights_every_log_with_the_tinderbox_parked(monkeypatch):
    chain = _Chain(monkeypatch, logs=3)
    assert fm.burn_chain(_ctx(), fm.DEFAULT_CONFIG) == 3
    clicks = [c for c in chain.calls if c[0] != "hover"]
    # first pair flies to the tinderbox; every later pair opens with an in-place click
    assert clicks == [("click", "Tinderbox"), ("click", "Willow logs"),
                      ("in_place",), ("click", "Willow logs"),
                      ("in_place",), ("click", "Willow logs")]
    assert chain.calls.count(("hover", "Tinderbox")) == 3
    assert chain.logs == 0


def test_burn_chain_reclicks_then_steps_west_on_idle_failures(monkeypatch):
    chain = _Chain(monkeypatch, logs=1, fail_first=fm.FAIL_STEP_AFTER)
    assert fm.burn_chain(_ctx(), fm.DEFAULT_CONFIG) == 1
    assert ("step_west",) in chain.calls
    # after a failure the park is not trusted: the tinderbox is re-aimed with a real click
    assert chain.calls.count(("click", "Tinderbox")) == fm.FAIL_STEP_AFTER + 1


def test_burn_chain_waits_out_the_games_own_retry(monkeypatch):
    # No XP inside the budget but the player is still animating: no re-click, no step.
    chain = _Chain(monkeypatch, logs=1, fail_first=1, idle_when_failing=False)
    assert fm.burn_chain(_ctx(), fm.DEFAULT_CONFIG) == 1
    assert ("step_west",) not in chain.calls
    assert chain.calls.count(("click", "Tinderbox")) == 1
