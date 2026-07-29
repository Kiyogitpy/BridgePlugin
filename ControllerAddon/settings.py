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
UDP_PORT = 50555

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

# ---------------- Human mistake simulation ----------------
# Small, deliberately-imperfect behaviors so supply/prayer upkeep and
# clicking don't look like a flawless state machine -- real players
# occasionally eat a bite too early, drink a potion before they needed it,
# fumble a prayer toggle, or click empty ground. Kept low-probability and
# rolled independently per check so they show up occasionally, not in
# clusters. See slayer.maintain_supplies/maintain_prayer and
# controller.maybe_misclick.
MISTAKE_OVEREAT_CHANCE = 0.04             # chance/tick to eat before actually hitting eatAt
MISTAKE_OVEREAT_MIN_HP_FRACTION = 0.75    # ...but only once hp is already at least this fraction of real hp
MISTAKE_OVERDRINK_CHANCE = 0.03           # chance/tick to drink a prayer potion before actually hitting restoreAt
MISTAKE_OVERDRINK_MIN_PRAYER_FRACTION = 0.75  # ...but only once prayer is already at least this fraction of real prayer
# Drinking at the exact same prayer point every single time is a tell -- see
# slayer.maintain_defensive_prayer, which rolls an actual drink threshold somewhere in
# [restoreAt, restoreAt + PRAYER_RESTORE_SPREAD] each cycle instead of always
# waiting for exactly the configured restoreAt floor.
PRAYER_RESTORE_SPREAD = 25
MISTAKE_PRAYER_FUMBLE_CHANCE = 0.03       # chance to toggle quick prayers to the wrong state for a tick
MISTAKE_MISCLICK_CHANCE = 0.045           # chance per click_entity call to aim beside the target instead of on it
MISCLICK_OFFSET_PX = (20, 45)             # px; how far off that miss lands -- roughly one tile over, not a wild miss
MISTAKE_RANDOM_CLICK_CHANCE = 0.02        # chance/tick to click a random viewport point instead of acting

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
