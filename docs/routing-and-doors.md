# Routing, doors and walking — measured history

Moved out of CLAUDE.md on 2026-09-14. Nothing here is obsolete: this is the full measured record; the condensed working rules live in CLAUDE.md.

- **Routing engine replaced 2026-08-29: Shortest Path (the separately-installed RuneLite plugin)
  is gone, along with roughly 700 lines of door/reachability heuristics it made necessary.**
  `ControllerAddon/worldmap.py` — an offline, world-wide per-edge collision bitmap covering all
  four planes, ported from EthanApi's `GlobalCollisionMap` — is now the **only** routing engine.
  `travel.route_to`/`travel.walk_to` call `worldmap.find_path` directly (0.4-3ms for an in-house
  route, 151ms for a 314-step cross-city one), no bridge call needed for the route itself, and it
  fixes the exact gap Shortest Path had: it routes on ANY plane, where Shortest Path's own dumped
  collision map was ground level only (see the NORMAN's/JEFF's/ROSS's measurements below, and the
  mahogany_homes.py section for the live session that forced this). The static map bakes doors
  OPEN, deliberately, same as Shortest Path's own dump was (`CacheMapDumper`'s `Exclusion` list
  does it on purpose) — so a computed route runs straight through a doorway, and
  `travel.open_door_toward(ctx, goal)` (built on `first_door_on_route`/`blocked_crossing_on_route`)
  finds and opens whichever shut door the route actually crosses. That one call replaced the
  three-tier escalation described in the bullets below (`_door_separates`/`_within_span`,
  `open_frontier_door`, `open_nearest_shut_door`) — those tiers existed only because there was no
  route to ask; given a real route the door question is nearly trivial and works identically on
  every floor. **What did NOT move onto the static map:** object-interaction adjacency ("can I
  click this hotspot/ladder from here") is still answered from LIVE `GET /collision` flags via
  `travel.can_reach`/`travel.reachable_set`, because the static map bakes an object's own blocking
  into the same bits as a wall's and structurally cannot tell furniture from a wall — see JEFF's
  measurement in the mahogany_homes.py section, still the reason. **Transport execution is gone
  too, not rebuilt:** `worldmap.find_path` has no transport graph at all, so `route_to`'s own
  docstring says plainly "no transports" — a routine that needs a teleport now owns that itself
  (`mahogany_homes.py`'s `CITY_TELEPORTS`, or the standalone `teleport_to_ferox`/`use_fountain`
  already in `travel.py`). The bullets below describe the Shortest-Path-era machinery as it was
  measured and built; keep reading them for the MEASURED game geometry (door ids, coordinates,
  collision flags), which is still true of the game — not for the function names, which have moved
  onto `worldmap.find_path`/`travel.open_door_toward`/`travel.first_door_on_route`/
  `travel.blocked_crossing_on_route`.

- **Historical, pre-2026-08-29 — Shortest Path's own transport graph could route through a
  transport we had no way to execute.** `_use_transport` could only click a single menu option on
  an object or NPC; any teleport, or any transport opening a destination picker (canoes, charter
  ships, minecarts), produced an edge with no clickable `objectId`. CONFIRMED live: a `walk_to`
  from Civitas illa Fortis once returned a route whose first edge was a `QUETZAL_WHISTLE` to a tile
  **454 tiles away**, purely because the user's own Shortest Path panel had quetzals enabled —
  `travel.TRANSPORT_POLICY`, sent with every request, was the fix at the time. Moot now: the new
  routing engine has no transport graph to leak a bad edge from in the first place, and Shortest
  Path's separate sticky, session-global config-override bug (a quirk of that plugin's own
  `PluginMessage` handling) went away with the plugin.

- **Doors: `collisionFlags` + `GET /collision` are the ground truth, and the useful question is
  per-EDGE, not per-door.** `ObjectProvider` reports `collisionFlags` on every object (the client's
  own `CollisionData` entry for that object's tile) and `CollisionProvider` serves
  `GET /collision?x=&y=&plane=&radius=` for ARBITRARY tiles, including empty ones -- needed because
  the doorway you are trying to walk through usually has no object on your side of it.

  MEASURED live by opening Barbara's two doors one at a time and re-reading the SAME doorway:

  | | shut | open |
  |---|---|---|
  | outer | id 7452 @ (1746,3534) `mE\|sE` (4104) | id **7453** @ (1747,3534) `mS\|sS`, doorway tiles -> **0** |
  | inner | id 7452 @ (1748,3537) `mS\|sS` (16416) | id **7453** @ (1748,3536) `mW\|sW`, doorway tile -> **0** |

  Five facts, all confirmed twice and still exactly what `travel.py`'s `DOOR_BLOCKING_FLAGS`/
  `DOOR_EDGE_BLOCKS` encode:
  1. Movement and line-of-sight bits are ALWAYS set together for the same edge (`mE|sE`).
  2. A blocked edge is recorded on BOTH its tiles as opposite directions -- it is a genuine EDGE,
     not a tile property, so either side is enough to know the crossing is shut.
  3. Opening a door CLEARS the doorway edge outright (to 0) and the leaf comes to rest blocking a
     PERPENDICULAR edge.
  4. The door object MOVES one tile and CHANGES ID when it opens (7452 shut -> 7453 open).
  5. A shut door's flags are identical to the plain wall segments beside it (Barbara's read 4104
     either side), so collision can never IDENTIFY a door -- `actions` ("Open" vs "Close") does
     that, and always did.

  **Do not compare two different doors' flags to infer state** -- an earlier pass did exactly that
  at Lumbridge (a shut door and a different open one both read 1026), concluded the flags carried
  no state, and wrote that up as fact. 1026 was the open door's LEAF edge. Comparing one doorway
  before and after is the only comparison that answers anything.

  What the code actually asks is narrower and needs no classification at all
  (`travel.blocked_crossing_on_route`/`first_door_on_route`, which replaced `_door_separates`):
  **is there a step of the computed ROUTE we cannot cross, and is a shut door standing on it?** A
  shut door across the path answers yes; the same door once open answers no, for free -- and
  because the question is asked against a real route rather than a straight-line comparison, it no
  longer misses a door that is off to the side or behind a detour the way the old single-axis
  `_door_separates` test structurally could (this used to need a separate escalation to a computed
  path for exactly that case; a route is now asked for up front, every time). Two traps worth
  knowing: `BLOCK_MOVEMENT_FULL` (2359552) is a COMPOSITE of `FLOOR | FLOOR_DECORATION | OBJECT`,
  so ORing it into a mask makes a bare floor decoration read as an obstruction (caught on
  Lumbridge's Trapdoor, 262144) -- `DOOR_BLOCKING_FLAGS` omits it; and an absent `collisionFlags`
  means "cannot tell", never "no".

- **A door is NOT always a WallObject, and not every door blocks an EDGE.** MEASURED live
  2026-08-29 at TAU's house, Falador, from the tile the routine was stuck on:

      Door id=24050 type=GameObject (3046,3348) d=1 actions=['Open'] flags=131328  <- the way in
      Door id=24050 type=WallObject (3046,3353) d=4 actions=['Open'] flags=4104    <- unrelated

  Same id, different object type -- `travel.DOOR_OBJECT_TYPES` is `("WallObject", "GameObject")`
  for exactly this (the name + "Open" action filter is what keeps it tight: nearby Drawers also
  offer "Open" and are excluded by name). Its collision is different in KIND too: `131328 =
  BLOCK_LINE_OF_SIGHT_FULL (131072) | BLOCK_MOVEMENT_OBJECT (256)`, with no movement edge bits at
  all -- a full-tile blocker like this separates only when its whole destination TILE is refused,
  which is what `blocked_crossing_on_route`'s destination-tile check (not just the edge test)
  catches; the route that exposed it ran (3045,3349) -> (3046,3348) -> (3046,3347) -> (3046,3346)
  straight through the door tile. The line-of-sight bit is the only flag family that marks this
  door at all, which is why LOS is in the mask and not just movement.

- **A hotspot's or ladder's own tile is usually not routable at all** (furniture stands on it),
  and aiming a route at the nearby standing tile instead can hide the doorway you actually need,
  because both the standing tile and the player often sit on the same side of the door. MEASURED
  live at Barbara's: the hotspot tile (1749,3536) itself reported unroutable (furniture) on 5 of 5
  checks while its neighbours routed fine. `travel.route_to(goal, goal_adjacent=True)` -- which
  `open_door_toward` always passes -- routes to a tile BESIDE the target rather than onto it, so
  the door question is resolved against the actual goal (not a nearby stand-in) by construction.
  The same TAU session found `standingLocation` itself can be unwalkable (furniture on
  (3047,3345)) -- another reason routing is asked for against the real goal, not a fallback tile.

- **Historical: three separate heuristic bugs at NORMAN's house (Falador), 2026-08-29, all fixed
  by moving to real routes rather than by patching the heuristics further.** A radius-limited door
  search missed doors standing 6-7 tiles away; a single-axis "is this edge between us and the
  goal" test false-positived on an unrelated `Door 24057` at (3027,3344), seven tiles off the
  direct line; and a walk aimed at `standingLocation` (3038,3344) crossed no doorway because it
  sat on the SAME side of the shut door 24050 (3037,3347) as the player. None of these failure
  modes exist against a real BFS route: `DOOR_ROUTE_LOOKAHEAD` (14 route steps) has no fixed-radius
  blind spot, and a route that actually reaches the goal is definitionally on the correct side of
  every door it opens.

- **Opening a door is CONFIRMED, not slept off, and the confirmation must budget the WALK to the
  door as well as the open itself.** A door is a WallObject whose id encodes its state and changes
  when it swings (shut -> shut+1, e.g. Barbara's 7452 -> 7453, Falador's 24050 -> 24051) and it
  also moves a tile, so the object you clicked is not there afterwards to re-read -- the doorway
  is, so `travel._open_door` polls the blocked EDGE until it clears (`DOOR_CONFIRM_POLL_S`, budget
  `DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S` capped at `DOOR_OPEN_WAIT_MAX_S`) and logs
  `still shut after Ns -- another player may be holding it, or the click missed` otherwise. MEASURED
  live at ROSS's: a flat 2.0s budget was less than the ~2.4s of walking to a door four tiles off,
  so a click that DID register as `Open Door` on id 40178 got reported as a failure and interrupted
  by its own retry, three times over -- fixed by adding the per-tile walk allowance. Also MEASURED
  at ROSS's front door (40178): it swings shut BEHIND you, so re-reading the doorway can answer
  "still shut" about a door that worked -- the confirmation also succeeds on `_crossed()` (the
  player having crossed the door's blocked edge since the click), because getting through is the
  outcome that matters, not the doorway's state at poll time.
  **PARTLY SUPERSEDED 2026-08-30.** A good share of those "still shut" timeouts was not the door
  swinging at all: `_open_door` clicked with a BARE `click_entity` and so aimed at the pixel the
  door occupied when the ROUTE was computed, while the player was still walking toward it. The
  recorder showed every click registering as `Open Door` because the MENU resolved correctly --
  but a stale aim still lands somewhere useless. It now passes `refresh_target`; see "A click's
  DEPARTURE is aimed too" above. Both effects are real; do not assume a "still shut" log means
  the door closed behind you until the aim has been ruled out.

- **ADJACENCY IS NOT INTERACTABILITY, and `goal_adjacent` does not give you a tile you can act
  from.** `worldmap.find_path(goal_adjacent=True)` stops at ANY tile cardinally adjacent to the
  goal — and it never has to cross a wall to get there, so the route is perfectly valid by the
  static map's own rules while the tile it lands on cannot touch the target at all. MEASURED live
  2026-08-30 at BARBARA's: the Bed hotspot at (1749,3539) has its north and east edges walled
  (16416 / 65664) and only its south and west open (135432 / 132354), both inside the bedroom.
  The walker parked on the north tile, `can_reach` went on (correctly) answering False, and the
  routine sat there until its ceiling stopped it. `travel.interaction_tiles(goal)` answers the
  question that actually matters — which tiles have an OPEN EDGE to the target — from live
  collision, and walking to one of those routes through the bedroom door, which
  `open_door_toward` then finds as an ordinary door on the route. Empty means "cannot tell";
  fall back rather than refuse to work.

- **A route can cross a tile that live collision refuses, so `walk_to` re-routes around it --
  EXCEPT when a shut door is standing there.** The static map is not live: it cannot know what is
  standing on a tile right now, and it bakes doors OPEN. MEASURED live 2026-08-30 at TAU's,
  Falador, where the door is DIAGONAL — which the game cannot express as a `WallObject` on a
  cardinal edge, so it is a `GameObject` blocking its WHOLE TILE both open and shut. OPENING it
  therefore does not clear a tile, it MOVES a full-tile blocker: door 24051 ("Close") sat on
  (3046,3347) reading 131841, the route ran straight through, `_hop_index` refused the first
  crossing and the walk died one tile from the door. `worldmap.find_path` now takes `avoid=` and
  `walk_to` feeds it tiles it has learned are really blocked (`WALK_MAX_REROUTES`, 4). The detour
  is normally free — at TAU's it was the SAME 51 steps.
  **The exception is load-bearing and cost a regression to find:** at ROSS's the only way in is a
  SHUT door at (2610,3316), and after the door budget was spent the reroute excluded that very
  tile, leaving no route at all — failing HARDER than before the reroute existed. A shut door is
  to be OPENED, never routed around. `_door_on_route` is the discriminator and it is exact: it
  only ever returns a door offering "Open", so TAU's open leaf ("Close") is avoided and ROSS's
  shut door is not.

- **MINIMAP for distance, viewport only for short precise hops -- and every viewport walk click
  is SHIFT-clicked.** User directive 2026-08-29. `_hop_to` used to prefer a viewport click
  whenever the tile was rendered, with the minimap only as a fallback beyond render distance;
  with `WALK_HOP_MIN/MAX_TILES` at 16-24 that made nearly every hop of a long route a viewport
  click into a scene full of interactive scenery. CONFIRMED live the same day
  (`recordings/2026-08-29_012522_mh-live-run12`): hops resolved as `Chop down` ("You need an
  axe") and `Cook` ("You haven't got anything to cook") instead of walking, which strands the
  player somewhere it did not choose and makes the next pass walk back -- the user saw this as
  clicking back and forth. The minimap has nothing drawn on it to hijack a click; precision is
  what it lacks, and precision is only wanted at the END of a walk (exact-tile work does not come
  through `_hop_to` at all -- `walk_to_exact_tile` handles that, and was the ONLY caller obeying
  CLAUDE.md's own shift-click rule). Now: `HOP_VIEWPORT_MAX_TILES` (6, first draft, wants a live
  tuning pass) decides, and `_click_walk_point` holds SHIFT for the viewport case.

- **The NEXT hop is clicked BEFORE arrival, not after it** (`HOP_NEXT_CLICK_LEAD_TILES`, 6,
  added 2026-08-30). USER: "we wait so long the player runs to the tile we clicked before we
  click the next point resulting in a weird quick stop". MEASURED in that night's recordings
  (runs 23/24): a 0.6-1.8s dead stop at EVERY hop boundary, because `_hop_to`'s wait released
  within `WALK_ARRIVAL_TOLERANCE` (3 tiles ≈ 0.9s of running) while the next click needs
  ~1.5-2s (0.6s poll quantization + walk_to's collision/door bookkeeping + the mouse flight).
  Hops longer than the lead now release the wait with 6 tiles (~1.8s) still to run, so the next
  minimap click lands mid-run — the game just replaces the queued destination, which is what
  the human recordings do. Hops no longer than the lead keep the exact wait (instant release
  would spam clicks at their own destination). VERIFIED live the same night (Ardougne→JESS,
  ~30 tiles): 16 consecutive moving ticks across 2-3 hop boundaries, zero stopped ticks.
- **THE MINIMAP ORBS ARE DRAWN OVER THE MINIMAP CIRCLE AND EAT A HOP CLICK.** MEASURED live
  2026-09-09 (vale_totems): a westward hop's aim point at screen (1966,151) was reported
  `onMinimap: true` by `/minimap/walk` and was the **Hitpoints orb** sitting over the circle's
  left edge; four identical clicks moved the player nowhere and the trip failed. `onMinimap`
  is a CIRCLE test and knows nothing about what is painted on top, and a minimap hop cannot
  use `uiBlockers` -- the minimap rect is itself in that list, deliberately. So
  `GameStateProvider` now also reports **`minimapOrbs`**, one rect per visible orb-sized child
  of the Orbs group (the HP/prayer/run/special orbs and their number boxes, the compass, the
  XP, world-map and wiki buttons -- 46 rects on this client), and `travel._hop_to` rejects a
  minimap aim point inside one and pulls in toward the player instead. Needs a client rebuild;
  an older client answers no rects and behaves exactly as before.

- **The minimap reaches 28 tiles, and the DIAGONALS are what bound it.** MEASURED live 2026-08-28
  from (3037,3345) by walking `/minimap/walk` outward in all eight directions: `onMinimap` held to
  28 everywhere, then failed at 30 on the four diagonals while the cardinals were still on at 32 —
  a corner sits √2× further from the minimap's centre than a cardinal at the same tile distance.
  `WALK_HOP_MIN/MAX_TILES` were raised 10-16 → 16-24 on this measurement (user request: fewer
  stops between clicks, since each hop ends with the player standing still while the next click is
  aimed and flown). 24 keeps margin under 28 because the minimap's reach is a **user setting** —
  zooming it in shrinks it — and `_hop_to` now pulls an unreachable aim point in toward the player
  and retries (`HOP_PULL_IN_FRACTIONS`) instead of abandoning the whole walk, so a zoomed-in
  minimap degrades to shorter hops rather than a failed trip. Re-measure before raising further.

