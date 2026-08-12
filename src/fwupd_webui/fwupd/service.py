from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdCli, FwupdError
from fwupd_webui.fwupd.flash import FlashJob, FlashManager, FlashStatus
from fwupd_webui.fwupd.models import Device, Release
from fwupd_webui.fwupd.policy import Permission, check_override, evaluate

log = logging.getLogger(__name__)

STAMP_FILENAME = ".webui-last-refresh"


@dataclass
class DeviceView:
    device: Device
    available: list[Release] = field(default_factory=list)

    @property
    def has_update(self) -> bool:
        return bool(self.available)


@dataclass
class MetadataStatus:
    last_refresh: float | None = None
    age_seconds: float | None = None
    stale: bool = True
    error: str | None = None


@dataclass
class Inventory:
    devices: list[DeviceView]
    metadata: MetadataStatus
    fwupd_version: str
    flashing_enabled: bool = False


def _dedupe_versions(releases: list[Release]) -> list[Release]:
    """Collapse releases that repeat a version, keeping the first.

    fwupd can offer the same version more than once when it appears in several
    remotes; the synthetic test device reports 1.2.4 twice. Listing it twice
    with an identical changelog reads as a UI fault, and picking between two
    identical entries is a choice nobody can make meaningfully.
    """
    seen: set[str] = set()
    unique: list[Release] = []
    for release in releases:
        if release.version in seen:
            continue
        seen.add(release.version)
        unique.append(release)
    return unique


class FwupdService:
    """Orchestrates fwupdtool calls and owns the two rules that keep it safe.

    1. fwupdtool blocks for seconds; every call goes through asyncio.to_thread.
    2. Concurrent fwupdtool invocations against the same hardware are unsafe; a
       single lock serializes them, so overlapping requests wait rather than
       spawning a second enumeration.
    """

    def __init__(
        self,
        cli: FwupdCli,
        config: Config,
        state_dir: Path,
        clock: Callable[[], float] = time.time,
    ):
        self._cli = cli
        self._config = config
        self._state_dir = Path(state_dir)
        self._clock = clock
        self._lock = asyncio.Lock()
        self.flash_manager = FlashManager(cli)
        self._cached: tuple[float, Inventory] | None = None

    async def _call(self, fn, *args):
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    @property
    def _stamp_path(self) -> Path:
        return self._state_dir / STAMP_FILENAME

    def _read_stamp(self) -> float | None:
        try:
            return float(self._stamp_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _write_stamp(self, when: float) -> None:
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._stamp_path.write_text(str(when))
        except OSError:
            log.warning("could not write refresh stamp to %s", self._stamp_path, exc_info=True)

    def _status(self, error: str | None = None) -> MetadataStatus:
        last = self._read_stamp()
        if last is None:
            return MetadataStatus(None, None, stale=True, error=error)
        age = self._clock() - last
        stale = age > self._config.refresh_interval_hours * 3600
        return MetadataStatus(last, age, stale=stale, error=error)

    async def inventory(self) -> Inventory:
        devices = await self._call(self._cli.get_devices)
        version = await self._call(self._cli.version)

        update_error: str | None = None
        try:
            updated = await self._call(self._cli.get_updates)
        except FwupdError as exc:
            # An inventory without update information is still worth showing.
            log.warning("get-updates failed: %s", exc)
            updated = []
            update_error = str(exc)

        releases_by_id = {d.device_id: d.releases for d in updated}
        views = [
            DeviceView(device=d, available=_dedupe_versions(releases_by_id.get(d.device_id, [])))
            for d in devices
        ]
        views.sort(key=lambda v: (not v.has_update, v.device.display_name.lower()))

        return Inventory(
            devices=views,
            metadata=self._status(error=update_error),
            fwupd_version=version,
            flashing_enabled=self._config.enable_flashing,
        )

    async def cached_inventory(self, max_age_seconds: float | None = None) -> Inventory:
        """Inventory, re-enumerating only when the cached copy has aged out.

        Exists for the monitoring API. Enumeration takes seconds and issues real
        commands to real disks, so a monitor polling every 15s would hammer the
        hardware; serving a slightly stale snapshot is the right trade for a
        report whose whole job is to be polled on a timer.

        While a flash is running the cached copy is returned unconditionally --
        enumerating would block on the hardware lock behind a write that may
        take minutes.
        """
        max_age = self._config.api_cache_seconds if max_age_seconds is None else max_age_seconds
        now = self._clock()

        if self._cached is not None:
            captured, inventory = self._cached
            if self.flash_manager.active or now - captured < max_age:
                return inventory

        if self.flash_manager.active:
            # Nothing cached and a flash is in flight: refuse rather than block.
            raise FwupdError("a flash is in progress and no inventory has been captured yet")

        inventory = await self.inventory()
        self._cached = (now, inventory)
        return inventory

    async def refresh(self) -> MetadataStatus:
        try:
            await self._call(self._cli.refresh)
        except FwupdError as exc:
            log.warning("metadata refresh failed: %s", exc)
            return self._status(error=str(exc))
        self._write_stamp(self._clock())
        return self._status()

    async def refresh_if_stale(self) -> MetadataStatus:
        status = self._status()
        if not status.stale:
            return status
        return await self.refresh()

    @property
    def flashing_enabled(self) -> bool:
        return self._config.enable_flashing

    def permission_for(self, device: Device) -> Permission:
        return evaluate(device, enabled=self._config.enable_flashing)

    async def start_flash(
        self,
        device_id: str,
        version: str,
        *,
        operation: str,
        typed_name: str | None,
    ) -> FlashJob:
        """Validate policy, then run the flash under the hardware lock.

        Policy is enforced here rather than in the route, so it holds whatever
        the HTML offered. Raises PermissionError when policy refuses,
        LookupError when the device or release does not exist.
        """
        devices = await self._call(self._cli.get_devices)
        device = next((d for d in devices if d.device_id == device_id), None)
        if device is None:
            raise LookupError(f"no such device: {device_id}")

        permission = self.permission_for(device)
        if not permission.flashable:
            raise PermissionError(permission.reason)
        # Every flash needs the typed name, storage or not. Enforced here rather
        # than in the route, so it holds regardless of what the HTML offered.
        if not check_override(device, typed_name):
            raise PermissionError(
                f"Type the device name exactly to confirm. Expected: {device.display_name}"
            )

        release = next((r for r in device.releases if r.version == version), None)
        if release is None:
            raise LookupError(f"device {device_id} has no release {version}")
        if not release.uri:
            raise LookupError(f"release {version} has no download URI")

        async with self._lock:
            job = await self.flash_manager.start(
                release.uri,
                device_id,
                device_name=device.display_name,
                version=version,
                allow_older=operation == "downgrade",
                allow_reinstall=operation == "reinstall",
            )
            if job.status is FlashStatus.SUCCEEDED:
                await self._record_outcome(job, device_id, version)
            return job

    async def _record_outcome(self, job: FlashJob, device_id: str, version: str) -> None:
        """Re-enumerate once so the result view can say whether the firmware is
        live or merely staged.

        Uses asyncio.to_thread directly rather than self._call: we already hold
        the lock, and self._call would try to acquire it again and deadlock.

        Best-effort -- a failure here must not turn a successful flash into a
        reported failure.
        """
        try:
            devices = await asyncio.to_thread(self._cli.get_devices)
        except FwupdError:
            log.warning("post-flash re-enumeration failed", exc_info=True)
            return
        fresh = next((d for d in devices if d.device_id == device_id), None)
        if fresh is None:
            return
        job.installed_version = fresh.version
        job.staged = fresh.version != version
