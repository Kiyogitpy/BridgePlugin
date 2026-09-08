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
# THE LOG BASKET NEEDS NO DRIVING, only emptying (2026-09-08, USER: "we use that for cutting
# wood basicly doubling our capacity" / "it fills automaticly all we need to do is right click it
# in bank"). An OPEN log basket absorbs logs as they are cut, so the inventory only reaches 28
# once the basket's own 28 are full -- that is the doubling, and it costs no code. What it does
# cost is keeping the thing: it is on keep_items below, and banker.deposit_inventory empties it
# at the bank (banker.empty_log_basket) before the deposit pass runs. Carry it OPEN; a shut one
# collects nothing and this routine does not open it.
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
    #
    # "log basket" is the same rule applied to the container (2026-09-08): it matches neither
    # "axe" nor anything else, so before this BOTH full-inventory policies destroyed it -- bank
    # shift-deposited the basket into the bank right after banker.deposit_inventory had emptied
    # it, and drop threw it on the ground. One substring covers "Log basket" and "Open log
    # basket" both, which is what the cache says the two states are called.
    keep_items=("axe", "log basket"),
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
