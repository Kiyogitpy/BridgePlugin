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


def test_at_site_is_known_only_for_aya_tasks():
    assert st.at_site("Banshees", {"x": 3440, "y": 3545, "plane": 0}) is True
    assert st.at_site("Banshees", {"x": 3200, "y": 3200, "plane": 0}) is False
    assert st.at_site("Nechryael", {"x": 3440, "y": 3570, "plane": 2}) is None, "Kuradal sites are sub-project 3"
    assert st.at_site("Banshees", {}) is False


def test_where_names_the_place_and_the_first_teleport():
    text = st.where("Bears")
    assert "Legends" in text and "BLR" in text
    assert st.where("Nechryael") == ""
