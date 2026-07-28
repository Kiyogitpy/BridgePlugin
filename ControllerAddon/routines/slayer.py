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


# Use the bridge's /config as-is -- this routine has no config of its own.
def get_config(bridge_config):
    return bridge_config


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
    maintain_prayer(ctx, combat)
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


def maintain_prayer(ctx, combat: dict):
    config = ctx.state.config
    prayer_cfg = config.get("prayer", {})
    if not prayer_cfg.get("enabled"):
        return

    prayers = controller.get_prayers()
    player = controller.get_player()

    if prayer_cfg.get("useQuickPrayers"):
        want_on = bool(combat.get("inCombat"))
        if random.random() < settings.MISTAKE_PRAYER_FUMBLE_CHANCE:
            # Fumble the toggle -- click the orb as if we meant the opposite
            # state. Self-corrects on a later tick once want_on/is_on
            # disagree again for real, same as a human noticing and
            # re-clicking a moment later.
            want_on = not want_on
        is_on = prayers.get("quickPrayerEnabled", False)
        if want_on != is_on:
            ox, oy = prayers.get("quickOrbScreenX"), prayers.get("quickOrbScreenY")
            if ox is not None and oy is not None:
                controller.click_entity(ctx.ser, {"screenX": ox, "screenY": oy}, offset=(0, 0))
    elif not ctx.state.data.get("warned_no_individual_prayers"):
        print("[prayer] usePrayer is on with useQuickPrayers off -- individual prayer "
              "icons aren't exposed with screen coordinates by the bridge yet, so only "
              "quick prayers can be toggled automatically. Enable quick prayers in "
              "Controller Settings and set up the matching quick-prayer loadout in-game.")
        ctx.state.data["warned_no_individual_prayers"] = True

    potions_cfg = config.get("potions", {})
    if potions_cfg.get("prayerEnabled"):
        restore_at = prayer_cfg.get("restoreAt", 18)
        current_prayer, prayer_max = player.get("prayer"), player.get("prayerReal")
        if current_prayer is None:
            return

        should_drink = current_prayer <= restore_at
        mistake = False
        if not should_drink and prayer_max and current_prayer >= prayer_max * settings.MISTAKE_OVERDRINK_MIN_PRAYER_FRACTION \
                and random.random() < settings.MISTAKE_OVERDRINK_CHANCE:
            should_drink, mistake = True, True  # wastes a dose we didn't actually need yet

        if should_drink and controller.consume_first(ctx.ser, controller.get_consumables().get("prayer", [])):
            print(f"[prayer] {'(mistake) ' if mistake else ''}drank prayer restore (prayer={current_prayer})")


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

        if should_eat and controller.consume_first(ctx.ser, controller.get_consumables().get("food", [])):
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
