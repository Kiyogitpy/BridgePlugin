# gathering.py
#
# The skill-agnostic half of routines/mining.py: click the nearest object offering a gathering
# action, react to each resource the moment the game reports it, and chain straight onto the
# next object without handing control back to the engine loop in between. routines/mining.py
# and routines/woodcutting.py are thin files over this; each supplies a Profile saying which
# skill, action, XP field and config keys it works with.
#
# Extracted from routines/mining.py 2026-09-07 (USER: "isnt our generic mining.py possible to do
# woodcutting to?"). Nothing about the loop changed in the move -- everything below was measured
# for mining and holds for woodcutting for the same reasons:
#
# TICK-PERFECT REACTION (user directive 2026-09-03: "mine tick perfect ... without depending
# on tick based info since that will gate our reaction to 0.6 sec intervals"). The signals:
#   * XP_GAINED (the profile's skill) on the UDP stream -- pushed the instant the client's own
#     StatChanged fires, sub-tick relative to any poll of ours.
#   * OBJECT_DESPAWNED on the object's own tile -- the rock/tree swapping to its spent form.
#   * GET /object/at (uncached) is the TRUTH: the object is done when its id no longer stands
#     on its tile. UDP decides WHEN we look; HTTP decides WHAT happened (CLAUDE.md's rule --
#     a dropped packet costs the FALLBACK_POLL_S cadence, never a wrong click).
# Depletion, not the XP drop, ends an object: gem rocks and amethyst keep giving after an ore
# and a tree keeps giving logs until it falls, so an XP drop with the object still standing
# only resets the budget. A felled tree's stump offers no "Chop down", so pick_target skips it
# on its own and a lone tree simply idles until it respawns.
#
# While the current object is being worked the cursor is already resting on the NEXT one
# (hover_entity with a refresh_target so it tracks the camera), so the click that follows
# depletion is a near-zero flight -- which is what a human tick-miner's 0.2s reaction is.
#
# EVENT-DRIVEN OUTCOMES (2026-09-04, docs/superpowers/specs/2026-09-04-event-driven-outcomes-
# design.md): the wait is bridge_events.await_outcome -- the same signals, one HTTP confirm per
# signal, the HTTP read deciding alone at the budget -- with mark()/since= in place of the old
# clear() calls (clearing threw away events the engine's on_event still needed). PLAYER_STOPPED
# (per-frame arrival) rebases the walk part of the budget the moment the player reaches a far
# object, so a queued walk-and-gather is judged from arrival, not from a tiles*0.6s estimate.
#
# A FULL INVENTORY is settled by the `fullInventory` config key, defaulting per profile:
#   bank -- find a bank within BANK_MAX_DISTANCE (banker.find_nearby_bank: booth, chest,
#           counter, GE booth), deposit everything but the profile's keep_items, walk back to
#           the tile we last gathered from, resume. Mining's default (user request 2026-09-04:
#           "allow it to deposit ore if there is a bank nearby").
#   drop -- shift-drop everything but the keep_items and carry on. Woodcutting's default:
#           power-chopping is the normal loop there.
#   stop -- the plain "inventory full" safety stop.
# The engine's own stopOnInventoryFull is turned off in get_config so this module gets to act
# first (check_safety runs BEFORE tick every iteration, so it would otherwise win the race).

import time
from dataclasses import dataclass
from typing import Callable, Sequence

import banker
import bridge_events
import controller
import debug_log
import travel


@dataclass(frozen=True)
class Profile:
    """Everything that differs between one gathering skill and the next."""

    key: str                   # log category, and the prefix of this routine's state.data keys
    skill: str                 # the XP_GAINED payload's skill name, e.g. "Mining"
    action: str                # lowercase menu action the object must offer, e.g. "mine"
    xp_field: str              # the /player field carrying that skill's cumulative xp
    names_key: str             # config key holding the object names, e.g. "rockNames"
    distance_key: str          # config key holding the max distance
    noun: str                  # "rock" / "tree", for log lines and stop reasons
    keep_items: Sequence[str]  # never deposited or dropped (substring match)
    config_fetch: Callable     # () -> the bridge's config dict for this skill, or None
    default_config: dict       # used only when the bridge cannot answer at all
    full_inventory: str = "bank"   # bank | drop | stop, unless the config says otherwise
    panel_section: str = "Gathering"   # named in the "nothing selected" log line


FALLBACK_POLL_S = 0.25    # idle-check cadence past the grace; also the HTTP-only cadence with no events
GATHER_BUDGET_S = 12.0    # nothing gained and no depletion for this long (plus the walk) -> re-pick
WALK_S_PER_TILE = 0.6     # a queued interaction is performed on ARRIVAL: budget the walk
IDLE_GRACE_S = 1.5        # idle with no progress past this (plus the walk) -> the click failed
# ...but only if the idle SUSTAINS for this long. An animation trough between two swings is not
# idleness: MEASURED live 2026-09-07 in the Motherlode Mine over 45s of uninterrupted, XP-earning
# mining, /combat.isIdle read true in 12 separate troughs -- median 1.2s, p90 1.28s, MAX 2.45s --
# so IDLE_GRACE_S alone sits inside the normal distribution of a working action and fires
# spuriously. That cost real throughput there (every spent vein was reported as "the click did
# not take", which then fed motherlode_mine's failure blacklist and set good veins aside), and
# the same trough exists for any rock or tree whose swing cycle outlasts the grace. This is the
# chinchompa_hunting lesson generalised: sustained idle is a signal, an instantaneous -1 is not.
IDLE_CONFIRM_S = 3.0
CHAIN_MAX_S = 45.0        # hand control back to the engine loop at least this often
CHAIN_MAX_TARGETS = 40
NEXT_CLICK_DELAY = (0.08, 0.2)   # human reaction after the depletion signal (cursor already there)
BANK_MAX_DISTANCE = 40    # a bank object this close (tiles) means "deposit when full"
RETURN_WALK_TILES = 3     # walk back to the stand tile when the bank left us farther than this


def _now():
    return time.time()


def _sleep(s):
    time.sleep(s)


def bank_config(profile):
    return {"maxBankDistance": BANK_MAX_DISTANCE, "keepItems": list(profile.keep_items)}


def get_config(profile):
    """The bridge's section for this skill (the sidebar panel's selection) with the profile's
    default_config filling any missing key. Only a bridge that cannot answer at all falls back
    to the defaults' object names -- a panel with nothing selected means an idle routine, not a
    surprise trip to the rune rocks."""
    cfg = profile.config_fetch()
    if cfg is None:
        return profile.default_config
    merged = dict(profile.default_config)
    merged.update(cfg)
    # Whatever the bridge says, the inventory-full decision belongs to this module (see the
    # module docstring and full_inventory_phase).
    merged["safety"] = dict(merged.get("safety") or {}, stopOnInventoryFull=False)
    return merged


def skill_xp(profile):
    return (controller.get_player() or {}).get(profile.xp_field)


def target_tile(obj):
    return obj.get("x"), obj.get("y"), obj.get("plane")


# EVERY bulk scan here passes interactable=True. USER 2026-09-07, minutes after a client
# rebuild: "whatever we did recently has slowed down the client drasticly stutters is happening".
# MEASURED in the Motherlode Mine: the scene holds 8,794 objects and only 275 of them offer any
# menu action, and the full scan costs 36ms median / 261ms MAX of CLIENT THREAD time against
# 1ms for the filtered one. ObjectProvider's own comment says exactly why -- "skipping them
# BEFORE the per-object work is what makes the bulk scan cheap" -- and this module simply never
# opted in, which went unnoticed until a scene this dense.
#
# Safe by construction: pick_target only ever returns an object that offers `profile.action`, so
# an object with no actions at all could never have been chosen. The one thing it changes is the
# "actions is None means an older client build" tolerance below, which cannot trigger when the
# server has already filtered on actions -- against such a build this would idle rather than
# misbehave.


def pick_target(profile, objects, config, exclude=()):
    names = {n.lower() for n in config.get(profile.names_key, [])}
    max_dist = config.get(profile.distance_key, 10)
    excluded = set(exclude)

    candidates = []
    for obj in objects:
        if (obj.get("name") or "").lower() not in names:
            continue
        if not obj.get("visible", True):
            continue
        if target_tile(obj) in excluded:
            continue
        # A spent variant can share the display name (a depleted vein, a felled tree's stump);
        # only the live one offers the action. An object with no `actions` at all (older client
        # build) is accepted as before.
        actions = obj.get("actions")
        if actions is not None and profile.action not in {a.lower() for a in actions}:
            continue
        dist = obj.get("distance", 9999)
        if dist < 0 or dist > max_dist:
            continue
        candidates.append(obj)

    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def _signal_match(profile, target):
    tile = target_tile(target)

    def match(payload):
        if payload.get("skill") == profile.skill:
            return True
        return (payload.get("x"), payload.get("y"), payload.get("plane")) == tile
    return match


def _refresh(target):
    return controller.object_refresh_target(target["x"], target["y"], target["plane"],
                                            target["id"])


def _still_there(target):
    x, y, plane = target_tile(target)
    return controller.get_object_at(x, y, plane, target["id"]) is not None


def _outcome(profile, target, xp_base):
    """The HTTP confirm behind every signal: 'depleted' when the object's id no longer stands on
    its tile, ('gained', xp) when the skill's XP rose with the object still up, None otherwise."""
    if not _still_there(target):
        return "depleted"
    xp = skill_xp(profile)
    if xp is not None and xp_base is not None and xp > xp_base:
        return ("gained", xp)
    return None


def gather(profile, ctx, target, config, exclude=(), pick_next=None):
    """Click `target`, pre-hover the next candidate, then wait on the pushed signals until the
    object is confirmed gone. Returns (outcome, next_target) with outcome one of
    depleted | idle | timeout | no_click. `exclude` lists tiles the pre-hover must not pick:
    the bulk /objects scan is tick-cached, so an object spent a moment ago can still appear in
    it under its old id, and without this the cursor went straight back to it.

    `pick_next(exclude) -> obj | None` overrides WHICH object is pre-hovered. It exists because
    a routine that chooses its own next target must pre-hover the SAME one: motherlode_mine
    discards this function's `next_target` and re-picks through its own floor/anchor/blacklist
    filters, so the default chooser was parking the cursor on a different vein entirely --
    MEASURED live 2026-09-07, the default would have hovered (3753,5684) while the routine went
    on to click (3752,5684), and 19 of the 66 veins in its pre-hover pool were on the FLOOR
    BELOW. A hover aimed at the wrong object buys nothing and costs a cursor flight."""
    since = ctx.events.mark() if ctx.events is not None else None
    xp_base = skill_xp(profile)
    if not controller.click_entity(ctx.ser, target, refresh_target=_refresh(target)):
        return "no_click", None
    t_click = _now()
    tiles = max(0, int(target.get("distance") or 0))
    walk_s = tiles * WALK_S_PER_TILE
    debug_log.log(profile.key, f"working {target.get('name')} at {target_tile(target)} "
                               f"(dist={tiles}, xp={xp_base})")

    match = _signal_match(profile, target)
    signals = {"XP_GAINED": match, "OBJECT_DESPAWNED": match, "PLAYER_STOPPED": None}
    walking = walk_s > 0

    # Rest the cursor on the next target while this one is being worked -- but ONLY ONCE THE
    # PLAYER IS STANDING STILL.
    #
    # USER 2026-09-07: "the hover function isnt doing anything if we hover before movement is
    # finished". This used to fire the instant the click was issued, and the click is issued
    # from a distance on purpose (the game queues the interaction and performs it on arrival),
    # so the player was still WALKING -- MEASURED in the same run at 0.58s, 1.08s, 1.25s, 1.31s,
    # 1.70s, 2.16s and 2.22s of walking after the click. refresh_target keeps the few hundred ms
    # of FLIGHT honest, but nothing tracks the target after the cursor lands: the player keeps
    # walking, the projection slides out from under it, and by the time the hover matters (the
    # current object depletes 40s later) the cursor is nowhere near. The park is only stable
    # once the player has stopped, which is exactly the giants-foundry lesson already in
    # CLAUDE.md -- USER, then: "hover after the move, otherwise the mouse moves away from the
    # object".
    #
    # Standing still at the object also means the candidate is picked from a FRESHER scan than
    # the one at click time, so this is not merely later, it is better informed.
    hovered = False

    def prehover():
        nonlocal nxt, hovered
        if hovered:
            return
        hovered = True
        chooser = pick_next or (lambda excl: pick_target(profile,
                                                 controller.get_objects(interactable=True),
                                                         config, exclude=excl))
        fresh = chooser([target_tile(target), *exclude])
        if fresh is not None:
            nxt = fresh
        if nxt is None:
            return
        ok = controller.hover_entity(ctx.ser, nxt, refresh_target=_refresh(nxt))
        # LOGGED for the same reason the idle drift is: a hover moves the cursor with no click,
        # so one that silently no-ops (off-window, or behind a UI panel -- hover_entity's own
        # documented behaviour) is invisible in a log full of clicks. That is exactly why this
        # bug survived: nothing in the log could show the cursor was not where we thought.
        debug_log.log(profile.key, f"pre-hover {'->' if ok else 'SKIPPED for'} the next "
                                   f"{profile.noun} at {target_tile(nxt)}")

    # The candidate is resolved UP FRONT so the return value is exactly what it always was;
    # only the CURSOR MOVE is deferred. prehover() re-picks from a fresher scan when it finally
    # runs, since by then the player is standing at the object.
    nxt = (pick_next or (lambda excl: pick_target(profile, controller.get_objects(interactable=True),
                                                  config, exclude=excl)))(
        [target_tile(target), *exclude])
    if not walking:
        prehover()

    def confirm():
        out = _outcome(profile, target, xp_base)
        if out:
            return out
        # A far target: the game walks there first. PLAYER_STOPPED (or /player.moving going
        # false) is the arrival; from then on the budgets are the working budgets alone.
        if walking and (controller.get_player() or {}).get("moving") is False:
            return "arrived"
        return None

    deadline = t_click + GATHER_BUDGET_S + walk_s
    idle_deadline = t_click + IDLE_GRACE_S + walk_s
    idle_since = None
    while True:
        now = _now()
        if now >= deadline:
            debug_log.log(profile.key, f"nothing from {target.get('name')} in "
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
                debug_log.log(profile.key, f"{target.get('name')} depleted "
                                           f"({_now() - t_click:.2f}s after the click)")
                return "depleted", nxt
            now = _now()
            if value == "arrived":
                walking = False
                debug_log.log(profile.key, f"arrived at {target.get('name')} "
                                           f"({now - t_click:.2f}s after the click)")
                prehover()
            else:
                # A resource landed but the object stands (gem rocks, amethyst, a tree that has
                # not fallen yet): the player keeps swinging. Re-arm on the SAME target; a slow
                # multi-resource object never times out.
                debug_log.log(profile.key, f"+{value[1] - xp_base} {profile.skill} xp, "
                                           f"{profile.noun} still up")
                xp_base = value[1]
                # A resource landed, so the player is demonstrably standing at the object --
                # the belt to arrival's braces, for an arrival signal we missed.
                walking = False
                prehover()
            deadline = now + GATHER_BUDGET_S
            idle_deadline = now + IDLE_GRACE_S
            idle_since = None
            since = ctx.events.mark() if ctx.events is not None else None
            continue

        now = _now()
        if now < idle_deadline:
            continue
        if not controller.get_combat().get("isIdle", True):
            idle_since = None       # a swing: whatever we were watching is still working
            continue
        # Idle -- but an animation trough reads exactly like this, so require it to LAST (see
        # IDLE_CONFIRM_S). Timing it from the first idle read rather than resetting the deadline
        # means a genuinely dead click still costs only IDLE_GRACE_S + IDLE_CONFIRM_S.
        if idle_since is None:
            idle_since = now
            continue
        if now - idle_since >= IDLE_CONFIRM_S:
            debug_log.log(profile.key, f"idle with nothing gained from {target.get('name')} for "
                                       f"{now - idle_since:.1f}s -- the click did not take")
            return "idle", nxt


# --- a full inventory --------------------------------------------------------------------

def _inventory_full():
    return len(controller.get_inventory()) >= getattr(controller, "INVENTORY_SIZE", 28)


def _player_tile():
    p = controller.get_player() or {}
    if p.get("x") is None or p.get("y") is None:
        return None
    return {"x": p["x"], "y": p["y"], "plane": p.get("plane", 0)}


def _stand_key(profile):
    return f"{profile.key}_stand_tile"


def _bank_it(profile, ctx):
    cfg = bank_config(profile)
    bank = banker.find_nearby_bank(cfg)
    if bank is None:
        ctx.state.trigger_safety_stop(
            f"inventory full and no bank within {BANK_MAX_DISTANCE} tiles")
        return True
    debug_log.log(profile.key, f"inventory full -- banking at {bank.get('name')} "
                               f"(dist={bank.get('distance')})")
    stand = ctx.state.data.get(_stand_key(profile))
    if not banker.open_bank(ctx, bank):
        debug_log.log(profile.key, "could not open the bank -- will retry next tick")
        return True
    banker.deposit_inventory(ctx, cfg)
    banker.close_bank(ctx)
    if _inventory_full():
        ctx.state.trigger_safety_stop("inventory still full after banking")
        return True
    here = _player_tile()
    if stand is not None and here is not None and max(
            abs(here["x"] - stand["x"]), abs(here["y"] - stand["y"])) > RETURN_WALK_TILES:
        debug_log.log(profile.key, f"walking back to the {profile.noun}s at "
                                   f"({stand['x']},{stand['y']})")
        travel.walk_to(ctx, stand, goal_adjacent=False)
    return True


def _drop_it(profile, ctx):
    keep = [k.lower() for k in profile.keep_items]
    items = [i for i in (controller.get_inventory() or [])
             if not any(k in (i.get("name") or "").lower() for k in keep)]
    debug_log.log(profile.key, f"inventory full -- dropping {len(items)} items")
    controller.drop_items(ctx.ser, items)
    if _inventory_full():
        # Not a shrug: a drop that frees nothing means the next tick is full again, and a
        # routine that keeps clicking a target it has no room for is the silent loop this
        # codebase keeps paying for.
        ctx.state.trigger_safety_stop("inventory still full after dropping")
    return True


def full_inventory_phase(profile, ctx, config):
    """True when this tick was spent on the inventory being full. Which of bank / drop / stop
    happens is the `fullInventory` config key, defaulting to the profile's own."""
    if not _inventory_full():
        return False
    policy = str(config.get("fullInventory") or profile.full_inventory).lower()
    if policy == "drop":
        return _drop_it(profile, ctx)
    if policy == "stop":
        ctx.state.trigger_safety_stop("inventory full")
        return True
    return _bank_it(profile, ctx)


def _revalidate(target):
    """The pre-hovered candidate came from a tick-cached bulk scan; confirm it still stands
    (fresh screen position included) before clicking it."""
    if target is None:
        return None
    x, y, plane = target_tile(target)
    live = controller.get_object_at(x, y, plane, target["id"])
    if live is None:
        return None
    merged = dict(target)
    merged.update(live)
    return merged


def tick(profile, ctx):
    config = ctx.state.config
    data = ctx.state.data

    if not config.get(profile.names_key):
        if not data.get(f"{profile.key}_warned_empty"):
            debug_log.log(profile.key, f"nothing selected -- pick some {profile.noun}s in the "
                                       f"Controller Settings panel's {profile.panel_section} "
                                       f"section")
            data[f"{profile.key}_warned_empty"] = True
        return
    data.pop(f"{profile.key}_warned_empty", None)

    if full_inventory_phase(profile, ctx, config):
        return

    if not controller.get_combat().get("isIdle", True):
        return  # already mid-action or walking to a target (e.g. after a restart) -- don't reclick

    target = pick_target(profile, controller.get_objects(interactable=True), config)
    if target is None:
        return

    started = _now()
    worked = []
    for _ in range(CHAIN_MAX_TARGETS):
        if target is None or ctx.state.safety_stopped or _now() - started >= CHAIN_MAX_S:
            return
        # Where we work FROM -- the walk-back target after a bank trip (the object's own tile is
        # not walkable). Refreshed per target so a drifting session returns to the last spot.
        stand = _player_tile()
        if stand is not None:
            data[_stand_key(profile)] = stand
        outcome, nxt = gather(profile, ctx, target, config, exclude=worked)
        if outcome != "depleted":
            # Bookkeeping done; hand back so the engine loop (safety checks, breaks,
            # PLAYER_DIED) gets its turn before the next attempt.
            return
        worked.append(target_tile(target))
        if _inventory_full():
            return    # no room for the next resource: next tick banks, drops or stops
        controller.human_delay(*NEXT_CLICK_DELAY)
        target = _revalidate(nxt)
        if target is None:
            # The bulk list is tick-cached and may still show the object we just spent under
            # its old id, so exclude every tile worked this chain rather than trusting it.
            target = pick_target(profile,
                                 controller.get_objects(fresh=True, interactable=True),
                                 config,
                                 exclude=worked)
