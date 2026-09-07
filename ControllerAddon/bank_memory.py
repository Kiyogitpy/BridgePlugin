# bank_memory.py
#
# "What do I own?", answerable while standing nowhere near a bank.
#
# The bridge side is BankMemoryProvider (GET /bank/memory), which snapshots the BANK
# ItemContainer on every ItemContainerChanged and persists it under
# ~/.runelite/bridge-bank-memory/<accountHash>.json. Read that class's header for why this is not
# just /bank -- in short, /bank can only report what the interface is DRAWING, so a bank search
# or a non-active tab silently truncates it, while this cannot be truncated at all.
#
# The shape here is deliberately "one fetch, many questions": load() makes a single HTTP call and
# returns an object you then interrogate as often as you like. Asking "do I own any of these
# twelve teleports" must not cost twelve round trips.
#
#     mem = bank_memory.load()
#     if mem.seen and mem.has("varrock teleport"):
#         ...
#
# Pass names= to push the filter down to the bridge instead, for a narrow repeated poll:
#
#     mem = bank_memory.load(names=["varrock teleport", "falador teleport"])
#
# THE ONE RULE WORTH READING: `seen` is not `items`. A bank we have never opened answers
# seen=False with items None, and a genuinely empty bank answers seen=True with []. They are
# different facts and code that collapses them will conclude you own nothing when it has simply
# never looked -- the same distinction the vendored m8aq readers draw (CLAUDE.md), and the same
# one that bit banking before.

import time

import controller


def _now():
    return time.time()


class BankMemory:
    """A remembered bank. Immutable; re-call load() for a fresh read."""

    __slots__ = ("seen", "items", "item_count", "captured_at", "captured_tick", "source",
                 "account_hash")

    def __init__(self, payload):
        payload = payload or {}
        items = payload.get("items")
        # A bridge with no /bank/memory (old client build) answers None, which api_get turns
        # into None -- indistinguishable from "never opened", and treated as such on purpose.
        self.seen = bool(payload.get("seen")) and items is not None
        self.items = list(items) if items is not None else []
        self.item_count = payload.get("itemCount", len(self.items))
        self.captured_at = payload.get("capturedAt")
        self.captured_tick = payload.get("capturedTick")
        self.source = payload.get("source")
        self.account_hash = payload.get("accountHash")

    @property
    def age_s(self):
        """Seconds since this snapshot was captured, or None if nothing ever was.

        Staleness policy lives with the CALLER, deliberately -- the provider only reports when it
        looked. For a bank trip it barely matters: every deposit and withdraw re-fires the
        capture, so the memory self-heals while you are standing at the bank.
        """
        if not self.seen or not self.captured_at:
            return None
        return _now() - (self.captured_at / 1000.0)

    def find(self, names):
        """Every remembered item whose name contains any of `names`.

        Case-insensitive SUBSTRING, matching banker.py's keepItems convention so the two agree
        about what a name means. The trap that comes with it: "teleport" matches every teleport
        you own, and "rune" matches runite bars, rune platebodies and every rune. Pass the
        fullest name you can.
        """
        needles = [n.strip().lower() for n in (names or []) if n and n.strip()]
        if not needles:
            return []
        return [
            item for item in self.items
            if any(needle in (item.get("name") or "").lower() for needle in needles)
        ]

    def has(self, name):
        """True only if we have LOOKED and the item is there. A bank we've never opened
        answers False here -- check `seen` first if you need to tell the two apart."""
        return bool(self.find([name]))

    def quantity(self, name):
        """Total remembered quantity across matching items, 0 if none, or None if we have never
        seen this account's bank. None is not zero: it means "unknown", and a caller deciding
        whether to make a trip must not read it as "you have none"."""
        if not self.seen:
            return None
        return sum(int(item.get("quantity") or 0) for item in self.find([name]))


def load(names=None):
    """One HTTP read of the remembered bank. Never raises, never blocks on the client thread.

    `names` pushes the filter to the bridge (~950 items is 60-80KB of JSON, so a repeated narrow
    poll should not drag the whole bank across). `item_count` still reports the WHOLE bank, so a
    filtered reply can never be mistaken for a small one.
    """
    return BankMemory(controller.get_bank_memory(names=names))


def _main(argv):
    """Live probe: `python bank_memory.py [name ...]`.

    Read-only and import-light (controller.py only, never the serial port), so it is safe to run
    alongside a routine -- same rationale as recorder.py and mcp_debug_server.py.
    """
    mem = load()
    if not mem.seen:
        print("no remembered bank -- open a bank once with the client running, then retry")
        print("(if this persists, the client may predate GET /bank/memory: rebuild it)")
        return 1

    age = mem.age_s
    print(f"account={mem.account_hash} source={mem.source} "
          f"items={mem.item_count} captured {age:.0f}s ago (tick {mem.captured_tick})")

    rows = mem.find(argv) if argv else mem.items
    if argv and not rows:
        print(f"nothing matching {argv}")
        return 0
    for item in sorted(rows, key=lambda i: -int(i.get("quantity") or 0)):
        print(f"  {int(item.get('quantity') or 0):>8}  {item.get('name')}  (id={item.get('id')})")
    return 0


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_main([a for a in _sys.argv[1:]]))
