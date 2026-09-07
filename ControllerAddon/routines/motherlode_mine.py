# routines/motherlode_mine.py
#
# Motherlode Mine, upstairs, in the pocket behind Mercy. Mine ore veins until the inventory is
# full, empty it into the hopper, and once the sack is full go down and run one collect/bank
# cycle at the sack + deposit box.
#
# Design: docs/superpowers/specs/2026-09-07-motherlode-mine-design.md. Written 2026-09-07 from
# the user's recording (recordings/2026-09-07_111533_motherload-mine.*, a PARTIAL loop), live
# bridge reads with the client standing in the mine, RuneLite's own MotherlodePlugin and the
# Plugin Hub "Motherlode Mine Improved" (tictac7x-motherlode) source the user pointed at.
#
# USER's decisions: mine "upstairs but behind the lady" (no rockfall to clear); fill the
# inventory entirely before each hopper trip; "mine until the sack is full, then one big
# collect/bank cycle"; and "ONLY repair broken strut if we cant deposit our ore. otherwise
# leave it broken. if we are lucky another player fixes it".
#
# THE MINING HALF IS gathering.py, UNCHANGED. This file builds a synthetic vein config and calls
# gathering.pick_target/gathering.gather directly, so it inherits the measured tick-perfect
# reaction (XP_GAINED / OBJECT_DESPAWNED, one HTTP confirm each), the pre-hover of the next vein
# and the walk-budgeted deadlines. It deliberately does NOT use gathering.tick: a full inventory
# here means "walk to the hopper", which is none of that function's three policies.
#
# Four MEASURED facts shape everything here:
#
#   * Ore veins are WallObjects (26661-26664), and a DEPLETED vein is a different id
#     (26665-26668) offering NO ACTIONS AT ALL. So gathering's existing "must offer the action"
#     filter already skips spent veins, and its "done when the id no longer stands on its tile"
#     is already the right depletion test.
#   * THE UPPER LEVEL SITS DIRECTLY ABOVE THE LOWER ONE AT THE SAME x/y, and /objects lists
#     both. It is not a plane and it is not a separate part of the map, so NO coordinate test
#     separates them -- world x/y, the static collision map and a reachability flood all answer
#     "same floor" about two tiles a storey apart. MEASURED the hard way on the first live run:
#     the routine mined happily upstairs, then spent 90s clicking a DOWNSTAIRS vein at
#     (3749,5685) from (3749,5684) with /last-action confirming a perfect "Mine Ore vein" every
#     time and nothing happening. USER, watching: "clicking lower level ore from highground".
#     RuneLite's own MotherlodePlugin has the same problem and answers it with TILE HEIGHT
#     (isUpstairs: Perspective.getTileHeight(...) < -490), so the bridge now reports tileHeight
#     on /objects and /player and that is the floor test here. The reachability flood stays, but
#     only for what it can actually answer: whether a wall is in the way.
#   * The conveyor is SLOW: pay-dirt leaves the inventory on the hopper click but reaches varbit
#     5558 seconds later. Deciding "is the sack full" off the raw varbit starts a trip whose
#     pay-dirt has nowhere to go, which is exactly what mlm-qol's SackTracker exists for.
#   * The wheel breaking does NOT stop the hopper accepting pay-dirt (the recording deposits 17
#     into a broken machine); it stops the sack FILLING. Hence the repair rule below.
#
# Run with:
#     python -u controller.py routines/motherlode_mine.py

import time

import controller
import debug_log
import gathering
import outcome_waits
import travel
import worldmap

LOG = "mlm"

# --- the mine, all MEASURED live 2026-09-07 --------------------------------------------------
REGION = 14936

VEIN_NAME = "Ore vein"                      # live 26661-26664; depleted 26665-26668, no actions
VEIN_ACTION = "Mine"
HOPPER_ID = 26674                           # (3748,5672) lower; (3755,5677) upper, 50 nuggets
HOPPER_ACTION = "Deposit"
LADDER_UP_ID = 19044                        # (3755,5673), offers Climb only from downstairs
LADDER_DOWN_ID = 19045                      # (3755,5674), offers Climb only from upstairs
LADDER_ACTION = "Climb"
SACK_ID = 26688                             # GroundObject (3748,5659)
SACK_ACTION = "Search"
DEPOSIT_BOX_ID = 25937                      # (3759,5664)
DEPOSIT_BOX_ACTION = "Deposit"
BROKEN_STRUT_ID = 26670                     # becomes 26669 once repaired; +126 Smithing each
STRUT_ACTION = "Hammer"

# Walk targets, used only to head toward something not yet in view.
TILE_SACK = {"x": 3748, "y": 5659, "plane": 0}
TILE_DEPOSIT_BOX = {"x": 3759, "y": 5664, "plane": 0}
TILE_LADDER = {"x": 3755, "y": 5673, "plane": 0}
TILE_LOWER_HOPPER = {"x": 3748, "y": 5672, "plane": 0}

# Varbits -- all served today by GET /varbit, no Java change.
VARBIT_SACK = 5558                          # pay-dirt in the sack
VARBIT_SACK_UPGRADED = 5556                 # 1 -> 189 capacity, else 108
VARBIT_UPSTAIRS = 2086                      # 1 while on the upper level
VARBIT_SUBSCRIPTIONS = [VARBIT_SACK, VARBIT_SACK_UPGRADED, VARBIT_UPSTAIRS]

SACK_SIZE = 108
SACK_SIZE_LARGE = 189

# RuneLite's MotherlodePlugin, verbatim: a tile rendered below this height is on the upper level.
UPPER_FLOOR_HEIGHT = -490

# The bank deposit box interface (group 192): 24 the inventory grid, 31 "Deposit inventory".
DEPOSIT_GROUP = 192
DEPOSIT_INV_CHILD = 24
DEPOSIT_ALL_CHILD = 31
DEPOSIT_ALL_ACTION = "Deposit-All"

PAYDIRT_NAME = "pay-dirt"
GEM_SACK_NAME = "gem sack"
HAMMER_NAME = "hammer"
PICKAXE_NAME = "pickaxe"
# Never banked when we deposit item by item (the hammer path). Substring match.
KEEP_ITEMS = (PICKAXE_NAME, HAMMER_NAME, GEM_SACK_NAME)

BROKEN_CHAT = "will need to be repaired"

# --- budgets -----------------------------------------------------------------------------------
REACH_RADIUS = 40
COLLISION_RADIUS = 14       # one /collision read per tick covers every candidate we filter
SNAPSHOT_MAX_AGE_S = 0.5
INTERFACE_BUDGET_S = 4.0
DEPOSIT_BUDGET_S = 3.0
CLIMB_BUDGET_S = 6.0
SEARCH_BUDGET_S = 5.0
REPAIR_BUDGET_S = 12.0
PENDING_MAX_S = 30.0        # a hopper deposit not reflected in varbit 5558 by now is written off
CHAIN_MAX_S = 40.0          # hand back to the engine loop at least this often
COLLECT_MAX_ROUNDS = 12     # a 189 sack is 7 fills; the rest is slack
DEPOSIT_MAX_STACKS = 10
NO_VEIN_STOP = 40
VEIN_FAIL_LIMIT = 2         # clicks on one vein that produce nothing before it is set aside
VEIN_BLOCK_S = 60.0         # ...and for how long
STALL_WARN_S = 90.0
STALL_STOP_S = 240.0
WALK_S_PER_TILE = 0.6

DEFAULT_CONFIG = {
    "enabled": True,
    # Veins within this many tiles of the ANCHOR (the tile the run started on) are ours.
    # Together with the reachability filter this is what keeps the routine in one pocket.
    "veinDistance": 8,
    # auto | never | always -- see _hopper_candidates. "never" until the upstairs hopper is
    # bought (50 golden nuggets from Prospector Percy; USER 2026-09-07: "need to unlock that
    # hopper we cant use it yet"), so the first live session spends no click on a fixture we
    # already know refuses us. Flip to "auto" the day it is bought and every ladder trip goes
    # away -- "auto" tries a same-floor hopper first and latches back to the ladder if the
    # deposit does not take.
    "upperHopper": "never",
    # Leave a broken machine to other players for this long before spending a hammer on it.
    "repairPatienceS": 180,
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
        # OFF: a full inventory is this routine's hopper trip, and check_safety runs BEFORE
        # tick() every iteration, so the engine would otherwise win the race.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

# The vein loop is gathering.py's; this profile is only the shape it needs.
PROFILE = gathering.Profile(
    key=LOG,
    skill="Mining",
    action=VEIN_ACTION.lower(),
    xp_field="miningXp",
    names_key="veinNames",
    distance_key="veinDistance",
    noun="vein",
    keep_items=(PICKAXE_NAME,),
    config_fetch=lambda: None,
    default_config=DEFAULT_CONFIG,
    full_inventory="stop",
    panel_section="Gathering (Mine)",
)


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


def get_config(bridge_config):
    return DEFAULT_CONFIG


# --- pure helpers ------------------------------------------------------------------------------

def sack_capacity(upgraded) -> int:
    """108, or 189 once the 200-nugget upgrade is bought (varbit 5556)."""
    return SACK_SIZE_LARGE if upgraded else SACK_SIZE


def sack_effective(sack, pending) -> int:
    """What the sack will hold once the conveyor catches up."""
    return max(0, int(sack or 0)) + max(0, int(pending or 0))


def mining_allowance(free_slots, held_paydirt, sack_eff, capacity) -> int:
    """How much MORE pay-dirt is worth mining before heading for the hopper.

    USER asked for "full inventory, always", and with a 108 sack and 27-pay-dirt loads that is
    exactly what happens -- 108 is four loads. The cap only bites when the sack sits on a number
    that is not a multiple of the load (the recording's sack was at 39), where a full load would
    overflow it and the excess pay-dirt is simply lost. Stopping a few short of a full inventory
    is much the cheaper of those two."""
    room = max(0, int(capacity) - int(sack_eff) - max(0, int(held_paydirt)))
    return max(0, min(max(0, int(free_slots)), room))


def derive_phase(*, holding_ore, inventory_full, held_paydirt, allowance, sack_eff, capacity,
                 collecting) -> str:
    """bank | collect | hopper | mine, from live state alone.

    `collecting` is the one latch and it only spans a collect cycle: emptying a full sack takes
    several inventories, and without it the first Search -- which drops the sack below full --
    would send us straight back up the ladder."""
    if holding_ore:
        return "bank"
    if collecting and sack_eff > 0:
        return "collect"
    if held_paydirt and (inventory_full or allowance <= 0):
        return "hopper"
    if not held_paydirt and sack_eff >= capacity:
        return "collect"
    return "mine"


def repair_due(broken_since, hammer, patience_s, now) -> bool:
    """USER: "ONLY repair broken strut if we cant deposit our ore. otherwise leave it broken. if
    we are lucky another player fixes it." So a broken machine is left alone until it has
    actually cost us `patience_s` of a sack that will not fill, and even then only if we hold a
    hammer."""
    if not hammer or not broken_since:
        return False
    return (now - broken_since) >= patience_s


# --- live reads --------------------------------------------------------------------------------

def _varbit(varbit_id, default=None):
    value = controller.get_varbit(varbit_id)
    return default if value is None else value


def _upstairs() -> bool:
    return bool(_varbit(VARBIT_UPSTAIRS, 0))


def _inventory():
    return controller.get_inventory() or []


def _count(items, needle) -> int:
    needle = needle.lower()
    return sum(1 for i in items if needle in (i.get("name") or "").lower())


def _held_paydirt(items=None) -> int:
    return _count(_inventory() if items is None else items, PAYDIRT_NAME)


def _ore_items(items=None):
    """Everything in the inventory that is neither kit nor pay-dirt -- i.e. what a Search of the
    sack just handed us."""
    items = _inventory() if items is None else items
    out = []
    for item in items:
        name = (item.get("name") or "").lower()
        if PAYDIRT_NAME in name or any(k in name for k in KEEP_ITEMS):
            continue
        out.append(item)
    return out


def _free_slots(items=None) -> int:
    items = _inventory() if items is None else items
    return max(0, getattr(controller, "INVENTORY_SIZE", 28) - len(items))


def _player_tile():
    player = controller.get_player() or {}
    if player.get("x") is None:
        return None
    return {"x": player["x"], "y": player["y"], "plane": player.get("plane", 0)}


def _objects(ids=None, action=None, fresh=False):
    out = []
    for obj in controller.get_objects(fresh=fresh) or []:
        if ids is not None and obj.get("id") not in ids:
            continue
        if action is not None:
            actions = obj.get("actions")
            if actions is not None and not any(action.lower() == (a or "").lower()
                                               for a in actions):
                continue
        out.append(obj)
    return sorted(out, key=lambda o: o.get("distance") if o.get("distance") is not None else 9999)


def _refresh(obj):
    return controller.object_refresh_target(obj.get("x"), obj.get("y"), obj.get("plane", 0),
                                            obj.get("id"))


def _walk_budget(obj):
    return ((obj or {}).get("distance") or 0) * WALK_S_PER_TILE


# --- state bookkeeping ---------------------------------------------------------------------------

def _d(ctx):
    return ctx.state.data


def snapshot(ctx, force=False):
    """The reachable set and one collision read, shared by every candidate filter this tick.

    can_reach fetches /collision itself when it is not given flags, which would be one HTTP call
    per vein; the pocket fits comfortably inside a single radius-14 read."""
    data = _d(ctx)
    cached = data.get("mlm_snapshot")
    if not force and cached and _now() - cached[0] < SNAPSHOT_MAX_AGE_S:
        return cached[1], cached[2]
    here = _player_tile()
    if here is None:
        return {}, None
    reach = worldmap.reachable_from(here["x"], here["y"], here.get("plane", 0),
                                    radius=REACH_RADIUS)
    tiles = controller.get_collision(here["x"], here["y"], here.get("plane", 0),
                                     radius=COLLISION_RADIUS) or []
    flags = {(t["x"], t["y"]): t.get("flags") for t in tiles} or None
    data["mlm_snapshot"] = (_now(), reach, flags)
    return reach, flags


def _reachable(obj, reach, flags) -> bool:
    """Can we work THIS object from where we stand? Reachability as a VETO, never as a
    requirement: an empty flood, or a client that cannot answer, means "cannot tell" and we
    behave exactly as we did before the filter existed.

    Used for VEINS, where the edge test is the point -- a vein is a WallObject and the wall it
    sits in is what decides whether the tile beside it can mine it."""
    if not reach:
        return True
    return travel.can_reach(obj, reach, flags) is not False


def is_upper(height) -> bool:
    """RuneLite's MotherlodePlugin.isUpstairs, verbatim: a tile rendered below
    UPPER_FLOOR_HEIGHT is on the upper level."""
    return height is not None and height < UPPER_FLOOR_HEIGHT


def same_floor(obj_height, player_height) -> bool:
    """Is this object on the floor we are standing on?

    THE ONLY WORKING FLOOR TEST IN THIS MINE. Coordinates cannot answer it -- the upper level is
    directly above the lower one -- and the first live run proved what happens without it: 90
    seconds of perfectly aimed, perfectly resolved "Mine Ore vein" clicks at a vein one storey
    below, with /last-action reporting success every time.

    An unknown height is treated as "same floor" only so a single missing field cannot make the
    routine refuse to work; _floor_ready stops the run up front when the client is not reporting
    heights at all, because guessing here is precisely the bug this replaced."""
    if obj_height is None or player_height is None:
        return True
    return is_upper(obj_height) == is_upper(player_height)


def _player_height():
    return (controller.get_player() or {}).get("tileHeight")


def _floor_ready(ctx) -> bool:
    """The bridge has to be reporting tileHeight before anything here is clicked."""
    if _player_height() is not None:
        return True
    ctx.state.trigger_safety_stop(
        "motherlode: this client does not report tileHeight on /player -- rebuild it with "
        "./gradlew.bat run. Without it the mine's two floors cannot be told apart and the "
        "routine mines through the floor")
    return False


def _pending(ctx) -> int:
    """Pay-dirt deposited into the hopper but not yet counted by varbit 5558."""
    data = _d(ctx)
    at = data.get("mlm_pending_at")
    if at is not None and _now() - at > PENDING_MAX_S:
        data["mlm_pending"] = 0
        data.pop("mlm_pending_at", None)
    return int(data.get("mlm_pending") or 0)


def _note_sack(ctx, sack):
    """Retire `pending` as the conveyor delivers, and take a rising sack as proof the machine is
    working again."""
    data = _d(ctx)
    previous = data.get("mlm_sack_seen")
    data["mlm_sack_seen"] = sack
    if previous is None or sack <= previous:
        return
    data["mlm_pending"] = max(0, int(data.get("mlm_pending") or 0) - (sack - previous))
    data.pop("mlm_broken_since", None)


def sack_state(ctx):
    """(raw sack, effective sack, capacity)."""
    sack = int(_varbit(VARBIT_SACK, 0) or 0)
    _note_sack(ctx, sack)
    capacity = sack_capacity(_varbit(VARBIT_SACK_UPGRADED, 0))
    return sack, sack_effective(sack, _pending(ctx)), capacity


# --- startup -------------------------------------------------------------------------------------

def setup(ctx):
    data = _d(ctx)
    data["mlm_started"] = _now()
    here = _player_tile()
    if here is not None:
        data["mlm_anchor"] = here
        data["mlm_anchor_upstairs"] = _upstairs()
        debug_log.log(LOG, f"anchor set to ({here['x']},{here['y']}) "
                           f"{'upstairs' if data['mlm_anchor_upstairs'] else 'downstairs'}")


def _wielding_pickaxe() -> bool:
    player = controller.get_player() or {}
    return PICKAXE_NAME in (player.get("weaponName") or "").lower()


def _startup_ok(ctx) -> bool:
    data = _d(ctx)
    if data.get("mlm_checked"):
        return True
    items = _inventory()
    if not items and not _wielding_pickaxe():
        # The first logged-in tick after an auto-login serves an empty inventory: ask again.
        return False
    if not _wielding_pickaxe() and _count(items, PICKAXE_NAME) == 0:
        ctx.state.trigger_safety_stop("motherlode: no pickaxe wielded or carried")
        return False
    region = (controller.get_player() or {}).get("regionId")
    if region != REGION:
        ctx.state.trigger_safety_stop(
            f"motherlode: start inside the Motherlode Mine (region {REGION}, saw {region})")
        return False
    data["mlm_hammer"] = _count(items, HAMMER_NAME) > 0
    if not data["mlm_hammer"]:
        debug_log.log(LOG, "no hammer carried -- strut repairs are off for this run; a broken "
                           "machine will be left for other players")
    if data.get("mlm_anchor") is None:
        setup(ctx)
    sack, _effective, capacity = sack_state(ctx)
    debug_log.log(LOG, f"sack {sack}/{capacity}, {_free_slots(items)} free slots, "
                       f"{'upstairs' if _upstairs() else 'downstairs'}")
    data["mlm_checked"] = True
    return True


# --- events -----------------------------------------------------------------------------------------

def on_event(ctx, event_type, payload):
    if event_type != "CHAT_MESSAGE":
        return
    message = ((payload or {}).get("message") or "").lower()
    if BROKEN_CHAT in message and not _d(ctx).get("mlm_broken_since"):
        _d(ctx)["mlm_broken_since"] = _now()
        debug_log.log(LOG, "the machine is broken -- our pay-dirt still goes in, but the sack "
                           "will stop filling. Leaving it for now.")


# --- the vein loop -----------------------------------------------------------------------------------

def _vein_config(ctx):
    """gathering's own config shape. The distance here is from the PLAYER and only bounds
    gathering's pre-hover of the next vein; the click target is chosen by pick_vein, which also
    bounds by the anchor and by reachability."""
    distance = (ctx.state.config or {}).get("veinDistance", DEFAULT_CONFIG["veinDistance"])
    return {"veinNames": [VEIN_NAME], "veinDistance": max(4, int(distance) + 6)}


def _blocked_veins(ctx):
    """Veins that took a click and did nothing, and when they come back into play."""
    blocked = _d(ctx).setdefault("mlm_blocked", {})
    now = _now()
    for tile, until in list(blocked.items()):
        if until <= now:
            blocked.pop(tile, None)
    return blocked


def note_vein_failure(ctx, target, outcome):
    """A vein that swallows clicks is set aside rather than re-picked forever.

    The belt to the floor test's braces, and on its own enough to have escaped the first live
    run's 90-second loop: whatever the reason a particular vein will not be mined from where we
    can stand -- wrong side of a wall object, a stale scene, another floor on an un-rebuilt
    client -- two dead clicks is proof enough to go and mine a different one. Any progress at
    all clears the count, so a vein that simply took a while is not penalised."""
    data = _d(ctx)
    fails = data.setdefault("mlm_vein_fails", {})
    tile = gathering.target_tile(target)
    key = str(tile)
    if outcome == "depleted":
        fails.pop(key, None)
        return
    n = fails.get(key, 0) + 1
    fails[key] = n
    if n >= VEIN_FAIL_LIMIT:
        fails.pop(key, None)
        _blocked_veins(ctx)[key] = _now() + VEIN_BLOCK_S
        debug_log.log(LOG, f"the vein at {tile} took {n} clicks and gave nothing -- leaving it "
                           f"for {VEIN_BLOCK_S:.0f}s")


def pick_vein(ctx, reach, flags, exclude=(), objects=None):
    """The nearest live vein that is on OUR FLOOR, not set aside, reachable from where we stand,
    and inside `veinDistance` of the anchor.

    The floor test is the load-bearing one and it is tile height, not coordinates -- see
    same_floor and this module's header. The anchor bound keeps us in the pocket behind Mercy;
    the reachability bound answers only whether a wall is in the way."""
    config = ctx.state.config or {}
    anchor = _d(ctx).get("mlm_anchor")
    limit = int(config.get("veinDistance", DEFAULT_CONFIG["veinDistance"]))
    excluded = set(exclude)
    blocked = _blocked_veins(ctx)
    height = _player_height()
    best = None
    for obj in (controller.get_objects() if objects is None else objects) or []:
        if (obj.get("name") or "").lower() != VEIN_NAME.lower():
            continue
        actions = obj.get("actions")
        # A depleted vein is a different id offering nothing -- this is what skips it.
        if actions is not None and not any((a or "").lower() == VEIN_ACTION.lower()
                                           for a in actions):
            continue
        if not obj.get("visible", True):
            continue
        tile = (obj.get("x"), obj.get("y"), obj.get("plane"))
        if tile in excluded or str(tile) in blocked:
            continue
        if not same_floor(obj.get("tileHeight"), height):
            continue
        if anchor is not None and max(abs(obj.get("x", 0) - anchor["x"]),
                                      abs(obj.get("y", 0) - anchor["y"])) > limit:
            continue
        if not _reachable(obj, reach, flags):
            continue
        distance = obj.get("distance")
        distance = 9999 if distance is None or distance < 0 else distance
        if best is None or distance < best[0]:
            best = (distance, obj)
    return best[1] if best else None


def _allowance(ctx, items=None):
    items = _inventory() if items is None else items
    _sack, effective, capacity = sack_state(ctx)
    return mining_allowance(_free_slots(items), _held_paydirt(items), effective, capacity)


def mine_phase(ctx):
    reach, flags = snapshot(ctx)
    target = pick_vein(ctx, reach, flags)
    data = _d(ctx)
    if target is None:
        n = data.get("mlm_no_vein", 0) + 1
        data["mlm_no_vein"] = n
        if n % 5 == 1:
            debug_log.log(LOG, "no reachable live vein near the anchor -- waiting for a respawn")
        if n >= NO_VEIN_STOP:
            ctx.state.trigger_safety_stop("motherlode: no reachable ore vein near the anchor")
        return
    data["mlm_no_vein"] = 0

    started = _now()
    worked = []
    while target is not None and not ctx.state.safety_stopped:
        if _now() - started >= CHAIN_MAX_S:
            return
        outcome, _next = gathering.gather(PROFILE, ctx, target, _vein_config(ctx), exclude=worked)
        note_vein_failure(ctx, target, outcome)
        if outcome != "depleted":
            return          # hand back so the engine gets its safety/break turn
        worked.append(gathering.target_tile(target))
        if _allowance(ctx) <= 0:
            return          # inventory full, or the sack has no room for more
        controller.human_delay(*gathering.NEXT_CLICK_DELAY)
        reach, flags = snapshot(ctx)
        target = pick_vein(ctx, reach, flags, exclude=worked,
                           objects=controller.get_objects(fresh=True))


# --- ladders -------------------------------------------------------------------------------------------

def climb(ctx, up) -> bool:
    """Climb to the other floor, confirmed on varbit 2086 rather than on the click.

    The two ladders sit a tile apart and each offers Climb only from the floor it leads away
    from, so the id plus the action tells them apart with no geometry."""
    if _upstairs() == bool(up):
        return True
    wanted = LADDER_UP_ID if up else LADDER_DOWN_ID
    ladder = next((o for o in _objects(ids={wanted}, action=LADDER_ACTION)
                   if same_floor(o.get("tileHeight"), _player_height())), None)
    if ladder is None:
        debug_log.log(LOG, f"no {'up' if up else 'down'} ladder in view -- walking to it")
        travel.walk_to(ctx, TILE_LADDER, goal_adjacent=True)
        return False
    mark = outcome_waits.mark(ctx)
    if not controller.click_entity(ctx.ser, ladder, refresh_target=_refresh(ladder),
                                   expected_action=LADDER_ACTION):
        return False
    arrived = outcome_waits.await_pre(
        ctx, {"VARBIT_CHANGED": outcome_waits.varbit_in([VARBIT_UPSTAIRS])},
        CLIMB_BUDGET_S + _walk_budget(ladder),
        confirm=lambda: (_upstairs() == bool(up)) or None,
        since=mark, reaction=False, now_fn=_now, sleep_fn=_sleep)
    if arrived:
        debug_log.log(LOG, f"climbed {'up' if up else 'down'}")
        return True
    debug_log.log(LOG, f"the climb {'up' if up else 'down'} did not register -- retrying")
    return False


# --- the hopper ------------------------------------------------------------------------------------------

def _hopper_candidates(ctx):
    """Reachable hoppers, nearest first, honouring the upperHopper policy.

    Default "auto": whatever is reachable on our own floor is tried first, and a deposit that
    leaves the pay-dirt in hand latches mlm_upper_locked for the session. The upper hopper costs
    50 golden nuggets and is not bought yet, so today that is one wasted click per run; the day
    it is bought, every ladder trip disappears with no change here."""
    policy = str((ctx.state.config or {}).get("upperHopper",
                                              DEFAULT_CONFIG["upperHopper"])).lower()
    height = _player_height()
    hoppers = [h for h in _objects(ids={HOPPER_ID}, action=HOPPER_ACTION)
               if same_floor(h.get("tileHeight"), height)]
    if policy != "always" and (policy == "never" or _d(ctx).get("mlm_upper_locked")):
        # Anything reachable from upstairs IS the upper hopper; the lower one needs the ladder.
        return [] if _upstairs() else hoppers
    return hoppers


def hopper_phase(ctx) -> bool:
    """Empty the held pay-dirt into a hopper. True once there is none left to deposit."""
    held = _held_paydirt()
    if held <= 0:
        return True
    hopper = next(iter(_hopper_candidates(ctx)), None)
    if hopper is None:
        if _upstairs():
            climb(ctx, up=False)
            return False
        debug_log.log(LOG, "no hopper in view -- walking toward it")
        travel.walk_to(ctx, TILE_LOWER_HOPPER, goal_adjacent=True)
        return False

    upper = _upstairs()
    mark = outcome_waits.mark(ctx)
    if not controller.click_entity(ctx.ser, hopper, refresh_target=_refresh(hopper),
                                   expected_action=HOPPER_ACTION):
        return False
    deposited = outcome_waits.await_pre(
        ctx, "INVENTORY_CHANGED", DEPOSIT_BUDGET_S + _walk_budget(hopper),
        confirm=lambda: (_held_paydirt() == 0) or None, since=mark, reaction=False,
        now_fn=_now, sleep_fn=_sleep)
    if deposited:
        data = _d(ctx)
        data["mlm_pending"] = int(data.get("mlm_pending") or 0) + held
        data["mlm_pending_at"] = _now()
        debug_log.log(LOG, f"deposited {held} pay-dirt at the "
                           f"{'upper' if upper else 'lower'} hopper")
        return True
    if upper and not _d(ctx).get("mlm_upper_locked"):
        # Pay-dirt still in hand after clicking the upstairs hopper: it is the locked one.
        _d(ctx)["mlm_upper_locked"] = True
        debug_log.log(LOG, "the upper hopper would not take the pay-dirt -- still locked "
                           "(50 golden nuggets from Percy). Using the ladder and the lower one.")
    return False


# --- the sack and the deposit box ---------------------------------------------------------------------------

def _deposit_open() -> bool:
    return bool((controller.get_widget(DEPOSIT_GROUP, DEPOSIT_INV_CHILD) or {}).get("clickable"))


def _deposit_children():
    # get_widget_children returns the LIST of populated children (karambwan_fishing's live crash
    # 2026-09-06 was reading it as {"children": [...]}); tolerate both.
    data = controller.get_widget_children(DEPOSIT_GROUP, DEPOSIT_INV_CHILD) or []
    if isinstance(data, dict):
        data = data.get("children") or []
    return [c for c in data if isinstance(c, dict) and c.get("itemName")]


def search_sack(ctx) -> bool:
    """Search the sack: the game hands over min(free slots, sack) ore."""
    sack = next((o for o in _objects(ids={SACK_ID}, action=SACK_ACTION)
                 if same_floor(o.get("tileHeight"), _player_height())), None)
    if sack is None:
        debug_log.log(LOG, "the sack is not in view -- walking to it")
        travel.walk_to(ctx, TILE_SACK, goal_adjacent=True)
        return False
    before = len(_inventory())
    mark = outcome_waits.mark(ctx)
    if not controller.click_entity(ctx.ser, sack, refresh_target=_refresh(sack),
                                   expected_action=SACK_ACTION):
        return False
    got = outcome_waits.await_pre(
        ctx, "INVENTORY_CHANGED", SEARCH_BUDGET_S + _walk_budget(sack),
        confirm=lambda: (len(_inventory()) > before) or None, since=mark, reaction=False,
        now_fn=_now, sleep_fn=_sleep)
    if not got:
        debug_log.log(LOG, "no ore came out of the sack")
    return bool(got)


def _open_deposit_box(ctx) -> bool:
    if _deposit_open():
        return True
    box = next((o for o in _objects(ids={DEPOSIT_BOX_ID}, action=DEPOSIT_BOX_ACTION)
                if same_floor(o.get("tileHeight"), _player_height())), None)
    if box is None:
        debug_log.log(LOG, "the deposit box is not in view -- walking to it")
        travel.walk_to(ctx, TILE_DEPOSIT_BOX, goal_adjacent=True)
        return False
    mark = outcome_waits.mark(ctx)
    if not controller.click_entity(ctx.ser, box, refresh_target=_refresh(box),
                                   expected_action=DEPOSIT_BOX_ACTION):
        return False
    opened = outcome_waits.await_pre(
        ctx, {"WIDGET_LOADED": lambda p: p.get("groupId") == DEPOSIT_GROUP},
        INTERFACE_BUDGET_S + _walk_budget(box), confirm=lambda: _deposit_open() or None,
        since=mark, reaction=False, now_fn=_now, sleep_fn=_sleep)
    if not opened:
        debug_log.log(LOG, "the deposit box interface did not open")
    return bool(opened)


def _deposit_inventory(ctx) -> bool:
    """The recording's single click on "Deposit inventory" (192.31). check_ui_blockers=False and
    no simulated misclick: banker.py's interface-click rule."""
    button = controller.get_widget(DEPOSIT_GROUP, DEPOSIT_ALL_CHILD)
    if not button or not button.get("clickable"):
        debug_log.log(LOG, "the Deposit inventory button is not clickable")
        return False
    mark = outcome_waits.mark(ctx)
    if not controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False,
                                   simulate_mistakes=False):
        return False
    done = outcome_waits.await_pre(
        ctx, "INVENTORY_CHANGED", DEPOSIT_BUDGET_S,
        confirm=lambda: (not _ore_items()) or None, since=mark, reaction=False,
        now_fn=_now, sleep_fn=_sleep)
    return bool(done)


def _deposit_each_ore(ctx) -> bool:
    """One Deposit-All per ore stack, so a carried hammer is never banked."""
    for _ in range(DEPOSIT_MAX_STACKS):
        remaining = {(i.get("name") or "").lower() for i in _ore_items()}
        if not remaining:
            return True
        child = next((c for c in _deposit_children()
                      if (c.get("itemName") or "").lower() in remaining), None)
        if child is None:
            debug_log.log(LOG, "an ore stack is not in the deposit grid")
            return False
        name = (child.get("itemName") or "").lower()
        mark = outcome_waits.mark(ctx)
        clicked = controller.click_entity(ctx.ser, child, offset=(0, 0), check_ui_blockers=False,
                                          simulate_mistakes=False,
                                          expected_action=DEPOSIT_ALL_ACTION)
        if not clicked:
            clicked = controller.click_menu_action(ctx.ser, child, DEPOSIT_ALL_ACTION,
                                                   offset=(0, 0), check_ui_blockers=False)
        if not clicked:
            debug_log.log(LOG, f"could not Deposit-All the {name}")
            return False
        outcome_waits.await_pre(
            ctx, "INVENTORY_CHANGED", DEPOSIT_BUDGET_S,
            confirm=lambda n=name: (n not in {(i.get("name") or "").lower()
                                              for i in _ore_items()}) or None,
            since=mark, reaction=False, now_fn=_now, sleep_fn=_sleep)
    return not _ore_items()


def bank_phase(ctx) -> bool:
    """Holding ore: get it into the deposit box.

    WITHOUT A HAMMER that is the recording's one click on Deposit inventory, and the user's
    existing Deposit Lock keeps the open gem sack. WITH one it is a Deposit-All per ore stack
    instead -- Deposit-inventory would bank the hammer, and locking it would mean driving the
    lock interface."""
    if not _ore_items():
        return True
    if _upstairs() and not climb(ctx, up=False):
        return False
    if not _open_deposit_box(ctx):
        return False
    had_gem_sack = _count(_inventory(), GEM_SACK_NAME) > 0
    ok = _deposit_each_ore(ctx) if _d(ctx).get("mlm_hammer") else _deposit_inventory(ctx)
    if ok and had_gem_sack and _count(_inventory(), GEM_SACK_NAME) == 0:
        debug_log.log(LOG, "[!] the open gem sack was banked -- the deposit box's Deposit Lock "
                           "is no longer set on it")
    return ok


def collect_phase(ctx):
    """One big collect/bank cycle: empty the sack a Search at a time, banking between fills, then
    go back up to the veins. USER's choice over a steady drip."""
    data = _d(ctx)
    data["mlm_collecting"] = True
    if _held_paydirt() and not hopper_phase(ctx):
        return          # never carry pay-dirt into the sack trip: the ore needs the slots
    if not climb(ctx, up=False):
        return
    if _maybe_repair(ctx):
        return

    started = _now()
    for _ in range(COLLECT_MAX_ROUNDS):
        if ctx.state.safety_stopped or _now() - started >= CHAIN_MAX_S:
            return
        if _ore_items():
            if not bank_phase(ctx):
                return
            continue
        sack, _effective, _capacity = sack_state(ctx)
        if sack <= 0:
            break
        if not search_sack(ctx):
            return

    sack, _effective, _capacity = sack_state(ctx)
    if sack > 0 or _ore_items():
        return          # more rounds next tick
    data["mlm_collecting"] = False
    data["mlm_pending"] = 0
    debug_log.log(LOG, "sack emptied -- back to the veins")
    _return_to_anchor(ctx)


def _return_to_anchor(ctx):
    anchor = _d(ctx).get("mlm_anchor")
    if anchor is None:
        return
    if _d(ctx).get("mlm_anchor_upstairs", True) and not climb(ctx, up=True):
        return
    travel.walk_to(ctx, anchor, goal_adjacent=False)


# --- repairs -------------------------------------------------------------------------------------------------

def _maybe_repair(ctx) -> bool:
    """True when this tick was spent repairing. Only ever runs downstairs, where the struts are,
    and only once a broken machine has actually cost us -- see repair_due."""
    config = ctx.state.config or {}
    if not repair_due(_d(ctx).get("mlm_broken_since"), bool(_d(ctx).get("mlm_hammer")),
                      float(config.get("repairPatienceS", DEFAULT_CONFIG["repairPatienceS"])),
                      _now()):
        return False
    strut = next((o for o in _objects(ids={BROKEN_STRUT_ID}, action=STRUT_ACTION)
                  if same_floor(o.get("tileHeight"), _player_height())), None)
    if strut is None:
        _d(ctx).pop("mlm_broken_since", None)       # someone else got there first
        return False
    tile = (strut.get("x"), strut.get("y"), strut.get("plane", 0))
    mark = outcome_waits.mark(ctx)
    if not controller.click_entity(ctx.ser, strut, refresh_target=_refresh(strut),
                                   expected_action=STRUT_ACTION):
        return True
    fixed = outcome_waits.await_pre(
        ctx, {"XP_GAINED": lambda p: p.get("skill") == "Smithing",
              "OBJECT_DESPAWNED": lambda p: (p.get("x"), p.get("y"), p.get("plane")) == tile},
        REPAIR_BUDGET_S + _walk_budget(strut),
        confirm=lambda: (controller.get_object_at(tile[0], tile[1], tile[2],
                                                  BROKEN_STRUT_ID) is None) or None,
        since=mark, reaction=False, now_fn=_now, sleep_fn=_sleep)
    if fixed:
        debug_log.log(LOG, "repaired a broken strut")
        _d(ctx).pop("mlm_broken_since", None)
    return True


# --- stuck watch ----------------------------------------------------------------------------------------------

def _progress_signature(ctx, phase):
    here = _player_tile() or {}
    return (phase, here.get("x"), here.get("y"), len(_inventory()),
            int(_varbit(VARBIT_SACK, 0) or 0))


def _stuck_watch(ctx, phase):
    data = _d(ctx)
    signature = _progress_signature(ctx, phase)
    if signature != data.get("mlm_sig"):
        data["mlm_sig"] = signature
        data["mlm_sig_at"] = _now()
        data.pop("mlm_warned", None)
        return
    stalled = _now() - (data.get("mlm_sig_at") or _now())
    if stalled >= STALL_STOP_S:
        ctx.state.trigger_safety_stop(
            f"motherlode: nothing changed for {stalled:.0f}s in the {phase} phase")
    elif stalled >= STALL_WARN_S and not data.get("mlm_warned"):
        data["mlm_warned"] = True
        debug_log.log(LOG, f"[!] mlm: STUCK? nothing changed for {stalled:.0f}s "
                           f"in the {phase} phase")


# --- the tick --------------------------------------------------------------------------------------------------

def phase_of(ctx):
    items = _inventory()
    held = _held_paydirt(items)
    _sack, effective, capacity = sack_state(ctx)
    allowance = mining_allowance(_free_slots(items), held, effective, capacity)
    return derive_phase(holding_ore=bool(_ore_items(items)),
                        inventory_full=_free_slots(items) <= 0,
                        held_paydirt=held, allowance=allowance,
                        sack_eff=effective, capacity=capacity,
                        collecting=bool(_d(ctx).get("mlm_collecting")))


def tick(ctx):
    if not _startup_ok(ctx) or not _floor_ready(ctx):
        return
    phase = phase_of(ctx)
    _stuck_watch(ctx, phase)
    if ctx.state.safety_stopped:
        return
    if phase != _d(ctx).get("mlm_phase"):
        _d(ctx)["mlm_phase"] = phase
        debug_log.log(LOG, f"phase -> {phase}")
    if phase == "bank":
        bank_phase(ctx)
    elif phase == "collect":
        collect_phase(ctx)
    elif phase == "hopper":
        hopper_phase(ctx)
    else:
        mine_phase(ctx)


def before_break(ctx):
    """Put the held pay-dirt in the hopper before a break starts, so a break never begins with a
    full inventory the next tick would only have to walk off anyway."""
    if _held_paydirt() <= 0:
        return
    debug_log.log(LOG, "break due -- emptying the inventory into the hopper first")
    for _ in range(4):
        if hopper_phase(ctx) or ctx.state.safety_stopped:
            return
