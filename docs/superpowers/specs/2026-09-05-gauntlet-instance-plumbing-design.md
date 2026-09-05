# Gauntlet sub-project 1: instance plumbing -- design

Date: 2026-09-05. Status: approved in chat, awaiting written review.

The Corrupted Gauntlet routine is three sub-projects: (1) this one, the bridge and Python
primitives every instanced activity needs; (2) the Hunllef fight, designed from
`recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1.*`; (3) the maze prep. Nothing in
(1) depends on a fight decision, which is why it is built first and on its own.

## Goal

Inside an instance, let a routine do the four things it can do everywhere else today: walk to
a tile, click an entity it resolved from the bulk lists, read whether an NPC is praying and
which prayer, and react to a spawn or a projectile from the UDP stream instead of a poll.

## Non-goals

- Any Gauntlet-specific behaviour (attack counting, tornado dodging, resource choice). That is
  sub-project 2/3, and it will consume what this delivers.
- Fixing the instanced case for endpoints the Gauntlet does not need (`/players`, `/golem`).
  They keep the CLAUDE.md caveat.
- Regenerating `worldmap.py`. The static map is irrelevant inside an instance by nature.

## Facts this rests on

- **The forward conversion is only broken for CORRECTED coordinates.** Our providers report
  `WorldPoint.fromLocalInstance` (template coordinates, the ~1900/5600 range in the Gauntlet
  recording), and `LocalPoint.fromWorld` cannot invert that. But the client's own scene base in
  an instance IS the instance space: `WorldPoint.fromLocal` (base + scene, the ~13000 range Pest
  Control used to show) round-trips through `LocalPoint.fromWorld` because `isInScene` compares
  against the same base. Verified by reading `LocalPoint.fromWorld`/`WorldPoint.isInScene`
  (`data/decompiled/osrs-wiki-cache-utils-main/runelite/runelite-api/.../coords/`). So the fix
  is a second coordinate space on the wire, not a new projection.
- **Instance-space coordinates are unique by construction** (one per scene tile). Template
  coordinates are not guaranteed to be: an instance may place the same template chunk twice.
  Whether the Gauntlet does is irrelevant once routines key on instance space.
- **The whole Corrupted Gauntlet is one region, 7768, on plane 1** (recording: 1226 records in
  7768, 25 in the Prifddinas lobby 12127, nothing else). One region fits inside the 104x104
  scene, so the scene does not reload during a run and instance-space coordinates are stable for
  the whole run. Regular Gauntlet is region 7512 (ZombieLite `RegionId`), same shape.
- **`CollisionProvider` already resolves an explicit tile by scanning the scene** for the tile
  whose corrected coordinates match, precisely so it keeps working in an instance. Its
  `MAX_RADIUS` is 25, which does not cover a 64-tile region from a corner.
- **`Actor.getOverheadSpriteIds()` is public API in runelite-api 1.12.38** (javap). ZombieLite
  reaches the same `short[]` by reflection into the injected client and indexes
  `HeadIcon.values()` with element 0 (`NpcsHook.getOldHeadIcon`).
- **Existing UDP events**: `NPC_DIED`, `NPC_TRACKED_ANIMATION_CHANGED`, `HITSPLAT`,
  `GRAPHIC_CHANGED`, `OBJECT_SPAWNED/DESPAWNED`, ... No NPC spawn, no projectile.
  `spawnEventBudgetAvailable()` caps spawn-type events at 40 per tick across all types.
- Gauntlet ids from ZombieLite (`C:/Users/Kiyo/Documents/ZombieLite/.../data/`): Hunllef NPCs
  9021-9024 crystalline / 9035-9038 corrupted; tornadoes 9025 / 9039; animations 8419 attack,
  8754 switch style, 8755 tornado special, 8418 prayer special, 8420 stomp, 8753 spawn;
  projectiles 249 bow, 1720/1723 staff, 1713/1714 prayer attack; hazard ground objects
  36047/36048; fight varbit 9177.

## Design

### 1. Instance-space coordinates everywhere a position is reported

Every provider that emits `x`/`y` for an entity or tile ALSO emits `instX`/`instY`, the
uncorrected `WorldPoint.fromLocal(client, localPoint)` (base + scene). Outside an instance the
two pairs are equal, so a routine may use `inst*` unconditionally. Providers: `PlayerProvider`
(`/player`, plus `inInstance`, `baseX`, `baseY`), `NPCProvider`'s shared builder (`/npcs`,
`/npc/nearest`, `/npc/by-index`), `ObjectProvider` (`/objects`, `/object/at`),
`GroundItemProvider` (`/ground-items`, `/ground-item/at`), `CollisionProvider` tiles. One helper,
`RuneLiteUtil.instancePoint(client, localPoint, plane)`, so every provider computes it the same
way. Existing fields keep their meaning; nothing is renamed.

### 2. Instance-space input on the endpoints that take a tile

`/ground/walk`, `/minimap/walk`, `/object/at`, `/ground-item/at` and `/collision` accept
`inst=true`, meaning `x`/`y` are instance-space. Resolution is arithmetic, never
`LocalPoint.fromWorld`: `sceneX = x - wv.getBaseX()`, `sceneY = y - wv.getBaseY()`, refuse
outside 0..103, then `LocalPoint.fromScene(sceneX, sceneY, wv)` for the walk points, and a
direct `tiles[plane][sceneX][sceneY]` for the rest. `/object/at` and `/ground-item/at` match on
`instX`/`instY` of the already-corrected bulk list, keeping their documented design.

`/collision` gains `all=true`: the whole plane's flags as one compact grid,
`{"baseX", "baseY", "plane", "size": 104, "flags": [row-major int list]}`, about 60 KB, so a
routine loads a room-scale map in one request instead of 25-tile squares. The per-tile form is
unchanged.

### 3. NPC overhead prayer

The NPC builder adds `overheadPrayer`: `HeadIcon.values()[ids[0]].name()` when
`getOverheadSpriteIds()` is non-empty and the index is in range, else `null`; the raw first id
rides along as `overheadPrayerId` so a mismatch is diagnosable rather than silent. Live
verification against the Hunllef decides whether the index is a `HeadIcon` ordinal (ZombieLite's
reading) or a sprite id; the raw field is what makes that check possible.

### 4. UDP events

- `NPC_SPAWNED` / `NPC_DESPAWNED` from `NpcSpawned`/`NpcDespawned`: `{id, index, name, x, y,
  instX, instY, plane}`, under the existing per-tick spawn budget (a scene load replays every
  NPC, same flood as objects).
- `PROJECTILE_SPAWNED` from `ProjectileMoved`, emitted once per projectile. `ProjectileMoved`
  fires every client tick for the projectile's whole flight, so a `ProjectileSeen` helper (an
  identity set backed by a `WeakHashMap`, pruned as projectiles are collected) decides "first
  sighting". Payload: `{id, remainingCycles, targetIndex (the projectile's interacting actor's
  index, -1 if none), x, y, instX, instY, plane}` for the target point. Under the spawn budget
  as well.
- These are accelerators, per the routine contract: sub-project 2 confirms on `/npcs` and
  `/npc/by-index` before acting.

### 5. `ControllerAddon/instance_nav.py`

The routing engine for instances, deliberately small and offline once loaded:

- `InstanceMap.load(plane)`: one `/collision?all=true` read into a 104x104 flag grid keyed by
  instance-space coordinates. `passable(x, y)`, `edge_open(a, b)` using the same cardinal
  movement bits `travel._edge_open` uses (both tiles' flags, opposite directions); a diagonal
  step needs both orthogonal corner routes open (the game's own rule, the conservative one).
- `find_path(map, start, goal, goal_adjacent=False, avoid=None)`: BFS, 8-directional, returns
  a list of instance-space tiles or `None`. No transports, no doors: the Gauntlet has neither.
- `walk_to(ctx, x, y, tolerance=WALK_ARRIVAL_TOLERANCE)`: reloads the map, routes from
  `/player`'s `instX/instY`, and hops along the route with the same lead-click pattern
  `travel._hop_to` uses: minimap for anything beyond `HOP_VIEWPORT_MAX_TILES`, a shift-clicked
  viewport point for the last short hop, both through `travel._click_walk_point` so the blocker
  opt-out rules are inherited, with `inst=True` on the point lookups. Re-routes around a tile
  that refuses the crossing (`avoid`), bounded like `travel.WALK_MAX_REROUTES`.
- `controller.py` wrappers gain `inst: bool = False`: `get_ground_walk_point`,
  `get_minimap_walk_point`, `get_object_at`, `get_ground_item_at`, `get_collision`, and a new
  `get_collision_grid(plane)`.

### 6. `ControllerAddon/gauntlet_ids.py`

The id table above plus the object ids the recording's summary lists (Barrier 37337, The
Gauntlet 37340, Reward Chest 37341, resource nodes), each with its source in a comment, so
sub-project 2 never re-derives them. Pure data, one test that the sets are disjoint and complete
against the recording's `.summary`.

## Error handling

- Outside an instance every `inst*` field equals its `x`/`y` twin and `inst=true` is harmless.
- A tile outside the loaded scene answers `onScreen`/`onMinimap` false, `found` false, or an
  empty grid; never a 500. Empty means "cannot tell" and `walk_to` returns False rather than
  clicking.
- `overheadPrayer` null means "no icon or unreadable"; sub-project 2 must treat it as unknown,
  not as "no prayer".
- Dropped spawn/projectile events past the budget are intended; the HTTP fallback covers them.

## Testing

- Java: `ProjectileSeenTest` (first sighting once, second sighting suppressed, a new projectile
  object seen again), `CollisionGridTest` for the row-major encoding against a mocked flags
  array, and a `RuneLiteUtil` unit for the scene-bounds check. No test touches a live client.
- Python: `tests/test_instance_nav.py` -- BFS on synthetic grids: straight corridor, a wall
  edge that blocks only one direction, the diagonal corner rule, `goal_adjacent`, `avoid`,
  unreachable returns `None`; `walk_to` against a stubbed bridge (autouse `_no_live_bridge`
  pattern from `tests/conftest.py`). `tests/test_gauntlet_ids.py` as above.
- `python -m pytest tests/` and `./gradlew.bat test` both green before the live checklist.

## Live verification checklist (first session)

1. Enter any instance -- a player-owned house is the cheapest -- and read `/player`:
   `inInstance` true, `instX` differs from `x`.
2. `/ground/walk?inst=true` on the player's own `instX/instY` answers `onScreen` true;
   `instance_nav.walk_to` crosses one room.
3. In the Gauntlet: `/collision?all=true` returns the room layout; a route from the start room
   to a neighbouring room walks.
4. At the Hunllef: `overheadPrayer` reads `RANGED`/`MAGIC`/`MELEE` and flips with its switch
   animation (8754); if not, `overheadPrayerId` says what the index really is.
5. `PROJECTILE_SPAWNED` arrives once per Hunllef attack; `NPC_SPAWNED` fires for a tornado
   (9039) within a tick of it appearing in `/npcs`.

## Files touched

Java: `RuneLiteUtil`, `PlayerProvider`, `NPCProvider`, `ObjectProvider`, `GroundItemProvider`,
`CollisionProvider`, `CombatProvider`, `ControllerPlugin` (+ `ProjectileSeen`), tests. Python:
`controller.py`, new `instance_nav.py`, new `gauntlet_ids.py`, tests. Docs: CLAUDE.md's
instancing gotcha gains the resolution.
