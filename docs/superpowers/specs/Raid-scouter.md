# Raid scouter (Chambers of Xeric) — design

Status: design approved 2026-08-29, not yet implemented.
Author: brainstormed with the user 2026-08-29.

## Goal

Reroll Chambers of Xeric instances until one matches a target rotation, hold that
instance, tell the user, wait for their main account to start the raid, then leave and
begin scouting a fresh instance. Repeat indefinitely.

The bot **never starts a raid and never fights anything.** It enters the start room,
reads the layout, and either leaves or waits. This is the single most important safety
invariant in the whole design.

### The target rotation

Approved by the user 2026-08-29 as the **exact** rule, not a preference ordering:

> The raid's ordered combat+puzzle room list must be **exactly four rooms**: `Vasa`
> first, followed by `Shamans`, `Vespula` and `Tightrope` in any order. Any extra
> combat or puzzle room disqualifies the layout.

| ordered rooms | verdict |
|---|---|
| Vasa, Tightrope, Shamans, Vespula | MATCH |
| Vasa, Vespula, Tightrope, Shamans | MATCH |
| Vasa, Shamans, Vespula, Tightrope, Crabs | no — five rooms |
| Tekton, Vasa, Shamans, Vespula, Tightrope | no — Vasa is not first |
| Vasa, Shamans, Vespula | no — Tightrope absent |

Note `Tightrope` is a **puzzle** room while `Vasa`/`Shamans`/`Vespula` are **combat**
rooms (`RaidRoom.java`), so the rule is stated over the combined ordered list — the
same list RuneLite's own `Raid.toRoomString()` prints. Scavengers, farming, resource
and start/end rooms are **not** part of the list and never affect the verdict.

**Precondition: the party must be set to SMALL raids.** A four-room layout is only
reachable there; large raids fill every floor with eight rooms and can even duplicate
puzzle rooms, so the target rotation is unobtainable. The routine cannot set this
itself — it is a manual setup step, and the routine should log loudly if it scouts
several instances that all exceed four rooms.

## Researched facts

Everything below was read out of the client we actually build against
(`data/decompiled/client-1.12.33/net/runelite/client/plugins/raids/`) or the OSRS Wiki
snapshot, on 2026-08-29. None of it is assumed.

### The client already solves the layout

`RaidsPlugin` is a **built-in** RuneLite plugin, on our classpath. It scouts on
`GameStateChanged` and on varbit 5432 flipping to 1, then posts `RaidScouted(raid,
firstScout)` on the EventBus. This is the source to consume; do not reconstruct the
layout from a nearer source (the 231.6 dialogue-widget lesson in CLAUDE.md).

Signals, all confirmed in `RaidsPlugin.java`:

| signal | id | meaning |
|---|---|---|
| in raid chambers | varbit **5432** | 1 = inside the chambers (lobby or raid) |
| raid party id | varp **1427** | changes when the party/instance dissolves; -1 = none |
| total points | varbit **5431** | 0 before the raid starts |
| raid started | chat `"The raid has begun!"` | `ChatMessageType.FRIENDSCHATNOTIFICATION` |
| raid complete | chat `"Congratulations - your raid is complete!"` | same type |
| party dissolved | `RaidReset` event | posted by the plugin |

### The room list is reachable with public API only

`Raid.getRooms()` (16 slots), `Raid.getLayout()`, `Layout.getRooms()` and
`Room.getPosition()` are all `public`. `Raid.getOrderedRooms()` and `toRoomString()`
are package-private, but they are two lines that we can reproduce:

```java
for (Room r : raid.getLayout().getRooms()) {
    RaidRoom room = raid.getRooms()[r.getPosition()];
    if (room != null) ordered.add(room);      // then filter to COMBAT | PUZZLE
}
```

So the provider lives in `com.example.providers` like every other provider. No
reflection, no vendored package, no edits to RuneLite source.

### Rooms can legitimately read as UNKNOWN, and that is not a "no match"

`determineRoom` maps instance template chunks to rooms and names every room we care
about directly (`RAIDS_VASA`, `RAIDS_SHAMANS`, `RAIDS_VESPULA`, `RAIDS_TIGHTROPE` are
all explicit switch cases). But `buildRaid` skips any room whose tile falls outside the
loaded 104x104 scene — the room grid is 4x2 rooms of 32 tiles per plane, i.e. 128 tiles
wide — and `Raid.updateLayout` fills those slots with `RoomType.getUnsolvedRoom()`,
which is `UNKNOWN_COMBAT` / `UNKNOWN_PUZZLE`.

`RotationSolver.solve` then repairs them against the four known combat rotations, but
it **needs at least two already-known combat rooms** and returns false otherwise.

Consequence, and it is load-bearing: **a layout containing any UNKNOWN room has not
been scouted yet.** Judging it produces a false "no match" and throws away a good
instance. The routine abstains and re-polls instead.

### Raid mechanics (OSRS Wiki, `Chambers of Xeric` § Mechanics)

- The instance belongs to the **party** (the leader's chat-channel). The layout is
  generated when the chambers are first entered.
- The start room holds a bank chest and is safe. The raid only truly begins when the
  leader starts it.
- **"no one may join the instance if they were not in by the time the raid started"** —
  this is the mechanic the whole loop rests on. Per the user 2026-08-29: *"You can
  rescout as long as the main lobby is closed (closed meaning when the main starts the
  raid) so we do not have to leave CC."* Once the main starts, the bot re-entering the
  chambers cannot rejoin that instance, so it gets a fresh one. **The bot never has to
  leave the clan chat.**
- If the leader leaves, leadership passes to the highest-ranked CC member (recruit+),
  or everyone is ejected. Not exercised by this design as long as the bot is not the
  leader — see the open question below.

## Architecture

### 1. New provider: `RaidsProvider.java`

Subscribes to `RaidScouted` and `RaidReset`, caches the last scout, serves `GET /raid`.
Not gated by `TickCache` — it is a varbit read plus a cached object, the same class as
`/player`.

```json
{
  "raidsPluginEnabled": true,
  "world": 420,
  "inRaid": true,
  "raidPartyId": 8812,
  "raidStarted": false,
  "totalPoints": 0,
  "scouted": true,
  "scoutId": 17,
  "firstScout": true,
  "gridBase": {"x": 3288, "y": 5320, "plane": 3},
  "layoutCode": "#SCPSFCP#",
  "rooms": ["Vasa", "Tightrope", "Shamans", "Vespula"],
  "allRooms": ["Start", "Vasa", "Scavengers", "...16 slots..."],
  "unknownRooms": 0
}
```

Field notes:

- **`scoutId`** — a counter incremented on every `RaidScouted`. It exists because of the
  `travel.py` lesson that *a finished path is not necessarily your path*: after
  re-entering, a poll landing before the new scout completes reads the **previous**
  instance's layout, which is complete, valid, and about a raid that no longer exists.
  The routine records the last `scoutId` it judged and refuses to judge it twice.
- **`unknownRooms`** — count of `UNKNOWN_COMBAT` + `UNKNOWN_PUZZLE` in `allRooms`. The
  routine treats `> 0` as "not scouted yet", never as a rejection.
- **`rooms`** — ordered, filtered to `COMBAT | PUZZLE`. This is the list the match rule
  is stated over.
- **`raidStarted`** — latched by a `ChatMessage` subscriber on `"The raid has begun!"`,
  cleared on `RaidReset` and whenever `raidPartyId` changes. A varbit alone cannot
  answer this.
- **`world`** — `client.getWorld()`. VERIFIED 2026-08-29 that **no existing provider
  exposes this**, so it has to be added here. It is the most important field in the
  alert: a matched layout the user cannot find the world for is useless.
- **`raidsPluginEnabled`** — checked via `PluginManager`. If the built-in Raids plugin
  is disabled, no `RaidScouted` ever fires and the bridge would report a permanently
  unscouted raid with no explanation. This field turns a silent dead loop into an
  actionable safety stop. An absent field (un-rebuilt client) reads as "cannot tell",
  same convention as `ObjectProvider.actions`.

A `@Subscribe` method must be named `on<EventSimpleName>` — `onRaidScouted`,
`onRaidReset`, `onChatMessage`, `onVarbitChanged`. One misnamed handler stops the
ENTIRE plugin from registering and the client comes up with no bridge at all.

**Requires a client rebuild + restart** (`./gradlew.bat run`) before `/raid` exists.

`ControllerAddon/controller.py` gains `get_raid()` alongside the existing accessors.

### 2. Routine: `routines/raid_scouter.py`

Match evaluation lives **here, in Python**, not in the provider. The Java side reports
rooms; the routine decides. Changing the target rotation must not need a rebuild.

```python
TARGET_FIRST = "Vasa"
TARGET_REST  = frozenset({"Shamans", "Vespula", "Tightrope"})
TARGET_EXACT = True          # no extra combat/puzzle rooms allowed

def evaluate(rooms):          # -> "match" | "reject" | "abstain"
```

`abstain` is returned for an empty list or any UNKNOWN room, and is never a rejection.

### 3. `tick()` phases (`ctx.state.data["rs_phase"]`)

`ctx.state.data` is wiped on restart while the character's position is not, so the
initial phase is **derived from live state** (`inRaid`, `raidStarted`, `scoutId`),
never defaulted — the mahogany-homes `mh_phase` lesson.

| phase | does | leaves when |
|---|---|---|
| `enter` | click the chambers entrance | `inRaid` true **and** `scoutId > last judged` |
| `scout` | poll `/raid`, run `evaluate` | `match` → `hold`; `reject` → `reroll`; `abstain` → keep polling |
| `hold` | fire the webhook once, anti-idle, watch for the handoff | handoff seen, or hold times out → `reroll` |
| `reroll` | leave the chambers | `inRaid` false → `enter` |

`hold` leaves on whichever the config selects:

- `EXIT_ON = "raid_started"` (**default**, matches what the user described) — waits for
  `raidStarted`, then leaves.
- `EXIT_ON = "main_arrived"` — leaves as soon as the configured main's display name
  appears in `/players`. See the scaling trade-off below. Only the `name` field is
  used: the start room is an instance, and `PvpProvider` still reads
  `player.getWorldLocation()` — it is not in the four providers fixed for instanced
  coordinates (CLAUDE.md), so **every x/y it reports in there should be assumed
  wrong**. Names are unaffected.

### 4. Anti-idle while holding

A held instance is worthless if the account logs out. OSRS's idle logout is ~5 minutes
without input, and the routine is doing nothing during `hold` by design.

The existing machinery mostly covers this: `human_delay` draws >= 2s already split into
human-length still segments with small out-and-back drifts, and `maybe_idle_mouse_move`
now triggers on time since the last *deliberate* cursor movement. But CLAUDE.md records
a **known blind spot** — a routine's own tight poll loop never enters that path. `hold`
is exactly such a loop, so it must not rely on the fidget: it issues an explicit camera
nudge (an arrow-key press) every `IDLE_NUDGE_S` (first draft: 120s, wants live tuning),
which is a real input event and unambiguously resets the timer.

### 5. Alerting: Discord webhook

Chosen by the user 2026-08-29 over terminal/desktop/in-game options. On a match, POST
once:

> `MATCH  world 420  party 8812` / `Vasa, Tightrope, Shamans, Vespula` / `holding — waiting for main`

**The webhook URL is a secret and must not be committed.** It is read from the
`RAID_SCOUT_WEBHOOK` environment variable, falling back to
`ControllerAddon/.raid_webhook` (gitignored, same shape as `.hm2_calibration.json`).
Missing URL is a startup warning, not a crash — the routine still works, it just logs
the match instead.

Every network failure is swallowed and logged. A webhook that is down must never stop
the scouting loop or wedge a held instance.

**This sends raid layout and world number to Discord**, an external service, which the
user explicitly asked for. Nothing else is sent — no account name, no position.

### 6. Safety stops

Rerolling is an intentionally unbounded loop, so the ceilings bound **failures**, not
iterations — a cooldown only sets the rate of an unbounded loop (CLAUDE.md).

| guard | fires when |
|---|---|
| `ENTER_MAX_CLICKS` | entrance clicked N times with `inRaid` still false |
| `SCOUT_TIMEOUT_S` | inside the chambers, no fresh `scoutId` within N seconds |
| `REROLL_MAX_CLICKS` | exit clicked N times with `inRaid` still true |
| `HOLD_TIMEOUT_S` | main never showed; webhook a "giving up" note, then reroll |
| raids plugin disabled | immediate stop, with the fix in the message |

The safety-stop reason must **not** repeat the engine's own ". Automation paused — fix
the issue and restart." suffix — `trigger_safety_stop` appends it (mahogany lesson).

### 7. GUI wiring (required, not polish)

Per CLAUDE.md, a routine file alone is only launchable from the command line. Both:

1. `RoutineChoice.java` — add `RAID_SCOUTER`.
2. `controller.py` — add `"RAID_SCOUTER": "routines/raid_scouter.py"` to `ROUTINE_FILES`.

No `ControllerConfig.java` section: this routine uses `DEFAULT_CONFIG`/`get_config` and
does not read the bridge's `GET /config`, same pattern as `gemstone_crab.py`.

## Live-verify items — record a human first

The entrance and exit interactions are **not** specified here on purpose. Every
guessed click in this codebase has cost a live session; `falconry.py` was written
entirely from a recording and is the reference for how that pays off.

**Before writing the routine, record 3-4 full reroll cycles:**

```
python -u recorder.py cox-scout-reroll
```

Read out of the narrative log, not guessed:

- the entrance object's id, name and action (and whether it is on the party board or
  the chambers entrance itself)
- how a human leaves an unstarted raid from the start room — object or interface, and
  its exact action
- how a human leaves an **already started** raid (may differ from the above)
- whether re-entry after a rejected layout needs any party-board interaction at all
- the tick gap between the click and varbit 5432 flipping, to size the timeouts
- whether `RaidScouted` lands before or after the player is controllable

Every world-object click site gets a reachability gate (`travel.can_reach`) — the
invariant from mahogany homes is that **the game accepts a click on anything you can
see and then silently does nothing**, and fixing one site just moves the failure to the
next ungated one.

## Testing

**Java — `RaidsProviderTest`.** Hermetic. Build a `Raid` with a stubbed `Layout`,
assert the ordered room list, the `COMBAT|PUZZLE` filter, `unknownRooms` counting, and
that `scoutId` increments per event. Assert the JSON key set is stable, the way
`M8aqProviderTest.knownPathsAreStable` does — these keys become a Python-facing
contract.

**Python — `tests/test_raid_scouter.py`.** Hermetic, with a `_no_live_bridge` autouse
fixture; `get_raid` must never reach the running client, or results depend on where the
character happens to be standing (the travel.py lesson from 2026-08-29).

Cases, at minimum:

- the five rows of the verdict table above
- an UNKNOWN room abstains, and specifically does **not** reject
- a stale `scoutId` is refused after re-entry
- `hold` exits on `raidStarted` under the default `EXIT_ON`
- `HOLD_TIMEOUT_S` rerolls rather than holding forever
- a webhook raising every exception type still leaves the loop running

## Explicitly out of scope

- Doing any part of the raid. The bot never starts, never fights, never loots.
- Challenge mode — its layout is static, so there is nothing to scout.
- Large/full raids — incompatible with an exactly-four-room target.
- Choosing or joining the party, setting raid size, or managing the CC. Manual setup.
- World hopping to scout faster.
- Any rotation preference beyond the single exact target; there is no scoring or
  "close enough" tier.

## Open questions for the first live session

1. **Who leads the party.** The design assumes the bot is a plain member and the main
   leads, so the bot leaving never transfers leadership or ejects anyone. If the bot
   ends up leading, the wiki's leadership-transfer rule applies and the main must be
   ranked recruit+ in the bot's CC — worth confirming before the first unattended run.
2. **Raid scaling.** Under the default `EXIT_ON = "raid_started"` the bot is inside the
   instance at the moment the raid starts, so **the raid scales for two players**.
   Scaling is calculated at start and leaving afterwards does not undo it. If that is
   unwanted, switch to `EXIT_ON = "main_arrived"`, which costs the ability to detect
   the start from inside. Flagged rather than decided — it is a gameplay call.
3. **Whether leaving before the start dissolves the instance.** If the bot is the only
   one inside an unstarted raid and leaves, the instance may be destroyed. Harmless
   during `reroll` (that is the intent) but it would break `EXIT_ON = "main_arrived"`
   if the main has not entered yet. Verify live before enabling that mode.
4. **`IDLE_NUDGE_S`, `SCOUT_TIMEOUT_S`, `HOLD_TIMEOUT_S`** are all first-draft guesses,
   in the same category as mahogany homes' click cooldowns — expect to correct them
   after the first live session, from the recording rather than from reasoning.
