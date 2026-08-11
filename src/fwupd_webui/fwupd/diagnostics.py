from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REQUIRED_MOUNTS: dict[str, str] = {
    "/sys": "fwupd reads device attributes from sysfs.",
    "/dev": "NVMe, SCSI generic, and MTD ioctls need the host device nodes.",
    "/run/udev": (
        "fwupd enumerates hardware through the udev database. Without the host's "
        "udev directory the device list comes back empty rather than erroring — "
        "this is the most common cause of an empty inventory."
    ),
}


@dataclass(frozen=True)
class MountCheck:
    path: str
    present: bool
    purpose: str


def check_mounts(root: Path = Path("/")) -> list[MountCheck]:
    """Report which required host paths are visible inside this container."""
    checks = []
    for path, purpose in REQUIRED_MOUNTS.items():
        target = Path(root) / path.lstrip("/")
        checks.append(MountCheck(path=path, present=target.exists(), purpose=purpose))
    return checks
