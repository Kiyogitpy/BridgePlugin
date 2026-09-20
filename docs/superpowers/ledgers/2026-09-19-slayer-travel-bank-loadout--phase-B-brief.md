# PHASE B — the house hub (detailed 2026-09-20 from live measurements)

Repo `C:\Users\Cindy\Documents\BridgePlugin`, branch `crossfloor-travel`, Python under `ControllerAddon/`, tests run from there with `python -m pytest tests/<file> -q`.

The project drives a REAL game account through a physical mouse/keyboard. Phase B adds the player-owned house (POH) as a travel hub: break a house tablet, drink from the pool, then leave through the ornate jewellery box or the house fairy ring. Every outcome is CONFIRMED by a bridge read; every failure returns False so the caller's ceilings stop loudly.

## Facts MEASURED LIVE on this account (build fixtures from THESE shapes, not from memory)

`GET /player` inside the house (relevant keys):
```json
{"x":1933,"y":7105,"plane":1,"instPlane":0,"inInstance":true,"instX":11669,"instY":1449,"regionId":7791,"prayer":77,"prayerReal":77,"hp":43,"hpReal":93,"runEnergy":100}
```
Outside the house `inInstance` is false and x/y are ordinary world coordinates.

`GET /objects?radius=..&fresh=true` returns a BARE JSON LIST. One record (real):
```json
{"plane":1,"visible":true,"distance":2147483647,"clickable":true,"instY":1449,"instX":11670,"impostorId":37538,"objectType":"GameObject","onScreen":true,"name":"Ornate Jewellery Box","x":1934,"y":7105,"id":29156,"actions":["Teleport Menu","Farming Guild","Remove"],"screenX":640,"screenY":400}
```
- **`distance` is 2147483647 on EVERY object in the house.** Never sort or filter by it.
- **Template `x`/`y` LIE about distance in the house; `instX`/`instY` are the real geometry.** Player inst (11669,1449); box inst (11670,1449) = 1 tile; pool template (1967,7040) but inst (11671,1447) = 2 tiles; fairy ring template (1964,7043) inst (11668,1444) = 5 tiles. Distances in the house = Chebyshev on instX/instY.
- A multi-tile object appears once PER TILE in the list (the pool 4 times, the ring 4 times).
- Objects: box **29156** actions `Teleport Menu / Farming Guild / Remove` — the MIDDLE action is the box's LAST DESTINATION and may be the left-click default, so the menu option `Teleport Menu` is ALWAYS taken through `controller.click_menu_action`, never a plain left click. Fairy ring **29228** actions `Zanaris / Configure / Last-destination / Favourites / Remove`. Pool: **29239** `Drink / Upgrade / Remove` on this account; any of `29241, 29240, 29239, 29238, 29237` counts (Microbot-Hub's list). Exit portal **4525**.
- House tablet: inventory item named `Teleport to house` (id 8013), left-click option `Break`; ~4.1 s to land, two LOADING flickers on the way (the bridge may answer nothing for a moment: treat a failed read as "not yet").

Jewellery box interface = widget group **590**, and it IS in `controller.get_widgets_dump()`. Real entries (dump records carry `group, child, index, screenX, screenY` (centre), `text`, `actions`):
```
590.5[8]  text='<col=ccccff>H:</col> Cooking Guild'            actions=['Cooking Guild']
590.2[7]  text='<col=ccccff>3:</col> Ferox Enclave'            actions=['Ferox Enclave']
590.2[8]  text='<col=ccccff>4:</col> <str>Fortis Colosseum</str>' actions=['Fortis Colosseum']   <- LOCKED (struck through)
590.5[3]  text='Skills necklace'                               actions absent                    <- a heading, not a destination
590.1[11] text=''                                              actions=['Close']
```
A destination entry = group 590, `actions` contains the destination name (case-insensitive exact), and its `text` does NOT contain `<str>`.

Existing code to reuse (read these before writing):
- `controller.get_player()`, `get_objects(radius=None, fresh=False)`, `get_inventory()` (items: `name`, `qty`, `id`, screen coords), `get_widgets_dump()`, `object_refresh_target(x, y, plane, obj_id, radius=None)`, `click_entity(ser, entity, offset=(-7,7), refresh_target=None, check_ui_blockers=True, simulate_mistakes=True, expected_action=None)` (returns a falsy value with `.withheld == True` when the hover check refuses), `click_menu_action(ser, entity, option, offset=(-7,7), check_ui_blockers=True, refresh_target=None)`, `open_inventory_tab(ser)`, `human_delay(a, b)`.
- `travel.hop_fairy_ring(ctx, code, expect, ring_tile=None, expect_region=None, log="travel", ring_ids=None)` — ONE bounded action per call; True once confirmed and hopped; False = "did something, call again". It already dials interface 398 and refuses a wrong Confirm text. It finds the ring with `get_objects(radius=20)` and `id in ring_ids`.
- `legs.py`: builders return flat dicts from `_base(kind, display, dest)`; `run_next(ctx, legs, cursor, log)` returns `(DONE|PROGRESS|FAILED|FINISHED, cursor)`; `await_arrival(dest, budget_s=ITEM_BUDGET_S, radius=ARRIVAL_TILES)`; `run_item_leg`. The `fairy` branch shows the PROGRESS pattern.
- `slayer_routes.py`: `parse`, `nearest_ring`, `plan(task_name, player, carried, skill_levels=None)`, `FAIRY_EXPECT`, `NEAR_SITE_TILES`.

## Task B1 — `ControllerAddon/poh.py` + `tests/test_poh.py`

Module header comment: what it is, the measured facts above in brief, UNVERIFIED LIVE.

```python
TABLET = "teleport to house"
BOX_ID, RING_ID, EXIT_PORTAL_ID = 29156, 29228, 4525
POOL_IDS = (29241, 29240, 29239, 29238, 29237)
BOX_GROUP = 590
BOX_MENU_OPTION = "Teleport Menu"
ENTER_BUDGET_S, POOL_BUDGET_S, BOX_MENU_BUDGET_S = 10.0, 6.0, 8.0
POLL_S = 0.3
SCAN_RADIUS = 40
```
Functions (all take fresh bridge reads; none sleeps in place of a confirmation):
- `in_house(player=None) -> bool`: True when `player.get("inInstance")` is true AND a house object is in the scene: `find((EXIT_PORTAL_ID, BOX_ID, RING_ID) + POOL_IDS)` is not None. (An instance alone is not a house: raids, Pest Control and the Gauntlet are instances too.) `player=None` -> reads `controller.get_player()`. An empty/failed read -> False.
- `inst_distance(a, b) -> int | None`: Chebyshev on `instX`/`instY`; None if either lacks them.
- `find(ids, player=None) -> dict | None`: `controller.get_objects(radius=SCAN_RADIUS, fresh=True)`; keep records whose `id` is in `ids`, `clickable` is true and `screenX` is not None; return the one nearest the player by `inst_distance` (records with no instance coordinates sort last); ties -> first. NEVER reads `distance`.
- `enter(ctx, log="poh") -> bool`: already `in_house()` -> True with no input. Else find the inventory item whose lowercased name contains `TABLET`; none -> log and False. `controller.open_inventory_tab(ctx.ser)`; `click_entity(ser, item, check_ui_blockers=False, simulate_mistakes=False, expected_action="Break")`; on a withheld click fall back to `click_menu_action(ser, item, "Break", check_ui_blockers=False)`; click failed -> False. Then poll `in_house()` until `ENTER_BUDGET_S`; count the spend in `ctx.state.data["poh_tablets_used"]`.
- `drink_pool(ctx, log="poh") -> bool`: read the player; if `prayer >= prayerReal` AND `runEnergy >= 90` (missing keys -> treat as needing a drink only when the value is present and low; all unknown -> skip with True) -> True without input. `find(POOL_IDS)`; none -> log "no pool" and return True (a house without a pool is not a travel failure). Click with `expected_action="Drink"` and `refresh_target=controller.object_refresh_target(x, y, plane, id)`, `simulate_mistakes=False`; withheld -> `click_menu_action(..., "Drink", refresh_target=...)`. Confirm: poll until `prayer >= prayerReal` or `POOL_BUDGET_S`; return True either way but LOG when it did not rise (the pool is a convenience, never a reason to strand).
- `box_entry(dump, destination) -> dict | None`: the ONE record with `group == BOX_GROUP`, a `screenX`, an action equal to `destination` case-insensitively, and no `<str>` in its text. Zero or two matches -> None. Pure.
- `box_is_locked(dump, destination) -> bool`: an entry with that action exists but its text has `<str>`. Pure.
- `use_box(ctx, destination, log="poh") -> bool`: `find((BOX_ID,))`; none -> False. `click_menu_action(ser, box, BOX_MENU_OPTION, refresh_target=object_refresh_target(...))`; failed -> False. Poll the dump until `box_entry` answers or `BOX_MENU_BUDGET_S` (the budget includes the walk to the box); if `box_is_locked` -> log "locked" and press ESC (`controller.key_tap(ser, "ESC")`), False; never appeared -> ESC, False. Click the entry: `click_entity(ser, entry, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False, expected_action=destination)`; return its truthiness. (The LANDING is confirmed by the caller with `legs.await_arrival`.)
- `use_ring(ctx, code, expect, log="poh") -> bool`: `return travel.hop_fairy_ring(ctx, code, expect, log=log, ring_ids=(RING_ID,))`.

Tests (`tests/test_poh.py`, with the `_no_live_bridge` autouse fixture copied from `tests/test_npc_contact.py`, and `_Ctx` like the other test files; patch `poh.time.sleep` and `controller.human_delay` to no-ops): fixtures built from the REAL records above. At least:
1. `find` picks by INSTANCE distance and ignores `distance` — give two pool tiles whose template coords are 65 tiles off and whose `distance` is 2147483647; and a farther box; assert the nearest-by-inst is returned.
2. `find` skips unclickable / no-screen records; returns None when nothing matches.
3. `in_house` is False outside an instance, False in an instance with no house object, True with one.
4. `enter`: already inside -> no input at all; no tablet -> False, no click; tablet -> click with `expected_action="Break"`, then True once `in_house` flips; never flips -> False after the budget (patch the budget small); withheld click -> the menu fallback is used.
5. `drink_pool`: full prayer and run -> no input; low prayer -> clicks `Drink` with a refresh target and returns True when prayer reaches `prayerReal`; no pool -> True, no input; prayer never rises -> True (logged).
6. `box_entry`: finds `Cooking Guild` (case-insensitive), refuses a heading, refuses the locked `Fortis Colosseum`, refuses when two records match; `box_is_locked` True only for the struck-through one.
7. `use_box`: takes `Teleport Menu` through `click_menu_action` (assert a plain `click_entity` is NOT used on the box itself), then clicks the entry with `expected_action="Cooking Guild"`, `offset=(0,0)`, `check_ui_blockers=False`; locked -> ESC and False; menu never opens -> ESC and False; no box -> False.
8. `use_ring` forwards `ring_ids=(29228,)`.

## Task B2 — legs for the house (`legs.py` + `tests/test_legs.py`)

Builders (flat dicts via `_base`):
- `house()` -> kind `"poh_enter"`, display `"house tablet"`, dest None.
- `pool()` -> kind `"poh_pool"`, display `"house pool"`.
- `house_box(destination, dest)` -> kind `"poh_box"`, display `f"jewellery box: {destination}"`, `leg["option"] = destination`, dest = landing.
- `house_ring(code, expect, dest=None)` -> kind `"poh_ring"`, display `f"house fairy ring {code}"`, `code`, `expect`.
`run_next` branches (import `poh` inside legs at module top; `poh` must NOT import `legs`):
- `poh_enter`: `poh.enter(ctx, log)` -> DONE/FAILED.
- `poh_pool`: `poh.drink_pool(ctx, log)` -> DONE (it never fails travel) / FAILED only if it returns False.
- `poh_box`: `poh.use_box(ctx, option, log)` False -> FAILED; True -> `await_arrival(dest)` -> DONE/FAILED.
- `poh_ring`: `poh.use_ring(...)` False -> PROGRESS (same contract as `fairy`); True -> with a 3-element dest `await_arrival(dest, FAIRY_BUDGET_S)` -> DONE/FAILED.
Tests: one per branch with `poh` functions monkeypatched; the existing legs tests stay green.

## Task B3 — `slayer_routes.plan` grows the house (+ tests)

- `BOX_LANDINGS` (destination name -> landing tile; UNVERIFIED coordinates, the 12-tile arrival radius absorbs small errors): Cooking Guild (3143,3442,0), Draynor Village (3105,3251,0), Karamja (2918,3176,0), Burthorpe (2899,3553,0), Barbarian Outpost (2520,3571,0), Ferox Enclave (3151,3635,0), Castle Wars (2441,3090,0), Edgeville (3087,3496,0), Al Kharid (3293,3163,0), Mining Guild (3049,9763,0), Fishing Guild (2611,3390,0), Crafting Guild (2933,3295,0), Grand Exchange (3164,3478,0), Falador Park (2996,3375,0), Warriors' Guild (2882,3548,0), Champions' Guild (3191,3368,0), Monastery (3052,3488,0), Ranging Guild (2655,3441,0), Woodcutting Guild (1662,3505,0), Farming Guild (1249,3718,0).
- `parse` additionally returns `("box", "<Destination>")` when the text after the colon, with any trailing ` (Option X)` stripped, names a key of `BOX_LANDINGS` — whatever the item before the colon is (`Ornate Jewellery Box: Cooking Guild`, `Glory amulet: Draynor Village (Option 3)`, `Skill's Necklace: Mining Guild`, `Glory amulet: Karamja`): the box serves all of those destinations. The existing `fairy` / `item` results are unchanged and are checked FIRST.
- `plan`: the near-site shortcut and the plugin's teleport order are unchanged. `has_tablet` = any carried name containing `"teleport to house"`. For `fairy`: an overworld ring within walking range is still preferred; otherwise, with a tablet, `[legs.house(), legs.pool(), legs.house_ring(code, FAIRY_EXPECT[code], dest=exit), site]`. For `box`: with a tablet `[legs.house(), legs.pool(), legs.house_box(dest_name, landing), site]`; without one the reason says "needs a house tablet". When the player is ALREADY in the house (`player.get("inInstance")`), a tablet is not required for the house routes (the `poh_enter` leg is then a no-op) — and `nearest_ring` must not be consulted at all (template coordinates in an instance are meaningless).
Tests (real data, hermetic): minotaurs with a tablet -> `["poh_enter","poh_pool","poh_box","walk"]` with option `Cooking Guild`; without a tablet -> None and the reason names the tablet; cave slimes from Lumbridge (no ring near) with a tablet -> house ring AJQ; cave slimes from the bears site still prefers the BLR overworld ring; a player inside the house with no tablet still gets the house route; `parse` cases for all four string shapes; the existing tests stay green (one of them asserts `Ornate Jewellery Box` is "unsupported" — update THAT assertion to the new `("box", "Cooking Guild")`, and `test_every_unusable_teleport_is_named_in_the_reason` must now run with `carried={}` and still name both teleports or the tablet).

## Constraints (binding)
- Input ONLY through `controller.click_entity` / `click_menu_action` / `key_tap`. Interface clicks: `check_ui_blockers=False, offset=(0,0), simulate_mistakes=False`. Scene-object clicks keep `check_ui_blockers` default and pass a `refresh_target`.
- Confirm from a bridge read; a poll inside a budget is fine, a bare sleep standing in for an outcome is not.
- No module-level mutable state. `poh.py` must not import `legs`.
- Do not change `travel.walk_to`, `travel.route_to`, `worldmap.py`, `controller.py`, or any file under `vendor/`.
- Tests hermetic: every bridge read and input call monkeypatched; the `_no_live_bridge` fixture in every new test file.
- Another session commits unrelated files here: `git add` exact paths only; never stage, revert or clean anything else.
- One commit per task (B1, B2, B3), each message ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
