#!/usr/bin/env bash
# Install fwupd-webui natively on a Debian or Ubuntu system.
#
# Intended for LXC containers, VMs and bare metal -- anywhere you would rather
# not run Docker. For Docker, see the README instead.
#
# Installs to /opt/fwupd-webui, runs as a systemd service on port 8099.
# Re-running it upgrades in place.
#
#   curl -fsSL https://raw.githubusercontent.com/yuzi-co/fwupd-webui/main/deploy/install.sh | bash
#
# Environment:
#   FWUPD_WEBUI_PORT            listen port                     (default 8099)
#   FWUPD_WEBUI_ENABLE_FLASHING true enables firmware writes     (default false)
#   FWUPD_WEBUI_REF             git ref to install               (default main)
set -euo pipefail

APP_DIR=/opt/fwupd-webui
SRC_DIR="$APP_DIR/src-checkout"
VENV="$APP_DIR/venv"
REPO=https://github.com/yuzi-co/fwupd-webui.git
REF="${FWUPD_WEBUI_REF:-main}"
PORT="${FWUPD_WEBUI_PORT:-8099}"
ENABLE_FLASHING="${FWUPD_WEBUI_ENABLE_FLASHING:-false}"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: firmware enumeration needs it"
command -v apt-get >/dev/null || die "this installer supports Debian and Ubuntu only"

log "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# fwupd brings fwupdtool, which runs the engine in-process. The fwupd daemon and
# its systemd units arrive with it but are not needed and are left alone.
apt-get install -y -qq --no-install-recommends \
    fwupd python3 python3-venv python3-pip git ca-certificates

FWUPD_VERSION="$(fwupdtool --version --json 2>/dev/null \
    | python3 -c 'import json,sys;print(next((e["Version"] for e in json.load(sys.stdin).get("Versions",[]) if e.get("Type")=="runtime" and e.get("AppstreamId")=="org.freedesktop.fwupd"),"unknown"))' \
    2>/dev/null || echo unknown)"
log "fwupd $FWUPD_VERSION"
case "$FWUPD_VERSION" in
    1.*|2.0.*)
        printf '\033[1;33mNote:\033[0m your distro ships fwupd %s. Newer releases cover more\n' "$FWUPD_VERSION"
        printf '      devices; the Docker image ships 2.1.x. This is a coverage difference,\n'
        printf '      not a fault -- everything here works on %s.\n' "$FWUPD_VERSION"
        ;;
esac

log "fetching source ($REF)"
mkdir -p "$APP_DIR"
if [ -d "$SRC_DIR/.git" ]; then
    git -C "$SRC_DIR" fetch --quiet origin "$REF"
    git -C "$SRC_DIR" checkout --quiet FETCH_HEAD
else
    git clone --quiet --depth 1 --branch "$REF" "$REPO" "$SRC_DIR"
fi

log "installing into $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$SRC_DIR"

log "writing systemd unit"
install -m 0644 "$SRC_DIR/deploy/systemd/fwupd-webui.service" \
    /etc/systemd/system/fwupd-webui.service

mkdir -p /etc/fwupd-webui
cat > /etc/fwupd-webui/env <<EOF
# Configuration for fwupd-webui. Restart after editing:
#   systemctl restart fwupd-webui
FWUPD_WEBUI_PORT=$PORT

# Set to true to allow writing firmware. While false, the flash routes are not
# registered at all and the UI is a read-only inventory.
FWUPD_WEBUI_ENABLE_FLASHING=$ENABLE_FLASHING

# FWUPD_WEBUI_LVFS_REMOTE=lvfs
# FWUPD_WEBUI_REFRESH_INTERVAL_HOURS=24
# FWUPD_WEBUI_LOG_LEVEL=info
EOF
chmod 0640 /etc/fwupd-webui/env

systemctl daemon-reload
systemctl enable --quiet --now fwupd-webui
sleep 2

if systemctl is-active --quiet fwupd-webui; then
    IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    log "running at http://${IP:-localhost}:$PORT/"
    [ "$ENABLE_FLASHING" = "true" ] \
        && printf '\033[1;33mFlashing is ENABLED.\033[0m Every flash still requires typing the device name.\n' \
        || printf 'Flashing is disabled. Set FWUPD_WEBUI_ENABLE_FLASHING=true in\n/etc/fwupd-webui/env and restart to enable it.\n'
else
    die "service failed to start; check: journalctl -u fwupd-webui -n 50"
fi
