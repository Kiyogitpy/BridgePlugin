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
import bank_memory

LOG = "vale_totems"

# Per wood: the inventory log name, the inventory name of the unstrung bow used to decorate,
# and the Fletching level the wiki requires to build that totem. The skillmulti MENU label is
# derived from the bow name by skillmulti.menu_label -- the menu names the finished item
# ("Yew longbow"), the inventory receives the unstrung one ("Yew longbow (u)").
WOODS = {
    "OAK":     {"log": "Oak logs",     "bow": "Oak longbow (u)",     "strung": "Oak longbow",     "level": 20},
    "WILLOW":  {"log": "Willow logs",  "bow": "Willow longbow (u)",  "strung": "Willow longbow",  "level": 35},
    "MAPLE":   {"log": "Maple logs",   "bow": "Maple longbow (u)",   "strung": "Maple longbow",   "level": 50},
    "YEW":     {"log": "Yew logs",     "bow": "Yew longbow (u)",     "strung": "Yew longbow",     "level": 65},
    "MAGIC":   {"log": "Magic logs",   "bow": "Magic longbow (u)",   "strung": "Magic longbow",   "level": 80},
}

DEFAULT_CONFIG = {"wood": "YEW"}

# USER 2026-09-09: "When we run out of yew logs swap to maple logs". The bank memory says
# when the current wood's logs are gone, and the routine steps down one wood; the totems
# already standing keep their own wood, so bows are matched to each SITE's wood below.
WOOD_FALLBACK = {"MAGIC": "YEW", "YEW": "MAPLE", "MAPLE": "WILLOW", "WILLOW": "OAK"}
WOOD_KEY = "vt_wood"


def _apply_wood(ctx, wood: str) -> None:
    cfg = ctx.state.config
    cfg.update(WOODS[wood])
    cfg["wood"] = wood
    ctx.state.data[WOOD_KEY] = wood


def _wood_override(ctx) -> None:
    """The engine re-resolves the config from the bridge, which would put the panel's wood
    back; the switch lives in data and is re-applied at the top of every tick."""
    wood = ctx.state.data.get(WOOD_KEY)
    if wood and ctx.state.config.get("wood") != wood:
        _apply_wood(ctx, wood)


def _bows_for(ctx, wood=None):
    """(unstrung, strung) names for `wood`, default the wood being worked."""
    if wood in WOODS:
        entry = WOODS[wood]
    else:
        entry = ctx.state.config
    return entry["bow"], entry["strung"]

# The engine posts these on load and after every login, and the bridge pushes VARBIT_CHANGED
# for SUBSCRIBED IDS ONLY (EventsProvider.java). Without them every await_pre below waits its
# whole budget for a signal that is never sent and confirms only on the HTTP re-read at the
# deadline -- roughly 45s of dead time per site, visible as slowness and nothing else, since
# each wait still returns the right answer.
#
# MEASURED 2026-09-08 with javap over runelite-api 1.12.38's VarbitID: site 1's block starts at
# ENT_TOTEMS_SITE_1_BASE = 17611 and the blocks are 18 apart (SITE_2_BASE 17629, SITE_3_BASE
# 17647 ... SITE_8_BASE 17737, and the m8aq reader's own SITE_VARBIT_COUNT is 18). NOTE the
# final-review brief said 17613: that id is ENT_TOTEMS_SITE_1_BASE_MULTILOC, two along, and
# subscribing from it would have missed BASE and BASE_CARVED -- the two the build confirm
# depends on -- while picking up two ids past the block instead.
#
# base..base+11 is exactly what parse_state reads: base (wood + built), +1 BASE_CARVED,
# +2 MULTILOC, +3/+4/+5 the LOW/MID/TOP segments, +6 DECORATIONS, +7/+8/+9 the nearby spirit
# trio, +10 DECAY, +11 POINTS (the unclaimed offerings pile). +12.. are the multi-animal flags
# nothing here reads.
SITE_1_BASE_VARBIT = 17611
SITE_VARBIT_STRIDE = 18
SITE_VARBIT_SPAN = 12

VARBIT_SUBSCRIPTIONS = tuple(
    SITE_1_BASE_VARBIT + SITE_VARBIT_STRIDE * (number - 1) + offset
    for number in range(1, 9)
    for offset in range(SITE_VARBIT_SPAN))

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
    # MERGE, do not replace. The engine reads breaks/safety out of the SAME dict a routine's
    # get_config returns (ControllerState.break_due, check_safety), so returning only the four
    # wood keys hands it a config with no breaks block at all -- and before_break can then
    # never fire. routines/fletching.py:145-153 merges for exactly this reason; the wood keys
    # go on top so the bridge cannot overwrite what we just resolved.
    cfg = dict(bridge_config or {})
    cfg.update(WOODS[wood])
    cfg["wood"] = wood
    return cfg


def read_state():
    """The whole minigame in one request. None means "cannot verify", never "nothing to do"."""
    return vt.parse_state(controller.get_api("minigames", "vale-totems"))


def setup(ctx):
    """CONFIG IS NOT AVAILABLE HERE. The engine calls setup() at controller.py:3540, BEFORE
    resolve_config() populates ctx.state.config at :3545 -- so a setup that reads the wood
    raises AttributeError on the first load however it spells the lookup. Log the circuit,
    which is static data, and let tick() read the config."""
    debug_log.log(LOG, "setup: circuit %s"
                  % "->".join(str(n) for n in route.RING))


def tick(ctx):
    _wood_override(ctx)
    state = read_state()
    if state is None:
        debug_log.log(LOG, "no vale-totems reader -- is the client up?")
        return
    if not state.at_vale_totems:
        ctx.state.trigger_safety_stop(
            "not in the Auburn Valley. This routine has no router -- worldmap has no "
            "collision data for Varlamore -- so it cannot walk back. Start it in the valley.")
        return

    data = ctx.state.data

    # WHICH SITE FIRST, because the stall watch is scoped to it (see progress_signature).
    current = data.get("vt_site")
    if current is None:
        current = nearest_site()
        if current is None:
            debug_log.log(LOG, "not within %d tiles of any totem site -- standing by. There is "
                               "no router for Varlamore, so start this routine at a site."
                          % SITE_SEARCH_RADIUS)
            return
        data["vt_site"] = current

    signature = progress_signature(state, current)
    if signature == data.get("vt_signature"):
        data["vt_stalled"] = data.get("vt_stalled", 0) + 1
        if data["vt_stalled"] >= STALL_STOP_TICKS:
            ctx.state.trigger_safety_stop(
                "no progress at site %d in %d ticks" % (current, STALL_STOP_TICKS))
            return
    else:
        data["vt_signature"] = signature
        data["vt_stalled"] = 0

    if not at_site(current):
        _walk_to_site(ctx, current)
        return
    data["vt_leg_tries"] = 0

    log_name = ctx.state.config["log"]

    # Top up from the basket BEFORE the site: the basket already holds logs we are carrying, so
    # this costs no walk, and a totem that could be built this visit otherwise waits a whole
    # lap. This is NOT the bank check -- that sits below work_site, see there.
    #
    # BELOW A TOTEM'S WORTH PLUS SPARE, not only at zero. USER 2026-09-09: "i just see it
    # running with full log basket empty inventory? better to empty so we can craft some
    # bows" -- the leg's fletch needs loose logs and the build reserve keeps some back, so
    # a basket left full while the inventory sits at one or two logs makes no bows at all.
    # A basket KNOWN to be empty is skipped (one shift-click saved per site); an unknown one
    # is tried, and the first empty teaches the estimate.
    if (count_item(log_name) < LOOSE_LOG_TARGET and find_basket()
            and (data.get(BASKET_LOGS_KEY) is None or basket_logs(data) > 0)):
        empty_basket(ctx)

    # STAND STILL FIRST. See SITE_SETTLE_S -- every action at this site aims at a model a few
    # pixels wide, and a target sliding under the cursor is the difference between "Claim" and
    # "Walk here". A timeout here is not a failure: work_site simply proceeds.
    # ...with the cursor already resting on the first thing to click. MEASURED: the claim's
    # flight from the inventory (where the knife left the cursor) to the pile took 1.5s AFTER
    # the player had stopped. Hovering it during the settle spends that time on the walk.
    _prehover_first_action(ctx, current)
    controller.wait_until_player_idle(timeout_s=SITE_SETTLE_S)
    data["vt_fletch_due"] = False        # the leg is over; the next site decides afresh

    work_site(ctx, current)

    # Re-read AFTER the site. work_site just built, carved and decorated, so the top-of-tick
    # snapshot over-states what is outstanding, and BOTH decisions below are budgets computed
    # from it. A failed re-read falls back to the snapshot rather than skipping the checks.
    state = read_state() or state

    # THE BANK CHECK SITS HERE, below work_site, and only at a site the booth can be reached
    # from. Two separate bugs closed by that:
    #
    # 1. It used to run BEFORE work_site on `count_item(log) == 0`, so arriving at a built,
    #    carved totem holding 4 bows and no logs went banking instead of decorating it -- the
    #    decorations need BOWS, and we had them.
    # 2. It used to run at every site. From sites 2-5 the booth is 70-80 tiles off and out of
    #    the loaded scene, so the trip could only ever fail, and the failure was a safety stop.
    #    Running out of logs anywhere else is NOT a stop now: claim, carve and decorate all
    #    still work, so the routine carries on round the ring until the bank leg comes round.
    #    Only a bank trip actually attempted and failed is fatal.
    # THE BASKET COUNTS. MEASURED live 2026-09-09 (vt_live1): having just banked at site 8
    # (28 in the basket + 19 loose = 47 logs against a 30-log lap), the check at site 7 counted
    # only the loose 19 and sent the routine 37 tiles back to the booth -- and that hop is what
    # found the minimap-orb bug. The basket's contents are not readable off the bridge
    # (CLAUDE.md, /api/storage/log-basket), so they are ESTIMATED from what fill/empty moved.
    if (current in BANK_SITES
            and vt.logs_needed_for_lap(state, bow_count(ctx))
            > count_item(log_name) + basket_logs(data)):
        if not bank_trip(ctx):
            # FATAL ONLY WITH NOTHING IN HAND. A trip can fail with logs still held (a
            # withdraw that raced the bank closing, MEASURED on the host 2026-09-09); the
            # ring carries on and the next pass at site 8 tries again.
            if count_item(ctx.state.config["log"]) == 0 and not basket_logs(data):
                ctx.state.trigger_safety_stop(
                    "the bank trip from site %d failed with no %s in hand"
                    % (current, ctx.state.config["log"]))
                return
            debug_log.log(LOG, "bank trip failed -- carrying on with what is in hand")

    # FLETCH ONLY WHAT IS NOT SPOKEN FOR. The make menu has no quantity, so the game fletches
    # until the next click interrupts it; firing it while every log is owed to a build is how
    # the routine reached "plenty of bows, no logs" and deadlocked. vt.fletch_budget does the
    # arithmetic (pure, table-tested).
    # DUE, not done. The fletch itself is fired by walk_leg once the first hop is clicked --
    # USER 2026-09-09: "can only fletch after we give the move command". Starting it here, at
    # a standstill, means the next tick's walk click lands on an action that has just begun
    # and cancels it; the budget is re-derived at the moment it actually fires, so a stale
    # flag can never make a bow out of a log a build is owed.
    data["vt_fletch_due"] = (vt.fletch_budget(state, count_item(log_name), bow_count(ctx)) > 0
                             or (bow_count(ctx) >= STRING_ABOVE_BOWS
                                 and count_item(ctx.state.config["bow"]) > 0))

    data["vt_from"] = current
    data["vt_site"] = route.next_in_ring(current)

    # START THE LEG NOW, inside this tick. MEASURED 2026-09-09 (USER: "compared to the human
    # its very slow"): the site's last action and the leg's first click were ~3s apart, the
    # engine round trip plus the top-of-tick reads, at every one of eight sites. The human
    # is walking within a second of the final decoration.
    _walk_to_site(ctx, data["vt_site"])


def _walk_to_site(ctx, current: int) -> None:
    """Walk the ring leg into `current` with the fletch on the move; retried, then fatal.

    A LEG IS RETRIED, not fatal on the first miss. travel._hop_to returns False for a point it
    cannot project AT ALL, which covers transient states the next tick will not be in -- a
    loading screen, a panel drawn over the minimap, the camera mid-swing. Hard-stopping the
    run on one of those throws away a working routine; the ceiling is what separates that
    from a leg that genuinely cannot be walked."""
    data = ctx.state.data
    previous = data.get("vt_from", route.RING[route.RING.index(current) - 1])
    if walk_leg(ctx, previous, current, after_first_hop=lambda: _fletch_on_the_move(ctx)):
        data["vt_leg_tries"] = 0
        return
    tries = data.get("vt_leg_tries", 0) + 1
    data["vt_leg_tries"] = tries
    debug_log.log(LOG, "leg %s->%s failed (attempt %d of %d)"
                  % (previous, current, tries, LEG_MAX_ATTEMPTS))
    if tries >= LEG_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(
            "could not walk leg %s->%s in %d attempts"
            % (previous, current, LEG_MAX_ATTEMPTS))


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


def _prehover_first_action(ctx, site_number: int) -> None:
    """Rest the cursor on the pile if it has offerings, else on the totem base -- whatever
    work_site will click first -- so the click that follows the settle is a click in place.
    Best effort: an object not yet in view simply leaves the cursor where it is."""
    site = _site_now(site_number)
    if site is None:
        return
    which = "offerings" if site.unclaimed_offerings > 0 else "totem"
    entry = route.SITES[site_number]
    x, y = entry["totem"] if which == "totem" else entry["offerings"]
    obj_id = entry["base_id"] if which == "totem" else entry["offerings_id"]
    obj = site_object(site_number, which)
    if not obj:
        return
    controller.hover_entity(ctx.ser, obj,
                            refresh_target=controller.object_refresh_target(
                                x, y, route.PLANE, obj_id),
                            refresh_legs=SITE_REFRESH_LEGS)


def _click_site(ctx, site_number, which, budget_s, confirm, option=None):
    """Click a site object and confirm on the READER, never on the click landing.

    The aim is re-resolved through object_refresh_target for the whole flight: these clicks are
    issued while the player is still walking up, and a click that departs toward a tick-old
    projection is the "flick back" CLAUDE.md documents.

    `option` names the menu entry the click is meant to be ("Claim", "Build", "Decorate").
    When the confirm fails AND /last-action shows the click resolved to something else -- a
    miss beside a pile a few pixels wide, MEASURED 2026-09-09 as "Walk here" at sites 5-7 on
    roughly one claim in six -- the click is retried ONCE through the right-click menu by
    name, re-resolving the object first (CLAUDE.md's click_menu_action trap). That tier is
    immune to a pixel that slides off the model; it is not used first because a bare click
    is what the human does and what lands five times in six."""
    entry = route.SITES[site_number]
    x, y = entry["totem"] if which == "totem" else entry["offerings"]
    obj_id = entry["base_id"] if which == "totem" else entry["offerings_id"]
    obj = site_object(site_number, which)
    if not obj:
        debug_log.log(LOG, "site %d: %s object not in view" % (site_number, which))
        return False
    refresh = controller.object_refresh_target(x, y, route.PLANE, obj_id)
    if not controller.click_entity(ctx.ser, obj, refresh_target=refresh,
                                   refresh_legs=SITE_REFRESH_LEGS):
        return False
    outcome = outcome_waits.await_pre(ctx, ["VARBIT_CHANGED", "CHAT_MESSAGE"], budget_s,
                                      confirm, now_fn=_now, sleep_fn=_sleep)
    if outcome or not option:
        return bool(outcome)
    last = controller.get_last_action() or {}
    if (last.get("option") or "").lower() == option.lower():
        return False            # the right action registered and still did not confirm
    debug_log.log(LOG, "site %d: %s click resolved to %r -- retrying through the menu"
                  % (site_number, option, last.get("option")))
    obj = site_object(site_number, which)
    if not obj:
        return False
    if not controller.click_menu_action(ctx.ser, obj, option, offset=(0, 0),
                                        refresh_target=refresh):
        return False
    return bool(outcome_waits.await_pre(ctx, ["VARBIT_CHANGED", "CHAT_MESSAGE"], budget_s,
                                        confirm, now_fn=_now, sleep_fn=_sleep))


def _site_now(site_number):
    state = read_state()
    return None if state is None else state.sites.get(site_number)


def claim(ctx, site) -> bool:
    """Take the offerings pile. Confirmed on the pile reading empty."""
    n = site.number

    def done():
        fresh = _site_now(n)
        return fresh is not None and fresh.unclaimed_offerings == 0

    return _click_site(ctx, n, "offerings", CLAIM_BUDGET_S, done, option="Claim")


def build(ctx, site) -> bool:
    """Erect a totem. Confirmed on baseValue leaving 0 -- one log, and 15% run energy back."""
    n = site.number

    def done():
        fresh = _site_now(n)
        return fresh is not None and fresh.base_value != 0

    return _click_site(ctx, n, "totem", BUILD_BUDGET_S, done, option="Build")


CARVE_MENU_BUDGET_S = 4.0
CARVE_BUDGET_S = 6.0


def _close_prompt(ctx, force: bool = False) -> None:
    """ESC whatever group-270 prompt is still on screen.

    A STRANDED INTERFACE EATS THE NEXT WORLD CLICK. motherlode_mine measured this live
    (mlm_live8): a deposit box left open turned eight consecutive ladder clicks into eight
    "close the interface" clicks with the player never moving an inch, ended by a ceiling
    rather than by anything noticing. Here the hazard is worse than usual, because the carve
    prompt and the fletch prompt are the SAME widget group -- so a carve menu the title gate
    withheld on, and a make menu opened by a Use that then found no option, are the same open
    interface, and it survives straight into the leg walk.

    "ESC", never "escape": Firmware.ino's keyFromToken only knows the token ESC and returns 0
    for anything else, which sends NOTHING and looks exactly like a keypress that landed.
    Every other caller in the repo (banker.py, cargo_hold.py, giants_foundry.py) uses "ESC".

    `force` skips the is_open() read for a caller that already knows an interface is up (the
    bank), where a failed widget dump must not mean the close is skipped."""
    if not force and not skillmulti.is_open():
        return
    controller.key_tap(ctx.ser, "ESC")


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
        # The title being wrong does not mean nothing is up -- a MAKE prompt reads this way,
        # and it is the same group 270 that would then eat the next world click.
        _close_prompt(ctx)
        return False
    widget = skillmulti.item_widget(spirit.capitalize())
    if not widget:
        debug_log.log(LOG, "carve menu has no %s option" % spirit)
        _close_prompt(ctx)
        return False
    option = skillmulti.option_number(widget)
    if option is not None:
        controller.key_tap(ctx.ser, str(option))
        return True
    return bool(controller.click_entity(ctx.ser, widget, offset=(0, 0),
                                        check_ui_blockers=False, simulate_mistakes=False))


# 7 tiles, and the number is travel.HOP_NEXT_CLICK_LEAD_TILES (6) PLUS ONE -- it has to sit
# ABOVE the lead or a completed walk can never be seen to have arrived.
#
# travel._hop_to deliberately RELEASES a hop longer than that lead with ~6 tiles still to run,
# so the next hop's aim and mouse flight overlap the remaining tiles instead of starting after
# the player has stopped (the measured 0.6-1.8s dead stop at every hop boundary; see that
# constant's own comment). Every leg here ends with an 8-12 tile waypoint, so the LAST hop
# releases early too and walk_leg returns with the player about 6 tiles short of the stand
# tile. Against the 4 this shipped with, at_site() then read False, tick() re-entered walk_leg
# and clicked the PREVIOUS site's first waypoint -- a minimap click back where we came from.
# That is the "clicking back and forth" signature CLAUDE.md documents, and it makes no progress
# while looking busy.
ARRIVAL_TOLERANCE = 7

# Re-aim checkpoints for a click at a site. MEASURED live 2026-09-09: the first click after a
# leg is issued while the player is STILL RUNNING (walk_leg releases early by design, see
# ARRIVAL_TOLERANCE), and a single mid-flight refresh lands behind the target -- site 7's claim
# aimed at (1019,428) and landed at (996,429), 23px back, which at the guest's zoomed-out
# camera is off the model entirely and resolves to "Walk here". More checkpoints keep the aim
# on a target that is sliding across the screen; see CLAUDE.md's refresh_legs note.
SITE_REFRESH_LEGS = 3

# ...and the first action at a site waits for the player to actually STOP, bounded. The same
# claim clicked from a standstill lands every time (MEASURED with a one-click probe: offerings
# 250 -> 315 on a pile the running routine had failed at six times). This costs the ~4s of
# running that walk_leg deliberately overlapped, once per site, and buys every action at that
# site a stationary target -- the camera is still catching up for part of it, which is the
# other half of the drift.
SITE_SETTLE_S = 5.0

# ...and the last few tiles are waited out rather than assumed: 6 tiles of running is ~3.6s at
# one tile per 0.6s tick, so 4.0s is that plus a poll's slack.
LEG_ARRIVAL_POLL_S = 4.0
LEG_ARRIVAL_POLL_INTERVAL_S = 0.6      # one game tick -- the soonest the player's tile changes

# How far from a stand tile a restart may be and still be placed on the ring. The widest gap
# between adjacent stand tiles is 57 tiles (2 -> 1), so 30 covers every point of every leg
# while still refusing a player who is somewhere else in Varlamore entirely.
SITE_SEARCH_RADIUS = 30

# Loose logs to keep in hand between sites: one totem (LOGS_PER_TOTEM) plus a few for the
# next leg's bows. Below this the basket is emptied at the site.
LOOSE_LOG_TARGET = vt.LOGS_PER_TOTEM + 4


def _player_xy():
    player = controller.get_player() or {}
    return player.get("x"), player.get("y")


def _tiles_between(x1, y1, x2, y2) -> int:
    """Chebyshev, because that is what a tile of RUNNING costs -- a diagonal step is one tick."""
    return max(abs(x1 - x2), abs(y1 - y2))


def at_site(site_number: int) -> bool:
    x, y = _player_xy()
    if x is None or y is None:
        return False
    sx, sy = route.SITES[site_number]["stand"]
    return _tiles_between(x, y, sx, sy) <= ARRIVAL_TOLERANCE


def nearest_site():
    """The ring site whose stand tile is closest, or None past SITE_SEARCH_RADIUS.

    THE RESTART CASE. ctx.state.data is wiped on every restart and routine reload, so tick()
    has to re-derive which site it is at. The first draft did that with
    min(route.RING, key=lambda n: 0 if at_site(n) else 1), which returns RING[0] = 6 whenever
    the player is on no stand tile at all -- min() keeps the FIRST minimum -- so a routine
    restarted mid-leg walked 7 -> 6 from wherever it happened to be, across terrain no
    waypoint covers and no router can path. Picking the genuinely nearest site instead means
    the resumed leg starts beside the player (see walk_leg's nearest-waypoint start), and
    "nowhere near the ring" is answered honestly rather than by guessing.
    """
    x, y = _player_xy()
    if x is None or y is None:
        return None
    best, best_distance = None, None
    for number in route.RING:
        sx, sy = route.SITES[number]["stand"]
        distance = _tiles_between(x, y, sx, sy)
        if best_distance is None or distance < best_distance:
            best, best_distance = number, distance
    if best_distance is None or best_distance > SITE_SEARCH_RADIUS:
        return None
    return best


# The direct leg: how long a whole leg may take (the longest, 2->1, is 57 tiles, ~35s running
# with a detour) and how long the player may stand still before the leg is declared stalled
# and the waypoint hops take over. 3s is five ticks -- a real path never pauses that long.
DIRECT_LEG_MAX_S = 50.0
DIRECT_LEG_STALL_S = 2.0      # ~3 ticks; a real path never pauses that long
DIRECT_LEG_POLL_S = 0.6
NO_DIRECT_KEY = "vt_no_direct"   # legs whose far click the game will not path at all


ARRIVED, STRETCH, NO_CLICK, STALLED = "arrived", "stretch", "no-click", "stalled"


def _far_click_target(b: int, points):
    """The farthest point of the leg that ONE click can reach right now: site `b`'s stand
    tile first, then the recorded waypoints from the far end. A point is reachable if it is
    rendered in the viewport (and not under a panel) OR on the minimap (and not on an orb).

    The minimap half is what makes the stretches long enough. MEASURED 2026-09-09 with the
    viewport alone: the picked point was 11-18 tiles out on most legs -- 7-11s, 3-5 bows,
    then the next click ended the fletch. The minimap reaches 28 tiles whatever the camera
    renders (CLAUDE.md's measurement), and the game paths there on its own."""
    # MINIMAP FIRST for each candidate (USER 2026-09-09: "click minimap as far away as
    # possible -> click knife -> click log -> confirm and voila"): nothing is drawn on it to
    # hijack the click and its reach does not depend on the camera; the viewport is the
    # fallback for a point the minimap cannot show.
    sx, sy = route.SITES[b]["stand"]
    for cx, cy in [(sx, sy)] + [tuple(pt) for pt in reversed(points)]:
        point = controller.get_minimap_walk_point(cx, cy, route.PLANE)
        if point.get("onMinimap") and not controller.is_point_on_minimap_orb(
                point.get("screenX", -1), point.get("screenY", -1)):
            return cx, cy, point, False
        point = controller.get_ground_walk_point(cx, cy, route.PLANE)
        if point.get("onScreen") and not controller.is_point_blocked_by_ui(
                point.get("screenX", -1), point.get("screenY", -1)):
            return cx, cy, point, True
    return None


def _direct_leg(ctx, b: int, points, fire_hook) -> str:
    """ONE click at the farthest reachable point of the leg, fire the hook, then only poll.

    ARRIVED: at the site. STRETCH: the intermediate point was reached, the hook has fired --
    the caller clicks again from here. NO_CLICK: nothing reachable, nothing clicked.
    STALLED: the hook has fired but the player stopped short (a log balance the game's own
    pathfinder will not cross), so the recorded hops take over. Every click before the
    stretch is walked would end the fletch, which is why there is exactly one per stretch."""
    target = _far_click_target(b, points)
    if target is None:
        return NO_CLICK
    cx, cy, point, viewport = target
    sx, sy = route.SITES[b]["stand"]
    travel._click_walk_point(ctx, point, shift=viewport)
    started = fire_hook()
    fletching = started == "fletch"
    debug_log.log(LOG, "direct leg toward site %d: one %s click at (%d,%d)%s"
                  % (b, "viewport" if viewport else "minimap", cx, cy,
                     {"fletch": ", fletch on the move", "string": ", stringing on the move"}
                     .get(started, "")))

    # THE FLETCH HAS NO QUANTITY: it turns every loose log into a bow until something ends
    # it, and a stretch long enough to make bows is long enough to eat the build logs too
    # (USER 2026-09-09: "gotta leave a few logs in the inventory to actually craft the
    # totems too"). So the loose count is watched, and once only the reserve the hook set
    # aside is left, the SAME walk target is clicked again -- the game keeps the walk and
    # ends the fletch, exactly what a human does with a stray click.
    log_name = ctx.state.config["log"]
    reserve = int(ctx.state.data.get(LOG_RESERVE_KEY) or 0)

    deadline = _now() + DIRECT_LEG_MAX_S
    last_xy, still_since = _player_xy(), _now()
    while _now() < deadline:
        _sleep(DIRECT_LEG_POLL_S)
        if fletching and count_item(log_name) <= reserve:
            travel._click_walk_point(ctx, point, shift=viewport)
            fletching = False
            debug_log.log(LOG, "fletch stopped at the build reserve (%d %s left)"
                          % (reserve, log_name))
        if at_site(b):
            return ARRIVED
        xy = _player_xy()
        if (cx, cy) != (sx, sy) and xy[0] is not None \
                and _tiles_between(xy[0], xy[1], cx, cy) <= ARRIVAL_TOLERANCE:
            return STRETCH
        if xy != last_xy:
            last_xy, still_since = xy, _now()
        elif _now() - still_since >= DIRECT_LEG_STALL_S:
            debug_log.log(LOG, "direct leg to site %d stalled at %s -- falling back to the "
                               "recorded hops" % (b, xy))
            return STALLED
    debug_log.log(LOG, "direct leg to site %d: not there after %.0fs -- falling back to the "
                       "recorded hops" % (b, DIRECT_LEG_MAX_S))
    return STALLED


# A leg is at most this many far clicks before the hops take over -- the longest leg (57
# tiles) is two or three 24-28 tile stretches; more than five means something is circling.
DIRECT_LEG_MAX_STRETCHES = 5


def walk_leg(ctx, a: int, b: int, after_first_hop=None) -> bool:
    """Walk site `a` -> site `b` by hopping the recorded waypoints.

    NO ROUTER: worldmap has no collision data for Varlamore, so travel.walk_to cannot be used
    here at all. These are the human's own tick-by-tick positions, so the path is known-good;
    the risk is only that a discrete hop lands differently than a continuous run, which is what
    the live checklist watches at the two log balances.

    `after_first_hop` runs ONCE, immediately after the leg's first hop click is out and before
    the remaining waypoints are clicked. USER 2026-09-09: "can only fletch after we give the
    move command" -- a knife-on-log started while standing still is cancelled by the very next
    walk click, so the fletch has to be issued with the player already on its way. That is the
    hook; see tick() for the caller.
    """
    points = route.leg_waypoints(a, b)
    if not points:
        debug_log.log(LOG, "no recorded path for leg %d->%d" % (a, b))
        return False

    # ONE FAR CLICK, THEN HANDS OFF -- the human's shape, and the only one a fletch survives.
    # MEASURED 2026-09-09 from the recording: every fletch run (5-9 bows) has ZERO clicks
    # between the knife and the next site -- one "Walk here" on a tile up to 31 tiles away,
    # then bows every 1.8s untouched. The bot's waypoint hops every ~2s each ended the fletch
    # (anim 1248 started, no product, every leg), so decorations never got made. USER:
    # "between each totem we should be able to craft enough bows for the next one".
    # ONCE PER LEG. USER 2026-09-09: "just saw a fletch bow before clicking move basically
    # just cancel the action" -- the second far click of a long leg ended the fletch the first
    # had started, and the hook then re-issued knife/log/make for the tail. A walk click ALWAYS
    # ends a make action (the recording's uninterrupted runs are the proof), so the fletch is
    # started on the first move only; the first stretch is 24-28 tiles, 6+ bows, more than a
    # totem needs. On a hop-only leg it fires after the LAST hop instead, whose final run to
    # the site is the one stretch nothing clicks through.
    fired = {"done": False}

    def fire_hook():
        # Returns the hook's own verdict ("fletch" / "string" / False) because _direct_leg
        # only watches the log reserve for a fletch it knows is running.
        if after_first_hop is None or fired["done"]:
            return False
        fired["done"] = True
        return after_first_hop()

    # A leg whose far click the game refused to path at all (MEASURED: 6->5, 4->3 and the
    # 8->7 leg from the booth all stall at once, a cliff or a log balance between) is
    # remembered, and the recorded hops take it from the start -- a 2s stall and a wasted
    # fletch start per lap otherwise.
    no_direct = ctx.state.data.setdefault(NO_DIRECT_KEY, {})
    stretches = 0
    if not no_direct.get(str((a, b))):
        for _ in range(DIRECT_LEG_MAX_STRETCHES):
            verdict = _direct_leg(ctx, b, points, fire_hook)
            if verdict == ARRIVED:
                return True
            if verdict == STRETCH:
                stretches += 1
                continue
            if verdict == STALLED and stretches == 0 and _tiles_between(
                    *(_player_xy() + route.SITES[a]["stand"])) <= ARRIVAL_TOLERANCE:
                no_direct[str((a, b))] = True
                debug_log.log(LOG, "leg %d->%d: the far click does not path from site %d -- "
                                   "recorded hops from now on" % (a, b, a))
            break               # nothing clickable, or stalled: the recorded hops from here

    # START FROM THE WAYPOINT NEAREST THE PLAYER, not always index 0. A leg is resumed mid-way
    # far more often than it is started clean: after a bank trip (which leaves the player ON
    # leg 8->7, beside the booth), after a restart, and after any hop this function had to
    # retry. Replaying the leg from its first waypoint in those cases is a minimap click BACK
    # toward the site we just left. The order stays forward from that index -- the waypoints
    # are the human's own recorded path and only make sense walked in sequence.
    x, y = _player_xy()
    start = 0
    if x is not None and y is not None:
        start = min(range(len(points)),
                    key=lambda i: _tiles_between(x, y, points[i][0], points[i][1]))

    remaining = points[start:]
    for index, (wx, wy) in enumerate(remaining):
        if not travel._hop_to(ctx, wx, wy, route.PLANE):
            debug_log.log(LOG, "leg %d->%d: hop to (%d,%d) failed" % (a, b, wx, wy))
            return False
        if index == len(remaining) - 1:
            # The LAST move is out; the run from here to the site is the one stretch no
            # further click interrupts, so this is where a fletch on a hop leg belongs.
            fire_hook()

    # POLL FOR ARRIVAL rather than returning on the last CLICK. That click released with
    # HOP_NEXT_CLICK_LEAD_TILES still to run (see ARRIVAL_TOLERANCE), so the player is still
    # moving when the loop above ends. Returning here without waiting hands tick() a position
    # that at_site() may still reject, and the caller's only recovery is to walk the leg again.
    deadline = _now() + LEG_ARRIVAL_POLL_S
    while True:
        if at_site(b):
            return True
        if _now() >= deadline:
            break
        _sleep(LEG_ARRIVAL_POLL_INTERVAL_S)
    debug_log.log(LOG, "leg %d->%d: still short of site %d after %.1fs"
                  % (a, b, b, LEG_ARRIVAL_POLL_S))
    return False


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
    if not controller.click_entity(ctx.ser, obj, refresh_target=refresh,
                                   refresh_legs=SITE_REFRESH_LEGS):
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
DECORATE_ACTION_S = 1.5     # recording: one decoration animation every 1.2s after ONE click
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
    """ONE Decorate click, confirmed on every bow in hand going on.

    USER 2026-09-09: "only need to click once to decorate i dont think it speeds it up", and
    the recording agrees -- one Decorate click is followed by four decoration animations
    1.2s apart with no further click. Decorating is a continuous action like fletching: it
    runs until the totem is full or the bows run out. The first draft clicked once per
    decoration (confirming on the count rising by one), so every extra click landed on a
    loop that was already running. Now the confirm is the count reaching what the bows in
    hand allow, with a budget of one action per decoration plus the walk."""
    n = site.number
    bows = bow_count(ctx, site.wood)
    target = min(vt.MAX_DECORATIONS, site.decorations + bows)
    if target <= site.decorations:
        return False

    def landed():
        fresh = _site_now(n)
        return fresh is not None and fresh.decorations >= target

    budget = DECORATE_BUDGET_S + DECORATE_ACTION_S * (target - site.decorations)
    return _click_site(ctx, n, "totem", budget, landed, option="Decorate")


LOG_RESERVE_KEY = "vt_log_reserve"
STRING_ABOVE_BOWS = 8            # two totems' worth in hand: string instead of fletching
SPOOL_NAMES = ("Bow string spool",)
SPOOL_DEAD_KEY = "vt_spool_dead"  # a spool that strung nothing (empty?) is not tried again
STRING_START_BUDGET_S = 4.0


def bow_count(ctx, wood=None) -> int:
    """Bows in hand for `wood` (default the wood being worked), strung or not -- both
    decorate. A totem takes only bows of its OWN wood, so decorating asks for the site's."""
    unstrung, strung = _bows_for(ctx, wood)
    return count_item(unstrung) + count_item(strung)


def find_spool():
    for name in SPOOL_NAMES:
        item = find_item(name)
        if item:
            return item
    return None


def start_stringing(ctx) -> bool:
    """Spool on an unstrung bow, then RETURN -- like the fletch, the walk overlaps it.

    NOT RECORDED: the human has never used the spool in a recording, so the interaction is
    assumed to be the knife's shape (Use spool -> bow, a make prompt if one opens) and the
    outcome is judged on the unstrung count FALLING within STRING_START_BUDGET_S. A spool
    that strings nothing -- empty of bow strings, or a different interaction -- is marked
    dead for the session so the leg goes back to fletching rather than trying every lap."""
    unstrung = ctx.state.config["bow"]
    spool = find_spool()
    bow = find_item(unstrung)
    if not spool or not bow:
        return False
    before = count_item(unstrung)
    controller.open_inventory_tab(ctx.ser)
    if not controller.click_entity(ctx.ser, spool, offset=(0, 0), check_ui_blockers=False,
                                   simulate_mistakes=False, expected_action="Use"):
        return False
    if not controller.click_entity(ctx.ser, bow, offset=(0, 0), check_ui_blockers=False,
                                   simulate_mistakes=False):
        return False
    # A make prompt, if the game asks: pick the strung bow by name.
    if outcome_waits.await_pre(ctx, ["WIDGET_LOADED"], 1.5,
                               lambda: skillmulti.showing(skillmulti.TITLE_MAKE),
                               now_fn=_now, sleep_fn=_sleep):
        widget = skillmulti.item_widget(ctx.state.config["strung"])
        if widget:
            option = skillmulti.option_number(widget)
            if option is not None:
                controller.key_tap(ctx.ser, str(option))
            else:
                controller.click_entity(ctx.ser, widget, offset=(0, 0),
                                       check_ui_blockers=False, simulate_mistakes=False)
        else:
            _close_prompt(ctx)
            return False
    ok = bool(outcome_waits.await_pre(ctx, ["INVENTORY_CHANGED"], STRING_START_BUDGET_S,
                                      lambda: count_item(unstrung) < before,
                                      now_fn=_now, sleep_fn=_sleep))
    if not ok:
        ctx.state.data[SPOOL_DEAD_KEY] = True
        debug_log.log(LOG, "the spool strung nothing -- is it loaded? not trying it again "
                           "this session")
    return ok


def _fletch_on_the_move(ctx) -> bool:
    """Start a fletch while the player is already walking, if one is still worth starting.

    Called from walk_leg's after_first_hop hook. Re-derives the budget from LIVE state rather
    than trusting the flag tick() set at the previous site: the walk may have been re-entered
    after a failed leg, and a log owed to a build must never become a bow (vt.fletch_budget's
    own deadlock note)."""
    data = ctx.state.data
    # Not popped: the flag stays up for the whole leg so every far click can restart the
    # fletch the click just ended; tick() clears it on arrival at the site.
    if not data.get("vt_fletch_due", False):
        return False
    state = read_state()
    if state is None:
        return False
    # STRING ABOVE TWO TOTEMS' WORTH. USER 2026-09-09: "if we have above say 8 bows (two
    # totems) instead of fletching more during a long run use the spool to string the bows
    # that way we get some free exp along the way". The wiki (Vale Totems/Strategies, the
    # Demonic Pacts fletching guide) confirms strung bows decorate just as well, so nothing is
    # lost: bows are counted strung or not everywhere below.
    if (bow_count(ctx) >= STRING_ABOVE_BOWS and count_item(ctx.state.config["bow"]) > 0
            and find_spool() and not data.get(SPOOL_DEAD_KEY)):
        return "string" if start_stringing(ctx) else False
    if vt.fletch_budget(state, count_item(ctx.state.config["log"]), bow_count(ctx)) <= 0:
        return False
    # What the fletch must NOT touch: one log per build still owed, the same reserve
    # fletch_budget spares. _direct_leg stops the fletch when the loose count reaches it.
    data[LOG_RESERVE_KEY] = vt.builds_needed(state)
    return "fletch" if start_fletching(ctx) else False


# The Vale Research Exchange's Fletching knife (USER 2026-09-09: "new knife name fletching
# knife") or an ordinary Knife -- the reward one first, since it is the one the user bought
# to use here.
KNIFE_NAMES = ("Fletching knife", "Knife")


def find_knife():
    for name in KNIFE_NAMES:
        item = find_item(name)
        if item:
            return item
    return None


def start_fletching(ctx) -> bool:
    """Knife a log and pick the bow, then RETURN -- the walk overlaps the fletching.

    MEASURED 2026-09-08: fletching continues while running (five bows delivered across a
    30-tile sprint), one product every 1.8s. So this is fired as the last act at a site and the
    leg walk runs underneath it; nothing waits here.

    NEVER `Space`: it repeats the last crafted item, which is session state we do not own.
    The option is matched by NAME and its digit derived from the matched widget, so a wood
    whose menu differs cannot silently make the wrong thing.
    """
    knife = find_knife()
    log = find_item(ctx.state.config["log"])
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
        # "Never opened" here means "the MAKE title never showed" -- a stray CARVE prompt
        # satisfies neither and is exactly the interface that must not survive into the walk.
        _close_prompt(ctx)
        return False
    widget = skillmulti.item_widget(ctx.state.config["bow"])
    if not widget:
        debug_log.log(LOG, "make menu has no %s option" % ctx.state.config["bow"])
        _close_prompt(ctx)
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
    """Case-insensitively, the way count_item/find_item already match -- /inventory's casing is
    the game's and nothing here should depend on it."""
    wanted = {n.lower() for n in BASKET_NAMES}
    for item in controller.get_inventory() or []:
        if (item.get("name") or "").lower() in wanted:
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


BASKET_CAPACITY = 28
BASKET_LOGS_KEY = "vt_basket_logs"


def basket_logs(data) -> int:
    """Our ESTIMATE of the logs in the basket -- 0 until a fill or empty has taught us
    otherwise. Conservative on purpose: an unknown basket makes the lap check bank at site 8,
    which is on the way and costs ~15s, whereas over-counting it would skip a needed trip."""
    return int(data.get(BASKET_LOGS_KEY) or 0)


BASKET_CLOSED_NAME = "log basket"        # 28140, the shut one
BASKET_OPEN_NAME = "open log basket"     # 28142


def _basket_is_open() -> bool:
    basket = find_basket()
    return bool(basket) and (basket.get("name") or "").lower() == BASKET_OPEN_NAME


def open_basket(ctx) -> bool:
    """Shut basket -> open basket, so that it can be emptied at all.

    MEASURED live 2026-09-09 (vale_totems in the VM): a right-click on the SHUT basket offers
    ['Cancel','Examine','Destroy','Open','Use','Check','Wear','Fill'] -- there is NO Empty on
    it, so every field top-up failed with "No 'empty' entry in menu". The cache agrees: 28140
    log_basket_closed has iops Fill/Wear/Check/Open/Destroy, and only 28142 log_basket_open
    carries Empty. CLAUDE.md had this listed as the basket's one unverified question.

    Cheap and idempotent -- an already-open basket returns True without clicking."""
    if _basket_is_open():
        return True
    return _basket_option(ctx, ("open",), _basket_is_open)


def empty_basket(ctx) -> bool:
    """Basket -> inventory, in the field ("You empty as many logs as you can carry.").

    The logs that ARRIVE are subtracted from the basket estimate; a successful Empty that
    moved nothing means the basket is empty, whatever the estimate said."""
    log = ctx.state.config["log"]
    before = count_item(log)
    free_before = 28 - len(controller.get_inventory() or [])
    if not open_basket(ctx):
        debug_log.log(LOG, "could not open the log basket -- cannot empty it this visit")
        return False
    # A SHIFT + LEFT CLICK -- not a menu selection, and not a plain click either. MEASURED
    # 2026-09-09: even the OPEN basket's right-click menu in the field is ['Cancel','Examine',
    # 'Destroy','Close','Use','Check','Wear','Fill'] -- Empty is a SUBMENU option (cache:
    # isubop under Check) that /menu cannot list, and a plain left click hovers Fill, not
    # Empty (the first cut was withheld on exactly that). The recording settles how the human
    # does it, three times out of three: SHIFT down ~0.25s, then a left click on "Open log
    # basket", which the game logs as CC_OP Empty with the sub-option identifier 65540. So
    # SHIFT is held around the click. The hover gate is tried first (with SHIFT down the
    # client's default entry should read Empty); if it withholds, one ungated shift-click
    # goes out and the inventory delta below is the only verdict -- a wrong action here
    # moves no logs, which is a False, never a wrong click.
    basket = find_basket()
    if not basket:
        return False
    controller.open_inventory_tab(ctx.ser)
    controller.key_down(ctx.ser, "SHIFT")
    try:
        controller.human_delay(0.2, 0.35)
        clicked = controller.click_entity(ctx.ser, basket, offset=(0, 0),
                                          check_ui_blockers=False, simulate_mistakes=False,
                                          expected_action="Empty")
        if not clicked:
            debug_log.log(LOG, "shift-click on the log basket did not hover as Empty -- "
                               "sending it ungated and trusting the inventory delta")
            clicked = controller.click_entity(ctx.ser, basket, offset=(0, 0),
                                              check_ui_blockers=False, simulate_mistakes=False)
    finally:
        controller.key_up(ctx.ser, "SHIFT")
    if not clicked:
        return False
    ok = bool(outcome_waits.await_pre(ctx, ["INVENTORY_CHANGED"], BASKET_BUDGET_S,
                                      lambda: count_item(log) > before,
                                      now_fn=_now, sleep_fn=_sleep))
    moved = max(0, count_item(log) - before)
    data = ctx.state.data
    # An Empty hands over at most the FREE SLOTS' worth, so "it moved as many as could fit"
    # says nothing about what is left. USER 2026-09-09: "just ignored a totem for some reason,
    # had 17 logs in logbasket none in inventory" -- after a restart the estimate was unknown,
    # the first empty moved a slots-worth, and the arithmetic then recorded the basket as
    # KNOWN EMPTY, after which it was never emptied again. Now: fewer than the free slots
    # moved -> the basket is empty (0); a full slots-worth moved -> known estimate minus
    # moved, floored at "unknown, not empty" (None), never a false zero.
    if moved < free_before:
        data[BASKET_LOGS_KEY] = 0
    elif data.get(BASKET_LOGS_KEY) is None or basket_logs(data) - moved <= 0:
        data[BASKET_LOGS_KEY] = None
    else:
        data[BASKET_LOGS_KEY] = basket_logs(data) - moved
    return ok


def fill_basket(ctx) -> bool:
    """Inventory -> basket, at the bank.

    The logs that LEAVE are added to the estimate. A Fill that moved nothing is the game's
    "The basket is full." (MEASURED 2026-09-09 -- the recorder showed the menu entry
    resolving correctly and that chat line following), so the estimate is pinned to the
    capacity rather than left where it was; bank_trip stops its rounds on that."""
    log = ctx.state.config["log"]
    before = count_item(log)
    ok = _basket_option(ctx, ("fill",), lambda: count_item(log) < before)
    moved = max(0, before - count_item(log))
    data = ctx.state.data
    if moved:
        data[BASKET_LOGS_KEY] = min(BASKET_CAPACITY, basket_logs(data) + moved)
    else:
        data[BASKET_LOGS_KEY] = BASKET_CAPACITY
    return ok


# The only ring sites the bank is reachable from. Leg 8->7's recorded waypoints run through
# (1412,3347) and (1422,3348), either side of the booth's stand tile (1416,3350) -- 4 and 6
# tiles off it -- so the bank is ON that leg and nowhere else. From sites 2-5 the booth is
# 70-80 tiles away and outside the loaded scene, which is why the first draft's
# get_object_at() there returned nothing and safety-stopped the run with the bank simply out
# of view. LEGS has no bank entry and there is no router, so this cannot be walked to from
# anywhere else.
#
# ONLY site 8, since 2026-09-09. The booth is ~20 tiles from site 8's stand tile (one hop)
# and ~37 from site 7's -- off the minimap, back along the leg just walked, and then 37
# tiles back again. MEASURED live (vt_live1): the site-7 trip fired one site after a full
# bank at 8, walked nowhere (the minimap-orb bug) and safety-stopped the run. From 7 the
# next move is the log balance to site 6; the bank comes round again on the next 8->7 leg.
BANK_SITES = (8,)

BANK_HOP_ATTEMPTS = 4


def _walk_to_bank(ctx) -> bool:
    """Hop to the booth's stand tile, repeatedly.

    ONE _hop_to CALL IS NOT AN ARRIVAL: it PULLS an aim point beyond the minimap's reach in
    toward the player and still returns True, and it releases a long hop early by design. The
    booth is ~20 tiles off site 8's stand tile and ~37 off site 7's, so neither is guaranteed
    in a single hop. False only when a hop is genuinely unprojectable or we never get there.
    """
    bx, by = route.BANK["stand"]
    for _ in range(BANK_HOP_ATTEMPTS):
        x, y = _player_xy()
        if x is not None and _tiles_between(x, y, bx, by) <= ARRIVAL_TOLERANCE:
            return True
        if not travel._hop_to(ctx, bx, by, route.PLANE):
            debug_log.log(LOG, "bank: hop to (%d,%d) failed" % (bx, by))
            return False
    x, y = _player_xy()
    if x is not None and _tiles_between(x, y, bx, by) <= ARRIVAL_TOLERANCE:
        return True
    debug_log.log(LOG, "bank: never reached the stand tile in %d hops" % BANK_HOP_ATTEMPTS)
    return False


def bank_trip(ctx) -> bool:
    """Walk to the booth, then Withdraw-All logs / close / Fill the basket for three rounds --
    the recording's own sequence.

    "You don't have enough inventory space to withdraw that many." is NORMAL here and must not
    be treated as a failure; fletching.py records the same thing for the same click.

    The walk is part of the trip, not the caller's job: the booth only resolves once we are
    standing beside it, and the caller (tick) has already established we are on a BANK_SITES
    site, which is the only place this walk is short and recorded.
    """
    if not _walk_to_bank(ctx):
        return False
    # OUT OF THIS WOOD? The bank memory (every tab, search or not) answers before the first
    # withdraw; 0 -- known empty, never "never seen" -- steps the wood down one.
    memory = bank_memory.load(names=[ctx.state.config["log"]])
    if memory is not None and memory.quantity(ctx.state.config["log"]) == 0:
        fallback = WOOD_FALLBACK.get(ctx.state.config["wood"])
        if not fallback:
            debug_log.log(LOG, "the bank is out of %s and there is no wood below %s"
                          % (ctx.state.config["log"], ctx.state.config["wood"]))
            return False
        debug_log.log(LOG, "the bank is out of %s -- switching to %s"
                      % (ctx.state.config["log"], fallback))
        _apply_wood(ctx, fallback)
    entry = route.BANK
    booth = controller.get_object_at(entry["tile"][0], entry["tile"][1], route.PLANE,
                                     entry["object_id"])
    if not booth:
        debug_log.log(LOG, "bank booth not in view")
        return False
    for round_index in range(BANK_ROUNDS):
        if not banker.open_bank(ctx, booth):
            return False
        if not banker.withdraw_all_shift(ctx, ctx.state.config["log"], fresh=True):
            return False
        _close_prompt(ctx, force=True)
        if round_index < BANK_ROUNDS - 1:
            # A FULL basket ends the trip with this round's withdraw already in hand: another
            # Fill would only draw "The basket is full." and the withdraw after it "You don't
            # have enough inventory space." -- MEASURED 2026-09-09, two wasted rounds (~15s)
            # on a basket the user had filled by hand before the run.
            if basket_logs(ctx.state.data) >= BASKET_CAPACITY:
                debug_log.log(LOG, "bank: basket already full -- done after round %d"
                              % (round_index + 1))
                break
            if not fill_basket(ctx):
                # A failed fill is LOGGED rather than swallowed: a fill that fails for any
                # reason other than a full basket (a menu miss) leaves this round's logs
                # loose, and the next Withdraw-All then has fewer free slots than it expects.
                debug_log.log(LOG, "bank: round %d did not fill the basket -- the next "
                                   "withdraw has fewer free slots than it expects"
                              % (round_index + 1))
            if basket_logs(ctx.state.data) >= BASKET_CAPACITY:
                # fill_basket pinned the estimate to the capacity (nothing moved, or the
                # count reached it): this round's withdraw is already in hand, so stop here.
                debug_log.log(LOG, "bank: basket full -- done after round %d"
                              % (round_index + 1))
                break
    return True


SITE_MAX_ACTIONS = 12     # claim + build + 3 carves + 4 decorates is 9; 12 is a ceiling
STALL_STOP_TICKS = 40
LEG_MAX_ATTEMPTS = 3      # a transient un-projectable hop point is not a broken route

# Action name -> the module-level function that performs it. Resolved through globals() at
# CALL time rather than bound into a dict at import time, so that monkeypatching
# routines.vale_totems.build (which every test of this loop does) actually takes effect. A
# dict built at import captures the original function objects and silently ignores the patch.
_ACTIONS = ("claim", "build", "carve", "decorate")


def work_site(ctx, site_number: int) -> bool:
    """Run the ladder at one site until it reports done, an action fails, or the ceiling.

    The ceiling is load-bearing: every action here confirms against the reader, so an action
    that reports success without advancing the totem would otherwise loop forever. A failed
    action ends the visit rather than retrying -- the caller walks on and the next lap sees the
    site again, which is cheaper than spinning in front of it.
    """
    for _ in range(SITE_MAX_ACTIONS):
        site = _site_now(site_number)
        if site is None:
            return False
        action, argument = vt.next_action(
            site, logs=count_item(ctx.state.config["log"]),
            bows=bow_count(ctx, site.wood if site.base_value else None))
        if action == "done":
            return True
        handler = globals().get(action) if action in _ACTIONS else None
        if handler is None or not handler(ctx, site):
            debug_log.log(LOG, "site %d: %s did not land" % (site_number, action))
            return False
    debug_log.log(LOG, "site %d: hit the action ceiling" % site_number)
    return False


def progress_signature(state, site_number=None):
    """What "the routine is getting somewhere" looks like.

    Deliberately EXCLUDES unclaimedOfferings: ents add to those piles on their own every 48s,
    so including them would let a wedged routine look busy -- the same reason Wintertodt's
    signature excludes passively-regenerating warmth.

    SCOPED TO THE SITE BEING WORKED, for the same reason one step further. Ents walk the whole
    ring every 80 ticks and each visit knocks a totem's durability down until it collapses, so
    a signature spanning all eight sites changes on its own every few minutes -- which cleared
    the stall counter of a routine wedged in front of one site, and the watch could never fire.
    The site number is part of the tuple, so walking on to the next site is itself progress and
    a healthy lap can never look stalled.

    site_number=None keeps the whole-ring signature for a caller that wants it.
    """
    sites = sorted(state.sites.values(), key=lambda site: site.number)
    if site_number is not None:
        sites = [site for site in sites if site.number == site_number]
    return (site_number,) + tuple(
        (site.number, site.base_value, site.base_carved, site.segment_values, site.decorations)
        for site in sites)


def before_break(ctx):
    """Finish the site we are standing at before the break starts.

    Cheap insurance rather than a play-out: an unfinished totem simply earns nothing until the
    next lap, so there is no GOTR-style round to protect -- but leaving mid-carve with the
    make menu open would have the break start on an open interface."""
    current = ctx.state.data.get("vt_site")
    if current is not None and at_site(current):
        work_site(ctx, current)
