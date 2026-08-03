# routines/blast_furnace.py
#
# Smelt a configured bar type at the Blast Furnace on an official ("mass") Blast Furnace
# world: bank materials, load coal (via a coal bag) then ore onto the conveyor belt
# following the OSRS Wiki's per-bar-type trip patterns, collect bars, repeat -- plus a
# periodic blind coffer top-up so the dwarves keep operating the machinery unattended.
#
# Mechanics (from the local OSRS wiki mirror, osrs-wiki-data-master/raw/B/Blast Furnace.txt,
# raw/B/Blast Furnace_Strategies.txt, raw/C/Coffer (Blast Furnace).txt, raw/C/Conveyor belt
# (Blast Furnace).txt, raw/B/Bar dispenser.txt, raw/C/Coal bag.txt):
#   - "Mass world" means an official Blast Furnace world, where dwarves handle the
#     pedals/pump/stove/cogs automatically -- this routine only loads the conveyor belt and
#     collects bars. Manual operation is out of scope entirely.
#   - PREREQUISITES ASSUMED, NOT VERIFIED: standing in the Blast Furnace basement on a mass
#     world; Ice gloves or Smiths gloves (i) equipped (so bars never need bucket-of-water
#     cooling); already own a coal bag.
#   - Loading the conveyor belt ("Put-ore-on") is a two-click "use" action -- click the
#     ore/coal item, then click the conveyor belt -- same item-then-target idiom
#     routines/firemaking.py established for tinderbox+log, just with a world object as the
#     second click.
#   - Coal must land before the primary ore or the furnace yields the wrong (lower-tier) bar
#     -- every trip pattern below deposits coal first.
#   - The coal bag has no direct bag-to-conveyor shortcut: its coal is emptied into inventory
#     first (banker.empty_coal_bag), then deposited item-by-item like loose coal.
#   - The coffer drains 1,200 coins/minute while active and has no bridge-exposed live
#     balance -- top-up here is a blind, time-based deposit, not balance-aware.
#   - ASSUMPTION, NOT YET VERIFIED LIVE: clicking the coffer (its only option is "Use") opens
#     a chatbox "Enter amount" numeric prompt that controller.type_digits_and_enter can fill
#     in. Verify this live before relying on unattended coffer top-up.
#   - Deliberately NOT implemented: the hybrid gold-mixing method, world-hopping strategies,
#     manual/pedal operation, the Smithing cape's 36-coal bag capacity, buying ore from
#     Ordan's shop.
#
# Run with:
#     python controller.py routines/blast_furnace.py

import random
import time

import banker
import controller

COAL_BAG_CAPACITY = 27

# Per-bar-type trip patterns, reproduced verbatim from Blast Furnace_Strategies.txt's "Bar
# patterns" table (which assumes a coal bag + ice gloves). Each trip is a "deposit" list of
# (item name, quantity) pairs -- coal always listed first, per the coal-before-ore ordering
# rule above -- and a "collect" flag for whether to gather bars at the end of that trip.
# tick() advances through a bar type's trip list in order and wraps back to the start after
# the last one, so single-trip bar types (bronze/iron/silver/steel/gold) just repeat trip 0
# forever with no special-casing needed.
BAR_TYPES = {
    "bronze": {
        "level": 1,
        "trips": [
            {"deposit": [("Copper ore", 14), ("Tin ore", 14)], "collect": True},
        ],
    },
    "iron": {
        "level": 15,
        "trips": [
            {"deposit": [("Iron ore", 28)], "collect": True},
        ],
    },
    "silver": {
        "level": 20,
        "trips": [
            {"deposit": [("Silver ore", 28)], "collect": True},
        ],
    },
    "steel": {
        "level": 30,
        "trips": [
            {"deposit": [("Coal", 27), ("Iron ore", 27)], "collect": True},
        ],
    },
    "gold": {
        "level": 40,
        "trips": [
            {"deposit": [("Gold ore", 27)], "collect": True},
        ],
    },
    "mithril": {
        "level": 50,
        "trips": [
            {"deposit": [("Coal", 54)], "collect": False},
            {"deposit": [("Coal", 27), ("Mithril ore", 27)], "collect": True},
            {"deposit": [("Coal", 27), ("Mithril ore", 27)], "collect": True},
        ],
    },
    "adamantite": {
        "level": 70,
        "trips": [
            {"deposit": [("Coal", 54)], "collect": False},
            {"deposit": [("Coal", 27), ("Adamantite ore", 27)], "collect": True},
        ],
    },
    "runite": {
        "level": 85,
        "trips": [
            {"deposit": [("Coal", 54)], "collect": False},
            {"deposit": [("Coal", 54)], "collect": False},
            {"deposit": [("Coal", 27), ("Runite ore", 27)], "collect": False},
            {"deposit": [("Coal", 54)], "collect": True},
            {"deposit": [("Coal", 27), ("Runite ore", 27)], "collect": True},
        ],
    },
}

# Used only if BlastFurnacePlugin isn't loaded/enabled in RuneLite (so /blast-furnace-config
# comes back None) -- keeps this routine runnable standalone, same as tormented_demon.py.
DEFAULT_CONFIG = {
    "enabled": True,
    "barType": "iron",

    "coffer": {
        "topUpEnabled": True,
        "topUpAmount": 40000,
        "topUpIntervalMinutes": 30,
    },
    "bank": {
        "teleportFallback": False,
    },

    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    config = controller.get_blast_furnace_config()
    return config if config is not None else DEFAULT_CONFIG


def check_level_requirement(ctx, config: dict) -> None:
    """One-time-per-activation under-level sanity check, run from inside tick() (never
    setup() -- see this plan's Global Constraints for why setup() would false-trigger a
    permanent safety stop pre-login). Gated by a ctx.state.data flag so it runs exactly once
    per routine-activation -- state.data is reset to {} by controller.py whenever the active
    routine file changes."""
    bar_type = (config.get("barType") or "iron").lower()
    pattern = BAR_TYPES.get(bar_type)
    if pattern is None:
        return  # unknown/custom bar type -- can't validate, don't block

    smithing = controller.get_skills().get("Smithing") or {}
    level = smithing.get("level")
    if level is not None and level < pattern["level"]:
        ctx.state.trigger_safety_stop(
            f"Smithing level {level} is below the {pattern['level']} required for {bar_type} bars"
        )


def maybe_top_up_coffer(ctx, config: dict) -> None:
    """Blind, time-based coffer top-up -- see module header for why this can't be
    balance-aware. Checked once per tick alongside the main trip logic, same as how other
    routines interleave periodic side-actions (e.g. slayer.py's supply/prayer checks) with
    their main loop."""
    coffer_cfg = config.get("coffer", {})
    if not coffer_cfg.get("topUpEnabled"):
        return

    now = time.time()
    next_at = ctx.state.data.get("bf_coffer_next_topup_at")
    if next_at is not None and now < next_at:
        return

    interval_s = coffer_cfg.get("topUpIntervalMinutes", 30) * 60
    coffer = find_object(config, "Coffer")
    if coffer is None:
        print("[blast furnace] coffer not found nearby -- will retry shortly")
        ctx.state.data["bf_coffer_next_topup_at"] = now + 60
        return

    amount = coffer_cfg.get("topUpAmount", 40000)
    print(f"[blast furnace] topping up coffer with {amount} coins")
    controller.click_entity(ctx.ser, coffer)
    time.sleep(random.uniform(0.8, 1.2))  # wait for the amount prompt to appear
    controller.type_digits_and_enter(ctx.ser, str(amount))
    time.sleep(random.uniform(0.4, 0.8))
    ctx.state.data["bf_coffer_next_topup_at"] = now + interval_s


def plan_bank_withdrawals(trip: dict) -> tuple:
    """Given one trip's deposit list, compute the bank withdraw list and whether the coal
    bag needs filling. A "Coal" entry over COAL_BAG_CAPACITY splits into a full bag (27) plus
    loose coal withdrawn directly into inventory for the remainder (e.g. 54 Coal -> fill the
    bag, withdraw 27 loose)."""
    withdraw = []
    need_bag_fill = False
    for name, qty in trip["deposit"]:
        if name.lower() == "coal":
            need_bag_fill = True
            loose = max(0, qty - COAL_BAG_CAPACITY)
            if loose > 0:
                withdraw.append({"name": "Coal", "quantity": loose})
        else:
            withdraw.append({"name": name, "quantity": qty})
    return withdraw, need_bag_fill


def run_trip(ctx, config: dict, trip: dict) -> bool:
    """Execute one full trip: bank for this trip's materials (depositing any bars/leftovers
    from the previous trip along the way, via the normal keepItems-aware deposit), walk to
    the conveyor belt, deposit coal (if any) before the primary ore, then collect bars if
    this trip ends with a collect. Returns False if something is missing and a safety-stop or
    retry was already handled; True once the trip's deposits (and any collect) completed.

    Note: the Ferox teleport fallback cannot fill the coal bag (banker.bank_up doesn't
    support it), so it only works for bar types that don't require coal (bronze/iron/silver/gold)."""
    withdraw, need_bag_fill = plan_bank_withdrawals(trip)
    bank_cfg = {"keepItems": ["coal bag"], "withdraw": withdraw}

    bank_obj = banker.find_nearby_bank(bank_cfg)
    if bank_obj is not None:
        if not banker.open_bank(ctx, bank_obj):
            ctx.state.trigger_safety_stop("failed to open the Blast Furnace bank chest")
            return False
        banker.deposit_inventory(ctx, bank_cfg)
        if need_bag_fill:
            banker.fill_coal_bag(ctx)
        banker.withdraw_loadout(ctx, bank_cfg)
        banker.close_bank(ctx)
    elif config.get("bank", {}).get("teleportFallback"):
        if need_bag_fill:
            ctx.state.trigger_safety_stop(
                "the Ferox teleport fallback can't fill the coal bag -- disable Banking > Teleport If No Bank Nearby, "
                "or make sure a bank is reachable near the Blast Furnace for coal-needing bar types"
            )
            return False
        if not banker.bank_up(ctx, dict(bank_cfg, teleportFallback=True)):
            ctx.state.trigger_safety_stop("couldn't reach a bank to restock materials")
            return False
    else:
        ctx.state.trigger_safety_stop("no bank found near the Blast Furnace")
        return False

    conveyor = find_object(config, "Conveyor belt")
    if conveyor is None:
        ctx.state.trigger_safety_stop("conveyor belt not found nearby -- are you at the Blast Furnace?")
        return False

    if need_bag_fill:
        banker.empty_coal_bag(ctx)
        time.sleep(random.uniform(0.3, 0.6))

    controller.open_inventory_tab(ctx.ser)
    for name, qty in trip["deposit"]:
        deposited = deposit_items(ctx, name, qty, conveyor)
        if deposited < qty:
            ctx.state.trigger_safety_stop(
                f"only deposited {deposited}/{qty} {name} onto the conveyor belt -- ran out?"
            )
            return False

    if trip.get("collect"):
        dispenser = find_object(config, "Bar dispenser")
        if dispenser is None:
            ctx.state.trigger_safety_stop("bar dispenser not found nearby")
            return False
        print("[blast furnace] collecting bars")
        time.sleep(random.uniform(0.2, 0.4))
        controller.click_entity(ctx.ser, dispenser)
        time.sleep(random.uniform(0.6, 1.0))

    return True


def deposit_items(ctx, name: str, quantity: int, target: dict) -> int:
    """Deposit up to `quantity` inventory items named `name` onto `target` (the conveyor
    belt) via the item-then-target two-click idiom -- click the item, then click the
    conveyor. Ore/coal aren't stackable in inventory, so this is a real loop, one click pair
    per item, not a single bulk action. Each iteration re-looks-up the item fresh via
    find_item rather than reusing one slot reference repeatedly (unlike bank-slot
    withdrawals, which target a persistent bank-side stack): depositing an item removes it
    from its inventory slot and shifts the rest, so a stale reference from an earlier
    iteration could click whatever shifted into that slot instead. Returns how many were
    actually deposited, so the caller can tell a real shortfall (ran out of that item) from
    success."""
    deposited = 0
    for _ in range(quantity):
        item = find_item(name)
        if item is None:
            break
        controller.click_entity(ctx.ser, item, check_ui_blockers=False)
        time.sleep(random.uniform(0.1, 0.2))
        controller.click_entity(ctx.ser, target)
        time.sleep(random.uniform(0.2, 0.4))
        deposited += 1
    return deposited


def tick(ctx):
    config = ctx.state.config

    if not ctx.state.data.get("bf_level_checked"):
        check_level_requirement(ctx, config)
        ctx.state.data["bf_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    # Soft-retry only -- maybe_top_up_coffer never calls trigger_safety_stop itself (a missed
    # coffer top-up shouldn't halt an otherwise-working session); if the player genuinely
    # isn't at the Blast Furnace at all, run_trip's own bank/conveyor checks below will catch
    # that and safety-stop instead.
    maybe_top_up_coffer(ctx, config)

    bar_type = (config.get("barType") or "iron").lower()
    pattern = BAR_TYPES.get(bar_type)
    if pattern is None:
        ctx.state.trigger_safety_stop(f"unknown bar type '{bar_type}'")
        return

    trips = pattern["trips"]
    index = ctx.state.data.get("bf_trip_index", 0) % len(trips)
    trip = trips[index]

    if not run_trip(ctx, config, trip):
        return

    ctx.state.data["bf_trip_index"] = (index + 1) % len(trips)


def find_item(name: str):
    """First inventory item whose name contains `name` (case-insensitive substring, same
    idiom as firemaking.py/travel.py/banker.py) -- kept local rather than shared, matching
    this codebase's established convention of duplicating small per-file lookup helpers."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if name_lower in (i.get("name") or "").lower()),
        None,
    )


def find_object(config: dict, name: str):
    """Closest visible object whose name contains `name`, within maxObjectDistance -- same
    filter shape as mining.py's pick_rock/banker.py's find_nearby_bank, kept local per this
    codebase's established convention."""
    name_lower = name.lower()
    max_dist = config.get("maxObjectDistance", 15)

    candidates = [
        o for o in controller.get_objects()
        if name_lower in (o.get("name") or "").lower()
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= max_dist
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]
