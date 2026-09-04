# routines/mining.py
#
# Mine whichever of the configured rocks is nearest, react to each ore the moment the game
# reports it, and chain straight onto the next rock without handing control back to the
# engine loop in between.
#
# Rock selection comes from the Controller Settings sidebar panel's "Mining" section
# (SettingsPanel.java): Refresh scans the loaded scene for objects offering "Mine", the
# checkboxes persist to the `rockNames` config key, and GET /mining-config serves it here.
# Before 2026-09-03 this routine ignored the bridge and mined a hardcoded list, so the panel's
# Rock Names field never did anything.
#
# TICK-PERFECT REACTION (user directive 2026-09-03: "mine tick perfect ... without depending
# on tick based info since that will gate our reaction to 0.6 sec intervals"). The signals:
#   * XP_GAINED (skill Mining) on the UDP stream -- pushed the instant the client's own
#     StatChanged fires, sub-tick relative to any poll of ours.
#   * OBJECT_DESPAWNED on the rock's own tile -- the rock swapping to its depleted form.
#   * GET /object/at (uncached) is the TRUTH: the rock is done when its id no longer stands
#     on its tile. UDP decides WHEN we look; HTTP decides WHAT happened (CLAUDE.md's rule --
#     a dropped packet costs the FALLBACK_POLL_S cadence, never a wrong click).
# Depletion, not the XP drop, ends a rock: gem rocks and amethyst keep giving after an ore,
# and the player keeps swinging on their own, so an XP drop with the rock still standing
# only resets the budget.
#
# While the current rock is being mined the cursor is already resting on the NEXT one
# (hover_entity with a refresh_target so it tracks the camera), so the click that follows
# depletion is a near-zero flight -- which is what a human tick-miner's 0.2s reaction is.
#
# EVENT-DRIVEN OUTCOMES (2026-09-04, docs/superpowers/specs/2026-09-04-event-driven-outcomes-
# design.md): the wait is bridge_events.await_outcome -- the same signals, one HTTP confirm per
# signal, the HTTP read deciding alone at the budget -- with mark()/since= in place of the old
# clear() calls (clearing threw away events the engine's on_event still needed). PLAYER_STOPPED
# (per-frame arrival) rebases the walk part of the budget the moment the player reaches a far
# rock, so a queued walk-and-mine is judged from arrival, not from a tiles*0.6s estimate.
#
# BANKING (user request 2026-09-04: "allow it to deposit ore if there is a bank nearby"): a full
# inventory looks for a bank within BANK_MAX_DISTANCE (banker.find_nearby_bank: booth, chest,
# counter, GE booth), deposits everything but the pickaxe, walks back to the tile it last mined
# from, and resumes. With no bank in range it safety-stops exactly as the engine used to -- the
# engine's own stopOnInventoryFull is turned off in get_config so this routine gets to bank
# first (check_safety runs BEFORE tick every iteration, so it would otherwise win the race).
#
# Run with:
#     python controller.py routines/mining.py

import time

import banker
import bridge_events
import controller
import debug_log
import travel

DEFAULT_CONFIG = {
    "enabled": True,
    "rockNames": ["Adamantite rocks", "Runite rocks"],
    "maxRockDistance": 20,

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
        # OFF: the routine banks a full inventory itself when a bank is in range (see
        # bank_if_full) and raises the same "inventory full" stop when none is. The engine's
        # check_safety runs before tick() and would otherwise stop the run before banking.
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

XP_SKILL = "Mining"
MINE_ACTION = "mine"

FALLBACK_POLL_S = 0.25    # idle-check cadence past the grace; also the HTTP-only cadence with no events
ROCK_BUDGET_S = 12.0      # no ore and no depletion for this long (plus the walk) -> re-pick
WALK_S_PER_TILE = 0.6     # a queued interaction is performed on ARRIVAL: budget the walk
IDLE_GRACE_S = 1.5        # idle with no progress past this (plus the walk) -> the click failed
CHAIN_MAX_S = 45.0        # hand control back to the engine loop at least this often
CHAIN_MAX_ROCKS = 40
NEXT_CLICK_DELAY = (0.08, 0.2)   # human reaction after the depletion signal (cursor already there)
BANK_MAX_DISTANCE = 40    # a bank object this close (tiles) means "deposit when full"
RETURN_WALK_TILES = 3     # walk back to the mining stand tile when the bank left us farther than this
KEEP_ITEMS = ["pickaxe"]  # never deposited (substring match, banker.deposit_inventory)
BANK_CONFIG = {"maxBankDistance": BANK_MAX_DISTANCE, "keepItems": KEEP_ITEMS}


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


def get_config(bridge_config):
    """/mining-config (the sidebar panel's selection) with DEFAULT_CONFIG filling any missing
    key. Only a bridge that cannot answer at all falls back to the defaults' rocks -- a panel
    with nothing selected means an idle routine, not a surprise trip to the rune rocks."""
    cfg = controller.get_mining_config()
    if cfg is None:
        return DEFAULT_CONFIG
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    # Whatever the bridge says, the inventory-full stop belongs to this routine (see
    # DEFAULT_CONFIG's safety comment and bank_if_full).
    merged["safety"] = dict(merged.get("safety") or {}, stopOnInventoryFull=False)
    return merged


def mining_xp():
    return (controller.get_player() or {}).get("miningXp")


def rock_tile(rock):
    return rock.get("x"), rock.get("y"), rock.get("plane")


def pick_rock(objects, config, exclude=()):
    names = {n.lower() for n in config.get("rockNames", [])}
    max_dist = config.get("maxRockDistance", 10)
    excluded = set(exclude)

    candidates = []
    for obj in objects:
        if (obj.get("name") or "").lower() not in names:
            continue
        if not obj.get("visible", True):
            continue
        if rock_tile(obj) in excluded:
            continue
        # A depleted variant can share the display name; only the live one offers Mine. An
        # object with no `actions` at all (older client build) is accepted as before.
        actions = obj.get("actions")
        if actions is not None and MINE_ACTION not in {a.lower() for a in actions}:
            continue
        dist = obj.get("distance", 9999)
        if dist < 0 or dist > max_dist:
            continue
        candidates.append(obj)

    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def _signal_match(rock):
    tile = rock_tile(rock)

    def match(payload):
        if payload.get("skill") == XP_SKILL:
            return True
        return (payload.get("x"), payload.get("y"), payload.get("plane")) == tile
    return match


def _refresh(rock):
    return controller.object_refresh_target(rock["x"], rock["y"], rock["plane"], rock["id"])


def _rock_still_there(rock):
    x, y, plane = rock_tile(rock)
    return controller.get_object_at(x, y, plane, rock["id"]) is not None


def _mine_outcome(rock, xp_base):
    """The HTTP confirm behind every signal: 'depleted' when the rock's id no longer stands on
    its tile, ('ore', xp) when Mining XP rose with the rock still up, None otherwise."""
    if not _rock_still_there(rock):
        return "depleted"
    xp = mining_xp()
    if xp is not None and xp_base is not None and xp > xp_base:
        return ("ore", xp)
    return None


def mine_rock(ctx, rock, config, exclude=()):
    """Click `rock`, pre-hover the next candidate, then wait on the pushed signals until the
    rock is confirmed gone. Returns (outcome, next_rock) with outcome one of
    depleted | idle | timeout | no_click. `exclude` lists tiles the pre-hover must not pick:
    the bulk /objects scan is tick-cached, so a rock emptied a moment ago can still appear in
    it under its old id, and without this the cursor went straight back to it."""
    since = ctx.events.mark() if ctx.events is not None else None
    xp_base = mining_xp()
    if not controller.click_entity(ctx.ser, rock, refresh_target=_refresh(rock)):
        return "no_click", None
    t_click = _now()
    tiles = max(0, int(rock.get("distance") or 0))
    walk_s = tiles * WALK_S_PER_TILE
    debug_log.log("mining", f"mining {rock.get('name')} at {rock_tile(rock)} "
                            f"(dist={tiles}, xp={xp_base})")

    # Rest the cursor on the next rock while this one is being mined.
    nxt = pick_rock(controller.get_objects(), config, exclude=[rock_tile(rock), *exclude])
    if nxt is not None:
        controller.hover_entity(ctx.ser, nxt, refresh_target=_refresh(nxt))

    match = _signal_match(rock)
    signals = {"XP_GAINED": match, "OBJECT_DESPAWNED": match, "PLAYER_STOPPED": None}
    walking = walk_s > 0

    def confirm():
        out = _mine_outcome(rock, xp_base)
        if out:
            return out
        # A far rock: the game walks there first. PLAYER_STOPPED (or /player.moving going
        # false) is the arrival; from then on the budgets are the mining budgets alone.
        if walking and (controller.get_player() or {}).get("moving") is False:
            return "arrived"
        return None

    deadline = t_click + ROCK_BUDGET_S + walk_s
    idle_deadline = t_click + IDLE_GRACE_S + walk_s
    while True:
        now = _now()
        if now >= deadline:
            debug_log.log("mining", f"no ore from {rock.get('name')} in "
                                    f"{now - t_click:.1f}s -- re-picking")
            return "timeout", nxt
        # Past the idle grace the idle check runs every FALLBACK_POLL_S; before it, one wait
        # up to the grace (both capped by the overall deadline).
        check_at = idle_deadline if now < idle_deadline else now + FALLBACK_POLL_S
        res = bridge_events.await_outcome(ctx.events, signals, min(deadline, check_at) - now,
                                          confirm=confirm, since=since, reaction=False,
                                          now_fn=_now, sleep_fn=_sleep)
        if res:
            value = res.confirmed
            if value == "depleted":
                debug_log.log("mining", f"{rock.get('name')} depleted "
                                        f"({_now() - t_click:.2f}s after the click)")
                return "depleted", nxt
            now = _now()
            if value == "arrived":
                walking = False
                debug_log.log("mining", f"arrived at {rock.get('name')} "
                                        f"({now - t_click:.2f}s after the click)")
            else:
                # Ore landed but the rock stands (gem rocks, amethyst): the player keeps
                # swinging. Re-arm on the SAME rock; a slow multi-ore rock never times out.
                debug_log.log("mining", f"ore gained (+{value[1] - xp_base} xp), rock still up")
                xp_base = value[1]
            deadline = now + ROCK_BUDGET_S
            idle_deadline = now + IDLE_GRACE_S
            since = ctx.events.mark() if ctx.events is not None else None
            continue

        now = _now()
        if now >= idle_deadline and controller.get_combat().get("isIdle", True):
            debug_log.log("mining", f"idle with no ore from {rock.get('name')} -- "
                                    f"the click did not take")
            return "idle", nxt


# --- banking -----------------------------------------------------------------------------

def _inventory_full():
    return len(controller.get_inventory()) >= getattr(controller, "INVENTORY_SIZE", 28)


def _player_tile():
    p = controller.get_player() or {}
    if p.get("x") is None or p.get("y") is None:
        return None
    return {"x": p["x"], "y": p["y"], "plane": p.get("plane", 0)}


def bank_if_full(ctx):
    """True when this tick was spent on the inventory being full: banked at a bank within
    BANK_MAX_DISTANCE (deposit all but the pickaxe, walk back to the stand tile), or, with no
    bank in range, the same safety stop the engine used to raise."""
    if not _inventory_full():
        return False
    bank = banker.find_nearby_bank(BANK_CONFIG)
    if bank is None:
        ctx.state.trigger_safety_stop(
            f"inventory full and no bank within {BANK_MAX_DISTANCE} tiles")
        return True
    debug_log.log("mining", f"inventory full -- banking at {bank.get('name')} "
                            f"(dist={bank.get('distance')})")
    stand = ctx.state.data.get("mining_stand_tile")
    if not banker.open_bank(ctx, bank):
        debug_log.log("mining", "could not open the bank -- will retry next tick")
        return True
    banker.deposit_inventory(ctx, BANK_CONFIG)
    banker.close_bank(ctx)
    if _inventory_full():
        ctx.state.trigger_safety_stop("inventory still full after banking")
        return True
    here = _player_tile()
    if stand is not None and here is not None and max(
            abs(here["x"] - stand["x"]), abs(here["y"] - stand["y"])) > RETURN_WALK_TILES:
        debug_log.log("mining", f"walking back to the rocks at ({stand['x']},{stand['y']})")
        travel.walk_to(ctx, stand, goal_adjacent=False)
    return True


def _revalidate(rock):
    """The pre-hovered candidate came from a tick-cached bulk scan; confirm it still stands
    (fresh screen position included) before clicking it."""
    if rock is None:
        return None
    x, y, plane = rock_tile(rock)
    live = controller.get_object_at(x, y, plane, rock["id"])
    if live is None:
        return None
    merged = dict(rock)
    merged.update(live)
    return merged


def tick(ctx):
    config = ctx.state.config
    data = ctx.state.data

    if not config.get("rockNames"):
        if not data.get("warned_no_rocks"):
            debug_log.log("mining", "no rocks selected -- pick some in the Controller "
                                    "Settings panel's Mining section")
            data["warned_no_rocks"] = True
        return
    data.pop("warned_no_rocks", None)

    if bank_if_full(ctx):
        return

    if not controller.get_combat().get("isIdle", True):
        return  # already mid-swing or walking to a rock (e.g. after a restart) -- don't reclick

    rock = pick_rock(controller.get_objects(), config)
    if rock is None:
        return

    started = _now()
    mined = []
    for _ in range(CHAIN_MAX_ROCKS):
        if rock is None or ctx.state.safety_stopped or _now() - started >= CHAIN_MAX_S:
            return
        # Where we mine FROM -- the walk-back target after a bank trip (a rock's own tile is
        # not walkable). Refreshed per rock so a drifting session returns to the last spot.
        stand = _player_tile()
        if stand is not None:
            data["mining_stand_tile"] = stand
        outcome, nxt = mine_rock(ctx, rock, config, exclude=mined)
        if outcome != "depleted":
            # Bookkeeping done; hand back so the engine loop (safety checks, breaks,
            # PLAYER_DIED) gets its turn before the next attempt.
            return
        mined.append(rock_tile(rock))
        if _inventory_full():
            return    # no room for the next ore: next tick banks (or stops)
        controller.human_delay(*NEXT_CLICK_DELAY)
        rock = _revalidate(nxt)
        if rock is None:
            # The bulk list is tick-cached and may still show the rock we just emptied under
            # its old id, so exclude every tile mined this chain rather than trusting it.
            rock = pick_rock(controller.get_objects(fresh=True), config, exclude=mined)
