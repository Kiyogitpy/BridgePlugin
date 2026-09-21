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


# --- reading the demon's protection prayer off an /npcs row --------------------------------------

def test_the_reliable_read_is_preferred_and_the_api_read_is_the_fallback():
    """The bridge reports the same question twice since 2026-09-21. `protectionPrayer` comes from
    OverheadPrayer, which reads the client's own head icon; `overheadPrayer` comes from the public
    API and is only correct when the sprite happened to come from the prayer archive. Prefer the
    reliable one, fall back rather than give up."""
    assert td.protected_style({"protectionPrayer": "MELEE", "overheadPrayer": "MAGIC"}) == td.MELEE
    assert td.protected_style({"overheadPrayer": "RANGED"}) == td.RANGED, "fallback still answers"
    assert td.protected_style({"protectionPrayer": "MAGIC"}) == td.MAGIC


def test_a_demon_with_no_overhead_at_all_reads_as_no_prayer():
    """Both keys absent is the shape of an npc praying nothing, and of a client that could not
    decode it. Neither is a style, and the caller decides what to do about it."""
    assert td.protected_style({}) is None
    assert td.protected_style({"protectionPrayer": None, "overheadPrayer": None}) is None
    assert td.protected_style(None) is None


def test_an_unrecognised_overhead_is_not_silently_turned_into_a_style():
    """Retribution, smite and redemption are real overheads and none of them protect from anything.
    Reading one as a style would swap gear for no reason."""
    assert td.protected_style({"protectionPrayer": "RETRIBUTION"}) is None
    assert td.protected_style({"protectionPrayer": "SMITE", "overheadPrayer": "MELEE"}) == td.MELEE, \
        "an unusable reliable answer still lets the fallback speak"


def test_the_whole_chain_answers_which_style_to_hold():
    """The one question a routine actually asks each tick, end to end: an /npcs row in, the weapon
    to be holding out."""
    pair = td.SwapPair(td.MELEE, td.RANGED)
    demon = {"name": "Tormented Demon", "id": 13599, "protectionPrayer": "MELEE",
             "animationId": td.ANIM_ATTACK_RANGED}

    assert td.is_demon(demon) is True
    assert pair.required(td.protected_style(demon)) == td.RANGED, "it prays melee, so hit it ranged"
    assert td.style_from_animation(demon["animationId"]) == td.RANGED, \
        "and it is attacking ranged, so protect from missiles"


# --- refinements 2026-09-21: two defects found by reading the module back ------------------------

def test_a_bomb_is_timed_from_the_tick_it_SPAWNED_not_the_tick_we_noticed_it():
    """DEFECT. `GRAPHICS_OBJECT_CREATED` carries its own `tick`, and the routine may read it a tick
    or two after it fired -- UDP is an accelerator with an HTTP fallback, so late is normal. Timing
    the five ticks from "now" instead of from the spawn would hand the routine a deadline LATER
    than the real one, which is the direction that gets you hit.

    The event's tick wins when it has one; `now_tick` is only the fallback for a caller reading
    graphics objects straight off a scan with no tick attached."""
    spawned_late = [{"id": td.FIREBALL_GRAPHICS_OBJECT_ID, "x": 40, "y": 50, "plane": 0, "tick": 900}]
    bombs = td.bombs_from(spawned_late, now_tick=903)

    assert bombs[0].detonates_tick == 905, "900 + 5, not 903 + 5"

    no_tick = [{"id": td.FIREBALL_GRAPHICS_OBJECT_ID, "x": 40, "y": 50, "plane": 0}]
    assert td.bombs_from(no_tick, now_tick=903)[0].detonates_tick == 908, "fallback only"


def test_the_dodge_breaks_a_tie_by_a_STATED_rule_not_by_hash_order():
    """DEFECT. The first version iterated a SET and kept the first nearest tile, so the choice among
    equally good candidates fell out of hash order -- stable within one process, which is exactly
    why a "call it twice and compare" test cannot catch it, and arbitrary between a set and a list.
    A routine whose choice is unreproducible from its own log is not debuggable.

    So the tie-break is a RULE: lowest x, then lowest y. Asserting the rule is what makes it real;
    asserting that two calls agree would pass against the broken version."""
    bombs = [td.Bomb(40, 50, 0, 105)]
    equally_near = [(39, 50, 0), (41, 50, 0), (40, 49, 0), (40, 51, 0)]

    assert td.dodge((40, 50, 0), bombs, equally_near) == (39, 50, 0)
    assert td.dodge((40, 50, 0), bombs, list(reversed(equally_near))) == (39, 50, 0)
    assert td.dodge((40, 50, 0), bombs, set(equally_near)) == (39, 50, 0), "a set answers the same"


def test_the_dodge_stays_as_close_to_the_demon_as_it_can():
    """Stepping away from the demon costs the fight: out of range it walks after you, and every tick
    it spends walking pushes the next bomb further out. Among equally near safe tiles, prefer the
    one that keeps the demon closest."""
    bombs = [td.Bomb(40, 50, 0, 105)]
    walkable = {(39, 50, 0), (41, 50, 0)}
    demon = (43, 50, 0)

    assert td.dodge((40, 50, 0), bombs, walkable, keep_near=demon) == (41, 50, 0)


def test_staying_near_the_demon_never_beats_actually_being_safe():
    """The demon is standing next to the other bomb. Being safe outranks being in range, always."""
    bombs = [td.Bomb(40, 50, 0, 105), td.Bomb(41, 50, 0, 105)]
    walkable = {(39, 50, 0), (41, 50, 0)}
    demon = (43, 50, 0)

    assert td.dodge((40, 50, 0), bombs, walkable, keep_near=demon) == (39, 50, 0)


# --- the punish window: when the shield is down and when attacks cannot miss ---------------------

def test_the_fire_bomb_drops_the_shield_and_it_stays_down_until_you_hit_it():
    """USER, 2026-09-20, correcting the wiki: after a bomb the shield is down INDEFINITELY -- a
    countdown running while the player is still dodging was measuring nothing. It is the player's
    own landed hit that starts the 4-tick window a second hit can fit inside."""
    demon = td.DemonState()
    demon.on_animation(td.ANIM_FIRE_BOMB, tick=100)

    assert demon.shield_down(tick=100) is True
    assert demon.shield_down(tick=140) is True, "still down 40 ticks later: nothing has hit it"
    assert demon.punish_ticks_left(tick=140) is None, "no window open yet, so no number to give"

    demon.on_player_hit(tick=140)
    assert demon.punish_ticks_left(tick=140) == td.SHIELD_DOWN_WINDOW_TICKS
    assert demon.punish_ticks_left(tick=143) == 1
    assert demon.shield_down(tick=144) is False, "window spent"


def test_a_miss_does_not_open_the_punish_window():
    """ZombieLite keys the window on a LANDED hit -- hitpoints XP is what tells it one happened. An
    attack that splashed leaves the shield down, waiting."""
    demon = td.DemonState()
    demon.on_animation(td.ANIM_FIRE_BOMB, tick=100)

    demon.on_player_hit(tick=110, landed=False)
    assert demon.punish_ticks_left(tick=110) is None
    assert demon.shield_down(tick=110) is True, "still waiting for a real hit"

    demon.on_player_hit(tick=112, landed=True)
    assert demon.punish_ticks_left(tick=112) == td.SHIELD_DOWN_WINDOW_TICKS


def test_the_demon_becomes_defenceless_thirty_ticks_after_a_bomb_until_you_hit_it():
    """Wiki, "Defencelessness": 100% accuracy, indicated by the flames on its back going out, and
    it lasts until the player's first hit. ZombieLite marks the 30 UNVERIFIED LIVE, so this is the
    softest number in the module."""
    demon = td.DemonState()
    demon.on_animation(td.ANIM_FIRE_BOMB, tick=100)

    assert demon.defenceless(tick=129) is False
    assert demon.defenceless(tick=130) is True, "100 + DEFENCELESS_AFTER_TICKS"
    assert demon.defenceless(tick=200) is True, "it lasts until something lands"

    demon.on_player_hit(tick=200)
    assert demon.defenceless(tick=201) is False


def test_the_bomb_counter_counts_the_demons_attacks_and_the_bomb_resets_it():
    """The cycle rides the attack COUNT. Each ordinary attack advances it; the bomb ends the cycle
    and starts the next one from zero."""
    demon = td.DemonState()
    for tick, animation in ((10, td.ANIM_ATTACK_MELEE), (16, td.ANIM_ATTACK_MELEE),
                            (22, td.ANIM_ATTACK_RANGED)):
        demon.on_animation(animation, tick=tick)

    assert demon.attacks_since_bomb == 3
    assert demon.last_attack_tick == 22
    assert demon.style == td.RANGED, "the last style it swung with"

    demon.on_animation(td.ANIM_FIRE_BOMB, tick=29)
    assert demon.attacks_since_bomb == 0


def test_a_leashed_fight_forgets_the_cycle_rather_than_aiming_at_a_bomb_that_is_not_coming():
    """Neither side has swung for ENGAGEMENT_TIMEOUT_TICKS, so the demon leashes and heals to full.
    Carrying a part-spent counter into the next fight would point the bomb warning at nothing."""
    demon = td.DemonState()
    demon.on_animation(td.ANIM_ATTACK_MELEE, tick=10)
    demon.on_animation(td.ANIM_ATTACK_MELEE, tick=16)
    assert demon.attacks_since_bomb == 2

    demon.on_tick(16 + td.ENGAGEMENT_TIMEOUT_TICKS + 1)

    assert demon.attacks_since_bomb == 0
    assert demon.shield_down(tick=100) is False
    assert demon.defenceless(tick=200) is False


def test_the_bomb_warning_comes_from_the_state_rather_than_the_caller_doing_the_sums():
    """The one call a routine makes to decide whether to commit to anything long."""
    demon = td.DemonState()
    for i in range(8):
        demon.on_animation(td.ANIM_ATTACK_MELEE, tick=10 + i * td.ATTACK_SPEED_TICKS)

    assert demon.bomb_window().imminent is True


# --- predicting the NEXT style, so the prayer is up before the hit lands -------------------------

def test_a_demon_closing_the_distance_is_going_to_use_melee():
    """Wiki, Fight overview: "If it approaches the player, it will use melee." ZombieLite reads the
    same thing off movement -- the player standing still, the gap shrinking, and the demon in its
    WALK pose."""
    assert td.predict_style(previous=6, current=4, moving=True) == td.MELEE


def test_a_demon_that_is_not_closing_is_going_to_use_magic_or_ranged():
    """The other half of the same wiki rule. Which of the two is NOT knowable from movement, so the
    answer is "not melee" rather than a guess dressed up as a style."""
    assert td.predict_style(previous=4, current=6, moving=True) == td.NOT_MELEE
    assert td.predict_style(previous=6, current=6, moving=False) == td.NOT_MELEE


def test_a_demon_already_standing_on_top_of_you_tells_you_nothing_by_not_moving():
    """Holding still at distance 1 is just melee range -- there is no information in it, and reading
    it as "not melee" would drop Protect from Melee at exactly the wrong moment. ZombieLite guards
    this with `currentDistance > 1`."""
    assert td.predict_style(previous=1, current=1, moving=False) is None


def test_an_unknown_previous_distance_predicts_nothing():
    """The first tick of a fight has nothing to compare against."""
    assert td.predict_style(previous=None, current=4, moving=True) is None


def test_the_predictor_never_claims_to_know_which_of_magic_or_ranged():
    """The wiki is explicit that after a melee phase the player has to guess and correct. A module
    that returned MAGIC here would be inventing certainty the game does not offer."""
    assert td.predict_style(previous=8, current=8, moving=False) != td.MAGIC
    assert td.predict_style(previous=8, current=8, moving=False) != td.RANGED


# --- speed: what the human actually does, so the routine has a target ---------------------------

def test_the_human_speed_budgets_are_the_measured_ones():
    """USER, mid-build: "might need to speed up our routine, look at how fast I play." MEASURED
    across all three recordings, so the routine has a number to hit rather than a feeling.

    These are pinned because the whole point is that a future call site cannot quietly drift off
    them -- the gear switch is the tightest sequence in the fight and the dodge is the one with a
    hard deadline."""
    assert td.HUMAN_GEAR_CLICK_MS == 222, "median inventory-slot to inventory-slot, n=213"
    assert td.HUMAN_GEAR_CLICK_FAST_MS == 143, "their fastest single one"
    assert td.HUMAN_BOMB_REACTION_MS == 578, "median bind chat -> first action, n=45"
    assert td.BOMB_DODGE_BUDGET_TICKS == 2, "the wiki's move window, 1.2 s"


def test_a_gear_switch_must_not_pay_a_reaction_delay_per_item():
    """This is the one that would make the routine visibly slower than the user.

    Our own click costs about 235 ms for an inventory hop (137 ms flight plus a ~98 ms hold), which
    is already human -- they measure 222. But `REACTION_DELAY_S` is 200-500 ms and confirming each
    equip separately would add it five times, turning a 1.0 s switch into roughly 3 s.

    So the budget is stated for the WHOLE burst, and a routine that fits it cannot be confirming
    item by item."""
    whole_burst = td.gear_switch_budget_ms(items=5)

    assert whole_burst <= 1500, "the human does five in about a second"
    assert whole_burst >= 5 * td.HUMAN_GEAR_CLICK_FAST_MS, "but not faster than they ever were"


def test_the_dodge_budget_leaves_room_for_the_click_to_actually_land():
    """Two ticks is 1200 ms and our viewport hop costs about 440 ms median, 650 at p90.

    SUPERSEDED IN PART: the "and no reaction delay" this once said was wrong, and the correction is
    in test_the_dodge_pays_a_human_reaction_because_it_has_the_headroom -- there is enough budget to
    pay a human reaction and we should, or the dodge beats the human by a constant margin. The HOVER
    GATE half still stands: the gate reads the cursor position RIGHT NOW and this click is issued
    while the player is still being bound."""
    assert td.BOMB_DODGE_BUDGET_MS == 1200
    assert td.BOMB_DODGE_BUDGET_MS - td.HUMAN_BOMB_REACTION_MS > 500, \
        "the human leaves this much room; so must we"


# --- the dodge should be SLOWED to human speed, not run flat out ---------------------------------

def test_the_dodge_pays_a_human_reaction_because_it_has_the_headroom():
    """CORRECTION, after the USER said their reaction is 150-220 ms, tested repeatedly.

    That is consistent with the recordings and was never contradicted by them: the 578 ms figure
    measured stimulus-to-CLICK, which is reaction plus mouse flight plus the click. Pure reaction
    could not be isolated from this data -- mouse samples are batched, so an empty window is "we
    cannot tell" rather than "the cursor was still", and the stimulus stamp sits ~580 ms into its
    own tick with recorder.py's own docs warning that stamp can be stale.

    What IS robust is the end to end: 578 ms median, stable under every filter tried. Our own
    viewport hop costs about 440 ms, so firing the moment the event arrives would be FASTER than
    the human, every single time, by a constant margin. That is a fingerprint, not an advantage --
    the whole cursor trajectory is transmitted. There is 1200 ms of budget and the human uses 578,
    so the headroom is there to spend."""
    assert td.DODGE_REACTION_MS == (150, 220), "the user's own tested range"

    slowest = td.DODGE_REACTION_MS[1] + td.VIEWPORT_HOP_P90_MS
    assert slowest < td.BOMB_DODGE_BUDGET_MS, "still lands inside two ticks at p90"

    typical = td.DODGE_REACTION_MS[0] + td.VIEWPORT_HOP_MS
    assert abs(typical - td.HUMAN_BOMB_REACTION_MS) < 120, \
        "and lands near where the human lands, rather than visibly before them"


def test_the_reaction_is_a_range_so_it_is_never_a_constant_on_the_wire():
    """settings.py's rule, and the reason it is a pair rather than a number: timing drawn from a
    human distribution is fine on the wire, a constant is a fingerprint."""
    low, high = td.DODGE_REACTION_MS
    assert high > low, "a draw, not a constant"


# --- the prayer is TIMED off the demon's cadence, not reacted to ---------------------------------

def test_the_demon_attacks_on_a_fixed_cadence_so_the_next_one_is_known():
    """This is what makes the prayer automatable, and it is not reaction speed.

    The demon's attack speed is 6 ticks, taken from CACHE DATA (config/npc param_14 on all four
    ids) rather than wiki prose. So the moment one attack animation fires, the next one's tick is
    arithmetic -- the prayer can simply BE up by then, with a whole 3.6 s to arrange it.

    The wiki backs this up from the other side: with two demons "they will not attack
    simultaneously as to allow players to prayer flick their attacks"."""
    demon = td.DemonState()
    demon.on_animation(td.ANIM_ATTACK_MELEE, tick=100)

    assert demon.next_attack_tick() == 106
    assert demon.ticks_until_attack(tick=100) == 6
    assert demon.ticks_until_attack(tick=105) == 1


def test_a_demon_that_has_not_attacked_yet_promises_no_tick():
    """The first attack of a fight has no cadence to project from, so there is no number. A caller
    must keep the prayer up on the predicted style instead of waiting for one."""
    demon = td.DemonState()
    assert demon.next_attack_tick() is None
    assert demon.ticks_until_attack(tick=100) is None


def test_the_bomb_resets_the_cadence_because_it_is_not_an_ordinary_attack():
    """The demon's style always changes after the bomb, so a cadence projected across it would aim
    the prayer at the wrong style as well as the wrong tick."""
    demon = td.DemonState()
    demon.on_animation(td.ANIM_ATTACK_MELEE, tick=100)
    demon.on_animation(td.ANIM_FIRE_BOMB, tick=107)

    assert demon.next_attack_tick() is None, "the next cycle's first attack is not on the old clock"


def test_there_is_time_to_get_a_prayer_up_between_attacks():
    """6 ticks is 3600 ms and an interface click costs us about 235 ms. The prayer is never the
    thing under time pressure in this fight -- the bomb dodge is."""
    window_ms = td.ATTACK_SPEED_TICKS * 600
    assert window_ms - td.INTERFACE_CLICK_MS > 3000, "not remotely tight"


# --- reading bombs over HTTP rather than off the event stream ------------------------------------

def test_a_finished_fireball_is_not_a_threat():
    """`GET /graphics-objects` reports `finished` for an object whose animation has run out. That
    one has already gone off, and treating it as pending would keep a routine dancing away from a
    scorch mark."""
    objects = [
        {"id": td.FIREBALL_GRAPHICS_OBJECT_ID, "x": 40, "y": 50, "plane": 0, "finished": True},
        {"id": td.FIREBALL_GRAPHICS_OBJECT_ID, "x": 41, "y": 50, "plane": 0, "finished": False},
    ]
    bombs = td.bombs_from(objects, now_tick=100)

    assert [(b.x, b.y) for b in bombs] == [(41, 50)]


def test_the_http_endpoint_carries_no_tick_so_the_fallback_is_the_honest_answer():
    """MEASURED from the provider: `/graphics-objects` returns id, x, y, plane, finished and
    distance -- no tick. The UDP event DOES carry one. So an HTTP read can only date a bomb from
    when it was read, which is later than the truth and therefore optimistic.

    That is a reason to prefer the event stream for the dodge, and the probe says so rather than
    quietly reporting a deadline it cannot actually know."""
    http_shaped = [{"id": td.FIREBALL_GRAPHICS_OBJECT_ID, "x": 40, "y": 50, "plane": 0,
                    "finished": False, "distance": 2}]

    bomb = td.bombs_from(http_shaped, now_tick=100)[0]

    assert bomb.detonates_tick == 105, "read tick + delay, the best it can do"
    assert td.bombs_from(http_shaped, now_tick=100)[0].plane == 0
