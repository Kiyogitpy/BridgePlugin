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
- **The steering control is the "Sails" object, not a separate "Ship's
  wheel" prop** -- see item 5 below, this corrected `sailing.HELM_NAME`.
- **A sunk wreck keeps the exact same display name as the active one** --
  confirmed both live (`/objects` while both an active and a sunk "Small
  shipwreck" sat within a few tiles of each other, same name, different
  object id: `60464` active vs. `60465` sunk) and earlier via cache2. Name
  alone can't distinguish them; `find_nearby_wreck` now also excludes the
  known stump ids for all 8 tiers (`SUNK_SHIPWRECK_IDS` in
  `routines/shipwreck_salvaging.py`) -- without this, the routine could
  treat a dead wreck as "in range" and never look for a live one.

## Architecture fix: boats are their own nested WorldView

Discovered live 2026-08-01, not anticipated by the original design: Sailing's boats are
`WorldEntity`s with their **own separate `WorldView`** (own scene, own plane numbering, own
coordinate base) -- confirmed by querying `/player` and `/objects` directly while aboard one (see
`AGENTS.md`'s "Live bridge data" section). This broke two things the original design didn't
anticipate needing:

1. **Distance/position math.** `Actor.getWorldLocation()` while aboard a boat returns coordinates
   local to the boat's own view (plane 1, huge nonsensical x/y), not real Gielinor coordinates --
   `GeometryUtil.distanceToPlayer` (`WorldPoint.distanceTo`) silently returned `Integer.MAX_VALUE`
   for every single object in the scene as a result (that sentinel fires whenever two points are
   on different planes, which a boat-local point and a top-level-world point always are). Fixed
   via `RuneLiteUtil.toTopLevelWorldPoint(Client, LocalPoint)`, which detects a non-top-level
   `LocalPoint` and resolves it through the owning `WorldEntity.transformToMainWorld()` --
   mirroring the lookup `Perspective.localToCanvas` already does internally
   (`worldView.worldEntities().byIndex(id)`, not a linear search). Wired into
   `GeometryUtil.distanceToPlayer` (used by every provider's "distance" field) and
   `PlayerProvider`'s `/player` x/y/plane.
2. **Object visibility.** `ObjectProvider` only ever walked `client.getScene()`, which RuneLite's
   own `Client.java` documents as `@Deprecated` and hardcoded to the top-level world only -- so
   fixtures built on the boat itself (the steering control, the salvaging hook) were completely
   absent from `/objects` while standing right next to them. Fixed by also walking the player's
   own `WorldView`'s scene when it isn't the top-level one, normalizing every returned object's
   x/y/plane/distance to real top-level coordinates via the same `toTopLevelWorldPoint` either way
   (screenX/Y still uses each object's own raw `LocalPoint`, since `Perspective` already handles a
   nested-WorldView point correctly on its own).
3. **Single-plane walking wasn't enough either.** Even after fix 2, the boat's "Helm" object was
   still missing from `/objects` while a salvaging hook on the main deck showed up fine -- both
   fixtures on the same boat, same nested `WorldView`. Unlike the top-level world (mutually
   exclusive floors you switch between one at a time), a boat's whole small deck structure -- main
   deck, an apparently-elevated helm/quarterdeck -- is rendered as one nested `WorldView` with
   multiple planes all at once, and the fix initially only walked
   `playerView.getPlane()` (wherever the player currently stood). Fixed by walking every plane a
   scene can have (`Constants.MAX_Z` = 4) for the nested-view case -- cheap for a boat's tiny
   scene, unlike doing the same for the top-level world.
4. **Ground-tile projection isn't accurate enough for boat fixtures.** Even once the salvaging
   hook was found and being clicked, the click landed on the deck tile beside it, not the hook
   itself -- confirmed live 2026-08-01. `ObjectProvider` computed `screenX`/`screenY` via a plain
   ground-tile projection (`Perspective.localToCanvas`), which `TileObject.getClickbox()`'s own
   javadoc warns against ("you don't want this, use getClickbox() instead") -- the hook's visible
   model sits off the tile's ground height enough for the two points to diverge. Fixed by
   preferring `TileObject.getClickbox()`'s actual on-screen clickable-area bounds (falling back to
   the old ground-tile projection only when no clickbox is available, e.g. not currently
   rendered). This is a general accuracy fix, not boat-specific -- it likely improves clicking
   reliability for any object whose model isn't flush with its tile's ground height, not only
   things on a boat.
5. **Even the fixed-up walk still missed the Helm -- it lives in a further-nested `WorldView`.**
   Confirmed live 2026-08-02 via a new general-purpose debug endpoint (`GET /debug-menu` on
   `GameStateProvider`, dumps whatever menu entries the client currently has built -- the same
   ground truth the client's own Examine debug tool reads from): hovering the Helm showed its
   `EXAMINE_OBJECT` entry's identifier (`59578`, matching what Examine itself reports) was
   *different* from its actual `GAME_OBJECT_FIRST_OPTION`/`GAME_OBJECT_FOURTH_OPTION`
   ("Navigate"/"Escape") entries' identifier (`59576`) -- and, more importantly, those entries'
   `worldViewId` (`3083`) differed from the player's own (`3078`, exposed via new
   `debugWorldViewId`-prefixed fields on `/player`). `WorldView.worldEntities()` supports nesting
   a view inside another view's own `WorldEntity`s, and the Helm turned out to live one level
   deeper than the boat's main view the player stands in. Fixed by replacing the
   single-player's-own-view walk with a full recursive walk of every `WorldEntity`'s `WorldView`
   starting from the top level, at any depth (`ObjectProvider.walkWorldViewRecursively`).
6. **The Helm's name still needed impostor resolution.** Even once found via fix 5, `id 59576`'s
   plain `ObjectComposition` reports name `"null"` -- it's a multiloc (varbit-conditional
   appearance) object, and the base composition is only a template; the real displayed name
   ("Helm", matching the menu's target text) only resolves through `ObjectComposition.getImpostor()`.
   This likely explains every other "null"-named object seen scattered throughout this session's
   `/objects` dumps too (several other boat fixtures), not just the Helm. Fixed by resolving
   through `getImpostor()` whenever `getImpostorIds() != null`, in `ObjectProvider.buildObjectData`.
7. **CONFIRMED live 2026-08-02, fixed and verified working.** Even with a correct clickbox, the
   click can still miss a thin/non-convex model. After fix 4, clicking the Helm (a rudder, not a
   wheel -- thin and likely rendered at an angle) still landed on the tile behind it, not the
   model. `debugHasClickbox` (a debug field added to chase this) confirmed `getClickbox()` *was*
   returning a real shape -- the bug was using `clickBounds.getCenterX()/getCenterY()`, the
   bounding *box's* center, which for a thin or angled model can fall in empty space beside the
   actual (non-rectangular) clickable area entirely. Fixed by `ObjectProvider.pointInsideShape`:
   sample a small grid of candidate points across the bounding box and use the first one
   `Shape.contains()` confirms is real, falling back to a vertex from the shape's own outline if
   none land inside. User confirmed clicking the rudder now works correctly.

8. **The player's own position isn't the right reference point for range checks.** Confirmed
   live: a Sailing boat's own `WorldView` is only ~8 tiles per side (`debugWorldViewSizeX/Y`), so
   standing at one end can be several tiles from the opposite end -- `maxDistance` is a range
   check against something the *boat*'s hook needs to reach, not wherever the player happens to
   be standing on deck. Fixed via `RuneLiteUtil.findTopLevelWorldEntityContaining` +
   `getBoatOrActorWorldLocation` (find the boat's own `WorldEntity` and use its position --
   `WorldEntity.getLocalLocation()` is already resolved to top-level coordinates per its own
   javadoc), exposed as new `boatX`/`boatY`/`boatPlane` fields on `/player` (the existing `x`/`y`
   stays the player's own foot position, which `sailing.py`'s hop-based steering math still
   wants). `find_nearby_wreck` now computes Chebyshev distance itself
   (`max(|dx|,|dy|)`, matching `WorldPoint.distanceTo2D`'s convention) from `boatX`/`boatY`
   instead of trusting `ObjectProvider`'s player-relative `distance` field.
   **Related, separately tracked:** the boat also keeps coasting for a bit after un-setting sails
   rather than stopping instantly (`sailing.stop_boat` now logs the drift distance). Two live
   samples so far: 2.2 and 5.0 tiles -- variable, likely scaling with speed at the moment of
   toggling. Both still comfortably within `maxDistance` (15), so not adjusting
   `SAIL_ARRIVAL_TOLERANCE` (currently 5) preemptively; revisit if a stop is ever observed landing
   outside hook range.
9. **Misclicks during navigation caused real problems.** `controller.py`'s engine-level
   `maybe_misclick` (a human-like stray-click simulation that runs before every routine's own
   `tick()`) already skips firing during combat; confirmed live it needed the same guard for
   `get_sailing().navigating` -- clicking away from a deployed salvaging hook is already confirmed
   to retrieve it, so a misclick mid-navigation is exactly that kind of unwanted "clicked away"
   landing somewhere unintended. Fixed with one more guard clause in `maybe_misclick`.

These fixes are generic (in `RuneLiteUtil`/`ObjectProvider`/`PlayerProvider`/`GameStateProvider`/
`controller.py`, not shipwreck-salvaging-specific code) and benefit any future routine that might
run while the player is aboard a boat, not just this one. `GET /debug-menu` and `/player`'s
`debugWorldViewId`-prefixed fields are kept as general-purpose debugging tools (see their code
comments), not removed once this specific investigation closed.

## Still unverified -- flagged for in-game testing, not blocking a first cut

1. **Heading quantization: CONFIRMED, 16-point compass.** The user tested
   this live: going clockwise from North back to North is 16 discrete steps
   (22.5&deg; increments -- N, NNE, NE, ENE, E, ESE, SE, SSE, S, SSW, SW,
   WSW, W, WNW, NW, NNW), finer than the "roughly 8 directions" first
   assumed. This resolves the quantization half of this item. **Still open:**
   how far a single "hop" actually travels before needing a re-aim, and
   whether `SAIL_HOP_MIN/MAX_TILES`/`SAIL_ARRIVAL_TOLERANCE` in `sailing.py`
   (currently 6/10/5, deliberately shorter/wider than `travel.py`'s
   walking-equivalent 10/16/3 on the assumption heading was coarser than it
   turns out to be) are still appropriately tuned now that the heading
   resolution is known to be roughly twice as fine as assumed -- needs an
   observed hop-tracking test (overshoot/stall behavior) before adjusting,
   not just the angular resolution alone. Interface group `604` in the stale
   source checkout (internal cache name `FossilquestSailing`, with
   `BEARING`/`THROTTLE`/`DIRECTION` sub-widgets incl.
   `THROTTLE_INCREASE`/`DECREASE`/`DIRECTION_LEFT`/`RIGHT`) remains an
   unused fallback note, not the primary mechanism -- minimap-click steering
   is confirmed sufficient.
2. **CONFIRMED live 2026-08-02, fixed and working.** Interface group `937`
   (`SailingSidepanel`) was already confirmed as the Sailing Options panel,
   and the steering controls are indeed dynamically built inside
   `FACILITIES_ROWS`, as guessed -- but the original plan (scan by text,
   `"Take down sails"` / `"Set sail"`) was wrong on two counts: (a) there's
   only **one** toggle icon, not two separately-labeled buttons -- its menu
   action just flips between `"Set sails"` and `"Un-set sails"` depending on
   current state; (b) that icon carries **no text of its own at all** --
   confirmed by dumping all 73 of `FACILITIES_ROWS`'s dynamic children live
   (a new debug field, `SailingProvider.getDebugFacilitiesChildren`-style),
   every one had an empty `getText()` except the fixed `"Steering"`/
   `"Repairs: No kits"` section labels. A text-match approach could never
   have worked here.

   Root cause understood via a new general-purpose `GET /debug-menu`
   endpoint (dumps the client's current menu entries, same ground truth the
   Examine debug tool reads from -- see the "Architecture fix" section
   above, item 5's investigation used the same tool): hovering the icon and
   checking that endpoint showed its menu action resolves through **one
   shared click-layer widget** (`FACILITIES_CONTENT_CLICKLAYER`) common to
   *every* icon in the Facilities tab, discriminated by a per-icon
   `MenuEntry.getParam0()` (confirmed `0` for the sail toggle, `1` for the
   confirmed-adjacent "Reverse" button) rather than by a distinct widget id
   per icon.

   Each icon renders as an 11-entry tile cluster within `FACILITIES_ROWS`'s
   dynamic children (8 corner/edge pieces + its own center point repeated
   3x), always in the same fixed order -- the Steering section's controls
   come first (matching the wiki's "set or un-set the sails, change the
   speed of the boat, or reverse the boat"), and cross-checking the
   confirmed-by-hover sail icon's on-screen position against a fresh dump
   showed it's reliably the array's first cluster. `SailingProvider.java`
   now just uses `children[0]`'s bounds directly (exposed as a single
   `sailToggle` field, replacing the two-button `takeDownSails`/`setSail`
   shape) instead of matching by text or a fixed widget id.
3. **CONFIRMED live 2026-08-02: re-clicking an already-deployed hook IS
   safe.** The `Hidden` conditional op (index 2) on the hook object had
   raised the question of whether the *default* (left-click) option might
   swap to something undesirable (e.g. "Retrieve") once deployed -- the user
   tested this directly and confirmed it does not. Combined with item 4
   below (the hook not auto-continuing), the routine no longer tries to
   track "did we already deploy" at all: `tick()` now just checks
   `controller.get_combat().get("isIdle")` and re-clicks the hook whenever
   idle and a wreck is in range. This self-heals from *any* stop reason
   (wreck sank, stray misclick retrieved it, ...) uniformly instead of
   needing separate detection logic per cause -- the `deployed_wreck_key`/
   `HOOK_STALL_TIMEOUT_S` bookkeeping item 4 originally added is gone,
   superseded by this simpler mechanism.
   **Follow-up bug, also confirmed live 2026-08-02:** unlike `mining.py`'s
   click-the-rock-whenever-idle loop (where `isIdle` gates the *entire*
   `tick()`), salvaging turned out to be a continuous multi-tick channel --
   `/combat`'s `isIdle` can read `False` for a wreck's *entire lifespan*, not
   brief per-swing bursts. Gating the whole near-wreck branch on `isIdle`
   (matching `mining.py`'s pattern) meant the routine never got past that
   check to notice the near wreck was gone, so it never fell through to look
   for a farther one -- this looked like "won't sail to nearby wrecks" /
   "max sail range too short" from the outside, but was never a range
   problem. Fixed: `isIdle` now only gates the reclick itself, inside the
   near-wreck branch -- the branch's own control flow (falling through to
   the far-tier check once `near_wreck` is `None`) runs every tick
   regardless of animation state.
4. **CONFIRMED live 2026-08-01: the hook does NOT auto-continue.** Once the
   specific wreck the hook was deployed at sinks, salvaging stops even if a
   different active wreck of the same name is still within `maxDistance` --
   observed directly. See item 3 above for how this is now handled (the
   idle-triggered re-click covers it, no special-casing needed).
5. **CONFIRMED live 2026-08-01 (fully resolved, after one wrong turn).** The
   wiki's "Helm.txt" article describes a "Ship's wheel" boat part, and
   that's what the original design (and `sailing.HELM_NAME`) targeted, but
   no such object (or the `BOATKIT_HELM01` id guessed from the stale source
   checkout) was ever found live. The steering control turned out to be an
   object literally named **"Helm"** (id `59578`, found via the client's own
   Examine debug tool + confirmed against the live cache: `ops = {0:
   "Navigate", 3: "Escape"}`) -- it simply wasn't showing up in `/objects`
   yet due to the plane-walking gap in the "Architecture fix" section below,
   which made it look absent. An intermediate live test misattributed the
   right-click result to a different, genuinely-nearby "Sails" object
   (id `29516`) purely because that was the only thing `/objects` reported
   at the player's tile at the time -- `sailing.HELM_NAME` briefly targeted
   `"sails"` before this correction; it's `"helm"` now. Index `0` for
   "Navigate" is this codebase's established left-click-default convention
   (matching the salvaging hook's own `0: "Deploy"`), so a plain
   `click_entity` needs no Forced Actions tweak.
6. **CONFIRMED live 2026-08-01: 'Q' is the correct keybind** for revealing
   the Sailing Options panel. `sailing.py`'s `SAILING_OPTIONS_TAB_KEY = "q"`
   guess was right, no code change needed.
7. **CONFIRMED WRONG live 2026-08-02, now fixed with a better signal.** This
   was flagged as the single highest-consequence unverified assumption in
   the whole feature, and it was indeed wrong: `FossilquestSailing.UNIVERSAL`
   widget visibility never reflects engaging the helm. The user confirmed
   clicking the helm/rudder consistently worked (resolved to Navigate), but
   `sailing.use_helm`'s poll loop always timed out
   (`[sailing] clicked the helm but never saw navigate mode engage`) --
   `sail_to` always failed as a result, so the routine could never
   reposition. Root cause: that widget guess assumed the wiki's
   bearing/throttle steering HUD opens on Navigate, but minimap-click
   steering (the mechanism this project actually uses, confirmed sufficient
   back in item 1) apparently never needs that HUD at all, so its visibility
   was never a meaningful signal to begin with.

   Fixed with a much more direct API found while chasing this:
   `WorldView.getYellowClickAction()` reports whether a tile click currently
   resolves to `CLICK_ACTION_WALK` or `CLICK_ACTION_SET_HEADING` for that
   view -- exactly what "navigating" needs to mean, from an API built for
   precisely this purpose rather than an inferred side effect of a widget's
   visibility. `SailingProvider.java` now checks
   `client.getTopLevelWorldView().getYellowClickAction() ==
   Constants.CLICK_ACTION_SET_HEADING` instead.

## Design update: approach braking + pair-midpoint targeting (2026-08-02)

Live testing surfaced two related navigation problems, reported directly by the user: the boat
physically rams the wreck (a real collision, not cosmetic) instead of stopping short of it, and
positioning only ever targets a single wreck even though shipwrecks spawn in close pairs at each
hotspot (wiki-confirmed: "a different pair of shipwrecks", "double Salvage spots" -- see
`data/raw/S/Shipwreck salvaging.txt`).

**Root cause of the ramming.** `steer_toward`'s outer loop only calls `stop_boat()` once the
player is within `SAIL_ARRIVAL_TOLERANCE` (5 tiles) of the target -- but the sails stay up and the
boat keeps coasting under full sail for the entire approach, right up until that check passes. By
the time `sail_to` finally triggers the stop, the boat is already point-blank, and `stop_boat`'s
own measured post-toggle drift (2.2-5.0 tiles, two live samples logged in item 8 above) carries it
the rest of the way into the wreck. This was previously assumed safe ("both still comfortably
within `maxDistance` (15)") -- true at the old 15-tile range, no longer true now that `maxDistance`
is 8 (see the hook-range fix from live testing this session).

**Fix: brake early, let the known drift close the gap.** Add `SAIL_BRAKE_TRIGGER_TILES` (10). Once
remaining distance to the navigation target drops under this, `steer_toward` stops issuing further
hops and returns immediately -- no click is ever aimed at or through the target itself. `sail_to`
then calls `stop_boat()` right away, and the already-measured 2.2-5.0 tile coast lands the boat
roughly 5-8 tiles short of the target: inside the hook's 8-tile range, never touching the wreck.
`SAIL_ARRIVAL_TOLERANCE` (5) is unchanged and keeps its existing job -- `_hop_to`'s own
intermediate-hop tracking during the cruise phase, unrelated to the final stop decision. If actual
drift ever falls outside the 2.2-5.0 tile range observed so far and the boat lands just past
hook range, the existing `HOOK_ENGAGE_TIMEOUT_S` self-heal (sail closer if the hook never actually
engages) already covers it as a backstop -- no new fallback needed.

**Pair-midpoint targeting.** New helper (in `routines/shipwreck_salvaging.py`): given the nearest
matching active wreck, look for a second matching active wreck within `PAIR_MAX_TILES` (16 -- twice
the hook's 8-tile `maxDistance`, so a midpoint puts both within range; confirmed with the user that
paired wrecks are typically close enough for this). If found, the navigation target is their
midpoint; otherwise it falls back to the lone wreck (confirmed with the user: a lone wreck is still
worth sailing to directly, pairing is an optimization, not a requirement). This replaces the raw
wreck-tile target in both places `sail_to` is currently called for a wreck: the initial far-wreck
approach (`tick()`'s far-wreck branch) and the near-wreck stuck-reposition self-heal from the
`HOOK_ENGAGE_TIMEOUT_S` fix (same ramming risk, same fix applies). The payoff isn't simultaneous
salvage from one click -- item 4 above already confirmed the hook doesn't auto-continue onto a
different active wreck once its current one sinks -- it's that a pre-positioned midpoint means the
*next* wreck is already in range after a reclick, with no second sail trip needed.

Both starting numbers (`SAIL_BRAKE_TRIGGER_TILES=10`, `PAIR_MAX_TILES=16`) are initial estimates
from known drift/hook-range figures, not live-measured -- flagged for tuning once observed, same
convention as every other numeric constant in this spec.

## Design update: obstacle-aware route planning (2026-08-02)

User-reported: the boat runs aground on islands during repositioning and comes to a **hard dead
stop** (confirmed with the user -- not a slow/inefficient detour that self-corrects, a real hang
requiring manual intervention to free). `steer_toward`/`sail_to` currently steer in a straight line
from current position to target with no obstacle awareness at all.

**Data source, live-verified before committing to this design.** `TileProvider.java`'s `/tiles`
already exposes per-tile `walkable`/`blocked` flags from RuneLite's own collision map
(`CollisionDataFlag.BLOCK_MOVEMENT_FULL`), computed for the whole loaded scene but never queried
by `sailing.py` until now. Checked live 2026-08-02 before relying on it (real risk: OSRS's
standard collision grid historically has no notion of open-ocean tiles at all, since foot travel
never went there before Sailing existed -- worth confirming rather than assuming): boat's current
position and surrounding water all read `walkable: true, blocked: false`, and a tight, contiguous
cluster of `blocked` tiles sits 3-5 tiles away, consistent with a real landmass rather than noise.
Confirms this data source distinguishes sailable water from land correctly.

**Scope is naturally bounded to the loaded scene.** `/tiles` only covers the currently loaded
scene (~104x104 tiles around the player, one plane). This is sufficient here without needing
incremental/off-scene pathfinding: repositioning targets come from `find_nearby_wreck`, bounded by
`maxSailDistance` (60 tiles) -- well within the loaded scene's extent, confirmed against the live
tile dump's own coordinate range this session.

**New `ocean_path.py`** (parallel to `travel.py`/`sailing.py`, one job: obstacle-aware routing over
the tile grid `/tiles` already exposes):

```python
BOAT_CLEARANCE_TILES = 4  # dilation radius around blocked tiles -- half the boat's own ~8-tile
                           # WorldView footprint (confirmed live, PlayerProvider's
                           # debugWorldViewSizeX/Y), so a route's center-line staying clear of a
                           # blocked tile doesn't mean the boat's actual hull does too.

def find_ocean_route(start: dict, target: dict) -> list[dict] | None:
    # 1. Fetch /tiles (one-time cost per reposition decision, not per-hop -- this must NOT be
    #    called every hop/tick, same stutter risk /objects had before its own radius fix).
    # 2. Build a blocked-tile set, dilated by BOAT_CLEARANCE_TILES.
    # 3. A* from start to target, 8-directional grid movement, Chebyshev heuristic -- fast enough
    #    on a ~104x104 scene that no timeout/node-cap handling is needed.
    # 4. Simplify the raw tile-by-tile path down to just its corner points (where heading needs
    #    to actually change), producing a handful of waypoints instead of dozens of tiles.
    # Returns None if start or target falls outside the loaded scene, or no route exists at all --
    # caller falls back to going direct, same as today's behavior (not worse than before pathing
    # existed).
    ...
```

**Wired into `sailing.sail_to`:** calls `find_ocean_route` first; if it returns waypoints,
`steer_toward`s through each one in sequence with no braking/stopping between legs -- only the
*final* leg (the real target) uses the existing wide `SAIL_BRAKE_TRIGGER_TILES` stop-and-brake
logic from the previous fix. This needs `steer_toward`'s arrival distance promoted from the
hardcoded `SAIL_BRAKE_TRIGGER_TILES` to a parameter (`arrival_tolerance: float =
SAIL_BRAKE_TRIGGER_TILES`, default preserves today's direct-target behavior unchanged), so
intermediate legs can pass the tighter `SAIL_ARRIVAL_TOLERANCE` instead -- an intermediate waypoint
exists specifically to route around something, so cutting the corner early on it would defeat the
point. If `find_ocean_route` returns `None`, `sail_to` falls back to a single direct
`steer_toward(ctx, target)` call, unchanged from today.

**Stuck-detection safety net, kept as a backstop even with routing in place** (scene not fully
loaded at plan time, a moving obstacle, or the coarse compass-snapped heading cutting a corner the
plan avoided could all still ground the boat): track position across hops in `steer_toward`'s loop;
if it hasn't moved meaningfully despite issuing steering commands across some threshold, treat it
as grounded and re-plan a route from the *current* (stuck) position via `find_ocean_route` again,
rather than blindly retrying the same heading that just failed.

`BOAT_CLEARANCE_TILES=4` is an initial estimate from the boat's known footprint, not live-tuned --
flagged for adjustment once observed, same convention as every other numeric constant in this spec.

## Design update: pair-midpoint zone size + verified arrival (2026-08-03)

Live testing surfaced two problems with the pair-midpoint targeting added 2026-08-02, both reported
directly by the user: it was landing on only one of the two wrecks instead of their midpoint, and
even after that was fixed the boat still missed the intended point by a wide margin.

**Zone size -- tried and reverted.** `PAIR_MAX_TILES` (16) was originally derived from twice the
hook's `maxDistance` (8). On 2026-08-03, based on a user-observed on-screen range overlay (two
green squares in the salvaging UI, one per wreck) that looked like a 15-tile per-wreck zone, this
was changed to `WRECK_ZONE_TILES = 15` / `PAIR_MAX_TILES = WRECK_ZONE_TILES * 2` (30) on the theory
that this overlay represented the real max-salvage-yield range. **Reverted 2026-08-04** (user
report: "it is literally sitting on two squares but tries to go away from them") -- that overlay is
NOT the hook's functional engage range. With `PAIR_MAX_TILES=30`, a computed midpoint could land up
to 15 tiles from either wreck, well outside the 8-tile range the hook can actually reach, so
`find_nearby_wreck(maxDistance=8)` kept reporting nothing in range even while the boat sat squarely
between both wrecks -- the routine never stopped trying to reposition. `PAIR_MAX_TILES` is back to
16 (`2 * maxDistance`), the only figure that's actually CONFIRMED live to matter for whether the
hook can engage.

**Root cause of only-one-wreck targeting.** `find_wreck_pair_target`'s partner search calls
`controller.get_objects(radius=PAIR_MAX_TILES + padding)` -- but that radius is centered on the
PLAYER (see `controller.get_objects`), not on `wreck`, the position actually being searched
around. On the routine's normal initial approach this runs against `far_wreck`, which can be up to
`maxSailDistance` (60) tiles from the player -- so a real partner sitting near `far_wreck` was
almost always outside the (much smaller) player-centered search box and silently never found.
Fixed by widening the search radius by however far the player currently is from `wreck`, so the box
reaches `PAIR_MAX_TILES` past `wreck` regardless of where the player is standing.

**Root cause of the remaining margin, after the search bug was fixed.** `sail_to`'s final-leg
arrival used the wide `SAIL_BRAKE_TRIGGER_TILES` (10) tolerance plus `stop_boat`'s own 2.2-5.0 tile
drift -- deliberate slop from the 2026-08-02 anti-ramming fix, appropriate when the target is a
solid wreck but far too loose for a computed midpoint over open water, where there's no collision
risk and precision is what actually matters. `sailing.sail_to` now takes a `brake: bool = True`
parameter; `brake=False` uses the tighter `SAIL_ARRIVAL_TOLERANCE` (5) for the final leg instead of
`SAIL_BRAKE_TRIGGER_TILES`.

**Verified arrival, not just trusted.** `find_wreck_pair_target` now returns `(target, partner)`
instead of just `target`, so the caller can tell a real midpoint (partner found, safe to skip
braking) apart from the single-wreck fallback (a real solid object, still needs the wide brake
tolerance). New `sail_to_pair(ctx, config, wreck)` wraps this: sails with `brake=False` only when a
partner exists, then explicitly checks the boat's landed position is within `config`'s actual
`maxDistance` of *both* wrecks (Chebyshev, matching every other range check in this file) -- if
not, one corrective `brake=False` re-sail. Checking against `maxDistance` specifically (not the
reverted `WRECK_ZONE_TILES` above) matters for the same reason: verifying against a looser zone
could report "landed fine" even when the hook genuinely couldn't reach either wreck from there.
Both `tick()` call sites (initial far-wreck approach, near-wreck stuck self-heal) now go through
`sail_to_pair` instead of calling `sailing.sail_to` directly on `find_wreck_pair_target`'s result.

Not yet live-tuned: whether one corrective re-sail is consistently enough to close the gap, or
whether the tighter `SAIL_ARRIVAL_TOLERANCE` still leaves occasional edge-of-zone landings needing
more than one retry.

## Design update: `WRECK_SQUARE_SIZE` + true-tile hull replaces `maxDistance` entirely (2026-08-04)

The `maxDistance` figure (15, then 8 after live tuning) was always an approximation standing in for
two things this session finally pinned down precisely:

1. **`SalvagingHighlight.java`'s `SIZE_SALVAGEABLE_AREA = 15`** -- re-examined against
   `runelite-api`'s actual `Perspective.getCanvasTileAreaPoly` source (not available in this
   project's own obfuscated-client decompile, but present in the separate `IdeaProjects/runelite`
   checkout): its own doc comment states `size` is a **centered** square ("3x3 AoE evaluates to
   size 3"), implemented as `localLocation ± (size * LOCAL_TILE_SIZE / 2)`. So `15` is a 15x15
   square split evenly around the wreck -- 7.5 tiles to each side -- not a one-directional radius,
   and not the "unrelated cosmetic overlay" the 2026-08-03/04 revert (above) had concluded. That
   revert's real bug was a units mix-up (using `15` directly as a radius, doubling it again for
   `PAIR_MAX_TILES`), not a wrong overlay.
2. **The boat's true-tile hull footprint** -- `PlayerProvider.java` already exposed
   `boatWidth`/`boatHeight` (RuneLite's own `WorldEntityConfig` bounds, modeled on the sideloaded
   Sailing plugin's `TrueTileIndicator.renderBoatArea`) but flagged them `UNVERIFIED live` since
   2026-08-02.

**CONFIRMED live 2026-08-04 (user report):** both of the above are exact -- the wreck's real
salvageable range is precisely its `SIZE_SALVAGEABLE_AREA` square, and the boat's real hull extent
for range purposes is precisely its true-tile bounding box. `maxDistance` is removed entirely (no
longer in `DEFAULT_CONFIG`) and replaced with `WRECK_SQUARE_SIZE = 15` / `WRECK_SQUARE_HALF = 7.5`
plus a rectangle/square overlap test (`_boat_hull_rect`, `_wreck_in_hook_range` in
`shipwreck_salvaging.py`) between the boat's hull box and each wreck's centered square, instead of
a single-point Chebyshev distance check. `find_wreck_in_hook_range` replaces the near-wreck
`find_nearby_wreck(config, maxDistance)` call in `tick()`; `find_nearby_wreck` itself is now only
used for the farther `maxSailDistance` "worth sailing to" search, which was never about hook range
in the first place.

`PAIR_MAX_TILES` is now derived directly as `WRECK_SQUARE_SIZE` (15, down from the old
`2 * maxDistance` = 16): a boat sitting at the midpoint of two wrecks is `WRECK_SQUARE_SIZE / 2`
from each one exactly when their centers are `WRECK_SQUARE_SIZE` tiles apart -- the shared edge of
both squares -- so `WRECK_SQUARE_SIZE` itself, not double it, is the correct bound. `HOOK_ENGAGE_TIMEOUT_S`'s reclick-stuck self-heal is kept as a backstop for whatever the geometric
check still can't see (scene/tile lag, etc.), but its docstring no longer blames `maxDistance` for
the failure case it was originally added to catch.

## Design

### 1. `SailingProvider.java` (new)

Registered alongside the other providers (see `BridgeProvider` subclasses in
`src/main/java/com/example/providers/`). Exposes `GET /sailing`:

```java
{
  "navigating": bool,              // WorldView.getYellowClickAction() == CLICK_ACTION_SET_HEADING (see item 7)
  "facilitiesTabOpen": bool,       // is Sailing Options -> Facilities selected
  "sailToggle": {screenX, screenY} | {}   // FACILITIES_ROWS's first dynamic-child cluster (see item 2), {} if not found
}
```

*(Historical note: this shape as originally sketched had `takeDownSails`/
`setSail` as two separate text-matched buttons, and `navigating` derived
from a guessed widget's visibility -- both wrong, see items 2 and 7 below
for what actually shipped.)* Slimmer than originally sketched: since
steering input reuses the existing generic minimap-click machinery
(`get_minimap_walk_point` in `controller.py`, already provider-backed for
foot travel), no bespoke steering-HUD widget lookups are needed here.
`sailToggle` follows `BankProvider.java`'s existing pattern for
widget-bounds lookups (`buttonPoint(int packedWidgetId)` plus a
children-scan loop, mirroring `BankProvider`'s `BANK_ITEM_CONTAINER`
handling) -- marked with a `// TODO verify` comment pointing at this spec's "Still unverified" section
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
    # tap Q (open_sailing_options_tab), click get_sailing()["sailToggle"] (see item 2 --
    # a single toggle icon, not two separate buttons), wait for player position to stop
    # changing between polls.
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
