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
DECLINE_NEEDLES = ("take a task from you", "no that's okay")   # Aya's Vannaka question. Deliberately
# NARROW (task-2 review): a bare "from you" would also match "Yes, I'd like a new task from you".
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
        # InventoryProvider emits "qty" (task-2 review: reading "quantity" counted every stack as 1).
        counts[name] = counts.get(name, 0) + int(item.get("qty") or item.get("quantity") or 1)
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
    answered = None
    while time.time() < deadline and steps < MAX_STEPS:
        state = _assigned()
        if state is not None:
            dialogue.close(ctx)
            return state
        d = dialogue.read()
        opts = dialogue.options(d)
        if opts and opts == answered:
            # The menu we already answered is still on screen (a slow tick). Pressing its number
            # again would land on whatever menu comes NEXT -- wait for it to change (task-2 review,
            # same rule as dialogue.choose's `seen`).
            time.sleep(POLL_S)
            continue
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
            answered = opts
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
    Raises slayer_state.StreakGuardError instead of contacting a filler master with a task active.

    NO master is asked for a task while one is active (task-2 review): the milestone master
    passes assert_may_contact -- she is the cancel path -- but walking her ASSIGNMENT dialogue
    with a task already held has only bad outcomes."""
    state = slayer_state.from_bridge(controller.get_slayer_task())
    slayer_state.assert_may_contact(master, state, tasks_cfg)
    if state.has_task:
        debug_log.log(LOG, f"not asking {master} for a task: {state.task or state.task_id} is still active")
        return None
    if not contact(ctx, master, tasks_cfg):
        return None
    return walk_to_assignment(ctx)


# ------------------------------------------------------------------ cancelling a task (30 points)
#
# MEASURED 2026-09-19: rewards = interface 426, the Tasks tab = 426.14 dynamic child 6. USER the
# same day: "You can cancel from the spell too". NOT measured: the dialogue option's wording, the
# cancel button's widget, the confirmation step (the account had 9 points). So the button is
# found by the WORD "cancel" inside group 426 and nothing is clicked unless exactly ONE control
# matches. Success is /slayer reading no task -- never the click. UNVERIFIED LIVE.

REWARDS_GROUP = 426
TASKS_TAB = (426, 14, 6)
REWARD_NEEDLES = ("reward",)
CONFIRM_NEEDLES = ("yes", "confirm")     # NOT "cancel": "No, don't cancel" would match it
REWARDS_BUDGET_S = 6.0
CANCEL_BUDGET_S = 6.0


def find_cancel_control(dump):
    """The ONE control in the rewards interface whose text or action says 'cancel', else None."""
    hits = []
    for entry in dump or ():
        if entry.get("group") != REWARDS_GROUP or entry.get("screenX") is None:
            continue
        words = [entry.get("text") or ""] + list(entry.get("actions") or ())
        if any("cancel" in (w or "").lower() for w in words):
            hits.append(entry)
    return hits[0] if len(hits) == 1 else None


def _rewards_open() -> bool:
    return any(e.get("group") == REWARDS_GROUP for e in controller.get_widgets_dump() or ())


def open_rewards(ctx, master, tasks_cfg) -> bool:
    if not contact(ctx, master, tasks_cfg):
        return False
    deadline = time.time() + REWARDS_BUDGET_S
    while time.time() < deadline:
        if _rewards_open():
            return True
        d = dialogue.read()
        opts = dialogue.options(d)
        if opts:
            hit = next((h for h in (dialogue.find_option(opts, n) for n in REWARD_NEEDLES) if h is not None), None)
            if hit is None:
                ctx.state.data["npc_contact_unknown_menu"] = " | ".join(opts)
                debug_log.log(LOG, f"no rewards option in {opts} -- escaping")
                dialogue.close(ctx)
                return False
            controller.key_tap(ctx.ser, str(hit + 1))
        elif dialogue.is_open(d):
            controller.key_tap(ctx.ser, "SPACE")
        controller.human_delay(0.5, 0.9)
    return _rewards_open()


def cancel_task(ctx, master, tasks_cfg) -> bool:
    """Cancel the active task for 30 points through `master`'s rewards screen. True only when
    /slayer then reads no task. Never through the filler master; never without the points."""
    state = slayer_state.from_bridge(controller.get_slayer_task())
    slayer_state.assert_may_contact(master, state, tasks_cfg)
    ok, why = slayer_state.can_skip(state.points, ctx.state.data.get("slayer_skips", 0), tasks_cfg)
    if not ok:
        debug_log.log(LOG, f"not cancelling: {why}")
        return False
    if not open_rewards(ctx, master, tasks_cfg):
        return False
    tab = controller.get_widget(*TASKS_TAB)
    if not tab or not tab.get("clickable"):
        debug_log.log(LOG, f"the Tasks tab {TASKS_TAB} is not clickable")
        dialogue.close(ctx)
        return False
    controller.click_entity(ctx.ser, tab, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    controller.human_delay(0.5, 0.9)
    button = find_cancel_control(controller.get_widgets_dump())
    if button is None:
        debug_log.log(LOG, "no single 'cancel' control in the rewards interface -- closing it untouched")
        dialogue.close(ctx)
        return False
    debug_log.log(LOG, f"cancelling {state.task or state.task_id} for {slayer_state.CANCEL_COST} points "
                       f"(widget {button.get('group')}.{button.get('child')}[{button.get('index')}])")
    controller.click_entity(ctx.ser, button, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    deadline = time.time() + CANCEL_BUDGET_S
    confirmed_once = False
    while time.time() < deadline:
        after = slayer_state.from_bridge(controller.get_slayer_task())
        if after is not None and not after.has_task:
            ctx.state.data["slayer_skips"] = ctx.state.data.get("slayer_skips", 0) + 1
            dialogue.close(ctx)
            return True
        opts = dialogue.options()
        if opts and not confirmed_once:
            hit = next((h for h in (dialogue.find_option(opts, n) for n in CONFIRM_NEEDLES) if h is not None), None)
            if hit is not None:
                controller.key_tap(ctx.ser, str(hit + 1))
                confirmed_once = True
        time.sleep(POLL_S)
    debug_log.log(LOG, "the task is still active after the cancel click")
    dialogue.close(ctx)
    return False
