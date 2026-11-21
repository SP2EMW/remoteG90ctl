#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Restore remoteG90ctl's state from an archive made by deploy/backup.sh:
#
#   sudo deploy/restore.sh FILE.tgz
#
# Run after deploy/install.sh (it creates the service user), with both
# services stopped. The configuration replaces /etc/remoteg90ctl/config.toml
# (the old one is kept as config.toml.before-restore); the server's and the
# guard's state go to the services' own state directories, the only ones the
# archive's configuration may name. State already present is not overwritten:
# the restore then stops (remove it first, knowingly). A TX log path on tmpfs
# (DietPi keeps /var/log in RAM) is changed to the guard's state directory, in
# the configuration too (AGENTS.md rule 7).
#
# An archive from someone else is as trusted as their script run as root: only
# restore your own. Even so the restore takes only regular files with the known
# names, into the fixed directories, and refuses links and special files
# (security review 2026-11-14, Z2).

set -euo pipefail

PREFIX=/opt/remoteg90ctl
VENV=$PREFIX/venv
CONFIG_DIR=/etc/remoteg90ctl
CONFIG=$CONFIG_DIR/config.toml
APP_USER=remoteg90ctl
UNITS=(remoteg90ctl-server.service remoteg90ctl-guard.service)
# The units' StateDirectory= and LogsDirectory=: nothing else is restored to.
GUARD_STATE=/var/lib/remoteg90ctl
SERVER_STATE=/var/lib/remoteg90ctl-server
LOG_DIR=/var/log/remoteg90ctl
# The files the guard and the server keep there (guard/state.py, accounts.py,
# logbook.py, checklist_record.py, mixer.py).
GUARD_FILES=(lockout.json running.json tx_history.json)
SERVER_FILES=(accounts.json sessions.json logbook.sqlite hardware-checklist.json audio-output.json)

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}
# The archive holds only directories and regular files (no links, devices).
check_archive() {
    local listing
    listing=$(tar -tvzf "$1") || return 1
    ! grep -qv '^[-d]' <<<"$listing"
}

# Unpack only the files a backup has (the names restored below), not every
# entry: GNU tar may unpack entries its listing did not show (CVE-2026-5704;
# dependency audit 2026-11-15), so check_archive alone is not enough.
extract_known() {
    local archive=$1 into=$2 name
    local -a wanted=(./manifest.txt ./config.toml) members=()
    for name in "${GUARD_FILES[@]}"; do wanted+=("./guard/$name"); done
    for name in "${SERVER_FILES[@]}"; do wanted+=("./server/$name"); done
    local listing
    listing=$(tar -tzf "$archive") || return 1
    for name in "${wanted[@]}"; do
        if grep -qxF -- "$name" <<<"$listing"; then members+=("$name"); fi
    done
    (( ${#members[@]} > 0 )) || return 1
    tar --no-same-owner --no-same-permissions -C "$into" -xzf "$archive" -- "${members[@]}"
}

# A file of the unpacked archive: its path if it is a regular file, nothing if
# it is absent; a link or anything else stops the restore.
staged_file() {
    local path=$1
    if [[ -L $path ]] || { [[ -e $path ]] && [[ ! -f $path ]]; }; then
        die "$(basename "$path") in the archive is not a regular file"
    fi
    if [[ -f $path ]]; then printf '%s\n' "$path"; fi
    return 0
}

# The three paths from the restored configuration (paths(), below), one per
# line and exactly three: a path with a newline in it cannot pass for several
# (audit 2026-11-15, A1; load_config also refuses it).
read_paths() {
    local -a PATHS
    mapfile -t PATHS < <(paths "$STAGE/config.toml") || true
    (( ${#PATHS[@]} == 3 )) && [[ -n ${PATHS[2]} ]] || die "the restored configuration cannot be used here"
    GUARD_DIR=${PATHS[0]} TX_LOG=${PATHS[1]} SERVER_DIR=${PATHS[2]}
}

# Sourced (the tests): the functions only.
(return 0 2>/dev/null) && return 0

case "${1:-}" in
    "" | -h | --help)
        sed -n '4,20p' "$0"
        exit 0
        ;;
esac
[[ $EUID -eq 0 ]] || die "run with sudo"
ARCHIVE=$1
[[ -f $ARCHIVE ]] || die "$ARCHIVE not found"
[[ -x $VENV/bin/python ]] || die "no installation in $PREFIX: run deploy/install.sh first"
id "$APP_USER" >/dev/null 2>&1 || die "no user $APP_USER: run deploy/install.sh first"
for unit in "${UNITS[@]}"; do
    if systemctl is-active --quiet "$unit"; then
        die "$unit is running; stop it first: sudo systemctl stop ${UNITS[*]}"
    fi
done

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
check_archive "$ARCHIVE" || die "$ARCHIVE holds links or special files: not a remoteG90ctl backup"
extract_known "$ARCHIVE" "$STAGE" || die "$ARCHIVE is not a remoteG90ctl backup"
[[ -n $(staged_file "$STAGE/manifest.txt") && -n $(staged_file "$STAGE/config.toml") ]] ||
    die "$ARCHIVE is not a remoteG90ctl backup"
sed -n 's/^/  /p' "$STAGE/manifest.txt"

# Paths from the restored configuration, which must also be valid here.
paths() {
    "$VENV/bin/python" -I -c '
import sys
from pathlib import Path
from remoteg90ctl.config import ConfigError, load_config
try:
    c = load_config(Path(sys.argv[1]))
except ConfigError as e:
    sys.exit(f"the restored configuration is invalid here: {e}")
for path in (c.guard.state_dir, c.guard.tx_log_path, c.server.state_dir):
    print(path)
' "$1"
}
read_paths
[[ $GUARD_DIR == "$GUARD_STATE" && $SERVER_DIR == "$SERVER_STATE" ]] ||
    die "the archive's configuration keeps the state in $GUARD_DIR and $SERVER_DIR; only $GUARD_STATE and $SERVER_STATE are restored (the services' state directories)"
tx_log_allowed() {
    local dir name
    dir=$(dirname "$1") name=$(basename "$1")
    [[ $dir == "$GUARD_STATE" || $dir == "$LOG_DIR" ]] || return 1
    [[ " ${GUARD_FILES[*]} " != *" $name "* ]]
}
tx_log_allowed "$TX_LOG" || die "the archive's TX log path $TX_LOG is not a file in $GUARD_STATE or $LOG_DIR"

on_tmpfs() {
    local probe=$1
    while [[ ! -e $probe ]]; do probe=$(dirname "$probe"); done
    [[ $(findmnt -n -o FSTYPE --target "$probe") == tmpfs ]]
}
if on_tmpfs "$(dirname "$TX_LOG")"; then
    NEW_LOG=$GUARD_DIR/tx.jsonl
    echo "TX log path $TX_LOG is on tmpfs here: using $NEW_LOG"
    sed -i "s|^\([[:space:]]*tx_log_path[[:space:]]*=[[:space:]]*\).*|\1\"$NEW_LOG\"|" "$STAGE/config.toml"
    read_paths
    [[ $TX_LOG == "$NEW_LOG" && $GUARD_DIR == "$GUARD_STATE" && $SERVER_DIR == "$SERVER_STATE" ]] ||
        die "could not change tx_log_path in the configuration"
fi

# The known files only; never overwrite state that is already here.
RESTORE=()  # "source target mode" triples
for name in "${GUARD_FILES[@]}"; do
    file=$(staged_file "$STAGE/guard/$name")
    if [[ -n $file ]]; then RESTORE+=("$file" "$GUARD_DIR/$name" 0640); fi
done
for name in "${SERVER_FILES[@]}"; do
    file=$(staged_file "$STAGE/server/$name")
    if [[ -n $file ]]; then RESTORE+=("$file" "$SERVER_DIR/$name" 0600); fi
done
for ((i = 1; i < ${#RESTORE[@]}; i += 3)); do
    [[ ! -e ${RESTORE[i]} ]] || die "${RESTORE[i]} exists; remove it first if it is to be replaced"
done
TX_SOURCE=$(staged_file "$STAGE/tx.jsonl")
if [[ -n $TX_SOURCE && -s $TX_LOG ]]; then
    die "$TX_LOG exists and is not empty; remove it first if it is to be replaced"
fi

if [[ -e $CONFIG ]]; then
    cp -p "$CONFIG" "$CONFIG.before-restore"
    echo "kept the old configuration as $CONFIG.before-restore"
fi
install -m 0640 -o root -g "$APP_USER" "$STAGE/config.toml" "$CONFIG"
install -d -m 0750 -o "$APP_USER" -g "$APP_USER" "$GUARD_DIR" "$SERVER_DIR" "$(dirname "$TX_LOG")"
for ((i = 0; i < ${#RESTORE[@]}; i += 3)); do
    install -m "${RESTORE[i + 2]}" -o "$APP_USER" -g "$APP_USER" "${RESTORE[i]}" "${RESTORE[i + 1]}"
done
if [[ -n $TX_SOURCE ]]; then
    install -m 0640 -o "$APP_USER" -g "$APP_USER" "$TX_SOURCE" "$TX_LOG"
fi

echo "restored: $CONFIG, $GUARD_DIR, $SERVER_DIR, $TX_LOG"
cat <<EOF
Check before starting the services:
  - $CONFIG: server.allowed_hosts (this machine's name in the tailnet),
    the radio port and the sound cards as named on this machine;
  - sudo deploy/install.sh   (validates the configuration again);
  - then: sudo systemctl start ${UNITS[*]}
EOF
