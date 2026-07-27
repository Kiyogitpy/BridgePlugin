package com.example.controller;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Wilderness spawn locations for each of Krystilia's (the wilderness slayer master) assignable
 * task categories -- scoped to wilderness-only tasks specifically because those monsters must be
 * killed IN the wilderness for task progress, and several categories here (e.g. Black demons,
 * Fire giants, Hill Giants, Moss giants) also have safe non-wilderness spawns elsewhere that
 * would NOT count. Sourced from individual, dedicated OSRS Wiki page fetches (including raw
 * wikitext {{LocLine}} templates for precise coordinates) -- not hand-recalled.
 *
 * Coordinates are a reasonable "walk here, then look for the monster nearby" point, not
 * necessarily an exact spawn tile -- fine given travel.walk_to()'s own arrival tolerance and
 * that a routine still needs to find_nearby() the actual NPC once in the area.
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

		m.put("Abyssal demons", List.of(
			loc(3335, 10161, 0, "17-32", "Wilderness Slayer Cave interior"),
			loc(3293, 3748, 0, "18", "North entrance, surface")
		));
		m.put("Ankou", List.of(loc(2981, 3756, 0, "30", "Forgotten Cemetery")));
		m.put("Aviansie", List.of(
			loc(2938, 10115, 0, "28", "Wilderness God Wars Dungeon interior"),
			loc(3016, 3739, 0, null, "Surface entrance, east of Forgotten Cemetery")
		));
		m.put("Bandits", List.of(loc(3029, 3701, 0, "17-24", "Bandit Camp")));
		m.put("Bears", List.of(loc(3001, 3674, 0, "12-16", "NW of Dark Warriors' Fortress")));
		m.put("Black demons", List.of(loc(3084, 9957, 0, null, "Edgeville Dungeon")));
		m.put("Black dragons", List.of(
			loc(3048, 10266, 0, "41-47", "Lava Maze Dungeon"),
			loc(3356, 10157, 0, null, "Wilderness Slayer Cave, level 247 variant")
		));
		m.put("Black Knights", List.of(loc(3308, 3769, 0, "~32", "NE of Bone Yard")));
		m.put("Bloodvelds", List.of(loc(2950, 10085, 0, "28", "Wilderness God Wars Dungeon")));
		m.put("Chaos druids", List.of(loc(3104, 9942, 0, null, "Edgeville Dungeon")));
		m.put("Dark warriors", List.of(loc(3022, 3624, 0, "12-16", "Dark Warriors' Fortress ground floor")));
		m.put("Dust devils", List.of(loc(3431, 10117, 0, "25-28", "Wilderness Slayer Cave, NE chamber")));
		m.put("Earth Warriors", List.of(loc(3118, 9974, 0, "7-10", "Edgeville Dungeon NE (needs 15 Agility, monkey bars)")));
		m.put("Ents", List.of(loc(3299, 3586, 0, "~12-20", "East of Chaos Temple")));
		m.put("Fire giants", List.of(loc(3046, 10341, 0, "49-56", "Deep Wilderness Dungeon -- only confirmed wildy spawn")));
		m.put("Greater demons", List.of(loc(3282, 3880, 0, "44-48", "Demonic Ruins, F2P open surface")));
		m.put("Green dragons", List.of(loc(2973, 3620, 0, "~14-17", "West of Dark Warriors' Fortress")));
		m.put("Hellhounds", List.of(loc(3439, 10082, 0, "17-32", "Wilderness Slayer Cave, hellhound chamber")));
		m.put("Hill Giants", List.of(loc(3307, 3669, 0, "17-18", "Bone Yard area, near Ferox Enclave")));
		m.put("Ice giants", List.of(
			loc(3333, 10052, 0, "17-32", "Wilderness Slayer Cave, safer entrance"),
			loc(2963, 3923, 0, "~50", "Frozen Waste Plateau, deep -- approximate center of a scattered spawn area")
		));
		m.put("Ice warriors", List.of(loc(2945, 3866, 0, "50", "Southern Frozen Waste Plateau -- only wildy spawn")));
		m.put("Jellies", List.of(loc(3429, 10100, 0, "17-32", "Wilderness Slayer Cave")));
		m.put("Lava dragons", List.of(loc(3223, 3830, 0, "36-42", "Lava Dragon Isle -- only spawn location")));
		m.put("Lesser demons", List.of(
			loc(3332, 10125, 0, "17-32", "Wilderness Slayer Cave, safest option"),
			loc(3091, 3861, 0, null, "Lava Maze, near muddy chest -- deep, exact wildy level unconfirmed"),
			loc(3311, 3845, 0, null, "South of Demonic Ruins -- deep, exact wildy level unconfirmed"),
			loc(3014, 3848, 0, null, "King Black Dragon lair entrance -- deep, exact wildy level unconfirmed")
		));
		m.put("Magic axes", List.of(loc(3187, 3959, 0, "54", "Magic axe hut, needs 23 Thieving + lockpick")));
		m.put("Mammoths", List.of(loc(3159, 3593, 0, "~9-10", "SE of Ferox Enclave")));
		m.put("Moss giants", List.of(loc(3139, 3818, 0, "35", "Wilderness Pond, SE of Lava Maze -- only wildy spawn")));
		m.put("Nechryael", List.of(loc(3335, 10114, 0, "22-25", "Wilderness Slayer Cave -- Krystilia assigns the Greater Nechryael variant specifically")));
		m.put("Pirates", List.of(loc(3039, 3950, 0, "54", "Pirates' Hideout, needs 39 Thieving + lockpick")));
		m.put("Revenants", List.of(loc(3073, 3654, 0, "17", "Revenant Caves, level 17 entrance (east of Dark Warriors' Fortress) -- all revenant types spawn throughout the same connected cave, so entrance choice doesn't affect which monster you get")));
		m.put("Rogues", List.of(loc(3276, 3930, 0, "52-56", "Rogues' Castle ground floor")));
		m.put("Scorpions", List.of(loc(3025, 3568, 0, "~6-8", "West of Obelisk of Air, near Edgeville Dungeon")));
		m.put("Skeletons", List.of(loc(3008, 3595, 0, "9-12", "SW Wilderness mine, near Edgeville")));
		m.put("Spiders", List.of(loc(2982, 3683, 0, "21", "Ruins (west), near Bandit Camp")));
		m.put("Spiritual creatures", List.of(loc(3029, 10127, 0, "28", "Wilderness God Wars Dungeon")));
		m.put("Zombies", List.of(loc(3152, 3674, 0, "18-21", "Graveyard of Shadows, north of Ferox Enclave")));

		return m;
	}
}
