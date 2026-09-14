Moved out of CLAUDE.md on 2026-09-14. Nothing here is obsolete: this is the full measured record; the condensed working rules live in CLAUDE.md.

## Sub-tick data and clicking while moving

Most game state is genuinely capped at OSRS's own 0.6s server tick — HP, true tile position,
target switches, which animation is playing — no client-side trick gets around that; the
information doesn't exist any faster server-side no matter how it's read. But on-screen
*position* data is not tick-capped: a moving actor's `LocalPoint` is smoothly interpolated by
the client every render frame, and RuneLite's `ClientTick` event (fires once per frame, not once
per game tick) is the general mechanism for reading that. This codebase's only current use of it
is `NpcTrackerRegistry.onClientTick`'s animation-*frame* diffing (catches a repeating attack
animation restarting even when the animation *id* itself never changes between reps — a
`GameTick`-only check would structurally miss this, not just see it late).

On the bridge side, only 6 providers (`NPCProvider`, `ObjectProvider`, `TileProvider`,
`GroundItemProvider`, `GraphicsObjectProvider`, `BankProvider`) are gated by `TickCache` — a
deliberate perf optimization for the bulk list endpoints (`/npcs`, `/objects`, ...), not a
platform limitation, and it already has a `?fresh=true` bypass. Single-entity lookups
(`/npc/by-index`, `/npc/nearest`, `/object/at`, `/player`, `/combat`) were never cached at all —
every poll already reads the client's current interpolated position fresh, however often you
ask.

This is why routines gate clicks behind `wait_until_player_idle()` (wait for the player to fully
stop before trusting a resolved screen position) rather than clicking while moving: most click
call sites resolve a screen position once and never re-check it, not because the position data
itself is too slow to get. `move_to_human`/`click_entity` already had a `refresh_target`
parameter for this (a zero-arg callable re-resolving a target's current screen position — used
by `slayer.py`/`tormented_demon.py`/`golem_crafting.py`, each hand-writing its own lambda) but it
only re-aimed once, partway through a flight. `refresh_legs` (added 2026-08-14, purely additive —
`None`/default reproduces every existing caller's exact prior behavior bit-for-bit) generalizes
this into multiple re-aim checkpoints toward interpolated waypoints (only the final leg lands
exactly on the target; earlier legs travel a fraction of the remaining distance, so a re-aim
mid-flight can still correct the approach), specifically so a routine could eventually click
without waiting for full stillness first. Reviewed via an independent second pass that caught
and fixed two real bugs in the first draft (leg 0 was flying the *entire* distance to a stale
snapshot before ever re-aiming, and per-leg jitter was being redrawn instead of fixed, adding
real wasted motion even for a stationary target) — see `_move_to_human_tracking`'s own docstring
for the full account. Covered by unit tests in `tests/test_controller.py`, but **not yet
exercised against a live client**, and no routine has adopted it yet as of this note. See
`npc_index_refresh_target`/`ground_tile_refresh_target`/`object_refresh_target`/
`ground_item_refresh_target` (the last two added 2026-08-14 alongside `/object/at` and
`/ground-item/at`, the object/ground-item equivalents of `/npc/by-index` — see "Instanced areas
break forward world-coordinate conversion" above for why both are implemented by filtering the
already-corrected bulk `/objects`/`/ground-items` lists rather than a fresh forward lookup) for
ready-made `refresh_target` factories instead of hand-writing the lambda at each call site.

**`click_menu_action` GAINED `refresh_target` on 2026-08-27** (it forwards to the underlying
`click_entity`, so a right-click now re-aims mid-flight like any other). The warning below
describes why it was needed and still describes what happens if you DON'T pass one: Added 2026-08-23 after breaking it live. The bare-click path hides stale coordinates
because `click_entity` re-aims mid-flight from an un-cached endpoint; the menu path has no such
parameter, so it right-clicks exactly the pixel you give it. Escalating a hijacked plinth click to
`click_menu_action` using the object from the `/objects` list fetched at the top of the tick — i.e.
before the player walked — produced a menu of pure ground options (`['Cancel', 'Mark Safespot',
'Examine', 'Walk here']`): a right-click on bare tile, with the object never involved. Re-resolve
through `get_object_at`/`get_npc_by_index` immediately before calling it. Also pass `offset=(0, 0)`
for a small target: the default `(-7, +7)` nudge is by itself enough to slide off a plinth.

**This is the PREFERRED pattern for every new click site — reach for it before
`wait_until_player_idle()`.** Stated as a standing directive 2026-08-23 (user): a single-entity
accessor is not tick-limited, so a click can be aimed while the player is still walking, and that
is what closes the gap to human speed. The shape is:

1. Fire the walk **without waiting for arrival** (no blocking exact-arrival poll).
2. Issue the target click a beat later, passing a `*_refresh_target` factory **and**
   `refresh_legs` — the un-cached endpoint behind the factory re-resolves the target's live screen
   position at each leg, so the aim stays correct even though the player (and camera) moved after
   the click was decided.
3. Confirm the outcome on a pushed/un-gated signal (XP_GAINED, a varbit, a state endpoint), not
   on a fixed sleep.

**Measured against a human, 2026-08-23** (`recordings/2026-08-23_194302_golem-carving.*`, captured
via `recorder.py` — see "Recording a human session" above): a human clicks the target object
**0.6–1.0s after the walk click**, from a tile they have not reached yet, four times out of four,
and every one of those interactions resolved correctly — the game queues the interaction and
performs it on arrival. Waiting for stillness first is therefore not a correctness requirement,
it is pure latency we were adding on top of the game's own cadence.

The legacy pattern — `walk_to_exact_tile`/`wait_until_player_idle()` blocking until the player has
fully stopped, then resolving a screen position once — is *safe* but structurally slower than a
human, and its blocking walk is what makes an over-tight confirmation timeout destructive rather
than merely slow (a timeout re-walks and interrupts the action that had genuinely started; see
`routines/golem_crafting.py`'s craft_phase notes). Prefer the accessor + `refresh_legs` path for
anything new; leave existing blocking call sites alone unless their throughput is actually a
problem.

Note the endpoints for this already exist and are already un-cached — `/object/at`,
`/ground-item/at`, `/npc/by-index`, `/npc/nearest`, `/player`, `/combat` — so adopting this
needs **no Java change and no client rebuild**, only the Python call site. Check whether the
accessor you need is already there before proposing a new provider.

**Movement realism batch (added 2026-08-27, Python-only, no rebuild):**
- **Pointer-scale calibration persists across restarts** (`.hm2_calibration.json` next to
  human_move.py, gitignored, atomic write, every I/O failure swallowed). It is a property of
  the MACHINE, not the session, but used to reset to 1.0 every process start, so the first flights
  under-traveled ~26% and the residual corrections cleaned up the difference. Tests isolate it
  via an autouse fixture — never let a test write the real file.
- **Startup warns if Windows "Enhance pointer precision" is on** (`warn_if_pointer_acceleration`,
  SPI_GETMOUSE via ctypes) — acceleration makes px-per-unit speed-dependent, which the single
  scalar calibration cannot express. Warn-only, never a hard stop.
- **Session tempo**: durations now share an OU-drifting log-offset (`HM2_TEMPO_SIGMA`/
  `HM2_TEMPO_TAU_S`, see their comment for the five-recording measurement: lag-1 ~0.15, 2-min
  block means wandering 2.4x the i.i.d. expectation). Sessions start in a random mood.
- **Handedness was MEASURED AND KILLED** the same pass — signed path curvature flipped sign
  between sessions (left-bulge 39-64%). v2's zero-mean curve draw is correct; do not add a bias.
- **Click hold times** re-fitted from 589 human down→up pairs (`CLICK_HOLD_RANGE_S`): median
  85ms, log-sigma 0.266, replacing a geometry whose median sat at 70ms with a truncated tail.
- **In-wait fidgeting** (same day, after diffing 13.1 active human minutes against 164 active
  bot minutes): cadence, pause distributions and tick-phase all already matched the human —
  the ONE loud remaining signature was stillness (human cursor busy 80% of active play, never
  still >4.5s; bot busy 13%, still-period p90 11.8s). `human_delay` draws ≥2s are now split
  into measured human-length still segments (median 1.5s, capped 4s) separated by small
  out-and-back drifts. The fidget is POSITION-INVARIANT so hover-parking through a wait still
  works; it skips when a right-click menu is open (moving off one closes it), the window is
  unfocused, or an abort is set; it only ever lengthens a wait, never shortens it; and it arms
  only via `register_fidget_serial(ser)` in main() — an import without the Arduino keeps the
  old plain sleep. The 2026-08-30 caveat (main() armed `__main__`'s `_fidget_ser` while a
  routine reached the other copy's `None`, so **in-wait fidgeting had never once fired for a
  routine-side wait**) was CLOSED 2026-09-06: the serial is mirrored into
  `input_state.fidget_ser` and read through `controller._fidget_serial()`. Routines' own
  0.2-0.5s poll loops still never enter `human_delay`, so `controller.fidget_once()` (same
  guards, one fidget) is what `outcome_waits.await_long` and the routines call from an event
  wait; Phase B (a background fidgeter behind a global action lock) remains a design only.
- **The fidget's return leg lands NEAR its anchor, not on it — an exact return is itself the
  signature (fixed 2026-09-06, the first live look at fidgeting actually running).** USER,
  watching karambwan fishing: "investigate idle movement it looks a bit weird? did something
  change?" — it had, that morning: closing the caveat above turned routine-side fidgeting on
  for the first time ever. MEASURED from the live recorder run against the user's own human
  recording of the SAME activity (`recordings/2026-09-06_090950_cg-bot1` vs
  `..._083723_karamwan-fishing`), tethered out-and-back bursts / longest chain about one
  anchor / rests on one exact pixel: giants-foundry bot 09-03 **12% / 11 / 13**, GOTR bot 09-02
  **5% / 3 / 10**, human **26% / 10 / 3**, this run **33% / 19 / 11**. Burst RATE matched the
  human fine (24.6 vs 22.4 per minute) — the fit is good. What did not was `aim_radius=0` on
  the return: the cursor snapped to a mathematically identical pixel every time and tethered to
  one point, which no hand does. The return now aims at a remembered ANCHOR
  (`input_state.fidget_anchor`) with `IDLE_FIDGET_RETURN_JITTER_PX` (3) of jitter — 200
  simulated rests spread over 59 distinct pixels, mean drift 2.2px, max 7.2px. **Anchoring is
  the load-bearing half**: returning to wherever the last fidget happened to land would compound
  the jitter into a random walk off the target, and mixology/golem_crafting park the cursor on
  the next target and then click IN PLACE. The anchor is re-taken only when the cursor is more
  than `IDLE_FIDGET_ANCHOR_RESET_PX` (15) from it, i.e. something else moved it.
  **General lesson: a humanization that returns to an EXACT coordinate is a tell, however
  human its timing is.** Diagnose this class by running the same metric over a bot recording
  and a human recording of the same activity — the routine's own log cannot see it at all.
  **VERIFIED live 2026-08-27** (recordings/2026-08-27_023951_phase-a-verify, 3.8 active min of
  slayer): freeze tail dead (max 103s → 8.5s, p90 11.8s → 7.1s), settles firing correctly, no
  interference — but the residual p90 sat exactly at the then-4-9s idle-move timer, i.e. the
  TIMER was the new ceiling. `maybe_idle_mouse_move` therefore no longer runs on wall-clock at
  all: it triggers on time since the last DELIBERATE cursor movement, with the budget drawn
  from the same measured still-segment distribution.
  **CORRECTED 2026-08-30 — the sentence above was NOT true in practice, for two separate
  reasons, and both are now fixed.** (a) The marker lived in `controller.py`, which is loaded
  TWICE in a running process (see "controller.py exists twice", below), so the engine read one
  copy while every routine click bumped the other: the trigger never saw routine activity at
  all and fired continuously through active play. It now lives in `input_state.py`. (b) Even
  once shared, the drawn budget can be ~1s, which is well inside the ordinary gap between two of
  our own actions, so it still drifted mid-work — MEASURED 14 drifts per 48 clicks, 5 of them
  after under 2.5s. `IDLE_MOVE_MIN_STILL_S` (4.0) is now a floor beneath the drawn budget; the
  same measurement this feature rests on says the human is NEVER still longer than 4.5s, so a
  drift at 1.3s was out-moving them rather than imitating them. AFTER: 7 drifts, stillness
  `[4.0, 4.4, 4.4, 17.7, 4.0, 4.5, 4.5]`s, none under 4s. USER-REPORTED throughout as the cursor
  being "dragged back" mid-work; `maybe_idle_mouse_move` now LOGS every drift, because one that
  moves the cursor with no click is otherwise invisible in a log full of clicks — which is
  exactly how it hid.
  Routines can also declare themselves busy (`controller.mark_busy(seconds)`) — belt and braces,
  and NOT sufficient on its own: marking work_phase busy left `_climb` unmarked and the drifts
  simply reappeared there. Prefer the floor; reach for `mark_busy` only for a genuinely long
  pending action.
  Still not re-measured on a 30+ active-minute recording; if still-p90 stays >4-5s the remaining
  freezes are the poll-loop blind spot and Phase B is what's left.
- The fitting scripts live in the session scratchpad, not the repo; the numbers and method are
  recorded in the settings comments. Reach segmentation: movement bursts split on 150ms
  stillness, kept if ≥30px and 80-3000ms; stillness stats count only ACTIVE play (a click
  within 30s on both sides), so scheduled breaks don't contaminate them.

**Moving-target aiming hardening (added 2026-08-27, needs a client rebuild for the Java parts):**
- **`npc_index_refresh_target`'s deadband now keys on every motion source, not just the NPC's
  tile.** The 2026-08-27 tile-only hold (added to suppress hull-animation jitter) was itself a
  bug for click-while-moving: when the PLAYER walks, the NPC's tile never changes while its
  projection slides continuously, so the hold served a stale point for the whole flight —
  literally "clicks land behind". `/npc/by-index` now also reports `npcLocalX/Y`,
  `playerLocalX/Y` (per-frame interpolated LocalPoints) and `cameraYaw/Pitch/Scale`;
  `_npc_view_key` folds them into the hold key (quantized by `NPC_HOLD_LOCAL_QUANTUM`). A fully
  still scene still holds through animation jitter; any real motion re-resolves. Degrades to
  tile-only against an un-rebuilt client (fields absent → None in the key).
- **`click_entity` takes one last look before the mouse-down** (`_settle_on_target_before_click`):
  the flight's final re-aim happens at a submovement boundary, so the settle + click tail
  (~100-300ms) was blind. One corrective move fires only past `PRECLICK_MAX_DRIFT_PX` (18px —
  generous because mid-flight refreshes deliberately drop the caller offset, see move_to_human's
  v2 dispatch), so stationary targets pay nothing. Skipped during a simulated misclick on
  purpose — settling would un-miss the deliberate miss; the corrective reclick gets the full
  treatment instead.
- **Verify-then-click via `GET /hover`** (MenuStateProvider): the default left-click action +
  target under the cursor RIGHT NOW — the top (highest-index) live menu entry, no menu open
  needed. Opt-in per call site: `click_entity(..., expected_action="Attack",
  expected_target="Gargoyle")` reads it before committing; on mismatch it re-aims once via
  refresh_target, and withholds the click if the hover still disagrees — prevention instead of
  `/last-action` post-hoc detection, and the first primitive that beats a player camped on the
  pixel WITHOUT the right-click menu escalation. `hover_matches` never blocks when it cannot
  verify (endpoint missing on an old build, or a real menu open). A withheld click returns the
  falsy `CLICK_WITHHELD` sentinel, not plain `False` — every existing truthiness check keeps
  working, and a caller with an escalation path tells "pixel contested" from "aim failed" via
  `getattr(result, "withheld", False)`. `expected_action` also takes a sequence (any match
  passes) for state-dependent options the caller doesn't choose. Adopted same day: slayer.py's
  two attack clicks (`expected_action="Attack"` + the live target name) and golem_crafting's
  plinth clicks (`GOLEM_PLINTH_OPTIONS`), where a withhold folds into the existing `hijacked`
  flag — it spends the same fast-retry budget, escalates to the same menu tier, and past the
  budget falls through to the same timeout/miss counting, so every measured bound (including
  the out-of-fur recovery, which counts on `gc_side_misses` climbing) is preserved. None of the
  adoptions have run live yet.


**XP gain is another sub-tick, general-purpose signal — not specific to any one routine.** Added
2026-08-17 (user directive, generalized from `pyramid_plunder.py`'s tomb-door confirmation):
`client.getSkillExperience(Skill.X)` reflects the moment the client's own `StatChanged` event
fires — this is the exact signal the game's own XP Drop plugin uses
(`data/decompiled/.../xpdrop/XpDropPlugin.java#onStatChanged`) — and is not gated by `TickCache`
or the 0.6s server tick the way bulk `/objects`/`/npcs` scans or `roomNumber`-style state are.
Any skilling action that grants XP on success (thieving, mining, fishing, crafting, ...) can use
this as a fast, definitive "did that attempt actually succeed" check instead of polling chat
messages, waiting out a fixed delay, or re-reading tick-capped object/state fields.

`PlayerProvider` exposes each skill's cumulative XP the same way (`hitpointsXp` was the existing
precedent; `thievingXp` was added alongside this note) — add more `data.put("<skill>Xp",
client.getSkillExperience(Skill.<SKILL>))` lines there for whichever skill a routine needs, same
pattern. **Requires a RuneLite client rebuild + restart** (`./gradlew.bat run`) before a newly
added skill's XP field shows up on `GET /player` — it's compiled into the Java plugin, not
something the Python side can pick up on its own.

Python-side usage pattern (see `pyramid_plunder.py`'s `attempt_door_click`): snapshot
`controller.get_player().get("<skill>Xp")` immediately before the click, then poll for an
increase inside the same short wait loop already used for other post-click signals (position
jump, confirmation chat message, ...) — an XP increase is proof the underlying skill check
succeeded, sub-tick and often faster than any other available signal.

