"""Tests for tormented_demon.py's tick() combat click using the refresh_target factory."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import tormented_demon as td
import controller


class _State:
    def __init__(self, config):
        self.config = config


class _Ctx:
    def __init__(self, config):
        self.ser = None
        self.state = _State(config)


def test_tick_uses_the_npc_index_refresh_target_factory(monkeypatch):
    """Regression: a hand-rolled lambda calling get_npc_screen_pos_by_index directly bypasses
    the 2026-08-27 deadband hardening that npc_index_refresh_target wraps around it. Asserting
    the factory itself was called (not just any callable) is what catches a regression back to
    the hand-rolled version."""
    npc = {"index": 9, "x": 100, "y": 200, "plane": 0, "name": "Tormented Demon"}
    config = {"food": {"enabled": False}}

    monkeypatch.setattr(controller, "get_player", lambda: {"hp": 99})
    monkeypatch.setattr(td, "find_target", lambda cfg: npc)
    monkeypatch.setattr(td, "maybe_dodge_fireball", lambda ctx, player: False)
    monkeypatch.setattr(td, "react_to_attack_animation", lambda ctx, npc: None)
    monkeypatch.setattr(td, "maintain_prayer_potions", lambda ctx, cfg: None)
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": True})

    factory_calls = []
    real_factory = controller.npc_index_refresh_target
    def _spy_factory(npc_index):
        factory_calls.append(npc_index)
        return real_factory(npc_index)
    monkeypatch.setattr(controller, "npc_index_refresh_target", _spy_factory)

    click_kwargs = []
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, e, **kw: click_kwargs.append(kw) or True)

    td.tick(_Ctx(config))
    assert factory_calls == [9], "must go through npc_index_refresh_target, not a hand-rolled lambda"
    assert click_kwargs and click_kwargs[0].get("refresh_target") is not None
