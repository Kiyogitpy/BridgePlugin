# Mastering Mixology: unattended live run, 2026-09-22

**Result: the run reached the intended stop.** At 10:12:22 it stopped with *"none of the
configured lye herbs (['Toadflax', 'Avantoe', 'Kwuarm']) are in the bank"*. Bank memory (all
tabs) confirmed it: 0 Toadflax, 0 Kwuarm, and 1 Avantoe (not picked up; the routine found no
lye herb in tab 8). The hopper was left at **mox 884 / lye 981 / aga 1164**.

The client ran on the **host**, not the VM (bridge on localhost:8080, Arduino on COM6).
`controller.py` ran on the host too, from 08:11 to 10:13 (about 2 hours), across six launches
(runs a–f). Each relaunch loaded a fix; nothing was restarted for any other reason.

## Totals

| Run | Window | Cycles (3 potions each) | Lever retries (1st / 2nd) | Bank trips | Speedup hits | Ended by |
|---|---|---|---|---|---|---|
| a | 08:11–08:19 | 9 | 10 / 0 | 2 | 9 | restart (hover fix) |
| b | 08:20–08:24 | 5 | 2 / 1 | 2 | 9 | **safety stop** (lever bug, fixed) |
| c | 08:30–09:12 | 48 | 20 / 1 | 11 | 85 | restart (verdict fix) |
| d | 09:13–09:48 | 41 | 19 / 0 | 10 | 77 | restart (hover-check fix) |
| e | 09:48–10:09 | 25 | 11 / 0 | 2 | 51 | restart (camera-glide fix) |
| f | 10:09–10:13 | 0 | – | 1 (the empty one) | – | **herb safety stop, the intended end** |

- **128 cycles = 384 potions** delivered to the belt. Median cycle time **47–48 s**, including
  a bank/refine trip every few cycles; the fastest was 36 s.
- There was never a third lever retry, and no stall or STUCK warning. The hopper refill ran on its
  own 7 times.
- About a third of the `[!] Misclick` lines are the deliberate humanization misclicks
  (`MISTAKE_MISCLICK_CHANCE`); every one was corrected by the reclick.

## Bugs found and fixed (all committed on `dev`)

1. **`1a0f6ed`: the banked-paste search broke the herb-tab switch** (the stop you pasted
   this morning). The paste step searched the bank for each paste type, and the second click on
   the search icon toggled the first search OFF, so "Aga paste" was typed into nothing and the
   herb-tab switch then failed. Per your note, paste is read straight off tab 8 now, and the
   step never searches. **Verified live**: every bank trip switched to (or was already on) tab 8,
   with no search.
2. **`1a0f6ed`: the hover fix you asked for** ("the hover not accounting for our movement … levers and
   take from mixing vessel"). Two causes:
   - `hover_entity` set off toward the bulk-scan **snapshot**, not the live position.
     `click_entity` got this fix on 2026-08-30 but `hover_entity` never did. Fixed in
     `controller.py`, so every routine gets it.
   - `_hover_when_settled` parked the cursor after at most 0.4 s, which is mid-walk, and left it
     there while the player kept walking. The next "in place" click then hit empty ground
     (`last-action target=''`) and the retry flew about 27 px. The hover now **follows** the
     target, re-aiming once the live projection drifts more than 6 px, until the player has
     arrived.
3. **`1b605b5`: the follow waited on a lagging flag, and the lever aim sat off the model.**
   MEASURED: `/player`'s `moving` reads false a full tick (~0.5 s) *after* arrival, while the
   lever/belt animation is already playing. So the follow now ends on the caller's own outcome
   (the mixer slot changed, the base is in the inventory).
   **Safety stop in run b:** the lever pre-hover sat at projection + the default (-7, +7) nudge,
   just off the thin lever model. Each retry counted as "already in place" and clicked the same
   empty pixel: three misses in 1 s. Levers are now aimed at their centre (`offset=(0,0)`, the
   small-target rule in CLAUDE.md), and a retry always re-aims first.
4. **`5c170fe`: a correct lever click was rejected when it landed in the same tick as the
   miss before it.** `/last-action` has no per-click sequence, only the game tick, and a retry
   0.27 s after a miss fell in the same tick. In run c this rejected an "Operate / Mox lever"
   that was already on screen, one attempt short of a safety stop. A same-tick action now counts
   when the baseline did not already name this lever. After this there was no second retry in
   runs d and e (66 cycles).
5. **`0a62f9c`: the camera glide.** The remaining lever misses were the *second*
   pull of a run. The click was made in place about 20–30 px below where the lever had slid,
   because the camera was still gliding after the first pull landed. The follow now continues
   until the projection agrees twice, capped at 0.4 s. **NOT verified live**: run f went
   straight into the empty bank trip and stopped.

`f768788` (run e's build) also added a `/hover` read before each lever click. **It did not reduce
the retries** (0.44/cycle in e against 0.42 in c/d): the lever was under the cursor when
checked and slid off before the click landed. It is harmless (it re-aims only on a mismatch
and never blocks), so I kept it; fix 5 is the real answer to what it was aimed at.

## Open items

- **First thing to watch next session:** the lever retry rate with fix 5 in place. Baseline:
  about 0.4 single retries per cycle, each costing about 0.5 s.
- **My last restart brought the stop forward.** Mid-run the routine only banks once a paste
  drops below `hopperLowWater` (150). But every fresh START begins in the bank phase, which
  tops everything up to `hopperTarget` (1000). Restarting for fix 5 sent run f straight to the
  bank with 884/981/1164 still in the hopper, enough for many more cycles, and that is where it
  found no lye herbs. Left alone it would have mixed on and hit the same stop later. A small
  change would skip the start-up bank trip when every paste is above low water; say if you
  want it.
- **Restock lye herbs** (Toadflax / Avantoe / Kwuarm) before the next run. Mox and aga herbs
  are still in the bank: grimy harralander 668 + harralander 763 for mox; irit, cadantine,
  lantadyme, dwarf weed and torstol for aga.
- The launch step steals window focus. The first run sat "not focused" for 15 s until I
  refocused the client with `tools/vm-guest-focus-client.ps1`, which works on the host too.

Logs: `ControllerAddon/logs/mm_live_20260922_{a..f}.log`.
