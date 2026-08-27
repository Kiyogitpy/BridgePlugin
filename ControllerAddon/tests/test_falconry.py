"""Tests for falconry.py's Quick-falcon menu click gaining refresh_target."""

import os
import sys

import pytest

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
