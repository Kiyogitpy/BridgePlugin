package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import net.runelite.api.Client;
import net.runelite.api.Skill;
import net.runelite.client.callback.ClientThread;

import java.util.HashMap;
import java.util.Map;

public class SkillProvider extends BridgeProvider
{
	public SkillProvider(Client client, ClientThread clientThread, BridgeHttpServer server)
	{
		super(client, clientThread, server);
	}

	@Override
	public void register()
	{
		server.register("/skills", () -> onClient(this::getSkills));
	}

	private Object getSkills()
	{
		Map<String, Object> skills = new HashMap<>();
		for (Skill s : Skill.values())
		{
			Map<String, Object> data = new HashMap<>();
			data.put("level", client.getRealSkillLevel(s));
			data.put("boosted", client.getBoostedSkillLevel(s));
			data.put("xp", client.getSkillExperience(s));
			skills.put(s.getName(), data);
		}
		return skills;
	}
}
