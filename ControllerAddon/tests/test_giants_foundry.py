import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

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


def test_plan_fill_from_empty_trims_to_an_exact_fit():
    # MEASURED: 14 mith via 4-bar bodies quantizes to 4 items (16 bars); the human then
    # added 7 legs (14) and accepted a 2-bar overflow. The planner trims the LAST (cheapest)
    # item instead: 6 legs (12) lands on the 28 cap exactly, 2 bars under the adamant
    # target and nothing lost -- a marginally different alloy beats wasted metal.
    steps, err = gf.plan_fill(FILL, {}, max_overflow_loss=2)
    assert err is None
    assert steps == [
        {"item": "Mithril platebody", "bars_each": 4, "count": 4},
        {"item": "Adamant platelegs", "bars_each": 2, "count": 6},
    ]


def test_plan_fill_rejects_overflow_beyond_cap():
    # 26 bars of space, only 4-bar bodies available: 7 bodies = 28 (loss 2), 6 = 24 (short).
    only_bodies = [{"item": "Mithril platebody", "metal": "MITHRIL", "bars_each": 4,
                    "target_bars": 28}]
    steps, err = gf.plan_fill(only_bodies, {"ADAMANT": 2}, max_overflow_loss=1)
    assert steps == []
    assert "overflow" in err


FILL_WITH_ALTERNATIVES = [
    {"metal": "MITHRIL", "target_bars": 14,
     "items": [{"item": "Mithril platebody", "bars_each": 4},
               {"item": "Mithril platelegs", "bars_each": 2}]},
    {"metal": "ADAMANT", "target_bars": 14,
     "items": [{"item": "Adamant platelegs", "bars_each": 2},
               {"item": "Adamant platebody", "bars_each": 4}]},
]


def test_metal_items_catalogue_matches_the_wiki_bar_values():
    items = dict(gf.metal_items("Adamant"))
    assert items["Adamant platebody"] == 4
    assert items["Adamant platelegs"] == 2 and items["Adamant 2h sword"] == 2
    assert items["Adamantite bar"] == 1 and items["Adamant scimitar"] == 1   # not "Adamant bar"
    assert "Adamant bar" not in items
    assert dict(gf.metal_items("Mithril"))["Mithril bar"] == 1
    assert dict(gf.metal_items("Rune"))["Runite bar"] == 1
    assert "Adamant med helm" not in items      # 1-bar smith -> worth 0


def test_plan_fill_combines_whatever_the_bank_holds():
    # USER 2026-09-03: "write mith and ada to get all items, we have plenty of bars".
    # 2 platebodies (8) + 6 bars covers 14 adamant; mithril from bodies as before.
    available = {"mithril platebody": 100, "adamant platebody": 2, "adamantite bar": 40}
    steps, err = gf.plan_fill(gf.DEFAULT_CONFIG["fill"], {}, max_overflow_loss=2,
                              available=available)
    assert err is None
    assert steps == [
        {"item": "Mithril platebody", "bars_each": 4, "count": 4},
        {"item": "Adamant platebody", "bars_each": 4, "count": 2},
        {"item": "Adamantite bar", "bars_each": 1, "count": 4},  # trimmed to an exact 28
    ]


def test_fill_counts_a_bar_stack_by_quantity(monkeypatch):
    ctx = _Ctx()
    _crucible(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Adamantite bar", "qty": 40}])
    keys = []
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, ent, option, **k: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    monkeypatch.setattr(gf, "_answer_fill_dialogs", lambda ctx_, count, accept_overflow: keys.append(count))
    st = _st(phase="MOULD_SET", crucibleContents={"MITHRIL": 16}, crucibleBarCount=16)
    gf._fill_crucible(ctx, st)
    assert keys == [12]                        # 12 bars wanted, 40 in ONE slot


def test_plan_fill_uses_the_first_alternative_with_stock():
    # MEASURED live 2026-09-03 (gf_live3): the bank held exactly 7 Adamant platelegs, the
    # first sword used them all, and the second stopped on "bank is out of Adamant
    # platelegs" with 9 Adamant platebodies sitting in the same bank.
    available = {"mithril platebody": 127, "adamant platelegs": 0, "adamant platebody": 9}
    steps, err = gf.plan_fill(FILL_WITH_ALTERNATIVES, {}, max_overflow_loss=2,
                              available=available)
    assert err is None
    assert steps == [
        {"item": "Mithril platebody", "bars_each": 4, "count": 4},
        {"item": "Adamant platebody", "bars_each": 4, "count": 3},   # 16 + 12 = 28 exactly
    ]


def test_plan_fill_tops_up_short_stock_with_the_primary_item():
    # MEASURED live 2026-09-03 (gf_live20): four leftover Adamant battleaxes in the
    # inventory (8 bars) made the held-stock plan reach 24/28 and safety-stop instead of
    # sending the routine to the bank for the rest.
    available = {"mithril platebody": 4, "adamant battleaxe": 4}
    steps, err = gf.plan_fill(gf.DEFAULT_CONFIG["fill"], {}, max_overflow_loss=2,
                              available=available)
    assert err is None
    assert sum(s["bars_each"] * s["count"] for s in steps) == 28
    assert "Adamant platebody" in [s["item"] for s in steps]      # the top-up to fetch


def test_plan_fill_trims_overflow_across_steps_smallest_first():
    # gf_live21: leftovers can make a 4-bar item the LAST step; the overflow must come off
    # the smallest items wherever they sit in the plan.
    available = {"mithril platebody": 3, "mithril bar": 40, "adamant platebody": 9}
    steps, err = gf.plan_fill(gf.DEFAULT_CONFIG["fill"], {}, max_overflow_loss=2,
                              available=available)
    assert err is None
    assert sum(s["bars_each"] * s["count"] for s in steps) == 28
    assert all(s["count"] > 0 for s in steps)


def test_plan_fill_unknown_stock_uses_the_primary_item():
    steps, err = gf.plan_fill(FILL_WITH_ALTERNATIVES, {}, max_overflow_loss=2, available=None)
    assert err is None
    assert [s["item"] for s in steps] == ["Mithril platebody", "Adamant platelegs"]


def test_plan_fill_skips_an_alternative_with_too_little_stock():
    available = {"mithril platebody": 127, "adamant platelegs": 2, "adamant platebody": 9}
    steps, err = gf.plan_fill(FILL_WITH_ALTERNATIVES, {}, max_overflow_loss=2,
                              available=available)
    assert err is None
    assert steps[1]["item"] == "Adamant platebody"


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
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {"open": False})
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: None)
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [])
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "hover_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "click_human", lambda *a, **k: None)
    monkeypatch.setattr(controller, "hover_matches", lambda *a, **k: False)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **k: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "type_text", lambda ser, text: None)
    monkeypatch.setattr(controller, "type_digits_and_enter", lambda ser, text: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "mark_busy", lambda s: None)
    monkeypatch.setattr(controller, "object_refresh_target",
                        lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "npc_index_refresh_target",
                        lambda idx: (lambda: None))
    monkeypatch.setattr(gf.time, "sleep", lambda s: None)


def _pool_recorder(menu, clicks=None, click_value=None):
    """click_entity stub: a pool click (carries expected_action -- Dunk/Quench are the
    left-click default, USER 2026-09-03) is recorded into `menu` as (id, option) exactly
    like the old click_menu_action record; any other click goes into `clicks`."""
    def stub(ser, ent, **k):
        if "expected_action" in k:
            menu.append((ent["id"], k["expected_action"]))
        elif clicks is not None:
            clicks.append(click_value() if click_value else ent["id"])
        return True
    return stub


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


def test_tick_clears_dead_clicks_when_completion_grows(monkeypatch):
    # gf_dead_clicks used to reset ONLY inside _watch_progress -- a handler the dispatcher
    # can stop routing to (the mh_contact_casts lesson) -- so a healthy session that left
    # REFINING for other work could accumulate dead clicks toward DEAD_CLICK_LIMIT forever.
    # _reset_counters now tracks completion growth itself, every tick, regardless of phase.
    ctx = _Ctx()
    d = ctx.state.data
    d["gf_dead_clicks"] = gf.DEAD_CLICK_LIMIT - 1
    d["gf_last_completion"] = 50
    _serve_state(monkeypatch, _st(completion=100))
    monkeypatch.setitem(gf.PHASE_HANDLERS, "REFINING", lambda ctx, st: None)
    gf.tick(ctx)
    assert d["gf_dead_clicks"] == 0
    assert d["gf_last_completion"] == 100


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


def test_hand_in_npc_text_only_dialogue_advances_not_clicks(monkeypatch):
    # The real m8aq Dialogue reader has NO open/isOpen/visible/type field -- a "click here
    # to continue" stage shows up as npcText/playerText/itemText with an EMPTY options
    # list, which used to collapse to bool(options) == False (read as closed) and let
    # hand_in re-click Kovac mid-dialogue, burning its ceiling.
    ctx = _Ctx()
    _kovac(monkeypatch)
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"npcText": "Hmm... sword look good.", "options": []}
                        if (g, r) == ("ui", "dialogue") else None)
    keys, clicks = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda *a, **k: clicks.append(a) or True)
    gf.hand_in_phase(ctx, _st(phase="READY_TO_HAND_IN", readyToHandIn=True))
    assert keys == ["SPACE"] and clicks == []


def test_dialogue_options_tolerates_both_shapes():
    # Entry 0 is always the menu title (see test_dialogue_options_drop_the_title_and_blanks).
    assert gf._dialogue_options({"options": ["Select an Option", "a", "b"]}) == ["a", "b"]
    assert gf._dialogue_options({"options": [{"text": "Select an Option"}, {"text": "a"},
                                             {"text": "b"}]}) == ["a", "b"]
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


def test_select_moulds_no_row_ceiling_stops(monkeypatch):
    # get_widget_children's fixture default ([]) means no row is ever selectable --
    # this must not spin forever.
    ctx = _Ctx()
    st = _st(phase="SELECTING_MOULDS", mouldSetupOpen=True, selectedMoulds={},
             usableMoulds=list(gf.MOULDS))
    for _ in range(gf.MOULD_NOROW_LIMIT + 1):
        gf.prepare_phase(ctx, st)
    assert "mould row" in ctx.state.stopped.lower()


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
    # /inventory is a bare LIST (controller.get_inventory), not {"items": [...]}.
    items = [{"name": n, "screenX": 900, "screenY": 400 + 10 * i, "qty": 1}
             for i, n in enumerate(names)]
    monkeypatch.setattr(controller, "get_inventory", lambda: items)
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

    # MEASURED live 2026-09-03 (gf_live1): this user's bank holds 952 items and the
    # platelegs sit in tab 2 (slot 487), so /bank reports a screen position far below the
    # window for them and every bare withdraw click is skipped -- which the old code then
    # reported as "bank is out of Adamant platelegs". The restock must go through
    # banker.search_and_withdraw (search first, then click) per item, never the blind
    # withdraw_loadout loop.
    def fake_search_and_withdraw(ctx_, name, quantity, contains=False):
        withdrew.append((name, quantity))
        _inv(monkeypatch, ["Mithril platebody"] * 4 + ["Adamant platebody"] * 3)
        return True
    monkeypatch.setattr(banker, "search_and_withdraw", fake_search_and_withdraw)

    def blind_withdraw(*a, **k):
        raise AssertionError("blind withdraw_loadout used -- off-screen bank slots are skipped")
    monkeypatch.setattr(banker, "withdraw_loadout", blind_withdraw)

    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    gf._fill_crucible(ctx, st)
    assert opened == [chest]
    assert withdrew == [("Mithril platebody", 4), ("Adamant platebody", 3)]   # 16 + 12 = 28
    assert ctx.state.stopped is None


def test_fill_restock_skips_open_bank_click_when_bank_already_open(monkeypatch):
    # A restart (or a manual cleanup) can leave the bank interface up; clicking the chest's
    # tile through the open interface lands on whatever bank widget covers that pixel.
    ctx = _Ctx()
    chest = {"id": gf.BANK_CHEST_ID, "name": "Bank chest", "distance": 2,
             "screenX": 400, "screenY": 300, "x": 3375, "y": 11493, "plane": 0}
    _crucible(monkeypatch, extra_objects=[chest])
    _inv(monkeypatch, [])
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {"open": True})
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: chest)

    def must_not_open(ctx_, obj, **k):
        raise AssertionError("open_bank called with the bank already open")
    monkeypatch.setattr(banker, "open_bank", must_not_open)
    monkeypatch.setattr(banker, "deposit_all_inventory", lambda ctx_: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx_: None)

    def fake_search_and_withdraw(ctx_, name, quantity, contains=False):
        _inv(monkeypatch, ["Mithril platebody"] * 4 + ["Adamant platebody"] * 3)
        return True
    monkeypatch.setattr(banker, "search_and_withdraw", fake_search_and_withdraw)
    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    gf._fill_crucible(ctx, st)
    assert ctx.state.stopped is None


def test_fill_restock_replans_from_the_open_banks_stock(monkeypatch):
    # MEASURED live 2026-09-03 (gf_live4): a transient /api/storage/bank read failure at
    # tick 1 left the pre-bank plan on the primary item, and the restock stopped on "out of
    # Adamant platelegs" with 9 Adamant platebodies in the same bank. Once the bank is OPEN
    # its item list is the authoritative stock -- re-plan against it before withdrawing.
    ctx = _Ctx()
    chest = {"id": gf.BANK_CHEST_ID, "name": "Bank chest", "distance": 2,
             "screenX": 400, "screenY": 300, "x": 3375, "y": 11493, "plane": 0}
    _crucible(monkeypatch, extra_objects=[chest])
    _inv(monkeypatch, [])
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: chest)
    monkeypatch.setattr(banker, "open_bank", lambda ctx_, obj, **k: True)
    monkeypatch.setattr(banker, "deposit_all_inventory", lambda ctx_: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx_: None)
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {
        "open": True,
        "items": [{"name": "Mithril platebody", "qty": 127},
                  {"name": "Adamant platelegs", "qty": 0},
                  {"name": "Adamant platebody", "qty": 9}]})
    withdrew = []

    def fake_search_and_withdraw(ctx_, name, quantity, contains=False):
        withdrew.append((name, quantity))
        _inv(monkeypatch, ["Mithril platebody"] * 4 + ["Adamant platebody"] * 3)
        return True
    monkeypatch.setattr(banker, "search_and_withdraw", fake_search_and_withdraw)
    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    gf._fill_crucible(ctx, st)
    assert withdrew == [("Mithril platebody", 4), ("Adamant platebody", 3)]
    assert ctx.state.stopped is None


def test_fill_uses_a_held_alternative_without_a_bank_trip(monkeypatch):
    # MEASURED live 2026-09-03 (gf_live5): 16 mithril already in the crucible and 3 Adamant
    # platebodies in hand, yet the pre-bank plan named the primary (platelegs), the held
    # bodies never matched it, and every tick became a bank trip. What we HOLD decides the
    # item outside the bank; the open bank's stock decides it inside.
    ctx = _Ctx()
    _crucible(monkeypatch)
    _inv(monkeypatch, ["Adamant platebody"] * 3)
    menu, clicks, keys = [], [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["name"], option)) or True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    trips = []
    monkeypatch.setattr(gf, "_restock_from_bank", lambda ctx_, steps, st: trips.append(steps))
    st = _st(phase="MOULD_SET", crucibleContents={"MITHRIL": 16}, crucibleBarCount=16)
    gf._fill_crucible(ctx, st)
    assert trips == []
    assert menu == [("Adamant platebody", "Use")]
    assert clicks == [gf.CRUCIBLE_ID]


def test_fill_restock_trips_have_a_ceiling(monkeypatch):
    ctx = _Ctx()
    chest = {"id": gf.BANK_CHEST_ID, "name": "Bank chest", "distance": 2,
             "screenX": 400, "screenY": 300, "x": 3375, "y": 11493, "plane": 0}
    _crucible(monkeypatch, extra_objects=[chest])
    _inv(monkeypatch, [])
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: chest)
    monkeypatch.setattr(banker, "open_bank", lambda ctx_, obj, **k: True)
    monkeypatch.setattr(banker, "deposit_all_inventory", lambda ctx_: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx_: None)
    # withdraw "succeeds" but the inventory read stays empty -- a trip that can never progress
    monkeypatch.setattr(banker, "search_and_withdraw", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Mithril platebody"}] * 4 + [{"name": "Adamant platebody"}] * 3)
    st = _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0)
    # held check sees the items only AFTER the trip; force the pre-trip read empty each time
    calls = {"n": 0}
    real_inv = controller.get_inventory
    def flaky_inv():
        calls["n"] += 1
        return [] if calls["n"] % 2 == 1 else real_inv()
    monkeypatch.setattr(controller, "get_inventory", flaky_inv)
    for _ in range(gf.RESTOCK_MAX_TRIPS + 1):
        gf._fill_crucible(ctx, st)
    assert ctx.state.stopped is not None
    assert "restock" in ctx.state.stopped.lower()


def test_dialogue_options_drop_the_title_and_blanks():
    # MEASURED live 2026-09-03 (gf_live6): the m8aq Dialogue reader returns every non-hidden
    # child of Chatmenu.OPTIONS in order -- the TITLE first, then the options, then blank
    # trailing children. Number keys count real options from 1, so "index + 1" over the raw
    # list pressed 3 for the option "3" that sat at raw index 2 -- selecting "X" instead.
    raw = {"options": ["How many would you like to add?", "1", "3", "X", "All", "", ""]}
    assert gf._dialogue_options(raw) == ["1", "3", "X", "All"]


def test_fill_dialog_presses_the_key_of_the_matching_option(monkeypatch):
    ctx = _Ctx()
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    prompt = {"options": ["How many would you like to add?", "1", "3", "X", "All", "", ""]}
    served = [prompt, {}]
    monkeypatch.setattr(gf, "_dialogue", lambda: served.pop(0) if served else {})
    gf._answer_fill_dialogs(ctx, 3, accept_overflow=True)
    assert keys == ["2"]


def test_fill_dialog_uses_x_for_an_unlisted_count(monkeypatch):
    ctx = _Ctx()
    keys, typed = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "type_digits_and_enter", lambda ser, text: typed.append(text))
    prompt = {"options": ["How many would you like to add?", "1", "5", "X", "All", ""]}
    served = [prompt, {}]
    monkeypatch.setattr(gf, "_dialogue", lambda: served.pop(0) if served else {})
    gf._answer_fill_dialogs(ctx, 4, accept_overflow=True)
    assert keys == ["3"] and typed == ["4"]


def test_choose_option_counts_from_the_first_real_option(monkeypatch):
    ctx = _Ctx()
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    d = {"options": ["Select an Option", "Yes, I'd like a new commission.", "No thanks.", ""]}
    assert gf._choose_option(ctx, d, ("commission",), fallback_key="9") is True
    assert keys == ["1"]


def test_commission_phase_answers_an_option_menu_by_key(monkeypatch):
    # MEASURED live 2026-09-03 (gf_live8): restarted with the hand-in chain's "Receive
    # another commission?" Yes/No menu still open -- Space never selects an option, so the
    # commission handler pressed Space once a tick for two minutes.
    ctx = _Ctx()
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(gf, "_dialogue", lambda: {
        "options": ["Receive another commission?", "Yes.", "No.", "", ""]})
    gf.get_commission_phase(ctx, _st(phase="AWAITING_COMMISSION", commissionTypes=[]))
    assert keys == ["1"]


def test_mould_ui_prefers_the_planned_row_on_a_score_tie(monkeypatch):
    # MEASURED live 2026-09-03 (gf_live13-15): FLAT/HEAVY scores Defender Base and Chopper
    # Forte +1 equally; the plan picked Defender Base (catalogue order) while the UI loop
    # picked Chopper Forte +1 (list order), and the mismatch re-clicked once a tick.
    ctx = _Ctx()
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent.get("text")) or True)
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: {"screenX": 1, "screenY": 1})
    rows = [{"text": "Chopper Forte +1", "screenX": 400, "screenY": 200},
            {"text": "Defender Base", "screenX": 400, "screenY": 240}]
    monkeypatch.setattr(controller, "get_widget_children",
                        lambda g, c: rows if (g, c) == (gf.MOULD_GROUP, gf.MOULD_LIST_CHILD) else [])
    st = _st(commissionTypes=["FLAT", "HEAVY"], usableMoulds=[], selectedMoulds={},
             mouldSetupOpen=True)
    plan = gf.best_moulds(["FLAT", "HEAVY"], [])
    assert plan["RICASSO"] == "DEFENDER_BASE"
    gf._select_moulds_in_ui(ctx, st, plan)
    assert clicks[-1] == "Defender Base"


def test_withdraw_by_metal_searches_once_per_metal_and_clicks_the_results(monkeypatch):
    # USER 2026-09-03: search "mith"/"ada", not each full item name.
    ctx = _Ctx()
    searches, modes, clicks, cleared = [], [], [], []
    monkeypatch.setattr(banker, "search_bank", lambda ctx_, q: searches.append(q) or True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx_, m: modes.append(m) or True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx_: cleared.append(True) or True)
    monkeypatch.setattr(banker, "search_and_withdraw",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("full-name search used")))
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {
        "open": True,
        "tabs": [{"index": 0, "screenY": 140}],
        "quantityButtons": {"one": {"screenX": 719, "screenY": 1006}},
        "items": [{"name": "Mithril platebody", "qty": 100, "screenX": 650, "screenY": 300},
                  {"name": "Adamant plateskirt", "qty": 1, "screenX": 700, "screenY": 300},
                  {"name": "Adamant battleaxe", "qty": 9, "screenX": 750, "screenY": 340}]})
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["name"]) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: clicks.append((ent["name"], option)) or True)
    gf._withdraw_by_metal(ctx, {"Mithril platebody": 4, "Adamant plateskirt": 1,
                               "Adamant battleaxe": 5})
    assert searches == ["mith", "adam"]          # USER: "adam" for all adamant items
    # USER: "clicking 12 times on a bar isn't very human" -- 5 goes through Withdraw-5
    assert clicks == ["Mithril platebody"] * 4 + ["Adamant plateskirt"] +         [("Adamant battleaxe", "Withdraw-5")]
    assert len(cleared) == 2


def test_withdraw_count_uses_menu_chunks_then_single_clicks(monkeypatch):
    ctx = _Ctx()
    slot = {"name": "Adamantite bar", "screenX": 650, "screenY": 690}
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: clicks.append("1") or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: clicks.append(option) or True)
    gf._withdraw_count(ctx, slot, 12)
    assert clicks == ["Withdraw-10", "1", "1"]
    clicks.clear()
    gf._withdraw_count(ctx, slot, 7)
    assert clicks == ["Withdraw-5", "1", "1"]


def test_withdraw_by_metal_falls_back_for_a_result_below_the_grid(monkeypatch):
    ctx = _Ctx()
    searches, fallback = [], []
    monkeypatch.setattr(banker, "search_bank", lambda ctx_, q: searches.append(q) or True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx_, m: True)
    monkeypatch.setattr(banker, "clear_bank_search", lambda ctx_: True)
    monkeypatch.setattr(banker, "search_and_withdraw",
                        lambda ctx_, name, n, contains=False: fallback.append((name, n)) or True)
    monkeypatch.setattr(controller, "get_bank", lambda fresh=False: {
        "open": True, "tabs": [{"index": 0, "screenY": 140}],
        "quantityButtons": {"one": {"screenX": 719, "screenY": 1006}},
        "items": [{"name": "Adamantite bar", "qty": 40, "screenX": 650, "screenY": 1500}]})
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    gf._withdraw_by_metal(ctx, {"Adamantite bar": 6})
    assert fallback == [("Adamantite bar", 6)]


def test_pickup_ceiling_does_not_stop_when_the_preform_is_already_in_hand(monkeypatch):
    # MEASURED live 2026-09-03 (gf_live18): the third click picked the preform up and the
    # ceiling fired on that same tick off a read one tick stale.
    ctx = _Ctx()
    ctx.state.data["gf_pickup_attempts"] = gf.PICKUP_MAX_ATTEMPTS
    _serve_state(monkeypatch, _st(phase="REFINING", preformState="EQUIPPED"))
    gf.pickup_phase(ctx, _st(phase="PREFORM_READY", preformState="NONE"))
    assert ctx.state.stopped is None
    assert ctx.state.data["gf_pickup_attempts"] == 0


def test_pickup_withheld_click_escalates_to_the_menu(monkeypatch):
    ctx = _Ctx()
    jig = {"id": gf.MOULD_JIG_ID, "name": "Mould jig (Poured metal)", "x": 3371, "y": 11489,
           "plane": 0, "screenX": 500, "screenY": 400}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [jig])
    menu = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: controller.CLICK_WITHHELD)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append(option) or True)
    gf.pickup_phase(ctx, _st(phase="PREFORM_READY", preformState="NONE"))
    assert menu == ["Pick-up"]


def test_bars_fill_through_the_crucible_interface(monkeypatch):
    # USER 2026-09-03: click the crucible (Fill) and pick the bar in its interface instead
    # of Use-bar-on-crucible one bar per tick.
    ctx = _Ctx()
    _crucible(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Adamantite bar", "qty": 1}] * 12)
    clicks, answered = [], []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append((ent.get("id") or ent.get("group"),
                                                             k.get("expected_action"))) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: clicks.append(("menu", option)) or True)
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": 149, "child": 0, "index": 3, "name": "<col=ff9040>Adamantite bar</col>",
         "screenX": 2200, "screenY": 900},                       # the inventory slot: skipped
        {"group": 270, "child": 14, "index": 2, "name": "<col=ff9040>Adamantite bar</col>",
         "screenX": 900, "screenY": 700, "actions": ["Add"]}])
    monkeypatch.setattr(gf, "_answer_fill_dialogs",
                        lambda ctx_, count, accept_overflow: answered.append(count))
    st = _st(phase="MOULD_SET", crucibleContents={"MITHRIL": 16}, crucibleBarCount=16)
    gf._fill_crucible(ctx, st)
    assert clicks == [(gf.CRUCIBLE_ID, "Fill"), (270, None)]
    assert answered == [12]


def test_bars_fall_back_to_use_when_the_interface_shows_no_entry(monkeypatch):
    ctx = _Ctx()
    _crucible(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Adamantite bar", "qty": 1}] * 12)
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    monkeypatch.setattr(gf.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent.get("id")) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: clicks.append(("menu", option)) or True)
    monkeypatch.setattr(gf, "_answer_fill_dialogs", lambda ctx_, count, accept_overflow: None)
    st = _st(phase="MOULD_SET", crucibleContents={"MITHRIL": 16}, crucibleBarCount=16)
    gf._fill_crucible(ctx, st)
    assert ("menu", "Use") in clicks


def test_fill_opens_the_inventory_tab_before_using_an_item(monkeypatch):
    # gf_live27: after a pause/resume the stats tab was up; the "platebody" right-click
    # offered "View Attack guide" three times and the fill ceiling stopped the run.
    ctx = _Ctx()
    _crucible(monkeypatch)
    _inv(monkeypatch, ["Mithril platebody"] * 4)
    order = []
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: order.append("tab") or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: order.append(option) or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    monkeypatch.setattr(gf, "_answer_fill_dialogs", lambda ctx_, count, accept_overflow: None)
    gf._fill_crucible(ctx, _st(phase="MOULD_SET", crucibleContents={}, crucibleBarCount=0))
    assert order[:2] == ["tab", "Use"]


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
    monkeypatch.setattr(banker, "search_and_withdraw", lambda *a, **k: False)
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
                        lambda g, r: {"open": True, "options": ["How many would you like to add?",
                                                               "1", "5", "X", "All", ""]}
                        if (g, r) == ("ui", "dialogue") else None)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    gf._answer_fill_dialogs(ctx, count=5, accept_overflow=True)
    assert keys[0] == "2"


def test_answer_fill_dialogs_custom_amount(monkeypatch):
    # count=3 has no matching option key ("1"/"5") and no confirm text yet -- the "X"
    # (custom amount) option is picked, and the typed amount MUST go through
    # type_digits_and_enter (which presses Enter), not type_text (which does not).
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_api",
                        lambda g, r: {"open": True, "options": ["How many would you like to add?",
                                                               "1", "5", "X", "All", ""]}
                        if (g, r) == ("ui", "dialogue") else None)
    keys, typed = [], []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: keys.append(key))
    monkeypatch.setattr(controller, "type_digits_and_enter",
                        lambda ser, text: typed.append(text))
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


def test_pour_ceiling_stops(monkeypatch):
    ctx = _Ctx()
    _crucible(monkeypatch)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    st = _st(phase="MOULD_SET", crucibleBarCount=28, crucibleState="FULL")
    for _ in range(gf.POUR_MAX_ATTEMPTS + 1):
        gf._pour(ctx, st)   # nothing ever advances the phase past MOULD_SET
    assert "pour" in ctx.state.stopped.lower()


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
    # a pickup attempt. The firmware only knows "ESC", not "ESCAPE".
    assert keys[0] == "E" and keys[-1] == "ESC"
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
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu))
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


def test_refine_quality_drop_streak_skips_action(monkeypatch):
    # THE QUALITY GUARD (2): qualityLost growing while temperatureStatus says READY on two
    # CONSECUTIVE reads means something is wrong with our belief -- skip acting rather than
    # compounding the damage. A single growing read is not enough (ordinary one-tick lag).
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    base = dict(temperatureStatus="READY", activity="NONE")
    # Each read sits past the previous click's stall window -- a click is never repeated
    # inside WORK_STALL_S any more (the polishing-wheel overwork lesson), so the reads
    # here are spaced the way a real stalled machine would present them.
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    gf.refine_phase(ctx, _st(qualityLost=0, **base))       # baseline read -- acts
    assert clicks == [44619]
    now[0] += gf.WORK_STALL_S + 1
    gf.refine_phase(ctx, _st(qualityLost=5, **base))       # growth #1 -- still acts
    assert clicks == [44619, 44619]
    now[0] += gf.WORK_STALL_S + 1
    gf.refine_phase(ctx, _st(qualityLost=10, **base))      # growth #2 in a row -- skip
    assert clicks == [44619, 44619]
    gf.refine_phase(ctx, _st(qualityLost=10, **base))      # non-growing read resets streak
    assert clicks == [44619, 44619, 44619]


def test_refine_too_cold_dunks_then_engages(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    menu, clicks = [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu, clicks))
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
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu))
    _serve_state(monkeypatch, _st(temperature=400, temperatureStatus="READY",
                                  requiredTool="GRINDSTONE",
                                  minimumTemperature=410, maximumTemperature=590))
    gf.refine_phase(ctx, _st(temperature=700, temperatureStatus="TOO_HOT",
                             requiredTool="GRINDSTONE", activity="NONE",
                             minimumTemperature=410, maximumTemperature=590))
    assert menu == [(gf.WATERFALL_ID, "Quench-preform")]


def test_adjust_heat_ceiling_stops(monkeypatch):
    # A pool click that keeps failing (click_menu_action -> False) must not retry forever.
    ctx = _Ctx()
    _machines(monkeypatch)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, ent, option, **k: False)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: False)
    st = _st(temperature=700, temperatureStatus="TOO_COLD", activity="NONE")
    for _ in range(gf.HEAT_FAIL_LIMIT + 1):
        gf._adjust_heat_and_engage(ctx, st, "TRIP_HAMMER")
    assert "heat adjust" in ctx.state.stopped.lower()


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


def test_refine_stored_finds_storage_via_impostor_id(monkeypatch):
    # 44633/44634 are multiloc IMPOSTOR variants of the BASE object 44778 -- ObjectProvider
    # reports the base id in "id" and the impostor in "impostorId" (ObjectProvider.
    # buildObjectData). Matching only on "id" == STORAGE_FULL_ID (the pre-fix behaviour)
    # never finds a real scan, which always reports the base id.
    ctx = _Ctx()
    storage = {"id": gf.STORAGE_BASE_ID, "impostorId": gf.STORAGE_FULL_ID,
               "x": 3366, "y": 11485, "plane": 0, "screenX": 520, "screenY": 380,
               "name": "Preform storage"}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [storage])
    kwargs = {}
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: (clicks.append(ent["id"]), kwargs.update(k)) or True)
    gf.refine_phase(ctx, _st(phase="STORED", preformState="STORED"))
    assert clicks == [gf.STORAGE_BASE_ID]
    assert kwargs.get("expected_action") == "Take-preform"


def test_refine_stored_storage_not_found_ceiling(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [])
    st = _st(phase="STORED", preformState="STORED")
    for _ in range(gf.STORAGE_NOT_FOUND_LIMIT + 1):
        gf.refine_phase(ctx, st)
    assert "preform storage" in ctx.state.stopped.lower()


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


def test_refine_recent_progress_counts_as_working_when_activity_flickers(monkeypatch):
    # MEASURED live 2026-09-03 (gf_live2 + recorder): the polishing animation (9454) plays
    # ~0.7s of each 1.2s wheel cycle and the player reads as idle (activity NONE) the rest,
    # so a random poll sees "not working" ~40% of the time. Every such poll re-clicked the
    # wheel, every re-click forfeited an action ("overwork the preform"), five in a row hit
    # DEAD_CLICK_LIMIT, and the stop left the wheel running while the preform went cold
    # (quality 147 -> 67). Progress advancing recently IS working, whatever the animation.
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    st = _st(requiredTool="POLISHING_WHEEL", minimumTemperature=59, maximumTemperature=273,
             temperature=150, temperatureStatus="READY", activity="POLISHING", completion=200)
    gf.refine_phase(ctx, st)                       # working: mark set at 200
    now[0] += 1.0
    gf.refine_phase(ctx, dict(st, activity="POLISHING", completion=210))   # progress
    now[0] += 0.7
    gf.refine_phase(ctx, dict(st, activity="NONE", completion=210))        # flicker
    assert clicks == []
    now[0] += gf.WORK_STALL_S + 1
    gf.refine_phase(ctx, dict(st, activity="NONE", completion=210))        # real stall
    assert clicks == [44621]


def test_refine_click_commit_window_budgets_the_walk(monkeypatch):
    # MEASURED live 2026-09-03: five "Use Trip hammer" clicks on five consecutive ticks
    # while the player was still walking to it (exactly DEAD_CLICK_LIMIT) -- a queued
    # interaction is performed on ARRIVAL, so the click gets base + walk before a repeat.
    ctx = _Ctx()
    machines = _machines(monkeypatch)
    machines[44619]["distance"] = 10
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    st = _st(temperatureStatus="READY", activity="NONE", completion=100)
    gf.refine_phase(ctx, st)
    assert clicks == [44619]
    for _ in range(6):
        now[0] += 1.1
        gf.refine_phase(ctx, st)                   # walking: activity NONE, no progress
    assert clicks == [44619]                       # inside WORK_FIRST_ACTION_S + 10 * 0.6
    assert ctx.state.stopped is None
    now[0] += gf.WORK_FIRST_ACTION_S + 10 * gf.WALK_S_PER_TILE
    gf.refine_phase(ctx, st)
    assert clicks == [44619, 44619]                # window spent -> one retry


def test_refine_tool_change_resets_progress_memory(monkeypatch):
    # A section boundary changes requiredTool; the previous tool's recent progress must
    # not make the NEXT machine wait out a stall window before its first click.
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    monkeypatch.setattr(gf.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    st = _st(temperatureStatus="READY", activity="HAMMERING", completion=180)
    gf.refine_phase(ctx, st)
    now[0] += 1.0
    gf.refine_phase(ctx, dict(st, completion=200))            # progress, still hammer
    now[0] += 1.0
    gf.refine_phase(ctx, _st(requiredTool="GRINDSTONE", minimumTemperature=410,
                             maximumTemperature=590, temperature=500,
                             temperatureStatus="READY", activity="NONE", completion=200))
    assert clicks == [44620]


def test_ramp_aim_caps_the_target_one_step_inside_the_band():
    # easy-giantsfoundry's measured DX_1 curve: Dunk/Quench deltas per tick run
    # 27,30,33,37,41,45,49,53,57,62,67,72,77,83,89,95. MEASURED live 2026-09-03 (gf_live7):
    # a poll that stopped at 935 for a 939 target peaked at 997 one tick later and the
    # hammer acted out of band twice (10 quality). The first reading past the aim may
    # overshoot it by one accelerating step, so the aim sits that far inside the band.
    assert gf.ramp_aim(939, 53, 725, 939, heating=True) == 876       # 939 - (53 + 6 + 4)
    assert gf.ramp_aim(800, 53, 725, 939, heating=True) == 800       # target already inside
    assert gf.ramp_aim(392, 45, 392, 606, heating=False) == 447      # 392 + (45 + 6 + 4)
    assert gf.ramp_stop_reached(877, 876, heating=True) is True
    assert gf.ramp_stop_reached(875, 876, heating=True) is False
    assert gf.ramp_stop_reached(440, 447, heating=False) is True
    assert gf.RAMP_MAX_RATE == 95   # MEASURED gf_live13: a stale first read gave "530/tick"


def test_adjust_heat_stops_before_the_ramp_overshoots(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    menu, clicks, hovers = [], [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    served = []
    monkeypatch.setattr(controller, "click_entity",
                        _pool_recorder(menu, clicks, click_value=lambda: (44619, served[-1])))
    monkeypatch.setattr(controller, "click_human",
                        lambda ser: clicks.append(("in-place", served[-1])))
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, ent, **k: hovers.append((ent["id"], served[-1])) or True)
    temps = [556, 556, 583, 613, 646, 683, 724, 769, 818, 871, 928, 990]
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    monkeypatch.setattr(gf.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))

    def read():
        t = temps[min(len(served), len(temps) - 1)]
        served.append(t)
        # one temperature step per 0.6s tick: two polls at HEAT_POLL_S see the same value
        return _st(temperature=t, temperatureStatus="TOO_COLD" if t < 725 else "READY",
                   minimumTemperature=725, maximumTemperature=939)
    monkeypatch.setattr(gf, "read_state", read)
    st = _st(temperature=556, temperatureStatus="TOO_COLD", completion=340,
             sectionCount=6, currentSection=3,
             minimumTemperature=725, maximumTemperature=939)
    gf._adjust_heat_and_engage(ctx, st, "TRIP_HAMMER")
    assert menu == [(gf.LAVA_POOL_ID, "Dunk-preform")]
    # USER 2026-09-03: hover only after the move -- the first ramp tick (556 -> 583) is
    # when the player provably stands at the pool; the stop is then a click in place.
    assert hovers == [(44619, 583)]
    # rate 57 at 928 -> aim 939 - 67 = 872: the first reading past it is the stop (in band)
    assert clicks == [("in-place", 928)]


def test_adjust_heat_waits_for_one_ramp_tick_before_projecting(monkeypatch):
    # MEASURED live 2026-09-03 (gf_live9): quenching from 980 toward the hammer's 939
    # target, the projection said "next tick lands at 937" on the FIRST poll and the
    # machine click cancelled the quench before it ran a single tick -- five times over,
    # into the dead-click stop. A projection needs an observed ramp tick behind it.
    ctx = _Ctx()
    _machines(monkeypatch)
    clicks, served, menu = [], [], []
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, ent, option, **k: True)
    monkeypatch.setattr(controller, "click_entity",
                        _pool_recorder(menu, clicks, click_value=lambda: (44619, served[-1])))
    monkeypatch.setattr(controller, "click_human",
                        lambda ser: clicks.append(("in-place", served[-1])))
    temps = [980, 980, 980, 953, 923, 890]
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    monkeypatch.setattr(gf.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))

    def read():
        t = temps[min(len(served), len(temps) - 1)]
        served.append(t)
        return _st(temperature=t, temperatureStatus="TOO_HOT" if t > 939 else "READY",
                   minimumTemperature=725, maximumTemperature=939)
    monkeypatch.setattr(gf, "read_state", read)
    st = _st(temperature=980, temperatureStatus="TOO_HOT", completion=0,
             sectionCount=6, currentSection=1,
             minimumTemperature=725, maximumTemperature=939)
    gf._adjust_heat_and_engage(ctx, st, "TRIP_HAMMER")
    assert clicks == [("in-place", 923)]          # first reading at or below the 939 aim


def test_edge_leave_due_asks_where_the_action_after_the_lead_fires():
    # MEASURED live 2026-09-03 (gf_live12): the wheel fell through its 59 floor and the
    # routine saw TOO_COLD two or three actions later, 5 quality each. And gf_live19: with
    # two wheel actions left at 94 both fit (94, then 76) -- measuring the temperature
    # AFTER the actions instead of where the next one FIRES left a whole action early.
    assert gf.action_fires_at(94, "POLISHING_WHEEL", 1) == 94
    assert gf.action_fires_at(94, "POLISHING_WHEEL", 2) == 76
    assert gf.action_fires_at(94, "POLISHING_WHEEL", 3) == 58
    assert gf.edge_leave_due(94, "POLISHING_WHEEL", 59, 273, lead=2) is True    # 3rd fires at 58
    assert gf.edge_leave_due(94, "POLISHING_WHEEL", 59, 273, lead=1) is False   # 2nd fires at 76
    assert gf.edge_leave_due(77, "POLISHING_WHEEL", 59, 273, lead=1) is False   # 2nd fires at 59
    assert gf.edge_leave_due(76, "POLISHING_WHEEL", 59, 273, lead=1) is True    # 2nd fires at 58
    assert gf.edge_leave_due(593, "GRINDSTONE", 392, 606, lead=1) is True       # 2nd fires at 607
    assert gf.edge_leave_due(592, "GRINDSTONE", 392, 606, lead=1) is False
    assert gf.edge_leave_lead("TRIP_HAMMER", False) == 1
    assert gf.edge_leave_lead("POLISHING_WHEEL", False) == 2
    assert gf.edge_leave_lead("POLISHING_WHEEL", True) == 1


def test_refine_does_not_leave_when_the_remaining_actions_fit(monkeypatch):
    # gf_live19: two actions left at 94 -> no leave, no self-cancelling dunk.
    ctx = _Ctx()
    _machines(monkeypatch)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu))
    st = _st(requiredTool="POLISHING_WHEEL", minimumTemperature=59, maximumTemperature=273,
             temperature=94, temperatureStatus="READY", activity="POLISHING",
             completion=480, sectionCount=6)          # section 3 ends at 500: 2 actions left
    gf.refine_phase(ctx, st)
    assert menu == []


def test_adjust_heat_skips_the_pool_when_already_at_target(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    menu, clicks = [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append(option) or True)
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu, clicks))
    st = _st(requiredTool="POLISHING_WHEEL", minimumTemperature=59, maximumTemperature=273,
             temperature=94, temperatureStatus="READY", completion=480, sectionCount=6)
    gf._adjust_heat_and_engage(ctx, st, "POLISHING_WHEEL", heating=True)
    assert menu == [] and clicks == [44621]


def test_refine_leaves_for_the_pool_before_the_band_edge(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu))
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    monkeypatch.setattr(gf.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    _serve_state(monkeypatch, _st(requiredTool="POLISHING_WHEEL", minimumTemperature=59,
                                  maximumTemperature=273, temperature=240,
                                  temperatureStatus="READY"))
    st = _st(requiredTool="POLISHING_WHEEL", minimumTemperature=59, maximumTemperature=273,
             temperature=94, temperatureStatus="READY", activity="POLISHING", completion=400)
    gf.refine_phase(ctx, st)
    assert menu == [(gf.LAVA_POOL_ID, "Dunk-preform")]


def test_pool_click_prefers_the_left_click_default_and_falls_back_to_the_menu(monkeypatch):
    # USER 2026-09-03: Dunk/Quench are the LEFT-click default on this client. In place when
    # the pool is hovered and /hover confirms it; a verified left click otherwise; the
    # right-click menu only when the hover check withholds.
    ctx = _Ctx()
    pool = {"id": gf.LAVA_POOL_ID, "x": 3372, "y": 11497, "plane": 0, "screenX": 650, "screenY": 450}
    calls = []
    monkeypatch.setattr(controller, "hover_matches", lambda **k: True)
    monkeypatch.setattr(controller, "click_human", lambda ser: calls.append("in-place"))
    assert gf._pool_click(ctx, pool, "Dunk-preform", in_place=True) is True
    assert calls == ["in-place"]

    calls.clear()
    monkeypatch.setattr(controller, "hover_matches", lambda **k: False)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: calls.append(("left", k.get("expected_action"))) or True)
    assert gf._pool_click(ctx, pool, "Dunk-preform", in_place=True) is True
    assert calls == [("left", "Dunk-preform")]

    calls.clear()
    withheld = controller.CLICK_WITHHELD
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: withheld)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: calls.append(("menu", option)) or True)
    assert gf._pool_click(ctx, pool, "Dunk-preform") is True
    assert calls == [("menu", "Dunk-preform")]


def test_refine_prehovers_the_pool_ahead_of_the_leave(monkeypatch):
    # USER 2026-09-03: "hover heat up / waterfall too since we just lost two quality for
    # being too slow". Two actions before the leave is due, rest the cursor on the pool.
    ctx = _Ctx()
    _machines(monkeypatch)
    hovers, clicks = [], []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, ent, **k: hovers.append(ent["id"]) or True)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(ent["id"]) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: clicks.append(option) or True)
    st = _st(requiredTool="POLISHING_WHEEL", minimumTemperature=59, maximumTemperature=273,
             temperature=130, temperatureStatus="READY", activity="POLISHING", completion=400)
    gf.refine_phase(ctx, st)             # 130 - 4*17 - 4 = 58 < 59: hover due, leave not yet
    assert hovers == [gf.LAVA_POOL_ID]
    assert clicks == []
    gf.refine_phase(ctx, st)             # already hovered -> no second flight
    assert hovers == [gf.LAVA_POOL_ID]


HEAT_ST = dict(sectionCount=6, toolSequence=["TRIP_HAMMER", "GRINDSTONE", "POLISHING_WHEEL",
                                              "GRINDSTONE", "POLISHING_WHEEL", "GRINDSTONE"])


def test_boundary_watch_prehovers_the_next_pool_and_clicks_away_on_tool_change(monkeypatch):
    # MEASURED live 2026-09-03 (gf_live7): every section boundary cost 5 quality -- the old
    # machine ticks once more with the WRONG tool before the routine's next poll+flight
    # reaches the pool. One action from the boundary: hover the pool the next tool needs
    # (hotter tool -> lava, colder -> waterfall), poll fast, click the instant the tool flips.
    ctx = _Ctx()
    _machines(monkeypatch)
    menu, clicks, hovers = [], [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu, clicks))
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, ent, **k: hovers.append(ent["id"]) or True)
    monkeypatch.setattr(controller, "click_human", lambda ser: clicks.append("in-place"))
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    monkeypatch.setattr(gf.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    # hammer (HIGH) section 1 ends at 166.7; at 160 one action remains; next tool GRINDSTONE (MED)
    hammer = _st(requiredTool="TRIP_HAMMER", completion=160, currentSection=1,
                 temperature=760, temperatureStatus="READY", activity="HAMMERING", **HEAT_ST)
    grind = _st(requiredTool="GRINDSTONE", completion=180, currentSection=2,
                minimumTemperature=392, maximumTemperature=606,
                temperature=735, temperatureStatus="TOO_HOT", activity="NONE", **HEAT_ST)
    reads = [hammer, hammer, grind] + [dict(grind, temperature=t, temperatureStatus="READY" if t <= 606 else "TOO_HOT")
                                       for t in (700, 660, 610, 560, 500, 440)]
    monkeypatch.setattr(gf, "read_state", lambda: reads.pop(0) if len(reads) > 1 else reads[0])
    gf.refine_phase(ctx, hammer)          # working, mark set
    now[0] += 1.0
    gf.refine_phase(ctx, hammer)          # one action left -> the watch arms and rides the flip
    assert hovers[0] == gf.WATERFALL_ID
    assert menu == [(gf.WATERFALL_ID, "Quench-preform")]
    assert clicks == ["in-place"]         # grindstone (hovered after arrival) engaged in place


def test_sweet_spot_claim_near_the_boundary_arms_the_watch(monkeypatch):
    # gf_live23: the claim's ~5% bonus crossed the section end on the next machine tick.
    ctx = _Ctx()
    _machines(monkeypatch)
    hovers, menu = [], []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, ent, **k: hovers.append(ent["id"]) or True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu))
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    monkeypatch.setattr(gf.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    # grindstone section 2 ends at 333; at 300 the bonus (50) crosses it; next tool POLISHING
    grind = _st(requiredTool="GRINDSTONE", completion=300, currentSection=2,
                minimumTemperature=392, maximumTemperature=606, temperature=500,
                temperatureStatus="READY", activity="GRINDING", sweetSpotState="AVAILABLE", **HEAT_ST)
    polish = _st(requiredTool="POLISHING_WHEEL", completion=350, currentSection=3,
                 minimumTemperature=59, maximumTemperature=273, temperature=500,
                 temperatureStatus="TOO_HOT", activity="NONE", **HEAT_ST)
    reads = [grind, polish] + [dict(polish, temperature=t, temperatureStatus="READY" if t <= 273 else "TOO_HOT")
                               for t in (450, 400, 340, 280, 230)]
    monkeypatch.setattr(gf, "read_state", lambda: reads.pop(0) if len(reads) > 1 else reads[0])
    gf.refine_phase(ctx, grind)
    assert hovers[0] == gf.WATERFALL_ID              # pool hovered right after the claim
    assert (gf.WATERFALL_ID, "Quench-preform") in menu


def test_boundary_watch_rides_through_a_transitional_none_read(monkeypatch):
    # gf_live25: a requiredTool=NONE read at the boundary made the watch return silently
    # and the wheel ticked once with the wrong tool (5 quality).
    ctx = _Ctx()
    _machines(monkeypatch)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu))
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    monkeypatch.setattr(gf.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    polish = _st(requiredTool="POLISHING_WHEEL", completion=490, currentSection=3,
                 minimumTemperature=59, maximumTemperature=273, temperature=150,
                 temperatureStatus="READY", activity="POLISHING", **HEAT_ST)
    none_read = dict(polish, requiredTool="NONE", temperatureStatus="UNKNOWN")
    grind = _st(requiredTool="GRINDSTONE", completion=500, currentSection=4,
                minimumTemperature=392, maximumTemperature=606, temperature=140,
                temperatureStatus="TOO_COLD", activity="NONE", **HEAT_ST)
    reads = [polish, none_read, none_read, grind] +         [dict(grind, temperature=t, temperatureStatus="READY" if t >= 392 else "TOO_COLD")
         for t in (170, 200, 240, 290, 350, 420)]
    monkeypatch.setattr(gf, "read_state", lambda: reads.pop(0) if len(reads) > 1 else reads[0])
    gf.refine_phase(ctx, polish)          # one action left -> watch arms
    assert (gf.LAVA_POOL_ID, "Dunk-preform") in menu


def test_sweet_spot_is_not_claimed_at_the_band_edge(monkeypatch):
    # gf_live26: the claim took the tick the edge-leave was due; next read was 41, TOO_COLD.
    ctx = _Ctx()
    _machines(monkeypatch)
    menu, clicks = [], []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, ent, option, **k: menu.append((ent["id"], option)) or True)
    monkeypatch.setattr(controller, "click_entity", _pool_recorder(menu, clicks))
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    monkeypatch.setattr(gf.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    _serve_state(monkeypatch, _st(requiredTool="POLISHING_WHEEL", minimumTemperature=59,
                                  maximumTemperature=273, temperature=200,
                                  temperatureStatus="READY"))
    st = _st(requiredTool="POLISHING_WHEEL", minimumTemperature=59, maximumTemperature=273,
             temperature=70, temperatureStatus="READY", activity="POLISHING",
             sweetSpotState="AVAILABLE", completion=400)
    gf.refine_phase(ctx, st)
    assert not ctx.state.data.get("gf_sweet_claimed")    # no claim click on the wheel
    assert menu == [(gf.LAVA_POOL_ID, "Dunk-preform")]   # the leave happened instead


def test_boundary_watch_skips_the_last_section(monkeypatch):
    ctx = _Ctx()
    _machines(monkeypatch)
    hovers = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, ent, **k: hovers.append(ent["id"]) or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, ent, **k: True)
    now = [1000.0]
    monkeypatch.setattr(gf.time, "time", lambda: now[0])
    last = _st(requiredTool="GRINDSTONE", completion=995, currentSection=6,
               minimumTemperature=392, maximumTemperature=606, temperature=500,
               temperatureStatus="READY", activity="GRINDING", **HEAT_ST)
    gf.refine_phase(ctx, last)
    now[0] += 1.0
    gf.refine_phase(ctx, last)
    assert hovers == []


# ---------------------------------------------------------------------------
# Task 10: setup + before_break + wiring
# ---------------------------------------------------------------------------

def test_setup_does_not_unequip_a_held_preform(monkeypatch):
    # MEASURED live 2026-09-03 (gf_live3): restarted mid-refine with the preform in hand,
    # /equipment reported weapon "Preform", and setup's free-hands recovery right-clicked
    # it and chose "Remove Preform". The preform IS the thing the hands are for.
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"gloves": {"name": "Ice gloves"}, "weapon": {"name": "Preform"},
                                 "shield": {}})
    freed = []
    monkeypatch.setattr(gf, "_free_hands", lambda ctx_: freed.append(True))
    gf.setup(ctx)
    assert freed == []
    assert ctx.state.stopped is None


def test_setup_requires_gloves(monkeypatch):
    # /equipment is a slot-keyed dict (EquipmentProvider) -- no "items" key. A non-empty
    # dict with the gloves slot present but not ice/smithing gloves means the read
    # succeeded and gloves genuinely aren't equipped.
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"body": {"name": "Rune platebody"}, "gloves": {}})
    gf.setup(ctx)
    assert "gloves" in ctx.state.stopped.lower()


def test_setup_accepts_ice_gloves(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"gloves": {"name": "Ice gloves"}})
    gf.setup(ctx)
    assert ctx.state.stopped is None


def test_setup_accepts_smiths_gloves_i(monkeypatch):
    # The real item name (cache obj 27031); an earlier "smithing gloves (i)" never matched.
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"gloves": {"name": "Smiths gloves (i)"}})
    gf.setup(ctx)
    assert ctx.state.stopped is None


def test_setup_cannot_read_equipment_does_not_stop(monkeypatch):
    # {} is get_equipment's bridge-down value (empty ItemContainer) -- "cannot tell"
    # must not stop the run.
    ctx = _Ctx()
    gf.setup(ctx)
    assert ctx.state.stopped is None


def test_setup_frees_hands_if_weapon_or_shield_equipped(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"gloves": {"name": "Ice gloves"},
                                 "weapon": {"name": "Imcando hammer"}, "shield": {}})
    calls = []
    monkeypatch.setattr(gf, "_free_hands", lambda ctx_: calls.append(1))
    gf.setup(ctx)
    assert ctx.state.stopped is None
    assert calls == [1]


def test_setup_does_not_free_hands_when_empty(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"gloves": {"name": "Ice gloves"}, "weapon": {}, "shield": {}})
    calls = []
    monkeypatch.setattr(gf, "_free_hands", lambda ctx_: calls.append(1))
    gf.setup(ctx)
    assert calls == []


def test_before_break_stores_equipped_preform(monkeypatch):
    ctx = _Ctx()
    storage = {"id": gf.STORAGE_EMPTY_ID, "x": 3366, "y": 11485, "plane": 0,
               "screenX": 520, "screenY": 380, "name": "Preform storage"}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [storage])
    _serve_state(monkeypatch, _st(preformState="EQUIPPED"))
    kwargs = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: kwargs.update(k) or True)
    gf.before_break(ctx)
    assert kwargs.get("expected_action") == "Store-preform"


def test_before_break_no_preform_no_click(monkeypatch):
    ctx = _Ctx()
    _serve_state(monkeypatch, _st(preformState="NONE", phase="AWAITING_COMMISSION"))
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, ent, **k: clicks.append(1) or True)
    gf.before_break(ctx)
    assert clicks == []


def test_routine_registered_in_controller():
    assert controller.ROUTINE_FILES.get("GIANTS_FOUNDRY") == "routines/giants_foundry.py"
