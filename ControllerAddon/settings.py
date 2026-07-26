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
DEFAULT_OVERSHOOT_PX = 12       # px; how far a simulated overshoot flies past the target
DEFAULT_OVERSHOOT_CHANCE = 0.25  # probability a given move overshoots before correcting

# ---------------- Moving-target re-aiming ----------------
MOVING_TARGET_MIN_DIST = 60             # px; shorter moves finish too fast to bother re-aiming
MOVING_TARGET_MIDFLIGHT_FRACTION = 0.6  # fly this far toward the stale point before re-aiming
