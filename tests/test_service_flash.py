import pytest

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdCommandFailed
from fwupd_webui.fwupd.flash import FlashStatus
from fwupd_webui.fwupd.models import Device
from fwupd_webui.fwupd.service import FwupdService


def make_device(plugin="nvme", name="NVMe SSD", version="1.0"):
    return Device.model_validate(
        {
            "DeviceId": "dev-1",
            "Name": name,
            "Plugin": plugin,
            "Version": version,
            "Flags": ["updatable"],
            "Releases": [{"Version": "2.0", "Uri": "https://lvfs/f.cab"}],
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


def service(tmp_path, *, enabled=True, plugin="nvme"):
    cli = StubCli([make_device(plugin=plugin)])
    config = Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": "true" if enabled else "false"})
    svc = FwupdService(cli, config, tmp_path)
    svc._stub_cli = cli
    return svc


async def test_flashing_an_allowlisted_device_succeeds(tmp_path):
    svc = service(tmp_path)
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)

    assert job.status is FlashStatus.SUCCEEDED
    assert svc._stub_cli.installs == [("https://lvfs/f.cab", "dev-1", False, False)]


async def test_flashing_is_refused_when_disabled(tmp_path):
    svc = service(tmp_path, enabled=False)
    with pytest.raises(PermissionError, match="FWUPD_WEBUI_ENABLE_FLASHING"):
        await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)


async def test_blocked_plugin_is_refused_without_the_typed_name(tmp_path):
    svc = service(tmp_path, plugin="ata")
    with pytest.raises(PermissionError, match="typing the device name"):
        await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)


async def test_blocked_plugin_proceeds_with_the_correct_typed_name(tmp_path):
    svc = service(tmp_path, plugin="ata")
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name="NVMe SSD")
    assert job.status is FlashStatus.SUCCEEDED


async def test_blocked_plugin_is_refused_on_a_near_miss(tmp_path):
    svc = service(tmp_path, plugin="ata")
    with pytest.raises(PermissionError):
        await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name="NVMe")


async def test_downgrade_sets_allow_older(tmp_path):
    svc = service(tmp_path)
    await svc.start_flash("dev-1", "2.0", operation="downgrade", typed_name=None)
    assert svc._stub_cli.installs[0][2] is True


async def test_reinstall_sets_allow_reinstall(tmp_path):
    svc = service(tmp_path)
    await svc.start_flash("dev-1", "2.0", operation="reinstall", typed_name=None)
    assert svc._stub_cli.installs[0][3] is True


async def test_unknown_device_is_rejected(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(LookupError):
        await svc.start_flash("nope", "2.0", operation="upgrade", typed_name=None)


async def test_unknown_version_is_rejected(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(LookupError, match="9.9"):
        await svc.start_flash("dev-1", "9.9", operation="upgrade", typed_name=None)


async def test_staged_firmware_is_detected(tmp_path):
    """The stub device still reports its old version after the flash, which is
    what every needs-reboot device on the deployed host does."""
    svc = service(tmp_path)
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)
    assert job.staged is True
    assert job.installed_version == "1.0"


async def test_a_live_update_is_reported_as_live(tmp_path):
    svc = service(tmp_path)
    cli = svc._stub_cli

    def bump_version():
        cli._devices = [make_device(version="2.0")]

    cli.get_devices_after_flash = bump_version
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)
    assert job.staged is False
    assert job.installed_version == "2.0"


async def test_a_failed_re_enumeration_does_not_fail_the_flash(tmp_path):
    """Best-effort: not knowing whether firmware is live must not turn a
    successful write into a reported failure."""
    svc = service(tmp_path)

    def boom():
        raise FwupdCommandFailed(1, "enumeration broke")

    svc._stub_cli.get_devices_after_flash = boom
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)

    assert job.status is FlashStatus.SUCCEEDED
    assert job.staged is None


async def test_start_flash_does_not_deadlock_on_the_service_lock(tmp_path):
    """Regression guard. The post-flash re-enumeration runs while the hardware
    lock is already held; routing it through self._call would try to acquire
    that same lock and hang forever."""
    import asyncio

    svc = service(tmp_path)
    job = await asyncio.wait_for(
        svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None),
        timeout=5,
    )
    assert job.status is FlashStatus.SUCCEEDED


async def test_the_lock_is_released_after_a_flash(tmp_path):
    """A flash must not leave the lock held, or every later page would hang."""
    svc = service(tmp_path)
    await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)
    inventory = await svc.inventory()
    assert len(inventory.devices) == 1
