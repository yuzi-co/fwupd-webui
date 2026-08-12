import pytest

from fwupd_webui.fwupd.cli import FwupdCommandFailed, ProgressLine
from fwupd_webui.fwupd.flash import FlashManager, FlashStatus


class FakeCli:
    """Stands in for FwupdCli.install, driving the progress callbacks."""

    def __init__(self, progress=(), logs=(), error=None):
        self._progress = progress
        self._logs = logs
        self._error = error
        self.calls = []

    def install(
        self,
        target,
        device_id,
        *,
        allow_older=False,
        allow_reinstall=False,
        on_progress=None,
        on_log=None,
    ):
        self.calls.append((target, device_id, allow_older, allow_reinstall))
        for phase, percent in self._progress:
            if on_progress:
                on_progress(ProgressLine(phase=phase, percent=percent))
        for line in self._logs:
            if on_log:
                on_log(line)
        if self._error:
            raise self._error


def manager(cli) -> FlashManager:
    return FlashManager(cli)


async def test_a_successful_flash_ends_succeeded():
    mgr = manager(FakeCli(progress=[("Writing", 50.0), ("Verifying", 100.0)]))
    job = await mgr.start("https://lvfs/f.cab", "dev-1", device_name="NVMe", version="2.0")

    assert job.status is FlashStatus.SUCCEEDED
    assert job.phase == "Verifying"
    assert job.percent == 100.0
    assert job.error is None


async def test_progress_is_visible_on_the_job_while_running():
    """The progress endpoint reads job state directly, so the callbacks must
    mutate the job as they arrive rather than only at the end."""
    mgr = manager(FakeCli())
    seen = []

    def spy(target, device_id, *, on_progress=None, **kwargs):
        for phase, percent in (("Writing", 10.0), ("Writing", 90.0)):
            on_progress(ProgressLine(phase=phase, percent=percent))
            seen.append((mgr.job.phase, mgr.job.percent))

    mgr._cli.install = spy
    await mgr.start("f.cab", "d", device_name="N", version="1")

    assert seen == [("Writing", 10.0), ("Writing", 90.0)]


async def test_a_failed_flash_records_exit_code_and_last_phase():
    cli = FakeCli(
        progress=[("Writing", 40.0)],
        error=FwupdCommandFailed(3, "device is locked"),
    )
    job = await manager(cli).start("f.cab", "d", device_name="N", version="1")

    assert job.status is FlashStatus.FAILED
    assert job.exit_code == 3
    assert "device is locked" in job.error
    assert job.phase == "Writing", "the phase reached tells you where it broke"


async def test_log_lines_are_retained_for_the_failure_view():
    cli = FakeCli(logs=["WARNING: ESP not found"], error=FwupdCommandFailed(1, "boom"))
    job = await manager(cli).start("f.cab", "d", device_name="N", version="1")

    assert any("ESP not found" in line for line in job.log)


async def test_only_one_job_runs_at_a_time():
    mgr = manager(FakeCli())
    await mgr.start("f.cab", "d", device_name="N", version="1")
    mgr.job.status = FlashStatus.RUNNING  # simulate an in-flight job

    with pytest.raises(RuntimeError, match="already running"):
        await mgr.start("f.cab", "d2", device_name="N", version="1")


async def test_active_is_false_once_a_job_finishes():
    mgr = manager(FakeCli())
    await mgr.start("f.cab", "d", device_name="N", version="1")
    assert mgr.active is False
    assert mgr.job is not None, "a finished job is retained so the result stays readable"


async def test_dismiss_clears_the_finished_job():
    mgr = manager(FakeCli())
    await mgr.start("f.cab", "d", device_name="N", version="1")
    mgr.dismiss()
    assert mgr.job is None


async def test_dismiss_refuses_while_a_job_runs():
    mgr = manager(FakeCli())
    await mgr.start("f.cab", "d", device_name="N", version="1")
    mgr.job.status = FlashStatus.RUNNING

    with pytest.raises(RuntimeError):
        mgr.dismiss()


async def test_operation_flags_reach_the_cli():
    cli = FakeCli()
    await manager(cli).start(
        "f.cab", "d", device_name="N", version="1", allow_older=True, allow_reinstall=True
    )
    assert cli.calls == [("f.cab", "d", True, True)]


async def test_there_is_no_cancel_method():
    """Deliberate omission. Killing fwupdtool between Writing and Verifying can
    leave partially written firmware."""
    assert not hasattr(FlashManager, "cancel")
