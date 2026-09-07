# Herb run — design

**Date:** 2026-09-07
**Status:** design approved in chat, not implemented
**Sources:** the user's own recording (`ControllerAddon/recordings/2026-09-07_093519_farm-run.*`,
9m46s, 171 actions, 9 of 10 patches), Quest Helper's `HerbRun.java` (pluginhub corpus, commit
`94acc61`), and RuneLite's own `net.runelite.client.plugins.timetracking.farming` package.

## Why

A herb run is a fixed lap of ten patches, and almost all of its difficulty is knowledge rather
than mechanics: *which* patches are ready, *how* to get to each one, and *what* to carry. All
three are already solved in code we can read, so this design is mostly a porting exercise plus
the routine that drives it.

It is also the first real consumer of the transport graph added 2026-09-07: Quest Helper models
each patch as "object + WorldPoint + the teleport item that gets you there", and every one of
those teleports is now a plannable edge rather than a hardcoded case.

## The two facts that shape everything

**1. A patch object's id encodes its crop AND growth stage.** MEASURED in the recording: the
same Ardougne-style herb patch appears as `Ranarr weed (8150 / 8151 / 8152 / 8153 / 18816 /
27115)` and `Limpwurt plant (7847 / 7850 / 27111 / 50693)`. There is no stable "herb patch" id
to scan for. **Patches are therefore addressed by TILE, never by object id**, and their state
comes from varbits, not from what `/objects` reports.

**2. Away from a patch, state is a PREDICTION, not a read.** RuneLite's `FarmingTracker` stores
the last observed `FARMING_TRANSMIT_*` varbit plus a timestamp per patch, and predicts current
growth from elapsed time. The raw varbit is only accurate while standing in the region. Since
the whole point is deciding what to skip *before* teleporting, the routine must consume the
prediction, not the varbit.

## Farming state (Java)

Vendor the non-UI half of RuneLite's `timetracking.farming` — `FarmingWorld`, `FarmingPatch`,
`FarmingRegion`, `FarmingTracker`, `CropState`, `Produce`, `PatchPrediction`, `CompostTracker`,
`TimeTrackingConfig`.

**Into `com.example.farming.vendored`, NOT the original package.** This differs from the m8aq
precedent deliberately: `net.runelite.client.plugins.timetracking.farming` already exists in the
client jar, so vendoring in place would be a split package. Every reference plugin renamed for
the same reason (`com.questhelper...farmruns`, `com.mystix.runelite.farming`,
`com.homeassistant.runelite.farming`). The m8aq *rules* still hold: never edit a vendored file,
wrap behaviour in the provider, and record the pinned upstream commit in a `VENDORED.md`.

`FarmingProvider` is the only adapter, serving `GET /farming`:

```
{ "patches": [ { "name": "Ardougne", "region": 10548,
                 "x": 2670, "y": 3374, "plane": 0,
                 "patchType": "HERB",
                 "produce": "RANARR", "cropState": "HARVESTABLE",
                 "stage": 4, "stages": 4, "readyAtUnix": 1757248800,
                 "known": true, "reachable": true } ] }
```

- **`known: false` is NOT `EMPTY`.** It means the tracker has never observed this patch. The two
  must not collapse — the same distinction that bit bank memory. Per the user's decision, an
  unknown patch is **visited anyway**: one wasted teleport is cheaper than leaving a ranarr patch
  unharvested for days.
- **`reachable`** is an access gate ported from Quest Helper, NOT from the RuneLite package
  (RuneLite's tracker has no notion of whether you can *get* to a patch) -- so this design
  combines two upstreams: Harmony needs
  Morytania elite, Weiss and Troll Stronghold need *Making Friends with My Arm* / *My Arm's Big
  Adventure*, Farming Guild needs Farming 65, Varlamore needs *Children of the Sun*, Morytania
  needs *Priest in Peril*. An unreachable patch drops out of the route silently, so the routine
  stays correct if the user unlocks Harmony later.
- **Startup floor assertion**, as `M8aqProviderTest` has: if patch discovery yields fewer than a
  known minimum, log loudly. A vendored reflective/table-driven component that silently finds
  nothing is the failure mode that class of code actually has.

## The routine (Python)

`routines/herb_run.py`. Phases are derived from live state every tick — never a stored counter,
because `ctx.state.data` resets on restart while the character's position does not.

**restock** → **travel** → **patch work** → next patch → **done**.

### restock

The bank list is the recording's, verbatim: farmer's outfit (strawhat / shirt / boro trousers /
boots), magic secateurs, rake, bottomless compost bucket, seeds, and the teleport items the
planned route needs. Two conditionals ported from Quest Helper rather than assumed:

- the **rake** is unnecessary when varbit `FARMING_BLOCKWEEDS == 2` (auto-weed),
- the **seed dibber** is unnecessary when Barbarian planting is unlocked
  (`BRUT_FARMING_PLANTING == 3`).

### travel

`teleports.plan(here, patch_tile, owned_ids, ...)` and the existing leg runner. Quest Helper's
per-patch teleport hints (Ardougne cloak 2+, Catherby tab, Explorer's ring 2+, Xeric's talisman,
skills necklace, Ectophial, stony basalt, icy basalt, Harmony tab, quetzal whistle) are used to
**seed the restock list**, not to choose the route — the graph chooses, so an account with a
better option uses it.

### patch work

Per patch, in order, each step confirmed rather than slept off:

1. **harvest** until the patch reports empty (repeat-click; herbs come several per patch),
2. **dead** → clear the patch; **diseased** → cure with plant cure, else clear,
3. **compost** — use the bottomless bucket on the patch,
4. **plant** — use the configured seed on the patch,
5. **note** — `Use <grimy herb> on Tool Leprechaun` (MEASURED in the recording: exactly this,
   ×4; the leprechaun stands beside each patch).

### ordering

Visit order is by travel cost from the current position, not a fixed list — the recording's own
order (Farming Guild → Catherby → Morytania → Hosidius → Farming Guild → Varlamore, with
Falador, Ardougne, Troll Stronghold and Weiss also covered) is a good default but not a
constraint. Patches that are `GROWING` and not yet ready are skipped entirely.

## Config

A Farming section in `ControllerConfig` (client rebuild), exported as a `herbRun` block:
seed choice, compost type, whether to cure disease or clear, and a per-patch include override on
top of the automatic `reachable` gate.

## Testing

- **Pure and unit-tested:** patch ranking (which patches to visit, in what order), the restock
  list derivation including both varbit conditionals, and the access gate. These take the
  farming state as an argument, so they need no bridge, map or client.
- **Java:** a floor assertion on patch discovery, and a `PatchPrediction` decode test.
- **Hermetic:** `tests/test_herb_run.py` gets a `_no_live_bridge` autouse fixture from the
  start — the 2026-09-07 lesson from `test_banker.py`, whose live `bank_memory.load()` made
  three unrelated tests flaky.
- **Stubs must match real payload shapes.** Two bugs on 2026-09-07 (`/equipment` is a slot dict,
  `get_varbit` returns a bare value) were each hidden by a stub that encoded the wrong
  assumption. Check the live shape before writing the stub.

## Live-verification checklist

1. Does `GET /farming` agree with RuneLite's own Farming tab for every patch, including the
   ones you are nowhere near?
2. Is the TimeTracking plugin enabled, and does it have stored observations for this account?
3. `known: false` — does a never-observed patch really report it, and get visited?
4. Does the `reachable` gate correctly drop Harmony and keep the other nine?
5. Does a teleport leg actually fire? **The transport graph's leg runner has never been
   exercised live** — a POH or Ectophial hop is unproven.
6. Tool Leprechaun noting: does `Use <herb> on NPC` land, and does it note the whole stack?
7. Does the compost/plant sequence work on a patch that is DEAD or DISEASED, not just empty?

## Out of scope (v1)

Tree, fruit tree, bush, hops, allotment, cactus and hespori patches; farming contracts; paying
farmers for protection; the Hespori; Kronos/attas seeds. Herb patches only.

## Known risks

- **The leg runner is unverified**, and this design leans on it for every patch. If POH or
  Ectophial hops turn out not to fire, travel degrades to walking, which for Weiss or Harmony
  is not viable — those patches would drop out until it is fixed.
- **The vendored tracker depends on RuneLite's TimeTracking plugin** being enabled and having
  observed the patches. If it is off, every patch reports `known: false` and the run degrades to
  a blind full lap — correct, but it pays every teleport.
- **One project, three concerns.** The user chose to keep farming state, the run engine and
  restock in a single project after I recommended splitting them. The Gauntlet's history says
  cross-piece interactions are where the Criticals live, so the implementation plan should stage
  them and re-review at each boundary rather than integrating at the end.
