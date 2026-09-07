"""Refuse to drive a game client nobody has deobfuscated and READ yet.

Added 2026-09-07 (USER: "add a guard incase a new revision is detected to update the
deobfuscate before routines can be run"). The readable copy of the injected client under
``data/decompiled/`` is how we know what the client sends home (CLAUDE.md, "What the game
client sends home"); every RuneLite build re-obfuscates it, so that copy goes stale per BUILD.
``GET /client-version`` (ClientVersionProvider.java) reports the SHA-256 of the jar the running
``client`` class was loaded from, ``tools/deobfuscate.py`` records the SHA-256 of the jar it
last deobfuscated in ``data/decompiled/deob-state.json``, and this module compares the two
before ``controller.py`` opens the serial port, and again on the first live bridge read when
the bridge was down at startup.

Verdicts (``evaluate`` is pure and table-tested):

- ``ok``        -- the running jar is the one on record. Drive.
- ``block``     -- the jar differs from the record, or there is no record, AND rl-suite is
                   present on this machine so the fix is one command. ``ensure`` prints that
                   command and exits before any input is sent.
- ``warn``      -- the check cannot be honoured here: the client was not rebuilt with the
                   endpoint (a ``/state`` that answers while ``/client-version`` carries no
                   sha), or ``data/decompiled/rl-suite-main`` is absent (``data/`` is
                   gitignored, so a fresh clone has no tool to run). Log once, drive.
- ``unverified``-- the bridge answered nothing at all. Re-check on the next call; the engine
                   calls ``ensure`` every loop iteration until a verdict lands, and it costs one
                   HTTP GET at most every RECHECK_INTERVAL_S while pending.

Design choice worth keeping: a MISSING endpoint is a warning, not a block. The engine must keep
working against a client that predates this provider, and a 404 here is indistinguishable
from an un-rebuilt client. The block is reserved for the case the guard exists for -- the
bridge proves the jar changed and the tool to read it is right there.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import debug_log

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "data" / "decompiled" / "deob-state.json"
TOOL_DIR = REPO_ROOT / "data" / "decompiled" / "rl-suite-main"
DEOB_COMMAND = "python tools/deobfuscate.py"      # run from the repo root
RECHECK_INTERVAL_S = 5.0
SKIP_ENV = "SKIP_DEOB_CHECK"


@dataclass(frozen=True)
class Verdict:
    kind: str          # "ok" | "block" | "warn" | "unverified"
    message: str

    @property
    def blocks(self) -> bool:
        return self.kind == "block"

    @property
    def settled(self) -> bool:
        return self.kind != "unverified"


def load_state(path: Path = STATE_PATH) -> Optional[dict]:
    """The record tools/deobfuscate.py wrote, or None when there is none (or it is unreadable --
    an unreadable record is treated exactly like a missing one, so a half-written file cannot
    pass as a match)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def tool_present(tool_dir: Path = TOOL_DIR) -> bool:
    return (tool_dir / "transform-jar").is_dir()


def evaluate(version_reply: Optional[dict], state_reply: Optional[dict],
             state: Optional[dict], have_tool: bool) -> Verdict:
    """Pure decision. ``version_reply`` is GET /client-version (None when it failed),
    ``state_reply`` is GET /state (used only to tell "bridge down" from "endpoint missing"),
    ``state`` is the on-disk record, ``have_tool`` whether rl-suite is on this machine."""
    sha = None
    if isinstance(version_reply, dict):
        sha = version_reply.get("injectedClientSha256")
    if not sha:
        if not state_reply:
            return Verdict("unverified", "bridge not answering yet -- will re-check")
        return Verdict("warn", "client has no /client-version (not rebuilt since 2026-09-07); "
                               "cannot verify the deobfuscation matches -- driving anyway")

    version = version_reply.get("runeliteVersion")
    revision = version_reply.get("revision")
    running = f"RuneLite {version} rev {revision} jar sha {sha[:12]}"

    recorded = (state or {}).get("injectedClientSha256")
    if recorded == sha:
        return Verdict("ok", f"deobfuscation matches the running client ({running})")

    if state is None:
        why = f"no deobfuscation record at {STATE_PATH.name} for the running client ({running})"
    else:
        why = (f"running client is NOT the one deobfuscated ({running}; recorded "
               f"RuneLite {state.get('runeliteVersion')} sha {str(recorded)[:12]})")
    if have_tool:
        return Verdict("block", f"{why}. Read the new build before driving it: `{DEOB_COMMAND}`")
    return Verdict("warn", f"{why}, and rl-suite is not present under data/decompiled/ so it "
                           "cannot be updated on this machine -- driving anyway")


class Guard:
    """Stateful wrapper around ``evaluate``: latches the first settled verdict and rate-limits
    re-checks while the bridge is down."""

    def __init__(self, skip: bool = False):
        self.skip = skip or os.environ.get(SKIP_ENV) == "1"
        self.verdict: Optional[Verdict] = None
        self._last_try = 0.0
        self._warned_pending = False

    def ensure(self, *, now: Optional[float] = None) -> Verdict:
        """Called before the serial port opens and once per engine loop until settled. Exits the
        process on a block -- deliberately before any input can be sent."""
        if self.skip:
            if self.verdict is None:
                self.verdict = Verdict("warn", "--skip-deob-check: not verifying the deobfuscation")
                debug_log.log("!", self.verdict.message)
            return self.verdict
        if self.verdict is not None and self.verdict.settled:
            return self.verdict
        t = time.monotonic() if now is None else now
        if self.verdict is not None and t - self._last_try < RECHECK_INTERVAL_S:
            return self.verdict
        self._last_try = t

        import controller  # local: controller imports nothing from here, but keep the cycle lazy
        version_reply = controller.api_get("/client-version", timeout=3.0)
        state_reply = controller.api_get("/state", timeout=1.0) if not version_reply else {"ok": True}
        self.verdict = evaluate(version_reply, state_reply, load_state(), tool_present())

        if self.verdict.kind == "unverified":
            if not self._warned_pending:
                debug_log.log("deob", self.verdict.message)
                self._warned_pending = True
        elif self.verdict.kind == "ok":
            debug_log.log("deob", self.verdict.message)
        elif self.verdict.kind == "warn":
            debug_log.log("!", "deob guard: " + self.verdict.message)
        else:
            debug_log.log("!", "DEOB GUARD: " + self.verdict.message)
            print("\n" + "=" * 78 + f"\nREFUSING TO START: {self.verdict.message}\n"
                  f"(or pass --skip-deob-check / set {SKIP_ENV}=1 to override)\n" + "=" * 78,
                  file=sys.stderr, flush=True)
            sys.exit(2)
        return self.verdict
