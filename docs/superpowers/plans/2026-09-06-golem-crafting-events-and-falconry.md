# Golem crafting: events + falconry fur trips -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is executed INLINE by the session that wrote it (CLAUDE.md: no subagents unless asked), so code blocks give interfaces and the gating tests rather than every implementation line.

**Goal:** Convert `routines/golem_crafting.py` to event-driven outcomes and replace its goat-pit fur supply with a falconry trip to Piscatoris, returning by slayer ring through the Wyrmscraig Cavern.

**Architecture:** A new shared `outcome_waits.py` holds the await/fidget/tick helpers both routines use. golem_crafting keeps every phase and measured constant, swapping polls for `await_outcome` on the golem varbits and `MENU_CLICKED`. A new `fur_trip` phase, dispatched on live position, reuses `falconry.py`'s travel legs and its hunting primitives (upgraded to events, shared).

**Tech Stack:** Python 3, `bridge_events.EventStream`, pytest with a scripted `controller.poll_udp` and a fake clock.

**Spec:** `docs/superpowers/specs/2026-09-06-golem-crafting-events-and-falconry-design.md`

## Global Constraints

- UDP is an accelerator, never truth: every await has an HTTP `confirm`, and `ctx.events is None` keeps polling behaviour.
- Nothing injects input except the Arduino: no packets, no client-side clicks.
- Existing golem tests keep passing; the module still exposes `_await_side_done`, `_await_golem_complete`, `_await_click_echo`, `walk_to_exact_tile`, `find_object`, `_drop_all` and every phase function by name, and its waits still run on `gc.time.time`/`gc.time.sleep`.
- Every measured constant in golem_crafting.py stays (`GOLEM_SIDE_TIMEOUT_S` 8.0, `GOLEM_SIDE_MAX_WAIT_S` 25.0, `GOLEM_WALK_TO_CLICK_GAP_S`, `GOLEM_FAST_RETRY_BUDGET` 1, `SUNSTONE_PER_TRIP` 20, `CORES_PER_TRIP` 4, ...).
- Falconry regions/tiles/ids come from `routines/falconry.py` (recorded), never retyped.
- Run `python -m py_compile` on every touched module and `python -m pytest tests/test_golem_crafting.py tests/test_mastering_mixology.py tests/test_falconry.py tests/test_outcome_waits.py -q` before claiming a task done.

---

### Task 1: `outcome_waits.py` and the mixology refactor

**Files:**
- Create: `ControllerAddon/outcome_waits.py`
- Modify: `ControllerAddon/routines/mastering_mixology.py` (the `_await`, `_await_long`, `_still_segment_s`, `_tick_pause`, `_varbit_in`, `_chat_has`, `_walk_budget`, `_mark` bodies become one-line delegations)
- Test: `ControllerAddon/tests/test_outcome_waits.py`

**Interfaces (Produces):**
```python
def events_of(ctx)                       # ctx.events or None
def mark(ctx)                            # events.mark() or None
def await_pre(ctx, signals, budget_s, confirm, *, since=None, reaction=True, shorten=None,
              now_fn=time.time, sleep_fn=time.sleep) -> bridge_events.Outcome
def await_long(ctx, signals, budget_s, confirm, *, since=None, stop=None, fidget=True,
               reaction=True, now_fn=time.time, sleep_fn=time.sleep) -> bridge_events.Outcome
def still_segment_s() -> float
def tick_pause(ctx, n=1, phase=None, *, now_fn=time.time, sleep_fn=time.sleep) -> None
def varbit_in(ids) -> callable(payload) -> bool
def chat_has(*needles) -> callable(payload) -> bool
def walk_budget(entity, s_per_tile=0.6) -> float
```

- [ ] Write `tests/test_outcome_waits.py`: `await_pre` returns at once with a reaction pad when confirm already holds; `await_long` calls `controller.fidget_once` between chunks and stops on `stop()`; `tick_pause` sleeps `n*0.6` with no events; `varbit_in`/`chat_has` predicates. Fake clock via `now_fn`/`sleep_fn`.
- [ ] Run: FAIL (module missing).
- [ ] Create `outcome_waits.py` by lifting the mixology bodies verbatim, parameterised on `now_fn`/`sleep_fn`.
- [ ] Rewrite mixology's helpers as delegations passing `now_fn=_now, sleep_fn=_sleep`.
- [ ] Run `tests/test_outcome_waits.py tests/test_mastering_mixology.py`: PASS.

### Task 2: golem awaits on varbits and MENU_CLICKED

**Files:**
- Modify: `routines/golem_crafting.py` (`_await_side_done`, `_await_golem_complete`, `_await_click_echo`, new constants)
- Test: `tests/test_golem_crafting.py` (append)

**Interfaces (Produces):**
```python
VB_STATION = {"north": 15728, "south": 15733}
VB_SIDE_STATE = {"north": {"north": 15729, "east": 15730, "south": 15731, "west": 15732},
                 "south": {"north": 15734, "east": 15735, "south": 15736, "west": 15737}}
VB_SIDE_PROGRESS = {"north": {"north": 15739, ...}, "south": {"north": 15743, ...}}
VB_CRAFTING_COUNT = 15738
VB_FALCON_HAS_CATCH = 14192
VARBIT_SUBSCRIPTIONS = (...)                      # all of the above
def _await_side_done(station_name, side, timeout_s, already_done=False, ctx=None) -> bool
def _await_golem_complete(station_name, timeout_s, before_total_crafted, before_progress, ctx=None) -> bool
def _await_click_echo(before, object_id, ctx=None)  # tri-state as today
```
`ctx=None` keeps the existing five-argument call sites and tests valid: with no ctx (or no events) the functions poll exactly as today. craft_phase passes `ctx=ctx`.

- [ ] Tests: with a scripted `EventStream`, `_await_side_done` returns True on the first `VARBIT_CHANGED` for the side's state varbit whose `/golem` confirm reads done, without sleeping through `GOLEM_CONFIRM_POLL_S`; a progress `VARBIT_CHANGED` extends the deadline like a polled progress bump; `_await_click_echo` returns True/False from a `MENU_CLICKED` whose `/last-action` confirm resolves, None at the budget; the existing polled tests (no ctx) still pass unchanged.
- [ ] Run: FAIL on the new names.
- [ ] Implement via `outcome_waits.await_pre` with `since=mark` taken before the click (craft_phase takes it) and the polled loop as the `events is None` path.
- [ ] Run the whole golem file: PASS.

### Task 3: chisel chain, bank confirm, mine pre-hover

**Files:** `routines/golem_crafting.py` (`chisel_phase`, `_run_bank_trip`, `mine_phase`), tests appended.

- [ ] Tests: `chisel_phase` chisels up to `CORES_PER_TRIP` cores in ONE call when each `INVENTORY_CHANGED` confirms a core, stops between cores when paused; `_run_bank_trip` sets `gc_fur_stock` from the withdrawn qty on `INVENTORY_CHANGED` (fake bank/inventory); `mine_phase`'s pre-hover waits on `PLAYER_STOPPED` + `/player.moving == False` (a `get_player` returning `moving: True` then False).
- [ ] Implement: `CHISEL_CHAIN_MAX_S` 12.0; confirm = core count increased; keep the `isIdle` gate for the first chisel only.
- [ ] PASS.

### Task 4: long waits fidget, stall watch, chaining

**Files:** `routines/golem_crafting.py` (`craft_phase` side/insert waits through `await_long`; `_stuck_watch`; `tick`), tests appended.

**Produces:** `STALL_WARN_S = 120.0`, `STALL_STOP_S = 300.0`, `TICK_CHAIN_MAX = 2`, `_progress_signature(ctx)`, `_stuck_watch(ctx)`.

- [ ] Tests: `_stuck_watch` stops after `STALL_STOP_S` of an unchanged signature and resets on a `/golem` progress change; `tick` runs `chisel` then `craft` in one call when chisel flips the phase, never chains out of `mine` or `craft`.
- [ ] Implement; the side wait's chunking must preserve the progress-extension rule (each chunk re-reads progress; `stop` is never used there).
- [ ] PASS.

### Task 5: falconry primitives on events (shared)

**Files:** `routines/falconry.py` (`_retrieve_falcon`, `_throw_falcon`, `_hire_falcon`, `_drop_bones`, new `_drop_meat`, `_ensure_hands_free`, `_use_teleport` confirm), `tests/test_falconry.py` (append).

**Produces:**
```python
VARBIT_SUBSCRIPTIONS = (VARBIT_FALCON_HAS_CATCH,)
def hunt_step(ctx, config, player, region, drop_meat: bool) -> str
    # one bounded action; returns "retrieved" | "dropped" | "hired" | "threw" | "missed" | "walked" | "idle"
def _ensure_hands_free(ctx) -> bool          # True when it acted this tick (weapon/shield/gloves removal)
def hands_free() -> bool
def _use_teleport(ctx, item_substring, option, left_click=False, expect_region=None) -> bool
EQUIPMENT_WEAPON_CHILD, EQUIPMENT_SHIELD_CHILD, EQUIPMENT_GLOVES_CHILD = 18, 20, 22
```
`falconry.tick`'s own `_hunt` calls `hunt_step` so the standalone routine keeps working.

- [ ] Tests: `_throw_falcon` confirms a catch on `VARBIT_CHANGED` 14192 -> 5 (fake `/varbit`), counts a miss without stopping; `_retrieve_falcon` returns once 14192 -> 0; `hunt_step` order is retrieve > drop > hire > throw; `_ensure_hands_free` opens the tab then removes shield child 20 when a shield is worn; `_use_teleport(expect_region=)` waits on `/player.regionId`.
- [ ] Implement with `outcome_waits.await_pre`; falconry gets its own `_now/_sleep`.
- [ ] PASS (`tests/test_falconry.py`).

### Task 6: the fur trip in golem_crafting; goat pit removed

**Files:** `routines/golem_crafting.py` (delete `hunt_phase`, `_ensure_wooden_spikes`, `_pit_ready_to_reline`, `_shed_pit_junk`, `is_valid_grab_target`, `goat_in_grab_range`, `_note_cast_*`, goat constants/anchors/chat; add `fur_trip_phase` and helpers; `_run_bank_trip` routes to `"fur_trip"`), `tests/test_golem_crafting.py` (delete the goat tests at 894-912, 1981-2060, 2240-2301 except 2271-2293; append fur-trip tests).

**Produces:**
```python
FUR_TRIP_DEFAULTS = {"hireCoins": 2000, "tripMaxMinutes": 25, "returnTeleport": "slayer ring", "dropMeat": True}
KIT_ITEMS = ("ardougne cloak", "dramen staff", "slayer ring")     # + coins >= hireCoins
CAVERN_MIN_Y = 8000
def trip_leg(player: dict, trip: dict) -> str
    # "kit" | "depart" | "fairy_ring" | "walk_in" | "hunt" | "return" | "cavern" | "home"
def fur_trip_phase(ctx, config) -> None
def _withdraw_kit(ctx, config) -> bool
def _hunt_done(ctx, config, trip) -> bool        # loose Dashing kebbit fur, or the time cap
def _teleport_home(ctx, config) -> bool          # slayer ring Teleport + dialogue number key / necklace
def _leave_cavern(ctx, config) -> bool
```
`trip_leg` is pure: region == falconry -> "hunt" or "return" (trip["done"]); region in monastery/fairy ring -> "fairy_ring"; region == AKQ landing or (falconry region and y >= 3615) -> "walk_in"; y >= CAVERN_MIN_Y -> "cavern"; Wyrmscraig surface region with kit not withdrawn -> "kit"; with kit and not done -> "depart"; with kit and done -> "home".

- [ ] Tests: `trip_leg` table (every branch, including a restart mid-trip with empty `trip`); `_withdraw_kit` safety-stops naming the missing item; `_hunt_done` on spill and on the time cap; `_teleport_home` presses the number key of the "Wyrmscraig" option (fake dialogue `["Select an Option", "Stronghold Slayer Cave", ..., "Wyrmscraig Cavern"]` -> key "6"); `_leave_cavern` clicks the `Cave`/`Exit` object and records `locations.cavernExit`, walks toward a known anchor when none is visible, safety-stops past the wander budget; `_run_bank_trip` with no fur sets `gc_phase == "fur_trip"`.
- [ ] Delete the goat code and tests; `python -m py_compile`; run the golem file: PASS.

### Task 7: docs and full verification

- [ ] CLAUDE.md: rewrite the golem_crafting "Current state" header paragraph (fur source, phases), add the fur-trip live worklist, note the removed goat machinery (keep the measured goat facts as history in one line), note `outcome_waits.py`.
- [ ] Memory: update the golem note.
- [ ] `python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_chinchompa_hunting.py` (the chinchompa suite has real sleeps; run it separately in the background).
