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

# Plugins this application refuses to flash through, ever. Two different hazards
# live in this set, and collapsing them into one sentence about motherboards is
# how this comment was wrong for a while.
#
# `uefi_capsule` and `mtd` are the firmware writes. A capsule is staged to the
# EFI system partition and applied by the firmware flashing itself on the next
# boot -- on Unraid that partition is the removable USB stick holding the OS and
# array configuration, so a mis-staged capsule leaves it unbootable. `mtd` writes
# the SPI flash chip directly, reaching the same silicon without the capsule
# path's mediation. It was added after deploying to a Proxmox host, which exposed
# `Internal SPI Controller (BIOS)` as updatable: blocking only the capsule was not
# blocking BIOS writes, it was blocking one of two roads to them. The first
# deployment target had no mtd devices, which is why neither the tests nor that
# deployment surfaced it. Failure either way costs the motherboard, and recovery
# needs BIOS flashback or an external programmer.
#
# `uefi_dbx` is not a firmware write and never touches the SPI chip. It appends to
# the UEFI Secure Boot revocation list by writing the authenticated dbx EFI
# variable through efivarfs; fwupd consults the ESP only to check whether the new
# list would revoke the bootloader in use, which is why the plugin reports
# esp-not-found and clears updatable when it cannot see one. It is blocked for
# different reasons than the two above. The append cannot be undone short of
# clearing the Secure Boot keys -- fwupd marks the device only-version-upgrade --
# and if the new list revokes the running bootloader, the machine stops booting
# until someone disables Secure Boot in firmware setup. On the headless NAS and
# hypervisor hosts this ships to, needing to walk to the machine is as bad as a
# brick. Typing a device name proves deliberateness; it does not prove anyone can
# reach the box.
#
# Keeping dbx in this set is also what makes the read-only efivars grant in
# deploy/proxmox-lxc.sh correct. Applying a dbx update needs write access to
# efivars, so unblocking it here would require widening that grant: the two
# decisions have to move together.
#
# `uefi_pk`, `uefi_kek` and `uefi_db` are blocked for the same reason as dbx. They
# write authenticated Secure Boot variables through efivarfs, this tool cannot
# undo those writes, and a bad one can need someone at the machine. They also sit
# behind the same read-only efivars grant.
#
# The Docker image also disables uefi_capsule in its baked /etc/fwupd/fwupd.conf.
# This check exists because that file is a property of the image, not of the
# application: an LXC or bare-metal install uses the host's fwupd configuration,
# where those plugins are live. Enforcing it here means the guarantee holds on
# every deployment target and can be tested without fwupd present at all.
SECURE_BOOT_KEY_PLUGINS = frozenset({"uefi_pk", "uefi_kek", "uefi_db"})
BLOCKED_PLUGINS = frozenset({"uefi_capsule", "uefi_dbx", "mtd"}) | SECURE_BOOT_KEY_PLUGINS

# The refusal shown to the operator. dbx gets its own wording because telling
# someone a revocation-list append costs them the motherboard is simply untrue,
# and the real consequence -- a machine that needs physical access to recover --
# is the one they need to hear.
_FIRMWARE_REFUSAL = (
    "The {plugin} plugin writes system firmware. This tool never does that: "
    "a failed write costs the motherboard, not a peripheral. Use your "
    "system's own firmware update path."
)
_DBX_REFUSAL = (
    "The uefi_dbx plugin appends to the UEFI Secure Boot revocation list. This "
    "tool never does that: the entries cannot be removed afterwards, and if the "
    "new list revokes the bootloader this machine uses, it stops booting until "
    "someone disables Secure Boot in firmware setup. Use your system's own "
    "firmware update path."
)
_SECURE_BOOT_KEY_REFUSAL = (
    "The {plugin} plugin changes the UEFI Secure Boot keys. This tool never does "
    "that: it cannot undo the change, and a bad one can leave this machine unable "
    "to boot until someone fixes Secure Boot in firmware setup. Use your system's "
    "own firmware update path."
)


def _refusal(plugin: str) -> str:
    if plugin == "uefi_dbx":
        return _DBX_REFUSAL
    if plugin in SECURE_BOOT_KEY_PLUGINS:
        return _SECURE_BOOT_KEY_REFUSAL.format(plugin=plugin)
    return _FIRMWARE_REFUSAL.format(plugin=plugin)


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
            reason=_refusal(plugin),
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
