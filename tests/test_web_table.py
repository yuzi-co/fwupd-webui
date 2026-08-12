import pytest
from fastapi.testclient import TestClient

from fwupd_webui.config import Config
from fwupd_webui.fwupd.models import Device, Release
from fwupd_webui.fwupd.service import DeviceView, Inventory, MetadataStatus
from fwupd_webui.web.app import create_app


class FakeService:
    def __init__(self, inventory: Inventory):
        self._inventory = inventory
        self.refresh_calls = 0

    async def inventory(self) -> Inventory:
        return self._inventory

    async def refresh(self) -> MetadataStatus:
        self.refresh_calls += 1
        return self._inventory.metadata

    async def refresh_if_stale(self) -> MetadataStatus:
        return self._inventory.metadata


def device(device_id="dev-1", name="Samsung SSD 990 PRO", **kwargs) -> Device:
    payload = {"DeviceId": device_id, "Name": name}
    payload.update(kwargs)
    return Device.model_validate(payload)


def inventory_with_one_update() -> Inventory:
    return Inventory(
        devices=[
            DeviceView(
                device=device(
                    device_id="nvme-1",
                    name="Samsung SSD 990 PRO",
                    Vendor="Samsung",
                    Version="4B2QJXD7",
                    Plugin="nvme",
                    Flags=["internal", "updatable"],
                ),
                available=[Release.model_validate({"Version": "5B2QJXD7", "Urgency": "high"})],
            ),
            DeviceView(
                device=device(
                    device_id="hba-1",
                    name="LSI SAS3008",
                    Vendor="Broadcom",
                    Version="16.00.12.00",
                    Plugin="scsi",
                    Flags=["internal"],
                ),
                available=[],
            ),
        ],
        metadata=MetadataStatus(last_refresh=1000.0, age_seconds=3600.0, stale=False),
        fwupd_version="2.0.20",
    )


@pytest.fixture
def client() -> TestClient:
    service = FakeService(inventory_with_one_update())
    app = create_app(service, Config.from_env({}))
    app.state.fake_service = service
    return TestClient(app)


def test_index_lists_every_device(client):
    body = client.get("/").text
    assert "Samsung SSD 990 PRO" in body
    assert "LSI SAS3008" in body


def test_index_renders_a_device_with_no_name():
    """Regression: an unnamed linux_display device used to 500 the whole page."""
    inventory = Inventory(
        devices=[
            DeviceView(
                device=Device.model_validate({"DeviceId": "u", "Plugin": "linux_display"}),
                available=[],
            )
        ],
        metadata=MetadataStatus(last_refresh=1000.0, age_seconds=60.0, stale=False),
        fwupd_version="2.0.20",
    )
    client = TestClient(create_app(FakeService(inventory), Config.from_env({})))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Unknown linux_display device" in resp.text


def test_index_shows_current_versions(client):
    body = client.get("/").text
    assert "4B2QJXD7" in body
    assert "16.00.12.00" in body


def test_index_marks_the_device_with_an_update(client):
    body = client.get("/").text
    assert "5B2QJXD7" in body


def test_index_marks_the_device_without_an_update(client):
    assert "up to date" in client.get("/").text


def test_index_shows_fwupd_version(client):
    assert "2.0.20" in client.get("/").text


def test_index_shows_metadata_age(client):
    assert "1h old" in client.get("/").text


def test_index_serves_local_htmx_not_a_cdn(client):
    body = client.get("/").text
    assert "/static/htmx.min.js" in body
    assert "unpkg.com" not in body
    assert "cdn." not in body


def test_static_htmx_is_served(client):
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
    assert len(resp.content) > 1000


def test_refresh_endpoint_calls_service_and_returns_table(client):
    resp = client.post("/refresh")
    assert resp.status_code == 200
    assert client.app.state.fake_service.refresh_calls == 1
    assert "Samsung SSD 990 PRO" in resp.text


def test_header_shows_the_application_version(client):
    """fwupd's version was already shown; the app's own was not, so 'which
    build is this' had no answer in the UI."""
    from fwupd_webui import app_version

    body = client.get("/").text
    assert f"v{app_version()}" in body


def test_header_distinguishes_app_version_from_fwupd_version(client):
    body = client.get("/").text
    assert "fwupd-webui" in body
    assert "fwupd 2.0.20" in body
