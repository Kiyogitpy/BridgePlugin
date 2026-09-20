# Vendored data: Microbot's fork of the Shortest Path tables

Copied **byte-identical** out of a sparse clone of `github.com/chsami/Microbot` (BSD-2-Clause),
path `runelite-client/src/main/resources/net/runelite/client/plugins/microbot/shortestpath/`,
by `tools/vendor_microbot_tables.py`. Added 2026-09-20 for the slayer task loop (sub-project 3,
task E-pre; USER 2026-09-19: "you should have all this data if you look into microbot").

    commit 8bcc65203607f9819e02ec115748619299f9be61  (2026-09-16, surveyed in docs/slayer-sites/microbot-core-survey.md)

    transports.tsv            348795 bytes   5731 rows  sha256 e8e9da01b730c42b
    agility_shortcuts.tsv      19269 bytes    286 rows  sha256 3f852ffcd8748eca
    fairy_rings.tsv             2890 bytes    109 rows  sha256 0375df151d6d747e
    teleportation_items.tsv    25535 bytes    292 rows  sha256 f56d18fc9ccecba5
    npcs.tsv                    2329 bytes     27 rows  sha256 ef5bc7fc9d8ab3fb
    quetzals.tsv                1388 bytes     28 rows  sha256 0582230f825142d8
    blocked_edges.tsv           7314 bytes    127 rows  sha256 932c1c51006b7e13
    dangerous_tiles.tsv          740 bytes      8 rows  sha256 9360887b2a11401c
    restrictions.tsv            5598 bytes    214 rows  sha256 5e22a6c465f6f5d4

**Why a second copy of the same table.** The Plugin Hub jar's `transports.tsv` (`../transports.tsv`,
5168 rows) has no row for the Karuulm Slayer Dungeon at all; Microbot's has the whole
`# Mount Karuulm Dungeon` section (elevator 34359, the rock climbs, the two staircases), a
dusty-key gate row keyed on item 1590, and three tables the jar does not ship (blocked edges,
dangerous tiles, tile restrictions). MEASURED at vendoring: 5043 rows shared by
(origin, destination, object id), 649 only here, 99 only in the jar copy.

**Only `transports.tsv` is read by code today** (`transports.load_microbot_links`, merged into
`transports.load_links`). The other eight are pinned at the same commit so the next consumer
does not mix commits.

**The layout differs, so it goes through an ADAPTER, never a drop-in copy.** Header:

    # Origin  Destination  menuOption menuTarget objectID  Skills  Item IDs  Currency  Quests  Varbits  Varplayers  isMembers  Duration  Display info

- The object cell is `Option;Target;id` (`Activate;Elevator;34359`), where the jar's is
  space-separated. `transports.normalise_object_info` rewrites it to the space form at load so
  every consumer sees one format; no Microbot option contains whitespace (pinned by test).
- `Item IDs` are NUMERIC. `1 2;3` means (1 or 2) and 3 -- semicolon is AND, space is OR, the
  order `Transport.java` splits them in. The jar's `Items` column is symbolic (`DUSTY_KEY=1`).
- `Currency` is `<amount> <name>` (`10 Coins`). One row carries `3741=1` there by mistake; the
  parser makes that unsatisfiable rather than guessing.
- `Varplayers` (jar: `VarPlayers`) uses the same `id>value` grammar. `isMembers` is `Y` on 6
  rows and is recorded, not gated.

**Do not hand-edit these files.** Re-run `tools/vendor_microbot_tables.py <clone>` against a
newer checkout, then diff, then update the commit and manifest above and re-run
`tools/slayer_route_report.py`. `tests/test_transports.py` pins the Karuulm elevator, that every
jar row still resolves identically after the merge, and the item-id and currency grammars.

**What was taken from the Java, as strings only:** `util/walker/door/Rs2DoorClassifier.java`'s
door-like name fragments and pass-through action priority, ported into `travel.py`
(`DOOR_NAME_HINTS`, `DOOR_ACTION_PRIORITY`, `is_door_name`, `door_walk_action`). No Microbot
code is on our build path and none of its input layer was read for reuse -- input here is the
physical Arduino only.
