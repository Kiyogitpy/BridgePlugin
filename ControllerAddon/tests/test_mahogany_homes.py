import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import time

import pytest

import mahogany_homes
import mahogany_homes as mh
import controller
import banker
import travel
import worldmap


def _hotspot(varb, object_id, value, needs_attention, material="PLANK", quantity=2):
    # material/quantity mirror MahoganyHomesProvider's per-slot HotspotType fields, which
    # work_phase reads to price the ONE hotspot it is about to click.
    return {"varb": varb, "objectId": object_id, "value": value,
            "needsAttention": needs_attention, "material": material, "quantity": quantity}


def _object(obj_id, distance=1, x=0, y=0, plane=0):
    return {"id": obj_id, "distance": distance, "x": x, "y": y, "plane": plane,
            "screenX": 100, "screenY": 100}


def _item(name, quantity=1):
    # "qty" is what GET /inventory actually emits (InventoryProvider.java) -- NOT "quantity".
    return {"name": name, "qty": quantity}


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Keep the suite hermetic. These reach the RUNNING CLIENT over HTTP when a test does not
    stub them, so results depended on where the character happened to be standing -- MEASURED
    2026-08-29 in the travel suite, where the same door test passed alone and failed in a full
    run. The defaults are the documented "client cannot tell" answers, which every caller must
    already handle.

    `get_player` is the third instance of this class this session (after two in the travel
    suite): work_phase calls `controller.get_player()` directly (for the reachability flood
    fill), unstubbed by test_work_phase_respects_click_cooldown, so with the client closed the
    call resolves via the bridge's own failure path (api_get's 0.3s timeout + swallowed
    exception) instead of returning instantly -- enough real wall-clock time for the cooldown
    window under test to expire before the click gate is even reached, so the "no click yet"
    assertion failed on a click that should have been suppressed. Verified with `requests`
    stubbed to record every URL hit across the whole suite: only GET /player was ever reached,
    and zero calls remain once this default is in place. `{}` matches get_player's own
    bridge-down return value (`api_get(...) or {}`), so every consumer already treats it as
    "cannot tell" -- same contract as the get_collision default above.

    `request_shortest_path`/`get_shortest_path_result` are gone from this fixture as of the
    global-collision-pathfinding migration (Task 5 deleted them from controller.py entirely);
    reachability now comes from `worldmap.reachable_from`, which reads no HTTP at all -- it reads
    the real, bundled world collision map straight off disk, keyed by real game coordinates. That
    makes it hermetic in the no-HTTP sense but NOT in the "independent of real map data" sense: a
    test using a synthetic placeholder tile (most fixtures here use (0,0), which is real open
    ocean and reads as reachable-from-everywhere under "no data defaults to passable") gets a real
    answer for a fake coordinate. `{}` here is the same "cannot tell" default `get_collision`
    already returns -- and is what every one of these tests got for free from the old
    `travel.reachable_set` + stubbed `get_collision` combination before this migration, so this
    stub reproduces prior behaviour exactly rather than changing it. Tests that care about a real
    reach answer patch `worldmap.reachable_from` (or `travel.can_reach` directly) themselves."""
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: [])
    monkeypatch.setattr(controller, "get_player", lambda: {})
    monkeypatch.setattr(worldmap, "reachable_from", lambda x, y, plane, **kw: {})

    # `get_mahogany_homes_state` joined this list when work_phase's hotspot click became a BURST:
    # _hotspot_progressed re-reads it after every click to see whether the varbit has moved, so
    # an unstubbed test reached the RUNNING CLIENT once per click and got whatever house the
    # character happened to be standing in. CAUGHT 2026-08-30 by three pre-existing tests going
    # red -- the live answer made _hotspot_progressed report progress, which reset
    # mh_hotspot_clicks and stopped the unreachable escalation ever being reached. `{}` yields no
    # hotspots, so the honest "no progress observed" default; tests about the burst patch it.
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {})

    # Burst repeats are in-place clicks, which would otherwise reach the serial port.
    monkeypatch.setattr(controller, "click_human", lambda ser: True)

    # The hotspot burst sleeps the human inter-click gap between clicks, so an unstubbed test
    # that reaches the click path pays up to HOTSPOT_SPAM_MAX_CLICKS x HOTSPOT_SPAM_GAP_MAX_S of
    # real wall clock. Sixteen tests here already stubbed this individually; defaulting it keeps
    # the suite at seconds rather than minutes. Nothing in this file asserts on delay timing --
    # the cooldown tests drive `mh_last_hotspot_click` directly instead.
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


@pytest.fixture(autouse=True)
def _stub_widgets_dump(monkeypatch):
    """tick() now calls _accept_tea_if_offered unconditionally, which reads /widgets/dump. Stub
    it out by default so no test accidentally makes a real (bridge-less) HTTP call; tests that
    care about the tea dialogue re-patch it themselves."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [])


# --- tier_number ---------------------------------------------------------------------

def test_tier_number_recognizes_all_four_tiers():
    assert mh.tier_number("beginner") == 1
    assert mh.tier_number("Novice") == 2
    assert mh.tier_number("ADEPT") == 3
    assert mh.tier_number("expert") == 4


def test_tier_number_returns_zero_for_unknown():
    assert mh.tier_number("bogus") == 0


# --- pick_next_hotspot ----------------------------------------------------------------

def test_pick_next_hotspot_returns_first_needing_attention():
    hotspots = [
        _hotspot(10554, 39989, 0, False),
        _hotspot(10555, 39990, 3, True),
        _hotspot(10556, 39991, 3, True),
    ]
    assert mh.pick_next_hotspot(hotspots)["objectId"] == 39990


def test_pick_next_hotspot_returns_none_when_all_done():
    hotspots = [_hotspot(10554, 39989, 0, False), _hotspot(10555, 39990, 0, False)]
    assert mh.pick_next_hotspot(hotspots) is None


def test_pick_next_hotspot_returns_none_for_empty_list():
    assert mh.pick_next_hotspot([]) is None


# --- find_object_by_id ------------------------------------------------------------------

def test_find_object_by_id_matches_exact_id():
    objects = [_object(1111, distance=5), _object(39990, distance=3)]
    found = mh.find_object_by_id(objects, 39990)
    assert found is not None and found["id"] == 39990


def test_find_object_by_id_picks_nearest_when_duplicated():
    objects = [_object(39990, distance=9), _object(39990, distance=2)]
    assert mh.find_object_by_id(objects, 39990)["distance"] == 2


def test_find_object_by_id_returns_none_when_absent():
    assert mh.find_object_by_id([_object(1111)], 39990) is None


# --- deficit --------------------------------------------------------------------------

def test_deficit_positive_when_short():
    assert mh.deficit(5, 10) == 5


def test_deficit_zero_when_sufficient():
    assert mh.deficit(10, 5) == 0


def test_deficit_zero_when_exact():
    assert mh.deficit(5, 5) == 0


# --- count_item -------------------------------------------------------------------------

def test_count_item_sums_matching_quantities_case_insensitively():
    inventory = [_item("Teak plank", 1), _item("teak plank", 1), _item("Steel bar", 5)]
    assert mh.count_item(inventory, "Teak plank") == 2


def test_count_item_uses_qty_field_when_present():
    # GET /inventory emits stack sizes under "qty" (InventoryProvider.java), not "quantity" --
    # reading the wrong key silently reported 1 of any stack.
    inventory = [{"name": "Astral rune", "qty": 300}]
    assert mh.count_item(inventory, "Astral rune") == 300


def test_count_item_ignores_a_quantity_key_the_bridge_never_sends():
    # Defensive: a dict carrying only "quantity" is not something /inventory produces, and must
    # not be silently trusted as a stack size.
    assert mh.count_item([{"name": "Astral rune", "quantity": 300}], "Astral rune") == 1


def test_count_item_returns_zero_when_absent():
    assert mh.count_item([_item("Hammer")], "Teak plank") == 0


# --- rune_pouch_counts ------------------------------------------------------------------

def test_rune_pouch_counts_builds_lowercase_name_map():
    pouch = {"runes": [{"slot": 0, "type": 1, "quantity": 500, "name": "Astral rune"},
                        {"slot": 1, "type": 2, "quantity": 300, "name": "Cosmic rune"}]}
    counts = mh.rune_pouch_counts(pouch)
    assert counts == {"astral rune": 500, "cosmic rune": 300}


def test_rune_pouch_counts_skips_unresolved_slots():
    pouch = {"runes": [{"slot": 0, "type": 0, "quantity": 0}]}
    assert mh.rune_pouch_counts(pouch) == {}


def test_rune_pouch_counts_handles_empty_pouch():
    assert mh.rune_pouch_counts({"runes": []}) == {}


# --- needs_bank_trip --------------------------------------------------------------------

class _State:
    def __init__(self):
        self.data = {}
        self.config = mh.DEFAULT_CONFIG
        self.safety_stopped = False
        self.safety_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = _State()


def test_needs_bank_trip_true_when_planks_low(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 2)])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    ctx = _Ctx()
    state = {"hasContract": False}
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, state) is True


def test_needs_bank_trip_true_when_a_required_rune_low(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], mh.DEFAULT_CONFIG["restock"]["targetPlanks"]),
        _item("Steel bar", mh.DEFAULT_CONFIG["restock"]["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8),
        _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 5},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    ctx = _Ctx()
    state = {"hasContract": False}
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, state) is True


def test_needs_bank_trip_false_when_everything_sufficient(monkeypatch):
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8),
        _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    ctx = _Ctx()
    state = {"hasContract": False}
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, state) is False


def test_needs_bank_trip_counts_loose_runes_not_just_the_pouch(monkeypatch):
    # bank_phase withdraws LOOSE runes (it builds no pouch-filling machinery), so a pouch-only
    # check would force a bank trip on a player already carrying hundreds in hand.
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 3), _item("Ardougne teleport", 3), _item("Falador teleport", 3),
        _item("Astral rune", 500), _item("Cosmic rune", 500),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})

    ctx = _Ctx()
    assert mh.needs_bank_trip(ctx, mh.DEFAULT_CONFIG, {"hasContract": False}) is False


def test_available_runes_sums_inventory_and_pouch():
    inventory = [_item("Astral rune", 120)]
    pouch = {"astral rune": 80}
    assert mh.available_runes(inventory, pouch, "Astral rune") == 200


# --- bank_phase -----------------------------------------------------------------------

def test_bank_phase_breaks_home_teleport_when_no_bank_and_no_teleport_leaves_safety_stopped(monkeypatch):
    monkeypatch.setattr(banker, "find_nearby_bank", lambda config: None)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})
    # No bank in range and no home teleport tablet in inventory -- genuine dead end.
    assert ctx.state.safety_stopped is True


def _mock_bank(monkeypatch, inventory=None, pouch=None, restocked=True):
    """Common bank_phase harness: an open bank, recording stubs for every banker call, and
    control over whether the post-trip needs_bank_trip re-check reports success."""
    record = {"withdrawn": [], "shift_withdrawn": [], "deposit": None}

    monkeypatch.setattr(banker, "find_nearby_bank", lambda config: {"id": 1, "name": "Bank booth"})
    monkeypatch.setattr(banker, "open_bank", lambda ctx, bank_obj: True)
    monkeypatch.setattr(banker, "close_bank", lambda ctx: None)
    monkeypatch.setattr(banker, "deposit_inventory",
                        lambda ctx, config: record.__setitem__("deposit", config))
    monkeypatch.setattr(banker, "withdraw_loadout",
                        lambda ctx, config, fresh=False: record["withdrawn"].append(config["withdraw"][0]))
    monkeypatch.setattr(banker, "withdraw_all_shift",
                        lambda ctx, name, fresh=False: record["shift_withdrawn"].append(name))
    monkeypatch.setattr(controller, "get_inventory", lambda: list(inventory or []))
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: pouch or {"runes": []})
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(mh, "needs_bank_trip", lambda ctx, config, state: not restocked)
    return record


def test_bank_phase_withdraws_up_to_target_and_advances_phase(monkeypatch):
    record = _mock_bank(monkeypatch)

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    names = {w["name"] for w in record["withdrawn"]}
    assert mh.DEFAULT_CONFIG["plankName"] in names
    assert "Steel bar" in names
    assert ctx.state.data["mh_phase"] == "travel"


def test_bank_phase_deposits_before_withdrawing_and_keeps_the_loadout(monkeypatch):
    record = _mock_bank(monkeypatch)

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert record["deposit"] is not None, "bank_phase must deposit before restocking"
    keep = [k.lower() for k in record["deposit"]["keepItems"]]
    for required in ["hammer", "saw", "rune pouch", "xeric's talisman",
                     "varrock teleport", "steel bar",
                     mh.DEFAULT_CONFIG["plankName"].lower(), "astral rune"]:
        assert required in keep, f"{required} must survive the deposit"
    # The rune pouch is a keepItem precisely because we want its contents -- the generic
    # "empty every container into the bank" step must not run and undo that.
    assert record["deposit"]["skipContainerDeposit"] is True


def test_bank_phase_shift_withdraws_stacking_runes(monkeypatch):
    # Runes must NOT go through withdraw_loadout's per-unit click loop -- 300 astral runes would
    # be 300 clicks. One shift-click takes the whole stack into a single slot.
    record = _mock_bank(monkeypatch)

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert set(record["shift_withdrawn"]) == {"astral rune", "cosmic rune"}
    assert not any(w["name"] in ("astral rune", "cosmic rune") for w in record["withdrawn"])


def test_bank_phase_skips_runes_already_covered_by_the_pouch(monkeypatch):
    record = _mock_bank(monkeypatch, pouch={"runes": [
        {"name": "Astral rune", "quantity": 1000},
        {"name": "Cosmic rune", "quantity": 1000},
    ]})

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert record["shift_withdrawn"] == []


def test_bank_phase_counts_a_failed_restock_and_stays_in_bank(monkeypatch):
    _mock_bank(monkeypatch, restocked=False)

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert ctx.state.data["mh_bank_stuck"] == 1
    assert ctx.state.data["mh_phase"] == "bank"   # must NOT advance on a failed restock
    assert ctx.state.safety_stopped is False


def test_bank_phase_safety_stops_after_repeated_failed_restocks(monkeypatch):
    _mock_bank(monkeypatch, restocked=False)

    ctx = _Ctx()
    ctx.state.data["mh_bank_stuck"] = mh.STUCK_TICK_LIMIT - 1
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert ctx.state.safety_stopped is True
    assert "bank" in ctx.state.safety_reason.lower()


def test_bank_phase_releases_the_teleported_latch_so_travel_can_teleport_back(monkeypatch):
    # A bank trip ends somewhere else entirely, so travel_phase must be allowed to teleport
    # again -- otherwise it would try to WALK from the bank back to the house.
    _mock_bank(monkeypatch)

    ctx = _Ctx()
    ctx.state.data["mh_teleported_this_contract"] = True
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert ctx.state.data["mh_teleported_this_contract"] is False
    assert ctx.state.data["mh_phase"] == "travel"


def test_bank_phase_resets_the_stuck_counter_on_a_good_trip(monkeypatch):
    _mock_bank(monkeypatch)

    ctx = _Ctx()
    ctx.state.data["mh_bank_stuck"] = 7
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})

    assert ctx.state.data["mh_bank_stuck"] == 0


# --- request_contract_phase -----------------------------------------------------------

def test_request_contract_phase_skips_when_already_has_contract():
    ctx = _Ctx()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})
    assert "mh_contact_cast_at" not in ctx.state.data


def test_request_contract_phase_casts_npc_contact_when_no_contract(monkeypatch):
    clicked = {}

    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget",
                         lambda group, child, index=None: {"clickable": True, "screenX": 1, "screenY": 2})

    def fake_click(ser, entity, **kw):
        clicked["entity"] = entity
        return True

    monkeypatch.setattr(controller, "click_entity", fake_click)

    ctx = _Ctx()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert clicked["entity"] == {"clickable": True, "screenX": 1, "screenY": 2}
    assert "mh_contact_cast_at" in ctx.state.data


def test_request_contract_phase_respects_cooldown(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: calls.append(key))

    ctx = _Ctx()
    ctx.state.data["mh_contact_cast_at"] = time.time()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert calls == []  # still on cooldown -- must not re-cast


def test_request_contract_phase_counts_an_unclickable_widget(monkeypatch):
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": False})

    ctx = _Ctx()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert ctx.state.data["mh_contact_stuck"] == 1
    assert ctx.state.safety_stopped is False


def test_request_contract_phase_safety_stops_when_widget_stays_unclickable(monkeypatch):
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget", lambda group, child, index=None: None)

    ctx = _Ctx()
    ctx.state.data["mh_contact_stuck"] = mh.STUCK_TICK_LIMIT - 1
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert ctx.state.safety_stopped is True
    assert "npc contact" in ctx.state.safety_reason.lower()


def test_request_contract_phase_resets_stuck_counter_once_clickable(monkeypatch):
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": True, "screenX": 1, "screenY": 2})
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_contact_stuck"] = 9
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert ctx.state.data["mh_contact_stuck"] == 0


# MEASURED LIVE 2026-08-29, twice in one session: work_phase's "hotspot is visible but not
# reachable" branch is a SILENT INFINITE LOOP. It logs nothing itself; open_door_toward logs only
# when it finds a door; and its walk goal is the home's standing tile, which -- once we are inside
# the house -- is already within HOUSE_ARRIVAL_TOLERANCE, so walk_to returns True immediately
# without clicking. No counter advances either: mh_work_stuck was just reset (the object IS
# visible) and mh_hotspot_clicks is never touched on this path.
#
# Result: 79 ticks of ZERO output the first time, 46s the second, with the routine ticking
# happily and doing absolutely nothing. USER: "another deadlock doing nothing".
#
# The underlying false negative is fixed in travel.can_reach (multi-tile objects), but this
# branch must not be ABLE to spin silently whatever can_reach answers -- that is what turned a
# wrong answer into a frozen routine instead of a visible failure.

def test_work_phase_cannot_spin_silently_when_a_hotspot_reads_unreachable(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1766, "y": 3620, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(mh, "plank_sack_count", lambda *a, **kw: 0)
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_object(40002, distance=3, x=1769, y=3622, plane=0)])
    monkeypatch.setattr(travel, "reachable_set", lambda *a, **kw: {(1766, 3620)})
    monkeypatch.setattr(travel, "can_reach", lambda *a, **kw: False)
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: None)
    # The walk is a no-op: the goal is already within tolerance. This is the live condition.
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, *a, **kw: True)
    monkeypatch.setattr(controller, "click_entity", lambda *a, **kw: True)

    ctx = _Ctx()
    state = {
        "homeName": "MARIAH",
        "standingLocation": {"x": 1766, "y": 3621, "plane": 0},
        "area": {"x": 1760, "y": 3616, "width": 14, "height": 12, "plane": 0},
        "hotspots": [_hotspot(10554, 40002, 3, True, quantity=3)],
        "requiredMaterials": {"planks": 3, "steelBars": 0},
    }
    state["hotspots"][0]["location"] = {"x": 1769, "y": 3622, "plane": 0}

    for _ in range(mh.STUCK_TICK_LIMIT + 2):
        mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.safety_stopped, (
        "work_phase span on an unreachable hotspot forever, logging nothing and clicking nothing"
    )


# MEASURED LIVE 2026-08-29 at TAU's: bank_phase discarded travel.walk_to's return value. The walk
# to the Falador bank died at TAU's diagonal door ("route makes no further progress"), and this
# then tried to open a bank booth THIRTY-FIVE tiles away regardless -- 20s of wait_until_player_idle
# retries, then "failed to open the bank", a safety stop naming the wrong cause entirely.
#
# walk_to's own docstring says it plainly: "False now means something". A caller that ignores it
# converts a walking failure into a misattributed downstream one.

def test_bank_phase_does_not_try_to_open_a_bank_it_failed_to_walk_to(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3047, "y": 3347, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(banker, "find_nearby_bank",
                        lambda cfg: {"x": 3013, "y": 3354, "plane": 0, "distance": 35})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, *a, **kw: False)
    monkeypatch.setattr(banker, "open_bank",
                        lambda ctx, obj: pytest.fail("must not reach for a bank 35 tiles away"))

    ctx = _Ctx()
    mh.bank_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert not ctx.state.safety_stopped or "bank" not in (ctx.state.safety_reason or "").lower()         or "walk" in (ctx.state.safety_reason or "").lower(),         "a failed WALK must not be reported as a failure to OPEN the bank"


# MEASURED from the user's OWN recordings 2026-08-29 (4 human sessions, n=1194 hotspot actions):
# the human SPAM-CLICKS. Median gap between hotspot actions 0.20s, p25 0.18s; 339 of 500 bursts
# are more than one click on the same option, mean burst 2.46, max 8. Remove->Build median 2.06s.
# The bot on the same activity that night: median gap 3.89s, Remove->Build median 3.62s -- about
# 1.5s of pure latency per pair, because the gate was a FIXED wall-clock cooldown (0.6s remove /
# 2.4s build + 0.7s per tile) that always paid the worst case even when the action had finished.
#
# So the gate becomes a SIGNAL instead of a timer: click, and keep clicking at the human's own
# cadence until the hotspot's varbit actually moves. This is both faster AND closer to the
# recorded human, which is the rare case where those pull the same way.
#
# The engine loop is ~1.1s, so the burst has to happen INSIDE one work_phase call -- successive
# engine ticks physically cannot produce a 0.2s cadence.

def _burst_ctx_and_state(hot_value=3):
    ctx = _Ctx()
    state = {
        "homeName": "MARIAH",
        "standingLocation": {"x": 1766, "y": 3621, "plane": 0},
        "area": {"x": 1760, "y": 3616, "width": 14, "height": 12, "plane": 0},
        "hotspots": [_hotspot(10554, 40002, hot_value, True, quantity=1)],
        "requiredMaterials": {"planks": 1, "steelBars": 0},
    }
    state["hotspots"][0]["location"] = {"x": 1767, "y": 3620, "plane": 0}
    return ctx, state


def test_the_burst_carries_straight_on_into_the_next_action(monkeypatch):
    """USER 2026-08-30: "keep clicking after we remove so we start the next process".

    A Remove completing is not the end of the work at this hotspot -- the object becomes
    "<thing> space" (SAME object id, stable across the whole Remove-then-Build lifecycle) and now
    offers Build. Breaking the burst on the varbit move hands control back to the engine loop,
    which is ~1.1s, and that is pure dead time between two halves of one job. The human just
    keeps clicking: their Remove->Build median is 2.06s against our 3.62s.

    The object MODEL changes across the transition, so the click after a state change must
    re-aim rather than repeat in place -- same reason a moving player must."""
    aimed, inplace = [], []
    _stub_for_burst_split(monkeypatch, aimed, inplace)
    ctx, state = _burst_ctx_and_state()
    monkeypatch.setattr(mh, "_player_is_moving", lambda prev: False)

    # value 3 -> 5 (remove lands) -> 7 (build lands, nothing left to do)
    seq = iter([3, 3, 5, 5, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7])
    monkeypatch.setattr(
        controller, "get_mahogany_homes_state",
        lambda: {"hotspots": [{"objectId": 40002, "value": next(seq, 7),
                               "needsAttention": True, "material": "PLANK", "quantity": 1,
                               "location": {"x": 1767, "y": 3620, "plane": 0}}]})

    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert len(aimed) >= 2, (
        "after the remove lands the burst must re-aim and start the build, not hand back to the "
        "engine loop for a tick"
    )


def _stub_for_burst_split(monkeypatch, aimed, inplace):
    """Like _stub_for_burst but keeps the two click KINDS apart: a re-aimed click_entity (which
    re-resolves the object's live screen position) versus an in-place click_human."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(mh, "plank_sack_count", lambda *a, **kw: 0)
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_object(40002, distance=1, x=1767, y=3620, plane=0)])
    monkeypatch.setattr(travel, "reachable_set", lambda *a, **kw: {(1766, 3620), (1767, 3620)})
    monkeypatch.setattr(travel, "can_reach", lambda *a, **kw: True)
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: None)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, obj, **kw: aimed.append(1) or True)
    monkeypatch.setattr(controller, "click_human", lambda ser: inplace.append(1) or True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1766, "y": 3620, "plane": 0})


def test_every_click_is_re_aimed_while_the_player_is_moving(monkeypatch):
    """USER 2026-08-30: "if we continuously click we gotta update the object x y otherwise while
    we move we click away".

    Exactly right, and it is what makes the in-place repeat unsafe MID-WALK: the object's world
    tile does not change, but its SCREEN projection slides under the cursor as the player and
    camera move, so clicking the same pixel again lands on bare ground. click_entity with
    object_refresh_target re-resolves that projection from /object/at (un-cached), which is what
    "update the object x y" means in practice.

    So the rule is: re-aim whenever we have moved since the last click, click in place only when
    standing still."""
    aimed, inplace = [], []
    _stub_for_burst_split(monkeypatch, aimed, inplace)
    ctx, state = _burst_ctx_and_state()
    monkeypatch.setattr(mh, "_hotspot_now", lambda target: None)
    monkeypatch.setattr(mh, "_player_is_moving", lambda prev: True)

    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert not inplace, "a moving player must never repeat a click at a stale pixel"
    assert len(aimed) == mh.HOTSPOT_SPAM_MAX_CLICKS


def test_repeats_are_in_place_once_the_player_has_stopped(monkeypatch):
    """Standing still, the projection cannot slide, so re-flying the cursor for every repeat is
    pure latency -- MEASURED live 2026-08-30 at 1.0-1.1s per re-aimed click against the human's
    0.20s median. The first click still aims."""
    aimed, inplace = [], []
    _stub_for_burst_split(monkeypatch, aimed, inplace)
    ctx, state = _burst_ctx_and_state()
    monkeypatch.setattr(mh, "_hotspot_now", lambda target: None)
    monkeypatch.setattr(mh, "_player_is_moving", lambda prev: False)

    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert len(aimed) == 1, "aim once"
    assert len(inplace) == mh.HOTSPOT_SPAM_MAX_CLICKS - 1, "then click in place"


def _stub_for_burst(monkeypatch, clicks):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1766, "y": 3620, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(mh, "plank_sack_count", lambda *a, **kw: 0)
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_object(40002, distance=1, x=1767, y=3620, plane=0)])
    monkeypatch.setattr(travel, "reachable_set", lambda *a, **kw: {(1766, 3620), (1767, 3620)})
    monkeypatch.setattr(travel, "can_reach", lambda *a, **kw: True)
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: None)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, obj, **kw: clicks.append(time.time()) or True)
    # Repeats inside a burst are in-place click_human calls, not fresh click_entity flights.
    monkeypatch.setattr(controller, "click_human",
                        lambda ser: clicks.append(time.time()) or True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


def test_work_phase_keeps_clicking_until_the_varbit_moves(monkeypatch):
    clicks = []
    _stub_for_burst(monkeypatch, clicks)
    ctx, state = _burst_ctx_and_state()

    # The varbit moves on the 3rd read -- a human-sized burst.
    reads = {"n": 0}

    def rows(target):
        reads["n"] += 1
        value = 99 if reads["n"] >= 3 else target.get("value")
        return {"objectId": 40002, "value": value, "needsAttention": False}

    monkeypatch.setattr(mh, "_hotspot_now", rows)
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert 2 <= len(clicks) <= mh.HOTSPOT_SPAM_MAX_CLICKS,         f"expected a human-sized burst, got {len(clicks)} clicks"


def test_work_phase_stops_bursting_at_the_human_click_cap(monkeypatch):
    """A hotspot whose varbit NEVER moves must not be clicked forever inside one tick."""
    clicks = []
    _stub_for_burst(monkeypatch, clicks)
    ctx, state = _burst_ctx_and_state()
    monkeypatch.setattr(mh, "_hotspot_now", lambda target: None)

    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert len(clicks) <= mh.HOTSPOT_SPAM_MAX_CLICKS
    assert ctx.state.data.get("mh_hotspot_clicks", 0) == 1,         "one unproductive BURST counts as one failure, so HOTSPOT_UNREACHABLE_CLICKS still means 5 bursts"


def test_a_completed_action_does_not_leave_a_long_cooldown_behind(monkeypatch):
    """The burst is the whole point only if what follows it is short. The old gate set a FIXED
    2.4s (+0.7s/tile) cooldown before every click and paid it whether or not the action had
    finished -- so even with the burst exiting on the varbit, the NEXT action would still wait
    out the worst case. A burst that saw the varbit move has, by definition, finished."""
    clicks = []
    _stub_for_burst(monkeypatch, clicks)
    ctx, state = _burst_ctx_and_state()
    monkeypatch.setattr(mh, "_hotspot_now",
                        lambda target: {"objectId": 40002, "value": 99,
                                        "needsAttention": False})

    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data["mh_hotspot_cooldown"] <= mh.HOTSPOT_SPAM_GAP_MAX_S,         "a finished action must not be followed by the unfinished-action budget"


def test_a_burst_keeps_clicking_while_the_player_is_still_walking(monkeypatch):
    """USER 2026-08-30, watching it live: "it clicks twice player moves then it waits at the thing
    and clicks again -- need to keep clicking while moving until done".

    An earlier draft stopped the burst the moment the player moved, reasoning from ROSS that
    re-clicking mid-walk re-queues and re-paths. That does not apply to an IN-PLACE repeat: ROSS
    was a re-AIMED click on a later tick, which re-resolved the object. Clicking the same pixel
    again just re-queues the same interaction -- which is exactly what the human recordings show,
    straight through the approach.

    And a burst spent walking must NOT count as a failed attempt: the action has not started, so
    charging it against HOTSPOT_UNREACHABLE_CLICKS would walk away from a hotspot we are still
    on our way to -- the ROSS misdiagnosis, arrived at from the opposite direction."""
    clicks = []
    _stub_for_burst(monkeypatch, clicks)
    ctx, state = _burst_ctx_and_state()
    monkeypatch.setattr(mh, "_hotspot_now", lambda target: None)
    monkeypatch.setattr(mh, "_player_is_moving", lambda prev: True)

    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert len(clicks) == mh.HOTSPOT_SPAM_MAX_CLICKS,         "movement must not cut the burst short -- keep clicking until the varbit moves"
    assert ctx.state.data.get("mh_hotspot_clicks", 0) == 0,         "a burst spent walking is not a failed attempt"
    assert ctx.state.data["mh_hotspot_cooldown"] <= mh.HOTSPOT_SPAM_GAP_MAX_S,         "come straight back and keep clicking rather than waiting out the action's budget"


# MEASURED LIVE 2026-08-30 at NOELLA's, the only home whose upper floor is DISCONNECTED -- two
# staircases reaching two halves that do not join. USER: "has to go downstairs then take the
# other ladder". The player stood at (2661,3319) plane 1 with hotspot 40160 at (2657,3322) plane
# 1; worldmap.reachable_from returned just EIGHTEEN tiles (the near half), find_path answered
# None, and can_reach answered False -- all three correct.
#
# The routine already had exactly the right recovery (SPLIT_UPPER_FLOOR_HOMES -> climb down and
# take the other staircase) but it lived AFTER the can_reach gate, in the
# HOTSPOT_UNREACHABLE_CLICKS branch. can_reach returning False returns early, so on the one home
# the recovery exists for, it could never be reached. It only ever ran when reachability was
# UNKNOWN -- which is why its own unit test passed while the live case wedged.

def test_noella_climbs_down_when_the_hotspot_is_on_the_other_half_of_a_split_floor(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _object(40160, distance=5, x=2657, y=3322, plane=1),
        _object(16685, distance=2, x=2660, y=3318, plane=1),
    ])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2661, "y": 3319, "plane": 1})
    # The live answer: the HOTSPOT is genuinely unreachable (far half of the split floor), while
    # the staircase on our own half is perfectly reachable -- _climb has its own gate and would
    # rightly refuse a ladder it cannot path to.
    monkeypatch.setattr(travel, "can_reach",
                        lambda goal, reach=None, **kw:
                        False if (goal.get("x"), goal.get("y")) == (2657, 3322) else True)
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: False)
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, *a, **kw: False)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(option=option))

    spot = _hotspot(10554, 40160, 3, True)
    spot["plane"] = 1
    spot["location"] = {"x": 2657, "y": 3322, "plane": 1}
    state = _work_state([spot], homeName="NOELLA",
                        ladderObjectIds=[17026, 16685, 15645, 15648],
                        standingLocation={"x": 2659, "y": 3322, "plane": 0})

    ctx = _Ctx()
    for _ in range(mh.HOTSPOT_UNREACHABLE_CLICKS + 2):
        ctx.state.data["mh_last_ladder_click"] = 0.0
        mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert menu.get("option") == "Climb-down",         "on a split upper floor the far half is reached by going DOWN and up the other staircase"
    assert not ctx.state.safety_stopped, "climbing down is the recovery, not a reason to stop"


# MEASURED LIVE 2026-08-30 at BARBARA's, and the third instance of one structural mistake.
# Player (1749,3534), hotspot 40013 at (1749,3539) walled in on all four sides in LIVE collision
# -- but worldmap finds a 13-step route to a tile beside it, i.e. the way in is a SHUT DOOR the
# static map bakes open. So the recovery is exactly right in principle: walk at the hotspot, and
# the walker resolves the door on the way.
#
# It walked at the STANDING TILE instead -- (1750,3534), Chebyshev distance ONE from the player,
# against HOUSE_ARRIVAL_TOLERANCE of 1. walk_to returned True instantly without moving or opening
# anything, 14 ticks running, until the ceiling stopped it.
#
# CLAUDE.md already records this lesson from NORMAN's ("head for the HOTSPOT, not the house's
# standing tile ... using it as the goal is what makes the doorway between us and it visible to
# the walker") -- but that fix lives in the HOTSPOT_UNREACHABLE_CLICKS branch, and the can_reach
# gate returns before ever reaching it. Same shape as the NOELLA split-floor bug: a correct
# recovery sitting behind a gate that returns early.

def test_the_unreachable_branch_walks_at_the_hotspot_not_the_standing_tile(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1749, "y": 3534, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(mh, "plank_sack_count", lambda *a, **kw: 0)
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_object(40013, distance=5, x=1749, y=3539, plane=0)])
    monkeypatch.setattr(travel, "reachable_set", lambda *a, **kw: {(1749, 3534)})
    monkeypatch.setattr(travel, "can_reach", lambda *a, **kw: False)
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: None)

    walked = {}
    monkeypatch.setattr(travel, "walk_to",
                        lambda ctx, target, *a, **kw: walked.setdefault("t", target) or True)

    spot = _hotspot(10554, 40013, 3, True, quantity=1)
    spot["location"] = {"x": 1749, "y": 3539, "plane": 0}
    state = {
        "homeName": "BARBARA",
        "standingLocation": {"x": 1750, "y": 3534, "plane": 0},
        "area": {"x": 1744, "y": 3530, "width": 14, "height": 14, "plane": 0},
        "hotspots": [spot],
        "requiredMaterials": {"planks": 1, "steelBars": 0},
    }

    mh.work_phase(_Ctx(), mh.DEFAULT_CONFIG, state)

    assert (walked["t"]["x"], walked["t"]["y"]) == (1749, 3539), (
        "walking at the standing tile we are already stood beside achieves nothing -- head for "
        "the hotspot so the walker sees the door between us and it"
    )


def test_the_unreachable_branch_walks_to_a_tile_it_could_act_from(monkeypatch):
    """The follow-on from BARBARA's, MEASURED live 2026-08-30. Aiming the walk at the hotspot is
    necessary but not sufficient: find_path(goal_adjacent=True) stops at ANY adjacent tile, and
    at Barbara's it stopped on (1749,3540) -- adjacent to the Bed but behind the bedroom's north
    WALL (16416). The player stood there and can_reach kept answering False, correctly.

    Only two tiles can actually touch that bed, (1749,3538) and (1748,3539), both inside the
    bedroom. Walking to one of THOSE routes through the bedroom door, which open_door_toward then
    finds as an ordinary door on the route."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1749, "y": 3541, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(mh, "plank_sack_count", lambda *a, **kw: 0)
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_object(40013, distance=2, x=1749, y=3539, plane=0)])
    monkeypatch.setattr(travel, "reachable_set", lambda *a, **kw: {(1749, 3541)})
    monkeypatch.setattr(travel, "can_reach", lambda *a, **kw: False)
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: None)
    monkeypatch.setattr(travel, "interaction_tiles",
                        lambda goal, flags=None: [(1749, 3538), (1748, 3539)])

    walked = {}
    monkeypatch.setattr(travel, "walk_to",
                        lambda ctx, target, *a, **kw: walked.setdefault("t", target) or True)

    spot = _hotspot(10554, 40013, 3, True, quantity=1)
    spot["location"] = {"x": 1749, "y": 3539, "plane": 0}
    state = {
        "homeName": "BARBARA",
        "standingLocation": {"x": 1750, "y": 3534, "plane": 0},
        "area": {"x": 1744, "y": 3530, "width": 14, "height": 14, "plane": 0},
        "hotspots": [spot],
        "requiredMaterials": {"planks": 1, "steelBars": 0},
    }

    mh.work_phase(_Ctx(), mh.DEFAULT_CONFIG, state)

    assert (walked["t"]["x"], walked["t"]["y"]) in {(1749, 3538), (1748, 3539)}, (
        "walk to a tile we could actually act from, not merely one adjacent through a wall"
    )


# MEASURED LIVE 2026-08-30, after the first idle-drift fix: drifts fell from 14-per-48-clicks to
# 4-per-28, but one still fired after only 1.5s -- mid-work. USER: "seems like flicking still
# happen".
#
# The cause is the SUPPRESSION WINDOW, not the suppression. mark_busy was handed
# mh_hotspot_cooldown, which after a SUCCESSFUL action is now HOTSPOT_SPAM_GAP_MAX_S (0.35s) --
# by design, since a finished action has nothing left to wait out. But the routine then spends a
# second or three on its own decision work before the next click, and that window was unguarded.
#
# Being in work_phase with a hotspot to act on IS being busy, whatever the click cooldown says.

def test_work_phase_holds_the_idle_drift_off_for_the_whole_working_tick(monkeypatch):
    clicks = []
    _stub_for_burst(monkeypatch, clicks)
    ctx, state = _burst_ctx_and_state()
    monkeypatch.setattr(mh, "_hotspot_now", lambda target: None)

    busy = []
    monkeypatch.setattr(controller, "mark_busy", lambda s: busy.append(s))

    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert busy, "work_phase must tell the idle drift it is working"
    assert max(busy) >= mh.WORK_BUSY_WINDOW_S, (
        "the window has to outlast the routine's own decision work between clicks, not just the "
        "click cooldown"
    )


# USER 2026-08-30, describing the residual flicking precisely: "it is most noticable when we run
# and reclick ... as we move the mouse have to flick more but we flick back for some reason after
# we click", and "only relevant when we click hotspots".
#
# That is exactly what a STALE AIM looks like. click_entity begins its flight at
# entity["screenX"/"screenY"], and the burst was handing it the SAME obj dict for every re-aim --
# fetched once at the top of the tick from /objects. While the player runs, that projection is
# stale within a fraction of a second, so each re-aimed click set off toward where the object had
# been (the flick BACK) and refresh_target only pulled it forward again MID-FLIGHT.
#
# refresh_target fixes the landing, never the departure. A re-aim has to START from the live
# position, which is what /object/at answers and what object_refresh_target wraps anyway.

def test_every_burst_re_aim_passes_a_refresh_target(monkeypatch):
    """The routine's half of the contract. The LIVE re-resolve itself now lives in click_entity,
    which uses refresh_target for the DEPARTURE as well as mid-flight (see
    test_click_entity_departs_from_the_refreshed_position_not_the_stale_one) -- so all this file
    has to guarantee is that every re-aim actually hands one over. Without it click_entity has
    nothing to re-resolve from and sets off toward the tick-old /objects snapshot, which is the
    "we flick back after we click" the user watched happen."""
    passed = []
    _stub_for_burst_split(monkeypatch, [], [])
    monkeypatch.setattr(controller, "object_refresh_target",
                        lambda x, y, plane, obj_id, **kw: ("factory", x, y, plane, obj_id))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, obj, **kw: passed.append(kw.get("refresh_target")) or True)
    monkeypatch.setattr(mh, "_hotspot_now", lambda target: None)
    monkeypatch.setattr(mh, "_player_is_moving", lambda prev: True)

    ctx, state = _burst_ctx_and_state()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert passed, "expected the burst to click"
    assert all(p == ("factory", 1767, 3620, 0, 40002) for p in passed), (
        f"every re-aim must carry a refresh_target for the hotspot -- got {passed}"
    )


# USER-REPORTED 2026-08-30, two symptoms with one shared cause and one aggravating one:
#   "seems like we miss the npc at the end is the mouse move not using updated data?"
#   "sometimes when we sucessfully click the npc and we walking towards him we time out and
#    click again and miss"
#
# 1. The homeowner is an NPC and NPCs WALK. The turn-in used a bare click_menu_action with no
#    refresh_target, and CLAUDE.md is explicit about what that means: the menu path "right-clicks
#    exactly the pixel you give it". Against a moving target that is a miss by construction.
#    click_menu_action gained refresh_target on 2026-08-27 precisely for this; this call site
#    never adopted it. npc_index_refresh_target tracks by INDEX, so it can never re-resolve onto
#    a different individual, and it is tile-deadbanded so hull jitter does not cause replans.
#
# 2. TURN_IN_CLICK_COOLDOWN_S was a flat 2.0s with no walk budget, while the hotspot click had
#    already been given `base + distance * walk` for exactly this reason (the ROSS measurement:
#    "the game queues the interaction and performs it ON ARRIVAL"). So a talk-to on an NPC a few
#    tiles away expired while we were still walking, we clicked again -- and because of (1) that
#    second click aimed where the NPC no longer was. The two faults produce precisely the
#    sequence reported: click works, walk starts, timeout, re-click, miss.

def _turnin_state():
    return {"homeName": "ROSS", "npcName": "Ross", "npcId": 10420, "npcUpstairs": False,
            "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
            "hotspots": [_hotspot(10554, 40164, 7, False)]}


def test_the_turn_in_click_tracks_the_npc_while_it_walks(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2613, "y": 3316, "plane": 0})
    monkeypatch.setattr(controller, "get_npc_nearest",
                        lambda npc_id: {"id": 10420, "index": 77, "name": "Ross", "distance": 4,
                                        "x": 2615, "y": 3318, "plane": 0,
                                        "screenX": 500, "screenY": 400})
    monkeypatch.setattr(controller, "npc_index_refresh_target",
                        lambda idx: ("npc-factory", idx))
    seen = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: seen.update(kw, option=option))

    ctx = _Ctx()
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, _turnin_state())

    assert seen.get("option") == "Talk-to"
    assert seen.get("refresh_target") == ("npc-factory", 77), (
        "the homeowner walks -- the click must track it, or it lands where the NPC was"
    )


def test_the_turn_in_does_not_reclick_while_we_are_still_walking_to_the_npc(monkeypatch):
    """USER 2026-08-30: "sometimes when we sucessfully click the npc and we walking towards him we
    time out and click again and miss", and then: "we can use the walk / idle function instead to
    verify".

    Exactly right, and better than the alternative. The game QUEUES the interaction and performs
    it on ARRIVAL, so the honest question is "have we arrived yet", not "has a guessed duration
    elapsed". TURN_IN_CLICK_COOLDOWN_S was a flat 2.0s first-draft guess with no idea how far the
    NPC was; budgeting the walk by distance would only have replaced one guess with another,
    while the player's own movement answers it exactly.

    A re-click while walking is not merely wasted: the NPC has moved on by then, so it lands
    where he was -- which is the miss that was reported."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2613, "y": 3316, "plane": 0})
    monkeypatch.setattr(controller, "get_npc_nearest",
                        lambda npc_id: {"id": 10420, "index": 77, "name": "Ross", "distance": 6,
                                        "x": 2619, "y": 3316, "plane": 0,
                                        "screenX": 500, "screenY": 400})
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: None)
    clicks = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, o, **kw: clicks.append(o) or True)
    monkeypatch.setattr(mh, "_player_is_moving", lambda prev: True)

    ctx = _Ctx()
    state = _turnin_state()
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)      # first click: fine
    assert len(clicks) == 1

    ctx.state.data["mh_last_turnin_click"] = 0.0          # any time-based gate has expired
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert len(clicks) == 1, (
        "still walking means the queued Talk-to has not happened yet -- re-clicking now aims "
        "where the NPC no longer is"
    )


# SEEN LIVE TWICE, 2026-08-30 (JEFF, then BOB): _approach_missing_hotspot's last-resort walk
# aims at `standingLocation`, which is ALWAYS reported at plane 0. From an upper floor that walk
# cannot succeed in any circumstance -- travel rightly answers "target is on plane 0 and we are
# on 1", and the tick is spent on a bridge call and a misleading "walking toward X's house" line.
#
# It is bounded (mh_work_stuck escalates), which is why it was left alone earlier in the session
# as waste rather than a stall. It is still the documented rule -- "a walk-to-X recovery must
# pick X on OUR OWN FLOOR" -- and this is the last known place that breaks it.

def test_the_missing_hotspot_fallback_does_not_walk_at_a_plane_0_tile_from_upstairs(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3241, "y": 3486, "plane": 1})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])   # no ladder in range either
    monkeypatch.setattr(travel, "walk_to",
                        lambda ctx, target, *a, **kw: pytest.fail(
                            "must not walk at a plane-0 tile from plane 1"))

    target = _hotspot(10554, 39987, 3, True)
    target["location"] = {"x": 3238, "y": 3488, "plane": 1}
    state = {"homeName": "BOB", "standingLocation": {"x": 3238, "y": 3486, "plane": 0},
             "ladderObjectIds": [], "hotspots": [target]}

    ctx = _Ctx()
    mh._approach_missing_hotspot(ctx, mh.DEFAULT_CONFIG, state, target)

    assert ctx.state.data.get("mh_work_stuck", 0) >= 1,         "the stuck counter must still advance so the escalation is reached"


# USER 2026-08-30: "an efficiency we can add when clicking contact spell we can immidiatly swap
# to our inventory with W while we wait for spell to be cast".
#
# Free latency. Casting NPC Contact leaves the MAGIC tab open, and travel_phase's very next act
# is to open the INVENTORY tab before it can click a teleport tablet (an inventory item is not on
# screen while the spellbook is -- LIVE RUN 2026-08-28, the talisman right-click opened an empty
# ['Cancel'] menu because it landed on the spellbook). That tab switch currently happens AFTER
# the spell has resolved, so its cost is serial with the cast. Doing it while the cast animates
# overlaps the two: the spell does not care which tab is showing.

def test_casting_npc_contact_switches_to_the_inventory_tab_while_it_resolves(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "key_tap", lambda ser, k: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget",
                        lambda g, c: {"screenX": 100, "screenY": 100, "clickable": True})
    order = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: order.append("cast") or True)
    monkeypatch.setattr(controller, "open_inventory_tab",
                        lambda ser: order.append("inventory") or True)

    ctx = _Ctx()
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert order == ["cast", "inventory"], (
        f"switch tabs right after the cast, while it resolves -- got {order}"
    )


# --- travel_phase ---------------------------------------------------------------

# MEASURED LIVE 2026-08-29, first run of the routine against the new collision map: with a LEELA
# contract in hand and the player at (1748,3598) in Hosidius, travel_phase logged
# "no walking route to (1785,3592) -- not attempting the walk" every tick, forever.
#
# The route request was not wrong -- LEELA's `standingLocation` is a SOLID tile. All four of its
# edges read blocked in both directions on the real map (it is furniture standing inside an
# otherwise walkable interior, exactly the hazard the TAU session recorded for (3047,3345)), so
# find_path correctly refuses to route ONTO it and `goal_adjacent=False` demands precisely that.
# An adjacent route exists and is 43 steps.
#
# `standingLocation` is a fixed per-home hint point, NOT a promise that the tile is stand-on-able,
# so no call site may ask for an exact route to it. Note every one of these walks already accepts
# arrival within a tolerance of 3+ tiles, which makes an exact-route demand self-contradictory
# even where the tile happens to be open.
#
# This runs against the REAL bundled map rather than a stub on purpose: stubbing walk_to (as every
# other travel_phase test does) swallows **kw and so cannot see the bug at all.
LEELA_STANDING_TILE = {"x": 1785, "y": 3592, "plane": 0}
LEELA_NEARBY_PLAYER_TILE = {"x": 1748, "y": 3598, "plane": 0}


def test_leelas_standing_tile_is_solid_on_every_edge():
    """The premise of the bug below. If a re-dumped map ever opens this tile, this fails first
    and tells you the regression test underneath it has stopped testing anything."""
    x, y, plane = 1785, 3592, 0
    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        assert worldmap.passable(x, y, plane, dx, dy) is False
        assert worldmap.passable(x - dx, y - dy, plane, dx, dy) is False


def test_travel_phase_asks_for_a_route_the_map_can_actually_answer(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: dict(LEELA_NEARBY_PLAYER_TILE))

    captured = {}

    def fake_walk_to(ctx, target, *args, **kw):
        captured["target"] = target
        captured["goal_adjacent"] = kw.get("goal_adjacent", True)
        return True

    monkeypatch.setattr(travel, "walk_to", fake_walk_to)

    ctx = _Ctx()
    ctx.state.data["mh_last_home"] = "LEELA"
    ctx.state.data["mh_teleported_this_contract"] = True
    state = {"homeName": "LEELA", "standingLocation": dict(LEELA_STANDING_TILE)}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert captured["target"]["x"] == 1785 and captured["target"]["y"] == 3592

    route = worldmap.find_path(
        LEELA_NEARBY_PLAYER_TILE["x"], LEELA_NEARBY_PLAYER_TILE["y"], 0,
        captured["target"]["x"], captured["target"]["y"],
        goal_adjacent=captured["goal_adjacent"],
    )
    assert route is not None, (
        "travel_phase asked for a route onto LEELA's standing tile, which is solid on every "
        "edge -- walk_to returns False every tick and the phase spins forever"
    )

def test_travel_phase_teleports_then_walks(monkeypatch):
    clicked = {}

    # UPDATED 2026-08-28: the latch now requires the click to succeed AND the player to have
    # actually moved, so this has to simulate a teleport that really happened.
    positions = iter([{"x": 3000, "y": 3000, "plane": 0}, {"x": 3239, "y": 3450, "plane": 0}])
    monkeypatch.setattr(controller, "get_player", lambda: next(positions, {"x": 3239, "y": 3450, "plane": 0}))
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Varrock teleport", 8)])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)

    def fake_click(ser, entity, **kw):
        clicked["entity"] = entity
        return True

    monkeypatch.setattr(controller, "click_entity", fake_click)
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)

    ctx = _Ctx()
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert clicked["entity"]["name"] == "Varrock teleport"
    assert ctx.state.data["mh_teleported_this_contract"] is True


def test_travel_phase_walks_to_standing_tile_after_teleport_and_advances_to_work(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3448, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_last_home"] = "JEFF"
    ctx.state.data["mh_teleported_this_contract"] = True
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data["mh_phase"] == "work"


# MEASURED LIVE 2026-08-29, second run: the player was UPSTAIRS at JEFF's (plane 1) with a
# JEFF contract in hand, and travel_phase logged this 32 times in ~40s with nothing to stop it:
#
#   [travel] target is on plane 0 and we are on 1 -- walking cannot change floor
#   [travel] no walking route to (3239,3450) -- not attempting the walk
#   [mahogany_homes] still walking toward JEFF's standing tile
#
# WALKING CANNOT CHANGE FLOOR, and `standingLocation` is ALWAYS reported at plane 0 -- so from
# any upper floor this walk cannot succeed in any circumstance. It is a hard deadlock, not a slow
# path. The identical bug was found and fixed in work_phase (_approach_missing_hotspot) and again
# in turn_in_phase on 2026-08-29; travel_phase was simply never covered, and it is the one phase
# a restart lands in, so a restart while upstairs wedges the routine on the spot.
#
# The descent is owned here for the same reason turn_in_phase owns its own: work_phase's ladder
# handling lives in _approach_missing_hotspot and does not run in this phase at all.

def test_travel_phase_climbs_down_instead_of_walking_at_a_plane_0_tile_from_upstairs(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3240, "y": 3450, "plane": 1})
    monkeypatch.setattr(controller, "get_objects",
                        lambda **kw: [_object(11789, distance=2, x=3240, y=3451, plane=1)])
    monkeypatch.setattr(travel, "walk_to",
                        lambda ctx, target, *a, **kw: pytest.fail(
                            "must not try to walk to a plane-0 tile from plane 1"))

    climbed = {}
    monkeypatch.setattr(mh, "_climb",
                        lambda ctx, ladder, option, why, **kw: climbed.update(
                            ladder=ladder, option=option))

    ctx = _Ctx()
    ctx.state.data["mh_last_home"] = "JEFF"
    ctx.state.data["mh_teleported_this_contract"] = True
    state = {
        "homeName": "JEFF",
        "standingLocation": {"x": 3239, "y": 3450, "plane": 0},
        "area": {"x": 3235, "y": 3445, "width": 10, "height": 12, "plane": 0},
        "ladderObjectIds": [11789, 11793],
    }
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert climbed["ladder"]["id"] == 11789
    assert climbed["option"] == "Climb-down"


def test_travel_phase_safety_stops_rather_than_retrying_a_routeless_walk_forever(monkeypatch):
    """The ceiling every other loop in this routine already has. travel_phase's walk branch was
    the only one without one -- which is why both routing bugs found live on 2026-08-29 presented
    as an endless spin instead of a stop naming the problem."""
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3240, "y": 3450, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, *a, **kw: False)

    ctx = _Ctx()
    ctx.state.data["mh_last_home"] = "JEFF"
    ctx.state.data["mh_teleported_this_contract"] = True
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}

    for _ in range(mh.STUCK_TICK_LIMIT + 1):
        mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.safety_stopped, "travel_phase retried a routeless walk forever"


def test_travel_phase_banks_when_teleport_item_missing(monkeypatch):
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])

    ctx = _Ctx()
    state = {"homeName": "JEFF", "standingLocation": {"x": 3239, "y": 3450, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data["mh_phase"] == "bank"


def test_travel_phase_resets_teleport_flag_on_new_home(monkeypatch):
    positions = iter([{"x": 3000, "y": 3000, "plane": 0}, {"x": 3047, "y": 3345, "plane": 0}])
    monkeypatch.setattr(controller, "get_player", lambda: next(positions, {"x": 3047, "y": 3345, "plane": 0}))
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Falador teleport", 8)])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_last_home"] = "JEFF"
    ctx.state.data["mh_teleported_this_contract"] = True
    state = {"homeName": "TAU", "standingLocation": {"x": 3047, "y": 3345, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    # New home -> the flag must have been reset before being set again by this same call.
    assert ctx.state.data["mh_last_home"] == "TAU"
    assert ctx.state.data["mh_teleported_this_contract"] is True


def test_travel_phase_uses_the_menu_for_xerics_talisman(monkeypatch):
    # The talisman's LEFT-CLICK default is not the destination we want -- "Xeric's Glade" has to
    # be picked by name off the right-click menu. Tablets ("Break") are fine bare-clicked.
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3000, "y": 3000, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Xeric's talisman")])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    bare_clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: bare_clicks.append(entity))
    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(entity=entity, option=option))

    ctx = _Ctx()
    state = {"homeName": "LEELA", "standingLocation": {"x": 1785, "y": 3592, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert menu["option"] == "Xeric's Glade"
    assert menu["entity"]["name"] == "Xeric's talisman"
    assert bare_clicks == []


def test_city_teleports_are_name_option_pairs():
    for home, entry in mh.CITY_TELEPORTS.items():
        assert isinstance(entry, tuple) and len(entry) == 2, home
        name, option = entry
        assert isinstance(name, str) and name
        # Only the talisman needs a menu option; every tablet's default action already teleports.
        assert option == ("Xeric's Glade" if name == "xeric's talisman" else None), home


def _work_state(hotspots, required=None, **extra):
    state = {
        "hotspots": hotspots,
        "requiredMaterials": required or {"planks": 3, "steelBars": 0},
        "standingLocation": {"x": 3239, "y": 3450, "plane": 0},
        "homeName": "JEFF",
        "ladderObjectIds": [11789, 11793],
    }
    state.update(extra)
    return state


def test_work_phase_advances_to_turn_in_when_nothing_left(monkeypatch):
    # Standing AT the home is now part of the claim -- an empty hotspot list from across the map
    # means "no data", not "finished". This test never said where it was standing (it reached the
    # live bridge for a position), so it says so now.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3450, "plane": 0})
    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 0, False)],
                                                      {"planks": 0, "steelBars": 0}))
    assert ctx.state.data["mh_phase"] == "turn_in"


def test_work_phase_banks_when_the_current_hotspot_is_unaffordable(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 1)])
    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([_hotspot(10554, 39989, 3, True, quantity=3)]))
    assert ctx.state.data["mh_phase"] == "bank"


def test_work_phase_works_a_hotspot_it_can_afford_even_when_the_contract_total_cannot(monkeypatch):
    # THE C3 REGRESSION: comparing against requiredMaterials (the whole remaining contract) sent
    # us to the bank without touching any of the hotspots we could already pay for.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 3)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(39989, distance=2)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.setdefault("entity", entity))

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([_hotspot(10554, 39989, 3, True, quantity=2)],
                              required={"planks": 14, "steelBars": 0}))

    assert clicked["entity"]["id"] == 39989
    assert ctx.state.data.get("mh_phase") != "bank"


def test_work_phase_prices_a_steel_bar_hotspot_against_bars_not_planks(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20)])   # planks aplenty, zero bars
    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([_hotspot(10554, 39989, 3, True, material="STEEL_BAR", quantity=1)]))
    assert ctx.state.data["mh_phase"] == "bank"


def test_work_phase_falls_back_to_the_contract_total_when_per_hotspot_cost_is_missing(monkeypatch):
    # material/quantity only exist on a REBUILT client. Defaulting a missing quantity to 0 would
    # read as "this hotspot is free" -- fail-OPEN, clicking hotspots we cannot pay for. An absent
    # field must fall back to the old (merely conservative) whole-contract check instead.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 2)])
    stale = _hotspot(10554, 39989, 3, True)
    del stale["material"], stale["quantity"]

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([stale], required={"planks": 9, "steelBars": 0}))
    assert ctx.state.data["mh_phase"] == "bank"


def test_work_phase_fallback_still_works_when_the_contract_total_is_affordable(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 20)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(39989, distance=2)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicked.setdefault("entity", entity))
    stale = _hotspot(10554, 39989, 3, True)
    del stale["material"], stale["quantity"]

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG,
                  _work_state([stale], required={"planks": 9, "steelBars": 0}))
    assert clicked["entity"]["id"] == 39989


def test_work_phase_walks_closer_when_object_not_yet_visible(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3100, "y": 3300, "plane": 0})
    walked = {}
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.setdefault("target", target))

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
    assert walked["target"] == {"x": 3239, "y": 3450, "plane": 0}
    assert ctx.state.data["mh_work_stuck"] == 1


def test_work_phase_climbs_a_ladder_when_at_the_house_but_the_hotspot_is_missing(monkeypatch):
    # /objects only reports the player's CURRENT plane, and most homes put hotspots upstairs
    # while standingLocation is always plane 0 -- "not found" here means "wrong floor".
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(11789, distance=3)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: pytest.fail("must not walk"))

    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(entity=entity, option=option))

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))

    assert menu["entity"]["id"] == 11789
    assert list(menu["option"]) == ["Climb-up", "Climb-down"]
    assert "mh_last_ladder_click" in ctx.state.data


def test_work_phase_does_not_re_climb_within_the_ladder_cooldown(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(11789, distance=3)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 0})
    clicks = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: clicks.append(option))

    ctx = _Ctx()
    ctx.state.data["mh_last_ladder_click"] = time.time()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
    assert clicks == []


def test_work_phase_safety_stops_when_a_hotspot_stays_unreachable(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_work_stuck"] = mh.STUCK_TICK_LIMIT - 1
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))

    assert ctx.state.safety_stopped is True
    assert "39989" in ctx.state.safety_reason


def test_work_phase_clicks_the_hotspot_object(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(39989, distance=2)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))

    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: clicked.setdefault("entity", entity))

    ctx = _Ctx()
    ctx.state.data["mh_work_stuck"] = 6
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
    assert clicked["entity"]["id"] == 39989
    assert "mh_last_hotspot_click" in ctx.state.data
    assert ctx.state.data["mh_work_stuck"] == 0     # progress resets the stuck counter


def test_work_phase_respects_click_cooldown(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                         lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(39989, distance=2)])

    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: clicked.append(entity))

    ctx = _Ctx()
    ctx.state.data["mh_last_hotspot_click"] = time.time()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
    assert clicked == []


def test_turn_in_phase_talks_to_homeowner(monkeypatch):
    monkeypatch.setattr(controller, "get_npc_nearest",
                         lambda npc_id: {"index": 5, "screenX": 10, "screenY": 20})
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [])

    talked = {}
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda ser, entity, option, **kw: talked.setdefault("option", option))

    ctx = _Ctx()
    state = {"npcId": 10415, "npcName": "Jeff"}
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert talked["option"] == "Talk-to"


def test_tick_accepts_offered_tea_whatever_phase_it_is_in(monkeypatch):
    # The completion chat line and the tea offer arrive on the SAME tick, so by the time the
    # dialogue is up hasContract has already flipped false and tick() has left "turn_in". A tea
    # check sited inside turn_in_phase could therefore never fire for the tick it mattered on.
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    monkeypatch.setattr(controller, "get_widgets_dump",
                         lambda: [{"group": mh.DIALOG_OPTION_GROUP, "child": 1,
                                   "text": "Yes, I'd love a cuppa.",
                                   "screenX": 40, "screenY": 50}])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank", lambda ctx, config, state: None)

    clicked = {}
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: clicked.setdefault("entity", entity))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.tick(ctx)
    assert clicked["entity"]["screenX"] == 40


def test_turn_in_phase_no_longer_reads_widgets_itself(monkeypatch):
    # tick() owns the tea check now; turn_in_phase must not duplicate it.
    monkeypatch.setattr(controller, "get_npc_nearest", lambda npc_id: {"index": 5})
    monkeypatch.setattr(controller, "get_widgets_dump",
                         lambda: pytest.fail("turn_in_phase must not dump widgets"))
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: True)

    ctx = _Ctx()
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, {"npcId": 10415, "npcName": "Jeff"})


def test_tick_starts_in_bank_phase_when_stock_is_low(monkeypatch):
    # PHASE_HANDLERS holds direct function references captured at module-load time, so
    # patching mh.bank_phase itself would NOT change what tick() actually calls through the
    # dict -- monkeypatch.setitem on the dict is the correct way to intercept it.
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank",
                         lambda ctx, config, state: ctx.state.data.setdefault("called", "bank"))

    ctx = _Ctx()
    mh.tick(ctx)
    assert ctx.state.data["mh_phase"] == "bank"
    assert ctx.state.data["called"] == "bank"


def test_tick_moves_to_request_contract_once_stocked(monkeypatch):
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8), _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "request_contract",
                         lambda ctx, config, state: ctx.state.data.setdefault("called", "request"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.tick(ctx)
    assert ctx.state.data["mh_phase"] == "request_contract"
    assert ctx.state.data["called"] == "request"


def test_tick_resets_to_request_contract_when_contract_disappears_mid_flow(monkeypatch):
    # Stock must read as sufficient here -- otherwise needs_bank_trip (which calls the real
    # controller.get_inventory/get_rune_pouch when unmocked) sees an empty inventory against a
    # bridge-less test environment and diverts to "bank" before this test ever gets to exercise
    # the request_contract reset it's actually checking.
    monkeypatch.setattr(controller, "get_mahogany_homes_state", lambda: {"hasContract": False})
    restock = mh.DEFAULT_CONFIG["restock"]
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], restock["targetPlanks"]),
        _item("Steel bar", restock["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8), _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                         lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                             {"name": "Cosmic rune", "quantity": 1000}]})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "request_contract",
                         lambda ctx, config, state: ctx.state.data.setdefault("called", "request"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "turn_in"
    mh.tick(ctx)
    assert ctx.state.data["mh_phase"] == "request_contract"
    assert ctx.state.data["called"] == "request"


def test_tick_does_not_overwrite_work_phases_own_bank_diversion(monkeypatch):
    # THE C3 REGRESSION: `elif phase in ("bank", "request_contract"): phase = "travel"` promoted
    # a "bank" diversion straight back to "travel" the instant hasContract was true, so
    # bank_phase never ran once and work_phase re-diverted every tick -- a livelock.
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 3})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank",
                        lambda ctx, config, state: ctx.state.data.setdefault("called", "bank"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.tick(ctx)

    assert ctx.state.data["mh_phase"] == "bank"
    assert ctx.state.data["called"] == "bank"


def test_tick_still_promotes_request_contract_to_travel_once_a_contract_exists(monkeypatch):
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 3})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "travel",
                        lambda ctx, config, state: ctx.state.data.setdefault("called", "travel"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "request_contract"
    mh.tick(ctx)

    assert ctx.state.data["mh_phase"] == "travel"
    assert ctx.state.data["called"] == "travel"


def test_tick_refuses_to_work_a_contract_of_the_wrong_tier(monkeypatch):
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 1, "homeName": "BOB"})
    for phase in mh.PHASE_HANDLERS:
        monkeypatch.setitem(mh.PHASE_HANDLERS, phase,
                            lambda ctx, config, state: ctx.state.data.setdefault("called", "any"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "travel"
    mh.tick(ctx)

    assert ctx.state.data["mh_phase"] == "request_contract"
    assert "called" not in ctx.state.data


def test_tick_works_a_contract_whose_tier_is_unreported(monkeypatch):
    # contractTier 0 means the provider could not parse a tier (older client build) -- be
    # permissive rather than deadlocking on a field that isn't being reported.
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 0})
    monkeypatch.setitem(mh.PHASE_HANDLERS, "travel",
                        lambda ctx, config, state: ctx.state.data.setdefault("called", "travel"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "request_contract"
    mh.tick(ctx)

    assert ctx.state.data["called"] == "travel"


def test_tick_does_nothing_when_disabled():
    ctx = _Ctx()
    ctx.state.config = dict(mh.DEFAULT_CONFIG)
    ctx.state.config["enabled"] = False
    mh.tick(ctx)
    assert "mh_phase" not in ctx.state.data


# --- knowing which FLOOR a hotspot is on ---------------------------------------------
#
# Adept homes put hotspots upstairs (JESS and NORMAN even put the HOMEOWNER upstairs -- the
# reference plugin's Home.npcUpstairs, ported 2026-08-28). /objects only ever reports the
# player's CURRENT plane, so before MahoganyHomesProvider started scanning every plane for the
# hotspot objects, "not in /objects" could not distinguish "upstairs" from "not close enough".
# The routine coped by right-clicking the ladder with BOTH ["Climb-up", "Climb-down"] and taking
# whichever the ladder offered -- fine in a two-storey house, but a guess, and on a three-storey
# one it can climb the wrong way and then have to guess its way back.

def test_climbs_up_when_the_hotspot_is_known_to_be_on_a_higher_floor(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(11789, distance=3)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: pytest.fail("must not walk"))
    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(option=option))

    spot = _hotspot(10554, 39989, 3, True)
    spot["plane"] = 1                      # provider located it upstairs
    mh.work_phase(_Ctx(), mh.DEFAULT_CONFIG, _work_state([spot]))

    assert menu["option"] == "Climb-up"


def test_climbs_down_when_the_hotspot_is_known_to_be_on_a_lower_floor(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(11789, distance=3)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 2})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: pytest.fail("must not walk"))
    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(option=option))

    spot = _hotspot(10554, 39989, 3, True)
    spot["plane"] = 1
    mh.work_phase(_Ctx(), mh.DEFAULT_CONFIG, _work_state([spot]))

    assert menu["option"] == "Climb-down"


def test_unknown_hotspot_plane_keeps_the_old_both_directions_guess(monkeypatch):
    """Against a client that hasn't been rebuilt the provider sends no plane, so the routine must
    fall back to offering both directions rather than assuming a floor."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(11789, distance=3)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: pytest.fail("must not walk"))
    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(option=option))

    mh.work_phase(_Ctx(), mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))

    assert list(menu["option"]) == ["Climb-up", "Climb-down"]


def test_a_hotspot_on_our_own_floor_is_walked_to_not_climbed_to(monkeypatch):
    """Knowing the plane also rules the ladder OUT: if the hotspot is on this floor and simply
    isn't rendered yet, climbing is exactly the wrong move -- it leaves the floor the work is on."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(11789, distance=3)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 0})
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: pytest.fail("must not climb"))
    walked = {}
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.setdefault("t", target))

    spot = _hotspot(10554, 39989, 3, True)
    spot["plane"] = 0
    mh.work_phase(_Ctx(), mh.DEFAULT_CONFIG, _work_state([spot]))

    assert walked["t"] == {"x": 3239, "y": 3450, "plane": 0}


# --- NOELLA: two staircases, disconnected upper floors -------------------------------
#
# USER REPORT 2026-08-28: "in ardougne we have two ladders in one house and the upstairs isnt
# connected so to solve that house you have to fix one upstairs climb down take the other ladder
# fix that upstairs". NOELLA is the house -- it is the ONLY one of the twelve with four ladder
# ids (17026, 16685, 15645, 15648 = two separate staircases); every other home has two (one
# staircase, up + down) or none.
#
# Two distinct failures here, and the second one bites even when the floor is known:
#  - Climbing the ladder nearest the PLAYER can land us in the upstairs section the hotspot is
#    not in. The ladder has to be chosen by proximity to the HOTSPOT.
#  - /objects reports the whole scene on the player's CURRENT plane, so a hotspot in the OTHER
#    upstairs section is perfectly visible from this one. work_phase therefore finds it and
#    clicks it forever, and because seeing the object is what resets mh_work_stuck, the stuck
#    counter never grows and the safety stop never fires. It needs its own progress guard.

def test_climbs_the_ladder_nearest_the_hotspot_not_the_one_nearest_us(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    # Two staircases. The near one (2 tiles) leads to the wrong half of the upper floor; the
    # hotspot sits above the far one (12 tiles).
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _object(17026, distance=2, x=2653, y=3318),
        _object(15645, distance=12, x=2665, y=3318),
    ])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2653, "y": 3318, "plane": 0})
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: pytest.fail("must not walk"))
    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(entity=entity, option=option))

    spot = _hotspot(10554, 40156, 3, True)
    spot["plane"] = 1
    spot["location"] = {"x": 2666, "y": 3319, "plane": 1}
    state = _work_state([spot], homeName="NOELLA",
                        ladderObjectIds=[17026, 16685, 15645, 15648],
                        standingLocation={"x": 2659, "y": 3322, "plane": 0})
    mh.work_phase(_Ctx(), mh.DEFAULT_CONFIG, state)

    assert menu["entity"]["id"] == 15645, "took the ladder nearest us, not the one under the hotspot"
    assert menu["option"] == "Climb-up"


def test_a_visible_but_unreachable_hotspot_eventually_climbs_down_to_switch_sections(monkeypatch):
    """The disconnected-upstairs case. The hotspot is on our own plane and fully visible in
    /objects, so nothing today stops the routine clicking it forever."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _object(40156, distance=14, x=2666, y=3319, plane=1),
        _object(16685, distance=2, x=2653, y=3318, plane=1),
    ])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2653, "y": 3318, "plane": 1})
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    menu = {}
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: menu.update(entity=entity, option=option))

    spot = _hotspot(10554, 40156, 3, True)
    spot["plane"] = 1
    spot["location"] = {"x": 2666, "y": 3319, "plane": 1}
    state = _work_state([spot], homeName="NOELLA",
                        ladderObjectIds=[17026, 16685, 15645, 15648],
                        standingLocation={"x": 2659, "y": 3322, "plane": 0})

    ctx = _Ctx()
    for _ in range(mh.HOTSPOT_UNREACHABLE_CLICKS + 2):
        ctx.state.data["mh_last_hotspot_click"] = 0.0     # bypass the click cooldown
        ctx.state.data["mh_last_ladder_click"] = 0.0
        mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert menu.get("option") == "Climb-down", "never gave up on the unreachable hotspot"


def test_real_progress_resets_the_unreachable_counter(monkeypatch):
    """A hotspot whose varbit value moves is being worked successfully -- that must never be
    mistaken for the unreachable case, or an ordinary multi-stage build would climb away mid-job."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(40156, distance=2, plane=1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2666, "y": 3319, "plane": 1})
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: pytest.fail("must not climb away mid-build"))

    ctx = _Ctx()
    for value in (1, 3, 4, 1, 3, 4, 1):          # varbit moving every tick = real progress
        spot = _hotspot(10554, 40156, value, True)
        spot["plane"] = 1
        ctx.state.data["mh_last_hotspot_click"] = 0.0
        mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([spot]))


def test_the_unreachable_guard_is_scoped_to_noella_only(monkeypatch):
    """Two staircases does not imply a disconnected upper floor, so this is keyed on the one home
    known to have one rather than inferred from ladder count. Everywhere else a hotspot that keeps
    refusing must stay on the existing walk/stuck/safety-stop path -- climbing away from a slow
    build would be a new failure mode in eleven houses to fix a quirk in one."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [
        _object(39989, distance=14, x=3250, y=3460, plane=1),
        _object(11793, distance=2, x=3239, y=3451, plane=1),
    ])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3239, "y": 3451, "plane": 1})
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: pytest.fail("JEFF has one staircase"))

    spot = _hotspot(10554, 39989, 3, True)
    spot["plane"] = 1
    ctx = _Ctx()
    for _ in range(mh.HOTSPOT_UNREACHABLE_CLICKS + 3):
        ctx.state.data["mh_last_hotspot_click"] = 0.0
        ctx.state.data["mh_last_ladder_click"] = 0.0
        mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([spot]))


# --- plank sack ----------------------------------------------------------------------
#
# USER REPORT 2026-08-28: "when loading up on planks we never fill the plank sack. after filling
# inventory once with teak we should click the plank sack confirm its full 28/28 then top up the
# inventory again". The sack was left out of the first draft ON PURPOSE (see the module docstring)
# to dodge golem_crafting's stale fur-pouch-estimate failure -- but that reasoning does not apply
# here, because the sack's contents are a real VARBIT, not an estimate.
#
# The reference plugin has this and the original port missed it: MahoganyHomesPlugin.
# countPlankSackVarb maps plank id -> varbit (960/18171, 8778/18172, 8780/18173, 8782/18174) and
# adds it to the inventory count whenever item 24882 is carried.
#
# MEASURED in recordings/2026-08-27_174045_mahogny-homes.log:812-870, which is exactly the loop
# the user describes:
#   Withdraw-All Teak plank -> inv +17 [28/28 used]
#   Fill Plank sack         -> varbit 18173 = 26, inv -17
#   Withdraw-All Teak plank -> inv +17 [28/28 used]
#   Fill Plank sack         -> varbit 18173 = 28, inv -2     <- full, 28 is the cap
#   Withdraw-All Teak plank -> inv +2  [28/28 used]

def test_plank_sack_count_reads_the_varbit_for_the_configured_plank(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Plank sack", 1)])
    seen = {}
    monkeypatch.setattr(controller, "get_varbit", lambda vid: seen.setdefault("id", vid) and 0 or 26)
    assert mh.plank_sack_count("Teak plank") == 26
    assert seen["id"] == 18173, "Teak plank must read varbit 18173"


def test_plank_sack_count_is_zero_without_the_sack(monkeypatch):
    """The varbit keeps its last value when the sack is banked, so reading it unconditionally
    would credit us planks we are not carrying -- exactly the stale-estimate trap the sack was
    originally left out to avoid."""
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 5)])
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 28)
    assert mh.plank_sack_count("Teak plank") == 0


def test_planks_available_counts_the_sack_as_well_as_the_inventory(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 28)
    inv = [_item("Teak plank", 10), _item("Plank sack", 1)]
    assert mh.planks_available(inv, "Teak plank") == 38


def test_an_unknown_plank_type_has_no_sack_varbit(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Plank sack", 1)])
    monkeypatch.setattr(controller, "get_varbit", lambda vid: pytest.fail("no varbit to read"))
    assert mh.plank_sack_count("Elder plank") == 0


def test_bank_phase_fills_the_sack_then_tops_the_inventory_back_up(monkeypatch):
    """The whole loop. The sack starts at 9, so it takes two fills to reach 28, and the run must
    end with one more withdraw so the inventory is full of loose planks too."""
    counts = iter([9, 26, 28, 28, 28, 28])
    monkeypatch.setattr(controller, "get_varbit", lambda vid: next(counts))
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Plank sack", 1)])

    calls = []
    monkeypatch.setattr(mh, "_withdraw_all", lambda ctx, name: calls.append(("withdraw", name)))
    monkeypatch.setattr(banker, "fill_plank_sack", lambda ctx: calls.append(("fill",)) or True)

    mh._load_plank_sack(_Ctx(), "Teak plank")

    assert calls == [("withdraw", "Teak plank"), ("fill",),
                     ("withdraw", "Teak plank"), ("fill",),
                     ("withdraw", "Teak plank")], calls


def test_no_plank_sack_means_no_fill_loop_at_all(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 5)])
    monkeypatch.setattr(mh, "_withdraw_all", lambda ctx, name: pytest.fail("no sack to load"))
    monkeypatch.setattr(banker, "fill_plank_sack", lambda ctx: pytest.fail("no sack to fill"))
    mh._load_plank_sack(_Ctx(), "Teak plank")


def test_a_sack_that_stops_absorbing_ends_the_loop(monkeypatch):
    """If the bank runs out of planks mid-fill the varbit stops moving. Without a no-progress
    exit that is an unbounded withdraw/fill loop against an empty bank."""
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 12)      # never moves
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Plank sack", 1)])
    fills = []
    monkeypatch.setattr(mh, "_withdraw_all", lambda ctx, name: None)
    monkeypatch.setattr(banker, "fill_plank_sack", lambda ctx: fills.append(1) or True)

    mh._load_plank_sack(_Ctx(), "Teak plank")
    assert len(fills) <= mh.PLANK_SACK_MAX_FILLS


def test_a_full_sack_alone_can_afford_a_hotspot_with_no_loose_planks(monkeypatch):
    """Sack planks are spent directly by the game -- CONFIRMED in the recordings, where varbit
    18173 ticks down in step with each hotspot's cost, next to the build animation and the
    Construction XP drop. Counting only loose planks would bank with a full sack in hand."""
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 28)
    inv = [_item("Plank sack", 1)]
    assert mh._can_afford(inv, "Teak plank", {}, _hotspot(10554, 39989, 3, True, quantity=3))


def test_an_empty_sack_and_no_loose_planks_still_banks(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    inv = [_item("Plank sack", 1)]
    assert not mh._can_afford(inv, "Teak plank", {}, _hotspot(10554, 39989, 3, True, quantity=3))


def test_steel_bar_hotspots_never_count_the_plank_sack(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 28)
    inv = [_item("Plank sack", 1)]
    spot = _hotspot(10554, 39989, 3, True, material="STEEL_BAR", quantity=1)
    assert not mh._can_afford(inv, "Teak plank", {}, spot)


# --- teleporting away from the magic tab ---------------------------------------------
#
# LIVE RUN 2026-08-28, first ever. request_contract_phase presses F6 to open the MAGIC tab and
# cast NPC Contact, and nothing ever switched back -- so travel_phase's right-click on the
# Xeric's talisman landed on the spellbook instead of the inventory. The controller log shows
# "No 'Xeric's Glade' entry in menu for Xeric's talisman (['Cancel'])" and the recorder, which
# sees what the CLIENT saw, logs no talisman interaction at all: the menu was empty.
#
# The second bug is what turned that miss into a disaster: mh_teleported_this_contract was
# latched unconditionally, right after a click that had plainly failed. travel_phase then
# believed it had already teleported and fell through to walking -- "following a 715-tile route
# from Shortest Path", on foot, from Ardougne toward Hosidius. The run had to be paused by hand.

def test_travel_phase_opens_the_inventory_tab_before_clicking_a_teleport(monkeypatch):
    """The magic tab is still up from the NPC Contact cast, so an inventory item is not on
    screen. controller.open_inventory_tab confirms the switch via /state's inventoryTabOpen
    rather than firing a key and hoping."""
    order = []
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: order.append("tab") or True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Xeric's talisman", 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2674, "y": 3314, "plane": 0})
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: order.append("click") or True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    state = {"homeName": "MARIAH", "standingLocation": {"x": 1766, "y": 3621, "plane": 0}}
    mh.travel_phase(_Ctx(), mh.DEFAULT_CONFIG, state)

    assert order[:2] == ["tab", "click"], f"inventory tab not opened before the click: {order}"


def test_a_failed_teleport_click_is_not_latched_as_done(monkeypatch):
    """The bug that cost the run. A click that returns falsy must leave the latch clear so the
    next tick retries the teleport -- latching it makes travel_phase believe it has already
    teleported and walk to another continent instead."""
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Xeric's talisman", 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2674, "y": 3314, "plane": 0})
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: False)
    monkeypatch.setattr(mh, "TELEPORT_CONFIRM_TIMEOUT_S", 0.05)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: pytest.fail("must not walk"))

    ctx = _Ctx()
    state = {"homeName": "MARIAH", "standingLocation": {"x": 1766, "y": 3621, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert not ctx.state.data.get("mh_teleported_this_contract")


def test_a_teleport_that_does_not_move_us_is_not_latched_either(monkeypatch):
    """A click can be accepted and still not teleport (out of charges, a dialogue eats it). The
    latch has to key on actually having MOVED, not on the click returning true."""
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Xeric's talisman", 1)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2674, "y": 3314, "plane": 0})
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: pytest.fail("must not walk"))
    monkeypatch.setattr(mh, "TELEPORT_CONFIRM_TIMEOUT_S", 0.05)

    ctx = _Ctx()
    state = {"homeName": "MARIAH", "standingLocation": {"x": 1766, "y": 3621, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert not ctx.state.data.get("mh_teleported_this_contract")


def test_a_teleport_that_moves_us_is_latched(monkeypatch):
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Xeric's talisman", 1)])
    positions = iter([{"x": 2674, "y": 3314, "plane": 0}, {"x": 1752, "y": 3566, "plane": 0}])
    monkeypatch.setattr(controller, "get_player", lambda: next(positions))
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    ctx = _Ctx()
    state = {"homeName": "MARIAH", "standingLocation": {"x": 1766, "y": 3621, "plane": 0}}
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data.get("mh_teleported_this_contract") is True


def test_withdraw_all_uses_one_shift_click_not_a_per_unit_loop(monkeypatch):
    """LIVE RUN 2026-08-28: this went through withdraw_loadout with quantity 28, which clicks
    once per unit -- 28 clicks at the booth for a withdrawal with room for ten, most of them
    skipped as off-window. withdraw_all_shift is one click, capped by free slots, and polls for
    arrival rather than sleeping."""
    calls = []
    monkeypatch.setattr(banker, "withdraw_all_shift",
                        lambda ctx, name, **kw: calls.append(name) or True)
    monkeypatch.setattr(banker, "withdraw_loadout",
                        lambda ctx, cfg, **kw: pytest.fail("per-unit click loop"))
    mh._withdraw_all(_Ctx(), "Teak plank")
    assert calls == ["Teak plank"]


def test_an_unreachable_hotspot_outside_a_split_floor_home_walks_instead_of_clicking(monkeypatch):
    """LIVE RUN 2026-08-28, the failure the user caught: stuck at (1771,3622) outside Mariah's
    house, clicking the Table at (1769,3622) every 3s forever. /objects returns the whole scene on
    the player's plane, so a hotspot behind a wall is perfectly visible -- and seeing the object is
    exactly what resets mh_work_stuck, so the safety stop never fires either.

    The routine never WALKED, which is why travel.py's door recovery never got a chance: it only
    runs inside a walk. So the unreachable guard must apply everywhere, not just to NOELLA, and
    outside a split-floor home its answer is "walk to the house", which is what opens the door."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(40002, distance=2, x=1769, y=3622)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1771, "y": 3622, "plane": 0})
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, entity, option, **kw: pytest.fail("MARIAH has no split floor"))
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.append(target) or True)

    spot = _hotspot(10554, 40002, 3, True, quantity=3)
    spot["plane"] = 0
    state = _work_state([spot], homeName="MARIAH",
                        standingLocation={"x": 1766, "y": 3621, "plane": 0})

    ctx = _Ctx()
    for _ in range(mh.HOTSPOT_UNREACHABLE_CLICKS + 2):
        ctx.state.data["mh_last_hotspot_click"] = 0.0
        mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert walked, "never walked -- so the door recovery could never run"
    assert walked[0] == {"x": 1766, "y": 3621, "plane": 0}


def test_walking_into_a_house_uses_a_tight_arrival_tolerance(monkeypatch):
    """LIVE 2026-08-28, second attempt: the walk to the standing tile returned True from
    (1766,3624) -- standing AGAINST the shut door, three tiles from the target, which is inside
    walk_to's default WALK_ARRIVAL_TOLERANCE of 3. underRoof was still False. A walk that
    "arrives" outside the door never stalls, so the door recovery never runs, and the next tick
    resumes clicking the unreachable hotspot: a loop, not a fix.

    Entering a building needs the walk to actually reach the tile, so it presses against the door
    and the stall recovery opens it."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [_item("Teak plank", 20), _item("Steel bar", 5)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(40002, distance=2, x=1769, y=3622)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1771, "y": 3622, "plane": 0})
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    kwargs = {}
    monkeypatch.setattr(travel, "walk_to",
                        lambda ctx, target, **kw: kwargs.update(kw) or True)

    spot = _hotspot(10554, 40002, 3, True, quantity=3)
    spot["plane"] = 0
    state = _work_state([spot], homeName="MARIAH",
                        standingLocation={"x": 1766, "y": 3621, "plane": 0})

    ctx = _Ctx()
    for _ in range(mh.HOTSPOT_UNREACHABLE_CLICKS + 2):
        ctx.state.data["mh_last_hotspot_click"] = 0.0
        mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert kwargs.get("tolerance") == mh.HOUSE_ARRIVAL_TOLERANCE
    assert kwargs.get("tolerance", 99) < 3, "default tolerance stops outside the door"


# --- the initial phase on a fresh process start ---------------------------------------
#
# CONFIRMED live 2026-08-28 (run 4): `ctx.state.data.get("mh_phase") or "bank"` meant every
# restart began in bank_phase. With a contract already in progress none of tick()'s branches
# override that ("bank" deliberately does NOT auto-promote, so work_phase's mid-contract
# diversion survives), so a routine restarted mid-contract went banking with a full inventory of
# planks -- from INSIDE Mariah's house, 20 tiles from the Hosidius bank through a wall. There is
# no diversion to preserve on a fresh start (ctx.state.data is empty by definition), so the
# initial phase must be derived from live state instead of assumed.

def _stock_the_inventory(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        _item(mh.DEFAULT_CONFIG["plankName"], mh.DEFAULT_CONFIG["restock"]["targetPlanks"]),
        _item("Steel bar", mh.DEFAULT_CONFIG["restock"]["targetSteelBars"]),
        _item("Varrock teleport", 8), _item("Ardougne teleport", 8),
        _item("Falador teleport", 8),
    ])
    monkeypatch.setattr(controller, "get_rune_pouch",
                        lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                           {"name": "Cosmic rune", "quantity": 1000}]})


def test_tick_on_a_fresh_start_mid_contract_goes_to_work_not_bank(monkeypatch):
    _stock_the_inventory(monkeypatch)
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 3})
    for phase in mh.PHASE_HANDLERS:
        monkeypatch.setitem(mh.PHASE_HANDLERS, phase,
                            lambda ctx, config, state, p=phase: ctx.state.data.setdefault("called", p))

    ctx = _Ctx()                      # no mh_phase -- exactly what a restart looks like
    mh.tick(ctx)

    assert ctx.state.data["mh_phase"] == "travel"
    assert ctx.state.data["called"] == "travel"


def test_tick_on_a_fresh_start_mid_contract_still_banks_when_genuinely_low(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 1)])
    monkeypatch.setattr(controller, "get_rune_pouch",
                        lambda: {"runes": [{"name": "Astral rune", "quantity": 1000},
                                           {"name": "Cosmic rune", "quantity": 1000}]})
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 3})
    for phase in mh.PHASE_HANDLERS:
        monkeypatch.setitem(mh.PHASE_HANDLERS, phase,
                            lambda ctx, config, state, p=phase: ctx.state.data.setdefault("called", p))

    ctx = _Ctx()
    mh.tick(ctx)

    assert ctx.state.data["mh_phase"] == "bank"
    assert ctx.state.data["called"] == "bank"


def test_tick_still_honours_an_explicit_bank_diversion_from_work_phase(monkeypatch):
    # The counterpart guard: a well-stocked inventory must NOT undo a diversion work_phase
    # already recorded, or the C3 livelock comes straight back.
    _stock_the_inventory(monkeypatch)
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                        lambda: {"hasContract": True, "contractTier": 3})
    for phase in mh.PHASE_HANDLERS:
        monkeypatch.setitem(mh.PHASE_HANDLERS, phase,
                            lambda ctx, config, state, p=phase: ctx.state.data.setdefault("called", p))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "bank"
    mh.tick(ctx)

    assert ctx.state.data["mh_phase"] == "bank"
    assert ctx.state.data["called"] == "bank"


# --- approaching a bank that is a room away -------------------------------------------
#
# banker.open_bank's own approach step (added the same day) minimap-hops toward a distant bank,
# and a minimap hop cannot open a closed door -- which is exactly what stands between this
# routine and the bank most of the time, since it banks from INSIDE the contract house. Only
# travel.walk_to carries the stall-triggered door recovery, so the approach belongs here.

def test_bank_phase_walks_to_a_bank_that_is_a_room_away(monkeypatch):
    _mock_bank(monkeypatch)
    monkeypatch.setattr(banker, "find_nearby_bank",
                        lambda config: {"id": 1, "name": "Bank booth", "distance": 21,
                                        "x": 1747, "y": 3598, "plane": 0})
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.append((target, kw)) or True)

    mh.bank_phase(_Ctx(), mh.DEFAULT_CONFIG, {"hasContract": True})

    assert walked, "a bank 21 tiles away must be walked to, not clicked at through a wall"
    target, kw = walked[0]
    assert (target["x"], target["y"], target["plane"]) == (1747, 3598, 0)
    # min_path_tiles no longer exists -- travel.walk_to routes off the global collision map
    # unconditionally now, so there is no short-hop/obstacle-blind split left to force past.
    assert "min_path_tiles" not in kw


def test_bank_phase_does_not_walk_to_a_bank_it_is_already_at(monkeypatch):
    _mock_bank(monkeypatch)
    monkeypatch.setattr(banker, "find_nearby_bank",
                        lambda config: {"id": 1, "name": "Bank booth", "distance": 2,
                                        "x": 1747, "y": 3598, "plane": 0})
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.append(target) or True)

    mh.bank_phase(_Ctx(), mh.DEFAULT_CONFIG, {"hasContract": True})

    assert walked == []


# --- confirming a teleport actually happened ------------------------------------------
#
# LIVE RUN 2026-08-28 (run 5, stopped by hand after it burned three Falador teleports):
# MEASURED from the recording, a tablet teleport takes 3.8s from the click to the region
# change (varbit 12393 -> anim 4069 -> anim 4071 -> spotanim 678 -> LOADING -> LOGGED_IN).
# The confirmation was a single human_delay(2.5, 3.2) and then one position read, which
# landed ~0.4s BEFORE the player arrived -- so a teleport that worked perfectly was recorded
# as "didn't take us anywhere". The retry then broke a second tablet in Falador to go to
# Falador, and a third, one per tick. Nothing capped it: the Falador tablet lands 74 tiles
# from Larry's standing tile, well past TRAVEL_TELEPORT_DISTANCE, so the only thing standing
# between a false negative and the whole stack of 155 tablets was the latch it had just failed
# to set.

def _teleport_harness(monkeypatch, positions, click_result=True):
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Falador teleport", 155)])
    monkeypatch.setattr(controller, "get_player", lambda: positions.pop(0) if len(positions) > 1 else positions[0])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(mh, "TELEPORT_CONFIRM_TIMEOUT_S", 0.4)
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: clicks.append(entity) or click_result)
    return clicks


HOSIDIUS = {"x": 1765, "y": 3621, "plane": 0}
FALADOR = {"x": 2964, "y": 3379, "plane": 0}
LARRY_TILE = {"x": 3038, "y": 3364, "plane": 0}


def test_travel_phase_waits_out_a_slow_teleport_instead_of_reclicking(monkeypatch):
    # The player only arrives on the FOURTH position read -- exactly the shape of the live
    # failure, where the single read fired 0.4s early.
    clicks = _teleport_harness(monkeypatch, [dict(HOSIDIUS), dict(HOSIDIUS),
                                             dict(HOSIDIUS), dict(FALADOR)])

    ctx = _Ctx()
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG,
                    {"homeName": "LARRY", "standingLocation": LARRY_TILE})

    assert len(clicks) == 1, f"one tablet per teleport, not {len(clicks)}"
    assert ctx.state.data.get("mh_teleported_this_contract") is True


def test_travel_phase_stops_burning_tablets_after_repeated_failures(monkeypatch):
    # Nothing moves, ever -- out of charges, or a dialogue eating every click. The attempt cap
    # is what turns "burn the whole stack, one per tick" into a diagnosable stop.
    clicks = _teleport_harness(monkeypatch, [dict(HOSIDIUS)])
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: pytest.fail("must not walk"))

    ctx = _Ctx()
    state = {"homeName": "LARRY", "standingLocation": LARRY_TILE}
    for _ in range(5):
        mh.travel_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert len(clicks) <= mh.TELEPORT_MAX_ATTEMPTS, \
        f"burned {len(clicks)} tablets on a teleport that never works"
    assert ctx.state.safety_stopped is True


def test_travel_phase_gives_each_new_contract_a_fresh_teleport_budget(monkeypatch):
    clicks = _teleport_harness(monkeypatch, [dict(HOSIDIUS)])

    ctx = _Ctx()
    for _ in range(mh.TELEPORT_MAX_ATTEMPTS):
        mh.travel_phase(ctx, mh.DEFAULT_CONFIG,
                        {"homeName": "LARRY", "standingLocation": LARRY_TILE})
    spent = len(clicks)

    ctx.state.safety_stopped = False
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG,
                    {"homeName": "TAU", "standingLocation": {"x": 2650, "y": 3283, "plane": 0}})

    assert len(clicks) > spent, "a new contract must get its own teleport attempts"


def test_travel_phase_walks_when_already_in_the_right_city(monkeypatch):
    """LIVE RUN 2026-08-28: the Falador tablet lands 74 tiles from Larry's standing tile, and
    TRAVEL_TELEPORT_DISTANCE was 40 -- so standing in Falador, already arrived, the gate still
    read "too far, teleport". Only the mh_teleported_this_contract latch stopped a second
    tablet, which is far too thin a thread: the moment the latch was wrong (it was), the
    routine teleported Falador -> Falador on a loop. The four destinations in CITY_TELEPORTS
    are 248+ tiles apart, so a threshold above the worst intra-city landing separates them
    cleanly."""
    clicks = _teleport_harness(monkeypatch, [dict(FALADOR)])
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.append(target) or True)

    ctx = _Ctx()
    mh.travel_phase(ctx, mh.DEFAULT_CONFIG,
                    {"homeName": "LARRY", "standingLocation": LARRY_TILE})

    assert clicks == [], "already in Falador -- a Falador teleport buys nothing"
    assert walked, "should have walked to Larry's tile instead"


# --- a cast that never produces a contract ---------------------------------------------
#
# LIVE RUN 2026-08-28 (run 8): the provider learns the active contract from chat, and after a
# client restart the only line the game offers is the DIALOG reminder it was dropping -- so
# hasContract stayed false and this phase re-cast NPC Contact once per tick, indefinitely, for a
# contract it already had. The provider bug is fixed; the unbounded loop it exposed is a second
# defect. Same shape as the teleport cap: a cast that changes nothing must be bounded.

def _contract_harness(monkeypatch):
    monkeypatch.setattr(controller, "key_tap", lambda ser, key: None)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": True,
                                                          "screenX": 10, "screenY": 10})
    casts = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: casts.append(entity) or True)
    return casts


def test_request_contract_stops_recasting_when_no_contract_ever_registers(monkeypatch):
    casts = _contract_harness(monkeypatch)

    ctx = _Ctx()
    for _ in range(mh.CONTACT_MAX_CASTS + 4):
        ctx.state.data["mh_contact_cast_at"] = 0.0     # skip the retry cooldown
        mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})

    assert len(casts) <= mh.CONTACT_MAX_CASTS, f"cast {len(casts)} times with nothing to show"
    assert ctx.state.safety_stopped is True


def test_request_contract_budget_resets_once_a_contract_arrives(monkeypatch):
    casts = _contract_harness(monkeypatch)

    ctx = _Ctx()
    for _ in range(mh.CONTACT_MAX_CASTS - 1):
        ctx.state.data["mh_contact_cast_at"] = 0.0
        mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})
    spent = len(casts)

    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": True})   # it landed

    ctx.state.data["mh_contact_cast_at"] = 0.0
    mh.request_contract_phase(ctx, mh.DEFAULT_CONFIG, {"hasContract": False})  # next contract

    assert len(casts) == spent + 1
    assert ctx.state.safety_stopped is False


# --- an unreachable hotspot is walked TO, not merely "toward the house" ----------------
#
# MEASURED live 2026-08-29 at NORMAN's, Falador. standingLocation is (3038,3344) and the shut
# door 24050 sits at (3037,3347) blocking the 3346|3347 crossing -- so the standing tile is on
# the SAME side of that door as the player. Walking there therefore crosses no doorway, the door
# recovery has nothing to resolve (correctly: nothing is between us and that tile), and we end up
# standing outside exactly as before. The hotspot is the thing we actually need to reach, and it
# is the goal that makes the doorway between us and it visible to the walker.

def test_work_phase_walks_to_the_hotspot_itself_when_it_cannot_be_reached(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 20)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(40089, distance=2)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3034, "y": 3341, "plane": 0})
    walked, doorways = [], []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.append(target) or True)
    monkeypatch.setattr(travel, "open_door_toward",
                        lambda ctx, goal: doorways.append(goal) or True)

    hotspot = _hotspot(10554, 40089, 3, True)
    hotspot["location"] = {"x": 3035, "y": 3346, "plane": 0}
    state = _work_state([hotspot])
    state["standingLocation"] = {"x": 3038, "y": 3344, "plane": 0}

    ctx = _Ctx()
    ctx.state.data["mh_hotspot_signature"] = (40089, 3)
    ctx.state.data["mh_hotspot_clicks"] = mh.HOTSPOT_UNREACHABLE_CLICKS
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert doorways and (doorways[0]["x"], doorways[0]["y"]) == (3035, 3346), (
        f"the DOOR must be resolved against the unreachable hotspot: {doorways}")
    assert walked and (walked[0]["x"], walked[0]["y"]) == (3038, 3344), (
        f"but the WALK must target a routable tile: {walked}")


def test_work_phase_falls_back_to_the_standing_tile_without_a_hotspot_location(monkeypatch):
    # `location` is null when the object is outside the loaded scene, and on an un-rebuilt client
    # the field does not exist at all -- keep the old target rather than walking nowhere.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 20)])
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_object(40089, distance=2)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3034, "y": 3341, "plane": 0})
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walked.append(target) or True)

    state = _work_state([_hotspot(10554, 40089, 3, True)])
    state["standingLocation"] = {"x": 3038, "y": 3344, "plane": 0}

    ctx = _Ctx()
    ctx.state.data["mh_hotspot_signature"] = (40089, 3)
    ctx.state.data["mh_hotspot_clicks"] = mh.HOTSPOT_UNREACHABLE_CLICKS
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert walked and (walked[0]["x"], walked[0]["y"]) == (3038, 3344)


# --- the tea option has to be actually clickable ---------------------------------------
#
# LIVE 2026-08-29: "accepting the offered cup of tea" fired on EVERY tick of an entire run, each
# time followed by "Skipping click on None -- aim point is outside the RuneLite window". The
# text match found a widget carrying "cuppa" that had no usable screen position -- a dialogue
# option that is present in the interface tree but not currently on screen. click_entity's own
# guard caught it every time, so nothing broke, but it is a wasted widget scan and a click
# attempt per tick, and it buried the real log lines.

def test_tea_is_not_clicked_when_the_option_has_no_screen_position(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump",
                        lambda: [{"group": mh.DIALOG_OPTION_GROUP, "text": "Yes, I'd love a cuppa.", "screenX": None, "screenY": None}])
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicked.append(e))
    logged = []
    monkeypatch.setattr(mh.debug_log, "log", lambda cat, msg: logged.append(msg))

    mh._accept_tea_if_offered(_Ctx())

    assert clicked == [], "an option with no screen position must not be clicked"
    assert not any("tea" in m for m in logged), f"and must not be announced: {logged}"


def test_tea_is_still_accepted_when_the_option_is_on_screen(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump",
                        lambda: [{"group": mh.DIALOG_OPTION_GROUP, "text": "Yes, I'd love a cuppa.", "screenX": 400, "screenY": 900}])
    clicked = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicked.append(e))

    mh._accept_tea_if_offered(_Ctx())

    assert clicked and clicked[0]["screenX"] == 400


# --- Remove and Build are not the same wait --------------------------------------------
#
# User, 2026-08-29: "the remove action is basically instant so we can just reclick after that,
# but after building there is a slight animation delay after exp drop where we can't click."
#
# That asymmetry is visible in the four hand-played recordings, and a single flat cooldown cannot
# express it. MEASURED over 427 consecutive hotspot actions (varbits 10554-10561, excluding the
# bulk contract-assignment writes):
#     median 2.39s   p25 0.60s   p75 3.00s
# p25 is exactly ONE game tick -- the Remove -> Build chain -- while p75 carries the build's
# animation tail. Ours ran a flat 3.80s median at TAU, ~1.6x slower than the human.
#
# A build target is named "<thing> space" ("Drawer space", "Table space", "Bookcase space" in our
# own run logs); anything else is a Remove or a Repair.

def test_only_remove_is_treated_as_instant():
    """User, 2026-08-29: "repair should be similar to build i believe" -- so Remove is the ONLY
    instant one. The object's own left-click action says which it is (actions[0], impostor-
    resolved by ObjectProvider), which is the one signal that separates Repair from Remove:
    both act on named furniture, while only Build acts on a "<thing> space"."""
    assert mh._is_instant_action({"name": "Drawers", "actions": ["Remove"]}) is True
    assert mh._is_instant_action({"name": "Drawer space", "actions": ["Build"]}) is False
    assert mh._is_instant_action({"name": "Broken Sink", "actions": ["Repair"]}) is False
    assert mh._is_instant_action({"name": "Hat stand", "actions": ["Repair"]}) is False
    # No actions field (older client build): fall back to the name, and default to the SLOW
    # wait -- clicking too early loses the click outright, waiting merely costs a moment.
    assert mh._is_instant_action({"name": "Drawer space"}) is False
    assert mh._is_instant_action({}) is False


def test_removing_uses_the_short_cooldown_and_building_the_long_one(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 20)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})

    for name, actions, expected in (("Drawers", ["Remove"], mh.HOTSPOT_CLICK_COOLDOWN_S),
                                    ("Drawer space", ["Build"], mh.HOTSPOT_BUILD_COOLDOWN_S),
                                    ("Broken Sink", ["Repair"], mh.HOTSPOT_BUILD_COOLDOWN_S)):
        # distance=0: standing on it, so the walk allowance contributes nothing and the BASE
        # shows through. The walk term itself is covered by its own tests below.
        obj = _object(39989, distance=0)
        obj["name"] = name
        obj["actions"] = actions
        monkeypatch.setattr(controller, "get_objects", lambda **kw: [obj])
        ctx = _Ctx()
        mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
        assert ctx.state.data["mh_hotspot_cooldown"] == expected, \
            f"{name!r} should wait {expected}s, got {ctx.state.data.get('mh_hotspot_cooldown')}"


def test_the_remove_cooldown_is_about_one_game_tick():
    # The human's p25. Faster than this would be clicking into an action the game has not
    # accepted yet; slower throws away the whole Remove -> Build chain.
    assert 0.5 <= mh.HOTSPOT_CLICK_COOLDOWN_S <= 1.0
    assert mh.HOTSPOT_BUILD_COOLDOWN_S > mh.HOTSPOT_CLICK_COOLDOWN_S


# --- the tea dialogue must be CLOSED, not clicked at forever ---------------------------
#
# CONFIRMED live 2026-08-29 (run16, user: "clicking back and forth not sure wtf is going on").
# After Barbara's contract completed, the routine alternated corner to corner every tick:
#     accepting the offered cup of tea -> click (571,1173)        bottom-left
#     turning in the contract with Barbara -> right-click (2010,172)   top-right
#     No 'Talk-to' entry in menu for Barbara (['Cancel','Mark Safespot','Walk here'])
# The tea option was clicked unbounded with no confirmation and no cooldown, and because the
# dialogue stayed open it also blocked the Talk-to menu, so every turn-in right-click resolved to
# bare ground -- at a different screen position each time as the camera swung. Two faults, one
# visible symptom.

def test_tea_is_not_reclicked_within_its_cooldown(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump",
                        lambda: [{"group": mh.DIALOG_OPTION_GROUP, "text": "Yes, I'd love a cuppa.", "screenX": 571, "screenY": 1173}])
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicks.append(e))

    ctx = _Ctx()
    for _ in range(6):
        mh._accept_tea_if_offered(ctx)

    assert len(clicks) == 1, f"one click per cooldown, not {len(clicks)} in a row"


def test_handling_the_tea_dialogue_stops_the_rest_of_the_tick(monkeypatch):
    """An open dialogue eats the Talk-to menu, so clicking an NPC across the screen in the same
    tick can only ever miss -- and it is what made the corner-to-corner clicking visible."""
    monkeypatch.setattr(controller, "get_widgets_dump",
                        lambda: [{"group": mh.DIALOG_OPTION_GROUP, "text": "Yes, I'd love a cuppa.", "screenX": 571, "screenY": 1173}])
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    assert mh._accept_tea_if_offered(_Ctx()) is True


def test_no_tea_on_offer_lets_the_tick_continue(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump", lambda: [])
    assert mh._accept_tea_if_offered(_Ctx()) is False


# --- "no hotspots need attention" is not the same as "the contract is done" -------------
#
# CONFIRMED live 2026-08-29 (user: "we havent done a single task in barbara yet" while the
# routine was repeatedly trying to hand the contract in). Standing at (1739,3549), OUTSIDE
# Barbara's area (x 1746-1756, y 3531-3542), every hotspot varbit read 0:
#     hasContract true, homeName BARBARA, remaining 0 / 7, all values 0, needsAttention false
# A value of 0 there means "no data" -- the varbits carry the contract's state only while the
# house is loaded -- and work_phase read it as "nothing left to do" and advanced to turn_in.
#
# The two cases are distinguishable by POSITION: a genuinely finished contract is finished while
# we are standing in the house we just worked. An empty hotspot list from across the map is the
# routine having no idea, and the answer to that is to go there, not to claim the reward.

def _far_from(home_area):
    return {"x": home_area["x"] - 8, "y": home_area["y"] + 18, "plane": 0}


def test_an_empty_hotspot_list_from_outside_the_house_is_not_a_finished_contract(monkeypatch):
    area = {"x": 1746, "y": 3531, "width": 10, "height": 11, "plane": 0}
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1739, "y": 3549, "plane": 0})

    ctx = _Ctx()
    state = _work_state([])
    state["area"] = area
    state["standingLocation"] = {"x": 1750, "y": 3534, "plane": 0}
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data["mh_phase"] != "turn_in", \
        "standing across the map with no hotspot data is not proof the contract is complete"
    assert ctx.state.data["mh_phase"] == "travel"


def test_an_empty_hotspot_list_inside_the_house_does_finish_the_contract(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1750, "y": 3535, "plane": 0})

    ctx = _Ctx()
    state = _work_state([])
    state["area"] = {"x": 1746, "y": 3531, "width": 10, "height": 11, "plane": 0}
    state["standingLocation"] = {"x": 1750, "y": 3534, "plane": 0}
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.data["mh_phase"] == "turn_in"


def test_a_missing_area_falls_back_to_the_standing_tile(monkeypatch):
    # `area` is only on a rebuilt client; distance to standingLocation answers the same question.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1750, "y": 3535, "plane": 0})
    ctx = _Ctx()
    state = _work_state([])
    state["standingLocation"] = {"x": 1750, "y": 3534, "plane": 0}
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert ctx.state.data["mh_phase"] == "turn_in"
def test_a_climb_that_changed_floor_clears_the_not_reachable_counter(monkeypatch):
    # LIVE 2026-08-29 at MARIAH's: the corner rotation found a working corner on its THIRD
    # attempt and the Climb-down went through -- then the safety stop fired two ticks later,
    # because three attempts at LADDER_CLICK_COOLDOWN_S apiece had already spent the 15-tick
    # budget. Changing floor is progress and must clear the counter.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1766, "y": 3621, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])
    monkeypatch.setattr(travel, "walk_to", lambda *a, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_work_stuck"] = 14          # one tick short of STUCK_TICK_LIMIT
    ctx.state.data["mh_ladder_click_plane"] = 1   # we clicked the ladder while on plane 1

    state = {"homeName": "MARIAH", "standingLocation": {"x": 1766, "y": 3621, "plane": 0},
             "ladderObjectIds": [11802]}
    target = {"objectId": 40289, "location": {"x": 1764, "y": 3623, "plane": 0}, "plane": 0}
    mh._approach_missing_hotspot(ctx, mh.DEFAULT_CONFIG, state, target)

    assert ctx.state.safety_stopped is False, "a climb that changed floor must not safety-stop"
    assert ctx.state.data["mh_work_stuck"] == 1


def test_a_climb_that_did_not_change_floor_still_counts_toward_the_stop(monkeypatch):
    # The other half: keying the reset on the menu click being taken rather than on the plane
    # actually changing would let an up-then-down loop reset the counter forever.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1766, "y": 3621, "plane": 1})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])
    monkeypatch.setattr(travel, "walk_to", lambda *a, **kw: True)

    ctx = _Ctx()
    ctx.state.data["mh_work_stuck"] = 14
    ctx.state.data["mh_ladder_click_plane"] = 1   # same plane we are still on -- no progress

    state = {"homeName": "MARIAH", "standingLocation": {"x": 1766, "y": 3621, "plane": 0},
             "ladderObjectIds": [11802]}
    target = {"objectId": 40289, "location": {"x": 1764, "y": 3623, "plane": 0}, "plane": 0}
    mh._approach_missing_hotspot(ctx, mh.DEFAULT_CONFIG, state, target)

    assert ctx.state.safety_stopped is True, "no floor change means no progress -- the stop must still fire"


def test_the_climb_marker_survives_the_tick_before_the_descent_lands(monkeypatch):
    # A climb takes a tick or two to land, so the tick right after the click still reports the
    # old plane. LIVE 2026-08-29: consuming the marker on that tick lost the descent entirely --
    # the player reached plane 0 and the run safety-stopped anyway.
    plane = {"now": 1}
    monkeypatch.setattr(controller, "get_player",
                         lambda: {"x": 1766, "y": 3621, "plane": plane["now"]})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])
    monkeypatch.setattr(travel, "walk_to", lambda *a, **kw: True)
    monkeypatch.setattr(mh, "_climb", lambda *a, **kw: None)

    ctx = _Ctx()
    ctx.state.data["mh_work_stuck"] = 5
    ctx.state.data["mh_ladder_click_plane"] = 1

    state = {"homeName": "MARIAH", "standingLocation": {"x": 1766, "y": 3621, "plane": 0},
             "ladderObjectIds": [11802]}
    target = {"objectId": 40289, "location": {"x": 1764, "y": 3623, "plane": 0}, "plane": 0}

    mh._approach_missing_hotspot(ctx, mh.DEFAULT_CONFIG, state, target)   # still on plane 1
    assert ctx.state.data["mh_ladder_click_plane"] == 1, "marker must survive an in-flight climb"

    plane["now"] = 0                                                      # the descent lands
    mh._approach_missing_hotspot(ctx, mh.DEFAULT_CONFIG, state, target)
    assert ctx.state.data["mh_work_stuck"] == 1, "the landed climb must clear the counter"
    assert ctx.state.data["mh_ladder_click_plane"] is None


def test_turn_in_goes_back_to_work_when_hotspots_still_need_attention(monkeypatch):
    # LIVE 2026-08-29 at ROSS's: the phase latched to "turn_in" one tick after a single hotspot
    # was built, then talked to Ross once every ~3s indefinitely while SIX of his seven hotspots
    # still reported needsAttention. Nothing re-checked, so it could not recover on its own.
    monkeypatch.setattr(controller, "get_npc_nearest",
                         lambda npc_id: pytest.fail("must not talk while work remains"))
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda *a, **kw: pytest.fail("must not talk while work remains"))

    ctx = _Ctx()
    state = {"npcId": 10420, "npcName": "Ross", "homeName": "ROSS",
             "standingLocation": {"x": 100, "y": 100, "plane": 0},
             "area": {"x": 95, "y": 95, "width": 10, "height": 10, "plane": 0},
             "hotspots": [_hotspot(10554, 40164, 1, False),
                          _hotspot(10556, 40165, 3, True)]}
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 100, "y": 100, "plane": 0})

    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert ctx.state.data["mh_phase"] == "work"


def test_turn_in_still_talks_when_the_contract_really_is_done(monkeypatch):
    monkeypatch.setattr(controller, "get_npc_nearest", lambda npc_id: {"index": 5, "name": "Ross"})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 100, "y": 100, "plane": 0})
    talked = {}
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda ser, npc, option, **kw: talked.setdefault("option", option))

    ctx = _Ctx()
    state = {"npcId": 10420, "npcName": "Ross", "homeName": "ROSS",
             "standingLocation": {"x": 100, "y": 100, "plane": 0},
             "area": {"x": 95, "y": 95, "width": 10, "height": 10, "plane": 0},
             "hotspots": [_hotspot(10554, 40164, 7, False)]}
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert talked["option"] == "Talk-to"


def test_turn_in_has_a_ceiling_not_just_a_cooldown(monkeypatch):
    # A cooldown only sets the RATE of an unbounded loop -- same lesson as the teleport tablet
    # and NPC Contact caps.
    monkeypatch.setattr(controller, "get_npc_nearest", lambda npc_id: {"index": 5, "name": "Ross"})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 100, "y": 100, "plane": 0})
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **kw: True)

    ctx = _Ctx()
    state = {"npcId": 10420, "npcName": "Ross", "homeName": "ROSS",
             "standingLocation": {"x": 100, "y": 100, "plane": 0},
             "area": {"x": 95, "y": 95, "width": 10, "height": 10, "plane": 0},
             "hotspots": [_hotspot(10554, 40164, 7, False)]}

    for _ in range(mh.TURN_IN_MAX_CLICKS + 2):
        ctx.state.data["mh_last_turnin_click"] = 0.0     # bypass the cooldown, not the ceiling
        mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.safety_stopped is True
    assert "Ross" in ctx.state.safety_reason


def test_work_phase_opens_a_shut_door_before_its_first_hotspot_click(monkeypatch):
    # USER 2026-08-29: "why when we come to the house do we not prioritise opening the god danm
    # door, should be the first thing we click if its closed". /objects returns the whole scene
    # on our plane, so a hotspot behind a shut front door is VISIBLE and used to be clicked
    # straight away -- the door was only resolved by the unreachable branch, five wasted clicks
    # later. MEASURED live at MARIAH's: exactly that, twice in one contract.
    order = []
    monkeypatch.setattr(travel, "open_door_toward",
                         lambda ctx, goal: order.append(("door", goal["x"], goal["y"])))
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40002, distance=2, x=1769, y=3622)])
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: order.append(("click", entity["id"])) or True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1766, "y": 3620, "plane": 0})

    hs = _hotspot(10554, 40002, 1, True)
    hs["location"] = {"x": 1769, "y": 3622, "plane": 0}
    state = {"homeName": "MARIAH", "npcId": 10422,
             "standingLocation": {"x": 1766, "y": 3621, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert order and order[0] == ("door", 1769, 3622), \
        f"the door must be resolved before the first click, got {order}"


def test_the_door_is_not_re_resolved_on_every_click_of_the_same_hotspot(monkeypatch):
    doors = []
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: doors.append(goal))
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40002, distance=2, x=1769, y=3622)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1766, "y": 3620, "plane": 0})

    hs = _hotspot(10554, 40002, 1, True)
    hs["location"] = {"x": 1769, "y": 3622, "plane": 0}
    state = {"homeName": "MARIAH", "npcId": 10422,
             "standingLocation": {"x": 1766, "y": 3621, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    for _ in range(4):
        ctx.state.data["mh_last_hotspot_click"] = 0.0     # bypass the click cooldown
        mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert len(doors) == 1, f"door check must run once per hotspot state, ran {len(doors)}x"


def test_climb_clicks_the_ladders_own_reported_centre(monkeypatch):
    # USER 2026-08-29: "revert the ladder to click center". A tile-corner aim was tried and
    # reverted the same day -- it broke ladders that already worked. MEASURED: at MARIAH's the
    # reported centroid and the tile centre were the SAME point ((1249,709) vs (1249,707)), i.e.
    # bare floor; at ROSS's, ladder 16683 reports (1263,316) against a tile centre of (1360,386)
    # and the centroid is squarely ON the model. An unconditional override aimed a good click
    # away from its target.
    monkeypatch.setattr(controller, "get_ground_walk_point",
                         lambda x, y, plane: pytest.fail("must not derive a corner any more"))
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: False)
    seen = {}
    def _menu(ser, entity, option, **kw):
        seen["entity"] = entity
        seen["offset"] = kw.get("offset")
        return True
    monkeypatch.setattr(controller, "click_menu_action", _menu)

    ctx = _Ctx()
    ladder = {"id": 16683, "name": "Ladder", "x": 2617, "y": 3315, "plane": 0,
              "screenX": 1263, "screenY": 316}
    mh._climb(ctx, ladder, "Climb-up", "why")

    assert seen["entity"]["screenX"] == 1263 and seen["entity"]["screenY"] == 316
    # offset=(0, 0) is what makes it the CENTRE -- the default (-7, +7) nudges off it.
    assert seen["offset"] == (0, 0)


def test_turn_in_climbs_down_instead_of_walking_at_a_tile_on_another_floor(monkeypatch):
    # LIVE 2026-08-29 at ROSS's, USER-REPORTED as "stuck upstairs": every hotspot was built and
    # the player was still on plane 1, but standingLocation is always plane 0. WALKING CANNOT
    # CHANGE FLOOR, so the walker clicked the identical pixel (1144,724) about thirty times with
    # the player never moving. work_phase's ladder handling could not help -- it lives in
    # _approach_missing_hotspot, and with no hotspots left work_phase never runs.
    monkeypatch.setattr(controller, "get_npc_nearest", lambda npc_id: None)   # downstairs
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2618, "y": 3318, "plane": 1})
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [{"id": 16683, "name": "Ladder", "distance": 3,
                                               "x": 2617, "y": 3315, "plane": 1,
                                               "screenX": 500, "screenY": 500}])
    monkeypatch.setattr(travel, "walk_to",
                         lambda *a, **kw: pytest.fail("must not walk at a plane-0 tile from plane 1"))
    climbed = {}
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda ser, entity, option, **kw: climbed.setdefault("option", option))

    ctx = _Ctx()
    state = {"npcId": 10420, "npcName": "Ross", "homeName": "ROSS", "npcUpstairs": False,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "ladderObjectIds": [16683], "hotspots": []}
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert climbed.get("option") == "Climb-down"


def test_turn_in_climbs_UP_for_a_homeowner_who_is_upstairs(monkeypatch):
    # JESS and NORMAN are the two homes where npcUpstairs is true, so the direction is not a
    # constant -- standingLocation being plane 0 cannot answer this on its own.
    monkeypatch.setattr(controller, "get_npc_nearest", lambda npc_id: None)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2618, "y": 3318, "plane": 0})
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [{"id": 16683, "name": "Ladder", "distance": 3,
                                               "x": 2617, "y": 3315, "plane": 0,
                                               "screenX": 500, "screenY": 500}])
    monkeypatch.setattr(travel, "walk_to", lambda *a, **kw: pytest.fail("should climb, not walk"))
    climbed = {}
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda ser, entity, option, **kw: climbed.setdefault("option", option))

    ctx = _Ctx()
    state = {"npcId": 10419, "npcName": "Jess", "homeName": "JESS", "npcUpstairs": True,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "ladderObjectIds": [16683], "hotspots": []}
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert climbed.get("option") == "Climb-up"


def test_turn_in_still_walks_when_the_homeowner_is_on_our_own_floor(monkeypatch):
    # Same floor, just not rendered yet -- walking is the right answer and must be preserved.
    monkeypatch.setattr(controller, "get_npc_nearest", lambda npc_id: None)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2650, "y": 3350, "plane": 0})
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda *a, **kw: pytest.fail("must not climb on our own floor"))
    walked = {}
    monkeypatch.setattr(travel, "walk_to",
                         lambda ctx, goal, **kw: walked.setdefault("goal", goal))

    ctx = _Ctx()
    state = {"npcId": 10420, "npcName": "Ross", "homeName": "ROSS", "npcUpstairs": False,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "ladderObjectIds": [16683], "hotspots": []}
    mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert walked["goal"]["x"] == 2613


def test_the_cross_floor_turn_in_cannot_loop_forever(monkeypatch):
    # An unbounded climb loop is exactly what this phase must not be able to do.
    monkeypatch.setattr(controller, "get_npc_nearest", lambda npc_id: None)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2618, "y": 3318, "plane": 1})
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [{"id": 16683, "name": "Ladder", "distance": 3,
                                               "x": 2617, "y": 3315, "plane": 1,
                                               "screenX": 500, "screenY": 500}])
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **kw: True)
    monkeypatch.setattr(travel, "walk_to", lambda *a, **kw: True)

    ctx = _Ctx()
    state = {"npcId": 10420, "npcName": "Ross", "homeName": "ROSS", "npcUpstairs": False,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "ladderObjectIds": [16683], "hotspots": []}
    for _ in range(mh.TURN_IN_MAX_CLICKS + 2):
        ctx.state.data["mh_last_ladder_click"] = 0.0     # bypass the cooldown, not the ceiling
        mh.turn_in_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert ctx.state.safety_stopped is True
def test_the_unreachable_walk_targets_a_tile_on_our_own_floor(monkeypatch):
    # MEASURED live 2026-08-29 at JESS's, where all seven hotspots are on plane 1: the walk goal
    # was standingLocation, which is ALWAYS reported at plane 0, so Shortest Path answered with a
    # "Climb-down Ladder" TRANSPORT and carried the player DOWN, away from the work. Log read
    # "using transport: Climb-down Ladder" -> "used the transport but didn't arrive where
    # expected" -> "target is on a different plane -- can't walk there yet", then repeated.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40171, distance=2, x=2613, y=3296)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2622, "y": 3293, "plane": 1})
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: False)
    walked = {}
    monkeypatch.setattr(travel, "walk_to",
                         lambda ctx, goal, **kw: walked.setdefault("goal", goal))

    hs = _hotspot(10554, 40171, 3, True)
    hs["location"] = {"x": 2613, "y": 3296, "plane": 1}      # upstairs, like us
    state = {"homeName": "JESS", "npcId": 10419,
             "standingLocation": {"x": 2620, "y": 3300, "plane": 0},   # always plane 0
             "hotspots": [hs]}

    ctx = _Ctx()
    ctx.state.data["mh_hotspot_clicks"] = mh.HOTSPOT_UNREACHABLE_CLICKS
    ctx.state.data["mh_hotspot_signature"] = (40171, 3)
    ctx.state.data["mh_door_checked"] = (40171, 3)
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert walked["goal"]["plane"] == 1, \
        f"must walk to a tile on our own floor, got {walked['goal']}"
    assert walked["goal"]["x"] == 2613


def test_the_unreachable_walk_still_uses_the_standing_tile_from_outside(monkeypatch):
    # The original case must be preserved: standing outside on plane 0 with a plane-0 hotspot,
    # the standing tile is the routable goal and the hotspot's own tile usually is not.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40002, distance=2, x=1769, y=3622)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1760, "y": 3620, "plane": 0})
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: False)
    walked = {}
    monkeypatch.setattr(travel, "walk_to",
                         lambda ctx, goal, **kw: walked.setdefault("goal", goal))

    hs = _hotspot(10554, 40002, 1, True)
    hs["location"] = {"x": 1769, "y": 3622, "plane": 0}
    state = {"homeName": "MARIAH", "npcId": 10422,
             "standingLocation": {"x": 1766, "y": 3621, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    ctx.state.data["mh_hotspot_clicks"] = mh.HOTSPOT_UNREACHABLE_CLICKS
    ctx.state.data["mh_hotspot_signature"] = (40002, 1)
    ctx.state.data["mh_door_checked"] = (40002, 1)
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    # The STANDING tile, not the hotspot: on our own floor the hotspot's own tile is usually
    # furniture and unroutable, which is the whole reason the standing tile is the walk goal.
    assert walked["goal"]["x"] == 1766 and walked["goal"]["plane"] == 0


def test_a_finished_contract_is_turned_in_before_banking(monkeypatch):
    # USER 2026-08-29: "another big nono was finishing barbaras house then before handing in the
    # contract go to bank and then back again wasting a tp ... finish the contract first then
    # bank". MEASURED live at Barbara's: every hotspot built, and it still teleported to the
    # Varrock bank and back before turning in -- a full round trip plus a tablet for nothing.
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                         lambda: {"hasContract": True, "contractTier": 3, "homeName": "BARBARA",
                                  "npcId": 10416, "npcName": "Barbara",
                                  "standingLocation": {"x": 1750, "y": 3534, "plane": 0},
                                  "area": {"x": 1745, "y": 3530, "width": 12, "height": 10,
                                           "plane": 0},
                                  "hotspots": [_hotspot(10554, 40011, 7, False)]})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1750, "y": 3534, "plane": 0})
    monkeypatch.setattr(mh, "needs_bank_trip", lambda ctx, config, state: True)   # low on planks
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})

    called = []
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank", lambda *a: called.append("bank"))
    monkeypatch.setitem(mh.PHASE_HANDLERS, "turn_in", lambda *a: called.append("turn_in"))
    monkeypatch.setitem(mh.PHASE_HANDLERS, "work", lambda *a: called.append("work"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "work"
    mh.tick(ctx)

    assert "bank" not in called, f"must not bank while a finished contract is unclaimed: {called}"


def test_still_banks_mid_contract_when_work_remains(monkeypatch):
    # The other half: genuinely running out of planks part-way through must still divert.
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                         lambda: {"hasContract": True, "contractTier": 3, "homeName": "BARBARA",
                                  "npcId": 10416, "npcName": "Barbara",
                                  "standingLocation": {"x": 1750, "y": 3534, "plane": 0},
                                  "area": {"x": 1745, "y": 3530, "width": 12, "height": 10,
                                           "plane": 0},
                                  "hotspots": [_hotspot(10554, 40011, 3, True)]})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1750, "y": 3534, "plane": 0})
    monkeypatch.setattr(mh, "needs_bank_trip", lambda ctx, config, state: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})

    called = []
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank", lambda *a: called.append("bank"))
    monkeypatch.setitem(mh.PHASE_HANDLERS, "work", lambda *a: called.append("work"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "work"
    mh.tick(ctx)

    assert called == ["bank"], f"a real mid-contract shortage must still bank: {called}"


def test_a_shortage_from_out_of_town_still_banks(monkeypatch):
    # Away from the house every hotspot varbit reads "nothing needs attention", so the
    # finished-contract check must be gated on actually being AT the home -- otherwise it would
    # suppress every legitimate restock made from out of town.
    monkeypatch.setattr(controller, "get_mahogany_homes_state",
                         lambda: {"hasContract": True, "contractTier": 3, "homeName": "BARBARA",
                                  "npcId": 10416, "npcName": "Barbara",
                                  "standingLocation": {"x": 1750, "y": 3534, "plane": 0},
                                  "area": {"x": 1745, "y": 3530, "width": 12, "height": 10,
                                           "plane": 0},
                                  "hotspots": [_hotspot(10554, 40011, 0, False)]})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 3200, "y": 3430, "plane": 0})
    monkeypatch.setattr(mh, "needs_bank_trip", lambda ctx, config, state: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_rune_pouch", lambda: {"runes": []})

    called = []
    monkeypatch.setitem(mh.PHASE_HANDLERS, "bank", lambda *a: called.append("bank"))
    monkeypatch.setitem(mh.PHASE_HANDLERS, "work", lambda *a: called.append("work"))

    ctx = _Ctx()
    ctx.state.data["mh_phase"] = "work"
    mh.tick(ctx)

    assert called == ["bank"], f"a shortage away from the house must still bank: {called}"


def test_a_door_miss_does_not_stop_the_walk_that_follows(monkeypatch):
    # USER 2026-08-29: "got stuck outside without opening door again". Outside ROSS's house the
    # only way in is a DETOUR door -- both player and hotspot east of its blocked edge -- so a
    # straight-line edge test alone would find nothing.
    #
    # REWRITTEN for the global-collision-pathfinding migration: this used to test a two-tier
    # fallback (a strict open_doorway_to, then a blunt open_nearest_shut_door reached only when
    # the first found nothing) that travel.open_door_toward's docstring says it replaces
    # outright -- it routes off the global map and opens whichever door actually lies on that
    # route, so there is no longer a second tier to fall back TO. What is left to prove is that
    # the stuck branch still calls the door opener at all (a False return -- "found no door" --
    # must not be treated as fatal and must not stop the walk that follows).
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40164, distance=3, x=2617, y=3318)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2614, "y": 3319, "plane": 0})
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda *a, **kw: walked.append(True) or True)
    opened = []
    monkeypatch.setattr(travel, "open_door_toward",
                         lambda ctx, goal: opened.append(goal) or False)   # found nothing

    hs = _hotspot(10554, 40164, 1, True)
    hs["location"] = {"x": 2617, "y": 3318, "plane": 0}
    state = {"homeName": "ROSS", "npcId": 10420,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    ctx.state.data["mh_hotspot_clicks"] = mh.HOTSPOT_UNREACHABLE_CLICKS
    ctx.state.data["mh_hotspot_signature"] = (40164, 1)
    ctx.state.data["mh_door_checked"] = (40164, 1)
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert opened, "a stuck hotspot must still try to open a door"
    assert walked, "a door miss (False) must not stop the walk that follows"


def test_a_door_hit_does_not_stop_the_walk_that_follows(monkeypatch):
    # Renamed (final review, "misleading test names / one weak assertion"): the old name claimed
    # the walk is SKIPPED when a door was found -- the opposite of what this test actually
    # asserts (`assert walked`, i.e. the walk still happens). REWRITTEN for the same reason as
    # the test above: the "strict test staying primary" this docstring used to describe was about
    # not second-guessing a found door with a second call -- there is only one call now, so the
    # thing left to prove is the mirror case, a True return (a door WAS found and opened) also
    # does not stop the walk that follows.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40164, distance=3, x=2617, y=3318)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2614, "y": 3319, "plane": 0})
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda *a, **kw: walked.append(True) or True)
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: True)    # door found + opened

    hs = _hotspot(10554, 40164, 1, True)
    hs["location"] = {"x": 2617, "y": 3318, "plane": 0}
    state = {"homeName": "ROSS", "npcId": 10420,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    ctx.state.data["mh_hotspot_clicks"] = mh.HOTSPOT_UNREACHABLE_CLICKS
    ctx.state.data["mh_hotspot_signature"] = (40164, 1)
    ctx.state.data["mh_door_checked"] = (40164, 1)
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert walked, "opening a door must still be followed by the walk"


def test_the_click_cooldown_budgets_the_walk_to_the_hotspot(monkeypatch):
    # USER 2026-08-29: "are we checking if we click remove on a furniture but we are a few tiles
    # away do we count that as removed before we arrive?" -- we were. The game QUEUES an
    # interaction and performs it on ARRIVAL, so until the player gets there the varbit cannot
    # move. With only the action's own duration budgeted, the cooldown expired mid-walk, we
    # re-clicked, every such click counted as "no progress", and HOTSPOT_UNREACHABLE_CLICKS fired
    # on a perfectly reachable hotspot. MEASURED live at ROSS's: five `Repair Broken Range`
    # actions all registering correctly in the recorder, no varbit movement, then "unreachable".
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 20)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})

    obj = _object(39989, distance=5)
    obj["name"] = "Drawers"
    obj["actions"] = ["Remove"]
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [obj])

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))

    cooldown = ctx.state.data["mh_hotspot_cooldown"]
    assert cooldown > mh.HOTSPOT_CLICK_COOLDOWN_S, \
        f"5 tiles of walking must be budgeted, got {cooldown}s"
    assert cooldown == pytest.approx(
        mh.HOTSPOT_CLICK_COOLDOWN_S + 5 * mh.HOTSPOT_WALK_ALLOWANCE_S)


def test_the_click_cooldown_is_capped_for_a_far_hotspot(monkeypatch):
    # Capped so a hotspot that genuinely cannot be reached still reaches the unreachable guard.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 20)])
    monkeypatch.setattr(controller, "object_refresh_target", lambda x, y, plane, obj_id: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})

    obj = _object(39989, distance=40)
    obj["name"] = "Drawer space"
    obj["actions"] = ["Build"]
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [obj])

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, _work_state([_hotspot(10554, 39989, 3, True)]))
    assert ctx.state.data["mh_hotspot_cooldown"] == mh.HOTSPOT_COOLDOWN_MAX_S


def test_an_unreachable_hotspot_is_never_clicked_at_all(monkeypatch):
    # USER 2026-08-29: "every time we have to walk through a door now we have to get stuck on the
    # outside 5 clicks in a row this is unacceptable." Reachability is decided BEFORE the first
    # click now, so the five-wasted-click discovery cannot happen.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40164, distance=3, x=2617, y=3318)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2614, "y": 3319, "plane": 0})
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: pytest.fail("must not click what we cannot reach"))
    monkeypatch.setattr(travel, "can_reach", lambda goal, reach=None, **kw: False)
    opened, walked = [], []
    monkeypatch.setattr(travel, "open_door_toward",
                         lambda ctx, goal: opened.append(goal) or True)
    monkeypatch.setattr(travel, "walk_to", lambda ctx, goal, **kw: walked.append(goal) or True)

    hs = _hotspot(10554, 40164, 1, True)
    hs["location"] = {"x": 2617, "y": 3318, "plane": 0}
    state = {"homeName": "ROSS", "npcId": 10420,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert opened, "an unreachable hotspot must resolve the door standing in the way"
    assert walked, "...and then walk"
    assert ctx.state.data.get("mh_hotspot_clicks", 0) == 0, "no click may be spent"


def test_a_reachable_hotspot_is_clicked_immediately(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40164, distance=1, x=2617, y=3318)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2617, "y": 3317, "plane": 0})
    monkeypatch.setattr(travel, "can_reach", lambda goal, reach=None, **kw: True)
    # A reachable hotspot skips the unreachable-branch escalation entirely (can_reach is not
    # False), but the pre-click door check still runs once per hotspot state regardless of
    # reachability -- see work_phase's own comment: "costs nothing when the way is already
    # clear". So open_door_toward IS still called here; it just must not block the click.
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: False)
    monkeypatch.setattr(controller, "object_refresh_target",
                         lambda x, y, plane, obj_id: (lambda: None))
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: clicked.append(entity["id"]) or True)

    hs = _hotspot(10554, 40164, 1, True)
    hs["location"] = {"x": 2617, "y": 3318, "plane": 0}
    state = {"homeName": "ROSS", "npcId": 10420,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    # A hotspot click is a BURST now, not one click -- see HOTSPOT_SPAM_GAP_MIN_S for the human
    # measurement. What matters here is that a reachable hotspot is acted on at once, and that
    # every click of the burst goes to the SAME object.
    assert clicked, "a reachable hotspot must be clicked immediately"
    assert set(clicked) == {40164}
    assert len(clicked) <= mh.HOTSPOT_SPAM_MAX_CLICKS


def test_an_unknown_reachability_answer_still_clicks(monkeypatch):
    # None means the client could not tell (older build, tile outside the scene). That must
    # behave exactly as before rather than refusing to work.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40164, distance=1, x=2617, y=3318)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2617, "y": 3317, "plane": 0})
    monkeypatch.setattr(travel, "can_reach", lambda goal, reach=None, **kw: None)
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: False)
    monkeypatch.setattr(controller, "object_refresh_target",
                         lambda x, y, plane, obj_id: (lambda: None))
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: clicked.append(entity["id"]) or True)

    hs = _hotspot(10554, 40164, 1, True)
    hs["location"] = {"x": 2617, "y": 3318, "plane": 0}
    state = {"homeName": "ROSS", "npcId": 10420,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert clicked, "an unknown reachability answer must still click, exactly as before"
    assert set(clicked) == {40164}
    assert len(clicked) <= mh.HOTSPOT_SPAM_MAX_CLICKS


def test_pick_next_hotspot_prefers_one_we_can_actually_reach(monkeypatch):
    # MEASURED live 2026-08-29 upstairs at JEFF's: of three hotspots needing attention, 39994
    # (3242,3446) was walled off while 39995 and 39996 were reachable -- and there was NO shut
    # door anywhere in range, so no door logic could have helped. USER: "there is no doors but we
    # are on the second floor". Bare list order fixated on the unreachable one, walked at it, and
    # wedged the obstacle-blind greedy walker (Shortest Path cannot route on plane 1 at all),
    # while reachable work sat two tiles away.
    reach = {(3238, 3455)}          # only 39995's neighbourhood is walkable
    monkeypatch.setattr(travel, "can_reach",
                         lambda loc, r=None, **kw: (loc["x"], loc["y"]) == (3238, 3454))

    def _hs(obj_id, x, y):
        h = _hotspot(10554, obj_id, 3, True)
        h["location"] = {"x": x, "y": y, "plane": 1}
        return h

    hotspots = [_hs(39994, 3242, 3446), _hs(39995, 3238, 3454), _hs(39996, 3242, 3447)]
    assert mh.pick_next_hotspot(hotspots)["objectId"] == 39994, "list order, unchanged"
    assert mh.pick_next_hotspot(hotspots, reach)["objectId"] == 39995


def test_pick_next_hotspot_keeps_list_order_when_everything_is_reachable(monkeypatch):
    # The normal case must be untouched.
    monkeypatch.setattr(travel, "can_reach", lambda loc, r=None, **kw: True)

    def _hs(obj_id, x, y):
        h = _hotspot(10554, obj_id, 3, True)
        h["location"] = {"x": x, "y": y, "plane": 0}
        return h

    hotspots = [_hs(1, 0, 0), _hs(2, 5, 5)]
    assert mh.pick_next_hotspot(hotspots, {(0, 1)})["objectId"] == 1


def test_pick_next_hotspot_falls_back_when_nothing_is_reachable(monkeypatch):
    # All walled off: still return something and let the door/walk recovery run, rather than
    # reporting "no work left" and going to turn_in on a contract that isn't finished.
    monkeypatch.setattr(travel, "can_reach", lambda loc, r=None, **kw: False)

    def _hs(obj_id):
        h = _hotspot(10554, obj_id, 3, True)
        h["location"] = {"x": 1, "y": 1, "plane": 0}
        return h

    hotspots = [_hs(7), _hs(8)]
    assert mh.pick_next_hotspot(hotspots, {(0, 0)})["objectId"] == 7


def test_pick_next_hotspot_ignores_reachability_it_cannot_determine(monkeypatch):
    # An empty reach set is the client saying "cannot tell" -- plain list order, never a guess.
    monkeypatch.setattr(travel, "can_reach",
                         lambda loc, r=None, **kw: pytest.fail("must not ask with no grid"))
    h = _hotspot(10554, 40011, 3, True)
    h["location"] = {"x": 1, "y": 1, "plane": 0}
    assert mh.pick_next_hotspot([h], set())["objectId"] == 40011

def test_a_hotspot_behind_a_wall_is_not_clicked_however_close_we_stand(monkeypatch):
    # The replacement for an earlier "standing next to it, just click" override, which was added
    # after a walk-in-place loop at JEFF's and then produced the OPPOSITE failure at ROSS's:
    # player (2617,3319), hotspot (2617,3318), Chebyshev distance 1, exterior wall on exactly that
    # edge -- so "adjacent" meant clicking through a wall. USER: "stuck outside ross again... this
    # is absurd". Raw tile distance cannot separate the two cases; travel.can_reach's edge test
    # can, so the routine simply trusts it.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40164, distance=1, x=2617, y=3318)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2617, "y": 3319, "plane": 0})
    monkeypatch.setattr(worldmap, "reachable_from",
                         lambda x, y, plane, **kw: {(2617, 3319): 0})
    monkeypatch.setattr(travel, "can_reach", lambda goal, reach=None, flags=None, **kw: False)
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: pytest.fail("that is through a wall"))
    monkeypatch.setattr(travel, "open_door_toward", lambda ctx, goal: False)
    walked = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, goal, **kw: walked.append(goal) or True)

    hs = _hotspot(10554, 40164, 1, True)
    hs["location"] = {"x": 2617, "y": 3318, "plane": 0}
    state = {"homeName": "ROSS", "npcId": 10420,
             "standingLocation": {"x": 2613, "y": 3316, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)
    assert walked, "it must walk around instead of clicking through the wall"


def test_the_reachability_gate_uses_live_collision_not_the_static_map(monkeypatch):
    # REWRITTEN 2026-08-29 (map-swap task): the original version put its wall on the GOAL's own
    # adjacent edge. can_reach's local edge test always re-fetches that edge from LIVE
    # controller.get_collision regardless of which `reach` set it was handed (see can_reach's
    # `flags is None` fallback, a radius-2 fetch centred on the goal) -- so on that geometry the
    # answer came out right even when work_phase's `live_reach` argument was swapped for the
    # door-blind static-map flood. The test passed against the very bug it was meant to catch.
    #
    # This version puts the wall SEVERAL TILES BACK, between the player and the goal's
    # neighbourhood -- ROSS's real shape (player outside, a shut door in the exterior wall,
    # hotspot inside). The goal's own local geometry (what the radius-2 fallback sees) is open on
    # every side; the ONLY thing that can catch the far door is the `reach` SET membership test
    # (`near not in reach`, see can_reach's cardinal/diagonal loops) -- i.e. whether `live_reach`
    # was built by flooding through the door (worldmap.reachable_from, which bakes doors OPEN) or
    # by stopping at it (travel.reachable_set, reading the door's real shut edge off LIVE
    # GET /collision). That is exactly what work_phase's `live_reach` line is supposed to use.
    monkeypatch.setattr(controller, "get_inventory", lambda: [_item("Teak plank", 28)])
    monkeypatch.setattr(controller, "get_objects",
                         lambda radius=None: [_object(40164, distance=6, x=6, y=0)])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 0, "y": 0, "plane": 0})

    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                         lambda ser, entity, **kw: clicked.append(entity) or True)

    # The static map's flood: doors baked open, so it claims the WHOLE line through to the
    # hotspot's neighbourhood is reachable -- the over-permissive answer a door-blind flood
    # always gives. The gate must not be built from this.
    monkeypatch.setattr(worldmap, "reachable_from",
                         lambda x, y, plane, **kw: {(x2, 0): x2 for x2 in range(0, 7)})

    # LIVE collision: a real shut door three tiles east of the player -- the ONLY wall in the
    # scene, and nowhere near the goal's own neighbourhood (a radius-2 probe centred on (6, 0)
    # sees nothing but open ground either way).
    BLOCK_MOVEMENT_EAST = 8
    BLOCK_MOVEMENT_WEST = 128
    grid = {(x, 0): 0 for x in range(-1, 9)}
    grid[(3, 0)] = BLOCK_MOVEMENT_EAST
    grid[(4, 0)] = BLOCK_MOVEMENT_WEST
    monkeypatch.setattr(controller, "get_collision", lambda x, y, plane, radius=6: [
        {"x": tx, "y": ty, "plane": 0, "flags": f} for (tx, ty), f in grid.items()
        if max(abs(tx - x), abs(ty - y)) <= radius])

    opened, walked = [], []
    monkeypatch.setattr(travel, "open_door_toward",
                         lambda ctx, goal: opened.append(goal) or True)
    monkeypatch.setattr(travel, "walk_to",
                         lambda ctx, goal, **kw: walked.append(goal) or True)

    hs = _hotspot(10554, 40164, 1, True)
    hs["location"] = {"x": 6, "y": 0, "plane": 0}
    state = {"homeName": "ROSS", "npcId": 10420,
             "standingLocation": {"x": 0, "y": 0, "plane": 0},
             "hotspots": [hs]}

    ctx = _Ctx()
    mh.work_phase(ctx, mh.DEFAULT_CONFIG, state)

    assert not clicked, (
        "the gate must reject a hotspot beyond a live-shut door, even though the static map "
        "says the whole line through to it is reachable")
    assert opened, "must have tried to open the door standing in the way"
    assert walked


def test_a_walled_off_ladder_is_not_clicked_either(monkeypatch):
    # MEASURED live 2026-08-29 at ROSS's: player (2618,3315), ladder 16683 at (2617,3315) --
    # cardinally adjacent, but (2618,3315) carries BLOCK_MOVEMENT_WEST so the shared edge is a
    # wall. `menu-select Climb-up Ladder` registered THREE times, the player never changed floor,
    # and the run safety-stopped. The game accepts an interaction it cannot path to and silently
    # does nothing -- the same failure as clicking an unreachable hotspot, at a call site that had
    # not been gated. USER: "i mean at this point am i going to have to hardcode each door?" -- no:
    # gate every world-object click on reachability, once.
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2618, "y": 3315, "plane": 0})
    monkeypatch.setattr(worldmap, "reachable_from",
                         lambda x, y, plane, **kw: {(2618, 3315): 0})
    monkeypatch.setattr(travel, "can_reach", lambda goal, reach=None, **kw: False)
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda *a, **kw: pytest.fail("that ladder is behind a wall"))
    opened, walked = [], []
    monkeypatch.setattr(travel, "open_door_toward",
                         lambda ctx, goal: opened.append(goal) or True)
    monkeypatch.setattr(travel, "walk_to", lambda ctx, goal, **kw: walked.append(goal) or True)

    ctx = _Ctx()
    ladder = {"id": 16683, "name": "Ladder", "x": 2617, "y": 3315, "plane": 0,
              "screenX": 100, "screenY": 100}
    mh._climb(ctx, ladder, "Climb-up", "why")

    assert opened and walked, "it must open the way and walk, not click through the wall"


def test_a_reachable_ladder_is_still_clicked_normally(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2617, "y": 3316, "plane": 0})
    monkeypatch.setattr(worldmap, "reachable_from",
                         lambda x, y, plane, **kw: {(2617, 3316): 0, (2617, 3315): 1})
    monkeypatch.setattr(travel, "can_reach", lambda goal, reach=None, **kw: True)
    # A REACHABLE ladder must involve no door work whatsoever. The old unconditional
    # open_doorway_to here ran the straight-line edge test every climb and false-positived on the
    # front door behind us at ROSS's, stepping outside between each Remove/Build.
    monkeypatch.setattr(travel, "open_door_toward",
                         lambda ctx, goal: pytest.fail("ladder is reachable -- no door work"))
    monkeypatch.setattr(travel, "walk_to", lambda *a, **kw: pytest.fail("no walk needed"))
    climbed = {}
    monkeypatch.setattr(controller, "click_menu_action",
                         lambda ser, e, option, **kw: climbed.setdefault("option", option))

    ctx = _Ctx()
    ladder = {"id": 16683, "name": "Ladder", "x": 2617, "y": 3315, "plane": 0,
              "screenX": 100, "screenY": 100}
    mh._climb(ctx, ladder, "Climb-up", "why")
    assert climbed["option"] == "Climb-up"


def test_a_hotspot_on_another_floor_is_not_judged_by_our_own_floors_grid(monkeypatch):
    # A reachable set is (x, y) with no plane in it, and the two floors share a coordinate space.
    # MEASURED at ROSS's: a plane-1 hotspot was judged "reachable" against a plane-0 flood fill
    # purely because its x/y landed on a walkable ground-floor tile, so work_phase preferred it
    # over the ground-floor hotspots and went ladder-hunting. Cross-floor must answer None.
    assert travel.can_reach({"x": 10, "y": 10, "plane": 1}, {(10, 10)}, plane=0) is None


def test_pick_next_hotspot_accepts_a_distance_dict_as_the_reach_map(monkeypatch):
    """worldmap.reachable_from returns {(x, y): distance} where travel.reachable_set returned a
    set. Both answer `in` and truthiness the same way, so the ranking is unchanged."""
    reachable = {"x": 10, "y": 10, "plane": 0}
    walled_off = {"x": 99, "y": 99, "plane": 0}
    hotspots = [
        {"objectId": 1, "needsAttention": True, "location": walled_off},
        {"objectId": 2, "needsAttention": True, "location": reachable},
    ]
    monkeypatch.setattr(mahogany_homes.travel, "can_reach",
                        lambda loc, reach, plane=None: loc == reachable)
    chosen = mahogany_homes.pick_next_hotspot(hotspots, {(10, 10): 0, (10, 11): 1}, 0)
    assert chosen["objectId"] == 2


def test_pick_next_hotspot_still_returns_a_walled_off_hotspot_when_nothing_else_is_left():
    """Rank 2 sorts last but is still RETURNED, so a walled-off contract falls through to the
    door/walk recovery rather than reporting no work left and turning in unfinished."""
    walled_off = {"x": 99, "y": 99, "plane": 0}
    hotspots = [{"objectId": 1, "needsAttention": True, "location": walled_off}]
    assert mahogany_homes.pick_next_hotspot(hotspots, {(10, 10): 0}, 0)["objectId"] == 1
