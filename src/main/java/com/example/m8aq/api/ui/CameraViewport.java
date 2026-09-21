package com.example.m8aq.api.ui;

import java.util.Objects;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import net.runelite.api.Client;

/** Provides camera position, orientation, focal point, and viewport dimensions. */
public final class CameraViewport
{
	private CameraViewport()
	{
	}

	/**
	 * @param client RuneLite client
	 * @return immutable camera and viewport snapshot
	 */
	public static State getState(Client client)
	{
		Objects.requireNonNull(client, "client");
		return new State(client.getCameraX(), client.getCameraY(), client.getCameraZ(), client.getCameraPitch(),
			client.getCameraYaw(), client.getCameraPitchTarget(), client.getCameraYawTarget(),
			client.getCameraFocalPointX(), client.getCameraFocalPointY(), client.getCameraFocalPointZ(),
			client.getViewportXOffset(), client.getViewportYOffset(), client.getViewportWidth(), client.getViewportHeight(),
			client.getCanvasWidth(), client.getCanvasHeight(), client.get3dZoom());
	}

	/** Immutable camera and viewport state. */
	@Getter
	@RequiredArgsConstructor(access = AccessLevel.PRIVATE)
	public static final class State
	{
		/** @return camera X */ private final int cameraX;
		/** @return camera Y */ private final int cameraY;
		/** @return camera height */ private final int cameraZ;
		/** @return camera pitch */ private final int pitch;
		/** @return camera yaw */ private final int yaw;
		/** @return target pitch */ private final int targetPitch;
		/** @return target yaw */ private final int targetYaw;
		/** @return focal-point X */ private final float focalPointX;
		/** @return focal-point Y */ private final float focalPointY;
		/** @return focal-point height */ private final float focalPointZ;
		/** @return viewport X offset */ private final int viewportX;
		/** @return viewport Y offset */ private final int viewportY;
		/** @return viewport width */ private final int viewportWidth;
		/** @return viewport height */ private final int viewportHeight;
		/** @return canvas width */ private final int canvasWidth;
		/** @return canvas height */ private final int canvasHeight;
		/** @return 3D zoom */ private final int zoom;
	}
}
