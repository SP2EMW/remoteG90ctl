# Contributing to remoteG90ctl

Thank you for your interest. This program keys a radio transmitter, so a few
rules are stricter than in most projects.

## Before you write code

- **Read [AGENTS.md](AGENTS.md)** — the hard safety rules (section 2) apply to
  every contribution, from people and from AI coding agents alike.
- **Anything that can affect transmitting** — the guard, the lease, PTT, the
  CI-V allowlist, audio to the radio, band limits, timing limits — starts with
  an issue describing the change. Such changes are decided by the maintainer.
- **New CI-V commands** need their category from
  [docs/civ-g90.md](docs/civ-g90.md) and a measurement on a real radio
  (firmware version, request and reply). Do not add commands from guesses.
- **Reports about other radios, firmware versions or interfaces** are very
  welcome as issues, with measurements. They do not change the defaults until
  confirmed (AGENTS.md rule 10).

## Pull requests

- One change per branch and pull request, against `main`. `main` is the
  development branch; users install releases (AGENTS.md section 11).
- All checks of AGENTS.md section 7 must pass; CI runs them.
- Tests use the simulator only. Never add a test that opens a real serial
  port or sound card.
- Changes to the guard or a safety rule include tests of the failure paths:
  client disconnect, exception, signal, limit exceeded.
- User-visible changes get an entry in `CHANGELOG.md` under `[Unreleased]`;
  changes touching transmitting go under **Safety**.
- Code, comments, commit messages and documentation are in English. Source
  files carry `SPDX-License-Identifier: GPL-3.0-or-later`.
- Code copied from another project: only under a GPL-3.0-compatible licence,
  with the author, licence and source in the file and in
  [docs/third-party.md](docs/third-party.md). Transmit paths are never copied.

## AI-assisted contributions

Welcome, under the same rules. You are responsible for what you submit: you
must understand the change and have run the checks. Point your agent at
`AGENTS.md`.

## Testing on a radio

If you test on your own G90, start with a dummy load, low power, VOX off and
`safety.allow_transmit = false` until CAT works, and follow the hardware
checklist in [docs/install.md](docs/install.md). Never test transmitting on a
frequency or with a power you are not licensed for.

## Licence

By contributing you agree that your contribution is licensed under
GPL-3.0-or-later.
