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
TRAVEL_TELEPORT_DISTANCE = 40       # tiles -- skip teleporting if already this close

# Which teleport item gets a player near each home's city. Xeric's talisman's "Xeric's Glade"
# destination is the closest teleport to Barbara/Leela/Mariah's homes (see Mahogany Homes.txt's
# own "Recommended setups" section) -- same city grouping as the reference plugin's Home enum.
CITY_TELEPORTS = {
    "JESS": "ardougne teleport", "NOELLA": "ardougne teleport", "ROSS": "ardougne teleport",
    "LARRY": "falador teleport", "NORMAN": "falador teleport", "TAU": "falador teleport",
    "BARBARA": "xeric's talisman", "LEELA": "xeric's talisman", "MARIAH": "xeric's talisman",
    "BOB": "varrock teleport", "JEFF": "varrock teleport", "SARAH": "varrock teleport",
}

# No plank sack in this version -- planks are carried loose. targetPlanks=18 covers an Adept
# contract's worst case (Bob/Jeff need up to ~16-17 per the wiki's own per-homeowner table)
# with a small buffer, while leaving inventory room for tools/tablets/rune pouch/talisman
# (hammer+saw+rune pouch+3 tablet types+talisman = 7 slots; 18+3 = 21 fits in the remaining 21).
# See the plan's Task 5 for why the sack was left out rather than built the way golem_crafting's
# fur pouch was.
DEFAULT_CONFIG = {
    "enabled": True,
    "tier": "adept",
    "plankName": "Teak plank",
    "restock": {
        "minPlanks": 6,
        "targetPlanks": 18,
        "minSteelBars": 1,
        "targetSteelBars": 3,
        "minTabletCount": 2,
        "tabletTargets": {
            "varrock teleport": 8,
            "ardougne teleport": 8,
            "falador teleport": 8,
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
    name_lower = name.lower()
    return sum(
        item.get("quantity", 1)
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
    counts = rune_pouch_counts(controller.get_rune_pouch())
    for rune_name in restock.get("runeTargets", {}):
        if deficit(counts.get(rune_name.lower(), 0), min_runes) > 0:
            return True

    return False


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

    _withdraw_up_to(ctx, plank_name, restock.get("targetPlanks", 0))
    _withdraw_up_to(ctx, "Steel bar", restock.get("targetSteelBars", 0))
    for tablet_name, target in restock.get("tabletTargets", {}).items():
        _withdraw_up_to(ctx, tablet_name, target)

    banker.close_bank(ctx)
    controller.human_delay(0.3, 0.6)
    ctx.state.data["mh_phase"] = "request_contract" if not state.get("hasContract") else "travel"


def _withdraw_up_to(ctx, name: str, target: int) -> None:
    have = count_item(controller.get_inventory(), name)
    need = deficit(have, target)
    if need <= 0:
        return
    banker.withdraw_loadout(ctx, {"withdraw": [{"name": name, "quantity": need}]}, fresh=True)


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
        debug_log.log("mahogany_homes", "NPC Contact not clickable -- Lunar spellbook active?")
        return

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
        teleport_name = CITY_TELEPORTS.get(home_name)
        if teleport_name is None:
            ctx.state.trigger_safety_stop(f"unrecognized Mahogany Homes home {home_name!r}")
            return

        item = next(
            (i for i in controller.get_inventory() if teleport_name in (i.get("name") or "").lower()),
            None,
        )
        if item is None:
            debug_log.log("mahogany_homes", f"no {teleport_name} in inventory -- banking")
            ctx.state.data["mh_phase"] = "bank"
            return

        debug_log.log("mahogany_homes", f"breaking {teleport_name} toward {home_name}")
        controller.click_entity(ctx.ser, item, check_ui_blockers=False)
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

    required = state.get("requiredMaterials") or {}
    inventory = controller.get_inventory()
    plank_name = config.get("plankName", "Teak plank")
    have_planks = count_item(inventory, plank_name)
    have_bars = count_item(inventory, "Steel bar")
    if (deficit(have_planks, required.get("planks", 0)) > 0
            or deficit(have_bars, required.get("steelBars", 0)) > 0):
        debug_log.log(
            "mahogany_homes",
            f"short on materials for the rest of this contract (have {have_planks} planks/"
            f"{have_bars} bars, need {required.get('planks', 0)}/{required.get('steelBars', 0)}) -- banking",
        )
        ctx.state.data["mh_phase"] = "bank"
        return

    objects = controller.get_objects()
    obj = find_object_by_id(objects, target["objectId"])
    if obj is None:
        debug_log.log(
            "mahogany_homes",
            f"hotspot object {target['objectId']} not visible yet -- walking toward "
            f"{state.get('homeName')}'s house",
        )
        travel.walk_to(ctx, state.get("standingLocation") or {})
        return

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


def turn_in_phase(ctx, config: dict, state: dict) -> None:
    npc_id = state.get("npcId")
    npc = controller.get_npc_nearest(npc_id)
    if npc is None:
        debug_log.log("mahogany_homes", f"homeowner npc {npc_id} not visible -- walking to standing tile")
        travel.walk_to(ctx, state.get("standingLocation") or {})
        return

    _accept_tea_if_offered(ctx)

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
    phase = ctx.state.data.get("mh_phase") or "bank"

    if not state.get("hasContract"):
        if needs_bank_trip(ctx, config, state):
            phase = "bank"
        elif phase != "request_contract":
            phase = "request_contract"
    elif phase in ("bank", "request_contract"):
        phase = "travel"
    elif phase == "work" and needs_bank_trip(ctx, config, state):
        debug_log.log("mahogany_homes", "running low mid-contract -- banking")
        phase = "bank"

    ctx.state.data["mh_phase"] = phase
    PHASE_HANDLERS[phase](ctx, config, state)
