from __future__ import annotations

from dataclasses import dataclass

from fwupd_webui.fwupd.models import Device

# Plugins that drive storage. Every device behind one of these requires the
# typed override, whatever else is true of it.
#
# The hazard is not the plugin, it is that a drive holds data something is
# actively using. An nvme in a USB enclosure would be perfectly safe to flash;
# the same nvme as a NAS cache pool is running the filesystem the container
# itself lives on. We cannot tell those apart without host mount visibility, so
# we treat all storage as the dangerous case -- over-warning about an idle spare
# costs five seconds, under-warning about a mounted pool costs the pool.
#
# Enumerated from `fwupdtool get-plugins` on fwupd 2.1.7, not guessed.
STORAGE_PLUGINS = frozenset({"ata", "emmc", "nvme", "scsi"})

# Plugins whose devices update at runtime without endangering anything the host
# depends on while the write is in flight: peripherals that hold no data.
#
# Storage is checked first and wins, so listing a storage plugin here would have
# no effect. Adding a plugin is a one-line change -- make it deliberately, with
# a test. Anything not listed requires the typed override, so a plugin fwupd
# adds in a future release fails safe rather than becoming flashable on rebuild.
RUNTIME_SAFE_PLUGINS = frozenset(
    {
        "thunderbolt",
        "logitech_hidpp",
        "wacom_usb",
        "wacom_raw",
    }
)


@dataclass(frozen=True)
class Permission:
    allowed: bool
    needs_override: bool
    reason: str
    is_storage: bool = False


def evaluate(device: Device, *, enabled: bool) -> Permission:
    """Decide whether `device` may be flashed. Pure; no I/O."""
    if not enabled:
        return Permission(
            allowed=False,
            needs_override=False,
            reason="Flashing is disabled. Set FWUPD_WEBUI_ENABLE_FLASHING=true to enable it.",
        )
    if not device.updatable:
        return Permission(
            allowed=False,
            needs_override=False,
            reason="fwupd reports this device is not updatable.",
        )
    plugin = device.plugin or "unknown"
    # Storage first: it outranks the allowlist, so a storage plugin can never
    # become one-click by being added to it.
    if plugin in STORAGE_PLUGINS:
        return Permission(
            allowed=False,
            needs_override=True,
            is_storage=True,
            reason=(
                "This is a storage device. Flashing it rewrites firmware on hardware "
                "that may be holding data right now, and a failure can take the data "
                "with it. Stop the array or unmount the device first, then confirm by "
                "typing the device name exactly."
            ),
        )
    if plugin not in RUNTIME_SAFE_PLUGINS:
        return Permission(
            allowed=False,
            needs_override=True,
            reason=(
                f"The {plugin} plugin is not on the runtime-safe list. "
                "Confirm by typing the device name exactly."
            ),
        )
    return Permission(allowed=True, needs_override=False, reason="")


def check_override(device: Device, typed_name: str | None) -> bool:
    """True when the operator typed the device name exactly.

    Server-side by design: a disabled button in HTML is a suggestion, this is
    the control.
    """
    if not typed_name:
        return False
    return typed_name.strip() == device.display_name
