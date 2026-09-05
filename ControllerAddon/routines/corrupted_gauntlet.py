"""Corrupted Gauntlet -- sub-project 2: the Hunllef fight, door-start mode.

Spec: docs/superpowers/specs/2026-09-05-hunllef-fight-design.md. Start it standing in the room in
front of the Hunllef's Barrier with the loadout in hand (both perfected corrupted weapons, fish,
an egniol). It checks the loadout, turns the first protection prayer on, Quick-passes the
barrier, fights on a tick-chained priority ladder (hunllef.ladder), opens the Reward Chest in
the lobby, and stops. Sub-project 3 adds the maze prep in front of `enter`.

Every decision is in hunllef.py (pure, unit-tested, replay-tested). This file only reads the
bridge, performs the top rung with the existing click primitives, and waits for the next game
tick. The fight loop holds tick() for the whole fight: the engine loop's ~1.1 s per iteration
would eat the 3 s prayer window; the panic key lives on its own thread and keeps working.

`NPC_TRACKED_ANIMATION_CHANGED` (the event feed_tracker prefers) only flows while the Hunllef is
our interaction target, so every weapon wield away from it opens a 2-3 tick event blind spot --
`feed_tracker`'s snapshot cross-check (C3, final-fix-brief.md, `/npcs`' `animationId` field) covers
exactly that gap.
"""
from __future__ import annotations

import time

import controller
import debug_log
import gauntlet_ids as g
import hunllef as h
import instance_nav
import travel

VARBIT_SUBSCRIPTIONS = (g.VARBIT_FIGHTING_HUNLLEF,)

DEFAULT_CONFIG = {
    "eat_at": 45,               # spec: correct-prayer hits are 2-10, tornado/stomp 21-38; human's low was 23
    "egniol_at": 15,
    "min_fish": 10,
    "min_egniol": 1,
    "melee_weapon": "bow",
    "offensive_prayers": {"bow": ["RIGOUR", "DEADEYE", "EAGLE_EYE"],
                          "staff": ["AUGURY", "MYSTIC_MIGHT", "MYSTIC_LORE"]},
    "escape_radius": 6,
    "attack_range": 7,
    "tornado_danger": 2,
    "tornado_avoid": 3,
    "fight_max_s": 480.0,
    "objects_radius": 12,
}
NO_HUNLLEF_TICKS = 10          # consecutive fight ticks without a Hunllef before re-deriving the phase
NO_SNAPSHOT_TICKS = 20         # consecutive unreadable snapshots before a safety stop
TICK_S = 0.6
ACTION_BUDGET_S = 1.2          # confirm window for a prayer/wield/eat click (two ticks)
ENTER_BUDGET_S = 8.0
ENTER_MAX_ATTEMPTS = 3         # barrier quick-pass ceiling before safety stop
LOOT_MAX_ATTEMPTS = 5          # chest open ceiling before safety stop


def get_config(bridge_config):
    return DEFAULT_CONFIG


# ----------------------------------------------------------------------------- reads

def read_snapshot(cfg=DEFAULT_CONFIG):
    player = controller.get_player()
    if not player or player.get("instX") is None:
        return None
    return h.FightSnapshot.from_bridge(
        player=player,
        npcs=controller.get_npcs(fresh=True),
        objects=controller.get_objects(radius=cfg["objects_radius"], fresh=True),
        prayers=controller.get_prayers(),
        equipment=controller.get_equipment(),
        inventory=controller.get_inventory(),
        combat=controller.get_combat())


def derive_phase(data, npcs, objects):
    """Phase from live state, never from a remembered default (the mahogany_homes lesson)."""
    ids = {o.get("id") for o in objects or []}
    if any(n.get("id") in g.HUNLLEF for n in npcs or []):
        data["cg_fight_seen"] = True
        return "fight"
    if g.OBJ_BARRIER in ids and not data.get("cg_fight_seen"):
        return "enter"
    if g.OBJ_REWARD_CHEST in ids and data.get("cg_fight_seen"):
        return "done" if data.get("cg_chest_opened") else "loot"
    return "unknown"


def feed_tracker(tracker, snap, events_since):
    """Feeds BOTH sources every tick when the event stream exists (C3, final-fix-brief.md):
    events first (tracker.observe per Hunllef animation-change event), then the snapshot's own
    `/npcs` animation read via tracker.observe_snapshot -- but only when that snapshot animation
    id is an attack-like or marker id that none of this tick's events already carried for the
    Hunllef. `NPC_TRACKED_ANIMATION_CHANGED` only fires while the Hunllef is our interaction
    target, so every weapon wield away from it opens a 2-3 tick event blind spot; the snapshot
    cross-check covers exactly that gap without double-counting an animation the events already
    reported. HTTP stays the truth for everything acted on -- the tracker only decides WHEN to
    switch prayer, and a wrong switch costs one 40+ hit, which the marker resync then corrects.

    Without an event stream, the snapshot is the only source and every distinct animation
    transition counts, same as before."""
    if events_since is None:
        if snap.hunllef is not None:
            tracker.observe_snapshot(snap.hunllef["anim"])
        return
    seen = set()
    for event_type, payload in events_since:
        if event_type == "NPC_TRACKED_ANIMATION_CHANGED" and payload and payload.get("id") in g.HUNLLEF:
            anim = payload.get("animationId")
            seen.add(anim)
            tracker.observe(anim)
    if snap.hunllef is not None:
        anim = snap.hunllef["anim"]
        if anim not in seen and (anim in g.ANIM_ATTACK_LIKE or anim in g.ANIM_MARKER):
            tracker.observe_snapshot(anim)


# ----------------------------------------------------------------------------- actions

def _unusable(ctx):
    return ctx.state.data.setdefault("cg_unusable_prayers", set())


def do_prayer(ctx, snap, name, cfg):
    if not controller.open_prayer_tab(ctx.ser):
        debug_log.log("cg", "could not open the prayer tab")
        return False
    icon = (controller.get_prayers().get("icons") or {}).get(name)
    if not icon:
        debug_log.log("cg", f"no icon for {name} -- marking it unusable this session")
        _unusable(ctx).add(name)
        return False
    controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    ok = _confirm(ctx, lambda: (controller.get_prayers().get("active") or {}).get(name))
    if not ok:
        debug_log.log("cg", f"{name} did not activate")
    return bool(ok)


def do_wield(ctx, snap, kind, cfg):
    item = snap.inventory_weapon(kind)
    if item is None:
        return False
    controller.open_inventory_tab(ctx.ser)
    controller.click_entity(ctx.ser, item, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False,
                            expected_action="Wield")
    names = g.BOW_NAMES if kind == "bow" else g.STAFF_NAMES
    ok = _confirm(ctx, lambda: ((controller.get_equipment() or {}).get("weapon") or {}).get("name") in names)
    debug_log.log("cg", f"wield {kind}: {'ok' if ok else 'not confirmed'}")
    return bool(ok)


def _click_item(ctx, item, action):
    controller.open_inventory_tab(ctx.ser)
    controller.click_entity(ctx.ser, item, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False,
                            expected_action=action)


def do_eat(ctx, snap, arg, cfg):
    fish = snap.fish()
    if not fish:
        return False
    fish.sort(key=lambda i: g.FISH_NAMES.index(i["name"]))     # corrupted first
    _click_item(ctx, fish[0], "Eat")
    return True                                                  # the next tick's hp/inventory read is the confirmation


def do_drink(ctx, snap, arg, cfg):
    pots = snap.egniol()
    if not pots:
        return False
    _click_item(ctx, pots[0], "Drink")
    return True


def do_attack(ctx, snap, arg, cfg):
    npc = snap.hunllef["entity"]
    refresh = controller.npc_index_refresh_target(snap.hunllef["index"])
    result = controller.click_entity(ctx.ser, npc, refresh_target=refresh, refresh_legs=2,
                                     expected_action="Attack", expected_target="Corrupted Hunllef")
    if getattr(result, "withheld", False) or not result:
        debug_log.log("cg", "attack click withheld/failed -- right-click menu")
        result = controller.click_menu_action(ctx.ser, npc, "Attack", target_hint="Hunllef", offset=(0, 0),
                                              refresh_target=refresh)
    return bool(result)


def _walk_to_tile(ctx, tile, plane):
    point = controller.get_ground_walk_point(tile[0], tile[1], plane, inst=True)
    if point.get("onScreen"):
        travel._click_walk_point(ctx, point, shift=True)
        return True
    point = controller.get_minimap_walk_point(tile[0], tile[1], plane, inst=True)
    if point.get("onMinimap"):
        travel._click_walk_point(ctx, point, shift=False)
        return True
    debug_log.log("cg", f"tile {tile} is neither on screen nor on the minimap")
    return False


def do_escape(ctx, snap, reason, cfg, require_in_range=False):
    imap = instance_nav.InstanceMap.load()
    if imap is None:
        debug_log.log("cg", "[!] no collision grid -- cannot pick an escape tile")
        return False
    tile = h.pick_safe_tile(snap, imap, cfg, require_in_range=require_in_range)
    if tile is None:
        debug_log.log("cg", f"[!] no safe tile ({reason})")
        return False
    debug_log.log("cg", f"{reason}: moving {snap.me} -> {tile}")
    return _walk_to_tile(ctx, tile, imap.plane)


def do_reposition(ctx, snap, arg, cfg):
    return do_escape(ctx, snap, "reposition", cfg, require_in_range=True)


ACTIONS = {"escape": do_escape, "prayer": do_prayer, "wield": do_wield, "eat": do_eat,
           "drink": do_drink, "attack": do_attack, "reposition": do_reposition}


def _confirm(ctx, check, budget_s=ACTION_BUDGET_S):
    """Poll `check` until truthy or the budget passes -- interface clicks have no pushed signal
    worth waiting on beyond the HTTP read itself."""
    deadline = time.time() + budget_s
    while True:
        if check():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


def _wait_tick(ctx):
    if ctx.events is not None:
        ctx.events.wait_ticks(1)
    else:
        time.sleep(TICK_S)


# ----------------------------------------------------------------------------- phases

def _log_rung(data, rung, arg, snap, tracker):
    key = (rung, arg)
    now = time.time()
    if key != data.get("cg_last_rung") or now - data.get("cg_last_rung_log", 0) > 10:
        data["cg_last_rung"] = key
        data["cg_last_rung_log"] = now
        hun = snap.hunllef
        debug_log.log("cg", f"rung={rung} arg={arg} hp={snap.hp} pray={snap.prayer} style={tracker.style} "
                            f"attacks={tracker.attacks} hunllef={hun['id'] if hun else None} "
                            f"hpRatio={hun['hp_ratio'] if hun else None} tornadoes={len(snap.tornadoes)} "
                            f"hazards={len(snap.hazards)}")


def fight_loop(ctx, cfg):
    """Holds tick() for the whole fight (see the module docstring).

    (C2, final-fix-brief.md) `mark` is taken ONCE, before the loop, not fresh every iteration.
    `EventStream.mark()` does NOT pump -- it only reports the current sequence number -- while
    `since()` and `wait_ticks()` both DO. A mark refreshed at the top of every iteration therefore
    reads a sequence number the PREVIOUS iteration's own `_wait_tick` call (wait_ticks(), which
    pumps) had already advanced past, so every event that arrived during that action's and tick's
    wait sat behind the new mark and `since(mark)` could never see it -- every animation event was
    silently dropped. `mark` is now updated to `ctx.events.mark()` immediately after each
    `since(mark)` call, which has just pumped, so that is exactly "the last event consumed"."""
    data = ctx.state.data
    tracker = data.get("cg_tracker")
    if tracker is None:
        tracker = data["cg_tracker"] = h.StyleTracker()
    deadline = time.time() + cfg["fight_max_s"]
    misses = 0
    no_hunllef = 0
    mark = ctx.events.mark() if ctx.events is not None else None
    while not ctx.state.safety_stopped:
        if time.time() >= deadline:
            ctx.state.trigger_safety_stop("cg: fight ran past fight_max_s")
            return
        snap = read_snapshot(cfg)
        if snap is None:
            misses += 1
            if misses >= NO_SNAPSHOT_TICKS:
                ctx.state.trigger_safety_stop("cg: bridge unreadable for 20 ticks")
                return
            _wait_tick(ctx)
            continue
        misses = 0
        if snap.hp <= 0:
            ctx.state.trigger_safety_stop("cg: died to the Hunllef (nothing lost -- read the log)")
            return
        if ctx.events is not None:
            events_since = ctx.events.since(mark)
            mark = ctx.events.mark()
        else:
            events_since = None
        feed_tracker(tracker, snap, events_since)
        rung, arg = h.ladder(snap, tracker, cfg, _unusable(ctx))
        _log_rung(data, rung, arg, snap, tracker)
        if rung == "no_hunllef":
            no_hunllef += 1
            if no_hunllef >= NO_HUNLLEF_TICKS:
                data.pop("cg_tracker", None)
                return                                   # the phase is re-derived by tick()
        else:
            no_hunllef = 0
            if rung != "idle":
                ACTIONS[rung](ctx, snap, arg, cfg)
        _wait_tick(ctx)


def enter_phase(ctx, cfg):
    snap = read_snapshot(cfg)
    if snap is None:
        return
    missing = []
    if snap.inventory_weapon("bow") is None and snap.weapon_kind() != "bow":
        missing.append("bow")
    if snap.inventory_weapon("staff") is None and snap.weapon_kind() != "staff":
        missing.append("staff")
    if len(snap.fish()) < cfg["min_fish"]:
        missing.append(f"fish ({len(snap.fish())}/{cfg['min_fish']})")
    if len(snap.egniol()) < cfg["min_egniol"]:
        missing.append("egniol")
    if missing:
        ctx.state.trigger_safety_stop("cg: loadout missing " + ", ".join(missing))
        return
    if not snap.prayers_active.get("PROTECT_FROM_MISSILES"):
        do_prayer(ctx, snap, "PROTECT_FROM_MISSILES", cfg)          # the first style is ranged
    offensive = h.offensive_prayer_for(snap.weapon_kind() or "bow", cfg, _unusable(ctx))
    if offensive and not snap.prayers_active.get(offensive):
        do_prayer(ctx, snap, offensive, cfg)
    barrier = next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True)
                    if o.get("id") == g.OBJ_BARRIER), None)
    if barrier is None:
        return
    # Check if we've exceeded the attempt limit
    attempts = ctx.state.data.get("cg_enter_attempts", 0)
    if attempts >= ENTER_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop("cg: barrier quick-pass did not start the fight after 3 attempts")
        return
    attempts += 1
    ctx.state.data["cg_enter_attempts"] = attempts
    debug_log.log("cg", "quick-passing the barrier")
    controller.click_menu_action(ctx.ser, barrier, g.BARRIER_QUICK_PASS, target_hint="Barrier", offset=(0, 0))
    _confirm(ctx, lambda: any(n.get("id") in g.HUNLLEF for n in controller.get_npcs(fresh=True)), ENTER_BUDGET_S)


def loot_phase(ctx, cfg):
    chest = next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True)
                  if o.get("id") == g.OBJ_REWARD_CHEST), None)
    if chest is None:
        return
    result = controller.click_entity(ctx.ser, chest, expected_action="Open")
    if result:
        ctx.state.data["cg_chest_opened"] = True
        controller.human_delay(2.0, 3.0)
    else:
        attempts = ctx.state.data.get("cg_loot_attempts", 0)
        attempts += 1
        ctx.state.data["cg_loot_attempts"] = attempts
        if attempts >= LOOT_MAX_ATTEMPTS:
            ctx.state.trigger_safety_stop("cg: could not open the reward chest")


def tick(ctx):
    cfg = get_config(None)
    data = ctx.state.data
    npcs = controller.get_npcs(fresh=True)
    objects = controller.get_objects(radius=cfg["objects_radius"], fresh=True)
    phase = derive_phase(data, npcs, objects)
    if phase != data.get("cg_phase"):
        debug_log.log("cg", f"phase {data.get('cg_phase')} -> {phase}")
        data["cg_phase"] = phase
        if phase == "fight":
            data["cg_enter_attempts"] = 0
    if phase == "enter":
        enter_phase(ctx, cfg)
    elif phase == "fight":
        fight_loop(ctx, cfg)
    elif phase == "loot":
        loot_phase(ctx, cfg)
    elif phase == "done":
        ctx.state.trigger_safety_stop("cg: chest opened -- run complete")
    else:
        if not data.get("cg_unknown_logged"):
            debug_log.log("cg", "not at the Hunllef barrier, in the fight, or at the chest -- start at the barrier")
            data["cg_unknown_logged"] = True
        controller.human_delay(1.0, 2.0)
