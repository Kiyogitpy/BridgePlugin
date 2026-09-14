# BridgePlugin

## What this is

A RuneLite (Old School RuneScape) automation plugin ("Controller") paired with a Python
controller that drives a physical Arduino-connected mouse/keyboard over serial — not
software-injected input, to avoid detection. The Java plugin exposes an HTTP + UDP bridge
reporting live game state; the Python side reads that state and decides what to click.

## Architecture

```
RuneLite client (Java plugin, src/main/java/com/example/)
  └─ HTTP bridge on localhost:8080 (BridgeHttpServer.java, providers/*Provider.java)
  └─ UDP event stream on port 50555 (PLAYER_DIED, NPC_DIED, LEVEL_UP, HP_CHANGED, CHAT_MESSAGE,
        TICK, OBJECT_SPAWNED/DESPAWNED, GROUND_ITEM_SPAWNED/DESPAWNED, XP_GAINED, and since
        2026-09-04: VARBIT_CHANGED (subscribed ids only), WIDGET_LOADED/CLOSED, MENU_CLICKED,
        GRAPHIC_CHANGED, HITSPLAT, PLAYER_MOVING/STOPPED (per-frame), NPC_SPAWNED/DESPAWNED, PROJECTILE_SPAWNED (2026-09-05),
        GRAPHICS_OBJECT_CREATED (2026-09-06, ground spotanims: id/x/y/plane/tick, spawn-budgeted), ...)
        ↕
Python controller (ControllerAddon/)
  └─ controller.py     — engine: bridge polling, Arduino serial driving, panic key,
                          safety stops, human-movement realism (see settings.py)
  └─ routines/*.py     — one file per activity (slayer, mining, golem_crafting, ...);
                          controller.py loads whichever is passed on the command line
  └─ banker.py         — shared banking helpers (deposit, withdraw, pouches/bags)
  └─ travel.py         — shared walking helpers (Shortest Path plugin integration,
                          minimap/viewport hop-clicking)
  └─ login.py          — auto-login if the bridge reports logged out
  └─ bridge_events.py  — live mirror of the UDP stream + a game-tick clock; lets a routine
                          act ON a tick boundary instead of polling and sleeping margins
  └─ debug_log.py      — the `[category] message` log lines seen in routine output
  └─ mcp_debug_server.py — exposes bridge/controller primitives as MCP tools (see .mcp.json),
                            for live interactive debugging outside a running routine
```

The Java side's `providers/` classes are the only thing that knows about RuneLite's internal
API; everything they expose becomes a JSON field on some bridge endpoint (`GET /player`,
`/objects`, `/inventory`, `/bank`, `/npcs`, `/combat`, `/ground-items`, `/menu`, ...). The Python
side never touches RuneLite internals directly — only these HTTP endpoints.

## Running things

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
- **The VM (2026-09-07)**: USER wanted the physical mouse free for the second screen. One
  Windows desktop has one cursor and one foreground window, so the client, the bridge,
  controller.py AND the Arduino live in a VirtualBox guest instead. VirtualBox 7.2.16 +
  Extension Pack, VM `OSRS` at `C:\VMs\OSRS` (Windows 10 Pro 22H2 from an official ISO at
  `E:\VM\`, 8 vCPU, 8 GB, 50 GB dynamic disk, EFI, VBoxSVGA with 3D OFF -- the GPU plugin
  cannot run there), guest user `Kiyo` / password `osrs`. The Leonardo (USB 2341:8036) is
  captured by a USB filter, so it is the GUEST's mouse and keyboard and the host never sees
  it while the VM runs (MEASURED: host COM6 disappears; in the guest it is **COM3**, exported
  as `BRIDGE_SERIAL_PORT`). The host's `C:\Users\Kiyo\Documents` is shared as `Docs`, which
  Windows mounted at **`Z:`** (D: is the DVD drive), so `Z:\Httpbridge` and `Z:\ZombieLite`
  keep the `../ZombieLite` layout the build needs. **The guest does NOT build or run from the
  share**: host and guest Gradle would fight over one `build\`/`.gradle\` on a network drive,
  so `tools/vm-sync.ps1` (run in the guest) robocopy-mirrors the repo and ZombieLite into
  `C:\work\` (excluding .git, build, `data\` -- 5.5 GB the guest never needs; the routing map
  is inside `ControllerAddon\maps`) and copies guest `logs\`/`recordings\` back to the host.
  So the loop is: edit on the host, `vm-sync.ps1` in the guest, run there.
  `tools/vm-guest-setup.ps1` provisioned the guest (a fresh 22H2 image has NO winget, so
  per-user direct installers from `data\vm-installers\`: Python 3.13.7, and Temurin
  **11.0.30+7 exactly** -- the host's client JVM build, since `java.version` is in the login
  fingerprint; pip requirements; pointer acceleration off).
  **`VBoxManage guestcontrol` IS DEAD ON THIS SETUP and `tools/vmexec.py` replaced it**
  (commit 946e07f, 2026-09-08): both `run --wait-stdout` and the async `start` form hang or do
  nothing with the guest logged in and Guest Additions at run level 3, and a guest reboot did
  not fix it. vmexec drives the guest over the OpenSSH server instead (paramiko, because the
  password needs supplying without a TTY). Its two forms are not interchangeable: plain SSH has
  **no window station**, so anything touching the cursor or a GUI dies there with
  `WinError 1459` -- `--interactive` runs it on the logged-on desktop through a scheduled task,
  and that is what the client and controller.py need.
  **Fingerprint parity (CLAUDE.md "What the game client sends home")**: the VM's SMBIOS UUID
  is the host's (`--hardware-uuid`, what `wmic csproduct get UUID` answers; VERIFIED identical
  in the guest); CPU count and heap are matched with `-XX:ActiveProcessorCount=32
  -Xmx8541700096` (the host's measured default `MaxHeapSize`) in the GUEST's run task rather
  than by giving the VM 32 vCPUs; Windows build (19045) and Temurin 11.0.30 match the host.
  All of `controller.py`'s Win32 calls (cursor, panic key, DPI) run inside the guest, so
  nothing in the Python changes. Running a client in a VM is itself USER's accepted risk.
  **VERIFIED end to end 2026-09-07**: the guest launched to the logged-in game as "07 Kiyo",
  bridge answering, ZombieLite + Controller loaded, `/client-version` reporting the same jar
  SHA as the host, the client JVM carrying `-XX:ActiveProcessorCount=32 -Xmx8541700096`, and
  the Arduino answering `V -> HB 3` over COM3 from inside the guest. **Login has NO Jagex
  Launcher**: RuneLite reads `%USERPROFILE%\.runelite\credentials.properties` (the JX_ tokens)
  at startup and signs in automatically; that file was copied from the host once. The tokens
  self-refresh into the GUEST's `.runelite`, so keep the host's RuneLite CLOSED while the VM
  runs or the two sessions fight over one refresh token. `tools/vm-run-client.ps1` launches
  it (default = full/developer "unsafe" start with the injected client and all builtin
  plugins; `-SafeMode` is a diagnostic external-plugins-off launch). The client is started
  from the host with `VBoxManage guestcontrol OSRS start ... -- cmd.exe /c "cd /d
  C:\work\Httpbridge && gradlew.bat --no-daemon run"`; a `natpf1` rule forwards guest 8080 to
  host 127.0.0.1:18080 for read-only host-side bridge probing. Since the bridge binds loopback
  by default (2026-09-07), the guest launch sets BRIDGE_BIND_ALL=1 (vm-run-client.ps1, and
  --putenv BRIDGE_BIND_ALL=1 on any guestcontrol launch) or the forward dead-ends.
  **SSH into the guest (host VSCode Remote-SSH / a host terminal), 2026-09-07.** OpenSSH server
  installed in the guest (`tools/vm-setup-ssh.ps1`, run ELEVATED -- Kiyo is a local admin but
  UAC hands guestcontrol a FILTERED token, so the install needs a real UAC consent: the
  self-scheduled-`RunLevel Highest` bypass is denied here, so `tools/vm-ssh-elevate.ps1` raises
  a one-click UAC prompt on the VM screen). A `natpf1` rule forwards host 127.0.0.1:2222 ->
  guest 22; the host `~/.ssh/config` has an `osrs-vm` block (user kiyo, password osrs, host-key
  checking off since it is a forwarded localhost). VSCode: Remote-SSH -> Connect to Host ->
  `osrs-vm`, then open `Z:\Httpbridge` (live host share, Python edits need no sync) or
  `C:\work\Httpbridge` (the build mirror); its terminal runs IN the guest. Password auth
  (key auth would need the admin `administrators_authorized_keys` file, another elevation --
  skipped). VERIFIED to the handshake: the host client got the guest host key and negotiated
  password auth through the forward.
  **What persists across a VM power-off vs not.** Durable in the VM/guest: the Arduino USB
  filter, the Docs share (Z:), the hardware UUID, and inside the guest the RuneLite profile,
  `credentials.properties`, the OpenSSH server, and `~/.gradle/gradle.properties` (parity JVM
  flags). NOT durable: the two NAT port-forwards (added at runtime). So start the VM with
  `tools/vm-up.ps1` (host), which starts it and re-adds both forwards idempotently; a GUI
  double-click start works too but then run `vm-up.ps1` once to restore the forwards. The
  client (`vm-run-client.ps1`) and routines never auto-start -- launch them per session, OR
  drive the whole session from the host with **`run-vm.bat`** (repo root, added
  2026-09-08; `tools/vm-session.ps1` is the real script). It runs `vm-up.ps1`, launches the
  client in the guest over `VBoxManage guestcontrol`, waits for the bridge on the 18080
  forward, waits for `/login/status` to report `loggedIn` with NO `clickToPlay` (the
  `login.is_fully_logged_in()` test -- `loggedIn` alone is already true under the
  click-here-to-play screen), then starts the routine in the guest with
  `BRIDGE_SERIAL_PORT=COM3`. **Bare `run-vm.bat` is PANEL MODE**: controller.py is started with
  NO routine argument, which is what makes it re-read the plugin's Routine dropdown every loop
  (`ROUTINE_FILES`) and hot-swap routines live, gated by Controller Enabled -- so the routine is
  chosen in the GUI and can be changed without restarting anything. `run-vm.bat <routine>` pins
  one instead (the dropdown is then ignored), and `-NoRoutine` starts no controller.py at all.
  Every step is idempotent and deadlined; it REFUSES to launch
  when a RuneLite client is running on the host (two clients fight over one self-refreshing
  Jagex token) or when python.exe is already running in the guest (one process per COM port),
  `-Force` overrides both. **F1 still has to be pressed inside the VM window** -- the panic key
  is polled in the guest.

  **THERE IS NO SYNC IN THE PYTHON EDIT->RUN LOOP (2026-09-08).** The routine runs from
  `Z:\Httpbridge\ControllerAddon`, which IS this repo through the share, so a Python edit on
  the host is live in the guest with no `vm-sync.ps1` at all -- and the routine's log lands in
  the host's own `ControllerAddon\logs`, readable with no copy-back. MEASURED in the guest off
  the share: `import worldmap, controller, travel` 0.31s, `reachable_from` + an 8-step route
  0.026s (1207 tiles, so the collision map really loads), and the deob guard passes there
  (`data/` is visible through the share where the synced mirror excluded it), so the guest now
  gets the guard's real protection instead of its warn-only fallback.
  `PYTHONDONTWRITEBYTECODE=1` keeps the guest from scattering `__pycache__` into the host repo.
  **`C:\work` is only for GRADLE** (host and guest would otherwise fight over one `build\` on a
  network drive), so `vm-session.ps1` syncs it in the CLIENT-launch branch alone. The three
  guest-side halves are `.cmd` files on the share (`vm-guest-sync.cmd`,
  `vm-guest-launch-client.cmd`, `vm-guest-run-routine.cmd`) so the host passes one unquoted
  token per call -- and the routine launcher takes its log name as argument 1 because a
  scheduled task inherits no environment. **Each maps `Z:` from `\\VBOXSVR\Docs` on its own
  first LINE, which is load-bearing twice over**: a drive letter is per-LOGON-SESSION, MEASURED
  2026-09-08 the scheduled-task desktop session had none, and a one-liner
  (`net use ... & echo > Z:\...`) still failed -- the mapping has to complete on an earlier
  line than the command using it.

  **A ROUTINE'S TARGETS AND ITS WHEN-FULL POLICY ARE PANEL CONFIG, NOT PYTHON (2026-09-08).**
  `gathering.get_config` lets the bridge's answer WIN over `DEFAULT_CONFIG` deliberately (a
  panel with nothing selected must mean an idle routine, never a surprise trip to the rune
  rocks), so "run woodcutting on yews, banking" cannot be expressed from the host by editing
  Python at all. `tools/vm-set-controller-config.ps1` (run IN the guest) writes the keys into
  the guest's active RuneLite profile properties -- `controller.<keyName>=<value>`, the
  `@ConfigItem` keyName from `ControllerConfig.java` under the `controller` group:
      python tools\vmexec.py --% "powershell -NoProfile -ExecutionPolicy Bypass -File \\VBOXSVR\Docs\Httpbridge\tools\vm-set-controller-config.ps1 treeNames='Yew tree' woodcuttingFullInventory=BANK"
  **It refuses while the client is running, and that guard is load-bearing**: RuneLite holds
  the profile in memory and rewrites the file on change/exit, so an edit made underneath it is
  silently discarded. It fired on its first real use, catching a client the VM had resumed from
  SAVED STATE -- note `vm-up.ps1` reporting "guest ready after 12s" means a resume, not a cold
  boot, so a previous session's client and routine can still be alive.
  **Two vmexec quoting traps, MEASURED the same day.** The guest's default shell is PowerShell
  5.1, so `cmd`'s `&` separator is a parse error there; and the HOST PowerShell expands `$_`
  and eats quotes before the string ever reaches paramiko, which turns a `Where-Object`
  one-liner into a silent empty result rather than an error. **Put the guest command in a
  `.ps1` on the share and invoke it by its UNC path** (`\\VBOXSVR\Docs\Httpbridge\...`,
  which needs no `Z:` mapping and so sidesteps the per-logon-session drive-letter trap
  entirely); pass arguments with `--%` in front of the whole vmexec invocation.

    **Host-side bridge reads go through `BRIDGE_API_BASE`** (2026-09-08): `settings.py`'s
  `API_BASE` is now `os.environ.get("BRIDGE_API_BASE", "http://localhost:8080")` and
  `.mcp.json` sets it to `http://127.0.0.1:18080` for the `osrs-debug` MCP server, so
  `bridge_get` reads the VM's live game state from the host again. The guest's own
  controller.py keeps the default and is untouched. **`poll_events` stays guest-only**: a
  `natpf1` rule forwards host->guest, while the plugin sends its UDP to the guest's own
  loopback, so the event stream never crosses.

  **VERIFIED END TO END FROM A POWERED-OFF VM, 2026-09-08**: `run-vm.bat` -> guest ready 14s ->
  sync -> client launched on the desktop -> bridge up ~35s later -> controller.py started ->
  `loggedIn: True`, camera set (pitch 1024 -> 4160), and the panel's routine working, with its
  log read off the host the whole time. Three failures on the way, each worth keeping:
  - **A CLIENT LAUNCHED FROM A SCHEDULED TASK COMES UP UNFOCUSED, and that silently defeats
    everything.** MEASURED: `/state` `windowFocused: False`, and controller.py clicked Play Now
    with the physical Arduino once a tick FOREVER without the login screen advancing --
    `login.attempt_login` runs BEFORE the engine's own `windowFocused` gate, so the login path
    is not covered by it. Focusing the window made the very next click land and the account
    logged straight in. `vm-guest-focus-client.ps1` (SetForegroundWindow, on the desktop) is now
    a step of `vm-session.ps1` before the routine starts. **The engine gap is NOT fixed**: any
    routine whose client is unfocused at the login screen still burns clicks silently.
  - **PowerShell 5.1 turns a native command's stderr into a TERMINATING error under
    `$ErrorActionPreference = "Stop"`.** paramiko writes "Error reading SSH protocol banner"
    while the guest is still booting -- normal, transient -- and that one line killed the entire
    cold start from inside `vm-up.ps1`'s readiness loop. Every vmexec call in these scripts runs
    under `Continue` now, and each step checks its own outcome instead.
  - **RuneLite's credentials.properties auto-login did NOT take** after the client had been
    force-killed; the client sat on `loginIndex 10` (Play Now) and the login gate correctly
    refused to start a routine there. `-SkipLoginGate` is the right answer -- login.py clicks
    that screen itself.
  Also MEASURED that day: the two NAT forwards SURVIVED a power-off (this file records them as
  non-durable; `vm-up.ps1` re-adds them idempotently either way), and the guest auto-logs into
  the console session (`AutoAdminLogon = 1`), which is what makes a `--interactive` desktop task
  possible at all after a cold boot.

  **Screen geometry (settled 2026-09-08).** The VM runs WINDOWED AND MAXIMIZED on the host's
  1920x1080 second display (`GUI/LastNormalWindowPosition = 2560,23,1920,1017,max`), which is
  what makes the GUEST desktop **1920x974** -- VirtualBox sizes the guest to the VM window's
  client area, so 974 is 1080 minus the VM's own chrome. Inside that, the RuneLite window is
  MAXIMIZED, which `vm-guest-focus-client.ps1` now asserts on every run. **USER's choice is
  windowed, not fullscreen**: VirtualBox fullscreen was tried and reverted the same day (it
  gives the guest a true 1920x1080 and the canvas 1889x1017 against 1859x911, ~13% more area),
  so **do not set `GUI/Fullscreen`** -- the key was deleted. Canvas with the sidebar collapsed
  is 1859x911; an expanded side panel costs ~242px of canvas WIDTH (measured 1617x911), which
  is worth checking before concluding the client shrank.
  Two traps found getting there:
  - **An SSH session cannot see the display.** `[Screen]::AllScreens` over plain SSH reports a
    phantom `1024x768` screen named **`WinDisc`** -- not the real desktop. It made the client
    look far larger than its screen with the sidebar off the edge, which was wrong. Measure any
    guest geometry from the DESKTOP session (`vmexec.py --interactive`), never over SSH.
  - **`ShowWindow(SW_RESTORE)` UN-maximizes an already-maximized window** -- the focus helper's
    first draft would have shrunk the client on every run. It uses `SW_SHOWMAXIMIZED` (3).
  **`routines/pest_control.py` cannot work in the VM as written**: `ABS_MINIMAP_CLICK_X/Y`
  (2365, 80) is an absolute HOST screen pixel captured with ShareX, and the guest desktop is
  only 1920 wide -- x=2365 is off-screen there. It needs re-capturing in the guest before that
  routine is run in the VM.
  **The RuneLite PROFILE must be copied by hand -- it does NOT transfer.** The active profile
  (`~/.runelite/profiles2/default-1209788292728200.properties`, ~1.5 MB: every plugin
  setting, keybind, and the load-bearing Menu Entry Swapper swaps the routines depend on) has
  `sync=false`, so it lives only on the host and never reaches the guest through RuneLite's
  cloud sync. Copied once 2026-09-07 by killing the guest client and copying the host's whole
  `profiles2\` into the guest `~/.runelite\` (its `profiles.json` then makes the host's
  `default` profile active in the guest). Re-copy after changing plugin settings on the host.
  A menu-swap mismatch is not cosmetic here: karambwan's worn-teleport clicks, the ice-glove
  swap and the mahogany withdraws all assume specific left-click swaps (see those sections).
- **A routine**: `python -u controller.py routines/<name>.py` (from `ControllerAddon/`). Runs
  until the panic key (F1) is pressed, a safety stop triggers, or the process is killed.
- **MCP debug server** (`osrs-debug` in `.mcp.json`): read-only bridge queries
  (`bridge_get`/`get_cursor_position`/`poll_events`) only — no mouse/keyboard action tools, so it
  never touches the Arduino serial port and can stay connected for the whole session, including
  while `controller.py` is running. (Action tools were removed 2026-08-12; if live click/mouse
  debugging is needed again, drive it through `controller.py` itself.)
- **Tests**: `python -m pytest tests/` from `ControllerAddon/`.
- **Looking at what the client is actually drawing** (added 2026-08-21): `GET /screenshot?name=<label>`
  dumps the current rendered frame to `~/.runelite/bridge-screenshots/` and returns its absolute
  path plus every coordinate-space measurement needed to interpret it (`imageWidth/Height`,
  `realWidth/Height`, `stretchedWidth/Height`, `canvasWidth/Height`, `canvasScreenX/Y`,
  `stretchScaleX/Y`, `gameState`, `loginIndex`). Read the PNG straight off disk. `GET
  /widgets/dump` lists every visible widget with ids, text, actions, and both canvas and screen
  bounds; `?scan=true` brute-forces `getWidget(group, child)` over a bounded id range instead of
  walking `getWidgetRoots()`. **Use both before concluding a screen "isn't a widget"** — a root
  that isn't attached to the root list is invisible to the walk but still resolves by id, so the
  walk alone is not proof. Both are read-only and cost nothing when unused. NumPy and Pillow are
  already installed, so measuring a dumped frame needs no new dependency.
- **Pathfinding**: `ControllerAddon/worldmap.py` holds a world-wide per-edge collision map
  (all four planes) and answers `find_path`/`reachable_from` offline in milliseconds. It is
  the only routing engine; Shortest Path was removed 2026-08-29. Doors are baked open in that
  map by design -- `travel.open_door_toward` finds the shut one standing on the route. The map
  CANNOT tell furniture from a wall, so `travel.can_reach` still answers object-interaction
  adjacency from live collision flags, and must not be moved onto the map.

## Offline OSRS research MCP

The `osrs-wiki` MCP server in `.mcp.json` provides the local OSRS reference corpus. **Use it
before external web lookups or broad filesystem searches** whenever the question involves Wiki
gameplay information, decoded game-cache definitions, interfaces/scripts/symbols, RuneLite source,
or Plugin Hub source. It runs entirely from these already-built local databases:

- `wiki.sqlite` — revision-pinned OSRS Wiki pages, redirects, equipment requirements, and
  structured NPC/object/ground-item spawns.
- `cache.sqlite` — decoded game-cache configurations, interfaces, scripts, and symbols; it does
  not include binary assets such as models, maps, sprites, or audio.
- `runelite-code.sqlite` — revision-pinned Java from RuneLite API/client/HTTP API, Plugin Hub
  tooling, and Plugin Hub plugins.

Choose tools by source: use `search_unified` for a combined Wiki + cache query, `search_wiki`
plus `get_wiki_page`/`get_wiki_sections`/`get_wiki_section` for game mechanics and Wiki prose,
and `search_spawns` for NPC/object/ground-item coordinates. Use `search_cache` for raw client
definitions (optionally restricting `kind`, for example `config/obj`, `config/loc`,
`config/varbit`, `interface`, or `script`), then call `get_cache_entry` with the returned exact
kind and ID. Use `search_code` for RuneLite or Plugin Hub implementations (optionally restrict
to `runelite-api`, `runelite-client`, `runelite-http-api`, `pluginhub-tooling`, or
`pluginhub/<internalName>`), then use `get_code_entry` with the returned case-sensitive kind and
ID. Keep queries short and distinctive; this is lexical full-text search, not semantic retrieval.

## Reference implementations in `data/decompiled/`

**Documentation only — nothing here is on our build path.** These are decompiled or vendored
third-party projects kept for reading. Before writing a new subsystem from scratch, check
whether one of them already solves it; two of the four below were found only after the
equivalent had already been hand-built and live-debugged for several sessions.

| Directory | What it is | Reach for it when |
|---|---|---|
| `PacketUtils/` (EthanApi) | `EthanApiPlugin/PathFinding/GlobalCollisionMap` + `PathingTesting` — a world-wide per-edge collision bitmap and a BFS over it, plus an entity/widget query API | You need a walking route, especially on a plane Shortest Path cannot reach |
| `Vitalite/api/.../services/pathfinder/` | A 21k-LOC pathing system: local vs global collision, A*/BFS/JPS/flow-field, transports with requirements, teleports, sailing | You need transports, teleports, or a worked example of local-vs-global collision |
| `CacheMapDumper-main/` | Generates a collision map from an OSRS cache pulled from OpenRS2. Writes RoaringBitmap + gzip, with a Swing viewer | The static map goes stale after a game update, or you need extra per-tile data baked in |
| `rl-suite-main/` | Deobfuscates a RuneLite injected-client jar into readable source; publishes packet tables (ids, names, lengths, field layouts) for revs 235-240 as TSV in `data/` | You need to read client internals the API does not expose. **See the caveat below before touching the packet half.** |
| `mahogany-homes-plugin/`, `shortest-path-plugin/`, `sailing-plugin/`, `target-true-tile/` | Sideloaded Plugin Hub jars | Porting a fact the plugin already knows — copy its SOURCE, not your reconstruction of it (see the 231.6 dialogue-widget lesson) |
| `client-1.12.33/`, `injected-client-1.12.33/` | The RuneLite client itself | Settling an API question against the real jar rather than assuming |

**`rl-suite-main`'s packet tables are not a licence to send packets.** This project drives a
physical Arduino over serial *specifically* because the input is not software-injected (see
"What this is"). Adopting packet-based interaction would reverse that decision, and it is the
user's call to make explicitly — not something to reach for mid-task because it is convenient.
The deobfuscation half carries no such caveat.

### Collision maps: three of them, and they must never be mixed

All three are per-edge bitmaps storing only NORTH and EAST per tile (south/west are read off
the neighbour), and all three bake **doors as OPEN** — deliberately. `CacheMapDumper`'s
`Exclusion` enum is the hand-curated list that does it (guild doors, city gates, arches, prison
doors), so doors-passable is a decision, not a dump artifact. The model everywhere is: route
straight through the door, then detect and open it when you reach it.

| Map | Packing | Polarity | Readable from Python |
|---|---|---|---|
| EthanApi `PathFinding/map` | `x & 16383 \| y<<14 \| plane<<28`, bit 30 = east | **set = passable** | Yes — RoaringBitmap portable format |
| Vitalite `map.dat` | `x & 8191 \| y<<13 \| plane<<28`, bit 30 = east | **set = blocked** | No — Java-serialised `SparseBitSet` |
| CacheMapDumper output | 12-bit x (base 480), 14-bit y, 2-bit plane; data bit 0 = north, 1 = east | **set = blocked** | Yes — RoaringBitmap + gzip |

Same shape, opposite meanings. Reading one with another's indexer produces a map that is
subtly wrong rather than obviously broken.

MEASURED 2026-08-29 against EthanApi's map: 3.6 MB gzipped / 78 MB decompressed, 82.2 M set
bits, per-plane p0 10.2 M / p1 22.3 M / p2 24.3 M / p3 25.4 M — **upper planes carry real
data**, which is the thing Shortest Path structurally cannot give us. Pure-Python BFS over it
costs 0.13 s to load and 0.4-3 ms for in-house routes, 151 ms for a 314-step cross-city one.

Two properties to know before trusting a route:

- **No data defaults to PASSABLE.** Verified at Varrock square on planes 1 and 2 (open sky above
  an outdoor tile) — all four edges passable. Upstairs, a missing exterior wall lets a BFS leak
  through the void.
- **An object's own blocking is baked into the same bits as a wall's**, so the static map cannot
  tell furniture from a wall. VERIFIED at JEFF's upper floor: the player stood diagonally beside
  a hotspot and interacted fine, while the map called every route to it blocked. **This is why
  `travel.can_reach` stays on the LIVE `collisionFlags` from `ObjectProvider`** — only there do
  `BLOCK_MOVEMENT_OBJECT` (256) and the directional `MOVEMENT_*` bits stay distinguishable.
  Vitalite makes the same split (`LocalCollisionMap.all()` uses the global static map for
  ordinary coordinates and live flags only for instanced ones).

`CacheMapDumper` packs **up to 5 data bits per tile and uses only two**, and
`Dumper.processRegion` already reads `getWallOrDoor()` and `getInteractType()`. So a "door on
this tile" bit and an "object-blocked, not wall-blocked" bit are available if the live check
ever proves insufficient.

### Doors: the model that replaced ours

Vitalite's `WalkerPath.handlePassThroughObjects` is the whole door algorithm, and it is smaller
than any of the three tiers we built: **when the next step is not reachable, find an object
named `door`/`gate`/`curtain` (excluding `trapdoor`) standing on the player's tile or on the
next step's tile, and interact with it.** No orientation, no edge-bit matching, no span tests —
having a real route is what makes the question trivial. It covers both shapes we measured
(Barbara's edge doors and Falador's `GameObject` 24050) because both sit *on* a route tile.

**One correction to make when copying it:** Vitalite matches on name only and interacts with
action index 0. Left-clicking an *open* door **CLOSES** it, turning a working walk into a wedged
one — a shut door and an open one are both `WallObject`s named `Door` with per-door ids
(Falador 24050 shut / 24051 open), and only the `Open` action separates them. Keep the action
check. Do take `curtain`, which our name list was missing.

See `docs/superpowers/specs/2026-08-29-global-collision-pathfinding-design.md` for the design
this feeds.

## What the game client sends home (read from the rl-suite deobfuscation, 2026-09-07)

Everything below was READ from `data/decompiled/rl-suite-main/semantics/injected-client-1.12.37-sources/`
(`PlatformInfo.java`, `classVS.java`, `client.java`, `classDK.java`, `classUO.java`,
`GameEngine.java`) and the rev-240 packet table
`data/decompiled/rl-suite-main/data/rev-240/mappings/client-packets.tsv` (117 opcodes, 101 named),
after USER asked how a VM could be hidden from the client. It is the list of what the client can
SEE, so future work knows which side of the wire a behaviour lands on. Two caveats: rl-suite's
semantic names are heuristic anchors (a couple of the server-packet names around the recorder
toggles look mis-anchored), so trust the described BEHAVIOUR over any name here; and this is the
Java client launched by `./gradlew.bat run`, not the Jagex Launcher, which this setup never runs.
Reading packet tables is not a licence to send packets -- see the rl-suite caveat above.

**Login fingerprint (`PlatformInfo`, serialised once at login).** On Windows the Java client
reads exactly: `os.name`, `os.version`, `os.arch`, `java.vendor`, `java.version`, max heap in MB
(`Runtime.maxMemory`, the JVM default of a quarter of RAM unless `-Xmx` is set), the CPU count,
and the SMBIOS system UUID by running **`wmic csproduct get UUID`** and taking the second line
(macOS `system_profiler`, Linux `/etc/machine-id`; on failure the placeholder
`12345678-0000-0000-0000-123456789012`, which is what a Windows 11 24H2+ guest sends since `wmic`
is gone there). The GPU / CPU-name / driver / display string fields exist in the class and the
Java client's constructor call passes them EMPTY -- they belong to the C++ client -- **but a
separate method fills THREE of the string slots after construction (found 2026-09-07 while
diffing our own 1.12.38 deobfuscation, and missed by the first read): the process's own
executable name (`ProcessHandle.current().info().command()`, first 10 chars), the PARENT
process's executable name (`.parent()`, first 10 chars), and the file name of the first JVM
argument whose 10-char prefix hashes to `-1094877034`, which is `-javaagent` (up to 22 chars).**
So the client can tell `java.exe` launched by a Gradle worker (`./gradlew.bat run`) from
`RuneLite.exe` launched by the launcher, and reports any Java agent by name. Keep the launch
shape constant per account; do not add a `-javaagent` to the run task. There is NO MAC-address
read, NO screen-size read and NO hypervisor check anywhere in the sources. So nothing says
"VMware" by name; a VM leaks only as a DIFFERENT identity from the one the account has always
logged in with. Three hypervisor-side knobs make the guest byte-identical: the same UUID
(VMware `uuid.bios` in the .vmx, VirtualBox `--hardware-uuid`), the same core count, and
`-Xmx` pinned so `maxMemory` matches -- plus the same launch shape, per the paragraph above.
Not traced through the obfuscation: which static wrapper calls the UUID getter on the Java
path; the method sits in the login class in the 1.12.14, 1.12.37 and 1.12.38 sources, so treat
it as sent.

**MEASURED on this host 2026-09-07** via `GET /client-version`'s `fingerprint` block
(`LoginFingerprint.java` computes it exactly as the client does; `python tools/deobfuscate.py
--status` prints it). These are the parity targets a different launch or a VM guest must match:

| field | host value under `./gradlew.bat run` |
|---|---|
| `processCommand` / `parentProcessCommand` | `java.exe` / `java.exe` (the Gradle worker -- a launcher-run client would report `RuneLite.e`) |
| `javaAgent` | none |
| `osName` / `osVersion` / `osArch` | `windows 10` / `10.0` / `amd64` |
| `javaVendor` / `javaVersion` | `Eclipse Adoptium` / `11.0.30` |
| `maxMemoryMb` / `availableProcessors` | 8147 / 32 |
| `smbiosUuid` | matches the host's `wmic csproduct get UUID` (printed by `--status`; not committed) |

The endpoint reads these; it never sends anything. It is served on **loopback only** since
2026-09-07 -- VERIFIED the same day: `Bridge running on http://127.0.0.1:8080` in the log, a
LAN request to `192.168.50.240:8080` and `192.168.56.1:8080` refused, `127.0.0.1:8080` 200.
The bridge has no auth and serves game state, screenshots, recorder keystrokes and the bank
memory, which is why the default moved; `bridgeBindAll` (Controller Settings) or
`BRIDGE_BIND_ALL=1` restores all-interfaces, and the VM guest NEEDS the env one because a
VirtualBox NAT forward lands on the guest's NAT adapter, never on its loopback.

**Continuous, during play -- this is the half that matters to us:**
- **`EVENT_MOUSE_MOVE`: the whole cursor trajectory, at 20 Hz.** A dedicated thread samples
  `(x, y, lastPressedTime)` every **50 ms** into a 500-slot buffer; the game loop flushes it
  when **40 samples** are pending (about every 2 s) or IMMEDIATELY when a button is down.
  Encoding is delta-from-previous with the time delta in 20 ms units (2 bytes for a step within
  +-32 px and under 160 ms, 3 bytes within +-128 px and under 640 ms, else absolute), a sample
  identical to the previous position is SKIPPED, positions are clamped to -1..65534 where
  **-1 means the cursor is off the canvas**, and a packet is capped at 246 bytes. Server packets
  switch the recorder on and off (on by default at startup). Consequences: stillness, snap
  moves, straight lines, an exact-pixel return, a cursor parked for minutes and the cursor
  LEAVING the window are all server-visible even with no click anywhere near them. This packet
  is what every measurement in "Movement realism batch" and the fidget anchor fix is aimed at.
- **`EVENT_MOUSE_CLICK_V2` (7 bytes), every press:** a flag byte, x clamped to the canvas
  width, `(ms since the previous press, capped 32767) << 1 | right-button`, y clamped to the
  canvas height. Inter-press timing therefore reaches the server with 1 ms resolution
  independently of the OP packets. The RELEASE is not sent, so `CLICK_HOLD_RANGE_S` is invisible
  to this packet (a hold still matters for drag-vs-click on the client).
- **`EVENT_KEYBOARD`: every key press with ms since the previous key** (24-bit cap). The
  password typed at login, the SHIFT before every walk click, F-keys, the held UP arrow of
  `login.setup_camera` -- their rhythm is on the wire. A fixed SHIFT-down-to-click gap is a
  signature; draw it.
- **`EVENT_MOUSE_SCROLL`**: the wheel delta per cycle. **`EVENT_CAMERA_POSITION`**: yaw and pitch,
  sent ONLY after an arrow-key camera move (key codes 96-99), rate-limited; a middle-mouse drag
  sends nothing.
- **`EVENT_APPLET_FOCUS`**: 1 / 0 on every window focus gain / loss, so karambwan's Alt+Tab AFK
  is seen exactly as a human's would be. Focus flapping at machine cadence would not be.
- **`WINDOW_STATUS`**: window mode plus canvas width and height, on change.
- **`IDLE`**: sent after 15 000 client cycles (20 ms each, 5 min) with NO mouse AND NO keyboard
  input -- the AFK-logout signal. **`NO_TIMEOUT`** is a keepalive every ~50 cycles that needs no
  input, so nothing has to nudge the mouse to hold the connection.
- **`REFLECTION_CHECK_REPLY`**: the server names classes, fields and methods; the client resolves
  them by reflection and replies with field values, modifiers and invoke results. RuneLite
  answers these against the vanilla jar named by `runelite.reflectcheck.jar` (the property is
  read in `client.java`), which is why an injected client reads as vanilla. We never modify
  injected classes, so nothing here changes it. **`DETECT_MODIFIED_CLIENT`** is one constant int
  sent on scene load whenever the client runs inside a top-level `Frame` (every RuneLite
  instance does), so it is not a differentiator. **`CONNECTION_TELEMETRY`** is login-connection
  timing counters (three longs, four ints). `SEND_SNAPSHOT` is the abuse report, `BUG_REPORT` and
  `CLIENT_CHEAT` (`::` commands) are user-initiated.

**What is NOT in any packet:** the plugin list, the sidebar, overlays, ZombieLite, the HTTP
bridge, the UDP stream, the serial port, other processes, the second monitor. The only channel
that carries our behaviour is the input stream itself -- which is exactly the channel a physical
Arduino feeds. Separately, RuneLite's own `TelemetryClient` (`client-1.12.33/.../TelemetryClient.java`)
sends the OS / Java / launcher-version strings and a "is a Jagex account" boolean to
**runelite.net**, not Jagex, and the GE plugin submits offers there too.

**Rules that fall out of this for future development:**
1. Treat every cursor sample as transmitted. "No click happened" never means "nothing was
   sent"; between-click motion, idle stillness and off-canvas excursions are the signal.
   Measure it with tools/humanity_report.py <human.jsonl> <bot.jsonl> -- the same metrics over
   both, which is the only way this class of signature is visible (the routine log cannot see it).
   **The recorder captures the two things that were previously invisible** (added 2026-09-07):
   `canvasexit`/`canvasenter` (the cursor leaving the window, which the client encodes as the
   -1 in `EVENT_MOUSE_MOVE`) and `focus` (its `EVENT_APPLET_FOCUS`), plus a `recorder` stamp
   record naming the schema. The stamp is load-bearing: a recording made BEFORE it exists
   reports those metrics as `-` (unmeasurable), never as 0, so an old capture can never be
   mistaken for "this bot never left the canvas". **Reading a recording out of the ring means
   reading it BEFORE disarming** -- `handleArm` calls `ring.reset()` unconditionally, so
   `arm?on=false` wipes the buffer (cost me one live run). VERIFIED live 2026-09-07: a scripted
   Arduino excursion above the canvas top plus one Alt+Tab produced `recorder` 1, `canvasexit`
   4, `canvasenter` 5, `focus` 2, and the report tool turned them into real numbers
   (`off_canvas_fraction` 0.266, `unfocused_fraction` 0.130). Note a maximized window has no
   left/right edge to leave by -- the cursor clamps inside the canvas; go UP into the title bar.
   **COMPARE THE SAME ACTIVITY, or the numbers mean nothing.** MEASURED 2026-09-07 over four
   real recordings, `still_p90_ms`: human karambwan 2038, human GOTR 608, cg-bot 612,
   foundry-bot 7493. The metric tracks the ACTIVITY's cadence, not human-vs-bot -- a human
   waiting on a fishing spot and a bot waiting on a foundry machine both look still, and a
   human at GOTR and a bot in the Gauntlet both click constantly. So a baseline human
   recording is only useful against a bot recording of THE SAME activity (the same rule the
   fidget-anchor fix already states). **The tool's first draft got this wrong in a way worth
   remembering**: `still_segments` held its anchor across sub-threshold gaps, which turned
   continuous motion into a fake ~150 ms pause every 150 ms and pinned `still_p50_ms` at
   152-160 ms on EVERY recording, human and bot alike. Two unit tests and two code reviews
   passed it; running it over one real recording is what caught it. Run any new metric over a
   real capture before pinning its expected value.
2. Timing that is drawn from a fitted human distribution (`settings.py`) is on the wire; timing
   that is a constant is a fingerprint. That applies to key presses as much as clicks.
3. Keep the login fingerprint constant per machine (same UUID, cores, heap, Windows build, JDK).
   A VM is fine on this channel once those match; server-side correlation is unknowable from
   the decompile, so "identical fingerprint" is the ceiling, not "undetectable".
4. Re-derive this list after a client build bump -- and the tooling below makes that one
   command, then a diff to read.

**Deobfuscating a new build ourselves, and the guard that forces it (added 2026-09-07).**
`python tools/deobfuscate.py` (repo root) does the whole thing: finds a JDK 17+ (rl-suite pins
a 17 toolchain with no foojay resolver, and this machine had only 8 and 11 -- Temurin 17 now
lives at `~/.jdks/jdk-17.0.20.1+1`, and the script downloads one there from the Adoptium API
when none exists), takes the jar the running client reports on `GET /client-version` (else the
newest `injected-client-*.jar` in the Gradle cache, else `--jar`), builds rl-suite's shaded jar
once, runs the transform, decompiles into
`data/decompiled/rl-suite-main/semantics/injected-client-<ver>-sources/`, and writes
`data/decompiled/deob-state.json` keyed on the jar's SHA-256. It answers rl-suite's two
documented refusals itself: "Expected N packet-handler shapes, found M" is retried with M
(**1.12.38 has SIX packet readers, MEASURED, where the tool expects five**), and a packet
fingerprint that matches no bundled profile -- a NEW wire protocol -- is retried with
`--packet-profile none` and recorded as `protocolKnown: false` (still readable, generic
`fieldN` packet names). **1.12.38 is still protocol 240**: the osrs-240 profile matched all 252
names. Our own runs carry no semantic map, so class names are generic (`PlatformInfo` is
`classVT` in 1.12.38); search by BEHAVIOUR (`wmic csproduct`, `new long[500]`, the packet
constants, which ARE named) rather than by the class names quoted above.
- **The guard**: `ControllerAddon/deob_guard.py`, called in `controller.main()` BEFORE the serial
  port opens and once per engine loop until it settles. `ClientVersionProvider.java`
  (`GET /client-version`: RuneLite version, `client.getRevision()`, the jar path from the
  `client` class's code source, its SHA-256 hashed once and cached) is compared with the
  record: match -> run; mismatch or no record while rl-suite is present -> print the command
  and exit 2 before any input; endpoint missing (un-rebuilt client), bridge down at startup
  (re-checked from the loop), or rl-suite absent (`data/` is gitignored, so a fresh clone has no
  tool) -> warn and drive. `--skip-deob-check` / `SKIP_DEOB_CHECK=1` overrides.
  `python tools/deobfuscate.py --status` shows record vs client without doing anything.
- **Every run diffs the telemetry surface against the previous build** (USER: "when we make a
  new deobfuscate we compare the diff from last rev"). A source diff is noise -- every name
  changes per build -- so `tools/telemetry_fingerprint.py` extracts the facts this section is
  built from by their name-independent tells (system properties and env vars read, commands
  passed to `exec`, the client packet table with ids and lengths, how many places reference
  each packet constant, the recorder's 500 / 50 ms / 40 constants, the ProcessHandle and
  `-javaagent` introspection) into `data/decompiled/deob/telemetry-<ver>.json`, and writes
  `telemetry-diff-<old>-to-<new>.md` beside it. The first run diffs against the author's
  checked-in 1.12.37 snapshot. **MEASURED 1.12.37 -> 1.12.38: nothing collected changed** --
  same properties, same three exec commands, same 117-packet table, same recorder constants;
  only reference counts moved (`CONNECTION_TELEMETRY` 2 -> 3, `EVENT_MOUSE_SCROLL` 3 -> 2, six
  gameplay packets +-1), which is the obfuscator re-inlining call sites, not new behaviour. A
  `+ exec`, `+ property`, a packet length change or a recorder constant change in that file is
  the signal to re-read the sources and amend this section.

## Vendored m8aq readers (`GET /api/...`)

Added 2026-08-29. `src/main/java/net/runelite/client/plugins/m8aq/api/` is a **byte-identical
copy of RLitePlus/m8aq-plugins' reader tree**, kept in its ORIGINAL package on purpose, and
`com.example.providers.M8aqProvider` is the only adapter between it and our bridge. This is the
one place in the repo where third-party source sits on the build path -- everything else under
`data/decompiled/` is still documentation only.

66 endpoints, `GET /api/<group>/<reader>` (kebab-cased), plus `GET /api/all` (every reader in one
snapshot, which is `export/GameOutput`) and `GET /api/index` (the live path list). Read
`/api/index` rather than hardcoding a path list from memory. Python: `controller.get_api(group,
reader)`, `get_api_all()`, `get_api_index()`.

What it adds that we never had: every storage container (rune pouch, herb sack, log basket,
gem/coal bag, seed box, fish barrel, looting bag, essence pouches, potion storage, seed vault,
group/house storage, death recovery), run energy/weight/destination (`player/movement`),
poison/venom/antifire/stamina timers (`player/status-effects`), `player/magic`,
`player/transportation`, all of `runtime/`, `social/`, `trade/` (GE, shops, player trade),
`progression/` (quests, diaries, collection log, combat achievements), `ui/dialogue`,
`ui/interfaces`, and nine minigames including Wintertodt, Blast Furnace, Giants' Foundry, GOTR
and Soul Wars.

Five things to know before touching any of it:

- **Never edit a file under `api/`.** The no-edits rule is what makes `tools/sync-m8aq.sh` a
  clean overwrite instead of a conflict-ridden rewrite of 71 files, and it is the whole reason
  the package was not renamed to `com.example`. Wrap behaviour in `M8aqProvider` instead. See
  `src/main/java/net/runelite/client/plugins/m8aq/VENDORED.md` for the pinned upstream commit.
- **Discovery is reflection, deliberately.** `ReaderCatalog` finds every public class with a
  `public static State getState(Client)` via a Guava classpath scan, so a reader added upstream
  becomes an endpoint on the next client rebuild with **no code change**. That is the payoff for
  vendoring verbatim. The cost: a scan that walks nothing yields zero endpoints and no error --
  a bridge that is up and serves none of this. Hence the startup count log, the `stderr` warning
  below `MINIMUM_EXPECTED_READERS`, and `M8aqProviderTest`'s floor assertion. MEASURED under the
  Gradle test classloader: 67 discovered, 66 endpoints.
- **Namespaced under `/api/` because eight names collide.** `Bank`, `Inventory`, `Equipment`,
  `Prayer`, `Npcs`, `Skills`, `GroundItems` and `TileObjects` are all readers here AND existing
  endpoints of ours with a different JSON shape. The two coexist and mean different things --
  `/bank` is not `/api/storage/bank`.
- **No `TickCache`, on purpose.** These are varbit and item-container reads, not the bulk scene
  scans that cache exists to make cheap; each request reads fresh on the client thread like
  `/player` and `/object/at`. A reader that throws returns an `{"reader", "error"}` dict rather
  than a 500, because several are interface-dependent and legitimately unhappy with their
  interface closed -- so on the Python side `None` means "bridge/endpoint unavailable" and an
  `error` key means "this reader failed", and neither means "empty container".
- **Serialization needed no Gson adapters, and that was checked, not assumed.** Every field is a
  primitive, `String`, enum, `WorldPoint`, `List<ItemSlot>` or `LocalPoint` (which stores
  `worldView` as an `int`, not a reference); the heavy `Widget` fields are already `transient`,
  which Gson honours; the one lambda field (`EssencePouches.Pouch.degradationReader`) lives on an
  enum, which Gson emits by name. Re-check this after a sync that adds a field type.

The real standing risk is **not** the sync mechanism, it is that reader field names become a
Python-facing contract we do not control: an upstream rename silently changes JSON keys.
`/api/index` makes a moved *endpoint* visible and `M8aqProviderTest.knownPathsAreStable` fails
the build on one, but nothing catches a renamed field. Diff `/api/all` after every sync.

Upstream has **no LICENSE file** (confirmed via the GitHub API, 2026-08-29).

## The routine contract

Every file in `routines/` is a plain Python module `controller.py` loads dynamically. It may
define (see `controller.py`'s own header comment for the authoritative version):

- `DEFAULT_CONFIG = {...}` — optional fallback config if the routine doesn't read from the
  bridge's `GET /config`.
- `get_config(bridge_config: dict | None) -> dict` — optional; resolves the config to use.
- `setup(ctx) -> None` — optional; runs once after the serial connection opens.
- `tick(ctx) -> None` — **required**; runs once per loop iteration, only while logged in,
  focused, no safety stop active, and not on a break.
- `on_event(ctx, event_type: str, payload: dict | None) -> None` — optional; runs for every UDP
  event regardless of whether `tick` is currently running.
- `before_break(ctx) -> None` — optional; runs once on the tick a break first comes due, **before
  the break starts**, under the same gates `tick` runs under. Added 2026-08-20 (user requirement
  for `routines/chinchompa_hunting.py`: "make it so it picks up the trap before the break
  function") because breaks are toggled *before* `tick` and `tick` is skipped for the whole
  break, so a routine otherwise physically cannot clean up after one begins — four laid box traps
  would sit out a 3-12 minute break unattended. The break starts once this returns, so a slow
  teardown just delays it. Backed by the side-effect-free predicate `ControllerState.break_due()`;
  routines without the hook are unaffected. There is **no** equivalent hook for a safety stop or
  for process exit (the panic key is a hard `os._exit` by design) — only breaks are covered.
- `ctx.events` — a `bridge_events.EventStream`, or **`None`**. Added 2026-08-21. A live mirror of
  the UDP stream plus a game-tick clock, so a routine can wait for a pushed event and act ON a
  tick boundary instead of polling HTTP and sleeping a conservative margin. Every consumer MUST
  tolerate `None` and keep a working fallback — it is absent whenever the engine did not start a
  listener. Two hard rules:
  - **It is an accelerator, never a source of truth.** It may answer "has X happened yet" for
    timing. Anything acted on — which entity to click, whether an action succeeded — is still
    confirmed against HTTP. UDP is lossy and these routines run unattended for hours on a real
    account, so a dropped packet must cost latency, never a wrong click.
  - **`wait_for_event` does not consume what it matches**, and the buffer holds ~10 ticks. Call
    `ctx.events.clear()` immediately before the action whose result you are about to wait for, or
    an identical event from a previous action can satisfy the wait. See `retrieve_trap`.
  Useful methods: `wait_for_event(types, match=, timeout_s=)` → payload or `None` on timeout
  (a normal outcome, not an error); `wait_ticks(n)`; `deadline_after_ticks(n)` → a wall-clock
  deadline to record and overlap your own work against, rather than sleeping inline;
  `clear()`; and `ticks` (`phase_ms`, `ms_until_next`, `is_stale`).
  **Added 2026-09-04 (event-driven outcomes,
  `docs/superpowers/specs/2026-09-04-event-driven-outcomes-design.md`):** `mark()` + `since=`
  replace the clear-before-the-action rule; `await_outcome(signals, budget_s, confirm=,
  since=, shorten=)` is THE way to wait for a click's outcome -- a listed UDP signal, confirmed
  by one HTTP read, with the HTTP read deciding alone at the budget, padded by a human
  reaction draw (`settings.REACTION_DELAY_S`, fitted from the GOTR benchmark). The
  module-level `bridge_events.await_outcome(ctx.events, ...)` handles `None` by polling.
  `controller.py --no-events` is the A/B control. Routines may declare `VARBIT_SUBSCRIPTIONS`/
  `VARP_SUBSCRIPTIONS`; the engine posts them on load and after every login. The engine loop
  wakes on any non-TICK event (`EventStream.wait_any`) and logs its own iteration time
  (`LoopStats`, `[engine] loop: ...` every 60s) -- read that line before assuming the loop's
  cost. `log_gaps.py <log>` shows where a run's time went.
- `SKIP_STRAY_DIALOG_CHECK = True` — optional module-level flag; opts out of the engine-level
  `check_stray_dialog` (dismisses a "click here to continue" dialogue via Space before every
  tick). Added 2026-08-14 for `routines/pest_control.py`: that check only presses Space once per
  tick, so a multi-stage dialogue only advances one stage per call instead of clearing in one
  shot — fine for a routine whose own state detection tolerates a lingering dialogue, not useful
  otherwise. Off (check runs) by default for every other routine.

`ctx.state.data` is a plain dict for a routine's own bookkeeping (current target, phase, etc.) —
**it resets to `{}`** whenever the routine file changes or the process restarts. It is NOT
persisted across restarts; a routine's own in-game progress (mid-craft state, partially-filled
inventory, etc.) very much is, so the two can desync after a restart — see the golem_crafting.py
notes below for a real example of this causing a bug.

**Wiring a new routine into the GUI is a required step, not optional polish.** A routine file
alone is only launchable via `python controller.py routines/<name>.py --routine <NAME>` from the
command line — CONFIRMED live 2026-08-14 (`pest_control.py` shipped without this and could only
be started that way). To make it selectable from the RuneLite plugin's own Controller Settings
panel (so `controllerEnabled` + picking it from the Routine dropdown is enough, no command line
needed), both of these are required, and need a client restart (Java-side change) to take effect:
1. Add an entry to the `RoutineChoice` enum
   (`src/main/java/com/example/controller/RoutineChoice.java`).
2. Add a matching key to `ROUTINE_FILES` in `controller.py` (the enum name, e.g. `PEST_CONTROL`,
   mapping to `"routines/pest_control.py"`).

Only add a dedicated `ControllerConfig.java` toggle/section on top of this (see the "Secondary
switch on top of launching controller.py with routines/X.py" entries for `golem_crafting.py`,
`gemstone_crab.py`, etc.) if the routine actually reads its config from the bridge's `GET
/config` — a routine using a plain `DEFAULT_CONFIG`/`get_config` that ignores `bridge_config`
(same pattern as `gemstone_crab.py`, `mining.py`, `pest_control.py`) needs nothing beyond the two
steps above.

## Known environment gotchas

- **A bridge query value is only safe if it has no characters `requests` will percent-encode.**
  `BridgeHttpServer.parseQuery` read `getRawQuery()` and split on `&`/`=` without ever
  URL-decoding, so a `%2C` (what `requests` turns a comma into when you pass `params=`) arrived
  literally. CONFIRMED live 2026-09-13: `subscribe_varbits` sent `varbits=5558%2C5556%2C2086`,
  `EventSubscriptions.parseIds` found no separator and returned an **empty set** — so *every*
  routine with two or more `VARBIT_SUBSCRIPTIONS` had been running with varbit events entirely
  off, and one with a single id worked (no comma), which is what hid it for so long. It failed
  **silently**: HTTP 200, a well-formed `{"varbits":[],"varps":[]}` reply, so the "bridge has no
  /events/subscribe" warning never fired either. The cost is pure latency, never a wrong click —
  which is exactly why nothing caught it: `await_pre` just falls through to `confirm()`-at-the-
  deadline and spends its **full budget**. MEASURED: motherlode ladder climbs at 11–12s against a
  6s budget, 5.7–6.0s once subscribed. `parseQuery` now decodes (fixes every `registerQuery`
  endpoint — a space, `&`, `=` or `+` in any value was equally broken) but **that half needs a
  client rebuild**; `subscribe_varbits` also puts its CSV in the path where `requests` cannot
  re-encode it, which is what makes it work on an un-rebuilt client. Check
  `GET /events/subscriptions` (plural — the read-only one) to see what the bridge actually holds.
  **`GET /events/subscribe` with no params CLEARS the set**, so it is not a read.
- **`get_dpi_scale()` caches for the process lifetime, and Windows lies about DPI until
  `set_dpi_aware()` runs.** `LOGPIXELSX` reports a virtualized 96 to a DPI-unaware process, so any
  caller that measures first locks the scale to 1.0 and leaves every bridge `screenX`/`screenY`
  unscaled. CONFIRMED live 2026-09-13: `main()` ran `deob_guard.ensure()` — which makes its own
  `api_get()`, and `api_get` ends in `_scale_screen_coords(..., get_dpi_scale())` — two lines
  before `set_dpi_aware()`. On a 125% display every click then landed at 1/1.25 of the correct
  offset (short, up-left) and routines read that as "the interaction did not register", retrying
  forever on an object they were never touching. **Invisible at 100% scaling**, where 1.0 is right
  anyway — so it only ever showed up on the laptop. `get_dpi_scale()` now calls `set_dpi_aware()`
  itself before measuring; don't reintroduce a measurement that trusts statement order.
- **Instanced areas break forward world-coordinate conversion — check for this first in any new
  provider that reads or writes a `WorldPoint`/`LocalPoint`.** Hit four separate times so far
  (`PlayerProvider`, `NPCProvider`, `ObjectProvider`, `GroundItemProvider`, all fixed 2026-08-14):
  reading a position *out* of the client (`LocalPoint` → real-world `WorldPoint`) needs
  `WorldPoint.fromLocalInstance()`, not `RuneLiteUtil.toTopLevelWorldPoint`/
  `getTopLevelWorldLocation`/a bare `tile.getWorldLocation()` alone (none of those handle classic
  instance-template chunk remapping, only (at best) a nested-WorldView case like a Sailing boat)
  — CONFIRMED live via Pest Control (an instanced arena) reporting every x/y in the ~13000s
  instead of the real ~2600s map coordinates. Going the *other* direction (a known real-world
  tile → a clickable screen position, e.g. `CombatProvider`'s `/minimap/walk`/`/ground/walk`,
  both built on `LocalPoint.fromWorld()`) is **not fixed and not easily fixable the same way** —
  CONFIRMED live these fail to resolve even the player's own current tile while inside an
  instance. `pest_control.py` works around this entirely by never doing forward world-coordinate
  resolution inside an instanced arena: it clicks a hardcoded absolute screen pixel on the
  minimap instead (see that routine's own header comment), and `/object/at`'s and
  `/ground-item/at`'s designs both deliberately filter the already-corrected bulk `/objects`/
  `/ground-items` lists rather than attempting a fresh forward lookup (see their own doc
  comments). If a future routine needs to click a known world tile while inside an instance and
  neither of those patterns fits, that forward-direction fix is still an open problem, not a
  solved one. `/players` (`PvpProvider`) hasn't been checked for this bug yet — assume it has the
  same gap until verified, same as every other provider not in the four already fixed.
  **RESOLVED for the Gauntlet 2026-09-05 (sub-project 1): the bridge now carries a second,
  INSTANCE-SPACE coordinate pair.** `instX`/`instY` (base + scene, `InstanceCoords.java`) on
  `/player` (+ `inInstance`, `baseX`, `baseY`), `/npcs`, `/npc/*`, `/objects`, `/object/at`,
  `/ground-items`, `/ground-item/at` and `/collision` tiles; `inst=true` on `/ground/walk`,
  `/minimap/walk`, `/object/at`, `/ground-item/at`, `/collision` resolves x/y by ARITHMETIC
  against the world view's base (never `LocalPoint.fromWorld`); `/collision?all=true` is the
  whole plane as one grid. `ControllerAddon/instance_nav.py` (`InstanceMap`, `find_path`,
  `walk_to`) is the instance routing engine; `worldmap.py` stays the overworld one. Template
  `x`/`y` keep their meaning and are still what a hardcoded tile in a routine matches. Live
  status: see the spec's checklist -- fill in what was verified here.
  **Fixed 2026-09-05 (final-review C1): `/player`'s `plane` is the TEMPLATE plane, not
  necessarily the plane the player stands on** -- `instPlane` (`topLevel.getPlane()`, 0 when the
  view is null) is the LIVE one, and `instance_nav.walk_to` loads `InstanceMap` with no plane
  argument at all so `CollisionProvider` defaults to the LIVE plane and hops on the grid's own
  `plane`, never `/player`'s.
  `/npcs`/`/npc/*` also carry `overheadPrayer`/`overheadPrayerId`/`overheadArchiveId` (the
  Hunllef's protection-prayer overhead, decoded only once a live check has paired an archive id
  with each of its states -- see NPCProvider's own comment); `gauntlet_ids.py`'s module docstring
  documents the `NPC_SPAWNED`/`NPC_DESPAWNED`/`PROJECTILE_SPAWNED` UDP event payload shapes.
- **Windows HiDPI scaling**: RuneLite's own AWT windows report `screenX`/`screenY` at 96 DPI
  under a bare `java.exe` launch (no DPI-aware manifest), regardless of the display's actual
  scaling. `controller.py` compensates by scaling every bridge response through
  `get_dpi_scale()`/`_scale_screen_coords()`. If clicks are consistently off by a fixed ratio,
  suspect this before anything else.
- **Two different pixel spaces, and mixing them costs you the stretch factor.** Widget bounds
  (`Widget.getBounds()`) and everything `Perspective` projects are in the game's **real**
  (pre-stretch) render resolution — `client.getRealDimensions()` — and must go through
  `RuneLiteUtil.canvasToScreen`, which applies `getStretchScale()` before adding the canvas's
  screen origin. Frames captured via `DrawManager` (`GET /screenshot`) are the opposite: they
  come back in **post-stretch canvas** space, the same size as `canvas.getWidth()/getHeight()`,
  so a pixel measured off a dumped frame maps to screen by adding `canvasScreenX/Y` and nothing
  else. Passing a frame-measured pixel to `canvasToScreen` scales it a second time — a live 2.06x
  error in the geometry this was confirmed in. `GET /screenshot` reports `imageWidth/Height`
  alongside both `realWidth/Height` and `stretchedWidth/Height` specifically so this can be
  settled by comparison instead of assumption.
- **The OSRS login screen has a layout rule; it is not a fraction of the canvas.** The login UI
  is drawn at a **fixed pixel size in real (pre-stretch) render space, horizontally centred and
  top-anchored** — so a button is at `(realWidth / 2, <fixed y>)`, fed to `canvasToScreen`. This
  was measured 2026-08-21 across four geometries (aspect 1.76 → 4.91, stretch 2.06x → none): the
  "Play Now" text top stayed at real y 241.1 ± 0.1 throughout, and a vertically-centred layout
  would have shifted it by 82px. It replaced a fraction of the canvas's *stretched* size, which
  was confirmed broken live — a window resize moved the reported point ~80px off target. Two
  things defeat a canvas fraction: the login art is letterboxed at some window sizes (142px bars
  each side at 2529x1377, none at 1202x682), and the UI doesn't scale with the canvas at all.
  See `LoginProvider.java` for the derivation table and the known `loginIndex` values (10 = Play
  Now, 24 = message dialog with an "Ok" button, e.g. after a disconnect). **These screens are
  genuinely not widgets** — re-confirmed against the current client by both dump modes — but the
  screens immediately after them are (CLICK HERE TO PLAY is widget 378.77), so check before
  assuming either way.
- **A click's DEPARTURE is aimed too, not just its landing -- and `refresh_target` now governs
  both.** `click_entity` used to begin every flight at the caller's `entity["screenX"/"screenY"]`
  and only consult `refresh_target` mid-flight. For a caller holding a snapshot -- anything read
  from the bulk `/objects` or `/npcs` scan at the top of a tick -- that means setting off toward
  where the target WAS and being hauled forward partway there. MEASURED live 2026-08-30 in
  `mahogany_homes`'s hotspot burst, which re-clicks the same object while the player runs: every
  re-aim departed toward the tick-old projection. USER, watching it on screen: *"as we move the
  mouse have to flick more but we flick back for some reason after we click"* -- the flick BACK is
  the departure toward the stale pixel, and the correction forward is the mid-flight refresh
  doing its job. `click_entity` now resolves the initial aim through `refresh_target` when one is
  given, falling back to the caller's coordinates when it cannot answer. **Passing a
  `refresh_target` is therefore the whole fix at a call site** -- do not re-resolve the position
  yourself first. The corollary for anything that does NOT pass one: it is aiming at whatever
  snapshot it was handed, so re-resolve through `/object/at` / `/npc/by-index` immediately before
  clicking (this is the same trap `click_menu_action` documents below).

- **Simulated human "mistakes" are intentional, not bugs.** `controller.click_entity` has a
  `MISTAKE_MISCLICK_CHANCE` (~4.5%, see `settings.py`) chance of deliberately aiming beside the
  target instead of on it, logged as `[!] Misclick -- aimed beside X instead of on it`. This is
  anti-detection humanization, not a targeting bug — code that reacts to a click's outcome must
  handle this (see golem_crafting.py's mining reclick gate for an example of a bug this caused:
  waiting forever for an inventory change that a misclick will never produce).
- **`controller.py` EXISTS TWICE in a running process, so its module-level state is duplicated.**
  It is run as a script (`python controller.py routines/x.py`), so it lives in `sys.modules` as
  `__main__` — and every routine and shared module then does `import controller`, which loads the
  same file AGAIN under the name `controller`. Python caches by NAME, so those are two distinct
  module objects. PROVEN 2026-08-30 by loading it both ways and comparing identity, after the
  numbers in a log stopped adding up (an idle-drift line claiming 3.1s of stillness one second
  after a click).
  The engine loop lives in `__main__`; routine calls go through `controller`. So **any
  module-level variable one of them writes, the other cannot see.** Confirmed casualties:
  `_last_mouse_activity` and `_busy_until` (the idle drift never saw routine activity at all, and
  drifted straight through active play), and `_fidget_ser` (in-wait fidgeting has never fired for
  a routine-side wait). `FIRMWARE_PROTOCOL` is safe only because nothing outside `controller.py`
  reads it.
  **Anything that must be shared across those copies belongs in its own module** —
  `input_state.py` exists for exactly this and is only ever imported by name, so there is one of
  it however many times `controller.py` is loaded. `tests/test_controller.py` has a test that
  fails the moment shared input state migrates back into `controller.py`.
  THE AUDIT IS NOT FINISHED: only the three variables above were checked. This class of bug fails
  SILENTLY — nothing errors, the state simply never arrives — so the remaining module-level
  mutable state in `controller.py` is worth a deliberate sweep rather than waiting for the next
  symptom.

- **The panic key can go dead SILENTLY under load, so it has two independent paths.**
  USER-REPORTED live 2026-08-30, mid-run: F1 and F2 both stopped responding while the routine was
  still driving the mouse. `keyboard.wait()` rides a Windows LOW-LEVEL HOOK, and Windows silently
  REMOVES such a hook when its callback overruns `LowLevelHooksTimeout` (default 300ms; confirmed
  not overridden on this machine). Once dropped it is gone for the life of the process, with no
  error anywhere. The suspected trigger was the hotspot burst having just become much busier — a
  tight click loop making two extra HTTP calls per click is exactly the GIL pressure that starves
  such a callback — though that specific causation is inferred, not proven.
  `_async_key_watchdog` polls `GetAsyncKeyState` on a daemon thread instead: no hook, so nothing
  to lose, and it keeps working when the foreground window is elevated. F1 runs BOTH paths
  (`panic_exit` is a hard `os._exit`, so a double fire is harmless); F2 runs ONLY the polled one
  and is edge-triggered, because toggling is not idempotent and two paths would cancel each press
  out. Panic HAD this redundancy until 2026-08-27, when it was unified to one mechanism for
  symmetry with pause — **that unification is what removed the protection this failure needed.**
  Do not re-unify them.

- **Arduino serial port**: only one process can hold the COM port at a time. The MCP debug
  server no longer touches it at all (read-only bridge queries only, see above) — the only real
  contender is a stray/leftover `controller.py` process.
- **`ctx.state.data` reset on restart** (see above) can desync from real in-game state that
  persists — always consider whether a routine needs to reconcile against a live bridge read
  (e.g. an object's actual name) rather than trusting its own resumed bookkeeping.
- **Always shift+click (`key_down`/`key_up` "SHIFT" around the click) when walking to a plain
  ground tile, never a bare click.** A ground point close to any interactive object can resolve
  to that OBJECT's own action instead of walking there if their clickable areas overlap on
  screen — CONFIRMED live (golem_crafting.py's hunt_phase, 2026-08-09): a plain click near the
  Goat Pit while walking back to a standing tile cleared/unlined it instead of just walking.
  Shift+click is OSRS's own standard force-walk shortcut (forces "Walk here" regardless of
  what's under the cursor) and looks like any other single click, so there's no reason not to
  use it for every walk-to-a-tile click, not just ones near a known-risky object. See
  `golem_crafting.py`'s `walk_to_exact_tile` for the reference implementation.
- **Routing engine replaced 2026-08-29: Shortest Path (the separately-installed RuneLite plugin)
  is gone, along with roughly 700 lines of door/reachability heuristics it made necessary.**
  `ControllerAddon/worldmap.py` — an offline, world-wide per-edge collision bitmap covering all
  four planes, ported from EthanApi's `GlobalCollisionMap` — is now the **only** routing engine.
  `travel.route_to`/`travel.walk_to` call `worldmap.find_path` directly (0.4-3ms for an in-house
  route, 151ms for a 314-step cross-city one), no bridge call needed for the route itself, and it
  fixes the exact gap Shortest Path had: it routes on ANY plane, where Shortest Path's own dumped
  collision map was ground level only (see the NORMAN's/JEFF's/ROSS's measurements below, and the
  mahogany_homes.py section for the live session that forced this). The static map bakes doors
  OPEN, deliberately, same as Shortest Path's own dump was (`CacheMapDumper`'s `Exclusion` list
  does it on purpose) — so a computed route runs straight through a doorway, and
  `travel.open_door_toward(ctx, goal)` (built on `first_door_on_route`/`blocked_crossing_on_route`)
  finds and opens whichever shut door the route actually crosses. That one call replaced the
  three-tier escalation described in the bullets below (`_door_separates`/`_within_span`,
  `open_frontier_door`, `open_nearest_shut_door`) — those tiers existed only because there was no
  route to ask; given a real route the door question is nearly trivial and works identically on
  every floor. **What did NOT move onto the static map:** object-interaction adjacency ("can I
  click this hotspot/ladder from here") is still answered from LIVE `GET /collision` flags via
  `travel.can_reach`/`travel.reachable_set`, because the static map bakes an object's own blocking
  into the same bits as a wall's and structurally cannot tell furniture from a wall — see JEFF's
  measurement in the mahogany_homes.py section, still the reason. **Transport execution is gone
  too, not rebuilt:** `worldmap.find_path` has no transport graph at all, so `route_to`'s own
  docstring says plainly "no transports" — a routine that needs a teleport now owns that itself
  (`mahogany_homes.py`'s `CITY_TELEPORTS`, or the standalone `teleport_to_ferox`/`use_fountain`
  already in `travel.py`). The bullets below describe the Shortest-Path-era machinery as it was
  measured and built; keep reading them for the MEASURED game geometry (door ids, coordinates,
  collision flags), which is still true of the game — not for the function names, which have moved
  onto `worldmap.find_path`/`travel.open_door_toward`/`travel.first_door_on_route`/
  `travel.blocked_crossing_on_route`.

- **Historical, pre-2026-08-29 — Shortest Path's own transport graph could route through a
  transport we had no way to execute.** `_use_transport` could only click a single menu option on
  an object or NPC; any teleport, or any transport opening a destination picker (canoes, charter
  ships, minecarts), produced an edge with no clickable `objectId`. CONFIRMED live: a `walk_to`
  from Civitas illa Fortis once returned a route whose first edge was a `QUETZAL_WHISTLE` to a tile
  **454 tiles away**, purely because the user's own Shortest Path panel had quetzals enabled —
  `travel.TRANSPORT_POLICY`, sent with every request, was the fix at the time. Moot now: the new
  routing engine has no transport graph to leak a bad edge from in the first place, and Shortest
  Path's separate sticky, session-global config-override bug (a quirk of that plugin's own
  `PluginMessage` handling) went away with the plugin.

- **Doors: `collisionFlags` + `GET /collision` are the ground truth, and the useful question is
  per-EDGE, not per-door.** `ObjectProvider` reports `collisionFlags` on every object (the client's
  own `CollisionData` entry for that object's tile) and `CollisionProvider` serves
  `GET /collision?x=&y=&plane=&radius=` for ARBITRARY tiles, including empty ones -- needed because
  the doorway you are trying to walk through usually has no object on your side of it.

  MEASURED live by opening Barbara's two doors one at a time and re-reading the SAME doorway:

  | | shut | open |
  |---|---|---|
  | outer | id 7452 @ (1746,3534) `mE\|sE` (4104) | id **7453** @ (1747,3534) `mS\|sS`, doorway tiles -> **0** |
  | inner | id 7452 @ (1748,3537) `mS\|sS` (16416) | id **7453** @ (1748,3536) `mW\|sW`, doorway tile -> **0** |

  Five facts, all confirmed twice and still exactly what `travel.py`'s `DOOR_BLOCKING_FLAGS`/
  `DOOR_EDGE_BLOCKS` encode:
  1. Movement and line-of-sight bits are ALWAYS set together for the same edge (`mE|sE`).
  2. A blocked edge is recorded on BOTH its tiles as opposite directions -- it is a genuine EDGE,
     not a tile property, so either side is enough to know the crossing is shut.
  3. Opening a door CLEARS the doorway edge outright (to 0) and the leaf comes to rest blocking a
     PERPENDICULAR edge.
  4. The door object MOVES one tile and CHANGES ID when it opens (7452 shut -> 7453 open).
  5. A shut door's flags are identical to the plain wall segments beside it (Barbara's read 4104
     either side), so collision can never IDENTIFY a door -- `actions` ("Open" vs "Close") does
     that, and always did.

  **Do not compare two different doors' flags to infer state** -- an earlier pass did exactly that
  at Lumbridge (a shut door and a different open one both read 1026), concluded the flags carried
  no state, and wrote that up as fact. 1026 was the open door's LEAF edge. Comparing one doorway
  before and after is the only comparison that answers anything.

  What the code actually asks is narrower and needs no classification at all
  (`travel.blocked_crossing_on_route`/`first_door_on_route`, which replaced `_door_separates`):
  **is there a step of the computed ROUTE we cannot cross, and is a shut door standing on it?** A
  shut door across the path answers yes; the same door once open answers no, for free -- and
  because the question is asked against a real route rather than a straight-line comparison, it no
  longer misses a door that is off to the side or behind a detour the way the old single-axis
  `_door_separates` test structurally could (this used to need a separate escalation to a computed
  path for exactly that case; a route is now asked for up front, every time). Two traps worth
  knowing: `BLOCK_MOVEMENT_FULL` (2359552) is a COMPOSITE of `FLOOR | FLOOR_DECORATION | OBJECT`,
  so ORing it into a mask makes a bare floor decoration read as an obstruction (caught on
  Lumbridge's Trapdoor, 262144) -- `DOOR_BLOCKING_FLAGS` omits it; and an absent `collisionFlags`
  means "cannot tell", never "no".

- **A door is NOT always a WallObject, and not every door blocks an EDGE.** MEASURED live
  2026-08-29 at TAU's house, Falador, from the tile the routine was stuck on:

      Door id=24050 type=GameObject (3046,3348) d=1 actions=['Open'] flags=131328  <- the way in
      Door id=24050 type=WallObject (3046,3353) d=4 actions=['Open'] flags=4104    <- unrelated

  Same id, different object type -- `travel.DOOR_OBJECT_TYPES` is `("WallObject", "GameObject")`
  for exactly this (the name + "Open" action filter is what keeps it tight: nearby Drawers also
  offer "Open" and are excluded by name). Its collision is different in KIND too: `131328 =
  BLOCK_LINE_OF_SIGHT_FULL (131072) | BLOCK_MOVEMENT_OBJECT (256)`, with no movement edge bits at
  all -- a full-tile blocker like this separates only when its whole destination TILE is refused,
  which is what `blocked_crossing_on_route`'s destination-tile check (not just the edge test)
  catches; the route that exposed it ran (3045,3349) -> (3046,3348) -> (3046,3347) -> (3046,3346)
  straight through the door tile. The line-of-sight bit is the only flag family that marks this
  door at all, which is why LOS is in the mask and not just movement.

- **A hotspot's or ladder's own tile is usually not routable at all** (furniture stands on it),
  and aiming a route at the nearby standing tile instead can hide the doorway you actually need,
  because both the standing tile and the player often sit on the same side of the door. MEASURED
  live at Barbara's: the hotspot tile (1749,3536) itself reported unroutable (furniture) on 5 of 5
  checks while its neighbours routed fine. `travel.route_to(goal, goal_adjacent=True)` -- which
  `open_door_toward` always passes -- routes to a tile BESIDE the target rather than onto it, so
  the door question is resolved against the actual goal (not a nearby stand-in) by construction.
  The same TAU session found `standingLocation` itself can be unwalkable (furniture on
  (3047,3345)) -- another reason routing is asked for against the real goal, not a fallback tile.

- **Historical: three separate heuristic bugs at NORMAN's house (Falador), 2026-08-29, all fixed
  by moving to real routes rather than by patching the heuristics further.** A radius-limited door
  search missed doors standing 6-7 tiles away; a single-axis "is this edge between us and the
  goal" test false-positived on an unrelated `Door 24057` at (3027,3344), seven tiles off the
  direct line; and a walk aimed at `standingLocation` (3038,3344) crossed no doorway because it
  sat on the SAME side of the shut door 24050 (3037,3347) as the player. None of these failure
  modes exist against a real BFS route: `DOOR_ROUTE_LOOKAHEAD` (14 route steps) has no fixed-radius
  blind spot, and a route that actually reaches the goal is definitionally on the correct side of
  every door it opens.

- **Opening a door is CONFIRMED, not slept off, and the confirmation must budget the WALK to the
  door as well as the open itself.** A door is a WallObject whose id encodes its state and changes
  when it swings (shut -> shut+1, e.g. Barbara's 7452 -> 7453, Falador's 24050 -> 24051) and it
  also moves a tile, so the object you clicked is not there afterwards to re-read -- the doorway
  is, so `travel._open_door` polls the blocked EDGE until it clears (`DOOR_CONFIRM_POLL_S`, budget
  `DOOR_OPEN_WAIT_S + tiles * DOOR_WALK_ALLOWANCE_S` capped at `DOOR_OPEN_WAIT_MAX_S`) and logs
  `still shut after Ns -- another player may be holding it, or the click missed` otherwise. MEASURED
  live at ROSS's: a flat 2.0s budget was less than the ~2.4s of walking to a door four tiles off,
  so a click that DID register as `Open Door` on id 40178 got reported as a failure and interrupted
  by its own retry, three times over -- fixed by adding the per-tile walk allowance. Also MEASURED
  at ROSS's front door (40178): it swings shut BEHIND you, so re-reading the doorway can answer
  "still shut" about a door that worked -- the confirmation also succeeds on `_crossed()` (the
  player having crossed the door's blocked edge since the click), because getting through is the
  outcome that matters, not the doorway's state at poll time.
  **PARTLY SUPERSEDED 2026-08-30.** A good share of those "still shut" timeouts was not the door
  swinging at all: `_open_door` clicked with a BARE `click_entity` and so aimed at the pixel the
  door occupied when the ROUTE was computed, while the player was still walking toward it. The
  recorder showed every click registering as `Open Door` because the MENU resolved correctly --
  but a stale aim still lands somewhere useless. It now passes `refresh_target`; see "A click's
  DEPARTURE is aimed too" above. Both effects are real; do not assume a "still shut" log means
  the door closed behind you until the aim has been ruled out.

- **ADJACENCY IS NOT INTERACTABILITY, and `goal_adjacent` does not give you a tile you can act
  from.** `worldmap.find_path(goal_adjacent=True)` stops at ANY tile cardinally adjacent to the
  goal — and it never has to cross a wall to get there, so the route is perfectly valid by the
  static map's own rules while the tile it lands on cannot touch the target at all. MEASURED live
  2026-08-30 at BARBARA's: the Bed hotspot at (1749,3539) has its north and east edges walled
  (16416 / 65664) and only its south and west open (135432 / 132354), both inside the bedroom.
  The walker parked on the north tile, `can_reach` went on (correctly) answering False, and the
  routine sat there until its ceiling stopped it. `travel.interaction_tiles(goal)` answers the
  question that actually matters — which tiles have an OPEN EDGE to the target — from live
  collision, and walking to one of those routes through the bedroom door, which
  `open_door_toward` then finds as an ordinary door on the route. Empty means "cannot tell";
  fall back rather than refuse to work.

- **A route can cross a tile that live collision refuses, so `walk_to` re-routes around it --
  EXCEPT when a shut door is standing there.** The static map is not live: it cannot know what is
  standing on a tile right now, and it bakes doors OPEN. MEASURED live 2026-08-30 at TAU's,
  Falador, where the door is DIAGONAL — which the game cannot express as a `WallObject` on a
  cardinal edge, so it is a `GameObject` blocking its WHOLE TILE both open and shut. OPENING it
  therefore does not clear a tile, it MOVES a full-tile blocker: door 24051 ("Close") sat on
  (3046,3347) reading 131841, the route ran straight through, `_hop_index` refused the first
  crossing and the walk died one tile from the door. `worldmap.find_path` now takes `avoid=` and
  `walk_to` feeds it tiles it has learned are really blocked (`WALK_MAX_REROUTES`, 4). The detour
  is normally free — at TAU's it was the SAME 51 steps.
  **The exception is load-bearing and cost a regression to find:** at ROSS's the only way in is a
  SHUT door at (2610,3316), and after the door budget was spent the reroute excluded that very
  tile, leaving no route at all — failing HARDER than before the reroute existed. A shut door is
  to be OPENED, never routed around. `_door_on_route` is the discriminator and it is exact: it
  only ever returns a door offering "Open", so TAU's open leaf ("Close") is avoided and ROSS's
  shut door is not.

- **MINIMAP for distance, viewport only for short precise hops -- and every viewport walk click
  is SHIFT-clicked.** User directive 2026-08-29. `_hop_to` used to prefer a viewport click
  whenever the tile was rendered, with the minimap only as a fallback beyond render distance;
  with `WALK_HOP_MIN/MAX_TILES` at 16-24 that made nearly every hop of a long route a viewport
  click into a scene full of interactive scenery. CONFIRMED live the same day
  (`recordings/2026-08-29_012522_mh-live-run12`): hops resolved as `Chop down` ("You need an
  axe") and `Cook` ("You haven't got anything to cook") instead of walking, which strands the
  player somewhere it did not choose and makes the next pass walk back -- the user saw this as
  clicking back and forth. The minimap has nothing drawn on it to hijack a click; precision is
  what it lacks, and precision is only wanted at the END of a walk (exact-tile work does not come
  through `_hop_to` at all -- `walk_to_exact_tile` handles that, and was the ONLY caller obeying
  CLAUDE.md's own shift-click rule). Now: `HOP_VIEWPORT_MAX_TILES` (6, first draft, wants a live
  tuning pass) decides, and `_click_walk_point` holds SHIFT for the viewport case.

- **The NEXT hop is clicked BEFORE arrival, not after it** (`HOP_NEXT_CLICK_LEAD_TILES`, 6,
  added 2026-08-30). USER: "we wait so long the player runs to the tile we clicked before we
  click the next point resulting in a weird quick stop". MEASURED in that night's recordings
  (runs 23/24): a 0.6-1.8s dead stop at EVERY hop boundary, because `_hop_to`'s wait released
  within `WALK_ARRIVAL_TOLERANCE` (3 tiles ≈ 0.9s of running) while the next click needs
  ~1.5-2s (0.6s poll quantization + walk_to's collision/door bookkeeping + the mouse flight).
  Hops longer than the lead now release the wait with 6 tiles (~1.8s) still to run, so the next
  minimap click lands mid-run — the game just replaces the queued destination, which is what
  the human recordings do. Hops no longer than the lead keep the exact wait (instant release
  would spam clicks at their own destination). VERIFIED live the same night (Ardougne→JESS,
  ~30 tiles): 16 consecutive moving ticks across 2-3 hop boundaries, zero stopped ticks.
- **THE MINIMAP ORBS ARE DRAWN OVER THE MINIMAP CIRCLE AND EAT A HOP CLICK.** MEASURED live
  2026-09-09 (vale_totems): a westward hop's aim point at screen (1966,151) was reported
  `onMinimap: true` by `/minimap/walk` and was the **Hitpoints orb** sitting over the circle's
  left edge; four identical clicks moved the player nowhere and the trip failed. `onMinimap`
  is a CIRCLE test and knows nothing about what is painted on top, and a minimap hop cannot
  use `uiBlockers` -- the minimap rect is itself in that list, deliberately. So
  `GameStateProvider` now also reports **`minimapOrbs`**, one rect per visible orb-sized child
  of the Orbs group (the HP/prayer/run/special orbs and their number boxes, the compass, the
  XP, world-map and wiki buttons -- 46 rects on this client), and `travel._hop_to` rejects a
  minimap aim point inside one and pulls in toward the player instead. Needs a client rebuild;
  an older client answers no rects and behaves exactly as before.

- **The minimap reaches 28 tiles, and the DIAGONALS are what bound it.** MEASURED live 2026-08-28
  from (3037,3345) by walking `/minimap/walk` outward in all eight directions: `onMinimap` held to
  28 everywhere, then failed at 30 on the four diagonals while the cardinals were still on at 32 —
  a corner sits √2× further from the minimap's centre than a cardinal at the same tile distance.
  `WALK_HOP_MIN/MAX_TILES` were raised 10-16 → 16-24 on this measurement (user request: fewer
  stops between clicks, since each hop ends with the player standing still while the next click is
  aimed and flown). 24 keeps margin under 28 because the minimap's reach is a **user setting** —
  zooming it in shrinks it — and `_hop_to` now pulls an unreachable aim point in toward the player
  and retries (`HOP_PULL_IN_FRACTIONS`) instead of abandoning the whole walk, so a zoomed-in
  minimap degrades to shorter hops rather than a failed trip. Re-measure before raising further.

- **`TickClock` is no longer a self-registered EventBus subscriber** (changed 2026-08-21). It is
  bumped explicitly by `ControllerPlugin.onGameTick` via `tickClock.bump()`, because RuneLite does
  not guarantee subscriber ordering and the emitted `TICK` payload must carry the number for the
  tick it is announcing, not the previous one. Anyone adding a `GameTick` handler needs to know
  this: there is one writer. Dropping the self-registration also fixed a latent leak — `shutDown()`
  never unregistered it, so every plugin disable/enable cycle left another instance subscribed.
- **A `@Subscribe` method MUST be named `on<EventSimpleName>` — this is enforced, not stylistic.**
  RuneLite's `EventBus.register()` does `checkArgument(method.getName().equals("on" +
  parameterClazz.getSimpleName()))`, and the throw escapes the whole `register(this)` loop before
  the subscriber map is built. So ONE misnamed handler stops the ENTIRE plugin from registering —
  every other handler dies with it and the client comes up with no bridge at all. CONFIRMED
  2026-08-21 against `client-1.12.36.jar` in the Gradle cache, after a review caught an
  `onXpGained(StatChanged)` that would have shipped this. The corollary: you cannot have two
  independently-named handlers for one event type, so a second consumer of an event has to be
  folded into the existing method (see `onStatChanged`, which serves both LEVEL_UP and XP_GAINED).
- **`UdpEventSender.send()` makes two SYNCHRONOUS blocking socket writes on the client thread.**
  Anything emitted at high frequency stutters the game. This is why the object/ground-item spawn
  events are capped by `SPAWN_EVENT_CAP_PER_TICK` (40, shared across all four types): a scene swap
  replays the whole scene as spawn events in one tick — verified against the decompiled
  `GameEventManager.simulateGameEvents` — which without a cap is hundreds of blocking writes on
  RuneLite's render thread. Note a `GameState`-based guard does NOT work for this: `GameState` is
  already `LOGGED_IN` at the CLICK-HERE-TO-PLAY screen (see `LoginProvider.java`), and scene swaps
  from ordinary walking never change it at all. Dropped events past the cap are intentional — the
  Python mirror is an accelerator with an HTTP fallback.

## Routine status index

Per-routine live-session history, measured facts and open worklists live in
**`docs/routine-notes.md`** — read a routine's section there BEFORE working on it;
those facts were measured live and must not be re-derived. Cross-references in this
file of the form "see the X.py section/notes" point there. One-line status:

| Routine | Status |
|---|---|
| `corrupted_gauntlet.py` | Lobby -> gather runs unattended (live 2026-09-06); craft/enter/fight phases written but UNRUN |
| `guardians_of_the_rift.py` | Full rounds unattended; beat the human benchmark (run 75). Event-driven rewrite of 2026-09-04 not yet run live |
| `deep_sea_trawling.py` | NOT run live; needs client rebuild. Banking is an unbuilt sub-project |
| `wintertodt.py` | NOT run live; needs client rebuild |
| `vale_totems.py` | At human parity live (2026-09-10); magic-log hold pass partially verified 2026-09-11 |
| `granite_3t.py` | Cycle settled from recordings 2026-09-09; NOT run live in that form |
| `mining_guild_iron.py` | Live-debugged to 1674 ore/hr (2026-09-11); 52-min unattended run clean |
| `blast_furnace.py` | Live: gold 24-25s/trip, adamantite 2-trip cycle ~48s (2026-09-04) |
| `giants_foundry.py` | Full loop unattended live (2026-09-03/04), incl. metal substitution |
| `mastering_mixology.py` | Was working live 2026-08-13; 2026-09-06 event-driven rewrite NOT yet run live |
| `motherlode_mine.py` | Whole loop verified live 2026-09-07 (mine/hopper/sack/bank); repairs and breaks unrun |
| `mining.py` / `woodcutting.py` (`gathering.py`) | Shared loop; mining live-proven, woodcutting first live run OK 2026-09-08 |
| `shipwreck_salvaging.py` | Last live 2026-08-05; three offline passes since NOT run live, needs client rebuild |
| `agility.py` | Written from a recording 2026-09-02; NOT run live |
| `falconry.py` | Written from a recording 2026-08-23; 2026-09-06 event-driven pass NOT run live |
| `golem_crafting.py` | Was running unattended live; 2026-09-06 event-driven + falconry fur-trip rewrite NOT run live |
| `karambwan_fishing.py` | Full loop verified live 2026-09-06; later left-click/AFK passes NOT run live |
| `slayer.py` | Bank-trip return rebuilt 2026-09-07, pinned in tests, NOT run live |
| `chinchompa_hunting.py` | Working live 2026-08-21; known 7% collapsed-trap waste left deliberately |
| `mahogany_homes.py` | Extensively live-debugged (~40 contracts/hr over 7h, 2026-08-30) |

## Arduino firmware protocol (v3)

`firmware/Firmware.ino` speaks protocol v3 as of 2026-08-27 (see its own header block for the
full derivation). Purely additive over v1/v2 — a v2 firmware silently ignores every v3 command,
so either side can be upgraded first. The new commands: `X` flush (discard every queued
command now; the active segment always finishes), `V` version query (responds `HB 3`), `R`
reset (flush including the active segment, release every held button/key), `W <n>` scroll wheel.
Plus a 60s stuck-input watchdog: if any button/key is held with no serial line arriving for 60s,
everything is released — the panic path is a hard process kill that cannot send key-ups, so this
is the only thing standing between that and a button held forever.

**`KT`/`KD`/`KU` UPPERCASE every single letter** (`keyFromName` maps `a-z -> A-Z` before
`Keyboard.write`), so anything typed through `type_text` arrives uppercase — "adam" -> "ADAM".
VERIFIED ON HARDWARE 2026-09-07 by typing into Notepad in the OSRS VM (confirmed the whole
`KT`/single-letter path works, which `type_text`'s docstring had flagged unverified). Harmless
for the bank search box (case-insensitive) and chat; it would BREAK a case-sensitive field, so
a typed username/password login must never rely on it (we sign in from credentials.properties
instead). Fix belongs in the firmware if a case-sensitive need ever arises.

**The contract that matters most: `X` drops queued button/key commands too, not just motion, so
it is only ever legal from inside `move()`'s flight loop, where nothing but segments are queued.**
A future agent adding a serial write from a background thread, or a drag primitive, has no reason
to look in `human_move.py` for this — the constraint lives in the firmware's own comment and here,
not in any type signature. Sending `X` while a click or key-down is queued would silently discard
it.

`controller.FIRMWARE_PROTOCOL` (set once at startup by `probe_firmware_protocol`) gates every v3
feature, and it is a hard probe rather than graceful degradation on purpose: an ignored `X` on a
v2 board doesn't fail loudly — the queued motion just plays out anyway while `move()`'s Python-side
bookkeeping (`cur`/`queued_ms`) has already rewound as if it didn't. That produces silently wrong
clicks (aimed from a stale position estimate), not a visible failure, which is a far worse outcome
than refusing to use `X` at all against an unprobed or pre-v3 device.

`ControllerAddon/firmware_bench.py` is the flash-day checklist — run it after every reflash before
trusting the device. It opens the same COM port `controller.py` does, so **never run it alongside
controller.py** (same one-process-per-port constraint as the general Arduino serial port gotcha
above).

**VERIFIED ON HARDWARE 2026-08-27, first flash: `ALL PASS`, including `--watchdog`.** So every v3
firmware command has now actually executed on the device — `V` answered `HB 3`, `X` cut a 375px
uninterrupted travel down to 112px (fraction 0.30 against a 0.50 bound) with 0px of drift after,
`R` released a held SHIFT, and the 60s watchdog released one on its own after 65s of silence.
Note the bench's flush check is deliberately scale-free (it measures an uninterrupted baseline in
the same run and bounds the flushed travel as a fraction of it) because `S` moves raw HID mouse
units, not pixels: an earlier absolute-pixel window PASSED on a device where `X` did nothing at
all. The 375px over 500 units also puts this machine at 0.75 px/unit, within 2% of the 0.737 in
`.hm2_calibration.json`. What is still unverified is the ROUTINE-level behavior, not the firmware:
no live session has yet run a moving-target routine with flushing enabled. Success signal to watch
for is `_settle_on_target_before_click`'s corrective move firing less often; the failure signal is
any cursor stall on a flushed flight, which would mean the reduced lookahead described below is
biting in practice.

**Open follow-up (not yet verified live):** `HM2_FLUSH_COMMIT_MS` (22.0, in `settings.py`) plus
the rewind's "first emission at or past `commit_ms`" selection rule double-count the active
segment, so the rewind lands one-sidedly ~18ms / one segment AHEAD of what the firmware actually
played (reproduced deterministically on 8/8 seeds against the test rig, 2026-08-27). Left alone
deliberately, pending real hardware measurement — see the ruling in
`docs/superpowers/plans/2026-08-27-firmware-v3-flush.md`. The direction is benign (the next replan
undershoots into the residual corrections rather than overshooting), it does NOT accumulate across
multiple flushes within one flight, and the second-order cost is real: firmware buffer depth drops
from 54ms to ~36ms for the remainder of a flushed flight only, which the firmware's own
`CHAIN_MAX_LAG_US` (50ms) absorbs without a phase restart. The next live session after a flash
should measure the real post-flush commit latency; the candidate one-line fix, if it's warranted,
is to select the LAST emission with `t_ms <= commit_ms` instead of the first one at or past it —
that restores exactly the semantics `settings.py`'s own comment originally claimed. Do not change
`HM2_FLUSH_COMMIT_MS` or the selection rule without that measurement in hand.

## Sub-tick data and clicking while moving

Most game state is genuinely capped at OSRS's own 0.6s server tick — HP, true tile position,
target switches, which animation is playing — no client-side trick gets around that; the
information doesn't exist any faster server-side no matter how it's read. But on-screen
*position* data is not tick-capped: a moving actor's `LocalPoint` is smoothly interpolated by
the client every render frame, and RuneLite's `ClientTick` event (fires once per frame, not once
per game tick) is the general mechanism for reading that. This codebase's only current use of it
is `NpcTrackerRegistry.onClientTick`'s animation-*frame* diffing (catches a repeating attack
animation restarting even when the animation *id* itself never changes between reps — a
`GameTick`-only check would structurally miss this, not just see it late).

On the bridge side, only 6 providers (`NPCProvider`, `ObjectProvider`, `TileProvider`,
`GroundItemProvider`, `GraphicsObjectProvider`, `BankProvider`) are gated by `TickCache` — a
deliberate perf optimization for the bulk list endpoints (`/npcs`, `/objects`, ...), not a
platform limitation, and it already has a `?fresh=true` bypass. Single-entity lookups
(`/npc/by-index`, `/npc/nearest`, `/object/at`, `/player`, `/combat`) were never cached at all —
every poll already reads the client's current interpolated position fresh, however often you
ask.

This is why routines gate clicks behind `wait_until_player_idle()` (wait for the player to fully
stop before trusting a resolved screen position) rather than clicking while moving: most click
call sites resolve a screen position once and never re-check it, not because the position data
itself is too slow to get. `move_to_human`/`click_entity` already had a `refresh_target`
parameter for this (a zero-arg callable re-resolving a target's current screen position — used
by `slayer.py`/`tormented_demon.py`/`golem_crafting.py`, each hand-writing its own lambda) but it
only re-aimed once, partway through a flight. `refresh_legs` (added 2026-08-14, purely additive —
`None`/default reproduces every existing caller's exact prior behavior bit-for-bit) generalizes
this into multiple re-aim checkpoints toward interpolated waypoints (only the final leg lands
exactly on the target; earlier legs travel a fraction of the remaining distance, so a re-aim
mid-flight can still correct the approach), specifically so a routine could eventually click
without waiting for full stillness first. Reviewed via an independent second pass that caught
and fixed two real bugs in the first draft (leg 0 was flying the *entire* distance to a stale
snapshot before ever re-aiming, and per-leg jitter was being redrawn instead of fixed, adding
real wasted motion even for a stationary target) — see `_move_to_human_tracking`'s own docstring
for the full account. Covered by unit tests in `tests/test_controller.py`, but **not yet
exercised against a live client**, and no routine has adopted it yet as of this note. See
`npc_index_refresh_target`/`ground_tile_refresh_target`/`object_refresh_target`/
`ground_item_refresh_target` (the last two added 2026-08-14 alongside `/object/at` and
`/ground-item/at`, the object/ground-item equivalents of `/npc/by-index` — see "Instanced areas
break forward world-coordinate conversion" above for why both are implemented by filtering the
already-corrected bulk `/objects`/`/ground-items` lists rather than a fresh forward lookup) for
ready-made `refresh_target` factories instead of hand-writing the lambda at each call site.

**`click_menu_action` GAINED `refresh_target` on 2026-08-27** (it forwards to the underlying
`click_entity`, so a right-click now re-aims mid-flight like any other). The warning below
describes why it was needed and still describes what happens if you DON'T pass one: Added 2026-08-23 after breaking it live. The bare-click path hides stale coordinates
because `click_entity` re-aims mid-flight from an un-cached endpoint; the menu path has no such
parameter, so it right-clicks exactly the pixel you give it. Escalating a hijacked plinth click to
`click_menu_action` using the object from the `/objects` list fetched at the top of the tick — i.e.
before the player walked — produced a menu of pure ground options (`['Cancel', 'Mark Safespot',
'Examine', 'Walk here']`): a right-click on bare tile, with the object never involved. Re-resolve
through `get_object_at`/`get_npc_by_index` immediately before calling it. Also pass `offset=(0, 0)`
for a small target: the default `(-7, +7)` nudge is by itself enough to slide off a plinth.

**This is the PREFERRED pattern for every new click site — reach for it before
`wait_until_player_idle()`.** Stated as a standing directive 2026-08-23 (user): a single-entity
accessor is not tick-limited, so a click can be aimed while the player is still walking, and that
is what closes the gap to human speed. The shape is:

1. Fire the walk **without waiting for arrival** (no blocking exact-arrival poll).
2. Issue the target click a beat later, passing a `*_refresh_target` factory **and**
   `refresh_legs` — the un-cached endpoint behind the factory re-resolves the target's live screen
   position at each leg, so the aim stays correct even though the player (and camera) moved after
   the click was decided.
3. Confirm the outcome on a pushed/un-gated signal (XP_GAINED, a varbit, a state endpoint), not
   on a fixed sleep.

**Measured against a human, 2026-08-23** (`recordings/2026-08-23_194302_golem-carving.*`, captured
via `recorder.py` — see "Recording a human session" above): a human clicks the target object
**0.6–1.0s after the walk click**, from a tile they have not reached yet, four times out of four,
and every one of those interactions resolved correctly — the game queues the interaction and
performs it on arrival. Waiting for stillness first is therefore not a correctness requirement,
it is pure latency we were adding on top of the game's own cadence.

The legacy pattern — `walk_to_exact_tile`/`wait_until_player_idle()` blocking until the player has
fully stopped, then resolving a screen position once — is *safe* but structurally slower than a
human, and its blocking walk is what makes an over-tight confirmation timeout destructive rather
than merely slow (a timeout re-walks and interrupts the action that had genuinely started; see
`routines/golem_crafting.py`'s craft_phase notes). Prefer the accessor + `refresh_legs` path for
anything new; leave existing blocking call sites alone unless their throughput is actually a
problem.

Note the endpoints for this already exist and are already un-cached — `/object/at`,
`/ground-item/at`, `/npc/by-index`, `/npc/nearest`, `/player`, `/combat` — so adopting this
needs **no Java change and no client rebuild**, only the Python call site. Check whether the
accessor you need is already there before proposing a new provider.

**Movement realism batch (added 2026-08-27, Python-only, no rebuild):**
- **Pointer-scale calibration persists across restarts** (`.hm2_calibration.json` next to
  human_move.py, gitignored, atomic write, every I/O failure swallowed). It is a property of
  the MACHINE, not the session, but used to reset to 1.0 every process start, so the first flights
  under-traveled ~26% and the residual corrections cleaned up the difference. Tests isolate it
  via an autouse fixture — never let a test write the real file.
- **Startup warns if Windows "Enhance pointer precision" is on** (`warn_if_pointer_acceleration`,
  SPI_GETMOUSE via ctypes) — acceleration makes px-per-unit speed-dependent, which the single
  scalar calibration cannot express. Warn-only, never a hard stop.
- **Session tempo**: durations now share an OU-drifting log-offset (`HM2_TEMPO_SIGMA`/
  `HM2_TEMPO_TAU_S`, see their comment for the five-recording measurement: lag-1 ~0.15, 2-min
  block means wandering 2.4x the i.i.d. expectation). Sessions start in a random mood.
- **Handedness was MEASURED AND KILLED** the same pass — signed path curvature flipped sign
  between sessions (left-bulge 39-64%). v2's zero-mean curve draw is correct; do not add a bias.
- **Click hold times** re-fitted from 589 human down→up pairs (`CLICK_HOLD_RANGE_S`): median
  85ms, log-sigma 0.266, replacing a geometry whose median sat at 70ms with a truncated tail.
- **In-wait fidgeting** (same day, after diffing 13.1 active human minutes against 164 active
  bot minutes): cadence, pause distributions and tick-phase all already matched the human —
  the ONE loud remaining signature was stillness (human cursor busy 80% of active play, never
  still >4.5s; bot busy 13%, still-period p90 11.8s). `human_delay` draws ≥2s are now split
  into measured human-length still segments (median 1.5s, capped 4s) separated by small
  out-and-back drifts. The fidget is POSITION-INVARIANT so hover-parking through a wait still
  works; it skips when a right-click menu is open (moving off one closes it), the window is
  unfocused, or an abort is set; it only ever lengthens a wait, never shortens it; and it arms
  only via `register_fidget_serial(ser)` in main() — an import without the Arduino keeps the
  old plain sleep. The 2026-08-30 caveat (main() armed `__main__`'s `_fidget_ser` while a
  routine reached the other copy's `None`, so **in-wait fidgeting had never once fired for a
  routine-side wait**) was CLOSED 2026-09-06: the serial is mirrored into
  `input_state.fidget_ser` and read through `controller._fidget_serial()`. Routines' own
  0.2-0.5s poll loops still never enter `human_delay`, so `controller.fidget_once()` (same
  guards, one fidget) is what `outcome_waits.await_long` and the routines call from an event
  wait; Phase B (a background fidgeter behind a global action lock) remains a design only.
- **The fidget's return leg lands NEAR its anchor, not on it — an exact return is itself the
  signature (fixed 2026-09-06, the first live look at fidgeting actually running).** USER,
  watching karambwan fishing: "investigate idle movement it looks a bit weird? did something
  change?" — it had, that morning: closing the caveat above turned routine-side fidgeting on
  for the first time ever. MEASURED from the live recorder run against the user's own human
  recording of the SAME activity (`recordings/2026-09-06_090950_cg-bot1` vs
  `..._083723_karamwan-fishing`), tethered out-and-back bursts / longest chain about one
  anchor / rests on one exact pixel: giants-foundry bot 09-03 **12% / 11 / 13**, GOTR bot 09-02
  **5% / 3 / 10**, human **26% / 10 / 3**, this run **33% / 19 / 11**. Burst RATE matched the
  human fine (24.6 vs 22.4 per minute) — the fit is good. What did not was `aim_radius=0` on
  the return: the cursor snapped to a mathematically identical pixel every time and tethered to
  one point, which no hand does. The return now aims at a remembered ANCHOR
  (`input_state.fidget_anchor`) with `IDLE_FIDGET_RETURN_JITTER_PX` (3) of jitter — 200
  simulated rests spread over 59 distinct pixels, mean drift 2.2px, max 7.2px. **Anchoring is
  the load-bearing half**: returning to wherever the last fidget happened to land would compound
  the jitter into a random walk off the target, and mixology/golem_crafting park the cursor on
  the next target and then click IN PLACE. The anchor is re-taken only when the cursor is more
  than `IDLE_FIDGET_ANCHOR_RESET_PX` (15) from it, i.e. something else moved it.
  **General lesson: a humanization that returns to an EXACT coordinate is a tell, however
  human its timing is.** Diagnose this class by running the same metric over a bot recording
  and a human recording of the same activity — the routine's own log cannot see it at all.
  **VERIFIED live 2026-08-27** (recordings/2026-08-27_023951_phase-a-verify, 3.8 active min of
  slayer): freeze tail dead (max 103s → 8.5s, p90 11.8s → 7.1s), settles firing correctly, no
  interference — but the residual p90 sat exactly at the then-4-9s idle-move timer, i.e. the
  TIMER was the new ceiling. `maybe_idle_mouse_move` therefore no longer runs on wall-clock at
  all: it triggers on time since the last DELIBERATE cursor movement, with the budget drawn
  from the same measured still-segment distribution.
  **CORRECTED 2026-08-30 — the sentence above was NOT true in practice, for two separate
  reasons, and both are now fixed.** (a) The marker lived in `controller.py`, which is loaded
  TWICE in a running process (see "controller.py exists twice", below), so the engine read one
  copy while every routine click bumped the other: the trigger never saw routine activity at
  all and fired continuously through active play. It now lives in `input_state.py`. (b) Even
  once shared, the drawn budget can be ~1s, which is well inside the ordinary gap between two of
  our own actions, so it still drifted mid-work — MEASURED 14 drifts per 48 clicks, 5 of them
  after under 2.5s. `IDLE_MOVE_MIN_STILL_S` (4.0) is now a floor beneath the drawn budget; the
  same measurement this feature rests on says the human is NEVER still longer than 4.5s, so a
  drift at 1.3s was out-moving them rather than imitating them. AFTER: 7 drifts, stillness
  `[4.0, 4.4, 4.4, 17.7, 4.0, 4.5, 4.5]`s, none under 4s. USER-REPORTED throughout as the cursor
  being "dragged back" mid-work; `maybe_idle_mouse_move` now LOGS every drift, because one that
  moves the cursor with no click is otherwise invisible in a log full of clicks — which is
  exactly how it hid.
  Routines can also declare themselves busy (`controller.mark_busy(seconds)`) — belt and braces,
  and NOT sufficient on its own: marking work_phase busy left `_climb` unmarked and the drifts
  simply reappeared there. Prefer the floor; reach for `mark_busy` only for a genuinely long
  pending action.
  Still not re-measured on a 30+ active-minute recording; if still-p90 stays >4-5s the remaining
  freezes are the poll-loop blind spot and Phase B is what's left.
- The fitting scripts live in the session scratchpad, not the repo; the numbers and method are
  recorded in the settings comments. Reach segmentation: movement bursts split on 150ms
  stillness, kept if ≥30px and 80-3000ms; stillness stats count only ACTIVE play (a click
  within 30s on both sides), so scheduled breaks don't contaminate them.

**Moving-target aiming hardening (added 2026-08-27, needs a client rebuild for the Java parts):**
- **`npc_index_refresh_target`'s deadband now keys on every motion source, not just the NPC's
  tile.** The 2026-08-27 tile-only hold (added to suppress hull-animation jitter) was itself a
  bug for click-while-moving: when the PLAYER walks, the NPC's tile never changes while its
  projection slides continuously, so the hold served a stale point for the whole flight —
  literally "clicks land behind". `/npc/by-index` now also reports `npcLocalX/Y`,
  `playerLocalX/Y` (per-frame interpolated LocalPoints) and `cameraYaw/Pitch/Scale`;
  `_npc_view_key` folds them into the hold key (quantized by `NPC_HOLD_LOCAL_QUANTUM`). A fully
  still scene still holds through animation jitter; any real motion re-resolves. Degrades to
  tile-only against an un-rebuilt client (fields absent → None in the key).
- **`click_entity` takes one last look before the mouse-down** (`_settle_on_target_before_click`):
  the flight's final re-aim happens at a submovement boundary, so the settle + click tail
  (~100-300ms) was blind. One corrective move fires only past `PRECLICK_MAX_DRIFT_PX` (18px —
  generous because mid-flight refreshes deliberately drop the caller offset, see move_to_human's
  v2 dispatch), so stationary targets pay nothing. Skipped during a simulated misclick on
  purpose — settling would un-miss the deliberate miss; the corrective reclick gets the full
  treatment instead.
- **Verify-then-click via `GET /hover`** (MenuStateProvider): the default left-click action +
  target under the cursor RIGHT NOW — the top (highest-index) live menu entry, no menu open
  needed. Opt-in per call site: `click_entity(..., expected_action="Attack",
  expected_target="Gargoyle")` reads it before committing; on mismatch it re-aims once via
  refresh_target, and withholds the click if the hover still disagrees — prevention instead of
  `/last-action` post-hoc detection, and the first primitive that beats a player camped on the
  pixel WITHOUT the right-click menu escalation. `hover_matches` never blocks when it cannot
  verify (endpoint missing on an old build, or a real menu open). A withheld click returns the
  falsy `CLICK_WITHHELD` sentinel, not plain `False` — every existing truthiness check keeps
  working, and a caller with an escalation path tells "pixel contested" from "aim failed" via
  `getattr(result, "withheld", False)`. `expected_action` also takes a sequence (any match
  passes) for state-dependent options the caller doesn't choose. Adopted same day: slayer.py's
  two attack clicks (`expected_action="Attack"` + the live target name) and golem_crafting's
  plinth clicks (`GOLEM_PLINTH_OPTIONS`), where a withhold folds into the existing `hijacked`
  flag — it spends the same fast-retry budget, escalates to the same menu tier, and past the
  budget falls through to the same timeout/miss counting, so every measured bound (including
  the out-of-fur recovery, which counts on `gc_side_misses` climbing) is preserved. None of the
  adoptions have run live yet.


**XP gain is another sub-tick, general-purpose signal — not specific to any one routine.** Added
2026-08-17 (user directive, generalized from `pyramid_plunder.py`'s tomb-door confirmation):
`client.getSkillExperience(Skill.X)` reflects the moment the client's own `StatChanged` event
fires — this is the exact signal the game's own XP Drop plugin uses
(`data/decompiled/.../xpdrop/XpDropPlugin.java#onStatChanged`) — and is not gated by `TickCache`
or the 0.6s server tick the way bulk `/objects`/`/npcs` scans or `roomNumber`-style state are.
Any skilling action that grants XP on success (thieving, mining, fishing, crafting, ...) can use
this as a fast, definitive "did that attempt actually succeed" check instead of polling chat
messages, waiting out a fixed delay, or re-reading tick-capped object/state fields.

`PlayerProvider` exposes each skill's cumulative XP the same way (`hitpointsXp` was the existing
precedent; `thievingXp` was added alongside this note) — add more `data.put("<skill>Xp",
client.getSkillExperience(Skill.<SKILL>))` lines there for whichever skill a routine needs, same
pattern. **Requires a RuneLite client rebuild + restart** (`./gradlew.bat run`) before a newly
added skill's XP field shows up on `GET /player` — it's compiled into the Java plugin, not
something the Python side can pick up on its own.

Python-side usage pattern (see `pyramid_plunder.py`'s `attempt_door_click`): snapshot
`controller.get_player().get("<skill>Xp")` immediately before the click, then poll for an
increase inside the same short wait loop already used for other post-click signals (position
jump, confirmation chat message, ...) — an XP increase is proof the underlying skill check
succeeded, sub-tick and often faster than any other available signal.

## Recording a human session before writing a routine

`ControllerAddon/recorder.py` + `providers/RecorderProvider.java` (added 2026-08-23, user request)
capture a **human** playing an activity by hand, so a new routine can be written by reading the log
instead of guessing the activity's mechanics and live-debugging them into existence. Prefer this
over starting a new routine from scratch.

```
python -u recorder.py sunstone-mining      # from ControllerAddon/, then go play. Ctrl+C stops.
```

Three files land in `ControllerAddon/recordings/` (gitignored):
`<stamp>_<label>.jsonl` (every record, raw), `.log` (the readable narrative — the one you read to
build), `.summary` (object/npc/anim/varbit ids, xp, net inventory).

- **Requires a client rebuild + restart** (`./gradlew.bat run`) before `/recorder/*` exists — it's
  compiled into the Java plugin. `recorder.py` detects the 404 and says so rather than failing
  obscurely.
- **Safe to run alongside anything.** It imports only `settings`+`requests` — deliberately *not*
  `controller.py` — so it never touches the Arduino serial port, same rationale as
  `mcp_debug_server.py`.
- **Endpoints**: `GET /recorder/arm?on=true&label=X` (arming clears the buffer and resets seq),
  `GET /recorder/events?since=<seq>&limit=<n>`, `GET /recorder/status`. Disarmed by default — every
  listener returns immediately, so an unused recorder costs nothing.

**Why the capture is Java-side and not a Python poll of `/last-action`:** that endpoint holds only
the most recent click (two clicks inside one poll interval and the first is gone); a
MenuOptionClicked's `param0`/`param1` are *scene* coordinates that only the client thread can
convert to a real world tile (and inside an instance need `WorldPoint.fromLocalInstance` — see the
instancing gotcha above, already handled here); and outcome correlation needs events observed as
they fire, not inferred from end state.

**Outcome correlation is the point.** The hard part of every routine here isn't "what to click", it's
"how do I know the click worked". So each action opens a 5-tick (~3s) window, and every ambient
record landing inside it (animation, spotanim, ground gfx, the clicked object transforming, XP,
inventory delta, chat, varbit flip) is tagged `causedBy: <action seq>`. The narrative log nests
those under the click. A despawn+spawn pair on one tile renders as a single `became` line — that's
a depleting rock / opening door / lighting brazier, usually the best success-signal available.
An action with **no** observed outcome is called out explicitly; that's the case needing a
precondition or retry in the eventual routine.

Records are append-only with monotonic `seq` (the `?since=` cursor depends on it) — actions are
*not* held back until their window closes; `recorder.py` does the grouping at render time.

**`dropped` means records evicted BEFORE the reader took them, since 2026-09-05.** The first
Corrupted Gauntlet recording (26695 records, seq 0..26694 verified gap-free, the whole Hunllef
fight present) ended with `!! 6695 records were dropped` -- exactly 26695 minus the 20000-slot
ring, because eviction counted whether or not recorder.py had already drained the record. Any
recording longer than the ring raised a false "this recording has a gap". `RecordRing` (extracted
from `RecorderProvider`, pinned by `RecordRingTest`) tracks the highest seq handed out and only
counts an eviction past it. **Before believing a drop warning, check `seq` continuity in the
`.jsonl`** -- a real hole is a gap there, nothing else is.

**Every record carries `t` (ms since arm), `wall` (absolute epoch ms), `tick`, and `tickAge`.**
`tick` is a mirror of `client.getTickCount()` refreshed on GameTick, because records are also
created on the AWT thread (mouse/key listeners) where touching the client isn't safe — so it can be
stale in a way that *looks* valid. `tickAge` (ms since that tick fired) makes staleness visible;
`t` is the authoritative ordering key, and the narrative flags any action whose `tickAge >= 600`.

Volume guards, all load-bearing: ambient records are capped to 12 tiles; other players' animations
are dropped entirely (a crowded bank would otherwise dominate); object spawn/despawn is suppressed
for 1.5s after any non-`LOGGED_IN` state, because `GameObjectSpawned` fires for *every* object in
the scene on each region load and would evict the real recording out of the ring buffer. Mouse
moves are batched into polyline records rather than one per sample. A `dropped` counter surfaces
any hole in the recording, loudly.

**Keystroke privacy**: the key listener sets `isEnabledOnLoginScreen() == false` and drops
everything unless the game state is `LOGGED_IN`, so an account password can't land in a plaintext
recording.

### Findings from the first live recording (Falconry, 2026-08-23) — all fixed, don't re-derive

- **`MenuAction.WALK`'s `param0`/`param1` are CANVAS coordinates, not scene coordinates.** Recorded
  values were identical to `canvasX/canvasY` (400,216), and 400 is outside the 104-tile scene grid.
  Every other tile-based action (objects, ground items) *does* use scene coords. The walk
  destination is only knowable from `getLocalDestinationLocation()` **after** the click is
  processed, so it's read on the next tick and emitted as a `walkdest` record attributed back to
  the action. Don't try to decode a walk target from the menu event.
- **`AnimationChanged` never fires for an NPC that spawns already animating** — the animation is
  part of its initial state, not a change to it. The Gyr Falcon spawns mid-swoop, so its animation
  was entirely absent while the player's and the kebbit's were present. NPC animations therefore
  come from a per-tick diff in `diffNearbyNpcs`, not the event; the event is kept for the local
  player only (always fires there, and gives sub-tick timing for free). This costs no fidelity —
  unlike screen position, which animation is playing is genuinely tick-quantised server-side.
- **NPCs transform in place: same index, different id.** The Dashing kebbit *becomes* the Gyr Falcon
  at index 12323 — no spawn, no despawn, so an id-change check is the only way to see it. Emitted as
  `npcchange`. Assume this pattern anywhere an NPC "turns into" something.
- **A first-event baseline silently eats one unit of everything.** The first `ItemContainerChanged`
  and the first `StatChanged` per skill were being consumed to establish a diff baseline, so 7
  catches summarised as "+6 Bones / Hunter +936" — exactly one catch short. `seedBaselines()` now
  captures inventory and all-skill XP at arm time.
- **Varbit noise is defined by fraction-of-ticks, not total count.** Three varps changing every tick
  (101/101/61 over ~100 ticks) buried the real signals. A flat count-12 cutoff hid the noise but
  *also* hid `varbit 14192` (14 changes — the falcon-has-a-catch flag, the most useful value in the
  session). `noise_cutoff` scales with elapsed ticks instead. Suppression affects only the narrative;
  the `.jsonl` and `.summary` always keep everything.
- Renderer gotcha: the Java fields are camelCase (`targetX`, `playerPlane`). Building the key as
  `targetx` renders every position as `?` rather than raising — the capture looked broken when it
  was perfect.
- **A MINIMAP click is not a `WALK`.** It fires `MenuAction.CANCEL` with `param0`/`param1` both 0
  and no target at all — indistinguishable from dismissing a right-click menu, from the event
  alone. CONFIRMED live 2026-08-23 (full Falconry bank run): every long-distance leg of a 5-region
  trip logged as a meaningless "Cancel" with the destination lost, which is exactly the data a
  travel routine needs. Both `WALK` and `CANCEL` now speculatively resolve a destination on the
  next tick, and a **destination-changed** comparison decides which it was (a real menu cancel
  moves nothing). Don't try to classify it by canvas position — the minimap moves between
  fixed/resizable layouts.
- **Decode packed widget ids.** RuneLite packs interfaces as `(group << 16) | child`; the raw
  integer is useless (`25362456`), the pair is not (`387.24` = ring slot on the worn-equipment
  interface). `param0` rides along as the slot index *within* the component (inventory/bank slot),
  which is a different thing from the child. `fmt_widget` does this. Verified against the bank run:
  `12.45` bank Empty-containers, `15.3` bank container (+slot), `149.0` inventory (+slot),
  `387.16/.18/.24` cape/weapon/ring equipment slots.

## Bank memory (`GET /bank/memory`, `ControllerAddon/bank_memory.py`)

Added 2026-09-07 (USER: "we can actually see what teleport items we have in bank by storing a
snapshot of our bank locally ... means we can poll which best method to travel back with").
Modelled on the Plugin Hub **Bank Memory** plugin (Lazyfaith/runelite-bank-memory-plugin), whose
mechanic is copied rather than reconstructed. **Sub-project 1 of 2: the travel chooser that
consumes it is NOT built.** Needs a client rebuild; no routine reads it yet.

**Why this is not `/bank`.** `BankProvider` builds its item list from `BANK_ITEM_CONTAINER`'s
dynamic children and skips `child.isHidden()`, so it can only report what the interface is
DRAWING: items on a numbered tab you are not viewing are hidden, and so is every non-matching
item while a bank **search** is active. `giants_foundry.py` searches `"adam"` before withdrawing,
so for as long as that search is up `/bank` describes a bank containing only adamant items. The
vendored m8aq `storage/bank` reader has the same limitation from the other direction
(`containerLoaded` false once the interface closes). The `InventoryID.BANK` **ItemContainer**
that `BankMemoryProvider` subscribes to has neither problem: it is the true contents, every tab,
whatever the interface happens to be showing.

- **The capture is one subscriber and it is free.** `onItemContainerChanged` filtered to
  `InventoryID.BANK` fires on every open, deposit and withdraw. Two consequences worth knowing:
  the memory **self-heals during a banking trip** with no invalidation logic anywhere, and it
  records the user's own manual banking whenever the client is running -- `controller.py` does
  not have to be.
- **PLACEHOLDERS ARE THE TRAP, and the check is not obvious.** A bank placeholder sits in the
  container with quantity 0 under an id that **canonicalizes to a different id**
  (`itemManager.canonicalize(id) != id`), so without the check a placeholder left by an item you
  ran out of still answers "yes, you have one" -- and a routine would withdraw nothing, then try
  to teleport home on an empty slot. Taken verbatim from `BankSave.fromCurrentBank`, along with
  the `-1` / `BANK_FILLER` filter. Quantity 0 is also dropped outright.
- **`BankSnapshot` is a pure value with no client references** so the filtering rules are
  unit-testable without a client (`BankSnapshotTest`) -- the same extraction `RecordRing` got out
  of `RecorderProvider`, for the same reason.
- **"Never seen" is NOT "empty", and the distinction is load-bearing.** A bank we have never
  opened answers `seen=false, items=null`; a genuinely empty one answers `seen=true, items=[]`.
  `bank_memory.quantity()` returns **None** for the first and **0** for the second. Collapsing
  them lets a routine conclude it owns no teleport when it has simply never looked. Same rule the
  m8aq readers already draw.
- **Serving it touches neither the client thread nor `TickCache`** -- the snapshot is
  already-resolved plain data, so unlike `/bank` it is cheap to poll. Persisted to
  `~/.runelite/bridge-bank-memory/<accountHash>.json` (same convention as
  `ScreenshotProvider`'s `bridge-screenshots`), written on a daemon executor behind a dirty flag
  with an atomic tmp+move, never on the client thread: a bank trip fires the handler a dozen
  times in seconds and this user's bank is ~950 items (~60-80KB). `WRITE_DEBOUNCE_MS` (3000) is a
  FIRST-DRAFT GUESS wanting one live session to fit. A null account hash (logged out, or -1) is
  kept in memory and never written, so a file can never be persisted under the wrong key.
- **`unregister()` is called from `ControllerPlugin.shutDown`** -- most providers here never
  unregister, which is exactly the leak this file already records biting `TickClock` (every
  disable/enable cycle left another subscriber alive). This one owns a subscription AND a writer
  thread, so it is held in a field.
- **An UNREBUILT client answers `/bank/memory` with `{"open": false}`, not a 404** -- MEASURED
  live 2026-09-07 before the rebuild. `com.sun.net.httpserver` matches by LONGEST PREFIX, so with
  no `/bank/memory` context registered the request falls through to `/bank`'s handler and returns
  a 200 of the wrong shape. `/widget` + `/widget/children` were confirmed coexisting on the same
  live bridge, so once registered `/bank/memory` wins the match. The wrong-shaped 200 reads as
  "never seen" rather than "empty bank", and that is pinned by test.
- **Python: one fetch, many questions.** `bank_memory.load()` makes a single HTTP call and
  returns an object answering `has()`, `quantity()`, `find()`, `seen`, `age_s`; asking about
  twelve teleport names must not cost twelve round trips. `load(names=[...])` pushes the filter
  to the bridge for a narrow repeated poll, and `itemCount` deliberately stays the size of the
  WHOLE bank so a filtered reply can never be mistaken for a small one. Name matching is
  case-insensitive SUBSTRING, matching `banker.py`'s `keepItems` convention -- with the trap that
  `"teleport"` matches every teleport and `"rune"` matches runite bars.
- **Staleness policy lives with the caller**, deliberately: the provider only reports *when* it
  captured. `python bank_memory.py [name ...]` is the read-only live probe (imports
  `controller` only, never the serial port, so it is safe alongside a running routine).

**First live session's checklist** -- nothing below has been run against a real client, and #3 is
the headline claim:
1. `ItemContainerChanged` fires for BANK on open, and again on every withdraw.
2. The snapshot holds items from ALL tabs while a single numbered tab is displayed.
3. The snapshot survives an active bank **search** (search `"adam"`, confirm everything is still
   there) -- this is the whole correctness argument over `/bank`.
4. A placeholder for an item held 0 of does not appear.
5. A fresh client reloads the file and answers correctly BEFORE the first bank visit.
6. `client.getAccountHash()` is non-null and stable on this setup.

## Teleporting home from a bank trip (`ControllerAddon/teleports.py`)

Added 2026-09-07, sub-project 2 of the bank-memory work (USER: "make sure slayer is using this
to decide what method of teleportation is best to get back"). **Not yet run live.**

**The Shortest Path handshake is GONE and is not coming back.** USER asked whether we could still
handshake it for a teleport suggestion: no. `ShortestPathProvider.java` (399 lines, the
`PluginMessage` request/poll) was deleted in `1e96c8d`, and the plugin is no longer an enabled
Plugin Hub install (`~/.runelite/externalmanager/` has no shortest-path jar, though the jar
itself still sits in `~/.runelite/plugins/`). Re-adding it would also drag back the hazard this
file already records: its answers were governed by the user's own Shortest Path panel, which
once produced a route whose first edge was a `QUETZAL_WHISTLE` to a tile 454 tiles away.

**USER's alternative is the one taken and it is strictly better**: "we jhust skip the handshake
altogether and use the teleport data from the plugin itself". The plugin ships its transport
tables as plain TSV resources inside the jar, and `transports/teleportation_items.tsv` is
vendored byte-identical into `ControllerAddon/vendor/` (see `vendor/VENDORED.md`; the jar carries
**no LICENSE file**, same as m8aq). 340 rows of destination tile + OR'd item ids + duration +
wilderness level. **It keys on ITEM IDS and bank memory reports item ids, so the two join
directly.** This is also CLAUDE.md's own rule applied -- copy the plugin's SOURCE rather than
your reconstruction of it: `travel.RING_OF_DUELING_NAME`'s Ferox teleport and
`mahogany_homes.CITY_TELEPORTS` are both hand-rolled reconstructions of rows in this very file.

- **`teleports.py` is PURE** -- no HTTP, no client state, no execution. `choose()` takes the
  router as an argument, so the whole decision is testable without a map, a bridge or a client.
- **The header line is ASSERTED, not assumed.** Column order differs BETWEEN Shortest Path's own
  transport files (`teleportation_spells.tsv` puts Items second where `teleportation_items.tsv`
  puts it fourth), so a silent upstream re-order would mis-parse every row into plausible-looking
  nonsense rather than failing. Row-count assertions are a FLOOR (>= 300), like
  `M8aqProviderTest`'s, so re-vendoring a newer table does not fail the build.
- MEASURED over the table: **zero rows use `&&`** -- the Items column is purely OR alternatives,
  usually the charge variants of one item (ring of dueling (8) down to (1)). Matching only the
  full-charge id would miss a part-used ring, which is the common case. 20 of the 340 rows carry
  neither a destination nor an item (section markers) and are skipped.
- **Wilderness level is load-bearing here, not a corner case**: Krystilia's slayer tasks are IN
  the wilderness, and a teleport does not fire above its own level (283 rows are level 20, 35 are
  30), so the click would be spent for nothing. `choose` filters on the player's live
  `wildernessLevel`.
- ~~**Player-specific destinations are excluded**~~ -- WRONG, corrected below: varbits 2187/4744
  resolve exactly which house row applies, and the graph uses them.
- **Walking is the default and a teleport must EARN the trip**: `MIN_SAVING_TILES` (40) over
  `walk_len`, plus `TELEPORT_COST_TILES` (25) charged against the teleport for the withdraw,
  click, dialogue and animation. Both are FIRST-DRAFT GUESSES wanting a live session to fit. The
  one exception: when there is no walking route home at all, anything that lands within reach
  wins, since it beats a walk that cannot happen.

**Where it runs.** The choice happens INSIDE the bank (`_withdraw_return_teleport`, called from
both `bank_up` branches after `withdraw_loadout`) because an open bank is the only moment the
winning item can be withdrawn; `return_to_spot` then uses it and walks whatever is left. So
**every routine that opts into `returnToSpot` gets this**, not just slayer -- and the callers
that opted out (firemaking, blast_furnace, golem_crafting) are untouched.

- **A teleport that does not fire costs latency, never the trip.** `_use_return_teleport`
  returns False and the existing walk home runs exactly as it did before. That is the entire
  safety argument for executing teleports this codebase has never driven live.
- **The plan is popped INSIDE `_use_return_teleport`, not by its caller.** A first cut popped it
  in `return_to_spot` before the call, so the function read nothing and the teleport silently
  never happened -- and the fallback test could not catch it because it stubs the very function
  the bug was in. Pinned by `test_return_to_spot_actually_reaches_the_teleport_it_planned`,
  red-green verified. Popping exactly once is also what stops a dud being retried into an empty
  inventory.
- **The landing is CONFIRMED, never slept off** (`RETURN_TELEPORT_BUDGET_S` 6.0s, polled) --
  karambwan_fishing MEASURED that a teleport's own signals fire on the tick the LOADING screen
  starts, when the bridge answers nothing at all.
- **This is the FOURTH copy of the left-click-with-hover-gate teleport pattern** in the repo
  (`falconry._use_teleport`, `golem_crafting._teleport_home`, `karambwan_fishing._click_option`,
  now `banker._use_return_teleport`). Extracting one shared helper is the obvious follow-up; it
  was deliberately not done here rather than editing three working routines mid-task.


**CORRECTION, same day: the single-hop model above was WRONG, and the transport GRAPH replaced
it.** USER: *"that is not true, if you'd ask shortest path the answer would have been take house
tablet then use the ordnate jewlry box to ferox enclave"*. They were right. Four mistakes, and
the deep one is that **the data was copied and the ALGORITHM was not**:

- **Only 1 of ~8 teleport tables was vendored** (25 transport files exist in the jar, including a
  301KB `transports.tsv`). `teleportation_boxes.tsv` and `teleportation_portals_poh.tsv` are now
  vendored too.
- **`teleportation_items.tsv` has no `Origin` column; the box and POH-portal tables DO.** Those
  rows are graph EDGES, usable only from a particular tile. Shortest Path's answer comes from its
  pathfinder chaining edges -- a flat "teleport from anywhere to X" table structurally cannot
  express house tablet -> POH -> box.
- **The house rows were excluded as "player-specific"** on the belief a house could be anywhere.
  The table's own comment names the varbits that resolve it (**2187** house location, **4744**
  inside/outside) and `PathfinderConfig.varbitChecks` evaluates them with the same generic
  machinery that gates diary capes and quest teleports -- nothing about the house is
  special-cased. MEASURED live: 2187=3, 4744=0.
- **The box tiers were misread as "only Basic has Ferox".** They are cumulative.

**How Shortest Path solves POH teleports** (read from the decompile, not guessed):
- `PathfinderConfig.checkJewelleryBoxTier`: a Basic row is available to ANY tier above NONE,
  Fancy needs FANCY+, Ornate needs ORNATE. Ported verbatim into `teleports.poh_allows`.
- **An ornate box DISABLES the mounted Amulet of Glory** (`isMountedGlory && ORNATE -> false`),
  because the box supersedes it -- offering both plans a route through something you would never
  use. The kind of subtlety only copying the source gives you.
- **The client cannot see inside a POH, so Shortest Path ASKS**: `usePoh`, `usePohFairyRing`,
  `usePohSpiritTree`, `useTeleportationPortalsPoh`, `pohJewelleryBoxTier`, `usePohMountedItems`,
  `usePohObelisk`, plus a hidden `builtTeleportationPortalsPoh` (`ID=X Y Z;...`) it learns. Our
  `teleports.PohConfig` mirrors those names. USER's actual POH: ornate box, portals to Varrock /
  Falador / Ardougne / Lunar Isle, a fairy ring, and a mounted digsite pendant.
- **"Enter your house" is NOT modelled as a transport anywhere** (0 rows across every table). The
  only way into the POH node is the house tablet itself.

**Shape now.** `teleports.load_all()` parses all three tables keyed by HEADER NAME -- they do not
share a column order (items has no Origin, boxes has no Varbits, poh_portals has both), so a
fixed-index parser mis-reads two of the three. `usable()` = items (any-of) AND varbits AND POH
gates AND wilderness level. `plan(start, goal, owned_ids, route_len, ...)` is a bounded
breadth-first search over hops (`MAX_HOPS` 2) returning a list of legs, still PURE -- `route_len`
is an argument, so the whole search tests with no map, bridge or client.

- **An unknown varbit FAILS its check rather than passing it.** These gates pick exactly one row
  out of the house tablet's ten; answering "yes" on unknown would make every house location
  simultaneously true.
- **`unreachable()` is separate from `plan()` returning None.** None means "walk" -- either
  walking is better or nothing helps. At Ferox those demand opposite actions (walk vs. stop and
  say so), so the two questions are asked separately.
- **The POH interior routes as OPEN VOID on our static map** -- MEASURED:
  `find_path((1923,5709) -> (1870,5698))` answers 64 steps, because POH template coords carry no
  map data and "no data defaults to PASSABLE". The step count is fictional (the house is
  instanced and its layout varies per player) but the conclusion is right: you can walk from your
  arrival tile to the box. A test stub that forbade this was the test's bug, not the code's.

**VERIFIED LIVE 2026-09-07** against the running client -- real inventory, real varbits
(2187=3, 4744=0), POH config as the user described, goal Ferox Enclave, real `worldmap` router:

    walking to Ferox: None
    leg 1: [item] Teleport to House tablet     anywhere      -> (1923,5709,0)
    leg 2: [box]  3: Ferox Enclave             (1870,5698,0) -> (3151,3635,0)
                  via: Teleport Menu Basic Jewellery Box 37492

**Ferox Enclave is a sealed 413-tile island on our collision map** (component does not grow at
radius 40/80/150; nothing routes in or out, not even 35 tiles south). MEASURED 2026-09-07. That
is the deterministic reason a bank trip gets stuck there -- `banker`'s walk home has been
impossible since worldmap.py replaced Shortest Path on 2026-08-29, independent of the three
defects fixed earlier that day. Of 210 plane-0 destinations, **167 route normally, 30 are
enclosed, 13 sit on an unwalkable tile**; the enclosed ones are genuinely enclosed places
(Slime Pit, Tarn's Lair, the pyramids, Lithkren, the gem mine -- which karambwan_fishing uses
happily, because it teleports out rather than walking).

**NOT DONE, and each is a real gap:**
1. **`banker` still calls the old single-hop `choose()`.** The graph is not wired in, so slayer
   does not yet benefit. Multi-leg execution is the work.
2. **No execution for a box or portal leg.** The jewellery box opens a destination picker --
   exactly the shape CLAUDE.md records `_use_transport` could never drive. The route is free; the
   clicking is not.
3. **No config surface.** `PohConfig` defaults to everything off/NONE, so until a Controller
   Settings section (or a routine config block) exists, the graph will never plan a POH route in
   production.
4. **`plan()` costs ~19s / 87 `route_len` calls** on a real cross-map query. Fine once per bank
   trip, far too slow per tick. The fix is one flood from the goal (`reachable_from` returns
   distances) instead of N `find_path` calls.
5. **4744's polarity is UNVERIFIED.** With 2187=3 and 4744=0, two rows match: one landing at the
   POH interior (1923,5709) and one outside at (3340,3003). The search considers both and takes
   whichever routes; which one the game actually does is a live question.
6. Only items/boxes/POH portals are vendored. Fairy rings, spirit trees, minecarts, gliders,
   obelisks and the rest are still absent -- the user has a POH fairy ring that we cannot use.


**Wired into banker 2026-09-07 (USER: "go ahead to 1+2+3"), and VERIFIED LIVE against the
running client.** `_plan_return_teleport` now calls `teleports.plan`, stores every leg, and
`_use_return_teleport` runs them in order; a leg that does not land stops the chain and the walk
home recovers, exactly as the single-hop version did. POH contents come off the bridge as a
`poh` block (`ControllerConfig`'s new Player-Owned House section, mirroring Shortest Path's own
option names: `usePoh`, `pohJewelleryBoxTier`, `usePohPortals`, `usePohMountedItems`,
`usePohFairyRing`), defaulting to everything OFF so an unconfigured account never routes through
a house it may not have. Needs the client rebuild.

Four bugs found by running it live, three of them the SAME SHAPE -- an assumed payload shape
that a lying test stub then hid:

- **`/equipment` is a DICT KEYED BY SLOT, not a list.** Iterating it yields slot NAMES, so worn
  items contributed NOTHING to the owned set and a worn-name lookup raised on a `str`.
  `_item_rows` normalises both shapes. The tell was there earlier and missed: an equipment dump
  that printed empty while the account was wearing eleven items.
- **`controller.get_varbit` returns the VALUE, not a `{"value": n}` envelope.** The first cut
  checked `isinstance(reply, dict)`, so it read NOTHING, so every varbit-gated row was rejected,
  so the house-tablet chain was impossible. **The unit test passed because its stub returned a
  dict** -- encoding the same wrong assumption it existed to catch. Both stubs now match reality.
  General lesson, twice in one session (the other was the pop-order regression): **a stub that
  lies about a shape is worse than no stub.**
- **`test_banker.py` was not hermetic**: `_owned_item_ids` reads `bank_memory.load()`, which
  reaches the RUNNING CLIENT (~929 real items that change as the account is played), so planning
  outcomes drifted and three unrelated tests failed once and passed on a verbatim re-run. It has
  a `_no_live_bridge` autouse fixture now, like the other suites; the full run also got ~20%
  faster.
- **THE VOID IS ONE CONNECTED COMPONENT, and it made planning both slow and wrong.** "No data
  defaults to PASSABLE" means every dungeon, instance and POH template is joined into one giant
  walkable void, so `find_path` invents hundreds-of-tiles routes between unrelated underground
  coordinates. MEASURED: a single decision cost **106s**, and the invented routes outranked a
  ring of dueling that teleported straight home. Fixed by bounding an approach walk in BOTH
  senses -- `APPROACH_MAX_TILES` (60) rejects a long answer, `APPROACH_MAX_NODES` (20000) bounds
  the SEARCH so a void crawl gives up in milliseconds -- plus `GOAL_FLOOD_RADIUS`: ONE
  `reachable_from` flood from the anchor replaces a cross-map search per candidate, and its
  radius doubles as the rule "a landing further than this from home is not worth teleporting to".
  **106s -> 9.9s**, answers unchanged.

**VERIFIED LIVE 2026-09-07** through banker's own code path, real bank memory (913-929 banked
items), real varbits, POH set to the user's actual house: `-> Ferox Enclave: 9.9s Ring of
dueling: Ferox Enclave` and `-> Varrock W bank: 9.9s Varrock tablet`. With the bank in view the
planner correctly prefers a direct teleport over the house->box chain; with only the house tablet
owned it finds the chain (pinned by test). **`/bank/memory` is also confirmed working live**
(`seen=true, source=live, items=929`), which clears item 1 of the bank-memory checklist.

**Still NOT verified live: the actual clicking.** `_run_object_leg` and `_pick_destination` have
never run inside a POH -- the jewellery box's destination picker is answered by matching the
TSV's own destination name in the widget dump, and that assumption is untested. Every step
confirms and any failure falls back to walking, which is the whole safety argument, but treat a
POH leg as unproven until a live session says otherwise.

**First live session's checklist** -- none of this has touched a real client:
1. Does the winning teleport's item actually withdraw (`search_and_withdraw` by the TSV's item
   name, e.g. "Ring of dueling", against a bank listing "Ring of dueling(8)")?
2. Does the left-click hover gate pass on these items, or does everything fall to the menu?
3. For a multi-destination item, is the TSV's `destination_name` really the dialogue option text?
   (This is the assumption the whole "safe subset" rests on and it is UNVERIFIED.)
4. Fit `MIN_SAVING_TILES` / `TELEPORT_COST_TILES` against real trips -- watch for a tablet burned
   on a walk that was already short.
5. Confirm the wilderness filter fires on a Krystilia task above level 20.

## Clicking interface elements (`/widget`)

`WidgetProvider.java` + `controller.get_widget(group, child, index=None)` (added 2026-08-23) give a
clickable screen position for **any** interface element by group/child id. Before it, the only
reachable interface elements were the handful `BankProvider` hard-codes — and `/equipment` reports
what is *worn* but carries **no screen position**, so a routine physically could not click a worn
item. That blocked `routines/falconry.py`, which at the time had to un-wield a Dramen staff after
a fairy-ring hop because the falconry glove needs the same weapon slot (the staff requirement is
gone from the game and from the code since 2026-09-06; worn-item clicks through this endpoint are
now what `karambwan_fishing.py` uses for its two worn teleports).

The intended workflow is: **record yourself doing the thing, read the decoded `group.child` out of
`recorder.py`'s log, address it here.** That is why this is generic rather than more named buttons.

- Returned dict has `screenX`/`screenY` (the element's *centre*), so it goes straight into
  `click_entity`/`click_menu_action` — always with `check_ui_blockers=False`, same as every other
  interface click (see `travel.teleport_to_ferox` for the rotate-forever loop that flag causes).
- `found:false` (wrong id) is deliberately distinct from `clickable:false` (right id, interface
  closed). Both return HTTP 200 — a closed interface is a normal state for a polling caller.
- `GET /widget/children?group=G&child=C` lists populated dynamic children, for finding which index
  holds what without guessing.
- Keyboard shortcuts still beat clicking a tab: `E` opens worn equipment.

## Building a routine with a subagent-driven plan (the Gauntlet, 2026-09-05)

The Corrupted Gauntlet was built in one day as three spec -> plan -> subagent-executed
sub-projects (~85 commits), Haiku implementers, Sonnet per-task reviews, Fable whole-branch
final reviews (USER: "do fable final review"). What the process itself taught, so the next one
does not re-learn it:

- **Plan-authored tests were wrong more often than the code they tested.** Every one of these
  was a test asserting a plausible fact the spec's sources contradicted: OR-logic diagonals for
  walking (`e3eff2f`), 50 shards for a basic weapon (`9e1c9ff`), a flood-fill claim about an
  8x8 corner (`39da441`), a start-centre fixture that was not walled and so flooded the whole
  grid (`92d2872`), a hop test that passed with the walker doing nothing, and a bowl order the
  code never promised. **When a test and a MEASURED fact disagree, check the wiki/recording/cache
  before changing the code to satisfy the test**, and record the ruling in the plan's ledger.
- **Implementers deviate in patterned ways; the task review is what catches them.** Seen here:
  relaxing a geometry rule to make a test pass; "efficiency" cascades that run several sub-state
  transitions in one call (`3b0181f` restored one transition per call -- the cascade executed
  `bowl_make` before the player reached the bowl); adding a plausible precondition the spec never
  stated (the shard gate); an `IdentityHashMap` that kept every projectile alive for the process
  lifetime (`b19ea69`, Guava `MapMaker().weakKeys()` instead). Brief the reviewer on the spec's
  facts, not just the diff.
- **Never run two implementers in the same checkout, and commit before dispatching one.** Two
  agents share one git index; and an implementer told to "leave the tree clean" reverted an
  UNCOMMITTED edit of mine to the recorder ("restored to committed state"), which then had to be
  redone (`8b0a7cc`). Uncommitted work in the tree while an implementer runs is work at risk.
- **Final whole-branch reviews found what task reviews structurally cannot**: every Critical in
  the three final reviews was a cross-task interaction (the `mark`/`since` event loss between
  the tick wait and the read, `pick_safe_tile`'s stomp guard blocking traversal, `craft_plan`
  re-queueing done steps every tick, `before_break` teleporting a mid-run instance home). Budget
  a final review per sub-project and a fix wave after it; the fix wave itself introduced two
  bugs (`7f055e0`), so re-review the fix wave too.
- **Everything here was reasoned from a recording and the cache, none of it run live.** The
  first live session is the three specs' checklists in order, and the expected outcome is that
  geometry facts (room pitch, size-5 NPC centre-vs-corner) and widget fields are wrong before any
  logic is.

## Live-debugging workflow

Use this loop by default when debugging a routine live against the running RuneLite client — it
was confirmed to work well over an hours-long session that took `golem_crafting.py` from broken
to reliably running unattended.

1. **Check for a serial-port conflict first.** Confirm no stray process still holds the Arduino:
   `Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"`.
2. **Launch the routine in the background, unbuffered, logged to a file in `logs/`:**
   `python -u controller.py routines/<name>.py *> logs/<logfile> 2>&1`. Live-session logs
   (`mh_live*.log`, `recorder_run*.err`, ...) belong in `ControllerAddon/logs/` (gitignored,
   same place `debug_log.py` writes its own `debug_*.log`), never loose in `ControllerAddon/`
   — 159 of them were swept into `logs/` on 2026-08-31.
3. **Poll the log with `Read`, not tight loops.** 20-90s gaps between checks are fine — this is a
   live session, not a race.
4. **Trust the user's live observations over your own read of the log.** If the log looks clean
   to you but the user reports something wrong, keep digging for the actual mechanism rather than
   concluding it's already fixed.
5. **Before every relaunch: `py_compile` every touched file, then run the test suite.** Pytest
   alone is not sufficient — some modules (e.g. `banker.py`) aren't imported by any test, so a
   syntax error there won't surface in `pytest`, only in a direct compile check.
6. **After relaunching, actually watch it work through the specific phase that was broken.**
   Compiling clean and "the reasoning sounds right" are not verification.
7. **Iterate in small, targeted fixes**, one reported symptom at a time, rather than large
   rewrites.

**Distinguish infra/hardware failures from code bugs**: a `serial.SerialException` crash mid-run
can mean the Arduino has dropped off the USB bus entirely, not a code bug. Check
`[System.IO.Ports.SerialPort]::getportnames()` (empty = gone) and `Get-PnpDevice -Class Ports`
(look for "Status: Unknown" on the Arduino Leonardo) before assuming it's a logic bug. A
reseat/power-cycle fixes this — no amount of code changes will.

**Before any live click/mouse debugging**, verify: RuneLite window is focused (`GET /state` →
`windowFocused`), Windows mouse acceleration ("Enhance pointer precision") is off, and the
process is DPI-aware (already handled by `mcp_debug_server.py`/`controller.py` at import time).

