import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import teleports


# --- parsing across the three tables -------------------------------------------------------

def test_loads_all_three_vendored_tables_with_their_different_column_layouts():
    """The tables do NOT share a column order -- items has no Origin, boxes has no Varbits,
    poh_portals has both. A fixed-index parser would mis-read two of the three, so columns are
    keyed by HEADER NAME."""
    ts = teleports.load_all()
    kinds = {t.kind for t in ts}
    assert kinds == {"item", "box", "poh_portal"}, kinds
    assert len(ts) > 400


def test_an_item_teleport_has_no_origin_and_a_box_edge_does():
    """This is the distinction the single-hop model missed: an item teleport works from
    anywhere, a jewellery box only from inside the POH. Origin is what makes chaining
    possible at all."""
    ferox_ring = next(t for t in teleports.load_all()
                      if t.display == "Ring of dueling: Ferox Enclave")
    assert ferox_ring.origin is None
    assert ferox_ring.dest == (3151, 3635, 0)

    ferox_box = next(t for t in teleports.load_all()
                     if t.kind == "box" and t.dest == (3151, 3635, 0))
    assert ferox_box.origin == (1870, 5698, 0)
    assert "Jewellery Box" in ferox_box.object_info


def test_varbit_requirements_parse_as_id_op_value_anded():
    """`2187=3;4744=0` -- semicolon is AND. This is how the house tablet's ten rows are
    disambiguated down to the one that applies to this account."""
    rows = [t for t in teleports.load_all() if "House tablet" in t.display and t.varbits]
    assert rows
    gated = [t for t in rows if any(v == (2187, "=", 3) for v in t.varbits)]
    assert gated, "expected rows gated on house-location varbit 2187"


# --- requirement checking -------------------------------------------------------------------

def _poh(**kw):
    return teleports.PohConfig(**kw)


def _in_poh(t):
    return 1800 <= t[0] <= 2000 and 5600 <= t[1] <= 5800


def _poh_only_router(a, b):
    """Walk nowhere except inside the POH -- so a route can only come from chaining."""
    if a[:2] == b[:2]:
        return 0
    if _in_poh(a) and _in_poh(b):
        return 64
    return None


def test_varbit_gate_selects_the_one_house_row_that_applies():
    live = {2187: 3, 4744: 0}
    rows = [t for t in teleports.load_all() if "House tablet" in t.display]
    ok = [t for t in rows if teleports.varbits_satisfied(t, live)]
    rejected = [t for t in rows if not teleports.varbits_satisfied(t, live)]
    assert ok and rejected, "the gate must actually exclude the other locations"
    for t in ok:
        for vid, op, val in t.varbits:
            assert live.get(vid) == val


def test_jewellery_box_tier_is_cumulative_exactly_as_shortest_path_computes_it():
    """Copied from PathfinderConfig.checkJewelleryBoxTier: a Basic row is available to ANY
    tier above NONE, Fancy needs Fancy+, Ornate needs Ornate. Getting this backwards would
    hide Ferox (a Basic row) from an ornate box -- which is the route the user named."""
    boxes = [t for t in teleports.load_all() if t.kind == "box"]
    basic = next(t for t in boxes if "Basic Jewellery Box" in t.object_info)
    fancy = next(t for t in boxes if "Fancy Jewellery Box" in t.object_info)
    ornate = next(t for t in boxes if "Ornate Jewellery Box" in t.object_info)

    assert not teleports.poh_allows(basic, _poh(box_tier="NONE"))
    for tier in ("BASIC", "FANCY", "ORNATE"):
        assert teleports.poh_allows(basic, _poh(box_tier=tier)), tier
    assert not teleports.poh_allows(fancy, _poh(box_tier="BASIC"))
    assert teleports.poh_allows(fancy, _poh(box_tier="ORNATE"))
    assert not teleports.poh_allows(ornate, _poh(box_tier="FANCY"))
    assert teleports.poh_allows(ornate, _poh(box_tier="ORNATE"))


def test_a_mounted_glory_is_disabled_by_an_ornate_box():
    """The subtlety worth stealing from PathfinderConfig: an ornate box supersedes the mounted
    glory, so offering both would plan a worse route through a thing you'd never use."""
    glory = next(t for t in teleports.load_all()
                 if t.kind == "box" and "Amulet of Glory" in (t.object_info or ""))
    assert teleports.poh_allows(glory, _poh(box_tier="FANCY", mounted_items=True))
    assert not teleports.poh_allows(glory, _poh(box_tier="ORNATE", mounted_items=True))
    assert not teleports.poh_allows(glory, _poh(box_tier="FANCY", mounted_items=False))


def test_the_poh_master_switch_disables_every_poh_transport():
    boxes = [t for t in teleports.load_all() if t.kind == "box"]
    assert not any(teleports.poh_allows(t, _poh(box_tier="ORNATE", enabled=False)) for t in boxes)


# --- the chain the user named ---------------------------------------------------------------

def test_house_tablet_then_ornate_box_is_a_reachable_two_hop_route_to_ferox():
    """USER 2026-09-07: 'if you'd ask shortest path the answer would have been take house
    tablet then use the ordnate jewlry box to ferox enclave'. The single-hop model could not
    express this at all; this is the regression test for that."""
    HOUSE_TAB = 8013
    legs = teleports.plan(
        start=(3200, 3200, 0),
        goal=(3151, 3635, 0),                       # Ferox Enclave
        owned_ids={HOUSE_TAB},
        varbit_values={2187: 3, 4744: 0},
        poh=_poh(box_tier="ORNATE"),
        # Stub the router: the only walking allowed is INSIDE the POH, so the route can only
        # be found by chaining two transports. MEASURED against the real router: POH template
        # coords carry no map data and "no data defaults to PASSABLE" (CLAUDE.md), so
        # worldmap.find_path((1923,5709) -> (1870,5698)) answers 64 steps. The step count is
        # fictional -- the house interior is instanced and its real layout varies per player --
        # but the conclusion it encodes is right: you can walk from your arrival tile to the
        # jewellery box. That is the one walk this chain needs.
        route_len=_poh_only_router,
    )
    assert legs is not None, "no chained route found"
    assert [l.kind for l in legs] == ["item", "box"]
    assert "House tablet" in legs[0].display
    assert legs[-1].dest == (3151, 3635, 0)
    assert "Jewellery Box" in legs[-1].object_info


def test_the_same_chain_is_refused_without_the_box():
    legs = teleports.plan(
        start=(3200, 3200, 0), goal=(3151, 3635, 0), owned_ids={8013},
        varbit_values={2187: 3, 4744: 0}, poh=_poh(box_tier="NONE"),
        route_len=_poh_only_router,
    )
    assert legs is None


def test_the_same_chain_is_refused_without_the_house_tablet():
    legs = teleports.plan(
        start=(3200, 3200, 0), goal=(3151, 3635, 0), owned_ids=set(),
        varbit_values={2187: 3, 4744: 0}, poh=_poh(box_tier="ORNATE"),
        route_len=_poh_only_router,
    )
    assert legs is None


def test_a_single_hop_still_wins_when_it_is_enough():
    """Chaining must not make the simple case worse: a ring of dueling straight to Ferox is
    one leg, not two."""
    legs = teleports.plan(
        start=(3200, 3200, 0), goal=(3151, 3635, 0), owned_ids={2552, 8013},
        varbit_values={2187: 3, 4744: 0}, poh=_poh(box_tier="ORNATE"),
        route_len=_poh_only_router,
    )
    assert legs is not None and len(legs) == 1
    assert legs[0].display == "Ring of dueling: Ferox Enclave"


def test_walking_wins_when_the_goal_is_already_close():
    """plan() answers None for 'just walk' -- the caller walks. A teleport must earn the trip."""
    legs = teleports.plan(
        start=(3140, 3630, 0), goal=(3151, 3635, 0), owned_ids={2552},
        varbit_values={}, poh=_poh(),
        route_len=lambda a, b: 12,          # everything is 12 tiles apart
    )
    assert legs is None


def test_hop_count_is_bounded():
    assert teleports.MAX_HOPS >= 2
    legs = teleports.plan(
        start=(3200, 3200, 0), goal=(3151, 3635, 0), owned_ids={8013},
        varbit_values={2187: 3, 4744: 0}, poh=_poh(box_tier="ORNATE"),
        route_len=_poh_only_router,
    )
    assert legs is None or len(legs) <= teleports.MAX_HOPS
