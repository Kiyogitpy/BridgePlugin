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
    disposition=FIGHT,                     # FIGHT | SKIP (cancel, 30 pts) | STOP (safety-stop, user does it by hand)
    target_ids=(414,),                     # NPC ids (Aya tasks: from the Turael Skipping registry)
    targets=("Banshee",),                  # name substrings (Kuradal tasks), for combat_core.resolve_targets
    site=Site(x=3442, y=3542, plane=0,     # target tile; areas = the kill zone, "at the site" = inside any area
              areas=((3431, 3530, 22, 38, 0),), region=None),
    routes=(                               # ordered; the first whose first-leg item is OWNED is used
        (Leg.item("slayer ring", "Slayer Tower"), Leg.walk(3442, 3542, 0)),
    ),
    gear=(Gear.worn("earmuffs", alternatives=("slayer helmet",)),),  # required, checked before leaving the bank
    carry=(),                              # required inventory items (e.g. "ice cooler" x N)
    hazards=(),                            # informational: "poison", "light source"
    verified=False,                        # flipped after a live run; tests do not depend on it
)
```

Leg kinds (`legs.py`, §8): `item(name, option)` inventory/worn teleport, `fairy(code, expect)`,
`object(name, action, at=(x,y,plane), lands=(x,y,plane, radius))` for ladders/caves/stairs/
trapdoors, `walk(x,y,plane)`. **Every route's first leg must be a teleport**, because trips
start at a bank and the usual one (Ferox) is a sealed island on the collision map (CLAUDE.md,
"Teleporting home from a bank trip"); for most routes that first leg is the house teleport,
followed by the POH fairy ring or ornate jewellery box (§6.2). A catalog test enforces it, and a
second test checks
that each `walk` leg's target is `worldmap.find_path`-reachable from the previous leg's
landing tile on the same plane (the "dungeon void" leak is why `walk` legs never span planes).

### 6.1 Sites were decided by the user, task by task (2026-09-18)

The first draft of this section picked one site per task by rule. The user replaced that with an
interactive pass over every task; the result is **`docs/slayer-sites/decisions.md`**, which is
the catalog's input and outranks anything reasoned here. The research it was decided from
(wiki Location-comparison tables, spawn counts, safespots, access) is beside it in
`docs/slayer-sites/`.

**Aya's 24 tasks come from the Turael Skipping plugin, verbatim.** The user runs that Plugin
Hub plugin and asked for its spots. `docs/slayer-sites/aya-turael-skipping-registry.txt` is a
reflective dump of the plugin's own `SlayerTaskRegistry.TURAEL_TASKS` -- its data, not a
reconstruction (the 231.6 dialogue-widget lesson in CLAUDE.md). Per task it carries exactly what
a `TaskSpec` needs: the **NPC ids** that count, the named spot, the kill area as `WorldArea`
rectangles, a target tile, a gear note, and the plugin's teleports **in preference order**. The
catalog vendors that dump as data (`ControllerAddon/vendor/turael_skipping_tasks.json`, with a
`VENDORED.md` naming the jar) and a test pins that every Aya task name resolves to an entry.
Travel takes the first listed teleport bank memory says is owned. Three user overrides apply:
dogs are **blocked at Aya**, monkeys are killed **on Karamja** instead of Kruk's Dungeon, and
minotaurs are reached via the **ornate jewellery box to the Cooking Guild**. One site serves
three tasks -- Alice's Farm by Ectophial covers zombies, birds and cows.

**Targets are NPC IDS for Aya tasks, names for Kuradal's.** `combat_core.resolve_targets`
already accepts both (`targets` ids, `targetNames` substrings), so `combat_config` grows an
`ids=` argument rather than the catalog inventing name lists the plugin already settled.

### 6.2 Kuradal's 43 tasks: 22 FIGHT, 1 STOP, 20 SKIP

Every row of `Duradel_Slayer assignments.txt` gets an entry so the game name always resolves,
each with a **disposition**:

- **FIGHT** (22): aberrant spectres, ankou, bloodveld, fire giants, hellhounds (all
  **Stronghold Slayer Cave**); abyssal demons, gargoyles, nechryael (**Slayer Tower
  basement**); basilisks, kurask (**Fremennik Slayer Dungeon**); dagannoth, dust devils
  (**Catacombs of Kourend**); black demons (Taverley Dungeon); elves (Lletya); kalphite
  (Kalphite Cave, the Aya site); suqahs (Lunar Isle south-west); trolls (Death Plateau);
  waterfiends (Ancient Cavern); wyrms (Karuulm task-only batch); cave horrors (Mos Le'Harmless
  Cave); mutated zygomites (Zanaris, Cosmic Altar); gryphons (Great Conch eastern cave, fairy
  ring **CJQ**). Four hubs cover twelve of them -- build those routes first.
- **STOP** (1): greater demons. The user spends that task on tormented demons by hand, so the
  routine safety-stops naming the task instead of cancelling or fighting it.
- **SKIP** (20): everything else, including the boss task.

Per-site access, required items and the user's reasoning are in `decisions.md`. Whether the
account can actually do a FIGHT task (Slayer level, quest, gear in bank) is still not knowable
offline -- §7.3's doability check reads `/skills` and bank memory live, so an
authored-but-unavailable entry degrades to a cancel, never a stuck routine.

**The skip list is expensive enough to change a default.** By task weight FIGHT is 140, STOP 9,
SKIP 169 of 318; discounting point-unlock tasks the account probably lacks, a Kuradal roll is a
SKIP about 40% of the time. Three in a row then happens once per ~16 milestones, which the
shipped `slayerMaxSkips` default of 3 would turn into a safety stop. **Raise that default to 8**
in the sub-project 2 plan (the points reserve is the real guard), and recommend the user block
the heaviest skipped tasks at Kuradal -- metal dragons, dark beasts, araxytes, black dragons,
cave kraken, smoke devils -- which takes the SKIP share to about 17%.

**The POH is the travel hub, not Ferox.** The house has a fairy ring and an ornate jewellery
box, and most routes above are "house teleport, then ring or box". Banking still happens at a
bank (the box reaches Ferox and Castle Wars), so a task trip is: bank → house teleport → POH
leg → walk. `legs.run_object_leg` / `pick_destination` (banker's POH legs, moved in
sub-project 1) have **never run live**; the first fairy-ring or box leg is therefore a §12
checklist item, and the panel's Player-Owned House section must have the ring and the ORNATE
box set or `teleports.plan` will never route through the house.

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
elif spec(s.task).disposition == STOP:            -> safety stop naming the task (greater demons:
                                                     the user does tormented demons by hand)
elif spec(s.task).disposition == SKIP
     or not doable(spec) or user_skip(s.task):    -> SKIP
elif at_site(spec, player):                       -> FIGHT, or RESTOCK when
                                                     combat_core.needs_banking(ctx) (§7.5)
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

**Mid-task restock is the ORCHESTRATOR's job, not the fight loop's** (corrected 2026-09-19;
the first draft delegated it to `combat_core`'s existing bank trip, which was wrong for the
sites the user chose). `combat_core`'s trip returns with `banker.return_to_spot`: a teleport
plan plus `travel.walk_to`, which is **same-plane walking only**. Most chosen sites are
underground or behind an entrance object -- Stronghold Slayer Cave, Slayer Tower basement,
Catacombs of Kourend, Taverley Dungeon, Fremennik Slayer Dungeon, Kalphite Cave -- so that
return would fail three times and safety-stop "stranded" (`RETURN_WALK_MAX_ATTEMPTS`). The
catalog already holds the one route that does get back in. So:

- `combat_config` also sets `bank.enabled = False`, so `fight_tick` never banks on its own.
- Before delegating each FIGHT tick the orchestrator calls the fight loop's own public
  predicate `combat_core.needs_banking(ctx)` (out of food at low HP, out of prayer restores at
  low prayer, or an inventory full of loot). True → phase **RESTOCK**.
- **RESTOCK**: clear the current target; leave by the **escape teleport** -- every loadout
  carries one (a ring of dueling, which `banker._bank_via_ferox` already drives live; the Ferox
  pool then restores HP, prayer and run for free) -- deposit loot, withdraw back up to the
  task's supply profile (§7.9), re-check worn gear.
- Getting back needs no new code: the task is still active and the player is not at the site,
  so §7.1 derives **TRAVEL** and the task's own route runs again -- house, pool, ring or box,
  the entrance object, the walk. The kill count lives in varp 394, so nothing is lost by
  leaving.
- Guard: `RESTOCK_MAX_PER_TASK` (4). A task that needs a fifth trip is under-supplied or
  over-matched; safety-stop naming the task and what ran out, rather than commuting forever.
- The escape must fire while there is still margin. `needs_banking` triggers at the panel's
  `eatAt` / `restoreAt` with nothing left to consume, which leaves that much HP/prayer for the
  teleport itself. For `PRAYER` tasks `restoreAt` must therefore sit high enough to cover the
  teleport animation under attack; §12 measures it on the first prayer-tanked task.

`routines/combat.py` keeps the old behaviour (its own bank trip and `returnToSpot`), which is
right for it: its spot is wherever the user parked, usually on the surface.

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

### 7.9 Supplies, charges and gear restore (added 2026-09-19, from the user's questions)

USER: "do we have a way to retrieve these special items and refresh our inventory between
tasks? The teleport rings etc eventually run out, food and prayer pots etc." The bank side
exists (`banker.bank_up` withdraw lists, `autoFood`/`autoPrayer`, `search_and_withdraw`, bank
memory, `gear.ensure_worn`, the fight loop's own mid-task bank trip). Four things did not, and
this section is their design. New module: **`supplies.py`** (pure where it can be).

**The house does most of the work.** MEASURED from the user: the POH has a fairy ring, an
ornate jewellery box, and a pool that restores **prayer and run energy but NOT hitpoints**
(rejuvenation tier). The box and the ring never run out, so games necklace / ring of dueling /
skills necklace / glory / combat bracelet destinations cost nothing. What still depletes:
house teleport tablets (unless a construction cape is owned), slayer rings (8 charges; the
eternal ring has none), items the box does not cover (Xeric's talisman, digsite pendant,
Ectophial refills, teleport crystal, Rada's blessing), food, prayer potions for long Kuradal
fights, and per-task consumables. **HP comes from food, or for free from the Ferox Pool of
Refreshment** whenever a bank trip goes there -- so Ferox stays the preferred bank.

**The loop between tasks** (refines §7.5-7.6):
```
task done -> NPC Contact next task (D7) -> supplies.check(spec)
   ok, nothing to fetch  -> house teleport -> pool (prayer + run) -> POH leg -> site
   something to fetch    -> bank (box -> Ferox, pool heals HP) -> loadout -> house -> pool -> leg
   short of a SKIPPABLE task's items -> SKIP (30 pts), e.g. lizards with no ice coolers
   short of a STAPLE     -> finish nothing new: safety stop naming what to buy
```
Every house pass drinks from the pool (one object click, confirmed by `/player.prayer` rising
or already full). A bank visit is skipped entirely when the inventory already satisfies the
next task -- nine Aya tasks in a row should not mean nine bank trips.

**1. Charge counting.** `supplies.charges(name) -> int | None`: item names carry the count as a
suffix (`Slayer ring (3)`, `Ring of dueling(8)`, `Games necklace(1)`); parse `\((\d+)\)\s*$`,
no suffix = uncharged item or unlimited (eternal slayer ring) = `None`.
`supplies.teleports_left(name)` sums charges over inventory + equipment + bank memory (ONE
`bank_memory.load(names=...)` call; `seen == False` means unknown, never zero). At the bank the
routine takes the **lowest-charge** copy first so partly used rings are finished off, and
swaps in a fresh one when the carried copy has ≤ 1 charge left. A route whose first-leg item
has no charges anywhere is simply not "owned" for §6's route choice, so the next route is tried.

**2. Per-task consumables.** `TaskSpec.carry` becomes `(name, quantity_rule)`: a fixed count
(rope ×1, lit bullseye lantern ×1, antipoison ×1) or a function of the task size
(ice coolers = `count + 5`; fungicide spray: enough charges for `count` kills -- the spray's
charge suffix is read like a ring's). `supplies.check` compares that against
inventory + bank memory BEFORE travelling.

**3. Gear restore -- the hole the first draft had.** Kurask need a leaf-bladed weapon,
basilisks a mirror shield, banshees earmuffs; nothing put the normal gear back, so the next
task would be fought in the wrong kit. Design:
- The **base loadout** is the worn equipment captured when no task gear is on: at `setup` and
  after every task completion, but ONLY if `/slayer.count == 0` or the active task's spec has
  no `gear`. It is persisted to `~/.runelite/bridge-slayer/<accountHash>-base-gear.json`
  (same convention as bank memory), because `ctx.state.data` resets on restart and a restart
  mid-kurask-task would otherwise record the leaf-bladed sword as "normal".
- Task gear is a per-SLOT override of that base. `gear.ensure_worn` puts it on at the bank;
  base items it displaces go into the inventory and are **kept** (added to `keepItems`), not
  banked, so the restore needs no bank visit.
- On task completion (`count` → 0) the routine re-equips every displaced base item before
  anything else, confirmed on `/equipment`. A base item that cannot be found is a safety stop
  naming it.
- A two-handed base weapon plus a shield requirement (mirror shield) needs a one-handed
  alternative: the spec entry names it (`gear=(Gear.worn("mirror shield"), Gear.worn("<1h
  weapon>"))`), chosen with the user when that task's entry is authored.

**4. Supplies are PER TASK, not one global floor** (USER 2026-09-19, correcting the first
draft's flat "8 food / 8 doses": *"a lot more for tasks that mostly rely on prayer to negate
damage, 5-6 full prayer pots which is 4 doses each; as for food it depends on the task -- if
it's nechryaels there is unavoidable damage, we should bring at least 15 food"*). Each
`TaskSpec` carries a **supply profile**:

```python
supplies=Supplies(food=15, prayer_potions=6, extras=(("antipoison", 1),))   # nechryael
```

`prayer_potions` counts FULL (4-dose) potions; the withdraw prefers 4-dose ones and the check
counts doses (`potions * 4`) so three 4-dose and two 2-dose potions are not mistaken for five.
Profiles come from four named defaults so 46 entries are not 46 hand-typed tuples, and any
task overrides its default. The two the user gave are anchors; the other numbers are PROPOSED
and listed per task in `docs/slayer-sites/decisions.md` for the user to correct:

| Profile | For | Prayer potions | Food |
|---|---|---|---|
| `PRAYER` | damage fully negated by one protection prayer (ankou, black demons, bloodveld, hellhounds, trolls, kurask, wyrms at range, ...) | **6** (user: 5-6) | 5 |
| `PRAYER_PLUS_DAMAGE` | prayer negates the monster but something still hits (nechryael's death spawns, kalphite poison, mixed melee/ranged elves) | 6 | **15** (user, nechryael) |
| `SAFESPOT` | fought from a safespot with ranged (waterfiends, zygomites) | 2 | 8 |
| `TRIVIAL` | Aya's tasks: 10-30 kills of level 1-30 monsters | 0 | 3 |

Inventory arithmetic is part of the entry's validation: profile + carry items + displaced
base gear + teleport items must fit in 28 slots, checked by a catalog test, so an entry that
cannot physically be packed fails in CI rather than at the bank.

Every trip starts with full prayer from the house pool, so the potions are for the fight only.
Mid-task, the fight loop's existing "out of critical supplies" bank trip still applies, and it
restocks to the SAME profile (today it restocks to the panel's flat quantities -- the
orchestrator passes the profile's counts down as the `withdraw` list instead).

**Low stock stops early, not late.** Before starting a task, `supplies.check` compares the
task's profile against inventory + bank memory (no bank visit needed). Not enough for THIS
task: a skippable task is SKIPPED, otherwise the routine safety-stops with the list to buy.
One staple has a global floor because every task needs it: `slayerMinHouseTeleports` (5).
**It never buys anything** -- no Grand Exchange restocking, by decision; an empty bank is a
human's job.

**What to take from Slayer Best in Bank** (FreeArcanes, BSD-2, the user's suggestion; surveyed
2026-09-19 in `docs/slayer-sites/slayer-best-in-bank-survey.md`). **Port facts and formulas, do
not vendor the plugin.** Unlike the m8aq readers it is not a set of pure functions: its scorer
sits behind a stateful RuneLite plugin and needs 20+ live inputs supplied by hand, so an adapter
would buy little. Its bank capture is also weaker than ours -- live `ItemContainerChanged` only,
never persisted, cleared on logout -- where `/bank/memory` already survives restarts. It
compiles on Java 11 (`options.release.set(11)`), so that was never the obstacle. Worth lifting:
- **Its required / protective item rules per task**, as a cross-check on `decisions.md`.
- **Its prayer-potion estimate**, a real drain-rate formula (restore per dose at the account's
  Prayer level, prayer bonus of the worn gear, the prayers kept on, trip length). That is
  better founded than a flat "6 potions", so `PRAYER` profiles should compute their count from
  it, with the user's 5-6 as the sanity range and the floor.
- Its **inventory capacity planning** as a model for the 28-slot packing check.
Not worth lifting: its FOOD estimate is pure trip-length scaling with no per-task danger
signal -- it would never have asked for 15 food at nechryael -- so the per-task food numbers
stay the user's. It also recommends a different site than the user chose for 7 of the 22 FIGHT
tasks, because it optimises for wiki best-xp, not this account's travel; the user's sites stand.
Licence note: `slayer-targets.tsv` inside it is **CC BY-NC-SA 3.0** (from osrs-dps-calc), not
BSD; it is monster defence data for DPS maths, which this routine does not need, so it is not
copied.

**Tests** (`tests/test_supplies.py`): the charge parser over real names incl. `Slayer ring
(eternal)` and `Ring of dueling(8)` vs `Slayer ring (8)` spacing; `teleports_left` treats an
unseen bank as unknown; lowest-charge-first selection; quantity rules; the base loadout is NOT
overwritten while a gear task is active (the restart case); restore order on completion;
a missing skippable item routes to SKIP and a missing staple to a stop.

**§12 additions:** the pool object's name/id/action in the user's house and the prayer-rise
confirm; whether item names in `/inventory`, `/equipment` and bank memory all carry the charge
suffix the same way.

### 7.10 Loot during a task (added 2026-09-19, from the user's question)

USER: "Do we dump items we pick up during a task to the bank, like alchables?" **Yes: loot is
banked, never alched.** The account stays on the Lunar spellbook for NPC Contact, and High
Level Alchemy is a standard-spellbook spell, so alching on the spot is not available; nothing
in the codebase alchs either. What exists (`combat_core.maybe_loot`, verified 2026-09-19):
a ground item is taken when its name matches the panel whitelist, OR its GE value is at least
`minLootValue` (default 5000), OR it is a herb/seed and an open herb sack / seed box is
carried; other players' drops are skipped. Every bank visit deposits everything not on the
keep list (`banker.deposit_inventory`), and empties the herb sack and seed box.

Three things the task loop has to add, because a task loadout is nearly a full inventory
(nechryael: 15 food + 6 potions + escape ring + house teleport + slayer ring ≈ 25 of 28):

1. **A full inventory of loot must not cause a bank trip.** `combat_core.needs_banking` returns
   True for "full, and some of it is loot", which for the Combat routine means "go bank". In a
   task that would burn the four RESTOCK trips (§7.5) on three free slots. So the orchestrator
   splits the predicate: **out of supplies → RESTOCK; merely full → looting pauses** and the
   fight goes on. Slots free up by themselves as food is eaten. New public helper
   `combat_core.out_of_critical_supplies(ctx)` already exists for the first half.
2. **Worth making room for -- two tiers** (values confirmed by the USER 2026-09-19: "50k is a
   good value. If it's above 150k make sure it drops or eats either food or prayer pot to pick
   it up. Will not happen a lot anyway"). With a full inventory:
   - **≥ `slayerMakeRoomValue` (50,000): opportunistic.** Eat one food to free a slot if HP is
     not full, then take it. At full HP it is left.
   - **≥ `slayerMustLootValue` (150,000): guaranteed.** A slot is freed whatever it costs:
     eat a food if HP is missing; else finish a **1-dose** prayer potion if prayer is missing
     (only the last dose frees a slot -- the vial is smashed automatically on this account);
     else **DROP** one consumable, choosing the cheapest to lose (the lowest-dose prayer
     potion, else one food). Never drops gear, a teleport item, or a task-required item.
     The drop is confirmed by the slot emptying on `/inventory`, the pickup by the item
     appearing there; the dropped consumable is not picked back up.
   - Below 50,000 a full inventory simply leaves it. Stackables (coins, runes, noted items,
     ammo) are always taken when a stack is already held, since they cost no slot.
   Ground-item value is `totalValue` from `/ground-items` (GE value × quantity), the same figure
   `minLootValue` already uses.
3. **The "skip the bank between tasks" rule (§7.9) must count slots, not just supplies.** A
   bank visit is needed when supplies are short for the next task OR the free slots left after
   packing the next task's profile would be fewer than `LOOT_RESERVE_SLOTS` (4). Otherwise nine
   Aya tasks would slowly fill the inventory with loot and starve the tenth task of space.

Per-profile defaults: **`TRIVIAL` (Aya) tasks loot only the whitelist** -- those monsters drop
nothing worth the clicks, and travel time dominates a 20-kill task; Kuradal tasks use the
panel's `minLootValue`. Bones follow the panel's Bury Bones toggle; a bonecrusher or ash
sanctifier stays protected as today.

**Empty vials are not a problem on this account**: USER 2026-09-19, "they get automatically
smashed" (the Barbarian guard toggle is already on). So a finished potion frees its slot, and
the routine needs no vial handling.

Tests: full-of-loot with supplies left does not derive RESTOCK; the 50k tier respects full HP;
the 150k tier always frees a slot and picks eat → finish-a-1-dose → drop in that order, never
touching gear, teleports or task items; the between-task bank decision counts free slots;
TRIVIAL ignores `minLootValue`.

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

### 8.1 Floors and entrances come from Shortest Path's transport table, not hand-written legs (added 2026-09-19)

USER: "how about travel?" Travel has three layers, and only the middle one was missing:

| Layer | State |
|---|---|
| Long hops: item teleports, POH box/portals, fairy rings | `teleports.py` + `legs.py` + `travel.hop_fairy_ring`. Items and fairy rings are live-proven; **POH legs have never run live** |
| **Changing floor / entering a dungeon** | **nothing** -- `walk_to`, `route_to` and `worldmap.find_path` are same-plane only |
| Walking on one plane, doors | `worldmap.py` + `travel.walk_to`, live-proven |

The first draft filled the middle layer with a hand-authored `Leg.object(...)` per site. That is
46 tasks' worth of entrance tiles to research and get wrong. **The data already exists.** The
installed Shortest Path jar (`shortest-path_*.jar`, the same source the three vendored teleport
TSVs came from) carries `transports/transports.tsv`: **5,634 rows** of
`Origin | Destination | menuOption menuTarget objectID | Skills | Items | Quests | Varbits |
VarPlayers | Duration`. MEASURED 2026-09-19, every entrance the chosen hubs need is in it:

```
2430 3424 0 -> 2429 9824 0   Enter Cave 26709              Stronghold Slayer Cave
1639 3673 0 -> 1666 10050 0  Investigate Statue 27785      Catacombs of Kourend
3417 3536 0 -> 3412 9932 3   Climb-down Ladder 30191       Slayer Tower basement (note: plane 3)
3417 3540 2 <-> 3412 3540 1  Climb-up/down Staircase       Slayer Tower floors
2884 3398 0 -> 2884 9798 0   Climb-down Ladder 16680       Taverley Dungeon
```
The same jar has `fairy_rings.tsv` (115 rows: ring tile, object id, code), `spirit_trees.tsv`,
`gnome_gliders.tsv`, `agility_shortcuts.tsv` (with level requirements) and
`destinations/game_features/bank.tsv` (297 banks -- answers "nearest bank to this site", which
`banker` cannot today).

**IMPORTED 2026-09-19** (USER: "the findings on travelling sound good, import that"). Six
tables are vendored byte-identical in `ControllerAddon/vendor/` (re-extract with
`tools/extract_shortest_path_tables.py`); `ControllerAddon/transports.py` loads them (pure,
headers asserted, requirements fail closed) and `tests/test_transports.py` pins the hub
entrances, fairy-ring codes incl. CJQ → the Great Conch, and the row-count floors. The loader
classifies the table's ~316 plain doors and ~668 Wilderness-ditch rows so a search can leave
them out, and found one trap worth knowing: `fairy_rings.tsv` contains a DIAL SEQUENCE row
(the Fairy Queen's hideout) that must not be read as aliases. 

**Built 2026-09-19, UNVERIFIED LIVE:** the route search (`crossfloor.py`), the executor
(`legs.run_transport`) and the floor-crossing walker (`legs.walk_across`), per
`docs/superpowers/plans/2026-09-19-crossfloor-travel-and-slayer-pure-pieces.md`. The routes it
finds for the chosen sites are in `docs/slayer-sites/routes.md`, which also records what it
could NOT route: the Karuulm Slayer Dungeon and the Great Conch gryphon cave have no row in the
vendored tables (the catalog hand-writes those legs), the Ancient Cavern whirlpool is gated on
varbit 3759 which nothing supplies yet, and Taverley's black demons need 80 Agility or the
dusty-key gate. Also built from this spec the same day: `supplies.py` (§7.9's pure half),
`slayer_state.py` (§7.2's `next_master`, the F2 guard, skip limits) and `aya_tasks.py` (§6.1).

**Design:** vendor `transports.tsv`, `fairy_rings.tsv` and `bank.tsv` byte-identical beside the
three already in `ControllerAddon/vendor/` (header names asserted, as `teleports.load_all`
does), and give routing a **transport edge**:

- `worldmap.find_path` stays the same-plane engine. A new `travel.route_across(start, goal)`
  searches over (plane-local walk) + (transport rows whose requirements are met), bounded like
  `teleports.plan` (`MAX_TRANSPORTS` 4, the approach-walk caps from banker's planner, because
  the no-data void joins every dungeon). It returns walk segments and transport steps.
- A transport step is executed by ONE generic primitive, `legs.run_transport(step)`: walk to
  `Origin` (goal-adjacent), find the object by **id** at that tile (`/object/at`), click it with
  `expected_action=menuOption`, confirm by `/player` landing within a tile or two of
  `Destination` (plane included). This replaces `Leg.object` entirely; the catalog's routes
  shrink to "first-leg teleport, then `walk(site)`" and `walk` is allowed to cross floors.
- Requirements columns (Skills/Quests/Varbits/Items) FAIL CLOSED on unknown, the rule
  `teleports.varbits_satisfied` already follows; agility shortcuts are only taken when
  `/skills` proves the level.
- Catalog test (replaces the same-plane reachability test): for every FIGHT site,
  `route_across(landing_of_first_leg, site)` returns a route offline, and its transport steps
  are printed into `docs/slayer-sites/` so a human can sanity-check them once.

This is a shared-module change (`travel.py`, a new loader, `legs.py`), so it is its own
sub-project BEFORE the catalog: every other routine that today hard-codes a ladder gets it too.

**The real travel risk is the house, not the dungeons.** The POH is the hub of every route, and
it is (a) an INSTANCE -- forward world-coordinate resolution is the known-broken direction
inside instances (CLAUDE.md "Instanced areas"), and `legs.run_object_leg` walks to the TSV's
template tile `1870 5698` with `travel.walk_to`, which routes on the OVERWORLD map; (b) laid
out per player, so the box and the ring are wherever this user built them; (c) never run live.
Design for it rather than hope: inside the house, do NOT route. Find the object by NAME in
`/objects` (its instance-corrected tile is already served), and click it -- the game paths the
player there itself, and a house is small enough that the object is in view from the portal.
Confirm the jewellery-box / fairy-ring interface opening (`/widget`), then the existing pickers.
Only if the object is not in `/objects` does `instance_nav.walk_to` step toward the room. The
house must be in **building mode off**, with **teleport-inside** set (varbit 4744, already a
gate in `teleports.py`). This is §12's first travel item, ahead of any dungeon.

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
   This is already useful and proves F1/F2/F4 live. **Built 2026-09-19**
   (`docs/superpowers/plans/2026-09-19-slayer-acquire-and-skip.md`), NOT run live; FIGHT is limited
   to Aya sites the player is already standing in, and away from one the routine stops and says where to go.
3. **Catalog + travel + loadout**: wave-1 sites, BANK and TRAVEL phases, object legs, wave 2.

## 14. Decisions taken without the user (change any of these and the spec adapts)

- **D1** ~~Home bank = Ferox via ring of dueling~~ **SUPERSEDED 2026-09-18**: the user's house
  has a fairy ring and an ornate jewellery box, so the POH is the travel hub; a bank (Ferox or
  Castle Wars via the box) is only where the loadout is taken (§6.2).
- **D2** Undoable task = cancel for 30 points, not block. Blocking is the user's manual call.
  **Extended 2026-09-18**: dispositions are FIGHT / SKIP / STOP, decided per task by the user
  (`docs/slayer-sites/decisions.md`).
- **D3** No cannon. The wiki calls it "strongly recommended" for boosting; it is a separate
  sub-project (placement, reloading, pickup before breaks) and out of scope here.
- **D4** Tasks whose routes are not authored yet are cancelled rather than blocking the whole
  routine on ladders. Costs ≤30 points per roll. (The "wave 1 / wave 2" split of the first
  draft is gone: Aya's sites now come from the Turael Skipping registry, §6.1.)
- **D5** Milestone master is Kuradal per the request, with Konar as the wiki-recommended
  alternative left as a later enum value (her location-locked tasks need a different catalog
  shape).
- **D6** The panel's old "Slayer / Combat" selection becomes the new task routine after the
  rebuild; users wanting the old behaviour pick "Combat".
- **D7** Tasks are acquired at the kill site of the previous task (NPC Contact works
  anywhere), BEFORE the trip to the bank, so one bank visit fits the next task's loadout.

## 15. Open questions for the user

1. ~~Which teleports does the account own?~~ ANSWERED in part 2026-09-18: POH fairy ring and
   ornate jewellery box; the rest is answered live by bank memory, route by route.
2. Is the account already on the Lunar spellbook with Lunar Diplomacy done and Magic ≥ 67, and
   does it meet Kuradal's combat 100 / Slayer 50? The spec assumes yes.
3. ~~Block nuisance Aya tasks?~~ ANSWERED: dogs are already blocked; the rest are fought at the
   Turael Skipping plugin's spots.
4. ~~Which Kuradal tasks to fight?~~ ANSWERED: 22 FIGHT, 1 STOP, 20 SKIP
   (`docs/slayer-sites/decisions.md`). Still open: whether to block the heaviest skipped tasks
   at Kuradal (§6.2) -- the user's manual call.
