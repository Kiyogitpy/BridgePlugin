# Mahogany Homes routine — design

Date: 2026-08-27
Status: approved by the user in chat (tier, travel method, provider approach, contract-request
method, and click strategy each confirmed individually).

## Goal

A new `routines/mahogany_homes.py` that runs **Adept-tier** Mahogany Homes contracts unattended:
request a contract via the NPC Contact spell, travel to the assigned homeowner's house, remove/
build/repair every hotspot in it, turn the contract in, and repeat — restocking teak planks,
steel bars, teleport tablets, and NPC Contact runes from the bank as needed.

## Researched facts

Sources: `data/raw/M/Mahogany Homes.txt`, `data/raw/M/Mahogany Homes_Mechanics.txt`, and the
**decompiled reference plugin** — `~/.runelite/plugins/mahogany-homes_*.jar` (sideloaded,
`thestonedturtle.mahoganyhomes`, decompiled with CFR 0.152 into
`data/decompiled/mahogany-homes-plugin/`) — plus four human recordings of Adept-tier play
(`recordings/2026-08-27_{174045,175143,180346,181548}_mahogny-homes{,2,3,4}.*`, ~11 min each,
20 total contracts across all 4 cities). `data/decompiled/shortest-path-plugin/` was also pulled
(same sideload-and-decompile method) to confirm `travel.py`'s existing Shortest Path integration
already covers everything needed here — see Architecture §3.

**Contract mechanics** (`Home.java`, `Hotspot.java`, `HotspotObjects.java`,
`RequiredMaterialsByTier.java` — all verbatim game data from the plugin, not guessed):

- 12 homeowners across 4 cities: Jess/Noella/Ross (Ardougne), Larry/Norman/Tau (Falador),
  Barbara/Leela/Mariah (Hosidius), Bob/Jeff/Sarah (Varrock). Each has a fixed `WorldArea`
  (real overworld coordinates — **not instanced**, confirmed by the coordinate ranges being
  ~1700-3200, unlike the ~13000s signature the instancing gotcha in `CLAUDE.md` warns about),
  a homeowner NPC id, a standing/talk-to tile, an optional set of ladder object ids, and a
  per-tier `(minPlanks, maxPlanks, minSteelBars, maxSteelBars)` cost.
- 8 varbits (10554-10561) each track one furniture "hotspot" slot. A slot **requires attention**
  when its value is **1, 3, or 4** (ported verbatim from
  `MahoganyHomesPlugin.doesHotspotRequireAttention` — this is more specific than the wiki's
  informal "3/2/1/0" table and is the value actually used by working plugin code). Not every
  home uses all 8 slots (Ross/Norman/Tau have 7; Sarah/Jess have 8, etc.).
- **The same in-game object id is reused across a hotspot's whole lifecycle** — e.g. object
  39990 shows as "Bookcase" (action `Remove`) before it's cleared and "Bookcase space" (action
  `Build`) after, confirmed directly in the recordings (`Remove -> object Bookcase (39990)` then
  later `Build -> object Bookcase space (39990)`). Some slots only ever need `Repair` (clocks,
  mirrors, sinks, ranges, hat stands, a bathtub) and never go through Remove/Build at all.
  **This is why work_phase is one unified loop, not three phases** — see Architecture §4.
- `Hotspot` also gives, per varbit slot, the **full set of object ids** that can ever render at
  that slot across every home and tier (6-12 ids per slot) — a cheap cross-check for "is this
  really a Mahogany Homes hotspot" independent of the live action name.
- **There is no varbit for "which contract is currently assigned."** The reference plugin's own
  approach is to regex the *contractor's dialogue widget text* for the home name and a separate
  chat-message regex for the tier. **This routine doesn't need either lookup**: the recordings
  show the single game-message chat line printed once a contract is accepted already contains
  both — `"Adept Contract: Go see Jeff in the middle of Varrock, west of the museum."` — so one
  regex against that one line (`r"(\w+) Contract: Go see (\w+)[,.]"`, group 1 = tier, group 2 =
  the homeowner's first name) gives tier and home in a single step. This is a deliberate
  simplification over the reference plugin's split widget+chat approach, made possible because
  `LastChatMessageProvider.java` (`/last-chat-message`) already captures every GAMEMESSAGE with
  its tick — confirmed present and already wired into `ControllerPlugin.java`. It also removes
  the plan's only real risk item: no widget id needs verifying against a live client before this
  works, since chat message text is stable, cosmetic-only game content. The same regex family
  catches contract completion (`"You have completed [\d,]* contracts with a total of [\d,]*
  points\.?"`, ported verbatim from the reference plugin's `CONTRACT_FINISHED` pattern) to reset
  `currentHome` to null.

**Contract request**: the user has NPC Contact (Lunar spellbook) configured so that a single
left-click cast auto-selects the last-used tier, which is set to Adept. No contractor NPC visit,
no tier-selection dialogue to navigate — confirmed by the user, overriding what the recordings
show (the recordings predate this NPC Contact setup and instead walk to a contractor each time).

**Clicking**: the user has RuneLite's menu-entry swapper configured so `Remove`/`Build`/`Repair`
are always the left-click default on a hotspot object, the same trick `chinchompa_hunting.py`
relies on for its trap objects. **Plain left-click, no menu escalation**, per explicit user
instruction — golem_crafting's 3-tier hijack escalation exists because other players camp on
shared plinths in a public area; these are private house interiors with no such contention, so
that machinery would be unused complexity here.

**Travel**: mirror the recordings — Varrock/Ardougne/Falador teleport tablets, Xeric's talisman
for Hosidius, restocked from the bank, rather than walking cross-map on every contract.

## Architecture

### 1. New provider: `MahoganyHomesProvider.java`

Ports the plugin's static data (`Home`, `Hotspot`, `HotspotObjects`, `RequiredMaterialsByTier` —
copied close to verbatim, same object ids and constants) plus the state-tracking subset of its
logic, simplified per the chat-regex discovery above: an `EventBus`-subscribed `onChatMessage`
matches the assignment line for home+tier and the completion line to clear it (see Researched
facts), plus the varbit map and `getCompletedCount`/`getRepairableVarbs`/
`getRequiredMaterialsForVarbs`. **Explicitly not ported**: overlays, hint arrows, world-map pin,
teleport-item highlighting, dialogue-widget polling (superseded by the chat regex), and config
persistence across client restarts (a bot-only session; losing `currentHome` on a client restart
mid-contract is an acceptable gap — the next assignment/completion chat message recovers it,
since the contract itself isn't lost server-side).

One endpoint, `GET /mahogany-homes`, uncached (not one of the six `TickCache`-gated providers):

```
hasContract: bool
homeName: string | null          # enum name, e.g. "JEFF"
contractTier: int                # 0 = none assigned
npcId: int
standingLocation: {x, y, plane}
area: {x, y, width, height, plane}
ladderObjectIds: [int]
remainingCount: int              # hotspots still requiring attention
requiredMaterials: {minPlanks, maxPlanks, minSteelBars, maxSteelBars}
```

### 2. Routine wiring

`routines/mahogany_homes.py` + `RoutineChoice.MAHOGANY_HOMES` +
`ROUTINE_FILES["MAHOGANY_HOMES"]` + a `ControllerConfig.java` toggle (needed here, unlike
`gemstone_crab.py`-style routines, because tablet/rune restock thresholds are configurable
through `GET /config`).

### 3. Travel

`travel.walk_to()` handles both the final approach to a home's `standingLocation` and in-house
floor changes — its existing Shortest Path integration already auto-executes single-click
transports (doors, staircases, ladders), confirmed by reading the decompiled
`shortest-path-plugin` transport loader alongside `travel.py`'s `_use_transport`/
`_transport_is_executable`. No per-house stair logic needs writing or hand-verifying beyond the
`Home.area`/`ladderObjectIds` data the provider already carries over. Between cities: break the
relevant teleport tablet from inventory (a plain inventory-item action, no new primitive) rather
than walking cross-map.

### 4. `tick()` phases (`ctx.state.data["mh_phase"]`)

1. **bank_phase** — top up teak planks (into the plank sack) + steel bars, restock the three
   teleport tablets, verify Xeric's talisman charge and NPC Contact rune stock (astral/cosmic +
   whatever spellbook-swap needs), confirm hammer+saw carried. Entered at startup and whenever
   `work_phase` finds materials have run out.
2. **request_contract_phase** — `hasContract == false`: left-click NPC Contact, then poll
   `/mahogany-homes` until `hasContract && contractTier == 3`.
3. **travel_phase** — teleport toward the assigned city, then `walk_to(standingLocation)`.
4. **work_phase** — while `remainingCount > 0`: scan `/objects` in `area` for an object offering
   `Remove`, `Build`, or `Repair` (cross-checked against the ported hotspot id set), walk to it
   and plain-left-click (`click_entity` with `refresh_target`/`refresh_legs`, no menu
   escalation), confirm via `remainingCount` dropping or an XP-gain check (per `CLAUDE.md`'s
   general sub-tick XP-signal pattern). Remove and Build chain naturally through the same object
   id across successive scans — no special-casing needed. If no actionable object is found while
   `remainingCount > 0` and materials should be sufficient, treat it as an out-of-materials
   condition (same lesson as golem_crafting's silent-menu-absence bug) and go to `bank_phase`.
5. **turn_in_phase** — `remainingCount == 0`: walk to `npcId`, Talk-to, accept the tea, loop back
   to `bank_phase` (if low) or `request_contract_phase`.

## Explicitly out of scope

- **Beginner/Novice/Expert tiers** — Adept only, per the recordings and the user's choice. The
  ported data model covers all tiers for free if this changes later, but no other tier's flow is
  exercised or tested.
- **Contractor-NPC dialogue navigation** — replaced entirely by NPC Contact; no code path visits
  Amy/Marlo/Ellie/Angelo.
- **Menu-escalation / hijack handling for hotspot clicks** — plain left-click only, per the
  menu-swap assumption above. If this assumption turns out wrong for some house layout, that's a
  live finding to fix then, not something to build defensively for now.
- **Config persistence of `currentHome` across a client restart** — mid-contract state recovers
  from the live client/provider on the next poll, not from anything saved to disk.

## Verification

`py_compile` every touched Python file and a Gradle compile for the Java provider, then the full
`pytest` suite, plus new unit tests for the pure phase-transition logic (work_phase's
target-selection, the out-of-materials heuristic, the assignment/completion regexes) against
synthetic data. First live run should watch one full contract end-to-end (request → travel →
work_phase → turn-in) before leaving it to loop, per the project's live-debugging workflow — this
is a brand new routine with no prior live run at all, unlike golem_crafting/chinchompa_hunting's
already-debugged baselines. Nothing in this design depends on an unverified widget id, so the
first live run is confirming behavior, not discovering ids.
