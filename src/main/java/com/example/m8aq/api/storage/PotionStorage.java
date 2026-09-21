package com.example.m8aq.api.storage;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.EnumComposition;
import net.runelite.api.EnumID;
import net.runelite.api.GameState;
import net.runelite.api.ItemComposition;
import net.runelite.api.ScriptID;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;
import com.example.m8aq.api.ui.Interfaces;

/** Provides a read-only snapshot of the bank's separate potion storage and loaded widgets. */
public final class PotionStorage
{
	private static final int[] FAVOURITE_VARPS =
	{
		VarPlayerID.POTIONSTORE_FAVOURITE_1,
		VarPlayerID.POTIONSTORE_FAVOURITE_2,
		VarPlayerID.POTIONSTORE_FAVOURITE_3,
		VarPlayerID.POTIONSTORE_FAVOURITE_4,
		VarPlayerID.POTIONSTORE_FAVOURITE_5,
		VarPlayerID.POTIONSTORE_FAVOURITE_6,
		VarPlayerID.POTIONSTORE_FAVOURITE_7,
		VarPlayerID.POTIONSTORE_FAVOURITE_8
	};

	private PotionStorage()
	{
	}

	/**
	 * Reads potion storage using the maintained enums and scripts used by RuneLite's bank tools.
	 * Account-persistent values are suppressed while logged out.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable potion-storage snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		boolean accountStateLoaded = client.getGameState() == GameState.LOGGED_IN;
		Bank.InteractionState interaction = Bank.readInteractionState(client, accountStateLoaded);
		Widget root = client.getWidget(InterfaceID.Bankmain.POTIONSTORE_CONTAINER);
		EnumComposition finished = accountStateLoaded ? client.getEnum(EnumID.POTIONSTORE_POTIONS) : null;
		EnumComposition unfinished = accountStateLoaded
			? client.getEnum(EnumID.POTIONSTORE_UNFINISHED_POTIONS) : null;
		boolean catalogueLoaded = hasValues(finished) && hasValues(unfinished);

		List<PotionEntry> mutablePotions = new ArrayList<>();
		if (catalogueLoaded)
		{
			readEnum(client, finished, mutablePotions);
			readEnum(client, unfinished, mutablePotions);
		}
		List<PotionEntry> potions = Collections.unmodifiableList(mutablePotions);
		long totalDoses = 0;
		for (PotionEntry potion : potions)
		{
			totalDoses += potion.doses;
		}
		List<Interfaces.WidgetEntry> widgets = Interfaces.snapshot(root);

		return new State(
			root != null && !root.isHidden(),
			accountStateLoaded,
			catalogueLoaded,
			accountStateLoaded && client.getVarbitValue(VarbitID.MM_POTION_STORAGE_UNLOCKED) == 1,
			accountStateLoaded ? client.getVarpValue(VarPlayerID.POTIONSTORE_VIALS) : 0,
			potions,
			potions.size(),
			totalDoses,
			accountStateLoaded ? readFavourites(client) : Collections.emptyList(),
			interaction.getWithdrawalMode(),
			interaction.getCustomQuantity(),
			interaction.isAlwaysDepositPotionsEnabled(),
			widgets,
			Interfaces.widgetSnapshot(widgets));
	}

	private static boolean hasValues(EnumComposition catalogue)
	{
		return catalogue != null && catalogue.getIntVals() != null;
	}

	private static void readEnum(Client client, EnumComposition catalogue, List<PotionEntry> potions)
	{
		for (int potionEnumId : catalogue.getIntVals())
		{
			EnumComposition potion = client.getEnum(potionEnumId);
			if (potion == null)
			{
				continue;
			}
			client.runScript(ScriptID.POTIONSTORE_DOSES, potionEnumId);
			int doses = client.getIntStack()[0];
			client.runScript(ScriptID.POTIONSTORE_WITHDRAW_DOSES, potionEnumId);
			int withdrawDoses = client.getIntStack()[0];
			if (doses > 0 && withdrawDoses > 0)
			{
				int itemId = potion.getIntValue(withdrawDoses);
				ItemComposition item = client.getItemDefinition(itemId);
				potions.add(new PotionEntry(potionEnumId, itemId,
					item == null ? null : item.getName(), doses, withdrawDoses));
			}
		}
	}

	private static List<Favourite> readFavourites(Client client)
	{
		List<Favourite> favourites = new ArrayList<>(FAVOURITE_VARPS.length);
		for (int index = 0; index < FAVOURITE_VARPS.length; index++)
		{
			int itemId = client.getVarpValue(FAVOURITE_VARPS[index]);
			ItemComposition item = itemId < 0 ? null : client.getItemDefinition(itemId);
			favourites.add(new Favourite(index, itemId, item == null ? null : item.getName()));
		}
		return Collections.unmodifiableList(favourites);
	}

	/** Immutable stored-potion entry. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class PotionEntry
	{
		/** @return raw potion-definition enum ID */ private final int potionEnumId;
		/** @return item ID at the configured withdrawal dose */ private final int itemId;
		/** @return canonical potion name, or null */ private final String itemName;
		/** @return total stored doses */ private final int doses;
		/** @return doses per configured withdrawal item */ private final int withdrawDoses;
	}

	/** Immutable configured favourite potion slot. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class Favourite
	{
		/** @return zero-based favourite position */ private final int index;
		/** @return raw favourite item ID, or a negative empty sentinel */ private final int itemId;
		/** @return canonical item name, or null */ private final String itemName;
	}

	/** Immutable potion-storage state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State extends Interfaces.WidgetLookup
	{
		/** @return whether potion storage is visibly open */ private final boolean open;
		/** @return whether account-persistent state was safe to read */ private final boolean accountStateLoaded;
		/** @return whether both maintained potion catalogues were available */ private final boolean catalogueLoaded;
		/** @return whether the account has unlocked potion storage */ private final boolean unlocked;
		/** @return stored empty-vial count */ private final int emptyVials;
		/** @return immutable stored potions with positive doses */ private final List<PotionEntry> potions;
		/** @return number of potion types with positive stored doses */ private final int storedPotionCount;
		/** @return total doses across stored potion types */ private final long totalDoses;
		/** @return immutable eight-entry favourites, or empty while account state is unavailable */
		private final List<Favourite> favourites;
		/** @return selected bank withdrawal quantity used by potion storage */
		private final Bank.WithdrawalMode withdrawalMode;
		/** @return configured bank X quantity used by potion storage */ private final int customQuantity;
		/** @return whether applicable bank deposits route to potion storage */
		private final boolean alwaysDepositEnabled;
		/** @return every loaded fixed and dynamic widget under the potion-storage panel */
		private final List<Interfaces.WidgetEntry> widgets;
		@Getter(AccessLevel.PROTECTED)
		private final transient Interfaces.WidgetSnapshot widgetSnapshot;

	}
}
