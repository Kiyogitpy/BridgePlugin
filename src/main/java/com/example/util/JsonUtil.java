package com.example.util;

import com.google.gson.Gson;

public final class JsonUtil
{
	private static final Gson GSON = new Gson();

	private JsonUtil()
	{
	}

	public static Gson gson()
	{
		return GSON;
	}

	public static String toJson(Object obj)
	{
		return GSON.toJson(obj);
	}
}
