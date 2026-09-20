# slayer_preflight.py -- what the slayer routine WOULD decide right now, printed. It clicks nothing.
#
#     python slayer_preflight.py                        # the live task
#     python slayer_preflight.py --task "Nechryael" --count 150     # what if this were the task
#     python slayer_preflight.py --all                              # all 22 Kuradal tasks, one line each
#
# Added 2026-09-20. Every slayer live gate so far has been spent discovering a fact the routine
# could have told us before a single click: the account holds a STOP task, no leg can run the
# task's teleport, the panel has no food chosen, the bank has no tablet. This reads the same
# inputs the routine reads and runs the same PURE decisions -- disposition, site, required items,
# route, loadout, bank verdict -- so the first minutes of a gate are spent watching it work.
#
# Read-only and safe beside a running controller.py: it imports `controller` for bridge GETs but
# never opens the serial port (the same contract as bank_memory.py's own probe). report() is pure
# and takes everything as arguments, which is what the tests drive.

import argparse
import sys

import slayer_catalog
import slayer_loadout
import slayer_policy
import slayer_routes
import slayer_state
import slayer_tasks


def _tablets_total(carried, banked):
    held = sum(int(q or 0) for n, q in (carried or {}).items()
               if not n.startswith("#") and slayer_loadout.HOUSE_TABLET in n)
    if banked is None:
        return None
    return held + sum(int(q or 0) for n, q in banked.items() if slayer_loadout.HOUSE_TABLET in n)


def report(task, count, streak, points, player, carried, banked, panel_cfg, tasks_cfg,
           skill_levels, prayer_level, prayer_bonus, proven=None) -> list:
    lines = []
    master = slayer_state.next_master(streak, tasks_cfg)
    if not task:
        return [f"no task held (streak {streak}, {points} points).",
                f"next master: {master} -- the routine would contact them by NPC Contact."]

    disposition = slayer_tasks.disposition(task, tasks_cfg)
    lines.append(f"task: {task} x{count}  (streak {streak}, {points} points)  ->  {disposition}")
    lines.append(f"next master after this task: {master}")
    if disposition == slayer_tasks.STOP:
        lines.append("STOP: the routine safety-stops on this task by design (done by hand, or unknown).")
        return lines
    if disposition == slayer_tasks.SKIP:
        ok, why = slayer_state.can_skip(points, 0, tasks_cfg)
        lines.append(f"SKIP: a 30-point cancel -- {'allowed' if ok else 'NOT allowed: ' + str(why)}")
        return lines

    where = slayer_tasks.where(task)
    here = slayer_tasks.at_site(task, player, margin=0)
    lines.append(f"site: {where or '(none known)'}")
    lines.append(f"at the site now: {here}")

    missing = slayer_tasks.missing_items(task, count, carried)
    if missing:
        lines.append(f"STOP before any teleport -- not carried: {', '.join(missing)}")

    legs_, why = slayer_routes.plan(task, player, carried, skill_levels=skill_levels, proven=proven)
    if legs_ is None:
        lines.append(f"cannot get there from {tuple((player or {}).get(k) for k in ('x', 'y', 'plane'))}: {why}")
    else:
        lines.append("route: " + "  ->  ".join(leg["display"] for leg in legs_))

    route_now = legs_
    bank_note = "bank NEVER SEEN -- the pack below is built on faith, open the bank once" if banked is None \
        else f"bank seen ({len(banked)} item names)"
    lines.append(bank_note)
    loadout = slayer_loadout.build(task, count, panel_cfg, prayer_level, prayer_bonus, carried, banked)
    lines.append("withdraw: " + (", ".join(f"{w['quantity']} {w['name']}" for w in loadout.withdraw) or "nothing"))
    lines.append(f"pack: {loadout.slots} of 28 slots")
    if route_now is None and loadout.withdraw:
        # plan() works from what is CARRIED. The routine (phase A) therefore stops here today; the
        # BANK phase would fetch the pack first. Show the route as it stands once that is done.
        after = dict(carried or {})
        for w in loadout.withdraw:
            after[w["name"].lower()] = after.get(w["name"].lower(), 0) + w["quantity"]
        legs_after, why_after = slayer_routes.plan(task, player, after, skill_levels=skill_levels,
                                                   proven=proven)
        lines.append("after the bank trip: " + ("  ->  ".join(leg["display"] for leg in legs_after)
                                                if legs_after else f"still no route -- {why_after}"))
    for problem in loadout.problems:
        lines.append(f"  problem: {problem}")

    spec = slayer_catalog.get(task)
    if spec is not None and spec.worn:
        lines.append(f"must be WORN (not checked here): {', '.join(spec.worn)}")
    if spec is not None and spec.note:
        lines.append(f"note: {spec.note}")

    skippable = disposition == slayer_tasks.FIGHT and slayer_state.can_skip(points, 0, tasks_cfg)[0]
    verdict, reason = slayer_policy.bank_verdict(loadout, None, tasks_cfg, _tablets_total(carried, banked),
                                                 skippable=skippable)
    lines.append(f"bank verdict: {verdict}" + (f" -- {reason}" if reason else ""))
    return lines


def overview(count, player, carried, banked, panel_cfg, skill_levels, prayer_level, prayer_bonus,
             proven=None) -> list:
    """One line per Kuradal FIGHT task: does it plan once the pack is fetched, and what is short.
    The whole catalog against one account, so a gap shows up here and not at the site."""
    lines, planned = [], 0
    for key in slayer_catalog.CATALOG:
        loadout = slayer_loadout.build(key, count, panel_cfg, prayer_level, prayer_bonus, carried, banked)
        after = dict(carried or {})
        for w in loadout.withdraw:
            after[w["name"].lower()] = after.get(w["name"].lower(), 0) + w["quantity"]
        route, why = slayer_routes.plan(key, player, after, skill_levels=skill_levels, proven=proven)
        planned += 1 if route else 0
        lines.append(f"{key:18} " + ("ROUTE " + " > ".join(leg["display"] for leg in route) if route
                                      else "NO ROUTE: " + why))
        lines.extend(f"{'':18}   short: {problem}" for problem in loadout.problems)
    lines.append(f"{planned} of {len(slayer_catalog.CATALOG)} plan after a bank trip")
    return lines


def _main(argv) -> int:
    parser = argparse.ArgumentParser(description="What the slayer routine would decide right now. Read-only.")
    parser.add_argument("--task", help="pretend this is the task instead of the live one")
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--all", action="store_true", help="every Kuradal FIGHT task, one line each")
    args = parser.parse_args(argv)

    import bank_memory
    import controller
    import legs
    from routines import slayer as routine

    state = slayer_state.from_bridge(controller.api_get("/slayer"))
    if state is None:
        print("the bridge (or GET /slayer) is unavailable -- is the client running, and rebuilt?")
        return 1
    config = controller.api_get("/config") or {}
    skills = controller.get_skills() or {}
    prayer = (skills.get("Prayer") or {}).get("level") or 1
    if args.all:
        for line in overview(args.count, controller.get_player() or {}, routine._carried(),
                             slayer_loadout.banked_from_memory(bank_memory.load()), config,
                             legs.skill_levels(), prayer, 0, proven=legs.requirement_values()):
            print(line)
        return 0
    task = args.task if args.task else (state.task if state.has_task else None)
    count = args.count if args.task else state.count
    if args.task:
        print(f"(pretending the task is {args.task} x{count}; the live task is {state.task} x{state.count})")
    for line in report(task, count, state.streak, state.points, controller.get_player() or {},
                       routine._carried(), slayer_loadout.banked_from_memory(bank_memory.load()),
                       config, config.get("tasks") or {}, legs.skill_levels(), prayer, 0,
                       proven=legs.requirement_values()):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
