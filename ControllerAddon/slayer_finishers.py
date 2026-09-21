# slayer_finishers.py -- the FINISHING-BLOW monsters as DATA: which task needs an item used on
# the monster to kill it at all, at what health, and with what reach.
# PURE: no HTTP, no client state. Added 2026-09-21 (plan phase E0).
#
# WHY THIS IS ITS OWN MODULE and not a field on slayer_catalog.TaskSpec: the mechanic spans
# MASTERS. slayer_catalog holds Kuradal's 22 FIGHT tasks, but desert lizards is an AYA task and
# rockslugs belongs to neither of our two masters. Keying by task NAME lets both paths consult one
# table, the same way slayer_tasks.at_site falls through for a task Aya does not know.
#
# WHERE EVERY NUMBER CAME FROM:
# - The MECHANIC and which item: FreeArcanes/slayer-best-in-bank, whose SmartSupplyAdvisor models
#   these as "Finisher" SupplyRules (see docs/slayer-sites/slayer-best-in-bank-survey.md §6, and
#   the per-task table in §5). The USER pointed at that plugin 2026-09-21 as the authority for
#   per-task gear: "it's a well established and already working plugin that gives us perfect
#   suggestions". We PORT its facts; we do not vendor it -- the survey's §7 recommendation
#   (Option C), already accepted on this branch. Note SBIB is authoritative here on GEAR and
#   FINISHERS only: the survey measured it recommending a different SITE than decisions.md for
#   half the 22 tasks, so sites stay slayer_catalog's.
# - `max_hp` and the smash THRESHOLD in hitpoints: the OSRS Wiki, per monster.
# - `ratio`: DERIVED from those two, and cross-checked against Microbot's own live-proven
#   constants (docs/slayer-sites/microbot-slayer-survey.md: gargoyles `healthRatio < 3`,
#   lizards/rockslugs `< 5`). Both derivations agree with Microbot, which is the only
#   independent confirmation available without a live kill.
#
# THE HEALTH READING, MEASURED LIVE 2026-09-21 off `GET /npcs` in the Slayer Tower basement:
# a gargoyle being fought read `hpRatio: 23, hpScale: 30`; every undamaged one read `-1/-1`
# ("never damaged", NOT "dead" -- combat_core's pick_target already documents that distinction).
# So the trigger is a ratio, not a hitpoint count: the client never sends us the monster's real
# current HP. `ratio` is the INCLUSIVE upper bound -- fire when `1 <= hpRatio <= ratio`. Zero is
# excluded because zero means dead.

from collections import namedtuple

# `item` is a label into slayer_catalog.REQUIREMENT_ITEMS, so one entry covers every acceptable
# variant (a granite hammer or a rock thrownhammer both satisfy "rock hammer") and the routine's
# _protected_names already refuses to drop any of them.
Finisher = namedtuple("Finisher", ("item", "ratio", "max_hp", "smash_at", "ranged", "autokill_varbit", "note"))


def _finisher(item, max_hp, smash_at, autokill_varbit, scale=30, ranged=False, note=""):
    """`ratio` is what hpScale/hpRatio will read at `smash_at` hitpoints.

    The client scales a monster's health onto 0..hpScale, so the ratio at `smash_at` hp is
    floor(scale * smash_at / max_hp). We take the FLOOR deliberately: it is the last ratio the
    monster can still be showing while at or below the smash threshold, so firing at or under it
    can never be too early. Too early is the failure that costs something -- the item is used,
    the monster does not die, and the click is wasted.

    `scale` is the client's health-bar scale. MEASURED as 30 for gargoyles; it is the same for
    the other three by the client's own default, but each is marked unverified until a live kill
    confirms it. A wrong `scale` only moves the trigger a ratio or two later, never earlier,
    because the routine keeps attacking until the smash lands.
    """
    return Finisher(item=item, ratio=max(1, scale * smash_at // max_hp), max_hp=max_hp,
                    smash_at=smash_at, ranged=ranged, autokill_varbit=autokill_varbit, note=note)


# ================= THE UNLOCK THAT MAKES ALL OF THIS UNNECESSARY ==============================
#
# Each of these four monsters has a Slayer reward unlock that makes the game perform the finishing
# blow ITSELF, provided the item is still carried. All four are bits of `slayer_rewards_unlocks`
# (decoded from the game cache, config/varbit):
#
#     4027 slayer_autokill_gargoyles      4028 slayer_autokill_rockslugs
#     4029 slayer_autokill_desertlizards  4030 slayer_autokill_zygomites
#
# MEASURED LIVE 2026-09-21 on this account: 4027=1, 4028=1, 4029=1, 4030=0. So gargoyles,
# rockslugs and desert lizards are ALREADY automatic here and only zygomites is not.
#
# READ THE VARBIT, NEVER THE POINT TOTAL. The perk is a one-time purchase, so current slayer
# points say nothing about whether it was bought -- an earlier draft of this file inferred "99
# points < 120, therefore no perk" and was flatly wrong while the account was killing gargoyles
# perfectly well. `needed()` below is the guard that stops that class of error reaching the mouse:
# smashing an already-auto-smashed monster wastes a click on EVERY kill, and an unattended routine
# would do it for hours.
AUTOKILL_GARGOYLES = 4027
AUTOKILL_ROCKSLUGS = 4028
AUTOKILL_DESERT_LIZARDS = 4029
AUTOKILL_ZYGOMITES = 4030

# Keyed by the lowercase task name, singular or plural both resolved by `get`.
FINISHERS = {
    # VERIFIED live 2026-09-21 that this is the account's active task and that hpScale is 30.
    # Wiki: 105 hp, "a rock hammer, a rock thrownhammer, or a granite hammer must be used when
    # they reach 9 or lower Hitpoints". 30*9//105 = 2, i.e. Microbot's measured `< 3`.
    "gargoyles": _finisher("rock hammer", max_hp=105, smash_at=9, autokill_varbit=AUTOKILL_GARGOYLES, note=(
        "NOT NEEDED ON THIS ACCOUNT. MEASURED live 2026-09-21: varbit 4027 = 1, i.e. the Gargoyle "
        "Smasher perk is already owned, and the account was killing gargoyles unaided while the "
        "hammer sat untouched in slot 17. A GRANITE HAMMER EQUIPPED also auto-smashes without the "
        "perk. The entry stays because the DATA is right and another account may lack the unlock "
        "-- `needed()` is what keeps it from firing here.")),

    # Wiki: 3 hp for the desert lizard family; the ice cooler is used on them. Microbot's
    # measured trigger is `healthRatio < 5` across a family whose max hp varies by variant
    # (desert lizard 10, small 15, ... ), so the ratio here is the GENEROUS one it proved live
    # rather than our own arithmetic on one variant's hp.
    "lizards": _finisher("ice cooler", max_hp=10, smash_at=3, autokill_varbit=AUTOKILL_DESERT_LIZARDS, note=(
        "UNVERIFIED: Microbot uses ratio < 5 for the whole lizard family, whose variants differ "
        "in max hp. Prefer its number over this arithmetic if a live kill disagrees.")),

    # Neither of our two masters assigns rockslugs; kept because the mechanic is identical and a
    # future master would need it. Wiki: bag of salt, used at 3 hp or lower.
    "rockslugs": _finisher("bag of salt", max_hp=30, smash_at=3, autokill_varbit=AUTOKILL_ROCKSLUGS, note="UNVERIFIED: never assigned by Aya or Kuradal."),

    # Kuradal DOES assign these (slayer_catalog has the site). SBIB models the fungicide spray as
    # a soft preferred item, decisions.md as a required carry -- decisions.md is right, the
    # monster cannot be killed without it. Wiki: sprayed when it drops low; it reanimates
    # otherwise.
    "mutated zygomites": _finisher("fungicide spray", max_hp=60, smash_at=4, autokill_varbit=AUTOKILL_ZYGOMITES, note=(
        "UNVERIFIED THRESHOLD. decisions.md routes this as a SAFESPOT (ranged from a pillar) "
        "task, but the spray is MELEE-RANGE like the rock hammer -- the routine must close to "
        "the monster to finish it. Worth one recording before the first live run.")),
}

# Aliases the wiki/game use for a task whose FINISHERS key differs.
_ALIASES = {
    "desert lizards": "lizards",
    "zygomites": "mutated zygomites",
}


def get(task_name):
    """The finisher for a task, or None. Case-insensitive, forgiving the same singular/plural
    slip slayer_catalog.get and aya_tasks.get already forgive."""
    key = (task_name or "").strip().lower()
    if not key:
        return None
    key = _ALIASES.get(key, key)
    for candidate in (key, key + "s", key[:-1] if key.endswith("s") else key):
        candidate = _ALIASES.get(candidate, candidate)
        if candidate in FINISHERS:
            return FINISHERS[candidate]
    return None


def needed(finisher, unlocks) -> bool:
    """True when this task's finishing blow must actually be CLICKED.

    `unlocks` maps varbit id -> value (what controller.get_vars returns for a csv read). A value
    of 1 means the game finishes the monster off for us and this routine must do nothing.

    FAILS OPEN, unlike most gates here, and deliberately: an unreadable varbit yields "the click
    is needed". Clicking when we did not have to costs one wasted click; NOT clicking when we had
    to means the monster never dies and the task never advances. The cheap failure is the right
    default."""
    if finisher is None:
        return False
    value = (unlocks or {}).get(finisher.autokill_varbit)
    return value != 1


def should_finish(finisher, npc) -> bool:
    """True when this NPC is in the window where the finishing item must be used.

    Deliberately strict about the two sentinel values, because both mean "do not smash" for
    OPPOSITE reasons and confusing them is the whole risk: `hpRatio == 0` is DEAD (smashing a
    corpse wastes the click and, worse, leaves the routine believing it acted), and `hpRatio ==
    -1` is NEVER DAMAGED (combat_core.pick_target documents that one -- an untouched monster
    reads -1, not full).
    """
    if finisher is None or not npc:
        return False
    ratio = npc.get("hpRatio")
    if not isinstance(ratio, int) or ratio <= 0:
        return False
    return ratio <= finisher.ratio
