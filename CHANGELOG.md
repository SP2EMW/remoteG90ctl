# Changelog

All notable changes to remoteG90ctl are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) with the rules in `AGENTS.md`
section 11:

- `0.x` is a development version, until `1.0.0` (what it still needs:
  `AGENTS.md` section 10).
- A release without a pre-release suffix is made only after the hardware
  checklist ([docs/install.md](docs/install.md) section 5) has been passed on
  a real G90. Until then releases are candidates (`rcN`).
- Every release that touches the guard, transmit leases, PTT or any other
  transmit safety rule has a **Safety** section.

## [Unreleased]

### Added

- Band conditions in the Propagation window (owner's request 2026-11-27): a
  table of the bands with the spots heard by European spotters in the last
  30 minutes (from the DX cluster, no new connection), the stations heard and
  the best SNR in the last FT8 band scan, both coloured green (10+), yellow
  (3-9) or red (0-2), and a grey, rough estimate from SFI, K and day or night
  at the station (not a measurement).
- FT8 band scan in the Propagation window (owner's request 2026-11-27,
  decisions.md D64 point 9): "Scan FT8 bands" asks for the bands, then on each
  one tunes the FT8 calling frequency in U-D with the attenuator on, decodes
  four full 15 s periods and puts the stations heard on the map (coloured by
  band; those without a locator in a list with their country). At the end, or
  when stopped, the frequency, mode and PRE/ATT from before come back.
  Another decoding window, a DX cluster spot or any change of a radio setting
  from the page asks whether to stop (on "go on" the change is not sent); PTT
  and audio that stops flowing (a sleeping phone) stop it; a hidden page goes
  on; closing the window warns first. The results
  stay until the next scan, at most three hours (in this browser). Nothing
  transmits.
- Propagation window (owner's request 2026-11-27): the "Propagation" button
  opens a window with the solar figures (SFI, A, K) of the last WWV or WCY
  report the DX cluster sent by itself (nothing is asked of the cluster; the
  report is kept six hours, also across restarts, unless a newer one comes), the
  UTC time, and a map of day, night and the grey line with the Sun overhead
  and this station's locator. Land outlines from Natural Earth (public
  domain), shipped with the program. Display only. SFI, A and K are coloured
  green (good), yellow (fair) or red (poor) for HF: SFI 120+ / 80-119 / below
  80; A below 10 / 10-29 / 30+; K 0-2 / 3-4 / 5+. Each has a sign (☀ SFI,
  ◎ A, ϟ K) and a tooltip saying what it measures.
- "Country" next to calls (owner's request 2026-11-27): a column in the DX
  cluster window, the FT8/FT4 list (the sender) and the CW window (the call
  read last), and in the tooltip of a spotted call over the waterfall. The
  country is the state the call's series is allocated to in the ITU's table
  of call sign series (Radio Regulations, Appendix 42), shipped with the
  program, whatever the station log holds. It is not a DXCC entity: KH6 shows
  the United States, VP8 the United Kingdom, BV China; calls the table cannot
  place (1A, 3D2) show nothing. The column shows a short name (United
  Kingdom, Russia, United States, Aruba); the full ITU name is in its tooltip.
- DX cluster, server side (decisions.md D63): a new process,
  `remoteg90ctl-cluster`, keeps one telnet connection to the DX cluster the
  administrator sets and passes the spots to the server; it is the only
  process with network access, runs as a user of its own and reads nothing of
  the station. It sends the cluster only the callsign at login. Installed and
  enabled by the installer (`remoteg90ctl-cluster.socket`). Server requests
  `cluster_subscribe`, `cluster_config` and `cluster_set`
  ([docs/cluster-protocol.md](docs/cluster-protocol.md),
  [docs/server-protocol.md](docs/server-protocol.md)).
- DX cluster on the page (D63): the "DX cluster" button opens a window with
  the spots of the last hour, filtered by band (160-10 m) and mode (CW, SSB,
  FT8, FT4, RTTY, SSTV, DIGI; the mode from a word in the comment, else the FT8/FT4
  calling frequencies, else the IARU band plan segment). A click on a spot
  tunes the radio and sets the mode, always U-D or L-D (the DE-19 carries the
  radio's audio in them alone): CW in L-D below 10 MHz and U-D above, with the
  tone at 700 Hz; SSB on the spot, L-D below 10 MHz and U-D above; FT8/FT4 on the calling frequency in U-D; RTTY in L-D with
  the RTTY window's mark tone on the spot; SSTV on the spot, L-D below 10 MHz
  and U-D above; other digital modes in U-D with the signal at 1.5 kHz. An
  FT8/FT4, RTTY, CW or SSTV spot also opens its window and starts reception
  (an FT8/FT4 window already on that mode and band only shows); an SSB spot
  closes the decoding window open, with its decoding. The administrator sets the
  cluster (host, port, callsign) in the window. "spots" in the waterfall block
  writes the calls of the last 15 minutes, as filtered, at the top of the
  spectrum, also beside FT8/FT4, RTTY and CW; a click on one tunes to it.
- SSTV reception (decisions.md D60): the "SSTV" button opens a window that
  shows each picture line by line as it is received, decoded in the browser
  from the audio before any processing. The mode is found by itself, from the
  VIS code or, when joined later, from the sync pulses: Martin 1/2, Scottie
  1/2/DX, Robot 36/72, PD 50-290 and Wraase SC2-180. The last 12 pictures stay
  in a list for the page; "save PNG" saves the one shown. Reception only;
  the decoder is a port of Robot36 by Ahmet Inan (0BSD).
- CW skimmer (decisions.md D62): the "CW" button opens a window that finds
  CW signals in the U-D/L-D passband and reads up to 8 at once in the browser,
  from the audio before any processing (tone, radio frequency, speed and the
  text of each; the last call read in green, and over the waterfall). A click
  on a signal narrows the browser's receive filter around it (200, 300 or
  500 Hz), and so does a right click on the waterfall, around the clicked
  place (while RTTY runs, its tones keep the right click); a new width in the window applies at once, to the filter and its
  band on the waterfall; a retune starts the list over (the calls do not move
  with the scale); "wide" brings the filter from before back. Our own decoder: the
  speed of each word is taken from its own elements, and each signal's filter
  narrows with its speed (half a dot: about 33 Hz at 20 WPM), so weak signals
  read better; noise peaks give their channel up to a stronger signal. Uneven
  keying, as by hand (elements 20 % off, dashes 2.7-3.3 dots), reads as
  well as even keying. Long gaps between characters (slow senders, Farnsworth
  spacing) are learnt per signal, so the words and the call come out right;
  speeds down to 4 WPM are followed.
- RTTY reception (45.45 baud, 170 Hz shift; decisions.md D61): the "RTTY"
  button opens a window with the received text. Decoded in the browser from
  the audio before any processing. The mark tone is set in the window or by a
  right click on the waterfall (the pair goes around the click; a left click
  tunes the radio as always); "REV" swaps mark and space; the two tones are
  orange lines over the spectrum and the waterfall. A squelch keeps noise
  from printing between the transmissions: text is shown only while one tone
  clearly stands over the other. "AFC" (on by default) follows a station up
  to about 80 Hz off the set tones and a shift other than 170 Hz (200 by
  some); the orange lines show the tones decoded and the window the offset
  found. Transmission stays on the set tones.
- RTTY transmission (D61): "TX" sends what is typed in the RTTY window and
  ends the transmission after it, as a macro does (no idle carrier while
  nothing is typed), "RX" sends what still waits and stops, "Stop" stops
  at once; macros for ordinary contacts and contests (CQ, answer, QSO, BTU,
  73, CQ TEST, exchange with a contest number, TU, AGN, my call; editable,
  kept on the account) transmit only on a click. "log" opens the station log
  with the call and RTTY (new log mode, ADIF `RTTY`).
- The about window ends with a note on how the program was made: the
  author's idea, requirements, safety rules and tests on a G90; the code
  written by an AI model (Claude, Anthropic) following those rules.

### Changed

- The RTTY, CW and SSTV windows set the mode usual on the band and keep it on
  every band change while they are open: RTTY L-D on every band, CW and SSTV
  L-D below 10 MHz and U-D above. That mode is not remembered for the band;
  closing the window brings back the voice mode remembered there. A CW spot
  opens the CW window, as RTTY and SSTV spots open theirs; an SSTV spot below
  10 MHz tunes in L-D.
- The station log's form shows the QSO's date and UTC time all the time
  (date and HH:MM:SS); they follow the clock every second until typed in,
  and again after Save or Clear. The QSO gets what the fields show.
- Only one of the FT8/FT4, RTTY, CW and SSTV windows at a time: opening one
  (button or F8/F4) closes the other, with its reception and any
  transmission of its own; FT8/FT4 gives the radio back its frequency and
  mode from before, as its "Stop" does.

### Safety

- RTTY transmission goes the FT8/FT4 way (D53, D61): the page holds an
  ordinary lease and renews it; the server makes the signal and feeds it to
  the radio only through the voice path's limiter and only within that lease
  (`rtty_start`, `rtty_text`, `rtty_end`; browser only). A released or
  expired lease, a silent page or a stuck card stop the sound at once and
  drop what waits; idle characters are sent only within the lease. The page
  stops after 6 minutes without the operator's activity, and when it is
  hidden; macros transmit only on a click. Guard limits unchanged.

### Fixed

- The FT8 band scan showed the SNR with all its decimals (13.327366449884387);
  it is now whole dB with its sign (+13).
- FT8 band scan: a change of frequency made outside the page (the radio's
  knob, another program) went unnoticed and the scan went on off its band;
  it now asks, and "go on" tunes back to the band.
- The mode remembered for each band: a click on a DX cluster spot on another
  band, and closing FT8/FT4 after its band was changed, stored the new mode
  for the wrong band (the mode was sent before the frequency, and the radio
  reported it on the band left behind). The frequency is now sent first.
- Movable windows no longer grow by their padding and border each time they
  are moved or their corner is caught: the size kept is the size shown.
- The station log takes long special event calls (e.g. PH40EUDXF): up to 15
  characters in each part of a call, 20 in all.
- The SSTV button no longer widens the first column of settings: Log, FT8,
  RTTY, CW, SSTV and PTT stand in one row above the columns.
- `docs/third-party.md`: fldigi's licence is GPL-3.0-or-later (it said
  GPL-3.0); the Baudot and Morse tables are listed as data from their
  standards; the check that the RTTY and CW code was not copied is recorded.

## [0.9.2rc1] — 2026-11-24

A release candidate: the page installs as an app, works better on a phone
(windows that fit the screen, reconnecting after the phone is unlocked), and
receive audio over a slow link has a larger buffer. FT8/FT4 shows the whole
transmitted band. LoTW upload is done with TQSL on the operator's computer.
No change to the transmit path.

### Added

- The page installs as an app on phones and computers (a web app manifest
  with 192 and 512 px icons): an icon and a window without the browser's bar.
  Manifest only, no service worker, so the page always comes from the server.
  The about window has an "install as app" button when the browser offers
  installing (Chrome, Edge), and otherwise says how to do it from the menu.

### Changed

- LoTW: the operator uploads the log's ADIF export with TQSL on their own
  computer; the server never talks to LoTW and holds no certificate
  (decisions.md D59, replacing the planned upload service on the server).
  README says how.
- FT8/FT4: the TX mark shows the band the transmitted signal takes above the
  TX tone (FT8 50 Hz, FT4 83 Hz), shaded on the spectrum and over the
  waterfall, instead of a single line. Display only.
- Receive audio over a slow link: the playback buffer may grow to 250 ms
  (was 200 ms), by 20 ms after each dropout up to 200 ms and by 5 ms above
  it; the whole delay from the radio to the speaker is kept within 350 ms
  (was 250 ms). Fewer dropouts in the evening, a little more delay.
- Sliders and check boxes are grey, the page's text colour, in every browser
  and in the installed app (they had the browser's default colour).

### Fixed

- The about window lists the PTT shortcut (left Ctrl + left Alt, held).
- Floating windows are never taller than the screen; their content scrolls
  inside. On a phone the top of the about window was off the screen.
- After a phone was unlocked the page showed the login, although the session
  was still valid: the first check of the session came before the phone's
  network was back, and no answer counted as logged out. Now no answer means
  trying again later; only the server saying "not logged in" shows the login.

## [0.9.1rc1] — 2026-11-22

A release candidate with a new layout of the page: PTT with the microphone
meter and the receive and transmit audio state above the settings, the
settings in four columns whose widths are set by dragging, number fields with
arrows instead of sliders for the filters and values in dB, and documentation
fixes. No change to the transmit path.

### Fixed

- Documentation: the System log needs the `admin` permission (the server
  protocol said `receive`); the installer example names a release that
  exists; the installation guide describes the page as it is now (filter
  fields, the "Receive audio" section, no power meter in the header); the
  README says that CW can be received but not transmitted, and where the
  hardware checklist has been passed; references to internal development
  stages removed.

### Added

- README: how to reach the page from another computer in the home network
  (SSH tunnel or your own HTTPS proxy), marked as not yet tested.

### Changed

- Web page: the PTT button, the microphone meter and the transmit state moved
  up beside Log and FT8, so they are in reach without scrolling; the
  "Transmit" section is now called "Transmit audio".
- Web page: the filter sliders (receive filter, transmit low and high cut) and
  every slider in dB (waterfall min and max, microphone gain, card out, gate,
  compressor threshold, make-up) are now number fields with arrows, like RF
  gain. The arrows apply at once; a typed value on Enter or when leaving the
  field; Escape puts back the last value. The equaliser keeps its sliders.
- Web page: the filter controls sit right of their spectrum — the receive
  filter beside the receive audio spectrum, low and high cut beside the
  transmit spectrum.
- Web page: right of PTT and its meter, a fixed two-line field with the
  receive (RX: delay, buffer, drops) and transmit (TX: delay, margin, gaps,
  skips) audio state; the receive state left the Receive audio section. More
  room between FT8 and PTT.
- Web page: the four setting blocks (Radio, Waterfall, Receive audio,
  Transmit audio) stand in columns of equal height under Log, FT8 and PTT.
  The bars between them change the column widths, like the handle below the
  waterfall; the controls in each block rearrange themselves, the widths are
  kept in the browser, a double click on a bar restores the defaults. Below
  1100 px the blocks wrap as before. Both audio spectra are the same size,
  PTT is a third wider, and "remove artefacts" is now "DC fix".

## [0.9.0rc4] — 2026-11-21

The release candidate for publication. Since 0.9.0rc3: fixes from the
pre-publication audit and the dependency audit, audio delay kept near 250 ms
each way with fewer gaps over slow links, FT8/FT4 with nonstandard callsigns,
DXpedition messages and band changes, and a week of fixes from use on the air.

### Added
- While transmitting voice, the page shows the voice path next to the meter: `delay ~190 ms · margin 80 · 2 gaps · 0 skip` (estimated microphone-to-radio delay, the server's margin, times the voice ran out, frames the browser left out). The server sends it with each lease renewal (`audio` in the `tx_renew` answer).

### Safety
- When the guard ends a transmission by itself (lease expiry, hard limit, lost port, lost guard), the server no longer plays the rest of the queued browser audio (up to 200 ms) into the radio's sound card after PTT is released; only a plain release by the page, while the guard still holds PTT, plays the tail of the last word (not a server-side end with a reason, which may race the guard's expiry).
- FT8/FT4 automatic transmissions stop when the radio leaves the chosen FT8/FT4 frequency or U-D (tuning, seek, another band or mode); they start again only with Enable TX and its confirmation. This is a stop on the page; the server does not check the FT8/FT4 frequency (it still checks the band, rule 17, and refuses radio changes during a transmission).

### Security
- The installer updates `uv` when it is older than 0.13.4 (it trusted a root CA whose intermediate keys were stolen, GHSA-hq85-5cpj-mr62), or stops.
- `restore.sh` unpacks only the files a backup has: GNU tar may unpack entries its listing does not show (CVE-2026-5704).
- A path in the configuration may no longer contain control characters, and `backup.sh` and `restore.sh` read the configuration's paths one per line: a newline in a path from a restored archive could hide the other paths from the restore's checks.

### Changed
- The page's blocks are fewer: Tuning and Radio are one block (Radio: tuning in its first row, the radio's settings in the second), and Listen and Receive audio are one (listening first, then the processing).

### Fixed
- Stopping FT8/FT4 no longer leaves the radio in U-D on 80 and 40 m: when the mode at the start was U-D (often FT8/FT4's own, after a page was closed while it ran), the band's remembered voice mode is set instead, or L-D below 10 MHz and U-D above.
- The receive audio spectrum is not drawn during FT8/FT4 reception, leaving the CPU to the decoder.
- Fewer lost audio frames for FT8/FT4 decoding: the server keeps up to 2 s of audio for a browser through a stall of the link (was 0.5 s) and 2.5 s of the card's blocks (1 s since the 20 ms blocks); the FT8/FT4 window says how much of a cycle's audio was missing.
- The listening state fits its place: `delay 216 ms · buf 120/80 · 3 drop` (delay, buffer now and its target, dropouts), with a fixed explanation on hover; it was cut off and its changing tooltip never showed.
- Audio delay kept near 250 ms each way (owner's decision 2026-11-21). Receive: the DE-19 card is read in 20 ms blocks with low PortAudio latency; the browser's buffer adapts (100 ms, up to 200 ms after dropouts, back down on a steady link) and a burst after a stall is cut so that the whole delay stays within 250 ms; the page shows the measured delay from the radio to the speaker. Transmit: the browser skips voice frames when over 100 ms of them wait to be sent; the server's margin adapts (80 ms, up to `[audio] tx_jitter_ms`, now 200 by default) and drops the oldest frames after a burst; the card is written with low PortAudio latency. The cards' latencies are logged (`alsa_input_opened`, `alsa_output_opened`).
- After an update the browser gets the new page at once: the page and its scripts are served with `Cache-Control: no-cache` (checked each time, a short 304 when unchanged); an old audio script could stay for hours. The audio worklet is also loaded fresh each time listening starts.
- Voice sent from the browser no longer has gaps on the air when frames arrive late (LTE, a relay): the server collects 250 ms of it before playing (`[audio] tx_jitter_ms`, 0 to 500), and again after the queue runs dry. The voice goes out 250 ms later; FT8/FT4 is not delayed.
- Received audio no longer lags by a second or more after a stall of the link (LTE, a relay): when the buffer grows past 400 ms it goes back to 150 ms at once, crossfaded; it used to take minutes.
- The error window shows a message repeated in a row once, with a count, instead of a line for each copy.
- The mode remembered for a band no longer takes FT8/FT4's U-D: a voice QSO on 80 or 40 m after FT8 came back in U-D.
- On a phone, a frequency, power or RF gain typed into its field is set when the field is left (the phone's keyboard often has no Enter for numbers or moves on to the next field); it went back to the old value. With a mouse and keyboard, Enter sets it as before.
- FT8/FT4: a DXpedition's double message ("SP2EMW RR73; F4NPE <YO2LIM> -22") ends our QSO (or, for the second station, is its report); the page kept sending R+report.
- The administrator's take-over logs out only the account using the station, no longer every other account with a remembered login.
- A wrong listening request (bitrate, codec or complexity) is refused without ending the listening it would have replaced.
- The sound card's output level is set in whole decibels; a value near zero was written as `-1e-05dB`, which `amixer` refuses.
- A band change remembers the band's mode only after the radio reports the mode sent, no longer after a fixed 2 s: a slow radio could store the old band's mode for the new one.
- A button clicked earlier with the mouse keeps the focus when it is later used with the keyboard.
- The "card out" slider is hidden where the sound card has no such control.
- FT8/FT4: a station with a nonstandard callsign (SX200H, HF10SQ8W, F4ELU/QRP) is called with its hash, `<SX200H> SP2EMW JO93`, as WSJT-X does; the server refused the full call, which the encoder would have shortened. An own nonstandard call calls CQ without the grid.
- FT8/FT4: clicking the partner's answer to push a QSO on no longer starts it afresh: the report received, the grid and the report sent stay, so the QSO is logged with them (they were missing). A QSO left for more than four cycles starts afresh; free text addressed to us is not answered. An orange `TX:` row is no longer added for a transmission halted while the server was starting it.
- FT8/FT4: callsigns with digits as the 2nd and 3rd character (S51A, A71AB, E73M, P40A…) are sent as standard calls; the encoder (ft8lib 1.3.2) took them for free text and the server refused the message.
- FT8/FT4: an answer decoded up to 1 s into our FT8 cycle (FT4: 0.3 s) is still sent in it; the server sends a late request only while it can still be decoded (FT8 1.5 s, FT4 0.8 s late, was 2 s for both).
- FT8/FT4: a band chosen on the page (band list, Alt + arrows) while FT8/FT4 reception runs moves the reception to that band's FT8/FT4 frequency in U-D, instead of the band's last frequency and mode, with the FT8 window still on the old band.

## [0.9.0rc3] — 2026-11-13

The first public release candidate. Since 0.9.0rc2: an antenna tuning lasts
until the power meter reads zero, lease renewals no longer wait behind other
requests, the page refuses Tailscale Funnel, the first account is the
administrator, and the page gains seek, keyboard shortcuts, an about window
and a server restart button.

### Safety
- A tuning in which the radio's power meter never showed power ends with a red warning on the page: the radio did not tune, the antenna is not tuned, and a G90 with its screen blanked tunes (transmits the carrier) only when the screen comes on. The guard sends the end of every tuning to the server (`atu_tune_end`, with `emitted`), and the page now also shows a tuning that did not end within 30 s. A warning only: nothing is locked (owner's decision).
- The drive into the radio can now be raised from the page ("card out", the sound card's output level), by any account with the transmit permission and also while transmitting (owner's decision, D58). The server's limiter (`audio.tx_peak_dbfs`) still caps the digital peak, but the absolute drive is set by "card out", which comes after it (up to the card's 0 dB). Power stays bounded by the radio's setting and `safety.max_rf_power_w`, time by the guard; no sound reaches the radio outside a lease.
- A page's lease renewals (and releases) are answered at once, no longer behind its other requests: a request waiting for the radio (each CAT exchange may wait up to 1 s, in a queue shared with the server's own readings) could hold the renewals back past the 2 s lease timeout, and the server ended a live page's FT8 transmission as `client_silent` (seen on the S740). Renewals still come only from the page (rule 5).
- An antenna tuning now lasts, for the guard, until the radio's power meter reads zero twice in a row (at least 3 s), instead of a fixed 3 s: measured on the S740, a tuning with a poorly matched antenna took about 9 s at more than 2 W whatever the power setting, and the meter showed it throughout. The guard reads the meter replies the server asks for; until the end no other transmission starts and nothing changes the radio. A tuning still going after 30 s locks transmitting until `tx_unlock`. 30 s must fit into the duty cycle before a tuning starts, then the real duration counts; the next tuning is allowed 10 s after the end of the previous one. TX log: `atu_tune` (`max_s`) and `atu_tune_end`; `atu_tune` answers `max_s` and `min_interval_s` (was `counted_s`, `next_in_s`); guard status `tuning` (D43).

### Security
- The "System log" window and I/Q recording are the administrator's (and the CLI's): the server's log shows who logged in and when, and a recording fills the disk. A failed login logs what was typed as the callsign only if it looks like one, so a password typed into that field never reaches the log. Without a session the page learns only that the station is in use, not by which account (security review 2026-11-14).
- ADIF import: at most 20 000 records per file and 100 000 QSOs per account; refused while another account uses the station (the import holds the server for seconds); a field length of thousands of digits or a `FREQ` of `inf` is a bad record, no longer a server error (security review 2026-11-14).
- Two requests to create the first account at the same moment made two accounts, and the slower one became the administrator; now the check is made under the accounts lock and the second is refused (security review 2026-11-14).
- `deploy/restore.sh` (also `install.sh --restore`) restores only into the services' state directories, only the known regular files, and refuses archives with links or special files: before, the archive's configuration could name any directory, and a file there (or a link to one elsewhere) was installed as root (security review 2026-11-14). Restore only your own backups.
- Tailscale Funnel could still reach the page with a forged `Host: 127.0.0.1:…` from a client that is not a browser: `tailscale serve` passes the client's `Host` on unchanged. A request is now known as proxied by `X-Forwarded-Host`, which `tailscale serve` always sets; it needs the tailnet name and the identity, and a request marked `Tailscale-Funnel-Request` is always refused (security review 2026-11-14, D57).
- The page answers requests under its tailnet name (`server.allowed_hosts`) only when they carry Tailscale's identity header (`Tailscale-User-Login`, which `tailscale serve` adds for tailnet users and strips from clients). Tailscale Funnel serves the same name to the public internet without it, so such requests are refused with 403 (D57). New key `server.require_tailnet_identity` (default `true`), to be turned off only for tagged devices. `remoteg90ctl setup` warns when Funnel is on.

### Added
- Listening: an 8 kbit/s quality for slow links, and a "complexity" list (0-10) for the server's Opus encoder: lower costs the server less CPU and sounds somewhat worse. FT8/FT4 reception always uses 48 kbit/s and complexity 10.
- The filters can be set on the audio spectra with the mouse: a click sets the lower edge there, Shift + click the upper edge (receive: the passband; transmit: low cut and high cut). The notch on the receive spectrum moves to the right button (it was the click). Listed in the about window.
- A band change brings back the mode last used on that band (LSB, USB, L-D or U-D), next to the frequency last used there; both are kept on the account.
- When the radio is in USB or LSB (set at the radio, back after a power loss, or chosen on the page), the page asks whether the radio's own microphone and speaker will be used (USB/LSB stays) or this computer (the radio is switched to U-D/L-D, the only modes in which the DE-19 carries the audio). Asked again only after the mode has left USB/LSB.
- "card out" in the Transmit section: the output level of the radio's sound card, which drives the radio after the server's limiter (its ALC), for voice and FT8/FT4. It may be changed while transmitting, watching the ALC; the server keeps it for the station and sets it again whenever the card opens (before, the system's level stayed: -20 dB on a fresh DietPi). Any account with the transmit permission.
- A "pause" button next to the callsign: it stops listening and the waterfall, so no audio or spectrum crosses the link (on a mobile connection, or while away), and the same button starts them again. Not during FT8/FT4 reception, which starting ends a pause; seek is off while paused (it would find stations in the spectrum from before the pause).
- Seek: "◀ seek" and "seek ▶" next to the frequency (keys < and >, also , and . without Shift, or Alt + PgDn / PgUp) tune to the next station down or up, found in the waterfall's spectrum (the highest level of the last 3 s, at least 10 dB over the noise and 200 Hz wide), placed in the receive passband of the mode. With none in view the radio moves 40 kHz and looks again, also outside the amateur bands, until 0.5 or 30 MHz; pressing again, tuning elsewhere or starting FT8/FT4 reception stops it. Off during FT8/FT4 reception. Receiving only: transmitting is still refused outside the bands.
- A "restart server" button in the resources window, after a confirmation: the server program exits and systemd starts it again; the page reconnects by itself. Not the guard. Refused while a lease is active or the radio transmits (`server_restart`, the administrator only: permission `admin`).
- An "about" window: version, author (SP2EMW), links to the source code, issues and private security reports, the licence and the third-party components, and the keyboard shortcuts.
- FT8/FT4: the CQ button stays lit (red, like "TX enabled") while CQ is being called, until a station answers or transmitting is halted.
- Keyboard shortcuts (not while typing in a field): ← → tune by the waterfall's step, Alt + ← → by 10 Hz, Shift + ← → by 100 Hz; Alt + ↑ ↓ the next band; F8 / F4 FT8 / FT4 reception on the current band; L the station log; P / A / N preamp / attenuator / neither; Alt + A the next AGC setting; Alt + N the noise blanker; + / − RF gain by 5 %. None uses Ctrl, so tuning never touches the PTT keys (left Ctrl + left Alt), and none transmits.
- The resources window shows Tailscale's daemon (`tailscaled`) next to the server and the guard while it runs, and the CPU temperature (orange from 85 °C).

### Changed
- The keyboard shortcuts no longer stop working after a control was used with the mouse: a list gives the focus back to the page after its choice, a slider when released, a check box or a button after the click (text and number fields keep it, and so does anything reached with Tab). Escape leaves any field.
- The waterfall costs less per line: the automatic levels come from a count of the line's values instead of sorting them, the visible bins are computed once per line and the buffers are reused, the frequency scale is redrawn only when the centre, span, zoom or size changes, the spectrum above the waterfall is redrawn at most about 12 times a second (the waterfall still takes every line), and nothing is drawn while the tab is hidden (the lines are kept and drawn when it is shown again).
- The page uses less CPU while nothing changes: the receive and transmit audio spectra are drawn at most 15 times a second and only while there is something to show (listening; sending, or playing a recording), their canvases are resized only when their size changes, and the connection dot blinks in two steps on a timer instead of a smooth endless animation, so the browser and the desktop no longer present a new frame at display rate all the time. What the page shows does not change, except the spectra's frame rate.
- The first account is the **administrator**. Only it sees and may use "restart server" (new permission `admin`, also the CLI's). One account uses the station at a time: while another works, the administrator can log in, and its page shows who uses the station, since when and whether it transmits, with "Close …'s page and take over" (after a confirmation: that account is logged out and its transmission ends; the administrator's other sessions stay) and "Wait". Logging in or reconnecting throws nobody out, and no other account can log the administrator out.
- The installer fetches from the project's public repository `SP2EMW/remoteG90ctl` by default.
- The server uses less CPU for the waterfall (measured on the S740 with the waterfall at 20 lines/s and audio at 16 kbit/s: 36 % of one core before, 22 % after with speech encoded at Opus complexity 5; complexity 10, used now, adds about 5 points). The waterfall is computed in single precision (a double-precision window had turned every block and FFT into complex128), the power without a square root, and the bins are pooled by pairs; the I/Q card delivers 50 ms blocks instead of 20 ms (one per waterfall line; the waterfall is 30 ms later). The Opus encoder now sets complexity 10 explicitly at every bitrate. The picture and the sound do not change.

### Fixed
- A click on the receive audio spectrum now sets the filter edge also when the passband had been slid across the dial (it then widened both ways on Shift + click and did nothing on a click): the side above or below the dial comes from the mode.
- FT8/FT4: the log form after a QSO now has the station's grid also when the station did not send it in the QSO (it answered a CQ with a report straight away, or we called it after its own QSO): the page remembers the grids heard in every decode since it opened, and failing that takes the grid of an earlier QSO in the log. A priori decodes are not used for it.
- "card out" failed on every change ("the sound card's level could not be set") and the kept level was not restored: amixer took a negative level such as "-20dB" for options. The level now follows "--".
- FT8/FT4 decoding stopped while the audio played on: after the page reconnected, after Start with the listening already on, after Stop and Start, or slowly when audio frames were lost on the way. The page dated the audio by the Opus decoder's timestamps, and Chrome's decoder takes only the first one and counts on by itself, so the audio drifted away from the server's clock until the receiver dropped it. The page now keeps the server's frame numbers itself and resets the decoder for each new subscription.
- The ALC reading vanished during a transmission when the ALC held still (an FT8 tone driving the ALC to its end): the "TX" mark that comes with the start of a transmission covered it, and the server sends the ALC again only when it changes. The last reading now stays shown.
- The confirmation after adding an account ("Account … added.") appeared under the red "Error" heading. The message window now has an information variant: green, headed "Info", unless an error is listed too.
- The tuning confirmation and the Tune button's hint give the measured times (1–1.5 s with a matched antenna, about 10 s with a poor one) and say that the next tuning comes 10 s after the end of the previous one.

## [0.9.0rc2] — 2026-11-12

The installer and the uninstaller after the first installation on a clean
DietPi (Fujitsu Futro S740). No change to the program itself.

### Added
- The installer shows that it is working: each long step (the package list, packages, uv, Tailscale, the code, Python and its libraries) has a line with a running time and ends with "done (Ns)"; a failing step shows the last lines of its output instead of passing them by silently.
- The installer keeps a log in `~/remoteg90ctl-install.log` (mode 0600): everything shown, and the whole output of every long step; a token in the repository address is left out.
- `uninstall.sh` in the repository root removes remoteG90ctl completely and undoes what the installer and `remoteg90ctl setup` changed, so another installation starts as on a clean computer: the program and all its data (after one `yes`, or `--yes`), the udev and WirePlumber rules, `tailscale serve`, and — following the installer's new record in `/var/lib/remoteg90ctl-installer/changes`, only what it changed — the units it masked, DietPi's clock and swap settings, the packages it installed (systemd-timesyncd back in chrony's place), uv, Tailscale and the cloned code. `--all` for an installation without a record.

### Fixed
- `remoteg90ctl setup` ended with "open the page: http://127.0.0.1:8078" also on a computer without a screen; it now shows the tailnet address set up in step 5 first, and the local one (with the configured port) as "on this computer".
- On a computer without Python (DietPi) the installer let uv download the newest Python (3.15), for which `ft8lib` has no ready-made package, so the installation stopped needing a C compiler. It now uses only the Python versions CI tests (3.12–3.13): the system's, or 3.13 downloaded.
- The installer went on when uv's or Tailscale's own installer could not be downloaded (a failed download piped into `sh` ran an empty script and "succeeded") and only stopped later with "uv not found"; a failed download now stops the step, and the installer checks that uv and Tailscale are really there.
- `deploy/uninstall.sh --purge` left the server's state behind (`/var/lib/remoteg90ctl-server`: accounts, the station log, recordings); it is removed too.

## [0.9.0rc1] — 2026-11-12

Stages 5 to 9 (the remaining CI-V commands, FT8/FT4 reception and
transmission, audio processing in the browser, the station log), login to
the web page from stage 10, and the one-command installer with the hardware
checklist. Used on the owner's G90 (firmware 1.81), including FT8/FT4 on the
air and the hardware checklist with its test transmissions at 1 W;
a candidate, because the checklist has not yet been passed on the final
remote setup.

### Added
- `remoteg90ctl setup` speaks English or Polish: the first question chooses (Enter takes the system's language); answers y/t/tak and n/nie work in both, skipping takes SKIP (or its Polish word).
- `remoteg90ctl setup` offers access from other devices through Tailscale: it logs the computer in (`tailscale up`) if needed, adds its tailnet name to `server.allowed_hosts` and runs `tailscale serve` for HTTPS, then prints the address. The server still listens on loopback only.
- `sudo remoteg90ctl setup`, the hardware setup the installer runs on a new computer (part 1): it finds the DE-19 (its serial port, and its sound card as the one on the same USB hub), offers the other stereo USB cards as the I/Q card (or none: scale only), writes the udev rules naming the cards from what it found, keeps a desktop's PipeWire away from the I/Q card and writes the configuration (hardware, the port, the cards) with transmission off on a new radio or computer. It starts and transmits nothing; the old configuration is kept when it changes.
- One-command installer (`install.sh` in the repository root, for `curl … | sudo bash`): Debian 12/13, Ubuntu 22.04+, Raspberry Pi OS (64-bit) and DietPi on x86_64 or aarch64. It prepares the system (packages, chrony, no brltty, ModemManager kept off the radio's serial port, no sleep, DietPi's swap off, a Huawei HiLink modem's interface), installs the newest release (a tag; while there is no release without a suffix, the newest candidate; `REMOTEG90CTL_RELEASE` picks another, `REMOTEG90CTL_BRANCH` follows a branch instead, and an installation following a branch keeps following it; the fetched release's own `install.sh` then does the installation, and a release too old for that is refused before anything changes) and, run again, updates it; a new installation is never started before the hardware setup. `deploy/dietpi-setup.sh` now calls it.
- Project logo (a small HF transceiver in front of a globe, in the colours of the web page): `docs/assets/logo.svg` and a 512 px PNG, shown in the README; the page has a browser tab icon (`favicon.svg`) and an icon for the home screen of phones (`apple-touch-icon.png`).
- The page header shows the logo at the top left, above the name; the name is coloured like the logo ("remote" light grey, "G90" green as the screen, "ctl" amber as the knob).
- Work without the I/Q card: `[iq] source = "none"` (or no `[iq]` section) runs no waterfall; the page shows the frequency scale alone, with the band plan, the dial, the receive passband and tuning by clicking or tapping it.
- Why the I/Q input does not run (for example "no ALSA card named 'G90IQ'") is shown over the waterfall instead of in the header, and the page asks the server again every 5 s until the waterfall comes back.
- While FT8/FT4 runs, a click or tap on the waterfall or scale sets the TX tone there (as in WSJT-X) instead of tuning the radio away from the dial; the tone is kept within 200 to 2716 Hz.
- While FT8/FT4 runs, the passband on the waterfall shows the range the decoder searches (100 to 3000 Hz above the dial) instead of the receive filter; Stop brings the filter back. The radio and the audio filter are not changed.
- FT8/FT4 window: an "only CQ" checkbox shows only the CQ calls and the messages with your callsign (and your own transmissions); the other decodes are kept and come back when it is unticked. The choice is remembered.
- FT8/FT4 decoding depth 3 (the deepest, with a7 for FT8) is the default for a browser that has not chosen one; weak signals around -20 dB were missed at depth 2.
- FT8/FT4 window: the dB, DT and Hz headings are right-aligned over their numbers, and the message column takes the spare width, so the columns no longer drift apart in a wide window.
- A 6-band graphic equaliser for the received audio (100 Hz to 3.2 kHz, ±12 dB, no added delay) under "Receive audio", after the noise reduction, so the audio spectrum shows its effect; "flat" resets it, and it is kept with the account's other receive settings.
- Movable, resizable windows: the station log, FT8/FT4, the system log, the resources and the error window are dragged by their title bar and resized from the bottom right corner; the one touched comes to the front, and one that opens too. A double click on the title puts a window back where it was by default. Place and size are kept on the account and always fitted so that the title bar stays on the screen.
- Transmit audio presets (D55): a `preset` list (DX, Local, Custom 1, Custom 2) with Save 1 and Save 2 buttons in the Transmit section. DX — narrow 300–2700 Hz, lows cut, gate and heavy compression, for the most range; Local — the full band with a deep low end and a gentle compressor, a broadcast voice; Custom 1 and 2 — empty (the neutral chain) until a Save puts the current settings in the slot. After a manual change the list reads "edited". Kept on the account; the microphone gain and the monitor are not part of a preset. The values are starting points set without listening.
- `deploy/backup.sh` and `deploy/restore.sh`: the configuration, accounts, settings, the station log (a consistent SQLite copy), the guard's state and the TX log in one archive (0600), to move to another computer or keep a copy; restoring never overwrites existing state and moves a TX log path on tmpfs to the guard's state directory.
- The Resources window shows the free space on the disk with the server's state (orange below 500 MB) and whether the server's clock is synchronised (FT8/FT4 transmits only then).
- Band plan on the waterfall (D54): a strip over the frequency scale with the IARU band plan of the ITU region of the account's locator (CW, narrow band and digital modes, all modes, beacons, satellites, FM), the region's amateur bands marked (outside them dimmed with a red line), and a description on hover. New request `get_bandplan`.
- FT8/FT4 transmission (stage 7, D53): in the FT8 window, TX Hz, level, the cycles for CQ (1st/2nd), CQ, Enable TX and Halt TX; a click on a decoded message answers its station. The page runs the QSO as WSJT-X (grid, report, R-report, RR73, 73) in the cycles opposite to the other station and then offers the QSO in a dialog to save in the log. Our messages show in the list in orange and the TX tone over the waterfall. New request `ft8_transmit` and event `ft8_sent`; dependency `ft8lib` (encoder only). Decoding now starts 13.6 s (FT8) / 6.0 s (FT4) into the cycle, so that an answer goes out in the next one.
- FT8/FT4 reception (stage 6, D52): an "FT8" button opens a window with the mode, band, decoding depth and Start/Stop. Start tunes the radio to the band's FT8 or FT4 frequency in U-D, listens at Opus 48 kbit/s and decodes every whole cycle in this browser (ft8ts, a TypeScript port of WSJT-X, in a Web Worker), on the audio before the page's processing; the automatic notch is off meanwhile. Decodes are listed (UTC, dB, DT, Hz, message; CQ in green, messages with the account's callsign in red) and the senders' calls are written over the waterfall at their signals. Stop brings back the frequency, mode and quality. A warning shows when the server's clock is not synchronised.
- Receive audio: a clock frame (`0x04`) once a second gives the UTC time of the audio by the server's clock; `status.audio.clock_synchronized`.
- Web page: the Transmit and Transmit audio sections are one; a recorder (● Rec up to 15 s of the processed microphone, ▶ Play in this browser only, ■ Stop) and an automatic call (Call, every 5–120 s, default 15 s) that sends the recording again and again (D51). The transmit spectrum shows the recording during a call, and a local playback in grey marked "playback — not sent"; the Call button is red.
- Station log (stage 9, D50): a "Log" button opens a window with the QSO form — frequency and mode from the radio, time of saving (UTC), reports 59/599 by mode, "worked before" while typing the call — and the account's own log below it, with search, editing, deleting, dupe marking, and ADIF 3.1.5 export and import. SQLite in the server's state directory. New permission `log` (every account).
- Transmit audio processing in the browser (stage 8, D49): a "Transmit audio" section with low cut and high cut (24 dB/octave), a 6-band graphic equaliser (100–3200 Hz, ±12 dB), a noise gate, a compressor with threshold, ratio and make-up gain, a monitor of the processed microphone (headphones), and the spectrum of what is sent — empty outside a transmission. The processing delay is shown; settings are kept on the account. The microphone gain now acts at the start of this chain. The server's limiter is unchanged.
- Receive audio processing in the browser (stage 8, D49): a "Receive audio" section with the spectrum of what is heard (0–4 kHz), a manual notch set by a click on it, an automatic notch (off in CW), and noise reduction — LMS (no delay) or spectral (about 21 ms) with a strength slider; the processing delay is shown. The audio band follows the Filter sliders under the waterfall (24 dB/octave). Settings are kept on the account.
- Settings on the account (D48): the radio settings (VFO, frequency, mode, split, PRE/ATT, AGC, NB, RF gain, ATU, power) are saved while logged in and set again in the radio at login — through the ordinary requests, so never during a transmission and never above `safety.max_rf_power_w`; the page settings (waterfall, filter, audio, band memory, …) are kept on the account instead of in the browser, taken over from the browser at the first login. `GET`/`PUT /api/settings`, event `radio_settings_restored`.
- Login to the web page (decisions.md D48): station accounts (callsign as the login, QTH locator, password). The first account is created on the page of a fresh installation and is the only one that adds and deletes accounts; an account menu under the callsign in the header (change password, add account, log out). Sessions last 30 days. `remoteg90ctl user list|passwd|delete` on the server. New config key `server.state_dir` (default `/var/lib/remoteg90ctl-server`).
- Web page: a fine tuning slider in the Radio section: up to ±500 Hz in 10 Hz steps, the offset growing exponentially from the middle to the ends from the radio's frequency when grabbed, back to the middle when released (the radio stays where it was tuned); arrow keys tune ±10 Hz.
- Web page: a band list (160m to 10m, IARU Region 1 edges) among the radio controls; picking a band tunes to the last frequency used on it in this browser, or to the middle of the band. It shows the current band, or "—" outside the bands.
- Web page: a "system log" button and window with the server's recent log (the last 500 lines of level INFO and above, then new ones while the window is open; warnings in orange). `log_subscribe` / `log_unsubscribe` and the `log_line` event on the WebSocket.
- Web page: a yellow bar "The radio may be transmitting (S-meter muted)" when the S-meter falls from S1 or more to below its scale for 1 s while the program does not transmit. The G90 mutes the S-meter while transmitting, also when nothing is emitted (microphone PTT in U-D or without speech), which the power meter cannot see. Event `radio_tx_suspected`; informational only.
- VFO A/B and split (CI-V `07`, `0F`, `25`, `26 01`, measured on the radio): `get_vfo`, `set_vfo`, `set_split`, `radio_vfo`, `remoteg90ctl vfo` and `remoteg90ctl split`. The page shows the selected VFO's letter by its (slightly smaller) frequency and the other VFO below it — in orange as "TX" in split; a SPLIT badge at the top; a split checkbox and an A>B / B>A button among the radio controls. With `radio.ssb_data_mode`, USB/LSB become U-D/L-D on either VFO.
- Web page: microphone gain slider in the "Transmit" section (−20 to +20 dB, double-click resets to 0 dB), applied in the browser before the level meter and before sending, remembered per browser. The operating system's microphone level cannot be changed from a web page. The server's limiter is unchanged.
- ALC meter (CI-V `15 13`): read every 0.5 s during a transmission and shown in the S-meter's place on the page as the number on the radio's display (converted with a scale measured on a steady sound; the raw 0–255 level in the tooltip); `get_alc` request, `radio_alc` event and `remoteg90ctl alc`.
- Web page: a 500 Hz tuning step.
- Web page: a separate bit rate for the microphone audio sent to the server ("Transmit" section), remembered in the browser; it no longer follows the "Listen" setting.

- Preamplifier and attenuator (stage 5, CI-V `16 02` and `11`, measured on
  the owner's G90): an "input" selector on the page (off / PRE / ATT −12 dB),
  a PRE/ATT badge next to the ATU badge, `remoteg90ctl frontend
  [off|pre|att]`, requests `get_front_end` / `set_front_end` and the event
  `radio_front_end`. The state follows the radio's front panel while a
  browser is connected.
- AGC (stage 5, CI-V `16 12`, measured on the owner's G90: 00 off, 01 fast,
  02 slow, 03 auto): an "AGC" selector and an AGC-F / AGC-S / AGC-A / AGC--
  badge on the page, `remoteg90ctl agc
  [off|fast|slow|auto]`, requests `get_agc` / `set_agc` and the event
  `radio_agc`.
- Noise blanker (stage 5, CI-V `16 22` and its level `14 12`, menu steps
  0-10, measured on the owner's G90; its width cannot be set over CAT): an
  "NB" switch with a level selector and an NB badge on the page,
  `remoteg90ctl nb [on|off|level N]`, requests `get_nb` / `set_nb` and the
  event `radio_nb`.
- RF gain (stage 5, CI-V `14 02`, 1-100 %, measured on the owner's G90):
  an "RF … %" field on the page and "RF gain: … %" at the top,
  `remoteg90ctl rfgain [PERCENT]`, requests
  `get_rf_gain` / `set_rf_gain` and the event `radio_rf_gain`.
- S-meter (stage 5, CI-V `15 02`, read only): S0-S9+60 with a bar at the
  top of the page, read every 0.5 s with the VFO in receive; the G90's
  non-linear scale measured on the owner's radio (S1 22 … S9 136 … S9+40
  174, 242 below the scale). `remoteg90ctl smeter`, request `get_s_meter`,
  event `radio_s_meter`.

### Changed
- `docs/install.md` section 8: the machine by the radio is now a Fujitsu Futro S740 with DietPi on Debian 13 (it replaces the Dell Wyse 3040 planned before), with chrony for the clock, online through a Huawei E3276 LTE modem in HiLink mode (no ModemManager, which could key the radio by probing the CH340 port).; `deploy/dietpi-setup.sh` prepares a fresh DietPi (packages, chrony, udev rules, no ModemManager, swap and sleep off, the LTE interface, uv, Tailscale) and installs the program, optionally with the state from `deploy/backup.sh` (`--restore`).
- The setting blocks under the waterfall fill each row: at any page zoom or window width they wrap and stretch to the full width, instead of leaving a ragged gap on the right.
- FT8/FT4 window: the cycle number is shown — live by the server's clock ("cycle 2nd (3/4)": the 1st or 2nd cycle, the even or odd ones, as the TX choice names them, and its place in the minute) and on the line that starts each cycle in the decode list.
- FT8/FT4: when a mode is switched on (and when its mode or band is changed) the waterfall zooms to the signals' range, from the dial up 3.2 kHz, as soon as the radio is on the dial; your own zoom or drag cancels it, and Stop brings back the zoom from before.
- `radio.mute_speaker` now also checks every 30 s: the speaker volume is read (CI-V `14 01`) and set to 0 again if it is not (a knob turned at the radio, a power cycle that lost the setting). Not during a transmission or a tuning. A log line `speaker_unmuted` shows when it was found up.
- The waterfall's spectrum is computed only while a browser watches it; without one the I/Q input only measures the level (`status.iq.idle`), saving CPU on a small computer by the radio.
- Installation on a small computer with DietPi (then planned: a Dell Wyse 3040): `deploy/install.sh` puts a Python that uv downloads (Debian 12 has 3.11) under `/opt/remoteg90ctl/python`, keeps no package cache, and checks PortAudio, libopus and `amixer` at once; `docs/install.md` section 8 (system, TX log on persistent storage, moving the state from another computer, measuring the load).
- The account's radio settings are saved every 30 s when they changed (was 2 s) and whenever the account's last page closes: fewer writes with fsync on the eMMC of a small computer.
- The TX log is by default in the guard's state directory, `/var/lib/remoteg90ctl/tx.jsonl` (was `/var/log/remoteg90ctl/`), and `deploy/install.sh` refuses a TX log on tmpfs: DietPi keeps `/var/log` in RAM and clears it hourly. Existing configurations keep their path.
- The clock check falls back to systemd-timesyncd's `/run/systemd/timesync/synchronized` where `timedatectl` cannot answer (no D-Bus on DietPi), so FT8/FT4 can transmit there.

- Web page: the filter sliders moved into the "Receive audio" section (the Filter section is gone); the artefact removal status is a dot — grey off, blinking green while learning, green removing, a green ring when on with nothing to remove — with a tooltip.
- Server: a frequency change (fine slider, waterfall click, MHz field, band) goes before the reads waiting for the CAT bus (follow loop, power meter), so tuning is not queued behind them.
- Web page layout: the header fits one line (smaller gaps, power/RF gain/S-meter a little smaller); the controls form three rows of pairs — Tuning (MHz, fine, band, mode, VFO, split) with Listen, Radio (power, RF, input, AGC, NB, ATU) with Transmit, Waterfall (now with the click step) with Filter; the listening buffer text has a fixed width.
- Web page: the power and RF gain fields are sent on Enter only (no longer after a pause while typing or when leaving the field); Escape or leaving the field without Enter puts back the radio's value.
- Web page: errors (refused settings, refused or ended transmissions, listening refused, unsupported browser) open a window in the middle of the page, marked red, that stays until closed and lists the latest messages. The transmit status text next to the quality list and the radio error text are gone; the confirmation dialog says to press PTT again.
- Web page: in split, while the radio transmits, the two frequency lines change places, so the top line shows the transmit VFO (in orange) and the line below the receive VFO.
- Web page: transmitting is shown by a red bar above the waterfall instead of the TX badge in the header and the "TRANSMITTING" text; the bar stays on for the whole transmit lease (the `radio_transmitting` event now also follows the lease), not only while the power meter shows output. The warnings and the bar lie over the top of the spectrum, so they no longer push the waterfall down (which also cleared it).
- Web page: the mode list offers only LSB, USB, L-D and U-D; the other modes are greyed out but still shown when set on the radio.
- Waterfall fully zoomed out: 10 kHz at each edge of the band (made
  unreadable by the I/Q card's filter) is left out of the view, and the view
  can be dragged to reach them; before, it could not be moved at all.
- The web page is in English (labels, hints, messages and dialogs).
- Waterfall speeds are shown as lines per second, with two new ones:
  2 and 1 line/s (`speed` `slower` and `slowest`).
- Top of the page: "Power:" before the power setting; "I/Q ok" is gone
  (the I/Q input is shown only when it has a problem); the connection state
  is a green / red dot, as tall as the badges, with the words in its tooltip;
  it blinks while data arrives, one blink per second per 100 kbit/s
  (0.5-6 per second).
- While a browser is connected, the radio's settings (tuner, preamp /
  attenuator, AGC, noise blanker, RF gain) are read every 1 s instead of every 0.5 s; frequency and
  mode still every 0.5 s.

### Fixed
- FT8/FT4: while transmitting is on, the cycle right before each of our transmissions is decoded at depth 2 at most (depth 3 took about 1.8 s, so its decodes came after our next message had gone out and every answer was a cycle late); the other cycles keep the chosen depth, and the status line says when depth 2 was used.
- FT8/FT4: the log dialog opened twice in one QSO. When the partner's R-report was decoded only after our next message had already gone out, the end of that transmission finished the QSO before RR73 was sent; now only a transmitted RR73 or 73 finishes it.
- FT8/FT4 decoding no longer stops after the audio is subscribed anew (Start/Stop, a quality change): late frames of the earlier stream, numbered from its own start, were dated minutes ahead and held back all later audio until the page reconnected. (The cause was found later: see 0.9.0rc3.) A decoder that does not answer for two cycles is also replaced by a new one.
- A browser's transmission that the guard or the port ended while it was starting is refused as `unknown_lease`, not as "the sound card did not open". The page's wait for the FT8/FT4 signal's confirmation counts from the signal's real start (up to 2 s late) and allows the server's 1 s for a stuck card.
- FT8/FT4 reception silenced the listening for good about 15 s after Start: the first cycle was handed to the decoder worker by transfer, which empties the array, and the next cycle's buffer was sized from it (length 0), so every audio frame then threw before it reached the player. The cycle length is kept apart, and a fault in the FT8/FT4 receiver can no longer stop the playback.
- FT8/FT4: the radio could be keyed without sound until the hard limit (180 s, then a lockout) when the sound card's queue was full or the card did not open, because a broken playback did not end the lease and the page waited for ever. Now the server ends the lease (`tx_ended`, reason `ft8_playback_failed`), a full queue is waited out for up to a second before that, the pace uses the monotonic clock (a step of the wall clock no longer matters), and the page gives the lease back when the server does not confirm the signal a few seconds after it should have ended. The same for a browser's voice lease when the sound card does not open (`audio_unavailable`).
- The automatic call stopped only after some refusals of its transmit request; now after every one. Ctrl+Alt (keyboard PTT) also stops the FT8/FT4 sequence.
- A full or failing disk no longer ends the server (the saving of the account's radio settings is a task of the whole server) nor skips the clean-up of a closing page, which left the account "logged in" and the transmission to the guard's 2 s silence timeout; the lease is now ended first.
- A session that had expired no longer kept its page open (and able to transmit); the open page ends with it (AGENTS.md rule 16).
- ADIF import of a file between 1 and 5 MB was refused (aiohttp's 1 MiB default); the limit is 5 MB, read as a stream.
- The accounts store is safe against its use from the event loop and worker threads at once (a clash could leave `accounts.json` unusable, which blocks every login); wrong logins no longer fail with an `OverflowError` after about 1000 in a row, and the list of guessed callsigns is bounded.
- `TxAudio.stop()` waited a whole second and left its thread behind when the card's queue was full; it now uses an event, plays what is queued and lets the thread close the card.
- Requests the web transport answers itself (the waterfall, audio, resource and log subscriptions) now go through the permission check too.
- The station log's `-wal` and `-shm` files are created private (0600) like the database; they were readable by the service group.
- Antenna tuner on/off: the G90 toggles the tuner on `1C 01 00` and `1C 01 01` alike (seen 2026-11-08), so restoring the settings at every page refresh flipped it. The server now reads the state, sends the command only when it differs and reads it back (`atu_mismatch`); the simulator toggles the same way.
- The yellow "may be transmitting" bar no longer appears after changing to a silent band: the muted S-meter check starts over after every change of frequency.
- TX log: an antenna tuning in split is logged with the selected (receive) VFO's frequency, where the G90 tunes (measured), not the transmit VFO's.
- A false "transmitting without a lease" warning right after a short transmission from the page: the power meter, read once a second, still showed output just after PTT was released. Output within 2 s after the server's own lease ends is now counted as that lease's (`LEASE_TAIL_S`).

### Safety
- The CLI's PTT test may use the antenna: `remoteg90ctl tx-test --antenna-low-power`, allowed by the server only in SSB, with at most 1 W set and with the whole signal in a band every ITU region has, checked before the lease is granted and again right after (decisions.md D56). `remoteg90ctl setup` offers it next to the dummy load.
- `remoteg90ctl setup` runs the hardware checklist once per radio port and switches transmission on only after an explicit yes at its end (decisions.md D56). Any item can be skipped by typing SKIP; the skip is recorded in the server's state directory, the server warns at start, and the page keeps a warning up (`status.hardware_checklist`). The PTT tests use the ordinary `tx-test` and need a dummy load (D15).
- FT8/FT4 transmission needs the clock's actual error to be at most 0.2 s, read from chrony (`chronyc -c tracking`: |offset| + root delay / 2 + root dispersion); before, only the system's "synchronised" flag counted, and a laptop clock 2.3 s off went unnoticed. Without chrony the flag from `timedatectl`, and without D-Bus the systemd-timesyncd file, count as before. chrony is recommended on the machine by the radio.
- When the guard cannot save its transmission history (`tx_history.json`), it refuses new transmissions and tunings until a save succeeds (tried again at each request, also after the window), and empties the file, so that a restart in the meantime sees a full window instead of the older file without the latest transmissions (AGENTS.md rule 4, review of 2026-11-10, point 10d). Before, only the running guard blocked the window.
- When the playing thread is stuck in the sound card past the stop's wait, what is still queued is thrown away and the card closed from the stop itself: nothing reaches the radio after the lease (a regression of the event-based stop, review of 2026-11-10).
- The radio is held while a transmission or an antenna tuning starts: `set_frequency`, `set_mode`, `set_vfo` and `set_split` are refused (`tx_busy`) between the band and mode check and the keying, and a browser's lease is checked once more after the grant, when the guard refuses every change; an out-of-band, not-U-D/L-D or unreadable radio ends the lease at once (rule 17; before, a change from a second page or from the restore after login could slip in between).
- The guard refuses commands that change the radio's state for 3 s after an `atu_tune`, as during a lease: the radio sends its carrier meanwhile and the tuning cannot be stopped (D43).
- FT8/FT4 and voice leases end when the playback breaks or the sound card does not open (see Fixed); a stopping lease no longer holds up the radio's return to receive for a second.
- A browser transmits only inside the amateur bands of the ITU region of its account's locator (regional ITU allocations, no national footnotes; D54): `tx_request` is refused with `out_of_band` unless the whole signal (3 kHz from the transmit frequency, the other VFO in split) is inside one band, and so is `atu_tune` for the tuning carrier; no locator or no frequency reading also refuses. The CLI dummy-load test is not affected.
- The TX log (AGENTS.md rule 7) defaults to persistent storage next to the guard's lockout state; installing with a TX log on tmpfs is refused.
- FT8/FT4 transmission (D53): every window is an ordinary lease of the page, renewed every 0.5 s; the server makes the signal from the message text and plays it only within that lease, through the limiter, and refuses it without a synchronised clock, with a tone outside 200–2800 Hz, a message that does not fit, or a late cycle. The page stops transmitting after 6 minutes without operator activity (cannot be turned off), on Halt TX, PTT, a hidden page, a lost connection, any refusal or ended lease; enabling transmission is confirmed in a dialog each time.

- Automatic call (D51): each call is an ordinary transmission — its own lease renewed by the page, every guard limit, the TX log, the server's limiter, its own approval every time it is started (frequency, power, length, pause shown). It stops after 2 minutes without the operator touching the page (cannot be turned off), and on Stop, PTT, a hidden or closed page, a lost connection or session, any refusal and any lease ended by the server or the guard. The recording stays in the page.
- Browsers need a logged-in account for everything (AGENTS.md rule 16): without a session there is only the login. The exceptions for browsers without login end — antenna tuning (D43) and transmitting from any browser in the tailnet (D46). One account at a time may have pages open; logging out, a new password, a deleted account or an expired session closes its pages and so ends their transmit lease. Passwords are scrypt hashes, session tokens are kept only as SHA-256 hashes, failed logins are slowed down, POST without Origin is refused, and an unreadable accounts file locks the browser out instead of offering a new first account. The TX log's source names the account.
- Guard: the serial port's descriptor is in the guard's selector, so a reply from the radio wakes the loop at once instead of after up to 50 ms (every CI-V exchange took about 52 ms, the bus was nearly saturated and tuning lagged). Safety loop unchanged: still at least every 50 ms, watchdog only after a full pass (rule 12). If the descriptor cannot be added, or keeps waking the loop with nothing to read (50 times in a row), the guard falls back to reading on its timeout for that open of the port. Owner's decision 2026-11-08.
- Split: the radio transmits on the other VFO, in that VFO's mode (measured). Before every transmission the server reads split from the radio; in split the TX log gets the other VFO's frequency (rule 7), and a browser may transmit only when that VFO is in U-D/L-D. Guard allowlist: `07 00`/`07 01` (select VFO) and `0F 00`/`0F 01` (split) are settings refused during a transmission; `0F`, `25 00`/`25 01` and `26 01` are read only. VFO swap (`07 B0`) is not allowed.
- Guard allowlist: `15 13` (ALC meter, read only, sub-command alone) added; allowed during a transmission like the other meters. `15 15` (supply voltage) stays off the list: the G90 always answers 255.
- Guard allowlist: read and set the preamplifier (`16 02`), the attenuator
  (`11`), the AGC (`16 12`), the noise blanker (`16 22`, level `14 12`) and
  the RF gain (`14 02`), category B, and reading the S-meter (`15 02`). Setting is refused during a
  transmission; none of them transmits.

## [0.4.0rc1] — 2026-11-07

Stage 4 (voice transmit from the browser). Checked on the owner's G90
(firmware 1.81), on the antenna at low power.

### Added

- Voice transmit from the browser (stage 4): a hold-to-talk PTT button
  ("Nadawanie"); the microphone with the browser's speech processing off,
  20 ms frames encoded with WebCodecs (Opus 16-48 kbit/s, or raw on
  localhost) and sent only while the page holds a lease, renewed every 0.5 s.
  The server decodes them with libopus, passes them through its own peak
  limiter (`audio.tx_peak_dbfs`) and plays them to the DE-19.
  PTT also from the keyboard (left Ctrl + left Alt held together); a
  microphone level meter (value twice a second, bar from -25 dBFS four times
  a second) once the microphone is open.

### Safety

- Browsers in the tailnet get the `transmit` permission until login (stage 10,
  an exception in D46 until login); lease, hard limit, duty cycle and TX log
  apply unchanged.
- No sound reaches the radio outside a lease (AGENTS.md section 3): the DE-19
  playback stream is opened when a browser's lease starts and closed when it
  ends, whatever ends it (every change of the active transmission goes through
  one place); frames outside a lease or from another session are dropped.
- A browser may transmit only with the radio in U-D/L-D (`not_data_mode`
  otherwise): in USB the radio would send its own microphone.
- On hardware a browser confirms transmitting on the antenna once per session
  (`antenna_confirmed`) instead of a dummy load; the confirmation only unlocks
  PTT, a new press is needed to transmit.

## [0.3.0rc1] — 2026-11-07

Stage 3 (receive audio in the browser), data modes U-D/L-D, speaker volume,
network usage. Checked on the owner's G90 (firmware 1.81).

### Added

- Network usage in the resource window: this browser's and the whole
  server's WebSocket traffic, split into waterfall, audio and other messages,
  as a current rate, a total since connecting and an estimate per month at
  the current rate (for metered mobile links).
- Receive audio in the browser (stage 3). The server opens the DE-19's own
  sound card (`[audio]`, capture only, stable name `G90AUDIO` from
  `deploy/udev/92-remoteg90ctl-audio.rules`), sets its capture gain and
  switches its AGC off, cuts 20 ms frames at 48 kHz and sends them to one
  browser at a time (a second gets `audio_busy`) as Opus at 16, 24, 32 or
  48 kbit/s, or as raw PCM for use on localhost. Opus is encoded through the
  system libopus (ctypes) and decoded by the browser's WebCodecs; playback in an
  AudioWorklet with a 150 ms buffer and clock-drift correction, flushed when the
  radio is retuned. A listening box: listen/stop, quality, volume, buffer state.
  The G90 sends audio to the DE-19 only in U-D/L-D (`radio.ssb_data_mode`).
- Data modes U-D and L-D (CI-V `26 00`): the mode is read with its data flag,
  so the browser header shows U-D/L-D, and the mode list offers them. With
  `radio.ssb_data_mode = true` choosing USB or LSB sets U-D or L-D, because
  only then does the G90 send receive audio to the DE-19 (measured). The I/Q
  offset and passband of USB/LSB apply to U-D/L-D.
- Speaker volume over CAT (`14 01`): `remoteg90ctl af [0-255]`, `get_af` and
  `set_af` requests. With the new `radio.mute_speaker = true` the server sets
  the volume to 0 whenever it connects to the guard, so a remote station does
  not play into the room.

### Safety

- The allowlist gains `26 00`: read (sub-command alone) and set with exactly
  mode, data flag and filter, refused during TX. It changes the mode only; no
  transmission.
- The allowlist gains `14 01` read (sub-command alone) and write (2 bytes,
  refused during TX): the speaker volume, category B, no transmission.

## [0.2.0rc1] — 2026-11-04

Stages 0 and 1. Verified only against the built-in simulator; nothing has been
run against a real radio yet.

### Added

- Configuration loading and validation (TOML), structured logging.
- CI-V frame codec (BCD frequency, modes) and a G90 radio simulator.
- PTT guard process: exclusive owner of the serial port, CI-V allowlist,
  transmit lease, persistent lockout, TX log, systemd watchdog.
- Server process: CAT (frequency, mode) and transmit lease relay.
- `remoteg90ctl` CLI: `status`, `freq`, `mode`, `tx-test`, `tx-unlock`,
  `port-open`, `--version`.
- systemd units and install, uninstall and service scripts.
- Program version in every TX log record, in the guard and server `status`
  responses and in `remoteg90ctl status`, which warns when the CLI, server and
  guard versions differ.
- CI on GitHub Actions: tests, lint, format and type checks.
- Documentation: G90 CI-V command reference (`docs/civ-g90.md`), install guide
  with hardware checklist, protocol descriptions, design decisions.

- I/Q input for the waterfall (stage 2a): an `[iq]` section with a synthetic,
  ALSA or WAV-file source; I/Q mapping, DC removal and balance/phase
  correction; spectrum lines at a fixed rate (Nuttall window, dBFS). The
  server opens the I/Q card by its udev name only (decisions.md D35) and
  reports the input in `status`.
- `remoteg90ctl iq status | record | analyze`: recordings through the server
  and their offline analysis (level, DC, balance, noise floor, strongest
  signals, image rejection) for the measurements of `AGENTS.md` section 8.
- Per-request permissions (receive, control, transmit) with a `forbidden`
  error; Unix-socket clients keep every permission until stage 5.
- `deploy/udev/` and `deploy/wireplumber/` templates for the I/Q card.
- Waterfall in the browser (stage 2b): `aiohttp` web server on the loopback
  addresses of `server.listen` (`http://127.0.0.1:8078`); spectrum, frequency
  scale following the VFO (read every 0.5 s while a browser watches), zoom and
  pan with mouse and touch, automatic or manual levels. Binary waterfall
  frames and the WebSocket protocol are in `docs/server-protocol.md`.
- The web server accepts only its own `Host` and `Origin` (decisions.md D34);
  browser sessions can watch and tune but never transmit.
- `radio_state` event when the known frequency or mode changes.
- Passband sliders in the browser: they move the shaded passband of the
  current mode to match the filter set in the radio (display only, stored in
  the browser per mode; the radio's filter cannot be read or set over CAT).
  `radio_state` carries the configured `passband_hz`.
- Receive passband of the current mode shaded on the spectrum and waterfall,
  from `[iq.passband_hz]` (the G90 does not report its filter over CAT).
- Example configuration samples the I/Q card at 96 kHz (8192 bins): the G90's
  I/Q output reaches about ±40 kHz, measured on the radio.
- `[iq.center_offset_hz]`: the I/Q centre offset per mode (measured on the
  G90: CW/CW-R move by the CW pitch, AM/FM by 100 Hz).
- The I/Q service reopens a source that stays open but delivers no data for
  2 s (seen after a USB reset of the card), instead of reporting it as running.
- Tuning from the browser (stage 2c): click or tap on the spectrum or
  waterfall tunes the radio there, snapped to a step (auto: 10 Hz in CW,
  100 Hz otherwise; 10 Hz, 100 Hz or 1 kHz to choose); a frequency field and a
  mode selector. The server coalesces bursts of `set_frequency` requests: one
  CAT command at a time, the newest target always sent.
- Waterfall lines taken while this server transmits, and for 0.5 s after (the
  I/Q output clicks), are flagged as blanked, drawn dimmed and left out of the
  automatic levels.
- A "remove artefacts" switch in the browser: removes the steady line at the
  centre of the spectrum and waterfall (0 Hz of the I/Q), display only.
  Switching it on learns the line under the current conditions for about two
  seconds; it keeps adapting slowly, ignores signals passing through and
  blanked (TX) lines, and keeps a real signal at the centre that is stronger
  than the line. The cleaned bins take the background noise of the same line
  (from 1-2 kHz off the centre), so they match the level and texture of the
  band instead of a dark strip. A signal sitting on the centre is not learned
  as the line: the line's model learns slowly and only from values close to
  its usual level, and a bin counts as a signal by how much the line itself
  flickers (at least 3 dB) and only after two consecutive lines, so the line's
  single-line jumps (about once a second on the G90) do not leave dots.
  Remembered per browser. Frontend tests run in CI (`npm test`).
- Resource window in the browser ("zasoby" button): CPU and memory of the
  server and the guard, plus CPU, load and memory of the machine, every 2 s.
  The server reads `/proc` only while a window is open (`system_subscribe` /
  `system_unsubscribe` on the WebSocket, `system_stats` events); closing the
  window stops the monitoring.

- Waterfall speed for slow links (decisions.md D40): `waterfall_subscribe`
  takes an optional `speed` — `fast` (every line, as before), `medium` (half the
  lines) or `slow` (a quarter); merged lines keep the per-bin maximum, so short
  signals stay visible. A "tempo" selector in the browser, remembered per
  browser.

- RF power and TX from the radio's power meter (CI-V `15 11`, decisions.md
  D33), read every second whether or not a browser is open:
  - the browser header shows the power, in watts when
    `radio.power_meter_watts` holds calibration points measured on the radio,
    otherwise the meter value;
  - `radio_power` and `radio_transmitting` events, a TX badge, and a warning
    (log `tx_without_lease`, red banner) when the radio emits power without a
    lease from the server, e.g. microphone PTT, TUNE or VOX;
  - a transmission ends 3 s after the last reading with power, so pauses in
    speech do not split it; waterfall lines during it are dimmed;
  - a "high SWR" banner (log `high_swr`) after two readings in a row at the
    end of the SWR meter's scale while emitting, i.e. SWR about 2 or more;
  - the browser header shows the RF power setting in watts (`14 0A`); the
    power meter only tells that the radio emits, as it ends its scale at
    about 3 W;
  - `remoteg90ctl power [--watch]` and the `get_power_meter` request give the
    meter level, watts, raw reply and the power setting (`14 0A`). The G90
    sends 0-255 levels with a wrong last BCD digit (decoded as Hamlib does);
    measured on the radio: 0 in receive and with PTT but no modulation, 171 at
    1 W, 241 at 2 W and the end of the scale (255) from about 3 W, so above
    2 W the browser shows "> 2.0 W" next to the power setting.

- Antenna tuner from the browser and the CLI: an "ATU" switch and a
  "Strojenie" button (with a confirmation) in the radio controls,
  `remoteg90ctl atu state|on|off|tune`, `get_atu`, `set_atu` and `atu_tune`
  requests and the `radio_atu` event.

- An "off" setting in the waterfall speed selector: the page unsubscribes, so
  the server sends no spectrum lines at all and the link is spared; the
  frequency, mode and tuner state still follow the radio (read every 0.5 s while
  any browser is connected, no longer only while one shows the waterfall).
- ATU badge above the waterfall: green when the radio's antenna tuner is on,
  crossed out when off, dashed until the radio has answered. Read when the
  page connects and with the VFO while a browser watches.

### Changed

- The server unit may use ALSA devices (group `audio`) instead of
  `PrivateDevices=yes`, may use IP on localhost only (instead of
  `PrivateNetwork=yes`) for the browser, and has a recordings directory
  (`/var/lib/remoteg90ctl-server/recordings`). New dependencies: `numpy`,
  `sounddevice` (needs the system `libportaudio2`), `aiohttp`; the frontend is
  built with TypeScript (development only, `web/`).

- `config/example.toml`: `radio_address = 0x70` and notes on 19200 baud and
  RTS as the PTT line, as measured on the owner's G90 (firmware 1.81) with the
  DE-19.
- `docs/install.md`: results of the first hardware checklist run, laptop
  suspend blocking, ModemManager and brltty handling for the DE-19 (CH340), and
  starting the guard only with the radio in SSB.

### Removed

- TX detection from the I/Q level (decisions.md D33), with its
  `radio_transmitting` event, `tx_without_lease` warning, browser banner and
  `iq.tx_detect_drop_db`. On the radio it took band changes and other level
  changes for transmissions (issue #30); the radio's power meter replaces it. **Remove `tx_detect_drop_db` from an installed
  configuration before upgrading**: an unknown key stops the server.

### Fixed

- Setting the RF power set 1 W less than asked for every value but 1 and 20 W
  (`rf_power_mismatch`, e.g. 5 W gave 4 W). The G90 sets whole watts and
  rounds a written level down (53 is 4.95 W, so 4 W), then reports the level
  of those watts, also rounded down. The server now writes the smallest level
  of the requested watts (5 W: 54, read back as 53) and compares watts, not
  levels. Only whole watts are accepted, and the browser's power field steps
  by 1 W and sets the radio as soon as it changes (arrows at once, typing
  after a 0.6 s pause; Enter no longer needed). The simulator models the
  measured behaviour.
- A frequency reply or broadcast whose field is not exactly 5 bytes is now
  rejected as a bad reply. On the G90 a byte was seen to go missing on the
  CI-V bus, and a shortened field used to decode silently to a wrong frequency.
- `tx-test` reports a lockout or another blocker before printing the
  "the radio will TRANSMIT" warning.
- Setting the mode now sends the filter byte the radio reported (read with
  0x04 just before). The G90 (firmware 1.81) refuses a mode without it, so
  `remoteg90ctl mode LSB` failed with `radio_rejected` on the real radio. The
  simulator now refuses a mode without the filter byte as well.
- `tx-test` reports why the system ended the transmission (e.g.
  `server_shutdown`) even when its renewal crossed the end and was answered
  with `unknown_lease` before the `tx_ended` event arrived.

### Safety

- **Setting the RF power (decisions.md D44).** `set_rf_power` (browser:
  "moc" field; CLI: `remoteg90ctl power --set W`) sets `14 0A`, which is now
  on the allowlist with exactly 2 data bytes and refused during a transmission.
  The guard does not read the value: the limit is the new optional
  `safety.max_rf_power_w` (1-20 W), checked by the server; without it setting
  the power is refused. How the G90 decodes a written level is UNVERIFIED, so
  the server reads the setting back: on a mismatch the request fails, and a
  setting above the limit is lowered to the minimum at once.
- **Antenna tuning (decisions.md D43, owner's exception to D26).** A tuning
  (`1C 01 02`) transmits a carrier for about 1.5 s that cannot be stopped. It
  exists only as the guard's `atu_tune`: the conditions of `tx_request` (no
  lease or lockout, confirmed identity, duty cycle) with its own switch
  `safety.allow_atu_tune` instead of `allow_transmit` (default false), at
  most one tuning per 10 s, counted 3 s towards the duty cycle, blocking any
  other transmission for those 3 s, and logged in the TX log before the frame
  is sent. The allowlist gains only the exact frames `1C 01` (read),
  `1C 01 00` and `1C 01 01` (tuner off, on); `1C 01 02` and PTT `1C 00` stay
  refused. Browser sessions get a narrow `tune` permission until stage 5
  (still no PTT), and the page asks before tuning.
- The guard's allowlist gains one read-only entry: `15 11`, the RF power
  meter, as the sub-command alone with no data (owner's decision, 2026-11-03).
  It is allowed during TX. It replaces the I/Q level as the way to see
  transmissions the guard did not key (decisions.md D33). The server reads it
  every second; this is informational and no safety path depends on it.
- A read-only entry for `15 12`, the SWR meter (sub-command alone), read
  every second with the power meter. Its scale ends at about SWR 2 on the
  G90 (measured: level 100 at 1.1, 220 at 1.5, 255 at 2.0), so it only feeds
  a "high SWR" warning; informational, no safety path depends on it.
- A second read-only entry: `14 0A`, the RF power setting, as the sub-command
  alone. With data it would set the power, so any longer body is refused.
  Shown next to the meter, which ends its scale at about 3 W on the G90.
- `server.allowed_hosts` (decisions.md D38): names under which a loopback
  HTTPS proxy such as `tailscale serve` presents the page; accepted only with
  an `https://` Origin. The server still listens on loopback only (rule 15)
  and browser sessions still cannot transmit. The page uses `wss` over HTTPS.
- Menu items on the allowlist (decisions.md D37): for `1A 05` the 2-byte item
  number is part of the sub-command, and each item's read and write are
  separate entries. No item is allowed: the RX filter item (`1A 05 00 10`) is
  answered NG by the G90 (firmware 1.81) both when read and when written.
- The guard enforces a duty cycle (decisions.md D31): at most
  `safety.duty_max_tx_s` (≤ 300 s) of transmission within any
  `safety.duty_window_s` (≥ 600 s). Reaching it ends the transmission
  (`duty_limit`); new requests are refused until the window has room, without
  `tx_unlock`. The history survives restarts (`tx_history.json`); an
  unreadable or unsaved history, or one from the future, means a full window.
  A transmission interrupted by a crash counts until the restart. **Both keys
  are required: add them to an existing configuration before upgrading.**
  Renewal responses report the time left to the nearer of the hard limit and
  the duty cycle; `remoteg90ctl status` shows the duty cycle.
- The guard checks the radio identity after every safe port open (decisions.md
  D32): it sends `1D 19` and compares the body of the answer with the new
  optional `radio.identity` (`"190090"` on the owner's G90, firmware 1.81). Until the answer matches, transmitting is blocked; CAT
  keeps working. Without `radio.identity` the guard starts with transmit
  blocked and reports what the radio answered. `remoteg90ctl status` shows the
  identity state.
- Transmit is keyed only by the guard, on the DTR or RTS line; PTT over CI-V
  is not implemented and `ptt_line = "civ"` starts with transmit blocked.
- DTR and RTS are set inactive before the port is opened, checked on every
  loop iteration and lowered on every exit path; HUPCL is set.
- Transmit lease: renewal every 0.5 s, release after 2 s without renewal,
  hard limit of 180 s followed by a persistent lockout cleared only by
  `tx-unlock`.
- A restart never resumes transmission; an unclean restart during transmit
  leaves transmit locked.
- Every CI-V message must be exactly one well-formed frame on the allowlist
  (read and set frequency and mode only).
- The G90 has no transmit time-out timer and its VOX bypasses the guard; both
  are covered by the hardware checklist.
