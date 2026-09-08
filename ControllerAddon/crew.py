"""Crew assignment through the Sailing side panel (interface 937) and dialogue group 219.

**NOT LIVE-VERIFIED.** Every id below was CAPTURED live on 2026-09-07 by a read-only widget
watcher, but no code has ever driven this flow, and the ORDER of the flow is inferred rather
than measured. USER 2026-09-08 chose to build it blind rather than probe first.

What was actually captured:

    937.33  Facilities tab          937.34  Stats tab         937.35  Crewmates tab
    937.6   the open tab's title    937.25  "Assign"          937.27  status text
    219     the ordinary dialogue-option group, showing
              "Crewmate Assignation: Ex-Captain Siad (H3 P2 D3)"
              1. Helm and sails (H3).
              2. Repairs (D1).
              3. Other facility.
              4. Current - Idle.

The inference: pick a crewmate, answer 219 with "Other facility.", then pick the facility. What
is NOT known is whether the facility pick is a further dialogue, a click on the Facilities grid,
or the FACILITIES_CONTENT_NPC_TARGETLAYER the interface also exposes.

BECAUSE it is unverified, EVERY function here fails safe: it returns False rather than raising,
never leaves a dialogue open (ESC on any path it does not understand), and the caller treats a
False as "carry on without the crew swap". A blind guess must never be able to strand the run or
pull a crewmate off a hook it cannot put them back on.

Group 219's own trap, already MEASURED for giants_foundry: the m8aq dialogue reader returns the
menu TITLE as options[0], so real options are numbered from 1. _dialogue_options strips it.
"""

import controller
import debug_log

SIDEPANEL_GROUP = 937
DIALOGUE_GROUP = 219

FACILITIES_TAB = 33
STATS_TAB = 34
CREW_TAB = 35
TAB_TITLE = 6
ASSIGN_CHILD = 25

SAILING_TAB_KEY = "q"        # CONFIRMED live 2026-08-01 (see sailing.SAILING_OPTIONS_TAB_KEY)
TAB_TRIES = 3
STEP_DELAY = (0.25, 0.45)

# The 219 option we want when putting a crewmate on a salvaging hook. Matched case-insensitively
# as a substring so the trailing "(H3)"-style annotations do not have to be reproduced.
OTHER_FACILITY_OPTION = "other facility"
REST_OPTION = "take a rest"


def _dialogue_open() -> bool:
    widget = controller.get_widget(DIALOGUE_GROUP, 1)
    return bool(widget.get("found")) and bool(widget.get("clickable"))


def dialogue_options():
    """The REAL options of a group-219 dialogue, title stripped.

    giants_foundry MEASURED that the reader returns the menu title as options[0] and blank
    trailing children -- number keys count real options from 1, so a naive read presses the wrong
    key. Returns [] when no dialogue is open."""
    rows = controller.get_widget_children(DIALOGUE_GROUP, 1) or []
    texts = [(r.get("text") or "").strip() for r in rows]
    texts = [t for t in texts if t]
    return texts[1:] if len(texts) > 1 else []


def _close_dialogue(ctx) -> None:
    if _dialogue_open():
        controller.key_tap(ctx.ser, "ESC")
        controller.human_delay(*STEP_DELAY)


def open_panel(ctx) -> bool:
    """Tap Q until the Sailing side panel is up. Same verified-and-retried shape sailing.stop_boat
    uses for the same key -- a single unconfirmed tap is how that function used to report "sail
    toggle not found" with the sails still up."""
    def _open():
        # NOT .get("found") -- MEASURED live 2026-09-08: every 937 child reports found=True while
        # the panel is CLOSED (found means "this id exists", per WidgetProvider's own deliberate
        # distinction between found:false = wrong id and clickable:false = interface closed). The
        # first version checked found, so ensure_ui_state concluded the panel was already open and
        # never tapped Q at all -- the failure surfaced downstream as "side panel tab 35 is not
        # clickable".
        widget = controller.get_widget(SIDEPANEL_GROUP, TAB_TITLE)
        return bool(widget.get("clickable")) and not widget.get("hidden", False)

    return controller.ensure_ui_state(
        lambda: controller.key_tap(ctx.ser, SAILING_TAB_KEY), _open, tries=TAB_TRIES)


def open_tab(ctx, child: int) -> bool:
    """Click one of the side panel's tabs (Facilities / Stats / Crewmates)."""
    if not open_panel(ctx):
        debug_log.log("crew", "the Sailing side panel would not open")
        return False
    widget = controller.get_widget(SIDEPANEL_GROUP, child)
    if not widget.get("clickable") or widget.get("screenX") is None:
        debug_log.log("crew", f"side panel tab {child} is not clickable")
        return False
    controller.click_entity(ctx.ser, widget, offset=(0, 0), check_ui_blockers=False,
                            simulate_mistakes=False)
    controller.human_delay(*STEP_DELAY)
    return True


def crew_rows():
    """Every populated row of the Crewmates tab (937.25's container). Empty when the tab is not
    open -- "cannot tell", never "no crew"."""
    return controller.get_widget_children(SIDEPANEL_GROUP, ASSIGN_CHILD) or []


def _answer(ctx, needle: str) -> bool:
    """Press the number key for the first 219 option containing `needle`."""
    options = dialogue_options()
    for index, text in enumerate(options, start=1):
        if needle in text.lower():
            controller.key_tap(ctx.ser, str(index))
            controller.human_delay(*STEP_DELAY)
            return True
    debug_log.log("crew", f"no '{needle}' option in the crew dialogue {options!r}")
    return False


def assign_to_hook(ctx) -> bool:
    """Put a crewmate on a salvaging hook so salvage keeps coming while the player sorts.

    Returns False on ANY step it cannot confirm, having closed whatever it opened. The caller
    carries on without the swap -- see this module's docstring for why that is the contract."""
    if not open_tab(ctx, CREW_TAB):
        return False

    rows = crew_rows()
    if not rows:
        debug_log.log("crew", "no crew rows on the Crewmates tab -- skipping the hook assignment")
        return False

    row = rows[0]
    if row.get("screenX") is None or row.get("screenY") is None:
        debug_log.log("crew", "the first crew row has no screen position -- skipping")
        return False
    controller.click_entity(ctx.ser, row, offset=(0, 0), check_ui_blockers=False,
                            simulate_mistakes=False)
    controller.human_delay(*STEP_DELAY)

    if not _dialogue_open():
        debug_log.log("crew", "clicking a crewmate did not open the assignation dialogue")
        return False

    if not _answer(ctx, OTHER_FACILITY_OPTION):
        _close_dialogue(ctx)
        return False

    # What follows "Other facility." is the UNVERIFIED half: it may be a second dialogue listing
    # the facilities, or a click on the Facilities grid. Handle the dialogue case, and give up
    # cleanly otherwise rather than clicking blindly at the deck.
    if _dialogue_open():
        if _answer(ctx, "hook"):
            debug_log.log("crew", "assigned a crewmate to a salvaging hook")
            return True
        _close_dialogue(ctx)
        return False

    debug_log.log("crew", "after 'Other facility.' there was no dialogue to pick the hook from -- "
                          "the facility pick is not a dialogue; leaving the crew alone")
    return False
