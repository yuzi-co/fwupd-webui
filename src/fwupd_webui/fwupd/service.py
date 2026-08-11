from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdCli, FwupdError
from fwupd_webui.fwupd.models import Device, Release

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
            DeviceView(device=d, available=list(releases_by_id.get(d.device_id, [])))
            for d in devices
        ]
        views.sort(key=lambda v: (not v.has_update, v.device.display_name.lower()))

        return Inventory(
            devices=views,
            metadata=self._status(error=update_error),
            fwupd_version=version,
        )

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
