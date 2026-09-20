# SDD ledger — plan: docs/superpowers/plans/2026-09-19-crossfloor-travel-and-slayer-pure-pieces.md

Branch `crossfloor-travel` off `dev` @ 1a134ba; plan committed f4b8d5f.
Spec: docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md
USER WIP in tree, NOT ours: ControllerAddon/routines/guardians_of_the_rift.py (uncommitted). Backup patch in
session scratchpad `gotr-user-wip.patch`. Every implementer is told not to touch, stage or revert it.

## Pre-flight scan

| Tasks | Produces vs consumes | Finding |
|---|---|---|
| 1 -> 3 | `crossfloor.find_route(start, goal, *, skill_levels=)`, `Route.steps`, `Walk.dest`, `Link.step` | consistent |
| 1 -> 4 | same + `Route.cost`, `Walk.tiles` | consistent |
| 2 -> 3 | `legs.run_transport(ctx, step, log=)` | consistent; T3 tests patch it with the same signature |
| 2 <-> 3 | both edit legs.py / test_legs.py | sequential, no overlap; T3 deletes `test_a_walk_leg_walks_to_the_exact_tile` (verified it exists, line 91) |
| 2 | `transports.Step(origin, dest, object_info, skills, items, quests, varbits, varplayers, duration, display)` | matches transports.py:55 |
| 2 | `legs.time.sleep` patch | legs imports time (line 27) |
| 6 | Java `slayerMaxSkips` @Range(0,10) return 3 | matches ControllerConfig.java:842-853 |
| 5, 6, 7 | self-contained pure modules | no shared interfaces |
| 8 | docs only | - |
| each task | tests vs own code | T1 hub tests were MEASURED by spike; T5 arithmetic hand-checked in the test docstring |

No conflicts found. Rulings: none yet.

## Progress
Task 1: complete — b72ac5b — review: spec OK, quality APPROVED. 11 tests, 5s.
  Ruling: reviewer notes find_route's docstring says "Cheapest" but the plane-penalty heuristic is inadmissible (best-first under a budget, by design) — reword the docstring in the final fix wave; behaviour stays — costs nothing if wrong.
Task 2: complete — ced9410 — review: spec OK, quality APPROVED. No findings.
Task 3: implemented — b3d21ae — review pending.
Task 4: complete — e9fdfcf — report tool + docs/slayer-sites/routes.md (no reviewer: a verbatim script whose output I read myself). 28s runtime.
  FINDINGS from routes.md (diagnosed with scratch diag_noroute.py):
  F-A Zanaris ring (2412,4434) and CJQ (3178,2447): flood from the landing = 1 tile. A fairy ring's own tile is object-blocked on the static map, but the player REALLY stands there after the teleport. => crossfloor needs a START tolerance (snap to nearest floodable neighbour). Ruling: fix in crossfloor after Task 3's review, with a test; re-run the report.
  F-B Waterfiends: whirlpool 25274 -> Ancient Cavern is gated `varbit 3759 > 1`; walk_across supplies no varbits so it fails closed. Ruling: not fixed here — sub-project 3's catalog gives that route an explicit transport leg or supplies varbit_values; noted in routes.md header.
  F-C Wyrms (Karuulm dungeon) and Gryphons (Great Conch cave): NO transport row exists in the vendored TSVs for either entrance. Catalog must hand-write those legs (legs.from_transport). Noted.
  F-D Black demons: needs Agility 80 shortcut or the dusty-key gate (not in data as usable). Expected.
Task 3: complete — b3d21ae — review: spec OK, quality APPROVED with two Important design notes:
  R3-2 walk leaf lost exact-tile semantics (walks to the GOAL_TOLERANCE-relocated tile; and a same-plane goal beyond FLOOD_RADIUS now fails where travel.walk_to alone succeeded). No caller builds a walk leg yet.
  Ruling: fix now (controller, fix wave 1): leaf tries the EXACT goal first, relocated tile as fallback; find_route None + same plane -> plain travel.walk_to. Cost if wrong: a walk that stops 12 tiles short — site areas are larger than that.
  R3-3 ~150-230ms find_route per walk leg. Ruling: accept; one plan per link, dwarfed by the walk itself.
Task 5: implemented — 859609b — review pending.
Fix wave 1: c762b77 — crossfloor.walkable_start (F-A), walk_across exact-goal-first + plain-walk fallback (R3-2), docstring (T1 ruling); routes.md regenerated (zygomites now route).
Task 5: complete — 859609b + fix (next line) — review CHANGES_REQUESTED: uncharged glory read as UNLIMITED (Important; my plan's defect), quantity 0 counted as 1 (Important), prefix matching (Minor). All three fixed by controller with 3 new tests.
  fix commit: 1991c97 supplies: an uncharged copy is zero teleports, not unlimited; whole-name matching; zero quantity is zero
Task 6: implemented — d764d01 — review pending.
Task 6: complete — d764d01 + fix 1429e1b — review CHANGES_REQUESTED (guard ignored a configured filler when tasks_cfg omitted; None state allowed contact). Fixed by controller: tasks_cfg required, unknown state refuses. +1 test.
Task 7: complete — 756f273 — review: spec OK, quality APPROVED. 25 registry entries (plan said 26: plan's arithmetic, rats is not duplicated). Minor parked: get("wolf")/get("dwarf") do not resolve; game names are always plural.
Task 8: complete — docs commit (see git log). Full suite: 2954 passed, 4 failed (exact mixology baseline), 1 skipped. gradlew test --offline: exit 0.
NOTE: another Claude session is committing GOTR work onto this same branch (7709b96, 0a25e4f, ...). Not ours; stage by exact path only. They ride along to dev on merge.
Final whole-branch review: pending.
Final review (Fable): FIX FIRST. Critical: short same-plane hops confirmed before they happen (610 links). Important: Link.approach dropped; walkable_start may snap to the origin side. Minor: case-sensitive master compare; exact-goal attempt wasteful. Limits: 13 non-ditch links enter the Wilderness; off-screen and two-state objects fail cleanly; Trolls route unverified.
Fix wave 2: c3ac6b0 — landed()/await_landing (nearer dest than origin), approach passed to run_transport, walkable_start(toward=goal), _undoes guard, enters_wilderness filter, last stretch = crossfloor's tile, master aliases. 128 targeted tests pass. Re-review pending.
Fix wave 2 re-review (Sonnet): FIX WAVE OK. One medium note taken: walkable_start key is now nearness-first, goal as tie-break (commit after c3ac6b0). Parked: Ladder 17386 has origin == dest in the vendored data, so landed() can never be True for it — fails cleanly after 10s, permanently unusable; underground Wilderness x-cap may under-classify the Lava Maze dungeon (false negative).
Full suite after all fixes: 2962 passed, 4 failed (exact mixology baseline), 1 skipped. gradlew test --offline exit 0 (run before fix wave 2; no Java touched since).
ALL TASKS COMPLETE. Branch crossfloor-travel NOT merged: the merge is the user's call, and another session is committing GOTR work onto the same branch.
