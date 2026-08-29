# banker.py
#
# Reusable banking helper. Routines call into this to empty loot and
# restock food/potions instead of each reimplementing bank interaction.
# Lives alongside controller.py (not in routines/) since it's shared
# infrastructure, the same reason `import controller` already works from
# any routine file.
#
# Config shape (a plain dict -- routines can build this however they like,
# e.g. straight from their own DEFAULT_CONFIG or a bridge /config section):
#
#   {
#       "enabled": True,
#       "bankNames": ["Bank booth", "Bank chest", "Bank counter"],
#       "maxBankDistance": 40,
#       "keepItems": ["ring of dueling"],        # never deposited; substring match
#       "withdraw": [
#           {"name": "Shark", "quantity": 10},
#           {"name": "Prayer potion", "quantity": 4},
#       ],
#   }
#
# A bank within walking distance is used directly -- clicking a bank object
# triggers the game's own pathfinding the same way clicking a distant rock
# does in routines/mining.py, so no manual navigation is needed for that
# case. If nothing's in range, bank_up falls back to travel.py: ring of
# dueling -> Ferox Enclave, bank + fountain there, then walk back.
#
# withdraw_loadout sets the bank's quantity mode to "1" itself (via the
# quantityButtons the bridge exposes) before withdrawing, so it no longer
# matters what mode you last left the bank in.

import re
import time

import controller
import debug_log
import travel

DEFAULT_BANK_NAMES = ("bank booth", "bank chest", "bank counter", "grand exchange booth")
FEROX_BANK_NAMES = ("bank chest", "bank booth")
FEROX_MAX_BANK_DISTANCE = 20

# Past this many tiles open_bank walks toward the bank before clicking it -- see the
# comment at that branch. Deliberately generous: a normal "arrived at the bank" position
# is a handful of tiles out, and this must never add a walk to a trip that already works.
BANK_APPROACH_TILES = 8


def find_nearby_bank(config: dict):
    """Return the closest matching bank object within range, or None."""
    names = {n.lower() for n in config.get("bankNames", DEFAULT_BANK_NAMES)}
    max_dist = config.get("maxBankDistance", 40)

    candidates = []
    # radius=max_dist -- CONFIRMED live 2026-08-06: this was an unbounded get_objects() call
    # (full scene walk) filtered down to max_dist *afterward*, in an object-dense area (Ferox
    # Enclave: 8532 objects) that routinely took longer than api_get's default 0.3s timeout,
    # especially right after a client (re)start. A failed/timed-out call fails-safe to an empty
    # list, indistinguishable from "genuinely nothing nearby" -- see _find_nearby_bank_retrying's
    # own docstring for why that ambiguity specifically matters here (a caller uses a None result
    # to decide whether to spend a limited resource, a ring of dueling charge, on a teleport).
    # Bounding the walk up front is the real fix; the retries there are a second line of defense
    # for whatever's still slow.
    for obj in controller.get_objects(radius=max_dist):
        name = (obj.get("name") or "").lower()
        if name not in names:
            continue
        if not obj.get("visible", True):
            continue

        dist = obj.get("distance", 9999)
        if dist < 0 or dist > max_dist:
            continue

        candidates.append(obj)

    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def _find_nearby_bank_retrying(config: dict, attempts: int = 4, delay: float = 0.4):
    """find_nearby_bank, retried a few times before giving up -- a single miss can just mean
    get_objects()'s underlying /objects call timed out this particular tick (it fails-safe to an
    empty list, indistinguishable from "genuinely nothing nearby"), which happens far more often
    right after a client (re)start while the scene is still loading. See _bank_via_ferox's own
    comment for why this matters enough to bother: a caller there uses a None result here to
    decide whether to spend a limited resource (a ring of dueling charge) on a teleport."""
    for i in range(attempts):
        bank_obj = find_nearby_bank(config)
        if bank_obj is not None:
            return bank_obj
        if i < attempts - 1:
            time.sleep(delay)
    return None


def is_open() -> bool:
    return controller.get_bank().get("open", False)


def _refresh_bank_object(bank_obj: dict) -> dict:
    """Re-find `bank_obj` (matched by id) among currently visible objects, so a retry click
    aims at its actual current screen position instead of a position captured once and now
    stale -- see open_bank's docstring for why this matters. Falls back to the original
    `bank_obj` unchanged if a matching id isn't found (e.g. a transient read miss); a stale
    position is still better than no click attempt at all."""
    target_id = bank_obj.get("id")
    for obj in controller.get_objects(radius=15):
        if obj.get("id") == target_id and obj.get("visible", True):
            return obj
    return bank_obj


def open_bank(ctx, bank_obj, timeout_s: float = 20.0) -> bool:
    """Walk to and open a bank object. Returns once the interface is up.

    CONFIRMED live 2026-08-12 (Mastering Mixology live session): a single click here can
    silently fail to open the bank -- the click itself "lands" (click_entity returns True) but
    the interface never comes up. Two distinct causes identified live, both needing the same
    shape of fix:

    1. The player wasn't standing right at the bank (having just walked off toward the
       refiner/hopper), so OSRS walks there first, then opens the bank -- a too-short reclick
       interval doesn't give that walk time to finish and instead re-issues a fresh
       walk-to-then-interact command mid-transit, redirecting the walk instead of letting the
       original click resolve.
    2. The bank's aim point can be behind a UI panel -- click_entity's own
       rotate_camera_away_from_ui already handles this by nudging the camera and returning
       False, with its docstring explicitly saying the CALLER should "re-resolve the target's
       (now-shifted) screen position on its next attempt." The original version of this
       function didn't honor that contract at all: its very first click
       (`if not click_entity(...): return False`) returned a hard failure immediately on a
       UI-panel block, never reaching any retry loop, and even after a retry loop was added it
       kept re-clicking the SAME stale `bank_obj` screen position captured before any camera
       rotation -- which can never land once the camera has actually moved.

    CORRECTED twice more the same session (user reports + insight): a fixed reclick interval
    was still just a guess, and the follow-up fix (gating on camera yaw/pitch settling) turned
    out not to be the right signal either -- this minigame's camera retargets almost every tick
    as the player turns to face its next interaction, so yaw==yawTarget held on nearly every
    poll even while the net on-screen position kept drifting (user report: "still clicks like
    crazy even when camera moving"). Per explicit user direction, this now gates on the
    player's own activity state instead (see controller.wait_until_player_idle: local position
    stable across consecutive reads, narrowed 2026-08-13 from an earlier animation+pose+position
    snapshot -- see that function's docstring) -- the direct signal for "is a screen position
    still safe to click", not a screen/camera-derived proxy for it.

    Fixed by waiting for the player to be idle before every click, including the first, then
    re-resolving the bank object's current position (see _refresh_bank_object) right before
    clicking -- so a UI-panel nudge's "try again next attempt" and an in-progress walk's
    changing position are both naturally covered by the loop itself."""
    if is_open():
        return True

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        controller.wait_until_player_idle(timeout_s=max(1.0, deadline - time.time()))
        fresh = _refresh_bank_object(bank_obj)

        # CONFIRMED live 2026-08-12 (user report: "not doing anything" -> safety-stopped on
        # "failed to open the bank"): the object's own `visible` flag can read True while
        # `onScreen` reads False (game-state-visible vs. actually-in-viewport are different
        # things) -- clicking it then aims at a screenX/Y outside the RuneLite window every
        # time, which click_entity correctly refuses, so the loop just burned its whole timeout
        # reclicking the same unreachable spot. Per user's own suggested fix: click the minimap
        # toward the bank's world position first so the camera/player turns it into view, then
        # let the loop's next iteration re-resolve and click it normally.
        off_screen = not fresh.get("onScreen", True)
        # CONFIRMED live 2026-08-28 (mahogany_homes, safety-stopped on "failed to open the
        # bank"): distance is the OTHER way this loop can aim somewhere unusable, and unlike
        # off-screen it looks perfectly fine on the wire. find_nearby_bank accepts a booth up to
        # maxBankDistance (40) away; standing inside Mariah's house in Hosidius, the Hosidius
        # booth 21 tiles away THROUGH A WALL still reported onScreen -- projection has no notion
        # of occlusion -- and projected onto the chatbox tab strip along the bottom of the
        # viewport, so every click resolved to "Switch tab" on widget 162.19 instead of the bank.
        # Nothing self-corrected either: _refresh_bank_object only re-resolves within 15 tiles,
        # so all five retries re-aimed at the identical stale pixel. Walk into range first and
        # the projection lands in open viewport where a click means what it says.
        too_far = fresh.get("distance", 0) > BANK_APPROACH_TILES
        if off_screen or too_far:
            x, y, plane = fresh.get("x"), fresh.get("y"), fresh.get("plane", 0)
            if x is not None and y is not None:
                point = controller.get_minimap_walk_point(x, y, plane)
                if point.get("onMinimap"):
                    why = "off-screen" if off_screen else f"{fresh.get('distance')} tiles away"
                    debug_log.log("bank", f"bank object {why} -- minimap-walking toward it first")
                    controller.click_entity(ctx.ser, point, offset=(0, 0))
                    controller.wait_until_player_idle(timeout_s=max(1.0, deadline - time.time()))
                    continue
            if off_screen:
                # An off-screen aim point is outside the RuneLite window, so click_entity would
                # refuse it anyway -- spending the timeout beats a guaranteed miss. A merely
                # DISTANT bank is still clickable (the game walks there itself), so fall through
                # to the click rather than deadlocking on a minimap that cannot reach ~28 tiles.
                continue

        controller.click_entity(ctx.ser, fresh)

        # Give the click a few seconds to resolve (open animation/server round trip) without
        # reclicking yet -- reclicking here would just interrupt a click that's about to work.
        click_deadline = min(deadline, time.time() + 3.0)
        while time.time() < click_deadline:
            if is_open():
                return True
            time.sleep(0.3)
    return is_open()


def close_bank(ctx) -> None:
    # Escape closes the bank interface like any other RuneScape interface.
    # Adjust this if the Arduino firmware expects a different key string.
    controller.key_tap(ctx.ser, "ESC")


LOOTING_BAG_NAME = "looting bag"

# Loot picked up while the bag is open routes straight into it ahead of the
# main inventory (standard wilderness looting-bag behavior), so there's no
# need to ever click Fill ourselves -- just empty it back at the bank.


def has_looting_bag() -> bool:
    return any(LOOTING_BAG_NAME in (i.get("name") or "").lower() for i in controller.get_inventory())


def empty_looting_bag(ctx) -> bool:
    """Click Empty on the looting bag to dump its contents into an open bank.
    "Empty" is a right-click option on the bag item itself, not a widget
    button -- add "Empty" to Controller Settings -> Clicking -> Forced
    Actions if that's not already its default left-click. check_ui_blockers=False
    since this is an inventory-item click, same reasoning as fill_coal_bag/
    empty_coal_bag below."""
    bag = next(
        (i for i in controller.get_inventory() if LOOTING_BAG_NAME in (i.get("name") or "").lower()),
        None,
    )
    if bag is None:
        return False

    debug_log.log("bank", "emptying looting bag")
    if not controller.click_entity(ctx.ser, bag, check_ui_blockers=False):
        return False

    controller.human_delay(0.6, 1.0)
    return True


HERB_SACK_NAME = "herb sack"
SEED_BOX_NAME = "seed box"


COAL_BAG_NAME = "coal bag"


def has_coal_bag() -> bool:
    return any(COAL_BAG_NAME in (i.get("name") or "").lower() for i in controller.get_inventory())


def fill_coal_bag(ctx) -> bool:
    """Right-click the coal bag at an open bank and explicitly select "Fill" -- CORRECTED
    2026-08-22 (user report, live Blast Furnace session): this used to be a plain click_entity
    left-click, which the function's own prior docstring already flagged as an unverified
    guess (uncertain whether a plain click resolves to Fill or Empty once the bag already
    holds coal). Confirmed live the plain click was wrong often enough to be a real bug --
    explicit menu selection via click_menu_action (same idiom empty_fur_pouch below uses)
    removes the ambiguity the same way it did there. offset=(0, 0): the bag's inventory icon
    is small, same reasoning empty_fur_pouch's own Take-sized click uses.
    check_ui_blockers=False since this is an inventory-item click, same reasoning as every
    other inventory click in this codebase."""
    bag = next(
        (i for i in controller.get_inventory() if COAL_BAG_NAME in (i.get("name") or "").lower()),
        None,
    )
    if bag is None:
        return False

    debug_log.log("bank", "filling coal bag")
    if not controller.click_menu_action(ctx.ser, bag, "fill", offset=(0, 0), check_ui_blockers=False):
        return False

    controller.human_delay(0.6, 1.0)
    return True


def empty_coal_bag(ctx) -> bool:
    """Click Empty on the coal bag to dump its coal into inventory -- e.g. before depositing
    it onto the Blast Furnace conveyor belt one piece at a time, since there's no direct
    bag-to-object deposit shortcut (see routines/blast_furnace.py). Same situation as
    empty_looting_bag above: "Empty" is not always the item's default option, so add it to
    Controller Settings -> Clicking -> Forced Actions if a plain click doesn't already
    resolve to it -- though per fill_coal_bag's note above, a non-empty bag may resolve to
    Empty by default anyway. check_ui_blockers=False since this is an inventory-item click:
    unlike empty_looting_bag (only ever called with a bank open, where the inventory sidebar
    widget is hidden), this is the first coal-bag call site used OUTSIDE a bank interface, at
    the conveyor belt, where the inventory panel itself is a live UI-blocker rect in
    resizable layouts."""
    bag = next(
        (i for i in controller.get_inventory() if COAL_BAG_NAME in (i.get("name") or "").lower()),
        None,
    )
    if bag is None:
        return False

    debug_log.log("bank", "emptying coal bag")
    if not controller.click_entity(ctx.ser, bag, check_ui_blockers=False):
        return False

    controller.human_delay(0.6, 1.0)
    return True


FUR_POUCH_NAME = "fur pouch"  # substring match -- covers small/medium/large uniformly, same
                               # idiom as COAL_BAG_NAME above (e.g. "Large fur pouch" still
                               # matches). See docs/superpowers/specs/2026-08-07-golem-crafting-
                               # design.md section 1.


def has_fur_pouch() -> bool:
    return any(FUR_POUCH_NAME in (i.get("name") or "").lower() for i in controller.get_inventory())


def fill_fur_pouch(ctx) -> bool:
    """Click the fur pouch to fill it with loose fur -- plain click_entity, NOT explicit menu
    selection. CORRECTED 2026-08-08 (live session): first tried the same explicit-menu fix as
    empty_fur_pouch below, on the assumption the same click-ambiguity risk applied -- CONFIRMED
    live it does not, and the explicit-menu version actively breaks the primary use case: while
    a bank interface is open, the pouch's own right-click menu doesn't offer "Fill" at all (it
    shows Deposit-1/5/10/X/All/Empty-to-bank instead -- bank-context actions replace the item's
    normal one), so click_menu_action(..., "fill", ...) always failed there with "No 'fill'
    entry in menu". A plain click resolves to Fill correctly in that exact context (this is
    the intended, designed-for use of a gathering pouch at a bank), which is the only place
    this function is actually called from (_run_bank_trip, bank interface open) -- unlike
    empty_fur_pouch, called AWAY from a bank, where the ambiguity between Close/Use/Check/
    Fill/Empty was real and confirmed. check_ui_blockers=False: inventory-item click, same
    reasoning as every other inventory click in this codebase."""
    pouch = next(
        (i for i in controller.get_inventory() if FUR_POUCH_NAME in (i.get("name") or "").lower()),
        None,
    )
    if pouch is None:
        return False

    debug_log.log("bank", "filling fur pouch")
    if not controller.click_entity(ctx.ser, pouch, check_ui_blockers=False):
        return False

    controller.human_delay(0.6, 1.0)
    return True


def empty_fur_pouch(ctx) -> bool:
    """Explicitly select "Empty" on the fur pouch's right-click menu to dump its fur into an
    open bank (pre-craft: into inventory instead, if no bank is open -- see
    routines/golem_crafting.py's bank_phase, which calls this right after closing the bank so
    any fur caught while hunting is available to insert into a golem).

    CORRECTED 2026-08-08 (live session, user report): this used to be a plain click_entity
    (whatever the pouch's current default action resolves to), which the function's own
    docstring already flagged as unverified -- CONFIRMED live it was the wrong guess: an
    "(open)" pouch's default click resolved to something other than "Empty" (its own menu also
    lists Close/Use/Check/Fill alongside Empty, all plausible defaults depending on state),
    silently pulling fur that had JUST been withdrawn from the bank back INTO the pouch instead
    of leaving it loose in inventory -- pick_best_fur_in_inventory() only scans top-level
    inventory item names, so fur trapped inside the pouch became invisible to craft_phase,
    which then reported "out of usable fur" and bounced back to bank_phase in an unproductive
    loop, on every single automated run. Explicit menu selection (via click_menu_action, same
    idiom slayer.py's maybe_loot uses for ground-item Take) removes the ambiguity entirely.
    offset=(0, 0): the pouch's inventory icon is small, same reasoning maybe_loot's own Take
    click uses for ground items rather than click_entity's NPC-sized default offset."""
    pouch = next(
        (i for i in controller.get_inventory() if FUR_POUCH_NAME in (i.get("name") or "").lower()),
        None,
    )
    if pouch is None:
        return False

    debug_log.log("bank", "emptying fur pouch")
    if not controller.click_menu_action(ctx.ser, pouch, "empty", offset=(0, 0), check_ui_blockers=False):
        return False

    controller.human_delay(0.6, 1.0)
    return True


PLANK_SACK_NAME = "plank sack"


def has_plank_sack() -> bool:
    return any(PLANK_SACK_NAME in (i.get("name") or "").lower() for i in controller.get_inventory())


def fill_plank_sack(ctx) -> bool:
    """Click the plank sack to absorb loose planks from the inventory. Plain click_entity, and
    ONLY meaningful with a bank interface open -- exactly the same mechanic (and the same reason)
    as fill_fur_pouch above: in bank context an item's right-click menu is replaced by
    Deposit-1/5/10/X/All, so click_menu_action(..., "Fill") cannot find the entry, while a plain
    left-click resolves to Fill correctly.

    CONFIRMED in a human recording rather than assumed -- recordings/2026-08-27_174045_
    mahogny-homes.log:812 logs the click as menuAction CC_OP on widget 15.3 slot 0 (the bank
    interface's own inventory panel) with option "Fill", against item Plank sack (24882)."""
    sack = next(
        (i for i in controller.get_inventory() if PLANK_SACK_NAME in (i.get("name") or "").lower()),
        None,
    )
    if sack is None:
        return False

    debug_log.log("bank", "filling plank sack")
    if not controller.click_entity(ctx.ser, sack, check_ui_blockers=False):
        return False

    controller.human_delay(0.6, 1.0)
    return True


def _click_bank_button(ctx, button_key: str) -> bool:
    """Click one of the bank's one-shot deposit-all buttons (depositInventory,
    depositEquipment, depositContainers) if it's currently visible. Standalone
    helpers, not wired into deposit_inventory()'s own flow above -- that one
    keeps its existing keepItems-aware, looting-bag-then-deposit-all logic
    unchanged. These exist so a routine can reach for one directly instead."""
    bank = controller.get_bank()
    if not bank.get("open"):
        return False

    button = bank.get(button_key) or {}
    if button.get("screenX") is None:
        return False

    controller.click_entity(ctx.ser, button, offset=(0, 0))
    controller.human_delay(0.3, 0.6)
    return True


def deposit_all_inventory(ctx) -> bool:
    return _click_bank_button(ctx, "depositInventory")


def deposit_all_worn(ctx) -> bool:
    return _click_bank_button(ctx, "depositEquipment")


def deposit_all_containers(ctx) -> bool:
    """Deposits the contents of every gatherer-bag/container in inventory at
    once -- looting bag, herb sack, seed box, gem bag, coal bag, etc. -- via
    the bank's own dedicated button, in a single click, without needing the
    bag's own Empty forced-action at all."""
    return _click_bank_button(ctx, "depositContainers")


def deposit_inventory(ctx, config: dict) -> None:
    keep = {n.lower() for n in config.get("keepItems", [])}

    bank = controller.get_bank()
    if not bank.get("open"):
        return

    # ADDED 2026-08-22 (user report, live Blast Furnace session): opt-out for a routine that
    # manages its own container (e.g. blast_furnace.py's coal bag, deliberately emptied at the
    # conveyor belt via banker.empty_coal_bag, not into the bank) and doesn't want the generic
    # "empty every container into the bank" step firing on every single bank visit -- CONFIRMED
    # live this was doing exactly that (deposit_all_containers below, unconditionally, every
    # trip) even though blast_furnace.py's own flow never needs it and it serves no purpose
    # there once the bag's contents are already handled explicitly. Defaults to running (every
    # other existing caller's behavior is unchanged) unless a caller explicitly opts out.
    if not config.get("skipContainerDeposit"):
        if has_looting_bag():
            empty_looting_bag(ctx)

        # Empties looting bag/herb sack/seed box/coal bag/etc. into the bank via the bank
        # interface's own "Deposit Containers" widget button, in one click -- CONFIRMED (user
        # request 2026-08-06): herb sack and seed box are kept out of the item-by-item deposit
        # pass below (see routines/slayer.py's protected_supply_names) so the container itself
        # never gets banked away, but that alone would leave its contents trapped inside it
        # forever. Redundant with empty_looting_bag above for that one bag specifically, which
        # is harmless -- emptying an already-empty container is a no-op.
        deposit_all_containers(ctx)

    if not keep:
        deposit_all = bank.get("depositInventory") or {}
        if deposit_all.get("screenX") is not None:
            controller.click_entity(ctx.ser, deposit_all, offset=(0, 0))
            controller.human_delay(0.3, 0.6)
        return

    # Keeping specific items -- deposit one DISTINCT item name at a time instead of Deposit-all,
    # skipping anything on the keep list. Shift+click instead of a plain click -- CORRECTED
    # 2026-08-22 (user report, live session): shift+click on an inventory item while the bank is
    # open is OSRS's own "deposit every item of this type" shortcut (same "shift = all" mechanic
    # banker.withdraw_all_shift already uses on the withdraw side), so it's one click per
    # distinct item name instead of one click per individual item slot -- a real difference for
    # non-stackable items like bars: a steel trip depositing up to 27 individual Steel bars used
    # to click each one separately. `deposited_names` tracks which names have already been
    # shift-deposited so later slots holding the same (now already-gone) item name aren't
    # clicked again.
    deposited_names = set()
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if any(k in name for k in keep):
            continue
        if name in deposited_names:
            continue
        deposited_names.add(name)
        controller.key_down(ctx.ser, "SHIFT")
        controller.click_entity(ctx.ser, item)
        controller.key_up(ctx.ser, "SHIFT")
        controller.human_delay(0.2, 0.4)


def set_quantity_mode(ctx, mode: str) -> bool:
    """mode is one of "one", "five", "ten", "x", "all" -- matching the keys
    BankProvider puts in quantityButtons. Clicking the mode that's already
    selected is harmless, so this doesn't bother checking first."""
    bank = controller.get_bank()
    button = bank.get("quantityButtons", {}).get(mode) or {}
    if button.get("screenX") is None:
        debug_log.log("bank", f"quantity mode button '{mode}' not found (bank open?)")
        return False

    controller.click_entity(ctx.ser, button, offset=(0, 0))
    controller.human_delay(0.2, 0.4)
    return True


def switch_bank_tab(ctx, index: int) -> bool:
    """Click bank tab `index` (0 = "view all", 1-9 = the player's own numbered tabs) --
    ADDED 2026-08-12 (user request): an alternative to search_bank for a player who's
    organized specific items (e.g. herbs) into a dedicated tab. See BankProvider.java's own
    "tabs" field docstring for the same not-yet-verified-live caveat on which widget-children
    call actually populates it.

    ADDED 2026-08-13 (user directive: check before switching instead of always re-clicking):
    skips the click entirely if `currentTab` (BankProvider.java, VarbitID.BANK_CURRENTTAB)
    already reports `index` -- a human wouldn't click a tab they're already viewing. Returns
    True in that case the same as an actual successful switch, since the caller's postcondition
    (now viewing that tab) already holds either way.

    CORRECTED 2026-08-27 (user report: misclicking a UI element used to cascade into a confusing
    downstream safety stop): the click used to be trusted as a successful switch outright, with
    no confirmation it landed -- a dropped/misclicked tap left the caller reading the WRONG tab's
    contents, surfacing later as an unrelated failure (mastering_mixology.py hard safety-stopping
    on the very next thing that looked wrong instead of on the actual missed click). Now retries
    via controller.ensure_ui_state, re-checking `currentTab` with a fresh bank read after each
    click, before declaring success or giving up."""
    bank = controller.get_bank()
    if bank.get("currentTab") == index:
        return True

    tab = next((t for t in bank.get("tabs", []) if t.get("index") == index), None)
    if tab is None or tab.get("screenX") is None:
        debug_log.log("bank", f"tab {index} button not found (bank open?)")
        return False

    def click_tab():
        controller.click_entity(ctx.ser, tab, offset=(0, 0), check_ui_blockers=False)

    def tab_is_active():
        return controller.get_bank(fresh=True).get("currentTab") == index

    ok = controller.ensure_ui_state(click_tab, tab_is_active, retry_delay_s=(0.3, 0.5))
    if not ok:
        debug_log.log("bank", f"clicked tab {index} but currentTab never confirmed the switch")
    return ok


def wait_until_bank_item_gone(ctx, name_substring: str, timeout_s: float = 1.5, poll_interval_s: float = 0.15) -> bool:
    """Polls /bank with fresh=True until no item name contains name_substring (case-insensitive),
    or timeout_s elapses. fresh=True only guarantees the bridge's own cache isn't stale -- it does
    NOT guarantee a just-clicked withdrawal has actually landed server-side yet (that still
    resolves on a real game-tick boundary), so a single fresh read taken immediately after a
    withdrawal click can still observe the pre-withdrawal state. Polling with a short interval
    catches the withdrawal as soon as it actually lands, instead of trusting either a single fresh
    read or a fixed blind sleep. Returns True if the item was confirmed gone, False if the timeout
    was hit first (logged; the caller isn't required to treat that as a hard failure -- the old
    sleep-based code didn't guarantee success here either, e.g. against a simulated misclick)."""
    name_substring = name_substring.lower()
    deadline = time.monotonic() + timeout_s
    while True:
        bank = controller.get_bank(fresh=True)
        if not any(name_substring in (i.get("name") or "").lower() for i in bank.get("items", [])):
            return True
        if time.monotonic() >= deadline:
            debug_log.log("bank", f"'{name_substring}' still shows in the bank after {timeout_s}s -- withdrawal may not have landed yet")
            return False
        time.sleep(poll_interval_s)


def withdraw_loadout(ctx, config: dict, fresh: bool = False) -> None:
    """fresh: forces this call's /bank reads to bypass the bridge's per-tick cache -- pass True
    when the bank state may have just changed from something outside this call (e.g. a separate
    withdraw_loadout call for a different item immediately before this one) and waiting out a
    full game tick for the cache to reflect it isn't acceptable. See controller.get_bank's own
    fresh kwarg."""
    entries = [e for e in config.get("withdraw", []) if e.get("name") and e.get("quantity", 1) > 0]

    if entries:
        set_quantity_mode(ctx, "one")

    for entry in entries:
        name = entry["name"].lower()
        quantity = entry["quantity"]

        bank = controller.get_bank(fresh=fresh)
        slot = next(
            (s for s in bank.get("items", []) if name in (s.get("name") or "").lower()),
            None,
        )
        if slot is None:
            debug_log.log("bank", f"{entry.get('name')} not found in bank, skipping")
            continue

        debug_log.log("bank", f"withdrawing {quantity}x {slot.get('name')}")
        for _ in range(quantity):
            controller.click_entity(ctx.ser, slot)
            controller.human_delay(0.15, 0.3)

    # Auto-pick fallback, independent of the explicit list above -- CONFIRMED (user request
    # 2026-08-06): "just let it pick any prayer restore/food, prioritising the one with the
    # highest number". Exists for when Controller Settings' withdraw list doesn't cover
    # food/prayer explicitly (or is empty entirely -- the case that originally surfaced this: an
    # out-of-critical-supplies bank trip kept "succeeding" without ever actually restocking
    # anything, since there was nothing configured to withdraw). A caller opts in per-trip via
    # config["autoFood"]/config["autoPrayer"] rather than this being unconditional, since not
    # every routine using banker.py wants it.
    if config.get("autoFood"):
        withdraw_best_food(ctx, config.get("autoFoodQuantity", 10))
    if config.get("autoPrayer"):
        withdraw_best_prayer_restore(ctx, config.get("autoPrayerQuantity", 10))


def withdraw_all_shift(ctx, name: str, fresh: bool = False) -> bool:
    """Shift+left-click a bank item once to withdraw its entire stack -- OSRS's own bank
    shift-click default action, naturally capped by however many free inventory slots the
    player actually has rather than needing an explicit quantity. ADDED 2026-08-22 (user
    report, live Blast Furnace session): withdraw_loadout's per-unit click loop (one click per
    unit of quantity) is fine for the handful of potions/food every other routine withdraws
    (see withdraw_coins_via_x's docstring in blast_furnace.py for the same observation), but
    is slow and needlessly click-heavy for a 27-28 unit ore withdrawal that's meant to fill
    the rest of inventory anyway -- one shift-click does the same job. Only safe to use when
    the caller wants "as much of this single item as fits", not an exact partial quantity
    (e.g. NOT bronze bars' 14 Copper ore + 14 Tin ore split -- shift-clicking Copper there
    would happily fill all 28 slots with copper and leave none for tin).

    Exact-name match preferred over substring, same reasoning as click_menu_action's
    option-matching and the coal/coal-bag substring-collision note in
    routines/blast_furnace.py's run_trip -- a bare substring search for "coal" could otherwise
    resolve to "Coal bag" instead of "Coal" if the bag sorts earlier in the bank.

    Polls inventory for the item's actual arrival before returning, rather than trusting a
    fixed delay -- CORRECTED 2026-08-22 (user report, live session): a fixed human_delay here
    wasn't a strong enough guarantee the withdrawal had actually landed server-side before the
    caller went on to close the bank. CONFIRMED live this raced the real game-tick boundary:
    closing the bank interface (ESC) before the withdrawal was processed cancelled it outright,
    leaving inventory with none of the withdrawn item at all -- the caller's own conveyor
    deposit then found 0 to deposit. Same "poll, don't just sleep" reasoning
    wait_until_bank_item_gone already applies to the coal-bag-withdrawal race, just watching
    for the item's arrival in inventory instead of its disappearance from the bank."""
    bank = controller.get_bank(fresh=fresh)
    name_lower = name.lower()
    slot = next(
        (s for s in bank.get("items", []) if name_lower == (s.get("name") or "").lower()),
        None,
    )
    if slot is None:
        slot = next(
            (s for s in bank.get("items", []) if name_lower in (s.get("name") or "").lower()),
            None,
        )
    if slot is None:
        debug_log.log("bank", f"{name} not found in bank, skipping")
        return False

    debug_log.log("bank", f"shift-withdrawing all {slot.get('name')}")
    controller.key_down(ctx.ser, "SHIFT")
    ok = controller.click_entity(ctx.ser, slot)
    controller.key_up(ctx.ser, "SHIFT")
    if not ok:
        return False

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if any(name_lower in (i.get("name") or "").lower() for i in controller.get_inventory()):
            return True
        time.sleep(0.15)

    debug_log.log("bank", f"shift-withdrawn {name} never showed up in inventory -- may have raced the bank closing")
    return False


def search_bank(ctx, query: str) -> bool:
    """Click the bank's search icon and type `query` into it -- filters the item grid live (no
    Enter needed), re-laying matches out starting from slot 0 so a caller can then find and
    click the item at a normal, guaranteed-visible screen position instead of wherever it
    happened to sit in the full unfiltered list. CONFIRMED live 2026-08-06: a bank with hundreds
    of unique items (this user's) reports real but far-off-screen screenX/screenY for anything
    not on the first visible page (seen up to y=7813 against a ~1400px-tall window) -- the same
    class of problem find_nearby_bank's radius fix addressed for an unbounded scene walk, but
    there's no equivalent bound for "how far into a scrollable list" short of actually searching.

    CONFIRMED live 2026-08-06: controller.type_text's letter-key-name sending works (typed
    "Sanfew serum" including the capital S and the space, filtered 815 items down to exactly
    the 1 match), and clicking Bankmain.SEARCH opens live-filtering search as expected.
    """
    bank = controller.get_bank()
    button = bank.get("searchButton") or {}
    if button.get("screenX") is None:
        debug_log.log("bank", "search button not found (bank open?)")
        return False

    controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False)
    controller.human_delay(0.2, 0.4)
    controller.type_text(ctx.ser, query)
    # CONFIRMED live 2026-08-12 (Mastering Mixology live session): a 0.3-0.5s settle here --
    # shorter than one full game tick (0.6s), with no margin -- reproduced a real bug twice in a
    # row on the THIRD back-to-back search within one bank session: /bank's own read already
    # reflected the new query (the caller's item-lookup found the right name/id), but the actual
    # on-screen click still landed on the PREVIOUS search's result instead, silently withdrawing
    # the wrong item. Bumped to comfortably clear a full tick with margin.
    controller.human_delay(0.7, 0.9)
    return True


def clear_bank_search(ctx) -> bool:
    """Clear an active bank search filter by clicking the search icon again, WITHOUT closing
    the bank -- CONFIRMED live 2026-08-06: pressing ESC while search is active closes the whole
    bank interface (tried first; not what we want mid-trip), but re-clicking the search icon
    toggles the filter off and the full item list (815 items, in the case tested) comes right
    back with the bank still open. Needed between withdraw_best_food and
    withdraw_best_prayer_restore in the same trip -- CONFIRMED live: without this, the second
    call's own "scan all bank items for candidates" step would only see whatever the first
    call's search left filtered down to, and (correctly, but confusingly) report finding
    nothing."""
    bank = controller.get_bank()
    button = bank.get("searchButton") or {}
    if button.get("screenX") is None:
        return False

    controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False)
    controller.human_delay(0.2, 0.4)
    return True


_DOSE_RE = re.compile(r"\((\d+)\)\s*$")


def _dose(name: str) -> int:
    """Trailing (N) dose count off an item name, e.g. "Prayer potion(4)" -> 4 -- 0 if there
    isn't one. Same idea as FoodUtil's heal amount for food, for picking the best available
    stack of something that comes in multiple charge/dose levels."""
    match = _DOSE_RE.search(name or "")
    return int(match.group(1)) if match else 0


def search_and_withdraw(
    ctx,
    item_name: str,
    quantity: int,
    contains: bool = False,
) -> bool:
    """Search-then-click withdraw for one named item, guaranteeing it's scrolled onto
    an on-screen, clickable position first (see search_bank's docstring for why that matters).

    Search by the base name, without any trailing (N) dose/charge suffix.

    By default, the bank item name must exactly match item_name. If contains=True,
    the bank item name only needs to contain item_name. This is useful for items
    with dose/charge suffixes such as "Ring of dueling(8)" while preserving the
    existing exact-match behavior for other callers.
    """
    search_query = _DOSE_RE.sub("", item_name).strip()

    if not search_bank(ctx, search_query):
        return False

    bank = controller.get_bank()

    # Require qty > 0 so zero-stock placeholder slots are ignored.
    slot = next(
        (
            s
            for s in bank.get("items", [])
            if (
                item_name.lower() in (s.get("name") or "").lower()
                if contains
                else (s.get("name") or "").lower() == item_name.lower()
            )
            and s.get("qty", 0) > 0
        ),
        None,
    )

    if slot is None:
        debug_log.log(
            "bank",
            f"{item_name} not found (or out of stock) after searching '{search_query}'",
        )
        return False

    set_quantity_mode(ctx, "one")

    debug_log.log(
        "bank",
        f"withdrawing {quantity}x {slot.get('name')}",
    )

    # Track whether the clicks actually landed instead of assuming success.
    landed = 0

    for _ in range(quantity):
        if controller.click_entity(ctx.ser, slot):
            landed += 1

        controller.human_delay(0.15, 0.3)

    clear_bank_search(ctx)

    if landed == 0:
        debug_log.log(
            "bank",
            f"withdrawing {slot.get('name')} failed -- no click landed",
        )
        return False

    if landed < quantity:
        debug_log.log(
            "bank",
            f"only {landed}/{quantity} withdraw clicks on {slot.get('name')} landed",
        )

    return True

def withdraw_best_food(ctx, quantity: int = 1) -> bool:
    """Auto-pick whichever food is actually in the bank, preferring the highest heal amount,
    instead of requiring an exact item name configured in Controller Settings -- CONFIRMED
    (user request 2026-08-06): "just let it pick any food, prioritising the one with the
    highest number [heal amount]". Classification (FoodUtil.isFood/getHealAmount) comes from
    BankProvider.java, the same lookup ConsumablesProvider already applies to inventory. Only
    meaningful while a bank interface is open."""
    bank = controller.get_bank()
    candidates = [i for i in bank.get("items", []) if i.get("isFood")]
    if not candidates:
        debug_log.log("bank", "no food found in bank")
        return False

    candidates.sort(key=lambda i: i.get("heal", 0), reverse=True)
    best = candidates[0]
    debug_log.log("bank", f"auto-picked food: {best.get('name')} (heals {best.get('heal')})")
    return search_and_withdraw(ctx, best.get("name"), quantity)


def withdraw_best_prayer_restore(ctx, quantity: int = 1) -> bool:
    """Same idea as withdraw_best_food but for prayer restores, preferring the highest dose
    count (e.g. Prayer potion(4) over Prayer potion(1)) -- CONFIRMED (user request 2026-08-06)."""
    bank = controller.get_bank()
    candidates = [i for i in bank.get("items", []) if i.get("isPrayerPotion")]
    if not candidates:
        debug_log.log("bank", "no prayer restore found in bank")
        return False

    candidates.sort(key=lambda i: _dose(i.get("name")), reverse=True)
    best = candidates[0]
    debug_log.log("bank", f"auto-picked prayer restore: {best.get('name')}")
    return search_and_withdraw(ctx, best.get("name"), quantity)


def bank_up(ctx, config: dict) -> bool:
    """
    Full cycle: bank at whatever's within walking distance if anything is,
    otherwise teleport to Ferox Enclave via the ring of dueling, bank and
    use the fountain there, then walk back. Either way: deposit per
    keepItems, withdraw the configured loadout, close.
    """
    if not config.get("enabled", True):
        return False

    # Retried, not a single check -- see _find_nearby_bank_retrying's own docstring (a transient
    # /objects timeout here would otherwise wrongly fall through to the teleport-to-Ferox path
    # below even when a real bank is right there).
    bank_obj = _find_nearby_bank_retrying(config)
    if bank_obj is not None:
        debug_log.log("bank", f"walking to {bank_obj.get('name')} (dist={bank_obj.get('distance')})")
        if not open_bank(ctx, bank_obj):
            debug_log.log("bank", "failed to open bank")
            return False

        deposit_inventory(ctx, config)
        withdraw_loadout(ctx, config)
        close_bank(ctx)
        return True

    if not config.get("teleportFallback", True):
        return False

    return _bank_via_ferox(ctx, config)


def _bank_via_ferox(ctx, config: dict) -> bool:
    ferox_search_config = dict(config)
    ferox_search_config["bankNames"] = FEROX_BANK_NAMES
    ferox_search_config["maxBankDistance"] = FEROX_MAX_BANK_DISTANCE

    # Check whether we're already standing at Ferox before teleporting -- CONFIRMED live
    # 2026-08-06: bank_up gets re-called every tick for as long as the caller's own "do we still
    # need to bank" check stays true (e.g. slayer.py's needs_banking), and this used to
    # unconditionally teleport on every single call. A ring of dueling that ran out mid-cycle
    # (charges are finite) then failed forever with "no ring of dueling in inventory" -- even
    # once the player was already standing right next to the Ferox bank and could just walk up
    # and use it normally. bank_ferox_return_location persists across calls within one trip so a
    # retry after a mid-trip failure doesn't re-save "already at Ferox" as the place to walk back
    # to, and gets popped (not just read) once actually used, below.
    #
    # find_nearby_bank retried a few times (not a single check) before concluding "not there,
    # need to teleport" -- CONFIRMED live 2026-08-06: get_objects() fails-safe to an empty list
    # on a bridge timeout (see api_get), which find_nearby_bank can't tell apart from "genuinely
    # no bank nearby". Right after a client (re)start the scene is still loading and /objects
    # timed out almost every tick, so a single check burned 4 ring-of-dueling charges
    # re-teleporting to a place the player never actually left. A few retries with a short pause
    # costs nothing when we really aren't there yet, but avoids spending an actually-finite
    # resource on what was just a slow poll.
    bank_obj = _find_nearby_bank_retrying(ferox_search_config)
    if bank_obj is None:
        if "bank_ferox_return_location" not in ctx.state.data:
            saved_location = travel.save_location()
            if saved_location is None:
                debug_log.log("bank", "couldn't record current location, aborting teleport bank trip")
                return False
            ctx.state.data["bank_ferox_return_location"] = saved_location

        if not travel.teleport_to_ferox(ctx):
            if not any(
                travel.RING_OF_DUELING_NAME in (i.get("name") or "").lower()
                for i in controller.get_inventory()
            ):
                # No bank in range AND no way to teleport to one that is -- a genuine dead end,
                # not a transient failure worth retrying forever. Left as a hard stop rather
                # than silently spinning every tick doing nothing while critically low on
                # supplies (see out_of_critical_supplies in routines/slayer.py, the case that
                # surfaced this).
                ctx.state.trigger_safety_stop("out of supplies, no bank in range, and no ring "
                                               "of dueling left to reach one")
            return False

        bank_obj = _find_nearby_bank_retrying(ferox_search_config)
        if bank_obj is None:
            debug_log.log("bank", "no bank found at Ferox Enclave -- did the teleport actually land?")
            return False

    if not open_bank(ctx, bank_obj):
        debug_log.log("bank", "failed to open the Ferox Enclave bank")
        return False

    # Never deposit the ring or seed pod themselves, or every trip after this
    # one is stranded (no way back, or no wilderness escape).
    deposit_config = dict(config)
    deposit_config["keepItems"] = list(config.get("keepItems", [])) + [
        travel.RING_OF_DUELING_NAME,
        travel.ROYAL_SEED_POD_NAME,
    ]
    deposit_inventory(ctx, deposit_config)
    withdraw_loadout(ctx, config)
    close_bank(ctx)

    travel.use_fountain(ctx)

    saved_location = ctx.state.data.pop("bank_ferox_return_location", None)
    if saved_location is not None:
        debug_log.log("bank", "walking back to the previous location")
        if not travel.walk_to(ctx, saved_location):
            debug_log.log("bank", "couldn't walk all the way back -- continuing from here")

    return True
