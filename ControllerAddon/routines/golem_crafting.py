# routines/golem_crafting.py
#
# Golem Crafting routine: mine sunstone -> chisel it into cores -> carve an empty plinth and
# insert a core + hunter fur to finish a golem (Crafting XP + an uncut gem) -> bank, with an
# automatic fallback to hunting goats at the Goat Pit when the bank has no usable fur left.
#
# Full design and research notes: docs/superpowers/specs/2026-08-07-golem-crafting-design.md.
# Golem Crafting is a member-only Crafting method on Wyrmscraig, released 2026-07-29 -- 9 days
# before that design doc. The bundled OSRS Wiki mirror (osrs-wiki-data-master/) still tags
# Wyrmscraig.txt and Fallen From Grace.txt {{Future Content}} with no mechanical detail, so
# EVERY object name, menu-option string, and click sequence below comes from a live wiki fetch,
# not the local mirror, and has NOT been cross-checked against the running game the way this
# codebase's other routines verify object/menu names before relying on them. Each such
# assumption below carries its own "ASSUMPTION, NOT YET VERIFIED LIVE" comment, same convention
# as blast_furnace.py's -- correct these during the staged live verification this design's
# Testing section lays out before trusting this routine unattended.
#
# Requirements assumed already met (out of scope for this routine, see the design's Scope):
# Crafting 60, "Fallen From Grace" quest complete, already own a fur pouch (any size).
#
# Deliberately NOT implemented: the XP-drop tick-timing speed-up trick for plinth carving
# (always uses the safe, un-optimized ~15-ticks-per-side cadence); acquiring a fur pouch;
# sourcing fur from anywhere but the bank or the Goat Pit.
#
# Run with:
#     python controller.py routines/golem_crafting.py

import random
import time

import banker
import controller
import debug_log

# Highest fur-quality first (routines/golem_crafting.py-design.md's "Fur table"). A pure module
# constant, not read from bridge config -- ConfigExporter.exportGolemCrafting deliberately
# doesn't export this list (the player never chooses a fur, same reasoning
# BlastFurnaceBarType's BAR_TYPES table isn't Java-exposed either, see the design's section 3).
# Keeping this a constant that every phase reads directly (rather than config.get("furPriority"))
# means behavior is identical whether get_config() below returns the live bridge config (which
# has no "furPriority" key at all) or DEFAULT_CONFIG.
FUR_PRIORITY = [
    "Moonlight antelope fur", "Sunlight antelope fur", "Dashing kebbit fur",
    "Kyatt fur", "Dark kebbit fur", "Fox fur", "Spotted kebbit fur",
    "Graahk fur", "Larupia fur", "Feldip weasel fur", "Common kebbit fur",
    "Desert devil fur", "Polar kebbit fur", "Wyrmscraig goat fur",
]

# Same reasoning as FUR_PRIORITY above -- ConfigExporter.exportGolemCrafting doesn't export
# maxObjectDistance either, so every phase's find_object() call uses this constant directly
# rather than config.get("maxObjectDistance", ...), which would silently fall back to this same
# value anyway whenever the live bridge config is in effect.
MAX_OBJECT_DISTANCE = 15

CRAFTING_LEVEL_REQUIRED = 60

# ASSUMPTION, NOT YET VERIFIED LIVE -- exact object names for the 22 rock spots + 1 monolith
# (design section "Key facts", "Core loop").
ROCK_NAMES = ("sunstone rocks", "sunstone monolith")

# ASSUMPTION, NOT YET VERIFIED LIVE -- exact inventory item names for the raw ore, the finished
# core, and the tool used to turn one into the other.
RAW_SUNSTONE_NAME = "Sunstone"
SUNSTONE_CORE_NAME = "Sunstone core"
CHISEL_NAME = "Chisel"

# ASSUMPTION, NOT YET VERIFIED LIVE -- exact object name for the crafting station. Assumed to
# stay findable by this same substring both before and after carving starts (see craft_phase's
# docstring in a later task for how this assumption is used).
PLINTH_NAME = "plinth"

# ASSUMPTION, NOT YET VERIFIED LIVE -- exact item/object names for the Goat Pit hunting loop.
WOODEN_SPIKES_NAME = "Wooden spikes"
GOAT_PIT_NAME = "Goat pit"
GOAT_NAME = "goat"

# Used only if a Controller plugin build predating this routine's wiring is running (so
# GET /golem-crafting-config 404s and get_golem_crafting_config() returns None) -- keeps this
# routine runnable standalone, same as every other routine's DEFAULT_CONFIG fallback.
DEFAULT_CONFIG = {
    "enabled": True,
    # Kept here for documentation/standalone completeness -- see the FUR_PRIORITY/
    # MAX_OBJECT_DISTANCE constants above for why the actual phase code doesn't read these two
    # keys out of config.
    "furPriority": FUR_PRIORITY,
    "maxObjectDistance": MAX_OBJECT_DISTANCE,

    "bank": {"teleportFallback": False},

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
    config = controller.get_golem_crafting_config()
    return config if config is not None else DEFAULT_CONFIG


def check_level_requirement(ctx, config: dict) -> None:
    """One-time-per-activation under-level sanity check, run from inside tick() (never setup()
    -- controller.py calls routine.setup(ctx) at routine-load time, BEFORE the
    login.is_fully_logged_in() gate, so /skills could be queried pre-login and false-trigger a
    permanent safety stop on an account that's actually fine once logged in). Gated by a
    ctx.state.data flag so it runs exactly once per routine-activation -- state.data is reset to
    {} by controller.py whenever the active routine file changes. Same shape as
    blast_furnace.py's/firemaking.py's own check_level_requirement."""
    crafting = controller.get_skills().get("Crafting") or {}
    level = crafting.get("level")
    if level is not None and level < CRAFTING_LEVEL_REQUIRED:
        ctx.state.trigger_safety_stop(
            f"Crafting level {level} is below the {CRAFTING_LEVEL_REQUIRED} required for Golem Crafting"
        )


def find_item(name: str):
    """First inventory item whose name matches `name` exactly (case-insensitive) -- exact
    rather than substring match, same reasoning as blast_furnace.py's find_item: a substring
    match here would wrongly match "Sunstone" against "Sunstone core" (and vice versa isn't a
    risk since RAW_SUNSTONE_NAME is a prefix of SUNSTONE_CORE_NAME, but an exact match makes
    both lookups unambiguous either way)."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if (i.get("name") or "").lower() == name_lower),
        None,
    )


def find_object(name: str):
    """Closest visible object whose name contains `name`, within MAX_OBJECT_DISTANCE -- same
    filter shape as blast_furnace.py's find_object/mining.py's pick_rock/banker.py's
    find_nearby_bank, kept local per this codebase's established convention."""
    name_lower = name.lower()
    candidates = [
        o for o in controller.get_objects()
        if name_lower in (o.get("name") or "").lower()
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def find_rocks(objects: list) -> list:
    """Every visible sunstone rock/monolith within MAX_OBJECT_DISTANCE, closest first --
    plural (unlike find_object above) because mine_phase's momentum re-targeting (see below)
    needs the whole candidate list, not just the nearest one."""
    candidates = [
        o for o in objects
        if (o.get("name") or "").lower() in ROCK_NAMES
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates


def mine_phase(ctx, config: dict) -> None:
    """Momentum-click sunstone rocks/the monolith until the inventory is full, then hand off
    to chisel_phase. One swing (or one re-target) per call, gated on isIdle -- same shape as
    mining.py's tick(), not a single blocking loop, so the engine's between-tick safety/break/
    PvP checks still run at their normal cadence throughout a long mining stretch.

    Momentum mechanic (design section "Key facts", "Core loop"): switching rocks between
    swings raises the success chance and gives a 20% chance of bonus sunstone, so this
    deliberately targets a DIFFERENT rock each successful click instead of hammering the same
    one like mining.py does -- tracked via ctx.state.data["gc_last_rock_pos"] (an (x, y) world
    tile, not an object id: every "Sunstone rocks" instance shares the same object id, so id
    alone can't distinguish which specific rock was clicked last)."""
    if not controller.get_combat().get("isIdle", True):
        return  # still swinging

    if len(controller.get_inventory()) >= controller.INVENTORY_SIZE:
        debug_log.log("golem crafting", "inventory full -- moving to chisel phase")
        ctx.state.data["gc_phase"] = "chisel"
        return

    rocks = find_rocks(controller.get_objects())
    if not rocks:
        ctx.state.trigger_safety_stop("no sunstone rocks or monolith found nearby -- wrong location?")
        return

    last_pos = ctx.state.data.get("gc_last_rock_pos")
    target = next((r for r in rocks if (r.get("x"), r.get("y")) != last_pos), rocks[0])

    debug_log.log("golem crafting", f"mining {target.get('name')} (dist={target.get('distance')})")
    time.sleep(random.uniform(0.15, 0.5))
    if controller.click_entity(ctx.ser, target):
        ctx.state.data["gc_last_rock_pos"] = (target.get("x"), target.get("y"))


def chisel_phase(ctx, config: dict) -> None:
    """For each raw sunstone in inventory, use the chisel on it to make a sunstone core --
    item-then-item two-click idiom, same as firemaking.py's tinderbox+log. One chisel action
    per call, gated on isIdle, so a low-Crafting-level failed attempt (if any -- design section
    "Key facts" doesn't document a failure chance for this step, unlike Firemaking's, but the
    isIdle gate costs nothing extra either way) just retries next call the same way
    firemaking.py's light loop does. -> craft once no raw sunstone is left."""
    if not controller.get_combat().get("isIdle", True):
        return

    controller.open_inventory_tab(ctx.ser)

    raw = find_item(RAW_SUNSTONE_NAME)
    if raw is None:
        debug_log.log("golem crafting", "no raw sunstone left -- moving to craft phase")
        ctx.state.data["gc_phase"] = "craft"
        return

    chisel = find_item(CHISEL_NAME)
    if chisel is None:
        ctx.state.trigger_safety_stop("no chisel in inventory")
        return

    debug_log.log("golem crafting", "chiselling sunstone into a core")
    time.sleep(random.uniform(0.15, 0.35))
    controller.click_entity(ctx.ser, chisel, check_ui_blockers=False)
    time.sleep(random.uniform(0.1, 0.25))
    controller.click_entity(ctx.ser, raw, check_ui_blockers=False)
    # Let the action register server-side before the next isIdle poll -- neither inventory
    # click makes isMoving true, so without this the very next poll can still read isIdle ==
    # True and fire a duplicate click pair (same reasoning as firemaking.py's own light loop).
    time.sleep(random.uniform(0.6, 1.0))


def pick_best_fur_in_inventory() -> str:
    """Highest-priority (per FUR_PRIORITY) fur type actually present in inventory right now,
    or None. Exact-name match against each inventory item, same reasoning as find_item."""
    inv_names = {(i.get("name") or "").lower() for i in controller.get_inventory()}
    return next((f for f in FUR_PRIORITY if f.lower() in inv_names), None)


# ASSUMPTION, NOT YET VERIFIED LIVE -- ~15 game ticks (0.6s each) per plinth side (design
# section "Key facts": "~15 ticks/side, ~60 ticks total"). Randomized within a band around
# that, same jitter style every other routine's animation-wait sleeps use.
PLINTH_SIDE_WAIT_SECONDS = (8.5, 9.5)


def craft_phase(ctx, config: dict) -> None:
    """Craft one golem per call (empty the fur pouch first if this is the first call since
    entering this phase, carve all 4 sides of an empty plinth, then insert one sunstone core +
    the best available fur) -- sized like blast_furnace.py's run_trip (one bounded unit of work
    per call, not a single call looping over every golem this batch), so the engine's between-
    tick safety/break/PvP checks still run between golems on a long crafting stretch. tick()
    keeps re-entering this phase (see Task 8) until it sets gc_phase to "bank" itself, once out
    of cores or fur -- that repetition IS the "repeat until out of cores or fur" the design
    describes, just spread across multiple tick() calls rather than one internal loop.

    ASSUMPTION, NOT YET VERIFIED LIVE -- PLINTH_NAME ("plinth") is assumed to still substring-
    match find_object's lookup both before carving starts and after the golem is finished
    (i.e. the object's display name doesn't change to something without "plinth" in it, e.g.
    switching to "Golem"/"Finished golem" entirely). If live testing shows otherwise, add the
    post-carve name as a second candidate substring here.
    """
    if not ctx.state.data.get("gc_pouch_emptied_this_craft"):
        if banker.has_fur_pouch():
            banker.empty_fur_pouch(ctx)
        ctx.state.data["gc_pouch_emptied_this_craft"] = True

    core = find_item(SUNSTONE_CORE_NAME)
    if core is None:
        debug_log.log("golem crafting", "out of sunstone cores -- moving to bank phase")
        ctx.state.data["gc_phase"] = "bank"
        ctx.state.data["gc_pouch_emptied_this_craft"] = False
        return

    fur_name = pick_best_fur_in_inventory()
    if fur_name is None:
        debug_log.log("golem crafting", "out of usable fur -- moving to bank phase")
        ctx.state.data["gc_phase"] = "bank"
        ctx.state.data["gc_pouch_emptied_this_craft"] = False
        return

    plinth = find_object(PLINTH_NAME)
    if plinth is None:
        ctx.state.trigger_safety_stop("no empty plinth found nearby")
        return

    debug_log.log("golem crafting", f"carving plinth (dist={plinth.get('distance')})")
    # A real while-loop with its own success counter, NOT `for side in range(4)` with a
    # `continue` on a failed click -- CONFIRMED during task review (2026-08-07): `continue`
    # inside a `for` loop advances to the next value from range(4) regardless of whether the
    # click actually landed, so a single missed/blocked click would silently consume one of the
    # 4 iterations without a real carve, and the loop would still exit claiming 4 sides done.
    # Bounded (not an unconditional retry-forever) the same way blast_furnace.py's deposit_items
    # counts real successes against a requested count rather than assuming success.
    sides_carved = 0
    attempts = 0
    max_attempts = 8  # up to 4 retries total across all sides, on top of the 4 real carves
    while sides_carved < 4 and attempts < max_attempts:
        attempts += 1
        controller.open_inventory_tab(ctx.ser)  # no-op if already open; harmless before a world-object click
        if not controller.click_entity(ctx.ser, plinth):
            time.sleep(random.uniform(0.3, 0.6))
            continue  # missed/blocked click -- retry the same side (sides_carved unchanged)
        sides_carved += 1
        debug_log.log("golem crafting", f"carved side {sides_carved}/4")
        time.sleep(random.uniform(*PLINTH_SIDE_WAIT_SECONDS))

    if sides_carved < 4:
        debug_log.log("golem crafting", f"only carved {sides_carved}/4 sides -- plinth may have gone out of range; retrying next tick")
        return

    controller.open_inventory_tab(ctx.ser)
    core = find_item(SUNSTONE_CORE_NAME)
    fur = find_item(fur_name)
    if core is None or fur is None:
        # Carving consumed more time than expected and the fur pouch/inventory state moved out
        # from under us (e.g. a catch routed into the pouch mid-carve) -- bail this golem
        # rather than clicking with a stale/missing item; next call re-evaluates from scratch.
        debug_log.log("golem crafting", "core or fur went missing mid-carve -- retrying next tick")
        return

    debug_log.log("golem crafting", f"inserting core + {fur_name}")
    controller.click_entity(ctx.ser, core, check_ui_blockers=False)
    time.sleep(random.uniform(0.15, 0.3))
    controller.click_entity(ctx.ser, plinth)
    time.sleep(random.uniform(0.4, 0.7))

    fur = find_item(fur_name)
    if fur is None:
        debug_log.log("golem crafting", "fur went missing after inserting the core -- retrying next tick")
        return
    controller.click_entity(ctx.ser, fur, check_ui_blockers=False)
    time.sleep(random.uniform(0.15, 0.3))
    controller.click_entity(ctx.ser, plinth)
    time.sleep(random.uniform(0.6, 1.0))


# Never deposited during a golem-crafting bank trip -- the pouch/tools are needed for the next
# mine/chisel/craft cycle, and a leftover sunstone core (if fur ran out before cores did) needs
# to survive the trip so the next craft_phase call can still use it.
BANK_KEEP_ITEMS = [banker.FUR_POUCH_NAME, CHISEL_NAME.lower(), WOODEN_SPIKES_NAME.lower(), SUNSTONE_CORE_NAME.lower()]

# Withdraw budget when no fur pouch is carried -- "a fixed inventory-slot budget (e.g. 14, half
# an inventory)" per the design's bank-phase bullet. With a pouch, the budget is effectively the
# pouch's own capacity (small/medium/large all <= 28), so 28 (a full inventory) as the withdraw
# cap is safe either way -- withdraw_loadout only ever takes min(cap, what's actually in the
# bank) since it withdraws unit-by-unit off the bank's real stack.
FUR_WITHDRAW_BUDGET_WITH_POUCH = controller.INVENTORY_SIZE
FUR_WITHDRAW_BUDGET_NO_POUCH = 14


def find_available_fur_in_bank(bank: dict) -> str:
    """Highest-priority (per FUR_PRIORITY) fur type with qty > 0 in the open bank, or None."""
    bank_items = {(i.get("name") or "").lower(): i.get("qty", 0) for i in bank.get("items", [])}
    return next((f for f in FUR_PRIORITY if bank_items.get(f.lower(), 0) > 0), None)


def _run_bank_trip(ctx, config: dict) -> None:
    """The actual deposit/withdraw logic, run once a bank is confirmed open (by either branch
    of bank_phase below). Deposits everything except BANK_KEEP_ITEMS (uncut gems and any
    leftover raw sunstone per the design's bank-phase bullet -- anything not explicitly kept),
    then withdraws the best available fur (filling the pouch if one is carried) or falls
    through to the hunt phase if the bank has none of FUR_PRIORITY at all."""
    banker.deposit_inventory(ctx, {"keepItems": BANK_KEEP_ITEMS})

    bank = controller.get_bank()
    fur_name = find_available_fur_in_bank(bank)
    if fur_name is None:
        debug_log.log("golem crafting", "bank has none of the usable fur types -- moving to hunt phase")
        ctx.state.data["gc_phase"] = "hunt"
        return

    has_pouch = banker.has_fur_pouch()
    budget = FUR_WITHDRAW_BUDGET_WITH_POUCH if has_pouch else FUR_WITHDRAW_BUDGET_NO_POUCH
    slot = next((s for s in bank.get("items", []) if (s.get("name") or "").lower() == fur_name.lower()), None)
    qty = min(budget, slot.get("qty", 0)) if slot else 0

    if qty > 0:
        debug_log.log("golem crafting", f"withdrawing {qty}x {fur_name}")
        banker.withdraw_loadout(ctx, {"withdraw": [{"name": fur_name, "quantity": qty}]})

    if has_pouch:
        time.sleep(random.uniform(0.3, 0.5))  # let the just-withdrawn fur register in
                                               # get_inventory() before fill_fur_pouch looks for it
        banker.fill_fur_pouch(ctx)

    debug_log.log("golem crafting", "banking done -- moving to mine phase")
    ctx.state.data["gc_phase"] = "mine"


def bank_phase(ctx, config: dict) -> None:
    """Bank near Wyrmscraig if a bank is in walking distance; otherwise teleport to Ferox
    Enclave via the ring of dueling if Teleport If No Bank Nearby is enabled. Sized like
    blast_furnace.py's run_trip / banker.bank_up (one full bounded bank visit per call).

    Fur selection needs to inspect the ALREADY-OPEN bank's actual contents (find_available_
    fur_in_bank), which banker.bank_up's single deposit+withdraw+close call can't do (it needs
    the withdraw list decided up front). So the local-bank branch below opens the bank itself
    and calls _run_bank_trip directly, same manual pattern blast_furnace.py's run_trip uses
    instead of bank_up.

    CORRECTED 2026-08-07 (caught in task review): the Ferox teleport-fallback branch cannot
    do that same dynamic fur scan -- it has to go through banker.bank_up (its private Ferox
    path isn't meant to be called directly), so it can only ever deposit everything and
    withdraw nothing dynamic, same acknowledged limitation blast_furnace.py's own module
    header documents for the coal bag ("the Ferox teleport fallback cannot fill the coal
    bag"). An earlier version of this docstring claimed the very next bank_phase call would
    then find Ferox's own bank chest nearby and take the local-bank branch instead -- that's
    wrong: banker._bank_via_ferox walks the player straight back to wherever they started
    (via travel.walk_to, using the location saved before teleporting) once it's done, not
    left standing at Ferox, so the next call is back at square one, not next to a bank. Per
    the design doc, Wyrmscraig has its own bank chest roughly central to the crafting/mining/
    hunting areas, so the local-bank branch above is expected to be the normal path in
    practice -- landing in the Ferox branch below at all means that bank genuinely wasn't
    found nearby. Rather than loop forever depositing-and-walking-back without ever
    restocking fur, this now stops with a clear message once the Ferox round-trip completes.
    """
    bank_obj = banker.find_nearby_bank({})
    if bank_obj is not None:
        if not banker.open_bank(ctx, bank_obj):
            ctx.state.trigger_safety_stop("failed to open the bank near Wyrmscraig")
            return
        _run_bank_trip(ctx, config)
        banker.close_bank(ctx)
        time.sleep(random.uniform(0.3, 0.6))
        return

    if config.get("bank", {}).get("teleportFallback"):
        if not banker.bank_up(ctx, {"keepItems": BANK_KEEP_ITEMS, "teleportFallback": True}):
            ctx.state.trigger_safety_stop("couldn't reach a bank near Wyrmscraig, and the Ferox teleport fallback failed")
            return
        # The Ferox trip above can only deposit, never withdraw fur dynamically (see docstring),
        # and it walks the player straight back afterward rather than leaving them at a
        # findable bank -- so there is no useful next state to advance to from here. Stop
        # rather than silently repeating a deposit-only round trip forever.
        ctx.state.trigger_safety_stop(
            "no bank found near Wyrmscraig for golem crafting -- the Ferox teleport fallback can "
            "only deposit items, not withdraw fur, so it can't recover from this on its own. "
            "Check you're actually on Wyrmscraig and its bank chest is reachable."
        )
        return

    ctx.state.trigger_safety_stop("no bank found near Wyrmscraig, and Teleport If No Bank Nearby is off")


def _ensure_wooden_spikes(ctx, config: dict) -> bool:
    """Withdraw wooden spikes from the bank if not already carried. Returns False (with a
    safety stop already triggered) if neither inventory nor bank has any -- a genuine dead end,
    same "hard stop, don't spin forever" convention banker.py's out_of_critical_supplies path
    uses."""
    if find_item(WOODEN_SPIKES_NAME) is not None:
        return True

    bank_obj = banker.find_nearby_bank({})
    if bank_obj is None:
        ctx.state.trigger_safety_stop("out of wooden spikes and no bank in range to restock")
        return False

    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank while restocking wooden spikes")
        return False

    banker.withdraw_loadout(ctx, {"withdraw": [{"name": WOODEN_SPIKES_NAME, "quantity": 1}]})
    banker.close_bank(ctx)
    time.sleep(random.uniform(0.3, 0.6))

    if find_item(WOODEN_SPIKES_NAME) is None:
        ctx.state.trigger_safety_stop("no wooden spikes in inventory or bank -- can't line the Goat Pit")
        return False
    return True


def hunt_phase(ctx, config: dict) -> None:
    """Entered only when bank_phase finds no usable fur at all. Line the Goat Pit with wooden
    spikes, open the fur pouch (if owned) so catches route straight in, then lure/harvest
    goats until none are left nearby (the pit needing to be "fully cleared of goats before it
    can be re-lured" per the design's Key facts -- approximated here as "no goat NPCs visible
    near the pit", since the bridge has no dedicated pit-state field to check against). One
    action (line the pit, lure, or harvest) per call, same isIdle-adjacent single-small-action
    shape as mine_phase/chisel_phase.

    ASSUMPTION, NOT YET VERIFIED LIVE -- this is the single most uncertain part of this whole
    routine (see the design doc's Research method: the Goat Pit's exact interaction states
    weren't deeply covered by the live wiki fetch this design was based on). In particular:
      - Whether a plain left-click on a wild goat resolves to "Lure" by default, and whether
        the SAME plain click on an already-trapped goat resolves to "Harvest" -- this function
        can't currently tell "needs luring" and "ready to harvest" apart from the bridge's NPC
        data alone, so it clicks whatever goat NPC is nearest and lets the game's own menu
        resolution sort out which action that click actually performs. Add "Lure"/"Harvest" to
        Controller Settings -> Clicking -> Forced Actions if a plain click doesn't resolve
        correctly in one or both states.
      - Whether "Open" is really the fur pouch's default action while not at a bank (as opposed
        to Fill/Empty, which is what a bank-side plain click is assumed to resolve to
        elsewhere in this file) -- same kind of state-dependent-default-action uncertainty
        banker.py's fill_coal_bag docstring already flags for the coal bag.
    Correct this function during the Goat Pit stage of this plan's live verification task
    before trusting the hunt phase unattended.
    """
    if not ctx.state.data.get("gc_pit_lined"):
        if not _ensure_wooden_spikes(ctx, config):
            return

        pit = find_object(GOAT_PIT_NAME)
        if pit is None:
            ctx.state.trigger_safety_stop("Goat Pit not found nearby")
            return

        spikes = find_item(WOODEN_SPIKES_NAME)
        debug_log.log("golem crafting", "lining the Goat Pit with wooden spikes")
        # CORRECTED 2026-08-07 (caught in task review): only commit gc_pit_lined once both
        # clicks actually landed -- CONFIRMED live click_entity returns False on a blocked/
        # missed aim point, and the original version here set the flag unconditionally,
        # matching the same class of bug Task 5's carve loop had (state committed without
        # checking whether the click that was supposed to produce it actually succeeded).
        spikes_clicked = controller.click_entity(ctx.ser, spikes, check_ui_blockers=False)
        time.sleep(random.uniform(0.15, 0.3))
        pit_clicked = controller.click_entity(ctx.ser, pit)
        time.sleep(random.uniform(0.6, 1.0))
        if not (spikes_clicked and pit_clicked):
            debug_log.log("golem crafting", "lining click missed/blocked -- retrying next tick")
            return
        ctx.state.data["gc_pit_lined"] = True

        if banker.has_fur_pouch() and not ctx.state.data.get("gc_pouch_opened_this_hunt"):
            pouch = find_item(banker.FUR_POUCH_NAME.title()) or next(
                (i for i in controller.get_inventory() if banker.FUR_POUCH_NAME in (i.get("name") or "").lower()),
                None,
            )
            if pouch is not None:
                debug_log.log("golem crafting", "opening fur pouch so catches route straight in")
                controller.click_entity(ctx.ser, pouch, check_ui_blockers=False)
                time.sleep(random.uniform(0.3, 0.5))
            ctx.state.data["gc_pouch_opened_this_hunt"] = True
        return

    goats = [
        n for n in controller.get_npcs()
        if GOAT_NAME in (n.get("name") or "").lower()
        and n.get("visible", True)
        and 0 <= n.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not goats:
        debug_log.log("golem crafting", "Goat Pit cleared -- moving to bank phase")
        ctx.state.data["gc_phase"] = "bank"
        ctx.state.data["gc_pit_lined"] = False
        ctx.state.data["gc_pouch_opened_this_hunt"] = False
        return

    goats.sort(key=lambda n: n.get("distance", 9999))
    target = goats[0]
    debug_log.log("golem crafting", f"interacting with {target.get('name')} (dist={target.get('distance')})")
    time.sleep(random.uniform(0.2, 0.5))
    controller.click_entity(ctx.ser, target)
    time.sleep(random.uniform(0.6, 1.0))


PHASES = {
    "mine": mine_phase,
    "chisel": chisel_phase,
    "craft": craft_phase,
    "bank": bank_phase,
    "hunt": hunt_phase,
}


def tick(ctx):
    config = ctx.state.config

    if not ctx.state.data.get("gc_level_checked"):
        check_level_requirement(ctx, config)
        ctx.state.data["gc_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    phase = ctx.state.data.get("gc_phase", "mine")
    handler = PHASES.get(phase)
    if handler is None:
        ctx.state.trigger_safety_stop(f"unknown golem crafting phase '{phase}'")
        return

    handler(ctx, config)
