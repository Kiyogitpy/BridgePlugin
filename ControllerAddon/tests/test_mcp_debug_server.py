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


def test_bridge_get_wraps_list_body_in_items(monkeypatch):
    # _bridge_get_impl's `requests_get` default is bound at import time, so patching
    # requests.get after the fact wouldn't reach it -- patch _bridge_get_impl itself instead,
    # which is exactly the seam bridge_get (the tool function under test here) calls through.
    def fake_bridge_get_impl(path, params):
        assert path == "/npcs"
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(s, "_bridge_get_impl", fake_bridge_get_impl)
    result = s.bridge_get("/npcs")
    assert result == {"items": [{"id": 1}, {"id": 2}]}


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
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: move_calls.append((x, y)))
    result = s.move_mouse(50, 50)
    assert result["ok"] is False
    assert move_calls == []  # never attempted the move


def test_move_mouse_moves_when_inside_window(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    move_calls = []
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: move_calls.append((x, y)))
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


def test_match_menu_entry_matches_by_option_substring():
    menu = {"open": True, "entries": [
        {"index": 0, "option": "Attack", "target": "Goblin", "screenX": 10, "screenY": 20},
        {"index": 1, "option": "Examine", "target": "Goblin", "screenX": 10, "screenY": 35},
    ]}
    match = s._match_menu_entry(menu, "attack", None)
    assert match == menu["entries"][0]


def test_match_menu_entry_matches_by_option_and_target_hint():
    menu = {"open": True, "entries": [
        {"index": 0, "option": "Take", "target": "Coins", "screenX": 1, "screenY": 2},
        {"index": 1, "option": "Take", "target": "Bones", "screenX": 3, "screenY": 4},
    ]}
    match = s._match_menu_entry(menu, "take", "bones")
    assert match == menu["entries"][1]


def test_match_menu_entry_returns_none_when_nothing_matches():
    menu = {"open": True, "entries": [{"index": 0, "option": "Cancel", "target": "", "screenX": 0, "screenY": 0}]}
    assert s._match_menu_entry(menu, "attack", None) is None


def test_click_menu_entry_rejects_out_of_window_origin(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: False)
    result = s.click_menu_entry(x=-1, y=-1, option="attack")
    assert result["ok"] is False
    assert "outside the RuneLite window" in result["error"]


def test_click_menu_entry_reports_available_options_on_no_match(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: None)
    monkeypatch.setattr(s.controller, "right_click_human", lambda ser: None)
    monkeypatch.setattr(s.controller, "click_human", lambda ser: None)
    monkeypatch.setattr(s.controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(s.controller, "get_cursor_pos", lambda: (5, 5))
    monkeypatch.setattr(s.controller, "pick_dismiss_point", lambda x, y: (200, 200))
    monkeypatch.setattr(s, "_bridge_get_impl", lambda path, params: {
        "open": True, "entries": [{"index": 0, "option": "Cancel", "target": "", "screenX": 0, "screenY": 0}],
    })
    result = s.click_menu_entry(x=10, y=10, option="attack")
    assert result["ok"] is False
    assert result["available_options"] == ["Cancel"]


def test_click_menu_entry_clicks_the_match(monkeypatch):
    moves, clicks = [], []
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: moves.append((x, y)))
    monkeypatch.setattr(s.controller, "right_click_human", lambda ser: None)
    monkeypatch.setattr(s.controller, "click_human", lambda ser: clicks.append(True))
    monkeypatch.setattr(s, "_bridge_get_impl", lambda path, params: {
        "open": True, "entries": [{"index": 0, "option": "Attack", "target": "Goblin", "screenX": 77, "screenY": 88}],
    })
    result = s.click_menu_entry(x=10, y=10, option="attack")
    assert result == {"ok": True, "matched": {"index": 0, "option": "Attack", "target": "Goblin", "screenX": 77, "screenY": 88}}
    assert (77, 88) in moves
    assert clicks == [True]


def test_click_menu_entry_dismisses_when_menu_never_opened(monkeypatch):
    dismiss_calls = []
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: None)
    monkeypatch.setattr(s.controller, "right_click_human", lambda ser: None)
    monkeypatch.setattr(s.controller, "key_tap", lambda ser, key: dismiss_calls.append(key))
    monkeypatch.setattr(s.controller, "get_cursor_pos", lambda: (5, 5))
    monkeypatch.setattr(s.controller, "pick_dismiss_point", lambda x, y: (200, 200))
    monkeypatch.setattr(s, "_bridge_get_impl", lambda path, params: {"open": False, "entries": []})
    result = s.click_menu_entry(x=10, y=10, option="attack")
    assert result == {"ok": False, "error": "menu never opened", "available_options": []}
    assert dismiss_calls == ["ESC"]


def test_click_menu_entry_reports_bridge_error_distinctly(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: None)
    monkeypatch.setattr(s.controller, "right_click_human", lambda ser: None)
    monkeypatch.setattr(s, "_bridge_get_impl", lambda path, params: {"error": "refused"})
    result = s.click_menu_entry(x=10, y=10, option="attack")
    assert result == {"ok": False, "error": "bridge error: refused"}


def test_left_mouse_down_tracks_held_button(monkeypatch):
    monkeypatch.setattr(s.controller, "mouse_left_down", lambda ser: None)
    s._held_buttons.clear()
    result = s.left_mouse_down()
    assert result == {"ok": True}
    assert "left" in s._held_buttons


def test_left_mouse_up_clears_held_button(monkeypatch):
    monkeypatch.setattr(s.controller, "mouse_left_up", lambda ser: None)
    s._held_buttons.clear()
    s._held_buttons.add("left")
    result = s.left_mouse_up()
    assert result == {"ok": True}
    assert "left" not in s._held_buttons


def test_right_mouse_down_tracks_held_button(monkeypatch):
    monkeypatch.setattr(s.controller, "mouse_right_down", lambda ser: None)
    s._held_buttons.clear()
    result = s.right_mouse_down()
    assert result == {"ok": True}
    assert "right" in s._held_buttons


def test_right_mouse_up_clears_held_button(monkeypatch):
    monkeypatch.setattr(s.controller, "mouse_right_up", lambda ser: None)
    s._held_buttons.clear()
    s._held_buttons.add("right")
    result = s.right_mouse_up()
    assert result == {"ok": True}
    assert "right" not in s._held_buttons


def test_key_down_tracks_held_key(monkeypatch):
    monkeypatch.setattr(s.controller, "key_down", lambda ser, key: None)
    s._held_keys.clear()
    result = s.key_down("LEFT")
    assert result == {"ok": True}
    assert "LEFT" in s._held_keys


def test_key_up_clears_held_key(monkeypatch):
    monkeypatch.setattr(s.controller, "key_up", lambda ser, key: None)
    s._held_keys.clear()
    s._held_keys.add("LEFT")
    result = s.key_up("LEFT")
    assert result == {"ok": True}
    assert "LEFT" not in s._held_keys


def test_release_all_releases_tracked_buttons_and_keys(monkeypatch):
    released = []
    monkeypatch.setattr(s.controller, "mouse_left_up", lambda ser: released.append("mouse_left_up"))
    monkeypatch.setattr(s.controller, "mouse_right_up", lambda ser: released.append("mouse_right_up"))
    monkeypatch.setattr(s.controller, "key_up", lambda ser, key: released.append(f"key_up:{key}"))
    s._held_buttons.clear()
    s._held_buttons.update({"left", "right"})
    s._held_keys.clear()
    s._held_keys.update({"LEFT", "SHIFT"})
    result = s.release_all()
    assert result["ok"] is True
    assert set(result["released_buttons"]) == {"left", "right"}
    assert set(result["released_keys"]) == {"LEFT", "SHIFT"}
    assert s._held_buttons == set()
    assert s._held_keys == set()
    assert set(released) == {"mouse_left_up", "mouse_right_up", "key_up:LEFT", "key_up:SHIFT"}


def test_release_all_noop_when_nothing_held():
    s._held_buttons.clear()
    s._held_keys.clear()
    result = s.release_all()
    assert result == {"ok": True, "released_buttons": [], "released_keys": []}
