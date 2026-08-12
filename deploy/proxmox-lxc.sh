#!/usr/bin/env bash
# Create a Proxmox LXC container running fwupd-webui.
#
# Run this ON THE PROXMOX HOST:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/yuzi-co/fwupd-webui/main/deploy/proxmox-lxc.sh)"
#
# The container reports the firmware of the PROXMOX HOST, not of itself. An LXC
# shares the host kernel, so /sys and /dev describe the real machine -- that is
# what makes this work at all, and it is also why the container must be
# privileged with device access. See "Why privileged" below.
#
# Environment (all optional):
#   CTID        container id                  (default: next free)
#   HOSTNAME    container hostname            (default fwupd-webui)
#   STORAGE     storage for the rootfs        (default local-lvm)
#   DISK_GB     rootfs size in GB             (default 4)
#   CORES       cpu cores                     (default 1)
#   RAM_MB      memory in MB                  (default 512)
#   BRIDGE      network bridge                (default vmbr0)
#   PORT        listen port                   (default 8099)
#   ENABLE_FLASHING  true to allow writes     (default false)
set -euo pipefail

HOSTNAME="${HOSTNAME:-fwupd-webui}"
DISK_GB="${DISK_GB:-4}"
CORES="${CORES:-1}"
RAM_MB="${RAM_MB:-512}"
BRIDGE="${BRIDGE:-vmbr0}"
PORT="${PORT:-8099}"
ENABLE_FLASHING="${ENABLE_FLASHING:-false}"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

command -v pct >/dev/null || die "pct not found -- run this on the Proxmox host, not inside a guest"
[ "$(id -u)" -eq 0 ] || die "run as root"

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
pct status "$CTID" >/dev/null 2>&1 && die "container $CTID already exists"

# Storage names are per-host. `local-lvm` is only the default on an LVM-thin
# install; a btrfs or ZFS host has neither. Detect rather than assume, and let
# STORAGE / TEMPLATE_STORAGE override.
STORAGE="${STORAGE:-$(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 {print $1; exit}')}"
[ -n "$STORAGE" ] || die "no storage accepting container rootfs; set STORAGE=<name>"

TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-$(pvesm status -content vztmpl 2>/dev/null | awk 'NR>1 {print $1; exit}')}"
[ -n "$TEMPLATE_STORAGE" ] || die "no storage accepting templates; set TEMPLATE_STORAGE=<name>"

cat <<BANNER

  fwupd-webui  ->  Proxmox LXC $CTID
  rootfs: $STORAGE    templates: $TEMPLATE_STORAGE    bridge: $BRIDGE

  This creates a PRIVILEGED container with full device access. That is not a
  default worth glossing over: enumerating firmware means issuing NVMe admin
  commands and SCSI ioctls against the host's real disks, which an unprivileged
  LXC cannot do. A privileged container with device access can reach the host's
  hardware -- treat it with the same care as root on the host itself.

  Flashing is ${ENABLE_FLASHING}. Even when enabled, every flash requires typing
  the device name exactly, and storage devices carry a data-loss warning.

BANNER
read -r -p "Continue? [y/N] " reply
[[ "$reply" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

log "locating a Debian 13 template"
pveam update >/dev/null 2>&1 || warn "pveam update failed; using the local template list"
# Filter by architecture: the template list carries amd64 and arm64 side by
# side, and `sort -V | tail -1` alone would pick arm64 on an amd64 host.
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
pick_template() {
    pveam available --section system 2>/dev/null \
        | awk -v pat="$1" -v suffix="_${ARCH}.tar" \
              '$2 ~ pat && index($2, suffix) {print $2}' \
        | sort -V | tail -1
}
TEMPLATE="$(pick_template 'debian-13-standard')"
[ -n "$TEMPLATE" ] || TEMPLATE="$(pick_template 'debian-12-standard')"
[ -n "$TEMPLATE" ] || die "no Debian $ARCH template found in 'pveam available'"

if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
    log "downloading $TEMPLATE"
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

log "creating container $CTID"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
    --hostname "$HOSTNAME" \
    --cores "$CORES" \
    --memory "$RAM_MB" \
    --rootfs "$STORAGE:$DISK_GB" \
    --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
    --features nesting=1 \
    --unprivileged 0 \
    --onboot 1 \
    --start 0

CONF="/etc/pve/lxc/$CTID.conf"
log "granting device access in $CONF"
cat >> "$CONF" <<'LXCCONF'

# fwupd-webui: hardware access.
#
# 'devices.allow: a' is the LXC equivalent of Docker's --privileged. fwupd needs
# to open block devices and their character-device control nodes, whose major
# numbers are allocated dynamically, so enumerating a safe subset here is not
# reliably possible. Narrowing this is a genuine improvement if someone wants to
# do the work for a specific machine.
lxc.cgroup2.devices.allow: a
lxc.cap.drop:
lxc.mount.auto: proc:rw sys:rw cgroup:rw

# fwupd enumerates through the udev database. Without this the device list comes
# back nearly empty rather than erroring -- the single most common way this ends
# up looking broken.
lxc.mount.entry: /run/udev run/udev none bind,ro,create=dir 0 0
lxc.mount.entry: /dev dev none bind,rw,create=dir 0 0
LXCCONF

log "starting container"
pct start "$CTID"

log "waiting for network"
for _ in $(seq 1 30); do
    pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
    sleep 2
done

log "installing fwupd-webui inside the container"
pct exec "$CTID" -- bash -c "
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates >/dev/null
    FWUPD_WEBUI_PORT=$PORT FWUPD_WEBUI_ENABLE_FLASHING=$ENABLE_FLASHING \
        bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/yuzi-co/fwupd-webui/main/deploy/install.sh)\"
"

IP="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')"

cat <<DONE

$(log "done")

  UI:        http://${IP:-<container-ip>}:$PORT/
  Container: $CTID ($HOSTNAME)
  Config:    pct exec $CTID -- editor /etc/fwupd-webui/env
  Logs:      pct exec $CTID -- journalctl -u fwupd-webui -f

  If the device list is empty, /run/udev did not bind correctly. Check:
      pct exec $CTID -- ls /run/udev/data | head

DONE
