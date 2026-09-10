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
# BRIDGE_API_BASE overrides this. It exists for ONE case: the client now runs in the OSRS VM
# (CLAUDE.md, "The VM"), where the guest's own controller.py still wants plain localhost:8080 --
# but a HOST-side reader (the osrs-debug MCP server in .mcp.json, tools/*.py) has to go through
# the NAT forward at 127.0.0.1:18080 to see the same bridge. Note the UDP event stream does NOT
# cross that forward: a natpf1 rule forwards host->guest, while the plugin sends events to the
# guest's own loopback, so poll_events is guest-only. HTTP reads work from either side.
API_BASE = os.environ.get("BRIDGE_API_BASE", "http://localhost:8080")
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

# ---------------- Pause/resume/panic feedback (added 2026-08-27) ----------------
# toggle_pause beeps on every F2 press so it's audible without alt-tabbing back to check --
# see start_pause_listener's own note on why F2 needs to work without focus in the first place.
# Two different tones so pause vs resume is tellable by ear alone. panic_exit's own beep is
# deliberately much lower/deeper than either -- a distinct "something serious just happened"
# tone for the hard-kill hotkey, not just a third shade of the same pause/resume pair.
PAUSE_BEEP_HZ = 500      # lower tone: pausing
RESUME_BEEP_HZ = 900     # higher tone: resuming
PAUSE_BEEP_DURATION_MS = 180
PANIC_BEEP_HZ = 150      # much deeper than either pause tone -- the emergency-exit hotkey
PANIC_BEEP_DURATION_MS = 250

# ---------------- Camera yaw turn by middle-mouse drag (controller.face_toward, 2026-09-10) ----------------
# The human's vale-totem recording (2026-09-10) turns the camera with middle-mouse drags of
# 89-360px, each right before a far viewport click, to click a distant object directly instead
# of minimap-hopping. These back controller.drag_middle / face_toward.
CAMERA_YAW_TOLERANCE = 120          # yaw units (of 2048) within which the target is "up the screen" -- no turn
CAMERA_DRAG_MAX_PX = 360            # the human's longest single drag; a bigger turn is split into several
CAMERA_DRAG_MARGIN_PX = 40          # keep a drag this far inside the canvas edge (a maximized window clamps)
CAMERA_UNITS_PER_PX_DEFAULT = 0.95  # yaw units (of 2048) per dragged pixel. MEASURED live 2026-09-10 on this
                                    # client: 334/352, 336/360, 348/360, 312/337; self-calibrates after the first drag
CAMERA_DRAG_SIGN_DEFAULT = -1       # a LEFTWARD (-px) drag INCREASES yaw here (same measurement); learned per drag
CAMERA_MAX_DRAGS = 4                # give up facing after this many drags (a backwards client costs one to learn)
CAMERA_DRAG_SETTLE_S = 1.5
# PITCH KEEPER. MEASURED live 2026-09-10 (vale totems run 11, /state sampled at 0.15s): a
# 360px yaw drag swings the pitch by +-300 JAU14 mid-drag (the human-shaped path bulges) and
# nets about +115 per drag, so a lap's 4-8 drags would tilt the camera to its limit within a
# few laps (USER: "with enough camera moves our up and down would eventually end up in a weird
# place"). After every yaw drag the pitch is pulled back to where it was when the process first
# turned the camera, with a vertical middle-drag whose rate is learned like the yaw's.
CAMERA_PITCH_TOLERANCE = 60         # JAU14 pitch units left uncorrected (a drag nets ~115)
CAMERA_PITCH_PER_PX_DEFAULT = 7.6   # JAU14 per dragged px until learned: the yaw's 0.95 units x 8
CAMERA_PITCH_SIGN_DEFAULT = 1       # +1: a downward (+dy) drag raises the pitch value; learned on the first correction
CAMERA_PITCH_MAX_TRIES = 3          # corrective drags per yaw drag
CAMERA_PITCH_AIM_RADIUS_PX = 2      # landing jitter of a corrective drag (the default 10 is half of a 20px drag)
CAMERA_PITCH_PER_PX_MIN = 3.0       # bounds on the learned vertical rate (MEASURED 3.8..9.3 with the loose aim)
CAMERA_PITCH_PER_PX_MAX = 12.0
CAMERA_DRAG_MARGIN_Y_PX = 160       # a drag's ends stay this far from the canvas top/bottom: its bulge must not clamp
CAMERA_DRAG_CURVE_FRACTION = 0.04   # S-curve control offset for a middle DRAG. MEASURED 2026-09-10 from the human's
                                    # recording: 89-360px middle drags wander 2-28px vertically, under 8% of their
                                    # length; a reach's HM2_CURVE_FRACTION (0.35) would bulge ~100px, all of it pitch.          # wait at most this long for the camera to finish interpolating after a drag

# ---------------- Human-like aiming ----------------
DEFAULT_AIM_RADIUS = 10         # px; move_to_human's own fallback when a caller doesn't specify one
ENTITY_AIM_RADIUS = 3           # px; used by click_entity -- tighter than the generic default since
# CLICK IN PLACE (controller._cursor_already_on). USER 2026-09-10: "the weird little flick if we
# basically have the mouse already over the target". Within the first radius the cursor is on
# the aim to within its own jitter (ENTITY_AIM_RADIUS) plus the hover tracker's drift
# (PREHOVER_TRACK_DRIFT_PX 10); up to the second, /hover naming the entity is what decides.
CLICK_IN_PLACE_RADIUS_PX = 12
CLICK_IN_PLACE_HOVER_RADIUS_PX = 40
                                 # ground items and inventory slots have small clickboxes
DEFAULT_OVERSHOOT_PX = 22       # px; how far a simulated overshoot flies past the target
DEFAULT_OVERSHOOT_CHANCE = 0.42  # probability a given move overshoots before correcting

# ---------------- Moving-target clicking (added 2026-08-27) ----------------
# After move_to_human returns, click_entity takes ONE more look at the target (refresh_target)
# before the mouse-down -- the flight's last re-aim happens at a submovement boundary, so the
# settle + click tail (~100-300ms) used to be blind, and while the player walks that is easily
# tens of px of drift ("clicks land behind"). Only a drift beyond this triggers a corrective
# move; the threshold absorbs the aim jitter (ENTITY_AIM_RADIUS soft-clamped at 2.5x) plus the
# caller offset that mid-flight refreshes deliberately drop, so a stationary target never pays
# an extra motion.
PRECLICK_MAX_DRIFT_PX = 18
# Verify-then-click: with expected_action/expected_target passed to click_entity, the /hover
# endpoint (what a left-click would do RIGHT NOW) is read before committing the click. The
# hover text updates a render frame after the cursor stops, so a mismatch is re-read a few
# times before it counts.
HOVER_VERIFY_TRIES = 3
HOVER_VERIFY_RETRY_DELAY_S = 0.04
# npc_index_refresh_target's hold key quantizes LocalPoints (128 units/tile) so only real
# motion -- own player, camera, or the NPC -- invalidates the held aim point, while hull
# animation jitter still holds. 16 units = 1/8 tile, ~5-11px on screen at typical zoom.
NPC_HOLD_LOCAL_QUANTUM = 16

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
# ON by default since 2026-08-26 (user: "we want to move to v2"). It was opt-in until then.
# Overridable from the environment either way (same pattern as SERIAL_PORT above), so a live
# comparison against the v1 engine needs no edit to a tracked file:
#     HUMAN_MOVE_V2=0 python -u controller.py routines/golem_crafting.py
#
# Enabled off the back of a live comparison on routines/plank_make.py that day, all three
# configs measured the same way (aim is logged pre-jitter, so landing error includes
# ENTITY_AIM_RADIUS):
#
#                        long flight (>400px)     landing error          cycle
#     v1                 0.789s  1062 px/s        mean 2.32px max 6.1px  53.26s
#     v2 (old timing)    0.346s  2244 px/s        mean 1.28px max 1.4px  --
#     v2 (human timing)  0.671s  1538 px/s        mean 0.94px max 1.4px  53.98s
#
# i.e. v2 at the corrected timing is MORE accurate than v1, not less, and costs 1.4% throughput.
HUMAN_MOVE_V2 = os.environ.get("HUMAN_MOVE_V2", "1") == "1"

# Duration model. MEASURED from recorder.py captures of a human hand-playing versus the routine
# playing the same activity, using the ONE reach-extraction method validated against 40
# ground-truth movements (11% duration error; two other methods were 132% out -- see the spec).
#
# The problem being fixed is NOT "not enough randomness". Human and bot both vary; the bot's
# variation is STRUCTURED -- 51% of its timing is predicted by distance, versus 28% for the
# human, because duration is literally f(distance) * uniform(0.85, 1.15).
HM2_D_REF = 52.0        # px, ~the median reach distance (human 54.7, bot 51.0)
HM2_MED_REF_MS = 170.0  # ms at HM2_D_REF. RE-FITTED 2026-08-27 against
                        # recordings/2026-08-27_001343_human_dataset (13m11s of mixed banking,
                        # slayer and travel; 136 clean reaches after discarding any spanning a
                        # dropped-record gap). Bucketed medians -- 259ms at 64-128px, 425 at
                        # 128-256, 454 at 256-512, 651 at 512-1024 -- fit 170ms at 52px.
                        # Was 432, which is the median duration ACROSS all distances rather than
                        # the value AT the reference distance, so it was far too slow for short
                        # reaches. Predicts 413ms at the 211px median against a measured 415.
HM2_SLOPE_MS_PER_BIT = 120.5  # ms per doubling of distance, from the same 2026-08-27 fit.
                              # NOTE this reverses a same-day change to 60.0 that was fitted to
                              # the 42-second banking-only recording (n=32 reaches). That sample
                              # was too small: the larger mixed dataset puts the slope at 120.5,
                              # near the golem capture's 148.7 and nowhere near 60. The lesson is
                              # in the intercept, not the slope -- 432ms at the reference
                              # distance was the actual error, and re-fitting the slope to
                              # compensate for it on a small sample was chasing the wrong knob.
HM2_SIGMA = 0.368       # log-duration spread AROUND the distance model.
                        # CORRECTED 2026-08-27 from 0.675, which was a DOUBLE COUNT: that figure
                        # was measured across all distances, so it contained the distance
                        # variation this model already applies through HM2_SLOPE_MS_PER_BIT, and
                        # the two multiplied. Residual spread around the fitted model is 0.368
                        # (n=136). USER-REPORTED symptom of the double count: "veeeery slow" --
                        # at 0.675 one reach in ten ran 2.38x the median and one in a hundred
                        # 4.81x; at 0.368 that is 1.60x and 2.35x.
HM2_MIN_MS = 90.0       # 1st percentile of the re-fitted model is ~72ms; 194 (scaled from the
                        # old 432 median) would have clipped a large share of short reaches.
HM2_MAX_MS = 1400.0           # MEASURED: the human's LONGEST single reach was 1287ms in the 42s
                              # sample and 1320ms across 13 minutes of mixed activity, so this
                              # cap is confirmed by both. 3888 let the lognormal tail
                              # plan 2-4s flights that no human in the sample ever made -- the
                              # other half of "veeeery slow", and far more visible than the
                              # median because a single 3s flight is impossible to miss.
#
# CHANGED 2026-08-26 (user: "we want to move to v2 so making it look more human is the aim").
# v2 originally kept the OLD ENGINE'S SPEED on purpose and fixed only the shape of the timing
# distribution. That left it running at 200/432 = 46% of human duration, and it showed: MEASURED
# live that day on routines/plank_make.py, an 842px reach took v2 264ms (3188 px/s) where v1 took
# 748ms (1025 px/s) and a human's own measured model predicts ~1029ms. Fitts's law puts the
# floor for that reach near 360ms, so v2 was below what a human can physically do, not merely
# fast. USER-REPORTED as "looks way too fast".
#
# The fix is to undo ONLY the speed-preservation half, keeping everything v2 actually fixed. All
# four constants are scaled by 432/200 = 2.16, which lands the two that were ever MEASURED
# (median 432, slope 148.7) back on their real human values -- 68.8 * 2.16 = 148.6, i.e. exactly
# reversing the note below. Because median and slope scale together, the RELATIVE distance
# signature v2 exists to control is untouched at 0.344/bit; only the absolute clock changes.
# HM2_MIN_MS/HM2_MAX_MS are scaled by the same factor so the distribution's tails keep their
# shape (the new floor, 194ms, lands almost exactly on v1's own 200ms MOVE_MIN_DURATION_MS).
#
# ORIGINAL RATIONALE, kept because it is still what governs the ratio between these two numbers:
# HM2_SLOPE_MS_PER_BIT was scaled by the same factor as the median (148.7 * 200/432 = 68.8).
# Keeping the human's ABSOLUTE slope while halving the median would raise the RELATIVE slope from
# 0.344/bit to 0.744/bit -- i.e. "keep current speed" would have made the distance signature
# stronger than a human's, not weaker. Simulated: r^2 0.190 and sigma 0.684, against human
# targets of 0.280 and 0.675, and the v1 engine's 0.322 / 0.154.

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
# The primary's share of the clock. KEPT, after briefly being retired and restored the same day.
#
# Retiring it (allocating every submovement's time purely by distance) made all submovements run
# at the SAME speed by construction -- and that is not human. MEASURED from the recording: a real
# reach is ballistic-then-homing, and the corrective phase runs median 4.2x, p90 17.9x SLOWER per
# pixel than the initial throw. Uniform speed has no mechanism for that asymmetry.
#
# Working back from that 4.2x: the primary carries ~HM2_UNDERSHOOT_MEAN (0.88) of the distance
# and the corrections ~0.12, so ratio = (0.88/Tp) / (0.12/Tc) = 7.33 * Tc/Tp. Solving for 4.2
# gives Tp = 0.64 -- which is what this constant already was. It was never the defect; the equal
# split AMONG the corrections was (see _plan_spans).
#
HM2_PRIMARY_TIME_SHARE = 0.62


# Ceiling on a whole movement's MEAN speed, px/ms, applied as a duration floor in duration_for.
# MEASURED: duration_for could plan 10.3 px/ms at 2000px, where the human's per-reach mean speed
# is p50 0.68, p90 1.10. NOTE the cap sits BELOW the fastest single reach in the recording: two
# independent extractions of that maximum disagreed (3.43 vs 1.70 px/ms) because they segment
# reaches differently, so it is one disputed outlier rather than a solid number. 2.5 clears both
# extractions' p90 comfortably while refusing the disputed tail -- erring low is the safe side
# here. HM2_MAX_CORRECTION_PX_PER_MS bounds only the residual
# correction loop, so the planned path needs its own bound. Only ever clips the tail, and leaves
# the guarded statistics inside their assertions (r^2 0.197 -> 0.204, log-sigma 0.682 -> 0.681,
# median duration 423 -> 412ms).
HM2_MAX_MEAN_PX_PER_MS = 2.5

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

# Speed ceiling for the final-correction loop, px per ms.
#
# ADDED 2026-08-26 (user: "sometimes half of a path is super slow then goes blazing fast").
# That loop used to emit the WHOLE residual gap as one segment with a hardcoded HM2_SEGMENT_MS
# duration, whatever the distance -- so the further the planned path fell short, the faster the
# cursor snapped to close it. The gap is largest exactly when _px_per_unit is still converging
# (it starts at 1.0; this machine's true value is ~0.74), which under-travels the whole planned
# path by ~26% and then crams that 26% into 18ms. On a 527px reach that is 7.6 px/ms.
#
# MEASURED from a human banking by hand (recordings/2026-08-26_225917_mousemove, n=2097 windows
# of ~10ms): p50 0.40, p90 2.06, p99 3.99, max 5.37 px/ms. So the old behaviour EXCEEDED the
# fastest instant in the whole human session, and a 900px reach at the un-learned scale would
# have hit 13 px/ms -- 2.4x it.
#
# CORRECTED 2026-08-26 from 4.0, after USER-REPORTED "if it misses the correction is very fast".
# 4.0 was derived from the human's OVERALL instantaneous speed (p99 3.99) -- the wrong
# population. A correction is the HOMING phase of a reach, and measured on its own it is an
# order of magnitude slower: splitting each recorded reach at its ballistic/homing boundary
# gives correction-phase speeds of p50 0.141, p90 0.329, MAX 0.498 px/ms (n=67, the larger
# 2026-08-27 dataset; the 42s banking sample agreed at 0.207/0.358/0.428). The old cap was
# ~10x the fastest correction the human ever made, so it barely bound anything. 0.50 sits just
# above that measured maximum.
HM2_MAX_CORRECTION_PX_PER_MS = 0.50

# When the residual after the open-loop path is too big to be a SETTLE, fly a fresh curved reach
# instead of the straight-line correction below it. IDENTIFIED 2026-08-28 by the user: dragging
# the physical mouse away mid-flight produced "a monotone uniform movement back" -- because the
# correction loop is a constant-velocity straight walk with no curve, tremor or easing. That is
# right for a few px of scale residual and badly wrong for a long haul, and it is reached
# whenever the real cursor diverges from the open-loop plan: a hand on the mouse, or a target
# whose projection jumps with the camera (MEASURED the same day: flights logged dist=133px with
# open_err=255px, and one dist=31px with open_err=800px).
# 60px is chosen to sit above the largest residual a plausible pointer-scale error can produce
# (a 300px reach at 12% off is ~36px) and below the divergences actually observed, so ordinary
# settling still takes the cheap straight path. At the 0.50 px/ms cap above, 60px is already
# ~120ms of uniform slide -- about where it becomes visible.
HM2_RECURVE_PX = 60.0
HM2_MAX_RECURVE = 2            # bounded: a recurved reach can itself fall short, but not forever

# Minimum cosine between the net MOUSE UNITS commanded during a flight and the net PIXELS the
# cursor actually moved, for that flight to be allowed to teach the pointer-scale estimator.
# The pointer scale is a single isotropic scalar, so it can only stretch or shrink motion ALONG
# the commanded direction -- it can never rotate it. A net displacement pointing somewhere else
# therefore contains motion nobody commanded (a hand on the mouse, the pointer clipping a screen
# edge), and its ratio would teach the estimator a number unrelated to this machine.
# MEASURED 2026-08-28: clean flights sit at cos 1.00000 (worst deviation 0.03 deg) whether the
# assumed scale is correct, 26% stale or 40% over -- a stale scale produces large but perfectly
# COLLINEAR errors, which is exactly why this rejects on direction and not on size; those large
# samples are the most informative ones there are. Injected drags measured 17.0 and 31.7 deg.
# 0.999 (2.6 deg) sits in the enormous gap between the two.
# KNOWN LIMIT: a disturbance closely parallel to the movement axis is not caught (a +60,+40 drag
# on a reach toward +600,+300 measured 0.83 deg). Bounding that needs a robust statistic over
# several samples, not a per-flight test -- deliberately not done, as every polluting sample
# actually observed live was far off-axis.
HM2_SCALE_COLLINEAR_MIN = 0.999

# Ignore target drift smaller than this when deciding whether to replan mid-flight. Without it,
# a slowly-walking NPC triggers a replan on EVERY refresh, and near the target each replanned
# submovement is a tiny hop -- a burst of micro-movements in place rather than one settling
# approach. Same jitter report as above.
HM2_REPLAN_MIN_DRIFT_PX = 6.0

# How long the mouse button is held per click. MEASURED 2026-08-27 across five human recordings
# (n=589 down->up pairs, dominated by the 13-minute mixed session at n=359): median 85-90ms,
# p90 ~125ms, max 242ms, log-sigma ~0.27. sample_human_delay's median is sqrt(min*max) and its
# sigma is ln(max/min)/4, so this range lands median 85ms / sigma 0.266 with the fat upper tail
# the old (0.045, 0.11) range's tighter geometry under-produced (its median sat at 70ms).
CLICK_HOLD_RANGE_S = (0.05, 0.145)

# ---------------- Session tempo (added 2026-08-27) ----------------
# A human's movement durations are NOT independent draws: MEASURED over five human recordings
# (786 reaches; the 13m11s 2026-08-27_001343_human_dataset dominates at n=564), the residual
# around the distance model has lag-1 autocorrelation ~0.15, and the long session's 2-minute
# block means wander with sigma 0.120 against 0.050 expected under independence -- fast spells
# and slow spells, on a timescale of minutes. Modeled as one Ornstein-Uhlenbeck "mood" term
# added to duration_for's log-duration draw: stationary sigma below, time constant ~5 minutes,
# session starting in a random mood. Rides ON TOP of HM2_SIGMA (marginal grows 0.368 -> 0.387
# in quadrature, still under the human's 0.49-0.66 measured with the same reach segmentation),
# so every existing distribution bound still holds with the tempo pinned to 0.
# The same measurement pass KILLED the handedness hypothesis: mean signed path curvature was
# tiny against its sigma AND flipped sign between sessions (left-bulge share 39-64%), so v2's
# zero-mean curve draw is already right -- do not add a curvature bias.
HM2_TEMPO_SIGMA = 0.12
HM2_TEMPO_TAU_S = 300.0
HM2_LOOKAHEAD_SEGMENTS = 3

# Motion that is already committed at the instant an X flush reaches the firmware: serial +
# parse latency (~2-4 ms) plus the active segment, which ALWAYS finishes (it executes in place
# at queue[q_head] and is never evicted -- see firmware/Firmware.ino's flushQueue). move()
# rewinds its position/time bookkeeping to the first emission at or past elapsed + this margin.
# MEASURED 2026-08-27 (8 seeds): that rewind actually lands roughly one segment AHEAD of what
# played, not short of it. commit_ms already budgets for the active segment's remaining time on
# its own, and the scan then picks the emission ACTIVE AT commit_ms (the first entry with
# t_ms >= it) rather than the last one ending at or before it -- so the active segment's
# duration is effectively counted twice. Measured error: +18ms and +1.2 to +30.6px, one-sided,
# across those 8 seeds. Left uncorrected deliberately: the direction is the benign one -- an
# ahead-of-reality cur makes the replan UNDERshoot, which the residual corrections (and
# HM2_UNDERSHOOT_MEAN/HM2_UNDERSHOOT_SIGMA) already exist to absorb, and re-deriving the margin
# with no reflashed hardware to verify against would be a design change made blind. Second-order
# cost: queued_ms stays ~18ms optimistic for the rest of a flushed flight, so the lookahead
# throttle (HM2_LOOKAHEAD_SEGMENTS) effectively holds roughly one segment LESS real firmware
# buffer than its nominal three for that flight.
HM2_FLUSH_COMMIT_MS = 22.0

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

# Event-driven outcomes (2026-09-04, docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md).
# REACTION_DELAY_S: after a confirmed outcome event, bridge_events.await_outcome sleeps a draw from
# this range (minus the time the HTTP confirm already took) before returning, so the next click does
# not land tens of milliseconds after the event every time. MEASURED 2026-09-04 with
# fit_reaction.py over the GOTR human benchmark (recordings/2026-09-03_061331_*, n=186
# outcome->next-click pairs): p10 219ms, p25 282ms, median 522ms, p75 1723ms. The tail is clicks
# that were not reactions to that event at all (an XP drop mid-mining while the human waits), so
# the range covers the reactive cluster (p10..~median), not p25..p75.
REACTION_DELAY_S = (0.20, 0.50)
# Polling cadence of the HTTP confirm when there is no event stream at all (ctx.events is None).
FALLBACK_POLL_S = 0.25

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
# RETUNED TWICE 2026-08-27. First against measurement (human never rests >4.5s in active play,
# so a nudge every 15-20s was frozen for multiples of the human's whole stillness range), then
# re-triggered after phase-a-verify live-measured the interval itself as the new stillness
# ceiling (bot still-p90 7.1s ~= the 4-9s timer): the drift now fires on time since the last
# DELIBERATE cursor movement, with the budget drawn from IDLE_FIDGET_STILL_* below -- so a
# routine actively driving the mouse never pays a pre-action drift, and only genuine idle is
# filled in. The old MIN/MAX interval constants are gone with the wall-clock timer.
IDLE_MOUSE_MOVE_RADIUS_PX = 30      # px; how far the idle drift wanders from the cursor's current spot
# The drift exists to kill FREEZES, so it must not fire inside the ordinary gaps between our own
# actions. MEASURED live 2026-08-30 across three attempts at this: drifts were firing after as
# little as 1.3s of stillness, repeatedly, while the routine was actively clicking hotspots --
# each one walking the cursor off target so the next click flew it back. USER, three times over:
# "when we flick i think the idle function drags back the mouse" ... "seems like flicking still
# happen" ... "we still flick like crazy".
#
# Marking individual call sites busy did NOT work: work_phase got a mark_busy and the drifts
# simply reappeared inside _climb, which has none. Every clicking path would need one, and new
# ones keep appearing -- so the floor lives HERE instead, in one place, where no call site can
# forget it.
#
# 4.0s is not a hunch. The same measurement this whole feature rests on (see the In-wait
# fidgeting block below) found the human is NEVER still longer than 4.5s. A drift at 1.3s is not
# imitating the human, it is out-moving them; one past 4s fills a gap they would never leave.
IDLE_MOVE_MIN_STILL_S = 4.0
IDLE_MOUSE_MOVE_SPEED_SCALE = 3.5   # multiplies move_to_human's normal travel time -- idle drift should look slow

# ---------------- In-wait fidgeting (added 2026-08-27) ----------------
# MEASURED (13.1 active human minutes vs 164 active bot minutes, breaks excluded, same reach
# segmentation as the tempo fit): the human cursor is busy 80% of active play and NEVER sits
# still longer than 4.5s (still>1s: 2.9/min, median 1.5s, p90 2.3s); the bot's sat frozen 87%
# of the time with still-period p90 11.8s and runs past 100s -- the loudest remaining
# behavioral difference after the within-reach kinematics were fitted. Long human_delay sleeps
# are therefore split into human-length still segments separated by small out-and-back drifts.
# The fidget is POSITION-INVARIANT (it always returns to its origin) so human_delay remains a
# drop-in sleep: hover-parking call sites (rest the mouse on a target now, click it later --
# see hover_entity and mastering_mixology's speedups) rely on the cursor staying put through
# the wait. Known blind spot, deliberate: routines' own poll loops sleeping 0.2-0.5s per
# iteration never enter this path -- that is Phase B (a background fidgeter behind a global
# action lock), to be built only if a live re-measure still shows long freezes.
IDLE_FIDGET_MIN_SLEEP_S = 2.0      # only sleeps at least this long fidget at all
IDLE_FIDGET_CHANCE = 0.75          # per still-segment boundary; a human sometimes just waits
IDLE_FIDGET_RADIUS_PX = (15, 80)   # out-leg distance from the resting position
IDLE_FIDGET_STILL_MEDIAN_S = 1.5   # lognormal still-segment length between fidgets (measured)
IDLE_FIDGET_STILL_SIGMA = 0.33     # ln(2.3/1.5)/1.2816, from the human's median -> p90
IDLE_FIDGET_MAX_STILL_S = 4.0      # the human's longest observed active still was 4.5s
# The return leg's aim radius. It was 0 -- a mathematically exact snap back to the origin --
# until 2026-09-06, which made the cursor tether to ONE pixel and oscillate about it. MEASURED
# live that day against the human recording of the SAME activity (karambwan fishing,
# recordings/2026-09-06_090950_cg-bot1 vs ..._083723_karamwan-fishing): consecutive
# out-and-backs about one anchor 19/17/14 for the bot against the human's single chain of 10,
# and 11 rests on the identical pixel against the human's 3. Pre-change bot runs sat at 5-12%
# tethered bursts, the human at 26%, this run at 33% -- the fidgeter had gone past the human it
# was fitted to, in the one dimension a human cannot imitate: pixel-exact repetition.
# 3px keeps the position-invariance contract intact (hover-parked call sites click IN PLACE,
# and every real click's own aim radius is far wider than this).
IDLE_FIDGET_RETURN_JITTER_PX = 3
# Further than this from the remembered anchor means something OTHER than a fidget moved the
# cursor (a click, a walk, the idle drift), so the next fidget re-takes the anchor there.
# Comfortably above the return jitter's own soft cap (pick_aim_point clamps at 2.5x radius).
IDLE_FIDGET_ANCHOR_RESET_PX = 15

# ---------------- Approach-tile humanization ----------------
# See travel.maybe_approach_before_click -- occasionally walk partway toward
# a target via a plain tile click instead of always relying on the
# click-the-entity-and-let-the-game-auto-walk shortcut every single time.
APPROACH_TILE_CHANCE = 0.35        # probability of taking an approach hop instead of clicking straight through
APPROACH_TILE_MIN_DISTANCE = 6     # tiles; don't bother approaching once already this close
APPROACH_TILE_FRACTION_MIN = 0.35  # how far along the player->target line the approach tile lands
APPROACH_TILE_FRACTION_MAX = 0.70
