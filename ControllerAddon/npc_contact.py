# npc_contact.py -- reach a slayer master through the Lunar spell NPC Contact, and take a task.
# Added 2026-09-19 (slayer spec §9). Every id and line below was MEASURED from the user's own
# recordings that day (docs/slayer-sites/recordings-2026-09-19.md), not reasoned:
#
#   spell            widget 218.113, option "Cast"; anim 4413 / spotanim 728 follow in 0.3s
#   picker           interface group 75, ~4.4s after the cast. A row's TEXT and NAME are EMPTY --
#                    the only thing naming a row is its ACTION, "Contact Aya". So rows come from
#                    /widgets/dump (which carries actions), never /widget/children (which does not),
#                    and never from a slot number (Aya 18, Kuradal 33 on the day; they move).
#   dialogue         greeting -> options ("I need another assignment.") -> a strong account gets
#                    Aya's "are you sure you don't want Vannaka" -> options -> "Your new task is
#                    to kill 15 Bears."  The task is assigned ON THE TICK that line appears
#                    (varbit 4067, varp 394, varp 395 together), before the dialogue ends.
#
# Two rules everything here obeys. (1) slayer_state.assert_may_contact runs before ANY input:
# asking Aya to replace an active task resets the streak. (2) An options menu with none of the
# expected options is ESC + failure -- never a guessed number, never Space. The one dialogue in
# the game this module must never accept is a replace-my-task confirmation, so an unknown menu is
# a bug to stop on, and its text is kept in ctx.state.data for the log.
#
# UNVERIFIED LIVE as of 2026-09-19: the option text that declines Vannaka was not captured (the
# recorder logs keys, not menus); DECLINE_NEEDLES is the wiki's wording and the first live run
# will confirm or correct it -- safely, by rule (2).

import time

import controller
import debug_log
import dialogue
import slayer_state

LOG = "npc_contact"
SPELL = (218, 113)
PICKER_GROUP = 75
MAGIC_TAB_KEY = "F6"
MAGIC_TAB_INDEX = 6

PICKER_BUDGET_S = 9.0        # cast -> picker readable. MEASURED 4.4s; the cast animation is fixed.
CONTACT_BUDGET_S = 5.0       # row click -> the master's greeting. MEASURED 0.5-0.7s.
DIALOGUE_BUDGET_S = 25.0     # greeting -> task in /slayer. MEASURED 18s at human pace with Vannaka.
POLL_S = 0.2
MAX_STEPS = 14               # keypresses in one dialogue walk; the recorded one takes 5

MASTERS = {
    "AYA": {"names": ("aya", "turael"), "magic": 67},
    "KURADAL": {"names": ("kuradal", "duradel"), "magic": 67},
    "KONAR": {"names": ("konar",), "magic": 67},
}

ASSIGN_NEEDLES = ("another assignment", "need a task", "new assignment")
DECLINE_NEEDLES = ("take a task from you", "no that's okay", "from you")   # Aya's Vannaka question
ASSIGNED_TEXT = "new task is to kill"
STILL_HUNTING_TEXT = "still hunting"

RUNES_PER_CAST = {"astral rune": 1, "cosmic rune": 1, "air rune": 2}
AIR_STAVES = ("staff of air", "air battlestaff", "mystic air staff", "smoke battlestaff", "mist battlestaff",
              "dust battlestaff", "mystic smoke staff", "mystic mist staff", "mystic dust staff")


def _master(master) -> dict:
    return MASTERS.get(slayer_state.canonical_master(master)) or {"names": ((master or "").lower(),), "magic": 67}


def find_master_row(dump, master):
    """The picker row whose action is 'Contact <one of the master's names>', or None."""
    wanted = {f"contact {name}" for name in _master(master)["names"]}
    for entry in dump or ():
        if entry.get("group") != PICKER_GROUP or entry.get("screenX") is None:
            continue
        if any((action or "").strip().lower() in wanted for action in entry.get("actions") or ()):
            return entry
    return None


def _rune_counts() -> dict:
    counts = {}
    for item in controller.get_inventory() or ():
        name = (item.get("name") or "").lower()
        counts[name] = counts.get(name, 0) + int(item.get("quantity") or 1)
    pouch = controller.get_rune_pouch() or {}
    for rune in pouch.get("runes") or ():
        name = (rune.get("name") or "").lower()
        if name:
            counts[name] = counts.get(name, 0) + int(rune.get("quantity") or 0)
    return counts


def missing_requirements(master):
    """A sentence naming the first thing that stops the cast, or None. An UNREADABLE value never
    blocks (None = cannot verify); the spell widget not being clickable is the backstop."""
    magic = controller.get_api("player", "magic") or {}
    book = magic.get("spellbook")
    if book is not None and str(book).upper() != "LUNAR":
        return f"NPC Contact needs the Lunar spellbook (the account is on {book})"
    level = ((controller.get_skills() or {}).get("Magic") or {}).get("level")
    need = _master(master)["magic"]
    if level is not None and level < need:
        return f"NPC Contact needs {need} Magic (have {level})"
    counts = _rune_counts()
    worn = [(item.get("name") or "").lower() for item in controller.get_equipment() or ()]
    has_air_staff = any(staff in name for name in worn for staff in AIR_STAVES)
    for rune, per_cast in RUNES_PER_CAST.items():
        if rune == "air rune" and has_air_staff:
            continue
        if counts.get(rune, 0) < per_cast:
            return f"NPC Contact needs {per_cast} {rune}(s) per cast (have {counts.get(rune, 0)})"
    return None


def open_picker(ctx) -> bool:
    """Magic tab -> cast -> the picker is readable. False (and no cast) when the spell is not
    clickable: wrong spellbook, filtered spell, or an interface covering the tab."""
    if controller.get_active_tab() != MAGIC_TAB_INDEX:
        controller.key_tap(ctx.ser, MAGIC_TAB_KEY)
        controller.human_delay(0.3, 0.5)
    spell = controller.get_widget(*SPELL)
    if not spell or not spell.get("clickable"):
        debug_log.log(LOG, f"spell widget {SPELL[0]}.{SPELL[1]} is not clickable -- Lunar spellbook? spell filtered?")
        return False
    debug_log.log(LOG, "casting NPC Contact")
    if not controller.click_entity(ctx.ser, spell, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False):
        return False
    ctx.state.data["npc_contact_casts"] = ctx.state.data.get("npc_contact_casts", 0) + 1
    deadline = time.time() + PICKER_BUDGET_S
    while time.time() < deadline:
        if any(entry.get("group") == PICKER_GROUP for entry in controller.get_widgets_dump() or ()):
            return True
        time.sleep(POLL_S)
    debug_log.log(LOG, f"the contact picker (interface {PICKER_GROUP}) never appeared within {PICKER_BUDGET_S}s")
    return False


def contact(ctx, master, tasks_cfg) -> bool:
    """Cast and pick `master`. True once THEIR dialogue is open."""
    slayer_state.assert_may_contact(master, slayer_state.from_bridge(controller.get_slayer_task()), tasks_cfg)
    if not open_picker(ctx):
        return False
    row = find_master_row(controller.get_widgets_dump(), master)
    if row is None:
        debug_log.log(LOG, f"no 'Contact {master}' row in the picker -- not unlocked, or scrolled out of view")
        dialogue.close(ctx)
        return False
    action = (row.get("actions") or [""])[0]
    clicked = controller.click_entity(ctx.ser, row, offset=(0, 0), check_ui_blockers=False,
                                      simulate_mistakes=False, expected_action=action)
    if not clicked:
        debug_log.log(LOG, f"the click on '{action}' was withheld or failed")
        dialogue.close(ctx)
        return False
    names = _master(master)["names"]
    deadline = time.time() + CONTACT_BUDGET_S
    while time.time() < deadline:
        speaker = (dialogue.read().get("npcName") or "").strip().lower()
        if speaker in names:
            return True
        time.sleep(POLL_S)
    debug_log.log(LOG, f"{master}'s dialogue did not open within {CONTACT_BUDGET_S}s")
    return False


def _assigned():
    state = slayer_state.from_bridge(controller.get_slayer_task())
    return state if state is not None and state.has_task else None


def walk_to_assignment(ctx):
    """Drive the open master dialogue until /slayer shows a task. Returns the TaskState, or None.

    Space through text; at a menu choose by TEXT (assignment first, then Aya's Vannaka decline);
    a menu with neither is ESC + None with its options kept in ctx.state.data. Success is read
    from /slayer, so the tail of the dialogue (tips / 'Okay, great!') is simply closed."""
    deadline = time.time() + DIALOGUE_BUDGET_S
    steps = 0
    while time.time() < deadline and steps < MAX_STEPS:
        state = _assigned()
        if state is not None:
            dialogue.close(ctx)
            return state
        d = dialogue.read()
        opts = dialogue.options(d)
        if opts:
            hit = None
            for needle in ASSIGN_NEEDLES + DECLINE_NEEDLES:
                hit = dialogue.find_option(opts, needle)
                if hit is not None:
                    break
            if hit is None:
                ctx.state.data["npc_contact_unknown_menu"] = " | ".join(opts)
                debug_log.log(LOG, f"UNEXPECTED menu, escaping without choosing: {opts}")
                dialogue.close(ctx)
                return None
            controller.key_tap(ctx.ser, str(hit + 1))
        elif STILL_HUNTING_TEXT in (d.get("npcText") or "").lower():
            debug_log.log(LOG, f"the master says a task is still active: {d.get('npcText')!r}")
            dialogue.close(ctx)
            return None
        elif dialogue.is_open(d):
            controller.key_tap(ctx.ser, "SPACE")
        else:
            time.sleep(POLL_S)
            continue
        steps += 1
        controller.human_delay(0.5, 0.9)
    state = _assigned()
    dialogue.close(ctx)
    if state is None:
        debug_log.log(LOG, f"no task registered after {steps} dialogue steps")
    return state


def get_task(ctx, master, tasks_cfg):
    """One full attempt: guard, cast, pick, take the assignment. TaskState on success, else None.
    Raises slayer_state.StreakGuardError instead of contacting a filler master with a task active."""
    slayer_state.assert_may_contact(master, slayer_state.from_bridge(controller.get_slayer_task()), tasks_cfg)
    if not contact(ctx, master, tasks_cfg):
        return None
    return walk_to_assignment(ctx)
