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

import time

import banker
import controller
import debug_log
import travel

# --------------------------------------------------------------------------- ids and waypoints

KEBBIT_NAME = "Dashing kebbit"
FALCON_NAME = "Gyr Falcon"
FALCONER_NAME = "Matthias"
# Worn in the WEAPON slot (see _leg_fairy_ring's/_unequip_dramen_staff's own comments -- CONFIRMED
# live, not a hands slot) for as long as an active falcon is out. No "wear" code exists anywhere
# in this file because none is needed: the game puts it there automatically once Matthias grants a
# falcon and clears it automatically once the falcon is gone, so checking for it is a direct read
# of "do we currently have a usable falcon", not something this routine has to maintain itself.
FALCONER_GLOVE_NAME = "falconer's glove"

BONES_NAME = "Bones"
FUR_NAME = "Dashing kebbit fur"
MEAT_NAME = "Raw dashing kebbit"

RING_OF_DUELING = "ring of dueling"
ARDOUGNE_CLOAK = "ardougne cloak"
DRAMEN_STAFF = "dramen staff"

# varbit 14192: 5 = the falcon is sitting on a caught kebbit and must be retrieved, 0 = it is with
# us and can be thrown. Established by frequency and pairing, not by guessing at its meaning: it
# changed exactly twice per successful catch across both recordings (54 changes / 27 catches, and
# 14 / 7), always 5 on the catch and 0 on the retrieve, and never moved on a miss.
VARBIT_FALCON_HAS_CATCH = 14192
FALCON_CATCH_WAITING = 5

# Region ids, in the order the recorded trip visited them.
REGION_FALCONRY = 9528
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
FAIRY_RING_ID = 29495
STILE_ID = 19222

# Used ONLY by _bank_trip's own REGION_FALCONRY dispatch (crossing the stile flips the reported
# region before the walk home is actually finished -- see that function's own comment) to tell
# "still finishing the walk home" apart from "genuinely back at the hunting ground, safe to
# depart again". Deliberately NOT used by _leg_walk_home's own stile-avoidance check, which needs
# a tighter bound (TILE_STILE's own y) -- CONFIRMED live 2026-08-24 that this 6-tile-wide buffer
# is far more than crossing the stile actually moves the player, so using it there let the
# routine climb back and forth over the same stile forever (see that function's own comment).
SOUTH_OF_STILE_Y = 3615

# Menu options, exactly as they appeared in the recording's menuAction/option fields.
OPT_CASTLE_WARS = "Castle Wars"
OPT_KANDARIN_MONASTERY = "Monastery Teleport"
OPT_LAST_DESTINATION = "Last-destination"
OPT_QUICK_FALCON = "Quick-falcon"

DEFAULT_CONFIG = {
    "enabled": True,

    # Bank when this few inventory slots remain. 3 leaves room for the fur+meat+bone of a catch
    # that lands while we are already walking away.
    "bankAtFreeSlots": 3,

    "dropBones": True,
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
    ctx.state.data["banking"] = region != REGION_FALCONRY
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


def _nearest_npc(name: str, max_distance: int):
    best = None
    for npc in controller.get_npcs():
        if (npc.get("name") or "").lower() != name.lower():
            continue
        distance = npc.get("distance", 9999)
        if distance < 0 or distance > max_distance:
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


def _use_teleport(ctx, item_substring: str, option: str, left_click: bool = False) -> bool:
    """Use a teleport item in the INVENTORY to reach `option`'s destination.

    Inventory, not worn: the recorded human used the worn-equipment interface, but a right-click
    menu read back through GET /menu (controller.click_menu_action) is the pattern this codebase
    already has working, and it needs a clickable screen position -- which /equipment does not
    provide for worn items. Keeping the teleports in the inventory avoids the whole problem.
    See the module docstring's loadout note.

    left_click, if True, skips the right-click + GET /menu selection entirely and just left-clicks
    the item -- ADDED 2026-08-24 (user directive) for the ring of dueling's Castle Wars hop: the
    user has Castle Wars set as that ring's own left-click default action (client-side menu-entry
    swap, same idea as routines/chinchompa_hunting.py's swapped trap options -- see CLAUDE.md),
    so drilling into the right-click menu is unnecessary there. `option` is still passed for the
    debug log message even on this path; it plays no role in what actually gets clicked. Default
    False (unchanged right-click-menu behavior) for every other teleport until confirmed the same
    way -- see _leg_bank's ardougne cloak call, which is NOT confirmed to have this swap."""
    item = _first_inventory_item(item_substring)
    if item is None:
        debug_log.log("falconry", f"no {item_substring} in inventory -- cannot teleport")
        return False

    debug_log.log("falconry", f"teleporting: {option}")
    if left_click:
        controller.human_delay(0.05, 0.12)
        ok = controller.click_entity(ctx.ser, item, check_ui_blockers=False, simulate_mistakes=False)
    else:
        ok = controller.click_menu_action(ctx.ser, item, option, check_ui_blockers=False)
    if not ok:
        return False

    controller.human_delay(3.0, 3.6)  # teleport animation + region load
    return True


def _has_return_loadout() -> bool:
    """Everything needed to get BACK from the bank. Checked before leaving the hunting area, not
    at the bank -- being short of a Dramen staff at Castle Wars strands the character, whereas
    noticing here just means we keep hunting."""
    missing = [name for name in (RING_OF_DUELING, ARDOUGNE_CLOAK, DRAMEN_STAFF)
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
    if region != REGION_FALCONRY:
        # Not where we think we are -- most likely a restart or a stray teleport. Route through
        # the trip logic, which knows how to get home from each leg.
        debug_log.log("falconry", f"not in the hunting region (in {region}) -- routing home")
        ctx.state.data["banking"] = True
        return

    # 1. A falcon sitting on a kill blocks everything else -- it cannot be thrown again until
    #    retrieved, so this is unconditionally first.
    if _retrieve_falcon(ctx, config):
        return

    # 2. Bones only ever cost us inventory space.
    if config.get("dropBones", True) and _drop_bones(ctx):
        return

    # 3. Full enough to be worth a trip?
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

    # 4. Make sure we actually have an active falcon before trying to hunt with it -- ADDED
    #    2026-08-24 (user directive, CORRECTED same day: an NPC lookup for the Gyr Falcon was
    #    tried first and rejected -- the falcon can be legitimately out of view mid-hunt, e.g.
    #    off attacking a kebbit, making that check unreliable). The Falconer's glove is what
    #    Matthias hands over with an active falcon (see FALCONER_GLOVE_NAME's own comment) and
    #    disappears again once it is gone, so equipment is the direct, unambiguous signal here --
    #    proactive rather than _throw_falcon's reactive "N failed throws" counter, which only
    #    notices a missing falcon after several wasted throw attempts against nothing.
    equipment = controller.get_equipment()
    weapon = (equipment.get("weapon") or {}).get("name") or ""
    if FALCONER_GLOVE_NAME not in weapon.lower():
        debug_log.log("falconry", "no falconer's glove equipped -- hiring a falcon from Matthias")
        _hire_falcon(ctx)
        return

    # 5. Throw at a kebbit.
    _throw_falcon(ctx, config)


def _retrieve_falcon(ctx, config) -> bool:
    """Click the Gyr Falcon to collect it and the kill. Returns True if it acted this tick."""
    falcon = _nearest_npc(FALCON_NAME, config.get("maxKebbitDistance", 12))
    if falcon is None:
        if _falcon_has_catch():
            # varbit says there is a catch waiting but we cannot see the falcon -- it is out of
            # range, typically because the kebbit ran before dying. Walk back to the hunting spot
            # rather than throwing again (which would silently do nothing).
            debug_log.log("falconry", "catch waiting but the falcon is out of range -- walking back")
            travel.walk_to(ctx, TILE_HUNT)
            return True
        return False

    debug_log.log("falconry", f"retrieving the falcon (dist={falcon.get('distance')})")
    controller.human_delay(0.15, 0.4)
    controller.click_entity(
        ctx.ser, falcon,
        refresh_target=controller.npc_index_refresh_target(falcon.get("index")),
    )

    # Retrieval resolved in ~0.3s in the recording; 2.5s is generous headroom. Waiting on the
    # varbit rather than a fixed sleep means a slow tick costs nothing and a fast one is not
    # padded.
    _wait_until(lambda: not _falcon_has_catch(), timeout_s=2.5)
    return True


def _drop_bones(ctx) -> bool:
    bones = _inventory_items(BONES_NAME)
    if not bones:
        return False

    # One per tick rather than a burst: the recorded human dropped at roughly 0.6-0.9s intervals,
    # and a tight loop of 11 drops is both unlike a person and a long window in which a falcon
    # could land unattended.
    debug_log.log("falconry", f"dropping {BONES_NAME} ({len(bones)} held)")
    _shift_click(ctx, bones[0])
    controller.human_delay(0.3, 0.7)
    return True


def _throw_falcon(ctx, config) -> bool:
    kebbit = _nearest_npc(KEBBIT_NAME, config.get("maxKebbitDistance", 12))
    if kebbit is None:
        debug_log.log("falconry", "no kebbit in range -- walking to the hunting spot")
        travel.walk_to(ctx, TILE_HUNT)
        return False

    debug_log.log("falconry", f"throwing at a kebbit (dist={kebbit.get('distance')})")
    controller.human_delay(0.15, 0.4)
    if not controller.click_entity(
        ctx.ser, kebbit,
        refresh_target=controller.npc_index_refresh_target(kebbit.get("index")),
    ):
        return False

    # Wait for the throw to RESOLVE, not merely for the click to land. A catch takes ~1.6s and a
    # miss reports at about the same time, so this settles either way; without it the next tick
    # would re-throw into a falcon already in flight.
    landed = _wait_until(_falcon_has_catch, timeout_s=2.6)

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
        travel.walk_to(ctx, TILE_FALCONER)
        return False

    debug_log.log("falconry", "hiring a falcon from Matthias")
    if not controller.click_menu_action(ctx.ser, matthias, OPT_QUICK_FALCON):
        return False

    controller.human_delay(1.5, 2.2)
    return True


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
    if region == REGION_FALCONRY:
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
        travel.walk_to(ctx, TILE_BANK)
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

    for name in (FUR_NAME, MEAT_NAME):
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


def _leg_fairy_ring(ctx):
    # The Dramen staff must be WIELDED for the fairy ring. It is removed again after the hop
    # because the falconry glove occupies the same weapon slot.
    equipment = controller.get_equipment()
    weapon = (equipment.get("weapon") or {}).get("name") or ""
    if DRAMEN_STAFF not in weapon.lower():
        staff = _first_inventory_item(DRAMEN_STAFF)
        if staff is None:
            debug_log.log("falconry", "no Dramen staff -- cannot use the fairy ring")
            ctx.state.trigger_safety_stop("falconry: no Dramen staff for the fairy ring")
            return
        debug_log.log("falconry", "wielding the Dramen staff")
        controller.click_entity(ctx.ser, staff, check_ui_blockers=False, simulate_mistakes=False)
        controller.human_delay(0.8, 1.3)
        return

    ring = next((o for o in controller.get_objects(radius=20)
                 if o.get("id") == FAIRY_RING_ID and o.get("visible", True)), None)
    if ring is None:
        debug_log.log("falconry", "walking to the fairy ring")
        travel.walk_to(ctx, TILE_FAIRY_RING)
        return

    # Last-destination reuses the previously-dialled code (AKQ in the recording) in one click,
    # with no interface to drive -- which is why the human used it and why this does too.
    debug_log.log("falconry", "taking the fairy ring to the last destination (AKQ)")
    if controller.click_menu_action(ctx.ser, ring, OPT_LAST_DESTINATION):
        controller.human_delay(3.0, 4.0)


def _leg_walk_home(ctx):
    # Glove needs the weapon slot, so the staff comes off before we reach Matthias.
    if _unequip_dramen_staff(ctx):
        return

    player = controller.get_player()
    if player.get("y", 0) < TILE_STILE["y"]:
        # CONFIRMED live 2026-08-24: this used to compare against SOUTH_OF_STILE_Y (3615) here
        # too, a 6-tile-wide buffer below the stile's own position (3621). Climbing only moves
        # the player a short distance to the other side -- nowhere near enough to clear that
        # buffer -- so on the very next tick the stile was STILL found within radius=15 (visible
        # from the south side too) and got clicked again, climbing straight back north. That
        # repeated forever: climb south, re-click, climb north... except the outer dispatch
        # (_bank_trip) kept routing back here since y never dropped below 3615 either way.
        # Comparing against the stile's own y instead means this branch -- which never touches
        # the stile object again -- is taken on the very first tick after crossing, before there
        # is ever another chance to re-click it. SOUTH_OF_STILE_Y stays as-is for _bank_trip's
        # own (coarser, and unaffected by this bug) depart-vs-still-walking-home decision.
        if travel.walk_to(ctx, TILE_HUNT):
            debug_log.log("falconry", "back at the hunting ground")
            ctx.state.data["banking"] = False
        return

    # get_object_at (single-entity, uncached), not the bulk get_objects scan -- CORRECTED
    # 2026-08-24 (user question) to the preferred accessor for a fixed-position object already
    # known by world tile + id, same idiom as golem_crafting.py's plinth/object clicks. Also
    # carries refresh_target so the click stays aimed at the stile's live screen position even if
    # the camera drifts between resolving it and the click landing.
    stile = controller.get_object_at(TILE_STILE["x"], TILE_STILE["y"], TILE_STILE["plane"],
                                      STILE_ID, radius=15)
    if stile is None:
        debug_log.log("falconry", "walking to the stile")
        travel.walk_to(ctx, TILE_STILE)
        return

    debug_log.log("falconry", "climbing over the stile")
    refresh_target = controller.object_refresh_target(
        TILE_STILE["x"], TILE_STILE["y"], TILE_STILE["plane"], STILE_ID, radius=15)
    if controller.click_entity(ctx.ser, stile, refresh_target=refresh_target):
        controller.human_delay(2.0, 3.0)


def _unequip_dramen_staff(ctx) -> bool:
    """Take the Dramen staff off via the equipment interface. Returns True if it acted this tick.

    Worn items have no screen position on /equipment, so this goes through WidgetProvider: group
    387 is the worn-equipment interface and child 18 is the weapon slot, both read directly out of
    the recording (recorder.py decodes the packed id into group.child for exactly this). `E` opens
    the equipment tab."""
    equipment = controller.get_equipment()
    weapon = (equipment.get("weapon") or {}).get("name") or ""
    if DRAMEN_STAFF not in weapon.lower():
        return False

    slot = controller.get_widget(387, 18)
    if not slot.get("clickable"):
        debug_log.log("falconry", "opening the equipment tab to remove the Dramen staff")
        controller.key_tap(ctx.ser, "E")
        controller.human_delay(0.6, 1.0)
        return True

    debug_log.log("falconry", "removing the Dramen staff")
    controller.click_entity(ctx.ser, slot, check_ui_blockers=False, simulate_mistakes=False)
    controller.human_delay(0.6, 1.0)
    return True
