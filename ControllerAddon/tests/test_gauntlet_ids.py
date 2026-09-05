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
