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
#   - A genuinely cold furnace with no coal in it at all can yield the wrong (lower-tier) bar
#     if ore arrives with no coal ever present -- but see the ordering note in run_trip below
#     for why this routine deposits ore before the coal bag's contents on every trip after
#     the first (there's no free inventory slot to empty the bag into otherwise).
#   - Every trip pattern here already uses all 28 inventory slots (the wiki's own
#     maximally-efficient patterns), including the coal bag's own slot on coal-needing trips
#     -- there is no spare slot to also carry loose coins during a materials trip. So the
#     coffer top-up (see maybe_top_up_coffer) does its own separate, dedicated bank visit for
#     coins rather than folding coin-carrying into run_trip's ore/coal banking.
#   - The coal bag has no direct bag-to-conveyor shortcut: its coal must be emptied into
#     inventory first (banker.empty_coal_bag), then deposited item-by-item like loose coal --
#     but since every coal-needing trip already fills all 28 slots the moment materials are
#     withdrawn, the bag can't be emptied until *something* is deposited first to free room
#     for it. So run_trip deposits the primary ore, then any loose coal (withdrawn straight
#     into inventory), THEN empties the bag into the now-free slots, THEN deposits that too --
#     matching the money-making guides' own sequence (e.g. the runite guide: ore onto the
#     belt first, "then emptying your coal bag of coal and depositing that right after").
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
import debug_log

COAL_BAG_CAPACITY = 27

# Per-bar-type trip patterns, reproduced verbatim from Blast Furnace_Strategies.txt's "Bar
# patterns" table (which assumes a coal bag + ice gloves). Each trip is a "deposit" list of
# (item name, quantity) pairs -- listed here with "Coal" first for readability against the
# wiki table, though run_trip actually deposits the non-coal entries first at the conveyor
# (see its docstring for why) -- and a "collect" flag for whether to gather bars at the end
# of that trip.
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


def current_coins() -> int:
    coins = find_item("Coins")
    return coins.get("qty", 0) if coins else 0


def withdraw_coins_via_x(ctx, amount: int) -> bool:
    """Withdraw an exact coin amount using the bank's custom-quantity ("X") mode rather than
    banker.withdraw_loadout's per-unit click loop -- that loop clicks a slot once per unit of
    quantity (fine for the handful of potions/food every other routine withdraws), which for
    a coin amount in the tens of thousands would mean tens of thousands of individual clicks.

    ASSUMPTION, NOT YET VERIFIED LIVE: that selecting "X" mode first and then clicking the
    item is what triggers the chatbox "Enter amount" prompt (the same one the coffer's own
    Use action opens, hence reusing controller.type_digits_and_enter here) -- as opposed to
    the X button itself prompting and storing a quantity that a later plain click withdraws
    silently. If it's the latter, this either withdraws some previously-stored amount or
    nothing useful; maybe_top_up_coffer's post-withdrawal current_coins() check catches the
    resulting shortfall either way and soft-retries rather than proceeding on a false
    success, but verify this live before trusting unattended coffer top-ups -- same
    epistemic honesty as the module header's coffer-prompt assumption above.

    Requires the bank slot to actually hold at least `amount` coins (skips a placeholder --
    a real item entry with qty 0 left behind by a full withdrawal -- rather than clicking it
    and silently withdrawing nothing)."""
    if not banker.set_quantity_mode(ctx, "x"):
        return False
    bank = controller.get_bank()
    slot = next(
        (
            s for s in bank.get("items", [])
            if "coins" in (s.get("name") or "").lower() and s.get("qty", 0) >= amount
        ),
        None,
    )
    if slot is None:
        return False
    if not controller.click_entity(ctx.ser, slot):
        return False
    controller.human_delay(0.4, 0.7)  # wait for the amount prompt to appear
    controller.type_digits_and_enter(ctx.ser, str(amount))
    controller.human_delay(0.3, 0.5)
    return True


def maybe_top_up_coffer(ctx, config: dict) -> None:
    """Blind, time-based coffer top-up -- see module header for why this can't be
    balance-aware. Does its own dedicated bank visit for coins (withdrawing only if the
    current holding is short) rather than folding coin-carrying into run_trip's ore/coal
    banking: every trip pattern here already fills all 28 inventory slots with ore/coal, so
    there's no spare slot to also carry coins alongside a materials trip. This runs at the
    top of tick(), right after the previous trip's bar collection -- inventory can be
    completely full of bars at that moment, so deposit_inventory (with no keepItems at all)
    clears everything away before withdrawing coins, guaranteeing the coin stack has a slot.
    The withdrawn coins are spent into the coffer within this same call, so by the time
    run_trip runs later this tick, inventory is back to holding no loose coins and its own
    slot budgeting is undisturbed. Checked once per tick alongside the main trip logic, same
    as how other routines interleave periodic side-actions (e.g. slayer.py's supply/prayer
    checks) with their main loop."""
    coffer_cfg = config.get("coffer", {})
    if not coffer_cfg.get("topUpEnabled"):
        return

    now = time.time()
    next_at = ctx.state.data.get("bf_coffer_next_topup_at")
    if next_at is not None and now < next_at:
        return

    interval_s = coffer_cfg.get("topUpIntervalMinutes", 30) * 60
    amount = coffer_cfg.get("topUpAmount", 40000)

    if current_coins() < amount:
        bank_obj = banker.find_nearby_bank({})
        if bank_obj is None:
            debug_log.log("blast furnace", "no bank found for coffer top-up -- will retry shortly")
            ctx.state.data["bf_coffer_next_topup_at"] = now + 60
            return
        if not banker.open_bank(ctx, bank_obj):
            debug_log.log("blast furnace", "couldn't open the bank for coffer top-up -- will retry shortly")
            ctx.state.data["bf_coffer_next_topup_at"] = now + 60
            return
        banker.deposit_inventory(ctx, {"keepItems": []})
        withdrew = withdraw_coins_via_x(ctx, amount)
        banker.close_bank(ctx)
        controller.human_delay(0.3, 0.6)  # let the bank-closing transition settle
        if not withdrew or current_coins() < amount:
            debug_log.log("blast furnace", "couldn't withdraw enough coins for coffer top-up -- will retry shortly")
            ctx.state.data["bf_coffer_next_topup_at"] = now + 60
            return

    coffer = find_object(config, "Coffer")
    if coffer is None:
        debug_log.log("blast furnace", "coffer not found nearby -- will retry shortly")
        ctx.state.data["bf_coffer_next_topup_at"] = now + 60
        return

    debug_log.log("blast furnace", f"topping up coffer with {amount} coins")
    controller.click_entity(ctx.ser, coffer)
    controller.human_delay(0.8, 1.2)  # wait for the amount prompt to appear
    controller.type_digits_and_enter(ctx.ser, str(amount))
    controller.human_delay(0.4, 0.8)
    ctx.state.data["bf_coffer_next_topup_at"] = now + interval_s


def plan_bank_withdrawals(trip: dict) -> tuple:
    """Given one trip's deposit list, compute the bank withdraw list, whether the coal bag
    needs filling, and the loose/bag coal split. A "Coal" entry over COAL_BAG_CAPACITY splits
    into a full bag (27) plus loose coal withdrawn directly into inventory for the remainder
    (e.g. 54 Coal -> fill the bag, withdraw 27 loose). Returns
    (withdraw, need_bag_fill, loose_coal, bag_coal)."""
    withdraw = []
    need_bag_fill = False
    loose_coal = 0
    bag_coal = 0
    for name, qty in trip["deposit"]:
        if name.lower() == "coal":
            need_bag_fill = True
            bag_coal = min(qty, COAL_BAG_CAPACITY)
            loose_coal = max(0, qty - COAL_BAG_CAPACITY)
            if loose_coal > 0:
                withdraw.append({"name": "Coal", "quantity": loose_coal})
        else:
            withdraw.append({"name": name, "quantity": qty})
    return withdraw, need_bag_fill, loose_coal, bag_coal


def run_trip(ctx, config: dict, trip: dict) -> bool:
    """Execute one full trip: bank for this trip's materials (depositing any bars/leftovers
    from the previous trip along the way, via the normal keepItems-aware deposit), walk to
    the conveyor belt, deposit the primary ore and any loose coal before the coal bag's
    contents (see the ordering note where the deposit loop starts, below), then collect bars
    if this trip ends with a collect. Returns False if something is missing and a safety-stop
    or retry was already handled; True once the trip's deposits (and any collect) completed.

    Note: the Ferox teleport fallback cannot fill the coal bag (banker.bank_up doesn't
    support it), so it only works for bar types that don't require coal (bronze/iron/silver/gold).

    Coal handling: the coal bag is only kept across trips (not deposited) for bar types that
    actually need coal -- freeing its slot for bar types that don't, since every trip pattern
    here already budgets all 28 inventory slots and a carried-but-unneeded bag would overflow
    that by one (see plan_bank_withdrawals). If the bag isn't currently held (e.g. a prior
    non-coal trip banked it away, or this is the first coal-needing trip of the session),
    it's withdrawn back from the bank in its OWN withdraw_loadout call, before the materials
    withdrawal: the bridge's /bank endpoint is cached per game tick (BankProvider.java), so a
    same-tick re-fetch between two entries in a single withdraw_loadout call can still return
    a stale, pre-withdrawal snapshot -- putting the bag first in the SAME call (as an earlier
    version of this function tried) doesn't reliably avoid banker.withdraw_loadout's
    substring-matching "Coal" search resolving to a still-cached "Coal bag" slot. Confirming the
    bag is actually gone from `/bank` is now done via `banker.wait_until_bank_item_gone`'s poll
    loop right after the bag withdrawal, since a single `fresh=True` read taken immediately after
    the withdrawal click can still race the server -- `fresh=True` only bypasses the bridge's own
    cache, it doesn't guarantee the withdrawal has actually been processed server-side yet (that
    still resolves on a real game-tick boundary). The follow-up `fresh=True` passed to the
    materials withdrawal's own `withdraw_loadout` call is now just cheap extra insurance in case a
    tick boundary passed between the two calls, not the thing that actually guarantees the state.
    Loose coal is deposited before the bag is
    emptied, since the bag can only be emptied into slots that are actually free (see module
    header). Coffer coins are handled entirely separately by maybe_top_up_coffer's own
    dedicated bank visit, so this function never carries coins itself."""
    withdraw, need_bag_fill, loose_coal, bag_coal = plan_bank_withdrawals(trip)

    keep_items = []
    need_bag_withdraw = False
    if need_bag_fill:
        keep_items.append("coal bag")
        need_bag_withdraw = not banker.has_coal_bag()

    bank_cfg = {"keepItems": keep_items, "withdraw": withdraw}

    bank_obj = banker.find_nearby_bank(bank_cfg)
    if bank_obj is not None:
        if not banker.open_bank(ctx, bank_obj):
            ctx.state.trigger_safety_stop("failed to open the Blast Furnace bank chest")
            return False
        # The bank's quantity-mode selector (1/5/10/X/All) persists across close/reopen, so a
        # prior maybe_top_up_coffer visit earlier this same tick can leave it on "X" -- reset
        # before deposit_inventory's keepItems-aware path (which clicks individual items,
        # unlike the mode-independent deposit-all button) runs in whatever mode was left over.
        banker.set_quantity_mode(ctx, "one")
        banker.deposit_inventory(ctx, bank_cfg)
        if need_bag_withdraw:
            banker.withdraw_loadout(ctx, {"withdraw": [{"name": "coal bag", "quantity": 1}]})
            # A single fresh=True read isn't enough here -- CONFIRMED (final branch review): it
            # guarantees the bridge's cache isn't stale, but not that this withdrawal has actually
            # been processed server-side yet (that still resolves on a real game-tick boundary), so
            # a fresh read taken immediately after the click can still race the server and observe
            # the coal bag as still present. Poll until it's actually gone instead of trusting a
            # single read or a fixed sleep.
            banker.wait_until_bank_item_gone(ctx, "coal bag")
        # fresh=True (only actually needed when the bag was just withdrawn above) forces this
        # call's /bank reads to bypass the bridge's per-tick cache instead of waiting out a full
        # game tick for it to naturally reflect the bag being gone -- see the docstring above and
        # withdraw_loadout's own fresh parameter.
        banker.withdraw_loadout(ctx, bank_cfg, fresh=need_bag_withdraw)
        if need_bag_fill:
            controller.human_delay(0.3, 0.5)  # let a just-withdrawn bag register in
                                                    # get_inventory() before fill_coal_bag
                                                    # looks for it
            if not banker.fill_coal_bag(ctx):
                banker.close_bank(ctx)
                ctx.state.trigger_safety_stop("no coal bag in inventory or bank -- this bar type needs one")
                return False
        banker.close_bank(ctx)
        controller.human_delay(0.3, 0.6)  # let the bank-closing transition settle
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

    controller.open_inventory_tab(ctx.ser)

    # Deposit the primary ore (and any loose coal beyond the bag's capacity) FIRST, before
    # touching the bag -- withdraw_loadout above already filled every slot this trip budgets
    # for (see plan_bank_withdrawals), so the coal bag has nowhere to empty into until
    # something else has been deposited to free room. This also matches the real click order
    # the money-making guides describe for coal-bag trips (e.g. the runite guide: ore onto
    # the belt first, "then emptying your coal bag of coal and depositing that right after"),
    # not a strict coal-always-first rule. The coal-before-ore caution in Blast Furnace.txt is
    # about a genuinely cold furnace with no coal in it at all, not the steady-state cycle
    # this routine repeats -- watch the very first cycle closely during live verification
    # (Step 3) in case a cold start needs an explicit priming trip this plan didn't include
    # for Steel specifically.
    for name, qty in trip["deposit"]:
        if name.lower() == "coal":
            continue  # coal (loose and/or bagged) is handled below, after ore frees room
        deposited = deposit_items(ctx, name, qty, conveyor)
        if deposited < qty:
            ctx.state.trigger_safety_stop(
                f"only deposited {deposited}/{qty} {name} onto the conveyor belt -- ran out?"
            )
            return False

    if loose_coal > 0:
        deposited = deposit_items(ctx, "Coal", loose_coal, conveyor)
        if deposited < loose_coal:
            ctx.state.trigger_safety_stop(
                f"only deposited {deposited}/{loose_coal} loose Coal onto the conveyor belt -- ran out?"
            )
            return False

    if need_bag_fill and bag_coal > 0:
        if not banker.empty_coal_bag(ctx):
            ctx.state.trigger_safety_stop("couldn't empty the coal bag at the conveyor belt")
            return False
        controller.human_delay(0.3, 0.6)
        deposited = deposit_items(ctx, "Coal", bag_coal, conveyor)
        if deposited < bag_coal:
            ctx.state.trigger_safety_stop(
                f"only deposited {deposited}/{bag_coal} bagged Coal onto the conveyor belt -- ran out?"
            )
            return False

    if trip.get("collect"):
        dispenser = find_object(config, "Bar dispenser")
        if dispenser is None:
            ctx.state.trigger_safety_stop("bar dispenser not found nearby")
            return False
        debug_log.log("blast furnace", "collecting bars")
        controller.human_delay(0.2, 0.4)
        controller.click_entity(ctx.ser, dispenser)
        controller.human_delay(0.6, 1.0)

    return True


def deposit_items(ctx, name: str, quantity: int, target: dict) -> int:
    """Deposit up to `quantity` inventory items named `name` onto `target` (the conveyor
    belt) via the item-then-target two-click idiom -- click the item, then click the
    conveyor. Ore/coal aren't stackable in inventory, so this is a real loop, one click pair
    per item, not a single bulk action. Each iteration re-looks-up the item fresh via
    find_item rather than reusing one slot reference repeatedly (unlike bank-slot
    withdrawals, which target a persistent bank-side stack): depositing an item empties its
    inventory slot, so a stale reference from an earlier iteration could click whatever ends
    up in that slot next instead. Only counts an item as deposited when BOTH clicks return
    True (click_entity returns False on a blocked or off-window aim point; note this does
    NOT catch every near-miss -- a small chance of an intentionally jittered "close but not
    on target" click still resolves as a normal, successful click in the game's own menu
    resolution, so the shortfall check below is a real improvement but not an absolute
    guarantee) -- an unregistered attempt sleeps briefly and tries again next iteration
    against a fresh find_item lookup, up to the `quantity` cap. Returns how many were
    actually deposited, so the caller can tell a real shortfall (ran out of that item) from
    success."""
    deposited = 0
    for _ in range(quantity):
        item = find_item(name)
        if item is None:
            break
        if not controller.click_entity(ctx.ser, item, check_ui_blockers=False):
            controller.human_delay(0.2, 0.4)
            continue
        controller.human_delay(0.15, 0.3)
        if not controller.click_entity(ctx.ser, target):
            controller.human_delay(0.2, 0.4)
            continue
        controller.human_delay(0.3, 0.5)
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
    """Inventory item whose name matches `name` exactly (case-insensitive) -- exact rather
    than substring match. This file only ever looks up fixed canonical item names (e.g.
    "Coal", "Iron ore", "Coins", "coal bag"), and a substring match would wrongly match a
    same-named container too -- e.g. "Coal" matching "Coal bag", the very item this routine
    also carries. Unlike firemaking.py's find_item, which intentionally supports arbitrary
    user-configured substring names, there's no equivalent need for that flexibility here."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if (i.get("name") or "").lower() == name_lower),
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
