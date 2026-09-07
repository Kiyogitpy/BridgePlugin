# recorder.py
#
# Records a HUMAN playing an activity by hand, so a routine can later be written by reading the
# log instead of guessing the activity's mechanics and live-debugging them into existence.
#
# Added 2026-08-23 (user request). Usage, from ControllerAddon/:
#
#     python -u recorder.py sunstone-mining
#
# ...then go play the activity normally. Ctrl+C stops it. Three files land in recordings/:
#
#     <stamp>_<label>.jsonl    every record, raw, exactly as the bridge emitted it
#     <stamp>_<label>.log      the readable narrative -- this is the one you read to build
#     <stamp>_<label>.summary  counts and inventories: object ids, anim ids, xp, varbits
#
# DELIBERATELY DOES NOT IMPORT controller.py. Only `settings` (for API_BASE) and `requests`. The
# Arduino serial port can be held by exactly one process at a time (see CLAUDE.md), and a recorder
# is something you want to run WHILE playing by hand -- possibly with a routine's own process
# alive. Importing controller would risk that contention for no benefit: this tool only ever
# issues HTTP GETs. It is safe to run alongside anything, same as mcp_debug_server.py.
#
# DPI scaling is likewise not applied, deliberately. controller.py scales the bridge's
# screenX/screenY fields into physical pixels because it has to put a real cursor there. Nothing
# here does: the recorder's canvas coordinates are canvas-relative (the game's own space), which is
# the coordinate system a routine's screen-position logic should be reasoned about in anyway.

import argparse
import datetime
import json
import os
import sys
import time
from collections import Counter, defaultdict

import requests

import settings

POLL_INTERVAL_S = 0.1
POLL_LIMIT = 4000
REQUEST_TIMEOUT_S = 5.0

# How long a record waits before being rendered into the narrative log. MUST exceed the Java side's
# outcome window (OUTCOME_WINDOW_TICKS = 5 ticks, ~3000ms) plus margin: an action is only rendered
# once every ambient record that could still be attributed to it has certainly arrived, otherwise
# its outcome block would be written half-empty and the missing lines silently orphaned.
RENDER_DELAY_MS = 4500

RECORDINGS_DIR = "recordings"

# Rendered inline in the narrative when they are NOT attributed to an action. Ticks and mouse
# samples are excluded -- they are the timeline's backbone and its highest-volume stream
# respectively, both kept in full in the .jsonl but far too noisy to read.
LOOSE_NARRATIVE_KINDS = {"chat", "xp", "anim", "spotanim", "gfx", "inv", "invstart", "key",
                         "gamestate", "interact", "objspawn", "objdespawn", "varbit",
                         "npcspawn", "npcdespawn", "npcchange", "walkdest",
                         "canvasexit", "canvasenter", "focus", "recorder"}

# Noise classification for varbits/varps. A variable is noise when it changes on a large FRACTION
# OF TICKS, not when it changes a lot in total -- a fixed count misjudges long recordings and short
# ones in opposite directions.
#
# CONFIRMED against the first live recording: a flat cutoff of 12 correctly hid three per-tick varps
# (101, 102 and 61 changes over ~100 ticks) but ALSO hid varbit 14192, which changed 14 times -- and
# 14192 is the falcon-has-a-catch flag, the single most useful signal in the whole session. Against
# tick count those separate cleanly: 14/100 ticks is an event, 101/100 ticks is a clock.
VARBIT_NOISE_TICK_RATIO = 0.25
# Never suppress below this many changes regardless of ratio, so a short recording (few ticks) does
# not classify a handful of ordinary state flips as noise.
VARBIT_NOISE_MIN = 10


# ---------------------------------------------------------------------------- bridge I/O

def bridge_get(path, params=None):
    """GET a bridge endpoint, returning the decoded body. Raises on any failure -- unlike
    controller.api_get, which swallows errors for a bot's tick-to-tick resilience. A recorder that
    quietly stopped recording would be worse than one that crashes: you would not find out until
    you went to read a log that is not there."""
    response = requests.get(f"{settings.API_BASE}{path}", params=params, timeout=REQUEST_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def arm(label):
    return bridge_get("/recorder/arm", {"on": "true", "label": label})


def disarm():
    try:
        return bridge_get("/recorder/arm", {"on": "false"})
    except Exception:
        return None


# ---------------------------------------------------------------------------- formatting helpers

def fmt_time(ms):
    return f"{ms / 1000.0:8.1f}s"


def fmt_delta(ms):
    return f"+{ms / 1000.0:.1f}s"


def fmt_tile(record, prefix=""):
    """Formats a tile from either the bare x/y/plane fields or a prefixed set.

    The prefixed fields are camelCase on the Java side (targetX, playerPlane), NOT lowercase --
    getting this wrong silently renders every position as "?" rather than raising, which is exactly
    what happened in the first live recording: the capture was perfect and the log looked broken."""
    if prefix:
        x = record.get(prefix + "X")
        y = record.get(prefix + "Y")
        plane = record.get(prefix + "Plane")
    else:
        x = record.get("x")
        y = record.get("y")
        plane = record.get("plane")
    if x is None or y is None:
        return "?"
    return f"({x},{y}) p{plane if plane is not None else '?'}"


def fmt_items(changes):
    parts = []
    for change in changes:
        delta = change.get("delta", 0)
        name = change.get("name") or f"id {change.get('id')}"
        parts.append(f"{'+' if delta > 0 else ''}{delta} {name}")
    return ", ".join(parts)


def action_signature(record, did_walk=False):
    """A stable identity for 'the same action done again', used for the summary's action counts.
    Keyed on the resolved target rather than the menu text alone -- 'Take' means nothing on its
    own, 'Take Sunstone (id 27616)' is something you can write a routine against."""
    option = record.get("option") or "?"
    kind = record.get("targetKind")
    if record.get("menuAction") == "CANCEL":
        return "Walk here (minimap)" if did_walk else "Cancel (dismissed a menu)"
    if kind == "walk":
        return "Walk here (viewport)"
    if kind == "npc":
        return f"{option} -> npc {record.get('npcName')} ({record.get('npcId')})"
    if kind == "object":
        return f"{option} -> object {record.get('objectName')} ({record.get('objectId')})"
    if kind == "grounditem":
        return f"{option} -> ground {record.get('groundItemName')} ({record.get('groundItemId')})"
    if kind == "widget":
        item = record.get("itemName")
        target = f" [{item}]" if item else ""
        packed = record.get("widgetId")
        where = f"{packed >> 16}.{packed & 0xFFFF}" if packed is not None else "?"
        return f"{option} -> widget {where}{target}"
    if kind == "walk":
        return "Walk here"
    return f"{option} -> {record.get('targetText') or kind or '?'}"


# ---------------------------------------------------------------------------- narrative rendering

def pair_object_changes(outcomes):
    """Collapses a despawn+spawn on the same tile into a single 'became' fact.

    This RuneLite version has no GameObjectChanged event, so an object transforming -- a rock
    depleting, a door opening, a brazier lighting -- arrives as two separate records. The
    transformation is the single most useful success-signal a skilling routine can key off ('the
    thing I clicked changed, therefore the click worked'), and as two unpaired records it is easy
    to miss entirely when reading the log. Returns (paired, consumed_ids)."""
    despawns = {}
    paired = []
    consumed = set()

    for record in outcomes:
        if record.get("kind") != "objdespawn":
            continue
        key = (record.get("x"), record.get("y"), record.get("plane"))
        despawns.setdefault(key, []).append(record)

    for record in outcomes:
        if record.get("kind") != "objspawn":
            continue
        key = (record.get("x"), record.get("y"), record.get("plane"))
        candidates = despawns.get(key)
        if not candidates:
            continue
        gone = candidates.pop(0)
        paired.append((gone, record))
        consumed.add(id(gone))
        consumed.add(id(record))

    return paired, consumed


def fmt_widget(record):
    """Decodes a packed widget id into the group/child pair a routine is actually written against.

    RuneLite packs interface ids as (group << 16) | child, and the raw packed integer is useless on
    its own -- 25362456 means nothing, group 387 child 24 is a thing you can look up. param0 rides
    along as the slot index within the component (an inventory or bank slot), which is separate
    from the child and is -1 when not applicable."""
    packed = record.get("widgetId")
    if packed is None:
        return "widget ?"
    group, child = packed >> 16, packed & 0xFFFF
    slot = record.get("widgetChild")
    slot_text = "" if slot in (None, -1) else f" slot {slot}"
    return f"widget {group}.{child}{slot_text}  (packed {packed})"


def actor_of(record):
    if record.get("isPlayer"):
        return "player"
    return f"{record.get('actorName')} ({record.get('npcId')})"


def noise_cutoff(tick_span):
    """How many changes a variable may make over `tick_span` game ticks before it counts as noise."""
    return max(VARBIT_NOISE_MIN, VARBIT_NOISE_TICK_RATIO * max(tick_span, 0))


def tick_span_of(records):
    ticks = [record["tick"] for record in records if isinstance(record.get("tick"), int)]
    return (max(ticks) - min(ticks)) if ticks else 0


def varbit_key(record):
    """Names a varbit record for display and for summary grouping.

    varbitId is -1 when the change was to a VARP (a player variable) rather than a varbit -- which
    is most of them. Rendering those all as "varbit -1" (as the first live recording did) collapses
    dozens of distinct variables into one meaningless line."""
    varbit_id = record.get("varbitId")
    if varbit_id is not None and varbit_id >= 0:
        return f"varbit {varbit_id}"
    return f"varp {record.get('varpId')}"


def describe(record):
    """One-line description of what a record says, with no timestamp prefix.

    Shared by the outcome lines nested under an action and the loose lines between actions, so the
    two can never drift out of sync -- they did in the first draft, where each had its own copy."""
    kind = record.get("kind")

    if kind == "anim":
        anim = record.get("animation")
        # -1 is not an animation, it is the absence of one. Reading a column of bare "anim -1" and
        # working out that they are stops, not starts, is needless friction.
        suffix = "  (ended)" if anim == -1 else ""
        return f"anim {anim}{suffix}  ({actor_of(record)})"
    if kind == "spotanim":
        return f"spotanim {record.get('spotAnimId')}  ({actor_of(record)})"
    if kind == "gfx":
        return f"ground gfx {record.get('gfxId')} at {fmt_tile(record)}"
    if kind == "xp":
        return f"xp {record.get('skill')} +{record.get('gained')}  (lvl {record.get('level')})"
    if kind in ("inv", "invstart"):
        # Slot count shown on every change, not just at the start: the fill-up curve is what a
        # routine's "time to bank" rule is written against, and reading it off a column of item
        # deltas by hand is exactly the work this log exists to remove.
        free = record.get("freeSlots")
        slots = f"   [{record.get('usedSlots')}/28 used, {free} free]" if free is not None else ""
        return f"inv  {fmt_items(record.get('changes') or [])}{slots}"
    if kind == "chat":
        return f'chat "{record.get("message")}"'
    if kind == "varbit":
        return f"{varbit_key(record)} = {record.get('value')}"
    if kind == "interact":
        target = record.get("targetName")
        return f"now interacting with {target}" if target else "stopped interacting"
    if kind == "objspawn":
        return f"object appeared {record.get('objectName')} ({record.get('objectId')}) at {fmt_tile(record)}"
    if kind == "objdespawn":
        return f"object vanished {record.get('objectName')} ({record.get('objectId')}) at {fmt_tile(record)}"
    if kind == "npcspawn":
        return f"npc appeared {record.get('npcName')} ({record.get('npcId')}) idx {record.get('npcIndex')} at {fmt_tile(record)}"
    if kind == "npcdespawn":
        return f"npc gone {record.get('npcName')} ({record.get('npcId')}) idx {record.get('npcIndex')}"
    if kind == "npcchange":
        return (f"npc idx {record.get('npcIndex')} BECAME {record.get('npcName')} ({record.get('npcId')})"
                f"  [was {record.get('fromName')} ({record.get('fromId')})] at {fmt_tile(record)}")
    if kind == "walkdest":
        if not record.get("resolved"):
            return "walk did not resolve (blocked, cancelled, or already standing there)"
        return f"walking to {fmt_tile(record)}  dist {record.get('distance')}"
    if kind == "gamestate":
        return f"-- game state: {record.get('state')} --"
    if kind == "canvasexit":
        return f"cursor LEFT the canvas at ({record.get('x')}, {record.get('y')})"
    if kind == "canvasenter":
        return f"cursor back on the canvas at ({record.get('x')}, {record.get('y')})"
    if kind == "focus":
        return "-- window FOCUSED --" if record.get("gained") else "-- window UNFOCUSED --"
    if kind == "recorder":
        return f"-- recorder schema {record.get('schema')} --"
    if kind == "key":
        return f"key  {record.get('keyName')}"

    skip = ("kind", "t", "seq", "wall", "tick", "tickAge", "causedBy", "dTick")
    return f"{kind} {json.dumps({k: v for k, v in record.items() if k not in skip})}"


def render_outcome(record, action_t):
    """One '-> +1.8s ...' line describing something that happened because of the action."""
    return f"{fmt_delta(record.get('t', action_t) - action_t)}  {describe(record)}"


def walked(outcomes):
    """True when this action actually started the player walking somewhere new."""
    return any(o.get("kind") == "walkdest" and o.get("resolved") for o in outcomes)


def render_action(record, outcomes, mouse_summary):
    """The full block for one human click: what was clicked, where, how the cursor got there, and
    everything that happened as a result."""
    lines = []
    option = record.get("option")

    # A minimap click arrives as MenuAction CANCEL with no target (see the Java side's
    # onMenuOptionClicked). Labelling it "CLICK Cancel" hides the fact that it is travel -- in the
    # first full bank-run recording that mislabelled every long-distance leg of the trip.
    if record.get("menuAction") == "CANCEL":
        option = "WALK (minimap)" if walked(outcomes) else "Cancel (dismissed a menu)"

    header = f"[{fmt_time(record.get('t', 0))} t={record.get('tick')}] CLICK  {option} {record.get('targetText') or ''}".rstrip()
    lines.append(header)

    kind = record.get("targetKind")
    detail = None
    if kind == "npc":
        detail = (f"    npc      {record.get('npcName')} (id {record.get('npcId')}, idx {record.get('npcIndex')})"
                  f"  @ {fmt_tile(record, 'target')}  dist {record.get('targetDistance')}")
    elif kind == "object":
        detail = (f"    object   {record.get('objectName')} (id {record.get('objectId')})"
                  f"  @ {fmt_tile(record, 'target')}  dist {record.get('targetDistance')}")
    elif kind == "grounditem":
        detail = (f"    ground   {record.get('groundItemName')} (id {record.get('groundItemId')})"
                  f"  @ {fmt_tile(record, 'target')}  dist {record.get('targetDistance')}")
    elif kind == "walk" and record.get("targetX") is not None:
        detail = f"    tile     {fmt_tile(record, 'target')}  dist {record.get('targetDistance')}"
    # A walk with no targetX prints no tile line at all: its destination is unknowable from the
    # menu event (WALK's params are canvas coords) and arrives instead as the `walkdest` outcome
    # line below. Printing "tile ?  dist None" here just looks like missing data.
    elif kind == "widget":
        item = f"  item {record.get('itemName')} ({record.get('itemId')})" if record.get("itemName") else ""
        text = f'  text "{record.get("widgetText")}"' if record.get("widgetText") else ""
        detail = f"    {fmt_widget(record)}{item}{text}"
    if detail:
        lines.append(detail)

    lines.append(f"    from     {fmt_tile(record, 'player')}   menuAction {record.get('menuAction')}")

    canvas = f"({record.get('canvasX')},{record.get('canvasY')})"
    lines.append(f"    canvas   {canvas}{mouse_summary}")

    # tickAge exists so a stale mirrored tick is visible rather than silently misleading -- see
    # RecorderProvider.base. Surfaced only when it is actually large, to avoid noise on every line.
    tick_age = record.get("tickAge")
    if isinstance(tick_age, (int, float)) and tick_age >= 600:
        lines.append(f"    !! tick stamp is {tick_age}ms old -- trust the wall time, not t={record.get('tick')}")

    paired, consumed = pair_object_changes(outcomes)
    for gone, came in paired:
        dt = fmt_delta(came.get("t", 0) - record.get("t", 0))
        lines.append(f"    ->  {dt}  object {fmt_tile(came)} became "
                     f"{came.get('objectName')} ({came.get('objectId')})  "
                     f"[was {gone.get('objectName')} ({gone.get('objectId')})]")

    for outcome in outcomes:
        if id(outcome) in consumed:
            continue
        lines.append(f"    ->  {render_outcome(outcome, record.get('t', 0))}")

    if not outcomes:
        lines.append("    ->  (nothing observed -- click may have been ignored or out of range)")

    return lines


def render_loose(record):
    """A notable record that no action claimed. Worth showing: an unattributed xp drop or chat line
    usually means something happened on its own (a passive effect, a random event, an NPC acting),
    which is exactly the kind of thing a routine has to cope with."""
    kind = record.get("kind")
    stamp = f"[{fmt_time(record.get('t', 0))} t={record.get('tick')}]"

    if kind == "invstart":
        return (f"{stamp}   starting inventory: {fmt_items(record.get('changes') or []) or '(empty)'}"
                f"   [{record.get('usedSlots')}/28 used, {record.get('freeSlots')} free]")
    if kind == "key" and record.get("edge") != "down":
        return None
    if kind == "interact" and not record.get("targetName"):
        return None

    return f"{stamp}   {describe(record)}"


def summarize_mouse(mouse_records):
    """Collapses the cursor trajectory since the previous action into one phrase. The full point
    list stays in the .jsonl for anyone wanting real human movement curves; the narrative only
    needs to convey how much travel and hesitation preceded the click."""
    if not mouse_records:
        return ""

    # Span measured across the POINT timestamps, not the batch records' own `t`. A batch is stamped
    # when it was flushed, so a single batch of 3 samples spanning 40ms reported "over 0ms" -- see
    # the first live recording. Each point carries its own capture time as its third element.
    stamps = [point[2] for record in mouse_records
              for point in (record.get("points") or []) if len(point) >= 3]
    if not stamps:
        return ""
    return f"   mouse: {len(stamps)} pts over {max(stamps) - min(stamps)}ms"


# ---------------------------------------------------------------------------- stream assembly

def build_narrative(records, state=None):
    """Turns a flat record stream into narrative lines. Pure apart from the `state` dict it
    threads -- no I/O, no clock -- so the whole rendering path is unit-testable.

    `state` carries running varbit counts ACROSS calls, because main() renders in batches as they
    arrive and noise can only be recognised by how often a variable has changed so far. Pass the
    same dict every call; omit it (tests, one-shot rendering) and suppression restarts each call."""
    if state is None:
        state = {}
    varbit_counts = state.setdefault("varbit_counts", Counter())
    suppressed = state.setdefault("varbit_suppressed", set())

    def keep_varbit(record):
        """Drops a varbit from the NARRATIVE (never from the .jsonl or the summary) once it has
        proven to be per-tick noise.

        CONFIRMED live 2026-08-23: the Falconry recording's log was dominated by one varp changing
        every single tick (102 times in 60s) plus two more at similar rates, burying the ones that
        actually mattered -- varbit 14192 flipping 5/0 around each catch, and 9893 incrementing once
        per kebbit. The cutoff scales with ticks elapsed so far, so it tightens as the recording
        grows rather than being a guess fixed in advance."""
        key = varbit_key(record)
        varbit_counts[key] += 1
        cutoff = noise_cutoff(state.get("max_tick", 0) - state.get("first_tick", 0))
        if varbit_counts[key] <= cutoff:
            return True
        suppressed.add(key)
        return False

    outcomes_by_action = defaultdict(list)
    for record in records:
        cause = record.get("causedBy")
        if cause is not None:
            outcomes_by_action[cause].append(record)

    lines = []
    pending_mouse = []

    for record in records:
        kind = record.get("kind")

        # Tracked across batches so keep_varbit's cutoff reflects the whole recording so far, not
        # just the handful of records in this one poll.
        tick = record.get("tick")
        if isinstance(tick, int):
            state["max_tick"] = max(state.get("max_tick", tick), tick)
            state.setdefault("first_tick", tick)

        if record.get("causedBy") is not None:
            continue  # rendered inside its action's block

        if kind == "mouse":
            pending_mouse.append(record)
            continue

        if kind == "action":
            outcomes = [outcome for outcome in outcomes_by_action.get(record.get("seq"), [])
                        if outcome.get("kind") != "varbit" or keep_varbit(outcome)]
            lines.append("")
            lines.extend(render_action(record, outcomes, summarize_mouse(pending_mouse)))
            pending_mouse = []
            continue

        if kind == "varbit" and not keep_varbit(record):
            continue

        if kind in LOOSE_NARRATIVE_KINDS:
            line = render_loose(record)
            if line:
                lines.append(line)

    return lines


def build_summary(records):
    """The counts you actually want in front of you when writing the routine: which ids to target,
    which animations mean 'working', which varbits track state, what the activity consumes and
    produces."""
    actions = Counter()
    objects = {}
    npcs = {}
    items = Counter()
    anims = Counter()
    spotanims = Counter()
    gfx = Counter()
    varbits = Counter()
    transforms = Counter()
    xp = Counter()
    regions = set()
    chat = Counter()
    keys = Counter()

    first_t = None
    last_t = 0
    click_count = 0
    route = []
    region_path = []

    # Which actions actually started a walk, so a minimap Cancel is counted as travel rather than as
    # a menu dismissal. Needs a pre-pass because the walkdest arrives after its action.
    walk_seqs = {record.get("causedBy") for record in records
                 if record.get("kind") == "walkdest" and record.get("resolved")}

    for record in records:
        kind = record.get("kind")
        t = record.get("t", 0)
        if first_t is None:
            first_t = t
        last_t = max(last_t, t)

        if kind == "action":
            actions[action_signature(record, record.get("seq") in walk_seqs)] += 1
            if record.get("objectId") is not None:
                objects[record["objectId"]] = record.get("objectName")
            if record.get("npcId") is not None:
                npcs[record["npcId"]] = record.get("npcName")
        elif kind == "click" and record.get("edge") == "down":
            click_count += 1
        elif kind == "anim":
            who = "player" if record.get("isPlayer") else f"npc {record.get('npcId')}"
            anims[f"{record.get('animation')} ({who})"] += 1
        elif kind == "spotanim":
            who = "player" if record.get("isPlayer") else f"npc {record.get('npcId')}"
            spotanims[f"{record.get('spotAnimId')} ({who})"] += 1
        elif kind == "gfx":
            gfx[record.get("gfxId")] += 1
        elif kind == "xp":
            xp[record.get("skill")] += record.get("gained", 0)
        elif kind == "varbit":
            varbits[varbit_key(record)] += 1
        elif kind == "npcchange":
            transforms[f"idx {record.get('npcIndex')}: {record.get('fromName')} "
                       f"({record.get('fromId')}) -> {record.get('npcName')} ({record.get('npcId')})"] += 1
        elif kind in ("npcspawn", "npcdespawn"):
            if record.get("npcId") is not None:
                npcs[record["npcId"]] = record.get("npcName")
        elif kind == "chat":
            chat[record.get("message")] += 1
        elif kind == "key" and record.get("edge") == "down":
            keys[record.get("keyName")] += 1
        elif kind == "tick":
            region = record.get("region")
            if region is not None:
                regions.add(region)
                # Consecutive-duplicate-free, so this reads as the trip's actual leg order rather
                # than one entry per tick. This is the backbone a travel routine is written from.
                if not region_path or region_path[-1] != region:
                    region_path.append(region)
        elif kind == "walkdest" and record.get("resolved"):
            route.append((record.get("x"), record.get("y"), record.get("region")))
        elif kind == "inv":
            for change in record.get("changes") or []:
                items[change.get("name") or f"id {change.get('id')}"] += change.get("delta", 0)
        elif kind in ("objspawn", "objdespawn"):
            if record.get("objectId") is not None:
                objects[record["objectId"]] = record.get("objectName")

    duration_s = (last_t - (first_t or 0)) / 1000.0

    lines = ["=" * 78, "SUMMARY", "=" * 78, ""]
    lines.append(f"duration          {int(duration_s // 60)}m {int(duration_s % 60)}s")
    lines.append(f"records           {len(records)}")
    lines.append(f"menu actions      {sum(actions.values())}")
    lines.append(f"raw mouse clicks  {click_count}")
    lines.append(f"regions           {', '.join(str(r) for r in sorted(regions)) or '-'}")

    lines.append("")
    lines.append("region path (in travel order):")
    lines.append("  " + (" -> ".join(str(r) for r in region_path) if region_path else "-"))

    lines.append("")
    lines.append("walk destinations (in order):")
    lines.extend([f"  {i + 1:3}. ({x},{y})   region {region}"
                  for i, (x, y, region) in enumerate(route)] or ["  -"])

    def section(title, counter, formatter=lambda k, v: f"  {k}   x{v}"):
        lines.append("")
        lines.append(f"{title}:")
        if not counter:
            lines.append("  -")
            return
        for key, value in counter.most_common():
            lines.append(formatter(key, value))

    section("actions performed", actions)
    section("npc transformations (same index, id changed)", transforms)
    section("animations", anims)
    section("spotanims (on actors)", spotanims)
    section("ground gfx", gfx)

    # Split by frequency rather than listed together. Anything flipping on most ticks is a clock or
    # a camera/animation variable and is noise; the rare ones are the activity's actual state. In
    # the Falconry recording that split cleanly separated three per-tick varps from varbit 14192
    # (falcon-has-a-catch) and 9893 (catch counter) -- the two a routine would actually key off.
    cutoff = noise_cutoff(tick_span_of(records))
    quiet = Counter({k: v for k, v in varbits.items() if v <= cutoff})
    noisy = Counter({k: v for k, v in varbits.items() if v > cutoff})
    section("state vars, low frequency (LIKELY MEANINGFUL)", quiet,
            lambda k, v: f"  {k}   changed {v}x")
    section("state vars, high frequency (likely noise; hidden from the narrative)", noisy,
            lambda k, v: f"  {k}   changed {v}x")

    section("chat lines seen", chat)
    section("keys pressed", keys)

    lines.append("")
    lines.append("xp gained:")
    lines.extend([f"  {skill}   +{amount}" for skill, amount in xp.most_common()] or ["  -"])

    lines.append("")
    lines.append("net inventory change:")
    net = [(name, delta) for name, delta in items.items() if delta != 0]
    lines.extend([f"  {'+' if d > 0 else ''}{d} {n}" for n, d in sorted(net, key=lambda p: -abs(p[1]))] or ["  -"])

    lines.append("")
    lines.append("object ids seen:")
    lines.extend([f"  {oid}   {name}" for oid, name in sorted(objects.items())] or ["  -"])

    lines.append("")
    lines.append("npc ids seen:")
    lines.extend([f"  {nid}   {name}" for nid, name in sorted(npcs.items())] or ["  -"])

    return lines


def split_renderable(pending, newest_t):
    """Splits pending records into (ready_to_render, still_waiting).

    An action is only ready once RENDER_DELAY_MS has passed since it fired, because its outcome
    records are still arriving until then. Its outcomes are pulled along with it regardless of their
    own timestamps -- they are guaranteed present by the time the action is ready, since
    RENDER_DELAY_MS exceeds the Java-side outcome window."""
    cutoff = newest_t - RENDER_DELAY_MS
    ready_actions = {record.get("seq") for record in pending
                     if record.get("kind") == "action" and record.get("t", 0) <= cutoff}

    ready = []
    waiting = []
    for record in pending:
        cause = record.get("causedBy")
        if cause is not None:
            (ready if cause in ready_actions else waiting).append(record)
        elif record.get("t", 0) <= cutoff:
            ready.append(record)
        else:
            waiting.append(record)

    return ready, waiting


# ---------------------------------------------------------------------------- main loop

def main():
    parser = argparse.ArgumentParser(description="Record a human play session for routine-building.")
    parser.add_argument("label", help="short name for this recording, e.g. sunstone-mining")
    parser.add_argument("--out", default=RECORDINGS_DIR, help=f"output directory (default {RECORDINGS_DIR})")
    parser.add_argument("--no-mouse", action="store_true",
                        help="omit mouse-movement samples from the .jsonl (they are still used for "
                             "the narrative's approach summary, just not persisted)")
    args = parser.parse_args()

    try:
        status = arm(args.label)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            print("The bridge has no /recorder endpoint.\n"
                  "RecorderProvider is compiled into the Java plugin, so the client must be rebuilt "
                  "and restarted before it exists:  ./gradlew.bat run", file=sys.stderr)
            return 2
        raise
    except requests.RequestException as exc:
        print(f"Cannot reach the bridge at {settings.API_BASE}: {exc}\n"
              "Is the RuneLite client running?  ./gradlew.bat run", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = os.path.join(args.out, f"{stamp}_{args.label}")
    jsonl_path, log_path, summary_path = f"{base}.jsonl", f"{base}.log", f"{base}.summary"

    print(f"[rec] armed  (outcome window {status.get('outcomeWindowTicks')} ticks, "
          f"ambient radius {status.get('ambientRadius')} tiles)")
    print(f"[rec] writing {jsonl_path}")
    print(f"[rec]         {log_path}")
    print("[rec] go play. Ctrl+C to stop.\n")

    all_records = []
    pending = []
    render_state = {}
    since = -1
    total_dropped = 0
    last_report = time.time()

    jsonl_file = open(jsonl_path, "w", encoding="utf-8")
    log_file = open(log_path, "w", encoding="utf-8")
    log_file.write(f"=== recording: {args.label}   started {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
    log_file.write(f"=== a varbit/varp changing on more than {VARBIT_NOISE_TICK_RATIO:.0%} of ticks "
                   f"is hidden below as noise; all are in the .summary and .jsonl ===\n")
    log_file.flush()

    try:
        while True:
            try:
                body = bridge_get("/recorder/events", {"since": since, "limit": POLL_LIMIT})
            except requests.RequestException as exc:
                print(f"[rec] bridge poll failed ({exc}) -- retrying", file=sys.stderr)
                time.sleep(1.0)
                continue

            events = body.get("events") or []
            if events:
                since = body.get("nextSeq", since)
                for record in events:
                    if args.no_mouse and record.get("kind") == "mouse":
                        continue
                    jsonl_file.write(json.dumps(record, separators=(",", ":")) + "\n")
                jsonl_file.flush()
                all_records.extend(events)
                pending.extend(events)

            dropped = body.get("dropped", 0)
            if dropped > total_dropped:
                # A hole in the recording. Loud, because a silently incomplete recording is exactly
                # the failure mode that wastes an hour of play.
                print(f"[rec] !! WARNING: bridge dropped {dropped - total_dropped} records "
                      f"(reader too slow / buffer overflow) -- this recording has a gap",
                      file=sys.stderr)
                total_dropped = dropped

            if pending:
                newest_t = max(record.get("t", 0) for record in pending)
                ready, pending = split_renderable(pending, newest_t)
                if ready:
                    ready.sort(key=lambda record: record.get("seq", 0))
                    for line in build_narrative(ready, render_state):
                        log_file.write(line + "\n")
                    log_file.flush()

            now = time.time()
            if now - last_report >= 15.0:
                actions = sum(1 for record in all_records if record.get("kind") == "action")
                elapsed = int(all_records[-1].get("t", 0) / 1000) if all_records else 0
                print(f"[rec] {actions} actions, {len(all_records)} records, "
                      f"{elapsed // 60}m{elapsed % 60:02d}s elapsed")
                last_report = now

            time.sleep(POLL_INTERVAL_S)

    except KeyboardInterrupt:
        print("\n[rec] stopping...")

    finally:
        # Final drain BEFORE disarming: anything buffered between the last poll and Ctrl+C is still
        # in the ring buffer, and disarming first would clear it on the next arm without ever
        # having been read.
        try:
            body = bridge_get("/recorder/events", {"since": since, "limit": POLL_LIMIT})
            tail = body.get("events") or []
            for record in tail:
                if args.no_mouse and record.get("kind") == "mouse":
                    continue
                jsonl_file.write(json.dumps(record, separators=(",", ":")) + "\n")
            all_records.extend(tail)
            pending.extend(tail)
        except requests.RequestException:
            pass

        disarm()

        pending.sort(key=lambda record: record.get("seq", 0))
        for line in build_narrative(pending, render_state):
            log_file.write(line + "\n")

        summary_lines = build_summary(all_records)
        log_file.write("\n\n")
        log_file.write("\n".join(summary_lines) + "\n")
        jsonl_file.close()
        log_file.close()

        with open(summary_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(summary_lines) + "\n")

        actions = sum(1 for record in all_records if record.get("kind") == "action")
        print(f"[rec] saved {len(all_records)} records, {actions} actions")
        if total_dropped:
            print(f"[rec] !! {total_dropped} records were dropped -- the recording has gaps")
        print(f"[rec]   {log_path}")
        print(f"[rec]   {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
