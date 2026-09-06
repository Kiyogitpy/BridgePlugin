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


def _dismiss_dialog(ctx) -> None:
    """A level-up (or any "click here to continue") interrupts fishing; the engine's own
    check_stray_dialog cannot run while fish_wait holds the tick."""
    if (controller.get_state() or {}).get("dialogOpen"):
        controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.3, 0.6)


def fish_wait(ctx) -> str:
    """Hold the tick while the player fishes. Returns "full" (no free slot), "idle" (the player
    stood still IDLE_RECLICK_S with room left -- re-click the spot), "no_bait", or "timeout".
    INVENTORY_CHANGED wakes the check on every catch; between catches the wait is spent in
    human-length still segments with a resting-hand fidget, like every other long wait here."""
    deadline = _now() + FISH_MAX_S
    idle_since = None
    while _now() < deadline:
        inv = _inventory()
        if _free_slots(inv) <= 0:
            return "full"
        if not _exact(inv, BAIT_NAME):
            return "no_bait"
        _dismiss_dialog(ctx)
        if _idle():
            idle_since = idle_since or _now()
            if _now() - idle_since >= IDLE_RECLICK_S:
                return "idle"
        else:
            idle_since = None
        chunk = min(outcome_waits.still_segment_s(), max(0.1, deadline - _now()))
        signals = {"INVENTORY_CHANGED": None, "CHAT_MESSAGE": outcome_waits.chat_has(FULL_CHAT),
                   "ANIMATION_CHANGED": lambda p: p.get("animation") == -1}
        outcome_waits.await_pre(ctx, signals, chunk, confirm=lambda: _free_slots() <= 0,
                                reaction=False, now_fn=_now, sleep_fn=_sleep)
        if deadline - _now() > 1.0:
            controller.fidget_once()
    return "timeout"


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


def worn_teleport(ctx, child: int, option: str, landed, what: str) -> bool:
    """Right-click a worn item through the equipment interface and pick `option`; True once
    `landed()` holds (awaited on PLAYER_STOPPED/MOVING, TELEPORT_BUDGET_S)."""
    if not controller.open_equipment_tab(ctx.ser):
        debug_log.log("karambwan", "could not open the equipment tab")
        return False
    widget = controller.get_widget(EQUIP_GROUP, child)
    if not widget.get("clickable"):
        debug_log.log("karambwan", f"equipment slot {EQUIP_GROUP}.{child} is not clickable")
        return False
    mark = outcome_waits.mark(ctx)
    debug_log.log("karambwan", f"{what}: {option}")
    if not controller.click_menu_action(ctx.ser, widget, option, check_ui_blockers=False, offset=(0, 0)):
        debug_log.log("karambwan", f"{what}: no '{option}' entry on the worn item's menu")
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
    """Right-click a karambwan in the deposit grid -> Deposit-All (the recorded human's one
    click, -24 fish 0.3s later). Repeats up to DEPOSIT_TRIES while fish remain."""
    for _ in range(DEPOSIT_TRIES):
        if _fish_count() == 0:
            return True
        child = _deposit_child((FISH_NAME,))
        if child is None:
            debug_log.log("karambwan", "karambwan not found in the deposit grid")
            return False
        mark = outcome_waits.mark(ctx)
        if not controller.click_menu_action(ctx.ser, child, DEPOSIT_ALL_ACTION, check_ui_blockers=False,
                                            offset=(0, 0)):
            debug_log.log("karambwan", "no Deposit-All entry on the karambwan -- retrying")
            continue
        outcome_waits.await_pre(ctx, "INVENTORY_CHANGED", DEPOSIT_BUDGET_S,
                                confirm=lambda: _fish_count() == 0, since=mark, reaction=False,
                                now_fn=_now, sleep_fn=_sleep)
    return _fish_count() == 0


def _empty_barrel(ctx) -> bool:
    """Right-click the barrel in the deposit grid -> Empty-to-bank ("You empty the barrel."
    0.2s later in the recording). Confirmed on that chat line, or on the reader when it can
    tell; the inventory itself does not change."""
    child = _deposit_child(BARREL_NAMES)
    if child is None:
        debug_log.log("karambwan", "fish barrel not found in the deposit grid")
        return False
    mark = outcome_waits.mark(ctx)
    if not controller.click_menu_action(ctx.ser, child, BARREL_EMPTY_ACTIONS, check_ui_blockers=False,
                                        offset=(0, 0)):
        debug_log.log("karambwan", "no Empty-to-bank entry on the barrel here")
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
    clicked = controller.click_menu_action(ctx.ser, ring, OPT_LAST_DEST_DKP, refresh_target=refresh)
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
