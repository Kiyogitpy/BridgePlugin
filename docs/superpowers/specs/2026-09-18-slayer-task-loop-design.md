# Slayer task loop (Aya ↔ Kuradal boosting) and the combat/slayer split — design

**Date:** 2026-09-18
**Status:** approved 2026-09-18; sub-project 1 (split + plumbing) implemented per docs/superpowers/plans/2026-09-18-slayer-split-and-plumbing.md; sub-projects 2-3 pending the §12 recording. Every game fact below is cited to the
offline wiki dump (`osrs-wiki-data-master/raw/...`) or the RuneLite 1.12.39 sources in the
Gradle cache; every "measure live" item is listed in §12.
**Sources:** `raw/A/Aya.txt`, `raw/K/Kuradal.txt`, `raw/N/NPC Contact.txt`,
`raw/S/Slayer reward point.txt`, `raw/S/Slayer Master.txt`, `raw/S/Slayer Rewards.txt`,
`raw/T/Turael_Slayer assignments.txt`, `raw/D/Duradel_Slayer assignments.txt`,
`raw/S/Slayer ring.txt`, `raw/E/Enchanted gem.txt`; RuneLite `runelite-api-1.12.39-sources.jar`
(`gameval/VarPlayerID`, `gameval/VarbitID`, `gameval/InterfaceID`, `gameval/DBTableID`) and
`client-1.12.39-sources.jar` (`plugins/slayer/SlayerPlugin.java`); the three codebase surveys
run 2026-09-18 (summarised in §3).

---

## 1. What the user asked for

> "split combat and slayer. combat will be what combat/slayer does today: just kill the
> selected monster. slayer is going to get a task from a slayer master, most likely Aya and
> Kuradal, do 9 tasks with Aya then swap to Kuradal to get the streak bonus. Every 10th, 50th
> and 100th gives a massive bonus so we don't want a task from Aya on that one -- task 20 would
> be Kuradal. I want it to work end to end from task to task, picking the task with NPC
> Contact."

Three deliverables fall out of that:

1. **`routines/combat.py`** -- today's `routines/slayer.py`, byte-for-byte behaviour, under the
   name it should always have had. Selected in the panel as **Combat**.
2. **`routines/slayer.py`** -- a NEW task orchestrator: decide which master, get the task by NPC
   Contact, bank for it, travel to it, fight it (by delegating to the combat engine), notice it
   is done, repeat. Selected in the panel as **Slayer**.
3. The **plumbing neither can do without**: a bridge endpoint that reads the task state
   (nothing in the repo reads it today), a generic transport-leg runner that can change floors
   (nothing can today), and the dialogue/NPC-Contact helpers promoted out of the routines that
   privately own copies.

## 2. The game facts that shape everything

Quoted, not paraphrased, because three of them are traps.

**F1. Aya tasks give 0 points but DO count toward the streak.** `Slayer reward point.txt:47`:
*"Tasks assigned by Turael/Spria give no points but do contribute to the four needed to start
gaining points."* `Aya.txt:20`: *"completing a task assigned by her will always reward 0
points."* So nine Aya tasks advance the streak from 10 to 19 at no cost, and the 20th from
Kuradal lands on the milestone.

**F2. Asking Aya to REPLACE another master's task resets the streak to zero.** `Aya.txt:29`:
*"After a task change is processed, your task streak will also be reset to zero!"*
`Slayer Master.txt:110`: *"if you're on the 48th task and take a replacement one from Turael,
your completed tasks will be set to 0."* Taking an Aya task when you have **no** task is not
a change and resets nothing. **The routine therefore never contacts Aya while a task is
active.** This is the single invariant everything else protects.

**F3. Aya refuses to touch a task she could have assigned herself.** `Aya.txt:25`: *"If
another master has assigned you a task Aya could assign herself, your request will not work.
Additionally, Aya will refuse to change her own tasks."* The one monster on BOTH lists is
**Kalphite** (`Turael_Slayer assignments.txt:126`, `Duradel_Slayer assignments.txt:195`).
Irrelevant while F2 holds (we never ask Aya to change anything), recorded so nobody "optimises"
by Aya-skipping later.

**F4. Kuradal points** (`Kuradal.txt:36-57`): 15 per task, **75 on every 10th, 225 on every
50th, 375 on every 100th**, 525 on 250th, 750 on 1000th. The milestone value REPLACES the base
(`Slayer reward point.txt:23-27`: "5x points every 10th task"). Requirements
(`Kuradal.txt:20`): **combat 100 and Slayer 50**, plus Shilo Village for the village itself
(not needed via NPC Contact). Since 1 Oct 2025 the combat requirement is enforced even with a
Slayer cape (`Slayer Master.txt:353`).

**F5. Cancelling through the Slayer Rewards interface costs 30 points and keeps the streak.**
`Slayer Rewards.txt:452-454`: *"Cancel task | 30 | Cancels the player's current assignment
without penalties."* Blocking at Kuradal costs 100 and is permanent (`Kuradal.txt:24`); at Aya
40, list shared with Turael/Spria (`Aya.txt:20`). This is how an undoable Kuradal task is
handled (§7.4) -- never via Aya.

**F6. NPC Contact** (`NPC Contact.txt`): Lunar spellbook, Magic 67, **1 Astral + 1 Cosmic +
2 Air**, requires Lunar Diplomacy. Casting opens *"an interface ... showing various NPCs the
player may contact"* (line 19); uncontactable NPCs are greyed (line 23). **Aya and Kuradal are
both in the list and both give tasks** (lines 73-76, 103-106). A right-click on the spell icon
re-contacts the last NPC (line 21) -- its label may still read "Turael"/"Duradel" but contacts
the replacement correctly (line 323). No cooldown is documented. `Slayer Master.txt:342` calls
it "the most efficient way of receiving tasks".

**F7. The wiki itself warns against 10th-task boosting with a high master** (`Aya.txt:22`):
*"You can do this every 10 tasks instead but this is not recommended due to the amount of
unfavourable tasks that can be given."* Kuradal's list is 43 tasks, mostly 130-200 kills of
things like abyssal demons, gargoyles, smoke devils and dark beasts (`Duradel_Slayer
assignments.txt`). **The user has chosen every 10th with Kuradal anyway; the design makes that
safe (a doable-task catalog plus the 30-point cancel) and keeps the milestone master and
interval as config so 50th-with-Konar is a settings change, not a rewrite.** Points economy at
the default: +75 per ten tasks, +225/+375 at the big milestones, minus 30 per cancel; a
milestone that needs three cancels is still net positive.

**F8. Task state is varps and varbits, decoded through DB tables** -- RuneLite's
`SlayerPlugin.updateTask()` (`SlayerPlugin.java:362-453`):

| What | Id | Source |
|---|---|---|
| kills remaining | **varp 394** `SLAYER_COUNT` | `VarPlayerID.java:189` |
| task id | **varp 395** `SLAYER_TARGET` | `:190` |
| original amount | varp 4258 `SLAYER_COUNT_ORIGINAL` | `:1860` |
| Konar area id | varp 2096 `SLAYER_AREA` | `:909` |
| assigning master id | **varbit 4067** `SLAYER_MASTER` | `VarbitID.java:3032` |
| points | **varbit 4068** `SLAYER_POINTS` | `:3033` |
| streak | **varbit 4069** `SLAYER_TASKS_COMPLETED` | `:3034` |
| boss id | varbit 4723 `SLAYER_TARGET_BOSSID` | `:3406` |
| Burthorpe master present | varbit 10782 `BURTHORPE_SLAYER_MASTER` | `Varbits.java:990` |

Task id → name is `client.getDBRowsByValue(DBTableID.SlayerTask.ID=113, COL_ID=0, 0, taskId)`
then `COL_NAME_UPPERCASE=10` (`SlayerPlugin.java:386-394`). **Completion is detected purely by
varp 394 going from >0 to 0** (`:448-452`); the plugin no longer parses chat for it. Only two
master ids are hard-coded upstream (Krystilia 7, Mortimer 10, `:110-111`); **Aya's and
Kuradal's values of varbit 4067 must be read live** (§12).

**F9. Interface ids** (`InterfaceID.java`): the NPC Contact spell icon is **218.113**
(`MagicSpellbook.NPC_CONTACT = 0x00da_0071`, already used by `mahogany_homes.py:32-33`); the
contact picker is **group 75** `LUNAR_CONTACT_NPC` (`MAIN=1, CONTENTS=3, SCROLLLAYER=4`); the
rewards interface is **426** `SLAYER_REWARDS` with **924** `SLAYER_REWARDS_TASK_LIST`. The
picker's rows come from DB table 130 `NpcContact` (`SLAYER_MASTER_BURTHORPE = 7152`,
`SLAYER_MASTER_SHILO_VILLAGE = 7157`) whose `COL_ALT_NAME` column is how Aya/Kuradal replace
Turael/Duradel on the same row -- so **match the row by the DISPLAYED name, read from the widget
text, never by a hard-coded child index.**

**F10. The enchanted gem / slayer ring "Check" reports the streak even with no task**
(`Enchanted gem.txt:49`, 5 Jan 2022) -- a fallback for the streak if varbit 4069 ever reads
back unreliably. The ring's worn **Master** option *cannot* assign tasks or spend points
(`Slayer ring.txt:59`), so it is not a substitute for NPC Contact.

## 3. What exists today (from the 2026-09-18 surveys)

- **`routines/slayer.py` (1214 lines) is a combat loop with no task awareness.** Targets come
  from the panel's monster list (`targetNames`/`targets`); it banks (nearest bank within 40
  tiles, else ring of dueling → Ferox), keeps prayer/food/potions/bracelets up, loots, buries,
  safespots, engages. Its tick order is pinned by `tests/test_slayer.py:69`. Two latent bugs
  found in passing: `BANK_NO_PROGRESS_MAX` is used at `:101` but **defined nowhere** (a
  `NameError` after two no-restock bank trips), and `bracelets.reequip` is read but never
  exported by `ConfigExporter`. Both get fixed in the move.
- **Nothing reads task state.** No `/varp`, no slayer reader in m8aq, no `SlayerProvider`.
  `/varbit?id=` exists (`PlayerProvider.java:51`). `VARP_SUBSCRIPTIONS` exists on the push side.
- **Task → location is 90% built and 0% used.** `SlayerLocations.java` holds 131 categories
  with wiki-sourced tiles; `ConfigExporter.resolveTaskLocations` ships them as `taskLocations`;
  `travel.task_location(config, category)` reads them and **has zero callers**. Many of those
  tiles are Wilderness or underground -- they are a starting point for the catalog (§6), not
  the catalog.
- **Travel cannot change floors.** `travel.walk_to`/`route_to`/`worldmap.find_path` are
  same-plane (`travel.py:432-435`); no ladder/stairs/cave helper exists. `teleports.py` +
  `banker._run_item_leg`/`_run_object_leg`/`_await_arrival` execute item/box/POH legs (object
  legs NOT verified live). `travel.hop_fairy_ring` is generic and live-proven. Slayer ring,
  games necklace, Karamja gloves 4 → Kuradal, skills necklace, digsite pendant are all in
  `vendor/teleportation_items.tsv`.
- **No gear swapping.** `banker.py` has no equip-from-bank. Prior art: `maintain_bracelet`'s
  hover-verified `expected_action="Wear"` click, `falconry._ensure_hands_free` (remove a worn
  slot via widget 387), `karambwan_fishing.worn_teleport`.
- **Dialogue handling is four private copies.** The best is `golem_crafting._choose_dialogue_
  option` (paged, "never re-press a page-forward that did not change the options"); the
  correct open-test is `giants_foundry._dialogue_open` (`/api/ui/dialogue` has no `open` field,
  so "open" = any of options/npcText/playerText/itemText). NPC Contact is already cast by
  `mahogany_homes.request_contract_phase` (`:806-841`): magic tab key, `get_widget(218,113)`,
  click with `offset=(0,0)` and no simulated mistake (spell icons are packed; a 20-45px
  humanised miss casts the neighbour), with a cast ceiling AND a cooldown.
- **`check_stray_dialog`** (`controller.py:3604`) presses Space once per tick on any open
  dialogue. A routine that walks dialogues must set `SKIP_STRAY_DIALOG_CHECK = True` and own it.

## 4. Architecture

```
routines/combat.py        thin: get_config -> /slayer-config; tick -> combat_core.fight_tick
routines/slayer.py        orchestrator (phases below); FIGHT phase -> combat_core.fight_tick
                          with the task's targets substituted into the config
combat_core.py            NEW shared module = today's slayer.py minus get_config
slayer_state.py           NEW: reads GET /slayer, decides next master, doable/skip logic (pure)
slayer_tasks.py           NEW: the catalog (pure data) + validation helpers
npc_contact.py            NEW: cast NPC Contact, pick a master, walk the dialogue, open rewards
dialogue.py               NEW shared: _dialogue_open / options / choose (promoted from golem)
legs.py                   NEW shared: run one transport leg (item / fairy / object / walk),
                          promoted from banker; banker imports it
gear.py                   NEW shared: equip_from_inventory, ensure_worn, worn_names
Java: SlayerProvider      NEW: GET /slayer (+ GET /varp?id=)
Java: ControllerConfig    NEW "Slayer tasks" section; RoutineChoice COMBAT + SLAYER relabel
```

Rules carried over from CLAUDE.md that bind every part: the physical Arduino is the only input
path; UDP events accelerate, HTTP confirms; clicks pass `refresh_target`; every wait is an
`await_outcome`/`await_long` with a signal and a confirm, never a fixed sleep; phases are
derived from live state each tick because `ctx.state.data` resets on restart.

## 5. The split: `combat.py` and `combat_core.py`

- `git mv routines/slayer.py combat_core.py` (keeps history), then:
  - delete `get_config` from `combat_core`; `tick` becomes `fight_tick(ctx)`, everything else
    unchanged and un-renamed so the 19 tests in `tests/test_slayer.py` move to
    `tests/test_combat.py` with only the import changed. The pinned tick ladder
    (`defensive_prayer, offensive_prayer, prayer_potions, supplies, bracelet, loot, safespot,
    engage`) is preserved.
  - define `BANK_NO_PROGRESS_MAX = 3` (the comment at `:77-99` already describes it) and add a
    test that the safety stop fires on the third no-progress trip.
  - export `bracelets.reequip` from `ConfigExporter` with a panel toggle
    (`slayerBraceletReequip`, default true), since the code already honours it.
- `routines/combat.py` (≈20 lines): `get_config = controller.get_slayer_config`,
  `on_event = combat_core.on_event`, `tick = combat_core.fight_tick`.
- `RoutineChoice`: add `COMBAT("Combat")`; relabel `SLAYER("Slayer (tasks)")`.
  `ROUTINE_FILES`: `"COMBAT": "routines/combat.py"`, `"SLAYER": "routines/slayer.py"`.
  **Anyone with "Slayer / Combat" selected today gets the new task routine after the client
  rebuild; that is the user's stated intent, and it is called out here so it is not a
  surprise.** The panel's monster list / task-location dropdown stay as the Combat routine's
  config; the Slayer routine ignores them.
- `combat_core` gains one new entry point the orchestrator needs:
  `combat_config(base_config, target_names)` → a copy of the panel config with
  `targetNames` replaced and `targets` emptied (ids are only ever resolved for NPCs in the
  scene, `ConfigExporter.java:403-439`, so names are the durable signal). `safespot` is
  forced off for task fights (a marked safespot belongs to one place).

## 6. Task catalog (`slayer_tasks.py`)

Pure data, unit-tested, no HTTP. One entry per **task name as the game reports it**
(`COL_NAME_UPPERCASE` lower-cased; the Python side normalises to lowercase and the test suite
pins the mapping against the 24 Aya + 43 Kuradal names from the wiki tables).

```python
TaskSpec(
    name="banshees",                       # key = game task name, lowercase
    masters=("aya", "kuradal"),            # who can assign it (from the two tables)
    targets=("Banshee",),                  # substrings for combat_core.resolve_targets
    site=Site(x=3429, y=3564, plane=0, region=None, radius=12),  # where to stand; radius = "at the site" test
    legs=(Leg.item("slayer ring", "Slayer Tower"),        # executed in order after banking
          Leg.walk(3429, 3564, 0)),
    gear=(Gear.worn("earmuffs", alternatives=("slayer helmet",)),),  # required, checked before leaving the bank
    carry=(),                              # required inventory items (e.g. "ice cooler" x N)
    hazards=(),                            # informational: "poison", "light source"
    doable=True,                           # False = cancel on assignment (Kuradal) / never expected (Aya blocked)
    verified=False,                        # flipped after a live run; tests do not depend on it
)
```

Leg kinds (`legs.py`, §8): `item(name, option)` inventory/worn teleport, `fairy(code, expect)`,
`object(name, action, at=(x,y,plane), lands=(x,y,plane, radius))` for ladders/caves/stairs/
trapdoors, `walk(x,y,plane)`. **Every entry's first leg must be a teleport or fairy ring**
because the home bank is Ferox and Ferox is a sealed island on the collision map (CLAUDE.md,
"Teleporting home from a bank trip"). A catalog test enforces it, and a second test checks
that each `walk` leg's target is `worldmap.find_path`-reachable from the previous leg's
landing tile on the same plane (the "dungeon void" leak is why `walk` legs never span planes).

### 6.1 Aya's 24 tasks -- first-wave choices

Kill spots chosen for: no Wilderness, no plane change where a surface option exists, reachable
from a vendored teleport, no cannon. Coordinates marked ~ are from `SlayerLocations.java` or
the wiki "Slayer Point Farming" column and are **unverified**; exact standing tiles are filled
at implementation from the wiki's `{{LocLine}}` spawns and confirmed on the first visit.

| Task | Where | First leg | Gear / carry | Wave |
|---|---|---|---|---|
| Banshees | Slayer Tower ground floor ~3429,3564 | slayer ring → Slayer Tower | **earmuffs** (or slayer helm) | 1 |
| Crawling hands | Slayer Tower ground floor ~3412,3549 | slayer ring → Slayer Tower | -- | 1 |
| Bats | Giant bats outside Slayer Tower ~3418,3522 | slayer ring → Slayer Tower | -- | 1 |
| Cave crawlers | Fremennik Slayer Dungeon ~2784,9998 (same plane as landing) | slayer ring → Fremennik | antipoison optional | 1 |
| Cows | Lumbridge cow field ~3254,3255 | Lumbridge teleport tablet | -- | 1 |
| Birds | Chickens, Fred's farm Lumbridge ~3171,3293 | Lumbridge teleport tablet | -- | 1 |
| Goblins | East of Lumbridge ~3250,3230 | Lumbridge teleport tablet | -- | 1 |
| Spiders | Giant spiders by H.A.M. hideout ~3160,3245 | Lumbridge teleport tablet | -- | 1 |
| Rats | Giant rats, Lumbridge swamp ~3222,3215 | Lumbridge teleport tablet | -- | 1 |
| Icefiends | Ice Mountain ~2995,3468 | Falador teleport tablet + walk | -- | 1 |
| Scorpions | Al Kharid mine ~3300,3300 | ring of dueling → Emir's Arena + walk | -- | 1 |
| Wolves | White Wolf Mountain surface ~2846,3492 | games necklace → Burthorpe + walk (or Catherby) | -- | 1 |
| Kalphite | Kalphite Cave (workers) ~3220,9525 | POH fairy ring → BIQ if `poh.fairyRing` is set; else Shantay pass tablet + walk + `object` cave entrance | -- | 2 |
| Bears | Grizzly bears SW of Legends' Guild | Ardougne teleport tablet + walk (or POH fairy ring → BLR) | -- | 2 |
| Dogs | Jackals, desert near Nardah | Nardah teleport (desert amulet / tablet) + walk, or POH fairy ring → DLQ | -- | 2 |
| Lizards | Small lizards north of Nardah | as Dogs | **ice coolers ×N** (carry) | 2 |

A fairy-ring leg is never a FIRST leg: from Ferox the only way to any ring is a teleport
first (the POH ring when configured, otherwise slayer ring → Fremennik dungeon → `object`
exit → walk to AJR, which is three legs for one hop and is why the rows above prefer tablets).
| Ghosts | Varrock Sewers or Catacombs | Varrock tablet + `object` leg (manhole/ladder), or Xeric's talisman | -- | 2 |
| Skeletons | Edgeville Dungeon | glory → Edgeville + `object` trapdoor | -- | 2 |
| Zombies | Varrock Sewers / Edgeville Dungeon | as above | -- | 2 |
| Dwarves | Dwarven Mine ~2984,9807 | Falador tablet + `object` ladder | -- | 2 |
| Minotaurs | Stronghold of Security floor 1 | Edgeville + `object` entrance | -- | 2 |
| Cave bugs | Lumbridge Swamp Caves ~3149,9578 | Lumbridge tablet + `object` (rope/hole) | **light source** | 2 |
| Cave slimes | Lumbridge Swamp Caves ~3150,9555 | as Cave bugs | light source, antipoison | 2 |
| Monkeys | Karamja, NW of glider / Ardougne Zoo | brimhaven/ardougne teleport | -- | 2 |

Wave 1 (12 tasks, weight 92/172 ≈ 53% of Aya's rolls) needs only item legs and `walk`.
Wave 2 needs `object` legs (§8) and per-task carry items. **Until wave 2 ships, a wave-2 Aya
task is handled exactly like an undoable Kuradal task: cancelled for 30 points (§7.4).** The
user may prefer to BLOCK the worst of them at Aya (40 points each, 6-7 slots, shared list);
that is a manual decision, and the routine reads the outcome (a blocked task is simply never
assigned) without needing to know about it.

### 6.2 Kuradal's list

Every row of `Duradel_Slayer assignments.txt` gets an entry so the game name always resolves;
`doable` defaults to **False** and is flipped per task as a site + legs + gear are authored.
The first Kuradal wave is the subset that reuses Aya sites or slayer-ring landings and needs no
special gear: **Kalphite** (Kalphite Cave), **Bloodveld / Aberrant spectres (nose peg) /
Nechryael / Gargoyles (rock hammer) / Abyssal demons** in the Slayer Tower if levels allow
(upper floors need `object` stair legs), **Ankou** (Stronghold Slayer Cave, slayer ring
landing is outside it: `object` cave leg), **Hellhounds / Fire giants / Bloodveld** in the
Stronghold Slayer Cave, **Cave crawlers → Basilisks/Turoth/Kurask** in the Fremennik dungeon
(mirror shield / leaf-bladed). Which of these the account can actually do (Slayer level, quest,
gear in bank) is not knowable offline -- §7.3's doability check reads `/skills` and bank memory
live, so an authored-but-unavailable entry degrades to a cancel, never a stuck routine.

Superiors ("A superior foe has appeared") are not targeted specially -- the 2026-08-05
superior spec was never implemented, and this design does not add it.

## 7. The orchestrator (`routines/slayer.py`)

`SKIP_STRAY_DIALOG_CHECK = True` (the routine owns every dialogue; during FIGHT it calls
`controller.check_stray_dialog(ctx)` itself so level-up boxes still clear).
`VARP_SUBSCRIPTIONS = (394, 395)`, `VARBIT_SUBSCRIPTIONS = (4067, 4068, 4069, 12393)` (12393 is
the busy varbit that flags a teleport/fairy ring animating, from `karambwan_fishing.py:125`).

### 7.1 Phase derivation (every tick, from live state)

```
s = slayer_state.read()          # GET /slayer, one HTTP read
if s is None: log + return       # bridge/endpoint unavailable: do nothing, never guess

if s.count == 0:                                  -> ACQUIRE
elif not spec(s.task).doable or user_skip(s.task):-> SKIP
elif at_site(spec, player):                       -> FIGHT
elif not loadout_ok(spec):                        -> BANK      (home bank, then travel)
else:                                             -> TRAVEL
```

`ctx.state.data` holds only accelerators (which leg index we are on, cast counters, last
master contacted, the anchor tile for mid-task bank trips) and is reconciled against the
derivation above -- so a restart mid-task with the loadout already in the inventory resumes at
TRAVEL or FIGHT without re-banking, and a restart with the task already done goes straight to
ACQUIRE.

### 7.2 ACQUIRE (no task)

1. `master = next_master(s.streak, cfg)`: **Kuradal if `(streak + 1) % milestoneEvery == 0`,
   else Aya.** Pure function, pinned by tests for streaks 0, 8, 9, 19, 49, 99. (`streak` is
   varbit 4069; the *next* completed task will be number `streak + 1`.)
2. Preconditions, checked in this order, each a safety stop with a specific message on
   failure: Lunar spellbook (`/api/player/magic.spellbook == "LUNAR"`); runes present (astral,
   cosmic, and air runes or an air staff -- names via `/inventory` + `/equipment`); Magic ≥ 67
   (`/skills`); if Kuradal: combat ≥ 100 and Slayer ≥ 50.
3. `npc_contact.get_task(ctx, master)` (§9). Outcome confirmed by **varp 394 > 0 pushed, then
   `/slayer` re-read** (`await_outcome` on `VARBIT_CHANGED` with `varpId == 394`, confirm =
   `/slayer.count > 0`). Budget 8 s per attempt, `CONTACT_MAX_CASTS = 3` per acquisition, then
   safety stop -- runes are spent per cast, so a ceiling not a cooldown (mahogany's rule).
4. Log `[slayer] task: {name} x{count} from {master} (streak {streak}, points {points})`.
   Record `assigned_master_varbit` = varbit 4067 for §12.

**Invariant guard (F2):** `get_task` raises before casting if `master == "aya"` and
`/slayer.count > 0`. Tested.

### 7.3 Doability (`slayer_state.doable(spec, skills, bank_memory, cfg)`)

`spec.doable` AND task not in `cfg.skipTasks` AND Slayer level ≥ spec's requirement (from the
wiki tables, stored on the entry) AND every `gear`/`carry` item is present in bank memory,
inventory or equipment (`bank_memory.load(names=...)` -- one call; `seen == False` means
"unknown", and unknown is treated as **doable** so a fresh client never cancels a task it
could have done; the bank visit will then answer for real, and a missing item at the bank
routes to SKIP with the reason logged).

### 7.4 SKIP (task active, not doable)

Cost 30 points, keeps the streak (F5). Guards: `points ≥ 30 + cfg.pointsReserve` (default
reserve 60) else safety stop "not enough slayer points to skip {task}"; `skips_this_slot <
cfg.maxSkipsPerTask` (default 3) else safety stop -- three undoable rolls in a row means the
catalog is too thin for this master, and a human should widen it or block. Mechanism:
`npc_contact.open_rewards(ctx, master)` → interface 426 → Tasks tab → Cancel → confirm dialog;
confirmed by varp 394 → 0 and `/slayer.count == 0`. **Whether the rewards interface opens
through NPC Contact is a §12 item;** the fallback is in-person: games necklace → Burthorpe,
walk to Aya (2931,3536), `click_menu_action(npc, "Rewards")`. Any master's interface cancels
the current task. After SKIP the next tick derives ACQUIRE and asks the SAME master again
(the milestone slot is unchanged because the streak did not move).

### 7.5 BANK (task active, loadout missing)

Home bank is **Ferox Enclave via ring of dueling** (the one teleport-bank `banker` already
supports, with the pool for HP/prayer/run). Sequence, each step confirmed:

1. If not at Ferox: `legs.run(Leg.item("ring of dueling", "Ferox Enclave"))` (replaces the
   blind 3.0-3.6 s sleep in `travel.teleport_to_ferox` with the karambwan-style landing wait).
2. `banker.bank_up` with a task-derived config: `keepItems` = protected names + gear + carry
   + the first leg's item; `withdraw` = panel food/prayer quantities + `spec.carry` + gear not
   already worn + the first leg's teleport item (+ a spare ring of dueling if the worn one is
   on its last charge -- charge read from the name suffix, as `_use_return_teleport` does) +
   an expeditious bracelet if the panel's bracelet setting is on. `returnToSpot=False`.
3. `gear.ensure_worn(ctx, spec.gear)`: for each required worn item not in `/equipment`,
   click it in the inventory with `expected_action in ("Wear","Wield","Equip")` (the
   `maintain_bracelet` pattern), confirm by `/equipment` name. A slayer helmet satisfies
   earmuffs/nose peg/facemask (`alternatives`).
4. `travel.use_fountain` if HP/prayer/run are below the panel thresholds.
5. `loadout_ok` re-derived; if still false → safety stop naming the missing item (this is the
   "bank has no earmuffs" case, and it must not loop).

Mid-task banking (inventory full / out of food) is `combat_core`'s existing path, unchanged:
nearest bank or Ferox, `returnToSpot=True`, the return-teleport planner. The orchestrator
only notices it happened because `at_site` becomes true again.

### 7.6 TRAVEL

`legs.run_next(ctx, spec.legs, cursor)` -- one bounded leg per call, `ctx.state.data["leg"]`
is the cursor, each leg confirmed by its own landing test (§8). After the last leg,
`travel.walk_to(site, goal_adjacent=False)`. `at_site` = same plane and within `site.radius`
of the site tile (or `site.region` matches, for dungeons where a tile radius is the wrong
shape). A leg that fails three times → safety stop naming the leg; being stranded with a task
active is exactly the case the return-to-spot rebuild taught us to stop loudly on.

### 7.7 FIGHT

`combat_core.fight_tick(ctx)` with `ctx.state.config` temporarily replaced by
`combat_config(panel_config, spec.targets)`. The orchestrator adds, per tick before delegating:
`controller.check_stray_dialog(ctx)`; a **no-target watch** -- `combat_core.engage_target`
returning without a target for `NO_TARGET_TICKS` (30) consecutive ticks logs and walks back to
the site tile (targets wander; the site tile is where the spawns are), and after
`NO_TARGET_WALKS` (3) safety-stops (this closes the "stranded with nothing to fight is
indistinguishable from idle" gap recorded in `docs/routine-notes.md:2777`). Task completion is
varp 394 → 0 (pushed) confirmed by `/slayer`; the next tick derives ACQUIRE. The kill that
completes the task also bumps varbit 4069; log the new streak and points delta so the milestone
bonus is visible in the log.

### 7.8 Breaks, death, wilderness

`before_break(ctx)`: nothing to tear down, but if a dialogue or the contact picker is open,
close it (ESC), so a break never starts with an interface up that swallows the resume's
hotkeys. Death: `safety.stopOnDeath` is honoured by the engine already; the catalog contains
no Wilderness sites, and a test asserts none of the authored sites has y ≥ 3520 on plane 0
(the overworld Wilderness line) unless the entry is explicitly tagged `wilderness=True`.

## 8. Transport legs (`legs.py`)

Promoted from `banker._run_leg/_run_item_leg/_run_object_leg/_await_arrival` so bank returns
and task travel share one runner; `banker` keeps its behaviour by importing it. Additions:

- **`Leg.item(name, option)`** -- inventory OR worn (worn via widget 387, the karambwan
  `worn_teleport` path; inventory via the falconry `_use_teleport` path: left click verified
  by `/hover` against `option`, fallback to the right-click menu). Landing confirmed the
  karambwan way: varbit 12393 → 0 / PLAYER_STOPPED, re-checked in one-tick chunks because the
  bridge answers nothing during LOADING, confirmed by `/player` within `lands` radius or
  region.
- **`Leg.fairy(code, expect)`** -- delegates to `travel.hop_fairy_ring`.
- **`Leg.object(name, action, at, lands)`** -- NEW, and the only genuinely new mechanism in
  the whole design: `walk_to(at, goal_adjacent=True)` → `get_object_at`/nearest object by name
  → `click_entity(..., expected_action=action, refresh_target=object_refresh_target)` →
  confirm by `/player` plane/region/position matching `lands` within the budget. This is the
  ladder/stairs/cave/trapdoor primitive walk_to cannot provide. Slayer Tower upper floors,
  Stronghold Slayer Cave, Varrock Sewers, Edgeville Dungeon and the swamp caves all reduce to
  one or two of these.
- **`Leg.walk(x, y, plane)`** -- `travel.walk_to`, same plane as the previous landing
  (enforced by the catalog test).

`run_next` returns `DONE | PROGRESS | FAILED`; the orchestrator advances the cursor on DONE,
retries on PROGRESS, counts FAILED. Each leg carries its own `budget_s` (item 8, fairy 12,
object 10 + walk budget, walk = `outcome_waits.walk_budget`).

## 9. NPC Contact (`npc_contact.py`)

Built from `mahogany_homes.request_contract_phase` plus the dialogue helpers, generalised to
"contact NPC X and pick dialogue option containing Y":

1. `open_magic_tab` (the tab key is one constant in `settings.py`; today `mahogany_homes` uses
   `F6` and `open_spellbook_if_needed` uses `T` -- the recording settles which, and both call
   sites move to the constant). Verify with `get_active_tab`.
2. Fast path: if `ctx.state.data["last_contacted"] == master`, `click_menu_action(spell
   widget 218.113, option=<master display name>, offset=(0,0))` -- the re-contact right-click
   (F6). Slow path: `click_entity(widget, offset=(0,0), simulate_mistakes=False,
   check_ui_blockers=False)` → wait for `get_widget(75, 3).clickable` → find the row whose
   text is the master's displayed name in `get_widget_children(75, 4)` / `/widgets/dump` →
   click it. Greyed row (uncontactable) → safety stop naming the requirement.
3. Confirm contact: `/api/ui/dialogue.npcName` in the master's accepted names
   (`{"Aya","Turael"}`, `{"Kuradal","Duradel"}` -- the alt-name mechanism, F9).
4. Walk the dialogue with `dialogue.choose(ctx, needle)`: Space through NPC text; at an
   options menu pick the option containing the recorded "assignment" phrase. **Hard rule: an
   options menu with no matching option is ESC + failure, never "press 1" and never Space** --
   Aya's replace-task confirmation is the one dialogue in the game this routine must never
   accept, and the F2 guard already keeps her from being asked while a task is active, so an
   unknown menu is a bug to stop on, not a page to guess through.
5. `open_rewards(ctx, master)`: same contact, option containing "reward"; confirmed by
   `get_widget(426, ...)` clickable. Cancel: Tasks tab → Cancel → Yes; confirmed by varp 394.
   Widget children ids come from the recording (§12).

Every option string, child index and the exact NPC texts are **recorded, not reasoned** -- the
dump has no transcript pages, so `recorder.py` is the source (`recorder.py contact-aya`,
`recorder.py contact-kuradal`, `recorder.py rewards-cancel`).

## 10. Bridge: `SlayerProvider` (Java)

`GET /slayer` -- reads on the client thread, no `TickCache` (varp reads are cheap, like
`/player`):

```json
{ "count": 27, "originalCount": 30, "taskId": 4135, "taskName": "Banshees",
  "areaId": 0, "areaName": null, "masterId": 2, "streak": 19, "points": 415,
  "bossId": 0, "hasTask": true }
```

Decoding copies `SlayerPlugin.updateTask()` (F8) including the boss sub-table branch, and
`taskName` is `null` (not `""`) when the DB lookup fails so Python can tell "no task" from
"could not decode". `masterId` is raw varbit 4067. Unit-testable part: a `SlayerTaskState`
value object with `fromRaw(count, taskId, name, ...)`; the provider is the adapter, as
`RecordRing`/`BankSnapshot` were split out for the same reason.

`GET /varp?id=N` → `{"value": n}` alongside the existing `/varbit` in `PlayerProvider`, and
`controller.get_varp(id)` -- so the next varp-shaped fact needs no Java change.

`ControllerConfig` "Slayer tasks" section (exported on `/slayer-config` under `"tasks"`):
`slayerFillerMaster` (enum, AYA), `slayerMilestoneMaster` (enum: KURADAL; KONAR/NIEVE
selectable later once the catalog has their sites -- the enum exists so the master is data,
the routine only ever knows "a master with a display name and NPC-Contact row"),
`slayerMilestoneEvery` (10, range 10-50 step 10), `slayerSkipTasks` (csv), `slayerMaxSkips`
(3), `slayerPointsReserve` (60), `slayerBraceletReequip` (true, §5). Food/prayer quantities and
the bracelet choice come from the existing Slayer section.

Both routines need a client rebuild: `RoutineChoice`, the config section and the provider are
Java.

## 11. Tests

- `test_combat.py` = today's `test_slayer.py` re-pointed, plus `BANK_NO_PROGRESS_MAX` fires.
- `test_slayer_state.py`: `next_master` table; phase derivation for each `(count, doable,
  at_site, loadout_ok)` combination; the F2 guard raises; `doable` treats unseen bank memory
  as doable and a missing worn item as not; skip guards (points, ceiling).
- `test_slayer_tasks.py`: every Aya/Kuradal wiki name has an entry; first leg is a teleport;
  `walk` legs are same-plane and map-reachable from the previous landing; no untagged
  Wilderness site; each `targets` substring matches at least one NPC name in
  `SlayerMonsters.TASKS` (a Python mirror of that map is generated from the Java file by a
  test helper so the two cannot drift silently).
- `test_legs.py`: `run_next` cursor semantics with a fake bridge; the object leg confirms by
  plane change and fails cleanly on timeout; banker's return path still passes its existing
  tests through the promoted runner.
- `test_npc_contact.py`: picker row chosen by displayed text not index; greyed row → stop;
  unknown option menu → ESC + failure, never a keypress; cast ceiling.
- `test_dialogue.py`: the golem paging tests moved, plus `dialogue_open` on a
  continue-only stage.
- Java: `SlayerTaskStateTest` (shape + null-name rule), `M8aqProviderTest.knownPathsAreStable`
  extended with `/slayer` and `/varp`.

## 12. First live session -- what must be measured before anything is trusted

In order; each is a `recorder.py` label or a bridge probe.

1. **`recorder.py contact-aya` / `contact-kuradal`**: the magic-tab key; interface 75's row
   layout (child ids under 75.4, row text = "Aya"/"Kuradal" or the alt name); the exact
   "assignment" option string; the NPC texts on the way; the assignment chat line; and
   **varbit 4067's value after a task from each master**.
2. **`recorder.py rewards-cancel`**: whether "rewards" is offered through NPC Contact; interface
   426's Tasks tab / Cancel / confirm child ids; points delta (expect exactly -30) and streak
   unchanged. If NPC Contact cannot open rewards, record the in-person Aya path instead.
3. **`GET /slayer` against a live task**: `taskName` matches the catalog key; `count`
   decrements per kill (and by 2 with an expeditious bracelet, as `combat_core:802-804`
   observed); `count` → 0 on the last kill; `streak` +1 on completion.
4. **One Aya task end to end** (banshees or crawling hands: one slayer-ring leg, one walk):
   loadout withdraw + `ensure_worn` of earmuffs; ring dialogue page 1; landing wait; site
   arrival; fight; completion → ACQUIRE.
5. **One milestone**: at streak 9 (or after adjusting `milestoneEvery`), Kuradal is contacted,
   the doability check runs, and either a doable task is fought or SKIP cancels for 30 and
   re-asks.
6. **First object leg** (Stronghold Slayer Cave entrance or the Slayer Tower stairs):
   confirm-by-plane works and the budget is right.

Expected outcome, per the Gauntlet lesson: widget ids and site tiles are wrong before any
logic is.

## 13. Build order (three sub-projects, one plan each)

1. **Split + plumbing**: `combat_core`/`combat.py`/`RoutineChoice`; `SlayerProvider` +
   `/varp`; `dialogue.py`, `legs.py` (promotion only, banker green), `gear.py`. Zero new
   behaviour for the user; `Combat` works exactly as before. Merge before 2.
2. **Acquire + skip**: recording session (§12.1-2), `npc_contact.py`, `slayer_state.py`, the
   orchestrator with ACQUIRE/SKIP/FIGHT only and a catalog whose sites are "wherever you are
   standing" -- i.e. the user parks at a spot and the routine gets and completes tasks there.
   This is already useful and proves F1/F2/F4 live.
3. **Catalog + travel + loadout**: wave-1 sites, BANK and TRAVEL phases, object legs, wave 2.

## 14. Decisions taken without the user (change any of these and the spec adapts)

- **D1** Home bank = Ferox via ring of dueling. (Alternative: a POH with a jewellery box, if
  the `poh` config is set -- the return planner already prefers it; would save the dueling
  ring charge.)
- **D2** Undoable task = cancel for 30 points, not block. Blocking is the user's manual call.
- **D3** No cannon. The wiki calls it "strongly recommended" for boosting; it is a separate
  sub-project (placement, reloading, pickup before breaks) and out of scope here.
- **D4** Wave-2 Aya tasks are cancelled until their legs are authored, rather than blocking
  the whole routine on ladders. Costs ≤30 points per roll of a wave-2 task.
- **D5** Milestone master is Kuradal per the request, with Konar as the wiki-recommended
  alternative left as a later enum value (her location-locked tasks need a different catalog
  shape).
- **D6** The panel's old "Slayer / Combat" selection becomes the new task routine after the
  rebuild; users wanting the old behaviour pick "Combat".
- **D7** Tasks are acquired at the kill site of the previous task (NPC Contact works
  anywhere), BEFORE the trip to the bank, so one bank visit fits the next task's loadout.

## 15. Open questions for the user

1. Which teleports does the account own for wave 1 -- slayer ring (charges?), games necklace,
   Lumbridge/Falador/Varrock tablets, glory, Xeric's talisman? (Bank memory will answer live;
   knowing now changes which sites go in wave 1.)
2. Is the account already on the Lunar spellbook with Lunar Diplomacy done and Magic ≥ 67, and
   does it meet Kuradal's combat 100 / Slayer 50? The spec assumes yes.
3. Should wave-2 Aya tasks be blocked at Aya now (40 points each, up to 6-7 slots) instead of
   cancelled per roll (D2/D4)? Candidates by nuisance: cave bugs, cave slimes, lizards,
   monkeys.
4. Which of Kuradal's 43 tasks do you actually want fought, and which should always be
   skipped? That list is `slayerSkipTasks` and the initial `doable` flags.
