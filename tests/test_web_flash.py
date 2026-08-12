from fastapi.testclient import TestClient

from fwupd_webui.config import Config
from fwupd_webui.fwupd.flash import FlashJob, FlashStatus
from fwupd_webui.fwupd.models import Device
from fwupd_webui.fwupd.service import DeviceView, Inventory, MetadataStatus
from fwupd_webui.web.app import create_app


class FakeManager:
    def __init__(self, job=None):
        self.job = job

    @property
    def active(self):
        return self.job is not None and not self.job.finished

    def dismiss(self):
        self.job = None


class FakeService:
    def __init__(self, *, enabled=True, job=None):
        self.flashing_enabled = enabled
        self.flash_manager = FakeManager(job)
        self.started = []

    async def inventory(self):
        device = Device.model_validate(
            {"DeviceId": "dev-1", "Name": "NVMe SSD", "Plugin": "nvme", "Flags": ["updatable"]}
        )
        return Inventory(
            devices=[DeviceView(device=device, available=[])],
            metadata=MetadataStatus(last_refresh=1.0, age_seconds=60.0, stale=False),
            fwupd_version="2.1.7",
            flashing_enabled=self.flashing_enabled,
        )

    async def refresh(self):
        return (await self.inventory()).metadata

    async def start_flash(self, device_id, version, *, operation, typed_name):
        self.started.append((device_id, version, operation, typed_name))
        job = FlashJob(device_id=device_id, device_name="NVMe SSD", version=version)
        job.status = FlashStatus.SUCCEEDED
        self.flash_manager.job = job
        return job

    def permission_for(self, device):
        from fwupd_webui.fwupd.policy import evaluate

        return evaluate(device, enabled=self.flashing_enabled)


def client_for(service, *, enabled=True) -> TestClient:
    config = Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": "true" if enabled else "false"})
    return TestClient(create_app(service, config))


def running_job() -> FlashJob:
    job = FlashJob(device_id="dev-1", device_name="NVMe SSD", version="2.0")
    job.status = FlashStatus.RUNNING
    return job


def test_flash_routes_do_not_exist_when_disabled():
    """Not registered at all -- a real 404, not a handler returning 403. A
    capability that does not exist cannot be reached by a bug above it."""
    client = client_for(FakeService(enabled=False), enabled=False)
    assert client.post("/flash", data={"device_id": "d", "version": "1"}).status_code == 404
    assert client.get("/flash").status_code == 404
    assert client.get("/flash/progress").status_code == 404


def test_flash_routes_exist_when_enabled():
    client = client_for(FakeService())
    assert client.get("/flash", follow_redirects=False).status_code in (200, 303)


def test_post_flash_starts_a_job():
    service = FakeService()
    client = client_for(service)
    resp = client.post(
        "/flash",
        data={"device_id": "dev-1", "version": "2.0", "operation": "upgrade"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert service.started == [("dev-1", "2.0", "upgrade", None)]


def test_post_flash_passes_the_typed_override():
    service = FakeService()
    client_for(service).post(
        "/flash",
        data={
            "device_id": "dev-1",
            "version": "2.0",
            "operation": "upgrade",
            "confirm_name": "ST4000VN008-2DR166",
        },
        follow_redirects=False,
    )
    assert service.started[0][3] == "ST4000VN008-2DR166"


def test_refused_flash_renders_the_banner_not_a_500():
    class Refusing(FakeService):
        async def start_flash(self, *a, **k):
            raise PermissionError("type the device name exactly")

    resp = client_for(Refusing()).post(
        "/flash", data={"device_id": "dev-1", "version": "2.0", "operation": "upgrade"}
    )
    assert resp.status_code == 200
    assert "type the device name exactly" in resp.text


def test_index_redirects_to_progress_while_a_flash_runs():
    client = client_for(FakeService(job=running_job()))
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/flash"


def test_refresh_redirects_to_progress_while_a_flash_runs():
    client = client_for(FakeService(job=running_job()))
    resp = client.post("/refresh", follow_redirects=False)
    assert resp.status_code == 303


def test_progress_fragment_reports_phase_and_percent():
    job = running_job()
    job.phase = "Writing"
    job.percent = 42.5
    body = client_for(FakeService(job=job)).get("/flash/progress").text
    assert "Writing" in body
    assert "42.5" in body


def test_progress_fragment_polls_itself_while_running():
    body = client_for(FakeService(job=running_job())).get("/flash/progress").text
    assert "every 1s" in body


def test_progress_fragment_stops_polling_once_finished():
    done = FlashJob(device_id="dev-1", device_name="NVMe SSD", version="2.0")
    done.status = FlashStatus.SUCCEEDED
    body = client_for(FakeService(job=done)).get("/flash/progress").text
    assert "every 1s" not in body


def test_staged_firmware_is_called_out():
    done = FlashJob(device_id="dev-1", device_name="NVMe SSD", version="2.0")
    done.status = FlashStatus.SUCCEEDED
    done.staged = True
    done.installed_version = "1.0"
    body = client_for(FakeService(job=done)).get("/flash/progress").text
    assert "staged" in body.lower()
    assert "reboot" in body.lower()


def test_failed_job_shows_the_phase_it_reached():
    failed = FlashJob(device_id="dev-1", device_name="NVMe SSD", version="2.0")
    failed.status = FlashStatus.FAILED
    failed.phase = "Verifying"
    failed.error = "device is locked"
    body = client_for(FakeService(job=failed)).get("/flash/progress").text
    assert "Verifying" in body
    assert "device is locked" in body


def test_dismiss_clears_the_job():
    done = FlashJob(device_id="dev-1", device_name="NVMe SSD", version="2.0")
    done.status = FlashStatus.SUCCEEDED
    service = FakeService(job=done)
    client_for(service).post("/flash/dismiss", follow_redirects=False)
    assert service.flash_manager.job is None


def test_no_cancel_route_exists():
    """Deliberate omission. Killing fwupdtool mid-write can leave partially
    written firmware."""
    client = client_for(FakeService())
    assert client.post("/flash/cancel").status_code == 404
