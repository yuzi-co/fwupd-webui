import pytest
from fastapi.testclient import TestClient

from fwupd_webui.config import Config
from fwupd_webui.fwupd.models import Device, Release
from fwupd_webui.fwupd.service import DeviceView, Inventory, MetadataStatus
from fwupd_webui.web.app import create_app


class FakeService:
    def __init__(self, inventory: Inventory):
        self._inventory = inventory

    async def inventory(self) -> Inventory:
        return self._inventory

    async def refresh(self) -> MetadataStatus:
        return self._inventory.metadata

    async def refresh_if_stale(self) -> MetadataStatus:
        return self._inventory.metadata


@pytest.fixture
def client() -> TestClient:
    inventory = Inventory(
        devices=[
            DeviceView(
                device=Device.model_validate(
                    {
                        "DeviceId": "nvme-1",
                        "Name": "Samsung SSD 990 PRO",
                        "Vendor": "Samsung",
                        "Version": "4B2QJXD7",
                        "Plugin": "nvme",
                        "Protocol": "org.nvmexpress",
                        "Guid": ["1111-2222", "3333-4444"],
                        "Flags": ["internal", "updatable"],
                    }
                ),
                available=[
                    Release.model_validate(
                        {
                            "Version": "5B2QJXD7",
                            "Urgency": "high",
                            "Summary": "Improves thermal throttling",
                            "Description": "<p>Fixes an issue under sustained write load.</p>",
                            "Uri": "https://fwupd.org/downloads/abc.cab",
                        }
                    )
                ],
            ),
            DeviceView(
                device=Device.model_validate({"DeviceId": "cpu-1", "Name": "Ryzen 7 5700G"}),
                available=[],
            ),
        ],
        metadata=MetadataStatus(last_refresh=1000.0, age_seconds=60.0, stale=False),
        fwupd_version="2.0.20",
    )
    return TestClient(create_app(FakeService(inventory), Config.from_env({})))


def test_detail_shows_guids(client):
    body = client.get("/devices/nvme-1").text
    assert "1111-2222" in body
    assert "3333-4444" in body


def test_detail_shows_device_id_and_protocol(client):
    body = client.get("/devices/nvme-1").text
    assert "nvme-1" in body
    assert "org.nvmexpress" in body


def test_detail_shows_release_metadata(client):
    body = client.get("/devices/nvme-1").text
    assert "5B2QJXD7" in body
    assert "high" in body
    assert "Improves thermal throttling" in body


def test_detail_links_to_the_release_uri(client):
    assert "https://fwupd.org/downloads/abc.cab" in client.get("/devices/nvme-1").text


def test_detail_renders_release_description_as_readable_text(client):
    """fwupd descriptions carry XHTML from LVFS. It must not be injected raw,
    and it must not be shown to the user as literal tags either -- escaping
    alone put '<p>Fixes an issue...</p>' on screen, tags and all."""
    body = client.get("/devices/nvme-1").text

    assert "<p>Fixes an issue" not in body, "vendor markup must not be injected"
    assert "&lt;p&gt;" not in body, "tags must not be visible to the user"
    assert "Fixes an issue" in body, "the text itself must survive"


def test_detail_for_device_without_releases(client):
    body = client.get("/devices/cpu-1").text
    assert "No releases available" in body


def test_unknown_device_returns_404(client):
    assert client.get("/devices/does-not-exist").status_code == 404
