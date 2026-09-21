# slayer_bib.py -- read the SLAYER BEST IN BANK plugin's own recommendations out of the bank.
#
# USER's call 2026-09-21, and it is the right one: "there is already a plugin in place, just use
# the ui inside the bank and just pickup the items it recommends". We do not port SBIB's task
# knowledge, we do not vendor it, and we do not reimplement its scoring. The plugin is installed
# and running (`~/.runelite/plugins/slayer-best-in-bank_lAHurv1obnS...jar`, loaded by RuneLite's
# ExternalPluginManager); it already decides what this account should bring for the live task.
# We click its button and read the answer.
#
# ================= WHY THIS WORKS AT ALL (the one insight the whole file rests on) =============
#
# `TieredBankLayout.layout()` HIDES every bank item widget and then un-hides ONLY the recommended
# ones:
#
#     for (Widget child : itemContainer.getChildren()) { child.setHidden(true); }
#     ... positionWidget() calls setHidden(false) on each recommendation
#
# and our own `BankProvider` builds its list from BANK_ITEM_CONTAINER's dynamic children while
# SKIPPING `child.isHidden()`. That is documented in CLAUDE.md as a LIMITATION -- the reason
# `/bank/memory` had to exist at all, because `/bank` can only report what the interface is
# drawing. Here the limitation is the entire feature: with the Best-in-Bank view switched on,
# **`GET /bank` returns exactly the plugin's recommendations and nothing else**, already ordered
# into its withdraw zigzag, each with a screen position we can click.
#
# So there is no widget geometry to reproduce, no overlay to OCR, and no second copy of SBIB's
# task table to keep in step with the plugin. If the plugin changes its mind, we change with it.
#
# ================= THE NUMBERS, READ OUT OF THE INSTALLED JAR ==================================
#
# `javap -c -p com/freearcanes/slayergear/BankAdvisorButton.class` on the installed jar, so these
# are what the RUNNING plugin does, not what some version of its source says:
#
#     ldc 786433                 -> client.getWidget(786433) = group 12, child 1
#                                   (Bankmain.UNIVERSE, the classic WidgetInfo.BANK_CONTAINER)
#     createChild(-1, 5)         -> appended DYNAMIC child, WidgetType.GRAPHIC
#     setItemId(24444)           -> drawn as a slayer helm
#     setOriginalWidth(27) / Height(24) / X(98) / Y(6)
#     setXPositionMode(2)        -> ABSOLUTE_RIGHT      setYPositionMode(0) -> ABSOLUTE_TOP
#     setName("Slayer Best in Bank")
#     setAction(1, "Show Slayer Best in Bank" | "Close Best-in-Bank view")
#
# INDEX IS NOT STABLE, so never hardcode one. `createChild(-1, ...)` APPENDS, and other installed
# plugins inject their own buttons into the same 12.1 container (`required-materials` does exactly
# this). Whichever loaded first takes the lower index. Match on NAME, fall back to the item id.
#
# ================= WHAT WE CANNOT SEE, AND WHAT WE USE INSTEAD =================================
#
# `WidgetProvider.describe()` reports index/name/itemId/screenX/screenY/clickable -- but NOT a
# widget's actions. So the button's own "Show ..." vs "Close ..." text, which is the plugin's real
# state, is invisible to us. We therefore never ask the button what state it is in; we ask the
# BANK, which is the thing we actually care about: the view is on when `/bank` has collapsed to a
# small set. That is a better test anyway -- it confirms the outcome we want rather than a label.

import sys

import banker
import controller
import debug_log
import gear

LOG = "bib"

BANK_CONTAINER_GROUP = 12       # Bankmain.UNIVERSE, from the jar's ldc 786433 = (12 << 16) | 1
BANK_CONTAINER_CHILD = 1
BUTTON_NAME = "Slayer Best in Bank"
BUTTON_ITEM_ID = 24444          # the slayer helm it draws itself as

# A full bank here is ~900 item names. A recommendation set is a loadout plus supplies. The gap is
# enormous, so the threshold does not need to be precise -- it only has to sit far below any real
# bank and far above any plausible recommendation list.
VIEW_ACTIVE_MAX_ITEMS = 60


def find_button():
    """The Best-in-Bank button as a clickable widget dict, or None.

    None has three different causes and the caller usually wants to tell them apart, so they are
    logged rather than collapsed: the bank is shut, the plugin is not loaded/installed, or the
    plugin is loaded but has not installed its button yet (it does so on bank open)."""
    data = controller.api_get("/widget/children",
                              params={"group": BANK_CONTAINER_GROUP, "child": BANK_CONTAINER_CHILD})
    if not data or not data.get("found"):
        return None
    children = data.get("children") or []
    named = [c for c in children if (c.get("name") or "").strip() == BUTTON_NAME]
    if not named:
        # The name is the contract; the item id is the fallback for a future release that renames
        # the button but keeps drawing it as a slayer helm.
        named = [c for c in children if c.get("itemId") == BUTTON_ITEM_ID]
    if not named:
        return None
    button = named[0]
    return button if button.get("clickable") else None


def bank_items():
    """Whatever `/bank` is currently drawing. With the view OFF this is the open tab; with the
    view ON it is the recommendation set."""
    data = controller.api_get("/bank")
    if not data or not data.get("open"):
        return None
    return data.get("items") or []


def view_is_active() -> bool:
    """True when the bank is showing the Best-in-Bank view rather than ordinary contents.

    Asks the BANK, not the button -- see the module header. `None` (bank shut) is False, not an
    error: a caller polling this during a bank trip sees the bank close normally."""
    items = bank_items()
    return items is not None and 0 < len(items) <= VIEW_ACTIVE_MAX_ITEMS


def toggle(ctx, want_active: bool = True) -> bool:
    """Put the Best-in-Bank view into `want_active`, returning whether it got there.

    Idempotent by design: it reads the state first and does nothing if it is already right, so a
    routine may call this every bank trip without tracking the toggle itself. A click is confirmed
    by the bank list changing, never slept off -- the same rule every other click site here
    follows."""
    if view_is_active() == want_active:
        return True
    button = find_button()
    if button is None:
        debug_log.log(LOG, "Best-in-Bank button not found (bank shut, or the plugin is not loaded)")
        return False
    before = bank_items()
    before_n = -1 if before is None else len(before)
    # check_ui_blockers=False: this is an interface click, inside the bank panel uiBlockers flags
    # -- the same rule as every other widget click in this codebase (see travel.teleport_to_ferox
    # for the rotate-forever loop the flag causes). offset=(0, 0): the button is 27x24, and the
    # default (-7, +7) nudge would slide off something that small.
    controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False,
                            simulate_mistakes=False)
    ok = _confirm_changed(ctx, before_n, want_active)
    debug_log.log(LOG, f"Best-in-Bank view {'on' if want_active else 'off'}: "
                       f"{'confirmed' if ok else 'NOT confirmed'}")
    return ok


def _confirm_changed(ctx, before_n: int, want_active: bool) -> bool:
    import bridge_events
    outcome = bridge_events.await_outcome(
        getattr(ctx, "events", None), ("WIDGET_LOADED",), 3.0,
        confirm=lambda: view_is_active() == want_active and len(bank_items() or []) != before_n)
    # Outcome exposes .hit, not .ok, and its __bool__ already returns it -- so this is just
    # bool(outcome). FOUND LIVE 2026-09-21 on the first Kuradal task: the atBank hook raised
    # AttributeError("'Outcome' object has no attribute 'ok'") every time it ran. The hook is
    # deliberately allowed to fail without aborting the trip, which is why this survived to the
    # first live gear-up instead of failing a test.
    return bool(outcome)


def recommendations(ctx):
    """The plugin's recommended items for the LIVE task, as clickable bank entries in its own
    withdraw order -- or None if the view could not be switched on.

    None is deliberately distinct from []: None means "we could not ask the plugin" (bank shut,
    plugin missing, toggle refused) and must never be read as "the plugin recommends nothing".
    That is the same distinction bank_memory draws between never-seen and empty, and for the same
    reason -- collapsing them lets a routine conclude it needs no gear when it simply failed to
    look."""
    if not toggle(ctx, True):
        return None
    return bank_items()


# ---------------- picking the loadout up ----------------
#
# TIER 2/3 MUST NOT BE WITHDRAWN. `TieredBankLayout` stacks its sections down the container in
# this order: Tier 1 equip path, boss switches, trip supplies, then Tier 2, then Tier 3. The first
# three are the loadout -- what we want. Tier 2/3 are ALTERNATIVES per slot ("next loadout swaps"),
# and taking them would drag a second and third weapon, helm and body into a 28-slot inventory.
#
# They are separable by COLUMN COUNT, which is the only structural difference between the
# sections: the loadout sections lay out 4 per row (EQUIP_PATH_ITEMS_PER_ROW), tiers 2 and 3 lay
# out 8 (ITEMS_PER_ROW). So a 5th distinct column can only be Tier 2 or 3.
#
# We work in RELATIVE columns, never the raw 51/99/147/195 from the Java: `/bank` reports
# canvasToScreen SCREEN coordinates, so the origin is wherever the bank happens to be drawn and
# the 48px pitch is multiplied by the stretch scale. Counting distinct x values is origin- and
# scale-independent; hardcoding 243 would not be.
LOADOUT_COLUMNS = 4


def _split_loadout(items):
    """(loadout, alternatives) -- the Tier 1 + switches + supplies sections, and Tier 2/3.

    Returns everything as loadout when only four columns are present, which is the common case:
    Tier 2/3 exist only when SBIB's 'alternatives per slot' setting is above 1.
    """
    positioned = [i for i in items if i.get("screenX") is not None and i.get("screenY") is not None]
    if not positioned:
        return list(items), []
    columns = sorted({i["screenX"] for i in positioned})
    if len(columns) <= LOADOUT_COLUMNS:
        return list(items), []
    # The 5th column onward is 8-wide territory. The topmost item sitting there marks where the
    # Tier 2 section begins; everything from that row down is an alternative.
    wide = [i for i in positioned if i["screenX"] in columns[LOADOUT_COLUMNS:]]
    boundary = min(i["screenY"] for i in wide)
    loadout = [i for i in items if (i.get("screenY") or -1) < boundary]
    return loadout, [i for i in items if (i.get("screenY") or -1) >= boundary]


def _withdraw(ctx, slot, quantity: int) -> bool:
    """Click one recommended bank slot. No search fallback and no scroll handling, deliberately:
    the whole point of the Best-in-Bank view is that it has already re-laid every recommended item
    from position 0, so all of them are on screen. If one is not reachable, something is wrong
    with the view rather than with the item, and silently searching for it would hide that."""
    if not banker._reachable(slot):
        debug_log.log(LOG, f"{slot.get('name')} is off-window even in the Best-in-Bank view, skipping")
        return False
    for _ in range(max(1, quantity)):
        controller.click_entity(ctx.ser, slot, check_ui_blockers=False)
        controller.human_delay(0.15, 0.3)
    return True


def gear_up(ctx, quantities=None, equip=True):
    """Open the Best-in-Bank view, withdraw everything it recommends, and wear what can be worn.

    THE BANK MUST ALREADY BE OPEN -- this is a step inside a bank trip, not a trip of its own.

    Returns a dict: {"withdrew": [names], "equipped": [names], "alternatives": n, "ok": bool}.

    `quantities` maps a lowercase name fragment -> how many to take, for the consumables. It is
    needed because SBIB's recommended COUNTS live in `SupplyRecommendation`, which is plugin state
    we cannot see -- the bank widgets carry only WHICH items, not how many. So the items come from
    the plugin and the counts come from the caller's own supply profile. Anything unlisted is
    taken once, which is right for every gear slot.

    The view is switched back OFF at the end, always: it hides real bank contents, and a later
    deposit or a `/bank` read by anything else would otherwise see the filtered set and conclude
    the bank is nearly empty.
    """
    result = {"withdrew": [], "equipped": [], "alternatives": 0, "ok": False}
    if not toggle(ctx, True):
        return result

    try:
        items = controller.get_bank(fresh=True).get("items", []) or []
        loadout, alternatives = _split_loadout(items)
        result["alternatives"] = len(alternatives)
        if alternatives:
            debug_log.log(LOG, f"leaving {len(alternatives)} Tier 2/3 alternatives in the bank")

        # One click = one item. Without this a bank left in "10" or "All" mode from an earlier
        # trip withdraws that many of the first gear slot clicked -- withdraw_loadout sets the
        # same mode for the same reason.
        banker.set_quantity_mode(ctx, "one")

        before = set(gear.carried_names())
        for slot in loadout:
            name = (slot.get("name") or "")
            quantity = 1
            for fragment, count in (quantities or {}).items():
                if fragment in name.lower():
                    quantity = count
                    break
            debug_log.log(LOG, f"withdrawing {quantity}x {name}")
            if _withdraw(ctx, slot, quantity):
                result["withdrew"].append(name)
        result["ok"] = True
    finally:
        # Never leave the view up, even if a withdraw threw.
        toggle(ctx, False)

    if equip:
        result["equipped"] = _equip_new(ctx, before)
    return result


def _equip_new(ctx, before):
    """Wear everything newly withdrawn that is wearable.

    The ITEM decides, not a table: we try Wear/Wield and keep what sticks. That is what makes this
    work for a loadout we did not choose and do not have a schema for -- SBIB may recommend any
    slot, and hardcoding which of its picks are equipment would be a second copy of its knowledge,
    which is the thing this whole approach exists to avoid.

    A two-handed weapon displaced by a shield is NOT a stop here (unlike routines/slayer._wear):
    SBIB recommends a coherent whole loadout, so if it names a shield it has also named the
    one-handed weapon to go with it. Order matters for that -- weapon before shield -- and comes
    from the plugin's own slot ordering, which the zigzag preserves.
    """
    # Skip what the bridge already knows is drinkable/edible. equip_from_inventory would WITHHOLD
    # on those anyway (its /hover gate sees no Wear/Wield), so this is not about correctness -- it
    # is about not flying the cursor to all fifteen potions to discover that, which is both slow
    # and nothing a human would ever do.
    consumable = set()
    for items in (controller.get_consumables() or {}).values():
        for item in items or ():
            name = (item.get("name") or "").lower()
            if name:
                consumable.add(name)

    worn = []
    for name in sorted(gear.carried_names() - before):
        if name in consumable:
            continue
        if gear.equip_from_inventory(ctx, name):
            worn.append(name)
            debug_log.log(LOG, f"equipped {name}")
    return worn


# ---------------- read-only live probe ----------------
# `python slayer_bib.py` from ControllerAddon/. Imports controller but never opens the serial
# port, so it is safe beside a running controller.py -- the same convention bank_memory.py uses.
# It does NOT click: it reports what it can see, so the first live look costs nothing and cannot
# fight the routine for the mouse.
def _probe():
    task = controller.api_get("/slayer") or {}
    print(f"task: {task.get('taskName')} x{task.get('count')}")

    data = controller.api_get("/widget/children",
                              params={"group": BANK_CONTAINER_GROUP, "child": BANK_CONTAINER_CHILD})
    if not data or not data.get("found"):
        print(f"widget {BANK_CONTAINER_GROUP}.{BANK_CONTAINER_CHILD}: not found -- bank is shut")
        return 1
    children = data.get("children") or []
    print(f"widget {BANK_CONTAINER_GROUP}.{BANK_CONTAINER_CHILD}: {len(children)} dynamic children")
    for child in children:
        mark = " <-- BEST IN BANK" if (child.get("name") == BUTTON_NAME
                                       or child.get("itemId") == BUTTON_ITEM_ID) else ""
        print(f"  [{child.get('index'):>3}] name={child.get('name')!r} "
              f"itemId={child.get('itemId')} item={child.get('itemName')!r} "
              f"screen=({child.get('screenX')},{child.get('screenY')}) "
              f"clickable={child.get('clickable')}{mark}")

    button = find_button()
    print(f"\nbutton: {'FOUND at ' + str((button['screenX'], button['screenY'])) if button else 'NOT FOUND'}")

    items = bank_items()
    if items is None:
        print("bank: shut")
        return 1
    print(f"bank is drawing {len(items)} items -- view_is_active()={view_is_active()}")
    for item in items[:40]:
        print(f"  {item.get('name')!r} x{item.get('qty')} "
              f"screen=({item.get('screenX')},{item.get('screenY')})")
    if len(items) > 40:
        print(f"  ... and {len(items) - 40} more")
    return 0


if __name__ == "__main__":
    sys.exit(_probe())
