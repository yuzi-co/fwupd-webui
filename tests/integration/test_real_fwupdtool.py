"""Runs against a real fwupdtool binary. Executed inside the container image.

Fixtures alone cannot catch a fwupd release that changes its JSON shape; only
invoking the real binary can.

Requires the image built with --build-arg WITH_TEST_DEVICES=true, which installs
the fwupd-tests package providing fwupd's synthetic devices.
"""

import json
import shutil
import subprocess

import pytest

from fwupd_webui.fwupd.cli import FwupdCli

pytestmark = pytest.mark.skipif(
    shutil.which("fwupdtool") is None, reason="requires a real fwupdtool binary"
)


@pytest.fixture(scope="module", autouse=True)
def enable_test_devices():
    subprocess.run(["fwupdtool", "enable-test-devices"], check=False, capture_output=True)
    yield
    subprocess.run(["fwupdtool", "disable-test-devices"], check=False, capture_output=True)


def test_version_is_reported():
    version = FwupdCli().version()
    assert version != "unknown"
    assert version[0].isdigit()


def test_get_devices_returns_parsed_devices():
    devices = FwupdCli().get_devices()
    assert devices, "synthetic test devices should be present"
    for d in devices:
        assert d.device_id
        assert d.name


def test_synthetic_test_device_is_present():
    plugins = {d.plugin for d in FwupdCli().get_devices()}
    assert "test" in plugins, f"fwupd-tests package missing? plugins seen: {plugins}"


def test_get_updates_returns_a_list_with_releases():
    devices = FwupdCli().get_updates()
    assert isinstance(devices, list)
    assert devices, "the synthetic test device should have updates available"
    assert devices[0].releases


def test_capsule_plugin_is_disabled():
    """Read-only enforcement: the plugin that can write to the ESP must not load."""
    proc = subprocess.run(
        ["fwupdtool", "--json", "get-plugins"], capture_output=True, text=True, check=False
    )
    plugins = {p["Name"]: p.get("Flags", []) for p in json.loads(proc.stdout)["Plugins"]}
    assert "disabled" in plugins["uefi_capsule"]
