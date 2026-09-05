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
import travel


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


def test_derive_phase_also_treats_the_fight_varbit_as_fight():
    """M: VARBIT_FIGHTING_HUNLLEF (mirrored into data['cg_varbit_fight'] by on_event) is a second
    'we are in the fight' signal alongside the Hunllef actually showing up in /npcs."""
    data = {"cg_varbit_fight": 1}
    assert cg.derive_phase(data, npcs=[], objects=[]) == "fight"
    assert data["cg_fight_seen"] is True


def test_on_event_mirrors_only_the_fight_varbit():
    ctx = _Ctx()
    cg.on_event(ctx, "VARBIT_CHANGED", {"varbitId": g.VARBIT_FIGHTING_HUNLLEF, "value": 1})
    assert ctx.state.data["cg_varbit_fight"] == 1
    cg.on_event(ctx, "VARBIT_CHANGED", {"varbitId": 99999, "value": 1})   # a different varbit
    assert ctx.state.data["cg_varbit_fight"] == 1                        # unchanged
    cg.on_event(ctx, "CHAT_MESSAGE", {"message": "hi"})                  # a different event type
    assert ctx.state.data["cg_varbit_fight"] == 1
    cg.on_event(ctx, "VARBIT_CHANGED", {"varbitId": g.VARBIT_FIGHTING_HUNLLEF, "value": 0})
    assert ctx.state.data["cg_varbit_fight"] == 0


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


def _escape_click_setup(monkeypatch):
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None:
                        {"baseX": 13056, "baseY": 5632, "plane": 1, "size": 32, "flags": [0] * 1024})
    clicks = []
    monkeypatch.setattr(controller, "get_ground_walk_point",
                        lambda x, y, plane, inst=False: {"onScreen": True, "screenX": 5, "screenY": 6, "_t": (x, y)})
    monkeypatch.setattr(cg.travel, "_click_walk_point", lambda ctx, point, shift: clicks.append(point["_t"]))
    return clicks


def test_do_escape_sticks_to_the_previous_target_across_calls(monkeypatch):
    """I5: two consecutive do_escape calls against the same snapshot geometry must click the same
    tile -- and, more precisely, the SECOND call must not re-run pick_safe_tile at all, reusing
    ctx.state.data['cg_escape_target'] because it is still safe under hunllef.tile_is_safe.

    Uses a tornado (not the Hunllef's own footprint) to force the picked tile at least two steps
    from the player -- a tile picked only one step away is trivially "reached" (Chebyshev <= 1)
    the instant it is picked, which would clear it immediately and hide the real stickiness this
    test exists to prove; a Hunllef far away keeps the footprint/margin filter out of the way."""
    clicks = _escape_click_setup(monkeypatch)
    calls = [0]
    real_pick = h.pick_safe_tile

    def spy_pick(*a, **kw):
        calls[0] += 1
        return real_pick(*a, **kw)
    monkeypatch.setattr(h, "pick_safe_tile", spy_pick)
    snap = h.FightSnapshot.from_bridge(
        {"instX": 13066, "instY": 5642, "plane": 1, "hp": 50, "prayer": 30},
        [_npc(9036, 13166, 5642, 8419), _npc(9039, 13066, 5643, index=2)], [], {}, {}, [], {})
    ctx = _Ctx()
    assert cg.do_escape(ctx, snap, "tornado", cg.DEFAULT_CONFIG)
    first_target = ctx.state.data["cg_escape_target"]
    assert h.cheb(snap.me, first_target) > 1, "test setup did not force a non-adjacent pick"
    assert cg.do_escape(ctx, snap, "tornado", cg.DEFAULT_CONFIG)
    assert calls[0] == 1, "pick_safe_tile ran again instead of keeping the sticky target"
    assert clicks[0] == clicks[1]


def test_do_escape_repicks_once_the_previous_target_is_reached(monkeypatch):
    clicks = _escape_click_setup(monkeypatch)
    ctx = _Ctx()
    ctx.state.data["cg_escape_target"] = (13066, 5642)   # == snap.me below: Chebyshev 0 -- reached
    calls = [0]
    real_pick = h.pick_safe_tile

    def spy_pick(*a, **kw):
        calls[0] += 1
        return real_pick(*a, **kw)
    monkeypatch.setattr(h, "pick_safe_tile", spy_pick)
    snap = h.FightSnapshot.from_bridge({"instX": 13066, "instY": 5642, "plane": 1, "hp": 50, "prayer": 30},
                                       [_npc(9036, 13072, 5642, 8419)],
                                       [{"id": 36048, "instX": 13066, "instY": 5642}], {}, {}, [], {})
    assert cg.do_escape(ctx, snap, "hazard", cg.DEFAULT_CONFIG)
    assert calls[0] == 1
    assert ctx.state.data["cg_escape_target"] != (13066, 5642)


def test_do_escape_repicks_when_previous_target_becomes_unsafe(monkeypatch):
    clicks = _escape_click_setup(monkeypatch)
    ctx = _Ctx()
    stale = (13066, 5642 + 6)     # far from the player -- not "reached" -- but about to be a hazard
    ctx.state.data["cg_escape_target"] = stale
    calls = [0]
    real_pick = h.pick_safe_tile

    def spy_pick(*a, **kw):
        calls[0] += 1
        return real_pick(*a, **kw)
    monkeypatch.setattr(h, "pick_safe_tile", spy_pick)
    snap = h.FightSnapshot.from_bridge({"instX": 13066, "instY": 5642, "plane": 1, "hp": 50, "prayer": 30},
                                       [_npc(9036, 13072, 5642, 8419)],
                                       [{"id": 36047, "instX": stale[0], "instY": stale[1]}], {}, {}, [], {})
    assert cg.do_escape(ctx, snap, "hazard", cg.DEFAULT_CONFIG)
    assert calls[0] == 1
    assert ctx.state.data["cg_escape_target"] != stale


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
    monkeypatch.setattr(controller, "get_player", lambda: {"regionId": 7768})
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [{"id": g.OBJ_REWARD_CHEST}])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
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


def test_do_attack_uses_a_substring_target_matching_either_hunllef_variant(monkeypatch):
    """M: 'Hunllef' substring-matches both 'Corrupted Hunllef' and 'Crystalline Hunllef' -- the
    old literal 'Corrupted Hunllef' withheld every click against the crystalline variant."""
    seen = {}
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda index: (lambda: None))
    monkeypatch.setattr(controller, "click_entity",
                        lambda ser, entity, **kw: seen.setdefault("expected_target", kw.get("expected_target")) or True)
    snap = h.FightSnapshot.from_bridge({"instX": 1, "instY": 2}, [_npc(9036)], [], {}, {}, [], {})
    assert cg.do_attack(_Ctx(), snap, None, cg.DEFAULT_CONFIG)
    assert seen["expected_target"] == "Hunllef"


def test_do_reposition_uses_the_in_range_escape_path_when_it_works(monkeypatch):
    monkeypatch.setattr(cg, "do_escape", lambda ctx, snap, reason, cfg, require_in_range=False:
                        require_in_range is True)
    assert cg.do_reposition(_Ctx(), None, None, cg.DEFAULT_CONFIG) is True


def test_do_reposition_falls_back_to_walking_toward_the_hunllef_when_out_of_range(monkeypatch):
    """M: when pick_safe_tile(require_in_range=True) finds nothing at all, do_reposition must
    walk toward the Hunllef instead of repeating "no safe tile" forever -- to a tile exactly
    REPOSITION_FALLBACK_CHEB (5) Chebyshev from its centre, on the line toward the player."""
    monkeypatch.setattr(cg, "do_escape", lambda ctx, snap, reason, cfg, require_in_range=False: False)
    calls = []
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y: calls.append((x, y)) or True)
    snap = h.FightSnapshot.from_bridge({"instX": 13060, "instY": 5632, "plane": 1},
                                       [_npc(9036, 13072, 5642, 8419)], [], {}, {}, [], {})
    ctx = _Ctx()
    assert cg.do_reposition(ctx, snap, None, cg.DEFAULT_CONFIG) is True
    assert len(calls) == 1
    target = calls[0]
    assert h.cheb(target, snap.hunllef_centre()) == cg.REPOSITION_FALLBACK_CHEB


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


def test_enter_phase_refuses_to_quick_pass_when_a_break_is_due():
    """I6: enter_phase must not click at all when ctx.state.break_due() exists and reports True --
    guarded via getattr so a state object without the method (an older engine build) behaves
    exactly as before. No bridge stub is needed here: the guard must fire before read_snapshot
    (or any other bridge call) ever runs."""
    ctx = _Ctx()
    ctx.state.break_due = lambda: True
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert ctx.state.safety_stopped is False


def test_enter_phase_proceeds_when_break_due_is_absent_or_false(monkeypatch):
    monkeypatch.setattr(controller, "get_player", lambda: {"hp": 70, "prayer": 40, "instX": 1, "instY": 2, "plane": 1, "animation": -1})
    monkeypatch.setattr(controller, "get_prayers", lambda: {"tabOpen": True, "active": {"PROTECT_FROM_MISSILES": True}, "icons": {}})
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (perfected)"}})
    inv = [{"name": "Corrupted paddlefish", "screenX": 1, "screenY": 1} for _ in range(10)]
    inv.append({"name": "Egniol potion", "screenX": 2, "screenY": 2})
    inv.append({"name": "Corrupted staff (perfected)", "screenX": 3, "screenY": 3})
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [{"id": g.OBJ_BARRIER, "screenX": 500, "screenY": 400}])
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_combat", lambda: {"targetId": -1})
    clicks = []
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: clicks.append(option) or True)
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=None: False)

    ctx = _Ctx()   # no break_due attribute at all
    cg.enter_phase(ctx, cg.DEFAULT_CONFIG)
    assert clicks == [g.BARRIER_QUICK_PASS]

    ctx2 = _Ctx()
    ctx2.state.break_due = lambda: False
    cg.enter_phase(ctx2, cg.DEFAULT_CONFIG)
    assert clicks == [g.BARRIER_QUICK_PASS, g.BARRIER_QUICK_PASS]


def test_before_break_plays_out_an_active_fight(monkeypatch):
    """I6: the GOTR shape -- a break due mid-fight must not start until fight_loop has run to
    completion (bounded by fight_max_s), since that is the only thing defending the player."""
    calls = []
    monkeypatch.setattr(cg, "fight_loop", lambda ctx, cfg: calls.append(cfg))
    ctx = _Ctx()
    ctx.state.data["cg_phase"] = "fight"
    cg.before_break(ctx)
    assert len(calls) == 1


def test_before_break_is_a_no_op_outside_the_fight_phase(monkeypatch):
    monkeypatch.setattr(cg, "fight_loop", lambda ctx, cfg: pytest.fail("fight_loop must not run outside the fight phase"))
    for phase in (None, "enter", "loot", "done", "unknown"):
        ctx = _Ctx()
        if phase is not None:
            ctx.state.data["cg_phase"] = phase
        cg.before_break(ctx)   # must not raise / must not call fight_loop


def test_before_break_plays_a_mid_run_prep_phase_out_to_done(monkeypatch):
    """C4: before_break must not hand the scheduler a mid-run instance -- for a prep phase
    (explore_gather here) it now keeps calling tick(ctx), the same dispatcher the engine loop
    itself uses, until the phase reaches "done" (or a safety stop, or the run's own worst-case
    deadline), instead of just teleporting home and abandoning the run's progress."""
    ctx = _Ctx()
    ctx.state.data["cg_t0"] = cg.time.time()
    ctx.state.data["cg_phase"] = "explore_gather"
    calls = [0]

    def fake_tick(ctx):
        calls[0] += 1
        if calls[0] >= 3:
            ctx.state.data["cg_phase"] = "done"
    monkeypatch.setattr(cg, "tick", fake_tick)
    cg.before_break(ctx)
    assert calls[0] == 3


def test_before_break_stops_looping_on_a_safety_stop(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["cg_t0"] = cg.time.time()
    ctx.state.data["cg_phase"] = "craft"
    calls = [0]

    def fake_tick(ctx):
        calls[0] += 1
        if calls[0] >= 2:
            ctx.state.trigger_safety_stop("cg: test stop")
    monkeypatch.setattr(cg, "tick", fake_tick)
    cg.before_break(ctx)
    assert calls[0] == 2


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


def test_time_left_and_note_entry():
    data = {}
    assert cg.time_left(data, now=100.0) is None
    cg.note_entry(data, {"regionId": 12127}, now=100.0)
    assert "cg_t0" not in data
    cg.note_entry(data, {"regionId": g.REGION_CORRUPTED}, now=100.0)
    assert data["cg_t0"] == 100.0
    cg.note_entry(data, {"regionId": g.REGION_CORRUPTED}, now=200.0)   # never moves
    assert data["cg_t0"] == 100.0
    assert cg.time_left(data, now=160.0) == cg.DEFAULT_CONFIG["timer_s"] - 60


def _kit_inv():
    return [{"slot": i, "name": n, "qty": q, "screenX": 1, "screenY": 1} for i, (n, q) in enumerate(
        [(g.ITEM_ORE, 3), (g.ITEM_BARK, 3), (g.ITEM_LINUM, 3), (g.ITEM_FRAME, 2), (g.ITEM_SHARDS, 300),
         (g.ITEM_RAW_FISH, 20), (g.ITEM_GRYM, 2)])]

EQ0 = {"weapon": {"name": g.ITEM_SCEPTRE}}


def test_prep_phase_ladder():
    cfg = cg.DEFAULT_CONFIG
    data = {"cg_t0": 0.0}
    assert cg.prep_phase(data, [], EQ0, cfg, now=10.0) == "explore_gather"
    # kit met but before the gather deadline: keep gathering (upgrades come from the ring)
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=100.0) == "explore_gather"
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=280.0) == "craft"
    # route done -> craft even if the kit is short
    data["cg_route_done"] = True
    assert cg.prep_phase(data, [], EQ0, cfg, now=100.0) == "craft"
    # hard deadline -> craft regardless
    data = {"cg_t0": 0.0}
    assert cg.prep_phase(data, [], EQ0, cfg, now=335.0) == "craft"
    # crafted, dragon revealed, time left -> upgrade; no time -> enter
    data = {"cg_t0": 0.0, "cg_route_done": True, "cg_crafted": True, "cg_demi_bosses": {(0, 2): 9047}}
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=300.0) == "upgrade"
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=400.0) == "enter"
    # crafted, nothing revealed -> enter
    data = {"cg_t0": 0.0, "cg_route_done": True, "cg_crafted": True, "cg_demi_bosses": {}}
    assert cg.prep_phase(data, _kit_inv(), EQ0, cfg, now=300.0) == "enter"
    # enter_by wins over everything
    data = {"cg_t0": 0.0}
    assert cg.prep_phase(data, [], EQ0, cfg, now=421.0) == "enter"


def test_derive_phase_prefers_prep_before_the_fight():
    data = {"cg_t0": 0.0}
    objects = [{"id": g.OBJ_BARRIER}]
    assert cg.derive_phase(data, [], objects, inventory=[], equipment=EQ0, cfg=cg.DEFAULT_CONFIG, now=10.0) == "explore_gather"
    assert cg.derive_phase(data, [], objects, inventory=[], equipment=EQ0, cfg=cg.DEFAULT_CONFIG, now=425.0) == "enter"
    assert cg.derive_phase(data, [_npc(9036)], objects) == "fight"       # fight still wins
    assert cg.derive_phase({}, [], objects) == "enter"                    # no t0 (started at the barrier by hand): old behaviour


# ---- explore_gather phase helpers and tests ----

def _player_at(x, y, hp=80, prayer=40):
    return {"hp": hp, "prayer": prayer, "instX": x, "instY": y, "plane": 1, "regionId": g.REGION_CORRUPTED, "animation": -1}


def _grid(size=64, room=None):
    flags = [0] * (size * size)
    if room is not None:
        # room is (x0, y0, w, h) in instance coords
        # Set BLOCK_MOVEMENT_OBJECT flag for all tiles OUTSIDE the room
        x0, y0, w, h = room
        base_x, base_y = 13056, 5632
        for y in range(size):
            for x in range(size):
                inst_x = base_x + x
                inst_y = base_y + y
                # Block if outside the room rectangle
                if not (x0 <= inst_x < x0 + w and y0 <= inst_y < y0 + h):
                    idx = y * size + x
                    flags[idx] = travel.BLOCK_MOVEMENT_OBJECT
    return {"baseX": 13056, "baseY": 5632, "plane": 1, "size": size, "flags": flags}


def _obj(id_, name, x, y):
    return {"id": id_, "name": name, "instX": x, "instY": y, "objectType": "GameObject", "screenX": 5, "screenY": 5, "actions": []}


def test_prep_state_derives_geometry_once(monkeypatch):
    ctx = _Ctx(); ctx.state.data["cg_t0"] = 0.0
    S = (13056 + 30, 5632 + 30)
    # 15x15 room centred on S: x0=S[0]-7 to S[0]+8, y0=S[1]-7 to S[1]+8
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid(room=(S[0] - 7, S[1] - 7, 15, 15)))
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*S))
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_obj(g.OBJ_BARRIER, "Barrier", S[0], S[1] - 8)])
    st = cg.prep_state(ctx, cg.DEFAULT_CONFIG)
    assert st["outward"] == (0, 1) and st["barrier"] == (S[0], S[1] - 8) and st["step"] == 0 and st["sub"] == "to_node" and st["start_centre"] == S
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: pytest.fail("grid re-read"))
    assert cg.prep_state(ctx, cg.DEFAULT_CONFIG) is st


def test_attacker_and_gather_ladder_prayer_then_attack(monkeypatch):
    me = (100, 100)
    npcs = [{"id": 9045, "index": 3, "name": "Corrupted Wolf", "instX": 102, "instY": 100, "interactingPlayer": True, "screenX": 5, "screenY": 5},
            {"id": 9040, "index": 4, "name": "Corrupted Rat", "instX": 101, "instY": 100, "interactingPlayer": False, "screenX": 5, "screenY": 5}]
    assert cg.attacker(npcs, me)["id"] == 9045
    assert cg.attacker([], me) is None
    calls = []
    ctx = _Ctx()
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*me))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: npcs)
    monkeypatch.setattr(controller, "get_prayers", lambda: {"active": {}, "tabOpen": True, "icons": {"PROTECT_FROM_MELEE": {"screenX": 1, "screenY": 1}}})
    monkeypatch.setattr(controller, "get_combat", lambda: {"targetId": None})
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    monkeypatch.setattr(controller, "open_prayer_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append(("click", entity.get("name") or "icon")) or True)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=1.0: True)
    assert cg.gather_ladder(ctx, cg.DEFAULT_CONFIG) == "prayer"
    monkeypatch.setattr(controller, "get_prayers", lambda: {"active": {"PROTECT_FROM_MELEE": True}, "tabOpen": True, "icons": {}})
    monkeypatch.setattr(controller, "npc_index_refresh_target", lambda index: (lambda: None))
    assert cg.gather_ladder(ctx, cg.DEFAULT_CONFIG) == "attack"
    assert ("click", "Corrupted Wolf") in calls


def test_gather_ladder_flees_a_demi_boss(monkeypatch):
    ctx = _Ctx(); ctx.state.data["cg_prep"] = {"prev_centre": (90, 90)}
    walks = []
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [{"id": 9046, "index": 1, "name": "Corrupted Bear", "instX": 102, "instY": 100, "interactingPlayer": True}])
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: walks.append((x, y)) or True)
    assert cg.gather_ladder(ctx, cg.DEFAULT_CONFIG) == "flee_demi"
    assert walks == [(90, 90)]


def test_gather_node_loops_until_depleted(monkeypatch):
    ctx = _Ctx()
    deposit = next(n for n in g.RESOURCE_NODES.values() if "Deposit" in n)
    dep_id = next(i for i, n in g.RESOURCE_NODES.items() if n == deposit)
    depleted_id = next(i for i, n in g.RESOURCE_NODES_DEPLETED.items() if "Deposit" in n)
    node = _obj(dep_id, deposit, 100, 100)
    ore = {"n": 0}
    reads = {"k": 0}
    def objects(radius=None, fresh=False, interactable=False):
        reads["k"] += 1
        return [_obj(depleted_id if ore["n"] >= 3 else dep_id, deposit, 100, 100)]
    monkeypatch.setattr(controller, "get_objects", objects)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"slot": 0, "name": g.ITEM_ORE, "qty": ore["n"]}] if ore["n"] else [])
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(101, 100))
    def click(ser, entity, **kw):
        assert kw.get("expected_action") == "Mine"
        ore["n"] += 1
        return True
    monkeypatch.setattr(controller, "click_entity", click)
    monkeypatch.setattr(controller, "object_refresh_target", lambda *a, **kw: (lambda: None), raising=False)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=1.0: check())
    monkeypatch.setattr(cg, "_wait_tick", lambda ctx: None)
    assert cg.gather_node(ctx, node, cg.DEFAULT_CONFIG)
    assert ore["n"] == 3


def test_light_node_success_clears_the_cached_instance_map(monkeypatch):
    """I5: lighting a node opens up scene (LOADING/LOGGED_IN ~1.2s later, per the recording), so a
    successful light must drop the cached collision grid -- a stale cg_imap would keep answering
    maze.edge_open/component questions against the pre-light scene."""
    ctx = _Ctx()
    ctx.state.data["cg_imap"] = object()
    node = _obj(35999, "Node", 100, 100)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: True)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=None: True)
    assert cg.light_node(ctx, node, cg.DEFAULT_CONFIG)
    assert "cg_imap" not in ctx.state.data


def test_light_node_failure_keeps_the_cached_instance_map(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["cg_imap"] = object()
    node = _obj(35999, "Node", 100, 100)
    monkeypatch.setattr(controller, "click_menu_action", lambda ser, entity, option, **kw: True)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=None: False)
    assert not cg.light_node(ctx, node, cg.DEFAULT_CONFIG)
    assert "cg_imap" in ctx.state.data


def test_teleport_home_via_crystal_clears_the_cached_instance_map(monkeypatch):
    ctx = _Ctx()
    ctx.state.data["cg_imap"] = object()
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": g.ITEM_TELEPORT, "screenX": 1, "screenY": 1}])
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [_obj(g.OBJ_SINGING_BOWL, "Singing Bowl", 1, 1)])
    assert cg.teleport_home(ctx, cg.DEFAULT_CONFIG)
    assert "cg_imap" not in ctx.state.data


def test_to_node_wedge_advances_to_to_room_when_already_close_enough(monkeypatch):
    """I5: when no maze node can be found between here and the target AND the static map's
    edge_open call disagrees with reality (both false), a player who is ALREADY within
    ROOM_ARRIVAL_TOLERANCE of the target must fall through to to_room instead of re-issuing the
    same walk_to forever."""
    ctx = _Ctx(); data = ctx.state.data; data["cg_t0"] = 0.0
    S = (13056 + 30, 5632 + 30)
    pitch = cg.DEFAULT_CONFIG["room_pitch"]
    st = {"start_centre": S, "barrier": (S[0], S[1] - 8), "outward": (0, 1), "pitch": pitch,
          "step": 0, "sub": "to_node", "revealed": set(), "entered": {(0, 0)}, "node_fails": 0,
          "prev_centre": S, "fishing_spots": set()}
    data["cg_prep"] = st
    target = cg._centre(st, (1, 0))
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid())
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*target))     # already AT the target
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    monkeypatch.setattr(cg.maze, "edge_open", lambda imap, a, b: False)   # the map disagrees
    monkeypatch.setattr(cg, "gather_ladder", lambda ctx, cfg: "none")
    monkeypatch.setattr(cg, "pick_up_drops", lambda ctx, cfg: 0)
    walks = []
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: walks.append((x, y)) or True)
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)
    # Fell through to_node -> to_room -> gather -> (nothing to gather, no reveal) -> advance, all
    # in this one call, instead of re-issuing the identical walk_to(target) forever.
    assert walks == []
    assert st["step"] == 1


def test_explore_gather_phase_lights_then_enters_then_gathers(monkeypatch):
    ctx = _Ctx(); data = ctx.state.data; data["cg_t0"] = 0.0
    S = (13056 + 30, 5632 + 30)
    pitch = cg.DEFAULT_CONFIG["room_pitch"]
    node_xy = (S[0] + pitch // 2, S[1])                  # between S and the room to the right (outward north)
    right_room = (S[0] + pitch, S[1])
    state = {"pos": S, "lit": False, "walks": [], "gathered": []}
    # 15x15 room centred on S
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid(room=(S[0] - 7, S[1] - 7, 15, 15)))
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*state["pos"]))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_ground_items", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    linum = next(n for n in g.RESOURCE_NODES.values() if "Linum" in n)
    linum_id = next(i for i, n in g.RESOURCE_NODES.items() if n == linum)
    def objects(radius=None, fresh=False, interactable=False):
        return [_obj(g.OBJ_BARRIER, "Barrier", S[0], S[1] - 8), _obj(35999, "Node", *node_xy),
                _obj(linum_id, linum, *right_room)]
    monkeypatch.setattr(controller, "get_objects", objects)
    monkeypatch.setattr(cg.maze, "edge_open", lambda imap, a, b: state["lit"])
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: (state["walks"].append((x, y)), state.__setitem__("pos", (x, y)))[1] or True)
    monkeypatch.setattr(cg, "light_node", lambda ctx, node, cfg: state.__setitem__("lit", True) or True)
    monkeypatch.setattr(cg, "gather_node", lambda ctx, obj, cfg: state["gathered"].append(obj["name"]) or True)
    monkeypatch.setattr(cg, "gather_ladder", lambda ctx, cfg: "none")
    monkeypatch.setattr(cg, "pick_up_drops", lambda ctx, cfg: 0)
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)            # walk to the node
    assert state["walks"] and h.cheb(state["walks"][-1], node_xy) <= 2
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)            # light it
    assert state["lit"] and data["cg_prep"]["sub"] == "to_room"
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)            # walk into the room
    assert state["walks"][-1] == right_room
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)            # gather the linum (deficit: yes)
    assert state["gathered"] == [linum]


def test_gather_caps_raw_fish_want_by_free_inventory_slots(monkeypatch):
    """C3a: raw fish are not stackable -- with only 1 free inventory slot (minus the 2-slot drop
    reserve, i.e. no room at all), the fishing node must be excluded from the gather candidates
    even though the kit's fish deficit is large; an ore node with room in its deficit is still
    picked."""
    ctx = _Ctx(); data = ctx.state.data; data["cg_t0"] = 0.0
    S = (13056 + 30, 5632 + 30)
    pitch = cg.DEFAULT_CONFIG["room_pitch"]
    st = {"start_centre": S, "barrier": (S[0], S[1] - 8), "outward": (0, 1), "pitch": pitch,
          "step": 0, "sub": "gather", "revealed": set(), "entered": {(0, 0), (1, 0)}, "node_fails": 0,
          "prev_centre": S, "fishing_spots": set()}
    data["cg_prep"] = st
    target = cg._centre(st, (1, 0))
    fish_id = next(i for i, n in g.RESOURCE_NODES.items() if "Fishing" in n)
    fish_name = g.RESOURCE_NODES[fish_id]
    ore_id = next(i for i, n in g.RESOURCE_NODES.items() if "Deposit" in n)
    ore_name = g.RESOURCE_NODES[ore_id]
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid())
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*target))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_ground_items", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": f"Junk{i}", "qty": 1} for i in range(27)])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [_obj(fish_id, fish_name, target[0], target[1]),
                         _obj(ore_id, ore_name, target[0] + 1, target[1])])
    gathered = []
    monkeypatch.setattr(cg, "gather_node", lambda ctx, obj, cfg: gathered.append(obj["name"]) or True)
    monkeypatch.setattr(cg, "gather_ladder", lambda ctx, cfg: "none")
    monkeypatch.setattr(cg, "pick_up_drops", lambda ctx, cfg: 0)
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)
    assert gathered == [ore_name]


def test_gather_node_failure_memory_skips_a_tile_after_max_fails(monkeypatch):
    """C3b: a resource node whose gather_node keeps returning False is excluded from the
    candidate list after NODE_GATHER_MAX_FAILS failures, tracked per-tile in cg_node_fails."""
    ctx = _Ctx(); data = ctx.state.data; data["cg_t0"] = 0.0
    S = (13056 + 30, 5632 + 30)
    pitch = cg.DEFAULT_CONFIG["room_pitch"]
    st = {"start_centre": S, "barrier": (S[0], S[1] - 8), "outward": (0, 1), "pitch": pitch,
          "step": 0, "sub": "gather", "revealed": set(), "entered": {(0, 0), (1, 0)}, "node_fails": 0,
          "prev_centre": S, "fishing_spots": set()}
    data["cg_prep"] = st
    target = cg._centre(st, (1, 0))
    ore_id = next(i for i, n in g.RESOURCE_NODES.items() if "Deposit" in n)
    ore_name = g.RESOURCE_NODES[ore_id]
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid())
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*target))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_ground_items", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [_obj(ore_id, ore_name, target[0], target[1])])
    attempts = []
    monkeypatch.setattr(cg, "gather_node", lambda ctx, obj, cfg: attempts.append(obj["name"]) or False)
    monkeypatch.setattr(cg, "gather_ladder", lambda ctx, cfg: "none")
    monkeypatch.setattr(cg, "pick_up_drops", lambda ctx, cfg: 0)
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(attempts) == cg.NODE_GATHER_MAX_FAILS
    cg.explore_gather_phase(ctx, cg.DEFAULT_CONFIG)   # third call: tile excluded, no new attempt
    assert len(attempts) == cg.NODE_GATHER_MAX_FAILS


def test_craft_phase_fish_pass_walks_to_recorded_spot_when_short(monkeypatch):
    """C3c: after cook has run (no raw fish left), short of the fish target, with time left and a
    recorded fishing spot -- craft_phase must walk to the spot instead of falling through to the
    normal craft plan."""
    ctx = _Ctx(); data = ctx.state.data
    data.update({"cg_t0": 0.0, "cg_home": True})
    spot = (13100, 5700)
    data["cg_prep"] = {"fishing_spots": {spot}}
    monkeypatch.setattr(cg.time, "time", lambda: 100.0)
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(13000, 5600))
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": g.ITEM_FISH, "qty": 12}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    walks = []
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: walks.append(((x, y), kw)) or True)
    monkeypatch.setattr(cg.kit, "craft_plan", lambda inv, eq, cfg: pytest.fail("must not fall through to the normal plan"))
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    assert len(walks) == 1
    (xy, kw) = walks[0]
    assert xy == spot and kw.get("goal_adjacent") is True


def test_craft_phase_fish_pass_skipped_when_fish_target_met(monkeypatch):
    """C3c: with the fish target already met, craft_phase must not enter the fish pass at all."""
    ctx = _Ctx(); data = ctx.state.data
    data.update({"cg_t0": 0.0, "cg_home": True})
    data["cg_prep"] = {"fishing_spots": {(13100, 5700)}}
    monkeypatch.setattr(cg.time, "time", lambda: 100.0)
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": g.ITEM_FISH, "qty": 20}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    walks = []
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: walks.append((x, y)) or True)
    monkeypatch.setattr(cg.kit, "craft_plan", lambda inv, eq, cfg: [])
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    assert walks == []
    assert data.get("cg_crafted") is True


# ---- craft phase tests ----

def test_bowl_make_clicks_the_widget_by_text(monkeypatch):
    ctx = _Ctx()
    calls = []
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_obj(g.OBJ_SINGING_BOWL, "Singing Bowl", 101, 100)])
    inv = {"n": 0}
    state = {"bowl_open": False}
    def get_inventory_mock():
        if inv["n"] == 0:
            return []
        elif inv["n"] == 1:
            return [{"slot": 0, "name": "Corrupted helm (basic)", "qty": 1}]
        else:  # inv["n"] == 2 or higher: inventory changes after click
            return []
    monkeypatch.setattr(controller, "get_inventory", get_inventory_mock)
    def click_entity_mock(ser, entity, **kw):
        name = entity.get("name") or entity.get("text")
        calls.append((name, kw.get("expected_action")))
        if kw.get("expected_action") == "Sing-crystal":
            state["bowl_open"] = True
        return True
    monkeypatch.setattr(controller, "click_entity", click_entity_mock)
    def widgets_dump(scan=False):
        if not state["bowl_open"]:
            return []
        return [
            {"group": 270, "child": 17, "text": "Corrupted helm", "screenX": 3, "screenY": 3, "actions": ["Make"]},
            {"group": 270, "child": 18, "text": "Corrupted body", "screenX": 4, "screenY": 3, "actions": ["Make"]},
            {"group": 162, "child": 1, "text": "Corrupted helm", "screenX": 9, "screenY": 9, "actions": []}]
    monkeypatch.setattr(controller, "get_widgets_dump", widgets_dump)
    calls_to_confirm = [0]
    def confirm(ctx, check, budget_s=1.0):
        calls_to_confirm[0] += 1
        if calls_to_confirm[0] == 1:
            inv["n"] = 1
        else:
            inv["n"] = 2  # Make inventory appear different
        return check()
    monkeypatch.setattr(cg, "_confirm", confirm)
    assert cg.bowl_make(ctx, "helm", cg.DEFAULT_CONFIG)
    assert calls[0] == ("Singing Bowl", "Sing-crystal")


def test_bowl_widget_matches_name_field_with_empty_text(monkeypatch):
    """I6: the substring match must consider `name` too, not just `text` -- a widget carrying
    the item's name in `name` with an empty `text` still matches."""
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": g.BOWL_GROUP, "child": 17, "name": "Corrupted helm", "text": "", "screenX": 3, "screenY": 3}])
    w = cg._bowl_widget("helm")
    assert w is not None and w["child"] == 17


def test_bowl_make_skips_the_sing_crystal_click_when_already_open(monkeypatch):
    """I6: the Singing Bowl interface stays open across Makes -- if the widget for this key is
    already visible, bowl_make must not re-click Sing-crystal (and re-trigger the walk/near
    check that click implies)."""
    ctx = _Ctx()
    calls = []
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False:
                        [_obj(g.OBJ_SINGING_BOWL, "Singing Bowl", 101, 100)])
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [
        {"group": g.BOWL_GROUP, "child": 18, "text": "Corrupted body", "screenX": 4, "screenY": 3}])
    inv = {"n": 0}
    monkeypatch.setattr(controller, "get_inventory", lambda: [] if inv["n"] == 0 else [{"name": "Corrupted body (basic)", "qty": 1}])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append(kw.get("expected_action")) or True)

    def confirm(ctx, check, budget_s=1.0):
        inv["n"] = 1
        return check()
    monkeypatch.setattr(cg, "_confirm", confirm)
    assert cg.bowl_make(ctx, "body", cg.DEFAULT_CONFIG)
    assert "Sing-crystal" not in calls


def test_craft_phase_runs_the_plan_and_marks_crafted(monkeypatch):
    ctx = _Ctx(); ctx.state.data.update({"cg_t0": 0.0, "cg_home": True})
    steps = []
    plans = [[("bowl", "helm"), ("cook", None)], [("cook", None)], []]
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    monkeypatch.setattr(cg.kit, "craft_plan", lambda inv, eq, cfg: plans.pop(0))
    monkeypatch.setattr(cg, "bowl_make", lambda ctx, key, cfg: steps.append(("bowl", key)) or True)
    monkeypatch.setattr(cg, "cook_all", lambda ctx, cfg: steps.append(("cook", None)) or True)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    assert steps == [("bowl", "helm"), ("cook", None)]
    assert ctx.state.data["cg_crafted"] is True


def test_use_on_clicks_both_items(monkeypatch):
    ctx = _Ctx()
    calls = []
    inv = [{"slot": 0, "name": g.ITEM_GRYM, "qty": 1, "screenX": 1, "screenY": 1},
           {"slot": 1, "name": g.ITEM_WATER_VIAL, "qty": 1, "screenX": 2, "screenY": 1}]
    monkeypatch.setattr(controller, "get_inventory", lambda: inv)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append((entity["name"], kw.get("expected_action"))) or True)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=1.0: True)
    assert cg.use_on(ctx, g.ITEM_GRYM, g.ITEM_WATER_VIAL)
    assert calls == [(g.ITEM_GRYM, "Use"), (g.ITEM_WATER_VIAL, None)]


def test_use_on_default_rule_still_confirms_on_name_a_falling(monkeypatch):
    """C2: use_on(confirm=None) must keep its old behaviour -- confirming stage 1 (grym leaf +
    water vial) on the grym leaf count falling, exercised with the real _confirm poll loop
    rather than a stubbed-True one."""
    ctx = _Ctx()
    reads = {"n": 0}

    def get_inventory_mock():
        reads["n"] += 1
        items = [{"slot": 1, "name": g.ITEM_WATER_VIAL, "qty": 1}]
        if reads["n"] < 4:
            items.append({"slot": 0, "name": g.ITEM_GRYM, "qty": 1})
        return items
    monkeypatch.setattr(controller, "get_inventory", get_inventory_mock)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    assert cg.use_on(ctx, g.ITEM_GRYM, g.ITEM_WATER_VIAL)


def test_pestle_confirms_on_dust_rising(monkeypatch):
    """C2: the pestle is a tool -- it is never consumed, so use_on's default rule (count(name_a)
    falling) can never confirm a pestle click. _pestle must instead watch Corrupted dust rising
    (via its own confirm= predicate)."""
    ctx = _Ctx()
    reads = {"n": 0}

    def get_inventory_mock():
        reads["n"] += 1
        items = [{"slot": 0, "name": g.ITEM_PESTLE, "qty": 1}, {"slot": 1, "name": g.ITEM_SHARDS, "qty": 590}]
        if reads["n"] >= 5:      # dust rises on the second _confirm poll
            items.append({"slot": 2, "name": g.ITEM_DUST, "qty": 10})
        return items
    monkeypatch.setattr(controller, "get_inventory", get_inventory_mock)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    assert cg._pestle(ctx)


def test_pestle_confirms_on_shards_falling_when_dust_lags(monkeypatch):
    """The fallback half of _pestle's confirm predicate: shards falling by DUST_SHARDS also
    counts, in case a stacked dust item lags one HTTP read behind."""
    ctx = _Ctx()
    reads = {"n": 0}

    def get_inventory_mock():
        reads["n"] += 1
        shards = 580 if reads["n"] >= 4 else 590
        return [{"slot": 0, "name": g.ITEM_PESTLE, "qty": 1}, {"slot": 1, "name": g.ITEM_SHARDS, "qty": shards}]
    monkeypatch.setattr(controller, "get_inventory", get_inventory_mock)
    monkeypatch.setattr(controller, "open_inventory_tab", lambda ser: True)
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: True)
    assert cg._pestle(ctx)


def test_upgrade_phase_walks_fights_loots_and_crafts(monkeypatch):
    ctx = _Ctx(); data = ctx.state.data
    data.update({"cg_t0": 0.0, "cg_demi_bosses": {(0, 2): 9047}})
    S = (13056 + 30, 5632 + 30); pitch = cg.DEFAULT_CONFIG["room_pitch"]
    data["cg_prep"] = {"start_centre": S, "barrier": (S[0], S[1] - 8), "outward": (0, 1), "pitch": pitch,
                       "step": 10, "sub": "to_node", "revealed": {(0, 2)}, "entered": set(), "node_fails": 0, "prev_centre": S}
    state = {"pos": S, "dragon": True, "walks": [], "t": 100.0}
    monkeypatch.setattr(cg.time, "time", lambda: state["t"])
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid())
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*state["pos"]))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [{"id": 9047, "index": 9, "name": "Corrupted Dragon", "instX": S[0], "instY": S[1] + 2 * pitch, "interactingPlayer": True, "screenX": 5, "screenY": 5}] if state["dragon"] else [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (attuned)"}})
    monkeypatch.setattr(cg.maze, "edge_open", lambda imap, a, b: True)
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: (state["walks"].append((x, y)), state.__setitem__("pos", (x, y)))[1] or True)
    monkeypatch.setattr(cg, "fight_npc_step", lambda ctx, npc, cfg: state.__setitem__("dragon", False) or "attack")
    monkeypatch.setattr(cg, "pick_up_drops", lambda ctx, cfg: 1)
    monkeypatch.setattr(cg, "teleport_home", lambda ctx, cfg: True)
    monkeypatch.setattr(cg, "bowl_make", lambda ctx, key, cfg: True)
    for _ in range(10):
        cg.upgrade_phase(ctx, cg.DEFAULT_CONFIG)
    assert state["walks"][0] == (S[0], S[1] + pitch)          # the middle-ring room below the dragon's
    assert state["walks"][1] == (S[0], S[1] + 2 * pitch)      # then into the dragon's room
    assert data.get("cg_upgrade_done") is True


def test_upgrade_phase_craft_retries_until_limit(monkeypatch):
    ctx = _Ctx(); data = ctx.state.data
    data.update({"cg_t0": 0.0, "cg_demi_bosses": {(0, 2): 9047}})
    S = (13056 + 30, 5632 + 30); pitch = cg.DEFAULT_CONFIG["room_pitch"]
    data["cg_prep"] = {"start_centre": S, "barrier": (S[0], S[1] - 8), "outward": (0, 1), "pitch": pitch,
                       "step": 10, "sub": "to_node", "revealed": {(0, 2)}, "entered": set(), "node_fails": 0, "prev_centre": S}
    state = {"pos": S, "dragon": True, "walks": [], "t": 100.0}
    monkeypatch.setattr(cg.time, "time", lambda: state["t"])
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid())
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*state["pos"]))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [{"id": 9047, "index": 9, "name": "Corrupted Dragon", "instX": S[0], "instY": S[1] + 2 * pitch, "interactingPlayer": True, "screenX": 5, "screenY": 5}] if state["dragon"] else [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (attuned)"}})
    monkeypatch.setattr(cg.maze, "edge_open", lambda imap, a, b: True)
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: (state["walks"].append((x, y)), state.__setitem__("pos", (x, y)))[1] or True)
    monkeypatch.setattr(cg, "fight_npc_step", lambda ctx, npc, cfg: state.__setitem__("dragon", False) or "attack")
    monkeypatch.setattr(cg, "pick_up_drops", lambda ctx, cfg: 1)
    monkeypatch.setattr(cg, "teleport_home", lambda ctx, cfg: True)
    monkeypatch.setattr(cg, "bowl_make", lambda ctx, key, cfg: False)  # Fail all bowl_make calls
    for _ in range(10):
        cg.upgrade_phase(ctx, cg.DEFAULT_CONFIG)
    # After 3 craft attempts, flag should be set and craft_fails should be 3
    assert data.get("cg_upgrade_done") is True
    assert data.get("cg_upgrade", {}).get("craft_fails") == 3


def test_upgrade_phase_craft_succeeds_on_retry(monkeypatch):
    ctx = _Ctx(); data = ctx.state.data
    data.update({"cg_t0": 0.0, "cg_demi_bosses": {(0, 2): 9047}})
    S = (13056 + 30, 5632 + 30); pitch = cg.DEFAULT_CONFIG["room_pitch"]
    data["cg_prep"] = {"start_centre": S, "barrier": (S[0], S[1] - 8), "outward": (0, 1), "pitch": pitch,
                       "step": 10, "sub": "to_node", "revealed": {(0, 2)}, "entered": set(), "node_fails": 0, "prev_centre": S}
    state = {"pos": S, "dragon": True, "walks": [], "t": 100.0, "bowl_attempt": 0}
    monkeypatch.setattr(cg.time, "time", lambda: state["t"])
    monkeypatch.setattr(controller, "get_collision_grid", lambda plane=None: _grid())
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(*state["pos"]))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [{"id": 9047, "index": 9, "name": "Corrupted Dragon", "instX": S[0], "instY": S[1] + 2 * pitch, "interactingPlayer": True, "screenX": 5, "screenY": 5}] if state["dragon"] else [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [])
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": "Corrupted bow (attuned)"}})
    monkeypatch.setattr(cg.maze, "edge_open", lambda imap, a, b: True)
    monkeypatch.setattr(cg.instance_nav, "walk_to", lambda ctx, x, y, **kw: (state["walks"].append((x, y)), state.__setitem__("pos", (x, y)))[1] or True)
    monkeypatch.setattr(cg, "fight_npc_step", lambda ctx, npc, cfg: state.__setitem__("dragon", False) or "attack")
    monkeypatch.setattr(cg, "pick_up_drops", lambda ctx, cfg: 1)
    monkeypatch.setattr(cg, "teleport_home", lambda ctx, cfg: True)
    def bowl_make_then_succeed(ctx, key, cfg):
        state["bowl_attempt"] += 1
        return state["bowl_attempt"] > 1  # Fail first time, succeed second time
    monkeypatch.setattr(cg, "bowl_make", bowl_make_then_succeed)
    for _ in range(10):
        cg.upgrade_phase(ctx, cg.DEFAULT_CONFIG)
    # After second craft attempt succeeds, flag should be set and craft_fails should be 1
    assert data.get("cg_upgrade_done") is True
    assert data.get("cg_upgrade", {}).get("craft_fails") == 1


# ---- craft phase fix: _potion and failure tracking ----

def test_potion_breaks_on_use_on_failure(monkeypatch):
    ctx = _Ctx()
    use_on_calls = [0]
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"slot": 0, "name": g.ITEM_GRYM, "qty": 1}, {"slot": 1, "name": g.ITEM_WATER_VIAL, "qty": 1}])
    def fake_use_on(ctx, name_a, name_b):
        use_on_calls[0] += 1
        return False  # Simulate failure (missed/withheld click)
    monkeypatch.setattr(cg, "use_on", fake_use_on)
    result = cg._potion(ctx)
    assert result is False
    assert use_on_calls[0] == 1  # Should stop after first failure


def test_craft_phase_tracks_failures_per_op(monkeypatch):
    ctx = _Ctx(); ctx.state.data.update({"cg_t0": 0.0, "cg_home": True})
    steps = []
    # Plan: always return one cook step
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    monkeypatch.setattr(cg.kit, "craft_plan", lambda inv, eq, cfg: [("cook", None)])
    monkeypatch.setattr(cg, "cook_all", lambda ctx, cfg: False)  # Always fail
    # Call craft_phase three times
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    # After 3 failures of cook, should be marked as crafted
    assert ctx.state.data.get("cg_crafted") is True
    # Failure dict should track "cook": 3
    assert ctx.state.data.get("cg_craft_fails", {}).get("cook") == 3


def test_craft_phase_tracks_different_ops_separately(monkeypatch):
    ctx = _Ctx(); ctx.state.data.update({"cg_t0": 0.0, "cg_home": True})
    plans = [[("cook", None)], [("wear", "body")], [("cook", None)]]
    monkeypatch.setattr(controller, "get_inventory", lambda: [])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    monkeypatch.setattr(cg.kit, "craft_plan", lambda inv, eq, cfg: plans.pop(0) if plans else [])
    monkeypatch.setattr(cg, "cook_all", lambda ctx, cfg: False)  # cook always fails
    monkeypatch.setattr(cg, "wear", lambda ctx, name: False)     # wear always fails
    # Three calls with alternating ops: cook, wear, cook
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    cg.craft_phase(ctx, cg.DEFAULT_CONFIG)
    # Failure dict should have cook: 2, wear: 1 (not yet 3 for any one)
    fails = ctx.state.data.get("cg_craft_fails", {})
    assert fails.get("cook") == 2
    assert fails.get("wear") == 1
    assert ctx.state.data.get("cg_crafted") is None or ctx.state.data.get("cg_crafted") is False


# ---- craft phase fix round 2: _potion return logic ----

def test_potion_returns_false_on_stage2_use_on_failure(monkeypatch):
    ctx = _Ctx()
    use_on_calls = [0]
    # Inventory: no stage 1 (no water vial/grym), but has stage 2 items
    monkeypatch.setattr(controller, "get_inventory", lambda: [
        {"slot": 0, "name": g.ITEM_GRYM_UNF, "qty": 1},
        {"slot": 1, "name": g.ITEM_DUST, "qty": 10}
    ])
    def fake_use_on(ctx, name_a, name_b, **kw):
        use_on_calls[0] += 1
        return False  # Failure on stage 2
    monkeypatch.setattr(cg, "use_on", fake_use_on)
    result = cg._potion(ctx)
    assert result is False  # Failed stage 2 should return False
    assert use_on_calls[0] == 1  # Only one use_on call (stage 2)


def test_potion_returns_true_on_successful_egniol_increase(monkeypatch):
    ctx = _Ctx()
    inv_state = {"call": 0}
    def get_inventory_mock():
        inv_state["call"] += 1
        # First call (before): no doses
        if inv_state["call"] == 1:
            return [{"slot": 0, "name": g.ITEM_GRYM_UNF, "qty": 1}, {"slot": 1, "name": g.ITEM_DUST, "qty": 20}]
        # Later calls: doses have been created
        if inv_state["call"] >= 5:
            return [{"slot": 0, "name": g.EGNIOL_DOSES[0], "qty": 1}]
        return [{"slot": 0, "name": g.ITEM_GRYM_UNF, "qty": 1}, {"slot": 1, "name": g.ITEM_DUST, "qty": 20}]
    monkeypatch.setattr(controller, "get_inventory", get_inventory_mock)
    monkeypatch.setattr(cg, "use_on", lambda ctx, name_a, name_b, **kw: True)  # Success
    result = cg._potion(ctx)
    assert result is True  # Egniol dose count rose


def test_tick_dispatches_prep_phases(monkeypatch):
    ctx = _Ctx(); ctx.state.data["cg_t0"] = 0.0
    monkeypatch.setattr(cg.time, "time", lambda: 10.0)
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [{"id": g.OBJ_BARRIER}])
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": g.ITEM_AXE}, {"name": g.ITEM_PICKAXE}, {"name": g.ITEM_HARPOON}, {"name": g.ITEM_PESTLE}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {"weapon": {"name": g.ITEM_SCEPTRE}})
    called = []
    monkeypatch.setattr(cg, "explore_gather_phase", lambda ctx, cfg: called.append("explore"))
    cg.tick(ctx)
    assert called == ["explore"] and ctx.state.data["cg_phase"] == "explore_gather"


def test_ensure_tools_takes_from_storage(monkeypatch):
    ctx = _Ctx()
    calls = []
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"slot": 0, "name": g.ITEM_SCEPTRE, "qty": 1}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [_obj(g.OBJ_TOOL_STORAGE, "Tool Storage", 101, 100)])
    monkeypatch.setattr(controller, "click_entity", lambda ser, entity, **kw: calls.append(kw.get("expected_action")) or True)
    monkeypatch.setattr(cg, "_confirm", lambda ctx, check, budget_s=1.0: True)
    assert cg.ensure_tools(ctx, cg.DEFAULT_CONFIG)
    assert calls == ["Take"]


def test_stuck_watch_stops_after_no_progress(monkeypatch):
    ctx = _Ctx(); data = ctx.state.data
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0)
    cg.stuck_watch(ctx, ("explore_gather", (100, 100), 5))
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0 + cg.STUCK_STOP_S + 1)
    cg.stuck_watch(ctx, ("explore_gather", (100, 100), 5))
    assert ctx.state.safety_stopped
    ctx2 = _Ctx()
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0)
    cg.stuck_watch(ctx2, ("explore_gather", (100, 100), 5))
    monkeypatch.setattr(cg.time, "time", lambda: 1000.0 + cg.STUCK_STOP_S + 1)
    cg.stuck_watch(ctx2, ("explore_gather", (101, 100), 5))     # moved: no stop
    assert not ctx2.state.safety_stopped


def test_stuck_watch_fires_even_when_ensure_tools_fails(monkeypatch):
    """Ensure that when ensure_tools fails, stuck_watch still runs and can trigger a safety stop
    after STUCK_STOP_S with no progress. This prevents a tool loop from freezing forever."""
    ctx = _Ctx(); data = ctx.state.data
    data["cg_t0"] = 0.0
    # Set up time to start at 100.0
    time_value = [100.0]
    def mock_time():
        return time_value[0]
    monkeypatch.setattr(cg.time, "time", mock_time)
    # Stub all bridge calls
    monkeypatch.setattr(controller, "get_player", lambda: _player_at(100, 100))
    monkeypatch.setattr(controller, "get_npcs", lambda fresh=False: [])
    monkeypatch.setattr(controller, "get_objects", lambda radius=None, fresh=False, interactable=False: [{"id": g.OBJ_BARRIER}])
    monkeypatch.setattr(controller, "get_inventory", lambda: [{"name": g.ITEM_SCEPTRE}])
    monkeypatch.setattr(controller, "get_equipment", lambda: {})
    # Stub ensure_tools to always return False
    monkeypatch.setattr(cg, "ensure_tools", lambda ctx, cfg: False)
    # Call tick() - should set up stuck_watch
    cg.tick(ctx)
    assert ctx.state.safety_stopped is False
    # Advance time past STUCK_STOP_S
    time_value[0] = 100.0 + cg.STUCK_STOP_S + 1
    # Call tick() again with same state - should trigger safety stop
    cg.tick(ctx)
    assert ctx.state.safety_stopped is True
