# routines/fletching.py
#
# Bank-standing fletching: withdraw a full inventory of logs, knife one of them, pick the
# product off the skillmulti menu, then AFK the whole inventory out tabbed away from the game
# before banking the results and going again.
#
# Written 2026-09-08 from ONE human recording -- recordings/2026-09-08_142953_fletching-bow.*
# (1m12s, 13 actions, a complete Seers' bank loop). Every id, widget and duration below came
# out of that log rather than out of reasoning; read its narrative before changing a budget.
# USER's own description of the loop, verbatim: "withdraw logs, use knife on any log select
# option 3 for longbow. tab out wait until all is compleated tab in and deposit bows. withdraw
# logs again. Default is maple".
#
# What the recording MEASURED:
#   - Bank booth 25808, player parked at (2727,3493) -- Seers'. Nothing walks; the human never
#     took a step, and this routine never needs a travel phase as long as it starts at a bank.
#   - Withdraw is ONE shift-click Withdraw-All on the logs (widget 12.12). It answers
#     "You don't have enough inventory space to withdraw that many." and withdraws 27 anyway --
#     that chat line is NORMAL here, not a failure, and nothing should treat it as one.
#   - Deposit is one shift-click Deposit-All per distinct name (widget 15.3), which is exactly
#     what banker.deposit_inventory already does with a keep list.
#   - Use knife -> log is two clicks in the inventory (WIDGET_TARGET then
#     WIDGET_TARGET_ON_WIDGET), the second only 0.5s after the first.
#   - The make menu is interface group 270, `skillmulti` (confirmed against the cache dump, and
#     the same group giants_foundry's crucible and the Gauntlet's singing bowl already drive).
#     Its item buttons are children 15..32 -- named `a`..`r` in the cache -- so the Nth option
#     is child 14+N. The human pressed the "3" number key for the longbow.
#
# What the FIRST LIVE RUN corrected (2026-09-08, logs/fletching_live2.log, and a dump of
# group 270 taken while the menu was actually open):
#   - The menu labels the FINISHED item and wraps it in colour tags:
#     `<col=ff9040>Maple longbow</col>`, not `Maple longbow (u)`. Matching the inventory name
#     raw could never have hit, so the routine fell back to the number key on every batch.
#     Hence strip_tags + menu_label. The five maple options, measured: `60 arrow shafts` (15),
#     `Maple shortbow` (16), `Maple longbow` (17), `Crossbow stock` (18), `Maple shield` (19)
#     -- confirming both the child = 14+N mapping and that "3" is right for the longbow.
#   - Child 14 carries the keyboard hints, and the middle option's reads "Space", not "3".
#     "3" works anyway (VERIFIED: 27 Maple longbow (u) banked from the first trip), so the
#     digit is an alternative binding rather than a wrong one.
#   - The logs are at bank slot 290 in this ~950-item bank, i.e. off-window, so a bare
#     shift-click is refused. See withdraw_logs.
#   - varp 2673 is `skillmulti_previousselection` (cache symbol) and read 2 after that press,
#     i.e. the game records the choice 0-INDEXED. Recorded here because it is a genuinely
#     useful signal, but NOT used: the bridge exposes /varbit, not /varp, and the inventory
#     answers the same question with no client rebuild.
#   - One product every ~1.8s (3 game ticks), 27 of them in ~48s, entirely unattended -- the
#     game keeps fletching with no further input at all.
#
# Deliberately NOT dependent on a client rebuild: every confirmation here is an inventory
# count. There is no fletchingXp on /player (checked live 2026-09-08 -- the field does not
# exist), and adding one would have made this routine wait on a Java change for no gain.
#
# Run with:
#     python controller.py routines/fletching.py

import random
import time

import banker
import controller
import debug_log
import skillmulti

DEFAULT_CONFIG = {
    "enabled": True,

    # USER: "Default is maple". `product` is what the skillmulti menu is matched on by NAME;
    # `productOption` is only the fallback number key for when that match fails, and it is the
    # human's own keypress (longbow is the 3rd option for a plain log).
    "logName": "Maple logs",
    "product": "Maple longbow (u)",
    "productOption": 3,
    "knifeName": "Knife",

    "maxBankDistance": 40,

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
        # OFF: a full inventory is the NORMAL state of this routine for ~48s out of every
        # minute -- it is an inventory of logs being fletched. check_safety runs before tick()
        # and would stop the run on its first pass. Same reasoning as gathering.py's.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

SKILLMULTI_GROUP = skillmulti.GROUP
SKILLMULTI_FIRST_ITEM_CHILD = skillmulti.FIRST_ITEM_CHILD
SKILLMULTI_LAST_ITEM_CHILD = skillmulti.LAST_ITEM_CHILD

MENU_OPEN_BUDGET_S = 4.0        # recording: menu up 0.7s after the use-on
FIRST_PRODUCT_BUDGET_S = 6.0    # recording: first product 1.3s after the option keypress
PRODUCT_STALL_S = 8.0           # recording: one product every 1.8s; 8s means it really stopped
FLETCH_MAX_S = 180.0            # a 27-log inventory is ~48s -- this is a ceiling, not a budget
SECONDS_PER_PRODUCT = 1.8       # measured

# The AFK half. Same primitives as routines/karambwan_fishing.py's (controller.alt_tab /
# wait_for_focus), but NOT the same numbers -- see below.
AFK_MIN_SECONDS = 12.0          # a near-finished inventory is not worth tabbing away for
AFK_POLL_S = 2.5                # how often a tabbed-out wait re-reads the bridge (no input)
AFK_MIN_POLL_S = 0.6            # ... tightened to one game tick as the batch nears its end

# USER 2026-09-08, watching run 2: "we are very slow tabbing back". MEASURED there, ~13s of
# dead time per batch: up to 2.5s to notice the batch had ended, 2-6s of "notice" delay, then
# 5s burned on one failed Alt+Tab before the retry.
#
# karambwan's numbers were lifted wholesale and they do not transfer, because the ACTIVITY's
# cadence is different -- the same lesson CLAUDE.md records for still_p90 ("compare the same
# activity, or the numbers mean nothing"). A karambwan inventory takes ~110s, so being 40s
# late costs a third of a trip; a fletching batch is ~48s, where the same 40s nearly doubles
# the cycle. So the lateness is rarer and shorter here. It is not removed: a return that is
# always instant is its own tell, and these stay tunable from config.
AFK_LATE_CHANCE = 0.12
AFK_LATE_RANGE_S = (6.0, 18.0)
AFK_PROMPT_RANGE_S = (1.0, 3.5)
AFK_FOCUS_TRIES = 3
# 2.0s, not 5.0: wait_for_focus polls /state every 0.3s and a successful Alt+Tab lands well
# inside a second, so a longer wait only makes FAILURE expensive -- it never buys a success.
AFK_FOCUS_WAIT_S = 2.0
AFK_UNFOCUS_WAIT_S = 2.0

BANK_OPEN_TRIES = 3
WITHDRAW_FAIL_LIMIT = 3  # failed withdrawals with logs visibly in the bank
START_FAIL_LIMIT = 4   # consecutive failed batch starts before a safety stop

# Test seams -- the suite installs a fake clock so budgets expire instantly (blast_furnace's
# _now/_sleep convention).
_now = time.monotonic
_sleep = time.sleep


def get_config(bridge_config):
    """This routine has no sidebar section of its own, so the bridge's generic config only ever
    fills in breaks/safety. Unlike gathering.py there is no "empty selection means idle" case:
    the log name is a plain default, not a panel selection."""
    merged = dict(DEFAULT_CONFIG)
    if bridge_config:
        merged.update(bridge_config)
    # The bridge may set the rest of the safety block, but stopOnInventoryFull is FORCED off
    # whatever it says -- see its comment in DEFAULT_CONFIG. Merging and letting the bridge win
    # would hand the engine a stop on this routine's normal working state (gathering.get_config
    # forces the same key off for the same reason).
    merged["safety"] = dict(DEFAULT_CONFIG["safety"], **(merged.get("safety") or {}))
    merged["safety"]["stopOnInventoryFull"] = False
    return merged


def _inv():
    return controller.get_inventory() or []


def count(inv, name):
    """Slot-count by exact name. Every item this routine counts (logs, unstrung bows) is
    non-stackable, so slots and quantity are the same number -- but qty is summed anyway so a
    stackable product (arrow shafts, which the same menu offers) counts correctly too."""
    name = (name or "").lower()
    return sum(int(i.get("qty") or 1) for i in inv
               if (i.get("name") or "").lower() == name)


def _find(inv, name):
    name = (name or "").lower()
    return next((i for i in inv if (i.get("name") or "").lower() == name), None)


# Aliases for the skillmulti module functions, so existing code continues to work unchanged.
strip_tags = skillmulti.strip_tags
menu_label = skillmulti.menu_label
menu_item_widget = skillmulti.item_widget
menu_option_number = skillmulti.option_number
menu_is_open = skillmulti.is_open


def _confirm(check, budget_s, poll_s=0.2):
    deadline = _now() + budget_s
    while _now() < deadline:
        if check():
            return True
        _sleep(poll_s)
    return check()


def notice_delay_s(rng=random):
    """How long after the fletching finishes before the player notices and tabs back in.
    karambwan_fishing's distribution, and USER's own 10-40s lateness."""
    lo, hi = AFK_LATE_RANGE_S if rng.random() < AFK_LATE_CHANCE else AFK_PROMPT_RANGE_S
    return rng.uniform(lo, hi)


def tab_out(ctx):
    """Alt+Tab away, CONFIRMED by /state going unfocused. False means we are still looking at
    the game (a machine with nothing else open swallows Alt+Tab entirely) -- the caller then
    waits focused rather than sitting there believing it is away."""
    controller.alt_tab(ctx.ser)
    if controller.wait_for_focus(timeout_s=AFK_UNFOCUS_WAIT_S, focused=False):
        return True
    debug_log.log("fletching", "Alt+Tab didn't unfocus the game -- fletching focused")
    return False


def tab_back_in(ctx):
    """Alt+Tab back, verified, AFK_FOCUS_TRIES times, then a safety stop.

    Not optional: the engine does not call tick() again while unfocused, so a tab back in that
    lands on some other window stalls the whole run silently with nothing in the log."""
    for attempt in range(AFK_FOCUS_TRIES):
        controller.alt_tab(ctx.ser)
        if controller.wait_for_focus(timeout_s=AFK_FOCUS_WAIT_S):
            return True
        debug_log.log("fletching", "tab-back-in attempt {0}/{1} didn't regain focus".format(
            attempt + 1, AFK_FOCUS_TRIES))
    ctx.state.trigger_safety_stop(
        "fletching: Alt+Tab back into RuneLite failed {0} times".format(AFK_FOCUS_TRIES))
    return False


def start_fletching(ctx, config):
    """Knife -> log -> pick the product off the skillmulti menu. True once a product has
    actually appeared in the inventory, so the caller knows the batch is running."""
    log_name = config["logName"]
    knife_name = config["knifeName"]
    product = config["product"]

    # With the bank still open the "inventory" is the bank's own side panel, and a click on
    # the knife there DEPOSITS it rather than selecting it -- one failed close_bank would
    # otherwise bank the knife and the whole withdrawal on the very next click. close_bank
    # confirms its own work but returns None either way, so the check belongs here.
    if banker.is_open():
        debug_log.log("fletching", "the bank is still open -- closing it before the use-on")
        banker.close_bank(ctx)
        if banker.is_open():
            debug_log.log("fletching", "the bank would not close -- retrying next tick")
            return False

    inv = _inv()
    knife = _find(inv, knife_name)
    log = _find(inv, log_name)
    if knife is None or log is None:
        debug_log.log("fletching",
                      "need both {0} and {1} in the inventory".format(knife_name, log_name))
        return False

    controller.open_inventory_tab(ctx.ser)
    before = count(inv, product)

    # Two inventory-interface clicks, so both opt out of the UI-blocker check and neither
    # simulates a misclick -- a miss beside the knife plus its corrective reclick would "use"
    # whatever sits in the neighbouring slot. banker.py's rule for every interface click.
    controller.click_entity(ctx.ser, knife, offset=(0, 0),
                            check_ui_blockers=False, simulate_mistakes=False)
    controller.click_entity(ctx.ser, log, offset=(0, 0),
                            check_ui_blockers=False, simulate_mistakes=False)

    if not _confirm(menu_is_open, MENU_OPEN_BUDGET_S):
        debug_log.log("fletching", "the make menu never opened -- the use-on didn't land")
        return False

    widget = menu_item_widget(product)
    option = menu_option_number(widget)
    if option is not None:
        # USER 2026-09-08: "why do we mouse click the bow? cant we just either use last craft
        # button space or 3 for longbow". Right -- MEASURED, the flight to that widget cost
        # ~1.7s per batch and a keypress is ~0.1s. The name match is still what CHOOSES the
        # option; it just supplies the position now instead of a click target, so this keeps
        # working when a different log type shifts the option list under a hardcoded number.
        # (Child 14 hints the middle option as "Space", but the digit works there too --
        # VERIFIED: 27 Maple longbow (u) came out of the run that pressed "3".)
        controller.key_tap(ctx.ser, str(option))
    elif widget is not None:
        # Matched by name but at a child we cannot turn into a key (outside 15..32, or past
        # the 9 the digit row can address) -- click it rather than guess a number.
        controller.click_entity(ctx.ser, widget, offset=(0, 0),
                                check_ui_blockers=False, simulate_mistakes=False)
    else:
        # The menu is up but carries no button matching the product name. Fall back to the
        # human's own keypress rather than giving up -- but say so, because a config naming a
        # product this log cannot make would look exactly like this every single tick.
        option = int(config.get("productOption") or 0)
        if option < 1:
            debug_log.log("fletching", "no '{0}' on the make menu and no productOption to fall "
                                       "back on".format(product))
            return False
        debug_log.log("fletching", "no '{0}' button on the make menu -- pressing {1} (check the "
                                   "product name for this log)".format(product, option))
        controller.key_tap(ctx.ser, str(option))

    started = _confirm(lambda: count(_inv(), product) > before, FIRST_PRODUCT_BUDGET_S)
    if not started:
        debug_log.log("fletching",
                      "no {0} after picking it -- the selection didn't take".format(product))
    return started


def _wait_out_batch(ctx, config, afk):
    """Poll until the logs run out, the products stop arriving, or FLETCH_MAX_S. Sends NO input
    while `afk` -- we are tabbed out, and a keypress would land in whatever window is in front.

    A stall is a normal outcome, not an error: a Fletching level-up interrupts the batch, and
    the engine's own check_stray_dialog clears that between ticks. Returning lets the next tick
    simply start the rest of the inventory again."""
    log_name = config["logName"]
    product = config["product"]

    deadline = _now() + FLETCH_MAX_S
    last_progress = _now()
    inv = _inv()
    seen = count(inv, product)
    left = count(inv, log_name)
    base = AFK_POLL_S if afk else min(AFK_POLL_S, 1.0)

    while _now() < deadline:
        if ctx.state.safety_stopped:
            return "stopped"
        # Poll rate tracks how much work is actually left. A flat 2.5s meant up to 2.5s of dead
        # time after the last log was consumed before the batch was even noticed to be over
        # (part of USER's "we are very slow tabbing back"); near the end this closes to one
        # game tick. Costs a handful of extra HTTP reads on the last few seconds of a batch and
        # nothing at all during the long middle.
        _sleep(max(AFK_MIN_POLL_S, min(base, left * SECONDS_PER_PRODUCT * 0.5)))
        inv = _inv()
        left = count(inv, log_name)
        if left == 0:
            return "done"
        made = count(inv, product)
        if made > seen:
            seen, last_progress = made, _now()
        elif _now() - last_progress >= PRODUCT_STALL_S:
            return "stalled"
        if not afk:
            controller.fidget_once()
    return "timeout"


def fletch_phase(ctx, config):
    """Start the batch, then wait it out -- tabbed away when the wait is long enough to be
    worth it. Returns False so the engine gets a tick back before the bank trip."""
    if not start_fletching(ctx, config):
        # A ceiling, not just a log line. A product name this log cannot make (or a menu whose
        # buttons stopped carrying names, so the fallback keypress picks the wrong row) fails
        # identically on every tick, and an unbounded retry with the inventory full of logs is
        # exactly the silent loop this codebase keeps paying for -- it looks like a healthy
        # routine in the log. Any successful start clears the count.
        fails = ctx.state.data.get("fl_start_fails", 0) + 1
        ctx.state.data["fl_start_fails"] = fails
        if fails >= START_FAIL_LIMIT:
            ctx.state.trigger_safety_stop(
                "fletching: could not start fletching {0} from {1} in {2} tries -- check that "
                "the product name is one this log can make".format(
                    config["product"], config["logName"], fails))
        return False
    ctx.state.data["fl_start_fails"] = 0

    remaining = count(_inv(), config["logName"])
    # ~1.8s per product, measured. Below AFK_MIN_SECONDS of work left, tabbing out and back
    # costs more than the wait is worth.
    afk = remaining * SECONDS_PER_PRODUCT >= AFK_MIN_SECONDS and tab_out(ctx)

    result = _wait_out_batch(ctx, config, afk)
    debug_log.log("fletching", "batch {0} ({1} {2} at the start)".format(
        result, remaining, config["logName"]))

    if afk:
        late = notice_delay_s()
        debug_log.log("fletching", "tabbing back in {0:.0f}s later".format(late))
        _sleep(late)
        tab_back_in(ctx)
    return False


def withdraw_logs(ctx, config):
    """Search the bank for the logs, then shift-click Withdraw-All on the result.

    Returns "ok", "empty" (the bank genuinely lists none) or "failed" (it lists them and the
    click did not land) -- three outcomes that need three different responses, and collapsing
    them is what made the first live run stop with 6,514 Maple logs in the bank.

    MEASURED live 2026-09-08, first run: a bare withdraw_all_shift was refused with "aim point
    is outside the RuneLite window". This account's bank holds ~950 item types and the logs sit
    at slot 290, so /bank reports a real but far-off-screen position for them -- the same thing
    banker.search_bank's own docstring records (positions up to y=7813 against a ~1400px
    window) and the reason giants_foundry searches before every withdraw. The human did the
    equivalent by hand in the recording, clicking through two bank tag tabs before the
    Withdraw-All; searching is the account-independent version of that.

    USER 2026-09-08: "if its on the screen we dont need to search". So the search is
    CONDITIONAL -- the logs are only searched for when the bank is not already showing them at
    a clickable position. On a tab where they are visible (the human's own setup) this costs
    nothing at all; the ~10 keystrokes are only paid when they would otherwise be a refused
    click. The test is controller.is_point_within_window, which is the very predicate
    click_entity uses to refuse the aim, so the two can never disagree."""
    name = config["logName"]

    def slot():
        # `qty` is the field /bank actually serves (confirmed live 2026-09-08 against the open
        # bank: id/name/qty/slot/screenX/screenY). Read fresh -- /bank is tick-cached and the
        # deposit that just ran can add an item type and shift every later slot.
        return next((s for s in (controller.get_bank(fresh=True).get("items") or [])
                     if (s.get("name") or "").lower() == name.lower()
                     and int(s.get("qty") or 0) > 0), None)

    here = slot()
    if here is not None and here.get("screenX") is not None and here.get("screenY") is not None \
            and controller.is_point_within_window(here["screenX"], here["screenY"]):
        return "ok" if banker.withdraw_all_shift(ctx, name) else "failed"

    if not banker.search_bank(ctx, name):
        debug_log.log("fletching", "could not search the bank for {0}".format(name))
        return "failed"

    found = slot()
    if found is None:
        # Only NOW is "the bank has none" a safe conclusion: before the search, an absent or
        # off-window entry means "not on the visible page", not "not in the bank". Collapsing
        # those is what stopped the first live run with 6,514 Maple logs banked.
        return "empty"

    ok = banker.withdraw_all_shift(ctx, name)
    # The filter is NOT cleared. USER 2026-09-08: "there is no need to click the search button
    # again after searching just pressing escape will exit the bank" -- and bank_phase closes
    # the bank on the very next line, so the one ESC ends both. (banker.clear_bank_search
    # exists for callers that keep banking after a search; this one does not.) A filter that
    # outlived the trip would be harmless here anyway: the deposit clicks INVENTORY slots,
    # which a bank filter does not touch, and the next withdrawal searches again.
    return "ok" if ok else "failed"


def bank_phase(ctx, config):
    """Deposit whatever was made and withdraw a fresh inventory of logs."""
    bank = banker.find_nearby_bank(config)
    if bank is None:
        ctx.state.trigger_safety_stop(
            "fletching: no bank within {0} tiles -- this routine fletches at a bank and does "
            "not travel".format(config.get("maxBankDistance", 40)))
        return

    if not banker.open_bank(ctx, bank):
        if banker.bank_pin_open():
            # Not a click failure and not worth retrying -- the booth click landed and opened
            # the PIN keypad. Say so, because "could not open Bank booth" sends you looking at
            # aim and geometry that were never wrong.
            # banker.open_bank answers the keypad itself (banker.enter_bank_pin), so reaching
            # here means that FAILED: no PIN stored for this machine, or a wrong one. Either
            # way retrying the booth cannot help.
            ctx.state.trigger_safety_stop(
                "fletching: the bank PIN keypad is up and could not be answered -- store the "
                "PIN on THIS machine with `python bank_pin.py --set`, or enter it by hand")
            return
        tries = ctx.state.data.get("fl_bank_tries", 0) + 1
        ctx.state.data["fl_bank_tries"] = tries
        if tries >= BANK_OPEN_TRIES:
            ctx.state.trigger_safety_stop(
                "fletching: could not open {0} in {1} tries".format(bank.get("name"), tries))
        else:
            debug_log.log("fletching", "could not open the bank -- retrying next tick")
        return
    ctx.state.data["fl_bank_tries"] = 0

    # keepItems is the knife alone; skipContainerDeposit because this routine carries no
    # container and the generic "empty every container" pass has nothing to do here.
    banker.deposit_inventory(ctx, {
        "keepItems": [config["knifeName"]],
        "skipContainerDeposit": True,
    })

    if count(_inv(), config["knifeName"]) == 0:
        debug_log.log("fletching", "no {0} -- withdrawing one".format(config["knifeName"]))
        banker.search_and_withdraw(ctx, config["knifeName"], 1)

    got = withdraw_logs(ctx, config)
    banker.close_bank(ctx)

    if got == "ok" and count(_inv(), config["logName"]) > 0:
        ctx.state.data["fl_withdraw_fails"] = 0
        return

    if got == "empty":
        # The bank running dry is the routine's natural end, and it is a stop rather than a
        # shrug: with no logs every later tick would re-open the bank forever.
        ctx.state.trigger_safety_stop(
            "fletching: no {0} left in the bank".format(config["logName"]))
        return

    # The bank HAS logs and the withdraw did not land -- a transient (a refused click, a bank
    # that redrew mid-trip). Retrying is right; retrying forever is not.
    fails = ctx.state.data.get("fl_withdraw_fails", 0) + 1
    ctx.state.data["fl_withdraw_fails"] = fails
    if fails >= WITHDRAW_FAIL_LIMIT:
        ctx.state.trigger_safety_stop(
            "fletching: {0} are in the bank but {1} withdrawals all failed".format(
                config["logName"], fails))
    else:
        debug_log.log("fletching", "the {0} withdrawal didn't land -- retrying next tick"
                                   .format(config["logName"]))


def tick(ctx):
    """Phase is derived from the live inventory every tick, never remembered -- so a restart
    part-way through a batch, or with a half-fletched inventory, resumes correctly."""
    config = ctx.state.config
    if count(_inv(), config["logName"]) > 0:
        fletch_phase(ctx, config)
    else:
        bank_phase(ctx, config)
