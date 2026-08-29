package com.example.providers;

import net.runelite.client.plugins.raids.RaidRoom;
import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import static org.junit.Assert.assertEquals;

/**
 * Tests only the pure helpers -- combatAndPuzzleNames/countUnknown/allNames take a plain
 * List<RaidRoom> built from RaidRoom's own public enum constants, no Client/Raid/Layout mock
 * needed. orderedRooms(Raid) itself is NOT unit tested here: Raid has no public layout setter
 * (Raid.updateLayout is package-private to net.runelite.client.plugins.raids), so a test in this
 * package cannot construct a Raid with a real Layout attached -- same boundary
 * MahoganyHomesProviderTest draws around locateHotspotObjects (untestable without a live Client,
 * so left to live verification instead of a mock).
 */
public class RaidsProviderTest
{
	@Test
	public void filtersToCombatAndPuzzleOnly()
	{
		List<RaidRoom> ordered = Arrays.asList(
			RaidRoom.START, RaidRoom.VASA, RaidRoom.SCAVENGERS, RaidRoom.CRABS,
			RaidRoom.SHAMANS, RaidRoom.FARMING, RaidRoom.TIGHTROPE, RaidRoom.VESPULA,
			RaidRoom.END);

		assertEquals(Arrays.asList("Vasa", "Crabs", "Shamans", "Tightrope", "Vespula"),
			RaidsProvider.combatAndPuzzleNames(ordered));
	}

	@Test
	public void countsUnknownCombatAndPuzzleSeparatelyFromResolvedRooms()
	{
		List<RaidRoom> ordered = Arrays.asList(
			RaidRoom.VASA, RaidRoom.UNKNOWN_COMBAT, RaidRoom.UNKNOWN_PUZZLE, RaidRoom.CRABS);

		assertEquals(2, RaidsProvider.countUnknown(ordered));
	}

	@Test
	public void aFullyResolvedLayoutHasZeroUnknownRooms()
	{
		List<RaidRoom> ordered = Arrays.asList(
			RaidRoom.START, RaidRoom.VASA, RaidRoom.CRABS, RaidRoom.SHAMANS,
			RaidRoom.TIGHTROPE, RaidRoom.VESPULA, RaidRoom.END);

		assertEquals(0, RaidsProvider.countUnknown(ordered));
	}

	@Test
	public void unknownRoomsStillAppearInTheFilteredList_byDesign()
	{
		// unknownRooms is the signal the routine abstains on; combatAndPuzzleNames deliberately
		// does not hide them, so a caller that skipped the unknownRooms check would see the gap
		// rather than a falsely-short "complete" list.
		List<RaidRoom> ordered = Collections.singletonList(RaidRoom.UNKNOWN_PUZZLE);
		assertEquals(Collections.singletonList("Unknown (puzzle)"),
			RaidsProvider.combatAndPuzzleNames(ordered));
	}

	@Test
	public void allNamesKeepsNonCombatRoomsTooInPathOrder()
	{
		List<RaidRoom> ordered = Arrays.asList(RaidRoom.START, RaidRoom.SCAVENGERS, RaidRoom.VASA);
		assertEquals(Arrays.asList("Start", "Scavengers", "Vasa"),
			RaidsProvider.allNames(ordered));
	}

	@Test
	public void emptyOrderedListProducesEmptyOutputsNotErrors()
	{
		List<RaidRoom> ordered = Collections.emptyList();
		assertEquals(Collections.emptyList(), RaidsProvider.combatAndPuzzleNames(ordered));
		assertEquals(Collections.emptyList(), RaidsProvider.allNames(ordered));
		assertEquals(0, RaidsProvider.countUnknown(ordered));
	}
}
