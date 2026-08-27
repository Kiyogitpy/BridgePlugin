"""Tests for falconry.py's Quick-falcon menu click gaining refresh_target."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import falconry
import controller


class _Ctx:
    ser = None


def test_hire_falcon_passes_refresh_target(monkeypatch):
    matthias = {"index": 3, "x": 100, "y": 200, "plane": 0, "name": "Matthias", "distance": 2}
    monkeypatch.setattr(falconry, "_first_inventory_item",
                         lambda sub: {"qty": falconry.DEFAULT_CONFIG["minCoins"]})
    monkeypatch.setattr(falconry, "_nearest_npc", lambda name, max_distance: matthias)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    assert falconry._hire_falcon(_Ctx()) is True
    assert seen.get("refresh_target") is not None


def test_leg_fairy_ring_passes_refresh_target(monkeypatch):
    """The fairy ring is a real-world object read from the TickCache-gated bulk /objects list, so
    its resolved screen position can be stale by up to 600ms before the click even fires and its
    projection moves with the camera -- same rationale as every other newly-fixed
    click_menu_action object call site in this branch (see pyramid_plunder.py's quick_leave)."""
    monkeypatch.setattr(controller, "get_equipment",
                         lambda: {"weapon": {"name": "Dramen staff"}})
    ring = {"id": falconry.FAIRY_RING_ID, "x": 100, "y": 200, "plane": 0, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=20: [ring])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    falconry._leg_fairy_ring(_Ctx())

    assert seen.get("refresh_target") is not None
