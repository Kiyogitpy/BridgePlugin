# routines/falconry.py
#
# Falconry (Dashing kebbits, Piscatoris Hunter area) with a full Castle Wars bank round trip.
#
# Run with:
#     python controller.py routines/falconry.py
# or pick FALCONRY from Controller Settings -> Routine (see RoutineChoice.java / ROUTINE_FILES).
#
# EVERY CONSTANT AND SIGNAL BELOW WAS DERIVED FROM A RECORDED HUMAN RUN, not guessed:
# ControllerAddon/recordings/2026-08-23_121236_falconry-entire-run.{log,summary}. Where something
# could NOT be established from that recording it is called out inline as an assumption -- treat
# those as the first suspects if this misbehaves live. See CLAUDE.md's "Recording a human session"
# section for the workflow that produced it.
#
# ---------------------------------------------------------------------------------------------
# The catch loop, as observed (timings are from the recording, click -> effect):
#
#     click "Catch" on a Dashing kebbit (5533)
#       +0.4s  player anim 5162 + spotanim 918, spotanim 923 on the kebbit   <- the throw landed
#       +1.6s  varbit 14192 = 5                                              <- SUCCESS
#              chat "The falcon successfully swoops down and captures the kebbit."
#              npc idx N BECOMES Gyr Falcon (1343), same index the kebbit had
#       or
#       +1.6s  chat "...but just misses catching it." / "...too quick for you."  <- MISS, no varbit
#
#     click "Retrieve" on the Gyr Falcon (1343)
#       +0.3s  varbit 14192 = 0
#              chat "You retrieve the falcon as well as the fur of the dead kebbit."
#              (or bare "You retrieve the falcon." when there was no room for the fur)
#
# 46 catch attempts produced 27 successes and 18 misses, so a miss is the NORMAL case, not an
# error -- roughly 40% of throws. Nothing here treats one as a failure.
#
# THE FALCON DOES NOT ANIMATE. Confirmed across two recordings: the Gyr Falcon has no animation of
# its own at any point, so there is no "swoop" animation to wait on. varbit 14192 and the NPC
# transformation are the only mechanical signals, which is why this routine keys off them.
#
# ---------------------------------------------------------------------------------------------
# Loot and the bank trigger:
#
# Fur and meat go into their pouches until each holds 28, then SPILL into the inventory (user,
# confirmed against the recording: fur/meat first appeared in inventory deltas at t=147.7s and
# every catch after that produced both). Bones always go straight to the inventory. So one bone
# appearing == one fur + one meat banked in the pouches.
#
# Post-spill each catch costs ~2 inventory slots (fur + meat, bones being dropped), so the
# inventory is the binding constraint and freeSlots is the honest trigger. The human banked with
# 12 fur + 12 meat held.
#
# CHANGED 2026-09-15: bones are no longer dropped as they arrive. They accumulate and go out in
# one burst when the inventory is nearly full (DROP_AT_FREE_SLOTS / _drop_junk) -- MEASURED at
# 17-22% of all hunting time when it was one bone per loop iteration. So "bones being dropped"
# above is now true only on average: between bursts they are occupying slots, which is why the
# drop check runs BEFORE the bank check in _hunt.

import time

import banker
import controller
import debug_log
import outcome_waits
import travel
import worldmap

# --------------------------------------------------------------------------- ids and waypoints

# WHICH KEBBIT. CHANGED 2026-09-14 to the Dark kebbit (USER: "It should actually catch dark
# kebbirs instead they are plentiful and faster to catch"). CONFIRMED live the same minute off
# /npcs at the hunting spot: 3x Dark kebbit (id 5532) in range against 1x Dashing (5533) and 4x
# Spotted (5531) -- the Dashing kebbit is the highest-tier fur but there is usually only one of
# them, so the throws go further with the Dark.
#
# For golem crafting this costs nothing: "Dark kebbit fur" is already in golem_crafting's
# FUR_PRIORITY (a golem takes any fur on that table), it is simply a lower entry than Dashing.
#
# A species is three names that must agree -- the NPC, its fur, its raw meat -- so they live in
# one table rather than three constants that can drift apart.
# `meat` is None for a species that yields none. MEASURED live 2026-09-14 on the Dark kebbit:
# 16 retrieves, 16 bone drops, ZERO raw meat -- it gives fur and bones only. That is worth
# encoding rather than carrying a meat pouch and a drop rule that can never fire; see
# golem_crafting's KIT_ITEMS, which now asks this table whether a meat pouch is wanted at all.
# The Dashing kebbit's "Raw dashing kebbit" was read off a live inventory; Spotted is UNVERIFIED.
KEBBIT_SPECIES = {
    "dark": {"npc": "Dark kebbit", "fur": "Dark kebbit fur", "meat": None},
    "dashing": {"npc": "Dashing kebbit", "fur": "Dashing kebbit fur",
                "meat": "Raw dashing kebbit"},
    "spotted": {"npc": "Spotted kebbit", "fur": "Spotted kebbit fur",
                "meat": "Raw spotted kebbit"},
}
DEFAULT_SPECIES = "dark"

KEBBIT_NAME = KEBBIT_SPECIES[DEFAULT_SPECIES]["npc"]
FALCON_NAME = "Gyr Falcon"
FALCONER_NAME = "Matthias"
# Worn in the WEAPON slot (CONFIRMED
# live, not a hands slot) for as long as an active falcon is out. No "wear" code exists anywhere
# in this file because none is needed: the game puts it there automatically once Matthias grants a
# falcon and clears it automatically once the falcon is gone, so checking for it is a direct read
# of "do we currently have a usable falcon", not something this routine has to maintain itself.
FALCONER_GLOVE_NAME = "falconer's glove"

BONES_NAME = "Bones"
FUR_NAME = KEBBIT_SPECIES[DEFAULT_SPECIES]["fur"]
MEAT_NAME = KEBBIT_SPECIES[DEFAULT_SPECIES]["meat"]      # None when the species yields no meat

RING_OF_DUELING = "ring of dueling"
ARDOUGNE_CLOAK = "ardougne cloak"
# No Dramen/Lunar staff anywhere in this file: USER 2026-09-06 "dramen staff isnt needed to take
# fairy rings anymore" (the game dropped the requirement) -- the staff wield/unwield legs are gone.

# varbit 14192: 5 = the falcon is sitting on a caught kebbit and must be retrieved, 0 = it is with
# us and can be thrown. Established by frequency and pairing, not by guessing at its meaning: it
# changed exactly twice per successful catch across both recordings (54 changes / 27 catches, and
# 14 / 7), always 5 on the catch and 0 on the retrieve, and never moved on a miss.
VARBIT_FALCON_HAS_CATCH = 14192
FALCON_CATCH_WAITING = 5

# Region ids, in the order the recorded trip visited them.
REGION_FALCONRY = 9528

# THE HUNTING GROUND IS TWO REGIONS, and one id was never a description of it. MEASURED
# 2026-09-15 by flooding the enclosure from TILE_HUNT on the collision map: 389 tiles, of which
# 335 are in 9528 and **54 are in 9527** -- the fence runs to y 3576 while the 9528/9527 boundary
# is at y 3584, so the southern eight rows of the pen report the lower region. The falconer
# himself (2379, 3603) is inside the same component.
#
# CONFIRMED live the same night: a kebbit chase carried the player to (2380, 3583) -- ONE TILE
# south of the boundary, still well inside the pen -- and both this routine's `_hunt` guard and
# golem_crafting's `trip_leg` fell through to "not on any known leg". The run safety-stopped and
# sat there for over two hours (USER: "stuck at falco"). Nothing was wrong with the position; the
# constant simply did not cover the ground it named.
FALCONRY_REGIONS = frozenset({9528, 9527})

# THE HUNT IS LEASHED TO TILE_HUNT, and that is the real fix for the whole family of
# "we ended up somewhere unexpected" stops -- the region constant above was the SYMPTOM fix.
#
# MEASURED LIVE 2026-09-15, twice in six minutes, with a freeze-frame off /npcs at the second
# stop: the player was at (2364, 3581) -- **24 tiles from TILE_HUNT and outside the mapped pen**
# -- standing among Wild kebbit / Copper longtail (the net-trap ground west of falconry), with
# **Dark kebbits at (2369, 3580) and (2367, 3587) that are ALSO outside the pen**.
#
# The mechanism: `_nearest_npc` measures from the PLAYER, not from the hunt tile, and this
# species lives on both sides of the fence. Every throw and every retrieve walks the player a few
# tiles toward its target, so picking the nearest kebbit each time makes the error COMPOUND --
# each action re-centres the search on a position further out than the last. Nothing pulled it
# back. The two observed endings:
#   - drift into region 9272 -> golem_crafting's trip_leg calls it "walk_in" and walks back over
#     the stile MID-RETRIEVE, abandoning a falcon that is sitting on a kill. The falcon is then
#     never collected and is lost -- and the glove STAYS in the weapon slot (measured: glove worn,
#     varbit 14192 == 0, no Gyr Falcon anywhere in the scene), so glove_worn() still answers "we
#     have a falcon" and the routine throws into nothing until _throw_falcon's 6-failure gate
#     re-hires. Cost: 45s of the out-of-range spin below + ~26s of empty throws + 500gp.
#   - drift into region 9271 -> no leg matches at all -> the run safety-stops.
#
# 14 is the pen: flooding the collision map from TILE_HUNT gives 389 tiles spanning x 2374-2393,
# y 3576-3604, and TILE_HUNT (2388, 3590) is at most 14 Chebyshev from any corner of that. The
# out-of-pen kebbits measured above sit at 19-24, so the leash separates them cleanly. Anchoring
# on TILE_HUNT rather than on the player is the whole point -- a leash that moves with the player
# is not a leash.
HUNT_LEASH_TILES = 14
# The falcon gets a longer rope than the kebbits, from the PLAYER: a kebbit can run a few tiles
# before it dies, so the bird legitimately lands outside the leash and we still want the catch.
# It is never a reason to go THROWING out there, which is what the leash above prevents.
FALCON_SEARCH_TILES = 20
# ...and the "catch waiting but we cannot see the falcon" recovery is bounded, because its action
# is `walk to TILE_HUNT`, which does NOTHING when we are already standing there. MEASURED live
# 2026-09-15: 54 iterations over 45s, ending only when the falcon despawned on its own.
FALCON_OUT_OF_RANGE_TRIES = 6
# The WIDER Piscatoris Hunter area the pen sits inside -- the 2x3 block of regions covering
# x 2304-2431, y 3520-3647 (region x 36-37, region y 55-57), which is every region a drifted hunt
# or a fairy-ring landing has been observed in: 9272 is the ring's landing pad and 9271 is where
# the 2026-09-15 drift safety-stopped. Being HERE is never a reason to stop -- it is a walk back,
# and falconry's own _leg_walk_home already asks the map whether TILE_HUNT is routable and climbs
# the stile only when it is not. Naming the neighbourhood once beats discovering it one region id
# per incident, which is what the two comments above this are a record of.
PISCATORIS_REGIONS = frozenset({9271, 9272, 9273, 9527, 9528, 9529})
REGION_CASTLE_WARS = 9776
REGION_MONASTERY = 10290
REGION_FAIRY_RING = 10546
REGION_FAIRY_LANDING = 9272

# Tiles the human actually stood on / clicked, read straight out of the recording.
TILE_HUNT = {"x": 2388, "y": 3590, "plane": 0}
TILE_FALCONER = {"x": 2379, "y": 3603, "plane": 0}
TILE_BANK = {"x": 2443, "y": 3083, "plane": 0}
TILE_FAIRY_RING = {"x": 2655, "y": 3229, "plane": 0}
TILE_STILE = {"x": 2370, "y": 3621, "plane": 0}

BANK_CHEST_ID = 4483
STILE_ID = 19222
# The stile OBJECT sits at (2371, 3621); TILE_STILE is the tile the player stands on to use it.
# CONFIRMED live 2026-09-14 off /objects, after a hardcoded-tile lookup wedged the leg.
STILE_SCAN_RADIUS = 15

# Used ONLY by _bank_trip's own REGION_FALCONRY dispatch (crossing the stile flips the reported
# region before the walk home is actually finished -- see that function's own comment) to tell
# "still finishing the walk home" apart from "genuinely back at the hunting ground, safe to
# depart again". Deliberately NOT used by _leg_walk_home's own stile-avoidance check, which needs
# a tighter bound (TILE_STILE's own y) -- CONFIRMED live 2026-08-24 that this 6-tile-wide buffer
# is far more than crossing the stile actually moves the player, so using it there let the
# routine climb back and forth over the same stile forever (see that function's own comment).
SOUTH_OF_STILE_Y = 3615

# How much of the component around a tile to flood before calling it sealed. Only ever needs to
# tell 1 from many -- the Piscatoris ring tile returns exactly 1, its neighbours 733-746.
ISOLATED_PROBE_NODES = 64

# Menu options, exactly as they appeared in the recording's menuAction/option fields.
OPT_CASTLE_WARS = "Castle Wars"
OPT_KANDARIN_MONASTERY = "Monastery Teleport"
OPT_LAST_DESTINATION = "Last-destination"
OPT_QUICK_FALCON = "Quick-falcon"

# --- The fairy ring dial (MEASURED LIVE 2026-09-14, against the open interface) ---------------
#
# The ring is DIALLED, never taken on "Last-destination" -- see _leg_fairy_ring for the live
# failure that forced this. Everything below was read off the running client:
#
#   * "Configure" on the ring opens group 398 ("Choose a combination"), three dials with a
#     clockwise/counter-clockwise arrow each, plus a Confirm button whose TEXT is a live readout
#     of whatever the dials currently spell ("Invalid location" when they spell nothing).
#   * Each dial is one varbit, 0-3, and each clockwise click adds 1 (mod 4). VERIFIED by turning
#     each dial once and reading the varbit back: 398.19 -> 3985, 398.21 -> 3986, 398.23 -> 3987.
#   * The letter orders were verified against the travel log's own codes, not assumed:
#     [1,0,0] read "Abyssal Nexus" (DIP), [1,1,1] read "Myreque hideout under The Hollows" (DLS),
#     [0,3,3] read "Cave south of Dorgesh-Kaan" (AJQ). The third of those is what corrected dial
#     2 from "ILJK" to "ILKJ" -- a plausible-looking order that would have teleported wrong.
#   * The TRAVEL LOG (group 381) was tried first and rejected: its rows carry a "Use code" action
#     and read nicely (381.30 is "Kandarin: Piscatoris Hunter area", name "A K Q"), but it is a
#     SCROLLING list whose widget bounds are content-space -- the Piscatoris row sat at canvasY
#     841 against a panel ending at 848, so the click landed on background and the menu offered
#     only "Cancel". Driving it would mean solving scroll position; the dial does not move.

# Where the falconry trip is going, and the substring the Confirm button must read before
# anything is confirmed. VERIFIED live: dialling AKQ made 398.26 read "Piscatoris Hunter area"
# and confirming landed at (2319, 3619), region 9272 == REGION_FAIRY_LANDING.
FAIRY_CODE_FALCONRY = "AKQ"
FAIRY_EXPECT_FALCONRY = "Piscatoris"

# --- Event-driven outcomes (2026-09-06, docs/superpowers/specs/2026-09-06-golem-crafting-events-
# and-falconry-design.md). The falcon varbit is pushed as VARBIT_CHANGED, so a throw's result and
# a retrieve are awaited on it and confirmed on /varbit; a teleport is awaited on the region the
# recording says it lands in. Everything a caller acts on is still an HTTP read. These primitives
# are shared with routines/golem_crafting.py's fur trip through hunt_step().
VARBIT_SUBSCRIPTIONS = (VARBIT_FALCON_HAS_CATCH,)
THROW_RESOLVE_S = 2.6          # catch or miss both report ~1.6s after the click (recording)
RETRIEVE_S = 2.5               # retrieval resolved in ~0.3s; generous headroom
HIRE_S = 3.0                   # Quick-falcon -> glove worn / coins gone landed 0.2s after the click
DROP_CONFIRM_S = 1.6           # a shift-drop -> the item gone from the inventory
# JUNK IS DEFERRED AND BURST-DROPPED, not dropped one item per hunt_step (2026-09-15, USER:
# "letting bones accumulate until 1 slot left and we drop them all"). One bone per hunt_step is
# one bone per ENGINE LOOP ITERATION, and MEASURED across three live logs of the golem fur trip
# the same day (logs/gc_live_0915_{004101,043927,121222}.log) that cost 2.60s / 2.69s / 3.26s per
# bone and **17-22% of ALL hunting time** -- 95s of one 582s hunt, 29 bones. The bone drops
# themselves are not avoidable (one per catch, and the inventory is the only place they land);
# their PER-DROP overhead is: an /inventory read, a tab raise, a 1.6s confirm wait, a human_delay
# and a loop hand-back, every one of them spent on a single click.
#
# 4, not the user's literal 1, and the extra three slots are load-bearing:
#   - golem_crafting's fur trip ENDS on fur SPILLING into the inventory (_hunt_done) -- that is
#     the only signal the fur pouch has hit 28. Fill the last slot with a bone and the spill has
#     nowhere to go, the signal never fires and the trip runs to its 25-minute time cap instead.
#   - it has to sit ABOVE bankAtFreeSlots (3) so this routine's own tick drops before it decides
#     to bank, rather than walking an inventory of bones to Castle Wars.
# The cost of the three slots is ~3 bones per burst out of ~19; the burst is ~0.4s/bone either way.
DROP_AT_FREE_SLOTS = 4
INVENTORY_SLOTS = 28
TELEPORT_BUDGET_S = 8.0        # teleport animation + region load (3.0-3.6s measured)
# Worn-equipment interface (group 387) children. cape 16 / weapon 18 / ring 24 are read straight
# out of the recording; shield 20 and gloves 22 follow the interface's standard layout and are
# UNVERIFIED live (fur-trip worklist item 3). Falconry needs all three hands slots empty (wiki).
EQUIPMENT_GROUP = 387
EQUIPMENT_WEAPON_CHILD = 18
EQUIPMENT_SHIELD_CHILD = 20
EQUIPMENT_GLOVES_CHILD = 22


def _now():
    return time.time()


def _sleep(s):
    if s > 0:
        time.sleep(s)


def _await(ctx, signals, budget_s, confirm, since=None, reaction=True):
    return outcome_waits.await_pre(ctx, signals, budget_s, confirm, since=since,
                                   reaction=reaction, now_fn=_now, sleep_fn=_sleep)

DEFAULT_CONFIG = {
    "enabled": True,

    # Bank when this few inventory slots remain. 3 leaves room for the fur+meat+bone of a catch
    # that lands while we are already walking away.
    "bankAtFreeSlots": 3,

    "dropBones": True,
    # Raw dashing kebbit only ever costs slots when no meat pouch is carried; the recorded human
    # had one (meat went to the pouch), so the standalone routine keeps it. golem_crafting's fur
    # trip passes drop_meat=True to hunt_step instead.
    "dropMeat": False,
    # Let junk pile up and go out in ONE burst once this few slots remain -- see
    # DROP_AT_FREE_SLOTS for the measurement and for why it is not 1.
    "dropAtFreeSlots": DROP_AT_FREE_SLOTS,
    "maxKebbitDistance": 12,

    # Quick-falcon costs 500gp per falcon; below this we cannot re-hire and must stop.
    "minCoins": 500,

    "bankNames": ["Bank chest"],
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
        # Deliberately False: this routine manages a deliberately-filling inventory and banks on
        # its own trigger. The engine-level full-inventory stop would fire first and kill the run.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # No Controller Settings panel section for this routine -- same as gemstone_crab.py/mining.py.
    return DEFAULT_CONFIG


def setup(ctx):
    debug_log.log("falconry", "reminder: OSRS Settings -> Controls -> 'Shift click to drop items' "
                              "must be ON, or bone dropping will right-click instead.")
    debug_log.log("falconry", f'reminder: add "{KEBBIT_NAME}" and "{FALCON_NAME}" to Controller '
                              f"Settings -> Clicking -> Priority NPCs -- they occupy the same tile "
                              f"as each other, so a plain click can resolve to the wrong one.")

    # ctx.state.data resets on restart but the character's position does not (see CLAUDE.md). If
    # we come back mid-trip, resume the trip rather than trying to hunt in Castle Wars.
    region = controller.get_player().get("regionId")
    ctx.state.data["banking"] = region not in FALCONRY_REGIONS
    if ctx.state.data["banking"]:
        debug_log.log("falconry", f"restarted outside the hunting area (region {region}) -- "
                                  f"resuming the bank trip")


# --------------------------------------------------------------------------- small helpers

def _inventory_items(name_substring: str):
    needle = name_substring.lower()
    return [i for i in controller.get_inventory() if needle in (i.get("name") or "").lower()]


def _first_inventory_item(name_substring: str):
    items = _inventory_items(name_substring)
    return items[0] if items else None


def _free_slots() -> int:
    """Occupied slots counted from the live inventory list, NOT from distinct item names.

    Non-stacking items each hold their own slot: 11 Bones is 11 slots, not 1. Getting this wrong
    is exactly the bug that made the recorder report 22 slots free while the game was refusing fur
    pickups for lack of room -- see RecorderProvider.occupiedSlots."""
    return 28 - len(controller.get_inventory())


def tiles_from_hunt(x, y) -> int:
    """Chebyshev distance from TILE_HUNT. Pure -- the leash's one measurement."""
    return max(abs(int(x) - TILE_HUNT["x"]), abs(int(y) - TILE_HUNT["y"]))


def in_leash(entity, leash: int = HUNT_LEASH_TILES) -> bool:
    """Whether an NPC (or a player dict) is inside the hunting pen's leash. Pure.

    An entity with no coordinates answers True: a missing field must not silently empty the
    candidate list and strand the hunt, the same fail-open rule travel.interaction_tiles uses."""
    x, y = (entity or {}).get("x"), (entity or {}).get("y")
    if x is None or y is None:
        return True
    return tiles_from_hunt(x, y) <= leash


def _nearest_npc(name: str, max_distance: int, leash: int = None):
    """Nearest matching NPC within `max_distance` OF THE PLAYER, and -- when `leash` is given --
    within that many tiles of TILE_HUNT. See HUNT_LEASH_TILES: without the second bound the
    search re-centres on the player every action and walks itself out of the pen."""
    best = None
    for npc in controller.get_npcs():
        if (npc.get("name") or "").lower() != name.lower():
            continue
        distance = npc.get("distance", 9999)
        if distance < 0 or distance > max_distance:
            continue
        if leash is not None and not in_leash(npc, leash):
            continue
        if best is None or distance < best.get("distance", 9999):
            best = npc
    return best


def _falcon_has_catch() -> bool:
    return controller.get_varbit(VARBIT_FALCON_HAS_CATCH) == FALCON_CATCH_WAITING


def _shift_click(ctx, item) -> bool:
    """Shift-click an inventory item.

    Context decides what that means: with the bank closed it is Drop, with the bank open it is
    Deposit-All. Both are the gesture the recorded human used for those two jobs.

    check_ui_blockers=False and simulate_mistakes=False: an inventory slot is always inside a UI
    panel by definition (see travel.teleport_to_ferox for the loop that flag otherwise causes), and
    a deliberate misclick here would drop nothing while the routine believed it had."""
    controller.key_down(ctx.ser, "SHIFT")
    try:
        controller.human_delay(0.05, 0.12)
        return controller.click_entity(ctx.ser, item, check_ui_blockers=False,
                                       simulate_mistakes=False)
    finally:
        controller.key_up(ctx.ser, "SHIFT")


def _use_teleport(ctx, item_substring: str, option: str, left_click: bool = None,
                  expect_region=None) -> bool:
    """Use a teleport item in the INVENTORY to reach `option`'s destination.

    Inventory, not worn: /equipment carries no screen positions, so a worn item cannot be
    clicked from here (see the module docstring's loadout note).

    2026-09-06, USER: "for inventory teleports ive changed them so we just click the item and we
    teleport to the right spot" -- every inventory teleport is a LEFT-click first, verified
    through GET /hover (expected_action=`option`, so a swap that is not in place on this item
    withholds the click instead of, say, wearing the ring), and only a withheld/failed left-click
    falls back to the right-click menu read back through GET /menu. `left_click` is kept for
    old callers: True skips the hover verification, False forces the menu path."""
    item = _first_inventory_item(item_substring)
    if item is None:
        debug_log.log("falconry", f"no {item_substring} in inventory -- cannot teleport")
        return False

    debug_log.log("falconry", f"teleporting: {option}")
    ok = False
    if left_click is not False:
        controller.human_delay(0.05, 0.12)
        ok = controller.click_entity(ctx.ser, item, check_ui_blockers=False, simulate_mistakes=False,
                                     expected_action=None if left_click else option)
    if not ok:
        if getattr(ok, "withheld", False):
            debug_log.log("falconry", f"{item_substring}: left-click is not '{option}' here -- using the menu")
        ok = controller.click_menu_action(ctx.ser, item, option, check_ui_blockers=False)
    if not ok:
        return False

    if expect_region is None:
        controller.human_delay(3.0, 3.6)  # teleport animation + region load
        return True
    # 2026-09-06: the landing is awaited, not slept off -- /player.regionId is the truth, and the
    # per-frame PLAYER_STOPPED/PLAYER_MOVING stream says when to look (a teleport ends with the
    # player standing still in the new region).
    landed = _await(ctx, ("PLAYER_STOPPED", "PLAYER_MOVING"), TELEPORT_BUDGET_S,
                    confirm=lambda: (controller.get_player() or {}).get("regionId") == expect_region,
                    reaction=False)
    if not landed:
        debug_log.log("falconry", f"teleport '{option}' did not land in region {expect_region} "
                                  f"within {TELEPORT_BUDGET_S}s")
    return bool(landed)


def _has_return_loadout() -> bool:
    """Everything needed to get BACK from the bank. Checked before leaving the hunting area, not
    at the bank -- being short of the cloak at Castle Wars strands the character, whereas
    noticing here just means we keep hunting."""
    missing = [name for name in (RING_OF_DUELING, ARDOUGNE_CLOAK)
               if _first_inventory_item(name) is None]
    if missing:
        debug_log.log("falconry", f"missing return items: {', '.join(missing)}")
    return not missing


# --------------------------------------------------------------------------- the hunting loop

def tick(ctx):
    config = ctx.state.config
    player = controller.get_player()
    region = player.get("regionId")

    if ctx.state.data.get("banking"):
        _bank_trip(ctx, config, region)
        return

    _hunt(ctx, config, player, region)


def _hunt(ctx, config, player, region):
    if region not in FALCONRY_REGIONS:
        # Not where we think we are -- most likely a restart or a stray teleport. Route through
        # the trip logic, which knows how to get home from each leg.
        debug_log.log("falconry", f"not in the hunting region (in {region}) -- routing home")
        ctx.state.data["banking"] = True
        return

    # 1. A falcon sitting on a kill blocks everything else -- it cannot be thrown again until
    #    retrieved, so this is unconditionally first (inside hunt_step).
    # 3. Full enough to be worth a trip? Checked here, between the retrieve/drop and the hire/
    #    throw, so a landing catch is still collected before we leave.
    if _retrieve_falcon(ctx, config):
        return
    # BEFORE the bank check, and its threshold sits one slot above bankAtFreeSlots: otherwise a
    # deferred pile of bones is what trips "we are full", and the trip banks a load of junk
    # instead of dropping it. Once there is no junk left to drop, the fur and meat that actually
    # filled the inventory are what carry it past bankAtFreeSlots, which is the intended trigger.
    if _drop_junk(ctx, config, drop_meat=config.get("dropMeat", False)):
        return
    free = _free_slots()
    if free <= config.get("bankAtFreeSlots", 3):
        if not _has_return_loadout():
            debug_log.log("falconry", "inventory is full but the return trip is not possible -- "
                                      "stopping here rather than stranding the character")
            ctx.state.trigger_safety_stop("falconry: inventory full and return loadout incomplete")
            return
        debug_log.log("falconry", f"{free} slots left -- heading to the bank")
        ctx.state.data["banking"] = True
        return

    hunt_step(ctx, config, drop_meat=config.get("dropMeat", False))


def glove_worn() -> bool:
    """Whether the falconer's glove is in the WEAPON slot -- Matthias hands it over with an active
    falcon and the game clears it once the falcon is gone, so this is a direct read of "do we
    currently have a usable falcon" (ADDED 2026-08-24; an NPC lookup for the Gyr Falcon was tried
    first and rejected, the falcon is legitimately out of view mid-hunt)."""
    equipment = controller.get_equipment() or {}
    weapon = (equipment.get("weapon") or {}).get("name") or ""
    return FALCONER_GLOVE_NAME in weapon.lower()


def hunt_step(ctx, config, drop_meat: bool = False) -> str:
    """ONE bounded hunting action, in priority order, and what it did:
    "retrieved" (a falcon sitting on a kill blocks everything else), "dropped" (the accumulated
    bones, and raw meat when `drop_meat` -- the WHOLE pile in one burst, and only once the
    inventory is crowded, see _drop_junk), "hired" (no glove worn), "threw" / "missed" (a throw
    that landed / did not), "walked" (no kebbit in range, walking to the hunt tile). Shared by
    this routine's own tick and golem_crafting's fur trip, which owns its own end condition.

    "dropped" is therefore now RARE -- roughly one call in twenty rather than one per catch --
    and a caller counting actions should not read it as one item."""
    if _retrieve_falcon(ctx, config):
        return "retrieved"
    # THE LEASH, checked on the PLAYER and after the retrieve (a bird outside the pen is still
    # worth collecting, and walking home first would abandon it -- that abandonment is exactly how
    # a falcon got lost live, see HUNT_LEASH_TILES). Before the throw, so a drift that has already
    # happened is corrected rather than extended by another chase.
    if _walk_back_into_leash(ctx, config):
        return "walked"
    if _drop_junk(ctx, config, drop_meat=drop_meat):
        return "dropped"
    if not glove_worn():
        debug_log.log("falconry", "no falconer's glove equipped -- hiring a falcon from Matthias")
        _hire_falcon(ctx)
        return "hired"
    outcome = _throw_falcon(ctx, config)
    if outcome is None:
        return "walked"
    return "threw" if outcome else "missed"


def hands_free() -> bool:
    """Falconry needs the weapon, shield and glove slots empty (wiki) -- except the falconer's
    glove itself, which the game puts in the weapon slot while a falcon is out."""
    equipment = controller.get_equipment() or {}
    for slot in ("weapon", "shield", "gloves"):
        name = ((equipment.get(slot) or {}).get("name") or "").lower()
        if name and FALCONER_GLOVE_NAME not in name:
            return False
    return True


def _ensure_hands_free(ctx) -> bool:
    """Remove whatever occupies the weapon, shield or glove slot through the worn-equipment
    interface (WidgetProvider, group 387 -- /equipment carries no screen positions). Returns
    True if it acted this tick; the caller re-checks hands_free() next tick. One slot per call."""
    equipment = controller.get_equipment() or {}
    for slot, child in (("weapon", EQUIPMENT_WEAPON_CHILD), ("shield", EQUIPMENT_SHIELD_CHILD),
                        ("gloves", EQUIPMENT_GLOVES_CHILD)):
        name = ((equipment.get(slot) or {}).get("name") or "").lower()
        if not name or FALCONER_GLOVE_NAME in name:
            continue
        widget = controller.get_widget(EQUIPMENT_GROUP, child)
        if not widget.get("clickable"):
            debug_log.log("falconry", f"opening the equipment tab to remove the {slot} ({name})")
            controller.key_tap(ctx.ser, "E")
            controller.human_delay(0.6, 1.0)
            return True
        debug_log.log("falconry", f"removing the {slot} ({name}) -- falconry needs both hands free")
        controller.click_entity(ctx.ser, widget, check_ui_blockers=False, simulate_mistakes=False)
        controller.human_delay(0.6, 1.0)
        return True
    return False


def _walk_back_into_leash(ctx, config) -> bool:
    """Walk back toward TILE_HUNT when the hunt has drifted out of the pen. True if it acted.

    This is the guard that stops the drift COMPOUNDING (see HUNT_LEASH_TILES): the leashed target
    filter keeps us from chasing an out-of-pen kebbit, and this puts us back when we are already
    out -- after a restart mid-drift, or after a kebbit died a long way off. Both halves are
    needed: the filter alone would leave a drifted player standing outside with nothing in reach,
    which reads as "no kebbit in range" forever."""
    leash = config.get("huntLeashTiles", HUNT_LEASH_TILES)
    player = controller.get_player() or {}
    if player.get("x") is None or in_leash(player, leash):
        return False
    gap = tiles_from_hunt(player["x"], player["y"])
    debug_log.log("falconry", f"drifted {gap} tiles from the hunting spot (leash {leash}) at "
                              f"({player.get('x')}, {player.get('y')}) -- walking back")
    travel.walk_to(ctx, TILE_HUNT, goal_adjacent=False)
    return True


def _retrieve_falcon(ctx, config) -> bool:
    """Click the Gyr Falcon to collect it and the kill. Returns True if it acted this tick."""
    # NOT leashed and NOT bounded by maxKebbitDistance (a throw distance, which this never was):
    # a kebbit can run before it dies, so the bird legitimately lands outside the pen and we still
    # want the catch back. FALCON_SEARCH_TILES is from the player.
    falcon = _nearest_npc(FALCON_NAME, FALCON_SEARCH_TILES)
    if falcon is None:
        if _falcon_has_catch():
            # varbit says there is a catch waiting but we cannot see the falcon -- it is out of
            # range, typically because the kebbit ran before dying. Walk back to the hunting spot
            # rather than throwing again (which would silently do nothing).
            #
            # BOUNDED since 2026-09-15: the walk is a no-op when we are ALREADY standing on
            # TILE_HUNT, so this branch spun 54 times over 45s and was broken only by the falcon
            # despawning. Past the cap the falcon is gone for practical purposes -- fall through
            # and let the hire/throw path recover rather than repeat a walk that cannot help.
            tries = ctx.state.data.get("falcon_out_of_range", 0) + 1
            ctx.state.data["falcon_out_of_range"] = tries
            if tries <= FALCON_OUT_OF_RANGE_TRIES:
                debug_log.log("falconry",
                              f"catch waiting but the falcon is out of range "
                              f"({tries}/{FALCON_OUT_OF_RANGE_TRIES}) -- walking back")
                travel.walk_to(ctx, TILE_HUNT, goal_adjacent=False)
                return True
            debug_log.log("falconry", "[!] a catch is waiting but the falcon has been unreachable "
                                      "for too long -- treating it as lost and carrying on")
            ctx.state.data["falcon_out_of_range"] = 0
        return False
    ctx.state.data["falcon_out_of_range"] = 0

    debug_log.log("falconry", f"retrieving the falcon (dist={falcon.get('distance')})")
    controller.human_delay(0.15, 0.4)
    mark = outcome_waits.mark(ctx)
    controller.click_entity(
        ctx.ser, falcon,
        refresh_target=controller.npc_index_refresh_target(falcon.get("index")),
    )

    # Retrieval resolved in ~0.3s in the recording; RETRIEVE_S is generous headroom. The varbit
    # flipping to 0 is pushed as VARBIT_CHANGED and confirmed on /varbit (2026-09-06).
    _await(ctx, {"VARBIT_CHANGED": outcome_waits.varbit_in((VARBIT_FALCON_HAS_CATCH,))}, RETRIEVE_S,
           confirm=lambda: not _falcon_has_catch(), since=mark)
    return True


def junk_names(config, drop_meat: bool) -> tuple:
    """The item names this hunt treats as junk. Pure, so the "never ask for a meat this species
    does not drop" rule is testable on its own -- MEASURED live 2026-09-14 on the Dark kebbit
    (16 retrieves, ZERO raw meat), and with MEAT_NAME None an unguarded name would scan the
    inventory for the substring "none"."""
    names = ()
    if config.get("dropBones", True):
        names += (BONES_NAME,)
    if drop_meat and MEAT_NAME:
        names += (MEAT_NAME,)
    return names


def junk_drop_due(free_slots: int, junk_held: int, config) -> bool:
    """Whether the accumulated junk should go out now. Pure.

    Deliberately NOT "is there any junk" (the pre-2026-09-15 rule, which cost a whole loop
    iteration per bone -- see DROP_AT_FREE_SLOTS): only crowding justifies spending the clicks,
    because junk still in the inventory at the end of a trip is deposited at the bank by a single
    shift-click and so is free."""
    return junk_held > 0 and free_slots <= config.get("dropAtFreeSlots", DROP_AT_FREE_SLOTS)


def _drop_junk(ctx, config, drop_meat: bool = False) -> bool:
    """Burst-drop every accumulated bone (and raw meat when nothing else takes it) once the
    inventory is crowded. Returns True if it acted this tick.

    ONE /inventory read answers both questions -- how full we are, and which slots hold junk --
    because they have to agree with each other: the burst resolves every slot position up front
    and clicks them without re-reading, which is only sound because dropping an item empties its
    slot in place and never shifts the ones after it.

    THE INVENTORY TAB HAS TO BE UP, and that is not a given on this leg. CONFIRMED live
    2026-09-14 (USER: "It doesn't have inventory up so It can't drop"): _ensure_hands_free taps
    "E" to reach the worn-equipment interface on the way in and nothing ever switches back, so
    every shift-click landed on the equipment panel instead of the bone -- MEASURED at 50+
    attempts on one bone before it was stopped by hand. controller.drop_items raises the tab
    itself, which is why the clicking is delegated to it rather than re-implemented here; it also
    holds SHIFT ONCE across the whole burst (a re-tapped SHIFT writes a machine-regular
    down-to-click gap into EVENT_KEYBOARD -- see its own comment) and verifies the FIRST drop, so
    an account with "Shift click to drop items" switched off falls back to the right-click menu
    instead of silently dropping nothing twenty times over."""
    names = junk_names(config, drop_meat)
    if not names:
        return False
    inventory = controller.get_inventory() or []
    junk = [i for i in inventory
            if any(n.lower() in (i.get("name") or "").lower() for n in names)]
    free = INVENTORY_SLOTS - len(inventory)
    if not junk_drop_due(free, len(junk), config):
        return False

    before = len(inventory)
    debug_log.log("falconry",
                  f"{free} slots free -- dropping {len(junk)} {'/'.join(names)} in one pass")
    mark = outcome_waits.mark(ctx)
    controller.drop_items(ctx.ser, junk)
    # drop_items polls for the FIRST drop (that is how it decides whether shift-drop works at
    # all) but returns as soon as the last click is sent, and a drop is processed on the NEXT
    # server tick -- so the last one is still in flight here. Wait for the whole pass to land
    # before anyone reads the inventory again, the same lesson shipwreck_salvaging's disposal
    # pass records ("every pass reported one fewer than it dropped").
    _await(ctx, "INVENTORY_CHANGED", DROP_CONFIRM_S,
           confirm=lambda: len(controller.get_inventory() or []) <= before - len(junk),
           since=mark, reaction=False)
    left = sum(len(_inventory_items(n)) for n in names)
    if left:
        debug_log.log("falconry", f"[!] {left} junk item(s) did not drop -- is the inventory tab "
                                  f"up, and is 'Shift click to drop items' on?")
    return True


def _throw_falcon(ctx, config):
    """True: the throw landed (varbit 14192 -> 5). False: it resolved without a catch (a miss,
    ~40% of throws, or a click that never registered). None: no kebbit in range, walked instead."""
    # LEASHED: only kebbits inside the pen are targets. This species lives on both sides of the
    # fence, and an unleashed "nearest kebbit" walks the hunt out of the enclosure a few tiles per
    # throw -- see HUNT_LEASH_TILES for the two live runs that ended that way.
    kebbit = _nearest_npc(KEBBIT_NAME, config.get("maxKebbitDistance", 12),
                          leash=config.get("huntLeashTiles", HUNT_LEASH_TILES))
    if kebbit is None:
        debug_log.log("falconry", "no kebbit in range -- walking to the hunting spot")
        travel.walk_to(ctx, TILE_HUNT, goal_adjacent=False)
        return None

    debug_log.log("falconry", f"throwing at a kebbit (dist={kebbit.get('distance')})")
    controller.human_delay(0.15, 0.4)
    mark = outcome_waits.mark(ctx)
    if not controller.click_entity(
        ctx.ser, kebbit,
        refresh_target=controller.npc_index_refresh_target(kebbit.get("index")),
    ):
        return False

    # Wait for the throw to RESOLVE, not merely for the click to land. A catch takes ~1.6s and a
    # miss reports at about the same time, so this settles either way; without it the next tick
    # would re-throw into a falcon already in flight. The varbit is pushed (2026-09-06), so a
    # catch is seen the tick it lands; a miss still costs the full THROW_RESOLVE_S.
    landed = bool(_await(ctx, {"VARBIT_CHANGED": outcome_waits.varbit_in((VARBIT_FALCON_HAS_CATCH,))},
                         THROW_RESOLVE_S, confirm=_falcon_has_catch, since=mark))

    if landed:
        ctx.state.data["failed_throws"] = 0
        return True

    # A miss is normal (~40% of throws) and indistinguishable here from a click that never
    # registered at all -- including a deliberate humanisation misclick, which produces no throw.
    # Counted rather than acted on immediately, so only a sustained run of nothing triggers the
    # "do we even have a falcon" check below.
    failed = ctx.state.data.get("failed_throws", 0) + 1
    ctx.state.data["failed_throws"] = failed
    if failed >= 6:
        debug_log.log("falconry", f"{failed} throws with no result -- checking for a falcon")
        ctx.state.data["failed_throws"] = 0
        _hire_falcon(ctx)
    return False


def _hire_falcon(ctx) -> bool:
    """Get a falcon from Matthias (500gp).

    Quick-falcon does the whole transaction on its own -- in the recording the chat line and the
    -500 Coins both landed 0.2s after that single click, with the preceding Talk-to contributing
    nothing but dialogue. So this never opens the conversation."""
    coins = _first_inventory_item("coins")
    held = coins.get("qty", 0) if coins else 0
    if held < DEFAULT_CONFIG["minCoins"]:
        debug_log.log("falconry", f"only {held} coins -- cannot hire a falcon")
        ctx.state.trigger_safety_stop("falconry: out of coins for Quick-falcon")
        return False

    matthias = _nearest_npc(FALCONER_NAME, 20)
    if matthias is None:
        debug_log.log("falconry", "walking to the falconer")
        travel.walk_to(ctx, TILE_FALCONER, goal_adjacent=False)
        return False

    debug_log.log("falconry", "hiring a falcon from Matthias")
    refresh = controller.npc_index_refresh_target(matthias["index"])
    mark = outcome_waits.mark(ctx)
    if not controller.click_menu_action(ctx.ser, matthias, OPT_QUICK_FALCON, refresh_target=refresh):
        return False

    # The -500 coins (INVENTORY_CHANGED) and the glove landed 0.2s after the click in the
    # recording; the glove in the weapon slot is the truth (2026-09-06, was a 1.5-2.2s sleep).
    hired = _await(ctx, "INVENTORY_CHANGED", HIRE_S, confirm=glove_worn, since=mark)
    if not hired:
        debug_log.log("falconry", "Quick-falcon did not put a glove on within the budget -- will re-check next tick")
    return bool(hired)


def _wait_until(predicate, timeout_s: float, poll_interval_s: float = 0.1) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll_interval_s)
    return False


# --------------------------------------------------------------------------- the bank trip
#
# Dispatched on the CURRENT REGION rather than a stored step counter. ctx.state.data is wiped on
# every restart while the character's position is not (see CLAUDE.md), so a step counter desyncs
# from reality exactly when it matters most -- mid-trip. Region is the one piece of state that
# cannot lie.

def _bank_trip(ctx, config, region):
    if region in FALCONRY_REGIONS:
        # CONFIRMED live 2026-08-24: climbing the stile flips the reported region to
        # REGION_FALCONRY before _leg_walk_home's own "south of the fence line" check
        # (player.y < 3615) goes true and clears "banking" -- so a plain region dispatch here
        # teleported the character away again (_leg_depart) immediately after crossing the
        # stile, having never actually finished the walk home. Reusing the exact same y
        # threshold _leg_walk_home already uses tells the two cases apart: still north of it
        # means the return walk is not actually done yet (route back into _leg_walk_home, which
        # clears "banking" itself once travel.walk_to(TILE_HUNT) reports arrival); at or south of
        # it means we are genuinely back at the hunting ground and it is safe to depart again.
        player = controller.get_player()
        if player.get("y", 0) >= SOUTH_OF_STILE_Y:
            _leg_walk_home(ctx)
        else:
            _leg_depart(ctx, config)
    elif region == REGION_CASTLE_WARS:
        _leg_bank(ctx, config)
    elif region in (REGION_MONASTERY, REGION_FAIRY_RING):
        _leg_fairy_ring(ctx)
    elif region == REGION_FAIRY_LANDING:
        _leg_walk_home(ctx)
    else:
        debug_log.log("falconry", f"in an unexpected region ({region}) mid-trip -- stopping "
                                  f"rather than guessing a route")
        ctx.state.trigger_safety_stop(f"falconry: unexpected region {region} during the bank trip")


def _leg_depart(ctx, config):
    _use_teleport(ctx, RING_OF_DUELING, OPT_CASTLE_WARS, left_click=True)


def _leg_bank(ctx, config):
    bank_obj = banker.find_nearby_bank(config)
    if bank_obj is None:
        debug_log.log("falconry", "walking to the bank chest")
        travel.walk_to(ctx, TILE_BANK, goal_adjacent=False)
        return

    if not banker.is_open():
        if not banker.open_bank(ctx, bank_obj):
            return

    # Order matters and mirrors the recording exactly: empty the pouches into the bank FIRST, then
    # deposit the spillover still loose in the inventory. Reversed, the spillover would be banked
    # and then the pouches would empty into a fresh inventory instead of the bank.
    debug_log.log("falconry", "emptying fur/meat pouches into the bank")
    banker.deposit_all_containers(ctx)
    controller.human_delay(0.6, 1.0)

    for name in [n for n in (FUR_NAME, MEAT_NAME) if n]:
        item = _first_inventory_item(name)
        if item is None:
            continue
        debug_log.log("falconry", f"depositing loose {name}")
        # Shift-click on an inventory item WITH THE BANK OPEN is Deposit-All -- the same single
        # gesture the recorded human used (Deposit-All on widget 15.3, once per item type). Same
        # physical action as the bone drop, entirely different meaning by context.
        _shift_click(ctx, item)
        controller.human_delay(0.4, 0.8)

    _restock_ring_of_dueling(ctx)

    banker.close_bank(ctx)
    controller.human_delay(0.4, 0.8)
    _use_teleport(ctx, ARDOUGNE_CLOAK, OPT_KANDARIN_MONASTERY)


def _restock_ring_of_dueling(ctx) -> None:
    """Withdraw a fresh ring of dueling if the last one crumbled.

    A ring of dueling vanishes from the inventory when its final charge is spent, so its ABSENCE
    is the signal -- there is no charge counter to read. The chat line ("Your ring of dueling has
    four uses left.") is informational only and stops appearing once the ring is gone."""
    if _first_inventory_item(RING_OF_DUELING) is not None:
        return

    debug_log.log("falconry", "ring of dueling used up -- withdrawing a replacement")
    if not banker.search_and_withdraw(ctx, "Ring of dueling", 1, contains=True):
        debug_log.log("falconry", "no ring of dueling in the bank")
        ctx.state.trigger_safety_stop("falconry: out of rings of dueling")


def _leg_fairy_ring(ctx, code=FAIRY_CODE_FALCONRY, expect=FAIRY_EXPECT_FALCONRY):
    """Take the Piscatoris fairy ring. One bounded action per call, leg-style.

    THE MECHANIC MOVED TO travel.hop_fairy_ring 2026-09-16 (USER: "The fairy ring implementation
    we made for golem should probably be implemented as a generic function for fairy hopping
    inside travel.py"). Nothing about dialling was ever falconry's -- the dial table, the
    stale-varbit retry and the Confirm-button guard are identical at every ring in the game, and
    golem_crafting's fur trip was reaching into this PRIVATE function to get at them.

    What stays here is the only falconry-specific part: which ring, which code, and where it
    lands. No Dramen/Lunar staff anywhere -- fairy rings stopped needing one (USER 2026-09-06).
    """
    return travel.hop_fairy_ring(
        ctx, code, expect,
        ring_tile=TILE_FAIRY_RING,
        expect_region=REGION_FAIRY_LANDING,
        log="falconry",
    )

def is_isolated(x, y, plane) -> bool:
    """Whether the static collision map thinks this tile connects to nothing at all.

    CONFIRMED live 2026-09-14: the fairy ring drops the player ON the ring, and the ring object's
    own blocking is baked into the same bits a wall's is (see CLAUDE.md -- the static map cannot
    tell furniture from a wall). `reachable_from` at the Piscatoris landing tile (2319, 3619)
    returns exactly ONE tile, so travel.walk_to could not route a single step and the trip sat
    there. The east/north/south neighbours are all in the same 738-tile component, which reaches
    the stile in 55 steps -- so the only thing needed is one step OFF the ring."""
    return len(worldmap.reachable_from(x, y, plane, max_nodes=ISOLATED_PROBE_NODES)) <= 1


def _step_off_isolated_tile(ctx) -> bool:
    """One shift-click onto a cardinal neighbour that is in a real component. Returns True if it
    acted. A routed walk cannot do this: routing is exactly what is unavailable while standing on
    a tile the map believes is sealed."""
    player = controller.get_player() or {}
    x, y = player.get("x"), player.get("y")
    plane = player.get("plane", 0)
    if x is None or not is_isolated(x, y, plane):
        return False
    for dx, dy in ((1, 0), (0, 1), (0, -1), (-1, 0)):
        if not is_isolated(x + dx, y + dy, plane):
            debug_log.log("falconry", f"standing on a tile the map calls sealed ({x}, {y}) -- "
                                      f"stepping off to ({x + dx}, {y + dy})")
            travel.walk_to_exact_tile(ctx, x + dx, y + dy, plane)
            return True
    debug_log.log("falconry", f"[!] ({x}, {y}) is sealed and so is every neighbour")
    return False


def _leg_walk_home(ctx):
    # The fairy ring lands ON the ring, which the map calls sealed -- step off before asking
    # anything else, because every question below is a routing question.
    if _step_off_isolated_tile(ctx):
        return

    player = controller.get_player() or {}
    x, y, plane = player.get("x"), player.get("y"), player.get("plane", 0)
    # CAN WE ACTUALLY GET THERE, rather than "does our y look southern". CORRECTED 2026-09-14
    # (live): this used to branch on `y < TILE_STILE["y"]` (3621) as a proxy for "already across
    # the stile" -- which is true just after climbing, and FALSE at the fairy ring, which lands at
    # y 3619 more than fifty tiles WEST of the fence. The trip therefore walked straight at the
    # hunt tile from the wrong side and stuck: MEASURED, the ring-side component is 738 tiles, the
    # hunting enclosure 389, and the two are disjoint -- the stile is the only way in and its own
    # tile is in neither.
    #
    # Asking the map whether the hunt tile is routable answers the real question, and it also
    # subsumes the 2026-08-24 climb-back-and-forth bug this y-check was written to fix: once
    # across, the hunt tile IS reachable, so the stile is never touched again. (SOUTH_OF_STILE_Y
    # still serves _bank_trip's own coarser depart-vs-walking-home decision.)
    if x is not None and worldmap.find_path(x, y, plane, TILE_HUNT["x"], TILE_HUNT["y"]):
        if travel.walk_to(ctx, TILE_HUNT, goal_adjacent=False):
            debug_log.log("falconry", "back at the hunting ground")
            ctx.state.data["banking"] = False
        return

    # FOUND BY ID, NOT AT A HARDCODED TILE. CONFIRMED live 2026-09-14: this looked the stile up
    # with get_object_at(TILE_STILE...), but TILE_STILE (2370, 3621) is the tile the player STANDS
    # on and the stile object itself is at (2371, 3621) -- and get_object_at matches the object's
    # own exact tile. It therefore never found it: the character walked to the stile, arrived, and
    # logged "walking to the stile" once a tick for a minute and a half, going nowhere.
    #
    # A one-tile error in a hardcoded constant should not be able to wedge a leg, so the stile is
    # now located the same way the fairy ring already is -- scan for its id nearby -- and
    # TILE_STILE goes back to being only what its name says: where to walk to. refresh_target
    # still tracks the object's own live tile so the click stays aimed while the camera drifts.
    stile = next((o for o in controller.get_objects(radius=STILE_SCAN_RADIUS)
                  if o.get("id") == STILE_ID and o.get("visible", True)), None)
    if stile is None:
        debug_log.log("falconry", "walking to the stile")
        travel.walk_to(ctx, TILE_STILE, goal_adjacent=False)
        return

    debug_log.log("falconry", f"climbing over the stile at ({stile.get('x')}, {stile.get('y')})")
    refresh_target = controller.object_refresh_target(
        stile.get("x"), stile.get("y"), stile.get("plane", 0), STILE_ID, radius=STILE_SCAN_RADIUS)
    if controller.click_entity(ctx.ser, stile, refresh_target=refresh_target):
        controller.human_delay(2.0, 3.0)

