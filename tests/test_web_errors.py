import pytest
from fastapi.testclient import TestClient

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdNotFound
from fwupd_webui.fwupd.service import Inventory, MetadataStatus
from fwupd_webui.web.app import create_app


class StubService:
    def __init__(self, inventory=None, error=None):
        self._inventory = inventory
        self._error = error

    async def inventory(self):
        if self._error:
            raise self._error
        return self._inventory

    async def refresh(self):
        if self._error:
            raise self._error
        return MetadataStatus()

    async def refresh_if_stale(self):
        return MetadataStatus()


def empty_inventory(metadata_error=None) -> Inventory:
    return Inventory(
        devices=[],
        metadata=MetadataStatus(
            last_refresh=None, age_seconds=None, stale=True, error=metadata_error
        ),
        fwupd_version="2.0.20",
    )


def test_zero_devices_renders_diagnostic_page_not_blank_table():
    client = TestClient(create_app(StubService(empty_inventory()), Config.from_env({})))
    body = client.get("/").text
    assert "No devices" in body
    assert "/run/udev" in body
    assert '<table id="device-table"' not in body


def test_empty_state_lists_mount_status():
    client = TestClient(create_app(StubService(empty_inventory()), Config.from_env({})))
    body = client.get("/").text
    for mount in ("/sys", "/dev", "/run/udev"):
        assert mount in body


def test_metadata_error_renders_banner_but_keeps_page_usable():
    client = TestClient(
        create_app(StubService(empty_inventory("network unreachable")), Config.from_env({}))
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert "network unreachable" in resp.text


def test_fwupd_failure_renders_error_page_not_500():
    client = TestClient(
        create_app(
            StubService(error=FwupdNotFound("fwupdtool not found on PATH")), Config.from_env({})
        )
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert "fwupdtool not found on PATH" in resp.text


@pytest.mark.parametrize("path", ["/", "/refresh"])
def test_no_route_raises_uncaught_fwupd_error(path):
    client = TestClient(
        create_app(StubService(error=FwupdNotFound("boom")), Config.from_env({})),
        raise_server_exceptions=False,
    )
    method = client.get if path == "/" else client.post
    resp = method(path)
    assert resp.status_code != 500
    assert "boom" in resp.text
