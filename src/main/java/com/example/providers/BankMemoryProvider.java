package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.google.gson.Gson;
import net.runelite.api.Client;
import net.runelite.api.ItemContainer;
import net.runelite.api.events.ItemContainerChanged;
import net.runelite.api.gameval.InventoryID;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.eventbus.EventBus;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.game.ItemManager;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * A remembered copy of the bank, so a routine can ask what it owns while standing nowhere near
 * one. Modelled on the Bank Memory plugin (Lazyfaith/runelite-bank-memory-plugin).
 *
 * <p>WHY THIS EXISTS AND WHY IT IS NOT /bank. BankProvider builds its item list from
 * BANK_ITEM_CONTAINER's dynamic children and skips {@code child.isHidden()}, so it can only ever
 * report what the interface is currently DRAWING: items on a numbered tab you are not viewing are
 * hidden, and -- the sharp edge -- so is every non-matching item while a bank SEARCH is active.
 * routines/giants_foundry.py searches "adam" before withdrawing, and for as long as that search
 * is up /bank describes a bank containing only adamant items. The BANK ItemContainer this class
 * subscribes to has none of those problems: it is the true contents, every tab, whatever the
 * interface happens to be showing.
 *
 * <p>The capture is one subscriber and it is free -- ItemContainerChanged fires for the bank on
 * every open, deposit and withdraw, which also means the memory self-heals during a banking trip
 * with no invalidation logic anywhere, and that it records the user's own manual banking whenever
 * the client is running.
 *
 * <p>Serving /bank/memory touches neither the client thread nor TickCache: the snapshot is
 * already-resolved plain data, so unlike /bank it is free to poll.
 */
public class BankMemoryProvider extends BridgeProvider
{
	// Same convention as ScreenshotProvider's OUTPUT_SUBDIR.
	private static final String OUTPUT_SUBDIR = ".runelite/bridge-bank-memory";

	/**
	 * A bank trip fires the handler a dozen times in a few seconds and this user's bank is ~950
	 * items (~60-80KB of JSON), so the write is debounced and off the client thread. FIRST-DRAFT
	 * GUESS -- wants one live session to fit, same as every other budget in this codebase.
	 */
	private static final long WRITE_DEBOUNCE_MS = 3000;

	private final EventBus eventBus;
	private final ItemManager itemManager;
	private final Gson gson = new Gson();
	private final AtomicBoolean dirty = new AtomicBoolean();

	private volatile BankSnapshot snapshot = BankSnapshot.neverSeen(null);
	private volatile String loadedAccount;
	private ScheduledExecutorService writer;

	public BankMemoryProvider(Client client, ClientThread clientThread, BridgeHttpServer server,
							  EventBus eventBus, ItemManager itemManager)
	{
		super(client, clientThread, server);
		this.eventBus = eventBus;
		this.itemManager = itemManager;
	}

	@Override
	public void register()
	{
		server.registerQuery("/bank/memory", this::getMemory);
		eventBus.register(this);

		writer = Executors.newSingleThreadScheduledExecutor(r -> {
			Thread t = new Thread(r, "bridge-bank-memory");
			t.setDaemon(true);
			return t;
		});
		writer.scheduleWithFixedDelay(this::flush, WRITE_DEBOUNCE_MS, WRITE_DEBOUNCE_MS,
			TimeUnit.MILLISECONDS);
	}

	/**
	 * Called from ControllerPlugin.shutDown. Providers here mostly never unregister, but that is
	 * exactly the leak CLAUDE.md records biting TickClock -- every plugin disable/enable cycle
	 * left another subscriber alive.
	 */
	public void unregister()
	{
		eventBus.unregister(this);
		if (writer != null)
		{
			flush();
			writer.shutdownNow();
			writer = null;
		}
	}

	private Object getMemory(Map<String, String> query)
	{
		// Reloading here rather than on a GameState event keeps the account check on the read
		// path, where a null/absent account hash is simply answered rather than raced against
		// login ordering.
		ensureLoadedForCurrentAccount();

		BankSnapshot current = snapshot;
		String names = query.get("names");
		if (names == null || names.trim().isEmpty())
		{
			return current.toMap();
		}
		return current.filtered(Arrays.asList(names.split(",")));
	}

	@Subscribe
	public void onItemContainerChanged(ItemContainerChanged event)
	{
		if (event.getContainerId() != InventoryID.BANK)
		{
			return;
		}

		ItemContainer container = event.getItemContainer();
		if (container == null)
		{
			return;
		}

		String account = accountKey();
		List<Map<String, Object>> items = BankSnapshot.itemsFrom(container.getItems(), lookup());
		snapshot = BankSnapshot.captured(account, System.currentTimeMillis(),
			client.getTickCount(), items, "live");
		loadedAccount = account;
		dirty.set(true);
	}

	private BankSnapshot.ItemLookup lookup()
	{
		return new BankSnapshot.ItemLookup()
		{
			@Override
			public int canonicalize(int id)
			{
				return itemManager.canonicalize(id);
			}

			@Override
			public String nameOf(int id)
			{
				return itemManager.getItemComposition(id).getName();
			}
		};
	}

	/**
	 * The account this snapshot belongs to, or null when the client cannot say (logged out, or
	 * an account hash of -1). A null account is kept in memory only and never written, so we can
	 * never persist a file under the wrong key -- Bank Memory guards the same way.
	 */
	private String accountKey()
	{
		long hash = client.getAccountHash();
		return hash == -1 ? null : Long.toString(hash);
	}

	private void ensureLoadedForCurrentAccount()
	{
		String account = accountKey();
		if (account == null || account.equals(loadedAccount))
		{
			return;
		}

		loadedAccount = account;
		BankSnapshot loaded = readFile(account);
		// Only adopt a disk snapshot if we have not already captured a live one for this account.
		if (loaded != null && !snapshot.isSeen())
		{
			snapshot = loaded;
		}
		else if (!snapshot.isSeen())
		{
			snapshot = BankSnapshot.neverSeen(account);
		}
	}

	private static Path directory()
	{
		return new File(System.getProperty("user.home"), OUTPUT_SUBDIR).toPath();
	}

	private BankSnapshot readFile(String account)
	{
		try
		{
			Path path = directory().resolve(account + ".json");
			if (!Files.isRegularFile(path))
			{
				return null;
			}

			Map<?, ?> root = gson.fromJson(
				new String(Files.readAllBytes(path), StandardCharsets.UTF_8), Map.class);
			if (root == null || !(root.get("items") instanceof List))
			{
				return null;
			}

			List<Map<String, Object>> items = new ArrayList<>();
			for (Object raw : (List<?>) root.get("items"))
			{
				Map<?, ?> entry = (Map<?, ?>) raw;
				Map<String, Object> item = new HashMap<>();
				item.put("id", ((Number) entry.get("id")).intValue());
				item.put("name", String.valueOf(entry.get("name")));
				item.put("quantity", ((Number) entry.get("quantity")).intValue());
				items.add(item);
			}

			return BankSnapshot.captured(account,
				((Number) root.get("capturedAt")).longValue(),
				((Number) root.get("capturedTick")).intValue(),
				items, "disk");
		}
		catch (Exception e)
		{
			// A corrupt or half-written file must never take the bridge down with it -- the
			// honest answer is "never seen", which every caller already has to handle.
			return null;
		}
	}

	private void flush()
	{
		if (!dirty.compareAndSet(true, false))
		{
			return;
		}

		BankSnapshot current = snapshot;
		String account = current.getAccountHash();
		if (account == null || !current.isSeen())
		{
			return;
		}

		try
		{
			Path dir = directory();
			Files.createDirectories(dir);

			Map<String, Object> data = current.toMap();
			Path tmp = dir.resolve(account + ".json.tmp");
			Files.write(tmp, gson.toJson(data).getBytes(StandardCharsets.UTF_8));
			// Atomic replace, so a kill mid-write can never leave a truncated snapshot behind
			// (same discipline as human_move.py's calibration file).
			Files.move(tmp, dir.resolve(account + ".json"), StandardCopyOption.REPLACE_EXISTING);
		}
		catch (IOException | RuntimeException e)
		{
			// Swallowed on purpose: losing a snapshot write costs one bank visit of memory,
			// while throwing here would surface on a background thread as noise.
		}
	}
}
