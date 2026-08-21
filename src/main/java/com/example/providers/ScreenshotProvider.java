package com.example.providers;

import com.example.bridge.BridgeHttpServer;
import com.example.util.RuneLiteUtil;
import net.runelite.api.Client;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.ui.DrawManager;
import net.runelite.client.util.ImageUtil;

import javax.imageio.ImageIO;
import java.awt.Canvas;
import java.awt.Dimension;
import java.awt.Image;
import java.awt.Point;
import java.awt.image.BufferedImage;
import java.io.File;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Debug-only frame capture: dumps the client's currently rendered frame to a PNG on disk and
 * reports where it landed, alongside every coordinate-space measurement needed to interpret it.
 *
 * <p>Added 2026-08-21 as a spike for generalizing login.py. That routine clicks three buttons on
 * screens the game draws as raw pixels (no widget, no Swing component -- see LoginProvider's own
 * comment for how that was confirmed), so their positions are currently a hardcoded absolute
 * pixel and a hardcoded fraction of the canvas, neither of which survives a different machine,
 * resolution, or DPI. Locating those buttons by what they actually look like requires first
 * seeing what they actually look like, at a real resolution, rather than guessing thresholds.
 *
 * <p><b>Why the metadata matters as much as the image.</b> There are three different pixel spaces
 * in play and they are only equal when stretched mode is off: the game's internal render
 * resolution ({@code realDimensions}), the stretched output ({@code stretchedDimensions}), and
 * the AWT canvas's on-screen size. {@link DrawManager} does not document which one its frame is
 * in, so this endpoint reports all of them plus the captured image's own dimensions -- comparing
 * those settles it empirically instead of by assumption. Any button position derived from this
 * image has to be converted through {@link RuneLiteUtil#canvasToScreen} in whichever space the
 * comparison shows the frame to be, or it lands off by the stretch factor. That exact mistake is
 * already documented in LoginProvider (a ~2x error from dividing a pre-stretch coordinate by a
 * post-stretch width).
 *
 * <p>Capture happens on the draw thread, so the HTTP handler blocks on a queue until the next
 * frame arrives. The timeout matters: on the login screen frames render continuously, but a
 * minimized or fully occluded client may not paint at all, and without a bound the bridge thread
 * would hang instead of returning a usable error.
 */
public class ScreenshotProvider extends BridgeProvider
{
	private static final long FRAME_TIMEOUT_S = 5;
	private static final String OUTPUT_SUBDIR = ".runelite/bridge-screenshots";

	private final DrawManager drawManager;

	public ScreenshotProvider(Client client, ClientThread clientThread, BridgeHttpServer server, DrawManager drawManager)
	{
		super(client, clientThread, server);
		this.drawManager = drawManager;
	}

	@Override
	public void register()
	{
		server.registerQuery("/screenshot", this::capture);
	}

	/**
	 * @param query optional {@code name=<label>} to tag the output file, so a sequence of captures
	 *              (retry screen, login screen, red button) stays identifiable on disk.
	 */
	private Object capture(Map<String, String> query)
	{
		Image frame;
		try
		{
			// requestNextFrameListener's queue is concurrent, so this is safe to call from the
			// HTTP thread; the consumer then fires on the draw thread. offer() on a size-1 queue
			// cannot block, which keeps the draw thread free even if we have already timed out.
			BlockingQueue<Image> handoff = new ArrayBlockingQueue<>(1);
			drawManager.requestNextFrameListener(handoff::offer);
			frame = handoff.poll(FRAME_TIMEOUT_S, TimeUnit.SECONDS);
		}
		catch (InterruptedException e)
		{
			Thread.currentThread().interrupt();
			return error("interrupted while waiting for a frame");
		}

		if (frame == null)
		{
			return error("no frame rendered within " + FRAME_TIMEOUT_S + "s (is the client minimized or occluded?)");
		}

		BufferedImage image = ImageUtil.bufferedImageFromImage(frame);
		if (image == null)
		{
			return error("frame could not be converted to a BufferedImage");
		}

		File file;
		try
		{
			file = write(image, query.get("name"));
		}
		catch (Exception e)
		{
			return error("failed to write PNG: " + e);
		}

		Map<String, Object> data = new HashMap<>();
		data.put("path", file.getAbsolutePath());
		data.put("imageWidth", image.getWidth());
		data.put("imageHeight", image.getHeight());
		data.putAll(coordinateSpaces());
		return data;
	}

	private File write(BufferedImage image, String name) throws Exception
	{
		File dir = new File(System.getProperty("user.home"), OUTPUT_SUBDIR);
		if (!dir.isDirectory() && !dir.mkdirs())
		{
			throw new IllegalStateException("could not create " + dir.getAbsolutePath());
		}

		String label = sanitize(name);
		File file = new File(dir, "frame-" + System.currentTimeMillis() + (label.isEmpty() ? "" : "-" + label) + ".png");
		ImageIO.write(image, "png", file);
		return file;
	}

	/**
	 * Strips anything that is not alphanumeric, dash, or underscore. The label is attacker-free in
	 * practice (localhost-only bridge), but it is still untrusted text going straight into a
	 * filename, and {@code ../} in a query parameter would otherwise escape the output directory.
	 */
	private static String sanitize(String name)
	{
		if (name == null)
		{
			return "";
		}
		String cleaned = name.replaceAll("[^A-Za-z0-9_-]", "");
		return cleaned.length() > 40 ? cleaned.substring(0, 40) : cleaned;
	}

	/**
	 * Every measurement needed to work out which pixel space the captured frame is in, and how to
	 * map a position inside it back to an absolute on-screen point.
	 */
	private Map<String, Object> coordinateSpaces()
	{
		@SuppressWarnings("unchecked")
		Map<String, Object> spaces = (Map<String, Object>) onClient(() -> {
			Map<String, Object> out = new HashMap<>();
			out.put("gameState", client.getGameState().name());
			out.put("loginIndex", client.getLoginIndex());
			out.put("stretchedEnabled", client.isStretchedEnabled());

			Dimension real = client.getRealDimensions();
			if (real != null)
			{
				out.put("realWidth", real.width);
				out.put("realHeight", real.height);
			}

			Dimension stretched = client.getStretchedDimensions();
			if (stretched != null)
			{
				out.put("stretchedWidth", stretched.width);
				out.put("stretchedHeight", stretched.height);
			}

			Canvas canvas = client.getCanvas();
			if (canvas != null)
			{
				out.put("canvasWidth", canvas.getWidth());
				out.put("canvasHeight", canvas.getHeight());
			}

			Point canvasLoc = RuneLiteUtil.getCanvasScreenLocation(client);
			out.put("canvasScreenX", canvasLoc.x);
			out.put("canvasScreenY", canvasLoc.y);

			double[] scale = RuneLiteUtil.getStretchScale(client);
			out.put("stretchScaleX", scale[0]);
			out.put("stretchScaleY", scale[1]);
			return out;
		});
		return spaces;
	}

	private static Map<String, Object> error(String message)
	{
		Map<String, Object> data = new HashMap<>();
		data.put("error", message);
		return data;
	}
}
