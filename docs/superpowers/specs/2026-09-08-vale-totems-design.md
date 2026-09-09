# Vale Totems (Forestry, Auburn Valley) — routine design

Written 2026-09-08 from the user's recording
`ControllerAddon/recordings/2026-09-08_135457_vale-totem.*` (8m22s, 194 actions, ~1.9 laps),
the vendored m8aq `ValeTotems` reader, the Plugin Hub **totem-fletching** source
(`pluginhub/totem-fletching`, commit `7c3b416`), the decoded cache (dump `5bc2ff1e`) and the
OSRS Wiki (Vale Totems rev 15262757). Live bridge reads were taken with the account standing
in the valley.

Every fact below is MEASURED unless it is in the live-verification checklist at the end.

## What the activity is

Eight totem sites ring the Auburn Valley. At each: **Claim** the offerings an ent left, **Build**
a totem (1 log), **Carve** three animals into it, **Decorate** it with four fletched bows. Ents
walk between the sites every 80 ticks (48s) leaving offerings at completed totems, and each visit
costs the totem one durability until it collapses and must be rebuilt.

**The three carvings must match the three spirits currently standing at that site.** Wrong animals
cost 25% of the decorate XP *each* and produce far fewer offerings. Of the five spirit variants
only three are active at a site at any time, and — per the wiki, and seen once in the recording —
**they can change while you are carving**.

Per complete totem: 5 logs (1 to build, 4 fletched into bows to decorate). 40 logs per lap.

## The state model: one HTTP read, no Java change

`GET /api/minigames/vale-totems` (`m8aq/api/minigames/ValeTotems.java`, already vendored, already
discovered by the reflection catalog) **answers on the current build** — VERIFIED live 2026-09-08.
Per site it serves `siteNumber`, `baseValue`, `wood`, `baseCarved`, `baseMultilocValue`,
`bottom`/`middle`/`top` (each `{value, spirit}`), `decorations`, `nearbySpiritValues`,
`nearbySpirits`, `durabilityRemaining`, `unclaimedOfferings`, `trailBuffActive`,
`multiAnimalSpirits`, `multiAnimalMask`; globally `researchPoints`, `totalUnclaimedOfferings`,
`trailXpBuffActionsRemaining`, `miniquestProgress`, `atValeTotems`.

**The reader's helper METHODS are not in the JSON.** `isFullyCarved()`, `isFullyBuilt()`,
`isFullyDecorated()`, `isOfferingCapped()`, `isStarted()`, `getMissingNearbySpirits()` and
`getIncorrectCarvings()` are Java methods and Gson serializes fields only — the same trap this
repo already records for GOTR's round phase and Wintertodt's `isOperational`. `vale_totems.py`
re-derives them from the serialized fields.

Sample of the live read, site 4, a correctly-finished totem:

    "baseValue": 4, "wood": "YEW", "baseCarved": true, "baseMultilocValue": 12,
    "bottom": {"value": 11, "spirit": "JAGUAR"}, "middle": {"value": 12, "spirit": "EAGLE"},
    "top": {"value": 14, "spirit": "SCORPION"}, "decorations": 4,
    "nearbySpiritValues": [2, 3, 5], "nearbySpirits": ["JAGUAR", "EAGLE", "SCORPION"]

Sites 1-3 read back `baseValue 0` (needing a build) carrying 52 / 65 / 60 unclaimed offerings,
with `totalUnclaimedOfferings` 385 and `researchPoints` 94 — so the per-site offering counts are
readable for sites the player is nowhere near, which is what the circuit planning rests on.

### Re-derived predicates

- `needs_build` — `baseValue == 0`
- `needs_carve` — `not baseCarved`
- `missing_spirits` — nearby spirits whose carved value (`nearbyValue + 9`) is not among
  `{bottom.value, middle.value, top.value}`. This is `TotemService.getCarvedAnimalsStatus`
  verbatim (`progressKey = animal + 9`) and matches m8aq's `Spirit.carvedValue` table
  (BUFFALO 10, JAGUAR 11, EAGLE 12, SNAKE 13, SCORPION 14).
- `needs_decorate` — `decorations < 4`
- `offerings_waiting` — `unclaimedOfferings > 0`

`unclaimedOfferings` is per-site and varbit-backed, so **the routine knows what is waiting at a
site before it can see it**. Same for the spirits: no NPC scan is needed anywhere.

### Why not `/objects`

The site base loc is a multiloc whose op1 cycles Build -> Carve -> Decorate
(`ent_totems_base_none` op1=Build name="Totem site"; `ent_totems_base_yew` op1=Carve name="Yew
totem"; `ent_totems_carved_base_yew` op1=Decorate name="Yew totem"). That works, and the recording
confirms all three fire on the same object id — but the name is identical for the last two, so
**the action is the only discriminator**, the Wintertodt brazier lesson again. The reader is
strictly better: one request for all eight sites, no scene needed, and it carries the spirits and
offering counts an object scan cannot give. `/objects` is used only to get a clickable screen
position for the site being worked.

## The two interfaces are the SAME widget group — this is the trap

Both the carve prompt and the fletch prompt are `InterfaceID.Skillmulti` = **group 270**
(`CarvingActionOverlay`: `TOTEM_CARVING_TEXT_WIDGET = InterfaceID.Skillmulti.TITLE`,
`FIRST_CARVING_WIDGET = InterfaceID.Skillmulti.A`). Decoded from `runelite-api` gameval:

| widget | id |
|---|---|
| `Skillmulti.TITLE` | **270.5** |
| `Skillmulti.A … E` | **270.15 … 270.19** |

**Every number-key press is gated on 270.5's text** (USER, 2026-09-08):

- `"What animal would you like to carve?"` -> the carve prompt. Options A..E are the five animals
  in fixed order, so **key `n` carves the spirit whose `nearbyValue` is `n`** — and
  `nearbySpiritValues` hands `n` over directly, no name mapping.
- `"What would you like to make?"` -> the fletch prompt.

Order 1 buffalo / 2 jaguar / 3 eagle / 4 snake / 5 scorpion is confirmed **three independent
ways**: the 14 builds in the recording (every key pressed matches the spirits present), the
cache's `Spirit` enum, and the plugin's fixed `A..E` order.

**The fletch prompt is answered with `3`, never `Space`** (USER). `Space` repeats the *last
crafted item* — it worked in the recording only because that session had already been making
longbows. A fresh login, or one manual fletch of something else, and `Space` silently produces the
wrong item for the whole run; the failure then surfaces at the *next totem* as "You don't have
anything to decorate the totem with", far from its cause. The bow option index is therefore a
property of the configured wood, stored beside its log and bow names.

## Fletching overlaps the walk, for free

MEASURED: knife-on-logs at t=35.9 from (1473,3321), prompt answered, then five bows delivered at
38.2 / 40.0 / 41.8 / 43.6 / 45.4 while the player ran ~30 tiles to the next site's Offerings.
**Fletching continues while running.** So the fletch is fired as the last act at a site and the
leg walk overlaps it; one bow per 1.8s (3 ticks), 4 needed per totem.

## The circuit

Ring walked in descending site number — `6 -> 5 -> 4 -> 3 -> 2 -> 1 -> 8 -> 7 -> 6`, as in the
recording (both laps).

| site | totem tile | offerings | stand tile | base id | offerings id |
|---|---|---|---|---|---|
| 1 | (1370,3375) | (1370,3374) | (1369,3375) | 57016 | 57020 |
| 2 | (1346,3319) | (1347,3319) | (1346,3318) | 57022 | 57026 |
| 3 | (1385,3274) | (1385,3275) | (1386,3274) | 57028 | 57032 |
| 4 | (1413,3286) | (1412,3286) | (1413,3287) | 57034 | 57038 |
| 5 | (1438,3305) | (1438,3306) | (1437,3305) | 57040 | 57044 |
| 6 | (1477,3332) | (1478,3332) | (1477,3331) | 57046 | 57050 |
| 7 | (1453,3341) | (1452,3341) | (1453,3340) | 57052 | 57056 |
| 8 | (1398,3329) | (1398,3330) | (1397,3329) | 57058 | 57062 |

Offerings id is always `base + 4`. Bank booth `57330` @ (1416,3351), stood (1416,3350).
Log balances (Agility shortcuts) `56990` @ (1401,3290) and `56991` @ (1453,3335).

### Walking: worldmap cannot help, and that is a repo-wide finding

**`worldmap.py` has no usable collision data for Varlamore.** MEASURED 2026-09-08:
`reachable_from(..., radius=40)` returns **1 tile** at every totem site, at the bank, at Civitas
illa Fortis, at Aldarin and at the Hunter guild, while Varrock square returns 5089. Every
`find_path` between two totem sites answers `None`. This is not tuning — the EthanApi map predates
this content. It also bears on `mahogany_homes`' Civitas references and on any teleport planning
into Varlamore; regenerating the map stays the separate deferred follow-up this repo already
records.

So the circuit is walked from **waypoint tables extracted from the user's own walked path** — one
tile per tick, so a real traversed route. USER chose this over regenerating the map. Extracted
2026-09-08; six of the eight legs were walked twice and agree, legs 7->6 and 8->7 once:

| leg | waypoints | max gap (tiles) |
|---|---|---|
| 1->8 | 6 | 12.0 |
| 2->1 | 7 | 12.0 |
| 3->2 | 7 | 12.1 |
| 4->3 | 5 | 10.8 |
| 5->4 | 7 | 12.0 |
| 6->5 | 6 | 11.7 |
| 7->6 | 5 | 12.2 |
| 8->7 | 7 | 11.2 |

Max gap ~12 tiles, comfortably inside the measured 28-tile minimap reach and under
`travel.WALK_HOP_MAX_TILES` (24), so each waypoint is one `travel._hop_to`. Leg 4->3 passes
(1402,3290), one tile from log balance `56990`, so the shortcut is already on the recorded path
rather than something the routine has to reason about. Leg 8->7 passes the bank.

**Consequence of having no router: the routine cannot recover from being lost.** It must start in
the valley, and a player outside the ring safety-stops rather than improvising — the same rule
`wintertodt.py` applies to region 6462. The reader's own `atValeTotems` (a serialized field) is
the test.

## Per-site ladder

Re-derived from the live reader every pass. Nothing load-bearing lives in `ctx.state.data`.

1. **Claim** — `unclaimedOfferings > 0`
2. **Build** — `baseValue == 0`. Consumes 1 log and restores 15% run energy.
3. **Carve** — while `not baseCarved`: click Carve, confirm 270.5's title, press the number of the
   first missing spirit. **Re-read the reader between every carve** — the wiki says the spirits can
   change mid-carve, and one build in the recording ended with a carve that no longer matched the
   spirits tracked at build time.
4. **Decorate** — while `decorations < 4` and a bow is held.
5. **Fletch, then walk.** Fire knife-on-logs, answer 270.5 with the wood's bow index, then hop the
   leg; the fletching runs underneath it.

A healthy fully-decorated totem needs no work and falls out of the ladder — it is claimed and
passed. That is what makes the ladder, rather than a fixed script, the right shape: the recording
rebuilds every site every lap only because a yew totem's 5 durability is spent by the time the
~4.4-minute lap comes round.

## Banking

Site 8 sits on the bank leg. When logs run out: `banker.open_bank` on booth `57330`, Withdraw-All
logs, Esc, **`Fill` the log basket** — three rounds, the recording's own sequence, ending with ~28
in the basket and ~23 loose, about 51 logs, about one lap. `Empty` the basket mid-lap to refill the
inventory ("You empty as many logs as you can carry.").

The basket is driven in **both** directions here, which is new: `banker.empty_log_basket` exists
but is the bank-context `Empty-to-bank`. Field `Empty` and bank-side `Fill` are the basket's own
item ops and need their own helpers. Neither gets the simulated misclick — the neighbouring
inventory slot is the knife.

Vale offerings stack and are never banked.

## Modules

- **`ControllerAddon/vale_totems.py`** — pure state. Parses the reader, re-derives the dropped
  predicates, answers `next_action(site)` -> `claim | build | carve(n) | decorate | done`.
  No HTTP, no client, no serial port; fully table-testable.
- **`ControllerAddon/vale_totem_route.py`** — data only: the site table above, the bank record and
  the per-leg waypoints. Same shape as `trawling_routes.py`: an ACCELERATOR, never truth — the
  reader decides what to do, the table only says where to walk.
- **`ControllerAddon/routines/vale_totems.py`** — the routine.
- **Java**: `RoutineChoice.VALE_TOTEMS`, `ROUTINE_FILES`, and a Vale Totems panel section with the
  wood dropdown (log name, bow name, bow option index, Fletching level per wood). Client rebuild
  needed for the dropdown only — **not** for state.

Reused rather than rebuilt: `outcome_waits` (`await_pre`/`await_long`/`tick_pause`/`walk_budget`),
`wintertodt.fletch_phase`'s knife-on-item shape, `banker.open_bank`/`withdraw_all_shift`,
`travel._hop_to`, `controller.get_widget`, `controller.open_inventory_tab`.

## Deliberately out of scope for v1

- **Ent trails** (objects `57115`/`57116`; +15% XP on the next 4 build/carve actions and double
  offerings from that ent). USER's call. `trailBuffActive` and `trailXpBuffActionsRemaining` are
  already in the reader when this is wanted.
- Travel *to* the valley. The routine starts there.
- Non-yew woods beyond the config record — the dropdown exists, but only yew is measured.

## Live-verification checklist (the first session's worklist)

1. The fletch prompt's title really is `"What would you like to make?"` and `3` selects longbow.
2. The carve prompt's title really is `"What animal would you like to carve?"`, and 270.5 is
   readable via `/widget` while it is open.
3. The waypoint hops clear both log balances (`56990` on leg 4->3, `56991` near site 7) — the one
   place the recorded path might not survive being re-walked as discrete hops.
4. `nearbySpirits` updates mid-carve, and the re-read between carves picks the change up.
5. Bank-side `Fill` and field `Empty` on the log basket resolve to the right options.
6. Whether a totem with durability left is ever met still standing and decorated, i.e. that the
   claim-and-pass branch is exercised at all.
7. Whether `3` holds as the bow index for any non-yew wood, if the dropdown is moved off Yew.
