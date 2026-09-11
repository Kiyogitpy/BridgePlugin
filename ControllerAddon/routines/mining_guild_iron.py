# routines/mining_guild_iron.py
#
# Single-tile iron in the Mining Guild: stand on ONE tile with three iron rocks cardinally
# adjacent, mine each rock for its two ores, swap to the next rock the instant the second ore
# depletes it, and Use the ore on the Bank Deposit Box when the inventory is about to fill.
# Nothing here reads the tick clock: the swap is driven by the pushed XP_GAINED /
# OBJECT_DESPAWNED events, confirmed by the uncached /object/at, so the reaction is the human's
# ~0.3s and not a 0.6s poll boundary.
#
# Everything below is MEASURED from recordings/2026-09-11_121030_mining-guild-single-tile.*
# (2m56s, 40 Mine clicks, 72 ores, 4 deposits):
#   * stand tile (3021,9721); rocks north (3021,9722) id 11365, west (3020,9721) id 11364,
#     south (3021,9720) id 11365. A spent rock is 11390/11391 ("Rocks", no actions).
#   * a Mine click -> anim 624 at +0.3s -> first ore at +1.5s -> second ore at +3.3s, and the
#     second ore DEPLETES the rock. The Mining gloves alternate strictly ("will not save the
#     next iron rocks" on exactly 36 of 72 successes), which is what makes it two ores per rock.
#     The Varrock platebody adds a third ore on ~12% of successes; it changes nothing here.
#   * the human's next Mine click lands 0.12-0.55s (median 0.33s) after the depleting XP drop,
#     always from the same tile, with the cursor already near the next rock. A rock respawns
#     2.4s after depleting, so with three rocks on a 3.6s cycle one is always up.
#   * deposit: Use Iron ore (widget 149.0) -> Bank Deposit Box (10529 at (3013,9720), 8 tiles):
#     the game walks there and deposits every iron ore (anim 834, -26 ore ~4.4s after the
#     click, the gem sack and minerals stay). One Walk-here back to the stand tile, then the
#     next Mine click. ~9s per trip.
#
# Run with:
#     python controller.py routines/mining_guild_iron.py
# USER's choices (2026-09-11): the stand tile is config, not "wherever I start"; the deposit
# trip starts below 2 free slots (at 1 free the rock's second ore is refused and its swing
# wasted -- the recording shows that once); the click after the walk back waits for the stand
# tile itself rather than firing one tile out (a rock clicked from a neighbouring tile can be
# mined from THAT tile, and then only one of the three rocks is adjacent).

import time

import bridge_events
import controller
import debug_log
import travel

LOG = "mgi"

# live id -> spent id, both measured. The north/south rocks are 11365, the west one 11364.
ROCK_IDS = {11364: 11390, 11365: 11391}
SPENT_TO_LIVE = {spent: live for live, spent in ROCK_IDS.items()}
ROCK_NAME = "Iron rocks"
SKILL = "Mining"
XP_FIELD = "miningXp"

ROCKS_KEY = "mgi_rocks"
SPENT_KEY = "mgi_spent_at"
HOVERED_KEY = "mgi_hovered"
FULL_KEY = "mgi_full"            # the game said the inventory is full: deposit regardless of count
FAILS_KEY = "mgi_fails"
RETURN_FAILS_KEY = "mgi_return_fails"
CHAIN_LIMIT_KEY = "mgi_chain_limit"   # tests: cap the chain at N rock clicks

INVENTORY_SIZE = 28
CLICK_GRACE_S = 1.2       # no swing and no ore this long after a click -> it did not take
ORE_BUDGET_S = 5.5        # an ore every 1.8s at this level; survives one missed swing
FALLBACK_POLL_S = 0.15
SWAP_REACTION_S = (0.10, 0.30)   # after the depleting ore, before the in-place click (human 0.12-0.55)
RESPAWN_WAIT_S = 0.3      # every rock spent (other players): poll for the 2.4s respawn
CHAIN_MAX_S = 60.0
CHAIN_MAX_TARGETS = 60
FAILED_CLICK_STOP = 10    # consecutive clicks that produced nothing -> safety stop
DEPOSIT_BASE_S = 3.0      # plus WALK_S_PER_TILE per tile to the box
WALK_S_PER_TILE = 0.6
DEPOSIT_TRIES = 2
RETURN_BUDGET_S = 12.0
RETURN_POLL_S = 0.1
RETURN_FAILS_STOP = 5
HOVER_WITHIN_TILES = 2    # park the cursor on a rock once this close to the stand tile

DEFAULT_CONFIG = {
    "enabled": True,
    "standTile": {"x": 3021, "y": 9721, "plane": 0},
    "regionId": 12183,
    "oreName": "Iron ore",
    "depositBoxId": 10529,
    "depositAtFreeSlots": 2,      # deposit once FEWER than this many slots are free
    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        # OFF: the deposit trip is this routine's own; check_safety runs before tick and
        # would otherwise win the race at 28/28.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


def get_config(bridge_config):
    cfg = dict(DEFAULT_CONFIG)
    cfg["safety"] = dict(cfg["safety"], stopOnInventoryFull=False)
    return cfg


# --- pure helpers ------------------------------------------------------------------------

def discover_rocks(objects, stand):
    """The iron rocks cardinally adjacent to the stand tile, each as {x, y, plane, id} with
    `id` the LIVE id whether the rock is up or spent right now. One bulk scan, once; after
    that every 'is it up' question is one uncached /object/at with this id."""
    sx, sy, sp = stand
    neighbours = {(sx, sy + 1), (sx - 1, sy), (sx + 1, sy), (sx, sy - 1)}
    rocks = []
    seen = set()
    for obj in objects:
        tile = (obj.get("x"), obj.get("y"))
        if tile not in neighbours or tile in seen:
            continue
        if obj.get("plane", sp) != sp:
            continue
        oid = obj.get("id")
        live = oid if oid in ROCK_IDS else SPENT_TO_LIVE.get(oid)
        if live is None:
            continue
        seen.add(tile)
        rocks.append({"x": tile[0], "y": tile[1], "plane": sp, "id": live})
    return rocks


def _tile(rock):
    return (rock["x"], rock["y"])


def pick_rock(rocks, up, last, spent_at):
    """Round-robin over the rocks that are UP: never the one just spent while another is up,
    otherwise the one spent longest ago (a never-spent rock counts as oldest). None when no
    rock is up."""
    live = [r for r in rocks if up(r)]
    if not live:
        return None
    others = [r for r in live if _tile(r) != last]
    pool = others or live
    return min(pool, key=lambda r: spent_at.get(_tile(r), float("-inf")))


def deposit_due(free_slots, threshold):
    if free_slots is None:
        return False
    return free_slots < threshold


def deposit_needed(ctx, free_slots):
    if ctx.state.data.get(FULL_KEY):
        return True
    return deposit_due(free_slots, ctx.state.config.get("depositAtFreeSlots", 2))


def on_event(ctx, event_type, payload):
    if event_type != "CHAT_MESSAGE":
        return
    msg = ((payload or {}).get("message") or "").lower()
    if "inventory is too full" in msg:
        ctx.state.data[FULL_KEY] = True


# --- bridge reads ------------------------------------------------------------------------

def _rock_live(rock):
    """The rock as /object/at reports it right now (screen position included), or None when
    its live id no longer stands on its tile."""
    return controller.get_object_at(rock["x"], rock["y"], rock["plane"], rock["id"])


def _refresh(rock):
    return controller.object_refresh_target(rock["x"], rock["y"], rock["plane"], rock["id"])


def _xp():
    return (controller.get_player() or {}).get(XP_FIELD)


def _free_slots():
    inv = controller.get_inventory()
    if inv is None:
        return None
    return INVENTORY_SIZE - len(inv)


def _ore_count(cfg):
    name = cfg.get("oreName", "Iron ore")
    return sum(1 for i in (controller.get_inventory() or []) if i.get("name") == name)


def _stand(cfg):
    t = cfg.get("standTile") or {}
    return (t.get("x"), t.get("y"), t.get("plane", 0))


def _player_tile():
    p = controller.get_player() or {}
    if p.get("x") is None:
        return None
    return (p["x"], p["y"], p.get("plane", 0))


# --- one rock ----------------------------------------------------------------------------

def mine_rock(ctx, rock, hover_next=None):
    """Click `rock`, park the cursor on `hover_next`, then wait on the pushed signals until the
    rock is confirmed gone from its tile. Returns depleted | failed | timeout | no_click.

    An ore with the rock still standing (the first of its two) re-arms the budget; nothing --
    no swing animation and no ore -- past CLICK_GRACE_S is a click that did not take (another
    player on the pixel, a simulated misclick that was not corrected, a rock taken as we
    clicked). A swinging player is never 'failed', only 'timeout' at the ore budget."""
    events = ctx.events
    since = events.mark() if events is not None else None
    xp_base = _xp()
    if not controller.click_entity(ctx.ser, rock, refresh_target=_refresh(rock)):
        return "no_click"
    t_click = _now()
    if hover_next is not None:
        controller.hover_entity(ctx.ser, hover_next, refresh_target=_refresh(hover_next))

    tile = (rock["x"], rock["y"], rock["plane"])

    def match_xp(payload):
        return payload.get("skill") == SKILL

    def match_tile(payload):
        return (payload.get("x"), payload.get("y"), payload.get("plane")) == tile

    signals = {"XP_GAINED": match_xp, "OBJECT_DESPAWNED": match_tile, "INVENTORY_CHANGED": None}

    def confirm():
        if _rock_live(rock) is None:
            return "depleted"
        xp = _xp()
        if xp is not None and xp_base is not None and xp > xp_base:
            return ("ore", xp)
        return None

    ores = 0
    deadline = t_click + ORE_BUDGET_S
    grace = t_click + CLICK_GRACE_S
    while True:
        now = _now()
        if now >= deadline:
            debug_log.log(LOG, f"nothing more from the rock at {tile[:2]} in {now - t_click:.1f}s")
            return "timeout"
        check_at = grace if (ores == 0 and now < grace) else now + FALLBACK_POLL_S
        res = bridge_events.await_outcome(events, signals, min(deadline, check_at) - now,
                                          confirm=confirm, since=since, reaction=False,
                                          now_fn=_now, sleep_fn=_sleep)
        if res:
            if res.confirmed == "depleted":
                debug_log.log(LOG, f"rock at {tile[:2]} depleted after {ores + 1} ore(s), "
                                   f"{_now() - t_click:.2f}s after the click")
                return "depleted"
            ores += 1
            xp_base = res.confirmed[1]
            deadline = _now() + ORE_BUDGET_S
            since = events.mark() if events is not None else None
            continue
        if ores == 0 and _now() >= grace and controller.get_combat().get("isIdle", True):
            debug_log.log(LOG, f"no swing and no ore {_now() - t_click:.1f}s after clicking the "
                               f"rock at {tile[:2]} -- the click did not take")
            return "failed"


# --- the deposit trip ----------------------------------------------------------------------

def _find_box(cfg):
    box_id = cfg.get("depositBoxId")
    for obj in controller.get_objects(interactable=True) or []:
        if obj.get("id") == box_id:
            return obj
    return None


def deposit_trip(ctx, cfg):
    """Use the ore on the deposit box and confirm the ore left the inventory. The game walks
    to the box and deposits on arrival, so the budget carries the walk."""
    events = ctx.events
    for attempt in range(DEPOSIT_TRIES):
        inv = controller.get_inventory() or []
        ore = next((i for i in inv if i.get("name") == cfg.get("oreName", "Iron ore")), None)
        if ore is None:
            ctx.state.data[FULL_KEY] = False
            return True
        box = _find_box(cfg)
        if box is None:
            debug_log.log(LOG, "[!] no Bank Deposit Box in the scene")
            return False
        controller.open_inventory_tab(ctx.ser)
        since = events.mark() if events is not None else None
        # The gem sack sits beside the ore: no simulated misclick on either click.
        ok = controller.click_entity(ctx.ser, ore, offset=(0, 0), check_ui_blockers=False,
                                     simulate_mistakes=False, expected_action="Use")
        if getattr(ok, "withheld", False):
            debug_log.log(LOG, "left-click is not Use on the ore -- clicking it anyway")
            ok = controller.click_entity(ctx.ser, ore, offset=(0, 0), check_ui_blockers=False,
                                         simulate_mistakes=False)
        if not ok:
            continue
        controller.click_entity(ctx.ser, box, refresh_target=_refresh(box), simulate_mistakes=False)
        tiles = max(0, int(box.get("distance") or 0))
        budget = DEPOSIT_BASE_S + tiles * WALK_S_PER_TILE
        debug_log.log(LOG, f"depositing {_ore_count(cfg)} ore at the box (dist={tiles})")
        res = bridge_events.await_outcome(events, {"INVENTORY_CHANGED": None}, budget,
                                          confirm=lambda: _ore_count(cfg) == 0, since=since,
                                          reaction=False, now_fn=_now, sleep_fn=_sleep)
        if res:
            ctx.state.data[FULL_KEY] = False
            debug_log.log(LOG, f"deposited ({res.latency_s:.1f}s)")
            return True
        debug_log.log(LOG, f"[!] the ore is still in the inventory after {budget:.1f}s "
                           f"(attempt {attempt + 1}/{DEPOSIT_TRIES}; box onScreen="
                           f"{box.get('onScreen')})")
    ctx.state.trigger_safety_stop(f"the deposit did not empty the ore after {DEPOSIT_TRIES} tries")
    return False


def return_to_stand(ctx, cfg, rocks, spent_at):
    """Shift-click the stand tile and wait until the player is exactly on it, parking the
    cursor on a rock once within HOVER_WITHIN_TILES so the first click is in place. Returns the
    hovered rock (or True with none hovered) on arrival, False when the walk did not land."""
    events = ctx.events
    stand = _stand(cfg)
    here = _player_tile()
    if here == stand:
        return True
    point = controller.get_ground_walk_point(*stand)
    if not point.get("onScreen"):
        debug_log.log(LOG, "stand tile not on screen -- routing to it")
        travel.walk_to(ctx, {"x": stand[0], "y": stand[1], "plane": stand[2]}, goal_adjacent=False)
    else:
        controller.key_down(ctx.ser, "SHIFT")
        controller.click_entity(ctx.ser, point, offset=(0, 0),
                                refresh_target=controller.ground_tile_refresh_target(*stand))
        controller.key_up(ctx.ser, "SHIFT")
    hovered = None
    deadline = _now() + RETURN_BUDGET_S
    while _now() < deadline:
        res = bridge_events.await_outcome(events, {"PLAYER_STOPPED": None, "PLAYER_MOVING": None},
                                          RETURN_POLL_S, confirm=lambda: _player_tile() == stand,
                                          since=None, reaction=False, now_fn=_now, sleep_fn=_sleep)
        if res:
            return hovered or True
        here = _player_tile()
        if hovered is None and here is not None and here[2] == stand[2] and \
                max(abs(here[0] - stand[0]), abs(here[1] - stand[1])) <= HOVER_WITHIN_TILES:
            rock = pick_rock(rocks, up=lambda r: _rock_live(r) is not None, last=None,
                             spent_at=spent_at)
            if rock is not None:
                controller.hover_entity(ctx.ser, rock, refresh_target=_refresh(rock))
                hovered = rock
    debug_log.log(LOG, f"[!] did not reach the stand tile {stand[:2]} in {RETURN_BUDGET_S:.0f}s")
    return False


# --- the chain ---------------------------------------------------------------------------

def _rocks(ctx, cfg):
    data = ctx.state.data
    rocks = data.get(ROCKS_KEY)
    if rocks:
        return rocks
    rocks = discover_rocks(controller.get_objects(interactable=True) or [], _stand(cfg))
    if rocks:
        data[ROCKS_KEY] = rocks
        debug_log.log(LOG, f"rocks around {_stand(cfg)[:2]}: "
                           + ", ".join(f"{_tile(r)} id {r['id']}" for r in rocks))
    return rocks


def tick(ctx):
    cfg = ctx.state.config
    data = ctx.state.data
    player = controller.get_player() or {}
    region = cfg.get("regionId")
    if region is not None and player.get("regionId") not in (None, region):
        ctx.state.trigger_safety_stop(f"not in the Mining Guild (region {player.get('regionId')})")
        return
    rocks = _rocks(ctx, cfg)
    if not rocks:
        ctx.state.trigger_safety_stop("no iron rocks adjacent to the stand tile")
        return
    spent_at = data.setdefault(SPENT_KEY, {})

    hovered = data.pop(HOVERED_KEY, None)
    if _player_tile() != _stand(cfg):
        back = return_to_stand(ctx, cfg, rocks, spent_at)
        if not back:
            data[RETURN_FAILS_KEY] = data.get(RETURN_FAILS_KEY, 0) + 1
            if data[RETURN_FAILS_KEY] >= RETURN_FAILS_STOP:
                ctx.state.trigger_safety_stop("cannot get back to the stand tile")
            return
        data[RETURN_FAILS_KEY] = 0
        hovered = back if isinstance(back, dict) else None
    elif not controller.get_combat().get("isIdle", True):
        return      # mid-swing after a restart: let it finish, the next tick takes over

    started = _now()
    last = None
    limit = data.get(CHAIN_LIMIT_KEY, CHAIN_MAX_TARGETS)
    for _ in range(limit):
        if ctx.state.safety_stopped or ctx.state.paused or _now() - started >= CHAIN_MAX_S:
            data[HOVERED_KEY] = hovered
            return
        if deposit_needed(ctx, _free_slots()):
            if not deposit_trip(ctx, cfg):
                return
            back = return_to_stand(ctx, cfg, rocks, spent_at)
            if not back:
                return
            hovered = back if isinstance(back, dict) else None
            last = None
        live = _rock_live(hovered) if hovered is not None else None
        if live is None:
            rock = pick_rock(rocks, up=lambda r: _rock_live(r) is not None, last=last, spent_at=spent_at)
            if rock is None:
                controller.fidget_once()
                _sleep(RESPAWN_WAIT_S)
                continue
            live = _rock_live(rock)
            if live is None:
                continue
        else:
            rock = hovered
        target = dict(rock)
        target.update(live)
        target.setdefault("name", ROCK_NAME)
        nxt = pick_rock(rocks, up=lambda r: True, last=_tile(rock), spent_at=spent_at)
        if nxt is not None and _tile(nxt) == _tile(rock):
            nxt = None
        outcome = mine_rock(ctx, target, hover_next=nxt)
        if outcome == "depleted":
            spent_at[_tile(rock)] = _now()
            last = _tile(rock)
            hovered = nxt
            data[FAILS_KEY] = 0
            controller.human_delay(*SWAP_REACTION_S)
            continue
        hovered = None
        if outcome == "failed":
            data[FAILS_KEY] = data.get(FAILS_KEY, 0) + 1
            if data[FAILS_KEY] >= FAILED_CLICK_STOP:
                ctx.state.trigger_safety_stop(f"{FAILED_CLICK_STOP} rock clicks in a row produced nothing")
                return
            if controller.continue_box_open():
                controller.dismiss_continue_box(ctx.ser)
            last = _tile(rock)
            continue
        if outcome == "timeout":
            last = None
            continue
        return      # no_click: hand back to the engine loop
