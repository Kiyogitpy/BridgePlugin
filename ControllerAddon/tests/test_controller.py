import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller


class _FakeResponse:
    def __init__(self, json_body):
        self._json_body = json_body

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_body


def test_api_get_uses_shared_session_not_a_fresh_connection_per_call(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "get_dpi_scale", lambda: 1.0)
    monkeypatch.setattr(
        controller._session, "get",
        lambda url, params=None, timeout=0.3: calls.append((url, params)) or _FakeResponse({}),
    )

    controller.api_get("/state")

    assert len(calls) == 1
    assert calls[0][0] == "http://localhost:8080/state"


def test_get_bank_without_fresh_sends_no_fresh_param(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=0.3: captured.update(params=params) or {"open": False},
    )

    controller.get_bank()

    assert captured["params"] is None


def test_get_bank_with_fresh_true_sends_fresh_param(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=0.3: captured.update(params=params) or {"open": False},
    )

    controller.get_bank(fresh=True)

    assert captured["params"] == {"fresh": "true"}


def test_get_objects_combines_radius_and_fresh_params(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=1.0: captured.update(params=params) or [],
    )

    controller.get_objects(radius=10, fresh=True)

    assert captured["params"] == {"radius": 10, "fresh": "true"}


def test_get_objects_with_no_radius_and_no_fresh_sends_no_params(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        controller, "api_get",
        lambda path, params=None, timeout=1.0: captured.update(params=params) or [],
    )

    controller.get_objects()

    assert captured["params"] is None
