# Microbot-core survey (POH / walker / magic / dialogue / transports)

Read-only research over a sparse checkout of the Microbot RuneLite fork (BSD-2), for porting
FACTS (ids, widget ids, varbits, menu strings, table rows) into our HTTP-bridge-driven
controller. We never port their input/execution code, only the facts it encodes. All paths
below are relative to
`...\scratchpad\microbot-core\runelite-client\src\main\java\net\runelite\client\plugins\microbot\`
unless stated otherwise. "The code says X" is marked as such; anything else is my inference,
marked "(inference)".

---

## 1. POH (`util/poh/*`)

### Detecting "inside the house"

`util/poh/PohTeleports.java:47-56` (`isInHouse()`):
```java
if (!Rs2Player.IsInInstance()) return false;
return Microbot.getRs2TileObjectCache().query().withId(ObjectID.POH_EXIT_PORTAL).nearest() != null;
```
So it is a **two-part check**: (a) player is in an instance at all, AND (b) a `POH_EXIT_PORTAL`
object (the portal you leave through) is found nearby via a tile-object cache query. No region-id
or varbit check for "am I in a POH" — the exit-portal object is the ground truth. Comment at
lines 48-51 explicitly warns that `Rs2GameObject.getGameObject` anchors off
`Rs2Player.getWorldLocation()`, which inside a POH instance returns the **overworld-template**
tile, not the real scene tile — this is the same instance-space gotcha our own CLAUDE.md
documents for `WorldPoint.fromLocalInstance()`. Their fix is a separate cache query; ours would
be the existing `instX`/`instY` bridge fields.

`hasHouse()` (line 77-79): `HouseLocation.getHouseLocation() != null` — i.e., "do we know a
house location" is answered purely from the `POH_HOUSE_LOCATION` varbit (see section 2), not
from any bank/config state.

### Jewellery box

- Object ids per tier, `util/poh/data/JewelleryBoxType.java:15-19`:
  - `NONE(-1)`
  - `BASIC(ObjectID.POH_JEWELLERY_BOX_1)`
  - `FANCY(ObjectID.POH_JEWELLERY_BOX_2)`
  - `ORNATE(ObjectID.POH_JEWELLERY_BOX_3)`
  (raw numeric ids not resolved in this checkout — `ObjectID` is a generated `gameval` constants
  class not included in the sparse checkout; only the symbolic names are available here.)
- Menu option: `"Teleport Menu"` — `PohTeleports.java:102`:
  `Rs2GameObject.interact(JewelleryBoxType.getObject(), "Teleport Menu")`.
- Interface: `Rs2Widget.getWidget(InterfaceID.POH_JEWELLERY_BOX, 0)` (`PohTeleports.java:116`) —
  a symbolic `InterfaceID.POH_JEWELLERY_BOX` constant, not a raw group number in this checkout.
- Destination matching: **by TEXT, not letter/index**. `interactWithJewelleryBoxWidget`
  (`PohTeleports.java:126-150`) calls
  `Rs2Widget.findWidget(jewelleryLocationEnum.getDestination().toLowerCase(), mainWidget.getStaticChildren())`
  — a case-insensitive substring/text match over the box's static children, then checks
  `widget.getText().contains("<str>")` (strikethrough markup = "not unlocked yet", same idea as
  our own bank-search caveat about `<str>` tags) before clicking. **This does not use keyboard
  shortcut letters at all** — contrast with the Portal Nexus below, which does.
- **Full destination table**: `JewelleryBox.java` (enum, one entry per teleport) lists only the
  teleport's **name** (via `JewelleryLocationEnum`, e.g. `BARBARIAN_ASSAULT`,
  `BURTHORPE_GAMES_ROOM`, `TEARS_OF_GUTHIX`, `CORPOREAL_BEAST`, `WINTERTODT_CAMP`, `PVP_ARENA`,
  `FEROX_ENCLAVE`, `CASTLE_WARS`, `FORTIS_COLOSSEUM`, `WARRIORS_GUILD`, `CHAMPIONS_GUILD`,
  `EDGEVILLE_MONASTERY`, `RANGING_GUILD`, `FISHING_GUILD_NECK`, `MINING_GUILD`,
  `CRAFTING_GUILD`, `COOKING_GUILD`, `WOODCUTTING_GUILD`, `FARMING_GUILD`, `EDGEVILLE`,
  `KARAMJA`, `DRAYNOR_VILLAGE`, `AL_KHARID`, `MISCELLANIA`, `GRAND_EXCHANGE`, `FALADOR_PARK`,
  `DONDAKAN`) plus **which box tier(s) it's available in** (`JewelleryBoxType.BASIC/FANCY/ORNATE`
  varargs per entry, `JewelleryBox.java:15-51`) — e.g. Games/Duelling-necklace teleports are in
  all three tiers, Combat-bracelet/Skills-necklace teleports need FANCY or ORNATE, and
  Glory/Wealth-ring teleports need ORNATE only. **No landing coordinates and no letter/index are
  stored here** — `getDestination()` (line 62-64) just forwards to
  `location.getLocation()`, where `location` is a `JewelleryLocationEnum` constant. That enum
  lives under `util/equipment/` which is **outside the sparse checkout given to me** — I could not
  read its coordinates. `FORTIS_COLOSSEUM` is explicitly unimplemented (comment
  `PohTeleports.java:87-96`: "This teleport has not been added... Requirements: Hero 12,000...
  via the ring of dueling").
- **Basic/Fancy boxes are ASSUMED to share the ornate box's interface** — `PohTeleports.java`'s
  own class-level TODO (line 32) says this is unverified: "Check if the Basic jewellery box &
  Fancy jewellery box use the same interface as the ornate jewellery box." Treat this as an open
  question, not a settled fact, if porting box-tier-specific behavior.

### Fairy ring (in POH)

`PohTeleports.java:219-249`: POH fairy ring/spirit-tree ids are resolved by **name lookup**,
not hardcoded ids: `Rs2GameObject.getObjectIdsByName("poh_spirit_ring")` (shared by both fairy
ring and spirit tree — comment implies the object family covers both) unioned with
`"poh_fairy_ring"` for fairy rings and `"poh_spirit_tree"` for spirit trees. `isFairyRing`/
`isSpiritTree` just test membership in those id sets. `getFairyRings()`/`getSpiritTree()` scan
the whole loaded scene (`findPohObjectAnywhere`, lines 263-282) rather than using
`Rs2Player.getWorldLocation()` as an anchor — same instance-space workaround as `isInHouse()`.
**Execution of the actual dial** (rotating dials, clicking teleport) is NOT in `PohTeleports.java`
at all — it's the same generic `handleFairyRing` in `Rs2Walker.java` used for overworld fairy
rings; see section 3 below (the code checks `PohTeleports.isInHouse()` first and calls
`PohTeleports.getFairyRings()` for the object lookup when true — `Rs2Walker.java:11705`).

### Portal Nexus

`PohTeleports.java:152-217` (`usePortalNexus`/`interactWithPortalNexusWidget`):
- Finds the nexus object among `NexusPortal.PORTAL_IDS` (`NexusPortal.java:87-95`) — three
  families: standalone portal (`POH_NEXUS_PORTAL_1/2/3`, `POH_NEXUS_PORTAL_LEAGUE_5`),
  "Teleportation Chamber" room variants (`POH_TELENEXUS_1`, `POH_TELENEXUS_2_MIDDLE/SIDE/CORNER`,
  `POH_TELENEXUS_3`), and amulet-topped variants (`POH_NEXUS_4_AMULET`, `POH_NEXUS_5_AMULET`).
- Menu option: `"Teleport Menu"` (`PohTeleports.java:163`).
- Interface: `Rs2Widget.getWidget(InterfaceID.TELENEXUS_TELEPORT, 0)` — **matches the user's
  measured `590`/`590.5` finding**: `InterfaceID.TELENEXUS_TELEPORT` is almost certainly
  interface 590 by name (TELE-NEXUS).
- **Destination matching: by TEXT first, then by a SHORTCUT LETTER extracted from the widget's
  own markup**, not click-through. `interactWithPortalNexusWidget` (lines 184-217):
  1. `Rs2Widget.findWidget(nexusPortal.getText().toLowerCase(), staticChildren)` — text match to
     find the right child widget for logging/verification purposes.
  2. Regex `<col=ffffff>(.*?)</col>` extracted from `widget.getText()` — the widget text embeds
     its own single-letter keyboard shortcut in white-colored markup (e.g. `<col=ffffff>O</col>`
     — matches the user's measured "H: Cooking Guild" text shape, where the leading letter is
     this same shortcut markup).
  3. **Types that single letter via the keyboard** (`Rs2Keyboard.typeString`) rather than
     clicking, with the comment (lines 203-205): *"to avoid scrolling — some of the teleports are
     not visible in the ui to click on. Using shortkeys should always work even if the teleport
     is not visible on the screen."* This is a real, generalizable trick: **the interface list is
     keyboard-navigable by its own embedded letter**, so a routine never needs to scroll the
     child list.
  4. After typing, checks `Rs2Widget.isWildernessInterfaceOpen()` (polled up to 1000ms) and calls
     `Rs2Widget.enterWilderness()` if so — i.e. some nexus destinations are in the Wilderness and
     trigger the standard "you are entering the Wilderness" confirmation, handled generically.
  5. Confirms arrival by polling `Rs2Player.getWorldLocation().distanceTo(nexusPortal.getLocation()) < 10`
     — a coarse 10-tile radius check, not exact-tile.
- Full destination list (`NexusPortal.java:26-57`, 30 entries) with associated spellbook/type tag
  and a `TeleportLocationData` location reference (coordinates not resolvable in this checkout —
  see caveat below): Varrock, Varrock GE (only unlocked with `VARROCK_DIARY_MEDIUM_COMPLETE`==1,
  free add-on to Varrock — `getAvailableTeleports()` line 111-117), Lumbridge, Falador, Camelot,
  Ardougne, Watchtower, Marim, Senntisten, Kharyrll, Carrallanger, Kourend Castle, Waterbirth
  Island, Annakarl, Ghorrock, Lunar Isle, Catherby, Fishing Guild, Troll Stronghold, Weiss,
  Arceuus Library, Draynor Manor, Battlefront, Mind Altar, Salve Graveyard, Fenkenstrain's
  Castle, West Ardougne, Harmony Island, Cemetery, Barrows, Ape Atoll Dungeon, Civitas illa
  Fortis.
- Which of these are UNLOCKED is driven by **35 varbits** `POH_NEXUS_TELE_1`..`POH_NEXUS_TELE_35`
  (`NexusPortal.java:124-160`), each holding an ordinal index into `NexusPortal.values()` (value
  `1` is special-cased to mean Varrock, `getAvailableTeleports()` lines 105-122) — i.e. the Nexus
  has 35 configurable "slots" and each varbit says which teleport (if any, `<=0` = empty) occupies
  that slot.

### Mounted glory / digsite pendant / Xeric's talisman / Myths cape (POH "trophy" teleports)

All four are **decorative objects** (`DecorativeObject`, not `GameObject`) mounted on the wall,
and all follow the same two-path pattern: if the object's OWN id already encodes the single
destination, left-click that action directly; otherwise open a "Teleport menu" and read/click a
widget by NAME.

- **Mounted glory** (`MountedGlory.java`): single object id `POH_TROPHY_AMULETOFGLORY_4`
  (`isMountedGlory`, `getObject`), 4 destinations (Edgeville, Karamja, Draynor Village, Al
  Kharid), each executed as `Rs2GameObject.interact(getObject(), destinationName)` directly (no
  widget at all — line 31-33) — implies the glory trophy's menu already lists all 4 named
  options.
- **Mounted digsite pendant** (`MountedDigsite.java`): id-per-tier
  (`POH_AMULET_DIG_FOSSIL`/`POH_AMULET_DIG_DIGSITE`/`POH_AMULET_DIG_LITHKREN`, base
  `POH_AMULET_DIGSITE`), 3 destinations (Fossil Island, Digsite, Lithkren). If the mounted
  object's own id equals the target tier's id, direct left-click by destination name (lines
  32-39); otherwise `"Teleport menu"` then `Rs2Widget.getWidget(InterfaceID.MENU, 3)` (raw
  interface id 3?? — `InterfaceID.MENU` symbolic, child 3) then `Rs2Widget.clickWidget(destinationName)`
  by text.
- **Xeric's talisman** (`MountedXerics.java`): same shape, 4 destinations (Lookout, Glade,
  Inferno, Heart) — `HONOUR` is commented out with the note "Require's Ancient Tablet to be used
  on Xeric's Talisman" (unimplemented precondition). Same `InterfaceID.MENU` child-3 widget
  fallback.
- **Mythical cape** (`MountedMythical.java`): single destination, Myths Guild, object id
  `POH_TROPHY_MYTHICAL_CAPE`, menu option **`"Teleport"`** (not "Teleport menu" — line 30).

### Portals used to leave the house (exit + destination portals)

- **Exit portal**: `ObjectID.POH_EXIT_PORTAL` (used only as the "am I in a POH" sentinel, section
  above — no separate "leave" execution code found in this checkout; presumably a generic
  `Rs2GameObject.interact(..., "Enter")`/"Exit" elsewhere not in scope).
- **Destination portals** (`PohPortal.java`): the portal you build for a specific spellbook
  destination, opened via `"enter"` (`execute()`, line 168, lower-case action string). 4 wood
  finishes per destination (`POH_PORTAL_MAG_*` / `_MARBLE_*` / `_TEAK_*` / `_MAHOGANY_*`) plus a
  League-5 variant family, all mapped to the SAME logical destination via a
  `Map<Integer, PohPortal> BY_OBJECT_ID` built from every finish's id (lines 171-187) — i.e. wood
  type never matters for routing, only decoration. ~35 destinations covering standard spellbook,
  Ancient Magicks, Lunar, and Arceuus teleport locations, plus a block explicitly marked
  "Backfilled 2026-07-20 from upstream Skretzo/shortest-path teleportation_portals_poh.tsv" (lines
  111-137: Barbarian Outpost, Fishing Guild, Port Khazard, Marim, Civitas illa Fortis, Ourania,
  Cemetery, Dareeyak, Trollheim, Lassar, Ice Plateau, Paddewwa) with **literal `WorldPoint`
  coordinates given directly in source** (not via `TeleportLocationData`), e.g.:
  - `BARBARIAN_OUTPOST` → `(2543, 3569, 0)`
  - `FISHING_GUILD` → `(2613, 3391, 0)`
  - `PORT_KHAZARD` → `(2636, 3167, 0)`
  - `MARIM` → `(2797, 2798, 1)`
  - `CIVITAS_ILLA_FORTIS` → `(1681, 3133, 0)`
  - `OURANIA` → `(2468, 3246, 0)`
  - `CEMETERY` → `(2981, 3764, 0)`
  - `DAREEYAK` → `(2968, 3696, 0)`
  - `TROLLHEIM` → `(2890, 3679, 0)`
  - `LASSAR` → `(3004, 3470, 0)`
  - `ICE_PLATEAU` → `(2975, 3938, 0)`
  - `PADDEWWA` → `(3097, 9881, 0)` (note plane-0 coordinate in the 9800s y-range — an
    underground/instance-style template coordinate, consistent with Ancient Magicks' Paddewwa
    teleport landing in a dungeon-like area).

### Pool of refreshment / "building mode" / teleport-inside setting

**Found nothing.** Grepped the whole `util/poh/` tree for `Pool`, `PoolOfRefreshment`,
`BuildingMode`, `TELEPORT_INSIDE`, `houseStyle` — zero matches. Microbot's POH module does not
model the pool of refreshment or a "build mode" toggle at all in this checkout. (inference: not
needed for their teleport-only use case.)

---

## 2. House location (`util/poh/data/HouseLocation.java`)

Varbit: `VarbitID.POH_HOUSE_LOCATION` (`HouseLocation.java:33`,
`Microbot.getVarbitValue(VarbitID.POH_HOUSE_LOCATION)`). Table (`HouseLocation.java:16-24`),
value → city, with the object id of the OVERWORLD entrance portal for that city (i.e. the portal
you click on the map to enter your house, not a POH-internal object) and a `WorldPoint` sourced
from `net.runelite.client.plugins.worldmap.TeleportLocationData` (a RuneLite core class **not
included in this sparse checkout**, so I could not resolve the literal coordinates — only the
symbolic reference):

| Varbit value | City | Portal location (`TeleportLocationData` constant) | Entrance portal object id |
|---|---|---|---|
| 1 | Rimmington | `CONSTRUCTION_CAPE_RIMMINGTON` | `ObjectID.POH_RIMMINGTON_PORTAL` |
| 2 | Taverley | `CONSTRUCTION_CAPE_TAVERLEY` | `ObjectID.POH_TAVERLY_PORTAL` |
| 3 | Pollnivneach | `CONSTRUCTION_CAPE_POLLNIVNEACH` | `ObjectID.POH_POLLNIVNEACH_PORTAL` |
| 4 | Rellekka | `CONSTRUCTION_CAPE_RELLEKKA` | `ObjectID.POH_RELLEKKA_PORTAL` |
| 5 | Brimhaven | `CONSTRUCTION_CAPE_BRIMHAVEN` | `ObjectID.POH_BRIMHAVEN_PORTAL` |
| 6 | Yanille | `CONSTRUCTION_CAPE_YANILLE` | `ObjectID.POH_YANILLE_PORTAL` |
| 8 | Hosidius | `CONSTRUCTION_CAPE_HOSIDIUS` | `PORTAL_28822` (raw `net.runelite.api.ObjectID` constant, not `gameval`) |
| 9 | Prifddinas | `CONSTRUCTION_CAPE_PRIFDDINAS` | `ObjectID.POH_PRIFDDINAS_PORTAL` |
| 13 | Aldarin | `CONSTRUCTION_CAPE_ALDARIN` | `ObjectID.POH_ALDARIN_PORTAL` |

Note: **values 0, 7, 10-12 are absent** — 0 presumably means "no house built yet" (falls through
to `getHouseLocation()` returning `null`, i.e. `hasHouse()` is false), and 7/10-12 are simply
unused/unmapped ids in this table (could be other real house locations e.g. Lumbridge if it
exists — not modelled here, or could be gaps in the varbit's own value space; not verified either
way in this checkout).

---

## 3. Fairy rings in the walker (`util/walker/Rs2Walker.java`)

The dial is operated entirely inside one private method, `handleFairyRing(Transport)`
(`Rs2Walker.java:11701-11777`), shared by BOTH overworld fairy rings and the POH fairy ring (it
branches on `PohTeleports.isInHouse()` for object lookup only, line 11705).

### Object lookup / which ids count as "a fairy ring"

- In a POH: `PohTeleports.getFairyRings()` (name-based lookup, section 1).
- On the overworld: `Rs2GameObject.getAll(o -> Objects.equals(o.getWorldLocation(), transport.getOrigin())).stream().findFirst()`
  — **not an id list at all**, just "whatever game object sits on the transport row's own origin
  tile". The generic transport TSV row supplies the coordinate; no id enumeration exists for
  overworld fairy rings in this file.
- Reachability gate: `!PohTeleports.isInHouse() && !Rs2GameObject.canWalkTo(fairyRingObject, 25)` → abort
  (25-tile walkability check before attempting to interact).

### Staff / diary requirement

- `VarbitID.LUMBRIDGE_DIARY_ELITE_COMPLETE == 1` → **no staff needed at all** (elite diary perk).
- Otherwise: must be wielding `"Dramen staff"` or `"Lunar staff"`; if not worn but in inventory,
  equips one (preferring Dramen), remembering the previously-worn weapon (`startingWeapon`) to
  re-equip after the teleport (lines 11771-11775). If neither worn nor in inventory → `return false`
  (hard failure, no fallback).
- Also gated at the transport-filtering level (`applyTransportFiltering`,
  `Rs2Walker.java:4600-4605`): if elite diary not complete, sets
  `t.setItemIdRequirements(Set.of(Set.of(ItemID.DRAMEN_STAFF, ...)))` on `FAIRY_RING` transports
  (truncated in my read — likely `DRAMEN_STAFF` OR `LUNAR_STAFF`) so the pathfinder itself won't
  route through a fairy ring the player can't use.

### Travel-log / "last-destination" shortcut

Before touching the dial interface at all, it checks the object's own available menu actions via
`ObjectComposition`:
```java
String lastDestinationAction = "last-destination (" + transport.getDisplayInfo() + ")";
String treeLastDestinationAction = "Ring-last-destination (" + transport.getDisplayInfo() + ")";
```
(`Rs2Walker.java:11730-11739`) — if the ring/tree's context menu already offers a
`"last-destination (<code>)"` (fairy ring) or `"Ring-last-destination (<code>)"` (spirit tree,
apparently sharing this code path) action matching the SPECIFIC code needed, it clicks that
directly and skips the whole dial UI — this is exactly the OSRS "repeat last destination" menu
option, gated on the code matching (so it only fires if the ring's last-used code happens to be
the one wanted). Otherwise falls through to `"Configure"` / `"Ring-configure"` (line 11742-11746).

### Dial widget ids

Confirmed via arithmetic against `group*65536+child` packing (matches the user's own convention):
all in interface group **398** — consistent with the user's measured "Configure opens dial
interface 398" and "confirm button 398.26".

| Constant | Raw id | Decoded (`group.child`) | Role |
|---|---|---|---|
| `SLOT_ONE` | 26083331 | 398.3 | Dial slot 1 widget (read `.getRotationY()`) |
| `SLOT_TWO` | 26083332 | 398.4 | Dial slot 2 |
| `SLOT_THREE` | 26083333 | 398.5 | Dial slot 3 |
| `SLOT_ONE_CW_ROTATION` | 26083347 | 398.19 | Rotate slot 1 clockwise button |
| `SLOT_ONE_ACW_ROTATION` | 26083348 | 398.20 | Rotate slot 1 anticlockwise button |
| `SLOT_TWO_CW_ROTATION` | 26083349 | 398.21 | Rotate slot 2 clockwise |
| `SLOT_TWO_ACW_ROTATION` | 26083350 | 398.22 | Rotate slot 2 anticlockwise |
| `SLOT_THREE_CW_ROTATION` | 26083351 | 398.23 | Rotate slot 3 clockwise |
| `SLOT_THREE_ACW_ROTATION` | 26083352 | 398.24 | Rotate slot 3 anticlockwise |
| `ComponentID.FAIRY_RING_TELEPORT_BUTTON` | (symbolic) | (not decoded here — presumably 398.26, matching the user's measured confirm button) | Confirm / teleport button |

`fairyRingGraphicId = 569` (line 11699) — the spotanim id played while teleporting; used as the
success signal: `sleepUntil(() -> Rs2Player.getGraphicId() == fairyRingGraphicId, 5000)` then
`sleepUntil(() -> Objects.equals(Rs2Player.getWorldLocation(), transport.getDestination()) && Rs2Player.getGraphicId() != fairyRingGraphicId, 10000)`
— i.e. confirmed by (a) the graphic appearing, then (b) world position matching the destination
AND the graphic having ended. **This is exactly the kind of sub-tick, event-driven confirmation
our own `await_outcome`/GRAPHICS_OBJECT_CREATED plumbing is built for** — 569 could be subscribed
as a graphic-changed signal if we ever build fairy-ring travel ourselves.

### Rotation math (letter → dial position)

`rotateSlotToDesiredRotation` (lines 11790-11819): computes clockwise vs anticlockwise turn count
from `(desired - current + 2048) % 2048`, in units of 512 per detent (4 positions per dial, since
2048/512=4), picks whichever direction is fewer clicks, and clicks the corresponding rotation
button that many times, polling `getRotationY()` after each click.

`getDesiredRotation(char letter)` (lines 11828-11849) — **only maps A/B/C/D and their
"aliases" I/J/K/L and P/Q/R/S to the same 4 rotation values**, confirming the classic fairy-ring
code alphabet (each of the 3 dials cycles through 4 symbols, and OSRS spells them with 3 parallel
letter ranges A-D/I-L/P-S so a code is always 3 letters, one from each range):

| Letters | Rotation value |
|---|---|
| A, I, P | 0 |
| B, J, Q | 512 |
| C, K, R | 1024 |
| D, L, S | 1536 |
| anything else | -1 (invalid) |

`transport.getDisplayInfo().charAt(0/1/2)` supplies the 3 letters — i.e. **the fairy code IS the
transport row's own `Display info` TSV column** (see section 7 for the `fairy_rings.tsv` shape:
codes like `AIQ`, `DLP` etc. live in that column on the DESTINATION-anchored rows).

---

## 4. `Rs2Magic` NPC Contact (`util/magic/Rs2Magic.java`)

`npcContact(String npcName)`, lines 330-361:

1. Requires Lunar spellbook: `isSpellbook(Rs2Spellbook.LUNAR)`, else logs and aborts (line 332-335).
2. Casts via the generic `cast(MagicAction.NPC_CONTACT)` (line 337) — routed through the same
   `Rs2Magic.cast()` used for all spells: switches to the Magic tab, resolves the spell's own
   widget id from `MagicAction.NPC_CONTACT.getWidgetId()`, and dispatches a `CC_OP` menu action
   on it (spell names containing "teleport" or "bones to", or whose actions include "cast",
   route through `CC_OP`; the codepath at line 164-170 would send `NPC_CONTACT` through `CC_OP`
   only if its action list satisfies that check — not fully confirmed from this file alone which
   branch NPC Contact takes, since its exact `actions` array isn't shown here).
3. **`CHOOSE_CHARACTER_WIDGET_ID = 4915200`** (line 330) — decodes to **group 75, child 0**
   (`4915200 / 65536 = 75` exactly) — matches the user's own measured "picker interface 75"
   finding. Waits up to 5000ms (100ms poll) for this widget to become visible after casting
   (line 338).
4. **Finds the target NPC row by TEXT SEARCH, not by a fixed index/id table**:
   `Rs2Widget.findWidget(npcName)` (line 343) searches globally for a widget whose text matches
   `npcName`. **There is no enum or list anywhere in this file (or found elsewhere in the whole
   `microbot` tree via grep for `npcContact`/`NPC_CONTACT`/`Aya`/`Kuradal`/`dark mage`) of
   "contactable NPCs and their indexes."** The only concrete NPC name used anywhere in this
   checkout is `"dark mage"` (for pouch repair, see below) — passed as a plain string, matched at
   runtime. **If a per-NPC index/child list exists in the real client (matching the user's
   measured "rows are dynamic children of 75.4"), it is not represented in this checkout at all**
   — Microbot resolves purely by scanning widget text, so porting this fact means we'd still need
   to determine actual `75.4` child indices ourselves (e.g. via `/widgets/dump`), not from this
   source.
5. Scroll handling: if the matched NPC widget's bounds are not fully inside the picker's bounds
   (`Rs2UiHelper.isRectangleWithinRectangle`), scrolls up/down via mouse wheel at the picker's
   center point until it is (lines 346-356) — i.e. the picker list can require scrolling and
   Microbot handles this generically rather than via a fixed row height / position table.
6. Final click: `Rs2Widget.clickWidget(npcName, Optional.of(75), 0, false)` (line 358) — text
   match constrained to **group 75, child 0** (consistent with #3).
7. **No mention anywhere of "re-contacting the last NPC via the right-click menu."** Grepped for
   this and found nothing — Microbot always re-casts NPC Contact fresh rather than using any
   right-click "Last-NPC-Contact"-style shortcut (if one exists in-game at all; not confirmed
   either way from this source).

### Spell cost / rune requirements

`Rs2Spells.java:426-430`: `NPC_CONTACT` costs **2 Air, 1 Astral, 1 Cosmic** runes on the Lunar
spellbook.

### Only concrete caller: pouch repair via the Dark Mage

`repairPouchesWithLunar()` (`Rs2Magic.java:363-384`) is the one real usage of `npcContact`,
calling `npcContact("dark mage")` then driving a short fixed dialogue via Space presses and
`Rs2Widget.clickWidget("Can you repair my pouches?", Optional.of(162), 0, true)` (group **162**,
child 0 — the general chat/dialogue-options interface, matching the "scroll bar" check
`Rs2Widget.isWidgetVisible(162, 559)` used elsewhere as a dialogue-open sentinel, see section 5).
This is a worked example of the *general* pattern (npcContact → wait for dialogue → click a
worded option) that would apply to Aya/Kuradal task-related NPC Contact flows too, but the
specific option text/flow for those NPCs is not present in this checkout.

---

## 5. `Rs2Dialogue` (`util/dialogues/Rs2Dialogue.java`)

### Detecting a dialogue is open

`isInDialogue()` (line 29-31): `!Rs2Widget.isWidgetVisible(162, 559) && (hasContinue() || hasSelectAnOption())`
— i.e. dialogue-open is `hasContinue() OR hasSelectAnOption()`, but **gated off if widget `162.559`
is visible** (comment: "the scroll bar is not visible" — `162.559` is apparently a scrollbar
element on the general chatbox interface 162, used here as a "this isn't really a dialogue,
it's some other chatbox state" exclusion).

### Continue prompts ("click here to continue") — checked across SEVEN different interfaces

`hasContinue()` (lines 47-51) is `hasNPCContinue() || hasPlayerContinue() || hasDeathContinue() ||
hasSpriteContinue() || hasTutContinue() || hasBarrowsContinue() || hasSpellFilterContinue()`:

| Sub-check | Widget(s) | Notes |
|---|---|---|
| `hasNPCContinue` | `InterfaceID.DIALOG_NPC`, child 5 | symbolic id, not resolved to a raw number in this checkout |
| `hasPlayerContinue` | `InterfaceID.DIALOG_PLAYER`, child 5 | symbolic id |
| `hasDeathContinue` | `633.0` | death screen |
| `hasSpriteContinue` | `InterfaceID.DIALOG_SPRITE` child 0 OR child 3, OR `InterfaceID.DIALOG_DOUBLE_SPRITE` child 4 | single vs. double sprite (item-box) dialogues — "sprite" = the item-icon dialogue box, e.g. "You have received..." |
| `hasTutContinue` | `229.0` or `229.2` | literal raw ids |
| `hasBarrowsContinue` | `229.4` | Barrows sarcophagus "click here to continue" — same group `229` as tut-continue, different child |
| `hasSpellFilterContinue` | `162.44` | reads TEXT (must equal `"Click here to continue"` after `Rs2UiHelper.stripTagsToSpace`), not just visibility — because "the same chatbox child is also used for text inputs" (comment, lines 116-117) |

`clickContinue()` just presses **Space** (`Rs2Keyboard.keyPress(KeyEvent.VK_SPACE)`) if
`hasContinue()` — i.e. Microbot never actually clicks a continue widget, it always uses the
Space-key shortcut (consistent with the user's own routine convention:
`check_stray_dialog`/Space-per-tick in our CLAUDE.md).

### "Select an option" dialogues

`hasSelectAnOption()` (line 137-145): `InterfaceID.DIALOG_OPTION` child 1 visible AND has dynamic
children. `getQuestion()` (line 152-160) reads the FIRST dynamic child's text (stripped of `<col>`
tags) as the question/title line. `getDialogueOptions()` (line 223-241) returns dynamic children
**from index 1 onward** (index 0 is the question, never an option), skipping blank-text ones.

Option **selection is via KEY PRESS, not click**: `keyPressForDialogueOption` reads the option
widget's OWN key binding from `option.getOnKeyListener()[7]` (a widget-native listener slot) and
presses that literal character (`pressDialogueOptionWidget`, lines 745-759); only if that binding
is unreadable does the plain numeric-digit fallback (`Rs2Keyboard.keyPress(String.valueOf(index).charAt(0))`)
apply — and the code comments (lines 328-336) explicitly say the fallback used to be wrong because
"the digit fallback ... would press a key for an option that is not on screen and then report
success". `clickOption(...)` (despite its name) is actually implemented via this same key-press
path, not a literal widget click.

`handleQuestOptionDialogueSelection()` (line 723-738): a Quest-Helper-plugin-specific
convenience — auto-selects any option whose text starts with `"["` (Quest Helper's own
highlighted-option markup).

`acceptQuestStartDialogue()` (line 769-779): pattern-matches `getQuestion()` against
`"would you like to start...quest...?"` (case-insensitive, must start/contain/end as specified)
and clicks "Yes" — a reusable heuristic if we ever need to auto-accept a quest-start prompt
generically.

### Dialogue text (NPC/player speech)

`getDialogueText()` (lines 545-565) tries THREE `{group, child}` pairs in order and returns the
first non-empty stripped text: **`{229,1}`, `{229,3}`, `{231,6}`** (raw literal ids in source,
comment: "Widget Group & Child Ids associated with dialogue text"). Combined with the continue
checks above, interface group **229** appears to be heavily overloaded across multiple dialogue
sub-types (tutorial continue at child 0/2, Barrows continue at child 4, and here NPC/player
speech text at child 1/3) — i.e. **it is not a single "the NPC dialog interface is always id X"
fact; different children of 229 (and separately 231) serve different dialogue variants**, so a
future port should not assume the group id alone identifies dialogue type.

### Sprite / item-box dialogues

`hasSpriteContinue()` covers both a single sprite dialogue (`DIALOG_SPRITE` child 0 or 3) and a
"double sprite" dialogue (`DIALOG_DOUBLE_SPRITE` child 4) — the double form is presumably the
"you received X and Y" two-item box. `getDialogueSprite()` (lines 820-823) returns
`Rs2Widget.getWidget(InterfaceID.DIALOG_SPRITE, 1)` only when `hasSpriteContinue()` is true — no
level-up-specific widget/handling found anywhere in this file (grepped the whole file; nothing
named "level up" — level-up dialogues appear to be treated as an ordinary sprite/continue
dialogue rather than a special case).

### Combination dialogues (multi-item make-X style)

`hasCombinationDialogue()`: `270.1` visible. Options come from `270.14`'s static children
(gated on `270.13` being visible first) filtered to ones with a non-empty `actions` array
(lines 522-534) — i.e. the crafting/cooking "how many would you like to make" style interface is
a DIFFERENT interface family (270) from the option-select dialogue (`DIALOG_OPTION`), matched by
widget NAME (`widget.getName()`, not `.getText()`) via `getCombinationOption`.

### Cutscenes

`isInCutScene()`: `Microbot.getVarbitValue(542) == 1` (raw varbit id 542, literal in source).
`waitForCutScene()` polls for cutscene start then end.

---

## 6. Walker: generic transport execution, special cases, doors, and the three TSVs

### Generic execution (the common path every ordinary object transport takes)

`handleObject(Transport, TileObject, String action)` (`Rs2Walker.java:9364-9483` — read in full to
~9483, excerpted below through the same-plane branch):

1. **`ensureRequiredItemBeforeTransport(transport)`** (lines 9347-9362) — a pre-step: if
   `PurchasableItemCatalog.forTransport(transport)` says this transport needs an item the player
   doesn't have, buys it from the specific vendor NPC **by id** (not name — comment explains a
   name-based lookup once matched the wrong "Shantay Guard" NPC and silently failed), then confirms
   via `Rs2Inventory.hasItem`. This is the generalized "Shantay pass" pattern — see section 8.
2. Snapshots `before = Rs2Player.getWorldLocation()`.
3. **`Rs2GameObject.interact(tileObject, action)`** — `action` is `transport.getAction()`, i.e.
   literally the TSV row's `menuOption` field (e.g. `"Open"`, `"Climb-down"`, `"Enter"` — see the
   `transports.tsv` samples in section 7). **This is the core generic mechanic**: find whatever
   `TileObject` sits at the transport's origin coordinate (resolution happens one level up, not
   shown here) and click the exact menu option string the TSV row specifies.
4. `handleObjectExceptions(transport, tileObject)` (section below) is consulted immediately after
   the click — if it recognizes the object id as one of ~15 hardcoded special cases, it takes over
   and short-circuits the rest of `handleObject`.
5. Otherwise, arrival confirmation branches on same-plane vs. plane-change:
   - **Same plane**: branches again by `TransportType` —
     - `AGILITY_SHORTCUT`: `Rs2Player.waitForAnimation()` then poll for
       `isPlayerWithinChebyshevInclusive(dest, 2)`.
     - `MINECART`: either the generic "Adventure Log" travel-list widget (see below) or, if that
       widget never appears, poll the player's **pose animation id 2148** appearing then
       disappearing (`sleepUntil(getPoseAnimation()==2148)` then `!=2148`) as the ride start/end
       signal.
     - `TELEPORTATION_PORTAL`: just a fixed 2-tick jitter sleep.
     - **default** (ordinary doors/stairs/ladders/gates): `Rs2Player.waitForWalking()`, then
       unconditionally tries `Rs2Dialogue.clickOption("Yes please")` with the inline comment
       `//shillo village cart` (a hardcoded dialogue option for the Shilo Village minecart, fired
       speculatively even for objects that never show it — harmless no-op if absent), then if the
       transport is "adjacent same-plane" polls up to 2000ms for arrival/movement-stop and, if the
       player didn't land exactly on the destination, falls back to **clicking the minimap, then
       viewport** (`walkMiniMap`/`walkFastCanvas`) to close the last few tiles.
   - **Plane change**: (not fully quoted here) waits for a plane-changed confirmation with
     instrumented timing — comment notes the FIRST plane-change transport of a walk measured
     ~9.5s vs. ~2.2s mid-route, attributed (not confirmed) to retry/detection overhead.

### The "Adventure Log" / travel-list widget (spirit trees AND minecarts)

`interactWithAdventureLog(transport)` (lines 11604-11622) is a SHARED generic destination-picker
handler used by both `handleSpiritTree` (line 10938-10989, via `"Travel"` menu option — falling
back to `PohTeleports.getSpiritTree()` for the POH tree, since its object id differs from the
overworld TSV's) and `handleObject`'s `MINECART` branch:
- Waits for `ComponentID.ADVENTURE_LOG_CONTAINER` to become visible (up to 10s, allowing
  continued movement).
- Strips a leading `"<digits>: "` prefix off `transport.getDisplayInfo()` (e.g. spirit tree TSV
  rows are apparently numbered, `"3: Tree Gnome Village"` style) to get the plain destination name.
- Finds and clicks the matching child by TEXT within **`Rs2Widget.getWidget(187, 3)`** — **note
  this is interface group 187 in this checkout, NOT 381** — differs from the user's own measured
  "travel log entries are widget 381.20" (fairy ring context). This is plausibly a different
  interface entirely (spirit-tree/minecart travel list vs. fairy-ring "last-destination" list) or
  a client-revision difference; **do not assume they're the same widget** — verify independently
  via `/widgets/dump` before porting either number.
- Confirms arrival by polling `isPlayerWithinChebyshevOf(destination, OFFSET)` (`OFFSET=10`,
  line 296).

### Special-cased objects (`handleObjectExceptions`, lines 9982-10164+) — with reasons

| Object(s) | Special handling | Why it needed special-casing |
|---|---|---|
| Trapdoor pairs (`OPEN_TO_CLOSED_MAPPINGS`, a `Map<closedId, openId>`) | If the tile object seen is the CLOSED variant, click "Open" on it first, wait for the OPEN id to appear nearby, then interact again with the transport's actual action on the now-open object | Opening a trapdoor changes its object id (same pattern as doors elsewhere in this codebase) — the transport TSV row is keyed to the OPEN id, so a closed one needs an extra open-then-reacquire step |
| Al Kharid toll gate (`isAlKharidTollGateObjectId` + a fixed set of `AL_KHARID_TOLL_GATE_POINTS`) | `handleAlKharidTollGate`: waits for a "Select an option" dialogue (2.5s) and clicks `"Yes, okay"`/`"Yes"`, then polls for arrival within 1 tile | Paying the toll pops a confirmation dialogue; also given transport-selection PRIORITY 1 over other options when the player is paying with currency (`transportHandlingPreference`) |
| `KHARID_POSHWALL_TOPLESS`, `KHARID_BIGWINDOW` (Al Kharid broken wall) | Waits for TWO separate animations in sequence | "will animate once and then stop and then animate again" (comment) — a single `waitForAnimation()` would return mid-sequence |
| `REGICIDE_PITFALL_SIDE` (Isafdar Forest leaf trap) | Waits ~1200ms animation; if landed south (`y > 6400`), interacts with `REGICIDE_TRAP_HAND_HOLDS` to climb back out and waits for `y < 6400` | The trap is one-way-feeling but has a climb-out interaction on the far side |
| `WILDY_HUB_ENTRY_BARRIER` / `_M` (Ferox Enclave barrier) | If a dialogue containing `"when returning to the enclave"` is open, clicks continue, selects `"Yes, and don't ask again."` | A one-time confirmation dialogue that can be permanently dismissed — handled so it never blocks again |
| `BIGWEB_SLASHABLE` (cobwebs) | If not wearing `ARANEA_BOOTS`, waits for the web to be slashed away (polling for despawn while re-issuing "slash"), then re-walks to the destination | Cobwebs require repeated slashing and Aranea Boots skip the whole obstacle |
| Object id `20877` (Brimhaven Dungeon entrance) | Waits for the question `"Pay 875 coins to enter?"` and clicks `"Yes"`, then polls for exact-tile arrival | A paid entrance with its own dialogue text, not text-matched generically elsewhere |
| `KARAM_DUNGEON_STONE1`/`STONE2` (Brimhaven stepping stones) | Waits a fixed `600*7`ms animation | A multi-step animated crossing with no better signal available |
| `FAIRY2_ROUTE_CAVEWALLTUNNEL` (Mort Myre cave squeeze) | Waits a fixed `(600*4)+300`ms animation | Same fixed-timing pattern, different duration |
| Object id `28807` at a specific origin (2435,3519,0) (Crash Site Cavern gate) | (truncated in my read, starts at line 10089) — origin-specific special case | Not read past the trigger condition |

**Charter ships, gnome gliders, canoes, Quetzals, and fairy rings are NOT handled by
`handleObjectExceptions`** — they're dispatched to entirely separate top-level handler methods
(`handleCharterShip`, `handleGlider`, `handleCanoe`, `handleQuetzal`/`handleQuetzalWhistle`,
`handleFairyRing`) before `handleObjectExceptions` is ever reached, each with its own widget-driven
destination picker (charter: recursive widget-tree text search for the destination name across
static/dynamic/nested children, `Rs2Walker.java:11458-11530`; canoe: a shaping dialogue
(`"chop-down"→"shape-canoe"→"float canoe"→"paddle canoe"` state machine, then a destination map
widget `InterfaceID.CanoeMapLum.DESTINATIONS`, noting a prior OSRS update moved this from group
647 — comment at 11158-11161; glider: an NPC-driven menu; Quetzal: a `QuetzalMenu`/
`QuetzalwhistleMenu` map interface, matched either by coordinate-derived label
(`quetzalMapLabelForDestination`) or displayInfo, since "Bank / script targets often sit several
tiles off quetzals.tsv landing coords" (comment line 11224)).

### Absent: Mount Karuulm / Ancient Cavern whirlpool / wilderness ditch / Mos Le'Harmless special-casing

**None of these are special-cased in `Rs2Walker.java`** (grepped the whole file for
`Karuulm`/`whirlpool`/`Ancient Cavern`/`ditch`/`Mos Le` — zero hits). They are handled entirely
generically:
- The **Mount Karuulm Dungeon elevator** and internal climbs are plain `transports.tsv` rows
  (`Climb;Rocks`, `Climb;Steps`, `Activate;Elevator`, `Exit;Cave exit` — see section 7) walked
  through `handleObject`'s default same-plane/plane-change branches with no special code.
- The **Ancient Cavern whirlpool** is a plain `Jump-into;Whirlpool;25274` row, gated by a varbit
  (`3759>1`) in the `Varbits` column — resolved generically wherever transport requirement
  filtering happens (not a special method).
- **No wilderness-ditch entry row was found searchable by the word "ditch"** in `transports.tsv`
  (not grepped exhaustively for the ditch's actual object id, only the word) — consistent with our
  own CLAUDE.md's rule that ditch-crossing/Wilderness-entering edges are deliberately excluded from
  routing; I found no evidence either way that Microbot excludes them on purpose vs. simply never
  having added the row.
- **Mos Le'Harmless's cave-horror cave** (`Enter;Cave entrance;3650` at (3749,2973,0)) is likewise a
  plain generic-object row with no dialogue special-case found — unlike Brimhaven's paid entrance,
  nothing in `handleObjectExceptions` matches object id 3650, so if that entrance pops a warning
  dialogue in-game, Microbot does not appear to handle it explicitly (or it's handled by the
  generic `check_stray_dialog`-equivalent elsewhere, not found in this file).

### Doors

Door detection/action-selection is **pure string/action heuristics**, extracted into a stateless
class (`util/walker/door/Rs2DoorClassifier.java`) specifically so it has no walker state:

- **Door-like name fragments** (`DOOR_LIKE_NAME_FRAGMENTS`, line 19-21): `door`, `gate`,
  `barrier`, `stile`, `portcullis`, `archway`, `cattlegate`, `fence` — **broader than our own
  door/gate/curtain list**; `fence` is matched as a whole word only (regex `\bfence\b`) to avoid
  matching inside `"defence"`.
- **Action priority when picking which menu option opens/passes a door** (`DOOR_ACTION_PRIORITY`,
  lines 27-30, in priority order): `pay-toll`, `pick-lock`, `walk-through`, `go-through`, `open`,
  `pass`, `enter`, `push`, `climb-over`, `climb-through`, `squeeze-through`, `cross`, `force`,
  `exit`. `pickWalkDoorAction(ObjectComposition)` picks the highest-priority action present,
  **excluding** any close/shut action.
- **Close/shut exclusion**: `isDoorCloseOrShutAction` — action string starts with `"close"` or
  `"shut"` (case-insensitive) — used everywhere to guarantee the walker never clicks the action
  that would SHUT an already-open door, matching our own CLAUDE.md's documented Vitalite-derived
  lesson about left-clicking an open door.
  `doorCompositionSpecifiesOnlyCloseOrShut` detects "this door is currently OPEN" purely from every
  non-null action being close/shut — i.e. **open-vs-shut is inferred from the action LIST shape,
  not from a separate id/flag table**, consistent with our own CLAUDE.md's "actions identify a
  door, never flags" rule.
- **Throttling** (`util/walker/door/Rs2DoorHandler.java`, pure bookkeeping, no game interaction):
  per-EDGE attempt cooldown (`shouldThrottleDoorAttempt`/`markDoorAttempt`, keyed by a normalized
  `DoorEdge` of from/to tiles) to avoid hammering the same door repeatedly, PLUS a separate
  "recently opened a STATIONARY door within 2 tiles of this segment" suppression
  (`recentlyOpenedStationaryDoorOnSegment`) and a GLOBAL cross-door interaction cooldown
  (`shouldThrottleGlobalDoorInteraction`) — three independent throttle layers stacked, none of
  which touch game state themselves (pure `Map`/timestamp bookkeeping, meant to be called from the
  stateful walker).
- A separate `door/` sub-package (`Rs2DoorAheadResolver`, `Rs2DoorProbe`, `DoorProbeContext`,
  `Rs2DoorGeometry`, `model/DoorEdge`, `model/DoorCandidate`, `model/DoorResolution`,
  `model/DoorFailureReason`, `model/AwaitTicket`, `Rs2WalkerAwaits`) exists for detecting a door
  standing on the path AHEAD of the current step and resolving which real door object corresponds
  to a route edge — **not read in detail** (out of budget for this survey); flagged here as
  present and worth a follow-up read if door-ahead detection specifically becomes a blocker,
  since it appears substantially more developed than Vitalite's single-method model our CLAUDE.md
  currently cites.

### The three TSVs

**`blocked_edges.tsv`** — 131 data rows. Header:
`# Origin	Destination	Bidirectional	Display info`. Purpose stated in a header comment: *"Static
collision edge overrides for map data gaps. These block movement across the edge, not the whole
tile."* Sample rows:
```
3267 3227 0	3268 3227 0	true	Al Kharid toll gate
3267 3228 0	3268 3228 0	true	Al Kharid toll gate
3229 3472 0	3229 3471 0	true	Varrock Palace garden south fence
```

**`dangerous_tiles.tsv`** — 14 data rows. Header: `# Tile	Display info` (single coordinate, not an
edge). Purpose per header comment: *"Tiles occupied by aggressive NPCs that attack you while you
path next to them. When the 'Avoid dangerous NPCs' pathfinder option is on, a high step penalty is
applied to every tile within 1 of these (the melee-aggro ring), so paths keep >=2 tiles away when a
reasonable detour exists. It is a penalty, not a block: at a true chokepoint the path still goes
through."* Sample rows:
```
3103 3347 0	Draynor Manor undead tree
3107 3342 0	Draynor Manor undead tree
3107 3344 0	Draynor Manor undead tree
```
This is exactly the `dangerCfg.isDangerousAdjacentTile`/`avoidDangerousNpcs` mechanism referenced
in `Rs2Walker.java` around lines 2543-2569 (route recovery specifically avoids parking the player
on a tile adjacent to one of these).

**`restrictions.tsv`** — 235 data rows. Header:
`# Origin	Item IDs	Quests	Skills	isMembers	Varbits	Varplayers` — i.e. this table says a TILE
(not an edge or transport) additionally requires an item/quest/skill/varbit to be WALKED ONTO at
all (independent of any transport). Sample rows:
```
3265 3442 0
3264 3442 0
# Gate between digsite and varrock
3296 3429 0					3637>152
```
(the first two are under a `# Varrock museum` comment header with all requirement columns blank —
plausibly placeholder/reserved rows or a members-only area gated some other way not shown in this
slice) and, with an actual requirement populated:
```
2584 3087 0			66 Magic
2936 9810 0			63 Agility
3207 3217 0		Recipe for Disaster
```
(Magic Guild tile requiring level 66 Magic; a tile near Al Kharid requiring 63 Agility, with the
inline comment `# Al Kharid toll gate is modeled as transports plus blocked edge overrides` — i.e.
that PARTICULAR gate does NOT use this file, this is a different nearby restriction; and an RFD
Room tile gated on quest progress).

---

## 7. The resources (headers, row counts, and slayer-relevant rows)

All under
`...\microbot-core\runelite-client\src\main\resources\net\runelite\client\plugins\microbot\shortestpath\`.

| File | Header line | Data rows (non-comment, non-blank) |
|---|---|---|
| `transports.tsv` | `# Origin	Destination	menuOption menuTarget objectID	Skills	Item IDs	Currency	Quests	Varbits	Varplayers	isMembers	Duration	Display info` | 6077 (includes many comment/section-header lines; this is the raw line count of the whole file, not a clean data-row count) |
| `fairy_rings.tsv` | `# Origin	Destination	Skills	Item IDs	Quests	Varbits	Duration	Display info` | 112 total lines (~40 origin-only permutation rows + ~55 destination+code rows + a few quest-gated/commented rows; see section 3's full breakdown) |
| `teleportation_items.tsv` | `# Destination	Skills	Item IDs	Quests	Varbits	Varplayers	isMembers	Consumable	Wilderness level	Duration	Display info` | 384 |
| `teleportation_spells.tsv` | `# Destination	Skills	Quests	Wilderness level	Varbits	Varplayers	isMembers	Duration	Display info` | 147 |
| `quetzals.tsv` | `# Origin	Destination	Skills	Quests	Varbits	Duration	Display info` | 29 |
| `npcs.tsv` | `# Origin	Destination	menuOption menuTarget objectID	Skills	Item IDs	Quests	Varbits	isMembers	Duration	Display info` | 37 |

### Mount Karuulm / Karuulm Slayer Dungeon — **PRESENT**, contradicting the assumption that no vendored table has it

`transports.tsv` has a dedicated **`# Mount Karuulm Dungeon`** section (lines 5101-5129, 29 rows):
```
1311 3806 0	1311 10189 0	Activate;Elevator;34359
1311 3808 0	1311 10189 0	Activate;Elevator;34359
1311 3806 0	1312 10189 0	Activate;Elevator;34359
1311 3808 0	1312 10189 0	Activate;Elevator;34359
1311 10189 0	1311 3806 0	Exit;Cave exit;34514
1311 10189 0	1311 3808 0	Exit;Cave exit;34514
1312 10189 0	1311 3806 0	Exit;Cave exit;34514
1312 10189 0	1311 3808 0	Exit;Cave exit;34514
1303 10205 0	1301 10205 0	Climb;Rocks;34544
1303 10206 0	1301 10206 0	Climb;Rocks;34544
1301 10205 0	1303 10205 0	Climb;Rocks;34544
1301 10206 0	1303 10206 0	Climb;Rocks;34544
1311 10214 0	1311 10215 0	Climb;Rocks;34544
1312 10214 0	1312 10215 0	Climb;Rocks;34544
1311 10215 0	1311 10214 0	Climb;Rocks;34544
1312 10215 0	1312 10214 0	Climb;Rocks;34544
1320 10205 0	1322 10205 0	Climb;Rocks;34544
1320 10206 0	1322 10206 0	Climb;Rocks;34544
1322 10205 0	1320 10205 0	Climb;Rocks;34544
1322 10206 0	1320 10205 0	Climb;Rocks;34544
1329 10205 0	1334 10205 1	Climb;Steps;34530
1329 10206 0	1334 10206 1	Climb;Steps;34530
1334 10205 1	1329 10205 0	Climb;Steps;34531
1334 10206 1	1329 10206 0	Climb;Steps;34531
1313 10188 1	1318 10188 2	Climb;Steps;34530
1313 10189 1	1318 10189 2	Climb;Steps;34530
1318 10188 2	1313 10188 1	Climb;Steps;34531
1318 10189 2	1313 10189 1	Climb;Steps;34531
```
This gives: the surface elevator origin **(1311, 3806/3808, 0)** landing underground at
**(1311/1312, 10189, 0)** and back (matches the user's expected surface x1260-1320/y3800-3830 and
underground y10150-10250 ranges exactly), plus internal rock-climb shortcuts and a two-flight
staircase moving between underground planes 0→1→2 around x1311-1334, y10188-10215.

Additionally, `teleportation_items.tsv` line 34-35 has a direct teleport landing right at the
dungeon entrance:
```
1311 3799 0		22945		7938<3		Y	T	19	4	Rada's blessing: Mount Karuulm
1311 3799 0		22947				Y	F	19	4	Rada's blessing: Mount Karuulm
```
(two charge-tier variants of the same item, one gated `7938<3` meaning some charge/quest varp
below 3, i.e. "OR the other item id if that's not the case" — same OR-pattern our own CLAUDE.md
describes for item rows.)

`seasonal_transports.tsv` also has Karuulm rows (lines 27, 116-117: "Clue compass: Mount Karuulm",
"Map of Alacrity: Kourend - Karuulm Slayer Dungeon Hydra Pipe", "...Karuulm Climb") but these are
gated `10032>0`, a Leagues/seasonal-mode varbit — **not usable in a normal game**, listed here only
so they aren't mistaken for regular-game rows if this file is ever read wholesale.

### Great Conch / gryphon cave — **PRESENT**, confirmed via fairy ring code CJQ

`UPSTREAM_COMPARISON.md:149` (a doc file in this same package, comparing Microbot's tables against
upstream Shortest Path) states outright: **`| Great Conch fairy ring (CJQ) | Yes | ✅ Yes — in
`fairy_rings.tsv` |`**. Cross-checked directly in `fairy_rings.tsv` line 108:
```
	3178 2447 0			Troubled Tortugans		5	CJQ
```
i.e. fairy ring code **CJQ** lands at **(3178, 2447, 0)** — inside the user's expected surface
range (x~3150-3250, y~2400-2500) — gated on the quest **Troubled Tortugans**. `WEBWALKER_IMPROVEMENT_PLAN.md:77`
independently confirms the same fact from a different angle (a punch-list entry: *"Slayer Ring
Ankous (3193,3611), Fairy Ring CJQ (Great Conch) — CJQ present, Ankous Slayer Ring variant still
missing"*). **No separate cave-entrance/underground row was found for the Great Conch/gryphon
cave** (grepped `transports.tsv` for `Conch`/`gryphon`/`Gryphon` — zero hits) — only the fairy
ring landing is present; if the gryphon cave itself needs a further surface→underground transport
(the user's expected y~8800-8900 underground range), that leg is NOT in this table.

### Taverley dungeon entrance + dusty-key gate — present

`transports.tsv:1173-1213` (`# Taverley Dungeon` section). Entrance ladder:
```
2884 3398 0	2884 9798 0	Climb-down;Ladder;16680
2884 9798 0	2884 3398 0	Climb-up;Ladder;17385
```
(plus three more parallel ladder tiles at x=2883/2885, matching the user's "~2884,3397" estimate).
Dusty-key gate:
```
2924 9803 0	2923 9803 0	Open;Gate;2623		1590
2923 9803 0	2924 9803 0	Open;Gate;2623
```
Column 5 (`Item IDs`) holds **`1590`** on the forward row only — `1590` is the Dusty key's item
id — confirming the gate is one-way-gated on holding the key (the reverse direction, walking back
out, needs nothing). Matches the user's "~2924,9803" coordinate exactly.

### Ancient Cavern whirlpool — present, varbit-gated

`transports.tsv:5813-5814`:
```
2511 3511 0	1768 5366 0	Jump-into;Whirlpool;25274					3759>1
2512 3511 0	1768 5366 0	Jump-into;Whirlpool;25274					3759>1
```
Exact coordinate match to the user's own measured (2511,3511)→(1768,5366). Gated on **varbit 3759
> 1** — our own CLAUDE.md already flags this whirlpool as "unusable" for us because nothing
supplies quest/varbit state; this confirms the SPECIFIC varbit (3759) that would need to be
sourced if we ever wire that up. No reverse row was found in this immediate area (not exhaustively
searched beyond these two lines) — plausibly one-way by design (the whirlpool is a one-way drop
into the Ancient Cavern in live OSRS).

### Mos Le'Harmless (cave horrors) — present

`transports.tsv:3572-3574` (`# Mos Le'Harmless` section, full section spans 3551-3580+):
```
3749 2973 0	3748 9373 0	Enter;Cave entrance;3650
3748 9373 0	3749 2973 0	Exit;Cave;5553
3748 9374 0	3749 2973 0	Exit;Cave;6702
```
Matches the user's ~(3748,2973) estimate (off by one tile on the origin, well within "around").
Two different EXIT object ids (5553 vs 6702) from two adjacent underground tiles back to the same
surface point — plausibly two different physical exit objects/ladders converging on one surface
landing.

### Lletya, Lunar Isle — not specifically searched beyond what surfaced incidentally

I did not do a targeted search for Lletya or Lunar Isle rows (out of scope budget) beyond noting
`NexusPortal.LUNAR_ISLE`/`PohPortal.LUNAR_ISLE` already listed in section 1 (POH teleports reach
Lunar Isle directly). No claim either way about a `transports.tsv`/`fairy_rings.tsv` row for
Lletya.

---

## 8. Other things a slayer loop would want (unexpected findings)

- **A slayer-master coordinate table already exists**: `util/walker/enums/SlayerMasters.java` —
  `TURAEL (2931,3536,0)`, `SPRIA (3091,3267,0)`, `KRYSTILIA (3109,3516,0)`,
  `MAZCHNA (3510,3507,0)`, `VANNAKA (3145,9914,0)`, `CHAELDAR (2444,4431,0)`,
  `KONAR (1308,3786,0)`, `NIEVE`/`STEVE` (both `2432,3423,0` — the same masters share a
  location/alternate), `DURADEL (2869,2982,1)`. **Notably absent: Kuradal and Aya** — this table
  does not cover either NPC our own `slayer.py` already targets, so it's only partially useful and
  should not be assumed complete.
- **A purchasable-item-at-the-gate catalog** (`shortestpath/PurchasableItemCatalog.java`, backed
  by `purchasable_items.tsv`) generalizes "the transport wants an item and a vendor standing right
  there sells it" (named after the Shantay Pass pattern) — vendor NPC id, item id, cost, and
  vendor action, consulted automatically before every generic object transport
  (`ensureRequiredItemBeforeTransport`, section 6). This is a reusable idea for any slayer site
  that gates behind a purchasable pass/ticket.
- **A "reachable tiles from a tile" live-collision primitive**, `Rs2Tile.getReachableTilesFromTile`
  / `Rs2Tile.isTileReachable` — used throughout `Rs2Walker.java` for route recovery, sidestep
  ranking, and goal-adjacency checks. Functionally the same role as our own
  `travel.can_reach`/`reachable_set`/`interaction_tiles` built on live collision flags.
  `Rs2GameObject.canWalkTo(object, radius)` is the object-specific wrapper used before every
  fairy-ring interaction.
  **Not read in source** (out of budget) but referenced consistently enough across the walker to
  be confident it exists and is central — would be worth a dedicated follow-up read if we ever
  build a general "can I reach this" primitive of our own beyond `travel.can_reach`.
- **A banking-aware pre-trip planner**: `util/walker/banking/Rs2WalkerBankingPlanner.java` runs a
  full `Pathfinder` pass with `useBankItems` toggled on the `PathfinderConfig`, i.e. it can plan a
  route assuming teleport items will be WITHDRAWN from the bank first, not just carried — a step
  beyond our own `bank_memory`/`teleports.py` split (which currently only reads what's already in
  the bank, doesn't factor "plan around what a withdrawal would enable" into route choice).
- **Dangerous-NPC tile avoidance is a first-class pathfinder feature**, not just a table: the
  `dangerous_tiles.tsv` penalty (section 6) is applied through a `dangerCfg.isAvoidDangerousNpcs()`
  toggle and `isDangerousAdjacentTile` check baked into route RECOVERY specifically (so a stuck
  reroute won't park the player next to an aggressive NPC) — a pattern worth copying if our own
  worldmap-based router ever needs "avoid walking next to this aggressive NPC" as a soft
  preference rather than a hard block.
- **Cave-entrance / gate confirmation dialogues are individually hardcoded, not handled by a
  generic "any Yes/No confirm" rule**: Al Kharid toll gate (`"Yes, okay"`/`"Yes"`), Brimhaven
  Dungeon entrance (`"Pay 875 coins to enter?"` → `"Yes"`), Ferox Enclave barrier
  (`"Yes, and don't ask again."`), Shilo Village minecart (`"Yes please"`, fired speculatively on
  every ordinary object transport as a harmless no-op when absent). **No generic "any dialogue
  after an object click gets auto-confirmed" fallback was found** — every one of these is a
  specific object id / text match. Mos Le'Harmless's cave entrance (object id 3650) has NO such
  case, so if it pops a warning dialogue in live OSRS, Microbot doesn't appear to handle it.
- **No pool of refreshment, no "building mode" toggle, no bank-in-POH handling** anywhere in
  `util/poh/` (confirmed absent, section 1) — if the slayer loop ever wants to use POH banking or
  pool-of-refreshment stat restoration, none of that is portable from here.
- **`Rs2Widget.findWidget` (text search) and `Rs2GameObject.interact(id/object, action)` are the
  two workhorse primitives** almost every one of the above handlers is built from — text-match a
  widget or an object composition's actions, then click. This is a strong signal that our own
  bridge's `/widget`, `/widgets/dump`, `/object/at`, and `/hover` endpoints are sufficient building
  blocks to port ANY of the above mechanics, since Microbot itself doesn't need anything richer
  than widget/object text+action introspection to drive them.

---

## What to port, in priority order

1. **`transports.tsv` lines 5101-5129 (`# Mount Karuulm Dungeon`) and lines 3572-3580
   (`# Mos Le'Harmless`)** — the two missing dungeon-entrance/internal-transport blocks our own
   vendored copy lacks entirely; these are ready-to-copy rows (coordinates + menu option +
   object id), not logic.
2. **`fairy_rings.tsv` line 108 (Great Conch, code `CJQ`, quest-gated on Troubled Tortugans)** —
   confirms the gryphon-cave-adjacent destination is reachable by fairy ring; needs pairing with
   our own fairy-ring dial execution once/if we build one.
3. **`util/walker/door/Rs2DoorClassifier.java`** — pure, dependency-free string heuristics
   (door-name fragments, action-priority list, close/shut exclusion) directly portable to Python
   with no RuneLite types involved; broader than our current door name list (adds barrier, stile,
   portcullis, archway, cattlegate, fence).
4. **`Rs2Walker.java`'s fairy-ring dial section (lines 11688-11849)** — the widget id table
   (398.3/.4/.5 slots, 398.19-.24 rotation buttons), the rotation-value-per-letter table
   (A/I/P=0, B/J/Q=512, C/K/R=1024, D/L/S=1536), and the "last-destination" menu-option shortcut —
   everything needed to execute an overworld or POH fairy-ring teleport once we decide to support
   fairy-ring travel legs ourselves.
5. **`transports.tsv:1173-1213` (Taverley Dungeon, incl. the dusty-key gate at 2924,9803 needing
   item 1590) and `:5813-5814` (Ancient Cavern whirlpool, varbit 3759>1)** — ready-to-copy rows
   for two more of the user's named slayer-adjacent sites.
6. **`util/poh/data/HouseLocation.java`'s varbit table** (`POH_HOUSE_LOCATION` values 1-13, city
   names, entrance portal object ids) — small, self-contained, and exactly the kind of fact our
   `teleports.py` house-tablet logic needs if it ever has to route to the RIGHT overworld portal
   for the player's specific house town.
7. **`util/poh/data/PohPortal.java` lines 111-137** (the "Backfilled from upstream" block with
   literal `WorldPoint` destinations for Barbarian Outpost, Fishing Guild, Port Khazard, Marim,
   Civitas illa Fortis, Ourania, Cemetery, Dareeyak, Trollheim, Lassar, Ice Plateau, Paddewwa) —
   ready-to-copy coordinates not sourced from any external class.
8. **`util/dialogues/Rs2Dialogue.java`'s continue/option/text detection logic** (the 7-way
   `hasContinue()` union, key-press-via-widget's-own-binding pattern, the `getDialogueText()`
   3-widget-id fallback chain) — a more complete "is a dialogue open / what does it say / how do I
   answer" model than what we likely have, portable as pure widget-id + text-matching logic against
   our own `/widget`/`/widgets/dump` endpoints.
9. **`Rs2Walker.java`'s `handleObjectExceptions` hardcoded list (lines 9982-10164+)** — a short,
   concrete list of named trouble-object ids and the exact dialogue text/timing each needs (Al
   Kharid toll gate, broken wall double-animation, Isafdar leaf trap, Ferox barrier, cobwebs,
   Brimhaven entrance, Brimhaven stepping stones, Mort Myre squeeze) — useful as a checklist of
   "known gotchas" even where we won't port the Java verbatim.
10. **`shortestpath/PurchasableItemCatalog.java` + `purchasable_items.tsv`** — the generalized
    "buy the gate's ticket from the vendor standing right there" pattern, worth keeping in mind if
    any slayer site's route ever crosses a paid/ticketed gate.
