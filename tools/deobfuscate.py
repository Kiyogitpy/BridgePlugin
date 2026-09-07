"""Deobfuscate and decompile the injected client this machine is running, and record it.

    python tools/deobfuscate.py            # from the repo root; the whole thing, end to end
    python tools/deobfuscate.py --jar path/to/injected-client-1.12.39.jar
    python tools/deobfuscate.py --status   # what is on record vs what the client reports

Added 2026-09-07. The readable copy of the client under ``data/decompiled/`` is how we know
what the client sends home (CLAUDE.md, "What the game client sends home"), and every RuneLite
build re-obfuscates it. This script is the one command ``ControllerAddon/deob_guard.py`` names
when it refuses to drive a build nobody has read yet. Steps, each of which is idempotent:

1. Find a JDK 17+ (rl-suite pins a 17 toolchain and has no foojay resolver, so Gradle will not
   fetch one itself). Looked for in DEOB_JAVA_HOME, JAVA_HOME, ~/.jdks, Program Files; when
   none exists, Temurin 17 is downloaded from the Adoptium API into ~/.jdks -- the same
   directory IntelliJ uses, which Gradle's toolchain detection also scans.
2. Find the injected-client jar: ``--jar``, else whatever the running bridge reports on
   ``GET /client-version`` (the exact file the ``client`` class was loaded from), else the
   newest ``injected-client-*.jar`` in the Gradle cache.
3. Build rl-suite's shaded jar once (``gradlew :transform-jar:shadedJar``).
4. Run the transform. rl-suite stops rather than guesses, and its two documented refusals are
   answered here: "Expected N packet-handler shapes, found M" is retried with
   ``--expected-packet-handlers M`` (1.12.37-SNAPSHOT and 1.12.38 both have 6 where the tool
   expects 5), and a packet-profile fingerprint that matches nothing -- a NEW wire protocol --
   is retried with ``--packet-profile none`` and recorded as ``protocolKnown: false`` so the
   result is still readable, just with generic ``fieldN`` packet names until a new
   ``osrs-<rev>.tsv`` profile exists.
5. Decompile with the bundled Vineflower into
   ``data/decompiled/rl-suite-main/semantics/injected-client-<ver>-sources/`` (the tool's own
   convention, beside the author's checked-in 1.12.37 snapshot).
6. Write ``data/decompiled/deob-state.json`` keyed on the jar's SHA-256.

Only the pure helpers (``java_major``, ``retry_hint``, ``newest_jar``, ``bundled_profiles``,
``build_state``) are unit-tested; the rest is subprocess plumbing that is exercised by running
the script. ``data/`` is gitignored, so nothing here touches version control.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import telemetry_fingerprint  # noqa: E402  (sibling module in tools/)

REPO_ROOT = Path(__file__).resolve().parent.parent
DECOMPILED = REPO_ROOT / "data" / "decompiled"
BASELINE_VERSION = "1.12.37"          # the author's checked-in snapshot CLAUDE.md was written from
BASELINE_SOURCES = DECOMPILED / "rl-suite-main" / "semantics" / "injected-client-1.12.37-sources"
TOOL_DIR = DECOMPILED / "rl-suite-main"
PROFILE_DIR = TOOL_DIR / "transform-jar" / "src" / "main" / "resources" / "dev" / "rl" / "suite" / "packet"
DEOB_DIR = DECOMPILED / "deob"
STATE_PATH = DECOMPILED / "deob-state.json"
JDKS_DIR = Path.home() / ".jdks"
ADOPTIUM_17 = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse"
BRIDGE = "http://localhost:8080"
MIN_JAVA = 17
MAX_TRANSFORM_ATTEMPTS = 4

JAR_NAME_RE = re.compile(r"injected-client-(?P<ver>[0-9][^/\\]*?)\.jar$")
HANDLER_HINT_RE = re.compile(r"Expected (\d+) packet-handler shapes, found (\d+)")
FINGERPRINT_HINT_RE = re.compile(r"packet class matching the complete \d+-entry id/length fingerprint; found 0")
JAVA_VERSION_RE = re.compile(r'version "(\d+)(?:\.(\d+))?')


# ---------------------------------------------------------------- pure helpers (tested)

def java_major(version_output: str) -> Optional[int]:
    """Major version from `java -version` text: `"17.0.20.1"` -> 17, `"1.8.0_481"` -> 8."""
    m = JAVA_VERSION_RE.search(version_output or "")
    if not m:
        return None
    major = int(m.group(1))
    if major == 1 and m.group(2):
        return int(m.group(2))
    return major


def retry_hint(tool_output: str, attempted_profile: str) -> Optional[dict]:
    """What to change for the next transform attempt, from the tool's refusal text; None when
    the failure is not one of the two documented refusals."""
    m = HANDLER_HINT_RE.search(tool_output or "")
    if m:
        return {"expected_packet_handlers": int(m.group(2))}
    if FINGERPRINT_HINT_RE.search(tool_output or "") and attempted_profile != "none":
        return {"packet_profile": "none"}
    return None


def version_of(jar: Path) -> Optional[str]:
    m = JAR_NAME_RE.search(str(jar))
    return m.group("ver") if m else None


def _version_key(ver: str):
    parts = re.split(r"[.-]", ver)
    key = []
    for p in parts:
        key.append((1, int(p)) if p.isdigit() else (0, p))   # "SNAPSHOT" sorts below a number
    key.append((2, 0))     # terminal: a release outranks its own "-SNAPSHOT" (which is longer)
    return key


def newest_jar(jars: Iterable[Path]) -> Optional[Path]:
    """Highest injected-client version by numeric tuple (1.12.38 > 1.12.9; a -SNAPSHOT sorts
    below its release)."""
    best, best_key = None, None
    for j in jars:
        v = version_of(j)
        if v is None:
            continue
        k = _version_key(v)
        if best_key is None or k > best_key:
            best, best_key = j, k
    return best


def bundled_profiles(profile_dir: Path = PROFILE_DIR) -> list[str]:
    """rl-suite's packet-name profiles, newest revision first: ['osrs-240', 'osrs-239', ...]."""
    names = []
    for p in profile_dir.glob("osrs-*.tsv"):
        m = re.fullmatch(r"osrs-(\d+)\.tsv", p.name)
        if m:
            names.append((int(m.group(1)), p.stem))
    return [n for _, n in sorted(names, reverse=True)]


def build_state(*, jar: Path, sha256: str, version: Optional[str], revision, profile: str,
                expected_handlers: Optional[int], deob_jar: Path, sources: Path, audit: Path,
                audit_metrics: Optional[dict], tool_version: str, now: Optional[str] = None) -> dict:
    metrics = audit_metrics or {}
    return {
        "schema": 1,
        "runeliteVersion": version,
        "revision": revision,
        "injectedClientJar": str(jar),
        "injectedClientSha256": sha256,
        "packetProfile": profile,
        "protocolKnown": profile != "none",
        "packetNamesApplied": metrics.get("metrics.packet.profile.names"),
        "packetHandlersMatched": metrics.get("metrics.packet.handlers.matched"),
        "expectedPacketHandlers": expected_handlers,
        "deobJar": str(deob_jar),
        "sources": str(sources),
        "audit": str(audit),
        "toolVersion": tool_version,
        "deobfuscatedAt": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- plumbing

def log(msg: str) -> None:
    print(f"[deob] {msg}", flush=True)


def run(cmd: list[str], *, env: Optional[dict] = None, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    log("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _java_exe(home: Path) -> Path:
    return home / "bin" / ("java.exe" if os.name == "nt" else "java")


def _jdk_major(home: Path) -> Optional[int]:
    exe = _java_exe(home)
    if not exe.is_file():
        return None
    try:
        out = subprocess.run([str(exe), "-version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return java_major(out.stderr + out.stdout)


def _jdk_candidates() -> list[Path]:
    homes = []
    for var in ("DEOB_JAVA_HOME", "JAVA_HOME"):
        if os.environ.get(var):
            homes.append(Path(os.environ[var]))
    roots = [JDKS_DIR, Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Eclipse Adoptium",
             Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Java",
             Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft",
             Path.home() / ".gradle" / "jdks"]
    for root in roots:
        if root.is_dir():
            homes.extend(sorted(p for p in root.iterdir() if p.is_dir()))
    return homes


def find_jdk() -> Optional[Path]:
    best, best_major = None, -1
    for home in _jdk_candidates():
        major = _jdk_major(home)
        if major is not None and major >= MIN_JAVA and major > best_major:
            best, best_major = home, major
    return best


def download_jdk() -> Path:
    JDKS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = JDKS_DIR / "temurin17.zip"
    log(f"no JDK {MIN_JAVA}+ found -- downloading Temurin 17 to {JDKS_DIR}")
    with urllib.request.urlopen(ADOPTIUM_17, timeout=60) as resp, open(zip_path, "wb") as out:
        shutil.copyfileobj(resp, out)
    before = {p.name for p in JDKS_DIR.iterdir()}
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(JDKS_DIR)
    zip_path.unlink()
    new = [JDKS_DIR / n for n in {p.name for p in JDKS_DIR.iterdir()} - before]
    for home in new:
        if _jdk_major(home) and _jdk_major(home) >= MIN_JAVA:
            log(f"installed {home}")
            return home
    raise SystemExit("downloaded a JDK but could not find a working java.exe inside it")


def ensure_jdk() -> Path:
    home = find_jdk()
    if home is None:
        home = download_jdk()
    log(f"JDK {_jdk_major(home)} at {home}")
    return home


def bridge_version() -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"{BRIDGE}/client-version", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("injectedClientSha256") else None


def cached_jars() -> list[Path]:
    root = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1" / "net.runelite" / "injected-client"
    return list(root.glob("*/*/injected-client-*.jar")) if root.is_dir() else []


def find_jar(explicit: Optional[str], live: Optional[dict]) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SystemExit(f"--jar {p} does not exist")
        return p
    if live and live.get("injectedClientJar") and Path(live["injectedClientJar"]).is_file():
        log("using the jar the running client reports")
        return Path(live["injectedClientJar"])
    jar = newest_jar(cached_jars())
    if jar is None:
        raise SystemExit("no injected-client jar found: pass --jar, or run the client once so "
                         "Gradle caches it")
    log("using the newest jar in the Gradle cache (bridge not answering)")
    return jar


def tool_jar(jdk: Path) -> Path:
    libs = TOOL_DIR / "transform-jar" / "build" / "libs"
    existing = sorted(libs.glob("rl-suite-*-all.jar")) if libs.is_dir() else []
    if existing:
        return existing[-1]
    if not (TOOL_DIR / "gradlew.bat").is_file():
        raise SystemExit(f"rl-suite is not present at {TOOL_DIR} (data/ is gitignored)")
    env = dict(os.environ, JAVA_HOME=str(jdk), PATH=f"{jdk / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    wrapper = TOOL_DIR / ("gradlew.bat" if os.name == "nt" else "gradlew")
    log("building rl-suite's shaded jar (first run only; downloads Gradle + deps)")
    r = run([wrapper, "--no-daemon", "-q", ":transform-jar:shadedJar"], env=env, cwd=TOOL_DIR)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-4000:] + r.stderr[-4000:])
        raise SystemExit("rl-suite build failed")
    existing = sorted(libs.glob("rl-suite-*-all.jar"))
    if not existing:
        raise SystemExit("rl-suite build reported success but produced no shaded jar")
    return existing[-1]


def transform(java: Path, tool: Path, jar: Path, deob_jar: Path, audit: Path, profile: str,
              expected_handlers: Optional[int]) -> tuple[str, Optional[int]]:
    """Run the transform, answering the tool's documented refusals. Returns the (profile,
    expected_handlers) that finally succeeded."""
    for attempt in range(1, MAX_TRANSFORM_ATTEMPTS + 1):
        cmd = [java, "-jar", tool, "--input", jar, "--output", deob_jar, "--report", audit,
               "--packet-profile", profile]
        if expected_handlers is not None:
            cmd += ["--expected-packet-handlers", str(expected_handlers)]
        r = run(cmd)
        if r.returncode == 0:
            return profile, expected_handlers
        text = r.stdout + r.stderr
        hint = retry_hint(text, profile)
        if hint is None:
            sys.stderr.write(text[-6000:])
            raise SystemExit(f"transform failed on attempt {attempt} with a refusal this script "
                             "does not know how to answer (see above)")
        if "expected_packet_handlers" in hint:
            expected_handlers = hint["expected_packet_handlers"]
            log(f"tool found {expected_handlers} packet readers -- retrying with that count")
        if "packet_profile" in hint:
            profile = hint["packet_profile"]
            log("packet fingerprint matched no bundled profile: this is a NEW wire protocol. "
                "Retrying with --packet-profile none (generic packet names)")
    raise SystemExit("transform did not converge")


def decompile(java: Path, tool: Path, deob_jar: Path, sources: Path) -> None:
    r = run([java, "-jar", tool, "--decompile", "--input", deob_jar, "--source-output", sources])
    if r.returncode != 0:
        sys.stderr.write((r.stdout + r.stderr)[-6000:])
        raise SystemExit("decompile failed")


def audit_metrics(audit: Path) -> Optional[dict]:
    try:
        data = json.loads(audit.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    flat = {}

    def walk(d, prefix):
        for k, v in d.items():
            if isinstance(v, dict):
                walk(v, prefix + k + ".")
            else:
                flat[prefix + k] = v
    walk(data, "")
    return flat


def previous_fingerprint() -> tuple[Optional[str], Optional[dict]]:
    """The last recorded build's telemetry fingerprint, or -- on the very first run -- one
    extracted from the author's checked-in 1.12.37 snapshot so the first diff is against the
    build CLAUDE.md's telemetry section was written from."""
    try:
        prior = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        fp = json.loads(Path(prior["telemetryFingerprint"]).read_text(encoding="utf-8"))
        return prior.get("runeliteVersion"), fp
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if BASELINE_SOURCES.is_dir():
        return BASELINE_VERSION, telemetry_fingerprint.extract(BASELINE_SOURCES)
    return None, None


def fingerprint_and_diff(version: Optional[str], sources: Path) -> tuple[Path, Optional[Path], Optional[bool]]:
    """Extract what this build collects/sends, diff it against the previous build, write both
    beside the record, print the diff. Returns (fingerprint path, diff path, changed?)."""
    fp = telemetry_fingerprint.extract(sources)
    fp_path = DEOB_DIR / f"telemetry-{version}.json"
    fp_path.write_text(json.dumps(fp, indent=2), encoding="utf-8")
    old_label, old = previous_fingerprint()
    if old is None:
        log(f"telemetry fingerprint written to {fp_path} (no previous build to diff against)")
        return fp_path, None, None
    lines = telemetry_fingerprint.diff(old, fp)
    report = telemetry_fingerprint.render(old_label or "previous", version or "new", lines, old, fp)
    diff_path = DEOB_DIR / f"telemetry-diff-{old_label}-to-{version}.md"
    diff_path.write_text(report, encoding="utf-8")
    print(report, flush=True)
    # Reference-count moves alone are the obfuscator re-inlining call sites (MEASURED 1.12.37 ->
    # 1.12.38: eight of them, nothing collected changed); only the other kinds count as a change
    # in what the client sends.
    substantive = [l for l in lines if " referenced " not in l]
    log(f"telemetry diff {old_label} -> {version}: "
        + (f"{len(lines)} line(s), {len(substantive)} substantive, see {diff_path}" if lines
           else "no change in what the client sends"))
    if substantive:
        log("!! the client's telemetry surface CHANGED -- re-read the sources and amend CLAUDE.md "
            "\"What the game client sends home\"")
    return fp_path, diff_path, bool(substantive)


FINGERPRINT_FIELDS = ("processCommand", "parentProcessCommand", "javaAgent", "osName", "osVersion",
                      "osArch", "javaVendor", "javaVersion", "maxMemoryMb", "availableProcessors",
                      "smbiosUuid")


def format_fingerprint(fp: dict) -> list[str]:
    """The login fingerprint as the client will send it, one `key = value` line per field in the
    order the client writes them. Empty for a client that does not serve the block."""
    if not fp:
        return []
    return [f"{k:22} = {'(none)' if fp.get(k) is None else fp.get(k)}" for k in FINGERPRINT_FIELDS]


def status(live: Optional[dict]) -> int:
    state = None
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    print(f"record : {STATE_PATH}")
    if state:
        print(f"         RuneLite {state.get('runeliteVersion')} rev {state.get('revision')} "
              f"sha {state.get('injectedClientSha256', '')[:12]} profile {state.get('packetProfile')} "
              f"at {state.get('deobfuscatedAt')}")
    else:
        print("         (none)")
    print("client : " + (f"RuneLite {live.get('runeliteVersion')} rev {live.get('revision')} "
                         f"sha {live.get('injectedClientSha256', '')[:12]}" if live
                         else "(bridge not answering, or client not rebuilt with /client-version)"))
    if live and live.get("fingerprint"):
        print("login fingerprint (what the server will see):")
        for line in format_fingerprint(live["fingerprint"]):
            print("         " + line)
    if live and state:
        same = live.get("injectedClientSha256") == state.get("injectedClientSha256")
        print("verdict: " + ("MATCH -- routines may run" if same else "MISMATCH -- run this script"))
        return 0 if same else 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jar", help="injected-client jar to deobfuscate (default: what the running "
                                  "client reports, else the newest in the Gradle cache)")
    ap.add_argument("--packet-profile", help="rl-suite packet-name profile (default: newest bundled)")
    ap.add_argument("--expected-packet-handlers", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="redo even when the record already matches")
    ap.add_argument("--status", action="store_true", help="compare the record with the running client and exit")
    args = ap.parse_args(argv)

    live = bridge_version()
    if args.status:
        return status(live)

    jar = find_jar(args.jar, live)
    sha = sha256_of(jar)
    version = version_of(jar)
    log(f"jar {jar} (RuneLite {version}, sha {sha[:12]})")

    if not args.force and STATE_PATH.is_file():
        try:
            prior = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prior = {}
        if prior.get("injectedClientSha256") == sha and Path(prior.get("sources", "")).is_dir():
            log(f"already deobfuscated ({prior.get('deobfuscatedAt')}) -- nothing to do (--force to redo)")
            return 0

    jdk = ensure_jdk()
    java = _java_exe(jdk)
    tool = tool_jar(jdk)
    profiles = bundled_profiles()
    profile = args.packet_profile or (profiles[0] if profiles else "none")

    DEOB_DIR.mkdir(parents=True, exist_ok=True)
    deob_jar = DEOB_DIR / f"injected-client-{version}-deob.jar"
    audit = DEOB_DIR / f"injected-client-{version}.audit.json"
    sources = TOOL_DIR / "semantics" / f"injected-client-{version}-sources"

    t0 = time.monotonic()
    profile, handlers = transform(java, tool, jar, deob_jar, audit, profile, args.expected_packet_handlers)
    log(f"transform ok in {time.monotonic() - t0:.0f}s (profile {profile}, packet readers {handlers})")
    t1 = time.monotonic()
    decompile(java, tool, deob_jar, sources)
    n = sum(1 for _ in sources.glob("*.java"))
    log(f"decompiled {n} classes to {sources} in {time.monotonic() - t1:.0f}s")

    state = build_state(jar=jar, sha256=sha, version=version,
                        revision=(live or {}).get("revision") if live and live.get("injectedClientSha256") == sha else None,
                        profile=profile, expected_handlers=handlers, deob_jar=deob_jar,
                        sources=sources, audit=audit, audit_metrics=audit_metrics(audit),
                        tool_version=tool.name)
    fp_path, diff_path, changed = fingerprint_and_diff(version, sources)
    state["telemetryFingerprint"] = str(fp_path)
    state["telemetryDiff"] = str(diff_path) if diff_path else None
    state["telemetryChanged"] = changed
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log(f"recorded {STATE_PATH}")
    if not state["protocolKnown"]:
        log("!! NEW WIRE PROTOCOL: packet fields carry generic names. Re-derive the telemetry "
            "section in CLAUDE.md by BEHAVIOUR (grep 'wmic csproduct', the 50 ms recorder "
            "thread), and add an osrs-<rev>.tsv profile to rl-suite when rsprot publishes it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
