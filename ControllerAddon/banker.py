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

import random
import re
import time

import controller
import travel

DEFAULT_BANK_NAMES = ("bank booth", "bank chest", "bank counter", "grand exchange booth")
FEROX_BANK_NAMES = ("bank chest", "bank booth")
FEROX_MAX_BANK_DISTANCE = 20


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


def open_bank(ctx, bank_obj, timeout_s: float = 10.0) -> bool:
    """Walk to and open a bank object. Returns once the interface is up."""
    if is_open():
        return True

    if not controller.click_entity(ctx.ser, bank_obj):
        return False

    waited = 0.0
    while waited < timeout_s:
        time.sleep(0.5)
        waited += 0.5
        if is_open():
            return True
    return False


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

    print("[bank] emptying looting bag")
    if not controller.click_entity(ctx.ser, bag, check_ui_blockers=False):
        return False

    time.sleep(random.uniform(0.6, 1.0))
    return True


HERB_SACK_NAME = "herb sack"
SEED_BOX_NAME = "seed box"


COAL_BAG_NAME = "coal bag"


def has_coal_bag() -> bool:
    return any(COAL_BAG_NAME in (i.get("name") or "").lower() for i in controller.get_inventory())


def fill_coal_bag(ctx) -> bool:
    """Click the coal bag at an open bank to fill it (to 27 coal, 36 with a Smithing cape --
    this project doesn't special-case the cape). UNCERTAIN which of Fill/Empty a plain
    left-click resolves to once the bag already holds coal -- the OSRS Wiki's Blast Furnace
    strategy guide notes the bank-side option can become "Empty" once the bag isn't empty,
    the opposite problem from empty_coal_bag below. Verify live which action a plain click
    actually performs in each state; add the wrong one to Controller Settings -> Clicking ->
    Forced Actions if needed. check_ui_blockers=False since this is an inventory-item click,
    same reasoning as every other inventory click in this codebase (see
    routines/firemaking.py's/routines/blast_furnace.py's own notes on this)."""
    bag = next(
        (i for i in controller.get_inventory() if COAL_BAG_NAME in (i.get("name") or "").lower()),
        None,
    )
    if bag is None:
        return False

    print("[bank] filling coal bag")
    if not controller.click_entity(ctx.ser, bag, check_ui_blockers=False):
        return False

    time.sleep(random.uniform(0.6, 1.0))
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

    print("[bank] emptying coal bag")
    if not controller.click_entity(ctx.ser, bag, check_ui_blockers=False):
        return False

    time.sleep(random.uniform(0.6, 1.0))
    return True


FUR_POUCH_NAME = "fur pouch"  # substring match -- covers small/medium/large uniformly, same
                               # idiom as COAL_BAG_NAME above (e.g. "Large fur pouch" still
                               # matches). See docs/superpowers/specs/2026-08-07-golem-crafting-
                               # design.md section 1.


def has_fur_pouch() -> bool:
    return any(FUR_POUCH_NAME in (i.get("name") or "").lower() for i in controller.get_inventory())


def fill_fur_pouch(ctx) -> bool:
    """Click the fur pouch at an open bank to fill it with hunter fur. ASSUMPTION, NOT YET
    VERIFIED LIVE -- which action a plain left-click resolves to here, same uncertainty
    fill_coal_bag documents above: the OSRS Wiki notes a bank-side click on a non-empty
    container can resolve to "Empty" instead of "Fill". Verify live which action a plain click
    actually performs in each state; add the wrong one to Controller Settings -> Clicking ->
    Forced Actions if needed. check_ui_blockers=False since this is an inventory-item click,
    same reasoning as every other inventory click in this codebase."""
    pouch = next(
        (i for i in controller.get_inventory() if FUR_POUCH_NAME in (i.get("name") or "").lower()),
        None,
    )
    if pouch is None:
        return False

    print("[bank] filling fur pouch")
    if not controller.click_entity(ctx.ser, pouch, check_ui_blockers=False):
        return False

    time.sleep(random.uniform(0.6, 1.0))
    return True


def empty_fur_pouch(ctx) -> bool:
    """Click Empty on the fur pouch to dump its fur into an open bank (pre-craft: into
    inventory instead, if no bank is open -- see routines/golem_crafting.py's craft_phase,
    which calls this before carving a plinth so any fur caught while hunting is available to
    insert into a golem). Same situation as empty_coal_bag above: "Empty" is not always the
    item's default option, so add it to Controller Settings -> Clicking -> Forced Actions if a
    plain click doesn't already resolve to it. check_ui_blockers=False since this is an
    inventory-item click, used both at a bank and out in the open (at the plinth), where the
    inventory panel itself is a live UI-blocker rect in resizable layouts."""
    pouch = next(
        (i for i in controller.get_inventory() if FUR_POUCH_NAME in (i.get("name") or "").lower()),
        None,
    )
    if pouch is None:
        return False

    print("[bank] emptying fur pouch")
    if not controller.click_entity(ctx.ser, pouch, check_ui_blockers=False):
        return False

    time.sleep(random.uniform(0.6, 1.0))
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
    time.sleep(random.uniform(0.3, 0.6))
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

    if has_looting_bag():
        empty_looting_bag(ctx)

    # Empties looting bag/herb sack/seed box/coal bag/etc. into the bank via the bank
    # interface's own "Deposit Containers" widget button, in one click -- CONFIRMED (user
    # request 2026-08-06): herb sack and seed box are kept out of the item-by-item deposit pass
    # below (see routines/slayer.py's protected_supply_names) so the container itself never
    # gets banked away, but that alone would leave its contents trapped inside it forever.
    # Redundant with empty_looting_bag above for that one bag specifically, which is harmless --
    # emptying an already-empty container is a no-op.
    deposit_all_containers(ctx)

    if not keep:
        deposit_all = bank.get("depositInventory") or {}
        if deposit_all.get("screenX") is not None:
            controller.click_entity(ctx.ser, deposit_all, offset=(0, 0))
            time.sleep(random.uniform(0.3, 0.6))
        return

    # Keeping specific items -- deposit one stack at a time instead of
    # Deposit-all, skipping anything on the keep list.
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if any(k in name for k in keep):
            continue
        controller.click_entity(ctx.ser, item)
        time.sleep(random.uniform(0.2, 0.4))


def set_quantity_mode(ctx, mode: str) -> bool:
    """mode is one of "one", "five", "ten", "x", "all" -- matching the keys
    BankProvider puts in quantityButtons. Clicking the mode that's already
    selected is harmless, so this doesn't bother checking first."""
    bank = controller.get_bank()
    button = bank.get("quantityButtons", {}).get(mode) or {}
    if button.get("screenX") is None:
        print(f"[bank] quantity mode button '{mode}' not found (bank open?)")
        return False

    controller.click_entity(ctx.ser, button, offset=(0, 0))
    time.sleep(random.uniform(0.2, 0.4))
    return True


def withdraw_loadout(ctx, config: dict) -> None:
    entries = [e for e in config.get("withdraw", []) if e.get("name") and e.get("quantity", 1) > 0]

    if entries:
        set_quantity_mode(ctx, "one")

    for entry in entries:
        name = entry["name"].lower()
        quantity = entry["quantity"]

        bank = controller.get_bank()
        slot = next(
            (s for s in bank.get("items", []) if name in (s.get("name") or "").lower()),
            None,
        )
        if slot is None:
            print(f"[bank] {entry.get('name')} not found in bank, skipping")
            continue

        print(f"[bank] withdrawing {quantity}x {slot.get('name')}")
        for _ in range(quantity):
            controller.click_entity(ctx.ser, slot)
            time.sleep(random.uniform(0.15, 0.3))

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
        print("[bank] search button not found (bank open?)")
        return False

    controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False)
    time.sleep(random.uniform(0.2, 0.4))
    controller.type_text(ctx.ser, query)
    time.sleep(random.uniform(0.3, 0.5))
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
    time.sleep(random.uniform(0.2, 0.4))
    return True


_DOSE_RE = re.compile(r"\((\d+)\)\s*$")


def _dose(name: str) -> int:
    """Trailing (N) dose count off an item name, e.g. "Prayer potion(4)" -> 4 -- 0 if there
    isn't one. Same idea as FoodUtil's heal amount for food, for picking the best available
    stack of something that comes in multiple charge/dose levels."""
    match = _DOSE_RE.search(name or "")
    return int(match.group(1)) if match else 0


def _search_and_withdraw(ctx, item_name: str, quantity: int) -> bool:
    # Search by the base name, without any trailing (N) dose/charge suffix -- CONFIRMED live
    # 2026-08-06: searching the item's full exact name (e.g. "Sanfew serum(4)") failed every
    # time, because key_tap has no way to send a shifted character like "(" or ")" -- there's no
    # such single key on a real keyboard, it's Shift+9/Shift+0. The base name alone is still
    # specific enough to filter down to just that item's dose variants (rarely more than a
    # handful), and the exact-name match below still picks the one dose we actually chose.
    search_query = _DOSE_RE.sub("", item_name).strip()
    if not search_bank(ctx, search_query):
        return False

    bank = controller.get_bank()
    slot = next(
        (s for s in bank.get("items", []) if (s.get("name") or "").lower() == item_name.lower()),
        None,
    )
    if slot is None:
        print(f"[bank] {item_name} not found after searching '{search_query}' -- search may not have worked")
        return False

    set_quantity_mode(ctx, "one")
    print(f"[bank] withdrawing {quantity}x {slot.get('name')}")
    # Track whether the clicks actually landed instead of assuming success -- CONFIRMED live
    # 2026-08-06: a concurrent human interaction moved the mouse mid-sequence and every withdraw
    # click failed ("aim point is outside the RuneLite window"), but this still unconditionally
    # returned True, telling the caller a withdrawal happened when nothing was actually taken.
    landed = 0
    for _ in range(quantity):
        if controller.click_entity(ctx.ser, slot):
            landed += 1
        time.sleep(random.uniform(0.15, 0.3))
    clear_bank_search(ctx)
    if landed == 0:
        print(f"[bank] withdrawing {slot.get('name')} failed -- no click landed")
        return False
    if landed < quantity:
        print(f"[bank] only {landed}/{quantity} withdraw clicks on {slot.get('name')} landed")
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
        print("[bank] no food found in bank")
        return False

    candidates.sort(key=lambda i: i.get("heal", 0), reverse=True)
    best = candidates[0]
    print(f"[bank] auto-picked food: {best.get('name')} (heals {best.get('heal')})")
    return _search_and_withdraw(ctx, best.get("name"), quantity)


def withdraw_best_prayer_restore(ctx, quantity: int = 1) -> bool:
    """Same idea as withdraw_best_food but for prayer restores, preferring the highest dose
    count (e.g. Prayer potion(4) over Prayer potion(1)) -- CONFIRMED (user request 2026-08-06)."""
    bank = controller.get_bank()
    candidates = [i for i in bank.get("items", []) if i.get("isPrayerPotion")]
    if not candidates:
        print("[bank] no prayer restore found in bank")
        return False

    candidates.sort(key=lambda i: _dose(i.get("name")), reverse=True)
    best = candidates[0]
    print(f"[bank] auto-picked prayer restore: {best.get('name')}")
    return _search_and_withdraw(ctx, best.get("name"), quantity)


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
        print(f"[bank] walking to {bank_obj.get('name')} (dist={bank_obj.get('distance')})")
        if not open_bank(ctx, bank_obj):
            print("[bank] failed to open bank")
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
                print("[bank] couldn't record current location, aborting teleport bank trip")
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
            print("[bank] no bank found at Ferox Enclave -- did the teleport actually land?")
            return False

    if not open_bank(ctx, bank_obj):
        print("[bank] failed to open the Ferox Enclave bank")
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
        print("[bank] walking back to the previous location")
        if not travel.walk_to(ctx, saved_location):
            print("[bank] couldn't walk all the way back -- continuing from here")

    return True
