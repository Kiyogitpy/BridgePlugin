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
    943.14       Deposit SALVAGE          <- the only SALVAGE deposit this module ever clicks
    943.15       Deposit Inventory
    943.20..24   quantity buttons 1 / 5 / 10 / X / All
    944.1        the player's inventory side panel -- deposit_item's own grid (C2: the sink for
                 KEEPERS, since 943.14 only ever deposits salvage)

Why 943.14 and never 943.15 or the hold object's own "Deposit-all": the salvaging setup carries
a chisel, a rune pouch and coins, and a whole-inventory deposit banks them. A salvage-only
button removes that failure mode entirely rather than guarding against it.

NOTE the cargo hold's ITEM CONTAINER is NOT readable: TrawlingProvider polls inv 963..967
(verified as the correct ids) and MEASURED null for the whole session, with carryingCargo
stuck at 0, while this interface showed 12 items. So fullness comes from 943.4/943.5, read
while the interface is open -- which costs nothing, since the hold is opened to deposit anyway.
"""

import time

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
SIDE_INVENTORY_CHILD = 1   # 944.1 -- deposit_item's own grid, see the module docstring

OPEN_TRIES = 3
OPEN_POLL_S = 0.35

# I7 (final-review fix wave, 2026-09-07): deposit_salvage/withdraw_salvage used to sleep
# 0.35-0.6s -- under one game tick at the low end -- then read /inventory exactly ONCE. An early
# read on a deposit/withdraw that was genuinely still landing made deposit_salvage return False,
# which stow_phase turns straight into forced_full=True, latching a near-empty hold as full for
# the rest of the session (withdraw_salvage has the mirror bug against hold_has_salvage). Poll to
# a short budget instead of trusting one read -- the same deadline-and-poll pattern
# shipwreck_salvaging._await_sort_done already uses for the sort channel itself. ~2s is generous
# against a single-tick UI action; referenced as a bare module global inside _poll_until (never
# bound as a default-argument value) so a test can shrink it with a plain monkeypatch.
HOLD_ACTION_POLL_S = 0.25
HOLD_ACTION_BUDGET_S = 2.0

# Interface clicks: never blocker-checked (an interface element is "behind a UI panel" by
# definition, and rotating the camera can never clear it), and never misclicked (a near-miss on
# a grid deposits or withdraws the neighbouring slot).
_CLICK_KWARGS = {"check_ui_blockers": False, "simulate_mistakes": False}


class _HoldUnknown:
    """Falsy sentinel returned by deposit_salvage: the interface was never open, or the Deposit
    Salvage button never registered a click -- "cannot tell", never "the hold is full". Distinct
    from a real `False` (the interface WAS open, the click landed, and salvage is still in the
    inventory -- genuinely full) so a caller can't fold "we don't know" into "no room" the way
    stow_phase's forced_full used to (I7). Same falsy-sentinel idiom as sailing.ALL_SKIPPED /
    controller.CLICK_WITHHELD."""

    __slots__ = ()

    def __bool__(self):
        return False

    def __repr__(self):
        return "HOLD_UNKNOWN"


HOLD_UNKNOWN = _HoldUnknown()


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
    # Minor fix (final review 2026-09-07): screenY was never checked, only screenX -- a widget
    # reporting one but not the other (a malformed/partial read) would still be clicked at
    # whatever garbage screenY happened to be in the dict.
    if (not widget.get("clickable") or widget.get("screenX") is None
            or widget.get("screenY") is None):
        return False
    controller.click_entity(ctx.ser, widget, **_CLICK_KWARGS)
    return True


def _poll_until(predicate, poll_s=HOLD_ACTION_POLL_S) -> bool:
    """Poll `predicate` (no args) until it returns True or HOLD_ACTION_BUDGET_S elapses.

    HOLD_ACTION_BUDGET_S is looked up as a bare module global INSIDE this function, never bound
    as a default-argument value -- default arguments are evaluated once at def time, which would
    freeze in the value at import and make it un-patchable by a test."""
    deadline = time.time() + HOLD_ACTION_BUDGET_S
    while True:
        if predicate():
            return True
        if time.time() >= deadline:
            return False
        controller.human_delay(poll_s, poll_s + 0.1)


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


def deposit_salvage(ctx):
    """Click 943.14 Deposit Salvage, then POLL (I7) rather than sleep-then-read-once until no
    salvage remains in the inventory -- salvage LEFT BEHIND is the authoritative "the hold is
    full" signal, whatever 943.4/943.5 say (the same rule deep_sea_trawling applies to a
    Collect-from that moved nothing, because a declared capacity is a ceiling, not a promise).

    Three-way return, not a plain bool:
      True         -- deposited; no salvage left in the inventory.
      False        -- the interface WAS open and the click DID land, but salvage is still in the
                       inventory after the poll budget -- genuinely full.
      HOLD_UNKNOWN -- the interface was never open, or the button never registered a click --
                       "cannot tell", not "full". Callers (see stow_phase) must not fold this
                       into a forced_full the way a real False does (I7)."""
    if not is_open():
        return HOLD_UNKNOWN
    if not _click(ctx, controller.get_widget(HOLD_GROUP, DEPOSIT_SALVAGE_CHILD)):
        debug_log.log("salvage", "Deposit Salvage button not clickable")
        return HOLD_UNKNOWN

    def _no_salvage_left():
        controller.dismiss_continue_box(ctx.ser)
        return not any((i.get("name") or "").lower().endswith("salvage")
                       for i in controller.get_inventory())

    if _poll_until(_no_salvage_left):
        return True
    debug_log.log("salvage", f"salvage still in the inventory {HOLD_ACTION_BUDGET_S:.1f}s "
                              f"after Deposit Salvage -- treating the hold as full")
    return False


def withdraw_salvage(ctx) -> bool:
    """Withdraw-All the first salvage stack in the grid, POLLING (I7) rather than
    sleep-then-read-once for it to arrive. Salvage does not stack in the inventory (MEASURED:
    944 listed 14 separate Plundered salvage entries), so the game caps this at the free slot
    count on its own -- no quantity arithmetic needed."""
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

    def _salvage_arrived():
        controller.dismiss_continue_box(ctx.ser)
        return any((i.get("name") or "").lower().endswith("salvage")
                   for i in controller.get_inventory())

    return _poll_until(_salvage_arrived)


def deposit_item(ctx, name: str) -> bool:
    """C2 (final-review fix wave, 2026-09-07): stow one KEEPER item into the already-open hold.
    943.14 (deposit_salvage) only ever deposits SALVAGE, so this is the sole sink for anything
    the keep-list selects -- without it a long run fills the inventory with sorted output and
    deadlocks (see shipwreck_salvaging.stow_keepers, which is the only caller and is what
    excludes HARD_KEEP before ever reaching here).

    Requires 943 already open -- same convention as deposit_salvage/withdraw_salvage; the caller
    opens/closes the hold once for a whole batch of keepers rather than per item.

    Matches `name` EXACT (case-insensitive) against the SIDE panel's (944.1) itemName -- not a
    substring, so this can never pick up a different item that merely shares a keepItems
    substring with `name`. Returns True only once `name`'s count in /inventory actually
    dropped -- the same "declared state is a ceiling, not a promise" rule deposit_salvage/
    withdraw_salvage already use."""
    if not is_open():
        return False
    target = (name or "").lower()
    if not target:
        return False

    entry = None
    for child in controller.get_widget_children(SIDE_GROUP, SIDE_INVENTORY_CHILD):
        if (child.get("itemName") or "").lower() == target:
            entry = child
            break
    if entry is None:
        return False

    before = sum(1 for i in controller.get_inventory()
                if (i.get("name") or "").lower() == target)
    if before == 0:
        return False

    # NOT **_CLICK_KWARGS -- see withdraw_salvage's own comment: click_menu_action has no
    # simulate_mistakes parameter, and splatting the dict here is a runtime TypeError.
    # ("Deposit-All", "Deposit") -- NOT "Deposit-All" alone. CONFIRMED LIVE 2026-09-08:
    #     [!] No 'Deposit-All' entry in menu for Diamond bracelet (['Cancel', 'Examine', 'Deposit'])
    # A STACKED item in the 944 side panel offers Deposit-All/-1/-5/-10/-X, but a single
    # non-stackable one offers only "Deposit". The widget capture that this module was built from
    # showed exactly that asymmetry (stacked Plundered salvage with the full set, a lone Rune
    # scimitar with just ['Deposit', 'Examine']) and the first implementation still asked only for
    # Deposit-All -- so the keeper sink could not stow any single item, which is most of them.
    # click_menu_action takes a sequence and matches whichever entry is present.
    controller.click_menu_action(ctx.ser, entry, ("Deposit-All", "Deposit"), offset=(0, 0),
                                 check_ui_blockers=False)

    def _count_dropped():
        controller.dismiss_continue_box(ctx.ser)
        after = sum(1 for i in controller.get_inventory()
                    if (i.get("name") or "").lower() == target)
        return after < before

    return _poll_until(_count_dropped)


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
