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
#   CT_HOSTNAME container hostname            (default fwupd-webui)
#   STORAGE     storage for the rootfs        (default local-lvm)
#   DISK_GB     rootfs size in GB             (default 4)
#   CORES       cpu cores                     (default 1)
#   RAM_MB      memory in MB                  (default 512)
#   BRIDGE      network bridge                (default vmbr0)
#   PORT        listen port                   (default 8099)
#   ENABLE_FLASHING  true to allow writes     (default false)
set -euo pipefail

# Not HOSTNAME: that is already set in the invoking shell, so ${HOSTNAME:-...}
# silently names the container after the Proxmox host itself.
CT_HOSTNAME="${CT_HOSTNAME:-fwupd-webui}"
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
    --hostname "$CT_HOSTNAME" \
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

# An LXC gets neither of these by default, unlike a privileged Docker container.
# /dev is what actually matters; /run/udev is cheap insurance for fwupd versions
# and plugins that consult the udev database rather than sysfs directly.
lxc.mount.entry: /run/udev run/udev none bind,ro,create=dir 0 0
lxc.mount.entry: /dev dev none bind,rw,create=dir 0 0

# Binding the host's /dev read-write means LXC prepares its pty devices against
# the host's devtmpfs rather than a private one. lxc_setup_devpts_child() does
# remove("/dev/ptmx") followed by open(O_CREAT) to build a bind-mount target,
# and since a bind mount shares inodes, both land on the host: /dev/ptmx stops
# being the 5:2 character device and becomes an empty regular file. Every pty
# allocation on the host then fails -- no ssh shells, no Proxmox web console,
# and no way back in short of recreating the node by hand. The container is
# unaffected, so the damage is invisible from inside it. Mount propagation does
# not help here; file creation and removal are not mount events.
#
# Setting pty.max to 0 makes LXC skip devpts setup entirely ("No new devpts
# instance will be mounted since no pts devices are required"), so it never
# touches the node. The container still gets working ptys through the bound
# host /dev, and device enumeration is unchanged -- verified at 10 devices on a
# J4125 host, against 2 for an unprivileged container.
lxc.pty.max: 0
LXCCONF

# UEFI security databases (PK, KEK, db, dbx) live in efivarfs, which an LXC does
# not get on its own: lxc.mount.auto hands the container a fresh sysfs, and
# efivarfs is a separate filesystem that does not come along with it. Without it
# fwupd enumerates 10 devices on a machine whose host sees 18, silently missing
# every uefi_* device -- including the dbx revocation list, which is one of the
# most commonly applied firmware updates there is.
#
# Bind-mounting efivarfs is necessary but not sufficient. LXC's AppArmor
# abstraction carries "deny /sys/firmware/efi/efivars/** rwklx". That rule is
# path-based and covers the contents rather than the directory, so listing the
# directory succeeds while every open() inside it fails with EACCES -- the
# devices stay missing and nothing is logged. It cannot be relaxed by adding an
# allow, because in AppArmor a deny always wins. The profile therefore has to be
# built from a copy of the abstraction with that single line rewritten.
#
# Both files are generated from the shipped policy rather than vendored, so they
# follow upstream changes instead of freezing today's rules. The profile is
# derived from the nesting variant because the container is created with
# features=nesting, and setting lxc.apparmor.profile explicitly overrides
# Proxmox's own nesting-based profile selection.
#
# Read is all that is granted, and all that is needed. Enumerating the uefi_*
# devices only reads; writing efivars is the bricking vector. The application
# refuses system firmware regardless -- policy.BLOCKED_PLUGINS covers
# uefi_capsule, uefi_dbx and mtd -- so write access would be a capability
# nothing here can use, with flashing enabled or not.
AA_BASE=/etc/apparmor.d/abstractions/lxc/container-base
AA_NEST=/etc/apparmor.d/lxc/lxc-default-with-nesting
AA_ABS=/etc/apparmor.d/abstractions/lxc/container-base-fwupd-webui
AA_PROFILE=/etc/apparmor.d/lxc/lxc-fwupd-webui
AA_NAME=lxc-container-fwupd-webui
AA_DENY='  deny /sys/firmware/efi/efivars/** rwklx,'

if [ ! -d /sys/firmware/efi/efivars ]; then
    warn "host is not UEFI-booted; skipping efivars (uefi_* devices will be unavailable)"
elif [ ! -r "$AA_BASE" ] || [ ! -r "$AA_NEST" ]; then
    warn "LXC AppArmor policy not found; skipping efivars (uefi_* devices will be unavailable)"
elif ! grep -qxF "$AA_DENY" "$AA_BASE"; then
    warn "LXC AppArmor policy has no efivars deny rule to rewrite; skipping efivars"
else
    log "granting read-only efivars access so fwupd can see UEFI firmware"
    sed "s|^$(printf '%s' "$AA_DENY" | sed 's/[][\.*^$/]/\\&/g')$|  /sys/firmware/efi/efivars/** r,|" \
        "$AA_BASE" > "$AA_ABS"
    sed -e "s|^profile lxc-container-default-with-nesting |profile $AA_NAME |" \
        -e "s|abstractions/lxc/container-base>|abstractions/lxc/container-base-fwupd-webui>|" \
        "$AA_NEST" > "$AA_PROFILE"

    if apparmor_parser -r /etc/apparmor.d/lxc-containers; then
        cat >> "$CONF" <<AACONF
lxc.mount.entry: /sys/firmware/efi/efivars sys/firmware/efi/efivars none bind,ro,optional 0 0
lxc.apparmor.profile: $AA_NAME
AACONF
    else
        rm -f "$AA_ABS" "$AA_PROFILE"
        warn "apparmor_parser failed; keeping the default profile (uefi_* devices unavailable)"
    fi
fi

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
  Container: $CTID ($CT_HOSTNAME)
  Config:    pct exec $CTID -- editor /etc/fwupd-webui/env
  Logs:      pct exec $CTID -- journalctl -u fwupd-webui -f

  If the device list is empty, /run/udev did not bind correctly. Check:
      pct exec $CTID -- ls /run/udev/data | head

DONE
