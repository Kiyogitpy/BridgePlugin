# Raid scouter (Chambers of Xeric) — design

Status: implemented 2026-08-30, **not yet run live**.
Author: brainstormed with the user 2026-08-29.

## Implementation status (2026-08-30)

`RaidsProvider.java` (`GET /raid`), `controller.get_raid()`, `RoutineChoice.RAID_SCOUTER`, and
`routines/raid_scouter.py` all exist and are wired into the GUI dropdown. `setup` → `scout` →
`reroll` → `hold` → `depart` are all implemented exactly per the MEASURED section below.
`RaidsProviderTest.java` (Java, 6 tests) and `tests/test_raid_scouter.py` (Python, 27 tests)
both pass; the full existing suite (848 Python tests, full Gradle test task) was re-run clean
after this change. Nothing here has touched a live client yet.

**`hop_phase` is deliberately a safety-stop, not a click sequence.** No recording of a human
hopping worlds exists (see the "World hopping" section below, unchanged since design), and
nothing in this codebase drives a world change today. Guessing pixel or widget positions for an
interface nobody has recorded is exactly what CLAUDE.md's live-debugging lessons warn against.
The routine therefore completes one full, correct cycle — scout, match, alert, wait for the
main, leave — and stops cleanly with an actionable message instead of clicking blind. Record a
human hopping worlds, then extend `hop_phase` the same way every other phase was built here.

## Goal

Reroll Chambers of Xeric instances until one matches a target rotation, hand that
instance to the user's main account, then start over on a different world.

The loop, as stated by the user 2026-08-29:

> create a party, enter raid, refresh until we find the configuration, then send
> discord, wait until main is inside. we leave with same stairs we reroll with. swap
> world, create a new party and go in again

1. `setup` — make a party, enter the chambers
2. `scout` / `reroll` — `Reload` the Steps until the layout matches
3. `handoff` — POST to Discord, then **wait until the main is inside the raid**
4. `depart` — leave via `Climb` on the **same Steps** used to reroll
5. `hop` — switch world, then back to 1

The bot **never starts a raid and never fights anything.** It sits in the start room,
reads the layout, and either reloads, waits, or leaves. This is the single most
important safety invariant in the whole design.

Note the handoff signal is **the main being inside**, not the raid having begun. That
is the user's explicit choice and it is the better one: the bot is gone before the raid
starts, so **the raid scales for the main alone**, not for two players.

### The target rotation

Approved by the user 2026-08-29 as the **exact** rule, not a preference ordering:

> The raid's ordered combat+puzzle room list must be **exactly five rooms**: `Vasa`
> first, followed by `Shamans`, `Vespula`, `Crabs` and `Tightrope` in any order. Any
> extra combat or puzzle room disqualifies the layout.

That is the **smallest layout the game can generate** — three combat rooms and two
puzzle rooms. Corrected by the user 2026-08-29 after an earlier draft of this spec
specified a four-room target, which **no instance can ever satisfy**; the routine would
have rerolled forever. The wiki's `Chambers of Xeric (Solo) Speed-Runner` page agrees:
*"3 combats and 2 puzzles is the smallest possible layout, Tightrope and crabs are the
fastest puzzle rooms allowing 3 combat rooms for the other supplies."*

Composition of the target, by room type (`RaidRoom.java`):

| room | type |
|---|---|
| Vasa, Shamans, Vespula | COMBAT (3) |
| Crabs, Tightrope | PUZZLE (2) |

| ordered rooms | verdict |
|---|---|
| Vasa, Crabs, Shamans, Tightrope, Vespula | MATCH |
| Vasa, Vespula, Tightrope, Shamans, Crabs | MATCH |
| Vasa, Shamans, Vespula, Crabs, Tightrope, Tekton | no — six rooms |
| Crabs, Vasa, Shamans, Vespula, Tightrope | no — Vasa is not first |
| Vasa, Shamans, Vespula, Tightrope | no — Crabs absent |
| Vasa, Shamans, Muttadiles, Crabs, Tightrope | no — Muttadiles for Vespula |

**"Vasa first" means index 0 of the ordered list**, not merely the first *combat* room —
so a puzzle room appearing before Vasa disqualifies the layout (row 4 above). This is
the stricter of the two readings and matches what the user picked. If the looser "first
combat room" variant is ever wanted, it is a one-line change to the predicate, not a
redesign.

The rule is stated over the **combined** ordered list — the same list RuneLite's own
`Raid.toRoomString()` prints. Scavengers, farming, resource and start/end rooms are
**not** part of the list and never affect the verdict.

**Precondition: the party must be set to SMALL raids.** A five-room layout is only
reachable there; large raids fill every floor with eight rooms — four combat/skilling
rooms per floor, and duplicate puzzle rooms become possible — so the target rotation is
unobtainable. The routine cannot set this itself; it is a manual setup step, and the
routine should log loudly if every instance it scouts exceeds five rooms.

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
about directly (`RAIDS_VASA`, `RAIDS_SHAMANS`, `RAIDS_VESPULA`, `RAIDS_CRABS` and
`RAIDS_TIGHTROPE` are all explicit switch cases — every one of the five target rooms is
named, so none of them depends on the rotation solver to be identified). But `buildRaid` skips any room whose tile falls outside the
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
  "rooms": ["Vasa", "Crabs", "Shamans", "Tightrope", "Vespula"],
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
TARGET_FIRST = "Vasa"                                        # must be ordered[0]
TARGET_REST  = frozenset({"Shamans", "Vespula", "Crabs", "Tightrope"})
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
| `setup` | recruiting board → make party → close → enter chambers | `inRaid` true **and** `scoutId > last judged` |
| `scout` | poll `/raid`, run `evaluate` | `match` → `hold`; `reject` → `reroll`; `abstain` → keep polling |
| `reroll` | `Reload` the Steps, answer the dialogue with `1` | `raidPartyId` goes -1 → a **new** id **and** `scoutId > last judged` |
| `hold` | POST the webhook once, anti-idle, watch for the main | main inside → `depart`; timeout → `reroll` |
| `depart` | `Climb` the **same** Steps | `inRaid` false |
| `hop` | switch world | logged in on a new world → `setup` |

`setup` runs once per **world**, not once per reroll. `reroll` never leaves the
chambers, so there is no walk-back-to-the-entrance phase.

**The main being inside is detected from `/players`** — the configured display name
appearing in the raid. Only the `name` field is used: the start room is an instance and
`PvpProvider` still reads `player.getWorldLocation()`, so it is not among the four
providers fixed for instanced coordinates (CLAUDE.md) and **every x/y it reports in
there should be assumed wrong**. Names are unaffected.

`hold` must **not** treat the main's arrival as the end of its job. The bot leaves as
soon as the main is in; it never waits for the raid to begin, and it never presses
anything resembling a start control.

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

### 4b. World hopping — net-new, no precedent in this repo

VERIFIED 2026-08-29: nothing in `ControllerAddon/` or `com.example` hops worlds today.
This is a new subsystem, not a detail of the routine, and it is the part most likely to
need its own live-debug session.

It is deliberately **the last phase in the loop**, so a hop that fails costs a cycle and
never strands the bot mid-raid or mid-handoff. `hop` is only ever entered from `depart`,
i.e. already outside the chambers with no instance held.

Two candidate mechanisms, to be settled by a **second recording of a human hopping**
before any code is written:

- **The world switcher interface** — the in-game world list. Reachable via `/widget` by
  group/child once the recorder names them. Keeps the session alive, no login flow.
- **Logout → world select → login** — reuses `login.py`'s existing `attempt_login` and
  `LoginProvider`'s already-derived login-screen geometry (the fixed-pixel layout rule
  and `loginIndex` values are already solved there). Slower, but built on parts that are
  proven live.

I recommend the world switcher and expect the logout path to be the fallback, but this
should not be guessed — record it, the same way the reroll loop was.

Whichever is used, the phase confirms on **`world` actually changing** on `GET /raid`
(the field added for the webhook does double duty here), never on a fixed sleep, and
`HOP_MAX_ATTEMPTS` bounds it. A world list is also a place where a misclick can start a
different activity entirely, so the confirmation matters more than usual.

The routine keeps a short list of recently used worlds and avoids immediately returning
to one, so a failed hop cannot ping-pong between two worlds forever.

### 5. Alerting: Discord webhook

Chosen by the user 2026-08-29 over terminal/desktop/in-game options. On a match, POST
once:

> `MATCH  world 420  party 8812` / `Vasa, Crabs, Shamans, Tightrope, Vespula` / `holding — waiting for main`

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
| `ENTER_MAX_CLICKS` | chambers entrance clicked N times with `inRaid` still false |
| `SCOUT_TIMEOUT_S` | inside the chambers, no fresh `scoutId` within N seconds |
| `RELOAD_MAX_ATTEMPTS` | `Reload` clicked N times with `raidPartyId` never taking a new value |
| `DIALOG_TIMEOUT_S` | `Reload` clicked, no widget 219 option within N seconds |
| `DEPART_MAX_CLICKS` | `Climb` clicked N times with `inRaid` still true |
| `HOP_MAX_ATTEMPTS` | hop attempted N times with `world` unchanged |
| `HOLD_TIMEOUT_S` | main never showed; webhook a "giving up" note, then reroll |
| raids plugin disabled | immediate stop, with the fix in the message |

Sizing note: the measured reroll cycle is **4.4–6.5s** end to end, so any timeout here
wants to be several times that, not tight against it. `RELOAD_MAX_ATTEMPTS` confirms on
`raidPartyId` rather than on the click being taken — the mahogany-homes lesson that a
registered click and a completed action are different things.

The safety-stop reason must **not** repeat the engine's own ". Automation paused — fix
the issue and restart." suffix — `trigger_safety_stop` appends it (mahogany lesson).

### 7. GUI wiring (required, not polish)

Per CLAUDE.md, a routine file alone is only launchable from the command line. Both:

1. `RoutineChoice.java` — add `RAID_SCOUTER`.
2. `controller.py` — add `"RAID_SCOUTER": "routines/raid_scouter.py"` to `ROUTINE_FILES`.

No `ControllerConfig.java` section: this routine uses `DEFAULT_CONFIG`/`get_config` and
does not read the bridge's `GET /config`, same pattern as `gemstone_crab.py`.

## MEASURED from a human recording

`recordings/2026-08-29_215134_raid-scouter1` — 8m 3s, 107 menu actions, **37 reloads**.
Everything in this section is read out of the narrative log. It replaced a first draft
that guessed the loop was enter → leave → re-enter; **it is not**.

### Setup, once per world (region 4919, Mount Quidamortem)

| step | how |
|---|---|
| Read Recruiting board | object **29776** @ (1246,3562) p0, first option |
| Make party | widget **499.59**, `CC_OP` → varp 1427 becomes a party id, varbit 5428 = 1 |
| Close the party interface | widget **507.2 slot 11** |
| Enter Chambers of Xeric | object **29777** @ (1232,3573) p0, first option |

The entry click was logged `(nothing observed -- click may have been ignored or out of
range)` and the region changed **5.6s later**: it was clicked from 14 tiles away and the
player walked there first. **Budget the walk** — the same omission that looped the
mahogany-homes door and hotspot clicks.

### The SAME confirmation widget is reused for the exit, with different text

The second full setup cycle late in the recording (the human demonstrating what a world
hop's return trip looks like) includes a real `Climb` → `Continue` on the Steps. The
confirmation is **widget 219.1 slot 1 again** — same group, same slot — but the text
reads **"Leave."**, not "Yes, reload the raid." Since the routine's confirm step
presses the `1` key rather than matching on text (see below), this needs no branching
at all: the same `_confirm_dialog_if_open` handles both the reroll confirm and the
depart confirm identically. Also confirms the earlier `GAME_OBJECT_FIRST_OPTION` =
`Climb` note directly: the exit click landed varbit 5432 at 0 and it **stayed there**
(NPCs from inside the raid despawn, the outside NPCs — Captain Rimor, Stuliette —
appear) through LOADING → LOGGED_IN, unlike a reload's dip-and-return.

### The reroll cycle, performed 37 times

1. `Reload` on object **Steps id 49999** — **`GAME_OBJECT_SECOND_OPTION`**. The *first*
   option is `Climb`, which is how you leave, so taking index 0 here would walk the bot
   out of the raid instead of rerolling it.
2. A confirmation dialogue opens: widget **219.1 slot 1**, text **"Yes, reload the raid."**
   Group 219 is `DIALOG_OPTION_GROUP` — scope any text match to that group, never to the
   whole widget tree (the chatbox is group 162 and keeps history forever).
3. The human answers by **pressing the `1` key** — 38 presses against 37 reloads, and
   only 17 `Continue` widget clicks. Prefer the keypress: no aiming, no hijack, immune
   to where the dialogue renders.
4. ~2s: LOADING → LOGGED_IN.
5. ~2s: **varp 1427 takes a new value** — the new instance is ready.

**Measured cycle time, click to click: 5.9s / 6.5s / 4.4s / 4.5s.**

Two facts that shape the routine:

- **No walking between rerolls.** The player respawns ~3 tiles from the Steps every
  time; all four consecutive reloads above were clicked with zero intervening movement.
  Only the initial entry needs a walk.
- **The Steps move every reload.** The instance alternates between regions 13137 and
  13393 and the Steps alternate with it — (3298,5185) and (3278,5184). **Re-resolve from
  `/objects` every cycle; never cache the tile.**

### Signals, refined by measurement

| signal | measured behaviour |
|---|---|
| varp **1427** | the party/instance id. Goes to **-1** on confirm, then to a **new value** ~2s after the load. The definitive new-instance marker. |
| varbit **5432** | 1 inside the chambers. **Dips to 0 and back to 1 inside a single batch during a reload** — never read a 0 here as "we left the raid". |
| varbit **5424**, **9540** | 0 → 2 once the instance settles; 1 while a reload is pending. |
| varbit **12285** | 109 once settled, 0 during the swap. |
| varbit **5425** | 5 during the swap, 0 once settled. |
| varbit **12393** | 1 on the confirm, 0 when the load finishes. **Not reload-specific** — also set when first entering the chambers, and it is the tablet-teleport varbit from mahogany homes. Do not key anything on it alone. |

`Shift` was pressed 52 times — the human shift-clicks to walk, exactly as CLAUDE.md
requires of us.

### Not covered by this recording

The human never started a raid and was never joined, so these remain unverified:

- **No chat line was seen in the entire 8 minutes.** `"The raid has begun!"` is
  unconfirmed by recording. It is no longer on the critical path (the handoff is the
  main's arrival) but `raidStarted` is still reported by the provider, so treat it as
  unproven until seen live.
- **`Climb` as the exit.** The action exists on the same Steps object (3 uses, and the
  human went straight to the Score board and Recruiting board afterwards, so it clearly
  exits) — but never with another player inside, which is exactly the case that matters.
- **Whether the instance survives the bot leaving while the main is inside.** The single
  most important unverified assumption in this design. See the open questions.
- **World hopping.** Not in the recording at all, and **no world-hop support exists
  anywhere in this repo today** — verified 2026-08-29 across `ControllerAddon/*.py`,
  `routines/*.py` and `src/main/java/com/example/`. This is net-new and is the riskiest
  part of the implementation, not a detail.

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

- every row of the verdict table above, including the "Vasa is not first" row that
  pins index-0 semantics rather than first-combat-room semantics
- a five-room layout is the smallest the game generates, so a four-room list is a bug
  in the provider, not a rejectable layout — assert it never appears as `reject`
- an UNKNOWN room abstains, and specifically does **not** reject
- a stale `scoutId` is refused after re-entry
- `hold` exits to `depart` when the configured main's name appears in `/players`, and
  does **not** exit on any other player's name
- `reroll` treats varbit 5432 dipping to 0 mid-reload as normal, **not** as having left
  the raid (measured: it dips and returns inside one batch)
- the `Reload` click uses the **second** menu option; a test pins this, since taking
  index 0 would `Climb` out of the raid instead
- `HOLD_TIMEOUT_S` rerolls rather than holding forever
- a webhook raising every exception type still leaves the loop running

## Explicitly out of scope

- Doing any part of the raid. The bot never starts, never fights, never loots.
- Challenge mode — its layout is static, so there is nothing to scout.
- Large/full raids — incompatible with an exactly-five-room target.
- Setting raid size or managing CC membership/ranks. Manual setup. (Creating the party
  each cycle *is* in scope — it is step 1 of the loop.)
- Any rotation preference beyond the single exact target; there is no scoring or
  "close enough" tier.

## Open questions for the first live session

1. **Does the instance survive the bot leaving while the main is inside?** The whole
   handoff rests on yes. MEASURED that the bot is the party **leader** — the recording
   shows `Make party`, and reloading sets varp 1427 to -1 and then to a new id, which is
   the leader's party being torn down and rebuilt. The wiki's rule is that when the
   leader leaves, leadership passes to the highest-ranked CC member (recruit+), and if
   there is none **everyone is ejected from the instance**. So the main almost certainly
   has to be **ranked recruit or above** in the bot's clan chat, or `depart` will throw
   the main out of the raid it was just handed. **Confirm this with one manual trial
   before any unattended run** — the failure mode is destroying the good instance at the
   exact moment it becomes valuable.
2. **Does the main need to be in the party before the good layout is found?** If joining
   is only possible before the bot's last `Reload`, the main cannot join on demand and
   the handoff needs rethinking. The recording has no second player, so this is untested.
3. **World hop mechanism.** Switcher interface vs logout/login — record a human hopping
   and pick. See §4b; nothing in the repo does this today.
4. **`IDLE_NUDGE_S`, `SCOUT_TIMEOUT_S`, `HOLD_TIMEOUT_S`, `HOP_MAX_ATTEMPTS`** are
   first-draft guesses, in the same category as mahogany homes' click cooldowns. The
   reroll-side numbers are **not** guesses any more — 4.4–6.5s measured.
5. **Anti-idle during `hold` is now the longest wait in the loop** and the only one
   bounded by a human rather than the game. It is also the one phase where the bot is
   holding something valuable, so a logout there is the most expensive possible failure.
