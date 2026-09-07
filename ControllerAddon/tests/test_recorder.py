import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# recorder.py deliberately imports neither controller nor pyserial (see its header comment), so
# unlike test_mcp_debug_server.py there is nothing to stub before importing -- no socket is bound
# and no serial port is touched at import time.
import recorder as r  # noqa: E402


def _action(seq, t, **extra):
    record = {"kind": "action", "seq": seq, "t": t, "tick": t // 600, "option": "Mine",
              "targetText": "Sunstone rocks", "menuAction": "GAME_OBJECT_FIRST_OPTION"}
    record.update(extra)
    return record


def _outcome(seq, t, kind, cause, **extra):
    record = {"kind": kind, "seq": seq, "t": t, "tick": t // 600, "causedBy": cause}
    record.update(extra)
    return record


# ---------------------------------------------------------------- object transformation pairing

def test_pair_object_changes_collapses_despawn_and_spawn_on_same_tile():
    despawn = {"kind": "objdespawn", "x": 1444, "y": 3169, "plane": 0,
               "objectId": 51487, "objectName": "Sunstone rocks"}
    spawn = {"kind": "objspawn", "x": 1444, "y": 3169, "plane": 0,
             "objectId": 51488, "objectName": "Depleted rock"}

    paired, consumed = r.pair_object_changes([despawn, spawn])

    assert paired == [(despawn, spawn)]
    assert id(despawn) in consumed and id(spawn) in consumed


def test_pair_object_changes_leaves_different_tiles_unpaired():
    despawn = {"kind": "objdespawn", "x": 1444, "y": 3169, "plane": 0, "objectId": 51487}
    spawn = {"kind": "objspawn", "x": 1450, "y": 3180, "plane": 0, "objectId": 51488}

    paired, consumed = r.pair_object_changes([despawn, spawn])

    assert paired == []
    assert consumed == set()


def test_pair_object_changes_ignores_plane_mismatch():
    """Same x/y on a different plane is a different tile -- pairing them would invent a
    transformation that never happened (e.g. an upstairs object swapping while a downstairs one
    depletes)."""
    despawn = {"kind": "objdespawn", "x": 1444, "y": 3169, "plane": 0, "objectId": 51487}
    spawn = {"kind": "objspawn", "x": 1444, "y": 3169, "plane": 1, "objectId": 51488}

    paired, _ = r.pair_object_changes([despawn, spawn])

    assert paired == []


# ---------------------------------------------------------------- render delay / stream splitting

def test_split_renderable_holds_back_an_action_inside_the_outcome_window():
    """An action must not render until its outcomes can no longer arrive, or its block would be
    written half-empty."""
    action = _action(1, 10000)
    pending = [action]

    ready, waiting = r.split_renderable(pending, newest_t=10000 + r.RENDER_DELAY_MS - 1)

    assert ready == []
    assert waiting == [action]


def test_split_renderable_releases_action_with_its_outcomes_once_delay_passes():
    action = _action(1, 1000)
    # Outcome timestamp is deliberately NEWER than the cutoff -- it must still be pulled along with
    # its action rather than being left behind and orphaned.
    late_outcome = _outcome(2, 3900, "xp", cause=1, skill="Mining", gained=65)
    pending = [action, late_outcome]

    ready, waiting = r.split_renderable(pending, newest_t=1000 + r.RENDER_DELAY_MS)

    assert ready == [action, late_outcome]
    assert waiting == []


def test_split_renderable_keeps_outcomes_of_a_not_yet_ready_action():
    action = _action(1, 10000)
    outcome = _outcome(2, 10100, "xp", cause=1, skill="Mining", gained=65)

    ready, waiting = r.split_renderable([action, outcome], newest_t=10200)

    assert ready == []
    assert waiting == [action, outcome]


def test_split_renderable_releases_old_loose_records_immediately():
    chat = {"kind": "chat", "seq": 5, "t": 100, "tick": 0, "message": "hello"}

    ready, waiting = r.split_renderable([chat], newest_t=100 + r.RENDER_DELAY_MS)

    assert ready == [chat]
    assert waiting == []


# ---------------------------------------------------------------- narrative assembly

def test_build_narrative_nests_outcomes_under_their_action_and_never_duplicates_them():
    records = [
        _action(1, 1000, targetKind="object", objectId=51487, objectName="Sunstone rocks",
                targetX=1444, targetY=3169, targetPlane=0, targetDistance=2,
                playerX=1442, playerY=3168, playerPlane=0, canvasX=612, canvasY=388),
        _outcome(2, 1600, "anim", cause=1, animation=6753, isPlayer=True),
        _outcome(3, 2800, "xp", cause=1, skill="Mining", gained=65, level=71),
    ]

    text = "\n".join(r.build_narrative(records))

    assert "CLICK  Mine Sunstone rocks" in text
    assert "Sunstone rocks (id 51487)" in text
    # Outcomes appear exactly once, indented under the action -- not a second time as loose lines.
    assert text.count("anim 6753") == 1
    assert text.count("xp Mining +65") == 1
    assert "    ->  +0.6s  anim 6753" in text
    assert "    ->  +1.8s  xp Mining +65" in text


def test_build_narrative_flags_an_action_with_no_observed_outcome():
    """A click that produced nothing is the single most important thing for a routine author to
    see -- it means the action needs a precondition or a retry, not a blind fire-and-forget."""
    records = [_action(1, 1000, targetKind="object", objectId=51487, objectName="Sunstone rocks")]

    text = "\n".join(r.build_narrative(records))

    assert "nothing observed" in text


def test_build_narrative_renders_object_transformation_as_a_became_line():
    records = [
        _action(1, 1000, targetKind="object", objectId=51487, objectName="Sunstone rocks"),
        _outcome(2, 1600, "objdespawn", cause=1, x=1444, y=3169, plane=0,
                 objectId=51487, objectName="Sunstone rocks"),
        _outcome(3, 1600, "objspawn", cause=1, x=1444, y=3169, plane=0,
                 objectId=51488, objectName="Depleted rock"),
    ]

    text = "\n".join(r.build_narrative(records))

    assert "became Depleted rock (51488)" in text
    assert "[was Sunstone rocks (51487)]" in text
    # The paired records must not ALSO appear as separate appeared/vanished lines.
    assert "object appeared" not in text
    assert "object vanished" not in text


def test_build_narrative_summarises_mouse_travel_before_the_click():
    records = [
        {"kind": "mouse", "seq": 1, "t": 500, "tick": 0,
         "points": [[10, 10, 500], [20, 20, 520]], "count": 2},
        {"kind": "mouse", "seq": 2, "t": 800, "tick": 1,
         "points": [[30, 30, 800]], "count": 1},
        _action(3, 900, targetKind="object", objectId=51487, objectName="Sunstone rocks",
                canvasX=30, canvasY=30),
    ]

    text = "\n".join(r.build_narrative(records))

    assert "mouse: 3 pts over 300ms" in text


def test_build_narrative_warns_when_the_tick_stamp_is_stale():
    """tickAge exists so a mirrored (and therefore possibly stale) tick cannot silently mislead --
    see RecorderProvider.base. A large age must be visible in the log."""
    records = [_action(1, 1000, targetKind="walk", tickAge=900)]

    text = "\n".join(r.build_narrative(records))

    assert "tick stamp is 900ms old" in text


def test_build_narrative_omits_the_stale_tick_warning_when_fresh():
    records = [_action(1, 1000, targetKind="walk", tickAge=120)]

    assert "tick stamp is" not in "\n".join(r.build_narrative(records))


def test_build_narrative_skips_tick_and_mouse_records_as_loose_lines():
    records = [
        {"kind": "tick", "seq": 1, "t": 100, "tick": 0, "x": 1442, "y": 3168, "plane": 0},
        {"kind": "mouse", "seq": 2, "t": 200, "tick": 0, "points": [[1, 2, 200]]},
    ]

    assert r.build_narrative(records) == []


# ---------------------------------------------------------------- action identity

def test_action_signature_distinguishes_targets_not_just_menu_text():
    take_a = _action(1, 0, option="Take", targetKind="grounditem",
                     groundItemId=27616, groundItemName="Sunstone")
    take_b = _action(2, 0, option="Take", targetKind="grounditem",
                     groundItemId=1618, groundItemName="Uncut diamond")

    assert r.action_signature(take_a) != r.action_signature(take_b)
    assert "Sunstone" in r.action_signature(take_a)


def test_action_signature_groups_repeats_of_the_same_interaction():
    first = _action(1, 0, option="Mine", targetKind="object",
                    objectId=51487, objectName="Sunstone rocks")
    second = _action(2, 5000, option="Mine", targetKind="object",
                     objectId=51487, objectName="Sunstone rocks")

    assert r.action_signature(first) == r.action_signature(second)


# ---------------------------------------------------------------- summary

def test_build_summary_accumulates_xp_and_net_inventory():
    records = [
        _action(1, 0, targetKind="object", objectId=51487, objectName="Sunstone rocks"),
        {"kind": "xp", "seq": 2, "t": 1000, "skill": "Mining", "gained": 65, "level": 71},
        {"kind": "xp", "seq": 3, "t": 2000, "skill": "Mining", "gained": 65, "level": 71},
        {"kind": "inv", "seq": 4, "t": 2000,
         "changes": [{"id": 27616, "name": "Sunstone", "delta": 1}]},
        {"kind": "inv", "seq": 5, "t": 3000,
         "changes": [{"id": 27616, "name": "Sunstone", "delta": 1}]},
    ]

    text = "\n".join(r.build_summary(records))

    assert "Mining   +130" in text
    assert "+2 Sunstone" in text
    assert "51487   Sunstone rocks" in text


def test_build_summary_reports_varbits_that_changed():
    records = [
        {"kind": "varbit", "seq": 1, "t": 100, "varbitId": 4104, "value": 1},
        {"kind": "varbit", "seq": 2, "t": 200, "varbitId": 4104, "value": 2},
    ]

    text = "\n".join(r.build_summary(records))

    assert "varbit 4104   changed 2x" in text


# -------------------------------------------- regressions from the first live recording (Falconry)

def test_fmt_tile_reads_camelcase_prefixed_fields():
    """The Java side emits targetX/targetY/targetPlane, not targetx/targety. Getting this wrong
    rendered every position in the first live recording as '?' while the capture was perfect."""
    record = {"targetX": 2389, "targetY": 3590, "targetPlane": 0}

    assert r.fmt_tile(record, "target") == "(2389,3590) p0"


def test_fmt_tile_reads_player_prefix_and_bare_fields():
    assert r.fmt_tile({"playerX": 2390, "playerY": 3589, "playerPlane": 0}, "player") == "(2390,3589) p0"
    assert r.fmt_tile({"x": 1, "y": 2, "plane": 3}) == "(1,2) p3"


def test_fmt_tile_still_reports_unknown_when_genuinely_absent():
    assert r.fmt_tile({}, "target") == "?"


def test_action_block_shows_resolved_positions():
    record = _action(1, 1000, targetKind="npc", npcId=5533, npcName="Dashing kebbit",
                     npcIndex=12323, targetX=2389, targetY=3590, targetPlane=0, targetDistance=1,
                     playerX=2390, playerY=3589, playerPlane=0)

    text = "\n".join(r.build_narrative([record]))

    assert "@ (2389,3590) p0" in text
    assert "from     (2390,3589) p0" in text
    assert "?" not in text.split("canvas")[0]


def test_varp_is_not_rendered_as_varbit_minus_one():
    """varbitId is -1 whenever the change was to a varp. Rendering those all as 'varbit -1'
    collapsed dozens of distinct variables into one meaningless line."""
    varp = {"kind": "varbit", "seq": 1, "t": 100, "varbitId": -1, "varpId": 570325, "value": 12}

    assert r.describe(varp) == "varp 570325 = 12"


def test_real_varbit_still_renders_as_varbit():
    varbit = {"kind": "varbit", "seq": 1, "t": 100, "varbitId": 14192, "varpId": 0, "value": 5}

    assert r.describe(varbit) == "varbit 14192 = 5"


def _per_tick_varp(ticks):
    """A varp changing on every single tick -- the noise pattern."""
    return [{"kind": "varbit", "seq": i, "t": i * 600, "tick": i,
             "varbitId": -1, "varpId": 3079, "value": i} for i in range(ticks)]


def test_high_frequency_varbit_is_suppressed_from_the_narrative():
    """Per-tick varp churn buried the meaningful flips in the first live recording."""
    lines = r.build_narrative(_per_tick_varp(100), {})

    assert 0 < len(lines) < 40


def test_varbit_suppression_persists_across_render_batches():
    """main() renders in batches as records arrive, so the running count must survive between
    calls -- otherwise every batch re-allows the same noise."""
    state = {}
    noise = _per_tick_varp(100)
    for start in range(0, 100, 10):
        last = r.build_narrative(noise[start:start + 10], state)

    assert last == []


def test_an_event_varbit_survives_alongside_per_tick_noise():
    """THE regression from the first live recording: varbit 14192 (falcon-has-a-catch) changed 14x
    over ~100 ticks and a flat count-12 cutoff wrongly hid it -- while the genuine noise changed
    ~100x. Judging by fraction-of-ticks separates them."""
    records = _per_tick_varp(100) + [
        {"kind": "varbit", "seq": 1000 + i, "t": i * 4000, "tick": i * 7,
         "varbitId": 14192, "varpId": 0, "value": 5} for i in range(14)]
    records.sort(key=lambda rec: rec["tick"])

    text = "\n".join(r.build_narrative(records, {}))

    assert text.count("varbit 14192") == 14


def test_low_frequency_varbit_is_never_suppressed():
    records = [{"kind": "varbit", "seq": i, "t": i * 600, "tick": i, "varbitId": 14192,
                "varpId": 0, "value": 5} for i in range(5)]

    assert len(r.build_narrative(records, {})) == 5


def test_summary_splits_meaningful_state_vars_from_noise():
    records = _per_tick_varp(100) + [
        {"kind": "varbit", "seq": 1000 + i, "t": i * 4000, "tick": i * 7,
         "varbitId": 14192, "varpId": 0, "value": 5} for i in range(14)]

    text = "\n".join(r.build_summary(records))
    meaningful, noise = text.split("high frequency")

    assert "varbit 14192" in meaningful
    assert "varp 3079" in noise


def test_noise_cutoff_scales_with_recording_length():
    assert r.noise_cutoff(0) == r.VARBIT_NOISE_MIN       # short recording: floor applies
    assert r.noise_cutoff(1000) > r.VARBIT_NOISE_MIN     # long recording: ratio takes over


def test_npc_transformation_is_reported():
    """The kebbit BECOMES the falcon at the same NPC index -- the central mechanic of the activity,
    and invisible in the first recording."""
    change = {"kind": "npcchange", "seq": 1, "t": 1000, "npcIndex": 12323,
              "fromId": 5533, "fromName": "Dashing kebbit",
              "npcId": 1343, "npcName": "Gyr Falcon", "x": 2389, "y": 3590, "plane": 0}

    assert "BECAME Gyr Falcon (1343)" in r.describe(change)
    assert "was Dashing kebbit (5533)" in r.describe(change)
    assert "idx 12323: Dashing kebbit (5533) -> Gyr Falcon (1343)" in "\n".join(r.build_summary([change]))


def test_walk_destination_is_rendered_from_its_resolved_record():
    """WALK params are canvas coords, so the tile arrives a tick later as a walkdest record."""
    resolved = {"kind": "walkdest", "seq": 2, "t": 1600, "causedBy": 1, "resolved": True,
                "x": 2395, "y": 3592, "plane": 0, "distance": 6}

    assert "walking to (2395,3592) p0" in r.describe(resolved)


def test_walk_header_omits_the_unknowable_tile_line():
    """WALK carries no resolvable target, so printing 'tile ?  dist None' reads as missing data
    when it is really a structural property of the menu event."""
    walk = _action(1, 1000, option="Walk here", targetKind="walk",
                   playerX=2390, playerY=3588, playerPlane=0)
    resolved = {"kind": "walkdest", "seq": 2, "t": 1100, "causedBy": 1, "resolved": True,
                "x": 2386, "y": 3591, "plane": 0, "distance": 4}

    text = "\n".join(r.build_narrative([walk, resolved]))

    assert "tile     ?" not in text
    assert "dist None" not in text
    assert "walking to (2386,3591) p0" in text


def test_unresolved_walk_is_called_out():
    unresolved = {"kind": "walkdest", "seq": 2, "t": 1600, "causedBy": 1, "resolved": False}

    assert "did not resolve" in r.describe(unresolved)


def _minimap_click(seq, t):
    """A minimap walk: MenuAction CANCEL, no target, zeroed params."""
    return {"kind": "action", "seq": seq, "t": t, "tick": t // 600, "option": "Cancel",
            "targetText": "", "menuAction": "CANCEL", "param0": 0, "param1": 0,
            "targetKind": "other", "playerX": 2604, "playerY": 3221, "playerPlane": 0}


def test_minimap_cancel_that_moved_the_player_is_labelled_as_travel():
    """CONFIRMED live: every long-distance leg of the bank run was a minimap click, which fires
    CANCEL with no target -- so the whole trip logged as meaningless 'Cancel'."""
    click = _minimap_click(1, 1000)
    dest = {"kind": "walkdest", "seq": 2, "t": 1600, "causedBy": 1, "resolved": True,
            "x": 2612, "y": 3221, "plane": 0, "distance": 8}

    text = "\n".join(r.build_narrative([click, dest]))

    assert "WALK (minimap)" in text
    assert "walking to (2612,3221) p0" in text


def test_cancel_that_moved_nothing_is_labelled_a_menu_dismissal():
    click = _minimap_click(1, 1000)
    dest = {"kind": "walkdest", "seq": 2, "t": 1600, "causedBy": 1, "resolved": False}

    text = "\n".join(r.build_narrative([click, dest]))

    assert "dismissed a menu" in text
    assert "WALK (minimap)" not in text


def test_action_signature_separates_minimap_travel_from_real_cancels():
    click = _minimap_click(1, 1000)

    assert r.action_signature(click, did_walk=True) == "Walk here (minimap)"
    assert r.action_signature(click, did_walk=False) == "Cancel (dismissed a menu)"


def test_summary_counts_a_minimap_walk_as_travel_not_as_cancel():
    records = [_minimap_click(1, 1000),
               {"kind": "walkdest", "seq": 2, "t": 1600, "causedBy": 1, "resolved": True,
                "x": 2612, "y": 3221, "region": 10290}]

    text = "\n".join(r.build_summary(records))

    assert "Walk here (minimap)" in text
    assert "Cancel" not in text


def test_summary_reports_the_route_and_region_path():
    """A travel routine is written from waypoints and leg order, which no per-action line carries."""
    records = [
        {"kind": "tick", "seq": 1, "t": 0, "tick": 0, "region": 9776},
        {"kind": "tick", "seq": 2, "t": 600, "tick": 1, "region": 9776},
        {"kind": "walkdest", "seq": 3, "t": 700, "causedBy": 99, "resolved": True,
         "x": 2612, "y": 3221, "region": 9776},
        {"kind": "tick", "seq": 4, "t": 1200, "tick": 2, "region": 10290},
        {"kind": "walkdest", "seq": 5, "t": 1300, "causedBy": 98, "resolved": True,
         "x": 2658, "y": 3230, "region": 10290},
    ]

    text = "\n".join(r.build_summary(records))

    assert "9776 -> 10290" in text
    assert "1. (2612,3221)   region 9776" in text
    assert "2. (2658,3230)   region 10290" in text


def test_inventory_change_shows_real_slot_usage():
    """CONFIRMED live: 11 non-stacking Bones occupy 11 slots but collapse to one entry when keyed
    by item id, so the old count reported 22 free while the game refused a pickup for lack of
    room. The slot figures must come from the container, and must be visible on every change."""
    record = {"kind": "inv", "seq": 1, "t": 1000, "usedSlots": 27, "freeSlots": 1,
              "changes": [{"id": 526, "name": "Bones", "delta": 1}]}

    assert "[27/28 used, 1 free]" in r.describe(record)


def test_widget_id_is_decoded_into_group_and_child():
    """A packed id like 25362456 is unusable; group 387 child 24 can be looked up. Values taken
    from the live bank run: the Ring of dueling teleport on the worn-equipment interface."""
    record = {"widgetId": 25362456, "widgetChild": -1}

    assert r.fmt_widget(record) == "widget 387.24  (packed 25362456)"


def test_widget_slot_is_shown_when_present():
    """Bank deposit: group 15 child 3 is the container, slot 4 is which item."""
    assert "widget 15.3 slot 4" in r.fmt_widget({"widgetId": 983043, "widgetChild": 4})


def test_widget_action_signature_uses_group_and_child():
    action = _action(1, 0, option="Deposit-All", targetKind="widget",
                     widgetId=983043, widgetChild=4, itemName="Dashing kebbit fur")

    assert r.action_signature(action) == "Deposit-All -> widget 15.3 [Dashing kebbit fur]"


def test_animation_end_is_labelled():
    assert "(ended)" in r.describe({"kind": "anim", "animation": -1, "isPlayer": True})
    assert "(ended)" not in r.describe({"kind": "anim", "animation": 5162, "isPlayer": True})


def test_mouse_span_uses_point_timestamps_not_batch_timestamps():
    """A single batch reported 'over 0ms' because the span was measured across batch records
    rather than the samples inside them."""
    batch = [{"kind": "mouse", "seq": 1, "t": 500,
              "points": [[10, 10, 500], [20, 20, 520], [30, 30, 540]]}]

    assert r.summarize_mouse(batch) == "   mouse: 3 pts over 40ms"


def test_build_summary_handles_an_empty_recording():
    """Ctrl+C immediately after arming must still produce a readable summary, not a traceback."""
    text = "\n".join(r.build_summary([]))

    assert "SUMMARY" in text
    assert "records           0" in text


def test_describe_renders_canvas_edge_and_focus_records():
    # New 2026-09-07 kinds for the telemetry metrics: the client sends the cursor leaving the
    # canvas (-1 in EVENT_MOUSE_MOVE) and every focus gain/loss (EVENT_APPLET_FOCUS).
    assert r.describe({"kind": "canvasexit", "x": 764, "y": 12}) == "cursor LEFT the canvas at (764, 12)"
    assert r.describe({"kind": "canvasenter", "x": 3, "y": 400}) == "cursor back on the canvas at (3, 400)"
    assert r.describe({"kind": "focus", "gained": True}) == "-- window FOCUSED --"
    assert r.describe({"kind": "focus", "gained": False}) == "-- window UNFOCUSED --"


def test_canvas_edge_and_focus_records_are_loose_narrative_kinds():
    for kind in ("canvasexit", "canvasenter", "focus"):
        assert kind in r.LOOSE_NARRATIVE_KINDS
