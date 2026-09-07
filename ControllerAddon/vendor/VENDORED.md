# Vendored data

## `teleportation_items.tsv`

Copied **byte-identical** out of the Shortest Path plugin jar, which ships its transport tables
as plain TSV resources:

    ~/.runelite/plugins/shortest-path_Z2mHP1lUWx9t9lfkfRY.jar
    -> transports/teleportation_items.tsv

Extracted 2026-09-07. 340 rows, 31266 bytes.

**Why a copy and not the live plugin.** Our PluginMessage handshake with Shortest Path was
deleted in `1e96c8d` (`ShortestPathProvider.java`, 399 lines) and the plugin is no longer an
enabled Plugin Hub install, so there is nothing to handshake with. The handshake also carried a
hazard the flat table does not: its answers were governed by the user's own Shortest Path panel,
which once produced a route whose first edge was a `QUETZAL_WHISTLE` to a tile 454 tiles away.
USER 2026-09-07: "we jhust skip the handshake altogether and use the teleport data from the
plugin itself".

**Do not hand-edit this file.** Re-extract it from a newer jar instead, then diff. `teleports.py`
asserts the header line for exactly this reason -- the column order differs BETWEEN Shortest
Path's own transport files (`teleportation_spells.tsv` puts Items second where this one puts it
fourth), so a silent upstream re-order would mis-parse every row into plausible-looking nonsense
rather than failing. `tests/test_teleports.py` pins a known row (Ring of dueling -> Ferox
Enclave) and a row-count floor.

**Licence: the jar contains no LICENSE file** (checked at extraction). Same situation as the
vendored m8aq readers. Upstream is Skretzo's shortest-path plugin.
