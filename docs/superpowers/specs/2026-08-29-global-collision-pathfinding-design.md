# Global collision pathfinding — design

**Date:** 2026-08-29
**Status:** approved, not yet implemented
**Supersedes:** most of the door machinery in `travel.py` documented in CLAUDE.md's Mahogany
Homes sections (the 2026-08-28 and 2026-08-29 entries)

## Problem

Mahogany Homes exposed the limits of the current walking stack, and four separate live
sessions went on patching around them rather than fixing them:

1. **Shortest Path cannot route on plane 1 at all.** MEASURED 2026-08-29: every plane-1
   target returns `active:false, done:false` and never completes; a plane-0 target requested
   while standing upstairs comes back `SEARCH_EXHAUSTED` with a single "step" that is the
   player's own tile. Its dumped collision map is ground level only. So upstairs there is no
   route, `_open_blocking_door`'s route branch has nothing to work with, and `walk_to` falls
   through to the obstacle-blind greedy walker.
2. **Doors are not in any route.** Shortest Path's map treats doors as open and carries no
   transport step for ordinary building doors, so the walker wedges against a shut door and
   `_hop_to` returns True on its timeout regardless — up to 8 minutes of identical hops.
3. **The compensating machinery grew to ~700 lines of heuristics** — `_door_separates`,
   `_within_span`, `reachable_set`, `open_frontier_door`, `open_nearest_shut_door`, a
   three-tier escalation — each tier added after a live failure, each one a proxy for the
   route we could not compute.

The fix is not a better heuristic. It is having a real route, on every plane, before taking a
step.

## Sources

Three reference implementations in `data/decompiled/`, documentation only:

- **`PacketUtils/`** (EthanApi) — `GlobalCollisionMap` + `PathingTesting`. A world-wide
  per-edge collision bitmap and a BFS over it.
- **`Vitalite/api/.../services/pathfinder/`** — a 21k-LOC pathing system: local vs global
  collision, several algorithms, transports with requirements, teleports.
- **`CacheMapDumper-main/`** — the generator that produces Vitalite's map from an OSRS cache
  fetched via OpenRS2.

## Measurements

Everything below was verified against the actual map file, not reasoned about.

**Ethan's map** (`PacketUtils/src/main/resources/.../PathFinding/map`): 3.6 MB gzipped, 78 MB
decompressed, RoaringBitmap portable format (cookie 12346, 11487 containers, 82.2 M set
bits). Packing `x | y<<14 | plane<<28`, bit 30 = east-vs-north, **set = passable**.

| probe | result |
|---|---|
| per-plane bits | p0 10.2 M, p1 22.3 M, p2 24.3 M, p3 25.4 M — upper planes are real data |
| ROSS upstairs (2617,3317,1) | `N=T E=F S=F W=T` — matches the geometry measured live 08-29 |
| Varrock square p1/p2 (void above outdoors) | all passable — **no data defaults to open** |
| Falador door (3037,3347), Barbara door (1746,3534) | all passable — doors baked open |
| ROSS ground hotspot (2617,3318,0) | all blocked — furniture tile |

**Pure-Python performance** (lazy header index over the 78 MB buffer, no full deserialize):

| case | steps | explored | time |
|---|---|---|---|
| load + index | | | 0.13 s |
| ROSS upstairs: player → beside ladder | 4 | 9 | 0.0 ms |
| ROSS: outside grass → inside range tile | 30 | 206 | 0.4 ms |
| Falador bank → Falador house door | 42 | 1 283 | 2.8 ms |
| Falador → Varrock (248 tiles apart) | 314 | 68 236 | 151 ms |

Two of these are cases Shortest Path structurally cannot answer.

**The one case the map gets wrong.** JEFF's upper floor, where the character demonstrably
interacted fine:

```
plane 1, y=3447   .   NES  NESW   NW    .     .
plane 1, y=3446  EW   NEW    NW    .    .    ES
                3238  3239  3240  3241  3242  3243
```

Player (3241,3447) has only N and W open; the hotspot (3242,3446) and both corner tiles are
solid. The map says "cannot interact". The reason is structural: **this map bakes an object's
own blocking into the same bits as a wall's.** A solid object at (3242,3447) clears the
east-edge bit stored on (3241,3447), so furniture and a wall are indistinguishable. The live
`collisionFlags` from `ObjectProvider` *can* separate them — `BLOCK_MOVEMENT_OBJECT` (256)
versus the directional `MOVEMENT_*` bits — which is why the current `can_reach` gets both JEFF
and ROSS right.

Verified against three other live geometries, all matching: ROSS (2617,3319) → (2617,3318)
unreachable, and both ROSS diagonals behind the wall unreachable.

## Decisions

| Question | Decision |
|---|---|
| Scope | Full replacement — the new pathfinder becomes the one routing engine |
| Transports / cross-plane | Routine owns them. The walker is walk-only. |
| Where it lives | Python, `ControllerAddon/worldmap.py` |
| Map source | Ethan's map now, behind a pluggable indexer |

**Where it lives — why Python.** The measurements say Python is fast enough by two orders of
magnitude for the cases that matter. A Java provider would add our first runtime dependency,
~90 MB to the *client's* heap, and a client rebuild + restart per iteration — and the
debugging loop is the actual constraint here. The door test needs only fields `ObjectProvider`
already serves (`objectType`, `actions`, `collisionFlags`, `x/y/plane`), so this needs **no
client rebuild at all**.

**Map source — why Ethan's.** Vitalite's `map.dat` is a Java-serialized custom `SparseBitSet`,
impractical from Python. CacheMapDumper writes RoaringBitmap + gzip, which *is* readable, but
uses a different layout (12-bit X base 480, 14-bit Y, 2-bit plane) and **inverted polarity —
set = blocked**. Isolating packing and polarity behind one indexer makes switching a ten-line
change. The two maps must never be mixed.

## Architecture

### `ControllerAddon/worldmap.py` (new)

Pure, offline, no bridge access. `data/collision-map.gz` committed alongside it. Lazy
singleton load, ~78 MB resident in the controller process only.

- `class Indexer` — packing and polarity in one place: `packed(x, y, plane)`, `EDGE_FLAG`,
  and whether a set bit means passable or blocked. Swapping map source touches only this.
- `passable(x, y, plane, dx, dy) -> bool` — one edge bit.
- `find_path(sx, sy, plane, gx, gy, *, goal_adjacent=False, max_nodes=250_000)` →
  `list[(x, y)] | None`. Cardinal BFS, **same plane only**. `None` is a first-class answer
  meaning *no walking route exists* — today that case wedges the greedy walker for minutes. A
  cross-plane request returns `None` immediately.
- `reachable_from(x, y, plane, *, radius, max_nodes)` → `dict[(x, y), distance]`. One bounded
  flood, replacing N separate reachability questions.

The BFS is cardinal-only, matching the map's edge model. The client walks its own diagonals,
so a cardinal path is longer than the real walk — which is why hop selection uses distance,
not step index (below).

### Division of responsibility

This is the load-bearing part of the design, and it mirrors Vitalite's own split
(`LocalCollisionMap.all()` falls back to the global static map for ordinary coordinates and
uses live flags only for instanced ones):

| Question | Answered by |
|---|---|
| Where do I walk? | `worldmap` — static map, any plane |
| Which door is in the way? | route + live `/objects` |
| Can I interact with this object from here? | **`travel.can_reach` — live `collisionFlags`, unchanged** |
| How do I change floor? | `mahogany_homes._climb`, unchanged |
| How do I cross a region? | `CITY_TELEPORTS`, unchanged |

`can_reach` must **not** move onto the static map. It is the only thing that gets JEFF and
ROSS both right, and the JEFF measurement above is exactly why.

### Doors

Vitalite's `handlePassThroughObjects` is simpler than anything we built and covers both of our
measured door shapes:

> when the next step isn't reachable, find an object named `door`/`gate`/`curtain` (excluding
> `trapdoor`) standing **on the player's tile or on the next step's tile**, and interact with
> it. If there's none, `repath()`.

No orientation, no edge-bit matching, no span tests. Barbara's edge doors and Falador's
`GameObject` 24050 both sit *on* a route tile, so both are covered.

Two deliberate deviations from Vitalite:

1. **We keep the `Open`-action check.** Vitalite matches on name only and interacts with
   action index 0. CLAUDE.md MEASURED that left-clicking an *open* door **closes** it, turning
   a working walk into a wedged one. A shut door and an open one are both `WallObject`s named
   `Door` with per-door ids (Falador 24050 shut / 24051 open); only the action separates them.
2. **We take `curtain`** into the name list, which we did not have.

`travel.first_door_on_route(route, objects, collision)` — walk the route, find the first step
pair whose crossing is blocked per *live* collision, then return the door/gate/curtain object
with an `Open` action standing on either of those two tiles.

Opening and confirming stay exactly as they are; they were measured and are correct:

- the `DOOR_OPEN_WAIT_S + tiles × DOOR_WALK_ALLOWANCE_S` budget capped by
  `DOOR_OPEN_WAIT_MAX_S` (a door is opened by *walking* to it and then opening it);
- success on `_crossed()` as well as on the doorway clearing, for ROSS's self-closing door;
- `DOOR_MAX_OPENS_PER_WALK`.

### The new `walk_to`

1. `route = worldmap.find_path(player → target)`.
2. `None` → return False immediately and loudly. No greedy fallback.
3. Resolve the first door on the route, open it, recompute.
4. Hop along the route.
5. On a stall: re-check the remaining route for a door; else recompute from the current tile.
   Bounded by the existing `WALK_MAX_HOPS`.

Two details that are not cosmetic:

- **Hops are chosen by Chebyshev distance from the player, not by step index.** A cardinal BFS
  path is longer than the diagonal walk the client performs, so index-based hops would
  silently shrink to ~14 tiles against the measured 16–24 window.
- **Hops stop at the door.** Borrowed from Vitalite, which extends a hop only as far along the
  path as stays reachable. Clicking *past* a shut door is what produced the wedge-and-re-click
  loop.

`_walk_greedy` and `SHORT_HOP_TILES` go away entirely. A route costs 0.4 ms, so there is no
reason to keep an obstacle-blind walker whose documented failure is wedging against walls it
cannot route around. This is the biggest behavioural win and also the biggest risk: it changes
every short walk in all five walking routines.

## What retires

**`travel.py`:** `_door_separates`, `_within_span`, `_find_blocking_door`,
`_shut_doors_in_range`, `_routable_goal`, `reachable_set`, `open_frontier_door`,
`open_nearest_shut_door`, the three-tier door escalation, `_request_computed_path`,
`_result_is_for`, `transport_config`, `TRANSPORT_POLICY`, `_use_transport`,
`_transport_is_executable`, `_find_transport_object`, `_find_transport_npc`,
`_find_next_transport_index`, `_is_short_hop`, `_walk_greedy`, and the associated
`DOOR_SEARCH_RADIUS` / `DOOR_SPAN_MARGIN` / `DOOR_ROUTE_*` constants.

**Java:** `ShortestPathProvider.java` and its registration.

**`controller.py`:** the 5 shortest-path lines.

**Kept untouched:** `_hop_to`, `_click_walk_point` (minimap/viewport split, shift-click),
`walk_to_exact_tile`, `_crossed`, `maybe_approach_before_click`, `can_reach`,
`GET /collision`.

## `mahogany_homes.py`

- The three-tier door escalation at lines 855 / 937 / 1081 collapses to one
  `travel.open_door_toward(ctx, goal)`.
- `reachable_set` (lines 768, 1076) → one bounded `worldmap.reachable_from` flood;
  `pick_next_hotspot`'s 0/1/2 ranking becomes a dict lookup instead of seven separate
  questions. Ranking semantics are unchanged: reachable here (0) > another floor (1) > this
  floor but walled off (2), and rank 2 is still *returned* when nothing better exists, so a
  walled-off contract reaches the door/walk recovery rather than turning in unfinished.
- `can_reach` call sites (lines 423, 852, 1077) are unchanged.
- `_climb` keeps its ladder choice and its reachability gate; only its door resolution moves
  to the route-based call.

## Testing

`worldmap` touches no bridge, so for the first time this is testable offline against real data
rather than against wherever the character happens to be standing.

- **Asset regression:** the per-plane bit counts, as a corruption check on the 3.6 MB file.
- **Pinned geometries:** ROSS upstairs edges, Varrock void all-passable, both doors baked
  open, ROSS hotspot tile solid — the table under *Measurements* becomes the fixture.
- **Routing:** the four benchmark cases with expected step counts, plus a perf bound on the
  cross-city route so a future BFS change cannot silently regress it.
- **`first_door_on_route`:** a pure function over a fake route and a fake object list, covering
  the edge-door shape, the full-tile-door shape, and the open-door-must-not-be-clicked case.
  This removes the documented flakiness where door tests fell through to the live bridge and
  passed or failed depending on the player's position.
- The `_no_live_bridge` autouse fixtures stay.

## Risks

1. **Void-is-passable on upper planes.** Planes 1–3 default to passable where the dump has no
   data, so a missing exterior wall upstairs lets BFS leak through the void and take a
   shortcut. Mitigation: reject any plane > 0 route that leaves the loaded scene, plus the
   existing stall detection. Accepted.
2. **The map is a static snapshot.** A game update that moves a wall yields a wrong route; the
   symptom is a stall at one specific tile. Mitigable later by dumping our own (below).
3. **Object blocking is baked in** — the JEFF finding. Mitigated by keeping `can_reach` on live
   flags, and by always routing to a walkable tile, never to an object's own tile.
4. **Every walking routine changes at once.** 39 `walk_to` / `walk_to_exact_tile` call sites
   across `chinchompa_hunting`, `falconry`, `golem_crafting`, `mahogany_homes`, `slayer`.
   Failure moves from "wedge for 8 minutes" to "return False in milliseconds" — safer, but any
   routine quietly relying on the greedy walker eventually succeeding will now fail visibly.
   All 39 sites get audited as part of the work.

## Deferred, deliberately

**Dumping our own map** (`CacheMapDumper-main`). It writes RoaringBitmap + gzip — the format we
already read — pulls caches from OpenRS2, and packs **up to 5 data bits per tile with only two
used** (bit 0 = north blocked, bit 1 = east blocked). Three spare bits, world-wide.
`Dumper.processRegion` already reads `getWallOrDoor()` and `getInteractType()` and classifies
wall orientation, so a "door on this tile" bit and an "object-blocked, not wall-blocked" bit
would let the static map answer both the door question and the JEFF question with no live
query. Not now: those bits would replace a live check that currently works, and validating a
regenerated world map is its own piece of work. The `Indexer` boundary exists so this stays a
ten-line change.

Note also that `Exclusion` is a hand-curated doors-open list (guild doors, city gates, arches,
prison doors), which confirms doors-passable is a deliberate decision in both maps rather than
a dump artifact.

**`transports.json`** (Vitalite, same resources folder) — plain JSON with fairy rings, spirit
trees, canoes, minecarts and charters, each carrying explicit `Requirements`. If the walk-only
decision is ever revisited, this is a far better source than Shortest Path's plugin-coupled
TSV.

**`rl-suite-main`** — deobfuscates a RuneLite injected client and publishes packet tables for
revisions 235–240 as TSV. Available, not adopted. The deobfuscation half could help read client
internals; the packet half should be treated with care, because this project drives a physical
Arduino over serial *specifically* so that input is not software-injected. Using packets for
interaction would reverse that decision, and that is a call for the user to make explicitly,
not something to reach for while fixing pathing.
