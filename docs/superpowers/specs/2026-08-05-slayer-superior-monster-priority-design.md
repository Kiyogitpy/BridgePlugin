# Slayer: prioritize superior monsters

## Problem

`slayer.py`'s targeting only ever attacks NPCs matching the task's configured
`targets`/`targetNames` (via `pick_target` -> `resolve_targets`). Superior slayer
monsters spawn with a distinct name unrelated to the normal monster's name (e.g.
task target "Nechryael" spawns a superior named "Nechryarch"), so they never
match the config's substring/id filters and the bot walks right past them,
losing out on their bonus Slayer XP and guaranteed unique-drop rolls.

## Detection

Superior names can't be derived from the task config, so they're hardcoded as a
new `SUPERIOR_MONSTER_NAMES` set (lowercased), the same pattern already used for
`PRAYER_LEVELS`. Sourced from the local wiki dump's "Superior slayer monster"
page (`data/raw/S/Superior slayer monster.txt`), which has the full, current
table of every normal -> superior pairing. All 37 superior variant names go in
the set (both variants that share one superior name -- e.g. Nechryael/Greater
Nechryael both spawn "Nechryarch" -- only need the superior name listed once).

## Targeting behavior

No change to fight commitment: `engage_target` already returns early and stays
committed to `current_target_index` until that NPC dies. A superior spawning
mid-fight is not chased -- it's only considered the next time the routine is
free to pick a fresh target (current target dead, or none tracked yet).

At that point, `engage_target` tries a superior first and falls back to the
normal configured target:

```python
target = pick_superior_target(npcs, config) or pick_target(npcs, config)
```

`pick_superior_target` reuses the exact same candidate filters as
`pick_target` -- `attackable`, `maxTargetDistance`, and `skipNpcInCombat` (skip
NPCs already fighting someone else) -- so a superior outside normal engagement
range or already claimed by another player is skipped, same as any other NPC.
No new Controller Settings toggle; this is always on, since superiors only
spawn while already on a task, so there's no scenario where a player wouldn't
want the free XP/loot.

## Refactor

`pick_target`'s loop (attackable check, distance check, in-combat skip,
closest-first sort) is extracted into a shared `_filter_candidates(npcs,
config, matches)` helper, where `matches(npc)` is the only thing that differs
between normal and superior targeting:

- `pick_target`: `npc.get("id") in ids or any(n in npc_name for n in names)`
  (existing substring/id logic, unchanged)
- `pick_superior_target`: `npc_name in SUPERIOR_MONSTER_NAMES` (exact match --
  these are precise, unique in-game names, not task-category labels that need
  substring matching)

## Logging

`engage_target`'s existing debug log line gets a `(superior)` tag when the
picked target came from `pick_superior_target`, so logs make it obvious why a
non-task-list monster is being attacked.

## Out of scope

- Interrupting an in-progress fight to chase a newly-spawned superior.
- A settings toggle to disable this behavior.
- Any change to `on_event`/`NPC_DIED` handling -- a superior becomes
  `current_target_index` exactly like any other tracked target.
