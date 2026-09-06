"""Shared event-driven wait helpers for routines (2026-09-06).

Lifted verbatim from routines/mastering_mixology.py once golem_crafting needed the same shape
(docs/superpowers/specs/2026-09-06-golem-crafting-events-and-falconry-design.md, section 1).
Every function takes `now_fn`/`sleep_fn` so each routine keeps its own patchable `_now`/`_sleep`
(the fake-clock fixture pattern from test_blast_furnace.py) and passes them through.

The contract is bridge_events' own: a pushed UDP signal decides WHEN to look, one HTTP
`confirm()` decides WHAT happened, and `confirm()` alone decides at the budget -- so an old
client, `--no-events`, or a dropped packet costs latency, never a wrong click. `ctx.events` may
be None everywhere here.
"""

import math
import random
import time

import bridge_events
import controller
import settings

GAME_TICK_S = bridge_events.GAME_TICK_MS / 1000.0
WALK_S_PER_TILE = 0.6           # a queued interaction is performed on ARRIVAL: budget the walk


def events_of(ctx):
    return getattr(ctx, "events", None)


def mark(ctx):
    """EventStream.mark() (a sequence number to pass as since=) or None without a stream."""
    events = events_of(ctx)
    return events.mark() if events is not None else None


def await_pre(ctx, signals, budget_s, confirm, *, since=None, reaction=True, shorten=None,
              now_fn=time.time, sleep_fn=time.sleep):
    """bridge_events.await_outcome with a PRE-CHECK: when confirm() already holds, return at
    once (padded by a human reaction draw when `reaction`) instead of waiting for a signal that
    has been and gone."""
    if confirm is not None:
        value = confirm()
        if value:
            if reaction:
                pad = controller.sample_human_delay(*settings.REACTION_DELAY_S)
                if pad > 0:
                    sleep_fn(pad)
            return bridge_events.Outcome(True, None, None, value, 0.0)
    return bridge_events.await_outcome(events_of(ctx), signals, budget_s, confirm=confirm,
                                       since=since, reaction=reaction, shorten=shorten,
                                       now_fn=now_fn, sleep_fn=sleep_fn)


def still_segment_s() -> float:
    """One human-length still segment (the measured distribution controller's in-wait fidgeter
    uses: median 1.5s, capped at 4s -- the human is never still longer than 4.5s in play)."""
    seg = random.lognormvariate(math.log(settings.IDLE_FIDGET_STILL_MEDIAN_S),
                                settings.IDLE_FIDGET_STILL_SIGMA)
    return max(0.5, min(settings.IDLE_FIDGET_MAX_STILL_S, seg))


def await_long(ctx, signals, budget_s, confirm, *, since=None, stop=None, fidget=True,
               reaction=True, now_fn=time.time, sleep_fn=time.sleep):
    """await_pre for a wait that can run tens of seconds (a station finishing, a golem side
    carving, a cue that may never come): the budget is spent in human-length still segments,
    with a resting-hand fidget between them (`fidget`, controller.fidget_once -- position-
    invariant, so a cursor parked on the next click target is still parked afterwards) and
    `stop()` re-checked between them. bridge_events' own 20ms poll never passes through
    human_delay, so without this a 30s wait was a frozen cursor -- the one loud bot signature
    the 2026-08-27 human diff found. `since` stays fixed across chunks, so an event landing
    during a fidget is still matched by the next chunk."""
    deadline = now_fn() + budget_s
    while True:
        remaining = deadline - now_fn()
        if remaining <= 0:
            return bridge_events.Outcome(False, None, None, None, budget_s)
        chunk = min(remaining, still_segment_s())
        out = await_pre(ctx, signals, chunk, confirm, since=since, reaction=reaction,
                        now_fn=now_fn, sleep_fn=sleep_fn)
        if out:
            return out
        if stop is not None and stop():
            return out
        if fidget and deadline - now_fn() > 1.0:
            controller.fidget_once()


def tick_pause(ctx, n=1, phase=None, *, now_fn=time.time, sleep_fn=time.sleep):
    """Sleep to the n-th tick boundary from now (aligned when the tick clock is live, a plain
    n*600ms otherwise), then a small drawn phase offset so clicks do not all land the same
    handful of milliseconds after the boundary."""
    events = events_of(ctx)
    if events is not None:
        events.wait_ticks(n, now_fn=now_fn, sleep_fn=sleep_fn)
    else:
        sleep_fn(n * GAME_TICK_S)
    if phase:
        pad = controller.sample_human_delay(*phase)
        if pad > 0:
            sleep_fn(pad)


def varbit_in(ids):
    ids = tuple(ids)
    return lambda payload: payload.get("varbitId") in ids


def chat_has(*needles):
    needles = tuple(n.lower() for n in needles)
    return lambda payload: all(n in (payload.get("message") or "").lower() for n in needles)


def walk_budget(entity, s_per_tile: float = WALK_S_PER_TILE) -> float:
    return ((entity or {}).get("distance") or 0) * s_per_tile
