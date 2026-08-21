# Golem Crafting routine

## Problem

We want a new automation routine for Golem Crafting, a member-only Crafting
training method on Wyrmscraig (released 2026-07-29, 9 days before this
design). Players mine sunstone, chisel it into cores, carve an empty plinth
from four sides, then insert a core plus a hunter fur to finish a golem for
Crafting XP and an uncut gem. Requires level 60 Crafting and completion of
the "Fallen From Grace" quest.

Unlike every other routine in this codebase, this activity is genuinely
brand new: the bundled OSRS Wiki mirror (`osrs-wiki-data-master/`) still has
`Wyrmscraig.txt` and `Fallen From Grace.txt` tagged `{{Future Content}}`
with no mechanical detail, and no "Golem crafting" page exists in the local
mirror's index at all. Everything mechanical in this document comes from a
live fetch of the current OSRS Wiki, not the local mirror, and none of it
has been cross-checked against the live game client or cache the way other
routines in this codebase (e.g. `shipwreck_salvaging.py`) verify object
names and menu options before relying on them. Treat every object/menu-option
name below as a first-cut assumption pending live testing, same as this
codebase's existing convention for unverified facts (see `blast_furnace.py`'s
inline "ASSUMPTION, NOT YET VERIFIED LIVE" comments).

## Scope

1. A new `routines/golem_crafting.py`: full pipeline (mine sunstone -> chisel
   cores -> craft golems -> bank) with an automatic fallback to hunting goats
   for fur at the Goat Pit when the bank has none of the usable fur types.
2. New helpers in `banker.py` for the fur pouch (`has_fur_pouch`,
   `fill_fur_pouch`, `empty_fur_pouch`), mirroring the existing coal bag
   helpers (`has_coal_bag`/`fill_coal_bag`/`empty_coal_bag`) exactly, since
   the fur pouch is a shared banking mechanic like the coal bag, not
   golem-crafting-specific code.
3. Wiring: `ROUTINE_FILES` in `controller.py`, a new `GOLEM_CRAFTING` entry
   in `RoutineChoice.java`, a new config section in `ControllerConfig.java`,
   and `ConfigExporter.exportGolemCrafting()`.
4. Explicitly NOT in scope:
   - The XP-drop tick-timing speed-up trick (clicking on the tick the XP
     drop appears to cut the next plinth side from 4 ticks to 3). Always use
     the safe, un-optimized cadence.
   - Acquiring the fur pouch itself -- the routine assumes the player
     already owns one (any size).push
   - Any fur types not obtainable at all (this routine only ever sources
     fur from the bank or the Goat Pit -- it never buys from a shop or trains
     other Hunter methods elsewhere on the map).
   - Quest completion, leveling up Crafting to 60, or any other
     one-time account setup.

## Research method and confidence

- **Live OSRS Wiki fetch** (`oldschool.runescape.wiki/w/Golem_crafting`,
  `/w/Wyrmscraig`, `/w/Fallen_From_Grace`, `/w/Fur_pouch` /
  `/w/Large_fur_pouch`, `/w/Goat_pit`), since the bundled local mirror
  predates this content. Treated as reliable for stated game mechanics,
  level requirements, and the fur XP table, but it says nothing about
  RuneLite object names, menu option strings, or exact click sequences --
  none of that has been verified.
- **No RuneLite decompile or live-cache lookup was performed for this
  design** (out of scope for a brainstorming pass). The implementation
  should do an equivalent live-verification pass (see Testing) before this
  routine is trusted unattended -- this activity has materially more
  unverified surface area than a typical new routine in this codebase,
  because it's 9 days old and not yet reflected in any of the local research
  material this project normally leans on.
- **Existing codebase conventions**, read directly from `blast_furnace.py`,
  `mining.py`, `firemaking.py`, and `banker.py`, for the routine contract,
  phase/trip-list state machine shape, and the coal-bag helper pattern this
  design mirrors for the fur pouch.

## Key facts from research (live wiki, unverified against the game)

- **Requirements**: Crafting 60, "Fallen From Grace" quest complete (which
  itself needs Sailing 62, Crafting 60, Runecraft 47, Mining 53, and the
  Pandemonium quest).
- **Location**: Wyrmscraig island, in the Unquiet Ocean (Sailing 62 to sail
  there before the quest; teleports available after via necklace of
  passage, slayer ring, and an Ardeaglais teleport scroll). A bank chest
  exists on the island, roughly central to the crafting/mining/hunting
  areas -- exact walking distances between them are unconfirmed.
- **Core loop**: mine sunstone (22 rock spots + 1 monolith; a "momentum"
  mechanic -- switching rocks quickly -- increases success chance and gives
  a 20% chance of bonus sunstone) -> chisel sunstone into sunstone cores ->
  click an empty plinth and carve all 4 sides (~15 ticks/side, ~60 ticks
  total) -> insert 1 sunstone core + 1 hunter fur, awarding Crafting XP and
  an uncut gem.
- **Fur table** (14 usable types, highest fur-quality bonus first): Moonlight
  antelope fur, Sunlight antelope fur, Dashing kebbit fur, Kyatt fur, Dark
  kebbit fur, Fox fur, Spotted kebbit fur, Graahk fur, Larupia fur, Feldip
  weasel fur, Common kebbit fur, Desert devil fur, Polar kebbit fur,
  Wyrmscraig goat fur (lowest). Higher-tier furs give more Crafting XP per
  golem; **Wyrmscraig goat fur is the only one farmable on the island
  itself** -- every other fur type has to already be in the bank.
- **Fur pouch**: comes in small (14)/medium (21)/large (28) capacity, holds
  a mix of hunter fur types at once. Right-click options: Fill, Empty,
  Check, Open, Destroy. Opening it lets furs caught while hunting route
  straight in (same idiom `empty_looting_bag`'s docstring already documents
  for the looting bag during combat); Empty transfers its contents into an
  open bank.
- **Goat Pit**: south side of Wyrmscraig, Hunter 60 required. Mechanic: line
  the pit with wooden spikes, lure/prod a goat in, harvest (spikes break on
  harvest), and the pit must be fully cleared of goats before it can be
  re-lured. Yields Wyrmscraig goat fur.

## Design

### 1. `banker.py` additions

```python
FUR_POUCH_NAME = "fur pouch"  # substring match -- covers small/medium/large uniformly, same idiom as COAL_BAG_NAME

def has_fur_pouch() -> bool: ...
def fill_fur_pouch(ctx) -> bool: ...   # click the pouch at an open bank to top it off
def empty_fur_pouch(ctx) -> bool: ...  # click Empty to dump into inventory (pre-craft) or into an open bank
```

Each mirrors `fill_coal_bag`/`empty_coal_bag` exactly: find the item by
substring match, `click_entity(ctx.ser, bag, check_ui_blockers=False)`, sleep
to let the action register, with the same "ASSUMPTION, NOT YET VERIFIED
LIVE -- which action a plain click resolves to in each state" docstring
those two already carry.

### 2. `routines/golem_crafting.py` (new)

```python
DEFAULT_CONFIG = {
    "enabled": True,
    # Highest fur-quality first -- bank/hunt phases walk this list looking
    # for the first type actually available, so "use whichever fur is best
    # available" needs no per-fur config toggle.
    "furPriority": [
        "Moonlight antelope fur", "Sunlight antelope fur", "Dashing kebbit fur",
        "Kyatt fur", "Dark kebbit fur", "Fox fur", "Spotted kebbit fur",
        "Graahk fur", "Larupia fur", "Feldip weasel fur", "Common kebbit fur",
        "Desert devil fur", "Polar kebbit fur", "Wyrmscraig goat fur",
    ],
    "maxObjectDistance": 15,
    "bank": {"teleportFallback": False},
    "breaks": {  # same shared shape as every other routine
        "enabled": True,
        "everyMinMinutes": 45, "everyMaxMinutes": 75,
        "durationMinMinutes": 3, "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {"stopOnDeath": True, "maxRuntimeHours": 0},
}

def get_config(bridge_config):
    config = controller.get_golem_crafting_config()
    return config if config is not None else DEFAULT_CONFIG
```

**Phase state machine**, stored in `ctx.state.data["gc_phase"]` (defaults to
`"mine"` when unset), dispatched from `tick(ctx)` -- one phase function
handles each tick, writing the next phase name into `ctx.state.data` when
its own work is done for now:

- **`mine`**: momentum-click sunstone rocks/the monolith (re-target on each
  swing rather than waiting out a full success/fail cycle, per the wiki's
  momentum mechanic) until inventory is full. Safety-stop if no sunstone
  rocks are found nearby at all (wrong location). -> `chisel`.
- **`chisel`**: for each raw sunstone in inventory, use the chisel on it to
  make a sunstone core (item-then-item two-click idiom, same as
  `firemaking.py`'s tinderbox+log). -> `craft`.
- **`craft`**: find an empty plinth and carve all 4 sides (click, wait ~15
  ticks, repeat x4 -- no XP-drop timing per Scope), then insert one
  sunstone core + the highest-priority fur present in inventory into the
  finished golem. Repeat until out of cores or fur. -> `bank`. **AMENDED
  2026-08-08** (implementation-time final review): this phase does NOT
  empty the fur pouch, contrary to this bullet's original text -- doing so
  here, after `mine` had already filled the inventory, was a deterministic
  deadlock (no free slot for the emptied fur to occupy). See `bank`'s own
  amendment below for where pouch handling actually lives now.
- **`bank`**: open the bank, deposit uncut gems and any leftover raw
  sunstone, withdraw the first `furPriority` type actually present in the
  bank -- a fixed inventory-slot budget (`FUR_WITHDRAW_BUDGET = 14`)
  regardless of whether a fur pouch is owned. **AMENDED 2026-08-08**: the
  original "enough to fill the fur pouch if one is owned" design withdrew
  up to a full inventory's worth when a pouch was carried, compacted it
  into the pouch, and left emptying it to `craft`; that combination is what
  produced the `craft`-phase deadlock above. Withdrawn fur now goes
  straight to loose inventory slots (no pouch fill/empty round-trip at
  all -- that pair bought nothing once the budget stopped depending on
  pouch ownership). The pouch is still emptied once, right after the bank
  closes, but only for fur caught while _hunting_ (see `hunt` below),
  which routes into an open pouch -- unrelated to this withdrawal. If
  **none** of `furPriority`'s fur types are in the bank at all -> `hunt`.
  Otherwise -> `mine`.
- **`hunt`**: entered only when the bank is out of every usable fur type.
  Verify/withdraw wooden spikes (safety-stop if the bank has none either),
  line the Goat Pit, open the fur pouch (if owned) so catches auto-route
  into it, lure and harvest goats until the pit needs clearing, clear it,
  then -> `bank` (to deposit/organize the catch before resuming `mine`).

Each phase's object/item lookups reuse this codebase's established
`find_object`/`find_item` local-helper pattern (closest visible match within
`maxObjectDistance`, case-insensitive substring for objects / exact match
for inventory items) rather than introducing a new lookup convention.

One-shot Crafting-level check on first tick, same shape as
`blast_furnace.py`'s `check_level_requirement` (gated by a
`ctx.state.data` flag so it never runs from `setup()`, which would
false-trigger a safety stop pre-login).

### 3. Wiring

- `controller.py`: add `"GOLEM_CRAFTING": "routines/golem_crafting.py"` to
  `ROUTINE_FILES`; add `get_golem_crafting_config()` (`api_get("/golem-crafting-config")`),
  same shape as `get_blast_furnace_config()`.
- `RoutineChoice.java`: add `GOLEM_CRAFTING("Golem Crafting")`.
- `ControllerConfig.java`: new `@ConfigSection` (`golemCraftingSection`)
  with `golemCraftingEnabled`, a bank-teleport-fallback toggle, and the
  shared Breaks/Safety fields every other section already exposes.
  `furPriority` is **not** a Java-exposed config field -- it's a Python-side
  default list, same as `blast_furnace.py`'s `BAR_TYPES` table isn't
  exposed either (the player never chooses a fur, unlike bar type, which is
  a deliberate choice `BlastFurnaceBarType` exposes as a dropdown).
- `ConfigExporter.java`: new `exportGolemCrafting(ControllerConfig config)`
  returning `{enabled, bank: {teleportFallback}, breaks, safety}` --
  smaller than `exportBlastFurnace` since there's no bar-type-equivalent
  selection to export.

### 4. Error handling & safety

- Hard safety-stops (matching this codebase's existing convention of
  stopping rather than looping forever on missing infrastructure): no
  sunstone rocks found, no plinth found, no bank and no teleport fallback,
  no Goat Pit found, no wooden spikes available in inventory or bank.
- Goat Pit clear-before-relure constraint is tracked directly (harvest until
  the pit reports empty of goats before attempting to re-lure), not assumed.
- Every object name, menu-option string, and click sequence in this design
  is unverified against the live game -- each gets an inline "ASSUMPTION,
  NOT YET VERIFIED LIVE" comment in the implementation, same convention as
  `blast_furnace.py`, and should be corrected during the staged live
  verification in Testing below rather than trusted as written.

## Testing

No unit-test framework exists for routines in this codebase -- verification
is live, against the running bridge (per `AGENTS.md`). Given how much of
this design rests on unverified wiki text rather than confirmed object/menu
facts (more than a typical routine here), verification needs to be staged
rather than a single end-to-end run:

1. Manually walk through one full cycle in-game first (mine, chisel, carve
   a plinth, insert core+fur, bank) to confirm exact menu-option names,
   plinth click behavior, and whether the fur pouch's Fill/Empty resolve the
   way assumed above.
2. Run the routine live through one `mine -> chisel -> craft -> bank` cycle,
   watching phase transitions in the debug log.
3. Deliberately drain the bank's fur stock to confirm the `hunt` phase
   triggers correctly and completes one full lure-harvest-clear cycle at the
   Goat Pit.
4. Only then a longer unattended soak run.
