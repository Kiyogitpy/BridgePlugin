"""tools/deobfuscate.py + tools/telemetry_fingerprint.py: the pure helpers. The subprocess
plumbing is exercised by running the script itself (2026-09-07: 1.12.38, six packet readers)."""
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

import deobfuscate as d          # noqa: E402
import telemetry_fingerprint as tf  # noqa: E402


# ---- deobfuscate.py -------------------------------------------------------------------------

def test_java_major_reads_modern_and_legacy_version_strings():
    assert d.java_major('openjdk version "17.0.20.1" 2026-08-18') == 17
    assert d.java_major('openjdk version "21" 2023-09-19') == 21
    assert d.java_major('java version "1.8.0_481"') == 8
    assert d.java_major('openjdk version "11.0.30" 2026-01-20') == 11
    assert d.java_major("not java at all") is None


def test_retry_hint_answers_the_two_documented_refusals_only():
    # MEASURED 2026-09-07 on 1.12.38: the tool expects 5 packet readers and finds 6.
    assert d.retry_hint("dev.rl.suite.TransformException: Expected 5 packet-handler shapes, found 6",
                        "osrs-240") == {"expected_packet_handlers": 6}
    fp = ("TransformException: Expected one client packet class matching the complete "
          "117-entry id/length fingerprint; found 0 []")
    assert d.retry_hint(fp, "osrs-240") == {"packet_profile": "none"}
    assert d.retry_hint(fp, "none") is None          # already profile-less: nothing left to try
    assert d.retry_hint("java.lang.OutOfMemoryError", "osrs-240") is None


def test_newest_jar_orders_by_version_tuple_not_string():
    jars = [Path("x/injected-client-1.12.9.jar"), Path("x/injected-client-1.12.38.jar"),
            Path("x/injected-client-1.12.38-SNAPSHOT.jar"), Path("x/not-a-client.jar")]
    assert d.newest_jar(jars).name == "injected-client-1.12.38.jar"
    assert d.newest_jar([]) is None
    assert d.version_of(Path("C:/c/injected-client-1.12.38.jar")) == "1.12.38"


def test_bundled_profiles_newest_first(tmp_path):
    for n in ("osrs-238", "osrs-240", "osrs-239", "renamed-client-1.12.31.1"):
        (tmp_path / f"{n}.tsv").write_text("", encoding="utf-8")
    assert d.bundled_profiles(tmp_path) == ["osrs-240", "osrs-239", "osrs-238"]
    assert d.bundled_profiles(tmp_path / "missing") == []


def test_build_state_records_what_the_guard_and_status_read():
    s = d.build_state(jar=Path("c/injected-client-1.12.38.jar"), sha256="ab" * 32, version="1.12.38",
                      revision=240, profile="osrs-240", expected_handlers=6,
                      deob_jar=Path("d/x-deob.jar"), sources=Path("s"), audit=Path("a.json"),
                      audit_metrics={"metrics.packet.profile.names": 252,
                                     "metrics.packet.handlers.matched": 6},
                      tool_version="rl-suite-1.3.1-all.jar", now="2026-09-07T00:00:00+00:00")
    assert s["injectedClientSha256"] == "ab" * 32
    assert s["protocolKnown"] is True and s["packetNamesApplied"] == 252
    assert d.build_state(jar=Path("j"), sha256="x", version=None, revision=None, profile="none",
                         expected_handlers=None, deob_jar=Path("d"), sources=Path("s"),
                         audit=Path("a"), audit_metrics=None, tool_version="t")["protocolKnown"] is False


# ---- telemetry_fingerprint.py ---------------------------------------------------------------

def _fake_sources(root: Path, *, interval=50, buffer=500, threshold=40, extra_cmd=None,
                  props=("os.name", "java.vendor"), packets=None, introspect=True):
    packets = packets or {"EVENT_MOUSE_MOVE": (114, -1), "IDLE": (7, 0), "OPLOC1_V2": (74, 8)}
    root.mkdir(parents=True, exist_ok=True)
    decls = "\n".join(f"   public static final classJS {n} = new classJS({i}, {l});" for n, (i, l) in packets.items())
    (root / "classJS.java").write_text(f"public class classJS {{\n{decls}\n}}\n", encoding="utf-8")
    cmds = ['"wmic csproduct get UUID"', '"cat /etc/machine-id"'] + ([f'"{extra_cmd}"'] if extra_cmd else [])
    prop_reads = "\n".join(f'      String p = System.getProperty("{p}");' for p in props)
    intro = ("      ProcessHandle h = ProcessHandle.current(); h.parent();\n"
             "      ManagementFactory.getRuntimeMXBean().getInputArguments();\n") if introspect else ""
    (root / "classVT.java").write_text(
        "public class classVT {\n   @ObfuscatedName(\"vs\")\n"
        f"   String x = \"12345678-0000-0000-0000-123456789012\";\n{prop_reads}\n{intro}"
        "   void f() {\n      String c = " + "; c = ".join(cmds) + ";\n"
        "      Process p = Runtime.getRuntime().exec(c);\n   }\n}\n", encoding="utf-8")
    (root / "classDK.java").write_text(
        "public class classDK implements Runnable {\n   public void run() {\n"
        f"      for (; this.alive; classRP.sleep({interval}L)) {{ }}\n   }}\n"
        f"   classDK() {{ this.a = new int[{buffer}]; this.b = new int[{buffer}]; this.c = new long[{buffer}]; }}\n}}\n",
        encoding="utf-8")
    (root / "client.java").write_text(
        "public class client {\n   void loop() {\n"
        f"      if (rec.count < {threshold}) {{ return; }}\n"
        "      node = classEF.make(classJS.EVENT_MOUSE_MOVE, cipher);\n"
        "      node = classEF.make(classJS.IDLE, cipher);\n      node = classEF.make(classJS.IDLE, cipher);\n"
        "   }\n}\n", encoding="utf-8")
    return root


def test_extract_reads_the_behaviour_tells_regardless_of_class_names(tmp_path):
    fp = tf.extract(_fake_sources(tmp_path / "a"))
    assert fp["packet_class"] == "classJS"
    assert fp["client_packets"]["EVENT_MOUSE_MOVE"] == {"id": 114, "length": -1}
    assert fp["packet_refs"] == {"EVENT_MOUSE_MOVE": 1, "IDLE": 2, "OPLOC1_V2": 0}
    assert fp["mouse_recorder"] == {"buffer": 500, "sample_interval_ms": 50, "flush_threshold": 40,
                                    "class": "classDK"}
    assert fp["exec_commands"] == ["cat /etc/machine-id", "wmic csproduct get UUID"]
    assert fp["system_properties"] == {"java.vendor": 1, "os.name": 1}
    assert fp["placeholder_uuid_present"] is True
    assert fp["process_introspection"] == {"process_command": True, "parent_process_command": True,
                                           "jvm_input_arguments": True}


def test_identical_builds_diff_to_nothing(tmp_path):
    a = tf.extract(_fake_sources(tmp_path / "a"))
    b = tf.extract(_fake_sources(tmp_path / "b"))
    assert tf.diff(a, b) == []
    assert "No change" in tf.render("1", "2", [], a, b)


def test_diff_names_every_kind_of_change(tmp_path):
    a = tf.extract(_fake_sources(tmp_path / "a"))
    b = tf.extract(_fake_sources(tmp_path / "b", interval=20, threshold=10, extra_cmd="wmic path win32_bios get serialnumber",
                                 props=("os.name", "java.vendor", "user.name"),
                                 packets={"EVENT_MOUSE_MOVE": (114, -2), "IDLE": (7, 0), "EVENT_NEW": (5, 3)},
                                 introspect=False))
    lines = tf.diff(a, b)
    text = "\n".join(lines)
    assert "+ property user.name" in text
    assert "+ exec 'wmic path win32_bios get serialnumber'" in text
    assert "~ packet EVENT_MOUSE_MOVE: id 114 len -1 -> id 114 len -2" in text
    assert "+ packet EVENT_NEW" in text and "- packet OPLOC1_V2" in text
    assert "~ mouse recorder sample_interval_ms: 50 -> 20" in text
    assert "~ mouse recorder flush_threshold: 40 -> 10" in text
    assert "~ process introspection jvm_input_arguments: True -> False" in text


def test_extractors_degrade_to_none_when_the_shape_is_gone(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "client.java").write_text("public class client {}\n", encoding="utf-8")
    fp = tf.extract(root)
    assert fp["packet_class"] is None and fp["client_packets"] == {}
    assert fp["mouse_recorder"]["buffer"] is None
    assert any(line.startswith("!") for line in tf.diff(tf.extract(_fake_sources(tmp_path / "a")), fp))


@pytest.mark.skipif(not d.BASELINE_SOURCES.is_dir(), reason="rl-suite snapshot not on this machine")
def test_the_real_1_12_37_snapshot_yields_the_measured_constants():
    # The numbers CLAUDE.md's telemetry section states, pinned against the tree they came from.
    fp = tf.extract(d.BASELINE_SOURCES)
    assert fp["mouse_recorder"]["buffer"] == 500
    assert fp["mouse_recorder"]["sample_interval_ms"] == 50
    assert fp["mouse_recorder"]["flush_threshold"] == 40
    assert "wmic csproduct get UUID" in fp["exec_commands"]
    assert fp["client_packets"]["EVENT_MOUSE_CLICK_V2"] == {"id": 40, "length": 7}
    assert len(fp["client_packets"]) == 117


def test_format_fingerprint_lists_every_field_the_client_sends_in_a_fixed_order():
    fp = {"processCommand": "java.exe", "parentProcessCommand": "java.exe", "javaAgent": None,
          "osName": "windows 10", "osVersion": "10.0", "osArch": "amd64",
          "javaVendor": "Eclipse Adoptium", "javaVersion": "11.0.30", "maxMemoryMb": 8147,
          "availableProcessors": 32, "smbiosUuid": "A1B2C3D4-5E6F-7A8B-9C0D-1E2F3A4B5C6D"}
    lines = d.format_fingerprint(fp)
    assert lines[0].startswith("processCommand")
    assert any(l.startswith("smbiosUuid") and "A1B2C3D4" in l for l in lines)
    assert any("javaAgent" in l and "(none)" in l for l in lines)
    assert len(lines) == 11
    assert d.format_fingerprint({}) == []          # un-rebuilt client: nothing to print, no crash
