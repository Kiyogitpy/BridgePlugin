# Tormented Demon fight comparison: Microbot-Hub reference vs. `tormented_demon.py`

Compares the open-source Microbot-Hub `tormenteddemons` plugin (BSD-2, client-injected input,
zero action latency) against this project's own `routines/tormented_demon.py` (untested,
physical Arduino input, HTTP bridge reads). Purpose: decide whether Greater demons — currently
**STOP** in `docs/slayer-sites/decisions.md` because the user fights them by hand on Tormented
Demons — could become a **FIGHT** task.

Files read:
- Microbot: `TormentedDemonScript.java` (577 lines), `TormentedDemonConfig.java` (313 lines),
  `TormentedDemonPlugin.java` (192 lines), `TormentedDemonOverlay.java` (44 lines), all in
  `.../microbot/tormenteddemons/` (scratchpad checkout).
- Mine: `ControllerAddon/routines/tormented_demon.py` (324 lines incl. header), and
  `ControllerAddon/tests/test_tormented_demon.py` (53 lines, one test).
- `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md` §6.2, 7.1, 7.5, 7.9.
- `docs/slayer-sites/decisions.md`.

The offline `osrs-wiki` MCP server failed to connect this session (CONNECTION_CLOSED), so no
wiki lookups were possible — every claim below comes only from the six files listed above. Where
that gap matters, it's called out explicitly.

---

## 1. The fight, as Microbot models it

**Architecture is split across two independent mechanisms**, which matters a lot for §5:

1. A **1-second polled loop** (`TormentedDemonScript.run`, scheduled
   `scheduleWithFixedDelay(..., 0, 1000, TimeUnit.MILLISECONDS)`,
   `TormentedDemonScript.java:67-86`) drives banking, travel, target selection, attacking,
   weakness-based gear swap, offensive prayer, potions, looting and retreat.
2. Two **RuneLite EventBus subscribers** in `TormentedDemonPlugin.java` — `onAnimationChanged`
   (`:151-190`) and `onGraphicsObjectCreated` (`:125-147`) — that react same-tick to the
   demon's own animation/graphics events, independent of the 1 s loop. This is where **all**
   defensive-prayer switching and fireball handling actually lives; `TormentedDemonScript.java`
   itself has no defensive-prayer code at all (confirmed by reading the whole file — the only
   prayer calls in `Script.java` are `disableAllPrayers()`, `activateOffensivePrayer`, and
   `switchOffensivePrayer`/`switchDefensivePrayer`, and `switchDefensivePrayer` is never called
   anywhere in the 4 files — dead code, see the ids table below).

**Attack-style detection.** `TormentedDemonPlugin.java:44-47` declares:
```java
private static final int CHANGE_ATTACK_STYLE_ANIMATION = 11387;
private static final int MELEE_ATTACK_ANIMATION = 11392;
private static final int MAGIC_ATTACK_ANIMATION = 11388;
private static final int RANGE_ATTACK_ANIMATION = 11389;
```
`onAnimationChanged` (`:151-189`) only acts when `event.getActor()` is an `NPC` AND
`localPlayer.getInteracting() == npc` (i.e., only the player's own current target, `:155-158`) —
this is **reactive**, not predictive: it fires off the animation actually playing, same as mine.
Two behaviors:
- On `MELEE_ATTACK_ANIMATION`/`RANGE_ATTACK_ANIMATION`/`MAGIC_ATTACK_ANIMATION`, if the matching
  protect prayer isn't already active, toggle it on (`:180-188`) — direct animation→prayer
  mapping, same idea as mine.
- On `CHANGE_ATTACK_STYLE_ANIMATION` (11387), it does **not** read the demon's next style at
  all — it blindly **rotates**: melee active → switch to range; range active → switch to magic;
  magic active → switch to melee; none active → default to melee (`:162-177`). This is a guess,
  not a reading — worth noting since animation 11387 is the same id my routine calls
  `ANIM_FIREBALL` (see §3).
- `TormentedDemonConfig.enableDefensivePrayer()` (keyName `autoPrayerSwitch`, default `true`,
  `TormentedDemonConfig.java:50-59`) is declared but **never read** anywhere in
  `Plugin.java`/`Script.java` — `onAnimationChanged`'s prayer switching is unconditional. The
  toggle appears to be dead/vestigial config.

**Shield/weakness mechanic (which style to use, when to swap).** The demon's `HeadIcon`
(`RANGED`/`MAGIC`/`MELEE`) is read directly off the NPC object
(`currentTarget.getHeadIcon()`, `Script.java:271`, and in `findNewTarget`, `:328`) — no damage
counter or hitsplat inference, a direct API read. `switchGear` (`:339-385`) maps the demon's
*current* weakness icon to the *opposite-style* gear the player should wear (e.g. icon
`RANGED` → wear melee or magic gear, `:350-358`) — i.e., the overhead icon signals what the
demon is **immune/resistant to**, and the player switches to a style NOT matching the icon. If
two eligible styles remain enabled in config, it picks between them with `Math.random() < 0.5`
(`:352,362,372`) — a coin flip, not a smarter tiebreak. Re-evaluated every loop iteration
(`newOverheadIcon != currentOverheadIcon` check, `:271-280`), plus once on new-target acquisition
(`:220-226`).

**Gear swapping.** Config: three comma-separated, free-text item-name strings
(`rangeGear()`, `magicGear()`, `meleeGear()`, `TormentedDemonConfig.java:236-288`, default `" "`
i.e. user must fill them in) — arbitrary item count per style, not fixed. `equipGear`
(`Script.java:395-400`) loops `Rs2Inventory.wield(item)` per name with `sleep(50)` between calls
— all client-injected, so an N-item swap costs Microbot roughly `N × 50ms` wall-clock, no cursor
travel. `isGearEquipped` checks **all** listed items via `Rs2Equipment::isWearing`
(`:391-393`) before deciding a swap is even needed. There's also a config-driven "Copy Gear
Setup" utility (`copyGear`, `TormentedDemonConfig.java:214-222`) that dumps currently-worn
equipment names to the clipboard via `TormentedDemonPlugin.onConfigChanged`
(`:89-120`) — a setup helper, not fight logic.

**Fire-bomb/fireball dodge.** `TormentedDemonPlugin.onGraphicsObjectCreated`
(`:125-147`) checks `graphicsObject.getId() != TORMENTED_VENGENCE_SPECIAL` where
`TORMENTED_VENGENCE_SPECIAL = 2856` (`:127`) — **this exactly matches my routine's
`FIREBALL_GRAPHICS_OBJECT_ID = 2856`**, corroborating it (see §3). On match: pauses all scripts
(`Microbot.pauseAllScripts.compareAndSet(false, true)`, `:136`), then after a configurable
`config.dodgeDelay()` (default **800 ms**, `TormentedDemonConfig.java:84-93`) calls
`Rs2Tile.addDangerousGraphicsObjectTile(graphicsObject, 600 * ticks)` with `ticks = 4`
(`:133-139`), i.e. marks the tile dangerous for 2400 ms, then unpauses. **The actual movement
mechanism that makes the player step off a dangerous tile is not in these 4 files** —
`Rs2Tile.addDangerousGraphicsObjectTile` is a Microbot-core call whose implementation isn't part
of this plugin; nothing in `Script.java` reads `Rs2Tile` or issues an explicit "walk away" click
in response to a dangerous tile. So Microbot's dodge, as visible here, is "mark the tile and let
generic Microbot pathing/tile-avoidance handle it elsewhere" — not a bespoke movement routine
like mine (`maybe_dodge_fireball`, which explicitly clicks one of four candidate offset tiles).
The 800 ms `dodgeDelay` default is a useful data point regardless: even with zero click latency,
Microbot's own authors chose to wait ~1.3 game ticks after the graphics object spawns before
reacting, implying the fireball's fuse is not a same-tick threat.

**Positioning.** No explicit in-fight positioning/safespotting code anywhere in `Script.java` —
after the travel state machine parks the player, there's no repositioning logic during
`handleFighting`. Not present.

**Multiple demons.** `findNewTarget` (`:322-337`) filters to
`!npc.isDead()` and `npc.getInteracting() == null || npc.getInteracting() == localPlayer`
(`:326`) — this avoids stealing another player's target, but there's no logic for fighting two
own-aggro'd demons at once (no kiting, no target-priority beyond "closest queryable on client
thread"). One target at a time, sequentially.

**Special attacks (demonbane, arclight, etc.).** Not present anywhere in any of the 4 files. No
weapon-special-attack code, no demonbane/silver-weapon logic. The whole design is
prayer-flick + weakness-driven gear/style swap, not a burn-with-specials design.

**Kill confirmation.** `currentTarget == null || currentTarget.isDead()`
(`Script.java:204`) — a direct API read of `Rs2NpcModel.isDead()`, no animation/hitsplat
inference needed (client-injected access advantage).

**Looting.** `attemptLooting` (`:418-428`): a 5-second scripted sleep with `pauseAllScripts`
held (`:207-212`), then `Rs2GroundItem.lootItemsBasedOnNames` against a **name whitelist**
parsed from `config.lootItems()` (comma-separated, default empty string, `:443-445`) — purely
name-based, no GE-value threshold logic anywhere in these files (unlike this project's planned
two-tier value looting in slayer spec §7.10). Optional "Scatter Ashes" behavior
(`lootAndScatterInfernalAshes`, `:430-441`) picks up "Infernal ashes" specifically and scatters
them via a menu action if `config.scatterAshes()` is on (`TormentedDemonConfig.java:114-123`).

---

## 2. Everything around the fight

**Travel** (`handleTravel`, `Script.java:90-145`), a 5-step state machine
(`TravelStep`: `LOCATION_ONE, CLIMB_FIRST_STAIRS, CLIMB_SECOND_STAIRS, CLIMB_THROUGH,
LOCATION_THREE`, `:47`):
1. `LOCATION_ONE`: inventory-interact "Guthixian temple teleport" → "Teleport"
   (`:101`), wait for animation to end, wait until within 5 tiles of `(4062, 4558, 0)`
   (`targetLocationOne`, `:94`).
2. `CLIMB_FIRST_STAIRS`: interact with tile-object id **53623**, action "Climb-up" (`:111`).
3. `CLIMB_SECOND_STAIRS`: tile-object id **53624**, "Climb-up" (`:120`).
4. `CLIMB_THROUGH`: tile-object id **54082**, "Climb-through" (`:129`).
5. `LOCATION_THREE`: `Rs2Walker.walkTo((4073, 4432, 0), 2)` (`targetFinalLocation`, `:95,138`),
   wait up to 5000 ms to arrive exactly, then `BOT_STATUS = FIGHTING` (`:139-142`).

None of the 4 files names the destination "Wilderness God Wars Dungeon" in any string or
comment — the closest evidence is the item name "Guthixian temple teleport" and the stair/climb
route. This is consistent with (does not contradict) my routine's header claim, but since the
`osrs-wiki` MCP failed to connect this session, I could not independently confirm the location
name from a source outside these files.

**Banking** (`handleBanking`, `:147-201`), 3-step state machine (`BankingStep`: `DRINK, BANK,
LOAD_INVENTORY`, `:51`):
1. `DRINK`: if HP or prayer below max, interact with tile-object id **39651**
   (`FEROX_POOL_ID`, `:148`), action "Drink" (`:159`), wait until both are maxed (`:161-164`) —
   this is the **Ferox Enclave** Pool of Refreshment (consistent with `SAFE_LOCATION =
   (3150, 3634, 0)`, `:41`, used as the post-retreat landing check, `:241`).
2. `BANK`: if `isRestocking`, first **wears** the saved "tormented" `Rs2InventorySetup`
   equipment (`:174-176`) — before opening the bank — then opens bank and `depositAll()`
   (`:178-183`).
3. `LOAD_INVENTORY`: loads both equipment and inventory from the same named "tormented"
   `Rs2InventorySetup` (`:185-199`); on success, closes bank, returns to `TRAVEL_TO_TORMENTED`,
   sets `isRestocking = true`; **on failure, calls `shutdown()`** (`:196-198`) — i.e. a missing
   loadout item is a hard stop, not a retry loop.

**Restock/retreat trigger** — `shouldRetreat` (`:402-410`), checked only in `FULL_AUTO` mode
(`:236`, `COMBAT_ONLY` never retreats — matches the config's own docstring, "Combat Only:
position your character near the demons and start the plugin"):
```java
return (noFood || currentHealth <= config.healthThreshold())
    || (noPrayerPotions && currentPrayer < 10);
```
`healthThreshold()` defaults to **30%** (`TormentedDemonConfig.java:156-165`). On trigger:
clear target, pause all scripts, `teleportToFeroxEnclave()` (wear a Ring of Dueling item id
from `ItemID.RING_OF_DUELING_1..8`, then `Rs2Equipment.interact(...FEROX_ENCLAVE...)`,
`:447-470`), wait up to 5000 ms to land at `SAFE_LOCATION`, unpause, `BOT_STATUS = BANKING`
(`:236-245`).

**Supplies maintained every loop iteration, unconditionally** (not gated on retreat):
- `Rs2Player.eatAt(config.minEatPercent())` and `Rs2Player.drinkPrayerPotionAt(config.minPrayerPercent())`
  (`:249-250`) — thresholds default **50%** HP (`:134-143`) and **20%** prayer (`:145-154`).
- `evaluateAndConsumePotions` (`:234,472-482`): maintains a combat potion (Super combat /
  Divine super combat) and a ranging potion (Ranging / Divine ranging / Bastion) above
  `config.boostedStatsThreshold()` (default **10%**, `:190-203`), independent of the
  eat/prayer-drink calls.

**Death handling.** Not present in any of the 4 files — no death/respawn check anywhere.

**Stuck/timeouts.** Only local, per-step `sleepUntil(..., timeoutMs)` calls (5000 ms for
teleport/retreat/travel landings, 3000 ms for the attack-interaction confirm, `:104,139,241,262`
etc.) — no global stuck-counter, retry ceiling, or escalation to a stop. The main loop's
try/catch (`:82-84`) just logs `"Error in main loop: " + ex.getMessage()` and lets the next
scheduled tick run again — errors are swallowed forever, never surfaced as a stop.

**State machine, full list:**
- Top level (`State`, `Script.java:43`): `BANKING`, `TRAVEL_TO_TORMENTED`, `FIGHTING`.
- `BankingStep` (`:51`): `DRINK → BANK → LOAD_INVENTORY → (State.TRAVEL_TO_TORMENTED)`.
- `TravelStep` (`:47`): `LOCATION_ONE → CLIMB_FIRST_STAIRS → CLIMB_SECOND_STAIRS →
  CLIMB_THROUGH → LOCATION_THREE → (State.FIGHTING)`.
- `FIGHTING → BANKING` only in `FULL_AUTO` mode via `shouldRetreat` (`:236-245`); in
  `COMBAT_ONLY` mode `BOT_STATUS` is pinned to `FIGHTING` for the plugin's whole lifetime
  (set once in `run()`, `:58-60`, never reassigned elsewhere for that mode).

---

## 3. Ids and constants table

| Constant | Microbot value | Source | My routine's value | Agreement |
|---|---|---|---|---|
| Melee attack animation | 11392 | `TormentedDemonPlugin.java:45` | `ANIM_MELEE = 11392` | **Agree** |
| Magic attack animation | 11388 | `TormentedDemonPlugin.java:46` | `ANIM_MAGIC = 11388` | **Agree** |
| Ranged attack animation | 11389 | `TormentedDemonPlugin.java:47` | `ANIM_RANGED = 11389` | **Agree** |
| "Change attack style" animation | 11387 (`CHANGE_ATTACK_STYLE_ANIMATION`) | `TormentedDemonPlugin.java:44` | `ANIM_FIREBALL = 11387` | **Id agrees, semantics differ** — see below |
| Summon animation | not present | — | `ANIM_SUMMON = 11395` | Neither confirmed nor denied |
| Death animation | not present | — | `ANIM_DEATH = 11394` | Neither confirmed nor denied |
| Fireball/special-attack graphic | 2856 (`TORMENTED_VENGENCE_SPECIAL`) | `TormentedDemonPlugin.java:127` | `FIREBALL_GRAPHICS_OBJECT_ID = 2856` | **Agree — this is the corroboration my routine's header flagged as needed.** |
| NPC id(s) | not present — targeted by name `"Tormented Demon"` (`Script.java:324`) | `Script.java:322-337` | `DEFAULT_NPC_IDS = [13599, 13600]` | **Not present** — Microbot's name-based targeting neither confirms nor contradicts these ids |
| Ferox Enclave pool object | 39651 | `Script.java:148` | not used (no banking in my routine) | — |
| Travel stair/climb objects | 53623, 53624, 54082 | `Script.java:111,120,129` | not used | — |
| Ring of Dueling item ids | `ItemID.RING_OF_DUELING_1..8` (symbolic constants, no literal numeric ids in these files) | `Script.java:448-457` | not used | — |
| World points | `SAFE_LOCATION (3150,3634,0)`; travel `(4062,4558,0)`, `(4073,4432,0)` | `Script.java:41,94-95` | not used | — |
| Varbits | **none referenced anywhere in the 4 files** | — | none used either | Both: not present |
| Weakness signal | `HeadIcon` enum (`RANGED`/`MAGIC`/`MELEE`), direct API read | `Script.java:349,360,370` | `overheadIcon` field from `NPCProvider`'s `UnsafeNpcOverheadIcon` reflection read (per my routine's own header) | Same concept, different reliability — mine depends on an unofficial reflection path Microbot doesn't need (it has direct client access) |

**On the 11387 naming mismatch:** Microbot's own constant name is
`CHANGE_ATTACK_STYLE_ANIMATION`, not anything naming a fireball. Their `onAnimationChanged`
handler treats it purely as a "the demon is about to switch style" tell and blindly rotates the
active protect prayer in response (`:162-177`) — it does **not** treat 11387 as the fireball
animation. The actual fireball/special attack is tracked separately, by the **graphics object**
id 2856 (`onGraphicsObjectCreated`). My routine's naming (`ANIM_FIREBALL`) for animation 11387
therefore looks like a mislabel inherited from the community fork cited in its header — though
functionally my routine does nothing with `ANIM_FIREBALL` at all (it's excluded from
`ATTACK_ANIMATION_TO_PRAYER` and never referenced elsewhere in `tick()`), so the mislabel has no
behavioral consequence today. What it does mean: my routine has **no equivalent of Microbot's
style-change-tell reaction** — not even Microbot's blind coin-flip rotation — so on the tick the
demon telegraphs an upcoming style change, my routine does nothing until the new style's actual
attack animation plays.

**On location:** my routine's header says the fight is in the "Wilderness God Wars Dungeon".
Microbot's `Script.java` never names the location in a string or comment; its travel route
(interact "Guthixian temple teleport" → climb two staircases → climb through → walk to
`(4073, 4432, 0)`) is consistent with, but does not itself confirm, that name. I could not
independently verify against the wiki this session (MCP connection failure) — this remains
open, not resolved by these files.

---

## 4. Gap list for my routine (ranked by importance for 130-200 unattended kills)

1. **No banking/restock loop at all in `tormented_demon.py`.** The module docstring says so
   outright: *"No pathing/positioning logic beyond the fireball dodge -- walk to the demon
   yourself before starting this routine."* There is no `BANKING`/`TRAVEL` state, no
   `shouldRetreat`-equivalent, and no function anywhere in the file resembling
   `combat_core.needs_banking(ctx)`. Food and prayer-potion maintenance
   (`maintain_prayer_potions`, the `hp <= eatAt` check in `tick()`) only consumes what's already
   in the inventory — once food or prayer potions run out, the routine has no path to leave and
   restock; it will just keep fighting empty-handed until death or a supply-driven safety stop
   external to this file. This is the single largest gap for surviving 130-200 kills. Per spec
   §7.5 (`slayer-task-loop-design.md:404-426`), this is *by design* meant to be the
   orchestrator's job (`combat_core.needs_banking`, `RESTOCK` phase) rather than the fight
   loop's own — but nothing in `tormented_demon.py` currently exposes a matching predicate to
   plug into that pattern, and the slayer orchestrator itself is still a design document, not
   built code.

2. **No looting at all.** `tormented_demon.py` has no ground-item pickup function anywhere in
   its 271 lines of logic — Microbot's `attemptLooting`/`lootAndScatterInfernalAshes` have no
   counterpart. For a task whose entire point (beyond slayer credit) is Tormented Demons' loot
   table, leaving 100% of drops on the ground defeats the purpose of automating this task,
   though it doesn't threaten survival.

3. **Auto gear-swap is present in code but explicitly disabled** (`tick()`, `:191-195`,
   commented out with the note *"Temporarily disabled -- maybe_swap_gear depends on the
   overheadIcon field... Re-enable once that's confirmed working again"*). So today the routine
   always fights with one fixed `combatStyle`, taking chip damage on every tick the demon's
   weakness rotates away from that style, per the header's own admission (*"worse DPS on
   off-style ticks -- it still takes some chip damage while shielded"*). Over 130-200 kills this
   compounds into materially more food/potion burn and longer session time than Microbot's live
   weakness-tracking gear swap. See §5 for why re-enabling it as a literal port of Microbot's
   multi-item swap is not physically viable anyway.

4. **No offensive prayer support** (Piety/Rigour/Augury-equivalent). Microbot's
   `activateOffensivePrayer`/`getBestMagePrayer`/`getBestMeleePrayer`/`getBestRangePrayer`
   (`Script.java:284-320`) have no counterpart in my routine. DPS-only impact, not survival.

5. **No stat-boost potion maintenance** (combat potion / ranging potion,
   `evaluateAndConsumePotions`, `Script.java:472-506`). DPS-only impact.

6. **No "don't steal another player's target" filter.** Microbot's `findNewTarget` explicitly
   checks `npc.getInteracting() == null || == localPlayer` (`:326`); `find_target` in
   `tormented_demon.py` just sorts by distance among matching, alive, in-range npc ids
   (`:209-222`) with no interacting-actor check. Low importance if the spot is normally
   uncontested, but easy to add.

7. **No style-change-tell reaction** — see §3's note on animation 11387. Even Microbot's version
   of this is a blind coin-flip, so this is a low-value gap to close, but it does mean my
   routine's reaction to a genuine style switch is strictly one attack-cycle later than
   Microbot's (which itself is only a guess, not a real improvement — noting for completeness).

8. **Kill confirmation is implicit only.** `find_target` drops a demon once `hpRatio == 0`
   (`:215`), which is adequate as a "this target is done" signal but — because there's no
   looting or per-kill bookkeeping in the file — nothing downstream actually consumes that
   signal (no kill counter, no loot window, no per-kill log line). Cosmetic/telemetry gap.

Positioning and multiple-demon handling are **not present in either implementation** — a wash,
not a gap specific to mine.

---

## 5. Physical-input feasibility

Baseline: ~150-600 ms per physical click via the Arduino, a 600 ms game tick, and — critically —
the observation from §1 that even Microbot, with **zero** action latency, still needed
**event-driven, not polled**, code for the two safety-critical reactions (defensive prayer,
fireball dodge), because their own 1-second poll loop was judged too slow for those two
mechanics. That is direct evidence from the reference implementation that a coarse poll loop is
marginal for TD's defensive mechanics even before any click-travel cost is added.

**a) Defensive prayer switch on attack-animation tell (1 action).** Concretely: possibly a
keypress to open the prayer tab (if not already open) + one click on the matching protect-prayer
icon. `tormented_demon.py`'s `react_to_attack_animation` does exactly one click
(`controller.click_entity(..., offset=(0,0), check_ui_blockers=False, simulate_mistakes=False)`,
`:242`) with no separate "toggle old prayer off" step — that's actually **correct and
sufficient**, not a gap: OSRS auto-deactivates the previous overhead protect prayer when a new
one is activated, unlike Microbot's generic `Rs2Prayer.toggle(old, false)` + `toggle(new, true)`
pattern (`switchDefensivePrayer`, `Script.java:290-296`), which is written for prayers in general
(several of which *can* be stacked) and is unnecessary overhead for protect-prayer-only
cycling. **Fits in one tick** if the prayer tab is already open (single click, 150-400 ms <
600 ms), assuming melee-style attacks have any hit-delivery delay after the animation starts —
if a melee attack's damage is calculated the same tick the animation plays (common for
non-projectile monsters), a reactive click that starts only after the animation is observed
cannot land before the hit regardless of click speed; this is a physics-of-the-fight question the
files don't answer (no attack-delay data in either source), so treat "occasional" hits through a
switch (as my routine's own header phrases it) as potentially a **systematic** cost on melee
specifically, not merely occasional, until measured live.

**b) Fireball dodge (1 action).** One click to a nearby minimap point
(`maybe_dodge_fireball`, `:285-310`). Microbot's own `dodgeDelay` default is **800 ms**
(`TormentedDemonConfig.java:84-93`) — i.e., even with instant clicks, the reference
implementation waits nearly 1.3 game ticks before reacting, which implies the fireball's fuse
gives at least that much margin. A single physical click well under that budget is
**comfortably feasible within 1-2 ticks**, the most confidently-feasible mechanic in this
comparison.

**c) Weakness-driven gear swap (2-4+ actions).** Microbot's `equipGear` loops one
`Rs2Inventory.wield()` per configured item name with a 50 ms sleep between (`:395-400`) — for a
plausible 2-4 item loadout (weapon + shield/off-hand, sometimes + ammo/headgear) that's
~100-200 ms of Microbot wall-clock, trivially inside one tick because every click is
client-injected. For real physical clicks at 150-400 ms each with no ability to overlap (each
needs to land and settle), a 2-item swap costs roughly 300-800 ms (borderline, likely spills
into a 2nd tick) and a 3-4 item swap costs 450-1600 ms (**1-3 ticks, does not fit Microbot's
implicit "swap immediately, mid-fight" pattern**). Since the weakness icon changes only every
several ticks (a periodic rotation, not every tick), spending 2-3 ticks to complete a swap while
still attacking with the old gear in the meantime would probably be tolerable — but this is not
what either file currently implements (mine disables the feature entirely rather than doing a
partial/staged swap). **Recommended redesign if re-enabled: a weapon-only, one-click swap**
(skip shield/ammo/headgear changes), accepting some chip damage on the off-style ticks — which
is effectively what my routine already does today by disabling the feature and fighting one
fixed style throughout, just without ever re-optimizing toward the current weakness.

**d) Offensive + defensive prayer both changing in the same tick.** Worst case for Microbot:
up to 4 `Rs2Prayer.toggle()` calls (2 for defensive, 2 for offensive if both switch), effectively
free for them. For physical input this would be up to 4 clicks × 150-400 ms = 600-1600 ms —
**does not fit in one tick, and often not two.** My routine avoids this today simply by never
implementing offensive prayer at all. If offensive prayer support is added later, it should use
a **quick-prayer orb** (one click activates a pre-configured pair) rather than the tab-toggle
pattern, matching the task instructions' suggestion.

**e) Retreat/restock sequence.** Not a per-tick pattern — a few clicks spread over several
seconds when supplies run low, so ordinary click latency doesn't threaten it the way the
per-tick mechanics above do.

**Summary table:**

| Action pattern | Microbot cost | Physical-input cost | Fits 1 tick? |
|---|---|---|---|
| Defensive prayer switch (1 click) | ~0 ms | 150-400 ms | Yes, if tab open — but see melee-delay caveat |
| Fireball dodge (1 click) | ~0 ms, 800 ms deliberate delay | 150-400 ms | Yes, comfortably |
| Weapon-only gear swap (1 click) | ~0 ms | 150-400 ms | Yes |
| Full 2-4 item gear swap | ~100-200 ms | 300-1600 ms | **No — spills 1-3 ticks** |
| Both offensive+defensive prayer (up to 4 clicks) | ~0 ms | 600-1600 ms | **No** |

---

## 6. Verdict

**Can Greater-demon slayer tasks realistically be automated on Tormented Demons with physical
input?** Not yet, on the evidence in these six files. The fight itself (single defensive-prayer
click, single fireball-dodge click) looks physically achievable within the tick budget — those
are the two mechanics Microbot itself judged needed event-driven code rather than its 1-second
poll loop, and my routine's per-tick reactive design is architecturally closer to that than
Microbot's own combat/banking loop is. But three things stand between that and a safe 130-200
kill unattended run:

1. **No restock loop exists in `tormented_demon.py` today**, and the slayer orchestrator design
   that's meant to supply one (`combat_core.needs_banking`, the `RESTOCK` phase, spec §7.5) is
   still a design document, not built code. Without it, the routine runs until its starting
   inventory of food/prayer potions is exhausted, then keeps fighting anyway — for a monster
   that hits hard enough that Microbot's own default retreat threshold is 30% HP
   (`TormentedDemonConfig.java:163-165`), this is a death sentence, not a soft failure.
2. **No looting**, which defeats the point of spending 130-200 kills on TDs specifically (their
   drop table) rather than any other Greater-demon-counting site.
3. **The melee-reaction-timing risk is unquantified.** Neither file states TD's attack-to-hit
   delay per style; if melee has zero travel delay (common for non-projectile monsters), a
   reactive prayer switch triggered by seeing the animation can never beat the hit, meaning it's
   not "occasional" chip damage but a **per-melee-attack-cycle guaranteed hit** every time the
   demon switches into melee — a materially different risk profile than my routine's header
   implies. This needs either a wiki lookup (unavailable this session) or a
   `recorder.py`-captured human fight to settle before trusting it unattended.

**Kill time / supply burn per 100 kills:** neither file states this — no timing logs, no
per-kill duration anywhere in the 4 Microbot files, and my own routine has never run live. I
can't give a grounded number from these sources; Microbot's config defaults (50% eat threshold,
20% prayer-restore threshold, 30% retreat threshold, `TormentedDemonConfig.java:141-165`) hint
at the intended safety margins but say nothing about throughput. This should come from either a
wiki page (once the MCP connection is restored) or a recorded human TD session before committing
to a supply profile the way `decisions.md`'s other Kuradal tasks already have one.

**Recommendation: keep Greater demons as STOP for now**, and stage the work if the user wants to
pursue automating it:

1. **Stage 1 — prove survivability, supervised, short sessions.** Wire up food/prayer-potion
   maintenance (already exists) plus a hard, conservative safety-stop the moment food or prayer
   potions run out (no retreat logic needed yet, just stop cleanly) — the exact opposite of
   "keep fighting empty-handed." Run 10-20 kills with a human present and the panic key within
   reach, specifically watching whether the melee-reaction-timing risk in point 3 above is real.
2. **Stage 2 — add restock and looting**, built against whatever `combat_core`/orchestrator
   conventions the slayer spec settles on (§7.5's `RESTOCK` phase, `needs_banking`-style
   predicate), not as bespoke logic inside `tormented_demon.py`. Re-test supervised.
3. **Stage 3 — only then consider unattended 130-200 kill runs**, and even then keep retreat
   thresholds well above Microbot's own defaults (which were tuned for a zero-latency client,
   not a 150-400 ms-per-click physical setup) to buy margin for slower reactions.
4. **Do not port Microbot's full multi-item gear swap.** If weakness-tracking is wanted at all,
   implement it as the one-click weapon-only version discussed in §5(c), not a literal port.

Until Stage 1 has actually run and confirmed the melee-timing risk is survivable, this fight
should stay a STOP, exactly as `decisions.md` has it today.
