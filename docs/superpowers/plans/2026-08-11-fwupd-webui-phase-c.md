# fwupd Web UI — Phase C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Docker container that serves a read-only web UI listing every firmware device `fwupd` can see on an Unraid host, annotated with available LVFS updates.

**Architecture:** One container, one Python process. FastAPI serves Jinja2 templates with htmx for row expansion and refresh. There is no fwupd daemon, no DBus, and no systemd — the backend shells out to `fwupdtool --json`, which runs the fwupd engine in-process. Layering runs strictly one direction: `cli.py` (subprocess) → `models.py` (parsing) → `service.py` (orchestration) → `routes.py` (HTTP).

**Tech Stack:** Python 3.13, FastAPI, uvicorn, Jinja2, pydantic v2, htmx (vendored), pytest, ruff, uv, Docker on `debian:trixie-slim`.

**Spec:** `docs/superpowers/specs/2026-08-11-fwupd-webui-design.md`

## Global Constraints

- **amd64 only.** No multi-arch builds. Unraid is x86_64.
- **No fwupd daemon, no DBus, no systemd** in the image. Every fwupd interaction goes through `fwupdtool`.
- **Process runs as root.** Device enumeration requires it. Do not add a `USER` directive.
- **Read-only is structural.** `/etc/fwupd/fwupd.conf` sets `DisabledPlugins=uefi_capsule`, and no code path may construct an `install` argv. Phase C has no write verbs.
- **All environment variables are prefixed `FWUPD_WEBUI_`.**
- **No external network requests at runtime from the browser.** htmx is vendored into the repo and served locally; no CDN links in templates.
- **Package root is `src/fwupd_webui/`.** Import path is `fwupd_webui`.
- **`fwupdtool` invocations are blocking and mutually exclusive.** Every call runs in a threadpool, serialized behind a single lock.
- **Pydantic models ignore unknown fields but require expected ones.** A fwupd version adding keys must not break parsing; a fwupd version removing `DeviceId` must fail loudly.

---

### Task 1: Project scaffolding and configuration

**Files:**
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `.gitignore`
- Create: `src/fwupd_webui/__init__.py`
- Create: `src/fwupd_webui/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `fwupd_webui.config.Config` — a frozen dataclass with fields `port: int`, `refresh_interval_hours: int`, `timeout_seconds: int`, `lvfs_remote: str`, `log_level: str`, and classmethod `Config.from_env(env: Mapping[str, str] | None = None) -> Config`.

- [ ] **Step 1: Create the project skeleton**

Create `pyproject.toml`:

```toml
[project]
name = "fwupd-webui"
version = "0.1.0"
description = "Read-only web UI for fwupd firmware inventory"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "jinja2>=3.1",
    "pydantic>=2.10",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.25",
    "httpx>=0.28",
    "ruff>=0.9",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/fwupd_webui"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

Create `.gitignore`:

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
dist/
*.egg-info/
```

Create `Makefile`:

```makefile
.PHONY: install lint test fixtures image integration

install:
	uv sync --extra dev

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

test:
	uv run pytest -v

image:
	docker build -t fwupd-webui:dev .
```

Create empty `src/fwupd_webui/__init__.py`.

- [ ] **Step 2: Write the failing test**

Create `tests/test_config.py`:

```python
import pytest

from fwupd_webui.config import Config


def test_defaults_when_env_empty():
    cfg = Config.from_env({})
    assert cfg.port == 8080
    assert cfg.refresh_interval_hours == 24
    assert cfg.timeout_seconds == 120
    assert cfg.lvfs_remote == "lvfs"
    assert cfg.log_level == "info"


def test_env_overrides_defaults():
    cfg = Config.from_env(
        {
            "FWUPD_WEBUI_PORT": "9000",
            "FWUPD_WEBUI_REFRESH_INTERVAL_HOURS": "6",
            "FWUPD_WEBUI_TIMEOUT_SECONDS": "30",
            "FWUPD_WEBUI_LVFS_REMOTE": "lvfs-testing",
            "FWUPD_WEBUI_LOG_LEVEL": "debug",
        }
    )
    assert cfg.port == 9000
    assert cfg.refresh_interval_hours == 6
    assert cfg.timeout_seconds == 30
    assert cfg.lvfs_remote == "lvfs-testing"
    assert cfg.log_level == "debug"


def test_unknown_remote_rejected():
    with pytest.raises(ValueError, match="FWUPD_WEBUI_LVFS_REMOTE"):
        Config.from_env({"FWUPD_WEBUI_LVFS_REMOTE": "lvfs-embargo-acme"})


def test_non_integer_port_rejected():
    with pytest.raises(ValueError, match="FWUPD_WEBUI_PORT"):
        Config.from_env({"FWUPD_WEBUI_PORT": "not-a-number"})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fwupd_webui.config'`

- [ ] **Step 4: Write the implementation**

Create `src/fwupd_webui/config.py`:

```python
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

VALID_REMOTES = frozenset({"lvfs", "lvfs-testing"})


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    port: int = 8080
    refresh_interval_hours: int = 24
    timeout_seconds: int = 120
    lvfs_remote: str = "lvfs"
    log_level: str = "info"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        e = os.environ if env is None else env
        remote = e.get("FWUPD_WEBUI_LVFS_REMOTE", "lvfs")
        if remote not in VALID_REMOTES:
            raise ValueError(
                f"FWUPD_WEBUI_LVFS_REMOTE must be one of {sorted(VALID_REMOTES)}, got {remote!r}"
            )
        return cls(
            port=_int_env(e, "FWUPD_WEBUI_PORT", 8080),
            refresh_interval_hours=_int_env(e, "FWUPD_WEBUI_REFRESH_INTERVAL_HOURS", 24),
            timeout_seconds=_int_env(e, "FWUPD_WEBUI_TIMEOUT_SECONDS", 120),
            lvfs_remote=remote,
            log_level=e.get("FWUPD_WEBUI_LOG_LEVEL", "info"),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS, 4 tests

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml Makefile .gitignore src/fwupd_webui tests/test_config.py
git commit -m "feat: project scaffolding and env configuration"
```

---

### Task 2: Container base image and real fwupd fixtures

This task produces the image that later tasks test against, and — critically — captures **real** `fwupdtool` JSON output into committed fixtures. Every parsing decision in Task 3 is made against real data rather than guessed schema.

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker/fwupd.conf`
- Create: `scripts/capture-fixtures.sh`
- Create: `tests/fixtures/get-devices.json` (generated)
- Create: `tests/fixtures/get-updates.json` (generated)
- Create: `tests/fixtures/get-updates-empty.json` (generated)
- Modify: `Makefile`

**Interfaces:**
- Consumes: nothing.
- Produces: a `fwupd-webui:dev` image containing `fwupdtool`; three fixture files whose exact JSON shape Task 3 parses.

- [ ] **Step 1: Write the fwupd config that disables capsule updates**

Create `docker/fwupd.conf`:

```ini
# Baked configuration. Phase C is read-only; the uefi_capsule plugin is the one
# that can stage a firmware capsule to the ESP, which on Unraid is the bootable
# USB stick holding the OS and array config. It is disabled at the engine level
# so read-only is a structural property, not a convention.
[fwupd]
DisabledPlugins=uefi_capsule
UpdateMotd=false
```

- [ ] **Step 2: Write the Dockerfile**

Create `Dockerfile`:

```dockerfile
FROM debian:trixie-slim

# uv provides dependency resolution; pinned rather than :latest so builds are reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /bin/uv

ENV DEBIAN_FRONTEND=noninteractive \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# fwupd brings fwupdtool. The daemon and its systemd units come along in the same
# package but are never started -- this image has no init system.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fwupd \
        ca-certificates \
        python3 \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY docker/fwupd.conf /etc/fwupd/fwupd.conf

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN uv venv /app/.venv && uv pip install --python /app/.venv/bin/python .

ENV PATH="/app/.venv/bin:${PATH}"

# Record the fwupd version this image was built against, so drift is diagnosable
# from `docker inspect` without starting the container.
RUN fwupdtool --version --json > /app/fwupd-version.json || \
    fwupdtool --version > /app/fwupd-version.txt

EXPOSE 8080

# Runs as root deliberately: device enumeration requires it.
CMD ["python", "-m", "fwupd_webui"]
```

Create `.dockerignore`:

```
.git
.venv
__pycache__
.pytest_cache
.ruff_cache
tests
docs
*.egg-info
```

- [ ] **Step 3: Build the image**

Run: `docker build -t fwupd-webui:dev .`
Expected: build succeeds. It will fail at the `CMD` module only at runtime, not build time — `fwupd_webui/__main__.py` does not exist yet and that is fine.

If `uv pip install` fails because `src/fwupd_webui/__main__.py` is referenced nowhere, that is expected; the package installs from `pyproject.toml` alone.

- [ ] **Step 4: Verify fwupdtool runs and supports the expected verbs**

Run:

```bash
docker run --rm fwupd-webui:dev fwupdtool --version
docker run --rm fwupd-webui:dev fwupdtool --help | grep -E 'get-devices|get-updates|refresh'
```

Expected: a version string, and three matching help lines. If `get-updates` is absent, stop and report — the whole daemonless design depends on it.

- [ ] **Step 5: Write the fixture capture script**

Create `scripts/capture-fixtures.sh`:

```bash
#!/usr/bin/env bash
# Capture real fwupdtool JSON into tests/fixtures/.
#
# Uses fwupd's built-in synthetic test devices so this works on any machine,
# including CI and developer laptops with no interesting firmware.
#
# Run:  ./scripts/capture-fixtures.sh
set -euo pipefail

IMAGE="${IMAGE:-fwupd-webui:dev}"
OUT="tests/fixtures"
mkdir -p "$OUT"

run() {
    docker run --rm "$IMAGE" bash -c "$1"
}

echo "==> get-devices (with synthetic test devices)"
run 'fwupdtool enable-test-devices >/dev/null 2>&1; fwupdtool --json get-devices' \
    > "$OUT/get-devices.json"

echo "==> get-updates (with synthetic test devices)"
run 'fwupdtool enable-test-devices >/dev/null 2>&1; fwupdtool --json get-updates' \
    > "$OUT/get-updates.json" || true

echo "==> get-updates with no updatable devices (error shape)"
run 'fwupdtool --json get-updates' > "$OUT/get-updates-empty.json" || true

echo
echo "Captured:"
wc -c "$OUT"/*.json
echo
echo "Top-level keys:"
for f in "$OUT"/*.json; do
    echo "  $f: $(python3 -c 'import json,sys; print(sorted(json.load(open(sys.argv[1])).keys()))' "$f" 2>/dev/null || echo '<not valid json>')"
done
```

Make it executable: `chmod +x scripts/capture-fixtures.sh`

- [ ] **Step 6: Capture the fixtures and inspect them**

Run: `./scripts/capture-fixtures.sh`

Then read all three files. **Record these facts, because Task 3 and Task 4 depend on them:**

1. The top-level key holding the device array in `get-devices.json` (expected: `Devices`).
2. The exact field names on a device object (expected to include `DeviceId`, `Name`, `Plugin`, `Version`, `Guid`, `Flags`).
3. Whether `get-updates` returns a `Devices` array with a `Releases` key per device.
4. **The exact shape of `get-updates-empty.json`** — when nothing is updatable, fwupd exits non-zero and emits an error object. Note its structure (expected: `{"Error": {"Code": <int>, "Message": "..."}}`) and the numeric `Code`. Task 4 keys its "no updates is not a failure" handling off this value.

If any expected field name differs from the above, use the real name in Task 3 and note the discrepancy in the commit message.

- [ ] **Step 7: Add the fixtures target to the Makefile**

Add to `Makefile`:

```makefile
fixtures: image
	./scripts/capture-fixtures.sh
```

- [ ] **Step 8: Commit**

```bash
git add Dockerfile .dockerignore docker/fwupd.conf scripts/capture-fixtures.sh tests/fixtures Makefile
git commit -m "feat: container base image and captured fwupdtool fixtures"
```

---

### Task 3: fwupd data models

**Files:**
- Create: `src/fwupd_webui/fwupd/__init__.py`
- Create: `src/fwupd_webui/fwupd/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: fixture files from Task 2.
- Produces:
  - `Release` — pydantic model, fields `version: str`, `appstream_id: str | None`, `remote_id: str | None`, `summary: str | None`, `description: str | None`, `urgency: str | None`, `created: int | None`, `uri: str | None`, `size: int | None`, `vendor: str | None`, `flags: list[str]`.
  - `Device` — pydantic model, fields `device_id: str`, `name: str`, `vendor: str | None`, `version: str | None`, `plugin: str | None`, `protocol: str | None`, `summary: str | None`, `serial: str | None`, `parent_device_id: str | None`, `guids: list[str]`, `flags: list[str]`, `releases: list[Release]`; property `updatable: bool`.
  - `parse_devices(payload: dict) -> list[Device]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`. Note the `_FIXTURES` path helper — later tasks reuse the same pattern.

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fwupd_webui.fwupd'`

- [ ] **Step 3: Write the implementation**

Create empty `src/fwupd_webui/fwupd/__init__.py`.

Create `src/fwupd_webui/fwupd/models.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# fwupd's JSON is not a stability-guaranteed API. `extra="ignore"` means a fwupd
# release that adds keys cannot break us; leaving DeviceId/Name/Version required
# means a release that *removes* them fails loudly instead of silently producing
# empty rows in the UI.
_MODEL_CONFIG = ConfigDict(extra="ignore", populate_by_name=True)


class Release(BaseModel):
    model_config = _MODEL_CONFIG

    version: str = Field(alias="Version")
    appstream_id: str | None = Field(default=None, alias="AppstreamId")
    remote_id: str | None = Field(default=None, alias="RemoteId")
    summary: str | None = Field(default=None, alias="Summary")
    description: str | None = Field(default=None, alias="Description")
    urgency: str | None = Field(default=None, alias="Urgency")
    created: int | None = Field(default=None, alias="Created")
    uri: str | None = Field(default=None, alias="Uri")
    size: int | None = Field(default=None, alias="Size")
    vendor: str | None = Field(default=None, alias="Vendor")
    flags: list[str] = Field(default_factory=list, alias="Flags")


class Device(BaseModel):
    model_config = _MODEL_CONFIG

    device_id: str = Field(alias="DeviceId")
    name: str = Field(alias="Name")
    vendor: str | None = Field(default=None, alias="Vendor")
    version: str | None = Field(default=None, alias="Version")
    plugin: str | None = Field(default=None, alias="Plugin")
    protocol: str | None = Field(default=None, alias="Protocol")
    summary: str | None = Field(default=None, alias="Summary")
    serial: str | None = Field(default=None, alias="Serial")
    parent_device_id: str | None = Field(default=None, alias="ParentDeviceId")
    guids: list[str] = Field(default_factory=list, alias="Guid")
    flags: list[str] = Field(default_factory=list, alias="Flags")
    releases: list[Release] = Field(default_factory=list, alias="Releases")

    @property
    def updatable(self) -> bool:
        return "updatable" in self.flags


def parse_devices(payload: dict) -> list[Device]:
    """Parse a `fwupdtool --json get-devices` / `get-updates` payload."""
    if "Devices" not in payload:
        raise ValueError(f"payload has no 'Devices' key; got keys {sorted(payload)}")
    return [Device.model_validate(d) for d in payload["Devices"]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS, 8 tests

If `test_parses_real_get_devices_fixture` fails, the fixture's real field names differ from the aliases above. Fix the aliases to match the fixture — the fixture is the source of truth.

- [ ] **Step 5: Commit**

```bash
git add src/fwupd_webui/fwupd tests/test_models.py
git commit -m "feat: fwupd device and release models"
```

---

### Task 4: fwupdtool subprocess client

The only module in the codebase that imports `subprocess`.

**Files:**
- Create: `src/fwupd_webui/fwupd/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Device`, `parse_devices` from Task 3.
- Produces:
  - Exceptions `FwupdError` (base), `FwupdNotFound`, `FwupdCommandFailed` (attrs `exit_code: int`, `stderr: str`), `FwupdTimeout`, `FwupdOutputInvalid` (attr `raw: str`).
  - `FwupdCli(binary: str = "fwupdtool", timeout: int = 120)` with **blocking** methods `get_devices() -> list[Device]`, `get_updates() -> list[Device]`, `refresh() -> None`, `version() -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
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


@pytest.fixture
def devices_json() -> str:
    return (FIXTURES / "get-devices.json").read_text()


def test_get_devices_parses_output(monkeypatch, devices_json):
    def fake_run(argv, **kwargs):
        assert argv[0] == "fwupdtool"
        assert "--json" in argv
        assert "get-devices" in argv
        return FakeCompleted(0, stdout=devices_json)

    monkeypatch.setattr(subprocess, "run", fake_run)
    devices = FwupdCli().get_devices()
    assert devices
    assert devices[0].device_id


def test_missing_binary_raises_fwupd_not_found(monkeypatch):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FwupdNotFound):
        FwupdCli().get_devices()


def test_nonzero_exit_raises_with_stderr(monkeypatch):
    def fake_run(argv, **kwargs):
        return FakeCompleted(1, stdout="", stderr="something went badly wrong\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FwupdCommandFailed) as exc:
        FwupdCli().get_devices()
    assert exc.value.exit_code == 1
    assert "something went badly wrong" in exc.value.stderr


def test_malformed_json_raises_output_invalid(monkeypatch):
    def fake_run(argv, **kwargs):
        return FakeCompleted(0, stdout="this is not json at all")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FwupdOutputInvalid) as exc:
        FwupdCli().get_devices()
    assert "not json" in exc.value.raw


def test_timeout_raises_fwupd_timeout(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FwupdTimeout):
        FwupdCli(timeout=1).get_devices()


def test_get_updates_returns_empty_when_nothing_to_do(monkeypatch):
    """fwupd exits non-zero with a NOTHING_TO_DO error when no device has an
    update. That is a normal state for a healthy machine, not a failure."""
    payload = json.dumps({"Error": {"Code": 9, "Message": "No updatable devices"}})

    def fake_run(argv, **kwargs):
        return FakeCompleted(2, stdout=payload, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert FwupdCli().get_updates() == []


def test_get_updates_returns_empty_when_not_found(monkeypatch):
    payload = json.dumps({"Error": {"Code": 8, "Message": "No devices found"}})

    def fake_run(argv, **kwargs):
        return FakeCompleted(2, stdout=payload, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert FwupdCli().get_updates() == []


def test_get_updates_still_raises_on_real_error(monkeypatch):
    payload = json.dumps({"Error": {"Code": 0, "Message": "internal engine failure"}})

    def fake_run(argv, **kwargs):
        return FakeCompleted(2, stdout=payload, stderr="internal engine failure")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FwupdCommandFailed):
        FwupdCli().get_updates()


def test_refresh_passes_force(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return FakeCompleted(0, stdout="{}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    FwupdCli().refresh()
    assert "refresh" in seen["argv"]
    assert "--force" in seen["argv"]


def test_no_install_verb_exists():
    """Phase C is read-only. Guard against an install path being added by accident."""
    import inspect

    import fwupd_webui.fwupd.cli as cli_module

    text = Path(inspect.getsourcefile(cli_module)).read_text()
    assert '"install"' not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fwupd_webui.fwupd.cli'`

- [ ] **Step 3: Write the implementation**

Create `src/fwupd_webui/fwupd/cli.py`:

```python
from __future__ import annotations

import json
import logging
import subprocess

from fwupd_webui.fwupd.models import Device, parse_devices

log = logging.getLogger(__name__)

# fwupd's FwupdError enum. NOT_FOUND and NOTHING_TO_DO are reported as command
# failures by `get-updates` on a machine where everything is current -- which is
# the normal, healthy state, not an error worth surfacing to the user.
FWUPD_ERROR_NOT_FOUND = 8
FWUPD_ERROR_NOTHING_TO_DO = 9
_BENIGN_UPDATE_ERRORS = frozenset({FWUPD_ERROR_NOT_FOUND, FWUPD_ERROR_NOTHING_TO_DO})

_STDERR_TAIL_CHARS = 2000
_RAW_SNIPPET_CHARS = 500


class FwupdError(Exception):
    """Base for every failure originating from the fwupdtool subprocess."""


class FwupdNotFound(FwupdError):
    """The fwupdtool binary is not on PATH."""


class FwupdTimeout(FwupdError):
    """fwupdtool exceeded the configured timeout."""


class FwupdCommandFailed(FwupdError):
    def __init__(self, exit_code: int, stderr: str):
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(f"fwupdtool exited {exit_code}: {stderr.strip() or '<no stderr>'}")


class FwupdOutputInvalid(FwupdError):
    def __init__(self, raw: str):
        self.raw = raw
        super().__init__(f"fwupdtool produced unparseable output: {raw[:_RAW_SNIPPET_CHARS]!r}")


class FwupdCli:
    """Blocking wrapper around the fwupdtool binary.

    Every method here blocks for seconds -- enumeration touches real hardware.
    Callers must run these in a threadpool and must serialize concurrent calls;
    FwupdService owns both responsibilities.
    """

    def __init__(self, binary: str = "fwupdtool", timeout: int = 120):
        self._binary = binary
        self._timeout = timeout

    def _run(self, *args: str) -> tuple[int, str, str]:
        argv = [self._binary, "--json", *args]
        log.debug("running %s", argv)
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise FwupdNotFound(f"{self._binary} not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise FwupdTimeout(f"{self._binary} timed out after {self._timeout}s") from exc
        return proc.returncode, proc.stdout, proc.stderr[-_STDERR_TAIL_CHARS:]

    @staticmethod
    def _decode(stdout: str) -> dict:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise FwupdOutputInvalid(stdout) from exc
        if not isinstance(payload, dict):
            raise FwupdOutputInvalid(stdout)
        return payload

    def _run_json(self, *args: str) -> dict:
        code, stdout, stderr = self._run(*args)
        if code != 0:
            raise FwupdCommandFailed(code, stderr)
        return self._decode(stdout)

    def get_devices(self) -> list[Device]:
        return parse_devices(self._run_json("get-devices"))

    def get_updates(self) -> list[Device]:
        code, stdout, stderr = self._run("get-updates")
        if code != 0:
            error_code = self._benign_error_code(stdout)
            if error_code is not None:
                log.info("get-updates reported nothing to do (fwupd code %s)", error_code)
                return []
            raise FwupdCommandFailed(code, stderr)
        return parse_devices(self._decode(stdout))

    @staticmethod
    def _benign_error_code(stdout: str) -> int | None:
        """Return the fwupd error code if stdout is a benign 'no updates' error."""
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        error = payload.get("Error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return None
        code = error.get("Code")
        return code if code in _BENIGN_UPDATE_ERRORS else None

    def refresh(self) -> None:
        # --force skips fwupd's own "metadata is recent enough" short-circuit;
        # the refresh cadence is our policy decision, made in FwupdService.
        self._run_json("refresh", "--force")

    def version(self) -> str:
        code, stdout, _ = self._run("--version")
        if code != 0:
            return "unknown"
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout.strip() or "unknown"
        runtime = payload.get("Runtime", [])
        for entry in runtime:
            if entry.get("Id") == "org.freedesktop.fwupd":
                return str(entry.get("Version", "unknown"))
        return "unknown"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, 10 tests

If `test_get_updates_returns_empty_when_nothing_to_do` disagrees with the real `tests/fixtures/get-updates-empty.json` captured in Task 2, change `_BENIGN_UPDATE_ERRORS` and the test payload to the real code and note it in the commit message.

- [ ] **Step 5: Commit**

```bash
git add src/fwupd_webui/fwupd/cli.py tests/test_cli.py
git commit -m "feat: fwupdtool subprocess client with typed errors"
```

---

### Task 5: Service layer — joining, refresh policy, concurrency

**Files:**
- Create: `src/fwupd_webui/fwupd/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `FwupdCli`, `Device`, `Release`, `Config`.
- Produces:
  - `DeviceView` dataclass — `device: Device`, `available: list[Release]`, property `has_update: bool`.
  - `MetadataStatus` dataclass — `last_refresh: float | None`, `age_seconds: float | None`, `stale: bool`, `error: str | None`.
  - `Inventory` dataclass — `devices: list[DeviceView]`, `metadata: MetadataStatus`, `fwupd_version: str`.
  - `FwupdService(cli, config, state_dir: Path, clock: Callable[[], float] = time.time)` with async methods `inventory() -> Inventory`, `refresh() -> MetadataStatus`, `refresh_if_stale() -> MetadataStatus`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_service.py`:

```python
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
    def __init__(self, devices=None, updates=None, version="2.0.0"):
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


async def test_devices_with_updates_sort_first(tmp_path, config):
    cli = FakeCli(
        devices=[make_device("a", "Alpha"), make_device("z", "Zulu")],
        updates=[make_device("z", "Zulu", releases=[{"Version": "9.0"}])],
    )
    service = FwupdService(cli, config, tmp_path)
    inv = await service.inventory()
    assert [v.device.name for v in inv.devices] == ["Zulu", "Alpha"]


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
    now = 100_000.0
    service = FwupdService(cli, config, tmp_path, clock=lambda: now)
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


async def test_metadata_status_reports_age(tmp_path, config):
    cli = FakeCli()
    now = [100_000.0]
    service = FwupdService(cli, config, tmp_path, clock=lambda: now[0])
    await service.refresh()
    now[0] += 3600
    inv = await service.inventory()
    assert inv.metadata.age_seconds == pytest.approx(3600, abs=1)
    assert inv.metadata.stale is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fwupd_webui.fwupd.service'`

- [ ] **Step 3: Write the implementation**

Create `src/fwupd_webui/fwupd/service.py`:

```python
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdCli, FwupdError
from fwupd_webui.fwupd.models import Device, Release

log = logging.getLogger(__name__)

STAMP_FILENAME = ".webui-last-refresh"


@dataclass
class DeviceView:
    device: Device
    available: list[Release] = field(default_factory=list)

    @property
    def has_update(self) -> bool:
        return bool(self.available)


@dataclass
class MetadataStatus:
    last_refresh: float | None = None
    age_seconds: float | None = None
    stale: bool = True
    error: str | None = None


@dataclass
class Inventory:
    devices: list[DeviceView]
    metadata: MetadataStatus
    fwupd_version: str


class FwupdService:
    """Orchestrates fwupdtool calls and owns the two rules that keep it safe.

    1. fwupdtool blocks for seconds; every call goes through asyncio.to_thread.
    2. Concurrent fwupdtool invocations against the same hardware are unsafe; a
       single lock serializes them, so overlapping requests wait rather than
       spawning a second enumeration.
    """

    def __init__(
        self,
        cli: FwupdCli,
        config: Config,
        state_dir: Path,
        clock: Callable[[], float] = time.time,
    ):
        self._cli = cli
        self._config = config
        self._state_dir = Path(state_dir)
        self._clock = clock
        self._lock = asyncio.Lock()

    async def _call(self, fn, *args):
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    @property
    def _stamp_path(self) -> Path:
        return self._state_dir / STAMP_FILENAME

    def _read_stamp(self) -> float | None:
        try:
            return float(self._stamp_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _write_stamp(self, when: float) -> None:
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._stamp_path.write_text(str(when))
        except OSError:
            log.warning("could not write refresh stamp to %s", self._stamp_path, exc_info=True)

    def _status(self, error: str | None = None) -> MetadataStatus:
        last = self._read_stamp()
        if last is None:
            return MetadataStatus(None, None, stale=True, error=error)
        age = self._clock() - last
        stale = age > self._config.refresh_interval_hours * 3600
        return MetadataStatus(last, age, stale=stale, error=error)

    async def inventory(self) -> Inventory:
        devices = await self._call(self._cli.get_devices)
        version = await self._call(self._cli.version)

        update_error: str | None = None
        try:
            updated = await self._call(self._cli.get_updates)
        except FwupdError as exc:
            # An inventory without update information is still worth showing.
            log.warning("get-updates failed: %s", exc)
            updated = []
            update_error = str(exc)

        releases_by_id = {d.device_id: d.releases for d in updated}
        views = [
            DeviceView(device=d, available=list(releases_by_id.get(d.device_id, [])))
            for d in devices
        ]
        views.sort(key=lambda v: (not v.has_update, v.device.name.lower()))

        return Inventory(
            devices=views,
            metadata=self._status(error=update_error),
            fwupd_version=version,
        )

    async def refresh(self) -> MetadataStatus:
        try:
            await self._call(self._cli.refresh)
        except FwupdError as exc:
            log.warning("metadata refresh failed: %s", exc)
            return self._status(error=str(exc))
        self._write_stamp(self._clock())
        return self._status()

    async def refresh_if_stale(self) -> MetadataStatus:
        status = self._status()
        if not status.stale:
            return status
        return await self.refresh()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_service.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add src/fwupd_webui/fwupd/service.py tests/test_service.py
git commit -m "feat: service layer joining devices with updates"
```

---

### Task 6: Web application and device table

**Files:**
- Create: `src/fwupd_webui/web/__init__.py`
- Create: `src/fwupd_webui/web/app.py`
- Create: `src/fwupd_webui/web/routes.py`
- Create: `src/fwupd_webui/web/templates/base.html`
- Create: `src/fwupd_webui/web/templates/index.html`
- Create: `src/fwupd_webui/web/templates/_device_table.html`
- Create: `src/fwupd_webui/web/static/app.css`
- Create: `src/fwupd_webui/web/static/htmx.min.js` (vendored)
- Test: `tests/test_web_table.py`

**Interfaces:**
- Consumes: `FwupdService`, `Inventory`, `DeviceView`, `Config`.
- Produces: `create_app(service: FwupdService, config: Config) -> FastAPI`, with routes `GET /` and `POST /refresh`. The app exposes the service as `app.state.service`.

- [ ] **Step 1: Vendor htmx**

Run:

```bash
mkdir -p src/fwupd_webui/web/static
curl -sL https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js \
  -o src/fwupd_webui/web/static/htmx.min.js
ls -l src/fwupd_webui/web/static/htmx.min.js
```

Expected: a file of roughly 50 KB. Record the exact version fetched in the commit message. If 2.0.4 is unavailable, fetch the newest 2.x and pin that.

- [ ] **Step 2: Write the failing test**

Create `tests/test_web_table.py`:

```python
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
        fwupd_version="2.0.14",
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


def test_index_shows_current_versions(client):
    body = client.get("/").text
    assert "4B2QJXD7" in body
    assert "16.00.12.00" in body


def test_index_marks_the_device_with_an_update(client):
    body = client.get("/").text
    assert "5B2QJXD7" in body


def test_index_shows_fwupd_version(client):
    assert "2.0.14" in client.get("/").text


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_web_table.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fwupd_webui.web'`

- [ ] **Step 4: Write the templates**

Create `src/fwupd_webui/web/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}fwupd{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css">
  <script src="/static/htmx.min.js"></script>
</head>
<body>
  <header class="topbar">
    <h1>Firmware Inventory</h1>
    <div class="meta">
      <span>fwupd {{ inventory.fwupd_version }}</span>
      <span>metadata {{ inventory.metadata | metadata_age }}</span>
      <button hx-post="/refresh" hx-target="#device-table" hx-swap="outerHTML">Refresh</button>
    </div>
  </header>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

Create `src/fwupd_webui/web/templates/index.html`:

```html
{% extends "base.html" %}
{% block content %}
{% include "_device_table.html" %}
{% endblock %}
```

Create `src/fwupd_webui/web/templates/_device_table.html`:

```html
<table id="device-table">
  <thead>
    <tr>
      <th>Device</th>
      <th>Vendor</th>
      <th>Version</th>
      <th>Update</th>
      <th>Plugin</th>
      <th>Flags</th>
    </tr>
  </thead>
  <tbody>
    {% for view in inventory.devices %}
    <tr class="device-row {% if view.has_update %}has-update{% endif %}"
        hx-get="/devices/{{ view.device.device_id }}"
        hx-target="next .device-detail"
        hx-swap="innerHTML">
      <td>{{ view.device.name }}</td>
      <td>{{ view.device.vendor or "—" }}</td>
      <td>{{ view.device.version or "—" }}</td>
      <td>
        {% if view.has_update %}
          <span class="badge">{{ view.available[0].version }}</span>
        {% else %}
          <span class="muted">up to date</span>
        {% endif %}
      </td>
      <td>{{ view.device.plugin or "—" }}</td>
      <td class="flags">{{ view.device.flags | join(", ") or "—" }}</td>
    </tr>
    <tr><td colspan="6" class="device-detail"></td></tr>
    {% endfor %}
  </tbody>
</table>
```

Create `src/fwupd_webui/web/static/app.css`:

```css
:root { color-scheme: light dark; --border: #8884; --accent: #c05621; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0; }
.topbar { display: flex; justify-content: space-between; align-items: center;
          gap: 1rem; padding: 0.75rem 1.25rem; border-bottom: 1px solid var(--border); }
.topbar h1 { font-size: 1.05rem; margin: 0; font-weight: 600; }
.meta { display: flex; gap: 1rem; align-items: center; font-size: 0.85rem; }
main { padding: 1.25rem; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 0.75rem; text-transform: uppercase;
     letter-spacing: 0.04em; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); }
td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
.device-row { cursor: pointer; }
.device-row:hover { background: #8881; }
.badge { background: var(--accent); color: #fff; border-radius: 3px;
         padding: 0.1rem 0.4rem; font-size: 0.8rem; }
.muted { opacity: 0.5; }
.flags { font-size: 0.8rem; opacity: 0.75; }
.device-detail:empty { padding: 0; border: 0; }
.banner { padding: 0.6rem 0.9rem; border: 1px solid var(--border);
          border-left: 3px solid var(--accent); margin-bottom: 1rem; }
.diagnostic { max-width: 60rem; }
.diagnostic pre { background: #8881; padding: 0.75rem; overflow-x: auto; }
.detail dl { display: grid; grid-template-columns: 8rem 1fr; gap: 0.25rem 1rem; }
.detail dt { font-weight: 600; opacity: 0.7; }
.detail dd { margin: 0; }
.releases { list-style: none; padding: 0; }
.releases li { border-top: 1px solid var(--border); padding: 0.6rem 0; }
.changelog { font-size: 0.9rem; opacity: 0.8; white-space: pre-wrap; }
```

- [ ] **Step 5: Write the application**

Create empty `src/fwupd_webui/web/__init__.py`.

Create `src/fwupd_webui/web/routes.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates
    inventory = await service.inventory()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"inventory": inventory},
    )


@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates
    await service.refresh()
    inventory = await service.inventory()
    return templates.TemplateResponse(
        request=request,
        name="_device_table.html",
        context={"inventory": inventory},
    )
```

Create `src/fwupd_webui/web/app.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fwupd_webui.config import Config
from fwupd_webui.fwupd.service import MetadataStatus
from fwupd_webui.web.routes import router

WEB_DIR = Path(__file__).parent


def metadata_age(status: MetadataStatus) -> str:
    if status.age_seconds is None:
        return "never fetched"
    hours = status.age_seconds / 3600
    if hours < 1:
        return f"{int(status.age_seconds // 60)}m old"
    if hours < 48:
        return f"{int(hours)}h old"
    return f"{int(hours // 24)}d old"


def create_app(service, config: Config) -> FastAPI:
    app = FastAPI(title="fwupd Web UI", docs_url=None, redoc_url=None)

    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    templates.env.filters["metadata_age"] = metadata_age

    app.state.service = service
    app.state.config = config
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.include_router(router)
    return app
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_table.py -v`
Expected: PASS, 7 tests

- [ ] **Step 7: Run the whole suite and lint**

Run: `make test && make lint`
Expected: all tests pass, ruff clean.

- [ ] **Step 8: Commit**

```bash
git add src/fwupd_webui/web tests/test_web_table.py
git commit -m "feat: web app and device inventory table"
```

---

### Task 7: Device detail fragment

**Files:**
- Create: `src/fwupd_webui/web/templates/_device_detail.html`
- Modify: `src/fwupd_webui/web/routes.py`
- Test: `tests/test_web_detail.py`

**Interfaces:**
- Consumes: `create_app`, `FakeService` pattern from Task 6.
- Produces: route `GET /devices/{device_id}` returning an HTML fragment; 404 for an unknown device ID.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_detail.py`:

```python
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
            )
        ],
        metadata=MetadataStatus(last_refresh=1000.0, age_seconds=60.0, stale=False),
        fwupd_version="2.0.14",
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


def test_detail_escapes_release_description_html(client):
    """fwupd descriptions carry markup from LVFS; it must not be injected raw."""
    body = client.get("/devices/nvme-1").text
    assert "<p>Fixes an issue" not in body
    assert "&lt;p&gt;Fixes an issue" in body


def test_unknown_device_returns_404(client):
    assert client.get("/devices/does-not-exist").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_detail.py -v`
Expected: FAIL — `/devices/nvme-1` returns 404 because the route does not exist.

- [ ] **Step 3: Write the template**

Create `src/fwupd_webui/web/templates/_device_detail.html`:

```html
<div class="detail">
  <dl>
    <dt>Device ID</dt><dd><code>{{ view.device.device_id }}</code></dd>
    <dt>Protocol</dt><dd>{{ view.device.protocol or "—" }}</dd>
    <dt>Serial</dt><dd>{{ view.device.serial or "—" }}</dd>
    <dt>Summary</dt><dd>{{ view.device.summary or "—" }}</dd>
    <dt>GUIDs</dt>
    <dd>
      {% for guid in view.device.guids %}<code>{{ guid }}</code><br>{% else %}—{% endfor %}
    </dd>
  </dl>

  <h3>Available releases</h3>
  {% if view.available %}
  <ul class="releases">
    {% for release in view.available %}
    <li>
      <strong>{{ release.version }}</strong>
      {% if release.urgency %}<span class="badge">{{ release.urgency }}</span>{% endif %}
      {% if release.summary %}<div>{{ release.summary }}</div>{% endif %}
      {% if release.description %}<div class="changelog">{{ release.description }}</div>{% endif %}
      {% if release.uri %}<div><a href="{{ release.uri }}" rel="noreferrer">{{ release.uri }}</a></div>{% endif %}
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="muted">No releases available for this device.</p>
  {% endif %}
</div>
```

Jinja2 autoescaping is on by default in `Jinja2Templates`, which is what makes `test_detail_escapes_release_description_html` pass. Do not add `| safe` to `release.description`.

- [ ] **Step 4: Add the route**

Append to `src/fwupd_webui/web/routes.py`:

```python
from fastapi import HTTPException


@router.get("/devices/{device_id}", response_class=HTMLResponse)
async def device_detail(request: Request, device_id: str) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates
    inventory = await service.inventory()
    for view in inventory.devices:
        if view.device.device_id == device_id:
            return templates.TemplateResponse(
                request=request,
                name="_device_detail.html",
                context={"view": view, "inventory": inventory},
            )
    raise HTTPException(status_code=404, detail=f"no such device: {device_id}")
```

Move the `from fastapi import HTTPException` import up to join the existing `from fastapi import ...` line.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_detail.py -v`
Expected: PASS, 6 tests

- [ ] **Step 6: Commit**

```bash
git add src/fwupd_webui/web tests/test_web_detail.py
git commit -m "feat: device detail fragment with release changelogs"
```

---

### Task 8: Error banner and empty-state diagnostics

The empty state is the single most likely support issue for this container. It gets a real screen, not a blank table.

**Files:**
- Create: `src/fwupd_webui/fwupd/diagnostics.py`
- Create: `src/fwupd_webui/web/templates/_banner.html`
- Create: `src/fwupd_webui/web/templates/empty_state.html`
- Modify: `src/fwupd_webui/web/routes.py`
- Modify: `src/fwupd_webui/web/templates/base.html`
- Test: `tests/test_diagnostics.py`
- Test: `tests/test_web_errors.py`

**Interfaces:**
- Consumes: `FwupdError` subclasses, `Inventory`.
- Produces:
  - `MountCheck` dataclass — `path: str`, `present: bool`, `purpose: str`.
  - `check_mounts(root: Path = Path("/")) -> list[MountCheck]`.
  - `GET /` renders `empty_state.html` when `inventory.devices` is empty; renders the banner when `inventory.metadata.error` is set; returns HTTP 200 with an error page rather than 500 when the service raises `FwupdError`.

- [ ] **Step 1: Write the failing diagnostics test**

Create `tests/test_diagnostics.py`:

```python
from fwupd_webui.fwupd.diagnostics import REQUIRED_MOUNTS, check_mounts


def test_reports_every_required_mount(tmp_path):
    checks = check_mounts(root=tmp_path)
    assert {c.path for c in checks} == set(REQUIRED_MOUNTS)


def test_missing_mount_is_flagged(tmp_path):
    checks = {c.path: c for c in check_mounts(root=tmp_path)}
    assert checks["/run/udev"].present is False


def test_present_mount_is_detected(tmp_path):
    (tmp_path / "run" / "udev").mkdir(parents=True)
    checks = {c.path: c for c in check_mounts(root=tmp_path)}
    assert checks["/run/udev"].present is True


def test_every_mount_explains_its_purpose():
    for check in check_mounts():
        assert check.purpose
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diagnostics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fwupd_webui.fwupd.diagnostics'`

- [ ] **Step 3: Write the diagnostics module**

Create `src/fwupd_webui/fwupd/diagnostics.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REQUIRED_MOUNTS: dict[str, str] = {
    "/sys": "fwupd reads device attributes from sysfs.",
    "/dev": "NVMe, SCSI generic, and MTD ioctls need the host device nodes.",
    "/run/udev": (
        "fwupd enumerates hardware through the udev database. Without the host's "
        "udev directory the device list comes back empty rather than erroring — "
        "this is the most common cause of an empty inventory."
    ),
}


@dataclass(frozen=True)
class MountCheck:
    path: str
    present: bool
    purpose: str


def check_mounts(root: Path = Path("/")) -> list[MountCheck]:
    """Report which required host paths are visible inside this container."""
    checks = []
    for path, purpose in REQUIRED_MOUNTS.items():
        target = Path(root) / path.lstrip("/")
        checks.append(MountCheck(path=path, present=target.exists(), purpose=purpose))
    return checks
```

- [ ] **Step 4: Run the diagnostics tests**

Run: `uv run pytest tests/test_diagnostics.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Write the failing web error tests**

Create `tests/test_web_errors.py`:

```python
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
        return MetadataStatus()

    async def refresh_if_stale(self):
        return MetadataStatus()


def empty_inventory(metadata_error=None) -> Inventory:
    return Inventory(
        devices=[],
        metadata=MetadataStatus(last_refresh=None, age_seconds=None, stale=True,
                                error=metadata_error),
        fwupd_version="2.0.14",
    )


def test_zero_devices_renders_diagnostic_page_not_blank_table():
    client = TestClient(create_app(StubService(empty_inventory()), Config.from_env({})))
    body = client.get("/").text
    assert "No devices" in body
    assert "/run/udev" in body
    assert "<table" not in body


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
        create_app(StubService(error=FwupdNotFound("fwupdtool not found on PATH")),
                   Config.from_env({}))
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
    assert method(path).status_code != 500
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_web_errors.py -v`
Expected: FAIL — the empty state renders an empty `<table>` today.

- [ ] **Step 7: Write the templates**

Create `src/fwupd_webui/web/templates/_banner.html`:

```html
{% if message %}
<div class="banner">{{ message }}</div>
{% endif %}
```

Create `src/fwupd_webui/web/templates/empty_state.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="diagnostic">
  <h2>No devices found</h2>
  <p>
    fwupd enumerated zero devices. On Unraid this is almost always a missing host
    mount or insufficient privileges rather than a hardware problem.
  </p>

  <h3>Required host access</h3>
  <table>
    <thead><tr><th>Path</th><th>Visible</th><th>Why it is needed</th></tr></thead>
    <tbody>
      {% for check in mounts %}
      <tr>
        <td><code>{{ check.path }}</code></td>
        <td>{% if check.present %}yes{% else %}<strong>no</strong>{% endif %}</td>
        <td>{{ check.purpose }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <h3>Also check</h3>
  <ul>
    <li>The container is running with <code>--privileged</code>.</li>
    <li>fwupd version in this image: <code>{{ inventory.fwupd_version }}</code></li>
  </ul>

  {% if inventory.metadata.error %}
  <h3>Last error</h3>
  <pre>{{ inventory.metadata.error }}</pre>
  {% endif %}
</div>
{% endblock %}
```

Create `src/fwupd_webui/web/templates/error.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="diagnostic">
  <h2>fwupd could not be queried</h2>
  <pre>{{ message }}</pre>
  <p>
    This container talks to hardware through <code>fwupdtool</code>. If that command
    is missing or failing, no inventory can be produced.
  </p>
</div>
{% endblock %}
```

Modify `src/fwupd_webui/web/templates/base.html` — insert immediately after `<main>`:

```html
    {% with message = inventory.metadata.error %}{% include "_banner.html" %}{% endwith %}
```

`error.html` extends `base.html`, which reads `inventory.fwupd_version` and `inventory.metadata`. The error route must therefore pass a placeholder `Inventory`; Step 8 does this.

- [ ] **Step 8: Rewrite the index route**

Replace the `index` function in `src/fwupd_webui/web/routes.py` with:

```python
@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates

    try:
        inventory = await service.inventory()
    except FwupdError as exc:
        # A failure to reach fwupdtool is a diagnosable condition, not a crash.
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "message": str(exc),
                "inventory": Inventory(
                    devices=[],
                    metadata=MetadataStatus(error=str(exc)),
                    fwupd_version="unknown",
                ),
            },
        )

    if not inventory.devices:
        return templates.TemplateResponse(
            request=request,
            name="empty_state.html",
            context={"inventory": inventory, "mounts": check_mounts()},
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"inventory": inventory},
    )
```

Add these imports at the top of `routes.py`:

```python
from fwupd_webui.fwupd.cli import FwupdError
from fwupd_webui.fwupd.diagnostics import check_mounts
from fwupd_webui.fwupd.service import Inventory, MetadataStatus
```

Also wrap the `refresh` route body so a `FwupdError` there returns the error page rather than propagating:

```python
@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates
    try:
        await service.refresh()
        inventory = await service.inventory()
    except FwupdError as exc:
        return templates.TemplateResponse(
            request=request,
            name="_banner.html",
            context={"message": str(exc)},
        )
    return templates.TemplateResponse(
        request=request,
        name="_device_table.html",
        context={"inventory": inventory},
    )
```

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS, all tests across every file.

- [ ] **Step 10: Commit**

```bash
git add src/fwupd_webui tests/test_diagnostics.py tests/test_web_errors.py
git commit -m "feat: error banner and empty-state diagnostics"
```

---

### Task 9: Entrypoint, compose, and integration test against real fwupdtool

**Files:**
- Create: `src/fwupd_webui/__main__.py`
- Create: `docker-compose.yml`
- Create: `tests/integration/test_real_fwupdtool.py`
- Create: `scripts/integration-test.sh`
- Modify: `Makefile`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: everything above.
- Produces: `python -m fwupd_webui` starts uvicorn serving `create_app` on the configured port; `make integration` runs the real-`fwupdtool` test inside the image.

- [ ] **Step 1: Write the entrypoint**

Create `src/fwupd_webui/__main__.py`:

```python
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import uvicorn

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdCli
from fwupd_webui.fwupd.service import FwupdService
from fwupd_webui.web.app import create_app

STATE_DIR = Path("/var/lib/fwupd")


def main() -> None:
    config = Config.from_env()
    logging.basicConfig(level=config.log_level.upper())

    cli = FwupdCli(timeout=config.timeout_seconds)
    service = FwupdService(cli, config, STATE_DIR)

    # A refresh failure at boot must never stop the UI from coming up; the
    # inventory is useful without update metadata.
    try:
        asyncio.run(service.refresh_if_stale())
    except Exception:
        logging.getLogger(__name__).warning("startup metadata refresh failed", exc_info=True)

    app = create_app(service, config)
    uvicorn.run(app, host="0.0.0.0", port=config.port, log_level=config.log_level)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the container starts and serves**

Run:

```bash
docker build -t fwupd-webui:dev .
docker run --rm -d --name fwupd-webui-smoke -p 18080:8080 fwupd-webui:dev
sleep 5
curl -sf http://localhost:18080/ | head -20
docker rm -f fwupd-webui-smoke
```

Expected: HTML output. Without host mounts it will be the empty-state diagnostic page — that is the correct behavior and confirms Task 8 works end to end.

- [ ] **Step 3: Write the integration test**

Create `tests/integration/test_real_fwupdtool.py`:

```python
"""Runs against a real fwupdtool binary. Executed inside the container image.

Fixtures alone cannot catch a fwupd release that changes its JSON shape; only
invoking the real binary can.
"""

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


def test_get_updates_does_not_raise():
    """Whether or not updates exist, this must return a list rather than blow up."""
    assert isinstance(FwupdCli().get_updates(), list)


def test_capsule_plugin_is_disabled():
    """Read-only enforcement: the plugin that can write to the ESP must not load."""
    proc = subprocess.run(
        ["fwupdtool", "--json", "get-plugins"], capture_output=True, text=True, check=False
    )
    assert "uefi_capsule" not in proc.stdout or '"disabled"' in proc.stdout
```

- [ ] **Step 4: Write the integration runner**

Create `scripts/integration-test.sh`:

```bash
#!/usr/bin/env bash
# Run the integration suite inside the built image, against real fwupdtool.
set -euo pipefail

IMAGE="${IMAGE:-fwupd-webui:dev}"

docker run --rm \
    -v "$PWD/tests:/app/tests:ro" \
    -v "$PWD/pyproject.toml:/app/pyproject.toml:ro" \
    "$IMAGE" \
    bash -c 'pip install --quiet pytest pytest-asyncio httpx && \
             python -m pytest tests/integration -v'
```

Make it executable: `chmod +x scripts/integration-test.sh`

Add to `Makefile`:

```makefile
integration: image
	./scripts/integration-test.sh
```

- [ ] **Step 5: Run the integration test**

Run: `make integration`
Expected: PASS, 4 tests.

If `test_get_devices_returns_parsed_devices` finds zero devices, `enable-test-devices` did not take effect in this fwupd version. Investigate with `docker run --rm fwupd-webui:dev bash -c 'fwupdtool enable-test-devices; fwupdtool get-devices'` before changing the test.

- [ ] **Step 6: Write docker-compose for development**

Create `docker-compose.yml`:

```yaml
services:
  fwupd-webui:
    build: .
    image: fwupd-webui:dev
    container_name: fwupd-webui
    # Required for device enumeration. Narrowing this to an explicit capability
    # set is a tracked follow-up, not a v1 requirement.
    privileged: true
    ports:
      - "8080:8080"
    volumes:
      - /sys:/sys
      - /dev:/dev
      # fwupd enumerates through the udev database; without this the device
      # list comes back empty rather than erroring.
      - /run/udev:/run/udev:ro
      - fwupd-metadata:/var/lib/fwupd
    environment:
      FWUPD_WEBUI_LOG_LEVEL: info
    restart: unless-stopped

volumes:
  fwupd-metadata:
```

- [ ] **Step 7: Exclude integration tests from the default run**

Modify `pyproject.toml`, replacing the `[tool.pytest.ini_options]` block:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
norecursedirs = ["tests/integration"]
```

Run: `make test`
Expected: unit tests pass, integration tests skipped from the default run.

- [ ] **Step 8: Commit**

```bash
git add src/fwupd_webui/__main__.py docker-compose.yml tests/integration \
        scripts/integration-test.sh Makefile pyproject.toml
git commit -m "feat: entrypoint, compose, and real-fwupdtool integration test"
```

---

### Task 10: Unraid template and documentation

**Files:**
- Create: `unraid/fwupd-webui.xml`
- Create: `README.md`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the published image name.
- Produces: a Community Applications template and user-facing documentation.

- [ ] **Step 1: Write the Unraid Community Applications template**

Create `unraid/fwupd-webui.xml`. Replace `YOURNAME` with the actual GitHub account before publishing.

```xml
<?xml version="1.0"?>
<Container version="2">
  <Name>fwupd-webui</Name>
  <Repository>ghcr.io/YOURNAME/fwupd-webui:latest</Repository>
  <Registry>https://ghcr.io/YOURNAME/fwupd-webui</Registry>
  <Network>bridge</Network>
  <Privileged>true</Privileged>
  <Support>https://github.com/YOURNAME/fwupd-webui/issues</Support>
  <Project>https://github.com/YOURNAME/fwupd-webui</Project>
  <Overview>
    Read-only firmware inventory for your server. Lists every device fwupd can see
    — NVMe drives, HBAs, network cards, Thunderbolt controllers, docks — with its
    current firmware version and any updates available from LVFS.

    This container does not flash anything. It requires privileged mode and host
    access to /sys, /dev and /run/udev in order to enumerate hardware.
  </Overview>
  <Category>Tools: Utilities:</Category>
  <WebUI>http://[IP]:[PORT:8080]/</WebUI>
  <Icon>https://raw.githubusercontent.com/YOURNAME/fwupd-webui/main/unraid/icon.png</Icon>

  <Config Name="WebUI Port" Target="8080" Default="8080" Mode="tcp"
          Description="Port for the web interface" Type="Port" Display="always"
          Required="true" Mask="false">8080</Config>

  <Config Name="sysfs" Target="/sys" Default="/sys" Mode="rw"
          Description="Required: fwupd reads device attributes from sysfs"
          Type="Path" Display="advanced" Required="true" Mask="false">/sys</Config>

  <Config Name="devices" Target="/dev" Default="/dev" Mode="rw"
          Description="Required: NVMe and SCSI ioctls need the host device nodes"
          Type="Path" Display="advanced" Required="true" Mask="false">/dev</Config>

  <Config Name="udev database" Target="/run/udev" Default="/run/udev" Mode="ro"
          Description="Required: without this the device list comes back empty"
          Type="Path" Display="advanced" Required="true" Mask="false">/run/udev</Config>

  <Config Name="Metadata cache" Target="/var/lib/fwupd"
          Default="/mnt/user/appdata/fwupd-webui" Mode="rw"
          Description="Persists downloaded LVFS metadata across restarts"
          Type="Path" Display="always" Required="true" Mask="false">/mnt/user/appdata/fwupd-webui</Config>

  <Config Name="Metadata refresh interval (hours)" Target="FWUPD_WEBUI_REFRESH_INTERVAL_HOURS"
          Default="24" Description="How old LVFS metadata may be before startup refetches it"
          Type="Variable" Display="advanced" Required="false" Mask="false">24</Config>

  <Config Name="LVFS remote" Target="FWUPD_WEBUI_LVFS_REMOTE" Default="lvfs"
          Description="lvfs or lvfs-testing" Type="Variable" Display="advanced"
          Required="false" Mask="false">lvfs</Config>
</Container>
```

- [ ] **Step 2: Write the README**

Create `README.md`:

````markdown
# fwupd-webui

A read-only web UI for [fwupd](https://fwupd.org), packaged as a Docker container for
Unraid and other systems without a native fwupd installation.

It lists every device fwupd can enumerate — NVMe drives, HBAs, network cards,
Thunderbolt controllers, docks — with current firmware versions and any updates
available from LVFS.

**It does not flash firmware.** This release is inventory only.

## Why a container

Unraid runs Slackware from a USB stick with the OS in RAM and no persistent package
manager, so there is no practical way to install fwupd on the host. A container is
the natural distribution mechanism on that platform.

## Running it

```bash
docker run -d --name fwupd-webui \
  --privileged \
  -p 8080:8080 \
  -v /sys:/sys \
  -v /dev:/dev \
  -v /run/udev:/run/udev:ro \
  -v /mnt/user/appdata/fwupd-webui:/var/lib/fwupd \
  ghcr.io/YOURNAME/fwupd-webui:latest
```

On Unraid, install from Community Applications instead — the template sets all of
this up for you.

### Why privileged

Enumerating firmware means talking to hardware: NVMe admin commands, SCSI generic
ioctls, sysfs attributes. That needs privileges a normal container does not have.
Narrowing this to an explicit capability set is a planned improvement.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `FWUPD_WEBUI_PORT` | `8080` | Listen port |
| `FWUPD_WEBUI_REFRESH_INTERVAL_HOURS` | `24` | Age above which startup refetches LVFS metadata |
| `FWUPD_WEBUI_TIMEOUT_SECONDS` | `120` | Hard timeout per fwupdtool invocation |
| `FWUPD_WEBUI_LVFS_REMOTE` | `lvfs` | `lvfs` or `lvfs-testing` |
| `FWUPD_WEBUI_LOG_LEVEL` | `info` | Log verbosity |

## Troubleshooting

**No devices listed.** The UI shows a diagnostic page for this case listing which
required mounts are visible. The usual cause is a missing `/run/udev` mount — fwupd
enumerates through the udev database and returns an empty list without it, rather
than an error.

**Metadata is stale.** Refresh failures are non-fatal; the device inventory still
renders. Check that the container can reach `fwupd.org`.

## Development

```bash
make install      # sync dependencies
make test         # unit tests
make lint         # ruff
make image        # build the container
make fixtures     # regenerate test fixtures from real fwupdtool output
make integration  # run tests against real fwupdtool inside the image
```

## Architecture

There is no fwupd daemon in this container. The backend shells out to `fwupdtool`,
which runs the fwupd engine in-process — so the image needs no DBus broker and no
init system, and stays a single Python process.

The `uefi_capsule` plugin is disabled in the baked `/etc/fwupd/fwupd.conf`. That is
the plugin capable of staging a firmware capsule to the EFI system partition, which
on Unraid is the bootable USB stick holding the OS and array configuration. Disabling
it makes read-only a structural property rather than a convention.

## License

MIT
````

- [ ] **Step 3: Write CI**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --extra dev
      - run: uv run ruff check src tests
      - run: uv run pytest -v

  integration:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t fwupd-webui:dev .
      - run: ./scripts/integration-test.sh
```

- [ ] **Step 4: Verify everything passes**

Run:

```bash
make lint
make test
make image
make integration
```

Expected: all four succeed.

- [ ] **Step 5: Commit**

```bash
git add unraid README.md .github
git commit -m "docs: Unraid template, README, and CI"
```

---

## Self-Review Notes

Checked against the spec:

| Spec section | Covered by |
| --- | --- |
| Daemonless architecture | Task 2 (image without init), Task 4 (`FwupdCli`) |
| Base image, pinned fwupd version | Task 2 |
| Runs as root | Task 2 (no `USER` directive), README |
| Host mounts `/sys`, `/dev`, `/run/udev` | Task 8 (`diagnostics.py`), Task 9 (compose), Task 10 (template) |
| Read-only enforced structurally | Task 2 (`fwupd.conf`), Task 4 (`test_no_install_verb_exists`), Task 9 (`test_capsule_plugin_is_disabled`) |
| `fwupd/cli.py` sole subprocess owner | Task 4 |
| `fwupd/models.py` tolerant parsing | Task 3 |
| `fwupd/service.py` join + policy | Task 5 |
| `config.py` env vars | Task 1 |
| Metadata cache volume + threshold | Task 5, Task 9 |
| Refresh failure non-fatal | Task 5, Task 8 |
| Device table | Task 6 |
| Device detail with releases | Task 7 |
| Header with version, age, refresh | Task 6 |
| Empty-state diagnostic screen | Task 8 |
| Error handling table (missing binary, bad JSON, timeout, refresh failure) | Task 4, Task 8 |
| Concurrency: threadpool + lock | Task 5 |
| Unit tests against real fixtures | Task 2, Task 3 |
| Integration test with `enable-test-devices` | Task 9 |
| Web tests via TestClient | Tasks 6, 7, 8 |
| Empty-state test | Task 8 |
| Dockerfile with uv, compose, amd64, ghcr, Unraid template | Tasks 2, 9, 10 |

Known uncertainties that the plan resolves empirically rather than by assumption:

1. **Exact fwupd JSON field names.** Task 2 captures real output before Task 3 writes any alias. If the fixture disagrees with the aliases, the fixture wins.
2. **The `get-updates` "nothing to do" error code.** Task 2 Step 6 records the real value; Task 4 keys `_BENIGN_UPDATE_ERRORS` off it.
3. **htmx version.** Task 6 Step 1 pins whatever is fetched and records it.
````
