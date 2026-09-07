# Telemetry vector mitigation -- design

**Date:** 2026-09-07
**Status:** draft, not started
**Builds on:** CLAUDE.md "What the game client sends home" (read from the rl-suite
deobfuscation of 1.12.37 and our own 1.12.38 run), `tools/telemetry_fingerprint.py`,
`ControllerAddon/deob_guard.py`.

## 1. Problem

We now know, from the decompiled client rather than folklore, exactly which channels carry
our behaviour to the server: a login fingerprint (`PlatformInfo`), the 20 Hz cursor trajectory
(`EVENT_MOUSE_MOVE`), per-press click timing (`EVENT_MOUSE_CLICK_V2`), per-press key timing
(`EVENT_KEYBOARD`), window focus (`EVENT_APPLET_FOCUS`), camera yaw/pitch after arrow-key moves
(`EVENT_CAMERA_POSITION`), and the 5-minute idle signal. Against that list, an audit of the
controller on 2026-09-07 found nine places where our input is distinguishable from the human's
on a channel the server provably receives. Nothing on the list is a bug; each is a behaviour
we never shaped because we did not know it was observed.

This spec fixes all nine. It is organised as five sub-projects in dependency order, each
with its own acceptance test, and it is measurement-first: every behavioural change has a
metric computed the same way over a human recording and a bot recording, because the
routine's own log cannot see any of this (the fidget-anchor lesson: "a humanization that
returns to an EXACT coordinate is a tell, however human its timing is").

### The vectors, ranked

| # | Vector | Channel | Severity | Why |
|---|---|---|---|---|
| V1 | Launch shape: exe `java.exe`, parent Gradle worker `java.exe`, Temurin 11, unpinned heap | login fingerprint | **High** | Exact, static, sent on every login and every post-break relogin; the whole legitimate population reports `RuneLite.exe` |
| V2 | Cursor never leaves the canvas | mouse move (off-canvas is an explicit `-1`) | High (unmeasured) | Hours without a single excursion; a human's cursor visits the second screen, chat, taskbar |
| V3 | Poll-loop stillness (fidgets only inside `human_delay`/`fidget_once`) | mouse move | Medium | Still-time p90 never re-measured on a 30-min run; Phase B never built |
| V4 | Tick-locked click bursts (trawling, mixology retort): 600 ms +- 100 ms for minutes | click timing | Medium-High | ms-since-previous-press is on every click; a human spam-clicker is 150-400 ms and irregular |
| V5 | One SHIFT press per viewport walk click | key timing | Medium | Press rate locked 1:1 to walk clicks; a human holds SHIFT across a run or never uses it |
| V6 | Flat 50-120 ms uniform typing (bank search, PIN) | key timing | Low | Login-screen typing is NOT on the wire (no game connection yet); only in-game typing is |
| V7 | Focus never changes for hours (karambwan excepted) | focus | Medium | Every gain/loss is sent; zero events in a 6-hour session is its own shape |
| V8 | Deterministic camera setup after every login (pitch to max, exactly 3 detents) | camera | Low | The packet carries the result; identical every login |
| V9 | Bridge bound to all interfaces, no auth | not the game: LAN exposure | Real, not detection | Game state, screenshots, recorder keystrokes and bank memory to anyone on the LAN |

Not vectors, confirmed: reflection checks (RuneLite answers from the vanilla jar; we never
touch injected classes), `DETECT_MODIFIED_CLIENT` (a constant every RuneLite sends), the
bridge/UDP/serial/plugin list (in no packet).

## 2. Goals and non-goals

**Goals**
- Every vector above has a measured before/after number, with the human recording as the
  target, and a pinned unit test for the mechanism.
- No routine loses a measured throughput or a tick-perfect reaction (mixology cues, foundry
  boundary watch, gathering pre-hovers) as a side effect. Cursor-life changes must be
  opt-out-able per wait, not per routine.
- The launch-shape fix leaves the same bridge, the same ZombieLite bundle and the same
  `RoutineChoice` panel in place.

**Non-goals**
- Server-side behavioural statistics (XP/hour constancy, session length, route repetition).
  Those are routine-design questions, not input-channel ones.
- Reworking `human_move.py`'s trajectory model. The measured fits stand; this spec adds
  behaviours around them (where the cursor rests, when it leaves), not new motion.
- Anything that sends packets. The rl-suite caveat in CLAUDE.md stands.

## 3. Sub-project A -- Measure first, and close the LAN hole (no behaviour change)

Everything after A is judged by A's numbers, so A ships alone.

### A1. Recorder: canvas enter/exit and focus records
`RecorderMouseListener` gains `mouseEntered`/`mouseExited` handlers emitting `canvasenter` /
`canvasexit` records (with `t`, `tick`); `RecorderProvider` also subscribes to
`FocusChanged` and emits `focus` records (`gained: bool`). Cheap, ambient, no change to the
narrative renderer beyond two new line types.

### A2. `tools/humanity_report.py`
Pure Python over a recording's `.jsonl`. Prints one table per metric, and takes two recordings
to print them side by side (human vs bot). Metrics, each with the vector it serves:

| Metric | Definition | Vector |
|---|---|---|
| still-segment p50 / p90 / max | gaps between mouse polyline points with no click within 30 s on both sides excluded (active play only, the existing rule) | V3 |
| busy fraction | share of active time with cursor movement | V3 |
| off-canvas fraction, excursion count/hour, excursion length p50/p90 | from `canvasexit`/`canvasenter` | V2 |
| burst interval p25/p50/p75 and burst length | clicks on one target within 1.5 s of each other | V4 |
| SHIFT presses per viewport walk click | key records vs `WALK` actions | V5 |
| inter-key interval p50, sigma (log) | key records while logged in | V6 |
| focus changes per hour, unfocused fraction | `focus` records | V7 |
| camera events per login and the pitch value | `EVENT_CAMERA_POSITION` is not observable client-side; use the camera pitch from `/camera` at login+60s across sessions instead | V8 |

Target values come from the human sessions already on disk (`mahogny-homes` x4, n=1194
hotspot actions; `karamwan-fishing`; `guaridianoftherift-benchmark`; `human_dataset`) plus
ONE new human recording made after A1 ships, since none of the existing ones carry canvas
exit or focus records. USER plays 20-30 minutes of anything with a second screen in use.

### A3. Fingerprint parity on the bridge
`ClientVersionProvider` gains a `fingerprint` block computed the SAME way the client computes
its login `PlatformInfo` (from the decompile, `classVT.method12108` in 1.12.38):
`processCommand` (exe file name, first 10 chars), `parentProcessCommand` (first 10 chars),
`javaAgent` (the `-javaagent` argument's file name, up to 22 chars, or null), `osName`,
`osVersion`, `osArch` (lower-cased as the client does), `javaVendor`, `javaVersion`,
`maxMemoryMb` (`maxMemory/1048576 + 1`), `availableProcessors`, and `smbiosUuid` (the same
`wmic csproduct get UUID`, second line, placeholder on failure). This is what the server
sees at login, readable before logging in. `tools/deobfuscate.py --status` prints it.
Privacy: the UUID is machine identity; it is only ever served on localhost once A4 lands.

### A4. Bridge binds to loopback
`BridgeHttpServer.start` binds `InetSocketAddress("127.0.0.1", port)`; a `bridgeBindAll`
Controller Settings toggle (default off, description says what it exposes) restores the old
behaviour for the second-PC monitoring case. The UDP sender already targets localhost.
`mcp_debug_server.py` and `settings.API_BASE` are localhost already, so nothing else moves.

**Acceptance for A:** humanity_report runs on the new human recording and on one 30-minute
bot recording of `mahogany_homes` and prints every metric; `/client-version.fingerprint` is
populated; `curl http://<lan-ip>:8080/state` from another machine is refused.

## 4. Sub-project B -- Cheap behaviour fixes (V5, V7, V8, V6)

### B1. SHIFT held across a walk (V5)
`input_state.shift_held: bool` (single copy, per the controller-loaded-twice rule).
`travel.walk_to` wraps its hop loop in a context manager `travel.holding_shift(ctx)` that
presses SHIFT once before the first VIEWPORT hop and releases after the last (and in
`finally`, and on any exception -- a stuck SHIFT turns every later click into a
shift-click, the existing `drop_items` rule). `_click_walk_point`, `_hop_to` and
`walk_to_exact_tile` skip their own press while `shift_held` is true. Minimap hops never
needed SHIFT and stay bare. A drawn pre-press latency of 100-400 ms before the first click
(the human presses SHIFT while the mouse is already moving) replaces the current
press-then-fly ordering only on the first hop. The gathering `drop_items` shift-drop keeps
its per-item press: that IS the human pattern for dropping (measure it in A2 before
touching it).
**Test:** a walk of 4 viewport hops issues exactly one SHIFT down and one up; an exception
mid-walk still releases; a minimap-only walk issues none.

### B2. Breaks leave the window (V7)
Breaks are the natural moment a human is not looking at the game. `ControllerState`'s break
start (in `maybe_toggle_break`, after `before_break` has run) calls
`controller.afk_tab_out()` -- the karambwan `alt_tab` + `wait_for_focus(focused=False)`
primitives, lifted into the engine -- and the break end calls `afk_tab_in()` with the
verified return and `AFK_FOCUS_TRIES`; a failed return safety-stops exactly as karambwan's
does, because the engine's focus gate would otherwise stall the run silently. On a machine
with no other window (`wait_for_focus(focused=False)` times out) the break proceeds focused
as today, logged once. **Note the 5-minute idle logout interacts with this:** a break longer
than 5 minutes with the cursor still logs the account out today already; tabbing out changes
nothing there, and `login.py` handles the relogin as now.
Micro tab-outs during ordinary waits are deliberately NOT in v1: they need `outcome_waits`
to know a wait is long and interruption-safe, which is sub-project D's problem.
**Test:** a fake serial + scripted `/state.windowFocused` sees one tab-out at break start, one
tab-in at break end, and the tab-in retry path.

### B3. Camera setup with variance (V8)
`login.setup_camera`: hold UP for the measured full pitch, then tap DOWN a drawn 0-2 times;
wheel out a drawn 2-4 detents; then a drawn yaw nudge of 0-1 LEFT/RIGHT taps of 200-600 ms.
Routines are camera-independent by construction (every click is a bridge projection), so
this needs no routine change; the one thing to verify live is that the minimap-reach and
`HOP_VIEWPORT_MAX_TILES` assumptions hold at the shallowest pitch/zoom the draw can produce.
**Test:** 200 draws produce at least 6 distinct (pitch-taps, detents, yaw) tuples and never
exceed the bounds.

### B4. Human typing cadence (V6)
`type_text` and `type_digits_and_enter` draw inter-key gaps from a log-normal
(`TYPING_GAP_MEDIAN_S`, `TYPING_GAP_SIGMA`) with a first-key latency draw and a 1-in-12
chance of a 300-800 ms pause, all in `settings.py` beside the click-hold fit. Fit the two
numbers from A2's inter-key metric over the human recording (the bank search in mahogany
runs typed "adam"/"mith" by hand). **Test:** 500 draws match the configured median within
15% and contain no gap under 40 ms.

**Acceptance for B:** humanity_report on a bot `mahogany_homes` recording shows SHIFT
presses per walk click <= 0.3 (was 1.0), at least one focus change per break, and camera
pitch varying across three logins.

## 5. Sub-project C -- Human spam cadence for tick-locked bursts (V4)

Both affected routines click once per game tick on the tick clock. The game rolls per tick
regardless of how many clicks landed in it, so the fix is to click the way the human does
and let the game coalesce:

- `controller.spam_click(ctx, refresh_target, stop, max_s)` -- aim once, then `click_human`
  at gaps drawn from the fitted human burst distribution (mahogany, n=1194: median 0.20 s,
  p25 0.18 s; the fit goes in `settings.SPAM_GAP_*`), with a drawn breather of 0.8-2.5 s
  every 8-25 clicks (a human's thumb tires; measure the breather from the same recordings
  before pinning), re-aiming through `refresh_target` whenever the player moved. `stop` is
  the routine's own predicate (net out of range, retort potion finished).
- `deep_sea_trawling.trawl_burst` and `mastering_mixology`'s retort loop call it. The
  trawling heartbeat (Sailing XP per roll) and the retort's progress varbit remain the
  truth signals; `tick_pause` stays for waits that must land on a boundary and are NOT
  clicks (the foundry ramp stop is a single click per reading and is not affected).
- Cost: ~3x the clicks. The Arduino's `CLICK_HOLD_RANGE_S` median 85 ms leaves room at
  0.2 s gaps; below 0.15 s the hold would overlap the next press, so the draw is floored at
  0.15 s.

**Test:** 300 simulated seconds of `spam_click` yield an inter-click distribution whose
600 ms +- 100 ms band holds under 20% of gaps (today: ~100%), and every game tick in the
window received at least one click. **Live acceptance:** trawling's catch rate per minute is
unchanged (the roll is per tick), and mixology's retort finishes in the same tick count.

## 6. Sub-project D -- Cursor life: excursions and a background fidgeter (V2, V3)

The riskiest sub-project, because the cursor is load-bearing in several routines (a
pre-hovered next target, the foundry pool hover, mixology's next lever). The design makes
"the cursor may wander now" an explicit, opt-in property of a WAIT, defaulting to off for
short waits and on for long ones, with a single kill switch a routine can hold.

### D1. `input_state.hover_critical_until`
Any routine that parks the cursor on purpose calls `controller.hold_hover(seconds)` (the
foundry `_boundary_watch`, mixology pre-hovers, gathering's next-rock hover, the trawling
net). While it is in the future, neither the excursion nor the background fidgeter moves
the cursor. `move_to_human` clears it (a deliberate move supersedes a park).

### D2. Off-canvas excursions (V2)
`controller.cursor_excursion()`: pick an off-canvas rest point -- outside the RuneLite
window on the side of the second monitor when `/state` reports one (the screen geometry is
already read for DPI), else the taskbar band -- fly there with the ordinary v2 movement,
rest for a drawn duration (fit from A2's excursion-length metric; until then a first draft
of median 4 s, capped 20 s), fly back to within `IDLE_FIDGET_RETURN_JITTER_PX` of the
remembered anchor (the fidget-anchor rule: never to the exact pixel). Fires only from
`human_delay` draws and `outcome_waits.await_long` chunks longer than `EXCURSION_MIN_WAIT_S`
(first draft 6 s), with probability fit from A2's excursions-per-hour, never while
`hover_critical_until` is ahead, never with a menu open, never unfocused. The return flight
is budgeted INSIDE the wait so a signal that fires mid-excursion costs at most one return
flight (~0.3-0.6 s); waits shorter than the round trip never qualify.
**The engine's focus gate is unaffected:** moving the cursor out of the window changes
nothing about focus; only a click would.
**Test:** a scripted wait of 8 s with excursions enabled leaves and returns within the wait
and lands within jitter of the anchor; the same wait with `hold_hover` active never moves.

### D3. Background fidgeter behind an input lock (V3, "Phase B")
`input_state.input_lock: threading.RLock`. Every serial-writing primitive
(`move_to_human`, `click_entity`, `click_human`, `key_tap/down/up`, `scroll_wheel`,
`type_*`, `hover_entity`) acquires it for the duration of its serial traffic. This is the
first time two threads may write the port, and the firmware's `X` flush rule ("only legal
inside `move()`'s flight loop, where nothing but segments are queued") is exactly what the
lock preserves: a fidget can never interleave with a queued click.
`fidgeter.py` (its own module, so there is one of it however many times `controller.py` is
loaded): a daemon thread started by `main()` after the serial probe, polling every 250 ms;
when time since the last deliberate input exceeds a budget drawn from the measured
still-segment distribution with the `IDLE_MOVE_MIN_STILL_S` (4.0) floor, and the engine is
not on a break, the window is focused, no menu is open and `hover_critical_until` has
passed, it `try_acquire`s the lock and runs one `fidget_once()`; if the lock is busy it
simply skips. The existing `maybe_idle_mouse_move` in the engine loop is retired in favour
of this (one mechanism, not two overlapping ones -- but do NOT remove it before D3's metric
is verified live).
**Test:** with a fake serial and a fake clock, a 30 s window with no deliberate input
produces fidgets whose gaps are all >= 4.0 s and drawn from the configured distribution; a
held lock (simulated click in progress) delays, never overlaps; `hold_hover` suppresses.

**Acceptance for D:** humanity_report on a 30-minute bot recording of two routines
(`mahogany_homes` and `giants_foundry`, the second because of its hover parks) shows
still-segment p90 <= 5 s (human: never above 4.5 s in active play), off-canvas excursions
per hour within 50% of the human number, and the foundry's quality loss per sword unchanged
(the hover parks were respected).

## 7. Sub-project E -- Launch shape (V1)

### The finding that shapes this
"Launch through the official launcher with our plugins sideloaded" is not available:
`RuneLite.java` sets `developerMode = options.has("developer-mode") &&
RuneLiteProperties.getLauncherVersion() == null`, and `PluginManager.loadSideLoadPlugins`
returns immediately without developer mode. A launcher-launched client can never load a
sideloaded jar. So the process shape has to be reproduced around OUR launch instead.

### What the fingerprint actually contains, and the target values
From the decompile: exe file name (10 chars), parent exe file name (10 chars), `-javaagent`
file name, `os.*`, `java.vendor`/`java.version`, `maxMemory`, CPU count, SMBIOS UUID.
**The target values are MEASURED, not reasoned**: whether the launcher runs the client
in-process (own name `RuneLite.e`, parent `explorer.e` or `JagexLaunc`) or spawns a child
JVM from its bundled runtime (own name `java.exe`, parent `RuneLite.e`) decides both names,
and only the process tree can say which. Procedure: launch the official RuneLite once on
this machine, WITHOUT our plugin (nothing of ours needs to be loaded for this), log in as far
as the login screen, and read

    Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'RuneLite|java' } |
        Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine

which gives the client process's own name, its parent's name (by `ParentProcessId`), the
exact JVM path (hence vendor/version: run that `java.exe -version`) and the `-Xmx` and other
JVM arguments. Those five values are the parity targets for E3, pasted into
`tools/launch-target.json` with the date they were measured, and re-measured after any
launcher update (the launcher log at `%LOCALAPPDATA%\RuneLite\launcher.log` records those).

### Design
1. `tools/make_launch.py` assembles `%LOCALAPPDATA%\HttpbridgeLaunch\`: a copy of the
   launcher's bundled JRE's `java.exe` renamed `RuneLite.exe` (a renamed `java.exe`
   resolves `jvm.dll` from its own directory, so the copy lives inside a copy of that JRE),
   a `classpath.txt` written by a new Gradle task `printRunClasspath` (the exact classpath
   `run` uses: our plugin classes, the ZombieLite jar, the RuneLite client and the
   injected client from the Gradle cache), and a Windows shortcut that runs
   `RuneLite.exe -Xmx<launcher's value> <launcher's other JVM args> -cp @classpath.txt
   com.example.ExamplePluginTest`. Started from the shortcut, the parent is `explorer.exe`.
2. `-ea` (the `run` task's only JVM arg) is dropped from this launch; the launcher does not
   pass it and it is invisible to the fingerprint anyway.
3. Parity check: `tools/deobfuscate.py --status` prints A3's `fingerprint` block; the
   acceptance test is that `processCommand == "RuneLite.e"`, `parentProcessCommand ==
   "explorer.e"`, `javaAgent == null`, and `javaVendor`/`javaVersion`/`maxMemoryMb` equal the
   values read from `launcher.log`. `./gradlew.bat run` stays for development and is
   flagged by the guard: `deob_guard` logs a one-line warning whenever
   `processCommand` is not `RuneLite.e`, so a dev launch on the real account is never
   silent.
4. USER decision, recorded here, not made: whether to route the launch through the Jagex
   launcher's parent shape (`JagexLaunc`) instead of Explorer. It depends on how the account
   normally logs in on this machine, which the decompile cannot tell us; the parity check
   takes either as the target.

**Acceptance for E:** the parity block matches the launcher-derived targets on every field,
the bridge and ZombieLite are both up, `/client-version.injectedClientSha256` still matches
`deob-state.json`, and one full routine runs from the new launch.

## 8. Order, and what each step needs

| Step | Needs | Rebuild? |
|---|---|---|
| A | one new human recording after A1 | yes (recorder, provider, bind) |
| B | A's numbers for B4's fit; the rest can start on the existing recordings | no |
| C | the mahogany burst fit (already measured); live trawling for acceptance | no |
| D | A's still/excursion targets; B's break tab-out for the "not on a break" gate | no |
| E | A3's parity block; the launcher's `launcher.log`; USER's decision in E4 | yes |

A and B are one session each. C is small but its live acceptance needs a trawling session.
D is the long one and should be reviewed as its own branch. E is mostly a script plus a
live parity check.

## 9. Risks

- **D3 is the first concurrent writer to the serial port.** The lock is the whole safety
  argument; `tests/test_controller.py` gets a test that fails if any primitive writes to
  the port without holding it (a fake serial that asserts the lock is held on `write`).
- **Excursions and hover parks.** A routine that parks without `hold_hover` will lose its
  hover on the next long wait and pay a return flight. The audit list in D1 is from grep;
  the live acceptance on the foundry (quality per sword) is what proves it complete.
- **Camera variance and projection assumptions.** `HOP_VIEWPORT_MAX_TILES` and the minimap
  reach were measured at one zoom; the shallowest draw must be re-measured (the CLAUDE.md
  rule: the minimap's reach is a user setting, re-measure before trusting).
- **E changes the parent of the JVM and the JVM itself.** Everything Win32 in
  `controller.py` (DPI awareness, GetCursorPos, GetAsyncKeyState) is unaffected -- it runs
  in the Python process -- but the client's DPI behaviour under a differently-manifested exe
  is not: the CLAUDE.md HiDPI note says the bare `java.exe` reports 96 DPI; a copied
  `java.exe` under a new name has the same manifest, so this should hold, and it is the
  first thing to verify (`get_dpi_scale` and one click).
- **Measurement debt.** V2's severity is a hypothesis. If A2 shows the human's cursor also
  rarely leaves the canvas during focused skilling, D2 shrinks to the break tab-out and the
  effort goes to D3.

## 10. USER decisions (answered 2026-09-07)

1. E4 -- parent shape: **measure it.** Launch the official client once and read the process
   tree (the procedure in section 7) before choosing; the parity target is whatever that
   shows, not an assumption.
2. Human recording: **yes, 20-30 minutes of any activity** with the second screen in use,
   after A1 ships. V2 and V7 targets come from it.
3. Breaks: **tab out most of the time, ~70%**; the rest stay on the game window idle.
   `BREAK_TAB_OUT_CHANCE = 0.7` in `settings.py`.
