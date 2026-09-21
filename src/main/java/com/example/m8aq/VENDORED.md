# Vendored: RLitePlus/m8aq-plugins `api/` readers

**Upstream:** https://github.com/RLitePlus/m8aq-plugins
**Branch:** main
**Synced commit:** 1130814de53dc3b1e19014f57424323d8cf628a6 ("Adopt widget snapshots across storage", 2026-08-25)
**Synced on:** 2026-08-29
**Subtree taken:** `src/main/java/com/example/m8aq/api/` (71 files)
**Upstream licence:** none declared (GitHub reported no LICENSE file, 2026-08-29)
**Upstream status:** **GONE.** `https://github.com/RLitePlus/m8aq-plugins` returns 404 to
both the API and anonymous `git ls-remote` as of 2026-09-20 -- deleted, renamed or made
private since the 2026-08-29 sync. `tools/sync-m8aq.sh` cannot clone it any more.

## Do not edit these files (one sanctioned exception, already applied)

Every file under `api/` is an upstream copy with exactly ONE mechanical change: the package was
renamed from `net.runelite.client.plugins.m8aq` to `com.example.m8aq` on **2026-09-20**. Nothing
else was touched -- no reformatting, no behaviour, no field names. Keep it that way: the value of
a near-verbatim copy is that a future diff against upstream is readable.

**Why the rename, since the original package was deliberate.** `REFLECTION_CHECK_REPLY` lets the
game server name a class and have the client resolve it with a plain `Class.forName` on the
CLIENT's own classloader (verified against the 1.12.39 deob -- the `runelite.reflectcheck.jar`
"answer as vanilla" path is dead code with zero callers). Our `run` task puts the injected
client, `com.example.*` and ZombieLite on ONE flat classpath, so anything here resolves. A class
sitting in `net.runelite.client.plugins.*` that is NOT a real RuneLite built-in, under a name
that was public on GitHub, was therefore a clean one-probe positive. `com.example.m8aq.*` is not
a published name. See CLAUDE.md "What the game client sends home" and `docs/telemetry.md`.

The old justification -- original package so a re-sync is a clean overwrite -- was already moot:
upstream is 404 (above), so there is nothing left to re-sync against.

Our own code lives in `com.example.providers.M8aqProvider` and only *imports* these classes.
If a reader needs different behaviour, wrap it there — do not patch it here.

## Re-syncing

    bash tools/sync-m8aq.sh

**This will fail at the clone until an upstream is found again** (see Upstream status). If one
turns up, the script needs a step it never had: after overwriting, rewrite each file's package
and import lines from `net.runelite.client.plugins.m8aq` to `com.example.m8aq`, and update
`ReaderCatalog.API_PACKAGE` with them -- that constant drives BOTH the recursive classpath scan
and `getGroupName`'s prefix stripping, so a copy that misses it discovers zero readers silently.

The script shallow-clones upstream, overwrites this directory, and rewrites the commit stamp
above. Review `git diff` afterwards: new reader classes become new `/api/...` endpoints with no
code change on our side, but an upstream **field rename** silently changes JSON keys that
`ControllerAddon/` may depend on.

## What makes a class a reader

`ReaderCatalog` discovers, by reflection, every public class under the `api` package with a
`public static <State> getState(Client)` method. 67 of the 71 files qualify; the other four
(`ReaderCatalog`, `scene/SceneActions`, `storage/ItemSlot`, `storage/PortableContainer`) are
support types and are skipped automatically.

`export/GameOutput` is a reader whose state is the aggregate of every *other* reader — it is
served as `/api/all` rather than as a per-reader endpoint.
