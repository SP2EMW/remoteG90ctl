#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Back up remoteG90ctl's state into one archive (to move it to another machine,
# e.g. the machine by the radio, docs/install.md section 8, or to keep a copy):
#
#   sudo deploy/backup.sh                 # ./remoteg90ctl-backup-<host>-<UTC time>.tgz
#   sudo deploy/backup.sh FILE.tgz
#
# Contents: the configuration, the server's state (accounts, sessions,
# settings, the station log — the SQLite log copied consistently while the
# server runs), the guard's state (lockout, duty-cycle history) and the TX log.
# I/Q recordings are left out. The archive holds password hashes and session
# tokens: it is created readable by you only (0600).
#
# Services may keep running. Restore with deploy/restore.sh.

set -euo pipefail

PREFIX=/opt/remoteg90ctl
VENV=$PREFIX/venv
CONFIG=/etc/remoteg90ctl/config.toml

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}
case "${1:-}" in
    -h | --help)
        sed -n '4,16p' "$0"
        exit 0
        ;;
esac
[[ $EUID -eq 0 ]] || die "run with sudo"
INVOKER=${SUDO_USER:-root}
[[ -x $VENV/bin/python && -f $CONFIG ]] || die "no installation found ($VENV, $CONFIG)"

OUT=${1:-./remoteg90ctl-backup-$(hostname -s)-$(date -u +%Y%m%dT%H%MZ).tgz}
[[ ! -e $OUT ]] || die "$OUT exists"

# The paths the configuration names, one per line (audit 2026-11-15, A1).
mapfile -t PATHS < <("$VENV/bin/python" -I -c '
import sys
from pathlib import Path
from remoteg90ctl.config import load_config
c = load_config(Path(sys.argv[1]))
for path in (c.guard.state_dir, c.guard.tx_log_path, c.server.state_dir):
    print(path)
' "$CONFIG")
(( ${#PATHS[@]} == 3 )) || die "cannot read the paths from $CONFIG"
GUARD_DIR=${PATHS[0]} TX_LOG=${PATHS[1]} SERVER_DIR=${PATHS[2]}

if systemctl is-active --quiet remoteg90ctl-guard.service; then
    # Its running marker (running.json) is copied as it is; nothing is dropped,
    # since a lockout that could not be saved lives there (AGENTS.md rule 13).
    echo "NOTE: the guard is running: restored elsewhere, its first start there is" >&2
    echo "      reported as an unclean shutdown. For a move, stop the services first." >&2
fi

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
chmod 0700 "$STAGE"
mkdir "$STAGE/guard" "$STAGE/server"

cp -p "$CONFIG" "$STAGE/config.toml"
if [[ -d $GUARD_DIR ]]; then
    # Everything but the TX log, which is taken on its own below.
    find "$GUARD_DIR" -maxdepth 1 -type f ! -name "$(basename "$TX_LOG")" -exec cp -p {} "$STAGE/guard/" \;
fi
if [[ -f $TX_LOG ]]; then cp -p "$TX_LOG" "$STAGE/tx.jsonl"; fi
if [[ -d $SERVER_DIR ]]; then
    find "$SERVER_DIR" -maxdepth 1 -type f ! -name 'logbook.sqlite*' -exec cp -p {} "$STAGE/server/" \;
    if [[ -f $SERVER_DIR/logbook.sqlite ]]; then
        # SQLite's own backup: consistent although the server may be writing.
        "$VENV/bin/python" -I -c '
import sqlite3, sys
source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
source.backup(target)
target.close()
source.close()
' "$SERVER_DIR/logbook.sqlite" "$STAGE/server/logbook.sqlite"
    fi
fi
cat >"$STAGE/manifest.txt" <<EOF
remoteG90ctl backup
created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
host=$(hostname)
program=$("${VENV}/bin/python" -I -c 'import remoteg90ctl; print(remoteg90ctl.__version__)')
config=$CONFIG
guard_state_dir=$GUARD_DIR
tx_log_path=$TX_LOG
server_state_dir=$SERVER_DIR
EOF

umask 077
tar -C "$STAGE" -czf "$OUT" .
chown "$INVOKER" "$OUT"
chmod 0600 "$OUT"
echo "backup: $OUT ($(du -h "$OUT" | cut -f1))"
tar -tzf "$OUT" | sed 's|^\./||' | grep -v '/$' | sed 's/^/  /'
