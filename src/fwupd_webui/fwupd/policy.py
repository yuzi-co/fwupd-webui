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

# Plugins this application refuses to flash through, ever: every route to system
# firmware. Failure here does not cost a peripheral, it costs the motherboard,
# and recovery needs an external programmer or BIOS flashback.
#
# `uefi_capsule` and `uefi_dbx` stage a payload to the EFI system partition. On
# Unraid that is the removable USB stick holding the OS and array configuration,
# and a mis-staged capsule leaves it unbootable.
#
# `mtd` was added after deploying to a Proxmox host, which exposed
# `Internal SPI Controller (BIOS)` as `updatable`. It writes the SPI flash chip
# directly, reaching the same silicon as a capsule update while bypassing the
# ESP entirely -- so blocking only the capsule path was not blocking BIOS
# writes, it was blocking one of two roads to them. The first deployment target
# had no mtd devices, which is why neither the tests nor that deployment
# surfaced it.
#
# The Docker image also disables uefi_capsule in its baked /etc/fwupd/fwupd.conf.
# This check exists because that file is a property of the image, not of the
# application: an LXC or bare-metal install uses the host's fwupd configuration,
# where those plugins are live. Enforcing it here means the guarantee holds on
# every deployment target and can be tested without fwupd present at all.
BLOCKED_PLUGINS = frozenset({"uefi_capsule", "uefi_dbx", "mtd"})


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
                f"The {plugin} plugin writes system firmware. This tool never does that: "
                "a failed write costs the motherboard, not a peripheral. Use your "
                "system's own firmware update path."
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


# Symbols that appear in vendor device names and that nobody should have to
# reproduce on a keyboard to confirm an action.
_UNTYPEABLE = "™®©℠"


def confirmation_phrase(device: Device) -> str:
    """The string the UI asks the operator to type.

    The device's own name, minus glyphs that are impractical to type. Falls
    back to the raw name if stripping would leave nothing, so a degenerate
    name never turns into an empty phrase that anything matches.
    """
    stripped = device.display_name
    for symbol in _UNTYPEABLE:
        stripped = stripped.replace(symbol, "")
    stripped = " ".join(stripped.split())
    return stripped or device.display_name


def _normalize(value: str) -> str:
    for symbol in _UNTYPEABLE:
        value = value.replace(symbol, "")
    return " ".join(value.split()).casefold()


def check_override(device: Device, typed_name: str | None) -> bool:
    """True when the operator typed the device's name.

    Required for every flash, not only risky ones. Server-side by design: the
    HTML input is a prompt, this is the control.

    Matching is deliberately forgiving about presentation and strict about
    content. Case, surrounding and repeated whitespace, and trademark glyphs
    are all ignored; the words themselves are not. The point of this step is
    to make the operator look at the device and name it deliberately, not to
    test whether they can produce a U+2122 on a keyboard.
    """
    if not typed_name:
        return False
    typed = _normalize(typed_name)
    if not typed:
        return False
    return typed == _normalize(device.display_name)
