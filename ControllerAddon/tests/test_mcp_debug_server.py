import os
import queue
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# mcp_debug_server opens a real UDP socket at import time -- stub it before importing, so
# these pure-logic tests run without any network binding.
import controller
controller.make_udp_listener = MagicMock(return_value=MagicMock())
# A real background thread (_event_listener_loop) runs for the whole test session against
# this stub, polling it every 0.05s. That's harmless only because poll_udp is pinned to
# always return [] -- if that's ever changed to return real events, the queue-draining
# assertions in test_poll_events_drains_queued_events could become order-sensitive/flaky
# against the live thread racing to drain the same queue.
controller.poll_udp = MagicMock(return_value=[])

import mcp_debug_server as s  # noqa: E402

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


def test_get_cursor_position_reads_from_controller(monkeypatch):
    monkeypatch.setattr(s.controller, "get_cursor_pos", lambda: (512, 340))
    assert s.get_cursor_position() == {"x": 512, "y": 340}


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
