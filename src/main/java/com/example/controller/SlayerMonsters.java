package com.example.controller;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Every distinct slayer task category, mapped to the real, specific monster/NPC names that
 * count toward it -- e.g. "Lizardmen" -> "Lizardman", "Lizardman brute", "Lizardman shaman".
 * Task category labels are frequently NOT literal NPC names (there's no NPC actually named
 * "Lizardmen"), so this mapping exists specifically so the UI/config can offer up real,
 * targetable names rather than a label that would never match anything in-game.
 *
 * Sourced from individual, dedicated fetches of each category's own page on
 * https://oldschool.runescape.wiki -- not hand-recalled. A handful of entries were still
 * dropped/adjusted after cross-checking the raw research for internal contradictions or
 * dangerously generic names:
 *   - "Revenant" was dropped from Ghosts -- it directly contradicts Revenants being its own,
 *     entirely separate task/monster family (wilderness-only, unrelated to ghosts).
 *   - A bare "Guard" was dropped from Elves (sourced as "Guard (Prifddinas)") -- that word alone
 *     is reused by generic town guards throughout the game and would cause false-positive
 *     targeting far outside Prifddinas.
 *   - Monkeys' dedicated page didn't have slayer-task info at all; "Monkey" is kept as a
 *     low-risk fallback (basic, long-standing content) but wasn't wiki-reconfirmed like the rest.
 * Still worth spot-checking against in-game/wiki if a specific entry ever misses a real NPC.
 */
public final class SlayerMonsters
{
	public static final Map<String, List<String>> TASKS = buildTasks();

	public static final List<String> ALL_CATEGORIES = Collections.unmodifiableList(new ArrayList<>(TASKS.keySet()));

	private SlayerMonsters()
	{
	}

	private static Map<String, List<String>> buildTasks()
	{
		Map<String, List<String>> m = new LinkedHashMap<>();

		m.put("Aberrant spectres", List.of("Aberrant spectre", "Deviant spectre"));
		m.put("Abyssal demons", List.of("Abyssal demon", "Greater abyssal demon", "Abyssal Sire"));
		m.put("Ankou", List.of("Ankou"));
		m.put("Aquanites", List.of("Aquanite", "Elder Aquanite"));
		m.put("Araxytes", List.of("Araxyte", "Dreadborn Araxyte"));
		m.put("Aviansie", List.of("Aviansie", "Kree'arra", "Flight Kilisa", "Wingman Skree", "Flockleader Geerin"));
		m.put("Bandits", List.of("Bandit"));
		m.put("Banshees", List.of("Banshee", "Screaming Banshee"));
		m.put("Basilisks", List.of("Basilisk", "Monstrous basilisk"));
		m.put("Bats", List.of("Bat", "Giant bat", "Albino bat"));
		m.put("Bears", List.of("Black bear", "Bear cub", "Grizzly bear", "Grizzly bear cub", "Reanimated bear", "Callisto", "Artio"));
		m.put("Birds", List.of("Bird", "Chicken", "Duck", "Rooster", "Seagull", "Penguin", "Entrana firebird", "Chompy bird", "Terrorbird", "Vulture", "Oomlie bird", "Mounted terrorbird", "Undead chicken", "Baby Roc"));
		m.put("Black demons", List.of("Black demon", "Demonic gorilla", "Balfrug Kreeyath", "Skotizo", "Porazdir", "Kolodion"));
		m.put("Black dragons", List.of("Black dragon", "Baby black dragon", "Brutal black dragon", "King Black Dragon"));
		m.put("Black Knights", List.of("Black Knight"));
		m.put("Bloodvelds", List.of("Bloodveld", "Mutated Bloodveld", "Insatiable Bloodveld"));
		m.put("Blue dragons", List.of("Blue dragon", "Baby blue dragon"));
		m.put("Brine rats", List.of("Brine rat"));
		m.put("Catablepon", List.of("Catablepon"));
		m.put("Cave bugs", List.of("Cave bug"));
		m.put("Cave crawlers", List.of("Cave crawler", "Icy cave crawler", "Chasm Crawler"));
		m.put("Cave horrors", List.of("Cave horror"));
		m.put("Cave kraken", List.of("Cave kraken", "Kraken"));
		m.put("Cave slimes", List.of("Cave slime"));
		m.put("Chaos druids", List.of("Chaos druid", "Elder Chaos druid"));
		m.put("Cockatrices", List.of("Cockatrice", "Moonlight cockatrice", "Cockathrice"));
		m.put("Cows", List.of("Cow", "Cow calf"));
		m.put("Crabs", List.of("Crab", "Ammonite crab", "Frost crab", "King sand crab", "Rock crab", "Giant rock crab", "Sand crab", "Swamp crab"));
		m.put("Crawling Hands", List.of("Crawling Hand"));
		m.put("Crocodiles", List.of("Crocodile"));
		m.put("Custodian stalkers", List.of("Juvenile custodian stalker", "Mature custodian stalker", "Elder custodian stalker", "Ancient Custodian"));
		m.put("Dagannoth", List.of("Dagannoth", "Dagannoth spawn"));
		m.put("Dark beasts", List.of("Dark beast", "Night beast"));
		m.put("Dark warriors", List.of("Dark warrior"));
		m.put("Dogs", List.of("Jackal", "Temple guardian", "Guard dog", "Wild dog", "Shadow hound", "Reanimated dog"));
		m.put("Drakes", List.of("Drake", "Guardian drake"));
		m.put("Dust devils", List.of("Dust devil", "Choke devil"));
		m.put("Dwarves", List.of("Dwarf"));
		m.put("Earth Warriors", List.of("Earth warrior"));
		m.put("Elves", List.of("Iorwerth Archer", "Elf Archer", "Iorwerth Warrior", "Elf Warrior", "Mourner", "Reanimated Elf"));
		m.put("Ents", List.of("Ent"));
		m.put("Fever spiders", List.of("Fever spider"));
		m.put("Fire giants", List.of("Fire giant", "Branda the Fire Queen"));
		m.put("Flesh Crawlers", List.of("Flesh Crawler"));
		m.put("Fossil Island Wyverns", List.of("Spitting Wyvern", "Taloned Wyvern", "Long-tailed Wyvern", "Ancient Wyvern"));
		m.put("Frost dragons", List.of("Frost dragon"));
		m.put("Gargoyles", List.of("Gargoyle", "Marble gargoyle"));
		m.put("Ghosts", List.of("Ghost", "Forgotten soul", "Tortured soul", "Death wing"));
		m.put("Ghouls", List.of("Ghoul"));
		m.put("Goblins", List.of("Goblin", "Cave goblin", "Cave goblin miner", "Cave goblin guard", "Sergeant Strongstack", "Sergeant Grimspike", "Sergeant Steelwill"));
		m.put("Greater demons", List.of("Greater demon", "K'ril Tsutsaroth", "Tstanon Karlak", "Skotizo", "Tormented Demon"));
		m.put("Green dragons", List.of("Green dragon"));
		m.put("Gryphons", List.of("Gryphon", "Dire gryphon"));
		m.put("Harpie bug swarms", List.of("Harpie bug swarm"));
		m.put("Hellhounds", List.of("Hellhound", "Skeleton Hellhound", "Greater Skeleton Hellhound", "Cerberus"));
		m.put("Hill Giants", List.of("Hill giant", "Cyclops", "Obor", "Reanimated giant"));
		m.put("Hobgoblins", List.of("Hobgoblin"));
		m.put("Hydras", List.of("Hydra", "Colossal Hydra", "Alchemical Hydra"));
		m.put("Icefiends", List.of("Icefiend"));
		m.put("Ice giants", List.of("Ice giant"));
		m.put("Ice warriors", List.of("Ice warrior", "Kamil"));
		m.put("Infernal Mages", List.of("Infernal Mage", "Malevolent Mage"));
		m.put("Jellies", List.of("Jelly", "Vitreous Jelly", "Warped Jelly", "Chilled Jelly"));
		m.put("Jungle horrors", List.of("Jungle horror"));
		m.put("Kalphites", List.of("Kalphite Worker", "Kalphite Soldier", "Kalphite Guardian", "Kalphite Queen"));
		m.put("Killerwatts", List.of("Killerwatt"));
		m.put("Kurasks", List.of("Kurask", "King kurask"));
		m.put("Lava dragons", List.of("Lava dragon"));
		m.put("Lesser demons", List.of("Lesser demon"));
		m.put("Lesser Nagua", List.of("Frost Nagua", "Sulphur Nagua", "Earthen Nagua"));
		m.put("Lizardmen", List.of("Lizardman", "Lizardman brute", "Lizardman shaman"));
		m.put("Lizards", List.of("Lizard", "Small lizard", "Desert lizard"));
		m.put("Magic axes", List.of("Magic axe"));
		m.put("Mammoths", List.of("Mammoth"));
		m.put("Metal dragons", List.of("Bronze dragon", "Iron dragon", "Steel dragon", "Mithril dragon", "Adamant dragon", "Rune dragon"));
		m.put("Minotaurs", List.of("Minotaur"));
		m.put("Mogres", List.of("Mogre"));
		m.put("Molanisks", List.of("Molanisk"));
		m.put("Monkeys", List.of("Monkey"));
		m.put("Moss giants", List.of("Moss giant"));
		m.put("Nechryael", List.of("Nechryael", "Greater Nechryael", "Nechryarch"));
		m.put("Ogres", List.of("Ogre"));
		m.put("Otherworldly beings", List.of("Otherworldly being"));
		m.put("Pirates", List.of("Pirate"));
		m.put("Pyrefiends", List.of("Pyrefiend", "Pyrelord", "Flaming pyrelord"));
		m.put("Rats", List.of("Rat"));
		m.put("Red dragons", List.of("Red dragon", "Brutal red dragon"));
		m.put("Revenants", List.of("Revenant imp", "Revenant goblin", "Revenant pyrefiend", "Revenant hobgoblin", "Revenant cyclops", "Revenant hellhound", "Revenant demon", "Revenant ork", "Revenant dark beast", "Revenant knight", "Revenant dragon", "Revenant maledictus"));
		m.put("Rockslugs", List.of("Rockslug"));
		m.put("Rogues", List.of("Rogue"));
		m.put("Scabarites", List.of("Small scarab", "Scarab swarm", "Locust rider", "Scarab mage", "Giant Scarab"));
		m.put("Scorpions", List.of("Scorpion"));
		m.put("Sea snakes", List.of("Sea snake hatchling", "Sea snake young"));
		m.put("Shades", List.of("Shade", "Loar shade", "Phrin shade", "Riyl shade", "Asyn shade", "Fiyr shade", "Urium shade"));
		m.put("Shadow warriors", List.of("Shadow warrior"));
		m.put("Skeletal Wyverns", List.of("Skeletal wyvern"));
		m.put("Skeletons", List.of("Skeleton"));
		m.put("Smoke devils", List.of("Smoke devil", "Nuclear smoke devil", "Thermonuclear smoke devil"));
		m.put("Sourhogs", List.of("Sourhog"));
		m.put("Spiders", List.of("Spider", "Giant spider", "Fever spider", "Jungle spider", "Ice spider", "Poison spider", "Crypt spider", "Giant crypt spider", "Shadow spider", "Deadly red spider", "Blessed spider"));
		m.put("Spiritual creatures", List.of("Spiritual ranger", "Spiritual warrior", "Spiritual mage"));
		m.put("Suqahs", List.of("Suqah"));
		m.put("Terror dogs", List.of("Terror dog"));
		m.put("Trolls", List.of("Troll", "Ice troll", "Dad", "Arrg", "Pee Hat", "Kraka", "Ice Troll King"));
		m.put("Turoth", List.of("Turoth"));
		m.put("TzHaar", List.of("TzHaar-Ket", "TzHaar-Xil", "TzHaar-Mej", "TzHaar-Hur"));
		m.put("Vampyres", List.of("Feral vampyre", "Vampyre Juvenile", "Vampyre Juvinate", "Vyrewatch", "Vyrewatch Sentinel"));
		m.put("Wall Beasts", List.of("Wall beast"));
		m.put("Warped creatures", List.of("Warped tortoise", "Warped terrorbird", "Mutated tortoise", "Mutated terrorbird"));
		m.put("Waterfiends", List.of("Waterfiend"));
		m.put("Werewolves", List.of("Werewolf"));
		m.put("Wolves", List.of("Wolf"));
		m.put("Wyrms", List.of("Wyrm", "Wyrmling", "Lava strykewyrm"));
		m.put("Zombies", List.of("Zombie"));
		m.put("Zygomites", List.of("Mutated zygomite", "Ancient zygomite"));

		return Collections.unmodifiableMap(m);
	}

	/** One matched task category plus the (full, unfiltered) list of real monster names under it. */
	public static final class TaskMatch
	{
		public final String category;
		public final List<String> monsters;

		TaskMatch(String category, List<String> monsters)
		{
			this.category = category;
			this.monsters = monsters;
		}
	}

	/**
	 * Task categories matching `query` (case-insensitive, matched against either the category
	 * name or any of its monster names), sorted so a category whose own name starts with query
	 * ranks first, then one whose *monster* name starts with query, then everything else that
	 * merely contains query somewhere -- alphabetical by category name within each group. An
	 * empty query returns every category in alphabetical order. Each returned TaskMatch carries
	 * its *entire* monster list regardless of which specific name matched, so the UI can show the
	 * whole task grouped together rather than a partial member list.
	 */
	public static List<TaskMatch> search(String query)
	{
		String q = query == null ? "" : query.trim().toLowerCase(Locale.ENGLISH);

		List<String> categories = new ArrayList<>();
		if (q.isEmpty())
		{
			categories.addAll(ALL_CATEGORIES);
		}
		else
		{
			for (String category : ALL_CATEGORIES)
			{
				boolean categoryMatches = category.toLowerCase(Locale.ENGLISH).contains(q);
				boolean monsterMatches = TASKS.get(category).stream()
					.anyMatch(name -> name.toLowerCase(Locale.ENGLISH).contains(q));
				if (categoryMatches || monsterMatches)
				{
					categories.add(category);
				}
			}

			categories.sort(Comparator
				.comparingInt((String category) -> matchRank(category, q))
				.thenComparing(category -> category.toLowerCase(Locale.ENGLISH)));
		}

		List<TaskMatch> matches = new ArrayList<>();
		for (String category : categories)
		{
			matches.add(new TaskMatch(category, TASKS.get(category)));
		}
		return matches;
	}

	private static int matchRank(String category, String q)
	{
		if (category.toLowerCase(Locale.ENGLISH).startsWith(q))
		{
			return 0;
		}
		boolean monsterStartsWith = TASKS.get(category).stream()
			.anyMatch(name -> name.toLowerCase(Locale.ENGLISH).startsWith(q));
		return monsterStartsWith ? 1 : 2;
	}
}
