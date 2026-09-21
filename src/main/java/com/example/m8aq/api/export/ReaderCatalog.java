package com.example.m8aq.api.export;

import com.google.common.reflect.ClassPath;
import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.stream.Collectors;
import net.runelite.api.Client;

/** Discovers, classifies, and invokes snapshot-style game-state readers. */
public final class ReaderCatalog
{
	private static final String API_PACKAGE = "com.example.m8aq.api";
	private static final List<String> GROUP_ORDER = List.of(
		"Runtime", "Player", "Progression", "Scene", "Ui", "Trade", "Social", "Storage",
		"Skills", "Minigames");
	private static final List<Reader> READERS = discoverReaders(ReaderCatalog.class.getClassLoader());

	private ReaderCatalog()
	{
	}

	/** @return every discovered reader class in display order */
	public static List<Class<?>> getClasses()
	{
		return classes(READERS);
	}

	/**
	 * Discovers reader classes from one class loader.
	 *
	 * @param classLoader class loader to scan
	 * @return immutable reader classes in display order
	 */
	public static List<Class<?>> discoverClasses(ClassLoader classLoader)
	{
		return classes(discoverReaders(Objects.requireNonNull(classLoader, "classLoader")));
	}

	/**
	 * Returns the top-level game-state group for one reader.
	 *
	 * @param type reader class
	 * @return package-derived group name
	 */
	public static String getGroupName(Class<?> type)
	{
		String packageName = Objects.requireNonNull(type, "type").getPackageName();
		String prefix = API_PACKAGE + ".";
		String relativeName = packageName.startsWith(prefix)
			? packageName.substring(prefix.length())
			: packageName.substring(packageName.lastIndexOf('.') + 1);
		int nestedDirectory = relativeName.indexOf('.');
		String directory = nestedDirectory < 0 ? relativeName : relativeName.substring(0, nestedDirectory);
		return Character.toUpperCase(directory.charAt(0)) + directory.substring(1);
	}

	/**
	 * Captures one reader through the validated catalog convention.
	 *
	 * @param type reader class
	 * @param client RuneLite client
	 * @return reader state
	 * @throws ReflectiveOperationException when the reader cannot be invoked
	 */
	public static Object capture(Class<?> type, Client client) throws ReflectiveOperationException
	{
		for (Reader reader : READERS)
		{
			if (reader.type == type)
			{
				return reader.capture(client);
			}
		}
		return reader(type).capture(client);
	}

	static Map<String, Map<String, Object>> captureAll(Client client)
	{
		Objects.requireNonNull(client, "client");
		Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
		for (String group : GROUP_ORDER)
		{
			groups.put(group, new LinkedHashMap<>());
		}
		for (Reader reader : READERS)
		{
			if (reader.type != GameOutput.class)
			{
				groups.computeIfAbsent(reader.group, ignored -> new LinkedHashMap<>())
					.put(reader.type.getSimpleName(), reader.captureUnchecked(client));
			}
		}
		Map<String, Map<String, Object>> copy = new LinkedHashMap<>();
		groups.forEach((group, values) ->
		{
			if (!values.isEmpty())
			{
				copy.put(group, Collections.unmodifiableMap(values));
			}
		});
		return Collections.unmodifiableMap(copy);
	}

	private static List<Class<?>> classes(List<Reader> readers)
	{
		return readers.stream()
			.map(reader -> reader.type)
			.collect(Collectors.toUnmodifiableList());
	}

	private static List<Reader> discoverReaders(ClassLoader classLoader)
	{
		try
		{
			List<Reader> readers = new ArrayList<>();
			for (ClassPath.ClassInfo classInfo : ClassPath.from(classLoader).getTopLevelClassesRecursive(API_PACKAGE))
			{
				Class<?> type = classInfo.load();
				if (Modifier.isPublic(type.getModifiers()))
				{
					try
					{
						readers.add(reader(type));
					}
					catch (IllegalArgumentException ignored)
					{
						// Not a snapshot reader.
					}
				}
			}
			readers.sort(Comparator.comparing((Reader reader) -> reader.type.getSimpleName())
				.thenComparing(reader -> reader.type.getName()));
			return Collections.unmodifiableList(readers);
		}
		catch (IOException ex)
		{
			throw new IllegalStateException("Unable to discover API readers", ex);
		}
	}

	private static Reader reader(Class<?> type)
	{
		try
		{
			Method getState = type.getMethod("getState", Client.class);
			if (!Modifier.isPublic(type.getModifiers()) || !Modifier.isStatic(getState.getModifiers())
				|| getState.getReturnType() == Void.TYPE)
			{
				throw new IllegalArgumentException(type.getName() + " is not a snapshot reader");
			}
			return new Reader(type, getGroupName(type), getState);
		}
		catch (NoSuchMethodException ex)
		{
			throw new IllegalArgumentException(type.getName() + " is not a snapshot reader", ex);
		}
	}

	private static final class Reader
	{
		private final Class<?> type;
		private final String group;
		private final Method getState;

		private Reader(Class<?> type, String group, Method getState)
		{
			this.type = type;
			this.group = group;
			this.getState = getState;
		}

		private Object capture(Client client) throws ReflectiveOperationException
		{
			try
			{
				return getState.invoke(null, client);
			}
			catch (InvocationTargetException ex)
			{
				Throwable cause = ex.getCause();
				if (cause instanceof RuntimeException)
				{
					throw (RuntimeException) cause;
				}
				if (cause instanceof Error)
				{
					throw (Error) cause;
				}
				throw ex;
			}
		}

		private Object captureUnchecked(Client client)
		{
			try
			{
				return capture(client);
			}
			catch (ReflectiveOperationException ex)
			{
				throw new IllegalStateException("Unable to capture " + type.getName(), ex);
			}
		}
	}
}
