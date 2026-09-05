"""hunllef.py: pure Hunllef-fight logic. Spec: docs/superpowers/specs/2026-09-05-hunllef-fight-design.md."""
import hunllef as h
import gauntlet_ids as g

# The Corrupted Hunllef's animation sequence from recordings/2026-09-05_014024_corrupted-gauntlet-
# humanrun1 (ticks since varbit 9177 went to 1, animation id), and the human's protection-prayer
# clicks (tick, Prayer enum name). Copied here so the test is hermetic.
ANIMS = [(7.0, 8419), (11.9, 8419), (16.9, 8419), (22.0, 8419), (24.0, 8754), (27.0, 8419), (32.0, 8419), (37.0, 8419), (42.1, 8419), (44.0, 8755), (47.0, 8418), (52.0, 8419), (57.0, 8419), (62.0, 8419), (64.0, 8754), (67.0, 8419), (72.0, 8419), (77.0, 8419), (82.0, 8419), (84.0, 8755), (87.0, 8419), (92.0, 8419), (97.0, 8419), (102.0, 8419), (104.0, 8754), (107.0, 8418), (112.0, 8419), (117.0, 8419), (122.0, 8419), (124.0, 8755), (127.0, 8419), (132.0, 8419), (137.0, 8419), (142.0, 8419), (144.0, 8754), (147.0, 8419), (152.0, 8418), (157.0, 8419), (162.0, 8419), (164.0, 8755), (167.0, 8419), (172.0, 8419), (177.0, 8419), (182.0, 8419), (184.0, 8754), (187.0, 8419), (192.0, 8419), (197.0, 8419), (202.0, 8419), (204.0, 8755), (207.0, 8419), (212.0, 8419), (217.0, 8418), (222.0, 8419), (224.0, 8754), (227.0, 8419), (232.0, 8419), (237.0, 8419), (242.0, 8419), (244.0, 8755), (247.0, 8419), (252.0, 8419), (257.0, 8419), (262.0, 8419), (264.0, 8754), (267.0, 8419), (272.0, 8419), (277.0, 8419), (282.0, 8419), (284.0, 8755), (287.0, 8418), (292.0, 8419), (297.0, 8419), (302.0, 8420), (307.0, 8419), (309.0, 8754), (312.0, 8419), (317.0, 8419), (322.0, 8419), (327.0, 8419), (329.0, 8755), (332.0, 8419), (337.0, 8419), (342.0, 8419), (347.0, 8419), (349.0, 8754), (352.0, 8419), (357.0, 8418), (362.0, 8419), (367.0, 8419), (369.0, 8755), (372.0, 8419), (377.0, 8419), (382.1, 8419), (387.0, 8419), (389.0, 8754), (392.0, 8419), (397.0, 8419), (402.0, 8419), (407.0, 8419), (409.0, 8755), (412.0, 8419), (417.0, 8419), (422.0, 8418), (427.0, 8419), (429.0, 8754), (432.0, 8419), (437.0, 8419), (442.0, 8419), (447.0, 8419), (449.0, 8755), (452.0, 8419), (457.0, 8419), (462.0, 8419), (467.0, 8419), (469.0, 8754), (472.0, 8419), (477.0, 8419)]
PRAYER_CLICKS = [(24.1, "PROTECT_FROM_MAGIC"), (34.9, "PROTECT_FROM_MAGIC"), (45.3, "PROTECT_FROM_MISSILES"), (65.7, "PROTECT_FROM_MAGIC"), (86.5, "PROTECT_FROM_MISSILES"), (104.4, "PROTECT_FROM_MAGIC"), (125.4, "PROTECT_FROM_MISSILES"), (144.5, "PROTECT_FROM_MAGIC"), (164.8, "PROTECT_FROM_MISSILES"), (184.7, "PROTECT_FROM_MAGIC"), (189.5, "PROTECT_FROM_MAGIC"), (204.6, "PROTECT_FROM_MISSILES"), (226.9, "PROTECT_FROM_MAGIC"), (235.1, "PROTECT_FROM_MAGIC"), (236.1, "PROTECT_FROM_MAGIC"), (244.1, "PROTECT_FROM_MISSILES"), (265.0, "PROTECT_FROM_MAGIC"), (279.5, "PROTECT_FROM_MAGIC"), (285.0, "PROTECT_FROM_MISSILES"), (317.5, "PROTECT_FROM_MAGIC"), (329.2, "PROTECT_FROM_MISSILES"), (348.8, "PROTECT_FROM_MAGIC"), (355.1, "PROTECT_FROM_MAGIC"), (374.4, "PROTECT_FROM_MISSILES"), (389.5, "PROTECT_FROM_MAGIC"), (400.1, "PROTECT_FROM_MAGIC"), (409.4, "PROTECT_FROM_MISSILES"), (429.4, "PROTECT_FROM_MAGIC"), (449.6, "PROTECT_FROM_MISSILES"), (469.4, "PROTECT_FROM_MAGIC")]


def test_starts_ranged_and_flips_on_each_marker():
    t = h.StyleTracker()
    assert t.style == h.RANGED and t.expected_prayer() == "PROTECT_FROM_MISSILES"
    for _ in range(4):
        assert t.observe(8419) == "attack"
    assert t.next_style_due() and t.expected_prayer() == "PROTECT_FROM_MAGIC"
    assert t.observe(8754) == "marker"
    assert t.style == h.MAGIC and t.attacks == 0 and not t.next_style_due()
    for a in (8418, 8419, 8419, 8419):
        t.observe(a)
    assert t.observe(8755) == "marker"       # the tornado cast is a marker too
    assert t.style == h.RANGED and t.resyncs == 0


def test_stomp_and_idle_are_ignored_and_short_counts_resync():
    t = h.StyleTracker()
    assert t.observe(8420) is None and t.observe(-1) is None and t.attacks == 0
    t.observe(8419); t.observe(8419)
    t.observe(8754)                           # marker after only 2 attacks
    assert t.style == h.MAGIC and t.resyncs == 1 and t.attacks == 0


def test_observe_snapshot_counts_transitions_only():
    t = h.StyleTracker()
    t.observe_snapshot(8419); t.observe_snapshot(8419)     # same value twice: one attack
    assert t.attacks == 1
    t.observe_snapshot(-1); t.observe_snapshot(8419)       # back to idle then attack again
    assert t.attacks == 2


def test_replay_of_the_human_kill_predicts_every_prayer_click():
    """For every protection click the human made, the tracker fed with the Hunllef's animations
    up to that instant expects exactly that prayer -- including the predictive window (after the
    4th attack, before the marker) and the re-activations after prayer-disable hits."""
    t = h.StyleTracker()
    i = 0
    for tick, name in PRAYER_CLICKS:
        while i < len(ANIMS) and ANIMS[i][0] <= tick:
            t.observe(ANIMS[i][1])
            i += 1
        assert t.expected_prayer() == name, (tick, name, t.style, t.attacks)
    for tick, anim in ANIMS[i:]:
        t.observe(anim)
    assert t.markers == sum(1 for _, a in ANIMS if a in g.ANIM_MARKER)
    assert t.resyncs == 0


def test_predictive_window_opens_on_the_fourth_attack():
    t = h.StyleTracker()
    for tick, anim in ANIMS:
        t.observe(anim)
        if tick == 22.0:
            assert t.next_style_due() and t.upcoming_style() == h.MAGIC
            assert t.expected_prayer() == "PROTECT_FROM_MAGIC"
            break
