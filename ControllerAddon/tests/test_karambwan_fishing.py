"""Tests for routines/karambwan_fishing.py (2026-09-06), pinned to the user's recording
recordings/2026-09-06_083723_karamwan-fishing.* -- widget ids, option texts, chat lines."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import bridge_events
import controller
import outcome_waits
import travel
import karambwan_fishing as kf


class _State:
    def __init__(self):
        self.data = {}
        self.config = dict(kf.DEFAULT_CONFIG)
        self.safety_stopped = False
        self.safety_reason = None
        self.paused = False

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason

    def break_due(self, breaks_cfg):
        return False


class _Ctx:
    def __init__(self, events):
        self.events = events
        self.ser = object()
        self.state = _State()


def _stream(monkeypatch, batches):
    """A real EventStream fed by a scripted poll_udp: each batch is a list of
    (type, payload, then_callable) delivered on one poll, the callable mutating the fake game
    BEFORE the event is visible -- so a signal's HTTP confirm sees the new state."""
    queue = list(batches)

    def poll_udp(_sock):
        if not queue:
            return []
        out = []
        for ev in queue.pop(0):
            if len(ev) > 2 and ev[2] is not None:
                ev[2]()
            out.append((ev[0], ev[1]))
        return out
    monkeypatch.setattr(controller, "poll_udp", poll_udp)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    monkeypatch.setattr(outcome_waits, "still_segment_s", lambda: 1.0)
    clock = {"t": 1000.0}
    monkeypatch.setattr(kf, "_now", lambda: clock["t"])
    monkeypatch.setattr(kf, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    ctx = _Ctx(bridge_events.EventStream(sock=None))
    ctx.clock = clock
    return ctx


def _kit(fish=0, bait=True, flakes=True):
    inv = [{"name": "Karambwan vessel", "slot": 1}, {"name": "Fish barrel", "slot": 2}]
    if bait:
        inv.append({"name": "Raw karambwanji", "slot": 3, "qty": 14000})
    if flakes:
        inv.append({"name": "Spirit flakes", "slot": 4, "qty": 1100})
    inv += [{"name": "Raw karambwan", "slot": 5 + i} for i in range(fish)]
    return inv


DKP = {"x": 2899, "y": 3118, "plane": 0, "regionId": kf.REGION_DKP}
MINE = {"x": 2841, "y": 9388, "plane": 0, "regionId": kf.REGION_GEM_MINE}
LEGENDS = {"x": 2730, "y": 3348, "plane": 0, "regionId": kf.REGION_LEGENDS}


# --- phase derivation (pure) -------------------------------------------------------------------

def test_derive_phase_follows_the_recorded_loop():
    """The barrel stores every catch on its own (USER 2026-09-06), so there is no Fill leg:
    a full inventory at DKP banks straight away."""
    d = kf.derive_phase
    assert d(DKP, 24, 0, {}, {}) == "fish"
    assert d(DKP, 4, 20, {}, {}) == "fish"                    # room left: keep fishing
    assert d(DKP, 0, 24, {}, {}) == "to_bank"                 # full: the barrel is full too
    assert d(MINE, 4, 24, {}, {}) == "bank"
    assert d(MINE, 28, 0, {}, {}) == "bank"                   # barrel not emptied this trip
    assert d(MINE, 28, 0, {"kf_trip_emptied": True}, {}) == "cape"
    assert d(LEGENDS, 28, 0, {}, {}) == "ring"
    assert d({"x": 3200, "y": 3200, "regionId": 12850}, 28, 0, {}, {}) == "lost"


def test_barrel_room_reads_the_m8aq_reader_and_admits_when_it_cannot_tell(monkeypatch):
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"present": True, "contentsLoaded": True,
                                                            "remainingCapacity": 3})
    assert kf.barrel_room() == 3
    monkeypatch.setattr(controller, "get_api", lambda g, r: {"present": True, "contentsLoaded": False,
                                                            "remainingCapacity": -1})
    assert kf.barrel_room() == -1
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    assert kf.barrel_room() == -1


# --- fishing ------------------------------------------------------------------------------------

def test_fish_phase_clicks_the_spot_and_returns_once_the_inventory_is_full(monkeypatch):
    inv = {"items": _kit(fish=20)}
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv["items"]))
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": False})
    monkeypatch.setattr(controller, "get_state", lambda: {"dialogOpen": False})
    spot = {"id": kf.SPOT_NPC_ID, "index": 21107, "name": "Fishing spot", "distance": 8}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [spot, {"id": 464, "index": 1, "distance": 3}])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    seen = {}
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: seen.update(kw) or True)
    ctx = _stream(monkeypatch, [
        [("INVENTORY_CHANGED", {"tick": 5}, lambda: inv.__setitem__("items", _kit(fish=22)))],
        [("INVENTORY_CHANGED", {"tick": 9}, lambda: inv.__setitem__("items", _kit(fish=24)))],
    ])
    assert kf.fish_phase(ctx, kf.DEFAULT_CONFIG) is True
    assert seen.get("expected_action") == kf.SPOT_ACTION
    assert seen.get("refresh_target") is not None
    assert ctx.clock["t"] - 1000.0 < 5.0                  # event-driven, not a fixed wait


def test_fish_wait_returns_full_on_the_games_own_chat_line(monkeypatch):
    inv = {"items": _kit(fish=23)}
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv["items"]))
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": False})
    monkeypatch.setattr(controller, "get_state", lambda: {"dialogOpen": False})
    ctx = _stream(monkeypatch, [
        [("CHAT_MESSAGE", {"message": "You can't carry any more Karambwan."},
          lambda: inv.__setitem__("items", _kit(fish=24)))],
    ])
    assert kf.fish_wait(ctx) == "full"


def test_fish_wait_reports_a_sustained_idle_player(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: _kit(fish=10))
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": True})
    monkeypatch.setattr(controller, "get_state", lambda: {"dialogOpen": False})
    ctx = _stream(monkeypatch, [])
    assert kf.fish_wait(ctx) == "idle"
    assert kf.IDLE_RECLICK_S <= ctx.clock["t"] - 1000.0 < kf.IDLE_RECLICK_S + 2.0


def test_fish_wait_dismisses_a_level_up_dialogue_and_notices_no_bait(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: _kit(fish=10, bait=False))
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": False})
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    monkeypatch.setattr(controller, "get_state", lambda: {"dialogOpen": True})
    ctx = _stream(monkeypatch, [])
    assert kf.fish_wait(ctx) == "no_bait"
    assert keys == []                       # bait check comes first; nothing to fish with anyway


def test_fish_phase_walks_when_no_spot_is_in_view(monkeypatch):
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, t, **kw: walked.append(t) or True)
    ctx = _stream(monkeypatch, [])
    assert kf.fish_phase(ctx, kf.DEFAULT_CONFIG) is False
    assert walked == [kf.TILE_SPOT]


def test_repeated_idle_clicks_that_catch_nothing_are_a_safety_stop(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: _kit(fish=10))
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": True})
    monkeypatch.setattr(controller, "get_state", lambda: {"dialogOpen": False})
    spot = {"id": kf.SPOT_NPC_ID, "index": 21107, "distance": 1}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [spot])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _stream(monkeypatch, [])
    for _ in range(kf.IDLE_STOP_AFTER - 1):
        assert kf.fish_phase(ctx, kf.DEFAULT_CONFIG) is True
        assert not ctx.state.safety_stopped
    assert kf.fish_phase(ctx, kf.DEFAULT_CONFIG) is False
    assert ctx.state.safety_stopped


# --- worn-item teleports ------------------------------------------------------------------------

def _equipment_setup(monkeypatch, player):
    monkeypatch.setattr(controller, "get_player", lambda: dict(player))
    monkeypatch.setattr(controller, "open_equipment_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": True, "screenX": 9, "screenY": 9,
                                                          "group": group, "child": child})
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, option, **kw: menu.append((e["group"], e["child"], option)) or True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    monkeypatch.setattr(controller, "get_active_tab", lambda: kf.INVENTORY_TAB_INDEX)
    return menu


def test_ensure_inventory_tab_taps_until_the_active_tab_varc_agrees(monkeypatch):
    """kf_live1: the blind "w" tap after a teleport was eaten by the LOADING screen, the panel
    on screen was still the equipment one and the next inventory click was withheld with
    nothing under the cursor. The tab is confirmed on /widget/active-tab, not assumed."""
    tab = {"v": 4}                                       # equipment showing
    monkeypatch.setattr(controller, "get_active_tab", lambda: tab["v"])
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: (keys.append(k), tab.__setitem__("v", 3)))
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    ctx = _Ctx(None)
    assert kf._ensure_inventory_tab(ctx) is True
    assert keys == [controller.INVENTORY_TAB_KEY]


def test_to_bank_right_clicks_the_worn_gloves_for_gem_mine(monkeypatch):
    player = dict(DKP)
    menu = _equipment_setup(monkeypatch, player)
    ctx = _stream(monkeypatch, [
        [("VARBIT_CHANGED", {"varbitId": kf.VARBIT_BUSY, "value": 1}, None)],
        [("VARBIT_CHANGED", {"varbitId": kf.VARBIT_BUSY, "value": 0}, lambda: player.update(MINE))],
    ])
    ctx.state.data["kf_trip_emptied"] = True
    assert kf.to_bank_phase(ctx, kf.DEFAULT_CONFIG) is True
    assert ctx.clock["t"] - 1000.0 < 1.0          # landed on the pushed varbit, not the budget
    assert menu == [(387, kf.EQUIP_GLOVES_CHILD, kf.OPT_GEM_MINE)]
    assert ctx.state.data["kf_trip_emptied"] is False


def test_cape_phase_right_clicks_the_worn_cape_and_resets_the_trip(monkeypatch):
    player = dict(MINE)
    menu = _equipment_setup(monkeypatch, player)
    ctx = _stream(monkeypatch, [
        [("ANIMATION_CHANGED", {"animation": -1, "from": 714}, lambda: player.update(LEGENDS))],
    ])
    ctx.state.data["kf_trip_emptied"] = True
    assert kf.cape_phase(ctx, kf.DEFAULT_CONFIG) is True
    assert menu == [(387, kf.EQUIP_CAPE_CHILD, kf.OPT_CAPE_TELEPORT)]
    assert ctx.state.data["kf_trip_emptied"] is False


def test_worn_teleport_that_never_lands_returns_false(monkeypatch):
    player = dict(DKP)
    _equipment_setup(monkeypatch, player)
    ctx = _stream(monkeypatch, [])
    assert kf.to_bank_phase(ctx, kf.DEFAULT_CONFIG) is False
    assert ctx.clock["t"] - 1000.0 >= kf.TELEPORT_BUDGET_S


def test_landing_is_rechecked_every_tick_when_the_signal_fired_during_loading(monkeypatch):
    """kf_live1: the busy varbit fires as LOADING begins, /player fails at that instant, and a
    single await sat out its whole budget. The landing must be noticed a tick after the bridge
    answers again, not at the budget."""
    player = dict(DKP)
    _equipment_setup(monkeypatch, player)
    ctx = _stream(monkeypatch, [
        [("VARBIT_CHANGED", {"varbitId": kf.VARBIT_BUSY, "value": 0}, None)],   # confirm: still DKP
    ])
    # The region flips 2.0s after the click with NO further event.
    t_click = ctx.clock["t"]
    real_now = kf._now
    monkeypatch.setattr(kf, "_now", lambda: (player.update(MINE) if real_now() - t_click >= 2.0 else None) or real_now())
    assert kf.to_bank_phase(ctx, kf.DEFAULT_CONFIG) is True
    assert ctx.clock["t"] - t_click < 2.0 + 2 * kf.LANDING_RECHECK_S + 0.1


# --- the deposit chest --------------------------------------------------------------------------

def test_bank_phase_deposits_all_empties_the_barrel_and_closes(monkeypatch):
    inv = {"items": _kit(fish=24)}
    ui = {"open": True}
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv["items"]))
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": ui["open"], "screenX": 1, "screenY": 1,
                                                          "group": group, "child": child})
    children = [
        {"index": 2, "itemName": "Fish barrel", "screenX": 5, "screenY": 5},
        {"index": 19, "itemName": "Raw karambwan", "screenX": 6, "screenY": 6},
    ]
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: list(children))   # a LIST, as controller returns
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, option, **kw: menu.append((e.get("itemName"), option)) or True)
    keys = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: keys.append(k))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _stream(monkeypatch, [
        [("INVENTORY_CHANGED", {"tick": 1}, lambda: inv.__setitem__("items", _kit(fish=0)))],
        [("CHAT_MESSAGE", {"message": "You empty the barrel."}, None)],
        [("WIDGET_CLOSED", {"groupId": kf.DEPOSIT_GROUP}, lambda: ui.__setitem__("open", False))],
    ])
    assert kf.bank_phase(ctx, kf.DEFAULT_CONFIG) is True
    assert menu == [("Raw karambwan", kf.DEPOSIT_ALL_ACTION), ("Fish barrel", kf.BARREL_EMPTY_ACTIONS)]
    assert keys == ["ESC"]
    assert ctx.state.data["kf_trip_emptied"] is True


def test_bank_phase_opens_the_chest_first_and_waits_for_the_interface(monkeypatch):
    ui = {"open": False}
    monkeypatch.setattr(controller, "get_inventory", lambda: _kit(fish=24))
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": ui["open"], "screenX": 1, "screenY": 1})
    chest = {"id": 10530, "name": "Bank Deposit Chest", "actions": ["Deposit"], "x": 2842, "y": 9382,
             "plane": 0, "distance": 6, "screenX": 726, "screenY": 328}
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [chest])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: (lambda: None))
    seen = {}
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: seen.update(kw) or True)
    # The interface never opens in this test: bank_phase must return False, not deposit blind.
    monkeypatch.setattr(controller, "get_widget_children", lambda g, c: [])
    ctx = _stream(monkeypatch, [])
    assert kf.bank_phase(ctx, kf.DEFAULT_CONFIG) is False
    assert seen.get("expected_action") == kf.DEPOSIT_ACTION
    assert seen.get("refresh_target") is not None


def test_bank_phase_walks_to_the_chest_when_it_is_not_in_view(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: _kit(fish=24))
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: {"clickable": False})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, t, **kw: walked.append(t) or True)
    ctx = _stream(monkeypatch, [])
    assert kf.bank_phase(ctx, kf.DEFAULT_CONFIG) is False
    assert walked == [kf.TILE_DEPOSIT]


# --- the fairy ring -----------------------------------------------------------------------------

def _ring_setup(monkeypatch, player):
    monkeypatch.setattr(controller, "get_player", lambda: dict(player))
    ring = {"id": 29495, "name": "Fairy ring", "x": 2740, "y": 3351, "plane": 0, "distance": 12,
            "screenX": 531, "screenY": 221}
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [ring])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: (lambda: None))
    return ring


def test_ring_phase_takes_last_destination_dkp_in_one_click(monkeypatch):
    player = dict(LEGENDS)
    _ring_setup(monkeypatch, player)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, option, **kw: menu.append(option) or True)
    ctx = _stream(monkeypatch, [
        [("PLAYER_MOVING", {"tick": 2}, None)],
        [("PLAYER_STOPPED", {"tick": 12}, lambda: player.update(DKP))],
    ])
    assert kf.ring_phase(ctx, kf.DEFAULT_CONFIG) is True
    assert menu == [kf.OPT_LAST_DEST_DKP]


def test_ring_phase_dials_dkp_when_last_destination_is_not_offered(monkeypatch):
    player = dict(LEGENDS)
    _ring_setup(monkeypatch, player)
    ui = {"open": False}
    # Dial arrows: 19/21/23 turn the left/middle/right dial DOWN here (the routine has to learn
    # the direction), 20/22/24 turn it up. Values start at A/I/P = (0, 0, 0); DKP is (1, 2, 0).
    varbits = {3985: 0, 3986: 0, 3987: 0}
    arrows = {19: (3985, -1), 20: (3985, +1), 21: (3986, -1), 22: (3986, +1), 23: (3987, -1), 24: (3987, +1)}
    monkeypatch.setattr(controller, "get_varbit", lambda vid: varbits[vid])
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": ui["open"], "screenX": 1, "screenY": 1,
                                                          "group": group, "child": child})
    clicks = []

    def click_entity(ser, e, **kw):
        child = e.get("child")
        clicks.append(child)
        if child in arrows:
            vid, d = arrows[child]
            varbits[vid] = (varbits[vid] + d) % 4
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    menu = []

    def click_menu_action(ser, e, option, **kw):
        menu.append(option)
        return option != kf.OPT_LAST_DEST_DKP
    monkeypatch.setattr(controller, "click_menu_action", click_menu_action)
    ctx = _stream(monkeypatch, [
        [("WIDGET_LOADED", {"groupId": kf.RING_GROUP}, lambda: ui.__setitem__("open", True))],
        [("PLAYER_STOPPED", {"tick": 20}, lambda: player.update(DKP))],
    ])
    assert kf.ring_phase(ctx, kf.DEFAULT_CONFIG) is True
    assert menu == [kf.OPT_LAST_DEST_DKP, kf.OPT_CONFIGURE]
    assert (varbits[3985], varbits[3986], varbits[3987]) == (1, 2, 0)
    assert clicks[-1] == kf.RING_CONFIRM_CHILD
    assert len(clicks) <= 1 + 4 + 4        # confirm + bounded dial clicks per dial


def test_ring_phase_walks_when_the_ring_is_out_of_range(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, t, **kw: walked.append(t) or True)
    ctx = _stream(monkeypatch, [])
    assert kf.ring_phase(ctx, kf.DEFAULT_CONFIG) is False
    assert walked == [kf.TILE_BLR]


# --- startup and the engine hook ----------------------------------------------------------------

def _worn(gloves="Karamja gloves 3", cape="Quest point cape"):
    return {"gloves": {"name": gloves} if gloves else None, "cape": {"name": cape} if cape else None}


def test_startup_stops_naming_every_missing_piece(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: _kit(bait=False))
    monkeypatch.setattr(controller, "get_equipment", lambda: _worn(gloves="Karamja gloves 2", cape=None))
    ctx = _Ctx(None)
    assert kf._startup_ok(ctx) is False
    assert "karambwanji" in ctx.state.safety_reason.lower()
    assert "gloves" in ctx.state.safety_reason.lower()
    assert "cape" in ctx.state.safety_reason.lower()


def test_startup_passes_with_the_recorded_loadout(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: _kit())
    monkeypatch.setattr(controller, "get_equipment", lambda: _worn())
    ctx = _Ctx(None)
    assert kf._startup_ok(ctx) is True
    assert not ctx.state.safety_stopped


def test_tick_chains_a_full_inventory_straight_into_the_bank_trip(monkeypatch):
    inv = {"items": _kit(fish=0)}
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inv["items"]))
    monkeypatch.setattr(controller, "get_equipment", lambda: _worn())
    monkeypatch.setattr(controller, "get_player", lambda: dict(DKP))
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    calls = []

    def fish(ctx, config):
        calls.append("fish")
        inv["items"] = _kit(fish=24)
        return True

    def to_bank(ctx, config):
        calls.append("to_bank")
        return False
    monkeypatch.setitem(kf.PHASES, "fish", fish)
    monkeypatch.setitem(kf.PHASES, "to_bank", to_bank)
    ctx = _stream(monkeypatch, [])
    kf.tick(ctx)
    assert calls == ["fish", "to_bank"]
    assert ctx.state.data["kf_phase"] == "to_bank"


def test_tick_stops_when_lost(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: _kit())
    monkeypatch.setattr(controller, "get_equipment", lambda: _worn())
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3222, "y": 3218, "regionId": 12850})
    monkeypatch.setattr(controller, "get_api", lambda g, r: None)
    ctx = _stream(monkeypatch, [])
    kf.tick(ctx)
    assert ctx.state.safety_stopped
    assert "unexpected position" in ctx.state.safety_reason


def test_stuck_watch_stops_after_the_stall_budget(monkeypatch):
    ctx = _stream(monkeypatch, [])
    sig = ("fish", 10, 10, kf.REGION_DKP, False)
    kf._stuck_watch(ctx, sig)
    ctx.clock["t"] += kf.STALL_WARN_S + 1
    kf._stuck_watch(ctx, sig)
    assert ctx.state.data["kf_sig_warned"] and not ctx.state.safety_stopped
    ctx.clock["t"] += kf.STALL_STOP_S
    kf._stuck_watch(ctx, sig)
    assert ctx.state.safety_stopped
