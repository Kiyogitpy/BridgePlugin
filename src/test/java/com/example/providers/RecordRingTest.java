package com.example.providers;

import org.junit.Test;

import java.util.HashMap;
import java.util.Map;

import static org.junit.Assert.assertEquals;

public class RecordRingTest
{
	private static Map<String, Object> record(String name)
	{
		Map<String, Object> r = new HashMap<>();
		r.put("name", name);
		return r;
	}

	@Test
	public void evictingAlreadyDeliveredRecordsIsNotADrop()
	{
		// The 2026-09-05 Corrupted Gauntlet recording: 26695 records through a 20000-slot ring,
		// every one of them delivered (seq 0..26694, no gaps) -- and "dropped" still reported 6695,
		// because eviction counted regardless of whether the reader had already taken the record.
		RecordRing ring = new RecordRing(3);
		ring.add(record("a"));
		ring.add(record("b"));
		ring.add(record("c"));
		RecordRing.Drain first = ring.drain(-1, 100);
		assertEquals(3, first.events.size());
		assertEquals(2, first.nextSeq);

		ring.add(record("d"));
		ring.add(record("e"));
		ring.add(record("f"));

		assertEquals(0, ring.dropped());
		RecordRing.Drain second = ring.drain(first.nextSeq, 100);
		assertEquals(3, second.events.size());
		assertEquals("d", second.events.get(0).get("name"));
		assertEquals(5, second.nextSeq);
	}

	@Test
	public void evictingAnUndeliveredRecordIsADrop()
	{
		RecordRing ring = new RecordRing(3);
		ring.add(record("a"));
		ring.add(record("b"));
		ring.add(record("c"));
		ring.add(record("d")); // evicts "a", which nobody read

		assertEquals(1, ring.dropped());
		RecordRing.Drain drain = ring.drain(-1, 100);
		assertEquals("b", drain.events.get(0).get("name"));
		assertEquals(3, drain.events.size());
	}

	@Test
	public void partialDrainOnlyMarksWhatWasHandedOut()
	{
		RecordRing ring = new RecordRing(3);
		ring.add(record("a"));
		ring.add(record("b"));
		ring.add(record("c"));
		RecordRing.Drain drain = ring.drain(-1, 1); // reader took only "a"
		assertEquals(1, drain.events.size());
		assertEquals(0, drain.nextSeq);

		ring.add(record("d")); // evicts "a": delivered, fine
		ring.add(record("e")); // evicts "b": never delivered
		assertEquals(1, ring.dropped());
	}

	@Test
	public void resetClearsEverything()
	{
		RecordRing ring = new RecordRing(2);
		ring.add(record("a"));
		ring.add(record("b"));
		ring.add(record("c"));
		assertEquals(1, ring.dropped());

		ring.reset();
		assertEquals(0, ring.dropped());
		assertEquals(0, ring.size());
		assertEquals(0, ring.nextSeq());
		assertEquals(0, ring.add(record("x")));
	}
}
