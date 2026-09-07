"""The boat's cargo hold, interface 943 (InterfaceID.SAILING_BOAT_CARGOHOLD).

Every id here was CAPTURED LIVE on 2026-09-07 from a 3x8 boat by a read-only widget watcher
polling /widgets/dump. See
docs/superpowers/specs/2026-09-07-shipwreck-salvaging-mass-world-and-sorting-design.md.

    943.1        title ("Cargo Hold: <boat name>"), and carries the Close action
    943.3        "Item stacking" toggle
    943.4        slots USED
    943.5        CAPACITY
    943.10       the hold's item grid -- Withdraw-All / -1 / -5 / -10 / -X / Examine
    943.13       Deposit Cargo
    943.14       Deposit SALVAGE          <- the only deposit this module ever clicks
    943.15       Deposit Inventory
    943.20..24   quantity buttons 1 / 5 / 10 / X / All
    944.1        the player's inventory side panel

Why 943.14 and never 943.15 or the hold object's own "Deposit-all": the salvaging setup carries
a chisel, a rune pouch and coins, and a whole-inventory deposit banks them. A salvage-only
button removes that failure mode entirely rather than guarding against it.

NOTE the cargo hold's ITEM CONTAINER is NOT readable: TrawlingProvider polls inv 963..967
(verified as the correct ids) and MEASURED null for the whole session, with carryingCargo
stuck at 0, while this interface showed 12 items. So fullness comes from 943.4/943.5, read
while the interface is open -- which costs nothing, since the hold is opened to deposit anyway.
"""

import controller
import debug_log
import sailing

HOLD_GROUP = 943
SIDE_GROUP = 944

TITLE_CHILD = 1
USED_CHILD = 4
CAPACITY_CHILD = 5
GRID_CHILD = 10
DEPOSIT_SALVAGE_CHILD = 14

OPEN_TRIES = 3
OPEN_POLL_S = 0.35

# Interface clicks: never blocker-checked (an interface element is "behind a UI panel" by
# definition, and rotating the camera can never clear it), and never misclicked (a near-miss on
# a grid deposits or withdraws the neighbouring slot).
_CLICK_KWARGS = {"check_ui_blockers": False, "simulate_mistakes": False}


def is_open() -> bool:
    widget = controller.get_widget(HOLD_GROUP, TITLE_CHILD)
    return bool(widget.get("found")) and bool(widget.get("clickable"))


def _read_int(child):
    widget = controller.get_widget(HOLD_GROUP, child)
    text = (widget.get("text") or "").strip().replace(",", "")
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def read_usage():
    """(used, capacity) off 943.4/943.5, or (None, None) when unreadable.

    Unreadable means "cannot tell", NEVER "empty" -- a caller must not conclude the hold has
    room from a failed read. Same degrade-gracefully stance as hover_matches."""
    return _read_int(USED_CHILD), _read_int(CAPACITY_CHILD)


def salvage_rows():
    """Every salvage stack in the hold's grid (943.10), newest-first as the game orders them."""
    rows = []
    for child in controller.get_widget_children(HOLD_GROUP, GRID_CHILD):
        if (child.get("itemName") or "").lower().endswith("salvage"):
            rows.append(child)
    return rows


def _click(ctx, widget) -> bool:
    if not widget.get("clickable") or widget.get("screenX") is None:
        return False
    controller.click_entity(ctx.ser, widget, **_CLICK_KWARGS)
    return True


def open_hold(ctx) -> bool:
    """Click our own boat's cargo hold and confirm 943 opened."""
    if is_open():
        return True
    hold = sailing.find_facility("cargo hold", action="Open")
    if hold is None:
        debug_log.log("salvage", "no cargo hold on our own boat")
        return False
    controller.click_entity(ctx.ser, hold, offset=(0, 0), simulate_mistakes=False)
    for _ in range(OPEN_TRIES):
        controller.human_delay(OPEN_POLL_S, OPEN_POLL_S + 0.2)
        controller.dismiss_continue_box(ctx.ser)
        if is_open():
            return True
    debug_log.log("salvage", "the cargo hold did not open")
    return False


def deposit_salvage(ctx) -> bool:
    """Click 943.14 Deposit Salvage. Returns True only once no salvage remains in the
    inventory -- salvage LEFT BEHIND is the authoritative "the hold is full" signal, whatever
    943.4/943.5 say (the same rule deep_sea_trawling applies to a Collect-from that moved
    nothing, because a declared capacity is a ceiling, not a promise)."""
    if not is_open():
        return False
    if not _click(ctx, controller.get_widget(HOLD_GROUP, DEPOSIT_SALVAGE_CHILD)):
        debug_log.log("salvage", "Deposit Salvage button not clickable")
        return False
    controller.human_delay(0.35, 0.6)
    controller.dismiss_continue_box(ctx.ser)
    remaining = [i for i in controller.get_inventory()
                 if (i.get("name") or "").lower().endswith("salvage")]
    return not remaining


def withdraw_salvage(ctx) -> bool:
    """Withdraw-All the first salvage stack in the grid. Salvage does not stack in the
    inventory (MEASURED: 944 listed 14 separate Plundered salvage entries), so the game caps
    this at the free slot count on its own -- no quantity arithmetic needed."""
    if not is_open():
        return False
    rows = salvage_rows()
    if not rows:
        return False
    row = rows[0]
    # NOT **_CLICK_KWARGS: click_menu_action's signature is
    # (ser, entity, option, target_hint, offset, check_ui_blockers, refresh_target, refresh_legs)
    # -- it has no simulate_mistakes parameter (it never misclicks by construction), so splatting
    # the dict here is a TypeError.
    controller.click_menu_action(ctx.ser, row, "Withdraw-All", offset=(0, 0),
                                 check_ui_blockers=False)
    controller.human_delay(0.35, 0.6)
    controller.dismiss_continue_box(ctx.ser)
    return any((i.get("name") or "").lower().endswith("salvage")
               for i in controller.get_inventory())


def close_hold(ctx) -> bool:
    if not is_open():
        return True
    controller.key_tap(ctx.ser, "ESC")
    controller.human_delay(0.25, 0.4)
    if not is_open():
        return True
    _click(ctx, controller.get_widget(HOLD_GROUP, TITLE_CHILD))
    controller.human_delay(0.25, 0.4)
    return not is_open()
