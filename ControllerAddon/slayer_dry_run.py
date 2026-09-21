"""Run the REAL routines/slayer.tick() against the LIVE bridge with every clicking primitive
stubbed out. Added 2026-09-21.

    cd ControllerAddon && python slayer_dry_run.py

READS are real (task, player, inventory, equipment, bank memory, skills, config); NOTHING can move
the mouse: `ctx.ser` is None, so a real serial write would raise, and every primitive that clicks
is replaced by a recorder at import time. Safe beside a running client -- it never opens the COM
port (the same contract as slayer_preflight.py and bank_memory.py).

Why it exists: slayer_preflight.py runs the PURE decisions; this runs the routine itself, so a
shape mismatch anywhere in the read path shows up as a wrong decision or a crash rather than
waiting for a live session. It is what caught the tool reading GET /config while the routine reads
GET /slayer-config, and what surfaced the blighted-food problem from the real bank.

`run(task_name, count, food=...)` pretends the live task is something else, and that the panel has
food and a prayer potion chosen, so the whole pipeline can be exercised from any starting state.
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "routines")

import bank_memory
import banker
import combat_core
import controller
import gear
import legs
import npc_contact
import slayer_state
import slayer as routine

# What the panel SHOULD be set to, measured against this account's real bank 2026-09-21: it holds
# 416 Cooked karambwan and 27 Super restore(4), and NO sharks and NO Prayer potion(4) at all. The
# tool used to pretend Shark and Prayer potion, so every task it tried "failed" on supplies that
# were never going to be there -- testing the pack against a fiction.
DEFAULT_PRETEND_FOOD = "Cooked karambwan"
DEFAULT_PRETEND_POTION = "Super restore"

ACTIONS = []


def _record(label):
    def fn(*a, **k):
        ACTIONS.append(label)
        return True
    return fn


# --- nothing below may reach the Arduino ---------------------------------------------------------
for name in ("click_entity", "click_menu_action", "key_tap", "drop_items", "consume_first",
             "open_inventory_tab", "open_spellbook_if_needed", "check_stray_dialog", "human_delay"):
    if hasattr(controller, name):
        setattr(controller, name, _record(f"controller.{name}"))
banker.bank_up = _record("banker.bank_up")
legs.run_next = lambda ctx, route, cursor, log="travel": (ACTIONS.append(f"leg {route[cursor]['display']}")
                                                          or (legs.DONE, cursor + 1))
gear.equip_from_inventory = lambda ctx, name: ACTIONS.append(f"equip {name}") or True
# ACQUIRE returns a TaskState, not a bool. A plain _record stub answered True and the routine
# then read `got.task` off it -- an AttributeError that only fires when the account holds NO TASK,
# which is exactly the state this tool had never been run in until 2026-09-21. The stub hands back
# the task the caller asked to pretend was assigned, so the ACQUIRE -> PACK -> TRAVEL path can be
# walked end to end without a real cast.
PRETEND_ASSIGNED = {"task": "cave slimes", "count": 17}


def _fake_get_task(ctx, master, tasks_cfg):
    ACTIONS.append(f"npc_contact.get_task from {master}")
    live = slayer_state.from_bridge(controller.get_slayer_task())
    return slayer_state.TaskState(
        count=PRETEND_ASSIGNED["count"], task=PRETEND_ASSIGNED["task"], task_id=0,
        streak=(live.streak if live else 0) + 1, points=(live.points if live else 0),
        master_id=getattr(live, "master_id", 0), has_task=True)


npc_contact.get_task = _fake_get_task
npc_contact.cancel_task = _record("npc_contact.cancel_task")
npc_contact.missing_requirements = lambda master: None
combat_core.fight_tick = _record("combat_core.fight_tick")


class _State:
    def __init__(self, config):
        self.data, self.stops, self.config = {}, [], config
        self.safety_stopped = False
        self.paused = False

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)
        self.safety_stopped = True


class _Ctx:
    def __init__(self, config):
        self.ser, self.events = None, None          # ser None: any real serial write would raise
        self.state = _State(config)


def run(task_name=None, count=150, ticks=3, food=None, potion=None):
    config = controller.get_slayer_config() or {}
    if food:                       # pretend the user has set these in Controller Settings
        config = dict(config, food=dict(config.get("food") or {}, name=food),
                      potions=dict(config.get("potions") or {},
                                   prayerPotion=potion or DEFAULT_PRETEND_POTION))
    real_task = controller.get_slayer_task()
    if task_name:
        def fake(_real=real_task):
            state = dict(_real or {})
            state.update({"taskName": task_name, "count": count, "hasTask": True})
            return state
        controller.get_slayer_task = fake
    ACTIONS.clear()
    ctx = _Ctx(config)
    for _ in range(ticks):
        routine.tick(ctx)
        if ctx.state.stops:
            break
    label = task_name or (real_task or {}).get("taskName")
    print(f"--- {label} x{count if task_name else (real_task or {}).get('count')}")
    for action in ACTIONS:
        print(f"      did: {action}")
    for stop in ctx.state.stops:
        print(f"      STOP: {stop}")
    if not ACTIONS and not ctx.state.stops:
        print("      (nothing -- no phase acted)")
    controller.get_slayer_task = lambda: real_task
    return ctx


if __name__ == "__main__":
    run()                                            # the live task, whatever it is
    print()
    print("=== as if food and a prayer potion WERE set in the panel ===")
    for name, kills in (("Cows", 20), ("Nechryael", 150), ("Basilisks", 20), ("Suqahs", 40),
                        ("Bloodveld", 150), ("Trolls", 80)):
        run(name, kills, ticks=5, food=DEFAULT_PRETEND_FOOD)
