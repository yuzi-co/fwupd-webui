import json
import subprocess
from pathlib import Path

import pytest

from fwupd_webui.fwupd.cli import (
    FwupdCli,
    FwupdCommandFailed,
    FwupdNotFound,
    FwupdOutputInvalid,
    FwupdTimeout,
)

FIXTURES = Path(__file__).parent / "fixtures"


class FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def stub_run(monkeypatch, result: FakeCompleted, capture: dict | None = None):
    def fake_run(argv, **kwargs):
        if capture is not None:
            capture["argv"] = argv
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_get_devices_parses_output(monkeypatch):
    capture: dict = {}
    stub_run(monkeypatch, FakeCompleted(0, stdout=fixture("get-devices.json")), capture)
    devices = FwupdCli().get_devices()

    assert capture["argv"] == ["fwupdtool", "--json", "get-devices"]
    assert len(devices) == 2
    assert devices[0].device_id


def test_get_updates_parses_releases(monkeypatch):
    stub_run(monkeypatch, FakeCompleted(0, stdout=fixture("get-updates.json")))
    devices = FwupdCli().get_updates()
    assert [r.version for r in devices[0].releases] == ["1.2.4", "1.2.3"]


def test_get_updates_returns_empty_when_nothing_to_do(monkeypatch):
    """Real fwupd 2.0 exits 0 with an empty Devices array when everything is
    current -- it does not report an error. Fixture-verified."""
    stub_run(monkeypatch, FakeCompleted(0, stdout=fixture("get-updates-empty.json")))
    assert FwupdCli().get_updates() == []


def test_version_reads_the_runtime_fwupd_entry(monkeypatch):
    """The fixture lists several components with both runtime and compile
    entries; version() must pick the runtime org.freedesktop.fwupd one. The
    expectation is derived from the fixture so regenerating it against a new
    fwupd release does not break the test."""
    payload = json.loads(fixture("version.json"))
    expected = next(
        e["Version"]
        for e in payload["Versions"]
        if e["Type"] == "runtime" and e["AppstreamId"] == "org.freedesktop.fwupd"
    )
    assert len(payload["Versions"]) > 1, "fixture must exercise the selection, not a single entry"

    capture: dict = {}
    stub_run(monkeypatch, FakeCompleted(0, stdout=fixture("version.json")), capture)
    assert FwupdCli().version() == expected
    assert capture["argv"] == ["fwupdtool", "--json", "--version"]


def test_version_falls_back_when_unparseable(monkeypatch):
    stub_run(monkeypatch, FakeCompleted(0, stdout="not json"))
    assert FwupdCli().version() == "unknown"


def test_missing_binary_raises_fwupd_not_found(monkeypatch):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FwupdNotFound):
        FwupdCli().get_devices()


def test_nonzero_exit_raises_with_stderr(monkeypatch):
    stub_run(monkeypatch, FakeCompleted(1, stdout="", stderr="something went badly wrong\n"))
    with pytest.raises(FwupdCommandFailed) as exc:
        FwupdCli().get_devices()
    assert exc.value.exit_code == 1
    assert "something went badly wrong" in exc.value.stderr


def test_malformed_json_raises_output_invalid(monkeypatch):
    stub_run(monkeypatch, FakeCompleted(0, stdout="this is not json at all"))
    with pytest.raises(FwupdOutputInvalid) as exc:
        FwupdCli().get_devices()
    assert "not json" in exc.value.raw


def test_timeout_raises_fwupd_timeout(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FwupdTimeout):
        FwupdCli(timeout=1).get_devices()


def test_configured_timeout_is_passed_to_subprocess(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return FakeCompleted(0, stdout='{"Devices": []}')

    monkeypatch.setattr(subprocess, "run", fake_run)
    FwupdCli(timeout=7).get_devices()
    assert seen["timeout"] == 7


def test_refresh_succeeds_when_metadata_already_current(monkeypatch):
    """Real fwupd exits 2 with FwupdError code 9 ("Metadata is already up to
    date") when the cache is current. That is success, not failure -- treating
    it as an error meant the refresh stamp was never written."""
    stub_run(monkeypatch, FakeCompleted(2, stdout=fixture("refresh-already-current.json")))
    FwupdCli().refresh()  # must not raise


def test_refresh_still_raises_on_a_real_error(monkeypatch):
    payload = '{"Error": {"Domain": "FwupdError", "Code": 5, "Message": "read failed"}}'
    stub_run(monkeypatch, FakeCompleted(2, stdout=payload, stderr="read failed"))
    with pytest.raises(FwupdCommandFailed):
        FwupdCli().refresh()


def test_refresh_raises_when_failure_is_not_json(monkeypatch):
    stub_run(monkeypatch, FakeCompleted(2, stdout="", stderr="network unreachable"))
    with pytest.raises(FwupdCommandFailed):
        FwupdCli().refresh()


def test_refresh_passes_force(monkeypatch):
    capture: dict = {}
    stub_run(monkeypatch, FakeCompleted(0, stdout="{}"), capture)
    FwupdCli().refresh()
    assert capture["argv"] == ["fwupdtool", "--json", "refresh", "--force"]
