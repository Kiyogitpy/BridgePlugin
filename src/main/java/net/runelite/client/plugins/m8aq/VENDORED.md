# Vendored: RLitePlus/m8aq-plugins `api/` readers

**Upstream:** https://github.com/RLitePlus/m8aq-plugins
**Branch:** main
**Synced commit:** 1130814de53dc3b1e19014f57424323d8cf628a6 ("Adopt widget snapshots across storage", 2026-08-25)
**Synced on:** 2026-08-29
**Subtree taken:** `src/main/java/net/runelite/client/plugins/m8aq/api/` (71 files)
**Upstream licence:** none declared (GitHub reports no LICENSE file)

## Do not edit these files

Every file under `api/` is a byte-identical copy of upstream, kept in its **original package**
(`net.runelite.client.plugins.m8aq.api`) so that a re-sync is a clean overwrite rather than a
conflict-ridden rewrite of 71 files. Renaming the package, reformatting, or "fixing" anything
here breaks that property permanently.

Our own code lives in `com.example.providers.M8aqProvider` and only *imports* these classes.
If a reader needs different behaviour, wrap it there — do not patch it here.

## Re-syncing

    bash tools/sync-m8aq.sh

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
