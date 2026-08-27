# routines/plank_make.py
#
# Cast Plank Make on a full inventory of logs at a bank, bank the planks, withdraw more,
# forever. Standing still the whole time -- no travel, no objects, no NPCs except the banker.
#
# Written 2026-08-26 from a hand-played recording (recordings/2026-08-26_182410_plankmake2.*),
# the falconry.py/cannonball_crafting.py pattern. Everything marked MEASURED came out of that
# log -- do not re-derive it by reasoning:
#
#   - MEASURED: Plank Make is a 3-TICK spell. 25 casts, item-click ticks 8062 -> 8133, the
#     diffs are 3 with only clock-jitter exceptions (a 4 always followed by a 2). Mean gap
#     1.79s against a theoretical 1.80s, so the recorded human was already at the cap. There is
#     no faster; "no wasted ticks" here means hitting 3 every time, never 4.
#   - MEASURED: THE GAME QUEUES THE CAST. Magic XP lands a consistent +0.8-1.3s AFTER the item
#     click (~1.5 ticks) while the cadence stays 3 ticks. The human was clicking during the
#     cooldown and the server executed on schedule. This is what the whole loop below is built
#     on: the pair is fired EARLY, into the cooldown, and the XP that confirms it is also the
#     clock for the next one.
#   - MEASURED: both clicks are the SAME PIXEL. The spell (widget 218.138) and the target
#     inventory slot (149.0 index 24) were both at canvas (1034,602), 24 times running --
#     selecting an item-target spell swaps the client to the inventory tab, and completing the
#     cast swaps it back to the spellbook, so a log parked under the spell icon needs no mouse
#     movement between the two clicks at all. choose_target_slot reproduces that by always
#     picking the log nearest the spell icon.
#   - MEASURED (recording, and re-confirmed live 2026-08-26): the plank replaces the LOWEST log
#     slot, never the clicked one. So the log nearest the spell icon is the LAST one to convert
#     and one aim point serves a whole inventory. The routine re-validates that slot against the
#     live inventory every cast anyway, and drops the aim on any failure, so it does not depend
#     on this staying true.
#   - MEASURED: 350gp and 90 Magic xp per teak plank; 25 logs per trip (28 slots less coins,
#     magic potion and rune pouch). "You don't have enough inventory space to withdraw that
#     many." on the Withdraw-All is normal and expected, not an error.
#   - MEASURED: the whole bank trip is 4 ticks -- Bank on the banker (8137), Deposit-All
#     (8140), Withdraw-All (8144), Escape. The recorded logs sat at bank slot 443, far enough
#     down the list to report a real but far-off-screen screenY, which is why the bank search
#     is kept as a fallback -- but only as a fallback. See withdraw_logs.
#
# The one thing that needed designing rather than reading off the log: WHEN to fire the pair.
# Firing the instant the previous cast's XP arrives would put the click ~2.25 ticks ahead of
# when the game can act on it, and nothing in the recording says a click that early is still
# honoured. CAST_LEAD_TICKS instead waits one tick boundary first, which lands the pair
# ~1.25-1.75 ticks early -- the phase the human actually demonstrated 25 times. See that
# constant for how to tune it if a live log ever shows 4-tick casts.
#
# MEASURED LIVE 2026-08-26 (plank4.log, two consecutive cycles, identical to 0.01s):
#
#     52.81s per load of 26 casts = 2.031 s/cast = 88.6% of the absolute 3-tick ceiling.
#     Cast-to-cast median 1.768s against a 1.800s cap; 72 casts, 0 failures, 0 misclicks.
#
# The 11% that is not casting is the bank trip, and the recorded human's was slightly SLOWER
# (their last-cast-to-first-cast was 8.5s against 7.8s here). Casting itself has ~0.03s/cast of
# headroom left; everything worth reclaiming has been. Three things got it there, in descending
# order of what they were worth:
#
#   1. Not moving the mouse. USER-DIRECTED. The overlap pixel is clicked twice per cast and the
#      cursor does not move again for the whole inventory -- see cast_in_place.
#   2. Draining into the bank inside the same tick() call, worth 1.9s a load. See tick().
#   3. Skipping the bank search whenever the logs are already on screen, worth ~2.5s a load.
#      See withdraw_logs.
#
# NOTE, a real consequence of (1): because most casts never call click_entity, click_entity's
# simulated misclick no longer fires during casting -- 0 in 72 casts where ~2 were expected.
# The humanisation that remains for this activity is the click timing, the once-per-load mouse
# flight, the idle-mouse drift between loads, and breaks.
#
# Run with:
#     python controller.py routines/plank_make.py

import time

import banker
import controller
import debug_log

DEFAULT_CONFIG = {
    "enabled": True,

    "logName": "Teak logs",
    "plankName": "Teak plank",
    # Coins per cast. Only used to stop cleanly before the coins run out mid-inventory --
    # nothing depends on it being exactly right.
    "costPerPlank": 350,

    # Plank Make on the Lunar spellbook, read straight out of the recording's decoded
    # group.child. A different spell (or a different client layout) only needs this changed.
    "spellWidget": [218, 138],
    # Whatever opens the Magic tab. F6 is the game's own default and is what the human pressed.
    "magicTabKey": "F6",

    # Runes the spell needs from the pouch, checked before each load. Earth runes are supplied
    # by the staff in the recorded setup, so they are deliberately not listed -- add them here if
    # a loadout ever carries them instead.
    "runeNames": ["Astral", "Nature"],

    "bankerNames": ["Banker"],
    "maxBankerDistance": 12,
    # Substring-matched against inventory item names and never deposited. The rune pouch has to
    # be here for the obvious reason, the magic potion because it is a boost this activity may
    # depend on, and the coins because they ARE the raw material.
    "keepItems": ["coins", "magic potion", "rune pouch"],

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
        # MUST stay False. The inventory is full by design for the entire casting phase (25
        # logs + coins + potion + pouch = 28/28), so the engine's generic inventory-full check
        # would safety-stop this routine on its first tick.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

# How long to wait for the Magic XP that proves a cast resolved. MEASURED click->XP is
# 0.8-1.3s; the pair is fired up to ~1.75 ticks before the game can act on it, so the real
# worst case is nearer 2 ticks of queueing plus the cast itself. 4 ticks covers that with
# margin. A timeout is not destructive here (unlike golem_crafting's carve timeout) -- the
# recovery is another pair of clicks, which the game just queues.
CAST_CONFIRM_TIMEOUT_S = 2.4

# Tick boundaries to wait after the confirming XP before firing the next pair.
#
# The XP lands ON the tick the cast resolved, and the next cast can resolve 3 ticks later. With
# lead 1, the boundary plus five short HTTP reads and two mouse flights puts the pair an
# ESTIMATED ~1.2 ticks ahead of that deadline (the human's MEASURED phase was ~1.5) -- close
# enough to the demonstrated behaviour, with the whole margin still on the safe side. Lead 0
# would fire ~2.2 ticks ahead: no faster, since the cadence is capped by the spell and not by
# us, and it bets on a click that early still being queued, which nothing here has measured.
#
# If a live log ever shows casts landing 4 ticks apart, the pair is arriving too LATE -- lower
# this to 0 rather than raising it.
CAST_LEAD_TICKS = 1

# Consecutive casts that can fail to produce Magic XP before this is treated as broken rather
# than unlucky. click_entity's simulated misclick (settings.MISTAKE_MISCLICK_CHANCE) is the
# expected cause of a single failure: a miss on the log slot lands on a neighbouring slot,
# casts on something that is not a log, and its automatic corrective re-click then does nothing
# because the spell is no longer selected. That costs one cycle and the next attempt is clean.
# Several in a row is a different problem (out of runes, spellbook switched, interface moved),
# and spinning on it unattended is worse than stopping.
MAX_CONSECUTIVE_CAST_FAILURES = 5

# Poll interval for the HTTP confirmation fallback, used only when ctx.events is None.
XP_POLL_INTERVAL_S = 0.1

# Pause between the two clicks of an in-place cast. MEASURED: the recorded human's own gap
# between selecting the spell and clicking the log was 0.2-0.4s. Anything much shorter risks
# the client coalescing them, and there is ~900ms of slack in the cycle, so there is nothing to
# win by going faster.
IN_PLACE_CLICK_GAP = (0.18, 0.34)

# How far the cursor may sit from the aim pixel and still be clicked in place rather than
# re-flown to. See cursor_on_aim: the overlap band it lives in the middle of is ~50x28px.
CURSOR_DRIFT_PX = 12

# How long the deposited planks may take to actually leave the inventory. CONFIRMED live
# 2026-08-26: a single read 0.5s after the shift-click still saw all 26 planks and reported a
# failed deposit, when in fact the click had landed and the server processed it a moment later.
# The withdrawal must not go ahead on a half-emptied inventory, but neither may a race be
# mistaken for a failure -- so this polls instead of reading once.
DEPOSIT_SETTLE_S = 2.5

# Fragments of the game's own refusal when a cast is declined for want of runes. Matched
# tolerantly against the last chat line, because the exact wording is not recorded anywhere in
# this repo and guessing it precisely would be worse than matching loosely: a false negative just
# falls through to the existing MAX_CONSECUTIVE_CAST_FAILURES stop, which is the same outcome one
# cycle later. The stop reason quotes the message verbatim, so the first real occurrence in a log
# is both the proof and the way to tighten this.
RUNE_SHORTAGE_HINTS = ("do not have enough", "don't have enough", "need more",
                       "not enough", "run out of")

BANK_OPEN_TIMEOUT_S = 15.0
BANK_CLOSE_TIMEOUT_S = 3.0


def get_config(bridge_config):
    # No Controller Settings panel for this one (same as gemstone_crab.py/pest_control.py) --
    # nothing here needs to be tuned from inside the client.
    return DEFAULT_CONFIG


# --- inventory helpers --------------------------------------------------------------------


def count_item(inventory, name):
    """Total units of `name` held: quantity for a stack, one per slot for anything that does
    not stack.

    Exact name match, not substring -- "Teak logs" and "Teak plank" share a prefix and a
    substring match would conflate them the moment the first plank exists. Counting quantity
    rather than distinct names is the falconry.py lesson: 11 Bones is 11 slots, not 1."""
    wanted = name.lower()
    return sum(item.get("qty", 1) for item in inventory
               if (item.get("name") or "").lower() == wanted)


def _distance(item, spell):
    dx = item.get("screenX", 0) - spell.get("screenX", 0)
    dy = item.get("screenY", 0) - spell.get("screenY", 0)
    return (dx * dx + dy * dy) ** 0.5


def choose_target_slot(inventory, log_name, spell):
    """The log slot to cast on: whichever one sits closest to the spell icon on screen.

    That is the whole trick from the recording. The client swaps to the inventory tab when the
    spell is selected, so the spell icon and the inventory slots occupy the same panel; a log
    directly under the icon turns the cast into two clicks at one pixel with no mouse flight
    between them. Anything else works too, just slower.

    Slots with no screenX are skipped rather than clicked at a guessed position -- the absence
    means the inventory panel is not currently drawn, and firing the mouse at a stale
    coordinate is how this codebase has broken clicks before."""
    wanted = log_name.lower()
    candidates = [item for item in inventory
                  if (item.get("name") or "").lower() == wanted
                  and item.get("screenX") is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda item: _distance(item, spell))


def slot_pitch(inventory):
    """Centre-to-centre spacing of adjacent inventory slots, in screen pixels, or None.

    Measured off the live slots rather than derived from a widget's reported width, because
    /widget mixes the two pixel spaces CLAUDE.md warns about: screenX/screenY come back scaled
    (canvasToScreen, which applies the stretch factor) while width/height stay in unscaled
    canvas units. MEASURED live 2026-08-26: a slot reports 36x32 but actually sits on an 86x74
    grid, a 2.06x difference. Comparing the reported size against a screen distance would be
    wrong by exactly that factor, so nothing here uses the reported size at all."""
    points = [(item.get("screenX"), item.get("screenY")) for item in inventory
              if item.get("screenX") is not None]
    gaps = [abs(a - b)
            for values in (set(p[0] for p in points), set(p[1] for p in points))
            for a in values for b in values if a != b]
    return min(gaps) if gaps else None


def in_place_aim(spell, target, inventory):
    """One pixel that hits the spell icon in the Magic tab AND the log slot in the Inventory
    tab, or None if the two are too far apart to share one.

    This is the whole point of the activity's layout, and it is what the recorded human was
    doing: selecting an item-target spell swaps the client to the inventory tab, so a single
    hover can take both clicks -- MEASURED, the human's two clicks were the same canvas pixel
    24 times running. USER-CONFIRMED 2026-08-26 that the overlap is real and does not need
    proving; the guard below only exists so a layout where it is NOT true degrades to two
    ordinary clicks instead of clicking somewhere useless.

    Midpoint of the two centres, accepted only when they are within half a slot pitch. That
    puts the aim within a quarter-pitch of each centre, which is inside both boxes for any
    icon at least half a slot tall -- no absolute sizes needed, so nothing here depends on the
    stretch factor or on a hardcoded OSRS grid constant."""
    pitch = slot_pitch(inventory)
    if pitch is None or _distance(target, spell) > pitch / 2.0:
        return None
    return (int(round((spell["screenX"] + target["screenX"]) / 2.0)),
            int(round((spell["screenY"] + target["screenY"]) / 2.0)))


def find_banker(npcs, config):
    """Nearest NPC matching one of `bankerNames` within range, or None.

    Matched by name because /npcs carries no action list -- there is no "has a Bank option"
    flag to filter on."""
    names = {n.lower() for n in config.get("bankerNames", DEFAULT_CONFIG["bankerNames"])}
    max_dist = config.get("maxBankerDistance", DEFAULT_CONFIG["maxBankerDistance"])

    candidates = [npc for npc in npcs
                  if (npc.get("name") or "").lower() in names
                  and 0 <= npc.get("distance", 9999) <= max_dist]
    if not candidates:
        return None
    return min(candidates, key=lambda npc: npc.get("distance", 9999))


# --- the engine entry point ---------------------------------------------------------------


def tick(ctx):
    config = ctx.state.config or DEFAULT_CONFIG
    log_name = config.get("logName", DEFAULT_CONFIG["logName"])

    # An interface left open by a restart mid-trip has to win over everything else: casting
    # into an open bank clicks the bank's own widgets, not the spellbook.
    if banker.is_open():
        bank_phase(ctx, config)
        return

    inventory = controller.get_inventory()

    cost = config.get("costPerPlank", DEFAULT_CONFIG["costPerPlank"])
    coins = count_item(inventory, "Coins")
    if coins < cost:
        # Not recoverable by banking -- bank_phase only ever withdraws logs, so carrying on
        # would just rediscover this every trip.
        ctx.state.trigger_safety_stop(f"out of coins ({coins}gp, a cast costs {cost}gp)")
        return

    # Proactive counterpart to rune_shortage's failed-cast detection, so a run ends with a clear
    # reason instead of a wasted cast and a generic no-XP stop.
    empty = empty_runes(config)
    if empty:
        ctx.state.trigger_safety_stop(f"out of {empty} runes")
        return

    if count_item(inventory, log_name):
        cast_phase(ctx, config, inventory)
        if ctx.state.safety_stopped:
            return
        inventory = controller.get_inventory()
        if count_item(inventory, log_name):
            # cast_phase stopped for a reason other than running dry (lost focus, a retry
            # budget) -- banking a half-converted inventory is not the recovery for any of them.
            return

    # Same tick() call, deliberately. MEASURED live 2026-08-26: returning here and letting the
    # engine dispatch bank_phase on its next pass cost 3.17s of dead time between the last cast
    # and the deposit -- the stray-dialog check, the idle-mouse roll, a 15% micro-break and the
    # poll sleep, all landing in the one gap where the character is standing at a bank with a
    # full inventory and nothing to do. Draining straight into the bank removes one of the two
    # round trips per cycle; the one after the withdrawal is kept, so breaks and safety checks
    # still get a look in once per load.
    bank_phase(ctx, config)


# --- casting ------------------------------------------------------------------------------


def cast_phase(ctx, config, inventory):
    """Cast until the logs run out, then hand back so the engine can bank on the next tick.

    Deliberately an inner loop rather than one cast per tick(). controller.main() runs a
    stray-dialog check, an idle-mouse roll, a 15% micro-break and its own poll sleep between
    tick() calls -- easily a second of overhead, which on a 1.8s cadence would mean missing
    every other cast window. The loop still re-reads the inventory and the window focus every
    cycle, so it is not blind to anything the engine would have caught; it just checks those
    itself instead of paying the whole between-tick cost.

    A full inventory is ~45s, which is also how long a break can be deferred by staying in
    here. That is fine and is in fact the tidiest place to break: between loads, holding no
    half-converted inventory."""
    log_name = config.get("logName", DEFAULT_CONFIG["logName"])
    cost = config.get("costPerPlank", DEFAULT_CONFIG["costPerPlank"])
    failures = 0
    casts = 0

    while True:
        if ctx.state.safety_stopped:
            return

        logs = count_item(inventory, log_name)
        if logs == 0:
            if casts:
                debug_log.log("plank", f"inventory converted -- {casts} casts, banking")
            return

        coins = count_item(inventory, "Coins")
        if coins < cost:
            ctx.state.trigger_safety_stop(f"out of coins ({coins}gp, a cast costs {cost}gp)")
            return

        # The engine's own focus gate only runs between tick() calls, and this loop deliberately
        # stays inside one for a whole inventory. Blind-clicking into a backgrounded window is
        # exactly what that gate exists to prevent, so the loop re-checks it itself.
        if not controller.get_state().get("windowFocused", True):
            debug_log.log("plank", "window lost focus -- pausing")
            return

        if cast_once(ctx, config, inventory):
            casts += 1
            failures = 0
            # Only realign after a cast that actually resolved: the confirming XP is the tick
            # anchor, and there is no anchor to align to after a failure.
            wait_for_cast_window(ctx)
        else:
            failures += 1
            # The single-pixel aim is the most likely thing to be wrong when a cast produces
            # nothing, and re-learning it costs one ordinary two-click cast. Cheap insurance
            # against a layout where the overlap assumption does not hold.
            ctx.state.data.pop("pm_aim", None)

            # Out of runes is the one cast failure worth stopping on IMMEDIATELY rather than
            # spending the retry budget: no amount of re-clicking makes a rune appear, and the
            # generic "N casts produced no Magic XP" stop tells whoever reads the log nothing
            # about why. USER-DIRECTED 2026-08-27: detect it from a failed cast rather than by
            # reading the rune pouch up front.
            shortage = rune_shortage()
            if shortage:
                ctx.state.trigger_safety_stop(f"out of runes -- the game said: {shortage}")
                return

            if failures >= MAX_CONSECUTIVE_CAST_FAILURES:
                ctx.state.trigger_safety_stop(
                    f"{failures} casts in a row produced no Magic XP")
                return
            debug_log.log("plank", f"cast produced no Magic XP -- retrying ({failures})")

        inventory = controller.get_inventory()


def cast_once(ctx, config, inventory):
    """One Plank Make cast, confirmed on Magic XP.

    Two routes. The fast one is what the human actually did: both clicks on a single pixel
    where the spell icon and the log slot overlap, so the mouse never moves between them. It
    needs an aim point, which only the slow route can work out -- an inventory slot reports a
    screen position only while its panel is drawn, and the Magic tab is what is showing before
    the first click. So the first cast of each load goes the slow way (click spell, read the
    now-visible inventory, click the log) and records the aim point for the rest of the load.

    MEASURED live 2026-08-26: the plank replaces the LOWEST log slot, never the clicked one, so
    the slot nearest the spell is the last one to convert -- the cached aim survives an entire
    inventory. It is still re-validated against the live inventory every cast rather than
    trusted, and dropped on any failure."""
    aim = cached_aim(ctx, inventory, config)
    if aim is not None:
        return cast_in_place(ctx, config, aim)
    return cast_and_learn_aim(ctx, config)


def cached_aim(ctx, inventory, config):
    """The stored single-pixel aim, if the slot it was computed for still holds a log."""
    aim = ctx.state.data.get("pm_aim")
    if not aim:
        return None
    log_name = config.get("logName", DEFAULT_CONFIG["logName"]).lower()
    holds_log = any(item.get("slot") == aim["slot"]
                    and (item.get("name") or "").lower() == log_name
                    for item in inventory)
    if not holds_log:
        ctx.state.data.pop("pm_aim", None)
        return None
    return aim


def cast_in_place(ctx, config, aim):
    """Park the mouse on the overlap pixel once, then just keep clicking it.

    USER-DIRECTED 2026-08-26: once a pair has worked, the mouse should not move again for the
    rest of the inventory -- not between the two clicks of a cast, and not between casts. The
    first cast on a fresh aim flies there through click_entity (which re-rolls its aim jitter
    every call, so it was still nudging the cursor a few pixels every single cast); afterwards
    the cursor is already on target and both clicks are bare click_human.

    The cursor position is verified rather than assumed, which is what makes standing still
    safe: it is a local Windows call, not an HTTP round trip, so checking costs nothing, and
    anything that moved the mouse out from under us just re-flies once."""
    spell = resolve_spell(ctx, config)
    if spell is None:
        return False

    xp_before = _prime_confirmation(ctx)

    if cursor_on_aim(aim):
        controller.click_human(ctx.ser)
    else:
        # A named synthetic entity so the live log still reads as something, and offset (0,0)
        # because the aim point is already exactly where it needs to be.
        point = {"name": "Plank Make on log", "screenX": aim["x"], "screenY": aim["y"]}
        if not controller.click_entity(ctx.ser, point, offset=(0, 0), check_ui_blockers=False):
            return False

    # MEASURED: the human's own gap between selecting the spell and clicking the log was
    # 0.2-0.4s. Nothing moves in between.
    controller.human_delay(*IN_PLACE_CLICK_GAP)
    controller.click_human(ctx.ser)

    return await_cast(ctx, config, xp_before)


def cursor_on_aim(aim):
    """Whether the mouse is already sitting close enough to the aim pixel to click in place.

    The tolerance covers click_entity's own aim jitter (settings.ENTITY_AIM_RADIUS) on the one
    flight that put the cursor here, and stays far inside the overlap band the aim sits in the
    middle of -- MEASURED live 2026-08-26, that band is ~50x28px, so a dozen pixels is nowhere
    near its edge."""
    try:
        x, y = controller.get_cursor_pos()
    except OSError:
        return False
    return abs(x - aim["x"]) <= CURSOR_DRIFT_PX and abs(y - aim["y"]) <= CURSOR_DRIFT_PX


def cast_and_learn_aim(ctx, config):
    """The two-click route, which also works out the single-pixel aim for the rest of the load."""
    log_name = config.get("logName", DEFAULT_CONFIG["logName"])

    spell = resolve_spell(ctx, config)
    if spell is None:
        return False

    xp_before = _prime_confirmation(ctx)

    if not controller.click_entity(ctx.ser, spell, offset=(0, 0), check_ui_blockers=False):
        return False

    # Read AFTER the spell click on purpose: selecting the spell is what swaps the client to the
    # inventory tab, and an inventory slot only reports a screen position while its panel is
    # actually drawn.
    inventory = controller.get_inventory()
    target = choose_target_slot(inventory, log_name, spell)
    if target is None:
        # Do not click anything now -- with a spell selected, a click on empty space just
        # cancels the selection, and a click on the wrong item wastes the runes.
        debug_log.log("plank", f"no {log_name} with a clickable slot -- leaving the spell selected")
        return False

    point = in_place_aim(spell, target, inventory)
    if point is None:
        debug_log.log("plank", "spell icon and the nearest log do not overlap -- "
                               "using two separate clicks for this load")
    else:
        ctx.state.data["pm_aim"] = {"x": point[0], "y": point[1], "slot": target.get("slot")}

    if not controller.click_entity(ctx.ser, target, offset=(0, 0), check_ui_blockers=False):
        return False

    return await_cast(ctx, config, xp_before)


def _prime_confirmation(ctx):
    """Clear the event buffer and snapshot Magic XP, both before any click goes out.

    The clear MUST come before the clicks. wait_for_event does not consume what it matches and
    the buffer holds ~10 ticks, so without it the previous cast's XP_GAINED would satisfy this
    cast's wait instantly -- the exact trap the routine contract calls out. The XP snapshot is
    taken before the cast rather than after, so a cast that resolves unusually fast cannot land
    between the click and the first read; it is what lets await_cast tell a genuinely failed
    cast from a lost UDP packet."""
    if ctx.events is not None:
        ctx.events.clear()
    return magic_xp()


def resolve_spell(ctx, config):
    """The spell icon's live screen position, opening the Magic tab first if it is not showing.

    found:false means the wrong widget id; found-but-not-clickable means the right id with its
    interface closed, which after a bank trip is simply the inventory tab still being up. Only
    the second is worth pressing a key over."""
    group, child = config.get("spellWidget", DEFAULT_CONFIG["spellWidget"])

    widget = controller.get_widget(group, child)
    if _spell_ready(widget):
        return _named(widget)

    controller.key_tap(ctx.ser, config.get("magicTabKey", DEFAULT_CONFIG["magicTabKey"]))
    controller.human_delay(0.15, 0.3)

    widget = controller.get_widget(group, child)
    if _spell_ready(widget):
        return _named(widget)

    debug_log.log("plank", f"spell widget {group}.{child} is not clickable "
                           f"(found={widget.get('found')}) -- wrong spellbook, or an interface is open")
    return None


def _spell_ready(widget):
    return bool(widget.get("clickable")) and widget.get("screenX") is not None


def _named(widget):
    """A spell widget usually carries no name, and click_entity logs entity['name'] on every
    click. Without this every cast logs `target=None(id=None)`, which is unreadable in exactly
    the live logs this routine will be debugged from."""
    if not widget.get("name"):
        widget["name"] = "Plank Make"
    return widget


def await_cast(ctx, config, xp_before=None):
    """Whether the cast resolved, judged by Magic XP.

    XP is the right signal for this and it is not a routine-specific trick: client.getSkill-
    Experience reflects the moment StatChanged fires, so it is sub-tick and definitive, where
    the inventory delta is the same information one round-trip later.

    Returns False on timeout, which is a normal outcome the caller retries -- a missed UDP
    packet costs one extra pair of clicks, and the game simply queues those."""
    if ctx.events is not None:
        payload = ctx.events.wait_for_event(
            "XP_GAINED",
            match=lambda p: (p.get("skill") == "Magic") and (p.get("delta") or 0) > 0,
            timeout_s=CAST_CONFIRM_TIMEOUT_S,
        )
        if payload is not None:
            return True
        # One HTTP cross-check before calling it a failure. UDP is lossy and the listener can go
        # quiet entirely; without this, a silent stream turns every perfectly good cast into a
        # "failure" and MAX_CONSECUTIVE_CAST_FAILURES safety-stops a routine that is working.
        # The stream stays the fast path -- this only runs on the timeout.
        if xp_before is None:
            return False
        current = magic_xp()
        return current is not None and current > xp_before

    # No event stream (the contract allows ctx.events to be None) -- poll instead. Slower, but
    # the routine keeps working rather than assuming the accelerator is always there.
    if xp_before is None:
        xp_before = magic_xp()
    if xp_before is None:
        return False

    deadline = time.monotonic() + CAST_CONFIRM_TIMEOUT_S
    while True:
        current = magic_xp()
        if current is not None and current > xp_before:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(XP_POLL_INTERVAL_S)


def empty_runes(config):
    """Name of the first required rune the pouch has run out of, or None.

    Proactive counterpart to rune_shortage: /rune-pouch reads the pouch's varbits, so this can
    stop BEFORE a wasted cast rather than after one. Both exist on purpose -- this one needs a
    client new enough to serve the endpoint, and controller.rune_pouch_count returns None
    (never 0) whenever it cannot tell, so a missing endpoint, an unresolved rune name or a
    forgotten pouch all fall through to the chat-based check instead of stopping the routine on
    an absence of evidence."""
    for name in config.get("runeNames", DEFAULT_CONFIG["runeNames"]):
        count = controller.rune_pouch_count(name)
        if count == 0:
            return name
    return None


def rune_shortage():
    """The game's own refusal message if the last chat line says a cast lacked runes, else None.

    Read only after a cast has already failed, so an unrelated chat line mentioning runes cannot
    stop the routine on its own -- it has to coincide with a cast that produced no Magic XP."""
    message = (controller.get_last_chat_message() or {}).get("message") or ""
    lowered = message.lower()
    if "rune" in lowered and any(hint in lowered for hint in RUNE_SHORTAGE_HINTS):
        return message
    return None


def magic_xp():
    skill = controller.get_skills().get("Magic") or {}
    return skill.get("xp")


def wait_for_cast_window(ctx):
    """Hold until the tick boundary the next pair should be fired on.

    Aligned to a real boundary via the event stream's tick clock rather than slept as a
    wall-clock interval: an unaligned sleep starts at whatever phase the XP happened to arrive
    at and lands anywhere inside a 600ms window, which is the difference between hitting the
    3-tick cadence and drifting into 4."""
    if CAST_LEAD_TICKS <= 0:
        return
    if ctx.events is not None:
        ctx.events.wait_ticks(CAST_LEAD_TICKS)
    else:
        time.sleep(CAST_LEAD_TICKS * 0.6)


# --- banking ------------------------------------------------------------------------------


def bank_phase(ctx, config):
    log_name = config.get("logName", DEFAULT_CONFIG["logName"])
    plank_name = config.get("plankName", DEFAULT_CONFIG["plankName"])

    if not banker.is_open():
        npc = find_banker(controller.get_npcs(), config)
        if npc is None:
            names = "/".join(config.get("bankerNames", DEFAULT_CONFIG["bankerNames"]))
            ctx.state.trigger_safety_stop(
                f"no {names} within {config.get('maxBankerDistance')} tiles -- this routine "
                f"does not travel, so it has to be started standing at a bank")
            return
        if not open_banker(ctx, npc):
            debug_log.log("plank", "bank did not open -- retrying next tick")
            return

    planks = count_item(controller.get_inventory(), plank_name)
    if planks:
        debug_log.log("plank", f"banking {planks} {plank_name}")

    # skipContainerDeposit is load-bearing, not tidiness: the generic path clicks the bank's
    # "Deposit Containers" button, which would empty the rune pouch this routine casts from.
    banker.deposit_inventory(ctx, {
        "keepItems": config.get("keepItems", DEFAULT_CONFIG["keepItems"]),
        "skipContainerDeposit": True,
    })

    # Withdrawing on top of a half-emptied inventory would take a few logs and read as success,
    # and the load after it would be short. Bail out and let the next tick re-enter with the
    # bank still open rather than carrying on from a state we know is wrong.
    left = await_deposit(plank_name)
    if left:
        debug_log.log("plank", f"{left} {plank_name} still in the inventory after depositing "
                               f"-- retrying next tick")
        return

    withdrew = withdraw_logs(ctx, log_name)
    close_bank(ctx)

    # A new load may sit in different slots, so the cached single-pixel aim has to go.
    ctx.state.data.pop("pm_aim", None)

    if withdrew is None:
        ctx.state.trigger_safety_stop(f"no {log_name} left in the bank")
    elif not withdrew:
        debug_log.log("plank", f"{log_name} withdrawal did not land -- retrying next tick")


def await_deposit(plank_name):
    """Units of `plank_name` still held once the deposit has had time to land.

    Polls rather than reading once: the shift-click is processed server-side on a game tick, so
    an immediate read reflects the inventory from before it -- CONFIRMED live 2026-08-26, that
    false alarm cost a whole engine tick on every bank trip."""
    deadline = time.monotonic() + DEPOSIT_SETTLE_S
    while True:
        left = count_item(controller.get_inventory(), plank_name)
        if left == 0 or time.monotonic() >= deadline:
            return left
        time.sleep(0.15)


def withdraw_logs(ctx, log_name):
    """True withdrawn, False the click did not land, None the bank genuinely has none left.

    Tries the plain shift-click first and only searches if that is not possible or does not
    work. The search costs ~2.5s of the ~8s bank trip (a click, nine keystrokes and a settle),
    and it is pure waste whenever the logs are already sitting on screen -- which they are on
    every trip where they live in the open bank tab. USER-DIRECTED 2026-08-26.

    The fallback is not optional though: MEASURED in the recording, the logs sat at bank slot
    443, and banker.py's own notes record that a bank item that far down the list reports a
    real but far-off-screen screenY. is_point_within_window is what tells those two cases
    apart, and a withdrawal that reports on screen but still does not arrive falls through to
    the search anyway, so a wrong guess costs latency rather than the trip."""
    if bank_slot_on_screen(log_name, fresh=True):
        if banker.withdraw_all_shift(ctx, log_name):
            return True
        debug_log.log("plank", f"{log_name} looked visible but the withdrawal did not land "
                               f"-- falling back to the bank search")

    banker.search_bank(ctx, log_name)

    # fresh=True because /bank is TickCache-gated and the search just rewrote the whole grid --
    # a cached read here would hand back the pre-search layout's positions. Checked here rather
    # than left to withdraw_all_shift so an empty bank can be told apart from a click that did
    # not land: both make that helper return False, but only one is worth stopping over.
    if not bank_slot_on_screen(log_name, fresh=True, require_on_screen=False):
        return None

    return banker.withdraw_all_shift(ctx, log_name)


def bank_slot_on_screen(name, fresh=False, require_on_screen=True):
    """Whether the bank holds `name` (and, by default, whether it is actually clickable)."""
    wanted = name.lower()
    for item in controller.get_bank(fresh=fresh).get("items", []):
        if (item.get("name") or "").lower() != wanted:
            continue
        if not require_on_screen:
            return True
        x, y = item.get("screenX"), item.get("screenY")
        return x is not None and controller.is_point_within_window(int(x), int(y))
    return False


def open_banker(ctx, npc):
    """Click the banker until the bank interface comes up.

    Not banker.open_bank: that one re-resolves its target through /objects between retries,
    which can never match an NPC. A bare left-click is right here -- for an NPC a player
    standing in the way makes the click a no-op rather than resolving it to something else, so
    the only failure mode is "nothing happened", and the only recovery needed is to click
    again. The re-resolve goes through /npc/by-index, which is uncached."""
    index = npc.get("index")
    deadline = time.monotonic() + BANK_OPEN_TIMEOUT_S

    while time.monotonic() < deadline:
        target = controller.get_npc_by_index(index) if index is not None else None
        controller.click_entity(ctx.ser, target or npc, offset=(0, 0))

        click_deadline = min(deadline, time.monotonic() + 3.0)
        while time.monotonic() < click_deadline:
            if banker.is_open():
                return True
            time.sleep(0.1)

    return banker.is_open()


def close_bank(ctx):
    """Escape, then check it actually closed.

    banker.py records that Escape with a search active closes the whole interface rather than
    just clearing the filter, which is what this wants -- but the routine is about to start
    clicking spellbook pixels, so it verifies rather than assumes."""
    deadline = time.monotonic() + BANK_CLOSE_TIMEOUT_S
    while time.monotonic() < deadline:
        banker.close_bank(ctx)
        settle = min(deadline, time.monotonic() + 1.0)
        while time.monotonic() < settle:
            if not banker.is_open():
                return True
            time.sleep(0.15)
    debug_log.log("plank", "bank interface did not close")
    return False
