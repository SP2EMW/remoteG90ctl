# Hardware

remoteG90ctl was built and tested with this set only. Measured facts are about
one G90 with firmware 1.81: [g90-measurements.md](g90-measurements.md).

| Part | What it is | Notes |
|---|---|---|
| **Xiegu G90** | the transceiver, firmware 1.81 | CI-V address 0x70, 19200 baud; VOX off (it would bypass the guard) |
| **Xiegu DE-19** | the radio's interface: USB serial (CH340) for CI-V and PTT, and a USB sound card (C-Media) for the radio's audio | PTT is its **RTS** line; every opening of the port keys the radio for a moment — start the guard with the radio in SSB |
| **USB sound card for I/Q** | StarTech ICUSBAUDIO2D (C-Media, "Speed Dragon"), stereo at 96 kHz | the G90's rear I/Q output → the waterfall, about ±40 kHz around the VFO |
| **Computer** | tested on a **Fujitsu Futro S740** with **DietPi (Debian 13 "trixie")** | another small computer with Debian may work; not tested |

## Connections

- **CI-V:** the DE-19's CI-V cable into the G90's **IO/User** socket (side of
  the front panel). Never into the rear COMM socket: the G90's manual warns it
  can put the radio into firmware-update mode.
- **Audio and PTT:** the DE-19's cable into the G90's rear **ACC** socket.
- **I/Q:** a stereo cable from the G90's rear **I/Q** socket to the I/Q
  card's microphone input.
- **USB:** the DE-19 and the I/Q card to the computer.

## Names the configuration uses

| Device | Name |
|---|---|
| CI-V port | `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` — the same for every CH340: do not plug in a second one |
| DE-19 sound card | ALSA `hw:CARD=G90AUDIO,DEV=0` (udev rule, [install.md](install.md)) |
| I/Q card | ALSA `hw:CARD=G90IQ,DEV=0` (udev rule, [install.md](install.md)) |

Before the first connection, go through the hardware checklist in
[install.md](install.md), section 5.
