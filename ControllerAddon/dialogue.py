# dialogue.py -- the chat dialogue, read off the vendored m8aq reader (GET /api/ui/dialogue).
#
# Promoted 2026-09-18 out of four private copies (golem_crafting, giants_foundry, crew,
# guardians_of_the_rift) for the slayer task loop, which walks slayer-master dialogues. Two
# measured facts shape it:
#
# - The reader has NO open/visible field (net.runelite.client.plugins.m8aq.api.ui.Dialogue has
#   only npcName/npcText/playerText/options/itemText/...). "Open" is therefore "any of those is
#   set". Collapsing it to bool(options) reads a "click here to continue" stage as CLOSED --
#   giants_foundry re-clicked Kovac mid-dialogue because of exactly that (2026-09-03).
# - `options` carries the menu TITLE as child 0, then the real options, then blank trailing
#   children (MEASURED live 2026-09-03: ['How many would you like to add?', '1', '3', 'X',
#   'All', '', '']). Number keys map to the real options, so the title must be dropped first.
#
# choose() pages through "More options..." (the slayer ring's teleport menu has two pages,
# CONFIRMED live 2026-09-14) and is bounded twice: MAX_PAGES, and "never re-press a
# page-forward that did not change the options", so a dialogue that loops cannot spin.

import time

import controller
import debug_log

MORE_OPTIONS = "more option"    # "More options..." -- the page-forward entry
MAX_PAGES = 3                   # the slayer ring has two; the cap is for a dialogue that loops
PAGE_S = 0.6                    # a page turn -> the new options being readable
CHOOSE_BUDGET_S = 6.0           # a click -> the menu, INCLUDING one page turn
POLL_S = 0.1


def read() -> dict:
    """The dialogue reader's state, or {} when the bridge/reader is unavailable or errored
    (an `error` key means the reader threw -- usually its interface is closed)."""
    d = controller.get_api("ui", "dialogue")
    if not d or d.get("error"):
        return {}
    return d


def is_open(d) -> bool:
    """True when ANY dialogue stage is up, including a continue-only one."""
    if not d:
        return False
    for key in ("open", "isOpen", "visible"):     # forward-compat if the reader grows one
        if isinstance(d.get(key), bool):
            return d[key]
    return bool(d.get("options") or d.get("npcText") or d.get("playerText") or d.get("itemText"))


def options(d=None) -> list:
    """The REAL options of the open chat menu, in number-key order (key = position + 1)."""
    if d is None:
        d = read()
    raw = [o if isinstance(o, str) else (o.get("text") or "") for o in (d.get("options") or [])]
    raw = raw[1:] if raw else raw            # child 0 is the title
    return [o for o in raw if o.strip()]


def find_option(opts, needle: str):
    """Index of the first option containing `needle` (case-insensitive), else None. Pure."""
    for i, text in enumerate(opts or []):
        if needle.lower() in (text or "").lower():
            return i
    return None


def choose(ctx, needle: str, budget_s: float = CHOOSE_BUDGET_S, log: str = "dialogue") -> bool:
    """Wait for a chat menu whose options contain `needle` and press its number key, paging
    through "More options..." when it is not on the first page. False when the budget runs out
    -- a normal outcome for the caller to handle, never a keypress guessed at."""
    deadline = time.time() + budget_s
    pages = 0
    seen = []
    while time.time() < deadline:
        opts = options()
        hit = find_option(opts, needle)
        if hit is not None:
            controller.key_tap(ctx.ser, str(hit + 1))
            return True
        more = find_option(opts, MORE_OPTIONS)
        if more is not None and opts != seen and pages < MAX_PAGES:
            debug_log.log(log, f"'{needle}' is not on this dialogue page -- taking '{opts[more]}'")
            seen = opts
            pages += 1
            controller.key_tap(ctx.ser, str(more + 1))
            time.sleep(PAGE_S)
            continue
        time.sleep(POLL_S)
    debug_log.log(log, f"no dialogue option containing '{needle}' appeared within {budget_s}s")
    return False


def close(ctx) -> None:
    """Dismiss whatever dialogue is up. ESC closes a chat menu without choosing anything."""
    controller.key_tap(ctx.ser, "ESC")
