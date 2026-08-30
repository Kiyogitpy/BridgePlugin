import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import time

import pytest

import giants_foundry as gf
import controller
import banker


# ---------------------------------------------------------------------------
# Task 1: mould selection
# ---------------------------------------------------------------------------

ALL_MOULDS = list(gf.MOULDS)

# The account's unlocks during the 2026-08-31 recording: all 18 defaults plus the five
# purchasables its picks prove it owned (SPIKER notably absent).
RECORDING_UNLOCKS = [m for m, v in gf.MOULDS.items() if v[3] is False] + [
    "JUGGERNAUT_FORTE", "CHOPPER_FORTE_PLUS_ONE", "FLAMBERGE_BLADE",
    "SERPENT_BLADE", "NEEDLE_POINT",
]


def test_mould_table_shape():
    # 33 moulds, three parts, every score positive, display names unique
    assert len(gf.MOULDS) == 33
    parts = {"RICASSO": 0, "BLADE": 0, "TIP": 0}
    displays = set()
    for enum, (display, part, scores, purchasable) in gf.MOULDS.items():
        parts[part] += 1
        assert display not in displays
        displays.add(display)
        assert scores and all(v > 0 for v in scores.values())
    assert parts == {"RICASSO": 11, "BLADE": 11, "TIP": 11}


def test_best_moulds_narrow_flat_matches_recording():
    # MEASURED 2026-08-31 (recording, second sword): the human picked exactly these.
    # Needle Point ties The Point! at 22; stable max keeps table order (Needle Point first),
    # which is also what the human chose.
    best = gf.best_moulds(["NARROW", "FLAT"], ALL_MOULDS)
    assert best == {"RICASSO": "CHOPPER_FORTE_PLUS_ONE",
                    "BLADE": "SERPENT_BLADE",
                    "TIP": "NEEDLE_POINT"}


def test_best_moulds_narrow_spiked_matches_recording_unlocks():
    # First sword: SPIKER (the global best forte at 23) was not unlocked; with the
    # recording's unlock set the argmax is what the human picked.
    best = gf.best_moulds(["NARROW", "SPIKED"], RECORDING_UNLOCKS)
    assert best == {"RICASSO": "JUGGERNAUT_FORTE",
                    "BLADE": "FLAMBERGE_BLADE",
                    "TIP": "NEEDLE_POINT"}


def test_best_moulds_empty_usable_falls_back_to_all():
    # usableMoulds == [] means "cannot tell" from an un-rebuilt client, not "nothing usable".
    best = gf.best_moulds(["LIGHT", "FLAT"], [])
    assert set(best) == {"RICASSO", "BLADE", "TIP"}


# ---------------------------------------------------------------------------
# Task 2: fill planner
# ---------------------------------------------------------------------------

FILL = DEFAULT_FILL = [
    {"item": "Mithril platebody", "metal": "MITHRIL", "bars_each": 4, "target_bars": 14},
    {"item": "Adamant platelegs", "metal": "ADAMANT", "bars_each": 2, "target_bars": 14},
]


def test_plan_fill_from_empty_matches_recording():
    # MEASURED: 14 mith via 4-bar bodies quantizes to 4 items (16 bars) -> 2 bars of
    # planned overflow at the 28 cap, which the human accepted. Loss 2 <= cap 2 -> ok.
    steps, err = gf.plan_fill(FILL, {}, max_overflow_loss=2)
    assert err is None
    assert steps == [
        {"item": "Mithril platebody", "bars_each": 4, "count": 4},
        {"item": "Adamant platelegs", "bars_each": 2, "count": 7},
    ]


def test_plan_fill_rejects_overflow_beyond_cap():
    steps, err = gf.plan_fill(FILL, {}, max_overflow_loss=1)
    assert steps == []
    assert "overflow" in err


def test_plan_fill_resumes_partial_crucible():
    # Mid-fill restart: 12 mithril + 14 adamant already in -> one more body finishes it.
    steps, err = gf.plan_fill(FILL, {"MITHRIL": 12, "ADAMANT": 14}, max_overflow_loss=2)
    assert err is None
    assert steps == [{"item": "Mithril platebody", "bars_each": 4, "count": 1}]


def test_plan_fill_full_crucible_is_done():
    steps, err = gf.plan_fill(FILL, {"MITHRIL": 14, "ADAMANT": 14}, max_overflow_loss=2)
    assert steps == [] and err is None


def test_plan_fill_short_config_errors():
    short = [{"item": "Adamant platelegs", "metal": "ADAMANT", "bars_each": 2, "target_bars": 20}]
    steps, err = gf.plan_fill(short, {}, max_overflow_loss=2)
    assert steps == []
    assert "20/28" in err


# ---------------------------------------------------------------------------
# Task 3: heat targeting
# ---------------------------------------------------------------------------

def test_tool_stats_are_the_measured_constants():
    # easy-giantsfoundry Stage.java, CONFIRMED live in the recording (hammer +20/-25 per
    # 5 ticks at 916->726; polish +10 per 2 ticks).
    assert gf.TOOL_STATS["TRIP_HAMMER"] == {"cycle_ticks": 5, "progress": 20, "heat": -25, "object": 44619}
    assert gf.TOOL_STATS["GRINDSTONE"] == {"cycle_ticks": 2, "progress": 10, "heat": 15, "object": 44620}
    assert gf.TOOL_STATS["POLISHING_WHEEL"] == {"cycle_ticks": 2, "progress": 10, "heat": -17, "object": 44621}


def test_actions_left_in_section():
    # 5 sections -> 200 per section. At completion 140, 60 to go: 3 hammer actions
    # (20 each) or 6 grind actions (10 each).
    assert gf.actions_left_in_section(140, 5, "TRIP_HAMMER") == 3
    assert gf.actions_left_in_section(140, 5, "GRINDSTONE") == 6
    # Exactly on a boundary: the whole next section remains.
    assert gf.actions_left_in_section(200, 5, "TRIP_HAMMER") == 10
    # No preform data -> 0, never a division by zero.
    assert gf.actions_left_in_section(0, 0, "TRIP_HAMMER") == 0


def test_heat_stop_target_enters_band_at_the_working_end():
    # Hammer cools (-25/action): enter near the TOP of the band so the whole section
    # rides down through it. 8 actions from min 830 -> 830+200=1030, clamped to max.
    assert gf.heat_stop_target("TRIP_HAMMER", 830, 1000, 8) == 1000
    # 3 actions -> 830+75 = 905, inside the band -> unclamped.
    assert gf.heat_stop_target("TRIP_HAMMER", 830, 1000, 3) == 905
    # Grindstone heats (+15): enter near the BOTTOM. 4 actions from max 590 -> 530.
    assert gf.heat_stop_target("GRINDSTONE", 410, 590, 4) == 530
    # Never below min.
    assert gf.heat_stop_target("GRINDSTONE", 410, 590, 20) == 410


# ---------------------------------------------------------------------------
# Test harness for the runtime half (Tasks 4+)
# ---------------------------------------------------------------------------

class _State:
    def __init__(self, config=None):
        self.config = config or dict(gf.DEFAULT_CONFIG)
        self.data = {}
        self.stopped = None

    def trigger_safety_stop(self, reason):
        if self.stopped is None:
            self.stopped = reason


class _Ctx:
    def __init__(self, config=None):
        self.ser = None
        self.state = _State(config)
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Hermetic suite (house rule): stub every controller call that would reach the
    running client or the Arduino. Defaults are the documented 'cannot tell' answers."""
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [])
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: {})
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: None)
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "type_text", lambda ser, text: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "mark_busy", lambda s: None)
    monkeypatch.setattr(controller, "object_refresh_target",
                        lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "npc_index_refresh_target",
                        lambda idx: (lambda: None))
    monkeypatch.setattr(gf.time, "sleep", lambda s: None)


def _st(**over):
    """A minimal healthy API snapshot; override per test."""
    base = {
        "phase": "REFINING", "commissionTypes": ["NARROW", "FLAT"],
        "crucibleContents": {}, "crucibleBarCount": 0, "crucibleState": "EMPTY",
        "selectedMoulds": {}, "usableMoulds": [],
        "preformState": "EQUIPPED", "temperature": 900, "completion": 100,
        "quality": 147, "startingQuality": 147, "qualityLost": 0,
        "difficulty": 95, "sectionCount": 5, "currentSection": 1,
        "toolSequence": ["TRIP_HAMMER", "GRINDSTONE", "POLISHING_WHEEL",
                          "GRINDSTONE", "TRIP_HAMMER"],
        "requiredTool": "TRIP_HAMMER",
        "minimumTemperature": 830, "maximumTemperature": 1000,
        "temperatureStatus": "READY", "sweetSpotState": "NONE",
        "readyToHandIn": False, "activity": "NONE", "atGiantsFoundry": True,
    }
    base.update(over)
    return base


def _serve_state(monkeypatch, st):
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: st if (g, r) == ("minigames", "giants-foundry") else None)


# ---------------------------------------------------------------------------
# Task 4: dispatcher
# ---------------------------------------------------------------------------

def test_read_state_error_and_none_mean_unavailable(monkeypatch):
    assert gf.read_state() is None                      # fixture default: get_api -> None
    _serve_state(monkeypatch, {"error": "boom", "reader": "GiantsFoundry"})
    assert gf.read_state() is None


def test_tick_unavailable_state_defers_then_stops(monkeypatch):
    ctx = _Ctx()
    for _ in range(gf.STATE_UNAVAILABLE_LIMIT - 1):
        gf.tick(ctx)
    assert ctx.state.stopped is None
    gf.tick(ctx)
    assert "unavailable" in ctx.state.stopped


def test_tick_inactive_safety_stops(monkeypatch):
    ctx = _Ctx()
    _serve_state(monkeypatch, _st(phase="INACTIVE"))
    gf.tick(ctx)
    assert "not at the foundry" in ctx.state.stopped.lower()


def test_tick_routes_every_known_phase(monkeypatch):
    seen = []
    for phase in ("AWAITING_COMMISSION", "PREPARING", "SELECTING_MOULDS", "MOULD_SET",
                  "PREFORM_READY", "COLLECTING_PREFORM", "REFINING", "STORED",
                  "READY_TO_HAND_IN", "RUINED"):
        assert phase in gf.PHASE_HANDLERS, phase


def test_tick_resets_counters_where_success_is_detected(monkeypatch):
    ctx = _Ctx()
    d = ctx.state.data
    d["gf_commission_attempts"] = 3
    d["gf_handin_attempts"] = 4
    d["gf_pickup_attempts"] = 2
    d["gf_fill_attempts"] = 2
    d["gf_last_bars"] = 4
    # A REFINING read with a commission present and more bars than last time clears all
    # the counters whose success it proves.
    _serve_state(monkeypatch, _st(crucibleBarCount=8))
    monkeypatch.setitem(gf.PHASE_HANDLERS, "REFINING", lambda ctx, st: None)
    gf.tick(ctx)
    assert d["gf_commission_attempts"] == 0
    assert d["gf_handin_attempts"] == 0      # phase is not READY_TO_HAND_IN/RUINED
    assert d["gf_pickup_attempts"] == 0      # phase is not PREFORM_READY/COLLECTING
    assert d["gf_fill_attempts"] == 0        # bar count grew
    assert d["gf_last_bars"] == 8


# ---------------------------------------------------------------------------
# Task 5: commission + hand-in
# ---------------------------------------------------------------------------

def _kovac(monkeypatch):
    npc = {"id": gf.KOVAC_NPC_ID, "index": 28704, "name": "Kovac",
           "screenX": 500, "screenY": 300}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [npc])
    return npc


def test_commission_right_clicks_kovac(monkeypatch):
    ctx = _Ctx()
    _kovac(monkeypatch)
    calls = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: calls.append(option) or True)
    gf.get_commission_phase(ctx, _st(phase="AWAITING_COMMISSION", commissionTypes=[]))
    assert calls == ["Commission"]
    assert ctx.state.data["gf_commission_attempts"] == 1


def test_commission_ceiling_stops(monkeypatch):
    ctx = _Ctx()
    _kovac(monkeypatch)
    st = _st(phase="AWAITING_COMMISSION", commissionTypes=[])
    for _ in range(gf.COMMISSION_MAX_ATTEMPTS + 1):
        gf.get_commission_phase(ctx, st)
    assert "commission" in ctx.state.stopped


def test_hand_in_dialogue_open_advances_not_clicks(monkeypatch):
    # With the reward dialogue open, hand_in presses keys, never re-clicks Kovac.
    ctx = _Ctx()
    _kovac(monkeypatch)
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"open": True, "options": []} if (g, r) == ("ui", "dialogue") else None)
    keys, clicks = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda *a, **k: clicks.append(a) or True)
    gf.hand_in_phase(ctx, _st(phase="READY_TO_HAND_IN", readyToHandIn=True))
    assert keys == ["SPACE"] and clicks == []


def test_hand_in_dialogue_options_picks_commission(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"open": True,
                                      "options": ["Can I have another commission?", "No thanks."]}
                        if (g, r) == ("ui", "dialogue") else None)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    gf.hand_in_phase(ctx, _st(phase="READY_TO_HAND_IN", readyToHandIn=True))
    assert keys == ["1"]


def test_hand_in_no_dialogue_clicks_kovac_with_ceiling(monkeypatch):
    ctx = _Ctx()
    _kovac(monkeypatch)
    calls = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: calls.append(option) or True)
    st = _st(phase="READY_TO_HAND_IN", readyToHandIn=True)
    for _ in range(gf.HANDIN_MAX_ATTEMPTS + 1):
        gf.hand_in_phase(ctx, st)
    assert calls == ["Hand-in"] * gf.HANDIN_MAX_ATTEMPTS
    assert "hand-in" in ctx.state.stopped.lower()


def test_dialogue_options_tolerates_both_shapes():
    assert gf._dialogue_options({"options": ["a", "b"]}) == ["a", "b"]
    assert gf._dialogue_options({"options": [{"text": "a"}, {"text": "b"}]}) == ["a", "b"]
    assert gf._dialogue_options({}) == []


# ---------------------------------------------------------------------------
# Task 6: mould selection execution
# ---------------------------------------------------------------------------

def _mould_rows(monkeypatch, names):
    rows = [{"text": n, "screenX": 400, "screenY": 200 + 20 * i}
            for i, n in enumerate(names)]
    monkeypatch.setattr(controller, "get_widget_children",
                        lambda g, c: rows if (g, c) == (gf.MOULD_GROUP, gf.MOULD_LIST_CHILD) else [])
    return rows


def test_prepare_opens_jig_when_moulds_unset(monkeypatch):
    ctx = _Ctx()
    jig = {"id": gf.MOULD_JIG_ID, "x": 3370, "y": 11488, "plane": 0,
           "screenX": 700, "screenY": 300, "name": "Mould jig"}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [jig])
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicked.append(ent.get("id")) or True)
    gf.prepare_phase(ctx, _st(phase="PREPARING", selectedMoulds={}, mouldSetupOpen=False))
    assert clicked == [gf.MOULD_JIG_ID]
    assert ctx.state.data["gf_mould_opens"] == 1


def test_prepare_open_jig_ceiling(monkeypatch):
    ctx = _Ctx()
    st = _st(phase="PREPARING", selectedMoulds={}, mouldSetupOpen=False)
    for _ in range(gf.MOULD_MAX_OPENS + 1):
        gf.prepare_phase(ctx, st)     # no jig visible -> attempts still count
    assert "mould" in ctx.state.stopped.lower()


def test_select_moulds_clicks_best_visible_row_one_part_per_call(monkeypatch):
    # Narrow Flat, RICASSO tab: with these rows visible the argmax is Chopper Forte +1.
    ctx = _Ctx()
    rows = _mould_rows(monkeypatch, ["Chopper Forte", "Serpent Ricasso", "Chopper Forte +1"])
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicked.append(ent) or True)
    st = _st(phase="SELECTING_MOULDS", mouldSetupOpen=True, selectedMoulds={},
             usableMoulds=list(gf.MOULDS))
    gf.prepare_phase(ctx, st)
    assert len(clicked) == 1
    assert clicked[0]["text"] == "Chopper Forte +1"


def test_select_moulds_all_set_clicks_set_button(monkeypatch):
    ctx = _Ctx()
    plan = gf.best_moulds(["NARROW", "FLAT"], [])
    set_button = {"screenX": 660, "screenY": 152, "clickable": True}
    monkeypatch.setattr(controller, "get_widget",
                        lambda g, c, index=None: set_button
                        if (g, c) == (gf.MOULD_GROUP, gf.MOULD_SET_CHILD) else None)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicked.append(ent) or True)
    st = _st(phase="SELECTING_MOULDS", mouldSetupOpen=True,
             selectedMoulds={"RICASSO": plan["RICASSO"], "BLADE": plan["BLADE"],
                              "TIP": plan["TIP"]})
    gf.prepare_phase(ctx, st)
    assert clicked == [set_button]


# ---------------------------------------------------------------------------
# Task 7: crucible fill + pour
# ---------------------------------------------------------------------------

def _crucible(monkeypatch, extra_objects=()):
    cru = {"id": gf.CRUCIBLE_ID, "x": 3373, "y": 11488, "plane": 0,
           "screenX": 670, "screenY": 320, "name": "Crucible"}
    objs = [cru] + list(extra_objects)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: objs)
    return cru


def _inv(monkeypatch, names):
    items = [{"name": n, "screenX": 900, "screenY": 400 + 10 * i, "qty": 1}
             for i, n in enumerate(names)]
    monkeypatch.setattr(controller, "get_inventory", lambda: {"items": items})
    return items


def test_fill_uses_item_on_crucible(monkeypatch):
    ctx = _Ctx()
    _crucible(monkeypatch)
    _inv(monkeypatch, ["Mithril platebody"] * 4 + ["Adamant platelegs"] * 7)
    menu, clicks = [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent.get("name"), option)) or True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent.get("id")) or True)
    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    gf._fill_crucible(ctx, st)
    # Selects Use on the first planned item (bodies first -- biggest bars_each), then
    # clicks the crucible.
    assert menu == [("Mithril platebody", "Use")]
    assert clicks == [gf.CRUCIBLE_ID]
    assert ctx.state.data["gf_fill_attempts"] == 1


def test_fill_restocks_from_bank_when_items_missing(monkeypatch):
    ctx = _Ctx()
    chest = {"id": gf.BANK_CHEST_ID, "name": "Bank chest", "distance": 2,
             "screenX": 400, "screenY": 300, "x": 3375, "y": 11493, "plane": 0}
    _crucible(monkeypatch, extra_objects=[chest])
    _inv(monkeypatch, [])
    opened, withdrew = [], []
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: chest)
    monkeypatch.setattr(banker, "open_bank", lambda ctx_, obj, **k: opened.append(obj) or True)
    monkeypatch.setattr(banker, "deposit_all_inventory", lambda ctx_: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx_: None)

    def fake_withdraw(ctx_, cfg, fresh=False):
        withdrew.extend(cfg["withdraw"])
        _inv(monkeypatch, ["Mithril platebody"] * 4 + ["Adamant platelegs"] * 7)
    monkeypatch.setattr(banker, "withdraw_loadout", fake_withdraw)

    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    gf._fill_crucible(ctx, st)
    assert opened == [chest]
    assert {w["name"]: w["quantity"] for w in withdrew} == \
        {"Mithril platebody": 4, "Adamant platelegs": 7}
    assert ctx.state.stopped is None


def test_fill_bank_dry_safety_stops(monkeypatch):
    ctx = _Ctx()
    chest = {"id": gf.BANK_CHEST_ID, "name": "Bank chest", "distance": 2,
             "screenX": 400, "screenY": 300, "x": 3375, "y": 11493, "plane": 0}
    _crucible(monkeypatch, extra_objects=[chest])
    _inv(monkeypatch, [])
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: chest)
    monkeypatch.setattr(banker, "open_bank", lambda ctx_, obj, **k: True)
    monkeypatch.setattr(banker, "deposit_all_inventory", lambda ctx_: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx_: None)
    monkeypatch.setattr(banker, "withdraw_loadout", lambda ctx_, cfg, fresh=False: None)
    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    gf._fill_crucible(ctx, st)
    assert "out of" in ctx.state.stopped


def test_fill_attempt_ceiling(monkeypatch):
    ctx = _Ctx()
    _crucible(monkeypatch)
    _inv(monkeypatch, ["Mithril platebody"] * 4)
    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    for _ in range(gf.FILL_MAX_ATTEMPTS + 1):
        gf._fill_crucible(ctx, st)   # bar count never grows -> counter never resets
    assert "fill" in ctx.state.stopped.lower()


def test_answer_fill_dialogs_exact_option(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"open": True, "options": ["1", "5", "X", "All"]}
                        if (g, r) == ("ui", "dialogue") else None)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    gf._answer_fill_dialogs(ctx, count=5, accept_overflow=True)
    assert keys[0] == "2"


def test_answer_fill_dialogs_custom_amount(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"open": True, "options": ["1", "5", "X", "All"]}
                        if (g, r) == ("ui", "dialogue") else None)
    keys, typed = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "type_text", lambda ser, text: typed.append(text))
    gf._answer_fill_dialogs(ctx, count=3, accept_overflow=True)
    assert keys[0] == "3" and typed == ["3"]


def test_pour_clicks_crucible_with_expected_action(monkeypatch):
    ctx = _Ctx()
    _crucible(monkeypatch)
    kwargs = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: kwargs.update(k) or True)
    gf._pour(ctx, _st(phase="MOULD_SET", crucibleBarCount=28, crucibleState="FULL"))
    assert kwargs.get("expected_action") == "Pour"


# ---------------------------------------------------------------------------
# Task 8: pickup + free hands
# ---------------------------------------------------------------------------

def _jig(monkeypatch):
    jig = {"id": gf.MOULD_JIG_ID, "x": 3370, "y": 11488, "plane": 0,
           "screenX": 700, "screenY": 300, "name": "Mould jig"}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [jig])
    return jig


def test_pickup_clicks_jig(monkeypatch):
    ctx = _Ctx()
    _jig(monkeypatch)
    kwargs = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: kwargs.update(k) or True)
    gf.pickup_phase(ctx, _st(phase="PREFORM_READY"))
    assert kwargs.get("expected_action") == "Pick-up"
    assert ctx.state.data["gf_pickup_attempts"] == 1


def test_pickup_free_hands_chat_triggers_unequip_once(monkeypatch):
    ctx = _Ctx()
    _jig(monkeypatch)
    gf.on_event(ctx, "CHAT_MESSAGE",
                {"message": "You must free your hands so you can hold the preform."})
    assert ctx.state.data["gf_need_free_hands"] is True
    keys, menu = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "get_widget",
                        lambda g, c, index=None: {"screenX": 1028, "screenY": 470, "clickable": True})
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append(option) or True)
    gf.pickup_phase(ctx, _st(phase="PREFORM_READY"))
    # The recovery presses E, removes both hand slots, escapes -- and does NOT count as
    # a pickup attempt.
    assert keys[0] == "E" and keys[-1] == "ESCAPE"
    assert menu == ["Remove", "Remove"]
    assert ctx.state.data["gf_need_free_hands"] is False
    assert ctx.state.data.get("gf_pickup_attempts", 0) == 0


def test_pickup_ceiling(monkeypatch):
    ctx = _Ctx()
    _jig(monkeypatch)
    st = _st(phase="PREFORM_READY")
    for _ in range(gf.PICKUP_MAX_ATTEMPTS + 1):
        gf.pickup_phase(ctx, st)
    assert "pick up" in ctx.state.stopped.lower()


def test_on_event_tolerates_none_payload():
    ctx = _Ctx()
    gf.on_event(ctx, "CHAT_MESSAGE", None)
    gf.on_event(ctx, "TICK", {"tick": 1})
    assert ctx.state.stopped is None


# ---------------------------------------------------------------------------
# Task 9: refinement controller
# ---------------------------------------------------------------------------

def _machines(monkeypatch):
    objs = [
        {"id": 44619, "name": "Trip hammer", "x": 3363, "y": 11496, "plane": 0,
         "screenX": 800, "screenY": 300},
        {"id": 44620, "name": "Grindstone", "x": 3369, "y": 11493, "plane": 0,
         "screenX": 600, "screenY": 350},
        {"id": 44621, "name": "Polishing wheel", "x": 3362, "y": 11484, "plane": 0,
         "screenX": 500, "screenY": 420},
        {"id": gf.LAVA_POOL_ID, "name": "Lava pool", "x": 3372, "y": 11497, "plane": 0,
         "screenX": 650, "screenY": 450},
        {"id": gf.WATERFALL_ID, "name": "Waterfall", "x": 3357, "y": 11488, "plane": 0,
         "screenX": 250, "screenY": 150},
        {"id": gf.STORAGE_FULL_ID, "name": "Preform storage", "x": 3366, "y": 11485,
         "plane": 0, "screenX": 520, "screenY": 380},
    ]
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: objs)
    return {o["id"]: o for o in objs}


def test_refine_ready_idle_clicks_machine(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    gf.refine_phase(ctx, _st(temperatureStatus="READY", activity="NONE"))
    assert clicks == [44619]


def test_refine_working_out_of_band_interrupts_to_adjust(monkeypatch):
    # THE QUALITY GUARD: working while TOO_COLD damages the sword every action -- the
    # controller must interrupt with the heat adjust instead of watching it burn.
    ctx = _Ctx()
    _machines(monkeypatch)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    _serve_state(monkeypatch, _st(temperature=990, temperatureStatus="READY"))
    gf.refine_phase(ctx, _st(temperature=700, temperatureStatus="TOO_COLD",
                             activity="HAMMERING"))
    assert menu == [(gf.LAVA_POOL_ID, "Dunk-preform")]


def test_refine_working_never_reclicks(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    gf.refine_phase(ctx, _st(temperatureStatus="READY", activity="HAMMERING"))
    assert clicks == []    # overwork guard


def test_refine_sweet_spot_claims_once(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    st = _st(temperatureStatus="READY", activity="HAMMERING", sweetSpotState="AVAILABLE")
    gf.refine_phase(ctx, st)
    gf.refine_phase(ctx, st)     # same AVAILABLE window -> no second click
    assert clicks == [44619]
    gf.refine_phase(ctx, _st(temperatureStatus="READY", activity="HAMMERING",
                             sweetSpotState="CLAIMED"))
    gf.refine_phase(ctx, _st(temperatureStatus="READY", activity="HAMMERING",
                             sweetSpotState="AVAILABLE"))
    assert clicks == [44619, 44619]   # new window -> one more claim


def test_refine_too_cold_dunks_then_engages(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    menu, clicks = [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    # Serve a state whose temperature is already past target so the inner poll exits
    # on its first read.
    hot = _st(temperature=990, temperatureStatus="READY")
    _serve_state(monkeypatch, hot)
    cold = _st(temperature=700, temperatureStatus="TOO_COLD", activity="NONE")
    gf.refine_phase(ctx, cold)
    assert menu == [(gf.LAVA_POOL_ID, "Dunk-preform")]
    assert clicks == [44619]     # engaged the hammer right after the ramp


def test_refine_too_hot_quenches(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    _serve_state(monkeypatch, _st(temperature=400, temperatureStatus="READY",
                                  requiredTool="GRINDSTONE",
                                  minimumTemperature=410, maximumTemperature=590))
    gf.refine_phase(ctx, _st(temperature=700, temperatureStatus="TOO_HOT",
                             requiredTool="GRINDSTONE", activity="NONE",
                             minimumTemperature=410, maximumTemperature=590))
    assert menu == [(gf.WATERFALL_ID, "Quench-preform")]


def test_refine_stored_takes_from_storage(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    kwargs = {}
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: (clicks.append(ent["id"]), kwargs.update(k)) or True)
    gf.refine_phase(ctx, _st(phase="STORED", preformState="STORED"))
    assert clicks == [gf.STORAGE_FULL_ID]
    assert kwargs.get("expected_action") == "Take-preform"


def test_watch_progress_stall_reengages_and_dead_clicks_stop(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    st = _st(temperatureStatus="READY", activity="HAMMERING", completion=200)
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    gf.refine_phase(ctx, st)               # working, progress mark set
    assert clicks == []
    now[0] += gf.WORK_STALL_S + 1
    gf.refine_phase(ctx, st)               # stalled -> re-engage (dead click 1)
    assert clicks == [44619]
    for _ in range(gf.DEAD_CLICK_LIMIT):
        now[0] += gf.WORK_STALL_S + 1
        gf.refine_phase(ctx, st)
    assert "no progress" in ctx.state.stopped.lower()
