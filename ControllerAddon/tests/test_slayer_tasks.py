import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import slayer_tasks as st


def test_the_users_kuradal_decisions():
    """docs/slayer-sites/decisions.md: 22 FIGHT, 1 STOP, 20 SKIP."""
    fights = ["Aberrant spectres", "Abyssal demons", "Ankou", "Basilisks", "Black demons", "Bloodveld",
              "Cave horrors", "Dagannoth", "Dust devils", "Elves", "Fire giants", "Gargoyles", "Gryphons",
              "Hellhounds", "Kalphite", "Kurask", "Mutated zygomites", "Nechryael", "Suqahs", "Trolls",
              "Waterfiends", "Wyrms"]
    skips = ["Boss", "Dark beasts", "Warped creatures", "Vampyres", "Araxytes", "Drakes",
             "Fossil Island wyverns", "Lizardmen", "Red dragons", "Aquanites", "Aviansie", "Black dragons",
             "Blue dragons", "Cave kraken", "Frost dragons", "Metal dragons", "Skeletal wyverns",
             "Smoke devils", "Spiritual creatures", "TzHaar"]
    assert [t for t in fights if st.disposition(t, {}) != st.FIGHT] == []
    assert [t for t in skips if st.disposition(t, {}) != st.SKIP] == []
    assert st.disposition("Greater demons", {}) == st.STOP
    assert len(fights) == 22 and len(skips) == 20


def test_aya_tasks_are_fought_and_a_blocked_one_stops():
    assert st.disposition("Bears", {}) == st.FIGHT
    assert st.disposition("bears", None) == st.FIGHT
    assert st.disposition("Dogs", {}) == st.STOP, "blocked at Aya: if it ever shows up a human should look"


def test_an_unknown_task_stops_it_is_never_cancelled():
    """Cancelling costs 30 points. A name we do not recognise (a new task, a decode slip) must not
    be paid for on a guess."""
    assert st.disposition("Some New Monster", {}) == st.STOP
    assert st.disposition(None, {}) == st.STOP
    assert st.disposition("", {}) == st.STOP


def test_the_panels_skip_list_overrides_a_fight():
    cfg = {"skipTasks": "Abyssal demons, kurask ,  "}
    assert st.user_skips(cfg) == {"abyssal demons", "kurask"}
    assert st.disposition("Kurask", cfg) == st.SKIP
    assert st.disposition("Abyssal Demons", cfg) == st.SKIP
    assert st.disposition("Bloodveld", cfg) == st.FIGHT
    assert st.user_skips(None) == set() and st.user_skips({"skipTasks": None}) == set()


def test_the_bridge_sends_the_skip_list_as_a_list():
    """MEASURED live: GET /slayer-config -> "skipTasks": []. The string form crashed on it."""
    cfg = {"skipTasks": ["Abyssal demons", " kurask "]}
    assert st.user_skips(cfg) == {"abyssal demons", "kurask"}
    assert st.disposition("Kurask", cfg) == st.SKIP
    assert st.user_skips({"skipTasks": []}) == set()


def test_the_skip_list_cannot_turn_a_stop_into_a_paid_cancel_by_accident():
    assert st.disposition("Greater demons", {"skipTasks": "greater demons"}) == st.SKIP, \
        "an explicit user entry IS a decision to pay"
    assert st.disposition("Some New Monster", {"skipTasks": "bloodveld"}) == st.STOP


def test_target_names_are_what_combat_core_matches_by_substring():
    """combat_core matches a lowercased name as a SUBSTRING of the NPC's name and adds the
    singular itself, so the task's own name is enough for most tasks."""
    assert st.target_names("Bears") == ["bears"]
    assert st.target_names("Nechryael") == ["nechryael"]
    assert "chicken" in st.target_names("Birds") and "seagull" in st.target_names("Birds")
    assert st.target_names("Dwarves") == ["dwarf"]
    assert st.target_names("Wolves") == ["wolf"]
    assert st.target_names("Kalphite") == ["kalphite"]
    assert st.target_names(None) == []


def test_every_fight_task_has_a_name_combat_core_can_actually_match():
    """Task-1 review: 'Zombies' fell back to ['zombies'], combat_core's singular of that is
    'zomby', and the monsters at the site are 'Undead cow' / 'Undead chicken' -- nothing would
    ever have matched. Run the real matcher's name expansion over a real NPC name per task."""
    import combat_core
    samples = {"Zombies": "Undead cow", "Birds": "Undead chicken", "Cows": "Undead cow", "Bears": "Grizzly bear",
               "Aberrant spectres": "Aberrant spectre", "Elves": "Elf warrior", "Trolls": "Mountain troll",
               "Mutated zygomites": "Zygomite", "Wolves": "White wolf", "Dwarves": "Dwarf", "Suqahs": "Suqah",
               "Lizards": "Desert Lizard", "Crawling hands": "Crawling Hand", "Fire giants": "Fire giant"}
    for task, npc in samples.items():
        _, names = combat_core.resolve_targets({"targetNames": st.target_names(task), "targets": []})
        assert any(n in npc.lower() for n in names if n), f"{task}: nothing in {sorted(names)} matches {npc!r}"


def test_at_site_is_known_only_for_aya_tasks():
    assert st.at_site("Banshees", {"x": 3440, "y": 3545, "plane": 0}) is True
    assert st.at_site("Banshees", {"x": 3200, "y": 3200, "plane": 0}) is False
    assert st.at_site("Nechryael", {"x": 3440, "y": 3570, "plane": 2}) is None, "Kuradal sites are sub-project 3"
    assert st.at_site("Banshees", {}) is False


def test_where_names_the_place_and_the_first_teleport():
    text = st.where("Bears")
    assert "Legends" in text and "BLR" in text
    assert st.where("Nechryael") == ""


def test_required_items_come_from_the_plugins_own_gear_notes():
    slimes = st.required_items("Cave slimes", 17)
    assert [n.label for n in slimes] == ["a light source"]
    assert 4550 in slimes[0].ids and 4548 not in slimes[0].ids and slimes[0].quantity == 1
    lizards = st.required_items("Lizards", 20)
    assert lizards[0].label == "ice coolers" and lizards[0].quantity == 25, "one per kill plus five spare"
    assert st.required_items("Skeletons", 15)[0].any_of == ("rope",)
    assert st.required_items("Goblins", 15) == [], "'South of the Sourhog Cave' is a place, not an item"
    assert st.required_items("Bears", 15) == [] and st.required_items("Nechryael", 90) == []


def test_missing_items_names_what_to_fetch_and_accepts_any_alternative():
    assert st.missing_items("Cave slimes", 17, {}) == ["a light source (e.g. bullseye lantern, LIT)"]
    assert st.missing_items("Cave slimes", 17, {"bullseye lantern": 1, "#4550": 1}) == []
    assert st.missing_items("Cave slimes", 17, {"bullseye lantern": 1, "#4548": 1}) != [], \
        "the UNLIT lantern has the SAME NAME (wiki: 4548 unlit, 4550 lit): only the id proves a light"
    assert st.missing_items("Cave slimes", 17, {"bullseye lantern": 1}) != [], "a name alone proves nothing"
    assert st.missing_items("Cave slimes", 17, {"mining helmet": 1, "#5013": 1}) == []
    assert st.missing_items("Cave slimes", 17, {"kandarin headgear 3": 1}) == [], "always lit: the name is enough"
    assert st.missing_items("Lizards", 20, {"ice cooler": 10}) == ["ice coolers x25 (have 10)"]
    assert st.missing_items("Lizards", 20, {"ice cooler": 30}) == []
