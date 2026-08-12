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
    return config if config is not None else DEFAULT_CONFIG


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
        withdraw.append({"name": chosen, "quantity": 14})

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
