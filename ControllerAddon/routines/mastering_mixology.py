"""routines/mastering_mixology.py

Automates Mastering Mixology (see docs/superpowers/specs/2026-08-12-mastering-mixology-design.md
for the original plan): bank herbs -> refine -> hopper -> read the 3 active orders -> mix ->
process -> deposit. Always makes and deposits all 3 current orders together every cycle (see
_choose_orders) rather than the original plan's max-points-per-paste selection, which skipped
single-paste orders (AAA/MMM/LLL) without an MAL order present -- changed 2026-08-12 per explicit
user direction, to always capture the wiki-confirmed resin bonus for depositing multiple orders
at once (+20% for 2, +40% for 3).

EVENT-DRIVEN SINCE 2026-09-06 (USER: "make it as tick perfect as can be with our new events";
docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md). Every wait goes through
`_await`: a pushed UDP signal decides WHEN to look, one HTTP read decides WHAT happened, and the
HTTP read alone decides at the budget -- so an old client, `--no-events`, or a dropped packet costs
latency, never a wrong click. The signals, all of them the game's own state rather than chat text:

- `VARBIT_CHANGED` on the minigame's varbits (VARBIT_SUBSCRIPTIONS, ids from runelite-api 1.12.38
  `VarbitID` via javap 2026-09-06): mixer slots say a lever pull LANDED, the vessel varbit says
  WHAT is in the vessel, a station's potion varbit flips non-zero on start and to 0 on finish, and
  a station's progress varbit changes exactly once per game tick while it works -- which is the
  Retort's "click every tick" clock.
- `MENU_CLICKED` is the game's verdict on WHICH lever a click hit (three stand side by side).
- `GRAPHICS_OBJECT_CREATED` (added to the bridge the same day, needs a client rebuild) is the
  Agitator/Alembic flash cue itself, id 2954 / 2955 at the station's tile; polling
  `/graphics-objects` is the fallback.
- `INVENTORY_CHANGED` for takes, finishes, the belt deposit, the refiner and the bank.

What was measured live in the 2026-08-12/13 sessions and still shapes this file:
- The vessel's plain `id` is always the multiloc base (55395); only `impostorId` -- or, now, the
  vessel varbit -- reflects content. Taking a base prints "You collect some X from the mixing
  vessel."; processing transforms the item IN PLACE with the same display name (id +10), so only
  ids tell unprocessed from processed.
- A station left-click processes the LEFTMOST unprocessed potion; an extra Retort click landing
  after the potion finished auto-starts the NEXT unprocessed potion at the Retort regardless of
  its intended station ("we made the MMA with the retort instead of the Agi[tator]"). Two defences
  now: `_choose_orders` makes the Retort order(s) LAST so nothing unprocessed remains when the
  Retort finishes, and the Retort loop only clicks on a strictly-increasing progress value.
- Retort progress climbs by exactly 1 per tick (1..21 for a Mammoth-might mix) and resets to 0 in
  the same instant the potion varbit clears; total ticks vary by potion tier and Herblore level, and
  Mystic mana amalgam needed well over 45s un-boosted, hence the 90s finish budgets.
- The lab's camera retargets almost every tick; camera-settle heuristics were scrapped
  (USER: "lets scrap the camera bullshit"). Clicks now aim through `object_refresh_target`
  (uncached `/object/at`) so the flight tracks the projection while the player walks -- the
  wait-for-stillness gate every world click used to pay is gone.
- Digweed: only the "Mature"/blooming stage is collectable and the young stage's name also contains
  "digweed"; digweed is held until a Liplack liquor base is in hand (USER: "we just save it until
  there is a LLL in our order").

Live-verification worklist for the first session in this form (none of the below has run live):
1. `MENU_CLICKED.target` for a lever reads "Mox lever"/"Aga lever"/"Lye lever" (the /last-action
   target did) and the mixer-slot varbits 11324-11326 change on the pull's tick.
2. Vessel varbit 11339 = 1-indexed POTION_NAMES index (`vessel_potion_name`) -- cross-check one
   base against `impostorId`.
3. `GRAPHICS_OBJECT_CREATED` lands for ids 2954/2955 with x/y within FLASH_CUE_RADIUS of the
   station object's own reported tile (the station is multi-tile; the cue may sit on a neighbour).
4. The Alembic click lands inside its 1-tick window with FLASH_REACTION_S; the skillshot varbits
   11337/11338 CHANGE on a hit (noots-mixology: a change from baseline, not non-zero).
5. Retort: one click per progress tick, zero clicks after the potion varbit reads 0.
"""

import random
import time

import banker
import bridge_events
import controller
import debug_log
import outcome_waits

MAX_OBJECT_DISTANCE = 15
HERBLORE_LEVEL_REQUIRED = 60

# name -> {mox, aga, lye} -- matches the varbit type-index order MixologyProvider reports (see
# this plan's Global Constraints), NOT alphabetical or wiki-page order. Its ORDER is load-bearing:
# vessel_potion_name indexes into it (varbit value - 1).
RECIPE_TABLE = {
    "Mammoth-might mix": {"mox": 3, "aga": 0, "lye": 0},
    "Mystic mana amalgam": {"mox": 2, "aga": 1, "lye": 0},
    "Marley's moonlight": {"mox": 2, "aga": 0, "lye": 1},
    "Alco-augmentator": {"mox": 0, "aga": 3, "lye": 0},
    "Azure aura mix": {"mox": 1, "aga": 2, "lye": 0},
    "Aqualux amalgam": {"mox": 0, "aga": 2, "lye": 1},
    "Liplack liquor": {"mox": 0, "aga": 0, "lye": 3},
    "Megalite liquid": {"mox": 1, "aga": 0, "lye": 2},
    "Anti-leech lotion": {"mox": 0, "aga": 1, "lye": 2},
    "Mixalot": {"mox": 1, "aga": 1, "lye": 1},
}
POTION_NAMES_BY_INDEX = list(RECIPE_TABLE)

# ItemID.MM_POTION_*_UNFINISHED / _FINISHED (net.runelite.api.gameval.ItemID,
# runelite-api-1.12.35.jar) -- processing transforms a base potion in place and the display
# NAME is identical before and after (confirmed: Mammoth-might mix has id1=30011 (Unfinished)
# and id2=30021 (Processed) sharing one `name` field in the wiki infobox), so name matching
# cannot distinguish unprocessed from processed. This ordering matches VESSEL_ID_TO_BASE's
# object-id order (NOT RECIPE_TABLE's varbit order) -- see this plan's Global Constraints.
UNFINISHED_IDS = {
    30011: "Mammoth-might mix", 30012: "Mystic mana amalgam", 30013: "Marley's moonlight",
    30014: "Alco-augmentator", 30015: "Aqualux amalgam",      30016: "Azure aura mix",
    30017: "Liplack liquor",   30018: "Anti-leech lotion",    30019: "Megalite liquid",
    30020: "Mixalot",
}
FINISHED_IDS = {k + 10: v for k, v in UNFINISHED_IDS.items()}

# ItemID.MM_LAB_SPECIAL_HERB -- Digweed. Used instead of a sticky ctx.state.data flag (see Fix 5
# in the final whole-branch review) because the plant withers out of inventory if the player
# leaves the minigame area, which would leave a flag-based confirm stuck wrong.
MM_LAB_SPECIAL_HERB_ID = 30031

# --- Varbits (runelite-api 1.12.38 VarbitID, javap 2026-09-06) ------------------------------
VB_ORDER_1_TYPE = 11315
VB_MIXER_SLOTS = (11324, 11325, 11326)            # MM_LAB_MIXER_SLOT_0..2
VB_RETORT_PROGRESS = 11327
VB_ALEMBIC_PROGRESS = 11328
VB_AGITATOR_PROGRESS = 11329
VB_HERB_READY = (11330, 11331, 11332, 11333)      # digweed NE/SE/SW/NW
VB_SKILLSHOT_AGITATOR = 11337
VB_SKILLSHOT_ALEMBIC = 11338
VB_VESSEL_READY = 11339                           # 1-indexed potion in the vessel, 0 empty
VB_AGITATOR_POTION = 11340
VB_RETORT_POTION = 11341
VB_ALEMBIC_POTION = 11342

# station name -> (potion varbit, progress varbit)
STATION_VARBITS = {
    "Retort": (VB_RETORT_POTION, VB_RETORT_PROGRESS),
    "Agitator": (VB_AGITATOR_POTION, VB_AGITATOR_PROGRESS),
    "Alembic": (VB_ALEMBIC_POTION, VB_ALEMBIC_PROGRESS),
}
SKILLSHOT_VARBITS = {"Agitator": VB_SKILLSHOT_AGITATOR, "Alembic": VB_SKILLSHOT_ALEMBIC}
# How many progress ticks the quick-action window stays open after the cue (noots-mixology's
# flashWindow: agitator 2, alembic 1; the wiki says the same).
FLASH_WINDOW_TICKS = {"Agitator": 2, "Alembic": 1}

VARBIT_SUBSCRIPTIONS = (
    (VB_ORDER_1_TYPE,) + VB_MIXER_SLOTS
    + (VB_RETORT_PROGRESS, VB_ALEMBIC_PROGRESS, VB_AGITATOR_PROGRESS)
    + VB_HERB_READY
    + (VB_SKILLSHOT_AGITATOR, VB_SKILLSHOT_ALEMBIC, VB_VESSEL_READY,
       VB_AGITATOR_POTION, VB_RETORT_POTION, VB_ALEMBIC_POTION)
)

# --- Budgets (seconds). "+ walk" means _walk_budget(entity) is added at the call site. ----------
GAME_TICK_S = bridge_events.GAME_TICK_MS / 1000.0
WALK_S_PER_TILE = 0.6           # a queued interaction is performed on ARRIVAL: budget the walk
CHAIN_MAX_S = 25.0              # the mix chain hands the tick back after this long regardless
MENU_VERDICT_S = 1.0            # click -> MENU_CLICKED / a newer /last-action
PULL_LAND_S = 1.5               # + walk: verdict -> a mixer-slot varbit change
VESSEL_TAKE_S = 2.0             # + walk: vessel click -> the base id in the inventory
STATION_START_S = 2.0           # + walk: station click -> its potion varbit non-zero
STATION_FINISH_S = 90.0         # measured: some potions need well over 45s un-boosted
RETORT_MAX_S = 90.0
RETORT_HTTP_CONFIRM_S = 2.0     # the Retort loop re-reads /mixology-state this often (lost packet)
RETORT_CLICK_PHASE_S = (0.05, 0.30)   # click this long after the progress tick lands
FLASH_WAIT_S = 15.0             # the cue always lands inside the first third of processing
FLASH_CUE_RADIUS = 3            # tiles between the cue's tile and the station object's tile
FLASH_REACTION_S = {"Agitator": (0.20, 0.45), "Alembic": (0.10, 0.28)}   # the Alembic window is ONE tick
SKILLSHOT_CONFIRM_S = 1.5
DEPOSIT_S = 3.0                 # + walk
HOPPER_DEPOSIT_S = 3.0          # + walk
HOPPER_DEPOSIT_TRIES = 3
REFINE_START_S = 3.0            # + walk: first herb consumed = we arrived and refining began
REFINE_MAX_S = 60.0             # a full inventory refines in well under this at 1/tick
REFINE_CLICK_PHASE_S = (0.05, 0.30)
BANK_WITHDRAW_LAND_S = 5.0      # measured: 3.0 was sometimes too tight for the bridge read
DIGWEED_APPLY_S = 2.0
DIGWEED_PICK_S = 3.0            # + walk
DIGWEED_STATE_POLL_S = 10.0     # /mixology-state read cadence without a pushed herb varbit
LEVER_PULL_MAX_ATTEMPTS = 3
MENU_VERDICT_SHORTEN_S = 0.15   # a MENU_CLICKED for the WRONG target ends the verdict wait early
TICK_CHAIN_MAX = 3              # phase transitions run inside one tick() before handing back
NEW_ORDERS_S = 1.5              # belt deposit -> the game has written the three new orders
STALL_WARN_S = 120.0            # no progress signature change: log a [!] line ...
STALL_STOP_S = 300.0            # ... then safety-stop (the longest legit silence is one station)
FOREIGN_STATION_STOP_S = 90.0   # a station still frozen after the collect clicks have been spent
# USER 2026-09-17, the station mechanics: every station advances ON ITS OWN once started and
# spits the finished potion out by itself. Input only ACCELERATES it -- the Retort by spam
# clicking, the Agitator and Alembic by a click on their flash cue. So progress that is not
# moving means the station is not running at all ("basically a misclick"), which makes a frozen
# progress bar an unambiguous fault signal rather than something to wait out.
#
# A running station moves 1 per game tick (0.6s), so ~2s of no change is already 3 missed ticks.
# MEASURED 2026-09-17: at the first-draft 4.0/6.0 these two waits ran BACK TO BACK (the finish
# wait spent one, then the restart helper started its own from scratch) and cost 8-11s per
# station -- about half of a ~60s three-order cycle spent noticing. A false positive is cheap:
# the station is occupied, so a re-click cannot auto-start anything, and on the Agitator/Alembic
# an extra click outside the cue window simply does nothing.
COLLECT_STALL_S = 2.0           # progress unchanged this long: the station is not running
COLLECT_MAX_TRIES = 4
FINISH_STALL_S = 2.0            # the finish wait hands back this soon once progress stops moving
# Hold a finished potion no live order wants rather than depositing it for a reduced reward --
# but not forever: past this many held, take the reduced reward instead of filling the inventory.
HOLD_UNMATCHED_MAX = 6
RESTART_CONFIRM_S = 3.0         # restart click -> progress moving again / the potion delivered
# Park the cursor only once the station's projection has stopped sliding (same idea, and the same
# numbers, as mining_guild_iron's arrival settle).
# Capped low on purpose: this is paid before EVERY anticipatory hover, and there are nine lever
# pre-hovers in a cycle. USER 2026-09-17: "significantly slower on the lever" at 1.2s, where a
# projection that never converges burned the whole ceiling nine times over. A genuinely sliding
# projection is caught well inside this; one that is still settles in two reads (~0.16s).
HOVER_SETTLE_MAX_S = 0.4        # the walk-in plus the camera glide; past this, hover anyway
HOVER_SETTLE_POLL_S = 0.08
HOVER_SETTLE_PX = 3             # two consecutive projection reads this close = settled
# FOLLOWING the target while the player walks (2026-09-22, see _hover_when_settled). The track
# runs only while /player says we are moving, so a still player pays the old settle and no more;
# the ceiling is the longest in-lab walk (vessel -> Retort/belt is ~5 tiles) with margin.
HOVER_TRACK_MAX_S = 3.5
HOVER_TRACK_DRIFT_PX = 6        # re-aim past this: under CLICK_IN_PLACE_RADIUS_PX (12) with jitter

# Where the stations stand (hex-agon plugin's AlchemyObject, world tiles): vessel (1394,9326),
# Alembic (1391,9325), Agitator (1393,9329), Retort (1397,9325), belt (1394,9331). The Retort
# is LAST for safety (see _choose_orders); of the Retort-last orders, vessel -> Alembic ->
# Agitator -> Retort -> belt is the shortest walk, so that is the rank.
STATION_RANK = {"Alembic": 0, "Agitator": 1, "Retort": 2}

# Refined paste, bankable and stackable. Withdrawing it straight from the bank skips the whole
# herb -> clean -> refine chain, so a hopper top-up costs one withdrawal instead of an inventory
# of herbs plus a cleaning sweep plus a refiner loop (USER 2026-09-18: "make it able to withdraw
# lye, mox and aga directly from the bank i have some stored up").
#
# EXACT names, never a "paste" substring: 'Swamp paste' is an unrelated item this account also
# banks 547 of. Ids are the wiki's (Mox 30005 / Aga 30007 / Lye 30009), kept for the inventory
# read where a name match would be just as ambiguous.
PASTE_ITEM_NAMES = {"mox": "Mox paste", "aga": "Aga paste", "lye": "Lye paste"}
PASTE_ITEM_IDS = {"mox": 30005, "aga": 30007, "lye": 30009}

DEFAULT_CONFIG = {
    "enabled": True,
    "herbPriority": {
        "mox": ["Guam leaf", "Marrentill", "Tarromin", "Harralander"],
        "aga": ["Huasca", "Irit leaf", "Cadantine", "Lantadyme", "Dwarf weed", "Torstol"],
        # Ranarr weed and Snapdragon deliberately excluded (user request 2026-08-12): reserved
        # for other uses, never to be consumed by this routine even if in stock.
        "lye": ["Toadflax", "Avantoe", "Kwuarm"],
    },
    "hopperTarget": 1000,
    "hopperLowWater": 150,
    "digweed": {"enabled": True},
    "speedups": {"retort": True, "agitator": True, "alembic": True},
    "maxObjectDistance": MAX_OBJECT_DISTANCE,
    "bank": {"teleportFallback": False},
    "breaks": {
        "enabled": True, "everyMinMinutes": 45, "everyMaxMinutes": 75,
        "durationMinMinutes": 3, "durationMaxMinutes": 12, "microBreaks": True,
    },
    "safety": {"stopOnDeath": True, "maxRuntimeHours": 0},
}


def get_config(bridge_config):
    config = controller.get_mastering_mixology_config()
    if config is None:
        return DEFAULT_CONFIG

    # ConfigExporter.exportMasteringMixology only emits enabled/bank/digweed/speedups/breaks/
    # safety -- herbPriority/hopperTarget/hopperLowWater/maxObjectDistance silently vanish
    # whenever the Controller plugin is actually running (i.e. always, in real deployment) if the
    # live bridge config were returned as-is, even though every phase function still reads them
    # via config.get(key, DEFAULT_CONFIG[key]) and so never crashes -- it just silently ignores
    # any user customization of those keys. Same bug golem_crafting.py's get_config hit and fixed
    # for its own "locations" key -- same fix shape here.
    config = dict(config)
    for key in ("herbPriority", "hopperTarget", "hopperLowWater", "maxObjectDistance"):
        config.setdefault(key, DEFAULT_CONFIG[key])
    return config


# --- clock + event plumbing ------------------------------------------------------------------
# Every wait runs on _now/_sleep so the test fixture can install a fake clock (same shape as
# blast_furnace.py).

def _now():
    return time.time()


def _sleep(s):
    if s > 0:
        time.sleep(s)


def _events(ctx):
    return outcome_waits.events_of(ctx)


def _mark(ctx):
    return outcome_waits.mark(ctx)


# The wait helpers live in outcome_waits.py since 2026-09-06 (golem_crafting needed the same
# shape); these wrappers bind this module's clock so the test fixture's fake _now/_sleep applies.

def _await(ctx, signals, budget_s, confirm, since=None, reaction=True, shorten=None):
    return outcome_waits.await_pre(ctx, signals, budget_s, confirm, since=since,
                                   reaction=reaction, shorten=shorten,
                                   now_fn=_now, sleep_fn=_sleep)


def _still_segment_s() -> float:
    return outcome_waits.still_segment_s()


def _await_long(ctx, signals, budget_s, confirm, since=None, stop=None, fidget=True,
                reaction=True):
    return outcome_waits.await_long(ctx, signals, budget_s, confirm, since=since, stop=stop,
                                    fidget=fidget, reaction=reaction,
                                    now_fn=_now, sleep_fn=_sleep)


_varbit_in = outcome_waits.varbit_in
_chat_has = outcome_waits.chat_has


def _tick_pause(ctx, n=1, phase=None):
    outcome_waits.tick_pause(ctx, n, phase, now_fn=_now, sleep_fn=_sleep)


def _walk_budget(entity) -> float:
    return outcome_waits.walk_budget(entity, WALK_S_PER_TILE)


# --- object access ---------------------------------------------------------------------------

def find_object(name: str, exclude: str = None):
    """Closest visible object whose name contains `name` (and does NOT contain `exclude`, if
    given), within MAX_OBJECT_DISTANCE -- same shape (including the radius fix) as
    golem_crafting.py's find_object. Every Mastering Mixology fixture is a multiloc/impostor
    object, so this resolves correctly regardless of which content variant (empty, or holding a
    specific potion) is currently active -- see the design doc's "Physical objects are
    multiloc" note.

    `exclude` exists for Digweed: CONFIRMED live 2026-08-12 (user report: "digweed spawned and
    we clicked the wrong one, should have been name=Mature not young") -- the wiki confirms
    digweed grows through a young stage before it matures and blooms into the collectible
    plant, and the young stage's object name apparently also contains "digweed", so a plain
    substring match can grab the wrong growth stage."""
    name_lower = name.lower()
    exclude_lower = exclude.lower() if exclude else None
    candidates = [
        o for o in controller.get_objects(radius=MAX_OBJECT_DISTANCE)
        if name_lower in (o.get("name") or "").lower()
        and (exclude_lower is None or exclude_lower not in (o.get("name") or "").lower())
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def _refresh_for(entity):
    """object_refresh_target for a bulk-scan entity: the uncached /object/at lookup re-resolves
    the projection at every leg of the flight, so the click tracks the object while the player
    walks and the camera retargets. Keyed on the multiloc BASE id (`id`), which never changes
    with content, not on impostorId."""
    if entity is None:
        return None
    x, y, plane, obj_id = entity.get("x"), entity.get("y"), entity.get("plane"), entity.get("id")
    if None in (x, y, plane, obj_id):
        return None
    return controller.object_refresh_target(x, y, plane, obj_id, radius=MAX_OBJECT_DISTANCE)


def _click_object(ctx, name: str, exclude: str = None, **kwargs):
    """Resolve the closest visible object named `name` and click it, aiming through
    _refresh_for so the flight follows the live projection. Returns the entity, or None when
    it was not found or the click was refused/withheld (so callers retry next tick).

    REPLACES the wait-for-stillness gate (`wait_until_player_idle`) every world click here used
    to pay, 2026-09-06: that gate existed only to protect a screen position resolved ONCE; a
    refresh_target re-resolves it in flight, which is the CLAUDE.md preferred pattern and what
    the human does (click the next thing while still walking)."""
    entity = find_object(name, exclude=exclude)
    if entity is None:
        return None
    ok = controller.click_entity(ctx.ser, entity, refresh_target=_refresh_for(entity), **kwargs)
    if not ok:
        # Found but not clicked (aim outside the window, a withheld hover-verified click). NOT
        # the same as "no such object": the caller's outcome confirm fails and its own retry
        # path runs, where None would have meant a safety stop (2026-09-06).
        debug_log.log("mixology", f"click on '{name}' was refused -- the outcome confirm will retry it")
    return entity


def _hover_object(ctx, name: str, exclude: str = None, **kwargs):
    """Rest the cursor on the object (no click) so a later time-critical click is a click in
    place (controller.click_human) -- the Retort's per-tick clicks, the flash cues, the
    refiner. Courtesy move, never an error."""
    entity = find_object(name, exclude=exclude)
    if entity is None:
        return None
    controller.hover_entity(ctx.ser, entity, refresh_target=_refresh_for(entity), **kwargs)
    return entity


def _aim_offset(name: str):
    """The aim nudge for a pre-hover or click on `name`: the model's CENTRE for a lever, the
    shared (-7, +7) default for everything else. LIVE 2026-09-22: a lever pre-hover parked at
    projection + (-7, +7) sat just off the thin lever model, the in-place click there resolved
    as "Walk here" (last-action target ''), and every retry counted as already-in-place and hit
    the same pixel -- three misses inside a second and a safety stop. CLAUDE.md's standing
    remedy for small targets is offset=(0, 0)."""
    return (0, 0) if "lever" in (name or "").lower() else (-7, 7)


def _player_moving() -> bool:
    """/player's per-frame `moving` flag (uncached). False when unreadable, so a bridge hiccup
    ends a track early rather than holding it to the ceiling."""
    return bool((controller.get_player() or {}).get("moving"))


def _hover_when_settled(ctx, station_name: str, stop=None, **kwargs):
    """Park the cursor on an object and KEEP it there while the player walks up to it.

    USER 2026-09-17, watching it live: "we dont wait until we are stopped to hover meaning the
    cursor slips out". The hover that parks the cursor for a station's speedup is taken right
    after the station click, while the player may still be walking in and the camera is still
    gliding -- so it aims at a projection that keeps sliding out from under it, and the cursor
    ends up beside the station rather than on it. On the Retort a click that lands beside it
    resolves as "Walk here", and walking CANCELS the concentration. USER again, on the lever
    pre-hovers: "still doing hovering on the levers without settling so they are pointless" --
    so this is used for EVERY anticipatory hover, not just the station parks.

    REWRITTEN 2026-09-22 (USER: "the hover not accounting for our movement in a few tasks like
    levers and take from mixing vessel, ends up looking really strange"). The settle above was
    capped at HOVER_SETTLE_MAX_S (0.4s, kept short because the levers paid it nine times a
    cycle), and a walk to the lever or the vessel lasts longer than that -- so the hover parked
    mid-walk on a projection still sliding, the cursor was left behind as the player carried on,
    and the next click flew after it. hover_entity also departed toward the bulk-scan SNAPSHOT
    (fixed there). Now the cursor goes to the target at once and FOLLOWS it -- one short re-aim
    whenever the live projection drifts past HOVER_TRACK_DRIFT_PX -- for as long as /player
    reports the player moving, up to HOVER_TRACK_MAX_S. The time is not lost: every caller's
    next outcome (the pull landing, the take, the station start) needs the player to arrive
    first. A still player (the levers stand side by side) takes the old two-read settle.

    `stop`: the caller's own outcome check (the pull landed, the base is in the inventory),
    which ends the follow the moment it holds. MEASURED live 2026-09-22 that /player's `moving`
    reads False a full tick AFTER arrival -- the lever/belt animation has already started while
    it still says True -- so without this the follow held the cursor ~0.5s past the outcome, and
    the cycle came out ~1s slower per base.
    Past the ceiling the hover stands regardless: _click_station_in_place and click_entity's
    in-place test both verify before committing."""
    entity = find_object(station_name)
    if entity is None:
        return None
    kwargs.setdefault("offset", _aim_offset(station_name))
    refresh = _refresh_for(entity)
    if refresh is None:
        return _hover_object(ctx, station_name, **kwargs)

    offset = kwargs["offset"]
    hovered = False
    prev = None
    still_reads = 0
    deadline = _now() + HOVER_TRACK_MAX_S
    while True:
        cur = refresh()
        if cur is None:
            break                          # unresolvable (gone, off-scene): the fallback below
        else:
            aim =(int(cur[0]) + offset[0], int(cur[1]) + offset[1])
            drifted = True
            if hovered:
                cx, cy = controller.get_cursor_pos()
                drifted = abs(aim[0] - cx) > HOVER_TRACK_DRIFT_PX or abs(aim[1] - cy) > HOVER_TRACK_DRIFT_PX
            if drifted:
                target = dict(entity, screenX=int(cur[0]), screenY=int(cur[1]))
                hovered = controller.hover_entity(ctx.ser, target, refresh_target=refresh, **kwargs) or hovered
        if hovered and stop is not None and stop():
            break
        if _player_moving():
            still_reads = 0
        else:
            still_reads += 1
        settled = (cur is not None and prev is not None
                   and abs(cur[0] - prev[0]) <= HOVER_SETTLE_PX
                   and abs(cur[1] - prev[1]) <= HOVER_SETTLE_PX)
        # Stopped walking AND the projection has stopped sliding (the camera glide outlasts the
        # walk by a frame or two). A projection that never converges -- the lab's camera -- is
        # what HOVER_SETTLE_MAX_S bounded before; still_reads bounds it the same way here.
        if hovered and still_reads >= 1 and (settled or still_reads * HOVER_SETTLE_POLL_S >= HOVER_SETTLE_MAX_S):
            break
        if _now() >= deadline:
            break
        prev = cur
        _sleep(HOVER_SETTLE_POLL_S)
    if not hovered:
        return _hover_object(ctx, station_name, **kwargs)
    return entity


def _click_station_in_place(ctx, station_name: str) -> bool:
    """Fire a speedup click ONLY once the cursor is provably on `station_name`.

    A speedup click is a raw click_human at whatever pixel the cursor holds, and the hover that
    parked it there can be tens of seconds old. LIVE 2026-09-17: the Agitator's cue clicks fired
    ~260px off it (idle drift, plus the camera moving the projection during the wait), both
    attempts missed, and one landed in the then-empty Alembic -- which auto-starts the next
    unprocessed potion there (the wrong-station bug _process_retort_speedup documents), occupying
    the station the routine needed next and livelocking 'Alembic still busy' until the stall stop.

    On the RETORT a stray click is worse still: it resolves as "Walk here", and walking CANCELS
    the concentration -- LIVE 2026-09-17 the potion froze at progress 2 and never moved again,
    with /hover naming the Retort while /last-action showed the walk.

    So: verify, re-aim, verify again, and DROP the click if it still cannot be placed. A missed
    speedup costs a few progress points; a stray one costs the whole run. The second re-aim drops
    the default (-7,+7) hover nudge for the model's centre -- that nudge sitting just off a small
    station's model is what produces the "Walk here", and a centred aim is CLAUDE.md's documented
    remedy. hover_matches degrades to True when it cannot verify (no /hover, a menu open), so an
    un-rebuilt client keeps the old behaviour rather than never clicking at all."""
    if controller.hover_matches(expected_target=station_name):
        controller.click_human(ctx.ser)
        return True
    for offset in ((-7, 7), (0, 0)):
        if _hover_object(ctx, station_name, check_ui_blockers=False, offset=offset) is None:
            continue
        if controller.hover_matches(expected_target=station_name):
            controller.click_human(ctx.ser)
            return True
    debug_log.log("mixology", f"speedup click withheld -- cursor is not on the {station_name}")
    return False


def check_level_requirement(ctx, config: dict) -> bool:
    """One-time-per-activation under-level sanity check -- same shape and same setup()-vs-tick()
    reasoning as golem_crafting.py's check_level_requirement.

    Returns False (without performing the check) if Herblore's reported level is 0 -- CONFIRMED
    live 2026-08-12: every real OSRS skill's minimum level is 1, so a 0 reading is never a real
    under-level account, it's /skills responding in the brief window right after
    login.is_fully_logged_in() first reports True but before RuneLite's own skill-data event has
    actually landed. Trusting it blindly permanently safety-stopped a perfectly fine level-90+
    account during this exact live session. Caller should retry next tick rather than mark the
    check done when this returns False."""
    herblore = controller.get_skills().get("Herblore") or {}
    level = herblore.get("level")
    if level == 0:
        return False
    if level is not None and level < HERBLORE_LEVEL_REQUIRED:
        ctx.state.trigger_safety_stop(
            f"Herblore level {level} is below the {HERBLORE_LEVEL_REQUIRED} required for Mastering Mixology"
        )
    return True


# --- banking / refining ----------------------------------------------------------------------

def bank_herbs_phase(ctx, config: dict) -> None:
    """Withdraw herbs per herbPriority (first candidate actually in the bank, per paste type),
    refine them, deposit the resulting paste into the hopper. Repeats (the phase re-enters
    itself on the next tick if the hopper target isn't reached yet) until every paste type in
    /mixology-state's hopper totals reaches hopperTarget, or safety-stops if the bank has none
    of a paste type's configured herb candidates left at all."""
    hopper = controller.get_mixology_state().get("hopper", {})
    target = config.get("hopperTarget", 1000)
    if all(hopper.get(paste, 0) >= target for paste in ("mox", "aga", "lye")):
        debug_log.log("mixology", f"hopper stocked ({hopper}) -- moving to orders phase")
        ctx.state.data["mm_phase"] = "orders"
        return

    herb_priority = config.get("herbPriority", DEFAULT_CONFIG["herbPriority"])
    needed_types = [p for p in ("mox", "aga", "lye") if hopper.get(p, 0) < target]
    if not needed_types:
        ctx.state.data["mm_phase"] = "orders"
        return

    bank_cfg = {"teleportFallback": config.get("bank", {}).get("teleportFallback", False)}
    bank_obj = banker.find_nearby_bank(bank_cfg)
    if bank_obj is None:
        ctx.state.trigger_safety_stop("no bank found near Mastering Mixology (and no teleport fallback configured)")
        return
    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank")
        return

    # CORRECTED 2026-08-13 (user directive: "shouldn't we check do we have any containers? Is
    # our inventory empty? ... instead of just always clicking them"): only reach for the deposit
    # buttons when there is something to deposit -- a human wouldn't click them without a reason.
    inventory = controller.get_inventory()
    known_container_names = (
        banker.LOOTING_BAG_NAME, banker.HERB_SACK_NAME, banker.SEED_BOX_NAME,
        banker.COAL_BAG_NAME, banker.FUR_POUCH_NAME, "gem bag",
    )
    if any(
        name in (item.get("name") or "").lower()
        for item in inventory
        for name in known_container_names
    ):
        banker.deposit_all_containers(ctx)
        controller.human_delay(0.3, 0.5)

    if inventory:
        deposit_all = controller.get_bank().get("depositInventory") or {}
        if deposit_all.get("screenX") is not None:
            mark = _mark(ctx)
            controller.click_entity(ctx.ser, deposit_all, offset=(0, 0), check_ui_blockers=False,
                                    simulate_mistakes=False)
            _await(ctx, "INVENTORY_CHANGED", 1.5, confirm=lambda: not controller.get_inventory(),
                   since=mark)

    # CONFIRMED live 2026-08-12 (user report): searching each configured herb name one at a
    # time raced against reality (a candidate in stock at selection time could be out of stock by
    # the click-specific read). Tab 8 (user's own dedicated herb-storage tab) surfaces every herb
    # in one view, so candidate selection and the withdrawal read the same live state.
    # MOVED AHEAD OF THE PASTE STEP 2026-09-22 (USER: "No need to search for mox etc its also in
    # the herb tab"): banked paste sits in tab 8 too, so one tab switch serves both steps and the
    # bank search is gone entirely -- see _withdraw_banked_paste for the live failure it caused.
    if not banker.switch_bank_tab(ctx, 8):
        banker.close_bank(ctx)
        ctx.state.trigger_safety_stop("could not switch to the herb bank tab")
        return

    # BANKED PASTE FIRST. It is already refined, so it goes straight to the hopper -- no herbs to
    # withdraw, clean or refine. Whatever the bank cannot cover falls through to the herb path
    # below, so a half-stocked paste type still works.
    needed_types = _withdraw_banked_paste(ctx, needed_types, hopper, target)
    if not needed_types:
        banker.close_bank(ctx)
        _refine_and_hopper_inventory(ctx)
        return
    # CORRECTED 2026-08-13 (user question: "why do we only pick 14 herbs cant we just clean 28
    # at once?"): fill the whole inventory split evenly across whatever paste types are needed --
    # every slot here holds a raw herb, nothing else competes for the space during this phase.
    # Computed AFTER the paste step, over the types it could not cover.
    per_type_qty = 28 // len(needed_types)

    bank_contents = controller.get_bank(fresh=True).get("items", [])
    chosen_names = {}
    for paste_type in needed_types:
        candidates = herb_priority.get(paste_type, [])
        # Substring match (not exact) -- herbPriority's candidates are clean names ("Avantoe"),
        # but the account can hold the grimy form ("Grimy avantoe") instead, or as well;
        # CORRECTED live 2026-08-12 -- an exact match missed real, in-stock Kwuarm/Avantoe.
        # qty > 0 alongside it is what defeats the zero-stock placeholder entries.
        chosen = next(
            (name for name in candidates
             if any(name.lower() in (i.get("name") or "").lower() and i.get("qty", 0) > 0
                    for i in bank_contents)),
            None,
        )
        if chosen is None:
            banker.close_bank(ctx)
            ctx.state.trigger_safety_stop(
                f"none of the configured {paste_type} herbs ({candidates}) are in the bank"
            )
            return
        chosen_names[paste_type] = chosen

    # Withdrawals: shift-click (OSRS's own withdraw-all shortcut) when exactly one paste type is
    # needed this trip (28 // 1 = 28 is the whole inventory anyway, user directive 2026-08-13);
    # the per-item right-click "Withdraw-X" + typed amount otherwise, because shift-click would
    # fill the whole inventory with the FIRST type. UNVERIFIED live: shift-click's exact quantity
    # follows the account's own bank quantity-mode setting.
    single_type_trip = len(needed_types) == 1
    for paste_type, name in chosen_names.items():
        # Re-resolve fresh right before each click -- an earlier withdrawal can reflow the tab's
        # grid (a herb hitting 0 stock drops out).
        current = controller.get_bank(fresh=True).get("items", [])
        slot = next(
            (i for i in current if name.lower() in (i.get("name") or "").lower() and i.get("qty", 0) > 0),
            None,
        )
        if slot is None:
            debug_log.log("mixology", f"{name} no longer available when it came time to withdraw it -- skipping this cycle, will re-evaluate next tick")
            continue

        name_lower = name.lower()

        def herb_landed():
            return any(name_lower in (i.get("name") or "").lower() for i in controller.get_inventory())

        if single_type_trip:
            mark = _mark(ctx)
            controller.key_down(ctx.ser, "SHIFT")
            controller.click_entity(ctx.ser, slot, check_ui_blockers=False)
            controller.key_up(ctx.ser, "SHIFT")
            # CONFIRMED live 2026-08-13 ("it forgot to clean the herbs"): the withdrawal landed
            # but _clean_grimy_herbs read a stale pre-withdrawal inventory. Confirm the herb is
            # actually held before moving on. Diagnostic, not a gate: downstream self-corrects on
            # the next bank cycle if it genuinely never landed.
            if not _await(ctx, "INVENTORY_CHANGED", BANK_WITHDRAW_LAND_S, confirm=herb_landed,
                          since=mark, reaction=False):
                debug_log.log("mixology", f"shift-click withdrawal of '{name}' didn't confirm landing within {BANK_WITHDRAW_LAND_S}s")
            continue

        if not controller.click_menu_action(ctx.ser, slot, "Withdraw-X", check_ui_blockers=False):
            debug_log.log("mixology", f"Withdraw-X menu entry not found for {name} -- skipping this cycle")
            continue
        # CONFIRMED live 2026-08-12: the "Enter amount" chatbox needs a moment to render and take
        # keyboard focus -- typing immediately dropped the digits silently.
        controller.human_delay(0.4, 0.6)
        mark = _mark(ctx)
        controller.type_digits_and_enter(ctx.ser, str(per_type_qty))
        _await(ctx, "INVENTORY_CHANGED", 1.5, confirm=herb_landed, since=mark, reaction=False)

    banker.close_bank(ctx)

    _refine_and_hopper_inventory(ctx)


def _withdraw_banked_paste(ctx, needed_types, hopper, target):
    """Withdraw already-refined paste for each needed type the bank can cover.

    Returns the types still needing the herb path. Paste is stackable, so all three fit in three
    inventory slots however much is taken -- the quantity asked for is the hopper's actual
    shortfall rather than a slot count.

    Reads the herb tab (tab 8) the caller has already switched to -- the paste is stored there
    alongside the herbs, and `/bank` only reports what the interface is DRAWING. The slot is
    matched on EXACT name -- 'Mox paste' must never select the 'Swamp paste' this account also
    banks 547 of.

    NO BANK SEARCH, deliberately (2026-09-22). This used to banker.search_bank each paste name in
    turn, and LIVE the second search broke the trip: clicking the search icon while the previous
    search is still up TOGGLES THE SEARCH OFF (clear_bank_search relies on exactly that), so 'Aga
    paste' was typed into nothing, and the following tab switch found no tab buttons and
    safety-stopped with 'could not switch to the herb bank tab'.

    The withdrawal is Withdraw-X with a typed amount, NOT banker.search_and_withdraw: that helper
    sets quantity mode to "one" and clicks once per item, so asking it for a shortfall of 800
    would fire 800 physical clicks. Same Withdraw-X shape the herb path below already uses.

    A type the bank has none of is simply left in the returned list."""
    remaining = []
    withdrew = {}
    for paste_type in needed_types:
        name = PASTE_ITEM_NAMES[paste_type]
        item_id = PASTE_ITEM_IDS[paste_type]
        shortfall = max(0, target - (hopper.get(paste_type, 0) or 0))
        if shortfall <= 0:
            continue

        def held(_id=item_id):
            return sum((i.get("qty") or 1) for i in controller.get_inventory()
                       if i.get("id") == _id)

        slot = next(
            (s for s in controller.get_bank(fresh=True).get("items", [])
             if (s.get("name") or "").lower() == name.lower() and s.get("qty", 0) > 0),
            None,
        )
        if slot is None:
            debug_log.log("mixology", f"no '{name}' in the bank -- falling back to herbs for {paste_type}")
            remaining.append(paste_type)
            continue

        before = held()
        if not controller.click_menu_action(ctx.ser, slot, "Withdraw-X", check_ui_blockers=False):
            debug_log.log("mixology", f"Withdraw-X menu entry not found for '{name}'")
            remaining.append(paste_type)
            continue
        # The "Enter amount" chatbox needs a moment to render and take keyboard focus -- typing
        # immediately drops the digits silently (CONFIRMED live 2026-08-12 on the herb path).
        controller.human_delay(0.4, 0.6)
        mark = _mark(ctx)
        controller.type_digits_and_enter(ctx.ser, str(shortfall))
        if _await(ctx, "INVENTORY_CHANGED", BANK_WITHDRAW_LAND_S,
                  confirm=lambda b=before: held() > b, since=mark, reaction=False):
            withdrew[paste_type] = held() - before
        else:
            debug_log.log("mixology", f"withdrawal of '{name}' didn't confirm landing")
            remaining.append(paste_type)

    if withdrew:
        debug_log.log("mixology", f"withdrew banked paste {withdrew} -- straight to the hopper, no refining")
    return remaining


def _clean_grimy_herbs(ctx) -> None:
    """Clean every 'Grimy X' herb currently held into its usable form -- CONFIRMED live
    2026-08-12 (user report, and the wiki: "Grimy herbs ... cannot be used in mixology until
    cleaned"): the Refiner only accepts clean herbs. Cleaning is one click per herb (herbs
    aren't stackable).

    CORRECTED 2026-08-13 (user directive): sweep each occupied 4-wide inventory row
    leftmost-to-rightmost with a click burst DURING the glide (controller.sweep_and_click, "around
    5-7 clicks per second") rather than re-aiming at each herb -- the grid's tight slot spacing
    means the burst lands on SOME herb regardless of precise aim."""
    # THE INVENTORY TAB MUST BE THE VISIBLE PANEL BEFORE ANY OF THIS. The sweep is a blind click
    # burst aimed at inventory SLOT coordinates, and those coordinates stay the same whichever
    # side panel is actually on screen -- so with another tab open the burst lands on that
    # panel's contents instead. LIVE 2026-09-17: it landed in Account Management and hit one of
    # its external links, which opened a browser and took focus off the client, stranding the run
    # until F1. A burst that cannot see what it is clicking must at least be pointed at the right
    # panel.
    if not controller.open_inventory_tab(ctx.ser):
        debug_log.log("mixology", "could not confirm the inventory tab is open -- skipping the herb sweep")
        return

    deadline = _now() + 30.0
    while _now() < deadline:
        grimy = [
            i for i in controller.get_inventory()
            if (i.get("name") or "").lower().startswith("grimy ")
        ]
        if not grimy:
            return

        rows: dict = {}
        for item in grimy:
            rows.setdefault(item.get("slot", 0) // 4, []).append(item)

        for row in sorted(rows):
            row_items = sorted(rows[row], key=lambda i: i.get("slot", 0))
            xs = [i.get("screenX") for i in row_items if i.get("screenX") is not None]
            ys = [i.get("screenY") for i in row_items if i.get("screenY") is not None]
            if not xs or not ys:
                continue
            y = sum(ys) // len(ys)
            start_x, end_x = min(xs), max(xs)

            controller.move_to_human(ctx.ser, start_x, y)
            # min_clicks=len(row_items) -- CORRECTED 2026-08-13 ("we arent clicking fast
            # enough"): a 4-slot row spans ~260px, which floors the glide at ~250-300ms -- barely
            # 2 clicks. Floor the glide at one click per herb in the row instead.
            controller.sweep_and_click(ctx.ser, end_x, y, click_rate_hz=(5.0, 7.0), min_clicks=len(row_items))

        mark = _mark(ctx)
        _await(ctx, "INVENTORY_CHANGED", 0.6, confirm=None, since=mark, reaction=False)
    debug_log.log("mixology", "grimy herb cleaning didn't finish within the time budget")


ALL_HERB_NAMES = {name for names in DEFAULT_CONFIG["herbPriority"].values() for name in names}


def _held_herbs():
    # Substring match: the grimy form ("Grimy toadflax") is a Toadflax-family herb too --
    # CONFIRMED live 2026-08-12 that exact-set membership silently no-opped this whole function.
    return [
        i for i in controller.get_inventory()
        if any(name.lower() in (i.get("name") or "").lower() for name in ALL_HERB_NAMES)
    ]


def _held_paste():
    return [i for i in controller.get_inventory() if (i.get("name") or "").endswith(" paste")]


def _refine_and_hopper_inventory(ctx) -> None:
    """Clean any grimy herbs held, then feed every clean herb / unfinished potion held into the
    refiner -- clicking it ONCE PER GAME TICK, which doubles refine speed per the wiki -- then
    deposit the resulting paste into the hopper.

    The click cadence is the tick, not a wall-clock spam: the 2026-08-13 version fired
    controller.click_human every 0.06-0.25s (4-16 clicks/s), which the refiner cannot use (it
    processes one herb per tick at most) and which no human produces. Now: one click, then
    `_tick_pause` to just after the next boundary. Coming straight from the bank the player
    usually is not at the Refiner yet, so the first click both walks there and starts refining;
    the first herb leaving the inventory (INVENTORY_CHANGED) is proof of arrival, and only THEN is
    the mouse rested on the refiner for the click-in-place loop (hovering mid-walk would aim at a
    projection the walk is about to move)."""
    _clean_grimy_herbs(ctx)

    held = _held_herbs()
    if held:
        n0 = len(held)
        mark = _mark(ctx)
        refiner = _click_object(ctx, "Refiner", check_ui_blockers=False)
        if refiner is None:
            ctx.state.trigger_safety_stop("no refiner found near the player")
            return
        _await(ctx, "INVENTORY_CHANGED", REFINE_START_S + _walk_budget(refiner),
               confirm=lambda: len(_held_herbs()) < n0, since=mark, reaction=False)

        if _hover_when_settled(ctx, "Refiner", check_ui_blockers=False) is None:
            return

        deadline = _now() + REFINE_MAX_S
        while _now() < deadline:
            if not _held_herbs():
                break
            controller.click_human(ctx.ser)
            _tick_pause(ctx, 1, phase=REFINE_CLICK_PHASE_S)

    if not _held_paste():
        return  # nothing left to deposit -- refining already consumed everything held

    # Confirm the paste actually landed (inventory emptied of paste) rather than assuming one
    # click succeeded; retry a bounded number of times against a freshly-resolved Hopper.
    for _ in range(HOPPER_DEPOSIT_TRIES):
        mark = _mark(ctx)
        hopper_obj = _click_object(ctx, "Hopper", check_ui_blockers=False)
        if hopper_obj is None:
            ctx.state.trigger_safety_stop("no hopper found near the player")
            return
        if _await(ctx, "INVENTORY_CHANGED", HOPPER_DEPOSIT_S + _walk_budget(hopper_obj),
                  confirm=lambda: not _held_paste(), since=mark):
            return
    debug_log.log("mixology", "hopper deposit click may not have landed -- paste still held after retries")


# --- orders ----------------------------------------------------------------------------------

def _choose_orders(orders: list) -> list:
    """Make and deposit all 3 current orders together, Retort order(s) LAST.

    All three: CHANGED 2026-08-12 (explicit user direction: "we need to do all 3 orders at once")
    -- the wiki confirms the conveyor belt deposits up to three potions in one click and pays 20%
    more resin per potion for 2 at once, 40% for 3; the original max-points-per-paste strategy
    never captured that. Not sorted by Lye content: digweed is HELD until a Liplack liquor base
    is in hand (USER 2026-08-13: "we just save it until there is a LLL in our order"), see
    _apply_digweed_if_held.

    Retort last (2026-09-06): a station click processes the LEFTMOST unprocessed potion, and an
    extra Retort click landing after its potion finished starts the NEXT unprocessed potion at
    the Retort -- CONFIRMED live 2026-08-13 ("we made the MMA with the retort instead of the
    Agi[tator]"). Bases are taken in this list's order and processed positionally in the same
    order (mix_phase's `chosen[already_made]`, process_phase's `chosen[len - unprocessed]`), so
    with every Retort order at the END nothing unprocessed is left in the inventory when the
    Retort finishes -- a stray per-tick click cannot start anything. Two Retort orders: the
    second is what the Retort should get anyway. Alembic before Agitator is the shorter walk
    from the vessel (STATION_RANK). Stable, so equal ranks keep the game's own order."""
    return sorted(orders, key=lambda o: STATION_RANK.get(o.get("station") or "", 1))


def orders_phase(ctx, config: dict) -> None:
    # CONFIRMED live 2026-08-13: a stale, unprocessed potion left over from a prior interrupted
    # session shifts what "leftmost unprocessed" means for the WHOLE cycle, routing genuine
    # potions to the wrong stations with no error. Flush any leftover potions (deposit_phase
    # actively processes a stray unfinished one) BEFORE choosing new orders.
    inventory = controller.get_inventory()
    leftover_unfinished = [i for i in inventory if i.get("id") in UNFINISHED_IDS]
    leftover_finished = [i for i in inventory if i.get("id") in FINISHED_IDS]

    # Only an UNFINISHED leftover actually breaks anything -- it is what shifts "leftmost
    # unprocessed". A FINISHED one is inert: it occupies a slot and nothing else.
    #
    # Sending a finished potion here to deposit_phase unconditionally is a LIVELOCK now that
    # deposit_phase declines to bin a potion no live order wants: orders_phase handed it over,
    # deposit_phase handed it straight back, at tick rate forever (LIVE 2026-09-17, on a
    # 'Liplack liquor' nothing wanted). So hand over finished potions only when depositing will
    # actually do something -- i.e. when some live order wants one of them.
    if leftover_unfinished:
        debug_log.log(
            "mixology",
            f"leftover potions in inventory before choosing new orders: "
            f"{[i.get('name') for i in leftover_unfinished]} -- flushing via deposit first",
        )
        ctx.state.data["mm_phase"] = "deposit"
        return

    if leftover_finished:
        orders_now = {(o.get("name") or "")
                      for o in (controller.get_mixology_state().get("orders") or [])}
        if any((i.get("name") or "") in orders_now for i in leftover_finished):
            debug_log.log(
                "mixology",
                f"finished potions a live order wants: "
                f"{[i.get('name') for i in leftover_finished]} -- depositing first",
            )
            ctx.state.data["mm_phase"] = "deposit"
            return
        # Nothing wants them yet; carry them through this cycle and deposit when an order turns up.

    # A potion still INSIDE a station is in no container the leftover check above can see.
    # USER 2026-09-17, watching it live: "it has one in the machine again when going for 3 new".
    # Starting a fresh set of three while a station still holds one strands it: the new cycle's
    # positional bookkeeping never refers to it again, so nothing ever collects it and the next
    # cycle livelocks on "<station> still busy". Waiting costs a few seconds; the generic stall
    # detector still stops the run if the station is genuinely wedged rather than just slow.
    still_cooking = _busy_station_names()
    if still_cooking:
        # Clicking it out is what actually clears this (see _collect_if_stalled); without that
        # this gate would just wait for a station that never empties itself.
        name = still_cooking[0]
        _collect_if_stalled(ctx, name.capitalize(), _station_state(name))
        return

    # THE MIXING VESSEL IS THE THIRD CONTAINER, and it is no more visible to the inventory check
    # than a station is. A mix left in it fills all three mixer slots, so the next lever pull can
    # change nothing -- which reads as "'<X> lever' click registered but no mixer slot changed"
    # and safety-stops after three tries, with the click itself perfectly on target
    # (/last-action confirmed "Operate / Lye lever"). LIVE 2026-09-17, on a vessel left loaded by
    # a previous run. Take it first; deposit_phase knows how to flush whatever that hands us.
    leftover_mix = _vessel_content()
    if leftover_mix:
        debug_log.log("mixology",
                      f"the mixing vessel still holds '{leftover_mix}' -- taking it before new orders")
        if _take_vessel_contents(ctx, leftover_mix):
            ctx.state.data["mm_phase"] = "deposit"
        return

    data = controller.get_mixology_state()
    orders = data.get("orders", [])
    hopper = data.get("hopper", {})

    if len(orders) < 3:
        # Bridge read came back short (not in the lab, or the mid-delivery instant where the
        # game zeroes every order varbit before setting the new ones) -- don't act on a partial
        # order set.
        return

    chosen = _choose_orders(orders)

    needed = {"mox": 0, "aga": 0, "lye": 0}
    for order in chosen:
        for paste in ("mox", "aga", "lye"):
            needed[paste] += order.get(paste, 0) * 10  # each lever pull uses 10 paste

    low_water = config.get("hopperLowWater", 150)
    if any(hopper.get(p, 0) - needed[p] < low_water for p in ("mox", "aga", "lye")):
        debug_log.log("mixology", f"hopper would drop below low-water fulfilling {[o.get('name') for o in chosen]} -- rebanking")
        ctx.state.data["mm_phase"] = "bank_herbs"
        return

    ctx.state.data["mm_chosen_orders"] = chosen
    debug_log.log("mixology", f"chosen orders this cycle: {[o.get('name') for o in chosen]}")
    ctx.state.data["mm_phase"] = "mix"


# --- mixing ----------------------------------------------------------------------------------

# Mixing vessel object ids by content, confirmed via direct ObjectID/ObjectID1 jar lookup --
# the FALLBACK content check when the vessel varbit cannot be read. This is the wiki-infobox
# object-id order, NOT the varbit type-index order RECIPE_TABLE uses -- they genuinely differ
# (Aqualux amalgam/Azure aura mix and Anti-leech lotion/Megalite liquid are swapped).
VESSEL_ID_TO_BASE = {
    54919: "Mammoth-might mix", 54920: "Mystic mana amalgam", 54921: "Marley's moonlight",
    54922: "Alco-augmentator", 54923: "Aqualux amalgam", 54924: "Azure aura mix",
    54925: "Liplack liquor", 54926: "Anti-leech lotion", 54927: "Megalite liquid",
    54928: "Mixalot",
}

LEVER_NAMES = {"mox": "Mox lever", "aga": "Aga lever", "lye": "Lye lever"}


def vessel_potion_name(varbit_value):
    """MM_LAB_VESSEL_READY (11339) -> potion name: the value is the 1-indexed position in the
    game's potion-type order (POTION_NAMES_BY_INDEX == MixologyProvider.POTION_NAMES), 0 for an
    empty vessel. None for 0, None, or anything out of range. LIVE-VERIFY once against
    impostorId (worklist item 2)."""
    if not varbit_value:
        return None
    try:
        idx = int(varbit_value) - 1
    except (TypeError, ValueError):
        return None
    if 0 <= idx < len(POTION_NAMES_BY_INDEX):
        return POTION_NAMES_BY_INDEX[idx]
    return None


def _mixer_slots():
    """The three mixer-slot varbits as a tuple, or None when none of them can be read."""
    slots = tuple(controller.get_varbit(v) for v in VB_MIXER_SLOTS)
    if all(s is None for s in slots):
        return None
    return slots


def _pull_lever(ctx, paste: str, next_name: str = None) -> bool:
    """Click the lever for `paste` and confirm TWO things, each on its own signal: the game's
    verdict that the click hit THAT lever (MENU_CLICKED target, /last-action as the HTTP truth
    -- three levers stand side by side and a clean click can land on a neighbour, user directive
    2026-08-13), then that the pull actually LANDED (a mixer-slot varbit changed; the pull is
    performed on arrival, so the budget includes the walk). Retries the pull up to
    LEVER_PULL_MAX_ATTEMPTS; the vessel-content check in _make_base is the backstop either way.

    `next_name`: the object the caller will click next (the next lever, or the vessel). Once the
    verdict is in, the cursor is rested on it WHILE the pull lands -- the human aims the next
    lever during the previous pull, so the next click is a click in place, not a flight."""
    lever_name = LEVER_NAMES[paste]
    want = lever_name.lower()
    for attempt in range(LEVER_PULL_MAX_ATTEMPTS):
        baseline_tick = controller.get_last_action().get("tick")
        slots_before = _mixer_slots()
        mark = _mark(ctx)
        if attempt > 0:
            # The last click MISSED from where the cursor is resting, so a click "in place" would
            # only repeat it (LIVE 2026-09-22: three identical misses and a safety stop). Re-aim
            # at the lever's live centre first; the click below then goes from there.
            _hover_object(ctx, lever_name, check_ui_blockers=False, offset=(0, 0))
        lever = _click_object(ctx, lever_name, check_ui_blockers=False, offset=_aim_offset(lever_name))
        if lever is None:
            debug_log.log("mixology", f"{lever_name} not found near the player (or the click was refused)")
            return False

        def verdict_ok():
            action = controller.get_last_action()
            tick = action.get("tick")
            return (
                tick is not None
                and (baseline_tick is None or tick > baseline_tick)
                and want in (action.get("target") or "").lower()
            )

        # shorten: ANY MENU_CLICKED is the game's verdict -- one for the wrong target means the
        # click missed, so the retry starts MENU_VERDICT_SHORTEN_S later instead of a full second.
        verdict = _await(ctx, {"MENU_CLICKED": lambda p: want in (p.get("target") or "").lower()},
                         MENU_VERDICT_S, confirm=verdict_ok, since=mark, reaction=False,
                         shorten={"MENU_CLICKED": MENU_VERDICT_SHORTEN_S})
        def landed():
            now = _mixer_slots()
            return now is not None and now != slots_before

        if verdict and next_name:
            _hover_when_settled(ctx, next_name, check_ui_blockers=False,
                                stop=landed if slots_before is not None else None)
        if not verdict:
            debug_log.log(
                "mixology",
                f"lever pull for '{lever_name}' didn't confirm (last-action target="
                f"'{controller.get_last_action().get('target')}') -- retry {attempt + 1}/{LEVER_PULL_MAX_ATTEMPTS}",
            )
            continue

        if slots_before is None:
            # Mixer varbits unreadable: trust the verdict and give the pull its tick.
            _tick_pause(ctx, 1)
            return True

        if _await(ctx, {"VARBIT_CHANGED": _varbit_in(VB_MIXER_SLOTS)},
                  PULL_LAND_S + _walk_budget(lever), confirm=landed, since=mark, reaction=False):
            return True

        # NO CHANGE IS NOT THE SAME AS NO PULL. The slot varbits hold WHICH paste is in each
        # slot, and the mixer overwrites its oldest slot -- so pulling lye into a mixer whose
        # slots already read lye/lye/lye moves no value at all. LIVE 2026-09-17: a run started
        # with (3,3,3) left over from a previous Liplack liquor, the next order was Liplack
        # liquor again, and three flawless pulls each read as a failure and safety-stopped the
        # run with /last-action showing "Operate / Lye lever" the whole time.
        #
        # The verdict above has ALREADY proven the game operated the right lever (a MENU_CLICKED
        # naming it, on a newer tick), which is the evidence that the paste went in; the slot
        # diff was only ever a second opinion. Correctness still has a real gate: _make_base
        # checks the finished vessel against the recipe and re-pulls the whole thing if it
        # disagrees, at no paste cost.
        debug_log.log(
            "mixology",
            f"'{lever_name}' pulled but no mixer slot value moved -- taking the menu verdict "
            f"(slots {slots_before} already held it); the vessel check is the backstop",
        )
        return True
    return False


def _vessel_content():
    """What the mixing vessel holds right now, by name (None when empty or unreadable). The
    vessel varbit is the primary read; the object's impostorId (CONFIRMED live 2026-08-12: the
    plain `id` is always the multiloc base 55395) is the fallback when /varbit cannot answer."""
    value = controller.get_varbit(VB_VESSEL_READY)
    if value is not None:
        return vessel_potion_name(value)
    vessel = find_object("Mixing vessel")
    if vessel is None:
        return None
    return VESSEL_ID_TO_BASE.get(vessel.get("impostorId"))


def _count_item(item_id) -> int:
    return sum(1 for i in controller.get_inventory() if i.get("id") == item_id)


def _make_base(ctx, order: dict, next_name: str = None) -> bool:
    """Pull the order's levers, check the vessel, take the base. True when the unfinished base is
    in the inventory; False to retry the same order next tick (or after a safety stop).
    `next_name` is what the caller clicks after the take (the next base's first lever, or the
    first station) -- the cursor is rested on it while the take lands."""
    pulls = []
    for paste in ("mox", "aga", "lye"):
        pulls.extend([paste] * order.get(paste, 0))

    for i, paste in enumerate(pulls):
        upcoming = LEVER_NAMES[pulls[i + 1]] if i + 1 < len(pulls) else "Mixing vessel"
        if not _pull_lever(ctx, paste, next_name=upcoming):
            ctx.state.trigger_safety_stop(
                f"{LEVER_NAMES[paste]} not found near the player, or repeated "
                f"unconfirmed/wrong-lever pulls"
            )
            return False

    target_name = order.get("name")
    actual = _vessel_content()
    if actual != target_name:
        # A misclick on one of the pulls is the likely cause; the mixer overrides its oldest slot
        # on every pull, so re-pulling the whole recipe next tick corrects it at no paste cost.
        debug_log.log("mixology", f"vessel shows '{actual}', expected '{target_name}' -- retrying")
        return False

    target_id = next((k for k, v in UNFINISHED_IDS.items() if v == target_name), None)
    if target_id is None:
        ctx.state.trigger_safety_stop(f"unknown potion '{target_name}' in the chosen orders")
        return False

    before = _count_item(target_id)
    mark = _mark(ctx)
    vessel = _click_object(ctx, "Mixing vessel", check_ui_blockers=False)
    if vessel is None:
        ctx.state.trigger_safety_stop("no mixing vessel found near the player")
        return False
    if next_name:
        _hover_when_settled(ctx, next_name, check_ui_blockers=False,
                            stop=lambda: _count_item(target_id) > before)

    # The game can refuse the take (insufficient paste or Herblore level) and click_entity's own
    # simulated misclick can miss -- the base is made only when its id is in the inventory.
    # CONFIRMED live 2026-08-13 the take prints "You collect some X from the mixing vessel.";
    # that chat line and INVENTORY_CHANGED are the signals, the inventory read is the truth.
    took = _await(
        ctx,
        {"INVENTORY_CHANGED": None,
         "CHAT_MESSAGE": _chat_has("you collect some", (target_name or "").lower())},
        VESSEL_TAKE_S + _walk_budget(vessel),
        confirm=lambda: _count_item(target_id) > before,
        since=mark,
    )
    if not took:
        debug_log.log("mixology", f"vessel take for '{target_name}' didn't land in inventory -- retrying")
        return False
    return True


def _take_vessel_contents(ctx, potion_name: str) -> bool:
    """Empty a vessel that is already holding a mix, so the mixer slots free up.

    A loaded vessel fills all three mixer slots, and a full mixer silently swallows lever pulls --
    the pull lands ("Operate / Lye lever" in /last-action) but no slot varbit moves, which reads
    as three failed pulls and a safety stop. Same take as _make_base's, without the recipe
    bookkeeping: whatever comes out is a stray potion and deposit_phase already knows how to
    flush one."""
    target_id = next((k for k, v in UNFINISHED_IDS.items() if v == potion_name), None)
    if target_id is None:
        return False
    before = _count_item(target_id)
    mark = _mark(ctx)
    if _click_object(ctx, "Mixing vessel", check_ui_blockers=False) is None:
        return False
    return bool(_await(
        ctx,
        {"INVENTORY_CHANGED": None,
         "CHAT_MESSAGE": _chat_has("you collect some", (potion_name or "").lower())},
        VESSEL_TAKE_S,
        confirm=lambda: _count_item(target_id) > before,
        since=mark,
    ))


def mix_phase(ctx, config: dict) -> None:
    """Make every chosen base, chained inside ONE tick() call (a human pulls the next lever the
    tick the previous pull lands and takes the base the moment it is ready; the engine loop's
    ~1s between ticks would cost that on every one of nine pulls). Bounded by CHAIN_MAX_S and the
    engine's pause/safety flags, re-checked between bases.

    How many bases are already made is re-derived from LIVE inventory every time (CONFIRMED live
    2026-08-13 after two Liplack liquors ended up in the inventory): ctx.state.data can desync
    from what is held after a restart or a misclick, and trusting a stale count re-makes or skips
    the wrong order."""
    chosen = ctx.state.data.get("mm_chosen_orders", [])
    deadline = _now() + CHAIN_MAX_S
    while True:
        if ctx.state.safety_stopped or ctx.state.paused:
            return
        already_made = sum(
            1 for i in controller.get_inventory()
            if i.get("id") in UNFINISHED_IDS or i.get("id") in FINISHED_IDS
        )
        if already_made >= len(chosen):
            ctx.state.data["mm_phase"] = "process"
            return
        if _now() >= deadline:
            return  # hand the tick back; re-enters here next tick
        order = chosen[already_made]
        if already_made + 1 < len(chosen):
            following = chosen[already_made + 1]
            first_paste = next((p for p in ("mox", "aga", "lye") if following.get(p, 0)), None)
            next_name = LEVER_NAMES[first_paste] if first_paste else None
        else:
            next_name = chosen[0].get("station")   # the first station to process
        if not _make_base(ctx, order, next_name=next_name):
            return


# --- processing ------------------------------------------------------------------------------

# Confirmed graphics-object ids for each station's speedup cue (SpotanimID
# VFX_MACHINERY_ALCHEMY01_AGITATOR01 / _ALEMBIC01) -- pushed as GRAPHICS_OBJECT_CREATED since
# 2026-09-06, readable via /graphics-objects before that.
AGITATOR_CUE_GRAPHICS_ID = 2954
ALEMBIC_CUE_GRAPHICS_ID = 2955


def _station_state(key: str) -> dict:
    return controller.get_mixology_state().get("stations", {}).get(key, {}) or {}


def _busy_station_names() -> list:
    """Every station currently holding a potion, from ONE /mixology-state read (asking
    _station_state three times would cost three)."""
    stations = controller.get_mixology_state().get("stations", {}) or {}
    return [k for k in ("retort", "agitator", "alembic")
            if (stations.get(k) or {}).get("potionName")]


def process_phase(ctx, config: dict) -> None:
    chosen = ctx.state.data.get("mm_chosen_orders", [])

    # CONFIRMED live 2026-08-13: clicking a station processes the LEFTMOST unprocessed potion --
    # no potion-selection click first (a select click risked using an adjacent, already-FINISHED
    # item: "it just used a finished product on the table"). Because selection is positional,
    # orders are processed in the exact order their bases were taken (chosen[0] first ...), and
    # the order up for processing is always chosen[len(chosen) - unprocessed_count], computed
    # fresh from the live inventory each loop.
    while True:
        # This loop walks every unprocessed potion inside one tick() call, so the engine's own
        # F2/safety-stop gates (which run BETWEEN ticks) cannot interrupt it -- re-check them
        # here (LIVE BUG 2026-08-27, same fix as plank_make.py's cast_phase). safety_stopped can
        # be set BY _process_one itself.
        if ctx.state.safety_stopped or ctx.state.paused:
            return

        unprocessed_count = sum(
            1 for i in controller.get_inventory() if i.get("id") in UNFINISHED_IDS
        )
        if unprocessed_count == 0:
            # A potion INSIDE a station is in no container the inventory can see, so counting only
            # the inventory calls the cycle done while a station is still working and deposits
            # without it -- the potion is then stranded there for good, and the next cycle
            # livelocks on "<station> still busy" (LIVE 2026-09-17, twice). The finish waits in
            # _process_one should have covered this; this is the backstop for when one of them
            # returns early, which is exactly what a wrong finish check did.
            still_cooking = _busy_station_names()
            if still_cooking:
                name = still_cooking[0]
                _collect_if_stalled(ctx, name.capitalize(), _station_state(name))
                return
            ctx.state.data["mm_phase"] = "deposit"
            return

        order_index = len(chosen) - unprocessed_count
        if not (0 <= order_index < len(chosen)):
            # More unprocessed potions than orders we know about -- a restart wiped
            # mm_chosen_orders while the potions stayed in the inventory. Nothing here can
            # route them, so hand them to orders_phase's leftover flush (-> deposit) instead
            # of returning here every tick forever (2026-09-06).
            debug_log.log(
                "mixology",
                f"unexpected: {unprocessed_count} unprocessed potions but "
                f"{len(chosen)} chosen orders -- flushing via orders/deposit",
            )
            ctx.state.data["mm_phase"] = "orders"
            return

        order = chosen[order_index]
        station_name = order.get("station")

        # /mixology-state's stations.{name}.potionName is ground truth for whether the station
        # is free -- never click a busy one (CONFIRMED live 2026-08-12: "tried to start a new
        # potion while we still working on one").
        station_state = _station_state(station_name.lower())
        if station_state.get("potionName"):
            _note_station_blocked(ctx, station_name, station_state, order.get("name"))
            if not ctx.state.safety_stopped:
                _collect_if_stalled(ctx, station_name, station_state)
            return
        ctx.state.data.pop("mm_blocked_key", None)

        # A station takes 10-60s, so a digweed that matured during the last one is checked
        # before each station rather than only at the top of the (now cycle-long) tick.
        maybe_collect_digweed(ctx, config)
        if ctx.state.safety_stopped or ctx.state.paused:
            return

        station = find_object(station_name)
        if station is None:
            return

        following = chosen[order_index + 1] if order_index + 1 < len(chosen) else None
        next_name = following.get("station") if following else "Conveyor belt"
        _process_one(ctx, config, station_name, station, order.get("name"), next_name=next_name)


def _collect_if_stalled(ctx, station_name: str, state: dict) -> None:
    """Click a station that has stopped advancing, to TAKE ITS POTION OUT.

    USER 2026-09-17, watching it live: "it has to click the machine to get it out". A station
    does not hand its potion back on its own, so the old behaviour -- log "<station> still busy"
    and return, every tick -- waited for something that was never going to happen. Every stall
    this routine hit today was really a finished potion nobody collected, and the only reason any
    of them ever cleared was the leftover flush happening to click the station.

    Rate-limited to a stalled station (progress unchanged for COLLECT_STALL_S) so a genuinely
    working one is never click-spammed, and the click is safe by construction: the
    auto-start-the-wrong-potion hazard only exists for an EMPTY station and this one is occupied.
    _note_station_blocked still escalates to a safety stop if collecting does not clear it."""
    data = ctx.state.data
    key = (station_name, state.get("potionName"), state.get("progress"))
    if data.get("mm_collect_key") != key:
        data["mm_collect_key"] = key
        data["mm_collect_since"] = _now()
        data["mm_collect_tries"] = 0
        debug_log.log("mixology", f"{station_name} still busy -- retrying")
        return
    if _now() - data.get("mm_collect_since", _now()) < COLLECT_STALL_S:
        return
    if data.get("mm_collect_tries", 0) >= COLLECT_MAX_TRIES:
        return          # leave it to _note_station_blocked's stop rather than clicking forever
    data["mm_collect_tries"] = data.get("mm_collect_tries", 0) + 1
    data["mm_collect_since"] = _now()
    debug_log.log("mixology",
                  f"{station_name} has sat on '{state.get('potionName')}' at progress "
                  f"{state.get('progress')} for {COLLECT_STALL_S:.0f}s -- clicking it to take the "
                  f"potion out (try {data['mm_collect_tries']}/{COLLECT_MAX_TRIES})")
    _click_object(ctx, station_name, check_ui_blockers=False)


def _note_station_blocked(ctx, station_name: str, state: dict, want: str) -> None:
    """Safety-stop when the station this order needs is FROZEN, whatever potion it holds.

    Two ways this happens, both seen live 2026-09-17: a stray click auto-started a potion at the
    wrong station (a foreign name), or the cycle deposited while a station was still working and
    stranded one of its own potions there (a matching name). Nothing here can clear either: the
    caller returns every tick, so the run spins at tick rate until the generic STALL_STOP_S fires
    five minutes later with a message that does not name the cause. Stopping sooner, naming the
    potion and the station, is what the recovery -- collect it by hand -- actually needs.

    `progress` is part of the key, so a station genuinely working climbs it every tick and keeps
    resetting the timer; only a frozen one accumulates. That is what makes a 60s threshold safe
    against a legitimately slow potion (STATION_FINISH_S is 90s)."""
    data = ctx.state.data
    busy_with = state.get("potionName")
    # A station frozen on OUR OWN potion is stuck just as badly as one holding a stranger's, and
    # exempting it is the hole the 2026-09-17 Alembic fell through: a stranded 'Megalite liquid'
    # matched the order name, so the timer never started and the run spun on "Alembic still busy"
    # with nothing to stop it. Progress is in the key either way, so a station that is genuinely
    # working keeps resetting the timer and never trips this.
    key = (station_name, busy_with, state.get("progress"))
    if data.get("mm_blocked_key") != key:
        data["mm_blocked_key"] = key
        data["mm_blocked_since"] = _now()
        return
    if _now() - data.get("mm_blocked_since", _now()) >= FOREIGN_STATION_STOP_S:
        cause = ("a potion was stranded there by an early deposit" if busy_with == want
                 else "a stray click started a potion at the wrong station")
        ctx.state.trigger_safety_stop(
            f"mixology: the {station_name} is frozen holding '{busy_with}' while this order needs "
            f"it for '{want}' -- {cause}; collect it by hand before restarting"
        )


def _process_one(ctx, config: dict, station_name: str, station: dict, order_name: str,
                 next_name: str = None) -> None:
    """Click the station, confirm it STARTED on its potion varbit, run the station's speedup,
    confirm it FINISHED (potion varbit back to 0, finished id in the inventory). During the
    finish wait the cursor is rested on `next_name` (the next station, or the belt)."""
    potion_vb, _progress_vb = STATION_VARBITS[station_name]
    key = station_name.lower()

    # SNAPSHOT BEFORE THE CLICK. This has to be taken before the station has run at all: the
    # speedup loops below return only once the station has emptied, i.e. once the potion has
    # ALREADY been delivered, so a snapshot taken after them counts the very potion the finish is
    # waiting for. finished() could then never exceed it and the wait sat out its full 90s on a
    # potion that was visibly done -- USER 2026-09-17: "standing idle long after its finished".
    unfinished_id = next((k for k, v in UNFINISHED_IDS.items() if v == order_name), None)
    finished_id = unfinished_id + 10 if unfinished_id is not None else None
    held_before = _count_item(finished_id) if finished_id is not None else 0

    mark = _mark(ctx)
    clicked = _click_object(ctx, station_name, check_ui_blockers=False)
    if clicked is None:
        return

    def started():
        return _station_state(key).get("potionName") == order_name

    # The game SAYS it started, and says it the same way at every station -- RECORDED 2026-09-17
    # (mixology-animation): "You start concentrating/crystallising/homogenising the <potion>...",
    # 0.9s after the click, landing with the potion varbit. Matching "you start" plus the potion
    # name is station-agnostic, and the colour tag sits BEFORE the name ("the @mes_hl_pur@Liplack
    # liquor") so a substring match still hits. This is a positive confirmation that the machine
    # is actually running, which is what a frozen progress bar could only ever infer after the
    # fact (USER: "there should be an animation to confirm we are working on the machine").
    started_chat = _chat_has("you start", (order_name or "").lower())
    if not _await(ctx, {"VARBIT_CHANGED": _varbit_in((potion_vb,)), "CHAT_MESSAGE": started_chat},
                  STATION_START_S + _walk_budget(clicked), confirm=started, since=mark,
                  reaction=False):
        debug_log.log("mixology", f"{station_name} did not start on '{order_name}' -- retrying next tick")
        return

    speedups = config.get("speedups", DEFAULT_CONFIG["speedups"])
    if station_name == "Retort" and speedups.get("retort"):
        _process_retort_speedup(ctx, station, order_name)
    elif station_name == "Agitator" and speedups.get("agitator"):
        _process_agitator_speedup(ctx, station)
    elif station_name == "Alembic" and speedups.get("alembic"):
        _process_alembic_speedup(ctx, station)

    # An INCREASE over what was held before this station ran, not "> 0". Two orders in one cycle
    # can be the SAME potion (the game hands out duplicates freely -- seen repeatedly live
    # 2026-09-17), and the first one's finished potion is still in the inventory when the second
    # starts. "> 0" is then already true the instant the second is clicked, so the finish wait
    # returns immediately, process_phase sees an empty inventory of unfinished potions and
    # deposits -- abandoning a potion that is still inside the station. That is the stranded
    # 'Megalite liquid' the Alembic was left frozen on. Same snapshot-then-increase rule CLAUDE.md
    # already states for XP. held_before is taken at the top of this function, before the click --
    # see the comment there for why taking it here instead deadlocked the wait.
    def finished():
        return finished_id is not None and _count_item(finished_id) > held_before

    # The potion varbit clearing and the inventory changing land on the finishing tick; the
    # finished id in the inventory is what process_phase's own positional check depends on.
    # Anticipation: the cursor rests on the next target through the wait (a human aims the next
    # station while this one works); the wait itself is chunked with fidgets (_await_long).
    if next_name and not finished():
        # Settled, like the other station parks: this one is taken right after the station click,
        # while the player may still be walking in and the camera gliding, and a long finish wait
        # follows it -- so the settle costs nothing and an unsettled hover is simply wasted
        # (USER 2026-09-17: "a lot of hover before we settle so the hover is meaningless").
        _hover_when_settled(ctx, next_name, check_ui_blockers=False)
    def released_unfinished():
        """The station let the potion go WITHOUT finishing it: our unfinished base is back in the
        inventory and the station is empty. Waiting out the full STATION_FINISH_S for a potion
        that is not in the machine is pure idle -- USER 2026-09-17: "It's idling", with an
        unfinished 'Azure aura mix' sitting in the inventory and all three stations reading empty
        while this wait had 90s left to run. Retrying immediately is always better than sleeping
        through it; `finished` is checked first so a normal finish is never mistaken for this."""
        return (not finished()
                and unfinished_id is not None
                and _count_item(unfinished_id) > 0
                and not _station_state(key).get("potionName"))

    # A station that has stopped advancing is DONE and waiting to be emptied, not slow -- the
    # potion does not come back on its own (USER 2026-09-17: "it has to click the machine to get
    # it out"). Bailing out on the stall hands straight back to process_phase, whose
    # _collect_if_stalled clicks it out; sitting here instead burned the full 90s every time
    # ("if it's idling for more than 10sec something is wrong").
    watch = {"key": None, "since": None}

    def station_stalled() -> bool:
        st = _station_state(key)
        if st.get("potionName") != order_name:
            # Empty, or moved on. NOT a stall: an empty station's (None, 0) sits perfectly still,
            # so keying on it declared a FINISHED potion "not running" 2s later and re-clicked --
            # into an empty station, which is the auto-start-the-wrong-potion hazard, and the
            # wait meanwhile stood idle (USER 2026-09-17: "standing idle long after its
            # finished"). Whether the potion actually arrived is finished()'s question, and a
            # station that let it go unfinished is released_unfinished()'s.
            return False
        k = (st.get("potionName"), st.get("progress"))
        if watch["key"] != k:
            watch["key"] = k
            watch["since"] = _now()
            return False
        return _now() - watch["since"] >= FINISH_STALL_S

    def give_up() -> bool:
        return released_unfinished() or (not finished() and station_stalled())

    debug_log.log("mixology", f"{station_name} working on '{order_name}' -- awaiting the finish")
    # "You finish concentrating/crystallising/homogenising the <potion>." is the game announcing
    # the finish directly (RECORDED 2026-09-17), so the wait wakes on it instead of discovering
    # the finish a poll later. The inventory count still DECIDES -- chat is the accelerator.
    finished_chat = _chat_has("you finish", (order_name or "").lower())
    if not _await_long(ctx, {"VARBIT_CHANGED": _varbit_in((potion_vb,)), "INVENTORY_CHANGED": None,
                             "CHAT_MESSAGE": finished_chat},
                       STATION_FINISH_S, confirm=finished, since=mark, stop=give_up):
        if released_unfinished():
            debug_log.log("mixology", f"{station_name} released '{order_name}' unfinished -- retrying it")
        elif station_stalled():
            # Re-click HERE. Handing back to process_phase made _collect_if_stalled start its own
            # stall timer from scratch, so the same stall was waited out twice -- the single
            # biggest cost in the cycle (MEASURED: 8-11s per station). The stall is established by
            # this point, and a station that is not running is one whose start click never took,
            # so clicking it again is the fix rather than a workaround. Safe: it is occupied, so
            # the click cannot auto-start a different potion.
            # RE-VERIFY IMMEDIATELY BEFORE THE CLICK. station_stalled() read the state a moment
            # ago; if the potion completed in that gap the station is now EMPTY, and a click on
            # an empty station takes the LEFTMOST unprocessed potion -- i.e. the next order's,
            # into the wrong station. MEASURED live 2026-09-17: an Alembic recovery click landed
            # just after it emptied, swallowed the Azure aura mix meant for the Agitator, and the
            # Agitator then reported "did not start" (7 of those in one run).
            if _station_state(key).get("potionName") != order_name:
                return
            debug_log.log("mixology",
                          f"{station_name} is not running on '{order_name}' -- clicking it again to start it")
            restart_mark = _mark(ctx)
            _click_object(ctx, station_name, check_ui_blockers=False)
            _await(ctx, {"VARBIT_CHANGED": _varbit_in((potion_vb,)), "INVENTORY_CHANGED": None},
                   RESTART_CONFIRM_S, confirm=finished, since=restart_mark)
        else:
            debug_log.log("mixology", f"{station_name} finish of '{order_name}' did not confirm within {STATION_FINISH_S}s")


def _process_retort_speedup(ctx, station: dict, order_name: str) -> None:
    """Click the Retort once per game tick while it works (wiki: "click it constantly every tick
    to speed up the process", +2 Herblore per click; no timing window, no cue).

    THE TICK IS THE PROGRESS VARBIT: MM_RETORT_PROGRESS climbs by exactly 1 per tick (measured
    live 2026-08-13: 1, 2, ... 21 then 0 with the potion gone in the same instant), so each
    VARBIT_CHANGED on it is the boundary, and a click RETORT_CLICK_PHASE_S after it lands
    squarely inside the following tick. Clicks fire ONLY on a strictly-increasing value: a
    repeat or a reset withholds the click (the reset is the finish -- an extra click into an
    empty Retort auto-starts the next unprocessed potion there, the 2026-08-13 wrong-station
    bug; _choose_orders' Retort-last ordering is the structural defence, this the belt).

    UDP is lossy, so the loop also re-reads /mixology-state every RETORT_HTTP_CONFIRM_S and stops
    when the station reads empty; with no event stream it polls that read once per tick and
    clicks on the increase it sees there (the pre-2026-09-06 behaviour). The mouse is rested on
    the Retort once and every click is in place -- the player never moves here."""
    events = _events(ctx)
    order_key = "retort"

    # The mark is taken BEFORE the hover flight: the first progress tick can land during it,
    # and a mark taken afterwards would read past that event and lose the first click.
    mark = events.mark() if events is not None else None
    if _hover_when_settled(ctx, "Retort", check_ui_blockers=False) is None:
        return

    last_progress = -1
    # ONE CLICK PER TICK, on the clock -- NOT gated on having seen a progress event.
    #
    # The clicks are a SPEEDUP, not what drives the bar: USER 2026-09-17, "the retort which we
    # basically spam click can speed up the progress, it will however regardless if we input
    # anything move the progress bar". So the clicks are worth nothing if they stop, and the old
    # event-gated version stopped completely whenever no VARBIT_CHANGED arrived -- its only
    # click-without-events path was behind `if events is None`, which never runs with a live
    # stream. MEASURED live 2026-09-17: a Retort holding 'Alco-augmentator' took ZERO clicks for
    # 12s (USER: "second retort doesnt spam click slowing us down").
    #
    # The identity read before every click is what keeps spamming safe -- an extra click into a
    # station that has moved on auto-starts the next unprocessed potion there (the wrong-station
    # bug, seen twice today). It is one local request per tick.
    deadline = _now() + RETORT_MAX_S
    while _now() < deadline:
        state = _station_state(order_key)
        if state.get("potionName") != order_name:
            return          # finished (it ejects on its own) or the station moved on
        _click_station_in_place(ctx, "Retort")
        _tick_pause(ctx, 1, phase=RETORT_CLICK_PHASE_S)

    current_potion = _station_state(order_key).get("potionName")
    if current_potion and current_potion != order_name:
        ctx.state.trigger_safety_stop(
            f"Retort speedup: expected '{order_name}' but station now shows "
            f"'{current_potion}' -- a stray click likely started the wrong potion"
        )


# Deliberately human -- see _process_flash_speedup's docstring for why this exists and why it
# never applies twice in a row.
SPEEDUP_MISS_CHANCE = 0.12

# Of a missed first flash, how often the miss is a mistimed late click (reflexively clicking
# just as the window closes) rather than no click attempt at all.
SPEEDUP_LATE_CLICK_CHANCE = 0.5


def _process_flash_speedup(ctx, station_name: str, cue_graphics_id: int, station: dict,
                            second_click: bool, deadline_s: float = FLASH_WAIT_S) -> None:
    """Shared Agitator/Alembic speedup: click when the station's flash cue appears.

    The cue is a graphics object (id 2954/2955) created at the station; GRAPHICS_OBJECT_CREATED
    is the signal, a fresh /graphics-objects read the truth, and a cue counts only when it sits
    within FLASH_CUE_RADIUS of the station object's own tile (the station is multi-tile, and the
    other station's cue must never fire this one). The mouse is rested on the station BEFORE the
    cue (user directive 2026-08-13) so only a click in place has to fire in the window --
    FLASH_REACTION_S after the event, a human reaction rather than a 30ms poll's reflex; the
    Alembic's window is a single tick, so its draw is the tighter one.

    Agitator: "clicks can be done in each tick of the 2-tick window, netting double bonus
    progress" (wiki) -- with `second_click` the next progress tick after the first click gets a
    second one. A hit is a CHANGE of the station's skillshot varbit from its pre-cue value
    (noots-mixology: "the hit varbit may still hold a stale value from a previous success").
    On a miss the game grants a second cue; one more attempt is taken.

    Deliberately human (user directive 2026-08-13): SPEEDUP_MISS_CHANCE of the time the FIRST
    cue is let go -- either ignored or (SPEEDUP_LATE_CLICK_CHANCE) clicked a beat after the
    window closed -- and the second is never missed. With no event stream the cue is polled and
    a new cue is recognised only after the previous one has cleared (an unbroken "present" streak
    is the same flash)."""
    events = _events(ctx)
    key = station_name.lower()
    # Mark BEFORE the hover flight: the cue lands inside the first third of processing, which
    # can be the very first tick -- a mark taken after the flight would exclude it.
    entry_mark = _mark(ctx)
    if _hover_when_settled(ctx, station_name, check_ui_blockers=False) is None:
        return

    sx, sy = station.get("x"), station.get("y")
    _potion_vb, progress_vb = STATION_VARBITS[station_name]
    skill_vb = SKILLSHOT_VARBITS[station_name]
    window_ticks = FLASH_WINDOW_TICKS[station_name]

    def station_done() -> bool:
        return not _station_state(key).get("potionName")

    def near(p) -> bool:
        x, y = p.get("x"), p.get("y")
        if None in (x, y, sx, sy):
            return False
        return abs(x - sx) <= FLASH_CUE_RADIUS and abs(y - sy) <= FLASH_CUE_RADIUS

    def cue_event(p) -> bool:
        return p.get("id") == cue_graphics_id and near(p)

    def cue_present() -> bool:
        return any(g.get("id") == cue_graphics_id and near(g)
                   for g in controller.get_graphics_objects(fresh=True))

    # The first cue gets deadline_s; after a miss the retry cue is waited for as long as the
    # potion is still processing (stop=station_done), since the finish wait would otherwise
    # spend that time anyway. No fidgets here: the cursor must be ON the station when the cue
    # lands, and the Alembic window is one tick.
    deadline = _now() + deadline_s
    attempts = 0
    missed_first = False
    mark = entry_mark
    while attempts < 2:
        skill_before = controller.get_varbit(skill_vb)
        budget = max(0.0, deadline - _now()) if attempts == 0 and not missed_first else STATION_FINISH_S
        if budget <= 0:
            return
        # Fidgets only while waiting for a RETRY cue: the first cue is certain and soon, so the
        # cursor stays planted; a retry cue is a bonus that may never come, and a cursor frozen
        # for a minute costs more than the odd missed retry.
        #
        # The idle drift is held off for the whole wait: unlike the fidgeter (which returns to
        # input_state.fidget_anchor, so a parked cursor stays parked) the drift has no anchor and
        # walks the cursor off the station -- half of how the cursor and the Agitator came 260px
        # apart live 2026-09-17. The deliberate anti-freeze motion the comment above wants is the
        # fidget, which `fidget=` still controls.
        controller.mark_busy(budget)
        seen = _await_long(ctx, {"GRAPHICS_OBJECT_CREATED": cue_event}, budget,
                           confirm=cue_present, since=mark, stop=station_done,
                           fidget=(attempts > 0 or missed_first), reaction=False)
        if not seen:
            return
        mark = _mark(ctx)

        if not missed_first and random.random() < SPEEDUP_MISS_CHANCE:
            missed_first = True
            late = random.random() < SPEEDUP_LATE_CLICK_CHANCE
            debug_log.log("mixology", f"{station_name} speedup -- deliberately missing first flash"
                          + (" (late click)" if late else ""))
            _tick_pause(ctx, window_ticks, phase=FLASH_REACTION_S[station_name])
            if late:
                _click_station_in_place(ctx, station_name)
            if events is None:
                _wait_cue_clear(cue_present, deadline)
            continue

        _sleep(controller.sample_human_delay(*FLASH_REACTION_S[station_name]))
        mark_click = _mark(ctx)
        _click_station_in_place(ctx, station_name)
        attempts += 1

        if second_click:
            if events is not None:
                events.await_outcome({"VARBIT_CHANGED": _varbit_in((progress_vb,))},
                                     GAME_TICK_S * 2, confirm=None, since=mark_click,
                                     reaction=False, now_fn=_now, sleep_fn=_sleep)
                _sleep(controller.sample_human_delay(*RETORT_CLICK_PHASE_S))
            else:
                _tick_pause(ctx, 1, phase=RETORT_CLICK_PHASE_S)
            _click_station_in_place(ctx, station_name)

        if skill_before is None:
            return  # cannot judge the hit; never risk a second click on a hit

        def hit():
            now = controller.get_varbit(skill_vb)
            return now is not None and now != skill_before

        if _await(ctx, {"VARBIT_CHANGED": _varbit_in((skill_vb,))}, SKILLSHOT_CONFIRM_S,
                  confirm=hit, since=mark, reaction=False):
            debug_log.log("mixology", f"{station_name} speedup hit")
            return
        debug_log.log("mixology", f"{station_name} speedup missed the window -- waiting for the retry cue")
        if events is None:
            _wait_cue_clear(cue_present, deadline)


def _wait_cue_clear(cue_present, deadline) -> None:
    while _now() < deadline and cue_present():
        _sleep(0.15)


def _process_agitator_speedup(ctx, station: dict) -> None:
    """Agitator's cue (2954, VFX_MACHINERY_ALCHEMY01_AGITATOR01): click on it and again on the
    next tick -- see _process_flash_speedup."""
    _process_flash_speedup(ctx, "Agitator", AGITATOR_CUE_GRAPHICS_ID, station, second_click=True)


def _process_alembic_speedup(ctx, station: dict) -> None:
    """Alembic's cue (2955): one click inside its single-tick window -- see
    _process_flash_speedup."""
    _process_flash_speedup(ctx, "Alembic", ALEMBIC_CUE_GRAPHICS_ID, station, second_click=False)


# --- depositing ------------------------------------------------------------------------------

def deposit_phase(ctx, config: dict) -> None:
    inventory = controller.get_inventory()

    # A stray UNPROCESSED potion can only reach here from orders_phase's leftover flush (it is
    # not one of mm_chosen_orders, so nothing else targets it) -- CONFIRMED live 2026-08-13 one
    # such Liplack liquor desynced every positional index for a whole cycle. Process it through
    # the Retort just to make it depositable; which station does not matter for an orphan.
    unfinished = [i for i in inventory if i.get("id") in UNFINISHED_IDS]
    if unfinished:
        item = unfinished[0]
        item_id = item.get("id")
        if find_object("Retort") is None:
            debug_log.log(
                "mixology",
                f"waiting for processing to finish (no Retort found to flush it): "
                f"{[i.get('name') for i in unfinished]}",
            )
            return
        debug_log.log("mixology", f"flushing stray unprocessed potion '{item.get('name')}' via Retort")
        before = _count_item(item_id)
        mark = _mark(ctx)
        if _click_object(ctx, "Retort", check_ui_blockers=False) is None:
            return
        # Same stall bail-out _process_one has. Without it this sat out the full STATION_FINISH_S
        # whenever the flush click did not take -- 90s of the routine visibly doing nothing, which
        # is where it was caught idling live 2026-09-17. A station that is running advances on its
        # own, so a progress bar that has not moved means the click missed and re-clicking (next
        # tick, through this same path) is the fix.
        watch = {"key": None, "since": None}

        def flush_stalled() -> bool:
            st = _station_state("retort")
            k = (st.get("potionName"), st.get("progress"))
            if watch["key"] != k:
                watch["key"] = k
                watch["since"] = _now()
                return False
            return _now() - watch["since"] >= FINISH_STALL_S

        _await_long(ctx, {"VARBIT_CHANGED": _varbit_in((VB_RETORT_POTION,)), "INVENTORY_CHANGED": None},
                    STATION_FINISH_S, confirm=lambda: _count_item(item_id) < before, since=mark,
                    stop=flush_stalled)
        return

    finished = [i for i in inventory if i.get("id") in FINISHED_IDS]
    if not finished:
        ctx.state.data["mm_phase"] = "orders"
        return

    # Per the wiki a belt click is always safe and always makes progress on whatever is held
    # (fulfils every matching order, or deposits the first potion for a reduced reward) -- never
    # withhold it for a potion count (CORRECTED live 2026-08-13: a `>= 3` gate waited forever).
    #
    # But "deposits the first potion for a REDUCED reward" is the wasted deposit this routine
    # kept paying: one click fulfils every matching order at once, and a held potion that matches
    # none of the three live orders then gets thrown at the belt by the next click for a fraction
    # of its value. MEASURED live 2026-09-17: holding four finished potions against three orders,
    # the deposit phase ran four times shedding one each pass and the surplus was wasted -- 2 of
    # 26 orders. Orders rotate on every fulfilment, so a potion nothing wants YET is worth
    # keeping; it costs an inventory slot and nothing else.
    orders_now = {(o.get("name") or "") for o in (controller.get_mixology_state().get("orders") or [])}
    matching = [i for i in finished if (i.get("name") or "") in orders_now]
    if not matching and len(finished) < HOLD_UNMATCHED_MAX:
        debug_log.log("mixology",
                      f"holding {[i.get('name') for i in finished]} -- no live order wants them yet "
                      f"(depositing now would be a reduced reward)")
        ctx.state.data["mm_phase"] = "orders"
        return

    debug_log.log("mixology", f"Deposit phase entered with {[i.get('name') for i in finished]}")

    held_before_deposit = len(finished)
    mark = _mark(ctx)
    belt = _click_object(ctx, "Conveyor belt", check_ui_blockers=False)
    if belt is None:
        ctx.state.trigger_safety_stop("no conveyor belt found near the player")
        return

    def deposited():
        # A DECREASE, not "all gone": one click fulfils the matching orders and legitimately
        # leaves an unmatched potion behind, which the old all-gone test read as a failed deposit
        # and retried -- the retry being exactly what wasted it.
        return sum(1 for i in controller.get_inventory()
                   if i.get("id") in FINISHED_IDS) < held_before_deposit

    # The deposit empties the inventory of finished potions and the game zeroes then rewrites
    # the order varbits (order 1 type is the first to move).
    if _await(ctx, {"INVENTORY_CHANGED": None, "VARBIT_CHANGED": _varbit_in((VB_ORDER_1_TYPE,))},
              DEPOSIT_S + _walk_budget(belt), confirm=deposited, since=mark):
        # The game zeroes every order varbit and then writes the new set; wait for the write
        # (order-1 type non-zero, three orders readable) so the chained orders_phase reads the
        # NEW orders instead of returning on the transitional empty set.
        _await(ctx, {"VARBIT_CHANGED": lambda p: p.get("varbitId") == VB_ORDER_1_TYPE and p.get("value")},
               NEW_ORDERS_S, confirm=lambda: len(controller.get_mixology_state().get("orders", [])) >= 3,
               since=mark, reaction=False)
        ctx.state.data["mm_phase"] = "orders"
        return

    remaining = [i for i in controller.get_inventory() if i.get("id") in FINISHED_IDS]
    debug_log.log(
        "mixology",
        f"still have finished potions after conveyor belt click: "
        f"{[i.get('name') for i in remaining]} -- retrying",
    )


# --- digweed ---------------------------------------------------------------------------------

DIGWEED_CORNERS = ["northEast", "southEast", "southWest", "northWest"]  # /mixology-state's
                                                                          # digweed dict keys


def _apply_digweed_if_held(ctx) -> bool:
    """If holding a Digweed item AND an unprocessed Liplack liquor, use the digweed on it.
    Returns True if holding digweed at all (used, or waiting for a Liplack liquor -- per the
    wiki digweed only withers on leaving the lab), False if not holding any.

    Checked every tick against the currently-held bases (CORRECTED 2026-08-13: tying the
    application to the take instant missed a base already sitting in the inventory), and held
    until Liplack liquor specifically (USER: "we just save it until there is a LLL in our
    order"). The application is confirmed by the digweed leaving the inventory (CONFIRMED live
    2026-08-13: an unconfirmed apply produced two Liplack liquors)."""
    digweed_item = next(
        (i for i in controller.get_inventory() if i.get("id") == MM_LAB_SPECIAL_HERB_ID), None
    )
    if digweed_item is None:
        return False

    # Unprocessed OR finished Liplack liquor: the wiki says digweed "can be added to any potion,
    # before or after processing", so a finished one is a target until the belt takes it.
    liplack_ids = {k for k, v in UNFINISHED_IDS.items() if v == "Liplack liquor"}
    liplack_ids |= {k for k, v in FINISHED_IDS.items() if v == "Liplack liquor"}
    best = next(
        (i for i in controller.get_inventory() if i.get("id") in liplack_ids), None
    )
    if best is None:
        return True  # holding digweed, no Liplack liquor held yet -- wait for one

    debug_log.log("mixology", f"applying held digweed to '{best.get('name')}'")
    controller.open_inventory_tab(ctx.ser)
    mark = _mark(ctx)
    controller.click_entity(ctx.ser, digweed_item, check_ui_blockers=False, simulate_mistakes=False)
    controller.human_delay(0.15, 0.3)
    controller.click_entity(ctx.ser, best, check_ui_blockers=False, simulate_mistakes=False)

    def consumed():
        return not any(i.get("id") == MM_LAB_SPECIAL_HERB_ID for i in controller.get_inventory())

    if not _await(ctx, "INVENTORY_CHANGED", DIGWEED_APPLY_S, confirm=consumed, since=mark):
        debug_log.log(
            "mixology",
            f"digweed application on '{best.get('name')}' didn't confirm (still held) -- retrying next tick",
        )
    return True


def maybe_collect_digweed(ctx, config: dict) -> None:
    # Live inventory check instead of a sticky flag (Fix 5, final whole-branch review): digweed
    # withers out of the inventory if the player leaves the lab, which would leave a flag stuck.
    if _apply_digweed_if_held(ctx):
        return  # holding one suppresses new spawns anyway
    if not config.get("digweed", {}).get("enabled", True):
        return

    # /mixology-state is read when a herb-ready varbit was pushed since the last look (on_event
    # sets the hint) or every DIGWEED_STATE_POLL_S as the no-events fallback.
    data = ctx.state.data
    last_poll = data.get("mm_digweed_polled_at", 0.0)
    if not data.get("mm_digweed_hint") and _now() - last_poll < DIGWEED_STATE_POLL_S:
        return
    data["mm_digweed_hint"] = False
    data["mm_digweed_polled_at"] = _now()

    digweed = controller.get_mixology_state().get("digweed", {})
    if not any(digweed.get(corner) for corner in DIGWEED_CORNERS):
        return

    mark = _mark(ctx)
    plant = _click_object(ctx, "Digweed", exclude="young", check_ui_blockers=False)
    if plant is None:
        return  # ready per the varbit but not yet visible/already collected by the time we look
    _await(ctx, "INVENTORY_CHANGED", DIGWEED_PICK_S + _walk_budget(plant),
           confirm=lambda: any(i.get("id") == MM_LAB_SPECIAL_HERB_ID for i in controller.get_inventory()),
           since=mark)


# --- engine contract -------------------------------------------------------------------------

PHASES = {
    "bank_herbs": bank_herbs_phase,
    "orders": orders_phase,
    "mix": mix_phase,
    "process": process_phase,
    "deposit": deposit_phase,
}


def setup(ctx) -> None:
    ctx.state.data.setdefault("mm_phase", "bank_herbs")


def on_event(ctx, event_type: str, payload) -> None:
    """A digweed corner's herb-ready varbit flipping non-zero flags the next tick to read
    /mixology-state -- the accelerator for maybe_collect_digweed's poll.

    Also watches for a WASTED deposit. RECORDED 2026-09-17: "You deposit some <potion>, but fail
    to fulfil an order." -- the belt took a potion that matched none of the three live orders, so
    the whole cycle's work on it earned a reduced reward. It is the one failure that costs
    throughput while leaving every other signal looking healthy (the potion really was made, the
    station really did finish, the deposit really did happen), so nothing else would surface it.
    Counted here rather than polled: a chat poll can miss messages between reads."""
    if event_type == "CHAT_MESSAGE" and payload:
        # "fulfil" is a prefix of "fulfill", so this matches either spelling.
        if "fail to fulfil" in (payload.get("message") or "").lower():
            data = ctx.state.data
            data["mm_wasted_deposits"] = data.get("mm_wasted_deposits", 0) + 1
            debug_log.log("!", f"WASTED DEPOSIT ({data['mm_wasted_deposits']} so far): "
                               f"{payload.get('message')}")
        return
    if event_type != "VARBIT_CHANGED" or not payload:
        return
    if payload.get("varbitId") in VB_HERB_READY and payload.get("value"):
        ctx.state.data["mm_digweed_hint"] = True


def _progress_signature(ctx):
    """Everything that moves when the routine is making progress: the phase, the inventory's
    item ids, the hopper totals and what each station holds."""
    state = controller.get_mixology_state()
    hopper = state.get("hopper", {}) or {}
    stations = state.get("stations", {}) or {}
    return (
        ctx.state.data.get("mm_phase"),
        tuple(sorted((i.get("id") or 0) for i in controller.get_inventory())),
        tuple(hopper.get(p, 0) for p in ("mox", "aga", "lye")),
        tuple((stations.get(k, {}) or {}).get("potionName") for k in ("retort", "agitator", "alembic")),
    )


def _stuck_watch(ctx) -> None:
    """Once per tick: if NOTHING in _progress_signature has changed for STALL_WARN_S, log a
    `[!]` line (the live monitor keys on those); for STALL_STOP_S, safety-stop. A phase that
    returns every tick with nothing happening -- standing outside the lab, an order set that
    never fills, a click that never lands -- used to look exactly like a healthy run in the
    log. The longest legitimate silence is one station's processing (up to ~60s un-boosted,
    and even that changes the inventory at its end), well under STALL_WARN_S."""
    data = ctx.state.data
    sig = _progress_signature(ctx)
    now = _now()
    if sig != data.get("mm_stall_sig"):
        data["mm_stall_sig"] = sig
        data["mm_stall_since"] = now
        data["mm_stall_warned"] = False
        return
    still = now - data.get("mm_stall_since", now)
    if still >= STALL_STOP_S:
        ctx.state.trigger_safety_stop(
            f"mixology: no progress for {int(still)}s in phase '{data.get('mm_phase')}'"
        )
    elif still >= STALL_WARN_S and not data.get("mm_stall_warned"):
        data["mm_stall_warned"] = True
        debug_log.log("!", f"mixology: STUCK? no progress for {int(still)}s in phase '{data.get('mm_phase')}'")


def tick(ctx) -> None:
    config = ctx.state.config
    if not ctx.state.data.get("mm_level_checked"):
        if not check_level_requirement(ctx, config):
            return  # skills not synced yet post-login -- retry next tick, don't mark done
        ctx.state.data["mm_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    # NOT IN THE LAB = DO NOTHING. The engine's "logged in" gate is GameState, and GameState is
    # already LOGGED_IN at the click-here-to-play screen (CLAUDE.md, LoginProvider) -- so tick()
    # runs during the whole login transition, before the player is in the world at all. LIVE
    # 2026-09-18: the account was logged out, auto-login reached the welcome screen, and tick()
    # meanwhile pulled at the Mox lever three times against an empty world ("last-action
    # target=''" each time), burning the retry budget into a safety stop that needed a restart --
    # while login was still clicking the welcome screen away. /mixology-state's own inLab is the
    # honest answer to "can this routine do anything right now", and the same guard covers
    # walking out of the lab, a hop, or a disconnect mid-cycle.
    if not controller.get_mixology_state().get("inLab"):
        return

    _stuck_watch(ctx)

    # Phase transitions CHAIN inside one tick (bounded by TICK_CHAIN_MAX and the engine's own
    # pause/safety/break flags): deposit -> orders -> mix, bank -> orders, mix -> process. Each
    # arrow used to cost an engine-loop iteration (~1s) in which the player stood still; a
    # human reads the new orders and pulls the first lever the moment the belt takes the potions.
    # Digweed is checked before EVERY chained phase -- a tick now spans a whole cycle.
    for _ in range(TICK_CHAIN_MAX + 1):
        maybe_collect_digweed(ctx, config)
        if ctx.state.safety_stopped or ctx.state.paused:
            return
        phase = ctx.state.data.get("mm_phase", "bank_herbs")
        handler = PHASES.get(phase)
        if handler is None:
            ctx.state.trigger_safety_stop(f"unknown mastering_mixology phase '{phase}'")
            return
        handler(ctx, config)
        if ctx.state.safety_stopped or ctx.state.paused:
            return
        break_due = getattr(ctx.state, "break_due", None)
        if callable(break_due) and break_due((config or {}).get("breaks") or {}):
            return
        if ctx.state.data.get("mm_phase", "bank_herbs") == phase:
            return
