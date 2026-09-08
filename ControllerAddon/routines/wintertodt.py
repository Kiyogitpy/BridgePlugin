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
    return bool(outcome)


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
    return bool(outcome)


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


DOSES_PER_POTION = 4             # a finished Rejuvenation potion is a standard 4-dose potion,
                                  # matching the (4) suffix ws.potion_doses parses


def _take_concoctions(ctx, items) -> bool:
    """Take unfinished potions in bulk, then brew them with a bruma herb.

    Herblore 75 on this account means Druidic Ritual is complete, so the Brew'ma NPC fallback
    (which mixes for players without the quest) is not needed.

    Stage 1 (take from the crate) is GATED on how many more unfinished potions are actually
    needed to cover the deficit. Fix round 1: an unconditional stage 1 always wins the moment
    the crate is in view -- a successful click returns True immediately -- so it refills
    unfinished potions forever and never falls through to picking a herb or combining. Live
    that deadlocks the whole kit: 28/28 unfinished potions, zero herbs, zero drinkable doses,
    then no room left to pick a herb either, and KIT_MAX_TAKES safety-stops a run in which
    every individual click had actually succeeded. Once held unf already covers the deficit
    (ceil'd to whole potions, since each unf brews into one DOSES_PER_POTION-dose potion),
    stage 1 is skipped so stages 2/3 run instead -- which is also what keeps the inventory
    from ever filling on unf alone, so combining (which frees a slot) stays reachable."""
    config = ctx.state.config or DEFAULT_CONFIG
    min_doses = int(config.get("minDoses", 4))
    have_doses = ws.potion_doses(items)
    have_unf = ws.count_item(items, w.ITEM_UNF)
    have_herb = ws.count_item(items, w.ITEM_HERB)
    needed_doses = max(0, min_doses - have_doses)
    potions_needed = -(-needed_doses // DOSES_PER_POTION) if needed_doses else 0

    crate = _nearest(_objects(ids={29320}))
    if crate and have_unf < potions_needed:
        want = int(config.get("takeConcoctions", 10))
        action = w.CRATE_TAKE_10 if want >= 10 else (
            w.CRATE_TAKE_5 if want >= 5 else "Take-concoction")
        available = [str(a) for a in (crate.get("actions") or []) if a]
        if not any(action.lower() == a.lower() for a in available):
            action = "Take-concoction"
        before = have_unf
        if _click_object(ctx, crate, action, TAKE_BUDGET_S,
                         lambda: ws.count_item(_inventory(), w.ITEM_UNF) > before,
                         signals=["INVENTORY_CHANGED"]):
            return True

    if have_unf and not have_herb:
        roots = _nearest(_objects(ids={w.OBJ_SPROUTING_ROOTS}))
        before = have_herb
        return _click_object(ctx, roots, "Pick", TAKE_BUDGET_S,
                             lambda: ws.count_item(_inventory(), w.ITEM_HERB) > before,
                             signals=["INVENTORY_CHANGED"])

    if have_unf and have_herb:
        before = have_doses
        herb = next((i for i in items if i.get("id") == w.ITEM_HERB), None)
        unf = next((i for i in items if i.get("id") == w.ITEM_UNF), None)
        if _use_on(ctx, herb, unf):
            outcome = outcome_waits.await_pre(
                ctx, ["INVENTORY_CHANGED"], 4.0,
                lambda: ws.potion_doses(_inventory()) > before,
                now_fn=_now, sleep_fn=_sleep)
            return bool(outcome)
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


# --- chop / fletch / feed rungs --------------------------------------------------------------

CHOP_BUDGET_S = 90.0             # a whole inventory of roots; a guess, fit it live
FLETCH_BUDGET_S = 90.0           # a whole inventory fletched; a guess, fit it live
FEED_BUDGET_S = 60.0             # a whole inventory fed; a guess, fit it live


def _await(ctx, signals, budget_s, confirm, since=None):
    # `bridge_events.Outcome` has no `.ok` -- its fields are hit/signal/payload/confirmed/
    # latency_s and `__bool__` returns `hit`. An earlier draft wrote `outcome.ok` here and at
    # three sites in Task 6; it crashed on the first genuinely SUCCESSFUL click and was
    # invisible to tests that mock the click helpers. Use plain truthiness.
    outcome = outcome_waits.await_pre(ctx, signals, budget_s, confirm, since=since,
                                      now_fn=_now, sleep_fn=_sleep)
    return bool(outcome)


def chop_phase(ctx, items) -> bool:
    """Chop until the inventory is full.

    ONE CLICK, not one per root: chopping is a CONTINUOUS action and the game ends it itself
    with "Your inventory is too full". Re-clicking mid-action would cancel and restart it."""
    roots = _nearest(_objects(ids={w.OBJ_ROOTS}, action="Chop"))
    if not roots:
        debug_log.log(LOG, "chop: no Bruma roots in view")
        return False
    return _click_object(
        ctx, roots, "Chop", CHOP_BUDGET_S,
        lambda: ws.inventory_full(_inventory()),
        signals=["CHAT_MESSAGE"])


def _use_knife_on_root(ctx, knife, root) -> bool:
    return _use_on(ctx, knife, root)


def fletch_phase(ctx, items) -> bool:
    """Fletch the whole inventory of roots into kindling.

    Also CONTINUOUS: one knife-on-root and the game fletches the lot, ending with "You have run
    out of bruma roots"."""
    knife = next((i for i in items if "knife" in (i.get("name") or "").lower()), None)
    root = next((i for i in items if i.get("id") == w.ITEM_ROOT), None)
    if not knife or not root:
        return False
    controller.open_inventory_tab(ctx.ser)
    if not _use_knife_on_root(ctx, knife, root):
        return False
    return _await(ctx, ["CHAT_MESSAGE", "INVENTORY_CHANGED"], FLETCH_BUDGET_S,
                  lambda: ws.count_item(_inventory(), w.ITEM_ROOT) == 0)


def feed_phase(ctx, reader, items, brazier) -> bool:
    """Feed the lit brazier until we are empty.

    The brazier is found by the LIT id (29314) specifically. Its unlit and broken twins are
    both named "Brazier" and one of them is usually nearer, so a nearest-by-name pick takes the
    wrong object -- the shut-vs-open door lesson."""
    lit = _nearest(_objects(ids={w.OBJ_BRAZIER_LIT}, action="Feed"))
    if not lit:
        debug_log.log(LOG, "feed: no burning brazier in view")
        return False
    return _click_object(
        ctx, lit, "Feed", FEED_BUDGET_S,
        lambda: (ws.count_item(_inventory(), w.ITEM_KINDLING) == 0
                 and ws.count_item(_inventory(), w.ITEM_ROOT) == 0),
        signals=["CHAT_MESSAGE", "INVENTORY_CHANGED"])


# --- light / fix / dodge rungs ---------------------------------------------------------------

LIGHT_MAX_CLICKS = 10
FIX_MAX_CLICKS = 10
BRAZIER_ACTION_BUDGET_S = 6.0
DODGE_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1))


def _click_menu(ctx, obj, option) -> bool:
    """The right-click escalation, immune to whatever is DRAWN over the aim pixel.

    golem_crafting MEASURED a 30-40% bare-click hijack rate from other players' models covering
    a plinth, and a Wintertodt brazier on an official world is far more crowded than that.
    Re-resolve the object first -- click_menu_action right-clicks exactly the pixel it is
    given, and a stale one produces a menu of pure ground options."""
    fresh = _nearest(_objects(ids={obj.get("id")}, fresh=True))
    return bool(controller.click_menu_action(ctx.ser, fresh or obj, option, offset=(0, 0),
                                             refresh_target=_refresh(fresh or obj)))


def _brazier_action(ctx, ctr_key, obj_id, option, done, brazier) -> bool:
    """Shared body for light and fix: bare click, then the menu, then a ceiling.

    `done` is a zero-arg predicate: True when THIS RUNG'S goal is already satisfied. Checked
    BEFORE anything else, fix round 1 (finding 2): on a busy official world another player
    relighting/fixing first is the NORMAL case -- the object simply vanishes from /objects --
    and that must read as success, never as a failed click. The ceiling below it only ever
    counts a REAL attempt (an object we could actually see and clicked) that produced nothing;
    an object that was never even visible is not an attempt at all and must not spend it."""
    data = _d(ctx)
    if done():
        data[ctr_key] = 0
        return True

    obj = _nearest(_objects(ids={obj_id}, action=option))
    if not obj:
        return False

    data[ctr_key] = int(data.get(ctr_key) or 0) + 1
    if data[ctr_key] > (LIGHT_MAX_CLICKS if option == "Light" else FIX_MAX_CLICKS):
        ctx.state.trigger_safety_stop(
            "wintertodt: %s never registered on the %s brazier after %d attempts"
            % (option, brazier, data[ctr_key] - 1))
        return False

    signals = ["CHAT_MESSAGE"]
    if _click_object(ctx, obj, option, BRAZIER_ACTION_BUDGET_S, done, signals=signals):
        data[ctr_key] = 0
        return True
    if _click_menu(ctx, obj, option):
        # fix round 1 (finding 3): reflect the confirm's own outcome, exactly like the
        # bare-click branch above -- returning True unconditionally here is what let a menu
        # click that never actually confirmed masquerade as success.
        confirmed = _await(ctx, signals, BRAZIER_ACTION_BUDGET_S + _walk_budget(obj), done)
        if confirmed:
            data[ctr_key] = 0
        return bool(confirmed)
    return False


def light_phase(ctx, reader, brazier) -> bool:
    """25 points, and the wiki says it is worth interrupting anything else for.

    `reader` is accepted (the _PHASES dispatcher passes it) but not used: the confirm re-reads
    the HUD itself, because the snapshot that chose this rung is by then a click old."""
    return _brazier_action(ctx, "wt_light_clicks", w.OBJ_BRAZIER_UNLIT, "Light",
                           lambda: ws.brazier_status(_reader(), brazier) == "LIT", brazier)


def fix_phase(ctx, reader, brazier) -> bool:
    """25 points. The wiki warns braziers are "fixed extremely quickly on Wintertodt worlds",
    so losing this race is normal and is a no-op, not a failure.

    Fix round 1 (finding 1): REPAIRING A BRAZIER DOES NOT LIGHT IT. The wiki's Strategies page
    says a broken brazier "can then be relit after it is fixed", and the HUD section lists
    three distinct states (unlit / lit / damaged) -- Fix transitions BROKEN -> UNLIT, never
    straight to LIT. So this confirms on LEAVING "BROKEN", not on reaching "LIT"; the light
    rung relights on its own next tick, exactly the wiki's own sequence.

    "UNKNOWN" (an unreadable reader) is excluded from "done" on purpose -- ws.brazier_status
    returns it for a key the reader has no data for at all, and treating THAT as "no longer
    broken, goal met" is the same "unreadable is not empty" mistake the m8aq readers guard
    against everywhere else in this repo. An unreadable brazier must fall through to a real
    attempt, not be waved through as already fixed."""
    return _brazier_action(
        ctx, "wt_fix_clicks", w.OBJ_BRAZIER_BROKEN, "Fix",
        lambda: ws.brazier_status(_reader(), brazier) not in ("BROKEN", "UNKNOWN"), brazier)


def _force_walk(ctx, x, y, plane) -> bool:
    """A SHIFT-clicked walk. CLAUDE.md's standing rule: always shift+click a plain ground tile,
    or a nearby interactive object's own action can swallow the click."""
    return bool(travel.walk_to_exact_tile(ctx, x, y, plane))


def dodge_phase(ctx) -> bool:
    """Step one tile out of the 3x3 area attack.

    An ACCELERATOR: if 29325 never reaches /objects (checklist item 1) this rung simply never
    fires and warmth is healed reactively instead. Nothing else depends on it."""
    px, py, plane = _player_tile()
    if px is None:
        return False
    _d(ctx).pop("wt_snow_tile", None)
    for dx, dy in DODGE_STEPS:
        if _force_walk(ctx, px + dx * 2, py + dy * 2, plane):
            debug_log.log(LOG, "dodge: stepped off the snowfall tile")
            return True
    return False


# --- dispatcher: tick / before_break / the stuck watch ----------------------------------------

STALL_WARN_S = 60.0
STALL_STOP_S = 180.0
BREAK_DEFER_MAX_S = 420.0        # a round averages ~5 min including the 60s gap
# NB: how many inactive reads make a round end is the CONFIG key `roundEndReads`, read by
# derive_phase. Do not add a module constant beside it -- two sources for one number is how
# they drift apart.


def _progress_signature(reader, items):
    """Everything WE make move when the routine is working. Unchanged for STALL_STOP_S means
    stuck -- and CLAUDE.md's standing lesson is that a silent loop is worse than a crash.

    Fix round 1, Finding 2: warmth and energyPercent are deliberately EXCLUDED. Both drift
    without any action of ours -- warmth regenerates passively (~8%/min, wiki), and energy
    falls from OTHER players damaging the boss -- so including either lets the signature move
    straight through a genuine wedge and defeats this watch entirely. points plus inventory
    composition are the only things that only change because WE did something."""
    reader = reader or {}
    return "|".join(str(x) for x in (
        reader.get("points"),
        len(items or []),
        ws.count_item(items, w.ITEM_ROOT),
        ws.count_item(items, w.ITEM_KINDLING),
    ))


def _stuck_watch(ctx, signature):
    data = _d(ctx)
    if data.get("wt_sig") != signature:
        data["wt_sig"] = signature
        data["wt_sig_at"] = _now()
        data["wt_sig_warned"] = False
        return
    elapsed = _now() - float(data.get("wt_sig_at") or _now())
    if elapsed >= STALL_STOP_S:
        ctx.state.trigger_safety_stop(
            "wintertodt: no progress for %.0fs" % elapsed)
    elif elapsed >= STALL_WARN_S and not data.get("wt_sig_warned"):
        data["wt_sig_warned"] = True
        debug_log.log(LOG, "[!] wintertodt: STUCK? no progress for %.0fs" % elapsed)


def between_rounds_phase(ctx, reader, items) -> bool:
    """The ~60s gap. Top up kit and potions, and be standing at the brazier ready to light the
    instant the timer hits 0:00 -- worth 25 points plus Firemaking level * 6 XP, the wiki's
    single best-value action of the round."""
    config = ctx.state.config or DEFAULT_CONFIG
    if ws.kit_deficit(items, config.get("minDoses", 4)):
        return kit_phase(ctx, items)
    seconds = reader.get("secondsUntilRoundStart")
    if seconds is not None and seconds > 5:
        brazier = _nearest(_objects(ids=w.BRAZIER_IDS))
        if brazier and _too_far(brazier):
            return _walk_to(ctx, brazier)
    outcome_waits.tick_pause(ctx, 1, now_fn=_now, sleep_fn=_sleep)
    return True


_PHASES = {
    "kit": lambda ctx, reader, items, brazier: kit_phase(ctx, items),
    "warmth": lambda ctx, reader, items, brazier: warmth_phase(ctx, items),
    "dodge": lambda ctx, reader, items, brazier: dodge_phase(ctx),
    "light": lambda ctx, reader, items, brazier: light_phase(ctx, reader, brazier),
    "fix": lambda ctx, reader, items, brazier: fix_phase(ctx, reader, brazier),
    "feed": lambda ctx, reader, items, brazier: feed_phase(ctx, reader, items, brazier),
    "fletch": lambda ctx, reader, items, brazier: fletch_phase(ctx, items),
    "chop": lambda ctx, reader, items, brazier: chop_phase(ctx, items),
    "between_rounds": lambda ctx, reader, items, brazier:
        between_rounds_phase(ctx, reader, items),
}


def tick(ctx):
    reader, items, brazier = snapshot(ctx)
    config = ctx.state.config or DEFAULT_CONFIG
    data = _d(ctx)

    phase = ws.derive_phase(
        reader=reader, items=items, config=config, brazier=brazier,
        snow_on_tile=_snow_on_tile(ctx),
        round_over_streak=int(data.get("wt_round_over_streak") or 0),
    )

    if phase != data.get("wt_phase"):
        debug_log.log(LOG, "phase=%s brazier=%s points=%s warmth=%s energy=%s"
            % (phase, brazier, reader.get("points"), reader.get("warmth"),
               reader.get("energyPercent")))
        data["wt_phase"] = phase

    if phase == "stop":
        ctx.state.trigger_safety_stop(
            "wintertodt: not in the Wintertodt arena (region %d) -- this routine owns no travel"
            % w.REGION)
        return

    # Fix round 1, Finding 3: the watch stands DOWN during "between_rounds". points reads -1
    # and the inventory is static in that phase, so with warmth/energy already dropped from the
    # signature (Finding 2) an unusually long gap before the next round starts would trip
    # STALL_STOP_S against a perfectly healthy run -- a sibling routine in this repo makes its
    # own stuck watch in-round-only for the same reason.
    if phase != "between_rounds":
        _stuck_watch(ctx, _progress_signature(reader, items))
    handler = _PHASES.get(phase)
    if handler:
        handler(ctx, reader, items, brazier)


def before_break(ctx):
    """Play the round out before the break starts.

    Points are all-or-nothing at 500 and every point is forfeited by leaving mid-round, so a
    break landing mid-round throws away the whole round. GOTR's own measured lesson, and the
    reason `before_break` exists in the routine contract at all. The break begins the moment
    this returns, so the ceiling matters as much as the loop.

    Fix round 1, Finding 1 (Critical): the loop paces ITSELF every iteration, the same
    `outcome_waits.tick_pause` between_rounds_phase already uses -- never relying on tick()'s
    own phase handler to have done any I/O. A phase handler that produces nothing (chop_phase
    with no roots in view, for one) never calls `_sleep` at all, and the loop used to have
    nothing else advancing its own clock: live it still terminates at BREAK_DEFER_MAX_S, but it
    was proven to hammer the bridge with unthrottled HTTP for up to 420s on a completely
    ordinary transient. Termination must not depend on what the callee happened to do.

    Fix round 1, Finding 5 (Minor): the reader is read as defensively as everywhere else in
    this file -- `_reader()` itself already collapses a bad read to `{}`, but an exception
    raised getting there (a monkeypatched test double, or a future change to `_reader`) must
    not escape this hook and crash the engine's break machinery."""
    started = _now()
    while _now() - started < BREAK_DEFER_MAX_S:
        try:
            reader = _reader()
        except Exception:
            reader = {}
        if not ws.round_active(reader):
            debug_log.log(LOG, "before_break: round over, break may start")
            return
        tick(ctx)
        outcome_waits.tick_pause(ctx, 1, now_fn=_now, sleep_fn=_sleep)
    debug_log.log(LOG, "before_break: gave up waiting for the round to end after %.0fs"
        % BREAK_DEFER_MAX_S)
