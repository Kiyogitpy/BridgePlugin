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
        self.account_hash = None       # no hash = no base-gear file, the rule bank memory follows


class World:
    """Everything the routine reads, and the few things it changes."""

    def __init__(self, assignments, bank, start=(3222, 3218, 0)):
        self.assignments = list(assignments)             # [(master, task, count)], handed out in order
        self.bank = dict(bank)
        self.inventory = {}                              # display name -> quantity
        self.player = {"x": start[0], "y": start[1], "plane": start[2]}
        self.task, self.count, self.streak, self.points = None, 0, 8, 200
        self.eats_per_tick = 0                           # sharks eaten per fight tick; 0 = never hungry
        self.worn = {"weapon": "Abyssal whip", "helmet": "Slayer helmet (i)"}
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
        self.player = {"x": 3150, "y": 3635, "plane": 0}     # a bank trip ENDS at a bank: Ferox Enclave
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

    def equipment(self):
        """GET /equipment's real shape: EVERY slot is present, an empty one as {} (measured live
        2026-09-21). The routine's two-handed check depends on that distinction."""
        slots = {"weapon": {}, "shield": {}, "helmet": {}, "boots": {}}
        for slot, name in self.worn.items():
            slots[slot] = {"name": name, "id": None, "qty": 1}
        return slots

    def equip(self, ctx, name):
        item = next((n for n in self.inventory if name.lower() in n.lower()), None)
        if item is None:
            return False
        slot = next((sl for frag, sl in (("shield", "shield"), ("boots", "boots"), ("helm", "helmet"))
                     if frag in item.lower()), "weapon")
        self.inventory[item] -= 1
        if self.inventory[item] <= 0:
            del self.inventory[item]
        if slot in self.worn:
            self.inventory[self.worn[slot]] = self.inventory.get(self.worn[slot], 0) + 1
        self.worn[slot] = item
        self.log.append(f"EQUIP {item}")
        return True

    def out_of_supplies(self, ctx):
        """Out of food with a task still running -- what combat_core reports when HP is low and
        nothing is left to eat."""
        return self.count > 0 and self.eats_per_tick > 0 and not any("shark" in n.lower() for n in self.inventory)

    def fight_tick(self, ctx):
        ctx.state.data["current_target_index"] = 77
        for name in [n for n in self.inventory if "shark" in n.lower()][:1]:
            self.inventory[name] -= self.eats_per_tick
            if self.inventory[name] <= 0:
                del self.inventory[name]
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
    monkeypatch.setattr(controller, "get_equipment", world.equipment)
    monkeypatch.setattr(routine.gear, "equip_from_inventory", world.equip)
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
    monkeypatch.setattr(combat_core, "out_of_critical_supplies", world.out_of_supplies)
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


def test_a_long_task_runs_out_of_food_restocks_and_comes_back_to_finish(monkeypatch):
    """Nechryael x100, fifteen sharks, one eaten a tick and five kills a tick: the food is gone
    before the kills are. The routine must leave, bank to the same pack, travel back and finish --
    and the restocks must be counted per task."""
    world = World([("KURADAL", "Nechryael", 100)], BANK)
    world.streak, world.eats_per_tick = 9, 1
    _install(monkeypatch, world)
    ctx = _Ctx()
    _run(ctx, ticks=200, world=world)
    assert ctx.state.stops == [], (ctx.state.stops, world.log)
    assert world.log[-1].startswith("TASK DONE")
    banks = [l for l in world.log if l.startswith("BANK")]
    assert len(banks) >= 2, "one pack, at least one restock"
    assert all("Shark" in b for b in banks[1:]), "every restock tops the food back up"
    # After each restock it TRAVELLED back before fighting on: a slayer ring leg follows every bank.
    for index, line in enumerate(world.log):
        if line.startswith("BANK") and index + 1 < len(world.log):
            assert world.log[index + 1].startswith("LEG slayer ring"), world.log[index:index + 3]


def test_a_task_that_keeps_running_dry_stops_after_the_restock_ceiling(monkeypatch):
    world = World([("KURADAL", "Nechryael", 5000)], BANK)
    world.streak, world.eats_per_tick = 9, 15             # a whole pack of food every fight tick
    _install(monkeypatch, world)
    ctx = _Ctx()
    _run(ctx, ticks=300, world=world)
    assert ctx.state.stops and "restocks" in ctx.state.stops[0] and "nechryael" in ctx.state.stops[0].lower()
    assert len([l for l in world.log if l.startswith("BANK")]) == 1 + routine.RESTOCK_MAX_PER_TASK


# --- restarts: ctx.state.data is WIPED, the game is not (CLAUDE.md's standing warning) ----------

def _restart(ctx):
    fresh = _Ctx()
    fresh.state.stops = ctx.state.stops
    return fresh


def test_a_restart_in_the_middle_of_a_fight_carries_on_without_a_needless_bank_trip(monkeypatch):
    """Killed and relaunched at the site with two sharks eaten: the bookkeeping is gone, the task
    is not. It must FIGHT on -- not decide the pack is short and walk off to a bank."""
    world = World([("KURADAL", "Nechryael", 100)], BANK)
    world.streak = 9
    _install(monkeypatch, world)
    ctx = _Ctx()
    _run(ctx, ticks=6, world=world)                       # asked, packed, travelled, fighting
    assert world.count < 100 and world.count > 0, world.log
    world.inventory["Shark"] -= 2
    banks_before = len([l for l in world.log if l.startswith("BANK")])
    ctx = _restart(ctx)
    _run(ctx, ticks=60, world=world)
    assert ctx.state.stops == [] and world.log[-1].startswith("TASK DONE")
    assert len([l for l in world.log if l.startswith("BANK")]) == banks_before, "no bank trip after the restart"


def test_a_restart_at_the_bank_with_the_pack_in_hand_just_travels(monkeypatch):
    world = World([("AYA", "Cows", 20)], BANK)
    _install(monkeypatch, world)
    ctx = _Ctx()
    _run(ctx, ticks=2, world=world)                       # asked, then banked: standing at Ferox
    assert world.log[-1].startswith("BANK"), world.log
    ctx = _restart(ctx)
    _run(ctx, ticks=60, world=world)
    assert ctx.state.stops == [] and world.log[-1].startswith("TASK DONE")
    assert len([l for l in world.log if l.startswith("BANK")]) == 1, "the pack was complete: not banked twice"


def test_a_restart_in_the_middle_of_a_half_failed_restock_heals_itself(monkeypatch):
    """The restocking flag lives in ctx.state.data and dies with the process. Standing at Ferox
    with no food and no flag, the ordinary BANK step must still see the pack is short."""
    world = World([("KURADAL", "Nechryael", 100)], BANK)
    world.streak, world.eats_per_tick = 9, 1
    _install(monkeypatch, world)
    ctx = _Ctx()
    real_bank, failures = world.bank_up, {"left": 1}

    def flaky(ctx_, cfg):
        if world.count < 100 and failures["left"]:        # the RESTOCK trip: teleports, then fails
            failures["left"] -= 1
            world.player = {"x": 3150, "y": 3635, "plane": 0}
            world.log.append("BANK FAILED at Ferox")
            return False
        return real_bank(ctx_, cfg)
    monkeypatch.setattr(routine.banker, "bank_up", flaky)
    for _ in range(200):
        if world.log and world.log[-1] == "BANK FAILED at Ferox":
            break
        routine.tick(ctx)
    ctx = _restart(ctx)                                   # the flag is gone with the process
    _run(ctx, ticks=200, world=world)
    assert ctx.state.stops == [] and world.log[-1].startswith("TASK DONE"), (ctx.state.stops, world.log)
    after = world.log[world.log.index("BANK FAILED at Ferox") + 1]
    assert after.startswith("BANK") and "Shark" in after, "it banked BEFORE travelling back"


# --- a task on the skip list ---------------------------------------------------------------------

def test_a_skip_list_task_is_cancelled_through_kuradal_and_the_next_one_is_taken(monkeypatch):
    world = World([("KURADAL", "Drakes", 120), ("KURADAL", "Hellhounds", 10)], BANK)
    world.streak = 9

    def cancel_task(ctx, master, tasks_cfg):
        assert master == "KURADAL", "the cancel goes through the milestone master, never Aya"
        world.log.append(f"CANCEL {world.task}")
        world.task, world.count, world.points = None, 0, world.points - 30
        return True
    _install(monkeypatch, world)
    monkeypatch.setattr(npc_contact, "cancel_task", cancel_task)
    ctx = _Ctx()
    _run(ctx, ticks=80, world=world)
    assert ctx.state.stops == [], (ctx.state.stops, world.log)
    assert world.log[:3] == ["ACQUIRE Drakes x120 from KURADAL", "CANCEL Drakes", "ACQUIRE Hellhounds x10 from KURADAL"]
    assert world.points == 200 - 30 + 15 and world.log[-1].startswith("TASK DONE")


# --- the gear seam: fetched at the bank, WORN, then travelled with -------------------------------

GEAR_BANK = dict(BANK, **{"mirror shield": 1, "boots of stone": 1})


def test_a_task_whose_gear_is_in_the_bank_fetches_it_wears_it_then_travels(monkeypatch):
    """Basilisks: the mirror shield is banked. The order must be pack -> wear -> travel, because a
    shield in the INVENTORY does nothing, and because it cannot be worn before it is fetched."""
    world = World([("KURADAL", "Basilisks", 20)], GEAR_BANK)
    world.streak = 9
    _install(monkeypatch, world)
    ctx = _Ctx()
    _run(ctx, ticks=60, world=world)
    assert ctx.state.stops == [], (ctx.state.stops, world.log)
    kinds = [l.split()[0] for l in world.log]
    assert kinds.index("BANK") < kinds.index("EQUIP") < kinds.index("LEG"), world.log
    assert "EQUIP Mirror shield" in world.log
    assert world.worn["shield"] == "Mirror shield" and world.worn["weapon"] == "Abyssal whip"
    assert "Abyssal whip" not in world.inventory, "a one-handed weapon is not displaced"
    assert world.log[-1].startswith("TASK DONE")


def test_a_two_handed_weapon_stops_the_task_rather_than_fight_bare_handed(monkeypatch):
    world = World([("KURADAL", "Basilisks", 20)], GEAR_BANK)
    world.streak = 9
    world.worn = {"weapon": "Toxic blowpipe", "helmet": "Slayer helmet (i)"}
    _install(monkeypatch, world)

    def two_handed_equip(ctx, name):
        ok = world.equip(ctx, name)
        if ok and "shield" in name.lower():
            world.inventory[world.worn.pop("weapon")] = 1        # the blowpipe comes off
        return ok
    monkeypatch.setattr(routine.gear, "equip_from_inventory", two_handed_equip)
    ctx = _Ctx()
    _run(ctx, ticks=60, world=world)
    assert ctx.state.stops and "weapon" in ctx.state.stops[0].lower()
    assert not any(l.startswith("TASK DONE") for l in world.log), "it stopped instead of fighting"


def test_the_base_gear_goes_back_on_before_the_next_task_is_asked_for(monkeypatch, tmp_path):
    """The whip the mirror shield did not displace stays; the HELMET a heat-boots task swaps is
    what proves the restore. Wyrms want boots of stone, so the base boots come back afterwards."""
    world = World([("KURADAL", "Wyrms", 10), ("AYA", "Cows", 20)], GEAR_BANK)
    world.streak = 9
    world.worn = {"weapon": "Abyssal whip", "helmet": "Slayer helmet (i)", "boots": "Dragon boots"}
    _install(monkeypatch, world)
    # A real account has a hash and a file; the fake bank memory has neither. Give the save/load
    # path a temp file so the REAL slayer_policy capture and restore run.
    monkeypatch.setattr(routine.slayer_policy, "base_gear_path",
                        lambda account_hash, root=None: str(tmp_path / "base-gear.json"))
    ctx = _Ctx()
    _run(ctx, ticks=90, world=world)
    assert ctx.state.stops == [], (ctx.state.stops, world.log)
    assert "EQUIP Boots of stone" in world.log
    # The base was captured on tick 1 (no task held), so the displaced Dragon boots go back on
    # once the wyrms are done -- BEFORE the cows are asked for.
    restore = world.log.index("EQUIP Dragon boots")
    assert restore < world.log.index("ACQUIRE Cows x20 from AYA"), world.log
    assert world.worn["boots"] == "Dragon boots"


# --- a break in the middle of a task -------------------------------------------------------------

def test_a_break_mid_fight_leaves_for_ferox_and_the_task_finishes_afterwards(monkeypatch):
    """The engine calls before_break fully live, then skips tick() for 3-12 minutes. The routine
    must come back from Ferox by itself -- and from Ferox that is always a teleport, because it is
    a sealed island on the collision map."""
    world = World([("KURADAL", "Nechryael", 30)], BANK)
    world.streak = 9
    _install(monkeypatch, world)

    def teleport(ctx):
        world.player = {"x": 3151, "y": 3635, "plane": 0}       # Ferox Enclave
        world.log.append("TELEPORT to Ferox for the break")
        return True
    monkeypatch.setattr(routine.travel, "teleport_to_ferox", teleport)
    ctx = _Ctx()
    for _ in range(5):                                          # get to the site and start fighting
        routine.tick(ctx)
    assert world.count < 30, world.log

    routine.before_break(ctx)                                   # the break falls mid-fight
    assert "TELEPORT to Ferox for the break" in world.log
    assert world.player["x"] == 3151, "we are away from the monsters for the whole break"

    _run(ctx, ticks=60, world=world)                            # the break ends; carry on
    assert ctx.state.stops == [], (ctx.state.stops, world.log)
    assert world.log[-1].startswith("TASK DONE")
    after = world.log[world.log.index("TELEPORT to Ferox for the break") + 1:]
    assert any(l.startswith("LEG slayer ring") for l in after), "it teleported back, not walked"
