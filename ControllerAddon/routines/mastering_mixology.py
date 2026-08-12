"""routines/mastering_mixology.py

Automates Mastering Mixology under the max-total-points-per-paste strategy (see
docs/superpowers/specs/2026-08-12-mastering-mixology-design.md): bank herbs -> refine -> hopper
-> read the 3 active orders -> mix -> process -> deposit, skipping single-paste orders (AAA/MMM/
LLL) unless an MAL order is present in the same set of 3.
"""

import random
import time

import banker
import controller
import debug_log

MAX_OBJECT_DISTANCE = 15
HERBLORE_LEVEL_REQUIRED = 60

# name -> {mox, aga, lye} -- matches the varbit type-index order MixologyProvider reports (see
# this plan's Global Constraints), NOT alphabetical or wiki-page order.
RECIPE_TABLE = {
    "Mammoth-might mix": {"mox": 3, "aga": 0, "lye": 0},
    "Mystic mana amalgam": {"mox": 2, "aga": 1, "lye": 0},
    "Marley's moonlight": {"mox": 2, "aga": 0, "lye": 1},
    "Alco-augmentator": {"mox": 0, "aga": 3, "lye": 0},
    "Azure aura mix": {"mox": 1, "aga": 2, "lye": 0},
    "Aqualux amalgam": {"mox": 0, "aga": 2, "lye": 1},
    "Liplack liquor": {"mox": 0, "aga": 0, "lye": 3},
    "Megalite liquid": {"mox": 1, "aga": 0, "lye": 2},
    "Anti-leech lotion": {"mox": 0, "aga": 1, "lye": 2},
    "Mixalot": {"mox": 1, "aga": 1, "lye": 1},
}
SINGLE_PASTE_BASES = {"Alco-augmentator", "Mammoth-might mix", "Liplack liquor"}
MAL_BASE = "Mixalot"

# ItemID.MM_POTION_*_UNFINISHED / _FINISHED (net.runelite.api.gameval.ItemID,
# runelite-api-1.12.35.jar) -- processing transforms a base potion in place and the display
# NAME is identical before and after (confirmed: Mammoth-might mix has id1=30011 (Unfinished)
# and id2=30021 (Processed) sharing one `name` field in the wiki infobox), so name matching
# cannot distinguish unprocessed from processed. This ordering matches VESSEL_ID_TO_BASE's
# object-id order (NOT RECIPE_TABLE's varbit order) -- see this plan's Global Constraints.
UNFINISHED_IDS = {
    30011: "Mammoth-might mix", 30012: "Mystic mana amalgam", 30013: "Marley's moonlight",
    30014: "Alco-augmentator", 30015: "Aqualux amalgam",      30016: "Azure aura mix",
    30017: "Liplack liquor",   30018: "Anti-leech lotion",    30019: "Megalite liquid",
    30020: "Mixalot",
}
FINISHED_IDS = {k + 10: v for k, v in UNFINISHED_IDS.items()}

# ItemID.MM_LAB_SPECIAL_HERB -- Digweed. Used instead of a sticky ctx.state.data flag (see Fix 5
# in the final whole-branch review) because the plant withers out of inventory if the player
# leaves the minigame area, and find_item()'s exact-match semantics made a flag-based confirm
# unreliable if the in-game name isn't precisely "Digweed".
MM_LAB_SPECIAL_HERB_ID = 30031

DEFAULT_CONFIG = {
    "enabled": True,
    "herbPriority": {
        "mox": ["Guam leaf", "Marrentill", "Tarromin", "Harralander"],
        "aga": ["Huasca", "Irit leaf", "Cadantine", "Lantadyme", "Dwarf weed", "Torstol"],
        "lye": ["Ranarr weed", "Toadflax", "Avantoe", "Kwuarm", "Snapdragon"],
    },
    "hopperTarget": 1000,
    "hopperLowWater": 150,
    "digweed": {"enabled": True},
    "speedups": {"retort": True, "agitator": True, "alembic": True},
    "maxObjectDistance": MAX_OBJECT_DISTANCE,
    "bank": {"teleportFallback": False},
    "breaks": {
        "enabled": True, "everyMinMinutes": 45, "everyMaxMinutes": 75,
        "durationMinMinutes": 3, "durationMaxMinutes": 12, "microBreaks": True,
    },
    "safety": {"stopOnDeath": True, "maxRuntimeHours": 0},
}


def get_config(bridge_config):
    config = controller.get_mastering_mixology_config()
    if config is None:
        return DEFAULT_CONFIG

    # ConfigExporter.exportMasteringMixology only emits enabled/bank/digweed/speedups/breaks/
    # safety -- herbPriority/hopperTarget/hopperLowWater/maxObjectDistance silently vanish
    # whenever the Controller plugin is actually running (i.e. always, in real deployment) if the
    # live bridge config were returned as-is, even though every phase function still reads them
    # via config.get(key, DEFAULT_CONFIG[key]) and so never crashes -- it just silently ignores
    # any user customization of those keys. Same bug golem_crafting.py's get_config hit and fixed
    # for its own "locations" key -- same fix shape here.
    config = dict(config)
    for key in ("herbPriority", "hopperTarget", "hopperLowWater", "maxObjectDistance"):
        config.setdefault(key, DEFAULT_CONFIG[key])
    return config


def find_item(name: str):
    """First inventory item whose name matches `name` exactly (case-insensitive) -- same
    exact-match idiom as golem_crafting.py's find_item."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if (i.get("name") or "").lower() == name_lower),
        None,
    )


def find_object(name: str):
    """Closest visible object whose name contains `name`, within MAX_OBJECT_DISTANCE -- same
    shape (including the radius fix) as golem_crafting.py's find_object. Every Mastering
    Mixology fixture is a multiloc/impostor object, so this resolves correctly regardless of
    which content variant (empty, or holding a specific potion) is currently active -- see the
    design doc's "Physical objects are multiloc" note."""
    name_lower = name.lower()
    candidates = [
        o for o in controller.get_objects(radius=MAX_OBJECT_DISTANCE)
        if name_lower in (o.get("name") or "").lower()
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def check_level_requirement(ctx, config: dict) -> None:
    """One-time-per-activation under-level sanity check -- same shape and same setup()-vs-tick()
    reasoning as golem_crafting.py's check_level_requirement."""
    herblore = controller.get_skills().get("Herblore") or {}
    level = herblore.get("level")
    if level is not None and level < HERBLORE_LEVEL_REQUIRED:
        ctx.state.trigger_safety_stop(
            f"Herblore level {level} is below the {HERBLORE_LEVEL_REQUIRED} required for Mastering Mixology"
        )


def bank_herbs_phase(ctx, config: dict) -> None:
    """Withdraw herbs per herbPriority (first candidate actually in the bank, per paste type),
    refine them, deposit the resulting paste into the hopper. Repeats (the phase re-enters
    itself on the next tick if the hopper target isn't reached yet) until every paste type in
    /mixology-state's hopper totals reaches hopperTarget, or safety-stops if the bank has none
    of a paste type's configured herb candidates left at all."""
    hopper = controller.get_mixology_state().get("hopper", {})
    target = config.get("hopperTarget", 1000)
    if all(hopper.get(paste, 0) >= target for paste in ("mox", "aga", "lye")):
        debug_log.log("mixology", f"hopper stocked ({hopper}) -- moving to orders phase")
        ctx.state.data["mm_phase"] = "orders"
        return

    herb_priority = config.get("herbPriority", DEFAULT_CONFIG["herbPriority"])
    needed_types = [p for p in ("mox", "aga", "lye") if hopper.get(p, 0) < target]
    if not needed_types:
        ctx.state.data["mm_phase"] = "orders"
        return
    per_type_qty = min(14, 28 // len(needed_types))

    bank_cfg = {"teleportFallback": config.get("bank", {}).get("teleportFallback", False)}
    bank_obj = banker.find_nearby_bank(bank_cfg)
    if bank_obj is None:
        ctx.state.trigger_safety_stop("no bank found near Mastering Mixology (and no teleport fallback configured)")
        return
    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank")
        return

    banker.deposit_inventory(ctx, {"keepItems": []})

    # fresh=True not strictly needed here (the bank was just opened this same call), but matches
    # this plan's Global Constraints convention of never trusting a possibly-stale read for a
    # decision that's about to drive a withdrawal.
    bank_contents = controller.get_bank(fresh=True).get("items", [])
    withdraw = []
    for paste_type in needed_types:
        candidates = herb_priority.get(paste_type, [])
        chosen = next(
            (name for name in candidates
             if any(name.lower() in (i.get("name") or "").lower() for i in bank_contents)),
            None,
        )
        if chosen is None:
            banker.close_bank(ctx)
            ctx.state.trigger_safety_stop(
                f"none of the configured {paste_type} herbs ({candidates}) are in the bank"
            )
            return
        withdraw.append({"name": chosen, "quantity": per_type_qty})

    banker.withdraw_loadout(ctx, {"withdraw": withdraw})
    banker.close_bank(ctx)

    _refine_and_hopper_inventory(ctx)


def _refine_and_hopper_inventory(ctx) -> None:
    """Deposit every clean herb / unfinished potion currently held into the refiner (clicking
    every tick to double refine speed, per the wiki), then deposit the resulting paste into the
    hopper. Loops until nothing refinable is left in the inventory."""
    refiner = find_object("Refiner")
    if refiner is None:
        ctx.state.trigger_safety_stop("no refiner found near the player")
        return

    all_herb_names = {name for names in DEFAULT_CONFIG["herbPriority"].values() for name in names}
    deadline = time.time() + 60.0  # a full inventory of herbs refines in well under this at 1/tick
    while time.time() < deadline:
        held = [i for i in controller.get_inventory() if (i.get("name") or "") in all_herb_names]
        if not held:
            break
        controller.click_entity(ctx.ser, refiner, check_ui_blockers=False)
        time.sleep(random.uniform(0.55, 0.75))  # one game tick, matches the refiner's own
                                                  # per-tick processing cadence when clicked

    hopper_obj = find_object("Hopper")
    if hopper_obj is None:
        ctx.state.trigger_safety_stop("no hopper found near the player")
        return
    controller.click_entity(ctx.ser, hopper_obj, check_ui_blockers=False)
    time.sleep(random.uniform(0.4, 0.7))


def _choose_orders(orders: list) -> list:
    """Max-points-per-paste selection: make every non-single-paste order, plus every
    single-paste order too if an MAL order is present in this same set of 3. If ALL 3 are
    single-paste, make only the one whose paste type currently has the lowest hopper stock
    (spends down whichever is SCARCEST, to keep the three roughly balanced over time, since
    single-paste orders are the only lever this routine has for correcting an imbalance;
    skipping the other two loses no points since single-paste orders are net-negative on their
    own per the wiki's reward-ratio table)."""
    has_mal = any(o.get("name") == MAL_BASE for o in orders)
    if has_mal:
        return list(orders)

    non_single = [o for o in orders if o.get("name") not in SINGLE_PASTE_BASES]
    if non_single:
        return non_single

    # All 3 are single-paste -- caller (orders_phase) picks which one via hopper stock, since
    # that needs a live hopper read this pure function shouldn't fetch itself.
    return orders


def orders_phase(ctx, config: dict) -> None:
    data = controller.get_mixology_state()
    orders = data.get("orders", [])
    hopper = data.get("hopper", {})

    if len(orders) < 3:
        # Bridge read came back short (e.g. not actually in the lab, or a fresh spawn not yet
        # populated) -- don't act on a partial order set.
        return

    chosen = _choose_orders(orders)
    if chosen and all(o.get("name") in SINGLE_PASTE_BASES for o in chosen):
        chosen = [min(chosen, key=lambda o: hopper.get(_paste_type_of(o), 0))]

    needed = {"mox": 0, "aga": 0, "lye": 0}
    for order in chosen:
        for paste in ("mox", "aga", "lye"):
            needed[paste] += order.get(paste, 0) * 10  # each lever pull uses 10 paste

    low_water = config.get("hopperLowWater", 150)
    if any(hopper.get(p, 0) - needed[p] < low_water for p in ("mox", "aga", "lye")):
        debug_log.log("mixology", f"hopper would drop below low-water fulfilling {[o.get('name') for o in chosen]} -- rebanking")
        ctx.state.data["mm_phase"] = "bank_herbs"
        return

    ctx.state.data["mm_chosen_orders"] = chosen
    debug_log.log("mixology", f"chosen orders this cycle: {[o.get('name') for o in chosen]}")
    ctx.state.data["mm_phase"] = "mix"


def _paste_type_of(order: dict) -> str:
    """The single paste type a single-paste order (AAA/MMM/LLL) needs -- whichever of
    mox/aga/lye is non-zero on that order dict."""
    return next(p for p in ("mox", "aga", "lye") if order.get(p, 0) > 0)


# Mixing vessel object ids by content, confirmed via direct ObjectID/ObjectID1 jar lookup (see
# this plan's Global Constraints) -- used to confirm the vessel's actual content after pulling
# levers, rather than trusting the pull count alone (see Global Constraints' "always re-read...
# rather than tracking locally").
VESSEL_ID_TO_BASE = {
    # This is the wiki-infobox object-id order, NOT the varbit type-index order RECIPE_TABLE
    # uses -- they genuinely differ (Aqualux amalgam/Azure aura mix and Anti-leech lotion/
    # Megalite liquid are swapped between the two). See this plan's Global Constraints.
    54919: "Mammoth-might mix", 54920: "Mystic mana amalgam", 54921: "Marley's moonlight",
    54922: "Alco-augmentator", 54923: "Aqualux amalgam", 54924: "Azure aura mix",
    54925: "Liplack liquor", 54926: "Anti-leech lotion", 54927: "Megalite liquid",
    54928: "Mixalot",
}

LEVER_NAMES = {"mox": "Mox lever", "aga": "Aga lever", "lye": "Lye lever"}


def mix_phase(ctx, config: dict) -> None:
    chosen = ctx.state.data.get("mm_chosen_orders", [])
    made = ctx.state.data.get("mm_made_bases", [])

    if len(made) >= len(chosen):
        ctx.state.data["mm_made_bases"] = []
        ctx.state.data["mm_phase"] = "process"
        return

    target_order = chosen[len(made)]
    pulls = []
    for paste in ("mox", "aga", "lye"):
        pulls.extend([paste] * target_order.get(paste, 0))

    for paste in pulls:
        lever = find_object(LEVER_NAMES[paste])
        if lever is None:
            ctx.state.trigger_safety_stop(f"no {LEVER_NAMES[paste]} found near the player")
            return
        controller.click_entity(ctx.ser, lever, check_ui_blockers=False)
        time.sleep(random.uniform(0.5, 0.7))  # one game tick -- levers resolve per-tick

    vessel = find_object("Mixing vessel")
    if vessel is None:
        ctx.state.trigger_safety_stop("no mixing vessel found near the player")
        return

    actual_base = VESSEL_ID_TO_BASE.get(vessel.get("id"))
    if actual_base != target_order.get("name"):
        # Vessel content doesn't match what this phase just tried to build -- a misclick on one
        # of the lever pulls above is the likely cause (see Global Constraints). Don't take a
        # wrong potion; log and retry the same target_order next tick with the vessel as it
        # actually is now, not what pulls we think we made.
        debug_log.log("mixology", f"vessel shows '{actual_base}', expected '{target_order.get('name')}' -- retrying")
        return

    # Fix 3 (final whole-branch review): the wiki confirms the game can refuse the take
    # (insufficient paste, insufficient Herblore level), and click_entity's own
    # MISTAKE_MISCLICK_CHANCE can also cause a miss -- don't advance mm_made_bases until the
    # base actually lands in inventory, matching golem_crafting.py's reclick-gate idiom for the
    # same class of "click looked fine but didn't land" problem.
    target_id = next(
        (k for k, v in UNFINISHED_IDS.items() if v == target_order.get("name")), None
    )
    controller.click_entity(ctx.ser, vessel, check_ui_blockers=False)
    took = False
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if target_id is not None and any(i.get("id") == target_id for i in controller.get_inventory()):
            took = True
            break
        time.sleep(0.3)
    if not took:
        debug_log.log(
            "mixology",
            f"vessel take for '{target_order.get('name')}' didn't land in inventory -- retrying",
        )
        return
    ctx.state.data["mm_made_bases"] = made + [target_order.get("name")]

    # Fix 5 (final whole-branch review): live inventory check instead of the old sticky
    # ctx.state.data["mm_have_digweed"] flag -- digweed withers out of inventory if the player
    # leaves the minigame area (e.g. a bank trip), which would otherwise leave the flag stuck
    # True forever; and find_item() is an exact-name match, which made the old flag-set fragile
    # if the plant's real in-game name isn't precisely "Digweed".
    digweed_item = next(
        (i for i in controller.get_inventory() if i.get("id") == MM_LAB_SPECIAL_HERB_ID), None
    )
    if digweed_item is not None and target_order.get("name") == MAL_BASE:
        base_item = find_item(MAL_BASE)
        if base_item is not None:
            controller.click_entity(ctx.ser, digweed_item, check_ui_blockers=False)
            time.sleep(random.uniform(0.15, 0.3))
            controller.click_entity(ctx.ser, base_item, check_ui_blockers=False)
            time.sleep(random.uniform(0.3, 0.5))


# Confirmed graphics-object ids for each station's speedup click-now cue (see this plan's
# Global Constraints) -- read via the existing /graphics-objects endpoint, no new Java needed.
AGITATOR_CUE_GRAPHICS_ID = 2954
ALEMBIC_CUE_GRAPHICS_ID = 2955

STATION_ORDER = ["Alembic", "Retort", "Agitator"]  # wiki's own recommended pathing order


def process_phase(ctx, config: dict) -> None:
    chosen = ctx.state.data.get("mm_chosen_orders", [])

    # Check if we have any unprocessed bases at all -- id-based (Fix 1, final whole-branch
    # review): processing transforms a base potion in place while keeping its display `name`
    # identical, so a name-in-RECIPE_TABLE test can never distinguish unprocessed from processed
    # and this check could never observe completion. See UNFINISHED_IDS/FINISHED_IDS above.
    current_inventory = controller.get_inventory()
    unprocessed_any = any(i.get("id") in UNFINISHED_IDS for i in current_inventory)
    if not unprocessed_any:
        ctx.state.data["mm_phase"] = "deposit"
        return

    # Process each chosen order in sequence, finding one unprocessed potion per order
    # (not pre-grouped by name, so duplicate-named orders with different stations don't collide).
    # Fix 8 (final whole-branch review): sort by station (STATION_ORDER's wiki-recommended
    # Alembic -> Retort -> Agitator pathing) while still iterating per-order, so the collision
    # fix Task 6 already made doesn't cost the station-grouped pathing optimization too.
    for order in sorted(chosen, key=lambda o: STATION_ORDER.index(o.get("station"))):
        order_name = order.get("name")
        station_name = order.get("station")

        # Find an unprocessed potion matching this order's name -- id-based (Fix 1).
        current_inventory = controller.get_inventory()
        potion = next(
            (i for i in current_inventory if i.get("name") == order_name and i.get("id") in UNFINISHED_IDS),
            None,
        )

        if potion is None:
            continue

        station = find_object(station_name)
        if station is None:
            continue

        _process_one(ctx, config, station_name, station, potion)

    # Check if anything unprocessed remains -- if so, retry next tick instead of transitioning.
    # Id-based (Fix 1).
    remaining = [i for i in controller.get_inventory() if i.get("id") in UNFINISHED_IDS]
    if remaining:
        debug_log.log("mixology", f"still unprocessed: {[i.get('name') for i in remaining]} -- retrying")
        return

    ctx.state.data["mm_phase"] = "deposit"


def _process_one(ctx, config: dict, station_name: str, station: dict, potion: dict) -> None:
    # Fix 7 (final whole-branch review): the wiki (data/raw/M/Mastering Mixology.txt:425)
    # confirms left-clicking the machine alone inputs the FIRST unprocessed potion in the
    # inventory, regardless of which order it fulfills -- click the specific potion item first,
    # same two-click item-on-target idiom mix_phase already uses for combining digweed into a
    # base, so the right potion (not just whichever happens to be first) goes into this station.
    controller.click_entity(ctx.ser, potion, check_ui_blockers=False)
    time.sleep(random.uniform(0.2, 0.35))
    controller.click_entity(ctx.ser, station, check_ui_blockers=False)
    time.sleep(random.uniform(0.4, 0.6))

    speedups = config.get("speedups", DEFAULT_CONFIG["speedups"])
    if station_name == "Retort" and speedups.get("retort"):
        _process_retort_speedup(ctx, station)
    elif station_name == "Agitator" and speedups.get("agitator"):
        _process_agitator_speedup(ctx, station)
    elif station_name == "Alembic" and speedups.get("alembic"):
        _process_alembic_speedup(ctx, station)

    # Fix 2 (final whole-branch review): processing transforms the item in place -- inventory
    # LENGTH never changes (only the item's id changes), so the old "wait for inventory count to
    # change" gate always burned its full timeout. Poll for this specific potion's id
    # transitioning from its UNFINISHED_IDS id to the corresponding FINISHED_IDS id (+10) instead.
    potion_id = potion.get("id")
    finished_id = potion_id + 10 if potion_id is not None else None
    deadline = time.time() + 20.0
    while time.time() < deadline:
        time.sleep(0.3)
        if finished_id is not None and any(i.get("id") == finished_id for i in controller.get_inventory()):
            return


def _process_retort_speedup(ctx, station: dict) -> None:
    """No timing window (confirmed: the reference plugin tracks no varbit/graphics cue for the
    Retort at all, consistent with the wiki's "click every tick" description) -- click every
    tick while /mixology-state still reports a potion loaded in the Retort."""
    deadline = time.time() + 20.0
    while time.time() < deadline:
        state = controller.get_mixology_state()
        if not state.get("stations", {}).get("retort", {}).get("potionName"):
            return
        controller.click_entity(ctx.ser, station, check_ui_blockers=False)
        time.sleep(random.uniform(0.55, 0.75))  # one game tick


def _process_agitator_speedup(ctx, station: dict) -> None:
    """Click again when graphics-object id 2954 (VFX_MACHINERY_ALCHEMY01_AGITATOR01) appears at
    the Agitator's tile -- the visual "fireworks" cue. Bounded wait: per the wiki, a
    consistently-missed window is worse than not attempting the speedup, so this gives up
    cleanly rather than risking a late click against a closed window."""
    deadline = time.time() + 15.0
    while time.time() < deadline:
        graphics = controller.get_graphics_objects()
        cue = next(
            (g for g in graphics
             if g.get("id") == AGITATOR_CUE_GRAPHICS_ID
             and g.get("x") == station.get("x") and g.get("y") == station.get("y")),
            None,
        )
        if cue is not None:
            controller.click_entity(ctx.ser, station, check_ui_blockers=False)
            return
        time.sleep(0.15)


def _process_alembic_speedup(ctx, station: dict) -> None:
    """Same shape as the Agitator's speedup -- click again when graphics-object id 2955
    (VFX_MACHINERY_ALCHEMY01_ALEMBIC01) appears at the Alembic's tile."""
    deadline = time.time() + 15.0
    while time.time() < deadline:
        graphics = controller.get_graphics_objects()
        cue = next(
            (g for g in graphics
             if g.get("id") == ALEMBIC_CUE_GRAPHICS_ID
             and g.get("x") == station.get("x") and g.get("y") == station.get("y")),
            None,
        )
        if cue is not None:
            controller.click_entity(ctx.ser, station, check_ui_blockers=False)
            return
        time.sleep(0.15)


def deposit_phase(ctx, config: dict) -> None:
    belt = find_object("Conveyor belt")
    if belt is None:
        ctx.state.trigger_safety_stop("no conveyor belt found near the player")
        return

    controller.click_entity(ctx.ser, belt, check_ui_blockers=False)
    time.sleep(random.uniform(0.5, 0.8))

    # Fix 4 (final whole-branch review): don't transition unconditionally -- confirm the click
    # actually landed by re-reading inventory, same retry-next-tick idiom process_phase's own
    # fix round established.
    remaining = [i for i in controller.get_inventory() if i.get("id") in FINISHED_IDS]
    if remaining:
        debug_log.log(
            "mixology",
            f"still have finished potions after conveyor belt click: {[i.get('name') for i in remaining]} -- retrying",
        )
        return

    ctx.state.data["mm_phase"] = "orders"


DIGWEED_CORNERS = ["northEast", "southEast", "southWest", "northWest"]  # /mixology-state's
                                                                          # digweed dict keys


def maybe_collect_digweed(ctx, config: dict) -> None:
    # Fix 5 (final whole-branch review): live inventory check instead of a sticky
    # ctx.state.data flag -- the wiki confirms digweed withers out of inventory if the player
    # exits the minigame area (e.g. an orders -> bank_herbs low-water trip), which would leave a
    # sticky flag stuck True and digweed collection permanently disabled; and find_item() is an
    # exact-name match, not substring, so a stale flag-set based on it was also fragile. This
    # also brings the file in line with the plan's own Global Constraint of always re-reading
    # live state rather than tracking it locally.
    if any(i.get("id") == MM_LAB_SPECIAL_HERB_ID for i in controller.get_inventory()):
        return  # already holding one -- per the wiki, holding it suppresses new spawns anyway
    if not config.get("digweed", {}).get("enabled", True):
        return

    state = controller.get_mixology_state()
    digweed = state.get("digweed", {})
    if not any(digweed.get(corner) for corner in DIGWEED_CORNERS):
        return

    plant = find_object("Digweed")
    if plant is None:
        return  # ready per the varbit but not yet visible/already collected by the time we look

    controller.click_entity(ctx.ser, plant, check_ui_blockers=False)
    time.sleep(random.uniform(0.5, 0.8))


PHASES = {
    "bank_herbs": bank_herbs_phase,
    "orders": orders_phase,
    "mix": mix_phase,
    "process": process_phase,
    "deposit": deposit_phase,
}


def setup(ctx) -> None:
    ctx.state.data.setdefault("mm_phase", "bank_herbs")


def tick(ctx) -> None:
    config = ctx.state.config
    if not ctx.state.data.get("mm_level_checked"):
        check_level_requirement(ctx, config)
        ctx.state.data["mm_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    maybe_collect_digweed(ctx, config)

    phase = ctx.state.data.get("mm_phase", "bank_herbs")
    handler = PHASES.get(phase)
    if handler is None:
        ctx.state.trigger_safety_stop(f"unknown mastering_mixology phase '{phase}'")
        return
    handler(ctx, config)
