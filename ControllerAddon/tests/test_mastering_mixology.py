import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import controller
import mastering_mixology as mm


class FakeState:
    def __init__(self):
        self.data = {}
        self.config = None
        self.safety_stopped = False
        self.stop_reason = None
        self.paused = False

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.stop_reason = reason


class FakeCtx:
    def __init__(self):
        self.ser = object()
        self.state = FakeState()
        self.events = None


def _unprocessed(count):
    return [{"id": next(iter(mm.UNFINISHED_IDS))} for _ in range(count)]


# --- process_phase's multi-station loop ------------------------------------------------
#
# CONFIRMED same structural shape as plank_make.py's cast_phase (see that file's own
# test_cast_phase_stops_mid_load_when_paused): process_phase can walk through every
# unprocessed potion in the inventory inside ONE tick() call, so the engine's own F2 pause
# gate -- which only runs BETWEEN tick() calls -- can't interrupt it until the whole batch is
# done. Same fix, same idiom: re-check ctx.state.paused (and safety_stopped, which this loop
# was missing entirely -- a safety stop triggered mid-batch by _process_one wasn't stopping
# the NEXT station either) at the top of the loop.

def test_process_phase_stops_when_paused(monkeypatch):
    ctx = FakeCtx()
    ctx.state.data["mm_chosen_orders"] = [
        {"station": "Retort", "name": "Mammoth-might mix"},
        {"station": "Agitator", "name": "Mystic mana amalgam"},
    ]

    calls = []

    def fake_process_one(_ctx, _config, station_name, _station, _order_name):
        calls.append(station_name)
        ctx.state.paused = True  # F2 pressed mid-batch, right after the first station

    monkeypatch.setattr(controller, "get_inventory", lambda: _unprocessed(2))
    monkeypatch.setattr(controller, "get_mixology_state", lambda: {"stations": {}})
    monkeypatch.setattr(mm, "find_object", lambda name: {"name": name})
    monkeypatch.setattr(mm, "_process_one", fake_process_one)

    mm.process_phase(ctx, {})

    assert calls == ["Retort"]              # never reached the second station
    assert not ctx.state.safety_stopped     # pausing is not a safety stop


def test_process_phase_stops_when_safety_stopped_mid_batch(monkeypatch):
    """A safety stop can be triggered BY _process_one itself (e.g. a stray click landing on the
    wrong potion) -- the loop must not barrel on to the next station once that happens."""
    ctx = FakeCtx()
    ctx.state.data["mm_chosen_orders"] = [
        {"station": "Retort", "name": "Mammoth-might mix"},
        {"station": "Agitator", "name": "Mystic mana amalgam"},
    ]

    calls = []

    def fake_process_one(_ctx, _config, station_name, _station, _order_name):
        calls.append(station_name)
        ctx.state.trigger_safety_stop("stray click started the wrong potion")

    monkeypatch.setattr(controller, "get_inventory", lambda: _unprocessed(2))
    monkeypatch.setattr(controller, "get_mixology_state", lambda: {"stations": {}})
    monkeypatch.setattr(mm, "find_object", lambda name: {"name": name})
    monkeypatch.setattr(mm, "_process_one", fake_process_one)

    mm.process_phase(ctx, {})

    assert calls == ["Retort"]
    assert ctx.state.safety_stopped
