# routines/amethyst_dart_tips.py
#
# Mine Amethyst until the inventory is full, chisel it all into dart tips (bolt tips once the
# dart tip stack passes a cap), repeat forever. No banking, no travel: start it standing at an
# Amethyst mining spot with a chisel in your inventory.
#
# USER 2026-09-17, verbatim: "use the gatherer to mine Amethyst crystals until we have full
# inventory then use a chisel on Amethyst that accumulates in our inventory. select dart tips
# from the dialog menu that pops up. or even easier just press 4. wait until all amethyst is
# gone then start mining again" -- and, once the design was approved: "once we have accumulated
# 5k darttips swap to bolt tips".
#
# Mining reuses gathering.py's shared loop (see that module's header for the measured
# reaction/full-inventory behaviour): most swings land with the vein still up (an ore lands and
# gathering.gather re-arms on the SAME vein), but a vein CAN fully deplete too -- see the FIRST
# LIVE SESSION note below, which corrects gathering.py's "amethyst keeps giving" assumption.
# Either way, gathering.gather already handles both outcomes. This routine's own tick() decides
# mine vs. chisel from the LIVE inventory size every call (the same "never remembered" idiom
# routines/fletching.py uses to survive a mid-batch restart, see tick()'s own docstring) --
# `profile.full_inventory="stop"` below is therefore a fail-safe that should never actually
# fire, not the real full-inventory handling.
#
# The chisel-on-amethyst half is fletching.py's start_fletching/_wait_out_batch pattern (two
# inventory clicks -> skillmulti make-menu -> a digit keypress -> poll until the raw material
# is gone), minus the AFK tab-out: this routine never leaves the mining spot, so there is
# nothing to tab away from.
#
# FIRST LIVE SESSION (2026-09-17): the rock name and the mined item's own name were both wrong
# on the first guess -- see the CONFIRMED comments on rockNames/chiselName in DEFAULT_CONFIG
# below. A vein was also seen to fully deplete once (contra gathering.py's generic gem-rock
# comment), and Amethyst's per-swing success rate turned out slow/variable enough to need its
# own, longer gather_budget_s -- see GATHER_BUDGET_S below, USER: "we swap before the ore
# despawn". The make-menu's product labels and digit positions (primaryProduct/secondaryProduct/
# their *Option fallbacks) were confirmed correct on the very first chisel batch -- `key_tap 4`
# resolved "Amethyst dart tip" by NAME, not the digit fallback.
#
# Run with:
#     python controller.py routines/amethyst_dart_tips.py

import time

import controller
import debug_log
import gathering
import skillmulti

DEFAULT_CONFIG = {
    "enabled": True,
    # CONFIRMED live 2026-09-17 against the running bridge's /objects: the rock's own display
    # name is "Amethyst crystals" -- the mined ITEM (ORE_NAME below) is the one that's plain
    # "Amethyst". Easy to conflate; they are two different strings.
    "rockNames": ["Amethyst crystals"],
    "maxRockDistance": 10,
    # CONFIRMED live 2026-09-17 against /inventory: this account's chisel is a "Jeweller's
    # chisel" (id 34024), not the plain "Chisel" every other chisel-using routine in this repo
    # reaches for -- a different item entirely, gem-cutting specific.
    "chiselName": "Jeweller's chisel",

    # `primaryProduct`/`secondaryProduct` are matched on the make-menu by NAME (skillmulti.py);
    # the `*Option` fields are only the fallback digit keypress for when that match fails --
    # USER: "select dart tips from the dialog menu that pops up. or even easier just press 4."
    "primaryProduct": "Amethyst dart tip",
    "primaryProductOption": 4,
    # USER: "once we have accumulated 5k darttips swap to bolt tips" -- read off the live
    # inventory stack, not a running total, so a restart mid-run still answers correctly.
    "primaryProductCap": 5000,
    "secondaryProduct": "Amethyst bolt tips",
    "secondaryProductOption": 3,

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
        # OFF: a full inventory is this routine's normal trigger to start chiseling, not a
        # safety condition -- see tick()'s docstring. Same rule gathering.py and fletching.py
        # force for the same reason (check_safety runs before tick() and would otherwise win
        # the race).
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}

ORE_NAME = "Amethyst"   # the mined item's inventory name -- see the UNVERIFIED note above

# LIVE 2026-09-17: gathering.py's generic gem-rock comment ("amethyst keeps giving after an
# ore") does not hold here -- a vein was observed to fully deplete (OBJECT_DESPAWNED) after
# 10.76s, same as any ordinary ore rock. gathering.gather already handles that fine (it just
# re-picks the next vein). The real issue that DID need a fix: Amethyst's per-swing success
# rate is slow and variable enough (while /combat stayed isIdle=False the whole time -- the
# player was genuinely still swinging, not stuck) that the generic GATHER_BUDGET_S (12s)
# repeatedly timed out and re-clicked a vein that had not actually despawned -- USER, watching
# it live: "we swap before the ore despawn", then, after widening to 30s: "still reclicks for
# no reason". MEASURED: successive live sessions produced dry spells of 17.4s, 31.1s and then
# 62.3s -- each widening bought a session or two before a longer one appeared, which is the
# signature of a genuinely long-tailed wait, not an outlier to clear with one more guess. A flat
# elapsed-time ceiling cannot distinguish "still working, just unlucky" from "stuck" for a
# resource whose legitimate gaps run this long -- the SUSTAINED-idle check already can (it needs
# /combat.isIdle to read True continuously for IDLE_GRACE_S + IDLE_CONFIRM_S, ~4.5s, real
# evidence of a dead click, not a guess about elapsed time), and it fires independently of this
# constant. So this is set high enough to functionally defer to that check -- a last-resort
# circuit breaker against a truly stuck state, not the everyday failure detector.
GATHER_BUDGET_S = 300.0

PROFILE = gathering.Profile(
    key="amethyst_dart_tips",
    skill="Mining",
    action="mine",
    xp_field="miningXp",
    names_key="rockNames",
    distance_key="maxRockDistance",
    noun="rock",
    keep_items=("pickaxe", "chisel"),
    # No bridge section of its own (see get_config) -- resolved at call time only so a test can
    # swap it out, matching mining.py's own comment.
    config_fetch=lambda: None,
    default_config=DEFAULT_CONFIG,
    gather_budget_s=GATHER_BUDGET_S,
    # Fail-safe only -- tick() never calls gathering.tick() with a full inventory in the first
    # place, so gathering.py's own full-inventory check is not expected to be the thing that
    # notices. See the module docstring.
    full_inventory="stop",
    panel_section="Amethyst Mining",
)

MENU_OPEN_BUDGET_S = 4.0        # fletching.py's measured budget for the same make-menu
FIRST_PRODUCT_BUDGET_S = 6.0
PRODUCT_STALL_S = 8.0           # no progress for this long -> the batch really stopped
CHISEL_MAX_S = 180.0            # a ceiling, not a budget
CHISEL_POLL_S = 0.5
START_FAIL_LIMIT = 4            # consecutive failed batch starts before a safety stop

# Test seams -- the suite installs a fake clock so budgets expire instantly (blast_furnace's
# _now/_sleep convention, same as fletching.py).
_now = time.monotonic
_sleep = time.sleep


def get_config(bridge_config):
    """This routine has no sidebar section of its own (it is not wired into the Controller
    Settings panel -- command-line only for now, see the module docstring), so the bridge's
    generic config only ever fills in breaks/safety on top of DEFAULT_CONFIG."""
    merged = dict(DEFAULT_CONFIG)
    if bridge_config:
        merged.update(bridge_config)
    merged["safety"] = dict(DEFAULT_CONFIG["safety"], **(merged.get("safety") or {}))
    merged["safety"]["stopOnInventoryFull"] = False
    return merged


def _inv():
    return controller.get_inventory() or []


def count(inv, name):
    """Slot-count by exact name, summed so a stackable product (dart/bolt tips) counts
    correctly and an unstackable one (Amethyst, the chisel) still just adds up to its slots."""
    name = (name or "").lower()
    return sum(int(i.get("qty") or 1) for i in inv
               if (i.get("name") or "").lower() == name)


def _find(inv, name):
    name = (name or "").lower()
    return next((i for i in inv if (i.get("name") or "").lower() == name), None)


def _inventory_full():
    return len(_inv()) >= getattr(controller, "INVENTORY_SIZE", 28)


def current_product(inv, config):
    """'Dart tips' until the stack reaches the cap, then 'Bolt tips' for good -- USER: "once we
    have accumulated 5k darttips swap to bolt tips". Returns (product_name, fallback_option)."""
    primary = config["primaryProduct"]
    if count(inv, primary) < config["primaryProductCap"]:
        return primary, config.get("primaryProductOption")
    return config["secondaryProduct"], config.get("secondaryProductOption")


# Aliases for the skillmulti module functions -- same convention routines/fletching.py uses.
menu_item_widget = skillmulti.item_widget
menu_option_number = skillmulti.option_number
menu_is_open = skillmulti.is_open


def _confirm(check, budget_s, poll_s=0.2):
    deadline = _now() + budget_s
    while _now() < deadline:
        if check():
            return True
        _sleep(poll_s)
    return check()


def start_chiseling(ctx, config):
    """Chisel -> Amethyst -> pick the current product off the skillmulti menu. True once a
    product has actually appeared in the inventory, so the caller knows the batch is running."""
    inv = _inv()
    chisel = _find(inv, config["chiselName"])
    amethyst = _find(inv, ORE_NAME)
    if chisel is None or amethyst is None:
        debug_log.log("amethyst_dart_tips",
                      "need both {0} and {1} in the inventory".format(config["chiselName"], ORE_NAME))
        return False

    product, product_option = current_product(inv, config)
    before = count(inv, product)

    # Two inventory-interface clicks, so both opt out of the UI-blocker check and neither
    # simulates a misclick -- fletching.py's rule for every interface click.
    controller.click_entity(ctx.ser, chisel, offset=(0, 0),
                            check_ui_blockers=False, simulate_mistakes=False)
    controller.click_entity(ctx.ser, amethyst, offset=(0, 0),
                            check_ui_blockers=False, simulate_mistakes=False)

    if not _confirm(menu_is_open, MENU_OPEN_BUDGET_S):
        debug_log.log("amethyst_dart_tips", "the make menu never opened -- the use-on didn't land")
        return False

    widget = menu_item_widget(product)
    option = menu_option_number(widget)
    if option is not None:
        controller.key_tap(ctx.ser, str(option))
    elif widget is not None:
        controller.click_entity(ctx.ser, widget, offset=(0, 0),
                                check_ui_blockers=False, simulate_mistakes=False)
    else:
        option = int(product_option or 0)
        if option < 1:
            debug_log.log("amethyst_dart_tips",
                          "no '{0}' on the make menu and no fallback option key configured"
                          .format(product))
            return False
        debug_log.log("amethyst_dart_tips",
                      "no '{0}' button on the make menu -- pressing {1}".format(product, option))
        controller.key_tap(ctx.ser, str(option))

    started = _confirm(lambda: count(_inv(), product) > before, FIRST_PRODUCT_BUDGET_S)
    if not started:
        debug_log.log("amethyst_dart_tips",
                      "no {0} after picking it -- the selection didn't take".format(product))
    return started


def _wait_out_chiseling(ctx, config):
    """Poll until the Amethyst runs out, the batch stalls, or CHISEL_MAX_S. Always sends fidget
    input while it waits -- unlike fletching.py's bank-side wait, this routine never tabs away."""
    deadline = _now() + CHISEL_MAX_S
    last_progress = _now()
    left = count(_inv(), ORE_NAME)

    while _now() < deadline:
        if ctx.state.safety_stopped:
            return "stopped"
        _sleep(CHISEL_POLL_S)
        now_left = count(_inv(), ORE_NAME)
        if now_left == 0:
            return "done"
        if now_left < left:
            left, last_progress = now_left, _now()
        elif _now() - last_progress >= PRODUCT_STALL_S:
            return "stalled"
        controller.fidget_once()
    return "timeout"


def chisel_phase(ctx, config):
    """Start a batch, then wait it out. A start failure is a ceiling, not just a log line -- an
    unbounded retry with the inventory stuck full is the silent loop this codebase keeps paying
    for (fletching.py's identical reasoning)."""
    if not start_chiseling(ctx, config):
        fails = ctx.state.data.get("adt_start_fails", 0) + 1
        ctx.state.data["adt_start_fails"] = fails
        if fails >= START_FAIL_LIMIT:
            ctx.state.trigger_safety_stop(
                "amethyst_dart_tips: could not start chiseling in {0} tries -- check chiselName "
                "and that {1} is actually in the inventory".format(fails, ORE_NAME))
        return
    ctx.state.data["adt_start_fails"] = 0

    result = _wait_out_chiseling(ctx, config)
    debug_log.log("amethyst_dart_tips", "chisel batch {0}".format(result))


def mine_phase(ctx, config):
    gathering.tick(PROFILE, ctx)


def tick(ctx):
    """Mine while there's room, chisel once full -- derived from the live inventory every tick,
    the same way fletching.py resumes a restart: there is no ctx.state.data phase to desync."""
    config = ctx.state.config
    if _inventory_full():
        chisel_phase(ctx, config)
    else:
        mine_phase(ctx, config)
