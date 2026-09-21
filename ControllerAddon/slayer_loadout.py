# slayer_loadout.py -- a slayer task plus what is carried and banked becomes a withdraw list.
# PURE: no HTTP, no client state, nothing clicked. Added 2026-09-20 (slayer sub-project 3, plan
# task C1; spec §7.9), built AHEAD of the phase A/B live runs at the user's say-so because it is
# arithmetic over inputs the caller hands in. The BANK phase that acts on it (C2) is NOT built and
# still waits for a live run.
#
# build() answers four things for one trip:
#   withdraw   [{"name", "quantity"}] in banker.bank_up's own config shape -- only what the bank
#              is KNOWN to hold (or, with a bank never seen, what we would ask for on faith);
#   keep       lowercase name fragments for banker's keepItems, so everything else is deposited;
#   slots      inventory slots the finished pack uses;
#   problems   every reason this trip cannot be packed, each naming the item and the numbers.
#              The plan's rule: a loadout still unmet after one bank visit is a STOP naming the
#              item, never a loop -- so a gap must be loud here, not discovered at the site.
#
# WHERE THE NUMBERS COME FROM: food and potion NAMES are the panel's (food.name,
# potions.prayerPotion); COUNTS are the task's supply profile (supplies.PROFILES via
# slayer_catalog, TRIVIAL for an Aya task), with prayer potions raised by the drain formula for
# a long task. The user's own two anchors are pinned by test: nechryael 15 food and 6 full
# potions, an Aya task 3 food and none.
#
# UNKNOWN IS NOT EMPTY (bank_memory's rule): `banked=None` means the bank was never seen. Nothing
# can then be called missing, so `problems` stays about what we CAN know and `unchecked` is True.
#
# A task both masters assign (kalphite) takes the CATALOG's heavier profile: build() is not told
# who assigned it, and over-packing a short task is the safe side of that ignorance.

import math
from collections import namedtuple

import slayer_catalog
import slayer_routes
import slayer_tasks
import supplies

Loadout = namedtuple("Loadout", ("withdraw", "keep", "slots", "problems", "unchecked"))

HOUSE_TABLET = "teleport to house"
HOUSE_TABLETS_CARRIED = 2          # one spare: a tablet is spent per unconfirmed landing (phase B)
ESCAPE_RING = "ring of dueling"

# The ROUTINE's own tools, as opposed to any task's supplies. Kept on every bank trip whatever the
# task needs, and never charged a slot -- they are already carried and simply stay.
#
# FOUND LIVE 2026-09-21 on the first real bank trip. USER: "it deposited the rune pouch all the
# prayer pots and our slayer ring??" The keep list held only what THAT task needed, and zombies
# needs none of it, so the trip correctly deposited everything else -- including the rune pouch.
# But NPC Contact is how this routine gets its NEXT task and the pouch carries the runes for it, so
# depositing it strands the run: finish the task, then be unable to ask for another.
#
# The runes are listed individually because banker matches keepItems by SUBSTRING and the pouch's
# contents are separate items when they are loose in the inventory. Dust/mist/smoke are here
# because they pay for the spell's air cost -- see runes.py.
ESSENTIALS = ("rune pouch", "divine rune pouch", "astral rune", "cosmic rune", "air rune",
              "dust rune", "mist rune", "smoke rune", "law rune")

# USER 2026-09-21: "we should have all teleports on us instead of doing banking runs after each.
# like slayer ring, ectophial, House teleport etc."
#
# The pack only ever fetched the ONE teleport the current task needed, so the next task wanting a
# different one meant another trip. Standing at a bank already, the others cost a few slots and no
# extra walking -- and between them these five reach most of both masters' lists: the house tablet
# opens every jewellery-box destination, the slayer ring twelve of Kuradal's, and the other three
# cover what the box cannot.
#
# OPPORTUNISTIC, and that is the whole contract. A kit item missing from the bank is NEVER a
# problem (unlike a task's own required item, which always is), and the kit gives way entirely
# rather than push a pack past the inventory -- walking to a bank again later beats refusing a
# trip that would have worked.
TRAVEL_KIT = ("slayer ring", "ectophial", "xeric's talisman")
SPELL_CASTS_CARRIED = 2            # a teleport spell's runes, for one retry

# A catalog `carry` / `worn` label -> the item names that satisfy it: slayer_catalog's table, the
# SAME one slayer_tasks.missing_items enforces, so the pack and the check cannot disagree.
CARRY_ITEMS = dict(slayer_catalog.REQUIREMENT_ITEMS, **{slayer_catalog.LIGHT_LABEL: slayer_tasks.LIGHT_SOURCES})
_NOT_AN_ITEM = ("fairy ring", "ornate jewellery box", "moonclan teleport")
# Blighted food and blighted super restores can ONLY be consumed in the Wilderness (wiki:
# Blighted). EVERY site this routine uses is outside it -- slayer_catalog asserts that -- so a
# blighted supply is food that cannot be eaten when HP drops. FOUND 2026-09-21 in the user's real
# bank: 1319 Blighted anglerfish and 149 Blighted super restore(4) against 27 ordinary ones, so
# choosing one in the panel is an easy mistake to make.
WILDERNESS_ONLY = "blighted"

# USER 2026-09-21: "do it any food any prayer but not wilderness food." The panel's two names were
# a HARD BLOCKER -- both were empty strings on the live account, so every task stopped at the pack
# without ever reaching a bank. An explicit panel choice still wins; these are the fallback.
#
# Ordered best-healing first, and the pick is the first one the account actually has ENOUGH of --
# not simply the best, which would report a shortfall while a perfectly good food sat beside it.
# Nothing containing WILDERNESS_ONLY can ever be chosen, however plentiful: the account holds over
# a thousand blighted anglerfish and not one of them can be eaten at any site in the catalog.
FOOD_CHOICES = ("anglerfish", "manta ray", "sea turtle", "shark", "cooked karambwan", "monkfish",
                "swordfish", "bass", "lobster", "tuna", "salmon", "trout")

#: Same idea for prayer. Dosed, so availability is counted in DOSES across every part-used
#: variant, the way `doses` fetches -- see _doses_available for the live stop that cost.
RESTORE_CHOICES = ("super restore", "sanfew serum", "prayer potion")
_STACKABLE_WORDS = ("teleport", "scroll")     # tablets and scrolls share one slot


def banked_from_memory(memory):
    """bank_memory.BankMemory -> {lowercased name: quantity}, or None when the bank was never
    seen. The one place the unknown/empty distinction crosses into this module."""
    if memory is None or not getattr(memory, "seen", False):
        return None
    out = {}
    for item in memory.items or ():
        name = (item.get("name") or "").lower()
        if name:
            out[name] = out.get(name, 0) + int(item.get("quantity") or 0)
    return out


def _display(name) -> str:
    return name[:1].upper() + name[1:] if name else name


def _names(carried):
    return [n for n in (carried or {}) if not n.startswith("#")]


def _held(carried, base) -> int:
    return sum(int(q or 0) for n, q in (carried or {}).items()
               if not n.startswith("#") and supplies._is(n, base))


def _stackable(name) -> bool:
    return any(word in name.lower() for word in _STACKABLE_WORDS)


class _Pack:
    """Collects the trip: what to withdraw, what to keep, slots, and what went wrong."""

    def __init__(self, carried, banked):
        self.carried = {(n or "").lower(): q for n, q in (carried or {}).items()}
        self.banked = banked
        self.withdraw, self.keep, self.problems, self.slots = [], [], [], 0

    def _take(self, bank_name, quantity):
        self.withdraw.append({"name": _display(bank_name), "quantity": quantity})

    def exact(self, label, name, wanted, stackable=False):
        """A plain item by its exact name (food): top up to `wanted`. `stackable` items share
        one slot whatever the quantity (35 ice coolers are ONE slot, not 35)."""
        base = name.lower()
        self.keep.append(base)
        self.slots += 1 if stackable or _stackable(base) else wanted
        held = int(self.carried.get(base) or 0)
        short = wanted - held
        if short <= 0:
            return
        if self.banked is None:
            self._take(base, short)
            return
        available = int(self.banked.get(base) or 0)
        if available < short:
            self.problems.append(f"{label}: need {wanted} {_display(base)}, hold {held}, bank has {available}")
        if available > 0:
            self._take(base, min(short, available))

    def doses(self, label, base, potions, floor=None):
        """A dosed potion: count DOSES held, then fill the trip's slot budget from the bank,
        FULLEST COPY FIRST.

        The bank is read by DOSE, not by full potions alone. FOUND LIVE 2026-09-21 on the first
        Kuradal task (gargoyles x183):

            [!] Safety stop: ... prayer potions: need 8 more Super restore(4), bank has 7

        The bank held 7 Super restore(4) AND a (3), five (2)s and three (1)s -- 16 containers,
        44 doses, comfortably enough. A part-used potion CARRIED was already counted by its
        doses; a part-used potion BANKED was invisible, because the fetch only ever asked for
        `<base>(4)`. That asymmetry, not a real shortage, stopped the run.

        `potions` is how many SLOTS the trip set aside, so the fetch is bounded by BOTH the dose
        deficit and the slots still free in that budget -- fullest copy first, which is the most
        doses that budget can hold, and a bank too thin to fill it is packed anyway rather than
        refused (see the `total <= 0` check below for the user's ruling on that).
        doses the budget can hold. FOUND LIVE the same day, on the bank trip straight after the
        fix above: eight containers holding 31 doses left a 1-dose deficit, the fetch asked for a
        NINTH container, and slayer_policy turns any withdraw list surviving a bank visit into a
        STOP ("still short after a full bank visit: 1 Super restore(2)"). `floor` is the
        profile's own minimum (supplies.PROFILES[...].prayer_potions) and is what a shortfall is
        measured against: `potions` is itself a cap (supplies.MAX_DOSES, "one trip's worth") and
        RESTOCK exists for a task that outlasts it, so stopping one dose under an estimate would
        refuse a trip the account can plainly make. Under the floor it is still a named problem.
        """
        base = base.lower()
        self.keep.append(base)
        self.slots += potions
        held = sum(int(q or 0) * (supplies.charges(n) or 0) for n, q in self.carried.items()
                   if not n.startswith("#") and supplies._is(n, base))
        wanted = potions * supplies.DOSES_PER_POTION
        if held >= wanted:
            return
        containers = sum(int(q or 0) for n, q in self.carried.items()
                         if not n.startswith("#") and supplies._is(n, base))
        room = min(math.ceil((wanted - held) / supplies.DOSES_PER_POTION),
                   max(0, potions - containers))
        if room and self.banked is None:
            self._take(f"{base}({supplies.DOSES_PER_POTION})", room)
            return
        copies = sorted(((supplies.charges(n) or 0, n, int(q or 0)) for n, q in (self.banked or {}).items()
                         if int(q or 0) > 0 and supplies._is(n, base) and supplies.charges(n)),
                        key=lambda c: -c[0])
        total = held
        for dose, name, available in copies:
            if room <= 0:
                break
            take = min(room, available)
            self._take(name, take)
            room -= take
            total += take * dose
        if self.banked is None:
            return
        # OUT is the only shortfall. USER 2026-09-21, told that the bank's best eight containers
        # came to ~22 doses against a 24-dose floor: "Use up the ones in the inventory first then
        # if they are out safety stop."
        #
        # So a thin bank is not a reason to refuse the trip -- it packs the fullest copies it can
        # find, fights on what that gives, and RESTOCK brings it back for the rest. The stop comes
        # when there is genuinely nothing left to drink: carried and banked both empty. That is a
        # real safety stop (a PRAYER task with no prayer is how an unattended account dies), and
        # it names the item so the fix is obvious.
        #
        # `floor` is kept in the signature and unused for now -- the profile minimum is still the
        # right question to ask if this ever wants a middle setting -- see supplies.PROFILES.
        if total <= 0:
            self.problems.append(f"{label}: out of {_display(base)} -- none carried and none in "
                                 f"the bank (blighted ones do not count outside the Wilderness)")

    def one_of(self, label, options, prefer_spent):
        """One copy of any of `options`. `prefer_spent` takes the lowest-charge copy (a teleport
        ring is finished off, not stranded); otherwise the fullest (an antipoison)."""
        options = [o.lower() for o in options]
        self.keep.extend(options)
        self.slots += 1
        if any(_held(self.carried, o) > 0 or any(o in n for n in _names(self.carried)) for o in options):
            return True
        if self.banked is None:
            self._take(options[0], 1)
            return True
        for option in options:
            copies = [n for n, q in self.banked.items() if int(q or 0) > 0
                      and (supplies._is(n, option) or option in n)
                      and not any(bad in n for bad in slayer_tasks._NOT_LIT)]
            if not copies:
                continue
            charged = [n for n in copies if supplies.charges(n) is not None]
            pick = (min(charged, key=supplies.charges) if prefer_spent else max(charged, key=supplies.charges)) \
                if charged else copies[0]
            self._take(pick, 1)
            return True
        self.problems.append(f"{label}: no {' / '.join(_display(o) for o in options)} carried or banked")
        return False


def _teleport(pack, site, task_name) -> None:
    """The FIRST teleport the PLANNER could actually run, read with the planner's own parse() so
    the two never disagree (scorpions list a skills necklace; the planner serves its destination
    through the house jewellery box, so no necklace is needed). A place -- fairy ring, house box,
    spell -- costs no slot. An item is carried, else fetched; alternatives stay in the bank. A
    line no leg can run is never packed."""
    other = slayer_catalog.get(task_name)
    texts = list(dict.fromkeys(tuple(site.teleports or ()) + tuple(other.teleports if other else ())))

    # FREE ONES FIRST. FOUND LIVE 2026-09-21, twice in a row. USER: "why do we need to bank? ...
    # we have all the components on us." This walked the list IN ORDER and packed the first
    # teleport it could run; for icefiends that order starts with "Lassar teleport", which was in
    # the bank, so it packed a whole bank trip -- while the NEXT entry was the jewellery box, which
    # the account reaches with house tablets it already carries, for no inventory slot.
    #
    # Order should not decide that. A teleport needing nothing fetched wins wherever it sits, and
    # only when none is free does anything get fetched. This is a stable sort, so with nothing free
    # the order -- and therefore every existing decision -- is exactly what it was.
    #
    # A house-hub teleport counts as free only while a tablet is actually CARRIED: without one the
    # house is itself something to fetch, and calling it free would pack nothing and strand the
    # trip.
    carried_names = _names(pack.carried)
    has_tablet = any(HOUSE_TABLET in n for n in carried_names)

    def _free(text) -> bool:
        kind, value = slayer_routes.parse(text)
        if kind == "fairy":
            return True
        if kind in ("box", "poh_mounted"):
            return has_tablet
        if kind in ("unsupported", "spell"):
            return False
        try:
            head = (slayer_routes.GLIDER_APPROACH[0] if kind == "glider"
                    else value[0] if kind == "dialogue_item" else slayer_routes.item_row(value)[0])
        except Exception:
            return False
        return any(supplies._is(n, head) or head in n for n in carried_names)

    texts.sort(key=lambda t: 0 if _free(t) else 1)
    wanted = []
    for text in texts:
        kind, value = slayer_routes.parse(text)
        if kind == "spell":
            # The runes ARE the teleport. Two casts' worth: a cast that never lands is retried, and
            # being one rune short on Lunar Isle is a strand. Each rune is a stack: one slot.
            staves = slayer_routes.RUNE_STAVES
            for rune, per_cast in slayer_routes.SPELL_RUNES.get(value, {}).items():
                if any(staff in name for name in _names(pack.carried) for staff in staves.get(rune, ())):
                    continue
                pack.exact(f"runes ({text})", rune, per_cast * SPELL_CASTS_CARRIED, stackable=True)
            return
        if kind in ("fairy", "box", "poh_mounted"):
            return
        if kind == "unsupported":
            continue
        head = (slayer_routes.GLIDER_APPROACH[0] if kind == "glider"      # the ring that reaches the pilot
                else value[0] if kind == "dialogue_item" else slayer_routes.item_row(value)[0])
        wanted.append((text, head))
        if any(supplies._is(n, head) or head in n for n in _names(pack.carried)):
            pack.keep.append(head)
            pack.slots += 1
            return
        if pack.banked is not None and any(int(q or 0) > 0 and (supplies._is(n, head) or head in n)
                                           for n, q in pack.banked.items()):
            pack.one_of(f"teleport ({text})", (head,), prefer_spent=True)
            return
    if not wanted:
        pack.problems.append("no usable teleport: no leg can run " + (", ".join(texts) or "anything (none listed)"))
    elif pack.banked is None:
        pack.one_of(f"teleport ({wanted[0][0]})", (wanted[0][1],), prefer_spent=True)
    else:
        pack.problems.append("teleport: none of " + ", ".join(_display(h) for _, h in wanted)
                             + " is carried or banked")




def _top_up_kit(pack) -> None:
    """Fetch the TRAVEL_KIT teleports this trip is not already carrying, while there is room.

    Runs LAST, after everything the task itself needs, so the kit can only ever use slots nothing
    else wanted. It adds no problems: a kit item the bank does not have is simply not taken.
    """
    for name in TRAVEL_KIT:
        held = [n for n in _names(pack.carried) if supplies._is(n, name) or name in n]
        pack.keep.append(name)                     # never deposit one once it is carried
        if held:
            continue
        if pack.banked is None:
            continue
        if any(name in (w.get("name") or "").lower() for w in pack.withdraw):
            continue
        if pack.slots + 1 > supplies.INVENTORY_SLOTS - slayer_catalog.LOOT_RESERVE_SLOTS:
            continue                               # no room: walk to a bank again another time
        options = tuple(n for n, q in pack.banked.items()
                        if int(q or 0) > 0 and (supplies._is(n, name) or name in n))
        if not options:
            continue
        before = len(pack.problems)
        pack.one_of(f"travel kit ({name})", (name,), prefer_spent=True)
        del pack.problems[before:]                 # opportunistic: never a reason to stop


def _available(pack, name):
    """How many of `name` the account can draw on -- carried plus banked. A bank never seen
    (banked is None) answers None, which means "cannot tell" and must not read as zero."""
    held = int((pack.carried or {}).get(name) or 0)
    if pack.banked is None:
        return None
    return held + int(pack.banked.get(name) or 0)


def _doses_available(pack, base):
    """DOSES of `base` across every dosed variant, carried and banked -- 7 (4)s, a (3), five (2)s
    and three (1)s is 44, not 7. None when the bank was never seen, which means "cannot tell".

    FOUND LIVE 2026-09-21, the second half of the same bug `_Pack.doses` had: asking for
    `<base>(4)` alone made a bank holding 4 Super restore(1) answer "no ordinary prayer restore"
    and stop the task. It also picked the WRONG restore -- this account's 37 doses of part-used
    Prayer potion lost to 9 doses of Super restore, because neither had a single (4).
    """
    held = sum(int(q or 0) * (supplies.charges(n) or 0) for n, q in (pack.carried or {}).items()
               if not n.startswith("#") and supplies._is(n, base))
    if pack.banked is None:
        return None
    return held + sum(int(q or 0) * (supplies.charges(n) or 0) for n, q in pack.banked.items()
                      if supplies._is(n, base) and supplies.charges(n))


def _choose(pack, choices, wanted, suffix="", count_doses=False):
    """The first of `choices` the account has at least `wanted` of, else whichever it has most of,
    else None. Never anything Wilderness-only, whatever the count. `count_doses` measures a dosed
    potion by its doses (see _doses_available), so `wanted` is then doses too."""
    usable = [c for c in choices if WILDERNESS_ONLY not in c]
    best, best_count = None, -1
    for choice in usable:
        count = _doses_available(pack, choice) if count_doses else _available(pack, choice + suffix)
        if count is None:
            return choice          # bank unseen: cannot compare, so take the best-ranked one
        if count >= wanted and wanted > 0:
            return choice
        if count > best_count:
            best, best_count = choice, count
    return best if best_count > 0 else None

def build(task_name, count, panel_cfg, prayer_level, prayer_bonus, carried, banked,
          keep_carried=False, skip_teleport=False) -> Loadout:
    """See the module header. `carried` is routines/slayer.py's _carried() dict (lowercased name
    -> quantity, plus "#<id>" keys); `banked` is banked_from_memory()'s dict, or None.
    `keep_carried` counts what is already held and NOT part of the trip toward `slots` -- for a
    caller that will not deposit first. The default assumes banker deposits everything not kept."""
    unchecked = banked is None
    site = slayer_catalog.get(task_name) or slayer_tasks._site(task_name)
    if site is None:
        return Loadout([], [], 0, [f"{task_name} has no site in any catalog -- no loadout"], unchecked)

    spec = slayer_catalog.get(task_name)
    profile = supplies.PROFILES[spec.profile if spec else "TRIVIAL"]
    pack = _Pack(carried, banked)
    panel_cfg = panel_cfg or {}

    food = (panel_cfg.get("food") or {}).get("name")
    if food and WILDERNESS_ONLY in food.lower():
        pack.problems.append(f"{food} can only be eaten in the WILDERNESS and no site here is in it "
                             f"-- choose an ordinary food in the panel")
        food = None
    if not food and profile.food:
        # Nothing usable named: choose one. See FOOD_CHOICES for why this is a fallback and not an
        # override, and why the best-healing food is not automatically the right answer.
        food = _choose(pack, FOOD_CHOICES, profile.food)
        if not food:
            pack.problems.append("no food is chosen in the panel (food.name) and the bank holds no "
                                 "ordinary food to choose instead")
    if food and profile.food:
        pack.exact("food", food, profile.food)

    if profile.prayer_potions:
        potion = (panel_cfg.get("potions") or {}).get("prayerPotion")
        if potion and WILDERNESS_ONLY in potion.lower():
            pack.problems.append(f"{potion} can only be drunk in the WILDERNESS and no site here is in "
                                 f"it -- choose an ordinary prayer restore in the panel")
            potion = None
        doses = supplies.prayer_doses_needed(int(count or 0), int(prayer_level or 1), int(prayer_bonus or 0))
        potions = supplies.prayer_potions_for(profile, doses)
        if not potion:
            potion = _choose(pack, RESTORE_CHOICES, potions * supplies.DOSES_PER_POTION,
                             count_doses=True)
            if not potion:
                pack.problems.append("no prayer restore is chosen in the panel "
                                     "(potions.prayerPotion) and the bank holds no ordinary one")
        if potion:
            pack.doses("prayer potions", potion, potions,
                       floor=profile.prayer_potions)

    if spec is not None:
        # `worn` too: `carried` merges inventory and equipment, so a worn mirror shield is already
        # held and a banked one is fetched. A label with no checkable item (the user's open gear
        # decision) is skipped, never invented.
        for label in tuple(spec.carry) + tuple(spec.worn):
            options = CARRY_ITEMS.get(label, (label,))
            if options:
                pack.one_of(label, options, prefer_spent=False)
    else:
        for need in slayer_tasks.required_items(task_name, count):
            if slayer_tasks._have(need, pack.carried) >= need.quantity:
                pack.keep.extend(need.any_of)
                pack.slots += 1
                continue
            options = slayer_tasks.LIGHT_SOURCES if need.ids else need.any_of
            if need.quantity == 1:
                pack.one_of(need.label, options, prefer_spent=False)
            else:
                # A required item wanted in bulk is a STACK (ice coolers: count + 5). Found by
                # sweeping Aya's tasks live: lizards "needed 37 of 28 slots".
                pack.exact(need.label, options[0], need.quantity, stackable=True)

    # A teleport is for covering DISTANCE. When the caller can already reach the site from where
    # it stands, there is nothing to fetch. FOUND LIVE 2026-09-21: the routine teleported to
    # Varrock, walked to the sewer manhole, stopped on an unrelated bug, and on RESTART -- which
    # wipes the in-progress journey, since it lives in memory -- re-planned from scratch and set
    # off 40 tiles to a bank for another Varrock tablet while standing on the entrance.
    #
    # The caller decides, because it has the player's position and the planner's answer and this
    # function has neither.
    if not skip_teleport:
        _teleport(pack, site, task_name)

    pack.exact("house tablets", HOUSE_TABLET, HOUSE_TABLETS_CARRIED)
    # FULL, not nearly spent. FOUND LIVE 2026-09-21 -- USER: "it should be able to get both from
    # banks im not helping it." prefer_spent takes the LOWEST-charge copy, which is good
    # housekeeping for an ordinary teleport and the wrong call for this one: the account holds 15
    # Ring of dueling(8) and the pack kept taking a (3), then a (2), then a (1), running out every
    # few teleports. That run ended with no ring at all. Running out is a DEAD END here, not an
    # inconvenience -- banker stops with "no bank in range, and no ring of dueling left to reach
    # one". Every other teleport still drains its part-charges first.
    pack.one_of("escape ring", (ESCAPE_RING,), prefer_spent=False)
    _top_up_kit(pack)

    slots = pack.slots
    if keep_carried:
        kept = set(pack.keep)
        slots += sum(int(q or 0) for n, q in pack.carried.items()
                     if not n.startswith("#") and not any(k in n for k in kept))
    if slots > supplies.INVENTORY_SLOTS:
        pack.problems.append(f"the pack needs {slots} slots and the inventory has {supplies.INVENTORY_SLOTS}")

    # ESSENTIALS last and slot-free: they are already carried, so counting them would make every
    # pack read larger than it is and could refuse a trip that fits.
    keep = list(dict.fromkeys(list(pack.keep) + list(ESSENTIALS)))
    return Loadout(pack.withdraw, keep, slots, pack.problems, unchecked)
