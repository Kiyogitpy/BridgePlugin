# routines/nmz.py
#
# Nightmare Zone AFK potion-sipping loop. Every tick: click the locator orb, click the
# Super ranging potion (whatever dose count it currently shows), sip Absorption potions
# (50 points/dose) while absorption is below (cap - doseValue) or until out of doses,
# Alt+Tab out of the RuneLite window, wait 10-20 minutes, Alt+Tab back in. No multi-tick
# state -- one full cycle per tick() call, the engine calling tick() again next iteration is
# what makes it repeat.
#
# Absorption points are read via PlayerProvider's "nmzAbsorptionPoints" field (varbit 3956),
# matching the real item name/varbit the official RuneLite Nightmare Zone plugin itself uses
# (data/decompiled/client-1.12.33/.../nightmarezone/NightmareZonePlugin.java's own
# checkAbsorption()) -- the in-game item is "Absorption (1)".."Absorption (4)" (data/raw/A/
# Absorption.txt), NOT "Absorption potion" (that's a different, scenery-only wiki page).
#
# Terminates (via ctx.state.trigger_safety_stop) if the player isn't in the NMZ region
# (9033) -- e.g. died and got bounced out, or the routine was started in the wrong place.
#
# KNOWN LIMITATION, accepted deliberately (2026-08-15): DEFAULT_CONFIG's "stopOnDeath": True
# and any other engine-level safety check (check_safety/check_pvp_threat in controller.py's
# main loop) cannot actually fire during the 10-20 min tabbed-out window. controller.py's main
# loop gates those checks behind the same "windowFocused" branch that skips calling tick() at
# all while unfocused -- so once this routine alt-tabs away, nothing re-evaluates safety
# conditions until it tabs back in and tick() returns, regardless of how the wait itself is
# structured (one long blocking sleep here vs. returning early and letting the engine's own
# loop wait -- both hit the same focus-gated skip). Accepted because NMZ deaths are low-stakes
# (no item loss). If this ever needs real safety coverage during the tabbed-out window, that
# requires decoupling check_safety/check_pvp_threat from the focus gate in controller.py's main
# loop itself (a shared engine change, not something fixable in this file alone) -- not done.
#
# Run with:
#     python controller.py routines/nmz.py

import controller
import debug_log

NMZ_REGION_ID = 9033

DEFAULT_CONFIG = {
    "enabled": True,
    "locatorOrbName": "locator orb",
    "potionName": "super ranging",  # matches "Super ranging (1)".."Super ranging (4)"
    "absorptionPotionName": "absorption",  # matches "Absorption (1)".."Absorption (4)"
    "absorptionCap": 1000,
    "absorptionDoseValue": 50,  # each dose restores this much -- drink while below (cap - this)
    "maxAbsorptionSips": 28,  # safety cap -- inventory size, worst case every slot is a dose
    "waitMinS": 600,   # 10 min
    "waitMaxS": 1200,  # 20 min

    "breaks": {
        "enabled": False,
    },
    "safety": {
        "stopOnDeath": True,
        "maxRuntimeHours": 0,
    },
}


def get_config(bridge_config):
    # No RuneLite Controller Settings panel for this yet -- see CLAUDE.md's routine-contract
    # notes on when that's required (only for routines that read GET /config).
    return DEFAULT_CONFIG


def tick(ctx):
    config = ctx.state.config

    player = controller.get_player()
    region = player.get("regionId")
    if region != NMZ_REGION_ID:
        ctx.state.trigger_safety_stop(f"not in the NMZ region (expected {NMZ_REGION_ID}, got {region})")
        return

    controller.open_inventory_tab(ctx.ser)

    click_inventory_item(ctx, config["locatorOrbName"])
    click_inventory_item(ctx, config["potionName"], wait_range=POTION_COOLDOWN_WAIT)
    sip_absorption(ctx, config)

    controller.alt_tab(ctx.ser)
    debug_log.log("nmz", "tabbed out, waiting")
    controller.human_delay(config["waitMinS"], config["waitMaxS"])

    # The engine's own main loop won't call tick() again while unfocused (see controller.py's
    # "Don't blind-click into a backgrounded window" check) -- it just polls windowFocused
    # forever with no timeout. That's fine for every other routine, since THEY never unfocus
    # themselves in the first place. This routine does, so if our own tab-back-in doesn't
    # actually land on RuneLite (e.g. some other window came to the foreground during the
    # 10-20 min gap and Alt+Tab cycles back to IT instead), nothing would ever notice or
    # retry -- tick() itself is what's supposed to fix this, and tick() won't be called again
    # while unfocused. So verify focus here, before returning, and retry/bail out loudly
    # rather than silently stalling forever.
    for attempt in range(3):
        controller.alt_tab(ctx.ser)
        if controller.wait_for_focus():
            debug_log.log("nmz", "tabbed back in")
            return
        debug_log.log("nmz", f"tab-back-in attempt {attempt + 1} didn't regain focus, retrying")

    ctx.state.trigger_safety_stop("Alt+Tab back into RuneLite failed after 3 attempts")


# Drinking a (non-barbarian) potion imposes a hard 3-tick (1.8s) cooldown before the next
# potion can be drunk -- confirmed OSRS wiki mechanic, not a humanization choice. 2.0-2.4s
# comfortably clears the 1.8s floor with margin, same "never let a wait dip below a real
# game-mechanic requirement" lesson as banker.py's search_bank delay (see human_delay's own
# docstring). Applies to the ranging potion.
POTION_COOLDOWN_WAIT = (2.0, 2.4)

# Absorption potions are the confirmed exception to the shared 3-tick potion timer -- they can
# be spam-clicked back to back. This is just a short settle wait for the click itself and for
# nmzAbsorptionPoints to register the new dose before the next poll, not a cooldown.
ABSORPTION_SETTLE_WAIT = (0.15, 0.3)

# Generic non-potion inventory click (the locator orb).
DEFAULT_CLICK_WAIT = (0.2, 0.4)


def sip_absorption(ctx, config) -> None:
    # Stop at (cap - doseValue), not cap itself -- drinking at or above that point would push
    # past the real 1000 cap and waste part of the dose. E.g. cap=1000, doseValue=50: keep
    # drinking while absorption < 950, since the next dose lands it at <= 1000.
    drink_below = config["absorptionCap"] - config["absorptionDoseValue"]
    for _ in range(config["maxAbsorptionSips"]):
        absorption = controller.get_player().get("nmzAbsorptionPoints", 0)
        if absorption >= drink_below:
            debug_log.log("nmz", f"absorption at {absorption} (>= {drink_below}) -- not drinking")
            return

        if not click_inventory_item(ctx, config["absorptionPotionName"], wait_range=ABSORPTION_SETTLE_WAIT):
            debug_log.log("nmz", f"out of absorption potions (had {absorption}, threshold {drink_below})")
            return
    debug_log.log("nmz", "sip_absorption hit its max-sip safety cap without reaching the target")


def click_inventory_item(ctx, name_substring: str, wait_range=DEFAULT_CLICK_WAIT) -> bool:
    name_substring = name_substring.lower()
    item = next(
        (i for i in controller.get_inventory() if name_substring in (i.get("name") or "").lower()),
        None,
    )
    if item is None:
        debug_log.log("nmz", f"no inventory item matching '{name_substring}' -- skipping")
        return False

    debug_log.log("nmz", f"clicking {item.get('name')}")
    # check_ui_blockers=False -- CONFIRMED live (2026-08-15): an inventory slot is a
    # fixed-position UI element, not part of the 3D viewport, so click_entity's default
    # "rotate the camera to work around it" response can never move it out from under
    # whatever's covering it (the native NMZ absorption/points widget almost certainly --
    # even the official RuneLite Nightmare Zone plugin has to hide/move that same widget for
    # exactly this reason, see NightmareZonePlugin.onBeforeRender). Without this, every click
    # got stuck rotating the camera left forever and never actually landed. Same precedent as
    # mastering_mixology.py's own panels (see CLAUDE.md's click_entity check_ui_blockers note).
    controller.click_entity(ctx.ser, item, check_ui_blockers=False)
    controller.human_delay(*wait_range)
    return True


