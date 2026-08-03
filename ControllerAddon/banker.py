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
    for obj in controller.get_objects():
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
    Actions if that's not already its default left-click."""
    bag = next(
        (i for i in controller.get_inventory() if LOOTING_BAG_NAME in (i.get("name") or "").lower()),
        None,
    )
    if bag is None:
        return False

    print("[bank] emptying looting bag")
    if not controller.click_entity(ctx.ser, bag):
        return False

    time.sleep(random.uniform(0.6, 1.0))
    return True


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
    if not entries:
        return

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


def bank_up(ctx, config: dict) -> bool:
    """
    Full cycle: bank at whatever's within walking distance if anything is,
    otherwise teleport to Ferox Enclave via the ring of dueling, bank and
    use the fountain there, then walk back. Either way: deposit per
    keepItems, withdraw the configured loadout, close.
    """
    if not config.get("enabled", True):
        return False

    bank_obj = find_nearby_bank(config)
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
    saved_location = travel.save_location()
    if saved_location is None:
        print("[bank] couldn't record current location, aborting teleport bank trip")
        return False

    if not travel.teleport_to_ferox(ctx):
        return False

    ferox_search_config = dict(config)
    ferox_search_config["bankNames"] = FEROX_BANK_NAMES
    ferox_search_config["maxBankDistance"] = FEROX_MAX_BANK_DISTANCE
    bank_obj = find_nearby_bank(ferox_search_config)
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

    print("[bank] walking back to the previous location")
    if not travel.walk_to(ctx, saved_location):
        print("[bank] couldn't walk all the way back -- continuing from here")

    return True
