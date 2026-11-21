# Installing remoteG90ctl

*In Polish: [install.pl.md](install.pl.md).*

## 0. One command

On the computer by the radio: Debian 12/13, Ubuntu 22.04 or later, Raspberry
Pi OS (64-bit) or DietPi, on an x86_64 or aarch64 processor (not 32-bit ARM).

```sh
curl -fsSL https://raw.githubusercontent.com/SP2EMW/remoteG90ctl/main/install.sh | sudo bash
```

The installer (`install.sh` in the repository root) prepares the system and
installs the program as systemd services — the **newest release** (a tag
`vX.Y.Z`), not the development branch `main`. A release with a plain number
has passed the hardware checklist on a real G90; while there is none, the
installer takes the newest candidate (`vX.Y.ZrcN`) and says so. On a new
computer it **does not start the services** — the hardware setup with its
checklist comes first (section 5). On a new computer it then runs the setup
wizard, **`sudo remoteg90ctl setup`** (you can also run it yourself, e.g.
after changing sound cards; D56). The first question is the language
(English / Polski, Enter = the system's language); to skip a checklist item,
type `SKIP`. The wizard:
1. finds the DE-19's port, the DE-19's sound card (the one on the same USB hub
   as the port) and the I/Q card, gives the cards fixed names (udev rules from
   the identifiers it found), switches the I/Q card off in PipeWire on a
   computer with a desktop, and writes the hardware configuration — with
   **transmitting off** when the port is new;
2. **on the first installation on that port** walks you through the hardware
   checklist (VOX, the DE-19's cable in the IO/User socket, SWR THR, plugging
   in USB without transmitting, starting the guard, the radio's identity, CAT,
   a 2 s PTT test and an interrupted test — those two into a dummy load or
   into the antenna at 1 W in SSB, in a band shared by all ITU regions, e.g.
   7.074 or 14.074 MHz); an item can be skipped by typing `SKIP` — the page
   then shows a permanent warning; it offers to move a TX log that is outside
   the guard's state directory;
3. asks for the maximum power for the browser, antenna tuning and **whether to
   enable transmitting**;
4. **access to the page:** on this computer `http://127.0.0.1:8078`; from a
   phone and other computers through Tailscale (HTTPS) — on "y" it logs the
   computer in to Tailscale (`tailscale up`, a link to open), adds its tailnet
   name to `server.allowed_hosts` and runs `tailscale serve`; it prints the
   address `https://<name>/`;
5. finally asks whether to start the services (starting the guard = a blink
   of transmission; radio in SSB).

What the installer does is described in section 8.2. Run again, it updates
the program and restarts the services. To move the state from another
computer (a copy made by `deploy/backup.sh`, section 8.4): `sudo ./install.sh
--restore FILE.tgz` from the directory with the code.

The installer works in two stages: the one fetched with `curl` picks a
release, downloads it and hands over to the `install.sh` of that release — so
the installer always matches the program it installs. A release whose
`install.sh` cannot take over (an older one) is refused before anything
changes.

A specific release: `REMOTEG90CTL_RELEASE=v0.5.0` (also a candidate newer
than the newest release without a suffix). A branch instead of releases:
`REMOTEG90CTL_BRANCH=main` — development code, not checked on the radio; an
installation that follows a branch keeps following it on later runs.

**Installation log:** `~/remoteg90ctl-install.log` (your account, mode
0600) — everything shown on the screen and the whole output of every long
step; each run adds a part with the date. A token in the repository address
never gets into it. Send this file when something goes wrong. The uninstaller
keeps it.

**Removing everything** (e.g. before another installation test):
```sh
sudo ~/remoteG90ctl/deploy/backup.sh      # a copy, if the data is needed
sudo ~/remoteG90ctl/uninstall.sh
```
It removes the program, the configuration, the TX log and the transmit
lockout, the accounts, the station log and the recordings, the udev rules and
the wizard's WirePlumber rule, `tailscale serve`. It undoes what the installer
changed — following its record in `/var/lib/remoteg90ctl-installer/changes`,
and only that: the units it masked, DietPi's clock and swap settings, the
packages it installed (systemd-timesyncd comes back in chrony's place), uv and
Tailscale (logged out; also delete the computer on the tailnet's admin page),
the cloned code. It asks once (type `yes`); `--yes` skips the question. An
installation from before `0.9.0rc2` has no record: `--all` undoes every change
the installer can make and only lists the packages.

The rest of this guide describes the manual steps the installer does by
itself, and the first start. **Always start in simulator mode.** Move to the
radio only after the hardware checklist (section 5).

**Short version** for installing from a local copy of the code (sections 1
and 2; safe to run repeatedly, never overwrites an existing configuration):

```sh
sudo deploy/install.sh --start      # install/update from the local repository
sudo deploy/services.sh --stop      # stop the services and disable autostart (--start, --status)
sudo deploy/uninstall.sh            # undo; keeps the configuration, the TX log and the lockout state
sudo deploy/uninstall.sh --purge    # undo, together with the data (also the accounts and the station log) and the remoteg90ctl user
```

## 1. User, directories, program

```sh
# The service user; members of the remoteg90ctl group may use the CLI.
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin remoteg90ctl
sudo usermod -aG remoteg90ctl "$USER"        # log out and back in

# The program in /opt/remoteg90ctl, its environment in /opt/remoteg90ctl/venv
sudo git clone <repository-address> /opt/remoteg90ctl
cd /opt/remoteg90ctl
sudo UV_PROJECT_ENVIRONMENT=/opt/remoteg90ctl/venv uv sync --no-dev --frozen

# Configuration
sudo install -d -m 0750 -o root -g remoteg90ctl /etc/remoteg90ctl
sudo install -m 0640 -o root -g remoteg90ctl config/example.toml /etc/remoteg90ctl/config.toml
```

The directories `/run/remoteg90ctl` (sockets) and `/var/lib/remoteg90ctl`
(the durable TX lockout, the duty-cycle history and the TX log) are created
by systemd from the guard's unit file. Until 09.11.2026 the TX log was in
`/var/log/remoteg90ctl/tx.jsonl` by default; an older configuration still
works, but `install.sh` refuses if that directory is in RAM (tmpfs) — then
move the file and the path as in section 8.2.

**Clock:** FT8/FT4 needs a clock accurate to better than about 0.5 s, and
the time of the received audio comes from the server's clock. Without time
synchronisation a laptop's clock drifted by about 2.3 s (11.11.2026): every
decode had DT ≈ 2.3 s and weak signals stopped decoding. Install and enable
synchronisation (`sudo apt install chrony` or systemd-timesyncd) and check
`timedatectl` (`System clock synchronized: yes`) and `chronyc tracking`
(`System time` in milliseconds). **chrony is recommended:** the server reads
the clock's actual error from it and refuses to transmit FT8/FT4 above 0.2 s
(D53); with systemd-timesyncd it knows only the "synchronised" flag. On the
Futro S740 — section 8.1 step 2.

The CLI for yourself:

```sh
sudo ln -s /opt/remoteg90ctl/venv/bin/remoteg90ctl /usr/local/bin/remoteg90ctl
```

## 2. Services

```sh
sudo cp deploy/systemd/remoteg90ctl-guard.service deploy/systemd/remoteg90ctl-server.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now remoteg90ctl-guard remoteg90ctl-server
systemctl status remoteg90ctl-guard remoteg90ctl-server
```

- The guard has `Type=notify` and `WatchdogSec=5`: if its safety loop hangs,
  systemd kills it and the kernel lowers DTR/RTS (HUPCL).
- The guard runs without network access (`PrivateNetwork=yes`); the server
  can reach only the loopback (`IPAddressDeny=any`, `IPAddressAllow=localhost`).
- Logs: `journalctl -u remoteg90ctl-guard -u remoteg90ctl-server`.
  The transmission log: `/var/lib/remoteg90ctl/tx.jsonl` (`guard.tx_log_path`).
- **Order: the radio first, then the services.** Reports on the internet say
  that CI-V traffic while the G90 boots can hang it; not measured here
  ([measurements](g90-measurements.md), item 15). The server polls the radio
  all the time (power every 1 s, the VFO every 0.5 s while a browser is
  open). Until it is measured:
  1. switch the radio on (in SSB, D23) and wait until it has finished booting;
  2. only then start the services: `sudo systemctl start remoteg90ctl-guard remoteg90ctl-server`.

  If the radio has to be switched off and on, stop the services first
  (`sudo systemctl stop remoteg90ctl-server remoteg90ctl-guard`).

## 3. Checking in simulator mode

With `radio.backend = "simulator"` (the value in the example) nothing
touches the radio.

```sh
remoteg90ctl status
remoteg90ctl freq 14074000
remoteg90ctl freq
remoteg90ctl mode USB
remoteg90ctl tx-test --seconds 3       # a simulated transmission
sudo tail -n 5 /var/lib/remoteg90ctl/tx.jsonl
```

Also check the failure paths: interrupt `tx-test --seconds 30` with Ctrl+C,
then run `sudo systemctl restart remoteg90ctl-server` during a `tx-test`.
In both cases the TX log must show the end of the transmission.

## 4. Diagnostics

A detailed trace is switched on in the configuration and by restarting the
services:

```toml
[logging]
level = "DEBUG"
```

```sh
sudo systemctl restart remoteg90ctl-guard remoteg90ctl-server
journalctl -u remoteg90ctl-guard -u remoteg90ctl-server -f
```

What you see at DEBUG level:

| Entry | Meaning |
|---|---|
| `civ_tx frame=...` | a frame the guard wrote to the port |
| `civ_rx frame=... kind=reply` | the radio's reply (passed to the server) |
| `civ_rx ... kind=echo` | the echo of our own frame on the CI-V bus |
| `civ_rx ... kind=broadcast` | an unsolicited message from the radio (transceive) |
| `civ_rx ... kind=other_device` | a frame from another address — e.g. a wrong `radio_address`; also an echo that lost a byte (seen on the G90 with the DE-19 with 11-byte frames) |
| `civ_rx_garbage` (WARNING) | bytes that are not a frame — often a wrong port speed |
| `lines reason=... set_* read_*` | what the guard set on DTR/RTS and what it read back |
| `port_open_step` | the steps of opening the port safely |
| `request` / `response` | the server's requests to the guard and the result |
| `guard_request` / `guard_response elapsed_ms=` | the same on the server's side, with timing |
| `cat_reply elapsed_ms=` / `cat_timeout` | the radio's reply time, or no reply |

Reading the usual symptoms:
- **`cat_timeout` and no `civ_rx` at all** — the radio is silent: wrong port,
  wrong speed, CI-V off in the menu, or the wrong socket.
- **`civ_rx_garbage`** — bytes arrive but do not form frames: most often a
  wrong speed.
- **`civ_rx ... kind=other_device`** — the radio answers from an address other
  than `radio.radio_address`; the address in the frame (the fourth byte) is
  the radio's.
- **No `kind=echo`** — the interface does not echo; that is normal and does
  no harm.

The CLI shows the raw messages with `-v`:

```sh
remoteg90ctl -v freq
```

While transmitting, DEBUG writes several entries a second (lease renewals).
Go back to `level = "INFO"` after diagnosing.

## 5. Hardware checklist — before connecting for the first time

The wizard `sudo remoteg90ctl setup` (section 0) walks you through this list
itself; each item is described below. What is known about the G90 and the
measurements: [g90-measurements.md](g90-measurements.md) — "item" in the text
is the item number in that table. Do everything below **with the radio on a
dummy load or at low power**. The results of the first pass through the list
(02.11.2026: a G90 with firmware 1.81, a DE-19, a laptop with Ubuntu) are
given with the items.

Before you connect cables to the radio:

- **Laptop: block suspend**, also when the lid is closed. A suspended computer
  does not check the lease, and the state of the DE-19's lines during suspend
  is not known:
  ```sh
  sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
  ```
  (to undo: the same with `unmask`).
- **Stop ModemManager before plugging in the DE-19 for the first time**
  (`sudo systemctl stop ModemManager`) — it probes new serial ports, which
  means opening them. A udev rule from step 4 keeps it away from the DE-19 for
  good.

- **VOX off** in the radio's menu (FUNC + VOL → VOX: OFF according to
  Radioddity; item 12). With VOX on, every sound from the sound card turns
  into a transmission, bypassing the guard, the lease and the 180 s limit. The
  program cannot check this.
- **The right sockets** (item 7b): the DE-19 connected as its manual shows —
  the jack cable from the IO/User socket on the **front panel** to the DE-19's
  CI-V socket, and the multi-pin DIN cable from the radio's rear ACC socket to
  the DE-19's ACC socket; no separate CAT cable is needed. The jack cable does
  not go into the rear COMM socket. The G90's manual (p. 58) warns that
  switching the radio on with a plain USB-serial cable in COMM can put it into
  firmware update mode and leave it unusable until firmware is loaded again.

1. **Firmware** of the main unit and the panel — write the versions down
   (item 10).
2. **Protection on the radio's side** (items 11, 11a). According to the G90's
   manual there is no transmit time-out (TOT) — look through the menu and
   confirm. Read the **SWR THR** threshold (3:1 by default) and keep it low:
   above it the radio reduces power by itself. **Screen blanking:** switch it
   off in the menu if you can — with the screen blanked the G90 keeps a tuning
   until the screen is switched on, and then transmits the carrier without
   the program knowing (item 13).
3. **The USB adapter in the DE-19** — chip type and stable name (items 6, 8b):
   ```sh
   lsusb
   ls -l /dev/serial/by-id/
   ```
   **Result (02.11.2026):** the DE-19 has a USB hub inside (Genesys
   `05e3:0608`), a **CH340** chip (`1a86:7523`, no serial number) and a C-Media
   sound card (`0d8c:0012`, card no. 1). Port:
   `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`.

   Put the path from `/dev/serial/by-id/` into `radio.port`. If the device is
   `ttyUSB*` or `ttyACM*`, the unit file already allows it; for anything else,
   change `DeviceAllow=` in `remoteg90ctl-guard.service`.

   **Is the name unique?** A name in `by-id` is stable only when the adapter
   has its own serial number. CH340/CH341 chips usually do not — then every
   such adapter gets the same name, and a second one plugged in can take it
   over. Check:
   ```sh
   udevadm info /dev/serial/by-id/... | grep -E 'ID_SERIAL=|ID_SERIAL_SHORT='
   ```
   A missing `ID_SERIAL_SHORT`, or a value that looks generic (e.g. all
   zeros), means the name is not unique. Then do not connect a second adapter
   of that type. The guard checks the radio's identity anyway (step 7), so it
   will not key a transmission on a foreign device. Another way of naming the
   port (`by-path`, or your own link from a udev rule) would need a change in
   the configuration's validation (`AGENTS.md` rule 8) — if you need it, open
   an issue.
4. **Nothing else opens the port.** The kernel raises DTR/RTS on every open
   of the device — by any program, which can key the radio. The guard blocks
   other opens (`TIOCEXCL`), but the kernel exempts root from that, and
   system programs run as root. The guard watches the lines on every pass of
   its loop and lowers them at once (state `line_fault`), but it is better if
   this never happens:
   - **ModemManager** probes new serial ports. Switch that off for the DE-19
     with a udev rule (read the `ID_SERIAL` value with
     `udevadm info /dev/serial/by-id/...`; for the DE-19 with a CH340 it is
     `1a86_USB_Serial` — the rule then covers every CH340, which does no
     harm):
     ```
     # /etc/udev/rules.d/90-remoteg90ctl.rules
     SUBSYSTEM=="tty", ENV{ID_SERIAL}=="1a86_USB_Serial", ENV{ID_MM_DEVICE_IGNORE}="1"
     ```
     ```sh
     sudo udevadm control --reload && sudo udevadm trigger
     udevadm info /dev/serial/by-id/... | grep ID_MM_DEVICE_IGNORE   # must be =1
     ```
   - **brltty** (Ubuntu) takes over a CH340 when the DE-19 is connected
     through a Terminus `1a40:0101` hub (a common cheap hub; that is how the
     rule in Ubuntu's package is written). **Seen on 02.11.2026:** the
     `ttyUSB0` port disappeared right after plugging in, and brltty sent and
     received bytes on the CI-V line, bypassing the guard. The symptom in
     `journalctl -k`: `interface 0 claimed by ch341 while 'brltty' sets
     config`. If you do not use a braille display:
     ```sh
     sudo systemctl stop brltty-udev.service
     sudo systemctl mask brltty-udev.service brltty.service   # or: sudo apt remove brltty
     ```
     Then unplug the DE-19's cable and plug it in again.
   - Do not run `minicom`, `screen` or `cat` on the radio's port.
5. **The PTT lines without the program** (items 8, 8a) — with the radio on a
   dummy load and **the guard stopped**
   (`sudo systemctl stop remoteg90ctl-server remoteg90ctl-guard`), check
   whether the radio goes into transmit when the computer boots, when the
   DE-19's USB cable is plugged in, unplugged and plugged in again. If it does
   — **do not go on**; a hardware solution or a udev rule is needed — report
   it. **Result (02.11.2026):** no keying when plugging in the cable or when
   the computer boots (services disabled).
6. **The pulse when the port is opened** (item 8). The guard sets DTR and RTS
   inactive before `open()`, lowers them right after the open and confirms
   that by reading them back. The kernel may still raise the lines for a
   fraction of a second inside `open()`, before the program can react —
   **software cannot rule this out**. Check it: set `backend = "hardware"`
   with `allow_transmit = false`, start the guard a few times
   (`sudo systemctl restart remoteg90ctl-guard`) and watch the radio (TX LED,
   power meter). If the radio blinks into transmit even once — stop and report
   it.

   **Result (02.11.2026): the radio blinks into transmit every time the guard
   starts** (a fraction of a second). The kernel raises RTS in `open()`, the
   guard lowers it about 1 ms later, but that is enough for the G90 to switch
   to transmit. A risk the project accepts ([decisions.md](decisions.md),
   D23). The rule: **start the guard and run `port-open` with the radio in SSB
   (USB/LSB), on a dummy load or the antenna** — in SSB without modulation such
   a blink sends practically nothing to the antenna; in CW, AM and FM it would
   send a carrier. The blink happens only when the port is opened: when the
   guard starts or restarts (also after a crash) and on `port-open`. In normal
   operation the port stays open.
7. **CI-V address and speed** (items 7, 7a) — read them from the radio's menu
   and put them into `radio.radio_address` and `radio.baudrate`. Sources give
   different addresses (0x88 and 0x70; according to Radioddity the default is
   0x70 and the radio also answers others), so there is no default. Details:
   `docs/civ-g90.md`. If you cannot find the address in the menu, find it by
   reading the frequency (`remoteg90ctl freq` with `allow_transmit = false`):
   the reply comes from the radio's address. **Result (02.11.2026):** 19200
   baud, address **0x70**.

   **The radio's identity** (`docs/decisions.md` D32). After every port open
   the guard asks the radio for its identifier (`1D 19`) and, unless it
   matches `radio.identity`, blocks transmitting (CAT works). On the first
   start remove `identity` from the configuration or leave the value from the
   example, start the guard and read the radio's reply:
   ```sh
   remoteg90ctl status      # the "radio identity" line, the field with the radio's reply
   ```
   Put it into `radio.identity` if there is certainly a G90 on the other end.
   **Result (02.11.2026, firmware 1.81):** reply `1D 19 00 90`, i.e.
   `radio.identity = "190090"` (model G90, 0x0090).
8. **CAT without transmitting** — with `allow_transmit = false`:
   `remoteg90ctl status` (port `open`, lines confirmed), `freq`, `mode`. At
   that time the guard passed only four CI-V commands (reading and setting
   frequency and mode). **Result (02.11.2026):** everything works (setting the
   mode after a fix: the G90 needs the filter byte). The U-D/L-D data mode is
   not visible through that read — `remoteg90ctl mode` then shows USB/LSB
   (`docs/civ-g90.md`). A band change switches the filter relays in the radio
   — you hear a click; that is not a transmission.
9. **Which line keys PTT** (item 4) — `radio.ptt_line = "rts"` or `"dtr"`.
   Find out by measurement (DE-19 documentation, a meter), not by trial on
   the antenna. Sources on the DE-19 do not name the line (they recommend PTT
   over CAT and both lines low). **Result (02.11.2026, `tx-test --seconds 2`
   into a dummy load): RTS keys, DTR does not** — `ptt_line = "rts"`.
10. **The first transmission** — only into a dummy load, at low power,
   watching the I/Q card's input level (item 3):
   ```toml
   [safety]
   allow_transmit = true
   ```
   ```sh
   sudo systemctl restart remoteg90ctl-guard remoteg90ctl-server
   remoteg90ctl tx-test --seconds 2 --dummy-load-connected
   ```
   Check that the radio goes back to receive: after the end of the test,
   after Ctrl+C, after `sudo systemctl stop remoteg90ctl-server` during the
   test, after `sudo systemctl kill -s SIGKILL remoteg90ctl-guard` during the
   test (the last leaves a TX lockout — clear it with `remoteg90ctl
   tx-unlock`). **Result (02.11.2026):** every case releases PTT, also a
   frozen client (`kill -STOP` on the CLI process — released after 2 s) and
   `systemctl kill -s SIGKILL` on the server. After the guard was killed the
   radio went back to receive at once (HUPCL), and the guard started with the
   lockout. The I/Q output is about 16 dB quieter while transmitting; there is
   a click when switching TX↔RX (peak −6 dBFS, the card does not clip).

   After the tests switch transmitting off again (`allow_transmit = false`)
   and switch it on knowingly when you need it.

## 6. The I/Q card (stage 2)

The waterfall reads the radio's I/Q output from a separate sound card (card
no. 2). Only the server opens it, directly through ALSA (`docs/decisions.md`
D35). The system's PortAudio is needed (`install.sh` checks it before
installing):

```sh
sudo apt install libportaudio2
```

1. **A fixed name for the card.** A udev rule gives the I/Q card the ALSA name
   `G90IQ` (card numbers change between boots — rule 8). The rule has the
   identifiers of the card used here (C-Media, `0d8c:8810`; Linux names it
   "Speed Dragon"); for another card read them from `lsusb` and change the
   rule. **Do not match the DE-19's card** (`0d8c:0012`).
   ```sh
   sudo cp deploy/udev/91-remoteg90ctl-iq.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   ```
   Unplug the I/Q card and plug it in again, then check: `cat
   /proc/asound/cards` must show `[G90IQ ]` next to the card.
2. **PipeWire without that card.** On a computer with a desktop PipeWire
   takes every card. Switch the I/Q card off in it (WirePlumber 0.4 — a `.lua`
   file, 0.5 — a `.conf` file; `wireplumber --version` shows which):
   ```sh
   mkdir -p ~/.config/wireplumber/main.lua.d
   cp deploy/wireplumber/51-remoteg90ctl-iq.lua ~/.config/wireplumber/main.lua.d/
   systemctl --user restart wireplumber
   ```
   Check: `pactl list cards short` no longer shows the I/Q card.
3. **Configuration** (`[iq]`, described in `config/example.toml`):
   ```toml
   [iq]
   source = "alsa"
   device = "hw:CARD=G90IQ,DEV=0"
   sample_rate = 96000     # the G90's I/Q output reaches about ±40 kHz (measurements, item 1)
   sample_bits = 24
   swap_channels = false
   invert_q = true          # measured with a ground-loop isolator (measurements, item 2)
   dc_removal = true
   amplitude_balance = 1.0
   phase_correction_deg = 0.0
   fft_size = 8192
   line_rate = 20
   recordings_dir = "/var/lib/remoteg90ctl-server/recordings"
   ```
   Then `sudo systemctl restart remoteg90ctl-server` (the guard need not be
   restarted — the radio will not blink).
4. **Checks and measurements** ([measurements](g90-measurements.md), items
   1–2c and 9):
   ```sh
   remoteg90ctl iq status                    # running? level, overruns
   remoteg90ctl iq record --seconds 5        # a WAV file on the server; prints its path
   remoteg90ctl iq analyze FILE.wav [--signal-hz +5000]
   ```
   `iq analyze` works without the server: it shows the level and the DC
   component of both channels, their balance, the noise floor, the strongest
   signals (offset from the I/Q centre) and — for a known signal — the image
   rejection. The sign of the offset tells whether the channel mapping is
   right: a signal above the CAT frequency must come out with a plus.

5. **The waterfall in the browser** (stage 2). In `[server]`:
   ```toml
   listen = ["127.0.0.1:8078"]
   ```
   After `sudo systemctl restart remoteg90ctl-server` open
   **http://127.0.0.1:8078** (or `http://localhost:8078`) on the same
   computer. The server listens only on the loopback (rule 15) and accepts
   only its own pages (`docs/decisions.md` D34).

   **Accounts** (`docs/decisions.md` D48). The first time a fresh installation's
   page is opened, the **administrator account** window (the first account)
   opens at once: the call sign (also the login), the QTH locator of the
   remote station and a password (at least 10 characters). Only the
   administrator adds and removes accounts and restarts the server from the
   page (the resources window). One account uses the station at a time; while
   another works, the administrator's page shows who it is and can close its
   page and log it out (taking the station over). The menu under the call sign in
   the top right corner: change password, "Add account", "Log out". Without logging in the page shows nothing and allows
   nothing. One account can be logged in at a time (the same one on several
   devices — yes). A session lasts 30 days. Accounts are in
   `/var/lib/remoteg90ctl-server/accounts.json` (passwords only as hashes).
   An account also remembers settings: the radio's (VFO, frequency, mode,
   split, PRE/ATT, AGC, NB, RF gain, ATU, power — restored on the radio after
   logging in) and the page's (waterfall, filter, audio, band memory and so
   on). A forgotten password or an account no longer needed — in a terminal
   on the server:
   ```sh
   remoteg90ctl user list
   remoteg90ctl user passwd SP1ABC     # asks for the new password; the account's sessions expire
   remoteg90ctl user delete SP2XYZ     # not for the administrator
   ```

   **From a phone through Tailscale** (`docs/decisions.md` D38). The server
   stays on the loopback; `tailscale serve` publishes the page over HTTPS, only
   in your tailnet:
   ```toml
   [server]
   allowed_hosts = ["<computer>.<your-tailnet>.ts.net"]   # the computer's name in the tailnet
   ```
   ```sh
   sudo systemctl restart remoteg90ctl-server
   sudo tailscale serve --bg 8078          # to undo: sudo tailscale serve reset
   ```
   On the phone: **https://<computer>.<your-tailnet>.ts.net** — after logging
   in to an account (above). **Never `tailscale funnel`:** it would put the
   page on the whole internet. The server refuses such requests (no Tailscale
   identity, D57), and the setup warns when Funnel is on.

   Using it: the mouse wheel zooms around the cursor; dragging pans; on a
   touch screen drag with one finger and spread with two. **A click or tap**
   on the spectrum or waterfall tunes the radio to that frequency, rounded to
   the step in the "step" field (auto: 10 Hz in CW and CW-R, 100 Hz in other
   modes; the choice is remembered in the browser). Type a frequency into the
   "MHz" field (e.g. `21.074`) and press Enter; "mode" switches the radio's
   mode. **Seek:** "◀ seek" and "seek ▶" next to the frequency (keys `<` and
   `>`, also `,` and `.` without Shift, or Alt + PgDn / PgUp) tune to the next
   station down or up, found in the waterfall's spectrum (the highest level of
   the last 3 s, at least 10 dB over the noise and 200 Hz wide), placed in the
   receive passband of the mode. With none in view the radio moves 40 kHz and
   looks again, also outside the amateur bands, until 0.5 or 30 MHz; pressing
   again, tuning elsewhere or starting FT8/FT4 reception stops it. Seek is off
   during FT8/FT4 reception. Receiving only: transmitting is still refused
   outside the bands.
   Tuning during a transmission is refused (a message next to the
   fields). The scale follows the VFO (read every 0.5 s, also after turning
   the knob). The red line is the frequency from the radio. An orange "scale
   not calibrated" badge — there is no entry in `[iq.center_offset_hz]` for
   the current mode. The light band at the VFO is the receive passband of the
   current mode from `[iq.passband_hz]`. The G90 neither reports nor accepts
   its filter over CAT (`docs/decisions.md` D37). The "filter from / to"
   sliders in the footer move the band on the screen so that it matches the
   radio's filter (remembered in the browser, per mode; "default" goes back to
   the configuration's value).

   **Power and transmitting** (`docs/decisions.md` D33): every second the
   server reads the radio's power meter (CI-V `15 11`), also with no page
   open. The header shows the power — in watts when `radio.power_meter_watts`
   is calibrated, otherwise the raw meter value ("PO 370"). When the radio
   puts out power, a red "TX" appears and the waterfall lines are dimmed (also
   for 0.5 s after the end, because the I/Q output clicks). "TX" goes out 3 s
   after the last power reading, so that pauses in speech do not split a
   transmission. **A red bar "Radio transmitting without a lease"** means
   power without a transmission from the program — PTT on the microphone,
   TUNE or VOX; the server then logs `tx_without_lease`. It is information
   only, not a safety measure. PTT alone in SSB without sound puts out no
   power, so it is not visible.
   The header shows the **power set on the radio** (in watts). The G90's power
   meter ends its scale at about 3 W, so it serves only to detect
   transmissions. **An orange "High SWR" bar** appears when the radio's SWR
   meter sits at the end of its scale for two reads during a transmission —
   an SWR of about 2 or more (the end of the scale may come as early as about
   1.7). It stays until the next transmission with a lower SWR. The page does
   not show an SWR scale, because above about 1.5 it is unreliable; below
   that the reading is only approximate too (the scale is non-linear and was
   checked against the radio's display only, on one antenna).

   **Antenna tuner** (`docs/decisions.md` D43): in the "Radio" box an **ATU**
   switch (on/off) and a **Tune** button. Tuning **transmits a carrier** for
   about 1–1.5 s (longer with a poorly matched antenna) on the current frequency and cannot be stopped, so the page
   asks for confirmation. The guard allows it only with
   `safety.allow_atu_tune = true` (separate from `allow_transmit`) and without
   a lockout; for the guard a tuning lasts until the power meter reads zero
   (at most 30 s, longer locks transmitting until `tx-unlock`), the next one
   10 s after the end of the previous one; it records it in the TX log. The
   tuning power does not follow the power setting (measured: above 2 W with
   1 W set). From the command line: `remoteg90ctl atu tune`.
   **The program does not confirm that the tuner found a match:** the radio
   does not report the result of a tuning (13 November 2026: SWR above 3.5
   after a tuning, with nothing on the display), and the "High SWR" bar appears
   only on the next transmission, and only at an SWR of about 2. For remote
   operation use a good, proven antenna that the G90's tuner matches easily
   on the bands you use, and check it on site with an external SWR meter.
   **With the screen blanked the G90 does not tune** — it carries the tuning
   out only when the screen is switched on, without the program knowing. Do
   not tune remotely when the screen may have gone dark (best: switch the
   blanking off in the radio's menu).
   A spectrum symmetric around the VFO, seen while speaking into the
   microphone, is not a radio signal but interference from the power or audio
   path ([measurements](g90-measurements.md), item 3).

6. **Listening in the browser** (stage 3). The DE-19's sound card gets the
   fixed name `G90AUDIO`:
   ```sh
   sudo cp deploy/udev/92-remoteg90ctl-audio.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   ```
   (then unplug and plug in the DE-19); in the configuration an `[audio]`
   section as in `config/example.toml`, and in `[radio]`
   `ssb_data_mode = true` — the G90 sends audio to the DE-19 only in U-D/L-D.
   The system's Opus library is needed (`sudo apt install libopus0`, usually
   already there) and `amixer` (package `alsa-utils`). On the page, the
   "Listen" box: **Listen**, quality (Opus 16–48 kbit/s, or "raw" on the same
   computer), volume. One browser at a time can listen. The browser needs
   WebCodecs (Chrome, Edge, Firefox, recent Safari).

7. **Transmitting voice from the browser** (stage 4). The radio in **U-D/L-D**
   (`radio.ssb_data_mode = true`; choose USB/LSB on the page — also when
   switching VFOs and in split, `docs/civ-g90.md`, the note at the end) and
   `safety.allow_transmit = true`. On the page, the "Transmit" box: the
   **PTT (hold)** button — it transmits only while you hold it. On the first
   press in a session the page asks you to confirm transmitting into the
   antenna; after confirming, press PTT again. The browser will ask for
   access to the microphone. The server limits the level
   (`audio.tx_peak_dbfs`). VOX on the radio must be off (`AGENTS.md`
   section 3).

## 7. When something goes wrong

- **TX blocked** — `remoteg90ctl status` shows the reason (`lockout`,
  `TX blocked by`). A lockout after the hard limit or a crash is cleared with
  `remoteg90ctl tx-unlock`.
- **`radio identity` other than `confirmed`** — transmitting is blocked, CAT
  works. `not_configured`: put the radio's reply into `radio.identity`;
  `mismatch`: another device or radio answers on the port (check that no
  second CH340 adapter is connected); `rejected`: the radio does not know the
  request (firmware older than 1.8?); `no_reply`: no answer — most often the
  radio was still off when the guard started (08.11.2026): switch the radio
  on and restart the guard (radio in SSB); if that does not help — check the
  address, the speed and the cable. After a fix: restart the guard
  (`remoteg90ctl port-open` with the port already open does not check the
  identity again).
- **`duty cycle` in `TX blocked by`** — in the last 600 s there have already
  been 300 s of transmitting (`docs/decisions.md` D31). Transmitting comes
  back by itself when the window frees up; `remoteg90ctl status` shows in how
  many seconds. No `tx-unlock` needed. If the reason is an unreadable or
  unwritten history (`tx_history.json`), check the state directory
  `/var/lib/remoteg90ctl` (disk space, permissions).
- **Port lost** (USB unplugged, I/O error) — the guard does not reopen it by
  itself. After checking the connection: `remoteg90ctl port-open`.
- **Port in `line_fault` state** — the PTT line became active although no one
  was transmitting; the guard lowered it. Check who opened the port
  (`sudo fuser -v /dev/serial/by-id/...`), remove the cause, then
  `remoteg90ctl port-open`.
- **Stop immediately** — `sudo systemctl stop remoteg90ctl-guard` (the guard
  lowers the lines; the kernel lowers them also when the process is killed).
  As a last resort: unplug the DE-19's USB cable or switch the radio off.
- **A risk the program cannot remove:** if the guard hangs inside the USB
  driver (in the kernel, in an uninterruptible state), even killing the
  process will not close the port, and the lines may stay active. The G90 has
  no TOT (step 2), so the radio will not stop transmitting by itself — know
  how to unplug the DE-19's USB cable or the radio's power quickly.

## 8. Example: a small computer by the radio — Fujitsu Futro S740 with DietPi

This is the computer by the radio in the remote station this project was
built for; another small computer with Debian is prepared the same way.
Futro S740: Intel Celeron J4105 (4 cores, 1.5–2.5 GHz), 4 GB DDR4 RAM, **an
8 GB M.2 SSD**. System: **DietPi on Debian 13 (Trixie)** — little disk space,
logs in RAM (RAMlog), Python 3.13 in the system, no desktop and no PipeWire
(sound cards directly through ALSA). DietPi has no D-Bus, so the clock is
checked through **chrony** (D53). Internet: **a Huawei E3276 modem in HiLink
mode** (SIM card, LTE).

8 GB of disk is enough, but with no margin: no swap on the disk, I/Q
recordings deleted. The M.2 SATA disk can be replaced with a larger one
(check the connector format on your unit).

### 8.1. Installation step by step

The installer `install.sh` (section 0) does everything that does not need
you (8.2). What remains: install DietPi, when moving make a copy of the
state on the old computer, log in to Tailscale and check the hardware.

1. **The modem** (once, on any computer with a browser): plug in the E3276,
   open `http://192.168.8.1`, set the SIM card's PIN (or switch it off) and the
   operator's APN. The modem remembers them.
2. **DietPi:** write the image `DietPi_NativePC-UEFI-x86_64-Trixie_Installer.iso`
   (dietpi.com → Downloads → Native PC) to a USB stick (e.g. balenaEtcher or
   `dd`), boot the S740 from it and install to the internal SSD. If the S740
   will not boot in UEFI mode — the `BIOS` version. For the installation
   connect an Ethernet cable (internet), and plug in the modem right away.
   DietPi updates itself on its first boot; log in as `dietpi` (change the
   default password when DietPi asks).
3. **Code and system:** the command from section 0. The installer fetches the
   code into `~/remoteG90ctl` and prepares the system.
4. **Only when moving — a copy of the state on the old computer** (accounts,
   settings, the station log, the guard's lockout and history, the TX log, the
   configuration — 8.4):
   ```sh
   sudo deploy/services.sh --stop
   sudo deploy/backup.sh
   f=$(ls remoteg90ctl-backup-*.tgz | tail -1)
   ssh dietpi@<S740-address> "cat > $f" < "$f"   # scp fails on a fresh DietPi (no sftp-server)
   ```
5. **The state from the old computer** on the S740:
   ```sh
   sudo ~/remoteG90ctl/install.sh --restore ~/remoteg90ctl-backup-*.tgz
   ```
   Without `--restore` you get a clean configuration (simulator). The
   installer can be run again, e.g. after plugging in the modem. It does not
   start the services.
6. **Next:** `sudo remoteg90ctl setup` (section 0), or by hand, in order:
   - `sudo tailscale up` and logging in with the link;
   - in `/etc/remoteg90ctl/config.toml`, `allowed_hosts` with the S740's name
     in the tailnet (`tailscale status`; after a move the old computer's name
     is still there), then `sudo tailscale serve --bg 8078`;
   - the port's and cards' names (`ls /dev/serial/by-id/`, `arecord -l`)
     matching the configuration;
   - **the hardware checklist again on the S740** (section 5; 8.3);
   - `sudo deploy/services.sh --start`;
   - `clock_synchronized synchronized=True` in the server's log.
7. **On the old computer** the services stay disabled (`deploy/services.sh
   --stop` in step 4 also disables autostart): the radio is connected to one
   computer only.

### 8.2. What the installer (`install.sh`) does

- Checks the system (Debian 12/13, Ubuntu 22.04+, Raspberry Pi OS, DietPi;
  x86_64 or aarch64) — another system only gets a warning, 32-bit ARM is
  refused.
- **Packages:** `git curl ca-certificates libportaudio2 libopus0 alsa-utils
  sysstat chrony`.
- **brltty** — if present, stops and masks it (on Ubuntu it takes over the
  CH340). **ModemManager** — masked on a computer without a desktop; on a
  computer with a desktop it stays (it may serve the laptop's modem), and a
  udev rule keeps it away from the CH340. Probing serial ports, either could
  raise RTS on the CH340 and **key the radio** (section 5). DietPi usually has
  neither.
- **udev rules:** `90-remoteg90ctl-serial.rules` (ModemManager skips the
  CH340), `91-…-iq.rules` (the I/Q card as `G90IQ`), `92-…-audio.rules` (the
  DE-19's card as `G90AUDIO`). Other cards — change the identifiers in the
  rules (`lsusb`).
- **Clock:** chrony (replaces systemd-timesyncd); on DietPi
  `CONFIG_NTP_MODE=0` in `/boot/dietpi.txt` (*Custom* — DietPi does not
  synchronise by itself). The server refuses to transmit FT8/FT4 when the
  clock's error according to chrony exceeds 0.2 s (D53); right after a start,
  before chrony has gathered measurements, FT8/FT4 transmitting is blocked
  for a few minutes — that is intended.
- **Suspend** masked; on DietPi the **swap** file on the disk is also
  switched off (`dietpi-set_swapfile 0`).
- **Does not set up the internet connection** — the connection (Ethernet,
  Wi-Fi, LTE modem) is up to you; for the Huawei E3276 — notes below.
- **uv** (for your user) and **Tailscale** (the install script from
  tailscale.com), if missing.
- **Code:** a clone into `~/remoteG90ctl` (or an update) when the installer
  came through `curl`; run from a directory with the code — that directory.
- **Program:** `deploy/install.sh` (on Debian 13 the system's Python 3.13; on
  Debian 12 and Ubuntu 22.04 uv downloads Python 3.12+), without a package
  cache; with `--restore` — `deploy/restore.sh` and a second check of the
  configuration. An existing installation: the services are restarted after
  the update.

DietPi keeps logs in RAM (RAMlog, cleared every hour) — that spares the disk;
the TX log is in `/var/lib` and is not lost. When something needs looking
into, save `journalctl` right away. Log level: `INFO`; `DEBUG` only briefly.

**Internet over LTE (Huawei E3276, HiLink) — notes:**

- **Connecting** is up to you. In HiLink mode the modem is a USB network card
  (`ip -br link`: a new interface, e.g. `enx…` or `usb0`) and gets its address
  through DHCP. With NetworkManager (Ubuntu with a desktop) it works at once;
  on DietPi and Debian without a desktop (ifupdown):
  ```sh
  printf 'allow-hotplug <interface>\niface <interface> inet dhcp\n' | sudo tee /etc/network/interfaces.d/lte
  sudo ifup <interface>
  ```
  (`/etc/network/interfaces` must contain `source /etc/network/interfaces.d/*`.)
  If `lsusb` shows the modem only as a CD drive — `sudo apt install
  usb-modeswitch` and plug it in again. **Do not install ModemManager** —
  HiLink does not need it, and probing serial ports it could key the radio.
- **Tailscale** works over LTE, also behind the operator's NAT; after
  `tailscale up` the S740 is visible in the tailnet. `tailscale serve` as in
  section 6.
- **Data:** listening with Opus at 48 kbit/s is about 21 MB per hour; the
  waterfall at 20 lines/s much more — the "resources" box shows the traffic
  and an estimate per month; with a small data plan lower the waterfall speed
  or switch it off.
- **Interference from the HF transmitter:** the modem on a short USB
  extension, away from the antenna and its feed line, with a ferrite on the
  cable. During the first transmissions watch whether the LTE connection and
  Tailscale hold (losing the browser's connection ends the lease — rule 5 —
  so the radio stops transmitting, but the contact is cut off).

### 8.3. The program

1. The installer fetches the code (8.1 step 3).
2. `deploy/install.sh` (run by the installer) uses the system's Python 3.13;
   `ft8lib` has a ready-made package for x86_64 — no compiler needed. Updating
   the program later: run `sudo ~/remoteG90ctl/install.sh` again (or the
   command from section 0), which moves to the newest release and updates `uv`
   when it is older than 0.13.4. The system, Tailscale and the system libraries
   are only installed, not kept up to date: update them yourself (`sudo apt
   update && sudo apt upgrade`, on DietPi also `dietpi-update`).
3. Sections 1–6 apply as on any computer, checking the names: the port
   (`/dev/serial/by-id/`), the cards (`arecord -l`, `/dev/snd/by-id/`), the
   udev rules. Do not connect a second CH340 adapter (the radio port's name is
   not unique, section 5). The LTE modem has names of its own and does not get
   in the way.
4. **The hardware checklist (section 5) again on the S740**, on a dummy load
   or at low power: no transmission when the computer boots, when the DE-19
   **and the LTE modem** are plugged in and out, a blink only when the guard
   starts (start in SSB). Results from another computer do not carry over —
   it is a different USB controller and a different system.
5. **The TX log** — by default in the guard's state directory
   (`/var/lib/remoteg90ctl/tx.jsonl`). A configuration from an older
   installation may still have the old path in `/var/log`; change it:
   ```toml
   [guard]
   tx_log_path = "/var/lib/remoteg90ctl/tx.jsonl"
   ```
   (`deploy/restore.sh` does this by itself when the old path is in RAM.)
6. **Whether the server can see chrony** inside its restrictions (the service
   can reach only localhost): `clock_synchronized synchronized=True` in the
   server's log, and the clock without a warning in the "resources" box. By
   hand:
   ```sh
   sudo systemd-run --pipe --wait -p User=remoteg90ctl -p IPAddressDeny=any -p IPAddressAllow=localhost chronyc -c -n tracking
   ```
   It must print one line ending in `Normal`.

### 8.4. Moving the state from another computer

`deploy/backup.sh` collects into one archive: the configuration, accounts,
sessions, settings, the station log (a consistent SQLite copy), the guard's
state (lockout, duty-cycle history) and the TX log — without I/Q recordings.
The archive holds passwords (as hashes) and sessions, so it is created
readable only by you (0600). `deploy/restore.sh` unpacks it on the new
machine into the services' state directories (`/var/lib/remoteg90ctl`,
`/var/lib/remoteg90ctl-server`; it accepts no others), takes only the known
regular files (links are refused), overwrites nothing that exists, and
changes a TX log path in RAM (tmpfs) to the guard's state directory by
itself. **Restore only your own backups:** an archive from someone else is
like their script run as root (it also carries the radio configuration and
the accounts).

On the old computer:
```sh
sudo systemctl stop remoteg90ctl-server remoteg90ctl-guard
sudo deploy/backup.sh
f=$(ls remoteg90ctl-backup-*.tgz | tail -1)
ssh dietpi@<s740> "cat > $f" < "$f"   # scp fails on a fresh DietPi (no sftp-server)
```
On the S740, `install.sh --restore` does it (8.1). By hand: **after**
`sudo deploy/install.sh` (it creates the service user), with the services
stopped:
```sh
sudo deploy/restore.sh ~/remoteg90ctl-backup-*.tgz
```
Then in `/etc/remoteg90ctl/config.toml`: `server.allowed_hosts` (the S740's
name in the tailnet), the radio's port and the cards' names if they differ;
once more `sudo deploy/install.sh` (it checks the configuration) and only then
`sudo systemctl enable --now remoteg90ctl-guard remoteg90ctl-server`. On the
old computer: `sudo systemctl disable remoteg90ctl-server remoteg90ctl-guard`
(the radio is connected to one computer only). After a successful move,
delete the archive or keep it somewhere safe (a backup).

A backup while running: `sudo deploy/backup.sh` also works with the services
running.

### 8.5. Measuring the load

After starting, with the waterfall on and off, with listening and without:
```sh
pidstat -t -u -p $(systemctl show -p MainPID --value remoteg90ctl-server) 5 3
sudo "$(command -v uvx)" py-spy top --gil --pid $(systemctl show -p MainPID --value remoteg90ctl-server)
uv cache clean      # py-spy from uvx leaves a cache on the small disk
```
`%usr` is the program's work, `%system` the kernel's (reading the cards in
small blocks, wake-ups). On a ThinkPad T470 the server used 15–18 % of a core
(09.11.2026); the J4105 is slower than the T470's i5 but has 4 cores — there
should be plenty of headroom. FT8/FT4 decoding does not load the S740 — the
browser does it (D52).
