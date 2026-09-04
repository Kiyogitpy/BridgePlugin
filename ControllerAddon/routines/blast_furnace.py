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
#     cooling); already own a coal bag. EXCEPTION for gold with Goldsmith gauntlets owned:
#     the glove-swap method activates itself and wears each pair at the right moment -- see
#     the "Gold glove-swap method" section below BAR_VARBITS.
#   - Loading the conveyor belt: a single left-click on the conveyor belt itself is enough --
#     CORRECTED 2026-08-22 (user report, live session): this used to assume a two-click "use"
#     action (click the ore/coal item, then click the conveyor belt, the same item-then-target
#     idiom routines/firemaking.py uses for tinderbox+log). CONFIRMED live that's wrong: the
#     conveyor's own left-click default action ("Put-ore-on") automatically consumes every
#     matching ore/coal already in inventory on its own -- one click starts an action that runs
#     until nothing matching is left, it does not need to be re-clicked per item or per ore
#     type. See deposit_via_conveyor.
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

import time

import banker
import bridge_events
import controller
import debug_log
import settings
import travel

COAL_BAG_CAPACITY = 27

# Varbit IDs the official RuneLite Blast Furnace plugin itself reads to know how many bars of
# each type are currently sitting in the bar dispenser, ready to collect (BarsOres.java in
# net.runelite.client.plugins.blastfurnace, decompiled from client-1.12.36.jar -- see
# BlastFurnaceOverlay.render's use of BarsOres.getVarbit()/Client.getVarbitValue). ADDED
# 2026-08-22 (user directive, live session): lets run_trip's collect step confirm a dispenser
# click actually produced bars instead of trusting a blind click+Space -- see PlayerProvider's
# new /varbit endpoint (controller.get_varbit) and run_trip's collect block below.
BAR_VARBITS = {
    "bronze": 941,
    "iron": 942,
    "steel": 943,
    "mithril": 944,
    "adamantite": 945,
    "runite": 946,
    "silver": 948,
    "gold": 947,
}

# --- Gold glove-swap method (added 2026-08-31, from a human recording) -------------------
#
# recordings/2026-08-31_000524_blast-furnace-gold.* (65s, 3 full 27-bar cycles) shows the
# goldsmith-gauntlets XP method, and everything below is MEASURED from it, not assumed:
#   - The spare glove pair rides in inventory (one pair worn, one carried), so Withdraw-All
#     gold ore yields 27 and the "You don't have enough inventory space to withdraw that
#     many." chat line is NORMAL on every single withdrawal, never an error.
#   - Goldsmith gauntlets must be WORN at the moment the furnace smelts (56.2 xp/bar vs
#     22.5): varbit 955 (gold ore in furnace) = 27, then ~3s later varbit 947 (gold bars in
#     dispenser) = 27 lands on the SAME tick as the Smithing XP drop, ~9s after the conveyor
#     click. varbit 947 is therefore the "smelt done, swap now" trigger -- it needs no client
#     rebuild, unlike a smithingXp field on /player would.
#   - On that signal the human wears Ice gloves (plain left-click on the inventory pair),
#     takes the bars (Take Bar dispenser + Space), then wears the gauntlets back ON THE WAY
#     to the bank. USER (2026-08-31): "when experience drops we swap to ice gloves and pickup
#     bars then after picked up we swap them on the way to bank".
#
# The method activates itself: barType gold + both pairs present (worn or carried) -- see
# gold_glove_swap_active. Without the gauntlets, gold runs exactly as before (ice gloves
# permanently worn, no waits added), so no new Java config toggle was needed.
GOLDSMITH_GAUNTLETS = "Goldsmith gauntlets"
ICE_GLOVES = "Ice gloves"
# Smiths gloves (i) (cache obj 27031, "stuffed with a pair of ice gloves") ARE ice gloves for
# every purpose here -- user request 2026-09-04. Whichever pair the account has is the ice pair.
ICE_GLOVE_NAMES = (ICE_GLOVES, "Smiths gloves (i)")
# Measured ~9s from conveyor click to varbit 947 = 27; generous headroom on top because a
# busy mass world adds server lag, but small enough that a genuinely stalled furnace (empty
# coffer -- the dwarves stop working) still fails the trip promptly.
BARS_READY_TIMEOUT_S = 25.0
GLOVE_WEAR_TIMEOUT_S = 4.0
# Fallback smelt wait when /varbit can't answer (un-rebuilt client): blind but roughly the
# measured smelt length, so the collect click lands after the bars exist.
SMELT_BLIND_WAIT_RANGE_S = (8.5, 10.5)

# --- Event-driven outcomes (2026-09-04, docs/superpowers/specs/2026-09-04-event-driven-outcomes-
# design.md; USER: "optimise blast furnace to be as much subtick as possible") ------------------
# Every wait below goes through _await: a pushed UDP signal decides WHEN to look, one HTTP read
# decides WHAT happened, and the HTTP read alone decides at the budget -- so an old client or a
# dropped packet costs latency, never a wrong click. Baseline per gold trip (bf_live2, 2026-08-31):
# ~27s, ~9s of it the smelt itself; the rest was 0.3-0.9s fixed delays and 0.3-0.6s polls.
VB_CHAT_MODAL = 10670             # chatmodal_unclamp: 1 while a chat modal (the dispenser's) is up
VARBIT_SUBSCRIPTIONS = tuple(BAR_VARBITS.values()) + (VB_CHAT_MODAL,)   # pushed as VARBIT_CHANGED
BANK_WIDGET_GROUP = 12            # InterfaceID.Bankmain
DIALOG_WIDGET_GROUPS = (229, 231, 219)   # Messagebox, ChatLeft (npc/player chat), Chatmenu
DISPENSER_DIALOG_BUDGET_S = 2.5   # dispenser click -> its dialogue, plus the walk
COLLECT_CONFIRM_S = 3.0           # Space -> dispenser varbit reads 0 / bars in the inventory
CONVEYOR_STALL_S = 4.0            # no inventory delta and no movement this long -> reclick the belt
CONVEYOR_CLICK_TRIES = 4          # refused (UI-blocked) conveyor clicks re-resolved before giving up
BANK_CLOSE_BUDGET_S = 0.8         # ESC -> bank interface gone


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


def _await(events, signals, budget_s, confirm, reaction=True, shorten=None, since=None):
    """bridge_events.await_outcome on this module's clock, with a PRE-CHECK: when confirm()
    already holds (the bars were ready before we asked, the gloves already on) return at once
    instead of waiting for a signal that has already been and gone."""
    if confirm is not None:
        value = confirm()
        if value:
            if reaction:
                controller.human_delay(*settings.REACTION_DELAY_S)
            return bridge_events.Outcome(True, None, None, value, 0.0)
    return bridge_events.await_outcome(events, signals, budget_s, confirm=confirm, since=since,
                                       reaction=reaction, shorten=shorten,
                                       now_fn=_now, sleep_fn=_sleep)


def _mark(ctx):
    events = getattr(ctx, "events", None)
    return events.mark() if events is not None else None


def _dialog_open() -> bool:
    return bool((controller.get_state() or {}).get("dialogOpen"))


def _dialog_widget(payload) -> bool:
    return payload.get("groupId") in DIALOG_WIDGET_GROUPS


# MEASURED bf_live7 07:11:59 (widget dumps every 150ms through a collect): Take -> ~1.5s ->
# group 270 ("Choose a quantity, then click an item to begin." / "How many would you like to
# take?"), the same skill-selection interface Giants' Foundry's crucible uses -> Space answers
# it -> group 229 "You take 27 gold bars from the dispenser. Click here to continue", which
# needs no dismissal (walking off closes it) -> the bars are in the inventory. Neither
# /state.dialogOpen nor the m8aq dialogue reader sees group 270; /widget does.
TAKE_PROMPT_GROUP = 270
TAKE_PROMPT_CHILD = 5


def _take_prompt_open() -> bool:
    w = controller.get_widget(TAKE_PROMPT_GROUP, TAKE_PROMPT_CHILD) or {}
    return bool(w.get("clickable"))


def _bank_widget(payload) -> bool:
    return payload.get("groupId") == BANK_WIDGET_GROUP


def _await_bank_closed(ctx) -> None:
    """After ESC: the bank interface going away (WIDGET_CLOSED 12, confirmed by /bank.open)
    -- replaces the flat 0.3-0.6s "let the bank-closing transition settle" delay."""
    _await(getattr(ctx, "events", None), {"WIDGET_CLOSED": _bank_widget}, BANK_CLOSE_BUDGET_S,
           confirm=lambda: not banker.is_open())


def gold_glove_swap_active(config: dict) -> bool:
    """Whether this session should run the goldsmith-gauntlets swap method: barType is gold
    and BOTH glove pairs are present -- each either worn or in inventory (after a restart
    mid-cycle the ice gloves can be the worn pair, with the gauntlets carried). Read fresh
    from /equipment + /inventory rather than cached in ctx.state.data, so losing a pair
    mid-session (shouldn't happen -- keepItems protects them -- but state.data resets have
    desynced from live inventory before, see the module header of golem_crafting.py) degrades
    to the plain method next trip instead of wedging on a missing item."""
    if (config.get("barType") or "").lower() != "gold":
        return False
    worn = ((controller.get_equipment().get("gloves") or {}).get("name") or "").lower()
    carried = {(i.get("name") or "").lower() for i in controller.get_inventory()}

    def present(name: str) -> bool:
        return worn == name.lower() or name.lower() in carried

    return present(GOLDSMITH_GAUNTLETS) and any(present(n) for n in ICE_GLOVE_NAMES)


def ice_glove_pair() -> str:
    """The ice pair this account actually has -- Ice gloves or Smiths gloves (i) -- worn or
    carried, read fresh like gold_glove_swap_active. Falls back to plain Ice gloves when
    neither is visible so the caller's error message names something sensible."""
    worn = ((controller.get_equipment().get("gloves") or {}).get("name") or "").lower()
    carried = {(i.get("name") or "").lower() for i in controller.get_inventory()}
    for name in ICE_GLOVE_NAMES:
        if worn == name.lower() or name.lower() in carried:
            return name
    return ICE_GLOVES


def ensure_gloves_worn(ctx, name: str) -> bool:
    """Make sure the named glove pair is the one currently worn, wearing it from inventory if
    not (plain left-click on the item -- its default action is Wear, the same CC_OP the
    recording shows on widget 149.0; same click_entity-on-inventory-item pattern
    tormented_demon.py's equip_starting_loadout already uses live). Verified against
    /equipment's gloves slot rather than trusting the click, with one full re-click retry --
    an inventory click can silently miss like any other. Returns False only when the pair
    can't be worn at all (missing from both hands and inventory, or the wear never
    registered); callers decide whether that's fatal."""
    name_lower = name.lower()

    def worn() -> bool:
        gloves = controller.get_equipment().get("gloves") or {}
        return (gloves.get("name") or "").lower() == name_lower

    if worn():
        return True

    controller.open_inventory_tab(ctx.ser)
    for _ in range(2):
        if banker.is_open():
            # With the bank interface up, a left-click on an inventory item DEPOSITS it --
            # the swap-on-the-run (bf_live10) can reach a retry here with the bank already
            # open. Never click; the pre-deposit gate wears the pair after the bank closes.
            return worn()
        item = find_item(name)
        if item is None:
            return worn()
        # check_ui_blockers=False, same as every other interface-element click in this
        # codebase (see the /widget section of CLAUDE.md for the rotate-forever loop the
        # default causes on interface clicks).
        mark = _mark(ctx)
        controller.click_entity(ctx.ser, item, check_ui_blockers=False)
        # Wearing swaps the pairs, so the INVENTORY changes the instant the wear lands --
        # that is the signal; /equipment's gloves slot is the truth.
        if _await(getattr(ctx, "events", None), "INVENTORY_CHANGED", GLOVE_WEAR_TIMEOUT_S,
                  confirm=worn, since=mark):
            return True
    return worn()


# The tile the human parks on during the smelt wait (USER walked their character onto it live
# 2026-08-31 and said "look my tile im standing on"; it is also the recording's walk
# destinations 4 and 8, and the tile every Take Bar dispenser click fired from, at dist 1 to
# the dispenser at (1940,4963)). The Blast Furnace room is NOT instanced, so a fixed world
# tile is safe to hardcode -- same reasoning as chinchompa_hunting's cave tiles.
DISPENSER_WAIT_TILE = (1940, 4964, 0)


def _prehover_item(ctx, name: str) -> bool:
    """Rest the cursor on the named inventory item (inventory tab opened first) so the click
    that follows is a click in place. A courtesy move: False when the item is not in the
    inventory or the hover is refused, never an error."""
    controller.open_inventory_tab(ctx.ser)
    item = find_item(name)
    if item is None:
        return False
    return bool(controller.hover_entity(ctx.ser, item, check_ui_blockers=False))


def walk_to_dispenser_wait_tile(ctx) -> None:
    """Fire-and-forget walk to the dispenser standing tile, issued right after the conveyor
    deposit so the ~9s smelt wait is spent WALKING there instead of standing at the belt --
    the human does exactly this, and it puts the ice-glove swap and the Take click at dist 1
    the moment the XP pops. Non-blocking on purpose (wait_for_bars_ready owns the waiting);
    travel.walk_to_exact_tile handles the shift+click (force-walk, per CLAUDE.md's standing
    rule for plain ground tiles) and early-outs free if already standing there. Best-effort:
    a failed projection just means the wait happens where we stand, so the return value is
    deliberately ignored."""
    x, y, plane = DISPENSER_WAIT_TILE
    travel.walk_to_exact_tile(ctx, x, y, plane, wait_for_arrival=False)


def wait_for_bars_ready(varbit_id, timeout_s: float = BARS_READY_TIMEOUT_S, events=None) -> bool:
    """Wait for the bar-dispenser varbit to report bars ready (the recording's smelt-done
    signal -- lands on the same tick as the Smithing XP drop, so this IS "when experience
    drops" in signal terms). Event-driven since 2026-09-04: the subscribed VARBIT_CHANGED for
    this bar type or the Smithing XP_GAINED decides when to read the varbit; the varbit read
    alone decides at the budget. A None varbit read means "cannot verify" (un-rebuilt client,
    or an unmapped bar type), never "no bars": fall back to one blind measured-smelt-length
    wait and report success, same can't-verify posture as collect_bars' own probe. Returns
    False only on a verified timeout -- bars provably never appeared."""
    if varbit_id is None or controller.get_varbit(varbit_id) is None:
        controller.human_delay(*SMELT_BLIND_WAIT_RANGE_S)
        return True
    signals = {"VARBIT_CHANGED": lambda p: p.get("varbitId") == varbit_id and (p.get("value") or 0) > 0,
               "XP_GAINED": lambda p: p.get("skill") == "Smithing"}
    res = _await(events, signals, timeout_s,
                 confirm=lambda: controller.get_varbit(varbit_id) not in (0, None))
    if res:
        debug_log.log("blast furnace", f"bars ready via {res.signal or 'varbit read'} "
                                       f"after {res.latency_s:.2f}s")
    return bool(res)

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
        # CORRECTED 2026-08-22 (user report): the wiki's own single-trip "27 Coal, 27 Iron"
        # pattern carries a footnote -- "Load furnace with at least 27 coal before starting, or
        # iron bars will be made" -- which this routine's steady-state loop was silently
        # violating: repeating a single 27-coal+27-iron trip forever deposits both onto the
        # conveyor in the SAME action, but with no coal ever banked in the furnace ahead of
        # time, ore can land before the furnace has any coal to consume it with, yielding iron
        # bars instead of steel (see the module header's ordering note, and Blast Furnace.txt's
        # own "bulk-loading coal, if needed, every few cycles" line -- the furnace keeps a coal
        # reserve across trips, it isn't consumed strictly same-trip-only). Fix: a pure-coal
        # priming trip (54 Coal = 27 in the bag + 27 loose, no ore, no collect) followed by TWO
        # pure-iron trips (27 Iron ore each, no coal carried, collect) -- CORRECTED again same
        # day (user report): consumption is 1 coal per steel bar, not 2, so one priming trip's
        # 54 coal covers two 27-iron trips' worth of bars (54 total), not just one. The cycle
        # repeats sustainably forever via tick()'s existing trip-index wraparound (same
        # priming-trip shape mithril/adamantite/runite already use below, just with two
        # ore-only trips per priming trip here instead of one ore-with-coal trip each).
        "trips": [
            {"deposit": [("Coal", 54)], "collect": False},
            {"deposit": [("Iron ore", 27)], "collect": True},
            {"deposit": [("Iron ore", 27)], "collect": True},
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

    now = _now()
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
        _await_bank_closed(ctx)           # the bank interface is gone (was a flat 0.3-0.6s)
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


def run_trip(ctx, config: dict, trip: dict, bar_uses_coal_bag: bool) -> bool:
    """Execute one full trip: bank for this trip's materials (depositing any bars/leftovers
    from the previous trip along the way, via the normal keepItems-aware deposit), walk to
    the conveyor belt, deposit the primary ore and any loose coal before the coal bag's
    contents (see the ordering note where the deposit loop starts, below), then collect bars
    if this trip ends with a collect. Returns False if something is missing and a safety-stop
    or retry was already handled; True once the trip's deposits (and any collect) completed.

    Note: the Ferox teleport fallback cannot fill the coal bag (banker.bank_up doesn't
    support it), so it only works for bar types that don't require coal (bronze/iron/silver/gold).

    `bar_uses_coal_bag` (whether ANY trip in the active bar type's pattern needs coal, computed
    once by tick() from the whole trip list -- see plan_bank_withdrawals for the per-trip
    `need_bag_fill` this is deliberately distinct from) controls whether the coal bag's slot is
    kept across EVERY trip of that bar type, not just the ones that fill it. CORRECTED
    2026-08-22 (user report, live session): this used to key off the current trip's own
    `need_bag_fill` instead, freeing the bag's slot on any trip that doesn't itself deposit
    coal -- fine for bar types with only one trip pattern, but wrong for steel's alternating
    priming-trip/iron-only-trip cycle: an iron-only trip has no "Coal" entry of its own
    (need_bag_fill False), so the bag got deposited away and its slot's worth of extra room let
    shift-withdrawing "all" Iron ore grab 28 instead of the intended 27, drifting the two
    iron-only trips' totals (28 then 26) out of sync with the one-time 54-coal priming budget --
    CONFIRMED live this produced iron bars later once the imbalance ran the furnace's coal
    reserve out early. Keeping the bag (even empty) reserves its slot on every trip of a
    coal-using bar type, so a single-item withdraw list's shift-click-all is naturally capped at
    27 instead of 28. If the bag isn't currently held (e.g. this is the very first trip of the
    session), it's withdrawn back from the bank in its OWN withdraw_loadout call, before the
    materials withdrawal: the bridge's /bank endpoint is cached per game tick (BankProvider.java),
    so a same-tick re-fetch between two entries in a single withdraw_loadout call can still return
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
    # Clear the dispenser FIRST, before withdrawing any new materials -- CORRECTED 2026-08-22
    # (user report, live session): an earlier version of this check ran later, right before the
    # conveyor deposit but AFTER the materials withdrawal -- CONFIRMED live that's exactly
    # backwards: by then inventory is already full of the ore/coal just withdrawn for THIS
    # trip, so a stuck-full dispenser had nowhere to put collected bars and the routine got
    # stuck re-clicking with a full inventory instead of actually collecting anything. Run this
    # here instead, at the very top of the trip, while inventory still holds whatever was left
    # over from the PREVIOUS trip's own end-of-trip collect (typically near-empty, or at worst
    # a few uncollected bars) -- see collect_bars' own docstring for why checking every trip
    # (not just the ones this pattern schedules a collect on) matters at all.
    #
    # glove_swap resolved once per trip, up front, so the pre-trip dispenser clearing below
    # gets the ice-glove wrap too -- a restart with 27 hot bars stranded in the dispenser
    # would otherwise try to grab them wearing the gauntlets.
    glove_swap = gold_glove_swap_active(config)
    if not collect_bars(ctx, config, glove_swap=glove_swap, swap_back=False, prehover_bank=True):
        return False

    withdraw, need_bag_fill, loose_coal, bag_coal = plan_bank_withdrawals(trip)

    keep_items = []
    if (config.get("barType") or "").lower() == "gold":
        # The spare pair's slot must survive every bank deposit -- and, like the coal bag's
        # kept slot, it naturally caps shift-withdraw-all at 27 gold ore (the recording's own
        # trip size; the "not enough inventory space" chat on each withdrawal is normal).
        # Every glove name is kept: one is worn, the other carried, and which is which
        # varies; a name the account does not own keeps nothing. Kept for EVERY gold trip,
        # not only when the swap is active -- LIVE 2026-09-04 the swap read inactive (the
        # account's ice pair is Smiths gloves (i), which the check did not know) and the
        # deposit-all banked the carried Goldsmith gauntlets. A missed detection must never
        # cost the pair again.
        keep_items += [*ICE_GLOVE_NAMES, GOLDSMITH_GAUNTLETS]
    need_bag_withdraw = False
    if bar_uses_coal_bag:
        keep_items.append("coal bag")
        need_bag_withdraw = not banker.has_coal_bag()

    bank_cfg = {"keepItems": keep_items, "withdraw": withdraw}

    bank_obj = banker.find_nearby_bank(bank_cfg)
    if bank_obj is not None:
        if glove_swap:
            _swap_back_on_the_run(ctx, bank_obj)
        if not banker.open_bank(ctx, bank_obj):
            ctx.state.trigger_safety_stop("failed to open the Blast Furnace bank chest")
            return False
        # The bank's quantity-mode selector (1/5/10/X/All) persists across close/reopen, so a
        # prior maybe_top_up_coffer visit earlier this same tick can leave it on "X" -- CORRECTED
        # 2026-08-22 (user report, live session): this used to unconditionally reset it to "one"
        # on EVERY bank visit, even trips where nothing downstream actually reads the mode --
        # CONFIRMED live this was clicking "1" for no reason on every single trip. Neither
        # deposit_inventory (shift+click, mode-independent, see its own docstring) nor
        # withdraw_all_shift (also shift+click) care about the mode at all; only
        # withdraw_loadout's per-unit-click loop does (bronze's Copper+Tin split, and the coal
        # bag's own single-unit withdraw below), so only reset when one of those is actually
        # about to run this trip.
        if need_bag_withdraw or len(withdraw) != 1:
            banker.set_quantity_mode(ctx, "one")
        banker.deposit_inventory(ctx, dict(bank_cfg, skipContainerDeposit=True))
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
        #
        # ADDED 2026-08-22 (user report, live session): when `withdraw` is a single entry (every
        # bar type except bronze's Copper+Tin split -- see plan_bank_withdrawals), shift-click
        # withdraw-all is a single click instead of withdraw_loadout's one-click-per-unit loop
        # (up to 27-28 clicks for a full-inventory ore trip). Not safe for a multi-entry withdraw
        # (bronze): shift-clicking Copper first would happily fill all 28 slots with copper and
        # leave none for Tin, so that case still goes through the exact-quantity loop below.
        if len(withdraw) == 1:
            if not banker.withdraw_all_shift(ctx, withdraw[0]["name"], fresh=need_bag_withdraw):
                banker.close_bank(ctx)
                ctx.state.trigger_safety_stop(
                    f"shift-click withdrawal of {withdraw[0]['name']} never showed up in inventory"
                )
                return False
        else:
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
        _await_bank_closed(ctx)           # the bank interface is gone (was a flat 0.3-0.6s)
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

    # Just an up-front presence check here ("are we even at the Blast Furnace") -- the actual
    # click target is re-fetched fresh inside deposit_via_conveyor immediately before each
    # click, not this snapshot. CORRECTED 2026-08-22 (user report, live session): this used to
    # be the exact dict clicked later too, but that reused a screenX/screenY snapshot taken
    # here, before the bank trip/coal-bag emptying that happens in between -- CONFIRMED live the
    # camera can drift enough in that gap for the cached position to go stale and miss.
    if find_conveyor(config) is None:
        ctx.state.trigger_safety_stop("conveyor belt not found nearby -- are you at the Blast Furnace?")
        return False

    # The gauntlets must be worn BEFORE the ore goes onto the belt: the XP is granted the
    # moment the furnace smelts, ~9s after the conveyor click (see the glove-swap header
    # above). Normally a free /equipment read -- the swap-back after the previous collect
    # already put them on; this catches the first trip of a session and any missed swap-back.
    if glove_swap and not ensure_gloves_worn(ctx, GOLDSMITH_GAUNTLETS):
        ctx.state.trigger_safety_stop(
            f"couldn't wear {GOLDSMITH_GAUNTLETS} before smelting -- is the pair missing?"
        )
        return False

    controller.open_inventory_tab(ctx.ser)

    # Deposit the primary ore (and any loose coal beyond the bag's capacity) FIRST, before
    # touching the bag -- withdraw_loadout/withdraw_all_shift above already filled every slot
    # this trip budgets for (see plan_bank_withdrawals), so the coal bag has nowhere to empty
    # into until something else has been deposited to free room. This also matches the real
    # click order the money-making guides describe for coal-bag trips (e.g. the runite guide:
    # ore onto the belt first, "then emptying your coal bag of coal and depositing that right
    # after"), not a strict coal-always-first rule. The coal-before-ore caution in Blast
    # Furnace.txt is about a genuinely cold furnace with no coal in it at all, not the
    # steady-state cycle this routine repeats. CONFIRMED live (user report, 2026-08-22) that
    # Steel specifically DID need an explicit priming trip -- see BAR_TYPES["steel"]'s own note
    # above for the alternating coal/iron fix.
    #
    # A single click on the conveyor deposits every matching ore/coal already in inventory at
    # once (see deposit_via_conveyor) -- so the primary ore and any loose coal go in the SAME
    # click here rather than one click pair per item.
    ore_names = [name for name, qty in trip["deposit"] if name.lower() != "coal"]
    ore_qty = sum(qty for name, qty in trip["deposit"] if name.lower() != "coal")
    deposit_names = list(ore_names)
    if loose_coal > 0:
        deposit_names.append("Coal")
    if deposit_names:
        expected = ore_qty + loose_coal
        deposited = deposit_via_conveyor(ctx, config, deposit_names)
        if deposited < expected:
            ctx.state.trigger_safety_stop(
                f"only deposited {deposited}/{expected} ore/coal onto the conveyor belt -- ran out?"
            )
            return False

    if need_bag_fill and bag_coal > 0:
        if not banker.empty_coal_bag(ctx):
            ctx.state.trigger_safety_stop("couldn't empty the coal bag at the conveyor belt")
            return False
        controller.human_delay(0.3, 0.6)
        deposited = deposit_via_conveyor(ctx, config, ["Coal"])
        if deposited < bag_coal:
            ctx.state.trigger_safety_stop(
                f"only deposited {deposited}/{bag_coal} bagged Coal onto the conveyor belt -- ran out?"
            )
            return False

    if trip.get("collect"):
        if glove_swap:
            # Spend the smelt wait walking to the dispenser's standing tile (non-blocking;
            # see walk_to_dispenser_wait_tile) so the swap-and-take starts from dist 1.
            walk_to_dispenser_wait_tile(ctx)
            # USER 2026-09-04: "when running from conveyor we can optimise by hovering the
            # gloves in wait for experience to drop" -- park the cursor on the ice pair during
            # the smelt so the wear at the XP drop is a click in place, not a ~1s flight
            # (bf_live6: 1.2s from the XP event to the wear landing).
            _prehover_item(ctx, ice_glove_pair())
            # Wait for the smelt to finish (varbit 947 flipping to the bar count, the same
            # tick as the XP drop) before touching the dispenser at all -- the non-swap paths
            # deliberately keep their old immediate-check behavior (an unready dispenser is
            # simply collected at the start of the next trip). A verified timeout here means
            # ore went in and bars provably never came out, and the usual cause is a drained
            # coffer (the dwarves stop operating the furnace), so say so.
            if not wait_for_bars_ready(BAR_VARBITS.get((config.get("barType") or "").lower()),
                                       events=getattr(ctx, "events", None)):
                ctx.state.trigger_safety_stop(
                    "gold bars never appeared in the dispenser after the conveyor deposit -- is the coffer empty?"
                )
                return False
        if not collect_bars(ctx, config, glove_swap=glove_swap, swap_back=False, prehover_bank=True):
            return False

    return True


def _swap_back_on_the_run(ctx, bank_obj) -> None:
    """USER 2026-09-04: "instead of equipping gold gauntlets before running to bank we can
    click the bank to start running and while running equip them then reclick bank". The
    bank click starts the run; the wear lands mid-run (an inventory click drops the queued
    open-bank interaction but not the walk itself); banker.open_bank's own first click, which
    fires while still running in, re-queues the open on arrival. Saves the ~1.2s the swap
    used to block for before the run started (bf_live8). Soft on a failed wear here -- the
    pre-deposit check in run_trip is the hard gate."""
    worn = ((controller.get_equipment().get("gloves") or {}).get("name") or "").lower()
    if worn == GOLDSMITH_GAUNTLETS.lower():
        return
    refresh = None
    if bank_obj.get("x") is not None and bank_obj.get("id") is not None:
        refresh = controller.object_refresh_target(
            bank_obj["x"], bank_obj["y"], bank_obj.get("plane", 0), bank_obj["id"])
    controller.click_entity(ctx.ser, bank_obj, refresh_target=refresh)
    if not ensure_gloves_worn(ctx, GOLDSMITH_GAUNTLETS):
        debug_log.log(
            "blast furnace",
            f"couldn't swap back to {GOLDSMITH_GAUNTLETS} on the run to the bank -- will retry before the next deposit",
        )


def _prehover_bank(ctx) -> bool:
    """USER 2026-09-04: "while using space and getting the bars we can pre aim the bank
    chest" -- the bars land ~0.7s after the Space (recording +1.1s from the click), which is
    a cursor flight, so the run-to-the-bank click that follows is a click in place. A courtesy
    move: no chest in view, or a projection under a UI panel, and it silently does nothing."""
    bank = banker.find_nearby_bank({})
    if bank is None or bank.get("screenX") is None:
        return False
    refresh = None
    if bank.get("x") is not None and bank.get("id") is not None:
        refresh = controller.object_refresh_target(
            bank["x"], bank["y"], bank.get("plane", 0), bank["id"])
    return bool(controller.hover_entity(ctx.ser, bank, refresh_target=refresh))


def collect_bars(ctx, config: dict, glove_swap: bool = False, swap_back: bool = True,
                 prehover_bank: bool = False) -> bool:
    """Collect everything currently sitting in the bar dispenser, verified via the same varbit
    the official RuneLite Blast Furnace plugin itself reads to show a bar-ready icon
    (BAR_VARBITS, decompiled from BarsOres.java/BlastFurnaceOverlay.java in
    client-1.12.36.jar) -- ADDED 2026-08-22 (user directive: "we can actually tell if we have
    bars in the dispenser. look at default plugin blast furnace").

    Called from TWO places in run_trip: unconditionally near the top of every trip (before the
    conveyor deposit), and again at the trip's own scheduled `collect` point below. The first
    call is not optional polish -- CONFIRMED live (user report) the Blast Furnace itself
    refuses to accept new ore/coal onto the conveyor while the dispenser is full ("it cant put
    more ore before the dispenser is empty"), so a dispenser left stuck full by an earlier
    missed collection (e.g. a routine restart mid-cycle, which is exactly how this was found
    live -- a prior process was killed mid-collect-retry, leaving 27 Steel bars stranded)
    deadlocks every following trip's conveyor deposit with a "0/N -- ran out?" safety stop that
    has nothing to do with the conveyor itself and looks unrelated until you know to check the
    dispenser. Checking first, every trip, breaks that deadlock instead of only ever
    discovering it after a conveyor deposit has already failed. Cheap when there's nothing to
    collect: the varbit read up front returns immediately without ever touching the dispenser.

    Re-fetches the dispenser object fresh on every retry inside the loop, not once outside it
    -- CORRECTED 2026-08-22 (user report, live session): a single snapshot resolved before the
    loop went stale after the very first click's own walk-over shifted the player (and hence
    the camera-projected screen position of the stationary dispenser), and every retry kept
    re-clicking that same now-wrong point, 7 attempts in a row, never once landing on the real
    thing -- same staleness class deposit_via_conveyor was already corrected for.

    Returns False only on a hard failure that should safety-stop the trip (dispenser not found
    at all); True otherwise, including the "couldn't verify, tried once anyway" (unmapped bar
    type or unsupported /varbit endpoint) and "still not empty after retries" (soft-logged, not
    fatal) cases -- same soft-retry posture maybe_top_up_coffer uses elsewhere in this file,
    since an occasional missed collection shouldn't halt an otherwise-working session by itself
    (the next call to this function, at the start of the following trip, will just try again)."""
    bar_type = (config.get("barType") or "iron").lower()
    varbit_id = BAR_VARBITS.get(bar_type)
    # The /varbit bridge endpoint needs a RuneLite client rebuild+restart to exist at all
    # (PlayerProvider.java, Java-side change) -- against an old, not-yet-rebuilt client, every
    # controller.get_varbit call below would return None forever. Probe once up front: a None
    # here means "can't verify" (unsupported endpoint, or a genuinely failed request), not "0
    # bars ready" -- fall back to a single best-effort pass either way rather than looping
    # blind for the full timeout on every single call.
    if varbit_id is not None and controller.get_varbit(varbit_id) is None:
        varbit_id = None

    if varbit_id is not None and controller.get_varbit(varbit_id) == 0:
        return True  # nothing ready -- the common case for the pre-deposit check

    if find_object(config, "Bar dispenser") is None:
        ctx.state.trigger_safety_stop("bar dispenser not found nearby")
        return False

    # Ice gloves on BEFORE the dispenser is touched -- hot gold bars can't be taken without
    # them (the whole reason the pair is carried). Runs on both call sites: the scheduled
    # end-of-trip collect AND the pre-trip dispenser clearing (a restart with bars stranded).
    # `glove_swap` guards it so every other bar type / the plain always-worn-ice method is
    # untouched.
    ice_pair = ice_glove_pair() if glove_swap else ICE_GLOVES
    if glove_swap and not ensure_gloves_worn(ctx, ice_pair):
        ctx.state.trigger_safety_stop(
            f"couldn't wear {ice_pair} before collecting bars -- is the pair missing?"
        )
        return False

    debug_log.log("blast furnace", "collecting bars")

    deadline = _now() + 20.0
    attempted = False
    last_pos = None
    nudge_key = "LEFT"
    while _now() < deadline:
        if varbit_id is not None and attempted and controller.get_varbit(varbit_id) == 0:
            break  # confirmed collected -- dispenser reports nothing left ready

        dispenser = find_object(config, "Bar dispenser")
        if dispenser is None:
            ctx.state.trigger_safety_stop("bar dispenser went out of range mid-collect")
            return False

        # ADDED 2026-08-22 (user report, live session): on a mass world this room is busy with
        # other players/dwarves, and a screen point that's technically inside the dispenser's
        # own clickbox can still be occluded by someone standing between the camera and it --
        # CONFIRMED live 7+ retries in a row resolved to the EXACT same screen position (player
        # and camera both genuinely stationary, so a stale-cache theory doesn't fit -- the
        # bridge's per-tick cache had long since rolled over across that many seconds) and
        # still never registered. Nudging the camera when a retry's position matches the
        # previous one forces a different viewing angle instead of clicking the same
        # potentially-blocked pixel again -- same idiom rotate_camera_away_from_ui already uses
        # for the conceptually similar "can't cleanly reach this point" problem.
        pos = (dispenser.get("screenX"), dispenser.get("screenY"))
        if attempted and pos == last_pos:
            controller.nudge_camera_yaw(ctx.ser, nudge_key)
            nudge_key = "RIGHT" if nudge_key == "LEFT" else "LEFT"
            dispenser = find_object(config, "Bar dispenser")
            if dispenser is None:
                ctx.state.trigger_safety_stop("bar dispenser went out of range mid-collect")
                return False
            pos = (dispenser.get("screenX"), dispenser.get("screenY"))
        last_pos = pos

        attempted = True
        events = getattr(ctx, "events", None)
        mark = _mark(ctx)
        controller.click_entity(ctx.ser, dispenser)
        # MEASURED in the 2026-08-31 recording: Take -> +0.5s varbit 10670 (chatmodal_unclamp,
        # the game's own "a chat modal is open" flag) = 1 -> the human's Space at +0.9s ->
        # +1.1s varbit 947 = 0, "You take 27 gold bars", +27 Gold bar. That modal is a widget
        # group NEITHER /state.dialogOpen NOR the m8aq dialogue reader covers (LIVE bf_live6,
        # 2026-09-04: both read closed while it was up, so the old dialogue wait never
        # confirmed and every collect paid ~4s in the settle/idle fallback). The modal varbit
        # is the confirm now; the bars landing without a modal is accepted too.
        walk_s = 0.7 * max(0, dispenser.get("distance") or 0)
        # chatmodal_unclamp STAYS 1 after a modal closes (MEASURED bf_live7 07:10:30: 1 for 5s+
        # with nothing on screen), so "== 1" alone is stale: the Space fired before the modal
        # existed and the first collect of run 7 took three passes. A modal is a TRANSITION --
        # the varbit flipping to 1 after the click (pushed event, or a read that differs from
        # the pre-click read) -- or a dialogue the readers do see.
        modal_before = controller.get_varbit(VB_CHAT_MODAL)
        modal_event = []

        def modal_flip(p):
            if p.get("varbitId") == VB_CHAT_MODAL and p.get("value") == 1:
                modal_event.append(True)
                return True
            return p.get("varbitId") == varbit_id

        def take_outcome():
            if _take_prompt_open() or _dialog_open() or modal_event or (
                    modal_before != 1 and controller.get_varbit(VB_CHAT_MODAL) == 1):
                return "modal"
            if varbit_id is not None and controller.get_varbit(varbit_id) == 0:
                return "landed"
            return None

        res = _await(events, {"VARBIT_CHANGED": modal_flip,
                              "WIDGET_LOADED": lambda p: p.get("groupId") in (TAKE_PROMPT_GROUP,) + DIALOG_WIDGET_GROUPS,
                              "INVENTORY_CHANGED": None, "PLAYER_STOPPED": None},
                     DISPENSER_DIALOG_BUDGET_S + walk_s, confirm=take_outcome, since=mark)
        landed = bool(res) and res.confirmed == "landed"
        if res and res.confirmed == "modal":
            mark = _mark(ctx)
            controller.key_tap(ctx.ser, "SPACE")
            if prehover_bank:
                _prehover_bank(ctx)    # the bars are landing meanwhile; see _prehover_bank
            if varbit_id is None:
                controller.human_delay(0.5, 0.8)
            else:
                landed = bool(_await(events, {"INVENTORY_CHANGED": None,
                                              "VARBIT_CHANGED": lambda p: p.get("varbitId") == varbit_id and not p.get("value")},
                                     COLLECT_CONFIRM_S,
                                     confirm=lambda: controller.get_varbit(varbit_id) == 0,
                                     since=mark))
        if varbit_id is None:
            break  # unmapped bar type -- can't verify, one pass is all we can do
        if not landed:
            # Nothing arrived in the budget: the click probably did not take (the walk-over
            # from range, a contested pixel). The loop re-resolves and re-clicks; give the
            # player a moment to finish any walk first so the retry does not redirect it.
            controller.wait_until_player_idle(timeout_s=4.0)

    if varbit_id is not None:
        still_ready = controller.get_varbit(varbit_id)
        if still_ready not in (0, None):
            debug_log.log(
                "blast furnace",
                f"bar dispenser still shows {still_ready} {bar_type} bar(s) ready after retries -- may have missed a collection",
            )

    if glove_swap and swap_back:
        # Swap back on the way to the bank (USER's own phrasing) -- the next batch's XP is
        # granted while these are worn, so they go on right after the take, and run_trip's
        # pre-deposit check re-verifies before the ore hits the belt. Soft on failure here
        # (that check is the hard gate): a missed swap-back alone shouldn't end the session.
        # run_trip passes swap_back=False and does this ON THE RUN to the bank instead
        # (_swap_back_on_the_run); this path stays for any other caller.
        if not ensure_gloves_worn(ctx, GOLDSMITH_GAUNTLETS):
            debug_log.log(
                "blast furnace",
                f"couldn't swap back to {GOLDSMITH_GAUNTLETS} after collecting -- will retry before the next deposit",
            )

    return True


def deposit_via_conveyor(ctx, config: dict, names: list, timeout_s: float = 30.0) -> int:
    """Left-click the conveyor belt ONCE -- CORRECTED 2026-08-22 (user report, live session):
    this used to click each matching inventory item then the conveyor, once per item (see
    module header). CONFIRMED live that's unnecessary: the conveyor's own left-click default
    action ("Put-ore-on") automatically consumes every matching ore/coal already in inventory
    on its own, across however many of `names` are present -- no need to select an item first,
    and no need to re-click the conveyor per item or per ore type. This is why run_trip passes
    the primary ore AND any loose coal together in one `names` list: they get deposited in the
    same single click.

    Takes `config` and calls find_conveyor(config) itself, immediately before clicking, rather
    than taking an already-resolved object dict -- CORRECTED 2026-08-22 (user report, live
    session): a dict resolved once at the top of run_trip and reused here went stale by the
    time this actually clicked (after a full bank trip and/or coal-bag emptying in between),
    landing off the belt because the camera had moved since that snapshot was taken. Re-fetching
    fresh right before the click is the same "look it up again, don't trust a stale reference"
    reasoning the old deposit_items docstring already applied to inventory items, just extended
    to the conveyor itself now that it's looked up once per run_trip instead of once per item.

    Since it's now one queued action rather than a click-per-item loop, success is measured by
    polling inventory (via find_item) until none of `names` remain or `timeout_s` elapses,
    then returning the before/after count difference -- same shortfall semantics run_trip's
    callers already expect (a return value less than what was expected means it ran out or got
    interrupted), just measured by counting instead of by counting successful click pairs.

    Re-clicks (re-fetching a fresh conveyor position) if inventory shows NO progress for
    `stall_retry_s` -- CORRECTED 2026-08-22 (user report, live session): the original version
    clicked once, then blindly polled for the full timeout_s. CONFIRMED live the initial click
    can silently fail to register the Put-ore-on action at all (e.g. fired too soon after the
    bank closed) with no error from click_entity itself -- inventory just never changes, and the
    old code burned the entire 30s timeout finding that out before safety-stopping. Retrying
    after a shorter stall window catches this the same tick instead of losing a full 30s to it
    every time it happens.

    CORRECTED again same day (user report, live session): "no inventory progress yet" isn't
    the same as "the click failed" -- a successful click that's still walking the player toward
    the conveyor (out of immediate range) also shows no inventory change until the walk
    finishes, and reclicking during that walk risks a misclick that sends the player somewhere
    else entirely, interrupting the very deposit that was already working ("didnt wait for ore
    to get processed so went to bank instead and now stuck" -- the disrupted deposit likely
    never actually reached 0, but the interruption confused what should have been a hard
    shortfall stop). Now also tracks the player's own local position each poll: movement counts
    as progress too (resets the stall timer, same as an inventory-count drop), so a reclick only
    fires when BOTH inventory and position have been static for the full stall window -- a
    much stronger signal the original click genuinely never registered at all."""
    names_lower = {n.lower() for n in names}

    def matching_count() -> int:
        return sum(1 for i in controller.get_inventory() if (i.get("name") or "").lower() in names_lower)

    before = matching_count()
    if before == 0:
        return 0

    events = getattr(ctx, "events", None)
    # A refused click (aim point behind a UI panel -- click_entity rotates the camera and
    # returns False, telling the caller to re-resolve) used to return 0 here and the trip
    # safety-stopped 1.8s after the bank closed with "0/27 -- ran out?" (LIVE bf_live5,
    # 2026-09-04: the camera had just turned LEFT for the bank chest). Re-resolve and retry.
    mark = None
    for attempt in range(CONVEYOR_CLICK_TRIES):
        target = find_conveyor(config)
        if target is None:
            debug_log.log("blast furnace", "conveyor belt not found for deposit -- may have moved out of range")
            return 0
        mark = _mark(ctx)
        if controller.click_entity(ctx.ser, target):
            break
        debug_log.log("blast furnace", f"conveyor click refused (attempt {attempt + 1}) -- re-resolving")
        controller.human_delay(0.3, 0.5)
    else:
        return 0

    # Event-driven since 2026-09-04: INVENTORY_CHANGED (ore leaving) and PLAYER_MOVING/STOPPED
    # (the walk to the belt) decide when to look; the inventory count and the player's local
    # position are the truth. Same rule as before: a reclick only when BOTH have been static
    # for CONVEYOR_STALL_S, since a click that is still walking the player shows no delta yet.
    state = {"count": before, "local": None}

    def progress():
        count = matching_count()
        player = controller.get_player() or {}
        local = (player.get("localX"), player.get("localY"))
        moved = state["local"] is not None and local != state["local"]
        state["local"] = local
        if count == 0:
            return "done"
        if count < state["count"] or moved:
            state["count"] = count
            return "progress"
        return None

    deadline = _now() + timeout_s
    while _now() < deadline:
        res = _await(events, {"INVENTORY_CHANGED": None, "PLAYER_MOVING": None,
                              "PLAYER_STOPPED": None},
                     min(CONVEYOR_STALL_S, deadline - _now()), confirm=progress,
                     reaction=False, since=mark)
        mark = None
        if res and res.confirmed == "done":
            break
        if res:
            continue    # ore is leaving or the player is still walking -- not a stall
        debug_log.log("blast furnace", "no progress depositing onto the conveyor -- reclicking")
        retry_target = find_conveyor(config)
        if retry_target is not None:
            mark = _mark(ctx)
            controller.click_entity(ctx.ser, retry_target)

    return before - matching_count()


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
    # Whether ANY trip in this bar type's pattern needs coal -- see run_trip's own docstring for
    # why this (computed once, across the whole pattern) is what decides whether the coal bag's
    # slot is kept across every trip, not the current trip's own need_bag_fill.
    bar_uses_coal_bag = any(
        any(name.lower() == "coal" for name, _ in t["deposit"]) for t in trips
    )

    if not run_trip(ctx, config, trip, bar_uses_coal_bag):
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


def find_conveyor(config: dict):
    """The Blast Furnace's conveyor belt, NOT via plain find_object -- CONFIRMED live (bridge
    /objects query while standing at the belt, 2026-08-22): "Conveyor belt" resolves to THREE
    separate entries, not one -- two tiles belonging to the same multi-tile GameObject (id
    9101, registered at both (1943,4965) and (1943,4966), same as any multi-tile object is
    registered at every tile it occupies) plus a third, differently-id'd GameObject (9100) one
    tile further north again at (1943,4967). find_object's plain nearest-distance pick lands on
    the SOUTHERNMOST of these -- CONFIRMED (user report, live session) that click consistently
    lands one tile off (south) of the belt's actual clickable spot; the correct spot is the
    next tile north of the nearest one, same object id, not the nearest tile itself and not the
    third (differently-id'd, two tiles further) candidate either.

    Picks the nearest candidate, then prefers another candidate exactly one tile north (same x,
    y+1) if one exists among the matches, falling back to the plain nearest pick otherwise (so
    this degrades to find_object's old behavior if the belt's on-screen tile layout is ever
    read differently, e.g. approached from an unexpected side)."""
    candidates = [
        o for o in controller.get_objects()
        if "conveyor belt" in (o.get("name") or "").lower()
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= config.get("maxObjectDistance", 15)
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    # The bridge reports each object's actions, and only ONE of the belt's entries offers
    # Put-ore-on (id 9100; the multi-tile 9101 entries report no actions at all). LIVE
    # 2026-09-04 (bf_live3): the north-of-nearest heuristic below picked a 9101 tile from a
    # different standing spot and six clicks resolved to "Walk here" -- 0/27 deposited, safety
    # stop. Prefer the tile that offers the action; the heuristic stays for an older client
    # build that reports no actions.
    with_action = [o for o in candidates
                   if any("put-ore-on" in (a or "").lower() for a in (o.get("actions") or []))]
    if with_action:
        return with_action[0]
    nearest = candidates[0]
    north = next(
        (
            o for o in candidates
            if o.get("x") == nearest.get("x") and o.get("y") == nearest.get("y", 0) + 1
        ),
        None,
    )
    return north if north is not None else nearest
