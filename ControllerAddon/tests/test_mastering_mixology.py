import os
import sys
from collections import deque

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import bridge_events
import controller
import mastering_mixology as mm


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


def _unprocessed(count):
    return [{"id": next(iter(mm.UNFINISHED_IDS))} for _ in range(count)]


# --- process_phase's multi-station loop ------------------------------------------------
#
# CONFIRMED same structural shape as plank_make.py's cast_phase (see that file's own
# test_cast_phase_stops_mid_load_when_paused): process_phase can walk through every
# unprocessed potion in the inventory inside ONE tick() call, so the engine's own F2 pause
# gate -- which only runs BETWEEN tick() calls -- can't interrupt it until the whole batch is
# done. Same fix, same idiom: re-check ctx.state.paused (and safety_stopped, which this loop
# was missing entirely -- a safety stop triggered mid-batch by _process_one wasn't stopping
# the NEXT station either) at the top of the loop.

def test_process_phase_stops_when_paused(monkeypatch):
    ctx = FakeCtx()
    ctx.state.data["mm_chosen_orders"] = [
        {"station": "Retort", "name": "Mammoth-might mix"},
        {"station": "Agitator", "name": "Mystic mana amalgam"},
    ]

    calls = []

    def fake_process_one(_ctx, _config, station_name, _station, _order_name, **_kw):
        calls.append(station_name)
        ctx.state.paused = True  # F2 pressed mid-batch, right after the first station

    monkeypatch.setattr(controller, "get_inventory", lambda: _unprocessed(2))
    monkeypatch.setattr(controller, "get_mixology_state", lambda: {"stations": {}})
    monkeypatch.setattr(mm, "find_object", lambda name: {"name": name})
    monkeypatch.setattr(mm, "_process_one", fake_process_one)

    mm.process_phase(ctx, {})

    assert calls == ["Retort"]              # never reached the second station
    assert not ctx.state.safety_stopped     # pausing is not a safety stop


def test_process_phase_stops_when_safety_stopped_mid_batch(monkeypatch):
    """A safety stop can be triggered BY _process_one itself (e.g. a stray click landing on the
    wrong potion) -- the loop must not barrel on to the next station once that happens."""
    ctx = FakeCtx()
    ctx.state.data["mm_chosen_orders"] = [
        {"station": "Retort", "name": "Mammoth-might mix"},
        {"station": "Agitator", "name": "Mystic mana amalgam"},
    ]

    calls = []

    def fake_process_one(_ctx, _config, station_name, _station, _order_name, **_kw):
        calls.append(station_name)
        ctx.state.trigger_safety_stop("stray click started the wrong potion")

    monkeypatch.setattr(controller, "get_inventory", lambda: _unprocessed(2))
    monkeypatch.setattr(controller, "get_mixology_state", lambda: {"stations": {}})
    monkeypatch.setattr(mm, "find_object", lambda name: {"name": name})
    monkeypatch.setattr(mm, "_process_one", fake_process_one)

    mm.process_phase(ctx, {})

    assert calls == ["Retort"]
    assert ctx.state.safety_stopped


# --- a station frozen on someone else's potion (LIVE 2026-09-17) -----------------------
#
# A stray speedup click auto-started an Agitator order in the empty Alembic. process_phase then
# wanted the Alembic for its own order, found it occupied, and returned every tick for five
# minutes until the generic stall stop fired with a message that never named the cause.

def _blocked_ctx(frozen_progress=14):
    ctx = FakeCtx()
    ctx.state.data["mm_chosen_orders"] = [{"station": "Alembic", "name": "Megalite liquid"}]
    return ctx


def _run_blocked(ctx, monkeypatch, clock, potion, progress):
    monkeypatch.setattr(controller, "get_inventory", lambda: _unprocessed(1))
    monkeypatch.setattr(controller, "get_mixology_state",
                        lambda: {"stations": {"alembic": {"progress": progress(),
                                                          "potionName": potion()}}})
    monkeypatch.setattr(mm, "find_object", lambda name: {"name": name})
    monkeypatch.setattr(mm, "_process_one", lambda *a, **k: None)
    monkeypatch.setattr(mm, "_now", lambda: clock["t"])
    mm.process_phase(ctx, {})


def test_process_phase_safety_stops_on_a_station_frozen_with_a_foreign_potion(monkeypatch):
    ctx = _blocked_ctx()
    clock = {"t": 1000.0}
    _run_blocked(ctx, monkeypatch, clock, lambda: "Liplack liquor", lambda: 14)
    assert not ctx.state.safety_stopped          # first sighting only starts the timer

    clock["t"] += mm.FOREIGN_STATION_STOP_S + 1
    _run_blocked(ctx, monkeypatch, clock, lambda: "Liplack liquor", lambda: 14)

    assert ctx.state.safety_stopped
    assert "Liplack liquor" in ctx.state.stop_reason
    assert "Megalite liquid" in ctx.state.stop_reason


def test_the_finish_wait_gives_up_early_when_the_station_released_it_unfinished(game, monkeypatch):
    """USER 2026-09-17: "It's idling" -- an unfinished 'Azure aura mix' was back in the inventory
    with every station empty, and the finish wait still had 90s to burn before it would retry."""
    order = "Azure aura mix"
    unfinished_id = next(k for k, v in mm.UNFINISHED_IDS.items() if v == order)

    game.inventory = [{"id": unfinished_id, "name": order}]   # released, still unfinished
    game.stations["retort"] = {"progress": 0}                 # and the station is empty
    ctx = _ctx_with_events()
    monkeypatch.setattr(mm, "_process_retort_speedup", lambda *a, **k: None)
    monkeypatch.setattr(mm, "_await", lambda *a, **k: True)

    captured = {}

    def fake_await_long(_ctx, _sig, _budget, confirm=None, stop=None, **_kw):
        captured["stop"] = stop
        return False
    monkeypatch.setattr(mm, "_await_long", fake_await_long)

    mm._process_one(ctx, {}, "Retort", game.station("Retort"), order, next_name=None)

    assert captured["stop"] is not None, "the finish wait must be able to bail out early"
    assert captured["stop"]() is True


def test_a_stalled_station_gets_CLICKED_to_take_the_potion_out(monkeypatch):
    """USER 2026-09-17: "it has to click the machine to get it out". A station never hands its
    potion back on its own, so logging "still busy" and returning waits for something that will
    never happen -- every stall today was a finished potion nobody collected."""
    ctx = FakeCtx()
    clock = {"t": 1000.0}
    monkeypatch.setattr(mm, "_now", lambda: clock["t"])
    clicked = []
    monkeypatch.setattr(mm, "_click_object",
                        lambda _ctx, name, **kw: clicked.append(name) or {"name": name})
    state = {"progress": 7, "potionName": "Marley's moonlight"}

    mm._collect_if_stalled(ctx, "Retort", state)        # first sighting: start the stall timer
    assert clicked == []

    clock["t"] += mm.COLLECT_STALL_S + 1
    mm._collect_if_stalled(ctx, "Retort", state)        # still on the same progress -> click

    assert clicked == ["Retort"]


def test_a_station_whose_progress_is_moving_is_never_clicked(monkeypatch):
    ctx = FakeCtx()
    clock = {"t": 1000.0}
    monkeypatch.setattr(mm, "_now", lambda: clock["t"])
    clicked = []
    monkeypatch.setattr(mm, "_click_object",
                        lambda _ctx, name, **kw: clicked.append(name) or {"name": name})

    for p in range(1, 8):
        mm._collect_if_stalled(ctx, "Retort", {"progress": p, "potionName": "Liplack liquor"})
        clock["t"] += mm.COLLECT_STALL_S + 1

    assert clicked == [], "a station that is still advancing must never be click-spammed"


def test_collect_clicks_are_bounded(monkeypatch):
    ctx = FakeCtx()
    clock = {"t": 1000.0}
    monkeypatch.setattr(mm, "_now", lambda: clock["t"])
    clicked = []
    monkeypatch.setattr(mm, "_click_object",
                        lambda _ctx, name, **kw: clicked.append(name) or {"name": name})
    state = {"progress": 7, "potionName": "Marley's moonlight"}

    mm._collect_if_stalled(ctx, "Retort", state)
    for _ in range(12):
        clock["t"] += mm.COLLECT_STALL_S + 1
        mm._collect_if_stalled(ctx, "Retort", state)

    assert len(clicked) == mm.COLLECT_MAX_TRIES


def test_orders_phase_will_not_start_three_new_while_a_station_holds_a_potion(monkeypatch):
    """USER 2026-09-17: "it has one in the machine again when going for 3 new". The inventory
    leftover check above it cannot see inside a station, so a stranded potion sailed past and the
    next cycle livelocked on "<station> still busy"."""
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_mixology_state",
                        lambda: {"stations": {"retort": {"progress": 4,
                                                         "potionName": "Mystic mana amalgam"}},
                                 "orders": [{"name": "A", "station": "Alembic"},
                                            {"name": "B", "station": "Agitator"},
                                            {"name": "C", "station": "Retort"}],
                                 "hopper": {"mox": 900, "aga": 900, "lye": 900}})

    mm.orders_phase(ctx, {})

    assert "mm_chosen_orders" not in ctx.state.data


def test_orders_phase_proceeds_once_every_station_is_clear(monkeypatch):
    ctx = FakeCtx()
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_mixology_state",
                        lambda: {"stations": {"retort": {"progress": 0}},
                                 "orders": [{"name": "A", "station": "Alembic", "mox": 0, "aga": 0, "lye": 0},
                                            {"name": "B", "station": "Agitator", "mox": 0, "aga": 0, "lye": 0},
                                            {"name": "C", "station": "Retort", "mox": 0, "aga": 0, "lye": 0}],
                                 "hopper": {"mox": 900, "aga": 900, "lye": 900}})
    monkeypatch.setattr(mm, "_make_base", lambda *a, **k: False)

    mm.orders_phase(ctx, {})

    assert ctx.state.data.get("mm_chosen_orders")


def test_process_phase_holds_the_deposit_while_a_station_is_still_working(monkeypatch):
    """A potion INSIDE a station is in no container the inventory can see. Counting only the
    inventory called the cycle done and deposited without it, stranding it there for good --
    LIVE 2026-09-17, twice (a Retort and an Alembic, each then livelocking the next cycle)."""
    ctx = FakeCtx()
    ctx.state.data["mm_chosen_orders"] = [{"station": "Alembic", "name": "Megalite liquid"}]
    monkeypatch.setattr(controller, "get_inventory", lambda: [])       # nothing unfinished held
    monkeypatch.setattr(controller, "get_mixology_state",
                        lambda: {"stations": {"alembic": {"progress": 6,
                                                          "potionName": "Megalite liquid"}}})
    monkeypatch.setattr(mm, "find_object", lambda name: {"name": name})
    monkeypatch.setattr(mm, "_process_one", lambda *a, **k: None)

    mm.process_phase(ctx, {})

    assert ctx.state.data.get("mm_phase") != "deposit"


def test_process_phase_deposits_once_every_station_is_clear(monkeypatch):
    ctx = FakeCtx()
    ctx.state.data["mm_chosen_orders"] = [{"station": "Alembic", "name": "Megalite liquid"}]
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_mixology_state",
                        lambda: {"stations": {"alembic": {"progress": 0}}})
    monkeypatch.setattr(mm, "find_object", lambda name: {"name": name})
    monkeypatch.setattr(mm, "_process_one", lambda *a, **k: None)

    mm.process_phase(ctx, {})

    assert ctx.state.data.get("mm_phase") == "deposit"


def test_a_station_frozen_on_our_OWN_potion_still_stops(monkeypatch):
    """The stranded-potion case matches the order name, so exempting "our own" potion was the
    hole the Alembic fell through: the timer never started and nothing ever stopped the spin."""
    ctx = _blocked_ctx()
    clock = {"t": 1000.0}
    _run_blocked(ctx, monkeypatch, clock, lambda: "Megalite liquid", lambda: 6)
    clock["t"] += mm.FOREIGN_STATION_STOP_S + 1
    _run_blocked(ctx, monkeypatch, clock, lambda: "Megalite liquid", lambda: 6)

    assert ctx.state.safety_stopped
    assert "stranded" in ctx.state.stop_reason


def test_a_station_still_working_its_foreign_potion_is_not_a_stop(monkeypatch):
    """Progress is in the key, so a station that is genuinely climbing keeps resetting the
    timer -- only a FROZEN one accumulates. Without this a slow potion (STATION_FINISH_S is
    90s, past the 60s threshold) would be stopped on mid-run."""
    ctx = _blocked_ctx()
    clock = {"t": 1000.0}
    progress = {"v": 1}
    for _ in range(6):
        _run_blocked(ctx, monkeypatch, clock, lambda: "Liplack liquor", lambda: progress["v"])
        clock["t"] += mm.FOREIGN_STATION_STOP_S / 2
        progress["v"] += 1

    assert not ctx.state.safety_stopped


def test_a_station_advancing_on_our_own_order_is_never_a_stop(monkeypatch):
    """ADVANCING is the thing that makes a busy station fine, not whose potion it is. The
    name-matches exemption this replaced is what let the stranded-potion freeze spin forever."""
    ctx = _blocked_ctx()
    clock = {"t": 1000.0}
    progress = {"v": 1}
    for _ in range(6):
        _run_blocked(ctx, monkeypatch, clock, lambda: "Megalite liquid", lambda: progress["v"])
        clock["t"] += mm.FOREIGN_STATION_STOP_S / 2
        progress["v"] += 1

    assert not ctx.state.safety_stopped


# --- Event-driven outcomes (2026-09-06) --------------------------------------------------
#
# A scripted fake of the bridge plus a REAL bridge_events.EventStream fed by a stubbed
# controller.poll_udp, so bridge_events.await_outcome's own semantics (mark/since, rejected
# seqs, the HTTP confirm deciding alone at the budget) are what is exercised, not a
# re-implementation of them. Every wait in the routine runs on mm._now/_sleep, so a budget
# expires instantly here (same fixture shape as test_blast_furnace.py).

LAB_PLANE = 0
LEVER_TILES = {"Mox lever": (1395, 9324), "Aga lever": (1394, 9324), "Lye lever": (1393, 9324)}
STATION_TILES = {"Retort": (1397, 9330), "Agitator": (1391, 9330), "Alembic": (1394, 9332)}


def _obj(name, x, y, obj_id=1, distance=1):
    return {"name": name, "id": obj_id, "impostorId": obj_id, "x": x, "y": y, "plane": LAB_PLANE,
            "screenX": 500, "screenY": 400, "distance": distance, "visible": True}


class Game:
    """Fake bridge state + a script of UDP batches. Each poll_udp call delivers ONE batch; an
    entry's `then` callable mutates the fake state at delivery time, so an event and the HTTP
    truth behind it land together, the way the real client emits them."""

    def __init__(self):
        self.inventory = []
        # A real client always answers a mixer-slot read (0 = empty); None would mean "unreadable".
        self.varbits = {11324: 0, 11325: 0, 11326: 0}
        self.stations = {"retort": {"progress": 0}, "agitator": {"progress": 0}, "alembic": {"progress": 0}}
        self.last_action = {}
        self.graphics = []
        self.objects = [_obj(n, *t) for n, t in LEVER_TILES.items()]
        self.objects += [_obj(n, *t) for n, t in STATION_TILES.items()]
        self.objects += [_obj("Mixing vessel", 1394, 9326), _obj("Conveyor belt", 1396, 9327),
                         _obj("Refiner", 1400, 9320), _obj("Hopper", 1396, 9324)]
        self.clicks = []       # entity clicks, by name
        self.in_place = 0      # click_human count
        self.hover_ok = True   # what controller.hover_matches answers
        self.busy = []         # controller.mark_busy durations
        self.batches = deque()
        self.on_click = {}     # name -> callable(game) run when that entity is clicked

    def feed(self, *events):
        """Queue one batch: each event is (type, payload) or (type, payload, then)."""
        self.batches.append(list(events))

    def poll_udp(self, _sock):
        if not self.batches:
            return []
        out = []
        for ev in self.batches.popleft():
            etype, payload = ev[0], ev[1]
            if len(ev) > 2 and ev[2] is not None:
                ev[2]()
            out.append((etype, payload))
        return out

    def mixology_state(self):
        return {"inLab": True, "orders": [], "hopper": {"mox": 900, "aga": 900, "lye": 900},
                "stations": self.stations, "digweed": {}}

    def station(self, name):
        return next(o for o in self.objects if o["name"] == name)


def _varbit(vid, value):
    return {"varbitId": vid, "varpId": 0, "value": value, "tick": 1}


@pytest.fixture
def game(monkeypatch):
    g = Game()
    monkeypatch.setattr(controller, "poll_udp", g.poll_udp)
    monkeypatch.setattr(controller, "get_inventory", lambda: list(g.inventory))
    monkeypatch.setattr(controller, "get_varbit", lambda vid: g.varbits.get(vid))
    monkeypatch.setattr(controller, "get_mixology_state", g.mixology_state)
    monkeypatch.setattr(controller, "get_last_action", lambda: dict(g.last_action))
    monkeypatch.setattr(controller, "get_last_chat_message", lambda: {})
    monkeypatch.setattr(controller, "get_objects",
                        lambda radius=None, fresh=False, interactable=False: list(g.objects))
    monkeypatch.setattr(controller, "get_graphics_objects", lambda fresh=False: list(g.graphics))
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "hover_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda **kw: True)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)
    monkeypatch.setattr(controller, "mark_busy", lambda s: g.busy.append(s))
    monkeypatch.setattr(controller, "hover_matches",
                        lambda expected_action=None, expected_target=None, **kw: g.hover_ok)

    def click_entity(ser, entity, **kw):
        name = entity.get("name")
        g.clicks.append(name)
        hook = g.on_click.get(name)
        if hook:
            hook(g)
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)

    def click_human(ser):
        g.in_place += 1
    monkeypatch.setattr(controller, "click_human", click_human)

    clock = {"t": 1000.0}
    monkeypatch.setattr(mm, "_now", lambda: clock["t"])

    def _sleep(s):
        clock["t"] += s
    monkeypatch.setattr(mm, "_sleep", _sleep)
    g.clock = clock
    return g


def _ctx_with_events():
    ctx = FakeCtx()
    ctx.events = bridge_events.EventStream(sock=None)
    return ctx


# --- order choice / constants -----------------------------------------------------------

def test_choose_orders_puts_retort_orders_last_and_keeps_all_three():
    orders = [
        {"name": "Mammoth-might mix", "station": "Retort"},
        {"name": "Alco-augmentator", "station": "Agitator"},
        {"name": "Liplack liquor", "station": "Alembic"},
    ]
    chosen = mm._choose_orders(orders)
    # Alembic (1391,9325) first, Agitator (1393,9329) second, Retort (1397,9325) last: the
    # vessel (1394,9326) -> Alembic -> Agitator -> Retort -> belt (1394,9331) walk is the
    # shortest of the Retort-last orders (AlchemyObject coordinates, hex-agon plugin).
    assert [o["station"] for o in chosen] == ["Alembic", "Agitator", "Retort"]
    assert len(chosen) == 3


def test_choose_orders_is_stable_for_two_retort_orders():
    orders = [
        {"name": "Mammoth-might mix", "station": "Retort"},
        {"name": "Alco-augmentator", "station": "Alembic"},
        {"name": "Liplack liquor", "station": "Retort"},
    ]
    chosen = mm._choose_orders(orders)
    assert [o["name"] for o in chosen] == ["Alco-augmentator", "Mammoth-might mix", "Liplack liquor"]


def test_varbit_subscriptions_cover_the_minigame():
    subs = set(mm.VARBIT_SUBSCRIPTIONS)
    for vid in (11324, 11325, 11326,            # mixer slots
                11339,                          # vessel ready
                11340, 11341, 11342,            # station potions
                11327, 11328, 11329,            # station progress
                11337, 11338,                   # skillshot hits
                11315):                         # order 1 type (deposit refresh)
        assert vid in subs


def test_vessel_potion_name_maps_the_1_indexed_varbit():
    assert mm.vessel_potion_name(1) == "Mammoth-might mix"
    assert mm.vessel_potion_name(7) == "Liplack liquor"
    assert mm.vessel_potion_name(10) == "Mixalot"
    assert mm.vessel_potion_name(0) is None
    assert mm.vessel_potion_name(None) is None
    assert mm.vessel_potion_name(11) is None


# --- lever pulls -----------------------------------------------------------------------

def test_pull_lever_confirms_on_menu_verdict_then_mixer_change(game):
    ctx = _ctx_with_events()
    game.feed(("MENU_CLICKED", {"option": "Pull", "target": "Mox lever", "tick": 5},
               lambda: game.last_action.update({"target": "Mox lever", "tick": 5})))
    game.feed(("VARBIT_CHANGED", _varbit(11324, 1), lambda: game.varbits.update({11324: 1})))

    assert mm._pull_lever(ctx, "mox") is True
    assert game.clicks == ["Mox lever"]


def test_pull_lever_retries_when_the_click_resolved_to_a_neighbour(game):
    ctx = _ctx_with_events()
    # First click: the game says it hit the Aga lever, and no mixer event follows, so the
    # verdict wait spends its budget and the pull is retried. Second click: the real thing.
    # Each verdict is fed FROM the click that causes it -- a script that queues both up front
    # delivers the second verdict during the first wait, which cannot happen live.
    clicks = {"n": 0}

    def on_click(g):
        clicks["n"] += 1
        if clicks["n"] == 1:
            g.feed(("MENU_CLICKED", {"option": "Pull", "target": "Aga lever", "tick": 5},
                    lambda: g.last_action.update({"target": "Aga lever", "tick": 5})))
        else:
            g.feed(("MENU_CLICKED", {"option": "Pull", "target": "Mox lever", "tick": 7},
                    lambda: g.last_action.update({"target": "Mox lever", "tick": 7})))
            g.feed(("VARBIT_CHANGED", _varbit(11324, 1), lambda: g.varbits.update({11324: 1})))
    game.on_click["Mox lever"] = on_click

    assert mm._pull_lever(ctx, "mox") is True
    assert game.clicks == ["Mox lever", "Mox lever"]


def test_pull_lever_gives_up_after_the_attempt_ceiling(game):
    ctx = _ctx_with_events()
    assert mm._pull_lever(ctx, "lye") is False
    assert len(game.clicks) == mm.LEVER_PULL_MAX_ATTEMPTS


def test_pull_lever_polls_the_http_truth_without_an_event_stream(game):
    ctx = FakeCtx()          # events None: the engine started no listener
    game.varbits[11324] = 0

    def landed(g):
        g.last_action.update({"target": "Mox lever", "tick": 9})
        g.varbits[11324] = 1
    game.on_click["Mox lever"] = landed

    assert mm._pull_lever(ctx, "mox") is True
    assert game.clicks == ["Mox lever"]


# --- the mix chain ---------------------------------------------------------------------

def _install_lab(game, chosen):
    """Wire the fake so lever clicks land (menu verdict + mixer varbit), the third pull of a
    base sets the vessel varbit to that order's potion, and a vessel click yields the base."""
    made = {"pulls": 0, "bases": 0}
    order_idx = {name: i + 1 for i, name in enumerate(mm.RECIPE_TABLE)}

    def pull(name):
        def _hook(g):
            made["pulls"] += 1
            n = made["pulls"]
            slot = 11324 + (n - 1) % 3
            g.feed(("MENU_CLICKED", {"target": name, "tick": n},
                    lambda: g.last_action.update({"target": name, "tick": n})))
            if n % 3 == 0:
                target = chosen[made["bases"]]["name"]
                g.feed(("VARBIT_CHANGED", _varbit(slot, 1),
                        lambda: g.varbits.update({slot: n, 11339: order_idx[target]})))
            else:
                g.feed(("VARBIT_CHANGED", _varbit(slot, 1),
                        lambda: g.varbits.update({slot: n})))
        return _hook

    for lever in LEVER_TILES:
        game.on_click[lever] = pull(lever)

    def take(g):
        target = chosen[made["bases"]]["name"]
        unfinished_id = next(k for k, v in mm.UNFINISHED_IDS.items() if v == target)
        made["bases"] += 1

        def _land():
            g.inventory.append({"id": unfinished_id, "name": target})
            g.varbits[11339] = 0
        g.feed(("CHAT_MESSAGE", {"message": f"You collect some {target} from the mixing vessel."}),
               ("INVENTORY_CHANGED", None, _land))
    game.on_click["Mixing vessel"] = take
    return made


def test_mix_phase_makes_every_chosen_base_inside_one_tick(game):
    ctx = _ctx_with_events()
    chosen = [
        {"name": "Alco-augmentator", "station": "Agitator", "mox": 0, "aga": 3, "lye": 0},
        {"name": "Liplack liquor", "station": "Alembic", "mox": 0, "aga": 0, "lye": 3},
        {"name": "Mammoth-might mix", "station": "Retort", "mox": 3, "aga": 0, "lye": 0},
    ]
    ctx.state.data["mm_chosen_orders"] = chosen
    ctx.state.data["mm_phase"] = "mix"
    _install_lab(game, chosen)

    mm.mix_phase(ctx, dict(mm.DEFAULT_CONFIG))

    assert [i["name"] for i in game.inventory] == [o["name"] for o in chosen]
    assert game.clicks.count("Mixing vessel") == 3
    assert game.clicks.count("Aga lever") == 3
    assert game.clicks.count("Lye lever") == 3
    assert game.clicks.count("Mox lever") == 3
    assert ctx.state.data["mm_phase"] == "process"
    assert not ctx.state.safety_stopped


def test_mix_phase_does_not_take_a_base_the_vessel_varbit_disagrees_with(game):
    ctx = _ctx_with_events()
    chosen = [{"name": "Liplack liquor", "station": "Alembic", "mox": 0, "aga": 0, "lye": 3}]
    ctx.state.data["mm_chosen_orders"] = chosen
    ctx.state.data["mm_phase"] = "mix"
    _install_lab(game, chosen)
    # Sabotage: the vessel reports Mammoth-might mix once the pulls are in.
    original = game.on_click["Lye lever"]

    def wrong(g):
        original(g)
        batch = g.batches[-1]
        etype, payload, then = batch[0]
        if payload.get("varbitId") in (11324, 11325, 11326):
            def wrong_then():
                then()
                if g.varbits.get(11339):
                    g.varbits[11339] = 1
            batch[0] = (etype, payload, wrong_then)
    game.on_click["Lye lever"] = wrong

    mm.mix_phase(ctx, dict(mm.DEFAULT_CONFIG))

    assert "Mixing vessel" not in game.clicks
    assert game.inventory == []
    assert ctx.state.data["mm_phase"] == "mix"
    assert not ctx.state.safety_stopped


def test_mix_phase_hands_back_when_paused_between_bases(game):
    ctx = _ctx_with_events()
    chosen = [
        {"name": "Alco-augmentator", "station": "Agitator", "mox": 0, "aga": 3, "lye": 0},
        {"name": "Liplack liquor", "station": "Alembic", "mox": 0, "aga": 0, "lye": 3},
    ]
    ctx.state.data["mm_chosen_orders"] = chosen
    ctx.state.data["mm_phase"] = "mix"
    _install_lab(game, chosen)
    take = game.on_click["Mixing vessel"]

    def take_then_pause(g):
        take(g)
        ctx.state.paused = True
    game.on_click["Mixing vessel"] = take_then_pause

    mm.mix_phase(ctx, dict(mm.DEFAULT_CONFIG))

    assert len(game.inventory) == 1
    assert ctx.state.data["mm_phase"] == "mix"


# --- stations --------------------------------------------------------------------------

def test_retort_speedup_clicks_once_per_progress_tick_and_stops_on_finish(game):
    ctx = _ctx_with_events()
    game.stations["retort"] = {"progress": 0, "potionName": "Mammoth-might mix"}
    game.varbits[11341] = 1
    for p in (1, 2, 3):
        game.feed(("VARBIT_CHANGED", _varbit(11327, p),
                   (lambda p=p: game.stations["retort"].update({"progress": p}))))
    game.feed(("VARBIT_CHANGED", _varbit(11327, 3)))       # a repeat: no click
    game.feed(("VARBIT_CHANGED", _varbit(11327, 0)),
              ("VARBIT_CHANGED", _varbit(11341, 0),
               lambda: (game.stations["retort"].update({"progress": 0, "potionName": None}),
                        game.varbits.update({11341: 0}))))
    start = game.clock["t"]

    mm._process_retort_speedup(ctx, game.station("Retort"), "Mammoth-might mix")

    assert game.in_place == 3
    assert game.clock["t"] - start < mm.RETORT_MAX_S     # returned on the finish, not the budget


def test_retort_speedup_withholds_a_click_that_would_land_off_the_station(game, monkeypatch):
    """A Retort click that misses the model resolves as "Walk here", and WALKING CANCELS the
    concentration: LIVE 2026-09-17 /hover named the Retort while /last-action showed the walk,
    and the potion sat frozen at progress 2 for the rest of the run. Withholding costs a few
    progress points; firing blind costs the potion."""
    game.hover_ok = False                       # neither re-aim gets back onto the model
    monkeypatch.setattr(controller, "hover_entity", lambda ser, entity, **kw: True)
    ctx = _ctx_with_events()
    game.stations["retort"] = {"progress": 0, "potionName": "Mammoth-might mix"}
    game.varbits[11341] = 1
    for p in (1, 2):
        game.feed(("VARBIT_CHANGED", _varbit(11327, p),
                   (lambda p=p: game.stations["retort"].update({"progress": p}))))
    game.feed(("VARBIT_CHANGED", _varbit(11341, 0),
               lambda: game.stations["retort"].update({"progress": 0, "potionName": None})))

    mm._process_retort_speedup(ctx, game.station("Retort"), "Mammoth-might mix")

    assert game.in_place == 0


def test_retort_speedup_recentres_its_aim_before_giving_up(game, monkeypatch):
    """The default (-7,+7) hover nudge sitting just off the model is what produced the walk, so
    the retry drops it for the model's centre before withholding."""
    offsets = []

    def hover_entity(ser, entity, **kw):
        offsets.append(kw.get("offset"))
        if kw.get("offset") == (0, 0):
            game.hover_ok = True       # the centred aim lands on the model
        return True
    monkeypatch.setattr(controller, "hover_entity", hover_entity)
    game.hover_ok = False
    ctx = _ctx_with_events()
    game.stations["retort"] = {"progress": 0, "potionName": "Mammoth-might mix"}
    game.varbits[11341] = 1
    game.feed(("VARBIT_CHANGED", _varbit(11327, 1),
               lambda: game.stations["retort"].update({"progress": 1})))
    game.feed(("VARBIT_CHANGED", _varbit(11341, 0),
               lambda: game.stations["retort"].update({"progress": 0, "potionName": None})))

    mm._process_retort_speedup(ctx, game.station("Retort"), "Mammoth-might mix")

    assert (0, 0) in offsets
    assert game.in_place == 1


def test_retort_speedup_stops_once_the_station_holds_a_DIFFERENT_potion(game):
    """USER 2026-09-17: "we clicked one too many on that station creating an additional potion".
    The Retort finished mid-burst, the extra clicks auto-started the leftover 'Azure aura mix'
    in it, and the loop kept going because progress was climbing again -- for the NEW potion.
    The old guard only asked "is it empty", which a different potion passes straight through."""
    ctx = _ctx_with_events()
    game.stations["retort"] = {"progress": 0, "potionName": "Mammoth-might mix"}
    game.varbits[11341] = 1
    game.feed(("VARBIT_CHANGED", _varbit(11327, 1),
               lambda: game.stations["retort"].update({"progress": 1})))
    # The station moves on to someone else's potion; progress keeps climbing, so only an
    # IDENTITY check can stop the loop.
    game.feed(("VARBIT_CHANGED", _varbit(11327, 2),
               lambda: game.stations["retort"].update({"progress": 2,
                                                       "potionName": "Azure aura mix"})))
    game.feed(("VARBIT_CHANGED", _varbit(11327, 3),
               lambda: game.stations["retort"].update({"progress": 3})))

    mm._process_retort_speedup(ctx, game.station("Retort"), "Mammoth-might mix")

    assert game.in_place == 1, "clicks must stop the moment the station is not on our potion"


def test_station_hover_waits_for_the_projection_to_stop_sliding(game, monkeypatch):
    """USER 2026-09-17: "we dont wait until we are stopped to hover meaning the cursor slips
    out" -- a hover taken mid-walk aims at a projection that keeps moving."""
    moving = iter([(100, 100), (140, 160), (180, 220), (200, 240), (200, 240), (200, 240)])
    monkeypatch.setattr(mm, "_refresh_for", lambda e: (lambda: next(moving, (200, 240))))
    hovered = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, entity, **kw: hovered.append(entity.get("name")) or True)
    ctx = FakeCtx()

    mm._hover_when_settled(ctx, "Retort", check_ui_blockers=False)

    # It waited out the three moving reads and only parked once two agreed.
    assert hovered == ["Retort"]
    assert game.clock["t"] >= 1000.0 + mm.HOVER_SETTLE_POLL_S * 3


def test_retort_speedup_stops_when_http_says_the_station_is_empty(game):
    """A lost potion->0 packet must cost a periodic HTTP read, never a run to the budget."""
    ctx = _ctx_with_events()
    game.stations["retort"] = {"progress": 0, "potionName": None}
    game.varbits[11341] = 0
    start = game.clock["t"]

    mm._process_retort_speedup(ctx, game.station("Retort"), "Mammoth-might mix")

    assert game.in_place == 0
    assert game.clock["t"] - start <= mm.RETORT_HTTP_CONFIRM_S + 1.0


def test_agitator_speedup_clicks_on_the_cue_and_again_on_the_next_tick(game, monkeypatch):
    monkeypatch.setattr(mm, "SPEEDUP_MISS_CHANCE", 0.0)
    ctx = _ctx_with_events()
    station = game.station("Agitator")
    game.stations["agitator"] = {"progress": 2, "potionName": "Alco-augmentator"}
    game.varbits[11337] = 4
    cue = {"id": mm.AGITATOR_CUE_GRAPHICS_ID, "x": station["x"], "y": station["y"], "plane": 0, "tick": 3}
    game.feed(("GRAPHICS_OBJECT_CREATED", cue, lambda: game.graphics.append(dict(cue))))
    game.feed(("VARBIT_CHANGED", _varbit(11329, 3)))
    game.feed(("VARBIT_CHANGED", _varbit(11337, 5), lambda: game.varbits.update({11337: 5})))

    mm._process_agitator_speedup(ctx, station)

    assert game.in_place == 2


def test_alembic_speedup_clicks_once_on_the_cue(game, monkeypatch):
    monkeypatch.setattr(mm, "SPEEDUP_MISS_CHANCE", 0.0)
    ctx = _ctx_with_events()
    station = game.station("Alembic")
    game.stations["alembic"] = {"progress": 2, "potionName": "Liplack liquor"}
    cue = {"id": mm.ALEMBIC_CUE_GRAPHICS_ID, "x": station["x"], "y": station["y"], "plane": 0, "tick": 3}
    game.feed(("GRAPHICS_OBJECT_CREATED", cue, lambda: game.graphics.append(dict(cue))))
    game.feed(("VARBIT_CHANGED", _varbit(11328, 3)))

    mm._process_alembic_speedup(ctx, station)

    assert game.in_place == 1


def test_flash_speedup_ignores_a_cue_at_another_station(game, monkeypatch):
    monkeypatch.setattr(mm, "SPEEDUP_MISS_CHANCE", 0.0)
    ctx = _ctx_with_events()
    station = game.station("Alembic")
    game.stations["alembic"] = {"progress": 2, "potionName": "Liplack liquor"}
    far = {"id": mm.ALEMBIC_CUE_GRAPHICS_ID, "x": station["x"] + 10, "y": station["y"], "plane": 0}
    wrong_id = {"id": mm.AGITATOR_CUE_GRAPHICS_ID, "x": station["x"], "y": station["y"], "plane": 0}
    game.feed(("GRAPHICS_OBJECT_CREATED", far, lambda: game.graphics.append(dict(far))))
    game.feed(("GRAPHICS_OBJECT_CREATED", wrong_id, lambda: game.graphics.append(dict(wrong_id))))

    mm._process_alembic_speedup(ctx, station)

    assert game.in_place == 0


# --- the speedup click is verified before it fires (LIVE 2026-09-17) -------------------
#
# The cue click is a raw click_human at a cursor parked up to tens of seconds earlier. Idle
# drift and the camera moving the projection pulled the two 260px apart live: both Agitator
# attempts missed AND one click landed in the then-empty Alembic, which auto-started an
# Agitator order there and livelocked the run on "Alembic still busy".

def test_speedup_click_is_withheld_when_the_cursor_is_not_on_the_station(game, monkeypatch):
    monkeypatch.setattr(mm, "SPEEDUP_MISS_CHANCE", 0.0)
    # The re-hover cannot rescue it either -- hover_matches keeps saying "not the Alembic".
    game.hover_ok = False
    ctx = _ctx_with_events()
    station = game.station("Alembic")
    game.stations["alembic"] = {"progress": 2, "potionName": "Liplack liquor"}
    cue = {"id": mm.ALEMBIC_CUE_GRAPHICS_ID, "x": station["x"], "y": station["y"], "plane": 0, "tick": 3}
    game.feed(("GRAPHICS_OBJECT_CREATED", cue, lambda: game.graphics.append(dict(cue))))

    mm._process_alembic_speedup(ctx, station)

    assert game.in_place == 0, "a click that cannot be placed must be dropped, never fired blind"


def test_speedup_click_rehovers_once_and_fires_when_that_lands_on_target(game, monkeypatch):
    monkeypatch.setattr(mm, "SPEEDUP_MISS_CHANCE", 0.0)
    ctx = _ctx_with_events()
    station = game.station("Alembic")
    game.stations["alembic"] = {"progress": 2, "potionName": "Liplack liquor"}
    cue = {"id": mm.ALEMBIC_CUE_GRAPHICS_ID, "x": station["x"], "y": station["y"], "plane": 0, "tick": 3}
    game.feed(("GRAPHICS_OBJECT_CREATED", cue, lambda: game.graphics.append(dict(cue))))

    # Drifted off at cue time; the corrective re-hover puts it back.
    game.hover_ok = False
    rehovered = []

    def hover_entity(ser, entity, **kw):
        rehovered.append(entity.get("name"))
        game.hover_ok = True
        return True
    monkeypatch.setattr(controller, "hover_entity", hover_entity)

    mm._process_alembic_speedup(ctx, station)

    # The click fired only BECAUSE the re-hover put the cursor back on the station.
    assert rehovered.count("Alembic") >= 1
    assert game.in_place == 1


def test_speedup_holds_the_idle_drift_off_while_the_cursor_is_parked(game, monkeypatch):
    monkeypatch.setattr(mm, "SPEEDUP_MISS_CHANCE", 0.0)
    ctx = _ctx_with_events()
    station = game.station("Alembic")
    game.stations["alembic"] = {"progress": 2, "potionName": "Liplack liquor"}
    cue = {"id": mm.ALEMBIC_CUE_GRAPHICS_ID, "x": station["x"], "y": station["y"], "plane": 0, "tick": 3}
    game.feed(("GRAPHICS_OBJECT_CREATED", cue, lambda: game.graphics.append(dict(cue))))

    mm._process_alembic_speedup(ctx, station)

    assert game.busy, "the cue wait must hold the anchorless idle drift off the parked cursor"


def test_flash_speedup_falls_back_to_polling_without_events(game, monkeypatch):
    monkeypatch.setattr(mm, "SPEEDUP_MISS_CHANCE", 0.0)
    ctx = FakeCtx()
    station = game.station("Alembic")
    game.stations["alembic"] = {"progress": 2, "potionName": "Liplack liquor"}
    game.graphics.append({"id": mm.ALEMBIC_CUE_GRAPHICS_ID, "x": station["x"], "y": station["y"], "plane": 0})

    mm._process_alembic_speedup(ctx, station)

    assert game.in_place == 1


def test_process_one_confirms_start_and_finish_on_the_station_varbit(game):
    ctx = _ctx_with_events()
    unfinished_id = next(k for k, v in mm.UNFINISHED_IDS.items() if v == "Liplack liquor")
    game.inventory = [{"id": unfinished_id, "name": "Liplack liquor"}]
    station = game.station("Alembic")

    def start(g):
        g.feed(("VARBIT_CHANGED", _varbit(11342, 7),
                lambda: g.stations["alembic"].update({"potionName": "Liplack liquor", "progress": 0})))
        g.feed(("VARBIT_CHANGED", _varbit(11342, 0),
                lambda: (g.stations["alembic"].update({"potionName": None}),
                         g.inventory.__setitem__(0, {"id": unfinished_id + 10, "name": "Liplack liquor"}))),
               ("INVENTORY_CHANGED", None))
    game.on_click["Alembic"] = start
    config = dict(mm.DEFAULT_CONFIG)
    config["speedups"] = {"retort": False, "agitator": False, "alembic": False}
    start_t = game.clock["t"]

    mm._process_one(ctx, config, "Alembic", station, "Liplack liquor")

    assert game.clicks == ["Alembic"]
    assert game.inventory[0]["id"] == unfinished_id + 10
    assert game.clock["t"] - start_t < 10.0


# --- deposit ---------------------------------------------------------------------------

def test_deposit_phase_confirms_on_the_inventory_emptying(game):
    ctx = _ctx_with_events()
    finished_id = next(iter(mm.FINISHED_IDS))
    game.inventory = [{"id": finished_id, "name": mm.FINISHED_IDS[finished_id]}]
    game.on_click["Conveyor belt"] = lambda g: g.feed(
        ("INVENTORY_CHANGED", None, lambda: g.inventory.clear()),
        ("VARBIT_CHANGED", _varbit(11315, 0)),
    )

    mm.deposit_phase(ctx, dict(mm.DEFAULT_CONFIG))

    assert game.clicks == ["Conveyor belt"]
    assert ctx.state.data["mm_phase"] == "orders"


def test_deposit_phase_retries_when_potions_remain(game):
    ctx = _ctx_with_events()
    finished_id = next(iter(mm.FINISHED_IDS))
    game.inventory = [{"id": finished_id, "name": mm.FINISHED_IDS[finished_id]}]

    mm.deposit_phase(ctx, dict(mm.DEFAULT_CONFIG))

    assert game.clicks == ["Conveyor belt"]
    assert ctx.state.data.get("mm_phase") != "orders"


# --- digweed hint ----------------------------------------------------------------------

def test_on_event_flags_a_digweed_spawn_for_the_next_tick():
    ctx = FakeCtx()
    mm.on_event(ctx, "VARBIT_CHANGED", {"varbitId": 11330, "value": 1})
    assert ctx.state.data.get("mm_digweed_hint") is True


def test_on_event_ignores_unrelated_varbits():
    ctx = FakeCtx()
    mm.on_event(ctx, "VARBIT_CHANGED", {"varbitId": 11327, "value": 3})
    assert "mm_digweed_hint" not in ctx.state.data


# --- 2026-09-06 second pass: chaining, anticipation, long waits -------------------------

def test_tick_chains_phase_transitions_inside_one_call(monkeypatch):
    """deposit -> orders -> mix used to cost an engine-loop iteration (~1s) per arrow; a human
    reads the new orders and pulls the first lever the moment the belt takes the potions."""
    ctx = FakeCtx()
    ctx.state.config = dict(mm.DEFAULT_CONFIG)
    ctx.state.data["mm_level_checked"] = True
    ctx.state.data["mm_phase"] = "a"
    calls = []

    def a(c, cfg):
        calls.append("a")
        c.state.data["mm_phase"] = "b"

    def b(c, cfg):
        calls.append("b")
        c.state.data["mm_phase"] = "c"

    def c_(c, cfg):
        calls.append("c")

    monkeypatch.setattr(mm, "PHASES", {"a": a, "b": b, "c": c_})
    monkeypatch.setattr(mm, "maybe_collect_digweed", lambda ctx, cfg: None)
    mm.tick(ctx)
    assert calls == ["a", "b", "c"]


def test_tick_chain_is_bounded(monkeypatch):
    ctx = FakeCtx()
    ctx.state.config = dict(mm.DEFAULT_CONFIG)
    ctx.state.data["mm_level_checked"] = True
    ctx.state.data["mm_phase"] = "a"
    calls = []

    def a(c, cfg):
        calls.append("a")
        c.state.data["mm_phase"] = "b"

    def b(c, cfg):
        calls.append("b")
        c.state.data["mm_phase"] = "a"

    monkeypatch.setattr(mm, "PHASES", {"a": a, "b": b})
    monkeypatch.setattr(mm, "maybe_collect_digweed", lambda ctx, cfg: None)
    mm.tick(ctx)
    assert len(calls) == mm.TICK_CHAIN_MAX + 1


def test_tick_chain_stops_when_paused(monkeypatch):
    ctx = FakeCtx()
    ctx.state.config = dict(mm.DEFAULT_CONFIG)
    ctx.state.data["mm_level_checked"] = True
    ctx.state.data["mm_phase"] = "a"
    calls = []

    def a(c, cfg):
        calls.append("a")
        c.state.data["mm_phase"] = "b"
        c.state.paused = True

    def b(c, cfg):
        calls.append("b")

    monkeypatch.setattr(mm, "PHASES", {"a": a, "b": b})
    monkeypatch.setattr(mm, "maybe_collect_digweed", lambda ctx, cfg: None)
    mm.tick(ctx)
    assert calls == ["a"]


def test_pull_lever_prehovers_the_next_target_while_the_pull_lands(game, monkeypatch):
    hovers = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, entity, **kw: hovers.append(entity.get("name")) or True)
    ctx = _ctx_with_events()
    game.on_click["Mox lever"] = lambda g: (
        g.feed(("MENU_CLICKED", {"target": "Mox lever", "tick": 5},
                lambda: g.last_action.update({"target": "Mox lever", "tick": 5}))),
        g.feed(("VARBIT_CHANGED", _varbit(11324, 1), lambda: g.varbits.update({11324: 1}))),
    )
    assert mm._pull_lever(ctx, "mox", next_name="Aga lever") is True
    assert hovers == ["Aga lever"]


def test_make_base_prehovers_the_vessel_on_the_last_pull_and_the_next_lever_on_the_take(game, monkeypatch):
    hovers = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, entity, **kw: hovers.append(entity.get("name")) or True)
    ctx = _ctx_with_events()
    chosen = [{"name": "Mammoth-might mix", "station": "Retort", "mox": 3, "aga": 0, "lye": 0}]
    _install_lab(game, chosen)
    assert mm._make_base(ctx, chosen[0], next_name="Lye lever") is True
    # pull 1 -> hover lever 2, pull 2 -> hover lever 3, pull 3 -> hover the vessel,
    # take -> hover whatever comes next (here the caller's Lye lever)
    assert hovers == ["Mox lever", "Mox lever", "Mixing vessel", "Lye lever"]


def test_process_one_waits_for_a_NEW_finished_potion_not_just_any(game, monkeypatch):
    """The game hands out duplicate orders freely, so the previous order's finished potion is
    already in the inventory when the second identical one starts. A "> 0" finish check is
    already true the instant that second station is clicked, so the wait returns immediately,
    process_phase sees nothing unfinished and deposits -- stranding a potion still inside the
    station. LIVE 2026-09-17: that is how the Alembic was left frozen on a 'Megalite liquid'."""
    order = "Mammoth-might mix"
    unfinished_id = next(k for k, v in mm.UNFINISHED_IDS.items() if v == order)
    finished_id = unfinished_id + 10

    # One identical finished potion is ALREADY held, from the cycle's earlier duplicate order.
    game.inventory = [{"id": finished_id, "name": order}]
    game.stations["agitator"] = {"progress": 0, "potionName": order}
    ctx = _ctx_with_events()
    monkeypatch.setattr(mm, "_process_agitator_speedup", lambda *a, **k: None)
    monkeypatch.setattr(mm, "_await", lambda *a, **k: True)        # the START confirms

    captured = {}

    def fake_await_long(_ctx, _sig, _budget, confirm=None, **_kw):
        captured["confirm"] = confirm
        return bool(confirm and confirm())
    monkeypatch.setattr(mm, "_await_long", fake_await_long)

    mm._process_one(ctx, {}, "Agitator", game.station("Agitator"), order, next_name=None)

    confirm = captured["confirm"]
    assert confirm() is False, "the potion already held must not count as this station's finish"

    game.inventory.append({"id": finished_id, "name": order})   # the station actually delivers
    assert confirm() is True
    hovers = []
    monkeypatch.setattr(controller, "hover_entity",
                        lambda ser, entity, **kw: hovers.append(entity.get("name")) or True)
    ctx = _ctx_with_events()
    unfinished_id = next(k for k, v in mm.UNFINISHED_IDS.items() if v == "Liplack liquor")
    game.inventory = [{"id": unfinished_id, "name": "Liplack liquor"}]
    station = game.station("Alembic")

    def start(g):
        g.feed(("VARBIT_CHANGED", _varbit(11342, 7),
                lambda: g.stations["alembic"].update({"potionName": "Liplack liquor"})))
        g.feed(("VARBIT_CHANGED", _varbit(11342, 0),
                lambda: (g.stations["alembic"].update({"potionName": None}),
                         g.inventory.__setitem__(0, {"id": unfinished_id + 10, "name": "Liplack liquor"}))),
               ("INVENTORY_CHANGED", None))
    game.on_click["Alembic"] = start
    config = dict(mm.DEFAULT_CONFIG)
    config["speedups"] = {"retort": False, "agitator": False, "alembic": False}

    mm._process_one(ctx, config, "Alembic", station, "Liplack liquor", next_name="Conveyor belt")

    assert hovers == ["Conveyor belt"]


def test_apply_digweed_targets_a_finished_liplack_too(game):
    """Wiki: digweed can be added before OR after processing -- a Liplack liquor that has
    already been through its station is still a target until the belt takes it."""
    ctx = _ctx_with_events()
    finished_liplack = next(k for k, v in mm.FINISHED_IDS.items() if v == "Liplack liquor")
    game.inventory = [
        {"id": mm.MM_LAB_SPECIAL_HERB_ID, "name": "Digweed", "screenX": 10, "screenY": 10},
        {"id": finished_liplack, "name": "Liplack liquor", "screenX": 20, "screenY": 20},
    ]
    game.on_click["Liplack liquor"] = lambda g: g.feed(
        ("INVENTORY_CHANGED", None, lambda: g.inventory.pop(0)))

    assert mm._apply_digweed_if_held(ctx) is True
    assert game.clicks == ["Digweed", "Liplack liquor"]


def test_await_long_stops_between_chunks_when_told_to(game):
    """A chunked wait re-checks `stop` between chunks so a cue wait ends when the potion
    finishes without one, instead of running to its full budget."""
    ctx = _ctx_with_events()
    start = game.clock["t"]
    reads = {"n": 0}

    def stop():
        reads["n"] += 1
        return reads["n"] >= 2

    out = mm._await_long(ctx, "INVENTORY_CHANGED", 60.0, confirm=lambda: False, stop=stop)
    assert not out
    assert game.clock["t"] - start < 60.0


def test_await_long_fidgets_between_chunks(game, monkeypatch):
    fidgets = []
    monkeypatch.setattr(controller, "fidget_once", lambda: fidgets.append(1))
    ctx = _ctx_with_events()
    mm._await_long(ctx, "INVENTORY_CHANGED", 12.0, confirm=lambda: False)
    assert len(fidgets) >= 2


# --- refused clicks, digweed between phases, stall watch ---------------------------------

def test_refused_lever_click_is_retried_not_treated_as_missing(game, monkeypatch):
    """click_entity returning falsy (aim off-window, withheld) is not "no lever here": the
    lever is found, the click just did not happen, so the attempt loop retries it."""
    ctx = _ctx_with_events()
    outcomes = iter([False, True])

    def click_entity(ser, entity, **kw):
        game.clicks.append(entity.get("name"))
        ok = next(outcomes)
        if ok:
            game.feed(("MENU_CLICKED", {"target": "Mox lever", "tick": 5},
                       lambda: game.last_action.update({"target": "Mox lever", "tick": 5})))
            game.feed(("VARBIT_CHANGED", _varbit(11324, 1), lambda: game.varbits.update({11324: 1})))
        return ok
    monkeypatch.setattr(controller, "click_entity", click_entity)

    assert mm._pull_lever(ctx, "mox") is True
    assert game.clicks == ["Mox lever", "Mox lever"]


def test_missing_lever_still_fails_immediately(game):
    ctx = _ctx_with_events()
    game.objects = [o for o in game.objects if o["name"] != "Mox lever"]
    assert mm._pull_lever(ctx, "mox") is False
    assert game.clicks == []


def test_tick_checks_digweed_before_every_chained_phase(monkeypatch):
    ctx = FakeCtx()
    ctx.state.config = dict(mm.DEFAULT_CONFIG)
    ctx.state.data["mm_level_checked"] = True
    ctx.state.data["mm_phase"] = "a"
    calls = []

    def a(c, cfg):
        calls.append("a")
        c.state.data["mm_phase"] = "b"

    def b(c, cfg):
        calls.append("b")

    monkeypatch.setattr(mm, "PHASES", {"a": a, "b": b})
    monkeypatch.setattr(mm, "maybe_collect_digweed", lambda ctx, cfg: calls.append("digweed"))
    monkeypatch.setattr(mm, "_stuck_watch", lambda ctx: None)
    mm.tick(ctx)
    assert calls == ["digweed", "a", "digweed", "b"]


def test_process_phase_checks_digweed_between_stations(monkeypatch):
    ctx = FakeCtx()
    ctx.state.data["mm_chosen_orders"] = [
        {"station": "Alembic", "name": "Mammoth-might mix"},
        {"station": "Agitator", "name": "Mystic mana amalgam"},
    ]
    calls = []
    left = {"n": 2}

    def fake_process_one(_ctx, _config, station_name, _station, _order_name, **_kw):
        calls.append(station_name)
        left["n"] -= 1

    monkeypatch.setattr(controller, "get_inventory", lambda: _unprocessed(left["n"]))
    monkeypatch.setattr(controller, "get_mixology_state", lambda: {"stations": {}})
    monkeypatch.setattr(mm, "find_object", lambda name: {"name": name})
    monkeypatch.setattr(mm, "_process_one", fake_process_one)
    monkeypatch.setattr(mm, "maybe_collect_digweed", lambda ctx, cfg: calls.append("digweed"))

    mm.process_phase(ctx, {})

    assert calls == ["digweed", "Alembic", "digweed", "Agitator"]
    assert ctx.state.data["mm_phase"] == "deposit"


def test_stuck_watch_stops_a_run_that_makes_no_progress(game):
    """A phase that returns every tick with nothing changing (out of the lab, an order set that
    never fills, a click that never lands) used to be indistinguishable from a healthy run."""
    ctx = _ctx_with_events()
    ctx.state.data["mm_phase"] = "orders"
    mm._stuck_watch(ctx)
    game.clock["t"] += mm.STALL_STOP_S + 1
    mm._stuck_watch(ctx)
    assert ctx.state.safety_stopped
    assert "no progress" in (ctx.state.stop_reason or "")


def test_stuck_watch_resets_on_any_progress(game):
    ctx = _ctx_with_events()
    ctx.state.data["mm_phase"] = "orders"
    mm._stuck_watch(ctx)
    game.clock["t"] += mm.STALL_STOP_S - 1
    game.inventory.append({"id": 30011, "name": "Mammoth-might mix"})   # something happened
    mm._stuck_watch(ctx)
    game.clock["t"] += mm.STALL_STOP_S - 1
    mm._stuck_watch(ctx)
    assert not ctx.state.safety_stopped
