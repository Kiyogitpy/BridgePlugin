package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Actor;
import net.runelite.api.Client;
import net.runelite.api.GameObject;
import net.runelite.api.GameState;
import net.runelite.api.GraphicsObject;
import net.runelite.api.ActorSpotAnim;
import net.runelite.api.Item;
import net.runelite.api.ItemComposition;
import net.runelite.api.ItemContainer;
import net.runelite.api.InventoryID;
import net.runelite.api.MenuAction;
import net.runelite.api.MenuEntry;
import net.runelite.api.NPC;
import net.runelite.api.NPCComposition;
import net.runelite.api.ObjectComposition;
import net.runelite.api.Player;
import net.runelite.api.Skill;
import net.runelite.api.TileObject;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
import net.runelite.api.events.AnimationChanged;
import net.runelite.api.events.ChatMessage;
import net.runelite.api.events.FocusChanged;
import net.runelite.api.events.GameObjectDespawned;
import net.runelite.api.events.GameObjectSpawned;
import net.runelite.api.events.GameStateChanged;
import net.runelite.api.events.GameTick;
import net.runelite.api.events.GraphicsObjectCreated;
import net.runelite.api.events.InteractingChanged;
import net.runelite.api.events.ItemContainerChanged;
import net.runelite.api.events.MenuOptionClicked;
import net.runelite.api.events.StatChanged;
import net.runelite.api.events.VarbitChanged;
import net.runelite.api.widgets.Widget;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.input.KeyManager;
import net.runelite.client.input.MouseAdapter;
import net.runelite.client.input.MouseManager;
import net.runelite.client.util.Text;

import java.awt.event.KeyEvent;
import java.awt.event.MouseEvent;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Records what a HUMAN does, so a routine can later be written by reading the log instead of
 * reverse-engineering the activity from scratch.
 *
 * Added 2026-08-23 (user request). Every existing provider answers "what does the world look like
 * right now" for a bot that already knows what it wants to do. This one answers the prior
 * question: "what did the player actually do, and what happened as a result" -- captured while a
 * human plays the activity by hand, so writing routines/<name>.py becomes read-the-log-and-build
 * rather than guess-and-live-debug.
 *
 * <h2>Why this lives in Java and not in a Python polling loop</h2>
 * The obvious cheap implementation is Python polling {@code /last-action} every 50ms. That does
 * not work, for three independent reasons:
 * <ol>
 *   <li>{@code /last-action} holds only the MOST RECENT click. Two clicks inside one poll interval
 *       and the first is gone forever. A recorder that silently drops actions is worse than none,
 *       because you cannot tell a dropped action from an action that never happened.</li>
 *   <li>A MenuOptionClicked's {@code param0}/{@code param1} are SCENE coordinates. Converting those
 *       to a real world tile requires the client thread and the scene's own base offset -- and
 *       inside an instance requires {@code WorldPoint.fromLocalInstance} (see the "Instanced areas"
 *       gotcha in CLAUDE.md, hit four times already). Python structurally cannot do this
 *       conversion; by the time it polls, the scene may also have moved on.</li>
 *   <li>Outcome correlation (below) needs to observe events at the instant they fire, ordered
 *       against the click that caused them. A poller sees only end state, never ordering.</li>
 * </ol>
 *
 * <h2>Outcome correlation -- the part that makes the log directly translatable to a routine</h2>
 * A bare list of clicks is not enough to write a routine from: the hard part of every routine in
 * this codebase is not "what to click", it is "how do I know the click WORKED". So when an action
 * is recorded, an outcome window opens for {@link #OUTCOME_WINDOW_TICKS} game ticks. Every ambient
 * record that fires inside that window (animation start, ground spotanim, the clicked object
 * transforming, XP gain, inventory delta, chat line, varbit flip) is tagged with
 * {@code causedBy: <that action's seq>}.
 *
 * Records are still emitted append-only and immediately -- the action is NOT held back until its
 * window closes. Holding it back would give clean pre-grouped output at the cost of reordering the
 * stream and delaying it by seconds; instead every record keeps its true timestamp and ordering,
 * and recorder.py groups by {@code causedBy} when it renders the readable log. This also keeps
 * {@code seq} strictly monotonic, which the {@code ?since=} cursor depends on.
 *
 * Attribution deliberately picks the NEWEST still-open action rather than trying to reason about
 * which of several overlapping clicks "really" caused an effect -- with a ~3s window, overlapping
 * clicks are rare, and guessing wrong silently is worse than a simple, predictable rule the reader
 * of the log can account for.
 *
 * <h2>Volume control</h2>
 * Ambient events are the flood risk, not clicks. Three guards, all necessary:
 * <ul>
 *   <li>{@link #AMBIENT_RADIUS} caps animation/spotanim/object records to nearby tiles -- a busy
 *       bank or a Slayer cave otherwise produces thousands of irrelevant NPC animation records.</li>
 *   <li>Other PLAYERS' animations are dropped entirely (only the local player's are kept). A
 *       crowded world 330 bank would otherwise dominate the log with strangers' idle animations.</li>
 *   <li>{@code GameObjectSpawned} fires once for EVERY object in the scene on login and on each
 *       region load -- thousands of records in one tick, which would evict the entire real
 *       recording out of the ring buffer. {@link #objectSuppressUntilMs} drops object spawn/despawn
 *       records for a moment after any non-LOGGED_IN game state.</li>
 * </ul>
 * Mouse movement is the other high-volume stream (~60-100 samples/sec); moves are batched into
 * polyline records ({@link #MOUSE_BATCH_MAX} points or {@link #MOUSE_FLUSH_MS}, whichever first)
 * rather than one record per sample.
 *
 * <h2>Keystroke privacy</h2>
 * A keylogger that runs on the login screen would write the account password to a plaintext file on
 * disk. So: the key listener reports {@code isEnabledOnLoginScreen() == false}, AND key records are
 * dropped unless the game state is {@code LOGGED_IN}. Typed characters are recorded only while
 * actually in game.
 *
 * <h2>Endpoints</h2>
 * <ul>
 *   <li>{@code GET /recorder/arm?on=true&label=sunstone-mining} -- start capturing (clears the
 *       buffer and resets seq to 0); {@code on=false} stops. Disarmed is the default: every
 *       listener returns immediately, so an unused recorder costs nothing.</li>
 *   <li>{@code GET /recorder/events?since=<seq>&limit=<n>} -- drain records after {@code seq}.
 *       Returns {@code nextSeq} to pass as the next {@code since}, and {@code dropped} (records
 *       evicted by the ring buffer before anyone read them -- nonzero means the reader is too slow
 *       or the buffer is too small, and the recording has a hole in it. Records the reader has
 *       already drained are evicted silently; see {@link RecordRing} for the false alarm that
 *       distinction fixed).</li>
 *   <li>{@code GET /recorder/status} -- armed state, label, buffered count, dropped count.</li>
 * </ul>
 */
public class RecorderProvider extends BridgeProvider
{
	/**
	 * Ring buffer capacity. At a busy ~200 records/sec this is ~100s of slack before eviction --
	 * far more than recorder.py's ~10/sec polling needs, and sized so a brief Python-side stall
	 * (GC, disk flush, the user alt-tabbing) cannot punch a hole in the recording.
	 */
	private static final int MAX_BUFFER = 20000;

	private static final int DEFAULT_LIMIT = 4000;

	/**
	 * Tiles around the player within which ambient animation/spotanim/object records are kept. 12
	 * comfortably covers the visible interaction area without pulling in the whole loaded scene
	 * (which is 104x104).
	 */
	private static final int AMBIENT_RADIUS = 12;

	/**
	 * How long after a click ambient records stay attributed to it. 5 ticks (~3s) covers the slow
	 * cases that matter -- a mining roll, a crafting animation, a bank interface opening -- without
	 * running so long that unrelated background activity gets blamed on the click.
	 */
	private static final int OUTCOME_WINDOW_TICKS = 5;

	private static final long MOUSE_FLUSH_MS = 40;
	private static final int MOUSE_BATCH_MAX = 24;

	/**
	 * How long object spawn/despawn records stay suppressed after a non-LOGGED_IN game state. Region
	 * loads fire a full-scene spawn burst; 1.5s clears it without hiding real in-play changes.
	 */
	private static final long OBJECT_SUPPRESS_MS = 1500;

	private static final int SCENE_SIZE = 104;

	private final EventBus eventBus;
	private final MouseManager mouseManager;
	private final KeyManager keyManager;

	private final RecordRing ring = new RecordRing(MAX_BUFFER);

	private volatile boolean armed = false;
	private volatile String label = null;
	private volatile long epochMs = System.currentTimeMillis();

	/**
	 * Mirrors client.getTickCount() so {@link #base} can stamp a tick from the AWT event thread
	 * (mouse/key listeners), where touching the client directly is not safe.
	 */
	private volatile int tickCount = 0;

	/** Wall-clock time the tick in {@link #tickCount} fired, so {@link #base} can report its age. */
	private volatile long tickStartMs = System.currentTimeMillis();

	private volatile int lastMouseX = -1;
	private volatile int lastMouseY = -1;

	private final List<int[]> mouseBatch = new ArrayList<>();
	private final Object mouseLock = new Object();
	private long mouseBatchStartMs = 0;

	/**
	 * Open outcome windows. Touched only from the client thread (every event that reads or closes a
	 * window -- MenuOptionClicked, GameTick, AnimationChanged, ... -- is a client-thread event), so
	 * it needs no lock. Mouse/key records come from the AWT thread and deliberately do NOT get
	 * attributed, which is what keeps that true.
	 */
	private final List<long[]> openActions = new ArrayList<>();

	private final Map<Integer, Integer> lastXp = new HashMap<>();
	private final Map<Integer, Integer> lastInventory = new HashMap<>();
	private final Map<Integer, Set<Integer>> lastSpotAnims = new HashMap<>();
	private final Map<Integer, NpcState> npcStates = new HashMap<>();

	/**
	 * True once seedBaselines has captured the starting inventory, so onItemContainerChanged knows
	 * whether it has something to diff against. An explicit flag rather than
	 * {@code lastInventory.isEmpty()}: a player who arms the recorder with a genuinely empty
	 * inventory would otherwise be treated as un-baselined forever.
	 */
	private boolean inventoryBaselineSet = false;

	/**
	 * Seq of an action awaiting walk-destination resolution on the next tick, or -1. See
	 * {@link #resolveWalkDestination}.
	 */
	private long pendingWalkSeq = -1;

	/**
	 * The player's walk destination at the moment that action was clicked, so the next tick can tell
	 * an actual new walk from a click that merely happened while already walking.
	 */
	private WorldPoint pendingWalkPrevDest = null;
	private volatile long objectSuppressUntilMs = 0;

	private RecorderMouseListener mouseListener;
	private RecorderKeyListener keyListener;

	public RecorderProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
		EventBus eventBus, MouseManager mouseManager, KeyManager keyManager)
	{
		super(client, clientThread, server);
		this.eventBus = eventBus;
		this.mouseManager = mouseManager;
		this.keyManager = keyManager;
	}

	@Override
	public void register()
	{
		server.registerQuery("/recorder/arm", this::handleArm);
		server.registerQuery("/recorder/events", this::handleEvents);
		server.register("/recorder/status", this::handleStatus);

		eventBus.register(this);

		mouseListener = new RecorderMouseListener();
		mouseManager.registerMouseListener(mouseListener);

		keyListener = new RecorderKeyListener();
		keyManager.registerKeyListener(keyListener);
	}

	// ------------------------------------------------------------------ endpoints

	private Object handleArm(Map<String, String> query)
	{
		String on = query.get("on");
		boolean wantOn = on == null || on.isEmpty() || "1".equals(on) || "true".equalsIgnoreCase(on);

		ring.reset();
		synchronized (mouseLock)
		{
			mouseBatch.clear();
		}

		// Set BEFORE the seeding below, so the baseline record it emits is stamped t~=0 rather than
		// against the previous recording's epoch.
		epochMs = System.currentTimeMillis();
		label = query.get("label");

		// Bounced onto the client thread rather than cleared inline. This method runs on an HTTP
		// worker thread, but openActions/lastXp/lastInventory/lastSpotAnims/npcStates are all
		// client-thread-owned (that ownership is exactly why they need no locks -- see openActions'
		// field comment). Clearing them from here would race the client thread mid-iteration: the
		// diffNearbyNpcs/closeExpiredActions loops would throw ConcurrentModificationException, and
		// the plain HashMaps could corrupt outright. onClient blocks until the reset has actually
		// happened, so the recorder is genuinely at a clean baseline before this returns armed.
		onClient(() -> {
			openActions.clear();
			lastXp.clear();
			lastInventory.clear();
			lastSpotAnims.clear();
			npcStates.clear();
			pendingWalkSeq = -1;
			pendingWalkPrevDest = null;
			inventoryBaselineSet = false;
			if (wantOn)
			{
				seedBaselines();
			}
			return null;
		});

		// Only now -- every listener above returns immediately while disarmed, so nothing can slip
		// into the buffer between the clear and the seed.
		armed = wantOn;

		if (wantOn)
		{
			// Stamps the recording with which ambient kinds it can contain, so tools/humanity_report.py
			// can tell "never recorded" (a recording predating a given kind) apart from "measured
			// zero" (a recording that could have captured it but didn't) instead of reading both as zero.
			Map<String, Object> record = base("recorder");
			record.put("schema", 2);
			record.put("canvasEdges", true);
			record.put("focus", true);
			add(record);
		}

		// Suppress the object-spawn burst that a freshly-armed recorder would otherwise catch if
		// arming happens to coincide with a region load already in flight.
		objectSuppressUntilMs = System.currentTimeMillis() + OBJECT_SUPPRESS_MS;

		return handleStatus();
	}

	/**
	 * Captures the starting inventory and per-skill XP at the moment of arming.
	 *
	 * Without this, the FIRST change of each kind is silently swallowed as the baseline, because
	 * there is nothing to diff it against. CONFIRMED live 2026-08-23 (first real recording,
	 * Falconry): 7 kebbit catches produced a summary reading "+6 Bones" and "Hunter +936" -- one
	 * bone and one 156xp drop short, exactly one catch's worth, because the first
	 * ItemContainerChanged and the first StatChanged per skill were consumed establishing the
	 * baseline instead of being counted.
	 *
	 * Seeding here also means the starting inventory is reported immediately on arm, rather than
	 * whenever the inventory first happens to change (7.7s into that recording, several actions
	 * after the fact, misleadingly labelled as the "starting" state).
	 *
	 * Client thread only -- called from inside handleArm's onClient block.
	 */
	private void seedBaselines()
	{
		for (Skill skill : Skill.values())
		{
			lastXp.put(skill.ordinal(), client.getSkillExperience(skill));
		}

		ItemContainer container = client.getItemContainer(InventoryID.INVENTORY);
		List<Map<String, Object>> starting = new ArrayList<>();
		if (container != null)
		{
			for (Item item : container.getItems())
			{
				if (item != null && item.getId() > 0)
				{
					lastInventory.merge(item.getId(), item.getQuantity(), Integer::sum);
				}
			}
		}
		for (Map.Entry<Integer, Integer> entry : lastInventory.entrySet())
		{
			starting.add(itemDelta(entry.getKey(), entry.getValue()));
		}

		Map<String, Object> record = base("invstart");
		record.put("changes", starting);
		record.put("usedSlots", occupiedSlots(container));
		record.put("freeSlots", 28 - occupiedSlots(container));
		add(record);

		inventoryBaselineSet = true;
	}

	private Object handleEvents(Map<String, String> query)
	{
		// Flush first so the tail of an in-progress mouse batch is never stranded behind the cursor
		// -- otherwise the last few samples before a click only appear on the NEXT poll, after the
		// click record, which would corrupt the very ordering this recorder exists to capture.
		flushMouseBatch();

		long since = parseLong(query.get("since"), -1L);
		int limit = (int) parseLong(query.get("limit"), DEFAULT_LIMIT);
		if (limit <= 0)
		{
			limit = DEFAULT_LIMIT;
		}

		RecordRing.Drain drain = ring.drain(since, limit);

		Map<String, Object> result = new HashMap<>();
		result.put("events", drain.events);
		result.put("nextSeq", drain.nextSeq);
		result.put("dropped", ring.dropped());
		result.put("buffered", ring.size());
		result.put("armed", armed);
		result.put("label", label);
		return result;
	}

	private Object handleStatus()
	{
		Map<String, Object> result = new HashMap<>();
		result.put("buffered", ring.size());
		result.put("nextSeq", ring.nextSeq());
		result.put("dropped", ring.dropped());
		result.put("armed", armed);
		result.put("label", label);
		result.put("epochMs", epochMs);
		result.put("outcomeWindowTicks", OUTCOME_WINDOW_TICKS);
		result.put("ambientRadius", AMBIENT_RADIUS);
		return result;
	}

	private static long parseLong(String value, long fallback)
	{
		if (value == null || value.isEmpty())
		{
			return fallback;
		}
		try
		{
			return Long.parseLong(value);
		}
		catch (NumberFormatException e)
		{
			return fallback;
		}
	}

	// ------------------------------------------------------------------ buffer plumbing

	/**
	 * Every record carries BOTH a time and a tick, plus how old that tick is.
	 *
	 * {@link #tickCount} is a mirror of client.getTickCount() refreshed on GameTick, because
	 * {@link #base} is also called from the AWT event thread (mouse/key listeners) where touching
	 * the client is not safe. A mirror can therefore be stale -- and worse, it is stale in a way
	 * that looks like valid data: a mouse sample taken 500ms into a tick is stamped with that tick
	 * as if it happened at its start, so reading the log by tick alone would silently compress
	 * half a second of real movement into an instant.
	 *
	 * So three fields, not one:
	 * <ul>
	 *   <li>{@code t} -- ms since arming. The authoritative ordering key; monotonic, never stale.</li>
	 *   <li>{@code tick} -- the mirrored game tick, for lining records up against tick-quantised
	 *       game logic.</li>
	 *   <li>{@code tickAge} -- ms elapsed since that tick actually fired. A value near 0 means the
	 *       tick stamp is fresh; a value approaching or exceeding 600 means the record sits late in
	 *       the tick (or the mirror missed an update), and {@code t} should be trusted over
	 *       {@code tick}.</li>
	 * </ul>
	 * {@code wall} is the absolute epoch millisecond, so a recording can be cross-referenced against
	 * an external log (a routine's own output, a screen capture) without needing the arm-time epoch.
	 */
	private Map<String, Object> base(String kind)
	{
		long now = System.currentTimeMillis();
		Map<String, Object> record = new HashMap<>();
		record.put("kind", kind);
		record.put("t", now - epochMs);
		record.put("wall", now);
		record.put("tick", tickCount);
		record.put("tickAge", now - tickStartMs);
		return record;
	}

	private long add(Map<String, Object> record)
	{
		return ring.add(record);
	}

	/**
	 * Adds an ambient record, tagging it with the newest still-open action's seq if one exists.
	 * Client thread only -- see {@link #openActions}.
	 */
	private void addAmbient(Map<String, Object> record)
	{
		if (!openActions.isEmpty())
		{
			long[] newest = openActions.get(openActions.size() - 1);
			record.put("causedBy", newest[0]);
			record.put("dTick", tickCount - (int) newest[2]);
		}
		add(record);
	}

	// ------------------------------------------------------------------ the action record

	@Subscribe
	public void onMenuOptionClicked(MenuOptionClicked event)
	{
		if (!armed)
		{
			return;
		}

		flushMouseBatch();

		MenuAction type = event.getMenuAction();
		Map<String, Object> record = base("action");
		record.put("option", Text.removeTags(event.getMenuOption()));
		record.put("targetText", Text.removeTags(event.getMenuTarget()));
		record.put("menuAction", type != null ? type.name() : null);
		record.put("identifier", event.getId());
		record.put("param0", event.getParam0());
		record.put("param1", event.getParam1());
		record.put("itemId", event.getItemId());
		record.put("canvasX", lastMouseX);
		record.put("canvasY", lastMouseY);

		Player local = client.getLocalPlayer();
		WorldPoint playerWp = null;
		if (local != null)
		{
			playerWp = playerWorldPoint(local);
			record.put("playerX", playerWp.getX());
			record.put("playerY", playerWp.getY());
			record.put("playerPlane", playerWp.getPlane());
			record.put("playerAnimation", local.getAnimation());
		}

		classifyTarget(record, type, event, playerWp);

		long seq = add(record);

		// [seq, deadlineTick, startTick] -- a long[] rather than a small class purely to keep this
		// allocation-light on the client thread; nothing outside this file reads it.
		openActions.add(new long[]{seq, tickCount + OUTCOME_WINDOW_TICKS, tickCount});

		// WALK is the obvious case. CANCEL is here because of a live finding (2026-08-23, full
		// Falconry bank run): a MINIMAP click does not produce a WALK -- it produces CANCEL with
		// param0/param1 both 0 and no target of any kind. All long-distance travel in that recording
		// (5 regions, every leg walked via the minimap) was therefore logged as a meaningless
		// "Cancel" with the destination lost, which is precisely the data a travel routine needs.
		//
		// CANCEL genuinely does also fire for dismissing a right-click menu, and the two are not
		// distinguishable from the event itself. Rather than guess from canvas position (the minimap
		// moves between fixed/resizable layouts), both are speculatively resolved on the next tick
		// and the destination-changed check below decides: a real cancel moves nothing.
		String typeName = type != null ? type.name() : "";
		if ("WALK".equals(typeName) || "CANCEL".equals(typeName))
		{
			pendingWalkSeq = seq;
			LocalPoint before = client.getLocalDestinationLocation();
			pendingWalkPrevDest = before != null ? WorldPoint.fromLocalInstance(client, before) : null;
		}
	}

	/**
	 * Works out WHAT was clicked and resolves it to something a routine could later target by name
	 * and world tile.
	 *
	 * Prefers {@link MenuEntry}'s direct accessors ({@code getNpc()}, {@code getPlayer()},
	 * {@code getWidget()}) over decoding param0/param1 by hand wherever they are populated -- they
	 * hand back the live object, which is both less error-prone and gives access to fields
	 * (composition, exact position, combat level) the raw menu ints do not carry. Scene-coordinate
	 * decoding is used only for the cases that have no accessor: objects, ground items, and walks.
	 */
	private void classifyTarget(Map<String, Object> record, MenuAction type, MenuOptionClicked event, WorldPoint playerWp)
	{
		String name = type != null ? type.name() : "";
		MenuEntry entry = event.getMenuEntry();

		NPC npc = entry != null ? entry.getNpc() : null;
		if (npc != null)
		{
			record.put("targetKind", "npc");
			record.put("npcId", npc.getId());
			record.put("npcIndex", npc.getIndex());
			record.put("npcName", npc.getName());
			record.put("npcCombatLevel", npc.getCombatLevel());
			WorldPoint wp = actorWorldPoint(npc);
			if (wp != null)
			{
				putTargetTile(record, wp, playerWp);
			}
			NPCComposition comp = npc.getComposition();
			if (comp != null && comp.getActions() != null)
			{
				List<String> actions = new ArrayList<>();
				for (String action : comp.getActions())
				{
					if (action != null)
					{
						actions.add(action);
					}
				}
				record.put("npcActions", actions);
			}
			return;
		}

		Player targetPlayer = entry != null ? entry.getPlayer() : null;
		if (targetPlayer != null)
		{
			record.put("targetKind", "player");
			record.put("playerName", targetPlayer.getName());
			WorldPoint wp = actorWorldPoint(targetPlayer);
			if (wp != null)
			{
				putTargetTile(record, wp, playerWp);
			}
			return;
		}

		if (name.contains("GAME_OBJECT") || "EXAMINE_OBJECT".equals(name))
		{
			record.put("targetKind", "object");
			int objectId = event.getId();
			record.put("objectId", objectId);
			record.put("objectName", resolveObjectName(objectId));

			WorldPoint wp = sceneToWorld(event.getParam0(), event.getParam1());
			if (wp != null)
			{
				putTargetTile(record, wp, playerWp);
			}
			// Remembered so the outcome window can recognise this exact object transforming --
			// a depleting rock, an opening door, a lit brazier. This is the single most useful
			// success-signal a mining/woodcutting/agility routine can key off, and it is only
			// identifiable as "the thing I clicked changed" if the clicked tile is recorded here.
			record.put("sceneX", event.getParam0());
			record.put("sceneY", event.getParam1());
			return;
		}

		if (name.contains("GROUND_ITEM") || "EXAMINE_ITEM_GROUND".equals(name))
		{
			record.put("targetKind", "grounditem");
			record.put("groundItemId", event.getId());
			record.put("groundItemName", resolveItemName(event.getId()));
			WorldPoint wp = sceneToWorld(event.getParam0(), event.getParam1());
			if (wp != null)
			{
				putTargetTile(record, wp, playerWp);
			}
			return;
		}

		if ("WALK".equals(name))
		{
			record.put("targetKind", "walk");
			// NO scene-coordinate decoding here, unlike every other tile-based action above.
			// CONFIRMED live 2026-08-23 (first real recording): for MenuAction.WALK, param0/param1
			// are the CANVAS click coordinates, not scene coordinates -- the recorded values were
			// identical to canvasX/canvasY (400,216), and 400 is outside the 104-tile scene grid
			// entirely. (sceneToWorld's bounds check caught this and returned null rather than
			// emitting a garbage tile, which is why the log showed "?" instead of a wrong answer.)
			//
			// The destination the client actually resolved that click to is only available from
			// getLocalDestinationLocation AFTER the click is processed, so it is picked up on the
			// next tick and emitted as a separate `walkdest` record attributed back to this one.
			return;
		}

		Widget widget = entry != null ? entry.getWidget() : null;
		if (widget != null || event.getItemId() > 0 || name.contains("WIDGET") || name.contains("ITEM") || "CC_OP".equals(name))
		{
			record.put("targetKind", "widget");
			// param1 is the packed widget id and param0 the child/slot index for every widget-family
			// menu action -- the same pair a routine would later need to locate this interface
			// element, so both are surfaced under explicit names rather than left as raw params.
			record.put("widgetId", event.getParam1());
			record.put("widgetChild", event.getParam0());
			if (event.getItemId() > 0)
			{
				record.put("itemName", resolveItemName(event.getItemId()));
			}
			if (widget != null)
			{
				record.put("widgetText", Text.removeTags(widget.getText() == null ? "" : widget.getText()));
				record.put("widgetName", Text.removeTags(widget.getName() == null ? "" : widget.getName()));
			}
			return;
		}

		record.put("targetKind", "other");
	}

	private void putTargetTile(Map<String, Object> record, WorldPoint wp, WorldPoint playerWp)
	{
		record.put("targetX", wp.getX());
		record.put("targetY", wp.getY());
		record.put("targetPlane", wp.getPlane());
		record.put("targetRegion", wp.getRegionID());
		if (playerWp != null && playerWp.getPlane() == wp.getPlane())
		{
			record.put("targetDistance", playerWp.distanceTo(wp));
		}
	}

	// ------------------------------------------------------------------ coordinate helpers

	/**
	 * Scene coordinates (as carried by a menu action's param0/param1) to a real world tile.
	 *
	 * {@code WorldPoint.fromLocalInstance} rather than {@code fromLocal}/{@code fromScene}: inside
	 * any instanced area the raw scene position maps to an instance-template coordinate in the
	 * ~13000s, not the real map tile a routine would be written against. See the "Instanced areas"
	 * entry in CLAUDE.md -- this exact bug has been fixed four separate times in other providers,
	 * so it is handled here from the start rather than after a live symptom.
	 */
	private WorldPoint sceneToWorld(int sceneX, int sceneY)
	{
		if (sceneX < 0 || sceneY < 0 || sceneX >= SCENE_SIZE || sceneY >= SCENE_SIZE)
		{
			return null;
		}
		WorldView view = client.getTopLevelWorldView();
		if (view == null)
		{
			return null;
		}
		LocalPoint local = LocalPoint.fromScene(sceneX, sceneY, view);
		if (local == null)
		{
			return null;
		}
		return WorldPoint.fromLocalInstance(client, local);
	}

	private WorldPoint playerWorldPoint(Player player)
	{
		LocalPoint local = player.getLocalLocation();
		if (local != null && local.getWorldView() <= WorldView.TOPLEVEL)
		{
			return WorldPoint.fromLocalInstance(client, local);
		}
		return RuneLiteUtil.getTopLevelWorldLocation(client, player);
	}

	private WorldPoint actorWorldPoint(Actor actor)
	{
		if (actor == null)
		{
			return null;
		}
		LocalPoint local = actor.getLocalLocation();
		if (local != null && local.getWorldView() <= WorldView.TOPLEVEL)
		{
			return WorldPoint.fromLocalInstance(client, local);
		}
		return RuneLiteUtil.getTopLevelWorldLocation(client, actor);
	}

	private WorldPoint tileObjectWorldPoint(TileObject obj)
	{
		LocalPoint local = obj.getLocalLocation();
		if (local == null)
		{
			return null;
		}
		if (local.getWorldView() <= WorldView.TOPLEVEL)
		{
			return WorldPoint.fromLocalInstance(client, local);
		}
		return RuneLiteUtil.toTopLevelWorldPoint(client, local);
	}

	/**
	 * Resolves an object id to its displayed name, following the impostor (multiloc) indirection --
	 * without it, varbit-conditional objects report the base composition's placeholder name
	 * ("null") instead of what the player actually sees on the right-click menu. Same lookup
	 * ObjectProvider.buildObjectData already does; see its comment for the live case that found it.
	 */
	private String resolveObjectName(int id)
	{
		ObjectComposition comp = client.getObjectDefinition(id);
		if (comp == null)
		{
			return null;
		}
		if (comp.getImpostorIds() != null)
		{
			ObjectComposition impostor = comp.getImpostor();
			if (impostor != null)
			{
				comp = impostor;
			}
		}
		return comp.getName();
	}

	private String resolveItemName(int id)
	{
		if (id <= 0)
		{
			return null;
		}
		ItemComposition comp = client.getItemDefinition(id);
		return comp != null ? comp.getName() : null;
	}

	private int distanceToPlayer(WorldPoint wp)
	{
		Player local = client.getLocalPlayer();
		if (local == null || wp == null)
		{
			return Integer.MAX_VALUE;
		}
		WorldPoint playerWp = playerWorldPoint(local);
		if (playerWp.getPlane() != wp.getPlane())
		{
			return Integer.MAX_VALUE;
		}
		return playerWp.distanceTo(wp);
	}

	// ------------------------------------------------------------------ ambient: the timeline

	@Subscribe
	public void onGameTick(GameTick event)
	{
		tickCount = client.getTickCount();
		tickStartMs = System.currentTimeMillis();

		if (!armed)
		{
			return;
		}

		// Bounds mouse-batch latency: without this, a batch that never reaches MOUSE_BATCH_MAX sits
		// unflushed until the next poll or click, losing its true position in the timeline.
		flushMouseBatch();

		// Before closeExpiredActions, so a walk clicked right on a window boundary still resolves.
		resolveWalkDestination();
		closeExpiredActions();
		emitTickSnapshot();
		diffNearbyNpcs();
	}

	/**
	 * Emits the tile a WALK action actually resolved to, on the tick after the click.
	 *
	 * A walk is the one action whose target cannot be read from the menu event itself -- its
	 * param0/param1 are canvas coordinates (see classifyTarget's WALK branch). The client resolves
	 * the click to a destination tile only when it processes it, so getLocalDestinationLocation is
	 * read one tick later. Attributed explicitly to the walk's own seq rather than through
	 * addAmbient's newest-open-action rule, since by now a subsequent click could already have
	 * opened a newer window and would otherwise steal the attribution.
	 */
	private void resolveWalkDestination()
	{
		if (pendingWalkSeq < 0)
		{
			return;
		}

		long seq = pendingWalkSeq;
		WorldPoint previous = pendingWalkPrevDest;
		pendingWalkSeq = -1;
		pendingWalkPrevDest = null;

		LocalPoint dest = client.getLocalDestinationLocation();
		WorldPoint wp = dest != null ? WorldPoint.fromLocalInstance(client, dest) : null;

		// Unchanged destination means the click did not start a walk: either a genuine menu Cancel,
		// or a walk click that was blocked / landed on the tile already occupied. Distinguished from
		// a real walk by comparison rather than by the menu action, since a minimap walk and a menu
		// dismissal arrive as the same CANCEL event -- see onMenuOptionClicked.
		if (wp == null || wp.equals(previous))
		{
			Map<String, Object> record = base("walkdest");
			record.put("causedBy", seq);
			record.put("resolved", false);
			add(record);
			return;
		}

		Map<String, Object> record = base("walkdest");
		record.put("causedBy", seq);
		record.put("resolved", true);
		record.put("x", wp.getX());
		record.put("y", wp.getY());
		record.put("plane", wp.getPlane());
		record.put("region", wp.getRegionID());
		Player local = client.getLocalPlayer();
		if (local != null)
		{
			WorldPoint from = playerWorldPoint(local);
			if (from.getPlane() == wp.getPlane())
			{
				record.put("distance", from.distanceTo(wp));
			}
		}
		add(record);
	}

	private void closeExpiredActions()
	{
		Iterator<long[]> it = openActions.iterator();
		while (it.hasNext())
		{
			if (tickCount > it.next()[1])
			{
				it.remove();
			}
		}
	}

	/**
	 * One lightweight player snapshot per game tick -- the backbone the rest of the log hangs off.
	 * Cheap (1.67/sec) and it is what lets a reader answer "where was I and what was I doing" at any
	 * point without needing an action or ambient record to have happened to fire at that moment.
	 */
	private void emitTickSnapshot()
	{
		Player local = client.getLocalPlayer();
		if (local == null)
		{
			return;
		}

		WorldPoint wp = playerWorldPoint(local);
		Map<String, Object> record = base("tick");
		record.put("x", wp.getX());
		record.put("y", wp.getY());
		record.put("plane", wp.getPlane());
		record.put("region", wp.getRegionID());
		record.put("animation", local.getAnimation());
		record.put("poseAnimation", local.getPoseAnimation());
		record.put("runEnergy", client.getEnergy() / 100);
		record.put("hp", client.getBoostedSkillLevel(Skill.HITPOINTS));
		record.put("prayer", client.getBoostedSkillLevel(Skill.PRAYER));
		record.put("menuOpen", client.isMenuOpen());

		LocalPoint dest = client.getLocalDestinationLocation();
		record.put("moving", dest != null);
		if (dest != null)
		{
			WorldPoint destWp = WorldPoint.fromLocalInstance(client, dest);
			record.put("destX", destWp.getX());
			record.put("destY", destWp.getY());
		}

		Actor interacting = local.getInteracting();
		if (interacting != null)
		{
			record.put("interactingName", interacting.getName());
			if (interacting instanceof NPC)
			{
				record.put("interactingNpcId", ((NPC) interacting).getId());
			}
		}

		add(record);
	}

	/**
	 * One per-tick scan of every nearby NPC, emitting spawn, despawn, transformation, animation and
	 * spotanim records.
	 *
	 * <h3>Why this is a per-tick scan and not purely event-driven</h3>
	 * CONFIRMED live 2026-08-23 (first real recording, Falconry): {@code AnimationChanged} only fires
	 * when an animation CHANGES on an actor the client already knows about. An NPC that spawns
	 * already mid-animation never fires it -- the animation is part of its initial state, not a
	 * change to it. In the Falconry recording the Gyr Falcon spawns already swooping, so its
	 * animation was entirely absent from the log while the player's and the kebbit's were both
	 * present. A routine keying off "has the falcon finished its swoop" would have had nothing to
	 * work with.
	 *
	 * Diffing per tick costs nothing in fidelity: unlike screen POSITION (interpolated every frame,
	 * see CLAUDE.md's sub-tick notes), which animation is playing is genuinely tick-quantised
	 * server-side, so a tick-boundary diff sees every animation the event would have -- plus the
	 * ones it structurally cannot. AnimationChanged is therefore kept only for the local player,
	 * where it is free, always fires, and gives sub-tick timing.
	 *
	 * <h3>Transformations</h3>
	 * Also CONFIRMED live in the same recording: the Dashing kebbit and the Gyr Falcon share NPC
	 * index 12323 -- the kebbit is not replaced by the falcon, it TRANSFORMS into it, keeping its
	 * index while its id changes. There is no spawn or despawn to observe, so without an explicit
	 * id-change check this (the central mechanic of the whole activity) is invisible in the log.
	 * Emitted as {@code npcchange}.
	 */
	private void diffNearbyNpcs()
	{
		Player local = client.getLocalPlayer();
		if (local != null)
		{
			checkSpotAnims(-1, local, local.getName());
		}

		WorldView view = client.getTopLevelWorldView();
		if (view == null)
		{
			return;
		}

		Set<Integer> seen = new HashSet<>();
		for (NPC npc : view.npcs())
		{
			if (npc == null)
			{
				continue;
			}
			WorldPoint wp = actorWorldPoint(npc);
			if (wp == null || distanceToPlayer(wp) > AMBIENT_RADIUS)
			{
				continue;
			}

			int index = npc.getIndex();
			seen.add(index);

			NpcState state = npcStates.get(index);
			if (state == null)
			{
				state = new NpcState();
				state.id = npc.getId();
				state.animation = Integer.MIN_VALUE;
				npcStates.put(index, state);
				emitNpcPresence("npcspawn", npc, wp);
			}
			else if (state.id != npc.getId())
			{
				Map<String, Object> record = base("npcchange");
				record.put("npcIndex", index);
				record.put("fromId", state.id);
				record.put("fromName", state.name);
				record.put("npcId", npc.getId());
				record.put("npcName", npc.getName());
				record.put("x", wp.getX());
				record.put("y", wp.getY());
				record.put("plane", wp.getPlane());
				addAmbient(record);
				state.id = npc.getId();
			}
			state.name = npc.getName();

			int animation = npc.getAnimation();
			if (state.animation != animation)
			{
				state.animation = animation;
				Map<String, Object> record = base("anim");
				record.put("animation", animation);
				record.put("actorName", npc.getName());
				record.put("isPlayer", false);
				record.put("npcId", npc.getId());
				record.put("npcIndex", index);
				record.put("x", wp.getX());
				record.put("y", wp.getY());
				record.put("plane", wp.getPlane());
				addAmbient(record);
			}

			checkSpotAnims(index, npc, npc.getName());
		}

		// Forget NPCs that walked out of range or despawned. Without this the map grows for the
		// whole recording, and worse, OSRS reuses NPC indices -- a new NPC inheriting a departed
		// one's index would be diffed against the wrong actor and silently skip its first effect.
		Iterator<Map.Entry<Integer, NpcState>> it = npcStates.entrySet().iterator();
		while (it.hasNext())
		{
			Map.Entry<Integer, NpcState> entry = it.next();
			if (seen.contains(entry.getKey()))
			{
				continue;
			}
			Map<String, Object> record = base("npcdespawn");
			record.put("npcIndex", entry.getKey());
			record.put("npcId", entry.getValue().id);
			record.put("npcName", entry.getValue().name);
			addAmbient(record);
			it.remove();
		}

		lastSpotAnims.keySet().retainAll(seen.isEmpty() ? Collections.singleton(-1) : withPlayerKey(seen));
	}

	private static Set<Integer> withPlayerKey(Set<Integer> indices)
	{
		Set<Integer> keys = new HashSet<>(indices);
		keys.add(-1);
		return keys;
	}

	private void emitNpcPresence(String kind, NPC npc, WorldPoint wp)
	{
		Map<String, Object> record = base(kind);
		record.put("npcIndex", npc.getIndex());
		record.put("npcId", npc.getId());
		record.put("npcName", npc.getName());
		record.put("x", wp.getX());
		record.put("y", wp.getY());
		record.put("plane", wp.getPlane());
		addAmbient(record);
	}

	/** Per-NPC-index memory backing {@link #diffNearbyNpcs}'s spawn/transform/animation diffing. */
	private static final class NpcState
	{
		int id;
		String name;
		int animation;
	}

	/**
	 * Emits one record per spotanim that is active on this actor NOW but was not on the previous
	 * tick.
	 *
	 * Reads getSpotAnims() rather than the deprecated single-valued getGraphic(): an actor can carry
	 * several spotanims simultaneously (a splash over a poison hit over a prayer effect), and
	 * getGraphic() reports only one of them, so a set diff is both the non-deprecated API and the
	 * only way to avoid dropping concurrent effects.
	 */
	private void checkSpotAnims(int key, Actor actor, String name)
	{
		Set<Integer> current = new HashSet<>();
		for (ActorSpotAnim spotAnim : actor.getSpotAnims())
		{
			if (spotAnim != null && spotAnim.getId() > 0)
			{
				current.add(spotAnim.getId());
			}
		}

		Set<Integer> previous = lastSpotAnims.put(key, current);
		if (current.isEmpty())
		{
			return;
		}

		WorldPoint wp = null;
		for (Integer id : current)
		{
			if (previous != null && previous.contains(id))
			{
				continue;
			}

			if (wp == null)
			{
				wp = actorWorldPoint(actor);
			}

			Map<String, Object> record = base("spotanim");
			record.put("spotAnimId", id);
			record.put("actorName", name);
			record.put("isPlayer", key == -1);
			if (key != -1)
			{
				record.put("npcIndex", key);
				if (actor instanceof NPC)
				{
					record.put("npcId", ((NPC) actor).getId());
				}
			}
			if (wp != null)
			{
				record.put("x", wp.getX());
				record.put("y", wp.getY());
				record.put("plane", wp.getPlane());
			}
			addAmbient(record);
		}
	}

	/**
	 * Local player animations only. NPC animations come from {@link #diffNearbyNpcs}'s per-tick
	 * diffing instead -- see that method for why this event structurally cannot see all of them.
	 * Kept for the player because it always fires there and costs nothing, giving the player's own
	 * animation a sub-tick timestamp rather than a tick-boundary one.
	 */
	@Subscribe
	public void onAnimationChanged(AnimationChanged event)
	{
		if (!armed)
		{
			return;
		}

		Actor actor = event.getActor();
		if (actor == null || actor != client.getLocalPlayer())
		{
			return;
		}

		WorldPoint wp = actorWorldPoint(actor);

		Map<String, Object> record = base("anim");
		record.put("animation", actor.getAnimation());
		record.put("actorName", actor.getName());
		record.put("isPlayer", true);
		if (wp != null)
		{
			record.put("x", wp.getX());
			record.put("y", wp.getY());
			record.put("plane", wp.getPlane());
		}
		addAmbient(record);
	}

	@Subscribe
	public void onGraphicsObjectCreated(GraphicsObjectCreated event)
	{
		if (!armed)
		{
			return;
		}

		GraphicsObject obj = event.getGraphicsObject();
		if (obj == null)
		{
			return;
		}

		WorldPoint wp = WorldPoint.fromLocalInstance(client, obj.getLocation());
		if (wp == null || distanceToPlayer(wp) > AMBIENT_RADIUS)
		{
			return;
		}

		Map<String, Object> record = base("gfx");
		record.put("gfxId", obj.getId());
		record.put("x", wp.getX());
		record.put("y", wp.getY());
		record.put("plane", obj.getLevel());
		addAmbient(record);
	}

	@Subscribe
	public void onGameObjectSpawned(GameObjectSpawned event)
	{
		recordObjectChange("objspawn", event.getGameObject());
	}

	@Subscribe
	public void onGameObjectDespawned(GameObjectDespawned event)
	{
		recordObjectChange("objdespawn", event.getGameObject());
	}

	/**
	 * An object appearing or vanishing near the player. There is no GameObjectChanged event in this
	 * RuneLite version, so an object TRANSFORMING (Sunstone rocks becoming a depleted rock, a closed
	 * door becoming an open one) shows up here as a despawn immediately followed by a spawn on the
	 * same tile -- which recorder.py renders as a single "became" line, since that pairing is the
	 * thing a routine actually wants to detect.
	 */
	private void recordObjectChange(String kind, GameObject obj)
	{
		if (!armed || obj == null)
		{
			return;
		}
		if (System.currentTimeMillis() < objectSuppressUntilMs)
		{
			return;
		}

		WorldPoint wp = tileObjectWorldPoint(obj);
		if (wp == null || distanceToPlayer(wp) > AMBIENT_RADIUS)
		{
			return;
		}

		Map<String, Object> record = base(kind);
		record.put("objectId", obj.getId());
		record.put("objectName", resolveObjectName(obj.getId()));
		record.put("x", wp.getX());
		record.put("y", wp.getY());
		record.put("plane", wp.getPlane());
		addAmbient(record);
	}

	@Subscribe
	public void onGameStateChanged(GameStateChanged event)
	{
		if (event.getGameState() != GameState.LOGGED_IN)
		{
			// Covers LOADING, HOPPING, LOGIN_SCREEN -- each is followed by a full-scene object spawn
			// burst that would otherwise evict the real recording out of the ring buffer.
			objectSuppressUntilMs = System.currentTimeMillis() + OBJECT_SUPPRESS_MS;
		}

		if (!armed)
		{
			return;
		}
		Map<String, Object> record = base("gamestate");
		record.put("state", event.getGameState().name());
		add(record);
	}

	@Subscribe
	public void onFocusChanged(FocusChanged event)
	{
		// EVENT_APPLET_FOCUS: the client sends 1/0 on every gain/loss (spec V7).
		if (!armed)
		{
			return;
		}
		Map<String, Object> record = base("focus");
		record.put("gained", event.isFocused());
		add(record);
	}

	@Subscribe
	public void onStatChanged(StatChanged event)
	{
		if (!armed)
		{
			return;
		}

		Skill skill = event.getSkill();
		int xp = client.getSkillExperience(skill);
		Integer previous = lastXp.put(skill.ordinal(), xp);
		if (previous == null || xp <= previous)
		{
			return;
		}

		Map<String, Object> record = base("xp");
		record.put("skill", skill.getName());
		record.put("xp", xp);
		record.put("gained", xp - previous);
		record.put("level", client.getRealSkillLevel(skill));
		addAmbient(record);
	}

	@Subscribe
	public void onItemContainerChanged(ItemContainerChanged event)
	{
		if (!armed || event.getContainerId() != InventoryID.INVENTORY.getId())
		{
			return;
		}

		ItemContainer container = event.getItemContainer();
		Map<Integer, Integer> current = new HashMap<>();
		if (container != null)
		{
			for (Item item : container.getItems())
			{
				if (item != null && item.getId() > 0)
				{
					current.merge(item.getId(), item.getQuantity(), Integer::sum);
				}
			}
		}

		List<Map<String, Object>> changes = new ArrayList<>();
		for (Map.Entry<Integer, Integer> entry : current.entrySet())
		{
			int before = lastInventory.getOrDefault(entry.getKey(), 0);
			if (entry.getValue() != before)
			{
				changes.add(itemDelta(entry.getKey(), entry.getValue() - before));
			}
		}
		for (Map.Entry<Integer, Integer> entry : lastInventory.entrySet())
		{
			if (!current.containsKey(entry.getKey()))
			{
				changes.add(itemDelta(entry.getKey(), -entry.getValue()));
			}
		}

		boolean first = !inventoryBaselineSet;
		inventoryBaselineSet = true;
		lastInventory.clear();
		lastInventory.putAll(current);

		// The first container event after arming has nothing to diff against, so every carried item
		// would read as a fresh "+N" gain. Recorded as a distinct kind so the log shows the starting
		// inventory (genuinely useful context) without it looking like the player picked it all up.
		Map<String, Object> record = base(first ? "invstart" : "inv");
		record.put("changes", changes);
		record.put("usedSlots", occupiedSlots(container));
		record.put("freeSlots", 28 - occupiedSlots(container));
		if (first)
		{
			add(record);
		}
		else if (!changes.isEmpty())
		{
			addAmbient(record);
		}
	}

	/**
	 * True occupied inventory slots.
	 *
	 * NOT {@code lastInventory.size()}, which counts DISTINCT ITEM IDS. CONFIRMED live 2026-08-23
	 * (full Falconry run): the player's inventory filled with non-stacking Bones until the game
	 * refused a fur pickup ("You retrieve the falcon." with no fur), while this field cheerfully
	 * reported 22 slots free -- 11 separate Bones occupy 11 slots but collapse to one map entry.
	 * A routine deciding when to bank or drop off that number would never fire.
	 */
	private static int occupiedSlots(ItemContainer container)
	{
		if (container == null)
		{
			return 0;
		}
		int used = 0;
		for (Item item : container.getItems())
		{
			if (item != null && item.getId() > 0)
			{
				used++;
			}
		}
		return used;
	}

	private Map<String, Object> itemDelta(int id, int delta)
	{
		Map<String, Object> entry = new HashMap<>();
		entry.put("id", id);
		entry.put("name", resolveItemName(id));
		entry.put("delta", delta);
		return entry;
	}

	@Subscribe
	public void onChatMessage(ChatMessage event)
	{
		if (!armed)
		{
			return;
		}
		switch (event.getType())
		{
			case GAMEMESSAGE:
			case SPAM:
			case DIALOG:
			case MESBOX:
				break;
			default:
				return;
		}

		Map<String, Object> record = base("chat");
		record.put("type", event.getType().name());
		record.put("message", Text.removeTags(event.getMessage()));
		record.put("sender", event.getName());
		addAmbient(record);
	}

	@Subscribe
	public void onInteractingChanged(InteractingChanged event)
	{
		if (!armed || event.getSource() != client.getLocalPlayer())
		{
			return;
		}

		Map<String, Object> record = base("interact");
		Actor target = event.getTarget();
		record.put("targetName", target != null ? target.getName() : null);
		if (target instanceof NPC)
		{
			record.put("npcId", ((NPC) target).getId());
			record.put("npcIndex", ((NPC) target).getIndex());
		}
		addAmbient(record);
	}

	@Subscribe
	public void onVarbitChanged(VarbitChanged event)
	{
		if (!armed)
		{
			return;
		}

		// Varbits are noisy enough that recorder.py files them in a side stream rather than the main
		// narrative log -- but they are recorded regardless, because finding "the varbit that tracks
		// this minigame's state" is exactly the kind of discovery this recorder exists to enable, and
		// it can only be made by correlating flips against actions after the fact.
		Map<String, Object> record = base("varbit");
		record.put("varbitId", event.getVarbitId());
		record.put("varpId", event.getVarpId());
		record.put("value", event.getValue());
		addAmbient(record);
	}

	// ------------------------------------------------------------------ mouse and keyboard

	private void flushMouseBatch()
	{
		List<int[]> points;
		synchronized (mouseLock)
		{
			if (mouseBatch.isEmpty())
			{
				return;
			}
			points = new ArrayList<>(mouseBatch);
			mouseBatch.clear();
		}

		List<List<Integer>> compact = new ArrayList<>(points.size());
		for (int[] point : points)
		{
			List<Integer> triple = new ArrayList<>(3);
			triple.add(point[0]);
			triple.add(point[1]);
			triple.add(point[2]);
			compact.add(triple);
		}

		Map<String, Object> record = base("mouse");
		// [x, y, msSinceArm] per sample. A compact triple array rather than named fields per point:
		// at ~60-100 samples/sec the key names would dominate the file size, and nothing reads these
		// individually -- they are a trajectory, consumed as a whole.
		record.put("points", compact);
		record.put("count", compact.size());
		add(record);
	}

	private class RecorderMouseListener extends MouseAdapter
	{
		// Every method returns the event UNMODIFIED. A MouseManager listener that returns a consumed
		// event would swallow the input before the game sees it -- this recorder must observe the
		// player's real play session without altering it in any way.

		@Override
		public MouseEvent mouseMoved(MouseEvent event)
		{
			sample(event);
			return event;
		}

		@Override
		public MouseEvent mouseDragged(MouseEvent event)
		{
			sample(event);
			return event;
		}

		@Override
		public MouseEvent mousePressed(MouseEvent event)
		{
			button(event, "down");
			return event;
		}

		@Override
		public MouseEvent mouseReleased(MouseEvent event)
		{
			button(event, "up");
			return event;
		}

		@Override
		public MouseEvent mouseEntered(MouseEvent event)
		{
			edge("canvasenter", event);
			return event;
		}

		@Override
		public MouseEvent mouseExited(MouseEvent event)
		{
			edge("canvasexit", event);
			return event;
		}

		/**
		 * The client's own EVENT_MOUSE_MOVE encodes an off-canvas cursor as -1, so when the cursor
		 * leaves and returns is server-visible (spec V2). The open mouse batch is flushed first so
		 * the polyline ENDS at the edge and the off-canvas gap is measurable in the .jsonl.
		 */
		private void edge(String kind, MouseEvent event)
		{
			if (!armed)
			{
				return;
			}
			flushMouseBatch();
			Map<String, Object> record = base(kind);
			record.put("x", event.getX());
			record.put("y", event.getY());
			add(record);
		}

		private void sample(MouseEvent event)
		{
			lastMouseX = event.getX();
			lastMouseY = event.getY();
			if (!armed)
			{
				return;
			}

			long now = System.currentTimeMillis();
			boolean flush;
			synchronized (mouseLock)
			{
				if (mouseBatch.isEmpty())
				{
					mouseBatchStartMs = now;
				}
				mouseBatch.add(new int[]{event.getX(), event.getY(), (int) (now - epochMs)});
				flush = mouseBatch.size() >= MOUSE_BATCH_MAX || (now - mouseBatchStartMs) >= MOUSE_FLUSH_MS;
			}
			if (flush)
			{
				flushMouseBatch();
			}
		}

		private void button(MouseEvent event, String edge)
		{
			lastMouseX = event.getX();
			lastMouseY = event.getY();
			if (!armed)
			{
				return;
			}

			// Flushed first so the movement leading INTO the click is ordered before the click
			// itself -- the approach curve is the whole point of recording movement.
			flushMouseBatch();

			Map<String, Object> record = base("click");
			record.put("edge", edge);
			record.put("button", event.getButton());
			record.put("x", event.getX());
			record.put("y", event.getY());
			add(record);
		}
	}

	private class RecorderKeyListener implements net.runelite.client.input.KeyListener
	{
		/**
		 * False deliberately, and this is a privacy control rather than a preference: with it true
		 * this listener would capture the account password being typed and write it to a plaintext
		 * recording file. See the class doc.
		 */
		@Override
		public boolean isEnabledOnLoginScreen()
		{
			return false;
		}

		@Override
		public void keyTyped(KeyEvent event)
		{
		}

		@Override
		public void keyPressed(KeyEvent event)
		{
			record(event, "down");
		}

		@Override
		public void keyReleased(KeyEvent event)
		{
			record(event, "up");
		}

		private void record(KeyEvent event, String edge)
		{
			// Second half of the privacy control above: isEnabledOnLoginScreen alone does not cover
			// a bank-PIN prompt or any other in-client credential entry that happens while already
			// logged in, but it does at least mean nothing is captured before login. Keys are only
			// recorded in the LOGGED_IN state.
			if (!armed || client.getGameState() != GameState.LOGGED_IN)
			{
				return;
			}

			Map<String, Object> record = base("key");
			record.put("edge", edge);
			record.put("keyCode", event.getKeyCode());
			record.put("keyName", KeyEvent.getKeyText(event.getKeyCode()));
			add(record);
		}
	}

	/**
	 * Unregisters the AWT-level listeners. The EventBus subscription is torn down by the plugin's
	 * own eventBus.unregister; these two are not on the EventBus and would otherwise outlive the
	 * plugin being disabled, leaving a dead listener attached to the client's input pipeline.
	 */
	public void unregister()
	{
		armed = false;
		if (mouseListener != null)
		{
			mouseManager.unregisterMouseListener(mouseListener);
			mouseListener = null;
		}
		if (keyListener != null)
		{
			keyManager.unregisterKeyListener(keyListener);
			keyListener = null;
		}
		eventBus.unregister(this);
	}
}
