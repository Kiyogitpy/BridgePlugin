import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# mcp_debug_server opens a real serial port at import time -- stub serial.Serial before
# importing it, so these pure-logic tests run without any hardware attached.
import serial
serial.Serial = MagicMock(return_value=MagicMock())

import mcp_debug_server as s  # noqa: E402


def _fake_response(status_code=200, json_body=None, raise_exc=None):
    resp = MagicMock()
    resp.status_code = status_code
    if raise_exc is not None:
        resp.json.side_effect = raise_exc
    else:
        resp.json.return_value = json_body
    return resp


def test_wrap_bridge_response_success_passes_json_through():
    resp = _fake_response(200, {"loggedIn": True})
    assert s._wrap_bridge_response(resp) == {"loggedIn": True}


def test_wrap_bridge_response_non_2xx_returns_error_with_status():
    resp = _fake_response(500, {"ignored": True})
    result = s._wrap_bridge_response(resp)
    assert result == {"error": "bridge returned HTTP 500"}


def test_wrap_bridge_response_bad_json_returns_error():
    resp = _fake_response(200, raise_exc=ValueError("no JSON"))
    result = s._wrap_bridge_response(resp)
    assert result["error"].startswith("invalid JSON")


def test_bridge_get_impl_wraps_request_exception():
    def raising_get(url, params=None, timeout=None):
        raise ConnectionError("refused")

    result = s._bridge_get_impl("/state", None, requests_get=raising_get)
    assert result == {"error": "refused"}


def test_bridge_get_impl_builds_correct_url_and_returns_json():
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return _fake_response(200, {"open": True})

    result = s._bridge_get_impl("/menu", {"id": 5}, requests_get=fake_get)
    assert result == {"open": True}
    assert calls == [(f"{s.settings.API_BASE}/menu", {"id": 5})]


def test_require_within_window_returns_none_when_inside(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    assert s._require_within_window(100, 100) is None


def test_require_within_window_returns_error_when_outside(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: False)
    result = s._require_within_window(9999, 9999)
    assert result == {"ok": False, "error": "(9999, 9999) is outside the RuneLite window"}


def test_move_mouse_rejects_out_of_window_target(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: False)
    move_calls = []
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y: move_calls.append((x, y)))
    result = s.move_mouse(50, 50)
    assert result["ok"] is False
    assert move_calls == []  # never attempted the move


def test_move_mouse_moves_when_inside_window(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    move_calls = []
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y: move_calls.append((x, y)))
    result = s.move_mouse(50, 50)
    assert result == {"ok": True}
    assert move_calls == [(50, 50)]


def test_type_text_maps_space_and_optional_enter(monkeypatch):
    taps = []
    monkeypatch.setattr(s.controller, "key_tap", lambda ser, key: taps.append(key))
    s.type_text("ab c", press_enter=True)
    assert taps == ["a", "b", "SPACE", "c", "ENTER"]


def test_type_text_no_enter_by_default(monkeypatch):
    taps = []
    monkeypatch.setattr(s.controller, "key_tap", lambda ser, key: taps.append(key))
    s.type_text("hi")
    assert taps == ["h", "i"]
