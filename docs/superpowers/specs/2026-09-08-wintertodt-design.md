# Wintertodt — design

Written 2026-09-08 from the OSRS Wiki (Wintertodt rev 15276053, Wintertodt/Strategies rev
15278826, Bruma herb rev 15187997, Rejuvenation potion rev 15187998, Rejuvenation potion (unf)
rev 15187999), the decoded game cache (dump `5bc2ff1e`), RuneLite's own decompiled
`WintertodtPlugin` (`data/decompiled/client-1.12.33/.../wintertodt/`), the vendored m8aq
`Wintertodt` reader, and live bridge reads against the running client.

**THERE IS NO HUMAN RECORDING AND NOTHING HERE HAS BEEN RUN LIVE.** USER chose to spec from
research rather than record first. Every duration and threshold below is a first-draft guess
unless it is marked MEASURED; the "Live-verification checklist" at the end is the first
session's worklist, and the expected outcome — the pattern from every routine built this way —
is that geometry and widget facts are wrong before any logic is.

USER's decisions, and they shape everything:

- **Fletch roots into kindling.** This is a deliberate points-over-XP trade and the wiki is
  explicit about the cost: "Fletching the roots into kindling is only recommended for gaining
  points and not Firemaking experience, because the amount of time it takes to fletch causes it
  to be slower overall experience." Kindling pays 25 points against a root's 10 (and 30% more
  Firemaking XP per item, but far fewer items per minute). The routine optimises **reward-cart
  rolls per round**, not Firemaking XP per hour. Anyone later reading a low FM/hr as a bug
  should read this paragraph first.
- **Group / official world**, not the solo brazier method.
- **Rejuvenation potions made in the arena** for warmth — no banked food.
- **Never search the Reward Cart**, let it accumulate.
- **Light and Fix braziers** when seen. **Do not heal pyromancers.**

## What makes this routine small

Three facts collapse most of the work a Wintertodt routine would normally need:

1. **Every tool is free inside the arena.** Axe, knife, tinderbox, hammer and the unfinished
   potions all come from crates a few tiles from the braziers. There is **no bank trip, no
   loadout, and no starting-kit precondition** — a run that begins with an empty inventory
   equips itself.
2. **The Reward Cart accumulates to 8,000 rewards** (wiki, Rewards). Nothing is lost by never
   searching it, so with USER's answer there is no banking phase and no inventory pressure from
   loot. The player empties it by hand whenever they like.
3. **Chopping, fletching and feeding are CONTINUOUS actions**, not per-item clicks. Each runs
   until the game interrupts it with a chat line. So one inventory is roughly **three clicks**,
   and the routine is mostly an *interrupt handler* rather than a click loop. This is read
   straight out of `WintertodtPlugin`: it models the player as being in one of
   IDLE / WOODCUTTING / FLETCHING / FEEDING_BRAZIER / LIGHTING_BRAZIER / FIXING_BRAZIER, sets
   that state from one animation or chat line, and clears it on an interrupt or a 3-second
   animation timeout.

Together these mean the routine never leaves **region 6462** and owns no cross-map travel,
no banking, and no consumable it cannot restock in place.

## The state endpoint already exists — no rebuild for state

`GET /api/minigames/wintertodt` is one of the 66 vendored m8aq readers and **was read live on
2026-09-08 with the client outside the arena**, returning a well-formed snapshot. So the state
half of this routine needs no Java change at all.

Fields it serves (`net.runelite.client.plugins.m8aq.api.minigames.Wintertodt`):

| field | meaning |
|---|---|
| `atWintertodt` | player's region == 6462 |
| `hudVisible` / `energyVisible` / `warmthVisible` / `brazierStatusVisible` | which HUD parts are up |
| `roundActive` | `energyVisible && energyPercent > 0` |
| `points` | our points this round, `-1` when unreadable |
| `energyPercent` | Wintertodt's remaining energy, `-1` when unreadable |
| `warmth` | raw **0-1000** scale (percent is `warmth / 10`) |
| `respawnDelay` / `secondsUntilRoundStart` | the latter is `respawnDelay * 30 / 50` |
| `rewardPool` | accumulated cart credits |
| `warningNeeded` | the server's own "you are getting cold" flag |
| `areaAttackActive` | the server's area-attack flag |
| `braziers` | object keyed `SOUTH_WEST` / `NORTH_WEST` / `NORTH_EAST` / `SOUTH_EAST` |

Each brazier entry carries `status` (`LIT` / `UNLIT` / `BROKEN` / `UNKNOWN`, decoded from the
HUD sprite) and `pyromancerStatus` (`ALIVE` / `INCAPACITATED` / `UNKNOWN`).

**`isOperational()` and `isRewardEligible()` are Java METHODS and are therefore absent from the
JSON** — VERIFIED against the live response, which has no `operational` or `rewardEligible` key.
Python re-derives both. This is the same trap CLAUDE.md records for GOTR ("round phase
re-derived Python-side — Gson serializes fields, not the reader's Java helper methods"), and it
is the second thing to check when a field seems missing.

The underlying varbits, all readable today over `GET /varbit?id=` with no Java change:
**11434** warmth, **11435** reward pool, **11436** needs-warning, **5354** area attack,
**7980** round respawn delay. The HUD is widget group **396** (`InterfaceID.WintStatus`).

## Ids, from the cache dump

Every one of these came from `config/loc` in dump `5bc2ff1e`, not from memory.

| Thing | id | name | action |
|---|---|---|---|
| Bruma roots (the outcrop) | 29311 | `Bruma roots` | `Chop` |
| Brazier, unlit | 29312 | **`Brazier`** | `Light` |
| Brazier, broken | 29313 | **`Brazier`** | `Fix` |
| Brazier, lit | 29314 | `Burning brazier` | `Feed` |
| Sprouting Roots | 29315 | `Sprouting Roots` | `Pick` |
| Crate — hammer | 29316 | **`Crate`** | `Take-hammer` |
| Crate — knife | 29317 | **`Crate`** | `Take-knife` |
| Crate — axe | 29318 | **`Crate`** | `Take-axe` |
| Crate — tinderbox | 29319 | **`Crate`** | `Take-tinderbox` |
| Crate — concoctions | 29320 | **`Crate`** | `Take-concoction` / `Take-5 concoctions` / `Take-10 concoctions` |
| Crate — scenery, no ops | 29301-29305 | **`Crate`** | none |
| Area-attack warning snow | 29325 | (unnamed) | none — `active=no` |
| Reward Cart | 55411-55416 | `Reward Cart` | `Search` / `Check` / `Big-search` |

**NEVER MATCH A BRAZIER OR A CRATE BY NAME.** Nine distinct objects here share three names:
the unlit and broken braziers are both `Brazier` and differ only in their action, and *six*
different objects are called `Crate`, five of which hand out different tools while 29301-29305
are pure scenery with no ops at all. This is exactly the shut-vs-open door lesson CLAUDE.md
already records ("a shut door and an open one are both `WallObject`s named `Door` ... only the
`Open` action separates them"), and here it applies five ways over. Match on **id**, and use
the action only as the confirmation.

Items: **Bruma root 20695** (10 pts fed), **Bruma kindling 20696** (25 pts fed), **Rejuvenation
potion (unf) 20697**, **Bruma herb 20698**. `WintertodtPlugin` is the source for the two point
values and both item ids.

Animations, from `WintertodtPlugin.onAnimationChanged`: fletching **1248**, feeding **832**,
lighting **733**, fixing **3676**/**8912**, and a long list for woodcutting (every axe
animation). Note **1248 is the plain fletching animation**, so it identifies the activity, not
the location.

## The rejuvenation potion chain

The warmth answer, pinned from the wiki and the cache rather than reasoned:

1. **Bruma herb (20698)** — `Pick` Sprouting Roots (29315), which stand on the east and west
   walls of the prison *and* in the lobby.
2. **Rejuvenation potion (unf) (20697)** — `Take-concoction` from crate 29320. The game's own
   wording for the unfinished potion is "concoction", which is what the menu entry says.
3. Use herb on unf → **Rejuvenation potion**, 4 doses, Herblore XP equal to level ÷ 10. The
   account has **Herblore 75**, so Druidic Ritual is complete and the Brew'ma NPC fallback
   (which mixes on the player's behalf for players without the quest) is not needed.

Each dose restores **30% warmth**, and a dose would also fully heal any pyromancer for 75
points — see "Out of scope".

Warmth also regenerates naturally at **8% per minute**, plus 1% per piece of warm clothing worn
up to 4. So the floor is not as sharp as it looks, and `drinkAtWarmth` has room to be tuned
down after a live session rather than up.

## Points, and why the ladder is ordered the way it is

Wiki, Reward points:

| activity | points |
|---|---|
| Lighting a brazier | 25 |
| Adding a bruma root | 10 |
| Adding bruma kindling | 25 |
| Repairing a brazier | 25 |
| Healing a pyromancer | **75** |

**500 points is a cliff, not a slope**: below it the round pays nothing at all, and leaving or
logging out mid-round forfeits every point earned. Above it, each further 500 is another
guaranteed cart roll with the remainder as a linear chance. Two consequences run through the
whole design — a break must never land mid-round, and warmth must never reach zero, because
both throw away the entire round rather than a slice of it.

## Architecture

Three files, following the `gathering.py` / `hunllef.py` split:

- **`ControllerAddon/wintertodt_ids.py`** — the two tables above as named constants, plus the
  chat-line constants. No logic. Exists so the routine reads as prose and so a cache change is
  a one-file edit.
- **`ControllerAddon/wintertodt_state.py`** — **pure functions over one snapshot**, no HTTP, no
  client, no serial: `derive_phase`, `choose_brazier`, `kit_deficit`, `warmth_action`,
  `feed_item`. Pure means the whole decision layer is table-tested without a client, the way
  `hunllef.StyleTracker` and the Gauntlet's `craft_plan` are, and it is where the live session's
  corrections will land.
- **`routines/wintertodt.py`** — the engine-facing routine: `tick`, `setup`, `on_event`,
  `before_break`, and one function per rung that does the clicking and confirming.

`ctx.state.data` holds only bookkeeping that is cheap to lose (the held rung, the last
snowfall tile, counters). **Every decision is re-derived from live state each tick** — the rule
CLAUDE.md draws from GOTR and the Gauntlet, and the reason a restart mid-round recovers on its
own.

## The ladder

`derive_phase(snapshot) -> str` is a pure function of the live snapshot and the inventory. It
is evaluated every tick and never reads a stored phase. Rungs, highest priority first:

1. **`stop`** — `atWintertodt` is false (we are not in the arena and this routine owns no
   travel), or a safety condition fired. Terminal.
2. **`kit`** — an axe, knife, tinderbox or hammer is missing, or potion doses are below
   `minDoses`. Restock from the crates by id. This is the only rung allowed to run between
   rounds *and* mid-round, because losing the axe mid-round would otherwise idle us.
3. **`warmth`** — `warmth / 10` below `drinkAtWarmth`. Drink a dose. **Outranks everything
   below it**, including a burning brazier and a full inventory, because 0% warmth is instant
   death and forfeits the round's points. USER's answer settles the mechanism: doses only, no
   banked food.
4. **`dodge`** — a 29325 snow object is standing on our tile. Step one tile off it. The attack
   covers a 3x3 centred on a player and the loc has no `width`/`length`, so the expectation is
   nine 1x1 objects rather than one 3x3 — either way the test is "is one of them on my tile",
   which is correct under both shapes. Checklist item 1 settles which.
5. **`light`** — our brazier (see "Which brazier" below) is `UNLIT` and its pyromancer is
   `ALIVE`. 25 points, and the wiki is explicit that this is worth interrupting anything else
   for: "The brazier should be relit immediately any time it is extinguished while you are
   nearby, as the points and XP are always worthwhile (even if it interrupts cutting or
   fletching)."
6. **`fix`** — our brazier is `BROKEN`. 25 points. The wiki warns these are "fixed extremely
   quickly on Wintertodt worlds", so this rung is expected to lose races often; losing one is a
   no-op, not a failure.
7. **`feed`** — the brazier is `LIT` and we hold something feedable. `feed_item` decides what
   that means: kindling always; roots as well when `fletch` is off, or when the round is nearly
   over and `feedRootsNearRoundEnd` is set. **Getting this condition wrong deadlocks the
   routine** — a `feed` rung that accepts only kindling, paired with `fletch=False`, leaves a
   full inventory of roots with no rung willing to spend it, chopping blocked by the full
   inventory and fletching disabled. `feed` and `fletch` must between them accept every state
   a full inventory can be in, and a test pins exactly that.
8. **`fletch`** — `fletch` is on and the inventory is full of roots.
9. **`chop`** — anything else during an active round.
10. **`between_rounds`** — `roundActive` false: top up potions and kit, and be standing at the
    brazier ready to light the instant the timer hits 0:00 (worth 25 points and
    `Firemaking level * 6` XP, the wiki's single best-value action of the round).

Three properties this ladder must have, each taken from a routine that learned it the hard way:

- **A verified action HOLDS its rung** (`_hold_action`, GOTR). Feeding an inventory of kindling
  is a continuous action lasting tens of seconds; re-deciding the rung every tick would
  re-click and cancel it. The hold is dropped the moment its precondition is gone (nothing left
  to feed, brazier no longer lit), so it can never pin a rung with no work.
- **A transitional snapshot must not be believed.** Between rounds the HUD disappears and
  `points`/`energyPercent` read `-1` while `warmth` still reads fine — the same shape as GOTR's
  transitional-snapshot family, which cost that routine six separate live bugs. The round-over
  test is therefore **sustained**: `ROUND_END_READS` consecutive non-active snapshots, not one.
- **`before_break` plays the round out.** Points are all-or-nothing at 500, so a break landing
  mid-round throws away the whole round. `before_break` keeps calling `tick` until the round
  ends or `BREAK_DEFER_MAX_S` expires, exactly as GOTR's does, so the break overlaps the
  60-second downtime instead of eating a round.

### Which brazier

`choose_brazier` prefers, in order: the brazier we are already working (stickiness beats
churn), then an operational one (`LIT` + pyromancer `ALIVE`, the re-derived `isOperational`),
then the configured `preferredBrazier` (default `SOUTH_WEST`, the conventional group brazier).
The HUD gives all four states for free, so this costs one field read and no scene scan.

A brazier whose pyromancer is `INCAPACITATED` **cannot be relit** (wiki) — so the `light` rung
tests the pyromancer, not just the brazier, and with healing out of scope the answer to a
downed pyromancer is to switch braziers or keep fletching, which is precisely what the wiki
recommends for a player who will not heal.

### Where to stand

The wiki names the two good standing spots: "hugging the wall right next to the bruma roots (to
avoid the 3x3 attack) or 2 squares away from a brazier (so you can react quickly if it is
broken or extinguished, while staying out of range from the shrapnel)". The routine fletches at
the roots and feeds from two tiles off the brazier for exactly these reasons — the shrapnel
from a shattering brazier only damages players standing directly next to it.

## Signals: pushed to decide when, HTTP to decide what

Every wait is `bridge_events.await_outcome` — a listed UDP signal, confirmed by one HTTP read,
with the HTTP read deciding alone at the budget. `ctx.events is None` degrades to polling
everywhere, per the routine contract.

| outcome | pushed signal | HTTP confirm |
|---|---|---|
| chop finished | chat `Your inventory is too full` | `/inventory` free slots == 0 |
| fletch finished | chat `You have run out of bruma roots` | no 20695 held |
| feed finished | `INVENTORY_CHANGED` | no 20695/20696 held |
| brazier lit | chat `You light the brazier` | reader status `LIT` |
| brazier fixed | chat `You fix the brazier` | reader status not `BROKEN` |
| brazier went out | chat `The brazier has gone out.` | reader status `UNLIT` |
| took damage | chat `The cold of` / `The freezing cold attack` / `The brazier is broken and shrapnel` | `warmth` |
| snowfall incoming | `OBJECT_SPAWNED` id 29325 | tile vs. our tile |
| round start / end | `VARBIT_CHANGED` 7980, `roundActive` flip | reader |

Every chat prefix in that table is copied from `WintertodtPlugin.onChatMessage`, which is the
game's own vocabulary rather than our reconstruction of it — the CLAUDE.md rule about copying a
reference plugin's *source* rather than the fact it encodes.

`VARBIT_SUBSCRIPTIONS` declares 11434 (warmth), 7980 (round timer) and 5354 (area attack) so
warmth changes arrive pushed rather than polled.

**The dodge rung is an accelerator, never a source of truth.** If 29325 turns out not to reach
`/objects` (see checklist item 1) the rung simply never fires and warmth is healed reactively
instead — the routine keeps working, just colder. Nothing else depends on it.

## Safety

- **Stuck watch** on an unchanged progress signature (points, inventory counts, tile, rung):
  warn at `STALL_WARN_S`, safety-stop at `STALL_STOP_S`. Every routine since GOTR has one, and
  CLAUDE.md's standing lesson is that a silent loop is worse than a crash.
- **Warmth floor**: a reading at or near 0 with no doses left is a safety stop, not a shrug.
- **Ceilings on every consuming loop**: `KIT_MAX_TAKES`, `LIGHT_MAX_CLICKS`, `FIX_MAX_CLICKS`.
  A cooldown only sets the *rate* of an unbounded loop — the mahogany-homes lesson.
- **Bounded rungs**: the whole-branch review lesson from shipwreck salvaging applies here.
  Every rung that can fail needs a ceiling *and* a sink, or the routine finds a new way to make
  no progress forever.

## Configuration

`DEFAULT_CONFIG`, with `get_config` reading the bridge's `GET /config` where present:

| key | default | why |
|---|---|---|
| `preferredBrazier` | `SOUTH_WEST` | the conventional group brazier; **unverified for this account's worlds** |
| `drinkAtWarmth` | 45 | a dose restores 30%, so drinking at 45 cannot overheal and leaves margin above the 3x3 attack's bite |
| `minDoses` | 4 | one potion's worth in reserve |
| `takeConcoctions` | 10 | one `Take-10 concoctions` click per restock |
| `fletch` | `True` | USER's choice; `False` gives the faster-XP roots-only method with no other code change |
| `feedRootsNearRoundEnd` | `True` | unfletched roots are worth 10 each; at low energy there is no time to fletch them and 0 is worse than 10 |
| `feedRootsBelowEnergy` | 15 | what "nearly over" means for the rung above — `energyPercent` at or under this feeds roots unfletched. A guess; fit it against the measured fletch duration in checklist item 5 |
| `dodgeSnowfall` | `True` | degrades safely to off if 29325 never arrives |

## What needs building

**Java — one enum entry, and nothing else.** `RoutineChoice.WINTERTODT`
(`src/main/java/com/example/controller/RoutineChoice.java`) plus the matching `ROUTINE_FILES`
key in `controller.py`. This is the required GUI-wiring step from CLAUDE.md's routine contract,
and it needs a client rebuild before the routine appears in the panel's dropdown. No provider,
no config section — the routine uses `DEFAULT_CONFIG`/`get_config` and ignores `bridge_config`,
the same pattern as `gemstone_crab.py` and `mining.py`, which CLAUDE.md says needs nothing
beyond the two wiring steps.

**Python** — the three files above plus `tests/test_wintertodt.py`, which tests
`wintertodt_state`'s pure functions as tables and drives a real `EventStream` through a
scripted `controller.poll_udp` for the outcome waits. The lesson recorded from
`test_mastering_mixology.py` applies: **feed each event from the click that causes it** — a
script that queues a later click's verdict up front delivers it during the earlier click's
wait, which cannot happen live, and the "failure" that produces is the test's.

## Out of scope, each for a stated reason

- **Healing pyromancers** — USER said no. Worth recording that this is the largest single award
  in the game mode (75 points, three times a kindling feed) and that the potions are already in
  the inventory for warmth, so the marginal cost is one rung and a dose. **This is the obvious
  first extension** once the base loop is proven.
- **Searching the Reward Cart and banking** — USER said no; the cart holds 8,000, so nothing is
  lost.
- **World-hopping for maximum Firemaking XP** — the wiki's separate method, incompatible with
  the fletching choice.
- **The Agility pillars during downtime** — real XP but a whole second activity.
- **The solo-brazier method** — USER chose group.
- **Bruma torch / warm clothing management** — warm clothing reduces damage and adds regen, but
  the account's gear is a manual setup concern, not a routine one.

## Live-verification checklist

The first session's worklist, in the order the failures would bite:

1. **Does `/objects` list `active=no` locs?** The entire dodge rung rests on 29325 arriving as
   an `OBJECT_SPAWNED` event and in the bulk scan. Also check it against
   `SPAWN_EVENT_CAP_PER_TICK` (40) — a crowded arena spawning snow for many players at once is
   exactly the shape that cap exists to bound, and dropped events are intentional.
2. **Is the arena instanced?** Assumed not, since it is a shared multi-player area with real
   world coordinates. CLAUDE.md's instancing gotcha has bitten four providers and its rule is to
   verify rather than assume: compare `/player`'s `x`/`y` against the map.
3. **The crate tiles and the brazier tiles**, and whether the crates are close enough to the
   brazier that a restock is cheap. `forceapproach=north` on all five tool crates and
   `forceapproach=south` on the cart mean the game forces an approach side, so the walk is
   longer than straight-line distance suggests.
4. **Which brazier the crowd actually uses** on the worlds this account plays, and whether a
   contested 3x3 brazier needs golem_crafting's menu escalation — that routine MEASURED a
   30-40% bare-click hijack rate from other players' models drawn over the aim pixel, and a
   Wintertodt brazier on an official world is the most crowded object in the game. Expect to
   need `click_menu_action` here; the tier is cheap to add and the spec assumes it may be
   required.
5. **Real durations** of a full chop, a full fletch and a full feed, to fit the budgets. The
   wiki says a round averages ~5 minutes including the 60-second gap.
6. **Whether `Take-10 concoctions` is reachable** as a menu entry, and how many doses a round
   actually costs — `drinkAtWarmth` and `minDoses` are both blind guesses.
7. **Whether the between-round HUD really reads `-1`** for points and energy, which is what the
   sustained round-end test is defending against.
8. **Points per round achieved**, against the 500 cliff. If the fletching loop cannot clear 500
   on a fast group world, `fletch=False` is the one-key fallback and the reason that key exists.
