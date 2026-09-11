"""routines/mining_guild_iron.py -- the single-tile Mining Guild iron loop.

Every number here is from recordings/2026-09-11_121030_mining-guild-single-tile.*: stand tile
(3021,9721), iron ids 11364/11365 live and 11390/11391 spent, two ores per rock, the second one
depleting it, deposit by Use-ore-on-box from 8 tiles.

The cycle tests drive a REAL bridge_events.EventStream through a scripted controller.poll_udp
(the mixology harness shape) so await_outcome's own mark/since semantics are what is exercised.
"""

import os
import sys
from collections import deque

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import bridge_events
import controller
import mining_guild_iron as mgi

STAND = (3021, 9721, 0)
N, W, S = (3021, 9722), (3020, 9721), (3021, 9720)


def _obj(obj_id, x, y, name="Iron rocks", **over):
    d = {"id": obj_id, "name": name, "x": x, "y": y, "plane": 0, "distance": 1,
         "visible": True, "screenX": 500 + (x - 3021) * 30, "screenY": 300 + (9721 - y) * 30,
         "actions": ["Mine"] if obj_id in mgi.ROCK_IDS else []}
    d.update(over)
    return d


# --- pure helpers ------------------------------------------------------------------------

def test_discover_rocks_takes_cardinal_iron_live_or_spent_and_maps_to_the_live_id():
    scene = [
        _obj(11365, *N),                       # north, up
        _obj(11390, *W, name="Rocks"),         # west, SPENT -> live id 11364
        _obj(11365, *S),                       # south, up
        _obj(11364, 3020, 9722),               # diagonal: not adjacent
        _obj(11366, 3022, 9721, name="Coal rocks"),   # east neighbour but coal
    ]
    rocks = mgi.discover_rocks(scene, STAND)
    assert sorted((r["x"], r["y"], r["id"]) for r in rocks) == sorted(
        [(*N, 11365), (*W, 11364), (*S, 11365)])
    assert all(r["plane"] == 0 for r in rocks)


def test_pick_rock_round_robins_away_from_the_rock_just_spent():
    rocks = [{"x": x, "y": y, "plane": 0, "id": 11365} for x, y in (N, W, S)]
    spent_at = {N: 10.0, W: 5.0, S: 8.0}
    # All three up: the one spent longest ago wins, unless it is the one we just left.
    chosen = mgi.pick_rock(rocks, up=lambda r: True, last=None, spent_at=spent_at)
    assert (chosen["x"], chosen["y"]) == W
    chosen = mgi.pick_rock(rocks, up=lambda r: True, last=W, spent_at=spent_at)
    assert (chosen["x"], chosen["y"]) == S
    # A rock never spent this session outranks every rock that has been.
    chosen = mgi.pick_rock(rocks, up=lambda r: True, last=None, spent_at={N: 1.0, W: 2.0})
    assert (chosen["x"], chosen["y"]) == S
    # Only rocks that are UP count; none up -> None.
    chosen = mgi.pick_rock(rocks, up=lambda r: (r["x"], r["y"]) == N, last=None, spent_at=spent_at)
    assert (chosen["x"], chosen["y"]) == N
    assert mgi.pick_rock(rocks, up=lambda r: False, last=None, spent_at=spent_at) is None
    # The just-spent rock is still taken when it is the only one up (it respawns in 2.4s).
    chosen = mgi.pick_rock(rocks, up=lambda r: (r["x"], r["y"]) == W, last=W, spent_at=spent_at)
    assert (chosen["x"], chosen["y"]) == W


def test_deposit_due_is_free_slots_below_the_threshold():
    assert mgi.deposit_due(free_slots=1, threshold=2)
    assert mgi.deposit_due(free_slots=0, threshold=2)
    assert not mgi.deposit_due(free_slots=2, threshold=2)
    assert not mgi.deposit_due(free_slots=None, threshold=2)     # unreadable: keep mining


# --- the scripted game -------------------------------------------------------------------

class FakeState:
    def __init__(self):
        self.data = {}
        self.config = dict(mgi.DEFAULT_CONFIG)
        self.safety_stopped = False
        self.stop_reason = None
        self.paused = False

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.stop_reason = reason


class FakeCtx:
    def __init__(self, events):
        self.ser = object()
        self.state = FakeState()
        self.events = events


class Game:
    """Scene + inventory + a queue of UDP batches. Each poll_udp delivers ONE batch and runs the
    batch's `then` mutations first, so the pushed signal and the HTTP truth land together."""

    def __init__(self):
        self.rocks = {N: 11365, W: 11364, S: 11365}      # tile -> id currently standing there
        self.player = {"x": 3021, "y": 9721, "plane": 0, "regionId": 12183, "moving": False,
                       "miningXp": 1000}
        self.ore = 5
        self.idle = True
        self.clicks = []            # (kind, name-or-tile)
        self.hovers = []
        self.in_place = 0
        self.batches = deque()
        self.auto_ore = False       # each rock click feeds its own two-ore script

    def script_rock(self, tile):
        self.feed(_xp(), (None, None, self.first_ore))
        self.feed(_xp(), _despawn(tile), (None, None, lambda: self.second_ore(tile)))
        self.feed((None, None, lambda: self.respawn(tile)))

    # --- bridge answers
    def objects(self, **_kw):
        scene = [_obj(i, x, y, name="Rocks" if i in mgi.SPENT_TO_LIVE else "Iron rocks")
                 for (x, y), i in self.rocks.items()]
        scene.append(_obj(10529, 3013, 9720, name="Bank Deposit Box", distance=8))
        return scene

    def object_at(self, x, y, plane, obj_id, **_kw):
        if self.rocks.get((x, y)) != obj_id:
            return None
        return _obj(obj_id, x, y)

    def inventory(self):
        items = [{"name": "Open gem sack", "qty": 1, "screenX": 900, "screenY": 380, "slot": 0}]
        if self.ore:
            items.append({"name": "Iron ore", "qty": 1, "screenX": 930, "screenY": 390, "slot": 1})
            items += [{"name": "Iron ore", "qty": 1, "screenX": 960, "screenY": 390, "slot": 2 + k}
                      for k in range(self.ore - 1)]
        return items

    def feed(self, *events):
        self.batches.append(list(events))

    def poll_udp(self, _sock):
        if not self.batches:
            return []
        out = []
        for ev in self.batches.popleft():
            if len(ev) > 2 and ev[2] is not None:
                ev[2]()
            out.append((ev[0], ev[1]))
        return out

    # --- game mutations used by scripts
    def first_ore(self):
        self.player["miningXp"] += 35
        self.ore += 1

    def second_ore(self, tile):
        self.first_ore()
        self.rocks[tile] = mgi.ROCK_IDS[self.rocks[tile]]     # depletes

    def respawn(self, tile):
        self.rocks[tile] = mgi.SPENT_TO_LIVE[self.rocks[tile]]


def _xp():
    return ("XP_GAINED", {"skill": "Mining", "delta": 35, "tick": 1})


def _despawn(tile):
    return ("OBJECT_DESPAWNED", {"x": tile[0], "y": tile[1], "plane": 0, "tick": 1})


@pytest.fixture
def game(monkeypatch):
    g = Game()
    monkeypatch.setattr(controller, "poll_udp", g.poll_udp)
    monkeypatch.setattr(controller, "get_objects", g.objects)
    monkeypatch.setattr(controller, "get_object_at", g.object_at)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "ground_tile_refresh_target", lambda *a, **k: (lambda: None))
    monkeypatch.setattr(controller, "get_player", lambda: dict(g.player))
    monkeypatch.setattr(controller, "get_inventory", g.inventory)
    monkeypatch.setattr(controller, "get_combat", lambda: {"isIdle": g.idle})
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane, **k: {"x": x, "y": y, "screenX": 600, "screenY": 540,
                                                  "onScreen": True})
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(controller, "sample_human_delay", lambda a, b: 0.0)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "continue_box_open", lambda: False)
    monkeypatch.setattr(controller, "dismiss_continue_box", lambda ser: True)
    monkeypatch.setattr(controller, "key_down", lambda ser, k: None)
    monkeypatch.setattr(controller, "key_up", lambda ser, k: None)
    monkeypatch.setattr(controller, "fidget_once", lambda: None)

    def hover_entity(ser, entity, **kw):
        g.hovers.append((entity.get("x"), entity.get("y")))
        return True
    monkeypatch.setattr(controller, "hover_entity", hover_entity)

    def click_entity(ser, entity, **kw):
        if entity.get("name") == "Iron rocks":
            tile = (entity["x"], entity["y"])
            g.clicks.append(("rock", tile))
            if g.auto_ore:
                g.script_rock(tile)
        elif entity.get("name") == "Iron ore":
            g.clicks.append(("use", "Iron ore"))
        elif entity.get("name") == "Bank Deposit Box":
            g.clicks.append(("box", (entity["x"], entity["y"])))
            # The game walks to the box and deposits every ore there.
            g.feed(("INVENTORY_CHANGED", None,
                    lambda: (setattr(g, "ore", 0), g.player.update(x=3014, y=9720))))
        else:
            g.clicks.append(("walk", (entity.get("x"), entity.get("y"))))
            g.feed(("PLAYER_STOPPED", None, lambda: g.player.update(x=3021, y=9721)))
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity)

    clock = {"t": 1000.0}
    monkeypatch.setattr(mgi, "_now", lambda: clock["t"])
    monkeypatch.setattr(mgi, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    g.clock = clock
    return g


def _ctx(game):
    ctx = FakeCtx(bridge_events.EventStream(sock=None))
    ctx.state.data[mgi.ROCKS_KEY] = mgi.discover_rocks(game.objects(), STAND)
    return ctx


# --- one rock ----------------------------------------------------------------------------

def test_mine_rock_waits_through_the_first_ore_and_ends_on_the_depletion(game):
    ctx = _ctx(game)
    rock = _obj(11365, *N)
    # First ore: XP lands, rock still standing -> keep waiting. Second ore: XP + despawn.
    game.feed(_xp(), (None, None, game.first_ore))
    game.feed(_xp(), _despawn(N), (None, None, lambda: game.second_ore(N)))
    result = mgi.mine_rock(ctx, rock, hover_next=_obj(11364, *W))
    assert result == "depleted"
    assert game.clicks == [("rock", N)]
    assert game.hovers == [W]            # the next rock was parked under the cursor immediately
    assert game.ore == 7


def test_mine_rock_reports_a_click_that_produced_nothing(game):
    ctx = _ctx(game)
    rock = _obj(11365, *N)
    game.idle = True                     # no swing animation ever starts
    result = mgi.mine_rock(ctx, rock, hover_next=None)
    assert result == "failed"
    assert game.clock["t"] - 1000.0 < mgi.CLICK_GRACE_S + mgi.CLICK_GRACE_S   # failed fast, not at the full budget


def test_mine_rock_does_not_call_a_swinging_player_failed_before_the_ore_budget(game):
    ctx = _ctx(game)
    rock = _obj(11365, *N)
    game.idle = False                    # animation running, ore not yet landed (a missed swing)
    result = mgi.mine_rock(ctx, rock, hover_next=None)
    assert result == "timeout"
    assert game.clock["t"] - 1000.0 >= mgi.ORE_BUDGET_S


# --- the chain ---------------------------------------------------------------------------

def test_tick_chains_rocks_in_rotation_with_the_next_one_prehovered(game):
    ctx = _ctx(game)
    # Script: each rock click gets its two ores; the chain should visit three rocks, never the
    # same one twice in a row, and the hover before each click names the rock clicked next.
    for tile in (W, S, N):
        game.feed(_xp(), (None, None, game.first_ore))
        game.feed(_xp(), _despawn(tile), (None, None, lambda t=tile: game.second_ore(t)))
        game.feed((None, None, lambda t=tile: game.respawn(t)))
    ctx.state.data[mgi.CHAIN_LIMIT_KEY] = 3
    mgi.tick(ctx)
    rock_clicks = [t for k, t in game.clicks if k == "rock"]
    assert len(rock_clicks) == 3
    assert all(a != b for a, b in zip(rock_clicks, rock_clicks[1:]))
    # Every hover names the rock that was clicked right after it.
    assert game.hovers[:2] == rock_clicks[1:3]


def test_tick_deposits_when_fewer_than_two_slots_are_free_then_walks_back_and_resumes(game):
    ctx = _ctx(game)
    game.ore = 25                        # 26 used with the gem sack -> 2 free
    game.auto_ore = True                 # the first rock's two ores fill it: 0 free
    ctx.state.data[mgi.CHAIN_LIMIT_KEY] = 2
    mgi.tick(ctx)
    kinds = [k for k, _ in game.clicks]
    assert kinds[:5] == ["rock", "use", "box", "walk", "rock"], game.clicks
    assert game.clicks[3] == ("walk", (3021, 9721))
    assert game.ore == 2                 # 27 deposited, then the next rock's two ores
    assert not ctx.state.data[mgi.FULL_KEY]
    assert not ctx.state.safety_stopped


def test_tick_safety_stops_outside_the_guild_or_with_no_rocks(game):
    ctx = _ctx(game)
    game.player["regionId"] = 12184
    mgi.tick(ctx)
    assert ctx.state.safety_stopped
    ctx = _ctx(game)
    game.player["regionId"] = 12183
    game.rocks = {}
    ctx.state.data.pop(mgi.ROCKS_KEY, None)
    mgi.tick(ctx)
    assert ctx.state.safety_stopped


def test_inventory_full_chat_forces_the_deposit(game):
    ctx = _ctx(game)
    mgi.on_event(ctx, "CHAT_MESSAGE", {"message": "Your inventory is too full to hold any more iron."})
    assert ctx.state.data.get(mgi.FULL_KEY)
    assert mgi.deposit_needed(ctx, free_slots=5)


def test_routine_is_wired_into_the_panel_dropdown():
    """Wiring into ROUTINE_FILES is a required step, not polish (CLAUDE.md)."""
    assert controller.ROUTINE_FILES.get("MINING_GUILD_IRON") == "routines/mining_guild_iron.py"
    java = open(os.path.join(os.path.dirname(__file__), "..", "..", "src", "main", "java", "com",
                             "example", "controller", "RoutineChoice.java"), encoding="utf-8").read()
    assert "MINING_GUILD_IRON(" in java
