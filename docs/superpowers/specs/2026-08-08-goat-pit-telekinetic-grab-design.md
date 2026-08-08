# Goat Pit hunting via Telekinetic Grab

## Problem

`golem_crafting.py`'s `hunt_phase` (see `2026-08-07-golem-crafting-design.md`)
was built on an explicitly flagged, never-verified assumption: that a plain
left-click on a wild goat resolves to "Lure" (and the same click on a trapped
goat resolves to "Harvest"). Live use (this session) confirms that assumption
was wrong. The real mechanic, per the user (account owner, verified live):

- Wild goats are pulled into the lined pit by casting the **Telekinetic
  Grab** spell (Magic) on them, not by clicking them directly.
- Grab can only target goats that are **not on the same side of the pit the
  player stands on** -- the player always approaches/stands on the pit's
  **north** side, so valid targets are goats to the south, east, or west.
  Telekinetic Grab's cast range is **10 tiles**; a target beyond that makes
  the character auto-walk toward it to get in range, which would pull the
  player off the fixed north tile the whole design depends on.
- Once enough goats have been pulled in, the chat message **"The pit is now
  filled with goats."** appears. Only then is the pit harvested (a plain
  click on the pit itself, not on individual goats).
- Harvesting always yields a **Goat horn** (useless, dropped) and has a
  **1/4 chance per trapped goat** of also yielding Wyrmscraig goat fur
  (routed straight into the open fur pouch, same as before).
- The pit needs to be **relined with wooden spikes before every single
  harvest cycle**, not just once per hunt visit.
- The fur pouch holds up to **28 fur**. Once full, any further fur spills
  into loose inventory instead of the pouch -- this is the signal the
  routine uses to know it's topped off, at which point it skips `bank_phase`
  entirely (nothing to deposit or withdraw for fur) and goes straight back
  to `mine_phase`.
- Telekinetic Grab consumes 1 law rune + 1 air rune per cast, drawn from a
  rune pouch (kept out of bank deposits -- see `BANK_KEEP_ITEMS`). No
  in-routine restock exists for runes; running out safety-stops.

This document covers only the `hunt_phase` rework and its two new pieces of
supporting infrastructure (spell casting, spellbook widget lookup). Nothing
else in `golem_crafting.py`'s design changes.

## Scope

1. New Java `SpellbookProvider` (`GET /spellbook`) exposing the currently
   open spellbook's spell icons by name and screen position.
2. New Python `controller.get_spellbook()` / `controller.cast_spell_on()`.
3. A rewritten `hunt_phase` in `golem_crafting.py`: spike batch pickup ->
   walk to the fixed north tile -> line the pit -> Grab-cast valid goats
   until full -> harvest -> drop horns -> detect pouch-full spillover ->
   reline-and-repeat or skip straight to mining.
4. Explicitly NOT in scope:
   - General-purpose spellcasting for other routines/spells beyond what
     `cast_spell_on` needs generically (it takes a spell name, so nothing
     stops reuse, but no other routine is being wired up here).
   - Any UI for choosing which spellbook is active -- assumes the player is
     already on the standard spellbook (where Telekinetic Grab lives).
   - Rune restocking. Out of runes is a safety-stop, not a recoverable
     state.

## Research method and confidence

- **User-verified live play** (this session) for every mechanic above --
  the wrong original assumption was also user-caught live, so this
  supersedes it entirely.
- **RuneLite API inspection** (`runelite-api-1.12.33.jar`, via `javap`) --
  found `InterfaceID.MagicSpellbook.TELEGRAB`, a direct generated
  packed-widget-id constant for Telekinetic Grab specifically (same
  `InterfaceID.<Group>.<CONSTANT>` idiom `BankProvider.java` already uses
  for e.g. `InterfaceID.Bankmain.SEARCH`). This replaces the original plan
  of walking the spellbook's dynamic children and matching by
  `Widget.getName()` -- no name-matching or iteration needed, just
  `client.getWidget(InterfaceID.MagicSpellbook.TELEGRAB)` directly. Still
  **NOT YET VERIFIED LIVE** that this constant resolves to a real, visible
  widget once the Magic tab is actually open in this game (only confirmed
  to exist in the API jar) -- verify via `GET /spellbook` before trusting
  this unattended.
- Exact chat message wording for the rune-shortage rejection ("You do not
  have enough &lt;rune&gt; to cast this spell.") was given directly by the
  user, not independently captured live -- match on the stable substrings
  (`"you do not have enough"`, `"to cast this spell"`) rather than the full
  string, since the rune name varies.
- The "pit full" message wording ("The pit is now filled with goats.") and
  the horn/fur drop rates and pouch-full spillover behavior were likewise
  given directly by the user from live experience.

## Design

### 1. `SpellbookProvider.java` (new)

```java
public class SpellbookProvider extends BridgeProvider
{
    @Override
    public void register()
    {
        server.register("/spellbook", () -> onClient(this::getSpells));
    }

    private Object getSpells()
    {
        Widget container = client.getWidget(WidgetID.SPELLBOOK_GROUP_ID, 0);
        List<Map<String, Object>> list = new ArrayList<>();
        if (container == null) return list;  // spellbook tab not open

        for (Widget child : container.getDynamicChildren())
        {
            if (child == null || child.isHidden() || child.getName() == null || child.getName().isEmpty())
                continue;
            Rectangle b = child.getBounds();
            Map<String, Object> data = new HashMap<>();
            data.put("name", child.getName());       // e.g. "Telekinetic Grab"
            Point screen = RuneLiteUtil.canvasToScreen(client, b.x + b.width / 2, b.y + b.height / 2);
            data.put("screenX", screen.x);
            data.put("screenY", screen.y);
            data.put("visible", b.width > 0 && b.height > 0);
            list.add(data);
        }
        return list;
    }
}
```

Identifies spells **by name**, not a hardcoded child index -- avoids betting
on an unverified index for "Telekinetic Grab" specifically (the exact
row/position of a low-level spell isn't worth memorizing when the widget
already carries its own name, same reasoning `ObjectProvider`/`NPCProvider`
already use everywhere else in this codebase). Empty list is the natural
"spellbook isn't open" signal, matching every other provider's fails-safe
convention.

Register it alongside the other providers in the plugin's provider list
(wherever `ObjectProvider`/`NPCProvider`/etc. are constructed and
`register()`-ed).

### 2. `controller.py` additions

```python
def get_spellbook():
    return api_get("/spellbook") or []

def open_spellbook_if_needed(ser):
    """Idempotent -- taps T only if the spellbook isn't already showing spells."""
    if get_spellbook():
        return
    key_tap(ser, "T")
    time.sleep(random.uniform(0.3, 0.5))

def cast_spell_on(ser, spell_name: str, target: dict) -> bool:
    """Click a named spell, then click the target -- same two-click
    'use X on Y' shape as any item-on-entity interaction elsewhere in this
    codebase. Returns False if the spell can't be found (spellbook not
    open/wrong spellbook) or either click misses."""
    open_spellbook_if_needed(ser)
    spell = next((s for s in get_spellbook() if s.get("name", "").lower() == spell_name.lower()), None)
    if spell is None or not spell.get("visible"):
        return False
    if not click_entity(ser, spell, offset=(0, 0), check_ui_blockers=False):
        return False
    time.sleep(random.uniform(0.15, 0.3))
    return click_entity(ser, target)
```

### 3. New constants in `golem_crafting.py`

```python
GRAB_SPELL_NAME = "Telekinetic Grab"
LAW_RUNE_NAME = "Law rune"      # unused for pre-checking (see rune handling below);
AIR_RUNE_NAME = "Air rune"      # kept only for readability/reference in comments
GOAT_HORN_NAME = "Goat horn"
FUR_POUCH_CAPACITY = 28
GRAB_RANGE_TILES = 10
GOAT_CHAT_PIT_FULL = "the pit is now filled with goats."
GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS = ("you do not have enough", "to cast this spell")
WOODEN_SPIKES_BATCH = 10  # informational -- matches how many the Spikes supply gives per Take;
                          # _ensure_wooden_spikes' existing "is there at least one" check already
                          # handles this transparently regardless of the actual batch size
```

### 4. Hunt-phase chat capture (separate from golem-crafting's own)

A new listener, parallel to `on_event`, writing into its own state keys so
it can never cross-talk with `craft_phase`'s `gc_last_chat`/`gc_last_chat_seq`
sequencing (the two phases are mutually exclusive via `gc_phase`, but keeping
the data isolated avoids any future ambiguity if that ever changes):

```python
def on_hunt_event(ctx, event_type, payload) -> None:
    if event_type != "CHAT_MESSAGE" or not payload:
        return
    message = (payload.get("message") or "").lower()
    is_pit_full = GOAT_CHAT_PIT_FULL in message
    is_rune_short = all(part in message for part in GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS)
    if not (is_pit_full or is_rune_short):
        return
    ctx.state.data["gc_last_hunt_chat"] = message
    ctx.state.data["gc_last_hunt_chat_seq"] = ctx.state.data.get("gc_last_hunt_chat_seq", 0) + 1
```

Wired into the controller's event dispatch the same way `on_event` already
is (`controller.py`'s main loop drains events and calls both before
`tick()`).

### 5. Geometry and range filter

```python
def is_valid_grab_target(pit: dict, goat: dict) -> bool:
    """South, east, or west of the pit only -- north is the player's own
    fixed standing side, per the account owner's live-verified play."""
    return goat.get("y", 0) <= pit.get("y", 0)

def goat_in_grab_range(player: dict, goat: dict) -> bool:
    """Telekinetic Grab's cast range -- a farther target makes the
    character auto-walk toward it, pulling the player off the fixed
    standing tile the whole north-side design depends on."""
    dx = abs(goat.get("x", 0) - player.get("x", 0))
    dy = abs(goat.get("y", 0) - player.get("y", 0))
    return max(dx, dy) <= GRAB_RANGE_TILES
```

### 6. Rewritten `hunt_phase`

State keys (new/reused): `gc_pit_lined` (reused), `gc_hunt_standing` (new,
bool), `gc_pit_filled` (new, bool), plus the existing
`gc_pouch_opened_this_hunt`, `gc_hunt_no_goat_streak`.

```
def hunt_phase(ctx, config):
    if not _ensure_wooden_spikes(ctx, config):
        return  # existing helper, unchanged -- takes a fresh batch from
                # the Spikes supply next to Geoff once inventory has none

    pit = find_object(GOAT_PIT_NAME)
    if pit is None:
        handle_missing_target(...) / safety_stop  # existing pattern

    if not ctx.state.data.get("gc_hunt_standing"):
        target = {"x": pit["x"], "y": pit["y"] + 2, "plane": pit.get("plane", 0)}
        # walk to the tile 1 north of the pit (2 tiles from pit center per
        # the account owner's own confirmed live position) -- reuse
        # walk_to_golem_side's exact-arrival idiom, not travel.walk_to's
        # 3-tile tolerance, since standing on the WRONG side here breaks
        # the whole valid-target geometry, not just "close enough"
        if not walk_to_exact_tile(ctx, target):
            return
        ctx.state.data["gc_hunt_standing"] = True

    if not ctx.state.data.get("gc_pit_lined"):
        # line the pit (spikes + pit click) -- existing click sequence,
        # now happens only once already standing on the fixed tile
        ...
        ctx.state.data["gc_pit_lined"] = True
        ctx.state.data["gc_pit_filled"] = False

    if not ctx.state.data.get("gc_pit_filled"):
        player = controller.get_player()
        goats = [n for n in controller.get_npcs()
                 if GOAT_NAME in (n.get("name") or "").lower()
                 and n.get("visible", True)
                 and is_valid_grab_target(pit, n)
                 and goat_in_grab_range(player, n)]
        if not goats:
            # existing GOAT_NO_GOAT_CLEAR_STREAK debounce -> bank_phase fallback
            ...
            return
        goats.sort(key=lambda n: n.get("distance", 9999))
        controller.cast_spell_on(ctx.ser, GRAB_SPELL_NAME, goats[0])
        time.sleep(random.uniform(0.6, 1.0))  # let the chat message land

        seq = ctx.state.data.get("gc_last_hunt_chat_seq", 0)
        if seq > ctx.state.data.get("gc_hunt_chat_seen_seq", 0):
            ctx.state.data["gc_hunt_chat_seen_seq"] = seq
            message = ctx.state.data.get("gc_last_hunt_chat", "")
            if all(p in message for p in GOAT_CHAT_NOT_ENOUGH_RUNES_PARTS):
                ctx.state.trigger_safety_stop("out of law/air runes for Telekinetic Grab")
                return
            if GOAT_CHAT_PIT_FULL in message:
                ctx.state.data["gc_pit_filled"] = True
        return

    # pit is full -- harvest
    pit = find_object(GOAT_PIT_NAME)  # fresh lookup, same idiom as craft_phase
    controller.click_entity(ctx.ser, pit)
    time.sleep(random.uniform(0.6, 1.0))

    for horn in [i for i in controller.get_inventory()
                 if GOAT_HORN_NAME.lower() in (i.get("name") or "").lower()]:
        controller.click_entity(ctx.ser, horn, check_ui_blockers=False)  # "Drop"
        time.sleep(random.uniform(0.2, 0.4))

    spilled_fur = any(
        (i.get("name") or "").lower() in {f.lower() for f in FUR_PRIORITY}
        for i in controller.get_inventory()
    )
    ctx.state.data["gc_pit_lined"] = False
    ctx.state.data["gc_pit_filled"] = False
    if spilled_fur:
        debug_log.log("golem crafting", "fur pouch full -- skipping bank, heading to mine phase")
        # Leftover wooden spikes are only useful at the pit -- no reason to carry them into
        # mine/craft phases once hunting is done for this trip (per the account owner).
        for spikes in [i for i in controller.get_inventory()
                       if WOODEN_SPIKES_NAME.lower() in (i.get("name") or "").lower()]:
            controller.click_entity(ctx.ser, spikes, check_ui_blockers=False)  # "Drop"
            time.sleep(random.uniform(0.2, 0.4))
        ctx.state.data["gc_fur_stock"] = FUR_POUCH_CAPACITY
        ctx.state.data["gc_hunt_standing"] = False
        ctx.state.data["gc_phase"] = "mine"
```

`walk_to_exact_tile` is a small generalization of the existing
`walk_to_golem_side` (same exact-arrival polling loop, parameterized on an
arbitrary target tile instead of a golem-side offset) -- both need "exactly
this tile, not within 3", so the logic is shared rather than duplicated.

### 7. Testing / live verification checklist

Per this file's established convention for freshly-designed, unverified
mechanics (see the original golem-crafting design's own ASSUMPTION flags):

1. Confirm `GET /spellbook` returns "Telekinetic Grab" with a sane
   `screenX`/`screenY` once the Magic tab is manually opened.
2. Confirm a Grab cast on a south/east/west goat actually pulls it toward
   the pit (not just plays an animation) and does NOT trigger a walk when
   the target is within 10 tiles.
3. Confirm the exact "pit full" and rune-shortage message text matches
   what's coded (case-insensitive substring match already tolerates minor
   capitalization differences, but confirm the substrings themselves).
4. Confirm a plain click on a full pit resolves to "Harvest" by default
   (not "Examine" or something else needing a Forced Action).
5. ~~Confirm dropping "Goat horn" via plain click resolves to "Drop"~~ --
   CONFIRMED live (user session): "Drop" is set as the left-click Forced
   Action for Goat horn, so a plain `click_entity` resolves correctly.
6. Confirm "Wooden spikes" also has "Drop" set as its left-click Forced
   Action (Controller Settings -> Clicking -> Forced Actions) -- this is a
   separate per-item-name setting from Goat horn's, not automatically
   covered by item 5's confirmation, and the leftover-spikes drop (end of
   a hunt trip once the pouch is full) relies on it the same way.
