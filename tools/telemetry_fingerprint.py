"""What the game client collects and sends, extracted from a decompiled source tree by
BEHAVIOUR so two builds can be diffed.

Added 2026-09-07 (USER: "when we make a new deobfuscate we compare the diff from last rev").
A plain source diff between two builds is noise: every class, field and method is renamed by
each obfuscation pass (``PlatformInfo`` in the author's mapped 1.12.37 snapshot is ``classVT``
in our own 1.12.38 run). What does NOT change between builds unless the client's behaviour
changes is the set of facts CLAUDE.md's "What the game client sends home" is built from, and
each of those has a name-independent tell in the source:

- system properties / environment variables read (``System.getProperty("os.name")``);
- commands the client executes (``wmic csproduct get UUID``, the machine-id readers);
- the client->server packet table -- rl-suite applies the published NAMES as field names on
  the packet class, and the constructor carries ``(id, length)``;
- how many places reference each packet constant (a new write site for EVENT_MOUSE_MOVE is a
  new telemetry path even if the packet's shape did not move);
- the mouse recorder's constants: 500-slot buffer, 50 ms sample interval, the 40-sample flush.

``extract`` reads a sources dir into a JSON-able dict; ``diff`` compares two of them and
returns human lines; ``tools/deobfuscate.py`` runs both after every deobfuscation and writes the
result beside the record. Every extractor degrades to ``None``/empty rather than raising, so a
decompile whose shape changed still produces a fingerprint -- and the diff then SAYS the
recorder constants went missing, which is itself the finding.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

PROP_RE = re.compile(r'System\.getProperty\("([^"]+)"\)')
ENV_RE = re.compile(r'System\.getenv\("([^"]+)"\)')
STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.){6,})"')   # single-line literals only
PROCESS_TELLS = {                                        # what a build introspects about its own process
    "process_command": "ProcessHandle.current()",        # exe name, first 10 chars -> PlatformInfo
    "parent_process_command": ".parent()",               # parent exe name, first 10 chars
    "jvm_input_arguments": "getInputArguments()",        # the -javaagent file name, up to 22 chars
}
PACKET_DECL_RE = re.compile(r"static final (\w+) (\w+) = new \1\((-?\d+), (-?\d+)\)")
ANNOTATION_RE = re.compile(r"^\s*@")
RECORDER_LONG_RE = re.compile(r"new long\[(\d+)\]")
RECORDER_INT_RE = re.compile(r"new int\[(\d+)\]")
INTERVAL_RE = re.compile(r"for \(;.*?\((\d+)L\)\)")
THRESHOLD_RE = re.compile(r"< (\d+)\)")
PLACEHOLDER_UUID = "12345678-0000-0000-0000-123456789012"
TELEMETRY_PACKETS = ("EVENT_MOUSE_MOVE", "EVENT_MOUSE_CLICK_V2", "EVENT_KEYBOARD", "EVENT_MOUSE_SCROLL",
                     "EVENT_CAMERA_POSITION", "EVENT_APPLET_FOCUS", "WINDOW_STATUS", "IDLE",
                     "NO_TIMEOUT", "REFLECTION_CHECK_REPLY", "DETECT_MODIFIED_CLIENT",
                     "CONNECTION_TELEMETRY", "SEND_PING_REPLY", "EVENT_NATIVE_MOUSE_MOVE")
FINGERPRINT_SCHEMA = 1


def _clean(text: str) -> str:
    """Drop annotation lines: rl-suite's @ObfuscatedName/@Named payloads contain arbitrary
    strings that would pollute the literal scans."""
    return "\n".join(line for line in text.splitlines() if not ANNOTATION_RE.match(line))


def _read_all(sources: Path) -> dict[str, str]:
    out = {}
    for p in sorted(sources.glob("*.java")):
        try:
            out[p.stem] = _clean(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return out


def _packet_class(files: dict[str, str]) -> Optional[str]:
    for name, text in files.items():
        if re.search(r"static final \w+ EVENT_MOUSE_MOVE = new", text):
            return name
    return None


def _packet_table(text: str) -> dict[str, dict]:
    table = {}
    for m in PACKET_DECL_RE.finditer(text):
        _, name, pid, length = m.groups()
        table[name] = {"id": int(pid), "length": int(length)}
    return table


def _packet_refs(files: dict[str, str], cls: str, names: list[str]) -> dict[str, int]:
    refs = {}
    others = [t for n, t in files.items() if n != cls]
    for name in names:
        pat = re.compile(r"\b" + re.escape(cls) + r"\." + re.escape(name) + r"\b")
        refs[name] = sum(len(pat.findall(t)) for t in others)
    return refs


def _mouse_recorder(files: dict[str, str], cls: Optional[str]) -> dict:
    out = {"buffer": None, "sample_interval_ms": None, "flush_threshold": None, "class": None}
    for name, text in files.items():
        longs = RECORDER_LONG_RE.findall(text)
        ints = RECORDER_INT_RE.findall(text)
        if len(longs) == 1 and ints.count(longs[0]) == 2 and "Runnable" in text or (
                len(longs) == 1 and ints.count(longs[0]) == 2 and "void run()" in text):
            out["class"] = name
            out["buffer"] = int(longs[0])
            m = INTERVAL_RE.search(text)
            out["sample_interval_ms"] = int(m.group(1)) if m else None
            break
    if cls:
        pat = re.compile(r"\b" + re.escape(cls) + r"\.EVENT_MOUSE_MOVE\b")
        for name, text in files.items():
            if name == cls:
                continue
            m = pat.search(text)
            if not m:
                continue
            window = text[max(0, m.start() - 4000):m.start()]
            hits = THRESHOLD_RE.findall(window)
            out["flush_threshold"] = int(hits[-1]) if hits else None
            break
    return out


def _exec_commands(files: dict[str, str]) -> list[str]:
    cmds = set()
    for text in files.values():
        if ".exec(" not in text:
            continue
        for lit in STRING_RE.findall(text):
            if len(lit) >= 8 and (" " in lit or "/" in lit):   # a command line, not a bare token
                cmds.add(lit)
    return sorted(cmds)


def _process_introspection(files: dict[str, str]) -> dict[str, bool]:
    """Which self-inspection calls the client makes. MEASURED 2026-09-07 in both 1.12.37 and
    1.12.38: the login class fills three of its string fields from ProcessHandle (own exe name,
    parent exe name) and the first JVM argument whose 10-char prefix hashes to -1094877034,
    which is "-javaagent"."""
    return {key: any(tell in text for text in files.values()) for key, tell in PROCESS_TELLS.items()}


def extract(sources: Path) -> dict:
    files = _read_all(Path(sources))
    joined = "\n".join(files.values())
    cls = _packet_class(files)
    table = _packet_table(files[cls]) if cls else {}
    names = sorted(table)
    return {
        "schema": FINGERPRINT_SCHEMA,
        "sources": str(sources),
        "class_count": len(files),
        "system_properties": dict(sorted(Counter(PROP_RE.findall(joined)).items())),
        "env_vars": dict(sorted(Counter(ENV_RE.findall(joined)).items())),
        "exec_commands": _exec_commands(files),
        "placeholder_uuid_present": PLACEHOLDER_UUID in joined,
        "process_introspection": _process_introspection(files),
        "packet_class": cls,
        "client_packets": table,
        "packet_refs": _packet_refs(files, cls, names) if cls else {},
        "mouse_recorder": _mouse_recorder(files, cls),
    }


def diff(old: dict, new: dict) -> list[str]:
    """Human lines describing what changed; empty means the telemetry surface is unchanged."""
    lines = []

    def dict_diff(label, a: dict, b: dict, fmt=lambda v: v):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                lines.append(f"+ {label} {k}: {fmt(b[k])}")
            elif k not in b:
                lines.append(f"- {label} {k}: {fmt(a[k])}")
            elif a[k] != b[k]:
                lines.append(f"~ {label} {k}: {fmt(a[k])} -> {fmt(b[k])}")

    dict_diff("property", old.get("system_properties", {}), new.get("system_properties", {}))
    dict_diff("env", old.get("env_vars", {}), new.get("env_vars", {}))
    oc, nc = set(old.get("exec_commands", [])), set(new.get("exec_commands", []))
    for c in sorted(nc - oc):
        lines.append(f"+ exec {c!r}")
    for c in sorted(oc - nc):
        lines.append(f"- exec {c!r}")
    if old.get("placeholder_uuid_present") != new.get("placeholder_uuid_present"):
        lines.append(f"~ placeholder UUID literal present: {old.get('placeholder_uuid_present')} "
                     f"-> {new.get('placeholder_uuid_present')}")
    dict_diff("process introspection", old.get("process_introspection", {}),
              new.get("process_introspection", {}))

    op, np_ = old.get("client_packets", {}), new.get("client_packets", {})
    if not np_:
        lines.append("! no client packet table found in the new sources (packet class not located)")
    dict_diff("packet", op, np_, fmt=lambda v: f"id {v['id']} len {v['length']}")

    orf, nrf = old.get("packet_refs", {}), new.get("packet_refs", {})
    for name in sorted(set(orf) | set(nrf)):
        if name in op and name in np_ and orf.get(name) != nrf.get(name):
            tag = "TELEMETRY " if name in TELEMETRY_PACKETS else ""
            lines.append(f"~ {tag}{name} referenced {orf.get(name)} -> {nrf.get(name)} places")

    om, nm = old.get("mouse_recorder", {}), new.get("mouse_recorder", {})
    for k in ("buffer", "sample_interval_ms", "flush_threshold"):
        if om.get(k) != nm.get(k):
            lines.append(f"~ mouse recorder {k}: {om.get(k)} -> {nm.get(k)}")
    return lines


def render(old_label: str, new_label: str, lines: list[str], old: dict, new: dict) -> str:
    head = [f"# Telemetry surface: {old_label} -> {new_label}", ""]
    if not lines:
        head.append("No change in what the client collects or sends (properties, exec commands, "
                    "packet table, packet reference counts, mouse recorder constants).")
    else:
        head.append(f"{len(lines)} change(s). `+` added, `-` removed, `~` changed, `!` extraction gap.")
        head.append("")
        head.extend(lines)
    head += ["", "## New build, for the record", "",
             f"- classes: {new.get('class_count')}",
             f"- packets: {len(new.get('client_packets', {}))} (packet class `{new.get('packet_class')}`)",
             f"- mouse recorder: {json.dumps(new.get('mouse_recorder'))}",
             f"- exec: {new.get('exec_commands')}",
             f"- properties: {sorted(new.get('system_properties', {}))}"]
    return "\n".join(head) + "\n"
