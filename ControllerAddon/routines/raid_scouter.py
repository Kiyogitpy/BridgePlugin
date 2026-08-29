# routines/raid_scouter.py
#
# Rerolls Chambers of Xeric instances until one matches a target rotation, hands that instance
# to the user's main account, then starts over on a different world. See
# docs/superpowers/specs/Raid-scouter.md for the full design; this file implements the loop
# exactly as MEASURED from a human recording (recordings/2026-08-29_215134_raid-scouter1, 8m03s,
# 37 reloads) rather than guessed -- every object id, widget id and confirmation text below
# comes from that recording's narrative log, not from reasoning about how the interface
# "probably" works.
#
# The loop, as stated by the user 2026-08-29: "create a party, enter raid, refresh until we find
# the configuration, then send discord, wait until main is inside. we leave with same stairs we
# reroll with. swap world, create a new party and go in again."
#
#   setup   -- recruiting board -> make party -> close -> enter chambers (once per WORLD)
#   scout   -- poll /raid, judge the layout
#   reroll  -- Reload the Steps until a fresh, judgeable layout appears
#   hold    -- alert Discord once, wait for the main to be INSIDE the raid (not for it to start
#              -- the bot is gone before that, so the raid scales for the main alone)
#   depart  -- Climb the SAME Steps used to reroll
#   hop     -- switch world, then back to setup
#
# The bot NEVER starts a raid and never fights anything. It only ever clicks the recruiting
# board, the party interface, the chambers entrance, and the Steps inside the start room.
#
# PRECONDITION (manual, this routine cannot set it): the party must be on SMALL raids. A 5-room
# layout (3 combat + 2 puzzle) is the smallest the game generates; large/full raids cannot
# produce it at all.
#
# WORLD HOPPING IS NOT IMPLEMENTED. Every other phase below is built from the recording; hopping
# is not -- nothing in this repo drives a world change today (verified against ControllerAddon/
# and src/main/java/com/example/ on 2026-08-29), and guessing the click sequence would be exactly
# the mistake CLAUDE.md warns against repeatedly ("every guessed click in this codebase has cost
# a live session"). hop_phase safety-stops with an actionable message instead of clicking blind,
# so the routine still delivers a complete, correct scout-reroll-alert-depart cycle -- the part
# that IS measured -- and simply asks for help on the one part that isn't. Extend hop_phase once
# a second recording of a human hopping worlds exists (see the spec's "World hopping" section).

import os
import time

import controller
import debug_log

STEPS_ID = 49999
RECRUITING_BOARD_ID = 29776
CHAMBERS_ENTRANCE_ID = 29777

# widget 499.59, menuAction CC_OP -- MEASURED, both setup cycles in the recording.
MAKE_PARTY_GROUP, MAKE_PARTY_CHILD = 499, 59
# widget 507.2 slot 11 -- the party interface's own close button, MEASURED both cycles.
CLOSE_PARTY_GROUP, CLOSE_PARTY_CHILD, CLOSE_PARTY_INDEX = 507, 2, 11

# The multi-choice dialogue OPTION list (not the chatbox, group 162, which keeps scrollback
# forever -- see mahogany_homes.py's own note on the deadlock that caused). Widget 219.1 slot 1
# is reused for BOTH the reload confirm ("Yes, reload the raid.") and the climb/leave confirm
# ("Leave.") -- MEASURED, different text, same slot, so pressing "1" is correct for either.
DIALOG_OPTION_GROUP = 219

# --- Target rotation: exactly 5 rooms, Vasa first, the rest in any order. Approved by the user
# 2026-08-29 as an EXACT rule, then corrected the same day from an earlier 4-room draft that was
# UNOBTAINABLE -- 3 combat + 2 puzzle is the smallest layout the game generates. See the spec's
# "correction" note; do not shrink this back to 4. ---
TARGET_FIRST = "Vasa"
TARGET_REST = frozenset({"Shamans", "Vespula", "Crabs", "Tightrope"})

# MEASURED reroll cycle time (click to click, 4 consecutive reloads in the recording):
# 5.9s / 6.5s / 4.4s / 4.5s. Cooldowns/timeouts below are sized off this, not guessed.
RELOAD_CLICK_COOLDOWN_S = 2.0    # don't re-click Reload while a load is plausibly still in flight
DIALOG_KEY_COOLDOWN_S = 1.0      # don't spam "1" every tick while the dialogue is up
RELOAD_MAX_ATTEMPTS = 60         # ~= 37 measured reloads plus real headroom, not a tight cap
DIALOG_TIMEOUT_S = 8.0           # no confirm dialogue within this long after a click -> retry

ENTER_CLICK_COOLDOWN_S = 2.0
ENTER_MAX_CLICKS = 10
SETUP_STEP_COOLDOWN_S = 2.0
SETUP_MAX_CLICKS = 6             # per step (board / make party / close) -- generous, cheap steps

DEPART_CLICK_COOLDOWN_S = 2.0
DEPART_MAX_CLICKS = 20

# Anti-idle: a routine's own tight poll loop is a known blind spot for the engine's fidget
# machinery (CLAUDE.md), and hold_phase does nothing else for potentially a long time. A real
# input event (an arrow-key tap) is the only thing guaranteed to reset OSRS's own idle-logout
# timer. First-draft guess, wants live tuning.
IDLE_NUDGE_S = 120.0

HOLD_TIMEOUT_S = 1800.0          # 30 min -- first-draft guess, see spec's open questions
SCOUT_TIMEOUT_S = 60.0           # inside the chambers, no fresh scout within this long

OBJECT_SCAN_RADIUS = 15

DEFAULT_CONFIG = {
    "enabled": True,
    # The main's exact in-game display name, watched for in /players during hold_phase. Empty
    # means "never detect the handoff" -- deliberately not a silent no-op; setup() warns loudly.
    "mainName": "",
    # Discord webhook URL. Prefer the RAID_SCOUT_WEBHOOK env var or ControllerAddon/.raid_webhook
    # (gitignored) over putting a secret in this dict -- see _webhook_url.
    "webhookUrl": None,
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
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # This routine doesn't read from the bridge's GET /config -- no ControllerConfig.java
    # section, same pattern as gemstone_crab.py/mining.py/pest_control.py.
    return DEFAULT_CONFIG


def setup(ctx):
    if not DEFAULT_CONFIG.get("mainName"):
        debug_log.log(
            "raid_scouter",
            "reminder: set mainName in DEFAULT_CONFIG to your main account's exact display "
            "name -- hold_phase cannot detect the handoff without it.",
        )
    debug_log.log(
        "raid_scouter",
        "reminder: party size must be set to SMALL raids -- the target rotation (5 rooms) "
        "cannot be generated on large/full raids.",
    )
    debug_log.log(
        "raid_scouter",
        "world hopping is not implemented (see this file's module docstring) -- the routine "
        "will safety-stop cleanly the first time it needs to hop.",
    )


def evaluate(rooms) -> str:
    """"match" | "reject" | "abstain". Pure -- no bridge calls, see tests/test_raid_scouter.py.

    abstain covers both an empty list (not scouted yet) and any room RaidsProvider could not
    resolve (its name contains "Unknown") -- judging a partial layout would reject a genuinely
    good instance, so this treats "can't tell yet" as "keep waiting", never as a rejection.
    """
    if not rooms:
        return "abstain"
    if any("unknown" in (r or "").lower() for r in rooms):
        return "abstain"
    if len(rooms) != 1 + len(TARGET_REST):
        return "reject"
    if rooms[0] != TARGET_FIRST:
        return "reject"
    if set(rooms[1:]) != TARGET_REST:
        return "reject"
    return "match"


def tick(ctx) -> None:
    config = ctx.state.config
    if not config.get("enabled", True):
        return

    state = controller.get_raid()
    if state is None:
        # Bridge unreachable -- controller.py's own retry/backoff already logs this; nothing
        # useful to do here.
        return

    if not state.get("raidsPluginEnabled", True):
        ctx.state.trigger_safety_stop(
            "RuneLite's built-in Raids plugin is not enabled -- enable it in the plugin list "
            "(no client rebuild needed, it ships with RuneLite)"
        )
        return

    phase = ctx.state.data.get("rs_phase")
    if phase is None:
        # Fresh process or file change -- ctx.state.data was just wiped. Derive from live state
        # rather than defaulting, same lesson as mahogany_homes.py's mh_phase: a restart mid-hold
        # must not silently start clicking Reload on an instance we already matched and alerted.
        if state.get("inRaid") and state.get("scouted") and not state.get("unknownRooms"):
            verdict = evaluate(state.get("rooms") or [])
            phase = "hold" if verdict == "match" else "scout"
        elif state.get("inRaid"):
            phase = "scout"
        else:
            phase = "setup"
        ctx.state.data["rs_phase"] = phase

    PHASE_HANDLERS[phase](ctx, config, state)


def _cooldown_ok(ctx, key: str, cooldown_s: float) -> bool:
    last = ctx.state.data.get(key, 0.0)
    return time.time() - last >= cooldown_s


def _mark(ctx, key: str) -> None:
    ctx.state.data[key] = time.time()


def _bump(ctx, key: str) -> int:
    n = ctx.state.data.get(key, 0) + 1
    ctx.state.data[key] = n
    return n


def _find_object(obj_id: int, radius: int = OBJECT_SCAN_RADIUS):
    for obj in controller.get_objects(radius=radius):
        if obj.get("id") == obj_id:
            return obj
    return None


def _dialog_option_open() -> bool:
    return any(w.get("group") == DIALOG_OPTION_GROUP for w in controller.get_widgets_dump())


def _confirm_dialog_if_open(ctx) -> bool:
    """Presses "1" if the DIALOG_OPTION_GROUP dialogue is up. Returns True if it did.

    Keyboard, not a widget click -- MEASURED as the human's own preferred primitive (38 "1"
    keypresses against 37 reloads, vs only 17 widget clicks): no aiming, no hijack surface,
    works identically for either confirmation text this dialogue shows. Gated on the dialogue
    actually being open first, so this can never fire into an unrelated screen."""
    if not _dialog_option_open():
        return False
    if not _cooldown_ok(ctx, "rs_last_dialog_key_at", DIALOG_KEY_COOLDOWN_S):
        return True
    controller.key_tap(ctx.ser, "1")
    _mark(ctx, "rs_last_dialog_key_at")
    return True


def setup_phase(ctx, config: dict, state: dict) -> None:
    step = ctx.state.data.get("rs_setup_step", "board")

    if step == "board":
        if not _cooldown_ok(ctx, "rs_setup_step_at", SETUP_STEP_COOLDOWN_S):
            return
        board = _find_object(RECRUITING_BOARD_ID)
        if board is None:
            debug_log.log("raid_scouter", "recruiting board not visible -- standing in the "
                                           "wrong spot? this routine does not walk there")
            return
        if controller.click_menu_action(
            ctx.ser, board, "Read",
            refresh_target=controller.object_refresh_target(
                board["x"], board["y"], board["plane"], RECRUITING_BOARD_ID),
        ):
            ctx.state.data["rs_setup_step"] = "make_party"
        _mark(ctx, "rs_setup_step_at")
        clicks = _bump(ctx, "rs_board_clicks")
        if clicks >= SETUP_MAX_CLICKS:
            ctx.state.trigger_safety_stop("could not read the recruiting board")
        return

    if step == "make_party":
        if not _cooldown_ok(ctx, "rs_setup_step_at", SETUP_STEP_COOLDOWN_S):
            return
        widget = controller.get_widget(MAKE_PARTY_GROUP, MAKE_PARTY_CHILD)
        if not widget or not widget.get("clickable"):
            clicks = _bump(ctx, "rs_make_party_clicks")
            if clicks >= SETUP_MAX_CLICKS:
                ctx.state.trigger_safety_stop("\"Make party\" button never became clickable")
            _mark(ctx, "rs_setup_step_at")
            return
        controller.click_entity(ctx.ser, widget, offset=(0, 0), check_ui_blockers=False)
        ctx.state.data["rs_setup_step"] = "close"
        _mark(ctx, "rs_setup_step_at")
        return

    if step == "close":
        if not _cooldown_ok(ctx, "rs_setup_step_at", SETUP_STEP_COOLDOWN_S):
            return
        widget = controller.get_widget(CLOSE_PARTY_GROUP, CLOSE_PARTY_CHILD,
                                        index=CLOSE_PARTY_INDEX)
        if not widget or not widget.get("clickable"):
            # Not fatal -- the party interface may already be closed (e.g. resumed mid-setup).
            # Move on rather than looping on a button that has nothing left to close.
            ctx.state.data["rs_setup_step"] = "enter"
            _mark(ctx, "rs_setup_step_at")
            return
        controller.click_entity(ctx.ser, widget, offset=(0, 0), check_ui_blockers=False)
        ctx.state.data["rs_setup_step"] = "enter"
        _mark(ctx, "rs_setup_step_at")
        return

    # step == "enter"
    if state.get("inRaid"):
        ctx.state.data["rs_setup_step"] = "board"
        ctx.state.data["rs_enter_clicks"] = 0
        ctx.state.data["rs_last_scout_id"] = -1
        ctx.state.data["rs_phase"] = "scout"
        return

    if not _cooldown_ok(ctx, "rs_enter_click_at", ENTER_CLICK_COOLDOWN_S):
        return
    entrance = _find_object(CHAMBERS_ENTRANCE_ID)
    if entrance is None:
        debug_log.log("raid_scouter", "Chambers of Xeric entrance not visible")
        return
    controller.click_menu_action(
        ctx.ser, entrance, "Enter",
        refresh_target=controller.object_refresh_target(
            entrance["x"], entrance["y"], entrance["plane"], CHAMBERS_ENTRANCE_ID),
    )
    _mark(ctx, "rs_enter_click_at")
    clicks = _bump(ctx, "rs_enter_clicks")
    if clicks >= ENTER_MAX_CLICKS:
        ctx.state.trigger_safety_stop("could not enter the Chambers of Xeric")


def scout_phase(ctx, config: dict, state: dict) -> None:
    if not state.get("inRaid"):
        # Dropped out unexpectedly (party dissolved, disconnect, ...) -- start over rather than
        # spinning here forever.
        ctx.state.data["rs_phase"] = "setup"
        ctx.state.data["rs_setup_step"] = "board"
        return

    # SCOUT_TIMEOUT_S watchdog: tracks when scoutId last CHANGED, independent of whether we've
    # judged it yet. This has to be separate from the "already judged" check below -- an early
    # version gated the timeout behind "scout_id <= last_judged", which never covers an abstain
    # (an unresolved/UNKNOWN layout that never finishes scouting): abstain returns without ever
    # touching that branch, so a raid stuck unscouted forever spun here silently with no
    # watchdog ever firing. Tracking "stuck at the same scoutId too long" catches both cases.
    scout_id = state.get("scoutId", 0)
    if ctx.state.data.get("rs_scout_id_seen") != scout_id:
        ctx.state.data["rs_scout_id_seen"] = scout_id
        ctx.state.data["rs_scout_seen_at"] = time.time()
    else:
        elapsed = time.time() - ctx.state.data.get("rs_scout_seen_at", time.time())
        if elapsed > SCOUT_TIMEOUT_S:
            debug_log.log("raid_scouter", f"scoutId stuck at {scout_id} for "
                                           f"{elapsed:.0f}s -- forcing a reroll")
            ctx.state.data["rs_phase"] = "reroll"
            ctx.state.data["rs_reroll_from_party"] = state.get("raidPartyId")
            return

    last_judged = ctx.state.data.get("rs_last_scout_id", -1)
    if scout_id <= last_judged:
        # Same instance we already judged -- e.g. a poll landing before the next reload's
        # RaidScouted event. See RaidsProvider.java's scoutId doc comment. Nothing new to do;
        # the watchdog above still applies if this drags on.
        return

    verdict = evaluate(state.get("rooms") or [])
    if verdict == "abstain":
        return

    ctx.state.data["rs_last_scout_id"] = scout_id

    if verdict == "match":
        rooms = ", ".join(state.get("rooms") or [])
        debug_log.log("raid_scouter", f"MATCH -- world {state.get('world')} "
                                       f"party {state.get('raidPartyId')} -- {rooms}")
        ctx.state.data["rs_webhook_sent_for_scout"] = None
        ctx.state.data["rs_hold_started_at"] = time.time()
        ctx.state.data["rs_last_idle_nudge_at"] = time.time()
        ctx.state.data["rs_phase"] = "hold"
        return

    rooms = ", ".join(state.get("rooms") or []) or "(none)"
    debug_log.log("raid_scouter", f"reject ({rooms}) -- rerolling")
    ctx.state.data["rs_phase"] = "reroll"
    ctx.state.data["rs_reroll_from_party"] = state.get("raidPartyId")


def reroll_phase(ctx, config: dict, state: dict) -> None:
    from_party = ctx.state.data.get("rs_reroll_from_party")
    if from_party is None:
        from_party = state.get("raidPartyId")
        ctx.state.data["rs_reroll_from_party"] = from_party

    scout_id = state.get("scoutId", 0)
    last_judged = ctx.state.data.get("rs_last_scout_id", -1)
    party_id = state.get("raidPartyId")
    if party_id not in (None, -1) and party_id != from_party and scout_id > last_judged:
        # New instance ready AND it has actually been scouted -- see the spec's reroll "leaves
        # when" row. Reset the click/dialogue bookkeeping for the next attempt.
        ctx.state.data["rs_reload_clicks"] = 0
        ctx.state.data["rs_reroll_from_party"] = None
        ctx.state.data["rs_phase"] = "scout"
        return

    if _confirm_dialog_if_open(ctx):
        return

    if not _cooldown_ok(ctx, "rs_reload_click_at", RELOAD_CLICK_COOLDOWN_S):
        return

    steps = _find_object(STEPS_ID)
    if steps is None:
        debug_log.log("raid_scouter", "Steps not visible -- can't reroll from here")
        return

    controller.click_menu_action(
        ctx.ser, steps, "Reload",
        refresh_target=controller.object_refresh_target(
            steps["x"], steps["y"], steps["plane"], STEPS_ID),
    )
    _mark(ctx, "rs_reload_click_at")
    clicks = _bump(ctx, "rs_reload_clicks")
    if clicks >= RELOAD_MAX_ATTEMPTS:
        ctx.state.trigger_safety_stop(
            f"clicked Reload {clicks} times with no new instance -- something is blocking it"
        )


def hold_phase(ctx, config: dict, state: dict) -> None:
    if not state.get("inRaid"):
        ctx.state.data["rs_phase"] = "setup"
        ctx.state.data["rs_setup_step"] = "board"
        return

    scout_id = state.get("scoutId", 0)
    if ctx.state.data.get("rs_webhook_sent_for_scout") != scout_id:
        rooms = ", ".join(state.get("rooms") or [])
        message = (f"MATCH  world {state.get('world')}  party {state.get('raidPartyId')}\n"
                   f"{rooms}\nholding -- waiting for main")
        _send_webhook(config, message)
        ctx.state.data["rs_webhook_sent_for_scout"] = scout_id

    main_name = (config.get("mainName") or "").strip().lower()
    if main_name:
        for player in controller.get_players():
            if (player.get("name") or "").strip().lower() == main_name:
                debug_log.log("raid_scouter", f"{config.get('mainName')} is in -- departing")
                ctx.state.data["rs_phase"] = "depart"
                ctx.state.data["rs_depart_clicks"] = 0
                return

    started_at = ctx.state.data.get("rs_hold_started_at", time.time())
    if time.time() - started_at > HOLD_TIMEOUT_S:
        debug_log.log("raid_scouter", f"main never arrived within {HOLD_TIMEOUT_S:.0f}s -- "
                                       f"giving up on this instance and rerolling")
        _send_webhook(config, "giving up on this instance -- main never arrived, rerolling")
        ctx.state.data["rs_phase"] = "reroll"
        ctx.state.data["rs_reroll_from_party"] = state.get("raidPartyId")
        return

    last_nudge = ctx.state.data.get("rs_last_idle_nudge_at", started_at)
    if time.time() - last_nudge > IDLE_NUDGE_S:
        controller.key_tap(ctx.ser, "LEFT")
        controller.key_tap(ctx.ser, "RIGHT")
        ctx.state.data["rs_last_idle_nudge_at"] = time.time()


def depart_phase(ctx, config: dict, state: dict) -> None:
    if not state.get("inRaid"):
        ctx.state.data["rs_phase"] = "hop"
        ctx.state.data["rs_hop_attempts"] = 0
        return

    if _confirm_dialog_if_open(ctx):
        return

    if not _cooldown_ok(ctx, "rs_depart_click_at", DEPART_CLICK_COOLDOWN_S):
        return

    steps = _find_object(STEPS_ID)
    if steps is None:
        debug_log.log("raid_scouter", "Steps not visible -- can't leave from here")
        return

    controller.click_menu_action(
        ctx.ser, steps, "Climb",
        refresh_target=controller.object_refresh_target(
            steps["x"], steps["y"], steps["plane"], STEPS_ID),
    )
    _mark(ctx, "rs_depart_click_at")
    clicks = _bump(ctx, "rs_depart_clicks")
    if clicks >= DEPART_MAX_CLICKS:
        ctx.state.trigger_safety_stop(
            f"clicked Climb {clicks} times without leaving the chambers"
        )


def hop_phase(ctx, config: dict, state: dict) -> None:
    # See this file's module docstring: world hopping has no measured click sequence anywhere
    # in this codebase. Stopping here is the honest outcome -- guessing a click sequence for an
    # interface nobody has recorded is exactly the mistake CLAUDE.md warns against, and this
    # routine has already done its real job (found a match, alerted, waited for the main, left
    # cleanly). Record a human hopping worlds, then implement this the same way the rest of the
    # loop was built.
    ctx.state.trigger_safety_stop(
        "world hopping is not implemented -- hop worlds manually, then restart this routine "
        "(see routines/raid_scouter.py's module docstring)"
    )


PHASE_HANDLERS = {
    "setup": setup_phase,
    "scout": scout_phase,
    "reroll": reroll_phase,
    "hold": hold_phase,
    "depart": depart_phase,
    "hop": hop_phase,
}


def _webhook_url(config: dict):
    url = config.get("webhookUrl")
    if url:
        return url
    env = os.environ.get("RAID_SCOUT_WEBHOOK")
    if env:
        return env
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".raid_webhook")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return content or None
    except OSError:
        return None


def _send_webhook(config: dict, message: str) -> None:
    url = _webhook_url(config)
    if not url:
        debug_log.log("raid_scouter", f"(no webhook configured) {message}")
        return
    try:
        import requests
        requests.post(url, json={"content": message}, timeout=5.0)
    except Exception as exc:
        # Every failure is swallowed -- a webhook that's down must never stop the scouting loop
        # or wedge a held instance.
        debug_log.log("raid_scouter", f"webhook post failed: {exc!r}")
