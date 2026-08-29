import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import time

import pytest

import raid_scouter as rs
import controller


def _object(obj_id, x=0, y=0, plane=0):
    return {"id": obj_id, "x": x, "y": y, "plane": plane, "screenX": 100, "screenY": 100}


class _State:
    def __init__(self):
        self.data = {}
        self.config = rs.DEFAULT_CONFIG
        self.safety_stopped = False
        self.safety_reason = None

    def trigger_safety_stop(self, reason):
        if self.safety_stopped:
            return
        self.safety_stopped = True
        self.safety_reason = reason


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = _State()


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Keep the suite hermetic -- get_raid() reaches the RUNNING CLIENT over HTTP when a test
    does not stub it, so a phase test's result would depend on whatever raid instance the real
    character happened to be standing in (the same class of bug travel.py's suite hit, and
    mahogany_homes' own conftest note explains in full). Individual tests override what they
    need to exercise; everything else defaults to the honest "nothing here" answer."""
    monkeypatch.setattr(controller, "get_raid", lambda: None)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False: [])
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_players", lambda: [])
    monkeypatch.setattr(controller, "get_widget", lambda group, child, index=None:
                         {"found": False})
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda *a, **kw: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **kw: True)
    monkeypatch.setattr(controller, "key_tap", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "object_refresh_target",
                         lambda *a, **kw: (lambda: None))


# --- evaluate() -- the exact target rule, corrected 2026-08-29 to the 5-room minimum layout ---

def test_exact_match_vasa_first_rest_in_any_order():
    assert rs.evaluate(["Vasa", "Crabs", "Shamans", "Tightrope", "Vespula"]) == "match"
    assert rs.evaluate(["Vasa", "Vespula", "Tightrope", "Shamans", "Crabs"]) == "match"


def test_extra_room_rejects_even_with_the_right_prefix():
    assert rs.evaluate(
        ["Vasa", "Shamans", "Vespula", "Crabs", "Tightrope", "Tekton"]) == "reject"


def test_vasa_must_be_first_not_merely_present():
    assert rs.evaluate(["Crabs", "Vasa", "Shamans", "Vespula", "Tightrope"]) == "reject"


def test_missing_room_rejects():
    assert rs.evaluate(["Vasa", "Shamans", "Vespula", "Tightrope"]) == "reject"


def test_wrong_room_in_the_rest_rejects():
    assert rs.evaluate(["Vasa", "Shamans", "Muttadiles", "Crabs", "Tightrope"]) == "reject"


def test_empty_list_abstains_not_rejects():
    assert rs.evaluate([]) == "abstain"


def test_unknown_room_abstains_not_rejects():
    # A layout containing an UNKNOWN_COMBAT/UNKNOWN_PUZZLE room has not been fully scouted yet
    # (RaidsProvider.java's countUnknown) -- judging it would throw away a good instance.
    assert rs.evaluate(["Vasa", "Unknown (puzzle)", "Shamans", "Vespula", "Crabs"]) == "abstain"


def test_four_room_list_is_a_provider_bug_not_a_rejectable_layout():
    # Five rooms (3 combat + 2 puzzle) is the smallest layout the game can generate -- a
    # four-room list should never occur live. It still resolves via the ordinary length check
    # (reject), which is the safe behaviour either way: never treated as a match.
    assert rs.evaluate(["Vasa", "Shamans", "Vespula"]) == "reject"


# --- tick() phase derivation on a fresh/restarted process ---------------------------------

def test_fresh_process_with_no_contract_state_starts_at_setup(monkeypatch):
    monkeypatch.setattr(controller, "get_raid", lambda: {
        "raidsPluginEnabled": True, "inRaid": False, "scouted": False, "rooms": [],
    })
    ctx = _Ctx()
    rs.tick(ctx)
    assert ctx.state.data.get("rs_phase") == "setup"


def test_fresh_process_mid_match_resumes_hold_not_setup(monkeypatch):
    # The mahogany_homes lesson: ctx.state.data is wiped on restart while the game state is
    # not, so a restart while sitting on an already-matched, already-scouted instance must not
    # walk away from it and start rerolling a good layout.
    monkeypatch.setattr(controller, "get_raid", lambda: {
        "raidsPluginEnabled": True, "inRaid": True, "scouted": True, "unknownRooms": 0,
        "rooms": ["Vasa", "Crabs", "Shamans", "Tightrope", "Vespula"],
        "scoutId": 5, "raidPartyId": 123, "world": 420,
    })
    ctx = _Ctx()
    rs.tick(ctx)
    assert ctx.state.data.get("rs_phase") == "hold"


def test_fresh_process_in_raid_but_not_yet_scouted_goes_to_scout(monkeypatch):
    monkeypatch.setattr(controller, "get_raid", lambda: {
        "raidsPluginEnabled": True, "inRaid": True, "scouted": False, "rooms": [],
    })
    ctx = _Ctx()
    rs.tick(ctx)
    assert ctx.state.data.get("rs_phase") == "scout"


def test_raids_plugin_disabled_safety_stops_with_an_actionable_message(monkeypatch):
    monkeypatch.setattr(controller, "get_raid", lambda: {
        "raidsPluginEnabled": False, "inRaid": False, "scouted": False, "rooms": [],
    })
    ctx = _Ctx()
    rs.tick(ctx)
    assert ctx.state.safety_stopped is True
    assert "raids plugin" in ctx.state.safety_reason.lower()


# --- reroll_phase: the second-menu-option / stale-scout / dip-tolerance lessons -----------

def test_reroll_treats_a_stale_scout_id_as_still_the_same_instance():
    ctx = _Ctx()
    ctx.state.data["rs_reroll_from_party"] = 100
    ctx.state.data["rs_last_scout_id"] = 5
    state = {"inRaid": True, "scoutId": 5, "raidPartyId": 200}
    rs.reroll_phase(ctx, rs.DEFAULT_CONFIG, state)
    # party id changed but scoutId did NOT advance past what we already judged -- must not
    # treat this as a fresh, judgeable instance yet.
    assert ctx.state.data.get("rs_phase") != "scout"


def test_reroll_advances_to_scout_once_party_changes_and_a_fresh_scout_lands():
    ctx = _Ctx()
    ctx.state.data["rs_reroll_from_party"] = 100
    ctx.state.data["rs_last_scout_id"] = 5
    state = {"inRaid": True, "scoutId": 6, "raidPartyId": 200}
    rs.reroll_phase(ctx, rs.DEFAULT_CONFIG, state)
    assert ctx.state.data.get("rs_phase") == "scout"


def test_reroll_max_attempts_safety_stops(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False:
                         [_object(rs.STEPS_ID)])
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **kw: True)
    ctx = _Ctx()
    ctx.state.data["rs_reroll_from_party"] = 100
    state = {"inRaid": True, "scoutId": 0, "raidPartyId": 100}
    for _ in range(rs.RELOAD_MAX_ATTEMPTS):
        ctx.state.data["rs_reload_click_at"] = 0.0  # bypass cooldown between synthetic ticks
        rs.reroll_phase(ctx, rs.DEFAULT_CONFIG, state)
    assert ctx.state.safety_stopped is True


# --- scout_phase: abstain-on-unknown must not consume the scoutId --------------------------

def test_scout_phase_abstains_on_unknown_and_does_not_mark_it_judged():
    ctx = _Ctx()
    state = {"inRaid": True, "scoutId": 3, "rooms": ["Vasa", "Unknown (combat)"]}
    rs.scout_phase(ctx, rs.DEFAULT_CONFIG, state)
    assert ctx.state.data.get("rs_last_scout_id", -1) < 3
    assert ctx.state.data.get("rs_phase") != "hold"
    assert ctx.state.data.get("rs_phase") != "reroll"


def test_scout_phase_rejects_and_moves_to_reroll():
    ctx = _Ctx()
    state = {"inRaid": True, "scoutId": 3, "rooms": ["Tekton", "Vasa"], "raidPartyId": 55}
    rs.scout_phase(ctx, rs.DEFAULT_CONFIG, state)
    assert ctx.state.data.get("rs_phase") == "reroll"
    assert ctx.state.data.get("rs_last_scout_id") == 3


def test_scout_phase_watchdog_forces_reroll_when_stuck_abstaining_forever():
    # A raid that never resolves (rooms stays [] / carries an UNKNOWN forever) abstains every
    # tick and would otherwise never advance -- BUG caught in review: the timeout used to be
    # gated behind "already judged this scoutId", which abstain never reaches. The watchdog
    # must fire purely off scoutId being STUCK, regardless of verdict.
    ctx = _Ctx()
    ctx.state.data["rs_scout_id_seen"] = 4
    ctx.state.data["rs_scout_seen_at"] = time.time() - (rs.SCOUT_TIMEOUT_S + 1)
    state = {"inRaid": True, "scoutId": 4, "rooms": [], "raidPartyId": 9}
    rs.scout_phase(ctx, rs.DEFAULT_CONFIG, state)
    assert ctx.state.data.get("rs_phase") == "reroll"


def test_scout_phase_watchdog_resets_when_scout_id_changes():
    ctx = _Ctx()
    ctx.state.data["rs_scout_id_seen"] = 3
    ctx.state.data["rs_scout_seen_at"] = time.time() - (rs.SCOUT_TIMEOUT_S + 1)
    state = {"inRaid": True, "scoutId": 4, "rooms": [], "raidPartyId": 9}
    rs.scout_phase(ctx, rs.DEFAULT_CONFIG, state)
    # scoutId moved 3 -> 4 this tick, so the watchdog must not fire on stale elapsed time from
    # the PREVIOUS scoutId's clock.
    assert ctx.state.data.get("rs_phase") != "reroll"
    assert ctx.state.data.get("rs_scout_id_seen") == 4


def test_scout_phase_matches_and_moves_to_hold():
    ctx = _Ctx()
    state = {"inRaid": True, "scoutId": 3,
             "rooms": ["Vasa", "Crabs", "Shamans", "Tightrope", "Vespula"],
             "raidPartyId": 55, "world": 420}
    rs.scout_phase(ctx, rs.DEFAULT_CONFIG, state)
    assert ctx.state.data.get("rs_phase") == "hold"


# --- hold_phase: handoff on the main's presence, ceiling, and the webhook never blocking ---

def test_hold_departs_once_the_configured_main_is_in_players(monkeypatch):
    monkeypatch.setattr(controller, "get_players", lambda: [{"name": "MyMain"}])
    config = dict(rs.DEFAULT_CONFIG)
    config["mainName"] = "MyMain"
    ctx = _Ctx()
    ctx.state.data["rs_hold_started_at"] = time.time()
    state = {"inRaid": True, "scoutId": 1, "rooms": [], "raidPartyId": 1, "world": 1}
    rs.hold_phase(ctx, config, state)
    assert ctx.state.data.get("rs_phase") == "depart"


def test_hold_does_not_depart_for_an_unrelated_player(monkeypatch):
    monkeypatch.setattr(controller, "get_players", lambda: [{"name": "SomeoneElse"}])
    config = dict(rs.DEFAULT_CONFIG)
    config["mainName"] = "MyMain"
    ctx = _Ctx()
    ctx.state.data["rs_hold_started_at"] = time.time()
    state = {"inRaid": True, "scoutId": 1, "rooms": [], "raidPartyId": 1, "world": 1}
    rs.hold_phase(ctx, config, state)
    assert ctx.state.data.get("rs_phase") != "depart"


def test_hold_timeout_rerolls_rather_than_holding_forever(monkeypatch):
    monkeypatch.setattr(controller, "get_players", lambda: [])
    ctx = _Ctx()
    ctx.state.data["rs_hold_started_at"] = time.time() - (rs.HOLD_TIMEOUT_S + 1)
    state = {"inRaid": True, "scoutId": 1, "rooms": [], "raidPartyId": 1, "world": 1}
    rs.hold_phase(ctx, rs.DEFAULT_CONFIG, state)
    assert ctx.state.data.get("rs_phase") == "reroll"


def test_webhook_failure_never_raises_or_blocks(monkeypatch):
    class _Boom:
        def post(self, *a, **kw):
            raise RuntimeError("network is down")

    monkeypatch.setitem(sys.modules, "requests", _Boom())
    config = dict(rs.DEFAULT_CONFIG)
    config["webhookUrl"] = "https://example.invalid/webhook"
    # Must not raise.
    rs._send_webhook(config, "test message")


def test_webhook_url_falls_back_to_env_var(monkeypatch):
    monkeypatch.delenv("RAID_SCOUT_WEBHOOK", raising=False)
    monkeypatch.setenv("RAID_SCOUT_WEBHOOK", "https://example.invalid/env-hook")
    config = {"webhookUrl": None}
    assert rs._webhook_url(config) == "https://example.invalid/env-hook"


def test_webhook_url_none_when_nothing_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("RAID_SCOUT_WEBHOOK", raising=False)
    monkeypatch.setattr(os.path, "abspath", lambda p: str(tmp_path / "routines" / "x.py"))
    assert rs._webhook_url({"webhookUrl": None}) is None


# --- depart_phase: waits for the real exit, not the reload's transient dip -----------------

def test_depart_moves_to_hop_once_inraid_goes_false():
    ctx = _Ctx()
    state = {"inRaid": False}
    rs.depart_phase(ctx, rs.DEFAULT_CONFIG, state)
    assert ctx.state.data.get("rs_phase") == "hop"


def test_depart_max_clicks_safety_stops(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False:
                         [_object(rs.STEPS_ID)])
    ctx = _Ctx()
    state = {"inRaid": True}
    for _ in range(rs.DEPART_MAX_CLICKS):
        ctx.state.data["rs_depart_click_at"] = 0.0
        rs.depart_phase(ctx, rs.DEFAULT_CONFIG, state)
    assert ctx.state.safety_stopped is True


# --- hop_phase: honest about being unimplemented, never a silent no-op ---------------------

def test_hop_phase_safety_stops_with_an_actionable_message():
    ctx = _Ctx()
    rs.hop_phase(ctx, rs.DEFAULT_CONFIG, {})
    assert ctx.state.safety_stopped is True
    assert "world hopping" in ctx.state.safety_reason.lower()
