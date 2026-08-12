from __future__ import annotations

from dataclasses import dataclass

from fwupd_webui.fwupd.models import Device

# Plugins whose devices update at runtime without endangering anything the host
# depends on while the write is in flight.
#
# `ata` is deliberately absent. On the deployed host those devices are the array
# drives, and rewriting drive firmware under a live array risks the array rather
# than just the drive. Adding a plugin here is a one-line change -- make it
# deliberately, with a test.
RUNTIME_SAFE_PLUGINS = frozenset({"nvme", "thunderbolt"})


@dataclass(frozen=True)
class Permission:
    allowed: bool
    needs_override: bool
    reason: str


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
