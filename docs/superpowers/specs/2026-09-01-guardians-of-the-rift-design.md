# Guardians of the Rift routine — design

2026-09-01. Status: approved (user authorized spec → plan → implementation without further
gates, subagent-driven).

Builds `ControllerAddon/routines/guardians_of_the_rift.py`: plays Guardians of the Rift
unattended on mass/official worlds — mine fragments → craft essence at the Workbench →
imbue at the active altars (balancing elemental/catalytic energy) → power the Great
Guardian → place/recharge barrier cells → repeat, with between-round pouch repair and
cell restock. Reward points are hoarded, never spent; crafted runes go into the Deposit
Pool.

Primary sources, in order of authority:

1. **`GET /api/minigames/guardians-of-the-rift`** (vendored m8aq reader
   `minigames/GuardiansOfTheRift.java`) — the routine's ground truth at runtime. Confirm
   the exact kebab-cased path against `/api/index` before hardcoding.
2. OSRS Wiki "Guardians of the Rift" (offline snapshot, revision 15303509) and
   "/Strategies" (revision 15279817) — all mechanics prose below.
3. **No human recording exists.** The user chose to spec and implement from research
   (the golem_crafting path). Every timing and click-target below is a first draft;
   the "Live-verification checklist" section is the debugging worklist.

## Decisions taken with the user (2026-08-31)

- **Account**: mid Runecraft (~50–76), normal degradable pouch set (small/medium/large/
  giant as available). No colossal pouch. Which altars are enterable follows from live
  RC level + quest state; the routine never hardcodes an altar list.
- **Pouch repair: NPC Contact** (Lunar spellbook, 67 Magic, runes carried/rune pouch),
  cast between rounds when the pouch reader says a pouch is degraded.
- **Strategy: reactive state-driven loop** + opportunistic huge-remains portal trips.
  No wiki timetable. Mass worlds assumed (contested pixels everywhere).
- **Rewards: hoard points, deposit runes.** Never touch the Rewards Guardian; deposit
  crafted runes at the Deposit Pool during crafting passes.
- **No recording first**; live-debug the guesses directly.

## Game mechanics the design rests on (wiki, revision-pinned)

- A round: prep window (~2 min, power 0), then battle; players charge the Great
  Guardian 0→100%; abyssal creatures drain it; 0% = failed round. At 60% a mass
  barrier-damage event fires. At 100% the round ends with a grace period.
- **Energy**: each guardian stone used on the Great Guardian = 2 energy of its type
  (poly = 3) + 5 RC xp; placing a barrier = 2 energy in BOTH types; **repairing a
  broken cell tile (12 fragments) = 25 energy in BOTH types** — the single best
  action available; recharging/strengthening per tier table. ≥300 total energy at
  round end pays RC-level × 45 xp. One point per 100 energy per type (fractional
  remainder rolls). Caps: 1000/type, 1200 total per game.
- **Portals to huge remains**: first spawns 160s into a round, then every 140s, open
  ~25s. The HUD reports open/position/seconds and the reader parses all three.
- **Altars**: exactly one elemental + one catalytic portal active at a time; the HUD
  rotation timer is parsed to seconds. Imbuing needs the altar's ordinary RC level.
  Points/xp scale with essence crafted, NOT altar tier — never wait for a "better"
  altar (wiki: only Air/Mind is worth skipping, and v1 doesn't even do that).
- **Cells**: up to 10 uncharged carried, exactly 1 charged at a time. Uncharged cells
  charge as a side effect of imbuing at an altar; the tier is the altar's tier.
  Cell tiles (barriers) are read from the reader's `barriers` set with per-tile state
  (BROKEN_REPAIRABLE / BROKEN / INACTIVE / WEAK..OVERCHARGED / INACTIVE_NO_OP).
- **Portal talismans** (1/250 per essence imbued) are round-scoped junk for this
  design: v1 ignores them (recommend the user right-click "Toggle-talisman" off once
  per portal by hand; the reader exposes the per-altar toggle varbits so the routine
  can log when drops are still enabled).
- Fragments, essence, and talismans are wiped at round start and on leaving the
  temple — there is nothing to protect across rounds except pouches and cells.
- Powering the guardian restores run energy (1%/stone + spec-bar bonus) — run energy
  management is free and out of scope.

## Prerequisites (checked in `setup()`, safety-stop with a named reason if missing)

- Player is in the main temple (reader `inMainTemple` true; region 14484). v1 does not
  travel there. If in the lobby (`inArena` false), the routine passes the entry
  barrier itself (see between-rounds phase).
- Pickaxe present (inventory or equipped — check `/equipment` and `/inventory` for
  name containing "pickaxe").
- Lunar spellbook active if `repair_pouches` is enabled (verify: `/api/player/magic`
  reports the spellbook; if it cannot, degrade to a warning log and disable repair
  rather than stopping).
- Essence pouches in inventory are noted (count + names) at setup for logging only —
  the live fill/empty logic re-reads `/api/storage/essence-pouches` every use.

## Config

```python
DEFAULT_CONFIG = {
    "use_large_remains": False,   # 56 Agility rubble shortcut to the east large-remains pocket
    "repair_pouches": True,       # NPC Contact -> Dark Mage between rounds when degraded
    "min_uncharged_cells": 5,     # restock from the cell table between rounds below this
    "craft_at_fragments": 60,     # start a workbench pass at/above this many fragments
    "altar_at_essence": 20,       # head for an altar when inventory essence >= this
                                  # (pouches are filled during the workbench pass)
}
```

`get_config` reads `DEFAULT_CONFIG` only (gemstone_crab pattern); no
`ControllerConfig.java` section. GUI wiring per the standing rule: `RoutineChoice`
enum entry `GUARDIANS_OF_THE_RIFT` + matching `ROUTINE_FILES` key.

## Ground truth: what the API gives us

`/api/minigames/guardians-of-the-rift` (fresh read, no TickCache; `None` = endpoint
unavailable, `error` key = reader failed, neither means "no game"). Gson serializes
FIELDS, so the Java convenience methods (`isRoundActive()` etc.) are NOT in the JSON —
Python re-derives them from the raw fields:

- `hudVisible`, `participating`, `finishedGame`, `banned`
- `guardianPower` (-1 unavailable), `altarRotationSecondsRemaining`
- `elementalEnergy`, `catalyticEnergy` (earned this game)
- `elementalAltar`, `catalyticAltar` (enum NAMES: "AIR", "CHAOS", ..., "UNKNOWN")
- `guardianEssencePortalOpen`, `guardianEssencePortalPosition` (compass text),
  `guardianEssencePortalSecondsRemaining`
- `inMainTemple`, `inArena`, `currentAltar` (enum name — which altar room we stand in),
  `miningArea` ("NONE"/"EAST_LARGE_REMAINS"/"WEST_HUGE_REMAINS"),
  `minedFragmentsThisGame`
- `inventory`: `guardianFragments`, `guardianEssence` (loose, excludes pouches),
  `unchargedCells`, `chargedCellTier`, `chargedCells` (map by tier name),
  `catalyticGuardianStones`, `elementalGuardianStones`, `polyelementalGuardianStones`,
  `polycatalyticGuardianStones`, `portalTalismans` (map by altar name),
  `chiselPresent`, `emptySlots`
- `barriers`: set of `{location: {x,y,plane}, state: <BarrierState name>}`
- `scene`: classified NPC lists (rift guardians, barrier NPCs, abyssal creatures) —
  v1 uses none of it; it exists for later
- `persistent.cordeliaPouchRepairEnabled` etc. — logged, not used
- `settings.portalTalismanDropsDisabled` — logged at setup when any drop is enabled

Phase derivation (pure function, unit-tested):

```
round_state(api) ->
    "unavailable"  api is None or error key or not hudVisible and not inMainTemple
    "outside"      not inMainTemple
    "lobby"        inMainTemple and not inArena
    "waiting"      participating and not finishedGame and guardianPower == 0
    "active"       participating and not finishedGame and 0 < guardianPower < 100
    "complete"     finishedGame or guardianPower >= 100   (grace period)
    "idle"         otherwise (in arena, not yet registered as participating)
```

Pouches: `/api/storage/essence-pouches` for contents + degradation state per pouch.
XP: `runecraftXp` / `miningXp` on `GET /player` (added by this work, see Java changes).

## Phase dispatch (`tick()`)

| round_state | handler |
|---|---|
| `unavailable` | log once, return (bridge hiccup is not a stop) |
| `outside` | safety-stop ("start at the Temple of the Eye") |
| `lobby` | enter_arena_phase (click entry barrier "Pass"/"Quick-pass"; ceiling `ENTER_MAX_CLICKS` 6 → safety-stop) |
| `waiting`, `complete`, `idle` | between_rounds_phase |
| `active` | in_round dispatch (priority ladder below) |

`banned` true at any point → safety-stop with the flag named.

## between_rounds_phase

In priority order, one action per tick:

1. **Repair pouches** (if `repair_pouches` and any pouch degraded): cast NPC Contact
   (spellbook → spell widget, mahogany_homes' cast pattern), select Dark Mage in the
   contact interface (widget child TO VERIFY live via `/widgets/dump`), advance the
   dialogue with Space until closed. Confirm: pouch reader no longer reports degraded.
   Ceiling `CONTACT_MAX_CASTS` (4) per between-round window → log and give up until
   next window (NOT a safety stop — degraded pouches still work at reduced capacity).
2. **Restock uncharged cells** below `min_uncharged_cells`: click the uncharged-cells
   table near the entry barrier ("Take"). Confirm on `unchargedCells` increase.
3. **Deposit stray runes** if any runes in inventory: Deposit Pool.
4. **Pre-position** near the guardian-parts mining spot; then idle (`human_delay`).

Grace-period (`complete`) uses the same handler — restock/repair happens the moment
the round ends, which is exactly when the between-round window opens.

## in_round priority ladder

Checked top to bottom each tick; first match acts. Every action confirms on an
un-gated signal and has a counted ceiling (table at the end).

1. **Repair a broken barrier** — a `BROKEN_REPAIRABLE` tile exists AND
   `guardianFragments >= 12`. Click the tile ("Repair"). Confirm: fragments drop by 12
   OR tile state changes. 25 energy both types.
2. **Huge-remains portal trip** — `guardianEssencePortalOpen` AND pouches+inventory
   not full AND `guardianEssencePortalSecondsRemaining` ≥ `PORTAL_MIN_SECONDS` (8;
   don't chase a closing portal). Walk to/click the portal object ("Enter"), mine huge
   remains (`miningArea == "WEST_HUGE_REMAINS"` confirms arrival), fill pouches from
   mined essence, return through the exit portal when full or the pocket's portal
   despawns.
3. **Spend stones + cell** — any guardian stones held: click the Great Guardian
   (power-up; confirm on energy counters / stones leaving inventory). Then if a
   charged cell is held: pick a cell tile from `barriers` — first `INACTIVE`
   (place, 2 energy both), else the lowest-tier live barrier (recharge). Confirm on
   `chargedCellTier` returning to NONE.
4. **Altar trip** — loose `guardianEssence >= altar_at_essence` OR (pouches full AND
   any loose essence): pick the active altar (from `elementalAltar`/`catalyticAltar`)
   whose energy counter is LOWER (balances points); skip an altar whose RC level
   exceeds the live level or whose quest gate fails (the routine carries the wiki's
   level table; quest-gated catalytic altars simply fall back to the other type).
   Click its Portal Guardian ("Enter"). Inside (`currentAltar != "UNKNOWN"`):
   click the Altar (imbue — converts loose essence to stones and charges cells;
   confirm on `runecraftXp` increase), empty pouches (inventory "Empty" menu action,
   fur-pouch idiom), imbue again, repeat until pouches and inventory are out of
   essence, then click the exit portal. `altarRotationSecondsRemaining` <
   `ALTAR_MIN_SECONDS` (10) defers ENTERING (mid-trip rotation is fine — the exit
   portal persists).
5. **Craft essence** — `guardianFragments >= craft_at_fragments` OR (mining pocket
   exhausted and fragments > 0): click Workbench ("Work-at"); it converts fragments →
   loose essence over ticks; fill pouches whenever loose essence appears and a pouch
   has room (fill via pouch's default click at... TO VERIFY: essence pouch Fill is an
   inventory menu action, not bank-gated like the fur pouch — use explicit
   `click_menu_action` "Fill"); deposit runes at the adjacent Deposit Pool if any.
   Confirm crafting on fragments↓/essence↑ deltas.
6. **Mine** — default target "Guardian remains"/"Guardian parts" at the temple edges;
   `use_large_remains` routes through the rubble shortcut to the east pocket instead.
   Confirm on `miningXp`/`minedFragmentsThisGame`/fragments delta. Re-click when the
   node depletes (object transform) or the player idles with no delta for
   `MINE_RECLICK_S`.

Inventory pressure: if `emptySlots == 0` blocks a needed pickup, drop portal
talismans first (the one junk item class), then proceed.

## Clicking policy (applies to every world click in this routine)

Mass worlds are the golem-plinth environment permanently. From day one:

- `travel.can_reach` gate before the first click on any object; `open_door_toward` is
  irrelevant here (no doors) but the unreachable branch defers, counts, and ceilings.
- `object_refresh_target`/`npc_index_refresh_target` + `refresh_legs` on every click;
  never a bare snapshot click.
- `expected_action`/`expected_target` hover verification on the contested clicks
  (mining nodes, Great Guardian, Workbench, portal guardians); a withheld click
  spends the same retry budget and escalates.
- Escalation tiers exactly as golem_crafting: bare click → one fast `/last-action`-
  verified retry → `click_menu_action` by option name with `offset=(0, 0)` and a
  fresh re-resolve.
- Walks: `travel.walk_to` (worldmap routes; the temple is one open room so hops are
  short); plain ground-tile walks are shift-clicked as standard.
- The temple and altar rooms are believed NOT instanced (static regions; the reader
  itself keys on `getRegionID`) — VERIFY on first live session before trusting
  `/minimap/walk` or `/ground/walk` there (standing instancing gotcha).

## Java changes (one rebuild)

1. `PlayerProvider`: add `runecraftXp` and `miningXp` (`client.getSkillExperience`),
   same lines as `smithingXp`.
2. `RoutineChoice`: add `GUARDIANS_OF_THE_RIFT`.

Nothing else — no new provider; the m8aq reader carries the whole state surface.
Never edit files under `m8aq/api/`.

## Testing (hermetic, `_no_live_bridge` fixture discipline)

Canned API snapshots as plain dicts; pure functions get direct unit tests:

- `round_state` derivation table (every branch, including `None`, `error`,
  `banned`, power -1, power 100, lobby).
- Priority-ladder selection: given a snapshot, which action fires (broken barrier
  beats portal beats stones beats altar beats craft beats mine; thresholds honored).
- Altar choice: lower-energy type wins; RC-level and quest gating falls back to the
  other active altar; Air/Mind no special-casing.
- Cell-tile choice: INACTIVE before recharge; lowest live tier for recharge;
  BROKEN (non-repairable) never targeted.
- Pouch-repair trigger from a degraded `/api/storage/essence-pouches` snapshot.
- Ceilings: each counted loop safety-stops at its bound with a non-duplicated reason
  string (no engine-suffix repetition).

## Ceilings (all safety-stop unless noted)

| Counter | Bound | Scope/reset |
|---|---|---|
| `ENTER_MAX_CLICKS` | 6 | entry barrier; reset on `inArena` |
| `CONTACT_MAX_CASTS` | 4 | per between-round window; log-and-skip, not stop |
| `ALTAR_ENTER_MAX` | 5 | per trip; reset on `currentAltar` change |
| `IMBUE_MAX_CLICKS` | 8 | per altar visit; reset on xp gain |
| `POWERUP_MAX_CLICKS` | 6 | per stone batch; reset on energy delta |
| `CELL_MAX_CLICKS` | 6 | per charged cell; reset on cell consumed |
| `MINE_STALL_TICKS` | 25 | no fragment/xp delta while mining; reset on delta |
| `WORKBENCH_STALL_TICKS` | 20 | no craft delta; reset on delta |
| `PORTAL_TRIP_MAX_TICKS` | 60 | inside huge-remains pocket; forces exit, not stop |

Counters whose reset marks a SUCCESS live where the success is detected (the
dispatcher/confirm site), not in the attempt handler — the mh_contact_casts lesson.

`before_break`: no teardown needed (nothing round-scoped survives anyway); returns
immediately.

## Live-verification checklist (the no-recording tax — first live session works this)

1. `/api/index` exact path for the reader; field names in one `/api/all` diff.
2. Object names + action strings: entry barrier, uncharged-cells table, Workbench,
   Deposit Pool, portal guardians ("Guardian of Air"...), huge-remains portal, cell
   tiles (GroundObjects — confirm `/objects` serves them; if not, click by
   `/ground/walk` tile + menu), Great Guardian NPC power-up action, altar + exit
   portal inside altar rooms.
3. Instancing check (temple + altar rooms) before any minimap/ground forward
   projection is trusted.
4. NPC Contact interface: Dark Mage widget group/child via `/widgets/dump`.
5. Essence-pouch Fill/Empty menu wording outside a bank.
6. All thresholds/timings (`craft_at_fragments`, stall bounds, portal seconds).
