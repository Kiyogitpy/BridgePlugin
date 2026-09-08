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


# --- click primitives ----------------------------------------------------------------------

KIT_MAX_TAKES = 12               # a ceiling, because a cooldown only sets the RATE of a loop
DRINK_BUDGET_S = 3.0
TAKE_BUDGET_S = 4.0


def _click_object(ctx, obj, action, budget_s, confirm, signals=None):
    """Click a world object and confirm it on a real outcome.

    NO expected_action hover gate. /hover reports what is under the cursor RIGHT NOW, and these
    clicks are issued while the player is still walking up to the target -- the gate withholds
    every one of them. That trap cost the Gauntlet ~8s per node and the Motherlode routine every
    single ladder click. The outcome confirm is what proves the click landed."""
    if not obj:
        return False
    if _too_far(obj):
        return _walk_to(ctx, obj)
    result = controller.click_entity(ctx.ser, obj, refresh_target=_refresh(obj))
    if not result:
        debug_log.log(LOG, "click %s on %d was refused or withheld" % (action, obj.get("id")))
        return False
    outcome = outcome_waits.await_pre(
        ctx, signals or [], budget_s + _walk_budget(obj), confirm,
        now_fn=_now, sleep_fn=_sleep)
    return bool(outcome and outcome.ok)


def _too_far(obj) -> bool:
    """WALK FIRST, CLICK CLOSE -- and onScreen is the load-bearing half.

    Motherlode MEASURED a click aimed at a projection 21 tiles away with onScreen false: it did
    nothing at all and /last-action still showed the previous action."""
    if obj.get("onScreen") is False:
        return True
    distance = obj.get("distance")
    return distance is not None and distance > CLICK_RANGE


def _walk_to(ctx, obj) -> bool:
    import travel
    return bool(travel.walk_to(ctx, obj.get("x"), obj.get("y"), obj.get("plane", 0)))


def _click_inventory_item(ctx, item, action, budget_s, confirm):
    """An inventory click. Never simulates a mistake and never checks UI blockers.

    banker.py's interface-click rule: a misclick plus its corrective reclick would spend the
    NEIGHBOURING slot, and here that neighbour is a bruma root or the knife.

    An item dict from controller.get_inventory() already carries screenX/screenY, so it IS the
    entity -- there is no separate point lookup. offset=(0,0) because the default (-7,+7) nudge
    is enough to slide off a 36px inventory cell. This is the shape corrupted_gauntlet.use_on
    uses."""
    controller.open_inventory_tab(ctx.ser)
    result = controller.click_entity(ctx.ser, item, offset=(0, 0), check_ui_blockers=False,
                                     simulate_mistakes=False)
    if not result:
        return False
    outcome = outcome_waits.await_pre(ctx, ["INVENTORY_CHANGED"], budget_s, confirm,
                                      now_fn=_now, sleep_fn=_sleep)
    return bool(outcome and outcome.ok)


def _use_on(ctx, item_a, item_b) -> bool:
    """Use item A on item B: two interface clicks, the first carrying the "Use" hover gate.

    Copied from corrupted_gauntlet.use_on -- there is NO controller-level use_on helper, and
    inventing one mid-task is how two routines end up with different semantics."""
    if not item_a or not item_b:
        return False
    controller.open_inventory_tab(ctx.ser)
    controller.click_entity(ctx.ser, item_a, offset=(0, 0), check_ui_blockers=False,
                            simulate_mistakes=False, expected_action="Use")
    controller.click_entity(ctx.ser, item_b, offset=(0, 0), check_ui_blockers=False,
                            simulate_mistakes=False)
    return True


# --- kit / warmth rungs ----------------------------------------------------------------------

def kit_phase(ctx, items) -> bool:
    """Restock whatever the kit is missing, from the arena crates.

    There is no bank trip anywhere in this routine: axe, knife, tinderbox, hammer and the
    unfinished potions are all free from crates a few tiles away."""
    data = _d(ctx)
    config = ctx.state.config or DEFAULT_CONFIG
    missing = ws.kit_deficit(items, config.get("minDoses", 4))
    if not missing:
        data["wt_kit_takes"] = 0
        return True

    data["wt_kit_takes"] = int(data.get("wt_kit_takes") or 0) + 1
    if data["wt_kit_takes"] > KIT_MAX_TAKES:
        ctx.state.trigger_safety_stop(
            "wintertodt: could not restock the kit (%s) after %d attempts"
            % (", ".join(missing), KIT_MAX_TAKES))
        return False

    want = missing[0]
    if want == "potion":
        return _take_concoctions(ctx, items)

    for crate_id, (action, item_needle) in w.CRATE_TOOLS.items():
        if item_needle != want:
            continue
        crate = _nearest(_objects(ids={crate_id}))
        if not crate:
            debug_log.log(LOG, "kit: no crate %d in view for %s" % (crate_id, want))
            return False
        before = len(items)
        return _click_object(ctx, crate, action, TAKE_BUDGET_S,
                             lambda: len(_inventory()) > before,
                             signals=["INVENTORY_CHANGED"])
    return False


def _take_concoctions(ctx, items) -> bool:
    """Take unfinished potions in bulk, then brew them with a bruma herb.

    Herblore 75 on this account means Druidic Ritual is complete, so the Brew'ma NPC fallback
    (which mixes for players without the quest) is not needed."""
    config = ctx.state.config or DEFAULT_CONFIG
    crate = _nearest(_objects(ids={29320}))
    if crate:
        want = int(config.get("takeConcoctions", 10))
        action = w.CRATE_TAKE_10 if want >= 10 else (
            w.CRATE_TAKE_5 if want >= 5 else "Take-concoction")
        available = [str(a) for a in (crate.get("actions") or []) if a]
        if not any(action.lower() == a.lower() for a in available):
            action = "Take-concoction"
        before = ws.count_item(items, w.ITEM_UNF)
        if _click_object(ctx, crate, action, TAKE_BUDGET_S,
                         lambda: ws.count_item(_inventory(), w.ITEM_UNF) > before,
                         signals=["INVENTORY_CHANGED"]):
            return True

    if ws.count_item(items, w.ITEM_UNF) and not ws.count_item(items, w.ITEM_HERB):
        roots = _nearest(_objects(ids={w.OBJ_SPROUTING_ROOTS}))
        before = ws.count_item(items, w.ITEM_HERB)
        return _click_object(ctx, roots, "Pick", TAKE_BUDGET_S,
                             lambda: ws.count_item(_inventory(), w.ITEM_HERB) > before,
                             signals=["INVENTORY_CHANGED"])

    if ws.count_item(items, w.ITEM_UNF) and ws.count_item(items, w.ITEM_HERB):
        before = ws.potion_doses(items)
        herb = next((i for i in items if i.get("id") == w.ITEM_HERB), None)
        unf = next((i for i in items if i.get("id") == w.ITEM_UNF), None)
        if _use_on(ctx, herb, unf):
            outcome = outcome_waits.await_pre(
                ctx, ["INVENTORY_CHANGED"], 4.0,
                lambda: ws.potion_doses(_inventory()) > before,
                now_fn=_now, sleep_fn=_sleep)
            return bool(outcome and outcome.ok)
    return False


def warmth_phase(ctx, items) -> bool:
    """Drink one dose. Outranks all work below it: 0% warmth is instant death and forfeits
    every point of the round."""
    dose = next((i for i in items
                 if w.POTION_NAME in (i.get("name") or "").lower()
                 and "(unf)" not in (i.get("name") or "").lower()), None)
    if not dose:
        ctx.state.trigger_safety_stop(
            "wintertodt: warmth is low and there is no rejuvenation potion to drink")
        return False
    before = ws.warmth_percent(_reader()) or 0.0
    return _click_inventory_item(
        ctx, dose, "Drink", DRINK_BUDGET_S,
        lambda: (ws.warmth_percent(_reader()) or 0.0) > before + 5)
