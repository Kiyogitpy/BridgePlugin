# gear.py -- what is worn, what is carried, and putting a carried item on. Added 2026-09-18 for
# the slayer task loop (spec §7.5): a task's required gear (earmuffs, nose peg, rock hammer...)
# is withdrawn at the bank and must be WORN before leaving. Nothing in banker.py could equip.
#
# The click is the pattern combat_core.maintain_bracelet proves live: left-click the inventory
# item with the equip action VERIFIED through /hover (expected_action), no simulated misclick
# (a miss here eats a Shark), and the result CONFIRMED on /equipment -- never assumed from the
# click. /equipment is a dict keyed by slot name; /inventory is a list (banker._item_rows).

import time

import controller
import debug_log

EQUIP_ACTIONS = ("Wear", "Wield", "Equip")
EQUIP_BUDGET_S = 2.0     # click -> the item showing on /equipment (one tick plus the read)
POLL_S = 0.15


def worn_names() -> set:
    eq = controller.get_equipment() or {}
    return {(v.get("name") or "").lower() for v in eq.values() if isinstance(v, dict) and v.get("name")}


def carried_names() -> set:
    return {(i.get("name") or "").lower() for i in (controller.get_inventory() or []) if i.get("name")}


def is_worn(name: str, alternatives=()) -> bool:
    """Substring match, like every item check in this codebase (charge suffixes vary)."""
    worn = worn_names()
    wanted = (name.lower(), *(a.lower() for a in alternatives))
    return any(w in n for n in worn for w in wanted)


def has(name: str) -> bool:
    n = name.lower()
    return any(n in w for w in worn_names()) or any(n in c for c in carried_names())


def equip_from_inventory(ctx, name: str) -> bool:
    """Wear/wield the first carried item containing `name`; True only once /equipment shows it."""
    n = name.lower()
    it = next((i for i in (controller.get_inventory() or []) if n in (i.get("name") or "").lower()), None)
    if it is None:
        debug_log.log("gear", f"no '{name}' in the inventory to equip")
        return False
    controller.open_inventory_tab(ctx.ser)
    ok = controller.click_entity(ctx.ser, it, check_ui_blockers=False, simulate_mistakes=False,
                                 expected_action=EQUIP_ACTIONS)
    if not ok:
        debug_log.log("gear", f"equip click on '{it.get('name')}' did not go "
                              f"({'withheld: no equip action under the cursor' if getattr(ok, 'withheld', False) else 'aim failed'})")
        return False
    deadline = time.time() + EQUIP_BUDGET_S
    while time.time() < deadline:
        if is_worn(n):
            return True
        time.sleep(POLL_S)
    debug_log.log("gear", f"'{it.get('name')}' never showed on /equipment after the click")
    return False


def ensure_worn(ctx, required) -> list:
    """Equip every (name, alternatives) not already worn. Returns the names still missing."""
    missing = []
    for name, alternatives in required:
        if is_worn(name, alternatives):
            continue
        if not equip_from_inventory(ctx, name):
            missing.append(name)
    return missing
