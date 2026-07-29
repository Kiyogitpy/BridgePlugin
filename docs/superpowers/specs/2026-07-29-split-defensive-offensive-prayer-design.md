# Split Combat Prayer into Defensive + Offensive slots, with offensive flicking

## Problem

The Controller Settings panel currently has a single "Combat Prayer" choice
(`CombatPrayer.java` / `ControllerConfig.combatPrayer()`), and `slayer.py`'s
`maintain_prayer` tracks one `combat_prayer` value at a time. In real OSRS play
you normally want a defensive prayer (Protect from Melee/Missiles/Magic) and an
offensive prayer (Piety, Rigour, Augury, etc.) active at the same time -- the
current single-slot design can't represent that.

Additionally, offensive prayers drain faster than defensive ones and are
commonly "flicked" -- turned on right before your own attack lands and off
again right after -- to save prayer points. The reference community fork this
project already draws on for `tormented_demon.py` (ZombieLite, local checkout
at `Documents/ZombieLite-master`) implements this via a `PrayerBehaviour` enum
(`DISABLED`, `LAZY`, `LAZY_FLICK`, `ONE_TICK`) backed by a full damage-prediction
subsystem (`TrackerPlugin`/`Damage`/`NpcTracker`) that watches NPC attack
animations and hitsplat timing to predict exactly which tick damage lands, and
an `AttackTimerMetronome` plugin integration (via reflection) to know exactly
how many ticks remain until the player's own next attack.

This project has no equivalent damage-prediction subsystem, and its
external-input architecture (HTTP polling + real Arduino mouse clicks, not an
injected client) cannot reliably hit tick-perfect timing regardless -- this is
already an explicitly documented limitation in `tormented_demon.py`'s module
header. Building a full ZombieLite-style predictive system is out of scope.

## Scope

1. Split the single Combat Prayer config into two independent selections:
   defensive and offensive.
2. Defensive prayer: **LAZY** behavior only (turn on for the whole fight, turn
   off when combat ends) -- this matches today's existing behavior, just
   scoped to its own slot instead of sharing one variable with offensive.
3. Offensive prayer: **LAZY_FLICK**-style behavior, but scoped down to what's
   actually achievable here: flick on right before the player's own attack
   (using the already-installed "Attack Timer Metronome" plugin's tick
   countdown, the same plugin ZombieLite itself integrates with), flick off
   once Hitpoints XP confirms the hit landed (or once the attack tick has
   passed, on a miss).
4. Explicitly NOT in scope: incoming-damage prediction, protection-prayer
   flicking, `ONE_TICK` tick-perfect timing, any NPC animation/hitsplat
   tracking subsystem. Defensive prayer stays LAZY (always-on-in-combat) as
   today.

## Design

### 1. Config split (Java)

Two new enums replace `CombatPrayer`:

```java
public enum DefensivePrayerChoice
{
    NONE(null, "None", 1),
    PROTECT_FROM_MAGIC(Prayer.PROTECT_FROM_MAGIC, "Protect from Magic", 37),
    PROTECT_FROM_MISSILES(Prayer.PROTECT_FROM_MISSILES, "Protect from Missiles", 40),
    PROTECT_FROM_MELEE(Prayer.PROTECT_FROM_MELEE, "Protect from Melee", 43);
    // itemName/getPrayer/getLevel accessors, same shape as today's CombatPrayer
}

public enum OffensivePrayerChoice
{
    NONE(null, "None", 1),
    AUTO_MELEE(null, "Auto (Melee)", 1),
    AUTO_RANGED(null, "Auto (Ranged)", 1),
    AUTO_MAGIC(null, "Auto (Magic)", 1),
    STEEL_SKIN(Prayer.STEEL_SKIN, "Steel Skin", 28),
    ULTIMATE_STRENGTH(Prayer.ULTIMATE_STRENGTH, "Ultimate Strength", 31),
    INCREDIBLE_REFLEXES(Prayer.INCREDIBLE_REFLEXES, "Incredible Reflexes", 34),
    CHIVALRY(Prayer.CHIVALRY, "Chivalry", 60),
    PIETY(Prayer.PIETY, "Piety", 70),
    EAGLE_EYE(Prayer.EAGLE_EYE, "Eagle Eye", 44),
    DEADEYE(Prayer.DEADEYE, "Deadeye", 62),
    RIGOUR(Prayer.RIGOUR, "Rigour", 74),
    MYSTIC_LORE(Prayer.MYSTIC_LORE, "Mystic Lore", 27),
    MYSTIC_MIGHT(Prayer.MYSTIC_MIGHT, "Mystic Might", 45),
    AUGURY(Prayer.AUGURY, "Augury", 77);
}
```

`ControllerConfig.java`: replace `combatPrayer()` with `defensivePrayer()`
(default `PROTECT_FROM_MELEE` or similar) and `offensivePrayer()` (default
`NONE`), both in the existing Prayer config section.

`ConfigExporter.java`: replace the single `prayer.combatPrayer` export with
`prayer.defensivePrayer` and `prayer.offensivePrayer` (`.name()` of each
choice, same convention as today).

### 2. New bridge data

`CombatProvider.java`'s `/combat` response gains:

- **`ticksUntilNextAttack`** (int, omitted if unavailable): resolved by
  finding the "Attack Timer Metronome" plugin instance via `PluginManager`
  and reflectively invoking its zero-arg, int-returning "ticks until next
  attack" method -- identical technique to ZombieLite's own
  `hookAttackTimerMetronome()`/`getTicksUntilNextAttack()` (find plugin by
  name once at startup, cache the `Method`, reflectively invoke each read).
  Same class of fragility as `UnsafeNpcOverheadIcon` (no official API,
  silently stops working if that plugin isn't loaded or changes shape) --
  documented the same way. If the plugin isn't found, this field is simply
  absent from the response every time, and offensive-prayer flicking becomes
  a permanent no-op (never crashes, never guesses).

`PlayerProvider.java`'s `/player` response (or `/combat`, colocated with the
other combat-relevant fields for one less HTTP round trip) gains:

- **`hitpointsXp`** (int): `client.getSkillExperience(Skill.HITPOINTS)`.
  Chosen over per-style skill XP (attack/strength/ranged/magic) because
  Hitpoints XP increases on essentially any successful damaging hit
  regardless of which combat style is currently active, so the Python side
  never needs to know which style/skill is "the" offensive one.

### 3. Python-side (`slayer.py`)

`maintain_prayer` is replaced by two functions, both called from `tick()`
where `maintain_prayer` used to be:

```python
maintain_defensive_prayer(ctx, combat)
maintain_offensive_prayer(ctx, combat)
```

**`maintain_defensive_prayer(ctx, combat)`**: today's existing logic
(resolve config, `want_on = combat.inCombat`, toggle if `want_on != is_on`),
scoped to the `defensivePrayer` config value only. No ladder auto-resolution
needed (defensive has no "Auto" tier). Behavior is unchanged from today,
just no longer sharing a variable with offensive.

**`maintain_offensive_prayer(ctx, combat)`**: new flick state machine.
Resolves `offensivePrayer` via the existing `resolve_combat_prayer`/
`PRAYER_LEVELS`/`AUTO_PRAYER_LADDERS` machinery. `PRAYER_LEVELS` gains two
entries the current dict is missing: `"DEADEYE": 62`, `"MYSTIC_LORE": 27`
(needed so a manual pick of either resolves its level gate correctly, even
though neither is in an Auto ladder -- see below). `AUTO_RANGED`'s ladder
stays `["RIGOUR", "EAGLE_EYE"]`, deliberately **not** adding `DEADEYE`: Deadeye
requires looting an unlock scroll from Royal Titans in addition to the level
43... 62 requirement, which the ladder's level-only gate can't verify --
an account with level 62+ Prayer but no scroll would have the ladder pick an
unavailable prayer, which our new icon-resolution-by-name would then just
silently fail to find (no crash, but a permanently-off offensive prayer with
no explanation). Accounts that do have Deadeye unlocked can select it
directly as a manual `OffensivePrayerChoice` entry instead of via Auto. State
kept in `ctx.state.data`:

- `offensive_prayer_on_since_tick`: the tick the prayer was last flicked on,
  or `None` if it's currently off.
- `offensive_prayer_hp_xp_at_flick`: `hitpointsXp` reading captured at the
  moment of flicking on.

Each tick, if an offensive prayer is configured and usable:

- If currently **off** and `ticksUntilNextAttack == 1`: flick on (click the
  icon), record the current tick and current `hitpointsXp`.
- If currently **on**:
  - If `hitpointsXp > offensive_prayer_hp_xp_at_flick`: the hit landed --
    flick off.
  - Else if the current tick is past `offensive_prayer_on_since_tick` (i.e.
    at least one full tick has elapsed since flicking on and the attack
    should have already resolved): flick off anyway (miss -- don't wait
    around for XP that isn't coming).
- If `ticksUntilNextAttack` is absent from the `/combat` response (metronome
  plugin not found): no-op entirely, every tick. No fallback timing guess.

### Testing

No unit test framework exists for the Python side (`routines/` are driven by
a live bridge + Arduino, same as everything else in this codebase). Verify by
running `routines/slayer.py` live against a real target with an offensive
prayer configured and the Attack Timer Metronome plugin loaded, watching for
`[prayer] turning on/off <OFFENSIVE_PRAYER>` log lines landing right around
each own-attack tick, same manual-verification approach used earlier this
session for the defensive-prayer fix.
