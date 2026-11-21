#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Replaced by the installer in the repository's root (install.sh), which
# covers DietPi and the other supported systems; kept so that the old command
# still works:
#
#   sudo deploy/dietpi-setup.sh [--restore FILE.tgz]

set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/install.sh" "$@"
