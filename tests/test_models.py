import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fwupd_webui.fwupd.models import Device, Release, parse_devices

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parses_real_get_devices_fixture():
    devices = parse_devices(load("get-devices.json"))
    assert devices, "fixture should contain at least one device"
    for d in devices:
        assert d.device_id
        assert d.name


def test_real_fixture_device_without_version_parses():
    """The CPU device in the fixture reports no Version at all."""
    devices = {d.plugin: d for d in parse_devices(load("get-devices.json"))}
    assert devices["cpu"].version is None


def test_real_get_updates_fixture_carries_releases():
    devices = parse_devices(load("get-updates.json"))
    assert len(devices) == 1
    assert [r.version for r in devices[0].releases] == ["1.2.4", "1.2.3"]


def test_real_empty_updates_fixture_parses_to_nothing():
    assert parse_devices(load("get-updates-empty.json")) == []


def test_ignores_unknown_fields():
    device = Device.model_validate(
        {
            "DeviceId": "abc123",
            "Name": "Widget",
            "SomeFieldFwupdAddedIn2030": "surprise",
        }
    )
    assert device.device_id == "abc123"
    assert device.name == "Widget"


def test_missing_required_field_fails_loudly():
    with pytest.raises(ValidationError):
        Device.model_validate({"Name": "No device id here"})


def test_defaults_for_absent_optional_fields():
    device = Device.model_validate({"DeviceId": "abc", "Name": "Widget"})
    assert device.guids == []
    assert device.flags == []
    assert device.releases == []
    assert device.vendor is None


def test_updatable_property_reads_flags():
    yes = Device.model_validate({"DeviceId": "a", "Name": "X", "Flags": ["internal", "updatable"]})
    no = Device.model_validate({"DeviceId": "b", "Name": "Y", "Flags": ["internal"]})
    assert yes.updatable is True
    assert no.updatable is False


def test_release_parses_nested_under_device():
    device = Device.model_validate(
        {
            "DeviceId": "a",
            "Name": "X",
            "Releases": [{"Version": "1.2.3", "Urgency": "high", "Summary": "Fixes things"}],
        }
    )
    assert len(device.releases) == 1
    assert device.releases[0].version == "1.2.3"
    assert device.releases[0].urgency == "high"


def test_release_requires_version():
    with pytest.raises(ValidationError):
        Release.model_validate({"Summary": "no version"})


def test_missing_devices_key_raises():
    with pytest.raises(ValueError, match="Devices"):
        parse_devices({"SomethingElse": []})
