# routines/tormented_demon.py
#
# Solo Tormented Demon fight (Wilderness God Wars Dungeon). Reference material for this came
# from reading (not copying/reusing code from) a community RuneLite fork's Tormented Demon
# assist plugin (com.salvation.plugins.tormenteddemons in a local ZombieLite checkout) -- that
# plugin is an *overlay/assist* tool, not an autonomous bot: it predicts the demon's next
# attack and draws a warning overlay for a human to react to, and it never auto-swaps gear
# either. This routine reimplements the same ideas (attack-type prediction -> prayer
# switching, fireball-tile detection -> dodge, overhead-icon -> weakness) as genuine
# autonomous actions using this project's own external-input model (real bridge state reads +
# real Arduino clicks), not that plugin's mechanism (RuneLite Client-injected packets, which
# doesn't apply here at all).
#
# ASSUMPTIONS / LIMITATIONS TO KNOW BEFORE RUNNING THIS:
#   - Reaction speed: attack-type prediction here is REACTIVE (switches prayer the instant the
#     demon's attack animation is observed), not predictive ahead of the tell the way the
#     reference plugin's more advanced pose/distance heuristics are. Combined with this
#     project's poll cadence (STATE_POLL_INTERVAL) and real mouse-travel time, there is a real
#     chance of eating an occasional hit through a prayer switch that lands a beat late --
#     this is inherent to reacting through external input rather than an injected client, not
#     a bug to chase out.
#   - autoSwapGear (off by default) depends on NPCProvider's "overheadIcon" field, which is
#     read via UnsafeNpcOverheadIcon -- reflection into obfuscated RuneLite internals, since
#     the public Client API has no NPC overhead-icon getter at all. That field can silently
#     stop working (return nothing) after a game/RuneLite update with no error to signal it.
#     Leave autoSwapGear off to just fight the demon with one fixed combatStyle the whole
#     time instead (worse DPS on off-style ticks -- it still takes some chip damage while
#     shielded -- but zero dependency on that reflection path).
#   - The fireball dodge (see maybe_dodge_fireball) reacts to ANY graphics object matching
#     FIREBALL_GRAPHICS_OBJECT_ID landing on the player's own tile; that ID is sourced from
#     the same community fork, not independently verified against an official RuneLite
#     constant (unlike the animation IDs above it, which are verified against
#     net.runelite.api.gameval.AnimationID in this project's local RuneLite checkout) -- watch
#     the first few live fireballs to confirm this actually fires before trusting it.
#   - Only the two real, repeatable-encounter NPC ids are targeted (13599/13600). The
#     community fork's table also listed 13601/13602 as further combat-level variants, but
#     RuneLite's own official NpcID constants name those TORMENTED_DEMON_QUEST_1/2 -- the
#     Nomad's Requiem quest fight, a different encounter. Deliberately excluded here.
#   - No pathing/positioning logic beyond the fireball dodge -- walk to the demon yourself
#     before starting this routine.
#
# Settings (combat style, auto swap gear, weapon loadouts, food/prayer thresholds, breaks,
# safety) live in their own RuneLite panel -- see
# com.example.controller.TormentedDemonConfig/TormentedDemonPlugin -- exposed over the bridge
# at GET /tormented-demon-config. npcIds is not part of that panel: it's the two
# correctness-verified NPC ids above, not something to edit from the UI.
#
# Run with:
#     python controller.py routines/tormented_demon.py

import random
import time

import controller
import debug_log

# net.runelite.api.gameval.AnimationID.LUC2_UNDEAD_DEMON_* -- verified against this project's
# local RuneLite checkout, not just taken on faith from the reference fork.
ANIM_MAGIC = 11388
ANIM_RANGED = 11389
ANIM_MELEE = 11392
ANIM_FIREBALL = 11387
ANIM_SUMMON = 11395
ANIM_DEATH = 11394

# See the module header -- sourced from a community fork, not independently verified.
FIREBALL_GRAPHICS_OBJECT_ID = 2856

# Real, repeatable overworld encounter only -- see module header for why the fork's other two
# "variants" are excluded.
DEFAULT_NPC_IDS = [13599, 13600]

ATTACK_ANIMATION_TO_PRAYER = {
    ANIM_MELEE: "PROTECT_FROM_MELEE",
    ANIM_RANGED: "PROTECT_FROM_MISSILES",
    ANIM_MAGIC: "PROTECT_FROM_MAGIC",
}

OVERHEAD_ICON_TO_STYLE = {
    "MELEE": "melee",
    "RANGED": "ranged",
    "MAGIC": "magic",
}

# Used only if TormentedDemonPlugin isn't loaded/enabled in RuneLite (so
# /tormented-demon-config comes back None) -- keeps this routine runnable standalone, same as
# before this had a settings panel.
DEFAULT_CONFIG = {
    "enabled": True,
    "npcIds": DEFAULT_NPC_IDS,
    "maxTargetDistance": 20,

    # Fixed fighting style used whenever autoSwapGear is off, or a given tick's weakness
    # reading is unavailable. One of "melee", "ranged", "magic".
    "combatStyle": "melee",

    # Off by default -- see module header. When on, automatically re-equips the matching
    # weapon in "loadouts" below as soon as the demon's overhead icon (weakness) changes.
    "autoSwapGear": False,
    "loadouts": {
        "melee": None,   # e.g. "Ghrazi rapier" -- exact inventory item name
        "ranged": None,  # e.g. "Toxic blowpipe"
        "magic": None,   # e.g. "Sanguinesti staff" -- assumes Autocast is already configured
                          # in-game for this staff; this routine has no right-click/menu
                          # support to set that up itself, only to equip the weapon.
    },

    "food": {"enabled": True, "eatAt": 50},
    "prayer": {"restoreAt": 30},

    "breaks": {
        "enabled": True,
        "everyMinMinutes": 30,
        "everyMaxMinutes": 50,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 10,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        "stopOnInventoryFull": False,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    config = controller.get_tormented_demon_config()
    if config is None:
        return DEFAULT_CONFIG

    # npcIds is intentionally not part of the settings panel -- see module header. Always
    # comes from the verified constant, regardless of what the bridge returned.
    config = dict(config)
    config["npcIds"] = DEFAULT_NPC_IDS
    return config


def setup(ctx):
    """Equip the configured starting loadout once before the fight begins -- combatStyle is
    also the style maybe_swap_gear falls back toward whenever a weakness reading isn't
    available, so this is what's actually fought with the whole time when autoSwapGear is
    off. ctx.state.config isn't populated yet at this point in the routine-switch lifecycle
    (setup() runs before the first get_config() resolution for this routine), so resolve the
    same way get_config() will, rather than reading ctx.state.config here."""
    config = get_config(None)
    style = config.get("combatStyle", "melee")
    weapon_name = (config.get("loadouts", {}) or {}).get(style)
    if not weapon_name:
        return

    equipped = (controller.get_equipment().get("weapon", {}) or {}).get("name") or ""
    if weapon_name.lower() in equipped.lower():
        ctx.state.data["current_style"] = style
        return

    controller.open_inventory_tab(ctx.ser)
    item = next(
        (i for i in controller.get_inventory() if weapon_name.lower() in (i.get("name") or "").lower()),
        None,
    )
    if item is None:
        debug_log.log("td", f"configured combatStyle is {style} ({weapon_name}) but it isn't in "
                            f"inventory -- fighting with whatever's currently equipped instead")
        return

    debug_log.log("td", f"equipping starting loadout: {weapon_name} ({style})")
    controller.click_entity(ctx.ser, item, check_ui_blockers=False)
    ctx.state.data["current_style"] = style


def tick(ctx):
    config = ctx.state.config

    player = controller.get_player()
    hp = player.get("hp")
    if hp is not None and config.get("food", {}).get("enabled") and hp <= config["food"].get("eatAt", 50):
        if controller.consume_first(ctx.ser, "food"):
            debug_log.log("td", f"ate food (hp={hp})")
            return  # matches slayer.py's convention -- one supply action per tick

    npc = find_target(config)
    if npc is None:
        debug_log.log("td", "no Tormented Demon in range")
        return

    # Fireball dodge takes priority over everything else -- see module header on why this
    # reacts immediately rather than trying to time a precise fuse countdown.
    if maybe_dodge_fireball(ctx, player):
        return

    react_to_attack_animation(ctx, npc)

    # Temporarily disabled -- maybe_swap_gear depends on the overheadIcon field, which comes
    # from UnsafeNpcOverheadIcon's reflection-based read of RuneLite internals (see module
    # header). Re-enable once that's confirmed working again.
    # if config.get("autoSwapGear"):
    #     maybe_swap_gear(ctx, npc, config)

    maintain_prayer_potions(ctx, config)

    combat = controller.get_combat()
    if combat.get("isIdle", True):
        npc_index = npc["index"]
        controller.click_entity(
            ctx.ser,
            npc,
            refresh_target=lambda: controller.get_npc_screen_pos_by_index(npc_index),
        )


def find_target(config: dict):
    npc_ids = set(config.get("npcIds", DEFAULT_NPC_IDS))
    max_dist = config.get("maxTargetDistance", 20)

    candidates = [
        n for n in controller.get_npcs()
        if n.get("id") in npc_ids and n.get("hpRatio") != 0
        and 0 <= n.get("distance", 9999) <= max_dist
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda n: n.get("distance", 9999))
    return candidates[0]


def react_to_attack_animation(ctx, npc: dict) -> None:
    """React to the demon's current attack animation by switching to the matching protect
    prayer -- see module header re: this being reactive (after the tell starts), not
    predictive. No-ops for the fireball/summon/death/idle animations (nothing to protect
    against with a prayer for those)."""
    prayer_name = ATTACK_ANIMATION_TO_PRAYER.get(npc.get("animationId"))
    if prayer_name is None:
        return

    prayers = controller.get_prayers()
    if prayers.get("active", {}).get(prayer_name):
        return

    controller.open_prayer_tab(ctx.ser)
    icon = controller.get_prayers().get("icons", {}).get(prayer_name)
    if icon:
        debug_log.log("td", f"{npc.get('name')} is attacking -- switching to {prayer_name}")
        controller.click_entity(ctx.ser, icon, offset=(0, 0), check_ui_blockers=False, simulate_mistakes=False)


def maybe_swap_gear(ctx, npc: dict, config: dict) -> None:
    """Re-equip the weapon matching the demon's current weakness (its overhead icon). Only
    acts on an actual reading -- if UnsafeNpcOverheadIcon couldn't get one this tick, npc has
    no "overheadIcon" key at all, and this deliberately does nothing rather than guess."""
    icon = npc.get("overheadIcon")
    if icon is None:
        return

    style = OVERHEAD_ICON_TO_STYLE.get(icon)
    if style is None or style == ctx.state.data.get("current_style"):
        return

    weapon_name = (config.get("loadouts", {}) or {}).get(style)
    if not weapon_name:
        if not ctx.state.data.get(f"warned_no_{style}_loadout"):
            debug_log.log("td", f"weakness is now {style}, but no loadout weapon is configured for "
                                 f"it in DEFAULT_CONFIG['loadouts']['{style}'] -- staying on current weapon.")
            ctx.state.data[f"warned_no_{style}_loadout"] = True
        return

    equipped = (controller.get_equipment().get("weapon", {}) or {}).get("name") or ""
    if weapon_name.lower() in equipped.lower():
        ctx.state.data["current_style"] = style
        return

    controller.open_inventory_tab(ctx.ser)
    item = next(
        (i for i in controller.get_inventory() if weapon_name.lower() in (i.get("name") or "").lower()),
        None,
    )
    if item is None:
        debug_log.log("td", f"weakness is now {style} ({weapon_name}) but it isn't in inventory")
        return

    debug_log.log("td", f"weakness is now {style} -- switching to {weapon_name}")
    controller.click_entity(ctx.ser, item, check_ui_blockers=False)
    ctx.state.data["current_style"] = style
    controller.human_delay(0.3, 0.6)


def maybe_dodge_fireball(ctx, player: dict) -> bool:
    """True (and already moving) if a fireball graphics object is currently sitting on the
    player's own tile -- steps to a nearby tile immediately, no confirmation wait, same
    "speed over certainty" logic as controller.travel's PvP flee. Returns False (does nothing)
    if there's no fireball on our tile right now."""
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")
    if px is None:
        return False

    on_us = any(
        obj.get("id") == FIREBALL_GRAPHICS_OBJECT_ID
        and obj.get("x") == px and obj.get("y") == py and obj.get("plane") == plane
        for obj in controller.get_graphics_objects()
    )
    if not on_us:
        return False

    debug_log.log("td", "fireball incoming on our tile -- moving")
    for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
        point = controller.get_minimap_walk_point(px + dx, py + dy, plane)
        if point.get("onMinimap"):
            controller.click_entity(ctx.ser, point, offset=(0, 0), simulate_mistakes=False)
            return True

    debug_log.log("td", "couldn't find a dodge tile on the minimap")
    return True


def maintain_prayer_potions(ctx, config: dict) -> None:
    prayer_cfg = config.get("prayer", {})
    restore_at = prayer_cfg.get("restoreAt", 30)

    player = controller.get_player()
    current_prayer = player.get("prayer")
    if current_prayer is None or current_prayer > restore_at:
        return

    if controller.consume_first(ctx.ser, "prayer"):
        debug_log.log("td", f"drank prayer restore (prayer={current_prayer})")
