# Shipwreck Salvaging routine

## Problem

We want a new automation routine for Shipwreck Salvaging, a low-intensity
Sailing gathering activity: sail near a shipwreck, deploy a salvaging hook
built on your own boat, and it periodically rolls for salvage until the wreck
despawns, at which point another rises nearby in the same hotspot.

Structurally this looked at first like `mining.py` (click nearest matching
object while idle, repeat), but two things make it materially different from
every existing routine:

1. **The interaction target isn't the wreck.** The shipwreck object only has
   an `Inspect` option (confirmed live against the game cache, see
   "Confirmed facts" below) -- the real interaction is the player's own
   Salvaging hook facility object, built on their boat.
2. **There's no foot-pathing, and steering is coarse.** Every existing
   routine (`mining.py`, `gemstone_crab.py`, `travel.py`'s walk helpers)
   assumes the player can reach a target by clicking it and letting the
   game's own click-to-walk handle the approach. On open ocean there's no
   walking at all -- moving the boat requires an explicit helm -> steer ->
   stop sequence, and (per the user's firsthand testing) minimap clicks do
   drive the boat once the helm is engaged, but the heading only snaps to a
   handful of coarse compass directions rather than plotting a precise route
   the way normal walking does.

## Scope

1. A new `routines/shipwreck_salvaging.py`: deploy the salvaging hook near a
   configured shipwreck tier, drop full-inventory salvage and keep going
   (no bank trips, no sorting-at-a-station in v1), reposition the boat via a
   new `sailing.py` helper when nothing is in hook range.
2. A new `sailing.py` (parallel to `travel.py`): boat-specific navigation --
   helm interaction, coarse minimap-based steering (reusing `travel.py`'s
   existing hop-and-click mechanism, not a bespoke steering widget), stopping
   via the Sailing Options panel.
3. A new `SailingProvider.java` exposing whatever the Python side needs that
   existing providers don't already cover -- in practice this is now mostly
   just the Sailing Options panel's Take-down-sails control, since steering
   input turned out to reuse the existing generic minimap-click machinery
   (see "Confirmed facts" and Design section 2 below).
4. Wiring: `ROUTINE_FILES` in `controller.py`, a new `SHIPWRECK_SALVAGING`
   entry in `RoutineChoice.java`.
5. Explicitly NOT in scope: crew/crewmate management, sorting salvage at a
   station, hazardous-waters boat-requirement checks, banking trips, relocating
   between separate hotspots (only repositioning within one).

## Research method and confidence levels

Two research passes fed this design, with different confidence levels --
called out explicitly per fact below rather than presented as uniformly
solid:

- **OSRS Wiki** (`data/raw/S/Shipwreck salvaging.txt`, `Helm.txt`,
  `Sailing Options.txt`): mechanics, XP/salvage tables, general UI
  description. Treated as reliable for game mechanics, but it doesn't
  describe RuneLite widget/object IDs at all.
- **RuneLite's generated ID constants**, read two ways:
  - A local source checkout (`C:\Users\Kiyo\IdeaProjects\runelite`, pinned to
    commit `e7e037cf8` / `runelite-parent-1.12.17-10`, dated 2026-02-19) --
    about 5 months behind the actual live client (1.12.33, 2026-07-16). Used
    for `InterfaceID`/`ObjectID1` constant names and values.
  - **Live-cache spot checks** via abextm's cache2 viewer
    (`https://abextm.github.io/cache2/#/editor`), which runs small
    TypeScript snippets against the actual current game cache in-browser.
    This confirmed or corrected several of the source checkout's object IDs
    directly against live data (see "Confirmed facts" below) -- these are
    the most trustworthy facts in this whole document. Full research notes
    saved to `data/raw/S/Sailing boat navigation - RuneLite IDs.txt` for
    reuse.
  - cache2's loader library (`@abextm/cache2`) only supports `animation,
    dbrow, dbtable, enum, index, item, healthbar, hitsplat, npc, obj, param,
    sprite, struct, underlay, worldentity` -- **no interface/widget loader
    exists**, so none of the interface (widget panel) layout facts below
    could be cross-checked this way. Those remain best-effort from the stale
    source checkout only.
- **The user's own firsthand play experience**, for the parts no static
  cache data can reveal at all (the exact click sequence to steer and stop
  the boat, and that clicking away from a deployed hook retrieves it).

## Confirmed facts (live cache2 spot check)

Object names and menu options (`ops`), sampled directly against the live
cache:

| Object | Name | ops |
|---|---|---|
| `BOATKIT_HELM01` (59928) + reskins | "Ship's wheel" | *(none in the static def -- see note)* |
| All 8 shipwreck tiers (60464-60479, active variant) | e.g. "Small shipwreck" | `Inspect` only |
| All 8 shipwreck tiers (`_STUMP` variant) | same name as active | *(none)* |
| Salvaging hook, all tiers/sizes sampled (60480-60510) | e.g. "Bronze salvaging hook" | `Deploy`, `Hidden` (conditional, unresolved), `Modify` |
| Salvaging station, on-boat facility (59699-59701) | "Salvaging station" | `Sort-salvage`, `Modify` |

Implications:
- **Wreck names match the wiki's tier names exactly** ("Small shipwreck",
  "Fisherman's shipwreck", etc.) -- safe to use directly in a `wreckNames`
  config list.
- **Sinking swaps the object out entirely** (distinct `_STUMP` object id,
  confirmed both tiers keep the same *name* but the stump has no ops at
  all) -- a routine re-scanning `get_objects()` for the tier name will
  naturally stop matching a sunk wreck without needing a separate
  "is it still active" flag.
- **The helm has no static ops at all.** The wiki's "Navigate"/"Escape"
  options are presumably added by a runtime script (conditional on being
  aboard your own boat), not baked into the object definition -- this is a
  known, common pattern for context-sensitive objects and isn't unusual, but
  it does mean a plain `click_entity` is relying on the game's own
  resolution the same way every other routine already does (Forced Actions
  as the fallback, same as `gemstone_crab.py`'s tunnel or the ring of
  dueling).
- **The salvaging hook, not the wreck, is what gets deployed.** Confirms the
  design pivot described in Scope above.
- Only the tutorial hook (`SAILING_INTRO_SALVAGING_HOOK`, 60480-60482) has a
  distinct deployed/idle object-id pair (`_NOOPS`/`_OPS`) the way shipwrecks
  do for sunk/active. The real tiered hooks (60483-60510) all show the same
  `Deploy` op regardless -- no "already deployed" object id was found for
  them, so whatever visual/interactive state change happens on deploy isn't
  visible from static cache data. This is why the routine tracks "did we
  deploy" itself (`ctx.state.data`) instead of trying to detect it from the
  object.

## Confirmed facts (from the user's firsthand play)

- Moving the boat: click the helm ("Ship's wheel") to enter steering mode,
  then steer toward the destination.
- **Steering itself reuses the existing minimap-click input** -- clicking a
  point on the minimap while the helm is engaged does move the boat, the
  same click mechanism `travel.py` already uses for foot-pathing
  (`get_minimap_walk_point` + `click_entity`). The difference from walking:
  heading only snaps to a handful of coarse compass directions (the user
  observed N/NE/E/SE/S/SW/W/NE-ish increments; finer sub-steps like NNE are
  unconfirmed) rather than plotting a precise line to the clicked point.
  This means `sailing.py` can reuse `travel.py`'s existing hop-and-click
  loop shape almost directly instead of a bespoke steering widget -- see
  Design section 2.
- Stopping the boat: before it actually stops, tap `Q`, then click the
  "Take down sails" UI element (un-sets the sails, boat decelerates to a
  stop). This lives in the Sailing Options panel per the wiki's description
  of the Facilities tab's non-reorderable "steering" section.
- **Clicking away from a deployed hook retrieves it** -- e.g. clicking the
  helm to reposition both ends gathering and opens steering mode in one
  action. No separate "stop/retrieve" step is needed before heading to the
  helm.

## Still unverified -- flagged for in-game testing, not blocking a first cut

1. **Exact heading quantization and hop tolerance.** Confirmed live that
   minimap clicks steer the boat, but not exactly how coarse the snapping is
   (the user observed roughly 8 compass directions; sub-increments like NNE
   are unconfirmed) or how far a single "hop" travels before needing a
   re-aim. This governs `WALK_HOP_MIN/MAX_TILES`-equivalent tuning for the
   boat version in `sailing.py` -- start from `travel.py`'s existing
   constants and adjust once this is observed live. Downgraded from a
   blocking unknown: interface group `604` in the stale source checkout
   (internal cache name `FossilquestSailing`, with `BEARING`/`THROTTLE`/
   `DIRECTION` sub-widgets incl. `THROTTLE_INCREASE`/`DECREASE`/
   `DIRECTION_LEFT`/`RIGHT`) looked like a bespoke steering HUD before this
   confirmation; it's kept only as a fallback note in case minimap-click
   steering turns out insufficient (e.g. too coarse to reliably reach a
   specific wreck), not as the primary mechanism.
2. **"Take down sails" button location.** Interface group `937`
   (`SailingSidepanel`) is confirmed as the Sailing Options panel (Facilities/
   Stats/Crew tabs), but the steering section's individual buttons are
   dynamically built inside `FACILITIES_ROWS` rather than fixed constants --
   same situation `BankProvider.java` already handles for individual bank
   item slots (dynamic children, matched at runtime, not a fixed id).
   `SailingProvider.java` will need to scan that container's dynamic
   children by text (`"Take down sails"` / `"Set sail"`) the same way, and
   this needs in-game confirmation that the text match actually finds the
   right child.
3. **Whether re-clicking an already-deployed hook is safe.** The `Hidden`
   conditional op (index 2) on the hook object suggests its available
   options change based on state, which raises the question of whether the
   *default* (left-click) option itself swaps to something undesirable
   (e.g. "Retrieve") once deployed. Until verified, the routine deploys the
   hook exactly once per arrival (tracked in `ctx.state.data`), never
   re-clicking on later ticks.
4. **Whether the hook auto-continues onto a newly-risen wreck** in the same
   spot once the current one sinks, or needs re-deploying. Given (3), the
   routine's default behavior is to assume it keeps working (do nothing) as
   long as a matching wreck stays within `maxDistance`, and only go through
   the full reposition sequence when nothing matches nearby at all.
5. **Whether left-click on the helm resolves straight to "Navigate"**, or
   needs "Navigate" added to Controller Settings -> Clicking -> Forced
   Actions -- same convention as every other non-default click in this
   codebase (the ring of dueling, the looting bag's "Empty", the gemstone
   crab tunnel).
6. **Whether 'Q' is the correct keybind for revealing the Sailing Options
   panel.** `sailing.py`'s `SAILING_OPTIONS_TAB_KEY` is a guess, not
   confirmed against the live client.

## Design

### 1. `SailingProvider.java` (new)

Registered alongside the other providers (see `BridgeProvider` subclasses in
`src/main/java/com/example/providers/`). Exposes `GET /sailing`:

```java
{
  "navigating": bool,              // is the helm currently engaged
  "facilitiesTabOpen": bool,       // is Sailing Options -> Facilities selected
  "takeDownSails": {screenX, screenY} | null,    // dynamic text-scan of FACILITIES_ROWS, null if not found
  "setSail": {screenX, screenY} | null           // same row, opposite state
}
```

Slimmer than originally sketched: since steering input reuses the existing
generic minimap-click machinery (`get_minimap_walk_point` in
`controller.py`, already provider-backed for foot travel), no bespoke
steering-HUD widget lookups are needed here. `takeDownSails`/`setSail`
follow `BankProvider.java`'s existing pattern for widget-bounds lookups
(`buttonPoint(int packedWidgetId)` plus a children-scan loop, mirroring
`BankProvider`'s `BANK_ITEM_CONTAINER` handling) -- marked with a
`// TODO verify` comment pointing at this spec's "Still unverified" section
2, exactly the codebase's existing convention for unverified assumptions
(see `travel.py`'s module header). If in-game testing shows minimap-click
steering is too coarse to be usable, `directionLeft/Right` and
`throttleIncrease/Decrease` (interface group `604`, see "Still unverified"
section 1) would be added here as a fallback -- not built preemptively.

### 2. `sailing.py` (new, parallel to `travel.py`)

```python
SAILING_OPTIONS_TAB_KEY = "q"   # TODO verify -- see spec section 5

def open_sailing_options_tab(ctx) -> None:
    # idempotent tap, same shape as controller.open_inventory_tab /
    # open_prayer_tab: check controller.get_sailing()["facilitiesTabOpen"]
    # first, only tap if not already open.
    ...

def use_helm(ctx) -> bool:
    # find + click the "Ship's wheel" object via get_objects(); this both
    # retrieves any currently-deployed hook and engages steering
    # (confirmed by the user -- no separate stop step).
    ...

def steer_toward(ctx, target: dict) -> bool:
    # Confirmed live: minimap clicks drive the boat once the helm is
    # engaged, same input as foot travel -- reuses controller.click_entity +
    # controller.get_minimap_walk_point directly, i.e. the same hop-and-check
    # loop shape as travel._walk_greedy (recompute next hop from current
    # position each iteration; no Shortest Path route needed for a
    # same-hotspot reposition over open, obstacle-free water). The one real
    # difference from travel._walk_greedy: heading only snaps to a handful
    # of coarse compass directions rather than plotting straight toward the
    # exact clicked point, so hops likely need a wider WALK_ARRIVAL_TOLERANCE
    # and/or more hops than the foot-travel constants -- tune once observed
    # live (see "Still unverified" section 1).
    ...

def stop_boat(ctx) -> bool:
    # tap Q (open_sailing_options_tab), click get_sailing()["takeDownSails"],
    # wait for player position to stop changing between polls.
    ...

def deploy_hook(ctx, hook_name: str) -> bool:
    # find + click the boat's own salvaging hook object by name
    # (e.g. "Bronze salvaging hook") via get_objects().
    ...

def sail_to(ctx, target: dict) -> bool:
    # orchestration: use_helm -> steer_toward -> stop_boat.
    # Caller (the routine) deploys the hook afterward -- kept separate so
    # the routine can decide when redeploying is appropriate.
    ...
```

### 3. `routines/shipwreck_salvaging.py` (new)

```python
DEFAULT_CONFIG = {
    "enabled": True,
    "wreckNames": ["Small shipwreck"],   # exact tier names, confirmed live
    "hookName": "Bronze salvaging hook", # exact object name, confirmed live
    "maxDistance": 15,        # hook-operating range -- gathering happens here
    "maxSailDistance": 60,    # a farther wreck worth repositioning the boat for

    "breaks": {...},          # same shape as every other routine
    "safety": {
        "stopOnDeath": True,
        "stopOnInventoryFull": False,   # routine drops salvage itself
        "maxRuntimeHours": 0,
    },
}
```

`tick()`:

1. If inventory is full (`len(get_inventory()) >= controller.INVENTORY_SIZE`):
   drop every item whose name ends in `"salvage"` (covers all 8 tiers --
   Small/Fishy/Barracuda/Large/Plundered/Martial/Fremennik/Opulent -- without
   needing a config list; confirmed left-click-to-drop needs "Drop" in Forced
   Actions, same convention as looting-bag Empty). Return.
2. If a matching wreck (per `wreckNames`) is within `maxDistance`: nothing to
   do if the hook is already marked deployed this arrival (see unverified
   item 3) -- otherwise `sailing.deploy_hook(ctx, config["hookName"])` and
   mark deployed in `ctx.state.data`. Return.
3. Else if a matching wreck is within `maxSailDistance` (farther, still
   visible via `get_objects()`): `sailing.sail_to(ctx, wreck)`, then
   `sailing.deploy_hook(...)`, mark deployed, clear any stale "deployed"
   flag from before the move. Return.
4. Else: print a rate-limited "nothing in range, waiting" message. No
   relocation between separate hotspots in v1 (Scope item 5).

### 4. Wiring

- `controller.py`'s `ROUTINE_FILES`: add
  `"SHIPWRECK_SALVAGING": "routines/shipwreck_salvaging.py"`.
- `RoutineChoice.java`: add
  `SHIPWRECK_SALVAGING("Shipwreck Salvaging")`.

## Testing

Same story as every other routine in this codebase: no unit test framework,
verified live against the bridge + Arduino. Given the number of flagged
unverified assumptions above, this one needs more staged live verification
than usual before it's trustworthy unattended:

1. Confirm object/click facts already covered live via cache2 (done, see
   "Confirmed facts").
2. Manually drive through one full cycle in-game first (helm click,
   minimap-steer, Q + Take down sails, hook deploy) to nail down items 1, 2,
   and 5 -- specifically the heading quantization/hop tolerance for item 1,
   and the Take-down-sails widget location for item 2 via the RuneLite
   Widget Inspector devtool.
3. Run the routine live for a single wreck-to-wreck cycle (including one
   sink-and-reposition transition) watching the `[sailing]`/`[salvage]` log
   lines, to confirm items 3 and 4 (deployed-hook re-click safety, whether
   it survives onto a newly-risen wreck).
4. Only then a longer unattended soak run.
