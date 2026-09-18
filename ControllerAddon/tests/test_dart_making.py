"""Tests for routines/dart_making.py.

Hermetic: every bridge read/action is stubbed. Mirrors test_amethyst_dart_tips.py's shape --
the closest existing precedent for a use-on-use inventory combine loop -- but this routine has
no make-menu step to cover: the combine fires directly on the second click (see the module
docstring for what the 2026-09-17 recording confirmed about that).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
from routines import dart_making as dm


class _State:
    def __init__(self, config):
        self.config = config
        self.data = {}
        self.safety_stopped = False
        self.stop_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.stop_reason = reason


class _Ctx:
    def __init__(self, config=None):
        self.ser = object()
        self.state = _State(config or dm.get_config(None))
        self.events = None


def _item(name, qty=1, slot=0):
    return {"name": name, "qty": qty, "screenX": 100 + slot, "screenY": 200}


# --------------------------------------------------------------------------- config


def test_get_config_defaults():
    cfg = dm.get_config(None)
    assert cfg["primaryItem"] == "Amethyst dart tip"
    assert cfg["secondaryItem"] == "Feather"


def test_get_config_merges_over_defaults():
    cfg = dm.get_config({"primaryItem": "Broad arrowheads"})
    assert cfg["primaryItem"] == "Broad arrowheads"
    assert cfg["secondaryItem"] == "Feather"   # untouched keys keep their default


def test_get_config_preserves_safety_defaults_under_a_partial_override():
    cfg = dm.get_config({"safety": {"stopOnDeath": False}})
    assert cfg["safety"]["stopOnDeath"] is False
    assert cfg["safety"]["stopOnInventoryFull"] is False   # not clobbered by the partial dict


# --------------------------------------------------------------------------- find_item


def test_find_item_matches_by_exact_name_not_substring():
    inv = [_item("Amethyst"), _item("Amethyst dart tip", 200)]
    assert dm.find_item(inv, "Amethyst")["name"] == "Amethyst"
    assert dm.find_item(inv, "Amethyst dart tip")["qty"] == 200


def test_find_item_survives_a_slot_reshuffle():
    """CONFIRMED in the recording: once the dart tip stack hits zero, Feather (previously slot
    4) moves into slot 0 as everything ahead of it shifts up -- this must still find it by name,
    not by a remembered slot."""
    inv = [_item("Feather", 9000, slot=0)]
    found = dm.find_item(inv, "Feather")
    assert found is not None
    assert found["screenX"] == 100


def test_find_item_returns_none_when_absent():
    assert dm.find_item([_item("Feather")], "Amethyst dart tip") is None


# --------------------------------------------------------------------------- run_combo


def test_run_combo_clicks_both_items_in_order(monkeypatch):
    clicks = []
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Amethyst dart tip", 100, 0), _item("Feather", 9000, 4)])
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["name"]) or True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    ctx = _Ctx()
    assert dm.run_combo(ctx, ctx.state.config) is True
    assert clicks == ["Amethyst dart tip", "Feather"]


def test_run_combo_waits_the_configured_gap_between_the_two_clicks(monkeypatch):
    gaps = []
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Amethyst dart tip", 100, 0), _item("Feather", 9000, 4)])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "human_delay", lambda lo, hi: gaps.append((lo, hi)))
    ctx = _Ctx()
    dm.run_combo(ctx, ctx.state.config)
    assert gaps == [tuple(ctx.state.config["comboGapS"])]


def test_run_combo_false_when_primary_is_missing(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Feather", 9000)])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    ctx = _Ctx()
    assert dm.run_combo(ctx, ctx.state.config) is False


def test_run_combo_false_when_secondary_is_missing(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Amethyst dart tip", 100)])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    ctx = _Ctx()
    assert dm.run_combo(ctx, ctx.state.config) is False


def test_run_combo_false_on_an_empty_inventory_read(monkeypatch):
    """get_inventory() returning [] means "cannot tell" (a bridge hiccup), and this must not
    crash treating it as anything else."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    assert dm.run_combo(ctx, ctx.state.config) is False


# --------------------------------------------------------------------------- tick


def test_tick_resets_the_missing_counter_on_a_successful_combo(monkeypatch):
    monkeypatch.setattr(dm, "run_combo", lambda ctx, cfg: True)
    ctx = _Ctx()
    ctx.state.data["dm_missing_ticks"] = 3
    dm.tick(ctx)
    assert ctx.state.data["dm_missing_ticks"] == 0
    assert not ctx.state.safety_stopped


def test_tick_counts_consecutive_misses(monkeypatch):
    monkeypatch.setattr(dm, "run_combo", lambda ctx, cfg: False)
    ctx = _Ctx()
    for expected in range(1, dm.DEPLETED_STOP_LIMIT):
        dm.tick(ctx)
        assert ctx.state.data["dm_missing_ticks"] == expected
        assert not ctx.state.safety_stopped


def test_tick_safety_stops_once_the_miss_limit_is_reached(monkeypatch):
    monkeypatch.setattr(dm, "run_combo", lambda ctx, cfg: False)
    ctx = _Ctx()
    for _ in range(dm.DEPLETED_STOP_LIMIT):
        dm.tick(ctx)
    assert ctx.state.safety_stopped
    assert "out of material" in ctx.state.stop_reason


def test_tick_a_recovered_read_clears_the_miss_count_before_the_limit(monkeypatch):
    """A one-off empty inventory read (bridge hiccup) must not accumulate toward the stop once
    a real read comes back -- only a PERSISTENT miss should ever stop the run."""
    results = iter([False, False, True, False, False, False])
    monkeypatch.setattr(dm, "run_combo", lambda ctx, cfg: next(results))
    ctx = _Ctx()
    for _ in range(6):
        dm.tick(ctx)
    assert not ctx.state.safety_stopped
    assert ctx.state.data["dm_missing_ticks"] == 3
