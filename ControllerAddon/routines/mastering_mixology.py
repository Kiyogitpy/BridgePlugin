"""routines/mastering_mixology.py

Automates Mastering Mixology (see docs/superpowers/specs/2026-08-12-mastering-mixology-design.md
for the original plan): bank herbs -> refine -> hopper -> read the 3 active orders -> mix ->
process -> deposit. Always makes and deposits all 3 current orders together every cycle (see
_choose_orders) rather than the original plan's max-points-per-paste selection, which skipped
single-paste orders (AAA/MMM/LLL) without an MAL order present -- changed 2026-08-12 per explicit
user direction, to always capture the wiki-confirmed resin bonus for depositing multiple orders
at once (+20% for 2, +40% for 3).
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
# leaves the minigame area, which would leave a flag-based confirm stuck wrong.
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
    ... We go with animation instead"), this gated on the player's own activity state (animation
    + pose + position) instead of anything screen/camera derived -- directly answering "is my
    character still busy or still moving" rather than inferring it from a proxy. That was
    narrowed further 2026-08-13 (see wait_until_player_idle/_player_activity_snapshot) to local
    position alone -- animation/pose were making every wait pay for an action's full animation
    length even though a stationary action (e.g. a lever pull) never invalidates the screen
    position this is actually protecting. Returns the freshly-resolved object (or None if not
    found/no click attempted) so callers that need its other fields (impostorId, world tile x/y,
    etc.) don't need a second lookup."""
    controller.wait_until_player_idle(timeout_s=timeout_s)
    entity = find_object(name, exclude=exclude)
    if entity is None:
        return None
    controller.click_entity(ctx.ser, entity, **kwargs)
    return entity


def _hover_world_object(ctx, name: str, exclude: str = None, timeout_s: float = 5.0, **kwargs):
    """Same wait-for-idle-then-resolve idiom as _click_world_object, but only rests the cursor
    on the object (controller.hover_entity) without clicking. Added 2026-08-13 (user directive)
    for time-critical station clicks (see _process_retort_speedup/_process_flash_speedup): move
    the mouse onto the target well before the click actually needs to fire, so only a click
    (controller.click_human) -- not a fresh move-then-click -- has to happen in the moment,
    instead of paying mouse-travel latency reactively against a tight window."""
    controller.wait_until_player_idle(timeout_s=timeout_s)
    entity = find_object(name, exclude=exclude)
    if entity is None:
        return None
    controller.hover_entity(ctx.ser, entity, **kwargs)
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
    # CORRECTED 2026-08-13 (user question: "why do we only pick 14 herbs cant we just clean 28
    # at once?"): the old min(14, ...) cap had no stated reason and only ever bit when 1-2 paste
    # types were needed -- with needed_types=1, 28 // 1 = 28 (the whole inventory), but the cap
    # forced it down to 14 anyway, needlessly doubling the number of bank trips for that case.
    # No downside to filling the full inventory split evenly across whatever paste types are
    # actually needed -- every slot here holds a raw herb, nothing else competes for the space
    # during this phase.
    per_type_qty = 28 // len(needed_types)

    bank_cfg = {"teleportFallback": config.get("bank", {}).get("teleportFallback", False)}
    bank_obj = banker.find_nearby_bank(bank_cfg)
    if bank_obj is None:
        ctx.state.trigger_safety_stop("no bank found near Mastering Mixology (and no teleport fallback configured)")
        return
    if not banker.open_bank(ctx, bank_obj):
        ctx.state.trigger_safety_stop("failed to open the bank")
        return

    # CORRECTED 2026-08-13 (user directive: "shouldn't we check do we have any containers? Is
    # our inventory empty? ... instead of just always clicking them"): banker.deposit_inventory
    # unconditionally clicks Deposit-Containers then Deposit-All every single call, even when
    # inventory is already empty or holds no gatherer-bag/container at all -- each click is
    # individually harmless (a no-op when there's nothing to act on, per deposit_inventory's own
    # comment), but a human wouldn't reach for either button without a reason to. Inlined here
    # rather than changing the shared banker.deposit_inventory (other routines call it
    # unconditionally too, and this phase's own keepItems is always empty anyway, so it only
    # ever needs the plain "deposit everything" branch).
    inventory = controller.get_inventory()
    known_container_names = (
        banker.LOOTING_BAG_NAME, banker.HERB_SACK_NAME, banker.SEED_BOX_NAME,
        banker.COAL_BAG_NAME, banker.FUR_POUCH_NAME, "gem bag",
    )
    if any(
        name in (item.get("name") or "").lower()
        for item in inventory
        for name in known_container_names
    ):
        banker.deposit_all_containers(ctx)
        time.sleep(random.uniform(0.3, 0.5))

    if inventory:
        deposit_all = controller.get_bank().get("depositInventory") or {}
        if deposit_all.get("screenX") is not None:
            controller.click_entity(ctx.ser, deposit_all, offset=(0, 0), check_ui_blockers=False)
            time.sleep(random.uniform(0.3, 0.6))

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
    #
    # CORRECTED 2026-08-13 (user directive: "we can just SHIFT left click instead to withdraw
    # all, instead of doing X amount. since 28 is max inventory anyway"): only safe when exactly
    # one paste type is needed this trip. With 2-3 types needed, per_type_qty deliberately caps
    # each below the full 28 to leave room for the others -- shift-click would grab as much of
    # the FIRST type as the bank holds, potentially filling the whole inventory before the
    # second/third type is ever withdrawn. With only one type needed, "withdraw everything up to
    # the 28-slot inventory cap" IS exactly what per_type_qty already computes (28 // 1 = 28), so
    # shift-click (OSRS's own withdraw shortcut, same key/click idiom as
    # golem_crafting.py's walk_to_exact_tile force-walk) gets there in one click instead of a
    # right-click menu + typed amount + Enter. UNVERIFIED live: shift-click's exact withdrawn
    # quantity depends on the account's own bank quantity-mode setting, not a single fixed game
    # rule -- worth confirming the next single-type trip actually withdraws close to 28, not
    # some other amount.
    single_type_trip = len(needed_types) == 1
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

        if single_type_trip:
            controller.key_down(ctx.ser, "SHIFT")
            controller.click_entity(ctx.ser, slot, check_ui_blockers=False)
            controller.key_up(ctx.ser, "SHIFT")

            # CONFIRMED live 2026-08-13 (user report: "it forgot to clean the herbs" -- the
            # shift-click withdrawal itself actually worked fine, 28 Grimy tarromin genuinely
            # landed, but _clean_grimy_herbs ran an eyeblink later and read a stale,
            # pre-withdrawal inventory snapshot showing nothing, returned immediately, and the
            # routine went straight to spam-clicking the Refiner with only grimy -- unrefinable
            # -- herbs held). Every other withdrawal/take in this file confirms landing before
            # moving on; this was the one that didn't. Poll inventory for the herb to actually
            # appear before returning control to the caller.
            # CONFIRMED live 2026-08-13: 3.0s was sometimes too tight -- a case that logged
            # "didn't confirm" here was checked moments later and the herb had genuinely landed
            # (refining had already produced paste from it), the bridge read just hadn't caught
            # up within the original window yet. Widened to 5.0s. Doesn't gate/retry the click
            # either way (this always falls through to close the bank regardless) -- downstream
            # (_clean_grimy_herbs/_refine_and_hopper_inventory finding nothing, hopper not
            # reaching target) already self-corrects on the next bank_herbs_phase cycle if the
            # withdrawal genuinely never landed, so this is diagnostic logging, not a gate.
            name_lower = name.lower()
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if any(name_lower in (i.get("name") or "").lower() for i in controller.get_inventory()):
                    break
                time.sleep(0.2)
            else:
                debug_log.log("mixology", f"shift-click withdrawal of '{name}' didn't confirm landing within 5s")
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
    """Clean every 'Grimy X' herb currently held into its usable form -- CONFIRMED live
    2026-08-12 (user report, and data/raw/M/Mastering Mixology.txt:73: "Grimy herbs are
    typically cheaper... but cannot be used in mixology until cleaned"): the Refiner only
    accepts clean herbs, never grimy ones directly. Cleaning is one click per herb, not a batch
    action (herbs aren't stackable).

    CORRECTED 2026-08-13 (user directive): the old version moved to and precisely clicked one
    herb at a time (find first grimy, click, sleep, repeat) -- treating the inventory like an
    arbitrary set of scattered targets instead of what it actually is: a fixed 4-wide grid a
    human who's done this before already knows the layout of. That human doesn't re-aim for
    each individual herb -- they sweep across a row spam-clicking at a high rate throughout the
    motion (user directive: "around 5-7 clicks per second"), since the grid's own tight slot
    spacing means the click burst lands on SOME herb regardless of precise aim, and move to the
    next row once nothing grimy remains in this one. Groups held grimy herbs by row (slot // 4,
    matching the 4-wide grid) and sweeps each occupied row leftmost-to-rightmost via
    controller.sweep_and_click, which fires the click burst DURING the glide rather than
    stopping to click at discrete stops."""
    deadline = time.time() + 30.0
    while time.time() < deadline:
        grimy = [
            i for i in controller.get_inventory()
            if (i.get("name") or "").lower().startswith("grimy ")
        ]
        if not grimy:
            return

        rows: dict = {}
        for item in grimy:
            rows.setdefault(item.get("slot", 0) // 4, []).append(item)

        for row in sorted(rows):
            row_items = sorted(rows[row], key=lambda i: i.get("slot", 0))
            xs = [i.get("screenX") for i in row_items if i.get("screenX") is not None]
            ys = [i.get("screenY") for i in row_items if i.get("screenY") is not None]
            if not xs or not ys:
                continue
            y = sum(ys) // len(ys)
            start_x, end_x = min(xs), max(xs)

            controller.move_to_human(ctx.ser, start_x, y)
            # min_clicks=len(row_items) -- CORRECTED 2026-08-13 (user report: "we arent
            # clicking fast enough"): a 4-slot row only spans ~260px, which on its own floors
            # the glide at ~250-300ms -- barely 2 clicks even at the fast end of click_rate_hz.
            # This floors the glide's duration at whatever it takes to actually fit one click
            # per herb in the row, so the burst isn't cut short by a distance the row itself is
            # too short to justify.
            controller.sweep_and_click(ctx.ser, end_x, y, click_rate_hz=(5.0, 7.0), min_clicks=len(row_items))

        time.sleep(random.uniform(0.2, 0.4))  # let this pass of clicks actually resolve
    debug_log.log("mixology", "grimy herb cleaning didn't finish within the time budget")


def _refine_and_hopper_inventory(ctx) -> None:
    """Clean any grimy herbs held, then deposit every clean herb / unfinished potion currently
    held into the refiner (clicking every tick to double refine speed, per the wiki), then
    deposit the resulting paste into the hopper. Loops until nothing refinable is left in the
    inventory."""
    _clean_grimy_herbs(ctx)

    all_herb_names = {name for names in DEFAULT_CONFIG["herbPriority"].values() for name in names}
    # CONFIRMED live 2026-08-12: this account's real stock can be the grimy/uncleaned form
    # ("Grimy toadflax"), but exact-set membership only recognized the clean configured names
    # ("Toadflax") -- held was always empty for anything grimy, so this function silently
    # no-opped (never clicked the Refiner, never deposited to the Hopper) every time
    # bank_herbs_phase withdrew a grimy herb, and the very next cycle just deposited it straight
    # back into the bank. Substring match (same fix already applied to the withdrawal-candidate
    # matching above) makes "Grimy toadflax" correctly recognized as a Toadflax-family herb.
    #
    # CORRECTED 2026-08-13 (user report: "using stale data for refiner"): this used to fetch the
    # Refiner's position ONCE then reuse that same cached entity dict for every later click via
    # controller.click_entity, which still re-issues a move_to_human to that cached position
    # every time -- if the camera drifted since the first click (screen coordinates aren't
    # tick-quantized, they drift continuously), every later click was moving to a STALE
    # location. Same fix as the Retort speedup (_process_retort_speedup): rest the mouse on the
    # Refiner ONCE and every following click only fires controller.click_human at wherever the
    # cursor already is -- no further move commands at all, so there's no stale position left to
    # drift away from. Per user directive, the per-click interval is now 0.06-0.25s (down from
    # ~1 game tick) -- faster than the refiner's own per-tick processing cadence, so extra
    # clicks within the same tick are simply harmless no-ops rather than adding speed, but this
    # is what was asked for.
    #
    # CORRECTED again 2026-08-13 (user directive: "we have to account for movement also...
    # Banking -> cleaning herb -> click refiner -> movement -> once at refiner and no movement ->
    # hover and start clicking"): hovering alone doesn't make the player walk anywhere, only a
    # real click does -- coming straight from the bank, the player usually isn't standing at the
    # Refiner yet. This first click_world_object both walks them there (if needed) and performs
    # the first refine click; _hover_world_object's own wait_until_player_idle gate then makes
    # sure that walk has fully settled before resting the mouse there for the click-only loop --
    # hovering mid-walk would just aim at wherever the Refiner appeared on screen before the
    # player finished arriving.
    if _click_world_object(ctx, "Refiner", check_ui_blockers=False) is None:
        ctx.state.trigger_safety_stop("no refiner found near the player")
        return

    if _hover_world_object(ctx, "Refiner", check_ui_blockers=False) is None:
        return

    deadline = time.time() + 60.0  # a full inventory of herbs refines in well under this at 1/tick
    while time.time() < deadline:
        held = [
            i for i in controller.get_inventory()
            if any(name.lower() in (i.get("name") or "").lower() for name in all_herb_names)
        ]
        if not held:
            break
        controller.click_human(ctx.ser)
        time.sleep(random.uniform(0.06, 0.25))

    # Same re-verify idiom as the fixes above: confirm the paste actually landed in the hopper
    # (via /mixology-state) rather than assuming one click succeeded, and retry a bounded number
    # of times against a freshly-refetched Hopper object if it didn't.
    held_paste = [i for i in controller.get_inventory() if i.get("name", "").endswith(" paste")]
    if not held_paste:
        return  # nothing left to deposit -- refining already consumed everything held

    # CORRECTED twice more same session: a fixed per-attempt poll window was still just a guess,
    # and a follow-up camera-yaw-based settle check turned out not to be the right signal either
    # (see _click_world_object's docstring). _click_world_object now gates each Hopper click on
    # controller.wait_until_player_idle (local position only, see that function's docstring)
    # before resolving position.
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
    """Always make and deposit all 3 current orders together.

    CHANGED 2026-08-12 (explicit user direction: "we need to do all 3 orders at once"): the
    wiki (data/raw/M/Mastering Mixology.txt:472,474) confirms the conveyor belt deposits up to
    three potions in a single click, fulfilling as many current orders as possible and
    refreshing all three together -- and fulfilling 2 at once gives 20% more resin per potion,
    3 at once gives 40% more. The original plan's max-points-per-paste strategy deliberately
    skipped single-paste orders (AAA/MMM/LLL) without an MAL order present, reasoning they're
    net-negative in isolation per the wiki's reward-ratio table -- correct in isolation, but it
    meant most cycles only ever chose 1-2 orders, never capturing the simultaneous-deposit
    bonus at all. User's call: always take all 3, unconditionally.

    CORRECTED 2026-08-13 (user directive: "i made a mistake with the prioritising thing...
    rather than prioritising we just save it until there is a LLL in our order"): no longer
    sorts by Lye content. Simpler than trying to maximize digweed's chance of landing on
    whichever order happens to be best available at an unpredictable moment -- see
    _apply_digweed_if_held, which now just holds digweed until Liplack liquor specifically
    shows up as one of the chosen orders, instead of settling for a lesser Lye-containing one."""
    return list(orders)


def orders_phase(ctx, config: dict) -> None:
    # CONFIRMED live 2026-08-13 (user report: chosen orders were Liplack liquor/Mammoth-might
    # mix/Mystic mana amalgam, but mix_phase made Mammoth-might mix FIRST and process_phase then
    # sent potions to the wrong stations for the rest of the cycle): a stale, unprocessed
    # Liplack liquor -- left over from before this routine even started this cycle (a prior,
    # interrupted session) -- was already sitting in inventory when the very first mix_phase call
    # ran. mix_phase's and process_phase's positional logic both rely on "the first unprocessed
    # potion in the player's inventory" corresponding to whatever index they expect (confirmed by
    # the wiki: "If the machine is left-clicked, the first unprocessed potion in the player's
    # inventory will be input to the machine" -- see process_phase's own docstring) -- a
    # contaminating leftover silently shifts what "leftmost" actually is without either phase
    # ever knowing, routing genuine potions to the wrong station for the rest of the cycle with
    # no error. Flush any leftover potions (deposit_phase now actively processes a stray
    # unfinished one instead of just waiting, see its own docstring) BEFORE ever choosing new
    # orders, so a fresh cycle always starts from a verified-clean inventory instead of trusting
    # it already is one.
    leftover = [
        i for i in controller.get_inventory()
        if i.get("id") in UNFINISHED_IDS or i.get("id") in FINISHED_IDS
    ]
    if leftover:
        debug_log.log(
            "mixology",
            f"leftover potions in inventory before choosing new orders: "
            f"{[i.get('name') for i in leftover]} -- flushing via deposit first",
        )
        ctx.state.data["mm_phase"] = "deposit"
        return

    data = controller.get_mixology_state()
    orders = data.get("orders", [])
    hopper = data.get("hopper", {})

    if len(orders) < 3:
        # Bridge read came back short (e.g. not actually in the lab, or a fresh spawn not yet
        # populated) -- don't act on a partial order set.
        return

    chosen = _choose_orders(orders)

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

LEVER_PULL_MAX_ATTEMPTS = 3


def _pull_lever_confirmed(ctx, paste: str) -> bool:
    """Click the lever for `paste` (mox/aga/lye) and confirm, via controller.get_last_action
    (/last-action, see LastActionProvider.java), that the click actually resolved to THAT
    lever -- not just that click_entity aimed at it and returned an object dict.

    Added 2026-08-13 (user directive): with three levers standing close together, a click can
    land cleanly on the WRONG one -- a different failure mode from click_entity's own
    simulated-miss-and-reclick, which only corrects an aim that missed its own intended target,
    not one that successfully hit a neighboring object instead. Before this, the only signal for
    a wrong pull was the mixing vessel's own content check AFTER all 3 pulls, which then retries
    the whole recipe from scratch (see mix_phase's `actual_base != target_order.get('name')`
    branch). Retrying just the mismatched pull here, immediately, is cheaper and catches it
    sooner -- the vessel-content check stays in place as a backstop either way."""
    lever_name = LEVER_NAMES[paste]
    for attempt in range(LEVER_PULL_MAX_ATTEMPTS):
        baseline_tick = controller.get_last_action().get("tick")
        lever = _click_world_object(ctx, lever_name, check_ui_blockers=False)
        if lever is None:
            return False  # object not found near the player -- retrying won't help
        time.sleep(random.uniform(0.5, 0.7))  # one game tick -- levers resolve per-tick

        action = controller.get_last_action()
        target = (action.get("target") or "").lower()
        confirmed = (
            action.get("tick") is not None
            and (baseline_tick is None or action.get("tick") > baseline_tick)
            and lever_name.lower() in target
        )
        if confirmed:
            return True
        debug_log.log(
            "mixology",
            f"lever pull for '{lever_name}' didn't confirm (last-action target="
            f"'{action.get('target')}') -- retry {attempt + 1}/{LEVER_PULL_MAX_ATTEMPTS}",
        )
    return False


def mix_phase(ctx, config: dict) -> None:
    chosen = ctx.state.data.get("mm_chosen_orders", [])

    # CONFIRMED live 2026-08-13 (user directive, after a live report of two Liplack liquors
    # ending up in inventory): re-derive how many bases are already made from LIVE inventory
    # instead of trusting a separately-tracked counter -- same reconciliation process_phase
    # already uses (see its own docstring) for the same reason: ctx.state.data can desync from
    # what's actually held (a restart, a misclick, or whatever produced the duplicate), and
    # blindly trusting a stale count risks re-making (or skipping) the wrong order. Counting
    # actual potion items already in inventory catches that before ever pulling a lever for what
    # should be the next one, instead of just moving on to the next order on faith.
    already_made = sum(
        1 for i in controller.get_inventory()
        if i.get("id") in UNFINISHED_IDS or i.get("id") in FINISHED_IDS
    )
    if already_made >= len(chosen):
        ctx.state.data["mm_phase"] = "process"
        return

    target_order = chosen[already_made]
    pulls = []
    for paste in ("mox", "aga", "lye"):
        pulls.extend([paste] * target_order.get(paste, 0))

    for paste in pulls:
        if not _pull_lever_confirmed(ctx, paste):
            ctx.state.trigger_safety_stop(
                f"{LEVER_NAMES[paste]} not found near the player, or repeated "
                f"unconfirmed/wrong-lever pulls"
            )
            return

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
    # MISTAKE_MISCLICK_CHANCE can also cause a miss -- don't treat this base as made until it
    # actually lands in inventory, matching golem_crafting.py's reclick-gate idiom for the same
    # class of "click looked fine but didn't land" problem.
    target_id = next(
        (k for k, v in UNFINISHED_IDS.items() if v == target_order.get("name")), None
    )
    # `vessel` above was only fetched to check content (impostorId) -- re-resolve fresh for
    # the actual click, same stale-position reasoning as _click_world_object's own docstring.
    baseline_tick = controller.get_last_chat_message().get("tick")
    clicked_vessel = _click_world_object(ctx, "Mixing vessel", check_ui_blockers=False)
    if clicked_vessel is None:
        ctx.state.trigger_safety_stop("no mixing vessel found near the player")
        return

    # CONFIRMED live 2026-08-13: taking a base from the vessel prints "You collect some X from
    # the mixing vessel." -- wait for that directly (see controller.wait_for_chat_message)
    # instead of inferring the take purely from a poll loop, then still confirm the id actually
    # landed in inventory rather than trusting the message alone.
    order_name_lower = (target_order.get("name") or "").lower()
    confirmed = controller.wait_for_chat_message(
        lambda m: (m.get("message") or "").lower().startswith("you collect some")
        and order_name_lower in (m.get("message") or "").lower()
        and "from the mixing vessel" in (m.get("message") or "").lower(),
        since_tick=baseline_tick,
        timeout_s=3.0,
    )
    took = confirmed is not None and target_id is not None and any(
        i.get("id") == target_id for i in controller.get_inventory()
    )
    if not took:
        debug_log.log(
            "mixology",
            f"vessel take for '{target_order.get('name')}' didn't land in inventory -- retrying",
        )
        return

    # Digweed application, if any is currently held, is handled independently of taking a base
    # -- see _apply_digweed_if_held (called every tick from maybe_collect_digweed), not here.
    # CORRECTED 2026-08-13 (live bug, user report: LLL made first per the Lye-priority sort in
    # _choose_orders, but digweed -- harvested one tick AFTER LLL was already taken -- landed on
    # the NEXT base (Marley's moonlight) instead, since this used to only ever apply digweed to
    # whatever base THIS call just took. The sort was working correctly; the bug was that
    # application was tied to the take instant instead of to "the best base currently held,
    # whenever digweed happens to become available" -- see _apply_digweed_if_held's own
    # docstring for the fix.


# Confirmed graphics-object ids for each station's speedup click-now cue (see this plan's
# Global Constraints) -- read via the existing /graphics-objects endpoint, no new Java needed.
AGITATOR_CUE_GRAPHICS_ID = 2954
ALEMBIC_CUE_GRAPHICS_ID = 2955


def process_phase(ctx, config: dict) -> None:
    chosen = ctx.state.data.get("mm_chosen_orders", [])

    # CONFIRMED live 2026-08-13 (user directive, then a live bug report): clicking a station
    # directly processes the LEFTMOST unprocessed potion in inventory automatically -- there is
    # no need to select/click the potion item first. The old "select potion, then click station"
    # idiom (and this file's later attempt to pick the right item by slot/name for that select
    # click) risked a misclick landing on an adjacent, already-FINISHED item and using THAT
    # instead -- CONFIRMED live: "it just used a finished product on the table". Removing the
    # select click removes that failure mode entirely, and removes the need to identify a
    # specific item at all.
    #
    # Because selection is positional, not by name/id, orders must be processed in the exact
    # left-to-right order their bases were taken in (chosen[0] first, chosen[1] second, ... --
    # see mix_phase's own `chosen[already_made]` indexing), NOT resorted by the wiki's own
    # recommended Alembic -> Retort -> Agitator pathing -- clicking a station out of that order
    # would process the wrong potion for it. The order currently up for processing is always
    # chosen[len(chosen) - unprocessed_count]: the one immediately after however many have
    # already been finished off the front, computed fresh from live inventory state each loop
    # iteration instead of separately persisted bookkeeping.
    while True:
        # id-based (Fix 1, final whole-branch review): processing transforms a base potion in
        # place while keeping its display `name` identical, so a name-in-RECIPE_TABLE test can
        # never distinguish unprocessed from processed. See UNFINISHED_IDS/FINISHED_IDS above.
        unprocessed_count = sum(
            1 for i in controller.get_inventory() if i.get("id") in UNFINISHED_IDS
        )
        if unprocessed_count == 0:
            ctx.state.data["mm_phase"] = "deposit"
            return

        order_index = len(chosen) - unprocessed_count
        if not (0 <= order_index < len(chosen)):
            debug_log.log(
                "mixology",
                f"unexpected: {unprocessed_count} unprocessed potions but "
                f"{len(chosen)} chosen orders -- retrying",
            )
            return

        order = chosen[order_index]
        station_name = order.get("station")

        # CONFIRMED live 2026-08-12 (user report: "tried to start a new potion while we still
        # working on one"): _process_one's completion wait can still time out without the
        # potion actually being done (e.g. a missed chat message) -- /mixology-state's own
        # stations.{name}.progress/potionName is ground truth for whether the station is free;
        # check it before clicking, and stop for now if not -- the top of this loop (called
        # again next tick via process_phase's own re-entry) picks it back up once free.
        station_state = controller.get_mixology_state().get("stations", {}).get(station_name.lower(), {})
        if station_state.get("potionName"):
            debug_log.log("mixology", f"{station_name} still busy -- retrying")
            return

        station = find_object(station_name)
        if station is None:
            return

        _process_one(ctx, config, station_name, station, order.get("name"))


def _process_one(ctx, config: dict, station_name: str, station: dict, order_name: str) -> None:
    baseline_tick = controller.get_last_chat_message().get("tick")

    # Click the processing station directly -- no potion-selection click first (see
    # process_phase's own comment: the station auto-processes the leftmost unprocessed potion,
    # and a select click first only risked misclicking an adjacent, already-finished item).
    _click_world_object(ctx, station_name, check_ui_blockers=False)
    time.sleep(random.uniform(0.4, 0.6))

    speedups = config.get("speedups", DEFAULT_CONFIG["speedups"])
    if station_name == "Retort" and speedups.get("retort"):
        _process_retort_speedup(ctx, station, baseline_tick, order_name)
    elif station_name == "Agitator" and speedups.get("agitator"):
        _process_agitator_speedup(ctx, station)
    elif station_name == "Alembic" and speedups.get("alembic"):
        _process_alembic_speedup(ctx, station)

    unfinished_id = next((k for k, v in UNFINISHED_IDS.items() if v == order_name), None)
    finished_id = unfinished_id + 10 if unfinished_id is not None else None

    # CONFIRMED live 2026-08-13 (Retort: "You finish concentrating the X."): the game announces
    # exactly when processing finishes -- wait for that (see controller.wait_for_chat_message)
    # instead of relying only on a fixed poll cadence. Matched on the generic "you finish ...
    # <name>" prefix, not a per-station verb -- Agitator's/Alembic's own wording (Homogenise/
    # Crystalise per the design doc) hasn't been confirmed live the same way, and this doesn't
    # need to know it. Still falls through to the id-based inventory poll below either way,
    # since that id is what process_phase's own completion check actually depends on.
    # CORRECTED 2026-08-13 (live-debugging session): 30s still repeatedly wasn't enough -- and
    # not just for Retort; Agitator hit the identical repeated-timeout pattern, which points at
    # THIS wait specifically (the one shared fallback every station goes through) rather than
    # anything Retort-speedup-specific. Every single timeout observed this session still
    # self-recovered (confirmed live via /mixology-state and /inventory each time -- no
    # corruption, no stuck station, just genuinely-still-processing), so the fix is a longer
    # wait, not different logic. The wiki's own reward table shows per-potion Herblore
    # level/XP scaling from 190 XP (level 60) up to 365 XP (level 81) -- consistent with
    # processing time varying a lot by potion tier, on top of this account's own Herblore level
    # (the wiki separately confirms lower levels take longer). Widened generously rather than
    # guessing a tighter number from limited samples.
    order_name_lower = (order_name or "").lower()
    controller.wait_for_chat_message(
        lambda m: (m.get("message") or "").lower().startswith("you finish")
        and order_name_lower in (m.get("message") or "").lower(),
        since_tick=baseline_tick,
        timeout_s=90.0,
    )

    deadline = time.time() + 3.0
    while time.time() < deadline:
        if finished_id is not None and any(i.get("id") == finished_id for i in controller.get_inventory()):
            return
        time.sleep(0.3)


def _process_retort_speedup(ctx, station: dict, baseline_tick, order_name: str) -> None:
    """No timing window (confirmed: the reference plugin tracks no varbit/graphics cue for the
    Retort at all, consistent with the wiki's "click every tick" description) -- click every
    tick while /mixology-state still reports a potion loaded in the Retort.

    CONFIRMED live 2026-08-13 (user directive): waits for the game's own "You start
    concentrating the X..." confirmation (controller.wait_for_chat_message) before doing
    anything else, so this never starts clicking before processing has actually begun. Then
    rests the mouse on the Retort once (_hover_world_object) and every following tick just
    clicks in place (controller.click_human) instead of re-issuing a full move-then-click each
    time -- the player never moves again once engaged here, so a fresh move has nothing to
    correct for, only latency to add.

    CONFIRMED live 2026-08-13 (user report: "we made the MMA with the retort instead of the
    Agi[tator]"): an extra click landing just AFTER the potion actually finishes hits an EMPTY
    Retort, which auto-starts processing the NEXT unprocessed potion in inventory (see
    process_phase's own positional design) at the Retort regardless of whether that's really its
    intended station -- silently routing the wrong potion through the wrong modifier, with no
    way to undo it after the fact. A first attempt gated each click on BOTH the "You finish
    concentrating..." chat message and /mixology-state's potionName -- CONFIRMED live that still
    wasn't enough (same bug recurred).

    Live-observed 2026-08-13 (direct /mixology-state polling during a real Retort cycle,
    user-triggered): stations.retort.progress climbs strictly by 1 roughly once per game tick
    (1, 2, 3, ... 21 observed for a Mammoth-might mix) and then, on the very next read, resets to
    0 with potionName gone entirely -- both changing in the SAME instant. There's no fixed
    "done" number to hardcode (the wiki confirms total ticks varies by potion and Herblore
    level), but the monotonic-until-the-exact-end shape means "did progress fail to advance
    since the last time I looked" is itself a race-free-enough proxy for "this might be about to
    finish" -- self-calibrating for any potion/level, no threshold needed. Clicking is now gated
    on progress having strictly increased since the last observed value; a stalled read (same
    value twice -- our poll cadence can occasionally outrun the tick rate) or a reset both
    withhold the next click rather than risk it, while a still-climbing value keeps clicking
    every tick same as before. Kept as a THIRD, independent check alongside the chat-message and
    potionName-empty checks from the first attempt, not a replacement for them."""
    order_name_lower = (order_name or "").lower()
    start_msg = controller.wait_for_chat_message(
        lambda m: (m.get("message") or "").lower().startswith("you start")
        and order_name_lower in (m.get("message") or "").lower(),
        since_tick=baseline_tick,
        timeout_s=5.0,
    )
    # Baseline for the "you finish" check below -- must be at least the start message's own
    # tick (falling back to baseline_tick if that wait timed out), otherwise a STALE "you
    # finish" from an earlier order sharing this same name (duplicate-named orders are routed
    # through here one at a time, see _choose_orders/process_phase) would match immediately and
    # skip clicking entirely for this one.
    last_seen_tick = start_msg.get("tick") if start_msg is not None else baseline_tick

    retort_pos = _hover_world_object(ctx, "Retort", check_ui_blockers=False)
    if retort_pos is None:
        return

    # CONFIRMED live 2026-08-13 (live-debugging session): Mystic mana amalgam repeatedly needed
    # well over 20s to finish concentrating at the Retort -- the wiki confirms processing time
    # scales with both the specific potion's own tier and the player's Herblore level (Mystic
    # mana amalgam is level 63, a higher tier than e.g. Mammoth-might mix's level 60, which
    # finished comfortably inside 20s in the same session), so a single fixed deadline that
    # works for a low-tier potion can still be too short for a higher-tier one on the same
    # account. Not a correctness bug either way -- process_phase's own station-busy check
    # already prevents a wrong-station click regardless of how long this waits -- but hitting
    # the deadline here meant _process_one's own separate "you finish" wait (see below) almost
    # always had to redundantly wait it out a second time. Widened so this loop itself is more
    # likely to catch the finish directly.
    #
    # WIDENED again 2026-08-13 (live-debugging session): 45s still wasn't enough for some
    # potions (see _process_one's own "you finish" wait, widened alongside this one, for the
    # full reasoning) -- matched to the same 90s.
    last_progress = -1
    deadline = time.time() + 90.0
    while time.time() < deadline:
        msg = controller.get_last_chat_message()
        msg_tick = msg.get("tick")
        finished_via_chat = (
            msg_tick is not None
            and (last_seen_tick is None or msg_tick > last_seen_tick)
            and (msg.get("message") or "").lower().startswith("you finish")
            and order_name_lower in (msg.get("message") or "").lower()
        )
        if finished_via_chat:
            return

        retort_state = controller.get_mixology_state().get("stations", {}).get("retort", {})
        if not retort_state.get("potionName"):
            return

        progress = retort_state.get("progress", 0)
        if progress > last_progress:
            controller.click_human(ctx.ser)
            last_progress = progress
        # else: progress hasn't advanced since we last saw it -- skip clicking this iteration
        # (see docstring) and just re-poll next loop instead.
        time.sleep(random.uniform(0.55, 0.75))  # one game tick

    current_potion = controller.get_mixology_state().get("stations", {}).get("retort", {}).get("potionName")
    if current_potion and current_potion != order_name:
        ctx.state.trigger_safety_stop(
            f"Retort speedup: expected '{order_name}' but station now shows "
            f"'{current_potion}' -- a stray click likely started the wrong potion"
        )


# Deliberately human -- see _process_flash_speedup's docstring for why this exists and why it
# never applies twice in a row.
SPEEDUP_MISS_CHANCE = 0.12

# Of a missed first flash, how often the miss is a mistimed late click (reflexively clicking
# just as the flash fades, too late to land) rather than no click attempt at all -- see
# _process_flash_speedup's docstring.
SPEEDUP_LATE_CLICK_CHANCE = 0.5


def _process_flash_speedup(ctx, station_name: str, cue_graphics_id: int, station: dict,
                            poll_interval_s: float, fresh: bool, deadline_s: float = 15.0) -> None:
    """Shared Agitator/Alembic speedup logic: click when graphics-object `cue_graphics_id`
    appears at the station's own world tile (the visual "fireworks"/flash cue). Bounded wait:
    per the wiki, a consistently-missed window is worse than not attempting the speedup, so this
    gives up cleanly rather than risking a late click against a closed window.

    CONFIRMED live 2026-08-13 (user directive): rests the mouse on the station BEFORE the cue
    even appears (_hover_world_object) instead of moving there reactively once it does -- the
    old behavior paid mouse-travel latency against a tight window; resting on the target first
    means only a click (controller.click_human, no further movement) has to fire in time.

    CONFIRMED live 2026-08-13 (user directive): the game grants a second flash if the first is
    missed, so this deliberately misses the first one some of the time (SPEEDUP_MISS_CHANCE,
    the same kind of human-reaction-time realism click_entity's own MISTAKE_MISCLICK_CHANCE
    provides elsewhere) but never misses the second -- a human who's already anxious about
    having missed once pays full attention to the retry. "Missing" here means letting that flash
    pass without clicking (or, per user directive, SPEEDUP_LATE_CLICK_CHANCE of the time,
    reflexively clicking a beat too late as the flash fades -- a human doesn't always just freeze
    when they miss a timed cue, they sometimes click anyway just after it's gone), then waiting
    for the cue to clear and reappear before treating the next appearance as the retry -- an
    unbroken `cue present` streak across polls is still the SAME flash, not a second one."""
    if _hover_world_object(ctx, station_name, check_ui_blockers=False) is None:
        return

    station_x, station_y = station.get("x"), station.get("y")

    def cue_present() -> bool:
        return any(
            g.get("id") == cue_graphics_id and g.get("x") == station_x and g.get("y") == station_y
            for g in controller.get_graphics_objects(fresh=fresh)
        )

    deadline = time.time() + deadline_s
    missed_first = False
    late_click_pending = False
    cue_cleared_since_miss = False
    while time.time() < deadline:
        present = cue_present()
        if not missed_first:
            if present:
                if random.random() < SPEEDUP_MISS_CHANCE:
                    late_click_pending = random.random() < SPEEDUP_LATE_CLICK_CHANCE
                    debug_log.log(
                        "mixology",
                        f"{station_name} speedup -- deliberately missing first flash"
                        + (" (late click)" if late_click_pending else ""),
                    )
                    missed_first = True
                else:
                    controller.click_human(ctx.ser)
                    return
        elif not present:
            if not cue_cleared_since_miss:
                cue_cleared_since_miss = True
                if late_click_pending:
                    controller.click_human(ctx.ser)  # mistimed -- fires just after the flash is gone
        elif cue_cleared_since_miss:
            controller.click_human(ctx.ser)
            return
        time.sleep(poll_interval_s)


def _process_agitator_speedup(ctx, station: dict) -> None:
    """Agitator's own graphics-object id (2954, VFX_MACHINERY_ALCHEMY01_AGITATOR01) flash cue --
    see _process_flash_speedup for the shared hover-then-click-in-place mechanics and the
    deliberate-miss-once/guaranteed-retry logic."""
    _process_flash_speedup(ctx, "Agitator", AGITATOR_CUE_GRAPHICS_ID, station, poll_interval_s=0.15, fresh=False)


def _process_alembic_speedup(ctx, station: dict) -> None:
    """Alembic's own graphics-object id (2955) flash cue -- see _process_flash_speedup for the
    shared hover-then-click-in-place mechanics and the deliberate-miss-once/guaranteed-retry
    logic."""
    _process_flash_speedup(ctx, "Alembic", ALEMBIC_CUE_GRAPHICS_ID, station, poll_interval_s=0.03, fresh=True)


def deposit_phase(ctx, config: dict) -> None:
    inventory = controller.get_inventory()

    # Wait until ALL processing has finished before depositing anything -- the only genuine
    # "not ready yet" signal, re-checked fresh every call. In NORMAL operation this phase is only
    # ever entered after process_phase's own "nothing unprocessed remains" check, so `unfinished`
    # should always be empty here -- but orders_phase can also route here directly to flush a
    # stray leftover potion that predates the current cycle entirely (not part of
    # mm_chosen_orders, see orders_phase's own docstring). Nothing else in the routine is
    # actively targeting an orphaned item like that, so passively waiting for it to finish would
    # wait forever -- CONFIRMED live 2026-08-13 this exact scenario caused a stale, unprocessed
    # Liplack liquor to silently desync every position-based index for a whole cycle. Which
    # station it goes through doesn't matter -- it isn't fulfilling any real order either way,
    # just needs to become FINISHED so it can be deposited and cleared out.
    unfinished = [i for i in inventory if i.get("id") in UNFINISHED_IDS]
    if unfinished:
        item = unfinished[0]
        station = find_object("Retort")
        if station is None:
            debug_log.log(
                "mixology",
                f"waiting for processing to finish (no Retort found to flush it): "
                f"{[i.get('name') for i in unfinished]}",
            )
            return
        debug_log.log("mixology", f"flushing stray unprocessed potion '{item.get('name')}' via Retort")
        baseline_tick = controller.get_last_chat_message().get("tick")
        _click_world_object(ctx, "Retort", check_ui_blockers=False)
        item_name_lower = (item.get("name") or "").lower()
        # CORRECTED 2026-08-13 (live-debugging session, see _process_one's own comment): widened
        # again for the same reason -- some potions genuinely take much longer than 45s to
        # concentrate, even un-accelerated, depending on tier and Herblore level.
        controller.wait_for_chat_message(
            lambda m: (m.get("message") or "").lower().startswith("you finish")
            and item_name_lower in (m.get("message") or "").lower(),
            since_tick=baseline_tick,
            timeout_s=90.0,
        )
        return

    # Nothing left to deposit.
    finished = [i for i in inventory if i.get("id") in FINISHED_IDS]
    if not finished:
        ctx.state.data["mm_phase"] = "orders"
        return

    # CORRECTED 2026-08-13 (live bug): this used to gate on `len(potion_items) >= 3` (total
    # unfinished+finished) before ever attempting a click, reasoning a potion might still be
    # mid-processing. CONFIRMED live that reasoning doesn't hold once we're actually here:
    # deposit_phase is only ever entered after process_phase's own "nothing unprocessed
    # remains" check, and `unfinished` above re-confirms it fresh -- nothing is still being
    # made. The bug this caused: a belt click that only partially matched current orders (e.g.
    # one leftover potion from an earlier/different order set) left that potion behind, and the
    # old gate then waited forever for a 3rd potion that was never coming, since nothing left in
    # this cycle could produce one -- "waiting for all 3 potions to be present: 1/3" repeating
    # every tick indefinitely. Per the wiki (data/raw/M/Mastering Mixology.txt:472), a belt
    # click is always safe and always makes progress on whatever's actually held -- it fulfils
    # as many current orders as it can match, or deposits the first potion for reduced reward if
    # none match -- so there's no reason to withhold a click just because fewer than 3 are
    # present.
    debug_log.log("mixology", f"Deposit phase entered with {[i.get('name') for i in finished]}")

    belt = _click_world_object(
        ctx,
        "Conveyor belt",
        check_ui_blockers=False
    )

    if belt is None:
        ctx.state.trigger_safety_stop("no conveyor belt found near the player")
        return

    debug_log.log("mixology", "Clicked conveyor")

    # Wait for the deposit to complete before retrying.
    deadline = time.time() + 5.0
    remaining = finished

    while time.time() < deadline:
        time.sleep(0.2)

        remaining = [
            i for i in controller.get_inventory()
            if i.get("id") in FINISHED_IDS
        ]

        if not remaining:
            ctx.state.data["mm_phase"] = "orders"
            return

    debug_log.log(
        "mixology",
        f"still have finished potions after conveyor belt click: "
        f"{[i.get('name') for i in remaining]} -- retrying",
    )

DIGWEED_CORNERS = ["northEast", "southEast", "southWest", "northWest"]  # /mixology-state's
                                                                          # digweed dict keys


def _apply_digweed_if_held(ctx) -> bool:
    """If holding a Digweed item AND currently holding an unprocessed Liplack liquor, use the
    digweed on it. Returns True if already holding digweed (whether Liplack liquor was found
    and used, or it's just waiting for one to show up -- per the wiki digweed only withers on
    leaving the lab, not on being held a while), False if not holding any at all.

    Added 2026-08-13 (live bug, user report): decoupled from mix_phase's own vessel-take step,
    where this logic used to live -- applying digweed only to whatever base THAT specific call
    just took meant a base made in an EARLIER mix_phase call (already sitting in inventory,
    unprocessed) was invisible to it. Checking every tick against currently-held unprocessed
    bases (not just the newest one) fixes that: whichever tick digweed becomes available, it can
    still reach a base that's already been sitting there.

    CORRECTED 2026-08-13 (user directive: "i made a mistake with the prioritising thing... we
    just save it until there is a LLL in our order"): a first attempt tried to maximize digweed's
    value by sorting chosen orders for Lye-priority and applying to whichever held base had the
    highest Lye content -- simpler to just hold the digweed until Liplack liquor (the single
    best non-Mixalot target, per the wiki's own "L > M > A" point-priority note) is actually one
    of the bases held, and use it then, rather than settling for a lesser Lye-containing
    substitute whenever the ideal one isn't available yet."""
    digweed_item = next(
        (i for i in controller.get_inventory() if i.get("id") == MM_LAB_SPECIAL_HERB_ID), None
    )
    if digweed_item is None:
        return False

    liplack_id = next((k for k, v in UNFINISHED_IDS.items() if v == "Liplack liquor"), None)
    best = next(
        (i for i in controller.get_inventory() if i.get("id") == liplack_id), None
    )
    if best is None:
        return True  # holding digweed, no Liplack liquor held yet -- wait for one

    debug_log.log("mixology", f"applying held digweed to '{best.get('name')}'")
    controller.click_entity(ctx.ser, digweed_item, check_ui_blockers=False)
    time.sleep(random.uniform(0.15, 0.3))
    controller.click_entity(ctx.ser, best, check_ui_blockers=False)
    time.sleep(random.uniform(0.3, 0.5))

    # CONFIRMED live 2026-08-13 (user report: ended up with two Liplack liquors in inventory
    # after a digweed application): this used to return True right after firing the clicks,
    # with nothing confirming the "use digweed on X" actually landed -- every OTHER action in
    # this file (vessel takes, lever pulls, station finishes) confirms before moving on; this
    # was the one step that didn't. Verify the digweed item is actually gone (consumed) before
    # reporting success -- if it's still held, leave it for the next tick's call to reassess
    # from a fresh inventory read (candidates re-evaluated from scratch, same as always) rather
    # than silently trusting this attempt worked.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not any(i.get("id") == MM_LAB_SPECIAL_HERB_ID for i in controller.get_inventory()):
            return True
        time.sleep(0.3)
    debug_log.log(
        "mixology",
        f"digweed application on '{best.get('name')}' didn't confirm (still held) -- retrying next tick",
    )
    return True


def maybe_collect_digweed(ctx, config: dict) -> None:
    # Fix 5 (final whole-branch review): live inventory check instead of a sticky
    # ctx.state.data flag -- the wiki confirms digweed withers out of inventory if the player
    # exits the minigame area (e.g. an orders -> bank_herbs low-water trip), which would leave a
    # sticky flag stuck True and digweed collection permanently disabled. This also brings the
    # file in line with the plan's own Global Constraint of always re-reading live state rather
    # than tracking it locally.
    if _apply_digweed_if_held(ctx):
        return  # already holding one -- per the wiki, holding it suppresses new spawns anyway,
                 # and _apply_digweed_if_held just used it (or is waiting for something to use it on)
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
