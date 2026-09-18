# routines/slayer.py
#
# PLACEHOLDER (2026-09-18). The Slayer task orchestrator -- NPC Contact task acquisition,
# Aya/Kuradal milestone selection, per-task banking and travel, the fight delegated to
# combat_core -- is designed in docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md
# and is built in that spec's sub-projects 2 and 3. Until then this file exists so the
# "Slayer (tasks)" panel choice loads something that says so instead of failing to load.
#
# The old "Slayer / Combat" behaviour (fight the monsters picked in the panel) is
# routines/combat.py, "Combat" in the dropdown.

import controller

# The orchestrator owns every dialogue it opens (spec §7); set here already so the engine's
# once-per-tick Space never lands in a slayer master's dialogue.
SKIP_STRAY_DIALOG_CHECK = True


def get_config(bridge_config):
    return controller.get_slayer_config()


def tick(ctx):
    ctx.state.trigger_safety_stop(
        "the Slayer task routine is not built yet (docs/superpowers/specs/"
        "2026-09-18-slayer-task-loop-design.md) -- pick 'Combat' in Controller Settings "
        "for the old fight-the-selected-monsters behaviour"
    )
