# Deep sea trawling — design

**Date:** 2026-09-06
**Routine:** `ControllerAddon/routines/deep_sea_trawling.py`
**Status:** written from the OSRS Wiki (rev 15297127 / 15237362 / 15290062), the decoded game
cache (dump commit `5bc2ff1e`) and `runelite-api-1.12.38` (javap). **No human recording exists
and nothing here has been run live.** Every number below is sourced, not measured; the
"Live-verification checklist" at the end is the first session's worklist.

---

## 1. What the activity is

Deep sea trawling is a hybrid Fishing + Sailing activity. The player sails to a *shoal* — a
moving 6×6 `GameObject` out at sea — parks the boat in range, lowers a *trawling net* boat
facility, and repeatedly **Operates** the net. Every operation window the game performs a
Fishing roll; a success puts 1..N raw fish into a per-player 250-capacity net container, which
is then emptied into the boat's cargo hold.

### Mechanics that shape the design (all Wiki-sourced)

| Fact | Source | Consequence for us |
|---|---|---|
| A roll fires every **3 ticks** while the net is operating in range; **clicking the net every tick makes it every 2 ticks** (+50% catch rate) | Deep sea trawling § Catching deep sea fish | The whole point of the routine. A one-click-per-tick burst is the core loop. |
| Each roll grants a small **Sailing XP** drop; a *successful* roll additionally grants **Fishing XP** | ibid. | Sailing `XP_GAINED` is a pushed, sub-tick **heartbeat** proving "the net is operating in range". Fishing `XP_GAINED` proves a catch. No polling needed for either. |
| XP (and fish) are **halved at the wrong depth** | ibid. | Only matters for multi-depth shoals. Not for v1 (see §2). |
| Shoal paths are **predetermined and cyclical** — fixed stop points, fixed stop durations, fixed lap time | § Locating a shoal, and the per-shoal *Routes* tables | The stop points are a pure data table. We can pre-position at the next stop instead of chasing. |
| Nets share a **250 fish** capacity across the boat, per player | § Transporting deep sea fish | `client.getItemContainer(968)` is the exact count. |
| Fish in the nets are **discarded** on disembark, teleport, world-hop or a forced logout | ibid. | The routine must collect before it ever stops, and must never hop worlds. |
| A crewmate with **Helmsmanship** operates the helm so the player can stand at another facility; *the player still clicks the heading* | Crew Management § Helmsmanship | Hard precondition: a crewmate on the helm. Without one the player has to stand at the helm to steer and cannot trawl at the same time. |
| Spreading fish offcuts from a chum station gives **+20%** fish per catch, one offcut per stop | § Catching deep sea fish | Deliberately **out of scope for v1** (see §9). |

### Cache / API identifiers (decoded, not guessed)

```
loc  59734  sailing_shoal_clickbox_giant_krill   name "<col=FFFF00>Giant krill shoal</col>", 6x6, op5=Rod-fish
loc  59735..59739                                haddock / yellowfin / halibut / bluefin / marlin
loc  59740..59742                                shimmering / glistening / vibrant (mixed shoals)
loc  59755  sailing_rope_trawling_net            op1=Operate op2=Raise op3=Lower op4=Collect-from op5=Modify
loc  59756/59757/59758                           linen / hemp / cotton (2x5 sloop)
loc  59759..59766                                the 3x8 port/starboard variants of all four
loc  59743..59754                                chum station / advanced / spreader (op1=Operate)
npc  15382  sailing_shoal_ripples                size 6, op1=Bait, no name (the visual, not the click target)
inv  968    sailing_trawling_net                 size 250   <- the authoritative net count
inv  963..967 sailing_boat_N_cargohold           size 240
vb   19206/19208  sailing_sidepanel_boat_trawling_net_{0,1}_depth
vb   19207/19209  ..._hotspot_id
vb   19134  sailing_carrying_cargo
vb   15687  sailing_player_trawling_shoal_baited_step
```

`op1=Operate` is index 0, i.e. the **left-click default** on the net — the same convention as
the salvaging hook's `Deploy` and the helm's `Navigate`. So the one-per-tick burst is a plain
left click, no menu.

---

## 2. Target: Giant krill at Great Sound, and why

The account is **Fishing 75 / Sailing 64 / Construction 84** (read live from
`/api/skills/skills`, 2026-09-06). That admits exactly two shoals and one net:

- Shoals: Giant krill (Fishing 69) and Haddock (73). Yellowfin needs 79.
- Nets: **Rope trawling net** only — Linen needs Sailing 65, one level away.

The Rope net reaches **shallow only**, and Giant krill and Haddock shoals are both
**shallow-only** shoals. **Therefore v1 has no depth management at all** — a whole mechanic
(and its halved-yield failure mode) is out of scope by construction, not by omission.

Between the two, **Giant krill / Great Sound** is the v1 target:

- **90 s stops** (150 ticks), the longest of any shoal — the most time trawling per unit of
  steering, which is exactly where the bot is weakest and the human is strongest.
- The route is **compact**: 7 stops inside `x 1560..1616, y 3338..3387` (~56×49 tiles). The
  RuneLite scene is 104×104 centred on the player, so from anywhere on the route **every stop
  point is inside the loaded scene** — the shoal is essentially always present in `/objects`.
  Haddock's routes span five or more regions and the shoal leaves the scene.
- Lap 1234 ticks; 7×150 = 1050 ticks stopped, so **184 ticks moving over ~217 tiles ≈ 1.18
  tiles/tick**. Any hull keeps up; the MMG's "camphor or better hull" is a yellowfin/marlin
  requirement, not ours. (This arithmetic is also the sanity check that the Wiki's stop order
  1..7 really is the visiting order — the per-leg distances come out even.)

Other routes and shoals are **data, not code** (§4): adding Haddock/Misty Sea or, after a
Sailing level, Yellowfin, is a table entry plus a depth phase.

---

## 3. Architecture

```
routines/deep_sea_trawling.py     the routine: phase derivation + the burst
trawling_routes.py                pure data + lookups: shoal specs, route stop tables
outcome_waits.py                  (existing) await_pre / await_long / tick_pause / walk_budget
sailing.py                        (existing) use_helm / steer_toward / sail_to / stop_boat
controller.get_trawling()         (new, 6 lines) -> GET /trawling
providers/TrawlingProvider.java   (new) net + cargo containers and the trawling varbits
```

Nothing else changes. `/objects` already walks the boat's nested `WorldView` **and** the
top-level ocean scene and reports both in top-level world coordinates, so the net (on our deck)
and the shoal (on the ocean) come out of one call on comparable terms.

### 3.1 The one Java addition: `GET /trawling`

Everything this routine needs is already on the bridge **except the two item containers**,
which no provider exposes and no m8aq reader covers (`/api/index` has no sailing group).
`TrawlingProvider` is small and read-only:

```json
{
  "netFish": 137, "netCapacity": 250, "netItems": [{"id":..., "name":..., "quantity":...}],
  "cargo": {"containerId": 963, "used": 12, "capacity": 240, "items": [...]},
  "carryingCargo": 1,
  "nets": [{"slot": 0, "depth": 1, "hotspotId": 3}, {"slot": 1, "depth": 0, "hotspotId": 0}]
}
```

- `netFish` = summed quantities of `client.getItemContainer(InventoryID.SAILING_TRAWLING_NET)`
  (968). A null container means "not aboard a boat with a net" and serves `netFish: null`, not 0
  — the same `None` vs `0` distinction the m8aq notes in CLAUDE.md insist on.
- `cargo` scans 963..967 and reports the first non-null one (a player is on one boat).
- `nets` reads varbits 19206/19207 and 19208/19209.
- No `TickCache` — these are container and varbit reads, like `/player`, not a scene scan.

### 3.2 Signals: what decides WHEN vs WHAT

Following the event-driven-outcomes contract (`docs/.../2026-09-04-event-driven-outcomes-design.md`):

| Question | Pushed signal (WHEN) | HTTP confirm (WHAT) |
|---|---|---|
| Is the net operating in range? | Sailing `XP_GAINED` | — (the heartbeat *is* the answer; absence for N ticks is the failure) |
| Did we catch? | Fishing `XP_GAINED` | `/trawling.netFish` (polled, not per tick) |
| Is the net lowered? | `VARBIT_CHANGED` 19206 | `/trawling.nets[0].depth` |
| Did the boat stop? | `PLAYER_STOPPED` | `/sailing.boatSpeed == 0` |
| Did Collect-from land? | `INVENTORY_CHANGED` / `WIDGET_LOADED` | `/trawling.netFish` back to 0 |
| Has the shoal moved? | `OBJECT_DESPAWNED` on a shoal id | `/objects` (the shoal's live tile) |

`VARBIT_SUBSCRIPTIONS = [19206, 19208, 19134]`. Every consumer tolerates `ctx.events is None`
and falls back to polling.

**The C2 rule applies here and is load-bearing.** The trawl burst holds `tick()` for up to 120 s
(`BURST_MAX_S`; the 90 s figure this said before was the *shoal stop* length, not the burst
ceiling — F-M8), and `tick()` chains up to `TICK_CHAIN_MAX` (4) of them plus their blocking
`sail_to` legs, so one `tick()` can realistically run for minutes,
and `on_event` is dispatched by the engine loop, so it never runs during a burst. The burst
therefore reads events with `ctx.events.since(mark)` and **advances `mark` to
`ctx.events.mark()` immediately after every read** — taking a fresh `mark()` at the top of each
iteration reads past the events its own tick-wait already pumped, silently dropping the entire
heartbeat.

---

## 4. `trawling_routes.py` — pure data

Stop points come from the Wiki's own tile-marker tables, stored as raw
`(regionId, regionX, regionY)` triples and converted by one helper, so no coordinate is
transcribed by hand:

```python
def world_of(region_id, rx, ry):
    return ((region_id >> 8) * 64 + rx, (region_id & 0xFF) * 64 + ry)
```

```python
SHOALS = {
    "giant_krill": ShoalSpec(object_ids=(59734,), mixed_ids=(59740,),   # shimmering
                             name="Giant krill shoal", fishing_level=69,
                             stop_ticks=150, depths=("shallow",)),
    "haddock":     ShoalSpec(object_ids=(59735,), mixed_ids=(59740,),
                             name="Haddock shoal", fishing_level=73,
                             stop_ticks=120, depths=("shallow",)),
    # yellowfin/halibut/bluefin/marlin: add with their depths when the levels allow
}

ROUTES = {
    ("giant_krill", "great_sound"): Route(lap_ticks=1234, stops=[      # order = Wiki label 1..7
        (6452, 16, 51), (6196, 44, 59), (6196, 25, 36), (6196, 38, 10),
        (6196, 44, 51), (6196, 24, 42), (6196, 44, 37)]),
    ("giant_krill", "simian_sea"):  Route(lap_ticks=1196, stops=[
        (11048, 12, 48), (11049, 30,  5), (11048, 54, 48), (11304, 23, 31),
        (11304, 13, 12), (11047, 43, 63), (11048, 35, 28)]),
    ("giant_krill", "sunset_bay"):  Route(lap_ticks=1250, stops=[
        (5934, 31,  5), (6189, 33, 56), (6189, 40, 24), (6188, 54, 56),
        (5933, 50, 16), (5933, 22,  8), (5933, 25, 41)]),
    ("giant_krill", "turtle_belt"): Route(lap_ticks=1410, stops=[
        (11815, 19, 29), (11815,  0, 49), (11816, 27,  6), (11815, 49, 35),
        (12071,  9, 16), (11815, 41,  6), (11814, 24, 45), (11559, 61, 10)]),
    # Haddock: anglerfish's light / misty sea / sea of shells -- transcribed but NOT the v1 default
}
```

Pure lookups, all unit-tested against fixed inputs:

- `stops_world(shoal, route) -> [(x, y), ...]`
- `nearest_stop(route, x, y) -> (index, (x, y), distance)`
- `next_stop(route, index) -> (index, (x, y))` (wraps)
- `route_bounds(route) -> (minx, miny, maxx, maxy)` and `in_route_area(route, x, y, margin)`
- `leg_ticks(route)` — `(lap_ticks - len(stops) * stop_ticks) / len(stops)`, the expected
  transit time used only to bound waits

The table is an **accelerator, never truth**: the live shoal object in `/objects` decides where
the shoal actually is. `next_stop` only ever aims the boat while the shoal is in transit; if the
shoal turns up somewhere else the ordinary follow corrects it on the next tick.

---

## 5. The routine

### 5.1 Config (plain `DEFAULT_CONFIG`, no bridge config)

Because `get_config` ignores `bridge_config`, wiring is only the two required steps
(`ROUTINE_FILES` + `RoutineChoice`) — no `ControllerConfig.java` section.

```python
DEFAULT_CONFIG = {
    "shoal": "giant_krill",
    "route": "great_sound",
    "trawlRange": 8,          # re-approach HYSTERESIS, not a parking distance (UNVERIFIED, F-I7)
    "netFullAt": 240,         # collect at this many fish, not at 250
    "collectTo": "cargo",     # "cargo" | "inventory"
    "stopWhenCargoFull": True,
    "safety": {},             # get_config forces stopOnInventoryFull off -- see below
}
```

`get_config` forces `safety.stopOnInventoryFull` **off**: the engine's safety check runs before
`tick()`, so it would win the race against our own `collect` phase. Same reason `mining.py`
forces it off for its banking trip.

### 5.2 Phases — derived from live state every tick, never from stored state

`derive_phase(snapshot) -> str` is a **pure function** of a `TrawlSnapshot` (player tile, boat
speed, `navigating`, net object presence + depth, nearest shoal object + distance, `netFish`,
cargo used/capacity, ticks since the last Sailing XP). Pure so it is table-tested, and derived
so a restart mid-trip cannot desync (`ctx.state.data` is wiped, the boat's state is not).

| Phase | Entered when | Does |
|---|---|---|
| `lost` | no trawling-net object anywhere in `/objects`, or `/trawling.netFish is None` | one-line safety stop: "start this routine standing on your boat, with a trawling net built and a crewmate on the helm" |
| `travel` | no shoal object in the scene | if inside `route_bounds` + margin, `sailing.sail_to` the nearest route stop; otherwise safety stop naming the route (v1 does no cross-ocean navigation) |
| `approach` | shoal present, boat further than `trawlRange`, **or** boat still moving | `sailing.sail_to` the shoal's live tile (or `next_stop` while the shoal is in transit), then `sailing.stop_boat` |
| `lower` | in range, boat stopped, net depth reads raised | walk to the net once, `click_menu_action(net, "Lower")`, confirm on varbit 19206 |
| `trawl` | in range, boat stopped, net lowered | **the burst** (§5.3) |
| `collect` | `netFish >= netFullAt` | `click_menu_action(net, "Collect-from")`, answer the destination dialogue, confirm `netFish == 0` |
| `done` | cargo hold full and `stopWhenCargoFull` | collect what is in the nets, raise the net, safety stop |

Phases **chain inside one `tick()`** (`TICK_CHAIN_MAX`), with pause/safety/`break_due` re-checked
between them — the same shape as `corrupted_gauntlet` and `mastering_mixology`. `break_due` takes
the breaks config (the `corrupted_gauntlet` crash of 2026-09-06: it is **not** a no-arg call).

### 5.3 `trawl_burst` — the tick-efficient core

This is where the user's "make it as tick perfect as can be" lives.

```
mark = ctx.events.mark()
walk to the net once (it is a few tiles away on a sloop deck)
aim once:  hover_entity(net, refresh_target=object_refresh_target(net tile, 59755))
loop, until the stop ends / the net fills / the budget expires:
    outcome_waits.tick_pause(ctx, 1, phase=CLICK_PHASE_S)   # ride the tick clock
    controller.click_human(ser)                             # click IN PLACE -- no flight
    events = ctx.events.since(mark); mark = ctx.events.mark()
    fold Sailing XP -> heartbeat, Fishing XP -> catch counter
    every AIM_REFRESH_TICKS, or whenever /player says we moved: re-aim (hover_entity)
    every NET_POLL_TICKS (~8): read /trawling.netFish
    if no Sailing XP for RANGE_LOST_TICKS (6): return "out of range" -> approach
    if a level-up / "click here to continue" dialogue is up: Space, then carry on
    check pause / safety / break_due every CHECK_TICKS
```

Five deliberate choices:

1. **Click in place, not a flight.** The boat is stopped and the player is standing at the net,
   so the net's projection is static; `click_human` is the ~92 ms in-place click already
   measured in `giants_foundry`. A fresh `click_entity` flight per tick costs ~1 s and cannot
   hold a 0.6 s cadence at all. This is the mahogany-homes burst lesson applied verbatim:
   *aim once, click in place, re-aim when the player has moved or the model changed.*
2. **No simulated misclick in the burst.** A sloop deck is a few tiles across and
   `MISCLICK_OFFSET_PX` (20–45 px) is wider than the net — a miss lands on another facility or
   walks the player off the net. Same rule as `banker.py`'s bank buttons and
   `sailing._FIXTURE_CLICK_KWARGS`.
3. **The heartbeat is the range test, not distance.** Distance to a 6×6 moving object is a
   guess; "the game granted Sailing XP" is the game's own answer. Distance only *aims* the boat.
4. **`/trawling` is polled every ~8 ticks, never per tick.** The burst must not spend its tick
   budget on HTTP; the pushed XP events carry the per-tick information.
5. **The burst yields at the stop boundary**, not on a wall clock: it returns as soon as the
   heartbeat stops (the shoal has left), so the `approach` phase starts within ~4 s of the shoal
   moving rather than after a fixed sleep.

`before_break` collects the nets before the break starts (fish in the nets are discarded on a
forced logout, and a break is minutes long) — the same reason `chinchompa_hunting` picks its
traps up in `before_break`.

### 5.4 Failure handling

- **No Sailing XP for `RANGE_LOST_TICKS` while operating** → not in range → `approach`. Counted;
  `APPROACH_MAX_TRIES` consecutive approaches that never re-establish the heartbeat → safety stop.
  (A ceiling on every loop: the mahogany-homes lesson.)
- **`sail_to` returns False** → counted, re-planned up to `SAIL_MAX_TRIES`, then safety stop.
- **Net never lowers** (varbit unchanged after the budget) → retry through the menu, then stop.
- **Cargo hold full while collecting** → `done`.
- **`_stuck_watch`**: a progress signature of `(netFish, cargo used, boat tile, phase)` unchanged
  for `STALL_WARN_S` logs `[!] trawling: STUCK?` and safety-stops at `STALL_STOP_S`.
- **Never world-hop, never teleport, never disembark** while `netFish > 0`. This routine
  deliberately has no world-hop path at all.

---

## 6. Preconditions the routine checks at startup

`_startup_checks` (deferred until the inventory or equipment reads non-empty — the GOTR
empty-first-tick lesson) verifies, and safety-stops with one clear sentence naming the
missing item:

1. A trawling-net object is in `/objects` (we are aboard a boat that has one).
2. `/sailing.navigating` is true **while the player is not standing at the helm** — i.e. a
   crewmate is holding it. Without this the boat cannot be steered from the net.
3. Fishing level ≥ the configured shoal's requirement.
4. The player's tile is inside the configured route's bounds (+ margin).

---

## 7. Testing

`tests/test_trawling_routes.py` — pure, exhaustive: region→world conversion against
hand-computed fixtures (Great Sound stop 1 = `(1616, 3379)`, stop 4 = `(1574, 3338)`),
`nearest_stop`/`next_stop` wrap-around, `route_bounds`, and the ordering sanity check that no
consecutive leg of a stored route exceeds `leg_ticks * MAX_BOAT_TILES_PER_TICK` (2.5).

That check is not vacuous — it is the evidence that the Wiki's label 1..7 really is the
visiting order. Computed across all four Giant krill routes the implied speeds land in a tight,
plausible band: Great Sound 1.18 tiles/tick average and 1.57 worst leg, Simian Sea 1.47 / 1.77,
Turtle Belt 1.26 / 1.56. A wrong ordering would scatter these.

`tests/test_deep_sea_trawling.py` — hermetic (no bridge, no serial), fake clock installed the
way `test_blast_furnace.py` does, and a **real `EventStream` driven by a scripted
`controller.poll_udp`** so `await_outcome`'s own mark/since semantics are what gets exercised.
Coverage: the `derive_phase` truth table (one case per phase plus the transitional reads), the
burst's heartbeat-loss exit, the mark-advance discipline (a test that fails if `mark` is
re-taken at the top of the loop), the collect confirm, `before_break` collecting, and every
ceiling firing.

**Feed each event from the click that causes it** — the mixology lesson: a script that queues a
later click's verdict up front delivers it during the earlier click's wait, which cannot happen
live, and the "failure" it produces is the test's.

---

## 8. Live-verification checklist (the first session's worklist, in order)

Nothing below has been measured. These are ordered so that the earliest failure is found first.

1. **Does `/objects` list the shoal at all**, and under what `name` — is the `<col=FFFF00>` tag
   present in `ObjectComposition.getName()`? (Match by **id** first, name second.)
2. **How does a moving shoal appear** — one object that changes tile, or a despawn/spawn pair
   per tick? If the latter, does it flood `SPAWN_EVENT_CAP_PER_TICK` (40)?
3. **What is "in range"** — park at increasing distances and find the largest boat→shoal centre
   distance that still produces the Sailing XP heartbeat. That measurement calibrates
   `trawlRange`, but note what `trawlRange` actually does (F-I7): it is **the distance past
   which `derive_phase` decides to re-approach — a hysteresis threshold, NOT a parking
   distance**. `approach_phase` sails at the shoal's own tile and `sailing.sail_to` returns as
   soon as it is inside `_dynamic_brake_tiles()`, floored at `SAIL_ARRIVAL_TOLERANCE` (5), so
   the boat parks 0–5 tiles from the shoal centre whatever this is set to. Failure mode if the
   guess is too LARGE: `derive_phase` keeps answering `trawl` while the heartbeat says we are
   out of range, so the approach recovery that `APPROACH_MAX_TRIES`' stop message names is
   never actually attempted. Steering to a deliberate stand-off distance is a separate change
   and needs this measurement first.
4. **Does the heartbeat really arrive every 2 ticks under a 1-per-tick click**, and 3 ticks
   without? Measure both; this is the entire premise.
5. **Net depth varbit 19206**: its value raised vs lowered, and whether it is populated with the
   Sailing sidepanel *closed*.
6. **Can the boat be steered from the net with a crewmate on the helm** — does a minimap click
   change heading while `/sailing.navigating` is true and the player is away from the helm?
7. **Collect-from**: which interface/dialogue opens, and the exact option text for the cargo hold.
8. **The Wiki stop table**: log the shoal's tile at each stop for one lap and diff against
   `stops_world("giant_krill", "great_sound")`. If it disagrees, the table is wrong, not the game.
9. **Boat speed vs 1.18 tiles/tick** — confirm the boat arrives at the next stop before or with
   the shoal.
10. Chat lines for a full net / full cargo hold (nothing is parsed today; add them if they exist).
11. **Does `navigating` actually discriminate the crewmate case?** (F-I3.) `SailingProvider`
    defines it as `WorldView.getYellowClickAction() == CLICK_ACTION_SET_HEADING` — "a tile click
    currently resolves to steering", a property of THIS player's click mode. It is not a crew
    readout, and the bridge has none. So: does it read **true** while the player stands AWAY
    from the helm with a crewmate assigned, and **false** without one? If it does not
    discriminate, this precondition needs a real signal (a new provider field), and the startup
    check as written passes in the one configuration the routine cannot work in.
12. **`sailing.use_helm`'s confirmation is vacuous here.** It clicks the helm and then polls
    `/sailing.navigating` — which this routine has already asserted is true — so it returns True
    on its first poll whether or not the click did anything. Every `sail_to` in this routine
    therefore has an unverified helm step; watch for a leg that never moves.
13. **Which dialogue does Collect-from open, and does `/state.dialogOpen` see it?** CLAUDE.md
    records that neither `/state.dialogOpen` nor the m8aq dialogue reader sees widget group 270
    — the skill-select modal family that Giants' Foundry's crucible and the Blast Furnace
    dispenser both use. `_dismiss_dialogue_if_open` keys on `dialogOpen`, so if Collect-from is
    a 270-family modal the detector is blind to it and the burst will keep clicking behind it.
14. **How long after the Collect-from click does its interface appear?** `_answer_collect_
    dialogue` presses its number key immediately. The one measured analogue in this repo (the
    Blast Furnace dispenser's Take) opens the modal ~1.5 s after the click. Once measured, gate
    the press on the interface actually being up rather than pressing blind.

---

## 9. Deliberately out of scope for v1

- **Banking.** The cargo hold is 240 slots and the Rope net's expected yield is roughly 5 fish
  per minute (10.5 % catch chance at Fishing 75, one roll per 2 ticks, up to 2 fish per catch),
  so a hold lasts many hours. `done` stops cleanly instead. A bank-boat round trip is its own
  sub-project and needs ocean navigation we have not verified since 2026-08-05.
- **Chum station baiting** (+20 % fish). The station's `Operate` opens an interface we have not
  seen; guessing at it blind is exactly the kind of thing the golem-crafting notes warn about.
  Worth doing second — it is a large yield gain for ~4 % of a 90 s stop.
- **Depth management.** No shoal we can reach has more than one depth. The `ShoalSpec.depths`
  field exists so the phase can be added without reshaping the data.
- **Fish crates** (10 fish per cargo slot). Only matters once banking exists.
- **A second net / a crewmate on the second net.** The MMG's setup; needs crew assignment.
- **Mixed shoals.** `SHOALS[...].mixed_ids` is matched so a Shimmering shoal that replaces our
  Giant krill shoal is still followed and fished, but nothing is done differently for it.

---

## 10. Files

| File | Change |
|---|---|
| `src/main/java/com/example/providers/TrawlingProvider.java` | new — `GET /trawling` |
| `src/main/java/com/example/controller/ControllerPlugin.java` | register the provider |
| `src/main/java/com/example/controller/RoutineChoice.java` | `DEEP_SEA_TRAWLING` entry |
| `ControllerAddon/controller.py` | `get_trawling()`; `ROUTINE_FILES["DEEP_SEA_TRAWLING"]` |
| `ControllerAddon/trawling_routes.py` | new — shoal specs + route stop tables + pure lookups |
| `ControllerAddon/routines/deep_sea_trawling.py` | new — the routine |
| `ControllerAddon/tests/test_trawling_routes.py` | new |
| `ControllerAddon/tests/test_deep_sea_trawling.py` | new |
| `CLAUDE.md` | a "Current state of routines/deep_sea_trawling.py" section |

**A client rebuild (`./gradlew.bat run`) is required** before `/trawling` exists or the routine
appears in the Controller Settings dropdown.
