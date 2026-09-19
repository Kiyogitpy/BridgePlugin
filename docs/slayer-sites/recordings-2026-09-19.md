# Slayer loop: facts MEASURED from the 2026-09-19 human recordings

Source: `ControllerAddon/recordings/2026-09-19_1317*_contact-kuradal`, `_1318*_house-box-and-ring`,
`_1321*_rewards-open` (gitignored; these are the facts worth keeping). Account state at the time:
Nechryael 92/213, streak 11, 9 points. `GET /slayer` answered correctly live for the first time
(`taskName` decoded, `masterId` 5, `hasTask` true).

## NPC Contact -> a master (contact-kuradal)

| Step | Measured |
|---|---|
| Open spellbook | `Magic` tab = widget `161.65` (F-key is the cheaper route) |
| Cast | widget `218.113`, option `Cast`. Outcome +0.3s: player anim `4413`, spotanim `728`, varbit `12393` = 1 |
| Picker appears | ~4.4s after the cast (varbits 1624-1626 and varp 261 change on the same tick); Magic +63 xp lands then too |
| Pick the master | widget `75.4`, **dynamic child slot 33 = Kuradal**, option `Contact Kuradal`. Aya's slot: NOT yet measured |
| Dialogue opens | +0.7s: chat `Kuradal|'Ello, and what are you after then?`, varbit `5006` = 11, varbit `10670` = 1 |
| Ask for a task | Space, then option **1** = `I need another assignment.` |
| With a task active | `Kuradal|You're still hunting nechryael; you have 92 to go. Come back when you've finished your task.` |
| Closed | Space; varbit `12393` back to 0 |

- Whole exchange: 17s cast-to-closed at human pace.
- varbit `12393` is 1 for the duration of every teleport AND this dialogue -- a "busy" flag, not slayer-specific.
- NOT captured: the option LIST text (the recorder logs the key, not the menu). `dialogue.options()` reads it live.
- NOT captured: a real assignment line. That is the `contact-aya` recording, after this task ends.

## NPC Contact -> Aya, a REAL assignment (contact-aya, no task active, streak 12)

Same spell and picker; **Aya is `75.4` slot 18** (Kuradal 33). Slots are never hardcoded: the
orchestrator reads the picker's children and clicks the one whose action is `Contact <master>`.

| Step | Key | Chat |
|---|---|---|
| greeting | Space | `Aya|'Ello, and what are you after then?` (varbit `5006` = 6 for Aya, 11 for Kuradal) |
| option list | **1** | `07 Kiyo|I need another assignment.` |
| **EXTRA STEP a strong account gets** | Space | `Aya|You're actually very strong, are you sure you don't want Vannaka under Edgeville to assign you a task?` |
| option list (texts not captured) | **1** | -- insists on Aya |
| **the assignment** | Space | `Aya|Excellent, you're doing great. Your new task is to kill 15 Bears.` |
| option list (texts not captured) | **2** | `07 Kiyo|Okay, great!` (option 1 is presumably the tips question) |
| close | Space, Space | `Aya|Good luck! Don't forget to come back when you need a new assignment.` |

- **The assignment lands on the tick the task LINE appears, before the dialogue is finished:**
  varbit `4067` (master) = 1, varp `394` = 15, varp `395` = 13 all on tick 3892. `/slayer` then read
  `Bears`, 15/15, `masterId` 1, streak still 12, `hasTask` true. So success is confirmed from
  `/slayer`, and the remaining Space/2/Space presses are only tidying up.
- Assignment regex candidate: `Your new task is to kill (\d+) (.+)\.` -- used for logging only;
  the state read is the truth.
- The option lists must be answered by TEXT through `dialogue.choose` ("assignment", then whatever
  declines Vannaka, then "Okay"), never by number: the Vannaka question shifts what "1" means.
- 38s cast-to-assigned at human pace, 46s to fully closed.

## The house (house-box-and-ring)

- `Break` a `Teleport to house` tablet (item 8013): anim `4069` -> `4071`, spotanim `678`, tablet gone at +1.5s,
  two LOADING flickers, settled ~4.1s after the click. Lands INSIDE at **(1922,7051) plane 1** both times
  (varbit `6719` = 2 while loading, 0 once in).
- Coordinates inside were stable across two visits: **Fairy Ring id 29228 at (1963,7044) p1**,
  **Ornate Jewellery Box id 29156 at (1934,7105) p1**. Both were clicked from the landing tile
  (41 and 54 tiles away) and the game walked there itself -- no routing inside the house is needed,
  only the object being on screen.
- Fairy ring: `Last-destination (BLR)` (third option) observed NOTHING when clicked from 41 tiles.
  What worked: `Configure` (second option) -> travel-log entry widget `381.20` (`Use code A J P`)
  -> `Confirm` widget `398.26`. Then anim `3265`, spotanim `569`, anim `3266`, landed (1651,3010) p0 ~3.3s later.
  The dialled code shows in varbits `3985`/`3986`/`3987`.
- Jewellery box: `Teleport Menu` (second option) -> interface **590**, destination = widget `590.5`
  dynamic child, **slot 8 = `H: Cooking Guild`** (the text carries its keyboard shortcut letter).
  Click -> anim `714`, spotanim `111`, landed beside the Cooking Guild ~2.8s later.
- The pool was not used in this recording.

## Slayer rewards (rewards-open)

- The user did NOT use NPC Contact for this: **Aya stands in Burthorpe** -- npc id **13619** at
  (2932,3536), 35 tiles from the games necklace landing (2897,3554) -- and has a right-click
  **`Rewards`** (NPC fifth option). Opening Rewards with a task active does not touch the task.
- Rewards interface = group **426**; the `Tasks` tab is widget **`426.14` slot 6**.
- The cancel button was not clicked (9 points, 30 needed), so its widget id is still unknown.
- varp `261` = kills left and varp `262` = task id were pushed when the Rewards option fired.

## The end of a task (slayer-task-end, captured by a watcher while `combat.py` fought)

`GET /slayer` was polled ~3x/s through the last 12 Nechryael kills (Slayer Tower top floor, plane 2)
with the recorder running; `ControllerAddon/logs/slayer_watch.log` has every state change.

- **Each kill**: varp `394` drops by one on the SAME tick as the Slayer xp drop (+113), which is the
  tick the NPC dies -- 1-2 ticks BEFORE its `npc gone`. `XP_GAINED` (Slayer) is therefore the pushed
  signal for "a kill counted"; `/slayer` agreed on the next poll every time.
- **The last kill, all on tick 3567**: varp `394` = 0, varbit `4069` (streak) 11 -> 12, varbit `4068`
  (points) 9 -> 24. One tick later, two chat lines:
  `You have completed your task! You killed 186 Nechryael. You gained 20,980 xp.` and
  `You've completed 12 tasks and received 15 points, giving you a total of 24; return to a Slayer master.`
- `/slayer` after it: `count` 0, `hasTask` false, `streak` 12, `points` 24, **`taskName` ABSENT** and
  **`taskId` still 52** -- varp 395 is NOT cleared. "No task" must be read from `count`/`hasTask`,
  never from the task id. `slayer_state.from_bridge` already does this.
- 15 points for task 12 from master id 5: a plain (non-milestone) reward, matching spec F4.
- The chat says 186 killed against `originalCount` 213. Not explained; do not build on either number.
- **`combat.py` kept attacking after completion** (it clicked a Nechryael 1.1s later). Expected --
  it knows nothing about tasks -- and it is the orchestrator's job to stop the fight on `count == 0`.
- Pace at this site with this gear: ~22s per kill (12 kills in 4m26s), close to the 18s default
  the prayer-dose formula assumes.

## What this changes in the spec

1. **Cancelling does not need NPC Contact.** Games necklace -> Burthorpe -> `Rewards` on Aya is a
   two-click, dialogue-free path, and it is safe with a task active (Rewards is not a task change).
   USER 2026-09-19: "You can cancel from the spell too" -- the NPC Contact dialogue also reaches
   the rewards screen, so the spell stays the PRIMARY cancel path (no travel, no necklace charge)
   and the Burthorpe walk is the fallback. Which dialogue option opens it is not yet measured.
2. **Aya can also be reached on foot** from the same necklace, which is a fallback for the 9 filler
   tasks if the spell path fails (no runes, wrong spellbook).
3. The POH hub is simpler than §8.1 feared: fixed coordinates, objects clickable from the landing tile.
