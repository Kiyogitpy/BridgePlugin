"""The Tormented Demon fight as PURE decisions -- no HTTP, no client, no clicking.

Everything here is PORTED from ZombieLite's `com.salvation.plugins.tormenteddemons`, which runs in
the same client and which the user has already corrected against live play. Copying its source
rather than reconstructing the mechanic from the wiki is this project's standing rule (see the
"Reference implementations" section of CLAUDE.md, and the 231.6 dialogue-widget lesson). Where the
wiki and ZombieLite disagree, ZombieLite is right and the disagreement is recorded beside the
constant.

Cross-checked against three of the USER's own recordings (2026-09-20 and 2026-09-21, 9.5 / 14.5 /
24.7 minutes, 93k records): every graphics and spot-animation id below was observed there, and the
bomb timing was measured across 45 binds.

The acting half -- which tile to click, when to pray, when to swap gear -- belongs in a routine.
This module only answers questions.
"""
import collections

# --- styles ------------------------------------------------------------------------------------

MELEE = "melee"
RANGED = "ranged"
MAGIC = "magic"

#: ZombieLite `DamageType.HEAD_ICON_MAPPING`. The demon's overhead says what it is PROTECTED from.
STYLE_FROM_HEAD_ICON = {"MELEE": MELEE, "RANGED": RANGED, "MAGIC": MAGIC}


# --- ids, every one of them observed in the user's recordings ------------------------------------

#: ZombieLite `NpcId.TORMENTED_DEMON_1..4`. 13604 is NOT in its table but WAS seen live on
#: 2026-09-20, transforming into 13600 at the same npc index -- so match on the NAME, never on an
#: id list. Kept only so a caller can recognise one without a name.
NPC_IDS = (13599, 13600, 13601, 13602, 13604)
NPC_NAME = "Tormented Demon"

#: ZombieLite `GraphicsObjectId.TORMENTED_DEMON_FIREBALL`. MEASURED: it arrives paired with id
#: 1446 on the SAME tile, so counting graphics records double-counts bombs. Only 2856 is the bomb.
FIREBALL_GRAPHICS_OBJECT_ID = 2856

#: ZombieLite `SpotAnimationId`. The attack ids are how the demon's CURRENT style is read without
#: waiting for a hitsplat.
SPOTANIM_SHIELD_ACTIVE = 2849
SPOTANIM_SHIELD_SPAWN = 2850
SPOTANIM_ATTACK_MELEE = 2851
SPOTANIM_ATTACK_RANGED = 2858
SPOTANIM_ATTACK_FIREBALL = 2852
SPOTANIM_SPAWNING = 2847

STYLE_FROM_SPOTANIM = {
    SPOTANIM_ATTACK_MELEE: MELEE,
    SPOTANIM_ATTACK_RANGED: RANGED,
}

#: RuneLite gameval `AnimationID.LUC2_UNDEAD_DEMON_*`, which is what ZombieLite keys its whole
#: state machine on. These matter more than the spot animations above: `/npcs` has carried
#: `animationId` since the Gauntlet work, so the demon's attack style needs NO bridge change to
#: read. CROSS-VALIDATED against the user's longest recording, where the two agree to within one
#: observation -- melee 11392 appears 131 times beside spotanim 2851's 131, ranged 11389 appears
#: 63 times beside spotanim 2858's 64.
ANIM_FIRE_BOMB = 11387           # LUC2_UNDEAD_DEMON_EXPLOSION_FIRE
ANIM_ATTACK_MAGIC = 11388        # LUC2_UNDEAD_DEMON_FIREY_BALLS
ANIM_ATTACK_RANGED = 11389       # LUC2_UNDEAD_DEMON_SPARE_RIBS
ANIM_WALK = 11390
ANIM_READY = 11391
ANIM_ATTACK_MELEE = 11392
ANIM_DEFEND = 11393
ANIM_DEATH = 11394
ANIM_SUMMON = 11395

STYLE_FROM_ANIMATION = {
    ANIM_ATTACK_MELEE: MELEE,
    ANIM_ATTACK_RANGED: RANGED,
    ANIM_ATTACK_MAGIC: MAGIC,
}


# --- timing, all in game ticks -------------------------------------------------------------------

#: ZombieLite `TormentedDemonFireBall`: `damageTick = client.getTickCount() + 5`. The wiki's "2
#: ticks (1.2s) to move" is the REACTION budget, not the time until it lands; both are true.
FIREBALL_DELAY_TICKS = 5

#: The demon's own attack speed. ZombieLite took this from CACHE DATA rather than wiki prose:
#: config/npc param_14 = 6 on all four ids.
ATTACK_SPEED_TICKS = 6

#: From the demon's last ordinary attack to the bomb. ZombieLite MEASURED the whole cadence from a
#: client log of six cycles and found it rigid: every cycle is 6*attacks + 7 ticks, to within one.
BOMB_GAP_TICKS = 7

#: The ATTACK COUNT per bomb cycle is NOT rigid -- ZombieLite logged 10, 9, 8, 10, 8, 9 in six
#: cycles, in no order, and could not tie it to the cycle's style or to fight progress. So this is
#: a BOUND and must never be presented as a prediction. The bomb rides the attack count, not a wall
#: clock: every protection-prayer swap freezes the demon for 6 ticks and every tick it spends
#: walking back into range pushes the bomb along with it, which is why a "fight start + N ticks"
#: timer drifts and this does not.
MIN_ATTACKS_PER_BOMB_CYCLE = 8
MAX_ATTACKS_PER_BOMB_CYCLE = 10

#: USER, 2026-09-20, correcting the wiki: "There isn't a 1 time max hit, it is actually a 4t window,
#: if you time your attacks correctly you can land 2 hits during this window as weapon speed changes
#: if you land an attack with the shield down." The wiki's "remains down until they are attacked
#: once" is the other half of the same mechanic: after a bomb the shield is down INDEFINITELY, and
#: the player's first LANDED hit is what opens this 4-tick window. A miss does not open it.
SHIELD_DOWN_WINDOW_TICKS = 4

#: Wiki, "Defencelessness": roughly 30 ticks into the fight and again roughly 30 ticks after each
#: fire bomb. ZombieLite marks this UNVERIFIED LIVE, so treat it as the softest number here.
DEFENCELESS_AFTER_TICKS = 30

#: Neither side has swung for this long, so the fight is over: the demon leashes and heals to full.
#: A part-spent bomb counter must not be carried into the next fight.
ENGAGEMENT_TIMEOUT_TICKS = 20

#: The demon changes its protection prayer to the last style that damaged it, every 150 damage
#: taken since its last swap, and stops attacking for 6 ticks while it does. This is why the user's
#: recordings alternate melee and ranged: 87% of 100 observed gear switches.
PRAYER_SWAP_DAMAGE = 150


Bomb = collections.namedtuple("Bomb", "x y plane detonates_tick")


def bombs_from(graphics_objects, now_tick):
    """Live graphics objects -> the fire bombs among them, each with the tick it detonates.

    Anything that is not `FIREBALL_GRAPHICS_OBJECT_ID` is ignored, INCLUDING the id-1446 graphic
    that arrives on the same tile in the same instant -- counting that too would report two bombs
    per bomb.
    """
    bombs = []
    for obj in graphics_objects or ():
        if (obj or {}).get("id") != FIREBALL_GRAPHICS_OBJECT_ID:
            continue
        bombs.append(Bomb(obj.get("x"), obj.get("y"), obj.get("plane") or 0,
                          now_tick + FIREBALL_DELAY_TICKS))
    return bombs


def in_danger(player, bombs, now_tick=None):
    """Is the player standing on a bomb tile that has not yet gone off?

    The blast is ONE TILE -- ZombieLite tests `worldPoint.equals(player.getWorldLocation())` -- so
    being beside a bomb is already safe. `now_tick` is optional: without it every bomb given is
    treated as still pending, which is what a caller that has already filtered them wants.
    """
    px, py, pplane = _tile(player)
    for bomb in bombs or ():
        if now_tick is not None and now_tick >= bomb.detonates_tick:
            continue
        if (bomb.x, bomb.y, bomb.plane) == (px, py, pplane):
            return True
    return False


def dodge(player, bombs, walkable, now_tick=None):
    """The tile to step to, or None when the player is already safe or nothing safe is reachable.

    Nearest first, because the step has to happen inside two ticks and one tile is a complete
    dodge. Returning None when every walkable tile is a bomb tile is deliberate: some spots offer
    as few as three safe tiles, and stepping onto the other bomb is the obvious way to get this
    wrong.
    """
    if not in_danger(player, bombs, now_tick):
        return None
    px, py, pplane = _tile(player)
    pending = [b for b in bombs or ()
               if now_tick is None or now_tick < b.detonates_tick]
    unsafe = {(b.x, b.y, b.plane) for b in pending}
    best = None
    for tile in walkable or ():
        tx, ty, tplane = _tile(tile)
        if tplane != pplane or (tx, ty, tplane) in unsafe:
            continue
        distance = max(abs(tx - px), abs(ty - py))
        if distance == 0:
            continue
        if best is None or distance < best[0]:
            best = (distance, (tx, ty, tplane))
    return best[1] if best else None


def _tile(value):
    """A tile as (x, y, plane), from either a tuple or a dict."""
    if isinstance(value, dict):
        return value.get("x"), value.get("y"), value.get("plane") or 0
    if len(value) == 2:
        return value[0], value[1], 0
    return value[0], value[1], value[2]


def style_from_overhead(name):
    """A decoded HeadIcon name from the bridge -> the style that overhead PROTECTS FROM.

    Anything unrecognised, and the absent key that means "the client could not decode it", read as
    None. A routine must never swap gear on a guess: unknown is not a style.
    """
    return STYLE_FROM_HEAD_ICON.get(name)


def style_from_spotanim(spotanim_id):
    """A spot-animation id -> the style the demon is ATTACKING with, or None if it is not an attack.

    This is the read that makes protection prayers automatable: the demon announces its style as it
    swings, rather than only being knowable from the hitsplat that has already landed.
    """
    return STYLE_FROM_SPOTANIM.get(spotanim_id)


class SwapPair(object):
    """The two styles brought to the demons, and which of them to be using right now.

    Ported from ZombieLite's `TormentedDemonSwapStyles`. A caller who brought melee and ranged is
    never told to pick up a staff.
    """

    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary

    def required(self, protected_style):
        """The half of the pair the demon is NOT praying against.

        When it prays against neither -- it copied a thrall's magic, or the overhead could not be
        read at all -- both halves are open and the primary is the answer. Stalling on an
        unreadable overhead would be worse than hitting it with the wrong half for one attack.
        """
        return self.secondary if protected_style == self.primary else self.primary


class SwapCounter(object):
    """How much more damage before the demon changes its protection prayer.

    The overhead is the TRUTH and this counter is only an estimate of when it will change, so the
    overhead changing is what resets it -- never the counter reaching zero. That way a hitsplat we
    failed to see costs one wrong estimate and not a permanent desynchronisation.
    """

    def __init__(self):
        self.damage_left = PRAYER_SWAP_DAMAGE
        self.overhead = None

    def on_hit(self, amount, overhead=None):
        if overhead is not None and self.overhead is not None and overhead != self.overhead:
            self.damage_left = PRAYER_SWAP_DAMAGE
        else:
            self.damage_left = max(0, self.damage_left - int(amount or 0))
        if overhead is not None:
            self.overhead = overhead
        return self.damage_left

    def damage_left_capped(self, hitpoints):
        """The countdown, never promising more damage than the demon has left to give.

        150 to the swap means nothing against a demon on 60 hitpoints: it dies first, and a caller
        waiting for a swap that cannot come would hold the wrong style until it did.
        """
        if hitpoints is None:
            return self.damage_left
        return min(self.damage_left, int(hitpoints))


BombWindow = collections.namedtuple("BombWindow", "earliest latest imminent")


def bomb_window(attacks_since_bomb, last_attack_tick):
    """When the next fire bomb can land, as a WINDOW -- never a single tick.

    ZombieLite logged six consecutive cycles at 10, 9, 8, 10, 8, 9 attacks and could tie the count
    to nothing observable: not the cycle's style, not progress through the fight. The cadence
    either side of it is rigid (`ATTACK_SPEED_TICKS` between attacks, `BOMB_GAP_TICKS` from the
    last attack to the bomb), so the timing is knowable to within the attack count and no further.

    `imminent` is what a routine should actually branch on: past `MIN_ATTACKS_PER_BOMB_CYCLE` the
    very next attack may be the last before the bomb, so there is no number left worth giving and
    the routine should stop committing to anything long.
    """
    remaining_min = max(0, MIN_ATTACKS_PER_BOMB_CYCLE - attacks_since_bomb)
    remaining_max = max(0, MAX_ATTACKS_PER_BOMB_CYCLE - attacks_since_bomb)
    return BombWindow(
        earliest=last_attack_tick + remaining_min * ATTACK_SPEED_TICKS + BOMB_GAP_TICKS,
        latest=last_attack_tick + remaining_max * ATTACK_SPEED_TICKS + BOMB_GAP_TICKS,
        imminent=attacks_since_bomb >= MIN_ATTACKS_PER_BOMB_CYCLE,
    )


def style_from_animation(animation_id):
    """The demon's animation -> the style it is ATTACKING with, or None if it is not attacking.

    Walking, standing ready, defending, dying and summoning all read as None, and so does the fire
    bomb -- that one is a cue to MOVE rather than a style to pray against, so it has its own
    predicate and cannot be mistaken for an attack here.
    """
    return STYLE_FROM_ANIMATION.get(animation_id)


def is_fire_bomb(animation_id):
    """The bomb animation, asked separately from the attack styles on purpose."""
    return animation_id == ANIM_FIRE_BOMB


def is_demon(npc):
    """Is this npc a Tormented Demon? By NAME, never by id.

    MEASURED across the user's three recordings: id 13600 throughout one session, 13599 throughout
    two others, and 13604 transforming into 13600 at a single npc index. ZombieLite's own id table
    stops at 13602, so matching on ids would have silently missed a demon actually fought.
    """
    name = (npc or {}).get("name") if isinstance(npc, dict) else None
    return bool(name) and NPC_NAME.lower() in name.lower()
