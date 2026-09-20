import pytest
import requests

import controller


def pytest_configure(config):
    # Registered so the opt-out in no_live_bridge below doesn't warn.
    config.addinivalue_line(
        "markers",
        "real_bridge_helper: test exercises a controller bridge helper itself, so the "
        "hermetic no_live_bridge fixture must not stub it out",
    )


@pytest.fixture(autouse=True)
def no_live_bridge(request, monkeypatch):
    """Keep the WHOLE suite hermetic -- several controller helpers reach the RUNNING CLIENT over
    HTTP when a test does not stub them, which makes results depend on the live game.

    PROMOTED FROM test_travel.py TO conftest 2026-09-16, after the same class of bug bit a third
    time in three different files:

    - `get_collision` (2026-08-29): two door tests passed alone and failed in a full run, and the
      same test flipped between runs as the live player walked between houses.
    - `is_point_within_window` -> `get_state` (2026-09-16): the USER sized the RuneLite window down
      to 844x910 to fit VS Code beside it, and two unrelated tests went red --
      test_click_entity_departs_from_the_refreshed_position_not_the_stale_one aims at (1180, 660)
      and started reporting "aim point is outside the RuneLite window". A suite that passes or
      fails according to how someone arranged their windows is not testing the code.

    Both defaults are the documented "the client told us nothing" answer, which is also the SAFE
    one and matches production:

    - an empty collision result reads as "cannot tell" to every consumer (route_to, can_reach,
      _collision_flags, ...), never as "blocked";
    - is_point_within_window itself fails OPEN when the window fields are missing ("can't verify --
      fail open rather than block every click"), so True is what a real client-less run does.

    A test that wants different behaviour simply stubs it -- an autouse fixture runs BEFORE the
    test body, so any monkeypatch.setattr inside the test wins. A test exercising one of these
    helpers directly marks itself `real_bridge_helper`."""
    if request.node.get_closest_marker("real_bridge_helper"):
        return
    monkeypatch.setattr(controller, "get_collision",
                        lambda x, y, plane, radius=2, inst=False: [], raising=False)
    monkeypatch.setattr(controller, "is_point_within_window",
                        lambda x, y: True, raising=False)


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    """The ROOT of what no_live_bridge above patches helper by helper: inside a test, a real HTTP
    request behaves exactly like a bridge that is DOWN.

    MEASURED 2026-09-20 with RuneLite running, by spying on requests for one full run: 88 tests
    across 9 files made real GETs (/state 29, /equipment 14, /bank 9, /objects 8,
    /api/player/movement 8, ...). They pass either way only because controller.api_get swallows
    the error and answers None -- so each of them was reading the user's live screen, inventory
    and bank whenever the client happened to be open. One finally failed on a clean HEAD:
    test_travel's hop test, whose fake click point (50,50) landed on one of the user's REAL
    minimap orbs from GET /state. That was the FOURTH bite of this bug (see no_live_bridge).

    ConnectionError is what a closed client raises, so nothing changes for a run without a
    client, and a run WITH one now sees the same world. A test that fakes HTTP replaces
    controller._session / api_get itself, and that still wins (fixtures run before the body).
    Files with a stricter _no_live_bridge (api_get raises AssertionError) keep it: refusing
    loudly is better where every read is meant to be faked."""
    def refuse(self, method, url, *args, **kwargs):
        raise requests.ConnectionError(f"tests are hermetic: no real {method} {url}")
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)

