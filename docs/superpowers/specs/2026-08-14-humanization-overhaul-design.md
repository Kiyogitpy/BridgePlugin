# Humanization overhaul design

## Context

A hypothetical "what would an OSRS anti-cheat engineer look at" discussion (2026-08-14) surfaced
six detection vectors, informed by reading two real packet-injection bot codebases
(`data/decompiled/PacketUtils/`, and a read-only review of an external, unrelated, "recently
detected" project at `ZombieLite/src/main/java/com/salvation/packets`):

1. Click coordinates piling up at literal repeat points instead of scattering naturally.
2. Jitter/delay distributions being too clean/reproducible (fixed formula, same shape forever).
3. Cross-session/cross-account statistical fingerprinting from a deterministic formula shared
   across many accounts.
4. Packet/action timing burstiness vs. ambient noise.
5. Cursor path plausibility (overshoot/deceleration/curvature matching real recorded motion).
6. Action-to-outcome reaction-latency distributions being uniform/bounded instead of
   fat-tailed/attention-dependent.

Checking this project's actual state against those six vectors found it's already fairly
sophisticated: `pick_aim_point` does Gaussian jitter with a soft (non-hard-edge) tail,
`build_bezier_path`/`ease_in_out` already model curvature and a real recorded speed profile
(peak near t≈1/3, not a symmetric bell, per that function's own provenance comment), and there's
already overshoot simulation plus misclick-and-reclick. Vectors #1, #3, and #4 don't meaningfully
apply here — this is a single physical Arduino-driven mouse, not shared packet-injection code run
across a fleet of accounts, so there's no cross-account fingerprint or packet-burstiness gap to
close.

What's left, and confirmed real: vector #6 is pervasive — **101 call sites across 15 files**
(`controller.py`, `banker.py`, `travel.py`, `login.py`, `sailing.py`, and 10 files under
`routines/`) use `time.sleep(random.uniform(a, b))` for reaction/action delays, a bounded uniform
with a hard ceiling no real human reaction time has. Vector #2/#5 partially applies: the aim-jitter
and curve-shape functions use *fixed* formula constants — over a long session that's itself a
signature, independent of how well-shaped any single draw is.

## Goal

Close the confirmed delay-distribution gap (uniform → fat-tailed, no hard ceiling) and the
flagged fixed-formula gap (aim jitter / curve magnitude drift over a session), without touching
what's already working (`ease_in_out`'s CDF shape, `estimate_duration_ms`'s tuning, overshoot
logic, misclick/reclick logic) — those are directly grounded in a prior real click-recording
session and are out of scope for this pass.

## Design

### `HumanProfile`

A small dataclass, created once at process start and lazily re-rolled during a run:

```python
@dataclass
class HumanProfile:
    aim_sigma_factor: float      # replaces pick_aim_point's fixed radius/1.5
    curve_mag_low: float         # replaces build_bezier_path's fixed 0.08
    curve_mag_high: float        # replaces build_bezier_path's fixed 0.20
    delay_skew: float            # shape param feeding human_delay's lognormal
    rerolled_at: float           # monotonic timestamp of last reroll
```

`get_human_profile()` returns the current profile, transparently re-rolling it once
`time.monotonic() - rerolled_at` exceeds a randomized interval drawn fresh each reroll (e.g.
`Uniform(10, 20)` minutes — the reroll cadence itself isn't a fixed period, so it doesn't just
relocate the fixed-formula problem one level up). Each knob re-rolls independently, within a
tuned range centered on today's existing constants (`aim_sigma_factor` around 1.5, `curve_mag`
range around 0.08–0.20) — narrow enough that any single draw feels like current behavior, wide
enough that the *aggregate* distribution across a long session isn't one fixed formula.

`pick_aim_point`, `build_bezier_path`, and the new `human_delay` all pull their working
parameters from `get_human_profile()` instead of hardcoded constants. Their internal shapes
(Gaussian-with-soft-tail, CDF-based curve, overshoot logic) are unchanged.

### `human_delay()`

Drop-in replacement for `time.sleep(random.uniform(a, b))`:

```python
def human_delay(min_s: float, max_s: float) -> None:
    time.sleep(sample_human_delay(min_s, max_s))

def sample_human_delay(min_s: float, max_s: float) -> float:
    mid = (min_s + max_s) / 2.0
    profile = get_human_profile()
    sigma = 0.35 + 0.15 * profile.delay_skew
    mu = math.log(max(mid, 1e-3))
    draw = random.lognormvariate(mu, sigma)
    # Soft floor near min_s (real reaction time has a hard physiological
    # minimum) but no hard ceiling -- unlike today's uniform(a, b), a human
    # occasionally pauses much longer than usual (distraction, re-reading a
    # dialog); the lognormal tail models that instead of clamping it away.
    return max(draw, min_s * 0.6)
```

Median tracks the existing tuned `(a, b)` range so call sites don't need bound retuning; shape
(`sigma`) drifts per-`HumanProfile` rather than being one constant everywhere.

### Integration into `pick_aim_point` / `build_bezier_path`

Minimal-diff: only the fixed constants become profile reads.

```python
def pick_aim_point(x: int, y: int, radius: int) -> tuple:
    if radius <= 0:
        return x, y
    profile = get_human_profile()
    sigma = radius / profile.aim_sigma_factor   # was: radius / 1.5
    ...  # unchanged: Gaussian draw + 2.5x soft clamp
```

```python
def build_bezier_path(start: tuple, end: tuple) -> Bezier:
    ...
    profile = get_human_profile()
    curve_mag = clamp(
        dist * random.uniform(profile.curve_mag_low, profile.curve_mag_high),  # was: 0.08, 0.20
        8.0, 150.0,
    )
    ...  # unchanged: curve_sign, c1_t/c2_t ranges, c2_sign reversal logic
```

`ease_in_out`'s CDF shape and `estimate_duration_ms`'s multiplier stay fixed constants — both are
directly grounded in the earlier real-click-recording session; drifting them risks pulling away
from validated values for marginal benefit. Flagged as a possible future follow-up, not part of
this pass.

### Migrating the 101 call sites

`human_delay` lives in `controller.py` alongside the other humanization primitives; every module
with a `time.sleep(random.uniform(...))` call already depends on `controller.py`, so no new import
wiring beyond adding `human_delay` to what's already imported.

Mechanical sweep across all 15 files: replace `time.sleep(random.uniform(a, b))` →
`human_delay(a, b)` at each of the 101 sites, preserving the exact `(a, b)` bounds already tuned
per call site — no bound retuning in this pass, since profile-driven skew changes the *shape*
without needing new bounds. `_reclick_after_misclick`'s `MISCLICK_RECLICK_DELAY_S` (a deliberately
short corrective-reaction delay) needs no special-casing — `human_delay`'s soft floor and
median-tracking behave correctly for any `(a, b)` pair, including short ones.

Files touched: `controller.py`, `banker.py`, `travel.py`, `login.py`, `sailing.py`,
`routines/tormented_demon.py`, `routines/slayer.py`, `routines/shipwreck_salvaging.py`,
`routines/pest_control.py`, `routines/gemstone_crab.py`, `routines/mining.py`,
`routines/firemaking.py`, `routines/mastering_mixology.py`, `routines/golem_crafting.py`,
`routines/blast_furnace.py`.

`py_compile` every touched file + run `pytest tests/` before considering the migration done,
matching the project's existing pre-relaunch discipline.

## Testing

**Monte Carlo unit tests** (`tests/test_controller.py`, same style as the existing `pick_aim_point`
distributional checks):

- `sample_human_delay`: N=10,000 draws for a representative `(a, b)`; assert median near
  `(a+b)/2` within tolerance, assert some fraction of draws exceed `b` (tail isn't hard-clamped),
  assert none fall below `min_s * 0.6`, assert the sample is measurably non-uniform (skewness
  check) — the regression guard against silently reverting to uniform-shaped behavior.
- `HumanProfile` reroll: force `rerolled_at` into the past, call `get_human_profile()` twice,
  assert the knobs changed and `rerolled_at` advanced; call twice in immediate succession, assert
  no reroll happens (no reroll-per-call bug).
- `pick_aim_point`/`build_bezier_path` with profile drift: re-run existing distributional tests
  across multiple forced profile rerolls, assert aggregate spread across profiles is wider than
  any single profile's spread alone.

**Regression guard**: existing 24 tests in `test_controller.py` must keep passing unchanged;
`HumanProfile`'s default/first-roll values should reproduce close to today's fixed constants so
no existing test's tolerance breaks.

**Live verification**: after migration + `py_compile`/`pytest` pass, launch `pest_control.py` in
the background per the project's standard live-debugging workflow, watch a few delay-gated
actions fire, confirm nothing stalls unreasonably long. Occasional longer-than-usual pauses (the
no-hard-ceiling tail) are expected, not a bug — but should stay rare and bounded-looking.

## Out of scope

- Real-sample recording/fitting pipeline — staying formula-based; existing constants already
  trace back to a prior live-recording session, new drift ranges are centered on those.
- `ease_in_out` / `estimate_duration_ms` drift — held fixed, both grounded in that recording
  session.
- Two-tier reaction-vs-pacing delay classification — every one of the 101 sites gets the same
  `human_delay` treatment; splitting them buys little safety (any deterministic-formula timing is
  a signature regardless of what it represents) for real added classification work.
- Correlated persona axes — `HumanProfile`'s knobs drift independently, not jointly; YAGNI for a
  single-user tool rather than a shared botnet where independent-axis drift already defeats the
  fixed-formula concern.
- Cross-account/packet-burstiness mitigations (vectors #3/#4) — not applicable; single physical
  Arduino, not shared injected code across accounts.
- Click-coordinate repeat-point piling (vector #1) — not applicable; `pick_aim_point` already
  jitters per click, this was specific to the reviewed bot's no-arg `queueClickPacket()` default.
