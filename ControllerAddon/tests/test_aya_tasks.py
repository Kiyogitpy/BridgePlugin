import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aya_tasks

# The 24 tasks Aya/Turael assigns (osrs-wiki Turael/Slayer assignments), as the game names them.
AYA_TASKS = ["Banshees", "Bats", "Bears", "Birds", "Cave bugs", "Cave crawlers", "Cave slimes", "Cows",
             "Crawling hands", "Dogs", "Dwarves", "Ghosts", "Goblins", "Icefiends", "Kalphite",
             "Lizards", "Minotaurs", "Monkeys", "Rats", "Scorpions", "Skeletons", "Spiders",
             "Wolves", "Zombies"]


def test_every_aya_task_name_resolves():
    missing = [t for t in AYA_TASKS if aya_tasks.get(t) is None]
    assert missing == []


def test_lookup_is_case_insensitive_and_tolerates_the_singular_form():
    assert aya_tasks.get("BANSHEES").npc_ids == (414,)
    assert aya_tasks.get("cave slime") is aya_tasks.get("Cave slimes")
    assert aya_tasks.get("crawling hand") is aya_tasks.get("Crawling Hands")
    assert aya_tasks.get("not a task") is None


def test_the_plugins_data_comes_through_verbatim():
    banshees = aya_tasks.get("Banshees")
    assert banshees.target == (3442, 3542, 0)
    assert banshees.location == "Morytania Slayer Tower"
    assert banshees.teleports == ("Slayer Ring: Morytania Slayer Tower (Option 2)",)
    assert banshees.areas == ((3431, 3530, 22, 38, 0),)
    assert aya_tasks.get("Cave bugs").info == "Bring a light source"
    assert aya_tasks.get("Lizards").info == "Bring Ice coolers"


def test_alices_farm_serves_three_tasks_from_one_ectophial():
    farm = [aya_tasks.get(t) for t in ("Zombies", "Birds", "Cows")]
    assert {t.location for t in farm} == {"Alice's Farm, west of the Ectofuntus"}
    assert all(t.teleports == ("Ectophial: Empty",) for t in farm)
    assert 2992 in aya_tasks.get("Cows").npc_ids and 2992 in aya_tasks.get("Zombies").npc_ids


def test_the_users_three_overrides_are_applied():
    """docs/slayer-sites/decisions.md: dogs are blocked at Aya; monkeys are killed on Karamja,
    not in Kruk's Dungeon; minotaurs keep the plugin's site but travel by the ornate jewellery
    box to the Cooking Guild."""
    assert aya_tasks.get("Dogs").disposition == aya_tasks.BLOCKED
    monkeys = aya_tasks.get("Monkeys")
    assert monkeys.disposition == aya_tasks.FIGHT
    assert monkeys.location == "Karamja, near the volcano" and monkeys.target == (2873, 3148, 0)
    assert "unverified" in monkeys.note.lower(), "the Karamja tile is a wiki pin, to confirm on the first visit"
    minotaurs = aya_tasks.get("Minotaurs")
    assert minotaurs.target == (1875, 5218, 0), "site unchanged"
    assert minotaurs.teleports[0] == "Ornate Jewellery Box: Cooking Guild"


def test_everything_else_is_fought():
    others = [t for t in AYA_TASKS if t not in ("Dogs",)]
    assert all(aya_tasks.get(t).disposition == aya_tasks.FIGHT for t in others)


def test_contains_answers_am_i_at_the_site():
    banshees = aya_tasks.get("Banshees")
    assert aya_tasks.contains(banshees, 3440, 3545, 0)
    assert not aya_tasks.contains(banshees, 3440, 3545, 1)
    assert not aya_tasks.contains(banshees, 3400, 3545, 0)
    assert aya_tasks.contains(aya_tasks.get("Lizards"), 3430, 3060, 0), "a site may be several rectangles"
