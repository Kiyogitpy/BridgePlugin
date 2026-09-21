# routines/slayer.py -- the Slayer TASK routine: get a task, deal with it, get the next one.
#
# docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md. ACQUIRE a task through NPC Contact
# (Kuradal on every `milestoneEvery`-th task, Aya otherwise), SKIP an unwanted one for 30 points,
# STOP on a task the user does by hand, PACK it at the bank, WEAR what it requires, TRAVEL to the
# site, FIGHT, RESTOCK when the food runs out, and take the next task.
#
# THE PHASE IS DERIVED FROM GET /slayer EVERY TICK (spec §7.1). ctx.state.data holds only counters
# and cursors, so a restart mid-task resumes correctly -- pinned by tests/test_slayer_end_to_end.py,
# which drives whole tasks (and restarts, and a skip) through one simulated world. None from
# /slayer means do nothing.
#
# The ORDER inside a tick, and why each step can refuse the next:
#
#   no task          -> _restore_base_gear (put back what a gear task displaced) THEN _acquire
#   skip-list task   -> _skip
#   a STOP task      -> a safety stop naming it
#   restock running  -> _restock, outranking everything: bank_up can teleport and THEN fail, and
#                       the player is then off-site with nothing to eat
#   not at the site  -> _pack (bank for the loadout) -> _wear (put the gear ON) -> _travel
#   at the site      -> _restock (only if supplies ran out) -> _wear -> _fight
#
# Each step returns True when it SPENT the tick, so the next one does not also run. Every failure
# is a safety stop that NAMES what went wrong -- never a loop, never a guess.
#
# What is decided where: the pure modules own every judgement (slayer_tasks disposition,
# slayer_catalog the sites and requirements, slayer_routes the route, slayer_loadout the pack,
# slayer_policy the bank/loot/gear verdicts); this file only sequences them and calls the
# primitives that click (npc_contact, banker, legs, gear, combat_core). Before a live run,
# `python slayer_preflight.py [--task X | --aya | --all]` prints what all of that WOULD decide
# without touching the mouse.
#
# The old "fight the monsters picked in the panel" behaviour is routines/combat.py.
# ACQUIRE from Aya is LIVE-PROVEN (2026-09-19, one cast). Everything else here is UNVERIFIED LIVE.

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
import slayer_bib
import slayer_catalog
import slayer_loadout
import slayer_policy
import slayer_routes
import slayer_state
import slayer_tasks
import travel

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
#: Seconds to let a fight break off before spending a cast. CONFIRMED LIVE 2026-09-21: a task
#: finishing mid-fight goes straight to ACQUIRE, and three casts fired ~1s after a combat click
#: with cave bugs still on the player opened NEITHER the picker nor a dialogue -- the runes were
#: spent every time (-5 astral/-5 cosmic/-10 dust across the session, exactly five casts), so the
#: spell went off and the interface was closed under it by the pending interaction. Three of those
#: is the CONTACT_MAX_CASTS stop, which is how a finished task turned into a dead run. The same
#: cast succeeded on the next launch, once the bugs had lost interest. This was already item 5 on
#: the handoff's open checklist as a predicted risk; it is measured now.
ACQUIRE_SETTLE_S = 8.0

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
    _settle_before_contact(ctx)
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


def _settle_before_contact(ctx) -> None:
    """Let the fight break off before the cast -- see ACQUIRE_SETTLE_S for the live failure.

    Bounded, and it CASTS ANYWAY when the budget runs out: a monster that will not disengage must
    cost one retry, never a routine that waits for ever. The target is dropped first so the
    routine stops feeding the fight it is waiting to end."""
    ctx.state.data["current_target_index"] = None
    deadline = time.time() + ACQUIRE_SETTLE_S
    told = False
    while time.time() < deadline:
        combat = controller.get_combat() or {}
        if not combat.get("inCombat") and not combat.get("underAttack") and combat.get("isIdle", True):
            return
        if not told:
            debug_log.log(LOG, "still in combat -- letting it break off before casting NPC Contact")
            told = True
        time.sleep(0.3)
    debug_log.log(LOG, f"still in combat after {ACQUIRE_SETTLE_S:.0f}s -- casting anyway")


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


AYA_WEAPON = "dragon scimitar"


def _held_names() -> set:
    """Everything worn or carried, lowercased. Read at the call site so `_bank_config` stays a
    pure function of its arguments -- its tests depend on that."""
    return gear.worn_names() | gear.carried_names()


def _gear_worth_keeping() -> list:
    """Task gear already on us, which a deposit must not take (USER 2026-09-21: "keep any gear
    from previous task").

    Reads the live bridge, so it is called at the BANK-TRIP call sites and its result passed into
    `_bank_config` as `keep_extra` -- `_bank_config` itself stays pure, which its tests rely on
    and which a first draft of this broke.

    Two sources, both about NOT re-fetching something we already hold:
    - everything WORN. banker deposits whatever is not on keepItems, and worn gear is by
      definition gear we chose; depositing it would strip the player at the bank.
    - anything CARRIED that satisfies some task's gear requirement, anywhere in the catalog --
      not just this task's. That is the whole point: a mirror shield fetched for basilisks, heat
      boots for wyrms, a witchwood icon for cave horrors should ride along to the next task
      rather than be banked and withdrawn again a task later.

    Names are lowercased fragments, matching keepItems' own substring convention."""
    keep = list(gear.worn_names())
    carried = gear.carried_names()
    for options in slayer_catalog.REQUIREMENT_ITEMS.values():
        for option in options:
            if any(option in held for held in carried):
                keep.append(option)
    return keep


def _bank_config(panel, loadout, base=None, task=None, keep_extra=(), held=(), banked=None) -> dict:
    """banker.bank_up's config for one slayer bank visit: the panel's own bank block (bankNames,
    keepItems, teleportFallback) with the loadout's withdraw and keep lists laid over it. Never
    returnToSpot: TRAVEL re-derives the way back from wherever the bank was.

    `base` is the saved base gear ({slot: name}). Every base item is KEPT: the defender a mirror
    shield displaced rides in the inventory for the whole task, and banker deposits everything
    that is not on this list."""
    cfg = dict((panel or {}).get("bank") or {})
    keep = list(cfg.get("keepItems") or []) + list(loadout.keep) + [n.lower() for n in (base or {}).values() if n]
    keep += [n.lower() for n in (keep_extra or ()) if n]
    cfg.update({"enabled": True, "withdraw": loadout.withdraw, "returnToSpot": False,
                "keepItems": list(dict.fromkeys(keep)),
                "teleportFallback": cfg.get("teleportFallback", True)})

    # GEAR UP FROM THE SLAYER BEST IN BANK PLUGIN, while the bank is open (USER 2026-09-21:
    # "make gearing up before a kuradal task using slayer best in bank ... opens the widget and
    # picks up all missing pieces swaps out the gear").
    #
    # The plugin is installed and already decides the loadout for the live task, so we do not
    # duplicate its judgement -- slayer_bib clicks its bank button and withdraws what it shows.
    # The division of labour: SBIB says WHICH items, our own loadout says HOW MANY, because the
    # recommended counts live in plugin state no widget exposes.
    #
    # It runs BESIDE the ordinary withdraw list rather than replacing it: `withdraw` still fetches
    # the teleport and supplies this routine knows it needs, so a missing/disabled plugin costs us
    # nothing and the trip behaves exactly as it did before.
    # ONLY FOR KURADAL'S TASKS (USER 2026-09-21: "for aya task all we gotta pickup is a dragon
    # scimmy"). Aya's monsters are cows, rats and goblins -- running a whole best-in-slot loadout
    # out to them would cost a bank trip's worth of clicks for nothing, and her tasks are ~20
    # kills where travel already dominates. slayer_catalog holds exactly Kuradal's FIGHT tasks, so
    # membership in it IS the question being asked.
    if slayer_catalog.get(task) is not None:
        quantities = {e["name"].lower(): e.get("quantity", 1)
                      for e in (loadout.withdraw or []) if e.get("name")}
        cfg["atBank"] = lambda hook_ctx: slayer_bib.gear_up(hook_ctx, quantities=quantities)
    elif (not any(AYA_WEAPON in h for h in (held or ()))
          and any(AYA_WEAPON in n for n in (banked or {}))):
        # MEASURED live 2026-09-21: the account was wearing a Zombie axe with its only Dragon
        # scimitar banked, so this is a real fetch and not a no-op. _wear puts it on.
        #
        # Only asked for when bank memory says there IS one. Never-seen bank memory answers None
        # and we ask for nothing -- an Aya task is fightable with whatever is already wielded, so
        # a speculative withdraw would only add a click and a "not found in bank" line.
        cfg["withdraw"] = list(cfg.get("withdraw") or []) + [{"name": AYA_WEAPON, "quantity": 1}]
    return cfg


def _holds(equipment, slot) -> bool:
    """Is `slot` actually filled? MEASURED LIVE 2026-09-21: GET /equipment reports an EMPTY slot as
    `{}` -- present, with no name -- so `slot in equipment` is True either way. The slot holds
    something only when its value is a dict with a name."""
    worn = (equipment or {}).get(slot)
    return isinstance(worn, dict) and bool(worn.get("name"))


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
    # AYA'S TASKS take the scimitar and nothing else (USER 2026-09-21). Withdrawn by _bank_config;
    # put on here, because _wear is the only place that equips and a weapon sitting in the
    # inventory kills nothing. Silent no-op once it is on, and when it was never fetched -- an Aya
    # task is fightable bare-handed, so a missing scimitar is slower, never a reason to stop.
    if spec is None:
        if not gear.is_worn(AYA_WEAPON) and any(AYA_WEAPON in c for c in gear.carried_names()):
            debug_log.log(LOG, f"{state.task}: putting on the {AYA_WEAPON}")
            gear.equip_from_inventory(ctx, AYA_WEAPON)
            return True
        data["slayer_worn_ok"] = key
        return False
    for label in (spec.worn if spec else ()):
        options = slayer_catalog.REQUIREMENT_ITEMS.get(label, ())
        if not options or gear.is_worn(options[0], options[1:]):
            continue
        carried = gear.carried_names()
        pick = next((o for o in options if any(o in held for held in carried)), None)
        if pick is None:
            ctx.state.trigger_safety_stop(f"Slayer: {state.task} needs {label} WORN and none is carried")
            return True
        had_weapon = _holds(controller.get_equipment(), "weapon")
        debug_log.log(LOG, f"{state.task}: putting on the {pick} ({label})")
        if not gear.equip_from_inventory(ctx, pick):
            fails = data.get("slayer_wear_fails", 0) + 1
            data["slayer_wear_fails"] = fails
            if fails >= TRAVEL_MAX_FAILS:
                ctx.state.trigger_safety_stop(f"Slayer: could not put on the {pick} for {state.task} "
                                              f"after {fails} tries -- see the [gear] lines above")
            return True
        data["slayer_wear_fails"] = 0
        if had_weapon and not _holds(controller.get_equipment(), "weapon"):
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
    if banker.bank_up(ctx, _bank_config(panel, loadout, _base_gear(ctx), state.task,
                                        _gear_worth_keeping(), _held_names(), banked)):
        data.update({"slayer_restocking": None, "slayer_restocks": data.get("slayer_restocks", 0) + 1,
                     "slayer_bank_fails": 0, "slayer_route": None, "slayer_route_for": None,
                     "slayer_leg": 0, "slayer_route_finished": 0})
    elif not _waiting_on_bank_pin(ctx):
        data["slayer_bank_fails"] = data.get("slayer_bank_fails", 0) + 1
    return True



TELEPORT_LEG_KINDS = slayer_routes.TELEPORT_LEG_KINDS


def _route_teleports(route) -> bool:
    """Does this route actually TELEPORT, or is it just walking? See slayer_routes, which owns the
    question now -- slayer_preflight asks it as well, and answering it differently in two places
    would print a bank trip this routine is not going to make."""
    return slayer_routes.spends_a_teleport(route)


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
    # Only pack a teleport if the route we would actually take USES one. A walk or a transport
    # needs nothing fetched, and asking merely "is there a route" answers yes to a walk.
    #
    # `proven` is passed for the same reason TRAVEL passes it: without it every GATED transport
    # fails closed, so this step would see a shorter list of links than the journey itself will and
    # could fetch a teleport for a walk that TRAVEL then takes anyway. It is one bulk read per task
    # (MEASURED 1.16 s on an un-rebuilt client, ~20 ms once GET /varbits/<csv> exists).
    route, _why = slayer_routes.plan(state.task, player, carried, skill_levels=legs.skill_levels(),
                                     proven=legs.requirement_values())
    loadout = slayer_loadout.build(state.task, state.count, panel, prayer, 0, carried, banked,
                                   skip_teleport=route is not None and not _route_teleports(route))
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
    if banker.bank_up(ctx, _bank_config(panel, loadout, _base_gear(ctx), state.task,
                                        _gear_worth_keeping(), _held_names(), banked)):
        data["slayer_banked_for"] = key
        data["slayer_bank_fails"] = 0
    elif not _waiting_on_bank_pin(ctx):
        data["slayer_bank_fails"] = data.get("slayer_bank_fails", 0) + 1
    return True


def _waiting_on_bank_pin(ctx) -> bool:
    """A failed bank trip that is really "a human has not typed the PIN yet".

    FOUND LIVE 2026-09-21, the first bank trip of a fresh login: the Ferox chest opened onto the
    PIN keypad, bank_up returned False three times, and the run safety-stopped on what looked
    like a missed click. A PIN is not a failure this routine can retry its way out of and not one
    it may guess at, but it IS cleared by a human in seconds and then stays cleared for the whole
    login session -- so the right behaviour is to hold the phase, spend the tick, and let the very
    next one sail through. Counting it toward BANK_MAX_FAILS instead throws the task away three
    ticks after a problem that fixes itself.

    Logged only on the transition, because this is checked every tick while it waits."""
    if not banker.pin_prompt_open():
        ctx.state.data["slayer_bank_pin_wait"] = False
        return False
    if not ctx.state.data.get("slayer_bank_pin_wait"):
        debug_log.log(LOG, "the bank PIN keypad is up -- waiting for a human to enter it (once "
                           "per login session). Not counting this as a failed bank trip.")
        ctx.state.data["slayer_bank_pin_wait"] = True
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


# What to give up FIRST when a big drop needs a slot. The bridge does not price an INVENTORY
# item (only ground items carry a value), so this is a stated preference, not a guessed price:
# an almost-empty potion costs least, a full one most. Anything not listed ranks last.
GIVE_UP_ORDER = ("antipoison", "antidote", "anti-venom", "(1)", "(2)", "(3)")


def _give_up_rank(name) -> int:
    lowered = (name or "").lower()
    for rank, fragment in enumerate(GIVE_UP_ORDER):
        if fragment in lowered:
            return rank
    return len(GIVE_UP_ORDER)


def _protected_names(task_name) -> tuple:
    """Item-name fragments that must NEVER be dropped to make room: the task's own required items,
    the teleports that get us there and home, and the escape ring. Dropping the rock hammer to
    hold a whip would end the task; dropping the last tablet would strand the run."""
    spec = slayer_catalog.get(task_name)
    names = [slayer_loadout.HOUSE_TABLET, slayer_loadout.ESCAPE_RING, "slayer ring", "ectophial",
             "teleport", "talisman", "blessing", "crystal", "scroll"]
    for label in (tuple(spec.carry) + tuple(spec.worn)) if spec else ():
        names.extend(slayer_catalog.REQUIREMENT_ITEMS.get(label, ()))
    return tuple(dict.fromkeys(n.lower() for n in names if n))


def _use_inventory_item(ctx, name) -> bool:
    """Drink/eat one named inventory item -- the specific copy, not 'the first prayer potion'
    (controller.consume_first cannot tell a (1) from a (4), and the point here is to FREE A SLOT)."""
    lowered = (name or "").lower()
    item = next((i for i in (controller.get_inventory() or [])
                 if lowered == (i.get("name") or "").lower()), None)
    if item is None:
        return False
    controller.open_inventory_tab(ctx.ser)
    return bool(controller.click_entity(ctx.ser, item, check_ui_blockers=False, simulate_mistakes=False,
                                        expected_action=("Drink", "Eat")))


def _make_room(ctx, item) -> bool:
    """combat_core's makeRoom hook (plan task D2). True = take it, room has been made or none is
    needed. UNVERIFIED LIVE.

    The decision is slayer_policy.loot_decision (pure, tested): below the make-room value a full
    inventory leaves the drop; from there one food is eaten for it ONLY when HP is missing; from
    the must-loot value it is always taken -- eat, else finish a 1-dose prayer potion, else drop
    the least costly consumable. Never gear, never a teleport, never what the task needs."""
    data, panel = ctx.state.data, (ctx.state.config or {})
    tasks_cfg = panel.get("tasks") or {}
    inventory = controller.get_inventory() or []
    stackable_held = bool(item.get("stackable")) and any(i.get("id") == item.get("id") for i in inventory)
    player = controller.get_player() or {}
    hp, hp_max = player.get("hp"), player.get("hpReal")
    food = (panel.get("food") or {}).get("name")
    consumables = controller.get_consumables() or {}
    protected = _protected_names(data.get("slayer_task"))
    action, what = slayer_policy.loot_decision(
        int(item.get("totalValue") or 0), stackable_held,
        max(0, slayer_loadout.supplies.INVENTORY_SLOTS - len(inventory)),
        max(0, (hp_max or 0) - (hp or 0)),
        food, len(consumables.get("food") or []),
        slayer_policy.one_dose_potion(inventory, (panel.get("potions") or {}).get("prayerPotion")),
        slayer_policy.cheapest_consumable(inventory, food, protected, _give_up_rank),
        tasks_cfg=tasks_cfg)
    if action == slayer_policy.TAKE:
        return True
    if action == slayer_policy.LEAVE:
        return False
    debug_log.log(LOG, f"{item.get('name')} is worth {item.get('totalValue')}: {action} {what}")
    if action == slayer_policy.EAT_THEN_TAKE:
        return bool(controller.consume_first(ctx.ser, "food"))
    if action == slayer_policy.DRINK_THEN_TAKE:
        return _use_inventory_item(ctx, what)
    dropped = next((i for i in inventory if (i.get("name") or "").lower() == (what or "").lower()), None)
    return bool(dropped is not None and controller.drop_items(ctx.ser, [dropped]))


def _fight(ctx, state) -> None:
    controller.check_stray_dialog(ctx)
    panel = ctx.state.config
    cfg = combat_core.combat_config(panel, slayer_tasks.target_names(state.task))
    # The TASK picks the protection prayer, not the panel. USER 2026-09-21, live on the first
    # gargoyle task: "this is a dangerous task, i cant configure this for every task so it needs
    # to be decided for this task specifically we need pray for melee". The panel holds one
    # Defensive Prayer for every monster in the game and it was NONE, so a PRAYER-profile task
    # walked in carrying eight super restores it could never spend. A catalog task that states
    # its prayer overrides the panel; one that does not is left exactly as it was, and so is
    # combat.py, which never comes through here.
    #
    # `prayer.enabled` stays the user's: this decides WHICH prayer, never whether to pray.
    # ... and it is HELD on between kills, not flicked off the moment a gargoyle dies (USER
    # 2026-09-21: "Leave the protection on for the duration of the task. since its a very short
    # time between fights anyway"). The drain that buys is small on this account's travel: a bank
    # trip goes through Ferox, whose Pool of Refreshment restores prayer in full on the way past.
    spec = slayer_catalog.get(state.task)
    if spec is not None and spec.protect:
        cfg["prayer"] = {**(cfg.get("prayer") or {}),
                         "defensivePrayer": spec.protect, "holdDefensive": True}
    # The orchestrator owns restocks (spec 7.5). Left on, fight_tick teleports to a bank by itself,
    # and the next tick sees the player off-site and safety-stops there (final review).
    cfg["bank"] = {**(cfg.get("bank") or {}), "enabled": False}
    # Aya's monsters drop nothing worth a click and travel dominates a 20-kill task (spec §7.10):
    # only the panel's named whitelist ("items") is looted. maybe_loot reads "minValue" and treats
    # 0 as "no value looting" (phase-A review: the first draft wrote a key nobody reads).
    # ONLY for a task that is not in Kuradal's catalog (self-review 2026-09-21: this was applied to
    # every task, and gargoyles and nechryael drop plenty) -- hers keep the panel's value.
    if spec is None:
        cfg["loot"] = {**(cfg.get("loot") or {}), "minValue": 0}
    else:
        # A catalog task's drops are worth giving a slot up for; Aya's are not, so there is
        # nothing to make room FOR there. _make_room reads the task from ctx.state.data.
        ctx.state.data["slayer_task"] = state.task
        cfg["loot"] = {**(cfg.get("loot") or {}), "makeRoom": lambda item: _make_room(ctx, item)}
    ctx.state.config = cfg
    try:
        combat_core.fight_tick(ctx)
    finally:
        ctx.state.config = panel
    data = ctx.state.data
    data["slayer_route_finished"] = 0          # we are AT the site: the journey is over
    if data.get("current_target_index") is not None:
        # FINDING ONE ANSWERS THE QUESTION. FOUND LIVE 2026-09-21 on elves at Lletya: the walk-back
        # counter was never reset, so it accumulated over a whole task and stopped a run that was
        # working -- 86 elves already killed at that very tile. Lletya has THREE elves on the
        # ground floor (slayer_catalog says so, and the preflight prints "expect a slow task"), so
        # running the site dry and waiting for a respawn is NORMAL there, not evidence of being in
        # the wrong place. The guard is for "there is nothing here to fight, ever"; a target in
        # view is proof that there is.
        data["slayer_no_target"] = 0
        data["slayer_no_target_walks"] = 0
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


def before_break(ctx) -> None:
    """Called by the engine right before a break starts, while everything is still live (the
    routine contract in controller.py). UNVERIFIED LIVE as of 2026-09-21.

    tick() is SKIPPED for the whole break -- 3 to 12 minutes on this account -- so nothing eats,
    nothing prays and nothing retaliates. Standing next to a nechryael for that long is a death and
    an item loss, which is exactly what an unattended run must not risk. So if the break falls
    while we are AT THE SITE, let the target go and leave: Ferox Enclave, which nothing attacks and
    which has a pool and a bank. The break is then spent somewhere safe, and TRAVEL re-plans the
    way back afterwards -- from Ferox that is always a teleport, and plan() proves the walk from
    its landing before spending one.

    Away from the site (travelling, banking, at the house) nothing is attacking us, so the break
    costs nothing and this does nothing. A failed escape -- no ring of dueling left -- lets the
    break happen anyway, exactly as it does today: refusing a break is not this hook's call."""
    state = slayer_state.from_bridge(controller.get_slayer_task())
    if state is None or not state.has_task or not state.task:
        return
    player = controller.get_player() or {}
    if not slayer_tasks.at_site(state.task, player, margin=SITE_MARGIN):
        return
    debug_log.log(LOG, f"a break is due while fighting {state.task} -- leaving for Ferox first")
    ctx.state.data["current_target_index"] = None
    if travel.teleport_to_ferox(ctx):
        # The way back is re-planned from wherever the break leaves us, not from the site.
        ctx.state.data.update({"slayer_route": None, "slayer_route_for": None, "slayer_leg": 0,
                               "slayer_route_finished": 0})
    else:
        debug_log.log(LOG, "could not leave before the break -- taking it where we stand")


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
