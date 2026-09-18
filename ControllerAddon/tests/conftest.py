import pytest

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
