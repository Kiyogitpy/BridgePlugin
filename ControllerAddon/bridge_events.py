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
STALE_AFTER_MS = GAME_TICK_MS * 2  # (I2, 2026-08-21) how long since the last TICK packet was
                                    # actually processed before the model is treated as unsynced.
                                    # Judged by TickModel._synced_at, never by phase_ms -- see
                                    # TickModel.is_stale's own docstring for why (I3 made phase_ms
                                    # a pure atMillis+wall-clock computation that would otherwise
                                    # keep reporting a plausible in-range phase forever even with a
                                    # dead listener).


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
        """Milliseconds since the last tick boundary, 0 if never synced.

        (I3, 2026-08-21) Derived from `_at_millis` -- Java's System.currentTimeMillis() at the
        real tick boundary -- and `now`, the same OS wall clock read via Python's time.time().
        Deliberately NOT derived from `_synced_at` (when Python happened to drain the packet):
        that undercounts phase by however late the drain was, which is exactly the tick-phase
        blindness this module exists to remove. The modulo also makes this wrap correctly at
        each tick boundary instead of growing unbounded, assuming ticks keep landing on schedule
        even between TICK packets -- see is_stale for the separate check that the anchor itself
        hasn't gone stale."""
        if self._at_millis is None:
            return 0
        return (now * 1000.0 - self._at_millis) % GAME_TICK_MS

    def ms_until_next(self, now):
        """Milliseconds until the next boundary. Never negative: the client can stutter and a
        tick can overrun, and callers feed this straight into sleep()."""
        if self._at_millis is None:
            return 0
        return max(0.0, GAME_TICK_MS - self.phase_ms(now))

    def is_stale(self, now):
        """Whether the model has gone quiet -- no TICK packet actually processed in over
        STALE_AFTER_MS, or never synced at all.

        Judged by `_synced_at` (when a TICK was last drained), NOT by phase_ms: post-I3, phase_ms
        is pure atMillis-anchor-plus-wall-clock arithmetic, so it keeps reporting a plausible
        0-599ms phase forever even if the UDP listener died minutes ago and nothing has arrived
        since. Staleness needs the "have we actually heard anything lately" signal, which only
        `_synced_at` carries."""
        if self.tick is None or self._synced_at is None:
            return True
        return (now - self._synced_at) * 1000.0 > STALE_AFTER_MS


class EventStream:
    """Owns the drain of the bridge's UDP socket and keeps a short live view of it.

    controller.main() already binds the socket and drains it once per loop, and only one thing
    may bind port 50555 (a second bind crashed a run with WinError 10048). So this does not
    start a thread or open its own socket: main() calls drain() in place of poll_udp(), drain()
    feeds the mirror AND returns the events for on_event dispatch, and the blocking helpers below
    call pump() themselves so events cannot pile up undrained during a long tick() -- pump() is
    the low-level socket read shared by both paths; only drain() hands events to a dispatcher.

    (C1, 2026-08-21) pump() and drain() are deliberately two different methods, not one. Every
    blocking helper below (wait_for_event, wait_ticks, deadline_after_ticks) calls pump() in its
    own wait loop so events don't pile up undrained while it blocks -- but a helper's caller is
    mid-routine, not main()'s dispatch loop, and has no business independently deciding an event
    was "handled". main() is the only thing that may consume from the dispatch queue, via
    drain(), once per loop iteration. Before this split, main() called pump() directly and any
    event a wait_for_event call happened to pump while waiting was silently dropped on the floor
    -- CONFIRMED as the live mechanism losing CHAT_MESSAGE (catch counter, block_tile, the Hunter
    safety stop) and PLAYER_DIED (so safety.stopOnDeath never fired) during retrieve_trap's 3s
    HTTP confirm loop, the exact window this module exists to accelerate through.
    """

    def __init__(self, sock):
        self._sock = sock
        self.ticks = TickModel()
        self._recent = []
        self._pending = []   # events pumped but not yet handed to a dispatcher via drain()

    def pump(self, now=None):
        """Drain the socket, update the mirror and the pending-dispatch queue, return the events
        seen this call.

        This is the ONLY thing that reads the socket -- every caller in this module and in
        controller.main() goes through this, directly or via drain()/wait_for_event/wait_ticks/
        deadline_after_ticks. Do not call this from main()'s dispatch loop (use drain() there):
        pump()'s return value is "what arrived just now", not "what hasn't been dispatched yet",
        and the two diverge the moment any blocking helper below pumps mid-wait -- see the class
        docstring (C1) for the bug that split these into two methods.

        The mirror is pruned two ways on every insert: a count cap (EVENT_BUFFER_MAX, so this
        never grows into a log across an hours-long unattended run) and a tick-age cap
        (EVENT_BUFFER_MAX_TICKS, so a wait_for_event can never match something that arrived long
        enough ago that it no longer proves anything about "did the click I just made work").
        An event with no `tick` field -- CHAT_MESSAGE and other older event types, or any
        payload-less event -- has no age to judge, so it is only ever subject to the count cap.

        (M3, 2026-08-21) The age prune also has to survive a client restart: tickClock resets to
        0 on the Java side, but the buffer can still hold entries tagged with a tick from before
        the restart (e.g. 5000). A plain `current_tick - tick <= EVENT_BUFFER_MAX_TICKS` computes
        0 - 5000 = -5000, which passes, so a now-meaningless stale entry would read as "fresh"
        forever. Requiring `tick <= current_tick` too closes that.
        """
        now = time.time() if now is None else now
        events = controller.poll_udp(self._sock)
        for event_type, payload in events:
            if event_type == "TICK" and payload:
                self.ticks.update(payload.get("tick"), payload.get("atMillis"), now)
            self._recent.append((event_type, payload))
        self._pending.extend(events)
        if len(self._recent) > EVENT_BUFFER_MAX:
            del self._recent[:-EVENT_BUFFER_MAX]
        current_tick = self.ticks.tick
        if current_tick is not None:
            def _still_fresh(entry):
                _event_type, payload = entry
                tick = payload.get("tick") if payload else None
                if tick is None:
                    return True
                return 0 <= current_tick - tick <= EVENT_BUFFER_MAX_TICKS
            self._recent = [entry for entry in self._recent if _still_fresh(entry)]
        return events

    def drain(self, now=None):
        """Every event pumped since the last drain() call, for main()'s dispatch loop.

        (C1) This is what main() must call in place of pump() -- pump() alone hands events to
        nobody but its own immediate caller, so an event pumped inside a blocking helper's wait
        loop (wait_for_event, wait_ticks, deadline_after_ticks) would otherwise never reach
        handle_generic_events/routine.on_event at all. drain() pumps once itself (so a call site
        that never blocks on anything still sees fresh events) and then empties the whole pending
        queue built up by every pump() call since the last drain(), so nothing pumped mid-wait is
        lost."""
        self.pump(now=now)
        out, self._pending = self._pending, []
        return out

    def wait_for_event(self, types, match=None, timeout_s=1.0,
                       now_fn=time.time, sleep_fn=time.sleep):
        """Payload of the first matching event, or None if `timeout_s` passes without one.

        Pumps once before the first scan (M1, 2026-08-21): a caller typically clear()s the
        buffer and does other work before calling this, so the event is very often already
        sitting unread in the socket by the time this is called -- scanning the (necessarily
        empty) buffer first and only pumping after the first sleep wastes a full
        EVENT_POLL_INTERVAL_S on exactly the path this module exists to accelerate.

        Checks the buffer BEFORE waiting: the event is very often already in hand by the time a
        caller decides to wait for it, and blocking the full timeout on something we already
        received is the exact stall this module exists to remove.

        None is a normal outcome, not an error -- UDP is lossy, and the caller falls back to
        whatever HTTP check it would have used anyway.
        """
        if isinstance(types, str):
            types = (types,)
        deadline = now_fn() + timeout_s
        self.pump(now=now_fn())

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
        lands anywhere inside a 600ms window. Falls back to a plain sleep when the model is
        unsynced or stale (I2, 2026-08-21), so a caller is never worse off than it is today.

        Pumps first: without this, a boundary that landed just before this call was made would
        not be reflected in `self.ticks` yet, and the very first alignment computed below would
        be stale by up to one full poll gap.
        """
        now = now_fn()
        self.pump(now=now)
        if self.ticks.is_stale(now):
            sleep_fn(n * GAME_TICK_MS / 1000.0)
            return
        remaining = self.ticks.ms_until_next(now) + (n - 1) * GAME_TICK_MS
        sleep_fn(remaining / 1000.0)

    def deadline_after_ticks(self, n, now_fn=time.time):
        """Wall-clock time when `n` tick boundaries will have passed, for a caller that wants to
        overlap its own work (HTTP lookups, a mouse flight) with the wait instead of sleeping
        through it -- the same shape as wait_ticks, but returned rather than slept, so the caller
        can record it (e.g. chin_free_at) and let a later, narrower wait consume only whatever is
        left of the window. Added (I4, 2026-08-21) alongside chinchompa_hunting.py's event path
        adopting it in place of an inline wait_ticks call -- see that file's
        _await_placement_settled for why sleeping the window out here, rather than deferring it,
        reintroduced the exact pessimisation (7.44s -> 6.16s) that path was written to avoid.

        Falls back to a plain `now + n * GAME_TICK_MS` wall-clock deadline when the model is
        unsynced or stale, same guarantee as wait_ticks."""
        now = now_fn()
        self.pump(now=now)
        if self.ticks.is_stale(now):
            return now + n * GAME_TICK_MS / 1000.0
        return now + (self.ticks.ms_until_next(now) + (n - 1) * GAME_TICK_MS) / 1000.0

    def clear(self):
        """Drop buffered events -- for a caller that must not match something from before it
        started waiting."""
        self._recent.clear()
