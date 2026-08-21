# Tick-accurate timing for routines

**Date:** 2026-08-21
**Status:** design, not yet implemented

## Problem

Routines feel sluggish. They spend most of their time waiting, and the waits are
conservative because nothing tells them when the player is actually able to act again. Every
routine invents its own margin, and those margins are guesses.

This was measured over a live `routines/chinchompa_hunting.py` session on 2026-08-21. Four
distinct costs stack on every action:

| Cost | Measured | Cause |
| --- | --- | --- |
| Tick-phase blindness | ~300ms average per action | Python has no idea where it is in the 600ms tick |
| Poll latency | 100-300ms per wait | State changes are discovered by asking repeatedly |
| Per-call overhead | ~20ms x 17 call sites per tick | 1.3ms is HTTP; the other ~19ms is server-side scene walking |
| "Am I actionable?" margin | 1.0s settle | No such signal exists, so each routine infers one |

The last row is the expensive one, and it is worth being precise about why. Checking a box trap
runs **two** animations — the check, then the game's own re-lay — separated by ~0.66s during
which `animation` reads `-1`. That gap is indistinguishable from "finished" unless you already
know the mechanic. The routine needed a 1.0s sustained-idle margin purely to see past it. When
the user supplied the actual rule (the trap hits the floor as an item, and two ticks later the
player is free), the same cycle dropped from **10.18s to 6.16s** — a 40% cut that no amount of
inference from the existing signals would have found.

That is the real lesson: the wins came from knowing the mechanic, and the code had no way to
express it. The infrastructure below exists to make that kind of precision **expressible and
repeatable across every routine**, instead of hand-tuned per routine and re-derived each time.

### Why the existing pieces do not cover it

- `TickClock.java` already counts `GameTick`s, but it is wired only into `TickCache`
  invalidation. The tick number is never exposed, so Python cannot align to a boundary.
- `controller.wait_until_player_idle` is **position-only by design** (see its docstring): it
  returns as soon as local x/y stops changing, which is before the camera settles and long
  before an animation ends. It is not, and is not meant to be, an "action finished" signal.
- The UDP stream carries 11 event types (`TICK`, `ANIMATION_CHANGED`, `INVENTORY_CHANGED`,
  `CHAT_MESSAGE`, ...) but none of the ones routines actually block on, and none carry a tick
  number, so an event cannot be placed in time relative to anything else.

## Decisions taken

Two forks were settled before designing:

1. **Event-push with a local mirror**, not fast-pull. Java emits what routines wait on; a Python
   background thread keeps a live view so `tick()` reads memory instead of HTTP.
2. **The mirror is an accelerator; HTTP remains the source of truth.** It answers "has X happened
   yet" for timing. Anything a routine acts on destructively — which trap to click, whether a
   catch is real — is still confirmed against HTTP. UDP is lossy, and these routines run
   unattended for hours on a real account: a dropped packet must cost latency, never a wrong
   click.

## Design

### 1. Tick clock

`TickClock` gains `lastTickMillis` alongside its counter. The existing `TICK` UDP event carries a
payload `{tick, atMillis}` instead of being bare.

Python gains a `TickModel`, fed by the UDP listener thread:

- `tick` — the current game tick number
- `phase_ms()` — milliseconds since the last boundary
- `ms_until_next()` — milliseconds until the next
- `wait_for_boundary(offset_ms=0)` — sleep until the next boundary, plus an optional offset

This is the smallest piece and the highest leverage, because it converts every wall-clock sleep
in every routine into a tick-aligned one. Concretely: `chinchompa_hunting`'s two-tick rule is
currently `time.sleep(GAME_TICK_S * 2)`, which is correct in *duration* but starts at whatever
arbitrary phase the poll loop happened to notice the placement — so it lands anywhere in a 600ms
window. Aligned, it lands on the boundary every time.

`TickClock` is already constructed once in `ControllerPlugin` and shared with the cached
providers, so exposing it costs no new plumbing.

### 2. Event surface

Deliberately not a world mirror. Only the signals current code polls for, each stamped with the
tick it occurred on so events can be ordered against each other:

| Event | Payload | Replaces |
| --- | --- | --- |
| `OBJECT_SPAWNED` / `OBJECT_DESPAWNED` | `{id, x, y, plane, tick}` | polling `/objects` for a trap to appear |
| `GROUND_ITEM_SPAWNED` / `GROUND_ITEM_DESPAWNED` | `{id, x, y, plane, qty, tick}` | polling `/ground-items` for the placement transient |
| `XP_GAINED` | `{skill, xp, delta, tick}` | nothing — currently unused |
| `ANIMATION_CHANGED` (extend) | add `tick` and `from` | inferring "action finished" from sustained idle |

Two of these deserve their justification recorded.

**`XP_GAINED`** is called out in CLAUDE.md as the definitive sub-tick success signal for any
skilling action — it fires when the client's own `StatChanged` does, is not tick-capped, and
proves the underlying skill check succeeded. `PlayerProvider` exposes cumulative XP per skill for
polling, but no routine gets told about a gain. Pushing it makes "did that attempt work" instant
and universal, rather than each routine inventing a confirmation.

**`ANIMATION_CHANGED` carrying `from`** is what would have made the 0.66s false-idle gap
unambiguous without any measurement: a transition `5212 -> -1` mid-sequence is distinguishable
from a final `5208 -> -1` if you know which animation ended.

### 3. Python: `bridge_events.py`

A new module beside `controller.py`, following the same convention as `travel.py` and
`banker.py` — shared infrastructure, importable from any routine.

One background thread owns the UDP socket (`controller.make_udp_listener` already exists and
already binds it; this takes it over rather than adding a second listener — only one process can
bind the port, which has already caused a crash this session). It maintains:

- the `TickModel`
- a ring buffer of the last 256 events, pruned to the last 10 ticks on insert. It exists only to
  close the already-arrived race in `wait_for_event`, not to be a log — 10 ticks is 6 seconds,
  comfortably longer than any single wait in the codebase (the longest is
  `RETRIEVE_CONFIRM_TIMEOUT_S` at 3s) and short enough that it cannot grow without bound during
  an unattended multi-hour run.

and exposes the primitives that replace the sleep-and-poll idiom:

- `wait_for_event(type, match=..., timeout=...)` — blocks until a matching event arrives, and
  returns immediately if one already arrived this tick (the common race today)
- `wait_ticks(n)` — tick-aligned, not wall-clock
- `wait_until_actionable(timeout=...)` — composed from animation + tick, replacing each routine's
  hand-rolled settle

`on_event` in the routine contract is unchanged and keeps working; this is a parallel consumer,
not a replacement.

### 4. Migration

Purely additive, with the same guarantee `refresh_legs` was built to: absent the new calls, every
existing routine behaves bit-for-bit as it does now. No routine is required to adopt it.

`chinchompa_hunting.py` adopts first, because it is the only routine with measured baselines to
A/B against — retrieve dwell **10.18s** (original) → **7.44s** (two-tick rule) → **6.16s**
(hover-first ordering). Anything the new infrastructure does must be compared against 6.16s, not
against the original.

Generalise to other routines only after that measures well. Three timing changes were tried and
reverted this session on measurement alone; the same discipline applies here.

### 5. Error handling

- **Dropped packet.** The mirror goes stale. Because it is an accelerator only, the cost is that
  a `wait_for_event` falls through to its timeout and the routine takes the slow HTTP path it
  takes today. No incorrect action.
- **Listener dies.** All primitives degrade to their timeout, so routines get slower, not wrong.
  The failure is logged once, edge-triggered, matching `api_get`'s existing convention for bridge
  failures.
- **Clock drift.** `TickModel` is corrected by every `TICK` event, so drift is bounded by one
  tick and self-healing.
- **Client restart.** The tick counter resets; the model resets with it on the next `TICK`.

### 6. Testing

- **Python** — the mirror is pure logic over a synthetic event stream, so it unit-tests with no
  client at all, the same way the current 261 tests run. Cover: tick alignment maths, the
  already-arrived race, timeout fallback, and a dropped-packet sequence.
- **Java** — the events are thin; assert payload shape and that the tick stamp is present.
- **Live** — the chinchompa A/B against 6.16s is the real test. A change that does not beat it
  does not ship.

## Expected result

Honestly bounded:

- ~300ms per action from tick alignment
- 100-300ms per wait from push instead of poll
- some part of the 17 x 20ms of HTTP in the hot path

Realistically **0.5-1.0s off a ~6s cycle** — a solid 10-20% on top of the 40% already won, not
another 40%. The larger benefit is that a routine can say *"act on the tick after the trap hits
the floor"* directly, instead of a maintainer re-deriving that from timing traces and encoding it
as a margin, which is what produced three reverted attempts today.

## Out of scope

- Rewriting existing routines. Adoption is opt-in and measured.
- Mirroring general world state. Only the listed signals.
- Replacing `wait_until_player_idle`. It stays as-is for its actual purpose — protecting a
  resolved screen position from a moving player.
- Making the mirror authoritative. Explicitly rejected above.
