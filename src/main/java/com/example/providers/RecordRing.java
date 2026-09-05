package com.example.providers;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Map;

/**
 * The recorder's bounded ring of records, with a {@code dropped} count that means what
 * recorder.py's warning says it means: <b>records evicted before any reader took them</b>.
 * <p>
 * Extracted from {@link RecorderProvider} 2026-09-05 after a Corrupted Gauntlet recording of
 * 26695 records (seq 0..26694, verified gap-free) reported 6695 dropped -- exactly the number of
 * records beyond the 20000-slot capacity. Eviction used to count unconditionally, so any
 * recording longer than the ring produced a "this recording has a gap" warning that was false,
 * and the user read it as the Hunllef fight being missing. Now every drain records the highest
 * seq handed out, and an eviction only counts when the evicted record is past that mark.
 * <p>
 * Delivered records deliberately stay in the ring until pushed out: a reader may re-request from
 * an older {@code since} after a failed poll, and the cost is only memory.
 */
final class RecordRing
{
	/** One drain's result: the records after {@code since}, and the seq to pass as the next since. */
	static final class Drain
	{
		final List<Map<String, Object>> events;
		final long nextSeq;

		Drain(List<Map<String, Object>> events, long nextSeq)
		{
			this.events = events;
			this.nextSeq = nextSeq;
		}
	}

	private final int capacity;
	private final Deque<Map<String, Object>> buffer = new ArrayDeque<>();
	private long nextSeq = 0;
	private long dropped = 0;
	/** Highest seq any drain has handed out; -1 until the first drain. */
	private long lastDelivered = -1;

	RecordRing(int capacity)
	{
		this.capacity = capacity;
	}

	/** Stamps {@code seq} on the record, appends it, evicts past capacity. Returns the seq. */
	synchronized long add(Map<String, Object> record)
	{
		long seq = nextSeq++;
		record.put("seq", seq);
		buffer.addLast(record);
		while (buffer.size() > capacity)
		{
			Map<String, Object> evicted = buffer.removeFirst();
			if (((Number) evicted.get("seq")).longValue() > lastDelivered)
			{
				dropped++;
			}
		}
		return seq;
	}

	/** Records with seq strictly after {@code since}, oldest first, at most {@code limit}. */
	synchronized Drain drain(long since, int limit)
	{
		List<Map<String, Object>> out = new ArrayList<>();
		long next = since;
		for (Map<String, Object> record : buffer)
		{
			long seq = ((Number) record.get("seq")).longValue();
			if (seq <= since)
			{
				continue;
			}
			out.add(record);
			next = seq;
			if (out.size() >= limit)
			{
				break;
			}
		}
		if (next > lastDelivered)
		{
			lastDelivered = next;
		}
		return new Drain(out, next);
	}

	synchronized void reset()
	{
		buffer.clear();
		nextSeq = 0;
		dropped = 0;
		lastDelivered = -1;
	}

	synchronized long dropped()
	{
		return dropped;
	}

	synchronized int size()
	{
		return buffer.size();
	}

	synchronized long nextSeq()
	{
		return nextSeq;
	}
}
