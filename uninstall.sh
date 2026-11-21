#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# remoteG90ctl uninstaller: removes the program and its data, and undoes what
# install.sh and `remoteg90ctl setup` changed in the system, so that the next
# installation starts as on a clean computer (docs/install.md section 0).
#
#   sudo ~/remoteG90ctl/uninstall.sh          # asks once before removing (yes/tak)
#   sudo ~/remoteG90ctl/uninstall.sh --yes    # no question
#   sudo ~/remoteG90ctl/uninstall.sh --all    # an installation without a record
#                                             # (installed before 0.9.0rc2)
#
# Removed: the services, the program, the configuration, the TX log and the
# transmit lockout, the accounts, the station log and the recordings (keep a
# copy first: deploy/backup.sh), the udev rules, WirePlumber's I/Q rule,
# `tailscale serve`. Undone from install.sh's record
# (/var/lib/remoteg90ctl-installer/changes), and only what it changed: units it
# masked, DietPi's clock and swap settings, the packages it installed (chrony
# gives systemd-timesyncd back), uv and Tailscale if it installed them, the
# code it cloned. Without a record, --all undoes every such change except the
# packages, which it only lists.

set -euo pipefail

RECORD_DIR=/var/lib/remoteg90ctl-installer
RECORD=$RECORD_DIR/changes
CONFIG=/etc/remoteg90ctl/config.toml

YES=false
ALL=false
for arg in "$@"; do
    case "$arg" in
        --yes) YES=true ;;
        --all) ALL=true ;;
        -h | --help)
            sed -n '4,21p' "$0"
            exit 0
            ;;
        *)
            echo "unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

say() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}
# The values recorded under a key, one per line.
recorded() { [[ -f $RECORD ]] && sed -n "s/^$1 //p" "$RECORD" || true; }

[[ $EUID -eq 0 ]] || die "run as root: sudo $0"
HERE=$(cd "$(dirname "$0")" && pwd)
[[ -x $HERE/deploy/uninstall.sh ]] || die "$HERE/deploy/uninstall.sh not found; run this from the program's code"

if [[ -f $RECORD ]]; then
    OWNER=$(recorded owner | head -n 1)
elif $ALL; then
    OWNER=${SUDO_USER:-}
    [[ -n $OWNER && $OWNER != root ]] ||
        OWNER=$(getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1; exit}')
else
    die "no record of what the installer changed ($RECORD; installed before 0.9.0rc2).
Run with --all to undo every change the installer can make (packages are only listed)."
fi
OWNER_HOME=""
[[ -n $OWNER ]] && OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)

if ! $YES; then
    cat <<EOF
This removes remoteG90ctl completely: the program, the configuration, the TX
log and the transmit lockout, the accounts, the station log and the
recordings. Keep a copy first if you need them: sudo $HERE/deploy/backup.sh
EOF
    [[ -r /dev/tty ]] || die "no terminal to ask; run with --yes"
    read -r -p "Type yes to remove everything: " answer </dev/tty
    case "$answer" in
        yes | YES | Yes | tak | TAK | Tak) ;;
        *) die "nothing removed" ;;
    esac
fi

# ---- The program first: stopping the guard lowers DTR/RTS ----

PORT=8078
if [[ -r $CONFIG ]]; then
    found=$(sed -n 's/^listen *= *\[ *"[^"]*:\([0-9]\+\)".*/\1/p' "$CONFIG" | head -n 1)
    [[ -n $found ]] && PORT=$found
fi

"$HERE/deploy/uninstall.sh" --purge

say "Files of the setup"
rm -f /etc/udev/rules.d/9[0-2]-remoteg90ctl-*.rules
udevadm control --reload 2>/dev/null || true
echo "udev rules removed"
if [[ -n $OWNER_HOME ]]; then
    rm -f "$OWNER_HOME/.config/wireplumber/main.lua.d/51-remoteg90ctl-iq.lua" \
        "$OWNER_HOME/.config/wireplumber/wireplumber.conf.d/51-remoteg90ctl-iq.conf"
    echo "WirePlumber's I/Q rule removed (in effect after the next login)"
fi
if command -v tailscale >/dev/null && tailscale serve status 2>/dev/null | grep -q ":$PORT\b"; then
    tailscale serve reset && echo "tailscale serve switched off (it served port $PORT)"
fi

# ---- What install.sh changed in the system ----

say "System settings"
if $ALL && [[ ! -f $RECORD ]]; then
    masked=(brltty.service brltty-udev.service ModemManager.service
        sleep.target suspend.target hibernate.target hybrid-sleep.target)
    for unit in "${masked[@]}"; do
        [[ $(systemctl is-enabled "$unit" 2>/dev/null) == masked ]] || continue
        systemctl unmask --quiet "$unit"
        echo "$unit unmasked (not enabled again: no record of its state)"
    done
else
    while read -r unit state; do
        [[ -n $unit ]] || continue
        systemctl unmask --quiet "$unit" 2>/dev/null || true
        if [[ $state == enabled ]]; then
            systemctl enable --now --quiet "$unit" 2>/dev/null || warn "$unit could not be enabled again"
        fi
        echo "$unit unmasked${state:+ ($state before)}"
    done < <(recorded masked)
fi
if [[ -e /boot/dietpi.txt ]]; then
    ntp_mode=$(recorded dietpi_ntp_mode | head -n 1)
    if [[ -n $ntp_mode ]]; then
        sed -i "s/^CONFIG_NTP_MODE=.*/CONFIG_NTP_MODE=$ntp_mode/" /boot/dietpi.txt
        echo "DietPi: CONFIG_NTP_MODE=$ntp_mode again"
    fi
    swap_size=$(recorded dietpi_swap | head -n 1)
    [[ -z $swap_size ]] && $ALL && [[ ! -f $RECORD ]] && swap_size=1
    if [[ -n $swap_size && -x /boot/dietpi/func/dietpi-set_swapfile ]]; then
        /boot/dietpi/func/dietpi-set_swapfile "$swap_size" >/dev/null &&
            echo "DietPi: swap file back ($swap_size)"
    fi
fi

say "Tools"
uv_home=$(recorded uv | head -n 1)
$ALL && [[ ! -f $RECORD ]] && uv_home=$OWNER_HOME
if [[ -n $uv_home && -x $uv_home/.local/bin/uv ]]; then
    rm -rf "$uv_home/.local/bin/uv" "$uv_home/.local/bin/uvx" "$uv_home/.local/bin/env" \
        "$uv_home/.local/bin/env.fish" "$uv_home/.local/share/uv" "$uv_home/.cache/uv"
    # The line uv's installer added to the shell's start files.
    for rc in .profile .bashrc .bash_profile .zshrc .zshenv; do
        [[ -f $uv_home/$rc ]] && sed -i '\|^\. "\$HOME/\.local/bin/env"$|d' "$uv_home/$rc"
    done
    echo "uv removed from $uv_home"
fi
tailscale_ours=$(recorded tailscale | head -n 1)
$ALL && [[ ! -f $RECORD ]] && command -v tailscale >/dev/null && tailscale_ours=installed
if [[ -n $tailscale_ours ]] && command -v tailscale >/dev/null; then
    tailscale logout 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq tailscale >/dev/null
    rm -rf /var/lib/tailscale /etc/apt/sources.list.d/tailscale.list \
        /usr/share/keyrings/tailscale-archive-keyring.gpg
    echo "Tailscale logged out and removed (the computer stays in the tailnet's admin page until you delete it there)"
fi

say "Packages"
mapfile -t packages < <(recorded package)
if ((${#packages[@]})); then
    # chrony's place goes back to systemd-timesyncd first, so the clock keeps its sync.
    if [[ -n $(recorded replaced) ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq systemd-timesyncd >/dev/null &&
            echo "systemd-timesyncd back"
    fi
    DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq "${packages[@]}" >/dev/null
    echo "removed: ${packages[*]} (apt autoremove removes what only they needed)"
elif $ALL && [[ ! -f $RECORD ]]; then
    echo "not removed (no record of which were there before): git curl ca-certificates libportaudio2 libopus0 alsa-utils sysstat chrony"
else
    echo "none installed by the installer"
fi

# ---- Last: the record and the code this script runs from ----

clone=$(recorded clone | head -n 1)
$ALL && [[ ! -f $RECORD && $HERE == "$OWNER_HOME/remoteG90ctl" ]] && clone=$HERE
rm -rf "$RECORD_DIR"
if [[ -n $clone && -d $clone/.git ]]; then
    say "Code"
    cd /
    rm -rf "$clone"
    echo "removed $clone"
fi
say "Done: remoteG90ctl is removed"
