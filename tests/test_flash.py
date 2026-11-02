from pathlib import Path

import pytest

from fwupd_webui.fwupd.cli import FwupdCommandFailed, ProgressLine
from fwupd_webui.fwupd.download import FirmwareDownloadError
from fwupd_webui.fwupd.flash import FlashManager, FlashStatus

SHA = "a" * 64


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


def fake_download(url, sha256, dest_dir):
    path = dest_dir / "firmware.cab"
    path.write_bytes(b"cab")
    return path


def manager(cli) -> FlashManager:
    return FlashManager(cli, download=fake_download)


async def test_a_successful_flash_ends_succeeded():
    mgr = manager(FakeCli(progress=[("Writing", 50.0), ("Verifying", 100.0)]))
    job = await mgr.start(
        "https://lvfs/f.cab", "dev-1", sha256=SHA, device_name="NVMe", version="2.0"
    )

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
    await mgr.start("https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1")

    assert seen == [("Writing", 10.0), ("Writing", 90.0)]


async def test_a_failed_flash_records_exit_code_and_last_phase():
    cli = FakeCli(
        progress=[("Writing", 40.0)],
        error=FwupdCommandFailed(3, "device is locked"),
    )
    job = await manager(cli).start(
        "https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1"
    )

    assert job.status is FlashStatus.FAILED
    assert job.exit_code == 3
    assert "device is locked" in job.error
    assert job.phase == "Writing", "the phase reached tells you where it broke"


async def test_log_lines_are_retained_for_the_failure_view():
    cli = FakeCli(logs=["WARNING: ESP not found"], error=FwupdCommandFailed(1, "boom"))
    job = await manager(cli).start(
        "https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1"
    )

    assert any("ESP not found" in line for line in job.log)


async def test_only_one_job_runs_at_a_time():
    mgr = manager(FakeCli())
    await mgr.start("https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1")
    mgr.job.status = FlashStatus.RUNNING  # simulate an in-flight job

    with pytest.raises(RuntimeError, match="already running"):
        await mgr.start("https://lvfs/f.cab", "d2", sha256=SHA, device_name="N", version="1")


async def test_active_is_false_once_a_job_finishes():
    mgr = manager(FakeCli())
    await mgr.start("https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1")
    assert mgr.active is False
    assert mgr.job is not None, "a finished job is retained so the result stays readable"


async def test_dismiss_clears_the_finished_job():
    mgr = manager(FakeCli())
    await mgr.start("https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1")
    mgr.dismiss()
    assert mgr.job is None


async def test_dismiss_refuses_while_a_job_runs():
    mgr = manager(FakeCli())
    await mgr.start("https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1")
    mgr.job.status = FlashStatus.RUNNING

    with pytest.raises(RuntimeError):
        mgr.dismiss()


async def test_operation_flags_reach_the_cli():
    cli = FakeCli()
    await manager(cli).start(
        "https://lvfs/f.cab",
        "d",
        sha256=SHA,
        device_name="N",
        version="1",
        allow_older=True,
        allow_reinstall=True,
    )
    assert [call[1:] for call in cli.calls] == [("d", True, True)]


async def test_there_is_no_cancel_method():
    """Deliberate omission. Killing fwupdtool between Writing and Verifying can
    leave partially written firmware."""
    assert not hasattr(FlashManager, "cancel")


async def test_the_downloaded_file_is_what_gets_installed_and_is_then_removed():
    seen = []
    cli = FakeCli()

    def spy(target, device_id, **kwargs):
        seen.append(Path(target))
        assert Path(target).read_bytes() == b"cab"

    cli.install = spy
    job = await manager(cli).start(
        "https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1"
    )

    assert job.status is FlashStatus.SUCCEEDED
    assert seen[0].name == "firmware.cab"
    assert not seen[0].exists()


async def test_the_url_and_checksum_reach_the_download():
    calls = []

    def spy(url, sha256, dest_dir):
        calls.append((url, sha256))
        return fake_download(url, sha256, dest_dir)

    await FlashManager(FakeCli(), download=spy).start(
        "https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1"
    )
    assert calls == [("https://lvfs/f.cab", SHA)]


async def test_a_failed_download_fails_the_job_and_never_installs():
    def bad_download(url, sha256, dest_dir):
        raise FirmwareDownloadError("checksum mismatch")

    cli = FakeCli()
    job = await FlashManager(cli, download=bad_download).start(
        "https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1"
    )

    assert job.status is FlashStatus.FAILED
    assert "checksum mismatch" in job.error
    assert job.phase == "Downloading"
    assert cli.calls == []


async def test_a_disk_error_fails_the_job_instead_of_leaving_it_running():
    def full_disk(url, sha256, dest_dir):
        raise OSError("No space left on device")

    mgr = FlashManager(FakeCli(), download=full_disk)
    job = await mgr.start("https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1")

    assert job.status is FlashStatus.FAILED
    assert mgr.active is False


async def test_the_file_is_kept_in_the_work_directory(tmp_path):
    work_dir = tmp_path / "state"
    seen = []
    cli = FakeCli()
    cli.install = lambda target, device_id, **kwargs: seen.append(Path(target))

    await FlashManager(cli, work_dir=work_dir, download=fake_download).start(
        "https://lvfs/f.cab", "d", sha256=SHA, device_name="N", version="1"
    )

    assert work_dir in seen[0].parents
    assert list(work_dir.iterdir()) == []
