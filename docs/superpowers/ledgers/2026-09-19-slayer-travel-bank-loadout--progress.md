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
Phase B review (Fable): FIX FIRST -> fix wave f4aac3a (the house from /player alone, one tablet per unconfirmed landing, a box hop confirmed by leaving the house). Live gate B: WAITING on the account (same Greater Demons task as A).
2026-09-20 (desktop session, resumed from docs/slayer-handoff.md): bridge up on the HOST (8080); /slayer still Greater Demons x133, streak 19, 24 points (cannot cancel: 30 needed) -- gates A and B stay blocked, so E-pre was taken (needs nothing from the user).
E-pre: complete -- controller, inline TDD (11 new tests, all watched fail first). Microbot @ 8bcc652 sparse-cloned to the scratchpad; tools/vendor_microbot_tables.py copies 9 TSVs byte-identical to vendor/microbot/ (VENDORED.md pins commit + sha256); transports.load_links merges Microbot rows by (origin, dest, object id) AFTER ours (ours never replaced/reordered, pinned); Microbot object cell `Option;Target;id` normalised to our space form at load; numeric Item IDs ((A or B) and C) and Currency fail closed via usable(owned_item_ids=, currency_held=); travel.is_door_name/door_walk_action/DOOR_ACTION_PRIORITY ported from Rs2DoorClassifier (fence whole-word), first_door_on_route uses is_door_name.
  MEASURED at vendoring: 5731 Microbot rows / 5168 ours / 5043 shared / 649 only Microbot / 99 only ours; 485 shared keys differ only in requirement cells (mostly Duration) -- ours wins by design.
  Ruling: the same physical edge can appear TWICE with different ids (Al Kharid toll gate: ours `Open Gate 44050`, Microbot `Pay-toll(10gp);Gate;2786`); kept both -- the executor finds objects by id so the stale one fails cleanly. Cost if wrong: one wasted leg attempt.
  Ruling: three plan-authored test facts were wrong and corrected from the data, not the code: ours ALREADY holds the dusty-key gate under the same key (symbolic `DUSTY_KEY=1`), so the merge keeps ours and Microbot's numeric copy is pinned via load_microbot_links; the toll gate's forward id is 2786 (2788 is the return row).
  routes.md regenerated: Wyrms ROUTES (elevator 34359 -> Climb Rocks 34544 -> site, cost 98); Gryphons still no cave row; Waterfiends still varbit-gated; Black demons dusty key still fails closed (no owned ids supplied). Mutated zygomites walk reads 152 tiles where the old report said 150: CHECKED, the jar-only table answers 152 too, so it is the walkable_start change of 18cc37b (nearness first) landing after the last regeneration, not the merge.
  Bank memory checklist (CLAUDE.md), from the live bridge: #6 accountHash non-null (8783289279948231245) and a file exists under it; #5 NOT distinguishable this session -- `source` reads `live` at capturedTick 19, so the container fired on login before any bank visit; the reload-from-file path is still unobserved.
