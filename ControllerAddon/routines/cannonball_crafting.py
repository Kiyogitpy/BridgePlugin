# routines/cannonball_crafting.py
#
# Smelt steel bars into cannonballs at a furnace next to a bank, forever: withdraw a full
# inventory of Steel bar, click Smelt on the furnace, press Space on the dialog that comes up,
# let the whole batch run itself, bank when the bars are gone.
#
# Written 2026-08-25 from a hand-played recording (recordings/2026-08-25_083031_cannonball.*
# and 2026-08-25_083259_cannonball.*), the falconry.py pattern -- everything below marked
# MEASURED came out of those logs, so don't re-derive it by reasoning:
#
#   - MEASURED: 2 Steel bar -> 8 Steel cannonball, +51 Smithing, ONE PAIR EVERY 6.0s
#     (17.4s -> 23.4s -> 29.4s across three consecutive pairs). A 26-bar inventory is therefore
#     ~78s of smelting from a single click.
#   - MEASURED: Withdraw-All on Steel bar fills to 26 -- the Double ammo mould and the
#     cannonball stack take the other two slots. Cannonballs STACK, so the inventory can never
#     fill and there is nothing to deposit; the human never banked them and neither does this.
#   - MEASURED: a plain LEFT-click is enough for both objects. The human's whole session was 3
#     raw mouse clicks for 3 correct actions (Bank on the booth, Withdraw-All, Smelt on the
#     furnace) with no right-click menu anywhere, so "Smelt" is the furnace's left-click
#     default. click_menu_action is kept only as an escalation tier -- see click_smelt_via_menu.
#   - MEASURED: after the Smelt click the dialog appears on ARRIVAL (the human clicked from 8
#     tiles away and the dialog came up 5.6s later), Space starts the batch, and the batch then
#     runs all 13 pairs with no further input at all.
#   - MEASURED: the fast confirmation that Space landed is the chat line "You heat the steel
#     bars into a liquid state." at +0.4s. Smithing XP is NOT a useful confirmation here despite
#     CLAUDE.md's general advice -- it only lands at +4.6s, at the END of the pair, because the
#     XP is granted when the cannonballs come out of the mould, not when the bars go in.
#
# The one thing that needed designing rather than reading off the log:
#
#   - MEASURED: the player goes IDLE FOR ~1.5s IN THE MIDDLE OF EVERY PAIR (animation 899 ends
#     at 15.9s, animation 832 doesn't start until 17.4s). So `get_combat().isIdle` is NOT a
#     valid "the batch is finished" signal -- believing it would re-click the furnace mid-batch
#     and interrupt a smelt that was running fine, the same shape of destructive timeout
#     golem_crafting.py's craft_phase notes warn about. smelt_phase instead treats the batch as
#     alive while the BAR COUNT IS STILL DROPPING (see BATCH_STALL_S), which is the game's own
#     answer and survives the idle gap.
#
# No travel: the bank and the furnace were 8-9 tiles apart in the recording (Prifddinas), both
# on screen from either one. Objects are looked up by NAME rather than by the recorded ids
# (Furnace 36555, Bank booth 36559) so this also works at any other furnace-beside-a-bank.
#
# Run with:
#     python controller.py routines/cannonball_crafting.py

import time

import banker
import controller
import debug_log

DEFAULT_CONFIG = {
    "enabled": True,
    "barName": "Steel bar",
    "mouldName": "ammo mould",
    "furnaceName": "Furnace",
    # Consumed by banker.find_nearby_bank, which reads these two keys by name.
    "bankNames": ["bank booth", "bank chest", "bank counter"],
    "maxBankDistance": 20,
    "maxFurnaceDistance": 20,

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
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

# Smithing level for steel cannonballs (Dwarf Cannon also has to be complete, which nothing
# here can check).
SMITHING_LEVEL_REQUIRED = 35

# Distinctive fragment of "You heat the steel bars into a liquid state." -- the +0.4s
# confirmation that Space landed on the dialog. Matched on "liquid state" rather than the bar
# name so it doesn't quietly stop matching if the wording around the bar type ever differs.
SMELT_START_CHAT = "liquid state"

# A Double ammo mould consumes 2 bars per pour, a plain Ammo mould 1. Read off the inventory
# rather than configured, and used as the "can I still make anything" threshold -- a single
# leftover bar with a double mould is unsmeltable, and without this the routine would click the
# furnace at it forever instead of banking.
DOUBLE_MOULD_NAME = "double ammo mould"

# How long the bar count may sit unchanged before the batch is treated as interrupted rather
# than merely between pairs. MEASURED pair time is 6.0s; 9.0s clears the idle gap and a slow
# tick with margin, and the only cost of it being generous is a 9s wait on a genuine interrupt.
BATCH_STALL_S = 9.0

# Space is pressed on arrival, so this has to cover the walk. The recorded walk was 8 tiles /
# 5.6s from a standing start.
ARRIVAL_TIMEOUT_S = 12.0

# Space is speculative -- nothing here polls for the dialog widget -- so it gets a few attempts
# spaced far enough apart for the click to have registered server-side. A Space press with no
# dialog open does nothing, which is what makes this safe to repeat.
SPACE_PRESSES = 3
SPACE_DELAY = (0.8, 1.3)
SPACE_CONFIRM_S = 2.5
CONFIRM_POLL_S = 0.3

# Consecutive unconfirmed Smelt clicks before escalating from a bare left-click to picking
# "Smelt" out of the right-click menu -- the tier that beats something being drawn over the
# furnace, per CLAUDE.md's golem_crafting notes. Deliberately not the first tier: a bare click
# is what the human used and it costs no menu round-trip.
MENU_ESCALATION_AFTER = 3

# Consecutive ticks a required object may be missing from /objects before giving up. A miss is
# usually just a timed-out bulk scan (get_objects fails safe to an empty list), so one is never
# enough to act on; this many in a row means the routine was started somewhere without one.
MAX_LOOKUP_MISSES = 10
MAX_WITHDRAW_FAILURES = 3

REFRESH_LEGS = 3  # slayer.py's measured sweet spot for mid-flight re-aiming


def get_config(bridge_config):
    # Nothing in Controller Settings applies to this routine -- same as gemstone_crab.py /
    # mining.py, which is why there's no ControllerConfig.java section for it either.
    return DEFAULT_CONFIG


# --- pure helpers (no client, no serial -- unit-tested in tests/test_cannonball_crafting.py) --


def count_item(inventory, name) -> int:
    """Total quantity of `name` held, matched on the WHOLE item name, case-insensitively.

    Exact rather than substring on purpose: the two things this counts ("Steel bar", and
    implicitly not "Steel cannonball") are close enough in name that a substring match is an
    accident waiting to happen, and unlike firemaking.py's logName there's no legitimate case
    here for matching a family of items."""
    name_lower = (name or "").lower()
    total = 0
    for item in inventory:
        if (item.get("name") or "").lower() == name_lower:
            qty = item.get("qty")
            total += int(qty) if qty else 1
    return total


def find_item(inventory, name):
    """First item whose name CONTAINS `name` -- substring here, unlike count_item, because the
    callers are looking for a family ("ammo mould" matching "Double ammo mould")."""
    name_lower = (name or "").lower()
    return next(
        (i for i in inventory if name_lower in (i.get("name") or "").lower()),
        None,
    )


def bars_per_pour(inventory) -> int:
    """Bars consumed per pour, and so the fewest bars worth walking to the furnace with."""
    return 2 if find_item(inventory, DOUBLE_MOULD_NAME) is not None else 1


def is_smelt_start_message(message) -> bool:
    return SMELT_START_CHAT in (message or "").lower()


def batch_still_running(batch_running: bool, now: float, bars_changed_at: float) -> bool:
    """Whether to leave a seemingly-idle player alone rather than re-clicking the furnace.

    Pure so the reasoning is testable without a client. The player is genuinely idle for ~1.5s
    in the middle of every pour (see this file's header), so idleness alone means nothing; what
    means the batch is alive is the bar count having dropped recently."""
    if not batch_running:
        return False
    return (now - bars_changed_at) < BATCH_STALL_S


# --- phases ------------------------------------------------------------------------------


def tick(ctx):
    config = ctx.state.config or DEFAULT_CONFIG
    data = ctx.state.data

    if not data.get("cb_requirements_checked"):
        if not check_requirements(ctx, config):
            return
        data["cb_requirements_checked"] = True

    inventory = controller.get_inventory()
    bar_name = config.get("barName", "Steel bar")
    bars = count_item(inventory, bar_name)

    # Dispatch on the live inventory rather than a stored phase counter: ctx.state.data is wiped
    # whenever the routine file changes or the process restarts, while the bars in the pack are
    # not, so a counter desyncs on exactly the restart it would be there to survive. Same
    # reasoning falconry.py's bank trip dispatches on region.
    if bars < bars_per_pour(inventory):
        bank_phase(ctx, config, bar_name)
    else:
        smelt_phase(ctx, config, bar_name, bars)


def check_requirements(ctx, config) -> bool:
    """One-time-per-activation sanity check. Run from tick() rather than setup() for the reason
    firemaking.py's check_level_requirement documents: setup() runs before the engine's
    logged-in gate, so /skills there can read zeros on a perfectly fine account."""
    inventory = controller.get_inventory()
    mould_name = config.get("mouldName", "ammo mould")
    if find_item(inventory, mould_name) is None:
        # Not recoverable by banking -- bank_phase only ever withdraws bars, so carrying on
        # would just rediscover this forever.
        ctx.state.trigger_safety_stop(f"no {mould_name} in inventory")
        return False

    smithing = controller.get_skills().get("Smithing") or {}
    level = smithing.get("level")
    if level is not None and level < SMITHING_LEVEL_REQUIRED:
        ctx.state.trigger_safety_stop(
            f"Smithing level {level} is below the {SMITHING_LEVEL_REQUIRED} needed for cannonballs"
        )
        return False
    return True


def smelt_phase(ctx, config, bar_name, bars):
    data = ctx.state.data
    now = time.monotonic()

    if bars != data.get("cb_last_bars"):
        data["cb_last_bars"] = bars
        data["cb_bars_changed_at"] = now

    if not controller.get_combat().get("isIdle", True):
        return  # mid-animation, unambiguously busy

    if batch_still_running(data.get("cb_batch_running", False), now,
                            data.get("cb_bars_changed_at", now)):
        return  # the idle gap between two pours -- clicking here would interrupt the batch

    if data.get("cb_batch_running"):
        debug_log.log("cannonball", f"batch stalled with {bars} bars left -- restarting it")
    data["cb_batch_running"] = False

    furnace = find_furnace(config)
    if furnace is None:
        misses = data.get("cb_furnace_misses", 0) + 1
        data["cb_furnace_misses"] = misses
        if misses >= MAX_LOOKUP_MISSES:
            ctx.state.trigger_safety_stop(
                f"no {config.get('furnaceName', 'Furnace')} within "
                f"{config.get('maxFurnaceDistance', 20)} tiles"
            )
        return
    data["cb_furnace_misses"] = 0

    attempts = data.get("cb_smelt_attempts", 0)
    if attempts >= MENU_ESCALATION_AFTER:
        clicked = click_smelt_via_menu(ctx, furnace)
    else:
        clicked = click_smelt(ctx, furnace, config)

    if not clicked:
        data["cb_smelt_attempts"] = attempts + 1
        return

    if not await_smelt_started(ctx, bar_name, bars):
        data["cb_smelt_attempts"] = attempts + 1
        debug_log.log("cannonball",
                       f"smelt didn't start (attempt {attempts + 1}) -- retrying next tick")
        return

    data["cb_smelt_attempts"] = 0
    data["cb_batch_running"] = True
    data["cb_bars_changed_at"] = time.monotonic()
    debug_log.log("cannonball", f"batch running -- {bars} {bar_name.lower()}s to get through")


def find_furnace(config):
    name = (config.get("furnaceName") or "Furnace").lower()
    max_dist = config.get("maxFurnaceDistance", 20)

    best = None
    for obj in controller.get_objects(radius=max_dist):
        if (obj.get("name") or "").lower() != name:
            continue
        if not obj.get("visible", True):
            continue
        dist = obj.get("distance", 9999)
        if dist < 0 or dist > max_dist:
            continue
        if best is None or dist < best.get("distance", 9999):
            best = obj
    return best


def click_smelt(ctx, furnace, config) -> bool:
    """Bare left-click on the furnace -- MEASURED as resolving to Smelt (see this file's
    header). refresh_target + refresh_legs so the aim keeps correcting itself while the player
    walks there, CLAUDE.md's preferred pattern for a new click site."""
    x, y, plane = furnace.get("x"), furnace.get("y"), furnace.get("plane", 0)
    obj_id = furnace.get("id")

    refresh = None
    if None not in (x, y, obj_id):
        refresh = controller.object_refresh_target(
            x, y, plane, obj_id, radius=config.get("maxFurnaceDistance", 20)
        )

    debug_log.log("cannonball", f"clicking Smelt on the {furnace.get('name')}")
    return controller.click_entity(ctx.ser, furnace, refresh_target=refresh,
                                    refresh_legs=REFRESH_LEGS)


def click_smelt_via_menu(ctx, furnace) -> bool:
    """Escalation tier: right-click, read /menu, pick "Smelt" by name -- immune to whatever is
    drawn on top of the furnace's aim pixel, which is the failure a bare click cannot retry its
    way out of (golem_crafting.py's plinth hijacks, same shape).

    Re-resolves the object through /object/at immediately before clicking: click_menu_action has
    no refresh_target and right-clicks exactly the pixel it is handed, so a position from the
    /objects scan at the top of the tick is not safe to give it once the player has moved."""
    x, y, plane = furnace.get("x"), furnace.get("y"), furnace.get("plane", 0)
    obj_id = furnace.get("id")
    if None in (x, y, obj_id):
        return False

    controller.wait_until_player_idle(timeout_s=ARRIVAL_TIMEOUT_S)
    fresh = controller.get_object_at(x, y, plane, obj_id)
    if fresh is None:
        return False

    debug_log.log("cannonball", "escalating to the right-click menu for Smelt")
    # offset=(0, 0): aim at the reported centre, not the default (-7, +7) nudge.
    return controller.click_menu_action(ctx.ser, fresh, "Smelt", offset=(0, 0))


def await_smelt_started(ctx, bar_name, bars_before) -> bool:
    """Press Space on the smelt dialog once the player has arrived, and confirm the batch
    actually started.

    Space is speculative -- the dialog's widget id was deliberately not pinned, so nothing polls
    for it -- which is why this presses more than once: the first press can land before the
    interaction has resolved server-side. A Space press with no dialog open does nothing at all,
    so an extra one costs nothing but the delay in front of it."""
    controller.wait_until_player_idle(timeout_s=ARRIVAL_TIMEOUT_S)

    for _ in range(SPACE_PRESSES):
        controller.human_delay(*SPACE_DELAY)
        if ctx.events is not None:
            # wait_for_event does not consume what it matches and the buffer holds ~10 ticks, so
            # without this the "liquid state" line from the PREVIOUS pour satisfies the wait
            # below instantly and every failed press reads as a success.
            ctx.events.clear()
        controller.key_tap(ctx.ser, "SPACE")
        if smelt_confirmed(ctx, bar_name, bars_before, SPACE_CONFIRM_S):
            return True
    return False


def smelt_confirmed(ctx, bar_name, bars_before, timeout_s) -> bool:
    """Three independent proofs the batch started, in the order they arrive.

    The chat line is the fast one (+0.4s) but rides on UDP, which is lossy -- so it only ever
    accelerates, and the two HTTP checks behind it decide on their own if it never shows up,
    per CLAUDE.md's rule for ctx.events. Both fallbacks are slower than the chat line by
    construction: the animation has to start, or a whole pour has to finish."""
    deadline = time.monotonic() + timeout_s
    while True:
        if ctx.events is not None:
            seen = ctx.events.wait_for_event(
                "CHAT_MESSAGE",
                match=lambda payload: is_smelt_start_message(payload.get("message")),
                timeout_s=0.0,
            )
            if seen is not None:
                return True

        if not controller.get_combat().get("isIdle", True):
            return True

        if count_item(controller.get_inventory(), bar_name) < bars_before:
            return True

        if time.monotonic() >= deadline:
            return False
        time.sleep(CONFIRM_POLL_S)


def bank_phase(ctx, config, bar_name):
    data = ctx.state.data
    data["cb_batch_running"] = False

    bank_obj = banker.find_nearby_bank(config)
    if bank_obj is None:
        misses = data.get("cb_bank_misses", 0) + 1
        data["cb_bank_misses"] = misses
        if misses >= MAX_LOOKUP_MISSES:
            ctx.state.trigger_safety_stop(
                f"no bank within {config.get('maxBankDistance', 20)} tiles"
            )
        return
    data["cb_bank_misses"] = 0

    if not banker.open_bank(ctx, bank_obj):
        debug_log.log("cannonball", "couldn't open the bank -- retrying next tick")
        return

    # Checked before withdrawing rather than inferred from withdraw_all_shift's return value:
    # that returns False both for "the bank hasn't got any" and for "the click raced the bank
    # closing", and only the first of those is worth stopping the routine over.
    bar_lower = bar_name.lower()
    bank_items = controller.get_bank().get("items", [])
    if not any(bar_lower == (slot.get("name") or "").lower() for slot in bank_items):
        banker.close_bank(ctx)
        ctx.state.trigger_safety_stop(f"no {bar_name} left in the bank")
        return

    # Nothing is deposited: cannonballs stack into one slot, so the inventory never fills.
    if not banker.withdraw_all_shift(ctx, bar_name):
        failures = data.get("cb_withdraw_failures", 0) + 1
        data["cb_withdraw_failures"] = failures
        banker.close_bank(ctx)
        if failures >= MAX_WITHDRAW_FAILURES:
            ctx.state.trigger_safety_stop(f"couldn't withdraw {bar_name} after {failures} tries")
        return
    data["cb_withdraw_failures"] = 0

    banker.close_bank(ctx)
    controller.human_delay(0.4, 0.8)
