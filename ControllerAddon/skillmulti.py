"""The skillmulti make-menu (widget group 270), shared by every routine that drives it.

Extracted from routines/fletching.py 2026-09-08, where every function below was live-verified.
It is shared because Vale Totems drives the SAME widget group for a different prompt: the
carve menu and the fletch menu are both group 270 and can only be told apart by the title.
"""
import re

import controller

GROUP = 270                # cache: skillmulti.if3
FIRST_ITEM_CHILD = 15      # children 15..32 are the item buttons `a`..`r`
LAST_ITEM_CHILD = 32
TITLE_CHILD = 5            # runelite-api gameval: Skillmulti.TITLE == 270.5

# The two prompts this group serves, lower-cased for comparison. USER 2026-09-08.
TITLE_CARVE = "what animal would you like to carve?"
TITLE_MAKE = "what would you like to make?"

_TAG_RE = re.compile(r"<[^>]*>")


def strip_tags(text):
    """Drop RuneScape colour/format tags. MEASURED live 2026-09-08: the skillmulti buttons
    carry their label as `<col=ff9040>Maple longbow</col>`, so a raw comparison never matches."""
    return _TAG_RE.sub("", text or "")


def menu_label(product):
    """The name the MAKE MENU uses for `product`, which is not the name the inventory uses.

    MEASURED live 2026-09-08 by dumping group 270 while the menu was open: the five maple
    options are `60 arrow shafts`, `Maple shortbow`, `Maple longbow`, `Crossbow stock`,
    `Maple shield`. The menu names the FINISHED item; the inventory receives the unstrung one
    (`Maple longbow (u)`). Matching the inventory name against the menu could therefore never
    succeed -- which is exactly what the first live run showed, falling back to the number key
    on every batch.

    Config keeps the inventory name because that is what actually has to be counted to confirm
    the batch; the menu label is derived from it by dropping the unstrung suffix."""
    label = strip_tags(product).strip()
    for suffix in (" (u)", " (unstrung)"):
        if label.lower().endswith(suffix):
            label = label[: -len(suffix)]
            break
    return label.lower()


def item_widget(product):
    """The skillmulti button for `product`, matched on name+text the way the Gauntlet's
    _bowl_widget and giants_foundry's crucible fill already match this same group -- a widget
    carrying the item name in `name` with an empty `text` (or the reverse) still matches.

    Matching by NAME rather than trusting the option number is the point: the option list is
    per-log (a plain log offers shafts / shortbow / longbow / stock) and the human's "3" is
    only correct for the product they happened to pick."""
    want = menu_label(product)
    if not want:
        return None
    in_range, anywhere = None, None
    for w in controller.get_widgets_dump():
        if w.get("group") != GROUP or w.get("screenX") is None:
            continue
        label = strip_tags("{0} {1}".format(w.get("name") or "", w.get("text") or "")).lower()
        if want not in label:
            continue
        child = w.get("child")
        if child is not None and FIRST_ITEM_CHILD <= child <= LAST_ITEM_CHILD:
            in_range = in_range or w
        else:
            anywhere = anywhere or w
    # Prefer a real item button, but do not LOSE a match just because the dump numbered it
    # somewhere unexpected -- the child range is documented by the cache and confirmed by
    # giants_foundry's 270.19, yet it has never been checked live for THIS menu, and falling
    # through to the number key would silently pick a row by position instead of by name.
    # (routines/corrupted_gauntlet.py's _bowl_widget matches this group with no range filter
    # at all and works; the range here is a preference, not a gate.)
    return in_range or anywhere


def option_number(widget):
    """Which number key selects `widget`, or None if it cannot be derived.

    The cache names the item buttons `a`..`r` at children 15..32, so option N is child 14+N --
    CONFIRMED live 2026-09-08 against the real menu, where `Maple longbow` (the human's option
    3) sat at child 17. Deriving the key from the matched widget is what makes the keypress
    safe: the number stays tied to the product's NAME instead of to a position guessed in
    config, so a log type with a different option list cannot silently make the wrong thing.

    Returns None past 9 (the digit row cannot address those) or for a child outside the button
    range, and the caller clicks the widget instead."""
    if widget is None:
        return None
    child = widget.get("child")
    if child is None or not (FIRST_ITEM_CHILD <= child <= LAST_ITEM_CHILD):
        return None
    option = child - FIRST_ITEM_CHILD + 1
    return option if 1 <= option <= 9 else None


def is_open():
    """Any group-270 widget on screen. Used only to tell "the menu never opened" (a use-on that
    did not land) from "the menu opened but this product is not on it" (a wrong config) -- two
    failures that need different messages."""
    return any(w.get("group") == GROUP and w.get("screenX") is not None
               for w in controller.get_widgets_dump())


def prompt_title() -> str:
    """The make-menu's title text, lower-cased and tag-stripped; "" when it cannot be read.

    Empty means "cannot tell", never "no menu" -- an unavailable widget must not be allowed to
    look like a confirmed prompt, because the caller's next act is a keypress."""
    widget = controller.get_widget(GROUP, TITLE_CHILD)
    if not widget:
        return ""
    return strip_tags(widget.get("text") or "").strip().lower()


def showing(title: str) -> bool:
    """Whether the menu currently on screen is the one titled `title`.

    THE GATE FOR EVERY KEYPRESS. Carve and fletch are the same widget group, so pressing a
    number without checking this can answer the wrong prompt -- carving an animal into a totem
    when we meant to fletch a bow, or the reverse."""
    return prompt_title() == title
