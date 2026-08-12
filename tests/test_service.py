import asyncio

import pytest

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdCommandFailed
from fwupd_webui.fwupd.models import Device
from fwupd_webui.fwupd.service import FwupdService


def make_device(device_id: str, name: str, flags=None, releases=None) -> Device:
    return Device.model_validate(
        {
            "DeviceId": device_id,
            "Name": name,
            "Flags": flags or [],
            "Releases": releases or [],
        }
    )


class FakeCli:
    def __init__(self, devices=None, updates=None, version="2.0.20"):
        self._devices = devices or []
        self._updates = updates or []
        self._version = version
        self.refresh_calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self.refresh_error: Exception | None = None

    def _enter(self):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)

    def get_devices(self):
        self._enter()
        try:
            return list(self._devices)
        finally:
            self.concurrent -= 1

    def get_updates(self):
        self._enter()
        try:
            return list(self._updates)
        finally:
            self.concurrent -= 1

    def refresh(self):
        self.refresh_calls += 1
        if self.refresh_error:
            raise self.refresh_error

    def version(self):
        return self._version


@pytest.fixture
def config():
    return Config.from_env({})


async def test_inventory_joins_updates_onto_devices(tmp_path, config):
    cli = FakeCli(
        devices=[make_device("a", "Alpha"), make_device("b", "Bravo")],
        updates=[make_device("b", "Bravo", releases=[{"Version": "2.0"}])],
    )
    service = FwupdService(cli, config, tmp_path)
    inv = await service.inventory()

    by_id = {v.device.device_id: v for v in inv.devices}
    assert by_id["a"].has_update is False
    assert by_id["b"].has_update is True
    assert by_id["b"].available[0].version == "2.0"


async def test_inventory_reports_fwupd_version(tmp_path, config):
    service = FwupdService(FakeCli(devices=[make_device("a", "Alpha")]), config, tmp_path)
    assert (await service.inventory()).fwupd_version == "2.0.20"


async def test_devices_with_updates_sort_first(tmp_path, config):
    cli = FakeCli(
        devices=[make_device("a", "Alpha"), make_device("z", "Zulu")],
        updates=[make_device("z", "Zulu", releases=[{"Version": "9.0"}])],
    )
    service = FwupdService(cli, config, tmp_path)
    inv = await service.inventory()
    assert [v.device.name for v in inv.devices] == ["Zulu", "Alpha"]


async def test_sorting_survives_a_device_with_no_name(tmp_path, config):
    """Real hardware includes devices with no Name; sorting must not crash."""
    unnamed = Device.model_validate({"DeviceId": "u", "Plugin": "linux_display"})
    cli = FakeCli(devices=[unnamed, make_device("a", "Alpha")])
    service = FwupdService(cli, config, tmp_path)
    inv = await service.inventory()
    assert [v.device.display_name for v in inv.devices] == [
        "Alpha",
        "Unknown linux_display device",
    ]


async def test_update_failure_does_not_hide_devices(tmp_path, config):
    """A broken get-updates must still leave the user with an inventory."""

    class BrokenUpdates(FakeCli):
        def get_updates(self):
            raise FwupdCommandFailed(2, "metadata is missing")

    cli = BrokenUpdates(devices=[make_device("a", "Alpha")])
    service = FwupdService(cli, config, tmp_path)
    inv = await service.inventory()
    assert len(inv.devices) == 1
    assert inv.metadata.error is not None


async def test_calls_are_serialized(tmp_path, config):
    cli = FakeCli(devices=[make_device("a", "Alpha")])
    service = FwupdService(cli, config, tmp_path)
    await asyncio.gather(*(service.inventory() for _ in range(5)))
    assert cli.max_concurrent == 1


async def test_refresh_writes_stamp_and_clears_staleness(tmp_path, config):
    cli = FakeCli()
    service = FwupdService(cli, config, tmp_path, clock=lambda: 1000.0)
    status = await service.refresh()
    assert cli.refresh_calls == 1
    assert status.error is None
    assert status.stale is False
    assert (tmp_path / ".webui-last-refresh").exists()


async def test_refresh_failure_is_not_fatal(tmp_path, config):
    cli = FakeCli()
    cli.refresh_error = FwupdCommandFailed(1, "network unreachable")
    service = FwupdService(cli, config, tmp_path, clock=lambda: 1000.0)
    status = await service.refresh()
    assert "network unreachable" in status.error
    assert not (tmp_path / ".webui-last-refresh").exists()


async def test_refresh_if_stale_skips_when_cache_is_fresh(tmp_path, config):
    cli = FakeCli()
    service = FwupdService(cli, config, tmp_path, clock=lambda: 100_000.0)
    await service.refresh()
    assert cli.refresh_calls == 1
    await service.refresh_if_stale()
    assert cli.refresh_calls == 1, "fresh cache must not trigger a second refresh"


async def test_refresh_if_stale_refreshes_when_cache_is_old(tmp_path, config):
    cli = FakeCli()
    now = [100_000.0]
    service = FwupdService(cli, config, tmp_path, clock=lambda: now[0])
    await service.refresh()
    now[0] += 25 * 3600  # default threshold is 24h
    await service.refresh_if_stale()
    assert cli.refresh_calls == 2


async def test_refresh_if_stale_refreshes_when_never_run(tmp_path, config):
    cli = FakeCli()
    service = FwupdService(cli, config, tmp_path, clock=lambda: 100_000.0)
    await service.refresh_if_stale()
    assert cli.refresh_calls == 1


async def test_metadata_status_reports_age(tmp_path, config):
    cli = FakeCli()
    now = [100_000.0]
    service = FwupdService(cli, config, tmp_path, clock=lambda: now[0])
    await service.refresh()
    now[0] += 3600
    inv = await service.inventory()
    assert inv.metadata.age_seconds == pytest.approx(3600, abs=1)
    assert inv.metadata.stale is False


async def test_duplicate_release_versions_are_collapsed(tmp_path):
    """Real fwupd returned 1.2.4 twice for the synthetic device, and the UI
    listed it twice with identical changelogs."""
    from fwupd_webui.config import Config
    from fwupd_webui.fwupd.models import Device
    from fwupd_webui.fwupd.service import FwupdService

    payload = {
        "DeviceId": "d1",
        "Name": "Webcam",
        "Plugin": "test",
        "Flags": ["updatable"],
        "Releases": [
            {"Version": "1.2.4"},
            {"Version": "1.2.3"},
            {"Version": "1.2.4"},
        ],
    }

    class Cli:
        def get_devices(self):
            return [Device.model_validate(payload)]

        def get_updates(self):
            return [Device.model_validate(payload)]

        def version(self):
            return "2.1.7"

    svc = FwupdService(Cli(), Config.from_env({}), tmp_path)
    inv = await svc.inventory()
    versions = [r.version for r in inv.devices[0].available]
    assert versions == ["1.2.4", "1.2.3"], versions
