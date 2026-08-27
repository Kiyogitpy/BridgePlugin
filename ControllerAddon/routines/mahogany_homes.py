# routines/mahogany_homes.py
#
# Runs Adept-tier Mahogany Homes contracts unattended: request a contract via the NPC Contact
# spell, travel to the assigned homeowner's house via teleport tablets, remove/build/repair
# every hotspot, turn the contract in, repeat. See
# docs/superpowers/specs/2026-08-27-mahogany-homes-design.md for the full design and the
# recording/decompile evidence behind every constant below.
#
# Every WorldArea/object-id/tier fact this routine depends on lives server-side in
# MahoganyHomesProvider.java (ported from the decompiled reference plugin) and is read fresh
# every tick via GET /mahogany-homes -- this file holds no duplicate copy of that data.
#
# NOT YET LIVE-TESTED as of this file's creation. HOTSPOT_CLICK_COOLDOWN_S/TURN_IN_CLICK_
# COOLDOWN_S below are first-draft guesses (no recording measured Mahogany Homes' own action
# duration the way golem_crafting.py's GOLEM_SIDE_TIMEOUT_S was measured at 11.8s/side) --
# expect to retune them after the first live run, the same way that constant was corrected
# from an initial guess once real timing was observed.

import time

import banker
import controller
import debug_log
import travel

# InterfaceID.MagicSpellbook.NPC_CONTACT (packed 14286961 == 218<<16 | 113) -- confirmed both
# against runelite-api-1.12.33.jar (javap -v) and against all four recordings, where every
# "Last-tier contract" click lands on this exact widget. See the design doc's "Contract
# request" section for the full derivation.
NPC_CONTACT_GROUP = 218
NPC_CONTACT_CHILD = 113

# F6 opens the Magic tab -- MEASURED in the recordings (0.6s before every NPC Contact click,
# e.g. recordings/2026-08-27_174045_mahogny-homes.log:2181-2186).
MAGIC_TAB_KEY = "F6"

CONTACT_RETRY_COOLDOWN_S = 6.0     # spell cast -> assignment chat measured at ~4.4s in the
                                    # recordings; this just stops re-casting while waiting.
HOTSPOT_CLICK_COOLDOWN_S = 2.5      # first-draft guess, see module docstring
TURN_IN_CLICK_COOLDOWN_S = 2.0      # first-draft guess, see module docstring
LADDER_CLICK_COOLDOWN_S = 4.0       # a floor change takes a game tick plus the climb animation;
                                    # without this the next tick would immediately re-click.
TRAVEL_TELEPORT_DISTANCE = 40       # tiles -- skip teleporting if already this close
HOME_ARRIVAL_TILES = 12             # every home's WorldArea is ~10x10 with standingLocation
                                    # roughly central, so this covers the whole house plus a
                                    # margin: "close enough that a missing hotspot means the
                                    # wrong FLOOR, not the wrong street".

# How many CONSECUTIVE ticks a phase may spend making no progress before it safety-stops instead
# of retrying silently forever. Deliberately not small: a transient miss (a slow scene load, the
# camera still turning, an NPC standing on the aim pixel) is normal and must not false-trigger,
# and at the engine's poll cadence 15 ticks is tens of seconds, not an instant trip. Every one of
# these counters resets to 0 on any real progress, so it only fires on a genuine hang.
STUCK_TICK_LIMIT = 15

# Which teleport item gets a player near each home's city, as (item name substring, menu option).
# A menu option of None means the item's own left-click default already teleports -- true for
# every "<City> teleport" tablet, whose default action is "Break". Xeric's talisman is the
# exception: its default is NOT the destination we want, so it has to go through the right-click
# menu and pick "Xeric's Glade" by name (confirmed in the recording's own action label,
# "Xeric's Glade -> widget 149.0 [Xeric's talisman]", against "Break -> widget 149.0 [Varrock
# teleport]" for the tablets). Xeric's Glade is the closest teleport to Barbara/Leela/Mariah's
# homes (see Mahogany Homes.txt's "Recommended setups") -- same city grouping as the reference
# plugin's Home enum.
CITY_TELEPORTS = {
    "JESS": ("ardougne teleport", None),
    "NOELLA": ("ardougne teleport", None),
    "ROSS": ("ardougne teleport", None),
    "LARRY": ("falador teleport", None),
    "NORMAN": ("falador teleport", None),
    "TAU": ("falador teleport", None),
    "BARBARA": ("xeric's talisman", "Xeric's Glade"),
    "LEELA": ("xeric's talisman", "Xeric's Glade"),
    "MARIAH": ("xeric's talisman", "Xeric's Glade"),
    "BOB": ("varrock teleport", None),
    "JEFF": ("varrock teleport", None),
    "SARAH": ("varrock teleport", None),
}

# Everything that must survive bank_phase's deposit-first step. Matched as a case-insensitive
# SUBSTRING by banker.deposit_inventory (so "plank" covers Teak/Mahogany/Oak plank alike). The
# configured plankName and every runeTargets key are appended on top of this at call time.
KEEP_ITEMS = [
    "hammer", "saw", "rune pouch", "xeric's talisman",
    "varrock teleport", "ardougne teleport", "falador teleport",
    "plank", "steel bar",
]

# No plank sack in this version -- planks are carried loose. See the plan's Task 5 for why the
# sack was left out rather than built the way golem_crafting's fur pouch was.
#
# SLOT BUDGET (28 inventory slots). The original comment here assumed 3 slots for the 3 tablet
# types; teleport TABLETS DO NOT STACK, so 8 of each was 24 slots on its own and the loadout
# could never fit. Corrected 2026-08-27 by the final whole-branch review. The arithmetic, so the
# next person can check it rather than re-derive it:
#
#   hammer                 1
#   saw                    1
#   rune pouch             1
#   Xeric's talisman       1
#   astral runes           1   (runes stack -- one slot no matter how many)
#   cosmic runes           1
#   varrock teleport   x3  3   (tablets DO NOT stack -- one slot each)
#   ardougne teleport  x3  3
#   falador teleport   x3  3
#   steel bar          x2  2   (bars do not stack; no Adept home needs more than ONE, so 2 is
#                               already a spare -- see MahoganyHomesProvider's Home table)
#                        ----
#                         17
#   teak plank        x10 10   -> 27 used, 1 slot deliberately left free for anything picked
#                        ----   up mid-contract (the tea, a stray drop) so a full inventory can
#                         27    never wedge an interaction.
#
# 10 planks no longer covers an Adept contract's worst case (~16-17) in one trip, and that is
# fine BY DESIGN as of this fix: work_phase now checks the CURRENT hotspot's own cost rather
# than the whole remaining contract's, so it works every hotspot it can afford and only banks
# when the next single one is unaffordable. Partial progress instead of an all-or-nothing trip.
DEFAULT_CONFIG = {
    "enabled": True,
    "tier": "adept",
    "plankName": "Teak plank",
    "restock": {
        # minPlanks is the BETWEEN-contract gate only (needs_bank_trip). Mid-contract shortfalls
        # are caught per-hotspot by work_phase instead, so this only needs to be above the
        # largest single hotspot cost (B4 = 4 planks).
        "minPlanks": 5,
        "targetPlanks": 10,
        "minSteelBars": 1,
        "targetSteelBars": 2,
        # 1, not 2: only 3 of each tablet are carried, and a contract uses at most one, so a
        # threshold of 2 would force a bank trip after a single teleport. 1 means "restock once
        # you are down to your last tablet", which still cannot strand us -- needs_bank_trip is
        # evaluated between every contract, before the tablet is needed.
        "minTabletCount": 1,
        "tabletTargets": {
            "varrock teleport": 3,
            "ardougne teleport": 3,
            "falador teleport": 3,
        },
        "minRuneCount": 30,
        "runeTargets": {
            "astral rune": 300,
            "cosmic rune": 300,
        },
    },
    "homeTeleport": "varrock teleport",
    "bank": {
        "bankNames": ["Bank booth", "Bank chest", "Bank counter", "Grand Exchange booth"],
        "maxBankDistance": 40,
    },
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
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    config = controller.get_mahogany_homes_config()
    if config is None:
        return DEFAULT_CONFIG

    # Only "enabled"/"breaks"/"safety" come from the bridge (ConfigExporter.exportMahoganyHomes)
    # -- everything else (restock thresholds, plank name, teleport mapping) is routine-specific
    # tuning that isn't worth a RuneLite GUI control, same reasoning golem_crafting.py's
    # "locations" re-injection comment gives for its own config.
    merged = dict(DEFAULT_CONFIG)
    merged["enabled"] = config.get("enabled", DEFAULT_CONFIG["enabled"])
    merged["breaks"] = config.get("breaks", DEFAULT_CONFIG["breaks"])
    merged["safety"] = config.get("safety", DEFAULT_CONFIG["safety"])
    return merged


def setup(ctx):
    debug_log.log("mahogany_homes", "reminder: RuneLite's menu-entry swapper must have Remove/"
                                     "Build/Repair set as the left-click default on Mahogany "
                                     "Homes hotspot objects, and NPC Contact must already be "
                                     "configured to request a last-tier (Adept) contract in one "
                                     "click -- this routine does not set up either.")


def tier_number(name: str) -> int:
    return {"beginner": 1, "novice": 2, "adept": 3, "expert": 4}.get((name or "").lower(), 0)


def pick_next_hotspot(hotspots: list):
    for hotspot in hotspots:
        if hotspot.get("needsAttention"):
            return hotspot
    return None


def find_object_by_id(objects: list, object_id: int):
    matches = [o for o in objects if o.get("id") == object_id]
    if not matches:
        return None
    return min(matches, key=lambda o: o.get("distance", float("inf")))


def deficit(current: int, target: int) -> int:
    return max(0, target - current)


def count_item(inventory: list, name: str) -> int:
    # "qty", NOT "quantity" -- GET /inventory (InventoryProvider.java) emits the stack size under
    # "qty", and plank_make.py's own item.get("qty", 1) is the established precedent for exactly
    # this. Reading "quantity" here always fell through to the default of 1, which happened to be
    # harmless only because every item counted at the time was non-stacking; the moment runes
    # (which very much stack) got counted it would have silently reported 1 of 300.
    name_lower = name.lower()
    return sum(
        item.get("qty", 1)
        for item in inventory
        if (item.get("name") or "").lower() == name_lower
    )


def rune_pouch_counts(rune_pouch: dict) -> dict:
    counts = {}
    for slot in rune_pouch.get("runes", []):
        name = (slot.get("name") or "").lower()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + slot.get("quantity", 0)
    return counts


def needs_bank_trip(ctx, config: dict, state: dict) -> bool:
    restock = config.get("restock", {})
    inventory = controller.get_inventory()
    plank_name = config.get("plankName", "Teak plank")

    if deficit(count_item(inventory, plank_name), restock.get("minPlanks", 0)) > 0:
        return True
    if deficit(count_item(inventory, "Steel bar"), restock.get("minSteelBars", 0)) > 0:
        return True

    min_tablets = restock.get("minTabletCount", 0)
    for tablet_name in restock.get("tabletTargets", {}):
        if deficit(count_item(inventory, tablet_name), min_tablets) > 0:
            return True

    min_runes = restock.get("minRuneCount", 0)
    rune_names = list(restock.get("runeTargets", {}))
    if rune_names:
        pouch = rune_pouch_counts(controller.get_rune_pouch())
        for rune_name in rune_names:
            if deficit(available_runes(inventory, pouch, rune_name), min_runes) > 0:
                return True

    return False


def available_runes(inventory: list, pouch_counts: dict, rune_name: str) -> int:
    """Loose runes in inventory PLUS whatever is in the rune pouch. OSRS draws a spell's runes
    from either automatically, so both count -- and bank_phase now withdraws LOOSE runes (it
    deliberately builds no rune-pouch-filling machinery, see its own comment), which a
    pouch-only check would never see. Checking the pouch alone therefore forced a bank trip on a
    player already carrying hundreds of the rune in hand."""
    return count_item(inventory, rune_name) + pouch_counts.get(rune_name.lower(), 0)


def bank_phase(ctx, config: dict, state: dict) -> None:
    bank_config = config.get("bank", {})
    bank_obj = banker.find_nearby_bank(bank_config)
    if bank_obj is None:
        home_teleport = config.get("homeTeleport", "varrock teleport")
        item = next(
            (i for i in controller.get_inventory() if home_teleport in (i.get("name") or "").lower()),
            None,
        )
        if item is None:
            ctx.state.trigger_safety_stop(
                f"out of {home_teleport} and no bank in range for Mahogany Homes restocking"
            )
            return
        debug_log.log("mahogany_homes", f"breaking {home_teleport} to reach a bank")
        controller.click_entity(ctx.ser, item, check_ui_blockers=False)
        controller.human_delay(2.5, 3.2)
        return

    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank for Mahogany Homes restocking")
        return

    restock = config.get("restock", {})
    plank_name = config.get("plankName", "Teak plank")

    # Deposit first. Two reasons: the loadout below is sized against a nearly-empty inventory
    # (see DEFAULT_CONFIG's slot budget), so anything that accumulated mid-contract would push
    # it over 28; and it is the routine's only recovery path if inventory ever ends up in a
    # state nothing else anticipated.
    #
    # skipContainerDeposit -- banker's generic "empty every container into the bank" step would
    # empty the RUNE POUCH, which is a keepItem precisely because we want its contents. This
    # routine manages no container that needs emptying, so the step has nothing to do here.
    keep = list(KEEP_ITEMS) + [plank_name] + list(restock.get("runeTargets", {}))
    banker.deposit_inventory(ctx, {"keepItems": keep, "skipContainerDeposit": True})

    _withdraw_up_to(ctx, plank_name, restock.get("targetPlanks", 0))
    _withdraw_up_to(ctx, "Steel bar", restock.get("targetSteelBars", 0))
    for tablet_name, target in restock.get("tabletTargets", {}).items():
        _withdraw_up_to(ctx, tablet_name, target)
    _withdraw_runes(ctx, restock)

    banker.close_bank(ctx)
    controller.human_delay(0.3, 0.6)

    # A bank trip that leaves us still short is the one failure this phase cannot fix by
    # repeating -- the bank is simply out of something. Bounded so it surfaces as a diagnosable
    # safety stop instead of an unbounded bank-open/withdraw-nothing/bank-close loop.
    if needs_bank_trip(ctx, config, state):
        stuck = ctx.state.data.get("mh_bank_stuck", 0) + 1
        ctx.state.data["mh_bank_stuck"] = stuck
        debug_log.log("mahogany_homes",
                      f"bank trip {stuck} left us still under the restock thresholds "
                      f"-- bank probably out of stock")
        if stuck >= STUCK_TICK_LIMIT:
            ctx.state.trigger_safety_stop(
                f"Mahogany Homes: {stuck} consecutive bank trips failed to reach the restock "
                f"thresholds -- the bank is out of planks/bars/tablets/runes"
            )
        return

    ctx.state.data["mh_bank_stuck"] = 0
    # A bank trip almost always ends somewhere else entirely (find_nearby_bank, or breaking the
    # home teleport), so the "already teleported for this contract" latch has to be released --
    # otherwise travel_phase would refuse to teleport back and try to WALK from the bank to the
    # house. Only reachable now that mid-contract banking actually happens (see tick()'s C3 fix);
    # before it, a bank diversion was overwritten before bank_phase ever ran.
    ctx.state.data["mh_teleported_this_contract"] = False
    ctx.state.data["mh_phase"] = "request_contract" if not state.get("hasContract") else "travel"


def _withdraw_up_to(ctx, name: str, target: int) -> None:
    have = count_item(controller.get_inventory(), name)
    need = deficit(have, target)
    if need <= 0:
        return
    banker.withdraw_loadout(ctx, {"withdraw": [{"name": name, "quantity": need}]}, fresh=True)


def _withdraw_runes(ctx, restock: dict) -> None:
    """Runes deliberately do NOT go through _withdraw_up_to. That helper is withdraw_loadout's
    per-unit click loop (one click per unit), which is right for 10 planks and catastrophic for
    300 astral runes. Runes stack, so one shift-click takes the whole bank stack into a single
    inventory slot -- the slot budget is identical whether that lands 300 or 30,000.

    Counted against loose inventory PLUS the rune pouch (available_runes), matching what
    needs_bank_trip gates on, so a pouch that already holds the target withdraws nothing."""
    targets = restock.get("runeTargets", {})
    if not targets:
        return
    inventory = controller.get_inventory()
    pouch = rune_pouch_counts(controller.get_rune_pouch())
    for rune_name, target in targets.items():
        if deficit(available_runes(inventory, pouch, rune_name), target) <= 0:
            continue
        banker.withdraw_all_shift(ctx, rune_name, fresh=True)


def request_contract_phase(ctx, config: dict, state: dict) -> None:
    if state.get("hasContract"):
        return

    last_cast = ctx.state.data.get("mh_contact_cast_at", 0.0)
    if time.time() - last_cast < CONTACT_RETRY_COOLDOWN_S:
        return

    controller.key_tap(ctx.ser, MAGIC_TAB_KEY)
    controller.human_delay(0.3, 0.5)

    widget = controller.get_widget(NPC_CONTACT_GROUP, NPC_CONTACT_CHILD)
    if not widget or not widget.get("clickable"):
        stuck = ctx.state.data.get("mh_contact_stuck", 0) + 1
        ctx.state.data["mh_contact_stuck"] = stuck
        debug_log.log("mahogany_homes",
                      f"NPC Contact widget {NPC_CONTACT_GROUP}.{NPC_CONTACT_CHILD} not clickable "
                      f"({stuck}x) -- Lunar spellbook active?")
        if stuck >= STUCK_TICK_LIMIT:
            ctx.state.trigger_safety_stop(
                f"Mahogany Homes: NPC Contact widget {NPC_CONTACT_GROUP}.{NPC_CONTACT_CHILD} has "
                f"been unclickable for {stuck} consecutive ticks -- is the Lunar spellbook active "
                f"and the spell unfiltered?"
            )
        return

    ctx.state.data["mh_contact_stuck"] = 0
    debug_log.log("mahogany_homes", "casting NPC Contact for a new contract")
    controller.click_entity(ctx.ser, widget, offset=(0, 0), check_ui_blockers=False)
    ctx.state.data["mh_contact_cast_at"] = time.time()


def travel_phase(ctx, config: dict, state: dict) -> None:
    home_name = state.get("homeName")
    if ctx.state.data.get("mh_last_home") != home_name:
        ctx.state.data["mh_last_home"] = home_name
        ctx.state.data["mh_teleported_this_contract"] = False

    target = state.get("standingLocation") or {}
    player = controller.get_player()
    if _chebyshev(player, target) > TRAVEL_TELEPORT_DISTANCE and not ctx.state.data.get("mh_teleported_this_contract"):
        entry = CITY_TELEPORTS.get(home_name)
        if entry is None:
            ctx.state.trigger_safety_stop(f"unrecognized Mahogany Homes home {home_name!r}")
            return
        teleport_name, menu_option = entry

        item = next(
            (i for i in controller.get_inventory() if teleport_name in (i.get("name") or "").lower()),
            None,
        )
        if item is None:
            debug_log.log("mahogany_homes", f"no {teleport_name} in inventory -- banking")
            ctx.state.data["mh_phase"] = "bank"
            return

        if menu_option is None:
            debug_log.log("mahogany_homes", f"breaking {teleport_name} toward {home_name}")
            controller.click_entity(ctx.ser, item, check_ui_blockers=False)
        else:
            # The item's left-click default is NOT the destination we want (Xeric's talisman),
            # so pick the option by name off the right-click menu instead.
            debug_log.log("mahogany_homes",
                          f"{teleport_name} -> {menu_option} toward {home_name}")
            controller.click_menu_action(ctx.ser, item, menu_option, check_ui_blockers=False)
        controller.human_delay(2.5, 3.2)
        ctx.state.data["mh_teleported_this_contract"] = True
        return

    if not travel.walk_to(ctx, target):
        debug_log.log("mahogany_homes", f"still walking toward {home_name}'s standing tile")
        return

    ctx.state.data["mh_phase"] = "work"


def _chebyshev(a: dict, b: dict) -> int:
    return max(abs(a.get("x", 0) - b.get("x", 0)), abs(a.get("y", 0) - b.get("y", 0)))


def work_phase(ctx, config: dict, state: dict) -> None:
    hotspots = state.get("hotspots") or []
    target = pick_next_hotspot(hotspots)
    if target is None:
        ctx.state.data["mh_phase"] = "turn_in"
        return

    # Cost of THIS ONE hotspot, not of the whole remaining contract. Comparing against
    # state["requiredMaterials"] (the sum over every hotspot still needing attention) meant that
    # being 2 planks short of a 14-plank contract sent us to the bank before touching any of the
    # 90% we could already afford. `material`/`quantity` come from MahoganyHomesProvider's own
    # per-slot HotspotType, added for exactly this.
    inventory = controller.get_inventory()
    plank_name = config.get("plankName", "Teak plank")
    if not _can_afford(inventory, plank_name, state, target):
        ctx.state.data["mh_phase"] = "bank"
        return

    objects = controller.get_objects()
    obj = find_object_by_id(objects, target["objectId"])
    if obj is None:
        _approach_missing_hotspot(ctx, config, state, target)
        return

    # Seeing the object at all is the progress signal -- reset before the cooldown gate, not
    # after the click, so a stuck streak clears the moment we are actually in position.
    ctx.state.data["mh_work_stuck"] = 0

    last_click = ctx.state.data.get("mh_last_hotspot_click", 0.0)
    if time.time() - last_click < HOTSPOT_CLICK_COOLDOWN_S:
        return

    debug_log.log("mahogany_homes", f"working hotspot {target['objectId']} (varb {target['varb']})")
    controller.click_entity(
        ctx.ser, obj,
        refresh_target=controller.object_refresh_target(obj["x"], obj["y"], obj["plane"], obj["id"]),
        refresh_legs=2,
    )
    ctx.state.data["mh_last_hotspot_click"] = time.time()


def _can_afford(inventory: list, plank_name: str, state: dict, target: dict) -> bool:
    """Cost of THIS ONE hotspot, not of the whole remaining contract. Comparing against
    state["requiredMaterials"] (the sum over every hotspot still needing attention) meant that
    being 2 planks short of a 14-plank contract sent us to the bank before touching any of the
    90% we could already afford.

    `material`/`quantity` come from MahoganyHomesProvider's own per-slot HotspotType, added for
    exactly this -- and they only exist on a REBUILT client. Against an older bridge the fields
    are absent, and a `quantity` defaulting to 0 would read as "this hotspot is free": fail-open,
    clicking hotspots we cannot pay for and silently achieving nothing. So an absent field falls
    back to the old whole-contract check instead, which is merely conservative."""
    quantity = target.get("quantity")
    if quantity is None:
        required = state.get("requiredMaterials") or {}
        have_planks = count_item(inventory, plank_name)
        have_bars = count_item(inventory, "Steel bar")
        if (deficit(have_planks, required.get("planks", 0)) > 0
                or deficit(have_bars, required.get("steelBars", 0)) > 0):
            debug_log.log(
                "mahogany_homes",
                f"per-hotspot cost missing from /mahogany-homes (client not rebuilt?) -- fell "
                f"back to the whole-contract check and we're short (have {have_planks} planks/"
                f"{have_bars} bars, need {required.get('planks', 0)}/"
                f"{required.get('steelBars', 0)}) -- banking",
            )
            return False
        return True

    material_name = "Steel bar" if target.get("material") == "STEEL_BAR" else plank_name
    have = count_item(inventory, material_name)
    if deficit(have, quantity) > 0:
        debug_log.log(
            "mahogany_homes",
            f"short on {material_name} for hotspot {target['objectId']} "
            f"(have {have}, need {quantity}) -- banking",
        )
        return False
    return True


def _approach_missing_hotspot(ctx, config: dict, state: dict, target: dict) -> None:
    """The hotspot object isn't in /objects. That is ambiguous in a way that matters: /objects
    only reports the player's CURRENT plane, and most homes put hotspots upstairs while
    standingLocation is always plane 0 -- so "not found" can mean "wrong floor", not just "not
    close enough yet". Try the home's own ladders (MahoganyHomesProvider ships their object ids
    per Home) before falling back to walking.

    "Climb-up"/"Climb-down" are passed as a SEQUENCE to one right-click menu read, so whichever
    direction the ladder actually offers from this floor is the one taken -- no need to guess
    the hotspot's plane, which the endpoint doesn't report.

    Walking is tried FIRST while still outside the house: a ladder can already be in the loaded
    scene from well down the street, and climbing one before arriving would just be noise. Only
    once we are standing in the house does an unfound hotspot mean "wrong floor". _chebyshev
    ignores plane on purpose, so being upstairs still reads as "at the house"."""
    stuck = ctx.state.data.get("mh_work_stuck", 0) + 1
    ctx.state.data["mh_work_stuck"] = stuck
    if stuck >= STUCK_TICK_LIMIT:
        ctx.state.trigger_safety_stop(
            f"Mahogany Homes: hotspot object {target.get('objectId')} at "
            f"{state.get('homeName')}'s house has not been reachable for {stuck} consecutive "
            f"ticks (walking and ladder-climbing both tried)"
        )
        return

    standing = state.get("standingLocation") or {}
    at_the_house = _chebyshev(controller.get_player(), standing) <= HOME_ARRIVAL_TILES

    ladder = None
    if at_the_house:
        ladder_ids = state.get("ladderObjectIds") or []
        if ladder_ids:
            ladder = min(
                (o for o in controller.get_objects() if o.get("id") in ladder_ids),
                key=lambda o: o.get("distance", float("inf")),
                default=None,
            )

    if ladder is not None:
        last_climb = ctx.state.data.get("mh_last_ladder_click", 0.0)
        if time.time() - last_climb < LADDER_CLICK_COOLDOWN_S:
            return
        debug_log.log(
            "mahogany_homes",
            f"hotspot {target['objectId']} not on this floor -- climbing ladder {ladder['id']} "
            f"({stuck}x since the last worked hotspot)",
        )
        controller.click_menu_action(ctx.ser, ladder, ["Climb-up", "Climb-down"],
                                     check_ui_blockers=False)
        ctx.state.data["mh_last_ladder_click"] = time.time()
        return

    debug_log.log(
        "mahogany_homes",
        f"hotspot object {target['objectId']} not visible yet ({stuck}x, no ladder to try) "
        f"-- walking toward {state.get('homeName')}'s house",
    )
    travel.walk_to(ctx, standing)


def turn_in_phase(ctx, config: dict, state: dict) -> None:
    npc_id = state.get("npcId")
    npc = controller.get_npc_nearest(npc_id)
    if npc is None:
        debug_log.log("mahogany_homes", f"homeowner npc {npc_id} not visible -- walking to standing tile")
        travel.walk_to(ctx, state.get("standingLocation") or {})
        return

    # NOTE: _accept_tea_if_offered is deliberately NOT called here -- see tick(), which calls it
    # unconditionally because the offer can arrive and vanish on a tick this phase never runs on.
    last_click = ctx.state.data.get("mh_last_turnin_click", 0.0)
    if time.time() - last_click < TURN_IN_CLICK_COOLDOWN_S:
        return

    debug_log.log("mahogany_homes", f"turning in the contract with {state.get('npcName')}")
    controller.click_menu_action(ctx.ser, npc, "Talk-to")
    ctx.state.data["mh_last_turnin_click"] = time.time()


def _accept_tea_if_offered(ctx) -> None:
    option = next(
        (w for w in controller.get_widgets_dump() if "cuppa" in (w.get("text") or "").lower()),
        None,
    )
    if option is not None:
        debug_log.log("mahogany_homes", "accepting the offered cup of tea")
        controller.click_entity(ctx.ser, option, offset=(0, 0), check_ui_blockers=False)


PHASE_HANDLERS = {
    "bank": bank_phase,
    "request_contract": request_contract_phase,
    "travel": travel_phase,
    "work": work_phase,
    "turn_in": turn_in_phase,
}


def tick(ctx) -> None:
    config = ctx.state.config
    if not config.get("enabled", True):
        return

    state = controller.get_mahogany_homes_state()

    # Unconditional, and NOT inside turn_in_phase, because the completion chat line and the
    # homeowner's tea offer arrive on the SAME tick: by the time hasContract flips false, tick()
    # has already left the "turn_in" phase, so a call sited inside that phase can never fire for
    # the tick the dialogue is actually up on. Cheap -- no-ops immediately when nothing matches.
    _accept_tea_if_offered(ctx)

    phase = ctx.state.data.get("mh_phase") or "bank"

    if state.get("hasContract") and not _tier_matches(config, state):
        # Should be unreachable live (NPC Contact is configured to request Adept), but an
        # unenforced constraint is one nobody notices breaking. There is no way to abandon a
        # contract in-game, so all we can do is refuse to work it and say so.
        if phase != "request_contract":
            debug_log.log(
                "mahogany_homes",
                f"contract tier {state.get('contractTier')} is not the configured "
                f"{config.get('tier')} tier -- refusing to work it",
            )
        ctx.state.data["mh_phase"] = "request_contract"
        return

    if not state.get("hasContract"):
        if needs_bank_trip(ctx, config, state):
            phase = "bank"
        else:
            phase = "request_contract"
    elif phase == "request_contract":
        # ONLY request_contract auto-promotes. "bank" must NOT: work_phase's own mid-contract
        # "I can't afford the next hotspot" diversion sets mh_phase to "bank", and promoting it
        # back to "travel" here discarded that diversion before bank_phase ever ran once --
        # a livelock of work -> divert -> overridden -> work. bank_phase sets the phase itself
        # once it has actually restocked.
        phase = "travel"
    elif phase == "work" and needs_bank_trip(ctx, config, state):
        debug_log.log("mahogany_homes", "running low mid-contract -- banking")
        phase = "bank"

    ctx.state.data["mh_phase"] = phase
    PHASE_HANDLERS[phase](ctx, config, state)


def _tier_matches(config: dict, state: dict) -> bool:
    """Global constraint: this routine only works Adept contracts. A contractTier of 0 means the
    provider could not parse a tier at all (e.g. an older client build) -- permissive there
    rather than deadlocking on a field that isn't being reported."""
    reported = state.get("contractTier") or 0
    if reported == 0:
        return True
    return reported == tier_number(config.get("tier", "adept"))
