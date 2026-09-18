# Microbot-Hub Slayer plugin survey

Source: `github.com/chsami/Microbot-Hub`, sparse-cloned to
`...\scratchpad\microbot-hub\`, package `net.runelite.client.plugins.microbot.slayer`
(+ the older `aiofighter/combat/SlayerScript.java`). All citations are `File.java:line`
against that checkout. Read-only research comparing against
`docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` (sections 6-9, 12) and
`docs/slayer-sites/decisions.md`.

Files read in full: `docs/README.md`, `SlayerState.java`, `SlayerScript.java` (4152 lines,
entire file), `SlayerConfig.java`, `SlayerMaster.java`, `SlayerLocation.java`,
`PohTeleportMethod.java`, `LootStyle.java`, `CannonSpot.java`, `PrayerFlickStyle.java`,
`SlayerCombatStyle.java`, `SlayerPrayer.java`, `profile/SlayerTaskProfileJson.java`,
`profile/SlayerProfileManager.java`, `combat/SlayerDodgeScript.java`,
`combat/SlayerFlickerScript.java`, `combat/SlayerHighAlchScript.java`,
`combat/SlayerMonster.java`, `combat/AttackStyle.java`, `SlayerPlugin.java`, and
`aiofighter/combat/SlayerScript.java`.

Everything Microbot does with `Rs2*` helper calls (`.click()`, `Rs2Walker.walkTo`,
`Rs2Inventory.interact`) is software-injected input and is explicitly **not** transferable to
my physical-Arduino setup — only cited where it reveals a *decision* (what to click, when, in
what order, under what confirmation), never as a clicking mechanism to copy.

---

## 1. What it is

A single-file-per-concern Microbot script plugin (version `1.1.0`, `SlayerPlugin.java:45`),
mature enough to ship a README with a full config table, default profiles, and a documented
"Known Limitations" section. It targets **8 masters**: Turael, Spria, Mazchna, Vannaka,
Chaeldar, Nieve/Steve, Duradel/Kuradal (`SlayerMaster.java:10-18`) — **Konar is explicitly
excluded** ("location-specific task complexity", `SlayerMaster.java:19`, and README.md:272:
"Konar not supported"). There is no master-alternation / streak-bonus logic anywhere — the user
configures exactly one `slayerMaster` (`SlayerConfig.java:53-55`, default `DURADEL`) and the
plugin always asks that one master. This is a **structural difference from my design**: my spec
alternates Aya/Kuradal by streak for the milestone bonus (§7.2); Microbot has no concept of
"filler master" vs "milestone master" at all.

It is a `Script` running on a 600 ms `scheduleWithFixedDelay` tick loop
(`SlayerScript.java:179-263`), reading everything through Microbot's own `Rs2Slayer` /
`Rs2NpcManager` / `Rs2InventorySetup` helper layer rather than raw varbits/varps in most places
(exception: several specific varbits/varps are read directly, listed in §10). Configuration is
via a `@ConfigGroup` with 6 sections; every option and its default (`SlayerConfig.java`):

**General** (`SlayerConfig.java:16-66`): `enablePlugin` (true), `enableAutoTravel` (true),
`slayerMaster` (DURADEL), `getNewTask` (true).

**Task Management** (`:71-143`): `enableAutoSkip` (false), `skipTaskList` (""),
`minPointsToSkip` (100), `enableAutoBlock` (false), `blockTaskList` (""), `minPointsToBlock`
(150).

**Banking** (`:148-187`): `enableAutoBanking` (true), `foodThreshold` (3), `potionThreshold` (4,
prayer-restore doses).

**POH** (`:192-253`): `usePohPool` (false), `pohTeleportMethod` (HOUSE_TAB), `usePohBeforeBanking`
(true), `usePohAfterTask` (true), `pohRestoreThreshold` (50, % HP).

**Combat** (`:258-385`): `enableAutoCombat` (true), `attackRadius` (10), `prioritizeSuperiors`
(true), `eatAtHealthPercent` (50), `prayerFlickStyle` (OFF), `drinkPrayerAt` (20),
`useCombatPotions` (false), `useOffensivePrayers` (false), `dodgeProjectiles` (false),
`hopWhenCrashed` (true), `hopWorldList` ("").

**Cannon** (`:390-429`): `enableCannon` (false), `cannonballThreshold` (100), `cannonTaskList`
("").

**Loot** (`:434-671`): `enableLooting` (true), `lootStyle` (MIXED), `lootItemList` (""),
`lootExcludeList` (""), `minLootValue` (1000), `maxLootValue` (0), `lootCoins` (true),
`minCoinStack` (0), `lootArrows` (false), `lootRunes` (false), `lootUntradables` (true),
`lootBones` (false), `buryBones` (false), `scatterAshes` (false), `forceLoot` (false),
`onlyLootMyItems` (false), `delayedLooting` (false), `eatForLootSpace` (false),
`enableHighAlch` (false), `highAlchItemList` (""), `highAlchExcludeList` ("").

Per-task behaviour (gear, prayer, cannon, location) is driven by a separate JSON file (§5), not
by config — the config section only sets global toggles/thresholds.

**Known limitations documented in the README** (`docs/README.md:269-275`): boss tasks not
supported at all ("Like a Boss" must be disabled or the boss-amount dialogue stalls the plugin);
Konar excluded; **no death recovery** (logs out on death, user recovers items manually); some
task locations need quests the plugin does not check; burst/barrage needs the Ancient spellbook,
swapped via POH lectern (needs a house with a lectern).

---

## 2. State machine

`SlayerState.java:3-14` — 11 states, a flat non-hierarchical `enum`: `IDLE`, `GETTING_TASK`,
`SKIPPING_TASK`, `BLOCKING_TASK`, `DETECTING_TASK`, `RESTORING_AT_POH`, `BANKING`,
`SWAPPING_SPELLBOOK`, `TRAVELING`, `AT_LOCATION`, `FIGHTING`. Static state lives on
`SlayerPlugin` (`@Getter @Setter private static SlayerState state`, `SlayerPlugin.java:78-79`)
so it survives Script-object identity but not process restarts (no persistence — a JVM restart
loses it entirely, unlike my design's `ctx.state.data` which is deliberately re-derived every
tick from live state per §7.1).

**Every tick, unconditionally** (`SlayerScript.java:200-217`): read points varbit `4068`
(`SLAYER_REWARD_POINTS_VARBIT`, `:71,201`); `hasTask = Rs2Slayer.hasSlayerTask()`; if true,
`taskName = Rs2Slayer.getSlayerTask()`, `remaining = Rs2Slayer.getSlayerTaskSize()` (`:205-212`).
These three values are **not** independently re-derived per state — they gate the `switch` at
the top of every tick, and each `handleXState` additionally reads its own signals (dialogue
open, widget visible, bank open, player moving/distance). There is **no direct read of a
"streak" varbit/varp anywhere in this plugin** — not present, because there's no
alternating-master feature to need it for.

State transitions (source: the full body of `SlayerScript.java`, cited per state):

- **IDLE** (`:265-291`) → has task: check block list first (permanent takes priority over skip)
  → `BLOCKING_TASK`; can't afford block but on skip list → `SKIPPING_TASK`; can't afford
  either → `DETECTING_TASK` anyway (does the task); on skip list only → `SKIPPING_TASK`; else
  → `DETECTING_TASK`. No task + `getNewTask` on → `GETTING_TASK`.
- **GETTING_TASK** (`:293-368`) → if in dialogue, `handleSlayerDialogue()` (below) and return.
  Once a task exists, same block/skip cascade as IDLE, with an added **hard-stop case**: on
  block or skip list but insufficient points for *either* → `Microbot.showMessage(...)` +
  `stopAndLogout()` (`:314-330`) — the plugin refuses to proceed with an unwanted task if it
  truly cannot afford to shed it. If no task yet: walk to `master.getLocation()` when
  `distanceTo > 5`; at ≤5, click the master NPC with menu action `"Assignment"`
  (`:352-360`) and `sleepUntil(Rs2Dialogue::isInDialogue, 3000)`.
- **`handleSlayerDialogue()`** (`:370-431`): click-through cascade, first match wins, one
  option per tick with a 300-600 ms sleep: `hasContinue()` → click continue; dialogue option
  containing **"I need another assignment"**; else containing **"assignment"**; else containing
  **"easier"**; else containing **"Okay"**; else containing **"great"**; else if task already
  present and still in dialogue, press **Escape**. Boss-amount input (chatbox widget `162.42`,
  text containing "how many would you like to slay"/"how many would you like to hunt",
  `:438-456`) has a full handler (`handleBossAmountInput`, `:462-471`, types "3" + Enter) but
  it's **commented out at the call site** (`:374-377`) — the README's "boss tasks not supported"
  limitation is literally this dead code path, left in but disabled.
- **SKIPPING_TASK** (`:473-561`) → in dialogue or widget group 426 visible: run
  `handleSkipDialogue()`. Detects success by **task changing or disappearing**, not by a direct
  "skip succeeded" signal: `!Rs2Slayer.hasSlayerTask()` → success → `GETTING_TASK`;
  `taskName != previously-tracked` → success, and if the *new* task is also skip-listed, loops
  again without re-entering the state (`:490-501`). `MAX_SKIP_ATTEMPTS = 10` (`:73`) then gives
  up and proceeds with the task (`:511-516`) rather than looping forever. Points checked against
  both the flat `SKIP_TASK_COST = 30` (`:72`) and `config.minPointsToSkip()`; insufficient →
  message + `stopAndLogout()` (`:518-528`). Walk-then-click-"Rewards" on the master identical to
  GETTING_TASK's walk-then-click-"Assignment" pattern.
- **`handleSkipDialogue()`** (`:821-881`) — two parallel paths depending on whether the
  skip/cancel flow is old-style dialogue text or the widget-based rewards interface (group
  `426`, `SLAYER_REWARDS_GROUP_ID`, `:819`): dialogue path clicks through **"Assignment"** →
  **"Cancel current task"** (or **"Skip task"**, or partial **"cancel"**) → **"Yes"**; widget
  path (`handleSlayerRewardsInterface`, `:887-910`) clicks **"Tasks"** tab → **"Cancel"** button
  → **"Confirm"** overlay, in that fixed order, first-match-wins each tick.
- **BLOCKING_TASK** (`:963-1069`) — structurally identical to SKIPPING_TASK with
  `BLOCK_TASK_COST = 100` (`:78`), `MAX_BLOCK_ATTEMPTS = 10` (`:79`); on running out of attempts
  it falls back to trying SKIPPING_TASK instead of just proceeding (`:1004-1013`); on
  insufficient points for BOTH block and skip → `stopAndLogout()` (`:1026-1031`). Widget path
  (`handleSlayerRewardsBlockInterface`, `:1127-1150`): **"Tasks"** → **"Block"** → **"Confirm"**.
- **DETECTING_TASK** (`:1207-1327`) → dismiss any residual dialogue first. No task →
  `IDLE` + `resetTravelData()`. Otherwise: look up the JSON profile
  (`profileManager.findProfile(taskName)`); if none found, **proceeds anyway** with a warning
  log rather than failing (`:1237-1240`) — prayer/location just won't apply. Sets
  `isUsingCannon = shouldUseCannon(taskName)`. Location is chosen by a **4-tier cascade**
  (`:1253-1309`, detailed in §4). Then: if auto-banking on and an inventory setup exists, bank
  first if `!initialSetupDone || needsBanking()` → `BANKING`; else if auto-travel on →
  `TRAVELING`; else stays in `DETECTING_TASK` (auto-travel off is a supported "manual travel"
  mode — the plugin idles at this state forever until the user walks there themselves and
  presumably flips `enableAutoTravel`, though there's no evidence in this file of it detecting
  arrival while auto-travel is off).
- **BANKING** (`:1329-1434`) → dismiss dialogue; `deactivateProfilePrayer()`; **safety check**:
  if `cannonPlaced` is still true here (implies the player teleported away without picking it
  up), checks if it's nearby and picks it up, else just logs a warning and resets the flag
  (`:1346-1359`) — an acknowledged "cannon left behind" leak path, not silently ignored. No
  inventory setup configured → skip banking entirely, go straight to TRAVELING/DETECTING_TASK
  (`:1362-1371`). Bank not open → `Rs2Bank.openBank()` if nearby else `Rs2Bank.walkToBank()`
  (`:1373-1385`, note: **nearest bank**, not a task-specific bank). Bank open → load
  `Rs2InventorySetup` (Microbot's Inventory Setups plugin integration); if it already matches,
  skip reloading and just `withdrawFinishingItems()` (ice cooler/bag of salt/rock hammer, topped
  up to `remaining - alreadyHave`, `:1436-1457`); else `loadEquipment()` + `loadInventory()`.
  After a successful load: `needsSpellbookSwap()` → `SWAPPING_SPELLBOOK`; else
  `enableAutoTravel` → `TRAVELING`; else `DETECTING_TASK`.
- **SWAPPING_SPELLBOOK** (`:1556-1679`) — reads the required spellbook off the *inventory
  setup's own metadata* (`InventorySetup.getSpellBook()`, int 0-4, `:1459-1547`), not off the
  task profile. Already correct spellbook → skip straight through. Needs POH (teleport there via
  `teleportToHouse()`, same method as RESTORING_AT_POH). At the altar: tries the direct
  right-click option first (**"Ancient Magicks" / "Lunar" / "Standard"**,
  `getAltarOptionForSpellbook`, `:1747-1757`); if that menu option isn't present, tries
  **"Venerate"**; if that fails too, a bare interact, then handles a resulting "Select an
  Option" dialogue or widget by the same three option-text strings
  (`handleSpellbookWidget`, `:1686-1721`). `MAX_SPELLBOOK_SWAP_ATTEMPTS = 5` (`:99`) before
  giving up and leaving without swapping. On success, `finishSpellbookSwap()`
  (`:1726-1742`) also calls `setupAutoCastIfNeeded()` **while stationary in the house**
  specifically because setting autocast is a UI interaction that movement can interrupt — an
  explicit design note in the code (`:1729`).
- **TRAVELING** (`:1803-1835`) — dismiss dialogue; no destination → back to `DETECTING_TASK`;
  arrived (`distanceTo(taskDestination) <= ARRIVAL_DISTANCE` = 10, `:69,1824`) →
  `AT_LOCATION`; else walk if not already moving (fire-and-forget `Rs2Walker.walkTo`, re-issued
  only when not moving — no leg/waypoint model, no mid-walk re-aim).
- **AT_LOCATION** (`:1837-1971`) — task complete/no task → pick up cannon if placed, reset
  travel data, then POH-after-task (if enabled) or GETTING_TASK/IDLE. Otherwise: **cannon
  placement happens here, gated by proximity** (`distanceTo(cannonSpot) <= 2`, `:1870-1909`) —
  walks closer first if not; assembly is a **blind 6-7 s sleep** (`:1878`, "Cannon assembly
  takes about 6 seconds" — a fixed guess, not confirmed by any state read) then
  `Rs2Cannon.refill()`. Cannon low-ammo check can flip `isUsingCannon` off and re-derive
  location via `DETECTING_TASK` (`checkAndDisableCannonIfLow`, detailed in §6). POH-before-bank
  and bank-need checks both gated the same way as FIGHTING (below). Finally, if
  `enableAutoCombat` → `FIGHTING`.
- **FIGHTING** (`:1973-2115`) — task complete detection has a **5-second post-completion loot
  window** (`TASK_COMPLETE_LOOT_DELAY_MS = 5000`, `:120`, `:1976-2008`): once `!hasTask ||
  remaining <= 0`, it keeps calling `handleLooting()` every tick for 5 more seconds before
  transitioning, so drops from the last kill aren't abandoned. Per-tick order inside FIGHTING
  (not just once): `activateProfilePrayer()` → sync flicker script's active prayer →
  `handlePotions()` → `handleFungicideRefill()` → `handleSlayerFinishingItems()` →
  `handleEating()` (captures whether food was eaten this tick) → cannon maintenance → cannon
  low-ammo check (→ `DETECTING_TASK` if triggered) → POH panic-teleport check (**skipped this
  tick if food was just eaten** — `:2076`, "give it time to heal before teleporting" — POH is
  explicitly a last-resort panic button, not the first response to low HP) → bank-need check →
  `handleLooting()` → `handleCombat()`.

**No explicit "which task / kills remaining" derivation beyond `Rs2Slayer.getSlayerTask()` /
`getSlayerTaskSize()`** — these are Microbot library calls whose internal varp/varbit reads are
not visible in this codebase (Microbot itself is not vendored here, only the plugin that calls
it). This is the single biggest thing **not present**: my design's raw varp 394/395 + varbit
4067/4069 reads (spec §10) have no equivalent shown in this survey — Microbot hides that behind
`Rs2Slayer`, so this survey cannot confirm or contradict my spec's specific varp/varbit ids for
task count/master/streak from this codebase alone (only the points varbit, 4068, is directly
visible and it matches).

---

## 3. Getting a task

**No NPC Contact spell usage anywhere in this plugin** — not present. Every task
acquisition/skip/block is done **in person**: walk to `master.getLocation()` (a fixed
`WorldPoint` per `SlayerMaster`, §4) and click the master NPC directly with menu action
`"Assignment"` or `"Rewards"` (`SlayerScript.java:352-360`, `:544-549`, `:1052-1057`). There is
therefore **nothing in this codebase to reuse as facts for my spec's §9 NPC Contact / widget
75/426 dialogue mechanics** — Microbot's dialogue-option strings (below) are still useful as
*likely* in-person dialogue text (NPC Contact should present the same underlying dialogue tree,
just reached via a different UI), but the widget ids, magic-tab mechanics, and the
"greyed-row-means-uncontactable" NPC Contact-specific behaviour in my spec have no counterpart
here to confirm.

Dialogue-option substrings used, first-match-wins, useful as candidate NPC Contact option text
to verify live (all from `handleSlayerDialogue`, `SlayerScript.java:370-431`): **"I need another
assignment"**, **"assignment"** (partial), **"easier"** (partial — a "got any easier tasks?"
style option, not otherwise explained or gated by points in this file, so may just be a normal
dialogue branch that gets clicked through rather than acted on for a reason), **"Okay"**,
**"great"** — the last two are guessed as "post-task dismissal" text with no stronger
confirmation in this file.

**Skip mechanism = the same in-game "Rewards" interface my spec targets** (widget group **426**,
`SLAYER_REWARDS_GROUP_ID`, `:819`), reached in person via right-click "Rewards" on the master
NPC rather than via NPC Contact. Two parallel implementations exist because the interface can
apparently present either as classic dialogue text or as the modern widget
(`handleSkipDialogue`/`handleBlockDialogue` try dialogue text first, then check
`Rs2Widget.isWidgetVisible(426, 0)`, `:474-475`, `:964-965`). Widget-path button texts: **"Tasks"**
tab → **"Cancel"** (skip, 30 pts) / **"Block"** (100 pts) → **"Confirm"**
(`handleSlayerRewardsInterface`/`handleSlayerRewardsBlockInterface`, `:887-910`, `:1127-1150`).
This directly corroborates my spec §7.4's "interface 426 → Tasks tab → Cancel → confirm dialogue"
mechanism (widget group id **426** matches exactly), though it gives no child/component ids
within group 426 — the plugin matches on button/tab **text**, not child index, throughout
(`Rs2Widget.clickWidget("Confirm", Optional.of(426), 0, false)` etc.) — worth copying as a
strategy: click-by-visible-text is more robust to a child-id renumbering than a hardcoded index.

Skip/block success is detected **indirectly**: either `hasSlayerTask()` goes false, or the task
name changes (`:481-502`, `:971-995`) — there is no direct "cancelled" chat message or varp
watched in this file. My spec's plan to confirm via `varp 394 → 0` is a stronger, more direct
signal than what Microbot does here.

**Point safety** is symmetric and stricter than my spec's default: both `minPointsToSkip` (100)
and `minPointsToBlock` (150) default well above the raw costs (30/100), explicitly to "keep a
reserve" (README.md:81,84) — closer in spirit to my spec's `pointsReserve` (default 60) but
with a much higher floor. A task that's on the block/skip list but genuinely unaffordable is a
**hard stop with logout**, not a silent "do the task anyway" — my spec's equivalent is a safety
stop without forced logout (§7.4), which is probably the better behaviour for an unattended
physical-input rig (logging out mid-session loses the human-realism session-tempo context my
CLAUDE.md's movement-realism rules care about).

**Boss task / "Like a Boss" handling**: fully implemented but deliberately disabled
(`SlayerScript.java:374-377` comments out the call), because the dialogue flow (a free-text
number entry, `:438-471`) can't be driven reliably — the README instructs the user to turn the
"Like a Boss" perk off instead of trying to automate around it. This is a candidate fact for my
own spec: if the account has "Like a Boss" enabled, my routine needs the same detection (chatbox
widget `162.42`, text containing "how many would you like to slay"/"hunt") to at least safety-stop
rather than stall silently the way this plugin's disabled code path would.

---

## 4. Task → location data

`SlayerLocation` (`SlayerLocation.java`) is a flat `enum` of **~80 entries** (I count 81 named
constants across the file), each `(key, displayName, WorldPoint)` — no area/radius, no plane
alternatives beyond what's baked into the single WorldPoint, no NPC-id list, no gear
requirement, no `verified` flag. Lookup is `fromName(String)`: exact `key` match or exact
`displayName` match, case-insensitive (`:173-182`) — this is a **weaker** data model than my
spec's `Site` (which carries `areas` rectangles, `region`, and is unit-tested for
map-reachability); Microbot's is literally just "one destination pixel per location key", and
arrival is a flat 10-tile radius check (`ARRIVAL_DISTANCE`, `SlayerScript.java:69`) with no
area/region concept at all.

**Location is chosen by a 4-tier cascade** (`SlayerScript.java:1253-1309`), evaluated once per
task (cached in `taskDestination` until `resetTravelData()`):
1. Using cannon + profile has `cannonLocation` → that `SlayerLocation`, and `cannonSpot` is set
   to the same point (cannon will be placed there).
2. Not using cannon + profile has `location` → that `SlayerLocation`.
3. Using cannon, no `cannonLocation` in profile → `CannonSpot.getClosestSpot(taskName,
   playerLocation)` (§5/§8 cannon spots, a **separate, independent** enum/dataset from
   `SlayerLocation`).
4. Fallback: `Rs2Slayer.getSlayerTaskLocation(3, true).getBestClusterCenter()` — a Microbot
   library call backed by `MonsterLocation`/`Rs2NpcManager.loadJson()` NPC-spawn data (loaded at
   `run()`, `:169-174`), i.e. Microbot has its *own* separate, larger, NPC-spawn-derived location
   database that only kicks in when nothing more specific is configured. Not inspectable from
   this codebase (the JSON itself isn't vendored here).

No entrance/ladder/stairs modelling anywhere — `SlayerLocation`'s WorldPoints are frequently
**already inside** a dungeon (e.g. `CATACOMBS` = `(1666, 10049, 0)`, `STRONGHOLD_CAVE` =
`(2431, 9806, 0)`, both on the underground y+6400ish coordinate band), and `Rs2Walker.walkTo` is
simply trusted to get there, doors/ladders/trapdoors and all. This is Microbot's own pathfinder
doing work my spec's `Leg.object` primitive exists specifically to do explicitly (spec §8) —
confirms my read that this is "the only genuinely new mechanism" needed, since Microbot's
walker's door/ladder handling is invisible, licensed-input machinery I can't borrow, not a gap
in my design.

### Gap/discrepancy table (my decisions.md FIGHT list vs Microbot's SlayerLocation/CannonSpot)

| Task (my decisions.md) | My site / note | Microbot's `SlayerLocation` entry | Microbot's `CannonSpot` entry | Comparison |
|---|---|---|---|---|
| Cave horrors (Mos Le'Harmless) | "no close item teleport found — settle at implementation"; access unresolved | `MOS_LEHARMLESS_CAVE_HORRORS` = `(3740, 9373, 0)` (`SlayerLocation.java:129`) | `CAVE_HORROR` = `(3785, 9460, 0)` (`CannonSpot.java:37`) | **Two DIFFERENT points for the same monster within Microbot's own codebase** (87 tiles apart) — neither file states how to physically reach the cave (boat to Mos Le'Harmless is implied but not coded); no access/quest note anywhere. Does not resolve my "settle at implementation" gap. |
| Dagannoth / Dust devils (Catacombs of Kourend) | via Xeric's talisman → Xeric's Heart, statue down; entrance unresolved beyond that | `CATACOMBS` (entry hub) = `(1666, 10049, 0)`; `CATACOMBS_DAGANNOTH` = `(1674, 9997, 0)`; `CATACOMBS_DUST_DEVILS` = `(1716, 10032, 0)` (`:17-27`) | `DAGANNOTH` cannon spots include `(2524,10020,0)` (Lighthouse, not Catacombs) and two Fremennik-area points; **no Catacombs-specific dagannoth cannon spot listed** | Confirms Catacombs as the right dungeon and gives interior per-monster points, but **no entrance-object coordinate or access item is coded anywhere** — Microbot relies entirely on its opaque walker to get in, same gap I have. |
| Stronghold Slayer Cave (spectres/ankou/bloodveld/fire giants/hellhounds — my single hub site) | one hub site for all five tasks | `STRONGHOLD_CAVE` hub = `(2431, 9806, 0)`; **per-monster sub-points spread widely**: `BLOODVELDS (2426,9820,0)`, `HELLHOUNDS (2413,9785,0)`, `FIRE_GIANTS (2392,9782,0)`, `ABERRANT_SPECTRES (2453,9793,0)` [wrong task but same cave], `ANKOU` not in this enum at all — only `stronghold ankou` under `SlayerLocation` = `(2393, 9806, 0)` (`:42-51`) | `CannonSpot.FIRE_GIANTS`/`HELLHOUNDS` list matching Stronghold points among several other caves' points too | **My "one hub site" assumption may be too coarse.** Microbot's own sub-points for fire giants (2392,9782) vs hellhounds (2413,9785) vs bloodveld (2426,9820) are up to ~40 tiles apart inside the same cave system — worth checking live whether a single site tile actually reaches every one of the five spawns, or whether per-monster sub-tiles (as Microbot models) are needed. |
| Abyssal demons / Gargoyles / Nechryael → my "Slayer Tower basement (task-only)" | task-only basement access via basement stairs | `SLAYER_TOWER_BASEMENT` = `(3417, 9932, 0)` exists as a *separate* entry, but the three per-monster entries actually used are on the **regular tower's upper floors**: `SLAYER_TOWER_GARGOYLES (3442,3543,2)`, `SLAYER_TOWER_NECHRYAEL (3438,3558,2)`, `SLAYER_TOWER_ABYSSAL_DEMONS (3420,3568,2)` — all **plane 2**, i.e. the normal top floor, not the basement (`:31-39`) | none of the three appear in `CannonSpot` | **Possible real discrepancy, not just a data gap**: Microbot's plugin sends these three to the *regular* Slayer Tower top floor rather than the task-only basement my decisions.md describes. Either (a) Microbot's plugin predates or doesn't model the task-only basement distinction and just fights the normal-access monsters, or (b) my decisions.md's basement framing is specific to a mechanic Microbot's author didn't bother separating. This needs live verification before trusting either source — flagged as a live-session checklist item, not resolved here. |
| Karamja monkeys | user override: fight on Karamja instead of Kruk's Dungeon, exact tile TBD | **Not present.** No "monkey" entry in `SlayerLocation` or `CannonSpot` at all. | not present | No data to borrow; my decisions.md's Karamja choice is genuinely outside what this reference implementation covers. |
| Great Conch gryphons | fairy ring CJQ → eastern task-only cave | **Not present.** No "gryphon" entry anywhere in `SlayerLocation`, `CannonSpot`, or `SlayerProfileManager`'s default profiles. | not present | Gryphons are newer (Sailing-skill-gated) content this plugin doesn't cover at all — confirms my design is ahead of this reference here, not behind it. |

**Cross-check on Duradel/Kuradal's shared location**: `SlayerMaster.java:17-18` gives both
`DURADEL` and `KURADAL` the **identical** `WorldPoint(2869, 2982, 1)` (Shilo Village), with a
comment "Replaces Duradel after While Guthix Sleeps" — useful confirmation that Kuradal is a
drop-in replacement at the same physical spot, matching my spec's `masters=("aya","kuradal")`
framing where Kuradal stands in for Duradel.

---

## 5. Per-task profiles

Format: `~/.runelite/slayer-profiles.json` (`SlayerProfileManager.java:24,31`), a flat
`Map<String taskNameKey, SlayerTaskProfileJson>`, generated with **42 default entries** on first
launch if the file doesn't exist (`createDefaultProfiles`, `:84-475`; README.md:174 claims
"40+", matching). `SlayerTaskProfileJson` fields (`SlayerTaskProfileJson.java`), all
`@SerializedName`-mapped, all optional with defaults:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `setup` | String | null | Inventory Setups plugin gear-set name; `{setup}-cannon` is tried automatically when cannon is active (`SlayerScript.java:3856-3865`) |
| `style` | String | `"melee"` (via `SlayerCombatStyle.fromString`'s fallback) | `melee\|ranged\|magic\|burst\|barrage`, with aliases (`m/r/mage/b/bb/ice burst/ice barrage/...`, `SlayerCombatStyle.java:26-57`) |
| `prayer` | String | `"none"` | Parsed via `SlayerPrayer.fromString`, accepts many aliases (`pmelee/pm/pmage/pmagic/prange/pr/...`, `SlayerPrayer.java:25-77`) |
| `cannon` | boolean | false | Per-task cannon opt-in |
| `goading` | boolean | false | Auto-forced true for burst/barrage regardless of this flag (`shouldUseGoading()`, `:176-183`) |
| `antipoison` | boolean | false | Informational only in this file — I found **no code path that reads `activeJsonProfile.isAntipoison()`** anywhere in `SlayerScript.java` beyond a log line (`:1233-1235`); poison handling itself is triggered purely by the live poison varp (102), not this flag. Same for `antivenom`. **Effectively dead/unused fields** as far as this survey can tell — worth noting since my spec might reasonably assume a flag like this gates behaviour, when in this implementation it doesn't. |
| `antivenom` | boolean | false | Same caveat as above |
| `superAntifire` | boolean | false | **Is** read and acted on (`handlePotions()` → `handleSuperAntifire()`, `:2245-2247`, `:2362-2411`) |
| `variant` | String | null | Overrides target monster name entirely — exact-match only when set (`isUsingVariant()`/`matchesTargetMonster`, `:3220-3243`) |
| `location` | String | null (or `"auto"`) | §4 cascade tier 2 |
| `cannonLocation` | String | null | §4 cascade tier 1 |
| `useSpecial` | boolean | false | **Declared but never read** anywhere in `SlayerScript.java` — grep confirms no `useSpecial()`/`isUseSpecial()` call site. Dead field. |
| `specialThreshold` | int | 50 | Same — declared, never read. Dead field. |
| `minStackSize` | int | 3 | Used by AoE combat's stacking check (`handleAoeCombat`, `:3081-3084`) |

**Matching** (`SlayerProfileManager.findProfile`, `:484-507`): exact case-insensitive key match
first; else a **partial substring match in HashMap iteration order** (task name contains a
profile key, or vice versa) — the first entry found wins, and HashMap iteration order is
unspecified, so an ambiguous task name (one that's a substring of two different profile keys, or
vice versa) could in principle resolve inconsistently between JVM runs. This is a real fragility
next to my spec's typed-enum `TaskSpec` catalog keyed by exact lowercased game name (spec §6) —
worth naming explicitly in §9 as something to avoid.

Cannon is opted into per-task via the `cannon` boolean plus either a `cannonLocation` string or
a fallback lookup in the wholly separate `CannonSpot` enum (`shouldUseCannon`,
`:3783-3837`) — of the 42 default profiles, only **black demons**, **kalphite**, **suqahs**, and
**trolls** have `cannon=true` set by default (`:141,307,425,435`); the rest leave it off even
where a `CannonSpot` entry technically exists, presumably a deliberate default (many tasks are
multi-combat-unfriendly for cannon, or the author simply didn't enable it broadly).

**Finishing-blow items** (rock hammer for gargoyles, bag of salt for rockslugs, icy water for
lizards) are **not** part of the JSON profile at all — they're handled by a completely separate,
hardcoded mechanism keyed off the task name and the vanilla "autokill" varbits
(`getRequiredFinishingItemId`/`handleSlayerFinishingItems`, `:2300-2349`, detailed in §10),
checked against `npc.getHealthRatio() < 5` (lizards/rockslugs) or `< 3` (gargoyles) before using
the item on the low-health NPC. This is a clean, reusable pattern: **check the vanilla
autokill/auto-smash varbit first, and only carry/use the manual item if the player hasn't
unlocked the free auto-finish** (`SLAYER_AUTOKILL_DESERTLIZARDS`/`_ROCKSLUGS`/`_GARGOYLES`
varbits, imported by name only — no numeric ids visible in this file, `:10-12`).

Burst/barrage `goading` auto-forces itself on for those two styles (`:176-183`); AoE target
selection stacks NPCs within a 3×3 (burst, `aoeRange=1`) or 5×5 (barrage, `aoeRange=2`) box
centered on a candidate target and picks whichever candidate has the most other target-monsters
in range, only attacking once `maxStackedCount >= minStackSize` — otherwise it clicks a lone
target anyway if goading is enabled, purely "to trigger goading aggro" (`handleAoeCombat`,
`:3054-3184`), which is an interesting sequencing detail: **goading requires already being in
combat to work, so the script deliberately attacks a single monster first to bootstrap the
aggro-pulling effect**, rather than waiting passively for a natural stack.

---

## 6. Supplies, banking, restock, POH

**Banking triggers** (`needsBanking()`, `SlayerScript.java:3304-3360`), all threshold-based, no
dose/count distinction beyond what's listed:
- Food count (`Rs2Inventory.getInventoryFood().size()`) `< foodThreshold` (default 3).
- Prayer-potion doses (`getPrayerPotionDoses()`, sums parsed `(4)/(3)/(2)/(1)` suffixes across
  "prayer potion"/"super restore" items) `< potionThreshold` (default 4). **Note: only checks
  items whose name contains "prayer potion" or "super restore"** — sanfew serum and blighted
  super restore are read by `handlePotions()`'s drink logic (`:3373-3374` mentions them in a
  *different*, wider check used for actual drinking) but **not** by this specific bank-trigger
  count, a small inconsistency between "what counts as a prayer supply for banking" vs "what
  counts as a prayer supply to drink".
- Cannonball count `< cannonballThreshold` (default 100), only if cannon is active.
- Fungicide spray at 0 charges AND no refill fungicide in inventory (zygomite tasks).
- Finishing item (ice cooler/bag of salt/rock hammer) fully depleted (`itemQuantity == 0`).

There is **no loot-related banking trigger** — a full inventory of valuable drops does not, by
itself, force a bank trip in this plugin; looting simply stops when the inventory is full
(`handleLooting`, `:2558-2561`, "don't loot if inventory full unless we can eat for space") —
this is actually the *same* conclusion my spec reaches independently in §7.10 ("merely full →
looting pauses, the fight goes on"), a good corroboration that separating "critical supplies
low" from "just full of loot" is the right call.

**No streak/master-alternation interaction with banking** — not applicable, no such feature
exists here.

**Return-to-task after banking is not a separate leg/route concept** — `handleBankingState()`
simply walks to *the nearest bank* (`Rs2Bank.walkToBank()`, `:1381`) with no site-specific bank
choice, then after loading gear the state machine falls through to `TRAVELING` again, which
re-walks the full `taskDestination` from scratch (no memory of "I was mid-fight, resume from
there") — my spec's RESTOCK design (§7.5, clear target → escape teleport → re-derive TRAVEL) is
considerably more deliberate about this than Microbot's "just walk to nearest bank, then walk
back to the task tile" approach, which would be extremely slow for underground/instanced sites
(Stronghold, Catacombs, Slayer Tower basement) since Microbot has no equivalent of my "one bank,
reached via teleport hub" restriction — it assumes `Rs2Bank.walkToBank()` can path from
literally anywhere, leaning on its opaque walker.

**POH usage** (`SlayerConfig.java:192-253`, `SlayerScript.java:568-815`, `:1549-1801`):
- Rejuvenation pool restores **HP, Prayer, and Run energy** (`useRejuvenationPool()`,
  `:683-690`, tries 5 pool object ids in order — my CLAUDE.md/spec description of the account's
  pool as "restores prayer and run but NOT hitpoints" **conflicts with what this code assumes**
  — worth flagging explicitly: Microbot's plugin treats the pool as restoring all three,
  suggesting either a higher-tier pool (a "rejuvenation pool" proper, vs. the fountain/pool tier
  my house apparently has) or that this plugin's assumption is simply wrong for lower pool
  tiers. My spec's HP-comes-from-food-or-Ferox-pool design (§7.9) already correctly does NOT
  rely on the POH pool for HP, so this doesn't need a code change, just confirms the two
  reference sources genuinely disagree and my design took the conservative (correct-for-my-house)
  reading.
- Four teleport methods (`PohTeleportMethod.java`): `HOUSE_TAB` (inventory item "Teleport to
  house", action **"Break"**), `SPELL` (`MagicAction.TELEPORT_TO_HOUSE`), `CONSTRUCTION_CAPE`
  (item "Construct. cape"/"Construct. cape(t)", action **"Tele to POH"**), `MAX_CAPE` (item "Max
  cape", action **"Tele to POH"**) — with automatic fallback to `HOUSE_TAB` if the configured
  method's item/spell isn't available (`teleportToHouse()`, `:632-681`). No fairy ring or ornate
  jewellery box use for *entering* the house anywhere — not present (my spec's design routes
  through the POH itself as a fairy-ring/box hub, which is a materially different and more
  developed travel model than Microbot's "teleport straight to house, restore, then teleport back
  out via portal nexus/mounted glory/mounted wealth to a fixed exit point" pattern).
- **Leaving the house** (`leaveHouse()`, `:692-715`) is a priority cascade: portal nexus →
  **"Grand Exchange"** or **"Varrock Grand Exchange"** action; else mounted glory →
  **"Edgeville"**; else mounted ring of wealth → **"Grand Exchange"**; else fall back to the
  house portal's own **"Enter"** action (which presumably drops the player at the house's
  configured outside-entrance location, not a useful GE-adjacent spot) — always exits toward a
  bank-adjacent hub (GE/Edgeville), never toward the task. This is a genuinely different pattern
  from my spec's "POH is itself a fairy-ring/box hub that legs into task sites" (§6.2) — Microbot
  treats the house purely as a restoration pit-stop bookended by bank trips, never as a
  mid-journey waypoint toward a task site.
- Three usage points, all config-gated: **before banking** (`usePohBeforeBanking`, saves
  supplies by healing before spending food/potions, `:1933-1939`, `:2076-2082` — the "panic
  teleport" framing noted in §2), **after task** (`usePohAfterTask`, before getting the next
  task, `:1852-1858`, `:2013-2019`), and **HP-percentage-triggered mid-fight**
  (`pohRestoreThreshold`, default 50%, `needsPohRestoration()`, `:801-813`).
- **Spellbook swap via occult altar** happens inside the POH too, and is entirely orthogonal to
  the pool/rejuvenation logic — it's driven by the loaded `InventorySetup`'s own recorded
  spellbook (an Inventory Setups plugin feature), not by the task profile.

**Running out of supplies entirely**: `needsBanking()` just returns true again next tick, and
banking re-triggers; there is **no explicit "give up, this task cannot be supplied" safety stop**
anywhere I found — if the bank genuinely lacks what's needed, the loop would presumably spin on
`BANKING` forever trying to `loadInventory()`/`loadEquipment()` (or possibly proceed with a
"Failed to load inventory setup completely" warning log and no state change, `:1431-1433`,
which would also just retry next tick). This is a real robustness gap my spec's `RESTOCK_MAX_PER_TASK`
(4) and "bank has no earmuffs → safety stop naming it" (§7.5 step 5) directly and deliberately
close.

---

## 7. Loot

`LootStyle` (`LootStyle.java`): **MIXED** (item list + GE price, default), **ITEM_LIST** (custom
list only), **GE_PRICE_RANGE** (price only). Built on `Rs2LootEngine.Builder`
(`handleLooting()`, `SlayerScript.java:2552-2652`), which composes independent filter clauses
rather than a single predicate:
- Custom item-name list (`lootItemList`), added when style is `ITEM_LIST` or `MIXED`
  (`addCustomItemList`, `:2721-2743`) — exact match by default, `*` wildcard support converts to
  a regex (escaping `.()[]`  first, `matchesItemPattern`, `:2677-2697`).
- GE-value range (`addByValue()`), added when style is `GE_PRICE_RANGE`/`MIXED`, **or whenever
  `minLootValue > 0`** regardless of style (`:2601-2605`) — i.e. the min-value floor always
  applies even in pure `ITEM_LIST` mode, a detail easy to miss reading just the enum names.
- Bones: loot+bury (`addBones()`) or loot-only via a custom "contains bones, not bonemeal"
  filter (`addBonesWithoutBury`, `:2748-2755`).
- Ashes (`addAshes()`, presumably scatters, same as bury for bones).
- Coins: either `addCoins()` (all) or, if `minCoinStack > 0`, a custom filter requiring
  `name.equals("coins")` AND `quantity >= minCoinStack` (`:2619-2632`).
- Untradables (`addUntradables()` — clue scrolls, keys).
- Arrows: `addArrows(MIN_ARROW_STACK)` where `MIN_ARROW_STACK = 9` i.e. **10+ arrows**
  (`:2549,2640`).
- Runes: `addRunes(MIN_RUNE_STACK)` where `MIN_RUNE_STACK = 1` i.e. **2+ runes** (`:2550,2643`).

**Exclusion list applied as a final gate inside the loot-action callback itself**
(`isItemExcluded`, checked inside `withLootAction`, `:2586-2592`), not as a filter subtracted
from the other clauses — so an item can match the value/item-list/coins/etc. filters and still
be skipped at click-time if it's on `lootExcludeList`. Same wildcard-to-regex mechanism as the
include list.

**Gating**: `LOOT_DELAY_MS = 600` between loot passes (`:70,2553-2556`, one game tick);
`Rs2Inventory.isFull() && !eatForLootSpace` → skip entirely (no room-making beyond the
`eatForLootSpace` flag feeding into `LootingParameters.setEatFoodForSpace`, `:2559-2561,2582`);
`Rs2Combat.inCombat() && !forceLoot` → skip (`:2564-2566`). **There is no tiered "opportunistic
vs guaranteed" value threshold** like my spec's 50k/150k split (§7.10) — Microbot has exactly one
floor (`minLootValue`, default 1000) and no logic to force room by dropping a cheaper item or
eating specifically *because* of a high-value drop; `eatForLootSpace` is a blanket flag, not a
value-conditional decision. This confirms §9(a): the two-tier value-based room-making is
something my spec has that this reference implementation entirely lacks.

**Task-complete looting window**: 5 seconds of continued looting after the task is detected done
before transitioning states (`TASK_COMPLETE_LOOT_DELAY_MS`, §2) — a real, useful pattern worth
matching (or improving on with an event-driven confirm) since it stops last-kill drops being
abandoned.

**High alch** (`SlayerHighAlchScript.java`) is a **separate always-running script**, not part of
the main state machine, gated only by `enableHighAlch` and running regardless of `SlayerState`
(as long as logged in). Whitelist-only (empty whitelist = does nothing, `:54-56`), same
wildcard-pattern matching as loot lists, with an independent exclude list. Timing is
**randomized per-alch, not per-tick**: `MIN_TICKS`/`MAX_TICKS` computed from 30-45 real seconds
(`:35-36`), next interval drawn via `Rs2Random.nextInt(MIN_TICKS, MAX_TICKS, 1.5, true)` (a
weighted/skewed random, not uniform — the `1.5` and `true` args aren't explained in this file but
imply a non-uniform distribution, notable as a "humanized timing" precedent, conceptually similar
to my CLAUDE.md's own "timing drawn from a fitted human distribution" rule). Uses Explorer's Ring
free alchs first if charged, else the High Level Alchemy spell, with a **high-value-item warning
dialog handler** (`:96-102`, presses "1" through a "Proceed to cast..." confirmation) — this
entire subsystem is **out of scope for my design** since my account stays on the Lunar
spellbook specifically to keep NPC Contact available and High Level Alchemy is a
standard-spellbook spell (my spec §7.10 already reasons this exact conflict through
correctly — Microbot's plugin apparently assumes standard spellbook access throughout, with no
visible spellbook-conflict handling between alching and NPC-Contact-equivalent features, because
it doesn't use NPC Contact at all).

**Force loot / only-my-items / delayed looting** are all present as independent boolean toggles
feeding straight into `LootingParameters` (`:2573-2581`) with no further logic shown in this
file (the actual delay/ownership-check behaviour lives inside `Rs2LootEngine`, not vendored
here).

---

## 8. Combat details

**Prayer flicking** (`PrayerFlickStyle.java`, `combat/SlayerFlickerScript.java`): four non-OFF
modes — `ALWAYS_ON` (handled inline in `SlayerScript`, not the flicker script — just toggles the
profile's prayer on/off each relevant tick), `LAZY_FLICK` (flick 1 tick before the hit),
`PERFECT_LAZY_FLICK` (flick exactly on the hit tick), `MIXED_LAZY_FLICK` (randomly 0 or 1 ticks
before, `Rs2Random.betweenInclusive(0,1)`). The flicker script tracks each attacking NPC as a
`SlayerMonster` with a `lastAttack` countdown seeded from `Rs2NpcManager.getAttackSpeed(npc.getId())`
(default 4 ticks if unavailable, `DEFAULT_ATTACK_SPEED`), decremented every `onGameTick()`, reset
either when it reaches ≤0, when an animation/graphic change is observed, or forcibly on
`onPlayerHit()` (a `HitsplatApplied` "mine" hitsplat against an NPC the player is interacting
with, `SlayerPlugin.java:151-168`) — hit-based resync is the mechanism that keeps the flick timing
from drifting over a long fight. Prayer to flick **on** is either the profile's fixed prayer
(when set) or derived per-monster from `Rs2NpcManager.getAttackStyle(npc.getId())` string-matched
against "melee/crush/slash/stab" → `PROTECT_MELEE`, "magic" → `PROTECT_MAGIC`, "ranged" →
`PROTECT_RANGE` (`mapToAttackStyle`, `:228-250`) — i.e. it **can** flick different prayers for
different simultaneous attackers if no profile prayer overrides it, a level of per-NPC
sophistication my spec's flat per-task `Gear.worn`/single protection-prayer model doesn't
attempt (my spec assumes one protection prayer per task, which the `SlayerCombatStyle`/`SlayerPrayer`
profile-level design here matches for the *common* case, but the flicker script's fallback shows
Microbot considered mixed-attack-style monsters as a real case worth handling automatically).

**AOE dodging** (`combat/SlayerDodgeScript.java`) is narrowly scoped: only projectiles whose
`getTargetActor() == null` (i.e., targeting a ground tile, not a specific actor — genuine AOE
attacks like dragon poison pools) are tracked, added via `ProjectileMoved`
(`SlayerPlugin.java:170-183`). Every 200 ms tick (`:35,68`): expire projectiles past
`getEndCycle()`; if any tracked projectile's target point is within 2 tiles of the player,
search an 11×11 grid (`searchRadius = 5`) for the *closest reachable* tile that's ≥2 tiles from
**every** dangerous point (`Rs2Tile.isTileReachable`), then walk there. This is a pure
reactive dodge with no prediction of where the projectile is *going* — it only reacts once
already in the blast radius of a landed/landing projectile, and no attempt to pre-empt by reading
projectile source/velocity.

**Superiors**: a hardcoded 25-name `Set<String>` (`SUPERIOR_MONSTERS`,
`SlayerScript.java:127-157`, each entry commented with its base-monster mapping, listed in full
in §10) — checked first, before any other targeting logic, if `prioritizeSuperiors` is on
(`findNearbySuperior()`, `:3287-3299`, nearest one within `attackRadius + 5`). My spec (§6.2)
explicitly does **not** special-case superiors ("the 2026-08-05 superior spec was never
implemented") — this list is directly reusable if that decision is ever revisited, since it's the
full name set with base-monster provenance in the comments.

**Targeting priority cascade** (`handleCombat()`, `:2757-2930`, non-AoE path): superiors (if
enabled) → already-interacting-with-a-live-target (do nothing, let it finish) →
being-attacked-but-not-retaliating (find the attacker in the target list, else *any* attacker as
a fallback **only if within `attackRadius` of `taskDestination`** — a deliberate guard against
retaliating against an unrelated monster far from the task site) → proactive search for
attackable target-list NPCs within `attackRadius` of `taskDestination`, sorted first by
"already interacting with me" then by distance, attacking the top of that sorted list. **No
explicit safespot logic in the melee-vs-ranged sense** — not present; "safespot" only appears as
free-text in `SlayerLocation`'s comments (e.g. "Aberrant spectres... safespots" implied by
location choice) and as the entire *reason* `AttackRadius`/`CannonSpot` points were pre-selected
by a human, not as runtime logic that picks or verifies a safespot tile.

**Crash detection / world hop** (`:2884-3012`): purely a "no attackable NPCs found" timer while
at the task location — `CRASH_HOP_DELAY_MS = 20000` (20s) before hopping,
`WORLD_HOP_COOLDOWN_MS = 60000` (60s) between hops. Target world: random pick from
`hopWorldList` if configured, else `Login.getRandomWorld(true, null)` (random members world).
Hop mechanics: `Microbot.hopToWorld(world)` → wait for a **"Switch World"** widget → press Space
→ wait for `GameState.HOPPING` then `LOGGED_IN` (`:2952-2958`) — useful confirmation of the
"Switch World" widget text and Space-to-confirm mechanism as a fact, though the actual hop call
itself is a Microbot API, not directly transferable.

**Death handling** (`SlayerPlugin.onActorDeath`, `:186-222`): local-player-only `ActorDeath` →
immediately `Rs2Prayer.disableAllPrayers()` → state reset to IDLE → **shut down every sub-script**
(main script, flicker, dodge, high-alch) *before* logging out ("so they don't interfere with
logout", `:207`) → `Rs2Player.logout()` → `Microbot.showMessage(...)`. No gravestone timer
management beyond the logout itself, no attempt to recover items — matches the README's stated
limitation. This ordering (stop everything, then logout, then notify) is a clean pattern worth
matching in spirit even though my design's panic/safety-stop path is architecturally different
(CLAUDE.md: panic is a hard `os._exit` that cannot send key-ups, a materially different
constraint from a script-based logout).

**Stuck detection / timeouts**: scattered per-mechanism, no unified "stuck" concept —
`MAX_SKIP_ATTEMPTS`/`MAX_BLOCK_ATTEMPTS` (10 each), `MAX_SPELLBOOK_SWAP_ATTEMPTS` (5), the 20s
crash-hop timer, and a blind cannon-assembly sleep (6-7s guess, no confirmation read) are the
only timeout-shaped constants in the file. **No general "no progress for N ticks → safety stop"
watchdog** — not present, unlike my spec's `NO_TARGET_TICKS`/`NO_TARGET_WALKS` (§7.7) or
`RESTOCK_MAX_PER_TASK` (§7.5).

**Break handler integration** (`updateBreakHandlerLock`, `:3393-3411`): locks (disallows) breaks
in every state **except `BANKING` and `TRAVELING`**. Note: **this contradicts the README**, which
claims breaks are allowed during "Idle, At Location" (README.md:55, "only allows breaks during
safe states (Idle, At Location)") — the actual code checks `BANKING`/`TRAVELING`, not
`IDLE`/`AT_LOCATION`. Worth flagging as a documentation/code mismatch in the reference project
itself, not something to copy either version of blindly — my spec's `before_break` hook
(CLAUDE.md's routine contract) is a materially different, more deliberate mechanism (close open
UI, let the routine's own phase derivation decide safety) than a static per-state allowlist.

---

## 9. Lessons for my design

### (a) Things Microbot handles that my spec does not mention at all — ranked by importance

1. **Boss-task ("Like a Boss") detection and refusal.** A chatbox-widget check
   (`162.42`, text containing "how many would you like to slay"/"hunt") that would otherwise
   silently stall the routine on a free-text number-entry dialogue. My spec has no mention of
   this at all. **Where it belongs**: spec §7.2/§9 (NPC Contact), as an explicit "if this dialogue
   appears, safety-stop naming it" check alongside the existing "unknown options menu → ESC +
   failure" rule (§9 step 4) — this is the same *class* of failure (an options-menu shape the
   routine cannot safely guess through) and should use the same guard.
2. **A cannon "left behind" reconciliation check at BANKING.** If the routine ever teleports away
   from a task mid-fight (POH panic-teleport, escape ring in my RESTOCK design) while a cannon
   would be placed, Microbot explicitly checks for and recovers a cannon still standing nearby at
   the bank, else logs a warning and resets state rather than getting stuck
   (`SlayerScript.java:1346-1359`). My spec has D3 "No cannon" (§14) so this is moot *today*, but
   if cannon support is ever added (a stated future sub-project), this reconciliation pattern
   should be designed in from the start rather than discovered live.
3. **Per-NPC attack-style-derived prayer flicking as a fallback**, not just a fixed per-task
   protection prayer (§8) — useful if a FIGHT task ever mixes attack styles (my spec assumes one
   protection prayer per task via `Gear`/profile, which is fine for most of the catalog, but a
   task like "elves" — noted in Microbot's own default profile as `prange` despite being a melee
   task, i.e. a genuinely mixed-style monster — is exactly the case this fallback exists for).
   Not urgent, but worth a note in §7.7/§8 that a mixed-style task may need this someday.
4. **The 5-second post-completion loot window** (§2/§7) — stops last-kill drops being abandoned
   the instant `count` hits 0. My spec's §7.7 completion detection (`varp 394 → 0` confirmed by
   `/slayer`) transitions straight to ACQUIRE with no equivalent grace period for the final kill's
   drop. Cheap to add: a short `wait_for_event`/timed loot pass between FIGHT-complete and ACQUIRE.
5. **A "cannot afford to skip or block an unwanted task" hard stop distinct from the normal safety
   stop** — Microbot forces a full logout in this specific case (`stopAndLogout()`), reasoning that
   continuing to try the task is worse than stopping entirely. My spec's §7.4 safety-stops but
   doesn't distinguish "temporarily out of points" from other stop causes; worth at minimum a
   distinct log message/reason code so a human skimming logs can tell "ran out of points" from
   "genuinely stuck", even if the actual behaviour (safety stop, no forced logout) stays as
   designed.

### (b) Places where Microbot's approach is simpler or more robust than mine — ranked

1. **Finishing items are gated by the vanilla autokill/auto-smash varbit, not just carried
   blindly.** `getRequiredFinishingItemId()` returns `-1` (don't need it) if the player has
   already unlocked the free auto-finish perk for that monster (`SlayerScript.java:2307-2321`).
   My spec's `TaskSpec.carry` for e.g. lizards/rockslugs/gargoyles should check the same
   varbits before packing the item — otherwise the routine would waste an inventory slot and bank
   trip on an item that's no longer needed once the unlock is bought. This is a clean, cheap
   robustness win worth adding to `supplies.py`'s `carry` resolution (spec §7.9.2).
2. **Loot-value filtering always applies the value floor even in `ITEM_LIST`-only mode**
   (`:2601-2605`) — a small but real UX robustness detail: a user who sets a custom item list
   but also leaves `minLootValue` non-zero probably wants *both* respected, not one silently
   overriding the other. My spec's §7.10 loot design (whitelist OR value OR herb/seed) already
   does this correctly (it's explicitly an OR, not an either/or by style), so no change needed —
   just a confirmation my design already avoided this trap.
3. **Click-by-visible-text rather than hardcoded child index** for the widget-based rewards
   interface (`Rs2Widget.clickWidget("Confirm", Optional.of(426), 0, false)`, §3) — more robust
   to a child-id renumbering across game updates than an index. My spec's §9 step 5 already plans
   to record exact child indices from `recorder.py`, which is fine for a first pass, but the
   fallback when a recorded index goes stale after an unrelated game update should be "search
   group 426 for a component whose text is 'Confirm'/'Cancel'/'Tasks'", not just re-recording.
   Worth a one-line addition to spec §9's `open_rewards` description.
4. **The POH-before-banking-as-panic-teleport sequencing** (§2, §6): explicitly skip the panic
   teleport on the very tick food was just eaten, giving the heal a chance to land first
   (`:2076`, "give it time to heal before teleporting"). My spec's RESTOCK trigger (§7.5, "fires
   at the panel's eatAt/restoreAt with nothing left to consume") is conceptually the same
   ordering (eat until truly out, then escape), but Microbot's explicit one-tick "just ate, don't
   panic yet" guard is a cheap, concrete refinement worth copying verbatim into `combat_core`'s
   RESTOCK trigger to avoid a wasted escape-teleport on a tick where eating alone would have
   sufficed.
5. **Superiors get a complete, provenance-commented 25-name list** (§8, full list in §10) —
   directly reusable data if the "superiors are not targeted specially" decision (spec §6.2) is
   ever revisited; no need to re-derive it from the wiki.

### (c) Things Microbot does that I should NOT copy — ranked, with reasons

1. **Every "click" mechanism is not transferable at all** — `Rs2Walker.walkTo`,
   `Rs2Inventory.interact`, `npc.click("Attack")`, `Rs2Bank.walkToBank()`, and the entire
   in-person-only master interaction model are Microbot's direct client-API calls
   (software-injected). Nothing about *how* Microbot clicks belongs anywhere near my design; only
   the *decisions* (what to click, in what order, confirmed by what) are citable, and I've
   filtered for that throughout this document.
2. **In-person-only task acquisition/skip/block, with no NPC Contact at all.** This is the single
   biggest structural gap between the two codebases (§3), and it's not something to adopt —
   walking to the master in person for every single task/skip/block is exactly the throughput and
   detection-risk cost NPC Contact exists to avoid. My spec's §9 design is strictly better here;
   Microbot simply predates or ignores the spell.
3. **Blind fixed-duration sleeps as confirmation** — the cannon-assembly "6-7 seconds, about 6
   seconds" comment-guess (`:1878`) is exactly the anti-pattern my CLAUDE.md's "Sub-tick data and
   clicking while moving" section (and the whole `await_outcome`/event-driven-outcomes design)
   was built to eliminate. Every wait in my design should confirm against a pushed event or a
   polled HTTP read, never a duration picked because it "usually works".
4. **Single global `minPointsToSkip`/`minPointsToBlock` reserves with no per-master or per-session
   tracking of "how many times have I skipped in a row".** Microbot's `MAX_SKIP_ATTEMPTS`
   (`:73`) counts *retry attempts within one skip action*, not *consecutive skipped tasks* — it
   has no equivalent of my spec's `maxSkipsPerTask`/three-in-a-row-safety-stop concept at all
   (§7.4). Don't copy Microbot's narrower interpretation; my spec's broader "three undoable rolls
   in a row means the catalog is too thin" guard is a real improvement Microbot lacks entirely
   and should be kept as designed.
5. **The `useSpecial`/`specialThreshold` JSON profile fields, which are dead code** (declared,
   parsed, documented in the README, but never read by any code path in `SlayerScript.java`,
   confirmed by grep, §5). A caution rather than a "don't copy the behaviour" item: don't assume
   documented Microbot config fields are actually load-bearing just because they're
   README-documented and JSON-serialized — always trace to an actual read site before trusting a
   fact sourced from a config schema alone. Same caution applies to `antipoison`/`antivenom`
   JSON fields, which are also effectively unused beyond a log line.
6. **A flat, single-point-per-location data model with no reachability testing.** `SlayerLocation`
   has no equivalent of my spec's "walk legs are same-plane and map-reachable from the previous
   landing" test (spec §11, `test_slayer_tasks.py`). Microbot gets away with this because its
   walker is a full, general-purpose pathfinder that doesn't need pre-validated routes; my
   worldmap-based router is not that general (CLAUDE.md's routing section), so my spec's stricter
   catalog validation is correct and should not be loosened to match Microbot's laxer model.

---

## 10. Reusable facts

| Fact | Value | Source |
|---|---|---|
| Slayer reward points varbit | `4068` | `SlayerScript.java:71` (`SLAYER_REWARD_POINTS_VARBIT`) — matches conventional public varbit id, corroborates using the same one in my spec |
| Skip cost | 30 points | `SlayerScript.java:72` (`SKIP_TASK_COST`) |
| Block cost | 100 points | `SlayerScript.java:78` (`BLOCK_TASK_COST`) |
| Slayer rewards interface widget group | **426** | `SlayerScript.java:819` (`SLAYER_REWARDS_GROUP_ID`) — matches my spec §7.4/§9's "interface 426" |
| Rewards interface button texts | "Tasks" (tab) → "Cancel" (skip) / "Block" (block) → "Confirm" | `SlayerScript.java:887-910`, `:1127-1150` |
| Boss-amount-input chatbox widget | `162.42` | `SlayerScript.java:442` |
| Boss-amount-input trigger text | contains "how many would you like to slay" or "how many would you like to hunt" | `SlayerScript.java:450-452` |
| "I need another assignment" dialogue option | exact string | `SlayerScript.java:387` |
| Post-task dismissal dialogue options | "Okay", "great" (partial match) | `SlayerScript.java:409-422` |
| World hop confirmation widget text | "Switch World" | `SlayerScript.java:2953` |
| World hop confirm key | Space | `SlayerScript.java:2954` |
| House portal object id | `4525` | `SlayerScript.java:84` |
| Rejuvenation pool object ids (tiers) | `29241, 29240, 29239, 29238, 29237` | `SlayerScript.java:85` |
| Portal nexus object ids (Basic/Marble/Gilded) | `33366, 33408, 33410` | `SlayerScript.java:87` |
| Mounted glory object ids | `13523, 13524, 13525, 13526` | `SlayerScript.java:89` |
| Mounted ring of wealth object id | `29156` | `SlayerScript.java:91` |
| Occult altar object ids (Basic/Marble/Gilded/Ancient) | `29149, 29150, 29151, 29152` | `SlayerScript.java:1550` |
| Occult altar right-click spellbook options | "Ancient Magicks", "Lunar", "Standard" | `SlayerScript.java:1747-1757` |
| Antifire varbit | `3981` | `SlayerScript.java:2355` |
| Super antifire varbit | `6101` | `SlayerScript.java:2356` |
| Poison/venom varp | `102` (0=none, 1-999999=poisoned, ≥1000000=venomed) | `SlayerScript.java:2214,2421,2461,2470` |
| Rigour-unlocked varbit | `5451` | `SlayerScript.java:3552` |
| Augury-unlocked varbit | `5452` | `SlayerScript.java:3553` |
| Arrival distance (task location) | 10 tiles | `SlayerScript.java:69` (`ARRIVAL_DISTANCE`) |
| Task-complete loot grace window | 5000 ms | `SlayerScript.java:120` |
| Loot pass cooldown | 600 ms | `SlayerScript.java:70` |
| Crash-hop delay (no targets) | 20000 ms | `SlayerScript.java:114` |
| World-hop cooldown | 60000 ms | `SlayerScript.java:115` |
| Cannon assembly wait (unconfirmed guess, not a read) | 6000-7000 ms | `SlayerScript.java:1878` |
| Min arrow stack to loot | 10 (constant is `9`, i.e. "9+" checked as `>=`, README says "10+") | `SlayerScript.java:2549` |
| Min rune stack to loot | 2 (constant is `1`) | `SlayerScript.java:2550` |
| Superior monster names (25, base-monster mapped) | Crushing hand, Chasm Crawler, Screaming banshee, Screaming twisted banshee, Giant rockslug, Cockathrice, Flaming pyrelord, Monstrous basilisk, Malevolent Mage, Insatiable Bloodveld, Insatiable mutated Bloodveld, Vitreous Jelly, Vitreous warped Jelly, Cave abomination, Abhorrent spectre, Repugnant spectre, Choke devil, King kurask, Marble gargoyle, Nechryarch, Greater abyssal demon, Night beast, Nuclear smoke devil, Shadow Wyrm, Guardian Drake, Colossal Hydra, Basilisk Sentinel | `SlayerScript.java:127-157` |
| Slayer master locations | Turael `(2931,3536,0)`; Spria `(2907,3324,0)`; Mazchna `(3510,3507,0)`; Vannaka `(3145,9914,0)`; Chaeldar `(2445,4431,0)`; Nieve/Steve `(2432,3423,0)`; Duradel/Kuradal `(2869,2982,1)` | `SlayerMaster.java:10-18` |
| Slayer Tower per-monster points (top floor, plane 2) | Gargoyles `(3442,3543,2)`; Nechryael `(3438,3558,2)`; Abyssal demons `(3420,3568,2)` | `SlayerLocation.java:33-35` |
| Slayer Tower basement (separate entry, not used by the 3 above) | `(3417,9932,0)` | `SlayerLocation.java:32` |
| Stronghold Slayer Cave sub-points | hub `(2431,9806,0)`; Bloodvelds `(2426,9820,0)`; Hellhounds `(2413,9785,0)`; Fire giants `(2392,9782,0)`; Aberrant spectres `(2453,9793,0)`; Abyssal demons `(2465,9772,0)`; Ankou `(2393,9806,0)` | `SlayerLocation.java:42-51` |
| Catacombs of Kourend sub-points | hub `(1666,10049,0)`; Dagannoth `(1674,9997,0)`; Dust devils `(1716,10032,0)` | `SlayerLocation.java:17-27` |
| Mos Le'Harmless cave horrors (two conflicting points in the same codebase) | `SlayerLocation`: `(3740,9373,0)`; `CannonSpot`: `(3785,9460,0)` | `SlayerLocation.java:129`; `CannonSpot.java:37` |
| Karamja monkeys / Great Conch gryphons | not present anywhere (`SlayerLocation`, `CannonSpot`, default profiles) | grep confirmed absent |
| Finishing items | Icy water (lizards, `ItemID.SLAYER_ICY_WATER`), Bag of salt (rockslugs), Rock hammer (gargoyles) | `SlayerScript.java:2313,2316,2319` |
| Finishing-item HP-ratio trigger | lizards/rockslugs: `healthRatio < 5`; gargoyles: `healthRatio < 3` | `SlayerScript.java:2337,2341,2345` |
| Default supply thresholds | food `<3`, prayer-restore doses `<4`, cannonballs `<100` | `SlayerConfig.java:174-187,416-418` |
| Default loot value floor | 1000 gp | `SlayerConfig.java:493-495` |
| Cannon setup interaction | "Cannon base" → "Set-up"; pickup: "Dwarf multicannon" → "Pick-up" | `SlayerScript.java:4010,4054,4078` |
| Cannon parts required (name check) | "Cannon base", "Cannon stand", "Cannon barrels", "Cannon furnace" | `SlayerScript.java:3913-3916` |
| Prayer-potion dose parsing | suffix `(4)/(3)/(2)/(1)` on "prayer potion"/"super restore" item names | `SlayerScript.java:3365-3387` |
| Fungicide charge parsing | last whitespace-split token of the item name, e.g. "Fungicide spray 5" → `5` | `SlayerScript.java:2290-2298` |

---

## Summary of what was NOT found (explicitly, per the task's "if something is absent say 'not
present'" rule)

- No NPC Contact spell usage anywhere (§3).
- No master-alternation / streak-bonus / "filler vs milestone master" concept (§1, §2).
- No raw varp/varbit reads for task count, task id, or streak — hidden behind `Rs2Slayer` (§2).
- No entrance-object (door/ladder/stairs/trapdoor) modelling — `SlayerLocation` points are
  sometimes already inside dungeons, trusted entirely to Microbot's own general walker (§4).
- No Karamja monkeys or Great Conch gryphons data anywhere (§4).
- No tiered (opportunistic/guaranteed) loot value logic — one flat `minLootValue` floor (§7).
- No unified "stuck"/no-progress watchdog beyond per-mechanism retry counters (§8).
- No death-recovery of any kind (README's own stated limitation, confirmed in code, §8).
- Two JSON profile fields (`useSpecial`, `specialThreshold`) declared and documented but never
  read by any code path (§5, §9c).
