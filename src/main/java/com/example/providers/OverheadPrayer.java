package com.example.providers;

import net.runelite.api.HeadIcon;
import net.runelite.api.NPC;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Which protection prayer an NPC is using, read the way ZombieLite reads it.
 *
 * <p><b>Why this exists beside the public API.</b> {@link NPC#getOverheadSpriteIds()} gives a raw
 * sprite ordinal that is only a {@link HeadIcon} when the sprite came from the PRAYER icon archive,
 * and {@code getOverheadArchiveIds()} says which archive each one actually came from. This codebase
 * has never recorded what that archive id is per state, so {@code NPCProvider}'s own comment marks
 * its decoded name untrustworthy -- an open checklist item since the Gauntlet work in 2026-09-05.
 *
 * <p>ZombieLite sidesteps the question entirely: it reads the head icon out of the client's own
 * obfuscated fields and gets a {@link HeadIcon} directly. PORTED here rather than called across,
 * because ZombieLite is only loaded when its checkout exists next to ours and a hard reference
 * would fail to LINK on a client built without it. Copying the fact instead of depending on the
 * plugin is this project's standing rule.
 *
 * <p>Ported from {@code com.salvation.hooks.NpcsHook#getOverheadPrayer} (ZombieLite, branch
 * {@code no-packets}), which has been running this in production for the Hunllef, Demonic Gorillas
 * and Tormented Demons. Three changes were made deliberately:
 *
 * <ul>
 *   <li>The resolved {@link Method} is CACHED per class. ZombieLite calls this for one tracked
 *       boss; {@code /npcs} calls it for every npc in a bulk scan, and re-walking
 *       {@code getDeclaredMethods()} each time would put a reflective scan on the client thread
 *       once per npc per request.</li>
 *   <li>{@link #fromSpriteId(int)} bounds-checks before indexing {@link HeadIcon#values()}. The
 *       upstream indexes straight in, which throws on a sprite from any other archive -- exactly
 *       the case this class exists to survive.</li>
 *   <li>Every path returns null rather than throwing, because a bulk scan must not be taken down
 *       by one unusual npc.</li>
 * </ul>
 *
 * <p><b>Unknown is never a prayer.</b> Null means "could not read it", and callers must treat that
 * as "do not act", never as a style -- swapping gear on a bad decode walks into the demon holding
 * the one style it is praying against.
 *
 * <p>UNVERIFIED AGAINST OUR OWN CLIENT as of 2026-09-21: the reflection is proven inside ZombieLite
 * but has never run from this package. Report it ALONGSIDE the API's answer rather than replacing
 * it -- the two together are what finally pairs an archive id with each state and closes the
 * Gauntlet question.
 */
public final class OverheadPrayer
{
	/** Resolved per NPC class, so a bulk scan pays the reflective walk once rather than per npc. */
	private static final Map<Class<?>, Method> SPRITE_METHODS = new ConcurrentHashMap<>();
	private static final Map<Class<?>, Method> COMPOSITION_METHODS = new ConcurrentHashMap<>();

	/** Cache entry meaning "this class has no such method"; null cannot be stored in the map. */
	private static final Method NONE;

	static
	{
		Method none = null;
		try
		{
			none = OverheadPrayer.class.getDeclaredMethod("noSuchMethod");
		}
		catch (NoSuchMethodException ignored)
		{
			// Cannot happen: the method is declared below.
		}
		NONE = none;
	}

	private OverheadPrayer()
	{
	}

	/** Placeholder target for {@link #NONE}; never called. */
	private static void noSuchMethod()
	{
	}

	/**
	 * A raw overhead sprite ordinal -> its {@link HeadIcon}, or null when it is not one.
	 *
	 * <p>-1 is the client's "no overhead" sentinel. Anything outside the enum came from an archive
	 * that is not the prayer archive and is refused rather than indexed, which is the difference
	 * between reporting "unknown" and throwing inside a bulk scan.
	 */
	public static HeadIcon fromSpriteId(int spriteId)
	{
		HeadIcon[] icons = HeadIcon.values();
		if (spriteId < 0 || spriteId >= icons.length)
		{
			return null;
		}
		return icons[spriteId];
	}

	/**
	 * The npc's protection prayer, or null if it has none or it could not be read.
	 *
	 * <p>Never throws. Three strategies in ZombieLite's own order, each covering a different client
	 * revision's field layout, and the first non-null answer wins.
	 */
	public static HeadIcon read(NPC npc)
	{
		if (npc == null)
		{
			return null;
		}
		try
		{
			HeadIcon icon = fromSpriteArrayMethod(npc);
			if (icon != null)
			{
				return icon;
			}
			icon = fromCompositionMethod(npc);
			if (icon != null)
			{
				return icon;
			}
			return fromLengthEightArrays(npc);
		}
		catch (Exception | LinkageError ignored)
		{
			// One unusual npc must not fail the whole scan; "could not read it" is a valid answer.
			return null;
		}
	}

	/** The npc's own two-character {@code short[]} accessor, as ZombieLite's getOldHeadIcon. */
	private static HeadIcon fromSpriteArrayMethod(NPC npc)
	{
		Method method = SPRITE_METHODS.computeIfAbsent(npc.getClass(), type -> {
			for (Method declared : type.getDeclaredMethods())
			{
				if (declared.getName().length() == 2
					&& declared.getReturnType() == short[].class
					&& declared.getParameterCount() == 0)
				{
					declared.setAccessible(true);
					return declared;
				}
			}
			return NONE;
		});
		if (method == NONE)
		{
			return null;
		}
		try
		{
			short[] sprites = (short[]) method.invoke(npc);
			return sprites == null || sprites.length == 0 ? null : fromSpriteId(sprites[0]);
		}
		catch (Exception ignored)
		{
			return null;
		}
	}

	/** The composition's short-returning accessor, as ZombieLite's getOlderHeadicon. */
	private static HeadIcon fromCompositionMethod(NPC npc)
	{
		Object composition = npc.getComposition();
		if (composition == null)
		{
			return null;
		}
		Method method = COMPOSITION_METHODS.computeIfAbsent(composition.getClass(), type -> {
			for (Method declared : type.getDeclaredMethods())
			{
				if (declared.getName().length() == 2
					&& declared.getReturnType() == short.class
					&& declared.getParameterCount() == 1)
				{
					declared.setAccessible(true);
					return declared;
				}
			}
			return NONE;
		});
		if (method == NONE)
		{
			return null;
		}
		try
		{
			short sprite = (short) method.invoke(composition, 0);
			return fromSpriteId(sprite);
		}
		catch (Exception ignored)
		{
			return null;
		}
	}

	/**
	 * The paired length-8 arrays the client keeps overheads in, as ZombieLite's
	 * headIconThroughLengthEightArrays. Deliberately last: it is the broadest match and so the most
	 * likely to find the wrong pair of arrays on a future client.
	 */
	private static HeadIcon fromLengthEightArrays(NPC npc) throws IllegalAccessException
	{
		Object composition = npc.getComposition();
		Class<?>[] candidates = composition == null
			? new Class<?>[]{npc.getClass()}
			: new Class<?>[]{npc.getClass(), composition.getClass()};
		for (Class<?> type : candidates)
		{
			for (Field declared : type.getDeclaredFields())
			{
				Field[] inner = declared.getType().getDeclaredFields();
				if (inner.length != 2 || !inner[0].getType().isArray() || !inner[1].getType().isArray())
				{
					continue;
				}
				Object[] first;
				Object[] second;
				try
				{
					inner[0].setAccessible(true);
					inner[1].setAccessible(true);
					first = (Object[]) inner[0].get(npc);
					second = (Object[]) inner[1].get(npc);
				}
				catch (Exception ignored)
				{
					continue;
				}
				if (first == null || second == null || first.length != 8 || second.length != 8)
				{
					continue;
				}
				Object[] sprites = inner[0].getType() == short[].class ? first : second;
				if (!(sprites[0] instanceof Short))
				{
					continue;
				}
				return fromSpriteId((Short) sprites[0]);
			}
		}
		return null;
	}
}
