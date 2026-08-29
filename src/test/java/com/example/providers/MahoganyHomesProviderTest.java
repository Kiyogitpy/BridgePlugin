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
	public void parsesContractReminderMessage()
	{
		MahoganyHomesProvider.ParsedAssignment parsed = MahoganyHomesProvider.parseReminder(
			"You're currently on an Adept Contract. Go see Jeff in the middle of Varrock, "
				+ "west of the museum. You can get another job once you have furnished his home.");
		assertNotNull(parsed);
		assertEquals(3, parsed.tier);
		assertEquals("Jeff", parsed.homeDisplayName);
	}

	@Test
	public void reminderPatternIgnoresAnAssignmentMessage()
	{
		assertNull(MahoganyHomesProvider.parseReminder(
			"Adept Contract: Go see Jeff in the middle of Varrock, west of the museum."));
	}

	@Test
	public void assignmentPatternIgnoresAReminderMessage()
	{
		assertNull(MahoganyHomesProvider.parseAssignment(
			"You're currently on an Adept Contract. Go see Jeff in the middle of Varrock, "
				+ "west of the museum. You can get another job once you have furnished his home."));
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

	// CONFIRMED live 2026-08-28 (run 8, stopped by hand): the reminder line NEVER arrives as a
	// GAMEMESSAGE. Its raw record, captured by recorder.py, is
	//   {"type": "DIALOG", "message": "Amy|You're currently on an Adept Contract. Go see ..."}
	// -- a DIALOG whose text carries the speaker as a "<name>|" prefix. onChatMessage dropped
	// every non-GAMEMESSAGE, so the mid-contract restart recovery this pattern exists for could
	// not fire at all: after a client restart the provider reported hasContract=false, and the
	// routine re-requested the contract it already had, once per tick, forever.

	@Test
	public void parsesTheReminderOutOfAnNpcDialogueLine()
	{
		MahoganyHomesProvider.ParsedAssignment parsed = MahoganyHomesProvider.parseReminder(
			MahoganyHomesProvider.stripDialogSpeaker(
				"Amy|You're currently on an Adept Contract. Go see Larry, north of the fountain "
				+ "in Falador. You can get another job once you have furnished his home."));

		assertNotNull(parsed);
		assertEquals(3, parsed.tier);
		assertEquals("Larry", parsed.homeDisplayName);
	}

	@Test
	public void leavesAGameMessageWithoutASpeakerAlone()
	{
		assertEquals("Adept Contract: Go see Larry, north of the fountain in Falador.",
			MahoganyHomesProvider.stripDialogSpeaker(
				"Adept Contract: Go see Larry, north of the fountain in Falador."));
	}

	@Test
	public void stripsOnlyTheSpeakerAndKeepsLaterBars()
	{
		assertEquals("a|b", MahoganyHomesProvider.stripDialogSpeaker("Amy|a|b"));
	}

	@Test
	public void parsesTheReminderThroughNonBreakingSpaces()
	{
		// OSRS writes U+00A0 in its own text and Java's \s does not match it, so an unnormalised
		// pattern fails on a line that reads identically on screen.
		MahoganyHomesProvider.ParsedAssignment parsed = MahoganyHomesProvider.parseReminder(
			MahoganyHomesProvider.normalizeWhitespace(MahoganyHomesProvider.stripDialogSpeaker(
				"Amy|You're currently on an Adept Contract. Go see Larry, north of the "
				+ "fountain in Falador. You can get another job once you have furnished his "
				+ "home.")));

		assertNotNull(parsed);
		assertEquals("Larry", parsed.homeDisplayName);
	}

	@Test
	public void parsesTheReminderThroughDoubledSpacesFromLineWrapping()
	{
		// sanitizeMultilineText turns <br> into a space; where the source already had one that
		// leaves two, which "\w+ home\." would otherwise reject.
		MahoganyHomesProvider.ParsedAssignment parsed = MahoganyHomesProvider.parseReminder(
			MahoganyHomesProvider.normalizeWhitespace(MahoganyHomesProvider.stripDialogSpeaker(
				"Amy|You're currently on an Adept Contract. Go see Larry, north of the fountain "
				+ "in Falador.  You can get another job once you have furnished his  home.")));

		assertNotNull(parsed);
		assertEquals(3, parsed.tier);
	}

	@Test
	public void normalizesAnAssignmentGameMessageToo()
	{
		MahoganyHomesProvider.ParsedAssignment parsed = MahoganyHomesProvider.parseAssignment(
			MahoganyHomesProvider.normalizeWhitespace(
				"Adept Contract: Go see Larry, north of the fountain in Falador."));

		assertNotNull(parsed);
		assertEquals("Larry", parsed.homeDisplayName);
	}
}
