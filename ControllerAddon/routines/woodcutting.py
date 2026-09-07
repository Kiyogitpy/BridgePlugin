# routines/woodcutting.py
#
# Chop whichever of the configured trees is nearest. The loop itself lives in gathering.py --
# this file is only the Woodcutting profile over it, the twin of routines/mining.py (added
# 2026-09-07, USER: "isnt our generic mining.py possible to do woodcutting to?").
#
# Tree selection comes from the Controller Settings sidebar panel's "Gathering" section with its
# action set to Chop down: Refresh scans the loaded scene for objects offering "Chop down", the
# checkboxes persist to the `treeNames` config key, and GET /woodcutting-config serves it here.
# The display name is "Yew tree" / "Willow tree" (cache config/loc yewtree 10822, willowtree
# 10819), never the bare wood name -- pick from the panel rather than typing them.
#
# WHY A FELLED TREE NEEDS NO RESPAWN TIMER: the stump keeps the tree's tile but offers no
# "Chop down", so gathering.pick_target stops matching it the moment it falls, and the
# tick-cached bulk scan is covered by the chain's own exclude set. A lone tree simply idles
# until it grows back.
#
# A FULL INVENTORY IS DROPPED, not banked (`fullInventory` in the config, see
# gathering.full_inventory_phase): power-chopping is the normal woodcutting loop. Set it to
# "bank" for a log-banking session -- the banking path is mining's, unchanged and shared.
#
# Run with:
#     python controller.py routines/woodcutting.py

import controller
import gathering

DEFAULT_CONFIG = {
    "enabled": True,
    "treeNames": ["Yew tree"],
    "maxTreeDistance": 20,
    # bank | drop | stop -- see gathering.full_inventory_phase.
    "fullInventory": "drop",

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
    key="woodcutting",
    skill="Woodcutting",
    action="chop down",
    # PlayerProvider.java, added alongside this routine -- needs a client rebuild. Without it
    # the field reads None and only the depletion signal ends a tree: still correct, just
    # slower on a tree that gives several logs before falling.
    xp_field="woodcuttingXp",
    names_key="treeNames",
    distance_key="maxTreeDistance",
    noun="tree",
    # Substring match, so this also keeps a pickaxe or a battleaxe in the inventory -- harmless
    # here, and far better than dropping the axe we are chopping with.
    keep_items=("axe",),
    # Resolved at call time, not bound here: a test (and controller.py's own module-duplication)
    # must be able to swap the bridge helper out from under it.
    config_fetch=lambda: controller.get_woodcutting_config(),
    default_config=DEFAULT_CONFIG,
    full_inventory="drop",
    panel_section="Gathering (Chop down)",
)


def get_config(bridge_config):
    return gathering.get_config(PROFILE)


def tick(ctx):
    gathering.tick(PROFILE, ctx)
