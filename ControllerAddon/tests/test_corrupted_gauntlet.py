"""routines/corrupted_gauntlet.py -- the I/O layer over hunllef.py. Bridge fully stubbed."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import controller
import hunllef as h
import gauntlet_ids as g
import corrupted_gauntlet as cg


@pytest.fixture(autouse=True)
def no_live_bridge(monkeypatch):
    for name in ("get_player", "get_npcs", "get_objects", "get_prayers", "get_equipment", "get_inventory",
                 "get_combat", "get_ground_walk_point", "get_minimap_walk_point", "get_collision_grid"):
        monkeypatch.setattr(controller, name, lambda *a, _n=name, **kw: pytest.fail(f"live bridge call {_n}"))
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)


class _State:
    def __init__(self):
        self.data = {}
        self.safety_stopped = False
        self.stops = []

    def trigger_safety_stop(self, msg):
        self.safety_stopped = True
        self.stops.append(msg)


class _Ctx:
    def __init__(self, events=None):
        self.ser = object()
        self.state = _State()
        self.events = events


def _npc(id_, x=13105, y=5700, anim=-1, index=1):
    return {"id": id_, "index": index, "instX": x, "instY": y, "animationId": anim, "hpRatio": 90,
            "name": "Corrupted Hunllef", "screenX": 500, "screenY": 400}


def test_derive_phase():
    data = {}
    assert cg.derive_phase(data, npcs=[], objects=[{"id": g.OBJ_BARRIER}]) == "enter"
    assert cg.derive_phase(data, npcs=[_npc(9036)], objects=[]) == "fight"
    assert data["cg_fight_seen"] is True
    assert cg.derive_phase(data, npcs=[_npc(9038)], objects=[]) == "fight"         # dying, still the fight phase
    assert cg.derive_phase(data, npcs=[], objects=[{"id": g.OBJ_REWARD_CHEST}]) == "loot"
    data["cg_chest_opened"] = True
    assert cg.derive_phase(data, npcs=[], objects=[{"id": g.OBJ_REWARD_CHEST}]) == "done"
    assert cg.derive_phase({}, npcs=[], objects=[{"id": g.OBJ_REWARD_CHEST}]) == "unknown"   # no fight seen this run
    assert cg.derive_phase({}, npcs=[], objects=[]) == "unknown"


def test_read_snapshot_composes_the_seven_reads(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"hp": 70, "prayer": 40, "instX": 1, "instY": 2, "plane": 1, "animation": -1})
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [_npc(9036)])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "get_prayers", lambda: {"active": {"PROTECT_FROM_MISSILES": True}})
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (perfected)"}})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"targetId": 9036})
    s = cg.read_snapshot()
    assert s.hp == 70 and s.hunllef["id"] == 9036 and s.weapon_kind() == "bow" and s.target_id == 9036
    monkeypatch.setattr(controller, "get_player", lambda: {})
    assert cg.read_snapshot() is None


def test_feed_tracker_prefers_events_and_falls_back_to_snapshot_transitions():
    t = h.StyleTracker()
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036, anim=8419)], [], {}, {}, [], {})
    cg.feed_tracker(t, snap, [("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": 8419}),
                              ("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": -1}),
                              ("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": 8419}),
                              ("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9040, "animationId": 8419})])
    assert t.attacks == 2                                   # two Hunllef attacks; the rat's is ignored
    t2 = h.StyleTracker()
    cg.feed_tracker(t2, snap, None)                         # no events stream: snapshot transition
    assert t2.attacks == 1
    cg.feed_tracker(t2, snap, None)
    assert t2.attacks == 1                                  # same animation again: no new attack


class _SpyTracker:
    """Records exactly which raw observations feed_tracker makes, so the C3 cross-check test can
    assert on the CALL PATTERN (events vs. the snapshot fallback) without going through
    StyleTracker's own attack/marker/resync bookkeeping, which would conflate the two."""

    def __init__(self):
        self.observed = []
        self.observed_snapshot = []

    def observe(self, anim):
        self.observed.append(anim)

    def observe_snapshot(self, anim):
        self.observed_snapshot.append(anim)


def test_feed_tracker_cross_checks_events_against_the_snapshot_animation():
    """C3: /npcs now reports animationId (NPCProvider.buildNpcData), so both sources are fed every
    tick -- events first, then the snapshot's own animation ONLY when it is an attack-like/marker
    id that none of this tick's Hunllef events already carried."""
    # events carry 8419, snapshot also shows 8419 -> a duplicate of the event, counted once.
    spy = _SpyTracker()
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036, anim=8419)], [], {}, {}, [], {})
    cg.feed_tracker(spy, snap, [("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": 8419})])
    assert spy.observed == [8419] and spy.observed_snapshot == []

    # events carry nothing, snapshot shows a marker id -> counted as a marker via the snapshot.
    spy2 = _SpyTracker()
    snap_marker = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036, anim=8754)], [], {}, {}, [], {})
    cg.feed_tracker(spy2, snap_marker, [])
    assert spy2.observed == [] and spy2.observed_snapshot == [8754]

    # events carry 8419 (an attack), snapshot shows 8754 (a marker the events did not carry) ->
    # both observations happen.
    spy3 = _SpyTracker()
    snap_both = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036, anim=8754)], [], {}, {}, [], {})
    cg.feed_tracker(spy3, snap_both, [("NPC_TRACKED_ANIMATION_CHANGED", {"id": 9036, "animationId": 8419})])
    assert spy3.observed == [8419] and spy3.observed_snapshot == [8754]


def test_do_prayer_opens_the_tab_and_clicks_the_icon(monkeypatch):
    clicks = []
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": True, "icons": {"PROTECT_FROM_MAGIC": {"screenX": 1, "screenY": 2}}, "active": {"PROTECT_FROM_MAGIC": True}})
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: clicks.append((entity, kw)) or True)
    ctx = _Ctx()
    assert cg.do_prayer(ctx, None, "PROTECT_FROM_MAGIC", cg.DEFAULT_CONFIG)
    entity, kw = clicks[0]
    assert entity["screenX"] == 1 and kw["check_ui_blockers"] is False and kw["simulate_mistakes"] is False and kw["offset"] == (0, 0)


def test_do_prayer_marks_a_missing_icon_unusable(monkeypatch):
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": True, "icons": {}, "active": {}})
    ctx = _Ctx()
    assert not cg.do_prayer(ctx, None, "RIGOUR", cg.DEFAULT_CONFIG)
    assert "RIGOUR" in ctx.state.data["cg_unusable_prayers"]


def test_do_escape_shift_clicks_a_safe_tile_on_screen(monkeypatch):
    clicks = []
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: {"baseX": 13056, "baseY": 5632, "plane": 1, "size": 32, "flags": [0] * 1024})
    monkeypatch.setattr(controller, "get_ground_walk_point", lambda x, y, plane, inst=False: {"onScreen": True, "screenX": 5, "screenY": 6, "_t": (x, y, inst)})
    monkeypatch.setattr(cg.travel, "_click_walk_point", lambda ctx, point, shift: clicks.append((point, shift)))
    snap = h.FightSnapshot.from_bridge({"instX": 13066, "instY": 5642, "plane": 1, "hp": 50, "prayer": 30},
                                       [_npc(9036, 13072, 5642, 8419)], [{"id": 36048, "instX": 13066, "instY": 5642}], {}, {}, [], {})
    assert cg.do_escape(_Ctx(), snap, "hazard", cg.DEFAULT_CONFIG)
    point, shift = clicks[0]
    assert shift is True and point["_t"][2] is True
    tx, ty = point["_t"][0], point["_t"][1]
    assert not h.forbidden_near_hunllef((tx, ty), (13072, 5642)) and (tx, ty) != (13066, 5642)


def test_do_escape_returns_false_without_a_map(monkeypatch):
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: None)
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2, "plane": 1}, [_npc(9036)], [], {}, {}, [], {})
    assert not cg.do_escape(_Ctx(), snap, "tornado", cg.DEFAULT_CONFIG)


def test_do_escape_caches_the_instance_map_across_calls(monkeypatch):
    """I4: the InstanceMap is static for the whole fight -- do_escape must load it once and reuse
    ctx.state.data['cg_imap'], not hit /collision?all=true on every escape/reposition click."""
    load_calls = [0]

    def fake_get_collision_grid(plane=None):
        load_calls[0] += 1
        return {"baseX": 13056, "baseY": 5632, "plane": 1, "size": 32, "flags": [0] * 1024}

    monkeypatch.setattr(controller, "get_collision_grid", fake_get_collision_grid)
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane, inst=False: {"onScreen": True, "screenX": 5, "screenY": 6})
    monkeypatch.setattr(cg.travel, "_click_walk_point", lambda ctx, point, shift: None)
    snap = h.FightSnapshot.from_bridge({"instX": 13066, "instY": 5642, "plane": 1, "hp": 50, "prayer": 30},
                                       [_npc(9036, 13072, 5642, 8419)],
                                       [{"id": 36048, "instX": 13066, "instY": 5642}], {}, {}, [], {})
    ctx = _Ctx()
    assert cg.do_escape(ctx, snap, "hazard", cg.DEFAULT_CONFIG)
    assert cg.do_escape(ctx, snap, "hazard", cg.DEFAULT_CONFIG)
    assert load_calls[0] == 1
    assert "cg_imap" in ctx.state.data


def test_tick_drops_the_cached_instance_map_when_leaving_the_fight_phase(monkeypatch):
    """I4: ctx.state.data['cg_imap'] must not survive into a phase where the static grid no
    longer applies -- tick() drops it the moment derive_phase reports leaving "fight"."""
    ctx = _Ctx()
    ctx.state.data.update({"cg_phase": "fight", "cg_imap": object(), "cg_fight_seen": True,
                          "cg_chest_opened": True})
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [{"id": g.OBJ_REWARD_CHEST}])
    cg.tick(ctx)
    assert "cg_imap" not in ctx.state.data
    assert ctx.state.safety_stopped is True   # phase derives to "done" -- incidental to this test


def test_do_eat_clicks_a_fish_after_opening_the_inventory(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: calls.append("inv") or True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append(("click", entity["name"], kw.get("expected_action"))) or True)
    inv = [{"slot": 0, "name": "Paddlefish", "screenX": 1, "screenY": 1}, {"slot": 1, "name": "Corrupted paddlefish", "screenX": 2, "screenY": 1}]
    snap = h.FightSnapshot.from_bridge({"hp": 30, "instX": 1, "instY": 2}, [_npc(9036)], [], {}, {}, inv, {})
    assert cg.do_eat(_Ctx(), snap, None, cg.DEFAULT_CONFIG)
    assert calls == ["inv", ("click", "Corrupted paddlefish", "Eat")]


def test_do_attack_escalates_a_withheld_click_to_the_menu(monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda index: (lambda: None))
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append("click") or controller.CLICK_WITHHELD)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: calls.append(("menu", option)) or True)
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036)], [], {}, {}, [], {})
    assert cg.do_attack(_Ctx(), snap, None, cg.DEFAULT_CONFIG)
    assert calls == ["click", ("menu", "Attack")]


def test_enter_phase_has_attempt_ceiling(monkeypatch):
    clicks = []
    inv = [{"name": f"Corrupted paddlefish", "screenX": 1, "screenY": 1} for _ in range(10)]
    inv.append({"name": "Egniol potion", "screenX": 2, "screenY": 2})
    inv.append({"name": "Corrupted staff (perfected)", "screenX": 3, "screenY": 3})  # staff in inventory
    monkeypatch.setattr(controller, "get_player", lambda: {"hp": 70, "prayer": 40, "instX": 1, "instY": 2, "plane": 1, "animation": -1})
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": True, "active": {"PROTECT_FROM_MISSILES": True}, "icons": {}})
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (perfected)"}})
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [{"id": g.OBJ_BARRIER, "screenX": 500, "screenY": 400}])
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])  # no Hunllef spawned
    monkeypatch.setattr(controller, "get_combat", lambda: {"targetId": -1})
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: clicks.append(option) or True)
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    def fake_confirm(ctx, check, budget_s=None):
        return False  # never confirms Hunllef spawn
    monkeypatch.setattr(cg, "_confirm", fake_confirm)
    ctx = _Ctx()
    # Call enter_phase three times: should click the barrier three times
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(clicks) == 1 and clicks[0] == g.BARRIER_QUICK_PASS
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(clicks) == 2
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(clicks) == 3
    # Fourth call should not click and should safety-stop
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(clicks) == 3  # no new click
    assert ctx.state.safety_stopped is True
    assert any("barrier quick-pass did not start" in s for s in ctx.state.stops)


def _loot_setup(monkeypatch):
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [{"id": g.OBJ_REWARD_CHEST, "screenX": 500, "screenY": 400}])
    monkeypatch.setattr(controller, "human_delay", lambda a, b: None)
    # Bypass real timing: _confirm's budget/sleep loop is exercised by test_hunllef.py-adjacent
    # machinery elsewhere; here the interesting behaviour is entirely in what `check()` decides,
    # so it is called exactly once.
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=None: check())


def test_loot_phase_click_alone_does_not_confirm_the_chest_opened(monkeypatch):
    """I2: a click that merely REGISTERED (result truthy) is not proof the chest opened -- only
    an inventory delta or a "You open the chest" chat message is. No delta, no chat -> the flag
    stays unset and the attempt is counted toward the ceiling."""
    _loot_setup(monkeypatch)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    inv = [{"name": "Bones", "qty": 1}]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)   # identical before and after
    ctx = _Ctx()
    cg.loot_phase(ctx, cg.DEFAULT_CONFIG)
    assert ctx.state.data.get("cg_chest_opened") is None
    assert ctx.state.data.get("cg_loot_attempts") == 1
    assert ctx.state.safety_stopped is False


def test_loot_phase_confirms_on_an_inventory_delta(monkeypatch):
    _loot_setup(monkeypatch)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    invs = iter([[{"name": "Bones", "qty": 1}],
                [{"name": "Bones", "qty": 1}, {"name": "Gold ore", "qty": 5}]])
    monkeypatch.setattr(controller, "get_inventory", lambda: next(invs))
    ctx = _Ctx()
    cg.loot_phase(ctx, cg.DEFAULT_CONFIG)
    assert ctx.state.data.get("cg_chest_opened") is True
    assert ctx.state.data.get("cg_loot_attempts", 0) == 0


def test_loot_phase_confirms_on_a_qty_delta_with_the_same_item_count(monkeypatch):
    """A stacked item (e.g. coins) can change qty without changing the inventory's length --
    'item count OR any qty delta' per the brief, so this must also confirm."""
    _loot_setup(monkeypatch)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    invs = iter([[{"name": "Coins", "qty": 100}], [{"name": "Coins", "qty": 250}]])
    monkeypatch.setattr(controller, "get_inventory", lambda: next(invs))
    ctx = _Ctx()
    cg.loot_phase(ctx, cg.DEFAULT_CONFIG)
    assert ctx.state.data.get("cg_chest_opened") is True


def test_loot_phase_confirms_via_chat_message_when_no_inventory_delta(monkeypatch):
    """The reward chest can legitimately give nothing new to carry (or something already fully
    covered by the pre-click snapshot's own item), so the chat line is a second, independent
    confirmation path -- via ctx.events.since(mark), where mark is taken before the click."""
    _loot_setup(monkeypatch)
    inv = [{"name": "Bones", "qty": 1}]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)   # never changes
    ctx = _Ctx(events=_FakeFightEvents([]))

    def fake_click(ser, entity, **kw):
        # The chat line arrives as a RESULT of the click -- after mark() was already taken.
        ctx.events._absorb([("CHAT_MESSAGE", {"message": "You open the chest."})])
        return True
    monkeypatch.setattr(controller, "click_entity", fake_click)
    cg.loot_phase(ctx, cg.DEFAULT_CONFIG)
    assert ctx.state.data.get("cg_chest_opened") is True


def test_loot_phase_ceiling_on_unconfirmed_attempts(monkeypatch):
    """I2: the ceiling still bounds the loop, now on UNCONFIRMED attempts (a click that registers
    but never proves the chest opened) rather than on a bare falsy click."""
    _loot_setup(monkeypatch)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Bones", "qty": 1}])
    ctx = _Ctx()
    for _ in range(cg.LOOT_MAX_ATTEMPTS - 1):
        cg.loot_phase(ctx, cg.DEFAULT_CONFIG)
    assert ctx.state.data.get("cg_chest_opened") is None
    assert ctx.state.safety_stopped is False
    cg.loot_phase(ctx, cg.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped is True
    assert any("could not open the reward chest" in s for s in ctx.state.stops)


def test_loot_phase_ceiling_still_counts_an_outright_click_failure(monkeypatch):
    _loot_setup(monkeypatch)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": "Bones", "qty": 1}])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: False)
    ctx = _Ctx()
    for _ in range(cg.LOOT_MAX_ATTEMPTS):
        cg.loot_phase(ctx, cg.DEFAULT_CONFIG)
    assert ctx.state.data.get("cg_chest_opened") is None
    assert ctx.state.safety_stopped is True
    assert any("could not open the reward chest" in s for s in ctx.state.stops)


class _FakeFightEvents:
    """Models exactly the two properties of the real EventStream that C2 depends on: mark() does
    NOT pump (it only returns the current sequence number), while since() and wait_ticks() DO --
    wait_ticks() here simulates a pump during the tick wait by absorbing the next queued batch of
    animation events into the log, the same way the real ctx.events.wait_ticks() drains the UDP
    socket mid-wait."""

    def __init__(self, wait_batches):
        self._seq = 0
        self._log = []                          # (seq, event_type, payload)
        self._wait_batches = list(wait_batches)  # one batch "arrives" per wait_ticks() call

    def _absorb(self, batch):
        for event_type, payload in batch:
            self._seq += 1
            self._log.append((self._seq, event_type, payload))

    def mark(self):
        return self._seq

    def since(self, seq):
        return [(t, p) for s, t, p in self._log if s > seq]

    def wait_ticks(self, n):
        if self._wait_batches:
            self._absorb(self._wait_batches.pop(0))


def _anim_event(anim, npc_id=9036):
    return ("NPC_TRACKED_ANIMATION_CHANGED", {"id": npc_id, "animationId": anim})


def test_fight_loop_mark_since_placement_does_not_drop_events(monkeypatch):
    """C2: mark() does not pump; since() and wait_ticks() do. Taking a fresh `mark =
    ctx.events.mark()` at the TOP of every iteration reads a sequence number that the PREVIOUS
    iteration's own `_wait_tick` call (which calls wait_ticks(), which pumps) already advanced --
    so every event that arrived during that action/tick wait sits behind the new mark and
    `since(mark)` can never see it. Two batches of Hunllef attack-animation events are queued to
    arrive during the first two `_wait_tick` calls; every one of the 4 events must still reach the
    tracker by the time the loop ends. FAILS against the old per-iteration mark placement (0
    attacks counted instead of 4) -- this failure IS the proof C2 existed."""
    events = _FakeFightEvents([[_anim_event(8419), _anim_event(8419)],
                              [_anim_event(8419), _anim_event(8419)]])
    ctx = _Ctx(events=events)
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2, "hp": 80, "prayer": 50},
                                       [_npc(9036, anim=-1)], [], {"active": {}}, {}, [],
                                       {"targetId": 9036})
    calls = {"n": 0}

    def fake_read_snapshot(cfg, ctx=None):
        calls["n"] += 1
        if calls["n"] >= 3:
            ctx.state.safety_stopped = True
        return snap

    monkeypatch.setattr(cg, "read_snapshot", fake_read_snapshot)
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0)
    monkeypatch.setattr(h, "ladder", lambda snap, tracker, cfg, unusable: ("idle", None))

    cg.fight_loop(ctx, cg.DEFAULT_CONFIG)

    assert calls["n"] == 3
    tracker = ctx.state.data["cg_tracker"]
    assert tracker.attacks == 4, "not every event fed during the waits reached the tracker"


def test_fight_loop_safety_stops_on_player_died_event(monkeypatch):
    """I3: a PLAYER_DIED event scanned from since(mark) safety-stops the fight loop, alongside the
    existing hp<=0 HTTP check -- UDP can carry this before the next HTTP poll notices hp==0.
    The ladder must never run once death is detected."""
    events = _FakeFightEvents([])
    ctx = _Ctx(events=events)
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2, "hp": 80, "prayer": 50},
                                       [_npc(9036, anim=-1)], [], {"active": {}}, {}, [],
                                       {"targetId": 9036})

    def fake_read_snapshot(cfg, ctx=None):
        events._absorb([("PLAYER_DIED", None)])   # arrives after this iteration's mark
        return snap

    monkeypatch.setattr(cg, "read_snapshot", fake_read_snapshot)
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0)
    monkeypatch.setattr(h, "ladder", lambda snap, tracker, cfg, unusable:
                        pytest.fail("ladder must not run once PLAYER_DIED is seen"))

    cg.fight_loop(ctx, cg.DEFAULT_CONFIG)

    assert ctx.state.safety_stopped is True
    assert any("died to the Hunllef" in s for s in ctx.state.stops)


class _StopLoop(Exception):
    """Ends a fight_loop test after exactly one iteration without going through
    ctx.state.trigger_safety_stop -- so 'no safety stop happened' stays a meaningful assertion."""


def test_fight_loop_skips_the_ladder_for_a_dead_hunllef(monkeypatch):
    """I3: HUNLLEF_PRAYER_BY_ID[id] == 'DEAD' (id 9038) is a corpse -- the ladder (and therefore
    every ACTIONS click, including attack) must be skipped for that tick entirely, with no
    safety stop triggered by the skip itself."""
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2, "hp": 80, "prayer": 50},
                                       [_npc(9038, anim=-1)], [], {"active": {}}, {}, [],
                                       {"targetId": 9038})
    ctx = _Ctx()
    monkeypatch.setattr(cg, "read_snapshot", lambda cfg, ctx=None: snap)
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0)
    monkeypatch.setattr(h, "ladder", lambda snap, tracker, cfg, unusable:
                        pytest.fail("ladder must not run against a dead Hunllef"))
    actions_called = []
    monkeypatch.setattr(cg, "ACTIONS",
                        {k: (lambda *a, _k=k, **kw: actions_called.append(_k)) for k in cg.ACTIONS})

    def fake_wait_tick(ctx):
        raise _StopLoop()
    monkeypatch.setattr(cg, "_wait_tick", fake_wait_tick)

    with pytest.raises(_StopLoop):
        cg.fight_loop(ctx, cg.DEFAULT_CONFIG)

    assert actions_called == []
    assert ctx.state.safety_stopped is False
    assert ctx.state.stops == []
