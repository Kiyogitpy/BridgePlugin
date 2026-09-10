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
import settings
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

# USER 2026-09-10: "we probably dont want to change camera angle on start since it puts
# camera entirely above us looking down meaning the camera skew wont do anything". The
# engine's login camera reset (pitch to the top, zoom out) is what makes a far tile
# un-renderable here; the yaw turn only reaches far with the camera the user left.
KEEP_CAMERA = True

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
    back; the switch lives in data and is re-applied at the top of every tick.

    THE LOGS IN HAND DECIDE FIRST. MEASURED live 2026-09-10 (vt_markers3): the bank trip had
    switched to maple, a restart wiped ctx.state.data, the panel said YEW again, and with 16
    Maple logs and 8 maple bows in hand every site read "Yew logs: 0" -- claimed and walked
    on, three sites, nothing built. So when none of the configured wood's logs are carried
    and a lower wood's are, that wood is adopted (and remembered) before anything reads the
    config. The configured wood is kept while any of its logs are in hand."""
    wood = ctx.state.data.get(WOOD_KEY)
    if not wood and count_item(ctx.state.config["log"]) == 0:
        step = WOOD_FALLBACK.get(ctx.state.config["wood"])
        while step:
            if count_item(WOODS[step]["log"]) > 0:
                debug_log.log(LOG, "holding %s and no %s -- working %s"
                              % (WOODS[step]["log"], ctx.state.config["log"], step))
                wood = step
                break
            step = WOOD_FALLBACK.get(step)
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
    if _basket_top_up_due(ctx):
        empty_basket(ctx)

    # STAND STILL FIRST. See SITE_SETTLE_S -- every action at this site aims at a model a few
    # pixels wide, and a target sliding under the cursor is the difference between "Claim" and
    # "Walk here". A timeout here is not a failure: work_site simply proceeds.
    # ...with the cursor already resting on the first thing to click. MEASURED: the claim's
    # flight from the inventory (where the knife left the cursor) to the pile took 1.5s AFTER
    # the player had stopped. Hovering it during the settle spends that time on the walk.
    _prehover_first_action(ctx, current)
    # CLICK FROM THE HOVER BEFORE THE STOP. USER 2026-09-10: "the hover actually feels a bit
    # pointless unless we actually click with it" -- the tracked cursor was resting on the
    # target for the last tiles of every approach and the click still waited for the player
    # to stand still. The human clicks the totem from a few tiles out and lets the game queue
    # the interaction (the same rule CLAUDE.md's "clicking while moving" section measured at
    # 0.6-1.0s after the walk click). Within EARLY_CLICK_TILES the projection barely slides
    # between the tracker's last re-aim and the mouse-down, so the settle is skipped and the
    # first action fires at once; further out the settle still waits for the stop. A click
    # that resolves to "Walk here" is _click_site's menu retry, and is logged so the miss
    # rate of the early click can be read off the log.
    if _tiles_to_site(current) > EARLY_CLICK_TILES:
        controller.wait_until_player_idle(timeout_s=SITE_SETTLE_S)
    data["vt_fletch_due"] = False        # the leg is over; the next site decides afresh

    # AT THE BANK SITE WITH A TRIP DUE, THE CAMERA TURNS TOWARD THE BOOTH WHILE THE
    # DECORATION RUNS, so the booth is on screen for one direct click afterwards. USER
    # 2026-09-10: "while decorating we can adjust camera if needed and click the banker
    # directly after the decoration is done. that way we save a bit of time not doing hop ->
    # click bank".
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
    if current in BANK_SITES and _bank_trip_due(ctx, state):
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


# A LAP'S WORTH, whatever the ring reads right now. MEASURED run 17 (2026-09-10, 15:15): the
# bank site came round with every totem still reading complete from the lap just walked, the
# outstanding-work need was ~0, no trip was made -- and site 7 finished with 1 log because
# every totem decays before the ring comes back to it (eight visits a lap, each a build and
# four decorations, ~40 units). So the trip is also due whenever hands, basket and bows hold
# less than that; a trip loads ~50, so it fires about once a lap, which is what the laps
# that did bank showed anyway.
LAP_UNITS = len(route.SITES) * vt.LOGS_PER_TOTEM


def _bank_trip_due(ctx, state) -> bool:
    """The lap ahead needs more logs than the hands and the basket hold: the ring's
    outstanding work, or a full lap's worth, whichever is larger."""
    bows = bow_count(ctx)
    need = max(vt.logs_needed_for_lap(state, bows), LAP_UNITS - bows)
    return need > count_item(ctx.state.config["log"]) + basket_logs(ctx.state.data)


def _face_booth(ctx) -> None:
    """Turn the camera toward the booth if it is not already on screen; sends nothing when
    it is."""
    _face_tile(ctx, *route.BANK["tile"])


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


PREHOVER_TRACK_DRIFT_PX = 10   # re-aim the parked cursor once the model has slid this far
PREHOVER_TRACK_POLL_S = 0.15
PREHOVER_STILL_READS = 2       # /player.moving false this many polls running = the run is over
PREHOVER_OFFSET = (-7, 7)      # hover_entity's own default aim offset, kept while tracking


def _prehover_first_action(ctx, site_number: int) -> None:
    """Rest the cursor on the pile if it has offerings, else on the totem base -- whatever
    work_site will click first -- so the click that follows the settle is a click in place.
    Best effort: an object not yet in view simply leaves the cursor where it is."""
    site = _site_now(site_number)
    if site is None:
        return
    which = "offerings" if vt.offering_capped(site) else "totem"
    entry = route.SITES[site_number]
    x, y = entry["totem"] if which == "totem" else entry["offerings"]
    obj_id = entry["base_id"] if which == "totem" else entry["offerings_id"]
    obj = site_object(site_number, which)
    if not obj:
        return
    refresh = controller.object_refresh_target(x, y, route.PLANE, obj_id)
    controller.hover_entity(ctx.ser, obj, refresh_target=refresh, refresh_legs=SITE_REFRESH_LEGS)

    # TRACK UNTIL THE PLAYER STOPS. USER 2026-09-10: "when we get towards the totem the click
    # while moving often misses" -- "the cursor hover while approaching". at_site fires
    # ARRIVAL_TOLERANCE tiles out, so the hover flight ends with the player still running
    # the last tiles in and the projection slides out from under the cursor. While /player
    # says the run is on, the hover follows the live projection whenever it has drifted;
    # the settle wait that follows then finds the cursor already on the model.
    deadline = _now() + SITE_SETTLE_S
    still = 0
    while _now() < deadline:
        player = controller.get_player() or {}
        still = still + 1 if not player.get("moving") else 0
        if still >= PREHOVER_STILL_READS:
            return
        px, py = player.get("x"), player.get("y")
        if px is not None and _tiles_between(px, py, *entry["stand"]) <= EARLY_CLICK_TILES:
            return                       # close enough to click from the hover -- see tick()
        target = refresh() if callable(refresh) else None
        if target:
            tx, ty = int(target[0]) + PREHOVER_OFFSET[0], int(target[1]) + PREHOVER_OFFSET[1]
            cx, cy = controller.get_cursor_pos()
            if max(abs(cx - tx), abs(cy - ty)) > PREHOVER_TRACK_DRIFT_PX:
                controller.move_to_human(ctx.ser, tx, ty, aim_radius=controller.ENTITY_AIM_RADIUS)
        _sleep(PREHOVER_TRACK_POLL_S)


def _click_site(ctx, site_number, which, budget_s, confirm, option=None, after_click=None):
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
    # NOT WHILE THE PREVIOUS ACTION IS STILL ANIMATING. MEASURED run 16 (2026-09-10): with the
    # in-place click the Decorate went out 0.66s after the third carve key, inside the carve's
    # animation, registered as Decorate and did nothing -- the same drop the carve keys
    # suffer. The flight used to add the half second that hid this; the wait costs only the
    # animation's tail, and only when there is one.
    _await_idle_animation()
    before_action = controller.get_last_action() or {}
    if not controller.click_entity(ctx.ser, obj, refresh_target=refresh,
                                   refresh_legs=SITE_REFRESH_LEGS):
        return False
    if after_click is not None:
        after_click()          # e.g. the camera turn toward the booth while the action runs
    # A MISS IS KNOWN WITHIN A SECOND, NOT AT THE BUDGET. MEASURED runs 13-14: an early click
    # that resolved to "Walk here" spent the whole 6s confirm budget before /last-action was
    # consulted, and that one wait cost more than the early click saved over ten sites.
    # /last-action names what the click became as soon as the game processes it, so it is
    # polled for the click's own record first; one that is not ours goes straight to the menu.
    missed = option and _click_missed(before_action, option)
    outcome = None
    if not missed:
        outcome = outcome_waits.await_pre(ctx, ["VARBIT_CHANGED", "CHAT_MESSAGE"], budget_s,
                                          confirm, now_fn=_now, sleep_fn=_sleep)
        if outcome or not option:
            return bool(outcome)
        last = controller.get_last_action() or {}
        if (last.get("option") or "").lower() == option.lower():
            return False            # the right action registered and still did not confirm
    last = controller.get_last_action() or {}
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


def _await_idle_animation(budget_s: float = None) -> bool:
    """Poll until the player's animation reads -1, at most CARVE_KEY_ANIM_WAIT_S."""
    return _poll_prompt(lambda: (controller.get_player() or {}).get("animation", -1) == -1,
                        CARVE_KEY_ANIM_WAIT_S if budget_s is None else budget_s, 0.1)


MISS_CHECK_S = 1.0          # how long /last-action is polled for the click's own record
MISS_CHECK_POLL_S = 0.15


def _click_missed(before_action: dict, option: str) -> bool:
    """True once /last-action shows a NEW record (not `before_action`) whose option is not
    `option`; False when the record is ours, or when nothing new appeared within MISS_CHECK_S
    (the click may still register on arrival -- the caller's confirm decides that)."""
    def key(rec):
        return (rec.get("tick"), rec.get("option"), rec.get("target"), rec.get("id"))
    deadline = _now() + MISS_CHECK_S
    while True:
        last = controller.get_last_action() or {}
        if last and key(last) != key(before_action):
            return (last.get("option") or "").lower() != option.lower()
        if _now() >= deadline:
            return False
        _sleep(MISS_CHECK_POLL_S)


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
# A landed carve key carves within ~0.6s (MEASURED, every carve in vt-markers7-approach); the
# human re-presses a dropped one at ~1.0-1.2s. See carve() for the race this covers.
CARVE_KEY_REPRESS_S = 1.5
CARVE_KEY_ANIM_WAIT_S = 1.0     # longest wait for the previous animation before the first key


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


# The tracked hover clicks from this close instead of waiting for the stop; see tick().
EARLY_CLICK_TILES = 1        # 2 missed 3 of ~23 early clicks (runs 13-14); the last tile slides less


def _tiles_to_site(site_number: int) -> int:
    """Chebyshev tiles from the player to the site's stand tile; a large number when the
    player cannot be read, so a caller's "close enough" test fails safe."""
    x, y = _player_xy()
    if x is None or y is None:
        return 10 ** 6
    sx, sy = route.SITES[site_number]["stand"]
    return _tiles_between(x, y, sx, sy)


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


# The straight line toward the stand is tried before any marker: points every LINE_STEP_TILES
# from just short of the stand down to LINE_MIN_TILES out, the farthest that projects wins.
LINE_STEP_TILES = 3
LINE_MIN_TILES = 8


def _line_candidates(px, py, sx, sy):
    """Tiles on the straight line (px,py) -> (sx,sy), farthest first, short of the stand."""
    dist = _tiles_between(px, py, sx, sy)
    out = []
    d = dist - LINE_STEP_TILES
    while d >= LINE_MIN_TILES:
        t = d / dist
        out.append((int(round(px + (sx - px) * t)), int(round(py + (sy - py) * t))))
        d -= LINE_STEP_TILES
    return out


# A viewport candidate must project at least this far inside the canvas edge. MEASURED live
# 2026-09-10 (vt_markers12, 6->5): the stand-line point projected on the canvas's TOP ROW
# (screen y 23 with the canvas starting at 23), isOnScreen said yes, and the click's own
# landing jitter put it at y 22 -- the window frame. The player never moved and the target
# was banned for the session.
VIEWPORT_EDGE_MARGIN_PX = 24


def _viewport_click_ok(point) -> bool:
    """On screen, not under a panel, and clear of the canvas edges by the margin."""
    if not point.get("onScreen"):
        return False
    x, y = point.get("screenX", -1), point.get("screenY", -1)
    if controller.is_point_blocked_by_ui(x, y):
        return False
    st = controller.get_state() or {}
    left, top = st.get("canvasScreenX"), st.get("canvasScreenY")
    width, height = st.get("canvasWidth"), st.get("canvasHeight")
    if None in (left, top, width, height):
        return True                       # cannot tell -- the old behaviour
    m = VIEWPORT_EDGE_MARGIN_PX
    return left + m <= x <= left + width - m and top + m <= y <= top + height - m


def _far_click_target(b: int, points, px=None, py=None, ser=None, skip=()):
    """The farthest point of the leg that ONE click can reach right now: site `b`'s stand
    tile first, then the recorded waypoints from the far end. A point is reachable if it is
    rendered in the viewport (and not under a panel) OR on the minimap (and not on an orb).

    The minimap half is what makes the stretches long enough. MEASURED 2026-09-09 with the
    viewport alone: the picked point was 11-18 tiles out on most legs -- 7-11s, 3-5 bows,
    then the next click ended the fletch. The minimap reaches 28 tiles whatever the camera
    renders (CLAUDE.md's measurement), and the game paths there on its own."""
    # FACE THE CAMERA AT THE LEG FIRST (USER 2026-09-10: "i can click much further by nudging
    # the camera in the direction of the totem and click the totem directly instead of minimap
    # hop" -- six middle-mouse drags in the recording, each right before a far viewport click).
    # The stand tile is 20-57 tiles off and not rendered at the default camera; turning toward
    # it brings it (and the near waypoints) on screen, so a direct viewport click reaches far
    # further than a minimap hop. Only when it is genuinely off screen -- a turn is not free of
    # a mouse flight, so a tile already rendered is left alone.
    sx, sy = route.SITES[b]["stand"]
    if ser is not None and not controller.get_ground_walk_point(
            sx, sy, route.PLANE).get("onScreen"):
        controller.face_toward(ser, sx, sy, px=px, py=py)

    # VIEWPORT FIRST for each candidate now that the camera faces the leg: the human clicks the
    # world directly (the recording has zero minimap hops on these legs). The minimap is the
    # fallback for a point the turn could not bring on screen. Farthest candidate first.
    # STRAIGHT TOWARD THE TOTEM BEFORE ANY MARKER. USER 2026-09-10: "we click the tile marker
    # 20 instead of just running straight to totem ... they are guidelines not meant as a
    # hop". MEASURED: from the booth the stand tile 37 tiles out projected ABOVE the
    # viewport's top edge (isOnScreen is a bounds test) while the ground short of it was on
    # screen, so the farthest on-screen point on the line to the stand is the click; the
    # markers only answer when nothing on that line projects.
    line = [] if px is None else _line_candidates(px, py, sx, sy)
    for cx, cy in [(sx, sy)] + line + [tuple(pt) for pt in reversed(points)]:
        # A MARKER ALREADY REACHED IS NOT A FAR CLICK. MEASURED live 2026-09-10 (vt_markers2,
        # legs 6->5 and 5->4): a stretch ends within ARRIVAL_TOLERANCE of its marker, the
        # stand tile is neither rendered nor on the minimap, and the marker just reached --
        # still rendered, three tiles off -- was picked again five times in five seconds,
        # each click ending the fletch. Nothing farther reachable means NO_CLICK; the hops
        # take it from here and pull an off-minimap point in.
        if px is not None and _tiles_between(px, py, cx, cy) <= ARRIVAL_TOLERANCE:
            continue
        if (cx, cy) in skip:            # a target the game would not path to from this site
            continue
        point = controller.get_ground_walk_point(cx, cy, route.PLANE)
        if _viewport_click_ok(point):
            return cx, cy, point, True
        # WITHIN THE MINIMAP'S REAL REACH. /minimap/walk's onMinimap is a generous test
        # (CLAUDE.md: the cardinals still read "on" at 32 tiles) and a 38-tile point projected
        # to a pixel outside the drawn circle, which clicks nothing (USER 2026-09-09: "long
        # leg hop clicked outside of minimap window resulting in small hops"). 24 is the
        # measured-safe hop range travel already uses.
        in_reach = (px is None
                    or _tiles_between(px, py, cx, cy) <= travel.WALK_HOP_MAX_TILES)
        point = controller.get_minimap_walk_point(cx, cy, route.PLANE) if in_reach else {}
        if point.get("onMinimap") and not controller.is_point_on_minimap_orb(
                point.get("screenX", -1), point.get("screenY", -1)):
            return cx, cy, point, False
    return None


def _ahead(points, origin, x, y):
    """The markers still ahead of (x, y) along the leg's polyline -- the one ending the
    nearest stretch and everything after it, minus one already reached."""
    start = _resume_index(points, origin, x, y)
    if start < len(points) - 1 and _tiles_between(x, y, *points[start]) <= ARRIVAL_TOLERANCE:
        start += 1
    return points[start:]


def _walk_point_now(cx, cy):
    """(point, viewport) for tile (cx, cy) as the client projects it RIGHT NOW: the viewport
    when rendered and uncovered, else the minimap when on it and off the orbs, else None."""
    if cx is None or cy is None:
        return None, False
    point = controller.get_ground_walk_point(cx, cy, route.PLANE)
    if _viewport_click_ok(point):
        return point, True
    point = controller.get_minimap_walk_point(cx, cy, route.PLANE)
    if point.get("onMinimap") and not controller.is_point_on_minimap_orb(
            point.get("screenX", -1), point.get("screenY", -1)):
        return point, False
    return None, False


HOOK_MIN_STRETCH_TILES = 12    # ~4s of running: a bow, or three strings; shorter is not worth it


def _hook_due(stretch_tiles: int, final: bool) -> bool:
    return final or stretch_tiles >= HOOK_MIN_STRETCH_TILES


def _direct_leg(ctx, b: int, points, fire_hook, origin, skip=()):
    """ONE click at the farthest reachable point of the leg, fire the hook, then only poll.

    ARRIVED: at the site. STRETCH: the intermediate point was reached, the hook has fired --
    the caller clicks again from here. NO_CLICK: nothing reachable, nothing clicked.
    STALLED: the hook has fired but the player stopped short (a log balance the game's own
    pathfinder will not cross), so the recorded hops take over. Every click before the
    stretch is walked would end the fletch, which is why there is exactly one per stretch."""
    here = _player_xy()
    # ONLY THE MARKERS AHEAD. MEASURED live 2026-09-10 (vt_markers4, 8->7 from the booth):
    # the first stretch reached the middle marker, the stand tile was not rendered, and the
    # pick fell to the first marker -- 20 tiles BACK toward the bank just left.
    ahead = points if here[0] is None else _ahead(points, origin, *here)
    target = _far_click_target(b, ahead, *here, ser=ctx.ser, skip=skip)
    if target is None:
        return NO_CLICK, None
    cx, cy, point, viewport = target
    sx, sy = route.SITES[b]["stand"]
    travel._click_walk_point(ctx, point, shift=viewport)
    # THE ACTION GOES ON EVERY STRETCH WORTH IT. USER 2026-09-10: "no stringing on that long
    # walk?" -- a 48-tile first stretch toward site 1 ran with idle hands while the action
    # waited for the final stand-tile click, 9 tiles from the end. A walk click always ends a
    # make action, so a later stretch click restarts it; a stretch too short to make a bow
    # (HOOK_MIN_STRETCH_TILES) is skipped unless it is the final one.
    stretch = 0 if here[0] is None else _tiles_between(here[0], here[1], cx, cy)
    started = fire_hook() if _hook_due(stretch, final=(cx, cy) == (sx, sy)) else False
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
    bow_name = ctx.state.config["bow"]
    reserve = int(ctx.state.data.get(LOG_RESERVE_KEY) or 0)

    deadline = _now() + DIRECT_LEG_MAX_S
    last_xy, still_since = here, _now()
    while _now() < deadline:
        _sleep(DIRECT_LEG_POLL_S)
        if fletching and (count_item(log_name) <= reserve
                          or count_item(bow_name) >= STRING_ABOVE_BOWS):
            # THE SPOOL IS THE INTERRUPTION. USER 2026-09-10: "instead of clicking to
            # interupt the fletch we can use the spool to start stringing to interupt it
            # instead. that way no new walk click will be needed and it can be done mid
            # walk" -- and "lower it to fire at 8 bows in inventory": the fletch ends either
            # at the build reserve or once STRING_ABOVE_BOWS unstrung bows are in hand, and
            # the hands go straight on to stringing them. Only without a usable spool (or
            # nothing to string) is the walk re-clicked, at THE SAME TILE, RE-RESOLVED --
            # never the same pixel (vt_markers4: the stale pixel overshot site 5 by 17 tiles).
            strung = False
            if (count_item(bow_name) > 0 and find_spool()
                    and not ctx.state.data.get(SPOOL_DEAD_KEY)):
                # THE BASKET FIRST, THEN THE SPOOL. MEASURED 2026-09-10 (run vt_markers9,
                # leg 4->3): the at-site Empty landed 0.7s after a string and the next string
                # slot never fired -- an inventory Empty ENDS a running action. So an Empty
                # sent after the spool would cut the stringing short; sent BEFORE it, it is
                # the interruption itself (the fletch ends on it), the logs come aboard, and
                # the stringing then runs untouched for the rest of the stretch with no
                # top-up left to do at the site. Only a basket KNOWN to hold logs: if the
                # Empty were ever not to end the fletch, the spool click ~1.5s later does,
                # and the one log that could be fletched meanwhile is one the Empty brought.
                if _basket_top_up_due(ctx) and basket_logs(ctx.state.data) > 0:
                    _empty_on_the_move(ctx, log_name)
                strung = start_stringing(ctx)
                if strung:
                    debug_log.log(LOG, "fletch -> stringing mid-walk (%d %s, %d %s left)"
                                  % (count_item(bow_name), bow_name, count_item(log_name),
                                     log_name))
            if not strung:
                fresh, fresh_viewport = _walk_point_now(cx, cy)
                if fresh is None:
                    fresh, fresh_viewport = _walk_point_now(*_player_xy()), True
                if fresh is not None:
                    travel._click_walk_point(ctx, fresh, shift=fresh_viewport)
                debug_log.log(LOG, "fletch stopped at the build reserve (%d %s left)"
                              % (reserve, log_name))
                # THE BASKET TOP-UP HAPPENS HERE, ON THE MOVE, when the hands have just gone
                # idle -- not in front of the totem. MEASURED vt_markers8: at the site the
                # Empty (click, delta wait) held the pre-hover flight back ~2s at every site
                # it fired on, while the rest of this stretch was walked with nothing to do.
                # ONLY after the walk re-click: an Empty is an inventory op and the human's
                # recording suggests it ends a running action (a fletch stopped on the tick
                # of an Empty), so it is never sent over the stringing the other branch just
                # started. A later stretch's hook then fletches the logs it brought.
                if _basket_top_up_due(ctx):
                    _empty_on_the_move(ctx, log_name)
            fletching = False
        if at_site(b):
            return ARRIVED, (cx, cy)
        xy = _player_xy()
        if (cx, cy) != (sx, sy) and xy[0] is not None \
                and _tiles_between(xy[0], xy[1], cx, cy) <= ARRIVAL_TOLERANCE:
            return STRETCH, (cx, cy)
        if xy != last_xy:
            last_xy, still_since = xy, _now()
        elif _now() - still_since >= DIRECT_LEG_STALL_S:
            debug_log.log(LOG, "direct leg to site %d stalled at %s -- falling back to the "
                               "recorded hops" % (b, xy))
            return STALLED, (cx, cy)
    debug_log.log(LOG, "direct leg to site %d: not there after %.0fs -- falling back to the "
                       "recorded hops" % (b, DIRECT_LEG_MAX_S))
    return STALLED, (cx, cy)


def _empty_on_the_move(ctx, log_name: str) -> bool:
    ok = empty_basket(ctx)
    if ok:
        debug_log.log(LOG, "basket emptied on the move: %d %s loose"
                      % (count_item(log_name), log_name))
    return ok


def _resume_index(points, origin, x, y) -> int:
    """Index of the marker ending the polyline stretch (origin -> points...) nearest (x, y)."""
    best, best_d = 0, None
    prev = origin
    for i, (qx, qy) in enumerate(points):
        vx, vy = qx - prev[0], qy - prev[1]
        length2 = vx * vx + vy * vy
        t = 0.0 if not length2 else max(0.0, min(1.0, ((x - prev[0]) * vx + (y - prev[1]) * vy)
                                                    / length2))
        cx, cy = prev[0] + t * vx, prev[1] + t * vy
        d = (x - cx) ** 2 + (y - cy) ** 2
        if best_d is None or d < best_d:
            best, best_d = i, d
        prev = (qx, qy)
    return best


# A leg is at most this many far clicks before the hops take over -- the longest leg (57
# tiles) is two or three 24-28 tile stretches; more than five means something is circling.
DIRECT_LEG_MAX_STRETCHES = 5


def walk_leg(ctx, a: int, b: int, after_first_hop=None) -> bool:
    """Walk site `a` -> site `b` with STRAIGHT clicks: the stand tile itself whenever the
    client can project it (viewport with the camera faced at it, else the minimap within
    reach), otherwise the farthest of USER's markers still ahead. No marker-by-marker hops,
    no shortcuts -- USER 2026-09-10: "click straight near the totems no small hops or
    shortcuts"; "the markers are a guide not an enforcement". The markers only ever say where
    to aim when the totem itself cannot be aimed at.

    NO ROUTER: worldmap has no collision data for Varlamore, so travel.walk_to cannot be used
    here at all; the game's own pathfinder walks whatever tile is clicked.

    `after_first_hop` runs ONCE, immediately after the leg's first move click is out. USER
    2026-09-09: "can only fletch after we give the move command" -- a knife-on-log started
    while standing still is cancelled by the very next walk click, so the fletch has to be
    issued with the player already on its way. That is the hook; see tick() for the caller.
    """
    points = route.leg_waypoints(a, b)
    if not points:
        debug_log.log(LOG, "no recorded path for leg %d->%d" % (a, b))
        return False

    # ONE FAR CLICK, THEN HANDS OFF -- the human's shape, and the only one a fletch survives.
    # MEASURED 2026-09-09 from the recording: every fletch run (5-9 bows) has ZERO clicks
    # between the knife and the next site -- one "Walk here" on a tile up to 31 tiles away,
    # then bows every 1.8s untouched. ONCE PER LEG: a walk click ALWAYS ends a make action
    # (the recording's uninterrupted runs are the proof), so the fletch is started on the
    # first move only.
    def fire_hook():
        # Returns the hook's own verdict ("fletch" / "string" / False) because _direct_leg
        # only watches the log reserve for a fletch it knows is running. Fired on EVERY
        # stretch that is long enough or final (see _hook_due) -- the hook re-derives its
        # budget live, so a restart after a click that ended the action is safe.
        return False if after_first_hop is None else after_first_hop()

    # A TARGET the game refused to path to is remembered PER TARGET, not per leg. MEASURED
    # live 2026-09-10 (vt_markers4, 1->8): a viewport click straight at site 8's stand tile 46
    # tiles off moved the player 5 tiles and stopped (the game's pathfinder gives up on that
    # line -- the river); the next attempt skips that target and takes the farthest marker.
    no_direct = ctx.state.data.setdefault(NO_DIRECT_KEY, {})
    banned = no_direct.setdefault(str((a, b)), [])
    skip = {tuple(t) for t in banned}
    origin = route.SITES[a]["stand"]
    sx, sy = route.SITES[b]["stand"]

    # A BOUNDED CHAIN OF STRAIGHT CLICKS. Each iteration is one click: a far click at the
    # farthest projectable candidate, or -- when nothing projects -- one minimap hop AT the
    # stand tile, which travel._hop_to pulls in toward the player when it is beyond reach.
    # A stalled click is retried from wherever the player stopped, its target banned.
    for _ in range(DIRECT_LEG_MAX_STRETCHES):
        if at_site(b):
            return True
        verdict, target = _direct_leg(ctx, b, points, fire_hook, origin, skip)
        if verdict == ARRIVED:
            return True
        if verdict == STRETCH:
            continue
        if verdict == STALLED:
            if target is not None and target not in skip:
                skip.add(target)
                banned.append(list(target))
                debug_log.log(LOG, "leg %d->%d: the click at %s did not walk there -- "
                                   "not tried again this session" % (a, b, target))
            continue
        # NO_CLICK: nothing projectable. One hop toward the totem itself.
        if not travel._hop_to(ctx, sx, sy, route.PLANE):
            debug_log.log(LOG, "leg %d->%d: hop toward site %d failed" % (a, b, b))
            return False
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
    """Carve the missing spirits into the totem: ONE Carve click, then the prompt answered as
    many times as it reopens.

    USER 2026-09-10: "we dont have to click inbetween choosing each totem piece like 1 2 3
    the menu will pop up after we choose one until we chose all three". Each spirit is
    re-derived from a fresh read before it is chosen, because the wiki says the nearby trio
    can change WHILE the player is carving. A prompt that does not reopen ends the call with
    what was carved; the caller's ladder re-reads the site and clicks Carve again if needed.
    """
    n = site.number
    fresh = _site_now(n) or site
    if not vt.missing_spirits(fresh):
        return False

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

    carved_any = False
    for index in range(3):
        if not _await_menu(ctx):
            if not carved_any:
                debug_log.log(LOG, "site %d: carve menu never opened" % n)
            return carved_any
        if index == 0:
            # THE FIRST KEY WAITS FOR THE BUILD ANIMATION TO END. MEASURED across runs 9-13
            # (2026-09-10): the dropped first keys (0.42-0.56s after the Carve click) and the
            # landed ones (0.43-1.47s) overlap completely in delay; what separates them in the
            # recorder is that every dropped key went out while the player's animation (the
            # build's 827, or the previous action's) was still playing. The prompt is already
            # up by then, so this costs at most the animation's tail; the re-press below stays
            # as the belt for the tick-phase race it cannot see.
            _await_idle_animation()
        fresh = _site_now(n) or fresh
        missing = vt.missing_spirits(fresh)
        if not missing:
            return True
        before = set(fresh.segment_values)          # BEFORE the key: the varbit can move fast
        if not choose_spirit(ctx, missing[0]):
            return carved_any

        def landed():
            after = _site_now(n)
            return after is not None and set(after.segment_values) != before

        outcome = outcome_waits.await_pre(ctx, ["VARBIT_CHANGED", "CHAT_MESSAGE"],
                                          CARVE_KEY_REPRESS_S, landed,
                                          now_fn=_now, sleep_fn=_sleep)
        if not outcome:
            # THE GAME DROPS A NUMBER PRESSED WHILE THE PREVIOUS ANIMATION IS STILL PLAYING.
            # MEASURED 2026-09-10 (recording vt-markers7-approach, site 4): Carve click
            # registered, key at +0.4s with the build's erect animation (827) still running,
            # busy varbit 12393 went 0 then 1 with NO carve behind it, and nothing happened
            # for six seconds until a fresh Carve click. Every landed key in the same
            # recording came at or after the busy drop (+0.5..+0.7s) and carved within 0.6s.
            # The human's own recording has the identical signature twice (keys at +0.4s,
            # nothing) and recovers both times by pressing the SAME number again ~1s later,
            # no new click -- so that is what this does, once. A key that landed carves well
            # inside CARVE_KEY_REPRESS_S, so the re-press costs nothing on a good tick, and
            # choose_spirit re-checks the title first: a prompt that has gone means a new
            # Carve click (the caller's retry), not a blind keypress.
            debug_log.log(LOG, "site %d: the %s key did not carve within %.1fs -- pressing it "
                               "again" % (n, missing[0], CARVE_KEY_REPRESS_S))
            if not choose_spirit(ctx, missing[0]):
                return carved_any
            outcome = outcome_waits.await_pre(ctx, ["VARBIT_CHANGED", "CHAT_MESSAGE"],
                                              CARVE_BUDGET_S - CARVE_KEY_REPRESS_S, landed,
                                              now_fn=_now, sleep_fn=_sleep)
        if not outcome:
            return carved_any
        carved_any = True
    return True


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


def decorate(ctx, site, after_click=None) -> bool:
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
    return _click_site(ctx, n, "totem", budget, landed, option="Decorate", after_click=after_click)


LOG_RESERVE_KEY = "vt_log_reserve"
STRING_ABOVE_BOWS = 8            # two totems' worth in hand: string instead of fletching
SPOOL_NAMES = ("Bow string spool",)
SPOOL_DEAD_KEY = "vt_spool_dead"  # a spool that strung nothing (empty?) is not tried again
SPOOL_STRIKES_KEY = "vt_spool_strikes"
SPOOL_STRIKES = 2                 # failed uses before the spool is written off for the session
STRING_START_BUDGET_S = 6.0       # recording: the first string lands +2.1s after the prompt
STRING_PROMPT_WAIT_S = 1.5
STRING_PROMPT_POLL_S = 0.15


def _poll_prompt(predicate, budget_s: float, poll_s: float) -> bool:
    """True as soon as predicate() holds, False once budget_s has passed without it."""
    deadline = _now() + budget_s
    while True:
        if predicate():
            return True
        now = _now()
        if now >= deadline:
            return False
        _sleep(min(poll_s, deadline - now))


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
    # The spool's own options are Fill/Check/Empty/Drop -- "Use" is only its left click when
    # the account's menu swap puts it there (USER 2026-09-09: "i changed the menu to be use on
    # leftclick"); the right-click menu is the fallback for a client without the swap.
    used = controller.click_entity(ctx.ser, spool, offset=(0, 0), check_ui_blockers=False,
                                   simulate_mistakes=False, expected_action="Use")
    if not used:
        used = controller.click_menu_action(ctx.ser, spool, "use", offset=(0, 0),
                                            check_ui_blockers=False)
    if not used:
        return False
    if not controller.click_entity(ctx.ser, bow, offset=(0, 0), check_ui_blockers=False,
                                   simulate_mistakes=False):
        return False
    # A prompt, if the game asks. The MAKE menu is answered by the strung bow's own number;
    # any other group-270 prompt (a quantity prompt: one product, so nothing to choose) with
    # Space, the key the human's recording uses. Missing the prompt was the first live
    # failure: the string never started, and the spool was written off.
    # SPACE FOLLOWS THE BOW CLICK WHETHER OR NOT A PROMPT WAS SEEN. MEASURED live 2026-09-10
    # (vt_markers5 vs 6): the string landed only when Space was pressed; twice the prompt was
    # not detected inside the wait, nothing was pressed, and the spool struck out. The
    # human's recording presses Space ~0.8s after the bow click regardless, and Space with
    # nothing open does nothing. Only a MAKE-titled menu (several products) is answered by
    # the strung bow's own number instead.
    # POLLED, not awaited on WIDGET_LOADED. MEASURED runs vt_markers9/10 (19 string starts):
    # bow click -> Space was 0.6-1.05s (the human: ~0.8s) except twice at 1.75s, when the
    # prompt's event was missed and the whole STRING_PROMPT_WAIT_S went by before the one
    # HTTP read at the budget noticed it. A widgets dump is ~50ms, so polling it every
    # STRING_PROMPT_POLL_S sees the prompt within ~0.2s of it opening every time; the human
    # reaction draw then pads the keypress as the event path would have.
    if _poll_prompt(skillmulti.is_open, STRING_PROMPT_WAIT_S, STRING_PROMPT_POLL_S):
        pad = controller.sample_human_delay(*settings.REACTION_DELAY_S)
        if pad > 0:
            _sleep(pad)
    if skillmulti.showing(skillmulti.TITLE_MAKE):
        widget = skillmulti.item_widget(ctx.state.config["strung"])
        option = skillmulti.option_number(widget) if widget else None
        if option is not None:
            controller.key_tap(ctx.ser, str(option))
        elif widget:
            controller.click_entity(ctx.ser, widget, offset=(0, 0),
                                   check_ui_blockers=False, simulate_mistakes=False)
        else:
            _close_prompt(ctx)
            return False
    else:
        controller.key_tap(ctx.ser, "SPACE")
    ok = bool(outcome_waits.await_pre(ctx, ["INVENTORY_CHANGED"], STRING_START_BUDGET_S,
                                      lambda: count_item(unstrung) < before,
                                      now_fn=_now, sleep_fn=_sleep))
    if not ok:
        # TWO STRIKES. MEASURED live 2026-09-10 (vt_markers4): the FIRST use strung nothing
        # inside the budget and the spool was written off for the session, so every later
        # leg fletched with 19 unstrung bows in hand. /last-action says what the bow click
        # actually did, for the next diagnosis.
        strikes = ctx.state.data.get(SPOOL_STRIKES_KEY, 0) + 1
        ctx.state.data[SPOOL_STRIKES_KEY] = strikes
        last = controller.get_last_action() or {}
        debug_log.log(LOG, "the spool strung nothing (strike %d of %d; last action %r on %r)"
                      % (strikes, SPOOL_STRIKES, last.get("option"), last.get("target")))
        if strikes >= SPOOL_STRIKES:
            ctx.state.data[SPOOL_DEAD_KEY] = True
            debug_log.log(LOG, "not trying the spool again this session -- is it loaded?")
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
        # NOTHING WORTH FLETCHING, BUT UNSTRUNG BOWS IN HAND: string them. USER 2026-09-10:
        # "nor is it stringing the bows" -- live (vt_markers2) the later legs held 5 logs all
        # owed to builds and 6 unstrung bows, so the leg walked with the hands idle. Free XP
        # on the same walk; a strung bow decorates just as well.
        if (count_item(ctx.state.config["bow"]) > 0 and find_spool()
                and not data.get(SPOOL_DEAD_KEY)):
            return "string" if start_stringing(ctx) else False
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


def _basket_top_up_due(ctx) -> bool:
    """Loose logs below a totem's worth plus spare, a basket carried, and the basket not
    KNOWN to be empty (None is unknown -> try it, and the first Empty teaches the
    estimate). The one condition behind both the at-site top-up and the on-the-leg one."""
    data = ctx.state.data
    return (bool(find_basket()) and count_item(ctx.state.config["log"]) < LOOSE_LOG_TARGET
            and (data.get(BASKET_LOGS_KEY) is None or basket_logs(data) > 0))


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
    # MOVED IS MEASURED IN FREE SLOTS, NOT LOOSE LOGS. MEASURED 2026-09-10 (runs 11-13): the
    # on-the-move Empty goes out while a fletch is still running, a log became a bow between
    # the `before` read and the Empty landing, the loose delta came out one short of the free
    # slots, and the rule below called the basket EMPTY with ~17 logs still in it -- no Empty
    # was tried again that run and the lap ran dry one site early. A fletch swaps a log for
    # a bow in the same slot, so the free-slot delta is exactly what the Empty moved.
    free_after = 28 - len(controller.get_inventory() or [])
    moved = max(0, free_before - free_after)
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

# Everything the routine carries on purpose; anything else that turns up in the inventory
# (the Molten glass a mis-aimed withdraw pulled, 2026-09-09) is banked at the next trip.
# Substrings, banker.deposit_inventory's convention: "logs" and "longbow" cover every wood.
KEEP_SUBSTRINGS = ("basket", "knife", "spool", "offerings", "logs", "longbow")
BANK_SLOT_SETTLE_S = 2.0
BANK_SLOT_POLL_S = 0.3


def _deposit_junk(ctx) -> None:
    """Shift-deposit whatever is not on KEEP_SUBSTRINGS, only when there is something."""
    junk = [i.get("name") for i in (controller.get_inventory() or [])
            if not any(k in (i.get("name") or "").lower() for k in KEEP_SUBSTRINGS)]
    if not junk:
        return
    debug_log.log(LOG, "bank: depositing %s" % sorted(set(junk)))
    banker.deposit_inventory(ctx, {"keepItems": list(KEEP_SUBSTRINGS),
                                   "skipContainerDeposit": True})


def _bank_slot(name):
    bank = controller.get_bank(fresh=True) or {}
    for item in bank.get("items") or []:
        if (item.get("name") or "").lower() == name.lower():
            return (item.get("screenX"), item.get("screenY"))
    return None


def _await_bank_slot(name) -> bool:
    """True once two consecutive fresh reads agree on where `name` sits in the open bank."""
    deadline = _now() + BANK_SLOT_SETTLE_S
    last = _bank_slot(name)
    while _now() < deadline:
        _sleep(BANK_SLOT_POLL_S)
        now = _bank_slot(name)
        if now is not None and now == last:
            return True
        last = now
    return False

BANK_HOP_ATTEMPTS = 4
BANK_DIRECT_BUDGET_S = 4.0     # on top of the walk: the click, the approach, the interface


def _open_bank_direct(ctx) -> bool:
    """Click the booth from where we stand and wait for the interface, walk budgeted.
    Not from the booth's own stand tile: there banker.open_bank's click is the same thing."""
    entry = route.BANK
    px, py = _player_xy()
    if px is not None and _tiles_between(px, py, *entry["stand"]) <= ARRIVAL_TOLERANCE:
        return False
    booth = controller.get_object_at(entry["tile"][0], entry["tile"][1], route.PLANE,
                                     entry["object_id"])
    if not booth or not booth.get("onScreen", True):
        return False
    refresh = controller.object_refresh_target(entry["tile"][0], entry["tile"][1],
                                              route.PLANE, entry["object_id"])
    if not controller.click_entity(ctx.ser, booth, refresh_target=refresh,
                                   refresh_legs=SITE_REFRESH_LEGS):
        return False
    tiles = booth.get("distance") or (0 if px is None else _tiles_between(px, py, *entry["stand"]))
    budget = BANK_DIRECT_BUDGET_S + tiles * outcome_waits.WALK_S_PER_TILE
    opened = bool(outcome_waits.await_pre(ctx, ["WIDGET_LOADED"], budget, banker.is_open,
                                          now_fn=_now, sleep_fn=_sleep))
    debug_log.log(LOG, "bank: direct booth click %s" % ("opened the bank" if opened
                                                       else "did not open the bank -- hopping"))
    return opened



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


def _exact_stock(memory, name: str) -> int:
    """Remembered quantity of the item called exactly `name` (case-insensitive)."""
    return sum(int(item.get("quantity") or 0) for item in (memory.items or [])
               if (item.get("name") or "").strip().lower() == name.strip().lower())


def bank_trip(ctx) -> bool:
    """Walk to the booth, then Withdraw-All logs / close / Fill the basket for three rounds --
    the recording's own sequence.

    "You don't have enough inventory space to withdraw that many." is NORMAL here and must not
    be treated as a failure; fletching.py records the same thing for the same click.

    The walk is part of the trip, not the caller's job: the booth only resolves once we are
    standing beside it, and the caller (tick) has already established we are on a BANK_SITES
    site, which is the only place this walk is short and recorded.
    """
    # ONE DIRECT CLICK ON THE BOOTH FROM THE SITE (~21 tiles): the game walks there and opens
    # on arrival. USER 2026-09-10: "click the banker directly after the decoration is done.
    # that way we save a bit of time not doing hop -> click bank". The hop is the fallback.
    if not _open_bank_direct(ctx) and not _walk_to_bank(ctx):
        return False
    # OUT OF THIS WOOD? The bank memory (every tab, search or not) answers before the first
    # withdraw; 0 -- known empty, never "never seen" -- steps the wood down one.
    # UNFILTERED, on purpose. MEASURED 2026-09-09 on the host: the name-filtered read answered
    # "0 Yew logs" with 129 in the bank and the routine withdrew maple (USER: "it just withdrew
    # maple when yew still exist"). The whole bank is one read per bank trip, and the answer
    # is trusted only when the memory has actually SEEN a populated bank.
    # EXACT NAME, not bank_memory.quantity's substring match. MEASURED live 2026-09-10
    # (vt_markers1): with no Yew logs banked at all, "Abyssal lantern (yew logs)" counted as
    # one, the fallback never fired, and the run stopped at the booth holding maple.
    memory = bank_memory.load()
    quantity = _exact_stock(memory, ctx.state.config["log"]) if memory is not None else None
    if memory is not None and memory.seen and len(memory.items or []) > 0 and quantity == 0:
        fallback = WOOD_FALLBACK.get(ctx.state.config["wood"])
        if not fallback:
            debug_log.log(LOG, "the bank is out of %s and there is no wood below %s"
                          % (ctx.state.config["log"], ctx.state.config["wood"]))
            return False
        debug_log.log(LOG, "the bank is out of %s (memory: %d items, %s of them) -- "
                           "switching to %s" % (ctx.state.config["log"], len(memory.items),
                                                quantity, fallback))
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
        if round_index == 0:
            _deposit_junk(ctx)
        # LET THE INTERFACE LAY OUT before aiming at a slot. MEASURED on the host 2026-09-09:
        # the first withdraw after the bank opened hit MOLTEN GLASS, not the yew logs the
        # fresh /bank read had placed there -- the same early-position trap CLAUDE.md records
        # for the basket 0.12s after a bank open. The slot is trusted only once two fresh
        # reads a beat apart agree on where it is.
        _await_bank_slot(ctx.state.config["log"])
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
SITE_ACTION_RETRIES = 1   # a failed action is tried once more before the visit ends
STALL_STOP_TICKS = 40
LEG_MAX_ATTEMPTS = 3      # a transient un-projectable hop point is not a broken route

# Action name -> the module-level function that performs it. Resolved through globals() at
# CALL time rather than bound into a dict at import time, so that monkeypatching
# routines.vale_totems.build (which every test of this loop does) actually takes effect. A
# dict built at import captures the original function objects and silently ignores the patch.
_ACTIONS = ("claim", "build", "carve", "decorate")


def work_site(ctx, site_number: int, during_decorate=None) -> bool:
    """Run the ladder at one site until it reports done, an action fails, or the ceiling.

    The ceiling is load-bearing: every action here confirms against the reader, so an action
    that reports success without advancing the totem would otherwise loop forever. A failed
    action ends the visit rather than retrying -- the caller walks on and the next lap sees the
    site again, which is cheaper than spinning in front of it.
    """
    # AT THE BANK SITE WITH A TRIP DUE, THE CAMERA TURNS TOWARD THE BOOTH WHILE THE
    # DECORATION RUNS, so the booth is on screen for one direct click afterwards. USER
    # 2026-09-10: "while decorating we can adjust camera if needed and click the banker
    # directly after the decoration is done. that way we save a bit of time not doing hop ->
    # click bank".
    if during_decorate is None:
        during_decorate = _decorate_camera_hook(ctx, site_number)
    done_actions = []
    failed = {}
    for _ in range(SITE_MAX_ACTIONS):
        site = _site_now(site_number)
        if site is None:
            return False
        logs = count_item(ctx.state.config["log"])
        bows = bow_count(ctx, site.wood if site.base_value else None)
        action, argument = vt.next_action(site, logs=logs, bows=bows)
        if action == "done":
            _log_site_outcome(ctx, site, done_actions, logs, bows)
            return True
        handler = globals().get(action) if action in _ACTIONS else None
        if action == "decorate" and during_decorate is not None:
            handler = lambda c, st: decorate(c, st, after_click=during_decorate)
        if handler is None or not handler(ctx, site):
            # ONE RETRY, re-read first. MEASURED live 2026-09-10 (vt_markers4, site 4): a
            # single build click that did not land ended the visit and the totem was skipped
            # for the whole lap (USER: "skipped a totem"). A second try costs a click; the
            # skip costs a lap.
            retried = failed.get(action, 0) + 1
            failed[action] = retried
            debug_log.log(LOG, "site %d: %s did not land%s"
                          % (site_number, action, " -- retrying" if retried <= SITE_ACTION_RETRIES
                             else ""))
            if retried > SITE_ACTION_RETRIES:
                return False
            continue
        done_actions.append(action)
    debug_log.log(LOG, "site %d: hit the action ceiling" % site_number)
    return False


def _decorate_camera_hook(ctx, site_number: int):
    """What the camera does while the decorations run: face the booth when this is the bank
    site with a trip due, else face the NEXT site so the leg's first click needs no drag.

    MEASURED run vt_markers9 (2026-09-10): the decorate wait is ~5s of idle hands at every
    site, and the leg that follows opened with two middle-mouse drags (~1.5s) at four of the
    eight sites before its first click could be aimed -- the same drags _far_click_target
    makes when the stand tile is off screen. Made here instead, they overlap the decorations
    and _far_click_target finds the tile already rendered. A camera drag never interrupts an
    action (the human's recording turns the camera mid-fletch, mid-walk, at will)."""
    if site_number in BANK_SITES:
        state = read_state()
        if state is not None and _bank_trip_due(ctx, state):
            return lambda: _face_booth(ctx)
    nxt = route.next_in_ring(site_number)
    return lambda: _face_tile(ctx, *route.SITES[nxt]["stand"])


def _face_tile(ctx, tx: int, ty: int) -> None:
    """Turn the camera toward (tx, ty) if it is not already rendered; sends nothing when it
    is -- the same rule _far_click_target applies before a far click."""
    if not controller.get_ground_walk_point(tx, ty, route.PLANE).get("onScreen"):
        controller.face_toward(ctx.ser, tx, ty)


def _log_site_outcome(ctx, site, done_actions, logs: int, bows: int) -> None:
    """ONE line per site visit saying what was done and what the totem looks like on leaving.
    USER 2026-09-10: "Add a log for finishing totems too since its not built one in 3 hops"
    -- the site work was invisible in the log except as clicks, so three sites skipped for
    want of logs looked like three sites finished."""
    log_name = ctx.state.config["log"]
    if not site.base_value:
        state = "unbuilt" + ("" if logs else " (no %s)" % log_name)
    elif not site.base_carved:
        state = "%s totem carved %d/3" % (site.wood.title(), sum(1 for v in site.segment_values if v))
    else:
        state = "%s totem, %d/4 decorations" % (site.wood.title(), site.decorations)             + ("" if site.decorations >= 4 or bows else " (no bows)")
    debug_log.log(LOG, "site %d: %s -> %s; %d %s, %d bows in hand"
                  % (site.number, ", ".join(done_actions) or "nothing to do", state,
                     logs, log_name, bows))


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
