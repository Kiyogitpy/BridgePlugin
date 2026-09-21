# Slayer task loop — handoff (written 2026-09-20 for a move from the laptop to the desktop; brought up to date 2026-09-21)

A Claude Code conversation lives on the machine it ran on, so the laptop session that built all
of this cannot be resumed elsewhere. **A fresh session should read this file first**, then the
files it points at. Nothing below depends on the old conversation.

## Where the work is

- Repo `BridgePlugin`, branch **`crossfloor-travel`** (pushed; `dev` is pushed too and is its base).
  CHECKED 2026-09-20: 70 commits ahead of `dev`, `dev` 1 ahead (a2929d5), and a dry-run
  `git merge-tree origin/dev HEAD` is CLEAN -- no conflicts. On 2026-09-20 the MAIN checkout was
  switched to `dev` by someone else mid-session; work continued in the worktree
  `.claude/worktrees/crossfloor-travel` (self-sufficient: the collision map is tracked).
  NOT merged to `dev` — the merge is the user's call. Another session also committed Guardians of
  the Rift work onto this branch; it rides along.
- `ZombieLite`, branch `no-packets` on the user's fork (remote name `fork`), pushed.
- NOT in git, by choice: `bin/`, `.vscode/`, `gradle-run.log`, and `osrs-wiki-data-master/` (a
  117 MB raw OSRS wiki dump used for offline lookups — re-download or copy it if wanted).
- The `osrs-wiki` / `osrs-debug` MCP servers failed on the laptop because `.mcp.json` points at
  another user's folder; expect to fix paths per machine.

## What the user asked for (the standing goal)

Split the old "Slayer / Combat" routine in two. `combat.py` keeps killing whatever is picked in
the panel. `slayer.py` runs slayer TASKS end to end: get a task by the Lunar spell NPC Contact,
**Aya (= Turael) for 9 tasks then Kuradal (= Duradel) on every 10th** so the streak bonus comes
from the high master, fight it at the user's chosen site, and repeat — travel, banking, restock
and loot included. The user is hands-on, tests live, and corrects facts; trust their live
observations over a clean log.

## Documents, in reading order

1. **Spec**: `docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md`.
2. **The user's decisions**: `docs/slayer-sites/decisions.md` (22 Kuradal FIGHT tasks with sites,
   1 STOP = Greater demons, 20 SKIP; supply profiles; Aya's sites come from the Turael Skipping
   plugin with three overrides).
3. **Measured facts** (these OUTRANK the spec): `docs/slayer-sites/recordings-2026-09-19.md`,
   the slayer section of `docs/routine-notes.md`, and the "MEASURED LIVE 2026-09-20" block in the
   sub-project 3 plan.
4. **Plans** (`docs/superpowers/plans/`): `2026-09-18-slayer-split-and-plumbing.md` (done),
   `2026-09-19-crossfloor-travel-and-slayer-pure-pieces.md` (done),
   `2026-09-19-slayer-acquire-and-skip.md` (done, ACQUIRE proven live),
   **`2026-09-19-slayer-travel-bank-loadout.md` (IN PROGRESS — five live-gated phases A–E)**.
5. **Ledgers** (rulings, review findings, what each fix wave changed):
   `docs/superpowers/ledgers/` — copies of the git-ignored `.superpowers/sdd/*/progress.md`,
   plus the Phase B brief.
6. **Research**: `docs/slayer-sites/` — `microbot-slayer-survey.md`, `microbot-core-survey.md`,
   `slayer-best-in-bank-survey.md`, `tormented-demon-comparison.md`, `routes.md`.

## State of the code

| Piece | File(s) | State |
|---|---|---|
| Slayer task state | `GET /slayer`, `slayer_state.py` | live-proven |
| Take a task from Aya by NPC Contact | `npc_contact.py`, `routines/slayer.py` | **live-proven 2026-09-19** (one cast, 9 s) |
| Picker path, Kuradal, right-click `Cast` | `npc_contact.py` | built, UNRUN (first Kuradal task exercises it) |
| Cancel a task (30 points) | `npc_contact.cancel_task` | built, UNRUN; needs ≥ 90 points and a supervised first run |
| Floor-crossing walking | `crossfloor.py`, `legs.walk_across`, `legs.run_transport` | built, UNRUN |
| **Phase A** travel from where we stand | `slayer_routes.py`, TRAVEL phase in `routines/slayer.py` | built, reviewed, fixed, **UNRUN** |
| **Phase B** the house hub | `poh.py`, house legs in `legs.py` | built, reviewed, fixed, **UNRUN** |
| **E-pre** Microbot transports adapter | `vendor/microbot/`, `transports.load_microbot_links`, `tools/vendor_microbot_tables.py` | **built 2026-09-20**, Wyrms route in `routes.md`; door lists ported into `travel.py` |
| **E1** Kuradal's 22 tasks as data | `slayer_catalog.py`; `slayer_tasks.at_site/where` and `slayer_routes.plan` fall through to it | **built 2026-09-20 ahead of the live gates at the user's say-so** (pure data): 21 of 22 route from their landing in tests; gryphons wait on one recording. The 12 SLAYER RING tasks are planned through `legs.dialogue_item` (the ring's paged menu was already proven live in `golem_crafting`); Xeric's talisman runs through the plain item leg (`mahogany_homes` had measured it) and Moonclan Teleport through `legs.spell`. **Live preflight 2026-09-20: 19 of 22 plan, each with its onward walk PROVEN for the account** -- `plan()` refuses a teleport whose walk it cannot prove, so none is spent toward a strand. Open: cave horrors (no scroll owned), gryphons (one recording), zygomites (the ring's Zanaris action has no leg; no fungicide spray owned). **Aya: 23 of 24 plan** (`--aya`) -- the house-mounted digsite pendant (`legs.house_mounted`) and the gnome glider (`legs.glider`, from Microbot's walker) were added on the user's pointers; only dwarves (White Wolf Tunnel pub) is open |
| **C1** the loadout calculator | `slayer_loadout.py` | **built 2026-09-20 ahead of the live gates** (pure): task + carried + bank memory -> withdraw list, keep list, slots, named problems. Run read-only on the real bank. Nothing calls it yet |
| The DECISIONS of C2, D2, C3 | `slayer_policy.py` | **built 2026-09-20** (pure): bank / carry on / skip / stop; the loot tiers; base-gear persistence and the slot diff. Nothing calls it yet |
| C4 + D3 Java config | `slayerMinHouseTeleports` 5, `slayerMakeRoomValue` 50k, `slayerMustLootValue` 150k, exported in the `tasks` block | **built 2026-09-20**, gradle test passes; **needs a client rebuild** to appear in the panel |
| **C2** the BANK step before TRAVEL | `routines/slayer._pack` -> `banker.bank_up` | **built 2026-09-21, UNRUN**: decides with `slayer_loadout` + `slayer_policy`, acts through the live-proven banker. Bank ONCE per task; still short is a stop naming the items; never mid-journey and never near the site; a missing item is always a STOP, never an automatic 30-point skip |
| **D1** RESTOCK | `routines/slayer._restock` -> `banker.bank_up` | **built 2026-09-21, UNRUN**: out of food or prayer AT the site (`combat_core.out_of_critical_supplies`) -> drop the target, bank to the same pack, TRAVEL brings us back. A `slayer_restocking` flag keeps a half-failed bank trip from walking back empty-handed. Ceiling 4 per task, then a stop naming it |
| `tests/test_slayer_end_to_end.py` | the routine through WHOLE tasks in one simulated world | three tasks in a row (Kuradal on the 10th), a long task that restocks and comes back, the restock ceiling. Every decision real; only clicks faked. **Read its log, not just its asserts** |
| **C3** wearing a task's gear, and the base gear | `routines/slayer._wear`, `_capture_base_gear`, `_restore_base_gear` | **built 2026-09-21, UNRUN**: a catalog task's `worn` requirements are put ON before travelling and before every fight tick; the base gear is saved once per process (never while a gear task is active, never from an empty read) and put back when the task ends, before the next master is asked. A shield that knocks a TWO-HANDED weapon off is a STOP -- the one-handed weapon is the user's open decision (E0) |
| **D2** loot tiers | `combat_core.maybe_loot`'s `makeRoom` hook -> `routines/slayer._make_room` | **built 2026-09-21, UNRUN**: with a FULL inventory, a drop under 50k is left; from 50k one food is eaten for it only when HP is missing; from 150k it is always taken -- eat, else finish a 1-dose prayer potion, else drop the least costly consumable. Never gear, a teleport, or what the task needs. **The hook absent = combat_core's behaviour is byte-for-byte what it was**, pinned by test |
| **Breaks** | `routines/slayer.before_break`, `combat_core.bank_before_break` | **built 2026-09-21, UNRUN.** Breaks are ON (45-75 min apart, 3-12 min long) and `tick()` is skipped for the WHOLE break, so nothing eats, prays or retaliates -- a break falling mid-fight is a death. Slayer teleports to Ferox when AT the site (it always leaves by a proven teleport anyway); `combat.py` cannot, because it walks home and **Ferox is a sealed 413-tile island on the collision map** -- it BANKS and takes the break there instead. Slayer costs one ring of dueling charge per break |
| **Many varbits in one read** | `GET /varbits/<csv>`, `/varps/<csv>`, `controller.get_vars` | **built 2026-09-21; needs a client rebuild to be fast.** `legs.requirement_values` cost **1.16 s** per route plan (45 varbits + 10 varplayers at ~20 ms each). One hop answers them all; `get_vars` falls back to one-per-id on an un-rebuilt client, so nothing breaks meanwhile. Ids go in the PATH -- `requests` percent-encodes a comma, the trap `subscribe_varbits` records |
| The remaining ACTING halves: E0 gear decisions; E2-E6 hub live runs | — | NOT built -- these wait for the user or a live run |

Tests: `python -m pytest tests/ -q` from `ControllerAddon/` → **3379 passed, 1 skipped, 0 failed**
(2026-09-21, after the day's work).
The four long-standing `test_mastering_mixology.py` failures are GONE: they were stale tests written
before that routine's 'do nothing outside the lab' guard, not a bug. **The suite is green, so treat
any failure as real.** `test_crossfloor::test_an_impossible_trip_is_none_and_fast`
has an 8 s wall-clock bound and failed once under load.

## Before any live gate: run the preflight

    cd ControllerAddon
    python slayer_preflight.py                              # the live task
    python slayer_preflight.py --task "Cows" --count 20      # what if this were the task
    python slayer_preflight.py --aya                         # all of Aya's tasks (the gates are Aya tasks)
    python slayer_preflight.py --all                         # all 22 Kuradal tasks

Read-only, safe beside a running `controller.py`. It prints what the routine WOULD decide:
disposition, site, missing items, the route or the reason there is none, the pack, and the bank
verdict -- plus the route as it stands AFTER the bank trip. Since 2026-09-21 the routine has a
BANK step before TRAVEL, so a teleport sitting in the bank is fetched first rather than stopping
the run; the 'cannot get there ... not carried' line is what TRAVEL would say WITHOUT that trip. FOUND with it and
re-checked on 2026-09-21 against the RIGHT endpoint: **the panel has no food and no prayer potion
chosen** -- `GET /slayer-config` (the one `routines/slayer.get_config` reads; NOT `/config`, which
is the general block and carries no slayer keys at all) answers `food.name: ""` and
`potions.prayerPotion: ""`. Every task stops at the pack until both are set in Controller Settings.

## What to do next, in order

1. **Phase A live gate.** Needs an Aya task reached by fairy ring or Ectophial (bears, cave bugs,
   cave slimes, lizards, zombies, birds, cows) left ACTIVE, and for the two cave tasks a LIT
   lantern carried (the check is by item id — a lit and an unlit bullseye lantern share one name).
   At handoff the account held **Greater Demons x153, streak 19** — the task the user does by
   hand, so the routine stops on it by design. Checklist: slayer section of `docs/routine-notes.md`.
2. **Phase B live gate**: one Aya task reached through the house. Watch: is the fairy ring within
   `radius=20` of the tablet landing (nothing walks inside the house yet); does the hover gate
   read a jewellery-box entry's name; does `object_refresh_target` answer inside the instance.
3. ~~E-pre: the Microbot transports adapter~~ **DONE 2026-09-20** on the desktop:
   `ControllerAddon/vendor/microbot/VENDORED.md` pins commit `8bcc652`; `tools/vendor_microbot_tables.py <clone>`
   re-vendors. Still open from it: the Great Conch (gryphon) cave has no row in EITHER table
   (hand-write that leg in E1); the dusty-key gate exists in both but fails closed until the
   catalog supplies owned item ids; the whirlpool still needs `varbit_values`.
4. ~~**Phase C** (bank + loadout)~~ **DONE 2026-09-21** -- C1 through C4, D1 through D3 and the
   gear seam are all built and tested; see the table above. It still depends on `bank_memory`,
   whose own six-item live checklist in `CLAUDE.md` has never been run, so watch it on the first
   bank trip.
5. **Rebuild the client** (`./gradlew.bat run`). Three things wait on it: the three panel settings
   (`slayerMinHouseTeleports`, `slayerMakeRoomValue`, `slayerMustLootValue`), the bulk varbit
   endpoints that take route planning from 1.16 s to roughly 20 ms, and `M8aqProvider.guarded`
   catching `LinkageError` so one crashed reader cannot take the endpoint down.
6. **Set the panel's food and prayer restore before any live gate.** Every task stops at the pack
   until both are chosen, and the bank holds neither of the obvious defaults -- Cooked karambwan
   and Super restore are what this account actually has. Do NOT pick a blighted one; the loadout
   refuses them by name because they only work in the Wilderness.

## How this project is built (process the user chose and liked)

Spec → plan → **subagent-driven execution**: cheap implementer per task from a written brief
(exact tests and code), a Sonnet task review, then a **Fable whole-phase review with read-only
probes of the LIVE bridge**, a fix wave, and a re-review of the fix wave. Rulings go in the
ledger. Lessons this project paid for, all recorded in the ledgers:
- **Build test fixtures from a real GET, never from memory.** `/equipment` is a dict keyed by
  slot; the bridge sends `skipTasks` as a list; inventory quantity is `qty`. Each crashed or
  lied behind a wrong-shaped fixture.
- **Every new test file carries the `_no_live_bridge` autouse fixture** (it makes
  `controller.api_get` raise). Three tests were caught reading the user's live screen.
  Since 2026-09-20 `tests/conftest.py` also makes EVERY real HTTP request raise
  `ConnectionError` (a spy run found 88 tests in 9 files reading the live client), so the
  suite sees the same world with RuneLite open or closed. Keep the per-file fixture anyway:
  it fails LOUDLY on a forgotten fake, where the conftest one only answers "bridge down".
- **The plan's own tests and snippets were wrong more often than the implementers.**
- **Live runs overturn reasoning.** The NPC Contact spell's left-click re-contacts the LAST NPC
  (no picker opens); interface 75 is not in `/widgets/dump`'s root walk; inside the house
  TEMPLATE x/y lie about distance and `distance` is 2147483647 on every object — use
  `instX`/`instY`; a teleport lands the player ON its own object, a tile the static map cannot
  route from.
- A safety-stopped `controller.py` stays alive holding UDP 50555 — kill it before a relaunch.
- Absolute rules: input ONLY through the physical Arduino; never edit `m8aq/api/`; never
  hand-edit `ControllerAddon/vendor/`; Aya is NEVER contacted while a task is active (it resets
  the streak) — `slayer_state.assert_may_contact` raises.

## Still wanted from the user

- **The one-handed weapon for basilisks (E0), now URGENT rather than theoretical.** MEASURED LIVE
  2026-09-21: the account fights in RANGED gear with a **Toxic blowpipe**, which is TWO-HANDED.
  Putting the mirror shield on takes it off, so a basilisk task STOPS by design until a
  one-handed weapon is chosen. The same read showed the slayer helmet already covers the nose
  peg and the facemask, and that the mirror shield, a leaf-bladed weapon or broad ammo, the
  witchwood icon and heat boots are none of them worn (they are fetched and equipped).

- **Set `food.name` and `potions.prayerPotion` in Controller Settings.** MEASURED 2026-09-21:
  both are empty strings in `GET /slayer-config`, so every task stops at the pack. The bank has
  no sharks and no Prayer potion(4); it has 416 Cooked karambwan, 561 Tuna and 27 Super
  restore(4). Its 1319 Blighted anglerfish and 149 Blighted super restore(4) are refused by the
  loadout on purpose -- blighted supplies only work in the Wilderness, and no site here is.
- **A client rebuild**, for the three panel settings, the bulk varbit endpoints and the
  crashed-reader fix.
- **A Mos Le'Harmless scroll and a fungicide spray**, the only two items standing between
  Kuradal's cave horrors and zygomites tasks and a planned route.
- **Merging `crossfloor-travel`.** The branch is pushed and a dry-run merge into `dev` was clean;
  the merge itself is deliberately left as the user's call.
- The remaining live gates above.
- Per-task gear decisions the research left open (Phase E0): a one-handed weapon beside the
  mirror shield, ranged kit for waterfiends, heat boots for wyrms.
- "Tormented demons later": Greater demons stays a STOP task until the user says otherwise.
- **One short recording for the gryphon cave** (`python -u recorder.py gryphon-cave`): fairy ring
  CJQ, walk to the TASK-ONLY south-eastern cave, enter, walk to the gryphons. CHECKED 2026-09-20:
  no source has that entrance -- not our jar's transports table, not Microbot's fork, not
  upstream Shortest Path's current master (5,780 lines, zero rows on the Great Conch), and the
  wiki gives no tile (the cave map centres on (3232,8865), i.e. under surface (3232,2465); Elder
  Kelmo stands just inside). Everything else about that task is already data.
- ~~One short recording into the Digsite Dungeon~~ **NOT NEEDED since 2026-09-20: skeletons is on
  the user's Aya block list.** Kept for the day it is unblocked: from the
  digsite (the house-mounted pendant lands at (3341,3445)) down to the SKELETONS, around
  (3373,9748). CHECKED 2026-09-20: no row in the merged jar + Microbot tables lands anywhere in
  that dungeon -- the winch is simply absent -- so skeletons is refused before the pendant is
  spent. Same situation as the gryphon cave; both could go in one sitting.
- **A look at the Trolls route** in `docs/slayer-sites/routes.md` (185 tiles from Burthorpe,
  climbing from the west and north): is that the way you walk it, or the quest-gated back route?
