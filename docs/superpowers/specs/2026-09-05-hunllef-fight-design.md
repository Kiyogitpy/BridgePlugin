# Gauntlet sub-project 2: the Hunllef fight -- design

Date: 2026-09-05. Status: approved in chat, awaiting written review.

Sub-project 1 (`2026-09-05-gauntlet-instance-plumbing-design.md`) gave routines instance-space
coordinates, an instance router, NPC overhead data and spawn/projectile events. This sub-project
builds the routine that uses them: `routines/corrupted_gauntlet.py`, phases `enter`, `fight`,
`loot`, `done`. Sub-project 3 (the maze prep) will add its phases in front of `enter`; the file
is named for the whole activity so nothing is renamed later.

## Goal

From the room in front of the Hunllef's barrier, with supplies in hand: pass the barrier, kill
the Corrupted Hunllef without dying, open the reward chest in the lobby, stop. Unattended.

## Non-goals

- The prep: gathering, crafting, potions, food. Sub-project 3.
- Regular (non-corrupted) Gauntlet. The id tables cover it; the routine is built and verified
  on the corrupted fight, which is the harder superset.
- Beating the human's kill time. Correctness and survival first; cadence tuning is a live-session
  activity, recorded in CLAUDE.md as measured.

## Facts this rests on

All from `recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1.*` (one kill, 4:50 fight,
prep 6:38), the OSRS Wiki (Corrupted Hunllef, rev 15301498), the game cache (osrs-dumps
5bc2ff1), and ZombieLite's `Hunllef.java`/`GauntletPlugin.java`.

- **Attack cadence: one attack every 5 ticks** (88 of 93 measured gaps exactly 5.0 ticks; the
  lone 10-tick gap spans a style boundary, where the marker animation sits between attacks).
- **Style alternates at a MARKER animation after every 4 attacks, ranged first.** Measured in
  ticks from the fight start: attacks 8419 at 7, 12, 17, 22; marker 8754 at 24; attacks at 27,
  32, 37, 42; marker 8755 (tornado cast) at 44; attacks 47 (8418), 52, 57, 62; marker 8754 at
  64; ... for the whole kill. So: four attack-like animations (8419 standard, 8418
  prayer-disabling), then two ticks later a marker -- 8754 (the "switch style" animation) and
  8755 (the tornado cast) ALTERNATE as the marker -- then the next style's first attack three
  ticks after the marker. The tornado cast does not consume one of the four; it IS the
  boundary. The human clicked the new protection prayer 0.1-1.7 ticks after each marker (24.1,
  45.3, 65.7, 86.5, 104.4, 125.4, ...). 8420 (stomp) appears once, mid-sequence, and shifts
  nothing. Predictive rule: after the 4th attack-like animation since the last marker, the
  next style is known and the switch can be made 5 ticks before it lands; the marker confirms
  and resyncs (a marker arriving with a count other than 4 resets the count).
- **The NPC id IS the overhead prayer.** Cache symbols: 9035 `crystal_hunllef_melee_hm`, 9036
  `crystal_hunllef_ranged_hm`, 9037 `crystal_hunllef_magic_hm`, 9038 `crystal_hunllef_death_hm`.
  Its prayer changes every 6th off-prayer player attack (wiki). In the recording each of the
  human's 14 weapon swaps followed an id flip by 1.5-2.0 s (npcchange 13.8 -> wield 15.8, 28.8 ->
  30.8, ...). `overheadPrayer`/`overheadArchiveId` from sub-project 1 are a cross-check only.
- **Size 5** (cache `size=5`): footprint is 5x5. Standing under it when it attacks draws a stomp
  (wiki: up to 68 typeless; the human ate one 38 at 223 s, chat "You're trampled beneath the
  Hunllef."). It moved once in the whole recording (two distinct positions).
- **Tornadoes**: NPC 9039, spawn in pairs (22 spawns), live 20 ticks, chase the player, 15-30
  damage. The human's next walk click came 0.6-4.1 s after a spawn (median 1.2 s).
- **Floor**: GroundObjects 36047 `..._floor_tile_01_warning_hm` (crimson) then 36048
  `..._floor_tile_01_hit_hm` (orange, 10-20 per tick while stood on). Patterns and speed step up
  at 667 and 333 HP (of 1000). The recorder captured none of them -- it listens for GameObject
  spawns only -- so they are POLLED from `/objects` (which does walk GroundObjects).
- **Prayer-disabling attack**: chat GAMEMESSAGE "Your prayers have been disabled!" (9 in the
  kill). Projectiles 1713/1714 precede it (ZombieLite); standard ranged/magic projectile ids are
  unknown until the live checklist logs `PROJECTILE_SPAWNED`.
- **Entry/exit**: `Quick-pass` on Barrier 37337 (template (1975,5694)), first Attack click 0.5 s
  later. The kill teleports the player to the lobby (LOADING/LOGGED_IN 2 s after the death
  message); `Open` on Reward Chest 37341 follows. Varbit 9177 is 1 for the whole fight, 0 after.
- **Damage taken with the right prayer**: 2-10 per hit (T-tier armour, wiki table); the big
  hits were tornado/stomp (31, 38, 21). Human: 48 fish eaten, 8 egniol doses, HP low 23,
  min prayer unknown. Paddlefish heal 16; eat delay 3 ticks.
- **Human click cadence**: 225 walk clicks in 290 s, i.e. one every 1.3 s -- the fight is mostly
  movement; 59 Attack clicks, 42 prayer clicks, 14 wields.
- **Available primitives**: `/npcs` (id, index, instX/instY, animationId, hpRatio),
  `NPC_TRACKED_ANIMATION_CHANGED` for the interaction target (auto-tracked by
  `CurrentTargetTrackerFactory`), `NPC_SPAWNED`, `CHAT_MESSAGE`, `PROJECTILE_SPAWNED`,
  `VARBIT_CHANGED` (subscribed), `/prayers` (`active`, `icons`, `tabOpen`), `/inventory`,
  `/equipment`, `/player` (hp, prayer, instX/instY), `get_ground_walk_point(inst=True)`,
  `instance_nav.InstanceMap`, `bridge_events.await_outcome`, `click_entity(expected_action=)`.

## Design

### Approach

A **tick-chained priority ladder**, the shape `guardians_of_the_rift.py` settled on. Once the
fight is live, `tick()` does not return to the engine loop between decisions (the engine costs
~1.1 s per iteration; the prayer window is 3 s). It loops on game-tick boundaries via
`ctx.events.wait_ticks(1)` (falling back to a 0.6 s sleep when `ctx.events` is None), takes one
HTTP snapshot per game tick, runs the ladder, performs at most the top rung's action, and
returns to the engine only on a phase change, a safety stop, or `FIGHT_MAX_S` (8 min). The panic
key is a separate thread and keeps working. Rejected: a purely event-driven state machine (UDP is
lossy; every acted-on fact must come from HTTP), and one decision per engine loop (too slow).

### Phases

`ctx.state.data["cg_phase"]`, derived fresh on every tick from live state, never defaulted:
- `enter` -- in the barrier room (Barrier 37337 visible, varbit 9177 == 0, no Hunllef in `/npcs`).
- `fight` -- varbit 9177 == 1, or a Hunllef id in `/npcs`.
- `loot` -- Reward Chest 37341 visible and varbit 9177 == 0 after a fight was seen this run.
- `done` -- chest opened (chat "You open the chest." / inventory delta), or the player died.
Any other situation (e.g. started mid-prep) logs "start at the Hunllef barrier" and idles.

### Snapshot (`FightSnapshot`, one per game tick)

Built from: `/player` (hp, prayer, instX, instY, plane, animation), `/combat` (`targetId`,
`targetName`, `inCombat` -- whether we are attacking the Hunllef), `/npcs`
(`fresh=True`; the Hunllef entry by id in `HUNLLEF`, every 9039 tornado), `/objects`
(`fresh=True`, radius 12, filtered to 36047/36048, keyed by instX/instY), `/prayers`
(`active`), `/equipment` (weapon name), `/inventory` (paddlefish, corrupted paddlefish,
egniol doses, bow, staff). ~5 HTTP calls, measured against the 0.6 s tick in the first live
session; if it does not fit, `/prayers`, `/equipment`, `/inventory` drop to every other tick
(they only change when we act on them).

### Attack counter and style

`StyleTracker` consumes the Hunllef's animation changes -- from `NPC_TRACKED_ANIMATION_CHANGED`
when present, confirmed against the snapshot's `animationId` transitions each tick (HTTP is the
truth; the event only shortens the reaction). Rules, from the measured sequence: `ANIM_ATTACK_LIKE
= {8419, 8418}` each add one to `attacks_since_marker`; `ANIM_MARKER = {8754, 8755}` flip
`style` (RANGED <-> MAGIC) and reset the count to 0; 8420 (stomp) is ignored. `style` starts
RANGED. `next_style_due()` is true from the 4th attack-like animation after the last marker
until the marker arrives -- the moment the routine switches protection prayer for the coming
style (5 ticks before its first attack). A marker arriving with a count other than 4 logs a
resync line and still flips. `expected_prayer()` returns Protect from Missiles for RANGED and
Protect from Magic for MAGIC, for the style that is current or, while `next_style_due()`, the
one coming.

### Hunllef prayer and weapon

`hunllef_prayer(id)`: 9035 -> MELEE, 9036 -> RANGED, 9037 -> MAGIC, 9038 -> DEAD. Desired
weapon: RANGED -> staff, MAGIC -> bow, MELEE -> bow (config `melee_weapon`), i.e. always the
style it is not praying against. Each weapon has an offensive prayer in config
(`offensive_prayers = {"bow": ["Rigour", "Deadeye", "Eagle Eye"], "staff": ["Augury", "Mystic
Might", "Mystic Lore"]}` -- first one present in `/prayers.icons` wins). Weapon names: config
lists the perfected, attuned and basic corrupted/crystal names for each; whichever is in the
inventory or equipped is used.

### The ladder (top rung wins; one action per tick)

1. **Escape** when any of: standing on a 36047/36048 tile; a tornado within `TORNADO_DANGER_TILES`
   (2) of us; Chebyshev distance from us to the Hunllef centre <= `HUNLLEF_FOOTPRINT_RADIUS` (2).
   Target: `pick_safe_tile(snapshot, grid)` scores every standable tile within
   `ESCAPE_RADIUS` (6) of us: forbidden if hazard, if Chebyshev to the Hunllef centre < 3
   (footprint + 1 margin), if within 3 of any tornado, if unreachable in the grid; preferred by
   (larger min tornado distance, within `ATTACK_RANGE` (7) of the footprint edge, shorter
   distance to us). Click: `get_ground_walk_point(inst=True)` + shift-click via
   `travel._click_walk_point`, no arrival wait (the next tick re-evaluates). Off-screen point:
   minimap via `get_minimap_walk_point(inst=True)`.
2. **Protection prayer** when `next_style_due()` or the expected protection prayer is not active
   (the prayer-disable attack; also detected early by `CHAT_MESSAGE` "Your prayers have been
   disabled!"): open the prayer tab (F-key), click the protection icon, then the weapon's
   offensive prayer if it is off. Clicks use `simulate_mistakes=False` and
   `check_ui_blockers=False` (interface clicks; a misclick on a toggle is two actions).
3. **Weapon** when the equipped weapon is not the desired one for the Hunllef's current id: open
   the inventory tab, click the weapon (`expected_action="Wield"`), then rung 2's offensive
   prayer, then re-click Attack (a wield cancels the auto-attack -- the human re-clicked 0.3 s
   after every wield).
4. **Eat / drink** when `hp <= eat_at` (45): click one paddlefish (corrupted first, they are
   equivalent; one per tick -- the game's 3-tick eat delay wastes the human's spam). When
   `prayer <= egniol_at` (15) or (`hp <= eat_at` and no fish): egniol. Inventory clicks open
   the inventory tab first (the pouch-repair lesson).
5. **Attack** when `/combat.targetId` is not a Hunllef id: `click_entity` on the Hunllef with
   `npc_index_refresh_target`, `expected_action="Attack"`, `expected_target="Corrupted Hunllef"`;
   a withheld click escalates to `click_menu_action("Attack")`.
6. **Reposition** when Chebyshev distance to the centre > `ATTACK_RANGE + 2` or < 3 (it walked
   onto us without a stomp yet): same `pick_safe_tile` with the "in range" preference made a
   requirement.
7. Idle: nothing to do this tick (the common case while auto-attacking).

Every click's outcome is awaited with `bridge_events.await_outcome` (a listed signal confirmed
by one HTTP read, budget one tick): prayer -> `/prayers.active`; wield -> `/equipment`;
eat -> `INVENTORY_CHANGED` + `/player.hp`; attack -> `/combat.targetId`.

### Stomp guard (user requirement)

The footprint (centre ±2, size 5) plus one tile of margin is forbidden ground for EVERY walk
target the routine picks -- escape, reposition, and any future prep-phase walk that passes
through `pick_safe_tile`. The escape rung fires the moment the centre comes within 2 of us. A
`Hunllef` that moves is re-read every tick, so the forbidden set moves with it.

### Entry and loot

`enter`: verify the loadout (both weapons present, `min_fish` (10), `min_egniol` (1); else
safety-stop with what is missing), activate the expected protection prayer (RANGED first) and
the bow's offensive prayer, then `Quick-pass` the Barrier (`click_menu_action` by name). The
fight phase starts on varbit 9177 == 1 (subscribed) confirmed by a Hunllef in `/npcs`.
`loot`: after the LOADING transition, `Open` the Reward Chest; `done` on the chat line.

### Config (`DEFAULT_CONFIG`, no bridge config section yet)

`eat_at`, `egniol_at`, `min_fish`, `min_egniol`, `offensive_prayers`, `weapons` (bow and staff
name lists), `melee_weapon` ("bow"), `fight_max_s`. `VARBIT_SUBSCRIPTIONS = [9177]`.
`RoutineChoice.CORRUPTED_GAUNTLET` + `ROUTINE_FILES` entry (client rebuild), per CLAUDE.md.

## Error handling

- Snapshot unreadable (bridge timeout, no Hunllef while varbit says fight): skip the tick;
  `NO_HUNLLEF_TICKS` (10) consecutive misses re-derive the phase.
- `PLAYER_DIED` or hp == 0: phase `done`, safety stop "died to the Hunllef" (nothing is lost in
  the Gauntlet; the stop is so the log is read).
- No walkable safe tile: stay put and log `[!] no safe tile`; prefer eating.
- No fish and no potions: keep fighting; log once.
- `ctx.events` None: every wait polls HTTP (`await_outcome`'s fallback); the slot counter runs
  purely on snapshot animation transitions (it may miss a slot if two animations change within
  one tick -- logged, and the 8754 resync covers it once verified).
- `FIGHT_MAX_S` reached: safety stop.

## Testing

Pure, hermetic (`no_live_bridge`-style autouse stub):
- `StyleTracker`: 4 attacks then a marker, ranged first, both markers flip, stomp ignored,
  `next_style_due` window, resync on a short count; a **replay test** feeding the recording's Hunllef animation sequence (copied into the
  test as `(t, anim)` pairs) and asserting each predicted switch falls within 5 ticks of the
  human's real prayer click times (the list above).
- `hunllef_prayer`, `desired_weapon`, `offensive_prayer_for`.
- `pick_safe_tile` on synthetic grids: hazard tiles excluded, footprint + margin excluded (the
  stomp guard), tornado radius excluded, in-range preference, "no safe tile" returns None.
- `ladder(snapshot)` returns the right rung for hand-built snapshots: hazard beats prayer beats
  weapon beats eat beats attack; nothing-to-do returns idle.
- `eat_decision`/`drink_decision` thresholds and the no-fish fallback.
- Phase derivation from hand-built state dicts.

## Live verification checklist (first session, door-start, one attempt per item)

1. `/npcs` for the Hunllef: is `instX/instY` its centre or its south-west tile? (size 5; the
   footprint math depends on it). Also the id flips 9035 -> 9036/9037 as expected.
2. `NPC_TRACKED_ANIMATION_CHANGED` arrives for the Hunllef once we attack it; slot counter's
   predicted switches vs. damage taken (a wrong-prayer hit is 40+).
3. What 8754 marks; the `PROJECTILE_SPAWNED` ids of ranged vs magic attacks (for a future resync).
4. `/objects` shows 36047/36048 with the expected timing (crimson then orange); escape latency.
5. Snapshot cost per tick; whether the ladder keeps up with the 0.6 s tick.
6. Eat cadence and whether a second fish in the same tick is ever worth it.
7. Kill time and fish used vs the human's 4:50 / 48.

## Files touched

New: `ControllerAddon/routines/corrupted_gauntlet.py`, `ControllerAddon/hunllef.py` (the pure
logic: `StyleTracker`, `FightSnapshot`, `pick_safe_tile`, decisions), `tests/test_hunllef.py`.
Modified: `gauntlet_ids.py` (add `HUNLLEF_PRAYER_BY_ID`, `ANIM_SLOT`), `controller.py`
(`ROUTINE_FILES`), `RoutineChoice.java`, CLAUDE.md (new "Current state" section).
