# Humanization Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close two anti-detection gaps in the Controller project's humanization code: (1) 101
call sites across 15 files use `time.sleep(random.uniform(a, b))` for delays — a bounded uniform
with a hard ceiling no real human reaction time has; (2) `pick_aim_point` and `build_bezier_path`
use fixed formula constants for the whole process lifetime, which is itself a statistical
signature over a long session.

**Architecture:** A new `HumanProfile` dataclass in `controller.py` holds session-level knobs
(aim jitter spread, curve magnitude range, delay skew) that re-roll on a randomized interval. A
new `human_delay(min_s, max_s)` function (fat-tailed, no hard ceiling, drop-in replacement for
`time.sleep(random.uniform(a, b))`) and the existing `pick_aim_point`/`build_bezier_path` all
read their parameters from `get_human_profile()` instead of hardcoded constants. All 101 call
sites are then mechanically migrated to `human_delay`.

**Tech Stack:** Python 3 (`ControllerAddon/`), `pytest` for tests, no new dependencies.

## Global Constraints

- Every existing test in `tests/test_controller.py` (24 tests as of this plan) must keep passing
  unchanged — `HumanProfile`'s knob ranges are centered on today's fixed constants specifically
  so default/typical behavior doesn't shift enough to break existing tolerances.
- `py_compile` every touched file before running `pytest`, per this project's standard
  pre-relaunch discipline (see `CLAUDE.md`'s "Live-debugging workflow" section) — some modules
  (e.g. `banker.py`) aren't imported by any test, so a syntax error there won't surface in
  `pytest` alone.
- Preserve every call site's existing `(a, b)` bounds exactly during migration — no bound
  retuning in this pass; `human_delay`'s shape change alone (uniform → fat-tailed) is the fix,
  not new bounds.
- Spec: `docs/superpowers/specs/2026-08-14-humanization-overhaul-design.md`.

---

## File Structure

- **`ControllerAddon/settings.py`** — modified: new `# ---------------- Humanization profile
  drift ----------------` section with 5 new tunable range constants.
- **`ControllerAddon/controller.py`** — modified: new `HumanProfile` dataclass, `_roll_human_profile`,
  `get_human_profile`, `sample_human_delay`, `human_delay` (inserted between the existing
  `clear_movement_abort` and `pick_aim_point`); `pick_aim_point`/`build_bezier_path` modified to
  read from the profile instead of fixed constants; 22 of its own delay call sites migrated.
- **`ControllerAddon/tests/test_controller.py`** — modified: new tests for profile rerolling,
  `sample_human_delay`'s distribution shape, and the aim/curve knobs actually affecting output.
- **`ControllerAddon/banker.py`, `travel.py`, `login.py`, `sailing.py`** — modified: delay call
  sites migrated.
- **`ControllerAddon/routines/{tormented_demon,slayer,shipwreck_salvaging,pest_control,
  gemstone_crab,mining,firemaking,mastering_mixology,golem_crafting,blast_furnace}.py`** —
  modified: delay call sites migrated.

No new files. No files deleted.

---

### Task 1: `HumanProfile` dataclass + settings constants

**Files:**
- Modify: `ControllerAddon/settings.py` (add new section after the "Moving-target re-aiming"
  section, before "Human mistake simulation")
- Modify: `ControllerAddon/controller.py:942-953` (insert between `clear_movement_abort` and
  `pick_aim_point`)
- Test: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `controller.HumanProfile` (dataclass: `aim_sigma_factor: float, curve_mag_low: float,
  curve_mag_high: float, delay_skew: float, rerolled_at: float, reroll_after_s: float`),
  `controller.get_human_profile() -> HumanProfile`. Task 2/3/4 consume `get_human_profile()`.

- [ ] **Step 1: Add the new settings constants**

In `ControllerAddon/settings.py`, find:

```python
# ---------------- Moving-target re-aiming ----------------
MOVING_TARGET_MIN_DIST = 60             # px; shorter moves finish too fast to bother re-aiming
MOVING_TARGET_MIDFLIGHT_FRACTION = 0.6  # fly this far toward the stale point before re-aiming

# ---------------- Human mistake simulation ----------------
```

Replace with:

```python
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
```

- [ ] **Step 2: Add the `HumanProfile` dataclass and rerolling logic to `controller.py`**

Find (`controller.py`, currently around line 949-953):

```python
def clear_movement_abort():
    _movement_abort.clear()


def pick_aim_point(x: int, y: int, radius: int) -> tuple:
```

Replace with:

```python
def clear_movement_abort():
    _movement_abort.clear()


# ---------------- Humanization profile (session-level parameter drift) ----------------
@dataclass
class HumanProfile:
    aim_sigma_factor: float
    curve_mag_low: float
    curve_mag_high: float
    delay_skew: float
    rerolled_at: float
    reroll_after_s: float


def _roll_human_profile() -> "HumanProfile":
    return HumanProfile(
        aim_sigma_factor=random.uniform(*AIM_SIGMA_FACTOR_RANGE),
        curve_mag_low=random.uniform(*CURVE_MAG_LOW_RANGE),
        curve_mag_high=random.uniform(*CURVE_MAG_HIGH_RANGE),
        delay_skew=random.uniform(*DELAY_SKEW_RANGE),
        rerolled_at=time.monotonic(),
        reroll_after_s=random.uniform(*PROFILE_REROLL_MINUTES_RANGE) * 60.0,
    )


_human_profile: "HumanProfile | None" = None


def get_human_profile() -> "HumanProfile":
    global _human_profile
    if _human_profile is None or (
        time.monotonic() - _human_profile.rerolled_at >= _human_profile.reroll_after_s
    ):
        _human_profile = _roll_human_profile()
    return _human_profile


def pick_aim_point(x: int, y: int, radius: int) -> tuple:
```

- [ ] **Step 3: Write the failing tests**

Add to `ControllerAddon/tests/test_controller.py` (near the top, `import dataclasses` alongside
the existing `import os` / `import sys`):

```python
import dataclasses
```

Then add these tests (anywhere after the existing tests, e.g. at the end of the file):

```python
def test_get_human_profile_returns_same_profile_until_interval_elapses(monkeypatch):
    monkeypatch.setattr(controller, "_human_profile", None)

    first = controller.get_human_profile()
    again = controller.get_human_profile()

    assert again is first


def test_get_human_profile_rerolls_after_interval_elapses(monkeypatch):
    monkeypatch.setattr(controller, "_human_profile", None)
    first = controller.get_human_profile()

    stale = dataclasses.replace(
        first, rerolled_at=first.rerolled_at - (first.reroll_after_s + 1.0)
    )
    monkeypatch.setattr(controller, "_human_profile", stale)

    second = controller.get_human_profile()

    assert second is not stale
    assert second.rerolled_at > stale.rerolled_at
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_controller.py -k human_profile -v`
Expected: FAIL with `AttributeError: module 'controller' has no attribute 'get_human_profile'`
(or `HumanProfile`) — confirms the test can't pass before Steps 1-2 land, and will after.

Since Steps 1-2 are written above already, apply them now if you haven't, then re-run:

Run: `cd ControllerAddon && python -m pytest tests/test_controller.py -k human_profile -v`
Expected: PASS (2 passed)

- [ ] **Step 5: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile settings.py controller.py tests/test_controller.py
python -m pytest tests/ -v
```
Expected: all compile cleanly, all tests pass (24 existing + 2 new = 26).

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/settings.py ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "Add HumanProfile session-level drift for aim/curve/delay parameters"
```

---

### Task 2: `human_delay()` / `sample_human_delay()`

**Files:**
- Modify: `ControllerAddon/controller.py` (insert immediately after `get_human_profile`, before
  `pick_aim_point`)
- Test: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Consumes: `controller.get_human_profile() -> HumanProfile` (Task 1).
- Produces: `controller.sample_human_delay(min_s: float, max_s: float) -> float`,
  `controller.human_delay(min_s: float, max_s: float) -> None`. All migration tasks (5-12)
  consume `human_delay`.

- [ ] **Step 1: Write the failing tests**

Add to `ControllerAddon/tests/test_controller.py`:

```python
def test_sample_human_delay_median_tracks_midpoint_with_soft_floor():
    controller.random.seed(1234)
    samples = sorted(controller.sample_human_delay(1.0, 2.0) for _ in range(10000))

    median = samples[len(samples) // 2]
    assert 1.3 < median < 1.7  # tracks (1.0+2.0)/2 == 1.5

    assert min(samples) >= 1.0 * 0.6 - 1e-9  # soft floor, not min_s itself


def test_sample_human_delay_has_no_hard_ceiling_unlike_uniform():
    controller.random.seed(5678)
    samples = [controller.sample_human_delay(1.0, 2.0) for _ in range(10000)]

    over_ceiling = sum(1 for s in samples if s > 2.0)
    assert over_ceiling > 1000  # meaningfully non-zero; uniform(1,2) would give exactly 0


def test_sample_human_delay_is_not_uniformly_distributed():
    controller.random.seed(9012)
    samples = [controller.sample_human_delay(1.0, 2.0) for _ in range(10000)]

    mean = sum(samples) / len(samples)
    variance = sum((s - mean) ** 2 for s in samples) / len(samples)
    # uniform(1, 2)'s variance is (2-1)**2/12 ~= 0.083; the fat-tailed shape
    # should sit well above that regardless of the current delay_skew draw.
    assert variance > 0.15


def test_human_delay_sleeps_for_the_sampled_duration(monkeypatch):
    slept = []
    monkeypatch.setattr(controller.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 1.23)

    controller.human_delay(1.0, 2.0)

    assert slept == [1.23]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_controller.py -k sample_human_delay or test_human_delay -v`
Expected: FAIL with `AttributeError: module 'controller' has no attribute 'sample_human_delay'`

- [ ] **Step 3: Implement**

Find (`controller.py`, right after `get_human_profile`'s closing `return _human_profile` and
before `def pick_aim_point`):

```python
def get_human_profile() -> "HumanProfile":
    global _human_profile
    if _human_profile is None or (
        time.monotonic() - _human_profile.rerolled_at >= _human_profile.reroll_after_s
    ):
        _human_profile = _roll_human_profile()
    return _human_profile


def pick_aim_point(x: int, y: int, radius: int) -> tuple:
```

Replace with:

```python
def get_human_profile() -> "HumanProfile":
    global _human_profile
    if _human_profile is None or (
        time.monotonic() - _human_profile.rerolled_at >= _human_profile.reroll_after_s
    ):
        _human_profile = _roll_human_profile()
    return _human_profile


def sample_human_delay(min_s: float, max_s: float) -> float:
    # Fat right tail instead of random.uniform's hard ceiling -- a real human occasionally
    # pauses much longer than usual (distraction, re-reading a dialog). Median still tracks
    # (min_s, max_s) so existing call sites don't need bound retuning; only the shape changes.
    # See docs/superpowers/specs/2026-08-14-humanization-overhaul-design.md.
    mid = (min_s + max_s) / 2.0
    profile = get_human_profile()
    sigma = 0.35 + 0.15 * profile.delay_skew
    mu = math.log(max(mid, 1e-3))
    draw = random.lognormvariate(mu, sigma)
    # Soft floor -- real reaction time has a hard physiological minimum -- but no hard ceiling.
    return max(draw, min_s * 0.6)


def human_delay(min_s: float, max_s: float) -> None:
    """Drop-in replacement for time.sleep(random.uniform(min_s, max_s)) -- same call shape,
    fat-tailed instead of uniform. See sample_human_delay for the distribution."""
    time.sleep(sample_human_delay(min_s, max_s))


def pick_aim_point(x: int, y: int, radius: int) -> tuple:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ControllerAddon && python -m pytest tests/test_controller.py -k "sample_human_delay or test_human_delay" -v`
Expected: PASS (4 passed)

- [ ] **Step 5: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile controller.py tests/test_controller.py
python -m pytest tests/ -v
```
Expected: all pass (26 existing + 4 new = 30).

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "Add human_delay(): fat-tailed replacement for time.sleep(random.uniform())"
```

---

### Task 3: Wire `HumanProfile` into `pick_aim_point`

**Files:**
- Modify: `ControllerAddon/controller.py:953-976` (the existing `pick_aim_point` function)
- Test: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Consumes: `controller.get_human_profile()` (Task 1).
- Produces: `pick_aim_point`'s jitter spread now varies with `HumanProfile.aim_sigma_factor`
  instead of a fixed `1.5`. No signature change.

- [ ] **Step 1: Write the failing test**

```python
def test_pick_aim_point_jitter_spread_follows_profile_aim_sigma_factor(monkeypatch):
    def make_profile(factor):
        return controller.HumanProfile(
            aim_sigma_factor=factor, curve_mag_low=0.08, curve_mag_high=0.20,
            delay_skew=0.5, rerolled_at=0.0, reroll_after_s=1e9,
        )

    def variance(samples):
        mean = sum(samples) / len(samples)
        return sum((s - mean) ** 2 for s in samples) / len(samples)

    controller.random.seed(1)
    monkeypatch.setattr(controller, "get_human_profile", lambda: make_profile(1.8))
    tight = [
        math.hypot(*controller.pick_aim_point(0, 0, 10)) for _ in range(5000)
    ]

    controller.random.seed(1)
    monkeypatch.setattr(controller, "get_human_profile", lambda: make_profile(1.3))
    wide = [
        math.hypot(*controller.pick_aim_point(0, 0, 10)) for _ in range(5000)
    ]

    # Same seed sequence, only the profile's sigma factor differs -- a smaller
    # factor means a larger sigma (radius / factor) and should measurably widen
    # the spread of magnitudes (confirmed empirically: ~1.9x variance ratio
    # between factor=1.3 and factor=1.8 at radius=10).
    assert variance(wide) > variance(tight) * 1.3
```

`tests/test_controller.py` does not currently import `math` — add `import math` alongside the
`import dataclasses` line added in Task 1, Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_controller.py -k jitter_spread -v`
Expected: FAIL (assertion fails — variance ratio ~1.0 since `pick_aim_point` still uses the
fixed `radius / 1.5`, unaffected by the monkeypatched profile).

- [ ] **Step 3: Implement**

Find (`controller.py`, current `pick_aim_point` body):

```python
    sigma = radius / 1.5
    dx = random.gauss(0, sigma)
```

Replace with:

```python
    profile = get_human_profile()
    sigma = radius / profile.aim_sigma_factor
    dx = random.gauss(0, sigma)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_controller.py -k jitter_spread -v`
Expected: PASS

- [ ] **Step 5: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile controller.py tests/test_controller.py
python -m pytest tests/ -v
```
Expected: all pass (30 existing + 1 new = 31). This is also the first point where a change to
`pick_aim_point` could disturb an *existing* test (e.g. any test relying on `pick_aim_point`'s
exact jitter magnitude) — if anything existing breaks, check whether it hardcoded the old fixed
`1.5` factor and adjust that test's tolerance rather than reverting this change.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "Wire HumanProfile.aim_sigma_factor into pick_aim_point"
```

---

### Task 4: Wire `HumanProfile` into `build_bezier_path`

**Files:**
- Modify: `ControllerAddon/controller.py:872-909` (the existing `build_bezier_path` function)
- Test: `ControllerAddon/tests/test_controller.py`

**Interfaces:**
- Consumes: `controller.get_human_profile()` (Task 1).
- Produces: `build_bezier_path`'s curve magnitude range now varies with
  `HumanProfile.curve_mag_low`/`curve_mag_high` instead of fixed `0.08`/`0.20`. No signature
  change.

- [ ] **Step 1: Write the failing test**

```python
def test_build_bezier_path_curve_magnitude_follows_profile_range(monkeypatch):
    def make_profile(low, high):
        return controller.HumanProfile(
            aim_sigma_factor=1.5, curve_mag_low=low, curve_mag_high=high,
            delay_skew=0.5, rerolled_at=0.0, reroll_after_s=1e9,
        )

    def mean_c1_offset(samples):
        return sum(samples) / len(samples)

    start, end = (0, 0), (400, 0)

    controller.random.seed(2)
    monkeypatch.setattr(controller, "get_human_profile", lambda: make_profile(0.08, 0.08))
    low_offsets = []
    for _ in range(2000):
        bez = controller.build_bezier_path(start, end)
        # c1 sits at c1_t along the straight line, plus a perpendicular offset --
        # isolate that offset by comparing against the line's own y at that x.
        low_offsets.append(abs(bez.p1[1]))

    controller.random.seed(2)
    monkeypatch.setattr(controller, "get_human_profile", lambda: make_profile(0.20, 0.20))
    high_offsets = []
    for _ in range(2000):
        bez = controller.build_bezier_path(start, end)
        high_offsets.append(abs(bez.p1[1]))

    # Same seed sequence, only the profile's curve_mag range differs -- pinning
    # it to 0.20 instead of 0.08 should produce a visibly larger average
    # perpendicular offset (confirmed empirically: ~2.5x on a 400px move).
    assert mean_c1_offset(high_offsets) > mean_c1_offset(low_offsets) * 1.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_controller.py -k curve_magnitude -v`
Expected: FAIL (ratio ~1.0 — `build_bezier_path` still uses the fixed `0.08`/`0.20` range,
unaffected by the monkeypatched profile).

- [ ] **Step 3: Implement**

Find (`controller.py`, current `build_bezier_path` body):

```python
    curve_mag = clamp(dist * random.uniform(0.08, 0.20), 8.0, 150.0)
```

Replace with:

```python
    profile = get_human_profile()
    curve_mag = clamp(dist * random.uniform(profile.curve_mag_low, profile.curve_mag_high), 8.0, 150.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ControllerAddon && python -m pytest tests/test_controller.py -k curve_magnitude -v`
Expected: PASS

- [ ] **Step 5: `py_compile` + full regression suite**

Run:
```
cd ControllerAddon
python -m py_compile controller.py tests/test_controller.py
python -m pytest tests/ -v
```
Expected: all pass (31 existing + 1 new = 32). This is the last foundational task — if any
pre-existing test unrelated to this plan breaks here or in Task 3, stop and diagnose before
moving to migration; every later task builds on `pick_aim_point`/`build_bezier_path`/
`human_delay` all being correct.

- [ ] **Step 6: Commit**

```bash
git add ControllerAddon/controller.py ControllerAddon/tests/test_controller.py
git commit -m "Wire HumanProfile.curve_mag_low/high into build_bezier_path"
```

---

## Migration tasks (5-12)

All eight tasks below follow the same mechanical pattern: a Python regex substitution replaces
`time.sleep(random.uniform(ARGS))` with `human_delay(ARGS)` (controller.py) or
`controller.human_delay(ARGS)` (every other file, which already calls other controller.py
functions via `controller.xxx(...)` — confirmed by grepping each file's existing call style).
The regex `time\.sleep\(random\.uniform\(([^()]*)\)\)` is safe here because none of the 101 call
sites nest parentheses inside their `random.uniform(...)` arguments (verified by inspection —
they're either two numeric literals or, in one case, a `*TUPLE_CONSTANT` splat); any trailing
`# comment` on the same line sits after the match and is left untouched.

Each task's script asserts the exact expected replacement count before writing the file, so a
mismatch (e.g. a call site this plan didn't account for) fails loudly instead of silently
under- or over-replacing.

### Task 5: Migrate `controller.py` (22 call sites)

**Files:**
- Modify: `ControllerAddon/controller.py`

**Interfaces:**
- Consumes: `controller.human_delay` (Task 2, same-module so called bare, no `controller.` prefix).
- Produces: no interface change — internal call sites only.

- [ ] **Step 1: Run the migration script**

```bash
cd ControllerAddon
python -c "
import re
path = 'controller.py'
with open(path, encoding='utf-8') as f:
    content = f.read()
pattern = re.compile(r'time\.sleep\(random\.uniform\(([^()]*)\)\)')
new_content, count = pattern.subn(r'human_delay(\1)', content)
assert count == 22, f'expected 22 replacements in {path}, got {count}'
assert pattern.search(new_content) is None
with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)
print('OK:', count, 'replacements in', path)
"
```

Expected output: `OK: 22 replacements in controller.py`

- [ ] **Step 2: Spot-check the result**

Run: `cd ControllerAddon && grep -n "human_delay(" controller.py | head -5`
Expected: lines like `human_delay(0.045, 0.11)` — confirms the substitution shape, and that the
`sample_human_delay`/`human_delay` definitions from Task 2 (which call `time.sleep(...)`, not
`time.sleep(random.uniform(...))`) were correctly left untouched.

- [ ] **Step 3: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile controller.py
python -m pytest tests/ -v
```
Expected: all 32 tests pass — this is a pure call-shape swap, no behavior visible to any existing
test changes (existing tests that monkeypatch `time.sleep` directly still see it called, just via
`sample_human_delay`'s draw instead of `random.uniform`'s).

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/controller.py
git commit -m "Migrate controller.py's own delay call sites to human_delay"
```

### Task 6: Migrate `banker.py` (15 call sites)

**Files:**
- Modify: `ControllerAddon/banker.py`

**Interfaces:**
- Consumes: `controller.human_delay` (Task 2).
- Produces: no interface change.

- [ ] **Step 1: Run the migration script**

```bash
cd ControllerAddon
python -c "
import re
path = 'banker.py'
with open(path, encoding='utf-8') as f:
    content = f.read()
pattern = re.compile(r'time\.sleep\(random\.uniform\(([^()]*)\)\)')
new_content, count = pattern.subn(r'controller.human_delay(\1)', content)
assert count == 15, f'expected 15 replacements in {path}, got {count}'
assert pattern.search(new_content) is None
with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)
print('OK:', count, 'replacements in', path)
"
```

Expected output: `OK: 15 replacements in banker.py`

- [ ] **Step 2: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile banker.py
python -m pytest tests/ -v
```
Expected: all 32 tests pass. (`banker.py` isn't imported by any test, per `CLAUDE.md`'s note on
`debug_log.py`'s migration — `py_compile` is the only thing that will actually catch a syntax
error here.)

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/banker.py
git commit -m "Migrate banker.py's delay call sites to human_delay"
```

### Task 7: Migrate `routines/golem_crafting.py` (14 call sites)

**Files:**
- Modify: `ControllerAddon/routines/golem_crafting.py`

**Interfaces:**
- Consumes: `controller.human_delay` (Task 2).
- Produces: no interface change.

- [ ] **Step 1: Run the migration script**

```bash
cd ControllerAddon
python -c "
import re
path = 'routines/golem_crafting.py'
with open(path, encoding='utf-8') as f:
    content = f.read()
pattern = re.compile(r'time\.sleep\(random\.uniform\(([^()]*)\)\)')
new_content, count = pattern.subn(r'controller.human_delay(\1)', content)
assert count == 14, f'expected 14 replacements in {path}, got {count}'
assert pattern.search(new_content) is None
with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)
print('OK:', count, 'replacements in', path)
"
```

Expected output: `OK: 14 replacements in routines/golem_crafting.py`

- [ ] **Step 2: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile routines/golem_crafting.py
python -m pytest tests/ -v
```
Expected: all 32 tests pass.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/golem_crafting.py
git commit -m "Migrate golem_crafting.py's delay call sites to human_delay"
```

### Task 8: Migrate `routines/blast_furnace.py` (14 call sites)

**Files:**
- Modify: `ControllerAddon/routines/blast_furnace.py`

**Interfaces:**
- Consumes: `controller.human_delay` (Task 2).
- Produces: no interface change.

- [ ] **Step 1: Run the migration script**

```bash
cd ControllerAddon
python -c "
import re
path = 'routines/blast_furnace.py'
with open(path, encoding='utf-8') as f:
    content = f.read()
pattern = re.compile(r'time\.sleep\(random\.uniform\(([^()]*)\)\)')
new_content, count = pattern.subn(r'controller.human_delay(\1)', content)
assert count == 14, f'expected 14 replacements in {path}, got {count}'
assert pattern.search(new_content) is None
with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)
print('OK:', count, 'replacements in', path)
"
```

Expected output: `OK: 14 replacements in routines/blast_furnace.py`

- [ ] **Step 2: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile routines/blast_furnace.py
python -m pytest tests/ -v
```
Expected: all 32 tests pass.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/blast_furnace.py
git commit -m "Migrate blast_furnace.py's delay call sites to human_delay"
```

### Task 9: Migrate `routines/mastering_mixology.py` (12 call sites)

**Files:**
- Modify: `ControllerAddon/routines/mastering_mixology.py`

**Interfaces:**
- Consumes: `controller.human_delay` (Task 2).
- Produces: no interface change.

- [ ] **Step 1: Run the migration script**

```bash
cd ControllerAddon
python -c "
import re
path = 'routines/mastering_mixology.py'
with open(path, encoding='utf-8') as f:
    content = f.read()
pattern = re.compile(r'time\.sleep\(random\.uniform\(([^()]*)\)\)')
new_content, count = pattern.subn(r'controller.human_delay(\1)', content)
assert count == 12, f'expected 12 replacements in {path}, got {count}'
assert pattern.search(new_content) is None
with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)
print('OK:', count, 'replacements in', path)
"
```

Expected output: `OK: 12 replacements in routines/mastering_mixology.py`

Note: two of this file's delays are explicitly tick-paced (comments `# one game tick -- levers
resolve per-tick` and `# one game tick`, at what were lines 635 and 966) — the regex still swaps
them like any other call site, which is correct: `human_delay`'s median tracks the original
`(a, b)` bounds, so a tick-paced wait still waits roughly one tick on average, just with a
fat-tailed rather than uniform distribution around it, per this plan's Global Constraints (no
bound retuning).

- [ ] **Step 2: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile routines/mastering_mixology.py
python -m pytest tests/ -v
```
Expected: all 32 tests pass.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/mastering_mixology.py
git commit -m "Migrate mastering_mixology.py's delay call sites to human_delay"
```

### Task 10: Migrate `shipwreck_salvaging.py`, `gemstone_crab.py`, `firemaking.py` (10 call sites)

**Files:**
- Modify: `ControllerAddon/routines/shipwreck_salvaging.py` (4 sites)
- Modify: `ControllerAddon/routines/gemstone_crab.py` (3 sites)
- Modify: `ControllerAddon/routines/firemaking.py` (3 sites)

**Interfaces:**
- Consumes: `controller.human_delay` (Task 2).
- Produces: no interface change.

- [ ] **Step 1: Run the migration script**

```bash
cd ControllerAddon
python -c "
import re
pattern = re.compile(r'time\.sleep\(random\.uniform\(([^()]*)\)\)')
targets = {
    'routines/shipwreck_salvaging.py': 4,
    'routines/gemstone_crab.py': 3,
    'routines/firemaking.py': 3,
}
for path, expected in targets.items():
    with open(path, encoding='utf-8') as f:
        content = f.read()
    new_content, count = pattern.subn(r'controller.human_delay(\1)', content)
    assert count == expected, f'expected {expected} replacements in {path}, got {count}'
    assert pattern.search(new_content) is None
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('OK:', count, 'replacements in', path)
"
```

Expected output:
```
OK: 4 replacements in routines/shipwreck_salvaging.py
OK: 3 replacements in routines/gemstone_crab.py
OK: 3 replacements in routines/firemaking.py
```

- [ ] **Step 2: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile routines/shipwreck_salvaging.py routines/gemstone_crab.py routines/firemaking.py
python -m pytest tests/ -v
```
Expected: all 32 tests pass.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/shipwreck_salvaging.py ControllerAddon/routines/gemstone_crab.py ControllerAddon/routines/firemaking.py
git commit -m "Migrate shipwreck_salvaging/gemstone_crab/firemaking delay call sites to human_delay"
```

### Task 11: Migrate `slayer.py`, `pest_control.py`, `login.py` (8 call sites)

**Files:**
- Modify: `ControllerAddon/routines/slayer.py` (3 sites)
- Modify: `ControllerAddon/routines/pest_control.py` (3 sites)
- Modify: `ControllerAddon/login.py` (2 sites)

**Interfaces:**
- Consumes: `controller.human_delay` (Task 2).
- Produces: no interface change.

- [ ] **Step 1: Run the migration script**

```bash
cd ControllerAddon
python -c "
import re
pattern = re.compile(r'time\.sleep\(random\.uniform\(([^()]*)\)\)')
targets = {
    'routines/slayer.py': 3,
    'routines/pest_control.py': 3,
    'login.py': 2,
}
for path, expected in targets.items():
    with open(path, encoding='utf-8') as f:
        content = f.read()
    new_content, count = pattern.subn(r'controller.human_delay(\1)', content)
    assert count == expected, f'expected {expected} replacements in {path}, got {count}'
    assert pattern.search(new_content) is None
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('OK:', count, 'replacements in', path)
"
```

Expected output:
```
OK: 3 replacements in routines/slayer.py
OK: 3 replacements in routines/pest_control.py
OK: 2 replacements in login.py
```

- [ ] **Step 2: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile routines/slayer.py routines/pest_control.py login.py
python -m pytest tests/ -v
```
Expected: all 32 tests pass.

- [ ] **Step 3: Commit**

```bash
git add ControllerAddon/routines/slayer.py ControllerAddon/routines/pest_control.py ControllerAddon/login.py
git commit -m "Migrate slayer/pest_control/login delay call sites to human_delay"
```

### Task 12: Migrate `travel.py`, `sailing.py`, `tormented_demon.py`, `mining.py` (6 call sites)

**Files:**
- Modify: `ControllerAddon/travel.py` (3 sites)
- Modify: `ControllerAddon/sailing.py` (1 site)
- Modify: `ControllerAddon/routines/tormented_demon.py` (1 site)
- Modify: `ControllerAddon/routines/mining.py` (1 site)

**Interfaces:**
- Consumes: `controller.human_delay` (Task 2).
- Produces: no interface change. This is the last migration task — after this, zero
  `time.sleep(random.uniform(` occurrences should remain anywhere in `ControllerAddon/`.

- [ ] **Step 1: Run the migration script**

```bash
cd ControllerAddon
python -c "
import re
pattern = re.compile(r'time\.sleep\(random\.uniform\(([^()]*)\)\)')
targets = {
    'travel.py': 3,
    'sailing.py': 1,
    'routines/tormented_demon.py': 1,
    'routines/mining.py': 1,
}
for path, expected in targets.items():
    with open(path, encoding='utf-8') as f:
        content = f.read()
    new_content, count = pattern.subn(r'controller.human_delay(\1)', content)
    assert count == expected, f'expected {expected} replacements in {path}, got {count}'
    assert pattern.search(new_content) is None
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('OK:', count, 'replacements in', path)
"
```

Expected output:
```
OK: 3 replacements in travel.py
OK: 1 replacements in sailing.py
OK: 1 replacements in routines/tormented_demon.py
OK: 1 replacements in routines/mining.py
```

- [ ] **Step 2: Verify zero occurrences remain project-wide**

Run: `cd ControllerAddon && grep -rn "time.sleep(random.uniform(" . --include=*.py`
Expected: no output (empty) — confirms all 101 call sites across all 15 files are migrated.

- [ ] **Step 3: `py_compile` + full test suite**

Run:
```
cd ControllerAddon
python -m py_compile travel.py sailing.py routines/tormented_demon.py routines/mining.py
python -m pytest tests/ -v
```
Expected: all 32 tests pass.

- [ ] **Step 4: Commit**

```bash
git add ControllerAddon/travel.py ControllerAddon/sailing.py ControllerAddon/routines/tormented_demon.py ControllerAddon/routines/mining.py
git commit -m "Migrate travel/sailing/tormented_demon/mining delay call sites to human_delay"
```

---

### Task 13: Live verification

**Files:** none modified — verification only.

**Interfaces:**
- Consumes: everything from Tasks 1-12, running live against the RuneLite client.
- Produces: confirmation the overhaul works outside the test suite (or a bug report if not).

- [ ] **Step 1: Check for a stray process holding the Arduino serial port**

Run: `Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"`
Expected: no leftover `controller.py` process from a previous session. If one is found, do not
kill it without checking with the user first — it may be in-progress work.

- [ ] **Step 2: Launch `pest_control.py` in the background, logged to a file**

Run (from `ControllerAddon/`):
```
python -u controller.py routines/pest_control.py *> ../pest_control_humanization_test.log 2>&1
```
Run this with `run_in_background: true` (or the equivalent for whatever tool is executing this
plan) — it runs until the panic key (F1), a safety stop, or the process is killed.

- [ ] **Step 3: Poll the log and watch for delay-gated actions**

Poll `pest_control_humanization_test.log` every 20-90s (per `CLAUDE.md`'s live-debugging
workflow — this is a live session, not a race). Specifically watch the gangplank-boarding delay
(`dock_tick`, originally `human_delay(0.2, 10.0)`) and the minimap step-forward delay
(`step_forward_blind`, originally `human_delay(1.0, 5.0)`) actually fire with plausible-looking
wait times — not stalling for an implausibly long time (the no-hard-ceiling tail means
occasional longer-than-usual pauses are expected, but should stay rare, not the norm) and not
breaking the routine's timing-sensitive logic (e.g. `GANGPLANK_CLICK_COOLDOWN_S` gating).

- [ ] **Step 4: Confirm with the user before concluding**

Per `CLAUDE.md`'s live-debugging workflow: trust the user's live observations over your own read
of the log. Ask the user to confirm the routine still looks and behaves normally before
considering this task (and the whole plan) done.

- [ ] **Step 5: Clean up**

Stop the routine (panic key or process kill, per how the session was ended), and note the result
in project memory if anything unexpected came up during live verification.

---

### Task 14: Clean up dead imports left by migration

Added during execution (2026-08-14): Task 8's reviewer flagged that migrating
`time.sleep(random.uniform(a, b))` to `human_delay(a, b)` leaves `import random` dead in any file
where `random.uniform` was that file's only use of the `random` module — already confirmed true
for `banker.py`, `routines/golem_crafting.py`, and `routines/blast_furnace.py` (Tasks 6-8), and
possibly true for some of Tasks 9-12's files too. The plan's migration scripts deliberately only
touch call sites, not imports, so this was out of scope for Tasks 5-12 individually. Human
decision (asked mid-execution): defer to a single cleanup pass here rather than fixing per-task
or leaving it for the final whole-branch review to flag.

**Files:**
- Modify (conditionally, only where dead): `ControllerAddon/banker.py`,
  `ControllerAddon/routines/golem_crafting.py`, `ControllerAddon/routines/blast_furnace.py`, and
  any other file touched by Tasks 5-12 where the same pattern turns out to apply.

**Interfaces:**
- Consumes: the final state of all files touched by Tasks 5-12.
- Produces: no interface change — import cleanup only.

- [ ] **Step 1: Identify every file with a now-dead `import random`**

Run (from `ControllerAddon/`):
```bash
python -c "
import re
files = [
    'controller.py', 'banker.py', 'travel.py', 'login.py', 'sailing.py',
    'routines/tormented_demon.py', 'routines/slayer.py', 'routines/shipwreck_salvaging.py',
    'routines/pest_control.py', 'routines/gemstone_crab.py', 'routines/mining.py',
    'routines/firemaking.py', 'routines/mastering_mixology.py', 'routines/golem_crafting.py',
    'routines/blast_furnace.py',
]
for path in files:
    with open(path, encoding='utf-8') as f:
        content = f.read()
    if not re.search(r'^import random$', content, re.MULTILINE):
        continue
    # Every use of `random.` outside the import line itself.
    uses = re.findall(r'(?<!import )\brandom\.\w+', content)
    if not uses:
        print('DEAD:', path)
    else:
        print('LIVE:', path, '-', len(uses), 'use(s)')
"
```

Note: `controller.py` itself is expected to print `LIVE` — it still uses `random.gauss`,
`random.uniform`, `random.choice`, `random.lognormvariate` etc. directly inside
`pick_aim_point`/`build_bezier_path`/`sample_human_delay`/misclick jitter, none of which this
plan's migration touched.

- [ ] **Step 2: Remove the dead `import random` line from each file printed as `DEAD`**

For each such file, delete the `import random` line. Use the Edit tool (or equivalent) rather
than a blanket script here — the exact surrounding import block formatting differs per file and
this is a small enough number of files (expected: `banker.py`, `routines/golem_crafting.py`,
`routines/blast_furnace.py`, plus whichever of Tasks 9-12's files also print `DEAD`) to do by
hand safely.

- [ ] **Step 3: `py_compile` every file touched in Step 2**

Run: `cd ControllerAddon && python -m py_compile <each touched file>`
Expected: all compile cleanly (removing an unused import cannot introduce a syntax error, but
this catches a mistaken over-deletion).

- [ ] **Step 4: Full test suite**

Run: `cd ControllerAddon && python -m pytest tests/ -v`
Expected: all 32 tests still pass — this task touches no runtime behavior.

- [ ] **Step 5: Re-run Step 1's detection script to confirm zero remaining `DEAD` files**

Expected output: every file that printed `DEAD` in Step 1 either no longer appears in the
scan (import removed) — confirm by re-running the Step 1 script and checking no `DEAD:` lines
remain.

- [ ] **Step 6: Commit**

```bash
git add <each file touched in Step 2>
git commit -m "Remove import random left dead by the human_delay migration (Tasks 5-12)"
```
