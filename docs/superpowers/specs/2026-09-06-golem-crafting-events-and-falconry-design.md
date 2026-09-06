# Golem crafting: event-driven outcomes + falconry as the fur source

**Date:** 2026-09-06. **Status:** approved in chat (USER: "go ahead and do the same improvement
and bugfixes on golem_crafting, and also instead of doing goat hunting to get furs merge falconry
as the method of furs"; return leg = slayer ring, route out = falconry.py's legs with
Last-destination AKQ, travel kit withdrawn per trip, trip ends at a full fur pouch).

## Problem

`routines/golem_crafting.py` was live-debugged to a working state on 2026-08-08..24, before
`bridge_events.await_outcome` existed. Every confirmation is a poll: `/golem` five times a second
for a carved side, `/last-action` ten times a second for the click echo, inventory every 0.2s for
a chisel, fixed sleeps around the bank. Its fur supply is the Wyrmscraig goat pit (Telekinetic
Grab, spikes, a pit that empties), which is slow, rune-hungry and yields the lowest-XP fur. The
wiki's own guide for this method is: fur pouch empty -> fairy ring AKQ -> catch dashing kebbits ->
teleport back. Dashing kebbit fur is 2720 XP per golem against goat fur's lower value.

## Facts this rests on (measured or sourced; none of the new travel legs have run live)

- Any fur works; `FUR_PRIORITY` already ranks dashing kebbit fur third. (wiki Golem crafting)
- Golem varbits (runelite-api 1.12.38 `VarbitID`): station 1 (north plinth) state 15728, sides
  N/E/S/W 15729-15732, progress N/E/S/W 15739-15742; station 2 (south) 15733, sides 15734-15737,
  progress 15743-15746; `GOLEM_CRAFTING_COUNT` 15738. `GolemProvider` reads exactly these.
- Falconry (from `recordings/2026-08-23_121236_falconry-entire-run`, already encoded in
  `routines/falconry.py`): varbit 14192 = 5 catch waiting / 0 retrievable; ~40% of throws miss;
  Matthias `Quick-falcon` 500gp; the falconer's glove sits in the WEAPON slot while a falcon is
  out; fur/meat route into open pouches until each holds 28 then spill loose; bones always loose.
  Regions: falconry 9528, Castle Wars 9776, monastery 10290, fairy ring 10546, AKQ landing 9272.
  Fairy ring DJP at (2655,3229) id 29495, stile (2370,3621) id 19222, hunt tile (2388,3590).
- Falconry needs the weapon, shield and glove slots EMPTY (wiki). Equipment interface children:
  cape 387.16, weapon 387.18, ring 387.24 (recording); shield 387.20 and gloves 387.22 by the
  standard layout, UNVERIFIED.
- Slayer ring inventory ops (cache obj 11866 / 21268 eternal): Wear / Rub / **Teleport** / Check;
  Teleport opens a numbered destination dialogue; the Wyrmscraig entry lands at the "northern end
  of the cavern, near Mortimer, near the entrance leading to the Mad Angel" (wiki). The cavern
  exit is loc 62220, name `Cave`, op1 `Exit` (cache); its tile is not in the offline spawn data.
  Necklace of passage lands on the SURFACE east of Ardeaglais (alternative).
- The offline collision map covers Wyrmscraig: 48% of surface edges and 64% of cavern edges are
  blocked, i.e. real data (measured 2026-09-06 with `worldmap.north/east` over x 2540-2640).
- Cavern coordinates are the surface's with y + 6400 (Broken golem at 2573,8650 under the
  cathedral; cavern map centre 2590,8615). The bridge has no fur-pouch reader (`/api/index`).

## Design

### 1. Shared wait helpers: `ControllerAddon/outcome_waits.py`

The helpers written for mastering_mixology move here unchanged in behaviour: `await_pre` (an
`await_outcome` with a confirm pre-check), `await_long` (chunked into measured human still
segments, `controller.fidget_once` between chunks, a `stop` predicate), `tick_pause`,
`still_segment_s`, `mark`, `varbit_in`, `walk_budget`. Every function takes `now_fn`/`sleep_fn`
so each routine keeps its own patchable `_now`/`_sleep`. mastering_mixology keeps thin wrappers
and its tests; golem_crafting uses the same.

### 2. golem_crafting, event-driven (behaviour-preserving)

- `VARBIT_SUBSCRIPTIONS`: the 19 golem varbits + 14192.
- `_await_side_done`: signal = `VARBIT_CHANGED` on the side's state or progress varbit; confirm =
  `/golem` side `done` (the transition rule and the progress-extension/hard-cap rule stay).
- `_await_golem_complete`: signal = `VARBIT_CHANGED` on 15738 or the station progress varbit;
  confirm = the existing two transitions.
- `_await_click_echo`: signal = `MENU_CLICKED`; confirm = `_click_resolved_wrongly` on
  `/last-action`; the tri-state and "None is not failure" rule stay.
- chisel: confirm each core on `INVENTORY_CHANGED`; chain up to `CORES_PER_TRIP` cores inside one
  tick (paused/safety re-checked between).
- mine: the pre-hover's `wait_until_player_idle` becomes an `await` on `PLAYER_STOPPED` with the
  `/player` `moving` flag as confirm; the XP gate is already event-driven.
- bank: withdraw confirm on `INVENTORY_CHANGED`.
- Long waits (a side is ~11.8s) go through `await_long`, so the cursor fidgets instead of
  freezing. Stall watch (`[!] golem: STUCK?` at 120s, stop at 300s) on a progress signature of
  phase, inventory ids, `/golem` progress fields and fur stock. Phase chaining inside one tick
  for chisel -> craft and bank -> mine (mine and craft stay one-action-per-call by design).
- Every measured constant, the three-tier hijack escalation, the side-avoid set, the block/backoff
  and the out-of-fur recovery are untouched.

### 3. Falconry replaces the goat pit

`hunt_phase` and its goat machinery (`_ensure_wooden_spikes`, `_pit_ready_to_reline`, spikes,
goat chat, `GRAB_*`, `is_valid_grab_target`, `goat_in_grab_range`, the `goatPit`/`spikesSupply`
anchors) are deleted, with their tests. `cast_telekinetic_grab` stays in controller.py (other
callers). `_run_bank_trip`'s "bank has none of the usable fur types" branch routes to the new
`fur_trip` phase instead of `hunt`.

`fur_trip_phase` is a state machine dispatched on LIVE position (region id, or the cavern's
y >= 8000 band) so a restart resumes mid-trip; `ctx.state.data["gc_trip"]` holds only hints
(kit withdrawn, trip start time, catches). Legs:

1. **Kit** (at the open Wyrmscraig bank, entered from `_run_bank_trip`): deposit sunstone and
   cores (existing hunt_keep_items behaviour), withdraw the travel kit -- Ardougne cloak, Dramen
   staff, slayer ring, `hireCoins` coins (config, 2000) -- each confirmed on `INVENTORY_CHANGED`;
   a missing kit item is a safety stop naming it. The fur pouch must be carried. Close the bank.
2. **Depart** (Wyrmscraig surface region): Ardougne cloak -> Kandarin Monastery
   (`falconry._use_teleport`, menu option "Monastery Teleport"), confirmed on the region change.
3. **Fairy ring** (monastery / fairy-ring regions): `falconry._leg_fairy_ring` unchanged (wield
   the staff, `Last-destination`).
4. **Walk in** (AKQ landing / north of the stile): `falconry._leg_walk_home` unchanged (unequip
   the staff, stile, walk to the hunt tile), plus an `_ensure_hands_free` step that removes a
   shield or gloves through the equipment widget the same way the staff is removed.
5. **Hunt** (falconry region): the falconry primitives, upgraded to events and shared by both
   routines: retrieve (signal `VARBIT_CHANGED` 14192 -> 0, confirm `/varbit`), drop bones and raw
   meat (shift-click, one per tick), hire when no glove is worn (`MENU_CLICKED` verdict), throw
   (`MENU_CLICKED` verdict, then `VARBIT_CHANGED` 14192 -> 5 within the measured 1.6s, miss is
   normal). The trip ends when a loose `Dashing kebbit fur` appears in the inventory (the pouch
   holds 28; the goat pit used the same spill signal), or after `tripMaxMinutes` (config, 25) as
   the safety cap. `gc_fur_stock` becomes 28 on the spill, else the counted catches.
6. **Return** (falconry region, trip ended): slayer ring `Teleport` via `click_menu_action`, then
   the destination dialogue read through the m8aq dialogue reader (`giants_foundry._dialogue_
   options` shape) and the number key of the option containing "Wyrmscraig"; confirmed on the
   region change. `returnTeleport` config: `"slayer ring"` (default) or `"necklace of passage"`.
7. **Cavern** (y >= 8000): scan the loaded scene once (`/objects?interactable=true`, no radius)
   for an object named `Cave` offering `Exit`; walk to it (`travel.walk_to`, goal_adjacent) and
   click; confirm on the y band leaving the cavern. The exit's tile is stored in
   `locations.cavernExit` once seen so later trips walk before scanning. No exit visible -> walk
   toward `locations.cavernExit` if known, else a bounded wander budget, then safety stop.
8. **Home** (surface, not at the bank): the existing `handle_missing_target("bank")` anchor walk,
   then the ordinary bank trip deposits the kit and meat/bones, keeps the pouch, and `mine`.

### 4. Config additions (`DEFAULT_CONFIG`, not exported by the Java panel)

`furTrip: {hireCoins: 2000, tripMaxMinutes: 25, returnTeleport: "slayer ring", dropMeat: true}`,
`locations.cavernExit: None`. `safety.stopOnInventoryFull` is forced off for this routine (the
hunt fills the inventory by design), the way falconry.py already does.

### 5. Testing

Pinned tests, hermetic (scripted `poll_udp` into a real `EventStream`, fake clock): every await's
signal/confirm pair; the chisel chain; the stall watch; the fur_trip dispatch table (position ->
leg) including the cavern band and a restart mid-trip; the kit withdrawal's missing-item stop;
the hunt's end conditions (spill, time cap); the slayer ring dialogue selection; the cavern exit
scan (found / not found / anchor known). The deleted goat tests go with the code. Existing
golem tests keep passing: the module still exposes `_await_side_done`, `_await_golem_complete`,
`_await_click_echo`, `walk_to_exact_tile` and the phase functions under their current names, and
still runs on `gc.time.time`/`gc.time.sleep`, which those tests patch.

## Live-verification worklist (first session, in order)

1. The kit withdraw at the Wyrmscraig bank: item names as the bank reports them.
2. Ardougne cloak menu option text and the monastery landing region from Wyrmscraig.
3. `_ensure_hands_free`: the shield/glove widget children (387.20 / 387.22).
4. Slayer ring: `Teleport` op present on the account's ring; the dialogue option text for
   Wyrmscraig; the landing tile and its y band.
5. The `Cave`/`Exit` object's tile (fills `locations.cavernExit`) and the surface tile it drops
   the player on; that `handle_missing_target("bank")` routes from there.
6. Whether the falconer's glove is removed on teleport, and the spill signal at Piscatoris.
7. The golem side/insert awaits: zero regressions against the 2026-08-24 measurements
   (first progress bump +2.1s, ~11.8s per side).
