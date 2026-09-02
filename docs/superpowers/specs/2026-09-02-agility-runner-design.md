# Agility course runner (Python) — design

2026-09-02. Status: approved in chat (user: "yes" to the sectioned design). Builds
`ControllerAddon/agility_courses.py` (pure course data + predicates) and
`ControllerAddon/routines/agility.py` (one generic lap engine) for the RuneLite bridge.
The Wilderness Agility Course is the only course shipped in v1; adding a rooftop later is
a data entry in `agility_courses.py`, not a new routine.

Lineage: `docs/superpowers/specs/2026-08-30-generic-agility-runner-design.md` designed the
same CourseDef-plus-engine split for the TypeScript `rs2_scripts` framework (a different
game server, never run live). This spec ports the IDEA to the Python controller; none of
the TS geometry carries over — every tile below is from a human recording of the real
course or a live bridge read, both taken 2026-09-02.

Primary sources, in order of authority:

1. **`recordings/2026-09-02_032621_wilderness-agility.{log,summary,jsonl}`** — one full
   human lap (51s, 14 menu actions, +571 Agility XP). Every click tile, action name,
   object id, chat line, varbit and per-step duration below comes from it.
2. **Live bridge reads 2026-09-02** while the user stood on the course and then in the
   dungeon: `GET /objects?interactable=true`, `GET /player`, `GET /api/skills/skills`,
   `GET /npcs`.
3. OSRS Wiki "Wilderness Agility Course" (rev 15283980), "Ropeswing (Wilderness Agility
   Course)" (rev 15219008), "Log balance (Wilderness Agility Course)" (rev 15232857),
   "Wilderness Agility Course Dungeon" (rev 14856301) — mechanics prose only.

## Decisions taken with the user (2026-09-02)

- **Dispenser: Tag only.** One `Tag` per lap for the free Wilderness agility ticket and the
  run-energy refill. The routine never pays the 150k fee and never Redeems. The user has
  ALREADY paid the fee on this account (recording: "reward streak is: 3", loot dispensed
  into the looting bag on every Tag) — that is the user's business; the routine's Tag
  click is identical either way.
- **HP: eat from inventory, no bank trips.** Below a configurable HP threshold, eat the
  best food in the inventory between obstacles (never mid-obstacle). Out of food and still
  under the threshold: safety-stop. Skeletons (level 18 on the course, ids 13495-13501;
  level 25 in the dungeon) are ignored.
- **Tickets: hoard, never redeem.** They stack in one slot and survive death.
- **Scope: laps + dungeon recovery only.** The routine starts with the character already
  inside the course (like GOTR). No Door entry, no bank travel. Outside the course zone and
  not in the dungeon: safety-stop "outside the course".
- **Wilderness flee OFF for this routine** (`pvpSafety.fleeEnabled: false`): the course is
  crowded, and the engine's flee check would stop the run on every passer-by.
- **Non-tick-gated signals wherever they exist** (user directive: "use non tick gated
  information so we flow much faster"). See "Signals".
- **Generic engine + data module** (option 1 of three): course data in
  `agility_courses.py`, engine in `routines/agility.py`. Not a wilderness-only file, not a
  literal three-file port of the TS layout.

## Game facts the design rests on (MEASURED, not assumed)

### Course geometry (live `/objects`, player at (2996,3945))

| Object | id | tile reported by `/objects` | action | notes |
|---|---|---|---|---|
| Obstacle pipe | 23137 | (3004,3939) | Squeeze-through | second pipe end reported at (3004,3949); 49 Agility; cannot fail |
| Ropeswing | 23132 | (3005,3955) | Swing-on | recorder resolved the clicked tile as (3005,3952) — multi-tile object, match by id + radius, never by exact tile |
| Stepping stone | 23556 | (3001,3960) | Cross | fail = same-scene knockback on the third stone, ⌊hp×0.2⌋+1 damage |
| Log balance | 23542 | (3001,3945) | Walk-across | walked from (2996,3960) south to (2996,3945); reported tile is the hull centre, match by id + radius |
| Rocks | 23640 | (2993..2995,3936) | Climb | three tiles share the id; lap bonus 498.9 XP only when every obstacle was completed, else 0 XP |
| Agility dispenser | 53224 | (3005,3936) | Tag / Pay / Redeem | Tag from (2995,3933): the game walks there (~2.6-2.8s to the ticket) |
| Ladder (course) | 14758 | (3005,3963) | Climb-down | north-east corner, between ropeswing and stones |
| Ladder (dungeon) | 17385 | (3005,10363) | Climb-up | player stood at (3005,10362), region 11937 — exactly y+6400 under the course ladder |
| Gate | 23552/23554 | (2998,3931)/(2997,3931) | Open | south boundary of the lap zone |
| Door | 23555 | (2998,3917) | Open | course entrance; OUT OF SCOPE v1 |

Course lap zone: plane 0, x 2988-3010, y 3932-3966. Dungeon zone: plane 0, y ≥ 6400
(x 2990-3010, y 10344-10370). The two are separated by the 6400 map-y offset the game
uses for underground areas; `y >= 6400` is the dungeon discriminator.

### The human lap (recording), per step

| step | clicked from | ends at | XP | click → XP / done | fail mode |
|---|---|---|---|---|---|
| pipe | (3004,3936) | (3004,3949) | 12.5 | 6.4s (XP lands on EXIT, after the human had already clicked the swing) | none |
| ropeswing | (3004,3950) | (3005,3958) | 20 | 3.7s, chat "You skillfully swing across." | pit (dungeon) |
| stepping stone | (3005,3958) | (2996,3960) | 20 | 9.0s, anim 741 ×6, chat "...You safely cross to the other side." | knockback |
| log balance | (2996,3960) | (2996,3945) | 20 | 10.0s, chat "You skillfully edge across the gap." | pit (dungeon) |
| rocks | (2996,3945) / (2994,3945) | (2995,3933) | 498.9 or 0 | 5.6s, chat "You reach the top." + lap count line | none |
| dispenser Tag | (2995,3933) | (3004,3935) | — | 2.6-2.8s to chat "You received a Wilderness agility ticket..." and inv +1 ticket | — |

Cadence: the human clicks the NEXT obstacle 0.2s after the previous one's XP drop (log
click 0.2s after the stones' XP; rocks click BEFORE the log's XP had even landed, on the
"edge across the gap" chat). Rocks and Tag were clicked in bursts of 2-3. The game queues
an interaction clicked mid-walk and performs it on arrival — exactly the click-while-moving
pattern CLAUDE.md prefers.

### Varbits

- **12393 = the game's `busy` flag** (cache `config/varbit` 12393, name `busy`,
  basevar `date_vars` bit 16). Recorded 1 while an obstacle animates, 0 when it ends —
  10 flips in one lap, one pair per obstacle, including the pipe (1 at +1.6s after the
  click, 0 on exit). Read via `GET /varbit?id=12393` (`controller.get_varbit`), fresh per
  request.
- 12391 / 12392 change every tick (noise; hidden by the recorder). 10675/10676/10688
  flip on the Tag (reward-streak bookkeeping). Not used.

### Walkability (offline `worldmap`, verified 2026-09-02)

- The dungeon is covered: 91 tiles reachable from the ladder-adjacent tile
  (x 2992-3006, y 10348-10364). A route from (2998,10350) to the ladder is 20 steps.
- From the course-ladder exit (3005,3962) the map routes 16 steps to the ropeswing start
  (3004,3950) and 16 to the log start (2996,3960) — i.e. the map says the ropeswing
  crevice and the stones can both be WALKED AROUND. Pipe start, ropeswing start, stones
  start and log start share one 285-tile component (x 2990-3009, y 3936-3964).
- The log's west landing (2996,3945) and the rocks' south side (2995,3933) are NOT in that
  component: the pocket the log lands in is reachable only over the log, and the rocks are
  the only way out of it. A recovery walk must never aim there from the north.

## Signals (all un-gated unless stated)

| question | source | gated? |
|---|---|---|
| Agility XP | `GET /api/skills/skills` → `skills.AGILITY.experience` (m8aq reader, fresh per request) | no |
| XP just landed (accelerator only) | UDP `XP_GAINED` with `skill == "Agility"` via `ctx.events` | lossy, never truth |
| obstacle in progress / finished | `GET /varbit?id=12393` | no |
| player tile, hp, plane | `GET /player` | no |
| in dungeon | `/player` y ≥ 6400 | no |
| obstacle aim | `controller.object_refresh_target(x, y, plane, id, radius)` over `/object/at` | no |
| ticket count | `GET /inventory` (count of "Wilderness agility ticket") | no |
| locate an obstacle once per step | `GET /objects?interactable=true` | yes (TickCache) — the ONLY gated read |

**Completion of a step** = EITHER the Agility XP rose by at least `step.xp - 1` over the
pre-click baseline (fractional XP means 12.5 can show as +12), OR busy has been seen at 1
and is now 0 while the player stands in the step's END box. The position rule is what
confirms Rocks on a lap whose bonus was forfeited (0 XP) and the pipe before its exit XP
lands. Both are polled every ~100ms; the XP event, when it arrives, just ends the poll
early.

**Late-XP guard.** A step confirmed by position may still have its XP in flight (the pipe:
XP lands ~1s after exit). That delta must not satisfy the NEXT step's XP check (swing and
stones are both 20). The engine carries `pending_xp` = the XP of any step confirmed by
position whose XP has not been seen; the next step's success threshold is
`baseline + pending_xp + step.xp - 1`. If the pending XP never arrives, the position rule
still completes the step.

**Failure of a step** = busy seen at 1 then 0 with NO qualifying XP and the player NOT in
the end box. Sub-cases:
- player y ≥ 6400 → fell to the dungeon (ropeswing / log) → dungeon recovery, then retry
  the SAME step.
- otherwise (stones knockback) → retry the same step from where the player stands.
- A fail never advances the step; skipping is never done (it forfeits the 499 bonus and
  means something structural is wrong — the ceiling safety-stops instead).

**Timeout** = neither completion nor failure inside the step budget
(`2 × measured duration + walk allowance`, see constants). If busy was NEVER seen, the
click did not register: re-click (counts an attempt). If busy WAS seen, treat as a
failure (retry).

## Course data model (`agility_courses.py`, zero controller imports)

```python
@dataclass(frozen=True)
class Box: x1: int; y1: int; x2: int; y2: int; plane: int = 0

@dataclass(frozen=True)
class Step:
    key: str            # "pipe", "ropeswing", ...
    name: str           # object name, lowercase, matched case-insensitively
    object_id: int      # exact id; the name is a diagnostic cross-check
    action: str         # menu option, also the hover expected_action
    start: Box          # where a player clicks this step from (resync + recovery walk goal)
    start_tile: tuple   # the recorded click tile — the recovery walk's exact goal
    end: Box            # where a SUCCESSFUL pass leaves the player
    xp: float
    fail_mode: str      # "none" | "knockback" | "pit"
    duration_s: float   # measured click→done, drives the budget
    match_radius: int   # /object/at radius for multi-tile objects (0 for exact)

@dataclass(frozen=True)
class Course:
    key: str; label: str; min_agility: int
    zone: tuple[Box, ...]
    steps: tuple[Step, ...]
    tag: Step | None                 # dispenser Tag, run after the last step
    pit: Box | None                  # dungeon zone
    pit_ladder: tuple | None         # (id, x, y, plane, action)
```

Pure predicates, all unit-tested: `in_box`, `in_zone(course, x, y, plane)`,
`in_pit(course, x, y, plane)`, `step_for_position(course, x, y, plane) -> Step | None`
(the step whose start box contains the tile; ties impossible by construction — boxes are
disjoint, tests pin it), `classify(signals) -> "success" | "fell" | "failed" | "timeout" |
"no_click"`, `xp_threshold(step, baseline, pending)`.

Wilderness boxes (from the recorded click tiles, each padded to the walkable area around
them; disjoint by test):

| step | start box | end box |
|---|---|---|
| pipe | x 3001-3007, y 3933-3938 | x 3002-3006, y 3947-3949 (exit (3004,3949)) |
| ropeswing | x 3002-3006, y 3947-3953 (covers the pipe exit, so the swing is the answer the moment the pipe is done) | x 3003-3007, y 3956-3959 |
| stepping stone | x 3003-3007, y 3956-3959 | x 2994-2999, y 3959-3962 |
| log | x 2994-2999, y 3959-3962 | x 2993-2998, y 3941-3947 |
| rocks | x 2993-2998, y 3941-3947 | x 2990-3000, y 3930-3936 |
| tag (dispenser) | x 2990-3000, y 3930-3936 | x 3001-3007, y 3933-3938 (= pipe start) |

START boxes are pairwise disjoint (pinned by a test); each step's END box equals or sits
inside the NEXT step's start box, so a successful pass lands the player already inside
the next step's box. END boxes may overlap start boxes freely -- `step_for_position`
consults START boxes only.

`step_for_position` therefore answers, for every tile a lap visits, "which step do I click
next". Restart mid-lap needs nothing from `ctx.state.data`.

## Engine (`routines/agility.py`)

Per `tick(ctx)`:

1. `player = get_player()`. If `in_pit` → `dungeon_recovery(ctx)`; return. If not
   `in_zone` → safety-stop `"outside the course"`; return.
2. `maybe_eat(ctx)`: hp ≤ `food.eatAt` → `controller.consume_first(ctx.ser, "food")`; if
   it returns False (no food) and hp ≤ `food.stopAt` → safety-stop `"out of food"`.
3. Resolve the step: `step_for_position`; if None (e.g. mid-crevice tile after a resync)
   → `travel.walk_to` the nearest step's `start_tile` (nearest by route length among
   steps in the player's walkable component; the rocks pocket is handled by the fact that
   its own start box covers it). Counted (`AGL_NO_STEP_TICKS`, ceiling → safety-stop).
4. `run_step(ctx, step)`; on success, **carry straight on** to the next step inside the
   same tick (the ~1.1s engine loop between ticks is exactly the latency the user does not
   want), up to `LAP_STEPS_PER_TICK` (= one full lap incl. Tag) before handing back so
   breaks/safety checks get their turn.

`run_step`:

```
obj  = locate(step)                 # /objects?interactable=true by id (+name cross-check),
                                    # fallback /object/at(step.start_tile.., radius)
                                    # none visible → count AGL_NO_OBJECT ticks, ceiling → stop
xp0  = agility_xp()                 # /api/skills/skills
base = xp0 + pending_xp
ctx.events.clear() if ctx.events
maybe_eat(ctx)                      # between steps only
click_entity(ser, obj, offset=(0,0), refresh_target=object_refresh_target(..., radius=step.match_radius),
             refresh_legs=3, expected_action=step.action, expected_target=obj.name)
deadline = now + budget(step, dist(player, step.start_tile))
saw_busy = False
loop every ~0.1s until deadline:
    if events: wait_for_event("XP_GAINED", match skill=="Agility", timeout 0.1)  # accelerator
    xp = agility_xp(); p = get_player(); busy = get_varbit(12393)
    if xp >= base + step.xp - 1: pending_xp = 0; return SUCCESS
    if busy == 1: saw_busy = True
    if in_pit(p): return FELL
    if saw_busy and busy == 0:
        if in_box(step.end, p): pending_xp = step.xp (if xp unchanged); return SUCCESS
        if not in_box(step.end, p): return FAILED
return NO_CLICK if not saw_busy else FAILED
```

Outcome handling: SUCCESS → `data["attempts"]=0`, next step. FAILED / NO_CLICK →
`attempts += 1`, ceiling `STEP_MAX_ATTEMPTS` (8) → safety-stop
`"<step> failed N times"`; otherwise retry (a knockback leaves the player in the start
box; if not, `walk_to(step.start_tile)` first). FELL → `data["fell_step"] = step.key`,
then `dungeon_recovery` on the next tick.

**Tag step** is an ordinary `Step` with its own confirm: SUCCESS when the ticket count in
`/inventory` rose OR the chat line "You received a Wilderness agility ticket" arrived
(UDP accelerator), budget 2× measured 2.8s + walk. Tag failures do NOT stop the run:
`TAG_MAX_ATTEMPTS` (3) then log and continue to the pipe — a lost ticket is cheap, a stalled
lap is not. Tag can be disabled by config (`tagDispenser: false`).

**Dungeon recovery** (`dungeon_recovery`): find ladder 17385 in `/objects` (fallback
`/object/at(3005,10363)`); `travel.walk_to` an adjacent tile if farther than 2; click with
`refresh_target` + `expected_action="Climb-up"`; poll `/player` until y < 6400 (budget
`LADDER_CLIMB_WAIT_S`). Then `travel.walk_to(step.start_tile)` for `data["fell_step"]` —
or, if that is unknown (restart), the ropeswing (the earlier of the two pit obstacles; a
lap that re-runs swing → stones → log is still in order). Counted:
`DUNGEON_MAX_ATTEMPTS` (4) → safety-stop.

**Eating** happens only in step 2 / before a click, never inside the wait loop: an eat
mid-obstacle cannot help and the click would be dropped. The wait loop does read hp; if
hp ≤ `food.emergencyAt` it does nothing but log — dying mid-obstacle is what
`stopOnDeath` is for.

### Constants (first draft, from the recording)

| name | value | why |
|---|---|---|
| `STEP_DURATION_S` | pipe 6.4, swing 3.7, stones 9.0, log 10.0, rocks 5.6, tag 2.8 | measured |
| budget | `2 × duration + 0.6 × tiles_to_start_tile`, cap 30s | timeouts must not interrupt a real pass (golem_crafting lesson) |
| `POLL_S` | 0.1 | all sources un-gated |
| `STEP_MAX_ATTEMPTS` | 8 | ~92% success per pit obstacle at 75 Agility; 8 consecutive fails ≈ 1e-9 |
| `TAG_MAX_ATTEMPTS` | 3 | cheap to lose |
| `DUNGEON_MAX_ATTEMPTS` | 4 | |
| `AGL_NO_OBJECT_TICKS` / `AGL_NO_STEP_TICKS` | 10 | silent loops are worse than stops |
| `LAP_STEPS_PER_TICK` | 6 | one lap incl. Tag per tick |
| `LADDER_CLIMB_WAIT_S` | 6.0 | |
| `BUSY_VARBIT` | 12393 | |
| `DUNGEON_Y_MIN` | 6400 | |

## Config

```python
DEFAULT_CONFIG = {
    "course": "wilderness",
    "tagDispenser": True,
    "food": {"enabled": True, "eatAt": 45, "stopAt": 30},
    "breaks": {...same shape as GOTR...},
    "safety": {"stopOnDeath": True, "stopOnInventoryFull": False, "maxRuntimeHours": 0},
    "pvpSafety": {"fleeEnabled": False},
}
```

`get_config` ignores `bridge_config` (no Controller Settings section needed; same pattern
as mining/pest_control). `stopOnInventoryFull` is False because the user's loadout is
28/28 by design (tickets stack).

## Wiring

- `RoutineChoice.AGILITY("Agility (course runner)")` +
  `ROUTINE_FILES["AGILITY"] = "routines/agility.py"`. Client rebuild needed for the GUI
  entry; command-line launch works immediately.
- No provider changes. Agility XP comes from the m8aq skills reader; no `agilityXp` on
  `/player` is added (would need a rebuild for no gain).

## Error handling summary

Every loop has a ceiling that safety-stops with a named reason: step attempts, tag
attempts, dungeon attempts, no-object ticks, no-step ticks, out of food, outside the
course. Nothing sleeps a fixed duration where a signal exists.

## Testing (`tests/test_agility.py`, hermetic)

- Course data: ids/actions/order pinned to the recording; start boxes pairwise disjoint;
  every recorded click tile resolves to its step via `step_for_position`; every recorded
  end tile resolves to the NEXT step; the dungeon tile (3005,10362) is `in_pit` and not
  `in_zone`.
- `classify` truth table (xp / busy edge / end box / pit → outcome), including the
  late-XP guard (pipe's 12 must not complete the swing's 20; pending 20 must not let the
  swing's late 20 complete the stones).
- Engine with a stubbed `controller` (autouse fixture, like GOTR's): success advances and
  chains steps within one tick; NO_CLICK re-clicks; knockback retries the same step; a
  fall routes to dungeon recovery then walks to the failed step's start tile; ceilings
  safety-stop; Tag failure continues the lap; eating fires only between steps.

## Live-verification checklist (first session's worklist)

1. Click-to-XP flow on a clean lap: is the next click landing within ~0.3s of XP?
2. `busy` 12393 timing relative to the click and to XP on each obstacle (the recording
   only shows it at 0.6s tick resolution).
3. Multi-tile aim: `object_refresh_target` with `match_radius` for the ropeswing (reported
   (3005,3955) vs clicked (3005,3952)) and the log ((3001,3945) vs walked at x 2996).
4. **A deliberate ropeswing fail**: where the spikes land you (must be inside the 91-tile
   ladder component), the walk to ladder 17385, and — critically — whether the map's
   walk-around from the ladder exit to the ropeswing start (16 steps) exists in the real
   game. If it does not, the recovery for the ropeswing needs a different route (e.g.
   re-enter via the pipe side) and this spec is wrong there.
5. **A deliberate log fail**: same, for the "end of dungeon" landing and the walk back to
   (2996,3960).
6. Rocks on a forfeited lap (0 XP): confirm the end-box rule completes it.
7. Tag confirm via ticket count; whether the extra unnoted blighted item ever needs a free
   slot (it does not with 28/28, by design).
8. Eating cadence and the hp thresholds against real skeleton damage.
