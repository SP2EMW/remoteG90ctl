#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Start or stop the installed remoteG90ctl services.
#
#   sudo deploy/services.sh --stop     # stop and disable autostart (server first, then guard)
#   sudo deploy/services.sh --start    # enable autostart and start (guard first, then server)
#   deploy/services.sh --status        # show state (no sudo needed)
#
# Stopping the guard lowers DTR/RTS; the kernel drops them as well when the
# port is closed (HUPCL).

set -euo pipefail

GUARD=remoteg90ctl-guard.service
SERVER=remoteg90ctl-server.service

usage() {
    sed -n '4,11p' "$0"
}

show_status() {
    for unit in "$GUARD" "$SERVER"; do
        printf '%-30s %-10s %s\n' "$unit" \
            "$(systemctl is-active "$unit" 2>/dev/null || true)" \
            "$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    done
}

need_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "ERROR: run with sudo" >&2
        exit 1
    fi
}

[[ $# -eq 1 ]] || {
    usage
    exit 2
}

case "$1" in
    --stop)
        need_root
        systemctl disable --now --quiet "$SERVER" || true
        systemctl disable --now --quiet "$GUARD" || true
        if systemctl is-active --quiet "$GUARD"; then
            echo "ERROR: $GUARD is still running" >&2
            show_status
            exit 1
        fi
        echo "stopped; autostart disabled"
        show_status
        ;;
    --start)
        need_root
        systemctl enable --now --quiet "$GUARD"
        systemctl enable --now --quiet "$SERVER"
        sleep 2
        show_status
        systemctl --no-pager --lines=0 status "$GUARD" | grep -E '^\s+Status:' || true
        ;;
    --status)
        show_status
        ;;
    -h | --help)
        usage
        ;;
    *)
        usage
        exit 2
        ;;
esac
