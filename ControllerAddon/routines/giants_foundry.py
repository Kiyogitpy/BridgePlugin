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
import dialogue
import travel

DEFAULT_CONFIG = {
    # Value-aware crucible fill plan (the recording's own loadout): items usable on the
    # crucible, the metal they melt into, bars per item (wiki: equipment = smithing bar
    # cost minus 1), and how many bars of that metal the crucible should end up holding.
    # Each metal lists its usable items in preference order (see metal_items); the planner
    # takes stock from each in turn until the metal's bars are covered -- USER 2026-09-03:
    # "write mith and ada to get all items, we have plenty of bars". MEASURED live: the
    # bank ran out of Adamant platelegs after one sword and of Adamant platebodies after
    # three more, with adamant bars and other adamant equipment still sitting there.
    # 18 mithril / 10 adamant is the wiki's pick past 70 Smithing (Strategies rev
    # 15275840: best economy AND best xp/reputation per hour -- score 89, a 5-section
    # sword, against 14/14's 95 and 6 sections). USER 2026-09-04. Platebody quantization
    # lands it at 20/8 (score 83, still 5 sections) when only bodies are in stock.
    "fill": [
        {"metal": "MITHRIL", "target_bars": 18, "items": None},   # None = metal_items("Mithril")
        {"metal": "ADAMANT", "target_bars": 10, "items": None},
    ],
    # Item quantization can overflow the 28-bar cap (MEASURED: 4-bar platebody into 2 bars
    # of space -> confirm dialog, 2 bars lost). The planner first trims the last item to an
    # exact fit; a residual overflow is accepted (confirm dialog) iff the loss is <= this.
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

# Wiki (Giants' Foundry, rev 15264119): equipment counts for its smithing bar cost minus 1;
# bars count 1; ornamental variants cannot be added (exact-name matching keeps "(g)"/"(t)"
# out). Ordered largest-first so a fill costs the fewest Use clicks; bars ahead of the
# 1-bar equipment because a bar stack is one search + N clicks and nobody minds spending it.
METAL_ITEM_VALUES = (
    ("platebody", 4),
    ("platelegs", 2), ("plateskirt", 2), ("chainbody", 2), ("kiteshield", 2),
    ("2h sword", 2), ("warhammer", 2), ("battleaxe", 2),
    ("bar", 1),
    ("full helm", 1), ("sq shield", 1), ("scimitar", 1), ("longsword", 1), ("claws", 1),
)


# The bar is not named like the equipment for two metals (MEASURED live 2026-09-03,
# gf_live22: "Adamant bar" matched nothing while the bank held Adamantite bars).
BAR_NAMES = {"Adamant": "Adamantite bar", "Rune": "Runite bar"}


def metal_items(metal_display):
    """Every crucible-usable item of one metal as (name, bars_each), e.g. metal_items("Adamant")."""
    out = []
    for suffix, bars in METAL_ITEM_VALUES:
        if suffix == "bar":
            out.append((BAR_NAMES.get(metal_display, f"{metal_display} bar"), bars))
        else:
            out.append((f"{metal_display} {suffix}", bars))
    return out


METAL_TIERS = ("BRONZE", "IRON", "STEEL", "MITHRIL", "ADAMANT", "RUNE")
# Adding a bar to the crucible needs its SMELTING level (wiki, Giants' Foundry rev
# 15264119, "Sword quality > Metal score"); a boost counts, so the boosted level is read.
METAL_SMITHING_LEVELS = {"BRONZE": 1, "IRON": 15, "STEEL": 30, "MITHRIL": 50,
                         "ADAMANT": 70, "RUNE": 85}


def _metal_usable(metal, stock, smithing_level, need=1):
    """Covers `need` bars from stock AND is within the Smithing level (an unknown level
    never filters). MEASURED live 2026-09-04 (gf_live_tier2): Rune planned at Smithing
    79 with Rune platebodies in the bank -- every Use click was refused and the fill
    ceiling stopped the run."""
    if smithing_level is not None and smithing_level < METAL_SMITHING_LEVELS.get(metal, 0):
        return False
    return stock is None or _metal_available_bars(metal, stock) >= need


def _metal_available_bars(metal, stock):
    """Crucible bars the bank can supply of this metal across every catalogue item."""
    return sum(max(0, stock.get(item.lower(), 0)) * bars
               for item, bars in metal_items(metal.capitalize()))


def _metal_in_stock(metal, stock):
    """Whether the bank holds ANY crucible-usable item of this metal (bars included)."""
    return _metal_available_bars(metal, stock) > 0


def substitute_empty_metals(fill_cfg, stock, smithing_level=None, contents=None):
    """(fill, swaps): every fill entry whose metal is unusable is re-assigned a lower
    tier, and the alloy never drops below ONE tier under its original lowest metal
    (a replacement that is empty too still stops the run through the restock's own
    "bank is out of" message, so the slide can never reach bronze unnoticed).

    USER 2026-09-04: shift the pair down rather than collapse onto one metal. Wiki
    (Strategies rev 15275840, 14+14 alloys): Mithril/Adamant 95, Steel/Adamant 77,
    Steel/Mithril 65, all-Mithril 40 -- two distinct metals is what the score rewards.
    A literal pair shift fails when the LOWER metal ran out (Steel/Mithril still needs
    the mithril), hence per-entry: adamant empty -> Steel/Mithril, mithril empty ->
    Steel/Adamant, both -> Iron/Steel. Higher tiers resolve first so the pair stays
    ordered. `stock` maps lower-cased item name -> bank quantity; `stock=None` checks
    the level alone (outside the bank, where stock is unknown but the level is not).

    "Unusable" means the bank cannot COVER what the crucible still lacks of the metal
    (`target_bars` minus `contents`), or the metal is above `smithing_level`. MEASURED
    live 2026-09-04 (gf_live_tier7): 11 mithril bars left against 18 read as in stock,
    the top-up named a platebody the bank no longer had, and the run stopped.

    Only the metals of entries that are KEPT count as taken, and a candidate that can
    cover its slot's need is preferred: MEASURED live (gf_live_tier8, 13:34) adamant at 1
    bar and mithril at 11 against 18/10 resolved to Iron 18 / Steel 10 (score 37) because
    mithril counted as taken by the entry being replaced -- the 11 mithril bars cover the
    10-bar slot, so Steel 18 / Mithril 10 (61) is the answer. When no candidate covers,
    the first tier below the metal that is not taken and within the level is used
    anyway, so the restock stop names it (the one-tier rule, USER)."""
    contents = contents or {}
    n = len(fill_cfg)
    metals = [e["metal"] for e in fill_cfg]
    needs = [e["target_bars"] - contents.get(m, 0) for e, m in zip(fill_cfg, metals)]

    def tier(m):
        return METAL_TIERS.index(m) if m in METAL_TIERS else -1

    def level_ok(m):
        return smithing_level is None or smithing_level >= METAL_SMITHING_LEVELS[m]

    open_entries = [i for i in range(n) if needs[i] > 0 and metals[i] in METAL_TIERS
                    and not _metal_usable(metals[i], stock, smithing_level, needs[i])]
    if not open_entries:
        return list(fill_cfg), []
    resolved = list(metals)
    taken = {metals[i] for i in range(n) if i not in open_entries}
    floor = min((tier(m) for m in metals if m in METAL_TIERS), default=0) - 1
    swaps = []
    for i in sorted(open_entries, key=lambda i: -tier(metals[i])):
        below = [m for m in reversed(METAL_TIERS[:tier(metals[i])])
                 if m not in taken and level_ok(m)]
        covering = [m for m in below if tier(m) >= floor
                    and _metal_usable(m, stock, smithing_level, needs[i])]
        choice = covering[0] if covering else (below[0] if below else None)
        if choice is None:
            continue
        resolved[i] = choice
        taken.add(choice)
        swaps.append((metals[i], choice))
    fill = []
    for entry, metal in zip(fill_cfg, resolved):
        if metal == entry["metal"]:
            fill.append(entry)
        else:
            fill.append({"metal": metal, "target_bars": entry["target_bars"], "items": None})
    return fill, swaps


def _effective_fill(ctx):
    """The fill plan for THIS sword: the bank-derived downgrade if one is in force
    (gf_fill_override, cleared by _reset_counters at the pour), else the config."""
    return ctx.state.data.get("gf_fill_override") or ctx.state.config["fill"]


def _smithing_level():
    return ((controller.get_skills() or {}).get("Smithing") or {}).get("boosted")


def _apply_substitution(ctx, stock, smithing, contents=None):
    """Run substitute_empty_metals over this sword's fill, log each swap, persist the
    result as the sword's override, and return the fill to plan from."""
    fill, swaps = substitute_empty_metals(_effective_fill(ctx), stock,
                                          smithing_level=smithing, contents=contents)
    for old_metal, new_metal in swaps:
        if smithing is not None and smithing < METAL_SMITHING_LEVELS[old_metal]:
            why = f"Smithing {smithing} is below the {METAL_SMITHING_LEVELS[old_metal]} for"
        else:
            have = _metal_available_bars(old_metal, stock or {})
            why = "bank is out of" if have == 0 else f"bank has only {have} bars of"
        debug_log.log("gf", f"{why} {old_metal.lower()} -- "
                            f"dropping to {new_metal.lower()} for this sword")
    if swaps:
        ctx.state.data["gf_fill_override"] = fill
    return fill


def _fill_items(entry):
    """An entry's usable items in preference order -- the "items" list, the whole metal
    catalogue when it is None, or the legacy single item/bars_each pair."""
    if entry.get("items") is None and entry.get("metal") and "item" not in entry:
        return metal_items(entry["metal"].capitalize())
    if entry.get("items"):
        return [(i["item"], i["bars_each"]) for i in entry["items"]]
    return [(entry["item"], entry["bars_each"])]


def _plan_metal(entry, remaining, available):
    """(item, bars_each, count) steps covering `remaining` bars of one metal. With stock
    unknown (available is None) the primary item alone; otherwise stock is taken from
    each item in preference order until the bars are covered, and a metal nothing can
    cover falls back to the primary (so the restock's own out-of-stock stop names it)."""
    items = _fill_items(entry)
    if available is None:
        item, bars_each = items[0]
        return [(item, bars_each, math.ceil(remaining / bars_each))]
    steps = []
    left = remaining
    for item, bars_each in items:
        qty = available.get(item.lower(), 0)
        if qty <= 0 or left <= 0:
            continue
        take = min(qty, math.ceil(left / bars_each))
        steps.append((item, bars_each, take))
        left -= take * bars_each
    if left > 0:
        # Short stock: top up with the primary item -- the restock fetches it, and the
        # open bank's own re-plan swaps in whatever it really holds (MEASURED gf_live20:
        # 4 leftover battleaxes planned 24/28 and safety-stopped instead of banking).
        item, bars_each = items[0]
        for i, (name, b, count) in enumerate(steps):
            if name == item:
                steps[i] = (name, b, count + math.ceil(left / b))
                break
        else:
            steps.append((item, bars_each, math.ceil(left / bars_each)))
    return steps


def plan_fill(fill_cfg, crucible_contents, max_overflow_loss, available=None):
    """What still needs adding, from the LIVE crucible varbits (crucibleContents) --
    re-derived every tick, no local estimate (the fur-pouch lesson). Returns
    (steps, error): steps ordered largest bars_each first so the 28-bar cap lands on the
    last, cheapest item; ([], None) means the crucible is satisfied; ([], "reason") is a
    config/stock problem worth a safety stop. `available` maps lower-cased item name ->
    bank quantity (None = cannot tell) and picks between each metal's alternatives.
    Item counts quantize UP (ceil), then the LAST step is trimmed to the smallest count
    that still fills the crucible -- an exact fit beats the game's overflow confirm
    (MEASURED: 2 bars lost on a 4-bar platebody into 2 bars of space); a residual loss is
    accepted at execution time iff <= max_overflow_loss."""
    contents = crucible_contents or {}
    space = CRUCIBLE_CAPACITY - sum(contents.values())
    if space <= 0:
        return [], None

    steps = []
    planned_bars = 0
    for entry in fill_cfg:
        # A metal never needs more than the crucible's free space (12 bars of space with
        # a 14-bar target planned a top-up trip while 12 held bars already fit exactly).
        remaining = min(entry["target_bars"] - contents.get(entry["metal"], 0),
                        space - planned_bars)
        if remaining <= 0:
            continue
        for item, bars_each, count in _plan_metal(entry, remaining, available):
            steps.append({"item": item, "bars_each": bars_each, "count": count})
            planned_bars += count * bars_each

    if not steps:
        return [], None
    if planned_bars < space:
        total = CRUCIBLE_CAPACITY - space + planned_bars
        return [], (f"fill plan only reaches {total}/{CRUCIBLE_CAPACITY} bars -- "
                    "raise the fill config's target_bars")
    steps.sort(key=lambda s: -s["bars_each"])
    # Trim the overflow away, smallest items first (MEASURED gf_live21: trimming only the
    # last step left 5 bars of overflow when the leftovers made a bigger item the last).
    for _, step in sorted(enumerate(steps), key=lambda p: (p[1]["bars_each"], -p[0])):
        while step["count"] > 0 and planned_bars - step["bars_each"] >= space:
            step["count"] -= 1
            planned_bars -= step["bars_each"]
    steps = [st for st in steps if st["count"] > 0]
    loss = planned_bars - space
    if loss > max_overflow_loss:
        return [], (f"fill plan would overflow {loss} bars "
                    f"(> max_overflow_loss {max_overflow_loss}) -- adjust the fill config; "
                    f"plan was {[(st['item'], st['count']) for st in steps]}")
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

# The Dunk/Quench ramp ACCELERATES: easy-giantsfoundry's measured DX_1 curve from the
# fast start is 27,30,33,37,41,45,49,53,57,62,67,72,77,83,89,95 per tick (CONFIRMED in the
# recording, 724->751->780->813). A fixed stop margin cannot track that -- MEASURED live
# 2026-09-03 (gf_live7): a poll that stopped at 935 for a 939 target peaked at 997 one tick
# later and the hammer acted out of band twice. And MEASURED gf_live9/10: with the machine
# pre-hovered, a click issued inside the same tick window as the reading cancels the ramp
# BEFORE its next increment -- the landing IS the reading (999 -> 971 read, click, 967).
# So the stop is: poll right after each tick boundary (ctx.events' tick clock), and click
# on the first reading past `ramp_aim` -- the target, capped one worst-case step inside the
# band so the first reading past it can never be out of band.
RAMP_RATE_STEP = 6       # DX_1 grows by 3..6 per tick in the fast regime; assume the worst
RAMP_INITIAL_RATE = 27   # first fast tick, before any delta has been observed
RAMP_TICK_MIN = 15       # smaller changes are passive decay, not a ramp tick
RAMP_MAX_RATE = 95       # the curve's last step; a bigger delta is a stale/late first read
TICK_S = 0.6
POLL_AFTER_TICK_S = 0.08  # the bridge serves the new varbit within ~20ms of the boundary


RAMP_EDGE_MARGIN = 4     # passive decay (-1 per 2 ticks) before the machine's first action


def ramp_aim(target, rate, min_t, max_t, heating):
    """The temperature to stop at: `target`, capped so that the first reading at or past
    it (which can overshoot the aim by up to one accelerating step) stays inside the band
    with room for the passive decay before the machine's first action (MEASURED gf_live28:
    a quench landed at 395 on a 392 floor)."""
    step = rate + RAMP_RATE_STEP + RAMP_EDGE_MARGIN
    return min(target, max_t - step) if heating else max(target, min_t + step)


def ramp_stop_reached(t, aim, heating):
    return t >= aim if heating else t <= aim


def action_fires_at(t, tool, n):
    """The temperature at which the n-th machine action from now fires (n=1 is the next
    one, at roughly the current reading): each earlier action moves the preform by its
    heat delta and the passive decay costs about 1 per action."""
    delta = TOOL_STATS[tool]["heat"]
    return t + (n - 1) * (delta - 1)


def edge_leave_due(t, tool, min_t, max_t, lead=2):
    """While a machine works, is it time to leave for the pool? MEASURED live 2026-09-03
    (gf_live12): the wheel fell through its floor and the next poll saw TOO_COLD two or
    three actions later, 5 quality each. The leave fires when action number `lead + 1`
    from now would fire OUT of band: with the pool hovered the interrupt registers
    within a tick (lead 1); otherwise the poll + flight needs the extra action (lead 2).
    MEASURED gf_live19: the earlier formula measured the temperature AFTER `lead`
    actions instead of the one action `lead + 1` fires AT, leaving one action early
    into a dunk that then cancelled itself (two wheel actions fit at 94 -> 76)."""
    fires = action_fires_at(t, tool, lead + 1)
    return fires < min_t if TOOL_STATS[tool]["heat"] < 0 else fires > max_t


def edge_leave_lead(tool, pool_hovered):
    if TOOL_STATS[tool]["cycle_ticks"] > 2:
        return 1                      # the hammer's 3s cycle leaves time either way
    return 1 if pool_hovered else 2


POOL_HOVER_EXTRA_LEAD = 2   # hover the pool this many actions before the leave is due


def _pool_for_tool(tool):
    """The pool a working tool will need next: cooling tools run into the floor (lava),
    the grindstone into the ceiling (waterfall)."""
    return LAVA_POOL_ID if TOOL_STATS[tool]["heat"] < 0 else WATERFALL_ID


def _pool_click(ctx, pool, option, in_place=False):
    """Start Dunk/Quench. USER 2026-09-03: "the right click isn't needed for quench and
    dunk, that is default to left click" (their client-side menu swap) -- so: a click in
    place when the pool is already hovered and /hover confirms the default, else a
    verified left click (click_entity withholds on a hover mismatch), else the menu."""
    if in_place and controller.hover_matches(expected_action=option):
        controller.click_human(ctx.ser)
        return True
    ok = controller.click_entity(ctx.ser, pool, refresh_target=_refresh(pool),
                                 expected_action=option)
    if ok:
        return True
    return bool(controller.click_menu_action(ctx.ser, pool, option,
                                             refresh_target=_refresh(pool)))


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
SWEET_SPOT_BONUS = 50      # wiki: "a large amount of progress (around 5%)" on the 0-1000 scale
WORK_STALL_S = 12.0        # > two hammer cycles (3s each) + the nulled first cycle
WORK_FIRST_ACTION_S = 6.0
WALK_S_PER_TILE = 0.6      # a queued machine click is performed on ARRIVAL: budget the walk
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
    data = ctx.state.data
    selected = st.get("selectedMoulds") or {}
    commission = st.get("commissionTypes") or []
    usable = set(st.get("usableMoulds") or [])
    for part in MOULD_PARTS:
        want = plan.get(part)
        if want is None or selected.get(part) == want:
            continue
        if selected.get(part) and selected.get(part) == data.get("gf_mould_fallback", {}).get(part):
            continue          # accepted fallback (see below)
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
            # Ties go to the PLANNED row: the plan's argmax walks the catalogue in table
            # order, this loop walks the UI list in its own order -- MEASURED live
            # 2026-09-03 (FLAT/HEAVY: Defender Base 18 == Chopper Forte +1 18), the two
            # disagreed and the selection re-clicked every tick ("clicking forte over and
            # over").
            if best is None or s > best[0] or (s == best[0] and enum == want):
                best = (s, enum, row)
        if best is None:
            data["gf_mould_norow"] = data.get("gf_mould_norow", 0) + 1
            debug_log.log("gf", f"no selectable mould row for {part} -- re-opening "
                                f"(x{data['gf_mould_norow']})")
            if data["gf_mould_norow"] >= MOULD_NOROW_LIMIT:
                ctx.state.trigger_safety_stop(
                    f"giants foundry: no selectable mould row for {part} after repeated tries")
            return
        data["gf_mould_norow"] = 0
        if best[1] != want:
            if selected.get(part) == best[1]:
                # The fallback is already selected: accept it rather than re-clicking it
                # once a tick forever (MEASURED live 2026-09-03, gf_live13/14, USER:
                # "clicking forte over and over" -- Defender Base's row never matched).
                data.setdefault("gf_mould_fallback", {})[part] = best[1]
                debug_log.log("gf", f"accepting {best[1]} for {part} (planned {want} not "
                                    f"selectable); rows seen: "
                                    f"{[_strip_tags(r.get('text')) for r in rows]}")
                continue
            debug_log.log("gf", f"planned {want} not on screen for {part} -- "
                                f"taking best visible {best[1]}")
        controller.click_entity(ctx.ser, best[2], offset=(0, 0),
                                check_ui_blockers=False, simulate_mistakes=False)
        controller.human_delay(0.5, 0.9)
        return
    # every part satisfied -> confirm
    if _click_widget(ctx, MOULD_GROUP, MOULD_SET_CHILD):
        controller.human_delay(0.6, 1.0)
    else:
        debug_log.log("gf", "Set Mould click failed (widget not found or not clickable)")


# ---------------------------------------------------------------------------
# Runtime: state access + dispatch
# ---------------------------------------------------------------------------

KOVAC_NPC_ID = 11472
BANK_CHEST_ID = 44630
LAVA_POOL_ID = 44631
WATERFALL_ID = 44632
STORAGE_EMPTY_ID = 44633    # "Preform storage" IMPOSTOR variant, op Store-preform
STORAGE_FULL_ID = 44634     # "Preform storage" IMPOSTOR variant, op Take-preform
STORAGE_BASE_ID = 44778     # the multiloc's own BASE id -- what ObjectProvider's "id"
                            # field actually reports; the impostor ids above only ever show
                            # up in its "impostorId" field. See _find_storage.
PREFORM_STORAGE_IDS = {STORAGE_BASE_ID, STORAGE_EMPTY_ID, STORAGE_FULL_ID}
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
RESTOCK_MAX_TRIPS = 3
FILL_DIALOG_TIMEOUT_S = 4.0
POUR_MAX_ATTEMPTS = 4
HEAT_FAIL_LIMIT = 5
MOULD_NOROW_LIMIT = 5
STORAGE_NOT_FOUND_LIMIT = 10


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
    if not st.get("selectedMoulds"):
        data["gf_mould_fallback"] = {}
    if phase not in ("READY_TO_HAND_IN", "RUINED"):
        data["gf_handin_attempts"] = 0
    if phase not in ("PREFORM_READY", "COLLECTING_PREFORM"):
        data["gf_pickup_attempts"] = 0
    bars = st.get("crucibleBarCount", 0)
    if bars > data.get("gf_last_bars", -1):
        data["gf_fill_attempts"] = 0
        data["gf_restock_trips"] = 0
    if bars < data.get("gf_last_bars", 0):
        data.pop("gf_fill_override", None)    # poured: the next sword plans from config
    data["gf_last_bars"] = bars
    if st.get("mouldSetupOpen"):
        data["gf_mould_opens"] = 0
    # _pour: reset once we've left MOULD_SET (poured) or a preform already exists (the
    # pour genuinely registered even if the phase read is a tick behind).
    if phase != "MOULD_SET" or st.get("preformState") in ("EQUIPPED", "STORED"):
        data["gf_pour_attempts"] = 0
    # STORED-but-storage-not-found: reset once we're no longer waiting to retrieve one.
    if phase != "STORED":
        data["gf_storage_not_found"] = 0
    # gf_dead_clicks is also cleared inside _watch_progress, but that handler only runs
    # while the dispatcher keeps routing to REFINING -- tracking completion growth here
    # too means a healthy session can never accumulate dead clicks across a phase change
    # the way mh_contact_casts did (see the mahogany_homes contact-cast lesson).
    completion = st.get("completion", 0)
    if completion > data.get("gf_last_completion", -1):
        data["gf_dead_clicks"] = 0
    data["gf_last_completion"] = completion


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def setup(ctx):
    # /equipment (EquipmentProvider) is a SLOT-KEYED dict -- {"gloves": {"name": ...}, ...},
    # no "items" key. It comes back as a totally empty {} only when the client's own
    # EQUIPMENT ItemContainer is null (bridge down / not logged in yet) -- that is "cannot
    # tell" and must never stop the run. A non-empty dict with an empty/mismatched
    # "gloves" sub-dict is a real, successful read of "no ice gloves equipped".
    eq = controller.get_equipment() or {}
    if not eq:
        debug_log.log("gf", "could not read equipment at setup -- glove/hand check skipped")
        return
    gloves = ((eq.get("gloves") or {}).get("name") or "").lower()
    # The item is "Smiths gloves (i)" (cache obj 27031) -- an earlier "smithing gloves (i)"
    # here never matched, so the routine refused to start for anyone wearing the pair.
    if "ice gloves" not in gloves and "smiths gloves (i)" not in gloves:
        ctx.state.trigger_safety_stop(
            "giants foundry: ice gloves / Smiths gloves (i) must be equipped to pick "
            "up preforms")
        return
    # The held preform itself lives in the weapon slot (/equipment reports weapon
    # "Preform" mid-refine) -- MEASURED live 2026-09-03: a restart with the sword in hand
    # right-clicked it and chose "Remove Preform". Only OTHER hand items block a pickup.
    held = [(eq.get(slot) or {}).get("name") or "" for slot in ("weapon", "shield")]
    if any(name and name.lower() != "preform" for name in held):
        debug_log.log("gf", "weapon/shield equipped at setup -- freeing hands")
        _free_hands(ctx)
    debug_log.log("gf", "setup ok -- gloves equipped")


STORE_CONFIRM_S = 4.0      # the store animation, on top of the walk to the storage
STORE_TRIES = 2            # a verified left-click, then the right-click menu entry


def _await_stored(ctx, storage):
    """Poll the reader until the preform reads STORED, budgeting the walk to the storage.
    Iteration-bounded (not wall-clock) like _answer_fill_dialogs so the hermetic tests'
    no-op sleep exits instantly."""
    budget = STORE_CONFIRM_S + (storage.get("distance") or 0) * WALK_S_PER_TILE
    for _ in range(max(1, int(budget / HEAT_POLL_S))):
        st = read_state()
        if st and (st.get("preformState") == "STORED" or st.get("preformStored")):
            return True
        time.sleep(HEAT_POLL_S)
    return False


def _step_off_machines(ctx, storage):
    """Last resort before a break with the preform still in hand: a force-walk one tile
    toward the storage interrupts whatever machine the failed store click may have
    started. Nothing runs under the preform while nobody is watching; it just cools."""
    player = controller.get_player() or {}
    px, py = player.get("x"), player.get("y")
    if px is None or py is None:
        return
    dx = (storage["x"] > px) - (storage["x"] < px)
    dy = (storage["y"] > py) - (storage["y"] < py)
    if dx == 0 and dy == 0:
        dy = 1
    travel.walk_to_exact_tile(ctx, px + dx, py + dy, player.get("plane", 0),
                              wait_for_arrival=False)


def before_break(ctx):
    # Park a held preform in the Preform Storage before a 3-12 minute break -- the
    # game's own pause mechanism; heat and progress hold indefinitely. On resume the
    # STORED phase picks it back up.
    # The store is CONFIRMED, never assumed. MEASURED live 2026-09-04 (gf_live_tier5,
    # 09:15): one unverified click, the preform stayed EQUIPPED through a 5-minute
    # break, and the sword came back RUINED (148 -> 0 quality; 5 per bad action is
    # ~40s of a machine running out of band). The break starts when this returns
    # regardless (engine contract), so a store that never lands ends with a walk off
    # the machines rather than a hope.
    st = read_state()
    if not st or st.get("preformState") != "EQUIPPED":
        return
    storage = _find_storage()
    if storage is None:
        debug_log.log("gf", "break due but preform storage not visible -- breaking anyway")
        return
    debug_log.log("gf", "break due -- storing the preform first")
    for attempt in range(STORE_TRIES):
        if attempt == 0:
            controller.click_entity(ctx.ser, storage, refresh_target=_refresh(storage),
                                    expected_action="Store-preform")
        else:
            controller.click_menu_action(ctx.ser, storage, "Store-preform",
                                         refresh_target=_refresh(storage), offset=(0, 0))
        if _await_stored(ctx, storage):
            debug_log.log("gf", f"preform stored (attempt {attempt + 1})")
            return
        debug_log.log("gf", f"[!] store click {attempt + 1} did not register -- "
                            f"preform still in hand")
    debug_log.log("gf", "[!] could not store the preform -- walking off the machines for the break")
    _step_off_machines(ctx, storage)


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
        # An option menu (the hand-in chain's "Receive another commission?" Yes/No, left
        # open by a restart) needs its number key -- Space never selects an option and
        # pressed once a tick forever (MEASURED live 2026-09-03, gf_live8).
        if _dialogue_options(d):
            _choose_option(ctx, d, ("yes", "commission"), fallback_key="1")
        else:
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
    fallback = ctx.state.data.get("gf_mould_fallback", {})
    if st.get("phase") != "MOULD_SET" and (
            len(selected) < 3 or any(selected.get(p) != plan.get(p) and
                                     selected.get(p) != fallback.get(p) for p in plan)):
        _open_mould_setup(ctx)
        return
    if st.get("crucibleBarCount", 0) < CRUCIBLE_CAPACITY:
        _fill_crucible(ctx, st)
        return
    _pour(ctx, st)


def _inventory_counts(inv):
    counts = {}
    for i in inv:
        name = (i.get("name") or "").lower()
        if name:
            counts[name] = counts.get(name, 0) + (i.get("qty") or i.get("quantity") or 1)
    return counts


def _fill_crucible(ctx, st):
    cfg = ctx.state.config
    data = ctx.state.data
    # Outside the bank, what we HOLD picks each metal's item (plan_fill's `available`);
    # inside it, the open bank's own item list does (_restock_from_bank re-plans). The
    # m8aq storage/bank reader only carries the container while the interface is open, so
    # it cannot answer here -- MEASURED live 2026-09-03 (gf_live5): it lingered for one tick
    # after closing, the plan flipped platebody -> platelegs, the three held platebodies no
    # longer matched, and every tick became a bank trip.
    inv = controller.get_inventory() or []    # bare list (controller.get_inventory), not {"items": [...]}
    held_counts = _inventory_counts(inv)
    # The level gate applies before ANY fill click, not just at the restock: MEASURED
    # live 2026-09-04 (gf_live_tier3), Rune platebodies still in hand were used straight
    # away at Smithing 79. Stock is unknown out here, so only the level is checked.
    fill = _apply_substitution(ctx, None, _smithing_level(), st.get("crucibleContents"))
    steps, err = plan_fill(fill, st.get("crucibleContents") or {},
                           cfg["max_overflow_loss"],
                           available=held_counts if held_counts else None)
    if err:
        ctx.state.trigger_safety_stop(f"giants foundry: {err}")
        return
    if not steps:
        return    # satisfied -- prepare_phase pours on the next tick's read

    step = steps[0]
    held = [i for i in inv if (i.get("name") or "").lower() == step["item"].lower()]
    held_qty = sum((i.get("qty") or i.get("quantity") or 1) for i in held)   # a bar stack is ONE slot
    if not held:
        if data.get("gf_restock_trips", 0) >= RESTOCK_MAX_TRIPS:
            ctx.state.trigger_safety_stop(
                "giants foundry: restock trips are not progressing the crucible fill")
            return
        data["gf_restock_trips"] = data.get("gf_restock_trips", 0) + 1   # reset when bars grow
        _restock_from_bank(ctx, steps, st)
        return

    if data.get("gf_fill_attempts", 0) >= FILL_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(
            "giants foundry: crucible fill not progressing (crucible bar count never grew)")
        return
    crucible = _find_object(CRUCIBLE_ID)
    if crucible is None:
        return
    data["gf_fill_attempts"] = data.get("gf_fill_attempts", 0) + 1   # reset when bars grow
    count = min(step["count"], held_qty)
    if _is_bar(step["item"]) and _fill_via_crucible(ctx, crucible, step["item"], count):
        return
    # The sidebar may not be showing the inventory (MEASURED live 2026-09-03, gf_live27:
    # after a pause/resume the stats tab was up and the "platebody" right-click offered
    # "View Attack guide"; three misses hit the fill ceiling). Same rule as GOTR's
    # inventory clicks: open the tab first.
    controller.open_inventory_tab(ctx.ser)
    # Explicit Use via the menu -- a wearable's LEFT-click default is Wear/Wield, so a
    # bare click on platelegs would equip them instead of selecting them.
    if not controller.click_menu_action(ctx.ser, held[0], "Use",
                                        offset=(0, 0), check_ui_blockers=False):
        return
    controller.human_delay(0.2, 0.4)
    if not controller.click_entity(ctx.ser, crucible, refresh_target=_refresh(crucible)):
        return
    controller.mark_busy(FILL_DIALOG_TIMEOUT_S + 4)
    _answer_fill_dialogs(ctx, min(step["count"], held_qty), accept_overflow=True)


def _is_bar(item):
    return item.lower().endswith(" bar")


FILL_UI_WAIT_S = 3.0
FILL_UI_SKIP_GROUPS = {149, 12, 15, 162}     # inventory, bank, bank inventory, chatbox


def _fill_via_crucible(ctx, crucible, item, count):
    """USER 2026-09-03: "instead of using a bar then on the crucible, which is very slow,
    we can click the crucible then select the bar we wanna fill". Bars are not stackable,
    so Use-bar-on-crucible adds ONE bar per tick (12 bars took ~90s live). Fill on the
    crucible opens a selection interface; the bar's entry is matched by its name text
    (the widget dump carries no item ids) and the usual quantity prompt follows. The
    layout is logged the first time so it can be pinned; nothing found in FILL_UI_WAIT_S
    means "cannot tell" and the caller falls back to the Use path."""
    ok = controller.click_entity(ctx.ser, crucible, refresh_target=_refresh(crucible),
                                 expected_action="Fill")
    if not ok:
        if not controller.click_menu_action(ctx.ser, crucible, "Fill",
                                            refresh_target=_refresh(crucible)):
            return False
    controller.mark_busy(FILL_UI_WAIT_S + FILL_DIALOG_TIMEOUT_S + 4)
    deadline = time.time() + FILL_UI_WAIT_S
    want = item.lower()
    while time.time() < deadline:
        widgets = controller.get_widgets_dump() or []
        hit = None
        for w in widgets:
            if w.get("group") in FILL_UI_SKIP_GROUPS:
                continue
            label = _strip_tags(f"{w.get('name') or ''} {w.get('text') or ''}").lower()
            if want in label and w.get("screenX") is not None:
                hit = w
                break
        if hit is not None:
            if not ctx.state.data.get("gf_fill_ui_logged"):
                ctx.state.data["gf_fill_ui_logged"] = True
                debug_log.log("gf", f"crucible Fill interface: {item} at widget "
                                    f"{hit.get('group')}.{hit.get('child')}[{hit.get('index')}] "
                                    f"actions={hit.get('actions')}")
            controller.click_entity(ctx.ser, hit, offset=(0, 0), check_ui_blockers=False,
                                    simulate_mistakes=False)
            _answer_fill_dialogs(ctx, count, accept_overflow=True)
            return True
        time.sleep(0.25)
    groups = sorted({w.get("group") for w in (controller.get_widgets_dump() or [])
                     if w.get("group") not in FILL_UI_SKIP_GROUPS and w.get("text")})
    debug_log.log("gf", f"crucible Fill interface: no entry for {item}; text-bearing groups "
                        f"{groups[:20]} -- falling back to Use")
    return False


def _pour(ctx, st):
    data = ctx.state.data
    if data.get("gf_pour_attempts", 0) >= POUR_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop("giants foundry: pour never registered")
        return
    crucible = _find_object(CRUCIBLE_ID)
    if crucible is None:
        return
    data["gf_pour_attempts"] = data.get("gf_pour_attempts", 0) + 1   # reset in _reset_counters
    controller.click_entity(ctx.ser, crucible, refresh_target=_refresh(crucible),
                            expected_action="Pour")
    controller.mark_busy(4.0)
    controller.human_delay(1.0, 1.6)


# USER 2026-09-03: "instead of searching the entire item, just search part of it like
# 'mith' to get all mithril items or 'ada' to get all adamant items" -- one search per
# metal lays every usable item of it out from slot 0, and the needed ones are clicked
# from those results.
METAL_SEARCH = {"MITHRIL": "mithril", "ADAMANT": "adamant", "RUNE": "rune", "STEEL": "steel",
                "IRON": "iron", "BRONZE": "bronze"}   # informational; the group key is the first word


def _slot_on_grid(bank, slot):
    """Whether a bank slot's reported point sits inside the visible item grid -- between
    the tab strip and the quantity buttons. Unknown geometry counts as visible."""
    y = slot.get("screenY")
    if y is None:
        return False
    tabs = bank.get("tabs") or []
    top = (tabs[0].get("screenY") if tabs else None)
    one = (bank.get("quantityButtons") or {}).get("one") or {}
    bottom = one.get("screenY")
    if top is not None and y < top + 20:
        return False
    if bottom is not None and y > bottom - 40:
        return False
    return True


def _withdraw_by_metal(ctx, need):
    """Withdraw {item: count}: one short search per metal (METAL_SEARCH), then click each
    needed item from the results in quantity-1 mode. A result that is not on the visible
    grid falls back to banker.search_and_withdraw's full-name search."""
    groups = {}
    for item, n in need.items():
        # USER 2026-09-03: "adam" for all adamant items (bars included -- "Adamantite"
        # shares the prefix), "mith" for mithril; there are never enough of a metal's
        # items to push one off the grid. (gf_live30's off-grid bar was a search that
        # never got typed, since fixed in banker.)
        key = item.split(" ", 1)[0].lower()[:4]
        groups.setdefault(key, []).append((item, n))
    for key, items in groups.items():
        if not banker.search_bank(ctx, key):
            for item, n in items:
                if not banker.search_and_withdraw(ctx, item, n):
                    debug_log.log("gf", f"search-withdraw of {n}x {item} failed")
            continue
        banker.set_quantity_mode(ctx, "one")
        for item, n in items:
            bank = controller.get_bank(fresh=True)
            slot = next((b for b in bank.get("items", [])
                         if (b.get("name") or "").lower() == item.lower() and b.get("qty", 0) > 0),
                        None)
            if slot is None or not _slot_on_grid(bank, slot):
                # Back-to-back searches are fine (banker.search_bank's own note); the
                # earlier clear-then-search dance left the bank closed (gf_live30).
                debug_log.log("gf", f"{item} not on the '{key}' results grid -- full-name search")
                if not banker.search_and_withdraw(ctx, item, n):
                    debug_log.log("gf", f"search-withdraw of {n}x {item} failed")
                if any(i2 != item for i2, _ in items):
                    banker.search_bank(ctx, key)
                continue
            debug_log.log("bank", f"withdrawing {n}x {item} from the '{key}' results")
            _withdraw_count(ctx, slot, n)
        banker.clear_bank_search(ctx)


WITHDRAW_MENU_STEPS = (10, 5)


def _withdraw_count(ctx, slot, n):
    """USER 2026-09-03: "clicking 12 times on a bar isn't very human" -- take the bulk
    through the right-click Withdraw-10 / Withdraw-5 entries and the remainder through
    Withdraw-1. EVERY withdraw is a NAMED menu entry: a bare left-click is whatever the
    account's Menu Entry Swapper made it. MEASURED live 2026-09-04
    (recordings/2026-09-04_134458_gf-iron-withdraw): the "single" clicks on Iron bar each
    resolved to Withdraw-14 (a per-item ui_786444_* swap), 10 + 5 + 14 hit the 28-slot
    cap, every later withdraw was refused for space, and the run stopped on "bank is out
    of Steel bar" three times -- the routine's own "Default quantity: 1" click had
    registered and changed nothing, because a swap ignores the quantity mode."""
    left = n
    for chunk in WITHDRAW_MENU_STEPS + (1,):
        while left >= chunk:
            if not controller.click_menu_action(ctx.ser, slot, f"Withdraw-{chunk}",
                                                offset=(0, 0), check_ui_blockers=False):
                break            # entry missing (thin stack): fall through to a smaller step
            left -= chunk
            controller.human_delay(0.2, 0.4)
    if left:
        debug_log.log("bank", f"[!] {left}x {slot.get('name')} not withdrawn -- no Withdraw entry took")


def _restock_from_bank(ctx, steps, st):
    """Withdraw everything the remaining plan needs in one trip. plan_fill already
    vetoed any overflow beyond config, so whatever we withdraw here is intended."""
    bank_obj = banker.find_nearby_bank({"maxBankDistance": 25})
    if bank_obj is None:
        debug_log.log("gf", "bank chest not visible -- waiting")
        return
    # A restart (or a manual cleanup) can leave the bank interface already up; clicking
    # the chest's tile through it lands on whatever bank widget covers that pixel.
    if not controller.get_bank().get("open") and not banker.open_bank(ctx, bank_obj):
        return
    banker.deposit_all_inventory(ctx)
    # The OPEN bank's item list is the authoritative stock -- re-plan against it before
    # withdrawing. MEASURED live 2026-09-03 (gf_live4): a transient /api/storage/bank read
    # failure at tick 1 left the pre-bank plan on the primary item and the restock stopped
    # on "out of Adamant platelegs" with 9 Adamant platebodies in the same bank.
    items = (controller.get_bank(fresh=True) or {}).get("items") or []
    if items:
        stock = {}
        for i in items:
            name = (i.get("name") or "").lower()
            if name:
                stock[name] = stock.get(name, 0) + (i.get("qty") or 0)
        cfg = ctx.state.config
        # An empty metal drops one tier for the rest of this sword (substitute_empty_metals);
        # the override is what _fill_crucible plans from outside the bank, so the steel we
        # withdraw here matches the next tick's plan instead of bouncing back to the bank.
        fill = _apply_substitution(ctx, stock, _smithing_level(), st.get("crucibleContents"))
        replanned, err = plan_fill(fill, st.get("crucibleContents") or {},
                                   cfg["max_overflow_loss"], available=stock)
        if err:
            banker.close_bank(ctx)
            ctx.state.trigger_safety_stop(f"giants foundry: {err}")
            return
        if replanned:
            steps = replanned
    need = {}
    for s in steps:
        need[s["item"]] = need.get(s["item"], 0) + s["count"]
    # Search-then-withdraw per item, never the blind withdraw_loadout loop. MEASURED live
    # 2026-09-03 (gf_live1): this user's bank holds 952 items and the platelegs sit in tab
    # 2 (slot 487) -- /bank reports a real screen position far below the window for
    # anything not on the visible page, every bare click was skipped as "outside the
    # RuneLite window", and the empty inventory then read as "bank is out of Adamant
    # platelegs" on a bank holding 7. search_bank lays the matches out from slot 0.
    _withdraw_by_metal(ctx, need)
    inv = controller.get_inventory() or []
    names = [(i.get("name") or "").lower() for i in inv]
    for item in need:
        if item.lower() not in names:
            # A bank that is no longer open says nothing about stock. MEASURED live
            # 2026-09-04 (gf_live_tier6, 12:49): a network drop closed the interface
            # mid-withdraw, the clicks hit nothing, and the empty inventory read as
            # "out of Mithril bar" with the bars still banked; login.py reconnected 3s
            # after the stop. Retry next tick -- RESTOCK_MAX_TRIPS still bounds it.
            if not (controller.get_bank(fresh=True) or {}).get("open"):
                debug_log.log("gf", f"[!] bank closed under the withdraw of {item} "
                                    f"(disconnect?) -- retrying next tick")
                return
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
            debug_log.log("gf", f"crucible prompt options {opts} -- want {count}")
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
                        # type_text has no trailing Enter -- this is a numeric chatbox
                        # prompt, so type_digits_and_enter (which presses Enter) is the
                        # one that actually submits the amount.
                        controller.type_digits_and_enter(ctx.ser, str(count))
                        picked = True
                        break
            if not picked:
                controller.key_tap(ctx.ser, "1")
            answered_quantity = True
            controller.human_delay(0.4, 0.7)
            continue
        if opts and _is_confirm(opts):
            want = ("yes", "yes.") if accept_overflow else ("no", "no.")
            _choose_option(ctx, d, want, fallback_key=None)
            return
        time.sleep(0.25)


CONFIRM_OPTION_TEXTS = ("yes", "yes.", "no", "no.")


def _is_confirm(opts):
    # Exact match, not "yes"/"no" as a substring of the joined option text -- a plain
    # substring test false-positives on option text containing "None" or "Now".
    return any(o.strip().lower() in CONFIRM_OPTION_TEXTS for o in opts)


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
        # MEASURED live 2026-09-03 (gf_live18): the third click DID pick the preform up,
        # and the ceiling fired on the same tick off a state read one tick stale.
        fresh = read_state()
        if fresh and fresh.get("preformState") == "EQUIPPED":
            data["gf_pickup_attempts"] = 0
            return
        ctx.state.trigger_safety_stop(
            "giants foundry: could not pick up the preform (hands still full?)")
        return
    jig = _find_object(MOULD_JIG_ID)
    if jig is None:
        return
    data["gf_pickup_attempts"] = data.get("gf_pickup_attempts", 0) + 1
    ok = controller.click_entity(ctx.ser, jig, refresh_target=_refresh(jig),
                                 expected_action="Pick-up")
    if getattr(ok, "withheld", False):
        # The hover check refused the pixel twice in a row live (gf_live18) -- a
        # contested/stale pixel is what the right-click menu beats (the golem_crafting
        # tier-3 lesson), so escalate instead of spending the attempt budget on it.
        controller.click_menu_action(ctx.ser, jig, "Pick-up", offset=(0, 0),
                                     refresh_target=_refresh(jig))
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
    controller.key_tap(ctx.ser, "ESC")   # the firmware only knows "ESC" -- see Firmware.ino


def on_event(ctx, event_type, payload):
    if event_type != "CHAT_MESSAGE":
        return
    msg = ((payload or {}).get("message") or "").lower()
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
        storage = _find_storage()
        if storage is None:
            data["gf_storage_not_found"] = data.get("gf_storage_not_found", 0) + 1
            debug_log.log("gf", "preform stored but storage not visible -- waiting")
            if data["gf_storage_not_found"] >= STORAGE_NOT_FOUND_LIMIT:
                ctx.state.trigger_safety_stop(
                    "giants foundry: preform storage never became visible to retrieve a "
                    "stored preform")
            return
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
        # A claim one action from the band edge is worth at most the 5 its bonus action
        # then loses out of band, and it preempted the edge-leave tick live (gf_live26:
        # claim at 21:27:49, TOO_COLD at 41 two seconds later). The leave wins.
        near_edge = (st.get("minimumTemperature", -1) >= 0 and st.get("temperature", -1) >= 0
                     and edge_leave_due(st["temperature"], tool, st["minimumTemperature"],
                                        st["maximumTemperature"], lead=1))
        if st.get("temperatureStatus") == "READY" and not data.get("gf_sweet_claimed")                 and not near_edge:
            machine = _find_object(TOOL_STATS[tool]["object"])
            if machine is not None:
                controller.click_entity(ctx.ser, machine, refresh_target=_refresh(machine),
                                        simulate_mistakes=False)
                data["gf_sweet_claimed"] = True
                data["gf_pool_hovered"] = None
                # Wiki: "the sweet spot may frequently push progress into the next section
                # and require a new tool, so the player should be ready to click away".
                # MEASURED live 2026-09-03 (gf_live23): the bonus crossed the boundary on
                # the machine's next tick and the ordinary path's pool click came 1.4s
                # later -- one wrong-tool tick, 5 quality. Arm the watch now.
                per = 1000.0 / st.get("sectionCount", 0) if st.get("sectionCount") else 0
                if per and (st.get("completion", 0) % per) + SWEET_SPOT_BONUS >= per                         and _next_tool(st) is not None:
                    data["gf_boundary_armed"] = (tool, st.get("completion", 0))
                    _boundary_watch(ctx, st, tool)
                return
    else:
        data["gf_sweet_claimed"] = False

    # THE QUALITY GUARD (2): qualityLost growing while temperatureStatus says READY means
    # our read is stale (the varbits and the working machine are a tick or more apart) or
    # something else is compounding the damage -- either way, acting on a belief that has
    # just been contradicted twice in a row risks making it worse. Skip this tick and
    # re-read rather than clicking blind; a single growing read is not enough (that is the
    # ordinary one-tick lag between an action and its varbit landing), but two in a row is.
    quality_lost = st.get("qualityLost", 0)
    prev_quality_lost = data.get("gf_last_quality_lost")
    grew = prev_quality_lost is not None and quality_lost > prev_quality_lost
    data["gf_last_quality_lost"] = quality_lost
    if grew and st.get("temperatureStatus") == "READY":
        streak = data.get("gf_quality_drop_streak", 0) + 1
        data["gf_quality_drop_streak"] = streak
        if streak >= 2:
            debug_log.log("gf", "quality dropping while believed correct -- re-reading")
            return
    else:
        data["gf_quality_drop_streak"] = 0

    # "Working" is NOT the animation alone. MEASURED live 2026-09-03 (gf_live2 + recorder):
    # the polishing animation (9454) plays ~0.7s of each 1.2s wheel cycle and the player
    # reads as idle (activity NONE) the rest, so a random poll saw "not working" ~40% of
    # the time; every such poll re-clicked the wheel, every re-click forfeited an action
    # ("overwork the preform"), five in a row hit DEAD_CLICK_LIMIT, and the stop left the
    # wheel running while the preform went cold (quality 147 -> 67). So: progress that
    # advanced within WORK_STALL_S is working, and so is a machine click still inside its
    # walk-budgeted commit window (five hammer clicks landed on five consecutive ticks
    # while the player was still walking there). The memory resets on a tool change so a
    # section boundary never makes the next machine wait out a stall window.
    now = time.time()
    if data.get("gf_work_tool") != tool:
        data["gf_work_tool"] = tool
        data.pop("gf_progress_mark", None)
        data["gf_engage_until"] = 0.0
    mark = data.get("gf_progress_mark")
    recent_progress = mark is not None and now - mark[1] < WORK_STALL_S
    engaged = now < data.get("gf_engage_until", 0.0)
    working = (st.get("activity") in WORKING_ACTIVITIES) or recent_progress or engaged
    status = st.get("temperatureStatus")
    if working:
        if status in ("TOO_COLD", "TOO_HOT"):
            # THE QUALITY GUARD: a running machine keeps acting while the temperature
            # is wrong, and every such action costs 5 quality. Interrupt it by going
            # straight to the heat adjust -- clicking the lava/waterfall interrupts the
            # work exactly like any other click, so this is one action, not two.
            _adjust_heat_and_engage(ctx, st, tool)
            return
        min_t, max_t = st.get("minimumTemperature", -1), st.get("maximumTemperature", -1)
        t = st.get("temperature", -1)
        actions_left = actions_left_in_section(st.get("completion", 0),
                                               st.get("sectionCount", 0), tool)
        if min_t >= 0 and t >= 0 and actions_left > 1:
            pool_id = _pool_for_tool(tool)
            lead = edge_leave_lead(tool, data.get("gf_pool_hovered") == pool_id)
            # Only an action that will actually fire in THIS section can be out of band;
            # the section's last action hands over to the boundary watch.
            if lead + 1 <= actions_left and edge_leave_due(t, tool, min_t, max_t, lead=lead):
                # Leave BEFORE the edge (see edge_leave_due); the last action of a section
                # is left to the boundary watch, which changes the band anyway.
                pool = _find_object(pool_id)
                debug_log.log("gf", f"leaving {tool} at {t} before the band edge {min_t}-{max_t}")
                _adjust_heat_and_engage(ctx, st, tool, pool=pool,
                                        prehovered=data.get("gf_pool_hovered") == pool_id,
                                        heating=TOOL_STATS[tool]["heat"] < 0)
                return
            if edge_leave_due(t, tool, min_t, max_t, lead=lead + POOL_HOVER_EXTRA_LEAD) and \
                    data.get("gf_pool_hovered") != pool_id:
                # USER 2026-09-03: "hover heat up / waterfall too since we just lost two
                # quality for being too slow" -- the player is standing still at the
                # machine, so a hovered pool pixel stays put until the leave.
                pool = _find_object(pool_id)
                if pool is not None and controller.hover_entity(
                        ctx.ser, pool, refresh_target=_refresh(pool)):
                    data["gf_pool_hovered"] = pool_id
        _watch_progress(ctx, st)
        return

    if status == "READY":
        _click_machine(ctx, st, tool)
    elif status in ("TOO_COLD", "TOO_HOT"):
        _adjust_heat_and_engage(ctx, st, tool)


HEAT_LEVEL = {"POLISHING_WHEEL": 1, "GRINDSTONE": 2, "TRIP_HAMMER": 3}
BOUNDARY_WATCH_MARGIN_S = 2.5


def _next_tool(st):
    seq = st.get("toolSequence") or []
    idx = st.get("currentSection", 0)      # 1-based current -> 0-based index of the NEXT
    if 0 < idx < len(seq):
        return seq[idx]
    return None


def _watch_progress(ctx, st):
    """While the machine runs, only watch. Progress advancing clears the dead-click
    counter; WORK_STALL_S with no progress while 'working' means the activity read is
    stale or the action never started -- re-engage once per stall window. One action from
    a section boundary, hand over to the boundary watch (see _boundary_watch)."""
    data = ctx.state.data
    comp = st.get("completion", 0)
    mark = data.get("gf_progress_mark")
    now = time.time()
    if mark is None or comp > mark[0]:
        data["gf_progress_mark"] = (comp, now)
        data["gf_dead_clicks"] = 0
    elif now - mark[1] > WORK_STALL_S:
        debug_log.log("gf", f"no progress for {WORK_STALL_S:.0f}s while working -- re-engaging")
        data["gf_progress_mark"] = (comp, now)
        _click_machine(ctx, st, st.get("requiredTool"))
        return
    tool = st.get("requiredTool")
    if tool in TOOL_STATS and st.get("temperatureStatus") == "READY" and \
            actions_left_in_section(comp, st.get("sectionCount", 0), tool) == 1 and \
            _next_tool(st) is not None and data.get("gf_boundary_armed") != (tool, comp):
        data["gf_boundary_armed"] = (tool, comp)
        _boundary_watch(ctx, st, tool)


def _boundary_watch(ctx, st, tool):
    """MEASURED live 2026-09-03 (gf_live7): every section boundary cost 5 quality -- the
    machine keeps acting after the section completes, once more with the WRONG tool,
    before the ordinary poll + mouse flight reaches the pool (wiki: "be ready to click
    away from the machine before the machine ticks again"). So with one action left:
    hover the pool the next tool needs (hotter tool -> lava, colder -> waterfall), poll
    fast, and the instant the required tool flips, right-click Dunk/Quench in place and
    ride the ramp into the new band. Bands never overlap between different heat levels,
    so the direction is known before the flip; a READY read after the flip (same level)
    just engages the next machine."""
    nxt = _next_tool(st)
    hotter = HEAT_LEVEL.get(nxt, 0) > HEAT_LEVEL.get(tool, 0)
    pool = _find_object(LAVA_POOL_ID if hotter else WATERFALL_ID)
    if pool is None:
        return
    controller.hover_entity(ctx.ser, pool, refresh_target=_refresh(pool))
    ctx.state.data["gf_pool_hovered"] = None
    budget = TOOL_STATS[tool]["cycle_ticks"] * TICK_S + BOUNDARY_WATCH_MARGIN_S
    controller.mark_busy(budget + HEAT_ADJUST_TIMEOUT_S)
    debug_log.log("gf", f"boundary watch armed on {tool} at {st.get('completion')} "
                        f"(next {nxt}, {budget:.1f}s)")
    deadline = time.time() + budget
    while time.time() < deadline:
        cur = read_state()
        if cur is not None:
            new_tool = cur.get("requiredTool")
            if new_tool != tool:
                if new_tool not in TOOL_STATS:
                    if cur.get("phase") != "REFINING":
                        return      # sword finished -- the dispatcher takes it from here
                    time.sleep(0.1)
                    continue        # transitional read at the boundary (gf_live25) -- keep polling
                debug_log.log("gf", f"section boundary: {tool} -> {new_tool} at "
                                    f"{cur.get('temperature')} ({cur.get('temperatureStatus')})")
                ctx.state.data["gf_work_tool"] = new_tool
                ctx.state.data.pop("gf_progress_mark", None)
                ctx.state.data["gf_engage_until"] = 0.0
                if cur.get("temperatureStatus") == "READY":
                    _click_machine(ctx, cur, new_tool)
                else:
                    _adjust_heat_and_engage(ctx, cur, new_tool, pool=pool, prehovered=True)
                return
        time.sleep(0.1)
    debug_log.log("gf", f"boundary watch on {tool}: no tool flip within {budget:.1f}s")


def _click_machine(ctx, st, tool, in_place=False):
    """Click the required machine. `in_place` clicks the cursor where it already rests
    (the machine was hovered once the player stood still at the pool) -- the ramp stop
    has ~300ms after a tick-aligned reading before the click risks straddling the next
    tick, and a full click_entity re-resolution (HTTP + settle) measured 0.39-0.69s."""
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
    data["gf_pool_hovered"] = None
    if in_place:
        controller.click_human(ctx.ser)
        ok = True
    else:
        ok = controller.click_entity(ctx.ser, machine, refresh_target=_refresh(machine))
    if ok:
        now = time.time()
        budget = WORK_FIRST_ACTION_S + (machine.get("distance") or 0) * WALK_S_PER_TILE
        data["gf_progress_mark"] = (st.get("completion", 0), now)
        data["gf_engage_until"] = now + budget     # no repeat click inside the commit window
        controller.mark_busy(budget)
    return bool(ok)


def _sleep_to_after_next_tick(ctx):
    """Sleep until just after the next game tick, so the following read is that tick's
    fresh varbit and a click issued on it lands inside the same tick window (the tick
    clock is an accelerator: absent or stale, fall back to a plain poll interval)."""
    events = getattr(ctx, "events", None)
    ticks = getattr(events, "ticks", None) if events is not None else None
    now = time.time()
    if ticks is None or ticks.is_stale(now):
        time.sleep(HEAT_POLL_S)
        return
    time.sleep(ticks.ms_until_next(now) / 1000.0 + POLL_AFTER_TICK_S)


def _adjust_heat_and_engage(ctx, st, tool, pool=None, depth=0, heating=None, prehovered=False):
    """Fast heat/cool (the human uses ONLY Dunk/Quench -- MEASURED), polled every
    HEAT_POLL_S against the live temperature varbit and stopped by ramp projection (see
    ramp_stop_reached) -- and the stop IS the machine click (MEASURED: clicking the next
    machine interrupts the dunk; there is no separate walk-away click). The machine is
    pre-hovered while the ramp runs so the stop is a click in place, one tick of latency.
    `pool` is a pre-resolved pool object (the boundary watch hovers it first)."""
    data = ctx.state.data
    if data.get("gf_heat_fails", 0) >= HEAT_FAIL_LIMIT:
        ctx.state.trigger_safety_stop(
            "giants foundry: heat adjust (lava pool / waterfall) click keeps failing")
        return
    min_t = st.get("minimumTemperature", -1)
    max_t = st.get("maximumTemperature", -1)
    if min_t < 0 or max_t < 0:
        return
    if heating is None:
        heating = st.get("temperatureStatus") == "TOO_COLD"
    actions = actions_left_in_section(st.get("completion", 0), st.get("sectionCount", 0), tool)
    target = heat_stop_target(tool, min_t, max_t, actions)
    if pool is None:
        pool = _find_object(LAVA_POOL_ID if heating else WATERFALL_ID)
    if pool is None:
        return
    option = "Dunk-preform" if heating else "Quench-preform"
    t0 = st.get("temperature", -1)
    if t0 >= 0 and ramp_stop_reached(t0, ramp_aim(target, RAMP_INITIAL_RATE, min_t, max_t, heating),
                                     heating) and min_t <= t0 <= max_t:
        debug_log.log("gf", f"{tool} already at {t0} for target {target} -- no {option} needed")
        _click_machine(ctx, st, tool)
        return
    if not _pool_click(ctx, pool, option, in_place=prehovered):
        data["gf_heat_fails"] = data.get("gf_heat_fails", 0) + 1   # reset on a successful click
        return
    data["gf_heat_fails"] = 0
    data["gf_pool_hovered"] = None
    controller.mark_busy(HEAT_ADJUST_TIMEOUT_S + 2)
    machine = _find_object(TOOL_STATS[tool]["object"])
    hovered = False
    started = time.time()
    deadline = started + HEAT_ADJUST_TIMEOUT_S
    cur = None
    t = -1
    last_t, rate = None, RAMP_INITIAL_RATE
    aim = ramp_aim(target, rate, min_t, max_t, heating)
    while time.time() < deadline:
        cur = read_state()
        if cur is not None:
            t = cur.get("temperature", -1)
            if t >= 0:
                if last_t is not None and abs(t - last_t) >= RAMP_TICK_MIN:
                    rate = min(RAMP_MAX_RATE, max(rate, abs(t - last_t)))   # never slows
                    aim = ramp_aim(target, rate, min_t, max_t, heating)
                    if not hovered and machine is not None:
                        # USER 2026-09-03, watching: "you need to hover after the move,
                        # otherwise the mouse moves away from the object". The first ramp
                        # tick means the player has reached the pool and stands still --
                        # only then does a hovered pixel stay on the machine.
                        hovered = bool(controller.hover_entity(
                            ctx.ser, machine, refresh_target=_refresh(machine)))
                last_t = t
                if ramp_stop_reached(t, aim, heating):
                    break
            if cur.get("requiredTool") != tool:
                break    # section moved under us -- re-decide from the top next tick
        _sleep_to_after_next_tick(ctx)
    debug_log.log("gf", f"heat adjust {option} for {tool}: {t0} -> stop at {t} "
                        f"(aim {aim}, rate {rate:.0f}/tick, target {target}, band {min_t}-{max_t}, "
                        f"{actions} actions left) after {time.time() - started:.1f}s")
    _click_machine(ctx, cur or st, tool, in_place=hovered)
    # Verify the landing: a slow flight can still cost a second tick of ramp, and a
    # machine acting out of band costs quality every action. One bounded correction.
    if depth == 0:
        controller.human_delay(0.7, 0.9)
        landed = read_state()
        if landed and landed.get("requiredTool") == tool and \
                landed.get("temperatureStatus") in ("TOO_COLD", "TOO_HOT"):
            debug_log.log("gf", f"heat adjust landed out of band at "
                                f"{landed.get('temperature')} -- correcting")
            _adjust_heat_and_engage(ctx, landed, tool, depth=depth + 1)


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


def _find_storage():
    # 44633 ("Store-preform")/44634 ("Take-preform") are multiloc IMPOSTOR variants of the
    # base object 44778 -- ObjectProvider reports the BASE id in "id" and the impostor in
    # "impostorId" (see ObjectProvider.buildObjectData). Matching on "id" alone (the old
    # STORAGE_EMPTY_ID/STORAGE_FULL_ID lookup) never found it.
    for obj in controller.get_objects(radius=32):
        if obj.get("id") in PREFORM_STORAGE_IDS or obj.get("impostorId") in PREFORM_STORAGE_IDS:
            return obj
    return None


def _find_kovac():
    for npc in controller.get_npcs():
        if npc.get("id") == KOVAC_NPC_ID or (npc.get("name") or "").lower() == "kovac":
            return npc
    return None


# Moved to dialogue.py 2026-09-18; the "no open field" and "title at index 0" findings that
# were documented here are in its header. Names kept for the call sites and tests below.
_dialogue = dialogue.read
_dialogue_open = dialogue.is_open
_dialogue_options = dialogue.options


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
