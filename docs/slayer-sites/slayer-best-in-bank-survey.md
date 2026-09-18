# Slayer Best in Bank — survey for reuse in BridgePlugin's slayer routine

Source: FreeArcanes/slayer-best-in-bank, BSD 2-Clause, cloned at installed commit `f05015a2`
into a scratch checkout (`sbib/`, temp scratchpad dir). 85 Java files under
`src/main/java/com/freearcanes/slayergear/`, 48 test files under `src/test/`, one data file
`src/main/resources/com/freearcanes/slayergear/slayer-targets.tsv`.

All file:line citations below are relative to that `sbib/` checkout's
`src/main/java/com/freearcanes/slayergear/<File>.java` unless stated otherwise.

---

## 1. What it does

**Inputs**: current Slayer task (name/location/amount, read from RuneLite's own `slayer` config
group: `SLAYER_CONFIG_GROUP="slayer"`, keys `taskName`/`taskLocation`/`amount`,
`SlayerGearAdvisorPlugin.java:70-73`), the account's bank/inventory/worn items (captured live via
`ItemContainerChanged`, never persisted — see §4), live skill levels and prayer/spellbook varbits
read directly off `Client` (`recalculate()`, `SlayerGearAdvisorPlugin.java:1156-1230`), and a large
block of user settings in `SlayerGearAdvisorConfig.java` (420 lines: objective, alternatives-per-
slot, pinned/excluded item name lists, Low-risk mode + cap, trip plan, food/prayer safety level,
boost/goading/bracelet toggles, travel-item preferences, panel theme, etc).

**Outputs**: a `GearRecommendations` object (`GearRecommendations.java`) rendered into a RuneLite
sidebar panel (`SlayerGearPanel.java`, 1967 lines) plus three overlays
(`BankRecommendationOverlay.java` — highlights bank items with tier-coloured boxes;
`DpsEstimateOverlay.java` — the expandable DPS/TTK card; `TaskPrepReminderOverlay.java` — a
"preparation missing" banner) and a filtered/tiered bank view (`TieredBankLayout.java`,
`BankAdvisorButton.java`) that repositions the actual bank widget so Tier-1 gear and supplies sit
in a fixed four-column zigzag. **It never withdraws, equips, or clicks anything itself** — this is
stated explicitly and repeatedly (README.md:147-150, VALIDATION.md:126-145's compliance review
finding "no automated gameplay actions"): the player still does every withdrawal/equip by hand,
guided by the highlighted view.

**"Objectives"** (`GearPriority.java`, `ObjectiveAdvisor.java`) are four named scoring weightings
the user picks from the panel — `MAX_DPS`, `PRAYER_SUSTAIN`, `DEFENCE_FIRST` (internal enum name;
displayed as "Defence First"), `VALUE`/"Value / Low Cost" (README.md:88-91) — that bias
`GearScorer`'s slot-candidate ranking (e.g. Prayer Sustain weights Prayer bonus and lets
`applyObjectiveQuantity` raise prayer-potion quantities; Value trims toward cheaper valid items).
`ObjectiveAdvisor.suggest(...)` can recommend one automatically from the selected combat method.

**"Strategies"** are `GearStrategy` instances (`GearStrategy.java`, immutable builder-based value
object): a named combat method for a task ("Catacombs barrage", "Golembane crush", "Leaf-bladed
melee", ...) carrying a `CombatStyle`, `AttackType`, optional `WeaponRule` (e.g. `DRAGONBANE`,
`GOLEMBANE`, `LEAF_BLADED`, `KALPHITE`, `DEMONBANE`), `TargetTrait`s, a required weapon/off-
hand/cape, a minimum equipped weight, an ordered `preferredItems` list (soft ranking nudges, not
hard requirements — see §5), and per-style defence/prayer weighting. A `SlayerTaskProfile`
(`SlayerTaskProfile.java`) is just a key + display name + summary + protection-advice string +
an ordered `List<GearStrategy>` — one profile per task, one-to-many with strategies (e.g.
Aberrant spectres has three: an elemental-magic method, a cannon+melee method, and a no-prayer
defensive-melee method). `GearScorer.eligibleStrategies`/`selectStrategy` pick which one applies
given owned items and levels, and the user can override the pick per task
(`queueSelectStrategy`).

**Slayer master / task coverage** (`SLAYER-MASTER-COVERAGE.md`, `COMBAT-TASK-COVERAGE.md`):
9 master families, 151 RuneLite-recognized task/boss names covered by the generic fallback
harness, 117 unique assignment categories audited for combat affinity. Confirmed from
`SLAYER-MASTER-COVERAGE.md:10-13` and `:45-48`:

- **Turael / Aya**: 24 assignments/categories (Banshees, Bats, Bears, Birds, Cave bugs, Cave
  crawlers, Cave slimes, Cows, Crawling hands, Dogs, Dwarves, Ghosts, Goblins, Icefiends,
  Kalphites, Lizards, Minotaurs, Monkeys, Rats, Scorpions, Skeletons, Spiders, Wolves, Zombies)
  — matches this project's Aya task-name list exactly.
- **Duradel / Kuradal**: 43 assignments/categories, matching this project's 43-row Kuradal list
  by name (the coverage doc lists them under "Duradel / Kuradal").

Both master lists are fully covered by name; per-task depth (whether a curated `SlayerTaskProfile`
exists vs. only a generic fallback) is assessed per task in §5.

**UI surface** (from README screenshots section + class list, not run live in this survey): a
sidebar panel with All/Missing/Gear/Supplies focus toggles, an expandable DPS/TTK card comparing
Tier 1/2/3, a settings screen grouped into Customization / Objectives / Trip planning / Teleport
routing / Appearance, Low-risk mode with a GP cap, Always-prefer/Never-recommend name lists,
versioned `SBIB1` copy/paste presets (`TaskPresetCodec.java`), and a Discord support icon
(`SupportLinks.java`) opened only via `net.runelite.client.util.LinkBrowser` on a direct click
(no background requests — VALIDATION.md:120-125, :142-145).

## 2. Architecture map

All 85 files live flat in `src/main/java/com/freearcanes/slayergear/` (no sub-packages). Line
counts are `Get-Content | Measure-Object -Line` on the cloned checkout. **PURE** = no
`net.runelite.client.*`, no `javax.swing`, no `java.awt`, no `net.runelite.api.Client`/`Widget`
import (checked by grepping every file's `import` block, not by reading each one in full).
**GAME-DATA** = touches `net.runelite.client.game.ItemManager`/`ItemStats`/`ItemEquipmentStats`
only (RuneLite's item-definition/stat lookup service — needed for gear scoring, not a UI or a
live `Client` handle). **UI/CLIENT** = Swing, AWT, `Client`, `Widget`, or Guice-injected plugin
lifecycle.

| Class | Lines | Responsibility | Coupling |
|---|---|---|---|
| SlayerGearAdvisorPlugin | 1522 | RuneLite `Plugin` entry point: event subscriptions, orchestrates the whole recalculation pipeline (§3), owns all mutable session state | UI/CLIENT (Swing, AWT, Client, Guice) |
| SlayerGearPanel | 1967 | Sidebar Swing panel rendering `GearRecommendations` | UI/CLIENT (Swing) |
| GearScorer | 2612 | Core scoring engine: builds candidate equipment per slot, coherent whole-loadout tiers, weapon/target-affinity math, DPS/offense estimates | GAME-DATA (ItemManager/ItemStats/ItemEquipmentStats) |
| TaskProfiles | 1154 | Static registry of ~117 `SlayerTaskProfile`/`GearStrategy` task definitions (the task knowledge base) | PURE |
| SmartSupplyAdvisor | 1199 | Builds the list of `SupplyRule`s per task/strategy and estimates quantities (food, potions, boosts, cannon ammo, finishers) | GAME-DATA + `ConfigManager` |
| TieredBankLayout | 373 | Repositions the live bank widget into a fixed four-column zigzag for Tier‑1 gear/supplies | UI/CLIENT (Client, Widget) |
| TaskCombatCatalog | 472 | Per-task-category combat-affinity audit data backing `COMBAT-TASK-COVERAGE.md` (weapon rule, melee affinity, traits, element) | PURE |
| TravelItemAdvisor | 383 | Maps assigned Slayer area / task location to recommended (advisory, not routed) travel items | PURE |
| SlayerGearAdvisorConfig | 420 | RuneLite `@ConfigGroup` — every user-facing setting | UI/CLIENT (`Config` annotations import `net.runelite.client.config`) |
| WeaponCombatRules | 300 | Per-weapon-name target-specific accuracy/damage multiplier logic (Golembane, Demonbane, Ratbane, Shade, etc.) | PURE |
| LoadoutOffenseEstimator | 267 | Converts a built loadout + `ItemEquipmentStats` into a DPS/offense number | GAME-DATA (ItemEquipmentStats) |
| PotionDoseEstimator | 268 | Duration/encounter-aware potion dose math (§6) | PURE |
| TripCostEstimator | 253 | GE-price-based trip cost / GP-per-kill estimate | GAME-DATA (ItemStats, ItemManager) |
| InventoryCapacityPlanner | 197 | Trims optional supplies to fit 28 slots (§6) | PURE |
| SlayerAreaCatalog | 187 | Named Slayer-area location table used by travel + supply rules | PURE |
| SlayerComboBoxUI | 205 | Custom Swing combo-box renderer | UI/CLIENT (Swing) |
| PanelTheme | 186 | Colour/theme constants for the panel | AWT only (`java.awt.Color`), no Swing/Client |
| BankRecommendationOverlay | 186 | Draws tier-coloured boxes over bank item widgets | UI/CLIENT (Client, AWT, RuneLite overlay API) |
| TaskConsumptionTracker | 189 | Session-only observed supply-consumption tracking between bank visits | GAME-DATA (ItemManager) |
| TaskPrepReminderOverlay | 148 | "Preparation missing" reminder overlay/chat message | UI/CLIENT (Client, AWT) |
| SupplyRecommendation | 163 | Value object: one recommended supply line (item id/name, category, status, quantities) | PURE |
| CannonTaskCatalog | 327 | Cannon-permitted-location catalog backing `CANNON-TASK-COVERAGE.md` | PURE |
| SlayerMasterCatalog | 284 | Per-master assignment tables (weight/category) backing `SLAYER-MASTER-COVERAGE.md` | PURE |
| GearRecommendations | 284 | Immutable value object: the whole pipeline's output (§3/§8) | PURE |
| TargetDefenceCatalog | 142 | Looks up `TargetDefence` rows (loaded from `slayer-targets.tsv`) for DPS estimation | PURE (reads a bundled resource, no client) |
| TargetDefence | 112 | Value object: one monster's defence/style/HP row from the TSV | PURE |
| TaskSafetyRules | 132 | **Hard** required-gear rules per task keyword (earmuffs, mirror shield, facemask, witchwood icon, boots of stone, ...) — §5 | PURE |
| TripPreparationState | 112 | Session state: what's been withdrawn/consumed this trip | PURE |
| CombatLevelContext | 85 | Value object: boosted skill levels + prayer multipliers fed into offense math | PURE |
| GearRequirement | 80 | Value object: one hard requirement (slot + acceptable item-name tokens) | PURE |
| EnchantedBoltEffects | 96 | Enchanted-bolt special-effect damage modelling | PURE |
| RangedAmmoPolicy | 131 | Weapon/ammo compatibility + ranged charge-state usability rules | PURE |
| InventorySetupBankTagState | 98 | Bridges to the Bank Tags plugin's Inventory Setups service | UI/CLIENT (`BankTagsService`) |
| ReadinessReport | 85 | Value object: readiness/missing-item summary | PURE |
| SlayerTaskHeadgear | 29 | Slot-specific headgear alias helper | PURE |
| ObjectiveAdvisor | 87 | Suggests/compares the four `GearPriority` objectives for a method | PURE |
| MethodComparisonAdvisor | 102 | Builds the "compare owned methods" panel data | GAME-DATA (via GearScorer calls) |
| OwnedMethodComparison | 73 | Value object: one method-comparison row | PURE |
| BossSlayerCatalog | 87 | Names classified as boss-style Slayer assignments | PURE |
| MagicCombatMethod | 81 | Magic spell/staff combat-method modelling | PURE |
| GearRequirement (see above) | | | |
| GearPriority | 18 | Enum: MAX_DPS / PRAYER_SUSTAIN / DEFENCE_FIRST / VALUE | PURE |
| GearStrategy | 174 | Value object: one combat method definition (§1/§5) | PURE |
| LoadoutTier | 47 | Value object: one coherent Tier 1/2/3 loadout | PURE |
| LoadoutCombatEffects | 122 | Set/passive-effect modelling (e.g. Ancestral +3%/piece) | PURE |
| LoadoutOffenseEstimate | 79 | Value object: a DPS/TTK estimate result | PURE |
| NameMatcher | 39 | Item/task name normalisation (lower-case, punctuation strip) | PURE |
| ElementalAmuletEffect | 39 | Elemental amulet +2 base max-hit modelling | PURE |
| ElementalWeakness | 15 | Enum: Air/Water/Earth/Fire | PURE |
| EquipmentChargePolicy | 68 | Name-suffix-based usable/broken/degraded classification (§4) | PURE |
| TwistedBowEffect | 28 | Twisted bow accuracy/damage-scaling-by-magic modelling | PURE |
| SupplyLevel | 17 | Enum: Light(0.65)/Normal(1.0)/Extra(1.4) multiplier | PURE |
| SupplyStatus | 16 | Enum: MISSING/PACKED/BANKED/PACKED_BANKED | PURE |
| SupplyQuantityAction | 7 | Enum: decrease/auto/increase | PURE |
| AttackType | 8 | Enum: STAB/SLASH/CRUSH/BALANCED | PURE |
| CombatStyle | 7 | Enum: MELEE/RANGED/MAGIC | PURE |
| TargetTrait | 21 | Enum: GOLEM/ARAXXOR/SCYTHE_TWO_HIT/... | PURE |
| WeaponRule | 13 | Enum: DRAGONBANE/GOLEMBANE/DEMONBANE/LEAF_BLADED/KALPHITE/... | PURE |
| AliasCatalog\<T\> | 76 | Generic ordered alias→value registry with collision policy (§5) | PURE |
| TaskCompletionSummary | 24 | Value object: end-of-task chat summary data | PURE |
| TaskPresetCodec | 59 | `SBIB1` preset string encode/decode | PURE |
| InventoryCapacityPlan | 53 | Value object: capacity-planning result | PURE |
| TripCostEstimate | 22 | Value object: one cost-estimate line | PURE |
| TripPlan | 11 | Enum: FULL_ASSIGNMENT/SHORT_TRIP/CUSTOM_KILLS | PURE |
| ObjectiveLoadoutComparison | 13 | Value object: one objective-comparison row | PURE |
| PrepFocusMode | 22 | Enum: ALL/MISSING/GEAR/SUPPLIES | PURE |
| PotionEstimationContext | 21 | Value object: inputs to `PotionDoseEstimator` | PURE |
| PluginRelease / PluginUpdateNotice | 42 / 14 | Version-check display data | PURE |
| SupportLinks | 13 | Discord URL constant + `LinkBrowser.browse` call | UI/CLIENT (`LinkBrowser`) |
| SlayerBraceletPreference, FairyRingPreference, HomeTeleportPreference, KourendTeleportPreference, PrayerRemainsPreference, PrayerRestorePreference, SlayerRingPreference, SpellTeleportPreference, AraxxorSwitchPreference | 16–20 each | Config-enum preferences for travel/supply choices | PURE |
| BankFlowState | 133 | Bank-open/lock/refresh session state machine | PURE |
| BankViewSearchFilter | 22 | Bank search-string predicate for the filtered view | PURE |
| GearRecommendations (see above) | | | |
| BankAdvisorButton | 72 | Injects a button widget into the live bank interface | UI/CLIENT (Client, Widget) |
| DpsEstimateOverlay | 108 | Renders the DPS/TTK overlay card | UI/CLIENT (Client, AWT) |

**The dependency-light core** is everything marked PURE or GAME-DATA — roughly 60 of the 85
files, including all of the task knowledge (`TaskProfiles`, `TaskCombatCatalog`,
`TaskSafetyRules`, `SlayerAreaCatalog`, `SlayerMasterCatalog`, `CannonTaskCatalog`,
`AliasCatalog`, `TuraelSpeedProfiles`), all supply math (`SmartSupplyAdvisor`,
`PotionDoseEstimator`, `InventoryCapacityPlanner`, `TravelItemAdvisor`, `RangedAmmoPolicy`,
`EquipmentChargePolicy`), and the scoring engine itself (`GearScorer`,
`LoadoutOffenseEstimator`, `WeaponCombatRules`, `TargetDefenceCatalog`). The GAME-DATA classes
(`GearScorer`, `SmartSupplyAdvisor`, `TaskConsumptionTracker`, `TripCostEstimator`,
`LoadoutOffenseEstimator`) need only `ItemManager`/`ItemStats`/`ItemEquipmentStats` — RuneLite's
item-definition service, obtainable from a live or a headless `Client`/plugin-test harness, not
a UI. Only ~15 files are genuinely UI/client-bound: the `Plugin` subclass itself, the panel, the
three overlays, `TieredBankLayout`, `BankAdvisorButton`, `SlayerComboBoxUI`, the `@ConfigGroup`
interface, `InventorySetupBankTagState`, and `SupportLinks`.

## 3. The recommendation pipeline

**There is no headless entry point.** The pipeline is a private method,
`SlayerGearAdvisorPlugin.recalculate(boolean rebuildBankView)`
(`SlayerGearAdvisorPlugin.java:1090-1218`), called only from inside the live `Plugin` (on every
game tick via `refreshTask(false)` → `onGameTick`, on every bank/inventory/worn
`ItemContainerChanged`, on config change, on bank widget open/close). A headless caller cannot
invoke "the pipeline" as a unit; it would have to call `GearScorer.score(...)` directly (package-
private, but same-package access is trivial) and supply every one of `recalculate()`'s live-
client reads itself. Trace, end to end:

1. **Task identity** — read from RuneLite's own `slayer` config group, not from the plugin's own
   state: `configManager.getConfiguration("slayer", "taskName"/"taskLocation"/"amount")`
   (`onRuneScapeProfileChanged`/`refreshTask`, not shown above but referenced at
   `SlayerGearAdvisorPlugin.java:70-73`). This is exactly the varp-backed task the vanilla
   RuneLite client already exposes — equivalent to this project's own `GET /slayer`.
2. **Task knowledge lookup** — `TaskProfiles.find(effectiveTaskName, effectiveTaskLocation,
   turaelAyaSpeedMode) -> Optional<SlayerTaskProfile>` (`recalculate()` line 1108-1111). Uses
   `AliasCatalog` normalisation (`TaskProfiles.java:9`) so name variants ("banshee"/"banshees")
   resolve to the same profile. `Optional.empty()` → `GearRecommendations.unsupported(...)`, the
   `UNSUPPORTED_TASK` state — a task name RuneLite reports that the catalog does not recognise at
   all (distinct from "recognised but no curated profile", which still gets a profile via the
   fallback harness described in §5).
3. **Ownership snapshot** — `lastBankItems`/`lastInventoryItems`/`lastWornItems` (`Item[]` fields
   populated only by `ItemContainerChanged`, see §4). If `lastBankItems == null` (bank never
   opened this session) → `GearRecommendations.openBank(...)`, the `OPEN_BANK` state. Otherwise
   three pools are combined (`combineGearPool`, `SlayerGearAdvisorPlugin.java:1136-1155`):
   `scoringPool` (bank+inventory+worn+any loaded Dizana's-quiver ammo — the full ownership set to
   score against), `scoringBankItems` (bank-only, for "is this in the bank" status), and
   `livePackedItems`/`packedSupplyItems` (inventory+worn, what's already carried).
4. **Live account facts** collected directly from `Client` at call time, not cached anywhere:
   `client.getRealSkillLevel(MAGIC/RANGED/ATTACK/STRENGTH)`,
   `client.getVarbitValue(VarbitID.KOUREND_DIARY_ELITE_COMPLETE)`,
   `client.getVarbitValue(VarbitID.SPELLBOOK)` (compared `== 1` for Ancient, `== 3` for Arceuus),
   whether a Dizana's quiver has ammo loaded (`snapshotLoadedQuiverAmmo()`), and a
   `CombatLevelContext` built from every active `Prayer` varbit times its known multiplier table
   (`meleeAttackPrayerMultiplier()` etc., `SlayerGearAdvisorPlugin.java:1233-1279` — e.g. Piety
   ×1.20 attack/×1.23 strength, hardcoded, not read from any RuneLite API for the multiplier
   value itself) plus the Kandarin hard diary varbit.
5. **Scoring** — `GearScorer.score(taskName, taskAmount, assignedLocation, profile, scoringPool,
   scoringBankItems, livePackedItems, packedSupplyItems, alternativesPerSlot, magicLevel,
   rangedLevel, kourendEliteComplete, ancientSpellbookActive, preferredStrategy, gearPriority,
   pinnedItems, excludedItems, lowRiskMode, riskCapGp, loadedDizanasQuiver,
   arceuusSpellbookActive, attackLevel, strengthLevel, combatLevels)` — the final, most-specific
   overload (`GearScorer.java:199-208`; four thinner overloads above it default the trailing
   args for older call sites/tests). Inside (`GearScorer.java:210-260+`):
   - canonicalises bank/packed item ids (`itemManager.canonicalize`, so a `(4)`-dose potion and a
     `(1)`-dose potion of the same base item are recognised as the same ownership fact where
     relevant) and builds `List<BankEquipment>` — one row per owned equipment item with its
     `ItemStats`/`ItemEquipmentStats` pulled via `ItemManager`;
   - `eligibleStrategies(profile, ownedNames, magicLevel, rangedLevel)` filters the profile's
     `GearStrategy` list to ones the account can actually run (level/name gates), then
     `selectStrategy` picks the best (or the user's `preferredStrategy` override, a per-task
     config string keyed `strategyKey(effectiveTaskName)`);
   - `TaskSafetyRules.gearRequirements(profile.getKey() + " " + taskName, selected,
     kourendEliteComplete)` builds the **hard** `GearRequirement` list (§5);
   - `buildCandidates(...)` ranks owned items per `EquipmentInventorySlot` against the strategy's
     weapon rule/target traits/preferred-item nudges, `gearPriority`, pinned/excluded name lists,
     and Low-risk mode's GP cap;
   - `buildCoherentLoadouts(...)` assembles 1–3 **whole-loadout** tiers (not independent per-slot
     top-picks — a two-handed weapon pick removes the off-hand slot, an ammo-slot ranged weapon
     rebuilds compatible ammo, Tier 2/3 are described as swaps from Tier 1);
   - `TargetDefenceCatalog.find(taskName, profile.getKey(), selected)` pulls the monster's
     defence/style row (from `slayer-targets.tsv`, §5) to rank loadouts by
     `averageOffense(...)` when the objective is Balanced/Value.
6. **Supplies** — folded into the same `GearScorer.score()` call implicitly via
   `packedSupplyItems`, but the actual supply list comes from a separate call chain:
   `SmartSupplyAdvisor.recommend(profile, strategy, assignedLocation, taskAmount, bankItems,
   packedItems, allowRadasBlessing, objective, potionContext) -> List<SupplyRecommendation>`
   (§6) — invoked from inside `GearScorer`/`MethodComparisonAdvisor`, not shown directly in
   `recalculate()` but wired through the injected `supplyAdvisor` field (`GearScorer.java:61,
   65-70`).
7. **Method comparison** (optional, for the expandable DPS panel) —
   `gearScorer.compareOwnedMethods(scored, taskName, ..., plannedKills)` re-runs scoring for every
   eligible strategy so the sidebar can show Tier 1/2/3 across methods.
8. **Capacity planning** — `InventoryCapacityPlanner.apply(scored, lastInventoryItems,
   bankPlanLocked, bankRefreshPending) -> GearRecommendations` (§6) trims optional supplies to
   fit 28 slots and produces the final object assigned to `recommendations`.
9. **Render** — `panel.display(recommendations)` (Swing) plus, if the bank's filtered view is
   active, `queueBankViewRefresh()` (drives `TieredBankLayout`).

**Entry point for a headless caller**: none exists as shipped. The closest thing is
`GearScorer.score(...)` itself (step 5) — it takes no `Client` reference and no Guice-injected
UI dependency, only `ItemManager`/`SmartSupplyAdvisor`/`SlayerGearAdvisorConfig` (the last only
used for a couple of preference reads inside `SmartSupplyAdvisor`, and `GearScorer` has a
config-less constructor overload, `GearScorer.java:73-76`, apparently kept for exactly this kind
of standalone use — it is used by `GearScorerTest`, not confirmed further here). Everything
`recalculate()` computes *before* calling `score()` — the live skill levels, varbits, and prayer
multiplier table — would have to be reproduced by the caller from this project's own bridge
endpoints (`/skills` for levels, a `/api/player/magic.spellbook` equivalent already exists per
CLAUDE.md, and the Prayer varbit list would need a small hardcoded multiplier table copied from
`SlayerGearAdvisorPlugin.java:1233-1279`, itself trivial and not RuneLite-API-dependent once
copied).

**What it returns**: `GearRecommendations` (immutable, `GearRecommendations.java:10-37` — see §2
table): a `State` enum (`NO_TASK`/`UNSUPPORTED_TASK`/`OPEN_BANK`/`READY`), the resolved
`SlayerTaskProfile` and selected/alternative `GearStrategy`, a per-slot map of ranked
`GearRecommendation`s (`GearRecommendation.java:5-89`: item id/canonical id/name, slot, numeric
score, rank, two-handed flag, weight, a human-readable reason string, and `packed`/`banked`
booleans), a `List<LoadoutTier>` (the coherent Tier 1/2/3 whole loadouts), a separate weapon-
switches list, the `List<SupplyRecommendation>` (§6), a `ReadinessReport`, the count of bank
items actually checked, the list of masters that can assign this task, the active `GearPriority`
objective, objective-comparison rows, and an `InventoryCapacityPlan`. None of this is exposed as
JSON or over any protocol — it is a plain in-memory Java object consumed directly by the Swing
panel.

## 4. How it knows what the account owns

**Capture mechanism**: a single `@Subscribe onItemContainerChanged(ItemContainerChanged event)`
handler (`SlayerGearAdvisorPlugin.java:363-389`) switches on `event.getContainerId()` and clones
the container's `Item[]` into one of three plain (non-volatile, non-persisted) instance fields:

```java
case InventoryID.BANK:
    lastBankItems = event.getItemContainer().getItems().clone();
    break;
case InventoryID.INV:
    lastInventoryItems = event.getItemContainer().getItems().clone();
    break;
case InventoryID.WORN:
    lastWornItems = event.getItemContainer().getItems().clone();
    break;
```

`lastBankItems` is also seeded eagerly the moment the bank widget loads
(`onWidgetLoaded`, `InterfaceID.BANKMAIN`, lines 401-424) by reading
`client.getItemContainer(InventoryID.BANK)` directly on the client thread, so the very first
open of a session captures state without waiting for a subsequent change event.

**No persistence whatsoever.** There is no file write, no `ConfigManager.setConfiguration` call,
no database — `lastBankItems`/`lastInventoryItems`/`lastWornItems` are ordinary Java fields.
Confirmed by grepping the whole file for `lastBankItems =` (5 occurrences, all in-memory
assignments) and for `Gson`/`Files`/persistence-adjacent calls (none). They are explicitly reset
to `null`/empty:
- On `shutDown()` (plugin disabled): `lastBankItems = null; lastInventoryItems = EMPTY_ITEMS;
  lastWornItems = EMPTY_ITEMS;` (`SlayerGearAdvisorPlugin.java:279-281`).
- On `GameStateChanged` to `HOPPING` or `LOGIN_SCREEN`: same reset, with the comment "Never
  carry bank/inventory/task state across accounts or worlds" (`:301-309`).

**Consequence**: the plugin has no memory of the bank across a client restart, a world hop, or a
logout — every session must open the bank at least once before any task-specific recommendation
is possible (`recalculate()`'s `lastBankItems == null` branch returns the `OPEN_BANK` state,
matching README.md:159 "A bank scan is required before account-specific recommendations are
possible"). **This is the single largest capability gap relative to this project's own
`BankMemoryProvider`/`GET /bank/memory`**, which persists to
`~/.runelite/bridge-bank-memory/<accountHash>.json`, self-heals on every open/withdraw/deposit,
and answers correctly *before* the first bank visit of a session (see CLAUDE.md's "Bank memory"
section). SBIB's bank knowledge is strictly session-scoped and interface-dependent — closer in
spirit to this project's `/bank` (which also only reports what the widget is currently drawing)
than to `/bank/memory`.

**Placeholders**: not specially filtered anywhere found in `SlayerGearAdvisorPlugin.java` or
`GearScorer.java` — no `quantity == 0` or `canonicalize(id) != id` check was found guarding
`lastBankItems`/`collectEquipment`. This project's `BankSave.fromCurrentBank`-derived
`BankSnapshot` (CLAUDE.md's bank-memory section) explicitly filters placeholders and `BANK_FILLER`;
SBIB appears to rely on `ItemContainer.getItems()` already excluding zero-quantity/placeholder
slots the way the bank interface itself would for a *displayed* (non-search, non-tab-hidden) view
— this was not verified against a live client in this survey and should be treated as unconfirmed
rather than "SBIB has no placeholder bug," since `BankProvider`'s own doc comment (referenced in
CLAUDE.md's "Bank memory" section) says the *displayed* bank items ARE what a raw container read
gives you, placeholders included as 0-quantity entries, unless explicitly filtered — SBIB's own
`collectOwnedItems`/`collectEquipment` (`GearScorer.java`, `SmartSupplyAdvisor.java`) were not
traced far enough in this pass to state definitively either way; flagged as an open question,
not a confirmed finding.

**Charge-state handling — "conservative charge-state filtering"** (RELEASE-NOTES.md:32-34) is
implemented as **name-suffix pattern matching, not numeric charge tracking**:
`EquipmentChargePolicy.isUsable(String itemName)` (`EquipmentChargePolicy.java:8-24`) returns
`false` for names containing `(broken)`, `dormant`, `depleted`, `(damaged)`, or — for a curated
list of charge-sensitive items only (`chargeStateControlsModeledPerformance`, lines 44-52:
Scythe of vitur, Tumeken's shadow, Sanguinesti staff, tridents, toxic blowpipe/staff, serpentine
helm, the four wilderness weapon pairs, crystal bow/armour pieces, Bow of faerdhinen, Venator
bow, Tonalztics of ralos, Dizana's quiver) — `(uncharged)`/`uncharged `/`(u)`/`(inactive)`/
`(empty)`. Fully-degraded Barrows pieces are caught separately by a `" 0"` trailing-number regex
(`isFullyDegradedBarrows`, lines 60-63). `RangedAmmoPolicy.isUsableWeapon` (`RangedAmmoPolicy.
java:8-20`) duplicates a similar name-based check specifically for ranged weapons (blowpipe
(empty), craw's/webweaver bow (u), venator bow variants (uncharged), crystal bow/Bow of
faerdhinen (inactive)). **This is binary usable/not-usable, never a remaining-charge count** —
there is no equivalent anywhere in the 85 files of parsing `Slayer ring (3)` down to the integer
`3`, comparing it against other copies, or deciding "swap in a fresh one at ≤1 charge left" the
way this project's planned `supplies.charges()`/`supplies.teleports_left()` (spec §7.9) does.
`EquipmentChargePolicy.note(itemName)` (lines 26-42) instead returns a static string,
`"charge-dependent; verify charges before leaving"`, for the same curated item list plus all
Barrows equipment — i.e. SBIB tells the player *which* items need a manual charge check, but
performs no charge arithmetic itself. Charged items relevant to this project's use case (slayer
rings, blowpipe, trident) are all on the "usable if not visibly empty/uncharged" list, so an
account with a slayer ring on its last charge (name still reads `Slayer ring (1)`, no `(u)`
suffix) would be reported as fully usable by SBIB with no warning that it is about to run out —
confirmed by the absence of any digit-extraction regex in `EquipmentChargePolicy.java` or
`RangedAmmoPolicy.java`.

## 5. Task knowledge

### `slayer-targets.tsv` (`src/main/resources/com/freearcanes/slayergear/slayer-targets.tsv`)

1296 lines. Header comment block (lines 1-3):

```
# OSRS Wiki DPS calculator monster data; CC BY-NC-SA 3.0
# Source: https://github.com/weirdgloop/osrs-dps-calc/blob/main/cdn/json/monsters.json
# Generated: 2026-08-24
```

Column header (line 4) and 5 sample rows (lines 5-11, one skipped for brevity):

```
name	version	defence	magic_level	stab	slash	crush	magic	light	standard	heavy	hitpoints	magic_attack	size	attributes	burn_immune
A corpse		90	1	0	0	0	0	0	0	0	90	0	1		false
Aberrant spectre		90	105	20	20	20	0	15	-15	15	90	0	2	undead,spectral	false
Abhorrent spectre		180	300	40	40	40	0	30	-30	30	250	0	3	undead,spectral	false
Abyssal demon	Catacombs of Kourend	135	1	20	20	20	0	20	20	20	150	0	1	demon	false
Abyssal demon	Standard	135	1	20	20	20	0	20	20	20	150	0	1	demon	false
```

`version` distinguishes location/phase variants of the same monster (e.g. three separate Abyssal
demon rows for Catacombs/Standard/Wilderness Slayer Cave, each with identical stats here but kept
separate for variants that do differ). This is **pure monster combat-stat data** (defence level,
per-style defence bonuses, HP, size, attribute tags) used by `TargetDefenceCatalog`/
`LoadoutOffenseEstimator` for the DPS/TTK estimates — it carries no gear requirements, sites, or
travel information at all, and is licensed **CC BY-NC-SA 3.0** by its own header, not the
project's BSD-2-Clause (see §9's licensing implication).

### The catalog classes

- **`TaskProfiles`** (1154 lines): the static registry mapping a normalized task key (via a
  shared `AliasCatalog<SlayerTaskProfile>`, `TaskProfiles.java:9`) to a `SlayerTaskProfile` — a
  display name, a plain-English summary, a plain-English protection-advice string, and an
  ordered `List<GearStrategy>` (one or more named combat methods, each with location, combat
  style/attack type, weapon rule, target traits, required weapon/off-hand/cape, minimum
  equipped weight, and a soft `preferredItems` ranking list — see §1). Registered under multiple
  aliases per task (`"basilisks", "basilisk"`) so singular/plural both resolve.
- **`TaskCombatCatalog`** (472 lines): the data behind `COMBAT-TASK-COVERAGE.md` — per-category
  weapon-family rule (Golembane/Demonbane/Dragonbane/Kalphite/Leafbane/Ratbane/Shade/Vampyre/
  Wilderness), melee affinity, `TargetTrait`s, and Standard-spellbook elemental weakness. This is
  the audit table, kept deliberately separate from `TaskProfiles`'s executable strategies — the
  coverage doc's own text (COMBAT-TASK-COVERAGE.md:5) says variants with conflicting weaknesses
  are left neutral rather than forced to one answer (e.g. Birds, Bats, Bears, Spiders,
  Spiritual creatures, Fossil Island wyverns).
- **`TaskSafetyRules`** (132 lines, quoted in full at §4/§5-table sources): the **hard**
  required-gear gate, keyed by a `taskKey.contains(...)` substring match against
  `profile.getKey() + " " + taskName` (`TaskSafetyRules.java:13-92`). This is the only class that
  produces a blocking `GearRequirement` — every other "required X" mentioned in a profile's
  advisory text or a strategy's `preferredItems` is a **soft ranking nudge**, not an enforced
  gate. Confirmed by direct inspection (§5 table below): several items decisions.md treats as
  required (fungicide spray, tortugan shield, light source) are not in this file at all.
- **`TuraelSpeedProfiles`** (127 lines): a second, independent set of Turael/Aya location picks
  (`TuraelSpeedProfiles.java:14-40`+) used only when the user enables "Turael/Aya speed mode" in
  settings — e.g. Dogs → "East of Sophanem", Monkeys → "Ardougne Zoo", Minotaurs → "Stronghold of
  Security - first level". **These disagree with the Turael Skipping plugin registry this
  project's own `decisions.md` adopted as source of truth** (Dogs blocked entirely at Aya on this
  account; Monkeys sent to Karamja instead; Minotaurs reached via the ornate box → Cooking Guild
  route) — SBIB's speed-mode locations are a second, wiki-derived opinion, not verified against
  this account's blocks/preferences, and are a different data source than the plugin-registry
  dump `decisions.md` chose deliberately for exactly the reason CLAUDE.md's "the 231.6
  dialogue-widget lesson" gives (use the authoritative plugin's own data, not a reconstruction).
- **`SlayerAreaCatalog`** (187 lines): "RuneLite's authoritative Slayer-area helper names" mirroring
  game DB table 115 (`SlayerAreaCatalog.java:11-16`), each tagged with a `TravelFamily` enum
  (VARLAMORE, KOUREND, FAIRY_RING, DUELING, KARUULM, SLAYER_RING, GAMES_NECKLACE, KARAMJA,
  ARDOUGNE, TROLLHEIM, FREMENNIK, ELVEN, DRAKAN, DIGSITE, ...) driving `TravelItemAdvisor`'s
  advisory teleport-item suggestions (§1/§6) — not an executable route.
- **`SlayerMasterCatalog`** (284 lines): "Live Slayer-master/task relationship catalog used for
  UI context" (`SlayerMasterCatalog.java:14-18`), explicitly kept separate from the strategy
  engine — a task can use a curated profile or the generic fallback while `SlayerMasterCatalog`
  still reports every master able to assign it. Backs `SLAYER-MASTER-COVERAGE.md`.
- **`AliasCatalog<T>`** (76 lines, quoted in full above): the shared generic ordered
  alias→value registry every other catalog is built on, with an explicit `CollisionPolicy`
  (`KEEP_FIRST`/`REPLACE`) so a broad fallback alias cannot silently overwrite a curated
  registration — RELEASE-NOTES.md:174-176 calls this out ("curated task profiles retain priority
  over broad boss aliases") and VALIDATION.md:41 lists a regression test for it.
- **`CannonTaskCatalog`** (327 lines) — permitted/prohibited cannon locations, backing
  `CANNON-TASK-COVERAGE.md`'s 81 location-aware cannon routes; not traced further in this pass.

### The 22 FIGHT tasks from `decisions.md`, cross-checked against SBIB

"Hard-enforced" = present in `TaskSafetyRules.gearRequirements` (blocks scoring/flags missing) or
a `WeaponRule` that rejects incompatible weapons outright. "Soft" = only a `preferredItems` nudge
or advisory text — the player can proceed without it and SBIB will not flag it as missing.

| Task | SBIB hard-enforced item | SBIB recommended style / site | Safety rule text | vs. `decisions.md` |
|---|---|---|---|---|
| Aberrant spectres | HEAD: slayer helmet/slayer helm/nose peg | Air Magic (elemental); cannon+melee w/ Protect Magic alt; Slayer Tower / Stronghold Slayer Cave | "A Slayer helmet or nose peg is required" | Matches (nose peg/slayer helmet, PRAYER, Protect from Magic) |
| Abyssal demons | none | Catacombs barrage / Venator / Demonbane melee, Catacombs of Kourend | "Use Protect from Melee when stacking demons" | **Site differs**: decisions.md picked Slayer Tower basement (task-only teleport); SBIB's profile never mentions the Tower for this task |
| Ankou | none | Catacombs barrage / Venator / single-target melee, Catacombs of Kourend | "Prayer is optional at high defence" | **Site differs**: decisions.md picked Stronghold Slayer Cave; SBIB only lists Catacombs |
| Basilisks | SHIELD: mirror shield/v's shield | Protected Crush melee, Fremennik Slayer Dungeon / Jormungand's Prison | "A mirror shield or V's shield is required" | Matches closely |
| Black demons | none | Demonbane melee, Catacombs / Chasm of Fire; Ranged fallback | "Protect from Melee for ordinary black demons" | **Site differs**: decisions.md picked Taverley Dungeon, not mentioned in SBIB's profile at all |
| Bloodveld | none | Venator/melee (Meiyerditch Labs / Iorwerth); a separate no-prayer defensive variant IS at Stronghold Slayer Cave / Slayer Tower | "Protect from Melee prevents magic-based melee damage" | Partial match — decisions.md's Stronghold pick exists only as SBIB's *secondary* no-prayer alternative, not its default method |
| Cave horrors | AMULET: witchwood icon | Melee (slash), Mos Le'Harmless Caves, cannon-compatible | "Wear a witchwood icon for the recommended melee setup" | Matches on witchwood icon; **light source is only a soft suggested supply**, not hard-enforced; Cabin Fever quest prerequisite not modeled anywhere found |
| Dagannoth | none | Venator / cannon+melee at Lighthouse, Island of Stone, or Waterbirth Island Dungeon | "Protection choice depends on location" | **Site differs materially**: decisions.md's chosen Catacombs-of-Kourend melee-only variant (Horror from the Deep quest req) is **absent from SBIB's location list entirely** |
| Dust devils | HEAD: facemask/slayer helmet/slayer helm | Ancient Magicks barrage (primary) / Venator / melee fallback, Catacombs of Kourend | "A Slayer helmet or facemask is required" | Matches location and required item; SBIB's *default* method is Ancients, decisions.md picked plain melee (still listed as SBIB's fallback method at the same site) |
| Elves | none | Melee (slash), Prifddinas / Lletya | "Use protection prayers as needed" | Matches (Lletya is one of the two named sites); Regicide quest prerequisite not modeled |
| Fire giants | none | Water Magic (elemental, 100% weakness) / Venator / melee, Catacombs / Waterfall Dungeon | "Protection is usually optional" | **Site differs**: decisions.md picked Stronghold Slayer Cave (task-only per-group safespots); not in SBIB's list |
| Gargoyles | Required *supply*, not a `GearRequirement`: rock hammer/rock thrownhammer/granite hammer (`SmartSupplyAdvisor.java:444-445`) | Golembane Crush melee (`WeaponRule.GOLEMBANE`), Slayer Tower | "A Granite hammer doubles as the finishing hammer; otherwise keep a rock hammer..." | Matches; **decisions.md itself already notes SBIB's table only lists the Tower's top floor, not the better basement site** ("Corrections this pass found in existing data", decisions.md:185) — independent confirmation that SBIB's location text under-specifies |
| Gryphons | none (30 kg weight and tortugan shield are advisory text + soft `preferredItem`, not enforced) | Heavy melee + cannon (stab), Great Conch | "Aim for at least 30 kg worn weight; the superior/boss requires a tortugan shield" | Matches location and weight advice; tortugan-shield hard enforcement exists only for the **Shellbane Gryphon boss** (RELEASE-NOTES.md:83-85), not the ordinary task — consistent with decisions.md scoping it to "the superior/boss" too |
| Hellhounds | none | Venator (Catacombs) / Demonbane melee (Catacombs / Cerberus) | "Protect from Melee removes ordinary Hellhound damage" | **Site differs**: decisions.md picked Stronghold Slayer Cave; not in SBIB's list |
| Kalphite | none (poison protection is advisory text only) | Cannon + Keris/partisan Crush melee (`WeaponRule.KALPHITE`), task-only Kalphite Cave | "Poison protection is suggested for soldiers and guardians" | **Best match of all 22** — site (task-only Kalphite Cave) and weapon family agree exactly |
| Kurask | Hard via `WeaponRule.LEAF_BLADED` (rejects non-leaf-bladed weapons outright) | Leaf-bladed **Slash** melee, Fremennik Slayer Dungeon / Iorwerth Dungeon | "Leaf-bladed weapons, broad ammunition, or Slayer Dart are required" | Location matches; **decisions.md's own supply table labels this "crush melee"**, but both SBIB's `AttackType.SLASH` and the OSRS wiki (leaf-bladed sword/battleaxe are Slash weapons) disagree — flagged as a likely error in decisions.md, not in SBIB. Also, SBIB registers no separate ranged/broad-ammo strategy for this task despite its own advisory text naming broad ammo as valid |
| Mutated zygomites | none (fungicide spray is only a soft `preferredItem` + advisory text) | Melee (slash), Zanaris | "Keep fungicide spray available for finishing blows" | Site matches (Zanaris); **method differs** — decisions.md classifies this a SAFESPOT (ranged from a pillar) task while SBIB offers only a melee strategy; fungicide spray is not hard-enforced despite decisions.md treating it as a required carry item; Lost City quest prerequisite not modeled |
| Nechryael | none | Catacombs barrage (Ancient AoE) primary; Demonbane melee fallback, Slayer Tower / Catacombs | "Protect from Melee while stacking Greater Nechryaels" | **Meaningful contradiction**: this advisory text implies Protect from Melee handles Nechryael damage; it says nothing about the death-spawns-bypass-prayer fact `decisions.md` records as the USER's own live-verified reasoning for the PRAYER_PLUS_DAMAGE 15-food allocation. SBIB would not have prompted extra food here |
| Suqahs | none | Cannon+melee w/ Protect from Magic primary; no-prayer defensive melee alt, Lunar Isle | "Use Protect from Magic and solid melee defence" | decisions.md's south-west group is "melee only, single combat"; SBIB's generic profile assumes a Magic attack style requiring Protect from Magic — plausibly a sub-population difference, not a contradiction, but worth checking on the first live run |
| Trolls | none | Three separate profiles: mountain trolls (South of Mount Quidamortem), ice trolls stab/crush (Fremennik Isles / Trollweiss) | "Use protection prayers against high-damage ice trolls" | **Site differs**: decisions.md picked Death Plateau (a fourth, low-level troll spot); none of SBIB's three named locations is Death Plateau |
| Waterfiends | none | Earth Magic (100% weakness) / Crush melee w/ magic defence, Ancient Cavern | "They attack with two styles; defensive balance may help" | Site matches exactly (Ancient Cavern); **SBIB has no Ranged strategy for this task at all**, while decisions.md's chosen method is specifically a ranged across-the-river safespot (SAFESPOT profile) |
| Wyrms | BOOTS: boots of stone/boots of brimstone/granite boots, waived if Kourend elite diary complete | Melee/dragonbane (balanced) / dragonbane Ranged / Earth Magic+dragonbane, Karuulm Slayer Dungeon | "Boots of stone or a heat-protecting upgrade may be required in Karuulm" | Matches closely, including the elite-diary exemption logic |

**Pattern across the mismatches**: SBIB's location data comes from general Wiki "best/current
XP method" research (its own README/RELEASE-NOTES describe cross-checking the Wiki strategy
page per task), while `decisions.md`'s sites were chosen by the user for THIS account's specific
travel access (owned fairy ring codes, jewellery box, unlocked areas) and often favour a
lower-traffic/task-only variant over the wiki's "best XP" location. Half of the 22 tasks
(abyssal demons, ankou, black demons, dagannoth, fire giants, hellhounds, trolls) have SBIB
recommending a **different site** than the one this project chose — SBIB's task knowledge is
useful for the *hard-gear-requirement* and *weapon-family* facts (which agree well) but is not a
substitute for `decisions.md`'s own site research when the two diverge on location.

## 6. Supplies

### `SmartSupplyAdvisor` — the rule engine (1199 lines)

`recommend(profile, strategy, assignedLocation, taskAmount, bankItems, packedItems,
allowRadasBlessing, objective, potionContext) -> List<SupplyRecommendation>`
(`SmartSupplyAdvisor.java:104-214`) is the outer loop:

1. `buildRules(profile, strategy, assignedLocation, encounterName) -> List<SupplyRule>`
   (line 254+) assembles a **context-dependent rule list** — not a fixed per-task table. Boss
   thrall books/runes if `bossPvm && config.useBossThralls()`; Goading/Prayer-regen if the user's
   global toggles are on; an Expeditious-bracelet suggestion automatically when
   `TuraelSpeedProfiles.isSpeedStrategy(strategy)` is true (i.e. only for Turael/Aya-style fast
   tasks) or the user's own bracelet preference otherwise; combat/ranged/magic boost potions
   gated on which `CombatStyle`s the strategy actually uses, with the item preference order
   itself switched by `config.preferDivineBoosts()`; a `"Food"` rule is always added (line
   541-542, item name list: anglerfish, manta ray, dark crab, shark, cooked karambwan, sea
   turtle, monkfish); cannon-set parts are added separately when `isCannon(strategy)`
   (`addCannonSetRecommendations`); task-specific "Finisher"/protection rules (rock hammer for
   Gargoyles, ice coolers for desert lizards, a light-source suggestion for caves, antipoison
   options) are added by keyword match on `key = profileKey + " " + encounterKey`, mirroring
   `TaskSafetyRules`'s substring-match style but through `SupplyRule`s (soft, `showWhenMissing`)
   rather than blocking `GearRequirement`s.
2. For each rule, **quantity is computed per rule category**, not a flat per-task number:
   `PotionDoseEstimator.estimate(category, plannedKills, profile, strategy, potionContext,
   config)` → `applyObjectiveQuantity` (Objective-based bias, e.g. Prayer Sustain raises prayer
   potion counts) → `applySupplyLevel` (the Light/Normal/Extra global multiplier, §below) →
   `quantityOverride` (a **per-task, per-account** manual override the user can set from the
   panel's decrease/Auto/increase controls, persisted via `ConfigManager`).
3. Ownership is checked **twice, independently** — once against packed items (inventory+worn)
   and once against the bank — so a supply already carried but with more available in the bank
   still shows both `PACKED` and `BANKED` state (`SupplyStatus` enum: `MISSING`/`PACKED`/
   `BANKED`/`PACKED_BANKED`), specifically so the filtered-bank widget for that item doesn't
   disappear the instant one unit is packed (`SmartSupplyAdvisor.java:151-154` comment).

### `PotionDoseEstimator` — the dose math (268 lines, quoted in full above)

This is a genuine **drain-rate model**, not a lookup table, for ordinary (non-boss) tasks:

- **Prayer** (`prayerDoses`, lines 81-127): `pointsPerMinute` is 58.0 if the task's own
  `protectionAdvice` text contains "protect from"/"protection prayer"/"use prayer" (i.e. the
  task actually needs a protection prayer running) or it's a boss encounter, else 38.0 (ordinary
  drain from a non-protection prayer or none at all); `resistance = 60 + prayerBonus*2` (higher
  worn Prayer bonus directly reduces the estimate); `totalDrain = (duration/60) *
  pointsPerMinute * 60 / resistance`; `baseDoses = max(0, totalDrain - prayerLevel) / restore`
  (a full Prayer bar at trip start is credited before any potions are needed); `restore =
  prayerRestorePerDose(prayerLevel, superRestore) = prayerLevel/4 + (7 or 8)` (this is the
  actual OSRS Prayer-potion restore formula: `floor(level/4) + 7`, `+8` for Super restore, so the
  estimator is grounded in real game mechanics, not an arbitrary constant). Clamped to
  `[1, 32]` doses for ordinary tasks (`[1, 48]`/`[1, 64]` for calibrated bosses).
- **Duration** (`durationSeconds`, lines 146-171): `secondsPerKill * kills + downtime *
  (kills-1)`, where `secondsPerKill` comes from `context.getSecondsPerKill()` (a caller-supplied
  DPS-derived figure — not traced further in this pass) or a fallback of 7.0s for Ancient
  AoE/Venator methods, 18.0s otherwise.
- **Food** is **not** part of this drain model at all — it falls through `estimate()`'s
  `default:` case straight to `SmartSupplyAdvisor.recommendedQuantity("Food", kills) =
  clamp(2, 12, (kills+19)/20)` (`SmartSupplyAdvisor.java:867-868`) — **one food item per 20
  planned kills**, floored at 2 and capped at 12, with no encounter-specific "this monster hits
  harder" adjustment anywhere in the reviewed code. The only per-task food differentiation
  available is the global `config.foodSafety()` Light/Normal/Extra multiplier (§below) and the
  manual per-task override — there is no equivalent of this project's task-specific "nechryael
  needs 15, ankou needs 5" reasoning built into the automatic estimate.
- 33 named bosses (`buildBosses()`, lines 191-241) get individually calibrated
  `prayerDosesPerKill`/`maxKillsPerTrip`/`fallbackSecondsPerKill`/`downtimeSeconds` tuples (e.g.
  Araxxor 3.43 doses/kill capped at 10 kills/trip, Vorkath 2.13/6, Cerberus 2.00/8), plus two
  `fixed()` special cases (TzTok-Jad: 56 doses over a fixed 3600s; TzKal-Zuk: 40 doses over
  7200s) — **none of the 22 FIGHT tasks in `decisions.md` is a named boss here**, so all 22 use
  the generic drain-rate path, not a calibrated figure.

### `SupplyLevel` (17 lines, quoted in full)

```java
LIGHT("Light", 0.65), NORMAL("Normal", 1.0), EXTRA("Extra", 1.4)
```

A single global (per-category: Food uses `config.foodSafety()`, Prayer/Prayer-regen use
`config.prayerSafety()`, everything else is always NORMAL) multiplier applied to the automatic
quantity, then rounded up to the nearest whole unit (`applySupplyLevel`,
`SmartSupplyAdvisor.java:930-937`) — this is the *entire* mechanism for "bring more for a
dangerous task": the user manually sets Food/Prayer safety higher for the whole account, or
overrides one task's own quantity by hand. There is no automatic per-task danger classification
driving it.

### `InventoryCapacityPlanner` (197 lines, quoted in full above)

`apply(recommendations, inventoryItems, bankPlanLocked, bankRefreshPending) ->
GearRecommendations`: computes `occupiedSlots` (already-held items) + `pendingGearSlots`
(Tier‑1 items still in the bank) + `supplySlots` (every supply's `additionalSlots`, which
correctly treats a 4-dose-potion "doses" unit as needing only 1 inventory slot per 4 doses via
`getUnitsPerWithdrawal()`, and a stackable like cannon ammo as needing at most 1 slot regardless
of quantity). If `requestedSlots > 28`, it trims **optional, quantity-adjustable** supplies in a
fixed priority order (`trimPriority`, lines 191-214): **Food first** (priority 0), then Run
energy, Goading, Prayer regen, combat/ranged/magic boosts, **Prayer potions second-to-last**
(priority 5), Cannon ammo last before "everything else" (priority 7) — required supplies and
gear are never trimmed; any leftover overflow is surfaced as a warning, matching this project's
spec's stated intent ("Required supplies are never capacity-trimmed" — VALIDATION.md:116 and
RELEASE-NOTES.md:73-74 confirm this independently).

### `TravelItemAdvisor` / `RangedAmmoPolicy` / `EquipmentChargePolicy`

Already covered: `TravelItemAdvisor` (§5) is advisory-only, keyed off `SlayerAreaCatalog`/
location-keyword matching, config-driven preference order (home teleport, slayer ring variant,
Kourend teleport, fairy ring, spell teleport) — it never executes a route or checks charge
counts numerically. `RangedAmmoPolicy` (§4) governs weapon/ammo slot compatibility (bolt tiers,
named-weapon exceptions like Hunter's crossbow → kebbit bolts only) and a binary usable/not-
usable check for ranged weapons by name suffix. `EquipmentChargePolicy` (§4) is the same binary
name-suffix policy for general equipment.

### `TripCostEstimator` (253 lines) / `TaskConsumptionTracker` (189 lines)

`TripCostEstimator.estimate(recommendations, itemManager, plannedKills) -> TripCostEstimate`
sums `itemManager.getItemPrice(itemId) * recommendedQuantity` over every supply with a resolved
item id and a non-zero GE price, converting a "doses" quantity back to whole potions via
`SmartSupplyAdvisor.doseScore(name)` (parses the trailing `(N)` dose suffix) before pricing, plus
`addCombatConsumables` for worn ammo/cannon ammo/supported Magic casts (not traced further) —
this is a pure GP-estimate function, no execution. `TaskConsumptionTracker` is session-only
(never written to disk) and explicitly "never guesses from bank quantities"
(`TaskConsumptionTracker.java:11`) — it diffs observed packed-item quantities against a
recorded starting snapshot to report what was actually used, purely for the end-of-task chat
summary; it has no bearing on the recommendation pipeline itself.

### Comparison with this project's spec §7.9 four profiles

| | SBIB | This project's spec §7.9 |
|---|---|---|
| Prayer potions | Computed per-trip from a drain-rate formula (prayer bonus, protection-prayer-or-not, planned kill count, real restore-per-dose formula), clamped 1–32 doses | Four fixed profile constants: `PRAYER`=6, `PRAYER_PLUS_DAMAGE`=6, `SAFESPOT`=2, `TRIVIAL`=0 full potions, independent of prayer bonus or trip length |
| Food | `clamp(2,12,(kills+19)/20)` — trip-length-scaled only, no per-monster danger factor, then a global Light/Normal/Extra slider | Four fixed profile constants: `PRAYER`=5, `PRAYER_PLUS_DAMAGE`=15, `SAFESPOT`=8, `TRIVIAL`=3, **explicitly differentiated per task's actual danger** (nechryael's unavoidable death-spawn damage, kalphite poison) |
| Danger classification | None automatic; user must recognise "this task is dangerous" and raise the safety slider or override the one task manually | Baked into which of the 4 named profiles a task is assigned in `decisions.md`, from the user's own live combat knowledge |
| Grounding | Prayer side is genuinely well-founded — real restore-per-dose formula, prayer-bonus-aware resistance term, protection-prayer detection from the task's own advisory text | The two anchor numbers (nechryael 15 food, "5-6" prayer potions) are the user's own measured/informed judgement; every other entry in the table is explicitly labelled PROPOSED, not measured |

**Where SBIB's model is better-founded**: the **Prayer** dose estimate. It is not a flat
constant — it scales down automatically as worn Prayer bonus rises, uses the real in-game
Prayer-potion restore formula, and distinguishes "this task actually runs a protection prayer"
from "it doesn't" using the task's own advisory text, none of which this project's four flat
profile numbers do. If `supplies.py` (spec §7.9) is built, adopting SBIB's
`resistance = 60 + prayerBonus*2` / `restore = floor(level/4) + 7or8` formula pair (both
independently verifiable against the OSRS wiki's own published Prayer-potion mechanics, not
SBIB-specific data) would make the Prayer side self-adjusting to whatever gear a task's loadout
actually equips, rather than a hand-typed guess per task.

**Where this project's model is better-founded**: **Food**, specifically. SBIB has no
per-task danger signal for food at all — nechryael and ankou at the same planned-kill-count get
the *same* automatic food quantity, differentiated only by a global safety slider the user would
have to remember to raise before every dangerous task and lower again after. This project's
`decisions.md` already encodes the user's own live combat knowledge (nechryael's death spawns
hit through Protect from Melee; kalphite poison ignores prayer) directly into the per-task
profile choice, which is exactly the kind of fact SBIB's generic advisory text (§5's Nechryael
row) does not capture and would not have prompted extra food for.

## 7. Reuse options, evaluated

### Java version — confirmed, corrects the task's premise

**`build.gradle:34-40`** (quoted in full):

```groovy
tasks.withType(JavaCompile).configureEach {
	options.encoding = 'UTF-8'
	options.release.set(11)
}

tasks.named('compileJava', JavaCompile) {
	options.compilerArgs.addAll(['-Xlint:deprecation', '-Werror'])
}
```

`--release 11` is enforced by the compiler itself (not just a `sourceCompatibility` string that
could silently drift), and README.md:180-183 states it directly: "This repository follows
RuneLite's standalone external-plugin structure and **targets Java 11 bytecode**."
VALIDATION.md:25-31 and RELEASE-NOTES.md:192 both restate "Main source compiles for Java 11...
with deprecation warnings treated as errors" as the release gate. **No file in this checkout —
README.md, RELEASE-NOTES.md, VALIDATION.md, `build.gradle`, or the two `.github/workflows/*`
files — mentions Java 21 anywhere** (checked by literal string search for `"21"` across all
three docs; zero matches). The task brief's premise that these docs mention Java 21 does not
hold for the actual `f05015a2` checkout surveyed here — possibly a different plugin/version was
being recalled, or this is stale information. Either way: **there is no Java-version mismatch to
resolve.** `--release 11` matches this project's own Java 11 build exactly.

Confirmed by direct source search, not just by trusting the `--release 11` flag: **zero**
occurrences across all 85 files of records (`record \w+\(`), text blocks (`"""`), `var` in a
lambda parameter (`(var `), `Stream.toList()`, or pattern-matching `instanceof` (`instanceof
Foo f`). One classic `switch` statement was found (`GearScorer.java:2302-2304`,
`switch (strategy.getCombatStyle()) { case MAGIC: ... }`) and it uses colon/`case`-label syntax,
not a `->` switch expression. The codebase is written in a deliberately conservative,
Java-8-compatible style throughout (explicit `Optional`, builder patterns instead of records,
enums instead of sealed interfaces) — consistent with genuinely targeting Java 11 rather than
targeting 21 and merely down-compiling.

### Option A — vendor the whole source byte-identical, one adapter provider

**What it needs**: copy all 85 files into
`src/main/java/net/runelite/client/plugins/slayergear/` (or keep the original
`com.freearcanes.slayergear` package, matching the `m8aq` precedent of never renaming a vendored
package), add a `VENDORED.md` per §9, and write one `SlayerLoadoutProvider.java` adapter class
exposing `GET /slayer/loadout?task=<name>` (§8).

**What breaks**: the ~15 UI/client-bound files (§2 table) would compile but never actually run
usefully headless — `SlayerGearAdvisorPlugin` itself (the only class with `@PluginDescriptor`,
the Guice injection graph, and all the event subscriptions that populate
`lastBankItems`/`lastInventoryItems`/`lastWornItems`) would either have to be instantiated as a
real RuneLite plugin (meaning it runs inside the SAME client this project already loads plugins
into, alongside ZombieLite, and reads/writes its own Swing panel and bank-widget-repositioning
side effects that this project has no use for and did not ask for) or the adapter would have to
reimplement `SlayerGearAdvisorPlugin`'s ~250 lines of state-capture/orchestration logic itself,
which defeats the "vendor byte-identical, never edit" premise the whole point of vendoring is
supposed to preserve. **This is the crux of Option A's problem**: `m8aq`'s `ReaderCatalog`
readers are pure `static State getState(Client)` functions with no plugin lifecycle of their own
(CLAUDE.md's m8aq section) — SBIB's actual recommendation logic is NOT structured that way. It
lives behind a stateful `Plugin` subclass that only updates its three item-array fields in
response to live RuneLite events, so getting a recommendation still requires either (a) running
the whole plugin live and reading its private `recommendations` field via reflection (Option D,
worse), or (b) driving `GearScorer.score(...)` directly with self-supplied inputs (this is
already most of the work Option B/C would require anyway).

**Adapter size**: small if (b) is chosen — the adapter itself is maybe 150-250 lines translating
this project's own `/skills`, `/equipment`, `/inventory`, `/bank/memory`, and a small
Prayer-varbit table (copied once from `SlayerGearAdvisorPlugin.java:1233-1279`, itself pure
arithmetic, no RuneLite import needed after copying the numbers) into `GearScorer.score(...)`'s
26-argument call. But at that point the vendored ~2600-line `GearScorer` plus its ~10 pure
supporting classes (`TaskSafetyRules`, `GearStrategy`, `WeaponCombatRules`, etc.) are doing all
the real work and the other ~75 files (panel, overlays, config, bank-layout, the `Plugin`
subclass itself) are dead weight that still has to be kept in sync on every upstream pull to
satisfy "byte-identical, never edited."

**Risks**: (1) `SlayerGearAdvisorConfig` is a RuneLite `@ConfigGroup` **interface** (not a class)
— RuneLite's Guice `ConfigManager` normally supplies a dynamic proxy implementation; a headless
caller must either register the plugin properly (heavyweight) or implement the interface itself
with a plain class of getters (`config.gearPriority()` etc. — RELEASE-NOTES.md's config surface
is at least 20 settings, and several classes take `SlayerGearAdvisorConfig` as a constructor
dependency: `GearScorer`, `SmartSupplyAdvisor`, `TravelItemAdvisor` uses it statically). (2) The
license/attribution burden is minor (BSD-2-Clause, §9) but the **bundled `slayer-targets.tsv` is
separately CC BY-NC-SA 3.0** (§5, §9) — a non-commercial share-alike licence distinct from the
plugin's own BSD terms, which must be tracked separately in any `VENDORED.md` if this file is
carried along. (3) Every upstream pull re-touches all 85 files' worth of diff-review overhead
for a project that only wants ~10 of them. (4) No Swing/reflection/native/subprocess risk per
VALIDATION.md's own compliance review (§1) — this part is clean.

### Option B — vendor only the pure core + data

Vendor the ~35-40 files marked PURE that actually hold task/combat/supply knowledge:
`TaskProfiles`, `TaskCombatCatalog`, `TaskSafetyRules`, `SlayerAreaCatalog`, `SlayerMasterCatalog`,
`CannonTaskCatalog`, `AliasCatalog`, `TuraelSpeedProfiles`, `GearStrategy`, `GearRequirement`,
`WeaponRule`/`WeaponCombatRules`, `TargetTrait`/`TargetDefence`/`TargetDefenceCatalog`
(+ `slayer-targets.tsv`), `AttackType`/`CombatStyle`/`ElementalWeakness`, `SupplyLevel`,
`SupplyStatus`, `TripPlan`, `PotionDoseEstimator`, `NameMatcher`, plus the GAME-DATA classes
(`GearScorer`, `SmartSupplyAdvisor`, `TripCostEstimator`, `LoadoutOffenseEstimator`,
`TaskConsumptionTracker`) if the ItemManager dependency is acceptable inside this project's own
`BridgeHttpServer` (it already runs inside the same live RuneLite client, so `ItemManager` is
freely available via the same Guice injector this project's own providers use).

**What has to be written**: the ~150-250 line adapter from Option A, PLUS a small
hand-implemented `SlayerGearAdvisorConfig`-equivalent (or refactor the ~15 vendored PURE/GAME-DATA
classes' constructors to drop the config dependency where it's genuinely optional — several
already tolerate `null`, e.g. `GearScorer`'s and `SmartSupplyAdvisor`'s config-less overloads).
**What breaks on upstream updates**: still whole-file replacement for the ~40 vendored files
(same "never edit, sync script overwrites" discipline as `m8aq`), but the excluded UI files can
never accidentally get vendored back in by a careless sync. **Size**: comparable adapter to
Option A, roughly 40 files instead of 85 to track. **Risk**: picking exactly the right file set
requires re-verifying the "PURE"/"GAME-DATA" cut in §2 after every meaningful upstream feature
add (a new class could straddle the line, e.g. adding a UI-only helper method to an otherwise
pure class) — this is the main structural risk of a partial vendor, absent in Option A's simpler
"copy everything" rule.

### Option C — port only the data tables to Python

Reimplement `TaskProfiles`'s ~117 task→strategy entries, `TaskSafetyRules`'s ~15 hard-requirement
rules, and the four `SmartSupplyAdvisor`/`PotionDoseEstimator` formulas (§6) as plain Python data
structures and functions in this project's own `slayer_tasks.py`/`supplies.py` (already planned
per the design spec, §6/§7.9). **What has to be written**: a genuine reimplementation, by hand,
of the task table (§5) and the Prayer drain-rate formula (§6) — not a mechanical port, since
Java `GearStrategy`/`GearRequirement` builder chains don't translate 1:1 to whatever shape
`TaskSpec`/`Supplies` already take in the design spec. **What breaks on upstream updates**:
nothing automatically — this is a one-time research input, like the wiki pages and Turael
Skipping registry dump `decisions.md` already used, not a maintained dependency; a future SBIB
release with corrected task data would need the same manual re-read this survey just did.
**Size**: no adapter at all — this becomes ordinary Python authored directly into the existing
catalog modules. **Risk**: none of Options A/B/D's Java-interop or licence-tracking risk, but the
CC BY-NC-SA 3.0 `slayer-targets.tsv` data (monster defence stats) would need to be read as
*research* to write independent facts, not copied verbatim into a redistributed file, if this
project intends to keep its own licensing simple (its `docs/slayer-sites/` and `worldmap.py`
etc. carry no stated licence of their own currently, so introducing a share-alike-encumbered
data file would be a new, avoidable complication — see §9).

### Option D — run the installed plugin as-is, read state by reflection

**What it needs**: the Plugin Hub build of SBIB installed and enabled in the SAME client this
project's bridge runs in (`ExamplePluginTest.main`'s client, per CLAUDE.md's "Running things"),
then `BridgeHttpServer` reflectively reaches into the running `SlayerGearAdvisorPlugin` instance's
private `recommendations` field (`GearRecommendations currentRecommendations()`,
`SlayerGearAdvisorPlugin.java:295-298`, package-private — actually already an accessible,
intentional-looking test hook, not even a reflection hack, IF the adapter lives in the same
package `com.freearcanes.slayergear`) to read the latest `GearRecommendations`.

**What breaks**: this makes the routine's gear/supply decisions depend on **SBIB's own bank-open
requirement** (§4) — the routine would have to synthetically open the bank widget (or the account
would need to already have it open) before every recommendation is fresh, since SBIB captures
bank contents only from live `ItemContainerChanged` events, never from this project's own
persisted `BankMemoryProvider`. It also inherits every UI side effect SBIB performs unconditionally
(bank widget repositioning via `TieredBankLayout`, overlay drawing, a Swing panel) that this
project's headless bridge would never otherwise trigger, and depends on Plugin Hub's own update
cadence/availability rather than a pinned commit this project controls. **Size**: smallest
adapter of all four (no vendoring, just one reflective/package-private read), but the ongoing
coupling to a live UI plugin's internal timing (does `recommendations` reflect the CURRENT task,
or a stale one mid-recalculation?) makes correctness harder to reason about than a pure function
call. **Risk**: highest of the four — no control over upstream updates at all (Plugin Hub can
update or remove the plugin at any time), and `currentRecommendations()`'s field is `private`
with only a **package-private accessor method**, so it is not part of any published contract and
could be removed/renamed without notice in a point release.

### Recommendation

**Option C** (port only the data tables + formulas to Python) fits this project's actual need
best. The reasons:

1. This project's own `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` already
   commits to a **pure, unit-tested Python catalog** (`slayer_tasks.py`, §6 of that spec) driven
   by a bridge that already has everything SBIB's pipeline struggles to get cheaply — persisted
   bank memory (`/bank/memory`, no bank-open requirement, §4), `/skills`, `/equipment`, and a new
   `GET /slayer`. SBIB's hardest problem (getting bank/inventory/worn state reliably without a
   live bank-open event, §4) is **already solved better** by this project's own bridge than by
   anything SBIB offers — there is nothing to gain by importing SBIB's weaker capture model.
2. The genuinely reusable content — which items are hard-required per task (§5's table), which
   weapon families get which passive bonus, and the Prayer-potion drain-rate formula (§6) — is
   small, stable, and already cross-checked against the OSRS Wiki by SBIB's own authors
   (COMBAT-TASK-COVERAGE.md:153-167 lists its Wiki sources). Reading it once to write independent
   Python facts costs far less than standing up and maintaining a Java-interop adapter (Options
   A/B) or a live-plugin dependency (Option D) for **22 tasks only used by this account's own
   routine**, most of which SBIB (§5) already gets a different site for anyway — meaning even a
   perfect vendor of SBIB's task catalog would give the WRONG site for over a third of the 22
   FIGHT tasks, since `decisions.md`'s sites were chosen for this account's specific travel
   access, not the Wiki's "best XP" pick SBIB encodes.
3. Options A and B both hit the same wall: SBIB's real logic lives behind a stateful `Plugin`
   whose useful entry point (`GearScorer.score`) still needs this project to supply every live
   input itself (skill levels, varbits, a hand-built Prayer-multiplier table, a config-interface
   stand-in) — at that point most of the integration work Option C requires has already been
   done, without gaining anything from the ~2600-line `GearScorer`'s general-purpose scoring
   machinery, which is built to rank *arbitrary owned equipment* across many possible strategies
   and objectives — capability this project's routine does not need, since `decisions.md` has
   already picked one fixed gear/site/profile per task by hand.
4. Option D's live-plugin coupling is the wrong shape for an unattended, hours-long automation
   routine (CLAUDE.md's whole design ethos): a dependency on Plugin Hub's release cadence and an
   un-contracted package-private field is a worse foundation than this project's own pinned,
   tested Python data.

If a future need arises for SBIB's genuinely more sophisticated capability — the DPS/TTK
estimator comparing owned gear tiers, useful if this project ever wants "is my gear good enough"
rather than "here is the one gear/site/profile decided in advance" — Option B (vendor just
`GearScorer` + its pure supporting classes) would be the fallback, not Option A.

## 8. If vendored: the adapter's exact contract

Not recommended (§7), but specified here in case a future need reverses that call — this is the
contract for **Option B** (vendor the PURE/GAME-DATA core, write one adapter class,
`com.example.providers.SlayerGearProvider`, following the `M8aqProvider` precedent).

**Endpoint**: `GET /slayer/loadout?task=<name>&amount=<int>&location=<name optional>`

- `task` (required): the task name exactly as `/slayer` reports it (e.g. `"nechryael"`,
  `"aberrant spectres"`) — passed straight to the vendored `TaskProfiles.find(task, location,
  false)`.
- `amount` (optional, default: current `/slayer` count): drives `plannedKillCount` for the
  supply-quantity math.
- `location` (optional): forwarded to `TaskProfiles.find`'s `assignedLocation` parameter and
  `TravelItemAdvisor`/`SmartSupplyAdvisor`'s location-keyword matching; omitted, it falls back to
  the selected strategy's own `getLocation()`.

**What the adapter must supply that `recalculate()` normally reads off a live `Client`**
(§3 step 4), sourced from this project's own existing endpoints instead:

| `GearScorer.score(...)` argument | Upstream source in `recalculate()` | This project's adapter source |
|---|---|---|
| `scoringPool`/`bankItems`/`packedGearItems`/`packedSupplyItems` (`Item[]`) | live `ItemContainerChanged` snapshots | `GET /bank/memory` + `GET /inventory` + `GET /equipment`, converted to `net.runelite.api.Item[]` by item id/quantity (all three already carry ids) |
| `magicLevel`, `rangedLevel`, `attackLevel`, `strengthLevel` | `client.getRealSkillLevel(...)` | `GET /skills` |
| `kourendEliteComplete` | `client.getVarbitValue(VarbitID.KOUREND_DIARY_ELITE_COMPLETE) == 1` | a new varbit read exposed the way `VARBIT_CHANGED` subscriptions already work, or a one-off `client.getVarbitValue` call from inside the adapter (it runs in-process, so `Client` IS available here — this is not cross-process) |
| `ancientSpellbookActive`/`arceuusSpellbookActive` | `client.getVarbitValue(VarbitID.SPELLBOOK)` | same — in-process `Client` read, or `/api/player/magic.spellbook` (m8aq) string-compared |
| `combatLevels` (`CombatLevelContext`) | live `Prayer` varbit checks × a hardcoded multiplier table (`SlayerGearAdvisorPlugin.java:1233-1279`) | copy the multiplier table verbatim (it's pure arithmetic, no RuneLite import needed) into the adapter, driven by `GET /combat` or a live `Prayer` varbit read |
| `loadedDizanasQuiver` | `snapshotLoadedQuiverAmmo()` (a live `VarPlayerID.DIZANAS_QUIVER_*` read) | in-process `Client` read (adapter runs inside the same client) |
| `preferredStrategy`, `gearPriority`, `pinnedItems`, `excludedItems`, `lowRiskMode`, `riskCapGp` | `SlayerGearAdvisorConfig` (Guice-backed `@ConfigGroup`) | either a small hand-rolled config object (defaults: `GearPriority.BALANCED`, no pins/exclusions, Low-risk off) or new fields on this project's OWN `ControllerConfig.java` if the panel should expose them |

**Response shape** (`application/json`), field-by-field provenance:

```json
{
  "state": "READY",
  "task": "nechryael",
  "taskAmount": 130,
  "profile": "Nechryael",
  "strategy": {
    "name": "Catacombs barrage",
    "location": "Catacombs of Kourend",
    "combatStyle": "MAGIC",
    "rationale": "High-XP multi-target method."
  },
  "gear": {
    "WEAPON": {"itemId": 12904, "itemName": "Kodai wand", "score": 118.4,
               "reason": "...", "packed": false, "banked": true},
    "HEAD": { "...": "one entry per EquipmentInventorySlot" }
  },
  "requiredMissing": [
    {"label": "Slayer helmet or nose peg required", "slot": "HEAD"}
  ],
  "supplies": [
    {"category": "Food", "itemName": "Shark", "status": "BANKED",
     "recommendedQuantity": 15, "packedQuantity": 0, "bankQuantity": 24,
     "quantityUnit": ""}
  ],
  "inventoryPlan": {"occupiedSlots": 3, "pendingGearSlots": 5,
                     "requestedSlots": 27, "plannedSlots": 27, "reductions": []},
  "readiness": {"ready": false, "missingRequired": 1}
}
```

- `state`: `GearRecommendations.getState()` (`NO_TASK`/`UNSUPPORTED_TASK`/`OPEN_BANK`/`READY`) —
  the adapter's caller must treat anything but `READY` as "no usable recommendation" (mirrors
  `M8aqProvider`'s `{"reader","error"}` convention for a reader that cannot currently answer).
- `gear`: one entry per `EquipmentInventorySlot`, from `GearRecommendations.getLoadoutTiers().get(0)`
  (Tier 1 only, the "strongest complete owned setup" — RELEASE-NOTES.md:117) — each
  `GearRecommendation`'s public getters (`GearRecommendation.java:35-45`) map directly to JSON
  fields.
- `requiredMissing`: derived from `GearRequirement`s (`TaskSafetyRules.gearRequirements`) whose
  slot has no satisfying owned item in Tier 1 — this is the "which special items does this task
  require" answer the design's spec is actually after (earmuffs/nose peg/facemask/mirror
  shield/witchwood icon/boots of stone, per §5's table), EXCEPT that as §5 showed, several items
  this project's own `decisions.md` treats as required (fungicide spray, light source, rope) are
  NOT in SBIB's hard-requirement set at all and would silently not appear here — **the adapter
  cannot be the sole source of truth for "which items does this task need"; `decisions.md`'s own
  `carry` list (spec §6) must still be authoritative, with this endpoint only supplementing it**.
- `supplies`: `SupplyRecommendation`'s public getters, one row per rule that matched (§6) —
  `category`/`itemName`/`status`/`recommendedQuantity`/`packedQuantity`/`bankQuantity`/
  `quantityUnit` map directly.
- `inventoryPlan`: `InventoryCapacityPlan`'s fields, useful as a cross-check against this
  project's own 28-slot packing validation (spec §7.9's "fit in 28 slots" catalog test) but not a
  replacement for it, since SBIB's plan is computed from ITS OWN supply list/objective, not this
  project's per-task `Supplies` profile.

**Endpoints NOT worth building**: anything touching `TieredBankLayout`/`BankAdvisorButton`
(bank-widget repositioning) or the Swing panel — this project's routine never looks at the bank
UI, it drives `banker.py`'s own withdraw sequencing (spec §7.5), so vendoring those ~15 UI files
at all would be pure dead weight (§7's Option A analysis).

## 9. Licence and attribution

`LICENSE` (repo root), full text:

```
BSD 2-Clause License

Copyright (c) 2026, FreeArcanes
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

No `NOTICE` file, no per-file copyright headers were found in any of the 85 `.java` files
(checked by eye across the read files; none carry a header comment block at all — they open
directly with `package com.freearcanes.slayergear;`).

**What a vendored copy needs, per the licence's own two conditions:**

1. **Source form** (if source files are vendored byte-identical into this repo, as `m8aq` was):
   retain the `LICENSE` file's copyright notice and disclaimer with the vendored source — the
   precedent (`src/main/java/net/runelite/client/plugins/m8aq/VENDORED.md`) is a small
   `VENDORED.md` next to the copied package stating the upstream repo, the pinned commit, and
   reproducing (or pointing at) this notice. A `VENDORED-SBIB.md` in the analogous location
   (e.g. `src/main/java/com/freearcanes/slayergear/`) satisfies condition 1 the same way.
2. **Binary form** (this project's own built jar/distribution): the notice and disclaimer must
   also appear "in the documentation and/or other materials provided with the distribution" —
   for a private automation project with no distributed binary this is moot in practice, but
   keeping the `LICENSE` file alongside the vendored source covers it regardless.

BSD-2-Clause has no copyleft/share-alike requirement and does not require published changes to
be released — safe for a private, non-redistributed vendoring the way `m8aq` was done. No
attribution is required in-UI/runtime; the licence only requires the notice to travel with
redistributed source/binary copies, which a same-repo vendor satisfies trivially.
