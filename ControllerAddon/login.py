# login.py
#
# Detect being logged out and click back in. Three screens, handled very
# differently -- see LoginProvider.java for the full story:
#
#   0. A login message dialog (GameState.LOGIN_SCREEN, loginIndex 24) with a
#      single "Ok" button, e.g. "You were disconnected from the server."
#      Dismissing it returns to the Play Now screen below, so this is just a
#      step on the way rather than a separate flow. Same layout rule as (1),
#      different fixed offset. This replaced a blind, unconditional
#      controller.click(1257, 669) fired on every login-screen tick on the
#      off-chance a dialog was showing -- an absolute desktop pixel that fell
#      outside the canvas entirely on a short window.
#
#   1. The "Play Now" account screen (GameState.LOGIN_SCREEN, loginIndex 10).
#      Drawn as raw pixels by the game engine itself, not a widget or Swing
#      component -- re-confirmed 2026-08-21 against the current client, both
#      by a widget-root walk and by a brute-force getWidget() sweep. Its
#      click point is computed Java-side from the login UI's layout rule
#      (fixed offset in the game's real render space, horizontally centred
#      and top-anchored), so it is resolution-, DPI-, and stretch-independent
#      and needs no recapturing. It arrives here already converted to an
#      absolute screen point as `playNow`.
#
#      This replaced a hardcoded fraction of the canvas's stretched size,
#      which was confirmed broken live: a window resize moved the reported
#      point ~80px off the button.
#
#   2. The "Welcome to Old School RuneScape" / CLICK HERE TO PLAY screen,
#      shown right after (GameState is already LOGGED_IN by this point,
#      even though you're not really in the world yet). This genuinely is a
#      widget, so its bounds are read fresh every call and aren't fragile
#      the way a pixel guess would be.
#
# Note on ordering: controller.py calls attempt_login() BEFORE its
# windowFocused gate, so these clicks can land while RuneLite is
# backgrounded. That is deliberate -- there is no point gating login on
# focus when the whole purpose is to recover unattended.


import controller
import debug_log

LOGIN_RETRY_INTERVAL_S = 1.0

# Camera defaults applied after EVERY login, for every routine (USER 2026-09-03: "the
# zoom out and look from above we should probably add that as a default in login.py so
# it always does that for every routine"). Hold UP until the pitch stops rising, then a
# few wheel detents over the viewport centre. Idempotent: when the pitch is already at
# its ceiling this is a routine restart, not a fresh client, and the zoom is left alone
# so repeated restarts do not keep zooming out (MEASURED gotr runs 65/66).
CAMERA_PITCH_HOLD_MAX_S = 2.5
CAMERA_ZOOM_OUT_CLICKS = 3

_camera_pending = True   # a login (or process start) has happened since the last setup

# Log the full bridge status once every this many consecutive no-op attempts.
# A login screen we have no click point for (an unrecognised loginIndex, e.g.
# the disconnect/retry screen) otherwise fails silently forever: attempt_login
# returns False, controller.py sleeps and calls it again, and nothing in the
# log ever says why. Escalating rather than hard-stopping is deliberate -- a
# transient screen shouldn't kill a long unattended run.
STUCK_REPORT_EVERY = 10

_consecutive_noops = 0


def is_fully_logged_in() -> bool:
    """True only once actually in the game world. GameState.LOGGED_IN alone
    isn't enough -- it's already true while the click-to-play welcome screen
    is still covering the game."""
    status = controller.get_login_status()
    return status.get("loggedIn", False) and not status.get("clickToPlay")


def setup_camera(ser, log=debug_log.log) -> None:
    """Overhead pitch + a slight zoom-out. See the CAMERA_* comment above."""
    import time
    st = controller.get_state() or {}
    pitch = st.get("cameraPitch")
    controller.key_down(ser, "UP")
    t0 = time.time()
    last = pitch
    try:
        while time.time() - t0 < CAMERA_PITCH_HOLD_MAX_S:
            time.sleep(0.3)
            now = (controller.get_state() or {}).get("cameraPitch")
            if now is not None and last is not None and now <= last and time.time() - t0 > 0.8:
                break
            last = now
    finally:
        controller.key_up(ser, "UP")
    if pitch is not None and last is not None and last <= pitch:
        log("login", f"camera already overhead (pitch {pitch}) -- zoom left alone")
        return
    cx = st.get("canvasScreenX", 0) + st.get("viewportWidth", st.get("canvasWidth", 0)) // 2
    cy = st.get("canvasScreenY", 0) + st.get("viewportHeight", st.get("canvasHeight", 0)) // 2
    controller.move_to_human(ser, cx, cy, aim_radius=40)
    controller.scroll_wheel(ser, -CAMERA_ZOOM_OUT_CLICKS)
    controller.human_delay(0.4, 0.7)
    log("login", f"camera set: pitch {pitch} -> {last}, zoomed out {CAMERA_ZOOM_OUT_CLICKS}")


def ensure_camera_after_login(ctx) -> None:
    """Called by the engine once per loop while fully logged in and focused: applies the
    camera defaults the first time after each login (and at process start)."""
    global _camera_pending
    if not _camera_pending:
        return
    _camera_pending = False
    try:
        setup_camera(ctx.ser)
    except Exception as exc:   # never let a camera nudge take the run down
        debug_log.log("!", f"camera setup failed: {exc}")


def attempt_login(ctx) -> bool:
    """Click through whichever login screen is currently showing.

    Returns True if it clicked something (caller should wait and let the
    next tick re-check), False if there was nothing to click yet (e.g. a
    loading transition between screens, or a login screen we have no click
    point for)."""
    global _consecutive_noops, _camera_pending
    _camera_pending = True   # a login is in progress: re-apply the camera once it lands

    status = controller.get_login_status()

    click_to_play = status.get("clickToPlay")
    if click_to_play:
        _consecutive_noops = 0
        debug_log.log("login", "clicking through the welcome screen")
        controller.click_entity(ctx.ser, click_to_play, offset=(0, 0))
        controller.human_delay(1.0, 1.5)
        return True

    ok_button = status.get("okButton")
    if ok_button:
        _consecutive_noops = 0
        debug_log.log("login", "dismissing a login dialog (Ok)")
        controller.click_entity(ctx.ser, ok_button, offset=(0, 0))
        controller.human_delay(1.0, 1.5)
        return True

    play_now = status.get("playNow")
    if play_now:
        _consecutive_noops = 0
        debug_log.log("login", "clicking the Play Now button")
        controller.click_entity(ctx.ser, play_now, offset=(0, 0))
        controller.human_delay(2.0, 3.0)
        return True

    if status.get("gameState") in ("CONNECTION_LOST", "LOGGING_IN", "LOADING", "HOPPING"):
        # The client's own reconnect state ("Connection lost. Please wait...").
        # There is nothing to click; it either comes back by itself or drops to a
        # login screen we do handle. MEASURED 2026-09-02 (gotr_live59): 3.5 minutes
        # of it, reported every 10 attempts as "no login click point" -- noise.
        if _consecutive_noops == 0:
            debug_log.log("login", f"{status.get('gameState')} -- waiting for the client")
        _consecutive_noops += 1
        return False
    _consecutive_noops += 1
    if _consecutive_noops % STUCK_REPORT_EVERY == 0:
        # The loginIndex is the useful part: it names which login screen we are
        # sitting on, which is what an unhandled screen needs in order to be
        # given a click point.
        debug_log.log(
            "!",
            f"no login click point after {_consecutive_noops} attempts -- "
            f"gameState={status.get('gameState')} loginIndex={status.get('loginIndex')}",
        )
    return False
