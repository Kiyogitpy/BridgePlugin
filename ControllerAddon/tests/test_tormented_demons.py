"""The Tormented Demon fight, as pure decisions.

Every constant here is PORTED, not derived -- from ZombieLite's own
`com.salvation.plugins.tormenteddemons` (which the user has already corrected against live play),
cross-checked against three of the user's own recordings on 2026-09-20/21. The recordings are the
reason each number below has a measurement beside it rather than a wiki citation.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tormented_demons as td


# --- the fire bomb: the mechanic that kills an unattended routine -------------------------------

def test_a_fireball_graphics_object_becomes_a_bomb_that_detonates_five_ticks_later():
    """MEASURED in the user's three recordings: the bind chat line and FOUR ground-graphics records
    land in the same instant, 45 binds out of 45. Two of those records carry id 2856 and two carry
    1446, and each PAIR sits on one tile -- so four records are two bomb tiles, never four.

    The five ticks are ZombieLite's (`TormentedDemonFireBall.damageTick = tickCount + 5`). The wiki
    says the player has 2 ticks, which is the time to MOVE, not the time until it lands."""
    objects = [
        {"id": td.FIREBALL_GRAPHICS_OBJECT_ID, "x": 4039, "y": 4417, "plane": 0},
        {"id": td.FIREBALL_GRAPHICS_OBJECT_ID, "x": 4039, "y": 4418, "plane": 0},
        {"id": 1446, "x": 4039, "y": 4417, "plane": 0},        # the paired graphic, not a bomb
        {"id": 9999, "x": 4050, "y": 4400, "plane": 0},        # something else entirely
    ]
    bombs = td.bombs_from(objects, now_tick=100)

    assert [(b.x, b.y) for b in bombs] == [(4039, 4417), (4039, 4418)]
    assert all(b.detonates_tick == 105 for b in bombs), "spawn + FIREBALL_DELAY_TICKS"
    assert all(b.plane == 0 for b in bombs)


def test_only_the_tile_you_stand_on_hurts_you():
    """ZombieLite's danger test is `worldPoint.equals(player.getWorldLocation())` -- the blast is
    the single tile, not a 3x3. That is what makes a ONE-tile step a complete dodge, and it matches
    the recordings: the user ends up 1 tile from the nearest bomb far more often than 2 or 3."""
    bombs = [td.Bomb(4039, 4417, 0, 105), td.Bomb(4039, 4418, 0, 105)]

    assert td.in_danger((4039, 4417, 0), bombs) is True
    assert td.in_danger((4039, 4418, 0), bombs) is True
    assert td.in_danger((4040, 4417, 0), bombs) is False, "one tile away is already safe"
    assert td.in_danger((4039, 4417, 1), bombs) is False, "a different plane is a different tile"


def test_a_bomb_that_has_already_gone_off_is_not_a_reason_to_move():
    bombs = [td.Bomb(4039, 4417, 0, 105)]
    assert td.in_danger((4039, 4417, 0), bombs, now_tick=104) is True
    assert td.in_danger((4039, 4417, 0), bombs, now_tick=105) is False, "it has landed"
    assert td.in_danger((4039, 4417, 0), bombs, now_tick=200) is False


def test_the_dodge_is_the_nearest_tile_that_is_not_a_bomb():
    """One step, because one step is enough. Nothing is gained by running further and the recordings
    show the user does not: the step has to fit inside two ticks."""
    bombs = [td.Bomb(4039, 4417, 0, 105), td.Bomb(4039, 4418, 0, 105)]
    walkable = {(4039, 4417, 0), (4039, 4418, 0), (4040, 4417, 0), (4041, 4417, 0)}

    step = td.dodge((4039, 4417, 0), bombs, walkable)

    assert step == (4040, 4417, 0), "adjacent and off both bomb tiles"


def test_the_dodge_refuses_to_step_onto_the_other_bomb():
    """Two bombs land together and they can be adjacent. Stepping off one onto the other is the
    obvious way to write this wrong."""
    bombs = [td.Bomb(4039, 4417, 0, 105), td.Bomb(4040, 4417, 0, 105)]
    walkable = {(4039, 4417, 0), (4040, 4417, 0), (4039, 4416, 0)}

    assert td.dodge((4039, 4417, 0), bombs, walkable) == (4039, 4416, 0)


def test_standing_somewhere_safe_already_is_not_a_dodge():
    bombs = [td.Bomb(4039, 4417, 0, 105)]
    walkable = {(4039, 4417, 0), (4040, 4417, 0)}

    assert td.dodge((4040, 4417, 0), bombs, walkable) is None, "do not move for nothing"


def test_no_safe_tile_says_so_rather_than_inventing_one():
    """The wiki warns some spots offer as few as three safe tiles. Returning a bomb tile because
    nothing else was walkable would be worse than admitting it."""
    bombs = [td.Bomb(4039, 4417, 0, 105), td.Bomb(4040, 4417, 0, 105)]
    walkable = {(4039, 4417, 0), (4040, 4417, 0)}

    assert td.dodge((4039, 4417, 0), bombs, walkable) is None


# --- the prayer swap: which style to attack with, and which prayer to protect with --------------

def test_the_style_to_use_is_the_one_of_your_pair_it_is_not_praying_against():
    """ZombieLite `TormentedDemonSwapStyles.required`. You bring TWO styles; the demon copies the
    last one that damaged it, so the answer is always the other half of your pair. This is the rule
    behind the user's recordings alternating Arclight and blowpipe 87% of the time."""
    pair = td.SwapPair(td.MELEE, td.RANGED)

    assert pair.required(td.MELEE) == td.RANGED
    assert pair.required(td.RANGED) == td.MELEE


def test_a_demon_praying_against_neither_of_your_styles_leaves_both_open():
    """It prays magic because a thrall hit it with magic. Both halves of a melee/ranged pair are
    open, and the primary is the answer -- that only matters to a caller holding a third style."""
    pair = td.SwapPair(td.MELEE, td.RANGED)
    assert pair.required(td.MAGIC) == td.MELEE
    assert pair.required(None) == td.MELEE, "overhead unreadable: keep hitting, do not stall"


def test_an_overhead_prayer_name_decodes_to_the_style_it_protects_from():
    """The bridge reports the decoded HeadIcon name. Anything else -- a name we do not know, or the
    key missing entirely because the client could not decode it -- must read as None, never as a
    style, or the routine would swap on a guess."""
    assert td.style_from_overhead("MELEE") == td.MELEE
    assert td.style_from_overhead("RANGED") == td.RANGED
    assert td.style_from_overhead("MAGIC") == td.MAGIC
    assert td.style_from_overhead(None) is None
    assert td.style_from_overhead("RETRIBUTION") is None


def test_the_demons_own_attack_style_is_readable_from_its_spot_animation():
    """This is what makes protection prayers automatable at all: the demon announces the style it
    is attacking with. All three ids were observed in the user's recordings -- 2851 melee 131 times
    and 2858 ranged 64 times in the longest session alone."""
    assert td.style_from_spotanim(td.SPOTANIM_ATTACK_MELEE) == td.MELEE
    assert td.style_from_spotanim(td.SPOTANIM_ATTACK_RANGED) == td.RANGED
    assert td.style_from_spotanim(td.SPOTANIM_SHIELD_ACTIVE) is None, "the shield is not an attack"
    assert td.style_from_spotanim(None) is None


# --- the 150-damage countdown to the next swap ---------------------------------------------------

def test_damage_dealt_counts_down_to_the_swap_and_the_swap_resets_it():
    """ZombieLite tracks this to warn BEFORE the swap. The reset is driven by the overhead actually
    changing, not by the counter reaching zero -- the counter is the estimate, the overhead is the
    truth, so a missed hitsplat can never desynchronise it permanently."""
    state = td.SwapCounter()
    assert state.damage_left == td.PRAYER_SWAP_DAMAGE

    state.on_hit(40, overhead=td.MELEE)
    state.on_hit(30, overhead=td.MELEE)
    assert state.damage_left == 80

    state.on_hit(10, overhead=td.RANGED)   # it swapped: the overhead is what says so
    assert state.damage_left == td.PRAYER_SWAP_DAMAGE


def test_the_countdown_never_promises_more_than_the_demon_has_left():
    """150 to the swap is meaningless when the demon only has 60 hitpoints -- it dies first, and a
    routine that waits for a swap that cannot come would stand there holding the wrong style."""
    state = td.SwapCounter()
    assert state.damage_left_capped(hitpoints=60) == 60
    state.on_hit(20, overhead=td.MELEE)
    assert state.damage_left_capped(hitpoints=600) == 130


# --- the bomb cycle: a BOUND, never a prediction -------------------------------------------------

def test_the_next_bomb_is_reported_as_a_range_because_the_attack_count_is_not_fixed():
    """ZombieLite logged 10, 9, 8, 10, 8, 9 attacks per cycle in six cycles and could tie the count
    to nothing it could see. So the honest answer is a window. Stating a single tick would read as
    a prediction and the bomb would land outside it one time in three."""
    early = td.bomb_window(attacks_since_bomb=0, last_attack_tick=100)
    assert early.latest > early.earliest, "a real window, not a point"
    assert early.earliest == 100 + 8 * td.ATTACK_SPEED_TICKS + td.BOMB_GAP_TICKS
    assert early.latest == 100 + 10 * td.ATTACK_SPEED_TICKS + td.BOMB_GAP_TICKS


def test_once_the_demon_is_inside_the_band_the_answer_is_simply_soon():
    """Past MIN_ATTACKS the next attack could be the last before the bomb, so there is no number
    left to give. 'Soon' is what the routine needs to stop committing to a long action."""
    assert td.bomb_window(attacks_since_bomb=8, last_attack_tick=100).imminent is True
    assert td.bomb_window(attacks_since_bomb=0, last_attack_tick=100).imminent is False


# --- the demon's ANIMATION is the attack signal, and /npcs already carries it --------------------

def test_the_attack_animations_name_the_style_and_the_bomb():
    """RuneLite's own gameval AnimationID, which is what ZombieLite keys on. These ids are the
    reason no Java change is needed to read the demon's style: `/npcs` has carried `animationId`
    since the Gauntlet work.

    CROSS-VALIDATED against the user's longest recording, where the animation counts and the
    spot-animation counts agree to within one: melee 11392 x131 beside spotanim 2851 x131, ranged
    11389 x63 beside spotanim 2858 x64."""
    assert td.style_from_animation(td.ANIM_ATTACK_MELEE) == td.MELEE
    assert td.style_from_animation(td.ANIM_ATTACK_RANGED) == td.RANGED
    assert td.style_from_animation(td.ANIM_ATTACK_MAGIC) == td.MAGIC

    assert (td.ANIM_ATTACK_MELEE, td.ANIM_ATTACK_RANGED, td.ANIM_ATTACK_MAGIC) == (11392, 11389, 11388)


def test_the_non_attack_animations_are_not_mistaken_for_a_style():
    """Walking, standing ready, defending, dying and spawning must all read as "not an attack" --
    praying against a death animation would leave the wrong prayer up for the next real one."""
    for animation in (td.ANIM_WALK, td.ANIM_READY, td.ANIM_DEFEND, td.ANIM_DEATH, td.ANIM_SUMMON,
                      td.ANIM_FIRE_BOMB, -1, None):
        assert td.style_from_animation(animation) is None, animation


def test_the_fire_bomb_animation_is_its_own_question():
    """The bomb is not a style to pray against, it is a cue to MOVE. It gets its own predicate so a
    caller cannot accidentally treat it as an attack to protect from."""
    assert td.is_fire_bomb(td.ANIM_FIRE_BOMB) is True
    assert td.is_fire_bomb(td.ANIM_ATTACK_MELEE) is False
    assert td.is_fire_bomb(None) is False


def test_a_demon_is_recognised_by_name_not_by_id():
    """MEASURED: id 13600 for the whole of one session, 13599 for two others, and 13604 transforming
    into 13600 at one npc index. ZombieLite's own id table stops at 13602, so an id list would have
    silently missed a demon the user actually fought."""
    assert td.is_demon({"name": "Tormented Demon", "id": 13604}) is True
    assert td.is_demon({"name": "Tormented Demon", "id": 99999}) is True, "an unknown id still counts"
    assert td.is_demon({"name": "Greater Demon", "id": 13599}) is False
    assert td.is_demon({}) is False
    assert td.is_demon(None) is False
