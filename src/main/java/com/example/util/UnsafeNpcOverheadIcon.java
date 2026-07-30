package com.example.util;

import net.runelite.api.HeadIcon;
import net.runelite.api.NPC;

import java.lang.reflect.Field;
import java.lang.reflect.Method;

/**
 * Reads an NPC's overhead icon (e.g. Tormented Demon's rotating combat-style weakness) by
 * reflecting into RuneLite's obfuscated internal implementation classes.
 *
 * THIS IS DIFFERENT FROM EVERYTHING ELSE IN THIS CODEBASE. Every other provider reads only
 * RuneLite's official, public, documented Client API -- the same surface any legitimate
 * RuneLite plugin uses. {@code Actor}/{@code NPC} do not expose an overhead-icon getter in
 * that public API at all (only Player does); the only way to read it for an NPC is to poke
 * around the obfuscated concrete implementation class's private fields/methods, which get
 * renamed every game update. That means:
 *   - This can silently start returning null (or, worse, a wrong-but-plausible value) after
 *     any RuneLite/game update, with no compile error to catch it -- it degrades quietly.
 *   - It is meaningfully outside this project's "official API only" design boundary, kept
 *     deliberately isolated in this one file (not folded into RuneLiteUtil) so that boundary
 *     stays visible rather than blurred.
 *   - It's read-only (a display/decision INPUT, never used to write state or fire actions
 *     directly) -- callers should already be treating its result as best-effort, e.g.
 *     tormented_demon.py logs when it can't get a reading rather than assuming melee/silence
 *     means anything in particular.
 *
 * Ported and trimmed from a technique used by community RuneLite forks (the multiple fallback
 * strategies below exist because different client builds obfuscate this differently) --
 * verify this still returns sensible values after any client update before trusting it in a
 * live session, e.g. by comparing its output against what the overhead icon actually looks
 * like in-game.
 */
public final class UnsafeNpcOverheadIcon
{
	private UnsafeNpcOverheadIcon()
	{
	}

	public static HeadIcon read(NPC npc)
	{
		if (npc == null)
		{
			return null;
		}

		try
		{
			HeadIcon icon = fromNoArgShortArrayMethod(npc);
			if (icon != null)
			{
				return icon;
			}

			icon = fromCompositionIndexedShortMethod(npc);
			if (icon != null)
			{
				return icon;
			}

			return fromParallelLengthEightArrays(npc);
		}
		catch (Exception e)
		{
			// Never let a client-update-broken reflection path take down the bridge or the
			// client -- degrade to "unknown" instead.
			return null;
		}
	}

	/**
	 * Looks for a no-arg, 2-character-named method returning short[] on the NPC's own class --
	 * a common obfuscated-getter shape for "all head icon slots" (index 0 is the one actually
	 * shown overhead).
	 */
	private static HeadIcon fromNoArgShortArrayMethod(NPC npc)
	{
		for (Method method : npc.getClass().getDeclaredMethods())
		{
			if (method.getName().length() != 2 || method.getReturnType() != short[].class || method.getParameterCount() != 0)
			{
				continue;
			}

			short[] headIcons = invokeQuietly(method, npc);
			if (headIcons == null || headIcons.length == 0 || headIcons[0] < 0 || headIcons[0] >= HeadIcon.values().length)
			{
				continue;
			}

			return HeadIcon.values()[headIcons[0]];
		}
		return null;
	}

	/**
	 * Same idea, but for client builds that instead expose it as a single-short-returning,
	 * index-parameterized method on the NPC's composition (definition) object.
	 */
	private static HeadIcon fromCompositionIndexedShortMethod(NPC npc)
	{
		if (npc.getComposition() == null)
		{
			return null;
		}

		for (Method method : npc.getComposition().getClass().getDeclaredMethods())
		{
			if (method.getName().length() != 2 || method.getReturnType() != short.class || method.getParameterCount() != 1)
			{
				continue;
			}

			Short headIcon = invokeQuietly(method, npc.getComposition(), 0);
			if (headIcon == null || headIcon < 0 || headIcon >= HeadIcon.values().length)
			{
				continue;
			}

			return HeadIcon.values()[headIcon];
		}
		return null;
	}

	/**
	 * Last-resort fallback for client builds that store head icon data as a pair of parallel
	 * length-8 arrays (one of the OSRS overhead-icon slot count) inside some nested field --
	 * scans both the NPC and its composition for a field type shaped like that.
	 */
	private static HeadIcon fromParallelLengthEightArrays(NPC npc) throws IllegalAccessException
	{
		Class<?>[] candidates = {npc.getClass(), npc.getComposition() != null ? npc.getComposition().getClass() : null};

		for (Class<?> outer : candidates)
		{
			if (outer == null)
			{
				continue;
			}

			for (Field outerField : outer.getDeclaredFields())
			{
				Field[] innerFields = outerField.getType().getDeclaredFields();
				if (innerFields.length != 2 || !innerFields[0].getType().isArray() || !innerFields[1].getType().isArray())
				{
					continue;
				}

				Object container = readFieldQuietly(outerField, npc);
				if (container == null)
				{
					continue;
				}

				for (Field innerField : innerFields)
				{
					if (innerField.getType() != short[].class)
					{
						continue;
					}

					short[] array = (short[]) readFieldQuietly(innerField, container);
					if (array == null || array.length != 8 || array[0] < 0 || array[0] >= HeadIcon.values().length)
					{
						continue;
					}

					return HeadIcon.values()[array[0]];
				}
			}
		}

		return null;
	}

	@SuppressWarnings("unchecked")
	private static <T> T invokeQuietly(Method method, Object target, Object... args)
	{
		try
		{
			method.setAccessible(true);
			return (T) method.invoke(target, args);
		}
		catch (Exception e)
		{
			return null;
		}
		finally
		{
			method.setAccessible(false);
		}
	}

	private static Object readFieldQuietly(Field field, Object target)
	{
		try
		{
			field.setAccessible(true);
			return field.get(target);
		}
		catch (Exception e)
		{
			return null;
		}
		finally
		{
			field.setAccessible(false);
		}
	}
}
