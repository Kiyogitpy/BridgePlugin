# Mastering Mixology routine

## Problem

We want a new automation routine for Mastering Mixology, a members-only Herblore minigame in
Aldarin (released 2024-09-25). Players refine herbs into paste, mix paste combinations into
potion bases, process those bases at one of three stations, and turn finished potions in against
rotating orders for Herblore XP and resin (spent on minigame-specific rewards).

Unlike `golem_crafting.py`'s subject (9 days old at design time, effectively undocumented), this
minigame is well-established and extensively documented in the local OSRS Wiki mirror, including
a dedicated Strategies page with several competing "efficient play" approaches and community
simulation data on points-per-paste ratios. The gap here isn't game-mechanics research — it's
that the minigame's core interactive HUD (active orders, hopper paste totals) lives in a widget
interface that has never been mapped in RuneLite's own `InterfaceID` gameval constants (confirmed
directly: zero of the 971 existing `InterfaceID` nested classes reference Mixology, Alchemy, or
similar), and this codebase's bridge has no existing provider for it either. The physical
scenery objects (refiner, levers, hopper, mixing vessel, processing stations, conveyor belt) are
fully documented with real object IDs and need no new research.

## Scope

1. A new `routines/mastering_mixology.py`: full pipeline (bank herbs → refine → hopper → read
   orders → mix → process → deposit), optimizing for maximum total points per paste (see
   "Optimization strategy" below), with digweed collection and processing-station speedup clicks.
2. A new Java provider, `MixologyProvider`, exposing the 3 active orders (name, paste
   combination, points) and the hopper's 3 current paste totals — the one piece of this design
   that cannot be finalized without a live Widget Inspector session (see "Open research items").
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
  mechanics, level requirements, points/reward tables, and the strategy math — this content is
  mature (not newly-released like golem_crafting's subject was) and includes real object IDs.
- **RuneLite `InterfaceID` gameval check** (`runelite-api-1.12.35.jar`, all 971 `InterfaceID`
  nested classes enumerated): confirmed no named constant exists for any Mastering Mixology
  widget. This is the basis for flagging the order-HUD/hopper-totals provider as unresearched
  below, rather than guessing at widget IDs.
- **No live RuneLite session, Widget Inspector capture, or bridge query was performed for this
  design** (out of scope for a brainstorming/spec pass, same convention `golem_crafting.py`'s
  design doc used). Every object ID below comes from the wiki's own infobox metadata, not a live
  capture — cross-check against a running client before trusting it unattended, per this
  codebase's standing convention.

## Key facts from research

- **Requirements**: Herblore 60 (not boostable) and Children of the Sun quest completion. All 9
  potion bases unlock progressively up to level 81 (Mixalot).
- **Location**: Aldarin, members. A bank chest exists in the lab itself.
- **Known object IDs** (from wiki infoboxes, unverified live):
  - Refiner: `54904`, option "Operate".
  - Levers: Mox `54868`, Aga `54867`, Lye `54869`, option "Operate" (10 paste per pull from the
    hopper; a 4th pull on a full vessel overrides the *oldest* selected paste, returning it to
    the hopper rather than wasting it).
  - Hopper: `54903`, option "Deposit" (max 3,000 per paste type).
  - Mixing vessel: 11 id variants by current content — `54918` empty, `54919` Mammoth-might mix,
    `54920` Mystic mana amalgam, `54921` Marley's moonlight, `54922` Alco-augmentator, `54923`
    Aqualux amalgam, `54924` Azure aura mix, `54925` Liplack liquor, `54926` Anti-leech lotion,
    `54927` Megalite liquid, `54928` Mixalot. Option "Take-from". **The vessel's current content
    is directly readable via `/objects`'s returned id — no widget needed for this one.**
  - Retort (Concentrate): 11 id variants `54870`-`54880` in the same content order as above,
    option "Concentrate-potion" (+"Check").
  - Agitator (Homogenise): 11 id variants `54881`-`54891`, option "Homogenise-potion" (+"Check").
  - Alembic (Crystalise): 11 id variants `54892`-`54902`, option "Crystalise-potion" (+"Check").
  - Conveyor belt: `54917`, option "Fulfil-order" (deposits up to 3 potions at once, fulfilling
    as many current orders as possible; refreshes all 3 orders regardless of match).
- **Recipe reference** (all 9 bases, mnemonic = first letter of each word = paste type):
  AAA/MMM/LLL (single-paste, level 60), MMA/MML/AAM/ALA/MLL/ALL (double-paste, level 63-78), MAL
  = Mixalot (level 81, one of each paste).
- **Herb → paste table** (paste amount per herb): Mox — Guam leaf (10), Marrentill (13), Tarromin
  (15), Harralander (20). Aga — Huasca (20), Irit leaf (30), Cadantine (34), Lantadyme (40),
  Dwarf weed (42), Torstol (44). Lye — Ranarr weed (26), Toadflax (32), Avantoe (30), Kwuarm (33),
  Snapdragon (40). Grimy herbs must be cleaned first; unfinished potions work directly. 6
  Herblore XP per item refined regardless of type.
- **Processing speedups**: Alembic — click once to start, click again on the 4th pump (33% time
  saved, +14 XP; a mistimed click can still recover the bonus by clicking on the 7th pump after).
  Retort — click every tick while processing (50% time saved, +2 XP per click). Agitator — click
  once to start, click again within a 2-tick window after a sound/visual cue (33-66% time saved,
  +14/+16 XP; consistently missing this window is worse than not attempting it).
- **Digweed**: spawns are announced by a chatbox message (usable via the existing UDP
  `CHAT_MESSAGE` event, no new provider needed) and appear as a collectible plant in one of the
  room's corners. Doubles XP/resin on whatever potion it's mixed into; only usable while the
  potion is still in the minigame area. Holding it in inventory suppresses new spawns — the
  wiki's advised trick is stacking it on the floor (3-minute despawn timer) or mixing it
  immediately into an unprocessed MAL base and holding that until an MAL order appears.
- **Efficient pathing** (from the Strategies page): group orders by processing station rather
  than working order-by-order; Alembic → Retort → Agitator → conveyor belt is the wiki's
  recommended route, 6 ticks total worst-case per potion.

## Design

### 1. `MixologyProvider.java` (new) — the one open research item

Exposes, from the minigame's HUD widget (**IDs to be determined via a live Widget Inspector
session before this file can be written**):

```
GET /mixology-orders
{
  "orders": [
    {"name": "Mystic mana amalgam", "mox": 2, "aga": 1, "lye": 0, "points": 30},
    ...
  ],
  "hopper": {"mox": 850, "aga": 1000, "lye": 620}
}
```

Follows this codebase's existing provider conventions once the widget IDs are known: plain
`Map`/`List` JSON via `BridgeProvider`, no caching needed (this data changes on player action,
not on a fixed tick cadence, so it's a plain always-live `register()` endpoint like
`PlayerProvider`, not tick-cached like the six providers `TickCache` covers).

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
  hopper and deposit the resulting paste. Repeat (multiple bank trips) until `/mixology-orders`'s
  `hopper` totals all reach `hopperTarget`, or safety-stop if the bank has none of a paste type's
  configured candidate herbs left at all. → `orders`.
- **`orders`**: `GET /mixology-orders`. If any paste type needed by the orders this phase would
  choose to fulfil would drop the hopper under `hopperLowWater`, → `bank_herbs` first. Otherwise
  apply the max-points rule (see "Optimization strategy") to the 3 returned orders, recording the
  chosen subset in `ctx.state.data`. → `mix`.
- **`mix`**: for each chosen order, pull the 3 levers matching its combination, then take from
  the mixing vessel — **reading the vessel's own object id after the pulls to confirm the actual
  content matches what was intended**, not just trusting the pull count (see Error handling).
  Repeat per chosen order. → `process`.
- **`process`**: walk Alembic → Retort → Agitator (per the wiki's efficient-pathing order),
  processing every unprocessed potion of the matching type currently held, applying the
  configured speedup clicks per station. → `deposit`.
- **`deposit`**: click the conveyor belt (fulfils up to 3 at once, refreshes all 3 orders
  regardless of match). → `orders`.

**Digweed** is handled via `on_event(ctx, event_type, payload)` on `CHAT_MESSAGE`, not a phase:
when the spawn message is seen and `digweed.enabled`, path to and collect the plant (object
lookup by name, exact id unresearched); mix it into an in-progress MAL base if one exists,
otherwise hold it until the next MAL order is chosen in the `orders` phase (per the wiki's
"always worth an MAL" point-multiplier reasoning, this never conflicts with the max-points
strategy — an MAL order is already always made in full once present).

One-shot Herblore 60 / Children of the Sun pre-flight check, same shape as `blast_furnace.py`'s
`check_level_requirement`, gated so it never runs from `setup()` (pre-login false-trigger risk).

### 3. Wiring

- `controller.py`: `"MASTERING_MIXOLOGY": "routines/mastering_mixology.py"` in `ROUTINE_FILES`;
  `get_mastering_mixology_config()` (`api_get("/mastering-mixology-config")`), same shape as
  `get_golem_crafting_config()`.
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

- Hard safety-stops: none of the six known fixtures found nearby (wrong location / not in the
  lab), no configured herb candidates left in the bank for a paste type the current strategy
  needs, sub-60 Herblore or incomplete Children of the Sun (pre-flight check).
- **Always re-read hopper totals, vessel content, and active orders from the bridge rather than
  tracking them locally.** This is the same class of bug this codebase just spent a full project
  fixing in the HTTP bridge's own caching (see
  `docs/superpowers/specs/2026-08-12-bridge-tick-cache-freshness-design.md`): trusting a locally
  maintained count instead of ground truth is exactly how staleness bugs happen. It matters even
  more here because the vessel's "4th lever pull overrides the oldest paste" mechanic means a
  routine that just counts its own lever-pulls can silently desync from the real vessel contents
  — especially after a simulated misclick (`CLAUDE.md`'s documented anti-detection humanization,
  `MISTAKE_MISCLICK_CHANCE`). `golem_crafting.py`'s hunt-phase reclick-gate bug (waiting forever
  for an inventory change a misclick will never produce) is the concrete precedent for what
  happens when this isn't handled — reconcile against the vessel's actual object id after every
  lever sequence, don't assume.
- Speedup timing: per the wiki, a consistently-missed cue costs more than not attempting the
  speedup at all. `speedups.{retort,agitator,alembic}` are independently toggleable so a station
  whose cue proves unreliable to detect/hit during live verification can be disabled without
  losing the other two.

## Open research items (block implementation, not just polish)

1. **`MixologyProvider`'s widget IDs** — needs a live Widget Inspector session against the
   running client, standing in the minigame with an active order, to find the order-list and
   hopper-total widget/component IDs. Nothing else in this design can be fully implemented
   without this (`orders` phase has no order data to act on otherwise).
2. **Processing-station speedup cue detection** — the wiki describes these as sound/visual cues
   (pump count, "fireworks" effect), not anything already exposed by the bridge.
   `GraphicsObjectProvider` (already generic/id-agnostic, built for exactly this class of thing —
   see its own docstring's reference to `tormented_demon.py`'s fireball-dodge use) is the
   strongest existing candidate to check first; each station's own "Check" menu option (reports
   progress as a game message, per the infobox) is a fallback worth testing live if the graphics
   approach doesn't pan out.
3. **Digweed's exact object id/name and spawn-message text** — the wiki confirms the mechanism
   (chat message + collectible plant) but not the literal object id or message string needed to
   match on.

## Testing

Same staged-verification convention as `golem_crafting.py`'s design (more unverified surface than
a typical routine here, concentrated in the two open research items above):

1. Live Widget Inspector session to resolve open research item #1 — prerequisite for writing
   `MixologyProvider` at all.
2. Live capture session (manually trigger each station's speedup cue while watching
   `/graphics-objects` and the "Check" option's message text) to resolve #2.
3. Manual one-cycle walkthrough (bank herbs → refine → hopper → read one order set → mix →
   process → deposit) confirming every object id/menu-option string above resolves as expected
   live, before any unattended run.
4. Short live run of the full phase loop, watching phase transitions in the debug log.
5. Deliberately let digweed spawn during a run to confirm the `CHAT_MESSAGE` interrupt and
   MAL-holding logic work (resolves open research item #3 in the process).
6. Only then a longer unattended soak run.
