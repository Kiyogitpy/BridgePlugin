# Routine notes — measured facts and live-session history

Moved out of CLAUDE.md on 2026-09-14 to keep that file readable. **Nothing here is
obsolete**: every section below is the accumulated MEASURED record for one routine
(live-session findings, ids, budgets, failure modes, open worklists). Before working
on a routine, read its section here the same way CLAUDE.md's own instructions apply —
these facts were paid for live and must not be re-derived.

Cross-references elsewhere in CLAUDE.md of the form "see the X.py section" or
"the X.py notes below" refer to the sections in this file.

## Current state of routines/corrupted_gauntlet.py

**First live session 2026-09-06 (cg_live1-8, recorder `cg-bot1`). Reached lobby entry ->
geometry -> node lighting -> gathering unattended; craft/enter/fight NOT yet reached live. Nine
fixes, all MEASURED and pinned in tests -- read these before the next session:**
- **The fight signal is varbit 9177, NOT a Hunllef in `/npcs`.** The Hunllef is in the loaded
  scene from the START room and wanders in and out of `/npcs` there, so `derive_phase`/
  `enter_phase`'s old "a Hunllef id is present" test quick-passed nothing, prayed in the start
  room and spammed Attack at a Hunllef behind the wall (cg_live2). `tick()` reads
  `/varbit?id=9177` over HTTP every tick (`cg_varbit_fight`); `on_event` still mirrors the change
  faster. 9177 is 1 from +0.6s after Quick-pass until the kill.
- **`/collision?all=true` marks unlit rooms `0x40000000` and the scene border `0xFFFFFF`**
  (RuneLite's `CollisionDataFlag` names neither; they are "no data here" markers, not walls).
  `instance_nav.standable` now refuses both -- before, the start-room flood leaked through the
  corridor gap into 9092 unloaded tiles and prep never derived geometry (cg_live3). This also
  killed the reason for the old 600-tile `START_COMPONENT_MAX` cap (now 2000, only to catch a
  genuine runaway); a resumed instance with corridors already lit legitimately measures ~700.
- **Room pitch 16 CONFIRMED**; each doorway has a PAIR of Nodes (35998 + 35999 flank it, not
  lit/unlit); a lit Node becomes a "null" object 36000/36001; `Light` is the Node's LEFT option.
- **`ControllerState.break_due(breaks_cfg)` takes the breaks config** -- the routine's no-arg call
  crashed the process on the first `enter_phase` tick (cg_live4); the tests had faked it no-arg.
- **Every prep world click re-aims through the object's INSTANCE tile** (`_obj_refresh` ->
  `object_refresh_target(..., inst=True)`, and `_item_refresh` for drops). Template x/y repeat
  between rooms, so a template `/object/at` there resolves another room's twin; and a click at a
  pre-walk projection lands on bare floor (cg_live5).
- **Prep/gather/light/bowl clicks carry NO `expected_action` hover gate** (`_click_object(...,
  verify=False)`, the default). The gate reads `/hover` and withholds whenever the cursor is not
  yet over the target -- i.e. every click issued while walking up to a node -- so gathering
  withheld, fell to a menu with no option yet, pressed ESC and waited ~8s per node (cg_live5/6).
  These clicks track the moving target and confirm on their own outcome instead; `verify=True`
  keeps the gate for hijack-sensitive clicks (the Hunllef Attack still uses it).
- **Pickups spam LEFT-clicks** (`pick_up_drops`), nearest first, re-aimed, menu only on a
  withheld click (USER: "we dont have time to right click each individual item just spam click it
  up"). The human loots with the ground item's own left option.
- **Room hops use the VIEWPORT, not the minimap** (`travel._hop_to(prefer_viewport=True)`, used by
  `instance_nav.walk_to`) -- USER: "doing minimap hops between rooms is also not needed since we
  can see it in the viewport". A rendered, un-occluded tile is shift-clicked in the viewport at
  any hop length; the minimap stays the fallback for an off-screen or panel-covered tile.
- **Strong monsters (unicorn 9043 / scorpion 9044 / wolf 9045, `g.STRONG_MONSTERS`) are prayed
  against and left alone until a bow or staff exists** (`should_fight`) -- USER: "tier two
  monsters ... shouldnt be fought without a proper weapon they take too much time". A strong
  monster we won't fight does not interrupt `gather_node` (`fightable_attacker_present`); a
  demi-boss still does (`gather_ladder` flees it). Weak monsters (rat/spider/bat) always fought.
- **`prep_state` teleports home when no Barrier is in view** (a mid-run restart lands in some
  other room; geometry can only be derived in the start room), once, via the teleport crystal.
- **`drop_prayers_if_safe`** turns every prayer off when nothing is attacking (the Gauntlet has
  no altar; prayer 77 -> 50 in two minutes with protection left on, cg_live2).
- **Offline pass after the runs (not yet measured live):** a Node is lit from up to
  `NODE_CLICK_RANGE` (10) tiles with a per-tile confirm budget (the human clicked Light from
  3-11 tiles; the old "walk within 2 tiles first" was a whole extra walk per node);
  `_await_scene_reload` waits (<= 2.5s) for `/collision?all=true` to change after a confirmed
  Light before the caller routes (cg_live5/7: "no route ..." on the tick after EVERY light, the
  reload lands ~1.2s after the click); gathering chains up to `GATHER_CHAIN_MAX` (4) nodes and
  `craft_phase` up to `CRAFT_CHAIN_MAX` (12) steps inside one tick, since the engine loop cost
  ~1s between every single node/bowl click before; `gather_node` re-scans `/objects` only every
  `GATHER_OBJECT_POLL_S` (0.9s) while polling the cheap `/inventory` every 0.3s.
- The lobby is entered by the routine itself: `derive_phase` returns `lobby` when The Gauntlet
  (37340) is in view before a run (`cg_t0` unset), `lobby_phase` clicks `Enter-corrupted` and
  confirms on `regionId == REGION_CORRUPTED`. Wired so a plain launch from the LOBBY runs the
  whole thing.
See `MEMORY.md` -> gauntlet-live-session-2026-09-06 for the operational notes. First-session
worklist for the UNRUN half: craft (bowl group-270 widgets by text, pump/pestle/potion chain,
cook), the barrier quick-pass, and the fight ladder itself.

Sub-project 2 of the Corrupted Gauntlet (spec `docs/superpowers/specs/2026-09-05-hunllef-fight-
design.md`), written 2026-09-05 from the human recording
`recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1.*` and the cache. **Door-start mode,
not yet run live**: start it in front of the Hunllef's Barrier with both perfected weapons, fish
and an egniol; it quick-passes, fights, opens the chest, stops. Sub-project 3 (below) put the
prep in front of it. Facts that shape it, all MEASURED: the Hunllef attacks every 5 ticks; four
attacks then a marker animation (8754 "switch style" / 8755 tornado cast, alternating) flip the
style, ranged first -- `hunllef.StyleTracker`, replay-tested against every prayer click in the
recording; its NPC id IS its overhead prayer (9035 melee / 9036 ranged / 9037 magic / 9038
death, cache symbols); size 5, so every walk target is filtered against centre +-2 plus one
margin tile (`hunllef.forbidden_near_hunllef` -- the user's stomp concern); floor tiles are
GroundObjects 36047 warning / 36048 hit, polled from `/objects`. **The prayer-disable tell is
`/prayers.active` on the NEXT snapshot, not chat** -- `ladder()` only ever reads
`snap.prayers_active` (`FightSnapshot.from_bridge`'s `prayers.get("active")`); nothing in this
routine parses "Your prayers have been disabled!" or any other chat line, so a prayer dropping
is noticed the same tick `expected_prayer()` next disagrees with `prayers_active`, one tick
after the disabling hit lands. The fight loop holds `tick()` on `wait_ticks(1)` and reads events
with `EventStream.since(mark)` because `on_event` is dispatched by the engine loop and never
runs while a routine holds the tick -- **`mark` is taken ONCE before the loop and advanced to
`ctx.events.mark()` immediately after every `since(mark)` read** (C2, final-fix-brief.md): taking
a fresh `mark()` at the top of each iteration instead reads a sequence number the previous
iteration's own tick wait had already pumped past, silently dropping every event that arrived
during that wait. **`/npcs`/`/npc/by-index` carry `animationId`** (`NPCProvider.buildNpcData`,
added 2026-09-05, C3) -- requires a client rebuild before it shows up; `feed_tracker` cross-checks
it against the tick's own animation-change events rather than trusting either source alone (see
that function's own docstring). First live session's worklist is the spec's checklist:
`instX/instY` centre-vs-corner for a size-5 NPC, 8754's meaning, the ranged/magic projectile
ids, snapshot cost per tick.

**Two walking rules that look alike and are not (settled 2026-09-05, `e3eff2f`).** A diagonal
STEP needs BOTH orthogonal corner tiles standable and open (`instance_nav.InstanceMap.step_ok`,
which `hunllef.flood` uses unchanged); a diagonal INTERACTION needs only ONE of the two corner
routes open (`travel.can_reach`, the JEFF's-upstairs measurement in the mahogany section). An
implementer read the mahogany note as the movement rule and relaxed `flood` to OR-logic to make a
plan-authored test pass; the test was the wrong one. Do not "unify" the two.

Sub-project 3 (spec `docs/superpowers/specs/2026-09-05-gauntlet-prep-design.md`, plan
`docs/superpowers/plans/2026-09-05-gauntlet-prep.md`, 32 commits 91bf1cc..84ed4eb) adds
`explore_gather` / `craft` / `upgrade` in front of `enter`, all dispatched by `derive_phase`
from live state plus the run clock, never from a stored phase alone. **Not yet run live**; the
spec's live checklist is the first session's worklist, starting with whether `/objects` lists
unopened rooms and the real room pitch. The shape:
- **Route** (USER's, `gauntlet_maze.ROUTE`, relative to the start side): from S to the
  inner-ring corner on the RIGHT, one room OUTWARD, reveal (light, do not enter) the border room
  there, walk the middle ring LEFT two rooms revealing each border room, then back in and a full
  circle of the inner ring. USER: the demi-bosses are "in the 3 center tiles of the 7x7 on each
  cardinal, at the border". Rooms are `(right, outward)` offsets from the start room's centre in
  INSTANCE space (`room_centre`/`room_of`; template x/y repeat between rooms, so they cannot
  identify one); `outward` is the unit vector Barrier -> start centre; `room_pitch` (config, 16)
  is UNVERIFIED and is the second live question.
- **Kit** (`gauntlet_kit.KIT` then `UPGRADES`, a priority ladder with a time budget, USER's
  choice): T1 helm/body/legs, attuned bow + attuned staff, 20 fish, 2 egniol; then T2 pieces and a
  perfected weapon when the route's reveal found a dragon (orb) or dark beast (bowstring).
  `kit_deficit`/`kit_minimum_met`/`craft_plan` are pure functions of inventory + equipment, so
  every plan is a re-derivation and nothing depends on remembered progress. A basic weapon costs a
  frame and NO shards (wiki; an implementer gated it on 50 shards, reverted in `9e1c9ff`).
- **Time gates** (`DEFAULT_CONFIG`, seconds from the first `REGION_CORRUPTED` read, resynced from
  the game's "Time remaining" chat): gathering stops at `gather_deadline_s` 270 once the minimum
  kit is met, unconditionally at `gather_hard_deadline_s` 330; `enter_by_s` 420 forces the
  barrier regardless of kit; `timer_s` 450 is the game's own 7:30.
- **`WALKING` is a falsy sentinel meaning "still approaching, not a failure"** returned by
  `bowl_make` and friends. Check it by IDENTITY (`is WALKING`) before any failure accounting
  (`craft_phase` at its `if ok is WALKING: return`). The upgrade phase accumulated it with `or`
  and counted three walking ticks as three failed crafts, forfeiting the perfected weapon
  (`7f055e0`). Any future "in progress" sentinel needs the same treatment at every caller.
- **`before_break` plays the run out, and a play-out loop must override the phase gates it
  drives** (`7f055e0`): `enter_phase` refuses the whole tick while `break_due()` is true (so a
  break cannot interrupt a quick-pass), and `before_break` calls `tick()` until `done` -- with the
  break due for the entire play-out, the two spun against each other with no pacing.
  `data["cg_playing_out"]` tells `_holding_for_break` to stand down, and a tick that made no
  phase progress sleeps `TICK_S`. Same family as GOTR's "a break must start at the round end".
- The bowl is driven by widget TEXT + NAME in group 270 (`_bowl_widget`, one `Make` per tier
  step); `use_on(confirm=)` is what lets the pestle step confirm; `cook_all` is one click;
  `stuck_watch` stops the run on a tile/inventory/phase stall but not during a demi-boss fight.

**Final-review fix wave, 2026-09-05** (`.superpowers/sdd/2026-09-05-gauntlet-prep/final-fix-brief.md`),
still not run live -- every fact below is reasoned from the recording and the code, not measured
against a live client:
- `craft_plan` is now idempotent (a deficit against each stage of the vial/dust/potion/cfish
  pipeline, not a running total), so a re-plan every tick no longer re-queues a step whose effect
  is already sitting in the inventory.
- **Post-craft fishing pass**: `prep_state`/`explore_gather_phase` remember every Corrupt Fishing
  Spot tile seen while exploring; once cook has run and the kit's higher fish target (20, vs the
  fight's own `min_fish` 10) isn't met, `craft_phase` makes one bounded (`FISH_PASS_MAX_S` 60s)
  trip back to the nearest recorded spot before falling back to its normal plan.
- **Timer chat resync**: `on_event` matches the game's own "Time remaining: M:SS" chat line and
  resets `cg_t0` so `time_left` agrees with it, closing any HTTP-latency drift between our own
  clock (started from the first `REGION_CORRUPTED` read) and the game's.
- **The cached instance map (`cg_imap`) is dropped on every scene change, not only leaving the
  fight**: after a successful `light_node` (the recording shows LOADING/LOGGED_IN ~1.2s later),
  after `teleport_home`, and on every phase change in `tick()` -- a stale grid answers
  `maze.edge_open`/`component` questions against a scene that no longer exists.
- **`/objects` room coverage is still the first live question to settle** (unverified whether the
  bulk scan actually lists rooms not yet entered, and the real room pitch) -- everything above
  assumes the original design's answer to it.

## Current state of routines/guardians_of_the_rift.py

- **Outcome-driven since 2026-09-04 (not yet run live in this form).** Every `_commit` window
  became `_await` (signals + HTTP confirm, chained inside one tick up to `CHAIN_MAX_ACTIONS`/
  `CHAIN_MAX_S`); Enter clicks take the game's verdict from `MENU_CLICKED`; walks return on
  `PLAYER_MOVING`. Baseline to beat: run 76, 48% of the round in gaps over 4s, 5732 earned RC
  XP/round (run 75). First live session's worklist is the spec's live-verification list
  (`docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md`), plan Task 13.

Written 2026-09-01 from research only — **no human recording exists** (user decision;
the spec records it). Design: `docs/superpowers/specs/2026-09-01-guardians-of-the-rift-design.md`,
whose "Live-verification checklist" is the first live session's worklist: every object
name/action in the routine's LIVE-VERIFY block, the Dark Mage row of the NPC Contact
interface (group 75 child scan), whether cell tiles (GroundObjects) appear in `/objects`
at all, and the temple/altar instancing check. **Not yet run live.** Ground truth is
`GET /api/minigames/guardians-of-the-rift` (round phase re-derived Python-side — Gson
serializes fields, not the reader's Java helper methods) plus
`/api/storage/essence-pouches`; `runecraftXp`/`miningXp` were added to `/player` for
sub-tick imbue/mine confirmation (client rebuild required). Strategy per the user's
decisions: reactive priority ladder (repair broken barrier > huge-remains portal >
spend stones > place cell > altar trip balancing elemental/catalytic energy > craft >
mine), hoard reward points, deposit runes, NPC Contact pouch repair between rounds.

A human recording DOES exist after all (`recordings/2026-09-01_061225_gotr-human1.*`,
9m25s, 238 actions) -- use it for cadence questions before reasoning from the wiki.

**Live sessions 2026-09-01 (runs 1-35) and 2026-09-02 (runs 36-41). Everything below was
MEASURED; the shape most of them share is the transitional-snapshot family.**

- **Log the ladder.** `_log_state` writes one `state=... action=...` line whenever the
  (round state, chosen rung) pair changes and a heartbeat every 20s. Before it, run 35
  sat 63s in idle drift, then entered and left the huge-remains portal 6s apart, and the
  log could not say which rung did either. Read those lines first when debugging.
- **A portal/altar teleport hides the HUD for 2+ ticks, and that snapshot reads as
  "idle" (power -1, both altars UNKNOWN, `inArena` still true).** The 2-tick between-round
  streak was not enough: run 37 exited the huge-remains pocket 3s after entering it (USER:
  "went in and out of portal twice without mining"). `ACTIVE_SETTLE_S` (5s): between-round
  ACTIONS wait that long after the last active read; the log shows those ticks as
  `action=settling`. A genuine round end costs one 5s delay.
- **The once-per-game mining latch (`gotr_batch_done`) is set the first time the ladder
  CHOOSES "craft"**, i.e. when the batch target or the altar-timed switch actually fires.
  Two wrong keys were tried live: unconditional in-round (idled a mid-round join with 0
  frags for the whole round, run 37) and the reader's mined count > 0 (latched on the
  FIRST fragment and dragged the bot back over the rubble with 3 frags, run 38). The
  reader's `minedFragmentsThisGame >= craft_at_fragments` re-latches after a RESTART, which
  otherwise loses the latch while the game keeps the fragments.
- **A verified Enter/cell click HOLDS the ladder** (`_hold_action`/`_held_action`): run 38
  clicked the huge-remains portal with 10s left, the next tick read 7s, the portal rung
  dropped below `PORTAL_MIN_SECONDS`, place_cell won and its click cancelled the walk. The
  same shape at the cell tile in run 40 (a portal opening 2.5s after the Place-cell click,
  cell still in hand three rungs later). A hold is dropped the moment its precondition is
  gone (cell placed, stones spent), so it never pins a rung with no work. The portal entry
  is also DISTANCE-gated: `secs < tiles * PORTAL_RUN_S_PER_TILE + PORTAL_ARRIVAL_MARGIN_S`
  skips the spawn for `PORTAL_SKIP_BLOCK_S`.
- **THE MINIMAP IS A UI BLOCKER.** Run 39 clicked the identical pixel once a tick for two
  minutes "heading for the rubble" with the player never moving (USER: "seems stuck"). A
  frame dump with the point marked put it INSIDE the minimap circle: `/ground/walk`'s
  `onScreen` tests viewport bounds only, and in the resizable layout the minimap and its
  orb column are drawn OVER the top-right of the viewport. `GameStateProvider` now adds
  `RESIZABLE_MINIMAP_WIDGET`/`_STONES_WIDGET`/`FIXED_VIEWPORT_MINIMAP`/`MINIMAP_ORBS` to
  `uiBlockers` (client rebuild, done 2026-09-02), and `_direct_walk` skips a candidate
  tile whose projection is under any blocker and pulls in instead (minimap hop when every
  candidate is). Note the blocker rect moves with the sidebar: canvas 2287 wide put it at
  x 1852-2287, sidebar collapsed (2529) at 2094-2529 -- never hardcode it.
- The bridge is NOT the slow part: `/objects?interactable=true` 108ms, everything else
  15-120ms. The ~5s gap on the FIRST tick of every run was `_reset_counters` seeding its
  baselines at -1, so the first snapshot read as a mine AND a craft delta and the reclick
  guards held the rung. The first snapshot now only seeds (`seeded` flag).
- **WALK FIRST, CLICK CLOSE applies inside altar rooms too.** The Cosmic altar stands 20
  tiles from its entry portal (player (2122,4833), altar (2142,4833)); at that range the
  projected point was on the inventory panel's BORDER (widget 161.97, which the container
  blocker rect did not cover -- both inventory PARENTS are blockers now, rebuilt
  2026-09-02) and eight withheld clicks safety-stopped run 42. `ALTAR_CLICK_RANGE` (8):
  `_work_altar` and the exit-portal branch `_direct_walk` in first. Same for Chaos.
- **An off-window projection needs a RECOVERY, not a retry.** `click_entity` skips a
  click whose aim point is outside the window (camera facing away) and both tiers return
  False silently; the Uncharged cells restock spent its whole budget that way at a round
  end (run 41) and prep started with 4 cells. `_bring_into_view` (camera turn toward the
  point + walk toward the object) now backs the restock, deposit, entry-barrier and both
  Enter clicks. VERIFIED on the deposit pool in run 43 (two skips, one turn, then a clean
  click).
- Runs 40-45 each completed full rounds unattended; the remaining known waste is ~60-80s
  of `idle` per round once the 120-fragment batch is spent (user rule: no re-mining).
- **Altar choice balances energy past a gap (USER 2026-09-02: "balance catalytic and
  elemental if possible, keeping priority but balance it out").** `choose_altar` still
  takes the higher-tier altar, but once one type leads by `ENERGY_BALANCE_GAP` (40, about
  half a full trip's ~78 energy) the lagging type's altar wins when usable. Run 45 had
  ended 25/209. And `ALTAR_TRIP_MAX_POWER` (96): no new altar trip that close to 100% --
  the trip started at 99% came back to a finished round holding 40 stones and a cell.
- **Nothing pulls the bot out of the pocket before the batch**: a WEAK cell carried over
  from the previous round made place_cell outrank mining at power 10 (run 44) -- place_cell
  and in-round restock now wait for `batch_done` while `miningArea` is the east pocket.
- Pouch repair via Astral Contact (the renamed NPC Contact, option 2) VERIFIED live at a
  round end in run 44: all three pouches back to full capacity.
- **The chatbox BODY (scrollbar included) swallows clicks too.** Run 46 clicked the walk
  tile toward the Cosmic altar at screen (1037,1266) -- the chatbox scrollbar -- ~30
  times, then a scheduled break froze it there (USER: "chat ui scrollbar softlocked us in
  altar"). `CHATBOX_PARENT` is a blocker now (rebuilt 2026-09-02). Two nets so the NEXT
  unregistered overlay cannot do this: `_direct_walk` escalates to a minimap hop once
  the same target has been clicked twice with the player on the same tile
  (`WALK_STUCK_CLICKS`), and `_stuck_watch` logs `[!] gotr: STUCK?` after
  `STUCK_WARN_TICKS` (15) in-round ticks with no progress signal (tile, rung, every
  inventory count, side area) and safety-stops at `STUCK_STOP_TICKS` (40). USER: "you
  need to monitor and catch this" -- the live monitor keys on `[!]` lines and on any
  pixel clicked 5+ times in a row; a stops-only filter missed this for 30 ticks.
- **After a relogin the camera can face AWAY from everything**, so every click site
  needs the off-window recovery, not just the ones that failed first: run 48 stopped on
  "power-up never registered" after six skipped right-clicks on the Great Guardian.
  `_bring_into_view` is now INSIDE `_click_escalating` (since run 58), so every site
  that uses it gets the recovery for free -- adding it per site kept missing one: the
  Power-up (run 48), the three exit portals (run 56, "could not leave the altar room"),
  then the cell tile through `_approach_or_click` (run 58, "charged cell never
  consumed"). Only the Power-up right-click, which bypasses the helper, keeps its own.
- **The pouch-repair cast leaves the MAGIC tab up.** `_repair_pouches` presses F6 and
  clicks the spell; nothing switched back, so every pouch Fill/Empty click afterwards
  landed on the spellbook -- run 49 clicked the three pouches plus the remains 40 times
  in a row with `inventoryTabOpen` false, until `_stuck_watch` stopped it. Every
  inventory-item click site (`_fill_pouches`, `_empty_pouches`, `drop_talismans_phase`)
  now calls `controller.open_inventory_tab` first, and the cast re-opens it itself.
- **The huge-remains portal is found by ID (43729 in, 38044 out), never by name.** The
  temple has other Portals offering Enter -- 43692 at (3615,9469) in the lobby -- and the
  nearest-by-name pick walked at that one for 15 ticks (run 49). Both ids come from the
  human recording.
- **A break must start at the ROUND END, never mid-round.** Run 50: a scheduled break
  began at 08:25:45 with the bot inside an altar room; the round ended during the
  8-minute break, the altar exit then dropped it in the LOBBY with 39 stones and a cell
  wiped, and it waited a whole extra game at the barrier. `before_break` now plays an
  active round out (it keeps calling `tick`, at most `BREAK_DEFER_MAX_S` = 12 min) and
  leaves any side area before returning, so the break overlaps prep and the next game
  instead of eating the current one. The round-over test is SUSTAINED
  (`BREAK_INACTIVE_READS` = 6 consecutive non-active reads): the first version broke out
  on one transitional read and started the break inside the Chaos altar 5s after
  entering it (run 51) -- the same transitional-snapshot family, once more.
- **"Missing clicks while moving" (USER 2026-09-02) was RE-CLICKING, not missing.** The
  recorder run alongside run 53 showed routine clicks and game-registered actions matching
  ~1:1 (74 clicks, 72 actions, one Deposit Pool click became Walk here) -- the game was
  receiving them. What the eye saw was the cursor re-flying to the SAME target once a tick
  while the player was still walking to it: MEASURED run 52, Deposit Pool re-clicked within
  8s on 21 of 41 clicks, huge remains 17 of 37, because those rungs confirmed on an
  inventory delta with no commit window. Every verified click now gets a commit window of
  base + `_walk_budget` (tiles * `PORTAL_RUN_S_PER_TILE`), the mahogany "budget the walk"
  lesson applied to all of them. Diagnose this class with the recorder + the same-target
  re-click count, not the routine's own log, which reports every one of those clicks as a
  success.
- **Leftover essence blocks the between-round cell restock.** The inventory is usually
  FULL of essence when a round ends (wiped at the next start), so `emptySlots` reads 0 and
  the Take could not fit -- run 55 left prep with 1 cell and paid an in-round restock trip.
  `between_rounds_phase` now makes a slot first (fill pouches if they have room, else
  `_drop_one` essence) and takes cells next tick.
- **loginIndex 9 is "Failed to login. Please try again."** (a mid-round disconnect whose
  first auto-login attempt was refused). `LoginProvider` had no click point for it and
  login.py spun on "no login click point" for minutes (run 56). It now exposes the Try
  again button as `okButton` (real y 311, same layout family as the index-24 dialog).
- **A side room after the round has ended reads as "outside".** The HUD is gone, so
  `side_area()` cannot recognise an altar room / the portal pocket, and the outside
  streak safety-stopped run 57 on "start at the Temple of the Eye" while standing in the
  Earth altar. `_leave_side_area` (an exit Portal in the scene is the tell) now runs from
  the third outside read, and `before_break` reuses it.
- **The altar Enter must fit inside the rotation timer, walk included.** The game performs
  the Enter on arrival; run 60 clicked a guardian at 11s on the timer with a 5s run,
  arrived to a rotated pair and waited out another cycle holding 21 essence.
  `altar_trip_phase` now waits (walking closer meanwhile) when
  `rotation < _walk_budget(portal) + ALTAR_ENTER_MARGIN_S`.
- Network drops (five in two hours on 2026-09-02 night) and failed rounds on a thin world
  are NOT routine bugs; the routine rides both out (quiet reconnect wait in login.py,
  `_leave_side_area` for a round that ends while inside an altar).
- **World hop and camera at startup (2026-09-03).** After seven straight failed rounds on a
  non-themed world (USER: "gotta be on a guardians of the rift world, otherwise i dont
  think we finish a game") the routine hops through the in-game World Switcher with REAL
  clicks (user choice over a client-API hop): `GET /world` (`WorldProvider.java`) reports
  the current world, the logout-tab button, the World Switcher button, the list's VISIBLE
  clip (69:17 -- the list widget itself is the 10455px scrollable content and a row can be
  "inside" it thousands of px off screen) and every numeric row. Two traps met live: the
  list has TWO numeric columns (world number and player count; "459" appeared twice, the
  first in the player column) -- `_world_rows` keeps the leftmost column; and
  `controller.scroll_wheel` silently no-oped from a routine because `FIRMWARE_PROTOCOL` is
  set in `__main__`'s copy of controller.py only -- it is now mirrored into
  `input_state.firmware_protocol` and read via `_firmware_protocol()`, which also fixes
  the flush gate for routine-side flights. `GOTR_WORLDS` has 409 first (USER: "409 is
  preferred"). VERIFIED live run 66: 465 -> 409 in 7s. The camera defaults (hold UP until
  the pitch stops rising -- 1024 -> 4160 on a fresh client -- then 3 wheel detents out, only
  when the pitch actually moved so routine restarts do not keep zooming out) now live in
  `login.setup_camera`, applied by the ENGINE once after every login for every routine
  (USER 2026-09-03: "add that as a default in login.py so it always does that for every
  routine") -- `login.ensure_camera_after_login` runs after the focus gate in main().
- **Human benchmark (USER, `recordings/2026-09-03_061331_guaridianoftherift-benchmark.*`,
  8m53s incl. prep): 7545 Runecraft xp (4395 + the 3150 rift bonus at RC 70), Crafting
  +725, 8 rift guardians assembled + 8 barrier placements, ~60s per bench->altar cycle.**
  Bot run 74 on the same world, one full round + prep (9m36s): 2625 + 3150 bonus, 5 altar
  trips + 2 portal trips, ~75-95s per cycle. Per-trip work matches (bench ~30s, altar
  ~14s, chores ~10s); the cycle loses time in the approach walk before each click (the
  human clicks the bench/pool from across the arena and lets the game path -- live
  collision covers 14 tiles, so farther targets read unreachable and were walked to
  first), the 10s rotation floor, and a do-nothing tick after every teleport. Hence
  `_arena_direct_click_ok` (arena-side, same side of the rubble, <= 20 tiles: click, the
  commit budgets the walk) and `ALTAR_MIN_SECONDS` 10 -> 6. The guardian rung
  (`create_guardian`, Assemble at the lagging type's essence pile, 2x+1 one-type energy)
  fell back to the barrier for the whole round because the count sat at 10/10 on a full
  world -- it only fires when the reader shows room. Measure with the recorder's `xp`
  records over the round window (see the parse in the 2026-09-03 session), not the
  routine log, which has no XP.
  **RESULT (run 75, first round with all three changes, 06:59:40-07:09:45 prep+round,
  10m05s): Runecraft 8882 (5732 earned + 3150 bonus), Crafting 845, 5 altar trips, 2
  portal trips, 4 guardians assembled -- against the human's 7545 (4395 + 3150) in 8m53s.
  Per minute 881 vs 848; earned-per-round 5732 vs 4395.** The guardian creation XP
  (RC 30/100/180/250 per assemble, wiki table) is a big part of the jump. Cycle times that
  round: 80/100/79/47/69s.
- **Arena entry is gated on the BARRIER OBJECT'S ID, not the cell ring.** Cache config/loc:
  43700 `gotr_barrier` passable, 43849 `gotr_barrier_closed` (red, round running), 53263
  `gotr_barrier_noentry` (200-player cap before a start). The earlier "any live cell barrier
  means a round is running" heuristic was measured on a quiet world where the ring reads
  INACTIVE between games; on themed world 409 the ring stays charged between games and run
  67 waited 47 minutes in front of a passable 43700 (USER: "never enters?"). Run 68 passed
  on the first click.
- **Cell targeting: STRENGTHEN (a live barrier exactly one tier below the cell, 7/13/22
  energy) > RECHARGE (any live barrier, lowest tier first, 2/5/9/15 -- a lesser cell heals
  a stronger barrier, wiki "Gaining energy" rev 15303509) > PLACE on an INACTIVE tile (2).**
  USER 2026-09-03: "power guardian, dump our cell, then go deposit runes" (that ladder
  order already held; only the target changed). A first cut restricted the cell to tiles
  BELOW its tier after run 69's MEDIUM-on-STRONG clicks were ignored; that was the wrong
  lesson -- a full-health barrier has nothing to recharge -- so a tile that ignores
  `CELL_TILE_TRIES` (2) clicks is skipped for `ACTION_BLOCK_S` and the next-best tried.
- `_startup_checks` defers until the inventory OR equipment reads non-empty: the first
  logged-in tick after an auto-login served an empty inventory and run 47 stopped on "no
  pickaxe" with a Rune pickaxe equipped.
- Round cadence after the fixes (run 40, one full round): prep-mine in the pocket with
  paced attempts, first frags 2s after the round goes active, 120 frags at +70s, craft ->
  fill -> craft to a full inventory -> altar (imbue, empty, imbue, exit) -> Power-up ->
  Place-cell -> portal trip -> craft, repeating; the round ended at 100% with the bot in
  the portal pocket, and the exit + prep walk ran under the settle guard.

## Current state of routines/deep_sea_trawling.py

Written 2026-09-06 from the OSRS Wiki (Deep sea trawling rev 15297127, Giant krill shoal rev
15237362, Crew Management rev 15290062), the decoded cache (dump `5bc2ff1e`) and
`runelite-api-1.12.38`. Design: `docs/superpowers/specs/2026-09-06-deep-sea-trawling-design.md`,
whose "Live-verification checklist" is the first session's worklist. **There is no human
recording and NOTHING has been run live.** Needs a client rebuild (`GET /trawling` and the
Routine dropdown entry are both Java).

- **The point of the routine is one click per game tick.** The game rolls for a catch every 3
  ticks while a net operates in range; clicking the net every tick makes it roll every 2 -- a
  flat +50%. `trawl_burst` therefore aims once and then calls `click_human` (in place, ~92ms) on
  the tick clock; a fresh `click_entity` flight per tick costs ~1s and cannot hold the cadence.
  No simulated misclick in the burst: a sloop deck is a few tiles across and
  `MISCLICK_OFFSET_PX` is wider than the net.
- **The range test is the game's own Sailing XP drop, not distance.** Every roll grants a small
  Sailing XP drop (a successful one also grants Fishing XP), so a pushed `XP_GAINED` proves the
  net is operating in range; `RANGE_LOST_TICKS` (6) without one means the shoal has moved.
  Distance to a moving 6x6 object only ever *aims* the boat.
- **The burst holds `tick()` for up to 120s (`BURST_MAX_S`), so `on_event` never runs during
  it** -- it reads
  `ctx.events.since(mark)` and advances `mark` after every read (the Hunllef C2 rule; re-taking
  `mark()` per iteration drops everything the tick wait pumped past, i.e. the whole heartbeat).
  With `ctx.events is None` there is no pushed heartbeat, so the burst falls back to
  `NO_EVENTS_STALL_TICKS` (100) ticks without the net's fish COUNT growing.
- **Target is Giant krill / Great Sound, and that is a level decision.** At Fishing 75 /
  Sailing 64 the account can reach only shallow shoals (krill 69, haddock 73) and only the Rope
  net, so **v1 has no depth management at all**. Great Sound's 7 stops fit in ~56x49 tiles, i.e.
  inside one loaded 104x104 scene, so the shoal is essentially always in `/objects`; Haddock's
  routes span five regions and the shoal leaves the scene.
- **`trawling_routes.py` holds the Wiki's stop tables as raw `(regionId, rx, ry)` markers.** It
  is an ACCELERATOR, never truth -- the live shoal object decides where the shoal is; the table
  only says where to sail when none is visible. The stored order is the Wiki's own `label`
  field, and the pinned test that every leg is traversable at <= 3.0 tiles/tick is the evidence
  that label order *is* visiting order (max implied speeds 1.57 / 1.78 / 2.52 / 1.58 across
  great_sound / simian_sea / sunset_bay / turtle_belt).
- **`GET /trawling` (TrawlingProvider.java) exists for exactly two things**: item container 968
  (`sailing_trawling_net`, size 250, the per-player fish count) and 963..967 (the cargo hold).
  Nothing else on the bridge can read them and no m8aq reader covers sailing. `netFish` is
  `None`, not 0, when the container is missing -- that is "not aboard a boat with a net".
- **Hard precondition: a crewmate on the helm.** Helmsmanship lets a crewmate hold the helm so
  the player can stand at another facility while still clicking the heading (Wiki, Crew
  Management); without one the player must stand at the helm and cannot trawl at the same time.
  `_startup_checks` reads `/sailing.navigating` for this.
- **Never world-hop, never teleport, never disembark with fish in the nets** -- they are
  discarded without a warning. `before_break` empties the nets before a break starts.
- **A trip's length is set by the FISH CRATES in the hold before it starts, and nothing later can
  change it -- so `crate_stock`/`_log_crate_stock_once` report it once per run and warn when
  there are none.** The v1 spec deferred banking on the arithmetic "a 240-slot hold lasts many
  hours at ~5 fish/min"; that was WRONG (240/5 is 48 minutes, and the real yield is ~8 fish/min:
  10.5% catch chance at Fishing 75 x 50 rolls/min x 1-2 fish). An UNCRATED run therefore ends in
  `done_phase` after about half an hour. Crates fix it for free -- the game fills them
  automatically when the net is emptied into the hold, and the asymmetry is the point: an EMPTY
  crate (obj 32366) is `stackable=yes` so any number cost ONE cargo slot, while a filled one is
  not stackable and holds 10 fish in its slot. ~240 fish becomes ~2,390, about five hours.
- **Banking is sub-project 2**, spec `docs/superpowers/specs/2026-09-07-deep-sea-trawling-
  banking-design.md`, NOT built. The geography is the favourable part: the Great Sound holds both
  the krill shoal and Land's End, whose mooring point needs Sailing 5 and has a bank deposit box,
  so the round trip is tens of tiles inside one sea. The hazard is the whole design: banking via
  a dock crewmate requires DISEMBARKING, which discards anything left in the nets, so the nets
  are emptied and `netFish == 0` is CONFIRMED before the boat ever docks -- an unconfirmed read
  abandons the trip to `done_phase` rather than disembarking. Its first live question is whether
  a "bank boat" exists that empties cargo without disembarking, which would avoid the hazard
  entirely. Note Land's End has a deposit BOX, not a bank, so it cannot restock crates: that is a
  Crafting loop (filled crate -> Empty -> camphor crate -> ice cooler) and its own sub-project.
- Still out of scope, deliberately: chum-station baiting (+20% fish, and the highest-value
  unbuilt thing after banking, but the station's `Operate` interface is unseen), depth
  management, and a second net.

## Current state of routines/wintertodt.py

Written 2026-09-08 from the OSRS Wiki (Wintertodt rev 15276053, Strategies rev 15278826), the
cache dump `5bc2ff1e`, RuneLite's own decompiled `WintertodtPlugin` and live bridge reads.
Design: `docs/superpowers/specs/2026-09-08-wintertodt-design.md`, whose live-verification
checklist is the first session's worklist. **There is no human recording and NOTHING has been
run live.** Needs a client rebuild (the `RoutineChoice` entry is Java).

- **`GET /api/minigames/wintertodt` already serves the whole HUD** -- points, energy, warmth
  (raw 0-1000), `secondsUntilRoundStart`, and per-brazier LIT/UNLIT/BROKEN plus pyromancer
  ALIVE/INCAPACITATED. VERIFIED live 2026-09-08. **No Java change for state.** But
  `isOperational()` and `isRewardEligible()` are Java METHODS, so Gson omits them and
  `wintertodt_state` re-derives both -- the same trap CLAUDE.md records for GOTR.
- **NEVER MATCH A BRAZIER OR CRATE BY NAME.** Nine objects share three names: the unlit
  brazier (29312) and the broken one (29313) are both `Brazier` and differ only in their
  action, and SIX objects are called `Crate` -- 29316-29320 hand out hammer/knife/axe/
  tinderbox/concoctions while 29301-29305 are scenery with no ops at all. This is the
  shut-vs-open door lesson five ways over.
- **There is NO banking and NO loadout.** Every tool is free from the arena crates and USER
  chose never to search the Reward Cart (it holds 8,000, so nothing is lost). The routine never
  leaves region 6462, which is why `derive_phase` returns `stop` rather than trying to travel.
- **Chop, fletch and feed are CONTINUOUS actions**, ended by the game's own chat lines
  (`Your inventory is too full`, `You have run out of bruma roots`). One inventory is about
  three clicks, so this routine is an interrupt handler, not a click loop. Re-clicking a
  running action cancels it.
- **Fletching is a deliberate POINTS-over-XP trade** (USER's choice). The wiki is explicit that
  fletching is SLOWER Firemaking XP; it buys reward-cart rolls. A low FM/hr is the design, not
  a bug. `fletch=False` is the one-key switch to the faster-XP roots-only method, and
  `feed_item`/`derive_phase` are written so that switch cannot deadlock a full inventory of
  roots -- pinned by test.
- **500 points is a CLIFF**: below it the round pays nothing and leaving mid-round forfeits
  everything. Hence warmth outranks all work below it in the ladder, and `before_break` plays
  the round out (GOTR's lesson).
- **The 3x3 area-attack warning is a GameObject (29325), not a spotanim**, so it arrives as an
  ordinary `OBJECT_SPAWNED` event. The dodge rung is an accelerator only: if it never arrives,
  warmth is healed reactively instead and nothing else breaks.
- **Expect the brazier to be contested.** golem_crafting MEASURED a 30-40% bare-click hijack
  rate from other players' models on a far quieter object; the light/fix rungs escalate to
  `click_menu_action` after a failed bare click for exactly that reason.
- **No `expected_action` hover gate on any world click.** `/hover` reports what is under the
  cursor RIGHT NOW and these clicks are issued while walking -- the gate withholds every one
  (the Gauntlet and Motherlode both paid for this).
- **Repairing a brazier does NOT light it.** Fix transitions BROKEN -> UNLIT; the wiki says a
  broken brazier "can then be relit after it is fixed". `fix_phase` therefore confirms on
  LEAVING "BROKEN", never on reaching "LIT" -- an earlier draft waited for "LIT" and ten
  successful repairs still spent the whole ceiling.
- **`bridge_events.Outcome` has no `.ok`** -- its fields are hit/signal/payload/confirmed/
  latency_s and `__bool__` returns `hit`. An earlier draft wrote `outcome.ok`, which crashes
  only on a SUCCESSFUL click and so hid from every test that mocked the click helper.
- **`_progress_signature` deliberately excludes warmth and energy**: warmth regenerates ~8%/min
  passively and energy falls because other players damage the boss, so either would defeat the
  stuck watch.
- **`before_break` paces its own loop** rather than relying on a phase handler to sleep, and
  stands the stuck watch down during `between_rounds`.

## Current state of routines/vale_totems.py

Written 2026-09-08 from a human recording (`recordings/2026-09-08_135457_vale-totem.*`, 8m22s,
194 actions, ~1.9 laps), the vendored m8aq `ValeTotems` reader, the Plugin Hub `totem-fletching`
source and the OSRS Wiki. Design: `docs/superpowers/specs/2026-09-08-vale-totems-design.md`,
whose "Live-verification checklist" is the first session's worklist. **NOT YET RUN LIVE** --
every fact below is either MEASURED from a cited source or the routine's own reasoning over the
reader's fields; nothing has been checked against a real client. This task (12) only wires the
Routine dropdown, adds the wood config item, and documents -- the routine itself (Tasks 1-11) was
already committed and fully unit-tested before this task started.

- **All state comes from one already-vendored reader, `GET /api/minigames/vale-totems`** -- no
  Java change needed for state, same "answers on the current build" free lunch the m8aq vendoring
  gave every other minigame reader. **The reader's helper METHODS are not in the JSON**:
  `isFullyCarved()`, `isFullyBuilt()`, `isFullyDecorated()`, `isOfferingCapped()`, `isStarted()`,
  `getMissingNearbySpirits()` and `getIncorrectCarvings()` are Java methods and Gson serializes
  fields only -- the same trap this file already records for GOTR's round phase and Wintertodt's
  `isOperational()`. `vale_totems.py` re-derives every one of them from the serialized fields
  (`baseValue`, `baseCarved`, `decorations`, `nearbySpiritValues`, `bottom`/`middle`/`top`, ...)
  rather than trusting a method Gson silently dropped.
- **THE 270-TITLE TRAP: the carve prompt and the fletch prompt are BOTH widget group 270**
  (`InterfaceID.Skillmulti`) -- the site's base loc cycles Build -> Carve -> Decorate on one
  object id, and the last two share both that object id AND this widget group, so neither the
  object nor the group can tell the two prompts apart. The only discriminator is the TITLE at
  **widget 270.5**: `"What animal would you like to carve?"` is the carve prompt (options A..E
  at 270.15-270.19 are the five spirits in a fixed order, so number key `n` carves the spirit
  whose `nearbyValue` is `n`, no name mapping needed); `"What would you like to make?"` is the
  fletch prompt. `skillmulti.py` (shared with `routines/fletching.py`) carries
  `TITLE_CARVE`/`TITLE_MAKE` for exactly this gate, and every number-key press is conditioned on
  it -- answering blind risks acting on whichever of the two prompts happens to be open.
- **The fletch prompt is answered with the derived bow-option number, NEVER `Space`.** `Space`
  repeats the *last crafted item*, which is only correct by accident when the previous fletch in
  the session happened to be the same bow -- a fresh login, or one manual fletch of something
  else first, makes `Space` silently produce the wrong item, and the failure then surfaces far
  from its cause, at the *next* totem, as "you don't have anything to decorate with". The bow
  index (`3` for a longbow, per the wood tables) is a property of the configured wood and is
  looked up, never guessed from what was last made.
- **`worldmap.py` has NO collision data for Varlamore.** MEASURED 2026-09-08:
  `reachable_from(radius=40)` returns a single tile at every totem site, at the bank, at Civitas
  illa Fortis, at Aldarin and at the Hunter guild, while the same call at Varrock square returns
  5089 tiles -- the EthanApi map this repo's routing runs on predates this content entirely, not
  a tuning gap. So the routine walks a **fixed table of click targets** (`vale_totem_route.py`
  -- USER's own ground markers since 2026-09-10, one click per stretch; before that the
  recording's tick-by-tick positions) rather than a computed route, and -- the direct
  consequence -- **it has no way to walk from an arbitrary tile back into the ring**, so it must
  be started standing in the valley; a player found outside safety-stops instead of trying to
  improvise a way home, the same rule `wintertodt.py` applies to its own region check.
- **The wood dropdown added this task is Java-only wiring** (`ValeTotemWood`, `valeTotemsWood`
  config item, `ConfigExporter.exportValeTotems`, and `ConfigProvider.getConfig()` -- the
  routine's `get_config(bridge_config)` reads `valeTotemsWood` straight off the bridge's generic
  `/config`, the same as `fletching.py`/`wintertodt.py`/`granite_3t.py` already do for their own
  shared keys, so `ConfigProvider` is what actually puts it on the wire and
  `ConfigExporter.exportValeTotems` exists only to keep the enum-NAME convention in one place
  beside every other routine's exported keys). Only Yew is measured against the recording; the
  dropdown lets the other four woods be selected but their bow option index and Fletching level
  are unverified past the wiki table `vale_totems.py.WOODS` was built from.
- **Nothing in this routine has been run live.** The spec's "Live-verification checklist" is the
  first session's worklist, in priority order: (1) the fletch prompt's title and that `3` selects
  a longbow; (2) the carve prompt's title, and that 270.5 is readable via `/widget` while it is
  open; (3) the waypoint hops clear both log balances (`56990` on leg 4->3, `56991` near site 7)
  -- the one place a recorded path might not survive being re-walked as discrete hops; (4) that
  `nearbySpirits` updates mid-carve and the re-read between carves picks the change up; (5) that
  bank-side `Fill` and field `Empty` on the log basket resolve to the right menu options; (6)
  whether a totem with durability left is ever met still standing and decorated, i.e. the
  claim-and-pass branch is exercised at all; (7) whether `3` holds as the bow index for any
  non-yew wood, if the dropdown is ever moved off Yew.

### First live session 2026-09-09 (VM, runs vt_live1 + vm_vale_totems_1900..1924)

The lap works: claim -> build -> carve x3 -> decorate x4, the recorded waypoints, the bank
trip and the fletch all ran unattended, and sites 1 and 8 were taken to built/carved/4
decorations. Checklist items 1, 2 and 5 are answered below; 3, 4, 6 and 7 are still open.
Four things were MEASURED, and the first is not about this routine at all:

- **A GUEST CONSOLE SITTING OVER THE 3D VIEWPORT SWALLOWS EVERY WORLD CLICK, AND
  `windowFocused` STAYS TRUE THROUGHOUT.** This cost most of the session and it will bite any
  routine run in the VM. A routine launched through `vmexec.py --interactive` runs as a
  scheduled task, which opens a console ON the guest desktop; when that console covers the
  viewport the client reports canvas mouse **(-1,-1)** for every point under it, so world
  clicks land on the console while INVENTORY and sidebar clicks (further right, uncovered)
  keep working perfectly. It read as `claim did not land` at site after site while the
  build/carve/decorate clicks on an object one tile away landed fine, and `/last-action`
  showed the claim resolving to `Walk here`.
  **The diagnostic is two lines and it is decisive**: move the cursor to an object's reported
  `screenX/screenY`, then compare `/state`'s own `mouseScreenX/mouseScreenY`. Equal means the
  client is seeing the cursor; **(-2,21)** -- canvas (-1,-1) converted to screen -- means
  something is covering the canvas there. With the client raised, the same pixel hovered
  `Claim`/`Offerings` exactly. **Focus is not the same question as z-order**: raising the
  client alone did NOT fix it (run 4), minimising the console did (runs 3 and 6), which is a
  clean A/B. `toolsm-guest-run-routine.cmd` now launches the routine with `start /min`, and
  `toolsm-guest-hide-console.ps1` minimises every console and re-raises the client.
- **The shut log basket has NO Empty option at all**, which closes CLAUDE.md's own open
  question ("whether a SHUT basket really offers Empty" -- it does not). MEASURED live: a
  right-click on `Log basket` (28140) offers `['Cancel','Examine','Destroy','Open','Use',
  'Check','Wear','Fill']`, and the cache agrees -- only `log_basket_open` (28142) carries
  Empty. So `empty_basket` now calls `open_basket` first, which is idempotent and skips when
  the basket is already open. Before this every field top-up failed with "No 'empty' entry in
  menu".
- **The basket's contents are COUNTED, because the lap check cannot see them.** MEASURED:
  holding 28 in the basket and 19 loose against a 30-log lap, the check counted only the loose
  19 and sent the routine 37 tiles back to the booth one site after it had banked there.
  `/api/storage/log-basket` cannot answer this (CLAUDE.md records why), so `basket_logs` is an
  ESTIMATE maintained by what fill and empty actually moved; unknown counts as empty, which
  banks one extra time rather than skipping a trip that was needed.
- **`BANK_SITES` is site 8 alone.** The booth is one hop from site 8's stand tile and ~37
  tiles from site 7's -- off the minimap, back along the leg just walked. The site-7 trip
  fired one site after a full bank at 8 and safety-stopped the run.
- **A bank trip stops as soon as the basket reports full**: a Fill that moves nothing draws
  "The basket is full." and the withdraw after it "You don't have enough inventory space", so
  rounds 2 and 3 were ~15s of nothing on a basket that was already full.
- **THE FLETCH FOLLOWS THE MOVE COMMAND, AND NOTHING MAY BE CLICKED UNTIL THE NEXT SITE.**
  USER: "can only fletch after we give the move command", then "between each totem we should
  be able to craft enough bows for the next one ... as ive shown in the recording". Both are
  in the recording, and the routine had both backwards: it fletched at the site (the next
  tick's walk click landed on an action that had only just begun), and once that was moved
  after the first hop, the hop every ~2s still ended every fletch before its first bow --
  anim 1248 started, no product, every leg, 0 bows across a whole lap, decorations 2/32.
  MEASURED from the recording: every human fletch run (5-9 bows, one every 1.8s) has ZERO
  clicks between the knife and the next site's action -- ONE "Walk here" on a tile up to 31
  tiles away, then knife -> logs -> Space, then hands off. So `walk_leg` now makes ONE
  shift-click in the viewport at the farthest point of the leg that is rendered (the next
  stand tile first, then the recorded waypoints from the far end -- the stand tile is 20-57
  tiles off and MEASURED not rendered at this camera, which is why a first draft aimed only
  there and fell straight back to the hops), fires the fletch, and only POLLS until that
  stretch is walked or stalls; the recorded hops carry whatever is left. RESULT: 21 bows in
  three minutes, the decorate clicks landing, `direct leg toward site N: one click at ...`
  in the log. The digit key works for the make prompt (Space was the human's choice; `3` is
  derived from the widget and starts the same action).
- **A leg is a CHAIN of far clicks, the minimap counts, and the fletch is restarted after
  each one and STOPPED at the build reserve.** USER: "hops still too short to craft bows",
  then "gotta leave a few logs in the inventory to actually craft the totems too". MEASURED:
  with the viewport alone the far click reached only 11-18 tiles on most legs (7-11s, 3-5
  bows) and the recorded hops that followed ended the fletch with no restart. Now
  `_far_click_target` takes the farthest point reachable by EITHER the viewport (rendered,
  not under a panel) or the minimap (`onMinimap`, not on an orb -- 28 tiles whatever the
  camera renders; site 4's whole 24-tile leg became one minimap click), `walk_leg` chains up
  to `DIRECT_LEG_MAX_STRETCHES` such clicks, the fletch hook fires after every one (it
  re-derives the budget live, so it restarts the fletch the click just ended or does nothing),
  and -- because the make prompt has NO quantity and a long stretch would eat every loose
  log -- `_direct_leg` watches the loose count and re-clicks the SAME walk target once the
  hook's reserve (`builds_needed`) is all that is left: the walk continues, the fletch ends,
  `fletch stopped at the build reserve (4 Yew logs left)`. A stall (a log balance the game's
  own pathfinder will not cross) hands the rest of the leg to the recorded hops. RESULT over
  five minutes: 28 bows, 6 totems erected, 18 decorations, 8 claims.
- **The basket is emptied below a totem's worth plus spare (`LOOSE_LOG_TARGET`, 9), not only
  at zero.** USER: "running with full log basket empty inventory? better to empty so we can
  craft some bows". A basket KNOWN empty is skipped; an unknown one is tried once.
- **Decorate is ONE click.** USER: "only need to click once to decorate i dont think it
  speeds it up", and the recording agrees: one Decorate, then four decoration animations
  1.2s apart with no further click. `decorate` now confirms on the count reaching what the
  bows in hand allow, budgeted per decoration, instead of one click per decoration landing on
  a loop already running.
- **A site click that resolved to the wrong action is retried ONCE through the menu**
  (`_click_site(option=...)`, `/last-action` as the tell). The bare click still misses the
  pile at sites 5-7 about one time in six ("Walk here"); the menu picks Claim by name.
- **The field Empty on the OPEN basket is SHIFT + left click.** MEASURED: the open basket's
  right-click menu in the field is `['Cancel','Examine','Destroy','Close','Use','Check',
  'Wear','Fill']` -- Empty is a SUBMENU option (cache `isubop3=1,Empty`) that `/menu` cannot
  list, a plain left click hovers Fill, and the profile's only basket swap
  (`menuentryswapper.ui_983043_28140=9`) is the BANK one. The recording shows it three
  times out of three: SHIFT down ~0.25s, left click on "Open log basket", logged as `CC_OP
  Empty` with the sub-option identifier 65540. `/hover` DOES read Empty with SHIFT held (the
  gate passed live), so `empty_basket` shift-clicks with `expected_action="Empty"` and
  falls back to one ungated shift-click judged only by the inventory delta. Note the empty
  only ever yields as many logs as there are free slots, so with 15+ bows in hand it hands
  over 4-8 logs -- enough for the next builds, and the reason the bank check counts the
  basket rather than the loose logs.
- **THE LEGS ARE STRAIGHT CLICKS AT THE TOTEM; USER'S GROUND MARKERS ARE A GUIDE (2026-09-10, eight
  live runs vt_markers1-8 on the HOST, a full lap in ~4.5 min with every site built, carved
  and decorated).** USER pasted two RuneLite ground-marker sets ("the most optimal path"):
  white 1-8 are the totem sites in walking order (their 1 is `SITES[7]`, same ring direction),
  cyan 1-20 one click per stretch, decoded as `x = (regionId >> 8) * 64 + regionX`,
  `y = (regionId & 0xFF) * 64 + regionY`. `LEGS` is that table, each leg ending ON the
  destination stand tile; the label arrows are the next click's heading (USER) and unused.
  Decisions, all USER's, in the order they were made: the "11 - BANK"/"DETOUR EXIT" markers
  (the Nemus Retreat **Bank buffalo**, loc 55199 at (1387,3310)) are NOT used -- "we have a
  log basket we can ignore the bank", banking stays at the Auburnvale booth from site 8; the
  two Log balances (56990 at (1401,3290), 56991 at (1453,3335), `op1=Walk-across`) were
  clicked for two runs and then REMOVED -- "adding the shortcuts introduces more fail vectors
  lets just remove them and click straight near the totems no small hops or shortcuts" --
  and MEASURED both crossings path fine without them (7->6 and 4->3 walked round the river);
  "they are guidelines not meant as a hop": `walk_leg` is a bounded chain
  (`DIRECT_LEG_MAX_STRETCHES`) of straight clicks whose candidates are the STAND TILE, then
  points every 3 tiles along the straight line toward it (`_line_candidates`, farthest that
  projects wins), then the markers still ahead (`_ahead`, the polyline projection), and only
  when nothing projects ONE minimap hop at the stand tile (travel pulls it in). A target the
  game would not walk to (2s stall) is banned PER TARGET for the session, never the leg.
  - **A far tile is refused by `isOnScreen`, not by the camera.** MEASURED: from the booth,
    site 7's stand tile 37 tiles out projected ABOVE the viewport's top edge at USER's pitch
    while the ground short of it was on screen -- `isOnScreen` is a bounds test on the real
    viewport. That is what the straight-line candidates answer. With the camera faced at
    the leg a viewport click reached 46-48 tiles on some legs.
  - **`Client.getCameraYaw()` is JAU14 (0..16383), 8 per 2048th** -- `face_toward` assumed
    0..2047 and every drag was aimed from a garbage angle (USER: "the camera implementation
    doesnt seem to work as intended"). Fixed in `controller._camera_yaw_units`; MEASURED
    live: a -352px drag gives +334 units, i.e. 0.95 units/px with a LEFTWARD drag INCREASING
    yaw, now the defaults (`CAMERA_UNITS_PER_PX_DEFAULT`, `CAMERA_DRAG_SIGN_DEFAULT`); a
    720-unit turn is two drags, on target. Every drag logs `[camera] drag Npx: yaw A -> B`.
    `KEEP_CAMERA = True` for this routine (USER: the login reset "puts camera entirely above
    us looking down meaning the camera skew wont do anything").
  - **The fletch/string fires on EVERY stretch of `HOOK_MIN_STRETCH_TILES` (12) or more and
    on the final one**, restarting after the click that ended it (USER, after a
    final-stretch-only draft left a 48-tile run idle: "no stringing on that long walk?").
    **The fletch is ended by STARTING THE SPOOL, not by a walk click** (USER, after the
    run: "use the spool to start stringing to interupt it instead ... no new walk click
    will be needed and it can be done mid walk"), at the build reserve OR once
    `STRING_ABOVE_BOWS` (8) unstrung bows are in hand; only with no usable spool does the
    old walk re-click run, and that one RE-PROJECTS the target tile (`_walk_point_now`):
    re-clicking the original PIXEL after six tiles of running mapped 17 tiles past site 5.
    Not yet run live in this form.
  - **The logs in hand decide the wood** (`_wood_override`): a restart wiped the bank trip's
    maple switch, the panel said YEW, and three sites were claimed and skipped for "Yew logs:
    0" with 16 Maple logs carried (USER: "its not built one in 3 hops"). Bank stock is the
    EXACT item name (`_exact_stock`): `bank_memory.quantity`'s substring match counted
    "Abyssal lantern (yew logs)" as one Yew log and the fallback never fired.
  - **Claim only a CAPPED pile** (`vt.OFFERING_CAP` 15,000, the reader's own; a build on a
    capped pile is refused) -- USER: "skip picking up vale offerings since they stack up on
    the floor and i can pick them later". **Carve is ONE click then the reopening prompt
    answered up to three times** (USER: "the menu will pop up after we choose one until we
    chose all three"); `before` is snapshotted BEFORE the key, the varbit moves fast. A
    failed site action gets ONE retry (`SITE_ACTION_RETRIES`) -- a single failed build had
    skipped a totem for a lap. **One outcome line per site**
    (`site N: build, carve, decorate -> Maple totem, 4/4 decorations; 9 Maple logs, 5 bows
    in hand`) -- USER: "Add a log for finishing totems too".
  - **The pre-hover TRACKS the totem until the player stops** (`_prehover_first_action`):
    at_site fires 7 tiles out, the hover flight ended with the player still running and
    the projection slid out from under the cursor (USER: "the click while moving often
    misses" -- "the cursor hover while approaching"); the cursor now follows the live
    projection whenever it has drifted `PREHOVER_TRACK_DRIFT_PX` until `/player.moving`
    is false. The site clicks themselves never missed in runs 6-8 (three object clicks
    for three actions at every site); what the eye saw was the parked hover.
  - **At site 8 with a trip due, the decorate click's confirm wait turns the camera toward
    the booth and the trip then CLICKS THE BOOTH DIRECTLY from the site** (~21 tiles; the
    game walks there and opens on arrival), the hop to the stand tile only as the fallback
    -- USER: "click the banker directly after the decoration is done. that way we save a
    bit of time not doing hop -> click bank". `banker.open_bank`'s own 12-tile "too far"
    guard is exactly that hop, which is why the routine opens the bank before calling it.
    Not yet run live in this form.
  - **The spool needs SPACE after the bow click, pressed blind** (the human's recording;
    Space with nothing open does nothing) -- twice the prompt was not detected inside the
    wait, nothing was pressed and the spool was written off on ONE strike; now
    `SPOOL_STRIKES` (2) and the confirm is 6s. With nothing worth fletching (all logs owed to
    builds) and unstrung bows in hand, the leg strings (USER: "nor is it stringing the bows").
- **AFTERNOON TUNING SESSION 2026-09-10 (host, runs vt_markers9-11, recorder `vt-markers9`): THE
  LAP IS AT HUMAN PARITY.** MEASURED run 9: 8 sites in 4m36s INCLUDING the bank trip, 33-39s per
  site against the human recording's 37.2s; site work 11-12s, legs 16-25s (game-bound: run
  energy 98% with run on), 4-6s from the last decoration to the leg's fletch. So "improving
  time" from here is the exceptions and the idle windows, not the cadence. Found and fixed:
  - **A CARVE NUMBER PRESSED WHILE THE PREVIOUS ANIMATION STILL PLAYS IS DROPPED.** Recorder
    signature: Carve click registers (dist 1), busy varbit 12393 goes 0 then 1 with NO carve
    chat/varbit behind it, and the prompt sits there -- the bot then spent CARVE_BUDGET_S and a
    fresh Carve click (6s, run 8 site 4). The HUMAN'S OWN recording has the identical signature
    twice (keys at +0.4s, nothing) and recovers both times by pressing the same number again
    ~1s later with no new click. `carve` now re-presses ONCE after `CARVE_KEY_REPRESS_S` (1.5s;
    every landed key in the recording carved within 0.6s). VERIFIED live run 9 site 8: dropped
    key recovered in 1.6s.
  - **AN INVENTORY `Empty` ENDS A RUNNING ACTION.** MEASURED (run 9, leg 4->3): strings landed
    at 39.2s and 40.4s, the basket Empty went out at 41.1s, the 41.6s string never came. So the
    basket top-up moved ONTO THE LEG (`_empty_on_the_move`): after the walk re-click at the
    reserve, and BEFORE the spool in the stringing branch when the basket is KNOWN to hold logs
    -- the Empty is then the interruption itself and the stringing runs untouched. An unknown
    basket (a restart) keeps spool-first. VERIFIED run 10 leg 6->5: `basket emptied on the move:
    14 Maple logs loose`, stringing after it, and site 5's first click 0.2s after arrival with
    no basket step.
  - **The camera turns toward the NEXT site during the decorate wait at every site**
    (`_decorate_camera_hook`, the booth turn generalized): the decorate is ~5s of idle hands and
    the leg had opened with two drags (~1.5s) at half the sites. VERIFIED run 10: drags during
    the decorations, 4/4 landed, the leg's first click needed none.
  - **Bow click -> Space is POLLED** (`_poll_prompt`, 0.15s on a ~50ms widgets dump). 19
    string starts measured 0.6-1.05s (human ~0.8s) except two at 1.75s, where the prompt's
    WIDGET_LOADED was missed and `await_pre` only re-read at its budget.
  - **`curl localhost` reads ~210ms on EVERY bridge endpoint on this host; `127.0.0.1` and
    Python's requests read 10-50ms.** It is the resolver, not the bridge -- time the bridge
    with 127.0.0.1 before concluding a read is slow.
  - The two unverified items from the morning -- the spool as the fletch's interruption and
    the direct booth click from site 8 -- both VERIFIED live in run 9 (bank open 11s after one
    click from the site, no hop).
  - **CAMERA PITCH DRIFTS WITH EVERY YAW DRAG, AND THE DRAG'S CURVE WAS THE CAUSE** (USER: "with
    enough camera moves our up and down would eventually end up in a weird place"; then "it
    happened again so the pitch is too far up"). MEASURED with /state sampled at 0.15s: a 360px
    yaw drag swung the pitch +-300 JAU14 mid-drag and netted ~+115; once the walk clicks landed
    on the viewport's top rows the drag started there, its bulge was clamped at the screen edge
    and single drags came back 500+ off (run 12: 3192 at 13:49 had become 3909 by 14:02). The
    human's own middle drags (recording `vale-totem-human-string`, 12 of them) wander 2-28px
    vertically on 89-360px, under 8% of their length; a reach's `HM2_CURVE_FRACTION` (0.35)
    bulges ~100px on the same drag, and on a middle drag every vertical pixel IS pitch. Fixes,
    all in controller/settings/human_move and so shared by every routine that turns the camera:
    `human_move.move(curve_fraction=)` (a per-move override, `CAMERA_DRAG_CURVE_FRACTION`
    0.04 for drags), `CAMERA_DRAG_MARGIN_Y_PX` (160) so a drag's bulge cannot reach the canvas
    edge, and a PITCH KEEPER in `face_toward` (`_hold_pitch`): after every yaw drag the pitch is
    pulled back to `input_state.camera_pitch_home` -- the pitch at the FIRST turn of the
    process, i.e. whatever the user set before launching (USER: "whatever i set the camera to
    now is the most ideal") -- with tight-aim vertical drags (`CAMERA_PITCH_AIM_RADIUS_PX` 2;
    the default 10px jitter on a 16px drag made the learned rate swing 4..9/px), the rate
    learned damped and bounded. VERIFIED run 13: a yaw drag kicked +124, one 16px drag put it
    within 4 of home. Set the camera by hand, then start the routine; a restart re-takes home.
  - **The same-id booth trap in `banker._refresh_bank_object`**: a counter is several booths
    with one id and the first id match in the scan was whichever came first, so bank rounds 2
    and 3 walked to a different booth (USER: "just choose the same one again"). It prefers the
    same TILE now. VERIFIED run 13: rounds 2 and 3 clicked the identical pixel.
  - **The first site click fires from the tracked hover within `EARLY_CLICK_TILES` (2) of the
    stand tile instead of waiting for the stop** (USER: "the hover actually feels a bit
    pointless unless we actually click with it"). A miss shows as `resolved to 'Walk here'`.
  - **A viewport far click needs `VIEWPORT_EDGE_MARGIN_PX` (24) inside the canvas**: a
    stand-line point projected on the canvas's top row passed isOnScreen and the click's jitter
    landed on the window frame (run 12, 6->5: player never moved, target banned for the session).
  - **The first carve key waits (<= 1s) for the player's animation to read -1**: across runs
    9-13 the dropped first keys and the landed ones overlapped completely in delay after the
    Carve click (0.42-0.56 vs 0.43-1.47s); every dropped one went out during the build's erect
    animation. The re-press stays as the belt.
  - **Typing to Claude with the game window focused reaches the game**: F-keys switch tabs,
    Escape closes the make menu, digits answer prompts. Three "make menu never opened" and a
    run of stalled walk clicks in run 11 line up exactly with keystrokes in the recorder.
  - **A click whose cursor is already on the target is made IN PLACE** (`controller.
    _cursor_already_on`, `CLICK_IN_PLACE_RADIUS_PX` 12 / `CLICK_IN_PLACE_HOVER_RADIUS_PX` 40
    with `/hover` naming the entity). USER: "the weird little flick if we basically have the
    mouse already over the target ... we don't really have to move if the interact is
    correct already" -- every pre-hovered click used to fly to a fresh aim point a few px away
    and back through the settle. Shared by every routine; a simulated miss keeps its flight.
  - Known and left alone: a lap that starts with too few logs reaches site 8 empty, the bank
    trip refills there, and site 8 stays unbuilt until the next lap (one lap in run 10, after a
    mid-lap restart). Steady state carries 52 logs for a 40-log lap, so it only follows a
    restart; walking back from the booth would cost ~17s.
- **MAGIC LOGS OUTLIVE THE LAP, SO THE ROUTINE NOW WAITS AT THE TOTEM (2026-09-11, NOT YET RUN
  LIVE).** USER: "Since we moved up to magic logs on vale totems the time they stay up has
  increased so we need to adapt so we dont just run around in circles". The arithmetic: ents
  move every 80 ticks (48s, wiki) and each visit costs one durability, so YEW's 5 visits are
  ~240s against the MEASURED ~276s lap (run 9, eight sites including the bank trip) -- the ring
  always came back to a collapsed totem and always had work. MAGIC's 6 are ~288s, so the ring
  arrives ~12s EARLY, finds nothing to click, and is not back for another 276s. **The cost is
  not the running, it is the dead ground behind us**: each site then spends most of the lap
  collapsed and earning nothing.
  - **The correction is made where the work is, not at the bank** (USER's choice: "hold at
    totem"). The eight sites run eight independent clocks; a pause at the booth shifts the whole
    lap uniformly and cannot align a site 40s from falling with one 5s from falling, while a
    wait at the site costs only the seconds that site is early by and delays every site behind
    it -- so the lap lengthens to match the decay on its own.
  - `vt.hold_due` (pure, table-tested) is the rule: finished, pile not capped, and
    `durabilityRemaining <= HOLD_DURABILITY` (1). `routines/vale_totems.py._hold_for_collapse`
    then polls `_site_now` every `HOLD_POLL_S` (1.2s) for up to `HOLD_MAX_S` (60s -- one ent
    cycle and a little, since the wiki says a visit only POSSIBLY fells the totem), rebuilds on
    the spot when it falls, and walks on otherwise. It sits ABOVE the bank check in `tick` so a
    totem that falls while we stand there is rebuilt before the trip's budget is computed.
  - **The wait is spent on the SPOOL, never on a fletch** (`_string_if_worthwhile`). A fletch
    started standing still has no walk click to end it -- the leg's own stop, `_direct_leg`'s
    reserve watch, is not running -- so it would fletch for the whole minute and turn every log
    a build is owed into a bow: the "plenty of bows, no logs" deadlock `vt.fletch_budget`
    exists to prevent. Stringing spends unstrung bows, which are logs already committed, so it
    needs no interrupt to stay safe.
  - `controller.fidget_once()` runs every poll: the engine's idle drift fires BETWEEN ticks and
    this holds one, and a cursor still for a minute is the signature CLAUDE.md's own stillness
    measurements name.
  - **Every site-outcome line now carries the durability** (`, N visits left`). It is the hold's
    own countdown and appears nowhere else, so a session of those lines is what
    `HOLD_DURABILITY`/`HOLD_MAX_S` get tuned against.
  - **A full ring with nothing to do is LOGGED, not acted on** (`_note_lap_progress`). It means
    every totem is outliving the lap by more than the one visit the hold waits for -- redwood,
    or a faster lap -- and the answer is to raise `HOLD_DURABILITY`, which is a decision about
    how long the routine should stand still and not one to guess at unattended. The line names
    the current per-site durabilities, which is exactly what the decision needs.
  - **FIRST LIVE RUN (vt_hold1, 2026-09-11, HOST) MEASURED TWO THINGS AND FOUND A THIRD BUG.**
    (1) A fresh MAGIC totem really does read 6, and (2) **THE DURABILITY DRAIN IS GLOBAL AND
    SYNCHRONISED** -- every built totem loses one at the SAME instant, every ~45s (sampled at
    10s: `10:26:21 3:6 4:6 5:5 6:4` -> `10:26:36 3:5 4:5 5:4 6:3`, drops at ~10:28:02,
    ~10:28:47, ~10:29:32). So the wiki's 80 ticks is a single world-wide clock; what staggers
    the COLLAPSES is when each totem was BUILT, i.e. the lap itself -- which is why the
    correction still has to be made per site. The lap came out at 289s against the 288s
    lifetime (site 6 built 10:24:42, rebuilt 10:29:31), so the hold was never reached in this
    run: it is a margin fix, not a per-lap one.
  - **THE BUG THAT RUN FOUND: `vt.fletch_budget` COUNTED THE INSTANT, SO A FULLY-STANDING RING
    FLETCHED NOTHING.** USER: "we didnt cut inbetween totems?" MEASURED: at 10:29:31, with
    every totem complete, `bows_needed` was 0 -> budget 0 -> `direct leg toward site 5` with no
    `fletch on the move`, and site 5 was then rebuilt **1/4 decorated holding 13 logs and 0
    bows**. The sampler puts site 5 at durability 1 at 10:29:22 and gone at 10:29:37, i.e. the
    global drop landed ~1s AFTER the budget was taken -- **a totem on its last visit is work
    that has not happened yet, and which side of a 45s tick a snapshot falls on is luck.** So
    both halves now count the LAP: `want` is `lap_decorations` (sites x 4) and the reserve is
    `lap_builds` (one per site), never `bows_needed`/`builds_needed`. The reserve had to move
    with it or the deadlock simply reverses -- a standing ring owes 0 builds, so every log
    becomes a bow and the ring collapses with nothing to build from. `_fletch_on_the_move`'s
    `LOG_RESERVE_KEY` (the stop `_direct_leg` watches) had the identical defect and takes
    `vt.lap_builds` too. **This is the same lesson `_bank_trip_due` already carries as
    `LAP_UNITS` (run 17)** -- it was fixed for the bank trip in 2026-09-10 and never for the
    fletch; magic logs made "all eight standing" the normal state and so made it bite every lap.
  - First live session's worklist: that `durabilityRemaining` really counts down one per ent
    visit on this account (the whole design rests on it), whether the hold usually fires at 1 or
    at 0, and whether 60s is generous or tight for a single ent cycle.
- Still unanswered from the spec's checklist: whether `nearbySpirits` really rotates mid-carve
  (item 4), the claim-and-pass branch (item 6), and any non-yew wood (item 7). Item 3 (the log
  balances) is now a designed crossing rather than an open question, unverified live.

### Pre-fletched decorations out of the bank (2026-09-16, NOT RUN LIVE)

USER: *"for vale totems i have about 1800 maple shortbows stocked up in the bank how do we code
it so we use them in each lap together with maple logs"*, and on the shape of it: *"its not a big
change its a addition to the bank stage where we first fill up the log basket but not the
inventory and fill inventory with bows. leaving 1-2 slots empty for logs"*.

- **A SHORTBOW DECORATES.** Wiki (`Maple totem`): "The totem can then be decorated with 4 of any
  Fletching product made of maple logs", and that page's Creation tabber lists **Maple shortbow
  (u)** and **Maple shortbow** beside the two longbows as interchangeable materials for the same
  40 offerings. The routine had only ever counted `<wood> longbow`/`longbow (u)`, so a bank full
  of shortbows was invisible to `bow_count`, to the keep list and to `vt.next_action`.
  `WOODS[wood]["decor"]` is now all four variants (`_decor_variants`), and `bow_count`,
  `_keep_substrings` and `_foreign_bows` all read that list. The knife and the spool keep the
  narrow `_bows_for` pair -- what we MAKE is still the longbow; what DECORATES is wider.
- **The bank stage is the only behaviour change.** The basket only ever holds logs, so logs and
  bows never compete for a slot: the log rounds stop one short (`rounds = BANK_ROUNDS - 1`, since
  that last round exists only to fill the HANDS with logs) and every round that does run fills
  the basket, then `_load_decorations` deposits the loose logs, withdraws `BANK_LOOSE_LOGS` (2)
  back, and shift-withdraws the bows into every remaining slot. Leaving the booth: ~28 logs in
  the basket, ~23 bows and 2 logs in hand.
- **The order is the safety argument.** The deposit HAS to come first -- there is no free slot
  for a bow until it does -- so a withdraw that then failed would leave the lap holding neither
  logs nor bows. Hence the logs are pulled straight back on a failed withdraw and the trip ends
  exactly as it used to, and the 2 loose logs come back BEFORE the bows so the next site's build
  does not depend on the leg's first basket `Empty` having landed.
- **The variant is chosen by EXACT name off the bank memory** (`_banked_decor_name`), asked once
  per trip at the open bank after the junk deposit. `"Maple shortbow"` is a substring of `"Maple
  shortbow (u)"`, so a substring count reports one stack under both names and withdraws whichever
  sorted first -- the same trap `_step_wood_down_if_bank_is_out` already carries for "Abyssal
  lantern (yew logs)". A memory that has never seen a bank falls back to the open interface;
  fewer than `MIN_BANKED_BOWS` (8) banked means no withdraw and the old trip.
- **The fletch is deliberately left ON.** The wiki's strategy page says the same thing: "When
  using pre-fletched decorations, the player will still have to fletch logs from the log basket
  if they want to do eight totems in a single run without banking." `vt.fletch_budget` needed no
  change -- it counts the bows in hand and simply asks for fewer. TRACED against the constants:
  the early legs fletch nothing (the loose count sits below the 8-log build reserve), and from
  ~site 4 the bows have freed enough slots that the basket tops up to 11-19 loose logs and the
  fletch covers the tail of the lap. A lap costs ~17 logs instead of ~40, and ~23 of the 32
  decorations come out of the bank -- 1800 bows is ~78 laps, ~5-6 hours.
- **What a live session has to check**: (1) the shift-withdraw on a 1800-stack of unstackables
  really fills the free slots (it is the same click the log withdraw already relies on); (2) the
  loose-log deposit is one shift-click and lands; (3) the lap really does finish all eight sites
  -- watch the `N bows in hand` in each site-outcome line, and a site leaving with fewer than 4
  decorations is the signal to raise `BANK_LOOSE_LOGS` or lower the bow count; (4) that the
  Fletching XP drop is what the user wants (the early legs now walk with idle hands, since
  neither the fletch budget nor the spool has anything to do while the hands are full of bows).
- **Not done, deliberately**: the spool still strings only longbows, so banked *unstrung*
  shortbows are spent as they are rather than strung mid-walk for the extra XP. That would need
  the make-menu option for `<wood> shortbow` and a bow-string stock, and it is not part of the
  bank stage.

## Current state of routines/granite_3t.py

**REWRITTEN 2026-09-09 FROM USER'S OWN RECORDINGS, AND THE CYCLE IS SETTLED. Everything below
this block that disagrees with it is history.** USER: "Opus has tried his best to make 3t mining
work but it looks like a mess" -- and the mess was three wrong readings of the tick data stacked
on each other (CYCLE_TICKS 3 at phase 25 -> "the fletch cancels the mine" -> a second rock
click -> CYCLE_TICKS 4). Three recordings settle it (`recordings/2026-09-09_081631_simple3t`,
`_082746_simple3t-take2`, `_085109_simple3t-1rock`; USER's caveat: "my recordings are not
great advice for 3t since it requires a lot of practice", so the TICK facts are taken from
them and the cadence is not). `ControllerAddon/tick_report.py <jsonl>` is the analysis, and
the routine's own per-cycle outcome lines are the same numbers live.

- **THE RECORDER'S TICK LABELS ARE ONE LOW ON EVERY OUTCOME RECORD.** An xp/chat/anim record is
  written during packet processing, ~20ms BEFORE the GameTick that bumps the label; the
  `tick` records' own timestamps are the boundaries. Every tick number below is re-derived
  that way. Read a recording's labels naively and every conclusion about "which tick" is wrong.
- **A knife-on-log clicked during client tick N pays its Mining xp with the packet that opens
  tick N+3** (six same-tick cycles in take 2, every one). So the NEXT fletch belongs in tick
  N+3, after that packet -- `CYCLE_TICKS = 3`. Take 1 clicked every fletch in N+4 (USER: "my 3
  tick is a bit too slow"), and the previous commit fit `CYCLE_TICKS = 4` to that.
- **THE MINE CLICK MUST LAND IN THE SAME CLIENT TICK AS THE FLETCH.** Every same-tick pair paid
  at 3; every pair whose Mine slipped into the next tick paid at 4 (two in take 2, both cycles
  of the 1-rock recording: fletch at phase 520-540, Mine at phase 80-160 of the next tick).
  A late rock click is a 4-tick cycle, never a lost log -- so **there is no abandon/late branch
  any more**: the rock is clicked regardless.
- **Why the 2026-09-08 CYCLE_TICKS=3 attempt ate logs**, two ways. (1) It clicked the log 25ms
  after the bridge's GameTick anchor, inside the model's jitter; a fletch processed one tick
  EARLY lands while the mine's timer is still positive (wiki: the timer is set "if the timer is
  not positive"), cancels the mine and completes the fletch -- the log becomes a Teak stock.
  `fletchPhaseMs` is now drawn from 150-320. (2) When the rock did not resolve it "consumed the
  knife selection" by clicking the log: a fletch with no mine behind it. The bot's own recording
  (`2026-09-08_222357_granite3t-diag`) shows runs of those two ticks apart. `prepare` now runs
  only AFTER the rock has resolved, so there is never a selection to consume.
- **One Mine click, one flight.** The wiki's "clicking the rock again" is the drop-between-mines
  variant; a second click cancels the mine it just started (28 Mine clicks : 26 fletches in
  take 1, once the human's re-clicks are read).
- **The step between rocks is about YIELD, not about the cycle working**: a cardinally
  adjacent rock also paid at 3 (take 2, tick 72160). The western-line "must step 2 tiles"
  config is gone; Cape Conch is the eastern 2x2 in USER's own row-alternating order.
- **Press vs release (old checklist item 6), ANSWERED from the same recordings:** a WORLD click
  fires its menu action 1-23ms after mouse DOWN; an INVENTORY click ~10ms after mouse UP (drag
  detection). So the rock click only has to be pressed inside the tick and the geometry budget
  books nothing for it; `Window.room_for` and the landing constants are deleted.
- **Sites (`SITES`, `cfg["site"]`, default `gem_mine`).** USER 2026-09-09: "swapping place to
  gem mine since it will make us have to drop less", plus the JCW guide transcript. The
  underground Shilo gem mine (region 11410, ids 11380/11381, 48 rocks, 59.4s respawn) cannot
  run a fixed rotation, so `rockTiles: []` selects the DYNAMIC one: nearest live rock from a
  `/objects` scan each cycle, the just-mined tile excluded so every cycle steps; a rock further
  than `stepMaxTiles` (2) goes through `far_rock_cycle` -- click it, select the knife and park
  while walking, poll the true tile, fletch ONE STEP OUT and re-click the rock in that same
  tick (the guide's rule, verbatim). A full inventory there is `fullInventory: "deposit"`: walk
  to the mine's Bank Deposit Chest (loc 10530 at (2842,9382)), one Deposit-All per stack (never
  "Deposit inventory", which would bank the knife, log and gem bag), Empty the gem bag, ESC,
  resume. No bank there, so a lost log is a safety stop; the guide's adamant-pickaxe advice
  (its 4-tick roll lets a broken cycle right itself) is USER's to follow. The guide's ground
  markers (region 11410, pasted 2026-09-09) are the human's lap and are NOT used yet -- greedy
  nearest-rock roams instead; wire them in only if it corners itself live.
- **NOT RUN LIVE in this form.** First session: read the `paid N ticks` lines and the every-25
  summary; expect 3 with the occasional 4. If 4 dominates, the fletch phase is too late for the
  flight; if logs vanish, the phase is too early or the rock did not resolve; if nothing pays,
  read the recorder with `tick_report.py` before touching a constant.


**The design-era notes below predate the 2026-09-09 rewrite; read them for the measurements
(reach model, click costs, the probe, the startup gates), not for the cycle.**

Written 2026-09-08 from the OSRS Wiki (Tick manipulation rev 15292712, Pay-to-play Mining
training rev 15297907) and this repo's own fitted mouse-movement model (`settings.py`'s HM2
reach/hold figures). Design: `docs/superpowers/specs/2026-09-08-3-tick-granite-mining-design.md`,
whose "Live-verification checklist" is the first session's worklist. **NOT YET RUN LIVE** --
every number below is either MEASURED from a cited source or a first-draft guess; nothing has
been checked against a real client. No Java is needed to run it from the command line; this task
only wires it into the panel dropdown.

- **This is a schedule, not a reaction.** Every other routine in this repo clicks and then waits
  on a pushed signal -- the tick boundary is where a click LANDS. Here two clicks must fall
  inside one named 600ms tick, which is why this needed a new scheduler
  (`ControllerAddon/tick_cycle.py`) rather than another `gathering.Profile`.
- **The cycle is two dead ticks then one critical tick, AND IT IS COUNTED IN TICKS.** The tick
  after the critical one clicks the knife (a client-side selection that sends no packet at all
  -- the packet is sent by the click on the TARGET) and flies to the log, parking the cursor
  there; the tick after that does nothing, the cursor just rests on the log; then the next
  critical tick clicks `click_human` **IN PLACE** on the log (no flight) to fire the 3-tick
  action, then makes ONE short flight to the next rock and clicks it. Two clicks, one flight,
  in the critical tick -- that asymmetry is the whole reason it fits in 600ms. The next rock's
  screen point is re-resolved over HTTP during the dead ticks (the player moves between rocks
  each cycle, so the projection changes), never during the critical tick.
- **`run_cycle` records the bridge TICK NUMBER its critical click landed on and the next cycle
  targets `that + CYCLE_TICKS` (3) through `tick_cycle.at_tick`.** The first draft used
  `at_phase`, which sleeps to the NEXT boundary whenever it is called -- so nothing pinned the
  period at all: it was however long `prepare()`'s knife flight, log flight and HTTP read
  happened to take, rounded up, and HTTP latency flipped it cycle to cycle. A fast prepare gives
  a 2-TICK cycle, which re-fires knife-on-log before the skilling timer has expired and yields
  nothing. `at_tick` answers `"missed"` when its target tick has already passed, and the routine
  treats that as an ordinary desync -- firing late is worse than not firing, because a click in
  the wrong tick desyncs every cycle after it. `at_phase` survives for the FIRST cycle of a
  chain, which has no previous critical tick to count from. **`at_phase` PUMPS THE STREAM AGAIN
  AFTER ITS SLEEP**, because that sleep crosses a boundary by construction: pumping only before
  it left `events.ticks.tick` holding the tick we LEFT, so `_open_window` recorded a tick one
  low and pinned the next cycle at +2 rather than +3 -- the exact defect the tick pin exists to
  remove, re-entered after every chain start, every desync and every deliberate miss.
- **THE ORDER WITHIN THE CRITICAL TICK IS FIXED -- log click, then rock click -- AND THERE IS NO
  CONFIG KEY FOR IT.** A `rockClickTickOffset` of -1 (rock click leading the log click) was
  built and then DELETED: it cannot work while a knife selection is live. An OSRS item selection
  is consumed by the next click on ANY target, so clicking the rock first issues "Use Knife ->
  Granite rock" (which does nothing), spends the selection, and leaves the following in-place
  log click a bare left-click with nothing selected -- knife-on-log never fires either, the
  cycle yields nothing, and it is recorded `"ok"`. The routine's own `_consume_knife_selection`
  docstring already stated the rule, and the surviving order is deliberately the one where the
  log click spends the selection before the rock is clicked. If a live session shows the
  ordering is wrong, the fix is to restructure `prepare()` so the knife is selected AFTER the
  rock click -- a design change, not a config flip.
- **MEASURED from `settings.py`:** a reach is 170ms median at 52px, +120.5ms per doubling of
  distance, floor 90ms; a click hold is 50-145ms (median 85) followed by a 30-90ms trailing
  `human_delay`. **A CLICK'S WALL-CLOCK OCCUPANCY OF THE TICK IS THEREFORE ~145ms MEDIAN AND
  ~235ms WORST CASE, NOT 92ms.** The 92ms this file records for giants_foundry is
  reading-to-click-LANDING (roughly to mouse-DOWN), which is a different quantity, and the
  spec's original budget line (`60 + 92 + 270 + 145 = 567`) charged the click cost two different
  ways in one sum. Honestly: the critical tick costs **~520ms median** (60 phase + 145 log click
  + 170 flight + 145 rock click) and **~730ms at p90**, so the median cycle fits in 600ms and
  the p90 cycle DOES NOT -- `Window.room_for` is expected to abandon some cycles, and
  `tick_cycle.CLICK_COST_MS` books the 235ms worst case rather than the median because a
  conservative guard costs one cycle while an optimistic one desyncs the chain. **Whether an
  over-budget RELEASE even matters is UNKNOWN** -- if the game acts on mouse-down only the press
  has to land in the tick -- and that is now item 6 of the spec's live-verification checklist.
- **`controller.click_entity` cannot be used inside the critical tick.** With a `refresh_target`
  it reads the bridge at departure, once per leg, and again at the settle -- exactly the
  "~1s per click" cost CLAUDE.md already records for deep-sea-trawling, which is the whole tick
  budget on its own. `tick_cycle.timed_click` exists to be HTTP-free: it moves to a pre-resolved
  `(x, y)` and clicks with no bridge call anywhere in the path.
- **`ctx.events` is a HARD REQUIREMENT here, and that INVERTS the repo-wide rule that every
  consumer must tolerate `ctx.events is None` and keep a working fallback.** Without the tick
  clock there is no tick phase, and without the phase this routine silently degrades into an
  ordinary 5-tick miner with extra steps -- indistinguishable in the log from a working run.
  Startup refuses to start when `ctx.events` is `None` or its tick model is stale, rather than
  falling back to polling. **Do not "fix" this into a polling fallback** -- there is no
  HTTP-cheap way to hit a 600ms window without the pushed tick clock, so a fallback here would
  just be the bug this design exists to avoid, wearing a different hat.
- **Startup also refuses to run unless the click geometry fits**, reporting the measured pixel
  distance between the log and each rock either way (`maxReachPx`, 220 first-draft), so a refusal
  tells the user how far to zoom in rather than failing silently. The wiki gives humans the same
  advice for this method: small client, zoomed in, camera south, to keep inventory-to-rock
  distance as short as possible.
- **A desync never stops the run** (USER's decision: resync and carry on, no ceiling on
  desyncs) -- an ordinary missed tick or a cycle that yielded nothing is not itself an error,
  since granite's second roll is a bonus, not a guarantee. A desync does DROP THE PIN, so the
  next cycle re-enters through `at_phase` on a clean boundary instead of inheriting a period
  that is now wrong. **The XP stall is the routine's one BELT, but it is NOT the only terminal
  condition** -- an earlier version of this section said it was, and that was simply false
  (there are seven `trigger_safety_stop` sites). The run stops on: no Mining xp for
  `xpStallStopS` (60s); a full inventory whose drop freed nothing, or whose every item is on the
  keep list; the knife or the log gone from a NON-empty inventory; `/inventory` reading empty 20
  ticks running; no `rockTiles` configured; and either a startup assertion failing once it is
  genuinely answerable (geometry out of budget, a rock that will not resolve, more than one log,
  an inventory holding something the drop would bin) or still being unanswerable after
  `STARTUP_DEFER_MAX_S`. A perfectly-aimed click into the wrong tick looks identical in the log
  to a good one, which is why the XP stall is the belt it is.
- **The XP stall clock only counts time during which `tick()` was actually running.** It is
  wall-clock and breaks are ON by default (20-40 min apart, 2-8 min long), during which `tick()`
  is not called at all -- so the first check after a break computed a several-hundred-second
  stall and stopped a perfectly healthy run with a message that was false. The same shape fires
  after an F2 pause, after any unfocus over 60s, and after `login.py`'s reconnect wait, all of
  which this codebase's standing rule says a routine must ride out. A gap over
  `TICK_GAP_RESEED_S` (5s) between `tick()` calls re-seeds the clock and logs one line;
  wintertodt stands its own watch down between rounds for the same reason.
- **The startup assertions run from `tick()`, DEFERRED, because `setup()` runs before the
  engine's login and focus gates** (VERIFIED in `controller.py`: `routine.setup(ctx)` at 3539,
  `login.is_fully_logged_in()` at 3552, `windowFocused` at 3558). At the login screen
  `get_inventory()` returns `[]` and no TICK packet has arrived, so BOTH assertions would fail
  and the run would be permanently safety-stopped before it began -- the GOTR run-47 bite this
  file already records. An empty inventory or a stale clock means "cannot tell yet" and defers
  quietly; `STARTUP_DEFER_MAX_S` (60s) turns a still-unanswerable check into a stop that names
  what could not be read.
- **The drop keeps `keepItems` (default `["pickaxe"]`) as well as the knife and the log, AND
  startup refuses an inventory holding anything else.** The keep list alone is not enough: it
  only knows the names it was told, and nothing here checks whether the pickaxe is worn or
  carried, so a carried one would be shift-dropped on the floor of a busy mine about a minute
  in, mining would then earn nothing, and the stall watch would stop the run 60s later blaming
  contested rocks. The startup refusal NAMES the offending items rather than binning them.
- **The chain gate is once per chain, never per cycle** (it costs an HTTP read and belongs
  outside the critical tick): `controller.open_inventory_tab` -- the gf_live27 lesson, a
  pause/resume can leave the stats tab up -- plus a `/hover` read over the knife proving its
  left click really is a Use. A Menu Entry Swapper entry making it "Drop" would bin the knife on
  cycle 1 and every cycle after would look perfect in the log, so unlike
  `controller.hover_matches` this one fails CLOSED: an unreadable hover refuses the chain.
- **`rockTiles`/`rockId` have NO config surface** -- this routine reads `DEFAULT_CONFIG` only,
  so the panel's dropdown entry ships with an empty rotation and selecting it stops immediately
  with a clear message. Capture the tiles with `python routines/granite_3t.py --probe` at the
  mine and paste them into `DEFAULT_CONFIG` in the Python file before a first run.
- **`tick_bench.py` is the go/no-go gate, it has NOT been run, and THE CORRECTED ARITHMETIC
  ALREADY FAILS ITS OWN OLD GATE.** The honest budget (see the spec's table) is **~520ms median
  and ~730ms p90** against a 600ms tick, not the ~347ms an earlier draft claimed by charging the
  click cost two different ways in one sum. The original rule -- "p90 over ~540ms means use the
  4-tick celastrus variant" -- was written against that wrong number, and the new p90 blows
  through it. Do not read that as "the design is dead", and do not read it as "the gate is
  obsolete" either. What it actually means:
  - the MEDIAN cycle fits, so the method works most of the time;
  - the p90 cycle does not, and `Window.room_for` is built to ABANDON those rather than let a
    click cross the boundary -- so the predicted cost is roughly a tenth of cycles abandoned and
    resynced, i.e. a rate hit, not a broken mechanic;
  - **the whole shortfall may be an artefact of measuring the wrong thing.** ~235ms of every
    cycle's booked budget is the click HOLD plus its trailing delay, which only counts if the
    game acts on mouse-RELEASE. If it acts on mouse-DOWN, the real occupancy is ~285ms median /
    ~430ms p90 and everything fits comfortably. That question is live-checklist item 6 and it is
    now the single highest-value thing the first session can answer.
  So: run the bench, and judge it against MEASURED occupancy rather than against the stale 540
  figure. If the bench's p90 is near or above 600ms **and** the game turns out to act on
  release, the 4-tick celastrus variant (2400ms per cycle) is the honest fallback -- and
  `tick_cycle.py` carries over to it unchanged, since the scheduler is cycle-length-agnostic.
- **Both once-per-chain gates (the inventory tab, the knife hover) are COUNTED and terminal**
  (`INVENTORY_TAB_STOP` / `KNIFE_HOVER_STOP`, 15 each, same shape as `EMPTY_INVENTORY_STOP`).
  They sit BEFORE `_stall_check` and return from `tick()` without running a cycle, so a gate
  that never passes was a silent forever-loop with one debug line per engine iteration and no
  terminal condition at all -- the XP stall watch is never reached to notice that no xp is
  arriving. Neither failure is exotic: `open_inventory_tab` reads `/state`'s `inventoryTabOpen`,
  which is None -> False on any bridge error, and `controller.get_hover()` returns None on a
  bridge error AND on any client built without `MenuStateProvider`, so **against an un-rebuilt
  client the hover gate failing is the DEFAULT outcome** -- and this routine needs a rebuild
  anyway for its dropdown entry.
- See the spec's live-verification checklist for the full first-session worklist, in priority
  order.

## Current state of routines/mining_guild_iron.py

Written 2026-09-11 inline (USER: "Just implement it inline its very simple routine") from the
user's recording `recordings/2026-09-11_121030_mining-guild-single-tile.*` (2m56s, 40 Mine
clicks, 72 ores, 4 deposits), then **live-debugged the same day over seven runs (mgi_live1-7,
host) to 1674 ore/hr against the recording's 1473**. The panel entry needs a client rebuild;
the command line runs today. It is deliberately NOT a `gathering.Profile`: gathering re-picks
from tick-cached bulk scans, pre-hovers only after a stillness check and budgets for walking
to far rocks, none of which this activity has.

- **Shape**: stand on ONE tile (config `standTile`, default the recording's (3021,9721)) with
  three iron rocks cardinally adjacent (N (3021,9722) id 11365, W (3020,9721) 11364, S
  (3021,9720) 11365; spent = 11390/11391 "Rocks"). `discover_rocks` reads them from one bulk
  scan; after that every "is it up" is one uncached `/object/at` with the rock's live id.
- **Every rock is exactly two ores, the second depletes it** (MEASURED: click -> anim 624
  +0.3s -> ore +1.5s -> ore +3.3s -> spent). The Mining gloves alternate strictly ("will not
  save the next iron rocks" on exactly 36 of 72 successes), so the swap trigger is the
  DEPLETION, never a count: `mine_rock` awaits `XP_GAINED`/`OBJECT_DESPAWNED`/
  `INVENTORY_CHANGED`, confirmed by `/object/at` gone (an ore with the rock standing re-arms
  `ORE_BUDGET_S` 5.5). No tick reads anywhere. **MEASURED live: rock-to-rock is 3.60s
  EXACTLY -- six ticks, tick-quantized -- in every run**, so the reaction draw
  (`SWAP_REACTION_S` 0.10-0.30, human 0.12-0.55) only has to stay inside the tick and cannot
  be traded for speed. The per-rock rate is the game's floor for this method.
- **THE PRE-HOVER MUST CARRY A SCREEN POSITION.** Run 1 handed `hover_entity` the bare
  `{x, y, plane, id}` record; it silently no-oped and every swap click flew. `_park_on` resolves
  the next rock through `/object/at`, using the SPENT id when it is down (it usually is: a rock
  respawns 2.4s into the 3.6s cycle), and logs `pre-hover ->`. Every swap click now logs
  `in place (cursor already on it)`.
- **NO SIMULATED MISCLICK ON A ROCK CLICK** (run 6): a miss "beside" a rock is a Walk-here
  onto the neighbouring tile, the player ended on (3020,9722) two tiles from the south rock,
  and every click on it then "did not take" for the rest of the chain -- the failure counter
  never reached its ceiling because the two adjacent rocks kept depleting in between. A
  "failed" click (no swing, no ore for `CLICK_GRACE_S` 1.2s) also re-checks the stand tile and
  walks back. Ten consecutive failures is the safety stop.
- **The deposit trip is at human parity (13.2s vs the recording's 13.2s), after four cuts:**
  (1) `CHAIN_MAX_S` 60 -> 240: every chain hand-back cost 1.0-1.5s before the next click;
  (2) the ORE is parked under the cursor before the rock that fills the inventory
  (`ORES_PER_ROCK`); (3) **the Use is ARMED before the depletion** -- after the trigger rock's
  first ore, Use is selected on the ore and the cursor parked on the box (a selection is
  client-side and sends no packet, the same fact granite_3t's knife rests on), so the depletion
  is answered with ONE in-place box click 0.3s later, where run 2 paid a 0.4s flight to the
  inventory and a 1.0s flight to the box; (4) the stand tile is tracked under the cursor
  during the deposit walk (`_track_hover`, `TRACK_DRIFT_PX` 6 -- it must stay under
  `CLICK_IN_PLACE_RADIUS_PX` with both jitters) and the first rock through arrival, since the
  camera keeps gliding after the last step. Deposit = Use ore on Bank Deposit Box 10529 at
  (3013,9720), 8 tiles; the game walks and deposits every ore (~4.5s), sack and minerals stay.
  Trigger: below 2 free slots (USER's choice) or the "inventory is too full" chat.
- **The walk is ~11 tiles each way, not 8**: the rocks block the diagonal into the stand tile,
  so the game paths round via y=9719, and the human's 3.8s walk is the same path at run speed.
  Not shrinkable. USER declined the human's one-tile-early rock click (~0.9s/trip): the game
  can mine from the neighbouring tile it stops on, and then only one rock is adjacent.
- **The arrival click waits for the camera glide to settle** (`_await_settled`, two
  projection reads within 3px, <= 0.8s). Run 7: a click made during the glide landed 35px
  from where the rock was drawn, resolved to a Walk-here beyond it, and the player walked off
  round the rocks twice (10s each).
- **Run 8 (52 min unattended, 1324 ore, 1523/hr, 1 failed click, scheduled break at 45 min
  taken cleanly)**: 26% of rocks took 7 ticks instead of 6 with the reaction unchanged
  (median 0.32s) -- the depletion event was arriving later in the server tick that session
  and the click crossed the boundary. The only lever is a shorter `SWAP_REACTION_S`; USER's
  call, since it goes under the recording's fastest 0.12s. A random event (Freaky Forester)
  spawned during the break and was dismissed by hand; nothing here handles them.
- Tests (`tests/test_mining_guild_iron.py`) drive a REAL `EventStream` through a scripted
  `poll_udp` whose rock script is fed by the click itself (a fixed batch order desynced the
  moment the pick order differed -- feed each event from the click that causes it).

## Current state of routines/blast_furnace.py

**Live gold session 2026-09-04 (bf_live3-9), the first routine converted to event-driven
outcomes (`docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md`) and measured
against its own 2026-08-31 baseline of ~27-30s per 27-bar gold trip. Now 24-25s; what is left
is game-bound (smelt ~9-11s after the belt click on a shared mass-world furnace, ~6.6s for
the ore to go onto the belt, the runs). Everything below was MEASURED that morning.**

- **Conveyor: click the tile that OFFERS `Put-ore-on` (id 9100).** The multi-tile 9101 entries
  report no actions; the old north-of-nearest heuristic picked one from a different standing
  spot and six clicks resolved to "Walk here" (0/27, safety stop). A refused click (aim behind
  the sidebar after the bank camera rotation) is re-resolved up to `CONVEYOR_CLICK_TRIES`.
- **Bank-interface clicks were being refused since the 2026-09-02 blocker rects**: the
  deposit-all button and the shift-deposit on items inside the bank's side panel hit the
  UI-blocker check and rotated the camera instead. Every bank-interface click in `banker.py`
  now passes `check_ui_blockers=False` (the interface-click rule). This is also how the
  Goldsmith gauntlets got banked that morning (the swap read inactive because the account's
  ice pair is `Smiths gloves (i)`, cache obj 27031 -- now one of `ICE_GLOVE_NAMES` -- and the
  glove names are on `keepItems` for EVERY gold trip, swap active or not).
- **The dispenser Take opens widget group 270** (the skill-select "How many would you like to
  take?" prompt, same interface as the Giants' Foundry crucible) ~1.5s after the click; Space
  answers it; group 229 "You take 27 gold bars ... Click here to continue" follows and needs
  no dismissal. NEITHER `/state.dialogOpen` NOR the m8aq dialogue reader sees 270 -- `/widget`
  (270.5 clickable) does. Varbit 10670 `chatmodal_unclamp` flips to 1 when a modal opens but
  STAYS 1 afterwards (read 1 for 5s+ with nothing on screen), so it only counts as a
  TRANSITION since the click. Before this, each collect paid ~4s in the settle/idle fallback.
- Bars-ready rides the pushed Smithing `XP_GAINED` (and the subscribed dispenser varbit);
  glove wears confirm on `INVENTORY_CHANGED` + the equipment slot; the bank opens on
  `WIDGET_LOADED` 12 (2.0s click-to-deposit, was 4.2s); the belt deposit on inventory deltas.
- **Two user ideas, both live:** the ice pair is PRE-HOVERED during the smelt (wear 0.2s after
  the XP drop, was 1.2s), and the gauntlets go back on DURING the run to the bank -- bank
  click first, wear mid-run, `banker.open_bank`'s own first click re-queues the open
  (`_swap_back_on_the_run`; `collect_bars(swap_back=False)` from `run_trip`).
- Every wait runs on `bf._now/_sleep`; the test fixture installs a fake clock, so budgets
  expire instantly in tests.
- **Adamantite the same morning (bf_live13-15): a 2-trip cycle (54 coal, then 27 coal + 27
  ore with the collect) in ~48s per 27 bars.** Three more facts: (1) `/bank` is TICK-CACHED
  and the deposit before a withdrawal can add a new item type to the bank, shifting every
  later slot -- a stale snapshot aimed the shift-click one slot off and withdrew ALL 11
  Mithril chainbodies; `banker` withdrawals now always read fresh and retry once. (2) The
  non-gold collect used to "check the dispenser immediately, collect next trip" -- an
  adamantite batch was only picked up two trips later; every bar type now waits for the
  bars (pushed varbit/XP) and collects in the same trip, and by the time the bag's coal is
  on the belt the ore's smelt is usually already done (bars ready "after 0.00s"). (3) The
  routine follows the panel's Bar Type LIVE, per tick -- a dropdown passing through
  "mithril" on the way to "adamantite" ran one mithril trip. Glove wears and container
  (coal bag) clicks never simulate a misclick: a miss + corrective reclick is two wears /
  a deposit of the neighbouring item with the bank open.

## Current state of routines/giants_foundry.py

Written 2026-08-31 from a human recording (`recordings/2026-08-31_003238_giants-foundry.*`)
and the easy-giantsfoundry Plugin Hub source; design
`docs/superpowers/specs/2026-08-31-giants-foundry-design.md`. **First live session
2026-09-03 (runs gf_live1-13, logs in `ControllerAddon/logs/gf_liveN.log`, recorder
`recordings/2026-09-03_*_giants-foundry-bot1.*`).** The full loop runs unattended:
commission -> moulds -> bank -> fill -> pour -> pickup -> refine -> hand-in -> next
commission. Sword 1 handed in with 80 quality lost, sword 2 with 20; everything below was
MEASURED that day, and the quality work is where the session ended.

- **`activity` (animation-derived) reads NONE for ~half of every 2-tick machine cycle**:
  anim 9454 plays ~0.7s of the 1.2s wheel/grindstone cycle. The reader's mapping is right;
  the player genuinely idles between actions. "Working" is therefore progress advanced
  within `WORK_STALL_S` OR a machine click still inside its walk-budgeted commit window
  (`WORK_FIRST_ACTION_S + distance * WALK_S_PER_TILE`), never the animation alone. Every
  re-click of a working machine is an "overwork" that forfeits the next action; five in a
  row hit `DEAD_CLICK_LIMIT` (run gf_live2), and the stop then left the wheel running
  while the preform went cold: 147 -> 67 quality.
- **Quality is lost 5 per bad action, exactly** (varbit 13939; sweet spot claim +5): a
  machine action outside the band, or the machine ticking once more with the WRONG tool
  after a section boundary (wiki: "be ready to click away before the machine ticks
  again"). The human loses 0-2 units per sword. Both loss sites are handled by
  pre-hovering the pool and clicking in place: `_boundary_watch` (armed one action from
  the boundary, polls at 100ms, VERIFIED 0.35s flip-to-Quench, no loss on 2 of 3
  boundaries) and `edge_leave_due` (leave when the action AFTER next would land out of
  band on a 2-tick tool; one action ahead on the hammer).
- **The Dunk/Quench ramp accelerates** (easy-giantsfoundry `HeatActionSolver.DX_1`: 27,30,
  33,37,41,45,49,53,57,62,67,72,77,83,89,95 per tick). A fixed stop margin cannot track it
  (935 read -> 997 one tick later, run gf_live7). And with the machine pre-hovered, a click
  inside the same tick window as the reading cancels the ramp BEFORE its next increment
  (999 -> 971 read, click, 967) while a click 0.4s+ after the reading eats one more step.
  So the stop polls just after each tick boundary (`ctx.events` tick clock,
  `_sleep_to_after_next_tick`), aims at `ramp_aim` = target capped one worst-case step
  inside the band, and clicks IN PLACE (`click_human`, measured 92ms after the reading).
  **USER: "hover after the move, otherwise the mouse moves away from the object"** -- the
  machine is hovered only once the first ramp tick shows the player standing at the pool.
- **USER: Dunk/Quench are the LEFT-click default on this client** (their menu swap);
  `_pool_click` clicks in place when `/hover` confirms it, a verified left click otherwise,
  the right-click menu only as the fallback.
- **The m8aq Dialogue reader returns the menu TITLE as options[0]** ("How many would you
  like to add?", "Select an Option") plus blank trailing children; number keys count real
  options from 1. `_dialogue_options` strips it -- before that the crucible prompt pressed
  "X" for "3" and the first two fills only worked because "X" landed on "All". Restarting
  with the hand-in "Receive another commission?" Yes/No open needs a number key; Space
  once a tick forever (run gf_live8).
- **The m8aq storage/bank reader only carries the container while the bank is OPEN**
  (`containerLoaded` false otherwise, and it lingers ~1 tick after closing -- run gf_live5
  planned platebody one tick and platelegs the next, and every tick became a bank trip).
  Outside the bank the plan is picked from what the inventory HOLDS; inside it,
  `_restock_from_bank` re-plans from `/bank` items. `plan_fill` takes per-metal
  alternative items (`"items": [...]` in the fill config) and trims the last item to an
  exact 28 (4 mith bodies + 3 adamant bodies) instead of accepting the overflow confirm.
- **This user's bank holds ~950 items**: `/bank` reports real off-window positions for
  anything not on the visible page, so every withdraw goes through a bank SEARCH first.
  USER: "search part of it like 'mith' to get all mithril items or 'adam' to get all
  adamant items" -- `_withdraw_by_metal` searches once per 4-letter metal prefix
  ("Adamantite bar" shares "adam") and clicks the needed items from the results, falling
  back to `banker.search_and_withdraw`'s full-name search only for a result below the
  visible grid. USER: "clicking 12 times on a bar isn't very human" -- `_withdraw_count`
  takes the bulk through the right-click Withdraw-10 / Withdraw-5 entries and only the
  remainder as single clicks (VERIFIED live). **Bank UI buttons never get the simulated
  misclick** (`banker.search_bank`/`clear_bank_search`/`set_quantity_mode`): a misclick
  plus its corrective reclick is two clicks on a TOGGLE, which opened and closed the
  search box and typed "adam" into nothing (gf_live30, USER: "the search never got typed
  adam"). Their Menu Entry Swapper per-item swaps (e.g. `ui_786444_1121` on Mithril
  platebody) rewrite left-click withdraws -- the user removed that one.
- **The fill draws from EVERY usable item of a metal** (`metal_items`, wiki bar values:
  platebody 4; platelegs/plateskirt/chainbody/kiteshield/2h/warhammer/battleaxe 2; bar,
  full helm, sq shield, scimitar, longsword, claws 1; med helm and the rest are worth 0),
  taking stock from each in preference order until the metal is covered. USER: "write
  mith and ada to get all items, we have plenty of bars". VERIFIED live: sword 5 was
  filled from 1 Adamant plateskirt + 5 Adamant battleaxes after the platebodies ran out.
- A restart with the sword in hand reports weapon "Preform" on `/equipment`; setup must
  not "free hands" over it. `RESTOCK_MAX_TRIPS` bounds bank trips that never grow the
  crucible. `/objects` lists multi-tile objects once per tile (the trip hammer 24 times).
- **Bars are NOT stackable, and Use-bar-on-crucible adds ONE bar per tick** (12 bars took
  ~90s live). USER: "click the crucible then select the bar we wanna fill" -- the
  crucible's Fill (its left-click default) opens the standard skill-selection interface
  (group 270; the bar's entry was 270.19 with action "Smelt", matched by NAME since the
  widget dump has no item ids) and the usual quantity prompt follows: 12 bars in ~13s.
  The bar item is "Adamantite bar"/"Runite bar", not "Adamant bar" (`BAR_NAMES`).
- **A sweet-spot claim's ~5% bonus can complete the section on the machine's next tick**
  (wiki warns; MEASURED twice, 5 and 10 quality). After a claim whose bonus crosses the
  section end the boundary watch is armed immediately (pool hovered, flip polled at
  100ms). VERIFIED live gf_live25: claim, boundary 3s later, no loss.
- **`edge_leave_due` asks where action `lead + 1` FIRES** (`action_fires_at`), not the
  temperature after `lead` actions -- the latter left one action early into a dunk that
  cancelled itself (two wheel actions fit at 94 -> 76). Lead 1 with the pool hovered or on
  the hammer, else 2; and only when that action falls inside the current section.
- **A sweet spot is NOT claimed one action from the band edge** (`edge_leave_due` with lead
  1): the claim took the tick the leave was due and the next read was 41 on a 59 floor
  (gf_live26). The boundary watch also rides through a transitional `requiredTool=NONE`
  read instead of returning (gf_live25 lost 5 to that) and logs arm/flip/timeout.
- **Open the inventory tab before every inventory-item click** -- after a pause/resume the
  stats tab was up and the "platebody" right-click offered "View Attack guide" (gf_live27).
- The Pick-up hover check can withhold the jig click while the player is still walking;
  a withheld pickup escalates to the right-click menu, and the attempt ceiling re-reads
  the state first (the third click had picked it up, gf_live18).
- **An unusable metal drops ONE tier instead of stopping the run (2026-09-04, VERIFIED
  live).** `substitute_empty_metals` replaces a metal the open bank cannot COVER (its
  catalogue items, bars included, sum to fewer bars than the crucible still lacks of
  it -- MEASURED gf_live_tier7: 11 mithril bars left against 18 read as "in stock", the
  top-up named a platebody the bank no longer had, safety stop) or ABOVE the account's boosted Smithing
  level (`METAL_SMITHING_LEVELS`, the smelting levels) with a lower tier -- adamant
  empty -> Steel/Mithril, mithril empty -> Steel/Adamant, both -> Iron/Steel. Only the
  metals of entries that are KEPT count as taken, and a candidate that can cover its
  slot's need (down to one tier below the original pair's lowest metal) is preferred:
  MEASURED gf_live_tier8 13:34, adamant at 1 bar and mithril at 11 against 18/10 first
  resolved to Iron 18 / Steel 10 (score 37) because mithril counted as taken by the
  entry being replaced; now Steel 18 / Mithril 10 (61, VERIFIED gf_live_tier9: poured
  at difficulty 61). When nothing covers, the first untaken tier below is used anyway so
  the restock stop names it. USER: shift the pair down rather
  than collapse onto one metal; wiki (Strategies rev 15275840, 14+14) Mithril/Adamant
  95, Steel/Adamant 77, Steel/Mithril 65, all-Mithril 40. A replacement that is empty
  too still hits the existing "bank is out of" stop, so stock never slides further; the
  level, being a hard fact, is skipped past. The adjusted plan lives in `gf_fill_override`
  for the rest of the sword (so the held steel matches the outside-bank plan instead of
  bouncing back to the bank) and `_reset_counters` drops it when the bar count falls,
  i.e. the pour. **The level gate runs before ANY fill click, not only at the restock**
  (`_apply_substitution` with `stock=None` in `_fill_crucible`): gf_live_tier3 used Rune
  platebodies still in hand at Smithing 79 because the gate lived only in the bank.
  Live proof (gf_live_tier4, Mithril/Rune config as the test vehicle since the bank held
  every real tier): `Smithing 79 is below the 85 for rune -- dropping to adamant` on tick
  1, one bank trip, 8 Adamantite bars, poured. Default fill is now **18 mithril / 10
  adamant** (wiki economy pick past 70; USER has far more mith than ada) -- platebody
  quantization plans 20/8, score 83, still 5 sections; live: 5 Mithril platebody + 8
  Adamantite bar, exact 28.
- **`before_break` CONFIRMS the preform store, retries through the menu, and walks off the
  machines if it still fails (2026-09-04).** MEASURED live (gf_live_tier5, 09:15): the
  old single unverified click left the preform EQUIPPED through a 5.2-minute break and
  the sword came back RUINED, 148 -> 0 quality (5 per bad action, so ~40s of a machine
  running out of band, most likely the click resolving to a machine). The break starts
  when the hook returns regardless (engine contract), so `_await_stored` polls the reader
  for STORED with a walk-budgeted window, `STORE_TRIES` escalates to `click_menu_action`
  "Store-preform", and `_step_off_machines` force-walks one tile toward the storage as the
  last resort -- a cooling preform costs nothing, a working machine costs the sword. The
  RUINED handler then handed it in and took a fresh commission on its own, as designed.
- **"bank is out of X" is only a verdict while the bank is still OPEN (2026-09-04).**
  MEASURED live (gf_live_tier6, 12:49): a network drop closed the interface mid-withdraw
  -- `Menu never opened after right-clicking Mithril bar`, eight single clicks into
  nothing, then `search button not found (bank open?)` -- and the empty inventory read as
  out of Mithril bar with ~30 swords' worth still banked; login.py reconnected 3s after
  the stop. `_restock_from_bank` now re-reads `/bank` before the stop and retries next
  tick when it is closed (RESTOCK_MAX_TRIPS still bounds it). Same family as GOTR's
  "network drops are not routine bugs": the routine has to ride them out, not stop.
- **A bare left-click on a bank item is whatever the account's Menu Entry Swapper made
  it -- every withdraw in `_withdraw_count` is a NAMED menu entry (Withdraw-10/5/1),
  2026-09-04.** MEASURED (recordings/2026-09-04_134458_gf-iron-withdraw): the three
  "single" clicks on Iron bar each resolved to `Withdraw-14` (a per-item `ui_786444_*`
  swap; 786444 is the bank item container 12.12), 10 + 5 + 14 hit the 28-slot cap, every
  later withdraw was refused with "You don't have enough inventory space", and the run
  stopped on "bank is out of Steel bar" three times with steel in the bank. The
  routine's own "Default quantity: 1" click registered (widget 12.29) and changed
  nothing: a swap ignores the quantity mode. Mithril and steel bars carried no swap,
  which is why the same code had withdrawn exactly 18 of them all morning. The recorder
  found this in one run after three log-only guesses; reach for it sooner. NOT fixed:
  `banker.search_and_withdraw` still single-left-clicks its remainder and is exposed to
  the same swap.
- **Planner shape now:** per metal, take stock from each catalogue item in order (bank
  stock inside the open bank, held inventory outside), cap a metal's need by the crucible's
  free space, top up a short held plan with the primary item so the restock fetches it,
  then trim overflow smallest-item-first (later equal-size steps first) to an exact 28.

## Current state of routines/mastering_mixology.py

Live-debugged to a working state 2026-08-12/13 (every MEASURED fact from those sessions is in the
module docstring and the per-function comments), then **converted to event-driven outcomes
2026-09-06 (USER: "make it as tick perfect as can be with our new events"). Not yet run live in
this form**; the module docstring's numbered worklist is the first session's checklist. Shape:

- **Every wait is `_await`** (`bridge_events.await_outcome` on the module clock with a pre-check,
  same as blast_furnace): a subscribed `VARBIT_CHANGED` (`VARBIT_SUBSCRIPTIONS`: mixer slots
  11324-11326, vessel 11339, station potions 11340-11342, station progress 11327-11329,
  skillshots 11337-11338, order-1 type 11315, digweed 11330-11333 -- runelite-api 1.12.38
  `VarbitID`, javap-verified), `MENU_CLICKED`, `INVENTORY_CHANGED`, `CHAT_MESSAGE` or the new
  `GRAPHICS_OBJECT_CREATED` decides WHEN to look; `/varbit`, `/inventory`, `/mixology-state` or
  `/last-action` decide WHAT happened. `ctx.events is None` keeps polling behaviour everywhere.
- **The wait-for-stillness gate is gone**: `_click_object`/`_hover_object` aim through
  `object_refresh_target` (uncached `/object/at`, keyed on the multiloc BASE id) so a click tracks
  the projection while the player walks and the lab's ever-retargeting camera moves.
- **Lever pull = two signals**: `MENU_CLICKED` target names the lever the click actually hit
  (three stand side by side), then a mixer-slot varbit change says the pull LANDED (budget
  includes the walk). Vessel content is the vessel varbit (`vessel_potion_name`, 1-indexed into
  the game's potion order), `impostorId` only as the fallback. All three bases chain inside one
  `tick()` under `CHAIN_MAX_S`, re-checking pause/safety between bases.
- **`_choose_orders` puts Retort orders LAST.** This is the structural fix for the 2026-08-13
  wrong-station bug (an extra Retort click after the potion finished auto-starts the NEXT
  unprocessed potion at the Retort): processing is positional, so with the Retort at the end
  nothing unprocessed remains when it finishes. The Retort loop's strictly-increasing-progress
  gate stays as the belt.
- **The Retort's clock is its progress varbit** (exactly +1 per tick, measured): one
  `click_human` per `VARBIT_CHANGED` on 11327, `RETORT_CLICK_PHASE_S` after it, stopped the
  instant 11341 reads 0, with a `/mixology-state` re-read every `RETORT_HTTP_CONFIRM_S` for a lost
  packet. The old loop slept 0.55-0.75s wall-clock per click and polled chat.
- **Agitator/Alembic click on `GRAPHICS_OBJECT_CREATED`** (2954/2955 within `FLASH_CUE_RADIUS`
  of the station's tile) after a station-specific reaction draw (`FLASH_REACTION_S`; the
  Alembic window is ONE tick), the Agitator takes its second in-window click on the next progress
  tick (wiki: double bonus), and a hit is a CHANGE of the skillshot varbit from its pre-cue value
  (noots-mixology). The deliberate first-flash miss (`SPEEDUP_MISS_CHANCE`, user directive) stays.
  Needs the 2026-09-06 client rebuild for the event; polls `/graphics-objects` otherwise -- the
  old Alembic path polled a FRESH scan 33 times a second.
- Refiner: one click per tick on `_tick_pause` (was 4-16 clicks/s); the first herb leaving the
  inventory is the arrival signal before the hover. Deposit/hopper/bank waits confirm on
  `INVENTORY_CHANGED`. `on_event` flags a digweed herb-ready varbit so `maybe_collect_digweed`
  reads `/mixology-state` only then or every `DIGWEED_STATE_POLL_S`.
- `process_phase` with more unprocessed potions than known orders (a restart wiped
  `mm_chosen_orders`) now routes to orders -> the leftover flush instead of returning forever.
- **Second pass the same day (USER: "keep finding improvements"):** phase transitions CHAIN
  inside one `tick()` (`TICK_CHAIN_MAX`, deposit -> orders -> mix in one go, digweed checked
  before every chained phase and before every station, since a tick now spans a cycle); the
  belt deposit waits for the game to WRITE the new orders (order-1 type non-zero) so the chained
  orders read is not the transitional empty set; the NEXT target is pre-hovered while the
  current action lands (next lever / the vessel / the next station / the belt -- the human aims
  ahead); a wrong-target `MENU_CLICKED` shortens the lever verdict wait to `MENU_VERDICT_SHORTEN_S`;
  `_await_long` chunks any long wait into measured human still segments with `controller.fidget_once`
  between them and a `stop` predicate (a cue wait ends when the potion finishes), fidget-free
  while the FIRST cue is due; `STATION_RANK` orders Alembic -> Agitator -> Retort (shortest walk
  from the vessel, AlchemyObject tiles); digweed also targets a FINISHED Liplack (wiki: before or
  after processing); a refused click returns the entity so the outcome confirm retries instead of
  a "not found" safety stop; `_stuck_watch` logs `[!] mixology: STUCK?` at `STALL_WARN_S` and
  stops at `STALL_STOP_S` on an unchanged progress signature.
  **Engine fix that fell out of it:** the in-wait fidget serial is now mirrored into
  `input_state.fidget_ser` (`controller._fidget_serial()`), closing the CLAUDE.md caveat that
  routine-side `human_delay` had never once fidgeted -- every routine gains that, not just this one.
- Tests (`tests/test_mastering_mixology.py`) drive a REAL `EventStream` through a scripted
  `controller.poll_udp`, so `await_outcome`'s own mark/since/rejected-seq semantics are what is
  exercised. Lesson from writing them: **feed each event from the click that causes it** -- a
  script that queues a later click's verdict up front delivers it during the earlier click's wait,
  which cannot happen live, and the "failure" it produced was the test's.

## Current state of routines/motherlode_mine.py

Written 2026-09-07 from the user's recording
(`recordings/2026-09-07_111533_motherload-mine.*`, a PARTIAL loop), live bridge reads with the
client standing in the mine, RuneLite's own decompiled `MotherlodePlugin` and the Plugin Hub
**Motherlode Mine Improved** (`tictac7x-motherlode`) source the user pointed at. Design:
`docs/superpowers/specs/2026-09-07-motherlode-mine-design.md`. USER's loop: mine the upstairs
pocket **behind Mercy** ("a spot where we dont have to clear the rockfall"), fill the inventory
entirely, empty it into the hopper, and once the sack is full run **one big collect/bank cycle**
at the sack + deposit box. **Run live 2026-09-07 (mlm_live1-2); the mining half works, the
hopper/collect half is the next session's worklist.**

- **THE UPPER LEVEL SITS DIRECTLY ABOVE THE LOWER ONE AT THE SAME x/y, and no coordinate test
  can separate them.** This cost the first live run and is the fact to carry forward. The
  routine mined correctly for a minute, then spent 90 seconds clicking a DOWNSTAIRS vein at
  (3749,5685) from (3749,5684) with **nothing happening at all** -- while `/last-action`
  reported a perfect `Mine / Ore vein / id 26661` on every single attempt, `/hover` agreed, and
  `travel.can_reach` agreed. USER, watching the screen: *"clicking lower level ore from
  highground"*. **No log line available to me could have caught this**; every diagnostic said
  the click was landing.
  The design had concluded the floors were separable because `worldmap.find_path(upstairs ->
  sack)` answers None and a `reachable_from` flood from the pocket returns 45 tiles that exclude
  the sack, the box and the struts. That evidence was real and the conclusion was wrong -- the
  flood was measuring a POCKET, not a floor.
  RuneLite's own `MotherlodePlugin` has the same problem and answers it with **render height**
  (`isUpstairs: Perspective.getTileHeight(client, p, 0) < -490`), hiding any vein whose
  `isUpstairs` disagrees with the player's. So **`tileHeight` is now on `/objects` and
  `/player`** (ObjectProvider, PlayerProvider -- CLIENT REBUILD REQUIRED) and
  `motherlode_mine.object_is_upper` is the floor test. The `-490` lives in the routine, not the
  provider: it is that mine's fact. The player's OWN floor needs none of this -- **varbit 2086
  is 1 upstairs**, exact and rebuild-free; only an object's floor is ever in question.
- **`ControllerAddon/mlm_sectors.py` is the fallback for an un-rebuilt client**: the union of
  `tictac7x-motherlode`'s six `UPSTAIRS_*` sector arrays, transcribed mechanically. It is a
  hand-authored overlay list, not a survey, and it is biased toward reading a tile as
  downstairs -- so its errors cost a vein rather than a dead click. MEASURED: it calls
  (3749,5685) downstairs and every vein that mined fine upstairs. `_floor_ready` logs which of
  the two tests a run is using; it never stops the run.
- **LADDERS ARE EXEMPT FROM THE FLOOR FILTER, deliberately.** Both stand on tiles that read as
  DOWNSTAIRS under either test (19044 at (3755,5673), 19045 at (3755,5674)), so filtering them
  makes the climb down impossible from upstairs -- caught before it shipped by checking the
  vendored table against those two tiles. Their ids plus the action already disambiguate:
  MEASURED live, from upstairs 19045 offers "Climb" and 19044 offers **no actions at all**.
- **`travel.can_reach` is a PREFERENCE for veins here, never a veto.** In this mine the client's
  collision grid has to describe two floors stacked on one set of coordinates, so it is
  ambiguous by construction: MEASURED, it passed the downstairs vein that cost 90 seconds AND
  rejected upstairs veins two tiles away that mine fine. `pick_vein` ranks reachable first and
  still returns a doubted vein when that is all there is.
- **`note_vein_failure` is the empirical belt to those braces**: two clicks on one vein that
  produce nothing and it is set aside for 60s. On its own it would have escaped the 90-second
  loop whatever the cause -- a wall object's wrong side, a stale scene, another player, or a
  floor test that has gone wrong again. Any depletion clears the count.
- **The mining half is `gathering.py`, UNCHANGED** -- the routine builds a synthetic vein config
  and calls `gathering.pick_target`/`gathering.gather` directly, inheriting the tick-perfect
  reaction, the next-vein pre-hover and the walk-budgeted deadlines. It does NOT use
  `gathering.tick`: a full inventory here means "walk to the hopper", none of that function's
  three policies. USER: "Can use alot of mining routine" -- correct, and it needed no change.
  Ore veins are `WallObject`s (26661-26664) and a DEPLETED vein is a different id (26665-26668)
  offering **no actions**, so gathering's existing action filter skips spent veins and its
  "gone from its tile" test is already the right depletion test. MEASURED live: one vein mined
  **42.7s for 11 pay-dirt** before depleting (the wiki's 36-40s upstairs figure), chaining
  straight onto the next.
- **The sack is `varbit + pending`, not the varbit.** 5558 is the sack's pay-dirt, 5556 the
  189-vs-108 upgrade. The conveyor takes seconds, so pay-dirt that has left the inventory is not
  in 5558 yet -- reading it raw starts a trip whose pay-dirt has nowhere to go (the reason
  `mlm-qol` has a `SackTracker`). `mining_allowance` also caps a load at the sack's remaining
  room: with a 108 sack and 27-pay-dirt loads that never bites, but the recording's sack sat at
  39, where a full load overflows and the excess is simply lost.
- **A broken machine still ACCEPTS pay-dirt** (the recording deposits 17 into one); what it
  stops is the sack FILLING. USER: "ONLY repair broken strut if we cant deposit our ore.
  otherwise leave it broken. if we are lucky another player fixes it" -- so the "will need to be
  repaired" chat starts a clock, mining and depositing carry on, and only after
  `repairPatienceS` (180s) AND with a hammer carried does it walk to a strut. No hammer disables
  repairs with one log line, never a stop.
- **The upper hopper (3755,5677) costs 50 golden nuggets** and is not bought (USER: "need to
  unlock that hopper we cant use it yet"), so `upperHopper` ships as **`never`** and every
  inventory takes the ladder to the lower hopper (3748,5672). Flipping it to `"auto"` the day it
  is bought removes every ladder trip with no code change -- auto tries a same-floor hopper
  first and latches back to the ladder if the pay-dirt does not leave the inventory.
- **Which deposit is used depends on whether a hammer is carried**: without one, the recording's
  single click on "Deposit inventory" (widget 192.31), with the user's existing Deposit Lock
  keeping the open gem sack; with one, a `Deposit-All` per ore stack in the 192.24 grid instead,
  because Deposit-inventory would bank the hammer.
- Ids, all MEASURED live: hopper 26674, ladders 19044 up / 19045 down, sack 26688 (a
  **GroundObject**) at (3748,5659), deposit box 25937 at (3759,5664), bank chest 26707 at
  (3761,5666) (**there IS a bank in the mine** -- a future hammer/pickaxe restock, out of scope
  for v1), broken strut 26670 -> 26669 (+126 Smithing), rockfalls 26679/26680 (never cleared --
  the whole point of the pocket behind Mercy is that none is in the way).
- **Four more live fixes the same day (mlm_live2-6), all MEASURED:**
  - **The hopper trip works** -- climb down, deposit 27 pay-dirt, ~18s -- **but nothing sent the
    routine back up afterwards**, so it stood downstairs and mined whatever veins sat near the
    ladder. `mine_phase` now returns to the anchor before it picks anything, and `at_anchor` is
    a FLOOR question before it is a distance one: standing directly below the anchor, every
    coordinate test says "you are here". Only `collect_phase` had a return trip; the far commoner
    hopper trip did not.
  - **The anchor was "wherever the run starts"**, so the restart after that wander anchored the
    whole run on a DOWNSTAIRS tile. It is now the configured pocket behind Mercy
    (`TILE_ANCHOR_DEFAULT` (3752,5680), three tiles north of Mercy at (3752,5677)) unless the
    player is already upstairs, which is the only case where "start where I'm standing" is what
    was meant.
  - **Every ladder click was WITHHELD by the `expected_action` hover gate** --
    `hover never matched action='Climb' target=None`. `/hover` reports what is under the cursor
    RIGHT NOW, and these clicks are issued from across the room; this is the same trap CLAUDE.md
    already records for the Gauntlet's gather clicks. The gate is gone from every world-object
    click (ladder, hopper, sack, deposit box, strut), each of which confirms on a real outcome
    instead. It stays on the `Deposit-All` in the bank grid, where a wrong resolve banks the
    neighbouring stack.
  - **WALK FIRST, CLICK CLOSE, and `onScreen` is the load-bearing half.** With the gate gone the
    routine then clicked the up-ladder from **21 tiles away with `onScreen: false`** -- aiming at
    a projection that is not on the screen at all, so the click did nothing and `/last-action`
    still showed the previous Mine. `too_far()` (CLICK_RANGE 10, or `onScreen` false at any
    distance) walks first. VERIFIED live immediately after: walk -> climb -> walk back to the
    anchor -> mining the pocket, unattended.
  - Plus `CLIMB_MAX_TRIES` (8): a ladder that never registers is a safety stop, not a loop.

- **A FULL SACK MAKES THE GAME REFUSE TO MINE AT ALL, and only a frame dump found it**
  (mlm_live7). Every click had been dying silently for two minutes -- perfectly aimed, perfectly
  resolved, nothing happening -- exactly like the through-the-floor failure. `GET /screenshot`
  showed a "Click here to continue" box reading **"Your sack will be full once your current dirt
  is cleaned."** with the plugin overlay at **Sack 111/108**. `/state`'s `dialogOpen` read FALSE
  throughout (the group-229 box karambwan_fishing already documents as invisible to it).
  The arithmetic that should have prevented 111/108 (`mining_allowance`) failed for a reason
  worth remembering: **`pending` -- pay-dirt on the conveyor but not yet in varbit 5558 -- lives
  in `ctx.state.data`, and a restart wipes it while the belt keeps its load.** Restarting with 84
  showing and 27 still travelling is how the sack went over. So the chat line is now the
  authoritative stop (`SACK_FULL_CHAT`, latched until the sack actually reads 0), a full sack
  outranks the hopper trip in `derive_phase` (pay-dirt deposited into a full sack is thrown
  away), and `collect_phase` no longer empties the hands first when the sack has no room.
- **AN OPEN INTERFACE EATS THE FIRST WORLD CLICK** (mlm_live8). The deposit box interface (192)
  was left open after banking, so every tick's ladder click merely closed it and the next tick
  found it open again: eight `the climb up did not register` in a row with the player never
  moving an inch, ended by the climb ceiling. `_close_deposit_box` confirms it shut (ESC, then
  the 192.1 button), and `mine_phase` refuses to start a walk while it is open.
- **The whole loop is VERIFIED live 2026-09-07 (mlm_live8/9)**: mine the pocket -> full inventory
  -> ladder down -> hopper (27 pay-dirt, ~18s round trip) -> ladder up -> back to the anchor ->
  repeat; and at 108 the collect cycle drained the sack **108 -> 0** across four Search/bank
  rounds, kept the open gem sack, closed the interface and went back to mining.
- **Still UNRUN**: every repair path (no hammer was carried), the upper hopper (not bought), and
  a break (`before_break`). Worth fitting from a longer session: `PENDING_MAX_S` against the real
  conveyor latency, and `VEIN_BLOCK_S` -- the pocket is CROWDED at peak (ten other players inside
  eight tiles, measured), and a vein whose approach tile is occupied looks exactly like one that
  will never be mined.

## Current state of gathering.py, routines/mining.py and routines/woodcutting.py

**The loop is `ControllerAddon/gathering.py` since 2026-09-07** (USER: "isnt our generic mining.py
possible to do woodcutting to?"). `routines/mining.py` and `routines/woodcutting.py` are ~60-line
files holding nothing but a `gathering.Profile`; every measured fact below was developed as
mining and moved verbatim, so read it as describing both. **Woodcutting has NOT been run live and
needs a client rebuild** (`woodcuttingXp`, `/woodcutting-config`, the panel's action dropdown and
`RoutineChoice.WOODCUTTING` are all Java).

- **A Profile is: `skill` / `action` / `xp_field` / `names_key` / `distance_key` / `noun` /
  `keep_items` / `config_fetch` / `default_config` / `full_inventory`.** Mining is
  Mining/"mine"/`miningXp`/`rockNames`/pickaxe/bank; woodcutting is
  Woodcutting/"chop down"/`woodcuttingXp`/`treeNames`/axe/DROP. `config_fetch` is a LAMBDA, not a
  bound function reference -- binding `controller.get_mining_config` directly captures the
  function object, so a monkeypatched test (and controller.py's own double-import) reaches the
  wrong one.
- **A felled tree needs no respawn timer, and that is the whole reason woodcutting fit.** The
  stump keeps the tile but offers no "Chop down", so `pick_target` stops matching it the moment
  it falls, exactly as a depleted vein stops offering "Mine"; the chain's `exclude=` covers the
  tick-cached bulk scan for the tick or two after. A lone tree simply idles until it grows back.
  A multi-log tree rides the SAME branch gem rocks and amethyst already used ("xp gained, object
  still standing -> keep waiting, reset the budget").
- **A full inventory is `fullInventory`: bank | drop | stop** (`gathering.full_inventory_phase`,
  `FullInventoryChoice.java`, exported on both config endpoints). Mining defaults to bank -- the
  2026-09-04 path, unchanged. Woodcutting defaults to drop, because power-chopping is the normal
  loop. A drop that frees nothing SAFETY-STOPS rather than looping on a full inventory.
- **`controller.drop_items` verifies the FIRST drop and falls back to the menu.** Shift-click
  drop is a GAME SETTING the account may have off, and with it off a shift+click on a log is an
  ordinary left-click that does nothing -- a blind loop would "drop" 27 items while dropping
  none. So: one `/inventory` read before, one after the first drop; if it did not shrink, that
  item is retried through the right-click "Drop" and every remaining one goes the same way. No
  simulated misclick and no UI-blocker check (banker.py's interface-click rule) -- here a
  corrective reclick would drop the NEIGHBOURING slot, which is the axe.
- **The sidebar section is "Gathering" now, with an ACTION dropdown** (Mine / Chop down) that
  decides both what the scene scan matches and WHICH CONFIG KEY the checkboxes persist to
  (`rockNames` / `treeNames`). Switching action reloads the selection from the other key and
  re-scans -- showing Mine's names while pointed at `treeNames` would write rocks into the
  woodcutting setting on the next click. `maxRockDistance` and `maxTreeDistance` stay separate;
  both existing mining keys keep their names, so nothing already configured moved.
- `keep_items` is a SUBSTRING match, so woodcutting's `"axe"` also keeps a pickaxe or a
  battleaxe in the inventory. Deliberate: dropping the axe you are chopping with is the failure
  that matters.
- Tests: `tests/test_gathering.py` (the loop, through the Mining profile -- was
  `test_mining.py`), `tests/test_woodcutting.py` (only what the second profile changes),
  `tests/test_controller.py` (`drop_items`).
- **THE LOG BASKET NEEDS NO DRIVING, ONLY EMPTYING (2026-09-08).** USER: "need to add a option
  to empty wood basket too since we use that for cutting wood basicly doubling our capacity",
  then "it fills automaticly all we need to do is right click it in bank". An OPEN log basket
  absorbs logs as they are cut (cache config/obj 28142 param_1784: "When opened logs will
  automatically be deposited into it"), so the inventory only reaches 28 once the basket's own
  28 are full -- the doubling costs no code, and there is no Fill step to drive the way
  `fill_fur_pouch`/`fill_coal_bag` have one.
  - **`banker.empty_log_basket` is a NAMED MENU SELECTION, and not because the click is
    contested.** MEASURED from the cache: NEITHER `log_basket_closed` (28140) NOR
    `log_basket_open` (28142) has an "Empty" iop at all -- both offer Fill / Wear / Check /
    Open|Close / Destroy. So the Empty the user right-clicks is a **bank-context** option the
    interface substitutes in over the item's own, exactly as `fill_fur_pouch`'s docstring
    already recorded for the fur pouch ("Deposit-1/5/10/X/All/Empty-to-bank instead"). A bare
    left click would resolve to whichever of Fill / Deposit-1 / Wear won that race, and **Fill
    is a silent no-op that looks like success**. The option is the SEQUENCE
    `("empty-to-bank", "empty")` tried against the one menu `click_menu_action` already reads
    back: the fish barrel next door answers to the first and the fur pouch and looting bag to
    the second. **CONFIRMED LIVE 2026-09-08 it is `Empty-to-bank`** (`menu-select Empty-to-bank
    Open log basket`), so the first name was right; keep "empty" behind it for the SHUT basket,
    which has not been exercised. Called from
    `deposit_inventory` beside `empty_looting_bag`, under the same `skipContainerDeposit`
    opt-out, so every routine that banks gets it.
  - **The basket matched nothing on the keep list, so BOTH full-inventory policies destroyed
    it**: `bank` shift-deposited the basket into the bank immediately after emptying it (no
    basket next trip), and `drop` threw it on the ground. `keep_items` is `("axe", "log
    basket")` now -- one substring covers "Log basket" and "Open log basket", which is what the
    cache says the two states are called. The Forestry basket (28143/28145, the log basket +
    forestry kit joined by a Log brace) is a DIFFERENT name and is deliberately not matched.
  - Nothing opens the basket: carry it open, a shut one collects nothing.
  - **`GET /api/storage/log-basket` CANNOT tell you how full it is.** MEASURED live 2026-09-08
    with the basket in hand: `present: true`, `open: true`, `itemId: 28142` -- but
    `contentsLoaded: false`, `storedQuantity: -1`, `remainingCapacity: -1`, because the m8aq
    reader only populates contents while the basket's own Check dialog is open. So the
    routine's "is it worth emptying" question is not answerable from the bridge; emptying an
    already-empty container is a no-op, which is why `empty_log_basket` is called
    unconditionally whenever one is carried rather than gated on a count.
  - **The bank's own "Deposit Containers" button does NOT empty a log basket** -- MEASURED
    2026-09-08, logs went on arriving loose after a trip whose `deposit_all_containers` click
    had landed. That button covers the looting bag / herb sack / seed box / coal bag family;
    the basket needs its own Empty, which is the whole reason this helper exists.
  - **The empty RETRIES against a fresh `/inventory` read, three times.** MEASURED the same
    day: one right-click hit BARE GROUND (`['Cancel', 'Walk here']`) because `/inventory`
    reported the basket at screen **(31,50)** -- read 0.12s after the bank interface opened,
    i.e. a pre-layout widget position taken mid-transition. The next item in the same panel
    resolved to a sane (1598,445) 2.8s later, and with the bank shut the basket reads
    (1529,438). **The position was not wrong, it was EARLY**, so a retry has to RE-READ rather
    than re-aim at the same dict. Failing all three is not a stop -- the deposit right after
    still banks the loose logs, so a stuck basket costs capacity, never the trip. **Any
    inventory-item click issued immediately after a bank opens shares this exposure.**
  - **THE DOUBLING IS VERIFIED LIVE 2026-09-08**: over the 12 minutes after an empty, +8606
    Woodcutting xp (~49 yew logs at 175 each) left only **19** in the inventory -- the basket
    took the first 28.
  - **A FULL basket is silently indistinguishable from a missing one while chopping.** MEASURED
    the same run: with an open basket carried, logs arrived LOOSE in the inventory (12 Yew logs
    in slots 2-13) -- the basket was already full from a previous session, so there was no
    doubling at all until the first bank trip emptied it. Nothing in the log says so, and the
    reader above cannot either. If a session's inventory fills at the normal rate with a basket
    carried, that is the reason; it self-corrects on the first bank trip.
- **`GATHER_BUDGET_S` (12s) IS TOO SHORT FOR YEWS -- MEASURED 2026-09-08, and it was the
  worklist's own open question.** A yew at this account's level yields a log about every
  **10.5s** (1973 xp / ~180 per log over 115s), so an ordinary gap between logs reaches the
  budget and `gather` logs `nothing from Yew tree in 12.2s -- re-picking` while the player is
  still happily chopping. It costs no logs -- `tick` then returns at the `not isIdle` gate and
  the GAME auto-chops the tree to the ground (one such gap ran 95s and still earned 1973 xp) --
  but it forfeits the pre-hover chain and fills the log with false failures. The fix is a
  per-Profile budget (woodcutting wants ~30s); NOT applied yet.
- **FIRST LIVE RUN 2026-09-08 (VM, `logs/vm_woodcutting_*.log`), and the client rebuild is
  IN**: chopping Yew trees at (1591,3487) with `woodcuttingXp` reporting real drops
  (`+180 Woodcutting xp, tree still up`), arrival 0.18s after the click, the next tree
  pre-hovered. So `xp_field`, `/woodcutting-config` and the panel entry are all confirmed
  present in the running client.
- **Woodcutting's remaining live worklist:** whether a tree's `OBJECT_DESPAWNED` lands on
  the tree's own tile the way a rock's does; whether the account has shift-click drop ON (watch for
  `shift-drop did nothing (shift-click drop off?)` in the log, `drop` policy only); that the
  panel's Chop down scan lists "Yew tree" rather than a bare "Yew". The log basket's own
  questions are settled (above); what is left there is whether a SHUT basket really offers
  "Empty", which the sequence's second name assumes.

- **Rock choice is the sidebar panel's "Gathering" section**
  (`SettingsPanel.java`): Refresh scans every plane of the loaded scene on the client thread
  for GameObjects whose impostor-resolved composition offers "Mine", lists each display name
  once with a filter box and All/None, and persists to the existing `rockNames` key -- so the
  wrench-icon text field and the panel are one setting. The routine reads it through
  `GET /mining-config` (`controller.get_mining_config`); before this it ignored the bridge
  entirely, so that text field had never done anything. A bridge that cannot answer falls back
  to `DEFAULT_CONFIG`; an EMPTY panel selection means an idle routine with one log line, never
  the defaults' rocks.
- **Reaction is event-driven, truth is HTTP.** `mine_rock` clears `ctx.events`, clicks, rests
  the cursor on the NEXT rock (`hover_entity` + `object_refresh_target`), then blocks on
  `wait_for_event` for a Mining `XP_GAINED` or an `OBJECT_DESPAWNED` on the rock's own tile
  (`EVENT_WAIT_S` 0.25 between HTTP checks; `FALLBACK_POLL_S` when `ctx.events` is None). The
  rock is DONE only when uncached `/object/at` no longer finds its id on its tile -- an XP drop
  with the rock still standing (gem rocks, amethyst) resets the budget and keeps waiting,
  because the player keeps swinging on their own. `tick` then chains straight onto the
  pre-hovered rock (re-validated through `/object/at` first) for up to `CHAIN_MAX_ROCKS` /
  `CHAIN_MAX_S`, handing back to the engine loop on any non-depleted outcome.
- **The bulk `/objects` list is tick-cached and still shows a just-emptied rock under its old
  id**, so every pick after the first excludes the tiles mined this chain (`exclude=`) -- the
  first test run caught the pre-hover flying straight back to the depleted rock.
- Budgets all include the walk (`WALK_S_PER_TILE` x distance): `IDLE_GRACE_S` before an idle
  player with no ore counts as a failed click, `ROCK_BUDGET_S` before re-picking.
- Live-verification worklist: `IDLE_GRACE_S`/`ROCK_BUDGET_S` against real swing times at the
  user's level, whether the rock's `OBJECT_DESPAWNED` reliably lands (or only the XP drop
  does), and the pre-hover's interaction with the misclick/settle machinery on the near-zero
  click flight.
- **2026-09-04: `mine_rock`'s wait is `bridge_events.await_outcome`** (mark/since instead of
  `clear()`, one HTTP confirm per matched signal, `PLAYER_STOPPED` + `/player.moving` false
  rebases the walk share of the budget on arrival) **and a full inventory BANKS** when a bank
  is within `BANK_MAX_DISTANCE` (40): deposit all but the pickaxe, walk back to the recorded
  stand tile, resume; no bank -> the same "inventory full" safety stop. `get_config` forces
  `safety.stopOnInventoryFull` OFF because `check_safety` runs before `tick` and would win the
  race. Not yet run live. (Both of these live in `gathering.py` now -- `gather` and
  `full_inventory_phase`.)

## Current state of routines/shipwreck_salvaging.py

Last LIVE session 2026-08-05 (design spec `docs/superpowers/specs/2026-08-01-shipwreck-salvaging-
design.md` carries every measured fact from those runs). **Offline bugfix pass 2026-09-03, NOT yet
run live** (USER: "cant live test right now but i want you to bugfix shipwreck_salvaging with mcp
and improve it for usage soon") -- every fact below came from the osrs-wiki MCP (wiki rev
15290733, the cache loc/obj dumps, the Plugin Hub sailing / salvaging-afk-timer sources) plus
`javap` over runelite-api 1.12.38 in the Gradle cache; the client was up but the player was not
aboard a boat, so `/sailing`/`boatX` could not be exercised. `tests/test_shipwreck_salvaging.py`
pins all of it. The first live session's worklist, in the order the failures would bite:

- **`stop_boat` could no longer click the sail toggle.** The Sailing Options panel lives in the
  SIDEBAR, and `GameStateProvider` has reported the whole sidebar as a UI blocker since the
  2026-09-02 rebuild (inventory-parent containers) -- `click_entity`'s default blocker check
  would refuse the click and rotate the camera instead, sails staying up, boat ramming the wreck.
  The toggle click now passes `check_ui_blockers=False` like every other interface click. The Q
  tap is also verified and retried (`controller.ensure_ui_state`, `STOP_TAB_TRIES`), and
  "stopped" needs `STOP_STILL_READS` (2) consecutive still polls with `boatSpeed == 0` when the
  bridge reports it. Watch for `couldn't open the Sailing Options panel` / `sail toggle not found`.
- **`SALVAGE_CHANNEL_ANIMATIONS` covered ONE hook size.** The three ids confirmed live
  (13576-13578) are the 2X5 family; runelite-api has 1X3 (13569-13571), 3X8 (13583-13585) and
  LARGE01 (13591/13592/13594) families too, and the cache shows the hook facility's animation
  follows the hull it is built on (`salvaging_hook_large_*_b` -> `..._3x8_idle01`). On a bigger
  boat every recovery (reclick, reposition, give-up) would have fired against a working hook.
  Also resolved: the "animation stuck at 13352, cause unconfirmed" note -- 13352 is
  `HUMAN_SAILING_ALPHA_HELM_SMALL01_ACTIVE01_LOOP`, the player still standing at the helm
  (`HELM_ANIMATIONS`; the routine logs it and reclicks).
- **On-boat fixture clicks (helm, hook, sail toggle) and salvage drops never get the simulated
  misclick** (`sailing._FIXTURE_CLICK_KWARGS`, `drop_salvage`): the deck is a few tiles across
  and `MISCLICK_OFFSET_PX` (20-45px) is bigger than the 7px offset already measured to land on
  the sail model; the toggle is a toggle (miss + corrective reclick = sails up then down); and a
  miss beside a salvage item DROPS whatever tool sits in the next slot. Same rule as banker.py's
  bank buttons.
- **The level check failed CLOSED**: an unreadable `/skills` read as boosted level 0, every
  wreck became "above my level", and the routine idled on "no shipwreck in range" until
  restarted. Unreadable now means "do not filter".
- **The far branch never repositioned after a short landing.** `sail_to`'s arrival pre-check
  returns True within braking distance of the TARGET (an approach point beside a lone wreck), so
  a landing outside the wreck's `WRECK_SQUARE_SIZE` square deployed the hook every
  `HOOK_CLICK_COOLDOWN_S` forever. `tick` now verifies `_wreck_in_hook_range` after the sail,
  closes in on the approach point, and gives the wreck up after `HOOK_ENGAGE_MAX_REPOSITION_
  ATTEMPTS` misses (`far_wreck_miss_count`).
- **Approach points were float tiles**, and `ocean_path.find_ocean_route` tests the target
  against `/tiles`' integer set -- obstacle routing was silently OFF for every approach-point
  sail. Rounded now.
- A given-up near wreck is now EXCLUDED from `find_wreck_in_hook_range` instead of nulling its
  result, so a second in-range wreck is still worked.
- **Chat signals (`on_event`)**: "reclaimed by the sea" (the wreck sank -- the hook does not
  carry on, CONFIRMED 2026-08-01) clears the click cooldown so the next tick redeploys; a
  "hazardous waters" refusal blacklists the wreck under the hook for `HAZARD_GIVEUP_S` (30 min)
  on both give-up keys, because it is the boat's fittings that are lacking, not the approach.
  Player chat types are ignored.
- **Salvage is dropped one slot EARLY** (`dropAtFreeSlots`, default 1) so the game's
  full-inventory "click here to continue" dialogue -- which interrupts the channel and needed the
  Space workaround -- is avoided on most fills; that workaround is kept as the fallback, and 0
  restores the old drop-only-when-full behaviour. UNVERIFIED whether an inventory click mid-channel
  costs anything; if the log shows a reclick after every drop, that is what it is.

**Second offline pass 2026-09-04 (USER: "keep improving it", then "take a look at sailing.py and
ocean_path and bugfix / improve them"). Still not run live; pinned in `tests/test_sailing`-style
tests inside `test_shipwreck_salvaging.py`, `tests/test_ocean_path.py` and one new travel test.**

- **EVERY MINIMAP CLICK WAS BEING REFUSED, in `travel.py` too, not just sailing.** The
  2026-09-02 `GameStateProvider` rebuild added the minimap rects (`RESIZABLE_MINIMAP_WIDGET`,
  `_STONES_WIDGET`, `FIXED_VIEWPORT_MINIMAP`, `MINIMAP_ORBS`) to `uiBlockers` so a force-walk
  projected INSIDE the minimap circle would stop being clicked -- correct for a viewport walk,
  but `click_entity`'s blocker check is a flat rect test, and a minimap HOP is aimed at that
  rect on purpose. `sailing._hop_to` and `travel._click_walk_point`'s minimap branch both used
  the default `check_ui_blockers=True`, so since that rebuild each minimap hop was refused and
  answered with a camera nudge (`Aim point is behind a UI panel -- rotating camera`). GOTR did
  not notice because its own `_direct_walk` minimap hop already passed `check_ui_blockers=False`.
  Both now pass it; the viewport (ground) click keeps the check. **Rule: any click aimed at a UI
  element -- widgets, sidebar panels, the minimap -- opts out of the blocker check.** No live log
  showed the refusal only because no `travel.walk_to` routine ran after 09-02; the code path is
  unambiguous.
- **Braking now budgets the reaction time, not just the coast.** `_dynamic_brake_tiles` was
  `speed * BRAKE_LOOKAHEAD_TICKS` (post-toggle coast only); the 1.2-2.5s between the brake
  decision and the toggle click (Q tap + settle, mouse flight, two bridge reads) is 2-4 ticks of
  FULL-SPEED travel it never charged -- 4-8 tiles at ~2 tiles/tick, which is the "overshot
  again" of 2026-08-05. Now `speed * (STOP_REACTION_TICKS + BRAKE_LOOKAHEAD_TICKS)`, `sail_to`
  pre-opens the Sailing Options panel before the FINAL leg so the Q tap is already paid, and
  `stop_boat` logs `toggling sails N.Ns after the brake decision` -- fit `STOP_REACTION_TICKS`
  (3, blind) from that number and the existing drift/overshoot line.
- **`stop_boat` reads `/hover` before clicking the toggle** (`_sails_already_down`): the icon's
  action flips between "Set sails" and "Un-set sails", so clicking it on an already-stopped boat
  SETS the sails. Fails open -- unreadable hover, a real menu open, or any text that is not
  plainly "Set sails" still clicks, because sails left up is the worse failure.
- **`ocean_path` string-pulls the route** (`_smooth_path`): `_simplify_path` keeps every grid
  direction change, and a 16-direction A* line at an angle the grid cannot draw (slope 1/3) is
  a straight/knight alternation with a "corner" every tile or two -- each one a hop click and a
  poll for a bend the compass-snapped heading could not follow anyway. Smoothing jumps to the
  farthest later point whose straight segment stays inside the dilated water set (four samples
  per tile), so a route around a wall keeps its real corners and open water collapses to one
  waypoint.
- **`sailing._hop_to` pulls an off-minimap aim point in** (`HOP_PULL_IN_FRACTIONS`, same as
  travel) instead of failing the leg -- which `sail_to` then re-planned into the same failure
  `MAX_ROUTE_ATTEMPTS` times.
- **XP stall watchdog in the routine** (`CHANNEL_XP_STALL_S`, 120s): the channel animation
  says "working", Sailing XP says whether salvage is actually rolled (every 4 ticks per the
  wiki's Dec-2025 hotfix; p(no XP in 120s) is under 1e-3 at the worst documented rate). No XP
  for that long while the animation plays forces one reclick. The clock only runs while the
  channel animation is playing.

Not changed, still open from the spec: `BRAKE_LOOKAHEAD_TICKS` (3) is unfitted, `SAIL_HOP_MIN/
MAX_TILES` are untuned for the 16-point compass, and the engine-level `maybe_misclick` guard the
spec's item 9 describes no longer exists in `controller.py` (the stray-click simulation was
removed), so it needs no re-adding.

**Third pass 2026-09-07: mass-world mode, cargo-hold sorting and a keep-list** (spec
`docs/superpowers/specs/2026-09-07-shipwreck-salvaging-mass-world-and-sorting-design.md`, plan
`.../plans/2026-09-07-...md`, 16 commits `e666508..e405b55` on `dev`). **NOT RUN LIVE — and it
CANNOT be until the client is rebuilt**; run `tools\salvaging-live-run.ps1`, which stops the
client, rebuilds, relaunches, runs the boatViewId gate and only then starts the routine.
Everything below was MEASURED live 2026-09-07 unless marked otherwise:

- **`/objects` and `/player` carry `boatViewId`** (the outermost non-top-level `WorldView`, i.e.
  the boat). Needed because on a mass world **26 `Salvaging station` objects from ~13 overlapping
  boats** sat within a few tiles, three on the player's exact tile, all `clickable`, screen
  positions ~20px apart — **position cannot separate them**, and `sailing.deploy_hook`'s old
  first-match-by-name pick was a coin flip between our hook and a stranger's. `sailing.find_facilities`
  / `find_facility` filter on it and fall back to nearest-by-name when it is absent.
- **The salvaging station** is loc `59699/59700/59701` (ports `60462/60463`), op `Sort-salvage`.
  It opens **NO interface** — a widget watcher across a full sort recorded no new group. Plain
  channel, **animation 13599** (confirmed on a 3x8 hull despite runelite-api naming it `..._1X3_
  INTERACT01`), **1.79s per salvage** (3 ticks), and **it ends itself** when the inventory runs
  dry. `SORT_ANIMATIONS` is deliberately SEPARATE from `SALVAGE_CHANNEL_ANIMATIONS` — merging
  them makes the hook's idle-reclick logic read sorting as hooking and never redeploy.
- **The cargo hold is interface 943** (side panel 944): `943.4`/`943.5` = used/capacity,
  `943.10` = grid with `Withdraw-All`, **`943.14` = `Deposit` SALVAGE only** (never `943.13`
  Cargo or `943.15` Inventory, both of which would bank the chisel and rune pouch), `943.20..24`
  = quantity buttons. Salvage does **not** stack in the inventory, so a `Withdraw-All` self-caps
  at the free slot count. Driven by `ControllerAddon/cargo_hold.py`.
- **`/trawling` is a DEAD END for the hold.** Its `cargo` block stayed null all session with 12
  items in the hold, and `carryingCargo` stayed 0 (that varbit is courier cargo). Containers
  `963..967` are the right ids and simply are not populated. **This also corrects an assumption
  in the deep-sea-trawling spec**, which has never run live either.
- **Group 229** ("Your inventory is too full to hold any more plundered salvage") is invisible to
  `/state`'s `dialogOpen`, same trap `karambwan_fishing` documents. `controller.continue_box_open`
  / `dismiss_continue_box` detect it **by the CONTINUE widget's presence, never by 229.3's text** —
  that wording is salvage-tier-specific.
- **The boat carries TWO hooks and a crewmate mans one** (USER). Nothing in Python can see which:
  **`GET /npcs` returns 0 NPCs on a sailing world** because `NPCProvider` reads only the
  top-level `WorldView` and crewmates live in the boats' nested views. So the rotation is
  EMPIRICAL — a hook that burns its engage budget is set aside for `HOOK_SKIP_S` (180s) and the
  other tried. `sailing.ALL_SKIPPED` (a falsy sentinel, the `CLICK_WITHHELD` idiom) separates
  "every hook skipped" from "the click missed", so an incidental miss cannot wipe the skip list.
- `massWorld` (default True) never sails — an area's wrecks share a spawn pool and one rises as
  another sinks (wiki rev 15290733), so waiting beats sailing. It suppresses the far-wreck branch
  AND the won't-engage reposition, while the reposition BUDGET still counts.
- **The whole-branch review's lesson, worth carrying to the next routine: the branch passed all
  nine per-task reviews and was still NOT safe to run unattended.** It "added four new ways to
  make no progress and zero new ceilings" — a failing sort rung could spin forever (and the
  full-hold clause outranks hooking, so it blocked earning too); kept items had no sink, so the
  inventory filled within ~an hour and deadlocked in an infinite hook loop; a hold that never
  opened silently dropped every salvage for hours. All bounded now (`SORT_FAIL_LIMIT`,
  `HOLD_OPEN_FAIL_LIMIT`, `stow_keepers`, terminal safety stops). **Per-task review cannot see
  this class of defect; only a whole-branch pass can.**
- **`drop_items` returns the CONFIRMED inventory delta, not clicks issued**, and polls
  `DROP_CONFIRM_S` (1.5s) before declaring a drop failed — a verification window shorter than the
  0.6s server tick reads a good drop as a failure ~3 times in 4, and an attempt count as the
  progress signal lets a stray interface eating every click defeat the no-progress ceiling
  entirely.
- First live session's worklist is the spec's checklist. Its headline unknowns: whether `943.14`
  really deposits salvage only, whether a respawn gap fits a withdraw-plus-sort cycle, and
  **whether the keep-list's item NAMES match what the game hands back** — they are wiki-sourced,
  the user's own `Sailors' amulet (inert)` is absent from the wiki's table entirely, and a
  near-miss under a substring keep-list fails **silently in the drop direction**. `drop_items`
  logs every dropped name for exactly this.

## Current state of routines/agility.py

Written 2026-09-02 from a human recording (`recordings/2026-09-02_032621_wilderness-agility.*`,
one lap) plus live bridge reads the same day. A generic lap engine over per-course data in
`ControllerAddon/agility_courses.py` (only WILDERNESS shipped; a rooftop is a data entry).
Design: `docs/superpowers/specs/2026-09-02-agility-runner-design.md`. **Not yet run live**;
the spec's "Live-verification checklist" is the first session's worklist — it needs one
deliberate ropeswing fail and one log fail to confirm the dungeon recovery walk-back.

Signals are un-gated on purpose (user directive): Agility XP from `/api/skills/skills`,
the game's own `busy` varbit 12393 (1 while an obstacle animates, 0 when done -- the signal
that confirms Rocks on a forfeited lap, where the 498.9 bonus is 0), the player tile from
`/player`. The step is re-derived from the player's tile every time
(`agility_courses.step_for_position`, disjoint START boxes pinned by test), so nothing
load-bearing lives in `ctx.state.data`. Steps are chained inside one `tick` (the human's
next click lands 0.2s after the previous XP drop; the engine loop's ~1.1s would triple
that). `pvpSafety.fleeEnabled` is False for this routine (crowded course, user decision).
Dungeon ladder pinned live: id 17385 at (3005,10363), `Climb-up`.


## Current state of routines/falconry.py

Written 2026-08-23 **entirely from a recorded human run** (`recordings/2026-08-23_121236_falconry-
entire-run.*`) rather than from guesswork — the first routine built that way, and the reference for
how the recorder is meant to pay off. Hunts Dashing kebbits and does the full Castle Wars bank round
trip. **Not yet run live.**

Signals it keys off, all established from the recording rather than assumed:
- **`varbit 14192`**: `5` = falcon is sitting on a kill and must be retrieved, `0` = it can be
  thrown. Verified by pairing, not by guessing at meaning — exactly two changes per successful
  catch across two recordings (54/27 and 14/7), never moving on a miss.
- **The falcon has no animation at all**, so there is nothing to wait on visually — confirmed
  across both recordings once NPC animation capture was known-working.
- A **miss is normal** (~40% of throws: 46 attempts → 27 catches), never treated as an error.
- **Bank trigger is free inventory slots.** Fur/meat go to their pouches until 28 each, then spill;
  post-spill each catch costs ~2 slots. Count slots from the live inventory list, **never** from
  distinct item names — 11 Bones is 11 slots, not 1 (the bug that made the recorder report 22 free
  while the game refused fur pickups).
- `Quick-falcon` on Matthias does the whole 500gp transaction in one click; the human's preceding
  `Talk-to` contributed nothing.

The bank trip dispatches on **current region**, not a stored step counter — `ctx.state.data` is
wiped on restart while the character's position is not, so a counter desyncs exactly mid-trip.

**Loadout requirement (a deliberate deviation from the recording):** ring of dueling and Ardougne
cloak must be in the **inventory**, not worn. The human used the worn-equipment interface; this
clicks the inventory item, which needs a screen position that `/equipment` does not provide.
**No Dramen staff any more (USER 2026-09-06: "dramen staff isnt needed to take fairy rings
anymore")** -- the wield/unwield legs, `_has_return_loadout`'s staff check and golem_crafting's
`KIT_ITEMS` entry are all gone. **Inventory teleports are a LEFT-click (USER 2026-09-06: "for
inventory teleports ive changed them so we just click the item and we teleport to the right
spot")**: `_use_teleport` left-clicks with `expected_action=<option>` so `/hover` proves the
account's menu-entry swap is in place on THAT item, and only a withheld click falls back to the
right-click menu; golem_crafting's `_teleport_home` (slayer ring) does the same and then answers
the destination dialogue if one opens. **2026-09-06 event-driven rewrite (not run live):** the
throw/retrieve/hire primitives await the falcon varbit 14192 and INVENTORY_CHANGED through
`outcome_waits`, `hunt_step`/`hands_free`/`_ensure_hands_free`/`_use_teleport(expect_region=)`
are exported for golem_crafting's fur trip (see that section), and the teleport landing is
confirmed on `/player.regionId` after PLAYER_STOPPED instead of a 3s sleep.

**2026-09-14 (live): getting off the fairy ring, and onto the right side of the fence.** USER:
"Also we are stuck on other side of ring"; "It's an enclosed area stile makes I able to jump
through". Two defects on the same leg, both MEASURED against the real collision map:

- **The ring tile is an island.** `worldmap.reachable_from(2319, 3619, 0)` returns exactly **1**
  tile — the fairy ring drops the player ON the ring and the ring object's own blocking is baked
  into the same bits a wall's is (CLAUDE.md: the static map cannot tell furniture from a wall). So
  `travel.walk_to` could not route a single step and the trip simply sat there. The east, north
  and south neighbours are all in the same **738-tile** component, which reaches the stile in 55
  steps, so one step OFF the ring is the entire fix: `_step_off_isolated_tile` shift-clicks a
  cardinal neighbour, because routing is exactly what is unavailable while standing on a tile the
  map believes is sealed. `is_isolated` is the reusable predicate — expect this at any fairy ring.
- **`y < TILE_STILE["y"]` was a bad proxy for "already across the stile".** True just after
  climbing; FALSE at the ring, which lands at y 3619 (under 3621) but fifty tiles WEST of the
  fence — so the trip walked straight at the hunt tile from the wrong side. MEASURED: the ring-side
  component is 738 tiles, the hunting enclosure **389**, and they are disjoint; the stile's own
  tile is in neither. `_leg_walk_home` now asks the map whether the hunt tile is ROUTABLE, which is
  the real question and also subsumes the 2026-08-24 climb-back-and-forth bug the y-check existed
  to fix (once across, the hunt tile is reachable, so the stile is never touched again).
  `SOUTH_OF_STILE_Y` still serves `_bank_trip`'s own coarser decision.

### The hunt is LEASHED to TILE_HUNT — why the falcon kept getting lost (2026-09-15, live)

USER, mid-run: "We somehow lost our falcon, because we clicked a kebbit and didn't retrieve it. We
have to quick falcon the guy again". The mechanism, MEASURED with a freeze-frame off `/npcs` and
`/player` at the moment the run stopped — **the falcon logic was never at fault**:

- The player was at **(2364, 3581), 24 tiles from `TILE_HUNT` and outside the pen**, standing among
  **Wild kebbit / Copper longtail** (the net-trap ground west of falconry), with **Dark kebbits at
  (2369, 3580) and (2367, 3587) that are also outside the pen**.
- `_nearest_npc` measured from the **player**, not from the hunt tile, and this species lives on
  both sides of the fence. Every throw and retrieve walks the player a few tiles toward its target,
  so "nearest kebbit" **re-centres the search further out each action and the error compounds**.
  Nothing pulled it back — there was no leash.

Two endings, both observed within six minutes:

1. Drift into region 9272 → `golem_crafting.trip_leg` called it `walk_in` and walked back over the
   stile **mid-retrieve**, abandoning a falcon sitting on a kill. It is then never collected and is
   lost — and **the glove STAYS in the weapon slot** (measured: glove worn, varbit 14192 == 0, no
   Gyr Falcon anywhere in the scene), so `glove_worn()` still answered "we have a falcon" and the
   routine threw into nothing until `_throw_falcon`'s 6-failure gate re-hired. **Cost: 45s of spin
   + ~26s of empty throws + 500gp + the catch.**
2. Drift into region 9271 → no leg matched → **the run safety-stopped.**

The 45s spin is its own defect: `_retrieve_falcon`'s "catch waiting but the falcon is out of range"
branch walks to `TILE_HUNT`, **which is a no-op when we already stand there** — 54 iterations,
ended only by the falcon despawning.

The fixes, all in `falconry.py` unless noted:

- **`HUNT_LEASH_TILES` (14), anchored on `TILE_HUNT`, not on the player** — a leash that moves with
  the player is not a leash. 14 is the pen: flooding the collision map from `TILE_HUNT` gives 389
  tiles spanning x 2374–2393, y 3576–3604, at most 14 Chebyshev from `TILE_HUNT`; the out-of-pen
  kebbits measured live sit at 19–24, so it separates them cleanly.
- **The falcon is NOT leashed** and no longer bounded by `maxKebbitDistance` (a throw distance,
  which it never was): `FALCON_SEARCH_TILES` (20, from the player), because a kebbit can run before
  it dies and the catch is still worth collecting.
- **`_walk_back_into_leash` runs AFTER the retrieve and BEFORE the throw** — retrieving outranks the
  leash, since walking home first is precisely what abandoned the falcon.
- **`FALCON_OUT_OF_RANGE_TRIES` (6)** bounds the no-op spin, then treats the bird as lost.
- **`falconry.PISCATORIS_REGIONS`** names the whole 2×3 region block around the pen
  ({9271, 9272, 9273, 9527, 9528, 9529}); `trip_leg` maps any of it to `walk_in` (or `return` when
  done). Being in the neighbourhood is a WALK, never a stop. Note the two older comments in
  `falconry.py` above `FALCONRY_REGIONS` are a record of this same bug being fixed at the SYMPTOM
  level twice (widening the region constant, after a two-hour "stuck at falco" stop) — the leash is
  the cause-level fix, and the region set is now defence in depth rather than the fix.

**VERIFIED LIVE the same session**: restarted with the character still standing at (2364, 3581) —
the position that had just safety-stopped the run. It logged `leg=walk_in region=9271`, walked back
to `TILE_HUNT` in **10s**, resumed hunting and completed the trip (pouch full, teleport home, bank,
re-equip, mine) with no drift, no lost falcon and no stop.

### Bones are DEFERRED and burst-dropped (2026-09-15, VERIFIED LIVE)

USER: "Seems the fur collecting is rather slow in golem crafting ... Like letting bones accumulate
until 1 slot left and we drop them all". MEASURED first, off three live golem fur-trip logs from
that morning (`logs/gc_live_0915_{004101,043927,121222}.log`), taking each hunt action's cost as the
gap to the next one — do not re-derive:

| action | n | mean cost | share of hunt time |
|---|---|---|---|
| throw | 110 | 4.03–4.63s | ~60% |
| retrieve | 56 | 3.30s | ~20% |
| **bone drop** | **57** | **2.60 / 2.69 / 3.26s** | **17–22%** |

The longest hunt was 582s for 30 catches, of which **94.7s was bones**, one at a time. The drop
COUNT is not reducible (one bone per catch, and the inventory is where they land) but its per-drop
overhead is: one bone per `hunt_step` is one bone per ENGINE LOOP ITERATION, each paying an
`/inventory` read, a tab raise, a 1.6s confirm wait, a `human_delay` and the hand-back — ~2.7s of
wall clock for a single click. Every drop line in all three logs read `(1 held)`, which is the tell
that nothing was ever accumulating.

`falconry._drop_junk` now lets them pile up and sends the lot through **`controller.drop_items`** —
the primitive `gathering`/`granite_3t`/`vale_totems`/`shipwreck_salvaging` already share, so the
three lessons baked into it are not re-learned here: it raises the inventory tab itself (the
2026-09-14 "50+ shift-clicks landed on the equipment panel" bug), holds SHIFT **once** across the
burst (a re-tapped SHIFT writes a machine-regular down-to-click gap into `EVENT_KEYBOARD`), and
verifies the FIRST drop so an account with shift-click-drop switched off falls back to the
right-click menu instead of dropping nothing twenty times over. At `DROP_CLICK_DELAY` (0.12–0.28s,
the recordings' own inventory cadence) a burst is **~0.4s/bone against ~2.7s** — on that 582s hunt,
~95s becomes ~12s.

**The threshold is 4 free slots, not the user's literal 1, and the three extra slots are
load-bearing:**

- golem_crafting's fur trip ENDS on fur **spilling** into the inventory (`_hunt_done`) — the only
  signal the fur pouch has reached 28. Let a bone take the last slot and the spill has nowhere to
  go, the signal never fires, and the trip runs to its 25-minute time cap instead.
- it must sit above falconry's own `bankAtFreeSlots` (3) so `_hunt` drops before it decides it is
  full, rather than walking a load of bones to Castle Wars. The drop check is therefore ordered
  BEFORE the bank check; once no junk is left, the fur and meat that genuinely filled the inventory
  are what trip the bank, which is the intended trigger.

Costs ~3 bones per burst out of ~19, at the same ~0.4s/bone either way.

**Leftover bones ride home and are BANKED, not dropped** — `banker.deposit_inventory` deposits one
distinct name per shift-click, so the ~13 still held when a trip ends cost exactly one click and no
hunting time. The visible consequence is a growing Bones entry in the bank; dropping them before
leaving would cost ~5s per trip for nothing.

Pinned by `test_falconry.py`'s pure-predicate tests on `junk_drop_due`/`junk_names` (threshold above
`bankAtFreeSlots`, never a `None` meat name) and the one-`drop_items`-call-per-pass assertions.

**MEASURED LIVE 2026-09-15, first burst:** `4 slots free -- dropping 15 Bones in one pass`,
13:44:56.697 → 13:45:07.138 = **10.44s for 15 bones, 0.70s each**, every one landing (no "did not
drop" warning, so shift-drop worked and the right-click fallback never engaged), and straight back
to throwing on the next loop. Against the 2.60–3.26s/bone measured that morning that is **~33s
saved in a single burst**. The realised rate is 0.70s/bone rather than the predicted ~0.4s because
each click costs its flight plus `DROP_CLICK_DELAY`, not the delay alone — still a **4x** cut, and
~60s per trip at ~29 bones.

**Not measured, and the obvious next cut if the hunt is still too slow:** a MISS spends the full
`THROW_RESOLVE_S` (2.6s), because the only confirm is the catch varbit and a miss never sets it —
~50% of throws paying a whole budget, worth ~30s a trip. The miss chat lines are in the recording
("...but just misses catching it.", "...too quick for you.") so a pushed `CHAT_MESSAGE` could end
the wait early, but `await_outcome`'s `shorten=` keys on event TYPE with **no predicate**, so any
passing public chat would cut a real catch's wait short too. Needs a predicate on `shorten` first —
do not bolt it on without one.


## Current state of routines/golem_crafting.py

**2026-09-14 mining rewritten to mining_guild_iron's single-tile chain (NOT yet run live).**
USER: "use the tile i stand on right now and keep mining the three adjacent nodes. exactly like we
did with mining_guild iron"; "we click after each xp drop right now in mining we should just await
node despawn like mining_guild". `mine_phase` now stands on ONE tile and rotates the rocks
cardinally adjacent to it, swapping the instant one DEPLETES; `find_mining_target` and
`_mine_swing_pending` (nearest-rock targeting + a per-XP-drop reclick gate) are deleted.

MEASURED from the human's own hand-played stretch in `recordings/2026-09-14_164323_golem-carve-
baseline.jsonl` (t=277-346s) -- do not re-derive:

- Expert mining gloves: a rock survives the FIRST ore and depletes on the SECOND. Ore drops land
  **1.80-1.83s** apart (3 ticks), the first **1.4-2.0s** after the click, so a rock is **~3.6s**.
  The momentum proc only adds to that (one +56 XP / 3 sunstone drop observed).
- A depleted rock is **"Rocks" id 11390** -- the same spent id iron uses -- and respawns in
  **5.32-5.46s** (9 ticks), over six depletion->respawn pairs. Three rocks at 3.6s is a 10.8s
  rotation, so one is always up. This is the user's point: with three in reach, never walk.
- Live ids are **62393 and 62394** (model variants), fixed per tile, both spending to 11390.
- CONFIRMED live off `/player` + `/objects` 2026-09-14: standing on **(2598, 2244)** puts rocks
  west (2597,2244) 62393, north (2598,2245) 62394, east (2599,2244) 62393, nothing to the south.
  That tile is now `locations.mining` (it was the monolith at 2599,2248, an unwalkable tile); the
  three rocks are DISCOVERED from it at runtime, so moving the spot is a one-line config change.
- The human's own waste, same capture: a rock depleted at t=284.6, they clicked one two tiles
  away and walked, and the next ore did not land until t=289.5 -- 4.9s for a 1.8s swap.

Shape: `discover_rocks`/`pick_rock`/`mine_rock` mirror `mining_guild_iron`'s (round-robin over the
rocks that are up, oldest-spent first; `mine_rock` waits on XP_GAINED/OBJECT_DESPAWNED/
INVENTORY_CHANGED confirmed by the uncached `/object/at`). The chain holds `tick()` across rocks
(`MINE_CHAIN_MAX_S`/`MINE_CHAIN_MAX_ROCKS`) because mgi measured every hand-back at 1.0-1.5s, and
checks safety/pause/`break_due` itself every iteration. `simulate_mistakes=False` on the rock click
is load-bearing, not a style choice (mgi_live6: a near-miss beside an adjacent rock is a Walk-here
one tile off, and then the other two rocks are out of reach). Position is checked before discovery,
since a bank return is ~60 tiles out. All of the above was CONFIRMED on the first live run, below.

### The fur trip's fairy ring: DIALLED, never "Last-destination" (2026-09-14, live)

**The bug that stranded the run.** `falconry._leg_fairy_ring` clicked "Last-destination", which the
2026-08-23 recording showed the human using because it happened to be AKQ for them. That is
MUTABLE PER-ACCOUNT STATE. Live it was dialled elsewhere, so the trip landed in **region 12582**
(a camphor-tree island ~800 tiles from Piscatoris) and safety-stopped with "not on any known leg".
A one-click fast path that silently goes to the wrong continent is not a fast path.

**The dial, MEASURED live against the open interface** — "Configure" on the ring opens group 398:

| widget (CW/CCW) | varbit | letters |
|---|---|---|
| `398.19` / `398.20` | 3985 | **A D C B** |
| `398.21` / `398.22` | 3986 | **I L K J** |
| `398.23` / `398.24` | 3987 | **P S R Q** |

Each dial is 0-3; one clockwise click adds 1 (mod 4), so three forward is one backward and no dial
ever needs more than two clicks. **`398.26` (Confirm) carries the live destination NAME as its
text** ("Invalid location" when the combination spells nothing) — that is the verification signal,
and it has already paid for itself: the first attempt had dial 2 as the plausible "ILJK" and the
button read back "Cave south of Dorgesh-Kaan" (AJQ per the travel log), which is how the real
order ILKJ was found. Without the guard that would have been another silent trip to the wrong
place. Verified end to end: dialling AKQ made it read "Piscatoris Hunter area" and confirming
landed at (2319, 3619), region 9272 == `REGION_FAIRY_LANDING`.

**The travel log (group 381) was tried first and rejected.** Its rows carry a "Use code" action and
read beautifully — `381.30` is "Kandarin: Piscatoris Hunter area", `name` "A K Q" — but it is a
SCROLLING list whose widget bounds are content-space: the Piscatoris row sat at canvasY 841 against
a panel ending at 848, so the click landed on background and the menu offered only "Cancel".
Driving it means solving scroll position first. The log also has a **Search** action (`381.7`),
which the USER points out can be typed into; that remains a viable simpler route if the dial ever
proves awkward — it was not taken because the dial is already verified and needs no typing (and
`KT` uppercases everything, see the firmware notes).

### Wyrmscraig's collision map is UNUSABLE; shortcuts are the real connections (2026-09-14, live)

MEASURED with `worldmap.reachable_from` across the whole area: Wyrmscraig is fragmented into
disconnected pockets of **1 to 841 tiles**. The cathedral the slayer ring's cavern exit lands
beside is 123; the **mining tile's own component is 41**; the **bank anchor (2587, 2260) is a
1-tile island**. `travel.walk_to` refuses without a route, so it cannot cross the region at all —
the routine only ever worked because its short hops are direct clicks (`walk_to_exact_tile`),
never routed walks. USER: "It should take the agility shortcut just ahead".

`_travel_toward` layers three strategies: route when the map has one, else take a shortcut, else
hop by minimap. Three things it got wrong first, each measured:

- **A distance heuristic picks the wrong shortcut.** From (2542, 2213) the Rocks at (2551, 2209)
  score 4 tiles WORSE on Chebyshev (the goal is at y 2260 and the rocks sit north of the line), so
  "is it closer" rejected the only shortcut that works. The right question is whether its FAR SIDE
  opens a route: tiles just east of those rocks — (2554, 2209), (2555, 2209) — are in a 190-tile
  bank-routable component while nothing on our side is. `shortcut_opens_route` probes for that,
  which also rejects the Cave we just walked out of (one tile away, carries "Enter").
- **Ask and walk with the same adjacency.** It tested routability with `goal_adjacent=True` then
  walked with `goal_adjacent=False`; since the bank anchor is a 1-tile island, it decided a route
  existed while `travel.walk_to` answered "no walking route" once a tick until the cap stopped it.
- **Progress has to reset the attempt budget.** Each shortcut attempt spent one of
  `MAX_WALK_ATTEMPTS`, so the run safety-stopped *standing on the far side of the rocks*, having
  just made the progress the cap exists to detect the absence of. It now resets when the player's
  tile changed since the last attempt, and still gives up when nothing moves.

Live result: cathedral → Rocks climb → walk → bank → mine tile, unattended.

### Gear and the meat pouch (2026-09-14, user-reported)

- **`falconry._ensure_hands_free` strips the weapon, shield and glove slots and nothing put them
  back.** Correct going out (falconry needs both hands free), silently never undone coming home —
  so the trip returned unable to mine at all (no pickaxe) and at half rate if it could (the Expert
  gloves are what make a rock give TWO ores). They also sit in the inventory while unequipped, and
  the batch's slot budget (25 stones + chisel + gem sack + fur pouch = 28) has no room for them.
  `ensure_mining_gear` runs at the top of `mine_phase`, one item per call, and acts ONLY on an
  empty slot with the item actually carried so it never overrides a deliberate setup.
- **No meat pouch was carried**, so every catch spent a click dropping raw kebbit. `KIT_ITEMS` now
  includes one (bank memory confirms "Large meat pouch (open)", id 29464), `_ensure_pouch_open`
  takes a pouch name and is called for both, and the hunt's `drop_meat` decision asks what is
  CARRIED rather than what was configured. A missing meat pouch is in `KIT_OPTIONAL` — it is an
  optimisation with a working fallback, so it must not stop the run the way a missing teleport does.
  **Then the hunt switched to the Dark kebbit and the pouch became moot**: MEASURED 16 retrieves,
  16 bone drops, ZERO raw meat — it yields fur and bones only. `falconry.KEBBIT_SPECIES` carries
  `meat: None` for it and `KIT_ITEMS` asks that table whether a pouch is wanted at all.
- **The bank DEPOSITED the pickaxe and the mining gloves** (USER: "Oh shit we just deposited our
  rune pickaxe. And also our gloves"). The gear is loose in the inventory for exactly one bank
  visit — between the fur trip stripping it and `mine_phase` putting it back — and
  `BANK_KEEP_ITEMS` named "hammer" but neither a pickaxe nor mining gloves, which is precisely why
  the Imcando hammer came back and the other two did not. Mining then failed ten clicks in a row
  and safety-stopped with nothing in hand. The keep list is now derived from `MINING_GEAR` so the
  two cannot drift, and `_withdraw_missing_mining_gear` recovers at the open bank for an account
  it already happened to. **Watch the tier**: recovery matches on the substring "pickaxe" and took
  the Adamant one, which measured **4.1-4.3s per rock against the Rune pickaxe's 3.16s**.

### Which kebbit (2026-09-14, user directive)

USER: "It should actually catch dark kebbirs instead they are plentiful and faster to catch."
CONFIRMED off /npcs at the hunting spot the same minute: **3x Dark kebbit (id 5532)** in range
against 1x Dashing (5533) and 4x Spotted (5531). Dashing is the higher-tier fur but there is
usually only one of them. Costs golem crafting nothing — "Dark kebbit fur" is already in
`FUR_PRIORITY`, just a lower entry. `falconry.KEBBIT_SPECIES` keeps the NPC, fur and meat names
together so they cannot drift; `DEFAULT_SPECIES` selects.

### The slayer ring's destination dialogue is PAGED (2026-09-14, live)

USER: "On slayer ring there is dialog options to tp or a second menu that expand when hovering
tele". The ring's right-click has a "Teleport" entry, and it opens a dialogue whose first page
does NOT hold Wyrmscraig:

```
page 1:  1 Stronghold Slayer Cave | 2 Morytania Slayer Tower | 3 Fremennik Slayer Dungeon
         4 More options...
page 2:  1 Tarn's Lair | 2 Dark Beasts | 3 the Wyrmscraig Cavern | 4 Previous options...
```

The single-page scan was looking for an entry that was never going to be on the page it read, so
the return leg logged "no dialogue option containing 'wyrmscraig'" once a tick with the pouch
already full. `_choose_dialogue_option` now pages through "More options..." (bounded by
`DIALOGUE_MAX_PAGES`, and it never re-presses a page-forward that did not change the options), and
`DIALOGUE_S` went 3.0 → 6.0 so the budget covers a page turn. Both the left-click and right-click
paths go through it. Live: teleport → cavern → surface → bank, unattended.

### Dropping needs the inventory tab up (2026-09-14, live)

USER: "It doesn't have inventory up so It can't drop". `falconry._ensure_hands_free` taps "E" to
reach the worn-equipment interface on the way in and nothing switched back, so every shift-click
landed on the equipment panel. MEASURED 50+ attempts on one bone. `_drop_loose` now raises the
inventory tab and CONFIRMS the item left — the loop was possible at all because it returned True
unconditionally.

**2026-09-14 FIRST LIVE RUN of the single-tile mining + chisel burst — both work.** MEASURED over
two full trips: **20 rocks, mean 3.16s** (min 2.34, max 5.01; the spread is momentum procs giving
1-3 ore), perfect three-rock rotation, every swap logged `in place (cursor already on it)`, **zero
walking**, a 25-stone batch in ~45s. Discovery matched the predicted ids on the first read.
**Chiselling while walking is CONFIRMED** — an external sampler caught all five cores converting
with `moving=True` as the player walked (2598,2244) → (2603,2249); the routine's own log said
"player was still" only because it sampled `moving` once, right after the hop click and before the
walk began (fixed: it now samples every pair and reports `walked during it`). Three wastes found
and fixed the same day:

1. **~7s dead at the end of every batch** (6.7s and 6.8s, both trips): the 25th stone fills the
   inventory to 28/28, the game refuses the rock's last ore, the rock never depletes and
   `MINE_ORE_BUDGET_S` is burned. The budget was only checked BETWEEN rocks — one rock too late.
   `mine_rock` now takes `stop_when` and ends the rock the moment the batch is full.
2. **~25s walking back to the stand tile after banking** — one attempt per call plus an engine
   hand-back, three attempts to arrive. `_return_to_stand` now retries inside one call
   (`MINE_RETURN_BUDGET_S`), logging the gap per attempt. **Pacing is load-bearing**: without a
   pause between attempts the loop spun 1.6M times when the walk returned instantly (caught by a
   test, not in production).
3. The chisel burst ran at 0.78s/core; deliberately slowed (USER: "we don't have to be that fast
   when chiseling we still have time") to the ordinary fitted click model rather than
   `tick_cycle`'s 3-tick-granite fast path — there is no tick to hit here, only the ~5s walk.

**2026-09-14 chisel burst, run during the walk to the plinth (NOT yet run live).** USER: "why
didnt we chisel while moving to the plinth?"; "1 tick per core so we should be able to chisel all
5 before we are at the plinth". MEASURED record-by-record from the same recording (the human's
five cores at t=16.8-27.1s):

- A core is **two clicks**: `WIDGET_TARGET` ("Use" on the chisel, widget 9764864 child 1) then
  `WIDGET_TARGET_ON_WIDGET` on the sunstone. Item selection is **client-side, no packet** (the
  fact granite_3t's knife selection rests on), so only the second click costs a tick.
- The conversion is on the **same tick** as that second click -- chat, +10 Crafting xp and the
  inventory change all carry `dTick: 0` on four of five cores.
- The stone converts **in its own slot** (`usedSlots` stayed 28 throughout), so slot positions
  resolved before a burst stay valid for the whole burst. This is what makes a burst possible.
- **No repeat action and no cooldown.** The human's 1.79-3.02s per core was their own click
  latency (chisel click on tick N, stone on N+1 or N+2). One core per tick is the ceiling and
  getting there is purely an input-speed problem.
- Animation 7202 runs continuously across the burst and gates nothing.

**Why it wasn't already happening: `chisel_phase`'s `isIdle` entry gate.** `isIdle = !moving &&
anim == -1 && target == null` (CombatProvider), so it returned every tick for the whole ~9-tile
walk and the first core only went out on arrival. It was also not protecting anything -- the
per-core confirmation is what stops a re-click landing on an already-converted stone. Gone.

Shape: slots resolved ONCE, stones ordered **nearest the chisel first** (tick_cycle's fitted reach
model puts a 42px hop at 86ms against 205ms at 120px -- that ordering is the difference between
one core per tick and one per two), pairs fired back to back through `tick_cycle.timed_click` at
`PRACTISED_FLIGHT_SCALE` with **no HTTP between them**. `mine` joined `TICK_CHAIN_FROM` so the
burst starts on the same tick as the hop toward the plinth. A post-burst settle
(`CHISEL_BURST_SETTLE_S`) is load-bearing: `/inventory` is tick-cached, and an under-count there
would make the next tick chisel the difference a second time and overshoot `CORES_PER_TRIP`.

**Chiselling WHILE WALKING is the user's claim and is in no recording here** -- the human stood
still at (2595, 2254) for all five. So the first burst that runs while moving spends ONE pair,
confirms it (`CHISEL_MOVING_PROBE_S`), remembers the answer in `gc_chisel_moving_ok` and logs it
loudly; a negative answer just falls back to chiselling on arrival, which is what the isIdle gate
did anyway. **That log line is the first thing to read after the next live run**, along with the
burst line (`N pair(s) in Xs -> M core(s), player was moving/still`).

**2026-09-06 event-driven pass + falconry fur trip (spec `docs/superpowers/specs/2026-09-06-golem-
crafting-events-and-falconry-design.md`, plan `docs/superpowers/plans/2026-09-06-golem-crafting-
events-and-falconry.md`; NOT yet run live).** Every measured wait below (side ~11.8s, first
progress bump +2.1s, chisel cadence, bank confirms) is kept as the BUDGET but is now awaited on
the pushed signal: `VARBIT_SUBSCRIPTIONS` covers the golem varbits 15728-15746 and the falcon's
14192, `_await_side_done`/`_await_golem_complete`/`_await_click_echo` take `ctx`+`since` and
return on VARBIT_CHANGED/MENU_CLICKED confirmed by `/golem`, chisel clicks chain inside one tick
(`CHISEL_CHAIN_MAX_S`), `tick` chains phases (`TICK_CHAIN_FROM`/`TICK_CHAIN_MAX`, `break_due`
checked between them -- pass it the breaks config, it takes one) and `_stuck_watch`
(`STALL_WARN_S`/`STALL_STOP_S`) keys on `_progress_signature`. The shared helpers live in
`ControllerAddon/outcome_waits.py` (`await_pre`, `await_long` with fidgets, `tick_pause`,
`varbit_in`, `chat_has`, `walk_budget`), lifted from mastering_mixology.
**The Goat Pit is gone; fur comes from falconry.** `hunt_phase` and every goat/Telekinetic-Grab
helper were deleted; `_run_bank_trip` routes to `fur_trip_phase` when the bank holds no usable
fur (skipping the fur search entirely when the pouch is stocked). The trip is dispatched on LIVE
position (`trip_leg`), user decisions: kit (`KIT_ITEMS` = Ardougne cloak, plus the configured
return teleport, default slayer ring; coins up to `furTrip.hireCoins`) withdrawn at the open bank
and deposited on return; out via the cloak to the monastery then the fairy ring's
Last-destination (AKQ) and falconry.py's own legs; hunt with `falconry.hunt_step` until the fur
pouch reads full (28); home via the slayer ring's Wyrmscraig entry, which lands in the
**cavern** (y = surface + 6400, `CAVERN_MIN_Y`), so `_leave_cavern` clicks loc 62220 "Cave"/Exit
-- its tile is unknown offline, recorded into `gc_cavern_exit` on first sight, and
`CAVERN_SCAN_LIMIT` bounds the search. `get_config` forces `safety.stopOnInventoryFull` off.
First live session worklist: the cavern exit tile, the slayer ring's dialogue option text, the
fur pouch "(open)" name, and whether `Last-destination (AKQ)` is what the ring offers on this
account. Everything below this paragraph predates the rewrite and is kept for its measurements.

As of the last session, this routine was extensively live-debugged and left in a working state:
mines Sunstone rocks to a budget, chisels cores, crafts golems on either of Wyrmscraig's two
plinths (tracked by object id, not position — see that file's own comments for why), inserts
cores/fur, loots gems, and cycles bank ↔ mine ↔ craft on its own. Several other routines
(`slayer.py`, `mining.py`, `blast_furnace.py`, etc.) exist and were not touched this session —
don't assume they share golem_crafting.py's specific fixes (sticky targeting, inventory-change
reclick gates, etc.) unless verified.

**craft_phase was rebuilt 2026-08-23 from a hand-played recording** (`recordings/2026-08-23_
194302_golem-carving`) after it failed live, and everything below is MEASURED — do not re-derive
it by reasoning:

- **One side takes ~11.8s** (11.9/11.9/11.7/11.7 across four sides). The first `progress` bump
  lands **+2.1s** after the click even from a tile you already stand on; bumps then arrive every
  ~2.4s (values 2→4→6→8) and the side completes with the chat line "You finish crafting the golem
  from this angle." `GOLEM_SIDE_TIMEOUT_S` was **4.0s** — under half the action it was timing.
- **A timeout here is destructive, not merely slow.** `carve_plan` re-picks a side from the
  player's new position on the next tick and walks away, interrupting a carve that had genuinely
  started and resetting its progress to 0. That is why the old value produced an unbroken retry
  loop rather than just a sluggish routine. Because `_await_side_done` resets its deadline on
  every progress bump, a real carve can never reach the timeout — **every timeout is a click that
  produced zero progress**, never a slow carve. Useful when reading logs.
- **The side carved is the tile you walked TO, not the tile you clicked from** — confirmed 3/3 by
  resolving `GOLEM_2_{NORTH,EAST,SOUTH,WEST}_PROGRESS` against `runelite-api-1.12.36.jar` and
  matching each against the walk destination in flight. The game queues the interaction and
  performs it on arrival, which is what makes clicking mid-walk safe. The human clicks the object
  **0.6-1.0s** after the walk click, never waiting to arrive.
- **A bare left-click on the plinth gets hijacked by whatever is on top of the aim pixel.**
  CONFIRMED live with the recorder running alongside the routine: a failing click was logged as
  `Walk here -> DoveHorn (level-115)` instead of `Shape-golem`. Roughly **30-40% of carve attempts
  fail this way** in a populated world. `/last-action` reports what a click actually resolved to,
  so a wrong action is provable in ~1.5s instead of inferred after the timeout.
- **The fast retry is capped at ONE per side, and the cap is load-bearing.** Retrying immediately
  re-clicks essentially the same pixel; the routine locked into a 20-attempt loop clicking
  (1093,766) while the clickbox centre sat at (1100,759), on a tile that had carved fine three
  minutes earlier. The old timeout wait was **accidentally load-bearing** — it let the camera and
  the world drift so each retry aimed slightly differently. A second consecutive hijack therefore
  falls through to the full wait on purpose. Never "optimize" that budget upward.
- **The hijacks are OTHER PLAYERS CAMPED ON THE SIDE TILES, and the aim was never wrong.**
  RESOLVED 2026-08-23 by dumping a frame (`GET /screenshot`) and marking the reported clickbox
  centre on it: the crosshair sits correctly on the plinth, but other players' models are drawn
  over it. `GET /players` at the same moment had `DoveHorn` standing on the south plinth's exact
  east tile — the same player, camped there for 30+ minutes of logs — and `M0T1S` beside the
  north plinth. Measured failure rate by side over 110 attempts: **east 67%, west 53%, south 12%,
  north 8%** (48% overall). The east/west skew is simply where those particular players were
  standing; it is not geometry, and a future run with a different crowd will skew differently.
  This also explains the 20-attempt lockup — a parked player never moves, so the geometry the
  retry depends on never drifts.
  **The fix is to stop relying on the default left-click action**: `click_menu_action` (right-click,
  read `/menu`, pick "Start-golem"/"Shape-golem"/"Insert-core" by name) is immune to whatever is
  drawn on top, which is exactly why `chinchompa_hunting.py`'s `recover_dropped_trap` uses it for
  ground items on tiles chinchompas wander across — see that routine's note on the asymmetry
  between a bare click and a menu selection. NOT yet implemented; the retry path above is only a
  mitigation.
- **The click escalation is now three tiers, and each tier exists for a different failure.**
  (1) bare left-click with `refresh_target`+`refresh_legs` — fast, human-looking, and what works
  when nothing is in the way; (2) ONE fast retry when `/last-action` proves the click resolved
  wrongly; (3) `click_menu_action` picking the option by name, which is the only tier that beats a
  player standing on the pixel. Tier 3 was added after the routine HALTED live 2026-08-23 21:04 —
  five consecutive failures on the south plinth's east side, safety-stopped and down until
  restarted, because a player was stacked on our own tile and no amount of waiting made them move.
  VERIFIED live: bare click hijacked -> fast retry hijacked -> menu selection landed and the carve
  proceeded, with zero menu misses once tier 3 re-resolved its position (see the
  `click_menu_action` gotcha above for the mistake that cost a deploy).
- **Known limitation in that mitigation: `gc_side_hijacks` is not keyed per side.** It only resets
  on a click that was NOT hijacked, so once any side hijacks, the next side's FIRST hijack already
  reads as "again" and skips its own fast retry — observed live 2026-08-23 20:50:44, where the
  west side's first attempt fell straight through to the full wait. This is safe (it errs toward
  the slow, proven path) and merely leaves value unclaimed: in practice the fast path helps about
  the first hijack after each success rather than one per side. The fix is to key the counter by
  side, or clear it when the planned side changes — deliberately not done unsupervised, since the
  budget cap is the thing standing between this and the 20-click spin it replaced.
- **HISTORICAL (goat machinery deleted 2026-09-06, see the top of this section): a Goat Pit fur
  run legitimately stopped golem production for ~15 minutes.** MEASURED
  2026-08-23 22:23-22:38: bank fur exhausted -> `hunt_phase` -> 166 Telekinetic Grab casts before
  "pit is now filled with goats -- harvesting", then a 46-tile Shortest Path walk back to the
  mine. Nothing is wrong during that window and the routine recovers on its own. Worth knowing
  before diagnosing a "stall": any watchdog keyed on golems-per-hour needs a threshold above this,
  or it fires every time fur runs out. A log that stops GROWING is the unambiguous stuck signal;
  golem count alone is not.
- **`Insert-core` needs the SAME escalation as a carve — fixing only the carve moves the failure
  here.** LIVE HALT 2026-08-24 02:17: with carving fixed, the identical hijack relocated to the
  one unprotected click on the same object. Signature: click positions alternating between two
  values across five attempts (the click resolves to "Walk here", the player moves, the next tick
  walks back). It strands a WHOLE golem — all four sides carved, progress 5, only the last step
  failing. Now escalates the same way, VERIFIED live 02:39 completing a golem that would have
  stranded.
- **ROOT CAUSE of every "starting golem ... never registered" halt: NO FUR. `Start-golem` is not
  offered at all when you are out of fur** — identified by the user 2026-08-24 after seven of my
  hypotheses had died. Everything fits: fur is inventory-wide so BOTH plinths refuse at once; the
  game gives no rejection message so the option is simply ABSENT from the right-click menu
  (`['Cancel','Mark Safespot','Examine','Walk here']` every time); it strikes only at progress 0
  because a part-carved golem can still be Shape-golem'd without fur; and a restart appears to fix
  it only because restarting forces a bank trip that refills fur. CONFIRMED on the fix's first
  deploy: the bank held **15 Wyrmscraig goat fur** while the routine carried none.
  Two things conspired. `has_start_materials` checks sunstone and cores and **never fur**. And
  `gc_fur_stock` is a local estimate this file's own comments already warned "can read >0 while
  the pouch is genuinely empty" — its one authoritative correction, `GOLEM_CHAT_NOT_ENOUGH_FUR`,
  only ever fires on **Insert-core**, so nothing catches an out-of-fur condition at the start
  step. craft_phase now treats a start refusal WITH sunstone and cores in hand as exactly that:
  zero the stale estimate and go to bank_phase (which restocks, and falls through to hunt_phase
  when the bank is empty too).
  **Lesson: when the game silently declines to OFFER an action, the missing ingredient is one the
  precondition check does not cover.** Read the full recipe before theorising about clicks — the
  absent menu entry was saying "you cannot do this", not "you missed".
- **Start-step failures: what was RULED OUT before the cause was found, so nobody re-derives it.** Three halts at
  "starting golem", progress 0 (21:04, 22:04, 02:00, 02:27, 02:34). Each of these was killed by
  measurement, not reasoning:
  - *Bad aim point* — NO. A frame dump with the reported centre marked shows the crosshair sitting
    squarely on the plinth (an Empty plinth renders as a dark hexagonal socket; it is easy to
    mistake for bare ground at low zoom).
  - *Missing materials* — NO. 15 Sunstone + 4 cores present at the time of failure.
  - *Another player occupying the plinth* — NO. `/players` showed nobody adjacent.
  - *Wrong start side* — NO. East and west failed identically, so `choose_start_side`'s new
    `avoid` set (added the same night) does not address it.
  The one condition every failure shared: **inventory at 28/28**. Unproven as a cause. Pit spikes
  are now shed before both mining and crafting (`_shed_pit_junk`), which is justified on budget
  grounds anyway — leftovers were also capping mining at 15 sunstone against the 20 budget. If a
  halt recurs on a NON-full inventory, that hypothesis is dead too and the next place to look is
  ObjectProvider's clickbox reporting or an unidentified Start-golem precondition.
- **One specific tile, (2597, 2257), intermittently fails to project — the north plinth's EAST
  approach tile.** MEASURED 2026-08-24 over a 2.5h run: **10 of 10** `couldn't walk to ...` walk
  failures were on that tile and no other, across four separate occurrences (03:37, 04:01, 05:30,
  06:09). `walk_to_exact_tile` returns False only when `get_ground_walk_point` reports the tile
  not on screen — yet checking `/ground/walk` for that exact tile seconds after one failure
  reported `onScreen:true` at (1150,621). So it is transient and tile-specific, not camera-wide.
  Survivable today (the approach back-off switches to the other plinth and production continues),
  but it is the cleanest remaining lead in this routine: something about that one tile's
  projection fails while its three neighbours never do. Start at `get_ground_walk_point` /
  `CombatProvider`'s `/ground/walk`, not at the routine.
- **Process note worth heeding: six fixes were made to what looked like one problem, and each
  relocated the failure instead of removing it** (carve timeout -> hijack retry -> retry guard ->
  menu escalation -> stale aim -> stillness -> menu-miss degrade -> side avoidance -> insert
  escalation). That pattern is the signal to stop patching and question the model. Every tier
  built re-aims at the SAME pixel `/object/at` reports, so when that pixel is unusable, all tiers
  fail identically — better retries cannot fix a bad target.
- Insert-core was observed succeeding from the **west** tile while `/golem` reports the south
  plinth's `insertSide` as `north` (core consumed 0.3s after the click, too fast to have walked).
  Either the insert side is not a standing requirement or `insertSide` means something else. Not
  resolved.

## Current state of routines/karambwan_fishing.py

Written 2026-09-06 from the user's recording of ONE full loop
(`recordings/2026-09-06_083723_karamwan-fishing.*`, 3m56s, 19 actions) -- read its narrative
before changing a budget here; every id, option text and landing time in the module header is
from it. **Not yet run live.** USER's loop, verbatim: "click fishspot -> wait for full inventory
-> load up fishing barrel -> click fishingspot wait until full again -> go into equiped item and
right click karamja gloves -> use the gem mine teleport -> deposit all fish and empty barrel ->
right click equiped quest point cape -> teleport -> take fairy ring DKP -> click fishspot".
- **Phases are derived from live state every tick (`derive_phase`, pure, table-tested)**:
  underground (y >= 9000) is the gem mine -> `bank` until no fish and the barrel was emptied this
  trip, then `cape`; region 10804 is the Legends' Guild -> `ring`; region 11568 is DKP -> `fish`
  or `to_bank`; anything else safety-stops as lost. Nothing load-bearing lives in
  `ctx.state.data` beyond `kf_trip_emptied`.
- **There is no Fill leg -- the barrel stores every catch on its own** (USER 2026-09-06, after
  the recording: "we no longer have to fill the barrel it will automaticly pick up all the fish
  so when we have full inventory we go to deposit as per usual"). So a full inventory means the
  barrel filled up first, and the trip banks straight away; the recording's Fill click and its
  mid-inventory top-up, `barrel_phase` and the `kf_barrel_count` running estimate are all gone.
  The barrel is still **emptied at the deposit chest** -- that is where its fish are banked --
  and `barrel_room()` survives only as the secondary confirm that the Empty-to-bank landed.
- **Full inventory = the game's own "You can't carry any more Karambwan." chat line** or 0 free
  slots on INVENTORY_CHANGED; `fish_wait` holds the tick between catches in human-length still
  segments with a fidget, dismisses a level-up dialogue itself (the engine's stray-dialog check
  cannot run while the tick is held), re-clicks the spot after `IDLE_RECLICK_S` of sustained
  `/combat.isIdle`, and safety-stops after `IDLE_STOP_AFTER` re-clicks that caught nothing.
- **Worn teleports go through the equipment interface** (`controller.open_equipment_tab`, new,
  confirmed on widget 387.1; `EQUIPMENT_TAB_KEY` "e" is this account's bind) and the right-click
  menu: gloves 387.22 "Gem Mine" (landed +3.2s), cape 387.16 "Teleport" (landed +2.4s), each
  awaited on PLAYER_STOPPED + the destination region/plane. A worn item's LEFT-click is Remove,
  so these are the one place the "inventory teleports are a left-click" rule does not apply.
- **Deposit chest** loc 10530 at (2842,9382), 6 tiles from the landing: interface 192, the fish
  are one right-click **"Deposit-All"** on the karambwan in grid 192.24 (-24 fish +0.3s), the
  barrel one **"Empty-to-bank"** on it ("You empty the barrel." +0.2s), ESC closes (192.1 is the
  recorded Close button, the fallback). Item names come from `/widget/children`'s `itemName`.
- **Fairy ring**: matched by name, "Last-destination (DKP)" clicked from up to 16 tiles (the
  human clicked from 12 and let the game walk; LOADING +6.5s); when that entry is missing,
  `_dial_dkp` Configures and turns the three dials on varbits 3985/3986/3987 to (1, 2, 0) =
  D-K-P via interface 398 children 19-24, learning which arrow increments from the first click
  that changes a value, then confirms on 398.26. The dial fallback is reasoned from the cache,
  not recorded.
- Wired into `ROUTINE_FILES` and `RoutineChoice` (`KARAMBWAN_FISHING`) -- client rebuild
  needed before it appears in the panel. The human also fired a harpoon special attack (widget
  160.36) once; not implemented.
- **First live run 2026-09-06 (kf_live1, ~5 min, one full loop + a second trip, stopped by F1):
  the loop WORKS end to end.** MEASURED: fish to full 110s (24 fish), Fill 0.4s, the 4-fish
  top-up then 28s more fishing, gloves click -> deposit phase 4.6s, deposit-all + empty + close
  3.4s (6.3s when the chest still had to be walked to), ring click -> DKP 8s. Three fixes came
  out of it: (1) `controller.get_widget_children` returns a LIST -- the first cut read a dict and
  crashed on the first deposit; (2) **a teleport's landing signals (busy varbit 12393 -> 0, the
  animation ending) fire on the same tick the LOADING screen starts, when the bridge answers
  nothing** -- the one HTTP confirm they trigger fails and `await_outcome` then sleeps to its full
  budget (5.4s lost on every cape teleport), so `_await_landing` chunks the wait at one game tick
  and re-reads `/player` at each chunk start; this applies to EVERY teleport wait in the codebase
  that confirms on `/player` -- `[!] bridge unreachable: GET /player` right after a teleport click
  is the tell; (3) the first barrel Fill was withheld (hover saw nothing under the cursor) and its
  menu fallback found no menu either -- the blind inventory-tab tap after a teleport can be eaten
  by LOADING, so `_ensure_inventory_tab` confirms on `/widget/active-tab` (3 = inventory) and a
  withheld Fill re-asserts the tab and left-clicks once more before the menu.
- **Every menu-swapped action is ONE LEFT CLICK now, hover-gated (2026-09-06, not yet run
  live).** USER: "basicly all things we do in karam fishing ive custom menu swapped so a simple
  left click will do, even the fairy ring will default to last destination. both teleports in
  inventory left clicks and deposit all and empty barrel both left clicks." `_click_option`
  left-clicks with `expected_action=<option>` so `/hover` proves the swap is on THAT target,
  and only a WITHHELD click falls back to the old right-click menu -- four sites: the worn
  gloves/cape, Deposit-All, Empty-to-bank, the ring's Last-destination. The gate is
  load-bearing rather than decorative: **an un-swapped WORN item's left click is `Remove`**, so
  a swap reset by a game update would unequip the gloves mid-run instead of teleporting. The
  ring passes `interface=False` (a world object keeps the UI-blocker check and the default aim
  offset); everything else is an interface click. `simulate_mistakes=False` on all of them --
  banker.py's rule, and here the neighbour of the karambwan in the deposit grid is the BARREL.
  The ring's `Configure` stays a right-click by definition: it is the not-swapped path. Watch
  the log for `left-click is not 'X' here -- using the menu`, which names a missing swap.
- **The fishing wait is spent TABBED OUT (2026-09-06, not yet run live).** USER: "such a long
  fishing time any human doing this would just be watching youtube or something ... we can be
  late tabbing back in sometimes like variance 10-40 sec". `fish_wait` wraps the old loop
  (now `_fish_loop`): physically Alt+Tab away, fish, then `notice_delay_s()` -- 2-6s most of
  the time, `AFK_LATE_CHANCE` (0.33) of the time the user's 10-40s -- and Alt+Tab back.
  Four things make it safe, and each one is load-bearing:
  - **No input at all while unfocused.** `_fish_loop(afk=True)` skips the level-up SPACE and
    the fidget; both would land in whatever window is now in front. Nothing is lost -- the
    engine's own `check_stray_dialog` runs before the next tick, once we are back.
  - **The tab back in is VERIFIED, `AFK_FOCUS_TRIES` (3) attempts, then a safety stop**, and
    it returns the extra `"unfocused"` result so `fish_phase` hands back to the engine instead
    of chaining the bank trip into another window. The engine does not call `tick()` again
    while unfocused (its "Don't blind-click into a backgrounded window" gate), so a tab back
    in that lands somewhere else would stall the run silently -- this is nmz.py's own lesson,
    and `alt_tab`/`wait_for_focus` moved out of that routine into `controller.py` so there is
    one copy. `wait_for_focus` gained `focused=False` for the tab-OUT half: a machine with
    only RuneLite open swallows Alt+Tab, and the routine then fishes focused rather than
    believing it is away.
  - **`AFK_MIN_FREE_SLOTS` (8)**: the last few catches are ~20s, not worth tabbing away for.
  - **Engine-level safety and break scheduling sit behind the same focus gate**, so neither
    fires while we are away -- accepted, since the window is one inventory (`FISH_MAX_S` 300s,
    not nmz's 20 minutes) and a fishing spot in Karamja is not a situation they would act on.
- **`/state`'s `dialogOpen` DOES NOT SEE THE PLAIN MESSAGE BOX (group 229), and that box
  swallows the tab hotkeys (2026-09-06, MEASURED live with it on screen).** USER: "there is a
  dialog in the way so we cant press e ... inventory full dialog pops up, we have to press
  space to confirm to use keyboard shortcut". `/state` read `dialogOpen: false` at that exact
  moment; `/widgets/dump` carried `229.3` "You can't carry any more Karambwan." and `229.4`
  "Click here to continue", and `/widget?group=229&child=4` answered `clickable: true`.
  `GameStateProvider.dialogOpen` only ORs the NPC/player/sprite dialogue widgets, so **the
  engine's own `check_stray_dialog` is blind to this box too** -- as is every routine relying
  on that flag. `karambwan_fishing._continue_box_open` reads 229.4 by id AND text; `_dismiss_
  dialog` SPACEs on either signal and confirms. NOT fixed in Java: adding 229 to `dialogOpen`
  would need a client rebuild and would start pressing SPACE at 229 boxes in every routine
  (blast_furnace deliberately ignores its dispenser one), so it is the user's call.
- **Nothing else could have cleared it, which is the second half of the bug.** `_fish_loop`
  returns `"full"` the moment free slots hit 0 -- BEFORE it reaches its own `_dismiss_dialog`
  -- and `tick()` chains the next phase inside the same tick, so `check_stray_dialog` (which
  runs only between ticks) never gets a turn either. **Any routine that chains phases inside
  one tick has this gap**: the engine's stray-dialog check covers the tick boundary only.
  `_open_equipment` therefore clears the box before the hotkey, and keeps a blind SPACE +
  one retry for the next box neither check knows about -- a SPACE with nothing open does
  nothing, while an unread box is indistinguishable from a dropped keypress from Python.

## Current state of combat_core.py / routines/combat.py (was routines/slayer.py until 2026-09-18)

**Split 2026-09-18** (docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md): the file
is now `combat_core.py` (git mv), `tick` is `fight_tick`, and `routines/combat.py` is the
panel-config wrapper the "Combat" dropdown entry runs. Everything below still applies to it
unchanged. Two latent bugs fixed in the move: `BANK_NO_PROGRESS_MAX` was referenced but never
defined (NameError after two no-restock bank trips), and `bracelets.reequip` was read but
never exported. `routines/slayer.py` is a stub until sub-project 2. The same pass moved
banker's return-teleport leg runner to `legs.py`; doing so fixed a latent `NameError` --
banker's old `_pick_destination` called `_now()`, a name that never existed in `banker.py`,
in the POH destination-picker path that has never run live.

**Bank trip return-to-spot rebuilt 2026-09-07 (USER: "a few bugs with slayer/combat mainly the
banking and getting back to the spot we previously was at", and specifically "ends up stuck at
Ferox"). Traced statically and pinned in tests -- NOT yet run live.** Three defects fed the one
symptom, and the shape they share is that **the only save/restore machinery in the whole
banking path lived inside `_bank_via_ferox`**:

- **A trip to a nearby bank never walked back at all.** `bank_up`'s first branch opened,
  deposited, withdrew, closed, `return True`. A bank is accepted out to `maxBankDistance` 40, so
  the routine could walk 40 tiles off the task and just carry on fighting next to the bank.
- **The Ferox path could be hijacked into stranding you there, and this is the reported one.**
  `FEROX_BANK_NAMES` is a strict SUBSET of `DEFAULT_BANK_NAMES`, and `ConfigExporter` exports
  neither `bankNames` nor `maxBankDistance` (grep over `src/main/java` finds nothing), so both
  defaults always apply. Once a trip aborted AFTER the teleport had landed -- the chest not
  visible yet, or `open_bank` failing, both plain `return False` while standing at Ferox -- the
  NEXT tick's `bank_up` matched the Ferox chest 40 tiles wide open on its own nearby-bank
  branch, banked there, and returned True: skipping `use_fountain` AND the walk home, and
  orphaning the saved location in `ctx.state.data` forever. Reproduced in
  `test_bank_up_stays_on_the_ferox_path_after_a_teleport_has_already_landed`, whose pre-fix log
  is the tell: `no bank found at Ferox Enclave` immediately followed by `walking to Bank chest
  (dist=4)`. `TELEPORTED_KEY` latches the moment the teleport lands and keeps the trip on the
  Ferox path until it completes.
- **The saved tile was popped BEFORE the walk was attempted**, so one walk that gave up partway
  discarded the destination with no possibility of a retry -- and a cross-map return is exactly
  where `WALK_MAX_STALLS` (a CUMULATIVE budget of 4 over the whole route, not a run of
  back-to-back stalls) runs out. It logged "continuing from here" and the routine fought on from
  wherever it stopped.

The shape now: `banker.RETURN_LOCATION_KEY` is recorded ONCE at the top of every trip, before
anything moves the player, and survives both a multi-tick trip and a failed walk.
`banker.return_to_spot(ctx)` is idempotent and safe to call every tick (it returns immediately
with no anchor recorded); `slayer.tick` spends the retry AFTER prayer/food maintenance -- so a
return trip cannot starve eating -- and BEFORE loot/safespot/engage, so nothing commits to a
fight at the bank it is walking away from. `RETURN_WALK_MAX_ATTEMPTS` (3) is the ceiling and it
surfaces as a SAFETY STOP, not a shrug: being stranded is the reported bug, and CLAUDE.md's own
standing lesson is that a silent loop is worse than a crash.

- **The walk home is OPT-IN for the nearby-bank path (`returnToSpot`), and that default is
  load-bearing.** `bank_up` has three other callers and a universal walk-back regresses one of
  them: `firemaking.py` banks every inventory and then walks to a FIXED starter tile of its own
  (`advance_starter` sets `fm_positioned = False`), so walking back to the end of the last burn
  line first is pure waste. `blast_furnace.py` and `golem_crafting.py` reach `bank_up` only on
  their teleport-fallback paths, and golem_crafting's own comment already depends on the Ferox
  walk-back ("it walks the player straight back afterward"). So the default is "walk back only
  if this trip teleported", which reproduces every pre-2026-09-07 caller exactly; slayer passes
  `returnToSpot=True` to extend it to the nearby-bank path. Both halves are pinned
  (`test_bank_up_does_not_add_a_walk_home_for_callers_that_never_had_one`,
  `test_the_ferox_path_still_walks_home_without_anyone_opting_in`).

Two adjacent facts found in the same pass and deliberately NOT changed:

- **`maintain_safespot` refuses to walk back past `SAFESPOT_MAX_RETURN_DISTANCE` (30) while
  banks are accepted out to 40**, so for a bank 31-40 tiles out even the safespot fallback
  declined, warning "this looks like a stale/wrong mark". Left alone because the 30 exists for a
  MEASURED reason (an instanced-area safespot stored hundreds of tiles away, 2026-08-06) and the
  return anchor now lands the player back on the pre-trip tile anyway, which is at or beside the
  safespot -- the two limits no longer have to agree.
- **`travel.task_location` has ZERO callers.** The Controller Settings panel's per-target Task
  Location dropdown is resolved Java-side (`ConfigExporter.resolveTaskLocations`) and shipped on
  `GET /slayer-config` as `taskLocations`, and nothing in Python has ever read it. So the
  dropdown is a dead setting today; wiring it up as the return anchor was offered and the user
  chose the pre-trip tile instead. (The teleport chooser added 2026-09-07 does NOT use it
  either -- it aims at the recorded pre-trip tile, see "Teleporting home from a bank trip".)

**`engage_target` ends `if not target: return` with no log and no counter**, so "stranded
somewhere with nothing to fight" is indistinguishable from a healthy idle routine in the log --
which is why this went unnoticed as a feeling rather than a visible failure. Still unfixed; a
stuck-watch keyed on "in-combat-enabled config, no target picked for N consecutive ticks" is the
obvious follow-up. Before 2026-09-07 there was no `tests/test_slayer.py` at all and `bank_up`
had no test coverage whatsoever, despite being the most live-fixed routine in the repo.

## Current state of routines/chinchompa_hunting.py

Written 2026-08-20 (design: `docs/superpowers/specs/2026-08-20-chinchompa-hunting-design.md`).
Box-traps Carnivorous chinchompas with 4 traps and picks every trap back up via the new
`before_break` hook. First run live 2026-08-21 in the Red chinchompa hunting ground (an
underground cave, and **not** instanced — forward world→screen conversion works there, so
`walk_to_exact_tile` is safe; verified before the first run). That session replaced most of the
guesswork in this routine with measurements:

- **Interacting with a trap is a plain left-click, not a menu selection.** The user swapped the
  client-side menu entries so the left-click action is correct in every trap state, so
  `click_menu_action`/`target_hint`/`TRAP_STATE_ACTIONS` are gone. The old `target_hint` guess was
  wrong anyway: a trap's **displayed name depends on its state** — a full one is a `Shaking box`,
  a set or collapsed one is a `Box trap` — so it missed on every single retrieval and only worked
  via the option-name-only fallback. Nothing matches on the name any more; identity is by object
  id (`classify_trap`).
- **A trap object appears in `/objects` ~1.9s AFTER the trap leaves the inventory** (≈3 game
  ticks, the setup animation), measured over three consecutive lays. Confirming a lay on the pack
  decrement alone — which is what `_await_trap_laid` used to do — hands control back inside that
  window, the next tick reads the tile as still empty, and the routine lays a *second* trap on it.
  See `_await_trap_laid`; it now waits for the object and nothing else.
- **Checking a full trap is TWO animations with a deceptive ~0.66s idle gap between them:** 5212
  (check / pick up) then 5208 (the game re-laying it automatically), ~6s end to end. `animation
  == -1` is therefore NOT a valid "action finished" signal on its own — only *sustained* idle is
  (`_await_action_finished`, `ACTION_SETTLE_S`). Returning during that gap let the next tick
  interrupt the re-lay, so the trap stayed in the pack and the tile stayed bare; over a few
  catches every trap ended up in the inventory with nothing on the ground.
- **A left-click keeps a *set* trap** (picks it up, no re-lay) but *re-lays* a full or collapsed
  one. That asymmetry is why `retrieve_trap` confirms on "the tile no longer holds the state we
  clicked on" rather than on the trap reaching the inventory — only one of the two outcomes
  involves the inventory at all — and it is what lets `before_break` still clear the ground.
- **An unreachable plan tile used to be retried forever, and it was the single biggest throughput
  drain found.** `block_tile` keys off the game's refusal chat message, which never arrives when
  the player cannot physically get onto the tile to attempt the lay — so `lay_trap` just logged
  "didn't reach" and gave up, every tick, at ~10s an action. CONFIRMED live 2026-08-21: two plan
  tiles were walls, burning ~200 actions an hour and holding the catch rate at roughly half
  baseline. `lay_trap` now counts consecutive failed walks per tile and blocks after
  `UNREACHABLE_BLOCK_THRESHOLD` (4), and the plan rebuilds onto a spare offset; catch gaps went
  from 28-96s back to ~10s immediately. The threshold is deliberately not 1 — a chinchompa
  standing on a tile blocks the walk transiently — and arriving clears the streak. Root cause of
  the bad geometry: repeated restarts let the plan adopt scattered leftover traps until the
  anchor drifted somewhere with walls for neighbours, so a **clean start from a deliberate anchor
  is worth doing** after a run of restarts.
- **Known, deliberately unfixed: collapsed traps sometimes time out before they are reset.**
  `work_queue` ranks `failed` last, so a collapsed trap waits behind catches, dropped-trap
  recoveries and empty slots; some wait long enough to time out and drop as an item, which then
  costs two actions (recover + re-lay) instead of one (reset). MEASURED 2026-08-21 over 115
  consecutive actions: 65 catches, 33 resets, 9 lays, 8 recoveries — so the waste is ~7%.
  Promoting `failed` above `empty` would recover most of it, **but** `full` + `failed` already
  account for 98 of those 115 actions, which is exactly the condition that starved the empty slot
  before (12 catches on 3 traps of a 4-trap plan). Left alone on purpose: a 7% loss is not worth
  re-risking a whole trap slot, and both previous reorderings made on reasoning rather than
  measurement regressed throughput 45-85%. Measure over a long run before touching this.
- **Do not try to reclaim the animation tail — it was measured and is strictly worse.** The
  obvious speed idea is that once the re-laid trap object is back on the ground (~1.3s before the
  animation ends) it has already left the pack, so the re-lay is committed and the next action
  can start. Tried 2026-08-21: the catch cycle went from ~9.7s to a **median of 18s**, with 6
  dropped retrieve clicks and 7 lay timeouts where the full wait had none. The trap being placed
  is not the same as the player being free — a click issued while the player is still animating
  is dropped by the game, and the retries cost far more than the wait saves. Throughput here is
  bound by the game's own ~6s two-part animation, and the routine is already saturated (~0.5s
  idle between actions), so there is very little left to win.
- **Traps left on the ground time out and despawn**, dropping as a `Box trap` *ground item*
  (`/ground-items`, not `/objects`) — MEASURED as a steady leak of ~1-2 traps an hour, since a
  set trap that never catches anything is never touched and so eventually times out. Recovered by
  `dropped_traps_by_tile`/`recover_dropped_trap`, which pick the item back up rather than paving
  the tile with a fresh trap from the pack. That pickup goes through the right-click menu (`Take
  Box trap`) and **must not** be "simplified" to a bare left-click: that was tried first and
  failed on its first live attempt (the item despawned instead), because these items lie on tiles
  the chinchompas wander across and a click on a shared pixel resolves to Attack. Note the
  asymmetry with `retrieve_trap`, which *does* use a bare click — the menu entries are swapped
  client-side for trap OBJECTS, but a ground ITEM still has an ordinary Take option.
- This is also why `block_tile` now
  refuses to blacklist a tile one of our own traps is standing on: striking such a tile off the
  plan means never returning for that trap, and it despawns. A run lost two traps exactly that
  way before the double-lay driving those refusals was fixed.

Two things it does differently from the other routines, on purpose:
- **Trap identity is a tile, not a remembered trap.** It re-reads `/objects` every tick and
  classifies whatever box trap is standing on each of its own planned tiles — which also settles
  ownership (a trap on any other tile belongs to someone else) and survives the `ctx.state.data`
  reset, since a plan built from scratch adopts traps already laid near the anchor.
- **No banking phase at all.** Box traps and red chinchompas both stack, so the inventory
  genuinely cannot fill — there is nothing to bank, and adding a bank cycle would be dead code.

## Current state of routines/mahogany_homes.py

Written 2026-08-27 from four human recordings (`recordings/2026-08-27_{174045,175143,180346,
181548}_mahogny-homes{,2,3,4}.*`) plus the decompiled reference plugin
(`data/decompiled/mahogany-homes-plugin/`, sideloaded Plugin Hub jar, documentation only).
Runs Adept-tier contracts: NPC Contact spell (widget 218.113, confirmed against both the
recordings and `runelite-api-1.12.33.jar`) requests a contract with no dialogue to navigate,
`MahoganyHomesProvider.java` tracks which home/tier is active purely from the assignment chat
message (no varbit exists for this), teleport tablets/Xeric's talisman get the player to the
right city, and a plain left-click on each hotspot's fixed object id (one id per home per slot,
stable across its whole Remove-then-Build lifecycle) does the work, relying on the user's own
client-side menu swap rather than any hijack-escalation machinery. **Not yet run live** — every
widget/interface id this depends on was verified against the recordings and the real
`runelite-api` jar rather than guessed, but `HOTSPOT_CLICK_COOLDOWN_S`/`TURN_IN_CLICK_COOLDOWN_S`
(both first-draft guesses, see the routine's own module docstring) and the overall phase timing
need a live session to confirm, the same way golem_crafting's own click cadence was corrected
after its first live run.

**Three gaps closed 2026-08-28, two of them things the reference plugin had that the original
port missed** — worth knowing before porting from a decompile again, because all three were
found by diffing our data against the reference rather than by reading our own code:
- **`npcUpstairs`** (reference `Home.isNpcUpstairs`) — true for JESS and NORMAN only. A full
  re-diff of every other Home field (object ids, per-slot HotspotType, ladders, and the
  slot↔varbit alignment against `Hotspot`'s authoritative object→varbit map) found **zero**
  other differences across all 12 homes. `teleportItems` is deliberately not ported (the routine
  uses its own simplified `CITY_TELEPORTS`) and `hint` is a non-functional UI string.
- **The plank sack** (reference `MahoganyHomesPlugin.countPlankSackVarb`). Item 24882, one
  varbit per plank type — 960/18171, 8778/18172, **8780 (teak)/18173**, 8782/18174 — capacity 28.
  The original "no plank sack, to avoid golem_crafting's stale fur-pouch estimate" reasoning was
  simply wrong: the count is a **varbit read fresh from the client**, not a running local
  estimate, so it cannot drift. Two facts MEASURED from the recordings, not assumed: `Fill` is
  only offered while a **bank interface is open** (same mechanic as `banker.fill_fur_pouch`, and
  the recording logs it as `CC_OP` on widget 15.3 slot 0), and the game **spends planks straight
  out of the sack with no Empty step** — varbit 18173 ticks 28→27→25→23 in step with each
  hotspot's cost, alongside the hotspot varbit, the build animation and the Construction XP drop.
  So sack planks count toward affordability; counting only loose ones banked with a full sack.
- **Which FLOOR a hotspot is on.** `/objects` only ever reports the player's current plane, so
  "not found" could not distinguish "upstairs" from "not close enough", and the routine guessed
  by right-clicking a ladder with both `Climb-up`/`Climb-down`. `MahoganyHomesProvider` now scans
  **every** plane of the loaded scene for the hotspot object ids and reports each hotspot's
  `location`/`plane`, so the direction is decided rather than guessed. Ladders are now chosen by
  proximity to the **hotspot**, not to the player — which matters for **NOELLA**, the only home
  of the twelve with two staircases and (per the user) a **disconnected** upper floor: "you have
  to fix one upstairs climb down take the other ladder fix that upstairs". Because `/objects`
  returns the whole scene on the current plane, the far half's hotspots are plainly visible from
  the near half, so work_phase would click an unreachable hotspot forever — and seeing the object
  is exactly what resets `mh_work_stuck`, so the safety stop never fired. Guarded by
  `HOTSPOT_UNREACHABLE_CLICKS`, scoped to `SPLIT_UPPER_FLOOR_HOMES` (NOELLA alone) rather than
  inferred from ladder count: two staircases do not imply a disconnected floor, and applying the
  guard everywhere would risk climbing away from a merely-slow build in eleven other houses. See
`docs/superpowers/specs/2026-08-27-mahogany-homes-design.md` for the full design and
`docs/superpowers/plans/2026-08-27-mahogany-homes.md` for the implementation history.

**Routing engine replaced 2026-08-29, later the same day as the session below.** Shortest Path
is gone — deleted along with roughly 700 lines of door/reachability heuristics it made
necessary (`_door_separates`, `_within_span`, `open_frontier_door`, `open_nearest_shut_door`,
the three-tier door escalation, and the live-HTTP `travel.reachable_set` flood fill this routine
used to call directly). `ControllerAddon/worldmap.py` — a world-wide per-edge collision map
covering all four planes, ported from EthanApi's GlobalCollisionMap and answering
`find_path`/`reachable_from` offline in milliseconds, no bridge call — is now the **only**
routing engine, and it fixes the exact gap Shortest Path had: it routes on plane 1+ (Shortest
Path's dumped collision map was ground level only). Doors are baked OPEN in the map by design
(`CacheMapDumper`'s `Exclusion` list), so a computed route goes straight through one; the door
question is answered afterward, from the route itself, by
`first_door_on_route`/`blocked_crossing_on_route` in `travel.py` — the first blocked crossing on
the route is very nearly the definition of "a shut door is here" on ANY plane, and it replaces
every one of the tiers named above with one idea. `travel.open_door_toward(ctx, goal)` is the
single call site every consumer below now uses. The one thing that did **not** move onto the
static map: `travel.can_reach` (object-interaction adjacency — "can I click this from here") is
still answered from **live** `GET /collision` flags, because the static map bakes an object's
own blocking into the same bits as a wall's and structurally cannot tell furniture from a wall
(the JEFF finding below). Every MEASURED geometry in this section — ROSS, JEFF, Barbara, TAU —
is still the evidence the design rests on; they are now also encoded as pinned fixtures in
`ControllerAddon/tests/test_worldmap.py` and `tests/test_travel.py` rather than living only
here. The bullets below describe the machinery as it was BUILT that day, on top of Shortest
Path; read them for the measurements, not for the current API names, which the "ONE call now"
notes inline correct where they matter most.

**Second live session, 2026-08-29 — three bugs, all MEASURED. Don't re-derive them.**

- **A widget text match MUST be scoped to a widget GROUP, or the chatbox answers for you.**
  `_accept_tea_if_offered` substring-matched `"cuppa"` over the whole `/widgets/dump`, and the
  chatbox (**group 162**, identifiable by its own All/Game/Public/Private tab labels) keeps
  `"Aaah, nothing like a nice cuppa tea!"` and `"You drink the tea, you feel energized!"` in
  scrollback for the rest of the session once any tea has been drunk. Chat history never clears,
  so the check returned True on EVERY tick and `tick()` never reached a phase handler at all --
  three clicks aimed at a chat line, then SPACE forever, with `dialogOpen` false throughout. A
  hard deadlock, not a slow path: the routine could not have made progress in any circumstance.
  Note the earlier screenX/screenY guard did NOT exclude these -- chat lines have real screen
  positions. Dialogue OPTIONS live in **group 219** (confirmed from recorder output,
  `widget 219.1 slot 1`); `DIALOG_OPTION_GROUP` is now what the match is scoped to. Verified by
  replaying both matchers against the live widget tree that deadlocked: old 3 matches, new 0.
- **A ladder is clicked at its own reported CENTRE. A tile-corner aim was tried on 2026-08-29 and
  REVERTED the same day** (user: *"revert the ladder to click center"*) -- it fixed one house and
  broke another, so don't reach for it again without per-ladder evidence. What is real is that an
  object's reported point is its **clickbox centroid** (`RuneLiteUtil.shapeCoords` ->
  `pointInsideShape`, which prefers the area centroid), and how useful that is varies by model:
  - MARIAH's upper-floor ladder: centroid `(1249,709)` vs the tile centre straight from
    `/ground/walk` `(1249,707)` -- the SAME point, i.e. the clickbox is effectively the whole TILE
    while the climbable model occupies only the top of it. A marked frame dump shows the point on
    bare floorboards. Right-clicking it returned ground options only
    (`['Cancel','Mark Safespot','Walk here']`) 13 times, then a safety stop.
  - ROSS's ladder 16683: centroid `(1263,316)` vs tile centre `(1360,386)` -- ~100px apart, and
    the centroid is squarely ON the model. Here the centroid was already right, and overriding it
    with a corner aimed a good click away; the rotation then spent the whole stuck budget.
  The user's fix for the MARIAH-shaped case is to **skew the camera** so top-floor ladders don't
  need a special aim at all. Do pass `offset=(0, 0)` -- `click_menu_action`'s default `(-7, +7)`
  nudge is DOWNWARD in screen space and is enough to slide off a small target.
- **Changing floor is progress and must clear `mh_work_stuck`.** With the corner fix in, the climb
  worked and the run STILL safety-stopped two ticks later: three attempts at
  `LADDER_CLICK_COOLDOWN_S` apiece had already spent the 15-tick budget. Keyed on the plane
  actually CHANGING (not on the menu click being taken) so an up-then-down loop cannot reset the
  counter forever. **The marker is not consumed on read** -- a climb takes a tick or two to land,
  so the tick right after the click still reports the old plane; consuming it there lost a descent
  that genuinely happened and stopped the run anyway.
- **`turn_in_phase` had no re-validation and no ceiling, and looped forever.** LIVE 2026-08-29 at
  ROSS's: the phase latched to `"turn_in"` one tick after a single hotspot was built -- work_phase
  moves there whenever `pick_next_hotspot` returns None, so one transient empty read is enough --
  and then talked to Ross every ~3s indefinitely while SIX of his seven hotspots still reported
  `needsAttention`. Nothing re-checked, so it could not recover. turn_in_phase now bounces back to
  `"work"` whenever we are at the home and a hotspot still needs attention (the mirror of
  work_phase's own "no hotspot data from out here" guard; the two conditions are mutually
  exclusive on the same data, so they cannot ping-pong), and `TURN_IN_MAX_CLICKS` (12, per home)
  safety-stops rather than talking forever -- **a cooldown only sets the RATE of an unbounded
  loop**, the same lesson as the teleport-tablet and NPC-Contact caps.
- **The door is resolved BEFORE the first hotspot click, not after five wasted ones.** USER
  2026-08-29: *"why when we come to the house do we not prioritise opening the god danm door,
  should be the first thing we click if its closed"*. `travel.walk_to` already resolves doors
  before stepping off -- but work_phase does not necessarily walk at all: `/objects` returns the
  whole scene on our plane, so a hotspot behind a shut front door is plainly VISIBLE and was
  clicked immediately, and the door only got resolved through the unreachable branch,
  `HOTSPOT_UNREACHABLE_CLICKS` (5) clicks and ~6s later. MEASURED live at MARIAH's: exactly that,
  twice in one contract. work_phase now calls `travel.open_door_toward` (the straight-line
  `open_doorway_to` this originally shipped with was later folded into that one route-based
  call, see the routing-engine note above) once per hotspot state (latched on the same
  `(objectId, value)` signature as the click counter), which costs nothing when the way is
  clear.
- **A door is opened by WALKING to it and then opening it, so the confirmation must budget the
  walk.** `DOOR_OPEN_WAIT_S` was a flat 2.0s, which is less than the ~2.4s of walking to a door
  four tiles off (one tile per 0.6s tick). USER 2026-08-29 on the loop at ROSS's: *"we clicked
  away before waiting for us to walk to the door and open it"*. The recorder settles it -- the
  FIRST click registered as `Open Door` on id 40178 and was good; the budget expired before the
  player arrived, and the two retries then resolved to `Walk here` because the player had moved
  off the pixel being re-aimed at. So a working click was reported as a failure and interrupted by
  its own retry, three times over. The budget is now
  `DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S`, capped by `DOOR_OPEN_WAIT_MAX_S` so a door
  that genuinely never opens still fails fast. This is the same rough edge the 2026-08-28 notes
  recorded as "seen once on the Varrock bank door, harmless, not yet tuned" -- it is not harmless;
  it loops. **Do not read `Pick-lock` in a door's actions as "locked and unopenable"** -- that was
  my own wrong first reading of this failure, and the click was landing fine.
- **WALKING CANNOT CHANGE FLOOR, and `turn_in_phase` did not know that.** USER-REPORTED
  2026-08-29 as "stuck upstairs": at ROSS's, with every hotspot built and the player still on
  plane 1, the phase saw "homeowner npc not visible" and walked at `standingLocation` -- which is
  ALWAYS reported at plane 0. The walker clicked the identical pixel (1144,724) about thirty
  times and the player never moved. work_phase's ladder handling could not help: it lives in
  `_approach_missing_hotspot`, and with no hotspots left work_phase does not run at all. The
  descent is now owned by turn_in_phase too, and counts against `TURN_IN_MAX_CLICKS` so it cannot
  loop. Which floor the homeowner is on comes from **`npcUpstairs`** (`_homeowner_plane`), not
  from `standingLocation.plane` -- the latter is plane 0 even for JESS and NORMAN, the two homes
  whose owner is upstairs.
  **General lesson: any "walk to X" recovery in this routine needs to ask whether X is even on
  our floor first.** Two separate loops this session were a cross-plane walk that could never
  succeed.
- **A travel unit test must not fall through to a live call.** The original concern here was a
  door test whose edge geometry did not resolve escalating to a Shortest Path HTTP request, so it
  passed or failed depending on what the running client happened to answer. `worldmap.find_path`
  is pure and offline (no bridge, no serial port, no client), which removed that failure mode by
  construction rather than by pinning test geometry around it -- see "Both Python test suites are
  now hermetic", below, for the fixture-based half of this (`travel.can_reach`'s own live
  `/collision` fallback still needs one).
- **Every climb resolves the doorway to the LADDER first, because the game accepts an object
  interaction it cannot path to and then silently does nothing.** USER 2026-08-29: *"there is a
  door we can open to get to the ladder"*, and *"just use line of sight that works on second
  floor"*. MEASURED at ROSS's upper floor: the player stood at (2617,3317), ONE tile from `Door
  1540` at (2616,3317) whose blocked SOUTH edge (flags 16416 = `BLOCK_MOVEMENT_SOUTH |
  BLOCK_LINE_OF_SIGHT_SOUTH`) was the only thing between them and the ladder at (2617,3315). All
  TWELVE `Climb-down` clicks registered in the recorder against the correct object -- the aim was
  never wrong -- and the player never took a single step. `_climb` now flood-fills from
  `worldmap.reachable_from` and asks `travel.can_reach` whether the ladder is reachable from
  there; if not, it calls `travel.open_door_toward(ctx, ladder)` (which routes to the ladder via
  `worldmap.find_path` and opens whichever door the route crosses) before ever clicking the
  ladder. VERIFIED live the same run: door opened, ladder climbed, contract turned in -- all
  inside ONE tick, replacing a loop that had ended three runs.
  This is also the general shape for upstairs: Shortest Path's route branch was useless on plane
  1 (its dumped collision map was ground level only), which is the reason `worldmap.py` exists at
  all -- `GET /collision` always worked fine on any plane, and now the offline map does too.
- **A safety-stop reason must not repeat the engine's own suffix.** `trigger_safety_stop` appends
  ". Automation paused -- fix the issue and restart." itself; the first turn-in ceiling message
  included that sentence too and printed it twice.
- **A "walk to X" recovery must pick X on OUR OWN FLOOR -- `standingLocation` is always plane 0.**
  Hit twice on 2026-08-29 in two different phases, so treat it as the standing rule rather than
  two bugs. In `work_phase`'s unreachable branch, MEASURED live at JESS's (the home where all
  seven hotspots are upstairs): the goal was `standingLocation`, Shortest Path answered the
  cross-plane request with a **`Climb-down Ladder` TRANSPORT**, and the walker rode it DOWN and
  away from the work -- `"using transport: Climb-down Ladder"` -> `"used the transport but didn't
  arrive where expected"` -> `"target is on a different plane -- can't walk there yet"`, then the
  whole cycle again, ~90s of climbing up and down per pass. It still finished the contract, so
  this shows up as waste rather than a halt, which is exactly why it is easy to miss in a log.
  The branch now walks to the HOTSPOT's own tile whenever `hotspot.plane == player.plane`, and
  falls back to the standing tile otherwise (the original outside-the-house case, where the
  hotspot's own tile is usually furniture and unroutable -- see Barbara's, above).
- **A click's COOLDOWN must budget the walk, because the game performs a queued interaction on
  ARRIVAL.** USER 2026-08-29: *"are we checking if we click remove on a furniture but we are a few
  tiles away do we count that as removed before we arrive?"* -- we were.
  `HOTSPOT_CLICK_COOLDOWN_S` (0.6, Remove) and `HOTSPOT_BUILD_COOLDOWN_S` (2.4, Build/Repair) are
  the ACTION's own duration and nothing else, so a click on a hotspot a few tiles off expired
  while the player was still walking. We re-clicked (re-queueing, sometimes re-pathing), the
  varbit never moved, and after `HOTSPOT_UNREACHABLE_CLICKS` such clicks work_phase declared a
  perfectly reachable hotspot "unreachable" and walked away. MEASURED at ROSS's: five
  `Repair Broken Range` actions ALL registering correctly in the recorder against the right
  object, zero varbit movement, then the unreachable branch. Now
  `base + distance * HOTSPOT_WALK_ALLOWANCE_S`, capped by `HOTSPOT_COOLDOWN_MAX_S`. This is the
  same omission as the door-open budget -- **whenever you time a click, ask whether the player
  has to walk there first.**
- **A door click needs no fallback tier any more, because it is answered from a real ROUTE, not
  a heuristic.** MEASURED that night at ROSS's: player (2614,3319) on the grass north of the
  house, Broken Range at (2617,3318) inside, and the only way in a door at (2610,3316) whose
  blocked edge (2610|2611) has BOTH of us to its EAST -- a straight-line "is this edge between us
  and the goal" test answers "not between us" and is wrong, because the door is a **DETOUR**, not
  a point on the direct line. That night's fix was a three-tier escalation
  (`open_frontier_door` -> `open_doorway_to` -> a blunt `open_nearest_shut_door` last resort) built
  because there was no route to ask and a detour is invisible to a straight-line test. All three
  are gone: `travel.open_door_toward` asks `worldmap.find_path` for an actual BFS route to the
  goal and opens whichever shut door the route crosses (`first_door_on_route`, see the
  routing-engine note above) -- a detour door is on the route exactly as much as a straight-ahead
  one, so the same ROSS geometry is found with the one call, for free, with no tier order to get
  right. VERIFIED live the same run: door opened at (2610,3316), hotspot 40164 completed, on to
  40165.
- **REACHABILITY IS DECIDED BEFORE THE FIRST CLICK -- click-then-discover was the whole problem,
  not the door logic specifically.** `/objects` returns the whole scene on the player's plane, so
  a hotspot behind a wall, a shut door or a ceiling is plainly VISIBLE -- and work_phase used to
  click it from wherever it stood, discovering it could not reach the thing only after
  `HOTSPOT_UNREACHABLE_CLICKS` failures (~20s). MEASURED over one full ROSS contract 2026-08-29:
  **15 of 27 hotspot clicks -- 56% -- achieved nothing**, across three stuck events. USER: *"every
  time we have to walk through a door now we have to get stuck on the outside 5 clicks in a row
  this is unacceptable."*
  A flood fill decides reachability up front instead: `worldmap.reachable_from(x, y, plane)`
  walks the STATIC map's cardinal edges offline (no HTTP call at all, any plane -- this is what
  replaced the live `GET /collision`-based `travel.reachable_set()` this routine originally called
  here) and `travel.can_reach(hotspot, reach, plane=player_plane)` answers whether the hotspot is
  reachable from that set, still checking the actual click-adjacency edge against LIVE flags
  (see the routing-engine note above for why that half stays live). `pick_next_hotspot(hotspots,
  reach, plane)` then prefers a hotspot we can actually get to. Two hard-won rules survive from
  that night, both still true of `can_reach` today:
  - **Count ALL EIGHT neighbours, not the four cardinals.** MEASURED upstairs at JEFF's: the
    player stood at (3241,3447), DIAGONALLY adjacent to the hotspot at (3242,3446) whose every
    cardinal neighbour is furniture. A cardinal-only test called it unreachable and the routine
    walked in place forever instead of clicking a thing it was standing beside -- strictly worse
    than the waste it replaced. USER: *"wtf it is reachable hes standing next to it"*. A diagonal
    is accepted only when one of its two orthogonal corner routes has an open edge (the game's own
    rule), not by an unconditional point-blank override -- an earlier override shaped that way
    PRODUCED the ROSS failure below by accepting a diagonal into a wall; it is gone.
  - **An empty fill means "cannot tell", never "nothing is reachable"** -- an un-rebuilt client or
    a tile outside the loaded scene must behave exactly as before, not refuse to work.
  The fill is cardinal-only on purpose, so it can only UNDER-report; the failure mode is the old
  behaviour, never a confident walk into a wall. And unlike Shortest Path it works on ANY plane --
  ground truth for why `worldmap.py` exists at all.
- **A hotspot can be genuinely unreachable with NO door anywhere.** MEASURED upstairs at JEFF's:
  of three hotspots needing attention one was walled off and there was no shut door in range at
  all. USER: *"there is no doors but we are on the second floor"*. Bare list order fixated on it,
  walked, and wedged the greedy walker; reachable work sat two tiles away. Hence
  `pick_next_hotspot`'s reachability preference -- and note it still returns an unreachable
  hotspot when EVERY one is unreachable, so a walled-off contract falls through to the door/walk
  recovery rather than reporting "no work left" and going to turn_in on an unfinished contract.
- **Both Python test suites are now hermetic (`_no_live_bridge` autouse fixtures).** `get_collision`
  reaches the RUNNING CLIENT when a test does not stub it, so a result depended on where the
  character happened to be standing -- the same door test passed alone and failed in a full run,
  and flipped between runs as the live player moved house. (`request_shortest_path` and
  `get_shortest_path_result`, the other two live calls this originally warned about, are gone
  along with Shortest Path itself -- `worldmap.find_path` needs no live call at all, which is what
  made the routing HALF of this hermetic by construction rather than by fixture; `get_collision`
  is what remains to stub, for `travel.can_reach`'s live fallback.) Mark a test
  `@pytest.mark.real_bridge_helper` (registered in `tests/conftest.py`) only when it is testing a
  controller bridge helper itself.
- **The reachability gate belongs at EVERY world-object click site, not one at a time.** The
  invariant is: **the game ACCEPTS a click on anything you can see -- through walls, through
  floors -- and then silently does nothing.** Fixing only work_phase's hotspot clicks moved the
  failure to the next ungated site rather than removing it. MEASURED live 2026-08-29 at ROSS's:
  player (2618,3315), ladder 16683 at (2617,3315), cardinally ADJACENT -- but (2618,3315) carries
  `BLOCK_MOVEMENT_WEST` (65664), so the shared edge is a wall. `menu-select Climb-up Ladder`
  registered THREE times in the recorder, the player never changed floor, and the run
  safety-stopped. USER: *"i mean at this point am i going to have to hardcode each door?"* -- no:
  none of the four failures that night were door-specific. Gated today: hotspots (work_phase),
  ladders (`_climb`). **Still ungated: the homeowner NPC in `turn_in_phase`.**
- **ADJACENCY IS NOT REACHABILITY -- the EDGE decides, and raw tile distance cannot tell the two
  cases apart.** Two live geometries the same night, identical at Chebyshev distance 1, opposite
  answers:
  - ROSS's, player (2617,3319) / Broken Range (2617,3318): the exterior wall runs on exactly that
    edge (y=3319 blocks south, y=3318 blocks north). Unreachable.
  - JEFF's upstairs, player (3241,3447) / hotspot (3242,3446): diagonal, every cardinal neighbour
    furniture but no wall on the edges. Reachable -- the character interacted fine.
  An earlier "the model never vetoes a point-blank click" override was added for the second case
  and PRODUCED the first (clicking through a wall); it is gone. `can_reach` now tests the movement
  bits on the shared edge for cardinals, and for diagonals requires one of the two orthogonal
  corner routes to be open -- the game's own rule. Accepting diagonals unconditionally was also
  wrong at ROSS's, where (2616,3319)/(2618,3319) are diagonal to the hotspot but behind the wall.
- **A reachable set is (x, y) with NO plane in it, and the floors share a coordinate space.**
  MEASURED at ROSS's: a plane-1 hotspot was judged "reachable" against a plane-0 flood fill purely
  because its x/y landed on a walkable ground-floor tile, so work_phase preferred it over the
  ground-floor hotspots and went ladder-hunting. `can_reach` takes the fill's `plane` and returns
  **None** for a cross-floor question -- the honest answer, since reaching another floor is a
  ladder problem, not a reachability one.
- **`pick_next_hotspot` ranks: reachable here (0) > another floor (1) > this floor but walled off
  (2).** The 0/1 gap is what stops the ping-pong. USER 2026-08-29: *"why do we click the door
  inbetween each remove / build"* -- with cross-floor ranked equal to reachable, it alternated
  ground-floor work with upstairs attempts, and each attempt re-opened ROSS's self-closing front
  door on the way to a ladder. Finish the floor, then climb once. Rank 2 is still RETURNED when
  nothing better exists, so a walled-off contract reaches the door/walk recovery instead of
  reporting "no work left" and turning in an unfinished contract.
- **A door that swings shut behind you cannot be confirmed by re-reading the doorway.** MEASURED
  at ROSS's front door (40178): the recorder shows EVERY click registering as `Open Door` -- eight
  across one session -- while the confirmation logged `still shut` and spent the whole budget, up
  to 6.9s each time. USER: *"this door is always closed, so when we open it it instantly closes
  behind us"*. Both confirmation loops now also succeed on `_crossed()` -- the player having
  crossed the door's blocked edge since the click. Getting through is the outcome; the doorway's
  state at poll time is not.
- **Do not leave an unconditional door resolution beside a reachability gate.** `_climb` kept an
  earlier `open_doorway_to(ladder)` call after the gate was added, so with the ladder ALREADY
  reachable it still ran the straight-line edge test -- which false-positives on whatever door
  shares an axis, at ROSS's the front door BEHIND us. That is what walked the character outside
  between each Remove/Build. USER: *"still doesnt make much sense since we can reach them so why
  would we step out"*. MEASURED from inside the house: `can_reach(ladder)` was True and the door
  was opened anyway. Door work belongs only inside the gate.
- **Shortest Path could not route on plane 1 at all -- this measurement is WHY it was replaced.**
  MEASURED with 20s waits, that night: every plane-1 target returned `active:false, done:false`
  and never completed, and a plane-0 target requested while standing upstairs came back
  `steps=1, SEARCH_EXHAUSTED` where the single "step" was the player's own current tile (its
  dumped collision map was ground level only). So upstairs there was no route, which meant the
  door recovery had nothing to work with and `walk_to` fell straight through to an obstacle-blind
  greedy walker -- CONFIRMED live: eleven identical `Walk here` clicks at canvas (554,331) with
  the player never moving, while the client displayed "destination could not be reached". Fixed
  2026-08-29 by `worldmap.py`, whose own module docstring opens with this exact measurement as
  its reason to exist: the static map covers all four planes, so an upstairs route is now no
  different from a ground-floor one.

**Live session 2026-08-30, first run against the global collision map. Nine routing/aiming bugs.
The single most useful thing to carry forward is the SHAPE four of them shared.**

- **A RECOVERY THAT SITS BEHIND AN EARLY-RETURNING GATE CANNOT RUN.** `work_phase` checks
  `travel.can_reach` and RETURNS when it is False. Three separate recoveries lived *after* that
  gate, in the `HOTSPOT_UNREACHABLE_CLICKS` branch — NOELLA's split-floor climb-down, NORMAN's
  "head for the hotspot, not the standing tile", and the ladder handling — so on the very homes
  they were written for they could never execute. They only ever fired when reachability was
  UNKNOWN (`None`), which is exactly why **their unit tests passed while the live case wedged**:
  the tests exercised the None path, not the False path. When adding a recovery, ask which gate
  returns before it, and write the test against the answer the live failure actually produces.
- **`standingLocation` is a per-home HINT, not a tile you can stand on** (LEELA's is solid on all
  four edges), and it is ALWAYS reported at plane 0 — so no walk should ever demand an exact
  route to it, and a walk aimed at it from an upper floor cannot succeed in any circumstance.
  `travel_phase` now climbs first when the planes differ.
- **Every loop needs a ceiling, and the ones that lacked them were the ones that hurt.**
  `travel_phase`'s walk branch and `work_phase`'s unreachable branch both logged nothing and
  counted nothing — two live stalls of 79 ticks and 46s with ZERO output, indistinguishable from
  a healthy idle routine. Both are now counted and safety-stop. A silent loop is worse than a
  crash: it looks like it is working.

**`open_door_toward` routes to an INTERACTION tile, not merely an adjacent one (fixed
2026-08-30, mh_live25/26).** ADJACENCY IS NOT INTERACTABILITY applies to the DOOR resolution
too, not just to walk goals: `goal_adjacent=True` routing stops at ANY cardinally adjacent
tile, and a neighbour on OUR side of a wall yields a route that crosses nothing — so no door
is ever found. MEASURED live at JEFF's: hotspot 39991 at (3241,3447), player (3242,3448); the
hotspot's north neighbour (3241,3448) is adjacent AND one step from the player but walled from
the hotspot (16416), so `open_door_toward` answered False 15 ticks straight — while the only
way in ran through the shut inner Door 11775 at (3238,3450) — and the run safety-stopped.
USER: "it doesnt want to open the inner door, opened the outer door fine". The Barbara's-bed
fix put `interaction_tiles` only in work_phase's WALK goal; the door primitive kept the blind
spot, and the walk half no-oped too (nearest interaction tile by straight-line distance was 1
tile away THROUGH the wall, inside `HOUSE_ARRIVAL_TOLERANCE`, so walk_to returned True without
moving). `open_door_toward` now routes to the nearest routable interaction tile (open LIVE
edge to the goal) and falls back to `goal_adjacent` only when live collision cannot tell.
VERIFIED live the same night at JEFF's and twice at JESS's: door found and opened on the FIRST
unreachable tick, each contract completed.

**A per-request ceiling reset in a phase handler dies with the phase (fixed 2026-08-30).**
`mh_contact_casts`' only reset lived in `request_contract_phase`'s hasContract branch -- but the
dispatcher auto-promotes `request_contract -> travel` on that same fact, so the handler never
ran again once a contract registered. The counter leaked +1 per contract and CONTACT_MAX_CASTS
(5) safety-stopped a HEALTHY session every ~5 contracts claiming "no contract ever registered"
-- twice in two runs (mh_live26 02:18:57, mh_live27 02:28:52), with /mahogany-homes showing the
new assignment fully parsed both times. (Run 26's stop was first misattributed to the bank
being out of planks; run 27 disproved that.) The reset now lives at the dispatcher's promote,
the moment a request provably succeeded. General shape: **a counter whose reset lives in a
phase handler dies when the dispatcher stops routing to that handler -- put the reset where the
success is DETECTED, not where the attempt is made.**

**Late 2026-08-30 session (mh_live28-35): six more live fixes, and the pattern most of them
share is CONTEXT — a recovery keyed to data from the wrong context cannot run when it matters.**
All are covered by pinned tests; details live in the code comments at each site.
- `turn_in_phase`'s Talk-to was the last ungated world click (CLAUDE.md had said so): twelve
  recorder-verified `Talk-to Jess` clicks, zero movement — she was behind shut doors. Now gated
  on `can_reach` with `open_door_toward(npc)` as the recovery, spending the same turn-in budget.
- travel_phase's climb-first branch searched the NEW contract's `ladderObjectIds` while standing
  in the PREVIOUS house (turned in upstairs at Norman's, next contract TAU) — found none, fell
  through to a cross-plane walk, safety-stopped with a staircase on screen.
  `_nearest_ladder_by_name` (name + Climb-action filter) is the fallback.
- `_open_door`'s confirm is MOTION-AWARE: the per-tile budget under-counts when the approach
  detours around furniture the static map does not know (JESS's (2622,3293) door, 2 tiles
  straight-line, 4+ tiles real — three timeouts in one night, every click recorder-verified
  good, and the re-resolution's next click CANCELLED the queued open each time). A route-length
  budget cannot fix what the map cannot see, so the wait now extends while the player is still
  moving and only stillness past the budget (or `DOOR_OPEN_WAIT_MAX_S`) spends it.
- `bank_phase`'s teleport-to-bank branch repeated travel_phase's whole 2026-08-28 history: flat
  sleep under the measured 3.8s teleport, then a LOADING-screen tick where /objects AND
  /inventory read empty -> "out of varrock teleport" with a tablet in hand. `_await_teleport`,
  an empty-inventory-means-cannot-read guard, and `TELEPORT_MAX_ATTEMPTS` — the three things
  the other branch already had.
- `travel.interaction_tiles` now filters neighbours that cannot be STOOD on (`FLOOR`-blocked;
  deliberately NOT the OBJECT bit — Barbara's live-verified approach tiles carry 256 and are
  pinned in tests). JESS's hotspot 40175 is furniture-ringed with one edge-open-but-FLOOR-blocked
  neighbour that became an unroutable walk goal: 15 ticks of "no walking route", safety stop.
  Diagonal-only hotspots stay `can_reach`'s job at click time.
- Tea = run energy: `request_contract_phase` used to cast NPC Contact 0.25s after the homeowner
  offered tea, walking out on the refill every contract (live energy 28.62% at that moment, then
  6%). Below `TEA_ENERGY_THRESHOLD` (50) it advances the turn-in dialogue (≤3 SPACEs) instead of
  casting over it. VERIFIED live: 6% -> 90.9%.
**JESS's upper floor is the measured stale-map hotspot** — live collision disagrees with the
EthanApi map there in three places (furniture at (2622,3292), hotspot ring at (2615,3296), the
north strip unroutable on the map but walked by players). Occasional stops at that house remain
possible until the map regeneration follow-up (the CacheMapDumper dump in
`ControllerAddon/map-output/`) is done.

**Overnight session 2026-08-30 (mh_live39-45, ~7h): six fixes, all pinned in tests, and the
biggest was a NEW stale-map zone.** Details live in the code comments at each site; the shapes:
- **The JEFF-exit pocket at (3242,3451) is the second measured stale-map zone** and it wedged
  TWO phases identically, three hours apart (travel_phase 07:12, bank_phase 10:28): the map
  funnels that whole neighbourhood through a corridor along y=3450 that live collision walls
  off whenever JEFF's self-closing front door's OPEN leaf rests across it — an open leaf offers
  no "Open" action, so the door recovery cannot see it, and avoiding the funnel tile leaves NO
  map route at all. The fix is `TRAVEL_STUCK_TELEPORT_AT` (5): a walk that provably cannot
  progress promotes to the city tablet even under the distance gate, in BOTH phases (a bank
  VISIBLE in the scene never reaches bank_phase's own no-bank teleport branch, so the escape
  has to live in its walk branch too). VERIFIED live in bank_phase on the fix's first tick.
  More stale pockets exist west of JEFF ((3236,3452), (3230,3450) route to nowhere on the map)
  — one more reason the map regeneration follow-up matters.
- **A found ladder is not a usable ladder**: in Falador the NEW contract's ladder ids resolve
  across the rooftops in a DIFFERENT building, so `_pick_ladder` succeeded and the by-name
  fallback never ran (`_routable_ladder` now discards a ladder with no route at all — a shut
  door keeps its route since the map bakes doors open, so door-blocked same-house ladders
  survive the filter). Plus `LADDER_WALLED_LIMIT`: the walled-off recovery had no ceiling.
- **The mh_work_stuck counter is motion-aware now**: NOELLA's working recovery (teleport, door,
  live-reroute walk, climb) spent its 15-tick budget on "still walking" defer ticks and the
  stop fired as the player reached plane 1. Walking after a recent ladder click holds the
  counter; stillness still counts up, so genuine wedges stop exactly as before.
- **MARIAH's ladder 11802 menu misses**: the clickable pixels sit 3-5px off the centroid in a
  camera-dependent direction (a fixed nudge is WRONG — the measured success was below-left
  while "model rises from tile" predicts above). A ground-only menu is a SYNCHRONOUS miss, so
  `_climb` retries inline with a widening probe ring instead of paying the cooldown per probe
  (~2.5s vs ~18s). `DOOR_FAILURE_MEMORY_S` also went 120s→1800s (JESS visits arrive ~6min
  apart, so the dead-door discovery was re-paid every contract) — note the memory only helps
  when an ALTERNATIVE candidate door exists; at JESS every route funnels through the dead one,
  so ~5s/visit remains there until the map regen.
- The session ended at 11:10 on a LEGITIMATE stop: the bank ran out of Teak planks after ~7h
  at ~40 contracts/hour (three trips, shift-withdraw yielding nothing, sack fill 0/28 with the
  per-item diagnostic). That stop worked exactly as designed.

**Hotspot clicks are a BURST, not a click, and the numbers come from the user's own recordings.**
MEASURED across four human sessions (n=1194 hotspot actions): median gap between clicks **0.20s**,
p25 0.18s, 339 of 500 bursts are more than one click on the same option (mean 2.46, max 8), and
Remove->Build median **2.06s**. The bot on the same activity managed 3.89s / 3.62s, because the
gate was a fixed wall-clock cooldown that always paid the worst case. So the gate is now a
SIGNAL:

- Keep clicking until the hotspot's varbit moves, then **carry straight on into the next action**
  (Remove -> Build is the same object id) rather than handing back to the ~1.1s engine loop.
- **Construction XP is the "the build landed" signal** (USER: "remove is nearly instant so we can
  keep clicking until we get experience drop") — Remove pays no XP, Build/Repair does, and
  `constructionXp` on `/player` is sub-tick. The varbit is the secondary "an action landed,
  re-aim" trigger.
- **Aim once, then click IN PLACE** (`click_human`), which is what the human's 0.20s cadence
  actually is — re-flying the cursor for every repeat costs 1.0-1.1s each. But **re-aim whenever
  the player has moved or the model changed**: standing still the projection cannot slide, moving
  it slides continuously. USER: "if we continuously click we gotta update the object x y otherwise
  while we move we click away".
- **Do NOT stop the burst because the player is walking.** The ROSS reasoning (re-clicking
  mid-walk re-queues and re-paths) applies to a re-AIMED click on a later tick, not to an in-place
  repeat, and the human recordings spam straight through the approach. A burst spent walking must
  also not count as a failed attempt, or the unreachable guard walks away from a hotspot we are
  still on our way to.
- NOT yet verified at scale: the best post-change sample is n=5.

**First long live session, 2026-08-28 — everything below was MEASURED, don't re-derive it.**

- **The active contract is read off the NPC dialogue WIDGET (231.6) on a GameTick, not off chat.**
  This is a verbatim port of the reference plugin's `checkForAssignmentDialog`, and it replaced a
  chat-event parse that could never work. The *assignment* does arrive as a GAMEMESSAGE and parses
  fine; the line that re-states an ALREADY-ACTIVE contract — the one that recovers state after a
  restart mid-contract — does not. Symptom: restart with a contract in hand, `hasContract` stays
  false, and `request_contract_phase` re-casts NPC Contact once per tick forever over a contract
  the account already holds. Three separate hypotheses died before the user said "why don't we just
  copy the mahogany home plugin, it recognises the contract" — all three were about the chat event
  (`ChatMessageType.DIALOG` being filtered out; RuneLite embedding the speaker in `getMessage()` as
  `Amy|You're currently on...`; the game's non-breaking spaces, which Java's `\s` does not match).
  Each was real, each was fixed, and none of them was sufficient. **The widget carries the full,
  untruncated, already-assembled text with no speaker prefix and no message-type question to get
  wrong.** Lesson for the next port from a decompile: when the reference plugin reads a different
  SOURCE for the same fact, copy the source, don't reconstruct the fact from a nearer one.
  `GET /mahogany-homes` now also returns **`lastChat`** — what the provider last parsed and whether
  it matched — added after the third dead hypothesis. Instrument the boundary sooner than that.
- **`WorldPoint.fromLocalInstance(client, local)` stamps the WORLD VIEW's current plane on the
  result — pass the plane explicitly when scanning planes you are not standing on.** This silently
  broke `MahoganyHomesProvider`'s all-planes hotspot scan in the one direction that matters: every
  hotspot found upstairs reported `plane: 0` while the player stood downstairs, so `work_phase` read
  "same floor as me", never tried a staircase, and clicked at a hotspot through a ceiling until the
  unreachable guard safety-stopped the run (CONFIRMED live at Larry's, Falador: `/objects` did not
  contain 40098/40099 at all, while the scan happily reported both at plane 0). Use the
  three-argument overload. This applies to ANY future multi-plane scan, not just this provider.
- **A tablet teleport takes 3.8s from click to region change** (varbit 12393 → anim 4069 → anim 4071
  → spotanim 678 → LOADING → LOGGED_IN), measured off `recordings/2026-08-28_173427_mh-live-run5`.
  The confirmation was a single `human_delay(2.5, 3.2)` and one position read — landing ~0.4s BEFORE
  arrival — so a teleport that worked read as a failure and the retry broke another tablet. It is
  now POLLED (`_await_teleport`, `TELEPORT_CONFIRM_TIMEOUT_S`): reading too early costs a tablet,
  waiting too long costs idle seconds, and that asymmetry decides it.
- **`TRAVEL_TELEPORT_DISTANCE` is 110 (was 150, was 40), and BOTH old values failed live — the
  bound is bank-to-home, not city-centre-to-city-centre.** It means "are we in a different city".
  40 failed low: the Falador tablet lands **74 tiles** from Larry's own standing tile, so standing
  in Falador having just arrived, the gate still said "teleport", and only the
  `mh_teleported_this_contract` latch stood between that and Falador→Falador once a tick. 150
  failed HIGH (2026-08-30, mh_live28; USER: "it just walked from varock to falador instead of
  taking a tablet? why"): it was derived from city-centre spacing ("the four destinations are
  248+ apart"), but the gate is evaluated where the player STANDS — usually a bank after
  restocking — and NORMAN, TAU and LARRY all sit on Falador's EAST side while the Varrock WEST
  bank is at ~(3185,3436): all three pairs measure 139-149, under the gate, so EVERY
  Varrock-bank→Falador-home transition walked ~147 tiles (~90s) instead of breaking a tablet.
  Real bounds: worst same-city distance ~75, closest cross-city bank→home pair 139; 110 sits
  between with ~30 tiles of margin each way.
- **Anything that spends a consumable on repeat needs a CEILING, not just a cooldown.** Two loops in
  one session burned real resources before being stopped by hand (three teleport tablets; NPC
  Contact casts). Hence `TELEPORT_MAX_ATTEMPTS` (2, then safety-stop rather than walk cross-country)
  and `CONTACT_MAX_CASTS` (5). A cooldown only sets the RATE of an unbounded loop.
- **The initial `mh_phase` is derived from live state, never defaulted to `"bank"`.** It used to be
  `data.get("mh_phase") or "bank"`, and with a contract in progress nothing below overrode it
  (`"bank"` deliberately does not auto-promote, to preserve work_phase's mid-contract diversion) —
  so EVERY restart mid-contract went banking. Live, that meant restarting inside Mariah's house and
  going for the Hosidius bank booth 21 tiles away through a wall.
- **`bank_phase` walks to a bank more than `banker.BANK_APPROACH_TILES` (8) away, via
  `travel.walk_to`, before opening it.** `banker.open_bank` gained its own minimap-hop approach the
  same day, but a minimap hop cannot open a closed door, and this routine banks from inside the
  contract house. Two things made the old behaviour a dead loop rather than a slow success:
  `find_nearby_bank` accepts anything within `maxBankDistance` (40) while `_refresh_bank_object`
  only re-resolves within **15**, so every retry re-aimed at the identical stale pixel; and the
  booth's projection landed on the chatbox tab strip, so all five clicks resolved to "Switch tab".

**The collision map `worldmap.py` reads is a static snapshot, not a live one.** A game update
that moves a wall makes a route wrong; the symptom is a walk that stalls at one specific tile,
not a crash. Regenerating it was deliberately deferred rather than done as part of this rewrite
(swapping mid-plan would have invalidated every pinned per-plane bit count and route-length
fixture the earlier tasks encoded). USER (2026-08-29) dumped a fresh map with `CacheMapDumper`
anyway, staged at `ControllerAddon/map-output/collision-map.gz`, and MEASURED it does **not**
load with today's reader: its cookie's low 16 bits are 12347 (`SERIAL_COOKIE` *with* run
containers -- `CacheMapDumper` calls `runOptimize()` before serializing), and `worldmap._Roaring`
currently accepts only the no-run-container cookie. Swapping to that dump is therefore not the
ten-line `Indexer` change the design doc originally assumed; it needs three things: (1)
run-container support in `worldmap._Roaring` -- the run-flag bitmap sits between the cookie and
the descriptive header, plus a third container type; (2) an `Indexer` change to the dump's own
layout -- 12-bit x (base 480), 14-bit y, 2-bit plane, and the polarity is **INVERTED** from
today's map (a set bit means BLOCKED, not passable); (3) re-measurement of every pinned fixture in
`ControllerAddon/tests/test_worldmap.py`, since the per-plane bit counts and all four route
lengths there come from the current (EthanApi) map, not this one. Not done here -- recorded as
its own follow-up task.

