# Right-click primitive + menu-state bridge endpoint

## Problem

The bot can only ever left-click. Anything that requires reading or choosing from a right-click
context menu -- e.g. verifying "Mark Safespot" actually appears and works, or any future feature
needing a specific non-default menu option -- currently needs a human at the keyboard, or the
existing `MenuPriority`-style trick of force-reordering a menu entry into the left-click slot
server-side (which only works for options that can be identified and force-prioritized ahead of
time, not for reading back what's actually on screen right now).

This adds the two missing primitives: the ability to right-click, and the ability to read back
what a currently-open menu actually contains, so the bot itself can trigger and verify menu
interactions end-to-end.

## Right-click primitive

Pure Python addition to `controller.py` -- no firmware change needed. `data/Firmware.c`'s
`handleMouseButton()` already parses a button-letter generically (`'L'`/`'R'`/`'M'` all map to
`MOUSE_LEFT`/`MOUSE_RIGHT`/`MOUSE_MIDDLE`), so the existing serial protocol already accepts
`"MRD"`/`"MRU"`/`"MRC"` (mouse-right-down/up/click) identically to how it already accepts
`"MLD"`/`"MLU"`/`"MLC"`. The Arduino side needs zero changes; the gap is purely that
`controller.py` never defined the Python-side senders for the right-button variants.

Add, mirroring the existing left-button functions exactly:
- `mouse_right_down(ser)` / `mouse_right_up(ser)` / `mouse_right_click(ser)` -- send `"MRD"` /
  `"MRU"` / `"MRC"`.
- `right_click_human(ser)` -- mirrors `click_human`: right-down, small random sleep, right-up,
  small random sleep, with the same `debug_log.log("action", ...)` line style.

`click_entity` gains a `button: str = "left"` parameter instead of a parallel
`right_click_entity` function. Everything before the final click call -- misclick simulation, the
`is_point_within_window`/`is_point_blocked_by_ui` checks, `move_to_human` with `refresh_target` --
is identical regardless of which mouse button eventually clicks, so duplicating all of that into a
second function would just be copy-pasted drift waiting to happen. The only change is the last
step: `click_human(ser)` when `button == "left"` (default, every existing call site unaffected),
`right_click_human(ser)` when `button == "right"`.

## Menu-state bridge endpoint

New `MenuStateProvider.java`, following the exact `BridgeProvider` pattern every existing provider
(`NPCProvider`, `GroundItemProvider`, etc.) already uses, registered in `ControllerPlugin.startUp()`
alongside the others.

`GET /menu` reads live on each poll -- `client.isMenuOpen()` and, when true,
`client.getMenuEntries()` -- the same on-demand-read style `NPCProvider`/`GroundItemProvider`
already use for their own live state, not a new event-subscription/caching mechanism. This
sidesteps the problem `MenuPriority`'s own docs already note: `MenuOpened` fires constantly during
normal play (on every hover, not just an actual right-click), so caching off that event would mean
exposing stale/irrelevant menu contents most of the time. `client.isMenuOpen()` answers "is a menu
genuinely displayed on screen right now" directly.

Response shape:
- Nothing open: `{"open": false}`
- Menu open: `{"open": true, "x": int, "y": int, "width": int, "height": int, "entries": [{"index": int, "option": str, "target": str, "screenX": int, "screenY": int}, ...]}`

`x`/`y`/`width`/`height` come straight from `Menu.getMenuX()`/`getMenuY()`/`getMenuWidth()`/
`getMenuHeight()` (the popup's overall bounding box -- confirmed present on the `Menu` interface).
`option`/`target` per entry get the same `Text.removeTags(...)` cleaning `MenuPriority` already
applies to raw menu entry text, for consistency and so the bot can match against plain strings
like `"Mark Safespot"` without stripping color tags itself.

**`screenX`/`screenY` per entry are computed, not read from the API directly** -- `Menu` exposes
only the popup's overall bounds, not per-row positions (confirmed via `javap` against
`Menu.class`; RuneLite's context menu is a custom-rendered overlay, not built from widgets with
their own queryable bounds). Each entry's row is computed as the popup's `x`/`y` plus a fixed
per-row height offset times the entry's index. **The exact row-height pixel constant is not
verified against a live client** -- flagged the same way this codebase already flags this class of
uncertainty (e.g. `SafespotMarker`'s insertion-position note, `banker.py`'s Fill/Empty ambiguity)
for confirmation during live testing rather than asserted as fact here.

## How the two combine

`right_click_entity` (i.e. `click_entity(..., button="right")`) or `right_click_human` opens a
menu -> poll `GET /menu` -> find the entry whose `option` (and/or `target`) matches what's wanted
-> `click_human` (always a left-click -- selecting a context-menu row is a left-click regardless
of how the menu was opened) at that entry's computed `screenX`/`screenY`. This is the full loop
needed to trigger and verify a specific menu option -- e.g. confirming "Mark Safespot" appears and
correctly updates config -- without a human or screen vision.

## Out of scope

- Submenus (e.g. a `MenuEntry` whose own `createSubMenu()`/`getSubMenu()` nests further options,
  as `GroundMarkerPlugin`'s "Color" entry does) -- `GET /menu` reports only the top-level entries
  currently open. Reading into a nested submenu is a natural follow-up if a future feature needs
  it, not required for verifying "Mark Safespot" (a single-level entry).
- Any change to `MenuPriority`'s existing force-left-click-slot behavior -- this is a fully
  separate mechanism (read-and-choose vs. force-reorder-for-a-plain-click) serving a different
  purpose, not a replacement for it.
