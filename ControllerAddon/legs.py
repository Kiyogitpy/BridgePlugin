# legs.py -- execute ONE transport leg (a teleport item, a fairy ring, a POH box/portal, a
# walk) and confirm it landed. Promoted 2026-09-18 out of banker.py's return-teleport runner
# (_run_leg / _run_item_leg / _run_object_leg / _pick_destination / _await_arrival, added
# 2026-09-07) so bank returns and slayer task travel share one runner. banker keeps those names
# as aliases; nothing about the bank path changed.
#
# A leg is a flat dict that survives in ctx.state.data across ticks:
#   {"kind": "item"|"fairy"|"walk"|"box"|"poh_portal", "display": str, "dest": [x, y, plane]|None,
#    "item": str|None, "option": str|None, "origin": [x, y, plane]|None,
#    "object_id": int|None, "object_info": str, "code": str|None, "expect": str|None}
# run_next() is the cursor over a chain: one bounded leg per call, so a routine's tick stays
# short and a restart resumes at the leg it was on. Object legs by NAME/ACTION (ladders, caves,
# stairs -- spec §8) are sub-project 3; "box"/"poh_portal" here are banker's POH legs, still
# UNVERIFIED live.
#
# How each kind confirms it actually landed, not just that the click went in: item/box/portal
# legs (run_leg) confirm through await_arrival against the leg's own dest. A fairy leg confirms
# through await_arrival too, but ONLY when built with a dest (legs.fairy(code, expect, dest=...));
# built with no dest it reports DONE straight off hop_fairy_ring's own True, same as before. A
# walk leg's confirmation is travel.walk_to's own return value -- nothing further to check.
#
# PROGRESS is unbounded BY DESIGN: run_next does one bounded action per call and leaves bounding
# repeated PROGRESS to the CALLER. A fairy ring that never comes into view, or a safety stop
# raised from inside hop_fairy_ring, both surface as an endless stream of PROGRESS -- a routine
# looping on run_next must cap its own retries.

import time

import controller
import crossfloor
import debug_log
import dialogue
import poh
import transports
import travel

ITEM_BUDGET_S = 6.0      # a teleport is ~4 ticks of animation plus a region load; falconry measured 2.4-3.2s
FAIRY_BUDGET_S = 12.0    # dial + hop + region load; spec §8's fairy budget
POLL_S = 0.3
ARRIVAL_TILES = 12       # several teleports drop you on a small spread of tiles; the walk covers the rest
PICKER_BUDGET_S = 4.0    # a transport's destination picker -> appeared and answered
LINK_ARRIVAL_TILES = 3   # a ladder lands you on (or beside) one tile -- far tighter than a teleport
LINK_BUDGET_S = 10.0     # click -> landed, INCLUDING the last steps onto the object and a region load
LINK_OBJECT_RADIUS = 6   # how far from the origin tile the transport's own object may sit
WALK_ACROSS_MAX_LINKS = 6
STEP_OFF_MAX = 2         # leaving a map-solid tile (a fairy ring's own) before any routing

DONE = "done"            # the leg landed; cursor advanced
PROGRESS = "progress"    # the leg did a bounded step and wants another call (fairy rings)
FAILED = "failed"        # the leg did not land; cursor unchanged, caller decides (retry / stop)
FINISHED = "finished"    # cursor is past the last leg


# ---------------------------------------------------------------- builders

def _base(kind, display, dest):
    return {"kind": kind, "display": display, "item": None, "option": None,
            "dest": list(dest) if dest else None, "origin": None, "object_id": None,
            "object_info": ""}


def item(name: str, option: str, dest) -> dict:
    """An INVENTORY teleport item: left-click verified against `option`, else the menu.

    run_item_leg searches the inventory only -- a WORN teleport item is not found here.
    Supporting a worn item as a leg is sub-project 3's work, not this one's."""
    leg = _base("item", f"{name}: {option}", dest)
    leg["item"] = name
    leg["option"] = option
    return leg


def spell(name: str, widget, dest) -> dict:
    """A teleport SPELL cast from the spellbook: `widget` is its (group, child) in interface 218."""
    leg = _base("spell", f"cast {name}", dest)
    leg["option"] = "Cast"
    leg["widget"] = list(widget)
    return leg


def dialogue_item(name: str, label: str, needle: str, dest) -> dict:
    """An INVENTORY item whose destination is CHOSEN in a chat menu -- the slayer ring.

    `label` is the destination as the item's own right-click sub-option spells it (cache obj
    11866: Stronghold / Slayer Tower / Fremennik Dungeon); `needle` is the lowercase text matched
    against the paged destination dialogue. See run_dialogue_item_leg."""
    leg = _base("dialogue_item", f"{name}: {label}", dest)
    leg["item"] = name
    leg["label"] = label
    leg["option"] = needle
    return leg


def fairy(code: str, expect: str, dest=None, ring_tile=None, ring_id=None) -> dict:
    """A fairy-ring hop, delegated to travel.hop_fairy_ring (dialled, never Last-destination).
    `ring_tile`/`ring_id` name WHICH ring when it is not an ordinary one already in view."""
    leg = _base("fairy", f"fairy ring {code}", dest)
    leg["code"] = code
    leg["expect"] = expect
    leg["ring_tile"] = list(ring_tile) if ring_tile else None
    leg["ring_id"] = ring_id
    return leg


def walk(x: int, y: int, plane: int) -> dict:
    """A same-plane walk toward (x, y, plane) (travel.walk_to, goal_adjacent=False).

    "Exact tile" overstates it -- walk_to uses its own default arrival tolerance (3 tiles),
    not this leg's own dest, so landing can be a few tiles short of (x, y)."""
    return _base("walk", f"walk to {x},{y},{plane}", (x, y, plane))


def house() -> dict:
    """Break a house tablet (poh.enter) -- a no-op if already inside."""
    return _base("poh_enter", "house tablet", None)


def pool() -> dict:
    """Drink from the house pool (poh.drink_pool) -- never fails the chain on its own."""
    return _base("poh_pool", "house pool", None)


def house_box(destination: str, dest) -> dict:
    """Leave through the ornate jewellery box's Teleport Menu to `destination` (poh.use_box),
    confirmed by the player no longer being in the house. `dest` is informational (the expected
    landing, for logs and for planning the walk that follows)."""
    leg = _base("poh_box", f"jewellery box: {destination}", dest)
    leg["option"] = destination
    return leg


def glider(pilot: str, destination: str, widget, dest) -> dict:
    """A gnome glider flight: talk to `pilot`, click `destination` on the map (interface 138)."""
    leg = _base("glider", f"glider: {destination}", dest)
    leg["item"] = pilot
    leg["option"] = "Glider"
    leg["widget"] = list(widget)
    return leg


def house_mounted(name: str, action: str, dest) -> dict:
    """An item MOUNTED in the house (a digsite pendant): take `action` from its menu."""
    leg = _base("poh_mounted", f"mounted {name}: {action}", dest)
    leg["item"] = name
    leg["option"] = action
    return leg


def house_ring(code: str, expect: str, dest=None) -> dict:
    """Leave through the house's own fairy ring (poh.use_ring), same dial/confirm mechanic as
    an ordinary `fairy` leg."""
    leg = _base("poh_ring", f"house fairy ring {code}", dest)
    leg["code"] = code
    leg["expect"] = expect
    return leg


def from_transport(t) -> dict:
    """A teleports.Transport, flattened -- banker's _leg_dict."""
    return {
        "kind": t.kind,
        "display": t.display,
        "item": t.item_name,
        "option": t.destination_name,
        "dest": list(t.dest),
        "origin": list(t.origin) if t.origin else None,
        "object_id": trailing_int(t.object_info),
        "object_info": t.object_info,
    }


def trailing_int(text):
    """`Teleport Menu Ornate Jewellery Box 37520` -> 37520.

    The column is `menuOption menuTarget objectID` with both names multi-word, so it cannot be
    split reliably -- but the object id is always the trailing integer, and an id is all we need
    to find the thing in /objects.
    """
    if not text:
        return None
    tail = text.strip().split()[-1]
    try:
        return int(tail)
    except ValueError:
        return None


# ---------------------------------------------------------------- running

def run_next(ctx, leg_list, cursor: int, log: str = "travel"):
    """Run the leg at `cursor`. Returns (status, new_cursor)."""
    if cursor >= len(leg_list or []):
        return FINISHED, cursor
    leg = leg_list[cursor]
    kind = leg.get("kind")
    if kind == "fairy":
        extra = {}
        tile = leg.get("ring_tile")
        if tile:
            extra["ring_tile"] = {"x": tile[0], "y": tile[1], "plane": tile[2]}
        if leg.get("ring_id"):
            extra["ring_ids"] = (leg["ring_id"],)
        ok = travel.hop_fairy_ring(ctx, leg.get("code"), leg.get("expect"), log=log, **extra)
        if not ok:
            return PROGRESS, cursor
        dest = leg.get("dest") or []
        if len(dest) == 3 and not await_arrival(dest, FAIRY_BUDGET_S):
            return FAILED, cursor
        return DONE, cursor + 1
    if kind == "walk":
        dest = leg.get("dest") or []
        ok = len(dest) == 3 and walk_across(ctx, tuple(dest), log=log)
        return (DONE, cursor + 1) if ok else (FAILED, cursor)
    if kind == "poh_enter":
        return (DONE, cursor + 1) if poh.enter(ctx, log) else (FAILED, cursor)
    if kind == "poh_pool":
        return (DONE, cursor + 1) if poh.drink_pool(ctx, log) else (FAILED, cursor)
    if kind == "poh_box":
        if not poh.use_box(ctx, leg.get("option"), log):
            return FAILED, cursor
        # Confirmed by LEAVING THE HOUSE, not by a landing tile: our own vendored tables disagree
        # about where the box lands (phase-B review), and the walk leg re-plans from the live tile.
        if not poh.await_left_house(ITEM_BUDGET_S):
            return FAILED, cursor
        return DONE, cursor + 1
    if kind == "poh_mounted":
        if not poh.use_mounted(ctx, leg.get("item"), leg.get("option"), log):
            return FAILED, cursor
        # Confirmed by LEAVING THE HOUSE, like the box: the walk leg re-plans from the live tile.
        if not poh.await_left_house(ITEM_BUDGET_S):
            return FAILED, cursor
        return DONE, cursor + 1
    if kind == "poh_ring":
        ok = poh.use_ring(ctx, leg.get("code"), leg.get("expect"), log=log)
        if not ok:
            return PROGRESS, cursor
        dest = leg.get("dest") or []
        if len(dest) == 3 and not await_arrival(dest, FAIRY_BUDGET_S):
            return FAILED, cursor
        return DONE, cursor + 1
    ok = run_leg(ctx, leg, log=log)
    return (DONE, cursor + 1) if ok else (FAILED, cursor)


def await_arrival(dest, budget_s: float = ITEM_BUDGET_S, radius: int = ARRIVAL_TILES) -> bool:
    """True once we are standing near `dest`. Polled, never slept off -- karambwan_fishing
    MEASURED that a teleport's own signals fire on the tick the LOADING screen starts, when the
    bridge answers nothing at all."""
    waited = 0.0
    while waited < budget_s:
        time.sleep(POLL_S)
        waited += POLL_S
        player = controller.get_player() or {}
        px, py = player.get("x"), player.get("y")
        if px is None:
            continue
        if (player.get("plane", 0) == dest[2]
                and abs(px - dest[0]) <= radius
                and abs(py - dest[1]) <= radius):
            return True
    return False


def run_leg(ctx, leg, log: str = "travel") -> bool:
    """Execute one item/box/portal hop. False means the chain stops and the caller decides."""
    dest = leg.get("dest") or []
    if len(dest) != 3:
        return False
    kind = leg.get("kind")
    runner = (run_item_leg if kind == "item" else run_dialogue_item_leg if kind == "dialogue_item"
              else run_spell_leg if kind == "spell" else run_glider_leg if kind == "glider"
              else run_object_leg)
    return runner(ctx, leg, log) and await_arrival(dest)


def run_item_leg(ctx, leg, log: str = "travel") -> bool:
    """An inventory teleport: LEFT-click verified through /hover, menu only as the fallback.

    The pattern falconry/karambwan already prove live (USER 2026-09-06: "for inventory teleports
    ive changed them so we just click the item and we teleport to the right spot").
    """
    name = (leg.get("item") or "").lower()
    it = next((i for i in (controller.get_inventory() or [])
               if name and name in (i.get("name") or "").lower()), None)
    if it is None:
        debug_log.log(log, f"no {leg.get('item')} in inventory -- leg '{leg.get('display')}' cannot run")
        return False

    option = leg.get("option")
    debug_log.log(log, f"leg: {leg.get('display')}")
    controller.open_inventory_tab(ctx.ser)
    ok = controller.click_entity(ctx.ser, it, check_ui_blockers=False,
                                 simulate_mistakes=False,
                                 expected_action=option if option else None)
    if not ok:
        if getattr(ok, "withheld", False):
            debug_log.log(log, f"{leg.get('item')}: left-click isn't the teleport here -- using the menu")
        ok = controller.click_menu_action(ctx.ser, it, option or "Teleport",
                                          check_ui_blockers=False)
    return bool(ok)


def run_spell_leg(ctx, leg, log: str = "travel") -> bool:
    """Cast a teleport spell exactly as npc_contact.cast casts NPC Contact (proven live
    2026-09-19): the magic tab, GET /widget for the spell, ONE click at offset (0,0) -- the icons
    are packed, the default nudge lands on a neighbour. The hover gate demands `Cast`.

    A spell that is not clickable (wrong spellbook, filtered out) fails here; one with no runes
    clicks and does nothing, and the caller's landing check fails the leg. UNVERIFIED LIVE for
    Moonclan Teleport; its widget id is from the cache, cross-checked against NPC Contact's."""
    import npc_contact                      # local: keeps legs importable without the contact module
    widget = leg.get("widget") or []
    if len(widget) != 2:
        return False
    if controller.get_active_tab() != npc_contact.MAGIC_TAB_INDEX:
        controller.key_tap(ctx.ser, npc_contact.MAGIC_TAB_KEY)
        controller.human_delay(0.3, 0.5)
    icon = controller.get_widget(widget[0], widget[1])
    if not icon or not icon.get("clickable"):
        debug_log.log(log, f"spell widget {widget[0]}.{widget[1]} is not clickable -- wrong spellbook, or filtered?")
        return False
    debug_log.log(log, f"leg: {leg.get('display')}")
    return bool(controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False,
                                        simulate_mistakes=False, expected_action="Cast"))


GLIDER_MAP_BUDGET_S = 10.0      # Microbot waits 10 s for interface 138; the pilot may be a walk away


def run_glider_leg(ctx, leg, log: str = "travel") -> bool:
    """A gnome glider: the pilot's `Glider` action opens the map (interface 138), and the
    destination is one widget on it. The mechanism is Microbot's Rs2Walker.handleGlider (facts
    only, never its input layer): its packed widget ids decode as (138 << 16) | child. The pilot
    is tracked by NPC INDEX through the click, like every other NPC click here.

    UNVERIFIED LIVE. Needs The Grand Tree; without it the map never opens and the leg fails
    within GLIDER_MAP_BUDGET_S. The caller confirms the landing."""
    widget = leg.get("widget") or []
    if len(widget) != 2:
        return False
    entry = controller.get_widget(widget[0], widget[1])
    if not (entry and entry.get("clickable")):
        pilot = (leg.get("item") or "").lower()
        npc = next((n for n in (controller.get_npcs(fresh=True) or [])
                    if pilot and pilot in (n.get("name") or "").lower() and n.get("clickable")), None)
        if npc is None:
            debug_log.log(log, f"no {leg.get('item')} in view -- leg '{leg.get('display')}' cannot run")
            return False
        debug_log.log(log, f"leg: {leg.get('display')}")
        refresh = controller.npc_index_refresh_target(npc.get("index"))
        if not controller.click_menu_action(ctx.ser, npc, leg.get("option") or "Glider", refresh_target=refresh):
            debug_log.log(log, f"couldn't take 'Glider' from {npc.get('name')}")
            return False
        waited = 0.0
        while waited < GLIDER_MAP_BUDGET_S:
            entry = controller.get_widget(widget[0], widget[1])
            if entry and entry.get("clickable"):
                break
            time.sleep(POLL_S)
            waited += POLL_S
        else:
            debug_log.log(log, "the glider map never opened -- is The Grand Tree done?")
            return False
    return bool(controller.click_entity(ctx.ser, entry, offset=(0, 0), check_ui_blockers=False,
                                        simulate_mistakes=False))


def _standing_near(dest) -> bool:
    player = controller.get_player() or {}
    px, py = player.get("x"), player.get("y")
    return (px is not None and player.get("plane", 0) == dest[2]
            and abs(px - dest[0]) <= ARRIVAL_TILES and abs(py - dest[1]) <= ARRIVAL_TILES)


def run_dialogue_item_leg(ctx, leg, log: str = "travel") -> bool:
    """The slayer ring: a verified LEFT-click, else the item's own menu, then the destination
    picked by TEXT in a dialogue that is PAGED ("More options...", dialogue.choose walks it).

    The pattern is routines/golem_crafting._teleport_home's, proven live there 2026-09-06 and
    2026-09-14 for the ring's Wyrmscraig entry; only the destination differs. The hover gate
    accepts the destination, `Teleport` or `Rub` (cache obj 11866: iop2=Rub, iop3=Teleport with
    the destinations as its sub-options), so an un-swapped left-click ("Wear") is WITHHELD, never
    clicked. UNVERIFIED LIVE for these three destinations; the caller confirms the landing, and
    a dialogue that never offers the destination fails the leg rather than picking a neighbour."""
    name = (leg.get("item") or "").lower()
    it = next((i for i in (controller.get_inventory() or [])
               if name and name in (i.get("name") or "").lower()), None)
    if it is None:
        debug_log.log(log, f"no {leg.get('item')} in inventory -- leg '{leg.get('display')}' cannot run")
        return False
    label, needle, dest = leg.get("label"), leg.get("option"), leg.get("dest")
    debug_log.log(log, f"leg: {leg.get('display')}")
    controller.open_inventory_tab(ctx.ser)
    clicked = controller.click_entity(ctx.ser, it, check_ui_blockers=False, simulate_mistakes=False,
                                      expected_action=(label, "Teleport", "Rub"))
    if clicked:
        # A swapped left-click either teleports at once or opens the destination dialogue.
        if not _standing_near(dest):
            dialogue.choose(ctx, needle, log=log)
        return True
    if controller.click_menu_action(ctx.ser, it, label, check_ui_blockers=False):
        return True
    if not controller.click_menu_action(ctx.ser, it, ("Teleport", "Rub"), check_ui_blockers=False):
        debug_log.log(log, f"{leg.get('item')}: no {label}/Teleport/Rub entry in its menu")
        return False
    if not dialogue.choose(ctx, needle, log=log):
        debug_log.log(log, f"{leg.get('item')}: the dialogue never offered '{needle}'")
        return False
    return True


def run_object_leg(ctx, leg, log: str = "travel") -> bool:
    """A world-object hop: a POH jewellery box or portal.

    NOT YET VERIFIED LIVE -- it has never run inside a house. Every step confirms, and any
    failure returns False so the caller falls back rather than clicking blindly.
    """
    obj_id = leg.get("object_id")
    origin = leg.get("origin")
    if obj_id is None or not origin:
        return False

    # Walk to the object first: the game accepts a click on anything visible and then silently
    # does nothing when it cannot path there (CLAUDE.md's standing invariant).
    if not travel.walk_to(ctx, {"x": origin[0], "y": origin[1], "plane": origin[2]},
                          goal_adjacent=True):
        debug_log.log(log, f"couldn't reach {leg.get('display')}'s object")
        return False

    obj = controller.get_object_at(origin[0], origin[1], origin[2], obj_id, radius=16)
    if obj is None:
        obj = next((o for o in (controller.get_objects(radius=16) or [])
                    if o.get("id") == obj_id), None)
    if obj is None:
        debug_log.log(log, f"object {obj_id} not found for {leg.get('display')}")
        return False

    debug_log.log(log, f"leg: {leg.get('display')} (object {obj_id})")
    if not controller.click_entity(
            ctx.ser, obj, simulate_mistakes=False,
            refresh_target=controller.object_refresh_target(origin[0], origin[1], origin[2],
                                                            obj_id, radius=16)):
        return False

    option = leg.get("option")
    if not option:
        return True
    return pick_destination(ctx, option, log)


def pick_destination(ctx, option, log: str = "travel") -> bool:
    """Answer a transport's destination picker by the TSV's own destination name.

    The box's `Display info` is "3: Ferox Enclave" -- the digit is the interface's own shortcut
    and the text is the entry. The widget carrying that text is clicked. UNVERIFIED live; this
    is the assumption the whole POH chain rests on.
    """
    deadline = time.time() + PICKER_BUDGET_S
    while time.time() < deadline:
        dump = controller.get_widgets_dump() or {}
        widgets = dump.get("widgets") if isinstance(dump, dict) else dump
        for w in (widgets or []):
            text = (w.get("text") or "").strip()
            if option.lower() in text.lower() and w.get("screenX") is not None:
                debug_log.log(log, f"picking '{text}' from the destination menu")
                return bool(controller.click_entity(ctx.ser, w, check_ui_blockers=False,
                                                    simulate_mistakes=False))
        time.sleep(POLL_S)

    debug_log.log(log, f"destination menu never offered '{option}'")
    return False


# ---------------------------------------------------------------- transports (ladders, caves, stairs)

def find_transport_object(step):
    """The scene object a transport row means: by ID when the row has one, else by NAME, and
    always the match nearest the row's origin tile (a building has many identical staircases)."""
    ox, oy, plane = step.origin
    objects = controller.get_objects(radius=LINK_OBJECT_RADIUS * 3, fresh=True) or []

    def nearest(accept):
        matches = []
        for obj in objects:
            if obj.get("plane", plane) != plane or obj.get("screenX") is None or not accept(obj):
                continue
            distance = max(abs(obj.get("x", 0) - ox), abs(obj.get("y", 0) - oy))
            if distance <= LINK_OBJECT_RADIUS:
                matches.append((distance, obj))
        return min(matches, key=lambda m: m[0])[1] if matches else None

    if step.object_id is None:
        return nearest(lambda obj: (obj.get("name") or "").lower() == (step.target or "").lower())
    found = nearest(lambda obj: obj.get("id") == step.object_id)
    if found is not None:
        return found
    # The merged table can name ONE physical object by TWO ids: the jar's and Microbot's newer
    # fork's (MEASURED on the Grand Tree, reworked since the jar: Ladder 16683 and 4458, same
    # origin, same destination). The search takes the first row; a stale id there would read as
    # "not found" on every re-plan until the routine stopped. A sibling row's id is the same thing.
    siblings = {s.object_id for s in list(transports.load_links()) + list(transports.load_agility_shortcuts())
                if s.origin == step.origin and s.dest == step.dest and s.object_id not in (None, step.object_id)}
    return nearest(lambda obj: obj.get("id") in siblings) if siblings else None


def landed(step, player) -> bool:
    """Has `player` (a /player dict) come out the far side of `step`?

    Near the destination is NOT enough. FINAL REVIEW 2026-09-19, MEASURED: 610 of the 4312
    usable transports move the player three tiles or less on one plane (stiles, "Climb Rocks",
    the Fremennik dungeon's Strange floor, the Slayer Tower's broken window), so a player still
    standing at the ORIGIN is already "within 3 of the destination" -- the first poll confirmed
    a crossing that had not happened, the re-plan clicked the same obstacle again, and a click
    planned before the crossing could land after it and jump straight back. So the player must
    also be strictly NEARER the destination than the origin."""
    px, py = (player or {}).get("x"), (player or {}).get("y")
    if px is None or py is None or player.get("plane", step.dest[2]) != step.dest[2]:
        return False
    to_dest = max(abs(px - step.dest[0]), abs(py - step.dest[1]))
    if to_dest > LINK_ARRIVAL_TILES:
        return False
    if step.origin[2] != step.dest[2]:
        return True
    return to_dest < max(abs(px - step.origin[0]), abs(py - step.origin[1]))


def await_landing(step, budget_s: float = LINK_BUDGET_S) -> bool:
    deadline = time.time() + budget_s
    while time.time() < deadline:
        if landed(step, controller.get_player()):
            return True
        time.sleep(POLL_S)
    return False


def run_transport(ctx, step, log: str = "travel", approach=None) -> bool:
    """Take ONE Shortest Path transport: get beside its origin, click its object with the row's
    own menu option, and confirm we came out the far side (`landed`). The generic replacement for
    a hand-written ladder/cave/stairs step per site (spec §8.1). UNVERIFIED LIVE as of 2026-09-19.

    `approach` is crossfloor's own reachable tile at or beside the origin (Link.approach). It is
    tried FIRST: 141 usable transports have an origin our map calls solid and 87 of those have no
    walkable cardinal neighbour either, so without it crossfloor promised routes this could not
    walk. The exact origin and a goal-adjacent walk stay as fallbacks. The hover gate is safe
    here: the walk has finished, so the cursor reads the object's action from a standing player
    (CLAUDE.md warns only against gating clicks made WHILE walking up).

    Known limits, all of which fail CLEANLY (False, no retry loop): an object that is off-screen
    after the walk is reported "not found" -- no camera turn is attempted; a two-state object
    (a SHUT trapdoor is a different id with an `Open` action) never matches its row."""
    ox, oy, plane = step.origin
    player = controller.get_player() or {}
    px, py = player.get("x"), player.get("y")
    beside = (px is not None and player.get("plane", plane) == plane
              and max(abs(px - ox), abs(py - oy)) <= 2)
    if not beside:
        target = {"x": ox, "y": oy, "plane": plane}
        near = {"x": approach[0], "y": approach[1], "plane": approach[2]} if approach else None
        if not ((near is not None and travel.walk_to(ctx, near, goal_adjacent=False))
                or travel.walk_to(ctx, target, goal_adjacent=False)
                or travel.walk_to(ctx, target, goal_adjacent=True)):
            debug_log.log(log, f"couldn't walk to {step.object_info!r} at {step.origin}")
            return False

    obj = find_transport_object(step)
    if obj is None:
        debug_log.log(log, f"{step.object_info!r} not found near {step.origin}")
        return False

    debug_log.log(log, f"transport: {step.object_info} {step.origin} -> {step.dest}")
    refresh = None
    if obj.get("id") is not None:
        refresh = controller.object_refresh_target(obj["x"], obj["y"], plane, obj["id"],
                                                   radius=LINK_OBJECT_RADIUS * 3)
    ok = controller.click_entity(ctx.ser, obj, simulate_mistakes=False, refresh_target=refresh,
                                 expected_action=step.option)
    if not ok and getattr(ok, "withheld", False):
        debug_log.log(log, f"left-click on {step.target} isn't '{step.option}' -- using the menu")
        ok = controller.click_menu_action(ctx.ser, obj, step.option, refresh_target=refresh)
    if not ok:
        return False
    return await_landing(step, LINK_BUDGET_S)


def skill_levels():
    """{"Agility": 71, ...} from GET /skills (REAL levels -- a boost that wears off mid-route must
    not unlock a shortcut), or None when the bridge gave nothing, so requirements fail closed."""
    raw = controller.get_skills() or {}
    levels = {name: data.get("level") for name, data in raw.items()
              if isinstance(data, dict) and data.get("level") is not None}
    return levels or None


COINS_ITEM_ID = 995


def requirement_values() -> dict:
    """What this account can PROVE to the route search, read once per journey: the varbits and
    varplayers any link names (crossfloor.requirement_ids), the item ids carried or worn, and the
    coins carried. A value the bridge cannot read is LEFT OUT -- unknown fails closed in
    transports.usable -- and never written as 0; with no inventory read at all, no item and no
    coin is proven. Shapes are the real GETs of 2026-09-20: /inventory a list of {id, qty},
    /equipment a dict keyed by slot."""
    varbit_ids, varp_ids = crossfloor.requirement_ids()
    varbits, varps = {}, {}
    for vid in sorted(varbit_ids):
        value = controller.get_varbit(vid)
        if value is not None:
            varbits[vid] = value
    for vid in sorted(varp_ids):
        value = controller.get_varp(vid)
        if value is not None:
            varps[vid] = value
    inventory = controller.get_inventory()
    owned, currency = None, None
    if isinstance(inventory, list):
        owned = {item["id"] for item in inventory if isinstance(item, dict) and item.get("id") is not None}
        coins = sum(item.get("qty") or 0 for item in inventory
                    if isinstance(item, dict) and item.get("id") == COINS_ITEM_ID)
        currency = {"Coins": coins}
        equipment = controller.get_equipment()
        if isinstance(equipment, dict):
            owned |= {worn["id"] for worn in equipment.values()
                      if isinstance(worn, dict) and worn.get("id") is not None}
    return {"varbit_values": varbits, "varplayer_values": varps,
            "owned_item_ids": owned, "currency_held": currency}


def _undoes(step, previous) -> bool:
    """`step` lands back where `previous` started: same plane, within 2 tiles of its origin."""
    return (step.dest[2] == previous.origin[2]
            and max(abs(step.dest[0] - previous.origin[0]), abs(step.dest[1] - previous.origin[1])) <= 2)


def walk_across(ctx, goal, log: str = "travel") -> bool:
    """Walk to `goal` (x, y, plane), taking ladders, stairs and cave mouths as needed.

    The route is re-planned from the LIVE position after every transport: a plan is a hint, the
    tile we actually landed on is the truth. Bounded by WALK_ACROSS_MAX_LINKS so a transport
    that reports success without moving us cannot spin. UNVERIFIED LIVE as of 2026-09-19.

    The last, link-free stretch walks to crossfloor's own destination tile. That IS the exact
    goal whenever the goal is walkable (it was then in the flood); it is a tile up to
    GOAL_TOLERANCE away only when the exact goal is not -- a research cluster centre inside a
    wall -- where an exact attempt would just burn worldmap's whole 250k-node search (~0.8s,
    MEASURED) to fail. And when crossfloor finds nothing for a goal on our own plane and not a
    dungeon's y+6400 away, plain travel.walk_to still gets its try: its search is not bounded by
    FLOOD_RADIUS, so a long surface walk that worked before this module existed keeps working
    (it cannot wander into the Wilderness: MEASURED, find_path across the ditch is None).

    A link that would carry us straight BACK over the one just taken is refused rather than
    clicked: a re-plan starts from scratch, and a start snapped onto the wrong side of a short
    obstacle would otherwise bounce across it until the cap."""
    goal = tuple(goal)
    exact = {"x": goal[0], "y": goal[1], "plane": goal[2]}
    levels = skill_levels()
    proven = requirement_values()
    previous = None
    stepped_off = 0
    taken = 0
    for _ in range(WALK_ACROSS_MAX_LINKS + STEP_OFF_MAX + 1):
        player = controller.get_player() or {}
        if player.get("x") is None:
            return False
        here = (player["x"], player["y"], player.get("plane", 0))
        # PHASE-A REVIEW, proven on the real map: a teleport lands the player ON its own object (a
        # fairy ring), a tile the static map calls solid. crossfloor plans from the nearest
        # walkable neighbour, but travel.walk_to routes from the LIVE tile and finds nothing --
        # find_path from the AJQ exit ring is None, from one tile north it is 42 steps. So step
        # off first: one shift-click onto that neighbour, no pathing needed.
        foothold = crossfloor.walkable_start(here, toward=goal)
        if foothold != here:
            if stepped_off >= STEP_OFF_MAX:
                debug_log.log(log, f"still on an unroutable tile {here} after {stepped_off} step-offs")
                return False
            stepped_off += 1
            debug_log.log(log, f"{here} is not routable on the map -- stepping off to {foothold}")
            travel.walk_to_exact_tile(ctx, foothold[0], foothold[1], foothold[2])
            continue
        route = crossfloor.find_route(here, goal, skill_levels=levels, **proven)
        if route is None:
            if here[2] == goal[2] and abs(here[1] - goal[1]) < crossfloor.UNDERGROUND_DY // 2:
                debug_log.log(log, f"no cross-floor route from {here} to {goal} -- trying a plain walk")
                return bool(travel.walk_to(ctx, exact, goal_adjacent=False))
            debug_log.log(log, f"no route from {here} to {goal}")
            return False
        link = next((s for s in route.steps if isinstance(s, crossfloor.Link)), None)
        if link is None:
            dest = route.steps[-1].dest
            return bool(travel.walk_to(ctx, {"x": dest[0], "y": dest[1], "plane": dest[2]},
                                       goal_adjacent=False))
        if previous is not None and _undoes(link.step, previous):
            debug_log.log(log, f"refusing {link.step.object_info!r}: it leads straight back over "
                               f"{previous.object_info!r}")
            return False
        if taken >= WALK_ACROSS_MAX_LINKS:
            break
        taken += 1
        if not run_transport(ctx, link.step, log=log, approach=link.approach):
            return False
        previous = link.step
    debug_log.log(log, f"gave up after {WALK_ACROSS_MAX_LINKS} transports without reaching {tuple(goal)}")
    return False
