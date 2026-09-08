"""Tests for routines/fletching.py.

Hermetic: every bridge read is stubbed, and the clock is faked so the routine's budgets expire
instantly (routines/blast_furnace.py's _now/_sleep seam).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import banker
import controller
from routines import fletching


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
        self.state = _State(config or fletching.get_config(None))
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Nothing in this suite may reach the running client."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_objects", lambda **k: [])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "alt_tab", lambda ser: None)
    monkeypatch.setattr(controller, "wait_for_focus", lambda **k: True)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    # banker.is_open() reads /bank, i.e. the RUNNING CLIENT -- start_fletching's
    # bank-still-open guard would otherwise make this suite depend on what the account is
    # doing right now. Tests that care re-patch it.
    monkeypatch.setattr(banker, "is_open", lambda: False)


@pytest.fixture(autouse=True)
def _fake_clock(monkeypatch):
    """Budgets expire in zero wall-clock time; _sleep advances the fake clock instead."""
    now = {"t": 0.0}
    monkeypatch.setattr(fletching, "_now", lambda: now["t"])
    monkeypatch.setattr(fletching, "_sleep", lambda s: now.__setitem__("t", now["t"] + s))
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


# --------------------------------------------------------------------------- config


def test_get_config_defaults_to_maple_longbow():
    """USER: 'Default is maple'."""
    cfg = fletching.get_config(None)
    assert cfg["logName"] == "Maple logs"
    assert cfg["product"] == "Maple longbow (u)"
    assert cfg["productOption"] == 3


def test_get_config_keeps_stop_on_inventory_full_off_even_if_the_bridge_asks_for_it():
    """A full inventory is this routine's normal working state for most of every minute -- the
    engine's check_safety runs before tick() and would stop the run on its first pass."""
    cfg = fletching.get_config({"safety": {"stopOnInventoryFull": True, "stopOnDeath": True}})
    assert cfg["safety"]["stopOnInventoryFull"] is False


# --------------------------------------------------------------------------- counting


def test_count_is_exact_name_not_substring():
    """'Maple logs' must not be counted by a 'Magic logs' entry, and the unstrung bow must not
    match the strung one."""
    inv = [_item("Maple logs", 20), _item("Magic logs", 3), _item("Maple longbow", 1)]
    assert fletching.count(inv, "Maple logs") == 20
    assert fletching.count(inv, "Maple longbow (u)") == 0


def test_count_sums_quantity_for_a_stackable_product():
    """Arrow shafts come off the same menu and DO stack -- slot counting alone would read 1."""
    assert fletching.count([_item("Headless arrow", 45)], "Headless arrow") == 45


# --------------------------------------------------------------------------- menu matching


# The REAL menu, dumped live 2026-09-08 while it was open with maple logs. Colour-tagged
# labels, finished-item names, and exactly five options.
LIVE_MENU = [
    {"group": 270, "child": 4, "name": None,
     "text": "Choose a quantity, then click an item to begin.", "screenX": 354, "screenY": 1122},
    {"group": 270, "child": 5, "name": None,
     "text": "What would you like to make?", "screenX": 354, "screenY": 1122},
    {"group": 270, "child": 15, "name": "<col=ff9040>60 arrow shafts</col>", "text": None,
     "screenX": 120, "screenY": 1239, "actions": ["Make"]},
    {"group": 270, "child": 16, "name": "<col=ff9040>Maple shortbow</col>", "text": None,
     "screenX": 328, "screenY": 1239, "actions": ["Make"]},
    {"group": 270, "child": 17, "name": "<col=ff9040>Maple longbow</col>", "text": None,
     "screenX": 536, "screenY": 1239, "actions": ["Make"]},
    {"group": 270, "child": 18, "name": "<col=ff9040>Crossbow stock</col>", "text": None,
     "screenX": 744, "screenY": 1239, "actions": ["Make"]},
    {"group": 270, "child": 19, "name": "<col=ff9040>Maple shield</col>", "text": None,
     "screenX": 952, "screenY": 1239, "actions": ["Make"]},
]


def test_strip_tags_removes_the_colour_wrapper():
    assert fletching.strip_tags("<col=ff9040>Maple longbow</col>") == "Maple longbow"


def test_menu_label_drops_the_unstrung_suffix():
    """The menu names the FINISHED item; the inventory receives the unstrung one."""
    assert fletching.menu_label("Maple longbow (u)") == "maple longbow"
    assert fletching.menu_label("Yew shortbow (u)") == "yew shortbow"
    assert fletching.menu_label("Arrow shaft") == "arrow shaft"


def test_menu_item_widget_matches_the_real_live_menu(monkeypatch):
    """The first live run's actual failure: the default product 'Maple longbow (u)' matched
    nothing against '<col=ff9040>Maple longbow</col>', so every batch fell back to the key."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: LIVE_MENU)
    assert fletching.menu_item_widget("Maple longbow (u)")["child"] == 17


def test_menu_item_widget_does_not_confuse_the_shortbow_for_the_longbow(monkeypatch):
    """The whole reason the product is matched by name rather than by the human's '3'."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: LIVE_MENU)
    assert fletching.menu_item_widget("Maple shortbow (u)")["child"] == 16


def test_menu_item_widget_matches_the_pluralised_stackable_option(monkeypatch):
    """The shafts option reads '60 arrow shafts' -- a count prefix and a plural."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: LIVE_MENU)
    assert fletching.menu_item_widget("Arrow shaft")["child"] == 15


def test_menu_item_widget_returns_none_for_a_product_this_log_cannot_make(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: LIVE_MENU)
    assert fletching.menu_item_widget("Yew longbow (u)") is None


def test_the_menu_title_never_wins_over_an_item_button(monkeypatch):
    """270.5 reads 'What would you like to make?' -- harmless here, but the range preference
    is what guarantees a real Make button is chosen when both could match."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: LIVE_MENU)
    w = fletching.menu_item_widget("Maple longbow (u)")
    assert 15 <= w["child"] <= 32 and w["actions"] == ["Make"]


def test_menu_item_widget_matches_when_the_name_is_carried_in_text(monkeypatch):
    """_bowl_widget's lesson: some group-270 buttons carry the label in `text`, not `name`."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 17, "text": "Maple longbow (u)", "screenX": 3, "screenY": 1},
    ])
    assert fletching.menu_item_widget("Maple longbow (u)")["child"] == 17


def test_menu_item_widget_prefers_a_real_item_button_over_any_other_match(monkeypatch):
    """Children 15..32 are the item buttons (cache: `a`..`r`). A match somewhere else in the
    interface -- a title or tooltip echoing the product name -- must never win over one."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 5, "text": "Make Maple longbow (u)", "screenX": 9, "screenY": 9},
        {"group": 270, "child": 17, "name": "Maple longbow (u)", "text": "", "screenX": 3, "screenY": 1},
    ])
    assert fletching.menu_item_widget("Maple longbow (u)")["child"] == 17


def test_menu_item_widget_still_uses_an_out_of_range_match_when_it_is_all_there_is(monkeypatch):
    """The child range is documented by the cache but UNVERIFIED live for this menu. Discarding
    an otherwise-good name match would fall through to the number key, which picks a row by
    position instead of by name -- the exact failure the name matching exists to prevent."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 40, "name": "Maple longbow (u)", "text": "", "screenX": 3, "screenY": 1},
    ])
    assert fletching.menu_item_widget("Maple longbow (u)")["child"] == 40


def test_menu_item_widget_ignores_other_groups(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 162, "child": 17, "text": "Maple longbow (u)", "screenX": 3, "screenY": 1},
    ])
    assert fletching.menu_item_widget("Maple longbow (u)") is None


def test_menu_is_open_needs_a_drawn_widget(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 17, "name": "Maple longbow (u)", "screenX": None},
    ])
    assert not fletching.menu_is_open()


# --------------------------------------------------------------------------- start_fletching


def test_menu_option_number_is_derived_from_the_matched_child():
    """Cache: buttons `a`..`r` at children 15..32, so option N is child 14+N. Live: `Maple
    longbow`, the human's option 3, sat at child 17."""
    assert fletching.menu_option_number({"child": 15}) == 1
    assert fletching.menu_option_number({"child": 17}) == 3
    assert fletching.menu_option_number({"child": 19}) == 5


def test_menu_option_number_declines_what_a_digit_cannot_address():
    """Past 9 there is no digit key, and outside the button range the mapping is meaningless --
    the caller clicks the widget instead of guessing."""
    assert fletching.menu_option_number({"child": 24}) is None   # option 10
    assert fletching.menu_option_number({"child": 5}) is None    # the title
    assert fletching.menu_option_number(None) is None


def test_start_fletching_presses_the_key_for_the_named_option(monkeypatch):
    """USER 2026-09-08: "cant we just either use last craft button space or 3 for longbow".
    The name match still CHOOSES the option; it supplies the position rather than a click
    target, so no mouse flight is spent and a shifted option list still cannot pick wrong."""
    keys, clicks = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent.get("name")) or True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: LIVE_MENU)
    _inv_seq(monkeypatch, [
        [_item("Knife"), _item("Maple logs", 27, 1)],
        [_item("Knife"), _item("Maple logs", 26, 1), _item("Maple longbow (u)", 1, 2)],
    ])
    ctx = _Ctx()
    assert fletching.start_fletching(ctx, ctx.state.config)
    assert keys == ["3"]
    # ... and the two inventory clicks only -- no flight to the menu button.
    assert clicks == ["Knife", "Maple logs"]


def test_start_fletching_presses_a_different_key_for_a_different_product(monkeypatch):
    """The point of deriving it: the shortbow is option 2 on the same menu."""
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: LIVE_MENU)
    _inv_seq(monkeypatch, [
        [_item("Knife"), _item("Maple logs", 27, 1)],
        [_item("Knife"), _item("Maple logs", 26, 1), _item("Maple shortbow (u)", 1, 2)],
    ])
    ctx = _Ctx()
    cfg = dict(ctx.state.config, product="Maple shortbow (u)")
    assert fletching.start_fletching(ctx, cfg)
    assert keys == ["2"]


def test_start_fletching_clicks_the_button_when_no_key_can_address_it(monkeypatch):
    keys, clicks = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent.get("child", ent.get("name"))) or True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 40, "name": "<col=ff9040>Maple longbow</col>",
         "screenX": 3, "screenY": 1},
    ])
    _inv_seq(monkeypatch, [
        [_item("Knife"), _item("Maple logs", 27, 1)],
        [_item("Knife"), _item("Maple logs", 26, 1), _item("Maple longbow (u)", 1, 2)],
    ])
    ctx = _Ctx()
    assert fletching.start_fletching(ctx, ctx.state.config)
    assert keys == [] and clicks[-1] == 40


def test_start_fletching_falls_back_to_the_number_key_when_the_product_is_not_on_the_menu(monkeypatch):
    """The human's own keypress. Keeps the routine working if the widget dump ever stops
    carrying names for these buttons."""
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 15, "name": "Arrow shaft", "text": "", "screenX": 1, "screenY": 1},
    ])
    _inv_seq(monkeypatch, [
        [_item("Knife"), _item("Maple logs", 27, 1)],
        [_item("Knife"), _item("Maple logs", 26, 1), _item("Maple longbow (u)", 1, 2)],
    ])
    ctx = _Ctx()
    assert fletching.start_fletching(ctx, ctx.state.config)
    assert keys == ["3"]


def test_start_fletching_reports_failure_when_the_menu_never_opens(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 27, 1)])
    ctx = _Ctx()
    assert not fletching.start_fletching(ctx, ctx.state.config)


def test_start_fletching_reports_failure_when_no_product_ever_appears(monkeypatch):
    """A selection that did not take. The batch never started, so the caller must not go on to
    wait ~48s for products that are not coming."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 17, "name": "Maple longbow (u)", "text": "", "screenX": 3, "screenY": 1},
    ])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 27, 1)])
    ctx = _Ctx()
    assert not fletching.start_fletching(ctx, ctx.state.config)


def test_start_fletching_closes_the_bank_before_touching_the_inventory(monkeypatch):
    """With the bank open the inventory panel is the bank's own side panel, so a click on the
    knife DEPOSITS it -- one failed close_bank would otherwise bank the knife and the whole
    withdrawal on the very next click."""
    order = []
    monkeypatch.setattr(banker, "is_open", lambda: len(order) == 0)
    monkeypatch.setattr(banker, "close_bank", lambda ctx: order.append("close"))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: order.append("click") or True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 270, "child": 17, "name": "Maple longbow (u)", "text": "", "screenX": 3, "screenY": 1},
    ])
    _inv_seq(monkeypatch, [
        [_item("Knife"), _item("Maple logs", 27, 1)],
        [_item("Knife"), _item("Maple logs", 26, 1), _item("Maple longbow (u)", 1, 2)],
    ])
    ctx = _Ctx()
    fletching.start_fletching(ctx, ctx.state.config)
    assert order[0] == "close"


def test_start_fletching_gives_up_the_tick_if_the_bank_will_not_close(monkeypatch):
    clicks = []
    monkeypatch.setattr(banker, "is_open", lambda: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx: None)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(1) or True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 27, 1)])
    ctx = _Ctx()
    assert not fletching.start_fletching(ctx, ctx.state.config)
    assert clicks == []


def test_repeated_failed_starts_safety_stop(monkeypatch):
    """A product name this log cannot make fails identically every tick. Unbounded, that is a
    silent loop that reads as a healthy routine in the log."""
    monkeypatch.setattr(fletching, "start_fletching", lambda ctx, cfg: False)
    ctx = _Ctx()
    for _ in range(fletching.START_FAIL_LIMIT - 1):
        fletching.fletch_phase(ctx, ctx.state.config)
        assert not ctx.state.safety_stopped
    fletching.fletch_phase(ctx, ctx.state.config)
    assert ctx.state.safety_stopped
    assert "Maple longbow (u)" in ctx.state.stop_reason


def test_a_successful_start_clears_the_failure_count(monkeypatch):
    """Occasional failures across a long session must not accumulate into a stop."""
    monkeypatch.setattr(fletching, "_wait_out_batch", lambda *a, **k: "done")
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 2)])
    ctx = _Ctx()
    monkeypatch.setattr(fletching, "start_fletching", lambda ctx_, cfg: False)
    fletching.fletch_phase(ctx, ctx.state.config)
    assert ctx.state.data["fl_start_fails"] == 1
    monkeypatch.setattr(fletching, "start_fletching", lambda ctx_, cfg: True)
    fletching.fletch_phase(ctx, ctx.state.config)
    assert ctx.state.data["fl_start_fails"] == 0


def test_start_fletching_needs_both_the_knife_and_a_log(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Maple logs", 27)])
    ctx = _Ctx()
    assert not fletching.start_fletching(ctx, ctx.state.config)


# --------------------------------------------------------------------------- the batch wait


def test_wait_out_batch_finishes_when_the_logs_run_out(monkeypatch):
    _inv_seq(monkeypatch, [
        [_item("Knife"), _item("Maple logs", 2), _item("Maple longbow (u)", 25, 1)],
        [_item("Knife"), _item("Maple logs", 1), _item("Maple longbow (u)", 26, 1)],
        [_item("Knife"), _item("Maple longbow (u)", 27, 1)],
    ])
    ctx = _Ctx()
    assert fletching._wait_out_batch(ctx, ctx.state.config, afk=True) == "done"


def test_wait_out_batch_polls_faster_as_the_batch_runs_out(monkeypatch, _fake_clock):
    """USER 2026-09-08: "we are very slow tabbing back". A flat 2.5s poll meant up to 2.5s of
    dead time after the last log was consumed before the batch was even seen to be over."""
    slept = []
    real_sleep = fletching._sleep
    monkeypatch.setattr(fletching, "_sleep", lambda s: (slept.append(s), real_sleep(s))[1])
    # One log left: the batch has ~1.8s to run, so waiting 2.5s would overshoot the end.
    _inv_seq(monkeypatch, [
        [_item("Knife"), _item("Maple logs", 1), _item("Maple longbow (u)", 26, 1)],
        [_item("Knife"), _item("Maple longbow (u)", 27, 1)],
    ])
    ctx = _Ctx()
    assert fletching._wait_out_batch(ctx, ctx.state.config, afk=True) == "done"
    assert slept and slept[0] < fletching.AFK_POLL_S
    assert slept[0] >= fletching.AFK_MIN_POLL_S


def test_wait_out_batch_still_polls_slowly_during_the_long_middle(monkeypatch, _fake_clock):
    """The fast poll must not turn a 48s AFK wait into 80 HTTP reads."""
    slept = []
    real_sleep = fletching._sleep
    monkeypatch.setattr(fletching, "_sleep", lambda s: (slept.append(s), real_sleep(s))[1])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 26)])
    ctx = _Ctx()
    fletching._wait_out_batch(ctx, ctx.state.config, afk=True)
    assert slept[0] == fletching.AFK_POLL_S


def test_the_afk_delays_are_tuned_for_this_activity_not_karambwans(monkeypatch):
    """karambwan's 10-40s lateness costs a third of its ~110s trip; here a batch is ~48s, so
    the same delay nearly doubles the cycle. Rarer and shorter -- but not zero, since an always
    instant return is its own tell."""
    assert fletching.AFK_LATE_CHANCE < 0.33
    assert fletching.AFK_LATE_RANGE_S[1] <= 20.0
    assert fletching.AFK_LATE_CHANCE > 0
    # A failed Alt+Tab must be cheap to discover: focus lands well inside a second or not at all.
    assert fletching.AFK_FOCUS_WAIT_S <= 2.5


def test_wait_out_batch_stalls_when_products_stop_arriving(monkeypatch):
    """A Fletching level-up interrupts the batch. This must return -- not spin to FLETCH_MAX_S
    -- so the next tick can restart the rest of the inventory."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 20),
                                 _item("Maple longbow (u)", 7, 1)])
    ctx = _Ctx()
    assert fletching._wait_out_batch(ctx, ctx.state.config, afk=True) == "stalled"


def test_wait_out_batch_sends_no_input_while_tabbed_out(monkeypatch):
    """We are looking at another window; a fidget would move the real cursor over it."""
    fidgets = []
    monkeypatch.setattr(controller, "fidget_once", lambda: fidgets.append(1))
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 20)])
    ctx = _Ctx()
    fletching._wait_out_batch(ctx, ctx.state.config, afk=True)
    assert fidgets == []


def test_wait_out_batch_bails_out_on_a_safety_stop(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 20)])
    ctx = _Ctx()
    ctx.state.safety_stopped = True
    assert fletching._wait_out_batch(ctx, ctx.state.config, afk=True) == "stopped"


def test_fletch_phase_does_not_tab_out_for_a_nearly_finished_inventory(monkeypatch):
    """Tabbing out and back costs more than the couple of seconds of work left."""
    tabs = []
    monkeypatch.setattr(controller, "alt_tab", lambda ser: tabs.append(1))
    monkeypatch.setattr(fletching, "start_fletching", lambda ctx, cfg: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 2)])
    ctx = _Ctx()
    fletching.fletch_phase(ctx, ctx.state.config)
    assert tabs == []


def test_fletch_phase_does_not_wait_when_the_batch_never_started(monkeypatch):
    """A failed start must hand the tick straight back, not sit through a 48s wait."""
    monkeypatch.setattr(fletching, "start_fletching", lambda ctx, cfg: False)
    waited = []
    monkeypatch.setattr(fletching, "_wait_out_batch",
                        lambda *a, **k: waited.append(1) or "done")
    ctx = _Ctx()
    fletching.fletch_phase(ctx, ctx.state.config)
    assert waited == []


def test_fletch_phase_tabs_back_in_after_a_long_afk_batch(monkeypatch):
    monkeypatch.setattr(fletching, "start_fletching", lambda ctx, cfg: True)
    monkeypatch.setattr(fletching, "_wait_out_batch", lambda *a, **k: "done")
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 27)])
    calls = []
    monkeypatch.setattr(controller, "alt_tab", lambda ser: calls.append(1))
    ctx = _Ctx()
    fletching.fletch_phase(ctx, ctx.state.config)
    assert len(calls) == 2  # out, and back in


def test_tab_back_in_safety_stops_when_focus_never_returns(monkeypatch):
    """The engine does not call tick() while unfocused, so an unnoticed failure here would
    stall the whole run silently."""
    monkeypatch.setattr(controller, "wait_for_focus", lambda **k: False)
    ctx = _Ctx()
    assert not fletching.tab_back_in(ctx)
    assert ctx.state.safety_stopped


def test_tab_out_reports_false_when_alt_tab_is_swallowed(monkeypatch):
    """A machine with nothing but RuneLite open. The caller then waits focused rather than
    believing it is away."""
    monkeypatch.setattr(controller, "wait_for_focus",
                        lambda timeout_s=5.0, poll_interval_s=0.3, focused=True: focused)
    ctx = _Ctx()
    assert not fletching.tab_out(ctx)


# --------------------------------------------------------------------------- banking


def _bank_stubs(monkeypatch, withdraw_ok=True, after_withdraw=None, banked_logs=6514):
    calls = {"deposit": [], "withdraw": [], "closed": [], "search": [],
             "searched": [], "cleared": []}
    monkeypatch.setattr(banker, "find_nearby_bank",
                        lambda cfg: {"name": "Bank booth", "distance": 1})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, bank, **k: True)
    monkeypatch.setattr(banker, "deposit_inventory",
                        lambda ctx, cfg: calls["deposit"].append(cfg))
    monkeypatch.setattr(banker, "withdraw_all_shift",
                        lambda ctx, name, **k: calls["withdraw"].append(name) or withdraw_ok)
    monkeypatch.setattr(banker, "search_and_withdraw",
                        lambda ctx, name, qty, **k: calls["search"].append(name) or True)
    monkeypatch.setattr(banker, "search_bank",
                        lambda ctx, q: calls["searched"].append(q) or True)
    monkeypatch.setattr(banker, "clear_bank_search",
                        lambda ctx: calls["cleared"].append(1) or True)
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {
        "open": True,
        "items": [{"name": "Maple logs", "qty": banked_logs, "slot": 290}] if banked_logs else [],
    })
    monkeypatch.setattr(banker, "close_bank", lambda ctx: calls["closed"].append(1))
    if after_withdraw is not None:
        monkeypatch.setattr(controller, "get_inventory", lambda: after_withdraw)
    return calls


# --- the first live run's bug: a 950-item bank puts the logs off-window ------------------


def test_withdraw_logs_searches_the_bank_before_clicking(monkeypatch):
    """MEASURED live 2026-09-08: a bare shift-click was refused with 'aim point is outside the
    RuneLite window' -- this account's bank has ~950 item types and the logs sit at slot 290,
    so /bank reports a real but far-off-screen position. Searching re-lays matches from slot 0."""
    calls = _bank_stubs(monkeypatch, after_withdraw=[_item("Maple logs", 27)])
    ctx = _Ctx()
    assert fletching.withdraw_logs(ctx, ctx.state.config) == "ok"
    assert calls["searched"] == ["Maple logs"]
    # ... and the search happens BEFORE the click, not as a fallback after one has failed.
    assert calls["withdraw"] == ["Maple logs"]


def test_withdraw_logs_does_not_click_the_search_button_again(monkeypatch):
    """USER 2026-09-08: "there is no need to click the search button again after searching just
    pressing escape will exit the bank" -- bank_phase closes the bank on the next line, so the
    one ESC ends the filter and the interface together."""
    calls = _bank_stubs(monkeypatch, after_withdraw=[_item("Maple logs", 27)])
    ctx = _Ctx()
    fletching.withdraw_logs(ctx, ctx.state.config)
    assert calls["cleared"] == []


def test_withdraw_logs_skips_the_search_when_the_logs_are_already_on_screen(monkeypatch):
    """USER 2026-09-08: "if its on the screen we dont need to search". Ten keystrokes per trip
    are only worth paying when they prevent a refused click."""
    calls = _bank_stubs(monkeypatch, after_withdraw=[_item("Maple logs", 27)])
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {
        "open": True,
        "items": [{"name": "Maple logs", "qty": 6514, "slot": 5,
                   "screenX": 531, "screenY": 281}],
    })
    monkeypatch.setattr(controller, "is_point_within_window", lambda x, y: True)
    ctx = _Ctx()
    assert fletching.withdraw_logs(ctx, ctx.state.config) == "ok"
    assert calls["searched"] == []
    assert calls["withdraw"] == ["Maple logs"]


def test_withdraw_logs_searches_when_the_logs_are_off_window(monkeypatch):
    """The measured case: slot 290 in a ~950-item bank reports a real but far-off-screen
    position, and click_entity refuses it. The predicate is click_entity's own."""
    calls = _bank_stubs(monkeypatch, after_withdraw=[_item("Maple logs", 27)])
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {
        "open": True,
        "items": [{"name": "Maple logs", "qty": 6514, "slot": 290,
                   "screenX": 531, "screenY": 7813}],
    })
    monkeypatch.setattr(controller, "is_point_within_window", lambda x, y: y < 1400)
    ctx = _Ctx()
    assert fletching.withdraw_logs(ctx, ctx.state.config) == "ok"
    assert calls["searched"] == ["Maple logs"]


def test_withdraw_logs_reports_empty_only_when_the_bank_really_lists_none(monkeypatch):
    _bank_stubs(monkeypatch, banked_logs=0)
    ctx = _Ctx()
    assert fletching.withdraw_logs(ctx, ctx.state.config) == "empty"


def test_withdraw_logs_reports_failed_when_the_bank_has_logs_but_the_click_missed(monkeypatch):
    """The exact first-live-run shape. 'failed' and 'empty' must not collapse together."""
    _bank_stubs(monkeypatch, withdraw_ok=False, banked_logs=6514)
    ctx = _Ctx()
    assert fletching.withdraw_logs(ctx, ctx.state.config) == "failed"


def test_bank_phase_does_not_stop_when_a_withdrawal_misses_with_logs_in_the_bank(monkeypatch):
    """The first live run stopped with 6,514 Maple logs banked because a refused click was
    reported as an empty bank. A miss is transient -- retry, with a ceiling."""
    _bank_stubs(monkeypatch, withdraw_ok=False, banked_logs=6514,
                after_withdraw=[_item("Knife")])
    ctx = _Ctx()
    fletching.bank_phase(ctx, ctx.state.config)
    assert not ctx.state.safety_stopped
    assert ctx.state.data["fl_withdraw_fails"] == 1


def test_repeated_withdrawal_misses_eventually_stop(monkeypatch):
    _bank_stubs(monkeypatch, withdraw_ok=False, banked_logs=6514,
                after_withdraw=[_item("Knife")])
    ctx = _Ctx()
    for _ in range(fletching.WITHDRAW_FAIL_LIMIT - 1):
        fletching.bank_phase(ctx, ctx.state.config)
        assert not ctx.state.safety_stopped
    fletching.bank_phase(ctx, ctx.state.config)
    assert ctx.state.safety_stopped
    assert "in the bank" in ctx.state.stop_reason


def test_a_good_withdrawal_clears_the_failure_count(monkeypatch):
    _bank_stubs(monkeypatch, after_withdraw=[_item("Knife"), _item("Maple logs", 27, 1)])
    ctx = _Ctx()
    ctx.state.data["fl_withdraw_fails"] = 2
    fletching.bank_phase(ctx, ctx.state.config)
    assert ctx.state.data["fl_withdraw_fails"] == 0
    assert not ctx.state.safety_stopped


def test_bank_phase_deposits_keeping_only_the_knife(monkeypatch):
    """A deposit that banked the knife would strand the next batch."""
    calls = _bank_stubs(monkeypatch,
                        after_withdraw=[_item("Knife"), _item("Maple logs", 27, 1)])
    ctx = _Ctx()
    fletching.bank_phase(ctx, ctx.state.config)
    assert calls["deposit"][0]["keepItems"] == ["Knife"]
    assert calls["deposit"][0]["skipContainerDeposit"] is True
    assert calls["withdraw"] == ["Maple logs"]
    assert calls["closed"] == [1]
    assert not ctx.state.safety_stopped


def test_bank_phase_withdraws_a_knife_when_one_is_missing(monkeypatch):
    calls = _bank_stubs(monkeypatch, after_withdraw=[_item("Maple logs", 27)])
    ctx = _Ctx()
    fletching.bank_phase(ctx, ctx.state.config)
    assert calls["search"] == ["Knife"]


def test_bank_phase_stops_when_the_bank_is_out_of_logs(monkeypatch):
    """Otherwise every later tick just re-opens the bank forever. 'Out of logs' means the bank
    LISTS none -- not merely that a click failed, which is the distinction the first live run
    got wrong."""
    _bank_stubs(monkeypatch, banked_logs=0, after_withdraw=[_item("Knife")])
    ctx = _Ctx()
    fletching.bank_phase(ctx, ctx.state.config)
    assert ctx.state.safety_stopped
    assert "Maple logs" in ctx.state.stop_reason


def test_bank_phase_stops_when_no_bank_is_in_range(monkeypatch):
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: None)
    ctx = _Ctx()
    fletching.bank_phase(ctx, ctx.state.config)
    assert ctx.state.safety_stopped


def test_bank_phase_retries_a_failed_open_before_stopping(monkeypatch):
    """A bank interface that does not open is usually transient (the mahogany/foundry lesson);
    only a repeated failure is worth a stop."""
    _bank_stubs(monkeypatch, after_withdraw=[_item("Knife")])
    monkeypatch.setattr(banker, "open_bank", lambda ctx, bank, **k: False)
    ctx = _Ctx()
    for _ in range(fletching.BANK_OPEN_TRIES - 1):
        fletching.bank_phase(ctx, ctx.state.config)
        assert not ctx.state.safety_stopped
    fletching.bank_phase(ctx, ctx.state.config)
    assert ctx.state.safety_stopped


# --------------------------------------------------------------------------- dispatch


def test_tick_fletches_while_logs_remain(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 27, 1)])
    went = []
    monkeypatch.setattr(fletching, "fletch_phase", lambda ctx, cfg: went.append("fletch"))
    monkeypatch.setattr(fletching, "bank_phase", lambda ctx, cfg: went.append("bank"))
    fletching.tick(_Ctx())
    assert went == ["fletch"]


def test_tick_banks_once_the_logs_are_gone(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple longbow (u)", 27, 1)])
    went = []
    monkeypatch.setattr(fletching, "fletch_phase", lambda ctx, cfg: went.append("fletch"))
    monkeypatch.setattr(fletching, "bank_phase", lambda ctx, cfg: went.append("bank"))
    fletching.tick(_Ctx())
    assert went == ["bank"]


def test_tick_resumes_a_half_fletched_inventory_after_a_restart(monkeypatch):
    """ctx.state.data is wiped on restart while the game's own state is not, so the phase is
    derived from the live inventory every tick rather than remembered."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Knife"), _item("Maple logs", 11, 1),
                                 _item("Maple longbow (u)", 16, 2)])
    went = []
    monkeypatch.setattr(fletching, "fletch_phase", lambda ctx, cfg: went.append("fletch"))
    monkeypatch.setattr(fletching, "bank_phase", lambda ctx, cfg: went.append("bank"))
    fletching.tick(_Ctx())
    assert went == ["fletch"]


def test_routine_is_wired_into_the_panel():
    """A routine file alone is only launchable from the command line -- both halves of the
    contract are required (CLAUDE.md, 'Wiring a new routine into the GUI')."""
    assert controller.ROUTINE_FILES["FLETCHING"] == "routines/fletching.py"
    java = os.path.join(os.path.dirname(__file__), "..", "..",
                        "src", "main", "java", "com", "example", "controller",
                        "RoutineChoice.java")
    with open(java, encoding="utf-8") as fh:
        assert "FLETCHING(" in fh.read()


# --- the VM's first run: a fresh login prompts for the bank PIN ---------------------------


def test_bank_phase_says_pin_instead_of_blaming_the_booth_click(monkeypatch):
    """The booth click WORKED and opened the PIN keypad; retrying it 15 times and reporting
    "could not open Bank booth" sent me looking at aim and geometry that were never wrong."""
    _bank_stubs(monkeypatch, after_withdraw=[_item("Knife")])
    monkeypatch.setattr(banker, "open_bank", lambda ctx, bank, **k: False)
    monkeypatch.setattr(banker, "bank_pin_open", lambda dump=None: True)
    ctx = _Ctx()
    fletching.bank_phase(ctx, ctx.state.config)
    assert ctx.state.safety_stopped
    assert "PIN" in ctx.state.stop_reason
    # ... and it stops at once rather than burning the retry budget on a click that is fine.
    assert ctx.state.data.get("fl_bank_tries", 0) == 0
