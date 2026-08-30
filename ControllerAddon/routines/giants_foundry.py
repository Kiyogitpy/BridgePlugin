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
