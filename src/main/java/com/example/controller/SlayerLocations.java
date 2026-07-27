package com.example.controller;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Real, walkable spawn locations for every assignable slayer task category (see
 * {@link SlayerMonsters#ALL_CATEGORIES}) -- not just Krystilia's wilderness-only assignments.
 * Sourced from individual, dedicated OSRS Wiki page fetches (including raw wikitext
 * {{LocLine}} templates for precise coordinates) -- not hand-recalled. Where a category's own
 * wiki page had no usable location data, the fetch fell back to the page of one of its real
 * member NPCs (see SlayerMonsters.TASKS for the mapping).
 *
 * Most entries are plain surface/dungeon spawns with no wilderness involvement at all
 * (wildernessLevel is null for these -- see the field's own javadoc). A handful of categories
 * are still wilderness-specific -- kept from this file's original wilderness-only scope -- and
 * for those several categories (e.g. Black demons, Fire giants, Hill Giants, Moss giants) also
 * have safe non-wilderness spawns elsewhere that would NOT count toward a Krystilia task; both
 * kinds of entry can coexist in this map since callers pick whichever fits their task source.
 *
 * Coordinates are a reasonable "walk here, then look for the monster nearby" point, not
 * necessarily an exact spawn tile -- fine given travel.walk_to()'s own arrival tolerance and
 * that a routine still needs to find_nearby() the actual NPC once in the area. Some entries note
 * an access prerequisite (a quest that must be started/completed, e.g. Heroes' Quest for the
 * Brimhaven Guard dogs, Royal Trouble for the Miscellania Sea snake dungeon) where the source
 * page called one out -- routines targeting those categories may need to account for that.
 *
 * Known gaps, left out rather than guessed:
 *   - Krystilia's "Wilderness bosses / demi-bosses" assignment isn't a single-location monster
 *     (it's an umbrella for Callisto, Vet'ion, Venenatis, Scorpia, Chaos Elemental, Chaos
 *     Fanatic, Crazy Archaeologist, King Black Dragon, etc., scattered across the wilderness) --
 *     not represented here, would need its own separate boss-picker treatment.
 *   - Nechryael: Krystilia specifically assigns the "Greater Nechryael" variant -- the regular
 *     Nechryael has no wilderness spawn at all -- so target selection for this category should
 *     resolve to "Greater Nechryael" specifically, not generic "Nechryael".
 *   - A few categories (Lesser demons' surface alternates) have confirmed coordinates but no
 *     confirmed wilderness level number (the source page gave the monster's combat level, not
 *     the location's PvP level range) -- left as null rather than a guessed number.
 *   - Cave kraken: no fixed spawn tile exists. Cave krakens surface only when a player disturbs
 *     one of several whirlpools scattered around Kraken Cove -- the wiki's own LocLine data for
 *     this page marks whirlpool positions, not monster spawns, same underlying mechanic as the
 *     Kraken boss encounter. Not represented here.
 *   - Monkeys: confirmed via two separate fetches (the category page and the plain "Monkey" NPC
 *     page) that no {{LocLine}} or other structured coordinate data exists anywhere on the wiki
 *     for this NPC -- only prose mentions of regions (Karamja, Ape Atoll, Mos Le'Harmless,
 *     Ardougne Zoo, Kharidian Desert). Left out rather than guessing a tile within one of those
 *     regions.
 *   - Shades: only the "Loar shade" variant's coordinates were confirmed; the other named colour
 *     variants (Phrin/Riyl/Asyn/Fiyr/Urium) are documented on the wiki as spawning in the same
 *     Mort'ton area but weren't individually coordinate-checked.
 *   - Zygomites: only the level-74 "Mutated zygomite" spawn (Zanaris, near the furnace) was used
 *     here. A level-86 Mutated zygomite cluster near the Cosmic Altar was also confirmed but not
 *     used (no clearly better than the furnace spot); the "Ancient zygomite" variant's own
 *     location (Fossil Island / Stalker Den) was not separately coordinate-checked.
 */
public final class SlayerLocations
{
	public static final Map<String, List<Location>> LOCATIONS = buildLocations();

	private SlayerLocations()
	{
	}

	public static final class Location
	{
		public final int x;
		public final int y;
		public final int plane;
		public final String wildernessLevel;
		public final String note;

		Location(int x, int y, int plane, String wildernessLevel, String note)
		{
			this.x = x;
			this.y = y;
			this.plane = plane;
			this.wildernessLevel = wildernessLevel;
			this.note = note;
		}
	}

	private static Location loc(int x, int y, int plane, String wildernessLevel, String note)
	{
		return new Location(x, y, plane, wildernessLevel, note);
	}

	private static Map<String, List<Location>> buildLocations()
	{
		Map<String, List<Location>> m = new LinkedHashMap<>();

		m.put("Aberrant spectres", List.of(loc(3413, 3550, 1, null, "Slayer Tower, 1st floor")));
		m.put("Abyssal demons", List.of(
			loc(3335, 10161, 0, "17-32", "Wilderness Slayer Cave interior"),
			loc(3293, 3748, 0, "18", "North entrance, surface")
		));
		m.put("Ankou", List.of(loc(2981, 3756, 0, "30", "Forgotten Cemetery")));
		m.put("Aquanites", List.of(loc(2248, 9862, 0, null, "Ynysdail Cavern")));
		m.put("Araxytes", List.of(loc(3658, 9842, 0, null, "Morytania Spider Cave, public area (level 96 variant)")));
		m.put("Aviansie", List.of(
			loc(2938, 10115, 0, "28", "Wilderness God Wars Dungeon interior"),
			loc(3016, 3739, 0, null, "Surface entrance, east of Forgotten Cemetery")
		));
		m.put("Bandits", List.of(loc(3029, 3701, 0, "17-24", "Bandit Camp")));
		m.put("Banshees", List.of(loc(3429, 3564, 0, null, "Slayer Tower, ground floor")));
		m.put("Basilisks", List.of(loc(2738, 10008, 0, null, "Fremennik Slayer Dungeon")));
		m.put("Bats", List.of(loc(3418, 3522, 0, null, "Slayer Tower entrance area")));
		m.put("Bears", List.of(loc(3001, 3674, 0, "12-16", "NW of Dark Warriors' Fortress")));
		m.put("Birds", List.of(loc(3171, 3293, 0, null, "Lumbridge West chicken farm -- category spans many bird types; chicken is the representative safe target")));
		m.put("Black demons", List.of(loc(3084, 9957, 0, null, "Edgeville Dungeon")));
		m.put("Black dragons", List.of(
			loc(3048, 10266, 0, "41-47", "Lava Maze Dungeon"),
			loc(3356, 10157, 0, null, "Wilderness Slayer Cave, level 247 variant")
		));
		m.put("Black Knights", List.of(loc(3308, 3769, 0, "~32", "NE of Bone Yard")));
		m.put("Bloodvelds", List.of(loc(2950, 10085, 0, "28", "Wilderness God Wars Dungeon")));
		m.put("Blue dragons", List.of(loc(2897, 9797, 0, null, "Taverley Dungeon, lower level")));
		m.put("Brine rats", List.of(loc(2702, 10135, 0, null, "Brine Rat Cavern")));
		m.put("Catablepon", List.of(loc(1950, 4996, 0, null, "Stronghold of Security, Pit of Pestilence (F2P, no quest required)")));
		m.put("Cave bugs", List.of(loc(3149, 9578, 0, null, "Lumbridge Swamp Caves")));
		m.put("Cave crawlers", List.of(loc(2784, 9998, 0, null, "Fremennik Slayer Dungeon")));
		m.put("Cave horrors", List.of(loc(3777, 9449, 0, null, "Mos Le'Harmless Cave")));
		m.put("Cave slimes", List.of(loc(3150, 9555, 0, null, "Lumbridge Swamp Caves")));
		m.put("Chaos druids", List.of(loc(3104, 9942, 0, null, "Edgeville Dungeon")));
		m.put("Cockatrices", List.of(loc(2785, 10035, 0, null, "Fremennik Slayer Cave")));
		m.put("Cows", List.of(loc(3254, 3255, 0, null, "Lumbridge East cow field")));
		m.put("Crabs", List.of(loc(1815, 3456, 0, null, "Hosidius southern coast (Sand crab)")));
		m.put("Crawling Hands", List.of(loc(3412, 3549, 0, null, "Slayer Tower, ground floor")));
		m.put("Crocodiles", List.of(loc(3337, 2922, 0, null, "River Elid, north bank")));
		m.put("Custodian stalkers", List.of(loc(1325, 3364, 0, null, "Stalker Den, southern entrance (Custodia Pass) -- walk in for the SW multicombat area; no LocLine data exists for the monster itself, only for the den entrance")));
		m.put("Dagannoth", List.of(loc(2511, 10020, 0, null, "Lighthouse basement")));
		m.put("Dark beasts", List.of(loc(3158, 12427, 0, null, "Iorwerth Dungeon, Prifddinas")));
		m.put("Dark warriors", List.of(loc(3022, 3624, 0, "12-16", "Dark Warriors' Fortress ground floor")));
		m.put("Dogs", List.of(loc(2784, 3191, 0, null, "Brimhaven, Black Arm Gang hideout (Guard dog; requires Heroes' Quest started)")));
		m.put("Drakes", List.of(loc(1195, 10189, 0, null, "Karuulm Slayer Dungeon")));
		m.put("Dust devils", List.of(loc(3431, 10117, 0, "25-28", "Wilderness Slayer Cave, NE chamber")));
		m.put("Dwarves", List.of(loc(2984, 9807, 0, null, "Dwarven Mine")));
		m.put("Earth Warriors", List.of(loc(3118, 9974, 0, "7-10", "Edgeville Dungeon NE (needs 15 Agility, monkey bars)")));
		m.put("Elves", List.of(loc(2193, 3244, 0, null, "Iorwerth Camp, SW of Prifddinas (Iorwerth Warrior)")));
		m.put("Ents", List.of(loc(3299, 3586, 0, "~12-20", "East of Chaos Temple")));
		m.put("Fever spiders", List.of(loc(2140, 5093, 0, null, "Braindeath Island brewery basement (requires Rum Deal started)")));
		m.put("Fire giants", List.of(loc(3046, 10341, 0, "49-56", "Deep Wilderness Dungeon -- only confirmed wildy spawn")));
		m.put("Flesh Crawlers", List.of(loc(1917, 5141, 0, null, "Stronghold of Security, Catacomb of Famine")));
		m.put("Fossil Island Wyverns", List.of(loc(3623, 10189, 0, null, "Wyvern Cave, south of Museum Camp (Spitting Wyvern)")));
		m.put("Frost dragons", List.of(loc(2851, 10455, 0, null, "Grimstone Dungeon")));
		m.put("Gargoyles", List.of(loc(3432, 3540, 2, null, "Slayer Tower, top floor")));
		m.put("Ghosts", List.of(loc(3239, 9915, 0, null, "Varrock Sewers")));
		m.put("Ghouls", List.of(loc(3423, 3461, 0, null, "North of Mort Myre Swamp, west of Canifis")));
		m.put("Goblins", List.of(loc(3183, 3244, 0, null, "Lumbridge")));
		m.put("Greater demons", List.of(loc(3282, 3880, 0, "44-48", "Demonic Ruins, F2P open surface")));
		m.put("Green dragons", List.of(loc(2973, 3620, 0, "~14-17", "West of Dark Warriors' Fortress")));
		m.put("Gryphons", List.of(loc(3084, 8868, 0, null, "Gryphons dungeon, western cave (off-task area)")));
		m.put("Harpie bug swarms", List.of(loc(2854, 3106, 0, null, "NE of Tai Bwo Wannai (fairy ring code dkp)")));
		m.put("Hellhounds", List.of(loc(3439, 10082, 0, "17-32", "Wilderness Slayer Cave, hellhound chamber")));
		m.put("Hill Giants", List.of(loc(3307, 3669, 0, "17-18", "Bone Yard area, near Ferox Enclave")));
		m.put("Hobgoblins", List.of(loc(2905, 3295, 0, null, "Hobgoblin Peninsula, west of Crafting Guild (F2P)")));
		m.put("Hydras", List.of(loc(1302, 10240, 0, null, "Karuulm Slayer Dungeon")));
		m.put("Icefiends", List.of(loc(2995, 3468, 0, null, "Ice Mountain")));
		m.put("Ice giants", List.of(
			loc(3333, 10052, 0, "17-32", "Wilderness Slayer Cave, safer entrance"),
			loc(2963, 3923, 0, "~50", "Frozen Waste Plateau, deep -- approximate center of a scattered spawn area")
		));
		m.put("Ice warriors", List.of(loc(2945, 3866, 0, "50", "Southern Frozen Waste Plateau -- only wildy spawn")));
		m.put("Infernal Mages", List.of(loc(3433, 3556, 1, null, "Slayer Tower, 1st floor")));
		m.put("Jellies", List.of(loc(3429, 10100, 0, "17-32", "Wilderness Slayer Cave")));
		m.put("Jungle horrors", List.of(loc(3713, 3024, 0, null, "Mos Le'Harmless jungle, northern camp area")));
		m.put("Kalphites", List.of(loc(3220, 9525, 0, null, "Kalphite Lair (Kalphite Worker)")));
		m.put("Killerwatts", List.of(loc(2642, 5202, 0, null, "Killerwatt Plane (requires Ernest the Chicken)")));
		m.put("Kurasks", List.of(loc(2694, 9995, 0, null, "Fremennik Slayer Dungeon, public area")));
		m.put("Lava dragons", List.of(loc(3223, 3830, 0, "36-42", "Lava Dragon Isle -- only spawn location")));
		m.put("Lesser demons", List.of(
			loc(3332, 10125, 0, "17-32", "Wilderness Slayer Cave, safest option"),
			loc(3091, 3861, 0, null, "Lava Maze, near muddy chest -- deep, exact wildy level unconfirmed"),
			loc(3311, 3845, 0, null, "South of Demonic Ruins -- deep, exact wildy level unconfirmed"),
			loc(3014, 3848, 0, null, "King Black Dragon lair entrance -- deep, exact wildy level unconfirmed")
		));
		m.put("Lesser Nagua", List.of(loc(1627, 9623, 0, null, "Ruins of Tapoyauik (Frost Nagua)")));
		m.put("Lizardmen", List.of(loc(1440, 3701, 0, null, "Lizardman Canyon, west side")));
		m.put("Lizards", List.of(loc(3386, 3065, 0, null, "SE of Shantay Pass, Kharidian Desert (Desert lizard)")));
		m.put("Magic axes", List.of(loc(3187, 3959, 0, "54", "Magic axe hut, needs 23 Thieving + lockpick")));
		m.put("Mammoths", List.of(loc(3159, 3593, 0, "~9-10", "SE of Ferox Enclave")));
		m.put("Metal dragons", List.of(loc(2704, 9431, 0, null, "Brimhaven Dungeon (Iron dragon)")));
		m.put("Minotaurs", List.of(loc(1862, 5190, 0, null, "Stronghold of Security, Vault of War")));
		m.put("Mogres", List.of(loc(2982, 3113, 0, null, "Mudskipper Point")));
		m.put("Molanisks", List.of(loc(2703, 5227, 0, null, "Dorgesh-Kaan South Dungeon")));
		m.put("Moss giants", List.of(loc(3139, 3818, 0, "35", "Wilderness Pond, SE of Lava Maze -- only wildy spawn")));
		m.put("Nechryael", List.of(loc(3335, 10114, 0, "22-25", "Wilderness Slayer Cave -- Krystilia assigns the Greater Nechryael variant specifically")));
		m.put("Ogres", List.of(loc(2584, 2967, 0, null, "Feldip Hills")));
		m.put("Otherworldly beings", List.of(loc(2381, 4425, 0, null, "Zanaris, near the flower pond")));
		m.put("Pirates", List.of(loc(3039, 3950, 0, "54", "Pirates' Hideout, needs 39 Thieving + lockpick")));
		m.put("Pyrefiends", List.of(loc(2757, 10008, 0, null, "Fremennik Slayer Cave")));
		m.put("Rats", List.of(loc(3222, 3215, 0, null, "Lumbridge")));
		m.put("Red dragons", List.of(loc(2697, 9506, 0, null, "Brimhaven Dungeon")));
		m.put("Revenants", List.of(loc(3073, 3654, 0, "17", "Revenant Caves, level 17 entrance (east of Dark Warriors' Fortress) -- all revenant types spawn throughout the same connected cave, so entrance choice doesn't affect which monster you get")));
		m.put("Rockslugs", List.of(loc(3222, 9571, 0, null, "Lumbridge Swamp Caves")));
		m.put("Rogues", List.of(loc(3276, 3930, 0, "52-56", "Rogues' Castle ground floor")));
		m.put("Scabarites", List.of(loc(3889, 4724, 0, null, "Uzer Mastaba, lower level (Small scarab)")));
		m.put("Scorpions", List.of(loc(3025, 3568, 0, "~6-8", "West of Obelisk of Air, near Edgeville Dungeon")));
		m.put("Sea snakes", List.of(loc(2595, 10271, 0, null, "Miscellania & Etceteria Dungeon (Sea Snake Young; requires Royal Trouble)")));
		m.put("Shades", List.of(loc(3474, 3280, 0, null, "Mort'ton (Loar shade -- other named shade colours spawn in the same area, not individually confirmed)")));
		m.put("Shadow warriors", List.of(loc(2693, 9776, 0, null, "Legends' Guild Dungeon")));
		m.put("Skeletal Wyverns", List.of(loc(3025, 9543, 0, null, "Asgarnian Ice Dungeon")));
		m.put("Skeletons", List.of(loc(3008, 3595, 0, "9-12", "SW Wilderness mine, near Edgeville")));
		m.put("Smoke devils", List.of(loc(2351, 9448, 0, null, "Smoke Devil Dungeon (fairy ring code bkp)")));
		m.put("Sourhogs", List.of(loc(3157, 9698, 0, null, "Sourhog Cave")));
		m.put("Spiders", List.of(loc(2982, 3683, 0, "21", "Ruins (west), near Bandit Camp")));
		m.put("Spiritual creatures", List.of(loc(3029, 10127, 0, "28", "Wilderness God Wars Dungeon")));
		m.put("Suqahs", List.of(loc(2096, 3942, 0, null, "North Lunar Isle")));
		m.put("Terror dogs", List.of(loc(3172, 4615, 0, null, "Tarn's Lair")));
		m.put("Trolls", List.of(loc(2852, 3591, 0, null, "Death Plateau (Mountain troll)")));
		m.put("Turoth", List.of(loc(2717, 10007, 0, null, "Fremennik Slayer Dungeon")));
		m.put("TzHaar", List.of(loc(2439, 5133, 0, null, "Mor Ul Rek, TzHaar City (not the Fight Caves/Fight Pit minigames)")));
		m.put("Vampyres", List.of(loc(3651, 3379, 0, null, "Darkmeyer (Vyrewatch)")));
		m.put("Wall Beasts", List.of(loc(3161, 9547, 0, null, "Lumbridge Swamp Caves, SW corner")));
		m.put("Warped creatures", List.of(loc(1487, 4291, 1, null, "Poison Waste Dungeon, main level (Warped Terrorbird)")));
		m.put("Waterfiends", List.of(loc(1738, 5343, 0, null, "Ancient Cavern")));
		m.put("Werewolves", List.of(loc(2836, 5349, 0, null, "God Wars Dungeon antechamber (non-wilderness variant)")));
		m.put("Wolves", List.of(loc(2602, 2955, 0, null, "Feldip Hills")));
		m.put("Wyrms", List.of(loc(1260, 10192, 0, null, "Karuulm Slayer Dungeon, lower level")));
		m.put("Zombies", List.of(loc(3152, 3674, 0, "18-21", "Graveyard of Shadows, north of Ferox Enclave")));
		m.put("Zygomites", List.of(loc(2412, 4470, 0, null, "Zanaris, near the furnace (Mutated zygomite, level 74)")));

		return m;
	}
}
