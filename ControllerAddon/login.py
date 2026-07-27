# login.py
#
# Detect being logged out and click back in. Two screens, handled very
# differently -- see LoginProvider.java for the full story:
#
#   1. The saved-account tile chooser (GameState.LOGIN_SCREEN). Drawn as raw
#      pixels by the game engine itself, not a widget or Swing component
#      (confirmed empirically: both a full widget-tree dump and a Swing/AWT
#      component-tree dump came back with nothing for it). Its click point
#      is a canvas-relative fraction captured once by hovering the mouse
#      over it and reading /state's mouseCanvasX/Y -- not something read
#      fresh each time. If it stops working (different saved-account list,
#      a client update that reflows the screen), it needs recapturing the
#      same way: hover the tile, read the fraction, update
#      LoginProvider.java's ACCOUNT_TILE_FRACTION_X/Y.
#
#   2. The "Welcome to Old School RuneScape" / CLICK HERE TO PLAY screen,
#      shown right after (GameState is already LOGGED_IN by this point,
#      even though you're not really in the world yet). This genuinely is a
#      widget, so its bounds are read fresh every call and aren't fragile
#      the way the tile click is.

import random
import time

import controller

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
        print("[login] clicking through the welcome screen")
        controller.click_entity(ctx.ser, click_to_play, offset=(0, 0))
        time.sleep(random.uniform(1.0, 1.5))
        return True

    if status.get("gameState") == "LOGIN_SCREEN":
        tile = status.get("accountTile")
        if tile:
            print("[login] clicking the saved account tile")
            controller.click_entity(ctx.ser, tile, offset=(0, 0))
            time.sleep(random.uniform(2.0, 3.0))
            return True

    return False
