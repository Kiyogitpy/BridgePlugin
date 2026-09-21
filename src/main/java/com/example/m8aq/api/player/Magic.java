package com.example.m8aq.api.player;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.ItemComposition;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetUtil;

/** Provides a read-only snapshot of the local player's spellbook and spell-selection state. */
public final class Magic
{
	private Magic()
	{
	}

	/**
	 * Reads current magic state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable magic snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		if (client.getLocalPlayer() == null)
		{
			return new State(null, false, -1, null, false,
				false, -1, null, null);
		}

		int spellbook = client.getVarbitValue(VarbitID.SPELLBOOK);
		boolean autocastSet = client.getVarbitValue(VarbitID.AUTOCAST_SET) == 1;
		int autocastSpellObjectId = autocastSet ? client.getVarpValue(VarPlayerID.AUTOCAST_SPELL_OBJ) : -1;
		ItemComposition autocastSpell = autocastSpellObjectId > 0
			? client.getItemDefinition(autocastSpellObjectId) : null;
		Widget selected = client.isWidgetSelected() ? client.getSelectedWidget() : null;
		boolean spellSelected = selected != null
			&& WidgetUtil.componentToInterface(selected.getId()) == InterfaceID.MAGIC_SPELLBOOK;

		return new State(
			Spellbook.from(spellbook),
			autocastSet,
			autocastSpellObjectId,
			autocastSpell == null ? null : autocastSpell.getName(),
			autocastSet && client.getVarbitValue(VarbitID.AUTOCAST_DEFMODE) == 1,
			spellSelected,
			selected == null ? -1 : selected.getId(),
			selected == null ? null : selected.getName(),
			selected == null ? null : selected.getText());
	}

	/** Current player spellbook. */
	@RequiredArgsConstructor
	public enum Spellbook
	{
		STANDARD(0),
		ANCIENT(1),
		LUNAR(2),
		ARCEUUS(3);

		private final int value;

		private static Spellbook from(int value)
		{
			for (Spellbook spellbook : values())
			{
				if (spellbook.value == value)
				{
					return spellbook;
				}
			}
			return null;
		}
	}

	/** Immutable magic state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return current spellbook, or null for an unknown/unavailable value */
		private final Spellbook spellbook;
		/** @return whether an autocast spell is set */
		private final boolean autocastSet;
		/** @return current autocast spell object ID, or -1 when unavailable */
		private final int autocastSpellObjectId;
		/** @return current autocast spell name, or null when unresolved/not set */
		private final String autocastSpellName;
		/** @return whether defensive autocasting is enabled */
		private final boolean defensiveAutocast;
		/** @return whether the selected client widget is a spell */
		private final boolean spellSelected;
		/** @return selected widget component ID, or -1 when none/unavailable */
		private final int selectedWidgetId;
		/** @return copied selected widget name, or null when none */
		private final String selectedWidgetName;
		/** @return copied selected widget text, or null when none */
		private final String selectedWidgetText;
	}
}
