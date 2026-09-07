# Shipwreck salvaging: mass-world mode, cargo-hold sorting and a keep-list

Design date: 2026-09-07. Written during a live session against the running client, on a world
stacked with roughly 13 other salvaging boats.

Extends `routines/shipwreck_salvaging.py`, whose existing behaviour and measured history live in
`docs/superpowers/specs/2026-08-01-shipwreck-salvaging-design.md` and in CLAUDE.md's "Current
state of routines/shipwreck_salvaging.py". **Nothing in the current routine is thrown away** --
the hook loop, the sunk-chat redeploy, the hazard blacklist, the XP watchdog and the
engage/reposition budgets are all kept exactly as measured.

## What the user asked for

1. **Mass-world mode.** On a busy world the boat does not need to move: when a wreck sinks
   another rises in the same area almost immediately. Wait for the respawn instead of sailing.
2. **A salvaging station is now built on the boat.** Salvage goes into the cargo hold (the player
   and the crewmate both fill it), and when it is full we sort everything at the station.
3. **A keep-list** for the items sorting produces; everything else is dropped.
4. **Sort while waiting.** If both salvage sites are down, spend that dead time sorting rather
   than idling.

Decisions the user made during design:

- Sort-salvage works on **inventory** salvage, so the sort loop withdraws from the hold in batches.
- Non-whitelisted items are **dropped**. Alching is explicitly out of scope for v1.
- Mass-world mode means **never sail at all**, not "prefer not to".
- The keep-list lives in the **routine's `DEFAULT_CONFIG`**, not the Controller Settings panel.
- A wreck rising mid-sort **abandons the sort immediately** and returns to the hook.
- **Crew swapping is wanted**, but as sub-project 2, after the rest is proven live.

## Measured facts

Everything in this section was read off the live client on 2026-09-07 unless a wiki revision is
cited. None of it is reasoned or assumed.

### The salvaging station

| | |
|---|---|
| Boat facility locs | `59699` (2x5a), `59700` (2x5b), `59701` (3x8) |
| Port locs | `60462`, `60463` |
| Actions | `op1 = Sort-salvage`, `op5 = Modify`; `forceapproach=south` |
| Requirements | Sailing 42, Construction 34 (cache dbrow `sailing_boat_facility_salvaging_station_*`) |

**Sort-salvage opens NO interface.** A widget watcher polling `/widgets/dump` across a full sort
recorded no new interface group at all. It is a plain channel, exactly like the salvaging hook.

**Animation `13599`** plays for the whole channel -- confirmed live on a **3x8** boat, even though
runelite-api names it `SAILING_HUMAN_SALVAGE_HOOK_KANDARIN_1X3_INTERACT01`. `13593`
(`LARGE01_INTERACT01`) is the other known member of the family. RuneLite's own `IdleNotifier`
labels both "Sort-salvage". **The routine's existing `SALVAGE_CHANNEL_ANIMATIONS` deliberately
EXCLUDES these two** (see its own comment); that exclusion is correct and must stay, because the
hook's idle-reclick logic keys off it. Sorting needs its own separate set.

**MEASURED rate: 1.79s per salvage** -- 12 salvage consumed in 19.7s, with deltas at
0.8 / 2.8 / 4.5 / 6.2 / 7.9 / 9.7 / 11.4 / 13.5 / 15.2 / 17.0 / 18.7 / 20.5s, i.e. a steady 3
game ticks each. This matches the wiki's "close to 1800 salvages per hour" (2.0s each) figure for
the port station.

**The channel ends by itself** when the inventory runs out of salvage: the animation went to `-1`
on the same poll that the last salvage left the inventory. Nothing has to stop it.

### The cargo hold

Interface **943** (`InterfaceID.SAILING_BOAT_CARGOHOLD`), with **944**
(`SAILING_BOAT_CARGOHOLD_SIDE`) as the player-inventory side panel. Captured live:

| Widget | Content |
|---|---|
| `943.1` | title, e.g. `Cargo Hold: Queen Death`; also carries the `Close` action |
| **`943.4`** | **slots used** (read `12`) |
| **`943.5`** | **capacity** (read `160`) |
| `943.3` | `Item stacking` toggle / `Un-stack items` |
| **`943.10`** | the hold's item grid -- ops `Withdraw-All / -1 / -5 / -10 / -X / Examine` |
| `943.13` | `Deposit` **Cargo** |
| **`943.14`** | `Deposit` **Salvage** |
| `943.15` | `Deposit` **Inventory** |
| `943.16` / `943.18` | `Tools:` row -- Captain's log, Spyglass, Current duck, Crowbar, Diving gear; op `Take` |
| `943.20`..`943.24` | quantity buttons `1`, `5`, `10`, `X`, `All` |
| `944.1` | the player's inventory, per item `Deposit-All / -1 / -5 / -10 / -X` |

Two consequences shape the whole design:

- **`943.14` deposits SALVAGE ONLY.** This is why the design does not use the hold object's own
  `Deposit-all` menu option or `943.15`: both risk depositing the chisel, rune pouch and coins the
  setup carries. A dedicated salvage-only button removes that failure mode entirely.
- **Hold fullness is `943.4` / `943.5`, read while the interface is open.** The hold is opened on
  every stow anyway, so fullness costs nothing extra.

**Salvage does not stack in the inventory.** `944` listed 14 separate `Plundered salvage` entries.
So a `Withdraw-All` from the hold fills at most the free inventory slots -- conveniently the exact
batch size the sort loop wants, with no quantity arithmetic.

### `/trawling` is a dead end for this feature

`TrawlingProvider` reads item containers `963..967` (`SAILING_BOAT_1..5_CARGOHOLD`) -- verified as
the correct ids against `InventoryID` -- but **`cargo` stayed `null` for the entire session**,
including while the hold interface was open showing 12 items. `carryingCargo` (varbit
`SAILING_CARRYING_CARGO`) stayed `0` throughout; the cache shows it is the cargo-hold loc's own
`multivar`, and it evidently tracks courier cargo, not salvage.

**So the hold is read from interface 943, not from `/trawling`.** This corrects an assumption in
`docs/superpowers/specs/2026-09-06-deep-sea-trawling-design.md`; that routine has never run live
either, so it inherits the same open question.

### The 229 message box

Group **229** appeared during the session reading:

- `229.3` "Your inventory is too full to hold any more plundered salvage."
- `229.4` "Click here to continue"

This is the same plain message box `karambwan_fishing.py` documents as **invisible to
`/state.dialogOpen`**, and therefore invisible to the engine's own `check_stray_dialog`. It also
eats tab hotkeys. The routine's existing `drop_salvage` already carries a Space workaround for the
sibling case; the new stow and sort paths need the same treatment, keyed on reading `229.4` by id
and text rather than on `dialogOpen`.

Note the text is **tier-specific** ("...any more plundered salvage"), so match on the box's
presence, never on that wording.

### Own boat vs. the neighbours' -- an existing bug

`/objects` reported **26 `Salvaging station` objects** within a few tiles, from roughly 13
overlapping boats. Three sat on the player's exact tile at `distance: 0`, all `clickable: true`,
with screen positions only about 20px apart.

`ObjectProvider` recurses through every `WorldEntity`'s `WorldView` (by design -- a boat's own
fixtures live in nested views), so every neighbouring boat's facilities appear in the scan with
real world coordinates and real screen positions. **Position cannot separate them.**

`sailing.deploy_hook` currently picks the first object whose name matches, within radius 20. On a
mass world -- the exact scenario this feature targets -- that is a coin flip between our hook and
a stranger's. **This is a live defect in the shipped routine**, not merely a hazard for the new
clicks.

`/player` already reports `debugWorldViewId` (read `1036`, `sizeX 8`, `sizeY 16` -- the 3x8 boat's
own view). Objects carry no equivalent.

### Crew assignment is widgets, not NPCs

`GET /npcs` returned **0 NPCs** on a world with roughly 13 crewed boats in view: `NPCProvider`
reads only the top-level world view, and crewmates live in the boats' nested views. **The routine
cannot see a crewmate at all today.**

It does not need to. `InterfaceID.SailingSidepanel` carries the whole path, and the live capture
confirms it:

- **937** (`SAILING_SIDEPANEL`): `937.33` `Facilities`, `937.34` `Stats`, `937.35` `Crewmates`
  tabs; `937.6` tab title; **`937.25` `Assign`** (four instances, one per assignable facility);
  `937.27` status text (`Not steering`, `Repairs:<br>No kits`).
- **219** -- the ordinary dialogue-option group -- captured showing
  `Crewmate Assignation: Ex-Captain Siad (H3 P2 D3)` with options `Helm and sails (H3).`,
  `Repairs (D1).`, `Other facility.`, `Current - Idle.`

So crew assignment is: sidepanel tab, then `Assign`, then a numbered dialogue -- the same
machinery `giants_foundry.py` already drives for group 219, with the same "the menu TITLE is
`options[0]`" correction that file documents.

### Respawn mechanics (wiki, Shipwreck salvaging rev 15290733)

> All shipwrecks in an area share a common spawn pool, and only a limited number (typically 4 of
> the 6 shipwreck sites) can be active at once. [...] When one shipwreck sinks, another in the
> area immediately rises.

This justifies mass-world mode: waiting is not laziness, it is the documented optimum on a busy
world. The wiki says as much -- "players may simply wish to salvage on one of the officially
designated worlds where high player counts ensure rapid cycling."

### What sorting produces (wiki, Money making guide/Salvaging pirate shipwrecks rev 15255112)

Per-hour output at roughly 279 sorts, with unit values derived from the guide's own totals:

| Item | per hour | ~gp each |
|---|---|---|
| Facility bottle (empty) | 0.4 | 34,615 |
| Rune scimitar | 0.35 | 15,249 |
| Diamond bracelet | 8.69 | 2,294 |
| Diamond ring | 8.69 | 2,114 |
| Mahogany repair kit | 8.69 | 1,500 |
| Ruby bracelet | 26.06 | 1,395 |
| Dragon cannonball | 0.3 | 1,243 |
| Emerald bracelet | 34.75 | 915 |
| Oyster pearls | 34.75 | 840 |
| Emerald ring | 17.37 | 765 |
| Rune cannonball | 10.42 | 678 |
| Mithril scimitar | 34.75 | 624 |
| Sapphire ring | 34.75 | 540 |
| Casket | 17.37 | 489 |
| Adamant cannonball | 30.41 | 215 |
| Gold ring | 69.5 | 210 |
| Mithril cannonball | 60.81 | 148 |

Live confirmation, partial: a 12-salvage sort produced `Rune scimitar` and `Rune cannonball`.

These values are the guide's alch/GE mix, since it alchs most of the list. **v1 does not alch** --
the keep-list decides keep-or-drop and nothing else.

## Design

### Sub-project 1: mass-world mode, hold stowing, sorting, keep-list

#### Configuration

Added to `DEFAULT_CONFIG` in `routines/shipwreck_salvaging.py`. No Controller Settings panel entry
and no `ConfigExporter` change: this routine's `get_config` already ignores `bridge_config`
entirely, which per CLAUDE.md's routine contract means nothing Java-side is required for config.

```python
"massWorld": True,        # never sail; wait for respawns and sort meanwhile
"stowSalvage": True,      # deposit salvage into the cargo hold instead of dropping it
"sortWhenIdle": True,     # sort while no wreck is in range
"holdFullSlots": 4,       # treat the hold as full this many slots short of capacity
"keepItems": [            # case-insensitive SUBSTRING keep-list for sorted output
    "facility bottle",    # ~34.6k each, the single most valuable drop
    "casket",             # opens into gems; a gem bag makes these cheap to carry
    "mahogany repair kit",
    "oyster pearls",
    "diamond",            # Diamond ring + Diamond bracelet, ~2.1-2.3k each
    "ruby",               # Ruby bracelet ~1.4k
    "dragon cannonball",
    "rune scimitar",
    "rune cannonball",
],
```

Everything not listed -- gold/sapphire/emerald rings, emerald bracelets, mithril scimitars,
mithril and adamant cannonballs -- is dropped. Those are the alch-fodder rows of the money-making
guide's table, and v1 does not alch. If the user later wants them banked instead, the change is
one line in this list, which is precisely why it is a config list rather than code.

`keepItems` follows `banker.py`'s `keepItems` convention (case-insensitive substring), with that
convention's known trap recorded in the config comment: `"ring"` matches `Gold ring` **and**
`Sapphire ring` **and** `Ruby ring`, and `"rune"` would match `Rune scimitar`, `Rune cannonball`
and anything else runite.

**A separate `HARD_KEEP` constant in code protects the kit** -- chisel, rune pouch, coins, gem
bag, plank sack, log basket, and salvage itself. It is not configurable and the keep-list cannot
override it. Rationale: `keepItems` is a list a human edits between sessions, and a typo there
must not be able to drop the tools. Same reasoning `banker.py` applies to bank UI buttons and
`sailing._FIXTURE_CLICK_KWARGS` applies to deck clicks.

The default `keepItems` seeds from the value table above -- the items worth a bank trip rather
than a drop. The user is expected to edit it; the design's contribution is that the list is real
item names measured from the wiki's own output table, not invented.

#### Own-boat facility identification (the only Java change)

`ObjectProvider` stamps every object with **`boatViewId`**: the id of the outermost non-top-level
`WorldView` in the recursion chain it was found in, or `null` for objects in the top-level ocean
scene. Outermost, not innermost, because `ObjectProvider`'s own comment records that the helm
lives in a `WorldEntity` nested *inside* the boat's main view -- keying on the immediate view
would split one boat's fixtures across several ids.

`PlayerProvider` reports the same value for the local player, computed the same way, as
`boatViewId` (alongside the existing `debugWorldViewId`).

`sailing.py` grows one helper:

```python
def find_facility(name, action=None, player=None):
    """The named facility ON OUR OWN BOAT, or None."""
```

It filters `/objects` on `boatViewId == player["boatViewId"]`. **When either field is absent -- an
un-rebuilt client -- it falls back to today's nearest-by-name behaviour**, so nothing regresses on
a build without the change; it just stays exposed to the neighbour-boat bug it already has.

`deploy_hook` moves onto `find_facility`. The station, the cargo hold, and (in sub-project 2) the
sidepanel's facility rows all use it too.

**Requires a client rebuild and restart** (`./gradlew.bat run`).

#### Phase machine

Derived from live state on every tick, never from a stored phase -- the same rule
`karambwan_fishing.derive_phase` and `corrupted_gauntlet.derive_phase` follow, and for the same
reason: `ctx.state.data` is wiped on restart while the boat's real state is not.

```
sort   if  (hold is full)
       or  (sortWhenIdle and no wreck in range and there is salvage to sort)
stow   if  free inventory slots <= dropAtFreeSlots and the inventory holds salvage
hook   if  a wreck is in range
wait   otherwise
```

Evaluated in that order. The user's interrupt rule is the first clause: **a wreck rising abandons
the sort and returns to the hook, unless the hold is full** -- because with a full hold there is
nowhere for new salvage to go, so hooking would just fill the inventory and stall.

"There is salvage to sort" means salvage in the inventory, or salvage recorded in the hold the
last time `943.10` was read. **When the hold has never been read this session, the answer is
"unknown", and unknown means try once**: enter `sort`, open the hold, and let step 1 find out.
Treating unknown as "no" would mean a restart during a respawn gap never sorts until the next
stow; treating it as "yes" forever would spin. One read settles it, and the read is recorded.

#### The stow loop

1. `find_facility("cargo hold", "Open")` on our own boat; click it.
2. Confirm interface 943 is open (widget `943.1` present).
3. Click **`943.14` `Deposit Salvage`**.
4. Confirm on the inventory's salvage count reaching 0.
5. Read **`943.4`** and **`943.5`** into `ctx.state.data` as the hold's used/capacity.
6. Close (`943.1`'s `Close`, ESC as fallback).

`stowSalvage: False` restores today's behaviour exactly: `drop_salvage` on the deck.

**Hold-full detection** is `used >= capacity - holdFullSlots`, *plus* the authoritative empirical
signal: a `Deposit Salvage` that leaves salvage in the inventory means the hold would not take it,
whatever the numbers say. This mirrors `deep_sea_trawling`'s "a Collect-from that moved nothing is
the authoritative full signal" rule, which exists because the declared capacity is a ceiling
rather than a promise.

#### The sort loop

0. **Dispose first.** Drop anything in the inventory that is neither salvage, `keepItems` nor
   `HARD_KEEP`. Without this step the loop deadlocks in a real, reachable state: enter `sort`
   holding an inventory full of the *previous* batch's sorted output, find no salvage to sort,
   and try to withdraw into an inventory with no free slots. Disposal is idempotent, so running
   it at the top of every pass costs nothing when there is nothing to drop.
1. If the inventory holds no salvage: open the hold, `Withdraw-All` the salvage stack in `943.10`
   (fills at most the free slots, since salvage does not stack in the inventory), close. If the
   hold turns out to hold no salvage either, record that and leave `sort` -- there is no work.
2. `find_facility("Salvaging station", "Sort-salvage")` on our own boat; click it.
3. Confirm the channel started on animation `13599` / `13593`, or on a Sailing XP gain.
4. Wait out `salvage_count * SORT_SECONDS_PER_SALVAGE` plus slack, polling for the inventory's
   salvage count to reach 0 -- the channel ends itself, so this is a budget, not a stop signal.
5. Dispose: keep anything matching `keepItems` or `HARD_KEEP`, drop the rest.
6. Re-derive the phase. If a wreck is now in range and the hold is not full, go back to hooking
   with whatever is left unsorted; the hold keeps it for the next idle window.

`SORT_SECONDS_PER_SALVAGE = 1.79` from the measurement above. `SORT_ANIMATIONS = {13599, 13593}`
as a **separate constant from `SALVAGE_CHANNEL_ANIMATIONS`** -- merging them would make the hook's
idle-reclick logic treat sorting as hooking.

#### Disposal

`drop_salvage` generalises to `drop_items(ctx, predicate)`, keeping every measured behaviour it
already has: the facilities-tab `w` press, the Space fallback for the 229 box,
`check_ui_blockers=False`, `simulate_mistakes=False`, and the 0.06-0.12s inter-click delay. The
existing "drop everything ending in salvage" call becomes one predicate; the new "drop everything
not kept" is another.

#### Mass-world mode

When `massWorld` is true, `tick()` skips:

- `find_nearby_wreck` and the whole far-wreck branch (so `sail_to_pair` is never called),
- the hook-engage-timeout **reposition** (`sailing.sail_to` toward an approach point).

The reposition budget still counts: a wreck whose hook will not engage after
`HOOK_ENGAGE_MAX_REPOSITION_ATTEMPTS` re-clicks is still given up for
`HOOK_ENGAGE_GIVEUP_COOLDOWN_S`, it just is not approached first. Everything else --
`HOOK_CLICK_COOLDOWN_S`, the sunk-chat redeploy, the hazard blacklist, the XP watchdog -- is
untouched.

"No wreck in range" stops being a problem to fix and becomes the `wait` phase, which
`sortWhenIdle` turns into useful work.

### Sub-project 2: crew swap (deferred until sub-project 1 has run live)

On entering `sort`, assign the crewmate to the hook the player is leaving; on returning to `hook`,
take it back. Path: open the sidepanel, `937.35` `Crewmates` (or `937.33` `Facilities` and the
`937.25` `Assign` on the hook's row -- **which of the two is the working flow is the first live
question**), then answer dialogue group 219 by number key, applying `giants_foundry.py`'s measured
correction that group 219's `options[0]` is the menu TITLE and real options are numbered from 1.

Deferred deliberately: it is the only part resting on an interface nobody has driven, and the rest
of the feature is worth having without it.

## Testing

`ControllerAddon/tests/test_shipwreck_salvaging.py` (already hermetic via `_no_live_bridge`) gains:

- **Phase derivation table** -- every combination of (wreck in range, hold full, salvage in
  inventory, salvage in hold, `massWorld`, `sortWhenIdle`) mapped to its expected phase, including
  the two interrupt cases: a wreck rising with a full hold keeps sorting; a wreck rising with room
  in the hold returns to the hook.
- **Keep-list vs hard-keep** -- a `keepItems` entry that would match a tool does not drop it; a
  substring entry matches the family it is expected to; an empty keep-list drops everything except
  hard-keeps.
- **Hold-full arithmetic** -- the `holdFullSlots` boundary, and the empirical override (salvage
  left in the inventory after `Deposit Salvage` means full regardless of the numbers).
- **`massWorld` asserts zero sail calls** -- `sailing.sail_to` and `sail_to_pair` monkeypatched to
  raise, driving a tick with a far wreck present and no near wreck.
- **The dispose-first deadlock** -- entering `sort` with a full inventory of already-sorted output
  and salvage still in the hold must drop the output and withdraw, not stall.
- **`find_facility`** -- picks the object whose `boatViewId` matches the player's out of several
  identically-named ones at the same coordinates (the live 26-station geometry as a fixture), and
  falls back to nearest-by-name when the field is absent.

`ObjectProviderTest` and a `PlayerProvider` assertion cover the `boatViewId` stamping Java-side.

## Live-verification checklist (first session's worklist, in the order failures would bite)

1. **`boatViewId` actually separates our boat from the neighbours'** -- stand in the same stacked
   spot and confirm `find_facility` returns exactly one station, one hold, and our own hooks.
2. **`943.14` `Deposit Salvage` deposits salvage only** -- confirm the chisel, rune pouch and
   coins survive a stow.
3. **`943.4` / `943.5` really are used/capacity**, and both update after a deposit.
4. **`Withdraw-All` on `943.10`** fills the inventory and stops at the free-slot count.
5. **`SORT_SECONDS_PER_SALVAGE`** against a full 28-salvage batch, not the 12 measured here.
6. **Salvage tiers other than Plundered** -- confirm the 229 box is detected by presence, not by
   its tier-specific wording.
7. **Whether the hold's item grid distinguishes salvage tiers** the routine should sort in a
   particular order.
7b. **The default `keepItems` list against real drops.** It is derived from the money-making
   guide's output table plus a single live 12-salvage sort (which produced only Rune scimitar and
   Rune cannonball); a 7-minute capture aimed at collecting more caught no sorting at all. So the
   exact item NAMES the game hands back are wiki-sourced, not measured -- a name that differs even
   slightly (plurals, "(empty)" suffixes) silently drops an item the user meant to keep. Log every
   dropped item name on the first run and diff it against the list.
8. **`sortWhenIdle` genuinely fits a respawn gap** -- measure the real gap on a mass world against
   the 1.79s-per-salvage batch length, and confirm the interrupt returns to the hook fast enough
   not to lose a wreck.
9. Whether the cargo-hold **item container** (`963..967`) ever populates -- if it does under some
   condition, `/trawling` becomes a cheaper fullness read than opening the interface, and
   `deep_sea_trawling.py` needs the same answer.

## Risks

- **The one Java change needs a client rebuild.** Until then `find_facility` falls back to
  nearest-by-name and the neighbour-boat bug remains -- so a first run on a rebuilt client is a
  precondition for trusting any station or hold click on a mass world.
- **`sortWhenIdle` competes with the hook for the player's body.** The interrupt rule bounds the
  loss, and it is unmeasured: if respawn gaps turn out to be shorter than one withdraw-plus-sort
  cycle, `sortWhenIdle` should be gated on a minimum observed gap instead.
- **Group 229 is invisible to `dialogOpen`**, so any new path that can raise it (stow, withdraw,
  sort) must check `229.4` explicitly. This has already cost live sessions in two other routines.
