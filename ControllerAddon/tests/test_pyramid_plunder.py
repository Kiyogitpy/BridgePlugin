"""Tests for pyramid_plunder.py's two click_menu_action call sites gaining refresh_target."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pyramid_plunder as pp
import controller


class _Ctx:
    ser = None
    state = None


def test_lobby_tick_passes_refresh_target(monkeypatch):
    mummy = {"index": 7, "x": 100, "y": 200, "plane": 0, "name": "Guardian mummy"}
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(pp, "note_progress", lambda ctx: None)
    monkeypatch.setattr(controller, "get_pyramid_plunder", lambda: {"inRun": False})
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return False  # bail out before the confirmation poll loop; only the call matters here
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    pp.lobby_tick(_Ctx(), pp.DEFAULT_CONFIG, mummy)
    assert seen.get("refresh_target") is not None


def test_quick_leave_passes_refresh_target(monkeypatch):
    exit_door = {"id": 123, "x": 100, "y": 200, "plane": 0, "name": "Tomb Door"}
    monkeypatch.setattr(pp, "find_exit_doors", lambda objs: [exit_door])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [exit_door])
    monkeypatch.setattr(pp, "note_progress", lambda ctx: None)
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return False
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    pp.quick_leave(_Ctx(), pp.DEFAULT_CONFIG)
    assert seen.get("refresh_target") is not None
