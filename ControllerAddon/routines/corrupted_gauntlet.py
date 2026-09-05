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
import gauntlet_kit as kit
import gauntlet_maze as maze
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
    "gather_deadline_s": 270,
    "gather_hard_deadline_s": 330,
    "craft_by_s": 360,
    "upgrade_min_left_s": 90,
    "enter_by_s": 420,
    "timer_s": 450,
    "room_pitch": 16,
    "fish_target": 20,
    "egniol_target": 2,
    "shard_reserve": 0,
}
NO_HUNLLEF_TICKS = 10          # consecutive fight ticks without a Hunllef before re-deriving the phase
NO_SNAPSHOT_TICKS = 20         # consecutive unreadable snapshots before a safety stop
TICK_S = 0.6
ACTION_BUDGET_S = 1.2          # confirm window for a prayer/wield/eat click (two ticks)
ENTER_BUDGET_S = 8.0
ENTER_MAX_ATTEMPTS = 3         # barrier quick-pass ceiling before safety stop
LOOT_MAX_ATTEMPTS = 5          # chest open ceiling before safety stop
LOOT_CONFIRM_S = 4.0           # (I2) budget to confirm the chest actually opened after the click
NODE_BUDGET_S = 6.0
GATHER_BUDGET_S = 8.0
NODE_MAX_FAILS = 2
DROP_SLOT_RESERVE = 2          # (C3a) slots kept free of raw fish for other loot to land in
NODE_GATHER_MAX_FAILS = 2      # (C3b) a resource node this many gather_node failures is skipped
ATTACKER_RANGE = 4
DROP_RANGE = 8
ROOM_ARRIVAL_TOLERANCE = 4
STUCK_WARN_S = 20.0            # log [!] after no progress for this many seconds
STUCK_STOP_S = 60.0            # safety-stop after no progress for this many seconds (prep phases only)


def get_config(bridge_config):
    return DEFAULT_CONFIG


# ----------------------------------------------------------------------------- prep phase

def note_entry(data, player, now):
    """cg_t0 = the first moment the corrupted region is seen; the 7:30 timer counts from it."""
    if "cg_t0" not in data and (player or {}).get("regionId") == g.REGION_CORRUPTED:
        data["cg_t0"] = now


def time_left(data, now=None, cfg=DEFAULT_CONFIG):
    t0 = data.get("cg_t0")
    if t0 is None:
        return None
    now = time.time() if now is None else now
    return cfg["timer_s"] - (now - t0)


def ensure_tools(ctx, cfg):
    """Sceptre, axe, pickaxe, harpoon, pestle -- all given at entry; a missing one is re-taken
    from the Tool Storage before leaving the start room."""
    names = {g.ITEM_SCEPTRE, g.ITEM_AXE, g.ITEM_PICKAXE, g.ITEM_HARPOON, g.ITEM_PESTLE}
    have = {i.get("name") for i in controller.get_inventory()} | {((controller.get_equipment() or {}).get("weapon") or {}).get("name")}
    if names <= have:
        return True
    missing = names - have
    storage = _find_object(cfg, g.OBJ_TOOL_STORAGE)
    storage_found = storage is not None
    storage_near = storage_found and _near(ctx, storage, 3)
    if not storage_near:
        debug_log.log("cg", f"[!] ensure_tools: missing {missing}; storage found={storage_found} near={storage_near}")
        return False
    controller.click_entity(ctx.ser, storage, expected_action="Take")
    result = _confirm(ctx, lambda: names <= {i.get("name") for i in controller.get_inventory()}, 4.0)
    if not result:
        debug_log.log("cg", f"[!] ensure_tools: missing {missing}; storage found={storage_found} near={storage_near} -- take did not confirm")
    return result


def _target_hp(npcs):
    """(I8, final-fix-brief.md) The combat target's HP ratio -- from /combat's own
    `targetHpRatio` when present, else the tracked npc's `hpRatio` looked up by id/index from the
    already-fetched npcs list. Used only to widen stuck_watch's signature so a demi-boss fight
    that IS making progress (falling target HP, same tile, same inventory) is never mistaken for
    a stall."""
    combat = controller.get_combat() or {}
    hp = combat.get("targetHpRatio")
    if hp is not None:
        return hp
    target_id = combat.get("targetId")
    if target_id is None:
        return None
    npc = next((n for n in npcs or [] if n.get("id") == target_id or n.get("index") == target_id), None)
    return (npc or {}).get("hpRatio")


def stuck_watch(ctx, signature):
    """signature = (phase, player tile, inventory size, target hp, player hp) (I8,
    final-fix-brief.md -- widened from (phase, tile, inventory size) so an upgrade-phase fight
    against a demi-boss, which can hold the same tile and inventory for many ticks while
    genuinely working, is not mistaken for a stall: a falling target hp or player hp still
    changes the signature every tick). No change for STUCK_WARN_S logs [!]; STUCK_STOP_S
    safety-stops -- except in enter/fight/loot, which have their own ceilings."""
    data = ctx.state.data
    now = time.time()
    if signature != data.get("cg_stuck_sig"):
        data["cg_stuck_sig"] = signature
        data["cg_stuck_since"] = now
        data["cg_stuck_warned"] = False
        return
    idle = now - data.get("cg_stuck_since", now)
    if idle >= STUCK_STOP_S and signature[0] in ("explore_gather", "craft", "upgrade"):
        ctx.state.trigger_safety_stop(f"cg: no progress for {int(idle)}s in {signature[0]}")
    elif idle >= STUCK_WARN_S and not data.get("cg_stuck_warned"):
        debug_log.log("cg", f"[!] cg: STUCK? {int(idle)}s without progress in {signature[0]}")
        data["cg_stuck_warned"] = True


def prep_phase(data, inventory, equipment, cfg, now=None):
    now = time.time() if now is None else now
    elapsed = now - data["cg_t0"]
    if elapsed >= cfg["enter_by_s"]:
        return "enter"
    met = kit.kit_minimum_met(inventory, equipment, cfg)
    if (not data.get("cg_route_done") and elapsed < cfg["gather_hard_deadline_s"]
            and not (met and elapsed >= cfg["gather_deadline_s"])):
        return "explore_gather"
    if not data.get("cg_crafted"):
        return "craft"
    wanted = {off: nid for off, nid in (data.get("cg_demi_bosses") or {}).items() if nid in g.DEMI_BOSS_WANTED}
    if wanted and not data.get("cg_upgrade_done") and time_left(data, now, cfg) > cfg["upgrade_min_left_s"]:
        d = kit.kit_deficit(inventory, equipment, cfg, targets=kit.UPGRADES)
        if d.orb or d.bowstring:
            return "upgrade"
    return "enter"


# ----------------------------------------------------------------------------- explore_gather phase

def _me():
    p = controller.get_player() or {}
    return (p.get("instX"), p.get("instY")), p


def prep_state(ctx, cfg):
    data = ctx.state.data
    st = data.get("cg_prep")
    if st is not None:
        return st
    imap = _cached_imap(ctx)
    me, _ = _me()
    if imap is None or me[0] is None:
        return None
    objects = controller.get_objects(radius=cfg["objects_radius"], fresh=True)
    barrier = next((o for o in objects if o.get("id") == g.OBJ_BARRIER), None)
    if barrier is None:
        return None
    comp = maze.component(imap, me)
    centre = maze.component_centre(comp)
    bpos = (barrier["instX"], barrier["instY"])
    st = {"start_centre": centre, "barrier": bpos, "outward": maze.outward_from(bpos, centre),
          "pitch": cfg["room_pitch"], "step": 0, "sub": "to_node", "revealed": set(), "entered": {(0, 0)},
          "node_fails": 0, "prev_centre": centre, "fishing_spots": set()}
    data["cg_prep"] = st
    data.setdefault("cg_demi_bosses", {})
    debug_log.log("cg", f"prep: start centre {centre}, barrier {bpos}, outward {st['outward']}")
    return st


def _centre(st, offset):
    return maze.room_centre(st["start_centre"], st["pitch"], st["outward"], offset)


def _room(st, tile):
    return maze.room_of(tile, st["start_centre"], st["pitch"], st["outward"])


def light_node(ctx, node, cfg):
    before = (node.get("id"), node.get("instX"), node.get("instY"))
    mark = ctx.events.mark() if ctx.events is not None else None
    # (I9, final-fix-brief.md) The player may still be walking toward the node when this click is
    # decided -- object_refresh_target(x, y, plane, obj_id) re-resolves the node's current screen
    # projection at each leg of the flight, the same way every other moving-target click site in
    # this codebase does (see CLAUDE.md's "A click's DEPARTURE is aimed too"). Note it compares in
    # ORDINARY world coordinates (no `inst` support), so this passes the node's own "x"/"y" fields,
    # not "instX"/"instY".
    refresh = controller.object_refresh_target(node.get("x"), node.get("y"), node.get("plane"), node.get("id"))
    controller.click_menu_action(ctx.ser, node, "Light", target_hint="Node", offset=(0, 0), refresh_target=refresh)

    def lit():
        for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True):
            if (o.get("instX"), o.get("instY")) == before[1:] and o.get("id") != before[0]:
                return True
        if ctx.events is not None:
            for t, p in ctx.events.since(mark):
                if t == "CHAT_MESSAGE" and "light the nodes" in str((p or {}).get("message", "")):
                    return True
        return False
    ok = _confirm(ctx, lit, NODE_BUDGET_S)
    if ok:
        # (I5, final-fix-brief.md) The recording shows LOADING/LOGGED_IN ~1.2s after each Light --
        # lighting a node opens up new scene, so the cached collision grid used for maze.edge_open/
        # component checks is stale the instant this succeeds.
        ctx.state.data.pop("cg_imap", None)
    debug_log.log("cg", f"light node at {before[1:]}: {'ok' if ok else 'no change'}")
    return ok


def gather_node(ctx, obj, cfg):
    name = obj.get("name")
    action = g.RESOURCE_ACTION.get(name)
    item = g.RESOURCE_ITEM.get(name)
    if action is None:
        return False
    pos = (obj.get("instX"), obj.get("instY"))
    got = 0
    for _ in range(g.RESOURCE_YIELD[name] + 2):
        live = next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True)
                     if (o.get("instX"), o.get("instY")) == pos), None)
        if live is None or live.get("id") in g.RESOURCE_NODES_DEPLETED:
            break
        me, _ = _me()
        if cg_attacker_present(me):
            return got > 0
        before = kit.count(controller.get_inventory(), item)
        controller.click_entity(ctx.ser, live, expected_action=action)
        if _confirm(ctx, lambda: kit.count(controller.get_inventory(), item) > before, GATHER_BUDGET_S):
            got += 1
        else:
            break
        _wait_tick(ctx)
    return got > 0


def cg_attacker_present(me):
    return attacker(controller.get_npcs(fresh=True), me) is not None


def attacker(npcs, me):
    best = None
    for n in npcs or []:
        if not n.get("interactingPlayer"):
            continue
        pos = (n.get("instX"), n.get("instY"))
        if pos[0] is None or h.cheb(pos, me) > ATTACKER_RANGE:
            continue
        if best is None or h.cheb(pos, me) < h.cheb((best["instX"], best["instY"]), me):
            best = n
    return best


def pick_up_drops(ctx, cfg):
    wanted = {g.ITEM_FRAME, g.ITEM_SHARDS, g.ITEM_GRYM, g.ITEM_RAW_FISH, g.ITEM_TELEPORT, g.ITEM_ORB, g.ITEM_BOWSTRING}
    me, _ = _me()
    taken = 0
    for item in controller.get_ground_items(fresh=True) or []:
        if item.get("name") not in wanted or h.cheb((item.get("instX"), item.get("instY")), me) > DROP_RANGE:
            continue
        before = len(controller.get_inventory())
        controller.click_menu_action(ctx.ser, item, "Take", target_hint=item.get("name"), offset=(0, 0))
        if _confirm(ctx, lambda: len(controller.get_inventory()) != before or kit.count(controller.get_inventory(), item.get("name")) > 0, 4.0):
            taken += 1
    return taken


def gather_ladder(ctx, cfg):
    me, _ = _me()
    npcs = controller.get_npcs(fresh=True)
    foe = attacker(npcs, me)
    if foe is None:
        return "none"
    if foe.get("id") in g.DEMI_BOSSES or foe.get("id") in g.DEMI_BOSS_DROP:
        prev = (ctx.state.data.get("cg_prep") or {}).get("prev_centre")
        if prev:
            debug_log.log("cg", f"[!] demi-boss {foe.get('name')} attacking -- leaving toward {prev}")
            instance_nav.walk_to(ctx, prev[0], prev[1])
        return "flee_demi"
    return fight_npc_step(ctx, foe, cfg)


def fight_npc_step(ctx, npc, cfg):
    """One tick of fighting an ordinary npc or demi-boss: prayer for its style, best weapon, eat,
    attack. Returns the rung taken."""
    style = g.ATTACK_STYLE_BY_NPC_ID.get(npc.get("id"), "MELEE")
    prayer = g.PROTECT_FOR_STYLE[style]
    player = controller.get_player() or {}
    if not (controller.get_prayers().get("active") or {}).get(prayer):
        do_prayer(ctx, None, prayer, cfg); return "prayer"
    inv = controller.get_inventory(); eq = controller.get_equipment()
    best = max((("bow", kit.owned_tier("bow", inv, eq)), ("staff", kit.owned_tier("staff", inv, eq))), key=lambda t: t[1])
    if best[1] > 0 and kit.owned_tier(best[0], [], eq) < best[1]:
        wear(ctx, g.weapon_name(best[0], best[1])); return "wield"
    if int(player.get("hp") or 0) <= cfg["eat_at"]:
        fish = [i for i in inv if i.get("name") in (g.ITEM_FISH, g.ITEM_CFISH)]
        if fish:
            _click_item(ctx, fish[0], "Eat"); return "eat"
    if (controller.get_combat() or {}).get("targetId") != npc.get("id"):
        controller.click_entity(ctx.ser, npc, refresh_target=controller.npc_index_refresh_target(npc.get("index")),
                                refresh_legs=2, expected_action="Attack")
    return "attack"


def upgrade_phase(ctx, cfg):
    data = ctx.state.data
    st = data.get("cg_prep")
    up = data.setdefault("cg_upgrade", {"sub": "go"})
    left = time_left(data, cfg=cfg)
    if st is None or left is None or (left < cfg["upgrade_min_left_s"] - 30 and up["sub"] in ("go", "fight")):
        debug_log.log("cg", "[!] upgrade abandoned -- out of time")
        teleport_home(ctx, cfg); data["cg_upgrade_done"] = True; return
    wanted = [(off, nid) for off, nid in data.get("cg_demi_bosses", {}).items() if nid in g.DEMI_BOSS_WANTED]
    if not wanted:
        data["cg_upgrade_done"] = True; return
    offset, nid = wanted[0]
    room = _centre(st, offset)
    below = _centre(st, (offset[0], offset[1] - 1))
    me, _ = _me()
    imap = _cached_imap(ctx)
    if up["sub"] == "go":
        if h.cheb(me, below) > ROOM_ARRIVAL_TOLERANCE and h.cheb(me, room) > ROOM_ARRIVAL_TOLERANCE:
            instance_nav.walk_to(ctx, below[0], below[1], tolerance=ROOM_ARRIVAL_TOLERANCE); return
        if not maze.edge_open(imap, below, room):
            node = maze.node_between(_nodes(controller.get_objects(radius=cfg["objects_radius"], fresh=True)), below, room, st["pitch"])
            if node is None or not light_node(ctx, node, cfg):
                data["cg_upgrade_done"] = True; return
        if h.cheb(me, room) > ROOM_ARRIVAL_TOLERANCE:
            instance_nav.walk_to(ctx, room[0], room[1], tolerance=ROOM_ARRIVAL_TOLERANCE); return
        up["sub"] = "fight"; return
    if up["sub"] == "fight":
        npc = next((n for n in controller.get_npcs(fresh=True) if n.get("id") == nid), None)
        if npc is None:
            up["sub"] = "loot"; return
        fight_npc_step(ctx, npc, cfg); return
    if up["sub"] == "loot":
        # (I7, final-fix-brief.md) The wiki: the primary component (orb/bowstring) drops only if
        # not already owned, otherwise ANY un-owned component drops instead -- the recording's
        # second dragon dropped a bowstring, not the orb DEMI_BOSS_DROP[nid] would have assumed.
        # Confirm a component actually landed (up to 3 pick_up_drops attempts) rather than moving
        # on after exactly one, and log when none did so a missed drop is visible in the log.
        before = kit.count(controller.get_inventory(), g.ITEM_ORB) + kit.count(controller.get_inventory(), g.ITEM_BOWSTRING)
        pick_up_drops(ctx, cfg)
        after = kit.count(controller.get_inventory(), g.ITEM_ORB) + kit.count(controller.get_inventory(), g.ITEM_BOWSTRING)
        if after > before:
            up["sub"] = "home"; return
        attempts = up.get("loot_attempts", 0) + 1
        up["loot_attempts"] = attempts
        if attempts >= 3:
            debug_log.log("cg", "[!] upgrade loot: no orb/bowstring landed after 3 pick-up attempts")
            up["sub"] = "home"
        return
    if up["sub"] == "home":
        if teleport_home(ctx, cfg):
            up["sub"] = "craft"; return
        return
    if up["sub"] == "craft":
        # (I7) Craft whichever perfected weapon the inventory actually allows -- an orb crafts the
        # staff, a bowstring crafts the bow, and BOTH craft if both components are on hand (a
        # single demi-boss trip can in principle yield either, per the wiki rule above).
        inv = controller.get_inventory()
        crafted_any = False
        if kit.count(inv, g.ITEM_ORB):
            crafted_any = bowl_make(ctx, "staff", cfg) or crafted_any
        if kit.count(inv, g.ITEM_BOWSTRING):
            crafted_any = bowl_make(ctx, "bow", cfg) or crafted_any
        if crafted_any:
            eq = controller.get_equipment()
            inv = controller.get_inventory()
            best = max(("bow", "staff"), key=lambda k: kit.owned_tier(k, inv, eq))
            tier = kit.owned_tier(best, inv, eq)
            if tier:
                wear(ctx, g.weapon_name(best, tier))
            data["cg_upgrade_done"] = True
        else:
            up.setdefault("craft_fails", 0)
            up["craft_fails"] += 1
            if up["craft_fails"] >= 3:
                debug_log.log("cg", f"[!] bowl_make failed {up['craft_fails']} times -- giving up")
                data["cg_upgrade_done"] = True


def _nodes(objects):
    return [o for o in objects if o.get("id") in g.NODE_IDS]


def _record_fishing_spots(st, objects):
    """(C3c, final-fix-brief.md) Every Corrupt Fishing Spot seen while exploring is remembered
    by instance tile, so craft_phase's post-craft fishing pass has somewhere to walk back to
    without re-scanning the whole maze."""
    spots = st.setdefault("fishing_spots", set())
    for o in objects:
        if o.get("id") in g.RESOURCE_NODES and g.RESOURCE_ITEM.get(o.get("name")) == g.ITEM_RAW_FISH:
            spots.add((o.get("instX"), o.get("instY")))


def explore_gather_phase(ctx, cfg):
    data = ctx.state.data
    st = prep_state(ctx, cfg)
    if st is None:
        debug_log.log("cg", "prep: cannot read the grid/barrier yet")
        return
    steps = maze.route()
    if st["step"] >= len(steps):
        data["cg_route_done"] = True
        return
    if gather_ladder(ctx, cfg) != "none":
        return
    offset, _, reveal = steps[st["step"]]
    target = _centre(st, offset)
    me, _ = _me()
    imap = _cached_imap(ctx)
    objects = controller.get_objects(radius=cfg["objects_radius"], fresh=True)
    _record_fishing_spots(st, objects)
    here_centre = _centre(st, _room(st, me))

    def advance():
        st["step"] += 1
        st["sub"] = "to_node"
        st["node_fails"] = 0
        st["prev_centre"] = here_centre

    if st["sub"] == "to_node":
        node = maze.node_between(_nodes(objects), here_centre, target, st["pitch"])
        if maze.edge_open(imap, here_centre, target):
            st["sub"] = "to_room"
            # Fall through to to_room logic
        elif node is None:
            # (I5, final-fix-brief.md) The static map can call an edge closed while a stale
            # cached grid disagrees with reality (see the imap-staleness fix above) with no
            # lighting node found either -- if we are ALREADY within arrival tolerance of the
            # target, there is nothing left to walk toward, so fall through to to_room instead of
            # silently wedging here forever re-issuing the same walk.
            if h.cheb(me, target) <= ROOM_ARRIVAL_TOLERANCE:
                st["sub"] = "to_room"
                # Fall through to to_room logic
            else:
                instance_nav.walk_to(ctx, target[0], target[1], tolerance=ROOM_ARRIVAL_TOLERANCE)
                return
        elif h.cheb(me, (node["instX"], node["instY"])) > 2:
            instance_nav.walk_to(ctx, node["instX"], node["instY"], tolerance=2)
            return
        elif light_node(ctx, node, cfg):
            st["sub"] = "to_room"
            return
        else:
            st["node_fails"] += 1
            if st["node_fails"] >= NODE_MAX_FAILS:
                debug_log.log("cg", f"[!] node toward {offset} will not light -- skipping the step")
                advance()
            return

    if st["sub"] == "to_room":
        if h.cheb(me, target) > ROOM_ARRIVAL_TOLERANCE:
            instance_nav.walk_to(ctx, target[0], target[1], tolerance=ROOM_ARRIVAL_TOLERANCE)
            return
        st["entered"].add(offset)
        st["sub"] = "gather"

    if st["sub"] == "gather":
        pick_up_drops(ctx, cfg)
        inventory = controller.get_inventory()
        deficit = kit.kit_deficit(inventory, controller.get_equipment(), cfg)
        # (C3a, final-fix-brief.md) Raw fish are NOT stackable -- capping fish deficit by the
        # kit target alone can plan to fish past a full inventory. Cap by free slots (minus a
        # reserve so pick_up_drops/other loot still has somewhere to land) rather than the fish
        # target; the other wanted items are all stackable and need no such cap.
        free_slots = max(0, 28 - len(inventory) - DROP_SLOT_RESERVE)
        want = {g.ITEM_ORE: deficit.ore, g.ITEM_BARK: deficit.bark, g.ITEM_LINUM: deficit.linum,
                g.ITEM_GRYM: deficit.grym, g.ITEM_RAW_FISH: min(deficit.fish, free_slots)}
        # (C3b) A node whose gather_node has already failed NODE_GATHER_MAX_FAILS times this run
        # is excluded from the candidate list -- without this, a node that can never be gathered
        # (blocked line of sight, permanently contested) is re-picked and re-failed every call.
        node_fails = data.setdefault("cg_node_fails", {})
        nodes = [o for o in objects if o.get("id") in g.RESOURCE_NODES and _room(st, (o["instX"], o["instY"])) == offset
                 and want.get(g.RESOURCE_ITEM.get(o.get("name")), 0) > 0
                 and node_fails.get((o["instX"], o["instY"]), 0) < NODE_GATHER_MAX_FAILS]
        if nodes:
            nodes.sort(key=lambda o: h.cheb((o["instX"], o["instY"]), me))
            picked = nodes[0]
            if not gather_node(ctx, picked, cfg):
                tile = (picked["instX"], picked["instY"])
                node_fails[tile] = node_fails.get(tile, 0) + 1
            return
        if reveal and reveal not in st["revealed"]:
            st["sub"] = "reveal"
            # Fall through to reveal logic
        else:
            advance()
            return

    if st["sub"] == "reveal":
        rc = _centre(st, reveal)
        node = maze.node_between(_nodes(objects), target, rc, st["pitch"])
        if node is None:
            debug_log.log("cg", f"[!] no node toward the reveal room {reveal}")
            st["revealed"].add(reveal); advance(); return
        if h.cheb(me, (node["instX"], node["instY"])) > 2:
            instance_nav.walk_to(ctx, node["instX"], node["instY"], tolerance=2)
            return
        light_node(ctx, node, cfg)
        for n in controller.get_npcs(fresh=True):
            if n.get("id") in g.DEMI_BOSS_DROP and _room(st, (n.get("instX"), n.get("instY"))) == reveal:
                data["cg_demi_bosses"][reveal] = n["id"]
                debug_log.log("cg", f"revealed {n.get('name')} ({n['id']}) in {reveal}")
        st["revealed"].add(reveal)
        advance()


# ----------------------------------------------------------------------------- reads

SNAPSHOT_LOG_INTERVAL_S = 10.0   # (I4) how often read_snapshot logs its own wall-clock cost


def read_snapshot(cfg=DEFAULT_CONFIG, ctx=None):
    """Same return contract as before (a FightSnapshot, or None when the bridge can't say where
    the player is): `ctx` is optional and purely additive, added (I4, final-fix-brief.md) so this
    can log its own wall-clock duration into ctx.state.data for `_log_rung` to report and for a
    live session to see whether the per-tick budget is bridge-bound."""
    t0 = time.time()
    player = controller.get_player()
    if not player or player.get("instX") is None:
        snap = None
    else:
        snap = h.FightSnapshot.from_bridge(
            player=player,
            npcs=controller.get_npcs(fresh=True),
            objects=controller.get_objects(radius=cfg["objects_radius"], fresh=True),
            prayers=controller.get_prayers(),
            equipment=controller.get_equipment(),
            inventory=controller.get_inventory(),
            combat=controller.get_combat())
    if ctx is not None:
        now = time.time()
        ms = (now - t0) * 1000.0
        data = ctx.state.data
        data["cg_last_snapshot_ms"] = ms
        if now - data.get("cg_snapshot_log_at", 0) >= SNAPSHOT_LOG_INTERVAL_S:
            data["cg_snapshot_log_at"] = now
            debug_log.log("cg", f"snapshot {ms:.0f} ms")
    return snap


def derive_phase(data, npcs, objects, inventory=None, equipment=None, cfg=None, now=None):
    """Phase from live state, never from a remembered default (the mahogany_homes lesson).

    (M, final-fix-brief.md) VARBIT_FIGHTING_HUNLLEF (varbit 9177, "1 while in the boss room") is a
    second, faster "we are in the fight" signal alongside the Hunllef's presence in /npcs --
    on_event mirrors it into data["cg_varbit_fight"] as soon as it changes, which can beat the
    next bulk /npcs scan by a tick or two."""
    ids = {o.get("id") for o in objects or []}
    if any(n.get("id") in g.HUNLLEF for n in npcs or []) or data.get("cg_varbit_fight") == 1:
        data["cg_fight_seen"] = True
        return "fight"
    if g.OBJ_BARRIER in ids and not data.get("cg_fight_seen"):
        # In the prep phase (before the fight is seen), return the prep phase if possible
        if data.get("cg_t0") is not None and inventory is not None:
            if cfg is None:
                cfg = DEFAULT_CONFIG
            if now is None:
                now = time.time()
            return prep_phase(data, inventory, equipment, cfg, now)
        return "enter"
    if g.OBJ_REWARD_CHEST in ids and data.get("cg_fight_seen"):
        return "done" if data.get("cg_chest_opened") else "loot"
    # Outside the arena (no barrier visible) but in prep phase
    if data.get("cg_t0") is not None and inventory is not None and not data.get("cg_fight_seen"):
        if cfg is None:
            cfg = DEFAULT_CONFIG
        if now is None:
            now = time.time()
        return prep_phase(data, inventory, equipment, cfg, now)
    return "unknown"


def on_event(ctx, event_type, payload):
    """Mirrors VARBIT_FIGHTING_HUNLLEF for derive_phase (see its own comment). Nothing else is
    recorded here -- VARBIT_SUBSCRIPTIONS already asks the engine to post this varbit on load and
    after every login, so the very first read arrives without waiting for it to actually change."""
    if event_type == "VARBIT_CHANGED" and payload and payload.get("varbitId") == g.VARBIT_FIGHTING_HUNLLEF:
        ctx.state.data["cg_varbit_fight"] = payload.get("value")


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
    # (M, final-fix-brief.md) "Hunllef" substring-matches both "Corrupted Hunllef" and
    # "Crystalline Hunllef" -- the old literal "Corrupted Hunllef" withheld every click against
    # the crystalline (regular Gauntlet) variant.
    result = controller.click_entity(ctx.ser, npc, refresh_target=refresh, refresh_legs=2,
                                     expected_action="Attack", expected_target="Hunllef")
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


def _cached_imap(ctx):
    """(I4, final-fix-brief.md) The instance's collision grid is static for the whole fight (only
    hazards -- read separately, per snapshot -- move), so load it once per fight and reuse it
    instead of paying a fresh /collision?all=true HTTP round-trip on every escape/reposition
    click. tick() drops ctx.state.data["cg_imap"] the moment the phase leaves "fight"."""
    imap = ctx.state.data.get("cg_imap")
    if imap is not None:
        return imap
    imap = instance_nav.InstanceMap.load()
    if imap is not None:
        ctx.state.data["cg_imap"] = imap
    return imap


def do_escape(ctx, snap, reason, cfg, require_in_range=False):
    """(I5, final-fix-brief.md) Sticks to the previously-picked escape target
    (ctx.state.data["cg_escape_target"]) instead of re-aiming every tick, as long as it is still
    safe under hunllef.tile_is_safe -- without this, two ticks with the same danger could pick two
    different, equally-scored tiles (pick_safe_tile's key ties are broken by dict iteration
    order, which is stable per snapshot but not guaranteed stable across snapshots a tornado or
    hazard tick apart) and the routine would flick the cursor between them instead of committing
    to one walk. Cleared once the player reaches it (Chebyshev <= 1) or it stops being safe."""
    imap = _cached_imap(ctx)
    if imap is None:
        debug_log.log("cg", "[!] no collision grid -- cannot pick an escape tile")
        return False
    prev = ctx.state.data.get("cg_escape_target")
    if prev is not None and (h.cheb(snap.me, prev) <= 1 or not h.tile_is_safe(snap, prev, cfg)):
        prev = None
        ctx.state.data.pop("cg_escape_target", None)
    if prev is not None:
        tile = prev
    else:
        tile = h.pick_safe_tile(snap, imap, cfg, require_in_range=require_in_range)
        if tile is not None:
            ctx.state.data["cg_escape_target"] = tile
    if tile is None:
        debug_log.log("cg", f"[!] no safe tile ({reason})")
        return False
    debug_log.log("cg", f"{reason}: moving {snap.me} -> {tile}")
    return _walk_to_tile(ctx, tile, imap.plane)


REPOSITION_FALLBACK_CHEB = 5   # (M) how close to walk toward an out-of-range Hunllef


def _tile_toward_centre(centre, me, distance):
    """The tile at Chebyshev `distance` from `centre`, on the straight line toward `me`."""
    dx, dy = me[0] - centre[0], me[1] - centre[1]
    if dx == 0 and dy == 0:
        return (centre[0] + distance, centre[1])
    scale = distance / max(abs(dx), abs(dy))
    return (round(centre[0] + dx * scale), round(centre[1] + dy * scale))


def do_reposition(ctx, snap, arg, cfg):
    """(M, final-fix-brief.md) When pick_safe_tile(require_in_range=True) finds nothing reachable
    at all -- almost always because the player is too FAR from the Hunllef, not too close --
    do_escape's own "no safe tile" log line would otherwise repeat every tick with no progress.
    Fall back to walking straight toward the Hunllef (instance_nav.walk_to, one call, no wait) to
    a tile just outside its footprint, rather than sitting there logging forever."""
    if do_escape(ctx, snap, "reposition", cfg, require_in_range=True):
        return True
    centre = snap.hunllef_centre()
    if centre is None:
        return False
    target = _tile_toward_centre(centre, snap.me, REPOSITION_FALLBACK_CHEB)
    debug_log.log("cg", f"reposition: no in-range escape tile -- walking toward the Hunllef {snap.me} -> {target}")
    return instance_nav.walk_to(ctx, target[0], target[1])


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


# ----------------------------------------------------------------------------- craft phase

BOWL_BUDGET_S = 4.0
HOME_BUDGET_S = 8.0
COOK_STALL_S = 5.0
POTION_MAX_STEPS = 6


def _find_object(cfg, obj_id):
    return next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True) if o.get("id") == obj_id), None)


def _near(ctx, obj, tiles):
    me, _ = _me()
    pos = (obj.get("instX"), obj.get("instY"))
    if h.cheb(me, pos) > tiles:
        instance_nav.walk_to(ctx, pos[0], pos[1], tolerance=tiles, goal_adjacent=True)
        return False
    return True


def teleport_home(ctx, cfg):
    st = ctx.state.data.get("cg_prep") or {}
    crystal = next((i for i in controller.get_inventory() if i.get("name") == g.ITEM_TELEPORT), None)
    if crystal is not None:
        _click_item(ctx, crystal, "Activate")
        if _confirm(ctx, lambda: _find_object(cfg, g.OBJ_SINGING_BOWL) is not None, HOME_BUDGET_S):
            # (I5) A teleport crosses the scene the same way a node light does.
            ctx.state.data.pop("cg_imap", None)
            return True
    if st.get("start_centre"):
        ok = instance_nav.walk_to(ctx, st["start_centre"][0], st["start_centre"][1], tolerance=3)
        if ok:
            ctx.state.data.pop("cg_imap", None)
        return ok
    return False


def _bowl_widget(key):
    """(I6, final-fix-brief.md) Match against name+text, the same group-270 matcher
    routines/giants_foundry.py's crucible Fill interface uses (`_fill_via_crucible`'s
    `label = f"{w.get('name') or ''} {w.get('text') or ''}"`) -- a widget carrying the item's
    name in the `name` field with an empty `text` (or vice versa) still matches."""
    want = g.BOWL_TEXT[key]
    for w in controller.get_widgets_dump():
        if w.get("group") != g.BOWL_GROUP or w.get("screenX") is None:
            continue
        label = f"{w.get('name') or ''} {w.get('text') or ''}"
        if want in label:
            return w
    return None


def bowl_make(ctx, key, cfg):
    bowl = _find_object(cfg, g.OBJ_SINGING_BOWL)
    if bowl is None or not _near(ctx, bowl, 3):
        return False
    # (I6) The Singing Bowl interface stays open across Makes -- skip the Sing-crystal click
    # (and the walk/near check it re-triggers) when the widget for this key is already visible.
    if _bowl_widget(key) is None:
        controller.click_entity(ctx.ser, bowl, expected_action="Sing-crystal")
        if not _confirm(ctx, lambda: _bowl_widget(key) is not None, BOWL_BUDGET_S):
            debug_log.log("cg", f"bowl: no widget for {key}")
            return False
    widget = _bowl_widget(key)
    before = [(i.get("name"), i.get("qty")) for i in controller.get_inventory()]
    controller.click_entity(ctx.ser, widget, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    ok = _confirm(ctx, lambda: [(i.get("name"), i.get("qty")) for i in controller.get_inventory()] != before, BOWL_BUDGET_S)
    debug_log.log("cg", f"bowl {key}: {'ok' if ok else 'no inventory change'}")
    return ok


def use_on(ctx, name_a, name_b, confirm=None):
    """(C2, final-fix-brief.md) `confirm`, when given, is a zero-arg predicate used instead of the
    default "count(name_a) fell" rule -- needed for `_pestle`, whose A item (the Pestle and
    mortar) is a TOOL that is never consumed, so the default rule can never confirm even when the
    click genuinely worked."""
    inv = controller.get_inventory()
    a = next((i for i in inv if i.get("name") == name_a), None)
    b = next((i for i in inv if i.get("name") == name_b), None)
    if a is None or b is None:
        return False
    controller.open_inventory_tab(ctx.ser)
    controller.click_entity(ctx.ser, a, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False, expected_action="Use")
    controller.click_entity(ctx.ser, b, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    check = confirm if confirm is not None else (lambda: kit.count(controller.get_inventory(), name_a) < kit.count(inv, name_a))
    return _confirm(ctx, check, 3.0)


def cook_all(ctx, cfg):
    rng = _find_object(cfg, g.OBJ_RANGE)
    if rng is None or not _near(ctx, rng, 3):
        return False
    controller.click_entity(ctx.ser, rng, expected_action="Cook")
    last = kit.count(controller.get_inventory(), g.ITEM_RAW_FISH)
    stall = time.time() + COOK_STALL_S
    while time.time() < stall:
        _wait_tick(ctx)
        raw = kit.count(controller.get_inventory(), g.ITEM_RAW_FISH)
        if raw == 0:
            return True
        if raw < last:
            last, stall = raw, time.time() + COOK_STALL_S
    return last == 0


def pump_fill(ctx, cfg):
    pump = _find_object(cfg, g.OBJ_WATER_PUMP)
    if pump is None or not _near(ctx, pump, 3):
        return False
    vials = kit.count(controller.get_inventory(), g.ITEM_VIAL)
    controller.click_entity(ctx.ser, pump, expected_action="Fill-from")
    return _confirm(ctx, lambda: kit.count(controller.get_inventory(), g.ITEM_WATER_VIAL) >= vials, 6.0)


def wear(ctx, name):
    item = next((i for i in controller.get_inventory() if i.get("name") == name), None)
    if item is None:
        return False
    _click_item(ctx, item, ("Wear", "Wield"))
    return _confirm(ctx, lambda: any((v or {}).get("name") == name for v in (controller.get_equipment() or {}).values() if isinstance(v, dict)), 3.0)


def _pestle(ctx):
    """(C2, final-fix-brief.md) The default use_on rule watches name_a (the Pestle and mortar)
    falling, which never happens -- it's a tool, not a consumable. Confirm on the Corrupted dust
    rising, or the shards falling by DUST_SHARDS as a fallback in case dust itself isn't visible
    on the same poll (a stacked item can lag one HTTP read behind a qty bump elsewhere)."""
    before_dust = kit.count(controller.get_inventory(), g.ITEM_DUST)
    before_shards = kit.count(controller.get_inventory(), g.ITEM_SHARDS)

    def confirm():
        inv = controller.get_inventory()
        return (kit.count(inv, g.ITEM_DUST) > before_dust
                or kit.count(inv, g.ITEM_SHARDS) <= before_shards - kit.DUST_SHARDS)

    return use_on(ctx, g.ITEM_PESTLE, g.ITEM_SHARDS, confirm=confirm)


def _potion(ctx):
    before_egniol = sum(kit.count(controller.get_inventory(), dose) for dose in g.EGNIOL_DOSES)
    steps = 0
    failed = False
    # First loop: Grym leaf + water vial
    while steps < POTION_MAX_STEPS and kit.count(controller.get_inventory(), g.ITEM_WATER_VIAL) and kit.count(controller.get_inventory(), g.ITEM_GRYM):
        if not use_on(ctx, g.ITEM_GRYM, g.ITEM_WATER_VIAL):
            failed = True
            break  # Stop immediately if use_on fails
        steps += 1
    # Second loop: Egniol dose + dust -- (C2) confirm on the Grym potion (unf) falling, not the
    # default rule's name_a (dust) falling, since dust is the item being spent in bulk (10 at a
    # time) and the unf item is the one this step is actually meant to consume one of.
    while steps < POTION_MAX_STEPS and kit.count(controller.get_inventory(), g.ITEM_GRYM_UNF) and kit.count(controller.get_inventory(), g.ITEM_DUST) >= 10:
        before_unf = kit.count(controller.get_inventory(), g.ITEM_GRYM_UNF)
        if not use_on(ctx, g.ITEM_DUST, g.ITEM_GRYM_UNF,
                      confirm=lambda bu=before_unf: kit.count(controller.get_inventory(), g.ITEM_GRYM_UNF) < bu):
            failed = True
            break  # Stop immediately if use_on fails
        steps += 1
    after_egniol = sum(kit.count(controller.get_inventory(), dose) for dose in g.EGNIOL_DOSES)
    # Return True only if doses increased OR (no failure AND no pairs left in either stage)
    no_stage1_pairs = not (kit.count(controller.get_inventory(), g.ITEM_WATER_VIAL) and kit.count(controller.get_inventory(), g.ITEM_GRYM))
    no_stage2_pairs = not (kit.count(controller.get_inventory(), g.ITEM_GRYM_UNF) and kit.count(controller.get_inventory(), g.ITEM_DUST) >= 10)
    return after_egniol > before_egniol or (not failed and no_stage1_pairs and no_stage2_pairs)


FISH_PASS_MIN_LEFT_S = 120      # (C3c) don't spend the digression once the timer is tight
FISH_PASS_MAX_S = 60            # wall-clock budget for the whole pass, win or lose


def _fish_pass_due(ctx, cfg, inventory):
    """(C3c, final-fix-brief.md) A one-off trip back to a recorded fishing spot when the kit's
    fish target isn't met even after cooking everything on hand -- the fight only needs
    min_fish (10), so this exists to close the gap toward the higher kit target (20) rather than
    to guarantee it. Gated on cook having already run (no raw fish left uncooked) so it never
    races the normal craft plan's own cook step."""
    data = ctx.state.data
    if data.get("cg_fish_pass_done"):
        return False
    if kit.count(inventory, g.ITEM_RAW_FISH) != 0:
        return False
    fish_total = kit.count(inventory, g.ITEM_FISH) + kit.count(inventory, g.ITEM_CFISH)
    if fish_total >= cfg["fish_target"]:
        return False
    left = time_left(data, cfg=cfg)
    if left is None or left <= FISH_PASS_MIN_LEFT_S:
        return False
    st = data.get("cg_prep") or {}
    return bool(st.get("fishing_spots"))


def _fish_pass_step(ctx, cfg):
    """One tick of the post-craft fishing pass: walk to the nearest recorded spot, gather it,
    then let craft_phase fall back onto its normal plan (which will queue a cook step once raw
    fish are on hand again)."""
    data = ctx.state.data
    if time.time() >= data.get("cg_fish_pass_deadline", 0):
        debug_log.log("cg", "[!] fish pass: out of time -- moving on")
        data["cg_craft_sub"] = None
        data["cg_fish_pass_done"] = True
        return
    st = data.get("cg_prep") or {}
    spots = st.get("fishing_spots") or set()
    if not spots:
        data["cg_craft_sub"] = None
        data["cg_fish_pass_done"] = True
        return
    me, _ = _me()
    spot = min(spots, key=lambda p: h.cheb(p, me))
    if h.cheb(me, spot) > 1:
        instance_nav.walk_to(ctx, spot[0], spot[1], tolerance=1, goal_adjacent=True)
        return
    obj = next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True)
                if (o.get("instX"), o.get("instY")) == spot and o.get("id") in g.RESOURCE_NODES), None)
    if obj is not None:
        gather_node(ctx, obj, cfg)
    inventory = controller.get_inventory()
    fish_total = kit.count(inventory, g.ITEM_FISH) + kit.count(inventory, g.ITEM_CFISH)
    if fish_total >= cfg["fish_target"] or kit.count(inventory, g.ITEM_RAW_FISH) > 0:
        # Either the target is met, or there is raw fish to go cook -- either way this pass is
        # done for now; craft_phase's normal plan takes it from here.
        data["cg_craft_sub"] = None
        data["cg_fish_pass_done"] = True


def craft_phase(ctx, cfg):
    data = ctx.state.data
    if not data.get("cg_home"):
        if teleport_home(ctx, cfg):
            data["cg_home"] = True
        return
    if data.get("cg_craft_sub") == "fish":
        _fish_pass_step(ctx, cfg)
        return
    inventory = controller.get_inventory()
    if _fish_pass_due(ctx, cfg, inventory):
        data["cg_craft_sub"] = "fish"
        data["cg_fish_pass_deadline"] = time.time() + FISH_PASS_MAX_S
        _fish_pass_step(ctx, cfg)
        return
    plan = kit.craft_plan(controller.get_inventory(), controller.get_equipment(), cfg)
    if not plan:
        data["cg_crafted"] = True
        return
    op, arg = plan[0]
    ok = {"bowl": lambda: bowl_make(ctx, arg, cfg), "pump": lambda: pump_fill(ctx, cfg),
          "pestle": lambda: _pestle(ctx), "potion": lambda: _potion(ctx),
          "cook": lambda: cook_all(ctx, cfg), "wear": lambda: wear(ctx, arg)}[op]()
    if "cg_craft_fails" not in data:
        data["cg_craft_fails"] = {}
    fails = data["cg_craft_fails"]
    if ok:
        fails[op] = 0
    else:
        fails[op] = fails.get(op, 0) + 1
    if any(count >= 3 for count in fails.values()):
        debug_log.log("cg", f"[!] craft step {op} {arg} failed 3 times -- skipping the rest")
        data["cg_crafted"] = True


# ----------------------------------------------------------------------------- phases

def _log_rung(data, rung, arg, snap, tracker):
    key = (rung, arg)
    now = time.time()
    if key != data.get("cg_last_rung") or now - data.get("cg_last_rung_log", 0) > 10:
        data["cg_last_rung"] = key
        data["cg_last_rung_log"] = now
        hun = snap.hunllef
        snapshot_ms = data.get("cg_last_snapshot_ms")
        snapshot_str = f"{snapshot_ms:.0f}" if snapshot_ms is not None else "?"
        debug_log.log("cg", f"rung={rung} arg={arg} hp={snap.hp} pray={snap.prayer} style={tracker.style} "
                            f"attacks={tracker.attacks} hunllef={hun['id'] if hun else None} "
                            f"hpRatio={hun['hp_ratio'] if hun else None} tornadoes={len(snap.tornadoes)} "
                            f"hazards={len(snap.hazards)} snapshot_ms={snapshot_str}")


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
        # (M, final-fix-brief.md) A fresh tracker always starts RANGED/0 -- correct only when the
        # round is genuinely starting; a mid-fight restart (a relogin, a crash) resumes here with
        # no idea what the Hunllef's real style is, and is wrong roughly half the time until the
        # first marker resyncs it. Worth calling out in the log, not silently assumed correct.
        debug_log.log("cg", f"tracker starts {tracker.style}/{tracker.attacks} -- wrong half the "
                            "time on a mid-fight restart until the first marker")
    deadline = time.time() + cfg["fight_max_s"]
    misses = 0
    no_hunllef = 0
    mark = ctx.events.mark() if ctx.events is not None else None
    while not ctx.state.safety_stopped:
        if time.time() >= deadline:
            ctx.state.trigger_safety_stop("cg: fight ran past fight_max_s")
            return
        snap = read_snapshot(cfg, ctx)
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
            # (I3, final-fix-brief.md) UDP can carry death before the next HTTP poll notices
            # hp<=0 above -- same stop, same message, just a faster signal for it.
            if any(event_type == "PLAYER_DIED" for event_type, _ in events_since):
                ctx.state.trigger_safety_stop("cg: died to the Hunllef (nothing lost -- read the log)")
                return
        else:
            events_since = None
        feed_tracker(tracker, snap, events_since)
        # (I3) A Hunllef id that maps to "DEAD" is a corpse -- skip the ladder entirely for this
        # tick so nothing (attack included) clicks on it while it's down.
        if snap.hunllef is not None and g.HUNLLEF_PRAYER_BY_ID.get(snap.hunllef["id"]) == "DEAD":
            _log_rung(data, "kill_pending", None, snap, tracker)
            no_hunllef = 0
            _wait_tick(ctx)
            continue
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
    """(I6, final-fix-brief.md) A break must not interrupt a barrier quick-pass mid-flight --
    ctx.state.break_due() (added after this routine; guarded via getattr so an older engine build
    that lacks it behaves exactly as before) refuses the whole phase, not just the click, so
    nothing (loadout check included) happens on a tick where a break is imminent."""
    break_due = getattr(ctx.state, "break_due", None)
    if break_due is not None and break_due():
        debug_log.log("cg", "cg: break due -- holding at the barrier without quick-passing")
        return
    snap = read_snapshot(cfg, ctx)
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


def _inventory_delta(before, after):
    """True on any change: a different item count, or the same items with a different qty (a
    stacked reward like coins can change qty with the slot count unchanged)."""
    def _sig(items):
        return sorted((i.get("name"), i.get("qty")) for i in items)
    return _sig(before) != _sig(after)


def loot_phase(ctx, cfg):
    """(I2, final-fix-brief.md) A click that merely REGISTERED is not proof the reward chest
    opened -- `click_entity` truthy only means the menu action resolved, same as any other click.
    Confirm against the inventory (a delta versus the pre-click snapshot) or, when ctx.events
    exists, a "You open the chest" chat message seen since the mark taken before the click, within
    LOOT_CONFIRM_S. Only a confirmed open sets cg_chest_opened; anything else -- a falsy click or
    a truthy one that never confirms -- counts toward the existing attempt ceiling."""
    chest = next((o for o in controller.get_objects(radius=cfg["objects_radius"], fresh=True)
                  if o.get("id") == g.OBJ_REWARD_CHEST), None)
    if chest is None:
        return
    before = controller.get_inventory()
    mark = ctx.events.mark() if ctx.events is not None else None
    result = controller.click_entity(ctx.ser, chest, expected_action="Open")
    opened = False
    if result:
        def check():
            if _inventory_delta(before, controller.get_inventory()):
                return True
            if ctx.events is not None:
                for event_type, payload in ctx.events.since(mark):
                    if event_type == "CHAT_MESSAGE" and payload \
                            and "You open the chest" in str(payload.get("message", "")):
                        return True
            return False
        opened = _confirm(ctx, check, budget_s=LOOT_CONFIRM_S)
    if opened:
        ctx.state.data["cg_chest_opened"] = True
        controller.human_delay(2.0, 3.0)
    else:
        attempts = ctx.state.data.get("cg_loot_attempts", 0)
        attempts += 1
        ctx.state.data["cg_loot_attempts"] = attempts
        if attempts >= LOOT_MAX_ATTEMPTS:
            ctx.state.trigger_safety_stop("cg: could not open the reward chest")


BREAK_DEFER_MARGIN_S = 60.0     # (C4) extra wall-clock slack past the run's own worst-case length


def before_break(ctx):
    """The GOTR shape (C4, final-fix-brief.md): a break must not hand the scheduler a mid-run
    instance. If the routine is currently in the fight, play it out to completion -- fight_loop is
    naturally bounded by cfg["fight_max_s"], so this cannot hang forever even if the fight never
    ends on its own. For every prep phase (explore_gather/craft/upgrade), teleporting home used to
    be the whole story -- but that abandons the run's own state (crafted gear, revealed demi-bosses,
    the maze route) mid-instance, exactly the transitional-snapshot family of bug GOTR's own
    before_break was rewritten to avoid. Instead, keep calling tick(ctx) -- the same dispatcher the
    engine loop itself uses -- until the phase reaches "done", a safety stop fires, or
    cg_t0 + timer_s + fight_max_s + BREAK_DEFER_MARGIN_S has elapsed (the run's own worst-case
    length plus slack), whichever comes first. This naturally hands off to fight_loop's own
    ceiling once tick() derives the fight phase, and to loot_phase's own attempt ceiling once the
    chest phase is reached, so the loop can never actually hang."""
    data = ctx.state.data
    phase = data.get("cg_phase")
    if phase == "fight":
        fight_loop(ctx, get_config(None))
        return
    if data.get("cg_t0") is None or phase == "done":
        return
    cfg = get_config(None)
    deadline = data["cg_t0"] + cfg["timer_s"] + cfg["fight_max_s"] + BREAK_DEFER_MARGIN_S
    while data.get("cg_phase") != "done" and not ctx.state.safety_stopped and time.time() < deadline:
        tick(ctx)


def tick(ctx):
    cfg = get_config(None)
    data = ctx.state.data
    player = controller.get_player()
    note_entry(data, player, time.time())
    npcs = controller.get_npcs(fresh=True)
    objects = controller.get_objects(radius=cfg["objects_radius"], fresh=True)
    inventory = controller.get_inventory()
    equipment = controller.get_equipment()
    phase = derive_phase(data, npcs, objects, inventory=inventory, equipment=equipment, cfg=cfg, now=time.time())
    if phase != data.get("cg_phase"):
        debug_log.log("cg", f"phase {data.get('cg_phase')} -> {phase}")
        # (I5, final-fix-brief.md) Widened from "only when leaving fight" (I4) -- EVERY phase
        # change can follow a scene reload (a node light, a teleport, entering/leaving the fight),
        # so the cached collision grid is stale on any of them, not just the fight boundary.
        data.pop("cg_imap", None)
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
    elif phase in ("explore_gather", "craft", "upgrade"):
        # Ensure tools once per run when starting prep phases
        if not data.get("cg_tools_ok"):
            if not ensure_tools(ctx, cfg):
                # Tool check failed, but still watch for stalls (might recover next tick)
                me, _ = _me()
                stuck_watch(ctx, (phase, me, len(inventory), _target_hp(npcs), (player or {}).get("hp")))
                return
            data["cg_tools_ok"] = True
        # Dispatch the appropriate prep phase
        if phase == "explore_gather":
            explore_gather_phase(ctx, cfg)
        elif phase == "craft":
            craft_phase(ctx, cfg)
        elif phase == "upgrade":
            upgrade_phase(ctx, cfg)
        # Watch for stalls
        me, _ = _me()
        stuck_watch(ctx, (phase, me, len(inventory), _target_hp(npcs), (player or {}).get("hp")))
    else:
        if not data.get("cg_unknown_logged"):
            debug_log.log("cg", "not at the Hunllef barrier, in the fight, or at the chest -- start at the barrier")
            data["cg_unknown_logged"] = True
        controller.human_delay(1.0, 2.0)
