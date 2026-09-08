"""ControllerAddon/wintertodt_ids.py -- the cache-derived tables.

These are pinned because they came from cache dump 5bc2ff1e, not from memory, and because the
name collisions are a live trap: nine objects in this arena share three names.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wintertodt_ids as w


def test_the_two_unlit_and_broken_braziers_share_a_name_and_differ_only_by_action():
    # The shut-vs-open door lesson: matching on name here picks the wrong object.
    assert w.OBJ_BRAZIER_UNLIT == 29312
    assert w.OBJ_BRAZIER_BROKEN == 29313
    assert w.OBJ_BRAZIER_LIT == 29314
    assert w.BRAZIER_IDS == frozenset({29312, 29313, 29314})


def test_every_tool_crate_is_a_distinct_id_with_its_own_action():
    assert set(w.CRATE_TOOLS) == {29316, 29317, 29318, 29319, 29320}
    assert w.CRATE_TOOLS[29316][0] == "Take-hammer"
    assert w.CRATE_TOOLS[29317][0] == "Take-knife"
    assert w.CRATE_TOOLS[29318][0] == "Take-axe"
    assert w.CRATE_TOOLS[29319][0] == "Take-tinderbox"
    assert w.CRATE_TOOLS[29320][0] == "Take-concoction"


def test_scenery_crates_are_disjoint_from_tool_crates():
    # 29301-29305 are named "Crate" too and have no ops at all -- clicking one does nothing.
    assert w.SCENERY_CRATES.isdisjoint(set(w.CRATE_TOOLS))


def test_item_and_point_values_match_runelites_own_plugin():
    assert (w.ITEM_ROOT, w.ITEM_KINDLING) == (20695, 20696)
    assert (w.ITEM_UNF, w.ITEM_HERB) == (20697, 20698)
    assert (w.POINTS_ROOT, w.POINTS_KINDLING) == (10, 25)
    assert w.REWARD_THRESHOLD == 500


def test_chat_constants_are_lowercase_prefixes_for_substring_matching():
    # outcome_waits.chat_has lowercases the incoming message, so these must be lowercase or
    # every match silently fails.
    for line in (w.CHAT_INVENTORY_FULL, w.CHAT_OUT_OF_ROOTS, w.CHAT_LIT, w.CHAT_FIXED,
                 w.CHAT_BRAZIER_OUT) + tuple(w.CHAT_DAMAGE):
        assert line == line.lower(), line
