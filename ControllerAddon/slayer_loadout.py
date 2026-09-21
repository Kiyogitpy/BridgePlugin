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

#: Same idea for prayer. Dosed, so availability is counted in full (4)s the way `doses` fetches.
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

    def doses(self, label, base, potions):
        """A dosed potion: count DOSES held, fetch full (4)s for the difference."""
        base = base.lower()
        self.keep.append(base)
        self.slots += potions
        held = sum(int(q or 0) * (supplies.charges(n) or 0) for n, q in self.carried.items()
                   if not n.startswith("#") and supplies._is(n, base))
        short = math.ceil(max(0, potions * supplies.DOSES_PER_POTION - held) / supplies.DOSES_PER_POTION)
        if short <= 0:
            return
        full = f"{base}(4)"
        if self.banked is None:
            self._take(full, short)
            return
        available = int(self.banked.get(full) or 0)
        if available < short:
            self.problems.append(f"{label}: need {short} more {_display(full)}, bank has {available}")
        if available > 0:
            self._take(full, min(short, available))

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



def _available(pack, name):
    """How many of `name` the account can draw on -- carried plus banked. A bank never seen
    (banked is None) answers None, which means "cannot tell" and must not read as zero."""
    held = int((pack.carried or {}).get(name) or 0)
    if pack.banked is None:
        return None
    return held + int(pack.banked.get(name) or 0)


def _choose(pack, choices, wanted, suffix=""):
    """The first of `choices` the account has at least `wanted` of, else whichever it has most of,
    else None. Never anything Wilderness-only, whatever the count."""
    usable = [c for c in choices if WILDERNESS_ONLY not in c]
    best, best_count = None, -1
    for choice in usable:
        count = _available(pack, choice + suffix)
        if count is None:
            return choice          # bank unseen: cannot compare, so take the best-ranked one
        if count >= wanted and wanted > 0:
            return choice
        if count > best_count:
            best, best_count = choice, count
    return best if best_count > 0 else None

def build(task_name, count, panel_cfg, prayer_level, prayer_bonus, carried, banked,
          keep_carried=False) -> Loadout:
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
            potion = _choose(pack, RESTORE_CHOICES, potions, suffix="(4)")
            if not potion:
                pack.problems.append("no prayer restore is chosen in the panel "
                                     "(potions.prayerPotion) and the bank holds no ordinary one")
        if potion:
            pack.doses("prayer potions", potion, potions)

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

    _teleport(pack, site, task_name)

    pack.exact("house tablets", HOUSE_TABLET, HOUSE_TABLETS_CARRIED)
    pack.one_of("escape ring", (ESCAPE_RING,), prefer_spent=True)

    slots = pack.slots
    if keep_carried:
        kept = set(pack.keep)
        slots += sum(int(q or 0) for n, q in pack.carried.items()
                     if not n.startswith("#") and not any(k in n for k in kept))
    if slots > supplies.INVENTORY_SLOTS:
        pack.problems.append(f"the pack needs {slots} slots and the inventory has {supplies.INVENTORY_SLOTS}")

    keep = list(dict.fromkeys(pack.keep))
    return Loadout(pack.withdraw, keep, slots, pack.problems, unchecked)
