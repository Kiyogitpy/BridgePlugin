package com.example.util;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

import static org.junit.Assert.*;

public class EventSubscriptionsTest
{
	@Test
	public void emptyByDefaultMatchesNothing()
	{
		EventSubscriptions s = new EventSubscriptions();
		assertFalse(s.matches(13691, 4310));
		assertFalse(s.matches(-1, 4310));
	}

	@Test
	public void matchesSubscribedVarbitOrVarp()
	{
		EventSubscriptions s = new EventSubscriptions();
		s.replace(Arrays.asList(13691, 5667), Collections.singletonList(1000));
		assertTrue(s.matches(13691, 4310));
		assertTrue(s.matches(-1, 1000));
		assertFalse(s.matches(13688, 4311));
	}

	@Test
	public void replaceIsReplaceNotMerge()
	{
		EventSubscriptions s = new EventSubscriptions();
		s.replace(Collections.singletonList(13691), Collections.emptyList());
		s.replace(Collections.singletonList(5667), Collections.emptyList());
		assertFalse(s.matches(13691, 0));
		assertTrue(s.matches(5667, 0));
		s.replace(Collections.emptyList(), Collections.emptyList());
		assertFalse(s.matches(5667, 0));
	}

	@Test
	public void parseIdsSkipsJunkAndBlanks()
	{
		assertEquals(Arrays.asList(1, 2, 3), EventSubscriptions.parseIds("1,2, 3"));
		assertEquals(Collections.emptyList(), EventSubscriptions.parseIds(null));
		assertEquals(Collections.emptyList(), EventSubscriptions.parseIds(""));
		assertEquals(Collections.singletonList(7), EventSubscriptions.parseIds("x,7,,"));
	}
}
