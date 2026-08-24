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

# Duration floors for a single _move_to_human_exact hop (see estimate_duration_ms, which clamps
# to [floor, 1800]). MEASURED 2026-08-24 with bench_hover.py against a Dashing kebbit:
# estimate_duration_ms floors EVERY hop under ~130px at MOVE_MIN_DURATION_MS, and
# _move_to_human_tracking aims each leg at a *fraction* of the remaining distance -- so every
# leg is short by construction and pays that floor in full. refresh_legs=6 measured a 1380ms
# median flight (6 * 200ms of floor + inter-leg delays + refresh round-trips) to cover ~250px
# that a single hop covers in ~242ms: a 5.7x tax, against a human's own measured 0.6-1.0s
# click-after-walk budget.
#
# The full floor exists so a discrete click gesture isn't robotically instant. A mid-flight
# tracking leg is NOT a discrete gesture -- it's one continuous hand movement being corrected en
# route, and a human doesn't pause between corrections -- so those legs get the lower floor.
# Only the final leg (the visible settling approach that actually lands on the target) keeps the
# full one.
#
# Be clear about what the low value actually buys, because it is NOT "legs now take 60ms":
# estimate_duration_ms's own intercept (90 + 0.55*dist, then **0.92 * 1.9) never returns less
# than ~105ms however short the hop, so 60 is inert AS A FLOOR. Its real effect is removing the
# 200ms *clamp*, letting a leg fall to its natural duration -- ~150ms for the ~42px legs a 6-leg
# flight actually uses. So the honest saving is ~50ms per non-final leg, not ~140ms. It's kept
# as a genuine floor rather than 0 because _move_to_human_exact runs a minimum of 18 steps per
# hop, each an Arduino serial write of ~2ms (~36ms of hardware floor), and a duration below that
# just makes the timing model lie about what it's doing.
# Getting materially faster than this needs the intercept itself lowered for tracking legs, not
# the floor -- deliberately not done here, since that's a humanization change, not a clamp fix.
MOVE_MIN_DURATION_MS = 200              # ms; every ordinary click movement (unchanged behavior)
TRACKING_LEG_MIN_DURATION_MS = 60       # ms; non-final legs of a refresh_legs tracking flight

# ---------------- Human mouse movement v2 (human_move.py) ----------------
# OFF by default. Flipping this switches every move_to_human caller -- all 157 click_entity call
# sites across 17 routines -- onto the v2 engine at once, and flipping it back restores v1
# byte-for-byte. See docs/superpowers/specs/2026-08-24-human-mouse-movement-v2-design.md.
#
# v2 requires Firmware.c v2 (the `S` segment command). With v1 firmware flashed, `S` lines are
# silently ignored and the cursor will not move -- so do not enable this without checking the
# firmware, e.g. with scratch probe verify_firmware_v2.py.
#
# Overridable from the environment so a live test run does not need an edit to a tracked file
# (same pattern as SERIAL_PORT above), which also means a forgotten edit can never leave v2 on:
#     HUMAN_MOVE_V2=1 python -u controller.py routines/golem_crafting.py
HUMAN_MOVE_V2 = os.environ.get("HUMAN_MOVE_V2", "0") == "1"

# Duration model. MEASURED from recorder.py captures of a human hand-playing versus the routine
# playing the same activity, using the ONE reach-extraction method validated against 40
# ground-truth movements (11% duration error; two other methods were 132% out -- see the spec).
#
# The problem being fixed is NOT "not enough randomness". Human and bot both vary; the bot's
# variation is STRUCTURED -- 51% of its timing is predicted by distance, versus 28% for the
# human, because duration is literally f(distance) * uniform(0.85, 1.15).
HM2_D_REF = 52.0        # px, ~the median reach distance (human 54.7, bot 51.0)
HM2_MED_REF_MS = 200.0  # ms at HM2_D_REF. Today's speed, taken from the CODE rather than a
                        # measurement: estimate_duration_ms(52) = 153.8, clamped up by its own
                        # 200 ms floor. The recording's 184 ms would bake in the extractor's
                        # known 11% underestimate.
HM2_SLOPE_MS_PER_BIT = 68.8  # NOT the human's measured 148.7 -- see below.
HM2_SIGMA = 0.675       # human log-duration sigma (the current engine's own is only ~0.154)
HM2_MIN_MS = 90.0
HM2_MAX_MS = 1800.0
# HM2_SLOPE_MS_PER_BIT is scaled by the same factor as the median (148.7 * 200/432 = 68.8).
# Keeping the human's ABSOLUTE slope while halving the median would raise the RELATIVE slope from
# 0.344/bit to 0.744/bit -- i.e. "keep current speed" would have made the distance signature
# stronger than a human's, not weaker. Simulated: r^2 0.190 and sigma 0.684, against human
# targets of 0.280 and 0.675, and the current engine's 0.322 / 0.154.

# Submovement structure. The primary reach deliberately UNDERSHOOTS; corrections follow until the
# cursor is inside tolerance. Human reaches need 4+ submovements 42% of the time; the current
# engine manages that 2% of the time, because maybe_overshoot_point is one 42% coin flip.
# v2 does not use maybe_overshoot_point at all.
HM2_UNDERSHOOT_MEAN = 0.88     # fraction of the remaining gap the primary actually covers
HM2_UNDERSHOOT_SIGMA = 0.12    # spread; this is what generates the submovement-count tail
HM2_LATERAL_ERROR = 0.06       # sideways aim error on the FIRST reach, as a fraction of the gap.
                               # Deliberately SMALL. Lateral error was tried as the source of the
                               # human's path length, but at the ~0.30 needed for that the residual
                               # after the primary reach is ~33% of the gap however much it decays,
                               # so 90%+ of reaches needed 4+ submovements against a human's 42%.
                               # Path length comes from HM2_CURVE_FRACTION instead; this only keeps
                               # misses from being purely along the approach axis.
HM2_LATERAL_DECAY = 0.45       # per-correction decay of both lateral error and undershoot spread.
                               # Load-bearing: without it, lateral error stops the gap closing and
                               # the submovement count runs to 95% at 4+ against a human's 42%.
HM2_MAX_SUBMOVES = 7
HM2_TOLERANCE_PX = 0.6         # a reach ends when the cursor is this close. LOAD-BEARING for the
                               # submovement distribution, and the single most sensitive constant
                               # here: at 2.0 only 7% of reaches need 4+ submovements, at 1.0 it is
                               # 19%, at 0.6 it is 41% -- against a human's 42%. A human keeps
                               # making sub-pixel settling corrections; stopping at 2px does not.
HM2_PRIMARY_TIME_SHARE = 0.62  # of total duration; corrections share the remainder

# Path shape. TUNED EMPIRICALLY (tests/test_human_move.py measures both properties directly):
# these values give straightness 0.787 and 40% of reaches at 4+ submovements, against human
# measurements of 0.787 and 42% and the current engine's 0.963 and 2%.
# The offsets are large because they OPPOSE each other -- a Bezier does not reach its control
# points, so a +/-0.5 offset pair yields a bulge of roughly 20% of the movement length, not 50%.
HM2_CURVE_FRACTION = 0.35      # S-curve control-point offset, as a fraction of submovement length
HM2_TREMOR_PX = 1.5            # mid-frequency wobble amplitude (ceiling; see next line)
HM2_TREMOR_MAX_FRACTION = 0.06 # ...but never more than this fraction of the submovement's own
                               # length. A flat 1.5px is invisible on a 300px reach and 75% of a
                               # 2px correction; unscaled, it made the cursor churn in place near
                               # the target ("hovers over it and jitters like crazy").
HM2_MIN_PX_PER_SEGMENT = 2.5   # a submovement gets at most one segment per this much travel, so
                               # a short correction is one settling move rather than several
                               # tremor-dominated ones

# Emission. Segment length trades smoothness against how far Python runs ahead of the cursor;
# the firmware queue holds 16, so LOOKAHEAD must stay well under that.
HM2_SEGMENT_MS = 18.0
# Closed-loop corrections after the planned path finishes. Needed because one emitted mouse unit
# is NOT one screen pixel: this machine measured 0.74 px/unit (Windows pointer speed 5/11), which
# made an open-loop v2 land a median 122 px short over a median 495 px move. v1 never showed this
# because it re-read the cursor every step.
#
# Cheap: once _px_per_unit has converged, zero or one correction fires. The budget only matters
# on a COLD START, when the scale is still 1.0 on a 0.74 machine -- each correction then closes
# only ~74% of the gap, so the residual falls as 0.26^n and 3 corrections left a 5-6px miss on
# the first few movements. 6 takes that below a pixel while costing nothing in steady state.
HM2_FINAL_CORRECTIONS = 6
# Tolerance for the FINAL-CORRECTION loop specifically -- deliberately NOT HM2_TOLERANCE_PX.
#
# GetCursorPos returns INTEGERS, so the cursor's measured position is quantised to 1px. A
# tolerance below that quantum can never be satisfied for a target on a fractional pixel: the
# loop bounces between two adjacent integers until its budget runs out. Reported live as "the
# mouse kinda just hovers over it and jitters like crazy", and raising HM2_FINAL_CORRECTIONS
# from 3 to 6 for cold-start accuracy is what made it obvious.
#
# 1.5 sits above the measurement resolution with margin. It does NOT loosen real accuracy:
# bench-measured landing error is ~0.65px mean, well inside this. HM2_TOLERANCE_PX (0.6) stays
# where it is -- it governs SUBMOVEMENT PLANNING, where it is load-bearing for the submovement
# count distribution and is never compared against a quantised measurement.
HM2_CORRECTION_TOLERANCE_PX = 1.5

# Ignore target drift smaller than this when deciding whether to replan mid-flight. Without it,
# a slowly-walking NPC triggers a replan on EVERY refresh, and near the target each replanned
# submovement is a tiny hop -- a burst of micro-movements in place rather than one settling
# approach. Same jitter report as above.
HM2_REPLAN_MIN_DRIFT_PX = 6.0
HM2_LOOKAHEAD_SEGMENTS = 3

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
MISTAKE_OVEREAT_MIN_HP_FRACTION = 0.23    # ...but only once hp is already at least this fraction of real hp
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
