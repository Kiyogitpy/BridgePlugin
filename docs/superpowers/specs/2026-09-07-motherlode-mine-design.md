# Motherlode Mine — design

Written 2026-09-07 from the user's recording
(`ControllerAddon/recordings/2026-09-07_111533_motherload-mine.*`, 2m55s, 38 actions — a
partial loop, not a full run), live bridge reads against the client standing in the mine, the
decompiled RuneLite `MotherlodePlugin`, and the Plugin Hub **Motherlode Mine Improved**
(`tictac7x-motherlode`) source the user pointed at.

USER's decisions, verbatim where they matter:

- Mine **upstairs, in the pocket behind Mercy** — "there is a spot where we dont have to clear
  the rockfall. so upstairs but behind the lady".
- **Fill the inventory entirely** before every hopper trip.
- **Mine until the sack is full, then one big collect/bank cycle.**
- Repair the broken strut **only when it actually costs us**: "ONLY repair broken strut if we
  cant deposit our ore. otherwise leave it broken. if we are lucky another player fixes it".
- Check the kit (pickaxe, hammer) at startup.
- **The upper hopper is not unlocked yet** — "need to unlock that hopper we cant use it yet".

## The geometry, all MEASURED live 2026-09-07

Region 14936, everything on **plane 0** — the upper level is not a plane, it is a geographically
separate part of the same plane, reached by ladders.

| Thing | id | tile | action |
|---|---|---|---|
| Ore vein (live) | 26661-26664 | many | `Mine` |
| Depleted vein | 26665-26668 | — | **no actions at all** |
| Hopper (lower) | 26674 | (3748,5672) | `Deposit` |
| Hopper (upper, 50-nugget unlock) | 26674 | (3755,5677) | `Deposit` |
| Ladder down (from upstairs) | 19045 | (3755,5674) | `Climb` |
| Ladder up (from downstairs) | 19044 | (3755,5673) | `Climb` |
| Sack | 26688 (GroundObject) | (3748,5659) | `Search` |
| Bank deposit box | 25937 | (3759,5664) | `Deposit` |
| Bank chest | 26707 | (3761,5666) | `Use` / `Collect` |
| Strut intact / broken | 26669 / 26670 | (3742,5669) (3744,5669) (3742,5663) (3744,5663) | `Hammer` when broken |
| Water wheel working / broken | 26671 / 26672 | (3743,5669) (3743,5663) | — |
| Rockfall | 26679 / 26680 | various | `Mine` |

Varbits, all readable today over `GET /varbit?id=` (no Java change):

- **5558 — pay-dirt in the sack.** Confirmed in the recording: 39 → 56 the moment 17 pay-dirt
  finished the conveyor. Read 27 live.
- **5556 — sack upgraded.** 0 → capacity **108**; 1 → 189 (`MotherlodePlugin`, `tictac7x`).
- **2086 — 1 while upstairs**, 0 downstairs. Flipped exactly on both ladder climbs in the
  recording and reads 1 with the player upstairs now.

Two facts that shape everything below:

- **Ore veins are `WallObject`s** (`/objects` `objectType`), and a depleted vein is a *different
  id with no actions*. So `gathering.pick_target`'s existing "must offer the action" filter
  already skips spent veins, and `gathering.gather`'s "done when the id no longer stands on its
  tile" is already the right depletion test. Nothing in `gathering.py` needs to change.
- **`/objects` lists the WHOLE loaded scene, both floors.** Standing upstairs the bridge happily
  reports the lower hopper 8 tiles away, the sack at 21 and the struts at 11. Clicking any of
  them through the floor is the exact failure CLAUDE.md records at JEFF's and ROSS's, so every
  candidate is filtered by reachability before it is ever clicked (below).

## Floor and pocket: one flood, no coordinate tables

`tictac7x` hand-lists the upstairs tiles in a `Sectors` table. We do not need it:
`worldmap.reachable_from(player)` already answers this, because **the static collision map models
the two floors as disconnected**. MEASURED from the pocket behind Mercy at (3752,5680):

    reachable tiles: 45  (y 5668-5685)  -- the NW pocket plus the SE corridor
    upstairs-adjacent (3755,5675) IN    -- the ladder-down approach
    lower hopper / sack / deposit box / struts / far NE upstairs: all OUT
    find_path(upstairs -> sack) -> None

So one `reachable_from` per tick plus `travel.can_reach` is both the floor test and the "am I
still in the right pocket" test, using machinery this repo already trusts, and it degrades the
way that machinery already degrades (an empty flood means "cannot tell", never "refuse to work").

## The loop

`routines/motherlode_mine.py`, phases **derived from live state every tick** (never from a
stored phase alone), in the priority order below:

1. **collect** — the sack is full (or the run is ending): go downstairs, `Search` the sack,
   `Deposit inventory` at the deposit box, repeat until varbit 5558 reads 0, climb back up.
2. **hopper** — the inventory is full: deposit at the nearest *reachable* Hopper. Upstairs that
   means climbing down and back up until the 50-nugget upper hopper is unlocked.
3. **mine** — `gathering.gather` over the reachable veins near the anchor.

The **anchor** is the tile the routine started on (or `anchorTile` in config). Veins are
filtered to `veinDistance` (8) of the anchor *and* reachability from the player, which is what
keeps it in the pocket behind Mercy instead of wandering the whole upstairs.

The mining half is `gathering.py` **unchanged**: the routine builds a synthetic config
(`{"rockNames": ["Ore vein"], "maxRockDistance": ...}`) and calls `gathering.pick_target` /
`gathering.gather` directly, so it inherits the measured tick-perfect reaction, the pre-hover of
the next vein, the walk-budgeted deadlines and the `exclude=` handling of the tick-cached scan.
It does **not** use `gathering.tick`, because a full inventory here means "go to the hopper",
which is not one of that function's three policies.

### The sack, and pay-dirt in transit

Capacity is 108 (189 upgraded). A hopper deposit does not reach the sack instantly — the
conveyor takes several seconds, and `mlm-qol`'s `SackTracker` exists entirely for this. We track
`pending` (pay-dirt deposited but not yet reflected in 5558) and treat
`sack_effective = varbit + pending`, retiring `pending` as the varbit catches up or after
`PENDING_MAX_S`.

Another trip is started only while `sack_effective + 27 <= capacity`; otherwise the collect
cycle runs. That is the user's "mine until the sack is full, then one big collect/bank cycle",
and it is also the thing that stops pay-dirt being thrown away past a full sack.

### Ore out

`Search` the sack fills the inventory with ore; the deposit box then takes it. **Which deposit
we use depends on whether we are carrying a hammer:**

- **No hammer** (the normal case, and the account's current state): one click on `Deposit
  inventory` (widget 192.31), exactly as the recording. The user's existing deposit lock keeps
  the open gem sack. The routine checks the gem sack survived the first deposit and says so
  loudly if it did not.
- **Carrying a hammer**: per-item `Deposit-All` on each ore stack in the 192.24 grid instead
  (karambwan_fishing's pattern). This costs a handful of clicks per trip and is only paid by a
  run that wants repairs, but it cannot deposit the hammer — no deposit-lock fiddling, nothing
  to get wrong.

### Repairs — only when they cost us

The wheel breaking does **not** stop the hopper accepting pay-dirt (the recording deposits 17
into a broken machine); it stops the sack filling. So:

- The refusal chat *"The machine will need to be repaired before your pay-dirt can be cleaned."*
  on a hopper deposit marks the machine broken.
- While broken the routine **keeps mining and depositing** and does nothing else — the user's
  "if we are lucky another player fixes it". The sack rising again clears the flag.
- Only when the machine has been broken for `repairPatienceS` (180s) **and** we hold a hammer
  does the routine take the repair: it is already downstairs on a hopper trip, so it walks to
  the nearest broken strut, `Hammer`s it, and confirms on the object turning 26670 → 26669 (the
  recording also shows +126 Smithing xp per strut).
- No hammer → repairs are disabled for the run with one log line, never a safety stop. There is
  no way to buy one in the mine; the bank chest (26707) is a possible future restock and is
  deliberately out of scope for v1.

### The upper hopper, when it is unlocked

`upperHopper: "auto" | "never" | "always"`, default **auto**: try a reachable same-floor hopper
first, and if the deposit leaves the pay-dirt in the inventory, latch
`mlm_upper_hopper_locked` for the session and use the lower one from then on. One wasted click
per run today; the day the user pays Percy the 50 nuggets every ladder trip disappears with no
code or config change. (Wiki, `Hopper (Motherlode Mine)` rev 15147049 — the upper hopper costs
50 golden nuggets; the upper *level* itself was the 100-nugget unlock the account already has.)

### Rockfalls

Not cleared. The whole point of the pocket behind Mercy is that none is in the way. A rockfall
that ever *is* in the way shows up as "no reachable vein" and is left to the stuck watch rather
than mined — mining one costs ~4s and the pocket does not need it.

## Safety and stopping

- `_stuck_watch` on a progress signature (player tile, inventory size, sack varbit, phase):
  warn at `STALL_WARN_S`, safety-stop at `STALL_STOP_S`. CLAUDE.md's standing rule — a silent
  loop is worse than a crash.
- Every ladder climb is confirmed on varbit **2086** flipping, budgeted for the walk, with a
  bounded retry.
- `before_break` finishes the current inventory into the hopper before a break starts, so a
  break never begins with 27 pay-dirt held and the sack half full.
- Startup: no pickaxe (wielded or carried) → safety stop. Not in region 14936 → safety stop
  naming the mine. Hammer absent → log, repairs off.

## What is Java, and what needs a rebuild

Only the wiring: `RoutineChoice.MOTHERLODE_MINE` and the `ROUTINE_FILES` entry. Everything else
is Python over endpoints that already exist (`/objects`, `/object/at`, `/varbit`, `/inventory`,
`/player`, `/widget`, `/widget/children`). The routine is runnable from the command line
immediately; the panel dropdown needs the client rebuild.

## Live-verification checklist (the first session's worklist)

1. Does `Deposit` on the upper hopper (3755,5677) leave the pay-dirt in the inventory while
   locked, and is that the only tell? (The `auto` latch depends on it.)
2. Real conveyor latency — fit `PENDING_MAX_S` from the gap between the hopper deposit and 5558
   rising. The recording's one sample is contaminated by a strut repair.
3. Does the ladder-down/up pair really flip 2086 within the budget, and does the walk back to
   the anchor put us in the same pocket?
4. Confirm a `Search` on the sack yields exactly `min(free slots, 5558)` ore, and that 5558
   reaching 0 is what ends the collect cycle.
5. Fit `veinDistance` — 8 from the anchor should cover the pocket behind Mercy without offering
   a vein across a wall (reachability already filters those, so a wrong value shows up as an
   idle routine, not a bad click).
6. Whether the gem sack survives `Deposit inventory` (the user's existing deposit lock).
