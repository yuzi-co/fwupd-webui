from fastapi.testclient import TestClient

from fwupd_webui.config import Config
from fwupd_webui.fwupd.models import Device, Release
from fwupd_webui.fwupd.service import DeviceView, Inventory, MetadataStatus
from fwupd_webui.web.app import create_app
from test_web_flash import FakeService


def service_with(plugin="logitech_hidpp", name="Unifying Receiver", enabled=True):
    class S(FakeService):
        async def inventory(self):
            device = Device.model_validate(
                {
                    "DeviceId": "dev-1",
                    "Name": name,
                    "Plugin": plugin,
                    "Version": "1.0",
                    "Flags": ["updatable", "needs-reboot"],
                }
            )
            return Inventory(
                devices=[
                    DeviceView(
                        device=device,
                        available=[Release.model_validate({"Version": "2.0"})],
                    )
                ],
                metadata=MetadataStatus(last_refresh=1.0, age_seconds=60.0, stale=False),
                fwupd_version="2.1.7",
                flashing_enabled=enabled,
            )

    return S(enabled=enabled)


def client_for(service, enabled=True) -> TestClient:
    config = Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": "true" if enabled else "false"})
    return TestClient(create_app(service, config))


def confirm(client, operation="upgrade"):
    return client.get(f"/devices/dev-1/confirm?version=2.0&operation={operation}").text


def test_allowlisted_device_gets_a_live_confirm_button():
    body = confirm(client_for(service_with(plugin="logitech_hidpp")))
    assert 'name="confirm_name"' not in body, "an allowlisted device needs no typed override"
    assert "2.0" in body


def test_blocked_device_requires_typing_the_name():
    body = confirm(client_for(service_with(plugin="ata", name="ST4000VN008-2DR166")))
    assert 'name="confirm_name"' in body
    assert "ST4000VN008-2DR166" in body


def test_confirm_shows_the_version_transition():
    body = confirm(client_for(service_with()))
    assert "1.0" in body
    assert "2.0" in body


def test_confirm_warns_about_needs_reboot():
    body = confirm(client_for(service_with()))
    assert "needs-reboot" in body
    assert "reboot" in body.lower()


def test_confirm_carries_the_operation_through():
    body = confirm(client_for(service_with()), operation="downgrade")
    assert 'value="downgrade"' in body


def test_device_detail_offers_flash_controls_when_enabled():
    body = client_for(service_with()).get("/devices/dev-1").text
    assert "/devices/dev-1/confirm" in body


def test_device_detail_explains_when_flashing_is_disabled():
    body = client_for(service_with(enabled=False), enabled=False).get("/devices/dev-1").text
    assert "FWUPD_WEBUI_ENABLE_FLASHING" in body


def test_device_detail_offers_no_flash_controls_when_disabled():
    body = client_for(service_with(enabled=False), enabled=False).get("/devices/dev-1").text
    assert "/devices/dev-1/confirm" not in body


def test_confirm_route_404s_for_an_unknown_device():
    resp = client_for(service_with()).get("/devices/nope/confirm?version=2.0")
    assert resp.status_code == 404


def test_storage_device_gets_the_big_warning():
    body = confirm(client_for(service_with(plugin="nvme")))
    assert 'class="danger"' in body
    assert "storage device" in body.lower()
    assert 'name="confirm_name"' in body, "storage always needs the typed name"


def test_peripheral_gets_no_storage_warning():
    body = confirm(client_for(service_with(plugin="logitech_hidpp")))
    assert 'class="danger"' not in body
    assert 'name="confirm_name"' not in body
