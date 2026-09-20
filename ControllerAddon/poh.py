# poh.py -- the player-owned house as a travel hub: break a tablet, drink from the pool, leave
# through the ornate jewellery box or the house fairy ring. Added 2026-09-20 (slayer spec phase
# B), UNVERIFIED LIVE -- nothing here has run against a real client yet.
#
# Facts MEASURED LIVE on this account, from the phase B brief (build fixtures from THESE shapes):
#
#   /player inside the house carries `inInstance: true` plus an INSTANCE-space pair `instX`/
#   `instY`, alongside the usual template `x`/`y`/`plane`. `player=None`-defaulted functions
#   read a fresh `controller.get_player()`.
#
#   /objects?radius=..&fresh=true records inside the house have `distance` PEGGED at
#   2147483647 on every single one -- it must never be sorted or filtered on -- and their
#   template x/y LIE about relative distance (a multi-tile object like the pool or the fairy
#   ring appears once per tile, at DIFFERENT template coordinates, all equally close in reality).
#   `instX`/`instY` are the only real geometry inside an instance; Chebyshev distance on those
#   is `inst_distance` below.
#
#   The ornate jewellery box (id 29156) offers `Teleport Menu / Farming Guild / Remove` --
#   the middle action is whatever the box was last used for and may answer a plain left-click,
#   so `Teleport Menu` is ALWAYS taken through the menu, never a bare click on the box. Its
#   destination picker is widget group 590: a destination entry has an action equal to the
#   destination name and its text does NOT contain "<str>"; a locked (struck-through) entry has
#   the action but "<str>" IS in its text.
#
#   The fairy ring (id 29228) is an ordinary `travel.hop_fairy_ring` target once its id is
#   passed as `ring_ids`. The exit portal (4525) and any of the pool's several ids
#   (`29241, 29240, 29239, 29238, 29237` -- Microbot-Hub's list; 29239 measured on this account)
#   are what makes an instance recognisable as a HOUSE rather than some other instance (a raid,
#   Pest Control, the Gauntlet) -- `in_house` requires one of them to be in the scene, not just
#   `inInstance`.
#
#   House tablet: inventory item named "Teleport to house" (id 8013), left-click option "Break",
#   ~4.1s to land with two loading flickers on the way (a failed bridge read during that window
#   means "not yet", not "failed").
#
# Every outcome here is confirmed by a bridge read; a failed confirmation returns False (except
# the pool, a convenience that never blocks travel) so the caller's own ceilings stop loudly
# instead of this module silently pretending an unconfirmed click worked.

import time

import controller
import debug_log
import travel

TABLET = "teleport to house"
BOX_ID, RING_ID, EXIT_PORTAL_ID = 29156, 29228, 4525
POOL_IDS = (29241, 29240, 29239, 29238, 29237)
BOX_GROUP = 590
BOX_MENU_OPTION = "Teleport Menu"
ENTER_BUDGET_S, POOL_BUDGET_S, BOX_MENU_BUDGET_S = 10.0, 6.0, 8.0
POLL_S = 0.3
SCAN_RADIUS = 40
TABLET_COOLDOWN_S = 60.0       # never break a second tablet this soon after the last one
HOUSE_REGIONS = (7791,)        # MEASURED 2026-09-20 on this account's house
HOUSE_TEMPLATE_BOX = (1850, 2050, 7000, 7200)   # x0, x1, y0, y1 -- the POH template chunks
TELEPORT_INSIDE_VARBIT = 4744  # vendor/teleportation_items.tsv: 0 = a plain Break lands INSIDE


def inst_distance(a, b):
    """Chebyshev distance on instX/instY -- the only real geometry inside an instance (the
    brief's own measurement: template x/y differ per tile of a multi-tile object even though
    every tile is equally close). None if either side lacks instance coordinates."""
    ax, ay = (a or {}).get("instX"), (a or {}).get("instY")
    bx, by = (b or {}).get("instX"), (b or {}).get("instY")
    if ax is None or ay is None or bx is None or by is None:
        return None
    return max(abs(ax - bx), abs(ay - by))


def find(ids, player=None):
    """The clickable record among `ids` nearest the player by INSTANCE distance -- never by the
    bulk scan's own `distance` field, which is a constant 2147483647 on every house object.
    Records with no instance coordinates sort last; ties keep the scan's own order (first)."""
    if player is None:
        player = controller.get_player() or {}
    candidates = [o for o in (controller.get_objects(radius=SCAN_RADIUS, fresh=True) or [])
                  if o.get("id") in ids and o.get("clickable") and o.get("screenX") is not None]
    if not candidates:
        return None

    def sort_key(o):
        d = inst_distance(player, o)
        return (d is None, d if d is not None else 0)

    candidates.sort(key=sort_key)
    return candidates[0]


def is_house_player(player) -> bool:
    """PURE: does this /player record say "inside a player-owned house"? An instance alone is not
    a house (raids, Pest Control, the Gauntlet and a Sailing boat are instances too), so it must
    also sit in a POH template region or inside the POH template box.

    PHASE-B REVIEW: the first draft proved a house by scanning /objects for house furniture --
    3,674 objects and 1.1 MB per read, polled every 0.3 s, with a 1 s timeout whose [] reads
    exactly like "no house object". A stuttering client during the two LOADING flickers would
    have left in_house() False after a GOOD teleport, and the travel ceiling would then have
    broken three tablets. /player alone answers it, and costs nothing."""
    if not player or not player.get("inInstance"):
        return False
    if player.get("regionId") in HOUSE_REGIONS:
        return True
    x, y = player.get("x"), player.get("y")
    x0, x1, y0, y1 = HOUSE_TEMPLATE_BOX
    return x is not None and y is not None and x0 <= x <= x1 and y0 <= y <= y1


def in_house(player=None) -> bool:
    if player is None:
        player = controller.get_player()
    return is_house_player(player)


def enter(ctx, log: str = "poh") -> bool:
    """Break a house tablet if we are not already inside. True once `in_house` confirms it."""
    if in_house():
        return True
    item = next((i for i in (controller.get_inventory() or [])
                 if TABLET in (i.get("name") or "").lower()), None)
    if item is None:
        debug_log.log(log, "no house tablet in inventory")
        return False
    # A tablet is spent by the click, whether or not the landing confirms. The travel phase
    # retries a FAILED leg, so without this a landing we cannot confirm costs three tablets.
    since = time.time() - ctx.state.data.get("poh_tablet_at", 0.0)
    if since < TABLET_COOLDOWN_S:
        debug_log.log(log, f"a house tablet was broken {since:.0f}s ago and the house was never "
                           f"confirmed -- not breaking another")
        return False
    # 4744 = 1 means a plain Break lands OUTSIDE at the portal (its "(Inside)" row is a separate
    # menu option), where in_house() stays False forever. None = unreadable: carry on.
    setting = controller.get_varbit(TELEPORT_INSIDE_VARBIT)
    if setting not in (None, 0):
        debug_log.log(log, f"varbit {TELEPORT_INSIDE_VARBIT} = {setting}: house teleports land "
                           f"OUTSIDE the portal on this account -- refusing to break a tablet")
        return False
    controller.open_inventory_tab(ctx.ser)
    ok = controller.click_entity(ctx.ser, item, check_ui_blockers=False,
                                 simulate_mistakes=False, expected_action="Break")
    if not ok:
        if not getattr(ok, "withheld", False):
            debug_log.log(log, "breaking the house tablet failed")
            return False
        debug_log.log(log, "house tablet: left-click isn't Break -- using the menu")
        ok = controller.click_menu_action(ctx.ser, item, "Break", check_ui_blockers=False)
        if not ok:
            debug_log.log(log, "breaking the house tablet failed (menu)")
            return False
    # The tablet is consumed by the click itself, whether or not the landing later confirms.
    ctx.state.data["poh_tablets_used"] = ctx.state.data.get("poh_tablets_used", 0) + 1
    ctx.state.data["poh_tablet_at"] = time.time()
    deadline = time.time() + ENTER_BUDGET_S
    while time.time() < deadline:
        if in_house():
            return True
        time.sleep(POLL_S)
    debug_log.log(log, f"never confirmed inside the house within {ENTER_BUDGET_S}s of breaking the tablet")
    return False


def _needs_drink(player) -> bool:
    """Missing values never demand a drink on their own -- only a value that IS present and low
    does. All-unknown therefore reads as "already full", matching the brief's "skip with True"."""
    prayer, prayer_real = player.get("prayer"), player.get("prayerReal")
    run = player.get("runEnergy")
    prayer_low = prayer is not None and prayer_real is not None and prayer < prayer_real
    run_low = run is not None and run < 90
    return prayer_low or run_low


def drink_pool(ctx, log: str = "poh") -> bool:
    """Top up at the house pool. Always True -- a missing pool, a failed click, or a prayer that
    never rises are all logged but never travel failures; the pool is a convenience."""
    player = controller.get_player() or {}
    if not _needs_drink(player):
        return True
    pool = find(POOL_IDS)
    if pool is None:
        debug_log.log(log, "no pool in this house -- skipping the drink")
        return True
    refresh = controller.object_refresh_target(pool.get("x"), pool.get("y"), pool.get("plane", 0),
                                               pool.get("id"))
    ok = controller.click_entity(ctx.ser, pool, expected_action="Drink", refresh_target=refresh,
                                 simulate_mistakes=False)
    if not ok:
        if getattr(ok, "withheld", False):
            ok = controller.click_menu_action(ctx.ser, pool, "Drink", refresh_target=refresh)
        if not ok:
            debug_log.log(log, "the pool's Drink click failed")
            return True
    deadline = time.time() + POOL_BUDGET_S
    risen = False
    while time.time() < deadline:
        p = controller.get_player() or {}
        # BOTH bars: with only run energy low, breaking out on a full prayer bar returned before
        # the player had even reached the pool, and the next leg's click cancelled the drink.
        if not _needs_drink(p) and p:
            risen = True
            break
        time.sleep(POLL_S)
    if not risen:
        debug_log.log(log, "prayer/run never read full after drinking -- carrying on anyway")
    return True


def _box_matches(dump, destination):
    """Every group-590 entry whose action equals `destination` (case-insensitive, exact)."""
    wanted = (destination or "").strip().lower()
    return [w for w in (dump or [])
            if w.get("group") == BOX_GROUP
            and any((a or "").strip().lower() == wanted for a in (w.get("actions") or ()))]


def box_entry(dump, destination):
    """The ONE group-590 entry offering `destination`, refusing a locked (struck-through) entry
    and refusing two matches outright rather than guessing which. Pure."""
    hits = [w for w in _box_matches(dump, destination)
            if w.get("screenX") is not None and "<str>" not in (w.get("text") or "")]
    return hits[0] if len(hits) == 1 else None


def box_is_locked(dump, destination) -> bool:
    """True when `destination` exists in the box's menu but is struck through (locked). Pure."""
    return any("<str>" in (w.get("text") or "") for w in _box_matches(dump, destination))


def use_box(ctx, destination, log: str = "poh") -> bool:
    """Open the jewellery box's Teleport Menu and click `destination`'s entry. The LANDING is
    confirmed by the caller (legs.await_arrival against the leg's own dest), not here."""
    box = find((BOX_ID,))
    if box is None:
        debug_log.log(log, "no jewellery box in view")
        return False
    refresh = controller.object_refresh_target(box.get("x"), box.get("y"), box.get("plane", 0),
                                               box.get("id"))
    if not controller.click_menu_action(ctx.ser, box, BOX_MENU_OPTION, refresh_target=refresh):
        debug_log.log(log, "couldn't open the jewellery box's Teleport Menu")
        return False
    deadline = time.time() + BOX_MENU_BUDGET_S
    entry = None
    while time.time() < deadline:
        dump = controller.get_widgets_dump() or []
        if box_is_locked(dump, destination):
            debug_log.log(log, f"{destination} is locked in the jewellery box")
            controller.key_tap(ctx.ser, "ESC")
            return False
        entry = box_entry(dump, destination)
        if entry is not None:
            break
        time.sleep(POLL_S)
    if entry is None:
        debug_log.log(log, f"the jewellery box menu never offered {destination!r}")
        controller.key_tap(ctx.ser, "ESC")
        return False
    clicked = controller.click_entity(ctx.ser, entry, offset=(0, 0), check_ui_blockers=False,
                                      simulate_mistakes=False, expected_action=destination)
    if not clicked:
        # Leave nothing open: a retry right-clicks the box, and with 590 still up that click
        # lands on the interface instead (phase-B review).
        debug_log.log(log, f"the click on {destination!r} was withheld or failed -- closing the menu")
        controller.key_tap(ctx.ser, "ESC")
        return False
    return True


def await_left_house(budget_s: float) -> bool:
    """True once /player no longer reads as inside the house. How a jewellery-box hop is confirmed:
    landing tiles disagree between our own vendored tables (Mining Guild is 28 tiles apart in
    teleportation_boxes.tsv and teleportation_items.tsv), but "we left the house" is unambiguous,
    and the walk leg that follows re-plans from wherever we really are."""
    deadline = time.time() + budget_s
    while time.time() < deadline:
        player = controller.get_player()
        if player and player.get("x") is not None and not is_house_player(player):
            return True
        time.sleep(POLL_S)
    return False


def use_ring(ctx, code, expect, log: str = "poh") -> bool:
    """The house fairy ring is an ordinary hop_fairy_ring target once its id is named."""
    return travel.hop_fairy_ring(ctx, code, expect, log=log, ring_ids=(RING_ID,))
