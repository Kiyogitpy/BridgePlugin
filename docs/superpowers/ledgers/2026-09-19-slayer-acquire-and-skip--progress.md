# SDD ledger — plan: docs/superpowers/plans/2026-09-19-slayer-acquire-and-skip.md
Branch crossfloor-travel (shared with another session's GOTR commits; stage by exact path).
Spec: docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md; measured facts: docs/slayer-sites/recordings-2026-09-19.md

## Pre-flight scan
| Tasks | Produces vs consumes | Finding |
|---|---|---|
| 1 -> 4 | slayer_tasks.disposition/target_names/at_site/where | consistent |
| 2 -> 3 | contact(), dialogue helpers, MASTERS | consistent; T3 appends to T2's two files (sequential) |
| 2,3 -> 4 | get_task(ctx, master, cfg), cancel_task(ctx, master, cfg), missing_requirements(master) | consistent |
| 4 | TaskState.task is LOWERCASED, so stop messages carry lowercase names | plan tests fixed to compare lowercased before dispatch |
| 3 | CONFIRM_NEEDLES had "cancel" -> would match "No, don't cancel" | removed before dispatch |
| each | every test traced by hand against its code by the controller | - |

## Progress
Task 1: implemented — 6c2b859 — 8 tests pass; review running.
Task 1: complete — 6c2b859 + fix (Zombies = undead cow/chicken; new test runs combat_core.resolve_targets over a real NPC name per task). Review: spec OK, CHANGES_REQUESTED -> fixed by controller.
Task 2: implemented — ddd613c — 12 tests pass; review running.
Task 2: complete — ddd613c + fix commit (above in git log). Review CHANGES_REQUESTED: Critical loose "from you" needle; Important re-press on unchanged menu; Important "quantity" vs bridge "qty" (masked by the test's own fixture). All fixed by controller + get_task refuses ANY master with a task active. +3 tests.
Task 3: implemented — f4ee6ce — 6 tests; reviewed in the final review (same file as T2, appended).
Task 4: implemented — c723d1d — 12 tests. Old stub test in test_combat.py replaced (it called tick() unmocked and hit the live bridge).
Final review (Fable): FIX FIRST. Two engine crashes PROVEN on the live bridge (/equipment is a dict; skipTasks is a list), fight_tick self-banking, dialogues left open on failure, rewards re-press, requirement reads before cooldown, counter/comment nits. All seven fixed by controller in cf00e7f, +5 tests, fixtures rebuilt from real GET shapes. Read-only live probe after the fix: state Bears x15, next master AYA, no missing requirement, phase GO_THERE with the Legends' Guild text.
Task 5: complete — 6252460 docs. Full suite: 3009 passed, 4 failed (exact mixology baseline), 1 skipped.
ALL TASKS COMPLETE. Not merged. Not run live.
LIVE 2026-09-19: runs 1-2 failed (cast re-contacts last NPC, no picker; picker not in root walk) -> fixed 8e69498; run 4 PASSED: cave slimes x17 from AYA in 9s, then GO_THERE stop.
