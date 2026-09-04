package com.example.util;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Which varbits/varps the bridge emits VARBIT_CHANGED for. VarbitChanged fires for hundreds
 * of varps on login and every scene load; unfiltered, that burst evicts the useful events
 * from UdpEventSender's 512-slot queue. So the set is opt-in, REPLACE semantics (a routine
 * declares what it needs; the engine posts it on load and after every login), and empty means
 * emit none. AtomicReference swaps make matches() a lock-free read on the client thread.
 *
 * See docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md.
 */
public final class EventSubscriptions
{
	private final AtomicReference<Set<Integer>> varbits = new AtomicReference<>(Collections.emptySet());
	private final AtomicReference<Set<Integer>> varps = new AtomicReference<>(Collections.emptySet());

	public void replace(Collection<Integer> varbitIds, Collection<Integer> varpIds)
	{
		varbits.set(Collections.unmodifiableSet(new HashSet<>(varbitIds)));
		varps.set(Collections.unmodifiableSet(new HashSet<>(varpIds)));
	}

	/** varbitId is -1 when the client changed a raw varp with no varbit mapping. */
	public boolean matches(int varbitId, int varpId)
	{
		return (varbitId >= 0 && varbits.get().contains(varbitId)) || varps.get().contains(varpId);
	}

	public Set<Integer> varbits()
	{
		return varbits.get();
	}

	public Set<Integer> varps()
	{
		return varps.get();
	}

	/** "1,2, 3" -> [1, 2, 3]; null/blank/junk entries are skipped, never an error. */
	public static List<Integer> parseIds(String csv)
	{
		List<Integer> out = new ArrayList<>();
		if (csv == null || csv.isEmpty())
		{
			return out;
		}
		for (String part : csv.split(","))
		{
			String s = part.trim();
			if (s.isEmpty())
			{
				continue;
			}
			try
			{
				out.add(Integer.parseInt(s));
			}
			catch (NumberFormatException ignored)
			{
				// junk in the query string -- skip it
			}
		}
		return out;
	}
}
