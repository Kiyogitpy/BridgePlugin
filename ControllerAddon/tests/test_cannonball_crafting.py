"""Tests for cannonball_crafting.py's menu-escalation smelt click."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import cannonball_crafting as cc
import controller


class _Ctx:
    ser = None


def test_click_smelt_via_menu_passes_refresh_target(monkeypatch):
    """click_smelt_via_menu re-resolves the furnace via /object/at then right-clicks it -- now
    that click_menu_action can re-aim mid-flight (2026-08-27), the call should hand it a
    refresh_target factory the same way click_smelt's bare-click path already does."""
    furnace = {"x": 100, "y": 200, "plane": 0, "id": 999, "name": "Furnace"}
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(
        controller, "get_object_at",
        lambda x, y, plane, obj_id, radius=None: {
            "id": obj_id, "x": x, "y": y, "plane": plane, "name": "Furnace",
        },
    )
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    assert cc.click_smelt_via_menu(_Ctx(), furnace) is True
    assert seen.get("refresh_target") is not None, \
        "menu escalation must re-resolve the furnace's live screen position"
