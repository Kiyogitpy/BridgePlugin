# Pyramid Plunder routine

## Problem

We want a new automation routine for Pyramid Plunder, the Thieving minigame
in the Jalsavrah Pyramid (Sophanem). The chosen strategy is loot-efficiency,
not XP-efficiency: only the Grand Gold Chest and Sarcophagus are looted in
each room (never the 13 urns per room), sarcophagi are skipped in rooms 1-3
(low sceptre odds don't justify their long open time), looted in rooms 4-6,
and after room 6 is looted the run is deliberately abandoned and restarted
from room 1 rather than pushing on to rooms 7-8. All loot except the
Pharaoh's sceptre is dropped on the spot -- there is no banking loop in this
routine at all.

## Scope

1. A new `routines/pyramid_plunder.py`: full loop from outside the pyramid
   (finding the correct one of the 4 anonymous doors) through repeated
   room-1-through-6 cycles until manually stopped.
2. A new `PyramidPlunderProvider.java` bridge provider exposing the room
   number, run timer, and spear-trap-armed state -- none of which any
   existing provider surfaces today.
3. Wiring: `ROUTINE_FILES` in `controller.py`, a new `PYRAMID_PLUNDER` entry
   in `RoutineChoice.java`. No `ControllerConfig.java` panel/section --
   this routine uses a plain `DEFAULT_CONFIG`/`get_config`, same as
   `pest_control.py`/`mining.py`/`gemstone_crab.py` (see CLAUDE.md's routine
   contract notes on when a Java config section is actually required).
4. Explicitly NOT in scope:
   - Urns (any room), and sarcophagi in rooms 1-3.
   - Rooms 7-8 -- the run always restarts after room 6.
   - Banking of any kind -- non-sceptre loot is dropped, not stored.
   - Prayer management (Protect from Melee, etc.) for mummy/scarab-swarm
     damage -- explicitly rejected in favor of the existing generic
     food-eat safety net (`consume_first(ctx.ser, "food")`), same
     precedent as `slayer.py`. Expected to rarely matter in practice.
   - Acquiring a lockpick, food, or antipoison -- the routine uses a
     lockpick if one is already in inventory, but doesn't restock any of
     these itself.
   - Handling the Pharaoh's sceptre once obtained (crafting/recharging/
     using it) -- the game auto-ejects the player the instant it's found,
     which the routine's own state-machine dispatch naturally absorbs (see
     Design), but no special sceptre-specific action is taken beyond
     logging it and never dropping it.

## Research method and confidence

- **Local OSRS Wiki mirror** (`data/raw/`) for `Pyramid Plunder.txt`,
  `Sarcophagus (Pyramid Plunder).txt`, `Grand Gold Chest.txt`,
  `Tomb Door.txt`, `Tomb Door (exit).txt`, `An anonymous looking door.txt`,
  `Guardian mummy.txt`, `Mummy (Pyramid Plunder).txt` -- reliable for stated
  game mechanics, room-by-room requirements/loot tables, and door/chest/
  sarcophagus interaction descriptions.
- **`data/decompiled/client-1.12.33/.../plugins/pyramidplunder/`** (the
  actual official RuneLite Pyramid Plunder plugin, decompiled from the
  currently-running client build) -- this is the highest-confidence source
  in this design, not a guess: it gave the exact varbits and object ids the
  live client uses, not wiki-inferred approximations.
- **`IdeaProjects/runelite` checkout's `VarbitID.java`** -- confirmed named
  constants for the same varbit numbers found in the decompiled plugin
  (`NTK_TRAP_ACTIVE=2365`, `NTK_PLAYER_TIMER_COUNT=2375`,
  `NTK_THIEVING_REQUIRED=2376`, `NTK_ROOM_NUMBER=2377`), plus the Guardian
  mummy's lobby coordinate (`WorldPoint(3288, 2787, 0)`) from
  `MinigameLocation.java`. Per this project's own established caveat, this
  checkout can drift stale relative to the live client -- but here it only
  *corroborates* names for values already pulled from the version-matched
  decompiled plugin, so it isn't load-bearing on its own.
- **User-supplied live gameplay knowledge**: the strategy itself (which
  rooms/containers to loot, when to restart, drop-everything-but-sceptre),
  and the chat message text a wrong tomb door produces (`"This door leads
  to a dead end."`) -- not independently found in either research source
  above.
- **Existing codebase conventions**, read directly from `pest_control.py`
  (region/varbit-driven phase dispatch re-evaluated every tick, no assumed
  phase), `golem_crafting.py` (`on_event`/`CHAT_MESSAGE` handling for
  actions the server confirms/rejects asynchronously a few ticks after the
  click, not synchronously), `slayer.py` (inline food-eat safety net), and
  `MixologyProvider.java` (the pattern for a dedicated per-minigame bridge
  provider reading `VarbitID` fields).
- **Not yet done, flagged for implementation-time live verification** (see
  Testing): the exact "wrong outer door -> abandoned room" detection signal
  and how to leave that room; whether tomb-door Pick-lock and Enter are one
  click or two; what a wrong-but-successfully-picked tomb door actually
  does; whether the spear trap is mandatory to path through or just an
  optional bonus-XP obstacle; live timing for the sarcophagus's opening
  animation before it becomes searchable.

## Key facts from research

- **Region**: the entire pyramid interior (all 8 rooms) is a single region,
  `7749` -- rooms are distinguished purely by varbit, never by region id.
- **Varbits** (`net.runelite.api.gameval.VarbitID`):
  - `NTK_ROOM_NUMBER` (2377) -- current room, 1-8.
  - `NTK_PLAYER_TIMER_COUNT` (2375) -- ticks elapsed in the current run;
    `0` means not currently in an active run (`PyramidPlunderPlugin`'s own
    `isInPyramidPlunder()` is exactly `regionId==7749 && varbit(2375) > 0`).
    Full run duration is 501 ticks (~5 minutes).
  - `NTK_TRAP_ACTIVE` (2365) -- `1` while the current room's spear trap is
    still armed.
  - `NTK_THIEVING_REQUIRED` (2376) -- the current room's Thieving level
    requirement (21/31/.../91) -- not needed by this design's logic (room
    number alone drives every branch) but cheap to expose alongside the
    others for debug-log purposes.
- **Object ids** (from the decompiled plugin, take priority over the wiki
  infobox's ids where they differ -- e.g. the wiki lists the opened
  sarcophagus as `21256`, the live client plugin uses `26626`):
  - Spear trap: `21280`.
  - Grand Gold Chest: closed `20946` -> searched `26616`.
  - Sarcophagus: closed `21255` -> opened `26626` (searchable).
  - Tomb Door (the 4 per-room progression doors, all identical in
    appearance): closed `20948`; the 4 physical wall-object instances are
    ids `26618`-`26621`.
  - Tomb Door (exit): `20931`, options "Leave Tomb" / **"Quick-leave"** --
    this is what ends a run instantly from inside any room, no need to walk
    back through previously-opened doors.
- **Loot mechanics**: Grand Gold Chest and Sarcophagus each independently
  roll for the Pharaoh's sceptre on every search/open attempt (regardless of
  success/failure since the 2023-08-30 rework); sceptre odds improve in
  later rooms. A scarab swarm (25 hp, weak but poisons) can spawn from the
  chest; a mummy (lvl 84, up to 6 dmg) can spawn from the sarcophagus, and
  is only ever aggressive toward the player who triggered it. Finding the
  sceptre immediately ejects the player from the pyramid.
- **Tomb doors**: 4 per room, 1 real + 3 fake, same for every player in that
  room until a new player enters room 1 (which re-randomizes for everyone).
  A wrong door produces the chat message `"This door leads to a dead end."`
  (user-confirmed). After 3 confirmed-wrong doors, the 4th always works
  (hidden game rule), bounding worst case at 4 attempts per room.
- **Outer entry**: 4 static "anonymous looking door" objects around the
  pyramid exterior (fixed positions -- only *which one* is correct
  rotates, roughly every 25 minutes, the same for all players until then).
  A wrong door leads to an "abandoned room"; the correct one leads to the
  Guardian mummy's lobby, who has a direct "Start-minigame" option.

## Design

### 1. `PyramidPlunderProvider.java` (new)

Same shape as `MixologyProvider.java` -- reads `VarbitID` fields directly,
no caching (single-entity-style cheap read):

```java
public class PyramidPlunderProvider extends BridgeProvider {
    public void register() {
        server.register("/pyramid-plunder", () -> onClient(this::getState));
    }

    private Object getState() {
        Map<String, Object> data = new HashMap<>();
        boolean inPyramidRegion = client.getLocalPlayer() != null
            && client.getLocalPlayer().getWorldLocation().getRegionID() == 7749;
        int timerTicks = client.getVarbitValue(VarbitID.NTK_PLAYER_TIMER_COUNT);
        data.put("inRun", inPyramidRegion && timerTicks > 0);
        data.put("roomNumber", client.getVarbitValue(VarbitID.NTK_ROOM_NUMBER));
        data.put("timerTicks", timerTicks);
        data.put("trapActive", client.getVarbitValue(VarbitID.NTK_TRAP_ACTIVE) == 1);
        return data;
    }
}
```

Registered in `BridgeHttpServer` alongside the other providers (same
one-line pattern every existing provider already follows).

### 2. `routines/pyramid_plunder.py` (new)

```python
DEFAULT_CONFIG = {
    "enabled": True,
    "lastRoomToLoot": 6,          # loot chest+sarco through this room, then quick-leave and restart
    "sarcophagusStartRoom": 4,    # skip sarco in rooms before this
    "useLockpick": True,          # use a lockpick on tomb doors if carried
    "artefactNames": [            # fixed drop-list -- never a substring match, so food/lockpick
        "ivory comb", "pottery scarab", "pottery statuette", "stone seal",
        "stone scarab", "stone statuette", "gold seal", "golden scarab",
        "golden statuette",
    ],
    "food": {"enabled": True, "eatAt": 5},   # same shape as slayer.py's food_cfg
    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45, "everyMaxMinutes": 75,
        "durationMinMinutes": 3, "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {"stopOnDeath": True, "maxRuntimeHours": 0},
}

def get_config(bridge_config):
    # No Controller Settings panel for this (same as pest_control.py/mining.py) --
    # just use DEFAULT_CONFIG.
    return DEFAULT_CONFIG
```

**Phase dispatch**, re-evaluated fresh every tick from live bridge state
(mirrors `pest_control.py`'s region-based `tick()` -- no phase is ever
assumed, so wherever a Quick-leave/timeout/sceptre-eject actually drops the
player, the very next tick's read of `/pyramid-plunder` + `/player` picks
the right phase on its own):

- **`OUTSIDE`** (`inRun == False` and the Guardian mummy isn't nearby):
  - If `ctx.state.data["correct_door_index"]` is set, search that door
    first. Success -> Guardian mummy becomes reachable -> `LOBBY` next
    tick. Failure (stunned / no state change after a wait) -> clear the
    cached index and fall through to the trial loop below.
  - Otherwise, cycle through the 4 static anonymous-door objects (stable
    clockwise ordering by world position, so the same physical door is
    always index 0/1/2/3 across sessions) until one succeeds; cache its
    index in `ctx.state.data["correct_door_index"]` once found. This cache
    is expected to keep working across every restart for the life of the
    ~25-minute rotation window -- most entries after the first should be a
    single direct click, not a fresh 4-door search.
  - **NEEDS LIVE VERIFICATION**: the exact detection signal for "this was
    the wrong door" (an abandoned room, not the lobby) and how to leave
    that room back outside to try the next one.

- **`LOBBY`** (Guardian mummy reachable, `inRun == False`): right-click the
  Guardian mummy -> "Start-minigame". Wait for `/pyramid-plunder` to report
  `inRun == True` (room 1) before continuing.

- **`IN_ROOM`** (`inRun == True`): dispatches on `roomNumber` into the
  room sub-loop below, tracked via `ctx.state.data["room_step"]` (values:
  `"trap"`, `"chest"`, `"sarco"`, `"door"`) so a single room's work spans
  multiple ticks cleanly, same "one phase function per tick, write the next
  step when done" idiom `golem_crafting.py` already uses:
  1. **`trap`**: if `trapActive`, disarm the spear trap (obj id `21280`),
     retrying on the 1-4 dmg failure roll. -> `chest`.
     **NEEDS LIVE VERIFICATION**: whether this is actually mandatory to
     reach the chest/sarcophagus/doors, or just an optional obstacle.
  2. **`chest`**: search the Grand Gold Chest (`20946` -> `26616`). ->
     `sarco` if `roomNumber >= sarcophagusStartRoom`, else -> `door`.
  3. **`sarco`**: click "Open" on the Sarcophagus (`21255`), wait for it to
     become searchable (`26626`), click "Search". No mummy-specific
     handling beyond the routine's normal food-eat safety net. -> `door`.
  4. **`door`**:
     - If `roomNumber >= lastRoomToLoot`: this room's looting is done --
       click the exit door's **Quick-leave** option (`20931`) and reset
       `room_step` for next run. Dispatch naturally lands back on
       `OUTSIDE`/`LOBBY` next tick, whichever the game actually did.
     - Else: work through the 4 Tomb Doors (`20948`) one at a time --
       pick-lock (using a lockpick if `useLockpick` and one is carried),
       then Enter. `on_event` watches for the `CHAT_MESSAGE` substring
       `"this door leads to a dead end."`: if seen, this door is confirmed
       wrong, advance to the next of the 4. If instead `roomNumber`
       increments with no dead-end message, it worked -- reset `room_step`
       to `"trap"` for the new room. Capped at 4 door attempts (the
       "3 wrong -> 4th always works" rule bounds this).
     **NEEDS LIVE VERIFICATION**: whether Pick-lock and Enter are one click
     or two, and what a wrong-but-successfully-picked door does before the
     dead-end message (if anything visible at all).

- **Loot drop**, independent of the phase above: after every chest/sarco
  search, scan inventory for any name in `artefactNames` and left-click it
  (`check_ui_blockers=False`, same idiom as `shipwreck_salvaging.py`'s
  `drop_salvage` -- needs "Drop" set in Controller Settings -> Clicking ->
  Forced Actions). If "Pharaoh's sceptre" appears in inventory, log it and
  do nothing else -- the game's own auto-eject handles the rest, and it's
  the one item never in `artefactNames` so it's never at risk of being
  dropped.

- **Food safety**, independent of phase, every tick: `hp <= food.eatAt` ->
  `controller.consume_first(ctx.ser, "food")`, identical inline pattern to
  `slayer.py`.

### 3. Wiring

- `controller.py`: add `"PYRAMID_PLUNDER": "routines/pyramid_plunder.py"`
  to `ROUTINE_FILES`.
- `RoutineChoice.java`: add `PYRAMID_PLUNDER("Pyramid Plunder")`.
- No `ControllerConfig.java`/`ConfigExporter.java` changes -- this routine
  never reads `GET /config` (see Scope).

### 4. Error handling & safety

- Hard safety-stop (matching this codebase's existing convention) if no
  anonymous door / Guardian mummy / chest / tomb door can be found at all
  within a reasonable number of attempts -- distinguishes "genuinely lost"
  from an in-progress multi-tick action.
- `stopOnDeath: True` plus the food-eat safety net is the entire defense
  against mummy/scarab-swarm damage, per Scope's explicit rejection of
  prayer management.
- The sceptre-drop-list is a fixed allowlist, not a substring/blocklist --
  guarantees food, lockpick, and the sceptre itself can never be
  accidentally dropped no matter what other loot names look like.
- Every "NEEDS LIVE VERIFICATION" item called out above gets an inline
  comment in the implementation (same convention as `golem_crafting.py`/
  `blast_furnace.py`'s "ASSUMPTION, NOT YET VERIFIED LIVE" notes), not
  silently trusted as written.

## Testing

No unit-test framework exists for routines in this codebase --
verification is live, against the running bridge. Given the number of
flagged unknowns above (more than a typical routine here, concentrated in
the outer-door and tomb-door mechanics), stage it rather than one
end-to-end run:

1. Manually walk through entering the pyramid once (find the correct outer
   door, reach the Guardian mummy, Start-minigame) to confirm the
   wrong-door/abandoned-room signal and settle how to leave it.
2. Manually pick a known-wrong tomb door once to confirm the exact chat
   text and casing, and to see what (if anything) happens before that
   message arrives.
3. Run the routine live through one full room 1-6 cycle, watching phase/
   step transitions in the debug log, confirming the spear trap's actual
   effect on pathing and the sarcophagus open-to-searchable timing.
4. Confirm the room-6 Quick-leave -> restart loop actually lands back in a
   phase the dispatch logic recognizes (`OUTSIDE` or `LOBBY`), across at
   least 2-3 consecutive restarts, before trusting the `correct_door_index`
   cache for an unattended soak run.
5. Only then a longer unattended soak run.
