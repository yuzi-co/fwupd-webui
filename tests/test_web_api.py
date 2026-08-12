import pytest
from fastapi.testclient import TestClient

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdCommandFailed
from fwupd_webui.fwupd.flash import FlashJob, FlashStatus
from fwupd_webui.fwupd.models import Device, Release
from fwupd_webui.fwupd.service import DeviceView, Inventory, MetadataStatus
from fwupd_webui.web.app import create_app


class FakeManager:
    def __init__(self, job=None):
        self.job = job

    @property
    def active(self):
        return self.job is not None and not self.job.finished


class FakeService:
    def __init__(self, *, inventory=None, job=None, error=None, enabled=False):
        self._inventory = inventory
        self._error = error
        self.flashing_enabled = enabled
        self.flash_manager = FakeManager(job)
        self.calls = 0

    async def cached_inventory(self, max_age_seconds=None):
        self.calls += 1
        if self._error:
            raise self._error
        return self._inventory

    async def inventory(self):
        return await self.cached_inventory()


def make_inventory(*, with_update=True, stale=False, meta_error=None) -> Inventory:
    nvme = Device.model_validate(
        {
            "DeviceId": "dev-1",
            "Name": "Samsung SSD 990 PRO",
            "Vendor": "Samsung",
            "Version": "4B2QJXD7",
            "Plugin": "nvme",
            "Flags": ["updatable", "needs-reboot"],
        }
    )
    hub = Device.model_validate(
        {"DeviceId": "dev-2", "Name": "USB Hub", "Version": "1.0", "Plugin": "usb"}
    )
    views = [
        DeviceView(
            device=nvme,
            available=(
                [Release.model_validate({"Version": "5B2QJXD7", "Urgency": "high"})]
                if with_update
                else []
            ),
        ),
        DeviceView(device=hub, available=[]),
    ]
    return Inventory(
        devices=views,
        metadata=MetadataStatus(
            last_refresh=1000.0, age_seconds=3600.0, stale=stale, error=meta_error
        ),
        fwupd_version="2.1.7",
        flashing_enabled=False,
    )


def client_for(service) -> TestClient:
    return TestClient(create_app(service, Config.from_env({})))


def test_status_is_json_and_ok():
    resp = client_for(FakeService(inventory=make_inventory())).get("/api/status")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["status"] == "ok"


def test_status_reports_counts():
    body = client_for(FakeService(inventory=make_inventory())).get("/api/status").json()
    assert body["devices"]["total"] == 2
    assert body["devices"]["updatable"] == 1
    assert body["devices"]["with_updates"] == 1


def test_status_lists_pending_updates_for_alerting():
    body = client_for(FakeService(inventory=make_inventory())).get("/api/status").json()
    assert len(body["updates"]) == 1
    update = body["updates"][0]
    assert update["device_id"] == "dev-1"
    assert update["name"] == "Samsung SSD 990 PRO"
    assert update["current_version"] == "4B2QJXD7"
    assert update["available_version"] == "5B2QJXD7"
    assert update["urgency"] == "high"
    assert update["needs_reboot"] is True


def test_no_updates_yields_an_empty_list_not_a_missing_key():
    """A monitoring check must be able to read updates[] unconditionally."""
    body = (
        client_for(FakeService(inventory=make_inventory(with_update=False)))
        .get("/api/status")
        .json()
    )
    assert body["updates"] == []
    assert body["devices"]["with_updates"] == 0


def test_stale_metadata_is_degraded_not_ok():
    body = client_for(FakeService(inventory=make_inventory(stale=True))).get("/api/status").json()
    assert body["status"] == "degraded"
    assert body["metadata"]["stale"] is True


def test_metadata_error_is_degraded_and_surfaced():
    inv = make_inventory(meta_error="network unreachable")
    body = client_for(FakeService(inventory=inv)).get("/api/status").json()
    assert body["status"] == "degraded"
    assert "network unreachable" in body["metadata"]["error"]


def test_fwupd_failure_is_error_with_503():
    """503 so a plain uptime check catches a broken fwupd, rather than a 200
    carrying a status field nobody configured an alert on."""
    service = FakeService(error=FwupdCommandFailed(1, "fwupdtool exploded"))
    resp = client_for(service).get("/api/status")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error"
    assert "exploded" in body["error"]


def test_status_reports_flashing_enabled():
    body = client_for(FakeService(inventory=make_inventory())).get("/api/status").json()
    assert body["flashing_enabled"] is False


def test_flash_is_null_when_no_job_has_run():
    body = client_for(FakeService(inventory=make_inventory())).get("/api/status").json()
    assert body["flash"] is None


def test_running_flash_is_reported_without_redirecting():
    """The HTML routes redirect to /flash during a job. The API must not: a
    monitoring tool needs JSON, and needs to see that a flash is in flight."""
    job = FlashJob(device_id="dev-1", device_name="Samsung SSD 990 PRO", version="5B2QJXD7")
    job.status = FlashStatus.RUNNING
    job.phase = "Writing"
    job.percent = 42.5

    resp = client_for(FakeService(inventory=make_inventory(), job=job)).get(
        "/api/status", follow_redirects=False
    )
    assert resp.status_code == 200
    flash = resp.json()["flash"]
    assert flash["active"] is True
    assert flash["status"] == "running"
    assert flash["phase"] == "Writing"
    assert flash["percent"] == 42.5


def test_failed_flash_is_reported_as_error_status():
    job = FlashJob(device_id="dev-1", device_name="Samsung SSD 990 PRO", version="5B2QJXD7")
    job.status = FlashStatus.FAILED
    job.phase = "Verifying"
    job.exit_code = 3
    job.error = "device is locked"

    body = client_for(FakeService(inventory=make_inventory(), job=job)).get("/api/status").json()
    assert body["status"] == "error"
    assert body["flash"]["status"] == "failed"
    assert body["flash"]["exit_code"] == 3
    assert "device is locked" in body["flash"]["error"]


def test_api_is_served_even_when_flashing_is_disabled():
    """Read-only reporting has nothing to do with the write path."""
    resp = client_for(FakeService(inventory=make_inventory(), enabled=False)).get("/api/status")
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ["/api/status"])
def test_api_never_returns_html(path):
    resp = client_for(FakeService(inventory=make_inventory())).get(path)
    assert "<html" not in resp.text.lower()
