# slayer_policy.py -- the DECISIONS of the slayer loop's bank, loot and gear phases.
# PURE: no HTTP, no clicks; the one side effect is base-gear persistence to a path the caller
# names. Added 2026-09-20 (slayer sub-project 3, the decision halves of plan tasks C2, D2, C3),
# built ahead of the live gates at the user's say-so. The phases that ACT on these answers --
# banker.bank_up, eating, dropping, equipping -- are NOT built here and wait for a live run,
# because that is where a wrong fact costs a real click.
#
# Every rule below is the plan's, and each is a rule about NOT doing something:
# - BANK once. A loadout still unmet after one full bank visit is a STOP naming the item.
# - It never buys anything: running low on house tablets is a STOP with the list to buy.
# - Unknown is never zero: a tablet count of None (bank never seen) stops nothing.
# - A full inventory leaves a cheap drop. Food is only eaten for a make-room drop when HP is
#   actually missing; only a must-loot drop may cost a sip or a dropped consumable.
# - Nothing is ever dropped that is gear, a teleport item, or an item the task requires.
# - Base gear is captured only while no gear task is active, never from an empty equipment read,
#   and never to a file without a real account hash.

import json
import os
import re

import slayer_catalog

GO, BANK, SKIP, STOP = "GO", "BANK", "SKIP", "STOP"
TAKE, LEAVE = "TAKE", "LEAVE"
EAT_THEN_TAKE, DRINK_THEN_TAKE, DROP_THEN_TAKE = "EAT_THEN_TAKE", "DRINK_THEN_TAKE", "DROP_THEN_TAKE"

MIN_HOUSE_TELEPORTS = 5          # ControllerConfig.slayerMinHouseTeleports
MAKE_ROOM_VALUE = 50_000         # ControllerConfig.slayerMakeRoomValue
MUST_LOOT_VALUE = 150_000        # ControllerConfig.slayerMustLootValue

_DOSED = re.compile(r"\((\d)\)\s*$")          # "Prayer potion(3)", "Antipoison(2)"


def _cfg(tasks_cfg, key, default):
    value = (tasks_cfg or {}).get(key)
    return default if value is None else value


# ---------------------------------------------------------------- C2: bank, carry on, skip, stop

def bank_verdict(loadout, free_slots, tasks_cfg, tablets_total, skippable=False, visited_bank=False):
    """(GO | BANK | SKIP | STOP, why) for one task, from slayer_loadout.build's answer.

    `tablets_total` is Teleport to house tablets carried plus banked, or None when the bank was
    never seen. `visited_bank` is True once this task has had its one full bank visit."""
    floor = int(_cfg(tasks_cfg, "minHouseTeleports", MIN_HOUSE_TELEPORTS))
    if tablets_total is not None and tablets_total < floor:
        return STOP, (f"buy Teleport to house tablets: {tablets_total} left, the floor is {floor} "
                      f"(the house is the travel hub; nothing is ever bought for you)")
    if loadout.problems:
        why = "; ".join(loadout.problems)
        return (SKIP, f"cannot be packed -- {why}") if skippable else (STOP, f"cannot be packed -- {why}")
    if loadout.withdraw:
        wanted = ", ".join(f"{w['quantity']} {w['name']}" for w in loadout.withdraw)
        if visited_bank:
            # SHORT BECAUSE THERE IS NO ROOM is not the same as short because the bank is empty.
            #
            # FOUND LIVE 2026-09-21 on the first Kuradal task (elves x139): the pack wanted 15
            # food, 8 prayer potions and a teleport crystal, the inventory filled on slot 28, and
            # the trip stopped over the one prayer potion that would not fit. The bank had it;
            # there was simply nowhere to put it. Stopping a 139-kill task for that is the wrong
            # trade when RESTOCK exists precisely to come back for more.
            #
            # So: with NO free slots and every remaining shortfall a restockable consumable, go
            # and fight on what fits. Anything else -- a missing teleport, gear, a task's own
            # required item, or any shortfall at all while slots remain -- is still a stop,
            # because those are the things RESTOCK cannot fix and the spec refuses to guess at.
            if free_slots == 0 and all(w.get("bulk") for w in loadout.withdraw):
                return GO, (f"short {wanted} but the inventory is full -- going with what fits; "
                            f"restock will top it up")
            return STOP, f"still short after a full bank visit: {wanted}"
        return BANK, f"to fetch {wanted}"
    if free_slots is not None and free_slots < slayer_catalog.LOOT_RESERVE_SLOTS and not visited_bank:
        return BANK, f"only {free_slots} free slots, the loot reserve is {slayer_catalog.LOOT_RESERVE_SLOTS}"
    return GO, ""


# ---------------------------------------------------------------- D2: loot tiers

def loot_decision(value, stackable_held, free_slots, hp_missing, food, food_held,
                  one_dose_potion, droppable, tasks_cfg=None):
    """(action, item) for one drop worth `value` coins.

    A drop that needs no slot is always taken. With a full inventory: below the make-room value it
    is left; from there, one food is eaten for it ONLY when HP is missing; from the must-loot value
    it is always taken -- eat, else finish a 1-dose prayer potion, else drop `droppable` (what
    cheapest_consumable chose), else leave it because nothing safe can be given up."""
    if stackable_held or (free_slots or 0) > 0:
        return TAKE, None
    make_room = int(_cfg(tasks_cfg, "makeRoomValue", MAKE_ROOM_VALUE))
    must_loot = int(_cfg(tasks_cfg, "mustLootValue", MUST_LOOT_VALUE))
    can_eat = bool(food) and (food_held or 0) > 0
    if value >= must_loot:
        if can_eat:
            return EAT_THEN_TAKE, food
        if one_dose_potion:
            return DRINK_THEN_TAKE, one_dose_potion
        if droppable:
            return DROP_THEN_TAKE, droppable
        return LEAVE, None
    if value >= make_room and can_eat and (hp_missing or 0) > 0:
        return EAT_THEN_TAKE, food
    return LEAVE, None


def one_dose_potion(inventory, potion):
    """The carried `potion` on its last dose, or None."""
    base = (potion or "").strip().lower()
    if not base:
        return None
    for item in inventory or ():
        name = item.get("name") or ""
        if name.lower().startswith(base) and name.rstrip().endswith("(1)"):
            return name
    return None


def cheapest_consumable(inventory, food, protected, value_of):
    """The cheapest thing safe to DROP: the panel's food or any dosed potion, never a name that
    contains a `protected` fragment (the task's required items and teleport items -- the
    loadout's keep list minus food and prayer potions) and never anything else, so gear cannot be
    chosen by construction. `value_of(name)` -> coins or None; an unknown price ranks LAST."""
    food = (food or "").strip().lower()
    guard = [p.lower() for p in protected or () if p]
    best = None
    for item in inventory or ():
        name = item.get("name") or ""
        lowered = name.lower()
        if not (lowered == food or _DOSED.search(name)):
            continue
        if any(fragment in lowered for fragment in guard):
            continue
        price = value_of(name)
        rank = (price is None, price if price is not None else 0)
        if best is None or rank < best[0]:
            best = (rank, name)
    return best[1] if best else None


# ---------------------------------------------------------------- C3: base gear

def base_gear_path(account_hash, root=None):
    """~/.runelite/bridge-slayer/<accountHash>-base-gear.json, or None without a real hash -- the
    rule bank memory follows, so gear is never persisted under the wrong account."""
    text = str(account_hash).strip() if account_hash is not None else ""
    if not text or text == "-1":
        return None
    root = root or os.path.join(os.path.expanduser("~"), ".runelite", "bridge-slayer")
    return os.path.join(root, f"{text}-base-gear.json")


def _names(equipment):
    return {slot: worn.get("name") for slot, worn in (equipment or {}).items()
            if isinstance(worn, dict) and worn.get("name")}


def save_base_gear(path, equipment) -> bool:
    """Persist the worn gear as {slot: name}. False, and nothing written, without a path or with
    an EMPTY read: /equipment answers {} when the bridge is down, and that must never become
    'the user wears nothing'. Atomic (tmp + replace)."""
    gear = _names(equipment)
    if not path or not gear:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"gear": gear}, handle, indent=1)
    os.replace(tmp, path)
    return True


def load_base_gear(path):
    """{slot: name}, or None when there is no path, no file or an unreadable one."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            gear = json.load(handle).get("gear")
    except (OSError, ValueError, AttributeError):
        return None
    return gear if isinstance(gear, dict) and gear else None


def may_capture_base(has_task, spec) -> bool:
    """Capture the base at setup and after a completion, but ONLY while no gear task is active:
    mid-basilisks the mirror shield is on, and it is not the user's base gear."""
    return not (has_task and spec is not None and getattr(spec, "worn", ()))


def gear_to_restore(base, equipment) -> dict:
    """{slot: base item name} for every slot that no longer holds its base item (swapped for task
    gear, or emptied). No base known: restore nothing."""
    if not base:
        return {}
    worn = _names(equipment)
    return {slot: name for slot, name in base.items() if worn.get(slot) != name}
