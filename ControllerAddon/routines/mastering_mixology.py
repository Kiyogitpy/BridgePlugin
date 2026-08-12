"""routines/mastering_mixology.py

Automates Mastering Mixology under the max-total-points-per-paste strategy (see
docs/superpowers/specs/2026-08-12-mastering-mixology-design.md): bank herbs -> refine -> hopper
-> read the 3 active orders -> mix -> process -> deposit, skipping single-paste orders (AAA/MMM/
LLL) unless an MAL order is present in the same set of 3.
"""

import random
import time

import banker
import controller
import debug_log

MAX_OBJECT_DISTANCE = 15
HERBLORE_LEVEL_REQUIRED = 60

# name -> {mox, aga, lye} -- matches the varbit type-index order MixologyProvider reports (see
# this plan's Global Constraints), NOT alphabetical or wiki-page order.
RECIPE_TABLE = {
    "Mammoth-might mix": {"mox": 3, "aga": 0, "lye": 0},
    "Mystic mana amalgam": {"mox": 2, "aga": 1, "lye": 0},
    "Marley's moonlight": {"mox": 2, "aga": 0, "lye": 1},
    "Alco-augmentator": {"mox": 0, "aga": 3, "lye": 0},
    "Azure aura mix": {"mox": 1, "aga": 2, "lye": 0},
    "Aqualux amalgam": {"mox": 0, "aga": 2, "lye": 1},
    "Liplack liquor": {"mox": 0, "aga": 0, "lye": 3},
    "Megalite liquid": {"mox": 1, "aga": 0, "lye": 2},
    "Anti-leech lotion": {"mox": 0, "aga": 1, "lye": 2},
    "Mixalot": {"mox": 1, "aga": 1, "lye": 1},
}
SINGLE_PASTE_BASES = {"Alco-augmentator", "Mammoth-might mix", "Liplack liquor"}
MAL_BASE = "Mixalot"

# ItemID.MM_POTION_*_UNFINISHED / _FINISHED (net.runelite.api.gameval.ItemID,
# runelite-api-1.12.35.jar) -- processing transforms a base potion in place and the display
# NAME is identical before and after (confirmed: Mammoth-might mix has id1=30011 (Unfinished)
# and id2=30021 (Processed) sharing one `name` field in the wiki infobox), so name matching
# cannot distinguish unprocessed from processed. This ordering matches VESSEL_ID_TO_BASE's
# object-id order (NOT RECIPE_TABLE's varbit order) -- see this plan's Global Constraints.
UNFINISHED_IDS = {
    30011: "Mammoth-might mix", 30012: "Mystic mana amalgam", 30013: "Marley's moonlight",
    30014: "Alco-augmentator", 30015: "Aqualux amalgam",      30016: "Azure aura mix",
    30017: "Liplack liquor",   30018: "Anti-leech lotion",    30019: "Megalite liquid",
    30020: "Mixalot",
}
FINISHED_IDS = {k + 10: v for k, v in UNFINISHED_IDS.items()}

# ItemID.MM_LAB_SPECIAL_HERB -- Digweed. Used instead of a sticky ctx.state.data flag (see Fix 5
# in the final whole-branch review) because the plant withers out of inventory if the player
# leaves the minigame area, and find_item()'s exact-match semantics made a flag-based confirm
# unreliable if the in-game name isn't precisely "Digweed".
MM_LAB_SPECIAL_HERB_ID = 30031

DEFAULT_CONFIG = {
    "enabled": True,
    "herbPriority": {
        "mox": ["Guam leaf", "Marrentill", "Tarromin", "Harralander"],
        "aga": ["Huasca", "Irit leaf", "Cadantine", "Lantadyme", "Dwarf weed", "Torstol"],
        # Ranarr weed and Snapdragon deliberately excluded (user request 2026-08-12): reserved
        # for other uses, never to be consumed by this routine even if in stock.
        "lye": ["Toadflax", "Avantoe", "Kwuarm"],
    },
    "hopperTarget": 1000,
    "hopperLowWater": 150,
    "digweed": {"enabled": True},
    "speedups": {"retort": True, "agitator": True, "alembic": True},
    "maxObjectDistance": MAX_OBJECT_DISTANCE,
    "bank": {"teleportFallback": False},
    "breaks": {
        "enabled": True, "everyMinMinutes": 45, "everyMaxMinutes": 75,
        "durationMinMinutes": 3, "durationMaxMinutes": 12, "microBreaks": True,
    },
    "safety": {"stopOnDeath": True, "maxRuntimeHours": 0},
}


def get_config(bridge_config):
    config = controller.get_mastering_mixology_config()
    if config is None:
        return DEFAULT_CONFIG

    # ConfigExporter.exportMasteringMixology only emits enabled/bank/digweed/speedups/breaks/
    # safety -- herbPriority/hopperTarget/hopperLowWater/maxObjectDistance silently vanish
    # whenever the Controller plugin is actually running (i.e. always, in real deployment) if the
    # live bridge config were returned as-is, even though every phase function still reads them
    # via config.get(key, DEFAULT_CONFIG[key]) and so never crashes -- it just silently ignores
    # any user customization of those keys. Same bug golem_crafting.py's get_config hit and fixed
    # for its own "locations" key -- same fix shape here.
    config = dict(config)
    for key in ("herbPriority", "hopperTarget", "hopperLowWater", "maxObjectDistance"):
        config.setdefault(key, DEFAULT_CONFIG[key])
    return config


def find_item(name: str):
    """First inventory item whose name matches `name` exactly (case-insensitive) -- same
    exact-match idiom as golem_crafting.py's find_item."""
    name_lower = name.lower()
    return next(
        (i for i in controller.get_inventory() if (i.get("name") or "").lower() == name_lower),
        None,
    )


def find_object(name: str, exclude: str = None):
    """Closest visible object whose name contains `name` (and does NOT contain `exclude`, if
    given), within MAX_OBJECT_DISTANCE -- same shape (including the radius fix) as
    golem_crafting.py's find_object. Every Mastering Mixology fixture is a multiloc/impostor
    object, so this resolves correctly regardless of which content variant (empty, or holding a
    specific potion) is currently active -- see the design doc's "Physical objects are
    multiloc" note.

    `exclude` exists for Digweed: CONFIRMED live 2026-08-12 (user report: "digweed spawned and
    we clicked the wrong one, should have been name=Mature not young") -- the wiki confirms
    digweed grows through a young stage before it matures and blooms into the collectible
    plant, and the young stage's object name apparently also contains "digweed", so a plain
    substring match can grab the wrong growth stage."""
    name_lower = name.lower()
    exclude_lower = exclude.lower() if exclude else None
    candidates = [
        o for o in controller.get_objects(radius=MAX_OBJECT_DISTANCE)
        if name_lower in (o.get("name") or "").lower()
        and (exclude_lower is None or exclude_lower not in (o.get("name") or "").lower())
        and o.get("visible", True)
        and 0 <= o.get("distance", 9999) <= MAX_OBJECT_DISTANCE
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]


def _click_world_object(ctx, name: str, exclude: str = None, timeout_s: float = 5.0, **kwargs):
    """Wait for the player to be genuinely idle (see controller.wait_until_player_idle), THEN
    resolve the closest visible object named `name`, THEN click it -- in that order.

    CONFIRMED live 2026-08-12, three rounds before this one: round 1 gated several call sites
    but handed the click a position the CALLER had already resolved before waiting for the
    camera. Round 2 fixed that ordering but gated on camera yaw/pitch settling, which this
    minigame's camera defeats by retargeting almost every tick as the player character turns to
    face its next interaction target -- yaw==yawTarget held on nearly every poll even while the
    net on-screen position kept drifting. Round 3 replaced the camera proxy with polling the
    object's own screen position directly until stable, which was closer but still missed a
    real, DIFFERENT mechanism live reads turned up during that same investigation: the player's
    own x/y genuinely shifts a few tiles between lever pulls (the Mox/Aga/Lye levers aren't all
    reachable from one standing tile), which a screen-position poll starting fresh each call
    can't see coming. Per explicit user direction 2026-08-12 ("lets scrap the camera bullshit
    ... We go with animation instead"), this now gates on the player's own activity state
    (animation + pose + position, see wait_until_player_idle) instead of anything screen/camera
    derived -- directly answering "is my character still busy or still moving" rather than
    inferring it from a proxy. Returns the freshly-resolved object (or None if not found/no
    click attempted) so callers that need its other fields (impostorId, world tile x/y, etc.)
    don't need a second lookup."""
    controller.wait_until_player_idle(timeout_s=timeout_s)
    entity = find_object(name, exclude=exclude)
    if entity is None:
        return None
    controller.click_entity(ctx.ser, entity, **kwargs)
    return entity


def check_level_requirement(ctx, config: dict) -> bool:
    """One-time-per-activation under-level sanity check -- same shape and same setup()-vs-tick()
    reasoning as golem_crafting.py's check_level_requirement.

    Returns False (without performing the check) if Herblore's reported level is 0 -- CONFIRMED
    live 2026-08-12: every real OSRS skill's minimum level is 1, so a 0 reading is never a real
    under-level account, it's /skills responding in the brief window right after
    login.is_fully_logged_in() first reports True but before RuneLite's own skill-data event has
    actually landed. Trusting it blindly permanently safety-stopped a perfectly fine level-90+
    account during this exact live session. Caller should retry next tick rather than mark the
    check done when this returns False."""
    herblore = controller.get_skills().get("Herblore") or {}
    level = herblore.get("level")
    if level == 0:
        return False
    if level is not None and level < HERBLORE_LEVEL_REQUIRED:
        ctx.state.trigger_safety_stop(
            f"Herblore level {level} is below the {HERBLORE_LEVEL_REQUIRED} required for Mastering Mixology"
        )
    return True


def bank_herbs_phase(ctx, config: dict) -> None:
    """Withdraw herbs per herbPriority (first candidate actually in the bank, per paste type),
    refine them, deposit the resulting paste into the hopper. Repeats (the phase re-enters
    itself on the next tick if the hopper target isn't reached yet) until every paste type in
    /mixology-state's hopper totals reaches hopperTarget, or safety-stops if the bank has none
    of a paste type's configured herb candidates left at all."""
    hopper = controller.get_mixology_state().get("hopper", {})
    target = config.get("hopperTarget", 1000)
    if all(hopper.get(paste, 0) >= target for paste in ("mox", "aga", "lye")):
        debug_log.log("mixology", f"hopper stocked ({hopper}) -- moving to orders phase")
        ctx.state.data["mm_phase"] = "orders"
        return

    herb_priority = config.get("herbPriority", DEFAULT_CONFIG["herbPriority"])
    needed_types = [p for p in ("mox", "aga", "lye") if hopper.get(p, 0) < target]
    if not needed_types:
        ctx.state.data["mm_phase"] = "orders"
        return
    per_type_qty = min(14, 28 // len(needed_types))

    bank_cfg = {"teleportFallback": config.get("bank", {}).get("teleportFallback", False)}
    bank_obj = banker.find_nearby_bank(bank_cfg)
    if bank_obj is None:
        ctx.state.trigger_safety_stop("no bank found near Mastering Mixology (and no teleport fallback configured)")
        return
    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank")
        return

    banker.deposit_inventory(ctx, {"keepItems": []})

    # CONFIRMED live 2026-08-12 (user report): searching each configured herb name one at a
    # time -- resolving "which candidate is in stock" from one bank read, then re-searching and
    # clicking from a SEPARATE later read inside search_and_withdraw -- raced against reality:
    # a candidate that looked in-stock at selection time could be genuinely out of stock by the
    # time the click-specific read ran, and the mismatch was only logged, never escalated. The
    # phase then looped open/closed on the bank forever, making no progress. Switching to tab 8
    # (user's own dedicated herb-storage tab, per user request) once surfaces every herb in one
    # view, so candidate selection below and the actual withdrawal both read from the same live
    # state -- no more two-read race.
    if not banker.switch_bank_tab(ctx, 8):
        banker.close_bank(ctx)
        ctx.state.trigger_safety_stop("could not switch to the herb bank tab")
        return

    bank_contents = controller.get_bank(fresh=True).get("items", [])
    chosen_names = {}
    for paste_type in needed_types:
        candidates = herb_priority.get(paste_type, [])
        # Substring match (not exact) -- herbPriority's candidates are clean names ("Avantoe"),
        # but the account can hold the grimy/uncleaned form ("Grimy avantoe") instead, or as
        # well; CORRECTED same session (this exact bug, caught live within minutes of writing
        # it): an exact match here missed real, in-stock Kwuarm/Avantoe entirely because their
        # actual bank names carry the "Grimy" prefix. Requiring qty > 0 alongside the substring
        # match is what actually matters for the zero-stock-placeholder problem (see the fix
        # earlier this session).
        chosen = next(
            (name for name in candidates
             if any(name.lower() in (i.get("name") or "").lower() and i.get("qty", 0) > 0
                    for i in bank_contents)),
            None,
        )
        if chosen is None:
            banker.close_bank(ctx)
            ctx.state.trigger_safety_stop(
                f"none of the configured {paste_type} herbs ({candidates}) are in the bank"
            )
            return
        chosen_names[paste_type] = chosen

    # CONFIRMED (user report): withdrawing one unit at a time (quantity mode "one", one click
    # per unit) is needlessly slow and click-heavy. CORRECTED same session (user report):
    # "X" means the per-item right-click "Withdraw-X" menu option, not the persistent global
    # quantity-mode button -- each herb needs its own right-click + Withdraw-X + typed amount.
    for paste_type, name in chosen_names.items():
        # Re-resolve fresh right before each click rather than reusing a position captured
        # earlier in this same loop -- an earlier withdrawal in this loop can shift the tab's
        # item grid (e.g. a herb that just hit 0 stock drops out, reflowing whatever came after
        # it) -- same "always re-read, never trust a captured position" principle as this file's
        # other click loops.
        current = controller.get_bank(fresh=True).get("items", [])
        # CONFIRMED live 2026-08-12: exact-name match here was inconsistent with the substring
        # match used for candidate selection just above -- this account's real stock sat in the
        # grimy form ("Grimy toadflax", qty 20) while the clean-named placeholder ("Toadflax",
        # qty 0) matched the exact-equality test and lost every time to the qty>0 filter, so the
        # herb was always reported "no longer available" even though real stock existed.
        slot = next(
            (i for i in current if name.lower() in (i.get("name") or "").lower() and i.get("qty", 0) > 0),
            None,
        )
        if slot is None:
            debug_log.log("mixology", f"{name} no longer available when it came time to withdraw it -- skipping this cycle, will re-evaluate next tick")
            continue
        if not controller.click_menu_action(ctx.ser, slot, "Withdraw-X", check_ui_blockers=False):
            debug_log.log("mixology", f"Withdraw-X menu entry not found for {name} -- skipping this cycle")
            continue
        # CONFIRMED live 2026-08-12: click_menu_action returns immediately after its click lands
        # (no settle wait built in -- other callers don't need one, since they don't chain a
        # keyboard prompt after it), but the "Enter amount" chatbox needs a moment to actually
        # render and take keyboard focus. Typing immediately dropped the digits silently (menu
        # click logged fine, digits logged fine, but nothing was ever actually withdrawn).
        time.sleep(random.uniform(0.4, 0.6))
        controller.type_digits_and_enter(ctx.ser, str(per_type_qty))
        time.sleep(random.uniform(0.3, 0.5))

    banker.close_bank(ctx)

    _refine_and_hopper_inventory(ctx)


def _clean_grimy_herbs(ctx) -> None:
    """Left-click each 'Grimy X' herb currently held to clean it into its usable form --
    CONFIRMED live 2026-08-12 (user report, and data/raw/M/Mastering Mixology.txt:73: "Grimy
    herbs are typically cheaper... but cannot be used in mixology until cleaned"): the Refiner
    only accepts clean herbs, never grimy ones directly. Cleaning is one click per herb, not a
    batch action (herbs aren't stackable), so this loops one at a time until nothing grimy
    remains in inventory or a bounded deadline is hit."""
    deadline = time.time() + 30.0
    while time.time() < deadline:
        grimy = next(
            (i for i in controller.get_inventory() if (i.get("name") or "").lower().startswith("grimy ")),
            None,
        )
        if grimy is None:
            return
        controller.click_entity(ctx.ser, grimy, check_ui_blockers=False)
        time.sleep(random.uniform(0.55, 0.75))  # one game tick
    debug_log.log("mixology", "grimy herb cleaning didn't finish within the time budget")


def _refine_and_hopper_inventory(ctx) -> None:
    """Clean any grimy herbs held, then deposit every clean herb / unfinished potion currently
    held into the refiner (clicking every tick to double refine speed, per the wiki), then
    deposit the resulting paste into the hopper. Loops until nothing refinable is left in the
    inventory."""
    _clean_grimy_herbs(ctx)

    # CONFIRMED live 2026-08-12: the refiner's on-screen position was fetched ONCE before this
    # loop and reused for the entire ~60s clicking window -- user directly observed clicks
    # landing off-target ("in thin air") partway through a long refine run. Screen coordinates
    # depend on the camera, which can drift continuously (not tick-quantized) over that window,
    # so a position cached at loop start goes stale the longer the loop runs. Re-fetching fresh
    # every iteration (this is a bridge read, not a serial action, so it's cheap) keeps every
    # click aimed at the refiner's actual current position.
    all_herb_names = {name for names in DEFAULT_CONFIG["herbPriority"].values() for name in names}
    # CONFIRMED live 2026-08-12: this account's real stock can be the grimy/uncleaned form
    # ("Grimy toadflax"), but exact-set membership only recognized the clean configured names
    # ("Toadflax") -- held was always empty for anything grimy, so this function silently
    # no-opped (never clicked the Refiner, never deposited to the Hopper) every time
    # bank_herbs_phase withdrew a grimy herb, and the very next cycle just deposited it straight
    # back into the bank. Substring match (same fix already applied to the withdrawal-candidate
    # matching above) makes "Grimy toadflax" correctly recognized as a Toadflax-family herb.
    # _click_world_object's player-idle gate covers arriving here via a walk from the bank, but
    # once actually standing at the Refiner clicking it repeatedly, the player never moves again
    # until this loop ends -- per user direction 2026-08-12 ("we can just spam click them until
    # we are finished, no need to do it every tick... no need to move between clicks either"),
    # only the FIRST click needs the idle-wait + fresh resolve; every click after that reuses
    # the same position directly, skipping the wait entirely (this was adding real, needless
    # latency to a loop that could otherwise run at its full tick-paced cadence).
    refiner_pos = None
    deadline = time.time() + 60.0  # a full inventory of herbs refines in well under this at 1/tick
    while time.time() < deadline:
        held = [
            i for i in controller.get_inventory()
            if any(name.lower() in (i.get("name") or "").lower() for name in all_herb_names)
        ]
        if not held:
            break
        if refiner_pos is None:
            refiner_pos = _click_world_object(ctx, "Refiner", check_ui_blockers=False)
            if refiner_pos is None:
                ctx.state.trigger_safety_stop("no refiner found near the player")
                return
        else:
            controller.click_entity(ctx.ser, refiner_pos, check_ui_blockers=False)
        time.sleep(random.uniform(0.55, 0.75))  # one game tick, matches the refiner's own
                                                  # per-tick processing cadence when clicked

    # Same re-verify idiom as the fixes above: confirm the paste actually landed in the hopper
    # (via /mixology-state) rather than assuming one click succeeded, and retry a bounded number
    # of times against a freshly-refetched Hopper object if it didn't.
    held_paste = [i for i in controller.get_inventory() if i.get("name", "").endswith(" paste")]
    if not held_paste:
        return  # nothing left to deposit -- refining already consumed everything held

    # CORRECTED twice more same session: a fixed per-attempt poll window was still just a guess,
    # and a follow-up camera-yaw-based settle check turned out not to be the right signal either
    # (see _click_world_object's docstring). _click_world_object now gates each Hopper click on
    # controller.wait_until_player_idle (animation/pose/position) before resolving position.
    for _ in range(3):
        hopper_obj = _click_world_object(ctx, "Hopper", check_ui_blockers=False)
        if hopper_obj is None:
            ctx.state.trigger_safety_stop("no hopper found near the player")
            return
        # Give the click a few seconds to resolve without reclicking yet.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            time.sleep(0.3)
            if not any(i.get("name", "").endswith(" paste") for i in controller.get_inventory()):
                return
    debug_log.log("mixology", "hopper deposit click may not have landed -- paste still held after retries")


def _choose_orders(orders: list) -> list:
    """Max-points-per-paste selection: make every non-single-paste order, plus every
    single-paste order too if an MAL order is present in this same set of 3. If ALL 3 are
    single-paste, make only the one whose paste type currently has the lowest hopper stock
    (spends down whichever is SCARCEST, to keep the three roughly balanced over time, since
    single-paste orders are the only lever this routine has for correcting an imbalance;
    skipping the other two loses no points since single-paste orders are net-negative on their
    own per the wiki's reward-ratio table)."""
    has_mal = any(o.get("name") == MAL_BASE for o in orders)
    if has_mal:
        return list(orders)

    non_single = [o for o in orders if o.get("name") not in SINGLE_PASTE_BASES]
    if non_single:
        return non_single

    # All 3 are single-paste -- caller (orders_phase) picks which one via hopper stock, since
    # that needs a live hopper read this pure function shouldn't fetch itself.
    return orders


def orders_phase(ctx, config: dict) -> None:
    data = controller.get_mixology_state()
    orders = data.get("orders", [])
    hopper = data.get("hopper", {})

    if len(orders) < 3:
        # Bridge read came back short (e.g. not actually in the lab, or a fresh spawn not yet
        # populated) -- don't act on a partial order set.
        return

    chosen = _choose_orders(orders)
    if chosen and all(o.get("name") in SINGLE_PASTE_BASES for o in chosen):
        chosen = [min(chosen, key=lambda o: hopper.get(_paste_type_of(o), 0))]

    needed = {"mox": 0, "aga": 0, "lye": 0}
    for order in chosen:
        for paste in ("mox", "aga", "lye"):
            needed[paste] += order.get(paste, 0) * 10  # each lever pull uses 10 paste

    low_water = config.get("hopperLowWater", 150)
    if any(hopper.get(p, 0) - needed[p] < low_water for p in ("mox", "aga", "lye")):
        debug_log.log("mixology", f"hopper would drop below low-water fulfilling {[o.get('name') for o in chosen]} -- rebanking")
        ctx.state.data["mm_phase"] = "bank_herbs"
        return

    ctx.state.data["mm_chosen_orders"] = chosen
    debug_log.log("mixology", f"chosen orders this cycle: {[o.get('name') for o in chosen]}")
    ctx.state.data["mm_phase"] = "mix"


def _paste_type_of(order: dict) -> str:
    """The single paste type a single-paste order (AAA/MMM/LLL) needs -- whichever of
    mox/aga/lye is non-zero on that order dict."""
    return next(p for p in ("mox", "aga", "lye") if order.get(p, 0) > 0)


# Mixing vessel object ids by content, confirmed via direct ObjectID/ObjectID1 jar lookup (see
# this plan's Global Constraints) -- used to confirm the vessel's actual content after pulling
# levers, rather than trusting the pull count alone (see Global Constraints' "always re-read...
# rather than tracking locally").
VESSEL_ID_TO_BASE = {
    # This is the wiki-infobox object-id order, NOT the varbit type-index order RECIPE_TABLE
    # uses -- they genuinely differ (Aqualux amalgam/Azure aura mix and Anti-leech lotion/
    # Megalite liquid are swapped between the two). See this plan's Global Constraints.
    54919: "Mammoth-might mix", 54920: "Mystic mana amalgam", 54921: "Marley's moonlight",
    54922: "Alco-augmentator", 54923: "Aqualux amalgam", 54924: "Azure aura mix",
    54925: "Liplack liquor", 54926: "Anti-leech lotion", 54927: "Megalite liquid",
    54928: "Mixalot",
}

LEVER_NAMES = {"mox": "Mox lever", "aga": "Aga lever", "lye": "Lye lever"}


def mix_phase(ctx, config: dict) -> None:
    chosen = ctx.state.data.get("mm_chosen_orders", [])
    made = ctx.state.data.get("mm_made_bases", [])

    if len(made) >= len(chosen):
        ctx.state.data["mm_made_bases"] = []
        ctx.state.data["mm_phase"] = "process"
        return

    target_order = chosen[len(made)]
    pulls = []
    for paste in ("mox", "aga", "lye"):
        pulls.extend([paste] * target_order.get(paste, 0))

    for paste in pulls:
        lever = _click_world_object(ctx, LEVER_NAMES[paste], check_ui_blockers=False)
        if lever is None:
            ctx.state.trigger_safety_stop(f"no {LEVER_NAMES[paste]} found near the player")
            return
        time.sleep(random.uniform(0.5, 0.7))  # one game tick -- levers resolve per-tick

    vessel = find_object("Mixing vessel")
    if vessel is None:
        ctx.state.trigger_safety_stop("no mixing vessel found near the player")
        return

    # CONFIRMED live 2026-08-12: the vessel's plain "id" field always reports the multiloc's own
    # base/impostor definition id (55395), never the content-variant ids (54918-54928)
    # VESSEL_ID_TO_BASE is keyed by -- ObjectProvider.java resolves the impostor for the object's
    # *name* but historically left "id" as the raw base id. "impostorId" (added alongside this
    # fix) is the impostor-resolved id and is what actually reflects current vessel content.
    actual_base = VESSEL_ID_TO_BASE.get(vessel.get("impostorId"))
    if actual_base != target_order.get("name"):
        # Vessel content doesn't match what this phase just tried to build -- a misclick on one
        # of the lever pulls above is the likely cause (see Global Constraints). Don't take a
        # wrong potion; log and retry the same target_order next tick with the vessel as it
        # actually is now, not what pulls we think we made.
        debug_log.log("mixology", f"vessel shows '{actual_base}', expected '{target_order.get('name')}' -- retrying")
        return

    # Fix 3 (final whole-branch review): the wiki confirms the game can refuse the take
    # (insufficient paste, insufficient Herblore level), and click_entity's own
    # MISTAKE_MISCLICK_CHANCE can also cause a miss -- don't advance mm_made_bases until the
    # base actually lands in inventory, matching golem_crafting.py's reclick-gate idiom for the
    # same class of "click looked fine but didn't land" problem.
    target_id = next(
        (k for k, v in UNFINISHED_IDS.items() if v == target_order.get("name")), None
    )
    # `vessel` above was only fetched to check content (impostorId) -- re-resolve fresh for
    # the actual click, same stale-position reasoning as _click_world_object's own docstring.
    clicked_vessel = _click_world_object(ctx, "Mixing vessel", check_ui_blockers=False)
    if clicked_vessel is None:
        ctx.state.trigger_safety_stop("no mixing vessel found near the player")
        return
    took = False
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if target_id is not None and any(i.get("id") == target_id for i in controller.get_inventory()):
            took = True
            break
        time.sleep(0.3)
    if not took:
        debug_log.log(
            "mixology",
            f"vessel take for '{target_order.get('name')}' didn't land in inventory -- retrying",
        )
        return
    ctx.state.data["mm_made_bases"] = made + [target_order.get("name")]

    # Fix 5 (final whole-branch review): live inventory check instead of the old sticky
    # ctx.state.data["mm_have_digweed"] flag -- digweed withers out of inventory if the player
    # leaves the minigame area (e.g. a bank trip), which would otherwise leave the flag stuck
    # True forever; and find_item() is an exact-name match, which made the old flag-set fragile
    # if the plant's real in-game name isn't precisely "Digweed".
    digweed_item = next(
        (i for i in controller.get_inventory() if i.get("id") == MM_LAB_SPECIAL_HERB_ID), None
    )
    if digweed_item is not None and target_order.get("name") == MAL_BASE:
        base_item = find_item(MAL_BASE)
        if base_item is not None:
            controller.click_entity(ctx.ser, digweed_item, check_ui_blockers=False)
            time.sleep(random.uniform(0.15, 0.3))
            controller.click_entity(ctx.ser, base_item, check_ui_blockers=False)
            time.sleep(random.uniform(0.3, 0.5))


# Confirmed graphics-object ids for each station's speedup click-now cue (see this plan's
# Global Constraints) -- read via the existing /graphics-objects endpoint, no new Java needed.
AGITATOR_CUE_GRAPHICS_ID = 2954
ALEMBIC_CUE_GRAPHICS_ID = 2955

STATION_ORDER = ["Alembic", "Retort", "Agitator"]  # wiki's own recommended pathing order


def process_phase(ctx, config: dict) -> None:
    chosen = ctx.state.data.get("mm_chosen_orders", [])

    # Check if we have any unprocessed bases at all -- id-based (Fix 1, final whole-branch
    # review): processing transforms a base potion in place while keeping its display `name`
    # identical, so a name-in-RECIPE_TABLE test can never distinguish unprocessed from processed
    # and this check could never observe completion. See UNFINISHED_IDS/FINISHED_IDS above.
    current_inventory = controller.get_inventory()
    unprocessed_any = any(i.get("id") in UNFINISHED_IDS for i in current_inventory)
    if not unprocessed_any:
        ctx.state.data["mm_phase"] = "deposit"
        return

    # Process each chosen order in sequence, finding one unprocessed potion per order
    # (not pre-grouped by name, so duplicate-named orders with different stations don't collide).
    # Fix 8 (final whole-branch review): sort by station (STATION_ORDER's wiki-recommended
    # Alembic -> Retort -> Agitator pathing) while still iterating per-order, so the collision
    # fix Task 6 already made doesn't cost the station-grouped pathing optimization too.
    for order in sorted(chosen, key=lambda o: STATION_ORDER.index(o.get("station"))):
        order_name = order.get("name")
        station_name = order.get("station")

        # Find an unprocessed potion matching this order's name -- id-based (Fix 1).
        current_inventory = controller.get_inventory()
        potion = next(
            (i for i in current_inventory if i.get("name") == order_name and i.get("id") in UNFINISHED_IDS),
            None,
        )

        if potion is None:
            continue

        # CONFIRMED live 2026-08-12 (user report: "tried to start a new potion while we still
        # working on one"): with two orders routed to the same station (the duplicate-name
        # scenario Fix 8's per-order iteration is meant to handle), _process_one's completion
        # poll can time out at its 20s deadline without ever detecting the FIRST potion actually
        # finishing, and returns anyway (success and timeout aren't distinguished) -- letting
        # this loop move on and click the station again while it's still actively processing the
        # first potion. /mixology-state's own stations.{name}.progress/potionName (already
        # exposed, no new bridge work needed) is ground truth for whether the station is free;
        # check it before clicking, and skip this order for now if not -- process_phase's own
        # "still unprocessed -- retrying" gate (below) picks it back up next tick once free.
        station_state = controller.get_mixology_state().get("stations", {}).get(station_name.lower(), {})
        if station_state.get("potionName"):
            continue

        station = find_object(station_name)
        if station is None:
            continue

        _process_one(ctx, config, station_name, station, potion)

    # Check if anything unprocessed remains -- if so, retry next tick instead of transitioning.
    # Id-based (Fix 1).
    remaining = [i for i in controller.get_inventory() if i.get("id") in UNFINISHED_IDS]
    if remaining:
        debug_log.log("mixology", f"still unprocessed: {[i.get('name') for i in remaining]} -- retrying")
        return

    ctx.state.data["mm_phase"] = "deposit"


def _process_one(ctx, config: dict, station_name: str, station: dict, potion: dict) -> None:
    # The wiki (data/raw/M/Mastering Mixology.txt:425) warns that left-clicking the machine
    # alone inputs the FIRST unprocessed potion in inventory regardless of which order it
    # fulfills, which is why this used to click the specific potion item first. CONFIRMED live
    # 2026-08-12 (user tested in-game): clicking the station alone is fine as long as orders are
    # processed in a consistent sequence -- which process_phase's own STATION_ORDER sort already
    # guarantees -- so the extra potion-selection click (and its own settle delay) is unneeded.
    _click_world_object(ctx, station_name, check_ui_blockers=False)
    time.sleep(random.uniform(0.4, 0.6))

    speedups = config.get("speedups", DEFAULT_CONFIG["speedups"])
    if station_name == "Retort" and speedups.get("retort"):
        _process_retort_speedup(ctx, station)
    elif station_name == "Agitator" and speedups.get("agitator"):
        _process_agitator_speedup(ctx, station)
    elif station_name == "Alembic" and speedups.get("alembic"):
        _process_alembic_speedup(ctx, station)

    # Fix 2 (final whole-branch review): processing transforms the item in place -- inventory
    # LENGTH never changes (only the item's id changes), so the old "wait for inventory count to
    # change" gate always burned its full timeout. Poll for this specific potion's id
    # transitioning from its UNFINISHED_IDS id to the corresponding FINISHED_IDS id (+10) instead.
    potion_id = potion.get("id")
    finished_id = potion_id + 10 if potion_id is not None else None
    deadline = time.time() + 20.0
    while time.time() < deadline:
        time.sleep(0.3)
        if finished_id is not None and any(i.get("id") == finished_id for i in controller.get_inventory()):
            return


def _process_retort_speedup(ctx, station: dict) -> None:
    """No timing window (confirmed: the reference plugin tracks no varbit/graphics cue for the
    Retort at all, consistent with the wiki's "click every tick" description) -- click every
    tick while /mixology-state still reports a potion loaded in the Retort.

    Same first-click-only idle-wait optimization as _refine_and_hopper_inventory's Refiner loop
    (user direction 2026-08-12): the player is standing at the Retort for this entire loop and
    never moves again once engaged, so only the first click needs to wait/resolve -- every
    click after that reuses the same position directly."""
    retort_pos = None
    deadline = time.time() + 20.0
    while time.time() < deadline:
        state = controller.get_mixology_state()
        if not state.get("stations", {}).get("retort", {}).get("potionName"):
            return
        if retort_pos is None:
            retort_pos = _click_world_object(ctx, "Retort", check_ui_blockers=False)
        else:
            controller.click_entity(ctx.ser, retort_pos, check_ui_blockers=False)
        time.sleep(random.uniform(0.55, 0.75))  # one game tick


def _process_agitator_speedup(ctx, station: dict) -> None:
    """Click again when graphics-object id 2954 (VFX_MACHINERY_ALCHEMY01_AGITATOR01) appears at
    the Agitator's tile -- the visual "fireworks" cue. Bounded wait: per the wiki, a
    consistently-missed window is worse than not attempting the speedup, so this gives up
    cleanly rather than risking a late click against a closed window.

    CONFIRMED live 2026-08-12 (user report on the Alembic's identical logic): the passed-in
    `station` is used only to compare against the graphics cue's tile (world coordinates, stable
    across the wait) -- the actual CLICK re-fetches the station fresh right before it fires,
    since up to 15s can pass before the cue appears and the station's screen position can drift
    in that window."""
    deadline = time.time() + 15.0
    while time.time() < deadline:
        graphics = controller.get_graphics_objects()
        cue = next(
            (g for g in graphics
             if g.get("id") == AGITATOR_CUE_GRAPHICS_ID
             and g.get("x") == station.get("x") and g.get("y") == station.get("y")),
            None,
        )
        if cue is not None:
            _click_world_object(ctx, "Agitator", check_ui_blockers=False)
            return
        time.sleep(0.15)


def _process_alembic_speedup(ctx, station: dict) -> None:
    """Same shape as the Agitator's speedup -- click again when graphics-object id 2955
    (VFX_MACHINERY_ALCHEMY01_ALEMBIC01) appears at the Alembic's tile.

    CONFIRMED live 2026-08-12 (user report: "crystalise potion is bugged, clicking away from
    the table"): the click re-fetches the station fresh right before firing, for the same
    stale-position reason as the Agitator/Retort speedups above."""
    deadline = time.time() + 15.0
    while time.time() < deadline:
        graphics = controller.get_graphics_objects()
        cue = next(
            (g for g in graphics
             if g.get("id") == ALEMBIC_CUE_GRAPHICS_ID
             and g.get("x") == station.get("x") and g.get("y") == station.get("y")),
            None,
        )
        if cue is not None:
            _click_world_object(ctx, "Alembic", check_ui_blockers=False)
            return
        time.sleep(0.15)


def deposit_phase(ctx, config: dict) -> None:
    # CONFIRMED live 2026-08-12 (user report: "it reclicks the conveyor belt even when the item
    # is already gone from our inventory"): this used to click the belt unconditionally every
    # single call, only checking AFTER the click whether anything was actually left to deposit.
    # If this phase gets re-entered with nothing finished held (e.g. deposit already succeeded
    # but the phase transition below hadn't taken effect yet on a stale read), it fired a real,
    # pointless click every time. Check first and skip straight to orders if there's nothing to
    # turn in.
    if not any(i.get("id") in FINISHED_IDS for i in controller.get_inventory()):
        ctx.state.data["mm_phase"] = "orders"
        return

    belt = _click_world_object(ctx, "Conveyor belt", check_ui_blockers=False)
    if belt is None:
        ctx.state.trigger_safety_stop("no conveyor belt found near the player")
        return
    time.sleep(random.uniform(0.5, 0.8))

    # Fix 4 (final whole-branch review): don't transition unconditionally -- confirm the click
    # actually landed by re-reading inventory, same retry-next-tick idiom process_phase's own
    # fix round established.
    remaining = [i for i in controller.get_inventory() if i.get("id") in FINISHED_IDS]
    if remaining:
        debug_log.log(
            "mixology",
            f"still have finished potions after conveyor belt click: {[i.get('name') for i in remaining]} -- retrying",
        )
        return

    ctx.state.data["mm_phase"] = "orders"


DIGWEED_CORNERS = ["northEast", "southEast", "southWest", "northWest"]  # /mixology-state's
                                                                          # digweed dict keys


def maybe_collect_digweed(ctx, config: dict) -> None:
    # Fix 5 (final whole-branch review): live inventory check instead of a sticky
    # ctx.state.data flag -- the wiki confirms digweed withers out of inventory if the player
    # exits the minigame area (e.g. an orders -> bank_herbs low-water trip), which would leave a
    # sticky flag stuck True and digweed collection permanently disabled; and find_item() is an
    # exact-name match, not substring, so a stale flag-set based on it was also fragile. This
    # also brings the file in line with the plan's own Global Constraint of always re-reading
    # live state rather than tracking it locally.
    if any(i.get("id") == MM_LAB_SPECIAL_HERB_ID for i in controller.get_inventory()):
        return  # already holding one -- per the wiki, holding it suppresses new spawns anyway
    if not config.get("digweed", {}).get("enabled", True):
        return

    state = controller.get_mixology_state()
    digweed = state.get("digweed", {})
    if not any(digweed.get(corner) for corner in DIGWEED_CORNERS):
        return

    plant = _click_world_object(ctx, "Digweed", exclude="young", check_ui_blockers=False)
    if plant is None:
        return  # ready per the varbit but not yet visible/already collected by the time we look
    time.sleep(random.uniform(0.5, 0.8))


PHASES = {
    "bank_herbs": bank_herbs_phase,
    "orders": orders_phase,
    "mix": mix_phase,
    "process": process_phase,
    "deposit": deposit_phase,
}


def setup(ctx) -> None:
    ctx.state.data.setdefault("mm_phase", "bank_herbs")


def tick(ctx) -> None:
    config = ctx.state.config
    if not ctx.state.data.get("mm_level_checked"):
        if not check_level_requirement(ctx, config):
            return  # skills not synced yet post-login -- retry next tick, don't mark done
        ctx.state.data["mm_level_checked"] = True
        if ctx.state.safety_stopped:
            return

    maybe_collect_digweed(ctx, config)

    phase = ctx.state.data.get("mm_phase", "bank_herbs")
    handler = PHASES.get(phase)
    if handler is None:
        ctx.state.trigger_safety_stop(f"unknown mastering_mixology phase '{phase}'")
        return
    handler(ctx, config)
