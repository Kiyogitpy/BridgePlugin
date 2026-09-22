import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import chinchompa_hunting as ch
import controller


ANCHOR = (2557, 2936, 0)


def _obj(obj_id, x, y, plane=0, distance=1):
    return {"id": obj_id, "name": "Box trap", "x": x, "y": y, "plane": plane, "distance": distance}


class _State:
    def __init__(self):
        self.data = {}
        self.config = ch.DEFAULT_CONFIG
        self.safety_stopped = False
        self.safety_reason = None

    def trigger_safety_stop(self, reason):
        self.safety_stopped = True
        self.safety_reason = reason


class _Ctx:
    def __init__(self):
        self.ser = object()
        self.state = _State()
        self.events = None    # EventStream, or None under the old engine / a dead listener


# --- classify_trap ------------------------------------------------------------------

@pytest.mark.parametrize("obj_id,expected", [
    (9380, "set"),
    (9383, "full"),      # HUNTING_BOXTRAP_FULL_CHINCHOMPA_BIG -- the carnivorous/red one
    (9382, "full"),
    (9384, "full"),
    (721, "full"),
    (9385, "failed"),
    (9381, "transition"),
    (9390, "transition"),
    (9397, "transition"),
])
def test_classify_trap_matches_the_object_lookup_groupings(obj_id, expected):
    assert ch.classify_trap(obj_id) == expected


def test_classify_trap_ignores_anything_that_isnt_a_box_trap():
    """The whole ownership/scenery filter rests on this returning None -- /objects is a dump of
    every tile object in range, including whatever scenery shares a trap tile."""
    for not_a_trap in (0, 1, 9379, 9398, 62351, 2027):
        assert ch.classify_trap(not_a_trap) is None


def test_the_four_state_sets_are_disjoint():
    """A single id classified into two states would make _STATE_PRIORITY's tie-break meaningless
    and could have tick() 'Check' a trap that is really mid-animation."""
    sets = [ch.TRAP_SET_IDS, ch.TRAP_FULL_IDS, ch.TRAP_FAILED_IDS, ch.TRAP_TRANSITION_IDS]
    for i, a in enumerate(sets):
        for b in sets[i + 1:]:
            assert not (a & b)


# --- block_tile ---------------------------------------------------------------------

def test_block_tile_refuses_to_blacklist_a_tile_our_own_trap_is_standing_on(monkeypatch):
    """A tile with our trap on it is PROVEN usable -- a trap is standing on it. A refusal in that
    situation means the click was redundant, not that the tile is bad. Blocking it anyway drops
    it from the plan, so the routine never returns for that trap and it despawns (confirmed by
    the user: traps left on the ground time out). That is how a run lost two traps."""
    monkeypatch.setattr(ch.controller, "get_objects",
                        lambda radius=0, fresh=False: [_obj(9380, 2518, 9302)])
    ctx = _Ctx()

    ch.block_tile(ctx, (2518, 9302, 0), "the game refuses a trap there")

    assert ctx.state.data.get("chin_blocked", []) == []


def test_block_tile_still_blacklists_a_genuinely_unusable_tile(monkeypatch):
    """The case block_tile exists for: nothing on the tile and the game still refuses it."""
    monkeypatch.setattr(ch.controller, "get_objects", lambda radius=0, fresh=False: [])
    ctx = _Ctx()

    ch.block_tile(ctx, (2518, 9302, 0), "the game refuses a trap there")

    assert ctx.state.data["chin_blocked"] == [[2518, 9302, 0]]


# --- _await_action_finished ---------------------------------------------------------

def _animation_stubs(monkeypatch, frames):
    """Feed `frames` as successive /player animation values, on a fake clock driven by sleep."""
    seq = list(frames)
    state = {"i": 0}

    def _player():
        i = min(state["i"], len(seq) - 1)
        state["i"] += 1
        return {"animation": seq[i]}

    monkeypatch.setattr(ch.controller, "get_player", _player)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])
    return state


def test_await_action_finished_ignores_the_gap_between_the_two_animations(monkeypatch):
    """MEASURED live 2026-08-21: checking a trap is TWO animations -- 5212 (check/pick up) then
    5208 (auto re-lay) -- with ~0.66s of anim=-1 BETWEEN them. Treating that gap as "the action
    finished" is what let the next tick interrupt the re-lay, leaving the trap in the pack and
    the tile bare. Only a sustained idle means done."""
    # 5212 for a while, a short idle gap, 5208, then idle for good.
    frames = [5212] * 8 + [-1] * 4 + [5208] * 12 + [-1] * 40
    _animation_stubs(monkeypatch, frames)

    assert ch._await_action_finished() is True
    # The 5208 frames start at index 12; returning during the gap would mean stopping before them.
    assert ch.controller.get_player()["animation"] == -1


def test_await_action_finished_gives_up_if_the_animation_never_settles(monkeypatch):
    """A player stuck in a loop of animations must not hold the tick forever."""
    _animation_stubs(monkeypatch, [5212] * 10000)

    assert ch._await_action_finished() is False


def test_await_action_finished_returns_promptly_when_already_idle(monkeypatch):
    _animation_stubs(monkeypatch, [-1] * 100)

    assert ch._await_action_finished() is True


def test_await_action_finished_stops_as_soon_as_idle_coincides_with_the_trap_being_down(monkeypatch):
    """The trap being back on the tile is RuneLite's own "Trap placed" moment. Checking it only
    while the animation is already idle removes the ACTION_SETTLE_S margin (~1s of every ~10s
    cycle) without ever clicking mid-animation -- which is what made the earlier, cruder version
    of this measurably worse. The 0.66s false gap between the two animations cannot fool it,
    because during that gap the tile is bare."""
    frames = [5212] * 6 + [-1] * 3 + [5208] * 10 + [-1] * 30      # gap at index 6..8
    _animation_stubs(monkeypatch, frames)
    # Bare during the gap, down once the re-lay finishes.
    calls = {"n": 0}

    def _trap_down():
        calls["n"] += 1
        return calls["n"] > 1        # false the first time it is asked (the gap), true after

    assert ch._await_action_finished(done_when=_trap_down) is True
    assert calls["n"] >= 2, "must have been consulted during the gap and rejected it"


def test_await_action_finished_never_consults_done_when_mid_animation(monkeypatch):
    """Guard against reintroducing the regression: the check must not fire while the player is
    still animating, however tempting the early exit looks."""
    _animation_stubs(monkeypatch, [5208] * 10000)
    asked = {"n": 0}

    def _always_true():
        asked["n"] += 1
        return True

    assert ch._await_action_finished(done_when=_always_true) is False
    assert asked["n"] == 0, "done_when must only be consulted once the animation is idle"


def test_await_action_finished_waits_out_the_whole_animation(monkeypatch):
    """Regression guard for a speed change that MEASURED WORSE and was reverted on 2026-08-21.
    Returning as soon as the re-laid trap object was back on the ground (~1.3s before the
    animation ends) took the catch cycle from 9.7s to a median of 18s: 6 retrieve clicks and 7
    lays were dropped by the game because the player was still animating. The trap being placed
    is not the same as the player being free -- nothing here may short-circuit the idle wait."""
    _animation_stubs(monkeypatch, [5208] * 10000)

    assert ch._await_action_finished() is False


def test_await_placement_settled_records_the_window_without_sleeping_it(monkeypatch):
    """The trap hits the floor as an ITEM the moment it leaves the pack -- earlier than the
    object spawn, and much earlier than the animation ending. It must RECORD when the player
    comes free rather than sleep it out here: sleeping put the next action's HTTP setup and its
    300-800ms mouse flight entirely after the window, which is what made the timing compound away
    and miss it."""
    seen = {"n": 0}
    slept = []

    def _items(**_kw):
        seen["n"] += 1
        return [_item(2512, 9300)] if seen["n"] >= 3 else []

    monkeypatch.setattr(ch.controller, "get_ground_items", _items)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep",
                        lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s)))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])
    ctx = _Ctx()

    assert ch._await_placement_settled(ctx, (2512, 9300, 0)) is True
    expected = clock["t"] + ch.GAME_TICK_S * ch.PLACEMENT_SETTLE_TICKS
    assert abs(ctx.state.data["chin_free_at"] - expected) < 1e-6
    assert ch.GAME_TICK_S * ch.PLACEMENT_SETTLE_TICKS not in slept, \
        "the window must not be slept out here -- the caller overlaps its own work with it"


def test_wait_until_free_only_sleeps_the_remainder(monkeypatch):
    """Whatever the caller already spent getting the cursor onto the target comes off the wait."""
    clock = {"t": 1000.0}
    slept = []
    monkeypatch.setattr(ch.time, "sleep",
                        lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s)))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])
    ctx = _Ctx()
    ctx.state.data["chin_free_at"] = 1000.4      # 0.4s left of the window

    ch._wait_until_free(ctx)

    assert slept == [pytest.approx(0.4)]
    assert "chin_free_at" not in ctx.state.data, "the window must not be reused next action"


def test_wait_until_free_does_not_sleep_when_the_window_has_passed(monkeypatch):
    """If the setup took longer than the window, act immediately -- never sleep a negative."""
    clock = {"t": 1000.0}
    slept = []
    monkeypatch.setattr(ch.time, "sleep",
                        lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s)))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])
    ctx = _Ctx()
    ctx.state.data["chin_free_at"] = 999.0       # already elapsed

    ch._wait_until_free(ctx)

    assert slept == []


def test_await_placement_settled_gives_up_when_the_trap_was_taken_not_re_laid(monkeypatch):
    """before_break takes a trap rather than re-laying it, so no item ever hits the floor --
    the caller must fall back to the animation signal instead of hanging."""
    monkeypatch.setattr(ch.controller, "get_ground_items", lambda **kw: [])
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    assert ch._await_placement_settled(_Ctx(), (2512, 9300, 0)) is False


def test_placement_uses_the_event_stream_when_available(monkeypatch):
    """The GROUND_ITEM_SPAWNED event is the instant the trap hits the floor -- the same moment
    HunterPlugin logs "Trap placed". Polling for it costs up to a poll interval on top.

    (I4, 2026-08-21) The event path must set chin_free_at via deadline_after_ticks, exactly like
    the polling path below -- NOT sleep the window out inline via wait_ticks. An inline sleep
    reintroduces the pessimisation _wait_until_free/chin_free_at was written to remove: it pushes
    the caller's own HTTP setup and mouse flight for the NEXT action entirely after the window
    instead of overlapping it, which is what took the cycle from 7.44s back up from the measured
    6.16s."""
    class _Events:
        def wait_for_event(self, types, match=None, timeout_s=1.0):
            return {"id": 10008, "x": 2512, "y": 9300, "plane": 0}
        def wait_ticks(self, n):
            raise AssertionError("must not sleep the window inline -- record a deadline instead")
        def deadline_after_ticks(self, n):
            self.deadline_ticks = n
            return 4242.0

    ctx = _Ctx()
    ctx.events = _Events()
    monkeypatch.setattr(ch.controller, "get_ground_items",
                        lambda **kw: (_ for _ in ()).throw(
                            AssertionError("must not poll when the event stream is available")))

    assert ch._await_placement_settled(ctx, (2512, 9300, 0)) is True
    assert ctx.events.deadline_ticks == ch.PLACEMENT_SETTLE_TICKS
    assert ctx.state.data["chin_free_at"] == 4242.0


def test_placement_falls_back_to_polling_without_an_event_stream(monkeypatch):
    """ctx.events is None under the old engine or if the listener died -- the routine must keep
    working exactly as it does today."""
    ctx = _Ctx()
    ctx.events = None
    calls = {"n": 0}

    def _items(**_kw):
        calls["n"] += 1
        return [_item(2512, 9300)]

    monkeypatch.setattr(ch.controller, "get_ground_items", _items)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    assert ch._await_placement_settled(ctx, (2512, 9300, 0)) is True
    assert calls["n"] >= 1


def test_placement_event_wait_returns_false_on_an_old_client_that_never_emits_the_event(monkeypatch):
    """An engine on an old client build that has never sent GROUND_ITEM_SPAWNED still hands a
    live ctx.events -- wait_for_event just times out and returns None. The event path must return
    False (not crash, not silently succeed) so retrieve_trap's caller falls back to
    _await_action_finished, same as if there were no event stream feeding it at all."""
    class _NeverEvents:
        def wait_for_event(self, types, match=None, timeout_s=1.0):
            return None      # timed out -- no such event was ever emitted
        def wait_ticks(self, n):
            raise AssertionError("must not wait ticks when nothing landed")

    ctx = _Ctx()
    ctx.events = _NeverEvents()

    assert ch._await_placement_settled(ctx, (2512, 9300, 0)) is False
    assert "chin_free_at" not in ctx.state.data


# --- _await_trap_laid ---------------------------------------------------------------

def _lay_confirm_stubs(monkeypatch, object_appears_after_polls=None):
    """Pack drops immediately (as it really does), while the trap OBJECT only shows up after
    `object_appears_after_polls` polls -- None meaning never. Returns the poll counter."""
    polls = {"n": 0}
    trap = _obj(9380, 2518, 9302)

    def _objects(radius=0, fresh=False):
        polls["n"] += 1
        if object_appears_after_polls is None or polls["n"] < object_appears_after_polls:
            return []
        return [trap]

    monkeypatch.setattr(ch.controller, "get_objects", _objects)
    monkeypatch.setattr(ch, "box_trap_count", lambda: 3)      # already dropped from 4
    monkeypatch.setattr(ch.controller, "get_object_at",
                        lambda x, y, p, i, radius=0: trap if polls["n"] >= (object_appears_after_polls or 10**9) else None)

    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])
    return polls


def test_await_trap_laid_is_false_when_the_trap_object_never_appears(monkeypatch):
    """The pack dropping is NOT proof the tile is occupied. CONFIRMED live 2026-08-21: the trap
    object takes ~1.9s (about 3 game ticks, the setup animation) to appear in /objects after the
    trap leaves the inventory, and returning True on the pack alone let the very next tick read
    the tile as empty and lay a second trap on it."""
    _lay_confirm_stubs(monkeypatch, object_appears_after_polls=None)

    assert ch._await_trap_laid((2518, 9302, 0), 4) is False


def test_await_trap_laid_is_true_once_the_trap_object_appears(monkeypatch):
    polls = _lay_confirm_stubs(monkeypatch, object_appears_after_polls=7)

    assert ch._await_trap_laid((2518, 9302, 0), 4) is True
    assert polls["n"] >= 7, "must have kept polling until the object was really there"


def test_await_trap_laid_accepts_a_trap_that_was_triggered_immediately(monkeypatch):
    """A chinchompa can walk into the trap during the setup animation, so the first object to
    appear on the tile may already be a full/transition one rather than a plain set trap.
    Anything classify_trap recognises means the tile is occupied."""
    caught = _obj(9383, 2518, 9302)      # "Shaking box" -- full
    monkeypatch.setattr(ch.controller, "get_objects", lambda radius=0, fresh=False: [caught])
    monkeypatch.setattr(ch, "box_trap_count", lambda: 3)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    assert ch._await_trap_laid((2518, 9302, 0), 4) is True


# --- retrieve_trap ------------------------------------------------------------------

def _retrieve_stubs(monkeypatch, trap, after=()):
    """Stub everything retrieve_trap touches except the click, and record the clicks. `after` is
    the object list /objects reports once the click has landed -- empty for "picked up"."""
    clicks = []
    monkeypatch.setattr(ch.controller, "wait_until_player_idle", lambda **kw: True)
    monkeypatch.setattr(ch.controller, "get_object_at", lambda x, y, p, i, radius=0: trap)
    monkeypatch.setattr(ch.controller, "click_entity",
                        lambda ser, target, **kw: clicks.append(target) or True)
    monkeypatch.setattr(ch.controller, "get_objects",
                        lambda radius=0, fresh=False: list(after) if clicks else [trap])
    monkeypatch.setattr(ch, "box_trap_count", lambda: 0)

    # Fake clock advanced by the stubbed sleep, so the give-up path doesn't spin for the real
    # RETRIEVE_CONFIRM_TIMEOUT_S seconds.
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    def _no_menu(*_a, **_k):
        raise AssertionError("retrieve_trap must not open the right-click menu any more")

    monkeypatch.setattr(ch.controller, "click_menu_action", _no_menu)
    return clicks


def test_retrieve_trap_re_aims_at_the_trap_during_the_click_flight(monkeypatch):
    """The trap does not move, but its SCREEN position does -- the camera keeps shifting while
    the player settles, so a position resolved before the mouse starts flying is stale by the
    time the cursor lands. That is what refresh_target is for (see controller's own factories);
    without it a retrieve click can land beside the trap. wait_until_player_idle is not enough on
    its own: it is position-only and returns True the moment local x/y stops changing, which is
    before the camera has caught up."""
    trap = _obj(9383, 2518, 9302)
    passed = {}
    monkeypatch.setattr(ch.controller, "wait_until_player_idle", lambda **kw: True)
    monkeypatch.setattr(ch.controller, "get_object_at", lambda x, y, p, i, radius=0: trap)
    monkeypatch.setattr(ch.controller, "get_objects", lambda **kw: [_obj(9380, 2518, 9302)])
    monkeypatch.setattr(ch.controller, "click_entity",
                        lambda ser, target, **kw: passed.update(kw) or True)
    monkeypatch.setattr(ch, "box_trap_count", lambda: 0)
    monkeypatch.setattr(ch, "_await_action_finished", lambda **kw: True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    ch.retrieve_trap(_Ctx(), (2518, 9302, 0), "full", trap)

    assert callable(passed.get("refresh_target")), \
        "the click must re-resolve the trap's screen position mid-flight"


def test_retrieve_trap_re_aims_off_the_tile_not_an_object_scan(monkeypatch):
    """The trap is always on a known plan tile, so /ground/walk's direct projection gives the
    same point as /object/at -- which rebuilds and scans the entire scene to find one trap.
    MEASURED 2026-08-21: ~5ms floor vs ~20-32ms, and that cost falls inside the click's flight."""
    trap = _obj(9383, 2518, 9302)
    passed = {}
    used = []
    monkeypatch.setattr(ch.controller, "wait_until_player_idle", lambda **kw: True)
    # The pre-hover is not what this test is about (and hover_entity now resolves its departure
    # through the refresh, which would otherwise spend one of the projections counted below).
    monkeypatch.setattr(ch.controller, "hover_entity", lambda *a, **kw: True)
    monkeypatch.setattr(ch.controller, "get_object_at", lambda x, y, p, i, radius=0: trap)
    monkeypatch.setattr(ch.controller, "get_objects", lambda **kw: [_obj(9380, 2518, 9302)])
    monkeypatch.setattr(ch.controller, "click_entity",
                        lambda ser, target, **kw: passed.update(kw) or True)
    monkeypatch.setattr(ch.controller, "get_ground_walk_screen_pos",
                        lambda x, y, p: used.append((x, y, p)) or (100, 200))
    monkeypatch.setattr(ch.controller, "get_object_screen_pos_at",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("re-aim must not scan the scene for the object")))
    monkeypatch.setattr(ch, "box_trap_count", lambda: 0)
    monkeypatch.setattr(ch, "_await_action_finished", lambda **kw: True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    ch.retrieve_trap(_Ctx(), (2518, 9302, 0), "full", trap)
    passed["refresh_target"]()

    assert used == [(2518, 9302, 0)], "must project the trap's own tile"


def test_retrieve_trap_left_clicks_the_trap(monkeypatch):
    """The menu entries are swapped client-side so a plain left-click does the right thing on a
    trap in ANY state -- so naming the option ourselves is both unnecessary and, as the first
    live run showed, wrong: a full trap is a "Shaking box", not a "Box trap", and the target_hint
    missed on every single retrieval."""
    trap = _obj(9383, 2518, 9302)
    clicks = _retrieve_stubs(monkeypatch, trap, after=[_obj(9380, 2518, 9302)])

    assert ch.retrieve_trap(_Ctx(), (2518, 9302, 0), "full", trap) is True
    assert clicks == [trap]


def test_retrieve_trap_succeeds_when_the_trap_is_re_laid_in_place(monkeypatch):
    """Checking a full trap re-lays it automatically -- it never passes through the inventory, so
    waiting for the pack to grow (what this used to do) would time out and be recorded as a
    failed action even though the catch succeeded. The honest signal is the tile no longer
    holding the state we clicked on."""
    trap = _obj(9383, 2518, 9302)                       # full
    _retrieve_stubs(monkeypatch, trap, after=[_obj(9380, 2518, 9302)])   # -> set, still there

    assert ch.retrieve_trap(_Ctx(), (2518, 9302, 0), "full", trap) is True


def test_retrieve_trap_succeeds_when_the_trap_comes_back_to_the_pack(monkeypatch):
    """The other outcome: the trap is taken rather than re-laid, leaving the tile bare."""
    trap = _obj(9385, 2518, 9302)                       # collapsed
    _retrieve_stubs(monkeypatch, trap, after=[])

    assert ch.retrieve_trap(_Ctx(), (2518, 9302, 0), "failed", trap) is True


def test_retrieve_trap_clears_the_event_buffer_immediately_before_the_click(monkeypatch):
    """wait_for_event does not consume the event it matches, and the buffer holds up to
    EVENT_BUFFER_MAX_TICKS of history -- so without a clear() right before the click,
    _await_placement_settled (which runs AFTER the click) could match a GROUND_ITEM_SPAWNED left
    over from a PREVIOUS placement on the same tile and conclude this click landed when it did
    nothing at all. clear() has to happen right before click_entity, not merely somewhere in the
    call -- a clear() that ran earlier (e.g. before the idle wait or the fresh object lookup)
    would still leave a window for a same-tile spawn to slip back into the buffer before the
    click actually fires."""
    trap = _obj(9385, 2518, 9302)     # collapsed
    order = []

    class _Events:
        def clear(self):
            order.append("clear")
        def wait_for_event(self, types, match=None, timeout_s=1.0):
            # No such event was ever emitted in this test -- the point here is only the ordering
            # of clear() against the click, not the placement-detection path itself.
            return None
        def wait_ticks(self, n):
            raise AssertionError("must not be reached when wait_for_event returned None")

    monkeypatch.setattr(ch.controller, "wait_until_player_idle", lambda **kw: True)
    monkeypatch.setattr(ch.controller, "get_object_at", lambda x, y, p, i, radius=0: trap)
    monkeypatch.setattr(ch.controller, "get_player", lambda: {"animation": -1})
    monkeypatch.setattr(ch.controller, "click_entity",
                        lambda ser, target, **kw: order.append("click") or True)
    monkeypatch.setattr(ch.controller, "get_objects",
                        lambda radius=0, fresh=False: [] if order.count("click") else [trap])
    monkeypatch.setattr(ch, "box_trap_count", lambda: 0)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    ctx = _Ctx()
    ctx.events = _Events()

    ch.retrieve_trap(ctx, (2518, 9302, 0), "failed", trap)

    assert order == ["clear", "click"], \
        "clear() must happen immediately before the click, not after it or not at all"


def test_retrieve_trap_tolerates_no_event_stream_when_clearing(monkeypatch):
    """ctx.events is None under the old engine or a dead listener -- the clear-before-click guard
    must not crash in that case."""
    trap = _obj(9385, 2518, 9302)
    monkeypatch.setattr(ch.controller, "wait_until_player_idle", lambda **kw: True)
    monkeypatch.setattr(ch.controller, "get_object_at", lambda x, y, p, i, radius=0: trap)
    monkeypatch.setattr(ch.controller, "get_player", lambda: {"animation": -1})
    monkeypatch.setattr(ch.controller, "click_entity", lambda ser, target, **kw: True)
    monkeypatch.setattr(ch.controller, "get_objects", lambda radius=0, fresh=False: [])
    # No item ever shows up either -- forces the polling fallback in _await_placement_settled
    # (ctx.events is None) to time out quickly rather than hitting a real bridge over HTTP.
    monkeypatch.setattr(ch.controller, "get_ground_items", lambda **kw: [])
    monkeypatch.setattr(ch, "box_trap_count", lambda: 0)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    ctx = _Ctx()
    ctx.events = None

    assert ch.retrieve_trap(ctx, (2518, 9302, 0), "failed", trap) is True


def test_retrieve_trap_fails_when_the_trap_is_untouched(monkeypatch):
    """This is what replaces the right-click menu's protection. A left-click on a pixel a
    chinchompa is standing on can resolve to Attack instead -- and then the trap is still sitting
    there in the state we found it, which is the only honest way to know. Report False and let
    the next tick retry."""
    trap = _obj(9383, 2518, 9302)
    _retrieve_stubs(monkeypatch, trap, after=[_obj(9383, 2518, 9302)])   # unchanged

    assert ch.retrieve_trap(_Ctx(), (2518, 9302, 0), "full", trap) is False


# --- max_traps_for_level ------------------------------------------------------------

@pytest.mark.parametrize("level,expected", [
    (1, 1), (19, 1), (20, 2), (39, 2), (40, 3), (59, 3), (60, 4), (79, 4), (80, 5), (99, 5),
])
def test_max_traps_for_level_matches_the_wiki_table(level, expected):
    assert ch.max_traps_for_level(level) == expected


def test_four_traps_are_allowed_at_the_level_needed_to_hunt_these_at_all():
    """The user asked for 4 traps; 63 Hunter is the requirement to catch carnivorous chinchompas.
    Pins that those two numbers are compatible, so resolve_trap_count never quietly clamps to 3
    on an account that just hit the requirement."""
    assert ch.max_traps_for_level(ch.REQUIRED_HUNTER_LEVEL) >= 4


@pytest.mark.parametrize("level", [None, 0, -1])
def test_max_traps_for_level_refuses_nonsense_levels(level):
    assert ch.max_traps_for_level(level) == 0


# --- build_plan ---------------------------------------------------------------------

def test_default_layout_is_the_four_orthogonal_neighbours_of_the_anchor():
    plan = ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 4)
    assert plan == [(2557, 2937, 0), (2558, 2936, 0), (2557, 2935, 0), (2556, 2936, 0)]


def test_the_plan_never_includes_the_standing_tile():
    """A trap on the tile the player stands on catches nothing (Box trap.txt), so a (0, 0) offset
    must be dropped rather than burning one of the four slots."""
    plan = ch.build_plan(ANCHOR, [[0, 0], [0, 1], [1, 0], [0, -1], [-1, 0]], 4)
    assert ANCHOR not in plan
    assert len(plan) == 4


def test_a_blocked_tile_is_replaced_by_the_next_spare_offset():
    plan = ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 4,
                          blocked=[(2558, 2936, 0)])
    assert (2558, 2936, 0) not in plan
    assert len(plan) == 4
    assert (2558, 2937, 0) in plan, "the first diagonal spare should have taken the free slot"


def test_the_plan_is_capped_at_the_trap_count():
    assert len(ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 2)) == 2


def test_offsets_running_out_shortens_the_plan_rather_than_repeating_a_tile():
    plan = ch.build_plan(ANCHOR, [[0, 1], [1, 0]], 4)
    assert plan == [(2557, 2937, 0), (2558, 2936, 0)]
    assert len(set(plan)) == len(plan)


def test_existing_traps_near_the_anchor_are_adopted_before_the_offsets():
    """The restart fix: state.data is wiped on relaunch but four laid traps are not, so a plan
    built from scratch must reclaim them instead of orphaning them."""
    laid = [(2556, 2935, 0), (2558, 2937, 0)]
    plan = ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 4,
                          nearby_trap_tiles=laid, adopt_radius=2)
    assert set(laid).issubset(set(plan))
    assert len(plan) == 4


def test_a_trap_outside_the_adopt_radius_is_not_claimed_as_ours():
    """Another player hunting a few tiles over must never be adopted into our plan -- we'd spend
    the session trying to Check a trap the game will keep telling us isn't ours."""
    plan = ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 4,
                          nearby_trap_tiles=[(2557 + 6, 2936, 0)], adopt_radius=2)
    assert (2563, 2936, 0) not in plan


def test_a_blocked_tile_is_never_adopted_either():
    plan = ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 4,
                          blocked=[(2556, 2935, 0)], nearby_trap_tiles=[(2556, 2935, 0)],
                          adopt_radius=2)
    assert (2556, 2935, 0) not in plan


def test_adoption_ignores_a_trap_on_another_plane():
    plan = ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 4,
                          nearby_trap_tiles=[(2556, 2935, 1)], adopt_radius=2)
    assert all(tile[2] == 0 for tile in plan)


def test_build_plan_is_deterministic_for_the_same_inputs():
    """A plan that reshuffled between ticks would have the routine abandoning half-laid layouts."""
    laid = [(2558, 2937, 0), (2556, 2935, 0), (2557, 2937, 0)]
    first = ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 4, nearby_trap_tiles=laid)
    second = ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 4,
                            nearby_trap_tiles=list(reversed(laid)))
    assert first == second


# --- traps_by_tile ------------------------------------------------------------------

def test_traps_on_tiles_we_never_planned_are_invisible_to_the_routine():
    """This IS the ownership check -- there is no other one. A trap two tiles off-plan belongs to
    somebody else and must not appear in the dict at all."""
    plan = [(2557, 2937, 0), (2558, 2936, 0)]
    objects = [
        _obj(9383, 2557, 2937),          # ours, full
        _obj(9380, 2540, 2900),          # a stranger's, well off-plan
        _obj(9385, 2559, 2939),          # a stranger's, just off-plan
    ]
    found = ch.traps_by_tile(objects, plan)
    assert set(found) == {(2557, 2937, 0)}
    assert found[(2557, 2937, 0)][0] == "full"


def test_non_trap_objects_sharing_a_plan_tile_are_ignored():
    plan = [(2557, 2937, 0)]
    assert ch.traps_by_tile([_obj(1234, 2557, 2937)], plan) == {}


def test_the_most_actionable_state_wins_when_a_tile_reports_two_trap_objects():
    plan = [(2557, 2937, 0)]
    found = ch.traps_by_tile([_obj(9380, 2557, 2937), _obj(9383, 2557, 2937)], plan)
    assert found[(2557, 2937, 0)][0] == "full"
    found_reversed = ch.traps_by_tile([_obj(9383, 2557, 2937), _obj(9380, 2557, 2937)], plan)
    assert found_reversed[(2557, 2937, 0)][0] == "full"


# --- dropped traps ------------------------------------------------------------------

def _item(x, y, plane=0, name="Box trap", item_id=10008):
    return {"id": item_id, "name": name, "x": x, "y": y, "plane": plane, "qty": 1}


def test_a_trap_we_just_placed_is_not_mistaken_for_a_dropped_one():
    """A trap being laid passes through the floor as an ITEM under the player before it becomes
    an object (user, 2026-08-21). Treating that transient as a timed-out trap makes the routine
    pick up the trap it just placed, then lay it again -- burning two actions and looking exactly
    like a leak. Anything on a tile we acted on moments ago is ignored."""
    plan = [(2512, 9300, 0)]
    items = [_item(2512, 9300)]
    recent = {"2512,9300,0": 1000.0}
    assert ch.dropped_traps_by_tile(items, plan, recent=recent, now=1000.5) == {}


def test_a_trap_dropped_long_enough_ago_is_still_recovered():
    """The guard must expire, or a tile that genuinely times out later is never recovered."""
    plan = [(2512, 9300, 0)]
    items = [_item(2512, 9300)]
    recent = {"2512,9300,0": 1000.0}
    found = ch.dropped_traps_by_tile(items, plan, recent=recent,
                                     now=1000.0 + ch.PLACEMENT_ITEM_GRACE_S + 0.1)
    assert list(found) == [(2512, 9300, 0)]


def test_dropped_traps_by_tile_finds_a_timed_out_trap_on_a_plan_tile():
    """CONFIRMED live 2026-08-21: a laid trap that is never touched eventually times out and
    drops as a Box trap GROUND ITEM on its own tile (observed at 2512,9300 with laid_objects
    falling 4 -> 3). Nothing picked it up, so it despawned and the routine spent a fresh trap
    from the pack -- a steady leak of ~1-2 traps an hour."""
    plan = [(2512, 9300, 0), (2513, 9301, 0)]
    found = ch.dropped_traps_by_tile([_item(2512, 9300)], plan)
    assert list(found) == [(2512, 9300, 0)]


def test_dropped_traps_ignores_items_that_are_not_ours_or_not_traps():
    plan = [(2512, 9300, 0)]
    assert ch.dropped_traps_by_tile([_item(2599, 9999)], plan) == {}          # off-plan
    assert ch.dropped_traps_by_tile([_item(2512, 9300, name="Bones", item_id=526)], plan) == {}


def test_a_tile_with_a_dropped_trap_is_recovered_rather_than_re_laid():
    """Recovering is strictly better than laying: it costs the same one action, saves a trap
    from the pack, and the item despawns if left."""
    plan = [(2512, 9300, 0)]
    queue = ch.work_queue(plan, {}, dropped=ch.dropped_traps_by_tile([_item(2512, 9300)], plan))
    assert [item[0] for item in queue] == ["dropped"]


def test_a_catch_still_outranks_recovering_a_dropped_trap():
    """The item lasts a couple of minutes and actions take ~10s, so it is in no danger from
    being one place down the queue; a catch is immediate value."""
    plan = [(2513, 9301, 0), (2512, 9300, 0)]
    traps = ch.traps_by_tile([_obj(9383, 2513, 9301)], plan)
    dropped = ch.dropped_traps_by_tile([_item(2512, 9300)], plan)
    queue = ch.work_queue(plan, traps, dropped=dropped)
    assert [item[0] for item in queue] == ["full", "dropped"]


def test_recovering_a_dropped_trap_goes_through_the_menu_not_a_bare_click(monkeypatch):
    """A plain left-click failed live on the first real attempt (2513,9301, 13:25:32 -- the trap
    never reached the inventory and the item despawned). These items lie on tiles that fifteen
    chinchompas wander across, so a click on a shared pixel can resolve to Attack instead. That
    is exactly what click_menu_action exists for, and unlike the trap OBJECTS (whose menu entries
    are swapped client-side so a left-click is correct) a ground item still has an ordinary Take
    option to select."""
    item = _item(2513, 9301)
    used = {}
    monkeypatch.setattr(ch.controller, "wait_until_player_idle", lambda **kw: True)
    monkeypatch.setattr(ch.controller, "get_ground_item_at", lambda x, y, p, i: item)
    monkeypatch.setattr(ch.controller, "click_menu_action",
                        lambda ser, target, options, target_hint=None, **kw:
                            used.update(target=target, options=options, hint=target_hint) or True)

    def _no_bare_click(*_a, **_k):
        raise AssertionError("a ground item must be taken via the menu, not a bare click")

    monkeypatch.setattr(ch.controller, "click_entity", _no_bare_click)
    counts = iter([0] + [1] * 50)
    monkeypatch.setattr(ch, "box_trap_count", lambda: next(counts))
    monkeypatch.setattr(ch, "_await_action_finished", lambda **kw: True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])

    assert ch.recover_dropped_trap(_Ctx(), (2513, 9301, 0), item) is True
    assert "Take" in used["options"]
    assert used["target"] is item


def test_recover_dropped_trap_passes_refresh_target(monkeypatch):
    """recover_dropped_trap's Take click should re-resolve the trap item's live screen position,
    same as every other ground-item menu click in this codebase.

    box_trap_count() must report an increase on its first post-click poll (not stay flat like a
    permanently-empty pack), and time.sleep/time.time run on a fake clock -- otherwise the
    confirmation loop below the click has nothing to see, runs out its full REAL-TIME
    RETRIEVE_CONFIRM_TIMEOUT_S, and burns ~3s of wall-clock every time the suite runs, same fix as
    the sibling test above."""
    tile = (2513, 9301, 0)
    item = {"x": tile[0], "y": tile[1], "plane": tile[2], "id": ch.BOX_TRAP_ITEM_ID}
    monkeypatch.setattr(controller, "wait_until_player_idle", lambda *a, **k: True)
    monkeypatch.setattr(controller, "get_ground_item_at", lambda x, y, plane, item_id: item)
    counts = iter([0] + [1] * 50)
    monkeypatch.setattr(ch, "box_trap_count", lambda: next(counts))
    monkeypatch.setattr(ch, "_note_acted_on", lambda ctx, tile: None)
    monkeypatch.setattr(ch, "_await_action_finished", lambda **kw: True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ch.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(ch.time, "time", lambda: clock["t"])
    seen = {}
    def _fake_click_menu_action(ser, entity, option, **kw):
        seen.update(kw)
        return True
    monkeypatch.setattr(controller, "click_menu_action", _fake_click_menu_action)

    ch.recover_dropped_trap(_Ctx(), tile, item)
    assert seen.get("refresh_target") is not None


def test_a_tile_the_player_can_never_reach_gets_blocked(monkeypatch):
    """CONFIRMED live 2026-08-21: tile 2511,9301 was unwalkable (a wall), so lay_trap logged
    "didn't reach" and gave up EVERY tick it came round -- nine times in under three minutes, at
    ~10s an action. block_tile never fired because it keys off the game's refusal chat message,
    and that never arrives when the player cannot even get to the tile to try. The plan has spare
    offsets precisely for this; nothing was using them."""
    monkeypatch.setattr(ch.travel, "walk_to_exact_tile", lambda ctx, x, y, p: True)
    monkeypatch.setattr(ch.controller, "get_player",
                        lambda: {"x": 2512, "y": 9301, "plane": 0})     # never the target tile
    monkeypatch.setattr(ch.controller, "get_objects", lambda **kw: [])
    ctx = _Ctx()
    tile = (2511, 9301, 0)

    for _ in range(ch.UNREACHABLE_BLOCK_THRESHOLD):
        assert ch.lay_trap(ctx, tile) is False

    assert [2511, 9301, 0] in ctx.state.data.get("chin_blocked", []), \
        "an unreachable tile must leave the plan instead of being retried forever"


def test_reaching_a_tile_clears_its_unreachable_streak(monkeypatch):
    """A chinchompa standing on the tile blocks the walk transiently -- that must not accumulate
    toward blocking a perfectly good tile."""
    player = {"x": 2512, "y": 9301, "plane": 0}
    monkeypatch.setattr(ch.travel, "walk_to_exact_tile", lambda ctx, x, y, p: True)
    monkeypatch.setattr(ch.controller, "get_player", lambda: dict(player))
    monkeypatch.setattr(ch.controller, "get_objects", lambda **kw: [])
    monkeypatch.setattr(ch.controller, "open_inventory_tab", lambda ser: None)
    monkeypatch.setattr(ch, "find_box_trap", lambda: None)              # stop right after arrival
    ctx = _Ctx()
    tile = (2511, 9301, 0)

    for _ in range(ch.UNREACHABLE_BLOCK_THRESHOLD - 1):
        ch.lay_trap(ctx, tile)
    player.update(x=2511, y=9301)                                       # this time we arrive
    ch.lay_trap(ctx, tile)
    player.update(x=2512, y=9301)                                       # and fail again
    ch.lay_trap(ctx, tile)

    assert [2511, 9301, 0] not in ctx.state.data.get("chin_blocked", [])


# --- work_queue ---------------------------------------------------------------------

def test_an_unlaid_slot_outranks_a_collapsed_trap_but_not_a_catch():
    """Both extremes were measured live 2026-08-21 and both were worse. Ranked LAST, the final
    slot starved forever (12 catches on 3 traps of a 4-trap plan) because there was always a
    catch or a collapsed trap ahead of it. Ranked FIRST, laying displaced collecting: the trap
    failure rate doubled and catch-to-catch went from ~9.7s to a median of 27.6s. In the middle,
    catches keep top priority and an unlaid slot still gets filled at the next collapsed trap."""
    plan = [(2557, 2937, 0), (2558, 2936, 0), (2557, 2935, 0)]
    traps = ch.traps_by_tile([
        _obj(9385, 2557, 2937),   # failed
        _obj(9383, 2558, 2936),   # full
    ], plan)                       # (2557, 2935) is empty
    queue = ch.work_queue(plan, traps)
    assert [item[0] for item in queue] == ["full", "empty", "failed"]


def test_a_healthy_set_trap_produces_no_work():
    plan = [(2557, 2937, 0)]
    traps = ch.traps_by_tile([_obj(9380, 2557, 2937)], plan)
    assert ch.work_queue(plan, traps) == []


def test_a_trap_mid_animation_is_left_alone():
    """Clicking through the closing animation is how you interrupt a catch that is about to
    land."""
    plan = [(2557, 2937, 0)]
    traps = ch.traps_by_tile([_obj(9390, 2557, 2937)], plan)
    assert ch.work_queue(plan, traps) == []


def test_every_plan_tile_with_no_trap_on_it_becomes_work():
    plan = [(2557, 2937, 0), (2558, 2936, 0)]
    queue = ch.work_queue(plan, {})
    assert [item[0] for item in queue] == ["empty", "empty"]


def test_empty_tiles_are_offered_in_plan_order_not_coordinate_order():
    """Holding fewer traps than slots must fill the PREFERRED offsets (the orthogonal neighbours
    the plan lists first), not whichever tile happens to have the lowest coordinates -- the
    default plan's last tile sorts first by x, so a coordinate sort would silently invert it."""
    plan = ch.build_plan(ANCHOR, ch.DEFAULT_CONFIG["trapOffsets"], 4)
    queue = ch.work_queue(plan, {})
    assert [item[1] for item in queue] == plan


# --- block_tile ---------------------------------------------------------------------

def test_blocking_a_tile_forces_the_plan_to_be_rebuilt():
    ctx = _Ctx()
    ctx.state.data["chin_plan_key"] = ["anything"]
    ch.block_tile(ctx, (2558, 2936, 0), "test")
    assert ctx.state.data["chin_blocked"] == [[2558, 2936, 0]]
    assert "chin_plan_key" not in ctx.state.data, \
        "a stale plan key would keep the routine laying traps on the tile it just gave up on"


def test_blocking_the_same_tile_twice_is_a_no_op():
    ctx = _Ctx()
    ch.block_tile(ctx, (2558, 2936, 0), "test")
    ch.block_tile(ctx, (2558, 2936, 0), "test")
    assert ctx.state.data["chin_blocked"] == [[2558, 2936, 0]]


# --- ensure_plan --------------------------------------------------------------------

def test_ensure_plan_reuses_the_cached_plan_until_something_changes():
    ctx = _Ctx()
    first = ch.ensure_plan(ctx, ch.DEFAULT_CONFIG, ANCHOR, 4, [])
    ctx.state.data["chin_plan"] = [[9, 9, 0]]   # a sentinel the rebuild path would overwrite
    second = ch.ensure_plan(ctx, ch.DEFAULT_CONFIG, ANCHOR, 4, [])
    assert first != second and second == [(9, 9, 0)], \
        "an unchanged key must return the stored plan rather than recomputing it"


def test_ensure_plan_rebuilds_after_a_tile_is_blocked():
    ctx = _Ctx()
    plan = ch.ensure_plan(ctx, ch.DEFAULT_CONFIG, ANCHOR, 4, [])
    blocked = plan[1]
    ch.block_tile(ctx, blocked, "test")
    rebuilt = ch.ensure_plan(ctx, ch.DEFAULT_CONFIG, ANCHOR, 4, [])
    assert blocked not in rebuilt
    assert len(rebuilt) == 4


def test_ensure_plan_adopts_traps_from_the_objects_it_is_handed():
    ctx = _Ctx()
    objects = [_obj(9380, 2556, 2935), _obj(9999, 2558, 2937)]  # second isn't a trap
    plan = ch.ensure_plan(ctx, ch.DEFAULT_CONFIG, ANCHOR, 4, objects)
    assert (2556, 2935, 0) in plan
    assert (2558, 2937, 0) not in plan


def test_adoption_can_be_turned_off():
    ctx = _Ctx()
    config = dict(ch.DEFAULT_CONFIG, adoptNearbyTraps=False)
    plan = ch.ensure_plan(ctx, config, ANCHOR, 4, [_obj(9380, 2556, 2935)])
    assert (2556, 2935, 0) not in plan


# --- ensure_anchor ------------------------------------------------------------------

def test_the_anchor_defaults_to_wherever_the_player_is_standing(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2557, "y": 2936, "plane": 0})
    ctx = _Ctx()
    assert ch.ensure_anchor(ctx, ch.DEFAULT_CONFIG) == ANCHOR


def test_the_anchor_is_resolved_once_and_then_remembered(monkeypatch):
    """Re-reading the player's position every tick would drag the whole layout around behind the
    player the moment a walk overshoots."""
    positions = [{"x": 2557, "y": 2936, "plane": 0}, {"x": 9999, "y": 9999, "plane": 0}]
    monkeypatch.setattr(controller, "get_player", lambda: positions.pop(0))
    ctx = _Ctx()
    assert ch.ensure_anchor(ctx, ch.DEFAULT_CONFIG) == ANCHOR
    assert ch.ensure_anchor(ctx, ch.DEFAULT_CONFIG) == ANCHOR


def test_a_configured_anchor_wins_over_the_player_position(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 1, "y": 2, "plane": 0})
    ctx = _Ctx()
    assert ch.ensure_anchor(ctx, dict(ch.DEFAULT_CONFIG, anchor=[2520, 9300, 0])) == (2520, 9300, 0)


def test_no_anchor_is_chosen_before_the_bridge_reports_a_position(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {})
    ctx = _Ctx()
    assert ch.ensure_anchor(ctx, ch.DEFAULT_CONFIG) is None
    assert "chin_anchor" not in ctx.state.data


# --- resolve_trap_count -------------------------------------------------------------

def test_an_under_levelled_account_safety_stops_instead_of_hunting(monkeypatch):
    monkeypatch.setattr(controller, "get_skills", lambda: {"Hunter": {"level": 55}})
    ctx = _Ctx()
    assert ch.resolve_trap_count(ctx, ch.DEFAULT_CONFIG) is None
    assert ctx.state.safety_stopped


def test_the_trap_count_is_clamped_to_what_the_level_allows(monkeypatch):
    """63 Hunter can catch these but a 5-trap config is still only 4 traps until level 80."""
    monkeypatch.setattr(controller, "get_skills", lambda: {"Hunter": {"level": 63}})
    ctx = _Ctx()
    assert ch.resolve_trap_count(ctx, dict(ch.DEFAULT_CONFIG, trapCount=5)) == 4


def test_the_requested_four_traps_survive_at_level_63(monkeypatch):
    monkeypatch.setattr(controller, "get_skills", lambda: {"Hunter": {"level": 63}})
    ctx = _Ctx()
    assert ch.resolve_trap_count(ctx, ch.DEFAULT_CONFIG) == 4


def test_a_silent_skills_endpoint_is_retried_rather_than_stopped(monkeypatch):
    monkeypatch.setattr(controller, "get_skills", lambda: {})
    ctx = _Ctx()
    assert ch.resolve_trap_count(ctx, ch.DEFAULT_CONFIG) is None
    assert not ctx.state.safety_stopped
    assert "chin_trap_count" not in ctx.state.data


# --- on_event -----------------------------------------------------------------------

def _chat(ctx, message):
    ch.on_event(ctx, "CHAT_MESSAGE", {"message": message})


def test_catches_are_counted():
    ctx = _Ctx()
    _chat(ctx, "You've caught a carnivorous chinchompa.")
    _chat(ctx, "You've caught a carnivorous chinchompa.")
    assert ctx.state.data["chin_catches"] == 2


def test_another_players_trap_blocks_that_tile():
    ctx = _Ctx()
    ctx.state.data["chin_last_tile"] = [2558, 2936, 0]
    _chat(ctx, "This isn't your trap.")
    assert ctx.state.data["chin_blocked"] == [[2558, 2936, 0]]


@pytest.mark.parametrize("message", [
    "You can't set a trap here.",
    "You can't lay a trap here.",
    "There isn't anything to catch indoors!",
])
def test_a_refused_tile_is_blocked(message):
    ctx = _Ctx()
    ctx.state.data["chin_last_tile"] = [2558, 2936, 0]
    _chat(ctx, message)
    assert ctx.state.data["chin_blocked"] == [[2558, 2936, 0]]


def test_a_level_rejection_from_the_game_itself_stops_the_routine():
    ctx = _Ctx()
    _chat(ctx, "You need a Hunter level of 63 to set that trap.")
    assert ctx.state.safety_stopped


def test_the_games_own_trap_limit_lowers_the_count_and_rebuilds_the_plan():
    ctx = _Ctx()
    ctx.state.data["chin_trap_count"] = 4
    ctx.state.data["chin_plan_key"] = ["stale"]
    _chat(ctx, "You can only set 3 traps at a time.")
    assert ctx.state.data["chin_trap_count"] == 3
    assert "chin_plan_key" not in ctx.state.data


def test_the_trap_limit_message_never_drops_below_one_trap():
    ctx = _Ctx()
    ctx.state.data["chin_trap_count"] = 1
    _chat(ctx, "You can only set 1 trap at a time.")
    assert ctx.state.data["chin_trap_count"] == 1


def test_unrelated_events_and_chatter_are_ignored():
    ctx = _Ctx()
    ch.on_event(ctx, "NPC_DIED", {"id": 2911})
    _chat(ctx, "Oh dear, you are dead!")
    assert ctx.state.data == {}


# --- box_trap_count -----------------------------------------------------------------

def test_box_traps_are_counted_by_quantity_not_by_slot(monkeypatch):
    """They became stackable in 2025, so four traps is normally ONE slot with qty 4 -- counting
    slots would report 1 and have the routine think it was out of traps."""
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Box trap", "qty": 4}, {"name": "Red chinchompa", "qty": 300}])
    assert ch.box_trap_count() == 4


def test_an_unstacked_pile_of_traps_still_counts_correctly(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [{"name": "Box trap", "qty": 1}, {"name": "Box trap", "qty": 1}])
    assert ch.box_trap_count() == 2


def test_a_box_trap_pack_is_not_mistaken_for_a_box_trap(monkeypatch):
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Box trap pack", "qty": 1}])
    assert ch.box_trap_count() == 0
    assert ch.find_box_trap() is None


# --- tick ---------------------------------------------------------------------------

def _stub_tick_world(monkeypatch, objects, inventory, calls, ground_items=()):
    monkeypatch.setattr(controller, "get_skills", lambda: {"Hunter": {"level": 70}})
    monkeypatch.setattr(controller, "get_player", lambda: {"x": 2557, "y": 2936, "plane": 0})
    monkeypatch.setattr(controller, "get_objects", lambda **kw: objects)
    # Not optional: tick() reads ground items every pass, and without this the tests issue real
    # HTTP requests to whatever bridge happens to be running.
    monkeypatch.setattr(controller, "get_ground_items", lambda **kw: list(ground_items))
    monkeypatch.setattr(controller, "get_inventory", lambda: inventory)
    monkeypatch.setattr(ch, "recover_dropped_trap",
                        lambda ctx, tile, item: calls.append(("recover", tile)) or True)
    monkeypatch.setattr(ch, "lay_trap", lambda ctx, tile: calls.append(("lay", tile)) or True)
    monkeypatch.setattr(ch, "retrieve_trap",
                        lambda ctx, tile, state, obj: calls.append(("retrieve", state, tile)) or True)
    monkeypatch.setattr(ch, "return_to_anchor", lambda ctx, anchor: calls.append(("return", anchor)))
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


def test_tick_recovers_a_timed_out_trap_instead_of_spending_one_from_the_pack(monkeypatch):
    """The leak fix end to end: a bare plan tile with our own dropped trap on it must be picked
    back up, not paved over with a fresh trap from the inventory."""
    calls = []
    _stub_tick_world(monkeypatch, [], [{"name": "Box trap", "qty": 3}], calls,
                     ground_items=[_item(2557, 2937)])
    ch.tick(_Ctx())
    assert calls == [("recover", (2557, 2937, 0))]


def test_tick_collects_a_catch_before_filling_an_unlaid_slot(monkeypatch):
    """Collecting stays top priority -- ranking laying above it doubled the trap failure rate and
    nearly tripled catch-to-catch time when measured live. See work_queue's docstring."""
    calls = []
    _stub_tick_world(monkeypatch, [_obj(9383, 2558, 2936)], [{"name": "Box trap", "qty": 3}], calls)
    ch.tick(_Ctx())
    assert calls == [("retrieve", "full", (2558, 2936, 0))]


def test_tick_fills_an_unlaid_slot_before_resetting_a_collapsed_trap(monkeypatch):
    """The half that fixes the starvation: with no catch waiting, an empty slot is worth more
    than a collapsed trap, so the unlaid trap stops sitting in the pack forever."""
    calls = []
    _stub_tick_world(monkeypatch, [_obj(9385, 2558, 2936)], [{"name": "Box trap", "qty": 3}], calls)
    ch.tick(_Ctx())
    assert calls == [("lay", (2557, 2937, 0))]


def test_tick_lays_on_an_empty_tile_when_there_is_nothing_to_collect(monkeypatch):
    calls = []
    _stub_tick_world(monkeypatch, [], [{"name": "Box trap", "qty": 4}], calls)
    ch.tick(_Ctx())
    assert calls == [("lay", (2557, 2937, 0))]


def test_tick_does_exactly_one_thing_per_call(monkeypatch):
    """Every branch returns after acting -- a tick that both retrieved and laid would be
    resolving screen positions from an /objects snapshot taken before the first action moved the
    player."""
    calls = []
    _stub_tick_world(monkeypatch, [_obj(9383, 2558, 2936), _obj(9385, 2557, 2937)],
                      [{"name": "Box trap", "qty": 2}], calls)
    ch.tick(_Ctx())
    assert len(calls) == 1


def test_tick_steps_off_the_trap_it_just_laid_once_everything_is_set(monkeypatch):
    """Load-bearing: a trap with the player standing on it catches nothing."""
    calls = []
    plan_objects = [_obj(9380, 2557, 2937), _obj(9380, 2558, 2936),
                    _obj(9380, 2557, 2935), _obj(9380, 2556, 2936)]
    _stub_tick_world(monkeypatch, plan_objects, [{"name": "Box trap", "qty": 0}], calls)
    ch.tick(_Ctx())
    assert calls == [("return", ANCHOR)]


def test_tick_waits_rather_than_acting_while_every_trap_is_mid_catch(monkeypatch):
    calls = []
    mid = [_obj(9390, 2557, 2937), _obj(9390, 2558, 2936),
           _obj(9390, 2557, 2935), _obj(9390, 2556, 2936)]
    _stub_tick_world(monkeypatch, mid, [], calls)
    ch.tick(_Ctx())
    assert calls == [("return", ANCHOR)]


def test_running_out_of_traps_entirely_is_a_safety_stop(monkeypatch):
    calls = []
    _stub_tick_world(monkeypatch, [], [], calls)
    ctx = _Ctx()
    ch.tick(ctx)
    assert ctx.state.safety_stopped and "box traps" in ctx.state.safety_reason
    assert calls == []


def test_an_empty_pack_is_not_a_safety_stop_while_traps_are_still_out_working(monkeypatch):
    """All four traps laid and none in the pack is the normal steady state, not an error."""
    calls = []
    _stub_tick_world(monkeypatch, [_obj(9380, 2557, 2937)], [], calls)
    ctx = _Ctx()
    ch.tick(ctx)
    assert not ctx.state.safety_stopped


def test_tick_does_nothing_at_all_while_under_levelled(monkeypatch):
    calls = []
    _stub_tick_world(monkeypatch, [], [{"name": "Box trap", "qty": 4}], calls)
    monkeypatch.setattr(controller, "get_skills", lambda: {"Hunter": {"level": 40}})
    ctx = _Ctx()
    ch.tick(ctx)
    assert ctx.state.safety_stopped
    assert calls == []


def test_a_run_of_failed_trap_actions_eventually_stops_the_routine(monkeypatch):
    """This routine is meant to run unattended for hours, and every failure path in it retries
    next tick. A permanent cause (no inventory room for the trap coming back, say) would
    otherwise be an invisible infinite loop rather than something a human ever finds out about."""
    calls = []
    _stub_tick_world(monkeypatch, [], [{"name": "Box trap", "qty": 4}], calls)
    monkeypatch.setattr(ch, "lay_trap", lambda ctx, tile: False)

    ctx = _Ctx()
    for _ in range(ch.MAX_CONSECUTIVE_FAILURES):
        assert not ctx.state.safety_stopped
        ch.tick(ctx)
    assert ctx.state.safety_stopped
    assert "failed" in ctx.state.safety_reason


def test_one_success_clears_the_failure_streak(monkeypatch):
    """Transient failures (a chinchompa standing on the tile, a stale /objects snapshot) are the
    normal case -- they must not accumulate across a working session into a spurious stop."""
    calls = []
    _stub_tick_world(monkeypatch, [], [{"name": "Box trap", "qty": 4}], calls)
    outcomes = iter([False] * (ch.MAX_CONSECUTIVE_FAILURES - 1) + [True]
                    + [False] * (ch.MAX_CONSECUTIVE_FAILURES - 1))
    monkeypatch.setattr(ch, "lay_trap", lambda ctx, tile: next(outcomes))

    ctx = _Ctx()
    for _ in range(ch.MAX_CONSECUTIVE_FAILURES * 2 - 1):
        ch.tick(ctx)
    assert not ctx.state.safety_stopped


# --- before_break -------------------------------------------------------------------

def test_before_break_clears_traps_that_get_re_laid_when_checked(monkeypatch):
    """CONFIRMED live 2026-08-21 ("2 trap(s) still laid going into the break", the one outcome
    this hook exists to prevent). Checking a full trap RE-LAYS it, so a tile takes two actions
    to actually clear: check it, then take the re-laid trap. Sorting "full" first made the loop
    round-robin across all four tiles, and by the time it came back a chinchompa had refilled the
    trap it had just re-laid -- so it never finished a single tile and burned its whole budget.

    The world here models both halves of that: a check re-lays, and a trap left set for a few
    actions refills. Teardown therefore has to finish the tile it just touched."""
    world = {(2557, 2937, 0): [9383, 0], (2558, 2936, 0): [9383, 0],
             (2557, 2935, 0): [9383, 0], (2556, 2936, 0): [9383, 0]}
    actions = []

    def _objects(**_kw):
        return [_obj(state[0], t[0], t[1]) for t, state in world.items()]

    def _retrieve(_ctx, tile, state, _obj_):
        actions.append((state, tile))
        if state == "full":
            world[tile] = [9380, 0]          # catch collected, trap re-laid as a set trap
        else:
            world.pop(tile)                  # set/failed -> taken into the pack
        for other, entry in world.items():   # everything still laid ages by one action
            if other != tile and entry[0] == 9380:
                entry[1] += 1
                if entry[1] >= 3:
                    entry[0] = 9383          # a chinchompa refilled it
        return True

    monkeypatch.setattr(controller, "get_objects", _objects)
    monkeypatch.setattr(ch, "retrieve_trap", _retrieve)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    ctx = _Ctx()
    ctx.state.data["chin_plan"] = [list(t) for t in world]
    ch.before_break(ctx)

    assert world == {}, \
        f"every trap must be off the ground before the break starts; {len(world)} left after " \
        f"{len(actions)} actions"



def _stub_break_world(monkeypatch, object_batches, retrieved):
    monkeypatch.setattr(controller, "get_objects", lambda **kw: object_batches.pop(0)
                        if object_batches else [])
    monkeypatch.setattr(ch, "retrieve_trap",
                        lambda ctx, tile, state, obj: retrieved.append((state, tile)) or True)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


def test_before_break_picks_up_every_laid_trap(monkeypatch):
    """The headline requirement. The world is mutable here rather than a fixed list of batches:
    each successful retrieval removes that trap, so the loop has to genuinely clear all of them
    to terminate."""
    world = [_obj(9380, 2557, 2937), _obj(9383, 2558, 2936)]
    retrieved = []

    def _retrieve(ctx, tile, state, obj):
        retrieved.append((state, tile))
        world.remove(obj)
        return True

    monkeypatch.setattr(controller, "get_objects", lambda **kw: list(world))
    monkeypatch.setattr(ch, "retrieve_trap", _retrieve)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    ctx = _Ctx()
    ctx.state.data["chin_plan"] = [[2557, 2937, 0], [2558, 2936, 0]]
    ch.before_break(ctx)

    assert world == [], "no trap may be left laid going into the break"
    assert [state for state, _ in retrieved] == ["set", "full"], \
        "teardown takes the one-action removals first -- see the re-lay test above for why " \
        "'catches first' could not clear the ground at all"


def test_before_break_collects_a_catch_rather_than_dismantling_it(monkeypatch):
    """The option list per state is what protects the catch -- assert the FULL trap is retrieved
    as "full" (whose action list starts with Check), not as a generic pickup."""
    retrieved = []
    _stub_break_world(monkeypatch, [[_obj(9383, 2558, 2936)], []], retrieved)
    ctx = _Ctx()
    ctx.state.data["chin_plan"] = [[2558, 2936, 0]]
    ch.before_break(ctx)
    assert retrieved == [("full", (2558, 2936, 0))]


def test_before_break_ignores_traps_that_are_not_on_our_plan_tiles(monkeypatch):
    retrieved = []
    _stub_break_world(monkeypatch, [[_obj(9380, 2540, 2900)], []], retrieved)
    ctx = _Ctx()
    ctx.state.data["chin_plan"] = [[2557, 2937, 0]]
    ch.before_break(ctx)
    assert retrieved == []


def test_before_break_cannot_loop_forever_on_a_trap_it_cannot_retrieve(monkeypatch):
    """A trap that never comes back (someone else's, or one that stopped responding) must not be
    able to hold the break -- and the routine -- hostage."""
    retrieved = []
    monkeypatch.setattr(controller, "get_objects", lambda **kw: [_obj(9380, 2557, 2937)])
    monkeypatch.setattr(ch, "retrieve_trap",
                        lambda ctx, tile, state, obj: retrieved.append(tile) or False)
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)

    ctx = _Ctx()
    ctx.state.data["chin_plan"] = [[2557, 2937, 0]]
    ch.before_break(ctx)
    assert 0 < len(retrieved) <= 4, "bounded by len(plan) * 2 + 2"


def test_the_teardown_report_rescans_instead_of_trusting_a_stale_count(monkeypatch):
    """The last attempt in the budget can succeed. Reporting the count from the top of that
    iteration would say "1 trap still laid" when there are none -- exactly the log line that
    sends someone hunting a bug that isn't there."""
    world = [_obj(9380, 2557, 2937)]
    logged = []
    monkeypatch.setattr(controller, "get_objects", lambda **kw: list(world))
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    monkeypatch.setattr(ch.debug_log, "log", lambda category, message: logged.append((category, message)))

    # Fails every attempt but the last, so the budget is exhausted at the moment the world
    # finally comes clean.
    attempts = []

    def _retrieve(ctx, tile, state, obj):
        attempts.append(tile)
        if len(attempts) == 4:      # len(plan) * 2 + 2 with a one-tile plan
            world.clear()
        return True

    monkeypatch.setattr(ch, "retrieve_trap", _retrieve)

    ctx = _Ctx()
    ctx.state.data["chin_plan"] = [[2557, 2937, 0]]
    ch.before_break(ctx)

    assert not any(category == "!" for category, _ in logged), \
        "a clean teardown must not be reported as traps left behind"


def test_before_break_does_nothing_when_no_traps_were_ever_laid(monkeypatch):
    retrieved = []
    _stub_break_world(monkeypatch, [], retrieved)
    ctx = _Ctx()
    ch.before_break(ctx)
    assert retrieved == []


def test_before_break_waits_out_a_trap_that_is_mid_catch(monkeypatch):
    """A closing trap is about to become a catch -- clicking through the animation throws it
    away."""
    retrieved = []
    _stub_break_world(monkeypatch, [[_obj(9390, 2557, 2937)], [_obj(9383, 2557, 2937)], []],
                       retrieved)
    ctx = _Ctx()
    ctx.state.data["chin_plan"] = [[2557, 2937, 0]]
    ch.before_break(ctx)
    assert retrieved == [("full", (2557, 2937, 0))]


# --- the routine is wired into the engine -------------------------------------------

def test_the_routine_is_registered_for_the_runelite_dropdown():
    """Wiring a routine into ROUTINE_FILES is a required step, not polish (CLAUDE.md) -- without
    it the routine is only launchable from a command line."""
    assert controller.ROUTINE_FILES["CHINCHOMPA_HUNTING"] == "routines/chinchompa_hunting.py"


def test_the_routine_satisfies_the_routine_contract():
    assert callable(ch.tick)
    assert callable(ch.before_break)
    assert callable(ch.on_event)
    assert callable(ch.get_config)
