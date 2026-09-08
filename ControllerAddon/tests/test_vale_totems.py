import vale_totems as vt


def _site(**kw):
    base = {
        "siteNumber": 1, "baseValue": 0, "wood": "NONE", "baseCarved": False,
        "bottom": {"value": 0, "spirit": "NONE"},
        "middle": {"value": 0, "spirit": "NONE"},
        "top": {"value": 0, "spirit": "NONE"},
        "decorations": 0, "nearbySpirits": ["BUFFALO", "JAGUAR", "SNAKE"],
        "nearbySpiritValues": [1, 2, 4],
        "durabilityRemaining": 0, "unclaimedOfferings": 0,
    }
    base.update(kw)
    return base


def _payload(*sites):
    return {"sites": list(sites), "researchPoints": 94, "totalUnclaimedOfferings": 385,
            "atValeTotems": True}


def test_parse_state_returns_none_when_the_endpoint_is_unavailable():
    assert vt.parse_state(None) is None


def test_parse_state_returns_none_when_the_reader_errored():
    assert vt.parse_state({"reader": "vale-totems", "error": "boom"}) is None


def test_an_unbuilt_site_needs_a_build_and_nothing_else():
    state = vt.parse_state(_payload(_site()))
    site = state.sites[1]
    assert vt.needs_build(site) is True
    assert site.unclaimed_offerings == 0


def test_missing_spirits_excludes_ones_already_carved():
    # BUFFALO carved (value 1 + 9 == 10) already sits in the bottom segment.
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW",
        bottom={"value": 10, "spirit": "BUFFALO"},
        nearbySpirits=["BUFFALO", "JAGUAR", "SNAKE"],
    ))).sites[1]
    assert vt.carved_spirits(site) == {"BUFFALO"}
    assert vt.missing_spirits(site) == ["JAGUAR", "SNAKE"]


def test_a_fully_carved_totem_still_needs_decorating_until_four_are_on():
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=True, decorations=3))).sites[1]
    assert vt.needs_carve(site) is False
    assert vt.needs_decorate(site) is True

    done = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=True, decorations=4))).sites[1]
    assert vt.needs_decorate(done) is False


def test_base_carved_is_what_ends_carving_not_the_segment_count():
    # The server owns "carving is finished". A totem whose three segments are all carved but
    # whose baseCarved is still false is NOT done -- trusting the segments would stop early.
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=False,
        bottom={"value": 10, "spirit": "BUFFALO"},
        middle={"value": 11, "spirit": "JAGUAR"},
        top={"value": 13, "spirit": "SNAKE"}))).sites[1]
    assert vt.needs_carve(site) is True


def test_state_carries_the_globals():
    state = vt.parse_state(_payload(_site()))
    assert state.research_points == 94
    assert state.total_unclaimed == 385
    assert state.at_vale_totems is True


import vale_totem_route as vtr


def test_claim_outranks_everything_so_offerings_are_never_left_behind():
    site = vt.parse_state(_payload(_site(unclaimedOfferings=65))).sites[1]
    assert vt.next_action(site, logs=10, bows=10) == ("claim", None)


def test_build_is_chosen_once_the_pile_is_claimed():
    site = vt.parse_state(_payload(_site())).sites[1]
    assert vt.next_action(site, logs=10, bows=10) == ("build", None)


def test_build_is_not_chosen_without_a_log():
    site = vt.parse_state(_payload(_site())).sites[1]
    assert vt.next_action(site, logs=0, bows=10) == ("done", None)


def test_carve_names_the_first_missing_spirit():
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW",
        bottom={"value": 10, "spirit": "BUFFALO"},
        nearbySpirits=["BUFFALO", "JAGUAR", "SNAKE"]))).sites[1]
    assert vt.next_action(site, logs=10, bows=10) == ("carve", "JAGUAR")


def test_decorate_is_skipped_with_no_bows_so_the_routine_moves_on_instead_of_spinning():
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=True, decorations=1))).sites[1]
    assert vt.next_action(site, logs=10, bows=2) == ("decorate", None)
    assert vt.next_action(site, logs=10, bows=0) == ("done", None)


def test_a_finished_totem_is_done():
    site = vt.parse_state(_payload(_site(
        baseValue=4, wood="YEW", baseCarved=True, decorations=4,
        durabilityRemaining=5))).sites[1]
    assert vt.next_action(site, logs=10, bows=10) == ("done", None)
    assert vt.site_is_finished(site) is True


def test_logs_needed_for_a_lap_counts_only_the_work_still_outstanding():
    payload = _payload(
        _site(siteNumber=1),                                             # unbuilt: 1 + 4
        _site(siteNumber=2, baseValue=4, wood="YEW", baseCarved=True,
              decorations=4, durabilityRemaining=5),                     # finished: 0
    )
    assert vt.logs_needed_for_lap(vt.parse_state(payload)) == 5
