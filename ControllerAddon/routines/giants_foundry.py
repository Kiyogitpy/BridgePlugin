# routines/giants_foundry.py
#
# Giants' Foundry: run sword commissions unattended -- commission from Kovac, best moulds
# for the commission, fill the crucible from the Foundry bank chest, pour, pick up, refine
# (hammer/grind/polish at the right temperature, claiming sweet spots), hand in, repeat.
#
# Design: docs/superpowers/specs/2026-08-31-giants-foundry-design.md
# Measured facts: ControllerAddon/recordings/2026-08-31_003238_giants-foundry.*
#
# Run with:
#     python controller.py routines/giants_foundry.py
#
# Ground truth at runtime is GET /api/minigames/giants-foundry (the vendored m8aq reader):
# phase, commission, crucible varbits, mould selections, tool sequence, temperature band,
# sweet-spot state. Nothing load-bearing lives in ctx.state.data -- every phase re-derives
# from live varbits, so restarts mid-sword resume correctly (STORED preforms included).

import math
import time

import banker
import controller
import debug_log

DEFAULT_CONFIG = {
    # Value-aware crucible fill plan (the recording's own loadout): items usable on the
    # crucible, the metal they melt into, bars per item (wiki: equipment = smithing bar
    # cost minus 1), and how many bars of that metal the crucible should end up holding.
    "fill": [
        {"item": "Mithril platebody", "metal": "MITHRIL", "bars_each": 4, "target_bars": 14},
        {"item": "Adamant platelegs", "metal": "ADAMANT", "bars_each": 2, "target_bars": 14},
    ],
    # Item quantization can overflow the 28-bar cap (MEASURED: 4-bar platebody into 2 bars
    # of space -> confirm dialog, 2 bars lost). Accept the confirm iff the loss is <= this.
    "max_overflow_loss": 2,

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
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # No Controller Settings panel section (gemstone_crab pattern) -- config lives here.
    return DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Pure logic: mould selection
# ---------------------------------------------------------------------------

# Per-mould commission-descriptor scores, ported verbatim from the easy-giantsfoundry
# Plugin Hub plugin's Mould enum (commit 9324c7f, read 2026-08-31 via the osrs-wiki MCP)
# -- the same numbers the game's own mould interface displays. Enum names match OUR
# vendored reader's Mould enum (m8aq GiantsFoundry.java), which is what selectedMoulds/
# usableMoulds serialize; display names are what the Select menu entry carries.
# Tuple: (display name, part, {descriptor: score}, purchasable).
# VALIDATED against the recording: argmax reproduces the human's Narrow Flat picks exactly.
MOULDS = {
    # RICASSO (forte)
    "CHOPPER_FORTE":          ("Chopper Forte", "RICASSO", {"BROAD": 4, "LIGHT": 4, "FLAT": 4}, False),
    "GLADIUS_RICASSO":        ("Gladius Ricasso", "RICASSO", {"BROAD": 4, "HEAVY": 4, "FLAT": 4}, False),
    "DISARMING_FORTE":        ("Disarming Forte", "RICASSO", {"NARROW": 4, "LIGHT": 4, "SPIKED": 4}, False),
    "MEDUSA_RICASSO":         ("Medusa Ricasso", "RICASSO", {"BROAD": 8, "HEAVY": 6, "FLAT": 8}, False),
    "SERPENT_RICASSO":        ("Serpent Ricasso", "RICASSO", {"NARROW": 6, "LIGHT": 8, "FLAT": 8}, False),
    "SERRATED_FORTE":         ("Serrated Forte", "RICASSO", {"NARROW": 8, "HEAVY": 8, "SPIKED": 6}, False),
    "STILETTO_FORTE":         ("Stiletto Forte", "RICASSO", {"NARROW": 8, "LIGHT": 10, "FLAT": 8}, True),
    "DEFENDER_BASE":          ("Defender Base", "RICASSO", {"BROAD": 8, "HEAVY": 10, "FLAT": 8}, True),
    "JUGGERNAUT_FORTE":       ("Juggernaut Forte", "RICASSO", {"BROAD": 4, "HEAVY": 4, "SPIKED": 16}, True),
    "CHOPPER_FORTE_PLUS_ONE": ("Chopper Forte +1", "RICASSO", {"BROAD": 3, "LIGHT": 4, "FLAT": 18}, True),
    "SPIKER":                 ("Spiker!", "RICASSO", {"NARROW": 1, "HEAVY": 2, "SPIKED": 22}, True),
    # BLADE
    "SAW_BLADE":       ("Saw Blade", "BLADE", {"BROAD": 4, "LIGHT": 4, "SPIKED": 4}, False),
    "DEFENDERS_EDGE":  ("Defenders Edge", "BLADE", {"BROAD": 4, "HEAVY": 4, "SPIKED": 4}, False),
    "FISH_BLADE":      ("Fish Blade", "BLADE", {"NARROW": 4, "LIGHT": 4, "FLAT": 4}, False),
    "MEDUSA_BLADE":    ("Medusa Blade", "BLADE", {"BROAD": 8, "HEAVY": 8, "FLAT": 6}, False),
    "STILETTO_BLADE":  ("Stiletto Blade", "BLADE", {"NARROW": 8, "LIGHT": 6, "FLAT": 8}, False),
    "GLADIUS_EDGE":    ("Gladius Edge", "BLADE", {"NARROW": 6, "HEAVY": 8, "FLAT": 8}, False),
    "FLAMBERGE_BLADE": ("Flamberge Blade", "BLADE", {"NARROW": 8, "LIGHT": 8, "SPIKED": 10}, True),
    "SERPENT_BLADE":   ("Serpent Blade", "BLADE", {"NARROW": 10, "LIGHT": 8, "FLAT": 8}, True),
    "CLAYMORE_BLADE":  ("Claymore Blade", "BLADE", {"BROAD": 16, "HEAVY": 4, "FLAT": 4}, True),
    "FLEUR_DE_BLADE":  ("Fleur de Blade", "BLADE", {"BROAD": 4, "HEAVY": 18, "SPIKED": 1}, True),
    "CHOPPA":          ("Choppa!", "BLADE", {"BROAD": 1, "LIGHT": 22, "FLAT": 2}, True),
    # TIP
    "PEOPLE_POKER_POINT": ("People Poker Point", "TIP", {"NARROW": 4, "HEAVY": 4, "FLAT": 4}, False),
    "CHOPPER_TIP":        ("Chopper Tip", "TIP", {"BROAD": 4, "LIGHT": 4, "SPIKED": 4}, False),
    "MEDUSAS_HEAD":       ("Medusa's Head", "TIP", {"BROAD": 4, "HEAVY": 4, "SPIKED": 4}, False),
    "SERPENTS_FANG":      ("Serpent's Fang", "TIP", {"NARROW": 8, "LIGHT": 6, "SPIKED": 8}, False),
    "GLADIUS_POINT":      ("Gladius Point", "TIP", {"NARROW": 8, "HEAVY": 8, "FLAT": 6}, False),
    "SAW_TIP":            ("Saw Tip", "TIP", {"BROAD": 6, "HEAVY": 8, "SPIKED": 8}, False),
    "CORRUPTED_POINT":    ("Corrupted Point", "TIP", {"NARROW": 8, "LIGHT": 10, "SPIKED": 8}, True),
    "DEFENDERS_TIP":      ("Defenders Tip", "TIP", {"BROAD": 10, "HEAVY": 8, "SPIKED": 8}, True),
    "SERRATED_TIP":       ("Serrated Tip", "TIP", {"NARROW": 4, "LIGHT": 16, "SPIKED": 4}, True),
    "NEEDLE_POINT":       ("Needle Point", "TIP", {"NARROW": 18, "LIGHT": 3, "FLAT": 4}, True),
    "THE_POINT":          ("The Point!", "TIP", {"BROAD": 2, "LIGHT": 1, "FLAT": 22}, True),
}

MOULD_BY_DISPLAY = {display.lower(): enum for enum, (display, _, _, _) in MOULDS.items()}

MOULD_PARTS = ("RICASSO", "BLADE", "TIP")


def mould_score(enum_name, commission):
    scores = MOULDS[enum_name][2]
    return sum(scores.get(word, 0) for word in commission)


def best_moulds(commission, usable):
    """Argmax mould per part for this commission (list of descriptor names like
    ["NARROW","FLAT"]). `usable` is the API's usableMoulds (unlocks x Smithing level);
    an EMPTY list means "cannot tell" (un-rebuilt client) and falls back to the full
    catalogue rather than refusing to work. Ties keep table order (stable max via >),
    which matches both the wiki's listing and the recorded human choice."""
    pool = set(usable) if usable else set(MOULDS)
    best = {}
    for enum_name in MOULDS:
        part = MOULDS[enum_name][1]
        if enum_name not in pool:
            continue
        s = mould_score(enum_name, commission)
        if part not in best or s > best[part][0]:
            best[part] = (s, enum_name)
    return {part: pick[1] for part, pick in best.items()}


# ---------------------------------------------------------------------------
# Pure logic: crucible fill planning
# ---------------------------------------------------------------------------

CRUCIBLE_CAPACITY = 28


def plan_fill(fill_cfg, crucible_contents, max_overflow_loss):
    """What still needs adding, from the LIVE crucible varbits (crucibleContents) --
    re-derived every tick, no local estimate (the fur-pouch lesson). Returns
    (steps, error): steps ordered largest bars_each first so the 28-bar cap (and any
    quantization loss) lands on the last, cheapest item; ([], None) means the crucible
    is satisfied; ([], "reason") is a config/stock problem worth a safety stop.
    Item counts quantize UP (ceil) -- the game caps the final item at remaining space
    and asks to confirm the loss (MEASURED: 2 bars lost on a 4-bar platebody into 2
    bars of space); the loss is accepted at execution time iff <= max_overflow_loss."""
    contents = crucible_contents or {}
    space = CRUCIBLE_CAPACITY - sum(contents.values())
    if space <= 0:
        return [], None

    steps = []
    planned_bars = 0
    for entry in fill_cfg:
        remaining = entry["target_bars"] - contents.get(entry["metal"], 0)
        if remaining <= 0:
            continue
        count = math.ceil(remaining / entry["bars_each"])
        steps.append({"item": entry["item"], "bars_each": entry["bars_each"], "count": count})
        planned_bars += count * entry["bars_each"]

    if not steps:
        return [], None
    if planned_bars < space:
        total = CRUCIBLE_CAPACITY - space + planned_bars
        return [], (f"fill plan only reaches {total}/{CRUCIBLE_CAPACITY} bars -- "
                    "raise the fill config's target_bars")
    loss = planned_bars - space
    if loss > max_overflow_loss:
        return [], (f"fill plan would overflow {loss} bars "
                    f"(> max_overflow_loss {max_overflow_loss}) -- adjust the fill config")
    steps.sort(key=lambda s: -s["bars_each"])
    return steps, None


# ---------------------------------------------------------------------------
# Pure logic: refinement heat targeting
# ---------------------------------------------------------------------------

# Per-machine constants ported from easy-giantsfoundry's Stage.java and CONFIRMED in the
# recording: the trip hammer acts every 5 game ticks for +20 progress / -25 heat; the
# grindstone and polishing wheel act every 2 ticks for +10 progress and +15 / -17 heat.
# Passive decay is -1 heat per 2 ticks. `object` is the machine's game-object id.
TOOL_STATS = {
    "TRIP_HAMMER":     {"cycle_ticks": 5, "progress": 20, "heat": -25, "object": 44619},
    "GRINDSTONE":      {"cycle_ticks": 2, "progress": 10, "heat": 15, "object": 44620},
    "POLISHING_WHEEL": {"cycle_ticks": 2, "progress": 10, "heat": -17, "object": 44621},
}

# Stop the fast heat/cool this far short of the computed target: the Dunk/Quench ramp
# runs +-27..33 per tick at the point we ride it (easy-giantsfoundry's measured DX_1
# curve from index 10; CONFIRMED in the recording, 724->751->780->813), so one poll of
# latency costs about one fast tick.
HEAT_STOP_MARGIN = 35


def actions_left_in_section(completion, section_count, tool):
    """How many actions of `tool` until the next section boundary (progress is on the
    0-1000 scale, sections are equal slices)."""
    if not section_count:
        return 0
    per = 1000.0 / section_count
    till_next = per - (completion % per)
    if till_next <= 0:
        till_next = per
    return max(1, math.ceil(till_next / TOOL_STATS[tool]["progress"]))


def heat_stop_target(tool, min_t, max_t, actions_left):
    """The band edge to ENTER at so the whole remaining section can run without another
    lava/waterfall trip (easy-giantsfoundry's targeting): cooling tools (hammer, polish)
    enter near the top and ride down; the grindstone (heats) enters near the bottom.
    Clamped into the band -- overshooting the far edge would damage the sword."""
    delta = TOOL_STATS[tool]["heat"]
    if delta < 0:
        target = min_t - delta * actions_left
    else:
        target = max_t - delta * actions_left
    return max(min_t, min(max_t, target))


# ---------------------------------------------------------------------------
# Task 9: refinement controller constants
# ---------------------------------------------------------------------------

WORKING_ACTIVITIES = ("HAMMERING", "GRINDING", "POLISHING")
WORK_STALL_S = 12.0        # > two hammer cycles (3s each) + the nulled first cycle
WORK_FIRST_ACTION_S = 6.0
DEAD_CLICK_LIMIT = 5
HEAT_ADJUST_TIMEOUT_S = 20.0
HEAT_POLL_S = 0.25


# ---------------------------------------------------------------------------
# Runtime: mould setup interface (group 718 -- MEASURED in the recording)
# ---------------------------------------------------------------------------

MOULD_GROUP = 718
MOULD_SET_CHILD = 6      # "Set Mould" button (recorder: widget 718.6)
MOULD_LIST_CHILD = 9     # scroll list of mould rows (recorder: "Select <name>" on 718.9)
MOULD_TAB_CHILD = 12     # part tabs (recorder: "View Fortes/Blades/Tips" on 718.12)
MOULD_TAB_SLOTS = {"RICASSO": 0, "BLADE": 9, "TIP": 18}
MOULD_MAX_OPENS = 3


def _strip_tags(text):
    out, depth = [], 0
    for ch in text or "":
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out).strip()


def _open_mould_setup(ctx):
    data = ctx.state.data
    if data.get("gf_mould_opens", 0) >= MOULD_MAX_OPENS:
        ctx.state.trigger_safety_stop(
            "giants foundry: mould setup never completed (interface not opening, or a "
            "planned mould row never selectable)")
        return
    data["gf_mould_opens"] = data.get("gf_mould_opens", 0) + 1   # reset when mouldSetupOpen reads True
    jig = _find_object(MOULD_JIG_ID)
    if jig is None:
        debug_log.log("gf", "mould jig not visible -- waiting")
        return
    controller.click_entity(ctx.ser, jig, refresh_target=_refresh(jig))
    controller.mark_busy(4.0)
    controller.human_delay(1.0, 1.6)


def _click_widget(ctx, group, child, index=None):
    w = controller.get_widget(group, child, index=index)
    if not w or not w.get("screenX"):
        return False
    return bool(controller.click_entity(
        ctx.ser, w, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False))


def _select_moulds_in_ui(ctx, st, plan):
    """One part per call (the varbit confirms between calls): find the first part whose
    live selection differs from the plan, switch to its tab, and click the best USABLE
    row that is actually on screen -- if the planned row isn't rendered (scrolled off),
    the best visible one is taken instead of scrolling, logged. When every part matches,
    click Set Mould."""
    selected = st.get("selectedMoulds") or {}
    commission = st.get("commissionTypes") or []
    usable = set(st.get("usableMoulds") or [])
    for part in MOULD_PARTS:
        want = plan.get(part)
        if want is None or selected.get(part) == want:
            continue
        _click_widget(ctx, MOULD_GROUP, MOULD_TAB_CHILD, index=MOULD_TAB_SLOTS[part])
        controller.human_delay(0.4, 0.7)
        rows = controller.get_widget_children(MOULD_GROUP, MOULD_LIST_CHILD) or []
        best = None
        for row in rows:
            enum = MOULD_BY_DISPLAY.get(_strip_tags(row.get("text")).lower())
            if enum is None or MOULDS[enum][1] != part:
                continue
            if usable and enum not in usable:
                continue
            s = mould_score(enum, commission)
            if best is None or s > best[0]:
                best = (s, enum, row)
        if best is None:
            debug_log.log("gf", f"no selectable mould row for {part} -- re-opening")
            return
        if best[1] != want:
            debug_log.log("gf", f"planned {want} not on screen for {part} -- "
                                f"taking best visible {best[1]}")
        controller.click_entity(ctx.ser, best[2], offset=(0, 0),
                                check_ui_blockers=False, simulate_mistakes=False)
        controller.human_delay(0.5, 0.9)
        return
    # every part satisfied -> confirm
    if _click_widget(ctx, MOULD_GROUP, MOULD_SET_CHILD):
        controller.human_delay(0.6, 1.0)


# ---------------------------------------------------------------------------
# Runtime: state access + dispatch
# ---------------------------------------------------------------------------

KOVAC_NPC_ID = 11472
BANK_CHEST_ID = 44630
LAVA_POOL_ID = 44631
WATERFALL_ID = 44632
STORAGE_EMPTY_ID = 44633    # "Preform storage", op Store-preform (cache config/loc)
STORAGE_FULL_ID = 44634     # "Preform storage", op Take-preform
CRUCIBLE_ID = 44776
MOULD_JIG_ID = 44777

PICKUP_MAX_ATTEMPTS = 3
EQUIP_GROUP = 387     # worn-equipment interface (recorder-decoded)
WEAPON_CHILD = 18
SHIELD_CHILD = 20

# A read that fails this many consecutive ticks means the endpoint genuinely is not
# there (un-rebuilt client / bridge down), not a blip.
STATE_UNAVAILABLE_LIMIT = 20
COMMISSION_MAX_ATTEMPTS = 4
HANDIN_MAX_ATTEMPTS = 6
FILL_MAX_ATTEMPTS = 3
FILL_DIALOG_TIMEOUT_S = 4.0


def read_state():
    """One fresh Giants' Foundry snapshot, or None for 'cannot tell' (endpoint missing,
    bridge down, or the reader itself erroring). None must always defer, never act."""
    st = controller.get_api("minigames", "giants-foundry")
    if not st or st.get("error"):
        return None
    return st


def _reset_counters(data, st, phase):
    # Counters reset where success is DETECTED (read back from varbits), never where the
    # attempt was made -- the mahogany_homes contact-cast lesson: a reset that lives in a
    # phase handler dies the moment the dispatcher stops routing there.
    if st.get("commissionTypes"):
        data["gf_commission_attempts"] = 0
    if phase not in ("READY_TO_HAND_IN", "RUINED"):
        data["gf_handin_attempts"] = 0
    if phase not in ("PREFORM_READY", "COLLECTING_PREFORM"):
        data["gf_pickup_attempts"] = 0
    bars = st.get("crucibleBarCount", 0)
    if bars > data.get("gf_last_bars", -1):
        data["gf_fill_attempts"] = 0
    data["gf_last_bars"] = bars
    if st.get("mouldSetupOpen"):
        data["gf_mould_opens"] = 0


def tick(ctx):
    data = ctx.state.data
    st = read_state()
    if st is None:
        data["gf_no_state"] = data.get("gf_no_state", 0) + 1
        if data["gf_no_state"] >= STATE_UNAVAILABLE_LIMIT:
            ctx.state.trigger_safety_stop(
                "giants foundry: /api/minigames/giants-foundry unavailable -- "
                "rebuild + restart the client?")
        return
    data["gf_no_state"] = 0

    phase = st.get("phase")
    if phase == "INACTIVE":
        ctx.state.trigger_safety_stop(
            "giants foundry: not at the Foundry -- start inside the minigame area")
        return

    _reset_counters(data, st, phase)

    handler = PHASE_HANDLERS.get(phase)
    if handler is None:
        debug_log.log("gf", f"unhandled phase {phase} -- waiting")
        return
    handler(ctx, st)


# Stubs -- replaced by Tasks 5-9. Defined so PHASE_HANDLERS can bind now.
def get_commission_phase(ctx, st):
    # Right-click Commission on Kovac -- MEASURED: the commission varbits land ~0.1s
    # after the click, before the confirmation dialogue is even dismissed. The counter
    # resets in _reset_counters the moment commissionTypes reads non-empty.
    data = ctx.state.data
    d = _dialogue()
    if _dialogue_open(d):
        controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.4, 0.8)
        return
    if data.get("gf_commission_attempts", 0) >= COMMISSION_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(
            "giants foundry: commission never registered after repeated asks")
        return
    kovac = _find_kovac()
    if kovac is None:
        debug_log.log("gf", "Kovac not visible -- waiting")
        return
    data["gf_commission_attempts"] = data.get("gf_commission_attempts", 0) + 1
    controller.click_menu_action(
        ctx.ser, kovac, "Commission",
        refresh_target=controller.npc_index_refresh_target(kovac.get("index")))
    controller.human_delay(0.8, 1.2)


def prepare_phase(ctx, st):
    plan = best_moulds(st.get("commissionTypes") or [], st.get("usableMoulds") or [])
    selected = st.get("selectedMoulds") or {}
    if st.get("mouldSetupOpen"):
        _select_moulds_in_ui(ctx, st, plan)
        return
    # A part-mismatch against the plan only forces a re-open while the mould is not yet
    # SET (phase PREPARING/SELECTING_MOULDS); once workflowState says MOULD_SET we take
    # the selection as final -- re-opening a set mould mid-fill would loop.
    if st.get("phase") != "MOULD_SET" and (
            len(selected) < 3 or any(selected.get(p) != plan.get(p) for p in plan)):
        _open_mould_setup(ctx)
        return
    if st.get("crucibleBarCount", 0) < CRUCIBLE_CAPACITY:
        _fill_crucible(ctx, st)
        return
    _pour(ctx, st)


def _fill_crucible(ctx, st):
    cfg = ctx.state.config
    data = ctx.state.data
    steps, err = plan_fill(cfg["fill"], st.get("crucibleContents") or {},
                           cfg["max_overflow_loss"])
    if err:
        ctx.state.trigger_safety_stop(f"giants foundry: {err}")
        return
    if not steps:
        return    # satisfied -- prepare_phase pours on the next tick's read

    step = steps[0]
    inv = controller.get_inventory() or {}
    held = [i for i in (inv.get("items") or [])
            if (i.get("name") or "").lower() == step["item"].lower()]
    if not held:
        _restock_from_bank(ctx, steps)
        return

    if data.get("gf_fill_attempts", 0) >= FILL_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(
            "giants foundry: crucible fill not progressing (crucible bar count never grew)")
        return
    crucible = _find_object(CRUCIBLE_ID)
    if crucible is None:
        return
    data["gf_fill_attempts"] = data.get("gf_fill_attempts", 0) + 1   # reset when bars grow
    # Explicit Use via the menu -- a wearable's LEFT-click default is Wear/Wield, so a
    # bare click on platelegs would equip them instead of selecting them.
    if not controller.click_menu_action(ctx.ser, held[0], "Use",
                                        offset=(0, 0), check_ui_blockers=False):
        return
    controller.human_delay(0.2, 0.4)
    if not controller.click_entity(ctx.ser, crucible, refresh_target=_refresh(crucible)):
        return
    controller.mark_busy(FILL_DIALOG_TIMEOUT_S + 4)
    _answer_fill_dialogs(ctx, min(step["count"], len(held)), accept_overflow=True)


def _pour(ctx, st):
    crucible = _find_object(CRUCIBLE_ID)
    if crucible is None:
        return
    controller.click_entity(ctx.ser, crucible, refresh_target=_refresh(crucible),
                            expected_action="Pour")
    controller.mark_busy(4.0)
    controller.human_delay(1.0, 1.6)


def _restock_from_bank(ctx, steps):
    """Withdraw everything the remaining plan needs in one trip. plan_fill already
    vetoed any overflow beyond config, so whatever we withdraw here is intended."""
    bank_obj = banker.find_nearby_bank({"maxBankDistance": 25})
    if bank_obj is None:
        debug_log.log("gf", "bank chest not visible -- waiting")
        return
    if not banker.open_bank(ctx, bank_obj):
        return
    banker.deposit_all_inventory(ctx)
    need = {}
    for s in steps:
        need[s["item"]] = need.get(s["item"], 0) + s["count"]
    banker.withdraw_loadout(
        ctx, {"withdraw": [{"name": item, "quantity": n} for item, n in need.items()]},
        fresh=True)
    inv = controller.get_inventory() or {}
    names = [(i.get("name") or "").lower() for i in (inv.get("items") or [])]
    for item in need:
        if item.lower() not in names:
            banker.close_bank(ctx)
            ctx.state.trigger_safety_stop(f"giants foundry: bank is out of {item}")
            return
    banker.close_bank(ctx)


def _answer_fill_dialogs(ctx, count, accept_overflow):
    """Ride the add-to-crucible dialogs: a quantity prompt (option keys; 'X' takes a
    typed amount -- MEASURED), then possibly the overflow confirm ('Are you sure?').
    Exact widget/option layout gets pinned on the first live session; matching is by
    option text so a layout shuffle degrades to the '1' fallback, not a wrong click.
    Iteration-bounded (not wall-clock) so the hermetic tests' no-op sleep exits
    instantly: 16 polls x HEAT_POLL_S-sized sleeps ~ FILL_DIALOG_TIMEOUT_S live."""
    answered_quantity = False
    for _ in range(int(FILL_DIALOG_TIMEOUT_S / 0.25)):
        d = _dialogue()
        opts = _dialogue_options(d)
        if opts and not answered_quantity and not _is_confirm(opts):
            picked = False
            for i, text in enumerate(opts):
                if text.strip() == str(count):
                    controller.key_tap(ctx.ser, str(i + 1))
                    picked = True
                    break
            if not picked:
                for i, text in enumerate(opts):
                    if text.strip().lower() in ("x", "other"):
                        controller.key_tap(ctx.ser, str(i + 1))
                        controller.human_delay(0.6, 0.9)
                        controller.type_text(ctx.ser, str(count))
                        picked = True
                        break
            if not picked:
                controller.key_tap(ctx.ser, "1")
            answered_quantity = True
            controller.human_delay(0.4, 0.7)
            continue
        if opts and _is_confirm(opts):
            want = ("yes",) if accept_overflow else ("no",)
            _choose_option(ctx, d, want, fallback_key=None)
            return
        time.sleep(0.25)


def _is_confirm(opts):
    low = " ".join(o.lower() for o in opts)
    return "yes" in low or "no" in low


def pickup_phase(ctx, st):
    # Pick the preform off the jig. Needs ice/smithing gloves (setup checks) AND both
    # hand slots empty -- MEASURED: an off-hand Imcando hammer blocks it with the
    # "free your hands" chat, which on_event flags for the recovery below.
    data = ctx.state.data
    if data.get("gf_need_free_hands"):
        _free_hands(ctx)
        data["gf_need_free_hands"] = False
        return
    if data.get("gf_pickup_attempts", 0) >= PICKUP_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(
            "giants foundry: could not pick up the preform (hands still full?)")
        return
    jig = _find_object(MOULD_JIG_ID)
    if jig is None:
        return
    data["gf_pickup_attempts"] = data.get("gf_pickup_attempts", 0) + 1
    controller.click_entity(ctx.ser, jig, refresh_target=_refresh(jig),
                            expected_action="Pick-up")
    controller.mark_busy(4.0)
    controller.human_delay(1.2, 1.8)


def _free_hands(ctx):
    # E opens worn equipment; Remove on both hand slots (387.18 weapon, 387.20 shield --
    # recorder-decoded). An empty slot's menu has no Remove entry, so click_menu_action
    # returns False harmlessly there.
    controller.key_tap(ctx.ser, "E")
    controller.human_delay(0.5, 0.9)
    for child in (WEAPON_CHILD, SHIELD_CHILD):
        w = controller.get_widget(EQUIP_GROUP, child)
        if w and w.get("screenX"):
            controller.click_menu_action(ctx.ser, w, "Remove",
                                         offset=(0, 0), check_ui_blockers=False)
            controller.human_delay(0.4, 0.7)
    controller.key_tap(ctx.ser, "ESCAPE")


def on_event(ctx, event_type, payload):
    if event_type != "CHAT_MESSAGE":
        return
    msg = ((payload or {}).get("message") or "")
    if "free your hands" in msg:
        ctx.state.data["gf_need_free_hands"] = True
    elif "overwork the preform" in msg:
        # Informational: the overwork guard should make this rare; count it so a log
        # shows how often it still happens.
        ctx.state.data["gf_overworked"] = ctx.state.data.get("gf_overworked", 0) + 1
        debug_log.log("gf", f"overworked the preform "
                            f"(x{ctx.state.data['gf_overworked']} this session)")


def refine_phase(ctx, st):
    """One decision per tick: sweet spot > (working? watch, don't touch) > in band?
    engage machine > out of band? fast-adjust and engage. The overwork rule (MEASURED:
    clicking a machine mid-action forfeits the next action) is why 'working' returns
    without clicking anything except a sweet-spot claim."""
    data = ctx.state.data

    if st.get("preformState") == "STORED":
        storage = _find_object(STORAGE_FULL_ID)
        if storage is not None:
            controller.click_entity(ctx.ser, storage, refresh_target=_refresh(storage),
                                    expected_action="Take-preform")
            controller.mark_busy(3.0)
            controller.human_delay(1.0, 1.5)
        return

    tool = st.get("requiredTool")
    if tool not in TOOL_STATS:
        return    # boundary tick / completed read -- dispatcher resolves next tick

    # Sweet spot: exactly one claim click per AVAILABLE window, and only while the
    # temperature is right -- claiming out of band damages the sword instead.
    if st.get("sweetSpotState") == "AVAILABLE":
        if st.get("temperatureStatus") == "READY" and not data.get("gf_sweet_claimed"):
            machine = _find_object(TOOL_STATS[tool]["object"])
            if machine is not None:
                controller.click_entity(ctx.ser, machine, refresh_target=_refresh(machine),
                                        simulate_mistakes=False)
                data["gf_sweet_claimed"] = True
                return
    else:
        data["gf_sweet_claimed"] = False

    working = st.get("activity") in WORKING_ACTIVITIES
    status = st.get("temperatureStatus")
    if working:
        if status in ("TOO_COLD", "TOO_HOT"):
            # THE QUALITY GUARD: a running machine keeps acting while the temperature
            # is wrong, and every such action costs 10 quality. Interrupt it by going
            # straight to the heat adjust -- clicking the lava/waterfall interrupts the
            # work exactly like any other click, so this is one action, not two.
            _adjust_heat_and_engage(ctx, st, tool)
            return
        _watch_progress(ctx, st)
        return

    if status == "READY":
        _click_machine(ctx, st, tool)
    elif status in ("TOO_COLD", "TOO_HOT"):
        _adjust_heat_and_engage(ctx, st, tool)


def _watch_progress(ctx, st):
    """While the machine runs, only watch. Progress advancing clears the dead-click
    counter; WORK_STALL_S with no progress while 'working' means the activity read is
    stale or the action never started -- re-engage once per stall window."""
    data = ctx.state.data
    comp = st.get("completion", 0)
    mark = data.get("gf_progress_mark")
    now = time.time()
    if mark is None or comp > mark[0]:
        data["gf_progress_mark"] = (comp, now)
        data["gf_dead_clicks"] = 0
        return
    if now - mark[1] > WORK_STALL_S:
        debug_log.log("gf", f"no progress for {WORK_STALL_S:.0f}s while working -- re-engaging")
        data["gf_progress_mark"] = (comp, now)
        _click_machine(ctx, st, st.get("requiredTool"))


def _click_machine(ctx, st, tool):
    data = ctx.state.data
    if tool not in TOOL_STATS:
        return False
    if data.get("gf_dead_clicks", 0) >= DEAD_CLICK_LIMIT:
        ctx.state.trigger_safety_stop(
            "giants foundry: machine clicks produce no progress -- aim or state problem")
        return False
    machine = _find_object(TOOL_STATS[tool]["object"])
    if machine is None:
        return False
    data["gf_dead_clicks"] = data.get("gf_dead_clicks", 0) + 1   # cleared on progress
    ok = controller.click_entity(ctx.ser, machine, refresh_target=_refresh(machine))
    if ok:
        data["gf_progress_mark"] = (st.get("completion", 0), time.time())
        controller.mark_busy(WORK_FIRST_ACTION_S)
    return bool(ok)


def _adjust_heat_and_engage(ctx, st, tool):
    """Fast heat/cool (the human uses ONLY Dunk/Quench -- MEASURED), polled every
    HEAT_POLL_S against the live temperature varbit, stopped HEAT_STOP_MARGIN short of
    the band-end target -- and the stop IS the machine click (MEASURED: clicking the
    next machine interrupts the dunk; there is no separate walk-away click)."""
    min_t = st.get("minimumTemperature", -1)
    max_t = st.get("maximumTemperature", -1)
    if min_t < 0 or max_t < 0:
        return
    heating = st.get("temperatureStatus") == "TOO_COLD"
    actions = actions_left_in_section(st.get("completion", 0), st.get("sectionCount", 0), tool)
    target = heat_stop_target(tool, min_t, max_t, actions)
    pool = _find_object(LAVA_POOL_ID if heating else WATERFALL_ID)
    if pool is None:
        return
    option = "Dunk-preform" if heating else "Quench-preform"
    if not controller.click_menu_action(ctx.ser, pool, option,
                                        refresh_target=_refresh(pool)):
        return
    controller.mark_busy(HEAT_ADJUST_TIMEOUT_S + 2)
    deadline = time.time() + HEAT_ADJUST_TIMEOUT_S
    cur = None
    while time.time() < deadline:
        cur = read_state()
        if cur is not None:
            t = cur.get("temperature", -1)
            if t >= 0:
                if heating and t >= target - HEAT_STOP_MARGIN:
                    break
                if not heating and t <= target + HEAT_STOP_MARGIN:
                    break
            if cur.get("requiredTool") != tool:
                break    # section moved under us -- re-decide from the top next tick
        time.sleep(HEAT_POLL_S)
    _click_machine(ctx, cur or st, tool)


def hand_in_phase(ctx, st):
    # Right-click Hand-in on Kovac, then ride the reward dialogue through to the offer
    # of a NEW commission (MEASURED: Space x2, option key 1, Space). A RUINED sword is
    # handed in identically -- that is the game's own recovery. Success is detected by
    # the dispatcher: the foundry varbits all zero and a fresh commission registers.
    data = ctx.state.data
    d = _dialogue()
    if _dialogue_open(d):
        if _dialogue_options(d):
            _choose_option(ctx, d, ("commission",), fallback_key="1")
        else:
            controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.4, 0.8)
        return
    if data.get("gf_handin_attempts", 0) >= HANDIN_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop("giants foundry: hand-in never completed")
        return
    kovac = _find_kovac()
    if kovac is None:
        return
    data["gf_handin_attempts"] = data.get("gf_handin_attempts", 0) + 1
    controller.click_menu_action(
        ctx.ser, kovac, "Hand-in",
        refresh_target=controller.npc_index_refresh_target(kovac.get("index")))
    controller.mark_busy(3.0)
    controller.human_delay(0.8, 1.2)


# ---------------------------------------------------------------------------
# Runtime: entity + dialogue helpers
# ---------------------------------------------------------------------------

def _find_object(obj_id, radius=32):
    for obj in controller.get_objects(radius=radius):
        if obj.get("id") == obj_id:
            return obj
    return None


def _refresh(obj):
    return controller.object_refresh_target(
        obj.get("x"), obj.get("y"), obj.get("plane", 0), obj.get("id"))


def _find_kovac():
    for npc in controller.get_npcs():
        if npc.get("id") == KOVAC_NPC_ID or (npc.get("name") or "").lower() == "kovac":
            return npc
    return None


def _dialogue():
    d = controller.get_api("ui", "dialogue")
    if not d or d.get("error"):
        return {}
    return d


def _dialogue_open(d):
    # Tolerant read of the m8aq Dialogue reader: exact field names get pinned on the
    # first live session; any of these shapes means "a dialogue is up".
    if not d:
        return False
    for key in ("open", "isOpen", "visible"):
        if isinstance(d.get(key), bool):
            return d[key]
    dtype = d.get("type")
    return bool(d.get("options")) or bool(dtype and dtype != "NONE")


def _dialogue_options(d):
    out = []
    for o in d.get("options") or []:
        out.append(o if isinstance(o, str) else (o.get("text") or ""))
    return out


def _choose_option(ctx, d, substrings, fallback_key=None):
    """Press the number key of the first dialogue option matching any substring
    (case-insensitive). Falls back to fallback_key when nothing matches."""
    for i, text in enumerate(_dialogue_options(d)):
        low = text.lower()
        if any(s in low for s in substrings):
            controller.key_tap(ctx.ser, str(i + 1))
            return True
    if fallback_key is not None:
        controller.key_tap(ctx.ser, fallback_key)
        return True
    return False


PHASE_HANDLERS = {
    "AWAITING_COMMISSION": get_commission_phase,
    "PREPARING": prepare_phase,
    "SELECTING_MOULDS": prepare_phase,
    "MOULD_SET": prepare_phase,
    "PREFORM_READY": pickup_phase,
    "COLLECTING_PREFORM": pickup_phase,
    "REFINING": refine_phase,
    "STORED": refine_phase,
    "READY_TO_HAND_IN": hand_in_phase,
    "RUINED": hand_in_phase,
}
