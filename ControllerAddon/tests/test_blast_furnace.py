import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import blast_furnace as bf
import banker
import controller
import travel


# Facts pinned here come from the 2026-08-31 human recording
# (recordings/2026-08-31_000524_blast-furnace-gold.*): 27-ore gold trips with the spare glove
# pair holding the 28th slot, varbit 947 = 27 landing on the same tick as the Smithing XP drop
# (~9s after the conveyor click), Wear Ice gloves -> Take Bar dispenser -> Wear Goldsmith
# gauntlets on the way back to the bank.


def _item(name, qty=1):
    # "qty" is what GET /inventory actually emits (InventoryProvider.java) -- NOT "quantity".
    return {"name": name, "qty": qty, "screenX": 100, "screenY": 100}


class _State:
    def __init__(self):
        self.data = {}
        self.config = {}
        self.safety_stopped = False
        self.safety_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason


class _Ctx:
    def __init__(self):
        self.state = _State()
        self.ser = object()
        self.events = None


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Keep the suite hermetic (same posture as test_mahogany_homes.py): every controller call
    that would otherwise reach the running client or the Arduino serial port gets a harmless
    default. Tests that care about a specific read re-patch it themselves."""
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_objects", lambda: [])
    monkeypatch.setattr(controller, "get_varbit", lambda vid: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_state", lambda: {})
    monkeypatch.setattr(controller, "get_player", lambda: {"localX": 0, "localY": 0})
    # Fake clock: every wait in the routine runs on bf._now/_sleep (2026-09-04), so a budget
    # expires instantly here instead of really sleeping through it.
    clock = {"t": 1000.0}
    monkeypatch.setattr(bf, "_now", lambda: clock["t"])

    def _sleep(s):
        clock["t"] += s
    monkeypatch.setattr(bf, "_sleep", _sleep)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda **kw: True)
    monkeypatch.setattr(controller, "nudge_camera_yaw", lambda ser, key: None)
    # walk_to_dispenser_wait_tile delegates here; unstubbed it reaches the RUNNING CLIENT
    # (/player, /ground/walk) and the serial port -- caught 2026-08-31 by two run_trip tests
    # passing alone and failing in the full run while a live session was up.
    monkeypatch.setattr(travel, "walk_to_exact_tile",
                        lambda ctx, x, y, plane, wait_for_arrival=True: True)


def _equip_gloves(monkeypatch, name):
    monkeypatch.setattr(
        controller, "get_equipment",
        lambda: {"gloves": {"name": name} if name else None},
    )


# --- gold_glove_swap_active -----------------------------------------------------------

def test_swap_active_with_gauntlets_worn_and_ice_in_inventory(monkeypatch):
    _equip_gloves(monkeypatch, "Goldsmith gauntlets")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Ice gloves")])
    assert bf.gold_glove_swap_active({"barType": "gold"}) is True


def test_swap_active_with_ice_worn_and_gauntlets_in_inventory(monkeypatch):
    # Restart mid-cycle: the take just happened, gauntlets not yet re-worn.
    _equip_gloves(monkeypatch, "Ice gloves")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Goldsmith gauntlets")])
    assert bf.gold_glove_swap_active({"barType": "gold"}) is True


def test_swap_inactive_without_gauntlets(monkeypatch):
    # Only ice gloves owned -> the plain always-worn-ice method, unchanged behavior.
    _equip_gloves(monkeypatch, "Ice gloves")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Gold ore", 27)])
    assert bf.gold_glove_swap_active({"barType": "gold"}) is False


def test_swap_inactive_for_non_gold_bar_type(monkeypatch):
    _equip_gloves(monkeypatch, "Goldsmith gauntlets")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Ice gloves")])
    assert bf.gold_glove_swap_active({"barType": "iron"}) is False


# --- ensure_gloves_worn ---------------------------------------------------------------

def test_ensure_gloves_worn_noop_when_already_worn(monkeypatch):
    _equip_gloves(monkeypatch, "Ice gloves")
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or True)
    ctx = _Ctx()
    assert bf.ensure_gloves_worn(ctx, "Ice gloves") is True
    assert clicks == []


def test_ensure_gloves_worn_clicks_inventory_pair_until_worn(monkeypatch):
    worn = {"name": "Goldsmith gauntlets"}
    monkeypatch.setattr(controller, "get_equipment", lambda: {"gloves": dict(worn)})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Ice gloves")])
    clicks = []

    def click(ser, entity, **kw):
        clicks.append(entity["name"])
        worn["name"] = "Ice gloves"  # the wear registers
        return True

    monkeypatch.setattr(controller, "click_entity", click)
    ctx = _Ctx()
    assert bf.ensure_gloves_worn(ctx, "Ice gloves") is True
    assert clicks == ["Ice gloves"]


def test_ensure_gloves_worn_fails_when_pair_is_missing(monkeypatch):
    _equip_gloves(monkeypatch, "Goldsmith gauntlets")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Gold ore", 27)])
    ctx = _Ctx()
    assert bf.ensure_gloves_worn(ctx, "Ice gloves") is False


# --- wait_for_bars_ready --------------------------------------------------------------

def test_wait_for_bars_ready_returns_true_when_bars_appear(monkeypatch):
    # varbit 947 = 27 is the recording's "smelt done" signal (same tick as the XP drop).
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 27)
    assert bf.wait_for_bars_ready(947, timeout_s=1.0) is True


def test_wait_for_bars_ready_times_out_when_bars_never_appear(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    assert bf.wait_for_bars_ready(947, timeout_s=0.3) is False


def test_wait_for_bars_ready_best_effort_when_varbit_unreadable(monkeypatch):
    # None means "cannot verify" (un-rebuilt client), never "no bars" -- fall back to a blind
    # smelt-length wait rather than failing a working session.
    monkeypatch.setattr(controller, "get_varbit", lambda vid: None)
    assert bf.wait_for_bars_ready(947, timeout_s=0.3) is True


# --- collect_bars glove wrap ----------------------------------------------------------

def _dispenser():
    return {"name": "Bar dispenser", "id": 9092, "distance": 1, "visible": True,
            "screenX": 200, "screenY": 200}


def test_collect_bars_swap_wears_ice_then_collects_then_wears_gauntlets(monkeypatch):
    order = []
    varbit = {"value": 27}

    monkeypatch.setattr(controller, "get_varbit", lambda vid: varbit["value"])
    monkeypatch.setattr(controller, "get_objects", lambda: [_dispenser()])

    def click(ser, entity, **kw):
        order.append("take")
        varbit["value"] = 0  # the take empties the dispenser
        return True

    monkeypatch.setattr(controller, "click_entity", click)
    monkeypatch.setattr(bf, "ensure_gloves_worn",
                        lambda ctx, name: order.append(name) or True)

    ctx = _Ctx()
    assert bf.collect_bars(ctx, {"barType": "gold"}, glove_swap=True) is True
    assert order == ["Ice gloves", "take", "Goldsmith gauntlets"]


def test_collect_bars_swap_leaves_gloves_alone_when_nothing_ready(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    worn = []
    monkeypatch.setattr(bf, "ensure_gloves_worn",
                        lambda ctx, name: worn.append(name) or True)
    ctx = _Ctx()
    assert bf.collect_bars(ctx, {"barType": "gold"}, glove_swap=True) is True
    assert worn == []


def test_collect_bars_swap_safety_stops_when_ice_gloves_unwearable(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 27)
    monkeypatch.setattr(controller, "get_objects", lambda: [_dispenser()])
    monkeypatch.setattr(bf, "ensure_gloves_worn", lambda ctx, name: False)
    ctx = _Ctx()
    assert bf.collect_bars(ctx, {"barType": "gold"}, glove_swap=True) is False
    assert ctx.state.safety_stopped


# --- walk_to_dispenser_wait_tile ------------------------------------------------------

def test_walk_to_dispenser_wait_tile_is_a_non_blocking_exact_walk(monkeypatch):
    # (1940,4964) is the dispenser standing tile the human parks on during the smelt wait
    # (recording walk destinations 4 and 8; Take Bar dispenser fired from it at dist 1).
    calls = []
    monkeypatch.setattr(travel, "walk_to_exact_tile",
                        lambda ctx, x, y, plane, wait_for_arrival=True:
                        calls.append((x, y, plane, wait_for_arrival)) or True)
    bf.walk_to_dispenser_wait_tile(_Ctx())
    assert calls == [(1940, 4964, 0, False)]


# --- run_trip integration -------------------------------------------------------------

def _stub_bank(monkeypatch, recorded):
    monkeypatch.setattr(banker, "find_nearby_bank", lambda cfg: {"name": "Bank chest"})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj: True)
    monkeypatch.setattr(banker, "deposit_inventory",
                        lambda ctx, cfg: recorded.__setitem__("deposit_cfg", cfg) or True)
    monkeypatch.setattr(banker, "withdraw_all_shift", lambda ctx, name, fresh=False: True)
    monkeypatch.setattr(banker, "set_quantity_mode", lambda ctx, mode: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx: True)


def test_run_trip_gold_swap_keeps_both_glove_pairs_across_the_deposit(monkeypatch):
    recorded = {}
    _stub_bank(monkeypatch, recorded)
    monkeypatch.setattr(bf, "gold_glove_swap_active", lambda config: True)
    monkeypatch.setattr(bf, "ensure_gloves_worn", lambda ctx, name: True)
    monkeypatch.setattr(bf, "wait_for_bars_ready", lambda vid, **kw: True)
    monkeypatch.setattr(bf, "collect_bars", lambda ctx, config, glove_swap=False, **kw: True)
    monkeypatch.setattr(bf, "find_conveyor", lambda config: {"name": "Conveyor belt"})
    monkeypatch.setattr(bf, "deposit_via_conveyor", lambda ctx, config, names: 27)

    ctx = _Ctx()
    trip = bf.BAR_TYPES["gold"]["trips"][0]
    assert bf.run_trip(ctx, {"barType": "gold"}, trip, bar_uses_coal_bag=False) is True
    keep = [k.lower() for k in recorded["deposit_cfg"].get("keepItems", [])]
    assert "ice gloves" in keep
    assert "goldsmith gauntlets" in keep


def test_run_trip_gold_swap_waits_for_smelt_before_collecting(monkeypatch):
    recorded = {}
    order = []
    _stub_bank(monkeypatch, recorded)
    monkeypatch.setattr(bf, "gold_glove_swap_active", lambda config: True)
    monkeypatch.setattr(bf, "ensure_gloves_worn", lambda ctx, name: True)
    monkeypatch.setattr(bf, "wait_for_bars_ready",
                        lambda vid, **kw: order.append(("wait", vid)) or True)
    monkeypatch.setattr(bf, "walk_to_dispenser_wait_tile",
                        lambda ctx: order.append("walk"))
    monkeypatch.setattr(bf, "collect_bars",
                        lambda ctx, config, glove_swap=False, **kw: order.append(("collect", glove_swap)) or True)
    monkeypatch.setattr(bf, "find_conveyor", lambda config: {"name": "Conveyor belt"})
    monkeypatch.setattr(bf, "deposit_via_conveyor",
                        lambda ctx, config, names: order.append(("deposit", tuple(names))) or 27)

    ctx = _Ctx()
    trip = bf.BAR_TYPES["gold"]["trips"][0]
    assert bf.run_trip(ctx, {"barType": "gold"}, trip, bar_uses_coal_bag=False) is True
    # Pre-trip dispenser clearing, then the conveyor deposit, then the non-blocking walk to
    # the dispenser standing tile, then wait-for-947 (spent walking), then collect.
    assert order == [
        ("collect", True),
        ("deposit", ("Gold ore",)),
        "walk",
        ("wait", bf.BAR_VARBITS["gold"]),
        ("collect", True),
    ]


def test_run_trip_gold_swap_safety_stops_when_bars_never_smelt(monkeypatch):
    recorded = {}
    _stub_bank(monkeypatch, recorded)
    monkeypatch.setattr(bf, "gold_glove_swap_active", lambda config: True)
    monkeypatch.setattr(bf, "ensure_gloves_worn", lambda ctx, name: True)
    monkeypatch.setattr(bf, "wait_for_bars_ready", lambda vid, **kw: False)
    monkeypatch.setattr(bf, "collect_bars", lambda ctx, config, glove_swap=False, **kw: True)
    monkeypatch.setattr(bf, "find_conveyor", lambda config: {"name": "Conveyor belt"})
    monkeypatch.setattr(bf, "deposit_via_conveyor", lambda ctx, config, names: 27)

    ctx = _Ctx()
    trip = bf.BAR_TYPES["gold"]["trips"][0]
    assert bf.run_trip(ctx, {"barType": "gold"}, trip, bar_uses_coal_bag=False) is False
    assert ctx.state.safety_stopped


def test_run_trip_non_swap_gold_is_unchanged(monkeypatch):
    # Without the gauntlets: no glove swap anywhere, collect called with glove_swap False.
    recorded = {}
    order = []
    _stub_bank(monkeypatch, recorded)
    monkeypatch.setattr(bf, "gold_glove_swap_active", lambda config: False)
    monkeypatch.setattr(bf, "wait_for_bars_ready",
                        lambda vid, **kw: order.append("wait") or True)
    monkeypatch.setattr(bf, "collect_bars",
                        lambda ctx, config, glove_swap=False, **kw: order.append(("collect", glove_swap)) or True)
    monkeypatch.setattr(bf, "find_conveyor", lambda config: {"name": "Conveyor belt"})
    monkeypatch.setattr(bf, "deposit_via_conveyor", lambda ctx, config, names: 28)

    ctx = _Ctx()
    trip = bf.BAR_TYPES["gold"]["trips"][0]
    assert bf.run_trip(ctx, {"barType": "gold"}, trip, bar_uses_coal_bag=False) is True
    # Since 2026-09-04 EVERY bar type waits for the bars and collects in the same trip.
    assert order == [("collect", False), "wait", ("collect", False)]
    # The glove names are kept on EVERY gold trip, swap or not (live 2026-09-04: a missed
    # swap detection let the deposit-all bank the carried Goldsmith gauntlets).
    keep = [k.lower() for k in recorded["deposit_cfg"].get("keepItems", [])]
    assert "goldsmith gauntlets" in keep and "smiths gloves (i)" in keep


# --- Smiths gloves (i) as the ice pair (2026-09-04) -----------------------------------
# LIVE the same day: the account's ice pair is Smiths gloves (i); the swap read inactive, the
# gauntlets were left off keepItems, and the bank deposit-all took them.

def test_swap_active_with_smiths_gloves_i_as_the_ice_pair(monkeypatch):
    _equip_gloves(monkeypatch, "Goldsmith gauntlets")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Smiths gloves (i)")])
    assert bf.gold_glove_swap_active({"barType": "gold"}) is True
    _equip_gloves(monkeypatch, "Smiths gloves (i)")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Goldsmith gauntlets")])
    assert bf.gold_glove_swap_active({"barType": "gold"}) is True


def test_ice_glove_pair_resolves_to_the_pair_the_account_has(monkeypatch):
    _equip_gloves(monkeypatch, "Goldsmith gauntlets")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Smiths gloves (i)")])
    assert bf.ice_glove_pair() == "Smiths gloves (i)"
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Ice gloves")])
    assert bf.ice_glove_pair() == "Ice gloves"
    _equip_gloves(monkeypatch, "Smiths gloves (i)")
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    assert bf.ice_glove_pair() == "Smiths gloves (i)"
    _equip_gloves(monkeypatch, None)
    assert bf.ice_glove_pair() == "Ice gloves"          # nothing visible: a sensible default


def test_collect_bars_swap_wears_the_smiths_pair_when_that_is_the_ice_pair(monkeypatch):
    order = []
    varbit = {"value": 27}
    monkeypatch.setattr(controller, "get_varbit", lambda vid: varbit["value"])
    monkeypatch.setattr(controller, "get_objects", lambda: [_dispenser()])
    _equip_gloves(monkeypatch, "Goldsmith gauntlets")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Smiths gloves (i)")])

    def click(ser, entity, **kw):
        order.append("take")
        varbit["value"] = 0
        return True
    monkeypatch.setattr(controller, "click_entity", click)
    monkeypatch.setattr(bf, "ensure_gloves_worn", lambda ctx, name: order.append(name) or True)
    assert bf.collect_bars(_Ctx(), {"barType": "gold"}, glove_swap=True) is True
    assert order == ["Smiths gloves (i)", "take", "Goldsmith gauntlets"]


# --- event-driven outcomes (2026-09-04) ------------------------------------------------

def _belt(obj_id, x, y, actions, dist=1):
    return {"name": "Conveyor belt", "id": obj_id, "x": x, "y": y, "distance": dist,
            "visible": True, "actions": actions, "screenX": 100, "screenY": 100}


def test_find_conveyor_prefers_the_tile_that_offers_put_ore_on(monkeypatch):
    # LIVE bf_live3: the nearest+north heuristic picked a 9101 tile with no actions; six
    # clicks became "Walk here" and the trip safety-stopped with 0/27 deposited.
    monkeypatch.setattr(controller, "get_objects", lambda: [
        _belt(9101, 1943, 4965, [], dist=1), _belt(9101, 1943, 4966, [], dist=1),
        _belt(9100, 1943, 4967, ["Put-ore-on"], dist=2)])
    assert bf.find_conveyor({})["id"] == 9100


def test_find_conveyor_falls_back_to_north_of_nearest_without_actions(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda: [
        _belt(9101, 1943, 4965, None, dist=1), _belt(9101, 1943, 4966, None, dist=2)])
    assert bf.find_conveyor({})["y"] == 4966


def test_deposit_via_conveyor_returns_when_the_ore_is_gone(monkeypatch):
    inv = {"n": 27}
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Gold ore")] * inv["n"])
    monkeypatch.setattr(controller, "get_objects",
                        lambda: [_belt(9100, 1943, 4967, ["Put-ore-on"])])
    clicks = []

    def click(ser, entity, **kw):
        clicks.append(entity["id"])
        inv["n"] = 0          # the single Put-ore-on click takes everything
        return True
    monkeypatch.setattr(controller, "click_entity", click)
    assert bf.deposit_via_conveyor(_Ctx(), {}, ["Gold ore"]) == 27
    assert clicks == [9100]


def test_deposit_via_conveyor_reclicks_only_after_a_real_stall(monkeypatch):
    inv = {"n": 27}
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Gold ore")] * inv["n"])
    monkeypatch.setattr(controller, "get_objects",
                        lambda: [_belt(9100, 1943, 4967, ["Put-ore-on"])])
    clicks = []

    def click(ser, entity, **kw):
        clicks.append(entity["id"])
        if len(clicks) == 2:
            inv["n"] = 0      # the reclick works
        return True
    monkeypatch.setattr(controller, "click_entity", click)
    assert bf.deposit_via_conveyor(_Ctx(), {}, ["Gold ore"], timeout_s=30.0) == 27
    assert clicks == [9100, 9100]


def test_collect_bars_spaces_the_dialogue_when_it_opens(monkeypatch):
    varbit = {"value": 27}
    monkeypatch.setattr(controller, "get_varbit", lambda vid: varbit["value"])
    monkeypatch.setattr(controller, "get_objects", lambda: [_dispenser()])
    monkeypatch.setattr(controller, "get_state", lambda: {"dialogOpen": True})
    order = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: order.append("take") or True)

    def tap(ser, key):
        order.append(key)
        varbit["value"] = 0
    monkeypatch.setattr(controller, "key_tap", tap)
    monkeypatch.setattr(controller, "wait_until_player_idle",
                        lambda **kw: order.append("idle-wait") or True)
    assert bf.collect_bars(_Ctx(), {"barType": "gold"}) is True
    assert order == ["take", "SPACE"]          # no settle/idle fallback needed


def test_wait_for_bars_ready_is_immediate_when_already_ready(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 27)
    assert bf.wait_for_bars_ready(947, timeout_s=25.0) is True
    assert bf._now() == 1000.0                 # no wait spent at all


def test_run_trip_gold_swap_wears_gauntlets_on_the_run_to_the_bank(monkeypatch):
    # USER 2026-09-04: click the bank to start running, wear the gauntlets mid-run, and let
    # open_bank's own click re-queue the open on arrival.
    recorded = {}
    order = []
    _stub_bank(monkeypatch, recorded)
    monkeypatch.setattr(banker, "open_bank", lambda ctx, obj: order.append("open_bank") or True)
    monkeypatch.setattr(banker, "find_nearby_bank",
                        lambda cfg: {"name": "Bank chest", "id": 26707, "x": 1, "y": 2, "plane": 0})
    _equip_gloves(monkeypatch, "Smiths gloves (i)")
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: order.append(("click", e.get("name"))) or True)
    monkeypatch.setattr(bf, "gold_glove_swap_active", lambda config: True)
    monkeypatch.setattr(bf, "ensure_gloves_worn", lambda ctx, name: order.append(("wear", name)) or True)
    monkeypatch.setattr(bf, "wait_for_bars_ready", lambda vid, **kw: True)
    collects = []
    monkeypatch.setattr(bf, "collect_bars",
                        lambda ctx, config, glove_swap=False, swap_back=True, **kw: collects.append(swap_back) or True)
    monkeypatch.setattr(bf, "find_conveyor", lambda config: {"name": "Conveyor belt"})
    monkeypatch.setattr(bf, "deposit_via_conveyor", lambda ctx, config, names: 27)
    ctx = _Ctx()
    trip = bf.BAR_TYPES["gold"]["trips"][0]
    assert bf.run_trip(ctx, {"barType": "gold"}, trip, bar_uses_coal_bag=False) is True
    assert collects == [False, False]              # collect_bars never swaps back itself
    assert order[:3] == [("click", "Bank chest"), ("wear", "Goldsmith gauntlets"), "open_bank"]


def test_swap_back_on_the_run_is_a_noop_when_already_worn(monkeypatch):
    _equip_gloves(monkeypatch, "Goldsmith gauntlets")
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicks.append(e) or True)
    bf._swap_back_on_the_run(_Ctx(), {"name": "Bank chest", "id": 1, "x": 1, "y": 1})
    assert clicks == []


def test_collect_bars_prehovers_the_bank_chest_while_the_bars_land(monkeypatch):
    varbit = {"value": 27}
    monkeypatch.setattr(controller, "get_varbit", lambda vid: varbit["value"])
    monkeypatch.setattr(controller, "get_objects", lambda: [_dispenser()])
    monkeypatch.setattr(controller, "get_state", lambda: {"dialogOpen": True})
    monkeypatch.setattr(banker, "find_nearby_bank",
                        lambda cfg: {"name": "Bank chest", "id": 26707, "x": 1, "y": 2, "screenX": 5, "screenY": 5})
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: None)
    order = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: order.append("take") or True)
    monkeypatch.setattr(controller, "hover_entity", lambda ser, e, **kw: order.append(("hover", e["name"])) or True)

    def tap(ser, key):
        order.append(key)
        varbit["value"] = 0
    monkeypatch.setattr(controller, "key_tap", tap)
    assert bf.collect_bars(_Ctx(), {"barType": "gold"}, prehover_bank=True) is True
    assert order == ["take", "SPACE", ("hover", "Bank chest")]


def test_ensure_gloves_worn_never_clicks_an_item_while_the_bank_is_open(monkeypatch):
    # bf_live10: a wear retry can land after the bank opened; a left-click on an inventory
    # item inside the bank interface deposits it.
    _equip_gloves(monkeypatch, "Smiths gloves (i)")
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Goldsmith gauntlets")])
    monkeypatch.setattr(banker, "is_open", lambda: True)
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicks.append(e) or True)
    assert bf.ensure_gloves_worn(_Ctx(), "Goldsmith gauntlets") is False
    assert clicks == []


def test_ensure_gloves_worn_never_simulates_a_misclick(monkeypatch):
    # bf_live11: a simulated miss + its corrective reclick wore the pair twice (swapped back).
    worn = {"name": "Goldsmith gauntlets"}
    monkeypatch.setattr(controller, "get_equipment", lambda: {"gloves": dict(worn)})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Smiths gloves (i)")])
    kwargs = []

    def click(ser, entity, **kw):
        kwargs.append(kw)
        worn["name"] = "Smiths gloves (i)"
        return True
    monkeypatch.setattr(controller, "click_entity", click)
    assert bf.ensure_gloves_worn(_Ctx(), "Smiths gloves (i)") is True
    assert kwargs and kwargs[0].get("simulate_mistakes") is False


def test_run_trip_non_gold_wears_the_carried_ice_pair_first(monkeypatch):
    # After a gold session the gauntlets are on and Smiths gloves (i) are carried; every
    # other bar type takes hot bars with the ice pair worn for the whole session.
    recorded = {}
    _stub_bank(monkeypatch, recorded)
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Smiths gloves (i)")])
    worn = []
    monkeypatch.setattr(bf, "ensure_gloves_worn", lambda ctx, name: worn.append(name) or True)
    monkeypatch.setattr(bf, "collect_bars", lambda ctx, config, glove_swap=False, **kw: True)
    monkeypatch.setattr(bf, "find_conveyor", lambda config: {"name": "Conveyor belt"})
    monkeypatch.setattr(bf, "deposit_via_conveyor", lambda ctx, config, names: 28)
    trip = bf.BAR_TYPES["iron"]["trips"][0]
    assert bf.run_trip(_Ctx(), {"barType": "iron"}, trip, bar_uses_coal_bag=False) is True
    assert worn == ["Smiths gloves (i)"]
