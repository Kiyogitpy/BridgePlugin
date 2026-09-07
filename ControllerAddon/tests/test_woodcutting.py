"""routines/woodcutting.py -- the second profile over gathering.py.

The loop itself is covered by test_gathering.py (through the Mining profile); what is tested
here is only what the Woodcutting profile changes: which action identifies a target, which XP
field and skill name confirm progress, which config section it reads, and that a full inventory
is DROPPED rather than banked (power-chopping is the normal woodcutting loop).
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routines"))

import pytest

import controller
import gathering
import woodcutting

P = woodcutting.PROFILE
CFG = {"treeNames": ["Yew tree"], "maxTreeDistance": 10}


def _tree(x, y, obj_id=10822, name="Yew tree", dist=3, actions=("Chop down",), **over):
    d = {"id": obj_id, "name": name, "x": x, "y": y, "plane": 0, "distance": dist,
         "visible": True, "screenX": 100 + x, "screenY": 100 + y}
    if actions is not None:
        d["actions"] = list(actions)
    d.update(over)
    return d


def test_trees_are_identified_by_their_chop_action():
    assert gathering.pick_target(P, [_tree(1, 1)], CFG)["x"] == 1


def test_a_felled_stump_keeping_the_trees_name_is_not_picked():
    # A chopped tree becomes a stump that can share the display name; only the live tree offers
    # Chop down. This is also why a lone tree needs no respawn timer -- it simply stops matching.
    stump = _tree(1, 1, dist=1, actions=("Examine",))
    standing = _tree(2, 2, dist=8)
    assert gathering.pick_target(P, [stump, standing], CFG)["x"] == 2
    assert gathering.pick_target(P, [stump], CFG) is None


def test_progress_is_confirmed_against_woodcutting_xp(monkeypatch):
    monkeypatch.setattr(controller, "get_player",
                        lambda: {"miningXp": 500, "woodcuttingXp": 1234})
    assert gathering.skill_xp(P) == 1234


def test_an_xp_event_for_another_skill_is_not_our_signal():
    match = gathering._signal_match(P, _tree(5, 5))
    assert match({"skill": "Woodcutting"}) is True
    # A Mining drop from someone else's... well, from our own other activity, on a tile that is
    # not the tree's: not this tree's business.
    assert match({"skill": "Mining", "x": 9, "y": 9, "plane": 0}) is False
    # ...but a despawn on the tree's own tile is, whatever skill it came from.
    assert match({"x": 5, "y": 5, "plane": 0}) is True


def test_config_comes_from_the_woodcutting_bridge_section(monkeypatch):
    monkeypatch.setattr(controller, "get_woodcutting_config",
                        lambda: {"enabled": True, "treeNames": ["Yew tree"], "maxTreeDistance": 12})
    cfg = woodcutting.get_config(None)
    assert cfg["treeNames"] == ["Yew tree"]
    assert cfg["maxTreeDistance"] == 12
    assert cfg["breaks"] == woodcutting.DEFAULT_CONFIG["breaks"]
    # The engine must not stop the run on a full inventory -- this routine drops instead.
    assert cfg["safety"]["stopOnInventoryFull"] is False


def test_config_falls_back_only_when_the_bridge_cannot_answer(monkeypatch):
    monkeypatch.setattr(controller, "get_woodcutting_config", lambda: None)
    assert woodcutting.get_config(None) == woodcutting.DEFAULT_CONFIG
    monkeypatch.setattr(controller, "get_woodcutting_config", lambda: {"treeNames": []})
    assert woodcutting.get_config(None)["treeNames"] == []


def test_a_full_inventory_is_dropped_keeping_the_axe(monkeypatch):
    inventory = [{"name": "Rune axe"}, {"name": "Dragon axe"}] + [{"name": "Yew logs"}] * 26
    dropped = []
    # The logs leave the inventory as they are dropped, exactly as the game would do it.
    monkeypatch.setattr(controller, "get_inventory",
                        lambda: [] if dropped else list(inventory))
    monkeypatch.setattr(controller, "drop_items",
                        lambda ser, items: dropped.extend(i["name"] for i in items) or True)
    monkeypatch.setattr(gathering.banker, "find_nearby_bank",
                        lambda cfg: pytest.fail("woodcutting power-chops; it must not bank"))
    state = controller.ControllerState()
    state.config = dict(woodcutting.DEFAULT_CONFIG, treeNames=["Yew tree"])
    ctx = types.SimpleNamespace(ser=None, state=state, events=None)
    assert gathering.full_inventory_phase(P, ctx, state.config) is True
    assert dropped == ["Yew logs"] * 26
    assert not ctx.state.safety_stopped
