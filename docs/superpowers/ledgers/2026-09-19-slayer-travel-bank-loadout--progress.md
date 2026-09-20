# SDD ledger — plan: docs/superpowers/plans/2026-09-19-slayer-travel-bank-loadout.md
Branch crossfloor-travel (shared with another session's GOTR commits; stage by exact path).
Ruling (in the plan itself): Phase A is coded to the line; Phases B-E are interface-level and get detailed after the previous phase's live run.

## Pre-flight scan (Phase A)
| Tasks | Produces vs consumes | Finding |
|---|---|---|
| A1 -> A4 | slayer_tasks.missing_items(task, count, carried: dict) | consistent with _carried() |
| A2 -> A3 | legs.fairy(code, expect, dest, ring_tile, ring_id) | consistent |
| A3 -> A4 | slayer_routes.plan(task, player, carried, skill_levels=None) -> (legs|None, why) | consistent |
| A4 <-> A5 | both edit routines/slayer.py + its test | sequential |
| A3 claims | VERIFIED on the real map before dispatch: nearest ring from (2700,3321) is BLR (2740,3351), walk cost 69; AJQ exit -> slimes 41 tiles; no ring candidates near (3221,9600) | - |
| A4/A5 | gear.carried_names / worn_names exist | verified |

## Progress
A1: complete — a84cfcb. A2: complete — db88b80. A3: complete — c4fb7eb. (one batched haiku dispatch; verbatim; 11 new tests)
A4: complete — 495c9ab. A5: complete — a15d883. (sonnet) Two PLAN DEFECTS corrected by the implementer, both accepted:
  Ruling: A4 FINISHED branch must not clear the route after the second FINISHED (my snippet re-planned forever past the stop) — accepted.
  Ruling: A5 watchdog walks THEN checks >= NO_TARGET_WALKS (my snippet needed a 4th cycle the test budget never reached) — accepted.
  _no_live_bridge fixture added to test_slayer_routine.py; no existing test was leaking.
Phase A review (Fable): pending.
Phase A review (Fable): FIX FIRST -- 2 Critical (walk cannot start from a ring's own tile; stepping out of the rectangle re-plans the whole teleport route), 3 Important (unreachable finished-twice stop; unlit lantern passes by name; loot override wrote a dead key). All fixed by controller in the commit above, +7 tests incl. two on the real map. Full suite: 3038 passed, 4 failed (mixology baseline).
A6 docs: pending. LIVE GATE: blocked on the account's state -- /slayer now reads Greater Demons x153, streak 19 (a STOP task).
A6: docs complete (commit above). Suite green at the fix wave (3038 passed, mixology baseline). PHASE A LIVE GATE: WAITING -- account holds Greater Demons x153 (STOP task, user does it by hand); no light source carried.
PHASE B detailed 2026-09-20 from live house measurements (phase-B-brief.md; facts also in the plan). B1 3cf6787 poh.py, B2 057b3b7 house legs, B3 504533b plan() grows the house. 183 tests across the touched files pass. Implementer concern: object_refresh_target on TEMPLATE coords inside an instance -- controller's note: MEASURED live that /object/at with template x/y + plane 1 + id answers found/clickable in the house. Also reported: test_crossfloor::test_an_impossible_trip_is_none_and_fast failed once in a full-suite run (8 s wall-clock bound under load) -- watch. Phase B review (Fable): pending.
