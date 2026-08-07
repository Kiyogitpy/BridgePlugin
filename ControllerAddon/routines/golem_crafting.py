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
    for side in range(4):
        controller.open_inventory_tab(ctx.ser)  # no-op if already open; harmless before a world-object click
        if not controller.click_entity(ctx.ser, plinth):
            time.sleep(random.uniform(0.3, 0.6))
            continue  # missed/blocked click -- retry the same side next loop iteration
        debug_log.log("golem crafting", f"carved side {side + 1}/4")
        time.sleep(random.uniform(*PLINTH_SIDE_WAIT_SECONDS))

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
