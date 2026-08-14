# login.py
#
# Detect being logged out and click back in. Two screens, handled very
# differently -- see LoginProvider.java for the full story:
#
#   1. The saved-account tile chooser (GameState.LOGIN_SCREEN). Drawn as raw
#      pixels by the game engine itself, not a widget or Swing component
#      (confirmed empirically: both a full widget-tree dump and a Swing/AWT
#      component-tree dump came back with nothing for it). Its click point
#      is a fraction of the canvas's actual on-screen size, captured once
#      from a confirmed on-screen point -- NOT a fraction of /state's raw
#      mouseCanvasX/Y, which is in RuneLite's pre-stretch render space and
#      mixes units if divided directly by the stretched canvas width. Not
#      something read fresh each time. If it stops working (different
#      saved-account list, a client update that reflows the screen), it
#      needs recapturing: hover the tile, note the true on-screen point,
#      update LoginProvider.java's ACCOUNT_TILE_FRACTION_X/Y.
#
#   2. The "Welcome to Old School RuneScape" / CLICK HERE TO PLAY screen,
#      shown right after (GameState is already LOGGED_IN by this point,
#      even though you're not really in the world yet). This genuinely is a
#      widget, so its bounds are read fresh every call and aren't fragile
#      the way the tile click is.

import time

import controller
import debug_log

LOGIN_RETRY_INTERVAL_S = 1.0


def is_fully_logged_in() -> bool:
    """True only once actually in the game world. GameState.LOGGED_IN alone
    isn't enough -- it's already true while the click-to-play welcome screen
    is still covering the game."""
    status = controller.get_login_status()
    return status.get("loggedIn", False) and not status.get("clickToPlay")


def attempt_login(ctx) -> bool:
    """Click through whichever login screen is currently showing.

    Returns True if it clicked something (caller should wait and let the
    next tick re-check), False if there was nothing to click yet (e.g. a
    loading transition between screens, or connection lost with neither
    screen up)."""
    status = controller.get_login_status()

    click_to_play = status.get("clickToPlay")
    if click_to_play:
        debug_log.log("login", "clicking through the welcome screen")
        controller.click_entity(ctx.ser, click_to_play, offset=(0, 0))
        controller.human_delay(1.0, 1.5)
        return True

    if status.get("gameState") == "LOGIN_SCREEN":
        tile = status.get("accountTile")
        if tile:
            debug_log.log("login", "clicking the saved account tile")
            controller.click_entity(ctx.ser, tile, offset=(0, 0))
            controller.human_delay(2.0, 3.0)
            return True

    return False
