# routines/vale_totems.py
#
# Vale Totems (Forestry, Auburn Valley) -- see docs/superpowers/specs/2026-09-08-vale-totems-
# design.md for the full derivation and the recording it came from
# (recordings/2026-09-08_135457_vale-totem.*). Facts repeated here are MEASURED, not guessed.
#
# WHAT THE ACTIVITY IS: eight totem sites ring the Auburn Valley. At each: Claim the offerings
# an ent left, Build a totem (1 log), Carve three animals into it, Decorate it with four
# fletched bows. Ents walk the ring every 80 ticks (48s) leaving offerings at completed totems,
# and each visit costs the totem one durability until it collapses and needs rebuilding. The
# three carvings must match the three spirits currently standing at that site -- of five spirit
# variants, only three are active at once, and per the wiki (and seen once in the recording)
# they can change WHILE you are carving, so which spirits are missing must be re-derived from a
# fresh read before every carve rather than decided once. Per complete totem: 5 logs (1 build +
# 4 fletched into bows for the decoration); 40 logs for a full lap of all eight sites.
#
# THE 270-TITLE TRAP: the carve prompt and the fletch prompt are BOTH widget group 270
# (skillmulti's Skillmulti.if3) -- the site base loc's own action cycles Build -> Carve ->
# Decorate on one object id, and the last two share both an object id AND a widget group, so
# neither can tell the two prompts apart. The only discriminator is the TITLE at widget 270.5:
# "What animal would you like to carve?" is the carve prompt (options A..E are the five
# animals in a fixed order, so option n carves the spirit whose nearbyValue is n); "What would
# you like to make?" is the fletch prompt. skillmulti.py (shared with routines/fletching.py)
# carries TITLE_CARVE/TITLE_MAKE for exactly this gate. Answering blind (Space repeats the last
# item crafted, not necessarily a bow) fails far from its cause -- see that module's docstring.
#
# NO ROUTER: worldmap.py has NO usable collision data anywhere in Varlamore. MEASURED
# 2026-09-08 -- reachable_from(radius=40) answers a single tile at every totem site, at the
# bank, at Civitas illa Fortis, at Aldarin and at the Hunter guild, and find_path between any
# two sites answers None. vale_totem_route.py's waypoints are the human's own recorded path,
# not a computed route, and they only ever connect sites ON the ring to each other and to the
# bank -- there is no way to walk from an arbitrary tile back into that ring. So a player found
# outside the valley cannot be walked home by this routine at all; the only safe response is a
# safety stop asking the user to start it standing in the valley.
import time

import banker
import controller
import debug_log
import outcome_waits
import skillmulti
import travel
import vale_totems as vt
import vale_totem_route as route

LOG = "vale_totems"

# Per wood: the inventory log name, the inventory name of the unstrung bow used to decorate,
# and the Fletching level the wiki requires to build that totem. The skillmulti MENU label is
# derived from the bow name by skillmulti.menu_label -- the menu names the finished item
# ("Yew longbow"), the inventory receives the unstrung one ("Yew longbow (u)").
WOODS = {
    "OAK":     {"log": "Oak logs",     "bow": "Oak longbow (u)",     "level": 20},
    "WILLOW":  {"log": "Willow logs",  "bow": "Willow longbow (u)",  "level": 35},
    "MAPLE":   {"log": "Maple logs",   "bow": "Maple longbow (u)",   "level": 50},
    "YEW":     {"log": "Yew logs",     "bow": "Yew longbow (u)",     "level": 65},
    "MAGIC":   {"log": "Magic logs",   "bow": "Magic longbow (u)",   "level": 80},
}

DEFAULT_CONFIG = {"wood": "YEW"}

_now = time.monotonic
_sleep = time.sleep


def get_config(bridge_config):
    """Resolve which wood to totem with -- the bridge's `valeTotemsWood` wins over the
    default, and an unrecognised value falls back rather than crashing later at the bow menu."""
    wood = (bridge_config or {}).get("valeTotemsWood") or DEFAULT_CONFIG["wood"]
    wood = str(wood).upper()
    if wood not in WOODS:
        debug_log.log(LOG, "unknown wood %r -- falling back to %s"
                      % (wood, DEFAULT_CONFIG["wood"]))
        wood = DEFAULT_CONFIG["wood"]
    cfg = dict(WOODS[wood])
    cfg["wood"] = wood
    return cfg


def read_state():
    """The whole minigame in one request. None means "cannot verify", never "nothing to do"."""
    return vt.parse_state(controller.get_api("minigames", "vale-totems"))


def setup(ctx):
    debug_log.log(LOG, "setup: %s totems, circuit %s"
                  % (ctx.config["wood"], "->".join(str(n) for n in route.RING)))


def tick(ctx):
    state = read_state()
    if state is None:
        debug_log.log(LOG, "no vale-totems reader -- is the client up?")
        return
    if not state.at_vale_totems:
        ctx.state.trigger_safety_stop(
            "not in the Auburn Valley. This routine has no router -- worldmap has no "
            "collision data for Varlamore -- so it cannot walk back. Start it in the valley.")
        return
    # Later tasks add the ladder here.


CLAIM_BUDGET_S = 6.0
BUILD_BUDGET_S = 6.0


def site_object(site_number: int, which: str):
    """The totem base or the offerings pile at `site_number`, by ID on its own known tile.

    BY ID, NEVER BY NAME: the base multiloc is called "Totem site" before it is built and
    "Yew totem" both while it needs carving AND while it needs decorating, so the name cannot
    separate the last two states -- the Wintertodt brazier lesson. The tile and the id are both
    fixed and measured, so /object/at answers exactly one thing.
    """
    entry = route.SITES[site_number]
    x, y = entry["totem"] if which == "totem" else entry["offerings"]
    obj_id = entry["base_id"] if which == "totem" else entry["offerings_id"]
    return controller.get_object_at(x, y, route.PLANE, obj_id)


def _click_site(ctx, site_number, which, budget_s, confirm):
    """Click a site object and confirm on the READER, never on the click landing.

    The aim is re-resolved through object_refresh_target for the whole flight: these clicks are
    issued while the player is still walking up, and a click that departs toward a tick-old
    projection is the "flick back" CLAUDE.md documents."""
    entry = route.SITES[site_number]
    x, y = entry["totem"] if which == "totem" else entry["offerings"]
    obj_id = entry["base_id"] if which == "totem" else entry["offerings_id"]
    obj = site_object(site_number, which)
    if not obj:
        debug_log.log(LOG, "site %d: %s object not in view" % (site_number, which))
        return False
    refresh = controller.object_refresh_target(x, y, route.PLANE, obj_id)
    if not controller.click_entity(ctx.ser, obj, refresh_target=refresh):
        return False
    outcome = outcome_waits.await_pre(ctx, ["VARBIT_CHANGED", "CHAT_MESSAGE"], budget_s,
                                      confirm, now_fn=_now, sleep_fn=_sleep)
    return bool(outcome)


def _site_now(site_number):
    state = read_state()
    return None if state is None else state.sites.get(site_number)


def claim(ctx, site) -> bool:
    """Take the offerings pile. Confirmed on the pile reading empty."""
    n = site.number

    def done():
        fresh = _site_now(n)
        return fresh is not None and fresh.unclaimed_offerings == 0

    return _click_site(ctx, n, "offerings", CLAIM_BUDGET_S, done)


def build(ctx, site) -> bool:
    """Erect a totem. Confirmed on baseValue leaving 0 -- one log, and 15% run energy back."""
    n = site.number

    def done():
        fresh = _site_now(n)
        return fresh is not None and fresh.base_value != 0

    return _click_site(ctx, n, "totem", BUILD_BUDGET_S, done)


CARVE_MENU_BUDGET_S = 4.0
CARVE_BUDGET_S = 6.0


def _await_menu(ctx) -> bool:
    """Wait for the CARVE prompt specifically."""
    outcome = outcome_waits.await_pre(
        ctx, ["WIDGET_LOADED"], CARVE_MENU_BUDGET_S,
        lambda: skillmulti.showing(skillmulti.TITLE_CARVE),
        now_fn=_now, sleep_fn=_sleep)
    return bool(outcome)


def choose_spirit(ctx, spirit: str) -> bool:
    """Pick `spirit` off the carve menu.

    TWO SAFEGUARDS, both load-bearing:

    1. The TITLE GATE. The carve prompt and the fletch prompt are the SAME widget group (270),
       so a number pressed without checking 270.5's text can answer the wrong one -- fletching
       a bow when we meant to carve, or carving when we meant to fletch. An unreadable title
       counts as "not the carve prompt" and refuses: withholding costs a tick, guessing costs
       a wrong carve, and a wrong animal is -25% XP and far fewer offerings for the whole totem.

    2. The number is DERIVED FROM THE MATCHED WIDGET, never from a table. skillmulti.item_widget
       finds the option by name and option_number turns its child into the digit, so the key
       stays tied to the animal's NAME. That is fletching.py's own rule ("the human's '3' is
       only correct for the product they happened to pick") applied to this menu.
    """
    if not skillmulti.showing(skillmulti.TITLE_CARVE):
        debug_log.log(LOG, "carve menu not on screen (title=%r) -- withholding"
                      % skillmulti.prompt_title())
        return False
    widget = skillmulti.item_widget(spirit.capitalize())
    if not widget:
        debug_log.log(LOG, "carve menu has no %s option" % spirit)
        return False
    option = skillmulti.option_number(widget)
    if option is not None:
        controller.key_tap(ctx.ser, str(option))
        return True
    return bool(controller.click_entity(ctx.ser, widget, offset=(0, 0),
                                        check_ui_blockers=False, simulate_mistakes=False))


ARRIVAL_TOLERANCE = 4


def at_site(site_number: int) -> bool:
    player = controller.get_player() or {}
    x, y = player.get("x"), player.get("y")
    if x is None or y is None:
        return False
    sx, sy = route.SITES[site_number]["stand"]
    return abs(x - sx) <= ARRIVAL_TOLERANCE and abs(y - sy) <= ARRIVAL_TOLERANCE


def walk_leg(ctx, a: int, b: int) -> bool:
    """Walk site `a` -> site `b` by hopping the recorded waypoints.

    NO ROUTER: worldmap has no collision data for Varlamore, so travel.walk_to cannot be used
    here at all. These are the human's own tick-by-tick positions, so the path is known-good;
    the risk is only that a discrete hop lands differently than a continuous run, which is what
    the live checklist watches at the two log balances.
    """
    points = route.leg_waypoints(a, b)
    if not points:
        debug_log.log(LOG, "no recorded path for leg %d->%d" % (a, b))
        return False
    for x, y in points:
        if not travel._hop_to(ctx, x, y, route.PLANE):
            debug_log.log(LOG, "leg %d->%d: hop to (%d,%d) failed" % (a, b, x, y))
            return False
    return True


def carve(ctx, site) -> bool:
    """Carve ONE spirit into the totem.

    Re-reads the site immediately before choosing, because the wiki says the nearby trio can
    change WHILE the player is carving -- so the spirit picked at build time may already be
    wrong by the third carve. One carve per call; the caller loops on needs_carve.
    """
    n = site.number
    fresh = _site_now(n) or site
    missing = vt.missing_spirits(fresh)
    if not missing:
        return False
    spirit = missing[0]

    obj = site_object(n, "totem")
    if not obj:
        debug_log.log(LOG, "site %d: totem not in view" % n)
        return False
    entry = route.SITES[n]
    refresh = controller.object_refresh_target(entry["totem"][0], entry["totem"][1],
                                              route.PLANE, entry["base_id"])
    if not controller.click_entity(ctx.ser, obj, refresh_target=refresh):
        return False
    if not _await_menu(ctx):
        debug_log.log(LOG, "site %d: carve menu never opened" % n)
        return False
    if not choose_spirit(ctx, spirit):
        return False

    before = set(fresh.segment_values)

    def landed():
        after = _site_now(n)
        return after is not None and set(after.segment_values) != before

    return bool(outcome_waits.await_pre(ctx, ["VARBIT_CHANGED", "CHAT_MESSAGE"],
                                        CARVE_BUDGET_S, landed,
                                        now_fn=_now, sleep_fn=_sleep))


DECORATE_BUDGET_S = 6.0
FLETCH_MENU_BUDGET_S = 4.0


def count_item(name: str) -> int:
    """Slots holding `name`. Counts SLOTS, not distinct names -- 11 logs is 11 slots."""
    want = name.lower()
    return sum(1 for i in (controller.get_inventory() or [])
               if (i.get("name") or "").lower() == want)


def find_item(name: str):
    want = name.lower()
    for item in controller.get_inventory() or []:
        if (item.get("name") or "").lower() == want:
            return item
    return None


def decorate(ctx, site) -> bool:
    """Attach one decoration. Confirmed on the reader's decoration count rising."""
    n = site.number
    before = site.decorations

    def landed():
        fresh = _site_now(n)
        return fresh is not None and fresh.decorations > before

    return _click_site(ctx, n, "totem", DECORATE_BUDGET_S, landed)


def start_fletching(ctx) -> bool:
    """Knife a log and pick the bow, then RETURN -- the walk overlaps the fletching.

    MEASURED 2026-09-08: fletching continues while running (five bows delivered across a
    30-tile sprint), one product every 1.8s. So this is fired as the last act at a site and the
    leg walk runs underneath it; nothing waits here.

    NEVER `Space`: it repeats the last crafted item, which is session state we do not own.
    The option is matched by NAME and its digit derived from the matched widget, so a wood
    whose menu differs cannot silently make the wrong thing.
    """
    knife = find_item("Knife")
    log = find_item(ctx.config["log"])
    if not knife or not log:
        return False
    controller.open_inventory_tab(ctx.ser)
    if not controller.click_entity(ctx.ser, knife, offset=(0, 0), check_ui_blockers=False,
                                   simulate_mistakes=False, expected_action="Use"):
        return False
    if not controller.click_entity(ctx.ser, log, offset=(0, 0), check_ui_blockers=False,
                                   simulate_mistakes=False):
        return False
    outcome = outcome_waits.await_pre(
        ctx, ["WIDGET_LOADED"], FLETCH_MENU_BUDGET_S,
        lambda: skillmulti.showing(skillmulti.TITLE_MAKE),
        now_fn=_now, sleep_fn=_sleep)
    if not outcome:
        debug_log.log(LOG, "make menu never opened (title=%r)" % skillmulti.prompt_title())
        return False
    widget = skillmulti.item_widget(ctx.config["bow"])
    if not widget:
        debug_log.log(LOG, "make menu has no %s option" % ctx.config["bow"])
        return False
    option = skillmulti.option_number(widget)
    if option is not None:
        controller.key_tap(ctx.ser, str(option))
        return True
    return bool(controller.click_entity(ctx.ser, widget, offset=(0, 0),
                                        check_ui_blockers=False, simulate_mistakes=False))


# Cache: the shut basket is "Log basket" (28140) and the open one "Open log basket" (28142).
BASKET_NAMES = ("Log basket", "Open log basket")
BANK_ROUNDS = 3          # recording: withdraw/fill three times -> ~28 in the basket, ~23 loose
BASKET_BUDGET_S = 4.0


def find_basket():
    for item in controller.get_inventory() or []:
        if (item.get("name") or "") in BASKET_NAMES:
            return item
    return None


def _basket_option(ctx, options, expect_change) -> bool:
    """Select a NAMED option on the basket, never a bare left click.

    The basket offers Fill / Wear / Check / Open|Close / Destroy and a bare click races them --
    and Fill is a SILENT NO-OP that looks like success. banker.empty_log_basket already selects
    by name for exactly this reason, and `options` is passed as a SEQUENCE for the same reason
    it does: click_menu_action takes one, tries each against the single menu it reads back, and
    the exact wording is the thing the live checklist has to confirm.

    Note click_menu_action has no simulate_mistakes parameter -- it reads the real menu and
    clicks a named entry, so there is no aim to miss."""
    basket = find_basket()
    if not basket:
        return False
    controller.open_inventory_tab(ctx.ser)
    if not controller.click_menu_action(ctx.ser, basket, options, offset=(0, 0),
                                        check_ui_blockers=False):
        return False
    return bool(outcome_waits.await_pre(ctx, ["INVENTORY_CHANGED"], BASKET_BUDGET_S,
                                        expect_change, now_fn=_now, sleep_fn=_sleep))


def empty_basket(ctx) -> bool:
    """Basket -> inventory, in the field ("You empty as many logs as you can carry.")."""
    before = count_item(ctx.config["log"])
    return _basket_option(ctx, ("empty", "empty-to-inventory"),
                          lambda: count_item(ctx.config["log"]) > before)


def fill_basket(ctx) -> bool:
    """Inventory -> basket, at the bank."""
    before = count_item(ctx.config["log"])
    return _basket_option(ctx, ("fill",),
                          lambda: count_item(ctx.config["log"]) < before)


def bank_trip(ctx) -> bool:
    """Withdraw-All logs, close, Fill the basket -- three rounds, the recording's own sequence.

    "You don't have enough inventory space to withdraw that many." is NORMAL here and must not
    be treated as a failure; fletching.py records the same thing for the same click.
    """
    entry = route.BANK
    booth = controller.get_object_at(entry["tile"][0], entry["tile"][1], route.PLANE,
                                     entry["object_id"])
    if not booth:
        debug_log.log(LOG, "bank booth not in view")
        return False
    for round_index in range(BANK_ROUNDS):
        if not banker.open_bank(ctx, booth):
            return False
        if not banker.withdraw_all_shift(ctx, ctx.config["log"], fresh=True):
            return False
        controller.key_tap(ctx.ser, "escape")
        if round_index < BANK_ROUNDS - 1:
            fill_basket(ctx)
    return True
