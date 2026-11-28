#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Remove what deploy/install.sh installed.
#
#   sudo deploy/uninstall.sh           # stop services, remove program, units and CLI link;
#                                      # KEEP config, TX log, lockout state and the user
#   sudo deploy/uninstall.sh --purge   # also remove /etc/remoteg90ctl, /var/lib/remoteg90ctl,
#                                      # /var/lib/remoteg90ctl-server (accounts, station log,
#                                      # recordings), /var/log/remoteg90ctl and the user/group
#
# The TX log and the lockout state are safety records (AGENTS.md rules 7 and 13);
# they are deleted only with --purge.

set -euo pipefail

APP_USER=remoteg90ctl
PREFIX=/opt/remoteg90ctl
CONFIG_DIR=/etc/remoteg90ctl
STATE_DIR=/var/lib/remoteg90ctl
SERVER_STATE_DIR=/var/lib/remoteg90ctl-server
# The DX cluster process's (DynamicUser: the data is under /var/lib/private).
CLUSTER_STATE_DIR=/var/lib/remoteg90ctl-cluster
LOG_DIR=/var/log/remoteg90ctl
RUN_DIR=/run/remoteg90ctl
UNIT_DIR=/etc/systemd/system
# Server first: it depends on the guard. Stopping the guard lowers DTR/RTS.
UNITS=(remoteg90ctl-server.service remoteg90ctl-guard.service
    remoteg90ctl-cluster.service remoteg90ctl-cluster.socket)
CLI_LINK=/usr/local/bin/remoteg90ctl

PURGE=false
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=true ;;
        -h | --help)
            sed -n '4,13p' "$0"
            exit 0
            ;;
        *)
            echo "unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

say() { printf '\n==> %s\n' "$*"; }
[[ $EUID -eq 0 ]] || {
    echo "ERROR: run with sudo" >&2
    exit 1
}

say "Stopping and disabling services"
for unit in "${UNITS[@]}"; do
    if systemctl list-unit-files --no-legend "$unit" | grep -q "$unit"; then
        systemctl disable --now --quiet "$unit" || true
        echo "$unit: stopped and disabled"
    else
        echo "$unit: not installed"
    fi
done
# The guard must be gone before anything else is removed.
if systemctl is-active --quiet remoteg90ctl-guard.service; then
    echo "ERROR: remoteg90ctl-guard is still running; not removing anything" >&2
    exit 1
fi

say "Removing units, CLI link and program"
for unit in "${UNITS[@]}"; do
    rm -f "$UNIT_DIR/$unit"
done
systemctl daemon-reload
systemctl reset-failed "${UNITS[@]}" 2>/dev/null || true
if [[ -L $CLI_LINK ]]; then
    rm -f "$CLI_LINK"
fi
rm -rf "$PREFIX"
rm -rf "$RUN_DIR"
echo "removed $UNIT_DIR/remoteg90ctl-*.service and .socket, $CLI_LINK, $PREFIX, $RUN_DIR"

if ! $PURGE; then
    say "Kept (use --purge to remove)"
    for path in "$CONFIG_DIR" "$STATE_DIR" "$SERVER_STATE_DIR" "$LOG_DIR"; do
        [[ -e $path ]] && echo "  $path"
    done
    id "$APP_USER" >/dev/null 2>&1 && echo "  user and group $APP_USER"
    exit 0
fi

say "Purging configuration, state, TX log, accounts, station log and user"
rm -rf "$CONFIG_DIR" "$STATE_DIR" "$SERVER_STATE_DIR" "$LOG_DIR" "$CLUSTER_STATE_DIR" /var/lib/private/remoteg90ctl-cluster
echo "removed $CONFIG_DIR, $STATE_DIR, $SERVER_STATE_DIR, $LOG_DIR, $CLUSTER_STATE_DIR"
if id "$APP_USER" >/dev/null 2>&1; then
    userdel "$APP_USER"
    echo "removed user $APP_USER"
fi
# userdel keeps the group while other users are members of it.
if getent group "$APP_USER" >/dev/null; then
    groupdel "$APP_USER"
    echo "removed group $APP_USER"
fi
say "Done"
