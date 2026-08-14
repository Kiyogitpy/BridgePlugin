# settings.py
#
# All the tunable, Python-side constants controller.py and its human-movement
# engine use, collected in one place so they're easy to find and change
# without hunting through controller.py.
#
# Game-behavior settings (combat targets, prayer, food, breaks, safety
# limits, click-priority forcing) live in RuneLite's Controller Settings
# panel instead -- see ControllerConfig.java / GET /config. This file is only
# for things that aren't exposed there: the bridge connection, the Arduino
# serial port, and human-movement realism tuning.

import os

# ---------------- Bridge connection ----------------
API_BASE = "http://localhost:8080"
UDP_PORT = 50555          # controller.py's own event stream -- routine on_event dispatch depends
                           # on this one, only controller.py should ever bind it.
DEBUG_UDP_PORT = 50556    # mcp_debug_server.py's poll_events -- a SEPARATE port the Java plugin
                           # fans the same events out to (see UdpEventSender.java), added
                           # 2026-08-13 after the two ports being the same one caused a real bind
                           # race: a poll_events() call during a live debugging session bound
                           # UDP_PORT in the debug server process, and controller.py's own launch
                           # right after lost the race for that port and crashed
                           # (OSError [WinError 10048]). Two ports means neither process's own
                           # bind can ever block the other's.

# ---------------- Arduino serial connection ----------------
SERIAL_PORT = os.environ.get("BRIDGE_SERIAL_PORT", "COM6")
SERIAL_BAUD = 115200

# ---------------- Main loop pacing ----------------
CONFIG_POLL_INTERVAL = 1.0   # how often GET /config is refreshed
STATE_POLL_INTERVAL = 0.3    # pace of the main decision loop
INVENTORY_SIZE = 28

# ---------------- Human-like aiming ----------------
DEFAULT_AIM_RADIUS = 10         # px; move_to_human's own fallback when a caller doesn't specify one
ENTITY_AIM_RADIUS = 3           # px; used by click_entity -- tighter than the generic default since
                                 # ground items and inventory slots have small clickboxes
DEFAULT_OVERSHOOT_PX = 22       # px; how far a simulated overshoot flies past the target
DEFAULT_OVERSHOOT_CHANCE = 0.42  # probability a given move overshoots before correcting

# ---------------- Moving-target re-aiming ----------------
MOVING_TARGET_MIN_DIST = 60             # px; shorter moves finish too fast to bother re-aiming
MOVING_TARGET_MIDFLIGHT_FRACTION = 0.6  # fly this far toward the stale point before re-aiming

# ---------------- Humanization profile drift ----------------
# HumanProfile (controller.py) holds the parameters that drive pick_aim_point,
# build_bezier_path, and human_delay. It re-rolls on a randomized interval during a session so
# no single fixed formula produces every sample -- a fixed formula run for an entire session is
# itself a statistical signature over enough samples, independent of how well-shaped any single
# draw is. See docs/superpowers/specs/2026-08-14-humanization-overhaul-design.md. Ranges are
# centered on the constants they replace (noted per line) so default/typical behavior doesn't
# shift enough to break existing tuning.
PROFILE_REROLL_MINUTES_RANGE = (10.0, 20.0)  # how long a profile lasts before re-rolling
AIM_SIGMA_FACTOR_RANGE = (1.3, 1.8)          # pick_aim_point: sigma = radius / this (was fixed 1.5)
CURVE_MAG_LOW_RANGE = (0.06, 0.10)           # build_bezier_path: low end of curve_mag fraction (was fixed 0.08)
CURVE_MAG_HIGH_RANGE = (0.16, 0.24)          # build_bezier_path: high end of curve_mag fraction (was fixed 0.20)
DELAY_SKEW_RANGE = (0.0, 1.0)                # human_delay: feeds its lognormal sigma directly

# ---------------- Human mistake simulation ----------------
# Small, deliberately-imperfect behaviors so supply/prayer upkeep and
# clicking don't look like a flawless state machine -- real players
# occasionally eat a bite too early, drink a potion before they needed it,
# or click empty ground. Kept low-probability and rolled independently per
# check so they show up occasionally, not in clusters. See
# slayer.maintain_supplies/maintain_defensive_prayer and controller.click_entity's
# simulate_mistakes/MISTAKE_MISCLICK_CHANCE.
MISTAKE_OVEREAT_CHANCE = 0.0013            # chance/tick to eat before actually hitting eatAt
MISTAKE_OVEREAT_MIN_HP_FRACTION = 0.75    # ...but only once hp is already at least this fraction of real hp
# Drinking at the exact same prayer point every single time is a tell -- see
# slayer.maintain_defensive_prayer, which rolls an actual drink threshold somewhere in
# [restoreAt, restoreAt + PRAYER_RESTORE_SPREAD] each cycle instead of always
# waiting for exactly the configured restoreAt floor.
PRAYER_RESTORE_SPREAD = 25
MISTAKE_MISCLICK_CHANCE = 0.025           # chance per click_entity call to aim beside the target instead of on it
MISCLICK_OFFSET_PX = (20, 45)             # px; how far off that miss lands -- roughly one tile over, not a wild miss
# A human who clicks empty ground beside a target notices nothing happened and clicks again
# almost immediately -- see click_entity's post-miss corrective reclick.
MISCLICK_RECLICK_DELAY_S = (0.25, 0.55)

# ---------------- Idle mouse drift ----------------
# Every so often, nudge the cursor a short distance from wherever it already
# is, slowly, with no click -- see controller.maybe_idle_mouse_move. A cursor
# that only ever moves with clear purpose (straight toward the next target,
# every single time, at normal click speed) is a tell; real players glance
# the mouse around between actions too, but that's a small resting-hand
# shift, not a full click-speed traverse across the viewport.
IDLE_MOUSE_MOVE_MIN_INTERVAL_S = 15.0
IDLE_MOUSE_MOVE_MAX_INTERVAL_S = 20.0
IDLE_MOUSE_MOVE_RADIUS_PX = 20      # px; how far the idle drift wanders from the cursor's current spot
IDLE_MOUSE_MOVE_SPEED_SCALE = 3.5   # multiplies move_to_human's normal travel time -- idle drift should look slow

# ---------------- Approach-tile humanization ----------------
# See travel.maybe_approach_before_click -- occasionally walk partway toward
# a target via a plain tile click instead of always relying on the
# click-the-entity-and-let-the-game-auto-walk shortcut every single time.
APPROACH_TILE_CHANCE = 0.35        # probability of taking an approach hop instead of clicking straight through
APPROACH_TILE_MIN_DISTANCE = 6     # tiles; don't bother approaching once already this close
APPROACH_TILE_FRACTION_MIN = 0.35  # how far along the player->target line the approach tile lands
APPROACH_TILE_FRACTION_MAX = 0.70
