# routines/karambwan_fishing.py
#
# Karambwan fishing at fairy ring DKP with a fish barrel, banked at the Shilo Village gem
# mine's deposit chest. USER 2026-09-06, verbatim loop:
#
#   click fishspot -> wait for full inventory -> go into equiped item and right click karamja
#   gloves -> use the gem mine teleport -> deposit all fish and empty barrel -> right click
#   equiped quest point cape -> teleport -> take fairy ring DKP -> click fishspot
#
# USER 2026-09-06, second pass: "we no longer have to fill the barrel it will automaticly pick
# up all the fish so when we have full inventory we go to deposit as per usual" -- the barrel
# stores every catch on its own now, so the recording's Fill leg (and the mid-inventory top-up
# it did) is gone: a full inventory means the barrel is full too and the trip banks straight
# away. The barrel is still EMPTIED at the deposit chest, which is where its fish are banked.
#
# Written 2026-09-06 from the user's own recording of one full loop
# (recordings/2026-09-06_083723_karamwan-fishing.*, 3m56s, 19 actions) plus the cache and the
# RuneLite source; NOT yet run live. Every widget id, option text and landing time below is off
# that recording:
#   Fish -> npc 4712, "now interacting" +0.5s, "You lower the Karambwan vessel." +2.3s from 8
#     tiles; a catch every 4 ticks (2.4s) at Fishing 75, a full inventory in ~90-110s; the game
#     announces the full inventory with "You can't carry any more Karambwan." and idles.
#   Gem Mine -> widget 387.22, LOADING +3.2s, landed (2841,9388); Deposit -> loc 10530 "Bank
#     Deposit Chest" at (2842,9382), interface up ~2.9s after the click (a 6-tile walk);
#     Deposit-All -> 192.24 [Raw karambwan] -24 fish +0.3s; Empty-to-bank -> 192.24 [Fish
#     barrel] "You empty the barrel." +0.2s; Close -> 192.1.
#   Teleport -> widget 387.16, landed at the Legends' Guild (2730,3348) +2.4s.
#   Last-destination (DKP) -> loc 29495 "Fairy ring" at (2740,3351) clicked from 10-12 tiles,
#     the game walks, LOADING +6.5s, landed (2900,3111).
# USER 2026-09-06 has menu-swapped every one of those to the LEFT click (the worn gloves and
# cape, Deposit-All, Empty-to-bank, and the ring's Last-destination), so each is one click via
# _click_option -- hover-gated, with the old right-click menu kept only as the fallback.
# The fishing wait itself is spent TABBED OUT (USER 2026-09-06: "any human doing this would
# just be watching youtube or something"): fish_wait Alt+Tabs away, sends nothing at all while
# unfocused, and comes back a drawn notice_delay_s later -- usually a glance, sometimes USER's
# 10-40s of genuine lateness. See tab_out/tab_back_in for why the tab back in is verified.
# One consequence, same as routines/nmz.py's: the engine's own check_safety/check_pvp_threat
# and break scheduling sit behind that focus gate too, so none of them fire while we are away.
# Accepted -- the window is one inventory (FISH_MAX_S 300s, not nmz's 20 minutes), and standing
# at a fishing spot in Karamja is not a situation any of them would have to act on.
#
# Every phase is derived from LIVE state (position, inventory, the fish barrel), never from a
# stored step, so a restart anywhere on the loop resumes in place (CLAUDE.md: ctx.state.data is
# wiped on restart, the character's position is not).
#
# Loadout: Karambwan vessel (loaded), a stack of Raw karambwanji (bait), a Fish barrel; WORN
# Karamja gloves 3/4 (cache obj 11140/13103 iop3 "Gem Mine") and a Quest point cape (cache obj
# 9813 iop3 "Teleport", lands at the Legends' Guild). The worn items are clicked through the
# worn-equipment interface (group 387: cape child 16, gloves child 22 -- wornitems.if3 slot1/
# slot9) via the right-click menu, because /equipment carries no screen position and a worn
# item's left-click is Remove.
#
# Run with:
#     python controller.py routines/karambwan_fishing.py

import random
import time

import controller
import debug_log
import outcome_waits
import travel

# --------------------------------------------------------------------------- game facts

# Karambwan spot: NPC 4712 "Fishing spot", op "Fish"; the DKP spawn is at (2899,3119), six
# tiles north of the DKP ring (2899,3113) (mejrs NPC spawn list; RuneLite FairyRingLocation).
# Karambwan spots do not move.
SPOT_NPC_ID = 4712
SPOT_NAME = "Fishing spot"
SPOT_ACTION = "Fish"
TILE_SPOT = {"x": 2899, "y": 3119, "plane": 0}
TILE_DKP = {"x": 2899, "y": 3113, "plane": 0}
TILE_BLR = {"x": 2740, "y": 3351, "plane": 0}      # Legends' Guild ring (recording)
TILE_DEPOSIT = {"x": 2842, "y": 9382, "plane": 0}  # gem mine "Bank Deposit Chest" (recording)
REGION_DKP = 11568          # (2899 >> 6) << 8 | (3113 >> 6)
REGION_LEGENDS = 10804      # (2739 >> 6) << 8 | (3353 >> 6)
REGION_GEM_MINE = 11410     # the underground Shilo mine (recording)
UNDERGROUND_MIN_Y = 9000    # the gem mine is the surface +6400 in y (landing (2841,9388))
FULL_CHAT = "can't carry any more"          # "You can't carry any more Karambwan."

VESSEL_NAME = "karambwan vessel"
BAIT_NAME = "raw karambwanji"           # EXACT match: "raw karambwan" is a substring of it
FISH_NAME = "raw karambwan"             # EXACT match, see above
BARREL_NAMES = ("fish barrel", "open fish barrel")   # cache obj 25582 / 25584
BARREL_EMPTY_ACTIONS = ("Empty-to-bank", "Empty")   # on the barrel in the deposit grid (recording)
BARREL_CAPACITY = 28
INVENTORY_SLOTS = 28

GLOVES_NAME = "karamja gloves"
GLOVES_TIERS = ("3", "4")               # only 3/4 carry the Gem Mine teleport (cache iop3)
CAPE_NAME = "quest point cape"
OPT_GEM_MINE = "Gem Mine"
OPT_CAPE_TELEPORT = "Teleport"
EQUIP_GROUP = controller.EQUIPMENT_WIDGET_GROUP
EQUIP_CAPE_CHILD = 16
EQUIP_GLOVES_CHILD = 22                 # recording: "Gem Mine Karamja gloves 3" on 387.22

# Deposit box interface (cache interface 192): 24 the inventory grid (right-click options
# "Deposit-All" on a fish, "Empty-to-bank" on the barrel -- recording), 1 the close button.
DEPOSIT_GROUP = 192
DEPOSIT_INV_CHILD = 24
DEPOSIT_CLOSE_CHILD = 1
DEPOSIT_ALL_ACTION = "Deposit-All"
DEPOSIT_NAME = "deposit"                # "Bank Deposit Chest" (loc 10530) / "Bank deposit box"
DEPOSIT_ACTION = "Deposit"

# Fairy ring: matched by NAME (every ring loc is "Fairy ring"; ids differ per ring). The
# one-click "Last-destination (DKP)" entry is preferred; "Configure" opens the dial interface
# (cache interface 398: 19/20 left dial, 21/22 middle, 23/24 right, 26 confirm). Dial letters by
# varbit value (RuneLite FairyRingPlugin): left A D C B (3985), middle I L K J (3986), right
# P S R Q (3987) -- DKP is (1, 2, 0).
RING_NAME = "fairy ring"
OPT_LAST_DEST_DKP = "Last-destination (DKP)"
OPT_CONFIGURE = ("Configure", "Use")
RING_GROUP = 398
RING_CONFIRM_CHILD = 26
RING_DIALS = ((3985, 1, 19, 20), (3986, 2, 21, 22), (3987, 0, 23, 24))
RING_CLICK_RANGE = 16           # the human clicked it from 12 tiles and let the game walk

# The game's "busy" varbit (agility.py's obstacle signal too): 1 while a teleport/fairy ring
# animates, 0 on landing (recording: every teleport and both ring hops). Subscribed so the
# landing arrives as a pushed VARBIT_CHANGED instead of the wait sitting out its whole budget.
VARBIT_BUSY = 12393
VARBIT_SUBSCRIPTIONS = (VARBIT_BUSY,)
LANDING_SIGNALS = {
    "VARBIT_CHANGED": lambda p: p.get("varbitId") == VARBIT_BUSY,
    "ANIMATION_CHANGED": None,
    "PLAYER_STOPPED": None,
    "PLAYER_MOVING": None,
}

LANDING_RECHECK_S = 0.6         # re-read the landing every game tick while waiting (see _await_landing)
INVENTORY_TAB_INDEX = 3         # VarClientInt.INVENTORY_TAB value for the inventory (/widget/active-tab)

# --------------------------------------------------------------------------- budgets

FISH_CLICK_BUDGET_S = 4.0       # click -> the player is no longer idle (plus the walk)
FISH_MAX_S = 300.0              # one inventory of karambwan; hand back to the engine past this
IDLE_RECLICK_S = 2.0            # sustained idle while fishing -> the spot needs a re-click
IDLE_STOP_AFTER = 6             # consecutive re-clicks producing no fish -> safety stop
TELEPORT_BUDGET_S = 8.0         # a worn-item teleport -> standing in the destination
INTERFACE_BUDGET_S = 3.0        # a click -> its interface open/closed
DEPOSIT_BUDGET_S = 2.0          # a deposit click -> the item gone from the inventory
DEPOSIT_TRIES = 3
RING_BUDGET_S = 8.0             # confirm/Last-destination -> standing at DKP
DIAL_CLICK_S = 0.5              # one dial rotation
TICK_CHAIN_MAX = 6              # phases chained inside one tick() before handing back

# --- AFK tab-out (USER 2026-09-06: "want to change karambwan to be tabbed out while fishing
# since it is such a long fishing time any human doing this would just be watching youtube or
# something ... we can be late tabbing back in sometimes like variance 10-40 sec").
# An inventory of karambwan is 90-110s of standing still with nothing to click, so the routine
# physically Alt+Tabs away for it (controller.alt_tab, the primitive routines/nmz.py has used
# since 2026-08-15) and sends NOTHING at all until it has tabbed back in -- a level-up SPACE or
# a fidget while unfocused lands in whatever window is now in front.
AFK_MIN_FREE_SLOTS = 8          # below this the wait is ~20s: not worth tabbing away for
AFK_POLL_S = 2.5                # how often a tabbed-out wait re-reads the bridge (no input)
AFK_LATE_CHANCE = 0.33          # ... of returns are a properly distracted one
AFK_LATE_RANGE_S = (10.0, 40.0)  # USER's number: sometimes we are this late noticing
AFK_PROMPT_RANGE_S = (2.0, 6.0)  # otherwise a normal glance at the corner of the screen
AFK_FOCUS_TRIES = 3             # tab-back-in attempts before a safety stop (nmz's number)
AFK_FOCUS_WAIT_S = 5.0          # per attempt
AFK_UNFOCUS_WAIT_S = 2.0        # confirming we actually LEFT the window
DIALOG_DISMISS_TRIES = 4        # SPACEs before giving up on a "click here to continue" box

# The PLAIN message box, which /state's dialogOpen does NOT report -- MEASURED live 2026-09-06
# (see _continue_box_open). 229.3 carries the line, 229.4 the prompt. Same group blast_furnace
# meets after a dispenser Take.
CONTINUE_GROUP = 229
CONTINUE_CHILD = 4
CONTINUE_TEXT = "click here to continue"
STALL_WARN_S = 150.0
STALL_STOP_S = 420.0

DEFAULT_CONFIG = {
    "enabled": True,
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
        "stopOnInventoryFull": False,   # a full inventory is the loop's own signal
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    config = dict(DEFAULT_CONFIG)
    config["safety"] = dict(DEFAULT_CONFIG["safety"], stopOnInventoryFull=False)
    return config


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


# --------------------------------------------------------------------------- state reads

def _inventory():
    return controller.get_inventory() or []


def _free_slots(inv=None) -> int:
    inv = _inventory() if inv is None else inv
    return INVENTORY_SLOTS - len(inv)


def _exact(inv, name: str):
    return [i for i in inv if (i.get("name") or "").lower() == name]


def _contains(inv, sub: str):
    return next((i for i in inv if sub in (i.get("name") or "").lower()), None)


def _fish_count(inv=None) -> int:
    inv = _inventory() if inv is None else inv
    return len(_exact(inv, FISH_NAME))


def _barrel_item(inv=None):
    inv = _inventory() if inv is None else inv
    return next((i for i in inv if (i.get("name") or "").lower() in BARREL_NAMES), None)


def barrel_room() -> int:
    """Free fish slots in the barrel per the m8aq reader, or -1 when it cannot tell (it learns
    the count only from a Check message, so usually it cannot). Nothing routes on this any more
    -- the barrel fills itself -- it is only the secondary confirm that an Empty-to-bank landed."""
    st = controller.get_api("storage", "fish-barrel")
    if st and not st.get("error") and st.get("present") and st.get("contentsLoaded") \
            and st.get("remainingCapacity") is not None:
        return int(st["remainingCapacity"])
    return -1


def _player():
    return controller.get_player() or {}


def _idle() -> bool:
    return bool((controller.get_combat() or {}).get("isIdle", False))


def _ensure_inventory_tab(ctx) -> bool:
    """The inventory tab, confirmed on the authoritative active-tab varc (/widget/active-tab),
    not on the inventory widget's hidden flag alone. kf_live1: the blind "w" tap after a
    teleport was eaten by the LOADING screen and the first barrel Fill was withheld because the
    hover saw no item under the cursor -- the panel showing was not the inventory."""
    _dismiss_dialog(ctx)                # a dialogue swallows "w" -- see _dismiss_dialog
    for _ in range(3):
        tab = controller.get_active_tab()
        if tab is None:
            return controller.open_inventory_tab(ctx.ser)
        if tab == INVENTORY_TAB_INDEX:
            return True
        controller.key_tap(ctx.ser, controller.INVENTORY_TAB_KEY)
        controller.human_delay(0.25, 0.4)
    return controller.get_active_tab() in (None, INVENTORY_TAB_INDEX)


def _underground(player) -> bool:
    return (player.get("y") or 0) >= UNDERGROUND_MIN_Y


# --------------------------------------------------------------------------- phase derivation

def derive_phase(player: dict, free: int, fish: int, data: dict, config: dict) -> str:
    """Pure: which leg of the loop the LIVE state is on.

    bank    -- in the gem mine with fish to deposit or a barrel not yet emptied this trip
    cape    -- in the gem mine, all deposited: Quest point cape to the Legends' Guild
    ring    -- at the Legends' Guild: fairy ring to DKP
    to_bank -- at DKP with a full inventory. The barrel stores every catch on its own, so a
               full inventory means the barrel filled up first: nothing left to do but bank.
    fish    -- at DKP with room: click the spot
    lost    -- anywhere else
    """
    if _underground(player):
        if fish > 0 or not data.get("kf_trip_emptied"):
            return "bank"
        return "cape"
    region = player.get("regionId")
    if region == REGION_LEGENDS:
        return "ring"
    if region == REGION_DKP:
        return "to_bank" if free <= 0 else "fish"
    return "lost"


# --------------------------------------------------------------------------- startup

def _startup_ok(ctx) -> bool:
    """Once per activation: the vessel, bait and barrel in the inventory, the gloves and cape
    worn. A missing piece is a safety stop naming it -- the loop cannot recover any of them."""
    inv = _inventory()
    equipment = controller.get_equipment() or {}
    if not inv and not equipment:
        return False        # first tick after a login can read empty -- try again next tick
    gloves = ((equipment.get("gloves") or {}).get("name") or "").lower()
    cape = ((equipment.get("cape") or {}).get("name") or "").lower()
    missing = []
    if _contains(inv, VESSEL_NAME) is None:
        missing.append("Karambwan vessel")
    if not _exact(inv, BAIT_NAME):
        missing.append("Raw karambwanji (bait)")
    if _barrel_item(inv) is None:
        missing.append("Fish barrel")
    if GLOVES_NAME not in gloves or not any(t in gloves for t in GLOVES_TIERS):
        missing.append("worn Karamja gloves 3/4")
    if CAPE_NAME not in cape:
        missing.append("worn Quest point cape")
    if missing:
        ctx.state.trigger_safety_stop("karambwan: missing " + ", ".join(missing))
        return False
    return True


# --------------------------------------------------------------------------- fishing

def _nearest_spot():
    best = None
    for npc in controller.get_npcs() or []:
        if npc.get("id") != SPOT_NPC_ID:
            continue
        if best is None or npc.get("distance", 9999) < best.get("distance", 9999):
            best = npc
    return best


def _click_option(ctx, target, option, what: str, refresh_target=None,
                  interface: bool = True) -> bool:
    """Take `option` on `target` with ONE left click, the right-click menu only as a fallback.

    USER 2026-09-06: "basicly all things we do in karam fishing ive custom menu swapped so a
    simple left click will do, even the fairy ring will default to last destination. both
    teleports in inventory left clicks and deposit all and empty barrel both left clicks."
    A menu costs an open, a /menu read and a second click (~1s each), and CLAUDE.md records
    live "Menu never opened" failures -- so the swap is worth taking.

    The left click is GATED on /hover reporting `option` as the live left-click action on this
    exact target, and a withheld click falls back to the menu -- so a swap that is missing, or
    reset by a game update, costs a menu rather than firing the wrong action. That gate is
    load-bearing on the two WORN items: an un-swapped worn item's left click is **Remove**,
    i.e. unequipping the gloves mid-run. Same shape as falconry._use_teleport.

    `simulate_mistakes=False` throughout, for the reason banker.py never misclicks a bank
    button: a deliberate miss beside an item in the deposit grid, plus its corrective reclick,
    lands on the neighbouring slot -- and the neighbour of the karambwan there is the barrel.
    `interface=False` is the fairy ring, a world object: it keeps the UI-blocker check and the
    default aim offset."""
    kwargs = {"simulate_mistakes": False}
    if interface:
        kwargs["check_ui_blockers"] = False
        kwargs["offset"] = (0, 0)
    if refresh_target is not None:
        kwargs["refresh_target"] = refresh_target

    ok = controller.click_entity(ctx.ser, target, expected_action=option, **kwargs)
    if ok:
        return True
    if getattr(ok, "withheld", False):
        debug_log.log("karambwan", f"{what}: left-click is not '{option}' here -- using the menu")
    kwargs.pop("simulate_mistakes", None)
    return bool(controller.click_menu_action(ctx.ser, target, option, **kwargs))


def _continue_box_open() -> bool:
    """True while the plain "Click here to continue" message box is up.

    MEASURED live 2026-09-06, with the box on screen and the routine stuck: /state reported
    dialogOpen **FALSE**, because GameStateProvider only counts the NPC/player/sprite dialogue
    widgets. /widgets/dump at that same moment carried group 229 -- 229.3 "You can't carry any
    more Karambwan.", 229.4 "Click here to continue" -- and /widget?group=229&child=4 answered
    clickable. So this box is invisible to dialogOpen and to the engine's own
    check_stray_dialog, and it SWALLOWS the tab hotkeys ("e"/"w"): USER, live, "there is a
    dialog in the way so we cant press e".

    The text is matched as well as the id, so a message box that is not this prompt (229 is
    reused) is not mistaken for one."""
    widget = controller.get_widget(CONTINUE_GROUP, CONTINUE_CHILD) or {}
    return bool(widget.get("clickable")) and CONTINUE_TEXT in (widget.get("text") or "").lower()


def _blocking_dialog() -> bool:
    """Anything on screen that swallows a keypress -- either kind of box."""
    if (controller.get_state() or {}).get("dialogOpen"):
        return True
    return _continue_box_open()


def _dismiss_dialog(ctx) -> bool:
    """A level-up (or any "click here to continue") interrupts fishing; the engine's own
    check_stray_dialog cannot run while fish_wait holds the tick.

    CONFIRMED, not blind, and it returns whether the way is actually clear -- MEASURED live
    2026-09-06: an open dialogue SWALLOWS the tab hotkeys ("e", "w"), so open_equipment_tab
    tapped "e" three times a tick and logged "could not open the equipment tab" for the rest
    of the run. A dialogue can also have more than one stage, so one blind SPACE is not
    enough. _blocking_dialog, not dialogOpen alone: the box that actually did this is the one
    dialogOpen cannot see (see _continue_box_open)."""
    for _ in range(DIALOG_DISMISS_TRIES):
        if not _blocking_dialog():
            return True
        controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.3, 0.6)
    return not _blocking_dialog()


def _fish_loop(ctx, afk: bool = False) -> str:
    """Hold the tick while the player fishes. Returns "full" (no free slot), "idle" (the player
    stood still IDLE_RECLICK_S with room left -- re-click the spot), "no_bait", or "timeout".
    INVENTORY_CHANGED wakes the check on every catch; between catches the wait is spent in
    human-length still segments with a resting-hand fidget, like every other long wait here.

    `afk=True` is the tabbed-out variant and sends NO INPUT: no SPACE for a stray dialogue and
    no fidget, because RuneLite is not the focused window -- both would land somewhere else.
    Whatever box appears meanwhile is cleared by the next _dismiss_dialog on the way to a tab
    hotkey -- NOT by the engine's check_stray_dialog, which cannot help here: tick() chains the
    next phase inside the same tick, so it does not run in between. The poll also slows to
    AFK_POLL_S, which is what someone watching a video is doing anyway."""
    deadline = _now() + FISH_MAX_S
    idle_since = None
    while _now() < deadline:
        inv = _inventory()
        if _free_slots(inv) <= 0:
            return "full"
        if not _exact(inv, BAIT_NAME):
            return "no_bait"
        if not afk:
            _dismiss_dialog(ctx)
        if _idle():
            idle_since = idle_since or _now()
            if _now() - idle_since >= IDLE_RECLICK_S:
                return "idle"
        else:
            idle_since = None
        segment = AFK_POLL_S if afk else outcome_waits.still_segment_s()
        chunk = min(segment, max(0.1, deadline - _now()))
        signals = {"INVENTORY_CHANGED": None, "CHAT_MESSAGE": outcome_waits.chat_has(FULL_CHAT),
                   "ANIMATION_CHANGED": lambda p: p.get("animation") == -1}
        outcome_waits.await_pre(ctx, signals, chunk, confirm=lambda: _free_slots() <= 0,
                                reaction=False, now_fn=_now, sleep_fn=_sleep)
        if not afk and deadline - _now() > 1.0:
            controller.fidget_once()
    return "timeout"


def notice_delay_s(rng=random) -> float:
    """How long after the fishing ends before the player notices and tabs back in.

    Mostly a glance at the corner of the screen (AFK_PROMPT_RANGE_S); AFK_LATE_CHANCE of the
    time properly distracted, and USER's 10-40s late. A full inventory sitting at the spot
    costs nothing -- the game just idles -- so the lateness is pure realism."""
    lo, hi = AFK_LATE_RANGE_S if rng.random() < AFK_LATE_CHANCE else AFK_PROMPT_RANGE_S
    return rng.uniform(lo, hi)


def tab_out(ctx) -> bool:
    """Alt+Tab away, CONFIRMED by /state going unfocused. False means we are still looking at
    the game (a machine with nothing else open swallows Alt+Tab entirely) -- the caller then
    fishes focused rather than sitting there believing it is away."""
    controller.alt_tab(ctx.ser)
    if controller.wait_for_focus(timeout_s=AFK_UNFOCUS_WAIT_S, focused=False):
        return True
    debug_log.log("karambwan", "Alt+Tab didn't unfocus the game -- fishing focused")
    return False


def tab_back_in(ctx) -> bool:
    """Alt+Tab back, verified, up to AFK_FOCUS_TRIES times; safety-stops otherwise.

    This verification is not optional: the engine loop does not call tick() again while
    unfocused (controller.py's "Don't blind-click into a backgrounded window" gate), so a
    tab-back-in that lands on some OTHER window -- one that came to the foreground while we
    were away -- would stall the whole run silently, with nothing in the log. Same reasoning,
    same shape as routines/nmz.py's."""
    for attempt in range(AFK_FOCUS_TRIES):
        controller.alt_tab(ctx.ser)
        if controller.wait_for_focus(timeout_s=AFK_FOCUS_WAIT_S):
            return True
        debug_log.log("karambwan", f"tab-back-in attempt {attempt + 1}/{AFK_FOCUS_TRIES} "
                                   f"didn't regain focus")
    ctx.state.trigger_safety_stop(
        f"karambwan: Alt+Tab back into RuneLite failed {AFK_FOCUS_TRIES} times")
    return False


def fish_wait(ctx) -> str:
    """_fish_loop, tabbed out for the wait when it is long enough to be worth it.

    Returns _fish_loop's own result, plus "unfocused" when the tab back in failed -- which
    fish_phase must NOT chain a bank trip onto, since those clicks would land in another
    window."""
    if _free_slots() < AFK_MIN_FREE_SLOTS or not tab_out(ctx):
        return _fish_loop(ctx)
    result = _fish_loop(ctx, afk=True)
    late = notice_delay_s()
    debug_log.log("karambwan", f"fishing done ({result}) -- tabbing back in {late:.0f}s later")
    _sleep(late)
    if not tab_back_in(ctx):
        return "unfocused"
    return result


def fish_phase(ctx, config: dict) -> bool:
    """Click the karambwan spot (verified through /hover as "Fish"), then fish_wait. True when
    the inventory is full (the next phase can run at once), False to hand back to the engine."""
    spot = _nearest_spot()
    if spot is None:
        debug_log.log("karambwan", "no karambwan spot in view -- walking to it")
        travel.walk_to(ctx, TILE_SPOT, goal_adjacent=True)
        return False

    controller.human_delay(0.1, 0.3)
    mark = outcome_waits.mark(ctx)
    ok = controller.click_entity(
        ctx.ser, spot,
        refresh_target=controller.npc_index_refresh_target(spot.get("index")),
        expected_action=SPOT_ACTION,
    )
    if not ok:
        debug_log.log("karambwan", "spot click did not land -- retrying next tick")
        return False
    # The click's outcome: the player stops being idle (walk + first cast), then the wait.
    outcome_waits.await_pre(ctx, ("TARGET_CHANGED", "ANIMATION_CHANGED", "PLAYER_MOVING",
                                  "PLAYER_STOPPED", "INVENTORY_CHANGED", "CHAT_MESSAGE"),
                            FISH_CLICK_BUDGET_S + outcome_waits.walk_budget(spot),
                            confirm=lambda: not _idle(), since=mark, reaction=False,
                            now_fn=_now, sleep_fn=_sleep)
    before = _fish_count()
    t_fish = _now()
    result = fish_wait(ctx)
    if result == "full":
        ctx.state.data["kf_idle_clicks"] = 0
        debug_log.log("karambwan", f"inventory full: {_fish_count() - before} karambwan in "
                                   f"{_now() - t_fish:.0f}s")
        return True
    if result == "no_bait":
        ctx.state.trigger_safety_stop("karambwan: out of Raw karambwanji")
        return False
    if result == "idle":
        n = ctx.state.data.get("kf_idle_clicks", 0) + (0 if _fish_count() > before else 1)
        ctx.state.data["kf_idle_clicks"] = n
        if n >= IDLE_STOP_AFTER:
            ctx.state.trigger_safety_stop(f"karambwan: {n} spot clicks in a row caught nothing")
            return False
        debug_log.log("karambwan", f"player idle with room left -- re-clicking the spot ({n}/{IDLE_STOP_AFTER})")
        return True
    # "timeout", or "unfocused" -- the tab back in failed and tab_back_in already safety-
    # stopped. Either way hand back to the engine (whose own focus gate then holds the loop):
    # chaining the bank trip onto an unfocused window would click into whatever is in front.
    return False


# --------------------------------------------------------------------------- worn-item teleports

def _await_landing(ctx, mark, landed, budget_s: float) -> bool:
    """Wait for `landed()` after a teleport/fairy ring, re-reading it every LANDING_RECHECK_S.

    MEASURED live 2026-09-06 (kf_live1): every landing signal -- the busy varbit 12393 going 0,
    the animation ending -- fires on the SAME tick the LOADING screen starts, and the bridge
    answers nothing during LOADING, so the one HTTP confirm those signals trigger fails and a
    single await_outcome then sleeps to its full budget (5.4s lost on the cape). Chunking the
    wait at one tick keeps the pushed signals (a landing is still noticed within a tick) while
    the pre-check at each chunk start re-reads /player as soon as the bridge is back."""
    deadline = _now() + budget_s
    while True:
        remaining = deadline - _now()
        if remaining <= 0:
            return bool(landed())
        out = outcome_waits.await_pre(ctx, LANDING_SIGNALS, min(remaining, LANDING_RECHECK_S),
                                      confirm=landed, since=mark, reaction=False,
                                      now_fn=_now, sleep_fn=_sleep)
        if out:
            return True


def _open_equipment(ctx) -> bool:
    """The worn-equipment tab, with a blind SPACE and one retry when the hotkey does not take.

    MEASURED live 2026-09-06 (USER: "there is a dialog in the way so we cant press e ...
    inventory full dialog pops up, we have to press space to confirm to use keyboard
    shortcut"): an open dialogue swallows the tab hotkeys, so this tapped "e" three times a
    tick and logged "could not open the equipment tab" for the rest of the run. Nothing else
    clears that box -- _fish_loop returns "full" BEFORE it reaches its own _dismiss_dialog,
    and tick() chains the next phase inside the same tick, so the engine's check_stray_dialog
    does not run in between either.

    _dismiss_dialog handles the box that caused this (group 229, MEASURED live -- see
    _continue_box_open). The blind SPACE below stays as a second layer for the NEXT box that
    neither check knows about: from here an unread box is indistinguishable from a dropped
    keypress, and a SPACE with nothing open does nothing at all. The log line names what the
    two checks saw, so the next occurrence says which case it was."""
    _dismiss_dialog(ctx)
    if controller.open_equipment_tab(ctx.ser):
        return True
    debug_log.log("karambwan", f"equipment tab hotkey didn't take (dialogOpen="
                               f"{(controller.get_state() or {}).get('dialogOpen')}, "
                               f"continueBox={_continue_box_open()}) -- SPACE and retrying")
    controller.key_tap(ctx.ser, "SPACE")
    controller.human_delay(0.3, 0.6)
    if controller.open_equipment_tab(ctx.ser):
        return True
    debug_log.log("karambwan", "could not open the equipment tab")
    return False


def worn_teleport(ctx, child: int, option: str, landed, what: str) -> bool:
    """Click a worn item's `option` through the equipment interface; True once `landed()` holds
    (awaited on PLAYER_STOPPED/MOVING, TELEPORT_BUDGET_S). One left click when the account's
    swap is in place, the menu otherwise -- see _click_option."""
    if not _open_equipment(ctx):
        return False
    widget = controller.get_widget(EQUIP_GROUP, child)
    if not widget.get("clickable"):
        debug_log.log("karambwan", f"equipment slot {EQUIP_GROUP}.{child} is not clickable")
        return False
    mark = outcome_waits.mark(ctx)
    debug_log.log("karambwan", f"{what}: {option}")
    if not _click_option(ctx, widget, option, what):
        debug_log.log("karambwan", f"{what}: neither the left click nor the menu took '{option}'")
        return False
    t0 = _now()
    out = _await_landing(ctx, mark, landed, TELEPORT_BUDGET_S)
    if not out:
        debug_log.log("karambwan", f"{what} did not land within {TELEPORT_BUDGET_S}s")
    else:
        debug_log.log("karambwan", f"{what}: landed {_now() - t0:.1f}s after the click")
    return bool(out)


def to_bank_phase(ctx, config: dict) -> bool:
    ok = worn_teleport(ctx, EQUIP_GLOVES_CHILD, OPT_GEM_MINE,
                       lambda: _underground(_player()), "Karamja gloves")
    if ok:
        ctx.state.data["kf_trip_emptied"] = False
        _ensure_inventory_tab(ctx)
    return ok


def cape_phase(ctx, config: dict) -> bool:
    ok = worn_teleport(ctx, EQUIP_CAPE_CHILD, OPT_CAPE_TELEPORT,
                       lambda: _player().get("regionId") == REGION_LEGENDS, "Quest point cape")
    if ok:
        ctx.state.data["kf_trip_emptied"] = False
        _ensure_inventory_tab(ctx)
    return ok


# --------------------------------------------------------------------------- the deposit chest

def _deposit_open() -> bool:
    return bool(controller.get_widget(DEPOSIT_GROUP, DEPOSIT_INV_CHILD).get("clickable"))


def _find_deposit():
    best = None
    for o in controller.get_objects(interactable=True) or []:
        if DEPOSIT_NAME not in (o.get("name") or "").lower():
            continue
        if not any(DEPOSIT_ACTION.lower() == (a or "").lower() for a in (o.get("actions") or [])):
            continue
        if best is None or o.get("distance", 9999) < best.get("distance", 9999):
            best = o
    return best


def _deposit_children():
    # controller.get_widget_children returns the LIST of populated children (live crash
    # 2026-09-06: the first cut read it as {"children": [...]}).
    data = controller.get_widget_children(DEPOSIT_GROUP, DEPOSIT_INV_CHILD) or []
    if isinstance(data, dict):
        data = data.get("children") or []
    return [c for c in data if isinstance(c, dict) and c.get("itemName")]


def _deposit_child(names) -> dict:
    names = tuple(n.lower() for n in names)
    return next((c for c in _deposit_children() if (c.get("itemName") or "").lower() in names), None)


def _open_deposit(ctx) -> bool:
    chest = _find_deposit()
    if chest is None:
        n = ctx.state.data.get("kf_no_chest", 0) + 1
        ctx.state.data["kf_no_chest"] = n
        debug_log.log("karambwan", f"no deposit chest in view ({n}) -- walking to it")
        if n >= 10:
            ctx.state.trigger_safety_stop("karambwan: no deposit chest found in the gem mine")
            return False
        travel.walk_to(ctx, TILE_DEPOSIT, goal_adjacent=True)
        return False
    ctx.state.data["kf_no_chest"] = 0
    refresh = controller.object_refresh_target(chest.get("x"), chest.get("y"), chest.get("plane", 0),
                                               chest.get("id"))
    mark = outcome_waits.mark(ctx)
    if not controller.click_entity(ctx.ser, chest, refresh_target=refresh, expected_action=DEPOSIT_ACTION):
        return False
    opened = outcome_waits.await_pre(
        ctx, {"WIDGET_LOADED": lambda p: p.get("groupId") == DEPOSIT_GROUP},
        INTERFACE_BUDGET_S + outcome_waits.walk_budget(chest), confirm=_deposit_open,
        since=mark, reaction=False, now_fn=_now, sleep_fn=_sleep)
    if not opened:
        debug_log.log("karambwan", "the deposit interface did not open")
    return bool(opened)


def _deposit_fish(ctx) -> bool:
    """Deposit-All a karambwan in the deposit grid (the recorded human's one click, -24 fish
    0.3s later). Repeats up to DEPOSIT_TRIES while fish remain."""
    for _ in range(DEPOSIT_TRIES):
        if _fish_count() == 0:
            return True
        child = _deposit_child((FISH_NAME,))
        if child is None:
            debug_log.log("karambwan", "karambwan not found in the deposit grid")
            return False
        mark = outcome_waits.mark(ctx)
        if not _click_option(ctx, child, DEPOSIT_ALL_ACTION, "deposit"):
            debug_log.log("karambwan", "Deposit-All did not land on the karambwan -- retrying")
            continue
        outcome_waits.await_pre(ctx, "INVENTORY_CHANGED", DEPOSIT_BUDGET_S,
                                confirm=lambda: _fish_count() == 0, since=mark, reaction=False,
                                now_fn=_now, sleep_fn=_sleep)
    return _fish_count() == 0


def _empty_barrel(ctx) -> bool:
    """Empty-to-bank the barrel in the deposit grid ("You empty the barrel."
    0.2s later in the recording). Confirmed on that chat line, or on the reader when it can
    tell; the inventory itself does not change."""
    child = _deposit_child(BARREL_NAMES)
    if child is None:
        debug_log.log("karambwan", "fish barrel not found in the deposit grid")
        return False
    mark = outcome_waits.mark(ctx)
    if not _click_option(ctx, child, BARREL_EMPTY_ACTIONS, "empty barrel"):
        debug_log.log("karambwan", "Empty-to-bank did not land on the barrel")
        return False
    emptied = outcome_waits.await_pre(
        ctx, {"CHAT_MESSAGE": outcome_waits.chat_has("empty the barrel")}, INTERFACE_BUDGET_S,
        confirm=lambda: _chat_since(ctx, mark, "empty the barrel") or barrel_room() == BARREL_CAPACITY,
        since=mark, reaction=False, now_fn=_now, sleep_fn=_sleep)
    if not emptied:
        debug_log.log("karambwan", "no confirmation that the barrel emptied -- taking the click")
    return True


def _chat_since(ctx, since, needle: str) -> bool:
    events = outcome_waits.events_of(ctx)
    if events is None or since is None:
        return False
    return any(t == "CHAT_MESSAGE" and needle in ((p or {}).get("message") or "").lower()
               for t, p in events.since(since))


def _close_deposit(ctx) -> None:
    mark = outcome_waits.mark(ctx)
    controller.key_tap(ctx.ser, "ESC")
    closed = outcome_waits.await_pre(ctx, {"WIDGET_CLOSED": lambda p: p.get("groupId") == DEPOSIT_GROUP},
                                     INTERFACE_BUDGET_S, confirm=lambda: not _deposit_open(), since=mark,
                                     reaction=False, now_fn=_now, sleep_fn=_sleep)
    if not closed:
        button = controller.get_widget(DEPOSIT_GROUP, DEPOSIT_CLOSE_CHILD)   # the recorded Close click
        if button.get("clickable"):
            controller.click_entity(ctx.ser, button, check_ui_blockers=False, simulate_mistakes=False)


def bank_phase(ctx, config: dict) -> bool:
    """At the gem mine: open the deposit chest, deposit every karambwan, Empty the barrel,
    close. True once nothing is left to deposit (derive_phase then says "cape")."""
    if not _deposit_open() and not _open_deposit(ctx):
        return False
    controller.human_delay(0.3, 0.6)
    if _fish_count() > 0 and not _deposit_fish(ctx):
        debug_log.log("karambwan", "deposit did not clear the karambwan -- retrying next tick")
        return False
    if not ctx.state.data.get("kf_trip_emptied"):
        if _empty_barrel(ctx):
            ctx.state.data["kf_trip_emptied"] = True
        else:
            n = ctx.state.data.get("kf_empty_fails", 0) + 1
            ctx.state.data["kf_empty_fails"] = n
            if n >= 3:
                # Nothing left to lose by carrying on: the barrel keeps storing catches until
                # it is full, and the next trip's Empty-to-bank clears whatever is left in it.
                debug_log.log("karambwan", "[!] could not Empty the barrel here -- carrying on")
                ctx.state.data["kf_trip_emptied"] = True
            else:
                return False
    ctx.state.data["kf_empty_fails"] = 0
    _close_deposit(ctx)
    return True


# --------------------------------------------------------------------------- the fairy ring

def _nearest_ring():
    best = None
    for o in controller.get_objects(radius=30) or []:
        if (o.get("name") or "").lower() != RING_NAME:
            continue
        if best is None or o.get("distance", 9999) < best.get("distance", 9999):
            best = o
    return best


def _rotate_dial(ctx, varbit: int, target: int, child_a: int, child_b: int) -> bool:
    """Turn one dial until its varbit reads `target`. Which of the two arrows increments the
    value is learned from the first click that changes it, then the shorter way is taken."""
    inc, dec = child_a, child_b
    learned = False
    v = controller.get_varbit(varbit)
    for _ in range(8):
        if v is None:
            return False
        if v == target:
            return True
        up, down = (target - v) % 4, (v - target) % 4
        child = inc if up <= down else dec
        w = controller.get_widget(RING_GROUP, child)
        if not w.get("clickable"):
            return False
        controller.click_entity(ctx.ser, w, check_ui_blockers=False, simulate_mistakes=False)
        _sleep(DIAL_CLICK_S)
        v2 = controller.get_varbit(varbit)
        if v2 is None or v2 == v:
            continue
        if not learned:
            learned = True
            if ((v2 - v) % 4 == 1) != (child == inc):
                inc, dec = dec, inc
        v = v2
    return v == target


def _dial_dkp(ctx, ring) -> bool:
    refresh = controller.object_refresh_target(ring.get("x"), ring.get("y"), ring.get("plane", 0), ring.get("id"))
    mark = outcome_waits.mark(ctx)
    if not controller.click_menu_action(ctx.ser, ring, OPT_CONFIGURE, refresh_target=refresh):
        return False
    opened = outcome_waits.await_pre(
        ctx, {"WIDGET_LOADED": lambda p: p.get("groupId") == RING_GROUP},
        INTERFACE_BUDGET_S + outcome_waits.walk_budget(ring),
        confirm=lambda: bool(controller.get_widget(RING_GROUP, RING_CONFIRM_CHILD).get("clickable")),
        since=mark, reaction=False, now_fn=_now, sleep_fn=_sleep)
    if not opened:
        debug_log.log("karambwan", "the fairy ring interface did not open")
        return False
    for varbit, target, a, b in RING_DIALS:
        if not _rotate_dial(ctx, varbit, target, a, b):
            debug_log.log("karambwan", f"could not set dial varbit {varbit} to {target}")
            return False
    confirm = controller.get_widget(RING_GROUP, RING_CONFIRM_CHILD)
    return bool(controller.click_entity(ctx.ser, confirm, check_ui_blockers=False, simulate_mistakes=False))


def ring_phase(ctx, config: dict) -> bool:
    """At the Legends' Guild: walk to BLR, take the ring to DKP -- "Last-destination (DKP)" in
    one click when offered, else Configure + dial DKP + confirm. True once standing at DKP."""
    ring = _nearest_ring()
    if ring is None or (ring.get("distance") or 99) > RING_CLICK_RANGE:
        debug_log.log("karambwan", "walking to the Legends' Guild fairy ring")
        travel.walk_to(ctx, TILE_BLR, goal_adjacent=True)
        return False
    refresh = controller.object_refresh_target(ring.get("x"), ring.get("y"), ring.get("plane", 0), ring.get("id"))
    mark = outcome_waits.mark(ctx)
    debug_log.log("karambwan", "fairy ring -> DKP")
    clicked = _click_option(ctx, ring, OPT_LAST_DEST_DKP, "fairy ring",
                            refresh_target=refresh, interface=False)
    if not clicked:
        debug_log.log("karambwan", "no Last-destination (DKP) entry -- dialling it")
        if not _dial_dkp(ctx, ring):
            return False
    t0 = _now()
    landed = _await_landing(ctx, mark, lambda: _player().get("regionId") == REGION_DKP,
                            RING_BUDGET_S + outcome_waits.walk_budget(ring))
    if not landed:
        debug_log.log("karambwan", "the fairy ring did not land at DKP within the budget")
    else:
        debug_log.log("karambwan", f"fairy ring: landed at DKP {_now() - t0:.1f}s after the click")
    return bool(landed)


# --------------------------------------------------------------------------- engine hooks

PHASES = {
    "fish": fish_phase,
    "to_bank": to_bank_phase,
    "bank": bank_phase,
    "cape": cape_phase,
    "ring": ring_phase,
}


def _stuck_watch(ctx, signature) -> None:
    data = ctx.state.data
    if data.get("kf_sig") != signature:
        data["kf_sig"] = signature
        data["kf_sig_since"] = _now()
        data["kf_sig_warned"] = False
        return
    still = _now() - data.get("kf_sig_since", _now())
    if still >= STALL_STOP_S:
        ctx.state.trigger_safety_stop(f"karambwan: no progress for {int(still)}s (state {signature})")
    elif still >= STALL_WARN_S and not data.get("kf_sig_warned"):
        data["kf_sig_warned"] = True
        debug_log.log("karambwan", f"[!] no progress for {int(still)}s (state {signature})")


def tick(ctx):
    config = ctx.state.config or DEFAULT_CONFIG
    data = ctx.state.data
    if not data.get("kf_checked"):
        if not _startup_ok(ctx):
            return
        data["kf_checked"] = True

    for _ in range(TICK_CHAIN_MAX):
        if ctx.state.safety_stopped or ctx.state.paused:
            return
        player = _player()
        inv = _inventory()
        free = _free_slots(inv)
        fish = _fish_count(inv)
        phase = derive_phase(player, free, fish, data, config)
        _stuck_watch(ctx, (phase, free, fish, player.get("regionId"), _underground(player)))
        if phase == "lost":
            ctx.state.trigger_safety_stop(
                f"karambwan: unexpected position ({player.get('x')},{player.get('y')}) region "
                f"{player.get('regionId')} -- start at DKP, the gem mine or the Legends' Guild")
            return
        if data.get("kf_phase") != phase:
            debug_log.log("karambwan", f"phase -> {phase}")
            data["kf_phase"] = phase
        if not PHASES[phase](ctx, config):
            return
        if ctx.state.break_due((config or {}).get("breaks") or {}):
            return
