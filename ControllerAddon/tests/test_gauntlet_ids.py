import gauntlet_ids as g


def test_npc_id_families_are_disjoint():
    families = [g.HUNLLEF, g.TORNADO, g.WEAK_MONSTERS, set(g.DEMI_BOSSES)]
    for i, a in enumerate(families):
        for b in families[i + 1:]:
            assert not (a & b)


def test_pinned_values_from_zombielite_and_the_recording():
    assert g.HUNLLEF_CORRUPTED == {9035, 9036, 9037, 9038}
    assert 9039 in g.TORNADO and 9025 in g.TORNADO
    assert g.ANIM_ATTACK == 8419 and g.ANIM_SWITCH_STYLE == 8754
    assert g.PROJ_PRAYER_ATTACK == {1713, 1714}
    assert g.HAZARD_TILES == {36047, 36048}
    assert g.VARBIT_FIGHTING_HUNLLEF == 9177
    assert g.REGION_CORRUPTED == 7768
    assert g.OBJ_REWARD_CHEST == 37341


def test_attack_counting_constants():
    assert g.HUNLLEF_ATTACK_PERIOD_TICKS == 5
    assert g.HUNLLEF_ATTACKS_PER_STYLE == 4
    assert g.ANIM_ATTACK_LIKE == {g.ANIM_ATTACK, g.ANIM_SPECIAL_PRAYER}


# I5 (final-fix-brief.md): every object id from the recording's own "object ids seen" list,
# copied here verbatim (hermetic -- this test must not re-open the gitignored .summary file) so
# a future edit to gauntlet_ids.py can't silently drop one.
RECORDING_OBJECT_IDS = [
    35966, 35967, 35968, 35969, 35970, 35971, 35972, 35973, 35974, 35975, 35976,
    35977, 35980, 35981, 35992, 35994, 35998, 35999, 36000, 36001,
    37337, 37340, 37341,
]

# Ids the recording saw but that carry no name at all ("null" in the summary dump) -- nothing to
# build a constant or a dict entry from, so they are named here rather than silently dropped.
IGNORED_OBJECT_IDS = {
    36000: "null name in the recording's object dump -- summary could not resolve a display name",
    36001: "null name in the recording's object dump -- summary could not resolve a display name",
}


def test_every_recorded_object_id_is_classified_exactly_once():
    obj_star_ids = {v for k, v in vars(g).items() if k.startswith("OBJ_") and isinstance(v, int)}
    resource_ids = set(g.RESOURCE_NODES)
    depleted_ids = set(g.RESOURCE_NODES_DEPLETED)
    ignored_ids = set(IGNORED_OBJECT_IDS)

    # The four buckets must not overlap -- an id counted twice would hide a real ambiguity the
    # same way "exactly one" below is meant to catch.
    buckets = [obj_star_ids, resource_ids, depleted_ids, ignored_ids]
    for i, a in enumerate(buckets):
        for b in buckets[i + 1:]:
            assert not (a & b), f"ids counted in more than one bucket: {a & b}"

    all_known = obj_star_ids | resource_ids | depleted_ids | ignored_ids
    for oid in RECORDING_OBJECT_IDS:
        assert oid in all_known, f"{oid} is not an OBJ_* constant, in RESOURCE_NODES(_DEPLETED), or in IGNORED_OBJECT_IDS"


def test_hunllef_prayer_by_id_covers_both_variants():
    assert g.HUNLLEF_PRAYER_BY_ID[9035] == "MELEE"
    assert g.HUNLLEF_PRAYER_BY_ID[9036] == "RANGED"
    assert g.HUNLLEF_PRAYER_BY_ID[9037] == "MAGIC"
    assert g.HUNLLEF_PRAYER_BY_ID[9038] == "DEAD"
    assert set(g.HUNLLEF_PRAYER_BY_ID) == g.HUNLLEF
    assert g.HUNLLEF_PRAYER_BY_ID[9022] == "RANGED"


def test_fight_constants():
    assert g.ANIM_MARKER == {g.ANIM_SWITCH_STYLE, g.ANIM_SPECIAL_TORNADO}
    assert not (g.ANIM_MARKER & g.ANIM_ATTACK_LIKE)
    assert g.HUNLLEF_SIZE == 5
    assert g.FLOOR_WARNING == 36047 and g.FLOOR_HIT == 36048
    assert g.HAZARD_TILES == {g.FLOOR_WARNING, g.FLOOR_HIT}
    assert g.FISH_NAMES[0] == "Corrupted paddlefish"
    assert g.BOW_NAMES[0] == "Corrupted bow (perfected)"
    assert g.STAFF_NAMES[0] == "Corrupted staff (perfected)"
