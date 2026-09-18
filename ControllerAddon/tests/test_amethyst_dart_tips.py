"""Tests for routines/amethyst_dart_tips.py.

Hermetic: every bridge read is stubbed, and the clock is faked so budgets expire instantly
(routines/blast_furnace.py's _now/_sleep seam, same as test_fletching.py).

skillmulti.py's own matching logic (item_widget/option_number/strip_tags/menu_label) is already
covered by test_skillmulti.py -- this file only exercises this routine's OWN decisions: which
product to pick, when to mine vs. chisel, and the start/wait/failure bookkeeping around the
chisel-on-amethyst use-on, mirroring test_fletching.py's start_fletching/_wait_out_batch tests
(the closest existing precedent: a bank-side "use tool on material, pick a make-menu option,
wait out the batch" routine).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import controller
import gathering
from routines import amethyst_dart_tips as adt


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
        self.state = _State(config or adt.get_config(None))
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Nothing in this suite may reach the running client."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_objects", lambda **k: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)


@pytest.fixture(autouse=True)
def _fake_clock(monkeypatch):
    """Budgets expire in zero wall-clock time; _sleep advances the fake clock instead."""
    now = {"t": 0.0}
    monkeypatch.setattr(adt, "_now", lambda: now["t"])
    monkeypatch.setattr(adt, "_sleep", lambda s: now.__setitem__("t", now["t"] + s))
    return now


def _item(name, qty=1, slot=0):
    return {"name": name, "qty": qty, "screenX": 100 + slot, "screenY": 200}


def _inv_seq(monkeypatch, frames):
    """Serve each inventory frame once, then repeat the last one forever."""
    box = {"i": 0}

    def fake():
        i = min(box["i"], len(frames) - 1)
        box["i"] += 1
        return frames[i]

    monkeypatch.setattr(controller, "get_inventory", fake)
    return box


# A synthetic make-menu dump, shaped like the real skillmulti group (see test_fletching.py's
# LIVE_MENU / skillmulti.py's own tests for the live-measured version of this shape). The exact
# item labels here are NOT yet confirmed against a live client -- see the module docstring.
CHISEL_MENU = [
    {"group": 270, "child": 5, "name": None,
     "text": "What would you like to make?", "screenX": 354, "screenY": 1122},
    {"group": 270, "child": 15, "name": "<col=ff9040>Amethyst arrowtips</col>", "text": None,
     "screenX": 120, "screenY": 1239, "actions": ["Make"]},
    {"group": 270, "child": 16, "name": "<col=ff9040>Amethyst javelin heads</col>", "text": None,
     "screenX": 328, "screenY": 1239, "actions": ["Make"]},
    {"group": 270, "child": 17, "name": "<col=ff9040>Amethyst bolt tips</col>", "text": None,
     "screenX": 536, "screenY": 1239, "actions": ["Make"]},
    {"group": 270, "child": 18, "name": "<col=ff9040>Amethyst dart tip</col>", "text": None,
     "screenX": 744, "screenY": 1239, "actions": ["Make"]},
]


# --------------------------------------------------------------------------- config


def test_get_config_defaults():
    cfg = adt.get_config(None)
    assert cfg["rockNames"] == ["Amethyst crystals"]
    assert cfg["chiselName"] == "Jeweller's chisel"
    assert cfg["primaryProduct"] == "Amethyst dart tip"
    assert cfg["primaryProductCap"] == 5000
    assert cfg["secondaryProduct"] == "Amethyst bolt tips"


def test_get_config_keeps_stop_on_inventory_full_off():
    """A full inventory is this routine's trigger to start chiseling, not a safety stop -- the
    engine's check_safety runs before tick() and would otherwise win the race (gathering.py's
    and fletching.py's identical rule)."""
    cfg = adt.get_config({"safety": {"stopOnInventoryFull": True, "stopOnDeath": True}})
    assert cfg["safety"]["stopOnInventoryFull"] is False


def test_get_config_merges_over_defaults():
    cfg = adt.get_config({"primaryProductCap": 1000})
    assert cfg["primaryProductCap"] == 1000
    assert cfg["chiselName"] == "Jeweller's chisel"   # untouched keys keep their default


# --------------------------------------------------------------------------- counting


def test_count_is_exact_name_not_substring():
    inv = [_item("Amethyst", 5), _item("Amethyst dart tip", 200)]
    assert adt.count(inv, "Amethyst") == 5
    assert adt.count(inv, "Amethyst dart tip") == 200


def test_count_sums_quantity_for_a_stackable_product():
    assert adt.count([_item("Amethyst dart tip", 4999)], "Amethyst dart tip") == 4999


# --------------------------------------------------------------------------- product choice


def test_current_product_is_dart_tips_below_the_cap():
    inv = [_item("Amethyst dart tip", 100)]
    product, _ = adt.current_product(inv, adt.get_config(None))
    assert product == "Amethyst dart tip"


def test_current_product_swaps_to_bolt_tips_at_the_cap():
    """USER: 'once we have accumulated 5k darttips swap to bolt tips'."""
    inv = [_item("Amethyst dart tip", 5000)]
    product, _ = adt.current_product(inv, adt.get_config(None))
    assert product == "Amethyst bolt tips"


def test_current_product_swaps_past_the_cap_too():
    inv = [_item("Amethyst dart tip", 6000)]
    product, _ = adt.current_product(inv, adt.get_config(None))
    assert product == "Amethyst bolt tips"


def test_current_product_is_dart_tips_with_none_held_yet():
    product, _ = adt.current_product([], adt.get_config(None))
    assert product == "Amethyst dart tip"


# --------------------------------------------------------------------------- start_chiseling


def test_start_chiseling_needs_both_chisel_and_amethyst(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Jeweller's chisel")])
    ctx = _Ctx()
    assert not adt.start_chiseling(ctx, ctx.state.config)


def test_start_chiseling_presses_the_key_for_the_configured_product(monkeypatch):
    keys, clicks = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent.get("name")) or True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: CHISEL_MENU)
    _inv_seq(monkeypatch, [
        [_item("Jeweller's chisel"), _item("Amethyst", 27, 1)],
        [_item("Jeweller's chisel"), _item("Amethyst", 26, 1), _item("Amethyst dart tip", 15, 2)],
    ])
    ctx = _Ctx()
    assert adt.start_chiseling(ctx, ctx.state.config)
    assert keys == ["4"]   # child 18 -> option 18-14 = 4
    assert clicks == ["Jeweller's chisel", "Amethyst"]   # the two use-on clicks, no menu flight


def test_start_chiseling_picks_bolt_tips_once_the_dart_tip_cap_is_reached(monkeypatch):
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: CHISEL_MENU)
    _inv_seq(monkeypatch, [
        [_item("Jeweller's chisel"), _item("Amethyst", 27, 1), _item("Amethyst dart tip", 5000, 2)],
        [_item("Jeweller's chisel"), _item("Amethyst", 26, 1), _item("Amethyst dart tip", 5000, 2),
         _item("Amethyst bolt tips", 15, 3)],
    ])
    ctx = _Ctx()
    assert adt.start_chiseling(ctx, ctx.state.config)
    assert keys == ["3"]   # child 17 -> option 17-14 = 3


def test_start_chiseling_falls_back_to_the_configured_option_key(monkeypatch):
    """The menu is up but carries no button matching the product name -- fall back to the
    human's own keypress rather than giving up, same rule fletching.py's start_fletching uses."""
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 15, "name": "Something else", "text": "", "screenX": 1, "screenY": 1},
    ])
    _inv_seq(monkeypatch, [
        [_item("Jeweller's chisel"), _item("Amethyst", 27, 1)],
        [_item("Jeweller's chisel"), _item("Amethyst", 26, 1), _item("Amethyst dart tip", 1, 2)],
    ])
    ctx = _Ctx()
    assert adt.start_chiseling(ctx, ctx.state.config)
    assert keys == [str(ctx.state.config["primaryProductOption"])]


def test_start_chiseling_reports_failure_when_the_menu_never_opens(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Jeweller's chisel"), _item("Amethyst", 27, 1)])
    ctx = _Ctx()
    assert not adt.start_chiseling(ctx, ctx.state.config)


def test_start_chiseling_reports_failure_when_no_product_ever_appears(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: CHISEL_MENU)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Jeweller's chisel"), _item("Amethyst", 27, 1)])
    ctx = _Ctx()
    assert not adt.start_chiseling(ctx, ctx.state.config)


# --------------------------------------------------------------------------- the chisel wait


def test_wait_out_chiseling_finishes_when_the_amethyst_runs_out(monkeypatch):
    _inv_seq(monkeypatch, [
        [_item("Jeweller's chisel"), _item("Amethyst", 2), _item("Amethyst dart tip", 25, 1)],
        [_item("Jeweller's chisel"), _item("Amethyst", 1), _item("Amethyst dart tip", 26, 1)],
        [_item("Jeweller's chisel"), _item("Amethyst dart tip", 27, 1)],
    ])
    ctx = _Ctx()
    assert adt._wait_out_chiseling(ctx, ctx.state.config) == "done"


def test_wait_out_chiseling_stalls_when_nothing_progresses(monkeypatch):
    """A level-up (or any other) dialog can interrupt the batch. This must return -- not spin to
    CHISEL_MAX_S -- so the next tick can restart the rest of the amethyst."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Jeweller's chisel"), _item("Amethyst", 20),
                                 _item("Amethyst dart tip", 7, 1)])
    ctx = _Ctx()
    assert adt._wait_out_chiseling(ctx, ctx.state.config) == "stalled"


def test_wait_out_chiseling_bails_out_on_a_safety_stop(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Jeweller's chisel"), _item("Amethyst", 20)])
    ctx = _Ctx()
    ctx.state.safety_stopped = True
    assert adt._wait_out_chiseling(ctx, ctx.state.config) == "stopped"


def test_wait_out_chiseling_fidgets_while_it_waits(monkeypatch):
    """Not tabbed away (this routine stays at the mining spot) -- fidget realism applies exactly
    like every other non-AFK wait in this codebase."""
    fidgets = []
    monkeypatch.setattr(controller, "fidget_once", lambda: fidgets.append(1))
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Jeweller's chisel"), _item("Amethyst", 20)])
    ctx = _Ctx()
    adt._wait_out_chiseling(ctx, ctx.state.config)
    assert fidgets   # at least one poll happened before the stall verdict


# --------------------------------------------------------------------------- chisel_phase


def test_repeated_failed_starts_safety_stop(monkeypatch):
    monkeypatch.setattr(adt, "start_chiseling", lambda ctx, cfg: False)
    ctx = _Ctx()
    for _ in range(adt.START_FAIL_LIMIT - 1):
        adt.chisel_phase(ctx, ctx.state.config)
        assert not ctx.state.safety_stopped
    adt.chisel_phase(ctx, ctx.state.config)
    assert ctx.state.safety_stopped


def test_a_successful_start_clears_the_failure_count(monkeypatch):
    monkeypatch.setattr(adt, "_wait_out_chiseling", lambda *a, **k: "done")
    ctx = _Ctx()
    monkeypatch.setattr(adt, "start_chiseling", lambda ctx_, cfg: False)
    adt.chisel_phase(ctx, ctx.state.config)
    assert ctx.state.data["adt_start_fails"] == 1
    monkeypatch.setattr(adt, "start_chiseling", lambda ctx_, cfg: True)
    adt.chisel_phase(ctx, ctx.state.config)
    assert ctx.state.data["adt_start_fails"] == 0


def test_chisel_phase_does_not_wait_when_the_batch_never_started(monkeypatch):
    monkeypatch.setattr(adt, "start_chiseling", lambda ctx, cfg: False)
    waited = []
    monkeypatch.setattr(adt, "_wait_out_chiseling",
                        lambda *a, **k: waited.append(1) or "done")
    ctx = _Ctx()
    adt.chisel_phase(ctx, ctx.state.config)
    assert waited == []


# --------------------------------------------------------------------------- tick dispatch


def test_tick_mines_while_there_is_room(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Jeweller's chisel"), _item("Amethyst", 10, 1)])
    went = []
    monkeypatch.setattr(adt, "mine_phase", lambda ctx, cfg: went.append("mine"))
    monkeypatch.setattr(adt, "chisel_phase", lambda ctx, cfg: went.append("chisel"))
    adt.tick(_Ctx())
    assert went == ["mine"]


def test_tick_chisels_once_the_inventory_is_full(monkeypatch):
    inv = [_item("Jeweller's chisel")] + [_item("Amethyst", 1, i) for i in range(27)]
    assert len(inv) == controller.INVENTORY_SIZE
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    went = []
    monkeypatch.setattr(adt, "mine_phase", lambda ctx, cfg: went.append("mine"))
    monkeypatch.setattr(adt, "chisel_phase", lambda ctx, cfg: went.append("chisel"))
    adt.tick(_Ctx())
    assert went == ["chisel"]


def test_mine_phase_delegates_to_the_shared_gathering_loop(monkeypatch):
    calls = []
    monkeypatch.setattr(gathering, "tick", lambda profile, ctx: calls.append(profile))
    ctx = _Ctx()
    adt.mine_phase(ctx, ctx.state.config)
    assert calls == [adt.PROFILE]


def test_profile_uses_a_longer_gather_budget_for_amethysts_slow_yield():
    """LIVE 2026-09-17, USER: "we swap before the ore despawn" -- the generic 12s budget
    (gathering.GATHER_BUDGET_S) fired while the player was still genuinely mid-swing, because
    Amethyst's per-swing success rate is slower than ordinary ore. See GATHER_BUDGET_S's own
    comment for the measured gaps this covers."""
    assert adt.PROFILE.gather_budget_s == adt.GATHER_BUDGET_S
    assert adt.PROFILE.gather_budget_s > gathering.GATHER_BUDGET_S
