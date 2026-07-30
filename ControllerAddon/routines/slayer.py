# routines/slayer.py
#
# Combat/slayer routine: pick a target from the Controller Settings panel's
# monster list, fight it, keep prayer/food/potions topped up, loot, bury
# bones. Config comes straight from the bridge's GET /config (i.e. whatever
# is set in the RuneLite "Controller" side panel) -- see
# com.example.controller.ControllerConfig / ConfigExporter for the shape.
#
# Run with:
#     python controller.py routines/slayer.py

import random
import time

import banker
import controller
import settings
import travel


# GET /slayer-config already includes the master switch (SlayerConfigProvider reads it off
# ControllerConfig too) alongside every slayer-specific setting -- bridge_config (GET /config)
# isn't needed here.
def get_config(bridge_config):
    return controller.get_slayer_config()


def on_event(ctx, event_type, payload):
    if event_type == "NPC_DIED" and payload:
        # Matched by instance index, not type id -- a different monster of
        # the same species dying nearby must not clear our current target.
        if payload.get("index") == ctx.state.data.get("current_target_index"):
            ctx.state.data["current_target_index"] = None


def tick(ctx):
    config = ctx.state.config

    ammo = controller.get_ammo_count()
    if ammo is not None and ammo <= 0:
        ctx.state.trigger_safety_stop("out of ammo")
        return

    bank_cfg = config.get("bank", {})
    if bank_cfg.get("enabled") and needs_banking(ctx):
        print("[bank] inventory full, banking")
        ctx.state.data["current_target_index"] = None  # don't try to resume a fight through a bank trip
        banker.bank_up(ctx, bank_cfg)
        return

    combat = controller.get_combat()
    maintain_defensive_prayer(ctx, combat)
    maintain_offensive_prayer(ctx, combat)
    maintain_supplies(ctx)

    if config.get("loot", {}).get("enabled"):
        maybe_loot(ctx)

    engage_target(ctx, combat)


def inventory_full() -> bool:
    return len(controller.get_inventory()) >= settings.INVENTORY_SIZE


def needs_banking(ctx) -> bool:
    if not inventory_full():
        return False

    if not has_bankable_loot(ctx.state.config):
        # Full, but only with supplies we brought on purpose (food, potions,
        # the ring, the bag, whatever's on the keep list) -- there's nothing
        # to gain from a bank trip, we'd just deposit and rewithdraw the same
        # things. This is also what stops banking from firing immediately on
        # a trip that simply started with a full inventory of supplies.
        return False

    return True


def has_bankable_loot(config: dict) -> bool:
    # Substring match, not exact -- item names carry dose/charge suffixes
    # (e.g. "Super restore(4)") that a configured preference like "Super
    # restore" won't equal exactly. Matches the same convention
    # ConsumablesProvider uses server-side.
    protected_names = protected_supply_names(config)
    for item in controller.get_inventory():
        name = (item.get("name") or "").lower()
        if name and not any(protected in name for protected in protected_names):
            return True
    return False


def protected_supply_names(config: dict) -> set:
    names = {n.lower() for n in config.get("bank", {}).get("keepItems", [])}

    food_name = config.get("food", {}).get("name")
    if food_name:
        names.add(food_name.lower())

    prayer_potion = config.get("potions", {}).get("prayerPotion")
    if prayer_potion:
        names.add(prayer_potion.lower())

    combat_potion = config.get("potions", {}).get("combatPotion")
    if combat_potion:
        names.add(combat_potion.lower())

    names.add(travel.RING_OF_DUELING_NAME)
    names.add(travel.ROYAL_SEED_POD_NAME)
    names.add(banker.LOOTING_BAG_NAME)
    return names


# ---------------- config-driven targeting ----------------
def _singularize(word: str) -> str:
    """Best-effort strip of a plural task-category label (e.g. as picked from
    the Controller Settings search bar's SlayerMonsters.java list) down to a
    form likely to appear inside the real, singular NPC name it actually
    covers -- "Bloodvelds" -> "bloodveld", "Zombies" -> "zombie", "Lizardmen"
    -> "lizardman" (irregular -men/-man plural). Slayer task category labels
    are frequently NOT literal NPC names at all (there's no NPC actually
    named "Lizardmen" -- the real ones are "Lizardman shaman", "Lizardman
    brute", etc.), so pick_target below matches substrings against this
    stripped form rather than requiring an exact name match."""
    w = word.lower().strip()
    if w.endswith("men") and not w.endswith("omen"):
        return w[:-3] + "man"
    if w.endswith("ies") and len(w) > 3:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def resolve_targets(config: dict):
    ids = set(config.get("targets") or [])
    names = set()
    for n in (config.get("targetNames") or []):
        n_lower = n.lower().strip()
        if not n_lower:
            continue
        names.add(n_lower)
        names.add(_singularize(n_lower))
    return ids, names


def pick_target(npcs: list, config: dict):
    ids, names = resolve_targets(config)
    if not ids and not names:
        return None

    max_dist = config.get("maxTargetDistance", 15)
    skip_in_combat = config.get("skipNpcInCombat", True)

    candidates = []
    for npc in npcs:
        if not npc.get("attackable"):
            continue
        npc_name = (npc.get("name") or "").lower()
        if npc.get("id") not in ids and not any(n in npc_name for n in names if n):
            continue

        dist = npc.get("distance", 9999)
        if dist < 0 or dist > max_dist:
            continue

        if skip_in_combat and npc.get("inCombat") and not npc.get("interactingPlayer"):
            continue

        candidates.append(npc)

    if not candidates:
        return None

    candidates.sort(key=lambda n: n.get("distance", 9999))
    return candidates[0]


# Prayer level requirements, mirroring com.example.controller.DefensivePrayerChoice /
# OffensivePrayerChoice on the Java side (keep both in sync if either changes) --
# standard OSRS prayer levels, not re-verified against a live client but stable/
# well-known. Used to skip a manually-picked prayer the account hasn't unlocked yet
# (rather than looping forever re-clicking a locked icon) and to resolve the three
# Auto entries below.
PRAYER_LEVELS = {
    "PROTECT_FROM_MAGIC": 37,
    "PROTECT_FROM_MISSILES": 40,
    "PROTECT_FROM_MELEE": 43,
    "STEEL_SKIN": 28,
    "ULTIMATE_STRENGTH": 31,
    "INCREDIBLE_REFLEXES": 34,
    "CHIVALRY": 60,
    "PIETY": 70,
    "EAGLE_EYE": 44,
    "DEADEYE": 62,
    "RIGOUR": 74,
    "MYSTIC_LORE": 27,
    "MYSTIC_MIGHT": 45,
    "AUGURY": 77,
}

# Best-to-worst per combat style -- AUTO_* resolves to the first entry the account's
# prayer level actually covers. DEADEYE is deliberately not in AUTO_RANGED's ladder:
# unlocking it needs a Royal Titans scroll drop on top of the level requirement,
# which this level-only check can't verify -- accounts that have it unlocked can
# pick it directly instead of via Auto.
AUTO_PRAYER_LADDERS = {
    "AUTO_MELEE": ["PIETY", "CHIVALRY", "STEEL_SKIN"],
    "AUTO_RANGED": ["RIGOUR", "EAGLE_EYE"],
    "AUTO_MAGIC": ["AUGURY", "MYSTIC_MIGHT", "MYSTIC_LORE"],
}

# maintain_offensive_prayer gives up trying to click the flick-off icon (and just clears its
# own tracking) after this many ticks of the icon lookup failing, rather than staying stuck
# unable to ever detect "off" again.
OFFENSIVE_PRAYER_STUCK_ON_TICKS = 3


def resolve_combat_prayer(combat_prayer: str, prayer_level):
    """Resolve a Defensive/Offensive Prayer config value down to a concrete prayer name
    (a key into PrayerProvider's active/icons maps), or None if nothing should be active
    right now. Auto (Melee/Ranged/Magic) walks its ladder and returns the first entry the
    account's level covers. A manually-picked prayer the account hasn't unlocked yet also
    resolves to None -- surfaced once via the caller's own warning, rather than silently
    retried forever."""
    if not combat_prayer or combat_prayer == "NONE":
        return None

    ladder = AUTO_PRAYER_LADDERS.get(combat_prayer)
    if ladder is not None:
        if prayer_level is None:
            return None
        for candidate in ladder:
            if prayer_level >= PRAYER_LEVELS.get(candidate, 999):
                return candidate
        return None

    required = PRAYER_LEVELS.get(combat_prayer)
    if required is not None and (prayer_level is None or prayer_level < required):
        return None

    return combat_prayer


def maintain_defensive_prayer(ctx, combat: dict):
    """Keeps a single defensive (Protect from X) prayer on for the whole fight --
    LAZY behavior (see docs/superpowers/specs/2026-07-29-split-defensive-offensive-prayer-design.md),
    unlike maintain_offensive_prayer's flick-on/flick-off. Also drives prayer-potion
    upkeep, since that's independent of which prayer(s) are actually selected."""
    config = ctx.state.config
    prayer_cfg = config.get("prayer", {})
    if not prayer_cfg.get("enabled"):
        return

    prayers = controller.get_prayers()
    player = controller.get_player()

    raw_defensive_prayer = prayer_cfg.get("defensivePrayer", "NONE")
    defensive_prayer = resolve_combat_prayer(raw_defensive_prayer, player.get("prayerReal"))

    if prayer_cfg.get("useQuickPrayers"):
        want_on = bool(combat.get("inCombat"))
        is_on = prayers.get("quickPrayerEnabled", False)
        if want_on != is_on:
            ox, oy = prayers.get("quickOrbScreenX"), prayers.get("quickOrbScreenY")
            if ox is not None and oy is not None:
                controller.click_entity(ctx.ser, {"screenX": ox, "screenY": oy}, offset=(0, 0))
    elif defensive_prayer:
        want_on = bool(combat.get("inCombat"))
        is_on = prayers.get("active", {}).get(defensive_prayer, False)
        if want_on != is_on:
            # Prayer icons only get live screen coords while the Prayer tab is open --
            # switch to it, then re-fetch (the "prayers" snapshot above was taken before
            # we knew we'd need it, so its "icons" may still be from whatever tab was
            # open a moment ago).
            controller.open_prayer_tab(ctx.ser)
            icon = controller.get_prayers().get("icons", {}).get(defensive_prayer)
            if icon:
                print(f"[prayer] {'turning on' if want_on else 'turning off'} {defensive_prayer}")
                controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
    elif raw_defensive_prayer in (None, "", "NONE"):
        if not ctx.state.data.get("warned_no_defensive_prayer"):
            print("[prayer] usePrayer is on with useQuickPrayers off, but Defensive Prayer is "
                  "set to None in Controller Settings -- nothing to toggle. Pick a prayer there "
                  "(e.g. Protect from Melee) or enable Use Quick Prayers instead.")
            ctx.state.data["warned_no_defensive_prayer"] = True
    elif not ctx.state.data.get("warned_defensive_prayer_level_too_low"):
        # Either a manually-picked prayer this account hasn't unlocked, or an Auto ladder
        # with nothing on it affordable yet -- either way, nothing usable right now. Surfaced
        # once rather than resolve_combat_prayer's None silently looking identical to "nothing
        # configured", which would otherwise be confusing to debug from the logs alone.
        print(f"[prayer] Defensive Prayer is set to {raw_defensive_prayer} in Controller "
              f"Settings, but nothing in that selection is usable yet at prayer level "
              f"{player.get('prayerReal')} -- nothing to toggle until it's leveled up.")
        ctx.state.data["warned_defensive_prayer_level_too_low"] = True

    potions_cfg = config.get("potions", {})
    if potions_cfg.get("prayerEnabled"):
        restore_at = prayer_cfg.get("restoreAt", 18)
        current_prayer, prayer_max = player.get("prayer"), player.get("prayerReal")
        if current_prayer is None:
            return

        # Drinking at the exact same prayer point every time is a tell -- roll a fresh
        # threshold somewhere in [restore_at, restore_at + spread] instead, so some fights
        # get topped up right at the floor and others earlier, same as a real player
        # glancing at the orb rather than watching it like a hawk. Sticks until a drink
        # actually happens, then re-rolls for next time.
        threshold = ctx.state.data.get("prayer_drink_threshold")
        if threshold is None:
            # Clamp below prayer_max -- otherwise a low real Prayer level (restore_at + spread
            # exceeding max) would make should_drink permanently True instead of "occasionally
            # a bit early".
            spread_max = restore_at + settings.PRAYER_RESTORE_SPREAD
            if prayer_max:
                spread_max = min(spread_max, prayer_max)
            threshold = random.uniform(restore_at, max(restore_at, spread_max))
            ctx.state.data["prayer_drink_threshold"] = threshold

        should_drink = current_prayer <= threshold
        mistake = False
        if not should_drink and prayer_max and current_prayer >= prayer_max * settings.MISTAKE_OVERDRINK_MIN_PRAYER_FRACTION \
                and random.random() < settings.MISTAKE_OVERDRINK_CHANCE:
            should_drink, mistake = True, True  # wastes a dose we didn't actually need yet

        if should_drink and controller.consume_first(ctx.ser, "prayer"):
            print(f"[prayer] {'(mistake) ' if mistake else ''}drank prayer restore "
                  f"(prayer={current_prayer}, threshold={threshold:.0f})")
            ctx.state.data["prayer_drink_threshold"] = None


def maintain_offensive_prayer(ctx, combat: dict):
    """Flicks a single offensive (stat-boosting) prayer on right before the player's
    own attack lands and off once it registers -- LAZY_FLICK-style behavior (see
    docs/superpowers/specs/2026-07-29-split-defensive-offensive-prayer-design.md),
    scoped down from the full damage-prediction system a reference community fork
    (ZombieLite) uses for the same idea, since this project has no equivalent and
    can't reliably hit tick-perfect timing through external input anyway. Requires
    the Attack Timer Metronome plugin to be loaded -- if combat.ticksUntilNextAttack
    is ever absent, this is a permanent no-op (never guesses a fallback timing)."""
    config = ctx.state.config
    prayer_cfg = config.get("prayer", {})
    if not prayer_cfg.get("enabled"):
        return

    raw_offensive_prayer = prayer_cfg.get("offensivePrayer", "NONE")
    player = controller.get_player()
    offensive_prayer = resolve_combat_prayer(raw_offensive_prayer, player.get("prayerReal"))
    if not offensive_prayer:
        return

    ticks_until_attack = combat.get("ticksUntilNextAttack")
    if ticks_until_attack is None:
        return

    prayers = controller.get_prayers()
    is_on = prayers.get("active", {}).get(offensive_prayer, False)
    hp_xp = player.get("hitpointsXp")
    flicked_on_tick = ctx.state.data.get("offensive_prayer_on_since_tick")

    if not is_on:
        # Triggers a couple of ticks out rather than right on the last one -- live timing
        # measurements (open-tab + click round trip through the bridge) showed 1-2 game
        # ticks (525-1121ms) between the decision and the click actually landing, so firing
        # at ticks_until_attack == 1 routinely landed the boost a tick after the attack it
        # was meant to cover. 2 ticks of lead covers the observed worst case with a little
        # margin to spare.
        if ticks_until_attack <= 2 and combat.get("inCombat"):
            controller.open_prayer_tab(ctx.ser)
            icon = controller.get_prayers().get("icons", {}).get(offensive_prayer)
            if icon:
                print(f"[prayer] flicking on {offensive_prayer}")
                controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
                ctx.state.data["offensive_prayer_on_since_tick"] = combat.get("tick")
                ctx.state.data["offensive_prayer_hp_xp_at_flick"] = hp_xp
        return

    hp_xp_at_flick = ctx.state.data.get("offensive_prayer_hp_xp_at_flick")
    hit_landed = hp_xp is not None and hp_xp_at_flick is not None and hp_xp > hp_xp_at_flick
    tick_passed = flicked_on_tick is not None and combat.get("tick") is not None and combat.get("tick") > flicked_on_tick
    # is_on but we never tracked flicking it on ourselves (e.g. a script restart mid-flick) --
    # we have no idea how long it's actually been on, so treat it the same as "time to turn
    # it off" rather than leaving it lit with nothing ever able to notice.
    untracked = flicked_on_tick is None

    if hit_landed or tick_passed or untracked:
        controller.open_prayer_tab(ctx.ser)
        icon = controller.get_prayers().get("icons", {}).get(offensive_prayer)
        cleared = False
        if icon:
            reason = "hit landed" if hit_landed else ("untracked" if untracked else "attack tick passed")
            print(f"[prayer] flicking off {offensive_prayer} ({reason})")
            controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)
            cleared = True
        elif tick_passed and combat.get("tick") - flicked_on_tick >= OFFENSIVE_PRAYER_STUCK_ON_TICKS:
            # The icon lookup has failed for several ticks running -- clear our own tracking
            # anyway rather than staying permanently unable to ever detect "off" again. The
            # prayer may stay lit in-game a tick or two longer than intended, but that beats
            # draining prayer points forever with nothing able to notice.
            print(f"[prayer] giving up on flicking off {offensive_prayer} -- icon not found for "
                  f"{OFFENSIVE_PRAYER_STUCK_ON_TICKS}+ ticks, clearing tracked state anyway")
            cleared = True

        if cleared:
            ctx.state.data["offensive_prayer_on_since_tick"] = None
            ctx.state.data["offensive_prayer_hp_xp_at_flick"] = None


def maintain_supplies(ctx):
    config = ctx.state.config
    food_cfg = config.get("food", {})
    potions_cfg = config.get("potions", {})

    player = controller.get_player()
    hp, hp_max = player.get("hp"), player.get("hpReal")

    if food_cfg.get("enabled") and hp is not None:
        should_eat = hp <= food_cfg.get("eatAt", 0)
        mistake = False
        if not should_eat and hp_max and hp >= hp_max * settings.MISTAKE_OVEREAT_MIN_HP_FRACTION \
                and random.random() < settings.MISTAKE_OVEREAT_CHANCE:
            should_eat, mistake = True, True  # eats a bite we didn't actually need, e.g. right up near full hp

        if should_eat and controller.consume_first(ctx.ser, "food"):
            print(f"[supplies] {'(mistake) ' if mistake else ''}ate food (hp={hp})")
            return  # avoid eating and drinking in the same tick

    if potions_cfg.get("combatEnabled"):
        # The bridge doesn't expose buff/potion timers yet (no varbit/timer
        # endpoint), so we can't tell when a combat potion has expired.
        # Left as a no-op until a BuffProvider-style endpoint exists.
        pass


def maybe_loot(ctx):
    config = ctx.state.config
    loot_cfg = config.get("loot", {})

    whitelist = [w.lower() for w in loot_cfg.get("items", [])]
    min_value = loot_cfg.get("minValue", 0)

    for item in controller.get_ground_items():
        if not item.get("clickable"):
            continue

        name = (item.get("name") or "").lower()
        name_match = bool(whitelist) and any(w in name for w in whitelist)
        # totalValue is GE price * quantity, computed server-side (GroundItemProvider).
        value_match = min_value > 0 and item.get("totalValue", 0) >= min_value

        if name_match or value_match:
            reason = "whitelist" if name_match else f"worth {item.get('totalValue')}gp"
            print(f"[loot] picking up {item.get('name')} ({reason})")
            time.sleep(random.uniform(0.2, 0.5))
            controller.click_entity(ctx.ser, item)
            break

    if loot_cfg.get("buryBones"):
        for item in controller.get_inventory():
            name = (item.get("name") or "").lower()
            if "bones" in name:
                print(f"[loot] burying {item.get('name')}")
                controller.click_entity(ctx.ser, item)
                break


def engage_target(ctx, combat: dict):
    config = ctx.state.config
    npcs = controller.get_npcs()

    # Tracked by instance index (stable per-monster identifier), not the
    # shared type id -- in a pack of identical monsters, "any NPC of this
    # type is alive" can't tell your target apart from its neighbors.
    current_target_index = ctx.state.data.get("current_target_index")
    if current_target_index is not None:
        still_alive = any(
            n.get("index") == current_target_index and n.get("hpRatio") != 0
            for n in npcs
        )
        if still_alive:
            # Committed until this one dies. Some areas enforce strict 1v1
            # combat and reject a second target with "you are already
            # attacking a different monster" -- combat.inCombat can read
            # False for a tick between attack animations even while the zone
            # still has us locked, so don't use it to decide whether to
            # re-engage; only "is our tracked target still alive" matters.
            return
        ctx.state.data["current_target_index"] = None

    if config.get("onlyAttackWhenIdle") and not combat.get("isIdle", True):
        return

    target = pick_target(npcs, config)
    if not target:
        return

    print(
        f"[combat] attacking {target.get('name')} "
        f"(id={target['id']}, index={target['index']}, dist={target.get('distance')})"
    )
    time.sleep(random.uniform(0.15, 0.6))
    target_index = target["index"]
    if controller.click_entity(
        ctx.ser,
        target,
        refresh_target=lambda: controller.get_npc_screen_pos_by_index(target_index),
    ):
        ctx.state.data["current_target_index"] = target_index
