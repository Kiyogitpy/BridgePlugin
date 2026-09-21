# stronghold.py -- the Stronghold of Security's question gates.
#
# FOUND LIVE 2026-09-21. Aya assigned minotaurs, whose site is the Vault of War, and the journey
# died 16 tiles short: `travel._open_door` clicked the Gate of War at (1859,5238) seven times over
# two ticks and it never swung, because this gate does not swing -- it TALKS. Clicking "Open"
# opens a dialogue ("Greetings Adventurer. This place is kept safe by the spirits within the
# doors..."), then asks an account-security question, and the gate opens only once the question is
# answered correctly. Our whole door model is "click it and watch the blocked edge clear", so the
# leg failed three times and the task stopped with a route it could never walk.
#
# The wiki's own changelog says the gates STOP asking once the Stronghold has been completed
# (3 March 2016). This account has not completed it, so every gate asks, and there are ~60 of them
# in the Vault of War scene -- the maze is built out of them.
#
# THE QUESTION/ANSWER FACTS ARE PORTED, NOT VENDORED, from the Plugin Hub plugin
# `stronghold-of-security` (severikupari1, commit 8058ca0, BSD-2-Clause), file SecurityAnswers.java
# -- the same "port its facts, do not vendor it" rule this repo applied to Slayer Best in Bank.
# Nothing here is a copy of its mechanism, and one thing is deliberately NOT copied: upstream keys
# a HashMap on the question text, and THREE of its entries key on a bare fragment ("respond?"
# twice, with DIFFERENT answers), so the later silently overwrites the earlier and one of those
# questions can never be answered. Matching here resolves that by intersecting the candidate
# answers with the options actually on screen, which is unambiguous even when the question text
# is not.
#
# A wrong answer is not destructive -- the gate simply asks again -- but it is not guessed at
# either: an unrecognised question is reported with its options so it can be added, rather than
# answered at random.

import time

import controller
import debug_log
import dialogue

LOG = "stronghold"

#: The four level gates. Matched case-insensitively, and only as a HINT: `is_gate_dialogue` also
#: accepts anything carrying the question marker, so a name we have wrong cannot hide a gate.
GATE_NAMES = ("gate of war", "rickety door", "oozing barrier", "portal of death")

#: The phrase every question opens with, and the greeting's own giveaway.
QUESTION_MARKER = "answer me this"
GREETING_MARKER = "spirits within the doors"

#: (question, answer). Ported from SecurityAnswers.java (see the header). The `<br>` line breaks
#: are left exactly as the client sends them; `_norm` removes them, so the table stays diffable
#: against upstream.
PAIRS = (
    ("To pass you must answer me this: Can I leave my<br>account logged in while I'm out of the room?",
     "No."),
    ("To pass you must answer me this: How do I remove a<br>hijacker from my account?",
     "Use the Account Recovery system."),
    ("To pass you must answer me this: What do you do if<br>someone asks you for your password or bank PIN to<br>make you a player moderator?",
     "Don't give them the information and send an 'Abuse report'."),
    ("To pass you must answer me this: My friend asks me<br>for my password so that he can do a difficult quest for<br>me. Do I give it to him?",
     "Don't give them my password."),
    ("To pass you must answer me this: Who can I give my<br>password to?",
     "Nobody."),
    ("To pass you must answer me this: How do I set up<br>two-factor authentication for my Old School RuneScape<br>account?",
     "Through account settings on oldschool.runescape.com."),
    ("To pass you must answer me this: Hey adventurer!<br>You've been randomly selected for a prize of 1 year of<br>free membership! I'm just going to need some of your<br>account details so I can put it on your account!",
     "No way! I'm reporting you to Jagex!"),
    ("To pass you must answer me this: What is an example<br>of a good bank PIN?",
     "The birthday of a famous person or event."),
    ("To pass you must answer me this: You're watching a<br>stream by someone claiming to be Jagex offering double<br>XP. What do you do?",
     "Report the stream as a scam. Real Jagex streams have a 'verified' mark."),
    ("To pass you must answer me this: What should you do<br>if your real-life friend asks for your password so he<br>can check your stats?",
     "Don't give out your password to anyone. Not even close friends."),
    ("To pass you must answer me this: A player tells you to<br>search for a video online, click the link in the description<br>and comment on the forum post to win a cash prize.<br>What do you do?",
     "Report the player for phishing."),
    ("To pass you must answer me this: Will Jagex prevent<br>me from saying my PIN in game?",
     "No."),
    ("To pass you must answer me this: You have been<br>offered an opportunity to check out a free giveaway or<br>double XP signup via email or in game chat. What<br>should I do?",
     "Report the incident and do not click any links."),
    ("that sound?",
     "No way! You'll just take my gold for your own! Reported!"),
    ("To pass you must answer me this: A website claims that<br>they can make me a player moderator. What should I<br>do?",
     "Nothing, it's a fake."),
    ("To pass you must answer me this: How do I set a<br>bank PIN?",
     "Talk to any banker."),
    ("react?",
     "Don't share your information and report the player."),
    ("To pass you must answer me this: You are part way<br>through the Stronghold of Security when you have to<br>answer another question. After you answer the question,<br>you should...",
     "Read the text and follow the advice given."),
    ("To pass you must answer me this: What should I do if<br>I receive an email asking me to verify my identity or<br>Account details due to suspicious activity?",
     "Delete it - it's a fake!"),
    ("To pass you must answer me this: What do you do if<br>someone asks you for your password or bank PIN to<br>make you a member for free?",
     "Don't tell them anything and click the 'Report Abuse' button."),
    ("To pass you must answer me this: What do I do if a<br>moderator asks me for my account details?",
     "Politely tell them no, then use the 'Report Abuse' button."),
    ("To pass you must answer me this: Is it okay to buy an<br>Old School RuneScape account?",
     "No, you should never buy an account."),
    ("To pass you must answer me this: Who is it ok to<br>share my account with?",
     "Nobody."),
    ("To pass you must answer me this: You have been<br>offered a free giveaway or double XP invitation via<br>social media or a livestream. What should you do?",
     "Report the incident and do not click any links."),
    ("To pass you must answer me this: Where is it safe to<br>use my Old School RuneScape password?",
     "Only on the Old School RuneScape website."),
    ("To pass you must answer me this: What should you do<br>if another player messages you recommending a website<br>to purchase items and/or gold?",
     "Do not visit the website and report the player who messaged you."),
    ("To pass you must answer me this: Whose responsibility<br>is it to keep your account secure?",
     "Me."),
    ("To pass you must answer me this: Is it safe to get<br>someone to level your account?",
     "No, you should never allow anyone to use your account."),
    ("To pass you must answer me this: What is the best<br>security step you can take to keep your registered<br>email secure?",
     "Set up two-factor authentication with my email provider."),
    ("To pass you must answer me this: What is the best<br>way to secure your account?",
     "Two-factor authentication on your account and your registered email."),
    ("To pass you must answer me this: What do I do if my<br>account is compromised?",
     "Secure my device and reset my password."),
    ("respond?",
     "Decline the offer and report that player."),
    ("To pass you must answer me this: What do I do if I<br>think I have a keylogger or virus?",
     "Virus scan my device then change my password."),
    ("To pass you must answer me this: A player says that<br>Jagex prevents you from saying your password<br>backwards in game. What do you do?",
     "Don't type in my password backwards and report the player."),
    ("To pass you must answer me this: Which of these is an<br>important characteristic of a secure password?",
     "It's never reused on other websites or accounts."),
    ("lose out! What do you say?",
     "Nope, you're tricking me into going somewhere dangerous."),
    ("respond?",
     "Nothing, it's a fake."),
    ("To pass you must answer me this: A player trades you<br>some valuable items, provides you a bond, then asks if<br>you want to share your account so he can help you<br>make progress. How do you respond?",
     "Decline the offer and report that player."),
    ("To pass you must answer me this: Who is it okay to<br>share my account with?",
     "Nobody."),
)

#: A fragment key shorter than this is too weak to match a question tail on. "respond?" is 8.
MIN_FRAGMENT = 8

CONTINUE_KEY = "SPACE"
POLL_S = 0.2
STAGE_S = 0.6          # a keypress -> the next dialogue stage being readable
HANDLE_BUDGET_S = 25.0  # greeting + question + the answer landing, for ONE gate


def _norm(text: str) -> str:
    """Comparable form: `<br>` gone, whitespace collapsed, lowercased."""
    return " ".join((text or "").replace("<br>", " ").split()).lower()


_ANSWERS = tuple((_norm(q), _norm(a)) for q, a in PAIRS)
ALL_ANSWERS = frozenset(a for _, a in _ANSWERS)


def candidates(question: str) -> set:
    """Every answer the table offers for `question`, normalised.

    A fragment key matches as a TAIL of the question, which is how the truncated upstream keys
    were meant to work; several can match at once (two questions end "respond?"), and that
    ambiguity is resolved against the options on screen rather than here.
    """
    q = _norm(question)
    if not q:
        return set()
    out = set()
    for known, answer in _ANSWERS:
        if q == known or (len(known) >= MIN_FRAGMENT and q.endswith(known)):
            out.add(answer)
    return out


def answer_index(question: str, opts):
    """Which option answers `question`. Returns (index, how) or (None, why).

    Two passes, both requiring EXACTLY ONE match -- never a best guess:
      1. the answers this question is known to have, intersected with the options;
      2. failing that, the options that are a known correct answer to ANY question, which
         rescues a re-worded question whose answer text has not changed.
    """
    normalised = [_norm(o) for o in (opts or [])]
    if not normalised:
        return None, "the menu had no options"
    wanted = candidates(question)
    hits = [i for i, o in enumerate(normalised) if o in wanted]
    if len(hits) == 1:
        return hits[0], "the question"
    if len(hits) > 1:
        return None, "the question matched more than one option"
    hits = [i for i, o in enumerate(normalised) if o in ALL_ANSWERS]
    if len(hits) == 1:
        return hits[0], "a known answer (the question text was not recognised)"
    if len(hits) > 1:
        return None, "several options are known answers to other questions"
    return None, "no option is a known answer"


def _title(d) -> str:
    """The options menu's own child 0, which for these gates carries the QUESTION.

    dialogue.options() drops it on purpose (number keys address the real options), so it has to
    be read straight off the payload -- the same child-0-is-the-title fact giants_foundry
    measured.
    """
    raw = (d or {}).get("options") or []
    if not raw:
        return ""
    first = raw[0]
    return first if isinstance(first, str) else (first.get("text") or "")


def is_gate_dialogue(d) -> bool:
    """Is this an open Stronghold gate dialogue? Name OR marker text, so a gate name we have
    wrong still cannot hide one."""
    if not dialogue.is_open(d):
        return False
    if _norm(d.get("npcName")) in GATE_NAMES:
        return True
    blob = " ".join(_norm(d.get(k)) for k in ("npcText", "playerText")) + " " + _norm(_title(d))
    return QUESTION_MARKER in blob or GREETING_MARKER in blob


def at_gate() -> bool:
    return is_gate_dialogue(dialogue.read())


def handle(ctx, budget_s: float = HANDLE_BUDGET_S) -> bool:
    """Talk one gate open. True when a question was answered (or the dialogue is already gone).

    Continue stages are spaced through; the question stage is matched and its number key pressed.
    An unrecognised question is LOGGED WITH ITS OPTIONS and left alone -- a random press would
    teach the reader nothing and the gate would ask again anyway.
    """
    deadline = time.time() + budget_s
    answered = False
    last_question = ""
    while time.time() < deadline:
        d = dialogue.read()
        if not is_gate_dialogue(d):
            return answered
        opts = dialogue.options(d)
        if not opts:
            # A "click here to continue" stage: the greeting, or the gate's reply to our answer.
            text = d.get("npcText") or d.get("playerText") or ""
            if _norm(text):
                last_question = text
            controller.key_tap(ctx.ser, CONTINUE_KEY)
            time.sleep(STAGE_S)
            continue
        # The question is usually the menu's own title; fall back to the text that preceded it.
        for source in (_title(d), last_question):
            index, how = answer_index(source, opts)
            if index is not None:
                debug_log.log(LOG, f"gate question answered by {how}: {opts[index]!r}")
                controller.key_tap(ctx.ser, str(index + 1))
                answered = True
                time.sleep(STAGE_S)
                break
        else:
            debug_log.log(LOG, "[!] a Stronghold gate asked a question this table does not know "
                               f"-- not guessing. Question: {_title(d) or last_question!r}. "
                               f"Options: {opts}. Add it to stronghold.PAIRS")
            return answered
    debug_log.log(LOG, f"the gate dialogue was still up after {budget_s:.0f}s")
    return answered
