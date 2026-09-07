# Deep sea trawling — the bank round trip (sub-project 2)

**Date:** 2026-09-07
**Extends:** `docs/superpowers/specs/2026-09-06-deep-sea-trawling-design.md` (sub-project 1)
**Routine:** `ControllerAddon/routines/deep_sea_trawling.py`
**Status:** written from the OSRS Wiki (Deep sea trawling rev 15297127, Mooring point rev
15200840, Great Sound rev 15212138, Trawling net rev 15213496, Transcript:Cabin Boy Jenkins
(ghost) rev 15233746) and the decoded cache (dump `5bc2ff1e`). **Nothing here has been run live,
and neither has sub-project 1.** Sub-project 1's own live checklist should be worked first —
several of its answers change this design.

---

## 1. Why this exists

Sub-project 1 ends a run with `done_phase`: the cargo hold is full, the net is raised, the
routine safety-stops. That was scoped on the reasoning *"the cargo hold is 240 slots and the
Rope net's expected yield is roughly 5 fish per minute, so a hold lasts many hours."*

**That arithmetic was wrong.** 240 ÷ 5 is 48 minutes, and the real yield is higher: at Fishing 75
a Giant krill roll succeeds 10.5 % of the time (Wiki catch-chance table), a 1-tick-clicked roll
fires every 2 ticks, so ~50 rolls/min → ~5.3 catches/min → **~8 fish/min** with the Rope net's
1-2 per catch. `netFullAt` is 240, so:

| Hold contents | Trip length |
|---|---|
| No crates | net fills in ~30 min, empties into a 240-slot hold, hold is full, run stops |
| A stack of empty crates | ~2,390 fish, **~5 hours** |

Two separate fixes fall out of that, and they are very different sizes:

- **Crates** — already done (`f8bace2`, sub-project 1). They need no interaction at all: the game
  fills them automatically when the net is emptied into the hold. The routine now reports the
  stock once per run and warns when the hold has none.
- **The bank round trip** — this spec. It is what turns "~5 hours then stop" into "runs until you
  stop it".

So this is a **throughput** feature, not a correctness one. Sub-project 1 is complete and
terminates cleanly without it.

---

## 2. The geography is unusually favourable, and that shapes everything

The Great Sound route this routine already targets and its bank are **in the same body of
water**:

> "The Great Sound is a body of water that divides the continent of Zeah nearly in two… **A giant
> krill shoal is located in the middle of the sound.** … **A mooring point can be found at Land's
> End.**" — Great Sound, rev 15212138

And from the Mooring point table (rev 15200840), Land's End requires **Sailing 5** (the account
has 64), needs only "must have visited Kourend at least once", and has a **bank deposit box**.

Route box (from `trawling_routes.stops_world("giant_krill", "great_sound")`): x 1560-1616,
y 3338-3387. The Great Sound's own map centre is (1527, 3380). Land's End sits on its northern
shore. So the round trip is **tens of tiles inside one sea**, not a cross-ocean voyage — which is
the single fact that makes this tractable, because ocean navigation is the part of this codebase
nothing has exercised live since 2026-08-05.

**Live-verify:** the mooring point's exact tile, and the actual sail distance from the route's
nearest stop. If it turns out to be a long haul, reconsider the whole feature — the value is
throughput, and a 10-minute round trip against a 5-hour crated run is not obviously worth the
risk described in §4.

---

## 3. The mechanic

Two ways to bank a cargo hold. They are **not** equivalent and the difference is the whole risk
model.

### 3a. Dock and disembark (the documented, general path)

> "When disembarking at a port that has a bank deposit box, **the first crewmate in the crew
> registry will be available on the dock to bank the contents of the ship's cargo hold.** This
> function is not available on any island, even if it has a bank." — Mooring point

The crewmate's dialogue (Transcript:Cabin Boy Jenkins (ghost), rev 15233746):

> "Can you help bank my cargo hold contents?" → *if there are items in the cargo hold:* **("Move
> Cargo to Bank" interface opens.)**

### 3b. The bank boat (no disembarking)

> "If both the cargo hold and inventory are full and there are still fish in the nets that
> haven't been collected, players can **empty their cargo at the bank boat without
> disembarking**." — Deep sea trawling § Transporting deep sea fish

This is the safer path — **disembarking is one of the four things that discards net fish** — but
the Wiki does not say where a bank boat is, and nothing in the cache dump was searched for it
while writing this. **Live-verify item #1** and the first thing to settle, because it decides
which of two quite different implementations gets built.

---

## 4. The hazard this design is built around

> "Always empty trawling nets before disembarking or teleporting away, as **uncollected fish will
> be discarded** upon doing so." — Deep sea trawling

Disembarking is *step one* of path 3a. So the ordering is not a nicety, it is the feature:

```
1. raise or leave the net; COLLECT the nets into cargo/inventory
2. CONFIRM /trawling.netFish == 0            <- a hard gate, not a best-effort
3. only then sail to the mooring point and disembark
4. bank via the crewmate
5. re-board, sail back, resume
```

**If step 2 cannot be confirmed, the trip is abandoned and the routine falls back to
sub-project 1's `done_phase`** (stop cleanly with fish aboard) rather than disembarking on an
unconfirmed read. A stopped routine costs the user a restart; a wrong disembark silently destroys
up to 240 fish, and `netFish` is exactly the field sub-project 1 already treats as
`None`-means-cannot-tell.

Note the interaction with the existing full-hold case: when the hold is full there may be fish
that fit nowhere. `done_phase` already collects what the inventory can take. Anything still in
the nets at step 2 blocks the trip — which is correct, and is why the crate stock matters: a
crated hold reaches the dock with room to spare.

---

## 5. Design

### 5.1 Phases

Two new phases, dispatched by the existing pure `derive_phase` from live state, same as every
other:

| Phase | Entered when | Does |
|---|---|---|
| `to_bank` | the hold is full (or crates are exhausted) AND `bank.enabled` | collect the nets, gate on `netFish == 0`, `sailing.sail_to` the mooring point, dock, disembark |
| `deposit` | ashore at the port | talk to the dock crewmate, drive the "Move Cargo to Bank" interface, re-board |

`done_phase` stays exactly as it is and remains the fallback for `bank.enabled: False`, for a
blocked step 2, and for every ceiling in the new phases. **Nothing about sub-project 1's
terminating behaviour is removed** — this is added in front of it.

`derive_phase` stays pure. The "am I ashore" test should be a region/position check, not a stored
flag: `ctx.state.data` is wiped on restart while the character's position is not, so a restart
mid-trip must resume from where the player actually is (the falconry/karambwan pattern).

### 5.2 Config

```python
"bank": {
    "enabled": False,          # OFF by default until a live session proves the round trip
    "port": "lands_end",
    "restockCrates": False,    # see §7 -- a deposit box cannot withdraw
    "maxTripS": 300,           # abandon and fall back to done_phase past this
},
```

Default **off**. Sub-project 1 is finished and safe; this turns on a path that ends in a
disembark, and it should be a deliberate act after a live session.

### 5.3 What it reuses

- `sailing.sail_to` / `stop_boat` / `ocean_path.find_ocean_route` — unchanged. Note the
  2026-09-04 offline fixes to those (braking that budgets reaction time, the minimap-click
  blocker opt-out, route string-pulling) are **themselves unverified live**; this feature is
  their first real exercise, which is a risk to name up front rather than discover.
- `outcome_waits.await_pre` / `walk_budget` for every confirm, same as sub-project 1.
- `banker.py` is **not** reused — this is a cargo-hold interface reached through an NPC dialogue,
  not a bank interface. Do not force it through `banker`.

### 5.4 Ceilings

Every loop gets one, per this repo's standing rule (an unbounded retry has previously burned real
consumables and run for 47 minutes): sail attempts, dialogue attempts, deposit confirms, and a
whole-trip `maxTripS` past which the routine abandons to `done_phase`. The stop reason must name
where the fish are — aboard, in the hold, or banked.

---

## 6. Live-verification checklist (in the order the first session should settle them)

Work sub-project 1's checklist first; these assume the routine already fishes.

1. **Does a "bank boat" exist in or near the Great Sound, and can cargo be emptied at it without
   disembarking?** This decides which implementation gets built (§3b vs §3a). Search `/objects`
   and `/npcs` near the route, and the cache for a bank-boat loc/npc.
2. **Land's End mooring point**: its exact tile, its object id and action, and the real sail
   distance from the nearest route stop.
3. **The dock crewmate**: which NPC actually appears, its name, and the exact dialogue option
   text ("Can you help bank my cargo hold contents?" is from a *ghost* crewmate's transcript and
   is very likely worded differently for a normal one).
4. **The "Move Cargo to Bank" interface**: its widget group, whether a "Move all" button exists,
   and its id. Note sub-project 1 already recorded that neither `/state.dialogOpen` nor the m8aq
   dialogue reader sees widget group 270 (the skill-select modal family) — check which family
   this one is before trusting any detector.
5. **Does disembarking with a non-empty net actually warn, or silently discard?** The Trawling
   net page says the *logout button* warns; it does not say disembarking does. Test this
   deliberately with 1-2 fish aboard, never with a full net.
6. **Whether the crewmate banks the whole hold in one action**, including filled crates, and what
   the chat line on success is.
7. **The real cargo-hold size** — sub-project 1 parked a known gap here: `capacity` is the item
   container's declared 240, which may exceed a given boat's built hold. This trip's arithmetic
   depends on it.
8. **`ESTIMATED_FISH_PER_MIN` (8.0)** — measure it. Sub-project 1 uses it only for a log line,
   but every number in §1 rests on it.

---

## 7. Explicitly out of scope, and one of them is load-bearing

- **Restocking empty fish crates. This is the important one.** Land's End has a **bank deposit
  box, not a bank** (Mooring point table), and a deposit box cannot withdraw. So the round trip
  as specified *deposits* fish and cannot replenish crates — meaning a crated run still ends when
  the crates run out, just much later. The replenishment chain is a Crafting loop, not a bank
  withdrawal: a filled crate has `iop1=Empty`; emptying it in the bank returns the **camphor
  crate**, which is **recharged with an ice cooler** into a new empty fish crate (yellowfin MMG:
  *"Camphor crates which are not consumed"*, *"When emptied in the bank camphor crates are
  returned, which can be recharged with an ice cooler"*). That is its own sub-project, and it may
  want a port with a real bank rather than Land's End. **Settle live-verify #1 and #6 before
  designing it** — if the bank boat turns out to be a full bank interface, this gets much
  simpler.
- **Chum station baiting** (+20 % fish per catch). Still the highest-value unbuilt thing after
  this, and still blocked on seeing the station's `Operate` interface.
- **Depth management, a second net, mixed-shoal handling** — unchanged from sub-project 1 §9.

---

## 8. Files

| File | Change |
|---|---|
| `ControllerAddon/routines/deep_sea_trawling.py` | `to_bank` / `deposit` phases, the `bank` config block, the netFish gate |
| `ControllerAddon/tests/test_deep_sea_trawling.py` | phase table, the gate refusing to disembark on an unconfirmed read, every ceiling |
| `docs/.../2026-09-06-deep-sea-trawling-design.md` | §9 updated to point banking here |

No Java change and no client rebuild: `/trawling` already reports the cargo hold, and the
mooring point and crewmate are ordinary `/objects` and `/npcs` entries.
