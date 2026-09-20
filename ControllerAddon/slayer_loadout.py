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
import slayer_tasks
import supplies

Loadout = namedtuple("Loadout", ("withdraw", "keep", "slots", "problems", "unchecked"))

HOUSE_TABLET = "teleport to house"
HOUSE_TABLETS_CARRIED = 2          # one spare: a tablet is spent per unconfirmed landing (phase B)
ESCAPE_RING = "ring of dueling"

# A catalog `carry` label -> the item names that satisfy it, best first.
CARRY_ITEMS = {
    "rock hammer": ("rock hammer",),
    "antipoison": ("antipoison", "superantipoison", "antidote"),
    "fungicide spray": ("fungicide spray",),
    "lit light source": slayer_tasks.LIGHT_SOURCES,
}
_NOT_AN_ITEM = ("fairy ring", "ornate jewellery box", "moonclan teleport")
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

    def exact(self, label, name, wanted):
        """A plain item by its exact name (food): top up to `wanted`."""
        base = name.lower()
        self.keep.append(base)
        self.slots += 1 if _stackable(base) else wanted
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


def _teleport(pack, site) -> None:
    """The FIRST teleport that can actually be used: a place (fairy ring, house box, a spell) costs
    no slot; an item is carried, else fetched. Alternatives stay in the bank."""
    items = []
    for text in site.teleports or ():
        head = text.partition(":")[0].strip().lower()
        if head in _NOT_AN_ITEM:
            return
        items.append(head)
        if any(supplies._is(n, head) or head in n for n in _names(pack.carried)):
            pack.keep.append(head)
            pack.slots += 1
            return
        if pack.banked is not None and any(int(q or 0) > 0 and (supplies._is(n, head) or head in n)
                                           for n, q in pack.banked.items()):
            pack.one_of(f"teleport ({text})", (head,), prefer_spent=True)
            return
    if items and pack.banked is None:
        pack.one_of(f"teleport ({site.teleports[0]})", (items[0],), prefer_spent=True)
    elif items:
        pack.problems.append(f"teleport: none of {', '.join(_display(i) for i in items)} is carried or banked")


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
    if not food:
        pack.problems.append("no food is chosen in the panel (food.name)")
    elif profile.food:
        pack.exact("food", food, profile.food)

    if profile.prayer_potions:
        potion = (panel_cfg.get("potions") or {}).get("prayerPotion")
        if not potion:
            pack.problems.append("no prayer potion is chosen in the panel (potions.prayerPotion)")
        else:
            doses = supplies.prayer_doses_needed(int(count or 0), int(prayer_level or 1), int(prayer_bonus or 0))
            pack.doses("prayer potions", potion, supplies.prayer_potions_for(profile, doses))

    if spec is not None:
        for label in spec.carry:
            pack.one_of(label, CARRY_ITEMS.get(label, (label,)), prefer_spent=False)
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
                pack.exact(need.label, options[0], need.quantity)

    _teleport(pack, site)

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
