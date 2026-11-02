import asyncio
import io
import threading
from pathlib import Path

import pytest

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdCommandFailed
from fwupd_webui.fwupd.flash import FlashStatus
from fwupd_webui.fwupd.models import Device
from fwupd_webui.fwupd.service import FwupdService

NAME = "Unifying Receiver"
SHA_A = "a" * 64
SHA_B = "b" * 64


def release(
    version="2.0", url="https://lvfs/a.cab", sha=SHA_A, flags=("trusted-metadata",), **rest
):
    return {
        "Version": version,
        "Locations": [url],
        "Checksum": ["0" * 40, sha],
        "Flags": list(flags),
        **rest,
    }


async def flash_to_end(svc, *args, **kwargs):
    job = await svc.start_flash(*args, **kwargs)
    await svc._flash_task
    return job


def recording_download(svc):
    """Replaces the network. Records what would have been fetched."""
    svc.downloads = []

    def fake(url, sha256, dest_dir):
        svc.downloads.append((url, sha256))
        path = dest_dir / "firmware.cab"
        path.write_bytes(b"cab")
        return path

    svc.flash_manager._download = fake


def make_device(plugin="logitech_hidpp", name="Unifying Receiver", version="1.0", releases=None):
    return Device.model_validate(
        {
            "DeviceId": "dev-1",
            "Name": name,
            "Plugin": plugin,
            "Version": version,
            "Flags": ["updatable"],
            "Releases": [releases or release()],
        }
    )


class StubCli:
    def __init__(self, devices):
        self._devices = devices
        self.installs = []
        self.get_devices_after_flash = None

    def get_devices(self):
        if self.installs and self.get_devices_after_flash is not None:
            self.get_devices_after_flash()
        return self._devices

    def get_updates(self):
        return self._devices

    def version(self):
        return "2.1.7"

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
        self.installs.append((target, device_id, allow_older, allow_reinstall))


def service(tmp_path, *, enabled=True, plugin="logitech_hidpp", releases=None):
    cli = StubCli([make_device(plugin=plugin, releases=releases)])
    config = Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": "true" if enabled else "false"})
    svc = FwupdService(cli, config, tmp_path)
    svc._stub_cli = cli
    recording_download(svc)
    return svc


async def test_flashing_with_the_typed_name_succeeds(tmp_path):
    svc = service(tmp_path)
    job = await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)

    assert job.status is FlashStatus.SUCCEEDED
    assert svc.downloads == [("https://lvfs/a.cab", SHA_A)]
    target, device_id, allow_older, allow_reinstall = svc._stub_cli.installs[0]
    assert (Path(target).name, device_id) == ("firmware.cab", "dev-1")
    assert (allow_older, allow_reinstall) == (False, False)


async def test_flashing_is_refused_when_disabled(tmp_path):
    svc = service(tmp_path, enabled=False)
    with pytest.raises(PermissionError, match="FWUPD_WEBUI_ENABLE_FLASHING"):
        await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)


async def test_every_device_is_refused_without_the_typed_name(tmp_path):
    """No device flashes without confirmation, storage or not."""
    for plugin in ("logitech_hidpp", "ata", "nvme"):
        svc = service(tmp_path, plugin=plugin)
        with pytest.raises(PermissionError, match="Type the device name"):
            await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=None)


async def test_storage_device_proceeds_with_the_correct_typed_name(tmp_path):
    svc = service(tmp_path, plugin="ata")
    job = await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)
    assert job.status is FlashStatus.SUCCEEDED


async def test_a_near_miss_name_is_refused(tmp_path):
    svc = service(tmp_path, plugin="ata")
    with pytest.raises(PermissionError):
        await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name="Unifying")


async def test_downgrade_sets_allow_older(tmp_path):
    svc = service(tmp_path)
    await flash_to_end(svc, "dev-1", "2.0", operation="downgrade", typed_name=NAME)
    assert svc._stub_cli.installs[0][2] is True


async def test_reinstall_sets_allow_reinstall(tmp_path):
    svc = service(tmp_path)
    await flash_to_end(svc, "dev-1", "2.0", operation="reinstall", typed_name=NAME)
    assert svc._stub_cli.installs[0][3] is True


async def test_unknown_device_is_rejected(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(LookupError):
        await flash_to_end(svc, "nope", "2.0", operation="upgrade", typed_name=NAME)


async def test_unknown_version_is_rejected(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(LookupError, match="9.9"):
        await flash_to_end(svc, "dev-1", "9.9", operation="upgrade", typed_name=NAME)


async def test_staged_firmware_is_detected(tmp_path):
    """The stub device still reports its old version after the flash, which is
    what every needs-reboot device on the deployed host does."""
    svc = service(tmp_path)
    job = await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)
    assert job.staged is True
    assert job.installed_version == "1.0"


async def test_a_live_update_is_reported_as_live(tmp_path):
    svc = service(tmp_path)
    cli = svc._stub_cli

    def bump_version():
        cli._devices = [make_device(version="2.0")]

    cli.get_devices_after_flash = bump_version
    job = await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)
    assert job.staged is False
    assert job.installed_version == "2.0"


async def test_a_failed_re_enumeration_does_not_fail_the_flash(tmp_path):
    """Best-effort: not knowing whether firmware is live must not turn a
    successful write into a reported failure."""
    svc = service(tmp_path)

    def boom():
        raise FwupdCommandFailed(1, "enumeration broke")

    svc._stub_cli.get_devices_after_flash = boom
    job = await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)

    assert job.status is FlashStatus.SUCCEEDED
    assert job.staged is None


async def test_start_flash_does_not_deadlock_on_the_service_lock(tmp_path):
    """Regression guard. The post-flash re-enumeration runs while the hardware
    lock is already held; routing it through self._call would try to acquire
    that same lock and hang forever."""
    svc = service(tmp_path)
    job = await asyncio.wait_for(
        flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME),
        timeout=5,
    )
    assert job.status is FlashStatus.SUCCEEDED


async def test_the_lock_is_released_after_a_flash(tmp_path):
    """A flash must not leave the lock held, or every later page would hang."""
    svc = service(tmp_path)
    await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)
    inventory = await svc.inventory()
    assert len(inventory.devices) == 1


async def test_cached_inventory_does_not_re_enumerate_within_the_window(tmp_path):
    """A monitor polling every few seconds must not issue fresh NVMe and SCSI
    commands each time."""
    svc = service(tmp_path)
    cli = svc._stub_cli
    calls = []
    original = cli.get_devices
    cli.get_devices = lambda: (calls.append(1), original())[1]

    await svc.cached_inventory(max_age_seconds=300)
    first = len(calls)
    await svc.cached_inventory(max_age_seconds=300)
    await svc.cached_inventory(max_age_seconds=300)

    assert len(calls) == first, "cached window should serve without touching hardware"


async def test_cached_inventory_re_enumerates_once_stale(tmp_path):
    svc = service(tmp_path)
    cli = svc._stub_cli
    calls = []
    original = cli.get_devices
    cli.get_devices = lambda: (calls.append(1), original())[1]

    await svc.cached_inventory(max_age_seconds=0)
    first = len(calls)
    await svc.cached_inventory(max_age_seconds=0)

    assert len(calls) > first


async def test_cached_inventory_serves_the_snapshot_during_a_flash(tmp_path):
    """Enumerating mid-flash would block on the hardware lock behind a write
    that can take minutes."""
    from fwupd_webui.fwupd.flash import FlashJob, FlashStatus

    svc = service(tmp_path)
    await svc.cached_inventory(max_age_seconds=0)

    running = FlashJob(device_id="dev-1", device_name="N", version="1")
    running.status = FlashStatus.RUNNING
    svc.flash_manager.job = running

    cli = svc._stub_cli
    cli.get_devices = lambda: pytest.fail("must not enumerate during a flash")

    inventory = await svc.cached_inventory(max_age_seconds=0)
    assert inventory is not None


def variant_service(tmp_path, *, updates=None):
    device = Device.model_validate(
        {
            "DeviceId": "dev-1",
            "Name": NAME,
            "Plugin": "logitech_hidpp",
            "Version": "1.0",
            "Flags": ["updatable"],
            "Releases": [
                release(AppstreamId="a.firmware"),
                release(url="https://lvfs/b.cab", sha=SHA_B, AppstreamId="b.firmware"),
            ],
        }
    )
    cli = StubCli([device])
    if updates is not None:
        cli.get_updates = lambda: updates
    config = Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": "true"})
    svc = FwupdService(cli, config, tmp_path)
    svc._stub_cli = cli
    recording_download(svc)
    return svc


async def test_ambiguous_version_is_refused_rather_than_guessed(tmp_path):
    svc = variant_service(tmp_path)
    with pytest.raises(LookupError, match="more than one"):
        await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)
    assert svc._stub_cli.installs == []


async def test_the_chosen_variant_is_the_one_flashed(tmp_path):
    svc = variant_service(tmp_path)
    await flash_to_end(
        svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME, appstream_id="b.firmware"
    )
    assert svc.downloads == [("https://lvfs/b.cab", SHA_B)]


async def test_release_get_updates_did_not_confirm_is_refused(tmp_path):
    """The page hides the button for these. This holds whatever the page offered."""
    svc = variant_service(tmp_path, updates=[])
    with pytest.raises(PermissionError, match="not confirmed"):
        await flash_to_end(
            svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME, appstream_id="b.firmware"
        )
    assert svc._stub_cli.installs == []


async def test_flash_is_refused_when_confirmation_cannot_be_checked(tmp_path):
    def broken():
        raise FwupdCommandFailed(1, "metadata is missing")

    svc = variant_service(tmp_path)
    svc._stub_cli.get_updates = broken
    with pytest.raises(PermissionError, match="could not check"):
        await flash_to_end(
            svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME, appstream_id="b.firmware"
        )
    assert svc._stub_cli.installs == []


@pytest.mark.parametrize(
    ("releases", "error", "message"),
    [
        (release(flags=()), PermissionError, "signed metadata"),
        (release(url="./fakedevice124.cab"), LookupError, "no HTTPS download location"),
        (release(url="http://lvfs/a.cab"), LookupError, "no HTTPS download location"),
        ({**release(), "Checksum": ["0" * 40]}, LookupError, "no SHA-256"),
    ],
)
async def test_a_release_that_cannot_be_verified_is_refused(tmp_path, releases, error, message):
    svc = service(tmp_path, releases=releases)
    with pytest.raises(error, match=message):
        await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)
    assert svc.downloads == []
    assert svc._stub_cli.installs == []


async def test_the_real_download_rejects_the_wrong_file_before_any_install(tmp_path, monkeypatch):
    """Everything but the network is real: a bad file must never reach fwupdtool."""
    from fwupd_webui.fwupd import download as download_module
    from fwupd_webui.fwupd.download import download_verified

    monkeypatch.setattr(
        download_module._opener, "open", lambda request, timeout: io.BytesIO(b"tampered")
    )
    svc = service(tmp_path)
    svc.flash_manager._download = download_verified

    job = await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)

    assert job.status is FlashStatus.FAILED
    assert "checksum mismatch" in job.error
    assert svc._stub_cli.installs == []
    assert [p for p in tmp_path.iterdir() if p.name.startswith("flash-")] == []


def blocked_download(svc):
    """A download that stays in progress until the returned event is set."""
    release_download = threading.Event()
    fake = svc.flash_manager._download

    def wait_then_fake(url, sha256, dest_dir):
        assert release_download.wait(timeout=5)
        return fake(url, sha256, dest_dir)

    svc.flash_manager._download = wait_then_fake
    return release_download


async def test_start_flash_returns_while_the_flash_is_still_running(tmp_path):
    svc = service(tmp_path)
    release_download = blocked_download(svc)

    job = await asyncio.wait_for(
        svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=NAME), timeout=2
    )
    assert job.finished is False
    assert svc.flash_manager.active is True
    assert svc._stub_cli.installs == []

    release_download.set()
    await svc._flash_task
    assert job.status is FlashStatus.SUCCEEDED
    assert len(svc._stub_cli.installs) == 1


async def test_a_second_flash_is_refused_while_one_is_running(tmp_path):
    svc = service(tmp_path)
    release_download = blocked_download(svc)
    await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=NAME)

    with pytest.raises(RuntimeError, match="already running"):
        await asyncio.wait_for(
            svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=NAME), timeout=2
        )

    release_download.set()
    await svc._flash_task
    assert len(svc._stub_cli.installs) == 1


async def test_a_double_click_writes_once(tmp_path):
    """Both requests arrive before either has finished validating. The second must
    not wait and then flash again on a device still reporting its old version."""
    svc = service(tmp_path)
    first = asyncio.create_task(
        svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=NAME)
    )
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="already running"):
        await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=NAME)

    await first
    await svc._flash_task
    assert len(svc._stub_cli.installs) == 1


async def test_a_refused_flash_does_not_block_the_next_one(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(PermissionError):
        await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name="wrong")

    job = await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)
    assert job.status is FlashStatus.SUCCEEDED


async def test_the_status_api_can_answer_during_a_flash_with_nothing_cached_before(tmp_path):
    svc = service(tmp_path)
    release_download = blocked_download(svc)
    await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=NAME)

    inventory = await asyncio.wait_for(svc.cached_inventory(), timeout=2)
    assert len(inventory.devices) == 1

    release_download.set()
    await svc._flash_task


async def test_the_outcome_is_recorded_before_the_job_reads_as_finished(tmp_path):
    svc = service(tmp_path)
    seen = []
    svc._stub_cli.get_devices_after_flash = lambda: seen.append(svc.flash_manager.job.finished)

    await flash_to_end(svc, "dev-1", "2.0", operation="upgrade", typed_name=NAME)

    assert seen == [False]
    assert svc.flash_manager.job.staged is True
