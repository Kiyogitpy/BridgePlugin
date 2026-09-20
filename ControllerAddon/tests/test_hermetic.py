"""The suite must not talk to a running RuneLite client -- whatever the test forgot to fake."""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller


def test_a_real_http_request_inside_a_test_behaves_like_a_bridge_that_is_down():
    """MEASURED 2026-09-20 with the client running: 88 tests across 9 files made real GETs
    (/state 29, /equipment 14, /bank 9, /objects 8, ...). They passed either way only because a
    DOWN bridge answers None -- and one hop test did fail, its fake click point landing on one of
    the user's real minimap orbs. conftest makes every request raise ConnectionError, which is
    byte-for-byte what a closed client does, so a test reads the same world in both cases."""
    with pytest.raises(requests.ConnectionError):
        requests.get("http://127.0.0.1:8080/state", timeout=0.3)
    assert controller.api_get("/state") is None
    assert controller.get_inventory() == []
