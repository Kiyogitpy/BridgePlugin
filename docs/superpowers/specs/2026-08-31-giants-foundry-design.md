# Giants' Foundry routine — design

2026-08-31. Status: spec approved pending user review; implementation not started.

Builds `ControllerAddon/routines/giants_foundry.py`: runs Giants' Foundry commissions
unattended — commission → moulds → crucible → pour → pick up → refine → hand in → repeat —
banking at the Foundry's own bank chest, stopping legitimately when the configured fill
items run out.

Primary sources, in order of authority:

1. **Recorded human session** `ControllerAddon/recordings/2026-08-31_003238_giants-foundry.*`
   (10m48s, two complete swords, 24,399 Smithing XP). Every timing/mechanic below marked
   MEASURED comes from this recording.
2. **`GET /api/minigames/giants-foundry`** (m8aq reader `minigames/GiantsFoundry.java`) —
   the routine's ground truth at runtime. Confirm the exact path against `/api/index`.
3. **`easy-giantsfoundry`** (Toofifty, Plugin Hub, read via the osrs-wiki MCP
   `pluginhub/easy-giantsfoundry`) — mould score table, heat-ramp curve, per-stage
   action constants. Data-only ports, same policy as mahogany-homes' Home table.
4. OSRS Wiki "Giants' Foundry" (offline snapshot, revision 15264119) — rules prose.

## Decisions taken with the user (2026-08-31)

- Metal mix **configurable**, default an ~equal mithril/adamant alloy (the recording's own
  mix; score 94 metal). No single hardcoded loadout.
- **Ice gloves (or smithing gloves (i)) equipped** is the pickup mechanism — verified at
  setup, safety-stop if absent. No bucket-of-water path.
- **Sweet spots are claimed** (one extra click on the current machine).
- **Poll-driven state machine** on the API's `phase`, with the reference plugin's numbers
  ported as *pure logic* for targeting — not a tick-synced predictive controller.
  `ctx.events` is an accelerator only, tolerated as `None`.

## Prerequisites (checked in `setup()`, safety-stop with a named reason if missing)

- Standing in the Foundry (region 13491, `atGiantsFoundry` true). v1 does not travel there.
- Ice gloves / smithing gloves (i) equipped (`/equipment`).
- **Both hand slots free of anything else** — MEASURED: an off-hand Imcando hammer blocks
  preform pickup with chat "You must free your hands so you can hold the preform." The
  human fixed it live via worn equipment (key `E`, `Remove` on widget 387.20). v1: check
  weapon/shield slots at setup and unequip to inventory if occupied (inventory is empty at
  that point in the cycle); gloves stay on.
- Sleeping Giants completed, Smithing level ≥ the configured metals (the game gates bar
  insertion at the smelt level; we simply let the insert fail loudly rather than model it).
- Configured fill items present in the bank (checked per cycle, not just at setup).

## Config

```python
DEFAULT_CONFIG = {
    # Fill plan: items usable on the crucible, with their bar metal/value and how many
    # bars of that metal the crucible should end up holding. Values are per the wiki
    # equipment table (equipment = smithing bar cost minus 1; bars = 1).
    "fill": [
        {"item": "Mithril platebody", "metal": "MITHRIL", "bars_each": 4, "target_bars": 14},
        {"item": "Adamant platelegs", "metal": "ADAMANT", "bars_each": 2, "target_bars": 14},
    ],
    "max_overflow_loss": 2,   # accept the overflow-confirm dialog if it loses <= this many bars
}
```

The recording used exactly this plan (14 mithril + 14 adamant bars via platebodies and
platelegs). `get_config` accepts a bridge config later if a `ControllerConfig` section is
ever wanted; v1 reads `DEFAULT_CONFIG` only.

**Fill planning is value-aware.** Targets that don't divide evenly by `bars_each` are
legal: the game caps the last item at remaining space and asks for confirmation
("Adding this item will overflow the crucible. You will lose N bars worth of X. Are you
sure?" — MEASURED, chatbox dialog). The planner prefers exact combinations; when the
remainder is smaller than the smallest available item it accepts the dialog iff
`N <= max_overflow_loss`, else declines and safety-stops with the mismatch named.

## Ground truth: what the API gives us

`/api/minigames/giants-foundry` returns, per read (fresh varbits, no TickCache):
`phase` (lifecycle), `commissionTypes`, `crucibleContents`/`crucibleBarCount`/
`crucibleState`, `selectedMoulds`/`usableMoulds`/`isMouldSelectionComplete`,
`preformState`, `temperature`, `completion`, `quality`/`startingQuality`/`qualityLost`,
`difficulty`, `sectionCount`/`currentSection`, `toolSequence`/`requiredTool`,
`minimumTemperature`/`maximumTemperature`/`temperatureStatus`, `sweetSpotState`
(NONE/AVAILABLE/CLAIMED/UNKNOWN from the HUD border colour), `activity`
(animation-derived: HEATING/COOLING/HAMMERING/GRINDING/POLISHING/…), `readyToHandIn`,
`atGiantsFoundry`. Python side: `None` = endpoint unavailable, an `error` key = reader
failed; neither means "no minigame state".

Restart safety falls out of this: every phase handler derives from live varbits, nothing
load-bearing lives in `ctx.state.data` (`STORED` — preform parked in storage — resolves by
picking it back up).

## Phase dispatch (`tick()`)

| API phase | Handler |
|---|---|
| `AWAITING_COMMISSION` | get_commission_phase |
| `PREPARING`, `SELECTING_MOULDS`, `MOULD_SET` | prepare_phase (moulds → fill → pour, sub-dispatched on `isMouldSelectionComplete` + `crucibleState`) |
| `PREFORM_READY`, `COLLECTING_PREFORM` | pickup_phase |
| `REFINING`, `STORED` | refine_phase |
| `READY_TO_HAND_IN`, `RUINED` | hand_in_phase (a ruined sword is still handed in — that is the game's own recovery; Kovac pays 30 xp / 60 coins and the loop restarts) |
| `INACTIVE` | safety-stop ("not at the Foundry") |

`before_break`: if a preform is held (`preformState == EQUIPPED`), park it in the Preform
Storage (the game's own pause mechanism — preserves heat and progress indefinitely) before
the break starts. On resume, `STORED` picks it back up.

## get_commission_phase

MEASURED, two working paths:

- **Standalone**: right-click `Commission` on Kovac (NPC_FOURTH_OPTION). Commission
  varbits (13907/13908) land ~0.1s after the click, before the dialogue is even dismissed.
  Dismiss with Space.
- **Chained after hand-in**: the hand-in dialogue offers a new commission — Space ×2,
  option key `1`, Space. hand_in_phase uses this; the standalone right-click is the
  recovery when `AWAITING_COMMISSION` is entered any other way (fresh start, restart).

Confirmation = `commissionTypes` non-empty on the next read. Ceiling:
`COMMISSION_MAX_ATTEMPTS` (4) then safety-stop.

## prepare_phase

### Moulds

Selection algorithm: for each part (forte/blade/tip) independently, pick the mould in
`usableMoulds` maximising `score(word1) + score(word2)` from the ported score table
(`easy-giantsfoundry` `Mould.java`: 32 moulds × 3 scored descriptors — port verbatim as a
Python dict; names match our API reader's `Mould` enum names). VALIDATED against the
recording: for "Narrow Flat" the human's picks (Chopper Forte +1, Serpent Blade, Needle
Point) are exactly the table's argmax.

Mechanics (all MEASURED):

- `Setup` on Mould jig (id 44777, "Mould jig (Empty)") opens interface **group 718**.
- Tabs: `View Fortes` / `View Blades` / `View Tips` on widget **718.12** (slots 0/9/18).
- Rows: `Select <mould name>` on widget **718.9** — the menu target carries the mould
  name, so the click is verified by name via `/menu`, and row positions come from
  `GET /widget/children?group=718&child=9` rather than hardcoded slots (rows are 17
  children apart per the reference plugin; scroll may be needed for lower rows — the
  recording never scrolled because optimal moulds sat high; handle "row not on screen"
  by falling back to the next-best visible mould rather than scrolling in v1, and log it).
- Confirm: `Set Mould` on widget **718.6**. Verify `isMouldSelectionComplete` and that
  `selectedMoulds` matches the plan on the next read; re-open and fix a mismatched part
  (ceiling `MOULD_MAX_ATTEMPTS` = 3).
- Requires a commission first (chat "You need a commission before you can set up the
  mould." — MEASURED; the dispatcher ordering makes this unreachable, but the chat line is
  logged if seen).

### Crucible fill

Withdraw the planned items from the **Bank chest (44630)** (banker.py; deposit any
leftovers first — MEASURED flow), then per item type: `Use <item>` (inventory widget
149.0) → click **Crucible (44776)**. A quantity dialogue follows (chatbox option keys
1/2/3 or type-amount + Enter — MEASURED; exact option layout to pin during
implementation from `/widgets/dump` on the first live run). Confirmation is authoritative
and per-metal: `crucibleContents` varbits (plus chat "You add N X to the crucible, worth
M bars"). No local estimate anywhere — the fur-pouch lesson.

- The crucible can be pre-filled from a previous cycle (`PARTIALLY_FILLED`/`FULL` on
  entry): recompute the remaining plan from `crucibleContents`, don't restart it.
- Overflow dialog: accept iff planned (see config), else decline (`No.` widget 193.0 —
  MEASURED) and safety-stop.
- Bank out of a planned item → diagnostic safety-stop naming the item (mahogany-homes
  plank-stop pattern).

### Pour

`Pour` on Crucible (full). MEASURED: chat "The score of the preform is N" lands +0.0s,
pour animation 810, then jig becomes "Mould jig (Poured metal)" and `workflowState` moves.
Pour is refused while a previous preform exists — the dispatcher ordering prevents this.

## pickup_phase

`Pick-up` on Mould jig (Poured metal). Confirmation: `preformState == EQUIPPED` (HUD
visible) — MEASURED +0.3s after the click, alongside chat "Your Ice gloves cool the
preform as you reach for it." Failure chat "You must free your hands…" triggers the
unequip recovery (see prerequisites), once, then retries; second failure safety-stops.

## refine_phase — the core loop

Each engine iteration reads the API fresh and answers "what is the single next click?"
Priority order:

1. `readyToHandIn` or `RUINED` → return (dispatcher takes it).
2. **Sweet spot**: `sweetSpotState == AVAILABLE` and `temperatureStatus == READY` → one
   `click_human` in place on the current machine (no re-fly). At most one claim click per
   AVAILABLE window (wait for CLAIMED/NONE before another). MEASURED payoff: +60 progress
   in one polish action (vs +10) and quality 142→147; claim confirm chats exist
   ("Your quick reactions allow you to…", green).
3. **Tool changed** (`requiredTool` differs from the machine we're working) → fall through
   to temperature/work targeting for the new tool. The tool sequence is known up front
   (`toolSequence`/`currentSection`), so the *next* tool's band is known before the
   boundary — no surprise.
4. **Temperature out of band** (`temperatureStatus` TOO_COLD/TOO_HOT) → `Dunk-preform` on
   Lava pool (44631) / `Quench-preform` on Waterfall (44632) — right-click menu options
   (GAME_OBJECT_SECOND_OPTION), via `click_menu_action` with `object_refresh_target`.
   MEASURED: the human uses ONLY the fast options, never slow Heat/Cool; heat moves
   ±27–33/tick (the reference plugin's DX_1 curve from index 10). **The interrupt is the
   next machine click itself** — no separate walk-away click exists or is needed.
5. **In band, not working** (`activity` not the tool's activity) → `Use` the required
   machine (Trip hammer 44619 / Grindstone 44620 / Polishing wheel 44621), once. Then
   *wait*, polling ~0.3s, watching only for: sweet spot, tool change, temperature exit,
   progress stalling. **Never re-click a machine while its activity is running** —
   MEASURED: "Your attempt to overwork the preform causes you to miss the action
   altogether" (red chat), one lost action per offence (the human paid it twice).

### Targeting (ported logic, pure Python, unit-tested)

Constants from `easy-giantsfoundry` `Stage.java`, CONFIRMED live in the recording:

| Machine | acts every | progress | heat per action | band |
|---|---|---|---|---|
| Trip hammer | 5 ticks | +20 | −25 | high (5/6) |
| Grindstone | 2 ticks | +10 | +15 | medium (3/6) |
| Polishing wheel | 2 ticks | +10 | −17 | low (1/6) |

Passive decay −1 heat per 2 ticks (MEASURED throughout the recording). Band min/max come
straight from the API (`minimumTemperature`/`maximumTemperature`).

When heating/cooling, the stop target is not "anywhere in band" but the band *end that
lets the whole remaining stage run*: tools that cool (hammer, polish) enter near the top
of their band, the grindstone (heats) near the bottom —
`target = band_edge − actions_left_in_section × heat_change`, clamped into the band, with
a margin covering one poll interval of fast-ramp drift (~35 heat at the curve's steep
end) plus walk-to-machine decay. Overshoot recovery is free: it becomes the opposite
adjustment next iteration, exactly like the human's own liberal over/undershoots in the
recording. The live temperature poll is always the ground truth for when to click; the
ported curve only chooses the intended stop point.

One reference-plugin fact to respect: **the first tool cycle after arriving at a machine
is usually nulled** (their comment; consistent with the recording's ~2-tick lag before the
first progress bump). Budget it in the work-watch timeout rather than treating a slow
first action as a stall.

### Quality guard

If `qualityLost` grows across two consecutive reads while we believe tool and temperature
are correct, stop working (walk one tile off the machine is unnecessary — simply withhold
further clicks) and re-read before acting again: protects against acting on a stale read
across a section boundary. 10 quality is lost per bad action; a sword is ruined at 0.

## hand_in_phase

Right-click `Hand-in` on Kovac (NPC_THIRD_OPTION). MEASURED sequence: chat "Hmm… sword
look good. Hand over." → +1.4s every foundry varbit zeroes, coins deposit chat, XP lump
lands (`xp Smithing +15510`) → Space ×2 → key `1` (new commission) → Space. Confirmation:
commission varbits populated for the *new* sword. Ceiling `TURN_IN_MAX_CLICKS`-style
(`HAND_IN_MAX_ATTEMPTS` = 6) — a cooldown only sets the rate of an unbounded loop.

## Ceilings, counters, safety (standing rules applied)

- Every click loop has a ceiling and a counter that **resets where success is detected**
  (a varbit/phase change), never where the attempt is made: commission (4), mould (3),
  fill attempts per item (3), pickup (3), pour (4), heat-adjust click failures (5),
  mould-row-not-found (5), storage-not-found (10), machine clicks with zero progress
  (5, cleared whenever completion grows), hand-in (6). (Amended post-implementation:
  the final review added the pour/heat/mould-row/storage ceilings and simplified the
  dead-click reset to completion growth.)
- Every wait budgets the walk (machines are 3–14 tiles apart; MEASURED distances in
  `Stage.java`: hammer→lava 4, grindstone→lava 7, polish→waterfall 10…).
- All object clicks pass `object_refresh_target` (+ `expected_action` where the option is
  fixed: "Use", "Pour", "Pick-up", "Dunk-preform", "Quench-preform", "Hand-in",
  "Commission"). Machines are fixed scenery — aims are stable — but the pattern is policy.
- XP as the sub-tick success signal for machine work: `smithingXp` IS now on `/player`
  (the one Java change; everything else needs no rebuild), but v1 as implemented keys the
  stall watchdog on `completion` (also a fresh varbit read) and does not consume the XP
  field yet — adopting it is a live-session follow-up, not a merge blocker. (Amended
  post-implementation to match what shipped.)
- Progress stall watchdog: `completion` unchanged for `WORK_STALL_S` (~12s, > two hammer
  cycles + the nulled first cycle) while believing we're working → re-evaluate from
  scratch; twice consecutively → safety-stop.

## Engine wiring

- `RoutineChoice.GIANTS_FOUNDRY` + `ROUTINE_FILES["GIANTS_FOUNDRY"] = "routines/giants_foundry.py"`
  (client restart required — the standing two-step rule). No `ControllerConfig.java`
  section: config is `DEFAULT_CONFIG`/`get_config` ignoring bridge config, the
  gemstone_crab pattern.
- Default stray-dialog check stays ON (no `SKIP_STRAY_DIALOG_CHECK`) — the commission and
  hand-in dialogues are advanced explicitly before returning, and a stray "click to
  continue" Space is harmless in every foundry state seen in the recording.

## Testing

Hermetic pytest (`_no_live_bridge` fixture discipline) over the pure logic:

- Mould argmax vs the recording's two commissions (Narrow Spiked given the account's
  unlocks; Narrow Flat = Chopper Forte +1 / Serpent Blade / Needle Point — pinned).
- Fill planner: exact fits, remainder-with-overflow accept/decline paths, resume from a
  partial crucible, bank-short diagnostics.
- Temperature targeting: per-tool stop targets and margins against the ported constants;
  pinned cases from the recording (e.g. dunk 724→813 over 3 ticks, hammer draining
  916→726 over 8 actions).
- Phase mapping: every API `phase` value routes to exactly one handler; `None`/`error`
  reads defer safely.
- refine_phase priority ordering: sweet spot beats work-wait; overwork guard blocks
  re-click while `activity` runs; quality guard halts on two dropping reads.

## Deferred / open

- **Reward shop, mould purchasing** — out of scope for v1.
- **Slow Heat/Cool options** — unused (human never used them; fast + reactive polling is
  measured sufficient at difficulty ~95). Revisit only if a rune-tier config (narrower
  bands) ever misses too often.
- **Smith's outfit progress bonus** (0.2/piece per action) — ignored in targeting; it only
  makes actions finish sooner, which the reactive loop absorbs.
- **Quantity-dialog exact widget ids** for the crucible fill — pin from `/widgets/dump`
  during first live session (the recorder captured keys, not the dialog widgets).
- Travel to the Foundry — v1 requires starting inside (region 13491).
