"""routines/slayer.py driven through WHOLE tasks in one simulated world.

Unit tests pin each piece; this pins the seams between them -- the state that must reset when a
task ends, the order BANK / TRAVEL / FIGHT, the hand-off from one task to the next. The world is
fake but CONSISTENT: NPC Contact hands out a task, the banker fills the inventory, a leg moves the
player to its destination, the fight ticks the kill count down. The decisions (disposition, site,
loadout, verdict, plan, walk goal) are all REAL; only what would click is simulated.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import combat_core
import controller
import legs
import npc_contact
import slayer_state
import slayer as routine


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    def refuse(path, *a, **k):
        raise AssertionError(f"a test reached the live bridge: GET {path}")
    monkeypatch.setattr(controller, "api_get", refuse)


TASKS = {"fillerMaster": "AYA", "milestoneMaster": "KURADAL", "milestoneEvery": 10, "skipTasks": [],
         "maxSkips": 8, "pointsReserve": 60}
PANEL = {"tasks": TASKS, "food": {"name": "Shark"}, "potions": {"prayerPotion": "Prayer potion"},
         "bank": {"keepItems": ["rune pouch"]}, "loot": {"minValue": 5000, "items": []}}


class _Memory:
    def __init__(self, items):
        self.seen, self.items = True, [{"name": n, "quantity": q} for n, q in items.items()]


class World:
    """Everything the routine reads, and the few things it changes."""

    def __init__(self, assignments, bank, start=(3222, 3218, 0)):
        self.assignments = list(assignments)             # [(master, task, count)], handed out in order
        self.bank = dict(bank)
        self.inventory = {}                              # display name -> quantity
        self.player = {"x": start[0], "y": start[1], "plane": start[2]}
        self.task, self.count, self.streak, self.points = None, 0, 8, 200
        self.log = []

    # --- what the bridge would answer
    def slayer(self):
        return {"count": self.count, "hasTask": self.count > 0, "taskName": self.task, "taskId": 1,
                "streak": self.streak, "points": self.points}

    def inventory_list(self):
        out = []
        for name, quantity in self.inventory.items():
            stack = any(w in name.lower() for w in ("teleport", "rune", "cooler"))
            out += [{"name": name, "qty": quantity, "id": None}] if stack else \
                   [{"name": name, "qty": 1, "id": None} for _ in range(quantity)]
        return out

    # --- what would click
    def get_task(self, ctx, master, tasks_cfg):
        want_master, task, count = self.assignments.pop(0)
        assert master == want_master, f"asked {master}, the streak wanted {want_master}"
        assert self.count == 0, "a master was contacted with a task active"
        self.task, self.count = task, count
        self.log.append(f"ACQUIRE {task} x{count} from {master}")
        return slayer_state.from_bridge(self.slayer())

    def bank_up(self, ctx, cfg):
        keep = [k.lower() for k in cfg["keepItems"]]
        self.inventory = {n: q for n, q in self.inventory.items() if any(k in n.lower() for k in keep)}
        for entry in cfg["withdraw"]:
            key = entry["name"].lower()
            assert self.bank.get(key, 0) >= entry["quantity"], f"withdrew more {key} than the bank holds"
            self.bank[key] -= entry["quantity"]
            self.inventory[entry["name"]] = self.inventory.get(entry["name"], 0) + entry["quantity"]
        self.log.append("BANK " + ", ".join(f"{e['quantity']} {e['name']}" for e in cfg["withdraw"]))
        return True

    def run_next(self, ctx, route, cursor, log="travel"):
        if cursor >= len(route):
            return legs.FINISHED, cursor
        leg = route[cursor]
        dest = leg.get("dest")
        if leg["kind"] == "poh_enter":
            self.player = {"x": 1922, "y": 7051, "plane": 1, "inInstance": True, "instX": 11669, "instY": 1449}
        elif dest:
            self.player = {"x": dest[0], "y": dest[1], "plane": dest[2]}
        self.log.append(f"LEG {leg['display']}")
        return legs.DONE, cursor + 1

    def fight_tick(self, ctx):
        ctx.state.data["current_target_index"] = 77
        self.count -= 5
        if self.count <= 0:
            self.count, self.task = 0, None
            self.streak += 1
            self.points += 15
            self.log.append(f"TASK DONE (streak {self.streak})")


class _State:
    def __init__(self):
        self.data, self.stops, self.config = {}, [], dict(PANEL)

    def trigger_safety_stop(self, reason):
        self.stops.append(reason)


class _Ctx:
    def __init__(self):
        self.ser, self.events, self.state = None, None, _State()


def _install(monkeypatch, world):
    monkeypatch.setattr(controller, "get_slayer_task", world.slayer)
    monkeypatch.setattr(controller, "get_player", lambda: dict(world.player))
    monkeypatch.setattr(controller, "get_inventory", world.inventory_list)
    monkeypatch.setattr(controller, "get_equipment", lambda: {"helmet": {"name": "Slayer helmet (i)", "id": 1, "qty": 1}})
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: None)
    monkeypatch.setattr(controller, "get_skills", lambda: {"Prayer": {"level": 77}})
    monkeypatch.setattr(controller, "check_stray_dialog", lambda ctx: None)
    monkeypatch.setattr(legs, "skill_levels", lambda: {"Agility": 77})
    monkeypatch.setattr(legs, "requirement_values", lambda: {"varbit_values": {3759: 3}, "varplayer_values": {11: 5}})
    monkeypatch.setattr(legs, "run_next", world.run_next)
    monkeypatch.setattr(routine.bank_memory, "load", lambda names=None: _Memory(world.bank))
    monkeypatch.setattr(routine.banker, "bank_up", world.bank_up)
    monkeypatch.setattr(npc_contact, "missing_requirements", lambda master: None)
    monkeypatch.setattr(npc_contact, "get_task", world.get_task)
    monkeypatch.setattr(combat_core, "fight_tick", world.fight_tick)
    monkeypatch.setattr(routine.time, "time", _Clock())


class _Clock:
    """Each tick is far enough apart that the contact cooldown never hides an ACQUIRE."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 10.0
        return self.now


BANK = {"shark": 500, "prayer potion(4)": 80, "teleport to house": 40, "ring of dueling(8)": 6,
        "ectophial": 1, "slayer ring (8)": 3, "rock hammer": 1}


def _run(ctx, ticks=120, world=None):
    for _ in range(ticks):
        if world is not None and not world.assignments and world.count == 0:
            break                                   # every task handed out has been finished
        routine.tick(ctx)
        if ctx.state.stops:
            break


def test_two_aya_tasks_then_a_kuradal_task_run_with_no_human_touch(monkeypatch):
    """Streak 8 -> task 9 is Aya's, task 10 is KURADAL's (the milestone), task 11 Aya's again."""
    world = World([("AYA", "Cows", 20), ("KURADAL", "Gargoyles", 30), ("AYA", "Banshees", 15)], BANK)
    _install(monkeypatch, world)
    ctx = _Ctx()
    _run(ctx, world=world)
    assert ctx.state.stops == [], (ctx.state.stops, world.log)
    done = [line for line in world.log if line.startswith("TASK DONE")]
    assert len(done) == 3, world.log

    # The order of one task: ask, pack, travel, (fight). No leg before its bank trip.
    first_bank = world.log.index(next(l for l in world.log if l.startswith("BANK")))
    first_leg = world.log.index(next(l for l in world.log if l.startswith("LEG")))
    assert world.log[0].startswith("ACQUIRE Cows") and first_bank < first_leg

    # Each task banked AT MOST once, and the pack was per task: the hammer only for gargoyles.
    banks = [l for l in world.log if l.startswith("BANK")]
    assert len(banks) <= 3
    assert sum("Rock hammer" in b for b in banks) == 1
    assert any("Ectophial" in b for b in banks) and any("Slayer ring" in b for b in banks)

    # Kuradal's task was reached by her slayer ring leg, Aya's by the Ectophial.
    text = " | ".join(world.log)
    assert "LEG ectophial: Empty" in text and "LEG slayer ring: Slayer Tower" in text


def test_a_task_the_bank_cannot_pack_stops_the_run_and_names_the_item(monkeypatch):
    world = World([("AYA", "Cows", 20)], {k: v for k, v in BANK.items() if k != "ectophial"})
    _install(monkeypatch, world)
    ctx = _Ctx()
    _run(ctx, ticks=10)
    assert ctx.state.stops and "ectophial" in ctx.state.stops[0].lower()
    assert not [l for l in world.log if l.startswith(("BANK", "LEG"))], "nothing was spent"


def test_a_stop_task_is_left_alone_and_nothing_is_contacted(monkeypatch):
    world = World([], BANK)
    world.task, world.count = "Greater demons", 74
    _install(monkeypatch, world)
    ctx = _Ctx()
    _run(ctx, ticks=5)
    assert "leaves to you" in ctx.state.stops[0] and world.log == []
