# routines/slayer.py -- the Slayer TASK routine: get a task, deal with it, get the next one.
#
# Sub-project 2 of docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md (2026-09-19):
# ACQUIRE through NPC Contact (Kuradal on every `milestoneEvery`-th task, Aya otherwise), SKIP an
# unwanted task for 30 points, STOP on a task the user does by hand, and FIGHT when already
# standing at a known site. Sub-project 3 phase A (2026-09-19) adds TRAVEL: a fairy-ring or
# carried-item route from where the player stands, for Aya tasks whose teleport list this phase
# knows how to use. The house, the bank and Kuradal's sites are still to come -- until they exist,
# a route TRAVEL cannot plan is a safety stop naming why.
#
# The phase is DERIVED from GET /slayer every tick (spec §7.1); ctx.state.data holds only
# counters, so a restart mid-task resumes correctly. None from /slayer = do nothing.
#
# The old "fight the monsters picked in the panel" behaviour is routines/combat.py.
# UNVERIFIED LIVE as of 2026-09-19.

import time

import aya_tasks
import bank_memory
import banker
import combat_core
import controller
import debug_log
import gear
import legs
import npc_contact
import slayer_catalog
import slayer_loadout
import slayer_policy
import slayer_routes
import slayer_state
import slayer_tasks

LOG = "slayer"

# The routine owns every dialogue it opens: the engine's once-per-tick Space must never land in a
# slayer master's dialogue. During FIGHT it calls check_stray_dialog itself (level-up boxes).
SKIP_STRAY_DIALOG_CHECK = True
VARP_SUBSCRIPTIONS = (394, 395)
VARBIT_SUBSCRIPTIONS = (4067, 4068, 4069)

ACQUIRE, SKIP, STOP, FIGHT, TRAVEL = "ACQUIRE", "SKIP", "STOP", "FIGHT", "TRAVEL"
GO_THERE = TRAVEL              # the pre-phase-A name; kept so old notes and logs still read

CONTACT_MAX_CASTS = 3          # each cast spends runes: a ceiling, not just a cooldown
CONTACT_RETRY_COOLDOWN_S = 6.0

SITE_MARGIN = 6                # tiles around the plugin's tight kill rectangles that still count as "there"
TRAVEL_MAX_FAILS = 3           # a leg that fails three times strands us: stop and NAME it
TRAVEL_MAX_PROGRESS = 40       # legs.run_next's PROGRESS is unbounded by design; the caller bounds it

RESTOCK_MAX_PER_TASK = 4       # plan D1: a task that needs a fifth restock is losing; stop and say so
BANK_MAX_FAILS = 3             # banker.bank_up returning False that many times for one task: stop

NO_TARGET_TICKS = 30           # ~18s with nothing to fight before walking back to the site tile
NO_TARGET_WALKS = 3            # that many walks back with still nothing found: stop, don't idle forever


def get_config(bridge_config):
    return controller.get_slayer_config()


def on_event(ctx, event_type, payload):
    combat_core.on_event(ctx, event_type, payload)


def derive_phase(state, tasks_cfg, player) -> str:
    if not state.has_task:
        return ACQUIRE
    if state.task is None:
        return STOP                      # the name did not decode: never cancel or fight a guess
    verdict = slayer_tasks.disposition(state.task, tasks_cfg)
    if verdict == slayer_tasks.SKIP:
        return SKIP
    if verdict == slayer_tasks.STOP:
        return STOP
    return FIGHT if slayer_tasks.at_site(state.task, player, margin=SITE_MARGIN) else TRAVEL


def _reset_slot_counters(ctx, state) -> None:
    """Casts and skips are counted per task SLOT. The streak moving, or a task appearing, is a
    new slot."""
    seen = ctx.state.data.get("slayer_streak_seen")
    if seen != state.streak:
        ctx.state.data.update({"slayer_streak_seen": state.streak, "slayer_casts": 0, "slayer_skips": 0,
                               "slayer_cancel_attempts": 0, "slayer_route": None, "slayer_route_for": None,
                               "slayer_leg": 0, "slayer_leg_fails": 0, "slayer_leg_progress": 0,
                               "slayer_route_finished": 0, "slayer_no_target_walks": 0,
                               "slayer_no_target": 0, "slayer_pack_ok": None,
                               "slayer_banked_for": None, "slayer_bank_fails": 0,
                               "slayer_restocks": 0, "slayer_restocking": None,
                               "slayer_worn_ok": None, "slayer_wear_fails": 0})


def _acquire(ctx, state, tasks_cfg) -> None:
    master = slayer_state.next_master(state.streak, tasks_cfg)
    # Cooldown FIRST: missing_requirements is five bridge reads, not something to repeat every tick.
    if time.time() - ctx.state.data.get("slayer_contact_at", 0.0) < CONTACT_RETRY_COOLDOWN_S:
        return
    problem = npc_contact.missing_requirements(master)
    if problem:
        ctx.state.trigger_safety_stop(f"Slayer: cannot contact {master} -- {problem}")
        return
    casts = ctx.state.data.get("slayer_casts", 0)
    if casts >= CONTACT_MAX_CASTS:
        ctx.state.trigger_safety_stop(
            f"Slayer: cast NPC Contact {casts} times for task {state.streak + 1} and no task ever "
            f"registered with {master} -- see the [npc_contact] lines above")
        return
    ctx.state.data["slayer_contact_at"] = time.time()
    ctx.state.data["slayer_casts"] = casts + 1
    ctx.state.data.pop("npc_contact_unknown_menu", None)
    debug_log.log(LOG, f"no task: asking {master} for task {state.streak + 1} (streak {state.streak}, "
                       f"{state.points} points)")
    try:
        got = npc_contact.get_task(ctx, master, tasks_cfg)
    except slayer_state.StreakGuardError as exc:
        ctx.state.trigger_safety_stop(f"Slayer: {exc}")
        return
    unknown = ctx.state.data.get("npc_contact_unknown_menu")
    if unknown:
        ctx.state.trigger_safety_stop(
            f"Slayer: {master}'s dialogue showed a menu this routine does not know, and it was "
            f"escaped without choosing: [{unknown}]. Tell the developer these options")
        return
    if got is not None:
        ctx.state.data["slayer_casts"] = 0
        debug_log.log(LOG, f"task: {got.task or got.task_id} x{got.count} from {master} "
                           f"(streak {got.streak}, {got.points} points)")


def _skip(ctx, state, tasks_cfg) -> None:
    ok, why = slayer_state.can_skip(state.points, ctx.state.data.get("slayer_skips", 0), tasks_cfg)
    if not ok:
        ctx.state.trigger_safety_stop(f"Slayer: {state.task} is on the skip list but {why}")
        return
    master = (tasks_cfg or {}).get("milestoneMaster") or slayer_state.MILESTONE_DEFAULT
    if time.time() - ctx.state.data.get("slayer_contact_at", 0.0) < CONTACT_RETRY_COOLDOWN_S:
        return
    problem = npc_contact.missing_requirements(master)
    if problem:
        ctx.state.trigger_safety_stop(f"Slayer: cannot cancel {state.task} -- {problem}")
        return
    ctx.state.data["slayer_contact_at"] = time.time()
    attempts = ctx.state.data.get("slayer_cancel_attempts", 0) + 1
    ctx.state.data["slayer_cancel_attempts"] = attempts
    if attempts > CONTACT_MAX_CASTS:
        ctx.state.trigger_safety_stop(f"Slayer: {attempts - 1} attempts to cancel {state.task} through "
                                      f"{master}'s rewards did not clear the task")
        return
    debug_log.log(LOG, f"skipping {state.task} through {master}'s rewards ({state.points} points)")
    try:
        if npc_contact.cancel_task(ctx, master, tasks_cfg):
            ctx.state.data["slayer_cancel_attempts"] = 0
    except slayer_state.StreakGuardError as exc:
        ctx.state.trigger_safety_stop(f"Slayer: {exc}")


def _bank_config(panel, loadout, base=None) -> dict:
    """banker.bank_up's config for one slayer bank visit: the panel's own bank block (bankNames,
    keepItems, teleportFallback) with the loadout's withdraw and keep lists laid over it. Never
    returnToSpot: TRAVEL re-derives the way back from wherever the bank was.

    `base` is the saved base gear ({slot: name}). Every base item is KEPT: the defender a mirror
    shield displaced rides in the inventory for the whole task, and banker deposits everything
    that is not on this list."""
    cfg = dict((panel or {}).get("bank") or {})
    keep = list(cfg.get("keepItems") or []) + list(loadout.keep) + [n.lower() for n in (base or {}).values() if n]
    cfg.update({"enabled": True, "withdraw": loadout.withdraw, "returnToSpot": False,
                "keepItems": list(dict.fromkeys(keep)),
                "teleportFallback": cfg.get("teleportFallback", True)})
    return cfg


def _base_gear(ctx):
    """The saved base gear ({slot: name}) for this account, or None. Read once per process."""
    data = ctx.state.data
    if "slayer_base_gear" not in data:
        path = slayer_policy.base_gear_path(bank_memory.load().account_hash)
        data["slayer_base_gear_path"] = path
        data["slayer_base_gear"] = slayer_policy.load_base_gear(path)
    return data["slayer_base_gear"]


def _capture_base_gear(ctx, state) -> None:
    """Save what is worn as the BASE, once per process and only while no gear task is active
    (slayer_policy.may_capture_base): mid-basilisks the mirror shield is on, and it is not the
    base gear. An empty /equipment read is never saved (the bridge answers {} when it is down)."""
    data = ctx.state.data
    if data.get("slayer_base_captured"):
        return
    if not slayer_policy.may_capture_base(state.has_task, slayer_catalog.get(state.task)):
        return
    _base_gear(ctx)                                        # resolves the path
    if slayer_policy.save_base_gear(data.get("slayer_base_gear_path"), controller.get_equipment()):
        data["slayer_base_gear"] = slayer_policy.load_base_gear(data["slayer_base_gear_path"])
        debug_log.log(LOG, f"base gear saved: {data['slayer_base_gear']}")
    data["slayer_base_captured"] = True


def _restore_base_gear(ctx) -> bool:
    """With NO task held: put back any base item a task's gear displaced, one equip per tick. True
    when this tick was spent on it. An item that is not in the inventory (banked, or the base was
    saved in odd gear) is logged once and left -- never a stop, never a bank trip of its own."""
    base = _base_gear(ctx)
    if not base:                               # nothing saved yet: no reads, nothing to put back
        return False
    todo = slayer_policy.gear_to_restore(base, controller.get_equipment())
    if not todo:
        return False
    carried = gear.carried_names()
    for slot, name in todo.items():
        if any(name.lower() in held for held in carried):
            debug_log.log(LOG, f"task done: putting the {name} back on ({slot})")
            gear.equip_from_inventory(ctx, name)
            return True
    missing = sorted(todo.values())
    if missing and ctx.state.data.get("slayer_base_missing_logged") != missing:
        ctx.state.data["slayer_base_missing_logged"] = missing
        debug_log.log(LOG, f"base gear not in the inventory, left as it is: {missing}")
    return False


def _wear(ctx, state) -> bool:
    """WEAR what the task requires (plan task C3). True when this tick was spent on it -- one equip,
    or a safety stop. UNVERIFIED LIVE; the click is gear.equip_from_inventory, the pattern
    combat_core.maintain_bracelet proves live.

    slayer_tasks.missing_items accepts a requirement that is carried OR worn, and a mirror shield
    in the inventory does nothing against a basilisk. So before the journey and before every fight
    tick, a catalog task's checkable `worn` requirements are put ON.

    Equipping a shield knocks a TWO-HANDED weapon off. Which one-handed weapon to bring beside the
    mirror shield is an open gear decision (plan E0); until it is made the routine STOPS rather
    than walk into basilisks bare-handed."""
    data = ctx.state.data
    key = (state.task, state.streak)
    if data.get("slayer_worn_ok") == key:
        return False
    spec = slayer_catalog.get(state.task)
    for label in (spec.worn if spec else ()):
        options = slayer_catalog.REQUIREMENT_ITEMS.get(label, ())
        if not options or gear.is_worn(options[0], options[1:]):
            continue
        carried = gear.carried_names()
        pick = next((o for o in options if any(o in held for held in carried)), None)
        if pick is None:
            ctx.state.trigger_safety_stop(f"Slayer: {state.task} needs {label} WORN and none is carried")
            return True
        had_weapon = "weapon" in (controller.get_equipment() or {})
        debug_log.log(LOG, f"{state.task}: putting on the {pick} ({label})")
        if not gear.equip_from_inventory(ctx, pick):
            fails = data.get("slayer_wear_fails", 0) + 1
            data["slayer_wear_fails"] = fails
            if fails >= TRAVEL_MAX_FAILS:
                ctx.state.trigger_safety_stop(f"Slayer: could not put on the {pick} for {state.task} "
                                              f"after {fails} tries -- see the [gear] lines above")
            return True
        data["slayer_wear_fails"] = 0
        if had_weapon and "weapon" not in (controller.get_equipment() or {}):
            ctx.state.trigger_safety_stop(
                f"Slayer: putting on the {pick} for {state.task} took the weapon OFF (a two-handed "
                f"weapon). Choose a one-handed weapon for this task and wear it -- stopping rather "
                f"than fight bare-handed")
        return True
    data["slayer_worn_ok"] = key
    return False


def _restock(ctx, state) -> bool:
    """RESTOCK (plan task D1). True when this tick was spent on it. UNVERIFIED LIVE.

    Out of food or prayer AT the site (combat_core.out_of_critical_supplies -- written after a
    live session fought on with nothing left): drop the target, bank to the SAME pack the task was
    packed to (the ring of dueling to Ferox is the escape, banker's own fallback), and let TRAVEL
    bring us back. 'Merely full of loot' is NOT a restock.

    `slayer_restocking` holds the task's key from the moment a restock starts until bank_up
    succeeds. bank_up can teleport and THEN fail; the player is then off-site, the phase reads
    TRAVEL, and without the flag the routine would walk back to the fight with nothing to eat.
    RESTOCK_MAX_PER_TASK restocks is the ceiling: past it, stop and name the task."""
    data = ctx.state.data
    key = (state.task, state.streak)
    if data.get("slayer_restocking") != key:
        if not combat_core.out_of_critical_supplies(ctx):
            return False
        done = data.get("slayer_restocks", 0)
        if done >= RESTOCK_MAX_PER_TASK:
            ctx.state.trigger_safety_stop(f"Slayer: {state.task} x{state.count} has needed more than "
                                          f"{RESTOCK_MAX_PER_TASK} restocks -- stopping rather than grind it out")
            return True
    panel = ctx.state.config or {}
    banked = slayer_loadout.banked_from_memory(bank_memory.load())
    prayer = ((controller.get_skills() or {}).get("Prayer") or {}).get("level") or 1
    loadout = slayer_loadout.build(state.task, state.count, panel, prayer, 0, _carried(), banked)
    if loadout.problems:
        ctx.state.trigger_safety_stop(f"Slayer: {state.task} x{state.count} ran out of supplies and the "
                                      f"bank cannot restock it -- " + "; ".join(loadout.problems))
        return True
    if data.get("slayer_bank_fails", 0) >= BANK_MAX_FAILS:
        ctx.state.trigger_safety_stop(f"Slayer: the restock trip for {state.task} failed {BANK_MAX_FAILS} "
                                      f"times -- see the [bank] lines above")
        return True
    if data.get("slayer_restocking") != key:
        debug_log.log(LOG, f"out of supplies on {state.task} x{state.count}: restock "
                           f"{data.get('slayer_restocks', 0) + 1} of {RESTOCK_MAX_PER_TASK}")
    data["slayer_restocking"] = key
    data["current_target_index"] = None
    if banker.bank_up(ctx, _bank_config(panel, loadout, _base_gear(ctx))):
        data.update({"slayer_restocking": None, "slayer_restocks": data.get("slayer_restocks", 0) + 1,
                     "slayer_bank_fails": 0, "slayer_route": None, "slayer_route_for": None,
                     "slayer_leg": 0, "slayer_route_finished": 0})
    else:
        data["slayer_bank_fails"] = data.get("slayer_bank_fails", 0) + 1
    return True


def _pack(ctx, state, tasks_cfg, player) -> bool:
    """The BANK step before TRAVEL (plan task C2). True when this tick was spent on it -- a bank
    trip or a safety stop -- and TRAVEL must not also run. UNVERIFIED LIVE as of 2026-09-21.

    The DECISION is slayer_loadout.build + slayer_policy.bank_verdict (pure, tested); the ACTING is
    banker.bank_up, live-proven in other routines -- a nearby bank, else Ferox by ring of dueling,
    deposit per keepItems, withdraw the list. Rules, each about NOT doing something:
    - never while a journey is under way, and never when already a short walk from the site: two
      sharks eaten must not pull the routine off a task. Running out AT the site is RESTOCK (D1).
    - bank ONCE per task. Still short afterwards is a STOP naming the items, never a loop.
    - a missing item is ALWAYS a stop. The plan allows a 30-point SKIP there; bank memory can be
      stale, and points are not spent on a guess (ruling 2026-09-21).
    - the panel's own bank settings and keepItems ride along, so a rune pouch is not deposited."""
    data = ctx.state.data
    key = (state.task, state.streak)
    if data.get("slayer_route") or data.get("slayer_pack_ok") == key:
        return False
    if slayer_routes.near_site(state.task, player, skill_levels=legs.skill_levels()):
        data["slayer_pack_ok"] = key
        return False
    panel = ctx.state.config or {}
    carried = _carried()
    banked = slayer_loadout.banked_from_memory(bank_memory.load())
    prayer = ((controller.get_skills() or {}).get("Prayer") or {}).get("level") or 1
    loadout = slayer_loadout.build(state.task, state.count, panel, prayer, 0, carried, banked)
    inventory = controller.get_inventory()
    free = slayer_loadout.supplies.INVENTORY_SLOTS - len(inventory) if isinstance(inventory, list) else None
    tablets = None if banked is None else sum(
        int(q or 0) for source in (carried, banked) for n, q in source.items()
        if not n.startswith("#") and slayer_loadout.HOUSE_TABLET in n)
    verdict, why = slayer_policy.bank_verdict(loadout, free, tasks_cfg, tablets, skippable=False,
                                              visited_bank=data.get("slayer_banked_for") == key)
    if verdict == slayer_policy.GO:
        data["slayer_pack_ok"] = key
        return False
    if verdict != slayer_policy.BANK:
        ctx.state.trigger_safety_stop(f"Slayer: {state.task} x{state.count} -- {why}")
        return True
    if data.get("slayer_bank_fails", 0) >= BANK_MAX_FAILS:
        ctx.state.trigger_safety_stop(f"Slayer: the bank trip for {state.task} failed {BANK_MAX_FAILS} "
                                      f"times ({why}) -- see the [bank] lines above")
        return True
    debug_log.log(LOG, f"banking for {state.task} x{state.count}: {why}")
    if banker.bank_up(ctx, _bank_config(panel, loadout, _base_gear(ctx))):
        data["slayer_banked_for"] = key
        data["slayer_bank_fails"] = 0
    else:
        data["slayer_bank_fails"] = data.get("slayer_bank_fails", 0) + 1
    return True


def _carried() -> dict:
    """Lowercased item NAME -> quantity AND "#<item id>" -> quantity, inventory plus worn. The ids
    matter: a lit and an unlit bullseye lantern share one name (phase-A review), so only the id
    can prove a light source. /equipment is a dict keyed by slot."""
    counts = {}

    def add(item):
        quantity = int(item.get("qty") or item.get("quantity") or 1)
        name = (item.get("name") or "").lower()
        if name:
            counts[name] = counts.get(name, 0) + quantity
        if item.get("id") is not None:
            key = f"#{item['id']}"
            counts[key] = counts.get(key, 0) + quantity

    for item in controller.get_inventory() or ():
        add(item)
    worn = controller.get_equipment() or {}
    for item in (worn.values() if isinstance(worn, dict) else worn):
        if isinstance(item, dict):
            add(item)
    # A CARRIED rune pouch's runes count as carried (a teleport spell needs them). Only when
    # carried: its contents are varbits and are reported wherever the pouch is (found live).
    pouch = controller.get_rune_pouch() or {}
    if pouch.get("carried"):
        for rune in pouch.get("runes") or ():
            add({"name": rune.get("name"), "qty": rune.get("quantity")})
    return counts


def _stranded(ctx, state, why) -> None:
    where = slayer_tasks.where(state.task)
    ctx.state.trigger_safety_stop(
        f"Slayer: task is {state.task} x{state.count} and I cannot get there -- {why}."
        + (f" The site is {where}." if where else ""))


def _travel(ctx, state, player) -> None:
    data = ctx.state.data
    carried = _carried()
    missing = slayer_tasks.missing_items(state.task, state.count, carried)
    if missing:
        ctx.state.trigger_safety_stop(f"Slayer: {state.task} needs {', '.join(missing)} -- not carried")
        return
    key = (state.task, state.streak)
    if data.get("slayer_route_for") != key or not data.get("slayer_route"):
        # `proven` is what opens gated transports (the whirlpool's varbit, a dusty key): plan()
        # refuses a teleport whose onward walk this account cannot make, so it must be told.
        route, why = slayer_routes.plan(state.task, player, carried, skill_levels=legs.skill_levels(),
                                        proven=legs.requirement_values())
        if route is None:
            _stranded(ctx, state, why)
            return
        data.update({"slayer_route": route, "slayer_route_for": key, "slayer_leg": 0,
                     "slayer_leg_fails": 0, "slayer_leg_progress": 0})
        debug_log.log(LOG, f"route to {state.task}: " + " -> ".join(leg["display"] for leg in route))
    route, cursor = data["slayer_route"], data.get("slayer_leg", 0)
    status, cursor = legs.run_next(ctx, route, cursor, log=LOG)
    name = route[min(cursor, len(route) - 1)]["display"]
    if status == legs.DONE:
        # slayer_route_finished is NOT reset here (phase-A review): a fresh route always starts
        # with a DONE, so resetting on DONE made the "finished twice" stop unreachable and the
        # re-plan loop unbounded. Only REACHING the site (the FIGHT branch) or a new slot clears it.
        data.update({"slayer_leg": cursor, "slayer_leg_fails": 0, "slayer_leg_progress": 0})
    elif status == legs.PROGRESS:
        data["slayer_leg_progress"] = data.get("slayer_leg_progress", 0) + 1
        if data["slayer_leg_progress"] > TRAVEL_MAX_PROGRESS:
            _stranded(ctx, state, f"leg '{name}' made no landing in {TRAVEL_MAX_PROGRESS} ticks")
    elif status == legs.FAILED:
        data["slayer_leg_fails"] = data.get("slayer_leg_fails", 0) + 1
        if data["slayer_leg_fails"] >= TRAVEL_MAX_FAILS:
            _stranded(ctx, state, f"leg '{name}' failed {TRAVEL_MAX_FAILS} times")
    else:                                   # FINISHED, and derive_phase still says we are not there
        first_finish = data.get("slayer_route_finished", 0) == 0
        data["slayer_route_finished"] = data.get("slayer_route_finished", 0) + 1
        if first_finish:
            data["slayer_route"] = None    # drop it so the NEXT tick re-plans from here
        if data["slayer_route_finished"] >= 2:
            # already gave up: leave the exhausted route in place so a tick that still runs after
            # the (terminal) safety stop cannot spend another plan() on it.
            _stranded(ctx, state, "the route finished twice but the site is not reached")


def _fight(ctx, state) -> None:
    controller.check_stray_dialog(ctx)
    panel = ctx.state.config
    cfg = combat_core.combat_config(panel, slayer_tasks.target_names(state.task))
    # The orchestrator owns restocks (spec 7.5). Left on, fight_tick teleports to a bank by itself,
    # and the next tick sees the player off-site and safety-stops there (final review).
    cfg["bank"] = {**(cfg.get("bank") or {}), "enabled": False}
    # Aya's monsters drop nothing worth a click and travel dominates a 20-kill task (spec §7.10):
    # only the panel's named whitelist ("items") is looted. maybe_loot reads "minValue" and treats
    # 0 as "no value looting" (phase-A review: the first draft wrote a key nobody reads).
    # ONLY for a task that is not in Kuradal's catalog (self-review 2026-09-21: this was applied to
    # every task, and gargoyles and nechryael drop plenty) -- hers keep the panel's value.
    if slayer_catalog.get(state.task) is None:
        cfg["loot"] = {**(cfg.get("loot") or {}), "minValue": 0}
    ctx.state.config = cfg
    try:
        combat_core.fight_tick(ctx)
    finally:
        ctx.state.config = panel
    data = ctx.state.data
    data["slayer_route_finished"] = 0          # we are AT the site: the journey is over
    if data.get("current_target_index") is not None:
        data["slayer_no_target"] = 0
        return
    data["slayer_no_target"] = data.get("slayer_no_target", 0) + 1
    if data["slayer_no_target"] < NO_TARGET_TICKS:
        return
    data["slayer_no_target"] = 0
    walks = data.get("slayer_no_target_walks", 0) + 1
    data["slayer_no_target_walks"] = walks
    # The SAME site record at_site and plan() read: Aya's registry, then Kuradal's catalog (self-
    # review 2026-09-21: this read aya_tasks only and stopped every Kuradal task as 'not in the
    # catalog'). The walk goes to the WALK GOAL -- the kill area when the target lies outside it.
    site = aya_tasks.get(state.task) or slayer_catalog.get(state.task)
    if site is None or not site.target:
        ctx.state.trigger_safety_stop(f"Slayer: nothing to fight for {state.task} and its site is "
                                      f"not in the catalog -- cannot walk back to it")
        return
    goal = slayer_routes.walk_goal(site)
    debug_log.log(LOG, f"no target for {NO_TARGET_TICKS} ticks -- walking back to the site tile {goal}")
    legs.walk_across(ctx, goal, log=LOG)
    if walks >= NO_TARGET_WALKS:
        ctx.state.trigger_safety_stop(f"Slayer: nothing to fight for {state.task} after "
                                      f"{NO_TARGET_WALKS} walks back to the site")


def tick(ctx):
    state = slayer_state.from_bridge(controller.get_slayer_task())
    if state is None:
        debug_log.log(LOG, "GET /slayer unavailable -- doing nothing")
        return
    tasks_cfg = (ctx.state.config or {}).get("tasks") or {}
    _reset_slot_counters(ctx, state)
    player = controller.get_player()
    phase = derive_phase(state, tasks_cfg, player)
    _capture_base_gear(ctx, state)
    if phase == ACQUIRE:
        if not _restore_base_gear(ctx):        # a finished task's gear comes off before the next ask
            _acquire(ctx, state, tasks_cfg)
    elif phase == SKIP:
        _skip(ctx, state, tasks_cfg)
    elif phase == STOP:
        label = state.task or f"task id {state.task_id} (name did not decode)"
        ctx.state.trigger_safety_stop(f"Slayer: {label} x{state.count} is a task this routine leaves to you")
    elif ctx.state.data.get("slayer_restocking") == (state.task, state.streak):
        _restock(ctx, state)                 # a restock under way outranks TRAVEL and FIGHT alike
    elif phase == TRAVEL:
        if not _pack(ctx, state, tasks_cfg, player) and not _wear(ctx, state):
            _travel(ctx, state, player)
    elif not _restock(ctx, state) and not _wear(ctx, state):
        _fight(ctx, state)
