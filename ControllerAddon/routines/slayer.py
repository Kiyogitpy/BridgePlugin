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

import controller


# Use the bridge's /config as-is -- this routine has no config of its own.
def get_config(bridge_config):
    return bridge_config


def on_event(ctx, event_type, payload):
    if event_type == "NPC_DIED" and payload:
        if payload.get("id") == ctx.state.data.get("current_target_id"):
            ctx.state.data["current_target_id"] = None


def tick(ctx):
    config = ctx.state.config

    ammo = controller.get_ammo_count()
    if ammo is not None and ammo <= 0:
        ctx.state.trigger_safety_stop("out of ammo")
        return

    combat = controller.get_combat()
    maintain_prayer(ctx, combat)
    maintain_supplies(ctx)

    if config.get("loot", {}).get("enabled"):
        maybe_loot(ctx)

    engage_target(ctx, combat)


# ---------------- config-driven targeting ----------------
def resolve_targets(config: dict):
    ids = set(config.get("targets") or [])
    names = {n.lower() for n in (config.get("targetNames") or [])}
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
        if npc.get("id") not in ids and (npc.get("name") or "").lower() not in names:
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

    if prayer_cfg.get("useQuickPrayers"):
        want_on = bool(combat.get("inCombat"))
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
        current_prayer = controller.get_player().get("prayer")
        if current_prayer is not None and current_prayer <= restore_at:
            if controller.consume_first(ctx.ser, controller.get_consumables().get("prayer", [])):
                print(f"[prayer] drank prayer restore (prayer={current_prayer})")


def maintain_supplies(ctx):
    config = ctx.state.config
    food_cfg = config.get("food", {})
    potions_cfg = config.get("potions", {})

    player = controller.get_player()
    hp = player.get("hp")

    if food_cfg.get("enabled") and hp is not None and hp <= food_cfg.get("eatAt", 0):
        if controller.consume_first(ctx.ser, controller.get_consumables().get("food", [])):
            print(f"[supplies] ate food (hp={hp})")
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
    if whitelist:
        for item in controller.get_ground_items():
            name = (item.get("name") or "").lower()
            if item.get("clickable") and any(w in name for w in whitelist):
                print(f"[loot] picking up {item.get('name')}")
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

    current_target_id = ctx.state.data.get("current_target_id")
    if current_target_id is not None:
        still_alive = any(
            n.get("id") == current_target_id and n.get("hpRatio") != 0
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
        ctx.state.data["current_target_id"] = None

    if config.get("onlyAttackWhenIdle") and not combat.get("isIdle", True):
        return

    target = pick_target(npcs, config)
    if not target:
        return

    print(f"[combat] attacking {target.get('name')} (id={target['id']}, dist={target.get('distance')})")
    time.sleep(random.uniform(0.15, 0.6))
    target_id = target["id"]
    if controller.click_entity(
        ctx.ser,
        target,
        refresh_target=lambda: controller.get_npc_screen_pos(target_id),
    ):
        ctx.state.data["current_target_id"] = target_id
