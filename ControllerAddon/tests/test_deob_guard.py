"""deob_guard: the verdict table, and that a block exits BEFORE anything is driven."""
import json

import pytest

import deob_guard
from deob_guard import Verdict, evaluate


SHA_A = "a" * 64
SHA_B = "b" * 64


def reply(sha=SHA_A, version="1.12.38", revision=240):
    return {"runeliteVersion": version, "revision": revision, "injectedClientSha256": sha,
            "injectedClientJar": f"C:/cache/injected-client-{version}.jar"}


def record(sha=SHA_A, version="1.12.38"):
    return {"injectedClientSha256": sha, "runeliteVersion": version}


# ---- the pure decision --------------------------------------------------------------------

def test_matching_sha_is_ok():
    v = evaluate(reply(), {"ok": True}, record(), have_tool=True)
    assert v.kind == "ok" and not v.blocks


def test_new_build_with_the_tool_present_blocks_and_names_the_command():
    v = evaluate(reply(sha=SHA_B, version="1.12.39"), {"ok": True}, record(), have_tool=True)
    assert v.blocks
    assert deob_guard.DEOB_COMMAND in v.message
    assert "1.12.39" in v.message and "1.12.38" in v.message   # both sides named in the log line


def test_no_record_with_the_tool_present_blocks():
    v = evaluate(reply(), {"ok": True}, None, have_tool=True)
    assert v.blocks


def test_new_build_without_the_tool_only_warns():
    # data/ is gitignored: a fresh clone has no rl-suite to run, so a block would be a brick.
    v = evaluate(reply(sha=SHA_B), {"ok": True}, record(), have_tool=False)
    assert v.kind == "warn"


def test_unrebuilt_client_warns_rather_than_blocking():
    # /state answers but /client-version carries nothing: the Java side predates the provider.
    assert evaluate(None, {"gameState": "LOGGED_IN"}, record(), True).kind == "warn"
    assert evaluate({"open": False}, {"gameState": "LOGGED_IN"}, record(), True).kind == "warn"


def test_bridge_down_is_unverified_not_a_verdict():
    v = evaluate(None, None, record(), True)
    assert v.kind == "unverified" and not v.settled


def test_unreadable_record_counts_as_no_record(tmp_path):
    p = tmp_path / "deob-state.json"
    p.write_text("{not json", encoding="utf-8")
    assert deob_guard.load_state(p) is None
    p.write_text(json.dumps(record()), encoding="utf-8")
    assert deob_guard.load_state(p)["injectedClientSha256"] == SHA_A
    assert deob_guard.load_state(tmp_path / "missing.json") is None


# ---- the stateful wrapper -----------------------------------------------------------------

@pytest.fixture
def bridge(monkeypatch):
    """Scripted api_get: a dict of path -> reply (None = failed request)."""
    import controller
    answers = {}
    calls = []

    def fake_get(path, params=None, timeout=0.3):
        calls.append(path)
        return answers.get(path)

    monkeypatch.setattr(controller, "api_get", fake_get)
    monkeypatch.setattr(deob_guard, "load_state", lambda path=None: record())
    monkeypatch.setattr(deob_guard, "tool_present", lambda tool_dir=None: True)
    answers["calls"] = calls
    return answers


def test_block_exits_the_process(bridge):
    bridge["/client-version"] = reply(sha=SHA_B)
    g = deob_guard.Guard()
    with pytest.raises(SystemExit) as e:
        g.ensure(now=0.0)
    assert e.value.code == 2


def test_ok_latches_and_never_asks_again(bridge):
    bridge["/client-version"] = reply()
    g = deob_guard.Guard()
    assert g.ensure(now=0.0).kind == "ok"
    n = len(bridge["calls"])
    for t in (1.0, 10.0, 100.0):
        assert g.ensure(now=t).kind == "ok"
    assert len(bridge["calls"]) == n


def test_bridge_down_at_startup_is_rechecked_later_and_can_still_block(bridge):
    g = deob_guard.Guard()
    assert g.ensure(now=0.0).kind == "unverified"          # nothing answered
    assert g.ensure(now=1.0).kind == "unverified"          # inside the rate limit: no new GET
    assert bridge["calls"].count("/client-version") == 1
    bridge["/client-version"] = reply(sha=SHA_B)           # the client came up, and it is new
    with pytest.raises(SystemExit):
        g.ensure(now=0.0 + deob_guard.RECHECK_INTERVAL_S + 0.1)


def test_skip_flag_never_touches_the_bridge(bridge):
    g = deob_guard.Guard(skip=True)
    assert g.ensure(now=0.0).kind == "warn"
    assert bridge["calls"] == []
