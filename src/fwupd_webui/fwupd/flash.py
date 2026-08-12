from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum

from fwupd_webui.fwupd.cli import FwupdCommandFailed, FwupdError, ProgressLine

log = logging.getLogger(__name__)

LOG_TAIL_LINES = 50


class FlashStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class FlashJob:
    device_id: str
    device_name: str
    version: str
    status: FlashStatus = FlashStatus.PENDING
    phase: str = ""
    percent: float = 0.0
    exit_code: int | None = None
    error: str | None = None
    log: list[str] = field(default_factory=list)
    # Filled in by the service after a successful flash, from one
    # re-enumeration. None means "not determined".
    installed_version: str | None = None
    staged: bool | None = None

    @property
    def finished(self) -> bool:
        return self.status in (FlashStatus.SUCCEEDED, FlashStatus.FAILED)


class FlashManager:
    """Owns at most one flash job, globally.

    There is deliberately no cancel: killing fwupdtool between Writing and
    Verifying is a way to leave partially written firmware on a device. A job
    runs to completion or fails on its own.

    A finished job is retained so the result stays readable after a reconnect,
    and is replaced only when the next flash starts.
    """

    def __init__(self, cli):
        self._cli = cli
        self.job: FlashJob | None = None

    @property
    def active(self) -> bool:
        return self.job is not None and not self.job.finished

    def dismiss(self) -> None:
        if self.active:
            raise RuntimeError("cannot dismiss a running job")
        self.job = None

    async def start(
        self,
        target: str,
        device_id: str,
        *,
        device_name: str,
        version: str,
        allow_older: bool = False,
        allow_reinstall: bool = False,
    ) -> FlashJob:
        if self.active:
            raise RuntimeError("a flash is already running")

        job = FlashJob(device_id=device_id, device_name=device_name, version=version)
        self.job = job

        def on_progress(progress: ProgressLine) -> None:
            job.phase = progress.phase
            job.percent = progress.percent

        def on_log(line: str) -> None:
            job.log.append(line)
            del job.log[:-LOG_TAIL_LINES]

        job.status = FlashStatus.RUNNING
        try:
            await asyncio.to_thread(
                self._cli.install,
                target,
                device_id,
                allow_older=allow_older,
                allow_reinstall=allow_reinstall,
                on_progress=on_progress,
                on_log=on_log,
            )
        except FwupdCommandFailed as exc:
            job.status = FlashStatus.FAILED
            job.exit_code = exc.exit_code
            job.error = exc.stderr or str(exc)
            log.warning("flash of %s failed during %s: %s", device_id, job.phase, exc)
            return job
        except FwupdError as exc:
            job.status = FlashStatus.FAILED
            job.error = str(exc)
            log.warning("flash of %s failed during %s: %s", device_id, job.phase, exc)
            return job

        job.status = FlashStatus.SUCCEEDED
        log.info("flash of %s completed", device_id)
        return job
