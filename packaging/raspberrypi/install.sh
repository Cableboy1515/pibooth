#!/usr/bin/env bash
# pibooth installer for Raspberry Pi OS (Bookworm or later).
#
# Installs the camera/GPIO stack from apt (the libcamera bindings must match
# the OS, so picamera2 must NOT come from pip), then installs pibooth into a
# virtualenv that can see those system packages.
#
# Usage:
#   ./install.sh [--dslr] [--printer] [--source <wheel|pip-requirement>] [--venv <dir>]
#
#   --dslr      also install the gPhoto2 DSLR backend
#   --printer   also install CUPS printing support
#   --source    what to install: a wheel file, a directory, or any pip
#               requirement (e.g. 'pibooth' or a git+https URL). Defaults to
#               the repository containing this script when run from a checkout.
#   --venv      virtualenv location (default: ~/.local/share/pibooth-venv)

set -euo pipefail

VENV_DIR="$HOME/.local/share/pibooth-venv"
SOURCE=""
WITH_DSLR=0
WITH_PRINTER=0

usage() {
    sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dslr) WITH_DSLR=1 ;;
        --printer) WITH_PRINTER=1 ;;
        --source)
            SOURCE="$2"
            shift
            ;;
        --venv)
            VENV_DIR="$2"
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

if [[ ! -f /etc/debian_version ]]; then
    echo "ERROR: this script targets Raspberry Pi OS (Debian-based systems)." >&2
    exit 1
fi

# Default to installing the source tree this script is part of
if [[ -z "$SOURCE" ]]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    repo_root="$(cd "$script_dir/../.." && pwd)"
    if [[ -f "$repo_root/pyproject.toml" ]]; then
        SOURCE="$repo_root"
    else
        echo "ERROR: not running from a pibooth checkout, pass --source <wheel|pip-requirement>." >&2
        exit 1
    fi
fi

APT_PKGS=(python3-venv python3-pip python3-picamera2 python3-lgpio)
EXTRAS=""
if [[ $WITH_DSLR -eq 1 ]]; then
    APT_PKGS+=(python3-gphoto2)
    EXTRAS="dslr"
fi
if [[ $WITH_PRINTER -eq 1 ]]; then
    # python3-cups provides the 'pycups' distribution to the venv below, so
    # pip does not need libcups2-dev and a compiler to build it
    APT_PKGS+=(python3-cups cups)
    EXTRAS="${EXTRAS:+$EXTRAS,}printer"
fi

echo ">>> Installing system packages: ${APT_PKGS[*]}"
sudo apt-get update
sudo apt-get install -y "${APT_PKGS[@]}"

echo ">>> Creating virtualenv in $VENV_DIR (with access to the system camera/GPIO packages)"
python3 -m venv --system-site-packages "$VENV_DIR"

REQUIREMENT="$SOURCE"
if [[ -n "$EXTRAS" ]]; then
    REQUIREMENT="${SOURCE}[${EXTRAS}]"
fi

echo ">>> Installing $REQUIREMENT"
"$VENV_DIR/bin/pip" install --upgrade "$REQUIREMENT"

echo ">>> Creating launchers in ~/.local/bin"
mkdir -p "$HOME/.local/bin"
for tool in pibooth pibooth-count pibooth-diag pibooth-fonts pibooth-regen pibooth-printcfg; do
    ln -sf "$VENV_DIR/bin/$tool" "$HOME/.local/bin/$tool"
done

installed_version="$("$VENV_DIR/bin/pibooth" --version 2>/dev/null | tail -1)"
echo ""
echo "pibooth $installed_version installed successfully."
echo "Run it with: pibooth   (make sure ~/.local/bin is in your PATH, it is by default on Raspberry Pi OS)"
echo "To start it automatically at boot, enable the 'autostart' option in the pibooth configuration."
