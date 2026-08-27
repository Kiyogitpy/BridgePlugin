package com.example.providers;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertNotNull;

public class MahoganyHomesProviderTest
{
	@Test
	public void parsesAdeptAssignmentWithTrailingHint()
	{
		MahoganyHomesProvider.ParsedAssignment parsed = MahoganyHomesProvider.parseAssignment(
			"Adept Contract: Go see Jeff in the middle of Varrock, west of the museum.");
		assertNotNull(parsed);
		assertEquals(3, parsed.tier);
		assertEquals("Jeff", parsed.homeDisplayName);
	}

	@Test
	public void parsesAssignmentWhereNameIsFollowedByComma()
	{
		MahoganyHomesProvider.ParsedAssignment parsed = MahoganyHomesProvider.parseAssignment(
			"Adept Contract: Go see Leela, east of the town market in Hosidius.");
		assertNotNull(parsed);
		assertEquals(3, parsed.tier);
		assertEquals("Leela", parsed.homeDisplayName);
	}

	@Test
	public void parsesEveryTier()
	{
		assertEquals(1, MahoganyHomesProvider.parseAssignment(
			"Beginner Contract: Go see Bob in north-east Varrock, opposite the church.").tier);
		assertEquals(2, MahoganyHomesProvider.parseAssignment(
			"Novice Contract: Go see Bob in north-east Varrock, opposite the church.").tier);
		assertEquals(4, MahoganyHomesProvider.parseAssignment(
			"Expert Contract: Go see Bob in north-east Varrock, opposite the church.").tier);
	}

	@Test
	public void rejectsUnrelatedChatMessage()
	{
		assertNull(MahoganyHomesProvider.parseAssignment("Welcome to Old School RuneScape."));
	}

	@Test
	public void recognizesCompletionMessage()
	{
		assertEquals(true, MahoganyHomesProvider.isCompletionMessage(
			"You have completed 190 contracts with a total of 200 points."));
	}

	@Test
	public void doesNotMatchCompletionOnUnrelatedText()
	{
		assertEquals(false, MahoganyHomesProvider.isCompletionMessage(
			"Adept Contract: Go see Jeff in the middle of Varrock, west of the museum."));
	}
}
