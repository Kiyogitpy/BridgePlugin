Moved out of CLAUDE.md on 2026-09-14. Nothing here is obsolete: this is the full measured record; the condensed working rules live in CLAUDE.md.

## Teleporting home from a bank trip (`ControllerAddon/teleports.py`)

Added 2026-09-07, sub-project 2 of the bank-memory work (USER: "make sure slayer is using this
to decide what method of teleportation is best to get back"). **Not yet run live.**

**The Shortest Path handshake is GONE and is not coming back.** USER asked whether we could still
handshake it for a teleport suggestion: no. `ShortestPathProvider.java` (399 lines, the
`PluginMessage` request/poll) was deleted in `1e96c8d`, and the plugin is no longer an enabled
Plugin Hub install (`~/.runelite/externalmanager/` has no shortest-path jar, though the jar
itself still sits in `~/.runelite/plugins/`). Re-adding it would also drag back the hazard this
file already records: its answers were governed by the user's own Shortest Path panel, which
once produced a route whose first edge was a `QUETZAL_WHISTLE` to a tile 454 tiles away.

**USER's alternative is the one taken and it is strictly better**: "we jhust skip the handshake
altogether and use the teleport data from the plugin itself". The plugin ships its transport
tables as plain TSV resources inside the jar, and `transports/teleportation_items.tsv` is
vendored byte-identical into `ControllerAddon/vendor/` (see `vendor/VENDORED.md`; the jar carries
**no LICENSE file**, same as m8aq). 340 rows of destination tile + OR'd item ids + duration +
wilderness level. **It keys on ITEM IDS and bank memory reports item ids, so the two join
directly.** This is also CLAUDE.md's own rule applied -- copy the plugin's SOURCE rather than
your reconstruction of it: `travel.RING_OF_DUELING_NAME`'s Ferox teleport and
`mahogany_homes.CITY_TELEPORTS` are both hand-rolled reconstructions of rows in this very file.

- **`teleports.py` is PURE** -- no HTTP, no client state, no execution. `choose()` takes the
  router as an argument, so the whole decision is testable without a map, a bridge or a client.
- **The header line is ASSERTED, not assumed.** Column order differs BETWEEN Shortest Path's own
  transport files (`teleportation_spells.tsv` puts Items second where `teleportation_items.tsv`
  puts it fourth), so a silent upstream re-order would mis-parse every row into plausible-looking
  nonsense rather than failing. Row-count assertions are a FLOOR (>= 300), like
  `M8aqProviderTest`'s, so re-vendoring a newer table does not fail the build.
- MEASURED over the table: **zero rows use `&&`** -- the Items column is purely OR alternatives,
  usually the charge variants of one item (ring of dueling (8) down to (1)). Matching only the
  full-charge id would miss a part-used ring, which is the common case. 20 of the 340 rows carry
  neither a destination nor an item (section markers) and are skipped.
- **Wilderness level is load-bearing here, not a corner case**: Krystilia's slayer tasks are IN
  the wilderness, and a teleport does not fire above its own level (283 rows are level 20, 35 are
  30), so the click would be spent for nothing. `choose` filters on the player's live
  `wildernessLevel`.
- ~~**Player-specific destinations are excluded**~~ -- WRONG, corrected below: varbits 2187/4744
  resolve exactly which house row applies, and the graph uses them.
- **Walking is the default and a teleport must EARN the trip**: `MIN_SAVING_TILES` (40) over
  `walk_len`, plus `TELEPORT_COST_TILES` (25) charged against the teleport for the withdraw,
  click, dialogue and animation. Both are FIRST-DRAFT GUESSES wanting a live session to fit. The
  one exception: when there is no walking route home at all, anything that lands within reach
  wins, since it beats a walk that cannot happen.

**Where it runs.** The choice happens INSIDE the bank (`_withdraw_return_teleport`, called from
both `bank_up` branches after `withdraw_loadout`) because an open bank is the only moment the
winning item can be withdrawn; `return_to_spot` then uses it and walks whatever is left. So
**every routine that opts into `returnToSpot` gets this**, not just slayer -- and the callers
that opted out (firemaking, blast_furnace, golem_crafting) are untouched.

- **A teleport that does not fire costs latency, never the trip.** `_use_return_teleport`
  returns False and the existing walk home runs exactly as it did before. That is the entire
  safety argument for executing teleports this codebase has never driven live.
- **The plan is popped INSIDE `_use_return_teleport`, not by its caller.** A first cut popped it
  in `return_to_spot` before the call, so the function read nothing and the teleport silently
  never happened -- and the fallback test could not catch it because it stubs the very function
  the bug was in. Pinned by `test_return_to_spot_actually_reaches_the_teleport_it_planned`,
  red-green verified. Popping exactly once is also what stops a dud being retried into an empty
  inventory.
- **The landing is CONFIRMED, never slept off** (`RETURN_TELEPORT_BUDGET_S` 6.0s, polled) --
  karambwan_fishing MEASURED that a teleport's own signals fire on the tick the LOADING screen
  starts, when the bridge answers nothing at all.
- **This is the FOURTH copy of the left-click-with-hover-gate teleport pattern** in the repo
  (`falconry._use_teleport`, `golem_crafting._teleport_home`, `karambwan_fishing._click_option`,
  now `banker._use_return_teleport`). Extracting one shared helper is the obvious follow-up; it
  was deliberately not done here rather than editing three working routines mid-task.


**CORRECTION, same day: the single-hop model above was WRONG, and the transport GRAPH replaced
it.** USER: *"that is not true, if you'd ask shortest path the answer would have been take house
tablet then use the ordnate jewlry box to ferox enclave"*. They were right. Four mistakes, and
the deep one is that **the data was copied and the ALGORITHM was not**:

- **Only 1 of ~8 teleport tables was vendored** (25 transport files exist in the jar, including a
  301KB `transports.tsv`). `teleportation_boxes.tsv` and `teleportation_portals_poh.tsv` are now
  vendored too.
- **`teleportation_items.tsv` has no `Origin` column; the box and POH-portal tables DO.** Those
  rows are graph EDGES, usable only from a particular tile. Shortest Path's answer comes from its
  pathfinder chaining edges -- a flat "teleport from anywhere to X" table structurally cannot
  express house tablet -> POH -> box.
- **The house rows were excluded as "player-specific"** on the belief a house could be anywhere.
  The table's own comment names the varbits that resolve it (**2187** house location, **4744**
  inside/outside) and `PathfinderConfig.varbitChecks` evaluates them with the same generic
  machinery that gates diary capes and quest teleports -- nothing about the house is
  special-cased. MEASURED live: 2187=3, 4744=0.
- **The box tiers were misread as "only Basic has Ferox".** They are cumulative.

**How Shortest Path solves POH teleports** (read from the decompile, not guessed):
- `PathfinderConfig.checkJewelleryBoxTier`: a Basic row is available to ANY tier above NONE,
  Fancy needs FANCY+, Ornate needs ORNATE. Ported verbatim into `teleports.poh_allows`.
- **An ornate box DISABLES the mounted Amulet of Glory** (`isMountedGlory && ORNATE -> false`),
  because the box supersedes it -- offering both plans a route through something you would never
  use. The kind of subtlety only copying the source gives you.
- **The client cannot see inside a POH, so Shortest Path ASKS**: `usePoh`, `usePohFairyRing`,
  `usePohSpiritTree`, `useTeleportationPortalsPoh`, `pohJewelleryBoxTier`, `usePohMountedItems`,
  `usePohObelisk`, plus a hidden `builtTeleportationPortalsPoh` (`ID=X Y Z;...`) it learns. Our
  `teleports.PohConfig` mirrors those names. USER's actual POH: ornate box, portals to Varrock /
  Falador / Ardougne / Lunar Isle, a fairy ring, and a mounted digsite pendant.
- **"Enter your house" is NOT modelled as a transport anywhere** (0 rows across every table). The
  only way into the POH node is the house tablet itself.

**Shape now.** `teleports.load_all()` parses all three tables keyed by HEADER NAME -- they do not
share a column order (items has no Origin, boxes has no Varbits, poh_portals has both), so a
fixed-index parser mis-reads two of the three. `usable()` = items (any-of) AND varbits AND POH
gates AND wilderness level. `plan(start, goal, owned_ids, route_len, ...)` is a bounded
breadth-first search over hops (`MAX_HOPS` 2) returning a list of legs, still PURE -- `route_len`
is an argument, so the whole search tests with no map, bridge or client.

- **An unknown varbit FAILS its check rather than passing it.** These gates pick exactly one row
  out of the house tablet's ten; answering "yes" on unknown would make every house location
  simultaneously true.
- **`unreachable()` is separate from `plan()` returning None.** None means "walk" -- either
  walking is better or nothing helps. At Ferox those demand opposite actions (walk vs. stop and
  say so), so the two questions are asked separately.
- **The POH interior routes as OPEN VOID on our static map** -- MEASURED:
  `find_path((1923,5709) -> (1870,5698))` answers 64 steps, because POH template coords carry no
  map data and "no data defaults to PASSABLE". The step count is fictional (the house is
  instanced and its layout varies per player) but the conclusion is right: you can walk from your
  arrival tile to the box. A test stub that forbade this was the test's bug, not the code's.

**VERIFIED LIVE 2026-09-07** against the running client -- real inventory, real varbits
(2187=3, 4744=0), POH config as the user described, goal Ferox Enclave, real `worldmap` router:

    walking to Ferox: None
    leg 1: [item] Teleport to House tablet     anywhere      -> (1923,5709,0)
    leg 2: [box]  3: Ferox Enclave             (1870,5698,0) -> (3151,3635,0)
                  via: Teleport Menu Basic Jewellery Box 37492

**Ferox Enclave is a sealed 413-tile island on our collision map** (component does not grow at
radius 40/80/150; nothing routes in or out, not even 35 tiles south). MEASURED 2026-09-07. That
is the deterministic reason a bank trip gets stuck there -- `banker`'s walk home has been
impossible since worldmap.py replaced Shortest Path on 2026-08-29, independent of the three
defects fixed earlier that day. Of 210 plane-0 destinations, **167 route normally, 30 are
enclosed, 13 sit on an unwalkable tile**; the enclosed ones are genuinely enclosed places
(Slime Pit, Tarn's Lair, the pyramids, Lithkren, the gem mine -- which karambwan_fishing uses
happily, because it teleports out rather than walking).

**NOT DONE, and each is a real gap:**
1. **`banker` still calls the old single-hop `choose()`.** The graph is not wired in, so slayer
   does not yet benefit. Multi-leg execution is the work.
2. **No execution for a box or portal leg.** The jewellery box opens a destination picker --
   exactly the shape CLAUDE.md records `_use_transport` could never drive. The route is free; the
   clicking is not.
3. **No config surface.** `PohConfig` defaults to everything off/NONE, so until a Controller
   Settings section (or a routine config block) exists, the graph will never plan a POH route in
   production.
4. **`plan()` costs ~19s / 87 `route_len` calls** on a real cross-map query. Fine once per bank
   trip, far too slow per tick. The fix is one flood from the goal (`reachable_from` returns
   distances) instead of N `find_path` calls.
5. **4744's polarity is UNVERIFIED.** With 2187=3 and 4744=0, two rows match: one landing at the
   POH interior (1923,5709) and one outside at (3340,3003). The search considers both and takes
   whichever routes; which one the game actually does is a live question.
6. Only items/boxes/POH portals are vendored. Fairy rings, spirit trees, minecarts, gliders,
   obelisks and the rest are still absent -- the user has a POH fairy ring that we cannot use.


**Wired into banker 2026-09-07 (USER: "go ahead to 1+2+3"), and VERIFIED LIVE against the
running client.** `_plan_return_teleport` now calls `teleports.plan`, stores every leg, and
`_use_return_teleport` runs them in order; a leg that does not land stops the chain and the walk
home recovers, exactly as the single-hop version did. POH contents come off the bridge as a
`poh` block (`ControllerConfig`'s new Player-Owned House section, mirroring Shortest Path's own
option names: `usePoh`, `pohJewelleryBoxTier`, `usePohPortals`, `usePohMountedItems`,
`usePohFairyRing`), defaulting to everything OFF so an unconfigured account never routes through
a house it may not have. Needs the client rebuild.

Four bugs found by running it live, three of them the SAME SHAPE -- an assumed payload shape
that a lying test stub then hid:

- **`/equipment` is a DICT KEYED BY SLOT, not a list.** Iterating it yields slot NAMES, so worn
  items contributed NOTHING to the owned set and a worn-name lookup raised on a `str`.
  `_item_rows` normalises both shapes. The tell was there earlier and missed: an equipment dump
  that printed empty while the account was wearing eleven items.
- **`controller.get_varbit` returns the VALUE, not a `{"value": n}` envelope.** The first cut
  checked `isinstance(reply, dict)`, so it read NOTHING, so every varbit-gated row was rejected,
  so the house-tablet chain was impossible. **The unit test passed because its stub returned a
  dict** -- encoding the same wrong assumption it existed to catch. Both stubs now match reality.
  General lesson, twice in one session (the other was the pop-order regression): **a stub that
  lies about a shape is worse than no stub.**
- **`test_banker.py` was not hermetic**: `_owned_item_ids` reads `bank_memory.load()`, which
  reaches the RUNNING CLIENT (~929 real items that change as the account is played), so planning
  outcomes drifted and three unrelated tests failed once and passed on a verbatim re-run. It has
  a `_no_live_bridge` autouse fixture now, like the other suites; the full run also got ~20%
  faster.
- **THE VOID IS ONE CONNECTED COMPONENT, and it made planning both slow and wrong.** "No data
  defaults to PASSABLE" means every dungeon, instance and POH template is joined into one giant
  walkable void, so `find_path` invents hundreds-of-tiles routes between unrelated underground
  coordinates. MEASURED: a single decision cost **106s**, and the invented routes outranked a
  ring of dueling that teleported straight home. Fixed by bounding an approach walk in BOTH
  senses -- `APPROACH_MAX_TILES` (60) rejects a long answer, `APPROACH_MAX_NODES` (20000) bounds
  the SEARCH so a void crawl gives up in milliseconds -- plus `GOAL_FLOOD_RADIUS`: ONE
  `reachable_from` flood from the anchor replaces a cross-map search per candidate, and its
  radius doubles as the rule "a landing further than this from home is not worth teleporting to".
  **106s -> 9.9s**, answers unchanged.

**VERIFIED LIVE 2026-09-07** through banker's own code path, real bank memory (913-929 banked
items), real varbits, POH set to the user's actual house: `-> Ferox Enclave: 9.9s Ring of
dueling: Ferox Enclave` and `-> Varrock W bank: 9.9s Varrock tablet`. With the bank in view the
planner correctly prefers a direct teleport over the house->box chain; with only the house tablet
owned it finds the chain (pinned by test). **`/bank/memory` is also confirmed working live**
(`seen=true, source=live, items=929`), which clears item 1 of the bank-memory checklist.

**Still NOT verified live: the actual clicking.** `_run_object_leg` and `_pick_destination` have
never run inside a POH -- the jewellery box's destination picker is answered by matching the
TSV's own destination name in the widget dump, and that assumption is untested. Every step
confirms and any failure falls back to walking, which is the whole safety argument, but treat a
POH leg as unproven until a live session says otherwise.

**First live session's checklist** -- none of this has touched a real client:
1. Does the winning teleport's item actually withdraw (`search_and_withdraw` by the TSV's item
   name, e.g. "Ring of dueling", against a bank listing "Ring of dueling(8)")?
2. Does the left-click hover gate pass on these items, or does everything fall to the menu?
3. For a multi-destination item, is the TSV's `destination_name` really the dialogue option text?
   (This is the assumption the whole "safe subset" rests on and it is UNVERIFIED.)
4. Fit `MIN_SAVING_TILES` / `TELEPORT_COST_TILES` against real trips -- watch for a tablet burned
   on a walk that was already short.
5. Confirm the wilderness filter fires on a Krystilia task above level 20.

