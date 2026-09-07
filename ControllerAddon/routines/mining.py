# routines/mining.py
#
# Mine whichever of the configured rocks is nearest. The loop itself lives in gathering.py --
# this file is only the Mining profile over it (see that module's header for the measured
# reaction/banking behaviour, which was all developed here before the 2026-09-07 extraction).
#
# Rock selection comes from the Controller Settings sidebar panel's "Gathering" section
# (SettingsPanel.java) with its action set to Mine: Refresh scans the loaded scene for objects
# offering "Mine", the checkboxes persist to the `rockNames` config key, and GET /mining-config
# serves it here. Before 2026-09-03 this routine ignored the bridge and mined a hardcoded list,
# so the panel's Rock Names field never did anything.
#
# Run with:
#     python controller.py routines/mining.py

import controller
import gathering

DEFAULT_CONFIG = {
    "enabled": True,
    "rockNames": ["Adamantite rocks", "Runite rocks"],
    "maxRockDistance": 20,
    # bank | drop | stop -- see gathering.full_inventory_phase. Ore is worth banking.
    "fullInventory": "bank",

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
        # OFF: this routine handles a full inventory itself (gathering.full_inventory_phase).
        # The engine's check_safety runs before tick() and would otherwise win the race.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

PROFILE = gathering.Profile(
    key="mining",
    skill="Mining",
    action="mine",
    xp_field="miningXp",
    names_key="rockNames",
    distance_key="maxRockDistance",
    noun="rock",
    keep_items=("pickaxe",),
    # Resolved at call time, not bound here: a test (and controller.py's own module-duplication)
    # must be able to swap the bridge helper out from under it.
    config_fetch=lambda: controller.get_mining_config(),
    default_config=DEFAULT_CONFIG,
    full_inventory="bank",
    panel_section="Gathering (Mine)",
)


def get_config(bridge_config):
    return gathering.get_config(PROFILE)


def tick(ctx):
    gathering.tick(PROFILE, ctx)
