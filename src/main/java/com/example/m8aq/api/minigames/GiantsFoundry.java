package com.example.m8aq.api.minigames;

import java.util.ArrayList;
import java.util.Collections;
import java.util.EnumMap;
import java.util.EnumSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;
import net.runelite.api.Player;
import net.runelite.api.Skill;
import net.runelite.api.gameval.AnimationID;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.Widget;

/** Provides a read-only snapshot of the client-observable Giants' Foundry state. */
public final class GiantsFoundry
{
	private static final int GIANTS_FOUNDRY_REGION_ID = 13491;
	private static final int CRUCIBLE_CAPACITY = 28;
	private static final int MAX_COMPLETION = 1000;
	private static final int MAX_DIFFICULTY = 130;
	private static final int BONUS_AVAILABLE_COLOR = 0xfcd703;
	private static final int BONUS_CLAIMED_COLOR = 0x00dd00;

	private GiantsFoundry()
	{
	}

	/**
	 * Reads the current Giants' Foundry state from the client.
	 * This method must be called on the client thread.
	 *
	 * @param client RuneLite client
	 * @return immutable state snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");

		int commissionWord1Value = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_COMMISSION_WORD_1);
		int commissionWord2Value = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_COMMISSION_WORD_2);
		EnumSet<CommissionType> commissionTypes = EnumSet.noneOf(CommissionType.class);
		addCommissionType(commissionTypes, commissionWord1Value);
		addCommissionType(commissionTypes, commissionWord2Value);

		EnumMap<Bar, Integer> crucibleContents = new EnumMap<>(Bar.class);
		for (Bar bar : Bar.values())
		{
			int count = client.getVarbitValue(bar.varbit);
			if (count != 0)
			{
				crucibleContents.put(bar, count);
			}
		}

		EnumMap<MouldPart, Integer> mouldSelectionValues = new EnumMap<>(MouldPart.class);
		EnumMap<MouldPart, Mould> selectedMoulds = new EnumMap<>(MouldPart.class);
		readMould(client, MouldPart.RICASSO, mouldSelectionValues, selectedMoulds);
		readMould(client, MouldPart.BLADE, mouldSelectionValues, selectedMoulds);
		readMould(client, MouldPart.TIP, mouldSelectionValues, selectedMoulds);

		EnumSet<Mould> unlockedMoulds = EnumSet.noneOf(Mould.class);
		EnumSet<Mould> usableMoulds = EnumSet.noneOf(Mould.class);
		int smithingLevel = client.getBoostedSkillLevel(Skill.SMITHING);
		for (Mould mould : Mould.values())
		{
			if (mould.unlockVarbit == 0 || client.getVarbitValue(mould.unlockVarbit) == 1)
			{
				unlockedMoulds.add(mould);
				if (smithingLevel >= mould.requiredSmithingLevel)
				{
					usableMoulds.add(mould);
				}
			}
		}

		int workflowStateValue = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_MOULD_STATE);
		WorkflowState workflowState = WorkflowState.fromValue(workflowStateValue);
		int crucibleStateValue = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_CRUCIBLE_STATE);
		CrucibleState crucibleState = CrucibleState.fromValue(crucibleStateValue);
		int difficulty = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_PREFORM_DIFICULTY);
		int quality = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_PREFORM_QUALITY);
		int startingQuality = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_PREFORM_START_QUALITY);
		int temperature = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_PREFORM_TEMPERATURE);
		int completion = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_PREFORM_COMPLETION);
		List<Integer> toolSeedValues = new ArrayList<>(7);
		for (int section = 0; section <= 6; section++)
		{
			toolSeedValues.add(client.getVarbitValue(seedVarbit(section)));
		}
		boolean preformStored = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_PREFORM_STORED) == 1;
		boolean refining = isVisible(client.getWidget(InterfaceID.GiantsFoundryHud.UNIVERSE));
		boolean mouldSetupOpen = isVisible(client.getWidget(InterfaceID.GiantsFoundryMould.UNIVERSE));
		boolean rewardShopOpen = isVisible(client.getWidget(InterfaceID.GiantsFoundryRewardShop.UNIVERSE));

		PreformState preformState = preformState(
			refining, preformStored, completion, quality, startingQuality);
		int sectionCount = preformState == PreformState.NONE ? 0 : sectionCount(difficulty);
		int currentSection = sectionCount == 0 ? 0
			: Math.min(completion * sectionCount / MAX_COMPLETION + 1, sectionCount);
		List<Tool> toolSequence = toolSequence(toolSeedValues, sectionCount);
		Tool requiredTool = Tool.NONE;
		if ((preformState == PreformState.EQUIPPED || preformState == PreformState.STORED)
			&& quality > 0 && completion < MAX_COMPLETION)
		{
			requiredTool = toolSequence.get(currentSection - 1);
		}
		TemperatureRange temperatureRange = temperatureRange(requiredTool, difficulty);

		Player localPlayer = client.getLocalPlayer();
		boolean atGiantsFoundry = localPlayer != null
			&& localPlayer.getWorldLocation() != null
			&& localPlayer.getWorldLocation().getRegionID() == GIANTS_FOUNDRY_REGION_ID;
		Activity activity = atGiantsFoundry
			? activity(localPlayer, requiredTool, preformState)
			: Activity.NONE;
		SweetSpotState sweetSpotState = sweetSpotState(
			client.getWidget(InterfaceID.GiantsFoundryHud.SWEETSPOT_LAYER));

		int crucibleBarCount = crucibleContents.values().stream()
			.mapToInt(Integer::intValue)
			.sum();
		Phase phase = phase(
			preformState,
			workflowState,
			mouldSetupOpen,
			atGiantsFoundry,
			commissionTypes,
			crucibleBarCount,
			selectedMoulds);
		int mouldTabValue = client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_MOULD_CURRENT_TAB);

		return new State(
			Collections.unmodifiableSet(commissionTypes),
			commissionWord1Value,
			commissionWord2Value,
			Collections.unmodifiableMap(crucibleContents),
			crucibleBarCount,
			crucibleStateValue,
			crucibleState,
			mouldTabValue,
			MouldPart.fromTab(mouldTabValue),
			Collections.unmodifiableMap(mouldSelectionValues),
			Collections.unmodifiableMap(selectedMoulds),
			Collections.unmodifiableSet(unlockedMoulds),
			Collections.unmodifiableSet(usableMoulds),
			client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_MOULD_ALLOY_DIFICULTY),
			workflowStateValue,
			workflowState,
			phase,
			mouldSetupOpen,
			refining,
			preformStored,
			preformState,
			temperature,
			completion,
			difficulty,
			quality,
			startingQuality,
			Math.max(0, startingQuality - quality),
			sectionCount,
			currentSection,
			Collections.unmodifiableList(toolSeedValues),
			Collections.unmodifiableList(toolSequence),
			requiredTool,
			temperatureRange.minimum,
			temperatureRange.maximum,
			temperatureStatus(temperatureRange, temperature),
			sweetSpotState,
			completion >= MAX_COMPLETION,
			activity,
			client.getVarbitValue(VarbitID.COLLECTION_MINIGAMES_GIANTSFOUNDRY_COMPLETED) == 1,
			client.getVarpValue(VarPlayerID.GIANTS_FOUNDRY_REWARD_SHOP_POINTS),
			client.getVarbitValue(VarbitID.GIANTS_FOUNDRY_REWARD_SHOP_FILTER),
			client.getVarpValue(VarPlayerID.GIANTS_FOUNDRY_REWARD_SHOP_SELECTED_ITEM),
			rewardShopOpen,
			atGiantsFoundry);
	}

	private static void addCommissionType(Set<CommissionType> types, int value)
	{
		CommissionType type = CommissionType.fromValue(value);
		if (type != null)
		{
			types.add(type);
		}
	}

	private static void readMould(
		Client client,
		MouldPart part,
		Map<MouldPart, Integer> values,
		Map<MouldPart, Mould> selected)
	{
		int value = client.getVarbitValue(part.selectionVarbit);
		values.put(part, value);
		Mould mould = Mould.fromSelection(part, value);
		if (mould != null)
		{
			selected.put(part, mould);
		}
	}

	private static boolean isVisible(Widget widget)
	{
		return widget != null && !widget.isHidden();
	}

	private static SweetSpotState sweetSpotState(Widget widget)
	{
		if (!isVisible(widget) || widget.getChildren() == null || widget.getChildren().length == 0)
		{
			return SweetSpotState.NONE;
		}
		Widget child = widget.getChildren()[0];
		if (child == null)
		{
			return SweetSpotState.NONE;
		}
		switch (child.getTextColor())
		{
			case BONUS_AVAILABLE_COLOR:
				return SweetSpotState.AVAILABLE;
			case BONUS_CLAIMED_COLOR:
				return SweetSpotState.CLAIMED;
			default:
				return SweetSpotState.UNKNOWN;
		}
	}

	private static PreformState preformState(
		boolean refining,
		boolean stored,
		int completion,
		int quality,
		int startingQuality)
	{
		if (completion >= MAX_COMPLETION)
		{
			return PreformState.COMPLETED;
		}
		if (startingQuality > 0 && quality <= 0)
		{
			return PreformState.RUINED;
		}
		if (stored)
		{
			return PreformState.STORED;
		}
		if (refining)
		{
			return PreformState.EQUIPPED;
		}
		return PreformState.NONE;
	}

	private static Phase phase(
		PreformState preformState,
		WorkflowState workflowState,
		boolean mouldSetupOpen,
		boolean atGiantsFoundry,
		Set<CommissionType> commissionTypes,
		int crucibleBarCount,
		Map<MouldPart, Mould> selectedMoulds)
	{
		switch (preformState)
		{
			case COMPLETED:
				return Phase.READY_TO_HAND_IN;
			case RUINED:
				return Phase.RUINED;
			case STORED:
				return Phase.STORED;
			case EQUIPPED:
				return Phase.REFINING;
			default:
				break;
		}

		if (mouldSetupOpen)
		{
			return Phase.SELECTING_MOULDS;
		}

		switch (workflowState)
		{
			case MOULD_SET:
				return Phase.MOULD_SET;
			case PREFORM_READY:
				return Phase.PREFORM_READY;
			case COLLECTING_PREFORM:
				return Phase.COLLECTING_PREFORM;
			default:
				break;
		}

		if (!commissionTypes.isEmpty() || crucibleBarCount > 0 || !selectedMoulds.isEmpty())
		{
			return Phase.PREPARING;
		}
		return atGiantsFoundry ? Phase.AWAITING_COMMISSION : Phase.INACTIVE;
	}

	private static int seedVarbit(int section)
	{
		switch (section)
		{
			case 0:
				return VarbitID.GIANTS_FOUNDRY_SEED_0;
			case 1:
				return VarbitID.GIANTS_FOUNDRY_SEED_1;
			case 2:
				return VarbitID.GIANTS_FOUNDRY_SEED_2;
			case 3:
				return VarbitID.GIANTS_FOUNDRY_SEED_3;
			case 4:
				return VarbitID.GIANTS_FOUNDRY_SEED_4;
			case 5:
				return VarbitID.GIANTS_FOUNDRY_SEED_5;
			case 6:
				return VarbitID.GIANTS_FOUNDRY_SEED_6;
			default:
				throw new IllegalArgumentException("section must be between 0 and 6");
		}
	}

	private static List<Tool> toolSequence(List<Integer> seeds, int sectionCount)
	{
		List<Tool> tools = new ArrayList<>(sectionCount);
		for (int section = 0; section < sectionCount; section++)
		{
			tools.add(section == 0 ? Tool.TRIP_HAMMER : Tool.fromSeed(seeds.get(section)));
		}
		return tools;
	}

	private static int sectionCount(int difficulty)
	{
		if (difficulty < 20)
		{
			return 3;
		}
		if (difficulty < 60)
		{
			return 4;
		}
		if (difficulty < 90)
		{
			return 5;
		}
		if (difficulty < 120)
		{
			return 6;
		}
		return 7;
	}

	private static TemperatureRange temperatureRange(Tool tool, int difficulty)
	{
		if (tool == Tool.NONE)
		{
			return new TemperatureRange(-1, -1);
		}

		int third = MAX_COMPLETION / 3;
		int half = third - third / 2;
		int width = third - difficulty * half / MAX_DIFFICULTY;
		int minimum = third / 2 + third * tool.temperatureBand - width / 2;
		return new TemperatureRange(minimum, minimum + width);
	}

	private static TemperatureStatus temperatureStatus(TemperatureRange range, int temperature)
	{
		if (range.minimum < 0)
		{
			return TemperatureStatus.UNKNOWN;
		}
		if (temperature < range.minimum)
		{
			return TemperatureStatus.TOO_COLD;
		}
		if (temperature > range.maximum)
		{
			return TemperatureStatus.TOO_HOT;
		}
		return TemperatureStatus.READY;
	}

	private static Activity activity(
		Player player,
		Tool requiredTool,
		PreformState preformState)
	{
		if (player == null)
		{
			return Activity.NONE;
		}

		switch (player.getAnimation())
		{
			case AnimationID.HUMAN_PICKUPFLOOR:
				return preformState == PreformState.EQUIPPED
					? Activity.HEATING : Activity.NONE;
			case AnimationID.HUMAN_PICKUPTABLE:
				return preformState == PreformState.EQUIPPED
					? Activity.COOLING : Activity.NONE;
			case AnimationID.GIANTS_FOUNDRY_CRUCIBLE_POUR01:
				return Activity.POURING;
			case AnimationID.HUMAN_PREFORM_LIFT:
				return Activity.COLLECTING_PREFORM;
			case AnimationID.GIANTS_FOUNDRY_FINISH_SWORD:
				return Activity.FINISHING;
			case AnimationID.GIANTS_FOUNDRY_WORK_PREFORM:
			case AnimationID.GIANTS_FOUNDRY_WORK_PREFORM_LOOP:
			case AnimationID.HUMAN_PREFORM_LIFT_START:
			case AnimationID.HUMAN_PREFORM_LIFT_START02:
			case AnimationID.HUMAN_PREFORM_LIFT_LOOP:
			case AnimationID.HUMAN_PREFORM_LIFT_LOOP02:
				return Activity.forTool(requiredTool);
			default:
				return Activity.NONE;
		}
	}

	private static final class TemperatureRange
	{
		private final int minimum;
		private final int maximum;

		private TemperatureRange(int minimum, int maximum)
		{
			this.minimum = minimum;
			this.maximum = maximum;
		}
	}

	/** Commission descriptors used to score mould choices. */
	public enum CommissionType
	{
		NARROW(1),
		LIGHT(2),
		FLAT(3),
		BROAD(4),
		HEAVY(5),
		SPIKED(6);

		private final int value;

		CommissionType(int value)
		{
			this.value = value;
		}

		private static CommissionType fromValue(int value)
		{
			for (CommissionType type : values())
			{
				if (type.value == value)
				{
					return type;
				}
			}
			return null;
		}
	}

	/** Metal bars currently held by the crucible. */
	public enum Bar
	{
		BRONZE(VarbitID.GIANTS_FOUNDRY_BRONZE_COUNT),
		IRON(VarbitID.GIANTS_FOUNDRY_IRON_COUNT),
		STEEL(VarbitID.GIANTS_FOUNDRY_STEEL_COUNT),
		MITHRIL(VarbitID.GIANTS_FOUNDRY_MITHRIL_COUNT),
		ADAMANT(VarbitID.GIANTS_FOUNDRY_ADAMANT_COUNT),
		RUNE(VarbitID.GIANTS_FOUNDRY_RUNE_COUNT);

		private final int varbit;

		Bar(int varbit)
		{
			this.varbit = varbit;
		}
	}

	/** Server-backed crucible fill state. */
	public enum CrucibleState
	{
		EMPTY(0),
		PARTIALLY_FILLED(1),
		FULL(2),
		UNKNOWN(-1);

		private final int value;

		CrucibleState(int value)
		{
			this.value = value;
		}

		private static CrucibleState fromValue(int value)
		{
			for (CrucibleState state : values())
			{
				if (state.value == value)
				{
					return state;
				}
			}
			return UNKNOWN;
		}
	}

	/** Mould category and its selection varbit. */
	public enum MouldPart
	{
		RICASSO(0, VarbitID.GIANTS_FOUNDRY_MOULD_SELECTED_RICASSO),
		BLADE(1, VarbitID.GIANTS_FOUNDRY_MOULD_SELECTED_BLADE),
		TIP(2, VarbitID.GIANTS_FOUNDRY_MOULD_SELECTED_TIP),
		UNKNOWN(-1, -1);

		private final int tab;
		private final int selectionVarbit;

		MouldPart(int tab, int selectionVarbit)
		{
			this.tab = tab;
			this.selectionVarbit = selectionVarbit;
		}

		private static MouldPart fromTab(int tab)
		{
			for (MouldPart part : values())
			{
				if (part.tab == tab)
				{
					return part;
				}
			}
			return UNKNOWN;
		}
	}

	/** Exact current mould catalogue and server selection keys. */
	@Getter
	public enum Mould
	{
		CHOPPER_FORTE("Chopper Forte", MouldPart.RICASSO, 11, 15, 0),
		GLADIUS_RICASSO("Gladius Ricasso", MouldPart.RICASSO, 10, 15, 0),
		DISARMING_FORTE("Disarming Forte", MouldPart.RICASSO, 9, 16, 0),
		MEDUSA_RICASSO("Medusa Ricasso", MouldPart.RICASSO, 8, 30, 0),
		SERPENT_RICASSO("Serpent Ricasso", MouldPart.RICASSO, 7, 30, 0),
		SERRATED_FORTE("Serrated Forte", MouldPart.RICASSO, 6, 31, 0),
		STILETTO_FORTE("Stiletto Forte", MouldPart.RICASSO, 5, 50,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_RICASSO_7),
		DEFENDER_BASE("Defender Base", MouldPart.RICASSO, 4, 51,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_RICASSO_8),
		JUGGERNAUT_FORTE("Juggernaut Forte", MouldPart.RICASSO, 3, 61,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_RICASSO_9),
		CHOPPER_FORTE_PLUS_ONE("Chopper Forte +1", MouldPart.RICASSO, 2, 70,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_RICASSO_10),
		SPIKER("Spiker!", MouldPart.RICASSO, 1, 79,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_RICASSO_11),
		SAW_BLADE("Saw Blade", MouldPart.BLADE, 11, 15, 0),
		DEFENDERS_EDGE("Defenders Edge", MouldPart.BLADE, 10, 15, 0),
		FISH_BLADE("Fish Blade", MouldPart.BLADE, 9, 16, 0),
		MEDUSA_BLADE("Medusa Blade", MouldPart.BLADE, 8, 29, 0),
		STILETTO_BLADE("Stiletto Blade", MouldPart.BLADE, 7, 30, 0),
		GLADIUS_EDGE("Gladius Edge", MouldPart.BLADE, 6, 31, 0),
		FLAMBERGE_BLADE("Flamberge Blade", MouldPart.BLADE, 5, 48,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_BLADE_7),
		SERPENT_BLADE("Serpent Blade", MouldPart.BLADE, 4, 50,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_BLADE_8),
		CLAYMORE_BLADE("Claymore Blade", MouldPart.BLADE, 3, 59,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_BLADE_9),
		FLEUR_DE_BLADE("Fleur de Blade", MouldPart.BLADE, 2, 71,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_BLADE_10),
		CHOPPA("Choppa!", MouldPart.BLADE, 1, 80,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_BLADE_11),
		PEOPLE_POKER_POINT("People Poker Point", MouldPart.TIP, 11, 15, 0),
		CHOPPER_TIP("Chopper Tip", MouldPart.TIP, 10, 15, 0),
		MEDUSAS_HEAD("Medusa's Head", MouldPart.TIP, 9, 16, 0),
		SERPENTS_FANG("Serpent's Fang", MouldPart.TIP, 8, 29, 0),
		GLADIUS_POINT("Gladius Point", MouldPart.TIP, 7, 30, 0),
		SAW_TIP("Saw Tip", MouldPart.TIP, 6, 31, 0),
		CORRUPTED_POINT("Corrupted Point", MouldPart.TIP, 5, 49,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_TIP_7),
		DEFENDERS_TIP("Defenders Tip", MouldPart.TIP, 4, 52,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_TIP_8),
		SERRATED_TIP("Serrated Tip", MouldPart.TIP, 3, 60,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_TIP_9),
		NEEDLE_POINT("Needle Point", MouldPart.TIP, 2, 69,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_TIP_10),
		THE_POINT("The Point!", MouldPart.TIP, 1, 81,
			VarbitID.GIANTS_FOUNDRY_UNLOCKED_MOULD_TIP_11);

		private final String name;
		private final MouldPart part;
		private final int selectionValue;
		private final int requiredSmithingLevel;
		private final int unlockVarbit;

		Mould(
			String name,
			MouldPart part,
			int selectionValue,
			int requiredSmithingLevel,
			int unlockVarbit)
		{
			this.name = name;
			this.part = part;
			this.selectionValue = selectionValue;
			this.requiredSmithingLevel = requiredSmithingLevel;
			this.unlockVarbit = unlockVarbit;
		}

		private static Mould fromSelection(MouldPart part, int value)
		{
			for (Mould mould : values())
			{
				if (mould.part == part && mould.selectionValue == value)
				{
					return mould;
				}
			}
			return null;
		}
	}

	/** Raw server workflow value for preparation and preform collection. */
	public enum WorkflowState
	{
		PREPARATION_OR_REFINING(0),
		MOULD_SET(1),
		PREFORM_READY(2),
		COLLECTING_PREFORM(3),
		UNKNOWN(-1);

		private final int value;

		WorkflowState(int value)
		{
			this.value = value;
		}

		private static WorkflowState fromValue(int value)
		{
			for (WorkflowState state : values())
			{
				if (state.value == value)
				{
					return state;
				}
			}
			return UNKNOWN;
		}
	}

	/** Best unambiguous lifecycle phase derived from all current client state. */
	public enum Phase
	{
		INACTIVE,
		AWAITING_COMMISSION,
		PREPARING,
		SELECTING_MOULDS,
		MOULD_SET,
		PREFORM_READY,
		COLLECTING_PREFORM,
		REFINING,
		STORED,
		RUINED,
		READY_TO_HAND_IN
	}

	/** Current preform ownership and terminal state. */
	public enum PreformState
	{
		NONE,
		EQUIPPED,
		STORED,
		RUINED,
		COMPLETED
	}

	/** Refinement tool required by the current completion section. */
	public enum Tool
	{
		NONE(-1),
		TRIP_HAMMER(2),
		GRINDSTONE(1),
		POLISHING_WHEEL(0);

		private final int temperatureBand;

		Tool(int temperatureBand)
		{
			this.temperatureBand = temperatureBand;
		}

		private static Tool fromSeed(int seed)
		{
			switch (seed)
			{
				case 0:
					return TRIP_HAMMER;
				case 1:
					return GRINDSTONE;
				case 2:
					return POLISHING_WHEEL;
				default:
					return NONE;
			}
		}
	}

	/** Current temperature relative to the required tool's valid range. */
	public enum TemperatureStatus
	{
		UNKNOWN,
		TOO_COLD,
		READY,
		TOO_HOT
	}

	/** Current sweet-spot border state generated by the Foundry HUD. */
	public enum SweetSpotState
	{
		NONE,
		AVAILABLE,
		CLAIMED,
		UNKNOWN
	}

	/** Current local-player action that can be observed without event history. */
	public enum Activity
	{
		NONE,
		HEATING,
		COOLING,
		POURING,
		COLLECTING_PREFORM,
		HAMMERING,
		GRINDING,
		POLISHING,
		FINISHING;

		private static Activity forTool(Tool tool)
		{
			switch (tool)
			{
				case TRIP_HAMMER:
					return HAMMERING;
				case GRINDSTONE:
					return GRINDING;
				case POLISHING_WHEEL:
					return POLISHING;
				default:
					return NONE;
			}
		}
	}

	/** Immutable Giants' Foundry state captured from one client read. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return immutable set of active commission descriptors */
		private final Set<CommissionType> commissionTypes;
		/** @return raw first commission-word varbit value */
		private final int commissionWord1Value;
		/** @return raw second commission-word varbit value */
		private final int commissionWord2Value;
		/** @return immutable map containing nonzero crucible bar counts */
		private final Map<Bar, Integer> crucibleContents;
		/** @return total number of bars represented in the crucible */
		private final int crucibleBarCount;
		/** @return raw crucible-state varbit value */
		private final int crucibleStateValue;
		/** @return decoded crucible fill state */
		private final CrucibleState crucibleState;
		/** @return raw mould tab value */
		private final int mouldTabValue;
		/** @return selected mould-interface tab, or UNKNOWN */
		private final MouldPart mouldTab;
		/** @return immutable raw 0-11 selection values for all mould parts */
		private final Map<MouldPart, Integer> mouldSelectionValues;
		/** @return immutable map of recognised nonzero mould selections */
		private final Map<MouldPart, Mould> selectedMoulds;
		/** @return immutable set of moulds whose purchase/unlock gate is satisfied */
		private final Set<Mould> unlockedMoulds;
		/** @return immutable set of unlocked moulds usable at the current Smithing level */
		private final Set<Mould> usableMoulds;
		/** @return raw mould alloy difficulty */
		private final int mouldAlloyDifficulty;
		/** @return raw workflow-state varbit value */
		private final int workflowStateValue;
		/** @return decoded server workflow state */
		private final WorkflowState workflowState;
		/** @return best unambiguous lifecycle phase */
		private final Phase phase;
		/** @return whether the mould setup interface is visible */
		private final boolean mouldSetupOpen;
		/** @return whether the refinement HUD is visible */
		private final boolean refining;
		/** @return whether the current preform is in Foundry storage */
		private final boolean preformStored;
		/** @return current preform ownership or terminal state */
		private final PreformState preformState;
		/** @return raw preform temperature on the 0-1000 scale */
		private final int temperature;
		/** @return raw preform completion on the 0-1000 scale */
		private final int completion;
		/** @return preform difficulty used to size sections and temperature bands */
		private final int difficulty;
		/** @return current preform quality */
		private final int quality;
		/** @return quality assigned when the preform was poured */
		private final int startingQuality;
		/** @return nonnegative quality lost from the starting value */
		private final int qualityLost;
		/** @return number of refinement sections, or zero without a preform */
		private final int sectionCount;
		/** @return one-based current section, or zero without a preform */
		private final int currentSection;
		/** @return immutable raw seed values for sections zero through six */
		private final List<Integer> toolSeedValues;
		/** @return immutable required-tool sequence for the current preform */
		private final List<Tool> toolSequence;
		/** @return tool required by the current completion section */
		private final Tool requiredTool;
		/** @return inclusive minimum valid temperature, or -1 when unavailable */
		private final int minimumTemperature;
		/** @return inclusive maximum valid temperature, or -1 when unavailable */
		private final int maximumTemperature;
		/** @return current temperature relative to the required tool range */
		private final TemperatureStatus temperatureStatus;
		/** @return complete sweet-spot border state */
		private final SweetSpotState sweetSpotState;
		/** @return whether completion has reached 1000 */
		private final boolean readyToHandIn;
		/** @return currently observable local-player Foundry action */
		private final Activity activity;
		/** @return whether the Giants' Foundry collection-log section is complete */
		private final boolean collectionLogCompleted;
		/** @return current Foundry reputation points */
		private final int rewardPoints;
		/** @return raw reward-shop filter value */
		private final int rewardShopFilter;
		/** @return raw selected reward-shop item value */
		private final int rewardShopSelectedItem;
		/** @return whether the reward shop interface is visible */
		private final boolean rewardShopOpen;
		/** @return whether the local player is in the Giants' Foundry region */
		private final boolean atGiantsFoundry;

		/** @return preform temperature as a percentage */
		public double getTemperaturePercent()
		{
			return temperature / 10.0;
		}

		/** @return preform completion as a percentage */
		public double getCompletionPercent()
		{
			return completion / 10.0;
		}

		/** @return whether an unclaimed sweet-spot bonus is active */
		public boolean isBonusActive()
		{
			return sweetSpotState == SweetSpotState.AVAILABLE;
		}

		/**
		 * Returns a crucible bar count.
		 *
		 * @param bar bar type to query
		 * @return stored quantity, or zero
		 */
		public int getCrucibleCount(Bar bar)
		{
			return crucibleContents.getOrDefault(bar, 0);
		}

		/** @return whether the crucible contains its full 28 bars */
		public boolean isCrucibleFull()
		{
			return crucibleBarCount == CRUCIBLE_CAPACITY;
		}

		/** @return whether all three mould parts have recognised selections */
		public boolean isMouldSelectionComplete()
		{
			return selectedMoulds.size() == 3;
		}
	}
}
