# Agent instructions

## Researching OSRS/RuneLite internals

This repo automates RuneLite via an HTTP/UDP bridge (`controller.py` +
`src/main/java/com/example/providers/*`). When you need facts about game
mechanics, object/widget/interface IDs, or client behavior, check local data
in this priority order before reaching for anything else:

1. **`data/decompiled/`** -- CFR-decompiled Java source of the actual,
   currently-running RuneLite client (pulled from `~/.runelite/repository2`'s
   cached jars: `injected-client-*.jar` and `client-*.jar`). This is the
   ground truth for anything code-level: real widget/interface IDs, client
   script behavior, object interaction logic, provider-relevant API shapes.
   Prefer this over the `IdeaProjects/runelite` source checkout, which is a
   separate git clone that can drift out of date against the live client.
2. **`data/raw/`** -- a local OSRS Wiki dump (plain text, one file per
   article, organized by first letter of title, e.g.
   `data/raw/S/Shipwreck salvaging.txt`). Use this for game mechanics,
   drop tables, XP rates, strategy -- anything a wiki article would cover.
   Check here before fetching the live wiki over the web.
3. Only fall back to external methods (fetching the live OSRS Wiki, the
   `IdeaProjects/runelite` source checkout, abextm's cache2 live viewer at
   `https://abextm.github.io/cache2`, or asking the user to check in-game /
   via RuneLite's Widget Inspector) if the local data above doesn't cover
   what's needed. cache2's `@abextm/cache2` loader library in particular has
   no interface/widget loader (only `animation, dbrow, dbtable, enum, index,
   item, healthbar, hitsplat, npc, obj, param, sprite, struct, underlay,
   worldentity`) -- for widget/interface layout questions, `data/decompiled/`
   is the better source anyway, not a last resort.

**Caveat confirmed 2026-08-01**: `data/decompiled/` is generated from the compiled RuneLite
*client* jars, which contain the generic script-interpreter/rendering machinery, not game
content. Widget/interface *layout* (which button says what, where a dynamic row's children
live) is stored in the separately-downloaded binary game cache and read by that interpreter at
runtime -- it will NOT show up by grepping `data/decompiled/` (confirmed: zero hits for
"sailing"/"salvage" there despite the Sailing skill clearly being live content), and the
generated `net.runelite.api.gameval` ID-constant classes aren't bundled in these jars either
(they're a separate compile-time-only artifact -- see the `IdeaProjects/runelite` source
checkout for those instead, keeping in mind it can be stale). `data/decompiled/` is still the
right first stop for client-side *behavior/logic* questions (how a provider should read
something, how an existing RuneLite plugin does X) -- just not for cache-content questions like
"what widget id is this button." For those, prefer the `IdeaProjects/runelite` gameval constants
(for object/interface *group* ids) and abextm's cache2 live viewer (for `obj`/`item`/`npc`
defs) -- and if neither has it, it's a genuine in-game-verification-only gap.

`data/` is gitignored (bulk external reference data, not project source) --
if either directory is missing or stale, regenerate it rather than skipping
straight to live lookups: re-run the wiki scrape, or re-decompile the
current `~/.runelite/repository2` jars with a Java decompiler (CFR is a
single downloadable jar; `java -jar cfr.jar <jar> --outputdir <dir>`).

When something genuinely can't be resolved from local data, say so
explicitly and record what you learned back into `data/raw/` or
`data/decompiled/`-adjacent notes so the next session doesn't repeat the
research.

## Live bridge data (while the RuneLite client is running)

When the RuneLite client is running locally with this plugin loaded (e.g. via `./gradlew.bat run`),
the HTTP bridge server is reachable directly at `http://localhost:8080` (port from
`ControllerAddon/settings.py`'s `API_BASE`) -- query it yourself with `curl`/`WebFetch` rather than
asking the user to paste JSON back. This is ground truth for anything runtime/state-dependent that
no static source can answer: actual object/NPC/item data at the player's current location, current
`/player` position and stats, `/state` (window/canvas/UI-blocker info), `/inventory`, `/bank`,
`/sailing`, etc. -- see every `server.register("/...", ...)` call across
`src/main/java/com/example/providers/*.java` for the full endpoint list, or `controller.py`'s
`get_*()` functions for the Python-side shape each one returns.

This was how a real bug got diagnosed on 2026-08-01: `curl http://localhost:8080/objects` while
the user was on their boat showed every single object's `distance` field reading
`2147483647` (`Integer.MAX_VALUE`) -- not a naming/config mismatch as first suspected, but
`GeometryUtil.distanceToPlayer` (`WorldPoint.distanceTo`) returning its cross-plane sentinel
because the player (registered in a boat's own nested `WorldView`, plane 1, coordinates local to
that view) and top-level-world objects like shipwrecks (plane 0, real Gielinor coordinates) aren't
in the same coordinate space at all. Querying the live endpoint directly found this in minutes;
guessing from static code would not have.
