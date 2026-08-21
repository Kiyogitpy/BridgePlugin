# routines/chinchompa_hunting.py
#
# Box-trap Carnivorous chinchompas (red chins) with four traps, unattended, and pick the traps
# back up before the engine takes a break instead of leaving them laid for 3-12 minutes.
#
# Run with:
#     python controller.py routines/chinchompa_hunting.py
# ...or pick "Chinchompa Hunting" from Controller Settings -> Routine (see RoutineChoice.java /
# controller.py's ROUTINE_FILES).
#
# ---------------------------------------------------------------------------
# Mechanics (researched, not recalled -- sources are the local mirrors in data/)
# ---------------------------------------------------------------------------
# data/raw/C/Carnivorous chinchompa.txt, data/raw/B/Box trap.txt:
#   - Carnivorous chinchompa is NPC id 2911, Hunter level 63, 265 xp per catch. Caught with a
#     Box trap (item id 10008, item options "Lay, Drop"). Found at the Feldip Hunter area, the
#     red chinchompa hunting ground, Gwenith, Tlati Rainforest and Chinchompa Island -- this
#     routine hunts wherever the player is standing when it starts, so it works at all of them
#     without needing a tile table per location.
#   - Trap count is capped by Hunter level: 1 / 2 / 3 / 4 / 5 at levels 1 / 20 / 40 / 60 / 80.
#     Four traps (the user's requirement) needs level 60, comfortably below the 63 needed to
#     catch the chins at all.
#   - A laid trap tries to lure anything within 2 tiles (a 5x5 square centred on the trap) every
#     3 game ticks -- so the four traps in the default layout all cover the standing tile and
#     each other, and a chin wandering anywhere near the cluster is in range of several at once.
#   - "Box traps won't trap prey if players are standing on the trap itself." This is why the
#     player stands on the ANCHOR tile, which is deliberately never a trap tile, and why tick()'s
#     idle branch walking back to the anchor is load-bearing rather than cosmetic: laying a trap
#     leaves the player standing on the tile they just laid it on, disabling it until they move.
#   - Box traps became stackable on 2025-08-20, and red chinchompas stack too. Nothing here ever
#     fills the inventory, so this routine has NO banking phase at all -- there is nothing to
#     bank. It runs until stopped.
#   - Baiting (+3%) and smoking (+2%) are deliberately not implemented: two extra clicks per trap
#     for that, and the wiki's own guidance is that it isn't worth it.
#
# Box trap GameObject ids come from data/lookups/ObjectLookup.java, cross-checked against
# data/decompiled/.../plugins/hunter/HunterPlugin.java, whose own state machine sorts these same
# ids into OPEN / FULL / EMPTY / TRANSITION. See the TRAP_*_IDS constants below.
#
# ---------------------------------------------------------------------------
# Why trap identity is a TILE, not a remembered trap
# ---------------------------------------------------------------------------
# ctx.state.data resets to {} whenever the routine file changes or the process restarts, while
# four traps laid in the dirt very much do not (see CLAUDE.md). So this routine never trusts
# per-trap bookkeeping: every tick it re-reads /objects and classifies whatever box-trap object
# is currently sitting on each of its own PLAN TILES. That also settles trap ownership for free
# -- in a busy hunting area other players' traps are GameObjects with these exact same ids, and
# anything on a tile that isn't one of ours is simply not in the dict.
#
# The restart case is covered on top of that by adoption: when the plan is first built, box traps
# already standing within `adoptRadius` of the anchor fill plan slots before the configured
# offsets do, so relaunching while four traps are down re-adopts them instead of orphaning them
# and trying to lay four more the inventory doesn't have.
#
# ---------------------------------------------------------------------------
# Picking traps up before a break (the headline requirement)
# ---------------------------------------------------------------------------
# controller.py toggles breaks BEFORE calling tick(), and skips tick() for the entire break, so a
# routine cannot clean up after one has started. before_break(ctx) (added to the routine contract
# for this routine -- see controller.py's header and ControllerState.break_due) is called on the
# tick a break first comes due, before it starts, and this routine uses it to retrieve every trap
# it has laid. The break then begins with the traps safely in the inventory, and tick() lays them
# again afterwards on its own, because it reads the world rather than its own memory.
#
# Traps are NOT picked up on a safety stop or on process exit -- there is no engine hook for
# either, and the panic key is a hard os._exit by design. Only breaks are covered.

import time

import controller
import debug_log
import travel

# --- box trap object ids (ObjectLookup.java; grouped as HunterPlugin.java groups them) --------

# HUNTING_BOXTRAP_EMPTY -- laid and waiting. "Empty" here is the cache's name for "no catch yet",
# not "no trap" -- this is the healthy steady state of a working trap.
TRAP_SET_IDS = frozenset({9380})

# Something is inside: 9382 grey chinchompa, 9383 chinchompa_BIG (this is the carnivorous/red
# one this routine is actually here for), 9384 ferret, 721 black chinchompa. All four are listed
# rather than just 9383 so a stray grey/ferret catch is still collected instead of sitting there
# blocking a trap tile forever.
TRAP_FULL_IDS = frozenset({721, 9382, 9383, 9384})

# HUNTING_BOXTRAP_FAILED -- the prey escaped and knocked the trap over. Must be picked up and
# re-laid; it will never catch anything in this state.
TRAP_FAILED_IDS = frozenset({9385})

# Mid-animation: the closing/failing frames (HUNTING_BOXTRAP_FAILING and the four-facing
# TRAPPING_* variants for chinchompa / chinchompa_BIG / ferret / black). Nothing to do but wait
# -- clicking during these is what HunterPlugin itself treats as a TRANSITION state.
TRAP_TRANSITION_IDS = frozenset({
    2025, 2026, 2028, 2029,                          # black chinchompa, N/E/S/W
    9381,                                            # failing
    9386, 9387, 9388, 9389,                          # chinchompa, N/E/S/W
    9390, 9391, 9392, 9393,                          # chinchompa_BIG (carnivorous), N/E/S/W
    9394, 9395, 9396, 9397,                          # ferret, N/E/S/W
})

TRAP_OBJECT_IDS = TRAP_SET_IDS | TRAP_FULL_IDS | TRAP_FAILED_IDS | TRAP_TRANSITION_IDS

BOX_TRAP_ITEM_NAME = "box trap"    # matched exactly (lowercased), so "Box trap pack" can't match

# NOTE a laid trap's DISPLAYED name depends on its state, which is why nothing here matches on it
# any more (CONFIRMED live 2026-08-21): a full trap is a "Shaking box", while set/collapsed ones
# are a "Box trap". Trap identity is by object id (see classify_trap) and retrieval is a plain
# left-click (see retrieve_trap), so neither path depends on the name.

# Hunter level -> simultaneous trap cap (Box trap.txt's "Multiple traps" table), highest first.
MAX_TRAPS_BY_LEVEL = ((80, 5), (60, 4), (40, 3), (20, 2), (1, 1))

REQUIRED_HUNTER_LEVEL = 63   # to catch a carnivorous chinchompa at all

OBJECT_SCAN_RADIUS = 15      # tiles; every plan tile is within `adoptRadius`+1 of the anchor and
                              # the player is normally standing on it, so this is generous
LAY_CONFIRM_TIMEOUT_S = 5.0
# 3.0 rather than 8.0 (user direction 2026-08-21). 8s was sized for the old slow cycle; now that
# an action is known to resolve within ~2 ticks, spending 8s discovering that a click did not
# take is most of what a miss actually costs. MEASURED at the 8s setting: 3 misses in 22
# retrieves cost ~24s; at 3s the same misses cost ~9s.
RETRIEVE_CONFIRM_TIMEOUT_S = 3.0
# 0.1 rather than 0.3 (user observation 2026-08-21): RuneLite knows the instant a trap is placed
# -- HunterPlugin logs "Trap placed by ..." off the object spawn -- but the bridge has no
# object-spawn UDP event, so the only way to see it is to poll /objects. At 0.3s we were up to
# 300ms late reacting to something the game already knew, and by then the player had begun
# walking. MEASURED: a fresh /objects costs ~22ms, so polling 3x more often is cheap relative to
# the lag it removes. If this ever needs to be genuinely instant, the fix is an OBJECT_SPAWNED
# event on the UDP stream (which already carries ANIMATION_CHANGED / INVENTORY_CHANGED), not a
# faster poll -- that needs a Java-side change and a client rebuild.
CONFIRM_POLL_INTERVAL_S = 0.1

# Waiting out a trap action, MEASURED live 2026-08-21 (see _await_action_finished).
ACTION_POLL_INTERVAL_S = 0.15
ACTION_SETTLE_S = 1.0        # sustained anim=-1 that counts as "really finished"
ACTION_TIMEOUT_S = 12.0      # whole check -> auto-relay cycle measured at ~6s; generous
IDLE_ANIMATION = -1

# Mid-flight re-aim checkpoints when clicking a trap. None = use move_to_human's own single
# mid-flight checkpoint (plain refresh_target), which is what slayer.py/golem_crafting.py use.
#
# refresh_legs was tried here on 2026-08-21 (3, then 2 by user direction) and BACKED OUT: this
# would have been _move_to_human_tracking's first live use anywhere, and lay_trap started failing
# outright the moment it went in -- the Lay click doing nothing at all, pack not even
# decrementing ("no trap object appeared ... pack went 26 -> 26"). Rate went from 0 failures in
# 83 lays across two prior builds to 9 in 19. The mechanism was never established (a random event
# was firing around the same time, so the attribution is correlational, not proven), which is
# exactly why this is left off: plain refresh_target already fixes the stale-screen-position
# problem it was reaching for and has years of live use behind it.
TRAP_CLICK_REFRESH_LEGS = None


DEFAULT_CONFIG = {
    "enabled": True,

    # The user's requirement. Clamped down at runtime if the account's Hunter level doesn't allow
    # this many (see max_traps_for_level) rather than spamming a trap the game will refuse.
    "trapCount": 4,

    # Candidate trap tiles as (dx, dy) from the anchor, in preference order. The first four -- the
    # orthogonal neighbours -- are the default layout: a diamond around the standing tile, every
    # trap exactly one step away, so re-laying after a catch is a single-tile walk. The diagonals
    # after them are spares, used only when one of the first four turns out to be unusable (a
    # "you can't set a trap here" tile, or one another player's trap is already sitting on).
    "trapOffsets": [[0, 1], [1, 0], [0, -1], [-1, 0], [1, 1], [1, -1], [-1, -1], [-1, 1]],

    # [x, y, plane] to build the layout around, or null to use whatever tile the player is
    # standing on at the first tick. Null is the normal case: stand where you want to hunt,
    # start the routine.
    "anchor": None,

    # Re-adopt traps already laid near the anchor instead of orphaning them -- the restart fix,
    # see this file's header. Radius is Chebyshev distance in tiles from the anchor.
    "adoptNearbyTraps": True,
    "adoptRadius": 2,

    # Hunter level needed to catch the prey this is set up for. 63 for carnivorous chinchompas;
    # drop it to 53 (and expect grey chins) or raise it to 73 (black chins, wilderness) if you
    # point this at a different box-trap creature -- everything else here already handles all of
    # them, since the trap ids and the whole lay/check/reset loop are shared.
    "requiredHunterLevel": REQUIRED_HUNTER_LEVEL,

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
        # Never true here: red chinchompas and box traps both stack, so the inventory cannot fill
        # from hunting. Leaving it on would only misfire on an inventory that was already full of
        # something else when the routine started.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # No Controller Settings panel of its own (same as mining.py / gemstone_crab.py /
    # pest_control.py) -- everything this routine needs is in DEFAULT_CONFIG above.
    return DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Pure helpers (no bridge, no clicking -- unit-tested in tests/test_chinchompa_hunting.py)
# ---------------------------------------------------------------------------

def classify_trap(object_id):
    """"set" / "full" / "failed" / "transition" for a box-trap GameObject id, or None if this
    id isn't a box trap at all."""
    if object_id in TRAP_SET_IDS:
        return "set"
    if object_id in TRAP_FULL_IDS:
        return "full"
    if object_id in TRAP_FAILED_IDS:
        return "failed"
    if object_id in TRAP_TRANSITION_IDS:
        return "transition"
    return None


# Which state wins if two trap objects somehow share a tile: the one there's most to gain from
# acting on. Also the order tick() considers work in.
_STATE_PRIORITY = {"full": 0, "failed": 1, "transition": 2, "set": 3}

# before_break's ordering, which is NOT tick()'s -- see the note at its sort() call. Clearing the
# ground is the goal there, and only a set or collapsed trap leaves in a single action.
_TEARDOWN_PRIORITY = {"set": 0, "failed": 1, "full": 2, "transition": 3}


def max_traps_for_level(level):
    """Simultaneous box traps allowed at `level` (Box trap.txt's table). 0 for a nonsense level
    so a caller can't accidentally read "unlimited" out of a missing/negative value."""
    if level is None or level < 1:
        return 0
    for threshold, traps in MAX_TRAPS_BY_LEVEL:
        if level >= threshold:
            return traps
    return 0


def chebyshev(ax, ay, bx, by):
    """Tiles between two points the way OSRS measures adjacency -- diagonal counts as one step,
    same metric /objects' own "distance" field uses."""
    return max(abs(ax - bx), abs(ay - by))


def build_plan(anchor, offsets, count, blocked=(), nearby_trap_tiles=(), adopt_radius=2):
    """The `count` tiles this routine will keep traps on, as a list of (x, y, plane).

    Already-laid traps near the anchor (`nearby_trap_tiles`, pre-filtered by the caller to real
    box-trap objects) claim slots first -- see the header's note on restart adoption -- then the
    remaining slots are filled from `offsets` in order. Blocked tiles, the anchor itself, tiles on
    another plane, and duplicates are skipped everywhere; adoption additionally only considers
    tiles within `adopt_radius` of the anchor, so a trap belonging to a player hunting ten tiles
    away is never mistaken for one of ours.

    Deterministic for a given input (adoption candidates are sorted nearest-first, then by x, y)
    so the plan doesn't quietly reshuffle between ticks."""
    ax, ay, aplane = anchor
    blocked = {tuple(t) for t in blocked}
    plan = []

    def _add(tile):
        if len(plan) >= count:
            return
        if tile in blocked or tile in plan:
            return
        if tile[2] != aplane:
            return
        if (tile[0], tile[1]) == (ax, ay):
            return  # the standing tile: a trap here would be disabled by the player standing on it
        plan.append(tile)

    for tile in sorted(nearby_trap_tiles, key=lambda t: (chebyshev(t[0], t[1], ax, ay), t[0], t[1])):
        if chebyshev(tile[0], tile[1], ax, ay) <= adopt_radius:
            _add(tuple(tile))

    for offset in offsets:
        _add((ax + offset[0], ay + offset[1], aplane))

    return plan


def traps_by_tile(objects, tiles):
    """{(x, y, plane): (state, object)} for the box traps currently standing on `tiles`.

    Everything else in `objects` -- scenery, and crucially any trap on a tile that isn't one of
    ours -- is dropped, which is the whole ownership story (see the header)."""
    wanted = {tuple(t) for t in tiles}
    found = {}
    for obj in objects:
        tile = (obj.get("x"), obj.get("y"), obj.get("plane"))
        if tile not in wanted:
            continue
        state = classify_trap(obj.get("id"))
        if state is None:
            continue
        existing = found.get(tile)
        if existing is not None and _STATE_PRIORITY[existing[0]] <= _STATE_PRIORITY[state]:
            continue
        found[tile] = (state, obj)
    return found


BOX_TRAP_ITEM_ID = 10008

# Menu options that pick a dropped trap up off the floor, in priority order. "Take" is the
# standard ground-item option; the rest are insurance, and click_menu_action prints the real
# entries if none of them match.
DROPPED_TRAP_ACTIONS = ("Take", "Pick-up", "Pick up")


# How long after acting on a tile a Box trap item lying there is assumed to be the placement
# transient rather than a timed-out trap. A trap being laid passes through the floor as an item
# under the player before it becomes an object, so without this the routine picks up the trap it
# just placed and lays it again -- two wasted actions that look exactly like a leak.
PLACEMENT_ITEM_GRACE_S = 2.5

GAME_TICK_S = 0.6
# Ticks to wait after the placement item appears before the player can act again (user, measured
# in-game 2026-08-21). The item is the EARLY signal: a trap being laid drops to the floor as an
# item the moment it leaves the pack (~2.9s into a check->relay cycle), well before the object
# spawns (~4.7s) and before the animation ends (~6.0s). Waiting the two ticks from the item
# instead of sitting out the animation is worth ~1.9s of every cycle.
# A flat 2 (user direction 2026-08-21). 1.6 was tried first, to aim inside the window and absorb
# residual click latency, but that shaving is unnecessary now that hover_entity fires before the
# blocking lookups: the cursor is already on the target when the window opens, so the click no
# longer trails a mouse flight behind it and the true two ticks is what to wait.
PLACEMENT_SETTLE_TICKS = 2
PLACEMENT_WAIT_TIMEOUT_S = 5.0


def dropped_traps_by_tile(ground_items, tiles, recent=None, now=None):
    """{(x, y, plane): item} for Box trap GROUND ITEMS lying on `tiles`.

    `recent` maps "x,y,plane" -> the time we last acted on that tile; anything within
    PLACEMENT_ITEM_GRACE_S of that is skipped as the placement transient (see above).

    A laid trap that nothing touches eventually times out and drops as an item on its own tile
    -- CONFIRMED live 2026-08-21 (observed at 2512,9300, with the laid-object count falling from
    4 to 3 at the same moment). Left alone the item despawns too, and the routine then spends a
    fresh trap from the pack on that tile, which is a steady leak of ~1-2 traps an hour.

    Filtered by tile for the same reason traps_by_tile is: an item on a tile that isn't one of
    ours is somebody else's business."""
    wanted = {tuple(t) for t in tiles}
    recent = recent or {}
    now = time.time() if now is None else now
    found = {}
    for item in ground_items:
        tile = (item.get("x"), item.get("y"), item.get("plane"))
        if tile not in wanted or item.get("id") != BOX_TRAP_ITEM_ID:
            continue
        acted = recent.get(f"{tile[0]},{tile[1]},{tile[2]}")
        if acted is not None and now - acted < PLACEMENT_ITEM_GRACE_S:
            continue        # our own trap mid-placement, not a timed-out one
        found[tile] = item
    return found


def work_queue(plan, traps, dropped=None):
    """[(priority_state, tile, object_or_None)] -- what tick() should consider doing, best first.

    "full" (a catch waiting to be collected) outranks "empty" (a slot with no trap on it), which
    outranks "failed" (a collapsed trap). A tile mid-animation, or holding a healthy set trap,
    produces no work. Pure so the ordering can be asserted directly rather than inferred from a
    live run.

    That middle position for "empty" is the whole point, and both extremes were measured live on
    2026-08-21. Ranked LAST (the original), a 4-trap plan reached 12 catches having only ever
    laid 3 traps: tick() takes one action per tick and there was always either a catch or a
    collapsed trap ahead of it, so the final slot starved forever. Ranked FIRST, the trap failure
    rate doubled (20% -> ~40% of retrievals) and the catch-to-catch time went from ~9.7s to a
    median of 27.6s, because laying displaced collecting. In the middle it does neither: catches
    still come first, so throughput is untouched, and an unlaid slot gets filled at the next
    collapsed trap instead of never -- which at a ~20% failure rate is within a few actions.

    Ties inside a state are broken by PLAN ORDER, not by coordinates or distance. That matters
    for "empty": holding fewer traps than slots (mid-restock, or clamped by Hunter level) should
    fill the preferred offsets -- the orthogonal neighbours the plan lists first -- rather than
    whichever leftover tile happens to sort lowest."""
    dropped = dropped or {}
    items = []
    for index, tile in enumerate(plan):
        entry = traps.get(tile)
        if entry is None:
            # A bare tile with our own timed-out trap lying on it is recovered, not re-laid:
            # same one action either way, but it saves a trap from the pack and the item
            # despawns if it is left there. See dropped_traps_by_tile.
            if tile in dropped:
                items.append((("dropped", tile, dropped[tile]), index))
            else:
                items.append((("empty", tile, None), index))
            continue
        state, obj = entry
        if state in ("full", "failed"):
            items.append(((state, tile, obj), index))
    order = {"full": 0, "dropped": 1, "empty": 2, "failed": 3}
    items.sort(key=lambda entry: (order[entry[0][0]], entry[1]))
    return [item for item, _ in items]


# ---------------------------------------------------------------------------
# Inventory helpers
# ---------------------------------------------------------------------------

def _box_trap_slots():
    return [i for i in controller.get_inventory()
            if (i.get("name") or "").lower() == BOX_TRAP_ITEM_NAME]


def box_trap_count():
    """Total box traps held. Summed across slots rather than read off one: they're stackable now
    (2025-08-20), but summing is correct either way and costs nothing."""
    return sum(slot.get("qty", 1) for slot in _box_trap_slots())


def find_box_trap():
    slots = _box_trap_slots()
    return slots[0] if slots else None


# ---------------------------------------------------------------------------
# State resolution
# ---------------------------------------------------------------------------

def ensure_anchor(ctx, config):
    """The standing tile, resolved once per routine activation and remembered in state.data.
    Returns None if the bridge hasn't reported a position yet (caller just retries next tick)."""
    stored = ctx.state.data.get("chin_anchor")
    if stored is not None:
        return tuple(stored)

    configured = config.get("anchor")
    if configured:
        anchor = (int(configured[0]), int(configured[1]),
                  int(configured[2]) if len(configured) > 2 else 0)
    else:
        player = controller.get_player()
        if player.get("x") is None or player.get("y") is None:
            return None
        anchor = (player["x"], player["y"], player.get("plane", 0))

    ctx.state.data["chin_anchor"] = list(anchor)
    debug_log.log("chins", f"anchor (standing tile) set to {anchor[0]},{anchor[1]} plane {anchor[2]}")
    return anchor


def ensure_plan(ctx, config, anchor, trap_count, objects):
    """The plan tiles, rebuilt only when something that feeds into them actually changed (anchor,
    trap count, or the blocked-tile set) -- a plan that reshuffled every tick would have the
    routine chasing its own tail between two layouts."""
    blocked = sorted(tuple(t) for t in ctx.state.data.get("chin_blocked", []))
    key = [list(anchor), trap_count, [list(t) for t in blocked]]

    cached = ctx.state.data.get("chin_plan")
    if cached is not None and ctx.state.data.get("chin_plan_key") == key:
        return [tuple(t) for t in cached]

    nearby = ()
    if config.get("adoptNearbyTraps", True):
        nearby = [(o.get("x"), o.get("y"), o.get("plane"))
                  for o in objects if classify_trap(o.get("id")) is not None]

    plan = build_plan(
        anchor,
        config.get("trapOffsets", DEFAULT_CONFIG["trapOffsets"]),
        trap_count,
        blocked=blocked,
        nearby_trap_tiles=nearby,
        adopt_radius=config.get("adoptRadius", 2),
    )
    ctx.state.data["chin_plan"] = [list(t) for t in plan]
    ctx.state.data["chin_plan_key"] = key
    debug_log.log("chins", f"trap plan: {[f'{t[0]},{t[1]}' for t in plan]}"
                            + (f" (blocked: {len(blocked)})" if blocked else ""))
    return plan


def block_tile(ctx, tile, reason):
    """Take a tile out of the plan permanently (for this activation) -- the game refused to let a
    trap be set there, or the trap standing on it turned out to belong to someone else. Clearing
    the plan key is what makes ensure_plan rebuild onto one of the spare offsets."""
    tile = list(tile)
    blocked = ctx.state.data.setdefault("chin_blocked", [])
    if tile in blocked:
        return

    # A tile with one of our traps standing on it is PROVEN usable, whatever the game just said,
    # so a refusal here means the click was redundant rather than the tile being bad. Blocking it
    # anyway is actively destructive: the tile leaves the plan, the routine never goes back for
    # the trap on it, and a trap left on the ground eventually times out and despawns (user
    # confirmed, 2026-08-21 -- it drops as a Box trap ground item). That is how one run lost two
    # traps before the double-lay behind these refusals was fixed.
    objects = controller.get_objects(radius=OBJECT_SCAN_RADIUS, fresh=True)
    if traps_by_tile(objects, [tuple(tile)]).get(tuple(tile)) is not None:
        debug_log.log("chins", f"ignoring a refusal for {tile[0]},{tile[1]} -- one of our traps "
                                f"is already standing on it, so the tile is fine")
        return

    blocked.append(tile)
    ctx.state.data.pop("chin_plan_key", None)
    debug_log.log("chins", f"tile {tile[0]},{tile[1]} is unusable ({reason}) -- moving that trap "
                            f"to a spare offset")


def resolve_trap_count(ctx, config):
    """Configured trapCount, clamped to what the account's Hunter level actually allows, and a
    hard stop if it can't catch carnivorous chinchompas at all.

    Run from tick() rather than setup(): controller.py calls setup() at routine-load time, BEFORE
    the login gate, so /skills there can read zeros for a not-yet-logged-in account and
    false-trigger a permanent safety stop. Same reasoning (and the same state.data-flag idiom) as
    firemaking.py's check_level_requirement."""
    cached = ctx.state.data.get("chin_trap_count")
    if cached is not None:
        return cached

    hunter = controller.get_skills().get("Hunter") or {}
    level = hunter.get("level")
    if level is None:
        return None  # /skills not answering yet -- try again next tick

    required = config.get("requiredHunterLevel", REQUIRED_HUNTER_LEVEL)
    if level < required:
        ctx.state.trigger_safety_stop(
            f"Hunter level {level} is below the {required} needed for carnivorous chinchompas"
        )
        return None

    allowed = max_traps_for_level(level)
    wanted = config.get("trapCount", 4)
    count = min(wanted, allowed)
    if count < wanted:
        debug_log.log("chins", f"Hunter {level} only allows {allowed} traps at once -- using "
                                f"{count} instead of the configured {wanted}")
    ctx.state.data["chin_trap_count"] = count
    return count


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

# Consecutive failed walks onto a plan tile before it is treated as genuinely unreachable and
# dropped from the plan. Not 1: a chinchompa standing on the tile blocks the walk transiently,
# and blocking a good tile for that would be worse than retrying it.
UNREACHABLE_BLOCK_THRESHOLD = 4


def _note_acted_on(ctx, tile):
    """Stamp the time we last acted on `tile`, so dropped_traps_by_tile can tell our own
    placement transient from a genuinely timed-out trap."""
    recent = ctx.state.data.setdefault("chin_recent_tiles", {})
    recent[f"{tile[0]},{tile[1]},{tile[2]}"] = time.time()


def _note_unreachable(ctx, tile):
    """Count this tile's consecutive failed walks and return the new streak."""
    streaks = ctx.state.data.setdefault("chin_unreachable", {})
    key = f"{tile[0]},{tile[1]},{tile[2]}"
    streaks[key] = streaks.get(key, 0) + 1
    return streaks[key]


def _clear_unreachable(ctx, tile):
    """Arriving proves the tile is reachable -- the streak must not carry over."""
    streaks = ctx.state.data.get("chin_unreachable")
    if streaks:
        streaks.pop(f"{tile[0]},{tile[1]},{tile[2]}", None)


def lay_trap(ctx, tile):
    """Walk onto `tile` and lay a box trap there. "Lay" always targets the player's own tile, so
    this has to be an exact-tile walk, not a tolerant one -- see travel.walk_to_exact_tile."""
    if not travel.walk_to_exact_tile(ctx, tile[0], tile[1], tile[2]):
        debug_log.log("chins", f"trap tile {tile[0]},{tile[1]} isn't on screen -- skipping it this tick")
        return False

    player = controller.get_player()
    if (player.get("x"), player.get("y"), player.get("plane", 0)) != tile:
        # The walk timed out short. Usually transient (a chinchompa standing on the tile), so a
        # single failure just retries -- but a tile that can NEVER be reached would otherwise be
        # retried forever, and each attempt costs a full ~10s action. CONFIRMED live 2026-08-21:
        # 2511,9301 was unwalkable and burned nine actions in under three minutes, roughly 200 an
        # hour, because block_tile keys off the game's refusal message and that never arrives
        # when the player cannot get to the tile to attempt the lay at all.
        streak = _note_unreachable(ctx, tile)
        debug_log.log("chins", f"didn't reach {tile[0]},{tile[1]} -- retrying next tick "
                                f"({streak}/{UNREACHABLE_BLOCK_THRESHOLD})")
        if streak >= UNREACHABLE_BLOCK_THRESHOLD:
            block_tile(ctx, tile, "the player can't get onto it")
        return False

    _clear_unreachable(ctx, tile)

    controller.open_inventory_tab(ctx.ser)
    item = find_box_trap()
    if item is None:
        return False

    before = box_trap_count()
    ctx.state.data["chin_last_tile"] = list(tile)
    _note_acted_on(ctx, tile)
    debug_log.log("chins", f"laying a trap at {tile[0]},{tile[1]} ({before} in the pack)")
    controller.human_delay(0.15, 0.35)
    # check_ui_blockers=False: an inventory item's own screen position is inside the inventory
    # panel that /state reports as a UI blocker -- same fix as every other inventory click here.
    controller.click_entity(ctx.ser, item, check_ui_blockers=False)
    return _await_trap_laid(tile, before)


def _await_placement_settled(ctx, tile, timeout_s=PLACEMENT_WAIT_TIMEOUT_S):
    """Watch for the trap hitting the floor as an ITEM on `tile` and record when the player will
    be free again (two game ticks later) -- WITHOUT sleeping it out here. True if the item was
    seen, False if none showed up in `timeout_s` (caller falls back to the animation).

    The item is deliberately what this watches. A trap being laid is on the floor as an item from
    the moment it leaves the pack -- earlier than the object spawn HunterPlugin's "Trap placed"
    logs, and much earlier than the animation ending -- and two ticks after it is when the player
    can act again (measured in-game 2026-08-21).

    Recording the deadline rather than sleeping on it is the point. Sleeping here put the whole
    of the next action's setup AFTER the window opened -- ~100ms of HTTP (objects, ground items,
    idle check, target lookup) plus a 300-800ms human mouse flight -- so the click landed several
    hundred ms late and the timing compounded away. _wait_until_free lets all of that happen
    DURING the wait instead, so only the leftover remainder is ever slept."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        items = controller.get_ground_items(fresh=True) or []
        if any(i.get("id") == BOX_TRAP_ITEM_ID
               and (i.get("x"), i.get("y"), i.get("plane")) == tuple(tile) for i in items):
            ctx.state.data["chin_free_at"] = time.time() + GAME_TICK_S * PLACEMENT_SETTLE_TICKS
            return True
        time.sleep(CONFIRM_POLL_INTERVAL_S)
    return False


def _wait_until_free(ctx):
    """Sleep whatever is left of the two-tick window recorded by _await_placement_settled, then
    clear it. Everything the caller did to get here -- HTTP lookups, moving the cursor onto the
    target -- has already eaten into it, which is the entire idea."""
    free_at = ctx.state.data.pop("chin_free_at", None)
    if free_at is None:
        return
    remaining = free_at - time.time()
    if remaining > 0:
        time.sleep(remaining)


def _trap_is_on(tile):
    """Whether a box trap in any recognised state is standing on `tile` right now. Used as
    _await_action_finished's done_when, so it is only ever called while the player is idle --
    a handful of times per action, not in a tight poll."""
    tile = tuple(tile)
    objects = controller.get_objects(radius=OBJECT_SCAN_RADIUS, fresh=True)
    return traps_by_tile(objects, [tile]).get(tile) is not None


def _await_action_finished(timeout_s=ACTION_TIMEOUT_S, done_when=None):
    """Block until the player has been animation-idle continuously for ACTION_SETTLE_S -- or, if
    `done_when` is given, until the animation is idle AND `done_when()` is true. True if either
    happened, False if `timeout_s` ran out first.

    `done_when` is ONLY ever consulted while the animation already reads idle, and that ordering
    is the whole design. An earlier version checked it unconditionally, so it returned while the
    player was still animating; the next click was dropped by the game and it measured much worse
    (catch cycle 9.7s -> median 18s, with 6 dropped retrieve clicks and 7 lay timeouts where the
    full wait had none). The trap being placed is not the same as the player being free.

    Gated behind idle, though, it is pure profit: it removes the ACTION_SETTLE_S margin, ~1s of
    every ~10s cycle. That margin exists only to see past the ~0.66s gap of anim=-1 BETWEEN the
    check and the auto-relay animations -- and during that gap the tile is bare, so a caller
    asking "is the trap back down?" rejects it for free and needs no timing margin at all. This
    is RuneLite's own "Trap placed by ..." moment (HunterPlugin logs it off the same object
    spawn), which is what suggested it.

    Why sustained idle and not just animation == -1: MEASURED live 2026-08-21, interacting with
    a full trap is TWO animations, not one -- 5212 (check / pick the trap up) and then 5208 (the
    game re-laying it automatically) -- separated by ~0.66s during which animation reads -1 and
    the tile is bare. The whole cycle takes ~6s. Treating that middle gap as "done" is what let
    the next tick see an empty tile, call lay_trap, and INTERRUPT the pending re-lay: the trap
    stayed in the inventory, the tile stayed bare, and repeated over a few catches every trap
    ended up in the pack with nothing on the ground catching anything (user report, confirmed
    live: pack held every trap, ground held none).

    Deliberately not controller.wait_until_player_idle: that one is position-only by design and
    returns True immediately mid-animation for an action performed from a standing tile, which
    is exactly this case (see its docstring)."""
    deadline = time.time() + timeout_s
    idle_since = None
    while time.time() < deadline:
        animation = (controller.get_player() or {}).get("animation")
        now = time.time()
        if animation == IDLE_ANIMATION:
            # Only asked while idle -- see the docstring; asking mid-animation is the regression.
            if done_when is not None and done_when():
                return True
            if idle_since is None:
                idle_since = now
            elif now - idle_since >= ACTION_SETTLE_S:
                return True
        else:
            idle_since = None
        time.sleep(ACTION_POLL_INTERVAL_S)
    debug_log.log("chins", f"an action was still animating after {timeout_s:.0f}s -- carrying on")
    return False


def _await_trap_laid(tile, before_count):
    """Wait until a trap object is actually STANDING on `tile`, and report whether it got there.

    The signal is deliberately the object and nothing else. The obvious alternative -- the trap
    leaving the inventory -- is what this used to return on, and it is wrong: MEASURED live
    2026-08-21, over three consecutive lays, the object does not appear in /objects until
    ~1.9s (about 3 game ticks, the setup animation) AFTER the pack decrements. Returning on the
    pack meant lay_trap handed control back during that window, the next tick's scan read the
    tile as still empty, and the routine laid a second trap on a tile that already had one. The
    game then refused it, the refusal chat message reached on_event, and block_tile struck the
    tile off the plan permanently -- orphaning the perfectly good trap standing on it. Repeated
    per tile, that drained the pack and ended the run with "out of box traps".

    LAY_CONFIRM_TIMEOUT_S (5s) is comfortably beyond the measured ~1.9s. Any state
    classify_trap recognises counts, not just a set trap: a chinchompa can walk in during the
    setup animation, so the first object to appear may already be a full or transition one."""
    deadline = time.time() + LAY_CONFIRM_TIMEOUT_S
    tile = tuple(tile)
    while time.time() < deadline:
        time.sleep(CONFIRM_POLL_INTERVAL_S)
        # fresh=True: this is the one read that must not be a tick behind, since the whole point
        # is detecting the moment the object appears.
        objects = controller.get_objects(radius=OBJECT_SCAN_RADIUS, fresh=True)
        if traps_by_tile(objects, [tile]).get(tile) is not None:
            # The object appears ~1.8s into the ~3.1s lay animation, so it is on the ground
            # before the player has finished putting it there -- settle before the next click.
            _await_action_finished()
            return True
    debug_log.log("chins", f"no trap object appeared at {tile[0]},{tile[1]} within "
                            f"{LAY_CONFIRM_TIMEOUT_S:.0f}s (pack went {before_count} -> "
                            f"{box_trap_count()}) -- re-evaluating next tick")
    return False


def retrieve_trap(ctx, tile, state, obj):
    """Collect a full trap, or pick a collapsed/laid one back up. Either way the box trap ends up
    back in the inventory, which is the signal this waits on.

    A plain left-click, deliberately -- the menu entries are swapped client-side so that the
    left-click action on a box trap is the right one whatever state it is in (Check on a full
    trap, Reset on a collapsed one), so there is nothing left for us to name.

    This REPLACED a right-click click_menu_action pass on 2026-08-21, after the first live run.
    Two reasons. The option names were being guessed and one guess was wrong: a full trap's
    displayed name is "Shaking box", not "Box trap", so the target_hint missed on every single
    retrieval and the routine only worked because of click_menu_action's option-name-only
    fallback -- confirmed live, once per catch, in the `!` log category. And the reason the menu
    was used at all (chinchompas wander over the trap tiles constantly, so a left-click on a
    shared pixel can resolve to Attack instead) is already covered without it: the trap landing
    back in the inventory is the confirmation, and a click that resolved to something else simply
    doesn't produce it, so this returns False and the next tick retries."""
    # Start the cursor moving IMMEDIATELY, before anything that blocks. The two-tick window from
    # the previous action is already running, and everything below -- the idle wait, the fresh
    # object lookup -- used to happen first and consume all of it, so the hover collapsed into
    # the click and the mouse never travelled during the window at all (user, 2026-08-21: "we
    # arent hovering the next trap"). `obj` is the tick's own scan position, good enough to aim
    # at; refresh_target corrects it en route and again on the click.
    refresh = controller.ground_tile_refresh_target(tile[0], tile[1], tile[2])
    controller.hover_entity(ctx.ser, obj, refresh_target=refresh,
                            refresh_legs=TRAP_CLICK_REFRESH_LEGS)

    # A resolved screen position is only trustworthy once the player has stopped moving.
    controller.wait_until_player_idle(timeout_s=3.0)
    fresh = controller.get_object_at(tile[0], tile[1], tile[2], obj.get("id"), radius=OBJECT_SCAN_RADIUS)
    target = fresh if fresh is not None else obj

    ctx.state.data["chin_last_tile"] = list(tile)
    _note_acted_on(ctx, tile)
    debug_log.log("chins", f"{state} trap at {tile[0]},{tile[1]} -- retrieving it")

    # refresh_target: the trap doesn't move but its SCREEN position does. The camera keeps
    # drifting while the player settles, so the position resolved above is stale by the time the
    # cursor finishes flying to it and the click can land beside the trap. wait_until_player_idle
    # above does not cover this -- it is position-only by design and returns as soon as local x/y
    # stops changing, which is before the camera has caught up. Re-resolving mid-flight is what
    # slayer.py and golem_crafting.py already do for the same reason.
    #
    # refresh_legs on top of it because ONE mid-flight correction is not enough here: a single
    # checkpoint suits a target that is drifting, but what drifts in this case is US -- the
    # camera trailing the player's own settle -- so the aim needs correcting repeatedly, all the
    # way to arrival. That is precisely what _move_to_human_tracking was written for; this is its
    # first adoption by a routine, so if clicks start landing oddly, drop back to a bare
    # refresh_target before suspecting anything else.
    # Re-aim off the TILE's projection, not an object lookup. A box trap always sits on a known
    # plan tile, so /ground/walk's direct LocalPoint.fromWorld projection resolves the same point
    # -- while /object/at rebuilds and linear-scans the whole scene (1300+ objects here) to find
    # one trap. MEASURED 2026-08-21: /ground/walk floors at ~5ms against ~20ms for /object/at
    # with a radius and ~32ms at its default. That cost lands INSIDE the click's flight on every
    # re-aim, which is the whole reason to keep it small.
    #
    # Caveat if this is ever copied elsewhere: the forward world->screen conversion behind
    # /ground/walk does not work inside instances (see CLAUDE.md). This cave is not instanced --
    # verified before the first live run -- but an instanced routine must use the object lookup.
    # Whatever is left of the window after the hover and the lookups above -- often nothing.
    _wait_until_free(ctx)

    if not controller.click_entity(ctx.ser, target, refresh_target=refresh,
                                    refresh_legs=TRAP_CLICK_REFRESH_LEGS):
        return False

    # Success is "the tile no longer holds the state we clicked on", NOT "the trap came back to
    # the inventory" -- which is what this waited for until 2026-08-21. Both outcomes are real
    # and only one involves the inventory: checking a full trap re-lays it in place (it never
    # passes through the pack at all), while taking a trap leaves the tile bare. Waiting on the
    # pack made the first case time out after RETRIEVE_CONFIRM_TIMEOUT_S and be recorded as a
    # failed action even though the catch had succeeded. Watching the tile covers both, and
    # covers the failure that matters too: a click that resolved to something else entirely
    # (a chinchompa wandering over the trap's pixels) leaves the state exactly as it was.
    tile = tuple(tile)
    deadline = time.time() + RETRIEVE_CONFIRM_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(CONFIRM_POLL_INTERVAL_S)
        objects = controller.get_objects(radius=OBJECT_SCAN_RADIUS, fresh=True)
        entry = traps_by_tile(objects, [tile]).get(tile)
        if entry is None or entry[0] != state:
            # The click registered -- but the action is NOT over. Checking a trap runs a second
            # animation that re-lays it, and returning here (the tile is already bare by then)
            # let the next tick interrupt that re-lay. See _await_action_finished.
            #
            # Wait for the action to really end. done_when lets that finish the moment the
            # re-laid trap is back on the tile AND the player is idle, instead of paying
            # ACTION_SETTLE_S on top -- see _await_action_finished for why the idle gate is
            # essential (returning early irrespective of the animation measured much worse).
            # Fast path first: the re-laid trap hits the floor as an item well before the
            # animation ends, and two ticks after that the player is free. Only fall back to
            # waiting out the animation when no item appears -- which is the "taken, not re-laid"
            # outcome (before_break's teardown), where none is coming.
            if not _await_placement_settled(ctx, tile):
                _await_action_finished(done_when=lambda: _trap_is_on(tile))
            return True
    debug_log.log("chins", f"the trap at {tile[0]},{tile[1]} is still '{state}' after "
                            f"{RETRIEVE_CONFIRM_TIMEOUT_S:.0f}s -- the click didn't take, "
                            f"retrying next tick")
    return False


def recover_dropped_trap(ctx, tile, item):
    """Pick a timed-out trap back up off the floor. See dropped_traps_by_tile for why this
    exists; without it these quietly despawn and the pack drains.

    Taken through the right-click menu, NOT a plain left-click. A bare click failed on the very
    first real attempt (2513,9301, 13:25:32 on 2026-08-21: the trap never reached the inventory
    and the item despawned) and the reason is the one click_menu_action was written for -- these
    items lie on tiles that fifteen chinchompas wander across, so a click on a shared pixel can
    resolve to Attack instead. Note this does NOT contradict retrieve_trap using a bare click:
    there the menu entries are swapped client-side so the left-click action on a trap OBJECT is
    always right, whereas a ground ITEM still has an ordinary Take option to pick out. A
    mismatch also prints the real menu entries, which is the diagnostic if this ever changes.

    Confirmed by the trap reaching the inventory -- unlike retrieve_trap there is no re-lay
    ambiguity here, an item either gets picked up or it doesn't."""
    controller.wait_until_player_idle(timeout_s=3.0)
    fresh = controller.get_ground_item_at(tile[0], tile[1], tile[2], BOX_TRAP_ITEM_ID)
    target = fresh if fresh is not None else item

    before = box_trap_count()
    ctx.state.data["chin_last_tile"] = list(tile)
    _note_acted_on(ctx, tile)
    debug_log.log("chins", f"a timed-out trap is lying at {tile[0]},{tile[1]} -- picking it back up")

    if not controller.click_menu_action(ctx.ser, target, DROPPED_TRAP_ACTIONS,
                                        target_hint=BOX_TRAP_ITEM_NAME):
        return False

    deadline = time.time() + RETRIEVE_CONFIRM_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(CONFIRM_POLL_INTERVAL_S)
        if box_trap_count() > before:
            _await_action_finished()
            return True
    debug_log.log("chins", f"the dropped trap at {tile[0]},{tile[1]} didn't reach the inventory "
                            f"-- retrying next tick")
    return False


def return_to_anchor(ctx, anchor):
    """Get back on the standing tile. Not cosmetic: laying a trap leaves the player standing on
    it, and a trap with a player on it catches nothing (Box trap.txt)."""
    player = controller.get_player()
    if (player.get("x"), player.get("y"), player.get("plane", 0)) == anchor:
        return
    debug_log.log("chins", f"stepping back to the standing tile {anchor[0]},{anchor[1]}")
    if not travel.walk_to_exact_tile(ctx, anchor[0], anchor[1], anchor[2]):
        # Off-screen -- the player got dragged well away somehow. Fall back to the long-distance
        # walker to get back in render range; the next tick's exact walk finishes the job.
        travel.walk_to(ctx, {"x": anchor[0], "y": anchor[1], "plane": anchor[2]})


# Consecutive failed lay/retrieve attempts before giving up. This routine is built to run
# unattended for hours, and every failure path in it is "log it and try again next tick" -- which
# is right for the transient causes (a chinchompa standing on the tile, a tick-cached /objects
# snapshot one step behind reality) and badly wrong for a permanent one (an inventory with no
# room for the trap coming back, a trap that has stopped responding to every option in its list).
# Without this, a permanent cause is an invisible infinite loop; with it, it's a safety stop with
# the reason in the log.
MAX_CONSECUTIVE_FAILURES = 10


def _record_attempt(ctx, succeeded):
    if succeeded:
        ctx.state.data["chin_fail_streak"] = 0
        return
    streak = ctx.state.data.get("chin_fail_streak", 0) + 1
    ctx.state.data["chin_fail_streak"] = streak
    if streak >= MAX_CONSECUTIVE_FAILURES:
        ctx.state.trigger_safety_stop(
            f"{streak} trap actions in a row failed -- see the log above for the last few "
            f"attempts (a full inventory and a trap that isn't ours are the usual causes)"
        )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def tick(ctx):
    config = ctx.state.config

    trap_count = resolve_trap_count(ctx, config)
    if trap_count is None:
        return  # under-levelled (safety-stopped) or /skills not up yet

    anchor = ensure_anchor(ctx, config)
    if anchor is None:
        return

    objects = controller.get_objects(radius=OBJECT_SCAN_RADIUS)
    plan = ensure_plan(ctx, config, anchor, trap_count, objects)
    traps = traps_by_tile(objects, plan)
    # fresh=True is load-bearing: GroundItemProvider is one of the TickCache-gated providers (see
    # CLAUDE.md), and a stale list means a trap that has just timed out onto the floor is
    # invisible -- the tile then reads "empty" and gets paved with a fresh trap from the pack
    # while the dropped one lies there and despawns. CONFIRMED live 2026-08-21: three items sat
    # at 2511,9299 / 2512,9300 / 2512,9301 and the routine laid over all three within a minute.
    dropped = dropped_traps_by_tile(controller.get_ground_items(fresh=True), plan,
                                     recent=ctx.state.data.get("chin_recent_tiles"))
    queue = work_queue(plan, traps, dropped=dropped)

    # Exactly one action per tick, always the highest-priority one. Deliberately not "do
    # everything the queue lists": the screen positions in `objects` were resolved before any of
    # it happened, and the first action moves the player, which invalidates the rest.
    if queue:
        state, tile, obj = queue[0]
        if state == "dropped":
            _record_attempt(ctx, recover_dropped_trap(ctx, tile, obj))
        elif state == "empty":
            if box_trap_count() <= 0:
                if not traps:
                    # Nothing laid anywhere and nothing to lay -- no way forward on our own.
                    ctx.state.trigger_safety_stop("out of box traps")
                # Otherwise: traps are still out working. Fewer than the plan wants is the normal
                # steady state while one is mid-catch, not an error.
                return
            _record_attempt(ctx, lay_trap(ctx, tile))
        else:
            _record_attempt(ctx, retrieve_trap(ctx, tile, state, obj))
        return

    # Every trap is either set and waiting or mid-animation: nothing to click. Make sure we're
    # not standing on one of them, then let the traps do the work.
    return_to_anchor(ctx, anchor)
    controller.human_delay(0.6, 1.4)


def before_break(ctx):
    """Pick every laid trap back up before the engine's break starts -- the user's headline
    requirement. See controller.py's routine contract and ControllerState.break_due for how this
    gets called; nothing here starts, delays or cancels the break itself, it just runs first.

    Bounded rather than "loop until clean": a trap that can't be retrieved (someone else's, per
    the chat handler, or one that's stopped responding) must not be able to hold the break -- and
    the routine -- hostage. Whatever is left is logged plainly instead of being papered over."""
    stored_plan = ctx.state.data.get("chin_plan")
    if not stored_plan:
        return  # never got as far as laying anything

    plan = [tuple(t) for t in stored_plan]
    debug_log.log("chins", "break is due -- picking the traps up first")

    cleared = False
    for _ in range(len(plan) * 2 + 2):
        objects = controller.get_objects(radius=OBJECT_SCAN_RADIUS, fresh=True)
        traps = traps_by_tile(objects, plan)
        if not traps:
            cleared = True
            break

        actionable = [(tile, state, obj) for tile, (state, obj) in traps.items()
                      if state != "transition"]
        if not actionable:
            # All mid-catch. Waiting a moment is strictly better than clicking through an
            # animation -- a trap that's closing on a chinchompa is about to become a catch.
            controller.human_delay(1.0, 1.6)
            continue

        # Set traps FIRST, then failed, then full -- the opposite of tick()'s ordering, and
        # deliberately so. Only a set or collapsed trap comes off the ground in one action;
        # checking a full one re-lays it, so that tile needs a second action to actually clear.
        # Ordering by "catches first" (what this used to do) made the loop round-robin across
        # tiles, and by the time it returned to one a chinchompa had refilled the trap it had
        # just re-laid -- CONFIRMED live 2026-08-21: four tiles, ten actions, and it still went
        # into the break with 2 traps laid. Preferring set traps makes the tile just checked the
        # top priority on the very next action, so each tile is finished before the next is
        # started. No catch is lost by this: a full trap is still checked (which collects the
        # chinchompa), it just gets picked up immediately afterwards instead of much later.
        actionable.sort(key=lambda entry: (_TEARDOWN_PRIORITY[entry[1]],
                                            entry[2].get("distance", 9999)))
        tile, state, obj = actionable[0]
        retrieve_trap(ctx, tile, state, obj)

    if cleared:
        debug_log.log("chins", "all traps are back in the pack -- starting the break")
        return

    # Exhausted the attempt budget. Re-scan rather than reporting the count from the top of the
    # last iteration: the final attempt may well have succeeded, and "1 trap still laid" when
    # there are none is exactly the kind of log line that sends someone hunting a bug that isn't
    # there.
    still_laid = traps_by_tile(controller.get_objects(radius=OBJECT_SCAN_RADIUS, fresh=True), plan)
    if still_laid:
        debug_log.log("!", f"{len(still_laid)} trap(s) still laid going into the break -- "
                            f"see the entries above for why they couldn't be picked up")
    else:
        debug_log.log("chins", "all traps are back in the pack -- starting the break")


# ---------------------------------------------------------------------------
# Chat signals
# ---------------------------------------------------------------------------
# All lowercased substrings. Each is either the game's only authoritative answer for something
# there's no state field for, or a failure that would otherwise loop forever in silence.
CHAT_CAUGHT = "you've caught"                    # "You've caught a carnivorous chinchompa."
CHAT_NOT_YOUR_TRAP = "isn't your trap"           # another player's trap on one of our tiles
CHAT_CANT_SET = (
    "you can't set a trap here",
    "you can't lay a trap here",
    "there isn't anything to catch",             # the "indoors"/no-prey rejection
)
CHAT_NEED_LEVEL = "you need a hunter level of"
CHAT_TRAP_LIMIT = "you can only set"             # "You can only set N trap(s) at a time."


def on_event(ctx, event_type, payload):
    if event_type != "CHAT_MESSAGE" or not payload:
        return
    message = (payload.get("message") or "").lower()

    if CHAT_CAUGHT in message:
        caught = ctx.state.data.get("chin_catches", 0) + 1
        ctx.state.data["chin_catches"] = caught
        debug_log.log("chins", f"caught one ({caught} this session)")
        return

    last_tile = ctx.state.data.get("chin_last_tile")

    if CHAT_NOT_YOUR_TRAP in message and last_tile:
        block_tile(ctx, last_tile, "another player's trap is on it")
        return

    if any(marker in message for marker in CHAT_CANT_SET) and last_tile:
        block_tile(ctx, last_tile, "the game refuses a trap there")
        return

    if CHAT_NEED_LEVEL in message:
        ctx.state.trigger_safety_stop("the game refused the trap: Hunter level too low")
        return

    if CHAT_TRAP_LIMIT in message:
        # Belt-and-braces against resolve_trap_count's level table disagreeing with the live game
        # (a Hunter level drained below the threshold, say). Drop one and let ensure_plan rebuild
        # a smaller layout rather than re-clicking a trap the game keeps refusing.
        current = ctx.state.data.get("chin_trap_count")
        if current and current > 1:
            ctx.state.data["chin_trap_count"] = current - 1
            ctx.state.data.pop("chin_plan_key", None)
            debug_log.log("chins", f"the game says the trap limit is lower than {current} -- "
                                    f"dropping to {current - 1}")
