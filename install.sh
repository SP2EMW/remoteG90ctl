#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# remoteG90ctl installer: prepares the computer by the radio and installs the
# program as systemd services (docs/install.md section 1).
#
#   curl -fsSL <address of this file> | sudo bash
#   sudo ./install.sh                      # from a checkout, the same
#   sudo ./install.sh --restore FILE.tgz   # ... with the state from deploy/backup.sh
#   sudo ./uninstall.sh                    # removes it all again (uninstall.sh)
#
# Debian 12/13, Ubuntu 22.04 or later, Raspberry Pi OS (64-bit), DietPi; on
# x86_64 or aarch64. Safe to run again (also to update). It never starts the
# services on a new installation: the hardware setup with its checklist comes
# first (`sudo remoteg90ctl setup`, docs/install.md section 5).
#
# Two stages: piped, it only fetches the code it is to install and hands over
# to that code's own install.sh, so the installer always matches the program.
# A release whose install.sh cannot take over is refused before anything is
# changed (INSTALLER_PROTOCOL below).
#
# What it installs: the newest release (a tag vX.Y.Z), not the development
# branch. A release without a suffix has passed the hardware checklist on a
# real G90; while there is none, the newest candidate (vX.Y.ZrcN). A checkout
# that follows a branch keeps following it.
#
# Environment:
#   REMOTEG90CTL_REPO     where to clone from (default below; a private copy
#                         needs an SSH key or a token in the address)
#   REMOTEG90CTL_RELEASE  a release to install instead of the newest (a tag)
#   REMOTEG90CTL_BRANCH   a branch to follow instead of the releases (e.g. main,
#                         the development branch: not checked on the radio)
#   REMOTEG90CTL_USER     your account, when run as root without sudo

set -euo pipefail

# The line a release's install.sh carries when it can take over from this one.
# installer-protocol: 1
INSTALLER_PROTOCOL="# installer-protocol: 1"

REPO=${REMOTEG90CTL_REPO:-https://github.com/SP2EMW/remoteG90ctl.git}
# The address as shown in messages and the log: without a token in it.
REPO_SHOWN=$(sed 's|//[^@/]*@|//|' <<<"$REPO")
RELEASE=${REMOTEG90CTL_RELEASE:-}
BRANCH=${REMOTEG90CTL_BRANCH:-}
PACKAGES=(git curl ca-certificates libportaudio2 libopus0 alsa-utils sysstat chrony)

ARGS=("$@")
RESTORE=""
while (($#)); do
    case "$1" in
        --restore)
            RESTORE=${2:-}
            [[ -n $RESTORE ]] || {
                echo "--restore needs the archive" >&2
                exit 2
            }
            shift 2
            ;;
        -h | --help)
            sed -n '4,34p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null || true
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
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

as_owner() { sudo -u "$OWNER" -H "$@"; }

# What this installer changed in the system, for uninstall.sh: one "key value"
# line per change, kept once. Only what was not there before is recorded.
RECORD_DIR=/var/lib/remoteg90ctl-installer
record() {
    mkdir -p "$RECORD_DIR"
    touch "$RECORD_DIR/changes"
    grep -qxF "$*" "$RECORD_DIR/changes" || echo "$*" >>"$RECORD_DIR/changes"
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
# Downloads an installer script ($1) and runs it; a failed download fails the
# step (piped into sh, it would run an empty script and "succeed").
# shellcheck disable=SC2016  # expanded by the sh that runs it
FETCH_AND_RUN='f=$(mktemp) && curl -fLsS "$1" -o "$f" && sh "$f"; s=$?; rm -f "$f"; exit $s'
installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'; }

# ---- Fetching the code (the first stage) ----

# The newest release tag: vX.Y.Z before any candidate vX.Y.ZrcN, which only
# counts while there is no release without a suffix (AGENTS.md section 11).
newest_release() {
    local tags stable
    tags=$(as_owner git ls-remote --tags --refs "$REPO" 'v*' | sed 's|.*refs/tags/||' |
        grep -E '^v[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?$') || return 1
    stable=$(grep -v rc <<<"$tags" || true)
    sort -V <<<"${stable:-$tags}" | tail -n 1
}

# Whether the install.sh at a fetched ref can take over from this one.
can_take_over() {
    local text
    text=$(as_owner git -C "$SRC" show "$1:install.sh" 2>/dev/null) || return 1
    grep -qxF "$INSTALLER_PROTOCOL" <<<"$text"
}

# Fetches into $SRC what is to be installed: the branch the checkout follows,
# REMOTEG90CTL_BRANCH, REMOTEG90CTL_RELEASE or the newest release. The working
# tree changes only after the fetched install.sh is known to take over.
fetch_code() {
    local fresh=false ref current
    # A checkout that follows a branch (a developer's) keeps following it,
    # unless a release is asked for; anything else gets a release.
    if [[ -z $BRANCH && -z $RELEASE && -d $SRC/.git ]]; then
        current=$(as_owner git -C "$SRC" rev-parse --abbrev-ref HEAD)
        [[ $current == HEAD ]] || BRANCH=$current
    fi
    if [[ -z $BRANCH && -z $RELEASE ]]; then
        RELEASE=$(newest_release) || die "no release found in $REPO_SHOWN; set REMOTEG90CTL_RELEASE or REMOTEG90CTL_BRANCH"
    fi
    if [[ ! -d $SRC/.git ]]; then
        [[ ! -e $SRC ]] || die "$SRC exists but is not a git checkout; move it away first"
        busy "downloading the code" as_owner git clone --quiet --no-checkout "$REPO" "$SRC"
        fresh=true
        record clone "$SRC"
    fi
    if [[ -n $BRANCH ]]; then
        echo "branch $BRANCH (not a release: what it holds may not have been checked on the radio)"
        as_owner git -C "$SRC" fetch --quiet origin "$BRANCH"
        ref=origin/$BRANCH
    else
        echo "release $RELEASE$([[ $RELEASE == *rc* ]] && echo " (a candidate: not yet checked on a real G90)")"
        as_owner git -C "$SRC" fetch --quiet --force --tags origin
        ref=refs/tags/$RELEASE
        as_owner git -C "$SRC" rev-parse --quiet --verify "$ref^{commit}" >/dev/null ||
            die "no release $RELEASE in $REPO_SHOWN"
    fi
    if ! can_take_over "$ref"; then
        $fresh && rm -rf "$SRC"
        die "${RELEASE:-branch $BRANCH} is too old for this installer (its install.sh cannot take over); choose a newer release (REMOTEG90CTL_RELEASE) or a branch (REMOTEG90CTL_BRANCH)"
    fi
    if [[ -n $BRANCH ]] && $fresh; then
        as_owner git -C "$SRC" checkout --quiet --force -B "$BRANCH" "$ref"
    elif [[ -n $BRANCH ]]; then
        as_owner git -C "$SRC" checkout --quiet "$BRANCH"
        as_owner git -C "$SRC" merge --quiet --ff-only "$ref"
    else
        local force=()
        $fresh && force=(--force)
        as_owner git -C "$SRC" -c advice.detachedHead=false checkout --quiet "${force[@]}" --detach "$ref"
    fi
    echo "$($fresh && echo cloned into || echo updated) $SRC ($(as_owner git -C "$SRC" rev-parse --short HEAD))"
}

# Sourced (the tests): the functions only.
(return 0 2>/dev/null) && return 0

# ---- Who and where ----

[[ $EUID -eq 0 ]] || die "run as root: curl ... | sudo bash, or sudo ./install.sh"
OWNER=${SUDO_USER:-${REMOTEG90CTL_USER:-}}
if [[ -z $OWNER || $OWNER == root ]]; then
    # Run as root without sudo (e.g. DietPi's root login): the first ordinary account.
    OWNER=$(getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1; exit}')
fi
[[ -n $OWNER ]] || die "no ordinary user account; create one, or set REMOTEG90CTL_USER"
[[ -z $RELEASE || -z $BRANCH ]] || die "set REMOTEG90CTL_RELEASE or REMOTEG90CTL_BRANCH, not both"
OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6)

# Everything shown also goes to a log in the user's home (not /var/log: DietPi
# keeps that in RAM); the second stage, started by the first, keeps writing to it.
if [[ -z ${REMOTEG90CTL_LOG:-} ]]; then
    export REMOTEG90CTL_LOG=$OWNER_HOME/remoteg90ctl-install.log
    [[ -e $REMOTEG90CTL_LOG ]] ||
        install -m 0600 -o "$OWNER" -g "$(id -gn "$OWNER")" /dev/null "$REMOTEG90CTL_LOG"
    printf '\n==== %s install.sh %s (from %s)\n' "$(date -Is)" "${ARGS[*]}" "$REPO_SHOWN" >>"$REMOTEG90CTL_LOG"
    if [[ -t 1 ]]; then
        exec 3>&1
        export REMOTEG90CTL_PROGRESS=1
    fi
    exec > >(tee -a "$REMOTEG90CTL_LOG") 2>&1
    echo "log: $REMOTEG90CTL_LOG"
fi
[[ -n $RESTORE ]] && RESTORE=$(realpath "$RESTORE")
[[ -z $RESTORE || -f $RESTORE ]] || die "$RESTORE not found"

say "System"
# shellcheck source=/dev/null
. /etc/os-release
ARCH=$(uname -m)
case "$ARCH" in
    x86_64 | aarch64) ;;
    armv7l | armv6l) die "32-bit ARM is not supported (the FT8 coder has no package for it); install the 64-bit Raspberry Pi OS" ;;
    *) die "unsupported processor: $ARCH" ;;
esac
# Derivatives (Linux Mint, Pop!_OS, ...) name their base in ID_LIKE and
# UBUNTU_CODENAME/DEBIAN_CODENAME; the versions they ship follow that base.
case "${ID:-}:${VERSION_ID:-}" in
    debian:12 | debian:13 | raspbian:12 | raspbian:13 | ubuntu:2[2-9].* | ubuntu:3*) ;;
    *)
        if [[ " ${ID_LIKE:-} " == *" ubuntu "* && -n ${UBUNTU_CODENAME:-} ]]; then
            echo "based on Ubuntu ($UBUNTU_CODENAME)"
        elif [[ " ${ID_LIKE:-} " == *" debian "* ]]; then
            echo "based on Debian"
        else
            warn "${PRETTY_NAME:-this system} is not Debian, Ubuntu, Raspberry Pi OS or DietPi, nor based on them; going on anyway"
        fi
        ;;
esac
DIETPI=false
[[ -e /boot/dietpi.txt ]] && DIETPI=true
DESKTOP=false
[[ $(systemctl get-default 2>/dev/null) == graphical.target ]] && DESKTOP=true
echo "${PRETTY_NAME:-?}, $ARCH$($DIETPI && echo ", DietPi")$($DESKTOP && echo ", desktop"); user $OWNER"

say "Code"
# Run as a file from a checkout: that checkout. Piped (curl | bash): no file.
script=${BASH_SOURCE[0]:-}
here=""
[[ -n $script && -f $script ]] && here=$(dirname "$script")
if [[ -n $here && -f $here/pyproject.toml && -d $here/.git ]]; then
    SRC=$(cd "$here" && pwd)
    echo "this checkout: $SRC"
else
    record owner "$OWNER"
    if ! command -v git >/dev/null; then
        for package in git ca-certificates; do
            installed "$package" || record package "$package"
        done
        busy "package list" apt-get update -qq
        busy "git" env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends git ca-certificates
    fi
    SRC=$OWNER_HOME/remoteG90ctl
    fetch_code
    echo "handing over to its own installer"
    exec "$SRC/install.sh" "${ARGS[@]}"
fi

say "Packages"
export DEBIAN_FRONTEND=noninteractive
busy "package list" apt-get update -qq
# chrony replaces systemd-timesyncd: the server reads the clock's actual error
# from it before FT8/FT4 may transmit (decisions.md D53).
record owner "$OWNER"
for package in "${PACKAGES[@]}"; do
    installed "$package" || record package "$package"
done
timesyncd=false
installed systemd-timesyncd && timesyncd=true
busy "packages (a few minutes on a new system)" apt-get install -y -qq --no-install-recommends "${PACKAGES[@]}"
# chrony takes systemd-timesyncd's place; uninstall.sh puts it back.
$timesyncd && ! installed systemd-timesyncd && record replaced systemd-timesyncd
echo "${PACKAGES[*]}"

# ---- Nothing may key the radio by opening its serial port ----

say "The radio's serial port"
# Opening the DE-19's port raises RTS, and RTS keys the radio (docs/install.md
# section 5). brltty takes CH340 adapters on Ubuntu; ModemManager probes new
# serial ports. A udev rule keeps ModemManager off the CH340 in any case.
# Masks a unit that exists; says so only when it was not masked yet.
mask_unit() {
    local unit=$1 why=$2
    systemctl list-unit-files "$unit" --no-legend 2>/dev/null | grep -q . || return 0
    if [[ $(systemctl is-enabled "$unit" 2>/dev/null) == masked ]]; then
        echo "$unit masked"
        return 0
    fi
    record masked "$unit" "$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    systemctl disable --now --quiet "$unit" 2>/dev/null || true
    systemctl mask --quiet "$unit"
    warn "$unit stopped and masked ($why)"
}
mask_unit brltty.service "a braille display will not work"
mask_unit brltty-udev.service "a braille display will not work"
if systemctl list-unit-files ModemManager.service --no-legend 2>/dev/null | grep -q .; then
    if $DESKTOP; then
        echo "ModemManager kept (desktop); the udev rule keeps it off the CH340"
    else
        mask_unit ModemManager.service "a modem in HiLink mode works without it"
    fi
fi
echo "sleep, suspend and hibernation off (the computer by the radio stays awake)"
for unit in sleep.target suspend.target hibernate.target hybrid-sleep.target; do
    if [[ $(systemctl is-enabled "$unit" 2>/dev/null) != masked ]]; then
        record masked "$unit" static
        systemctl mask --quiet "$unit"
    fi
done

# ---- Clock and swap (the internet connection is the user's own) ----

say "Clock (chrony)"
if $DIETPI && grep -q '^CONFIG_NTP_MODE=' /boot/dietpi.txt; then
    ntp_mode=$(sed -n 's/^CONFIG_NTP_MODE=//p' /boot/dietpi.txt)
    [[ $ntp_mode == 0 ]] || record dietpi_ntp_mode "$ntp_mode"
    sed -i 's/^CONFIG_NTP_MODE=.*/CONFIG_NTP_MODE=0/' /boot/dietpi.txt # DietPi's own sync off
fi
systemctl enable --now --quiet chrony
if chronyc -c -n tracking 2>/dev/null | grep -q ',Normal$'; then
    chronyc -n tracking | grep 'System time'
else
    echo "not synchronised yet (needs the internet; FT8/FT4 transmission waits for it)"
fi

if $DIETPI; then
    say "DietPi: swap"
    if [[ -x /boot/dietpi/func/dietpi-set_swapfile ]]; then
        swap_size=$(sed -n 's/^AUTO_SETUP_SWAPFILE_SIZE=//p' /boot/dietpi.txt)
        [[ -z $swap_size || $swap_size == 0 ]] || record dietpi_swap "$swap_size"
        /boot/dietpi/func/dietpi-set_swapfile 0 >/dev/null && echo "swap file off (small disk)"
    else
        warn "turn the swap file off in dietpi-config"
    fi
fi

# ---- Tools ----

say "uv and Tailscale"
if [[ ! -x $OWNER_HOME/.local/bin/uv ]] && ! command -v uv >/dev/null; then
    busy "uv" as_owner sh -c "$FETCH_AND_RUN" - https://astral.sh/uv/install.sh
    record uv "$OWNER_HOME"
fi
UV=""
for candidate in "$(command -v uv || true)" "$OWNER_HOME/.local/bin/uv" "$OWNER_HOME/.cargo/bin/uv"; do
    if [[ -n $candidate && -x $candidate ]]; then
        UV=$candidate
        break
    fi
done
[[ -n $UV ]] || die "uv is not installed (astral.sh's installer did not put it in $OWNER_HOME/.local/bin); check the internet connection and run this again"
# Older uv trusts a root CA whose intermediate keys were stolen
# (GHSA-hq85-5cpj-mr62, uv < 0.13.4; dependency audit 2026-11-15): it fetches
# the packages and, on some systems, Python itself.
UV_MIN=0.13.4
uv_version() { "$UV" --version | awk '{print $2}'; }
uv_too_old() { [[ $(printf '%s\n%s\n' "$UV_MIN" "$(uv_version)" | sort -V | head -n1) != "$UV_MIN" ]]; }
if uv_too_old; then
    busy "updating uv $(uv_version)" as_owner "$UV" self update ||
        die "uv $(uv_version) is older than $UV_MIN and could not update itself; update it (uv self update, or your system's package) and run this again"
    ! uv_too_old || die "uv is still $(uv_version), older than $UV_MIN; update it and run this again"
fi
echo "uv: $("$UV" --version)"
if ! command -v tailscale >/dev/null; then
    busy "Tailscale" sh -c "$FETCH_AND_RUN" - https://tailscale.com/install.sh
    record tailscale installed
fi
command -v tailscale >/dev/null || die "Tailscale is not installed; check the internet connection and run this again"
echo "tailscale: $(tailscale version 2>/dev/null | head -1)"

# ---- The program ----

say "Program"
install -m 0644 "$SRC"/deploy/udev/9[0-2]-remoteg90ctl-*.rules /etc/udev/rules.d/
udevadm control --reload
udevadm trigger --subsystem-match=tty --subsystem-match=sound

FRESH=true
systemctl is-enabled --quiet remoteg90ctl-guard.service 2>/dev/null && FRESH=false
# deploy/install.sh's own "next steps" do not apply here.
export REMOTEG90CTL_NO_NEXT_STEPS=1
SUDO_USER=$OWNER UV=$UV "$SRC/deploy/install.sh"

if [[ -n $RESTORE ]]; then
    say "State from $RESTORE"
    systemctl stop remoteg90ctl-server.service remoteg90ctl-guard.service 2>/dev/null || true
    "$SRC/deploy/restore.sh" "$RESTORE"
    SUDO_USER=$OWNER UV=$UV "$SRC/deploy/install.sh" # checks the restored configuration
fi

if ! $FRESH; then
    say "Updated: restarting the services"
    systemctl restart remoteg90ctl-guard.service remoteg90ctl-server.service
    sleep 3
    "$SRC/deploy/services.sh" --status
    failed=false
    for unit in remoteg90ctl-guard.service remoteg90ctl-server.service; do
        systemctl is-active --quiet "$unit" || failed=true
    done
    if $failed; then
        die "a service did not start: journalctl -u remoteg90ctl-guard -u remoteg90ctl-server -n 50"
    fi
    echo "Up to date and running."
    exit 0
fi

say "Next"
if /usr/local/bin/remoteg90ctl setup --help >/dev/null 2>&1; then
    echo "The radio, the sound cards, the hardware checklist and the access:"
    if [[ -r /dev/tty ]]; then
        /usr/local/bin/remoteg90ctl setup </dev/tty
    else
        echo "  sudo remoteg90ctl setup"
    fi
else
    cat <<EOF
Hardware setup by hand for now (docs/install.md sections 5, 6 and 8):
  configuration /etc/remoteg90ctl/config.toml, then  sudo $SRC/deploy/services.sh --start
EOF
fi
