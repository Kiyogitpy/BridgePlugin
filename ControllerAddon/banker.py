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

import bank_memory
import bridge_events
import controller
import debug_log
import legs
import teleports
import travel
import worldmap

# Event-driven waits (2026-09-04, docs/superpowers/specs/2026-09-04-event-driven-outcomes-
# design.md): the bank interface is group 12 (InterfaceID.Bankmain); its WIDGET_LOADED /
# WIDGET_CLOSED and the INVENTORY_CHANGED of a deposit decide WHEN to read /bank and
# /inventory, which stay the truth. With no event stream these degrade to the same polls as
# before (bridge_events.await_outcome's None fallback).
BANK_WIDGET_GROUP = 12
BANK_CLOSE_BUDGET_S = 0.8
DEPOSIT_ALL_BUDGET_S = 1.5


def _bank_widget(payload) -> bool:
    return payload.get("groupId") == BANK_WIDGET_GROUP


def _events(ctx):
    return getattr(ctx, "events", None)


def _mark(ctx):
    events = _events(ctx)
    return events.mark() if events is not None else None

DEFAULT_BANK_NAMES = ("bank booth", "bank chest", "bank counter", "grand exchange booth")
FEROX_BANK_NAMES = ("bank chest", "bank booth")
FEROX_MAX_BANK_DISTANCE = 20

#: Ferox Enclave, as a box. FOUND LIVE 2026-09-21 -- USER: "the bug is it teleported twice to
#: ferox". The guard below asks "is a bank within FEROX_MAX_BANK_DISTANCE", which is not the same
#: question as "am I already there": the ring lands at (3150, 3636) and the enclave's bank chests
#: sit around x 3129, right on that 20-tile boundary. Miss it and the trip decides it never
#: arrived and teleports AGAIN into the same square, spending another charge -- that run ended
#: with no ring of dueling left at all.
#:
#: Drawn to the enclave's walls rather than generously: a box too wide would refuse the teleport
#: somewhere that genuinely needs it.
FEROX_AREA = (3122, 3617, 3159, 3659)      # x1, y1, x2, y2 on plane 0


def in_ferox(player) -> bool:
    """Is the player standing inside Ferox Enclave? Unknown position answers False -- never claim
    to be somewhere we cannot see, since that would refuse a teleport the trip depends on."""
    if not isinstance(player, dict):
        return False
    x, y = player.get("x"), player.get("y")
    if x is None or y is None or (player.get("plane") or 0) != 0:
        return False
    x1, y1, x2, y2 = FEROX_AREA
    return x1 <= x <= x2 and y1 <= y <= y2

# Past this many tiles open_bank walks toward the bank before clicking it -- see the
# comment at that branch. Deliberately generous: a normal "arrived at the bank" position
# is a handful of tiles out, and this must never add a walk to a trip that already works.
# RAISED 8 -> 12 on 2026-08-30 alongside first-click-while-running: the booth is clicked from
# a minimap hop away now (the game re-paths the last stretch itself), so "close enough to
# click" has to cover where that hop actually stops. The through-wall 21-tile chatbox
# projection this guards against (see the too_far comment) is still comfortably past 12.
BANK_APPROACH_TILES = 12


# A name match is not enough: the Grand Exchange has TWO booth objects both named "Grand
# Exchange booth" -- 10060/30389 offer Bank + Collect, 10061 (exchange_bank_wall_exchange in
# the cache) offers Exchange + Collect and opens the GE clerk interface. MEASURED live
# 2026-09-05 (firemaking at the GE): the nearest by name was a 10061 and open_bank clicked
# "Exchange" on it over and over (USER: "thats not a banker"). Bank chests report "Use" rather
# than "Bank", so both count. An object that reports no actions at all is kept -- absent means
# "cannot tell", never "no", same as collisionFlags elsewhere in this codebase.
BANK_OPEN_ACTIONS = ("bank", "use")


def _offers_banking(obj: dict) -> bool:
    actions = obj.get("actions")
    if not actions:
        return True
    return any((a or "").lower() in BANK_OPEN_ACTIONS for a in actions)


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
        if not _offers_banking(obj):
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
    tile = (bank_obj.get("x"), bank_obj.get("y"))
    # THE SAME TILE FIRST. A bank counter is several booths sharing one id, and the first id
    # match in the scan is whichever the list happens to order first -- USER 2026-09-10,
    # watching the vale totems bank trip: "we click one banker then after we fill inventory we
    # swap to a different banker. just choose the same one again".
    same_id = None
    for obj in controller.get_objects(radius=15):
        if obj.get("id") == target_id and obj.get("visible", True):
            if (obj.get("x"), obj.get("y")) == tile:
                return obj
            same_id = same_id or obj
    return same_id or bank_obj


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
    first_click = True
    while time.time() < deadline:
        # The FIRST click flies while still running in -- clicking the booth mid-approach is
        # what the human does (the game re-paths to it and opens on arrival), and the click's
        # aim stays true through the flight via refresh_target below. USER 2026-08-30, watching
        # the old behaviour live: "weird clicks around the bank instead of just updating the
        # bank booth while running there and clicking it". The idle wait predates
        # refresh_target (2026-08-12 Mixology, when a moving player made any aim stale) and
        # still guards every RETRY: a retry means the first click failed, and re-clicking while
        # the previous click's walk is still resolving is exactly the redirect loop that
        # session measured.
        if not first_click:
            controller.wait_until_player_idle(timeout_s=max(1.0, deadline - time.time()))
        first_click = False
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
                    controller.click_entity(ctx.ser, point, offset=(0, 0), check_ui_blockers=False)
                    controller.wait_until_player_idle(timeout_s=max(1.0, deadline - time.time()))
                    continue
            if off_screen:
                # An off-screen aim point is outside the RuneLite window, so click_entity would
                # refuse it anyway -- spending the timeout beats a guaranteed miss. A merely
                # DISTANT bank is still clickable (the game walks there itself), so fall through
                # to the click rather than deadlocking on a minimap that cannot reach ~28 tiles.
                continue

        # refresh_target so the aim tracks the booth THROUGH the flight. USER 2026-08-30,
        # watching live (mh_live29 02:51:11): "very stale" -- the pre-flight snapshot aim
        # landed 140px from where the booth projected by the time the cursor arrived (the
        # player was walking), the click bought nothing, and the loop paid a full idle-wait
        # before re-clicking: ~9s for what should be one click.
        refresh = None
        if fresh.get("x") is not None and fresh.get("id") is not None:
            refresh = controller.object_refresh_target(
                fresh["x"], fresh["y"], fresh.get("plane", 0), fresh["id"])
        mark = _mark(ctx)
        controller.click_entity(ctx.ser, fresh, refresh_target=refresh)

        # Give the click time to resolve without reclicking yet -- reclicking would interrupt
        # a click that is about to work. Budgets the WALK the game performs first (the same
        # lesson as the door and hotspot budgets: whenever you time a click, ask whether the
        # player has to walk there first) -- the old flat 3.0s expired mid-walk from 4+ tiles
        # out, so the retry raced the bank opening it was waiting for. The interface loading
        # (WIDGET_LOADED 12) is when /bank is read; the read decides alone at the budget.
        click_deadline = min(deadline, time.time() + _click_resolve_budget(fresh.get("distance", 0)))
        if bridge_events.await_outcome(_events(ctx), {"WIDGET_LOADED": _bank_widget},
                                       max(0.0, click_deadline - time.time()),
                                       confirm=is_open, since=mark, reaction=False):
            return True
    return is_open()


def _click_resolve_budget(distance) -> float:
    """Seconds to give one bank click before retrying: base resolve time plus the walk the
    game performs to reach the booth (~0.6s/tile at run, budgeted at 0.7), capped so a click
    that will never work still fails fast."""
    return min(3.0 + max(0, distance or 0) * 0.7, 10.0)


def close_bank(ctx) -> None:
    # Escape closes the bank interface like any other RuneScape interface.
    # Adjust this if the Arduino firmware expects a different key string.
    mark = _mark(ctx)
    controller.key_tap(ctx.ser, "ESC")
    # Return once the interface is actually gone (WIDGET_CLOSED 12, confirmed by /bank.open)
    # so a caller's next click never lands on a closing bank; a caller that used to sleep a
    # flat "let the bank close" delay after this can drop it.
    bridge_events.await_outcome(_events(ctx), {"WIDGET_CLOSED": _bank_widget}, BANK_CLOSE_BUDGET_S,
                                confirm=lambda: not is_open(), since=mark, reaction=False)


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
    if not controller.click_entity(ctx.ser, bag, check_ui_blockers=False, simulate_mistakes=False):
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
    if not controller.click_entity(ctx.ser, bag, check_ui_blockers=False, simulate_mistakes=False):
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
    if not controller.click_entity(ctx.ser, pouch, check_ui_blockers=False, simulate_mistakes=False):
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


LOG_BASKET_NAME = "log basket"  # substring match, and it has to be: MEASURED from the cache
                                # (config/obj), the item is "Log basket" (28140) while shut and
                                # "Open log basket" (28142) once opened -- and only the OPEN one
                                # collects logs on its own, so the shut name alone would miss the
                                # state this exists for. Deliberately NOT matching the Forestry
                                # basket (28143/28145, the log basket + forestry kit combined via
                                # a Log brace): a different name, and the user does not have one.


EMPTY_BASKET_TRIES = 3


def has_log_basket() -> bool:
    return any(LOG_BASKET_NAME in (i.get("name") or "").lower() for i in controller.get_inventory())


def empty_log_basket(ctx) -> bool:
    """Empty the log basket into an OPEN bank. Added 2026-09-08 (USER: "need to add a option to
    empty wood basket too since we use that for cutting wood basicly doubling our capacity",
    then "it fills automaticly all we need to do is right click it in bank").

    THERE IS NO FILL STEP, and that is the whole shape of this: an open log basket absorbs logs
    as they are cut (cache config/obj 28142, param_1784: "When opened logs will automatically be
    deposited into it"), so unlike fill_fur_pouch/fill_coal_bag above there is nothing to drive
    on the gathering side. The inventory simply does not reach 28 until the basket's own 28 are
    full -- which is the capacity doubling -- and the only thing we owe it is the empty here.

    An explicit menu selection, not a plain click, and NOT because the click is contested:
    MEASURED from the cache, neither log_basket_closed (28140) nor log_basket_open (28142) has
    an "Empty" iop AT ALL -- both offer Fill / Wear / Check / Open|Close / Destroy. So the Empty
    the user right-clicks is a BANK-CONTEXT option the bank interface substitutes in over the
    item's own, exactly as fill_fur_pouch's docstring records for the fur pouch
    ("Deposit-1/5/10/X/All/Empty-to-bank instead -- bank-context actions replace the item's
    normal one"). A bare left click would resolve to whichever of Fill / Deposit-1 / Wear won
    that race, and Fill in particular is a silent no-op that looks like success.

    The option is a SEQUENCE tried in priority order against the one menu this already reads
    back (no extra right-click): the exact bank-context wording is not knowable offline, and the
    fish barrel next door answers to "Empty-to-bank" while the fur pouch and looting bag answer
    to "Empty". A wrong single guess would no-op forever -- click_menu_action's sequence form
    exists for precisely this (see its docstring on chinchompa_hunting's trap states).

    offset=(0, 0) because an inventory icon is small, and check_ui_blockers=False because this
    is a click inside the bank's own inventory panel, which the 2026-09-02 blocker rects cover
    (LIVE bf_live5 -- the default check refused the click and rotated the camera instead).
    There is deliberately NO simulate_mistakes argument here: click_menu_action does not take
    one, because it already hardcodes simulate_mistakes=False on its own right-click
    (controller.py) -- which is what this site needs anyway, since a misclick's corrective
    re-click in that grid lands on the NEIGHBOURING item, here a stack of logs. Passing it
    CRASHED the routine live on 2026-09-08; see the test that now binds the real signature."""
    debug_log.log("bank", "emptying log basket")
    for attempt in range(EMPTY_BASKET_TRIES):
        # Re-read EVERY attempt. MEASURED live 2026-09-08 on the first real bank trip: the
        # right-click landed on bare ground (menu `['Cancel', 'Walk here']`) because
        # /inventory reported the basket at screen (31,50) -- read 0.12s after the bank
        # interface opened, i.e. a pre-layout widget position taken mid-transition. The very
        # next item in the same panel resolved to a sane (1598,445) 2.8s later, and with the
        # bank shut the basket itself reads (1529,438). The position was not wrong, it was
        # EARLY; retrying with the same stale dict would re-aim at the same dead pixel.
        basket = next((i for i in controller.get_inventory()
                       if LOG_BASKET_NAME in (i.get("name") or "").lower()), None)
        if basket is None:
            return False
        if controller.click_menu_action(ctx.ser, basket, ("empty-to-bank", "empty"),
                                        offset=(0, 0), check_ui_blockers=False):
            controller.human_delay(0.6, 1.0)
            return True
        if attempt + 1 < EMPTY_BASKET_TRIES:
            controller.human_delay(0.6, 1.0)

    # Not a stop: a basket that will not empty costs capacity, never the trip -- the deposit
    # pass right after this still banks the loose logs and the run carries on.
    debug_log.log("bank", f"the log basket would not empty after {EMPTY_BASKET_TRIES} tries "
                          f"-- carrying on with it full")
    return False


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
    if not controller.click_entity(ctx.ser, sack, check_ui_blockers=False, simulate_mistakes=False):
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

    controller.click_entity(ctx.ser, button, offset=(0, 0), simulate_mistakes=False,
                            check_ui_blockers=False)
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

        # The log basket, same idea and the same opt-out. It is emptied BEFORE the deposit pass
        # below, which is load-bearing in both directions: its logs have to reach the bank, and
        # the basket itself has to survive -- gathering.py puts it on the woodcutting keep list
        # so the pass does not bank the container away after emptying it.
        if has_log_basket():
            empty_log_basket(ctx)

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
            before = len(controller.get_inventory())
            mark = _mark(ctx)
            # check_ui_blockers=False: a bank BUTTON is inside the bank interface, which the
            # 2026-09-02 blocker rects cover -- the default check refused it and rotated the
            # camera instead (LIVE bf_live5, 2026-09-04). Same rule as every interface click.
            controller.click_entity(ctx.ser, deposit_all, offset=(0, 0), check_ui_blockers=False)
            if before > 0:
                # The deposit landing is an INVENTORY_CHANGED; fewer items is the truth.
                bridge_events.await_outcome(_events(ctx), "INVENTORY_CHANGED", DEPOSIT_ALL_BUDGET_S,
                                            confirm=lambda: len(controller.get_inventory()) < before,
                                            since=mark)
            else:
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
        # Inventory items sit inside the bank's own side panel -- an interface click, so it
        # opts out of the blocker check (LIVE bf_live5, 2026-09-04: refused + camera rotate,
        # and the ore never left the inventory).
        controller.click_entity(ctx.ser, item, check_ui_blockers=False)
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

    controller.click_entity(ctx.ser, button, offset=(0, 0), simulate_mistakes=False,
                            check_ui_blockers=False)
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




#: Category words that describe WHAT an item is rather than WHICH one. Searching one of these
#: matches half the bank, so search_query drops them when anything else is left.
GENERIC_ITEM_WORDS = frozenset((
    "teleport", "teleports", "ring", "rings", "potion", "potions", "scroll", "scrolls",
    "amulet", "necklace", "staff", "cape", "talisman", "tablet", "pouch", "seed", "of", "to",
    "the", "a", "an",
))


def search_query(name: str) -> str:
    """The text to type into the bank search to bring `name` into view: its one distinctive WORD.

    FOUND LIVE 2026-09-21. The first time the search fallback fired it typed the full item name,
    "Ring of dueling(3)", and the box received `ring of dueling3` -- the USER read it off the
    screen. The firmware's keyFromName has no parentheses (a shifted digit) and no apostrophe, so
    those characters vanish silently and the mangled query matches nothing.

    USER: "just search like this: dueling". One word is right for more reasons than the
    parentheses. It contains nothing the firmware can mangle, it is short to type, and it matches
    every charge variant at once -- the caller then picks the exact item out of the filtered list
    the way it already does, because the bank search is a substring match.

    The LONGEST alphabetic word is reliably the distinctive one: "dueling" over "ring",
    "karambwan" over "cooked", "talisman" over "xeric". A name with no typeable word at all falls
    back to itself rather than to an empty query, which would clear the filter and show the whole
    bank.
    """
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", name or "")]
    if not words:
        return name
    # Skip the CATEGORY half of the name. FOUND LIVE 2026-09-21: "longest" sent "teleport" for
    # "Varrock teleport", a query matching hundreds of items in a 909-item bank, and the withdraw
    # took nothing -- the trip stopped with "no varrock teleport in inventory". Longest was only
    # ever a proxy for MOST DISTINCTIVE, and it breaks whenever the generic half is the longer
    # one. Item names pair a place or a kind with a category word, so dropping the category words
    # is what makes the proxy hold: "Varrock teleport" -> varrock, "Teleport to house" -> house,
    # "Slayer ring" -> slayer, while the live-proven "Ring of dueling" -> dueling is unchanged.
    distinctive = [w for w in words if w not in GENERIC_ITEM_WORDS]
    return max(distinctive or words, key=len)


def _reachable(slot) -> bool:
    """Is this bank slot's drawn position somewhere we could actually click?

    Fails OPEN when the position is missing -- the same rule controller.is_point_within_window
    itself follows, since "cannot tell" must not block a withdraw that would have worked.
    """
    x, y = slot.get("screenX"), slot.get("screenY")
    if x is None or y is None:
        return True
    return controller.is_point_within_window(x, y)


def withdraw_loadout(ctx, config: dict, fresh: bool = False) -> None:
    """fresh: forces this call's /bank reads to bypass the bridge's per-tick cache -- pass True
    when the bank state may have just changed from something outside this call (e.g. a separate
    withdraw_loadout call for a different item immediately before this one) and waiting out a
    full game tick for the cache to reflect it isn't acceptable. See controller.get_bank's own
    fresh kwarg."""
    entries = [e for e in config.get("withdraw", []) if e.get("name")
               and (e.get("all") or e.get("quantity", 1) > 0)]

    if any(not e.get("all") for e in entries):
        set_quantity_mode(ctx, "one")

    for entry in entries:
        name = entry["name"].lower()
        quantity = entry.get("quantity", 1)

        # {"name": ..., "all": True} (USER 2026-09-05, firemaking): one shift-click withdraws
        # as much as fits instead of one click per unit -- 27 log clicks was the alternative.
        # Same "as much of this single item as fits" contract as withdraw_all_shift itself.
        if entry.get("all"):
            withdraw_all_shift(ctx, entry["name"], fresh=True)
            continue

        # Always fresh: see withdraw_all_shift's find_slot for the one-slot-off stale read
        # that withdrew the wrong item live (2026-09-04). `fresh` is kept for callers.
        bank = controller.get_bank(fresh=True)
        slot = next(
            (s for s in bank.get("items", []) if name in (s.get("name") or "").lower()),
            None,
        )
        # SCROLLED OUT OF VIEW, found live 2026-09-21 on the first slayer bank trip: "Skipping
        # click on Ring of dueling(3) -- aim point is outside the RuneLite window", and the trip
        # then reported itself short. /bank gives an item's DRAWN position, and in a ~900-item bank
        # a single low-charge ring sits far down a scrolled list, so that position is off-window.
        # search_bank re-lays the matches from slot 0 -- the same mechanism giants_foundry has used
        # live since 2026-09-03. USER: "just search for it".
        #
        # A FALLBACK, not the normal path: every routine shares this function, and searching costs
        # a click and a retype on every withdraw that never needed one.
        searched = False
        if slot is None or not _reachable(slot):
            searched = search_bank(ctx, search_query(entry["name"]))
            if searched:
                bank = controller.get_bank(fresh=True)
                slot = next(
                    (s for s in bank.get("items", []) if name in (s.get("name") or "").lower()),
                    None,
                )

        if slot is None:
            debug_log.log("bank", f"{entry.get('name')} not found in bank, skipping")
            continue
        if not _reachable(slot):
            debug_log.log("bank", f"{slot.get('name')} is still off-screen after searching, skipping")
            continue

        debug_log.log("bank", f"withdrawing {quantity}x {slot.get('name')}")
        for _ in range(quantity):
            controller.click_entity(ctx.ser, slot, check_ui_blockers=False)
            controller.human_delay(0.15, 0.3)

        # CLEAR THE FILTER WE PUT UP, now that this item is taken. The search icon is a TOGGLE, so
        # a filter left active means the NEXT item's lookup sees only this query's matches, and
        # the click that would start its search closes this box instead of opening a new one.
        # FOUND LIVE 2026-09-21: "withdrawing 1x Ectophial", then "Ring of dueling(8) not found in
        # bank" for a ring the account holds fifteen of.
        #
        # AFTER, not before. Clearing first was tried and the next run showed it is worse: with no
        # filter up, clear turns one ON and search turns it straight back OFF. This is the side
        # golem_crafting and giants_foundry already clear on.
        if searched:
            clear_bank_search(ctx)

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
    name_lower = name.lower()
    del fresh  # kept for callers; every read below is fresh regardless (see find_slot)

    def find_slot():
        # ALWAYS fresh (2026-09-04, bf_live13): /bank is cached per game tick, and the deposit
        # that precedes every withdrawal can add a NEW item type to the bank, shifting every
        # later slot by one. A stale snapshot aimed the shift-click one slot off and withdrew
        # ALL of the neighbour (11 Mithril chainbodies) instead of the ore -- the caller then
        # safety-stopped on "never showed up". One un-cached read is cheap; a wrong click is not.
        bank = controller.get_bank(fresh=True)
        slot = next(
            (s for s in bank.get("items", []) if name_lower == (s.get("name") or "").lower()),
            None,
        )
        if slot is None:
            slot = next(
                (s for s in bank.get("items", []) if name_lower in (s.get("name") or "").lower()),
                None,
            )
        return slot

    for attempt in range(2):
        slot = find_slot()
        if slot is None:
            debug_log.log("bank", f"{name} not found in bank, skipping")
            return False

        debug_log.log("bank", f"shift-withdrawing all {slot.get('name')}")
        controller.key_down(ctx.ser, "SHIFT")
        ok = controller.click_entity(ctx.ser, slot, check_ui_blockers=False, simulate_mistakes=False)
        controller.key_up(ctx.ser, "SHIFT")
        if not ok:
            return False

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(name_lower in (i.get("name") or "").lower() for i in controller.get_inventory()):
                return True
            time.sleep(0.15)
        debug_log.log("bank", f"shift-withdrawn {name} never showed up in inventory"
                              + (" -- re-reading the bank and retrying" if attempt == 0
                                 else " -- may have raced the bank closing"))
    return False


# Bank UI buttons (search icon, quantity mode, deposit-all) are TOGGLES or one-shot
# switches: a simulated misclick plus its corrective reclick is two clicks, which flips a
# toggle twice. MEASURED live 2026-09-03 (giants_foundry gf_live30, USER: "the search never
# got typed adam"): misclick + reclick on the search icon opened and closed the search box
# and the query was typed into nothing. simulate_mistakes=False on every one of them.
# How many times the query is retyped when the bank list does not react to it. See search_bank:
# the box demonstrably opens and the keystrokes are what go missing, so retyping into the
# already-open box is the cheap fix; re-clicking the icon would TOGGLE it shut.
SEARCH_TYPE_TRIES = 3


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

    unfiltered = len(bank.get("items") or [])

    controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False,
                          simulate_mistakes=False)
    controller.human_delay(0.2, 0.4)

    # THE TYPING IS VERIFIED, NOT ASSUMED (2026-09-16). USER, watching the screen: "Second time i
    # see it not writing anything when searching" / "the type never worked fur never came up in
    # the searchbar" / "Yes it did open" -- so the icon click lands and the box opens, and the
    # KEYSTROKES are what go missing. MEASURED live the same morning: 'f','u','r' were sent at
    # 07:49:03.27/.36/.45, and a frame dumped straight after showed the bank holding all **948
    # items, unfiltered**, with an empty search box. The routine then read the unfiltered list,
    # found "Dark kebbit fur" at a screenY far below the window, and refused the click with "aim
    # point is outside the RuneLite window" -- the off-window skip is the DOWNSTREAM SYMPTOM of a
    # search that never filtered, not a problem with the fur slot.
    #
    # Dropped keystrokes are already a known hazard on this input path: _withdraw_coins carries
    # "the 'Enter amount' chatbox needs a moment to take keyboard focus (CONFIRMED live in the
    # mixology bank, 2026-08-12: digits typed immediately were dropped)". But the delay is NOT the
    # discriminator here -- measured across 180 searches in the live logs, successful ones span
    # 315-661ms between the click and the first keystroke, straddling the 460ms of the failure.
    # So rather than guess at a longer sleep, this now checks the one thing that actually matters
    # and retries: a search that filtered nothing has not happened, whatever the reason.
    #
    # Filtering is the signal because it is unambiguous and free: a real query cuts a ~950-item
    # bank to a handful, while an empty box leaves the count exactly where it was.
    for attempt in range(SEARCH_TYPE_TRIES):
        controller.type_text(ctx.ser, query)
        # CONFIRMED live 2026-08-12 (Mastering Mixology live session): a 0.3-0.5s settle here --
        # shorter than one full game tick (0.6s), with no margin -- reproduced a real bug twice in
        # a row on the THIRD back-to-back search within one bank session: /bank's own read already
        # reflected the new query (the caller's item-lookup found the right name/id), but the
        # actual on-screen click still landed on the PREVIOUS search's result instead, silently
        # withdrawing the wrong item. Bumped to comfortably clear a full tick with margin.
        controller.human_delay(0.7, 0.9)
        # fresh=True: /bank is a BULK endpoint behind TickCache while the box filters LIVE, so a
        # cached read here would answer with the pre-search list and fail every check.
        # CHANGED, not merely smaller: a caller can search again while an earlier filter is still
        # active (_withdraw_kit does, item after item), and the new query's match count can be
        # larger than the old one's. An empty box is the case this has to catch, and that leaves
        # the count EXACTLY where it was.
        filtered = len(controller.get_bank(fresh=True).get("items") or [])
        if filtered != unfiltered:
            return True
        if attempt + 1 < SEARCH_TYPE_TRIES:
            debug_log.log("bank", f"searched '{query}' but the bank still lists {filtered} items "
                                  f"-- the keystrokes did not reach the box, retyping "
                                  f"({attempt + 1}/{SEARCH_TYPE_TRIES})")
    debug_log.log("bank", f"[!] could not get '{query}' into the bank search box after "
                          f"{SEARCH_TYPE_TRIES} attempts -- leaving the search alone")
    return False


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

    controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False,
                          simulate_mistakes=False)
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
        if controller.click_entity(ctx.ser, slot, check_ui_blockers=False):
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


# Where a bank trip records the tile it left from, so it can walk back to it afterwards.
# USER-REPORTED 2026-09-07 ("ends up stuck at Ferox"): before this, the ONLY save/restore
# machinery in the file lived inside _bank_via_ferox, so a trip to a bank within
# maxBankDistance (40) walked there and simply carried on fighting next to it -- and the Ferox
# path's own restore was popped BEFORE the walk was attempted, so one walk that gave up partway
# (WALK_MAX_STALLS is a cumulative budget of 4 across a whole cross-map route) discarded the
# destination with no possibility of a retry. The anchor is now recorded once at the top of
# every trip, whichever branch it takes, and survives a failed walk so a later tick retries.
RETURN_LOCATION_KEY = "bank_return_location"
RETURN_ATTEMPTS_KEY = "bank_return_attempts"

# Set the moment travel.teleport_to_ferox succeeds, cleared when the trip completes.
# FEROX_BANK_NAMES is a strict SUBSET of DEFAULT_BANK_NAMES, and the Java panel exports neither
# bankNames nor maxBankDistance (so both defaults always apply) -- meaning that once a trip
# aborted after the teleport had already landed (chest not visible yet, or open_bank failed),
# the next tick's bank_up matched the Ferox chest on its own nearby-bank branch and banked
# there, skipping use_fountain AND the walk home. This latch keeps a trip that has teleported
# on the Ferox path until it finishes.
TELEPORTED_KEY = "bank_teleported_to_ferox"

# Walks home attempted before giving up. A ceiling, because a stranded routine is exactly the
# reported bug and CLAUDE.md's standing lesson is that a silent loop is worse than a crash --
# so the ceiling surfaces as a safety stop rather than a shrug.
RETURN_WALK_MAX_ATTEMPTS = 3


RETURN_TELEPORT_KEY = "bank_return_teleport"

# The leg runner moved to legs.py 2026-09-18; these names stay for this file's readers.
RETURN_TELEPORT_BUDGET_S = legs.ITEM_BUDGET_S
RETURN_TELEPORT_POLL_S = legs.POLL_S
RETURN_TELEPORT_ARRIVAL_TILES = legs.ARRIVAL_TILES
PICKER_BUDGET_S = legs.PICKER_BUDGET_S

# One flood from the anchor replaces a cross-map search per candidate teleport. The radius
# doubles as the rule "a landing further than this from home is not worth teleporting to".
GOAL_FLOOD_RADIUS = 200
GOAL_FLOOD_MAX_NODES = 400000

# An "approach" is the short walk from where a hop lands to the object that makes the NEXT hop
# (arrival tile -> jewellery box, a few dozen tiles inside a house). Bounded hard, because the
# static map treats missing data as PASSABLE and so joins every dungeon, instance and POH
# template into one giant connected VOID -- MEASURED 2026-09-07: unbounded, the planner spent
# 78s inventing hundreds-of-tiles routes between unrelated underground coordinates, and the
# nonsense outranked a ring of dueling that teleported straight home. A real approach is short;
# anything long is the void, not a walk.
APPROACH_MAX_TILES = 60
# Bounds the SEARCH as well as the answer: a short approach resolves well inside this, while a
# void crawl gives up in milliseconds instead of minutes.
APPROACH_MAX_NODES = 20000


def _item_rows(payload):
    """Item dicts out of either bridge shape.

    /inventory is a LIST of items; /equipment is a DICT KEYED BY SLOT whose values are the
    items. Iterating the dict yields slot NAMES, so treating them alike silently contributed
    nothing from equipment -- caught 2026-09-07 when a worn-item lookup raised on a str.
    """
    if isinstance(payload, dict):
        return [v for v in payload.values() if isinstance(v, dict)]
    return [i for i in (payload or []) if isinstance(i, dict)]


def _owned_item_ids(config):
    """Every item id we could get our hands on this trip: banked, carried, or worn.

    Bank memory is what makes the banked half answerable at all -- /bank can only report what
    the interface is currently drawing, so an active search or a non-active tab would hide the
    very teleport we are looking for (see BankMemoryProvider's header).
    """
    ids = set()

    memory = bank_memory.load()
    if memory.seen:
        for item in memory.items:
            try:
                ids.add(int(item.get("id")))
            except (TypeError, ValueError):
                continue

    for source in (controller.get_inventory(), controller.get_equipment()):
        for item in _item_rows(source):
            try:
                ids.add(int(item.get("id")))
            except (TypeError, ValueError):
                continue

    return ids


def _route_length(sx, sy, plane, gx, gy, max_nodes=None):
    """Walk length in tiles, or None when there is no route. Same-plane only, like the map.

    `max_nodes` bounds the SEARCH, not just the answer. That matters because the static map
    treats missing data as passable, so a search between two unrelated underground coordinates
    crawls an enormous connected void before failing -- capping the node budget makes that give
    up in milliseconds while a genuine short approach still resolves.
    """
    try:
        if max_nodes is None:
            route = worldmap.find_path(sx, sy, plane, gx, gy, goal_adjacent=True)
        else:
            route = worldmap.find_path(sx, sy, plane, gx, gy, goal_adjacent=True,
                                       max_nodes=max_nodes)
    except Exception:
        return None
    return len(route) if route else None


def _poh_config(config):
    """What is built in the player's house, off the bridge config's `poh` block.

    Absent means everything OFF -- an unconfigured account must never route through a house it
    may not have. The client cannot see inside a POH, which is why this is configuration at all
    (Shortest Path does the same; see ControllerConfig's Player-Owned House section).
    """
    poh = (config or {}).get("poh") or {}
    return teleports.PohConfig(
        enabled=bool(poh.get("enabled")),
        box_tier=poh.get("boxTier", "NONE"),
        portals=bool(poh.get("portals")),
        fairy_ring=bool(poh.get("fairyRing")),
        mounted_items=bool(poh.get("mountedItems")),
    )


def _referenced_varbits(owned_ids=None):
    """Live values for the varbits that rows we could ACTUALLY take depend on.

    One read per row would be ~450 HTTP calls, and the whole table references 86 distinct
    varbits -- still ~1.7s of round trips for gates on teleports we do not own. Narrowing to
    rows whose items we hold cuts it to a handful. Every row that needs a varbit needs it
    ANSWERED or it is rejected (teleports.varbits_satisfied -- unknown FAILS, deliberately),
    so under-reading here can only lose a route, never invent one.
    """
    have = set(owned_ids) if owned_ids is not None else None
    wanted = set()
    for t in teleports.load_all():
        if have is not None and t.item_ids and not (t.item_ids & have):
            continue
        for vid, _op, _val in t.varbits:
            wanted.add(vid)

    values = {}
    for vid in sorted(wanted):
        # get_varbit returns the VALUE, not a {"value": n} envelope -- and None means "could
        # not read", never zero (its own docstring says so). An earlier cut checked for a dict
        # here and so silently read nothing, which rejected every varbit-gated row.
        value = controller.get_varbit(vid)
        if value is not None:
            values[vid] = value
    return values


_leg_dict = legs.from_transport
_trailing_int = legs.trailing_int


def _plan_return_teleport(ctx, config):
    """The cheapest chain of transports home, or None to just walk.

    Called INSIDE the bank, because that is the only moment an item leg can be withdrawn.
    """
    if not config.get("returnToSpot"):
        return None

    anchor = ctx.state.data.get(RETURN_LOCATION_KEY)
    if not anchor:
        return None

    player = controller.get_player() or {}
    px, py, plane = player.get("x"), player.get("y"), player.get("plane", 0)
    if px is None:
        return None

    owned = _owned_item_ids(config)
    goal = (anchor["x"], anchor["y"], anchor.get("plane", 0))

    # ONE flood from the goal, not N cross-map searches. MEASURED 2026-09-07: with a 913-item
    # bank unlocking dozens of candidate teleports, one find_path per candidate cost 106s for a
    # single decision. reachable_from answers {(x, y): steps} for the whole neighbourhood in a
    # single pass, so "how far is this landing spot from home" becomes a dict lookup.
    #
    # The radius therefore doubles as the product rule: a teleport landing further than
    # GOAL_FLOOD_RADIUS from the anchor is not worth taking, and simply reads as no route.
    try:
        goal_reach = worldmap.reachable_from(goal[0], goal[1], goal[2],
                                             radius=GOAL_FLOOD_RADIUS,
                                             max_nodes=GOAL_FLOOD_MAX_NODES)
    except Exception:
        goal_reach = {}

    approach_cache = {}

    def route_len(a, b):
        if b[:2] == goal[:2] and b[2] == goal[2]:
            return goal_reach.get((a[0], a[1])) if a[2] == goal[2] else None
        # The only other question asked is "can I walk from here to this edge's origin", and
        # origins are few and shared (every jewellery box row has the same one), so a plain
        # cache over real searches is enough.
        key = (a[:2], b[:2])
        if key not in approach_cache:
            n = (_route_length(a[0], a[1], a[2], b[0], b[1],
                               max_nodes=APPROACH_MAX_NODES) if a[2] == b[2] else None)
            approach_cache[key] = n if (n is not None and n <= APPROACH_MAX_TILES) else None
        return approach_cache[key]

    return teleports.plan(
        (px, py, plane), goal,
        owned_ids=owned,
        route_len=route_len,
        varbit_values=_referenced_varbits(owned),
        poh=_poh_config(config),
        wilderness_level=player.get("wildernessLevel", 0) or 0,
    )


def _withdraw_return_teleport(ctx, config):
    """Plan the way home and make sure the FIRST leg's item is carried before the bank closes.

    Later legs are objects standing in the world (a jewellery box, a portal) and need nothing
    withdrawn. Never fatal: any failure here just means we walk, exactly as before this existed.
    """
    try:
        legs = _plan_return_teleport(ctx, config)
    except Exception as exc:
        debug_log.log("bank", f"couldn't plan a route home ({exc}) -- will walk")
        return

    if not legs:
        return

    first = legs[0]
    if first.kind == "item" and first.item_name:
        carried = {(i.get("name") or "").lower() for i in _item_rows(controller.get_inventory())}
        worn = {(i.get("name") or "").lower() for i in _item_rows(controller.get_equipment())}
        name = first.item_name.lower()
        if not (any(name in n for n in carried) or any(name in n for n in worn)):
            debug_log.log("bank", f"withdrawing {first.item_name} to travel home")
            if not search_and_withdraw(ctx, first.item_name, 1):
                debug_log.log("bank", f"couldn't withdraw {first.item_name} -- walking instead")
                return

    debug_log.log("bank", "route home: " + " -> ".join(l.display for l in legs))
    ctx.state.data[RETURN_TELEPORT_KEY] = {"legs": [_leg_dict(l) for l in legs]}


# Moved to legs.py (2026-09-18). Aliases so _use_return_teleport and the tests that monkeypatch
# banker._run_leg keep working; the "bank" log tag becomes legs' default "travel". Only
# banker._run_leg is a meaningful monkeypatch point -- it's the one _use_return_teleport actually
# calls. Patching banker._run_item_leg, banker._await_arrival, or the RETURN_TELEPORT_*/
# PICKER_BUDGET_S constant copies further up this file has NO effect on behaviour: legs.run_leg
# calls its OWN module's functions (legs.run_item_leg, legs.await_arrival, ...) and reads its
# own constants, never these aliases -- patch legs.* instead.
_await_arrival = legs.await_arrival
_run_leg = legs.run_leg
_run_item_leg = legs.run_item_leg
_run_object_leg = legs.run_object_leg
_pick_destination = legs.pick_destination


def _use_return_teleport(ctx):
    """Run the planned chain, leg by leg. True only if every leg landed.

    The plan is popped HERE, not by the caller: consumed exactly once whether or not it fires,
    so a dud can never be retried into an empty inventory -- and a caller that popped it first
    would leave this reading nothing at all (the 2026-09-07 regression, pinned by test).
    """
    plan = ctx.state.data.pop(RETURN_TELEPORT_KEY, None)
    if not plan:
        return False

    for leg in plan.get("legs") or []:
        if not _run_leg(ctx, leg):
            debug_log.log("bank", f"leg '{leg.get('display')}' didn't land "
                                  f"-- walking home from here")
            return False
    return True


def return_to_spot(ctx) -> bool:
    """Walk back to the tile the current bank trip left from, if one is still pending.

    True when there is nothing left to do (no anchor recorded, or we made it back). False while
    a retry is still owed -- the anchor stays in ctx.state.data so the caller's next tick tries
    again, rather than fighting on from wherever the walk happened to stop. Cheap and safe to
    call every tick: with no anchor recorded it does nothing at all.
    """
    target = ctx.state.data.get(RETURN_LOCATION_KEY)
    if target is None:
        return True

    attempts = ctx.state.data.get(RETURN_ATTEMPTS_KEY, 0) + 1
    ctx.state.data[RETURN_ATTEMPTS_KEY] = attempts

    # A teleport planned at the bank shortens the walk; it is consumed once, whether or not it
    # fires, so a dud cannot be retried into an empty inventory. Failing is not fatal by design:
    # the anchor survives and the walk below runs exactly as it did before teleports existed.
    if ctx.state.data.get(RETURN_TELEPORT_KEY) is not None:
        _use_return_teleport(ctx)
    debug_log.log("bank", f"walking back to where we left from "
                          f"({target['x']},{target['y']},{target.get('plane', 0)}) "
                          f"-- attempt {attempts}/{RETURN_WALK_MAX_ATTEMPTS}")

    if travel.walk_to(ctx, target, goal_adjacent=False):
        ctx.state.data.pop(RETURN_LOCATION_KEY, None)
        ctx.state.data.pop(RETURN_ATTEMPTS_KEY, None)
        return True

    if attempts >= RETURN_WALK_MAX_ATTEMPTS:
        ctx.state.data.pop(RETURN_LOCATION_KEY, None)
        ctx.state.data.pop(RETURN_ATTEMPTS_KEY, None)
        ctx.state.trigger_safety_stop(
            f"banked, but couldn't walk back to ({target['x']},{target['y']}) after "
            f"{attempts} attempts -- stranded somewhere the routine didn't choose"
        )
        return False

    debug_log.log("bank", "couldn't walk all the way back -- retrying next tick")
    return False


def bank_up(ctx, config: dict) -> bool:
    """
    Full cycle: bank at whatever's within walking distance if anything is,
    otherwise teleport to Ferox Enclave via the ring of dueling, bank and
    use the fountain there. Either way: deposit per keepItems, withdraw the
    configured loadout, close, and -- for the Ferox path, or for any caller passing
    returnToSpot -- walk back to the tile we left from.
    """
    if not config.get("enabled", True):
        return False

    # Recorded once per trip, BEFORE anything moves the player, and kept across ticks so a trip
    # spanning several of them (or one that aborts and re-enters) still knows where it started.
    if RETURN_LOCATION_KEY not in ctx.state.data:
        saved_location = travel.save_location()
        if saved_location is not None:
            ctx.state.data[RETURN_LOCATION_KEY] = saved_location
            ctx.state.data[RETURN_ATTEMPTS_KEY] = 0
        else:
            debug_log.log("bank", "couldn't record current location -- banking without a way back")

    if ctx.state.data.get(TELEPORTED_KEY):
        # Already teleported this trip: stay on the Ferox path, so neither the fountain step nor
        # the ring-charge reasoning is skipped by the nearby-bank branch matching the very chest
        # we teleported to. See TELEPORTED_KEY.
        if not _bank_via_ferox(ctx, config):
            return False
    else:
        # Retried, not a single check -- see _find_nearby_bank_retrying's own docstring (a
        # transient /objects timeout here would otherwise wrongly fall through to the
        # teleport-to-Ferox path below even when a real bank is right there).
        bank_obj = _find_nearby_bank_retrying(config)
        if bank_obj is not None:
            debug_log.log("bank", f"walking to {bank_obj.get('name')} (dist={bank_obj.get('distance')})")
            if not open_bank(ctx, bank_obj):
                debug_log.log("bank", "failed to open bank")
                return False

            deposit_inventory(ctx, config)
            withdraw_loadout(ctx, config)
            # Chosen and withdrawn HERE because an open bank is the only moment we can take the
            # item; return_to_spot uses it and walks whatever is left.
            _withdraw_return_teleport(ctx, config)
            close_bank(ctx)
        elif not config.get("teleportFallback", True):
            return False
        elif not _bank_via_ferox(ctx, config):
            return False

    teleported = bool(ctx.state.data.pop(TELEPORTED_KEY, None))

    # Default preserves every pre-2026-09-07 caller exactly: the Ferox path always walked back
    # (golem_crafting's own comment depends on it), the nearby-bank path never did. Opting in
    # extends the walk home to the nearby-bank path too -- which routines/slayer.py wants and
    # routines/firemaking.py must NOT have, since it banks every inventory and then walks to a
    # fixed starter tile of its own (advance_starter), so a walk back to the end of the last
    # burn line would be pure waste.
    if not config.get("returnToSpot", teleported):
        ctx.state.data.pop(RETURN_LOCATION_KEY, None)
        ctx.state.data.pop(RETURN_ATTEMPTS_KEY, None)
        return True

    return_to_spot(ctx)
    return True


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
    # and use it normally.
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
    if bank_obj is None and in_ferox(controller.get_player()):
        # ALREADY THERE. The scan missing the chest is not evidence of being somewhere else, and
        # teleporting again lands in the same square a charge poorer. See FEROX_AREA for the live
        # run that spent a whole ring this way.
        debug_log.log("bank", "already inside Ferox -- not teleporting again, the chest is a walk")
        return False
    if bank_obj is None:
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

        # Latched the moment the teleport lands, so an abort below can't leave the next tick's
        # bank_up believing it is still standing at the task and free to use its nearby-bank
        # branch on the Ferox chest itself.
        ctx.state.data[TELEPORTED_KEY] = True

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
    _withdraw_return_teleport(ctx, config)
    close_bank(ctx)

    travel.use_fountain(ctx)
    return True
