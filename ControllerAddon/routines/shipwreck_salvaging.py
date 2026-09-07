# routines/shipwreck_salvaging.py
#
# Shipwreck Salvaging routine: deploy the boat's own salvaging hook near a matching shipwreck
# and keep it running, sailing to a new wreck when nothing is left in range. Unlike every other
# routine here, the wreck itself is never clicked -- it only has an Inspect option. The real
# interaction is the salvaging hook facility built on the player's own boat -- see
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md for the full research behind
# this and the list of assumptions still needing in-game verification.
#
# Run with:
#     python controller.py routines/shipwreck_salvaging.py
#
# ASSUMPTIONS TO VERIFY IN-GAME (see the spec's "Still unverified" section for the full list):
#   - CONFIRMED live 2026-08-02 (#3): re-clicking an already-deployed hook IS safe -- it does not
#     retrieve/stop it. tick() below re-clicks whenever idle, self-healing from any stop reason
#     (the wreck sank, a stray misclick retrieved it, ...) instead of needing to track why it
#     stopped. UNLIKE mining.py's click-the-rock-whenever-idle loop, the isIdle check only gates
#     the reclick itself, not the whole near-wreck branch -- salvaging turned out to be a
#     continuous multi-tick channel (isIdle can stay False for a wreck's entire lifespan), so
#     gating the branch on it meant the routine never noticed a sunk wreck and never looked
#     farther out, confirmed live 2026-08-02.
#   - CONFIRMED live 2026-08-01 (#4): the hook does NOT keep working on its own once its wreck
#     sinks, even if a different active wreck of the same name is still in range -- salvaging
#     visibly stopped. No longer tracked explicitly (see #3 above) -- the idle-triggered re-click
#     covers this the same as every other stop reason.

import math
import time

import controller
import debug_log
import sailing
import cargo_hold

DEFAULT_CONFIG = {
    "enabled": True,
    "wreckNames": ["Small shipwreck", "Fisherman's shipwreck", "Large shipwreck"],
    "hookName": "salvaging hook",  # tier-agnostic on purpose: deploy_hook() matches this as a
                                    # substring against the boat's actual hook name, and the boat's
                                    # hook tier (Bronze/Iron/.../Dragon, see
                                    # data/decompiled/sailing-plugin's SalvagingHookTier) changes
                                    # independently of this routine's config -- pinning to one tier
                                    # name broke the moment the boat's hook got upgraded to Steel.
    "maxSailDistance": 60,   # a farther wreck worth repositioning the boat for
    "dropAtFreeSlots": 1,    # drop salvage once this many inventory slots are left (0 = only when
                             # completely full, the pre-2026-09-03 behaviour -- see tick())

    # --- mass-world salvaging + on-boat sorting (2026-09-07) ---------------------------------
    # See docs/superpowers/specs/2026-09-07-shipwreck-salvaging-mass-world-and-sorting-design.md
    "massWorld": True,        # never sail: an area's wrecks share a spawn pool and one rises as
                              # another sinks (wiki, Shipwreck salvaging rev 15290733), so on a
                              # busy world waiting beats sailing. Disables the far-wreck branch
                              # AND the won't-engage reposition.
    "stowSalvage": True,      # deposit salvage into the boat's cargo hold instead of dropping
                              # it on the deck. False restores the pre-2026-09-07 behaviour.
    "sortWhenIdle": True,     # spend a respawn gap sorting rather than idling
    "holdFullSlots": 4,       # treat the hold as full this many slots short of its capacity
    "keepItems": [            # case-insensitive SUBSTRING keep-list applied to SORTED output.
                              # Trap inherited from banker.py's same convention: "ring" would
                              # match Gold/Sapphire/Ruby ring alike, and "rune" would match every
                              # runite item -- hence the spelled-out entries below.
        "amulet (inert)",     # Sailors' amulet (inert), cache obj 32398. Keyed WITHOUT the
                              # apostrophe on purpose: that character is the one most likely to
                              # differ between the cache dump and the runtime item name, and a
                              # near-miss here fails silently IN THE DROP DIRECTION.
        "facility bottle",    # ~34.6k each, the most valuable single drop
        "mahogany repair kit",
        "diamond",            # Diamond ring + Diamond bracelet, ~2.1-2.3k each
        "ruby",               # Ruby bracelet ~1.4k
        "dragon cannonball",
        "rune scimitar",
        "rune cannonball",
    ],

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
        "stopOnInventoryFull": False,   # routine drops salvage itself, see drop_salvage
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # This routine doesn't need anything from the Controller Settings panel.
    return DEFAULT_CONFIG


WAITING_MESSAGE_INTERVAL_S = 30.0
HOOK_CLICK_COOLDOWN_S = 3.0  # don't reclick the hook again within this long of the last click --
                              # CONFIRMED live 2026-08-02: isIdle can read True for a tick or two
                              # right after clicking, before the salvaging animation actually
                              # starts, causing back-to-back redundant clicks without this. Still
                              # short enough to redeploy quickly for a genuine stop (wreck sank,
                              # misclick retrieved it, ...), just not on literally every tick.
HOOK_ENGAGE_TIMEOUT_S = 12.0  # Kept as a backstop even after switching to the geometric
                               # WRECK_SQUARE_SIZE check (CONFIRMED live 2026-08-04). History: this
                               # was originally added because a flat maxDistance guess (15, then 8)
                               # didn't reliably predict real engagement -- observed live standing
                               # at a wreck that still failed to engage despite passing the
                               # distance check. If the real salvage channel (SALVAGE_CHANNEL_
                               # ANIMATIONS) never starts for this long despite repeated reclicks
                               # even though _wreck_in_hook_range said yes, reposition closer instead
                               # of reclicking forever -- covers any edge case the geometric check
                               # still misses (e.g. scene/tile lag).

HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS = 3  # CONFIRMED live 2026-08-05 (user report): with no cap,
                                           # a wreck where only a sliver of the boat's hull overlaps
                                           # WRECK_SQUARE_SIZE could pass _wreck_in_hook_range's
                                           # overlap check without the hook ever actually engaging --
                                           # each reposition attempt landing at another similarly
                                           # marginal spot turned this branch into an unbounded loop
                                           # (toggle sails off, try the hook, toggle sails back on to
                                           # reposition, repeat forever). Same bounded-retry
                                           # philosophy as sailing.MAX_ROUTE_ATTEMPTS -- give up on
                                           # this wreck instead of retrying indefinitely.
HOOK_ENGAGE_GIVEUP_COOLDOWN_S = 30.0  # how long to leave a given-up-on wreck alone (see above)
                                        # before it's eligible to be retried -- long enough that a
                                        # repeated find_wreck_in_hook_range call doesn't just walk
                                        # straight back into the same loop next tick, short enough
                                        # that a wreck worth salvaging isn't abandoned for good over
                                        # one bad approach.

# The player's own salvaging-channel animations, ONE FAMILY PER HOOK SIZE -- pinned against
# runelite-api 1.12.38's AnimationID (javap over the Gradle-cache jar, 2026-09-03) and matched
# against what the Plugin Hub's salvaging-afk-timer / distraction-reducer plugins treat as
# "salvaging". Used instead of trusting /combat's isIdle for "is the hook actually working" --
# CONFIRMED live 2026-08-05 (user report, "seems he's stuck on the helm after clicking the hook
# once"): isIdle's own formula (!moving && animation == -1 && target == null) makes ANY non-(-1)
# animation read as "not idle", channeling or not.
#
# BUG FIXED 2026-09-03: the set used to hold only the 2X5 family (13576-13578, the three ids
# CONFIRMED live on the boat of the day). The hook facility's animation family follows the hull
# the hook is built on (cache: salvaging_hook_bronze -> ..._2x5_idle01, salvaging_hook_large_*_b
# -> ..._3x8_idle01), so a bigger boat would have read as "never channeling" -- every recovery
# below (reclick, reposition, give-up) would then fire against a hook that was working fine.
_SALVAGE_CHANNEL_BY_HULL = {
    "1x3": (13569, 13570, 13571),   # SAILING_HUMAN_SALVAGE_HOOK_KANDARIN_1X3_{DROP01,IDLE01,RESET01}
    "2x5": (13576, 13577, 13578),   # ..._2X5_{DROP01,IDLE01,RESET01} -- CONFIRMED live 2026-08-05
    "3x8": (13583, 13584, 13585),   # ..._3X8_{DROP01,IDLE01,RESET01}
    "large": (13591, 13592, 13594),  # HUMAN_SAILING_SALVAGE01_LARGE01_{DROP01,IDLE01,RESET01}
}
SALVAGE_CHANNEL_ANIMATIONS = frozenset(
    anim for anims in _SALVAGE_CHANNEL_BY_HULL.values() for anim in anims
) | frozenset({
    13596,  # SAILING_HUMAN_SALVAGE_HOOK_KANDARIN_1X3_SALVAGING01
    13598,  # SAILING_HUMAN_SALVAGE_HOOK_KANDARIN_1X3_PULL01
})
# Deliberately NOT in the set: the Sort-salvage poses (13599 1X3_INTERACT01, 13593
# LARGE01_INTERACT01 -- RuneLite's own IdleNotifier labels them "Sort-salvage") and 13597
# 1X3_INACTIVE01, whose name suggests a deployed hook with nothing left to pull -- exactly the
# state the idle-triggered reclick exists to recover from.

# Standing at the helm. 13352 is HUMAN_SAILING_ALPHA_HELM_SMALL01_ACTIVE01_LOOP (runelite-api
# 1.12.38) -- the "animation stuck at 13352 for 100+ seconds after a single hook click" seen live
# 2026-08-05 was the player still steering, i.e. the hook click never took effect, not some
# leftover UI pose. Listed so a log line can say so; the reclick path already covers it.
HELM_ANIMATIONS = frozenset({
    13351, 13352, 13353, 13354, 13355,   # HUMAN_SAILING_ALPHA_HELM_SMALL01_*
    13362, 13363, 13364, 13365, 13366,   # HUMAN_SAILING_HELM_3X8_*
})

# The on-boat salvaging station (cache locs; port stations are 60462/60463 and behave the same).
SALVAGING_STATION_IDS = frozenset({59699, 59700, 59701})
SALVAGING_STATION_NAME = "salvaging station"
SORT_ACTION = "Sort-salvage"

# The Sort-salvage channel's own animations, DELIBERATELY SEPARATE from
# SALVAGE_CHANNEL_ANIMATIONS -- merging them would make the hook's idle-reclick logic read
# sorting as hooking and never redeploy. 13599 CONFIRMED live 2026-09-07 on a 3x8 boat, despite
# runelite-api naming it ..._KANDARIN_1X3_INTERACT01; 13593 is LARGE01_INTERACT01. RuneLite's
# own IdleNotifier labels both "Sort-salvage".
SORT_ANIMATIONS = frozenset({13599, 13593})

# MEASURED live 2026-09-07: 12 salvage sorted in 19.7s, a steady 3 game ticks each. Matches the
# wiki's "close to 1800 salvages per hour" for the port station. The channel ENDS ITSELF when
# the inventory runs out, so this is a budget, not a stop signal.
SORT_SECONDS_PER_SALVAGE = 1.79
SORT_BUDGET_SLACK_S = 6.0
SORT_POLL_S = 0.4

FAR_WRECK_GIVEUP_COOLDOWN_S = 60.0  # CONFIRMED live 2026-08-05 (user report): "we just sail around
                                      # in circles even though we have multiple wreck pairs around
                                      # us" -- sail_to_pair failing already means sail_to exhausted
                                      # MAX_ROUTE_ATTEMPTS worth of re-planning (multiple minutes of
                                      # real time, observed live as one long stretch of ~10s hop
                                      # timeouts), so find_nearby_wreck just re-picking the exact same
                                      # nearest-but-unreachable wreck next tick repeated that entire
                                      # multi-minute journey forever with no way to try a different,
                                      # reachable pair. Longer than HOOK_ENGAGE_GIVEUP_COOLDOWN_S
                                      # since a failed full sail attempt is far more expensive than a
                                      # failed reposition -- not worth retrying as quickly.


# Chat signals (wiki "Shipwreck salvaging", rev 15290733, + Transcript:Jobless Jim). Matched as
# lowercase substrings so a punctuation/wording tweak does not silently disarm them.
SUNK_CHAT_FRAGMENT = "reclaimed by the sea"        # "You salvage all you can from the shipwreck
                                                   #  before it is reclaimed by the sea."
HAZARD_CHAT_FRAGMENT = "hazardous waters"          # "...not safe to salvage ... hazardous waters."
CHANNEL_XP_STALL_S = 120.0  # channel animation playing but NO Sailing XP for this long => the
                             # hook is not really working; reclick. Players roll for salvage every
                             # 4 ticks (wiki, 5 Dec 2025 hotfix) and the lowest documented success
                             # rate leaves p(no XP in 120s) below 1e-3, so a real channel cannot
                             # trip this. A stale animation read or a channel the game has quietly
                             # ended (the reasons the animation-only gate is not enough) can.
HAZARD_GIVEUP_S = 30 * 60  # a hazardous-waters refusal is about the BOAT's fittings (mast/hull),
                            # not the approach -- no reposition fixes it, so the wreck is
                            # blacklisted for far longer than an ordinary engage failure.


def _wreck_key(wreck: dict):
    return (wreck.get("x"), wreck.get("y"), wreck.get("plane"))


def _active_giveup(data: dict, key_name: str, until_name: str, now: float) -> frozenset:
    key = data.get(key_name)
    if key and now < data.get(until_name, 0):
        return frozenset({key})
    return frozenset()


# Items the keep-list can NEVER drop, whatever the config says. keepItems is a list a human
# edits between sessions, and a typo there must not be able to throw away the chisel or the rune
# pouch -- same non-negotiable-floor reasoning banker.py applies to bank UI buttons and
# sailing._FIXTURE_CLICK_KWARGS applies to deck clicks. Lowercase substrings.
HARD_KEEP = frozenset({
    "chisel", "rune pouch", "coins", "gem bag", "plank sack", "log basket",
    "salvage",   # unsorted salvage is the WORK -- it gets stowed or sorted, never dropped here
})


def keep_item(name: str, keep_items) -> bool:
    """Whether an inventory item survives post-sort disposal.

    Case-insensitive substring match against HARD_KEEP first (non-negotiable), then the
    configured keep-list. Everything else is dropped."""
    lowered = (name or "").lower()
    if any(needle in lowered for needle in HARD_KEEP):
        return True
    return any((needle or "").lower() in lowered for needle in (keep_items or []))


def _holds_salvage(inventory) -> bool:
    return any((item.get("name") or "").lower().endswith("salvage") for item in inventory)


def derive_phase(config, *, wreck_in_range, hold_full, inventory_salvage, free_slots,
                 hold_has_salvage):
    """Which phase this tick should run, derived ENTIRELY from live state.

    Never from a stored phase: ctx.state.data is wiped on restart while the boat's real state
    is not, so a remembered phase and the world desync exactly when it matters. Same rule
    karambwan_fishing.derive_phase and corrupted_gauntlet.derive_phase follow.

    hold_has_salvage is True / False / None, where None means "the hold has never been read
    this session". Unknown is treated as "try once": assuming no would mean a restart during a
    respawn gap never sorts until the next stow, and assuming yes forever would spin. One read
    settles it and the answer is recorded.

    Order matters:
      1. A FULL HOLD outranks everything -- there is nowhere for new salvage to go, so hooking
         would only fill the inventory and stall. This is the user's stated exception to the
         interrupt rule, and it is deliberately NOT gated on sortWhenIdle: a full hold is a
         blockage, not an optimisation.
      2. An idle gap with something to sort, when sortWhenIdle is on.
      3. Stowing a salvage-laden inventory.
      4. Hooking.
    Because 4 is below 2, a wreck rising mid-sort returns us to the hook -- hooking out-earns
    sorting (the user's interrupt rule)."""
    something_to_sort = bool(inventory_salvage) or hold_has_salvage is not False

    if hold_full and something_to_sort:
        return "sort"
    if config.get("sortWhenIdle", True) and not wreck_in_range and something_to_sort:
        return "sort"
    if (config.get("stowSalvage", True) and inventory_salvage
            and free_slots <= config.get("dropAtFreeSlots", 1)):
        return "stow"
    if wreck_in_range:
        return "hook"
    return "wait"


def _record_hold(data, used, capacity, has_salvage, config, forced_full=False):
    """Record what the open hold reported. Unreadable numbers mean "cannot tell" -- they must
    never make the hold LOOK full or LOOK empty, so hold_full only goes True on a real reading
    or on the empirical forced_full signal."""
    data["hold_used"] = used
    data["hold_capacity"] = capacity
    data["hold_has_salvage"] = has_salvage
    if forced_full:
        data["hold_full"] = True
        return
    if used is None or capacity is None:
        data["hold_full"] = False
        return
    data["hold_full"] = used >= capacity - config.get("holdFullSlots", 4)


def stow_phase(ctx) -> bool:
    """Deposit the inventory's salvage into our own boat's cargo hold, recording its fullness
    while the interface is open (the only place it is readable -- see cargo_hold's docstring).

    Falls back to dropping on the deck whenever the hold cannot be opened, so a broken or
    missing hold degrades to the pre-2026-09-07 behaviour instead of stalling the run."""
    config = ctx.state.config
    data = ctx.state.data

    if not config.get("stowSalvage", True):
        return bool(drop_salvage(ctx))

    if not cargo_hold.open_hold(ctx):
        debug_log.log("salvage", "could not open the cargo hold -- dropping salvage instead")
        return bool(drop_salvage(ctx))

    deposited = cargo_hold.deposit_salvage(ctx)
    used, capacity = cargo_hold.read_usage()
    has_salvage = bool(cargo_hold.salvage_rows())
    _record_hold(data, used, capacity, has_salvage, config, forced_full=not deposited)

    if not deposited:
        debug_log.log("salvage", f"salvage left in the inventory after Deposit Salvage "
                                  f"(hold {used}/{capacity}) -- treating the hold as full")
    cargo_hold.close_hold(ctx)
    return deposited


def _inventory_salvage_count():
    return sum(1 for i in controller.get_inventory()
               if (i.get("name") or "").lower().endswith("salvage"))


def _await_sort_done(ctx, count) -> bool:
    """Poll until no salvage is left in the inventory. The channel ends itself, so the budget
    only bounds a channel that never started (a hijacked click, a station out of reach)."""
    deadline = time.time() + count * SORT_SECONDS_PER_SALVAGE + SORT_BUDGET_SLACK_S
    while time.time() < deadline:
        controller.human_delay(SORT_POLL_S, SORT_POLL_S + 0.2)
        controller.dismiss_continue_box(ctx.ser)
        if _inventory_salvage_count() == 0:
            return True
    debug_log.log("salvage", f"sorting {count} salvage did not finish inside its budget")
    return False


def sort_phase(ctx) -> bool:
    """Sort the hold's salvage at our own boat's salvaging station, keeping only what the
    keep-list selects.

    Step 0 -- disposal -- runs FIRST and unconditionally. Without it the loop deadlocks in a
    real, reachable state: enter sort holding an inventory full of the PREVIOUS batch's sorted
    output, find no salvage to sort, and try to withdraw into an inventory with no free slots.
    Disposal is idempotent, so running it every pass costs nothing when there is nothing to
    drop."""
    config = ctx.state.config
    data = ctx.state.data
    keep = config.get("keepItems", [])

    drop_items(ctx, lambda name: not keep_item(name, keep))

    if _inventory_salvage_count() == 0:
        if not cargo_hold.open_hold(ctx):
            return False
        withdrew = cargo_hold.withdraw_salvage(ctx)
        used, capacity = cargo_hold.read_usage()
        _record_hold(data, used, capacity, bool(cargo_hold.salvage_rows()), config)
        cargo_hold.close_hold(ctx)
        if not withdrew:
            debug_log.log("salvage", "no salvage left in the hold -- nothing to sort")
            data["hold_has_salvage"] = False
            return False

    count = _inventory_salvage_count()
    if count == 0:
        return False

    station = sailing.find_facility(SALVAGING_STATION_NAME, action=SORT_ACTION)
    if station is None:
        debug_log.log("salvage", "no salvaging station on our own boat")
        return False

    debug_log.log("salvage", f"sorting {count} salvage (~{count * SORT_SECONDS_PER_SALVAGE:.0f}s)")
    controller.click_entity(ctx.ser, station, offset=(0, 0), simulate_mistakes=False)
    finished = _await_sort_done(ctx, count)
    drop_items(ctx, lambda name: not keep_item(name, keep))
    return finished


def tick(ctx):
    config = ctx.state.config
    data = ctx.state.data
    now = time.time()

    inventory = controller.get_inventory()
    free_slots = controller.INVENTORY_SIZE - len(inventory)
    inventory_salvage = sum(1 for i in inventory
                            if (i.get("name") or "").lower().endswith("salvage"))

    # A wreck this routine already gave up repositioning for (see HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS
    # below) is skipped for HOOK_ENGAGE_GIVEUP_COOLDOWN_S -- keyed by its own tile position, NOT
    # its object id (SALVAGE_LEVEL_REQ/SUNK_SHIPWRECK_IDS confirm ids are shared per TIER, e.g.
    # every "Small shipwreck" instance uses the same id -- keying by id would blacklist every wreck
    # of that tier, not just this one stuck instance). Passed INTO the search (2026-09-03) rather
    # than nulling its result afterwards, so a second in-range wreck is still found.
    near_exclude = _active_giveup(data, "hook_engage_giveup_key", "hook_engage_giveup_until", now)
    near_wreck = find_wreck_in_hook_range(config, exclude=near_exclude)

    phase = derive_phase(
        config,
        wreck_in_range=near_wreck is not None,
        hold_full=bool(data.get("hold_full")),
        inventory_salvage=inventory_salvage,
        free_slots=free_slots,
        hold_has_salvage=data.get("hold_has_salvage"),
    )
    if phase != data.get("salvage_phase"):
        debug_log.log("salvage", f"phase -> {phase}")
        data["salvage_phase"] = phase

    if phase == "sort":
        sort_phase(ctx)
        return
    if phase == "stow":
        if inventory_salvage:
            stow_phase(ctx)
        elif now - data.get("last_no_salvage_message_at", 0) > WAITING_MESSAGE_INTERVAL_S:
            debug_log.log("salvage", "inventory is full of things that are not salvage")
            data["last_no_salvage_message_at"] = now
        return

    if near_wreck is not None:
        data["salvage_current_wreck_key"] = _wreck_key(near_wreck)
        data.pop("far_wreck_miss_count", None)
        # channeling (SALVAGE_CHANNEL_ANIMATIONS) only gates the reclick itself (avoid
        # spam-clicking mid-channel) -- it must NOT gate this whole branch, or the far-wreck check
        # below never runs. CONFIRMED live 2026-08-02: salvaging is a continuous multi-tick
        # channel, not brief per-swing bursts like mining.py -- the channel can run for a wreck's
        # entire lifespan, so gating the branch on it meant the routine never noticed the near
        # wreck was gone and never looked for a farther one to sail to.
        # No "not navigating" gate here (there used to be one) -- CONFIRMED live 2026-08-02:
        # get_sailing()["navigating"] reflects whether the helm/steering interface is still
        # engaged, not whether the boat is physically moving, and it can stay True even after the
        # boat has fully stopped (sails down) until the helm is explicitly left somehow. Gating on
        # it here got the routine permanently stuck ("stuck at the helm... not clicking the
        # hook") once navigating got stuck True. stop_boat() already waits for the boat's actual
        # *position* to stabilize before returning, which is the real "safe to deploy" signal.
        animation = controller.get_combat().get("animation")
        channeling = animation in SALVAGE_CHANNEL_ANIMATIONS
        if channeling:
            # Channel confirmed actually running -- clear the stuck trackers below.
            data.pop("hook_engage_attempt_at", None)
            data.pop("hook_engage_reposition_count", None)
        # XP watchdog (2026-09-04): the animation says "channeling", the XP says whether salvage
        # is actually being rolled. The stall clock only runs while the channel animation plays.
        sailing_xp = _sailing_xp()
        if sailing_xp is not None and sailing_xp != data.get("sailing_xp"):
            data["sailing_xp"] = sailing_xp
            data["sailing_xp_changed_at"] = now
        elif not channeling or "sailing_xp_changed_at" not in data:
            data["sailing_xp_changed_at"] = now
        stalled = channeling and now - data["sailing_xp_changed_at"] > CHANNEL_XP_STALL_S
        if stalled:
            debug_log.log("salvage", f"channel animation {animation} playing but no Sailing XP for "
                                      f"{CHANNEL_XP_STALL_S:.0f}s -- reclicking the hook")
            data["sailing_xp_changed_at"] = now
        cooldown_elapsed = now - data.get("last_deploy_click_at", 0) > HOOK_CLICK_COOLDOWN_S
        if (not channeling or stalled) and cooldown_elapsed:
            if animation in HELM_ANIMATIONS:
                debug_log.log("salvage", "still standing at the helm -- the last hook click did not take, reclicking")
            attempt_start = data.setdefault("hook_engage_attempt_at", now)
            if now - attempt_start > HOOK_ENGAGE_TIMEOUT_S:
                reposition_count = data.get("hook_engage_reposition_count", 0) + 1
                data["hook_engage_reposition_count"] = reposition_count
                if reposition_count > HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS:
                    debug_log.log("salvage", f"giving up on this wreck after {HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS} "
                                              f"reposition attempts -- letting it cool down and looking elsewhere")
                    data["hook_engage_giveup_key"] = _wreck_key(near_wreck)
                    data["hook_engage_giveup_until"] = now + HOOK_ENGAGE_GIVEUP_COOLDOWN_S
                    data.pop("hook_engage_attempt_at", None)
                    data.pop("hook_engage_reposition_count", None)
                    return
                # Sail at a point clear of near_wreck's own collision footprint -- NOT sail_to_pair,
                # and NOT near_wreck's own (x, y) tile either (that tile IS the solid footprint, see
                # WRECK_COLLISION_SIZE -- aiming straight at it is how the boat kept bonking the
                # wreck). find_wreck_in_hook_range already confirmed the boat's hull overlaps THIS
                # wreck's square, so what's needed is fine positioning on it, not sail_to_pair's
                # pair-midpoint logic (meant for the initial far-away approach): that can retarget
                # toward a different, farther partner wreck up to PAIR_MAX_TILES away and send the
                # boat somewhere it was never stuck getting to before -- observed live 2026-08-04
                # running the boat aground trying to reach that new point right after sitting fine
                # at near_wreck.
                approach_point = (None if config.get("massWorld", True)
                                  else _approach_point_from_here(near_wreck))
                if config.get("massWorld", True):
                    # massWorld: never move. The reposition BUDGET still counts, so a wreck
                    # whose hook genuinely never engages is still given up for the usual
                    # cooldown -- it just is not approached first.
                    debug_log.log("salvage", "hook isn't engaging -- waiting rather than "
                                              f"repositioning (attempt {reposition_count}/"
                                              f"{HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS})")
                elif approach_point is not None:
                    debug_log.log("salvage", "hook isn't engaging after repeated clicks -- repositioning closer "
                                              f"(attempt {reposition_count}/{HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS})")
                    sailing.sail_to(ctx, approach_point)
                else:
                    # Already within safe clearance of the wreck -- see _approach_point_clear_of_wreck's
                    # own docstring for why calling sail_to anyway (engaging the helm with nowhere new
                    # to steer to) is actively harmful, not just a no-op. Nothing left to try but
                    # reclicking the hook again; the reposition_count above still counts this attempt,
                    # so a wreck that genuinely never engages still gets given up on eventually.
                    debug_log.log("salvage", "hook isn't engaging after repeated clicks, but already as close as "
                                              "it's safe to get -- reclicking instead of repositioning "
                                              f"(attempt {reposition_count}/{HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS})")
                data.pop("hook_engage_attempt_at", None)
            sailing.deploy_hook(ctx, config.get("hookName", ""))
            data["last_deploy_click_at"] = time.time()
        return
    data.pop("salvage_current_wreck_key", None)
    data.pop("hook_engage_attempt_at", None)
    data.pop("hook_engage_reposition_count", None)

    if config.get("massWorld", True):
        # An area's wrecks share a spawn pool and one rises as another sinks (wiki, Shipwreck
        # salvaging rev 15290733), so on a busy world waiting beats sailing. derive_phase has
        # already decided this tick is "wait" -- there is nothing else to do.
        if now - data.get("last_waiting_message_at", 0) > WAITING_MESSAGE_INTERVAL_S:
            debug_log.log("salvage", "no shipwreck in range -- waiting for a respawn")
            data["last_waiting_message_at"] = now
        return

    far_exclude = _active_giveup(data, "far_wreck_giveup_key", "far_wreck_giveup_until", now)

    far_wreck = find_nearby_wreck(config, config.get("maxSailDistance", 60), exclude=far_exclude)
    if far_wreck is not None:
        # No "not navigating" gate here either -- see the near-wreck branch's comment above.
        if sail_to_pair(ctx, config, far_wreck):
            data.pop("far_wreck_giveup_key", None)
            # VERIFY the landing before deploying (added 2026-09-03). sail_to has an arrival
            # pre-check that returns True as soon as the boat is within braking distance of the
            # TARGET, and a lone wreck's target is an approach point beside it -- so a landing a
            # few tiles short can leave the hull outside the wreck's WRECK_SQUARE_SIZE square while
            # every later sail_to call returns instantly. Without this check that was a deploy
            # click every HOOK_CLICK_COOLDOWN_S forever, never once repositioning. Now: sail to
            # the approach point instead, and give the wreck up after the same bounded number of
            # attempts the near branch uses.
            player = controller.get_player()
            if not _wreck_in_hook_range(player, far_wreck):
                misses = data.get("far_wreck_miss_count", 0) + 1
                data["far_wreck_miss_count"] = misses
                if misses > HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS:
                    debug_log.log("salvage", f"still outside hook range of ({far_wreck.get('x')}, {far_wreck.get('y')}) "
                                              f"after {HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS} approaches -- giving up on it "
                                              f"for {FAR_WRECK_GIVEUP_COOLDOWN_S:.0f}s")
                    data["far_wreck_giveup_key"] = _wreck_key(far_wreck)
                    data["far_wreck_giveup_until"] = time.time() + FAR_WRECK_GIVEUP_COOLDOWN_S
                    data.pop("far_wreck_miss_count", None)
                    return
                approach_point = _approach_point_from_here(far_wreck, player)
                if approach_point is not None:
                    debug_log.log("salvage", "landed outside the wreck's hook range -- closing in "
                                              f"(attempt {misses}/{HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS})")
                    sailing.sail_to(ctx, approach_point)
                else:
                    debug_log.log("salvage", "within clearance of the wreck but the hull does not overlap its "
                                              "square -- waiting for the next tick's range read")
                return
            data.pop("far_wreck_miss_count", None)
            # CONFIRMED live 2026-08-05: sail_to's own arrival pre-check (see sailing.py) means a
            # far_wreck that's already in range returns here almost instantly every tick instead of
            # taking the several seconds a real helm-engage-and-steer cycle used to -- without the
            # same HOOK_CLICK_COOLDOWN_S courtesy the near-wreck branch already has, this spammed a
            # fresh deploy_hook click on literally every tick (~1s) instead of respecting the cooldown.
            if time.time() - data.get("last_deploy_click_at", 0) > HOOK_CLICK_COOLDOWN_S:
                sailing.deploy_hook(ctx, config.get("hookName", ""))
                data["last_deploy_click_at"] = time.time()
        else:
            debug_log.log("salvage", f"couldn't reach the wreck pair near ({far_wreck.get('x')}, {far_wreck.get('y')}) "
                                      f"-- giving up on it for {FAR_WRECK_GIVEUP_COOLDOWN_S:.0f}s and trying elsewhere")
            data["far_wreck_giveup_key"] = _wreck_key(far_wreck)
            data["far_wreck_giveup_until"] = time.time() + FAR_WRECK_GIVEUP_COOLDOWN_S
        return

    if now - data.get("last_waiting_message_at", 0) > WAITING_MESSAGE_INTERVAL_S:
        debug_log.log("salvage", "no shipwreck in range -- waiting")
        data["last_waiting_message_at"] = now


def on_event(ctx, event_type, payload):
    """Two game messages the polling loop cannot see fast enough (added 2026-09-03; the engine
    drains UDP events and calls this before every tick, see controller.py's main()):

    - The wreck sinking ("...reclaimed by the sea."): the hook does NOT carry on to another wreck
      (CONFIRMED live 2026-08-01), and the reclick otherwise waits out HOOK_CLICK_COOLDOWN_S plus
      the engage timer -- clearing both lets the very next tick redeploy.
    - A hazardous-waters refusal: no amount of repositioning helps (it is the boat's own fittings
      that are lacking), so the wreck under the hook is blacklisted for HAZARD_GIVEUP_S on BOTH
      give-up keys instead of burning the reposition budget and retrying every minute.

    Player chat types (PUBLICCHAT, PRIVATECHAT, FRIENDSCHAT, CLAN_*) are ignored so another player
    typing the words cannot trigger either."""
    if event_type != "CHAT_MESSAGE" or not payload:
        return
    if "CHAT" in (payload.get("type") or "").upper():
        return
    message = (payload.get("message") or "").lower()
    data = ctx.state.data
    if SUNK_CHAT_FRAGMENT in message:
        debug_log.log("salvage", "the wreck has sunk -- redeploying on the next tick")
        data.pop("last_deploy_click_at", None)
        data.pop("hook_engage_attempt_at", None)
        return
    if HAZARD_CHAT_FRAGMENT in message:
        key = data.get("salvage_current_wreck_key")
        if key is None:
            debug_log.log("salvage", "hazardous-waters refusal with no wreck under the hook -- ignoring")
            return
        until = time.time() + HAZARD_GIVEUP_S
        debug_log.log("salvage", f"not safe to salvage here (hazardous waters) -- leaving wreck {key} alone "
                                  f"for {HAZARD_GIVEUP_S // 60} minutes; the boat needs better fittings for it")
        data["hook_engage_giveup_key"] = key
        data["hook_engage_giveup_until"] = until
        data["far_wreck_giveup_key"] = key
        data["far_wreck_giveup_until"] = until


def _approach_point_from_here(wreck: dict, player: dict = None):
    """_approach_point_clear_of_wreck measured from the boat's current reference position, or
    None when that position is unreadable (or already within clearance -- see the helper)."""
    if player is None:
        player = controller.get_player()
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))
    if origin_x is None or origin_y is None:
        return None
    return _approach_point_clear_of_wreck(origin_x, origin_y, wreck, player)


def drop_items(ctx, predicate):
    """Drop every inventory item `predicate(name)` selects. `predicate` takes the item's name
    as the game reports it (NOT lowercased -- lowercase inside your predicate if you need to).

    Drop every inventory item whose name ends in "salvage" (covers all 8 tiers -- Small,
    Fishy, Barracuda, Large, Plundered, Martial, Fremennik, Opulent -- without needing a config
    list). Left-click-to-drop needs "Drop" in Controller Settings -> Clicking -> Forced Actions,
    same convention as the looting bag's "Empty" elsewhere in this codebase.

    check_ui_blockers=False: CONFIRMED live 2026-08-01 -- click_entity's default UI-blocker check
    exists to catch a *world* click accidentally landing on the floating inventory panel (in
    Resizable-Classic layout), but an inventory item's own screen position is naturally *inside*
    that same panel -- the check flagged every salvage-item click as "blocked", endlessly rotated
    the camera trying to un-block something that was never a world click, and never actually
    clicked anything, so salvage silently never dropped. Not appropriate for any inventory-item
    click, so skip it here same as the rest of this file's world-object clicks keep it on.

    Forced "w" press when facilitiesTabOpen: CONFIRMED live 2026-08-02 -- the boat's Sailing
    Options (Facilities) panel, left open after any sail_to reposition, visually covers the
    inventory panel without clearing GameStateProvider's inventoryTabOpen flag (the inventory
    widget is still technically visible underneath), so open_inventory_tab's idempotent check
    silently skipped the "w" press and every item click landed on the sail menu instead of a
    salvage item, dropping nothing. Bypass the check and force the tab switch whenever the
    Facilities panel is open.

    Space fallback if "w" still didn't switch tabs: CONFIRMED live 2026-08-02 -- when the
    inventory fills up completely while on the Facilities tab, a standard-looking blue "click
    here to continue" dialogue appears and eats the "w" press entirely, leaving the Facilities
    tab open. It's not caught by controller.check_stray_dialog (GET /state's dialogOpen stayed
    False while the user confirmed the dialogue was still on screen) -- like every other Sailing
    UI element found this session, it apparently doesn't wire through the widgets it visually
    resembles. Dismissed with Space specifically (CONFIRMED live 2026-08-02 -- Enter was tried
    first and the user corrected this: it isn't right, Space is), matching
    controller.check_stray_dialog's own existing convention for this exact dialogue style. Only
    retried (not pressed proactively every call) to avoid the same risk that ruled out Enter --
    a key that isn't actually needed doing something unwanted when nothing was actually blocking
    -- this checks facilitiesTabOpen again first and only falls back once the plain "w" attempt is
    confirmed to have failed."""
    if controller.get_sailing().get("facilitiesTabOpen"):
        controller.key_tap(ctx.ser, controller.INVENTORY_TAB_KEY)
        controller.human_delay(0.2, 0.35)
        if controller.get_sailing().get("facilitiesTabOpen"):
            debug_log.log("salvage", "still on the sail menu after 'w' -- dismissing a likely dialogue with Space")
            controller.key_tap(ctx.ser, "SPACE")
            controller.human_delay(0.3, 0.5)
            controller.key_tap(ctx.ser, controller.INVENTORY_TAB_KEY)
            controller.human_delay(0.2, 0.35)
    controller.open_inventory_tab(ctx.ser)
    dropped = 0
    for item in controller.get_inventory():
        name = item.get("name") or ""
        if predicate(name):
            # simulate_mistakes=False (2026-09-03): with Drop as the forced left-click, a
            # simulated near-miss lands on the NEIGHBOURING slot -- fine when that is more
            # salvage, but it drops whatever tool happens to sit there (chisel, rune pouch, ...)
            # when it is not. Same reasoning as banker.py's no-misclick rule for bank UI buttons.
            controller.click_entity(ctx.ser, item, check_ui_blockers=False,
                                    simulate_mistakes=False)
            dropped += 1
            controller.human_delay(0.06, 0.12)  # 40% faster still, on top of the earlier 2x (original 0.2-0.4s)
    return dropped


def drop_salvage(ctx):
    """Drop every inventory item whose name ends in "salvage" -- the pre-2026-09-07 behaviour,
    still used when stowSalvage is off."""
    return drop_items(ctx, lambda name: name.lower().endswith("salvage"))


# Sinking swaps a wreck's object out entirely for a "stump" variant that keeps the exact same
# display name as the active wreck (confirmed live via cache2 -- see
# docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md) -- id is the only way to tell
# them apart. One stump id per tier (Small..Merchant), confirmed against the live game cache.
SUNK_SHIPWRECK_IDS = frozenset({60465, 60467, 60469, 60471, 60473, 60475, 60477, 60479})

# Required boosted Sailing level to actually engage each wreck tier's hook (active-wreck object id
# -> level) -- from data/decompiled/sailing-plugin's SalvagingHighlight.java SALVAGE_LEVEL_REQ,
# which the plugin itself checks (`sailingLevel >= SALVAGE_LEVEL_REQ.get(wreck.getId())`) before
# treating a wreck as "active" rather than "too high level" in its own highlight overlay. A wreck
# below this threshold isn't really salvageable yet even though it's a live, non-stump object
# matching wreckNames -- CONFIRMED live 2026-08-04 (user report): find_wreck_pair_target picked a
# higher-tier wreck as a pairing partner that the player's actual Sailing level couldn't engage,
# only one of the pair was genuinely salvageable, and the resulting midpoint wasn't a sensible
# target for either. req is looked up with a None-is-fine default (fail open) so an id missing from
# this map -- a tier not yet seen live -- doesn't silently exclude an otherwise-valid wreck.
SALVAGE_LEVEL_REQ = {
    60464: 15,
    60466: 26,
    60468: 35,
    60470: 53,
    60472: 64,
    60474: 73,
    60476: 80,
    60478: 87,
}


def _sailing_xp():
    """Cumulative Sailing XP from /skills, or None when unreadable."""
    skills = controller.get_skills() or {}
    xp = (skills.get("Sailing") or {}).get("xp")
    return xp if isinstance(xp, (int, float)) else None


def _boosted_sailing_level():
    """The player's boosted Sailing level, or None when /skills cannot be read right now.
    FIXED 2026-09-03: this used to default to 0, and 0 fails every SALVAGE_LEVEL_REQ check --
    so one timed-out /skills read turned every wreck on the ocean into "above my level" and the
    routine sat logging "no shipwreck in range" until restarted. Unreadable now means "do not
    filter", the same fail-open stance _wreck_is_salvageable already takes for an unknown id."""
    skills = controller.get_skills() or {}
    level = (skills.get("Sailing") or {}).get("boosted")
    return level if isinstance(level, (int, float)) else None


def _wreck_is_salvageable(wreck: dict, sailing_level) -> bool:
    req = SALVAGE_LEVEL_REQ.get(wreck.get("id"))
    return req is None or sailing_level is None or sailing_level >= req


# Padding added on top of max_dist when bounding /objects' radius-limited scene walk (see
# find_nearby_wreck below). CONFIRMED live 2026-08-02: ObjectProvider's radius is centered on the
# PLAYER, but find_nearby_wreck measures from the BOAT's reference position, which can differ from
# the player's exact tile by up to the boat's own WorldView size (~8 tiles per side, confirmed
# live) -- without this padding, a wreck could be within max_dist of the boat but just outside the
# player-centered box and get silently missed.
WRECK_SEARCH_RADIUS_PADDING = 10

# The sideloaded Sailing plugin's SalvagingHighlight.java SIZE_SALVAGEABLE_AREA -- CONFIRMED live
# 2026-08-04 (user report) to be the hook's real engage boundary, replacing the earlier maxDistance
# guess (15, then 8) entirely. runelite-api's own Perspective.getCanvasTileAreaPoly centers this on
# the wreck's own tile ("size the size of the area (ie. 3x3 AoE evaluates to size 3)", and the
# implementation offsets by size * LOCAL_TILE_SIZE / 2 to each side) -- so it's a size x size
# square split evenly around the wreck, not a one-directional radius on its own.
WRECK_SQUARE_SIZE = 15
WRECK_SQUARE_HALF = WRECK_SQUARE_SIZE / 2.0

# The wreck object's own solid collision footprint -- CONFIRMED live 2026-08-04 (user report): a
# 3x3 square centered on the wreck's tile, well inside its own WRECK_SQUARE_SIZE (15x15)
# salvage-range square. Sailing straight at the wreck's own (x, y) -- what find_wreck_pair_target's
# lone-wreck fallback and tick()'s hook-engage-timeout reposition both used to do -- aims the boat
# directly at this solid footprint: even a perfect, zero-overshoot approach ends with the boat
# sitting on top of it, and any real overshoot (see sailing.py's stop_boat drift/overshoot
# logging, live-confirmed to run well past the aim point) sails straight through it -- "bonking"
# the wreck. Both callers now aim short of it instead, see _approach_point_clear_of_wreck.
WRECK_COLLISION_SIZE = 3
WRECK_COLLISION_HALF = WRECK_COLLISION_SIZE / 2.0


def _approach_point_clear_of_wreck(origin_x, origin_y, wreck: dict, player: dict):
    """A point on the straight line between (origin_x, origin_y) and wreck's own tile, offset back
    from the wreck by WRECK_COLLISION_HALF plus the boat's own hull half-size -- the minimum
    distance for the boat's hull (not just its reference point) to clear the wreck's solid
    WRECK_COLLISION_SIZE footprint if this point is reached exactly -- instead of the wreck's own
    (x, y) tile. Still comfortably inside WRECK_SQUARE_HALF (7.5) for any origin more than a
    couple tiles from the wreck, so hook range isn't sacrificed for collision clearance.

    Returns None if origin is already at or within that clearance distance (including origin and
    wreck coinciding exactly) -- CONFIRMED live 2026-08-05 (user report, "we just circling now"):
    returning origin itself in this case (the previous behavior) meant steer_toward saw itself as
    already arrived and issued zero hop clicks, but sail_to still unconditionally calls use_helm()
    first -- which re-engages steering with no fresh minimap click ever telling the boat where to
    go, letting it resume moving on whatever stale heading the game still had queued. Observed
    live: a boat sitting still at hook range drifted ~45 tiles away moments after a "reposition"
    that should have been a no-op. Callers must skip calling sailing.sail_to entirely when this
    returns None -- there is nothing safe to reposition to, and attempting it is actively harmful."""
    wx, wy, wplane = wreck["x"], wreck["y"], wreck.get("plane", 0)
    dx, dy = origin_x - wx, origin_y - wy
    dist = math.hypot(dx, dy)

    clearance = WRECK_COLLISION_HALF + max(player.get("boatWidth", 0), player.get("boatHeight", 0)) / 2.0
    if dist <= clearance:
        return None
    # Rounded to a whole tile (2026-09-03): ocean_path.find_ocean_route tests the target against
    # /tiles' integer tile set, so a float target silently disabled obstacle routing for every
    # approach-point sail and sent the boat on a straight line instead.
    return {
        "x": int(round(wx + dx / dist * clearance)),
        "y": int(round(wy + dy / dist * clearance)),
        "plane": wplane,
    }


# Max distance (tiles, between the two wrecks themselves) for find_wreck_pair_target to treat two
# active wrecks as a pair worth positioning between. Shipwrecks spawn in close pairs at each
# hotspot (wiki-confirmed: "a different pair of shipwrecks", "double Salvage spots" -- see
# data/raw/S/Shipwreck salvaging.txt), and the hook does NOT auto-continue onto a different active
# wreck once its own sinks (CONFIRMED live 2026-08-01) -- so pre-positioning at the midpoint of a
# pair means a re-click after the first sinks can pick up the second immediately, no second sail
# trip needed. Derived from WRECK_SQUARE_SIZE: a boat sitting at the midpoint of two wrecks is
# WRECK_SQUARE_SIZE/2 from each wreck once their centers are WRECK_SQUARE_SIZE tiles apart --
# exactly the shared edge of both squares -- so WRECK_SQUARE_SIZE itself is the right geometric
# bound. (History, see the design spec's 2026-08-03/04 updates: two earlier values -- 30 from
# doubling WRECK_SQUARE_SIZE, and 16 from doubling the old maxDistance=8 -- were both wrong for the
# same reason, mixing up a diameter with a radius.)
PAIR_MAX_TILES = WRECK_SQUARE_SIZE


def find_nearby_wreck(config: dict, max_dist: float, exclude: frozenset = frozenset()):
    """Nearest ACTIVE, actually-salvageable-at-this-level object matching config["wreckNames"]
    within max_dist tiles of the BOAT (not the player), or None (sunk wrecks -- see
    SUNK_SHIPWRECK_IDS -- and wrecks above the player's boosted Sailing level -- see
    SALVAGE_LEVEL_REQ -- are both filtered out; matching either would let the routine treat a wreck
    it can't actually work as "in range" and never look for a real one).

    exclude: a set of (x, y, plane) tuples to skip -- CONFIRMED live 2026-08-05 (user report,
    "we just sail around in circles even though we have multiple wreck pairs around us"): without
    this, a wreck sail_to_pair already failed to actually reach (e.g. blocked/aground) is still the
    single nearest candidate every subsequent tick, so tick() retried the exact same unreachable
    ~2-minute journey forever instead of trying a different, reachable pair. tick() passes the
    give-up blacklist here (see HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS's sibling for the near-wreck
    case) so this picks the next-nearest candidate instead.

    Only used for the farther "is this wreck worth sailing to" check (maxSailDistance) now -- the
    near/hook-range case uses find_wreck_in_hook_range's WRECK_SQUARE_SIZE geometry instead (see
    its own docstring for why a flat max_dist guess didn't hold up for that purpose).

    Measures from /player's boatX/boatY (falling back to the player's own x/y if not aboard a
    WorldEntity) rather than trusting ObjectProvider's own player-relative "distance" field.
    CONFIRMED live 2026-08-02: a Sailing boat's own WorldView is only ~8 tiles per side, and
    standing at one end can be several tiles from the opposite end -- this is a range check against
    something the *boat* needs to reach, not specifically wherever the player happens to be
    standing on deck, so the player-relative distance could read "out of range" (or "in range")
    wrongly by that same margin.

    Pure range test otherwise -- the wreck itself is never clicked (only the boat's own salvaging
    hook is), so on-screen-projectability doesn't matter here, only world position. ObjectProvider
    always sets x/y from the object's WorldPoint regardless of whether it's currently on-screen.
    Chebyshev distance (max(|dx|, |dy|)), matching WorldPoint.distanceTo2D's own convention on the
    Java side.

    Passes max_dist (plus padding, see WRECK_SEARCH_RADIUS_PADDING) as /objects' radius --
    CONFIRMED live 2026-08-02: an unbounded full-scene walk building screen-projection data for
    every object in the loaded scene, every game tick, was a real source of client stutter. This
    routine only ever cares about objects within max_dist anyway, so bounding the scan costs
    nothing here."""
    names = {n.lower() for n in config.get("wreckNames", [])}
    sailing_level = _boosted_sailing_level()

    player = controller.get_player()
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))
    if origin_x is None or origin_y is None:
        return None

    best = None
    best_dist = None
    for o in controller.get_objects(radius=int(max_dist) + WRECK_SEARCH_RADIUS_PADDING):
        if (o.get("name") or "").lower() not in names:
            continue
        if o.get("id") in SUNK_SHIPWRECK_IDS:
            continue
        if not _wreck_is_salvageable(o, sailing_level):
            continue
        wx, wy = o.get("x"), o.get("y")
        if wx is None or wy is None:
            continue
        if (wx, wy, o.get("plane")) in exclude:
            continue

        dist = max(abs(wx - origin_x), abs(wy - origin_y))
        if dist > max_dist:
            continue
        if best_dist is None or dist < best_dist:
            best, best_dist = o, dist

    return best


def find_wreck_pair_target(config: dict, wreck: dict):
    """Given a wreck the routine is about to sail to, look for a second matching active wreck
    within PAIR_MAX_TILES of it (same tier names as wreck, same plane, not sunk, and actually
    within the player's current Sailing level -- see SALVAGE_LEVEL_REQ) and target their midpoint
    instead of the lone wreck -- see PAIR_MAX_TILES's comment for why. Falls back to a point clear
    of the wreck's own solid footprint (_approach_point_clear_of_wreck, see WRECK_COLLISION_SIZE)
    if no partner is found nearby (confirmed with the user: a lone wreck is still worth sailing to
    directly, pairing is an optimization, not a requirement) -- not the wreck's own (x, y) tile,
    which is the collision footprint itself.

    The level check matters specifically here, not just as a general filter -- CONFIRMED live
    2026-08-04 (user report): aiming for the midpoint is only reasonable when BOTH wrecks are
    actually salvageable; a nearby wreck that merely matches wreckNames by name but is above the
    player's level isn't really "active" for them (same distinction the sideloaded Sailing plugin's
    own highlight overlay draws), and pairing toward it skews the target for no benefit -- the
    player can't engage that wreck's hook once there anyway.

    Returns (target, partner): target is a plain {"x", "y", "plane"} dict, the same shape
    sailing.sail_to already expects (not a raw ObjectProvider entry, since a midpoint isn't a real
    game object). partner is the raw partner object if one was found, else None -- callers need it
    (not just target) to verify the boat actually landed within range of BOTH wrecks after
    sailing, since target alone can't distinguish "real wreck, brake to avoid ramming it" from "a
    midpoint over open water, no collision risk"."""
    names = {n.lower() for n in config.get("wreckNames", [])}
    sailing_level = _boosted_sailing_level()
    wx, wy, wplane = wreck.get("x"), wreck.get("y"), wreck.get("plane")

    # get_objects' radius is centered on the PLAYER, not on wreck (see controller.get_objects) --
    # BUG confirmed live 2026-08-03 (user report: "it only aims for one wreck instead of the
    # center of the two"): passing PAIR_MAX_TILES alone left the search box too small whenever
    # wreck itself was still far from the player (the common case -- this runs against far_wreck,
    # up to maxSailDistance away, on the routine's initial approach), so a real partner near wreck
    # sat outside the box and was silently never found. Widen the box by however far the player
    # already is from wreck, so it reaches PAIR_MAX_TILES past wreck regardless of where the
    # player currently stands.
    player = controller.get_player()
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))
    dist_to_wreck = 0
    if origin_x is not None and origin_y is not None:
        dist_to_wreck = max(abs(wx - origin_x), abs(wy - origin_y))

    best_partner = None
    best_dist = None
    for o in controller.get_objects(radius=int(dist_to_wreck + PAIR_MAX_TILES) + WRECK_SEARCH_RADIUS_PADDING):
        if (o.get("name") or "").lower() not in names:
            continue
        if o.get("id") in SUNK_SHIPWRECK_IDS:
            continue
        if not _wreck_is_salvageable(o, sailing_level):
            continue
        if o.get("plane") != wplane:
            continue
        ox, oy = o.get("x"), o.get("y")
        if ox is None or oy is None:
            continue
        if ox == wx and oy == wy:
            # This is the wreck itself, not a partner.
            continue

        dist = max(abs(ox - wx), abs(oy - wy))
        if dist > PAIR_MAX_TILES:
            continue
        if best_dist is None or dist < best_dist:
            best_partner, best_dist = o, dist

    if best_partner is None:
        approach_point = None
        if origin_x is not None and origin_y is not None:
            approach_point = _approach_point_clear_of_wreck(origin_x, origin_y, wreck, player)
        # None means origin is already within safe clearance of the wreck (see
        # _approach_point_clear_of_wreck's own docstring) -- this is the far-approach path, so
        # falling back to wreck's own point is fine here: sail_to's normal speed-scaled braking
        # handles arriving safely, unlike the near-wreck reposition-on-timeout path this same
        # helper also serves, where a from-already-close no-op target caused real drift (CONFIRMED
        # live 2026-08-05 -- see tick()'s own handling of this same None case).
        if approach_point is None:
            approach_point = {"x": wx, "y": wy, "plane": wplane}
        return approach_point, None

    debug_log.log("salvage", f"found a wreck pair {best_dist} tiles apart -- sailing to the midpoint")
    target = {
        "x": (wx + best_partner["x"]) // 2,
        "y": (wy + best_partner["y"]) // 2,
        "plane": wplane,
    }
    return target, best_partner


def _boat_hull_rect(player: dict):
    """Boat's true-tile hull footprint as (min_x, max_x, min_y, max_y) -- CONFIRMED live
    2026-08-04 (user report) against the sideloaded Sailing plugin's own TrueTileIndicator overlay
    (data/decompiled/sailing-plugin's TrueTileIndicator.renderBoatArea), which PlayerProvider's
    boatWidth/boatHeight fields were already modeled on (RuneLite's own WorldEntityConfig bounds,
    not a guessed size). None if the boat's position/bounds aren't available (not aboard a
    WorldEntity)."""
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))
    if origin_x is None or origin_y is None:
        return None
    half_w = player.get("boatWidth", 0) / 2.0
    half_h = player.get("boatHeight", 0) / 2.0
    return origin_x - half_w, origin_x + half_w, origin_y - half_h, origin_y + half_h


def _wreck_in_hook_range(player: dict, wreck: dict) -> bool:
    """Whether the boat's true-tile hull footprint (_boat_hull_rect) overlaps wreck's own
    WRECK_SQUARE_SIZE square -- i.e. whether the hook could actually engage it from here. Replaces
    the old single-point maxDistance guess entirely (CONFIRMED live 2026-08-04, see
    WRECK_SQUARE_SIZE's own comment) with a real rectangle/square overlap test: two axis-aligned
    boxes overlap exactly when they're within reach of each other on BOTH axes at once."""
    hull = _boat_hull_rect(player)
    wx, wy = wreck.get("x"), wreck.get("y")
    if hull is None or wx is None or wy is None:
        return False
    boat_min_x, boat_max_x, boat_min_y, boat_max_y = hull
    return (
        boat_min_x <= wx + WRECK_SQUARE_HALF and boat_max_x >= wx - WRECK_SQUARE_HALF
        and boat_min_y <= wy + WRECK_SQUARE_HALF and boat_max_y >= wy - WRECK_SQUARE_HALF
    )


def find_wreck_in_hook_range(config: dict, exclude: frozenset = frozenset()):
    """Nearest ACTIVE, actually-salvageable-at-this-level object matching config["wreckNames"]
    whose WRECK_SQUARE_SIZE square overlaps the boat's own true-tile hull footprint
    (_wreck_in_hook_range) -- i.e. whether the hook could engage it without moving. This is the
    near-wreck counterpart to find_nearby_wreck's farther maxSailDistance search; see
    find_nearby_wreck's docstring (including for the SALVAGE_LEVEL_REQ filter).

    exclude: (x, y, plane) keys to skip -- tick() passes its give-up blacklist here (2026-09-03)
    so a second in-range wreck is still picked instead of the whole branch going dark."""
    names = {n.lower() for n in config.get("wreckNames", [])}
    sailing_level = _boosted_sailing_level()

    player = controller.get_player()
    hull = _boat_hull_rect(player)
    if hull is None:
        return None
    origin_x = player.get("boatX", player.get("x"))
    origin_y = player.get("boatY", player.get("y"))

    # Bound /objects' radius-limited scene walk the same way find_nearby_wreck does (see its own
    # comment) -- half the wreck square plus half the boat's own footprint on each axis covers the
    # farthest a wreck could be from the boat's reference point and still overlap, plus the usual
    # padding for the player-vs-boat offset.
    search_radius = int(WRECK_SQUARE_HALF + player.get("boatWidth", 0) + player.get("boatHeight", 0)) + WRECK_SEARCH_RADIUS_PADDING

    best = None
    best_dist = None
    for o in controller.get_objects(radius=search_radius):
        if (o.get("name") or "").lower() not in names:
            continue
        if o.get("id") in SUNK_SHIPWRECK_IDS:
            continue
        if not _wreck_is_salvageable(o, sailing_level):
            continue
        if not _wreck_in_hook_range(player, o):
            continue
        wx, wy = o.get("x"), o.get("y")
        if (wx, wy, o.get("plane")) in exclude:
            continue
        dist = max(abs(wx - origin_x), abs(wy - origin_y))
        if best_dist is None or dist < best_dist:
            best, best_dist = o, dist

    return best


def sail_to_pair(ctx, config: dict, wreck: dict) -> bool:
    """Sail to wreck (or, if a nearby partner exists, the pair midpoint -- see
    find_wreck_pair_target) and verify the boat actually ends up within the hook's real
    WRECK_SQUARE_SIZE range of BOTH wrecks, instead of just trusting sail_to's own arrival
    tolerance blind. CONFIRMED live 2026-08-03 (user report): without this check, sail_to's
    brake-then-drift landing could miss a computed pair midpoint by a wide enough margin to land
    outside hook range of one or both wrecks, silently defeating the whole point of pairing --
    sail_to's braking distance is now always speed-scaled (see its own docstring), which was part
    of that same overshoot (a flat, unscaled tolerance used to apply to this exact midpoint case).

    Checks against _wreck_in_hook_range specifically, not some looser notion of "nearby" --
    CONFIRMED live 2026-08-04 (user report): an earlier version of this check used a flat
    maxDistance figure that didn't match the real engage boundary, so it could report "landed
    fine" even when the hook genuinely couldn't reach either wreck from there."""
    target, partner = find_wreck_pair_target(config, wreck)
    ok = sailing.sail_to(ctx, target)

    if ok and partner is not None:
        player = controller.get_player()
        if not (_wreck_in_hook_range(player, wreck) and _wreck_in_hook_range(player, partner)):
            debug_log.log("salvage", "landed outside hook range of one of the paired wrecks -- correcting")
            ok = sailing.sail_to(ctx, target)

    return ok
