from __future__ import annotations

from dataclasses import dataclass

from fwupd_webui.fwupd.models import Device

# Plugins that drive storage. These get an additional, prominent data-loss
# warning in the confirm step.
#
# The hazard is not the plugin, it is that a disk holds data something is
# actively using. An nvme in a USB enclosure would be safe to flash; the same
# nvme as a NAS cache pool is running the filesystem the container itself lives
# on. We cannot tell those apart without host mount visibility, so all storage
# is treated as the dangerous case.
#
# Enumerated from `fwupdtool get-plugins` on fwupd 2.1.7, not guessed.
STORAGE_PLUGINS = frozenset({"ata", "emmc", "nvme", "scsi"})

# Plugins this application refuses to flash through, ever. Both stage their
# payload to the EFI system partition, which is the one write this project has
# excluded from the outset: on Unraid the ESP is the removable USB stick holding
# the OS and array configuration, and a mis-staged capsule leaves it unbootable.
#
# The Docker image also disables uefi_capsule in its baked /etc/fwupd/fwupd.conf.
# This check exists because that file is a property of the image, not of the
# application: an LXC or bare-metal install uses the host's fwupd configuration,
# where the plugin is live. Enforcing it here means the guarantee holds on every
# deployment target and can be tested without fwupd present at all.
BLOCKED_PLUGINS = frozenset({"uefi_capsule", "uefi_dbx"})


@dataclass(frozen=True)
class Permission:
    """Whether a device may be flashed, and how loudly to warn about it.

    There is no "flash without confirming" state. Every flashable device
    requires the operator to type its name exactly. An allowlist of
    supposedly-safe plugins existed here and was wrong twice -- it made the
    deployed host's cache pool one click while its array disks needed typing.
    Classifying 124 plugins by risk is a judgement call that has to be right
    every time; requiring one confirmation always is a rule that cannot be
    got wrong, and costs seconds on a machine flashed twice a year.
    """

    flashable: bool
    is_storage: bool
    reason: str


def evaluate(device: Device, *, enabled: bool) -> Permission:
    """Decide whether `device` may be flashed. Pure; no I/O."""
    plugin = device.plugin or "unknown"
    is_storage = plugin in STORAGE_PLUGINS

    if not enabled:
        return Permission(
            flashable=False,
            is_storage=is_storage,
            reason="Flashing is disabled. Set FWUPD_WEBUI_ENABLE_FLASHING=true to enable it.",
        )
    if plugin in BLOCKED_PLUGINS:
        return Permission(
            flashable=False,
            is_storage=is_storage,
            reason=(
                f"The {plugin} plugin stages firmware to the EFI system partition, which "
                "this tool never writes to. Use your system's own firmware update path."
            ),
        )
    if not device.updatable:
        return Permission(
            flashable=False,
            is_storage=is_storage,
            reason="fwupd reports this device is not updatable.",
        )
    if is_storage:
        return Permission(
            flashable=True,
            is_storage=True,
            reason=(
                "This is a storage device. Flashing it rewrites firmware on hardware "
                "that may be holding data right now, and a failure can take the data "
                "with it. Stop the array or unmount the device first."
            ),
        )
    return Permission(
        flashable=True,
        is_storage=False,
        reason="Confirm by typing the device name exactly.",
    )


def check_override(device: Device, typed_name: str | None) -> bool:
    """True when the operator typed the device name exactly.

    Required for every flash, not only risky ones. Server-side by design: the
    HTML input is a prompt, this is the control.
    """
    if not typed_name:
        return False
    return typed_name.strip() == device.display_name
