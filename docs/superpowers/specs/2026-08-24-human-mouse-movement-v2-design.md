# Human mouse movement v2 — design

Date: 2026-08-24
Status: IMPLEMENTED and VERIFIED LIVE 2026-08-24 (flag still off by default).

## Problem

`move_to_human` produces movement whose **timing is a deterministic function of distance**. That
is a statistical signature, and it is present regardless of how much jitter is layered on top.

This was measured, not inferred. `recorder.py` captured both a human hand-playing golem crafting
and the routine playing the same activity, so the mover is the only thing that differs. Every
number below comes from those recordings via `refit_v2.py`, restricted to **click-anchored
reaches** (idle mouse drift is excluded — it is not a targeted movement, and including it
flatters exactly the statistic we care about). A third human recording, 3.3 minutes of mining,
was captured specifically to enlarge this sample.

| metric (median, extracted reaches) | human (n=37) | bot (n=196) |
|---|---|---|
| **timing explained by distance (r²)** | **0.280** | **0.512** |
| duration ms | 432 | 184 |
| log-duration σ | 0.675 | 0.539 |
| slope ms/bit | 148.7 | 103.6 |
| straightness (1.0 = straight line) | 0.787 | 0.963 |
| submovements 4 / 5 / 6+ | 16 / 13 / 13% | 2 / 0 / 0% |
| time-to-peak velocity (fraction) | 0.261 | 0.269 |

### How these numbers were arrived at (read before changing them)

Extracting "reaches" from free-play mouse data is genuinely hard and **three different
segmentation methods gave three different answers** (human median 331 / 1008 / 1632 ms). The
numbers above come from the one method that was *validated against ground truth*:
`gen_groundtruth.py` drove 40 movements of known start, end and duration and
`validate_extractor.py` measured how well each method recovered them.

| method | duration error vs truth |
|---|---|
| **backward velocity walk (0.08 px/ms, 120 ms idle hold)** | **11.2%** (slight underestimate) |
| distance-to-target "arrival" | +132% — rejected |
| velocity walk at higher thresholds | matched almost nothing |

Two known limitations, both accepted:
- The ground truth is necessarily **bot-generated**, so it validates the method against bot-like
  movement. The human-specific failure mode (hand tremor keeping the velocity above threshold, so
  drift merges into a "reach") is *not* covered by it. A `MIN_STRAIGHTNESS = 0.45` guard rejects
  those merged-drift reaches; that guard is a judgement call, not a validated one.
- Recorder points are **canvas** coordinates while the ground truth log is in **screen** pixels,
  which is why validation shows a uniform ~52% distance error across every method. It affects no
  conclusion here: r² and ms/bit slope are both invariant under a constant scale factor.

Three things follow, and the first is the one that matters:

1. **The bot is randomly *structured*.** 51% of bot timing is predicted by distance versus 28%
   for the human — the direct fingerprint of `duration = f(distance) × uniform(0.85, 1.15)`.
   Simulating that formula alone gives a log-duration σ of just **0.154** against the human's
   0.675, so the engine also has roughly four times too little timing variance. (The bot's
   *measured* σ of 0.539 is inflated by spread in distance, not by real jitter — which is why
   simply widening the ±15% jitter is not the fix; it would add variance without removing the
   distance dependence.)
2. **Paths are too clean** (0.963 vs 0.787) even after resampling both to a common 8 ms grid to
   remove the sampling-rate confound (human recordings sample at 1–2 ms, the bot at 6 ms).
3. **Submovement counts are collapsed** onto 1-2, because `maybe_overshoot_point` is a 42% coin
   flip on top of a base movement. 42% of human reaches need four or more submovements; 2% of the
   bot's do.

`ease_in_out`'s velocity profile is **already correct** (time-to-peak 0.261 human vs 0.269 bot)
and is explicitly out of scope.

## Decisions taken

- **Keep current speed.** The human is ~2× slower (432 vs 184 ms measured). Matching that was offered and
  declined; v2 re-centres on today's speed and fixes only the structure.
- **Ship alongside, opt-in, default off.** There are 157 `click_entity` call sites across 17
  routines and `golem_crafting.py` runs unattended for hours. v2 must be revertible by flipping
  one boolean.

## Design

### Where it lives

New self-contained module `ControllerAddon/human_move.py`. `settings.py` gains
`HUMAN_MOVE_V2 = False` plus v2's tunables. `controller.move_to_human()` gains a **single**
dispatch at the top; nothing else in the existing movement path is edited, so all 157 call sites
inherit v2 without being touched and v1 remains byte-identical when the flag is off.

v2 drives the firmware's `S` segment command (see `data/Firmware.c` v2: sub-pixel deltas
interpolated at the 1 kHz USB frame rate). v1 keeps using `M`. The two paths do not share code.

### 1. Duration — the core fix

```
D_REF     = 52.0    # px, ~the median reach distance (human 54.7, bot 51.0)
MED_REF   = 200.0   # ms at D_REF -- today's speed, taken FROM THE CODE not a measurement:
                    # estimate_duration_ms(52) = 153.8 ms, which its own 200 ms floor clamps
                    # up to 200. Using the recording's 184 ms would bake in the extractor's
                    # known 11% underestimate.
SLOPE     = 68.8    # ms/bit
SIGMA     = 0.675   # human log-duration sigma

median_ms(d) = MED_REF + SLOPE * log2(d / D_REF)
duration_ms  = median_ms(d) * lognormal(sigma = SIGMA)
```

**`SLOPE` is 68.8, not the human's measured 148.7, and this is load-bearing.** Re-centring the
median from 432 ms to 200 ms while keeping the absolute slope would raise the *relative* slope
from 0.344/bit to 0.744/bit — "keep current speed" would have made the distance signature
**stronger** than the human's, not weaker. The slope is scaled by the same factor as the median
(148.7 × 200/432 = 68.8), preserving the human's relative structure exactly.

Verified by simulation over 6000 draws against the human distance distribution:

| model | median | r² | log-duration σ |
|---|---|---|---|
| **v2** | 195.5 ms | **0.190** | **0.684** |
| current engine | 200.0 ms | 0.322 | 0.154 |
| human target | — | 0.280 | 0.675 |

Note the current engine's σ of **0.154** — it has roughly four times too little timing variance,
which the recording-based comparison masked (the bot's measured 0.539 was inflated by spread in
distance, not by real jitter). v2's 0.684 matches the human's 0.675.

Clamp to `[90, 1800]` ms. The simulation models the *duration formula only*, not the whole
engine; the authoritative check is measuring a real v2 recording (see Verification).

### 2. Path shape — 0.963 → ~0.787 straightness

A single Bezier arc cannot get there honestly: 15% excess path length needs `curve_mag ≈ 0.24`,
the very top of the current range, and would produce one clean bow every time. Instead:

- **Low-frequency wander**: a cubic Bezier whose two control points are offset in *opposing*
  perpendicular directions, giving an S-curve rather than a bow.
- **Mid-frequency tremor** superimposed along the path.

Amplitudes are fitted, not derived — the target is the measurement. Final:
`HM2_CURVE_FRACTION = 0.35`, `HM2_TREMOR_PX = 1.5`, giving straightness **0.799** against the
human's 0.787.

**Lateral aim error was tried as the source of path length and rejected.** At the ~0.30 needed to
reach 0.787 by that route, the residual after the primary reach is ~33% of the gap however fast
it decays, so 90%+ of reaches needed 4+ submovements against a human's 42%. Path length comes
from curvature; `HM2_LATERAL_ERROR` is now 0.06 and only stops misses being purely on-axis.

### 3. Submovements — emergent, not sampled

A ballistic primary submovement that **undershoots by a variable fraction**, followed by
corrective submovements until the cursor is inside the target tolerance. The count emerges from
undershoot variance and target size; undershoot variance is tuned until the distribution matches
the human's tail: 42% of human reaches take FOUR or more submovements, versus 2% for the bot.
Final: `HM2_UNDERSHOOT_MEAN = 0.88`, `HM2_UNDERSHOOT_SIGMA = 0.12`, `HM2_TOLERANCE_PX = 0.6`,
giving **41%**.

`HM2_TOLERANCE_PX` is the most sensitive constant in the module and was nearly missed: at 2.0px
only 7% of reaches need 4+ submovements, at 1.0px 19%, at 0.6px 41%. A human keeps making
sub-pixel settling corrections; stopping at 2px does not. It was also nearly mis-tuned — the
first sweep used a uniform 60-600px distance mix (median ~330px) rather than the realistic
lognormal one (median ~55px), and short reaches converge in far fewer submovements. **Tune this
against a realistic distance distribution or the answer is wrong by 5x.**

Sampling the count directly from the histogram was rejected: it matches the histogram while
faking the mechanism, and breaks as soon as target size varies — which is precisely what happens
once clickbox-aware aiming lands (`RuneLiteUtil.shapeCoords`, same date).

**v2 does not use `maybe_overshoot_point`.** The 42% coin flip is the direct cause of the
collapsed distribution and has no equivalent in v2.

### 4. Explicitly out of scope

- `ease_in_out` — measured already correct (time-to-peak 0.261 human vs 0.269 bot).
- Minimum-jerk trajectories — a lateral move at best given the above; considered and dropped.
- Velocity-lead target prediction and the continuous control loop (the earlier tracking design).
  Those address *tracking a moving target*, a separate concern from the movement signature. They
  remain worth doing and should be their own spec.

## Verification

Success is defined against already-measured quantities, so this either passes or it does not:

Measured after implementation (pure-function sampling, `tests/test_human_move.py`):

| metric | v2 | human | old engine |
|---|---|---|---|
| r² (timing vs distance) | **0.178** | 0.280 | 0.512 |
| log-duration σ | **0.690** | 0.675 | 0.154 |
| median duration ms | **195** | 432 | 200 |
| straightness | **0.799** | 0.787 | 0.963 |
| 4+ submovements | **41%** | 42% | 2% |

r² lands below the human's rather than on it, which is the safe direction. Median duration is
held at the old engine's 200 ms by the explicit "keep current speed" decision.

### Verified live 2026-08-24 (slayer.py on gargoyles, n=39 reaches)

| metric | v2 live | human | v1 |
|---|---|---|---|
| r² (timing vs distance) | **0.288** | 0.280 | 0.512 |
| straightness | **0.768** | 0.787 | 0.963 |
| time-to-peak | 0.353 | 0.261 | 0.269 |
| 4+ submovements | 14% | 42% | 2% |
| log-duration σ | 0.525 | 0.675 | 0.539 |

r² and straightness — the two headline signatures — now match a human almost exactly. The
submovement tail and σ remain short of the human, and time-to-peak is still slightly late; all
three trace to the residual final-correction drains and are the obvious next tuning target.

### TWO BUGS FOUND ONLY ON HARDWARE (do not re-derive these)

**1. v2 was open-loop and missed by 24.6%.** First hardware run: median error 122 px over a
median 495 px move, max 556 px. One emitted mouse unit is NOT one screen pixel -- this machine
measured 0.74 px/unit (Windows pointer speed 5/11 rather than the neutral 6/11). v1 never showed
this because `_move_to_human_exact` re-reads the cursor every step and emits `target - current`,
so any scaling silently cancels. v2 planned the path up front and streamed it blind.

Fixed by learning the ratio at runtime (`human_move._px_per_unit`, EMA) plus a bounded
closed-loop correction. Do not hardcode 0.74 -- it is a per-machine OS setting. Covered by
`test_v2_lands_on_target_even_when_the_os_scales_pointer_motion`.

**2. The first fix for (1) broke the velocity profile.** Draining the firmware queue at EVERY
submovement boundary made it accurate but inserted stalls mid-reach: time-to-peak went 0.269 ->
0.586 against a human's 0.261, and duration inflated ~44% over the model's budget. Time-to-peak
was the one metric the old engine already had right. Fixed by draining ONCE after the planned
path; the learned scale is what makes staying open-loop through the path safe.

Bench accuracy after both fixes: **median 1.00 px, mean 0.65 px, max 1.41 px** across 12 moves
including cold start, against v1's 0.20 px mean.

Still outstanding:

| check | target | how |
|---|---|---|
| r² (timing vs distance) | 0.15-0.35 | record v2, run `refit_v2.py` |
| median duration | ~200 ms | same |
| straightness | 0.75-0.83 | same |
| submovement histogram | tail present: >=25% at 4+ submovements | same |
| flight time / accuracy | no regression vs v1 | `bench_hover.py` |
| v1 unchanged with flag off | byte-identical behaviour | unit tests |

Procedure: flip `HUMAN_MOVE_V2 = True`, run a routine with `recorder.py` alongside, then re-run
the same analysis scripts used to produce this document's tables against the new recording.

## Risks and caveats

- **The human baseline is n=37**, after a 3.3-minute mining recording (125 clicks) was added and
  the extractor was rebuilt and validated. r² = 0.280 still has wide confidence intervals at that
  sample size. More recording is the cheapest way to firm this up
  (`python -u recorder.py <label>`, then play normally) -- but note the extractor, not the sample
  size, was the dominant error source, and that is now measured.
- **Windows pointer speed is at 5/11, not the neutral 6/11** — measured 0.74 px per mouse unit.
  The OS rescales and re-quantises every report, partly undoing the firmware's sub-pixel
  precision. Setting it to 6/11 before tuning v2 is recommended, otherwise v2 is fitted against a
  distorted output stage.
- Two engines coexist until v2 is trusted. That is the accepted cost of the rollout decision.
- v2 depends on firmware v2 (`S` command). Flashing is done and verified; a rollback of the
  firmware would require `HUMAN_MOVE_V2 = False`.

## Provenance

Analysis scripts live in the session scratchpad (`compare_mouse.py`, `compare_mouse2.py`,
`refit_clicks.py`, `refit_v2.py`, `gen_groundtruth.py`, `validate_extractor.py`). `refit_v2.py`
and the two validation scripts are the ones that matter -- the earlier two use the rejected
gap-based segmentation and are kept only to show what was tried. They are throwaway but should be copied into the repo alongside the
implementation, since every number here is reproducible only through them and they are the
verification harness.
