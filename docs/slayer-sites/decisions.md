# Slayer task sites — decisions (2026-09-18)

Decided by the user in an interactive pass, task by task, after the research in this folder was
compiled. This file is the input to the task catalog (`slayer_tasks.py`, spec §6,
`docs/superpowers/specs/2026-09-18-slayer-task-loop-design.md`). Every task has one of three
**dispositions**:

- **FIGHT** — travel to the chosen site and kill the task.
- **SKIP** — cancel through the Slayer Rewards interface for 30 points (streak kept), then ask
  the same master again.
- **STOP** — safety-stop and leave the task for the user to do by hand.

## Account facts learned in this pass

- The house has a **fairy ring** and an **ornate jewellery box**. The POH is therefore the
  travel hub: house teleport, then ring or box. The panel's Player-Owned House section must
  have the fairy ring and ORNATE box set, or none of the ring/box routes below will plan.
- Aya and Kuradal are the post-*While Guthix Sleeps* names for Turael and Duradel; one task list
  each (`Turael_Slayer assignments`, `Duradel_Slayer assignments`).
- **Dogs are blocked at Aya**, so that task is never assigned.
- The user runs the **Turael Skipping** Plugin Hub plugin, and wants its spots used for Aya.

## Aya (Turael list) — source of truth is the Turael Skipping plugin

`aya-turael-skipping-registry.txt` is a verbatim dump of the plugin's own
`SlayerTaskRegistry.TURAEL_TASKS` (jar `turael-skipping_cfQKan5V...jar`, dumped reflectively by
`DumpTuraelSkippingRegistry.java` against runelite-api 1.12.39 — its data, not a
reconstruction). Per task it gives: the NPC ids that count, the named spot, the kill area as
`WorldArea` rectangles, a target tile (`shortestPath`), a gear note, and the plugin's teleports
in preference order.

Rules for using it:

1. The site, NPC ids, kill area and target tile are taken **as they are**.
2. Travel: try the plugin's teleports **in its listed order** and take the first one bank memory
   says the account owns. "Fairy ring: XYZ" means house teleport → POH fairy ring → XYZ.
   "Ornate Jewellery Box: ..." means house teleport → box.
3. One spot serves three tasks: **Alice's Farm** (Ectophial) covers Zombies (undead cow 2992,
   undead chicken 2993), Birds (1838, 1839, 2993) and Cows (2992).
4. Gear notes from the plugin: cave bugs and cave slimes need a **light source**, lizards need
   **ice coolers**, skeletons (Digsite Dungeon) need a **rope**. Banshees need earmuffs or a
   slayer helmet (wiki; the plugin's note is empty there).

User overrides of the plugin's picks:

| Task | Plugin spot | User decision |
|---|---|---|
| Dogs | East of Sophanem (Pharaoh's sceptre) | **Blocked at Aya** — never assigned; no catalog entry needed beyond a name that resolves |
| Monkeys | Kruk's Dungeon (glider, trapdoor, light source) | **Karamja monkeys** instead ("closer on Karamja"). Wiki pin ~2873,3148,0 near the volcano, 4 spawns; glory / ornate box → Karamja (Musa Point 2918,3176). Exact tile to confirm on the first visit |
| Minotaurs | Stronghold of Security via skull sceptre | Same site (area x=1869,y=5210,w=17,h=14; target 1875,5218), but travel via **ornate jewellery box → Cooking Guild** (skills necklace destination), then walk to the Barbarian Village entrance and down |

`aya-wiki-candidates.md` is the wiki-derived alternative list compiled before the plugin was
chosen. It is kept as a fallback reference only. It also records that `SlayerLocations.java`
points Zombies, Scorpions and Wolves at Wilderness or poorly documented spots.

## Kuradal (Duradel list) — 43 tasks

### FIGHT (22)

| Task | Site | Access | Needs | Notes |
|---|---|---|---|---|
| Aberrant spectres | Stronghold Slayer Cave | slayer ring → Stronghold Slayer Cave, cave entrance | nose peg or slayer helmet | safespots |
| Abyssal demons | Slayer Tower basement (task-only) | slayer ring → Slayer Tower, basement stairs | Slayer 85 | the only "hard" task kept; their teleport can move the player a few tiles |
| Ankou | Stronghold Slayer Cave | as above | — | 50-80 kills |
| Basilisks | Fremennik Slayer Dungeon | slayer ring → Fremennik | mirror shield; 80-point unlock | defaulted (one sensible site) |
| Black demons | Taverley Dungeon | Taverley tablet / house → box, then the dungeon | — | 24 spawns, safespot; long walk without agility shortcuts |
| Bloodveld | Stronghold Slayer Cave | slayer ring | — | Protect from Melee negates them |
| Cave horrors | Mos Le'Harmless Cave | (no close item teleport found — settle at implementation) | witchwood icon, light source; Cabin Fever | defaulted (only site) |
| Dagannoth | Catacombs of Kourend | Xeric's talisman → Xeric's Heart, statue down | Horror from the Deep (task req) | melee-only variant, three safespots |
| Dust devils | Catacombs of Kourend | as above | facemask or slayer helmet | user chose Catacombs over the Smoke Dungeon |
| Elves | **Lletya** | teleport crystal → Lletya | Regicide | user chose Lletya although it has only 3 elves (1 archer + 2 warriors); expect a slow task |
| Fire giants | Stronghold Slayer Cave | slayer ring | — | task-only, per-group safespots |
| Gargoyles | Slayer Tower basement | slayer ring, basement stairs | rock hammer finish, or granite hammer as weapon, or the auto-smash unlock | defaulted |
| Gryphons | Great Conch, **eastern task-only cave** | house → POH fairy ring → **CJQ** (ring at 3178,2447; needs one prior visit) | Slayer 51, Sailing 45, Troubled Tortugans, 80-point unlock | keep equipped weight ≥ ~30 kg to avoid the knockback; no documented safespot in the eastern cave |
| Hellhounds | Stronghold Slayer Cave | slayer ring | — | 13 spawns, safespots |
| Kalphite | Kalphite Cave (task-exclusive) | desert amulet 4 → Kalphite cave, else house → fairy ring BIQ | antipoison recommended (poison ignores prayer) | defaulted; same site as Aya's kalphite task |
| Kurask | Fremennik Slayer Dungeon | slayer ring → Fremennik | **leaf-bladed weapon or broad ammo** (Magic Dart is standard spellbook, not available on Lunars) | 14 spawns, safespottable |
| Mutated zygomites | Zanaris, near the Cosmic Altar | house → POH fairy ring → Zanaris | fungicide spray with charges; Lost City | pillar safespot; ~2412-2421,4370-4377 |
| Nechryael | Slayer Tower basement (task-only) | slayer ring, basement stairs | Slayer 80 | death spawns hit through Protect from Melee |
| Suqahs | Lunar Isle, **south-west** group | Moonclan teleport (Lunar spell the account already has) | — | melee only, single combat; ~2064-2106,3853-3882 |
| Trolls | Death Plateau | games necklace / box → Burthorpe, short walk | — | defaulted; Protect from Melee negates them |
| Waterfiends | Ancient Cavern | games necklace / box → Barbarian Outpost, whirlpool | ranged for the across-the-river safespot | defaulted |
| Wyrms | Karuulm Slayer Dungeon, task-only south batch | Rada's blessing → Mount Karuulm (or fairy ring CIR) | boots of stone/brimstone/granite unless elite Kourend is done | defaulted; Protect from Magic at range negates them |

"Defaulted" means there was one sensible site, so it was not put to the user as a question;
the user was told and did not object. Coordinates and full candidate details for every row are
in `kuradal-1a.md`, `kuradal-1b.md`, `kuradal-2a.md`, `kuradal-2b.md`.

Four hubs cover 12 of the 22: **Stronghold Slayer Cave** (spectres, ankou, bloodveld, fire
giants, hellhounds), **Slayer Tower basement** (abyssal demons, gargoyles, nechryael),
**Fremennik Slayer Dungeon** (basilisks, kurask), **Catacombs of Kourend** (dagannoth, dust
devils). Build those travel routes first.

### STOP (1)

| Task | Why |
|---|---|
| Greater demons | The user does this task by hand on **tormented demons**, which count for it. The routine safety-stops with a message naming the task. |

### SKIP (20)

Boss, Dark beasts, Warped creatures, Vampyres, Araxytes, Drakes, Fossil Island wyverns,
Lizardmen, Red dragons, Aquanites, Aviansie, Black dragons, Blue dragons, Cave kraken, Frost
dragons, Metal dragons, Skeletal wyverns, Smoke devils, Spiritual creatures, TzHaar.

## What the skip list costs, and what to do about it

By task weight (`Duradel_Slayer assignments.txt`): FIGHT 140, STOP 9, SKIP 169, total 318. If
every task were assignable, a Kuradal roll would be a SKIP **53%** of the time. The account
probably lacks several point-unlock tasks (Boss, Warped, Vampyres, Lizardmen, Red dragons,
Aquanites, Aviansie, TzHaar = weight 69, never assigned without their unlock); without those
the SKIP share is about **40%**.

At 40%, each milestone costs on average 0.67 cancels ≈ 20 points against the 75+ gained, which
is fine. But three SKIPs in a row happen about once in every 16 milestones, and
`slayerMaxSkips` defaults to 3, so the routine would safety-stop that often. Two fixes:

1. **Raise the `slayerMaxSkips` default to 8** (six in a row at 40% is 0.4%). The points
   reserve is the real guard against draining points. One-line Java default; do it in the
   sub-project 2 plan.
2. **Block the heaviest skipped tasks at Kuradal** (100 points each, up to 7 slots): Metal
   dragons (14), Dark beasts (11), Araxytes (10), Black dragons (9), Cave kraken (9), Smoke
   devils (9), plus Drakes or Red dragons (8). That takes the SKIP share from about 40% to
   about 17%. This is the user's manual call; the routine needs no change to benefit.

## Corrections this pass found in existing data

- `SlayerLocations.java` points **Hellhounds, Greater demons, Spiritual creatures, Black
  dragons, Zombies** at Wilderness sites, and marks Edgeville Dungeon's **black demon** spot
  non-Wilderness although the wiki lists it as level 5 Wilderness. The Combat routine's
  location dropdown inherits those. The catalog must not.
- `SlayerMonsters.java`'s Red dragons entry lacks **Baby red dragon**, which counts for the task.
- Gargoyles: the table only has the Slayer Tower top floor; the basement is the better site.
