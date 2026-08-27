# routines/mining.py
#
# Minimal skilling routine, shown as an example of what a non-combat profile
# looks like: click the nearest matching rock, wait for the mining animation
# to finish, repeat. There's no RuneLite settings panel for this yet (that
# only exists for the combat/slayer config), so it just keeps its own
# DEFAULT_CONFIG instead of reading GET /config.
#
# Run with:
#     python controller.py routines/mining.py

import controller
import debug_log

DEFAULT_CONFIG = {
    "enabled": True,
    "rockNames": ["Tin rocks", "Copper rocks"],
    "maxRockDistance": 10,

    "breaks": {
        "enabled": True,
        "everyMinMinutes": 45,
        "everyMaxMinutes": 75,
        "durationMinMinutes": 3,
        "durationMaxMinutes": 12,
        "microBreaks": True,
    },
    "safety": {
        "stopOnDeath": True,
        # No drop/bank logic below, so let the engine's generic safety stop
        # pause things once the inventory can't hold any more ore.
        "stopOnInventoryFull": True,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # This routine doesn't need anything from the Controller Settings panel.
    return DEFAULT_CONFIG


def tick(ctx):
    config = ctx.state.config

    if not controller.get_combat().get("isIdle", True):
        return  # already mid-swing or walking to the rock -- don't reclick

    rock = pick_rock(controller.get_objects(), config)
    if not rock:
        return

    debug_log.log("mining", f"swinging at {rock.get('name')} (dist={rock.get('distance')})")
    controller.human_delay(0.15, 0.5)
    # rock's screenX/screenY came from the bulk /objects list, which is TickCache-gated (see
    # CLAUDE.md's "Sub-tick data and clicking while moving") -- by the time the physical click
    # lands, the cached position can be stale (camera turn, the rock's own animation). Passing
    # refresh_target re-resolves the rock's live position via the un-cached /object/at endpoint
    # (identified by id + last-known tile) at each leg of the click's flight, the same pattern
    # CLAUDE.md calls out as preferred over trusting a single stale snapshot.
    controller.click_entity(
        ctx.ser, rock,
        refresh_target=controller.object_refresh_target(
            rock["x"], rock["y"], rock["plane"], rock["id"]
        ),
    )


def pick_rock(objects: list, config: dict):
    names = {n.lower() for n in config.get("rockNames", [])}
    max_dist = config.get("maxRockDistance", 10)

    candidates = []
    for obj in objects:
        if (obj.get("name") or "").lower() not in names:
            continue
        if not obj.get("visible", True):
            continue

        dist = obj.get("distance", 9999)
        if dist < 0 or dist > max_dist:
            continue

        candidates.append(obj)

    if not candidates:
        return None

    candidates.sort(key=lambda o: o.get("distance", 9999))
    return candidates[0]
