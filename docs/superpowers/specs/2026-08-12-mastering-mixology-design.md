# Mastering Mixology routine

## Problem

We want a new automation routine for Mastering Mixology, a members-only Herblore minigame in
Aldarin (released 2024-09-25). Players refine herbs into paste, mix paste combinations into
potion bases, process those bases at one of three stations, and turn finished potions in against
rotating orders for Herblore XP and resin (spent on minigame-specific rewards).

Unlike `golem_crafting.py`'s subject (9 days old at design time, effectively undocumented), this
minigame is well-established: the local OSRS Wiki mirror has extensive mechanics/strategy
documentation, and — the significant find that reshaped this design's second half — an existing
open-source RuneLite QoL plugin, [hex-agon/mastering-mixology](https://github.com/hex-agon/mastering-mixology),
reads this minigame's entire live state via RuneLite's own generated gameval constants
(`VarbitID`, `ObjectID`, `ItemID`, `SpotanimID`, `VarPlayerID`). Every constant that plugin
references was cross-checked directly against this project's own cached RuneLite jar
(`runelite-api-1.12.35.jar`) and all of them exist there — this is not a version-mismatch risk,
and it eliminates the live-investigation blocker an earlier version of this design had (see
"Superseded research" below for what changed and why).

## Scope

1. A new `routines/mastering_mixology.py`: full pipeline (bank herbs → refine → hopper → read
   orders → mix → process → deposit), optimizing for maximum total points per paste (see
   "Optimization strategy" below), with digweed collection and processing-station speedup clicks.
2. A new Java provider, `MixologyProvider`, exposing the 3 active orders, hopper paste totals,
   mixing vessel slot contents, each processing station's current potion + progress, and digweed
   corner state — all via direct varbit reads (see Design). Fully specifiable now; no live
   session is needed to write it, only to confirm it against the real game (Testing).
3. Wiring: `ROUTINE_FILES` in `controller.py`, a new `MASTERING_MIXOLOGY` entry in
   `RoutineChoice.java`, a new config section in `ControllerConfig.java`, and
   `ConfigExporter.exportMasteringMixology()`.
4. Explicitly NOT in scope:
   - Live GE price lookups to pick the cheapest herb per paste type. `herbPriority` is a
     configured, static candidate list per paste type instead (see Design) — the player updates
     it if prices shift enough to change which herb is actually cheapest.
   - Any strategy other than max-points-per-paste (the wiki documents at least four others: max
     XP/hour, complete-every-order, target-one-resin, complete-non-triple-orders). Only one
     strategy is built now; others are a config/strategy-function extension point for later, not
     built speculatively.
   - Acquiring the Mixology Rewards shop items automatically, or any account-setup step (level
     60 Herblore, Children of the Sun quest completion, initial travel to Aldarin).
   - Optimizing travel to/from the minigame (see Transportation options in the wiki source) --
     the routine assumes the player is already standing in the lab when it starts, same as
     `golem_crafting.py` assumes the player is already on Wyrmscraig.
   - Depending on the `hex-agon/mastering-mixology` plugin being installed. It's used here purely
     as a research reference (its source is public/MIT-style, read to learn the varbit/object IDs
     it already reverse-engineered) — `MixologyProvider` reimplements the handful of lookup
     tables it needs directly, with no runtime dependency on that plugin at all.

## Optimization strategy: max total points per paste

Per the wiki's own simulation-backed strategy (`data/raw/M/Mastering Mixology_Strategies.txt`,
"Prioritise total points" section): make every potion in the current 3-order set **except**
single-paste potions (AAA/MMM/LLL) **unless** an MAL (Mixalot, mox+aga+lye) order is present in
that same set — in which case make all 3, including any singles. When all 3 current orders are
single-paste, make whichever single-paste type is currently most wanted and skip the other two.

This strategy is written against a level-81 account (all 9 potion bases unlocked, all reward
ratios in the wiki's table apply). Confirmed against the game's own changelog
(`Mastering Mixology.txt`'s Changes section, 2024-09-27 hotfix): **the game itself only ever
offers orders the player currently has the Herblore level for** — so a sub-81 account simply
never sees an MAL order, and the "unless MAL" branch never triggers; the strategy degrades
automatically to "always skip single-paste orders" with no level-tracking logic needed in the
routine at all. This also means the routine needs no explicit Herblore-level read for order
eligibility — only for the one-shot 60 + quest-complete pre-flight check.

## Research method and confidence

- **Local OSRS Wiki mirror** (`data/raw/M/Mastering Mixology.txt`,
  `data/raw/M/Mastering Mixology_Strategies.txt`, and the individual scenery pages —
  `data/raw/H/Hopper (Mastering Mixology).txt`, `data/raw/C/Conveyor belt (Mastering
  Mixology).txt`, `data/raw/R/Refiner.txt`, `data/raw/R/Retort.txt`, `data/raw/A/Agitator.txt`,
  `data/raw/A/Alembic.txt`, `data/raw/M/Mixing vessel.txt`, `data/raw/M/Mox lever.txt`,
  `data/raw/A/Aga lever.txt`, `data/raw/L/Lye lever.txt`). Treated as reliable for game
  mechanics, level requirements, points/reward tables, and the strategy math.
- **`hex-agon/mastering-mixology`** (GitHub, fetched via `gh api` — `MasteringMixologyPlugin.java`,
  `AlchemyObject.java`, `PotionType.java`, `PotionModifier.java`, `PotionComponent.java`,
  `PotionOrder.java`, `MasteringMixologyOverlay.java`). A published QoL overlay plugin for this
  exact minigame — read as a research reference for its already-reverse-engineered varbit/object
  ID mappings, not depended on at runtime. Treated as reliable: it's a real plugin visibly built
  against live gameplay (it reacts to varbit/graphics/sound events to drive on-screen highlights
  a player would immediately notice being wrong), and every constant it references was
  independently confirmed to exist in this project's own RuneLite jar (see next bullet) rather
  than taken on faith.
- **Direct jar verification** (`javap -p -constants` against
  `runelite-api-1.12.35.jar`'s `net.runelite.api.gameval.{VarbitID,VarbitID (via ObjectID1 for
  the overflow constants), ObjectID, ObjectID1, ItemID, SpotanimID, VarPlayerID}` classes):
  confirmed every `MM_*`/`MIXOLOGY_*` constant referenced by the plugin above exists with the
  exact same value in our own dependency version. This is what upgrades the plugin's mappings
  from "a well-informed guess" to "confirmed against the same jar this project's Java code
  compiles against."
- **No live RuneLite session or bridge query was performed for this design** (out of scope for a
  brainstorming/spec pass, same convention `golem_crafting.py`'s design doc used) — but unlike
  this design's first draft, that's no longer a blocker: every value `MixologyProvider` needs is
  a named, verified constant, not a guess pending live discovery. Live verification (Testing)
  confirms behavior, not identity, going in.

### Superseded research (for context, not current guidance)

This design's first draft assumed the active-orders/hopper-totals data lived in an
unmapped widget interface, and that processing-station speedup cues would need live capture via
`GraphicsObjectProvider` or a station's "Check" menu option, based on: (a) the wiki's own
player-facing description ("sound/visual cue"), and (b) a check of RuneLite's `InterfaceID`
gameval class finding no named Mixology interface. Both are still individually true — but they
were the wrong place to look. The actual data lives in **varbits**, not a widget interface, and
RuneLite *does* have a full family of named `VarbitID.MM_*` constants for this minigame (the
`InterfaceID` check was real but answered a narrower question than the one that mattered). This
section is kept only so a future reader doesn't waste time re-deriving what changed.

## Key facts from research

- **Requirements**: Herblore 60 (not boostable) and Children of the Sun quest completion. All 9
  potion bases unlock progressively up to level 81 (Mixalot).
- **Location**: Aldarin, members. A bank chest exists in the lab itself. Region id `5521`, plane
  `0`, is the lab region (`isInLabRegion` in the reference plugin) — a cheap "is the player even
  in the right area" check independent of any specific object being visible.
- **Physical objects are multiloc (impostor) objects**, not the 11-separate-static-ids model this
  design's first draft assumed. `ObjectID1` has both a "base" id per station (e.g.
  `MM_LAB_MACHINE_RETORT = 55389`) and the 11 content-variant ids (`MM_LAB_MACHINE_RETORT_EMPTY`
  through `..._MAL`) the game swaps the base id's *impostor* to, driven by the same varbits below
  — the same pattern `ObjectProvider.java` already resolves generically via
  `ObjectComposition.getImpostor()`. This means **`find_object("Retort")`-by-name (this
  codebase's existing convention, per `golem_crafting.py`) already works correctly today**,
  without needing any of the 11 variant ids — they're listed below only because
  `MixologyProvider` reads current content from varbits directly, which is simpler and more
  precise than inferring it from whichever id the object happens to resolve to.
  - Refiner: `MM_LAB_MILL` (`54904`), option "Operate".
  - Levers: Mox `MM_LAB_SWITCH_MOX` (`54868`), Aga `MM_LAB_SWITCH_AGA` (`54867`), Lye
    `MM_LAB_SWITCH_LYE` (`54869`), option "Operate" (10 paste per pull from the hopper; a 4th
    pull on a full vessel overrides the *oldest* selected paste, returning it to the hopper
    rather than wasting it — confirmed both by the wiki and by the plugin's own vessel-slot
    varbit model, see below).
  - Hopper: `MM_LAB_HOPPER` (`54903`), option "Deposit" (max 3,000 per paste type).
  - Mixing vessel: `MM_LAB_VESSEL` (`55395`, base/impostor id), option "Take-from".
  - Retort (Concentrate): `MM_LAB_MACHINE_RETORT` (`55389`, base/impostor id), option
    "Concentrate-potion" (+"Check").
  - Agitator (Homogenise): `MM_LAB_MACHINE_AGITATOR` (`55390`), option "Homogenise-potion"
    (+"Check").
  - Alembic (Crystalise): `MM_LAB_MACHINE_ALEMBIC` (`55391`), option "Crystalise-potion"
    (+"Check").
  - Conveyor belt: `MM_LAB_CONVEYOR` (`54917`), option "Fulfil-order" (deposits up to 3 potions
    at once, fulfilling as many current orders as possible; refreshes all 3 orders regardless of
    match).
  - Digweed (4 corners, `AlchemyObject.java`'s coordinates): North-east `MM_HERB_1` at
    `(1399, 9331, 0)`, south-east `MM_HERB_2` at `(1399, 9322, 0)`, south-west `MM_HERB_3` at
    `(1389, 9322, 0)`, north-west `MM_HERB_4` at `(1389, 9331, 0)` — all plane `0`.
- **Governing varbits** (`net.runelite.api.gameval.VarbitID`, all confirmed present in
  `runelite-api-1.12.35.jar`):
  - `MM_LAB_ORDER_1_TYPE` / `_MODIFIER`, `MM_LAB_ORDER_2_TYPE` / `_MODIFIER`,
    `MM_LAB_ORDER_3_TYPE` / `_MODIFIER` (ids `11315`-`11320`) — each order's potion-type index
    and processing-modifier index, both **1-indexed** (0 = no order yet / slot empty; real values
    are `client.getVarbitValue(id) - 1`, indexing into the tables below).
  - `MM_AVAILABLE_MOX` / `_AGA` / `_LYE` (ids `11431`-`11433`) — current hopper paste totals.
    Notably **not** read by the reference plugin at all (its own overlay doesn't display them),
    found instead by scanning the full `VarbitID` class for the `MM_*` prefix rather than only
    following the plugin's own usages — worth remembering as a research technique: a reference
    implementation shows what its author needed, not everything that exists.
  - `MM_LAB_MIXER_SLOT_0` / `_1` / `_2` (ids `11324`-`11326`) — the mixing vessel's 3 currently
    loaded paste slots, in pull order. More precise than inferring content from the vessel
    object's resolved impostor id (this design's first draft), since it exposes state mid-fill,
    not just once all 3 are loaded.
  - `MM_LAB_AGITATOR_POTION`, `MM_LAB_RETORT_POTION`, `MM_LAB_ALEMBIC_POTION` (ids `11340`-
    `11342`) — each station's currently-loaded potion type index (same 1-indexed convention as
    the order varbits; 0 = station empty).
  - `MM_AGITATOR_PROGRESS`, `MM_RETORT_PROGRESS`, `MM_ALEMBIC_PROGRESS` (ids `11327`-`11329`) —
    raw per-station processing progress.
  - `MM_LAB_HIT_SKILLSHOT_AGITATOR`, `MM_LAB_HIT_SKILLSHOT_ALEMBIC` (ids `11337`-`11338`) —
    pulses when a speedup click lands successfully. Useful as a hit/miss confirmation signal, but
    (per the reference plugin's own tick-counting logic around `agitatorQuickActionTicks`) likely
    a short-lived pulse — the *primary* click-timing signal should be the graphics cue below,
    which persists across its visual duration rather than a single tick.
  - `MM_HERB_READY_1` / `_2` / `_3` / `_4` (ids `11330`-`11333`) — 1 when that corner's digweed is
    collectible, matching the `AlchemyObject` corner order above (NE/SE/SW/NW).
  - `MM_LAB_VESSEL_READY` (id `11339`) — mirrors whether the vessel currently holds a
    take-able finished base (referenced by the plugin but not otherwise needed here, since the
    3 mixer-slot varbits already give strictly more detail).
- **Speedup graphics cues** (`net.runelite.api.gameval.SpotanimID`, confirmed present):
  `VFX_MACHINERY_ALCHEMY01_AGITATOR01` (`2954`) and `VFX_MACHINERY_ALCHEMY01_ALEMBIC01` (`2955`)
  — these are the exact graphics-object ids `GraphicsObjectProvider.java` (already built, generic,
  id-agnostic) will report at the station's tile when the click-now cue fires. **No new Java code
  is needed to detect them** — the existing `/graphics-objects` endpoint already exposes any
  graphics object's numeric id, and `2954`/`2955` are simply the ids to filter for. The Retort has
  no equivalent cue at all (confirmed absent from the reference plugin's varbit/graphics handling,
  consistent with the wiki's "click every tick, no timing window" description) — its speedup
  needs no cue detection, just "click while `MM_LAB_RETORT_POTION` is nonzero."
- **Recipe/points table** (`PotionType.java`'s declared order = the index the `_TYPE` varbits
  encode, 0-indexed; `PotionModifier.java`'s declared order = the index the `_MODIFIER` varbits
  encode). Points values are the wiki's own single-order figures (`data/raw/M/Mastering
  Mixology.txt`'s rewards table); XP values are the reference plugin's own constants, cross-
  checked against the wiki and consistent:

  | idx | Name | mox/aga/lye | Modifier idx → station | Points | XP |
  |---|---|---|---|---|---|
  | 0 | Mammoth-might mix | 3/0/0 | | 20 | 190 |
  | 1 | Mystic mana amalgam | 2/1/0 | | 30 | 215 |
  | 2 | Marley's moonlight | 2/0/1 | | 30 | 240 |
  | 3 | Alco-augmentator | 0/3/0 | | 20 | 190 |
  | 4 | Azure aura mix | 1/2/0 | | 30 | 265 |
  | 5 | Aqualux amalgam | 0/2/1 | | 30 | 290 |
  | 6 | Liplack liquor | 0/0/3 | | 20 | 190 |
  | 7 | Megalite liquid | 1/0/2 | | 30 | 315 |
  | 8 | Anti-leech lotion | 0/1/2 | | 30 | 340 |
  | 9 | Mixalot | 1/1/1 | | 60 | 365 |

  Modifier idx 0 = Homogenised (Agitator), 1 = Concentrated (Retort), 2 = Crystalised (Alembic) —
  **which station a given order uses is per-order, not fixed per potion name** (the same base can
  be ordered as, say, homogenised in one order and crystalised in another) — confirmed directly
  by `PotionOrder`/`PotionModifier` tracking modifier independently of potion type, which
  resolves what this design's first draft had flagged as an unconfirmed assumption in the
  `process` phase.
- **Herb → paste table** (paste amount per herb): Mox — Guam leaf (10), Marrentill (13), Tarromin
  (15), Harralander (20). Aga — Huasca (20), Irit leaf (30), Cadantine (34), Lantadyme (40),
  Dwarf weed (42), Torstol (44). Lye — Ranarr weed (26), Toadflax (32), Avantoe (30), Kwuarm (33),
  Snapdragon (40). Grimy herbs must be cleaned first; unfinished potions work directly. 6
  Herblore XP per item refined regardless of type.
- **Efficient pathing** (from the Strategies page): group orders by processing station rather
  than working order-by-order; Alembic → Retort → Agitator → conveyor belt is the wiki's
  recommended route, 6 ticks total worst-case per potion.

## Design

### 1. `MixologyProvider.java` (new) — fully specifiable now

```
GET /mixology-state
{
  "inLab": true,
  "orders": [
    {"name": "Mystic mana amalgam", "mox": 2, "aga": 1, "lye": 0, "station": "Retort"},
    ...
  ],
  "hopper": {"mox": 850, "aga": 1000, "lye": 620},
  "vesselSlots": ["mox", "aga", null],
  "stations": {
    "retort": {"potionName": "Mammoth-might mix", "progress": 12},
    "agitator": {"potionName": null, "progress": 0},
    "alembic": {"potionName": "Mixalot", "progress": 8}
  },
  "digweed": {"northEast": false, "southEast": true, "southWest": false, "northWest": false}
}
```

Reads every field via `client.getVarbitValue(VarbitID.X)`, using local `String[]`/`int[][]`
lookup tables reimplementing `PotionType`/`PotionModifier`'s declared order (see the table
above) — this file has no dependency on the `hex-agon` plugin at runtime, only on the same
RuneLite `VarbitID`/`SpotanimID` constants this project's Java code already depends on elsewhere.
`station` is resolved directly from the modifier index (Homogenised→Agitator,
Concentrated→Retort, Crystalised→Alembic), removing the need for the routine to guess or look it
up separately. Follows this codebase's existing provider conventions: plain `Map`/`List` JSON via
`BridgeProvider`, no caching needed (changes on player/server action, not a fixed tick cadence —
same reasoning as `PlayerProvider`, not the six providers `TickCache` covers).

### 2. `routines/mastering_mixology.py` (new)

```python
DEFAULT_CONFIG = {
    "enabled": True,
    "herbPriority": {
        "mox": ["Guam leaf", "Marrentill", "Tarromin", "Harralander"],
        "aga": ["Huasca", "Irit leaf", "Cadantine", "Lantadyme", "Dwarf weed", "Torstol"],
        "lye": ["Ranarr weed", "Toadflax", "Avantoe", "Kwuarm", "Snapdragon"],
    },
    "hopperTarget": 1000,
    "hopperLowWater": 150,
    "digweed": {"enabled": True},
    "speedups": {"retort": True, "agitator": True, "alembic": True},
    "maxObjectDistance": 15,
    "bank": {"teleportFallback": False},
    "breaks": {
        "enabled": True, "everyMinMinutes": 45, "everyMaxMinutes": 75,
        "durationMinMinutes": 3, "durationMaxMinutes": 12, "microBreaks": True,
    },
    "safety": {"stopOnDeath": True, "maxRuntimeHours": 0},
}

def get_config(bridge_config):
    config = controller.get_mastering_mixology_config()
    return config if config is not None else DEFAULT_CONFIG
```

**Phase state machine**, stored in `ctx.state.data["mm_phase"]` (defaults to `"bank_herbs"`),
dispatched from `tick(ctx)`:

- **`bank_herbs`**: open the bank; withdraw herbs per `herbPriority` (first candidate actually
  present, per paste type) up to a per-trip budget; deposit any leftover potions/junk. Walk to
  the refiner, deposit all withdrawn herbs (click every tick to double refine speed), walk to the
  hopper and deposit the resulting paste. Repeat (multiple bank trips) until `/mixology-state`'s
  `hopper` totals all reach `hopperTarget`, or safety-stop if the bank has none of a paste type's
  configured candidate herbs left at all. → `orders`.
- **`orders`**: `GET /mixology-state`. If any paste type needed by the orders this phase would
  choose to fulfil would drop the hopper under `hopperLowWater`, → `bank_herbs` first. Otherwise
  apply the max-points rule (see "Optimization strategy") to the 3 returned orders, recording the
  chosen subset in `ctx.state.data`. → `mix`.
- **`mix`**: for each chosen order, pull the 3 levers matching its combination, then take from
  the mixing vessel — **reading `vesselSlots` after each pull to confirm the actual contents
  match what was intended**, not just trusting the pull count (see Error handling). Repeat per
  chosen order. → `process`.
- **`process`**: walk Alembic → Retort → Agitator (per the wiki's efficient-pathing order),
  processing every unprocessed potion of the matching type currently held, applying the
  configured speedup clicks per station — Retort clicks every tick while
  `stations.retort.potionName` is non-null, Agitator/Alembic click once to start then again when
  `/graphics-objects` reports id `2954`/`2955` at the station's tile. → `deposit`.
- **`deposit`**: click the conveyor belt (fulfils up to 3 at once, refreshes all 3 orders
  regardless of match). → `orders`.

**Digweed** is handled inside the `orders`/`mix` phases via `/mixology-state`'s `digweed` field
(polled, not event-driven — no chat-message parsing needed): when a corner is ready and
`digweed.enabled`, path to and collect the plant; mix it into an in-progress MAL base if one
exists, otherwise hold it until the next MAL order is chosen (per the wiki's "always worth an
MAL" point-multiplier reasoning, this never conflicts with the max-points strategy — an MAL order
is already always made in full once present).

One-shot Herblore 60 / Children of the Sun pre-flight check, same shape as `blast_furnace.py`'s
`check_level_requirement`, gated so it never runs from `setup()` (pre-login false-trigger risk).

### 3. Wiring

- `controller.py`: `"MASTERING_MIXOLOGY": "routines/mastering_mixology.py"` in `ROUTINE_FILES`;
  `get_mastering_mixology_config()` (`api_get("/mastering-mixology-config")`), same shape as
  `get_golem_crafting_config()`; `get_mixology_state()` (`api_get("/mixology-state")`).
- `RoutineChoice.java`: add `MASTERING_MIXOLOGY("Mastering Mixology")`.
- `ControllerConfig.java`: new `@ConfigSection` with an enabled toggle, bank-teleport-fallback,
  digweed/speedup toggles, and the shared Breaks/Safety fields every section exposes.
  `herbPriority`/`hopperTarget`/`hopperLowWater` are Python-side defaults, not Java-exposed config
  — same reasoning as `golem_crafting.py`'s `furPriority` (no per-player choice UI needed for a
  priority list; the player edits the Python default directly if they want a different herb).
- `ConfigExporter.java`: new `exportMasteringMixology(ControllerConfig config)` returning
  `{enabled, bank: {teleportFallback}, digweed: {enabled}, speedups: {...}, breaks, safety}`.
- `ControllerPlugin.java`: `new MixologyProvider(client, clientThread, server).register();`
  alongside the other provider registrations.

### 4. Error handling & safety

- Hard safety-stops: `/mixology-state`'s `inLab` false or none of the known fixtures found
  nearby (wrong location), no configured herb candidates left in the bank for a paste type the
  current strategy needs, sub-60 Herblore or incomplete Children of the Sun (pre-flight check).
- **Always re-read hopper totals, vessel slot contents, station state, and active orders from the
  bridge rather than tracking them locally.** This is the same class of bug this codebase just
  spent a full project fixing in the HTTP bridge's own caching (see
  `docs/superpowers/specs/2026-08-12-bridge-tick-cache-freshness-design.md`): trusting a locally
  maintained count instead of ground truth is exactly how staleness bugs happen. It matters even
  more here because the vessel's "4th lever pull overrides the oldest paste" mechanic means a
  routine that just counts its own lever-pulls can silently desync from the real vessel contents
  — especially after a simulated misclick (`CLAUDE.md`'s documented anti-detection humanization,
  `MISTAKE_MISCLICK_CHANCE`). `golem_crafting.py`'s hunt-phase reclick-gate bug (waiting forever
  for an inventory change a misclick will never produce) is the concrete precedent for what
  happens when this isn't handled — reconcile against `vesselSlots` after every lever pull, don't
  assume.
- Speedup timing: per the wiki, a consistently-missed cue costs more than not attempting the
  speedup at all. `speedups.{retort,agitator,alembic}` are independently toggleable so a station
  whose cue proves unreliable to hit in practice (Arduino/physical-click latency against a
  short-lived graphics cue is a real risk the reference plugin, which drives a mouse in zero
  time, doesn't share) can be disabled without losing the other two.

## Testing

Since every value `MixologyProvider` needs is now a confirmed constant rather than a research
gap, this no longer needs golem_crafting.py's fully-staged live-investigation approach — but live
confirmation still matters (a verified *constant* is not the same as verified *behavior*):

1. Manual one-cycle walkthrough (bank herbs → refine → hopper → read one order set → mix →
   process → deposit) confirming `/mixology-state` reports real data matching what's on screen,
   and every object name/menu-option string above resolves as expected, before any unattended
   run.
2. Confirm the Agitator/Alembic speedup detection live: trigger each station's cue and watch
   `/graphics-objects` for id `2954`/`2955` appearing at the right moment, and cross-check
   against `MM_LAB_HIT_SKILLSHOT_AGITATOR`/`_ALEMBIC` pulsing on a successful click.
3. Short live run of the full phase loop, watching phase transitions in the debug log.
4. Deliberately let digweed spawn during a run to confirm `digweed` field polling and the
   MAL-holding logic work.
5. Only then a longer unattended soak run.
