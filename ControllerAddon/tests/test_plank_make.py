import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import controller
import plank_make as pm


def _item(name, slot, qty=1, x=None, y=None):
    data = {"name": name, "slot": slot, "qty": qty, "id": 6333}
    if x is not None:
        data["screenX"] = x
        data["screenY"] = y
    return data


def _inventory(log_count=25, coins=100000, first_log_slot=3, x0=1000, y0=500):
    """An inventory shaped like the recorded one: coins/potion/pouch plus N Teak logs, laid out
    on the real 4-wide inventory grid so distance-to-spell tests mean something."""
    items = [
        _item("Coins", 0, coins, x0, y0),
        _item("Magic potion(2)", 1, 1, x0 + 42, y0),
        _item("Rune pouch", 2, 1, x0 + 84, y0),
    ]
    for i in range(log_count):
        slot = first_log_slot + i
        items.append(_item("Teak logs", slot, 1,
                           x0 + (slot % 4) * 42, y0 + (slot // 4) * 36))
    return items


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


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Nothing in this file may reach a real client -- the same trap test_golem_crafting.py
    documents (its tests ran against a live bridge for 42s before it grew this)."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_npcs", lambda **kw: [])
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    monkeypatch.setattr(controller, "get_state", lambda: {"windowFocused": True})
    monkeypatch.setattr(controller, "get_widget", lambda *a, **kw: {"found": False})
    monkeypatch.setattr(controller, "click_entity", lambda *a, **kw: True)
    monkeypatch.setattr(controller, "key_tap", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": []})
    monkeypatch.setattr(controller, "get_npc_by_index", lambda index: None)
    monkeypatch.setattr(controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (0, 0))
    monkeypatch.setattr(pm, "DEPOSIT_SETTLE_S", 0.0)
    monkeypatch.setattr(controller, "click_human", lambda ser: None)
    monkeypatch.setattr(controller, "get_last_chat_message", lambda: {})
    monkeypatch.setattr(controller, "rune_pouch_count", lambda name: None)
    monkeypatch.setattr(pm.banker, "is_open", lambda: False)


# --- inventory helpers --------------------------------------------------------------


def test_count_item_counts_slots_not_distinct_names():
    """The falconry bug, restated as a test: 11 Bones is 11 slots, not 1."""
    assert pm.count_item(_inventory(log_count=25), "Teak logs") == 25


def test_count_item_uses_quantity_for_a_stack():
    assert pm.count_item(_inventory(coins=8750), "Coins") == 8750


def test_count_item_is_an_exact_name_match():
    """Teak logs and Teak plank share a prefix, so a substring match would conflate them once
    the inventory is half converted."""
    inventory = _inventory(log_count=2) + [_item("Teak plank", 30, 1)]
    assert pm.count_item(inventory, "Teak logs") == 2
    assert pm.count_item(inventory, "Teak plank") == 1


# --- choose_target_slot -------------------------------------------------------------


def test_target_slot_is_the_log_nearest_the_spell_icon():
    """The recorded human put the spell icon and the target slot on the same pixel and clicked
    twice without moving the mouse. Picking the nearest log reproduces that by construction."""
    inventory = _inventory()
    spell = {"screenX": 1042, "screenY": 716, "clickable": True}
    chosen = pm.choose_target_slot(inventory, "Teak logs", spell)
    others = [i for i in inventory
              if i["name"] == "Teak logs" and i["slot"] != chosen["slot"]]
    assert all(pm._distance(chosen, spell) <= pm._distance(o, spell) for o in others)


def test_target_slot_ignores_planks():
    inventory = [_item("Teak plank", 3, 1, 1000, 700),
                 _item("Teak logs", 27, 1, 1126, 754)]
    spell = {"screenX": 1000, "screenY": 700}
    assert pm.choose_target_slot(inventory, "Teak logs", spell)["slot"] == 27


def test_target_slot_skips_items_with_no_screen_position():
    """A slot with no screenX means the inventory panel is not drawn -- clicking a made-up
    position would fire the mouse at nothing."""
    inventory = [_item("Teak logs", 3, 1), _item("Teak logs", 4, 1, 1100, 700)]
    spell = {"screenX": 1000, "screenY": 700}
    assert pm.choose_target_slot(inventory, "Teak logs", spell)["slot"] == 4


def test_target_slot_is_none_when_out_of_logs():
    assert pm.choose_target_slot([_item("Teak plank", 3, 1, 1000, 700)],
                                 "Teak logs", {"screenX": 1000, "screenY": 700}) is None


# --- find_banker --------------------------------------------------------------------


def test_find_banker_picks_the_nearest_match_in_range():
    npcs = [{"name": "Banker", "distance": 5, "index": 1},
            {"name": "Banker", "distance": 1, "index": 2},
            {"name": "Grand Exchange Clerk", "distance": 0, "index": 3}]
    assert pm.find_banker(npcs, pm.DEFAULT_CONFIG)["index"] == 2


def test_find_banker_ignores_anything_out_of_range():
    npcs = [{"name": "Banker", "distance": 999, "index": 1}]
    assert pm.find_banker(npcs, pm.DEFAULT_CONFIG) is None


# --- tick dispatch ------------------------------------------------------------------


def test_tick_casts_then_banks_in_the_same_call(monkeypatch):
    """MEASURED live: returning between the two cost 3.17s of dead time per load, standing at a
    bank with a full inventory while the engine ran its between-tick housekeeping."""
    ctx = FakeCtx()
    logs = [25]
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=logs[0]))
    called = []
    monkeypatch.setattr(pm, "cast_phase",
                        lambda *a: called.append("cast") or logs.__setitem__(0, 0))
    monkeypatch.setattr(pm, "bank_phase", lambda *a: called.append("bank"))
    pm.tick(ctx)
    assert called == ["cast", "bank"]


def test_tick_does_not_bank_a_half_converted_inventory(monkeypatch):
    """cast_phase also returns on lost focus and on its retry budget. Banking is not the
    recovery for either -- the logs are still there and should still be cast."""
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    called = []
    monkeypatch.setattr(pm, "cast_phase", lambda *a: called.append("cast"))
    monkeypatch.setattr(pm, "bank_phase", lambda *a: called.append("bank"))
    pm.tick(ctx)
    assert called == ["cast"]


def test_tick_does_not_bank_after_a_safety_stop(monkeypatch):
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    called = []
    monkeypatch.setattr(pm, "cast_phase",
                        lambda *a: ctx.state.trigger_safety_stop("boom"))
    monkeypatch.setattr(pm, "bank_phase", lambda *a: called.append("bank"))
    pm.tick(ctx)
    assert called == []


def test_tick_banks_when_the_logs_run_out(monkeypatch):
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=0))
    called = []
    monkeypatch.setattr(pm, "cast_phase", lambda *a: called.append("cast"))
    monkeypatch.setattr(pm, "bank_phase", lambda *a: called.append("bank"))
    pm.tick(ctx)
    assert called == ["bank"]


def test_tick_banks_while_the_bank_is_open_even_holding_logs(monkeypatch):
    """A restart mid-trip leaves the interface up; casting into an open bank clicks nothing."""
    ctx = FakeCtx()
    monkeypatch.setattr(pm.banker, "is_open", lambda: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    called = []
    monkeypatch.setattr(pm, "cast_phase", lambda *a: called.append("cast"))
    monkeypatch.setattr(pm, "bank_phase", lambda *a: called.append("bank"))
    pm.tick(ctx)
    assert called == ["bank"]


def test_tick_stops_when_coins_cannot_pay_for_a_cast(monkeypatch):
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: _inventory(log_count=25, coins=100))
    monkeypatch.setattr(pm, "cast_phase", lambda *a: pytest.fail("should not cast"))
    monkeypatch.setattr(pm, "bank_phase", lambda *a: pytest.fail("should not bank"))
    pm.tick(ctx)
    assert ctx.state.safety_stopped
    assert "coins" in ctx.state.stop_reason.lower()


# --- cast_once ----------------------------------------------------------------------


def _spell_visible(monkeypatch, clickable=True):
    widget = {"found": True, "clickable": clickable, "screenX": 1042, "screenY": 716}
    monkeypatch.setattr(controller, "get_widget", lambda *a, **kw: dict(widget))
    return widget


def test_cast_once_clicks_the_spell_then_the_log(monkeypatch):
    ctx = FakeCtx()
    _spell_visible(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory())
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)
    monkeypatch.setattr(pm, "await_cast", lambda *a, **kw: True)
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, _inventory()) is True
    assert len(clicks) == 2
    assert clicks[0]["screenX"] == 1042          # the spell icon
    assert clicks[1]["name"] == "Teak logs"      # then the log


def test_cast_once_opens_the_magic_tab_when_the_spell_is_not_showing(monkeypatch):
    """After the bank closes it is the inventory tab that is up, not the spellbook."""
    ctx = FakeCtx()
    states = [{"found": True, "clickable": False},
              {"found": True, "clickable": True, "screenX": 1042, "screenY": 716}]
    monkeypatch.setattr(controller, "get_widget",
                        lambda *a, **kw: states.pop(0) if states else
                        {"found": True, "clickable": True, "screenX": 1042, "screenY": 716})
    taps = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: taps.append(key))
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory())
    monkeypatch.setattr(pm, "await_cast", lambda *a, **kw: True)
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, _inventory()) is True
    assert taps == [pm.DEFAULT_CONFIG["magicTabKey"]]


def test_cast_once_gives_up_when_the_spell_never_appears(monkeypatch):
    ctx = FakeCtx()
    _spell_visible(monkeypatch, clickable=False)
    monkeypatch.setattr(pm, "await_cast", lambda *a, **kw: pytest.fail("nothing to confirm"))
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, _inventory()) is False


def test_cast_once_reports_failure_when_no_magic_xp_arrives(monkeypatch):
    ctx = FakeCtx()
    _spell_visible(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory())
    monkeypatch.setattr(pm, "await_cast", lambda *a, **kw: False)
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, _inventory()) is False


def test_cast_once_does_not_click_a_log_it_could_not_find(monkeypatch):
    """Clicking with the spell selected and no valid target would cancel the selection."""
    ctx = FakeCtx()
    _spell_visible(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=0))
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)
    monkeypatch.setattr(pm, "await_cast", lambda *a, **kw: pytest.fail("nothing to confirm"))
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, _inventory()) is False
    assert len(clicks) == 1


# --- await_cast ---------------------------------------------------------------------


class FakeEvents:
    def __init__(self, payload):
        self.payload = payload
        self.cleared = False

    def clear(self):
        self.cleared = True

    def wait_for_event(self, types, match=None, timeout_s=1.0):
        if self.payload is None:
            return None
        if match is not None and not match(self.payload):
            return None
        return self.payload


def test_await_cast_accepts_a_magic_xp_event():
    ctx = FakeCtx()
    ctx.events = FakeEvents({"skill": "Magic", "delta": 90, "tick": 100})
    assert pm.await_cast(ctx, pm.DEFAULT_CONFIG) is True


def test_await_cast_ignores_xp_from_another_skill():
    ctx = FakeCtx()
    ctx.events = FakeEvents({"skill": "Hunter", "delta": 40, "tick": 100})
    assert pm.await_cast(ctx, pm.DEFAULT_CONFIG) is False


def test_await_cast_falls_back_to_polling_when_there_is_no_event_stream(monkeypatch):
    """ctx.events is None whenever the engine did not start a listener -- the routine contract
    requires every consumer to keep working without it."""
    ctx = FakeCtx()
    xp = iter([{"Magic": {"xp": 1000}}, {"Magic": {"xp": 1090}}])
    monkeypatch.setattr(controller, "get_skills", lambda: next(xp))
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    assert pm.await_cast(ctx, pm.DEFAULT_CONFIG) is True


def test_await_cast_polling_times_out_without_an_xp_increase(monkeypatch):
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_skills", lambda: {"Magic": {"xp": 1000}})
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    monkeypatch.setattr(pm, "CAST_CONFIRM_TIMEOUT_S", 0.0)
    assert pm.await_cast(ctx, pm.DEFAULT_CONFIG) is False


def test_cast_once_clears_the_event_buffer_before_clicking(monkeypatch):
    """wait_for_event does not consume what it matches, so without this the PREVIOUS cast's
    XP_GAINED satisfies this cast's wait instantly and every cast reads as an instant success."""
    ctx = FakeCtx()
    ctx.events = FakeEvents({"skill": "Magic", "delta": 90, "tick": 100})
    _spell_visible(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory())
    order = []
    monkeypatch.setattr(ctx.events, "clear", lambda: order.append("clear"))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: order.append("click") or True)
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, _inventory()) is True
    assert order == ["clear", "click", "click"]


# --- cast_phase ---------------------------------------------------------------------


@pytest.fixture
def _instant_cast_phase(monkeypatch):
    monkeypatch.setattr(pm, "wait_for_cast_window", lambda ctx: None)


def test_cast_phase_returns_once_the_logs_are_gone(monkeypatch, _instant_cast_phase):
    """The loop has to end by itself -- bank_phase only runs on the next engine tick."""
    ctx = FakeCtx()
    remaining = [3]

    def _cast(_ctx, _config, _inventory):
        remaining[0] -= 1
        return True

    monkeypatch.setattr(pm, "cast_once", _cast)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: _inventory(log_count=remaining[0]))
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=remaining[0]))
    assert remaining[0] == 0
    assert not ctx.state.safety_stopped


def test_cast_phase_stops_after_repeated_failures(monkeypatch, _instant_cast_phase):
    ctx = FakeCtx()
    monkeypatch.setattr(pm, "cast_once", lambda *a: False)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=25))
    assert ctx.state.safety_stopped
    assert "Magic XP" in ctx.state.stop_reason


def test_cast_phase_tolerates_an_isolated_failure(monkeypatch, _instant_cast_phase):
    """A simulated misclick costs one cycle. It must not end the load."""
    ctx = FakeCtx()
    logs = [4]
    results = iter([True, False, True, True, True])

    def _cast(_ctx, _config, _inventory):
        ok = next(results)
        if ok:
            logs[0] -= 1
        return ok

    monkeypatch.setattr(pm, "cast_once", _cast)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=logs[0]))
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=logs[0]))
    assert logs[0] == 0
    assert not ctx.state.safety_stopped


def test_cast_phase_pauses_when_the_window_loses_focus(monkeypatch, _instant_cast_phase):
    """The engine's own focus gate only runs between tick() calls, and this loop stays inside
    one for a whole inventory."""
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_state", lambda: {"windowFocused": False})
    monkeypatch.setattr(pm, "cast_once", lambda *a: pytest.fail("must not click unfocused"))
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=25))
    assert not ctx.state.safety_stopped


def test_cast_phase_stops_mid_load_when_paused(monkeypatch, _instant_cast_phase):
    """LIVE BUG (user report, 2026-08-27): cast_phase's loop can run for a whole inventory
    (~45s) inside ONE tick() call, and the engine's own F2 pause gate only runs BETWEEN tick()
    calls -- so pressing F2 mid-load kept clicking for ~16s in the observed log before it took
    effect. Same shape as the windowFocused check above: this loop has to re-check paused
    itself, same idiom."""
    ctx = FakeCtx()
    casts = [0]

    def _cast(_ctx, _config, _inventory):
        casts[0] += 1
        if casts[0] == 2:
            ctx.state.paused = True  # F2 pressed mid-load, after the 2nd cast lands
        return True

    monkeypatch.setattr(pm, "cast_once", _cast)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=25))
    assert casts[0] == 2                    # stopped right after the pause was noticed
    assert not ctx.state.safety_stopped     # pausing is not a safety stop


def test_cast_phase_stops_when_the_coins_run_out_mid_load(monkeypatch, _instant_cast_phase):
    ctx = FakeCtx()
    monkeypatch.setattr(pm, "cast_once", lambda *a: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: _inventory(log_count=10, coins=10))
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=10, coins=10))
    assert ctx.state.safety_stopped
    assert "coins" in ctx.state.stop_reason.lower()


# --- await_cast cross-check ---------------------------------------------------------


def test_await_cast_cross_checks_over_http_when_the_event_is_lost(monkeypatch):
    """UDP is lossy and the listener can go quiet entirely. Without this cross-check a silent
    stream turns every good cast into a failure and safety-stops a working routine."""
    ctx = FakeCtx()
    ctx.events = FakeEvents(None)
    monkeypatch.setattr(controller, "get_skills", lambda: {"Magic": {"xp": 1090}})
    assert pm.await_cast(ctx, pm.DEFAULT_CONFIG, xp_before=1000) is True


def test_await_cast_fails_when_the_cross_check_shows_no_xp(monkeypatch):
    ctx = FakeCtx()
    ctx.events = FakeEvents(None)
    monkeypatch.setattr(controller, "get_skills", lambda: {"Magic": {"xp": 1000}})
    assert pm.await_cast(ctx, pm.DEFAULT_CONFIG, xp_before=1000) is False


# --- bank_phase ---------------------------------------------------------------------


@pytest.fixture
def _bank(monkeypatch):
    """A bank that opens, deposits everything depositable, and holds logs."""
    calls = []
    state = {"open": False, "inventory": _inventory(log_count=0) + [_item("Teak plank", 3, 1)]}

    monkeypatch.setattr(pm.banker, "is_open", lambda: state["open"])
    monkeypatch.setattr(controller, "get_npcs",
                        lambda **kw: [{"name": "Banker", "distance": 1, "index": 7}])
    monkeypatch.setattr(controller, "get_bank",
                        lambda **kw: {"items": [{"name": "Teak logs", "qty": 5000}]})
    monkeypatch.setattr(controller, "get_inventory", lambda: state["inventory"])

    def _deposit(ctx, config):
        calls.append("deposit")
        state["inventory"] = _inventory(log_count=0)

    monkeypatch.setattr(pm.banker, "deposit_inventory", _deposit)
    monkeypatch.setattr(pm.banker, "search_bank",
                        lambda ctx, q: calls.append(f"search:{q}") or True)
    monkeypatch.setattr(pm.banker, "withdraw_all_shift",
                        lambda ctx, name, **kw: calls.append(f"withdraw:{name}") or True)
    monkeypatch.setattr(pm, "open_banker",
                        lambda ctx, npc: calls.append("open") or state.update(open=True) or True)
    monkeypatch.setattr(pm, "close_bank",
                        lambda ctx: calls.append("close") or state.update(open=False) or True)
    return calls, state


def test_bank_phase_opens_deposits_searches_then_withdraws(_bank):
    calls, _state = _bank
    ctx = FakeCtx()
    pm.bank_phase(ctx, pm.DEFAULT_CONFIG)
    assert calls == ["open", "deposit", "search:Teak logs", "withdraw:Teak logs", "close"]
    assert not ctx.state.safety_stopped


def test_bank_phase_stops_when_the_bank_is_out_of_logs(monkeypatch, _bank):
    calls, _state = _bank
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": []})
    ctx = FakeCtx()
    pm.bank_phase(ctx, pm.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped
    assert "Teak logs" in ctx.state.stop_reason
    assert "withdraw:Teak logs" not in calls


def test_bank_phase_retries_instead_of_withdrawing_onto_a_failed_deposit(monkeypatch, _bank):
    """Withdrawing on top of undeposited planks takes a few logs and reads as success, leaving
    a short load behind. The wrong stop reason it used to produce was 'no logs in the bank'."""
    calls, state = _bank
    monkeypatch.setattr(pm.banker, "deposit_inventory",
                        lambda ctx, config: calls.append("deposit"))
    ctx = FakeCtx()
    pm.bank_phase(ctx, pm.DEFAULT_CONFIG)
    assert calls == ["open", "deposit"]
    assert not ctx.state.safety_stopped


def test_bank_phase_stops_when_no_banker_is_in_range(monkeypatch, _bank):
    monkeypatch.setattr(controller, "get_npcs", lambda **kw: [])
    ctx = FakeCtx()
    pm.bank_phase(ctx, pm.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped
    assert "Banker" in ctx.state.stop_reason


# --- the single-pixel aim -----------------------------------------------------------


def test_slot_pitch_is_measured_off_the_live_slots():
    """NOT derived from a widget's reported width. /widget returns a SCALED centre next to an
    UNSCALED size -- MEASURED live, a slot reports 36x32 while sitting on an 86x74 screen grid,
    a 2.06x stretch factor apart. Anything comparing the two spaces is wrong by that factor."""
    assert pm.slot_pitch(_inventory(x0=1000, y0=500)) == 36    # the row gap, the smaller of the two


def test_in_place_aim_is_the_midpoint_of_an_overlapping_pair():
    inventory = _inventory()
    spell = {"screenX": 1042, "screenY": 716}
    target = pm.choose_target_slot(inventory, "Teak logs", spell)
    aim = pm.in_place_aim(spell, target, inventory)
    assert aim == (int(round((spell["screenX"] + target["screenX"]) / 2)),
                   int(round((spell["screenY"] + target["screenY"]) / 2)))


def test_in_place_aim_refuses_a_slot_too_far_from_the_spell():
    """A midpoint only lands inside both boxes when the two centres are close. Further apart
    and the routine must fall back to two ordinary clicks rather than click between them."""
    inventory = _inventory()
    spell = {"screenX": 500, "screenY": 200}
    target = pm.choose_target_slot(inventory, "Teak logs", spell)
    assert pm.in_place_aim(spell, target, inventory) is None


def test_cached_aim_is_dropped_once_its_slot_stops_holding_a_log():
    ctx = FakeCtx()
    ctx.state.data["pm_aim"] = {"x": 1, "y": 2, "slot": 25}
    inventory = [_item("Teak plank", 25, 1, 1, 2)]
    assert pm.cached_aim(ctx, inventory, pm.DEFAULT_CONFIG) is None
    assert "pm_aim" not in ctx.state.data


def test_cached_aim_survives_while_its_slot_still_holds_a_log():
    ctx = FakeCtx()
    ctx.state.data["pm_aim"] = {"x": 1, "y": 2, "slot": 25}
    inventory = [_item("Teak logs", 25, 1, 1, 2)]
    assert pm.cached_aim(ctx, inventory, pm.DEFAULT_CONFIG) == {"x": 1, "y": 2, "slot": 25}


def test_a_cached_aim_hovers_once_and_clicks_twice(monkeypatch):
    """The point of the overlap: one mouse flight, two clicks, no movement between them."""
    ctx = FakeCtx()
    ctx.state.data["pm_aim"] = {"x": 1042, "y": 730, "slot": 25}
    _spell_visible(monkeypatch)
    monkeypatch.setattr(pm, "await_cast", lambda *a, **kw: True)
    flights, clicks = [], []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: flights.append(entity) or True)
    monkeypatch.setattr(controller, "click_human", lambda ser: clicks.append("click"))
    inventory = [_item("Teak logs", 25, 1, 1042, 745)]
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, inventory) is True
    assert len(flights) == 1 and (flights[0]["screenX"], flights[0]["screenY"]) == (1042, 730)
    assert clicks == ["click"]


def test_the_slow_route_records_an_aim_for_the_rest_of_the_load(monkeypatch):
    ctx = FakeCtx()
    spell = _spell_visible(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory())
    monkeypatch.setattr(pm, "await_cast", lambda *a, **kw: True)
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, _inventory()) is True
    target = pm.choose_target_slot(_inventory(), "Teak logs", spell)
    assert ctx.state.data["pm_aim"]["slot"] == target["slot"]


def test_a_failed_cast_drops_the_cached_aim(monkeypatch, _instant_cast_phase):
    """Self-correcting fallback: if the overlap assumption is ever wrong, one failure sends the
    next cast back through the two-click route that re-derives the aim."""
    ctx = FakeCtx()
    ctx.state.data["pm_aim"] = {"x": 1, "y": 2, "slot": 25}
    monkeypatch.setattr(pm, "cast_once", lambda *a: False)
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=25))
    assert "pm_aim" not in ctx.state.data


# --- withdraw_logs ------------------------------------------------------------------


def test_withdraw_skips_the_search_when_the_logs_are_on_screen(monkeypatch):
    """The search is a click, nine keystrokes and a settle -- ~2.5s of an 8s bank trip, and
    pure waste whenever the logs are already visible."""
    calls = []
    monkeypatch.setattr(controller, "get_bank",
                        lambda **kw: {"items": [{"name": "Teak logs", "screenX": 650,
                                                 "screenY": 281}]})
    monkeypatch.setattr(pm.banker, "search_bank",
                        lambda ctx, q: calls.append("search") or True)
    monkeypatch.setattr(pm.banker, "withdraw_all_shift",
                        lambda ctx, name, **kw: calls.append("withdraw") or True)
    assert pm.withdraw_logs(FakeCtx(), "Teak logs") is True
    assert calls == ["withdraw"]


def test_withdraw_searches_when_the_logs_are_off_screen(monkeypatch):
    """MEASURED in the recording: the logs sat at bank slot 443, reporting a real but far
    off-screen screenY. The fallback is why that case still works."""
    calls = []
    monkeypatch.setattr(controller, "get_bank",
                        lambda **kw: {"items": [{"name": "Teak logs", "screenX": 650,
                                                 "screenY": 7813}]})
    monkeypatch.setattr(controller, "is_point_within_window", lambda x, y: y < 1400)
    monkeypatch.setattr(pm.banker, "search_bank",
                        lambda ctx, q: calls.append("search") or True)
    monkeypatch.setattr(pm.banker, "withdraw_all_shift",
                        lambda ctx, name, **kw: calls.append("withdraw") or True)
    assert pm.withdraw_logs(FakeCtx(), "Teak logs") is True
    assert calls == ["search", "withdraw"]


def test_withdraw_falls_back_to_search_when_a_visible_click_does_not_land(monkeypatch):
    """A wrong visibility guess must cost latency, not the trip."""
    calls = []
    monkeypatch.setattr(controller, "get_bank",
                        lambda **kw: {"items": [{"name": "Teak logs", "screenX": 650,
                                                 "screenY": 281}]})
    monkeypatch.setattr(pm.banker, "search_bank",
                        lambda ctx, q: calls.append("search") or True)
    results = iter([False, True])
    monkeypatch.setattr(pm.banker, "withdraw_all_shift",
                        lambda ctx, name, **kw: calls.append("withdraw") or next(results))
    assert pm.withdraw_logs(FakeCtx(), "Teak logs") is True
    assert calls == ["withdraw", "search", "withdraw"]


def test_withdraw_reports_none_when_the_bank_has_no_logs(monkeypatch):
    monkeypatch.setattr(controller, "get_bank", lambda **kw: {"items": []})
    monkeypatch.setattr(pm.banker, "search_bank", lambda ctx, q: True)
    monkeypatch.setattr(pm.banker, "withdraw_all_shift",
                        lambda ctx, name, **kw: pytest.fail("nothing to withdraw"))
    assert pm.withdraw_logs(FakeCtx(), "Teak logs") is None


def test_a_parked_cursor_is_clicked_without_moving_at_all(monkeypatch):
    """USER-DIRECTED: once a pair has worked, the mouse must not move again for the rest of the
    inventory. click_entity re-rolls its aim jitter on every call, so leaving it in the loop was
    still nudging the cursor a few pixels every cast."""
    ctx = FakeCtx()
    ctx.state.data["pm_aim"] = {"x": 1042, "y": 730, "slot": 25}
    _spell_visible(monkeypatch)
    monkeypatch.setattr(pm, "await_cast", lambda *a, **kw: True)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (1042, 730))
    monkeypatch.setattr(controller, "click_entity",
                        lambda *a, **kw: pytest.fail("must not move the mouse"))
    clicks = []
    monkeypatch.setattr(controller, "click_human", lambda ser: clicks.append("click"))
    inventory = [_item("Teak logs", 25, 1, 1042, 745)]
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, inventory) is True
    assert clicks == ["click", "click"]


def test_a_drifted_cursor_is_re_flown_once(monkeypatch):
    """Standing still is only safe because the position is verified, not assumed."""
    ctx = FakeCtx()
    ctx.state.data["pm_aim"] = {"x": 1042, "y": 730, "slot": 25}
    _spell_visible(monkeypatch)
    monkeypatch.setattr(pm, "await_cast", lambda *a, **kw: True)
    monkeypatch.setattr(controller, "get_cursor_pos", lambda: (300, 300))
    flights = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: flights.append(entity) or True)
    inventory = [_item("Teak logs", 25, 1, 1042, 745)]
    assert pm.cast_once(ctx, pm.DEFAULT_CONFIG, inventory) is True
    assert len(flights) == 1


def test_await_deposit_polls_instead_of_reading_once(monkeypatch):
    """CONFIRMED live: a single read 0.5s after the shift-click still saw all 26 planks, because
    the click is processed on a game tick. That false alarm cost an engine tick every trip."""
    reads = iter([_inventory(log_count=0) + [_item("Teak plank", 3, 1)],
                  _inventory(log_count=0)])
    monkeypatch.setattr(controller, "get_inventory", lambda: next(reads))
    monkeypatch.setattr(pm, "DEPOSIT_SETTLE_S", 5.0)
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    assert pm.await_deposit("Teak plank") == 0


def test_await_deposit_reports_what_is_left_when_it_really_did_not_land(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: _inventory(log_count=0) + [_item("Teak plank", 3, 1)])
    monkeypatch.setattr(pm, "DEPOSIT_SETTLE_S", 0.0)
    assert pm.await_deposit("Teak plank") == 1


# --- safety stops --------------------------------------------------------------------


def test_out_of_runes_stops_immediately_on_a_failed_cast(monkeypatch, _instant_cast_phase):
    """No amount of re-clicking makes a rune appear, so this must not spend the retry budget --
    and the generic "N casts produced no Magic XP" stop would say nothing about why."""
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    monkeypatch.setattr(controller, "get_last_chat_message",
                        lambda: {"message": "You do not have enough Astral runes to cast this spell."})
    attempts = []
    monkeypatch.setattr(pm, "cast_once", lambda *a: attempts.append(1) or False)
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=25))
    assert len(attempts) == 1                       # stopped on the FIRST failure
    assert ctx.state.safety_stopped
    assert "out of runes" in ctx.state.stop_reason
    assert "Astral" in ctx.state.stop_reason        # quotes the game verbatim


def test_an_unrelated_rune_chat_line_alone_does_not_stop_the_routine(monkeypatch,
                                                                     _instant_cast_phase):
    """The check only runs after a cast already failed, so someone saying "rune" in chat can
    never stop it by itself."""
    ctx = FakeCtx()
    logs = [3]
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=logs[0]))
    monkeypatch.setattr(controller, "get_last_chat_message",
                        lambda: {"message": "Selling nature runes 300gp each!"})
    monkeypatch.setattr(pm, "cast_once",
                        lambda *a: logs.__setitem__(0, logs[0] - 1) or True)
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=logs[0]))
    assert not ctx.state.safety_stopped


def test_a_failed_cast_with_no_rune_message_uses_the_retry_budget(monkeypatch,
                                                                  _instant_cast_phase):
    """A misclick is not an out-of-runes condition and must still be retried."""
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    monkeypatch.setattr(controller, "get_last_chat_message",
                        lambda: {"message": "You cast Plank Make."})
    attempts = []
    monkeypatch.setattr(pm, "cast_once", lambda *a: attempts.append(1) or False)
    pm.cast_phase(ctx, pm.DEFAULT_CONFIG, _inventory(log_count=25))
    assert len(attempts) == pm.MAX_CONSECUTIVE_CAST_FAILURES
    assert "Magic XP" in ctx.state.stop_reason


def test_rune_shortage_matches_the_phrasings_the_game_might_use(monkeypatch):
    for msg in ("You do not have enough Astral runes to cast this spell.",
                "You need more Nature runes to cast this spell.",
                "You don't have enough runes to cast this spell."):
        monkeypatch.setattr(controller, "get_last_chat_message", lambda m=msg: {"message": m})
        assert pm.rune_shortage() == msg
    for msg in ("", "You cast Plank Make.", "Buying rune scimitar"):
        monkeypatch.setattr(controller, "get_last_chat_message", lambda m=msg: {"message": m})
        assert pm.rune_shortage() is None


def test_an_empty_rune_stops_before_a_wasted_cast(monkeypatch):
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    monkeypatch.setattr(controller, "rune_pouch_count",
                        lambda name: 0 if name == "Astral" else 5000)
    monkeypatch.setattr(pm, "cast_phase", lambda *a: pytest.fail("should not cast"))
    monkeypatch.setattr(pm, "bank_phase", lambda *a: pytest.fail("should not bank"))
    pm.tick(ctx)
    assert ctx.state.safety_stopped
    assert "out of Astral runes" in ctx.state.stop_reason


def test_an_unreadable_pouch_does_not_stop_the_routine(monkeypatch):
    """None means "cannot tell" -- a client too old for /rune-pouch, an unresolved rune name, or
    no pouch carried. None of those are evidence of an empty pouch, and stopping on an absence of
    evidence would take the routine down on any older client."""
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _inventory(log_count=25))
    monkeypatch.setattr(controller, "rune_pouch_count", lambda name: None)
    called = []
    monkeypatch.setattr(pm, "cast_phase", lambda *a: called.append("cast"))
    pm.tick(ctx)
    assert called == ["cast"]
    assert not ctx.state.safety_stopped
