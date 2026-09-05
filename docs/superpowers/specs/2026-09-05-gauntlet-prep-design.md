# Gauntlet sub-project 3: the prep -- design

Date: 2026-09-05. Status: approved in chat, awaiting written review.

Sub-projects 1 (instance plumbing) and 2 (the Hunllef fight, `routines/corrupted_gauntlet.py`)
are built. This one adds the phases in front of `enter`: explore the maze on a fixed route,
gather, craft, upgrade, then hand over to the existing `enter` -> `fight` -> `loot` phases.

## Goal

From the moment the Corrupted Gauntlet loads, inside the 7:30 timer: build at least the minimum
kit (T1 armour, two attuned weapons of different styles, ~20 fish, 2 egniol), use spare time on
upgrades (T2 pieces, a perfected weapon when the route finds its demi-boss), and be at the
barrier with prayers on before the timer forces the fight. Unattended.

## Non-goals

- Beating the human's 6:38 prep. Meeting the minimum reliably first; speed is a live-session
  tuning activity.
- Regular Gauntlet (10-minute timer, guaranteed resource rooms). The ids cover it; nothing is
  tuned for it.
- Halberds. Two styles are enough for the fight routine (bow + staff); melee is never built.

## Facts this rests on

From `recordings/2026-09-05_014024_corrupted-gauntlet-humanrun1.*` (prep 6:38), the wiki (The
Gauntlet rev 15273190, Corrupted Hunllef rev 15301498), the cache, and the user's route (chat,
2026-09-05).

- **7x7 room grid; the boss room is the centre; the start room is adjacent to it on a side
  that VARIES between runs; the demi-bosses are in the three central border rooms of each
  side** (user, 2026-09-05). Every direction below is relative: OUTWARD = from the boss room
  toward the start room; LEFT/RIGHT = the two perpendicular directions.
- **New rooms open by `Light`ing a `Node` with the crystal sceptre** (wiki: "required to light
  nodes, i.e. to visit new rooms"); the human lit 17. Node ids 35998/35999 both took `Light`.
  Whether a node's id changes when lit, and whether the corridor's collision opens, are
  live-checklist items. Chat on success: "You light the nodes in the corridor to help guide the way."
- **Template coordinates repeat across rooms** (the same chunk templates are reused: fishing
  spots at (1922,5645), (1922,5650), (1923,5692), ...; nodes at (1934,5686/5689/5638/5657)), so
  only sub-project 1's instance-space coordinates identify a room. Resource rooms are ~14 tiles
  square with nodes at the mid-edges (x 1920/1921 west, 1934 east, y 5694/5695 north in one room).
- **Resources per node** (wiki): Corrupt Deposit 3 ore, Phren Roots 3 bark, Linum Tirinum 3
  linum, Grym Root 1 leaf, Fishing Spot 4 raw paddlefish (also a water source). Gathering
  awards shards ("You find 28 corrupted shards"). Depleted variants: 35968/35970/35976/35974/35972.
- **Costs** (wiki, confirmed by the recording's inventory deltas): T1 piece = 1 ore + 1 bark + 1
  linum + 50 shards (helm/body/legs); T2 body = 2/2/2 + 100, T2 helm/legs = 1/1/1 + 50;
  basic weapon = weapon frame (0 shards); attuned = basic + 50 shards; perfected = attuned + orb
  (staff; first dragon kill) / bowstring (bow; first dark beast kill) / spike (halberd; bear).
  Vial = 10 shards at the bowl; corrupted paddlefish = paddlefish + 10 shards at the bowl.
  Egniol (3): vial -> fill at the Water Pump or a Fishing Spot -> use Grym leaf -> Grym potion
  (unf) -> use Corrupted dust (Pestle and mortar on 10 shards -> 10 dust). Teleport crystal:
  one is given at entry; more cost shards at the bowl (not needed for this design).
- **Frames**: weapon frames drop from monsters (the human got 3: rat room, two dragon kills).
  A frame is needed per weapon; two frames minimum.
- **Bowl interface**: `Sing-crystal` opens group 270; the recording's `Make` clicks were 270.17
  helm, 270.18 body, 270.19 legs, 270.21 staff, 270.22 bow, 270.23 corrupted paddlefish (270.20
  presumably halberd; vial/teleport children unrecorded). One click makes ONE item at the tier
  the materials allow; upgrading a piece is a second click with the lower tier in the inventory.
  Live checklist: confirm children by name via `/widgets/dump` rather than trusting the index.
- **Range**: one `Cook` click cooked 15 fish at 0.6 s each (no quantity prompt recorded).
- **Monsters**: weak (rat 9040, spider 9041, bat 9042) aggro and interrupt gathering; strong
  (unicorn 9043, scorpion 9044, wolf 9045) hurt; demi-bosses (bear 9046 melee, dragon 9047
  magic, dark beast id unknown, ranged) need prayer. The human prayed Melee while gathering
  near wolves and Magic for the dragon.
- **Human timeline**: 0-160 s gathering (7/7/7 materials, 8 fish, 2 grym, 2 frames), 160-180 s
  first craft (attuned bow, T1 set, T2 helm), 180-250 s two dragon kills (orb, frame,
  bowstring... the second demi-boss drop was a bowstring, so a dark beast was also killed or
  the dragon's table includes it -- live checklist), 250 s teleport home, 250-400 s craft the
  rest, fish, potions, cook, 401 s quick-pass.
- **Timer**: 7:30 from entry (wiki). The routine's clock starts when region 7768 is first seen
  (`/player.regionId`), the same moment `gamestate` LOADING -> LOGGED_IN lands in the recording.

## Design

### The route (user's strategy, made relative)

Grid rows/cols 0..6, boss B at (3,3). Rings by Chebyshev distance from B: inner (1), middle
(2), border (3). **Demi-bosses spawn in the three central border rooms of each side** (user:
"the 3 center tiles of the 7x7 on each cardinal, at the border"): on the north side those are
(0,2), (0,3), (0,4). With the start room S adjacent to B on a side that varies, use OUTWARD =
B -> S, RIGHT/LEFT perpendicular; written below for S north of B, i.e. S = (2,3):

1. S -> (2,4): the inner-ring corner to the RIGHT (light its node from S, enter, gather).
2. (2,4) -> (1,4): one room OUTWARD into the middle ring (enter, gather). Light its OUTWARD
   node to reveal the border room (0,4). Do not enter it.
3. Walk the middle ring LEFTWARD two rooms: (1,3), then (1,2), lighting each room's OUTWARD
   node to reveal (0,3) and (0,2). Do not enter them. These three reveals are the demi-boss
   check.
4. (1,2) -> (2,2): one room back toward B (the inner-ring corner to the LEFT), then complete
   the circle around B through the inner ring: (3,2), (4,2), (4,3), (4,4), (3,4), ending at
   (2,4) beside S. Gather in every room entered.

Rooms entered: 10 plus S; rooms revealed only: 3. The other three sides' border rooms are not
visited; the user reports this route usually yields all the materials and one or two perfected
weapons. Each step is a node to light plus a room to gather; `gauntlet_maze.route(start_side)`
emits the ordered list of (room, action) pairs for all four start sides, pinned by tests.

"Reveal" is one `Light` on the node facing the room; the room's contents then show on the
map. Whether `/npcs`/`/objects` list a revealed-but-unentered room (or every room from the
start) is the first live-checklist item; the design works either way, it only decides how
early a demi-boss is known. A revealed demi-boss room is entered only in `upgrade`, from the
middle-ring room below it, when its drop is wanted (dragon -> orb -> perfected staff; dark
beast -> bowstring -> perfected bow; bear -> not wanted).

### Room model

`gauntlet_maze.py` (new, pure) builds the room grid from instance space: the boss room is the
component containing the Barrier's far side... simpler and robust: the start room is the
component the player stands in at t=0; its centre is the mean of its walkable tiles; the room
pitch P is measured live (the distance between the start room's centre and the first opened
room's centre, expected ~16 tiles; pinned as a constant once measured). Room (i, j) =
`start_centre + i*P*RIGHT + j*P*OUTWARD`; the boss room is (0, -1). OUTWARD is the unit vector
from the Barrier toward the start-room centre. Nodes are matched to rooms and edges by
position: the node nearest the midpoint between two room centres is that edge's node.

### Phases (derived each tick, in front of the existing `enter`)

- `explore_gather` -- walk the route; in each entered room gather every node whose resource is
  still in DEFICIT (see the kit ladder), kill what attacks us, pick up drops (frames, shards).
  Ends when the route is done, or the kit's minimum materials are all in the bag AND
  `GATHER_DEADLINE_S` (270 s) has passed, or `GATHER_HARD_DEADLINE_S` (330 s).
- `craft` -- teleport crystal home (or walk if none), bowl: vials first (2), then T1 set, then
  the two weapons to attuned, then T2 pieces cheapest first while shards allow; pump the vials;
  pestle shards; combine potions; cook at the Range; bowl again for corrupted paddlefish if
  shards remain and time allows.
- `upgrade` -- only when `time_left > UPGRADE_MIN_S` (90 s) and a demi-boss whose drop we
  want (dragon -> orb -> perfected staff; dark beast -> bowstring -> perfected bow) was seen in
  one of the three revealed border rooms: go, kill it with the sub-project 2 rungs (prayer by its style,
  our best weapon), take the drop, return, craft the perfected weapon.
- `enter` (existing) -- from `ENTER_BY_S` (420 s) the enter phase takes over regardless of the
  kit, so the timer never forces the fight with prayers off.

### Kit ladder and deficits

`KIT = [T1 helm, T1 body, T1 legs, attuned bow, attuned staff, fish 20, egniol 2]`, then
`UPGRADES = [T2 helm, T2 legs, T2 body, perfected staff, perfected bow]`. `kit_deficit(inventory,
equipment)` returns the materials still needed for every unmet KIT entry (ore/bark/linum/shards/
frames/fish/grym), and `gather_value(room_contents, deficit)` scores a room. The explore phase
gathers only deficit resources, except shards (always) and fish up to the target.

### Monsters during gathering

Reuse `hunllef.ladder`'s shape in a small `gather_ladder`: escape is not needed (no floor
tiles); protection prayer by the attacker's style (`ATTACK_STYLE_BY_NPC_ID`: rat/spider/bat/
unicorn/scorpion/wolf/bear melee, dragon magic, dark beast ranged); attack anything attacking
us with the best weapon we hold (sceptre until a bow/staff exists); eat at `eat_at`; never
engage a demi-boss outside `upgrade`; when a demi-boss attacks us in a room we entered, leave
the room (walk to the previous room's centre) rather than fight it.

### Crafting mechanics

- Bowl: `Sing-crystal` (click_entity, expected_action) -> wait for widget 270 (`WIDGET_LOADED`
  + `/widget` clickable) -> click the child whose text matches the item name (`/widgets/dump`
  filtered to group 270, matched by name, never by index) -> confirm on the inventory delta.
- Range: `Cook`, confirm on the cooked count rising each tick; stop when raw = 0.
- Pump: `Fill-from` with vials in the inventory.
- Potions: `Use` item A on item B via inventory clicks (item then item) with `expected_action`
  "Use"; confirm on the delta.
- Equip: `Wear`/`Wield` from the inventory (sub-project 2's `do_wield` generalised).

### Time control

`time_left()` = 450 - (now - t0). Every phase checks it first; the deadlines above are config.
A stuck watch (`STUCK_WARN_S` 20 s of no inventory/position/phase change) logs `[!]` and, at
`STUCK_STOP_S` (60 s), safety-stops -- except in `enter`, which never stops.

## Error handling

- No Barrier visible at start, or region not 7768: log "start inside the Gauntlet" and idle.
- A node click that does not open the corridor within `NODE_BUDGET_S` (6 s): retry once, then
  mark the edge dead and re-plan the route around it (the ring has two directions).
- Route rooms with nothing useful are still walked through (they are the path).
- Missing sceptre/tools: `Take` from Tool Storage before leaving the start room.
- Death or the teleport crystal missing: walk home via the room graph.
- Shards short for the T1 set at craft time: craft what is affordable, log the shortfall.

## Testing

Pure, hermetic: `kit_deficit` on recorded inventories (the recording's deltas as fixtures);
`gather_value`; the route generator for all four start sides (S north/east/south/west of the
boss: the room list, the three outward peeks, the full circle); node-to-edge matching on a
synthetic grid; room grid from a synthetic collision grid; the craft plan from materials +
shards (T1 first, then weapons, then T2 cheapest first, never overspending); `time_left`
gates; the gather ladder's rung order.

## Live verification checklist (first session, with the recorder running)

1. `/objects`/`/npcs` at t=0: how many rooms' contents are listed (all, revealed only, entered
   only)?
2. Node ids before/after `Light`; corridor tiles in `/collision?all=true` before/after.
3. Room pitch P and the start-room centre estimate; the four start sides over four runs.
4. Bowl children by name (`/widgets/dump` group 270) at each tier.
5. One `Cook` click cooks the stack; potion `Use` chain lands on `expected_action`.
6. Which of the three revealed border rooms held demi-bosses over several runs, and
   whether a dragon drops bowstrings (the recording suggests so).
7. Prep time vs the human's 6:38; where the time goes (`log_gaps.py`).

## Files touched

New: `ControllerAddon/gauntlet_maze.py` (room grid, route, node matching), `ControllerAddon/
gauntlet_kit.py` (kit ladder, deficits, craft plan, recipes), `tests/test_gauntlet_maze.py`,
`tests/test_gauntlet_kit.py`. Modified: `routines/corrupted_gauntlet.py` (new phases, gather
ladder, crafting actions), `gauntlet_ids.py` (resource/monster style tables, bowl child names),
`hunllef.py` (nothing expected), CLAUDE.md.
