#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install or update remoteG90ctl from a local checkout as systemd services.
#
#   sudo deploy/install.sh            # install/update, services not started
#   sudo deploy/install.sh --start    # ... and enable + (re)start both services
#
# Safe to run repeatedly:
#   - an existing /etc/remoteg90ctl/config.toml is never overwritten
#     (a fresh install gets config/example.toml: backend = "simulator");
#   - /opt/remoteg90ctl is cloned from the checkout's committed state on the
#     current branch, or fast-forwarded on later runs (uncommitted changes are
#     NOT installed); a checkout of a release tag (install.sh's default)
#     installs that release;
#   - the persistent TX lockout and the TX log are left untouched.

set -euo pipefail

APP_USER=remoteg90ctl
PREFIX=/opt/remoteg90ctl
VENV=$PREFIX/venv
CONFIG_DIR=/etc/remoteg90ctl
UNIT_DIR=/etc/systemd/system
UNITS=(remoteg90ctl-guard.service remoteg90ctl-server.service)

START=false
for arg in "$@"; do
    case "$arg" in
        --start) START=true ;;
        -h | --help)
            sed -n '4,17p' "$0"
            exit 0
            ;;
        *)
            echo "unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

say() { printf '\n==> %s\n' "$*"; }
die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

# A long, quiet step: its name with a running time, so it is clear the
# installer is working; its output is shown only when it fails, and kept in
# install.sh's log. (The same function is in install.sh and deploy/install.sh.)
busy() {
    local what=$1 out pid start status=0 progress=""
    shift
    start=$SECONDS
    # The running time goes straight to the terminal (fd 3 while install.sh
    # copies its output to the log), so the log gets only the result.
    if [[ ${REMOTEG90CTL_PROGRESS:-} == 1 ]] && { true >&3; } 2>/dev/null; then
        progress=3
    elif [[ -t 1 ]]; then
        progress=1
    fi
    [[ -n $progress ]] || echo "  $what …"
    out=$(mktemp)
    "$@" >"$out" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        [[ -n $progress ]] && printf '\r  %s … %ds ' "$what" $((SECONDS - start)) >&"$progress"
        sleep 0.2
    done
    wait "$pid" || status=$?
    [[ -n $progress ]] && printf '\r\033[K' >&"$progress"
    if [[ -n ${REMOTEG90CTL_LOG:-} ]]; then
        { echo "---- $what: exit $status, output:"; cat "$out"; echo "----"; } >>"$REMOTEG90CTL_LOG"
    fi
    if ((status)); then
        echo "  $what … FAILED after $((SECONDS - start))s:"
        tail -n 30 "$out" >&2
        rm -f "$out"
        die "$what failed"
    fi
    echo "  $what … done ($((SECONDS - start))s)"
    rm -f "$out"
}

[[ $EUID -eq 0 ]] || die "run with sudo"
INVOKER=${SUDO_USER:-}
[[ -n $INVOKER && $INVOKER != root ]] || die "run with sudo from your normal user account"

SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[[ -f $SRC/pyproject.toml && -d $SRC/.git ]] || die "$SRC is not a remoteG90ctl checkout"

# uv is usually installed per user (~/.local/bin), outside sudo's PATH.
INVOKER_HOME=$(getent passwd "$INVOKER" | cut -d: -f6)
UV=${UV:-$(command -v uv || true)}
if [[ -z $UV && -x $INVOKER_HOME/.local/bin/uv ]]; then
    UV=$INVOKER_HOME/.local/bin/uv
fi
[[ -n $UV && -x $UV ]] || die "uv not found; pass it explicitly: sudo UV=/path/to/uv $0"


# The checkout belongs to another user; allow git to read it as root.
GIT=(git -c "safe.directory=$SRC" -c "safe.directory=$SRC/.git")
BRANCH=$("${GIT[@]}" -C "$SRC" rev-parse --abbrev-ref HEAD)
TAG=""
if [[ $BRANCH == HEAD ]]; then
    # Detached: install.sh checks out a release tag.
    TAG=$("${GIT[@]}" -C "$SRC" describe --tags --exact-match HEAD 2>/dev/null) ||
        die "$SRC is not on a branch or a release tag; check one out first"
fi
if [[ -n $("${GIT[@]}" -C "$SRC" status --porcelain --untracked-files=no) ]]; then
    echo "WARNING: $SRC has uncommitted changes; they will NOT be installed." >&2
fi

say "Service user $APP_USER"
if id "$APP_USER" >/dev/null 2>&1; then
    echo "exists"
else
    useradd --system --user-group --no-create-home --shell /usr/sbin/nologin "$APP_USER"
    echo "created"
fi
if id -nG "$INVOKER" | tr ' ' '\n' | grep -qx "$APP_USER"; then
    echo "$INVOKER is already in group $APP_USER"
else
    usermod -aG "$APP_USER" "$INVOKER"
    echo "added $INVOKER to group $APP_USER (effective after the next login)"
fi

if [[ -n $TAG ]]; then
    say "Program in $PREFIX (release $TAG)"
else
    say "Program in $PREFIX (branch $BRANCH)"
fi
if [[ -d $PREFIX/.git && -n $TAG ]]; then
    "${GIT[@]}" -C "$PREFIX" fetch --quiet --force --tags origin
    "${GIT[@]}" -C "$PREFIX" checkout --quiet --detach "refs/tags/$TAG"
    echo "updated to $TAG"
elif [[ -d $PREFIX/.git ]]; then
    "${GIT[@]}" -C "$PREFIX" fetch --quiet origin "$BRANCH"
    "${GIT[@]}" -C "$PREFIX" checkout --quiet "$BRANCH"
    "${GIT[@]}" -C "$PREFIX" merge --quiet --ff-only "origin/$BRANCH"
    echo "updated to $("${GIT[@]}" -C "$PREFIX" rev-parse --short HEAD)"
elif [[ -e $PREFIX ]]; then
    die "$PREFIX exists but is not a git checkout; remove it first"
else
    "${GIT[@]}" -c advice.detachedHead=false clone --quiet --branch "${TAG:-$BRANCH}" "$SRC" "$PREFIX"
    echo "cloned $("${GIT[@]}" -C "$PREFIX" rev-parse --short HEAD)"
fi

say "Python environment in $VENV"
# A Python that uv has to download (Debian 12 has 3.11, the project needs
# 3.12+; Debian 13 has 3.13; DietPi has none) goes under $PREFIX, where the
# service user can read it, not under /root. No package cache is kept: the
# machine by the radio runs from a small 8 GB disk.
# Only the Python versions CI tests: the system's when it has one, otherwise
# the newest of them downloaded. A newer Python (uv would pick the newest) can
# lack ready-made packages of a dependency (ft8lib), which then needs a C
# compiler the computer by the radio does not have.
PYTHON_VERSIONS=">=3.12,<3.14"
sync_environment() {
    cd "$PREFIX" && UV_PROJECT_ENVIRONMENT=$VENV UV_PYTHON_INSTALL_DIR=$PREFIX/python \
        UV_PYTHON=$PYTHON_VERSIONS UV_NO_CACHE=1 "$UV" sync --no-dev --frozen
}
busy "Python and libraries (a few minutes the first time)" sync_environment
for exe in remoteg90ctl remoteg90ctl-guard remoteg90ctl-server; do
    [[ -x $VENV/bin/$exe ]] || die "$VENV/bin/$exe missing after uv sync"
done
echo "$("$VENV/bin/python" -I -c 'import sys; print("Python", sys.version.split()[0])')"

say "System libraries"
# The exact checks the server depends on: sounddevice needs PortAudio (I/Q
# input and audio), the Opus codec is loaded from libopus, amixer sets the
# DE-19 card's capture gain.
missing=()
"$VENV/bin/python" -I -c 'import sounddevice' 2>/dev/null || missing+=(libportaudio2)
"$VENV/bin/python" -I -c '
import sys
from remoteg90ctl.audio.opus import OpusEncoder, OpusUnavailableError
try:
    OpusEncoder(48000, 24000).close()
except OpusUnavailableError:
    sys.exit(1)
' 2>/dev/null || missing+=(libopus0)
command -v amixer >/dev/null || missing+=(alsa-utils)
if ((${#missing[@]})); then
    die "missing system packages; install them first: sudo apt install ${missing[*]}"
fi
echo "ok"

say "Configuration in $CONFIG_DIR"
install -d -m 0750 -o root -g "$APP_USER" "$CONFIG_DIR"
if [[ -e $CONFIG_DIR/config.toml ]]; then
    echo "config.toml exists; left unchanged"
else
    install -m 0640 -o root -g "$APP_USER" "$PREFIX/config/example.toml" "$CONFIG_DIR/config.toml"
    echo "installed config/example.toml (backend = \"simulator\")"
fi
# Refuse to continue with a configuration the programs would reject anyway.
if ! "$VENV/bin/python" -I -c '
import sys
from pathlib import Path
from remoteg90ctl.config import ConfigError, load_config
try:
    c = load_config(Path(sys.argv[1]))
except ConfigError as e:
    print(e, file=sys.stderr)
    sys.exit(1)
print(f"valid: backend = {c.radio.backend}, allow_transmit = {c.safety.allow_transmit}")
' "$CONFIG_DIR/config.toml"; then
    die "configuration is invalid"
fi
# The TX log is a safety record (AGENTS.md rule 7): never in RAM. DietPi keeps
# /var/log on tmpfs and clears it hourly.
TX_LOG=$("$VENV/bin/python" -I -c '
import sys
from pathlib import Path
from remoteg90ctl.config import load_config
print(load_config(Path(sys.argv[1])).guard.tx_log_path)
' "$CONFIG_DIR/config.toml")
TX_LOG_DIR=$(dirname "$TX_LOG")
probe=$TX_LOG_DIR
while [[ ! -e $probe ]]; do probe=$(dirname "$probe"); done
if [[ $(findmnt -n -o FSTYPE --target "$probe") == tmpfs ]]; then
    die "guard.tx_log_path ($TX_LOG) is on tmpfs (RAM); the TX log would be lost.
Set tx_log_path = \"/var/lib/remoteg90ctl/tx.jsonl\" in $CONFIG_DIR/config.toml
(and move an existing log there: sudo mv $TX_LOG /var/lib/remoteg90ctl/)."
fi
echo "TX log: $TX_LOG"

say "systemd units"
for unit in "${UNITS[@]}"; do
    install -m 0644 "$PREFIX/deploy/systemd/$unit" "$UNIT_DIR/$unit"
    echo "installed $UNIT_DIR/$unit"
done
systemctl daemon-reload

say "CLI"
ln -sfn "$VENV/bin/remoteg90ctl" /usr/local/bin/remoteg90ctl
echo "/usr/local/bin/remoteg90ctl -> $VENV/bin/remoteg90ctl"

if $START; then
    say "Starting services"
    systemctl enable --quiet "${UNITS[@]}"
    systemctl restart remoteg90ctl-guard.service
    systemctl restart remoteg90ctl-server.service
    sleep 2
    systemctl --no-pager --lines=0 status "${UNITS[@]}" || true
fi

say "Done"
[[ -n ${REMOTEG90CTL_NO_NEXT_STEPS:-} ]] && exit 0
cat <<EOF
Next steps:
  sudo systemctl enable --now ${UNITS[*]}     (if not started with --start)
  sg $APP_USER -c "remoteg90ctl status"            (until you log in again)
  journalctl -u remoteg90ctl-guard -u remoteg90ctl-server -f
EOF
