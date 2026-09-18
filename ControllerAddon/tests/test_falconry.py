"""Tests for falconry.py: the refresh_target adoption (2026-08-27) and the event-driven
primitives shared with golem_crafting's fur trip (2026-09-06)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import bridge_events
import falconry
import travel
import controller


class _Ctx:
    ser = None
    events = None


def test_hire_falcon_passes_refresh_target(monkeypatch):
    matthias = {"index": 3, "x": 100, "y": 200, "plane": 0, "name": "Matthias", "distance": 2}
    monkeypatch.setattr(falconry, "_first_inventory_item",
                         lambda sub: {"qty": falconry.DEFAULT_CONFIG["minCoins"]})
    monkeypatch.setattr(falconry, "_nearest_npc", lambda name, max_distance: matthias)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    # The hire is confirmed on the glove appearing in the weapon slot (2026-09-06).
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Falconer's glove"}})
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    assert falconry._hire_falcon(_Ctx()) is True
    assert seen.get("refresh_target") is not None


def test_leg_fairy_ring_passes_refresh_target(monkeypatch):
    """The fairy ring is a real-world object read from the TickCache-gated bulk /objects list, so
    its resolved screen position can be stale by up to 600ms before the click even fires and its
    projection moves with the camera -- same rationale as every other newly-fixed
    click_menu_action object call site in this branch (see pyramid_plunder.py's quick_leave)."""
    ring = {"id": travel.FAIRY_RING_ID, "x": 100, "y": 200, "plane": 0, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=20: [ring])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    _stub_dial(monkeypatch, dials=[0, 2, 3], confirm_text="Piscatoris Hunter area")
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    # AFTER _stub_dial, which stubs click_menu_action itself -- the last setattr wins.
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    falconry._leg_fairy_ring(_ring_ctx())

    assert seen.get("refresh_target") is not None


# --- The fairy ring is DIALLED, never taken on Last-destination (2026-09-14) -------------------
#
# CONFIRMED live: clicking "Last-destination" teleported the fur trip to region 12582 -- a
# camphor-tree island ~800 tiles from Piscatoris -- because the last destination is mutable
# per-account state that any other trip, or the user by hand, changes. The routine then
# safety-stopped with "not on any known leg" and the run was stranded.
#
# The dial mechanic below was MEASURED live against the open interface the same day; see
# travel.FAIRY_DIAL_VARBITS's own comment block for the readings.

def _ring_ctx():
    class _S:
        def __init__(self):
            self.data = {}
            self.config = dict(falconry.DEFAULT_CONFIG)
            self.safety_stopped = False
            self.safety_reason = None
            self.paused = False

        def trigger_safety_stop(self, reason):
            self.safety_stopped = True
            self.safety_reason = reason

    class _C:
        def __init__(self):
            self.ser = None
            self.state = _S()
            self.events = None
    return _C()


def test_a_stale_dial_varbit_is_re_read_and_fixed_instead_of_stopping_the_run(monkeypatch):
    """MEASURED LIVE 2026-09-15 (USER: "Are we stuck in ring ui?"). The interface and the dial
    varbits disagree just after Configure opens, and the VARBITS are the half that lies: the
    routine made ZERO rotations because every varbit already read as its target, the button then
    read 'Cave south of Dorgesh-Kaan' (AJQ), and it safety-stopped with the ring one click from
    correct. Re-reading the varbits minutes later gave 3986 = 3 (J), agreeing with the button.

    So the first pass sees the stale value and does nothing; the second must read it settled and
    rotate. The button text is the authority throughout -- it is never confirmed on a wrong one."""
    ring = {"id": travel.FAIRY_RING_ID, "x": 100, "y": 200, "plane": 0, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=20: [ring])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "get_player", lambda: {"regionId": falconry.REGION_FAIRY_LANDING})

    # Dial 2 really sits on J (index 3); the varbit claims K (index 2) on the first read only.
    truth = [0, 3, 3]
    reads = {"n": 0}

    def api_get(path, params=None, **kw):
        if path != "/varbit":
            return None
        i = travel.FAIRY_DIAL_VARBITS.index(params["id"])
        reads["n"] += 1
        if i == 1 and reads["n"] <= 3:        # the stale window: first pass only
            return {"value": 2}
        return {"value": truth[i]}
    monkeypatch.setattr(controller, "api_get", api_get)

    def get_widget(group, child, index=None):
        spelled = "".join(travel.FAIRY_DIAL_LETTERS[i][truth[i]] for i in range(3))
        text = ("Piscatoris Hunter area" if spelled == falconry.FAIRY_CODE_FALCONRY
                else "Cave south of Dorgesh-Kaan")
        return {"found": True, "clickable": True,
                "text": text if child == travel.FAIRY_CONFIRM_CHILD else "",
                "screenX": 10, "screenY": 10, "child": child}
    monkeypatch.setattr(controller, "get_widget", get_widget)

    confirmed = []

    def click_entity(ser, entity, **kw):
        child = entity.get("child")
        if child in travel.FAIRY_ROTATE_CW:
            i = travel.FAIRY_ROTATE_CW.index(child)
            truth[i] = (truth[i] + 1) % 4
        elif child in travel.FAIRY_ROTATE_CCW:
            i = travel.FAIRY_ROTATE_CCW.index(child)
            truth[i] = (truth[i] - 1) % 4
        elif child == travel.FAIRY_CONFIRM_CHILD:
            confirmed.append(True)
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **kw: True)
    monkeypatch.setattr(travel.outcome_waits, "await_pre", lambda *a, **kw: True)
    monkeypatch.setattr(travel.time, "sleep", lambda s: None)

    ctx = _ring_ctx()
    falconry._leg_fairy_ring(ctx)

    assert not ctx.state.safety_stopped, "a stale varbit must not stop the run"
    assert truth == [0, 2, 3], "the second pass must actually move dial 2 onto K"
    assert confirmed, "and then confirm, because the button finally reads Piscatoris"


def test_a_dial_that_never_reaches_the_destination_still_refuses_to_teleport(monkeypatch):
    """The guard that stops a silent trip to the wrong continent must survive the retry loop."""
    ring = {"id": travel.FAIRY_RING_ID, "x": 100, "y": 200, "plane": 0, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=20: [ring])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    # Varbits always claim the code is dialled; the button always disagrees.
    _stub_dial(monkeypatch, dials=[0, 2, 3], confirm_text="Cave south of Dorgesh-Kaan")
    confirmed = []
    inner = controller.click_entity
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: (confirmed.append(True)
                                              if e.get("child") == travel.FAIRY_CONFIRM_CHILD
                                              else None) or inner(ser, e, **kw))
    ctx = _ring_ctx()
    falconry._leg_fairy_ring(ctx)
    assert ctx.state.safety_stopped
    assert "instead of" in (ctx.state.safety_reason or "")
    assert not confirmed, "must never confirm a wrong destination"


def _stub_dial(monkeypatch, dials, confirm_text):
    """A fake dial: each rotate click moves the matching varbit, and the Confirm button's text is
    whatever the caller says the combination currently spells."""
    state = list(dials)
    clicks = []

    def api_get(path, params=None, **kw):
        if path == "/varbit":
            return {"value": state[travel.FAIRY_DIAL_VARBITS.index(params["id"])]}
        return None
    monkeypatch.setattr(controller, "api_get", api_get)

    def get_widget(group, child, index=None):
        text = confirm_text if child == travel.FAIRY_CONFIRM_CHILD else ""
        return {"found": True, "clickable": True, "text": text, "screenX": 10, "screenY": 10,
                "child": child}
    monkeypatch.setattr(controller, "get_widget", get_widget)

    def click_entity(ser, entity, **kw):
        child = entity.get("child")
        clicks.append(child)
        if child in travel.FAIRY_ROTATE_CW:
            i = travel.FAIRY_ROTATE_CW.index(child)
            state[i] = (state[i] + 1) % 4
        elif child in travel.FAIRY_ROTATE_CCW:
            i = travel.FAIRY_ROTATE_CCW.index(child)
            state[i] = (state[i] - 1) % 4
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)
    monkeypatch.setattr(controller, "click_menu_action", lambda *a, **kw: True)
    monkeypatch.setattr(travel.outcome_waits, "await_pre", lambda *a, **kw: True)
    monkeypatch.setattr(travel.time, "sleep", lambda s: None)
    return state, clicks


# --- Getting off the ring and onto the right side of the fence (2026-09-14, live) -------------
#
# USER: "Also we are stuck on other side of ring". Two separate defects, both MEASURED against
# the real collision map (the assertions below run on it, so they are a regression pin for the
# live geometry and not a restatement of the code):
#
#   1. The fairy ring drops the player ON the ring, whose own tile the static map marks blocked --
#      reachable_from there returns exactly ONE tile, so travel.walk_to cannot route a step.
#   2. _leg_walk_home branched on `y < TILE_STILE["y"]` as a proxy for "already across the stile".
#      The ring lands at y 3619 -- under 3621, but fifty tiles WEST of the fence -- so the trip
#      walked straight at the hunt tile from the wrong side. The two components are disjoint.

# --- The hunting ground is TWO regions (2026-09-15, live) -------------------------------------
#
# USER: "stuck at falco". A kebbit chase carried the player to (2380, 3583) -- one tile south of
# the 9528/9527 boundary but well inside the fence -- and the trip safety-stopped with "not on
# any known leg", sitting there for over two hours.

def test_the_hunting_enclosure_spans_both_falconry_regions():
    """MEASURED on the real collision map by flooding from TILE_HUNT: 389 tiles, 335 in region
    9528 and 54 in 9527, because the fence runs to y 3576 while the boundary is at y 3584."""
    import collections
    import worldmap
    comp = worldmap.reachable_from(falconry.TILE_HUNT["x"], falconry.TILE_HUNT["y"], 0,
                                   max_nodes=400000)
    regions = collections.Counter((x // 64) * 256 + (y // 64) for (x, y) in comp)
    assert set(regions) == set(falconry.FALCONRY_REGIONS), (
        f"the pen spans {sorted(regions)}, FALCONRY_REGIONS holds "
        f"{sorted(falconry.FALCONRY_REGIONS)}")
    assert regions[9527] > 0, "the southern rows really are the lower region"


def test_the_tile_that_stranded_the_run_is_inside_the_pen():
    import worldmap
    comp = worldmap.reachable_from(falconry.TILE_HUNT["x"], falconry.TILE_HUNT["y"], 0,
                                   max_nodes=400000)
    assert (2380, 3583) in comp, "the stranding tile was legitimately inside the hunting ground"


def test_the_falconer_is_inside_the_same_component():
    """Walking to Matthias must not read as leaving the area either."""
    import worldmap
    comp = worldmap.reachable_from(falconry.TILE_HUNT["x"], falconry.TILE_HUNT["y"], 0,
                                   max_nodes=400000)
    assert (falconry.TILE_FALCONER["x"], falconry.TILE_FALCONER["y"]) in comp


def test_the_piscatoris_ring_tile_really_is_sealed_on_the_map():
    """The live finding, pinned against the real map: the landing tile connects to nothing, and
    its east neighbour connects to a component that reaches the stile."""
    import worldmap
    assert falconry.is_isolated(2319, 3619, 0), "the ring tile must read as sealed"
    assert not falconry.is_isolated(2320, 3619, 0), "its east neighbour must not"
    assert worldmap.find_path(2320, 3619, 0, 2370, 3621, goal_adjacent=True), \
        "off the ring, the stile must be routable"


def test_the_hunting_ground_is_only_reachable_across_the_stile():
    """Why the y-proxy could not work: the ring side and the hunting enclosure are disjoint."""
    import worldmap
    assert not worldmap.find_path(2320, 3619, 0, 2388, 3590), \
        "the hunt tile must NOT be routable from the ring side -- the stile is the only way in"
    assert worldmap.find_path(2388, 3591, 0, 2388, 3590), \
        "inside the enclosure it must route normally"


def _walk_world(monkeypatch, tile, hunt_routable):
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"x": tile[0], "y": tile[1], "plane": 0})
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(falconry.worldmap, "find_path",
                        lambda *a, **kw: ["step"] if hunt_routable else [])
    exact, routed = [], []
    monkeypatch.setattr(falconry.travel, "walk_to_exact_tile",
                        lambda ctx, x, y, plane, **kw: exact.append((x, y)) or True)
    monkeypatch.setattr(falconry.travel, "walk_to",
                        lambda ctx, t, **kw: routed.append((t["x"], t["y"])) or True)
    return exact, routed


def test_the_walk_home_steps_off_the_ring_before_routing(monkeypatch):
    exact, routed = _walk_world(monkeypatch, (2319, 3619), hunt_routable=False)
    monkeypatch.setattr(falconry, "is_isolated",
                        lambda x, y, plane: (x, y) == (2319, 3619))

    falconry._leg_walk_home(_ring_ctx())

    assert exact == [(2320, 3619)], "must shift-click one tile off the ring, not route"
    assert routed == [], "routing is exactly what is unavailable while sealed"


def test_the_walk_home_crosses_the_stile_when_the_hunt_is_not_routable(monkeypatch):
    exact, routed = _walk_world(monkeypatch, (2340, 3619), hunt_routable=False)
    monkeypatch.setattr(falconry, "is_isolated", lambda x, y, plane: False)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [])   # stile not in view

    falconry._leg_walk_home(_ring_ctx())

    assert routed == [(falconry.TILE_STILE["x"], falconry.TILE_STILE["y"])], \
        "the wrong side of the fence means go to the stile, whatever the y says"


def test_the_stile_is_found_by_id_not_at_the_standing_tile(monkeypatch):
    """CONFIRMED live: TILE_STILE (2370, 3621) is where the player STANDS; the stile object is at
    (2371, 3621), and get_object_at matches the object's own exact tile. Looking it up there found
    nothing, so the character stood on the stile logging "walking to the stile" once a tick for
    ninety seconds."""
    exact, routed = _walk_world(monkeypatch, (2370, 3621), hunt_routable=False)
    monkeypatch.setattr(falconry, "is_isolated", lambda x, y, plane: False)
    stile = {"id": falconry.STILE_ID, "x": 2371, "y": 3621, "plane": 0, "visible": True,
             "screenX": 5, "screenY": 5}
    monkeypatch.setattr(controller, "get_objects", lambda radius=None: [stile])
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: None)
    clicked = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicked.append((e.get("x"), e.get("y"))) or True)

    falconry._leg_walk_home(_ring_ctx())

    assert clicked == [(2371, 3621)], "must climb the stile where it actually is"
    assert routed == [], "must not walk to the stile while standing at it"


def test_the_walk_home_goes_straight_to_the_hunt_once_across(monkeypatch):
    """And never touches the stile again -- which is what the y-proxy was originally written to
    guarantee, now falling out of the reachability question instead of a coordinate buffer."""
    exact, routed = _walk_world(monkeypatch, (2388, 3600), hunt_routable=True)
    monkeypatch.setattr(falconry, "is_isolated", lambda x, y, plane: False)
    stile_lookups = []
    monkeypatch.setattr(controller, "get_objects",
                        lambda radius=None: stile_lookups.append(radius) or [])
    ctx = _ring_ctx()
    ctx.state.data["banking"] = True

    falconry._leg_walk_home(ctx)

    assert routed == [(falconry.TILE_HUNT["x"], falconry.TILE_HUNT["y"])]
    assert stile_lookups == [], "must not even look for the stile once across"
    assert ctx.state.data["banking"] is False


def test_dial_rotations_takes_the_short_way_round():
    """Three forward is one backward -- at most two clicks per dial, never four."""
    assert travel.fairy_dial_rotations(0, "A", "ADCB") == []
    assert travel.fairy_dial_rotations(0, "D", "ADCB") == ["cw"]
    assert travel.fairy_dial_rotations(0, "C", "ADCB") == ["cw", "cw"]
    assert travel.fairy_dial_rotations(0, "B", "ADCB") == ["ccw"]
    assert travel.fairy_dial_rotations(1, "A", "ADCB") == ["ccw"]


def test_dial_rotations_refuses_a_letter_that_is_not_on_the_dial():
    """A typo'd code is caught before a single click, not after a teleport."""
    assert travel.fairy_dial_rotations(0, "K", "ADCB") is None
    assert travel.fairy_dial_rotations(None, "A", "ADCB") is None


def test_the_second_dial_reads_ILKJ_not_ILJK():
    """MEASURED live: dials [0,3,3] read back "Cave south of Dorgesh-Kaan", which the travel log
    gives as AJQ -- so index 3 is J, not K. The plausible-looking order would have teleported to
    the wrong place every trip."""
    assert travel.FAIRY_DIAL_LETTERS[1] == "ILKJ"
    assert travel.FAIRY_DIAL_LETTERS[1].index("K") == 2


def test_the_ring_is_dialled_to_the_code_and_then_confirmed(monkeypatch):
    ring = {"id": travel.FAIRY_RING_ID, "x": 100, "y": 200, "plane": 0, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=20: [ring])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    state, clicks = _stub_dial(monkeypatch, dials=[1, 1, 1],  # DLS, the Myreque hideout
                               confirm_text="Piscatoris Hunter area")
    ctx = _ring_ctx()

    falconry._leg_fairy_ring(ctx)

    assert state == [0, 2, 3], f"dials must spell AKQ, got {state}"
    assert clicks[-1] == travel.FAIRY_CONFIRM_CHILD, "Confirm must be the last click"
    assert not ctx.state.safety_stopped


def test_a_dial_that_spells_the_wrong_place_is_never_confirmed(monkeypatch):
    """The guard that has already paid for itself: the Confirm button names the destination the
    dials spell, so a wrong combination is caught BEFORE anything teleports."""
    ring = {"id": travel.FAIRY_RING_ID, "x": 100, "y": 200, "plane": 0, "visible": True}
    monkeypatch.setattr(controller, "get_objects", lambda radius=20: [ring])
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    state, clicks = _stub_dial(monkeypatch, dials=[0, 0, 0],
                               confirm_text="Cave south of Dorgesh-Kaan")
    ctx = _ring_ctx()

    falconry._leg_fairy_ring(ctx)

    assert travel.FAIRY_CONFIRM_CHILD not in clicks, "must not confirm a wrong destination"
    assert ctx.state.safety_stopped
    assert "Dorgesh-Kaan" in (ctx.state.safety_reason or "")


# --- Event-driven primitives (2026-09-06, shared with golem_crafting's fur trip) ---------------

class _State:
    def __init__(self):
        self.data = {}
        self.config = dict(falconry.DEFAULT_CONFIG)
        self.safety_stopped = False
        self.safety_reason = None
        self.paused = False

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason


class _EvCtx:
    def __init__(self, events):
        self.events = events
        self.ser = object()
        self.state = _State()


def _stream(monkeypatch, batches):
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
    clock = {"t": 100.0}
    monkeypatch.setattr(falconry, "_now", lambda: clock["t"])
    monkeypatch.setattr(falconry, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    ctx = _EvCtx(bridge_events.EventStream(sock=None))
    ctx.clock = clock
    return ctx


def test_throw_confirms_a_catch_on_the_falcon_varbit(monkeypatch):
    varbit = {"v": 0}
    monkeypatch.setattr(controller, "get_varbit", lambda vid: varbit["v"])
    kebbit = {"index": 7, "name": falconry.KEBBIT_NAME, "distance": 3}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [kebbit])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _stream(monkeypatch, [
        [("VARBIT_CHANGED", {"varbitId": 14192, "value": 5, "tick": 2},
          lambda: varbit.__setitem__("v", 5))],
    ])
    assert falconry._throw_falcon(ctx, falconry.DEFAULT_CONFIG) is True
    assert ctx.state.data["failed_throws"] == 0
    assert ctx.clock["t"] - 100.0 < 1.0


def test_a_miss_is_counted_not_treated_as_an_error(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    kebbit = {"index": 7, "name": falconry.KEBBIT_NAME, "distance": 3}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [kebbit])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _stream(monkeypatch, [])
    assert falconry._throw_falcon(ctx, falconry.DEFAULT_CONFIG) is False
    assert ctx.state.data["failed_throws"] == 1
    assert not ctx.state.safety_stopped


def test_retrieve_returns_once_the_varbit_clears(monkeypatch):
    varbit = {"v": 5}
    monkeypatch.setattr(controller, "get_varbit", lambda vid: varbit["v"])
    falcon = {"index": 7, "name": "Gyr Falcon", "distance": 2}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [falcon])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    ctx = _stream(monkeypatch, [
        [("VARBIT_CHANGED", {"varbitId": 14192, "value": 0, "tick": 3},
          lambda: varbit.__setitem__("v", 0))],
    ])
    assert falconry._retrieve_falcon(ctx, falconry.DEFAULT_CONFIG) is True
    assert ctx.clock["t"] - 100.0 < 1.0


def _full_of(name, n, keep_free=0):
    """An inventory of `n` `name` items padded to leave exactly `keep_free` slots."""
    junk = [{"name": name, "screenX": 10 + i, "screenY": 20} for i in range(n)]
    filler = [{"name": "Coins", "screenX": 0, "screenY": 0}
              for _ in range(falconry.INVENTORY_SLOTS - keep_free - n)]
    return junk + filler


def test_hunt_step_retrieves_before_anything_else(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 5)
    falcon = {"index": 7, "name": "Gyr Falcon", "distance": 2}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [falcon])
    monkeypatch.setattr(controller, "get_inventory", lambda: _full_of("Bones", 6))
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    clicks = []
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: clicks.append(e.get("name")) or True)
    ctx = _stream(monkeypatch, [])
    assert falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=True) == "retrieved"
    assert clicks == ["Gyr Falcon"], "a full inventory must not outrank a falcon sitting on a kill"


# --- Junk is DEFERRED and dropped in one burst (2026-09-15) ------------------------------------
#
# MEASURED across three live fur-trip logs the same day: one bone per hunt_step is one bone per
# engine loop iteration, 2.6-3.3s each and 17-22% of ALL hunting time. These pin the two halves
# of the replacement -- that it waits for crowding, and that when it goes it takes the whole pile.

def test_junk_is_not_dropped_while_there_is_room():
    config = falconry.DEFAULT_CONFIG
    assert falconry.junk_drop_due(free_slots=20, junk_held=12, config=config) is False
    assert falconry.junk_drop_due(free_slots=5, junk_held=12, config=config) is False


def test_junk_is_dropped_once_the_inventory_is_crowded():
    config = falconry.DEFAULT_CONFIG
    assert falconry.junk_drop_due(free_slots=4, junk_held=12, config=config) is True
    assert falconry.junk_drop_due(free_slots=0, junk_held=1, config=config) is True


def test_the_drop_threshold_leaves_room_for_the_fur_spill_and_outranks_banking():
    """golem_crafting's fur trip ENDS on fur spilling into the inventory (_hunt_done), so the
    threshold must never let junk take the last slot; and it has to fire before this routine's own
    bank trigger, or a pile of bones is what decides we are full."""
    assert falconry.DROP_AT_FREE_SLOTS >= 1
    assert falconry.DROP_AT_FREE_SLOTS > falconry.DEFAULT_CONFIG["bankAtFreeSlots"]


def test_a_crowded_inventory_drops_every_bone_in_one_pass(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": {"name": "Falconer's glove"}})
    monkeypatch.setattr(controller, "get_inventory", lambda: _full_of("Bones", 11))
    monkeypatch.setattr(controller, "get_player", lambda: dict(falconry.TILE_HUNT))
    passes = []
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: passes.append([i["name"] for i in items]) or True)
    ctx = _stream(monkeypatch, [])
    assert falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=False) == "dropped"
    assert passes == [["Bones"] * 11], ("the whole pile goes in ONE controller.drop_items call -- "
                                        "that is where the saving is, not in the click count")


def test_a_crowded_inventory_drops_meat_too_only_when_asked(monkeypatch):
    # Driven against a species that HAS meat: the default (Dark) yields none, which the
    # meat-free test below covers instead.
    monkeypatch.setattr(falconry, "MEAT_NAME", falconry.KEBBIT_SPECIES["dashing"]["meat"])
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: _full_of(falconry.MEAT_NAME, 9))
    monkeypatch.setattr(controller, "get_player", lambda: dict(falconry.TILE_HUNT))
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Falconer's glove"}})
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    import travel
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)
    passes = []
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: passes.append([i["name"] for i in items]) or True)
    ctx = _stream(monkeypatch, [])
    assert falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=True) == "dropped"
    assert passes == [[falconry.MEAT_NAME] * 9]
    passes.clear()
    assert falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=False) == "walked"
    assert passes == []


def test_the_burst_delegates_to_controller_drop_items_so_the_tab_is_raised(monkeypatch):
    """controller.drop_items raises the inventory tab, holds SHIFT once across the burst and
    verifies the first drop. CONFIRMED live 2026-09-14 the tab is NOT a given on this leg
    (_ensure_hands_free taps "E" on the way in): 50+ shift-clicks landed on the equipment panel
    and dropped nothing. Re-implementing the clicking here would re-open all three holes."""
    monkeypatch.setattr(controller, "get_inventory", lambda: _full_of("Bones", 5))
    tabs = []
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: tabs.append(True) or True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, k: None)
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    # Window bounds are a live HTTP read (is_point_within_window -> get_state), and this test is
    # about the tab being raised, not about where the client window happens to be sized today.
    monkeypatch.setattr(controller, "is_point_within_window", lambda x, y: True)
    monkeypatch.setattr(controller, "DROP_CONFIRM_S", 0.01)
    monkeypatch.setattr(controller, "DROP_CONFIRM_POLL_S", 0.0)
    ctx = _stream(monkeypatch, [])
    assert falconry._drop_junk(ctx, falconry.DEFAULT_CONFIG) is True
    assert tabs, "the inventory tab must be raised before a drop"


def test_a_species_that_drops_no_meat_never_tries_to_drop_any(monkeypatch):
    """MEASURED live 2026-09-14 on the Dark kebbit -- 16 retrieves, 16 bone drops, ZERO raw meat.
    With MEAT_NAME None an unguarded name would scan the inventory for the substring "none"."""
    assert falconry.KEBBIT_SPECIES["dark"]["meat"] is None
    monkeypatch.setattr(falconry, "MEAT_NAME", None)
    assert falconry.junk_names(falconry.DEFAULT_CONFIG, drop_meat=True) == (falconry.BONES_NAME,)
    assert None not in falconry.junk_names(falconry.DEFAULT_CONFIG, drop_meat=True)


def test_junk_names_is_empty_when_nothing_is_droppable(monkeypatch):
    """An empty name list must short-circuit BEFORE the /inventory read -- otherwise every tick
    of a dropBones=False hunt pays for a scan that can never select anything."""
    monkeypatch.setattr(falconry, "MEAT_NAME", None)
    config = dict(falconry.DEFAULT_CONFIG, dropBones=False)
    assert falconry.junk_names(config, drop_meat=True) == ()
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: (_ for _ in ()).throw(AssertionError("read the inventory anyway")))
    assert falconry._drop_junk(_Ctx(), config, drop_meat=True) is False


# --- The hunt is LEASHED to TILE_HUNT (2026-09-15) ---------------------------------------------
#
# MEASURED live twice in six minutes: Dark kebbits live on BOTH sides of the falconry fence, and
# `_nearest_npc` measures from the PLAYER -- so each throw walked the hunt a few tiles further out
# and the error compounded, ending at (2364, 3581), 24 tiles out, among Wild kebbit / Copper
# longtail. One ending lost a falcon (the walk back cancelled a retrieve), the other safety-stopped.

def test_the_leash_covers_the_whole_pen_but_not_the_ground_outside_it():
    """Flooding the collision map from TILE_HUNT gives x 2374-2393, y 3576-3604. The leash has to
    admit every tile of that and exclude the out-of-pen kebbits measured live."""
    assert falconry.tiles_from_hunt(2374, 3576) <= falconry.HUNT_LEASH_TILES
    assert falconry.tiles_from_hunt(2393, 3604) <= falconry.HUNT_LEASH_TILES
    for x, y in ((2369, 3580), (2367, 3587), (2364, 3581)):   # measured live, all outside the pen
        assert falconry.tiles_from_hunt(x, y) > falconry.HUNT_LEASH_TILES


def test_an_out_of_pen_kebbit_is_never_a_throw_target(monkeypatch):
    near_but_outside = {"name": falconry.KEBBIT_NAME, "index": 1, "distance": 2,
                        "x": 2369, "y": 3580}
    inside = {"name": falconry.KEBBIT_NAME, "index": 2, "distance": 9, "x": 2386, "y": 3592}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [near_but_outside, inside])
    picked = falconry._nearest_npc(falconry.KEBBIT_NAME, 12, leash=falconry.HUNT_LEASH_TILES)
    assert picked is inside, ("the NEARER kebbit is outside the fence -- taking it is what walked "
                             "the hunt out of the pen")


def test_the_falcon_is_not_leashed_so_a_kill_outside_the_pen_is_still_collected(monkeypatch):
    strayed = {"name": falconry.FALCON_NAME, "index": 3, "distance": 6, "x": 2368, "y": 3585}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [strayed])
    assert falconry._nearest_npc(falconry.FALCON_NAME, falconry.FALCON_SEARCH_TILES) is strayed
    assert falconry._nearest_npc(falconry.KEBBIT_NAME, 12,
                                 leash=falconry.HUNT_LEASH_TILES) is None


def test_a_drifted_player_walks_back_before_throwing_again(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2364, "y": 3581, "plane": 0})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": {"name": "Falconer's glove"}})
    import travel
    walks = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walks.append(target) or True)
    assert falconry.hunt_step(_stream(monkeypatch, []), falconry.DEFAULT_CONFIG) == "walked"
    assert walks == [falconry.TILE_HUNT]


def test_the_retrieve_outranks_the_leash_so_a_pending_catch_is_never_abandoned(monkeypatch):
    """The live falcon loss: the player drifted out, something walked it back, and the retrieve it
    was part-way through was abandoned. Retrieve must win over the leash walk."""
    monkeypatch.setattr(controller, "get_varbit", lambda vid: falconry.FALCON_CATCH_WAITING)
    falcon = {"name": falconry.FALCON_NAME, "index": 3, "distance": 5, "x": 2366, "y": 3583}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [falcon])
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2364, "y": 3581, "plane": 0})
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    clicks = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: clicks.append(e.get("name")) or True)
    import travel
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)
    assert falconry.hunt_step(_stream(monkeypatch, []), falconry.DEFAULT_CONFIG) == "retrieved"
    assert clicks == [falconry.FALCON_NAME]


def test_the_out_of_range_walk_back_is_bounded(monkeypatch):
    """MEASURED live: 54 iterations over 45s, because the walk is a no-op when we already stand on
    TILE_HUNT. It ended only when the falcon despawned."""
    monkeypatch.setattr(controller, "get_varbit", lambda vid: falconry.FALCON_CATCH_WAITING)
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    import travel
    walks = []
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: walks.append(target) or True)
    ctx = _stream(monkeypatch, [])
    for _ in range(falconry.FALCON_OUT_OF_RANGE_TRIES):
        assert falconry._retrieve_falcon(ctx, falconry.DEFAULT_CONFIG) is True
    assert falconry._retrieve_falcon(ctx, falconry.DEFAULT_CONFIG) is False, "must give up"
    assert len(walks) == falconry.FALCON_OUT_OF_RANGE_TRIES


def test_seeing_the_falcon_again_resets_the_out_of_range_counter(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: falconry.FALCON_CATCH_WAITING)
    falcon = {"name": falconry.FALCON_NAME, "index": 3, "distance": 4, "x": 2388, "y": 3590}
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: True)
    import travel
    monkeypatch.setattr(travel, "walk_to", lambda ctx, target, **kw: True)
    ctx = _stream(monkeypatch, [])
    ctx.state.data["falcon_out_of_range"] = falconry.FALCON_OUT_OF_RANGE_TRIES
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [falcon])
    assert falconry._retrieve_falcon(ctx, falconry.DEFAULT_CONFIG) is True
    assert ctx.state.data["falcon_out_of_range"] == 0


def test_hunt_step_hires_when_no_glove_is_worn(monkeypatch):
    monkeypatch.setattr(controller, "get_varbit", lambda vid: 0)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Coins", "qty": 2000}])
    monkeypatch.setattr(controller, "get_player", lambda: dict(falconry.TILE_HUNT))
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": None})
    matthias = {"index": 3, "name": "Matthias", "distance": 4}
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [matthias])
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda idx: (lambda: None))
    seen = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, option, **kw: seen.append(option) or True)
    ctx = _stream(monkeypatch, [])
    assert falconry.hunt_step(ctx, falconry.DEFAULT_CONFIG, drop_meat=True) == "hired"
    assert seen == [falconry.OPT_QUICK_FALCON]


def test_ensure_hands_free_removes_a_shield_through_the_equipment_widget(monkeypatch):
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": None, "shield": {"name": "Rune kiteshield"}, "gloves": None})
    calls = []
    monkeypatch.setattr(controller, "get_widget",
                        lambda group, child, index=None: {"clickable": True, "screenX": 5, "screenY": 5,
                                                          "group": group, "child": child})
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: calls.append((e["group"], e["child"])) or True)
    monkeypatch.setattr(controller, "human_delay", lambda *a, **k: None)
    ctx = _EvCtx(None)
    assert falconry._ensure_hands_free(ctx) is True
    assert calls == [(387, falconry.EQUIPMENT_SHIELD_CHILD)]


def test_ensure_hands_free_ignores_the_falconers_glove_in_the_weapon_slot(monkeypatch):
    monkeypatch.setattr(controller, "get_equipment",
                        lambda: {"weapon": {"name": "Falconer's glove"}, "shield": None, "gloves": None})
    ctx = _EvCtx(None)
    assert falconry.hands_free() is True
    assert falconry._ensure_hands_free(ctx) is False


def test_use_teleport_waits_for_the_expected_region(monkeypatch):
    region = {"v": 9528}
    monkeypatch.setattr(controller, "get_player", lambda: {"regionId": region["v"]})
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Ardougne cloak 4", "screenX": 1, "screenY": 1}])
    # Inventory teleports are a hover-verified LEFT-click first (USER 2026-09-06).
    seen = []
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, e, **kw: seen.append(kw.get("expected_action")) or True)
    monkeypatch.setattr(controller, "click_menu_action", _menu_forbidden)
    ctx = _stream(monkeypatch, [
        [("PLAYER_STOPPED", {"tick": 9}, lambda: region.__setitem__("v", 10290))],
    ])
    assert falconry._use_teleport(ctx, falconry.ARDOUGNE_CLOAK, falconry.OPT_KANDARIN_MONASTERY,
                                  expect_region=10290) is True
    assert seen == [falconry.OPT_KANDARIN_MONASTERY]
    assert ctx.clock["t"] - 100.0 < falconry.TELEPORT_BUDGET_S


def _menu_forbidden(*a, **kw):
    raise AssertionError("the right-click menu must not be used here")


def test_use_teleport_falls_back_to_the_menu_when_the_left_click_is_withheld(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"regionId": 10290})
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Ardougne cloak 4", "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "click_entity", lambda ser, e, **kw: controller.CLICK_WITHHELD)
    menu = []
    monkeypatch.setattr(controller, "click_menu_action",
                        lambda ser, e, option, **kw: menu.append(option) or True)
    ctx = _stream(monkeypatch, [])
    assert falconry._use_teleport(ctx, falconry.ARDOUGNE_CLOAK, falconry.OPT_KANDARIN_MONASTERY,
                                  expect_region=10290) is True
    assert menu == [falconry.OPT_KANDARIN_MONASTERY]
