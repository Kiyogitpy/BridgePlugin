# Telemetry Mitigation, Sub-project A: Measure First -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every telemetry vector in the spec measurable (human vs bot, same metric), expose exactly what the client's login fingerprint will carry before logging in, and close the LAN exposure of the bridge -- with no behaviour change to any routine.

**Architecture:** Four independent pieces. (A1) `RecorderProvider` gains three ambient record kinds -- `canvasexit`, `canvasenter`, `focus` -- and `recorder.py` renders them. (A2) A new pure-Python tool `tools/humanity_report.py` computes the spec's metric table over any recording's `.jsonl` and prints two recordings side by side. (A3) A new `LoginFingerprint` utility reproduces, field for field, what the decompiled client puts in its login `PlatformInfo`; `ClientVersionProvider` serves it as a `fingerprint` block and `tools/deobfuscate.py --status` prints it. (A4) `BridgeHttpServer` binds `127.0.0.1` unless a new `bridgeBindAll` toggle or the `BRIDGE_BIND_ALL=1` env override says otherwise; the OSRS VM guest needs the override because a VirtualBox NAT port-forward reaches the guest's NAT address, not its loopback.

**Tech Stack:** Java 11 (RuneLite plugin, JUnit 4, Gradle), Python 3.13 (pytest). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-07-telemetry-vector-mitigation-design.md` (sections 3 and 9; the vector table in section 1 is what A2's metrics serve).

## Global Constraints

- **No behaviour change to routines.** Sub-project A must not alter how any routine clicks, moves or waits. Only measurement, reporting, a read-only endpoint field, and the bind address change.
- **Nothing sends packets.** The rl-suite caveat in CLAUDE.md stands; the fingerprint block only READS what the client already reads.
- **Every `@Subscribe` method is named `on<EventSimpleName>`** (CLAUDE.md: RuneLite's `EventBus.register` throws otherwise and the WHOLE plugin fails to register). `FocusChanged` -> `onFocusChanged`.
- **Recorder listeners must return the event unmodified** and record nothing unless `armed` (existing rule in `RecorderMouseListener`).
- **`data/` is gitignored**; recordings live in `ControllerAddon/recordings/` (gitignored). Tests use synthetic records only; the real-recording run is a verification step, not a test.
- **Python tests are hermetic** (`_no_live_bridge` convention): nothing in a test may reach the running client. `humanity_report.py` imports nothing from `controller`.
- **The client needs a rebuild + restart** (`./gradlew.bat run` on the host; in the VM, `vm-sync.ps1` then `vm-run-client.ps1`) before A1/A3/A4 exist at runtime. Python-only tasks need none.
- Commit messages follow the repo's `<area>: <what>` style (see `git log`), one commit per task.
- Fingerprint field semantics are the decompiled client's, exactly (spec section 7 and CLAUDE.md "What the game client sends home"): exe/parent names truncated to **10** chars, `-javaagent` file name to **22**, `os.*` lower-cased, `maxMemoryMb = maxMemory/1048576 + 1`, SMBIOS UUID from `wmic csproduct get UUID` second line, placeholder `12345678-0000-0000-0000-123456789012` on failure.

---

## File structure

| File | Responsibility |
|---|---|
| `src/main/java/com/example/util/LoginFingerprint.java` (new) | Pure helpers (`truncate`, `fileName`, `javaAgentName`, `uuidFromWmic`) + `collect()` that assembles the block the client sends at login |
| `src/test/java/com/example/util/LoginFingerprintTest.java` (new) | Pins the four pure helpers against the client's rules |
| `src/main/java/com/example/providers/ClientVersionProvider.java` (modify) | Adds a cached `fingerprint` block to `GET /client-version` |
| `tools/deobfuscate.py` (modify) | `--status` prints the fingerprint block; new pure `format_fingerprint` |
| `ControllerAddon/tests/test_deobfuscate_tool.py` (modify) | Test for `format_fingerprint` |
| `src/main/java/com/example/bridge/BridgeHttpServer.java` (modify) | `start(port, bindAll)` + pure `bindAddress(boolean)`; default loopback |
| `src/test/java/com/example/bridge/BridgeHttpServerTest.java` (new) | Pins the default bind address |
| `src/main/java/com/example/controller/ControllerConfig.java` (modify) | `bridgeBindAll` toggle (General section, default off) |
| `src/main/java/com/example/controller/ControllerPlugin.java` (modify) | Passes toggle OR `BRIDGE_BIND_ALL` env to `start` |
| `tools/vm-run-client.ps1`, `CLAUDE.md` (modify) | Guest launch sets `BRIDGE_BIND_ALL=1`; docs |
| `src/main/java/com/example/providers/RecorderProvider.java` (modify) | `canvasexit`/`canvasenter` from the mouse listener, `focus` from `FocusChanged` |
| `ControllerAddon/recorder.py` (modify) | Renders the three new kinds in the narrative |
| `ControllerAddon/tests/test_recorder.py` (modify) | `describe()` tests for the new kinds |
| `tools/humanity_report.py` (new) | Metric functions + CLI, pure over a `.jsonl` |
| `ControllerAddon/tests/test_humanity_report.py` (new) | Synthetic-record tests per metric |

---

### Task 1: `LoginFingerprint` pure helpers (Java, TDD)

**Files:**
- Create: `src/main/java/com/example/util/LoginFingerprint.java`
- Test: `src/test/java/com/example/util/LoginFingerprintTest.java`

**Interfaces:**
- Produces (package-private statics, used by Task 2 and tests): `String truncate(String s, int max)`, `String fileName(String path)`, `String javaAgentName(List<String> jvmArgs)`, `String uuidFromWmic(String wmicOutput)`; public `Map<String, Object> collect()`; public constant `PLACEHOLDER_UUID`.

- [ ] **Step 1: Write the failing tests**

```java
package com.example.util;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

public class LoginFingerprintTest
{
	@Test
	public void truncateKeepsTheClientsPrefixLengths()
	{
		// The decompiled client keeps the first 10 chars of an exe name and 22 of an agent name.
		assertEquals("RuneLite.e", LoginFingerprint.truncate("RuneLite.exe", 10));
		assertEquals("java.exe", LoginFingerprint.truncate("java.exe", 10));
		assertNull(LoginFingerprint.truncate(null, 10));
	}

	@Test
	public void fileNameStripsEitherSeparator()
	{
		assertEquals("java.exe", LoginFingerprint.fileName("C:\\Users\\Kiyo\\.jdks\\jdk-11\\bin\\java.exe"));
		assertEquals("java", LoginFingerprint.fileName("/usr/lib/jvm/bin/java"));
		assertEquals("RuneLite.exe", LoginFingerprint.fileName("RuneLite.exe"));
		assertNull(LoginFingerprint.fileName(null));
	}

	@Test
	public void javaAgentNameFollowsTheClientsParse()
	{
		// classVS.method12108: the first argument whose 10-char prefix hashes to -1094877034
		// ("-javaagent"); take from after the last '/' or '\' (else ':') up to '=' (else the end),
		// at most 22 chars.
		assertEquals("agent.jar", LoginFingerprint.javaAgentName(Arrays.asList(
			"-Xmx512m", "-javaagent:C:\\tools\\agent.jar=opt1,opt2", "-ea")));
		assertEquals("agent.jar", LoginFingerprint.javaAgentName(Collections.singletonList(
			"-javaagent:/opt/agent.jar")));
		assertEquals("a-very-long-agent-name", LoginFingerprint.javaAgentName(Collections.singletonList(
			"-javaagent:a-very-long-agent-name-that-exceeds.jar")));
		assertNull(LoginFingerprint.javaAgentName(Arrays.asList("-Xmx512m", "-ea")));
		assertNull(LoginFingerprint.javaAgentName(Collections.emptyList()));
	}

	@Test
	public void uuidFromWmicTakesTheSecondLineAndFallsBackToThePlaceholder()
	{
		// wmic prints a header line, the value, then blank lines.
		assertEquals("1EBB33E4-2B90-8093-433F-50EBF62C698A",
			LoginFingerprint.uuidFromWmic("UUID\r\n1EBB33E4-2B90-8093-433F-50EBF62C698A\r\n\r\n"));
		assertEquals(LoginFingerprint.PLACEHOLDER_UUID, LoginFingerprint.uuidFromWmic(""));
		assertEquals(LoginFingerprint.PLACEHOLDER_UUID, LoginFingerprint.uuidFromWmic("UUID\n\n"));
		assertEquals(LoginFingerprint.PLACEHOLDER_UUID, LoginFingerprint.uuidFromWmic(null));
	}
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./gradlew.bat --no-daemon -q test --tests "com.example.util.LoginFingerprintTest"`
Expected: compilation FAILS with `cannot find symbol: class LoginFingerprint`.

- [ ] **Step 3: Write the implementation**

```java
package com.example.util;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

/**
 * What the game client puts in its login {@code PlatformInfo}, computed the SAME way the client
 * computes it -- so a launch can be checked for fingerprint parity BEFORE the account logs in.
 *
 * Every rule here was read from the rl-suite deobfuscation (CLAUDE.md "What the game client
 * sends home", spec 2026-09-07-telemetry-vector-mitigation section 7): the Java client sends the
 * OS/Java property strings (os.* lower-cased), max heap in MB + 1, the CPU count, the SMBIOS
 * UUID via {@code wmic csproduct get UUID} (second line; a fixed placeholder on failure), and --
 * found only by diffing our own 1.12.38 run -- its own exe file name (first 10 chars), its PARENT
 * process's exe file name (first 10 chars) and the file name of the first {@code -javaagent}
 * argument (up to 22 chars). Nothing here is sent anywhere by us; it is served read-only on
 * {@code GET /client-version} for the parity check.
 */
public final class LoginFingerprint
{
	public static final String PLACEHOLDER_UUID = "12345678-0000-0000-0000-123456789012";
	static final int NAME_LIMIT = 10;
	static final int AGENT_LIMIT = 22;

	private LoginFingerprint()
	{
	}

	static String truncate(String s, int max)
	{
		if (s == null)
		{
			return null;
		}
		return s.length() > max ? s.substring(0, max) : s;
	}

	static String fileName(String path)
	{
		if (path == null)
		{
			return null;
		}
		int i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
		return i >= 0 ? path.substring(i + 1) : path;
	}

	/**
	 * The client matches the argument by {@code arg.substring(0, 10).hashCode() == -1094877034},
	 * which is exactly {@code "-javaagent".hashCode()}; startsWith is the readable equivalent.
	 */
	static String javaAgentName(List<String> jvmArgs)
	{
		for (String a : jvmArgs)
		{
			if (a.length() > 10 && a.startsWith("-javaagent"))
			{
				int eq = a.indexOf('=');
				if (eq == -1)
				{
					eq = a.length();
				}
				int sep = a.lastIndexOf('/', eq);
				if (sep == -1)
				{
					sep = a.lastIndexOf('\\', eq);
				}
				if (sep == -1)
				{
					sep = a.lastIndexOf(':');
				}
				return truncate(a.substring(sep + 1, eq), AGENT_LIMIT);
			}
		}
		return null;
	}

	/** The client joins every output line with "\n" and takes everything after the FIRST "\n", trimmed. */
	static String uuidFromWmic(String output)
	{
		if (output == null)
		{
			return PLACEHOLDER_UUID;
		}
		int nl = output.indexOf('\n');
		if (nl < 0)
		{
			return PLACEHOLDER_UUID;
		}
		String value = output.substring(nl).trim();
		return value.isEmpty() ? PLACEHOLDER_UUID : value;
	}

	private static String lower(String s)
	{
		return s == null ? null : s.toLowerCase(Locale.ROOT);
	}

	private static String readSmbiosUuid()
	{
		String os = System.getProperty("os.name", "");
		if (!os.toLowerCase(Locale.ROOT).contains("windows"))
		{
			return null;   // the mac/linux readers exist in the client too, but this project is Windows-only
		}
		try
		{
			Process p = Runtime.getRuntime().exec("wmic csproduct get UUID");
			StringBuilder sb = new StringBuilder();
			try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8)))
			{
				String line;
				while ((line = r.readLine()) != null)
				{
					sb.append(line).append('\n');
				}
			}
			return uuidFromWmic(sb.toString());
		}
		catch (Exception e)
		{
			return PLACEHOLDER_UUID;   // what the client sends when wmic is absent (Windows 11 24H2+)
		}
	}

	/** Assemble the block. Spawns wmic once; callers cache the result. Never throws. */
	public static Map<String, Object> collect()
	{
		Map<String, Object> f = new LinkedHashMap<>();
		try
		{
			ProcessHandle self = ProcessHandle.current();
			f.put("processCommand", truncate(fileName(self.info().command().orElse(null)), NAME_LIMIT));
			Optional<String> parentCmd = self.parent().flatMap(p -> p.info().command());
			f.put("parentProcessCommand", truncate(fileName(parentCmd.orElse(null)), NAME_LIMIT));
		}
		catch (Exception e)
		{
			f.put("processCommand", null);
			f.put("parentProcessCommand", null);
		}
		f.put("javaAgent", javaAgentName(ManagementFactory.getRuntimeMXBean().getInputArguments()));
		f.put("osName", lower(System.getProperty("os.name")));
		f.put("osVersion", lower(System.getProperty("os.version")));
		f.put("osArch", lower(System.getProperty("os.arch")));
		f.put("javaVendor", System.getProperty("java.vendor"));
		f.put("javaVersion", System.getProperty("java.version"));
		f.put("maxMemoryMb", (int) (Runtime.getRuntime().maxMemory() / 1048576L) + 1);
		f.put("availableProcessors", Runtime.getRuntime().availableProcessors());
		f.put("smbiosUuid", readSmbiosUuid());
		return f;
	}
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./gradlew.bat --no-daemon -q test --tests "com.example.util.LoginFingerprintTest"`
Expected: BUILD SUCCESSFUL; `build/test-results/test/TEST-com.example.util.LoginFingerprintTest.xml` shows `tests="4" failures="0" errors="0"`.

- [ ] **Step 5: Commit**

```bash
git add src/main/java/com/example/util/LoginFingerprint.java src/test/java/com/example/util/LoginFingerprintTest.java
git commit -m "fingerprint: LoginFingerprint reproduces the client's login PlatformInfo fields (telemetry A3)"
```

---

### Task 2: Serve the fingerprint on `/client-version` and print it in `--status`

**Files:**
- Modify: `src/main/java/com/example/providers/ClientVersionProvider.java` (the `getVersion()` method and fields)
- Modify: `tools/deobfuscate.py` (`status()`)
- Test: `ControllerAddon/tests/test_deobfuscate_tool.py`

**Interfaces:**
- Consumes: `LoginFingerprint.collect()` (Task 1).
- Produces: `GET /client-version` gains `"fingerprint": {processCommand, parentProcessCommand, javaAgent, osName, osVersion, osArch, javaVendor, javaVersion, maxMemoryMb, availableProcessors, smbiosUuid}`; Python `deobfuscate.format_fingerprint(fp: dict) -> list[str]`.

- [ ] **Step 1: Write the failing Python test**

Append to `ControllerAddon/tests/test_deobfuscate_tool.py`:

```python
def test_format_fingerprint_lists_every_field_the_client_sends_in_a_fixed_order():
    fp = {"processCommand": "java.exe", "parentProcessCommand": "java.exe", "javaAgent": None,
          "osName": "windows 10", "osVersion": "10.0", "osArch": "amd64",
          "javaVendor": "Eclipse Adoptium", "javaVersion": "11.0.30", "maxMemoryMb": 8147,
          "availableProcessors": 32, "smbiosUuid": "1EBB33E4-2B90-8093-433F-50EBF62C698A"}
    lines = d.format_fingerprint(fp)
    assert lines[0].startswith("processCommand")
    assert any(l.startswith("smbiosUuid") and "1EBB33E4" in l for l in lines)
    assert any("javaAgent" in l and "(none)" in l for l in lines)
    assert len(lines) == 11
    assert d.format_fingerprint({}) == []          # un-rebuilt client: nothing to print, no crash
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ControllerAddon && python -m pytest tests/test_deobfuscate_tool.py::test_format_fingerprint_lists_every_field_the_client_sends_in_a_fixed_order -q`
Expected: FAIL with `AttributeError: module 'deobfuscate' has no attribute 'format_fingerprint'`.

- [ ] **Step 3: Implement `format_fingerprint` and print it from `status()`**

In `tools/deobfuscate.py`, add above `def status(`:

```python
FINGERPRINT_FIELDS = ("processCommand", "parentProcessCommand", "javaAgent", "osName", "osVersion",
                      "osArch", "javaVendor", "javaVersion", "maxMemoryMb", "availableProcessors",
                      "smbiosUuid")


def format_fingerprint(fp: dict) -> list[str]:
    """The login fingerprint as the client will send it, one `key = value` line per field in the
    order the client writes them. Empty for a client that does not serve the block."""
    if not fp:
        return []
    return [f"{k:22} = {'(none)' if fp.get(k) is None else fp.get(k)}" for k in FINGERPRINT_FIELDS]
```

In `status()`, after the `print("client : " + ...)` line, add:

```python
    if live and live.get("fingerprint"):
        print("login fingerprint (what the server will see):")
        for line in format_fingerprint(live["fingerprint"]):
            print("         " + line)
```

- [ ] **Step 4: Run the Python test**

Run: `cd ControllerAddon && python -m pytest tests/test_deobfuscate_tool.py -q`
Expected: all pass (the file's existing tests plus the new one).

- [ ] **Step 5: Add the block to the provider**

In `ClientVersionProvider.java`, add a field and two lines in `getVersion()`:

```java
	private volatile Map<String, Object> cachedFingerprint;
```

```java
		// What the login packet will carry, computed the client's way (LoginFingerprint). Spawns
		// wmic once, so cache it -- the guard and --status poll this endpoint.
		if (cachedFingerprint == null)
		{
			cachedFingerprint = com.example.util.LoginFingerprint.collect();
		}
		data.put("fingerprint", cachedFingerprint);
		return data;
```

(Replace the existing `return data;` with the block above.)

- [ ] **Step 6: Compile and run the Java tests**

Run: `./gradlew.bat --no-daemon -q test --tests "com.example.providers.ClientVersionProviderTest" --tests "com.example.util.LoginFingerprintTest"`
Expected: BUILD SUCCESSFUL, 7 tests, 0 failures.

- [ ] **Step 7: Commit**

```bash
git add src/main/java/com/example/providers/ClientVersionProvider.java tools/deobfuscate.py ControllerAddon/tests/test_deobfuscate_tool.py
git commit -m "fingerprint: /client-version serves the login fingerprint block; deobfuscate --status prints it (telemetry A3)"
```

---

### Task 3: Bridge binds loopback by default, with a toggle and an env override

**Files:**
- Modify: `src/main/java/com/example/bridge/BridgeHttpServer.java` (`start`)
- Create: `src/test/java/com/example/bridge/BridgeHttpServerTest.java`
- Modify: `src/main/java/com/example/controller/ControllerConfig.java` (General section)
- Modify: `src/main/java/com/example/controller/ControllerPlugin.java:133-134` (`server.start(HTTP_PORT)`)
- Modify: `tools/vm-run-client.ps1` (env for the guest launch)

**Interfaces:**
- Produces: `static String BridgeHttpServer.bindAddress(boolean bindAll)`; `void start(int port, boolean bindAll)` (the old `start(int)` stays and means loopback); config `boolean bridgeBindAll()` default `false`; env `BRIDGE_BIND_ALL=1` forces all interfaces.

- [ ] **Step 1: Write the failing Java test**

```java
package com.example.bridge;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class BridgeHttpServerTest
{
	@Test
	public void theDefaultIsLoopbackAndTheToggleIsAllInterfaces()
	{
		// The bridge serves game state, screenshots, the recorder's keystrokes and the bank
		// memory with no auth; before 2026-09-07 it was bound to every interface (spec V9).
		assertEquals("127.0.0.1", BridgeHttpServer.bindAddress(false));
		assertEquals("0.0.0.0", BridgeHttpServer.bindAddress(true));
	}
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./gradlew.bat --no-daemon -q test --tests "com.example.bridge.BridgeHttpServerTest"`
Expected: compilation FAILS with `cannot find symbol: method bindAddress(boolean)`.

- [ ] **Step 3: Implement in `BridgeHttpServer.java`**

Replace the existing `start(int port)` with:

```java
	/** Loopback unless told otherwise -- the bridge has no auth (spec V9, 2026-09-07). */
	static String bindAddress(boolean bindAll)
	{
		return bindAll ? "0.0.0.0" : "127.0.0.1";
	}

	public void start(int port)
	{
		start(port, false);
	}

	public void start(int port, boolean bindAll)
	{
		try
		{
			String host = bindAddress(bindAll);
			server = HttpServer.create(new InetSocketAddress(host, port), 0);
			server.setExecutor(Executors.newCachedThreadPool());
			server.start();
			System.out.println("Bridge running on http://" + host + ":" + port
				+ (bindAll ? "  (ALL interfaces -- reachable from the LAN)" : ""));
		}
		catch (Exception e)
		{
			throw new RuntimeException("Failed to start HTTP bridge", e);
		}
	}
```

- [ ] **Step 4: Add the toggle to `ControllerConfig.java`** (in the General section, after `controllerEnabled`)

```java
	@ConfigItem(
		keyName = "bridgeBindAll",
		name = "Bridge on all interfaces",
		description = "Bind the HTTP bridge to 0.0.0.0 instead of 127.0.0.1. It has NO auth and serves "
			+ "game state, screenshots, recorder keystrokes and the bank memory -- turn on only for a "
			+ "monitoring PC. The OSRS VM guest uses the BRIDGE_BIND_ALL=1 environment variable instead "
			+ "(a VirtualBox port-forward reaches the guest's NAT address, not its loopback).",
		section = generalSection,
		position = 1
	)
	default boolean bridgeBindAll()
	{
		return false;
	}
```

- [ ] **Step 5: Pass it in `ControllerPlugin.java`**

Replace `server.start(HTTP_PORT);` with:

```java
		// Loopback by default (spec V9). The panel toggle is per PROFILE, which the VM guest copies
		// from the host, so the guest's need for an all-interfaces bind is an ENV override instead
		// -- it survives every profile re-copy.
		boolean bindAll = config.bridgeBindAll() || "1".equals(System.getenv("BRIDGE_BIND_ALL"));
		server.start(HTTP_PORT, bindAll);
```

- [ ] **Step 6: Set the env in the guest launch**

In `tools/vm-run-client.ps1`, next to `$env:BRIDGE_SERIAL_PORT = "COM3"`, add:

```powershell
$env:BRIDGE_BIND_ALL = "1"       # the host's 18080 -> guest 8080 forward lands on the NAT adapter, not loopback
```

And in the CLAUDE.md "The VM" bullet, add one sentence after the `natpf1` sentence: `Since the bridge binds loopback by default (2026-09-07), the guest launch sets BRIDGE_BIND_ALL=1 (vm-run-client.ps1, and --putenv BRIDGE_BIND_ALL=1 on any guestcontrol launch) or the forward dead-ends.`

- [ ] **Step 7: Run the Java tests**

Run: `./gradlew.bat --no-daemon -q test --tests "com.example.bridge.BridgeHttpServerTest"`
Expected: BUILD SUCCESSFUL, 1 test, 0 failures. Then `./gradlew.bat --no-daemon -q compileJava` -> no errors.

- [ ] **Step 8: Commit**

```bash
git add src/main/java/com/example/bridge/BridgeHttpServer.java src/test/java/com/example/bridge/BridgeHttpServerTest.java src/main/java/com/example/controller/ControllerConfig.java src/main/java/com/example/controller/ControllerPlugin.java tools/vm-run-client.ps1 CLAUDE.md
git commit -m "bridge: bind loopback by default; bridgeBindAll toggle + BRIDGE_BIND_ALL env for the VM guest (telemetry A4)"
```

---

### Task 4: Recorder emits `canvasexit` / `canvasenter` / `focus`, and `recorder.py` renders them

**Files:**
- Modify: `src/main/java/com/example/providers/RecorderProvider.java` (imports; `RecorderMouseListener` ~line 1493; a new `@Subscribe` near `onGameStateChanged` ~line 1258)
- Modify: `ControllerAddon/recorder.py` (`LOOSE_NARRATIVE_KINDS` ~line 53; `describe()` ~line 239)
- Test: `ControllerAddon/tests/test_recorder.py`

**Interfaces:**
- Produces record kinds, each with the `base()` fields (`kind`, `t`, `wall`, `tick`, `tickAge`, `seq`): `canvasexit {x, y}`, `canvasenter {x, y}`, `focus {gained: bool}`. Task 5-7 consume these shapes.

- [ ] **Step 1: Write the failing Python tests** (append to `tests/test_recorder.py`)

```python
def test_describe_renders_canvas_edge_and_focus_records():
    # New 2026-09-07 kinds for the telemetry metrics: the client sends the cursor leaving the
    # canvas (-1 in EVENT_MOUSE_MOVE) and every focus gain/loss (EVENT_APPLET_FOCUS).
    assert r.describe({"kind": "canvasexit", "x": 764, "y": 12}) == "cursor LEFT the canvas at (764, 12)"
    assert r.describe({"kind": "canvasenter", "x": 3, "y": 400}) == "cursor back on the canvas at (3, 400)"
    assert r.describe({"kind": "focus", "gained": True}) == "-- window FOCUSED --"
    assert r.describe({"kind": "focus", "gained": False}) == "-- window UNFOCUSED --"


def test_canvas_edge_and_focus_records_are_loose_narrative_kinds():
    for kind in ("canvasexit", "canvasenter", "focus"):
        assert kind in r.LOOSE_NARRATIVE_KINDS
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ControllerAddon && python -m pytest tests/test_recorder.py -q -k "canvas or focus"`
Expected: 2 FAIL (`describe` falls through to the generic `kind {...}` line; the set lacks the kinds).

- [ ] **Step 3: Implement in `recorder.py`**

Extend the set:

```python
LOOSE_NARRATIVE_KINDS = {"chat", "xp", "anim", "spotanim", "gfx", "inv", "invstart", "key",
                         "gamestate", "interact", "objspawn", "objdespawn", "varbit",
                         "npcspawn", "npcdespawn", "npcchange", "walkdest",
                         "canvasexit", "canvasenter", "focus"}
```

In `describe()`, before the `if kind == "key":` line:

```python
    if kind == "canvasexit":
        return f"cursor LEFT the canvas at ({record.get('x')}, {record.get('y')})"
    if kind == "canvasenter":
        return f"cursor back on the canvas at ({record.get('x')}, {record.get('y')})"
    if kind == "focus":
        return "-- window FOCUSED --" if record.get("gained") else "-- window UNFOCUSED --"
```

- [ ] **Step 4: Run the Python tests**

Run: `cd ControllerAddon && python -m pytest tests/test_recorder.py -q`
Expected: all pass.

- [ ] **Step 5: Implement the Java side**

Add the import `import net.runelite.api.events.FocusChanged;` to `RecorderProvider.java`.

Inside `RecorderMouseListener` (after `mouseReleased`), add:

```java
		@Override
		public MouseEvent mouseEntered(MouseEvent event)
		{
			edge("canvasenter", event);
			return event;
		}

		@Override
		public MouseEvent mouseExited(MouseEvent event)
		{
			edge("canvasexit", event);
			return event;
		}

		/**
		 * The client's own EVENT_MOUSE_MOVE encodes an off-canvas cursor as -1, so when the cursor
		 * leaves and returns is server-visible (spec V2). The open mouse batch is flushed first so
		 * the polyline ENDS at the edge and the off-canvas gap is measurable in the .jsonl.
		 */
		private void edge(String kind, MouseEvent event)
		{
			if (!armed)
			{
				return;
			}
			flushMouseBatch();
			Map<String, Object> record = base(kind);
			record.put("x", event.getX());
			record.put("y", event.getY());
			add(record);
		}
```

Check `flushMouseBatch()`'s guard: if it does not already return on an empty batch, wrap the call as `synchronized (mouseLock) { if (!mouseBatch.isEmpty()) flush = true; }` before calling -- the existing `sample()` only calls it with a non-empty batch.

Add the focus subscriber after `onGameStateChanged`:

```java
	@Subscribe
	public void onFocusChanged(FocusChanged event)
	{
		// EVENT_APPLET_FOCUS: the client sends 1/0 on every gain/loss (spec V7).
		if (!armed)
		{
			return;
		}
		Map<String, Object> record = base("focus");
		record.put("gained", event.isFocused());
		add(record);
	}
```

- [ ] **Step 6: Compile**

Run: `./gradlew.bat --no-daemon -q compileJava`
Expected: no errors. (The `on<EventSimpleName>` rule is satisfied: `onFocusChanged(FocusChanged)`.)

- [ ] **Step 7: Commit**

```bash
git add src/main/java/com/example/providers/RecorderProvider.java ControllerAddon/recorder.py ControllerAddon/tests/test_recorder.py
git commit -m "recorder: canvasexit/canvasenter and focus records for the telemetry metrics (telemetry A1)"
```

---

### Task 5: `humanity_report.py` -- loading, active windows, stillness, busy fraction

**Files:**
- Create: `tools/humanity_report.py`
- Test: `ControllerAddon/tests/test_humanity_report.py`

**Interfaces:**
- Produces (all pure): `load(path) -> list[dict]`; `percentile(values, p) -> float|None`; `clicks(records) -> list[dict]`; `active_windows(records, pad_ms=30000) -> list[tuple[int,int]]`; `cursor_samples(records) -> list[tuple[int,int,int]]` (t, x, y sorted); `still_segments(records, windows) -> list[int]` (ms); `busy_fraction(records, windows) -> float|None`; `duration_ms(records) -> int`. Tasks 6-8 build on these.

- [ ] **Step 1: Write the failing tests**

```python
"""tools/humanity_report.py: every metric on hand-built records with known answers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import humanity_report as h  # noqa: E402


# ---- record builders (the recorder's real shapes, minimal fields) ---------------------------

def mouse(t, x, y):
    return {"kind": "mouse", "t": t, "points": [[x, y, t]]}


def click(t, x=100, y=100):
    return {"kind": "click", "t": t, "edge": "down", "button": 1, "x": x, "y": y}


def key(t, name, edge="down"):
    return {"kind": "key", "t": t, "edge": edge, "keyName": name, "keyCode": 0}


def action(t, menu="WALK"):
    return {"kind": "action", "t": t, "menuAction": menu}


def canvas(t, kind):
    return {"kind": kind, "t": t, "x": 0, "y": 0}


def focus(t, gained):
    return {"kind": "focus", "t": t, "gained": gained}


def test_percentile_interpolates_and_handles_empty():
    assert h.percentile([], 0.5) is None
    assert h.percentile([10], 0.9) == 10
    assert h.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert h.percentile([1, 2, 3, 4], 1.0) == 4


def test_active_windows_are_the_spans_between_clicks_no_further_than_2x_pad_apart():
    # Two clicks 10s apart form one active span; a click 100s later starts a new one (with the
    # pad at 30s, a 100s gap has no click within 30s on both sides in its middle).
    recs = [click(0), click(10_000), click(110_000), click(115_000)]
    assert h.active_windows(recs, pad_ms=30_000) == [(0, 10_000), (110_000, 115_000)]
    assert h.active_windows([click(5)], pad_ms=30_000) == []          # one click: no span
    assert h.active_windows([], pad_ms=30_000) == []


def test_still_segments_are_gaps_between_cursor_samples_inside_active_play_only():
    recs = [click(0), mouse(100, 1, 1), mouse(600, 2, 2), mouse(5_600, 3, 3),   # gaps 500, 5000
            click(6_000),
            mouse(200_000, 4, 4), mouse(203_000, 5, 5)]                         # outside any window
    windows = h.active_windows(recs, pad_ms=30_000)                             # [(0, 6000)]
    assert h.still_segments(recs, windows) == [500, 5_000]


def test_busy_fraction_counts_one_second_bins_with_any_cursor_sample():
    recs = [click(0), mouse(100, 1, 1), mouse(2_500, 2, 2), click(4_000)]      # window (0, 4000)
    windows = h.active_windows(recs, pad_ms=30_000)
    # bins: [0,1s) has a sample, [1,2) none, [2,3) has, [3,4) none -> 2 of 4
    assert abs(h.busy_fraction(recs, windows) - 0.5) < 1e-9
    assert h.busy_fraction(recs, []) is None


def test_load_skips_malformed_lines(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"kind":"click","t":1}\nnot json\n{"kind":"mouse","t":2,"points":[]}\n', encoding="utf-8")
    assert [r["kind"] for r in h.load(p)] == ["click", "mouse"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ControllerAddon && python -m pytest tests/test_humanity_report.py -q`
Expected: FAIL at import (`ModuleNotFoundError: No module named 'humanity_report'`).

- [ ] **Step 3: Write the module**

```python
"""Humanity report: the same input-behaviour metrics over a human recording and a bot recording.

    python tools/humanity_report.py recordings/human.jsonl [recordings/bot.jsonl]

Added 2026-09-07 for the telemetry mitigation spec (sub-project A2). The decompiled client
sends the whole cursor trajectory, every press with ms-since-previous, every key press with
ms-since-previous, and every focus change; a routine's own log cannot see any of that. This
tool reads a recorder .jsonl (ControllerAddon/recorder.py) and prints, per metric, a number that
the human recording sets the target for. Every function is pure over the record list; the CLI
only loads and formats. Stillness and busy-fraction are counted over ACTIVE play only (a click
within ACTIVE_PAD_MS on both sides -- the existing rule from the movement-realism fits), so a
scheduled break cannot pass for a human pause.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from bisect import bisect_right
from pathlib import Path
from typing import Iterable, Optional

ACTIVE_PAD_MS = 30_000
BUSY_BIN_MS = 1_000
BURST_GAP_MS = 1_500
TYPING_MAX_GAP_MS = 2_000
MODIFIER_KEYS = {"Shift", "Ctrl", "Alt", "Windows", "Caps Lock", "Context Menu"}
FUNCTION_KEYS = {f"F{i}" for i in range(1, 13)}


# ---------------------------------------------------------------- loading and basics

def load(path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def percentile(values: Iterable[float], p: float) -> Optional[float]:
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def duration_ms(records: list[dict]) -> int:
    ts = [r["t"] for r in records if "t" in r]
    return (max(ts) - min(ts)) if ts else 0


def clicks(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("kind") == "click" and r.get("edge") == "down"]


def cursor_samples(records: list[dict]) -> list[tuple[int, int, int]]:
    """(t, x, y) for every mouse polyline point, time-sorted."""
    pts = []
    for r in records:
        if r.get("kind") == "mouse":
            for x, y, t in r.get("points") or []:
                pts.append((t, x, y))
    pts.sort()
    return pts


# ---------------------------------------------------------------- active play

def active_windows(records: list[dict], pad_ms: int = ACTIVE_PAD_MS) -> list[tuple[int, int]]:
    """Spans of active play: a moment is active when a click lies within pad_ms on BOTH sides,
    i.e. it sits between two consecutive clicks no more than 2*pad_ms apart. Adjacent spans
    merge."""
    ts = sorted(c["t"] for c in clicks(records))
    windows: list[tuple[int, int]] = []
    for a, b in zip(ts, ts[1:]):
        if b - a <= 2 * pad_ms:
            if windows and windows[-1][1] == a:
                windows[-1] = (windows[-1][0], b)
            else:
                windows.append((a, b))
    return windows


def _in_windows(t: int, windows: list[tuple[int, int]]) -> Optional[int]:
    """Index of the window containing t, or None."""
    starts = [w[0] for w in windows]
    i = bisect_right(starts, t) - 1
    if i >= 0 and windows[i][0] <= t <= windows[i][1]:
        return i
    return None


def still_segments(records: list[dict], windows: list[tuple[int, int]]) -> list[int]:
    """Gaps (ms) between consecutive cursor samples that fall inside the SAME active window --
    the stillness the client's 20 Hz recorder reports as 'no sample'."""
    gaps = []
    prev_t, prev_w = None, None
    for t, _, _ in cursor_samples(records):
        w = _in_windows(t, windows)
        if w is not None and prev_w == w:
            gaps.append(t - prev_t)
        prev_t, prev_w = t, w
    return gaps


def busy_fraction(records: list[dict], windows: list[tuple[int, int]]) -> Optional[float]:
    """Share of BUSY_BIN_MS bins inside active play that contain at least one cursor sample."""
    total = 0
    busy = 0
    samples = [t for t, _, _ in cursor_samples(records)]
    for start, end in windows:
        for b in range(start, end, BUSY_BIN_MS):
            total += 1
            lo = bisect_right(samples, b - 1)
            if lo < len(samples) and samples[lo] < b + BUSY_BIN_MS:
                busy += 1
    return (busy / total) if total else None
```

- [ ] **Step 4: Run the tests**

Run: `cd ControllerAddon && python -m pytest tests/test_humanity_report.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/humanity_report.py ControllerAddon/tests/test_humanity_report.py
git commit -m "humanity_report: active windows, stillness and busy fraction over a recording (telemetry A2)"
```

---

### Task 6: `humanity_report.py` -- off-canvas excursions and focus

**Files:**
- Modify: `tools/humanity_report.py`
- Test: `ControllerAddon/tests/test_humanity_report.py`

**Interfaces:**
- Consumes: `duration_ms` (Task 5), the `canvasexit`/`canvasenter`/`focus` record shapes (Task 4).
- Produces: `excursions(records) -> dict(count, per_hour, lengths_ms: list[int], off_fraction)`; `focus_stats(records) -> dict(changes, per_hour, unfocused_fraction)`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_excursions_pair_exit_with_the_next_enter():
    recs = [click(0), canvas(1_000, "canvasexit"), canvas(4_000, "canvasenter"),
            canvas(10_000, "canvasexit"), canvas(11_000, "canvasenter"), click(3_600_000)]
    ex = h.excursions(recs)
    assert ex["count"] == 2
    assert ex["lengths_ms"] == [3_000, 1_000]
    assert abs(ex["per_hour"] - 2.0) < 1e-6            # 1 hour of recording
    assert abs(ex["off_fraction"] - 4_000 / 3_600_000) < 1e-9


def test_excursion_still_open_at_the_end_counts_to_the_end():
    recs = [click(0), canvas(1_000, "canvasexit"), click(5_000)]
    ex = h.excursions(recs)
    assert ex["count"] == 1 and ex["lengths_ms"] == [4_000]


def test_focus_stats_integrate_unfocused_time():
    recs = [click(0), focus(1_000, False), focus(3_000, True), focus(8_000, False), click(10_000)]
    fs = h.focus_stats(recs)
    assert fs["changes"] == 3
    assert abs(fs["unfocused_fraction"] - (2_000 + 2_000) / 10_000) < 1e-9
    assert h.focus_stats([click(0), click(10)])["changes"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ControllerAddon && python -m pytest tests/test_humanity_report.py -q -k "excursion or focus"`
Expected: 3 FAIL with `AttributeError`.

- [ ] **Step 3: Implement** (append to `humanity_report.py`)

```python
# ---------------------------------------------------------------- off-canvas and focus

def _spans(records: list[dict], open_kind: str, close_kind: str) -> list[tuple[int, int]]:
    """[open_t, close_t] pairs; an open with no close runs to the recording's last t."""
    end = max((r["t"] for r in records if "t" in r), default=0)
    spans, open_t = [], None
    for r in sorted((r for r in records if r.get("kind") in (open_kind, close_kind)), key=lambda r: r["t"]):
        if r["kind"] == open_kind and open_t is None:
            open_t = r["t"]
        elif r["kind"] == close_kind and open_t is not None:
            spans.append((open_t, r["t"]))
            open_t = None
    if open_t is not None:
        spans.append((open_t, end))
    return spans


def excursions(records: list[dict]) -> dict:
    """Times the cursor left the canvas (the client encodes this as -1 in EVENT_MOUSE_MOVE)."""
    spans = _spans(records, "canvasexit", "canvasenter")
    total = duration_ms(records)
    lengths = [b - a for a, b in spans]
    hours = total / 3_600_000 if total else 0
    return {"count": len(spans), "per_hour": (len(spans) / hours) if hours else None,
            "lengths_ms": lengths, "off_fraction": (sum(lengths) / total) if total else None}


def focus_stats(records: list[dict]) -> dict:
    """Window focus changes (EVENT_APPLET_FOCUS) and the share of time spent unfocused."""
    events = sorted((r for r in records if r.get("kind") == "focus"), key=lambda r: r["t"])
    total = duration_ms(records)
    unfocused = 0
    lost_at = None
    for r in events:
        if not r.get("gained") and lost_at is None:
            lost_at = r["t"]
        elif r.get("gained") and lost_at is not None:
            unfocused += r["t"] - lost_at
            lost_at = None
    if lost_at is not None:
        unfocused += max((r["t"] for r in records if "t" in r), default=lost_at) - lost_at
    hours = total / 3_600_000 if total else 0
    return {"changes": len(events), "per_hour": (len(events) / hours) if hours else None,
            "unfocused_fraction": (unfocused / total) if total else None}
```

- [ ] **Step 4: Run the tests**

Run: `cd ControllerAddon && python -m pytest tests/test_humanity_report.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/humanity_report.py ControllerAddon/tests/test_humanity_report.py
git commit -m "humanity_report: off-canvas excursions and focus changes (telemetry A2)"
```

---

### Task 7: `humanity_report.py` -- click bursts, SHIFT-per-walk, typing cadence

**Files:**
- Modify: `tools/humanity_report.py`
- Test: `ControllerAddon/tests/test_humanity_report.py`

**Interfaces:**
- Produces: `bursts(records) -> dict(intra_gaps_ms: list[int], lengths: list[int], burst_share)`; `shift_per_walk(records) -> dict(shift_downs, walk_clicks, ratio)`; `typing(records) -> dict(intervals_ms: list[int], p50, log_sigma)`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_bursts_group_clicks_within_the_gap_and_report_intra_gaps():
    # Three quick clicks (200 ms apart), then a lone click 5 s later, then two more.
    recs = [click(0), click(200), click(400), click(5_400), click(9_000), click(9_300)]
    b = h.bursts(recs)
    assert b["lengths"] == [3, 2]              # bursts of >= 2 clicks only
    assert b["intra_gaps_ms"] == [200, 200, 300]
    assert abs(b["burst_share"] - 2 / 3) < 1e-9   # 2 of 3 click groups were bursts


def test_shift_per_walk_is_shift_downs_over_viewport_walk_actions():
    recs = [key(0, "Shift"), click(50), action(60, "WALK"), key(70, "Shift", "up"),
            key(1_000, "Shift"), click(1_050), action(1_060, "WALK"), key(1_070, "Shift", "up"),
            action(2_000, "GAME_OBJECT_FIRST_OPTION")]
    s = h.shift_per_walk(recs)
    assert s == {"shift_downs": 2, "walk_clicks": 2, "ratio": 1.0}
    assert h.shift_per_walk([click(0)])["ratio"] is None


def test_typing_ignores_modifiers_and_function_keys_and_long_pauses():
    recs = [key(0, "A"), key(120, "D"), key(250, "Shift"), key(300, "A"), key(5_000, "M"), key(5_090, "F1"), key(5_200, "Enter")]
    t = h.typing(recs)
    # A->D 120, D->A 180 (Shift skipped), A->M 4700 is > TYPING_MAX_GAP_MS so dropped, M->Enter 200 (F1 skipped)
    assert t["intervals_ms"] == [120, 180, 200]
    assert t["p50"] == 180
    assert t["log_sigma"] is not None and t["log_sigma"] > 0
    assert h.typing([key(0, "A")])["p50"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ControllerAddon && python -m pytest tests/test_humanity_report.py -q -k "burst or shift or typing"`
Expected: 3 FAIL with `AttributeError`.

- [ ] **Step 3: Implement** (append to `humanity_report.py`)

```python
# ---------------------------------------------------------------- clicks and keys

def bursts(records: list[dict]) -> dict:
    """Consecutive presses no more than BURST_GAP_MS apart form a burst (the human's 0.20 s
    hotspot spam; EVENT_MOUSE_CLICK_V2 carries every one of these gaps)."""
    ts = sorted(c["t"] for c in clicks(records))
    groups: list[list[int]] = []
    for t in ts:
        if groups and t - groups[-1][-1] <= BURST_GAP_MS:
            groups[-1].append(t)
        else:
            groups.append([t])
    multi = [g for g in groups if len(g) >= 2]
    gaps = [b - a for g in multi for a, b in zip(g, g[1:])]
    return {"intra_gaps_ms": gaps, "lengths": [len(g) for g in multi],
            "burst_share": (len(multi) / len(groups)) if groups else None}


def shift_per_walk(records: list[dict]) -> dict:
    """SHIFT presses per viewport walk click. The bot presses SHIFT once per walk click (spec V5);
    a human holds it across a run, or never uses it."""
    shift = sum(1 for r in records if r.get("kind") == "key" and r.get("edge") == "down"
                and r.get("keyName") == "Shift")
    walks = sum(1 for r in records if r.get("kind") == "action" and r.get("menuAction") == "WALK")
    return {"shift_downs": shift, "walk_clicks": walks, "ratio": (shift / walks) if walks else None}


def typing(records: list[dict]) -> dict:
    """Intervals between consecutive character key presses (modifiers and F-keys excluded, gaps
    over TYPING_MAX_GAP_MS dropped as 'not typing'); the log-sigma is what a fitted cadence
    draw needs (spec B4)."""
    downs = sorted((r["t"] for r in records if r.get("kind") == "key" and r.get("edge") == "down"
                    and r.get("keyName") not in MODIFIER_KEYS and r.get("keyName") not in FUNCTION_KEYS))
    intervals = [b - a for a, b in zip(downs, downs[1:]) if 0 < b - a <= TYPING_MAX_GAP_MS]
    log_sigma = statistics.pstdev(math.log(i) for i in intervals) if len(intervals) >= 2 else None
    return {"intervals_ms": intervals, "p50": percentile(intervals, 0.5), "log_sigma": log_sigma}
```

- [ ] **Step 4: Run the tests**

Run: `cd ControllerAddon && python -m pytest tests/test_humanity_report.py -q`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/humanity_report.py ControllerAddon/tests/test_humanity_report.py
git commit -m "humanity_report: click bursts, SHIFT-per-walk and typing cadence (telemetry A2)"
```

---

### Task 8: `humanity_report.py` -- the metric table, CLI, and a run on real recordings

**Files:**
- Modify: `tools/humanity_report.py`
- Test: `ControllerAddon/tests/test_humanity_report.py`
- Modify: `CLAUDE.md` (the telemetry section's rule 4 gets the tool's name)

**Interfaces:**
- Produces: `metrics(records) -> dict[str, float|None]` with the fixed key order in `METRIC_ORDER`; `render(labels: list[str], tables: list[dict]) -> str`; `main(argv) -> int`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_metrics_table_has_every_key_in_order_and_tolerates_an_empty_recording():
    m = h.metrics([])
    assert list(m) == list(h.METRIC_ORDER)
    assert all(v is None for v in m.values())


def test_render_prints_two_recordings_side_by_side():
    a = h.metrics([click(0), mouse(100, 1, 1), mouse(600, 2, 2), click(1_000)])
    b = h.metrics([click(0), click(300), click(5_000)])
    out = h.render(["human", "bot"], [a, b])
    assert "human" in out and "bot" in out
    assert "still_p90_ms" in out
    lines = out.splitlines()
    assert len(lines) >= len(h.METRIC_ORDER) + 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ControllerAddon && python -m pytest tests/test_humanity_report.py -q -k "metrics or render"`
Expected: 2 FAIL with `AttributeError`.

- [ ] **Step 3: Implement** (append to `humanity_report.py`)

```python
# ---------------------------------------------------------------- the table

METRIC_ORDER = (
    "duration_min", "active_min",
    "still_p50_ms", "still_p90_ms", "still_max_ms", "busy_fraction",
    "excursions_per_hour", "excursion_p50_ms", "excursion_p90_ms", "off_canvas_fraction",
    "burst_gap_p25_ms", "burst_gap_p50_ms", "burst_gap_p75_ms", "burst_len_mean", "burst_share",
    "shift_per_walk", "typing_p50_ms", "typing_log_sigma",
    "focus_changes_per_hour", "unfocused_fraction",
)


def metrics(records: list[dict]) -> dict:
    windows = active_windows(records)
    still = still_segments(records, windows)
    ex = excursions(records)
    b = bursts(records)
    s = shift_per_walk(records)
    ty = typing(records)
    fs = focus_stats(records)
    total = duration_ms(records)
    active = sum(e - a for a, e in windows)
    m = {
        "duration_min": (total / 60_000) if total else None,
        "active_min": (active / 60_000) if active else None,
        "still_p50_ms": percentile(still, 0.5), "still_p90_ms": percentile(still, 0.9),
        "still_max_ms": (max(still) if still else None), "busy_fraction": busy_fraction(records, windows),
        "excursions_per_hour": ex["per_hour"], "excursion_p50_ms": percentile(ex["lengths_ms"], 0.5),
        "excursion_p90_ms": percentile(ex["lengths_ms"], 0.9), "off_canvas_fraction": ex["off_fraction"],
        "burst_gap_p25_ms": percentile(b["intra_gaps_ms"], 0.25), "burst_gap_p50_ms": percentile(b["intra_gaps_ms"], 0.5),
        "burst_gap_p75_ms": percentile(b["intra_gaps_ms"], 0.75),
        "burst_len_mean": (statistics.mean(b["lengths"]) if b["lengths"] else None), "burst_share": b["burst_share"],
        "shift_per_walk": s["ratio"], "typing_p50_ms": ty["p50"], "typing_log_sigma": ty["log_sigma"],
        "focus_changes_per_hour": fs["per_hour"], "unfocused_fraction": fs["unfocused_fraction"],
    }
    return {k: m[k] for k in METRIC_ORDER}


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 10 else f"{v:.1f}"
    return str(v)


def render(labels: list[str], tables: list[dict]) -> str:
    w = max(len(k) for k in METRIC_ORDER)
    head = f"{'metric':{w}}  " + "  ".join(f"{l:>14}" for l in labels)
    rows = [head, "-" * len(head)]
    for k in METRIC_ORDER:
        rows.append(f"{k:{w}}  " + "  ".join(f"{_fmt(t.get(k)):>14}" for t in tables))
    return "\n".join(rows)


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__.strip().splitlines()[2])
        return 2
    labels, tables = [], []
    for p in args:
        recs = load(p)
        labels.append(Path(p).stem[:14])
        tables.append(metrics(recs))
    print(render(labels, tables))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `cd ControllerAddon && python -m pytest tests/test_humanity_report.py -q`
Expected: 13 passed.

- [ ] **Step 5: Run it on real recordings (verification, not a test)**

Run from the repo root:

```
python tools/humanity_report.py ControllerAddon/recordings/2026-09-06_083723_karamwan-fishing.jsonl ControllerAddon/recordings/2026-09-06_090950_cg-bot1.jsonl
```

Expected: a two-column table with numbers for stillness, bursts, SHIFT-per-walk and typing; `excursions_*`, `off_canvas_fraction`, `focus_*` are `-` for both, because those record kinds did not exist when these were captured (Task 4 adds them; the human recording in Task 9 is the first with them). Sanity anchors from CLAUDE.md's measurements: the human's `still_max_ms` should be under ~4500 and `burst_gap_p50_ms` near 200; the bot's `still_p90_ms` should be visibly larger.

- [ ] **Step 6: Point CLAUDE.md at the tool**

In the "What the game client sends home" section, rule 1 ("Treat every cursor sample as transmitted..."), append the sentence: `Measure it with tools/humanity_report.py <human.jsonl> <bot.jsonl> -- the same metrics over both, which is the only way this class of signature is visible (the routine log cannot see it).`

- [ ] **Step 7: Commit**

```bash
git add tools/humanity_report.py ControllerAddon/tests/test_humanity_report.py CLAUDE.md
git commit -m "humanity_report: metric table + CLI, side-by-side human vs bot (telemetry A2)"
```

---

### Task 9: Rebuild, live-verify A1/A3/A4, and capture the baseline human recording

**Files:** none new. This is the spec's acceptance for A, run against the real client on the host and in the VM. Record results in CLAUDE.md's VM bullet / telemetry section as MEASURED facts.

- [ ] **Step 1: Rebuild and restart the host client**

Run: `./gradlew.bat run` (host). Wait for the login screen.

- [ ] **Step 2: A3 -- the fingerprint block is served and looks like the launch**

Run: `python tools/deobfuscate.py --status`
Expected: `login fingerprint (what the server will see):` followed by 11 lines. Under a Gradle launch, `processCommand = java.exe` and `parentProcessCommand = java.exe` (the Gradle worker), `javaAgent = (none)`, `availableProcessors = 32`, `maxMemoryMb` near 8147, `smbiosUuid = 1EBB33E4-2B90-8093-433F-50EBF62C698A`. Paste these into the CLAUDE.md telemetry section as the MEASURED host values (they are the parity targets sub-project E compares against).

- [ ] **Step 3: A4 -- the bridge is loopback-only on the host**

Run (host): `curl -s -m 3 http://127.0.0.1:8080/state | head -c 80` -> JSON. Then find the host LAN IP with `ipconfig | findstr IPv4` and run `curl -s -m 3 http://<lan-ip>:8080/state` -> connection refused / no output. The client console shows `Bridge running on http://127.0.0.1:8080` with no "ALL interfaces" suffix.

- [ ] **Step 4: A4 in the VM -- the forward still works with the env override**

In the guest (Remote-SSH terminal): `powershell Z:\Httpbridge\tools\vm-sync.ps1`, then `powershell C:\work\Httpbridge\tools\vm-run-client.ps1` (it now exports `BRIDGE_BIND_ALL=1`). From the host: `curl -s -m 3 http://127.0.0.1:18080/client-version` -> JSON including `"fingerprint"`. Then `python tools/deobfuscate.py --status` on the host with `BRIDGE` pointed at 18080 is not supported -- instead read the guest's block with `curl -s http://127.0.0.1:18080/client-version | python -m json.tool` and confirm `availableProcessors = 32` and `maxMemoryMb` equal to the host's (the `clientJvmArgs` parity flags), and `javaVersion = 11.0.30`. Record both blocks side by side in the CLAUDE.md VM bullet.

- [ ] **Step 5: A1 -- the recorder sees the cursor leave and focus change**

Host: `cd ControllerAddon && python -u recorder.py a1-check`, then move the mouse off the game canvas and back twice, Alt+Tab away and back once, Ctrl+C. Expected in the `.log`: two `cursor LEFT the canvas` / `cursor back on the canvas` pairs and one `-- window UNFOCUSED --` / `-- window FOCUSED --` pair. Then `python tools/humanity_report.py ControllerAddon/recordings/<stamp>_a1-check.jsonl` shows `excursions_per_hour` and `focus_changes_per_hour` as numbers, not `-`.

- [ ] **Step 6: The baseline human recording (USER)**

USER records 20-30 minutes of any activity with the second screen in use: `python -u recorder.py human-baseline-2s`. Then:

```
python tools/humanity_report.py ControllerAddon/recordings/<stamp>_human-baseline-2s.jsonl ControllerAddon/recordings/2026-09-06_090950_cg-bot1.jsonl
```

Copy the human column into the spec (section 3, A2) as the TARGET values for sub-projects B-D, and into CLAUDE.md's telemetry section as MEASURED. These numbers are what every later sub-project is judged against; without them V2 and V7 stay hypotheses.

- [ ] **Step 7: Commit the documentation**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-07-telemetry-vector-mitigation-design.md
git commit -m "telemetry A: measured fingerprint, bind and baseline human metrics recorded"
```

---

## Self-review

- **Spec coverage:** A1 (recorder records + narrative) -> Task 4. A2 (every metric in the spec table: still p50/p90/max, busy fraction, excursions, bursts, SHIFT-per-walk, typing, focus) -> Tasks 5-8; the camera row is deliberately not in the tool (the spec itself says it is not observable client-side and points at `/camera` across logins -- recorded as a manual check in Task 9's notes rather than a metric). A3 (fingerprint block + `--status`) -> Tasks 1-2. A4 (loopback + toggle) -> Task 3, with the VM env override the spec did not foresee (a NAT forward cannot reach loopback). Acceptance (report on a human + bot recording, block populated, LAN refused) -> Task 9.
- **Placeholders:** none; every step has its code or exact command.
- **Type consistency:** `active_windows` returns `list[tuple[int,int]]` and every consumer (`still_segments`, `busy_fraction`, `metrics`) takes that; record builders in tests use the recorder's real field names (`kind`, `t`, `edge`, `keyName`, `menuAction`, `points`, `gained`); Java names (`bindAddress`, `truncate`, `fileName`, `javaAgentName`, `uuidFromWmic`, `collect`) match between tests and implementations.
