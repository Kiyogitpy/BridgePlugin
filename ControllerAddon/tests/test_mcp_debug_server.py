import os
import queue
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# mcp_debug_server opens a real UDP socket at import time -- stub it before importing, so
# these pure-logic tests run without any network binding. The Arduino serial port is opened
# lazily instead (see _get_serial() in the module itself), not at import time, but is still
# stubbed here since most tests below exercise action tools that need *a* serial handle and
# don't care which -- individual tests that care about serial-unavailable behavior override
# `s.ser`/`s.serial.Serial` themselves via monkeypatch.
import serial
serial.Serial = MagicMock(return_value=MagicMock())

import controller
controller.make_udp_listener = MagicMock(return_value=MagicMock())
# A real background thread (_event_listener_loop) runs for the whole test session against
# this stub, polling it every 0.05s. That's harmless only because poll_udp is pinned to
# always return [] -- if that's ever changed to return real events, the queue-draining
# assertions in test_poll_events_drains_queued_events could become order-sensitive/flaky
# against the live thread racing to drain the same queue.
controller.poll_udp = MagicMock(return_value=[])

import mcp_debug_server as s  # noqa: E402

# Pre-open the serial handle once so the ~35 existing action-tool tests below (which don't
# care about serial specifically) don't each pay _get_serial()'s real 2s Pro Micro reset
# sleep on whichever of them happens to run first.
s.ser = MagicMock()
# Same idea for the UDP listener: mark it already-started so poll_events() tests that set
# s._udp_socket directly aren't overwritten by a real (if mocked) _ensure_udp_listener() bind
# attempt. Tests that specifically exercise the lazy-bind path reset this themselves.
s._udp_listener_started = True
s._udp_socket = MagicMock()


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


def test_drag_rejects_invalid_button():
    result = s.drag(0, 0, 10, 10, button="middle")
    assert result == {"ok": False, "error": "button must be 'left' or 'right'"}


def test_drag_rejects_out_of_window_start(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: (x, y) != (-1, -1))
    move_calls = []
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: move_calls.append((x, y)))
    result = s.drag(-1, -1, 10, 10)
    assert result["ok"] is False
    assert move_calls == []


def test_drag_rejects_out_of_window_end(monkeypatch):
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: (x, y) != (999, 999))
    move_calls = []
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: move_calls.append((x, y)))
    result = s.drag(0, 0, 999, 999)
    assert result["ok"] is False
    assert move_calls == []


def test_drag_presses_moves_and_releases_left_button(monkeypatch):
    events = []
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(s.controller, "move_to_human", lambda ser, x, y, **kwargs: events.append(("move", x, y)))
    monkeypatch.setattr(s.controller, "mouse_left_down", lambda ser: events.append(("down",)))
    monkeypatch.setattr(s.controller, "mouse_left_up", lambda ser: events.append(("up",)))
    s._held_buttons.clear()
    result = s.drag(1, 2, 3, 4)
    assert result == {"ok": True}
    assert events == [("move", 1, 2), ("down",), ("move", 3, 4), ("up",)]
    assert "left" not in s._held_buttons


def test_drag_releases_button_even_if_move_raises(monkeypatch):
    events = []
    move_calls = []
    monkeypatch.setattr(s.controller, "is_point_within_window", lambda x, y: True)

    def move_maybe_raise(ser, x, y, **kwargs):
        move_calls.append((x, y))
        events.append(("move", x, y))
        if len(move_calls) == 2:  # the move to (x2, y2)
            raise RuntimeError("boom")

    monkeypatch.setattr(s.controller, "move_to_human", move_maybe_raise)
    monkeypatch.setattr(s.controller, "mouse_right_down", lambda ser: events.append(("down",)))
    monkeypatch.setattr(s.controller, "mouse_right_up", lambda ser: events.append(("up",)))
    s._held_buttons.clear()
    raised = False
    try:
        s.drag(1, 2, 3, 4, button="right")
    except RuntimeError:
        raised = True
    assert raised, "expected RuntimeError to propagate"
    assert ("up",) in events
    assert "right" not in s._held_buttons


def test_poll_events_returns_error_when_listener_unavailable(monkeypatch):
    monkeypatch.setattr(s, "_udp_socket", None)
    monkeypatch.setattr(s, "_udp_error", "port already in use")
    result = s.poll_events()
    assert result == {"error": "UDP listener not available: port already in use"}


def test_poll_events_drains_queued_events(monkeypatch):
    monkeypatch.setattr(s, "_udp_socket", object())  # any non-None sentinel
    test_queue = queue.Queue()
    test_queue.put(("PLAYER_DIED", None))
    test_queue.put(("HP_CHANGED", {"hp": 10}))
    monkeypatch.setattr(s, "_event_queue", test_queue)
    result = s.poll_events()
    assert result == {"events": [
        {"type": "PLAYER_DIED", "payload": None},
        {"type": "HP_CHANGED", "payload": {"hp": 10}},
    ]}
    assert test_queue.empty()


def test_poll_events_returns_empty_list_when_nothing_queued(monkeypatch):
    monkeypatch.setattr(s, "_udp_socket", object())
    monkeypatch.setattr(s, "_event_queue", queue.Queue())
    result = s.poll_events()
    assert result == {"events": []}


def test_get_serial_reuses_already_open_handle(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(s, "ser", sentinel)
    open_mock = MagicMock()
    monkeypatch.setattr(s.serial, "Serial", open_mock)
    ser_result, err = s._get_serial()
    assert ser_result is sentinel
    assert err is None
    open_mock.assert_not_called()


def test_get_serial_opens_and_caches_on_first_success(monkeypatch):
    monkeypatch.setattr(s, "ser", None)
    fake_handle = MagicMock()
    monkeypatch.setattr(s.serial, "Serial", MagicMock(return_value=fake_handle))
    monkeypatch.setattr(s.time, "sleep", lambda secs: None)
    ser_result, err = s._get_serial()
    assert ser_result is fake_handle
    assert err is None
    assert s.ser is fake_handle


def test_get_serial_returns_error_without_raising_when_port_busy(monkeypatch):
    monkeypatch.setattr(s, "ser", None)
    monkeypatch.setattr(
        s.serial, "Serial",
        MagicMock(side_effect=s.serial.SerialException("port busy")),
    )
    ser_result, err = s._get_serial()
    assert ser_result is None
    assert err["ok"] is False
    assert "port busy" in err["error"]


def test_get_serial_retries_after_a_previous_failure(monkeypatch):
    # A failed open must not be cached -- once controller.py stops holding the port, the very
    # next action-tool call should succeed without restarting the MCP server.
    monkeypatch.setattr(s, "ser", None)
    fake_handle = MagicMock()
    monkeypatch.setattr(
        s.serial, "Serial",
        MagicMock(side_effect=[s.serial.SerialException("port busy"), fake_handle]),
    )
    monkeypatch.setattr(s.time, "sleep", lambda secs: None)

    first_ser, first_err = s._get_serial()
    assert first_ser is None
    assert first_err is not None

    second_ser, second_err = s._get_serial()
    assert second_ser is fake_handle
    assert second_err is None


def test_bridge_get_works_when_serial_port_unavailable(monkeypatch):
    monkeypatch.setattr(s, "ser", None)
    monkeypatch.setattr(
        s.serial, "Serial",
        MagicMock(side_effect=s.serial.SerialException("port busy")),
    )
    monkeypatch.setattr(s, "_bridge_get_impl", lambda path, params: {"loggedIn": True})
    result = s.bridge_get("/state")
    assert result == {"loggedIn": True}


def test_left_click_returns_clear_error_when_serial_port_unavailable(monkeypatch):
    monkeypatch.setattr(s, "ser", None)
    monkeypatch.setattr(
        s.serial, "Serial",
        MagicMock(side_effect=s.serial.SerialException("port busy")),
    )
    result = s.left_click()
    assert result["ok"] is False
    assert "port busy" in result["error"]


def test_ensure_udp_listener_binds_once_and_caches(monkeypatch):
    monkeypatch.setattr(s, "_udp_listener_started", False)
    monkeypatch.setattr(s, "_udp_socket", None)
    fake_socket = object()
    bind_mock = MagicMock(return_value=fake_socket)
    monkeypatch.setattr(s.controller, "make_udp_listener", bind_mock)
    s._ensure_udp_listener()
    assert s._udp_socket is fake_socket
    bind_mock.assert_called_once()

    s._ensure_udp_listener()  # second call must not rebind
    bind_mock.assert_called_once()


def test_ensure_udp_listener_records_error_without_raising_on_bind_failure(monkeypatch):
    monkeypatch.setattr(s, "_udp_listener_started", False)
    monkeypatch.setattr(s, "_udp_socket", None)
    monkeypatch.setattr(
        s.controller, "make_udp_listener",
        MagicMock(side_effect=OSError("port already in use")),
    )
    s._ensure_udp_listener()
    assert s._udp_socket is None
    assert "port already in use" in s._udp_error


def test_poll_events_triggers_lazy_bind_on_first_call(monkeypatch):
    monkeypatch.setattr(s, "_udp_listener_started", False)
    monkeypatch.setattr(s, "_udp_socket", None)
    monkeypatch.setattr(s, "_event_queue", queue.Queue())
    fake_socket = object()
    monkeypatch.setattr(s.controller, "make_udp_listener", MagicMock(return_value=fake_socket))
    result = s.poll_events()
    assert result == {"events": []}
    assert s._udp_socket is fake_socket


def test_event_listener_loop_stops_and_clears_socket_on_exception(monkeypatch):
    monkeypatch.setattr(s, "_udp_socket", object())  # any non-None sentinel

    def raising_poll_udp(sock):
        raise OSError("recvfrom failed")

    monkeypatch.setattr(s.controller, "poll_udp", raising_poll_udp)

    # The fix makes _event_listener_loop return after one failed iteration, so it's safe to
    # call directly (no thread needed) -- it must not loop forever or re-raise.
    s._event_listener_loop()

    assert s._udp_socket is None
    result = s.poll_events()
    assert "error" in result
