# Carnivorous chinchompa hunting routine — design

Date: 2026-08-20
Status: approved by the user in advance ("full agency"), implemented in the same session.

## Goal

A new `routines/chinchompa_hunting.py` that box-traps **Carnivorous chinchompas** with **4
traps**, unattended, and **picks its traps back up before the engine's break starts** (explicit
user requirement) so a 3-12 minute break doesn't leave four traps rotting in the dirt for
someone else to walk off with.

## Researched facts (all from the local mirrors in `data/`, not recalled)

Sources: `data/raw/C/Carnivorous chinchompa.txt`, `data/raw/B/Box trap.txt`,
`data/lookups/ObjectLookup.java`, `data/lookups/NpcLookup.java`,
`data/decompiled/client-1.12.33/net/runelite/client/plugins/hunter/HunterPlugin.java`.

| Fact | Value |
|---|---|
| NPC | Carnivorous chinchompa, id **2911** (`HUNTING_CHINCHOMPA_BIG`) |
| Hunter level | **63** (265 xp/catch) |
| Trap | Box trap, item id **10008**, item options `Lay, Drop` — **stackable** since 2025-08-20 |
| Traps allowed | 1 @ lvl 1, 2 @ 20, 3 @ 40, **4 @ 60**, 5 @ 80 |
| Lure radius | 5x5 centred on the trap (2 tiles), re-rolled every 3 game ticks |
| Blocking rule | *"Box traps won't trap prey if players are standing on the trap itself"* |
| Loot | Red chinchompa — **stackable**, so the inventory never fills → **no banking needed** |

Box trap GameObject ids (from `ObjectLookup.java`, cross-checked against `HunterPlugin.java`'s
own state machine, which sorts the same ids into OPEN / FULL / EMPTY / TRANSITION):

| State | ids | meaning |
|---|---|---|
| set | `9380` | laid and waiting (`HUNTING_BOXTRAP_EMPTY`) |
| full | `9382` (grey), **`9383`** (big/carnivorous), `9384` (ferret), `721` (black) | a catch is inside |
| failed | `9385` | collapsed after an escape — must be picked up and re-laid |
| transition | `9381`, `9386`-`9397`, `2025`/`2026`/`2028`/`2029` | mid-animation, do not touch |

## Architecture

### 1. Engine: a `before_break` routine hook (`controller.py`)

`main()` calls `state.maybe_toggle_break()` **before** `routine.tick()`, and skips `tick()`
entirely for the whole break — so a routine can never clean up after a break has begun. Two
small additive changes fix that without altering any existing routine's behaviour:

- `ControllerState.break_due(breaks_cfg) -> bool` — a **side-effect-free predicate** that is
  True exactly when the next `maybe_toggle_break()` call would start a break.
- `main()` calls `routine.before_break(ctx)` (if the routine defines it) immediately before
  `maybe_toggle_break()`. Routines without the hook are bit-for-bit unaffected. The break simply
  starts however long the teardown took, which is the point.

Documented in `controller.py`'s routine-contract header and in `CLAUDE.md`.

### 2. Shared helper: `travel.walk_to_exact_tile`

Laying a trap requires standing on an **exact** tile, which `travel.walk_to`'s 3-tile arrival
tolerance cannot deliver. `golem_crafting.py` already solved this (shift+click force-walk, poll
until the player's own tile matches). That implementation moves to `travel.py` verbatim;
`golem_crafting.walk_to_exact_tile` becomes a thin delegating wrapper that keeps its own
docstring as the historical record. `test_golem_crafting.py`'s existing direct tests of it patch
`controller.*` attributes (not `gc.*` internals), so they keep passing through the delegation —
that is the verification that the move was safe, not an assumption.

### 3. The routine: tile-keyed trap tracking

**Plan tiles are the trap's identity.** `ctx.state.data` resets on restart while laid traps
persist (CLAUDE.md), so the routine never trusts remembered per-trap bookkeeping: every tick it
re-reads `/objects` and classifies whatever box-trap object currently sits on each of its own
planned tiles. This also solves trap ownership for free — a trap on any tile that is not one of
our planned tiles is somebody else's and is ignored entirely.

- **Anchor**: `config["anchor"]`, else the tile the player is standing on at the first tick
  (stored in `state.data`). The player stands here; it is never a trap tile.
- **Plan**: the first `trapCount` non-blocked tiles from `config["trapOffsets"]` (8 candidates:
  the four orthogonal neighbours first, then the four diagonals as spares), relative to the
  anchor. Default layout is the diamond N/E/S/W — every trap one step from the standing tile.
- **Adoption**: on first plan build, box-trap objects already within `adoptRadius` of the anchor
  fill plan slots first. This is the restart-desync fix: relaunching while four traps are down
  re-adopts them instead of abandoning them and laying four more we don't have.
- **Blocked tiles**: `"you can't set a trap here"` / `"this isn't your trap"` chat messages mark
  the last-touched tile blocked, which rebuilds the plan onto a spare offset.

Pure, unit-testable functions: `classify_trap(obj_id)`, `max_traps_for_level(level)`,
`plan_tiles(anchor, offsets, count, blocked)`, `build_plan(...)` (adoption + fill),
`chebyshev(a, b)`.

### 4. tick() priority order

1. One-time Hunter-level check (63) + clamp `trapCount` to what the level allows.
2. Resolve anchor and plan; scan `/objects` once.
3. **Full trap** → menu-select `Check` (collects chin **and** returns the trap to inventory).
4. **Failed trap** → menu-select `Dismantle`/`Take`/`Pick-up`.
5. **Empty plan tile** and a box trap in the inventory → `walk_to_exact_tile`, click the item to
   `Lay`, confirm object `9380` appeared on that tile.
6. Otherwise idle — and if the player is not on the anchor, walk back. This is load-bearing, not
   cosmetic: after laying the last trap the player is *standing on it*, which disables it.

Every trap-object interaction goes through `click_menu_action` with a **priority list of option
names** rather than a bare left-click. Two reasons: chinchompas wander across the trap tiles, so
a left-click can silently resolve to `Attack` on the NPC sharing that pixel (the exact case
`click_menu_action` was built for, per its docstring); and the live menu read means the routine
never has to have guessed the right option name — a mismatch is logged with the real entries
instead of silently doing nothing. Interactions are ~4/minute, so the extra right-click costs
nothing measurable.

`click_menu_action` is extended to accept `option` as a **str or a sequence of strs** tried in
priority order (existing str callers unchanged), plus a hint-free retry so a wrong
`target_hint` degrades to a looser match instead of a permanent no-op.

### 5. before_break() teardown

Bounded loop (max `trapCount * 2 + 2` attempts): rescan `/objects`, pick the nearest trap on a
plan tile, retrieve it with the option list for its state (`Check` for a full one — never throw
away a catch — `Dismantle` otherwise), stop when no plan tile holds a trap. Traps come back on
the next tick after the break because tick() reads the world rather than its own memory.

## Explicitly out of scope

- **Banking** — red chinchompas stack; the inventory never fills. Nothing to bank.
- **Travel to the hunting area** — the routine hunts wherever the player is standing when it
  starts, which also makes it work at every carnivorous-chinchompa location (Feldip, the red
  chinchompa hunting ground, Gwenith, Tlati, Chinchompa Island) with no per-location data.
- **Bait / smoking / anti-odour salts** — +3%/+2% catch rate for two extra clicks per trap; the
  wiki's own guidance is that it isn't worth it.
- **The "stand and shoot" tick-manipulation method** — far more click-intensive and needs combat
  handling; the plain trap-and-wait loop is what was asked for.

## Verification

`py_compile` on every touched file, then the full `pytest` suite (existing golem tests are the
regression gate for the `walk_to_exact_tile` move), plus new unit tests for every pure function
and for the `break_due` predicate. **Not live-tested** — that needs the RuneLite client, the
Arduino, and a logged-in account at a chinchompa area, none of which are available while the
user is away. First live run should watch the log for the `trap` category's menu-entry dumps,
which is where a wrong option name or object name would show up immediately.
