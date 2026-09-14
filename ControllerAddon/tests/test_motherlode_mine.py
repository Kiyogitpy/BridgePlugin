"""routines/motherlode_mine.py.

Hermetic: every bridge read is stubbed and the module clock is faked, so a budget that would
wait seconds live expires instantly here (the test_blast_furnace fixture pattern).

The facts pinned below are the MEASURED ones from the design spec
(docs/superpowers/specs/2026-09-07-motherlode-mine-design.md): a depleted vein offers no
actions; the mine's upper level sits directly above the lower one so TILE HEIGHT is the only
floor test that works; the conveyor lags so the sack has to be read as varbit + pending; and a
broken machine is left alone until it costs us.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import controller
import motherlode_mine as mlm
import travel


class _State:
    def __init__(self, config=None):
        self.config = dict(config or mlm.DEFAULT_CONFIG)
        self.data = {}
        self.safety_stopped = False
        self.stop_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.stop_reason = reason


class _Ctx:
    def __init__(self, config=None):
        self.ser = object()
        self.state = _State(config)
        self.events = None


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(mlm, "_now", clock.now)
    monkeypatch.setattr(mlm, "_sleep", clock.sleep)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [])
    # Nothing worn by default. Without this the hammer check reaches the RUNNING CLIENT, so
    # test_a_missing_hammer_only_turns_repairs_off passed or failed depending on what the
    # account happened to have equipped -- it went red the moment a real Imcando hammer was
    # worn. Same hermeticity rule test_banker.py's _no_live_bridge already applies.
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    # Upstairs by default: varbit 2086 is the PLAYER's floor (exact, no rebuild needed) and
    # tileHeight is the OBJECT's, so the two have to agree in a fixture as they do live.
    monkeypatch.setattr(controller, "get_varbit",
                        lambda vid: 1 if vid == mlm.VARBIT_UPSTAIRS else 0)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3752, "y": 5680, "plane": 0,
                                                           "regionId": mlm.REGION,
                                                           "weaponName": "Rune pickaxe",
                                                           "tileHeight": -600})
    monkeypatch.setattr(controller, "get_collision", lambda x, y, p, radius=6, **kw: [])
    # No interface open unless a test says so -- otherwise _deposit_open reaches the live bridge.
    monkeypatch.setattr(controller, "get_widget", lambda group, child, index=None: {})
    monkeypatch.setattr(controller, "get_widget_children", lambda group, child: [])
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "sample_human_delay", lambda *a, **k: 0.0)
    return clock


def _vein(x, y, obj_id=26662, name=mlm.VEIN_NAME, dist=3, actions=("Mine",), **over):
    d = {"id": obj_id, "name": name, "x": x, "y": y, "plane": 0, "distance": dist,
         "visible": True, "screenX": 100, "screenY": 100}
    if actions is not None:
        d["actions"] = list(actions)
    d.update(over)
    return d


def _items(*names):
    return [{"name": n} for n in names]


def _paydirt(n):
    return _items(*(["Pay-dirt"] * n))


# --- the sack ---------------------------------------------------------------------

def test_sack_capacity_follows_the_upgrade_varbit():
    assert mlm.sack_capacity(0) == 108
    assert mlm.sack_capacity(1) == 189


def test_sack_effective_counts_paydirt_still_on_the_conveyor():
    # The whole reason mlm-qol has a SackTracker: 5558 lags a deposit by seconds, and reading it
    # raw would start a trip whose pay-dirt has nowhere to go.
    assert mlm.sack_effective(81, 27) == 108


def test_pending_is_retired_as_the_conveyor_delivers(monkeypatch):
    ctx = _Ctx()
    ctx.state.data.update(mlm_pending=27, mlm_pending_at=mlm._now())
    monkeypatch.setattr(controller, "get_varbit",
                        lambda vid: 54 if vid == mlm.VARBIT_SACK else 0)
    mlm.sack_state(ctx)                       # first read only seeds the baseline
    monkeypatch.setattr(controller, "get_varbit",
                        lambda vid: 81 if vid == mlm.VARBIT_SACK else 0)
    _sack, effective, _capacity = mlm.sack_state(ctx)
    assert ctx.state.data["mlm_pending"] == 0
    assert effective == 81


def test_a_rising_sack_clears_the_broken_flag(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["mlm_broken_since"] = mlm._now()
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 27 if vid == mlm.VARBIT_SACK else 0)
    mlm.sack_state(ctx)
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 54 if vid == mlm.VARBIT_SACK else 0)
    mlm.sack_state(ctx)
    assert "mlm_broken_since" not in ctx.state.data


def test_pending_expires_so_a_lost_deposit_cannot_wedge_the_run(_hermetic):
    ctx = _Ctx()
    ctx.state.data.update(mlm_pending=27, mlm_pending_at=mlm._now())
    _hermetic.t += mlm.PENDING_MAX_S + 1
    assert mlm._pending(ctx) == 0


# --- how much to mine -------------------------------------------------------------

def test_a_full_load_is_mined_when_the_sack_has_room():
    # USER: "full inventory, always". 27 free slots, an empty sack -> mine all 27.
    assert mlm.mining_allowance(27, 0, 0, 108) == 27


def test_mining_stops_short_rather_than_overflowing_the_sack():
    # The recording's sack sat at 39: 39 + 27 + 27 + 27 = 120 > 108, and the excess pay-dirt is
    # simply lost. Two loads in, the third is capped.
    assert mlm.mining_allowance(27, 0, 93, 108) == 15


def test_allowance_counts_what_we_are_already_holding():
    assert mlm.mining_allowance(17, 10, 90, 108) == 8


def test_allowance_is_never_negative():
    assert mlm.mining_allowance(27, 0, 200, 108) == 0


# --- the phase machine ------------------------------------------------------------

def _phase(**over):
    args = dict(holding_ore=False, inventory_full=False, held_paydirt=0, allowance=27,
                sack_eff=0, capacity=108, collecting=False)
    args.update(over)
    return mlm.derive_phase(**args)


def test_holding_ore_always_banks_first():
    assert _phase(holding_ore=True, collecting=True, sack_eff=50) == "bank"


def test_a_full_inventory_of_paydirt_goes_to_the_hopper():
    assert _phase(inventory_full=True, held_paydirt=27, allowance=0) == "hopper"


def test_a_capped_load_goes_to_the_hopper_before_the_inventory_is_full():
    assert _phase(held_paydirt=15, allowance=0, sack_eff=93) == "hopper"


def test_a_full_sack_starts_the_collect_cycle():
    assert _phase(sack_eff=108, capacity=108) == "collect"


def test_the_collect_latch_survives_the_first_search():
    # Without the latch the first Search drops the sack below full and the next tick would climb
    # straight back up holding ore.
    assert _phase(collecting=True, sack_eff=54) == "collect"


def test_the_collect_latch_ends_with_an_empty_sack():
    assert _phase(collecting=True, sack_eff=0) == "mine"


def test_otherwise_we_mine():
    assert _phase(held_paydirt=5) == "mine"


# --- repairs -----------------------------------------------------------------------

def test_a_broken_machine_is_left_alone_at_first():
    # USER: "otherwise leave it broken. if we are lucky another player fixes it".
    assert mlm.repair_due(broken_since=100.0, hammer=True, patience_s=180, now=200.0) is False


def test_a_broken_machine_is_repaired_once_it_has_cost_us():
    assert mlm.repair_due(broken_since=100.0, hammer=True, patience_s=180, now=300.0) is True


def test_no_hammer_means_no_repair_ever():
    assert mlm.repair_due(broken_since=100.0, hammer=False, patience_s=180, now=9999.0) is False


def test_the_broken_chat_starts_the_clock_once(_hermetic):
    ctx = _Ctx()
    mlm.on_event(ctx, "CHAT_MESSAGE",
                 {"message": "The machine will need to be repaired before your pay- dirt can "
                             "be cleaned."})
    first = ctx.state.data["mlm_broken_since"]
    _hermetic.t += 60
    mlm.on_event(ctx, "CHAT_MESSAGE", {"message": "The machine will need to be repaired."})
    assert ctx.state.data["mlm_broken_since"] == first


# --- picking a vein ----------------------------------------------------------------

def _reach(*tiles):
    return {t: 0 for t in tiles}


def test_a_depleted_vein_is_skipped_because_it_offers_no_actions(monkeypatch):
    # MEASURED live: a depleted vein is a different id (26665-26668) with actions == [].
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    monkeypatch.setattr(travel, "can_reach", lambda *a, **k: True)
    objs = [_vein(3751, 5680, obj_id=26666, name="Depleted vein", dist=1, actions=[]),
            _vein(3751, 5680, obj_id=26666, dist=1, actions=[]),
            _vein(3753, 5680, dist=2)]
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None, objects=objs)["x"] == 3753


def test_a_reachable_vein_is_preferred_over_a_walled_one(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    _standing(monkeypatch, UPSTAIRS_H)
    walled = _vein(3752, 5684, dist=2, tileHeight=UPSTAIRS_H)
    open_one = _vein(3753, 5682, dist=4, tileHeight=UPSTAIRS_H)
    monkeypatch.setattr(travel, "can_reach",
                        lambda goal, reach=None, flags=None, **k: goal is not walled)
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None,
                         objects=[walled, open_one]) is open_one


def test_a_walled_vein_is_still_offered_when_it_is_all_there_is(monkeypatch):
    """Reachability is a PREFERENCE, not a veto.

    The mine's collision grid describes two stacked floors on one set of coordinates, so it is
    ambiguous by construction -- live it both passed the downstairs vein that cost 90 seconds
    and rejected upstairs veins two tiles away. A vein it doubts is still worth one try;
    note_vein_failure is what settles the question empirically.
    """
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(travel, "can_reach", lambda *a, **k: False)
    only = _vein(3753, 5682, dist=4, tileHeight=UPSTAIRS_H)
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None, objects=[only]) is only


def test_a_vein_outside_the_anchor_pocket_is_never_offered(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    monkeypatch.setattr(travel, "can_reach", lambda *a, **k: True)
    far = _vein(3766, 5680, dist=1)
    near = _vein(3753, 5684, dist=6)
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None, objects=[far, near]) is near


def test_an_unanswerable_flood_does_not_stop_the_routine_working(monkeypatch):
    # Empty reach means "cannot tell", never "nothing is reachable".
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    monkeypatch.setattr(travel, "can_reach", lambda *a, **k: False)
    vein = _vein(3753, 5680, dist=2)
    assert mlm.pick_vein(ctx, {}, None, objects=[vein]) is vein


def test_worked_tiles_are_excluded_so_the_chain_does_not_reclick_a_spent_vein(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    monkeypatch.setattr(travel, "can_reach", lambda *a, **k: True)
    spent = _vein(3751, 5681, dist=1)
    other = _vein(3753, 5680, dist=2)
    picked = mlm.pick_vein(ctx, _reach((3752, 5680)), None,
                           exclude=[(3751, 5681, 0)], objects=[spent, other])
    assert picked is other


# --- what counts as ore -------------------------------------------------------------

def test_kit_and_paydirt_are_not_ore():
    items = _items("Rune pickaxe", "Hammer", "Open gem sack", "Pay-dirt", "Coal", "Gold ore")
    assert [i["name"] for i in mlm._ore_items(items)] == ["Coal", "Gold ore"]


# --- the hopper ----------------------------------------------------------------------

UPSTAIRS_H = -600      # any height below UPPER_FLOOR_HEIGHT (-490)
DOWNSTAIRS_H = 0

UPPER_HOPPER = {"id": mlm.HOPPER_ID, "name": "Hopper", "x": 3755, "y": 5677, "plane": 0,
                "distance": 3, "actions": ["Deposit"], "screenX": 1, "screenY": 1,
                "tileHeight": UPSTAIRS_H}
LOWER_HOPPER = {"id": mlm.HOPPER_ID, "name": "Hopper", "x": 3748, "y": 5672, "plane": 0,
                "distance": 2, "actions": ["Deposit"], "screenX": 1, "screenY": 1,
                "tileHeight": DOWNSTAIRS_H}


def _standing(monkeypatch, height, **player):
    """Put the player on a floor: the height /player reports AND the 2086 varbit that says the
    same thing, because the routine reads its own floor from the varbit and objects' from the
    height."""
    upper = 1 if height < mlm.UPPER_FLOOR_HEIGHT else 0
    monkeypatch.setattr(controller, "get_varbit",
                        lambda vid: upper if vid == mlm.VARBIT_UPSTAIRS else 0)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3752, "y": 5680, "plane": 0, "regionId": mlm.REGION,
                                 "weaponName": "Rune pickaxe", "tileHeight": height, **player})


def test_the_upper_hopper_is_used_now_that_it_is_bought(monkeypatch):
    # USER 2026-09-14: "we can now use the hopper at the top of the mine so we dont have to go
    # down the stairs to deposit. only to retrieve from sack." The shipped default is "auto", so
    # a deposit made upstairs stays upstairs and the ladder is only the sack trip's.
    ctx = _Ctx()
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [UPPER_HOPPER])
    assert mlm._hopper_candidates(ctx) == [UPPER_HOPPER]


def test_upper_hopper_never_still_takes_the_ladder(monkeypatch):
    # The opt-out still works, for an account that has not bought it.
    ctx = _Ctx({**mlm.DEFAULT_CONFIG, "upperHopper": "never"})
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [UPPER_HOPPER])
    assert mlm._hopper_candidates(ctx) == []


def test_a_blocked_machine_upstairs_climbs_down_and_does_not_latch_the_upper_hopper(monkeypatch,
                                                                                    _hermetic):
    """The hopper refuses both when it is the locked one AND when the machine is stalled, and
    those must not be confused. Latching on a stalled machine would send every deposit back down
    the ladder for the rest of the session over a broken strut -- expensive now that the upper
    hopper is the normal one. The struts are downstairs, so the answer is to climb."""
    ctx = _Ctx()
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(controller, "get_inventory", lambda: _paydirt(27))
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [UPPER_HOPPER])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_widget",
                        lambda g, c, **kw: {"text": "You've already got some pay-dirt in the "
                                                    "machine."} if g == mlm.MACHINE_BUSY_GROUP
                        else {})
    climbed = []
    monkeypatch.setattr(mlm, "climb", lambda c, up: climbed.append(up) or True)

    assert mlm.hopper_phase(ctx) is False
    assert climbed == [False], "a stalled machine seen from upstairs must climb down to the struts"
    assert "mlm_upper_locked" not in ctx.state.data


def test_a_locked_upper_hopper_latches_and_falls_back_to_the_ladder(monkeypatch):
    ctx = _Ctx({**mlm.DEFAULT_CONFIG, "upperHopper": "auto"})
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(controller, "get_inventory", lambda: _paydirt(27))
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [UPPER_HOPPER])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)

    assert mlm.hopper_phase(ctx) is False        # the pay-dirt never left the inventory
    assert ctx.state.data["mlm_upper_locked"] is True
    # Latched: nothing upstairs is offered any more, so the next call takes the ladder.
    assert mlm._hopper_candidates(ctx) == []


def test_a_hopper_on_the_other_floor_is_never_offered(monkeypatch):
    # /objects reports the lower hopper while we stand one storey above it, at nearly the same
    # x/y. Only tile height separates them.
    ctx = _Ctx({**mlm.DEFAULT_CONFIG, "upperHopper": "auto"})
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [LOWER_HOPPER])
    assert mlm._hopper_candidates(ctx) == []


def test_the_lower_hopper_is_offered_once_we_are_downstairs(monkeypatch):
    ctx = _Ctx({**mlm.DEFAULT_CONFIG, "upperHopper": "auto"})
    _standing(monkeypatch, DOWNSTAIRS_H)
    monkeypatch.setattr(controller, "get_objects",
                        lambda fresh=False, **kw: [LOWER_HOPPER, UPPER_HOPPER])
    assert [h["y"] for h in mlm._hopper_candidates(ctx)] == [LOWER_HOPPER["y"]]


def test_a_successful_hopper_deposit_books_the_paydirt_as_pending(monkeypatch):
    ctx = _Ctx()
    inventory = {"items": _paydirt(27)}
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory["items"])
    _standing(monkeypatch, DOWNSTAIRS_H)
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [LOWER_HOPPER])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)

    def _click(*a, **k):
        inventory["items"] = []
        return True
    monkeypatch.setattr(controller, "click_entity", _click)

    assert mlm.hopper_phase(ctx) is True
    assert ctx.state.data["mlm_pending"] == 27


# --- banking the ore -------------------------------------------------------------------

def test_a_carried_hammer_switches_to_per_item_deposits(monkeypatch):
    # Deposit-inventory would bank the hammer; per-item Deposit-All cannot.
    ctx = _Ctx()
    ctx.state.data["mlm_hammer"] = True
    ctx.state.data["mlm_hammer_in_inventory"] = True
    _standing(monkeypatch, DOWNSTAIRS_H)
    monkeypatch.setattr(controller, "get_inventory", lambda: _items("Hammer", "Coal"))
    monkeypatch.setattr(mlm, "_open_deposit_box", lambda c: True)
    called = []
    monkeypatch.setattr(mlm, "_deposit_each_ore", lambda c: called.append("each") or True)
    monkeypatch.setattr(mlm, "_deposit_inventory", lambda c: called.append("all") or True)
    mlm.bank_phase(ctx)
    assert called == ["each"]


def test_without_a_hammer_it_is_one_click(monkeypatch):
    ctx = _Ctx()
    _standing(monkeypatch, DOWNSTAIRS_H)
    monkeypatch.setattr(controller, "get_inventory", lambda: _items("Open gem sack", "Coal"))
    monkeypatch.setattr(mlm, "_open_deposit_box", lambda c: True)
    called = []
    monkeypatch.setattr(mlm, "_deposit_each_ore", lambda c: called.append("each") or True)
    monkeypatch.setattr(mlm, "_deposit_inventory", lambda c: called.append("all") or True)
    mlm.bank_phase(ctx)
    assert called == ["all"]


def test_banking_ore_upstairs_climbs_down_first(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _items("Coal"))
    climbed = []
    monkeypatch.setattr(mlm, "climb", lambda c, up: climbed.append(up) or False)
    monkeypatch.setattr(mlm, "_open_deposit_box", lambda c: pytest.fail("walked to a box "
                                                                       "through the floor"))
    assert mlm.bank_phase(ctx) is False
    assert climbed == [False]


# --- startup ---------------------------------------------------------------------------

def test_no_pickaxe_stops_the_run(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3752, "y": 5680, "plane": 0, "regionId": mlm.REGION,
                                 "weaponName": "Dragon scimitar"})
    monkeypatch.setattr(controller, "get_inventory", lambda: _items("Open gem sack"))
    assert mlm._startup_ok(ctx) is False
    assert "pickaxe" in ctx.state.stop_reason


def test_starting_outside_the_mine_stops_the_run(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3200, "y": 3200, "plane": 0, "regionId": 12850,
                                 "weaponName": "Rune pickaxe"})
    assert mlm._startup_ok(ctx) is False
    assert "Motherlode Mine" in ctx.state.stop_reason


def test_a_missing_hammer_only_turns_repairs_off(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _items("Open gem sack"))
    assert mlm._startup_ok(ctx) is True
    assert ctx.state.safety_stopped is False
    assert ctx.state.data["mlm_hammer"] is False


def test_a_worn_hammer_enables_repairs_and_keeps_the_one_click_deposit(monkeypatch):
    """USER 2026-09-13: "we carry hammer but not in inventory its in equipment". An Imcando
    hammer is WORN, and the check only read the inventory -- so repairs were off on an account
    that could repair. /equipment is a dict keyed by SLOT, so it also has to be unwrapped."""
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_inventory", lambda: _items("Open gem sack"))
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"WEAPON": {"name": "Imcando hammer"},
                                 "HEAD": {"name": "Prospector helmet"}})
    assert mlm._startup_ok(ctx) is True
    assert ctx.state.data["mlm_hammer"] is True             # repairs ON
    # ...but the deposit must NOT fall to the slow per-stack path: a worn hammer is not at risk
    # from "Deposit inventory".
    assert ctx.state.data["mlm_hammer_in_inventory"] is False


def test_a_worn_hammer_still_uses_the_one_click_deposit(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["mlm_hammer"] = True                      # owned...
    ctx.state.data["mlm_hammer_in_inventory"] = False        # ...but worn
    _standing(monkeypatch, DOWNSTAIRS_H)
    monkeypatch.setattr(controller, "get_inventory", lambda: _items("Coal"))
    monkeypatch.setattr(mlm, "_open_deposit_box", lambda c: True)
    called = []
    monkeypatch.setattr(mlm, "_deposit_each_ore", lambda c: called.append("each") or True)
    monkeypatch.setattr(mlm, "_deposit_inventory", lambda c: called.append("all") or True)
    mlm.bank_phase(ctx)
    assert called == ["all"]


def _broken_strut(dist=1):
    return {"id": mlm.BROKEN_STRUT_ID, "name": "Broken strut", "actions": ["Hammer"],
            "x": 3742, "y": 5663, "plane": 0, "distance": dist,
            "screenX": 500, "screenY": 400, "tileHeight": 0}


def test_a_broken_strut_in_view_starts_the_patience_clock_without_the_chat_line(monkeypatch,
                                                                                _hermetic):
    """USER 2026-09-13: "still not fixing the strutt". mlm_broken_since was only ever set from
    the chat line announcing the break, so a machine ALREADY broken when the run started -- or
    broken while unfocused or on a break -- could never be repaired, however long we stood next
    to it. The object itself is the evidence."""
    ctx = _Ctx()
    ctx.state.data["mlm_hammer"] = True
    monkeypatch.setattr(controller, "get_varbit", lambda vid, **kw: 0)   # downstairs
    monkeypatch.setattr(mlm, "_objects", lambda **kw: [_broken_strut()])
    assert "mlm_broken_since" not in ctx.state.data
    mlm._maybe_repair(ctx)                       # no chat has ever fired
    assert ctx.state.data.get("mlm_broken_since") is not None


def test_the_hopper_trip_can_repair_so_a_break_cannot_deadlock_the_run(monkeypatch, _hermetic):
    """A broken machine stops the SACK filling; collect_phase only runs when there is a sack
    worth draining; and _maybe_repair used to be reachable ONLY from collect_phase. So a break
    deadlocked the run -- no repair meant no sack, and no sack meant no trip to repair from."""
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_varbit", lambda vid, **kw: 0)   # downstairs
    monkeypatch.setattr(mlm, "_hopper_candidates",
                        lambda c: [{"id": mlm.HOPPER_ID, "name": "Hopper", "distance": 1,
                                    "x": 3748, "y": 5672, "plane": 0,
                                    "screenX": 500, "screenY": 400}])
    monkeypatch.setattr(mlm, "_held_paydirt", lambda items=None: 27 if not called else 0)
    monkeypatch.setattr(mlm, "too_far", lambda o: False)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(mlm.outcome_waits, "await_pre", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(mlm, "_maybe_repair", lambda c: called.append("repair") or False)
    mlm.hopper_phase(ctx)
    assert called == ["repair"], "the hopper trip must be able to repair"


def test_a_blocked_machine_repairs_immediately_instead_of_reclicking(monkeypatch, _hermetic):
    """MEASURED live 2026-09-13: the hopper answered "You've already got some pay-dirt in the
    machine" on every click, the engine Spaced the box away, and the routine clicked again for
    minutes with the sack frozen -- a broken strut had stopped the waterway. USER: "you cant
    deposit if we have ore in the waterway without repairing the strut". Waiting out
    repairPatienceS here is backwards: we are the ones being blocked."""
    ctx = _Ctx()
    ctx.state.data["mlm_hammer"] = True
    monkeypatch.setattr(controller, "get_varbit", lambda vid, **kw: 0)      # downstairs
    monkeypatch.setattr(mlm, "_held_paydirt", lambda items=None: 27)
    monkeypatch.setattr(mlm, "_hopper_candidates",
                        lambda c: [{"id": mlm.HOPPER_ID, "name": "Hopper", "distance": 1,
                                    "x": 3748, "y": 5672, "plane": 0,
                                    "screenX": 500, "screenY": 400}])
    monkeypatch.setattr(mlm, "too_far", lambda o: False)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    monkeypatch.setattr(mlm.outcome_waits, "await_pre", lambda *a, **k: False)   # deposit refused
    monkeypatch.setattr(controller, "get_widget",
                        lambda g, c, **kw: {"text": "You've already got some pay-dirt in the "
                                                    "machine."} if g == mlm.MACHINE_BUSY_GROUP
                        else {})
    repairs = []
    monkeypatch.setattr(mlm, "_maybe_repair",
                        lambda c, urgent=False: repairs.append(urgent) or True)
    mlm.hopper_phase(ctx)
    assert repairs == [True], "a blocked machine must trigger an URGENT repair, not another click"


def test_a_nearby_anchor_is_clicked_directly_not_routed(monkeypatch, _hermetic):
    """MEASURED live 2026-09-14: the player drifted 7 tiles off the anchor, past
    ANCHOR_TOLERANCE, and every tick logged "no walking route to (3752,5680)" about twice a
    second for four minutes until the stall watch stopped the run. worldmap holds ONE set of
    collision data for both floors of this mine (they share x/y and plane 0) and it describes
    the downstairs layout, so it cannot route the upper floor at all. A few tiles in plain view
    should be clicked, not asked for."""
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    ctx.state.data["mlm_anchor_upstairs"] = True
    _standing(monkeypatch, UPSTAIRS_H, x=3759, y=5683)
    monkeypatch.setattr(mlm, "climb", lambda c, up: True)
    direct, routed = [], []
    monkeypatch.setattr(travel, "walk_to_exact_tile",
                        lambda c, x, y, p, **kw: direct.append((x, y)) or True)
    monkeypatch.setattr(travel, "walk_to", lambda c, g, **kw: routed.append(g) or False)
    mlm._return_to_anchor(ctx)
    assert direct == [(3752, 5680)], "a 7-tile anchor must be clicked directly"
    assert routed == [], "the router must not be consulted for a walk it cannot compute"


def test_a_distant_anchor_still_uses_the_router(monkeypatch, _hermetic):
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    ctx.state.data["mlm_anchor_upstairs"] = True
    _standing(monkeypatch, UPSTAIRS_H, x=3700, y=5600)
    monkeypatch.setattr(mlm, "climb", lambda c, up: True)
    direct, routed = [], []
    monkeypatch.setattr(travel, "walk_to_exact_tile",
                        lambda c, x, y, p, **kw: direct.append((x, y)) or True)
    monkeypatch.setattr(travel, "walk_to", lambda c, g, **kw: routed.append(g) or True)
    mlm._return_to_anchor(ctx)
    assert direct == [] and routed, "a long walk back still belongs to the router"


def test_an_empty_first_read_is_retried_rather_than_stopped(monkeypatch):
    # The first logged-in tick after an auto-login serves nothing -- run 47's GOTR lesson.
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3752, "y": 5680, "plane": 0, "regionId": mlm.REGION,
                                 "weaponName": ""})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    assert mlm._startup_ok(ctx) is False
    assert ctx.state.safety_stopped is False


# --- the stuck watch ----------------------------------------------------------------------

def test_the_stuck_watch_stops_a_wedged_run(_hermetic):
    # A wedged routine keeps TICKING -- the failing-climb run that prompted this ticked about
    # every 10s -- so the stall is accumulated tick by tick, not read off one clock jump.
    ctx = _Ctx()
    mlm._stuck_watch(ctx, "mine")
    for _ in range(int(mlm.STALL_STOP_S / 10) + 2):
        _hermetic.t += 10
        mlm._stuck_watch(ctx, "mine")
    assert ctx.state.safety_stopped is True


def test_a_long_unfocused_gap_is_not_charged_to_the_stall_watch(_hermetic):
    """REGRESSION 2026-09-13: the window sat unfocused for five minutes and the first tick back
    safety-stopped a healthy run with "nothing changed for 315s". tick() is not called while
    unfocused, on a break, or paused, so that time was never the routine's to be charged for."""
    ctx = _Ctx()
    mlm._stuck_watch(ctx, "mine")
    _hermetic.t += mlm.STALL_STOP_S + 75      # a 5-minute park, as one gap
    mlm._stuck_watch(ctx, "mine")
    assert ctx.state.safety_stopped is False


def test_progress_resets_the_stuck_watch(_hermetic, monkeypatch):
    ctx = _Ctx()
    mlm._stuck_watch(ctx, "mine")
    _hermetic.t += mlm.STALL_STOP_S - 1
    monkeypatch.setattr(controller, "get_inventory", lambda: _paydirt(3))
    mlm._stuck_watch(ctx, "mine")
    _hermetic.t += 5
    mlm._stuck_watch(ctx, "mine")
    assert ctx.state.safety_stopped is False


# --- breaks --------------------------------------------------------------------------------

def test_before_break_empties_the_inventory_into_the_hopper(monkeypatch):
    ctx = _Ctx()
    held = {"n": 27}
    monkeypatch.setattr(controller, "get_inventory", lambda: _paydirt(held["n"]))
    calls = []

    def _hopper(c):
        calls.append(1)
        held["n"] = 0
        return True
    monkeypatch.setattr(mlm, "hopper_phase", _hopper)
    mlm.before_break(ctx)
    assert calls == [1]


def test_before_break_does_nothing_with_an_empty_inventory(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(mlm, "hopper_phase", lambda c: pytest.fail("no pay-dirt to deposit"))
    mlm.before_break(ctx)


# --- which floor (the first live run's bug) -------------------------------------------

def test_is_upper_uses_runelites_own_threshold():
    assert mlm.is_upper(-600) is True
    assert mlm.is_upper(-490) is False      # strictly below, as MotherlodePlugin has it
    assert mlm.is_upper(0) is False
    assert mlm.is_upper(None) is False


def test_a_vein_one_storey_below_is_never_offered(monkeypatch):
    """THE bug from the first live run (2026-09-07).

    The routine stood at (3749,5684) upstairs and clicked the vein at (3749,5685) for 90
    seconds; /last-action confirmed "Mine Ore vein" every single time and nothing happened,
    because that vein is on the floor BELOW. USER: "clicking lower level ore from highground".
    Coordinates cannot tell these apart -- only the render height can.
    """
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(travel, "can_reach", lambda *a, **k: True)
    downstairs = _vein(3749, 5685, obj_id=26661, dist=1, tileHeight=DOWNSTAIRS_H)
    upstairs = _vein(3750, 5682, dist=3, tileHeight=UPSTAIRS_H)
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None,
                         objects=[downstairs, upstairs]) is upstairs


def test_without_tile_height_the_vendored_tile_table_answers(monkeypatch):
    """An un-rebuilt client still gets a working floor test, from the same plugin's own table.

    (3749,5685) is the vein that cost the first live run; (3750,5682) is one that mined fine.
    """
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3752, "y": 5680, "plane": 0, "regionId": mlm.REGION,
                                 "weaponName": "Rune pickaxe"})       # no tileHeight
    monkeypatch.setattr(travel, "can_reach", lambda *a, **k: True)
    downstairs = _vein(3749, 5685, obj_id=26661, dist=1)
    upstairs = _vein(3750, 5682, dist=3)
    assert mlm._floor_ready(ctx) is True          # a warning, never a stop
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None,
                         objects=[downstairs, upstairs]) is upstairs


def test_a_ladder_is_never_floor_filtered(monkeypatch):
    """Both ladders stand on tiles that read as downstairs whichever test is in use -- filtering
    them would make the climb down impossible. The Climb action is what disambiguates."""
    ctx = _Ctx()
    _standing(monkeypatch, UPSTAIRS_H)
    down = {"id": mlm.LADDER_DOWN_ID, "name": "Ladder", "x": 3755, "y": 5674, "plane": 0,
            "distance": 4, "actions": ["Climb"], "screenX": 1, "screenY": 1, "tileHeight": 0}
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [down])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **k: clicked.append(e) or True)
    mlm.climb(ctx, up=False)
    assert clicked and clicked[0]["id"] == mlm.LADDER_DOWN_ID


# --- veins that swallow clicks ---------------------------------------------------------

def test_a_vein_that_gives_nothing_twice_is_set_aside(_hermetic, monkeypatch):
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(travel, "can_reach", lambda *a, **k: True)
    bad = _vein(3749, 5685, dist=1, tileHeight=UPSTAIRS_H)
    good = _vein(3750, 5682, dist=3, tileHeight=UPSTAIRS_H)

    mlm.note_vein_failure(ctx, bad, "idle")
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None, objects=[bad, good]) is bad
    mlm.note_vein_failure(ctx, bad, "idle")
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None, objects=[bad, good]) is good

    _hermetic.t += mlm.VEIN_BLOCK_S + 1
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None, objects=[bad, good]) is bad


def test_a_vein_that_depletes_clears_its_failure_count(_hermetic, monkeypatch):
    # A vein that merely took a while must not be penalised for it.
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = {"x": 3752, "y": 5680, "plane": 0}
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(travel, "can_reach", lambda *a, **k: True)
    vein = _vein(3750, 5682, dist=1, tileHeight=UPSTAIRS_H)
    mlm.note_vein_failure(ctx, vein, "idle")
    mlm.note_vein_failure(ctx, vein, "depleted")
    mlm.note_vein_failure(ctx, vein, "idle")
    assert mlm.pick_vein(ctx, _reach((3752, 5680)), None, objects=[vein]) is vein


# --- getting back to the veins ---------------------------------------------------------

ANCHOR = {"x": 3752, "y": 5680, "plane": 0}


def test_at_anchor_is_a_floor_question_first():
    # MEASURED live (mlm_live2): the hopper trip left the routine downstairs directly below the
    # anchor, where every coordinate test says "you're here".
    assert mlm.at_anchor(ANCHOR, ANCHOR, upper=True, anchor_upper=True) is True
    assert mlm.at_anchor(ANCHOR, ANCHOR, upper=False, anchor_upper=True) is False


def test_at_anchor_wants_us_near_it_too():
    far = {"x": ANCHOR["x"] + mlm.ANCHOR_TOLERANCE + 1, "y": ANCHOR["y"], "plane": 0}
    assert mlm.at_anchor(far, ANCHOR, upper=True, anchor_upper=True) is False


def test_mining_returns_to_the_anchor_before_it_picks_a_vein(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = ANCHOR
    ctx.state.data["mlm_anchor_upstairs"] = True
    _standing(monkeypatch, DOWNSTAIRS_H)          # just deposited at the lower hopper
    monkeypatch.setattr(mlm, "pick_vein",
                        lambda *a, **k: pytest.fail("mined before going back up"))
    returned = []
    monkeypatch.setattr(mlm, "_return_to_anchor", lambda c: returned.append(True))
    mlm.mine_phase(ctx)
    assert returned == [True]


def test_a_run_started_downstairs_anchors_on_the_configured_pocket(monkeypatch):
    """mlm_live3: run 2 wandered downstairs chasing veins near the ladder, and run 3 inherited
    that tile as the place to mine. The anchor is the user's pocket unless we are already
    upstairs."""
    ctx = _Ctx()
    _standing(monkeypatch, DOWNSTAIRS_H)
    mlm.setup(ctx)
    assert ctx.state.data["mlm_anchor"]["x"] == mlm.TILE_ANCHOR_DEFAULT["x"]
    assert ctx.state.data["mlm_anchor"]["y"] == mlm.TILE_ANCHOR_DEFAULT["y"]
    assert ctx.state.data["mlm_anchor_upstairs"] is True


def test_starting_upstairs_anchors_where_we_stand(monkeypatch):
    ctx = _Ctx()
    _standing(monkeypatch, UPSTAIRS_H)
    mlm.setup(ctx)
    assert ctx.state.data["mlm_anchor"]["x"] == 3752
    assert ctx.state.data["mlm_anchor_upstairs"] is True


def test_world_clicks_carry_no_hover_gate(monkeypatch):
    """mlm_live4: every ladder click was withheld -- "hover never matched action='Climb'" --
    because /hover reports what is under the cursor now, and these clicks are issued from
    across the room. They confirm on their own outcome instead."""
    ctx = _Ctx()
    _standing(monkeypatch, UPSTAIRS_H)
    down = {"id": mlm.LADDER_DOWN_ID, "name": "Ladder", "x": 3755, "y": 5674, "plane": 0,
            "distance": 9, "actions": ["Climb"], "screenX": 1, "screenY": 1}
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [down])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **k: seen.update(k) or True)
    mlm.climb(ctx, up=False)
    assert "expected_action" not in seen


def test_a_ladder_that_never_registers_stops_the_run(monkeypatch):
    ctx = _Ctx()
    _standing(monkeypatch, UPSTAIRS_H)
    down = {"id": mlm.LADDER_DOWN_ID, "name": "Ladder", "x": 3755, "y": 5674, "plane": 0,
            "distance": 4, "actions": ["Climb"], "screenX": 1, "screenY": 1}
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [down])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    for _ in range(mlm.CLIMB_MAX_TRIES):
        mlm.climb(ctx, up=False)
    assert ctx.state.safety_stopped is True
    assert "climb" in ctx.state.stop_reason


# --- walk first, click close ------------------------------------------------------------

def test_an_off_screen_object_is_walked_to_not_clicked():
    """mlm_live5: the up-ladder was clicked from 21 tiles with onScreen false -- a projection
    that is not on the screen -- and the click did nothing at all."""
    assert mlm.too_far({"distance": 21, "onScreen": False}) is True
    assert mlm.too_far({"distance": 3, "onScreen": False}) is True     # onScreen decides
    assert mlm.too_far({"distance": 21, "onScreen": True}) is True
    assert mlm.too_far({"distance": 9, "onScreen": True}) is False


def test_a_far_ladder_is_walked_to_before_it_is_clicked(monkeypatch):
    ctx = _Ctx()
    _standing(monkeypatch, DOWNSTAIRS_H)
    far = {"id": mlm.LADDER_UP_ID, "name": "Ladder", "x": 3755, "y": 5673, "plane": 0,
           "distance": 21, "actions": ["Climb"], "onScreen": False, "screenX": 1, "screenY": 1}
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [far])
    monkeypatch.setattr(controller, "click_entity",
                        lambda *a, **k: pytest.fail("clicked an off-screen ladder"))
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda c, t, **k: walked.append(t) or True)
    assert mlm.climb(ctx, up=True) is False
    assert walked and walked[0]["x"] == 3755


# --- a full sack (mlm_live7) --------------------------------------------------------------

def test_a_full_sack_outranks_a_hopper_trip():
    """MEASURED live: with the sack at 111/108 the game REFUSES to mine at all and every click
    dies silently, and pay-dirt deposited into a full sack is thrown away. So collect comes
    before hopper even with a full inventory in hand."""
    assert _phase(held_paydirt=27, inventory_full=True, allowance=0,
                  sack_eff=108, capacity=108) == "collect"


def test_the_games_own_refusal_forces_a_collect(monkeypatch):
    """The arithmetic that should have prevented 111/108 failed because `pending` -- pay-dirt on
    the conveyor -- lives in ctx.state.data and is wiped by a restart, while the pay-dirt on the
    belt is not. The chat line is the belt to that brace."""
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_varbit",
                        lambda vid: 84 if vid == mlm.VARBIT_SACK else 0)   # varbit still lagging
    assert mlm.phase_of(ctx) == "mine"
    mlm.on_event(ctx, "CHAT_MESSAGE",
                 {"message": "Your sack will be full once your current dirt is cleaned."})
    assert mlm.phase_of(ctx) == "collect"


def test_the_refusal_latch_is_cleared_only_by_an_empty_sack(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["mlm_sack_full"] = True
    monkeypatch.setattr(controller, "get_varbit",
                        lambda vid: 40 if vid == mlm.VARBIT_SACK else 0)
    assert mlm.phase_of(ctx) == "collect"          # half-drained is not drained
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    assert mlm.phase_of(ctx) == "mine"
    assert "mlm_sack_full" not in ctx.state.data


def test_a_full_sack_is_not_topped_up_before_the_collect(monkeypatch):
    """collect_phase empties the hands into the hopper first to make room for ore -- but not
    when the sack cannot take them, because that deposit is pay-dirt thrown away."""
    ctx = _Ctx()
    _standing(monkeypatch, DOWNSTAIRS_H)
    monkeypatch.setattr(controller, "get_inventory", lambda: _paydirt(5))
    monkeypatch.setattr(controller, "get_varbit",
                        lambda vid: 108 if vid == mlm.VARBIT_SACK else 0)
    monkeypatch.setattr(mlm, "hopper_phase",
                        lambda c: pytest.fail("deposited into a full sack"))
    monkeypatch.setattr(mlm, "climb", lambda c, up: True)
    monkeypatch.setattr(mlm, "_maybe_repair", lambda c: False)
    monkeypatch.setattr(mlm, "search_sack", lambda c: False)
    mlm.collect_phase(ctx)


def test_the_deposit_box_is_closed_after_banking(monkeypatch):
    """mlm_live8: leaving interface 192 open cost the whole return trip. An open interface EATS
    THE FIRST WORLD CLICK, so every tick's ladder click merely closed it -- eight "the climb up
    did not register" in a row with the player never moving, then the climb ceiling stopped the
    run."""
    ctx = _Ctx()
    _standing(monkeypatch, DOWNSTAIRS_H)
    monkeypatch.setattr(controller, "get_inventory", lambda: _items("Open gem sack", "Coal"))
    monkeypatch.setattr(mlm, "_open_deposit_box", lambda c: True)
    closed = []
    monkeypatch.setattr(mlm, "_close_deposit_box", lambda c: closed.append(True) or True)
    # ore in hand on the way in, none once the deposit has landed
    banked = {"done": False}

    def _ore(items=None):
        return [] if banked["done"] else [{"name": "Coal"}]
    monkeypatch.setattr(mlm, "_ore_items", _ore)
    monkeypatch.setattr(mlm, "_deposit_inventory",
                        lambda c: banked.update(done=True) or True)
    mlm.bank_phase(ctx)
    assert closed == [True]


def test_mining_will_not_start_with_the_deposit_box_open(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["mlm_anchor"] = ANCHOR
    ctx.state.data["mlm_anchor_upstairs"] = True
    _standing(monkeypatch, UPSTAIRS_H)
    monkeypatch.setattr(mlm, "_deposit_open", lambda: True)
    monkeypatch.setattr(mlm, "pick_vein",
                        lambda *a, **k: pytest.fail("clicked the world through an open panel"))
    closed = []
    monkeypatch.setattr(mlm, "_close_deposit_box", lambda c: closed.append(True) or True)
    mlm.mine_phase(ctx)
    assert closed == [True]


def test_a_climb_still_walking_is_waited_on(monkeypatch):
    """mlm_live9: a climb reported "did not register" at 11.4s and had plainly worked -- the next
    tick was already upstairs and mining. A tiles*0.6s budget assumes RUNNING, and at low run
    energy the same walk takes twice as long, which would count healthy climbs toward
    CLIMB_MAX_TRIES."""
    ctx = _Ctx()
    clock = _Clock()
    monkeypatch.setattr(mlm, "_now", clock.now)
    monkeypatch.setattr(mlm, "_sleep", clock.sleep)
    down = {"id": mlm.LADDER_UP_ID, "name": "Ladder", "x": 3755, "y": 5673, "plane": 0,
            "distance": 9, "actions": ["Climb"], "onScreen": True, "screenX": 1, "screenY": 1}
    monkeypatch.setattr(controller, "get_objects", lambda fresh=False, **kw: [down])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **k: True)
    start = clock.t
    # Downstairs and walking until well past the plain budget, then upstairs.
    monkeypatch.setattr(controller, "get_varbit",
                        lambda vid: (1 if clock.t - start > 18 else 0)
                        if vid == mlm.VARBIT_UPSTAIRS else 0)
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": 3755, "y": 5670, "plane": 0, "regionId": mlm.REGION,
                                 "weaponName": "Rune pickaxe", "tileHeight": 0,
                                 "moving": clock.t - start <= 18})
    assert mlm.climb(ctx, up=True) is True
    assert ctx.state.data.get("mlm_climb_fails") == 0
