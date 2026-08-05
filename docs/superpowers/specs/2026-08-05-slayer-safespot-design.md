# Slayer: safespot marking and anchoring

## Problem

Ranged/Magic slayer tasks are often fought from a "safespot" -- a tile from which the player has
line of sight to a monster but the monster (usually melee-only) can't path to reach the player,
thanks to a wall, rock, or gap in the terrain. The slayer routine has no notion of this today:
`engage_target` just clicks whatever `pick_target` returns, with no way to guarantee the attack
is only ever initiated from a specific, pre-verified-safe tile, and nothing corrects the player's
position if something (a loot pickup, a monster's pull/drag special) moves them off of it.

## Marking mechanism

A new "Mark Safespot" option gets injected into the ground's right-click menu, alongside the
game's own "Walk here" -- via a `MenuEntryAdded` subscriber that calls `client.createMenuEntry(...)`
(the same technique RuneLite's own Ground Markers plugin uses to add tile-targeted options).
Clicking it is handled by a `MenuOptionClicked` subscriber: the entry carries the clicked tile's
scene coordinates, converted to a `WorldPoint` via `WorldPoint.fromScene(client, param0, param1,
client.getPlane())`, then written into `SlayerConfig` via `configManager.setConfiguration(...)`.

This needs no "arming" step or mode toggle -- the option is always available on any ground
right-click, exactly mirroring "Walk here" being always available. It also never fires
accidentally on a normal walk-click, since it's a distinct menu option the player has to
deliberately select.

## Storage

Three new hidden `SlayerConfig` fields -- `safespotX`, `safespotY`, `safespotPlane` (ints,
`@ConfigItem(hidden = true)` so they're real persisted config values but never rendered in the
Settings panel, the same annotation attribute RuneLite itself uses for this exact purpose) --
plus one visible field, `safespotEnabled` (boolean, a normal "Use Safespot" checkbox, off by
default). All four export through `ConfigExporter` into a `safespot` block in the bridge config,
e.g. `{"enabled": bool, "x": int, "y": int, "plane": int}` (x/y/plane are `null` collectively
until a tile has actually been marked at least once).

This is a single tile, not one per task category -- marking takes one click, so re-marking when
you switch task/location is cheap enough not to need a per-category map (unlike
`SlayerLocations`' hardcoded, rarely-changing wilderness spawn points). If this turns out to be
too annoying in practice, a per-category map is a natural follow-up, not a blocker here.

## Anchoring

A new `maintain_safespot(ctx)` in `slayer.py`, called from `tick()` alongside the other
`maintain_*` functions (`maintain_defensive_prayer`, `maintain_offensive_prayer`,
`maintain_supplies`), before `engage_target`. Every tick, if `safespot.enabled` is true and a
tile has been marked: compare the player's current position to the marked tile; if they differ,
call `travel.walk_to` to return.

Running this every tick (not just before picking a new target) is what catches the actual failure
mode being guarded against -- getting dragged off-tile mid-fight by a loot pickup or a monster's
pull attack, not just at the moment a fresh target gets picked.

## `travel.walk_to` tolerance parameter

`walk_to`'s existing `WALK_ARRIVAL_TOLERANCE` (3 tiles) is tuned for bank-trip-scale travel,
where "close enough" is a reasonable relaxation. It's too loose for combat safety -- 3 tiles can
still be within a melee monster's reach. `walk_to` gains an optional `tolerance` parameter,
defaulting to the existing `WALK_ARRIVAL_TOLERANCE` (so `banker.py`'s current call sites are
unaffected), which `maintain_safespot` calls with a tight value (0-1 tiles) instead.

## Out of scope

- A per-task-category safespot map (see Storage above).
- Any change to `pick_target`/`engage_target`'s targeting logic itself -- safespotting only
  constrains *where the player stands*, not *which monster gets attacked*. A monster that can't
  actually be reached/seen from the safespot simply won't get attacked (the existing `attackable`
  flag and click-resolution behavior already handle that; no new detection needed).
- Any UI for viewing/clearing the marked tile beyond marking a new one (which simply overwrites
  the old one).
