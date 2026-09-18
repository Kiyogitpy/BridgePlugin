# routines/combat.py
#
# "Combat" in the Controller Settings routine dropdown: fight whatever monsters are picked in
# the panel's Slayer section, using the shared fight loop in combat_core.py. This is exactly
# what routines/slayer.py did until 2026-09-18; slayer.py is now the task orchestrator.
#
# Run with:
#     python controller.py routines/combat.py

import combat_core
import controller


# GET /slayer-config already includes the master switch (SlayerConfigProvider reads it off
# ControllerConfig too) alongside every combat setting -- bridge_config (GET /config) isn't
# needed here.
def get_config(bridge_config):
    return controller.get_slayer_config()


on_event = combat_core.on_event
tick = combat_core.fight_tick
