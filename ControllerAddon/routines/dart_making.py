# routines/dart_making.py
#
# Fast, no-banking dart combining: click the dart tips, then click the feathers, over and over.
# No mining, no travel, no bank trip -- start it standing anywhere with dart tips and feathers
# already in the inventory and it just alternates the two clicks until one runs out.
#
# USER 2026-09-17, describing the whole mechanic: "all there is to it is clicking inventory slot
# 0 and inventory slot 1 after clicking 1 click 1 again and click back to 0 then click 0 etc etc.
# very similar speed to the 3t routine we made."
#
# Recorded live the same day (recordings/2026-09-17_155430_dart-making.*, 42s, 131 actions) to
# get the real widget ids and cadence instead of guessing -- see the notes below for what that
# corrected in USER's own description.
#
# WHAT THE RECORDING CORRECTED:
#   - The two items are NOT fixed slots 0 and 1. Amethyst dart tip sat at widget 149.0 slot 0 and
#     Feather at slot 4 for almost the whole session -- nothing sits at slot 1 here (other items
#     stack ahead of the feathers). A fixed slot number is exactly the stale-snapshot trap
#     CLAUDE.md's refresh_target section warns about: once the dart tip stack ran out entirely
#     near the end of the recording (t=154), every item behind it shifted up and Feather itself
#     landed in slot 0. So this looks both items up by NAME every cycle rather than a slot index.
#   - There is no make-menu / quantity dialog. "Use dart tip -> Feather" (or the reverse) fires
#     directly and always produces exactly 10 darts per successful combo -- confirmed by the
#     "You finish making 10 darts." chat line and a matching +10/-10/-10 inventory delta every
#     time. No digit keypress, no skillmulti widget -- unlike fletching.py's knife-on-log.
#
# THE CYCLE: click item A (arms "Use"), then click item B (fires "Use A -> B", the actual
# combine). Order doesn't matter to the game -- the recording shows both dart-tip-then-feather
# and feather-then-dart-tip firing the same combine -- so this always goes primary-then-secondary
# for consistency. A same-item reclick (clicking A again while already armed on A) just wastes a
# click and costs nothing, which is what most of the recording's "nothing observed" clicks are.
#
# Run with:
#     python controller.py routines/dart_making.py

import controller

DEFAULT_CONFIG = {
    "enabled": True,
    # WHICH TWO ITEMS COMBINE, matched by exact inventory name every cycle (see find_item) so a
    # stack that empties and reshuffles the inventory is still found.
    "primaryItem": "Amethyst dart tip",
    "secondaryItem": "Feather",
    # Gap between the two clicks of one combo. MEASURED from the recording's own successful
    # pairs (the ones that actually produced the "You finish making 10 darts." chat line, not
    # the misses): 60-400ms, most well under 300ms. USER: "very similar speed to the 3t routine".
    "comboGapS": [0.08, 0.30],

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
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

# Consecutive ticks that found one or both items missing before this decides the run is
# genuinely out of material, rather than reading a one-off empty inventory -- get_inventory()
# returns [] on ANY bridge read error, never "the items are gone" by itself (CLAUDE.md's
# standing rule: ride out a transient, only stop on a persistent one).
DEPLETED_STOP_LIMIT = 4


def get_config(bridge_config):
    """No sidebar section of its own -- command-line only, same as amethyst_dart_tips.py."""
    merged = dict(DEFAULT_CONFIG)
    if bridge_config:
        merged.update(bridge_config)
    merged["safety"] = dict(DEFAULT_CONFIG["safety"], **(merged.get("safety") or {}))
    return merged


def find_item(inv, name):
    name = (name or "").lower()
    return next((i for i in inv or [] if (i.get("name") or "").lower() == name), None)


def run_combo(ctx, config):
    """One click-pair: arm the primary item, then apply it to the secondary. Fires one "make 10"
    batch whenever both items are present in the inventory. False means one or both are missing
    (out of material, or a transient empty read) -- the caller decides what that means."""
    inv = controller.get_inventory() or []
    primary = find_item(inv, config["primaryItem"])
    secondary = find_item(inv, config["secondaryItem"])
    if primary is None or secondary is None:
        return False

    # Two inventory-interface clicks: both opt out of the UI-blocker check and neither simulates
    # a misclick, same as every other use-on-use inventory click in this repo (fletching.py's
    # knife-on-log, amethyst_dart_tips.py's chisel-on-amethyst).
    controller.click_entity(ctx.ser, primary, offset=(0, 0),
                            check_ui_blockers=False, simulate_mistakes=False)
    controller.human_delay(*config["comboGapS"])
    controller.click_entity(ctx.ser, secondary, offset=(0, 0),
                            check_ui_blockers=False, simulate_mistakes=False)
    return True


def tick(ctx):
    config = ctx.state.config
    if run_combo(ctx, config):
        ctx.state.data["dm_missing_ticks"] = 0
        return

    missing = ctx.state.data.get("dm_missing_ticks", 0) + 1
    ctx.state.data["dm_missing_ticks"] = missing
    if missing >= DEPLETED_STOP_LIMIT:
        ctx.state.trigger_safety_stop(
            f"dart_making: no {config['primaryItem']!r} and/or {config['secondaryItem']!r} in "
            f"the inventory after {missing} ticks -- out of material")
