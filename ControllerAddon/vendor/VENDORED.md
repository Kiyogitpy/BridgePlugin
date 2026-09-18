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

## `transports.tsv`, `agility_shortcuts.tsv`, `fairy_rings.tsv`, `spirit_trees.tsv`, `gnome_gliders.tsv`, `bank.tsv`

Added 2026-09-19 for the slayer task loop (spec §8.1, USER: "the findings on travelling sound
good, import that"). Same jar, same rule: **byte-identical, never hand-edited.**

    ~/.runelite/plugins/shortest-path_j65TV2lGDTkVcJlwg4jIvqU_Z2mHP1lUWx9t9lfkfRY.jar
    transports/transports.tsv            301844 bytes  5168 rows  sha256 802662c095456617...
    transports/agility_shortcuts.tsv      37821 bytes   572 rows  sha256 c987d113e2cad887...
    transports/fairy_rings.tsv             4512 bytes   112 rows  sha256 3d4133b8ad9b1070...
    transports/spirit_trees.tsv            8536 bytes   156 rows  sha256 055aca3e72c17095...
    transports/gnome_gliders.tsv           1906 bytes    22 rows  sha256 765d231dad846c2a...
    destinations/game_features/bank.tsv   10591 bytes   294 rows  sha256 0ee8cdf34bb37f88...

**Re-extract with `python tools/extract_shortest_path_tables.py`** (`--check` only reports
differences). It writes BYTES, so nothing re-encodes the files; git may still normalise line
endings on checkout, which is why the script compares against the jar rather than a stored hash.

**Why.** Every walking primitive here is same-plane, and the slayer sites are mostly underground.
`transports.tsv` is every ladder, staircase, cave mouth and trapdoor with the tile you click
from, the tile you land on, the object id, the menu option and its requirements -- MEASURED to
contain the Stronghold Slayer Cave, the Catacombs statue, the Slayer Tower basement ladder
(lands on plane 3) and the Taverley ladder. `bank.tsv` answers "which bank is near this site".

**Loaded by `ControllerAddon/transports.py`** (pure, header asserted by column name,
`tests/test_transports.py` pins the hub entrances, a fairy-ring code and row-count floors).
`spirit_trees.tsv` and `gnome_gliders.tsv` are vendored for a consistent snapshot but have **no
loader yet** -- nothing needs them until a route wants one.

Three traps in the data, all handled in the loader and pinned by test:
- ~316 rows are plain **doors** (`Open Door 9398`, adjacent same-plane tiles). worldmap bakes doors
  open and `travel.open_door_toward` opens the shut one, so they are classified `DOOR` and a
  route search leaves them out (`load_links(include_doors=False)`).
- ~668 rows are the **Wilderness ditch**, flagged so a search can refuse them.
- `fairy_rings.tsv` holds two row types (ring entrances, code exits) AND one **dial sequence**
  ("A I R - D L R - D J Q - A J S", the Fairy Queen's hideout). Reading that row as four
  aliases re-points the real AIR ring at the hideout; the first draft did exactly that.
- `Items` requirements are **symbolic names** (`MACHETE=1`, `SHANTAY_PASS=1|COINS=5`), not item
  ids, so they fail closed unless the caller supplies owned names in the same vocabulary.
