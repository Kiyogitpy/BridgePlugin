"""Wintertodt -- chop, fletch to kindling, feed, on a group/official world.

Design: docs/superpowers/specs/2026-09-08-wintertodt-design.md
NOT YET RUN LIVE. Every budget below is a first-draft guess; the spec's live-verification
checklist is the first session's worklist.

Three facts shape this routine:

1. Every tool is FREE from crates in the arena (axe, knife, tinderbox, hammer, concoctions),
   and USER chose never to search the Reward Cart, so there is NO banking and NO loadout. The
   routine never leaves region 6462.
2. Chopping, fletching and feeding are CONTINUOUS actions that run until the game interrupts
   them with a chat line. One inventory is about three clicks, so this is mostly an interrupt
   handler rather than a click loop.
3. Fletching is a deliberate POINTS-over-XP trade (USER's choice). The wiki is explicit that
   fletching is SLOWER Firemaking XP; it buys reward-cart rolls. A low FM/hr is the design, not
   a bug.

The decision layer is wintertodt_state.py and is pure; this file does the clicking.
"""

import time

import controller
import debug_log
import outcome_waits
import travel
import wintertodt_ids as w
import wintertodt_state as ws

LOG = "wintertodt"

VARBIT_SUBSCRIPTIONS = [w.VARBIT_WARMTH, w.VARBIT_ROUND_TIMER, w.VARBIT_AREA_ATTACK]

CLICK_RANGE = 10                 # walk first, click close (motherlode's measured rule)
SNOW_MEMORY_S = 3.0              # how long a recorded snowfall tile stays believed

DEFAULT_CONFIG = {
    "preferredBrazier": "SOUTH_WEST",     # the conventional group brazier -- UNVERIFIED
    "drinkAtWarmth": 45,                  # a dose restores 30%, so 45 cannot overheal
    "minDoses": 4,                        # one potion in reserve
    "takeConcoctions": 10,                # one Take-10 click per restock
    "fletch": True,                       # USER's choice; False = the faster-XP roots method
    "feedRootsNearRoundEnd": True,
    "feedRootsBelowEnergy": 15,           # what "nearly over" means -- a guess, fit it live
    "dodgeSnowfall": True,
    "roundEndReads": 3,                   # SUSTAINED: one inactive read is transitional
    "safety": {"stopOnInventoryFull": False},   # a full inventory is normal here, not a fault
}


def get_config(bridge_config):
    """No ControllerConfig section: this routine reads nothing from the bridge's /config.

    Same pattern as gemstone_crab.py and mining.py, which CLAUDE.md says needs nothing beyond
    the RoutineChoice enum entry and the ROUTINE_FILES key."""
    return DEFAULT_CONFIG


# --- module clock (the fake-clock fixture patches these) ----------------------------------

def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


# --- live reads ---------------------------------------------------------------------------

def _reader():
    """GET /api/minigames/wintertodt -- the whole HUD in one read, no client rebuild needed."""
    return controller.get_api("minigames", "wintertodt") or {}


def _inventory():
    return controller.get_inventory() or []


def _objects(ids=None, action=None, fresh=False):
    """Scene objects filtered by ID and action -- NEVER by name.

    Nine objects in this arena share three names (both the unlit and broken braziers are
    "Brazier"; six things are called "Crate"). Matching on name picks the wrong one."""
    out = []
    for obj in (controller.get_objects(fresh=fresh) or []):
        if ids is not None and obj.get("id") not in ids:
            continue
        if action is not None:
            actions = [a for a in (obj.get("actions") or []) if a]
            if not any(action.lower() == str(a).lower() for a in actions):
                continue
        out.append(obj)
    return out


def _nearest(objs):
    objs = [o for o in (objs or []) if o is not None]
    if not objs:
        return None
    return min(objs, key=lambda o: o.get("distance") if o.get("distance") is not None else 1e9)


def _refresh(obj):
    """A refresh_target so a click re-aims while the player walks -- CLAUDE.md's standing rule
    that a click's DEPARTURE is aimed too, not only its landing."""
    if not obj:
        return None
    return controller.object_refresh_target(obj.get("x"), obj.get("y"),
                                            obj.get("plane", 0), obj.get("id"))


def _walk_budget(obj):
    return outcome_waits.walk_budget(obj)


def _player_tile():
    player = controller.get_player() or {}
    return (player.get("x"), player.get("y"), player.get("plane", 0))


def _d(ctx):
    return ctx.state.data


# --- snowfall -----------------------------------------------------------------------------

def _snow_on_tile(ctx) -> bool:
    """Whether a 29325 area-attack warning is standing on us right now.

    An ACCELERATOR, never a source of truth: if 29325 never reaches us (checklist item 1) this
    simply stays False and warmth is healed reactively instead. The memory expires so a missed
    despawn cannot pin the dodge rung forever."""
    data = _d(ctx)
    tile = data.get("wt_snow_tile")
    if not tile:
        return False
    if _now() - float(data.get("wt_snow_at") or 0) > SNOW_MEMORY_S:
        data.pop("wt_snow_tile", None)
        return False
    return tuple(tile) == _player_tile()


def on_event(ctx, event_type, payload):
    payload = payload or {}
    if event_type == "OBJECT_SPAWNED" and payload.get("id") == w.OBJ_SNOW:
        tile = (payload.get("x"), payload.get("y"), payload.get("plane", 0))
        if tile == _player_tile():
            _d(ctx)["wt_snow_tile"] = tile
            _d(ctx)["wt_snow_at"] = _now()
    elif event_type == "OBJECT_DESPAWNED" and payload.get("id") == w.OBJ_SNOW:
        _d(ctx).pop("wt_snow_tile", None)


# --- snapshot -----------------------------------------------------------------------------

def snapshot(ctx):
    """One reader read plus one inventory read, and the brazier chosen from them."""
    reader = _reader()
    items = _inventory()
    data = _d(ctx)
    config = ctx.state.config or DEFAULT_CONFIG
    brazier = ws.choose_brazier(reader, current=data.get("wt_brazier"),
                                preferred=config.get("preferredBrazier", "SOUTH_WEST"))
    if brazier:
        data["wt_brazier"] = brazier
    if ws.round_active(reader):
        data["wt_round_over_streak"] = 0
    else:
        data["wt_round_over_streak"] = int(data.get("wt_round_over_streak") or 0) + 1
    return reader, items, brazier


def setup(ctx):
    debug_log.log(LOG, "setup: group-world fletch loop, no banking, region %d" % w.REGION)
    _d(ctx).clear()
