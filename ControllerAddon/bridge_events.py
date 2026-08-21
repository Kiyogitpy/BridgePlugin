"""Live view of the bridge's UDP event stream, plus a game-tick clock.

Shared infrastructure alongside controller.py, same as travel.py and banker.py.

This is an ACCELERATOR, never a source of truth. It answers "has X happened yet" so a routine
can stop sleeping conservative margins. Anything a routine ACTS on -- which entity to click,
whether an action succeeded -- is still confirmed against HTTP, because UDP is lossy and these
routines run unattended for hours on a real account. A dropped packet must cost latency, not a
wrong click.
"""

import time

import controller

GAME_TICK_MS = 600      # OSRS server tick
EVENT_BUFFER_MAX = 256       # closes the already-arrived race; deliberately not a log
EVENT_BUFFER_MAX_TICKS = 10  # age prune -- a count cap alone still lets a stale event outlive
                              # its usefulness during a quiet stretch (few events/tick), so a
                              # wait_for_event could match something from 30+ seconds ago
EVENT_POLL_INTERVAL_S = 0.02


class TickModel:
    """Where we are in the game-tick cycle, driven by the bridge's TICK event.

    `now` is injected rather than read internally so this is testable without a client.
    """

    def __init__(self):
        self.tick = None
        self._at_millis = None
        self._synced_at = None

    def update(self, tick, at_millis, now):
        self.tick = tick
        self._at_millis = at_millis
        self._synced_at = now

    def phase_ms(self, now):
        """Milliseconds since the last tick boundary, 0 if never synced."""
        if self._synced_at is None:
            return 0
        return (now - self._synced_at) * 1000.0

    def ms_until_next(self, now):
        """Milliseconds until the next boundary. Never negative: the client can stutter and a
        tick can overrun, and callers feed this straight into sleep()."""
        if self._synced_at is None:
            return 0
        return max(0.0, GAME_TICK_MS - self.phase_ms(now))


class EventStream:
    """Owns the drain of the bridge's UDP socket and keeps a short live view of it.

    controller.main() already binds the socket and drains it once per loop, and only one thing
    may bind port 50555 (a second bind crashed a run with WinError 10048). So this does not
    start a thread or open its own socket: main() calls pump() in place of poll_udp(), pump()
    feeds the mirror AND returns the events for on_event dispatch, and the blocking helpers
    below call pump() themselves so events cannot pile up undrained during a long tick().
    """

    def __init__(self, sock):
        self._sock = sock
        self.ticks = TickModel()
        self._recent = []

    def pump(self, now=None):
        """Drain the socket, update the mirror, return the events for the caller to dispatch.

        The mirror is pruned two ways on every insert: a count cap (EVENT_BUFFER_MAX, so this
        never grows into a log across an hours-long unattended run) and a tick-age cap
        (EVENT_BUFFER_MAX_TICKS, so a wait_for_event can never match something that arrived long
        enough ago that it no longer proves anything about "did the click I just made work").
        An event with no `tick` field -- CHAT_MESSAGE and other older event types, or any
        payload-less event -- has no age to judge, so it is only ever subject to the count cap.
        """
        now = time.time() if now is None else now
        events = controller.poll_udp(self._sock)
        for event_type, payload in events:
            if event_type == "TICK" and payload:
                self.ticks.update(payload.get("tick"), payload.get("atMillis"), now)
            self._recent.append((event_type, payload))
        if len(self._recent) > EVENT_BUFFER_MAX:
            del self._recent[:-EVENT_BUFFER_MAX]
        current_tick = self.ticks.tick
        if current_tick is not None:
            def _still_fresh(entry):
                _event_type, payload = entry
                tick = payload.get("tick") if payload else None
                if tick is None:
                    return True
                return current_tick - tick <= EVENT_BUFFER_MAX_TICKS
            self._recent = [entry for entry in self._recent if _still_fresh(entry)]
        return events

    def wait_for_event(self, types, match=None, timeout_s=1.0,
                       now_fn=time.time, sleep_fn=time.sleep):
        """Payload of the first matching event, or None if `timeout_s` passes without one.

        Checks the buffer BEFORE waiting: the event is very often already in hand by the time a
        caller decides to wait for it, and blocking the full timeout on something we already
        received is the exact stall this module exists to remove.

        None is a normal outcome, not an error -- UDP is lossy, and the caller falls back to
        whatever HTTP check it would have used anyway.
        """
        if isinstance(types, str):
            types = (types,)
        deadline = now_fn() + timeout_s

        while True:
            for event_type, payload in self._recent:
                if event_type in types and (match is None or match(payload or {})):
                    return payload or {}
            if now_fn() >= deadline:
                return None
            sleep_fn(EVENT_POLL_INTERVAL_S)
            self.pump(now=now_fn())

    def wait_ticks(self, n, now_fn=time.time, sleep_fn=time.sleep):
        """Sleep until `n` tick boundaries have passed, ALIGNED to the boundary rather than
        counted in wall-clock milliseconds.

        This is the difference between "wait 1.2s" and "act on the tick": an unaligned sleep
        starts at whatever arbitrary phase the caller happened to notice something at, so it
        lands anywhere inside a 600ms window. Falls back to a plain sleep when the clock has
        never synced, so a caller is never worse off than it is today.
        """
        if self.ticks.tick is None:
            sleep_fn(n * GAME_TICK_MS / 1000.0)
            return
        remaining = self.ticks.ms_until_next(now_fn()) + (n - 1) * GAME_TICK_MS
        sleep_fn(remaining / 1000.0)

    def clear(self):
        """Drop buffered events -- for a caller that must not match something from before it
        started waiting."""
        self._recent.clear()
