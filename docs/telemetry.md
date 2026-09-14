Moved out of CLAUDE.md on 2026-09-14. Nothing here is obsolete: this is the full measured record; the condensed working rules live in CLAUDE.md.

## What the game client sends home (read from the rl-suite deobfuscation, 2026-09-07)

Everything below was READ from `data/decompiled/rl-suite-main/semantics/injected-client-1.12.37-sources/`
(`PlatformInfo.java`, `classVS.java`, `client.java`, `classDK.java`, `classUO.java`,
`GameEngine.java`) and the rev-240 packet table
`data/decompiled/rl-suite-main/data/rev-240/mappings/client-packets.tsv` (117 opcodes, 101 named),
after USER asked how a VM could be hidden from the client. It is the list of what the client can
SEE, so future work knows which side of the wire a behaviour lands on. Two caveats: rl-suite's
semantic names are heuristic anchors (a couple of the server-packet names around the recorder
toggles look mis-anchored), so trust the described BEHAVIOUR over any name here; and this is the
Java client launched by `./gradlew.bat run`, not the Jagex Launcher, which this setup never runs.
Reading packet tables is not a licence to send packets -- see the rl-suite caveat above.

**Login fingerprint (`PlatformInfo`, serialised once at login).** On Windows the Java client
reads exactly: `os.name`, `os.version`, `os.arch`, `java.vendor`, `java.version`, max heap in MB
(`Runtime.maxMemory`, the JVM default of a quarter of RAM unless `-Xmx` is set), the CPU count,
and the SMBIOS system UUID by running **`wmic csproduct get UUID`** and taking the second line
(macOS `system_profiler`, Linux `/etc/machine-id`; on failure the placeholder
`12345678-0000-0000-0000-123456789012`, which is what a Windows 11 24H2+ guest sends since `wmic`
is gone there). The GPU / CPU-name / driver / display string fields exist in the class and the
Java client's constructor call passes them EMPTY -- they belong to the C++ client -- **but a
separate method fills THREE of the string slots after construction (found 2026-09-07 while
diffing our own 1.12.38 deobfuscation, and missed by the first read): the process's own
executable name (`ProcessHandle.current().info().command()`, first 10 chars), the PARENT
process's executable name (`.parent()`, first 10 chars), and the file name of the first JVM
argument whose 10-char prefix hashes to `-1094877034`, which is `-javaagent` (up to 22 chars).**
So the client can tell `java.exe` launched by a Gradle worker (`./gradlew.bat run`) from
`RuneLite.exe` launched by the launcher, and reports any Java agent by name. Keep the launch
shape constant per account; do not add a `-javaagent` to the run task. There is NO MAC-address
read, NO screen-size read and NO hypervisor check anywhere in the sources. So nothing says
"VMware" by name; a VM leaks only as a DIFFERENT identity from the one the account has always
logged in with. Three hypervisor-side knobs make the guest byte-identical: the same UUID
(VMware `uuid.bios` in the .vmx, VirtualBox `--hardware-uuid`), the same core count, and
`-Xmx` pinned so `maxMemory` matches -- plus the same launch shape, per the paragraph above.
Not traced through the obfuscation: which static wrapper calls the UUID getter on the Java
path; the method sits in the login class in the 1.12.14, 1.12.37 and 1.12.38 sources, so treat
it as sent.

**MEASURED on this host 2026-09-07** via `GET /client-version`'s `fingerprint` block
(`LoginFingerprint.java` computes it exactly as the client does; `python tools/deobfuscate.py
--status` prints it). These are the parity targets a different launch or a VM guest must match:

| field | host value under `./gradlew.bat run` |
|---|---|
| `processCommand` / `parentProcessCommand` | `java.exe` / `java.exe` (the Gradle worker -- a launcher-run client would report `RuneLite.e`) |
| `javaAgent` | none |
| `osName` / `osVersion` / `osArch` | `windows 10` / `10.0` / `amd64` |
| `javaVendor` / `javaVersion` | `Eclipse Adoptium` / `11.0.30` |
| `maxMemoryMb` / `availableProcessors` | 8147 / 32 |
| `smbiosUuid` | matches the host's `wmic csproduct get UUID` (printed by `--status`; not committed) |

The endpoint reads these; it never sends anything. It is served on **loopback only** since
2026-09-07 -- VERIFIED the same day: `Bridge running on http://127.0.0.1:8080` in the log, a
LAN request to `192.168.50.240:8080` and `192.168.56.1:8080` refused, `127.0.0.1:8080` 200.
The bridge has no auth and serves game state, screenshots, recorder keystrokes and the bank
memory, which is why the default moved; `bridgeBindAll` (Controller Settings) or
`BRIDGE_BIND_ALL=1` restores all-interfaces, and the VM guest NEEDS the env one because a
VirtualBox NAT forward lands on the guest's NAT adapter, never on its loopback.

**Continuous, during play -- this is the half that matters to us:**
- **`EVENT_MOUSE_MOVE`: the whole cursor trajectory, at 20 Hz.** A dedicated thread samples
  `(x, y, lastPressedTime)` every **50 ms** into a 500-slot buffer; the game loop flushes it
  when **40 samples** are pending (about every 2 s) or IMMEDIATELY when a button is down.
  Encoding is delta-from-previous with the time delta in 20 ms units (2 bytes for a step within
  +-32 px and under 160 ms, 3 bytes within +-128 px and under 640 ms, else absolute), a sample
  identical to the previous position is SKIPPED, positions are clamped to -1..65534 where
  **-1 means the cursor is off the canvas**, and a packet is capped at 246 bytes. Server packets
  switch the recorder on and off (on by default at startup). Consequences: stillness, snap
  moves, straight lines, an exact-pixel return, a cursor parked for minutes and the cursor
  LEAVING the window are all server-visible even with no click anywhere near them. This packet
  is what every measurement in "Movement realism batch" and the fidget anchor fix is aimed at.
- **`EVENT_MOUSE_CLICK_V2` (7 bytes), every press:** a flag byte, x clamped to the canvas
  width, `(ms since the previous press, capped 32767) << 1 | right-button`, y clamped to the
  canvas height. Inter-press timing therefore reaches the server with 1 ms resolution
  independently of the OP packets. The RELEASE is not sent, so `CLICK_HOLD_RANGE_S` is invisible
  to this packet (a hold still matters for drag-vs-click on the client).
- **`EVENT_KEYBOARD`: every key press with ms since the previous key** (24-bit cap). The
  password typed at login, the SHIFT before every walk click, F-keys, the held UP arrow of
  `login.setup_camera` -- their rhythm is on the wire. A fixed SHIFT-down-to-click gap is a
  signature; draw it.
- **`EVENT_MOUSE_SCROLL`**: the wheel delta per cycle. **`EVENT_CAMERA_POSITION`**: yaw and pitch,
  sent ONLY after an arrow-key camera move (key codes 96-99), rate-limited; a middle-mouse drag
  sends nothing.
- **`EVENT_APPLET_FOCUS`**: 1 / 0 on every window focus gain / loss, so karambwan's Alt+Tab AFK
  is seen exactly as a human's would be. Focus flapping at machine cadence would not be.
- **`WINDOW_STATUS`**: window mode plus canvas width and height, on change.
- **`IDLE`**: sent after 15 000 client cycles (20 ms each, 5 min) with NO mouse AND NO keyboard
  input -- the AFK-logout signal. **`NO_TIMEOUT`** is a keepalive every ~50 cycles that needs no
  input, so nothing has to nudge the mouse to hold the connection.
- **`REFLECTION_CHECK_REPLY`**: the server names classes, fields and methods; the client resolves
  them by reflection and replies with field values, modifiers and invoke results. RuneLite
  answers these against the vanilla jar named by `runelite.reflectcheck.jar` (the property is
  read in `client.java`), which is why an injected client reads as vanilla. We never modify
  injected classes, so nothing here changes it. **`DETECT_MODIFIED_CLIENT`** is one constant int
  sent on scene load whenever the client runs inside a top-level `Frame` (every RuneLite
  instance does), so it is not a differentiator. **`CONNECTION_TELEMETRY`** is login-connection
  timing counters (three longs, four ints). `SEND_SNAPSHOT` is the abuse report, `BUG_REPORT` and
  `CLIENT_CHEAT` (`::` commands) are user-initiated.

**What is NOT in any packet:** the plugin list, the sidebar, overlays, ZombieLite, the HTTP
bridge, the UDP stream, the serial port, other processes, the second monitor. The only channel
that carries our behaviour is the input stream itself -- which is exactly the channel a physical
Arduino feeds. Separately, RuneLite's own `TelemetryClient` (`client-1.12.33/.../TelemetryClient.java`)
sends the OS / Java / launcher-version strings and a "is a Jagex account" boolean to
**runelite.net**, not Jagex, and the GE plugin submits offers there too.

**Rules that fall out of this for future development:**
1. Treat every cursor sample as transmitted. "No click happened" never means "nothing was
   sent"; between-click motion, idle stillness and off-canvas excursions are the signal.
   Measure it with tools/humanity_report.py <human.jsonl> <bot.jsonl> -- the same metrics over
   both, which is the only way this class of signature is visible (the routine log cannot see it).
   **The recorder captures the two things that were previously invisible** (added 2026-09-07):
   `canvasexit`/`canvasenter` (the cursor leaving the window, which the client encodes as the
   -1 in `EVENT_MOUSE_MOVE`) and `focus` (its `EVENT_APPLET_FOCUS`), plus a `recorder` stamp
   record naming the schema. The stamp is load-bearing: a recording made BEFORE it exists
   reports those metrics as `-` (unmeasurable), never as 0, so an old capture can never be
   mistaken for "this bot never left the canvas". **Reading a recording out of the ring means
   reading it BEFORE disarming** -- `handleArm` calls `ring.reset()` unconditionally, so
   `arm?on=false` wipes the buffer (cost me one live run). VERIFIED live 2026-09-07: a scripted
   Arduino excursion above the canvas top plus one Alt+Tab produced `recorder` 1, `canvasexit`
   4, `canvasenter` 5, `focus` 2, and the report tool turned them into real numbers
   (`off_canvas_fraction` 0.266, `unfocused_fraction` 0.130). Note a maximized window has no
   left/right edge to leave by -- the cursor clamps inside the canvas; go UP into the title bar.
   **COMPARE THE SAME ACTIVITY, or the numbers mean nothing.** MEASURED 2026-09-07 over four
   real recordings, `still_p90_ms`: human karambwan 2038, human GOTR 608, cg-bot 612,
   foundry-bot 7493. The metric tracks the ACTIVITY's cadence, not human-vs-bot -- a human
   waiting on a fishing spot and a bot waiting on a foundry machine both look still, and a
   human at GOTR and a bot in the Gauntlet both click constantly. So a baseline human
   recording is only useful against a bot recording of THE SAME activity (the same rule the
   fidget-anchor fix already states). **The tool's first draft got this wrong in a way worth
   remembering**: `still_segments` held its anchor across sub-threshold gaps, which turned
   continuous motion into a fake ~150 ms pause every 150 ms and pinned `still_p50_ms` at
   152-160 ms on EVERY recording, human and bot alike. Two unit tests and two code reviews
   passed it; running it over one real recording is what caught it. Run any new metric over a
   real capture before pinning its expected value.
2. Timing that is drawn from a fitted human distribution (`settings.py`) is on the wire; timing
   that is a constant is a fingerprint. That applies to key presses as much as clicks.
3. Keep the login fingerprint constant per machine (same UUID, cores, heap, Windows build, JDK).
   A VM is fine on this channel once those match; server-side correlation is unknowable from
   the decompile, so "identical fingerprint" is the ceiling, not "undetectable".
4. Re-derive this list after a client build bump -- and the tooling below makes that one
   command, then a diff to read.

**Deobfuscating a new build ourselves, and the guard that forces it (added 2026-09-07).**
`python tools/deobfuscate.py` (repo root) does the whole thing: finds a JDK 17+ (rl-suite pins
a 17 toolchain with no foojay resolver, and this machine had only 8 and 11 -- Temurin 17 now
lives at `~/.jdks/jdk-17.0.20.1+1`, and the script downloads one there from the Adoptium API
when none exists), takes the jar the running client reports on `GET /client-version` (else the
newest `injected-client-*.jar` in the Gradle cache, else `--jar`), builds rl-suite's shaded jar
once, runs the transform, decompiles into
`data/decompiled/rl-suite-main/semantics/injected-client-<ver>-sources/`, and writes
`data/decompiled/deob-state.json` keyed on the jar's SHA-256. It answers rl-suite's two
documented refusals itself: "Expected N packet-handler shapes, found M" is retried with M
(**1.12.38 has SIX packet readers, MEASURED, where the tool expects five**), and a packet
fingerprint that matches no bundled profile -- a NEW wire protocol -- is retried with
`--packet-profile none` and recorded as `protocolKnown: false` (still readable, generic
`fieldN` packet names). **1.12.38 is still protocol 240**: the osrs-240 profile matched all 252
names. Our own runs carry no semantic map, so class names are generic (`PlatformInfo` is
`classVT` in 1.12.38); search by BEHAVIOUR (`wmic csproduct`, `new long[500]`, the packet
constants, which ARE named) rather than by the class names quoted above.
- **The guard**: `ControllerAddon/deob_guard.py`, called in `controller.main()` BEFORE the serial
  port opens and once per engine loop until it settles. `ClientVersionProvider.java`
  (`GET /client-version`: RuneLite version, `client.getRevision()`, the jar path from the
  `client` class's code source, its SHA-256 hashed once and cached) is compared with the
  record: match -> run; mismatch or no record while rl-suite is present -> print the command
  and exit 2 before any input; endpoint missing (un-rebuilt client), bridge down at startup
  (re-checked from the loop), or rl-suite absent (`data/` is gitignored, so a fresh clone has no
  tool) -> warn and drive. `--skip-deob-check` / `SKIP_DEOB_CHECK=1` overrides.
  `python tools/deobfuscate.py --status` shows record vs client without doing anything.
- **Every run diffs the telemetry surface against the previous build** (USER: "when we make a
  new deobfuscate we compare the diff from last rev"). A source diff is noise -- every name
  changes per build -- so `tools/telemetry_fingerprint.py` extracts the facts this section is
  built from by their name-independent tells (system properties and env vars read, commands
  passed to `exec`, the client packet table with ids and lengths, how many places reference
  each packet constant, the recorder's 500 / 50 ms / 40 constants, the ProcessHandle and
  `-javaagent` introspection) into `data/decompiled/deob/telemetry-<ver>.json`, and writes
  `telemetry-diff-<old>-to-<new>.md` beside it. The first run diffs against the author's
  checked-in 1.12.37 snapshot. **MEASURED 1.12.37 -> 1.12.38: nothing collected changed** --
  same properties, same three exec commands, same 117-packet table, same recorder constants;
  only reference counts moved (`CONNECTION_TELEMETRY` 2 -> 3, `EVENT_MOUSE_SCROLL` 3 -> 2, six
  gameplay packets +-1), which is the obfuscator re-inlining call sites, not new behaviour. A
  `+ exec`, `+ property`, a packet length change or a recorder constant change in that file is
  the signal to re-read the sources and amend this section.

