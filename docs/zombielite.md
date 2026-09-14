# ZombieLite in the merged client — history and workflow

Moved out of CLAUDE.md on 2026-09-14. Nothing here is obsolete: this is the full measured record; the condensed working rules live in CLAUDE.md.

- **RuneLite client**: `./gradlew.bat run` (from repo root). Launches via
  `ExamplePluginTest.main`, which loads the Controller plugin and starts the bridge.
  **Since 2026-09-05 the same command also loads every ZombieLite plugin** (the user's
  overlay-only boss-helper bundle, `../ZombieLite`, branch `no-packets` on
  github.com/Kiyogitpy/ZombieLite) when that checkout exists: `run` builds its jar in place
  (`buildZombieLite`) and `ExamplePluginTest` loads `CustomPlugins.CLASSLIST` by reflection ahead
  of `ControllerPlugin`, so nothing here compiles against it and a machine without the checkout
  gets the bridge alone. Why: a Hunllef kill can only be played by hand with ZombieLite's
  attack/prayer counters up, and `/recorder` lives in this plugin, so recording it needs both in
  ONE client. `./gradlew.bat checkZombieLite` links all of its plugin classes against this
  build's RuneLite without launching (run it after pulling ZombieLite or a `latest.release`
  bump). That branch has NO packet layer: `com.salvation.packets`, the Zombie Updater, the Zombie
  Prayer automation and the hopper's auto-teleport/logout were deleted 2026-09-05 so nothing in
  the merged client can inject input -- keep it that way; the "physical Arduino, never
  software-injected" rule in "What this is" applies to every plugin loaded next to the bridge.
  **Zombie Prayer came BACK as indicators-only on 2026-09-06.** The 2026-09-05 removal deleted the
  whole plugin, and its `PrayerOverlay` was the ONLY reader of `TrackerPlugin.getIncomingDamages()`
  -- the set every boss plugin's projectile/NPC trackers queue `Damage` into -- so the Whisperer,
  Cerberus, Jad, Muspah and Mad Angel silently stopped showing what to pray (the Hunllef kept
  working only because the gauntlet plugin draws its own swap text). USER: "when i said i wanted
  packet removed i meant packets that simulate clicks"; "take it from upstream". So
  `plugins/prayer/*`, `audio/PrayerMuter.java` and `TrackerConfig`'s click-pattern enum are
  upstream's files verbatim, EXCEPT `ZombiePrayerPlugin`, which lost only the three things that
  acted -- the onGameTick `Prayer.activate()/deactivate()` loop, the Attack-click activation
  handler, and the Gaussian click-pattern call -- each marked with a `no-packets:` comment.
  `Prayer.setActive/flick/toggle` and `Widgets.click` stay deleted. Audit of the removal commit
  (`bc41f45`, 22 files) found nothing else read-only lost; the AoE warning plugin was untouched.
  Read-only-ness is judged per FILE when taking an upstream commit, not per plugin: an upstream
  plugin that both draws and clicks is kept with its clicking methods removed, not dropped.
  **Taking upstream ZombieLite commits (2026-09-05):** the checkout's `origin` is
  StephanBijzitter's repo and `fork` is the user's; fetch `origin`, `git cherry-pick` onto
  `no-packets`, push to `fork` ONLY. Skip any upstream commit that touches `com.salvation.packets`
  or re-adds an input-sending plugin, and run `checkZombieLite` after. Two were taken that day:
  `39120eb` (graphics-object tick counters) and `3fe479f` (Lowerniel Drakan). The Drakan one is
  the answer to USER's "feel like it just keeps piling on but should reset after his combo is
  done": the overlay's telegraph `TreeMap` is only drained in `onAnimationChanged`, and the plugin
  had registered only the IDLE animation, so a combo's telegraphs accumulated until the boss
  idled; upstream registers all seven combo animations. If the pile-up is still visible live, the
  remaining fix is pruning by expiry tick rather than by animation.
  **Kiyo's own Weapon Tracker addon lives in ZombieLite since 2026-09-06** (`com.WeaponTracker`,
  commit `75d5515` on `no-packets`): the ZombieLite copy is the CANONICAL one, not
  `IdeaProjects/weapon_tracker_rs` -- its classifier defers to `com.salvation.data.WeaponDamageType`
  for melee/ranged/magic (SPEC stays a name check; unarmed -1 is answered UNKNOWN before the table,
  whose NONE entry maps -1 to MELEE), so it no longer compiles standalone. The per-line file logger
  was dropped on import (USER: "remove logger from my addon").

