# MenuPriority: force-prioritize value-matched loot

## Problem

`MenuPriority.java` intercepts every `MenuOpened` event and swaps a matching menu entry into
the last array slot -- the slot the game always resolves a plain left-click to (the same trick
shift-click uses to force "Walk here"). This is the codebase's established mechanism for making
a left-click land on the intended action regardless of what the game would otherwise put on top
of the menu, and it already covers attack targets, named loot-whitelist items, target objects,
and the "Forced Actions" list.

It does NOT cover value-matched loot. `maybe_loot` (slayer.py) can pick up an item purely because
`totalValue >= minLootValue`, without that item's name being on the loot whitelist. Since
`MenuPriority`'s loot check is `"take".equals(option) && lootItems.contains(target)`, a
value-matched item gets no priority-forcing at all -- a left-click on it gambles on whatever
happens to be on top of that tile's menu.

This is the confirmed root cause of a live-observed bug: a "Grimy ranarr weed" ground item (only
matched via `minLootValue`, not the whitelist -- confirmed from the debug log's
`worth 5271gp` reason string) got re-clicked for 40+ consecutive ticks without ever being picked
up, because the plain left-click wasn't actually resolving to "Take" on it.

## Fix

Extend `MenuPriority.apply()` to also force-prioritize "Take" on any item meeting the value
threshold, using the exact same last-slot-swap mechanism already used for the other three cases
-- no new interaction paradigm (e.g. right-click + menu-read) needed anywhere.

1. **Thread `ItemManager` into `MenuPriority.apply(...)`.** `ControllerPlugin` already has
   `@Inject private ItemManager itemManager;`; pass it as a new argument at the single call site
   (`ControllerPlugin.java:144`).
2. **Read `slayerConfig.minLootValue()`** once per call, alongside the existing
   `targetNpcsCache`/`lootItemsCache`/`targetObjectsCache`/`forcedActionsCache` reads.
3. **Widen the loot half of the `wanted` condition:**
   ```java
   ("take".equals(option) && (lootItems.contains(target) || meetsLootValue(itemManager, entry, minLootValue)))
   ```
4. **Add `meetsLootValue`:**
   ```java
   private static boolean meetsLootValue(ItemManager itemManager, MenuEntry entry, int minLootValue)
   {
       if (minLootValue <= 0)
       {
           return false;
       }
       int itemId = entry.getItemId();
       if (itemId <= 0)
       {
           return false;
       }
       return itemManager.getItemPrice(itemId) >= minLootValue;
   }
   ```
   `MenuEntry.getItemId()` is a real method on the RuneLite API (confirmed via `javap` against
   `runelite-api-1.12.33.jar` in the local Gradle cache), so this needs no name/text parsing --
   unlike the existing `target`-based checks, which strip color tags and quantity suffixes to
   compare against configured name lists.

## Known simplification

This checks unit GE price (`itemManager.getItemPrice(itemId)`), not `price x quantity` the way
`GroundItemProvider`'s `totalValue` field does -- `MenuEntry` has no quantity accessor; getting
it would mean reading the tile's `TileItem` quantity from the entry's `getParam0()`/`getParam1()`
world coordinates, extra plumbing for a case that doesn't come up in practice. Monster drops are
effectively always qty=1 per ground-item menu entry; the only real gap this leaves is a stacked
multi-quantity item (e.g. a coin pile) whose *total* value crosses the threshold only via
quantity, which won't get prioritized. Not worth the added complexity for how rarely it applies.

## Out of scope

- Any change to `maybe_loot` in `slayer.py` -- the plain left-click it already issues will now
  resolve correctly server-side, exactly as it already does for whitelisted loot. No Python-side
  changes at all.
- A right-click + read-menu interaction mechanism -- considered and rejected in favor of this
  smaller, architecture-consistent fix (see conversation this spec came out of).
- Quantity-aware value matching (see Known simplification above).
