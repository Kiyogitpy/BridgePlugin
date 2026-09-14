# The OSRS VM — setup, tooling and measured traps

Moved out of CLAUDE.md on 2026-09-14. Nothing here is obsolete: this is the full measured record; the condensed working rules live in CLAUDE.md.

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

