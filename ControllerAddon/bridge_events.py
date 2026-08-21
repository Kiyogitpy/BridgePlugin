"""Live view of the bridge's UDP event stream, plus a game-tick clock.

Shared infrastructure alongside controller.py, same as travel.py and banker.py.

This is an ACCELERATOR, never a source of truth. It answers "has X happened yet" so a routine
can stop sleeping conservative margins. Anything a routine ACTS on -- which entity to click,
whether an action succeeded -- is still confirmed against HTTP, because UDP is lossy and these
routines run unattended for hours on a real account. A dropped packet must cost latency, not a
wrong click.
"""

GAME_TICK_MS = 600      # OSRS server tick


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
