from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from fwupd_webui.fwupd.cli import FwupdCommandFailed, ProgressLine
from fwupd_webui.fwupd.download import download_verified

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

    def __init__(
        self,
        cli,
        *,
        work_dir: Path | None = None,
        download: Callable[[str, str, Path], Path] = download_verified,
    ):
        self._cli = cli
        self._work_dir = work_dir
        self._download = download
        self.job: FlashJob | None = None

    @property
    def active(self) -> bool:
        return self.job is not None and not self.job.finished

    def dismiss(self) -> None:
        if self.active:
            raise RuntimeError("cannot dismiss a running job")
        self.job = None

    def begin(self, device_id: str, *, device_name: str, version: str) -> FlashJob:
        """Claim the single job slot and return the job, without doing any work.

        Synchronous, so nothing can run between the check and the claim, and so
        the job is visible to the progress page before run() has started.
        """
        if self.active:
            raise RuntimeError("a flash is already running")

        job = FlashJob(
            device_id=device_id,
            device_name=device_name,
            version=version,
            status=FlashStatus.RUNNING,
        )
        self.job = job
        return job

    async def run(
        self,
        job: FlashJob,
        url: str,
        *,
        sha256: str,
        allow_older: bool = False,
        allow_reinstall: bool = False,
        on_success: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Download, verify and install, recording the outcome on `job`.

        on_success runs before the job is marked finished, because the progress
        page stops polling at that point and would never show what it records.
        """

        def on_progress(progress: ProgressLine) -> None:
            job.phase = progress.phase
            job.percent = progress.percent

        def on_log(line: str) -> None:
            job.log.append(line)
            del job.log[:-LOG_TAIL_LINES]

        job.phase = "Downloading"
        try:
            if self._work_dir is not None:
                self._work_dir.mkdir(parents=True, exist_ok=True)
            # In the work directory, not the system temp: /tmp can be a tmpfs, and
            # a firmware file can be larger than the memory of a small container.
            with tempfile.TemporaryDirectory(prefix="flash-", dir=self._work_dir) as tmp:
                path = await asyncio.to_thread(self._download, url, sha256, Path(tmp))
                await asyncio.to_thread(
                    self._cli.install,
                    str(path),
                    job.device_id,
                    allow_older=allow_older,
                    allow_reinstall=allow_reinstall,
                    on_progress=on_progress,
                    on_log=on_log,
                )
        except FwupdCommandFailed as exc:
            job.exit_code = exc.exit_code
            job.error = exc.stderr or str(exc)
            job.status = FlashStatus.FAILED
            log.warning("flash of %s failed during %s: %s", job.device_id, job.phase, exc)
            return
        except Exception as exc:
            # Everything, not only FwupdError. This runs in a background task, so
            # nothing else would report it, and a job left running sends every page
            # to /flash until the process restarts.
            job.error = str(exc)
            job.status = FlashStatus.FAILED
            log.warning("flash of %s failed during %s: %s", job.device_id, job.phase, exc)
            return

        if on_success is not None:
            try:
                await on_success()
            except Exception:
                log.warning("post-flash step failed for %s", job.device_id, exc_info=True)

        job.status = FlashStatus.SUCCEEDED
        log.info("flash of %s completed", job.device_id)
