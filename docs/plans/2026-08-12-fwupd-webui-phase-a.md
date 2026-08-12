# fwupd Web UI — Phase A Implementation Plan (Flashing)

**Goal:** Add the firmware write path — select a release, flash it, watch progress — gated behind an
explicit enable flag and a typed-name confirmation. (As planned this also included a plugin
allowlist; see the correction note below — it was removed during execution.)

**Architecture:** A single server-owned `FlashJob` runs one `fwupdtool install` at a time under the
existing `asyncio.Lock`. Progress is parsed from the subprocess's stderr and polled by htmx once a
second. When `FWUPD_WEBUI_ENABLE_FLASHING` is not set, none of the flash routes are registered and
the application is byte-for-byte the phase C container in behaviour.

**Tech Stack:** Python 3.13+, FastAPI, Jinja2, htmx 2.0.4 (vendored), pydantic, pytest
(`asyncio_mode=auto`), ruff.

**Spec:** `docs/specs/2026-08-12-fwupd-webui-phase-a-design.md`

> **Historical document — executed 2026-08-12, with corrections.** Kept as a record of
> what was planned, not as a description of the code. Five things in it turned out to be
> wrong when run against real fwupd and real hardware, and the spec carries the corrected
> design:
>
> 1. **`--json` suppresses install progress entirely.** Task 4 builds
>    `fwupdtool --json install`, which would have produced a progress view that never
>    moved: one stderr line instead of 77. `install` is invoked without `--json`.
> 2. **The install timeout bounded nothing.** `proc.wait(timeout=…)` sits after a
>    blocking stderr read, so a hung fwupdtool emitting no output would never time out.
>    Replaced with a watchdog thread that kills the process.
> 3. **The `--force` structural guard was unsatisfiable as written.** `refresh()` already
>    passes `--force` legitimately, to bypass fwupd's metadata-freshness short-circuit.
>    The guard is scoped to `install()`'s source instead.
> 4. **`python-multipart` was missing.** FastAPI needs it to parse the confirm step's
>    form body; it was added as a runtime dependency.
> 5. **The safety policy in Tasks 2, 6 and 8 was replaced entirely.** The plugin
>    allowlist made the deployed host's cache pool — the drive Docker runs from — the
>    easiest device in the UI to flash. There is now no allowlist: every flash requires
>    the device name typed exactly, and storage plugins additionally carry a data-loss
>    banner. `Permission` has `flashable`/`is_storage`, not `allowed`/`needs_override`.


## Global Constraints

- **Never construct `--force`.** It relaxes fwupd's own runtime safety checks. Task 4 adds a test
  asserting it never appears in any argv.
- **Never add a cancel path.** Killing `fwupdtool` mid-write can leave partially written firmware.
- **`uefi_capsule` stays in `DisabledPlugins`** in `docker/fwupd.conf`. Do not touch that file.
- **Allowlist is exactly `{"nvme", "thunderbolt"}`** initially. `ata` is excluded deliberately.
- **`FWUPD_WEBUI_ENABLE_FLASHING` defaults to false**, and when false the flash routes are not
  registered at all — a real 404, not a handler returning 403.
- **Templates are autoescaped.** Never add `| safe` to release descriptions; they are vendor strings.
- **No AI attribution in commit messages.** No `Co-Authored-By` or session trailers.
- **Run `make lint` before every commit.** `ruff format` is enforced in CI.
- **Every `fwupdtool` call goes through `asyncio.to_thread`** and holds the service lock.

---

### Task 1: Config — enable flag and install timeout

**Files:**
- Modify: `src/fwupd_webui/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config.enable_flashing: bool`, `Config.install_timeout_seconds: int`,
  `_bool_env(env, key, default) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_flashing_is_disabled_by_default():
    assert Config.from_env({}).enable_flashing is False


def test_flashing_can_be_enabled():
    for raw in ("true", "TRUE", "1", "yes", "Yes"):
        assert Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": raw}).enable_flashing is True


def test_flashing_can_be_explicitly_disabled():
    for raw in ("false", "FALSE", "0", "no"):
        assert Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": raw}).enable_flashing is False


def test_unparseable_enable_flashing_is_a_startup_error():
    """A typo must not quietly resolve to either value. Enabling by accident
    grants firmware writes; disabling by accident silently drops the feature."""
    with pytest.raises(ValueError, match="FWUPD_WEBUI_ENABLE_FLASHING"):
        Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": "maybe"})


def test_install_timeout_defaults_to_thirty_minutes():
    assert Config.from_env({}).install_timeout_seconds == 1800


def test_install_timeout_is_configurable():
    cfg = Config.from_env({"FWUPD_WEBUI_INSTALL_TIMEOUT_SECONDS": "600"})
    assert cfg.install_timeout_seconds == 600
```

Ensure `import pytest` is present at the top of the file.

- [ ] **Step 2: Run and verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `Config` has no attribute `enable_flashing`.

- [ ] **Step 3: Implement**

In `src/fwupd_webui/config.py`, add after `_int_env`:

```python
_TRUE = frozenset({"true", "1", "yes"})
_FALSE = frozenset({"false", "0", "no"})


def _bool_env(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(
        f"{key} must be one of {sorted(_TRUE | _FALSE)}, got {raw!r}"
    )
```

Add two fields to `Config` (after `timeout_seconds`):

```python
    install_timeout_seconds: int = 1800
    enable_flashing: bool = False
```

And in `from_env`, inside the `cls(...)` call:

```python
            install_timeout_seconds=_int_env(e, "FWUPD_WEBUI_INSTALL_TIMEOUT_SECONDS", 1800),
            enable_flashing=_bool_env(e, "FWUPD_WEBUI_ENABLE_FLASHING", False),
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config.py -v && make lint`
Expected: PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/fwupd_webui/config.py tests/test_config.py
git commit -m "feat: add flashing enable flag and install timeout config

FWUPD_WEBUI_ENABLE_FLASHING defaults to false. An unparseable value is a
startup error rather than a silent fallback: resolving a typo to 'enabled'
would grant firmware writes nobody asked for, and resolving it to
'disabled' would silently drop a feature the operator meant to turn on."
```

---

### Task 2: Safety policy

**Files:**
- Create: `src/fwupd_webui/fwupd/policy.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: `Device` from `fwupd_webui.fwupd.models`.
- Produces: `RUNTIME_SAFE_PLUGINS: frozenset[str]`, `Permission` dataclass with fields
  `allowed: bool`, `needs_override: bool`, `reason: str`, and
  `evaluate(device, *, enabled: bool) -> Permission`, plus
  `check_override(device, typed_name: str | None) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_policy.py`:

```python
from fwupd_webui.fwupd.models import Device
from fwupd_webui.fwupd.policy import check_override, evaluate


def device(**kwargs) -> Device:
    payload = {"DeviceId": "d-1", "Name": "Test Device"}
    payload.update(kwargs)
    return Device.model_validate(payload)


def test_allowlisted_updatable_device_is_permitted():
    perm = evaluate(device(Plugin="nvme", Flags=["updatable"]), enabled=True)
    assert perm.allowed is True
    assert perm.needs_override is False


def test_thunderbolt_is_allowlisted():
    perm = evaluate(device(Plugin="thunderbolt", Flags=["updatable"]), enabled=True)
    assert perm.allowed is True


def test_ata_requires_an_override():
    """The four array drives on the deployed host are ata. Flashing drive
    firmware under a live array risks the array, not just the drive."""
    perm = evaluate(device(Plugin="ata", Flags=["updatable"]), enabled=True)
    assert perm.allowed is False
    assert perm.needs_override is True
    assert "ata" in perm.reason


def test_device_without_updatable_flag_offers_nothing():
    perm = evaluate(device(Plugin="nvme", Flags=["internal"]), enabled=True)
    assert perm.allowed is False
    assert perm.needs_override is False
    assert "not updatable" in perm.reason


def test_nothing_is_permitted_when_flashing_is_disabled():
    perm = evaluate(device(Plugin="nvme", Flags=["updatable"]), enabled=False)
    assert perm.allowed is False
    assert perm.needs_override is False
    assert "FWUPD_WEBUI_ENABLE_FLASHING" in perm.reason


def test_needs_reboot_does_not_block():
    """Every updatable device on the deployed host reports needs-reboot.
    Blocking on it would disable the feature entirely."""
    perm = evaluate(device(Plugin="nvme", Flags=["updatable", "needs-reboot"]), enabled=True)
    assert perm.allowed is True


def test_override_accepts_the_exact_device_name():
    assert check_override(device(Name="ST4000VN008-2DR166"), "ST4000VN008-2DR166") is True


def test_override_rejects_a_near_miss():
    assert check_override(device(Name="ST4000VN008-2DR166"), "ST4000VN008") is False


def test_override_rejects_absent_input():
    assert check_override(device(Name="ST4000VN008-2DR166"), None) is False
    assert check_override(device(Name="ST4000VN008-2DR166"), "") is False


def test_override_uses_display_name_for_unnamed_devices():
    unnamed = Device.model_validate({"DeviceId": "u", "Plugin": "linux_display"})
    assert check_override(unnamed, "Unknown linux_display device") is True
```

- [ ] **Step 2: Run and verify they fail**

Run: `uv run pytest tests/test_policy.py -v`
Expected: FAIL — no module named `fwupd_webui.fwupd.policy`.

- [ ] **Step 3: Implement**

Create `src/fwupd_webui/fwupd/policy.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from fwupd_webui.fwupd.models import Device

# Plugins whose devices update at runtime without endangering anything the host
# depends on while the write is in flight.
#
# `ata` is deliberately absent. On the deployed host those devices are the array
# drives, and rewriting drive firmware under a live array risks the array rather
# than just the drive. Adding a plugin here is a one-line change -- make it
# deliberately, with a test.
RUNTIME_SAFE_PLUGINS = frozenset({"nvme", "thunderbolt"})


@dataclass(frozen=True)
class Permission:
    allowed: bool
    needs_override: bool
    reason: str


def evaluate(device: Device, *, enabled: bool) -> Permission:
    """Decide whether `device` may be flashed. Pure; no I/O."""
    if not enabled:
        return Permission(
            allowed=False,
            needs_override=False,
            reason="Flashing is disabled. Set FWUPD_WEBUI_ENABLE_FLASHING=true to enable it.",
        )
    if not device.updatable:
        return Permission(
            allowed=False,
            needs_override=False,
            reason="fwupd reports this device is not updatable.",
        )
    plugin = device.plugin or "unknown"
    if plugin not in RUNTIME_SAFE_PLUGINS:
        return Permission(
            allowed=False,
            needs_override=True,
            reason=(
                f"The {plugin} plugin is not on the runtime-safe list. "
                "Confirm by typing the device name exactly."
            ),
        )
    return Permission(allowed=True, needs_override=False, reason="")


def check_override(device: Device, typed_name: str | None) -> bool:
    """True when the operator typed the device name exactly. Server-side; a
    disabled button in HTML is a suggestion, this is the control."""
    if not typed_name:
        return False
    return typed_name.strip() == device.display_name
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_policy.py -v && make lint`
Expected: 10 passed, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/fwupd_webui/fwupd/policy.py tests/test_policy.py
git commit -m "feat: add per-device flash safety policy

Three tiers: allowlisted plugins permit directly, other updatable devices
require typing the device name exactly, and non-updatable devices offer
nothing. The allowlist starts at nvme and thunderbolt; ata is excluded
because the four ata devices on the deployed host are the array drives.

needs-reboot is explicitly not a blocking condition. Every updatable
device on that host reports it, so blocking would disable the feature."
```

---

### Task 3: Progress line parsing

**Files:**
- Create: `tests/fixtures/install-progress-stderr.txt`
- Modify: `src/fwupd_webui/fwupd/cli.py`
- Test: `tests/test_progress.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ProgressLine` dataclass with `phase: str` and `percent: float`, and
  `parse_progress_line(line: str) -> ProgressLine | None` in `cli.py`.

- [ ] **Step 1: Capture the fixture from real fwupd**

Real output beats a guess about the format. Run:

```bash
docker build --build-arg WITH_TEST_DEVICES=true -t fwupd-webui:test .
docker run --rm fwupd-webui:test bash -c '
  fwupdtool enable-test-devices >/dev/null 2>&1
  fwupdtool --json install /usr/share/installed-tests/fwupd/fakedevice124.cab 2>&1 >/dev/null
' > tests/fixtures/install-progress-stderr.txt
wc -l tests/fixtures/install-progress-stderr.txt
```

Expect roughly 75-80 lines. Confirm it contains percentage lines, at least one ANSI-coloured
`WARNING:` line, and at least one timestamped `FuEngine` line. **If the format differs from what the
tests below assume, the fixture wins — adjust the tests and note it in the commit.**

- [ ] **Step 2: Write the failing tests**

Create `tests/test_progress.py`:

```python
import re
from pathlib import Path

from fwupd_webui.fwupd.cli import parse_progress_line

FIXTURE = Path(__file__).parent / "fixtures" / "install-progress-stderr.txt"


def test_parses_a_plain_progress_line():
    parsed = parse_progress_line("Writing…: 42.3%")
    assert parsed is not None
    assert parsed.phase == "Writing"
    assert parsed.percent == 42.3


def test_parses_a_phase_containing_a_space():
    parsed = parse_progress_line("Restarting device…: 68.5%")
    assert parsed is not None
    assert parsed.phase == "Restarting device"


def test_strips_ansi_colour_codes():
    parsed = parse_progress_line("\x1b[32mWriting…\x1b[0m: 10.0%")
    assert parsed is not None
    assert parsed.phase == "Writing"


def test_ignores_a_warning_line():
    assert parse_progress_line("WARNING: UEFI ESP partition not detected") is None


def test_ignores_engine_chatter():
    line = "06:44:19.444 FuEngine failed to coldplug backend modem-manager: no such file"
    assert parse_progress_line(line) is None


def test_ignores_blank_lines():
    assert parse_progress_line("") is None
    assert parse_progress_line("   ") is None


def test_every_phase_in_the_real_capture_is_recognised():
    """Guards against fwupd renaming or reformatting a phase."""
    lines = FIXTURE.read_text().splitlines()
    parsed = [p for p in (parse_progress_line(line) for line in lines) if p]
    assert len(parsed) > 20, "fixture should contain many progress lines"

    phases = {p.phase for p in parsed}
    assert {"Loading", "Decompressing", "Writing", "Verifying"} <= phases

    assert all(0.0 <= p.percent <= 100.0 for p in parsed)


def test_unrecognised_lines_in_the_real_capture_are_not_progress():
    """Anything the parser rejects must genuinely not be a percentage line, so
    a format change shows up as a test failure rather than silent blankness."""
    lines = [line for line in FIXTURE.read_text().splitlines() if line.strip()]
    rejected = [line for line in lines if parse_progress_line(line) is None]
    for line in rejected:
        assert not re.search(r":\s*\d+(\.\d+)?%\s*$", line), f"parser missed: {line!r}"
```

- [ ] **Step 3: Run and verify they fail**

Run: `uv run pytest tests/test_progress.py -v`
Expected: FAIL — cannot import `parse_progress_line`.

- [ ] **Step 4: Implement**

In `src/fwupd_webui/fwupd/cli.py`, add `import re` and `from dataclasses import dataclass` at the
top, then after the exception classes:

```python
# fwupdtool colours some stderr output even when stderr is not a TTY.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Progress arrives as "Writing…: 42.3%". The phase may contain spaces
# ("Restarting device"). The trailing ellipsis is fwupd's, not ours.
_PROGRESS_RE = re.compile(r"^(?P<phase>[^:]+?)\s*[….]*\s*:\s*(?P<percent>\d+(?:\.\d+)?)%$")


@dataclass(frozen=True)
class ProgressLine:
    phase: str
    percent: float


def parse_progress_line(line: str) -> ProgressLine | None:
    """Parse one stderr line from `fwupdtool install`.

    Returns None for anything that is not a progress report -- warnings,
    engine log chatter, blank lines. Callers route those to the log tail
    rather than failing, so a fwupd format change degrades the progress
    display instead of breaking the flash.
    """
    cleaned = _ANSI_RE.sub("", line).strip()
    if not cleaned:
        return None
    match = _PROGRESS_RE.match(cleaned)
    if not match:
        return None
    percent = float(match.group("percent"))
    if not 0.0 <= percent <= 100.0:
        return None
    return ProgressLine(phase=match.group("phase").strip(), percent=percent)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_progress.py -v && make lint`
Expected: PASS.

If `test_every_phase_in_the_real_capture_is_recognised` fails, read the fixture and fix the regex —
do not weaken the assertion.

- [ ] **Step 6: Commit**

```bash
git add src/fwupd_webui/fwupd/cli.py tests/test_progress.py tests/fixtures/install-progress-stderr.txt
git commit -m "feat: parse fwupdtool install progress from stderr

Progress arrives as '<phase>: <percent>%' on stderr, interleaved with
ANSI-coloured warnings and timestamped engine chatter. Unrecognised lines
return None so callers can route them to a log tail; a fwupd format change
then degrades the progress display rather than breaking the flash.

Tested against a real capture from a synthetic-device install, committed
as a fixture. One test asserts nothing the parser rejects looks like a
percentage line, so a reformat surfaces as a failure not as blankness."
```

---

### Task 4: `cli.install()` with streamed progress

**Files:**
- Modify: `src/fwupd_webui/fwupd/cli.py`
- Test: `tests/test_cli_install.py`
- Modify: `tests/test_cli.py` (retire the read-only guard)

**Interfaces:**
- Consumes: `parse_progress_line`, `ProgressLine` from Task 3.
- Produces:
  `FwupdCli.install(target: str, device_id: str, *, allow_older: bool = False,
  allow_reinstall: bool = False, on_progress: Callable[[ProgressLine], None] | None = None,
  on_log: Callable[[str], None] | None = None) -> None`.
  Raises `FwupdCommandFailed` on non-zero exit, `FwupdTimeout` on timeout.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_install.py`. These use a real subprocess — a shell script standing in for
`fwupdtool` — so the `Popen` streaming path is genuinely exercised without needing fwupd:

```python
import os
import stat
from pathlib import Path

import pytest

from fwupd_webui.fwupd.cli import FwupdCli, FwupdCommandFailed, FwupdTimeout


def fake_binary(tmp_path: Path, body: str) -> str:
    """Write an executable stand-in for fwupdtool and return its path."""
    script = tmp_path / "fake-fwupdtool"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_install_builds_the_expected_argv(tmp_path):
    argv_dump = tmp_path / "argv.txt"
    binary = fake_binary(tmp_path, f'printf "%s\\n" "$@" > {argv_dump}\nexit 0\n')

    FwupdCli(binary=binary).install("https://lvfs/f.cab", "dev-1")

    assert argv_dump.read_text().split("\n")[:-1] == [
        "--json",
        "install",
        "https://lvfs/f.cab",
        "dev-1",
    ]


def test_install_adds_allow_older_for_a_downgrade(tmp_path):
    argv_dump = tmp_path / "argv.txt"
    binary = fake_binary(tmp_path, f'printf "%s\\n" "$@" > {argv_dump}\nexit 0\n')

    FwupdCli(binary=binary).install("f.cab", "dev-1", allow_older=True)

    assert "--allow-older" in argv_dump.read_text()


def test_install_adds_allow_reinstall(tmp_path):
    argv_dump = tmp_path / "argv.txt"
    binary = fake_binary(tmp_path, f'printf "%s\\n" "$@" > {argv_dump}\nexit 0\n')

    FwupdCli(binary=binary).install("f.cab", "dev-1", allow_reinstall=True)

    assert "--allow-reinstall" in argv_dump.read_text()


def test_install_streams_progress_as_it_arrives(tmp_path):
    binary = fake_binary(
        tmp_path,
        'echo "Loading…: 10.0%" >&2\n'
        'echo "Writing…: 50.0%" >&2\n'
        'echo "Verifying…: 100.0%" >&2\n'
        "exit 0\n",
    )
    seen = []
    FwupdCli(binary=binary).install("f.cab", "d", on_progress=seen.append)

    assert [(p.phase, p.percent) for p in seen] == [
        ("Loading", 10.0),
        ("Writing", 50.0),
        ("Verifying", 100.0),
    ]


def test_install_routes_non_progress_lines_to_the_log(tmp_path):
    binary = fake_binary(
        tmp_path,
        'echo "WARNING: ESP not found" >&2\n'
        'echo "Writing…: 50.0%" >&2\n'
        "exit 0\n",
    )
    logs = []
    FwupdCli(binary=binary).install("f.cab", "d", on_log=logs.append)

    assert any("ESP not found" in line for line in logs)
    assert not any("50.0%" in line for line in logs)


def test_install_raises_with_stderr_tail_on_failure(tmp_path):
    binary = fake_binary(tmp_path, 'echo "device is locked" >&2\nexit 3\n')

    with pytest.raises(FwupdCommandFailed) as exc:
        FwupdCli(binary=binary).install("f.cab", "d")

    assert exc.value.exit_code == 3
    assert "device is locked" in exc.value.stderr


def test_install_times_out(tmp_path):
    binary = fake_binary(tmp_path, "sleep 10\n")

    with pytest.raises(FwupdTimeout):
        FwupdCli(binary=binary, install_timeout=1).install("f.cab", "d")


def test_install_never_constructs_force(tmp_path):
    """--force relaxes fwupd's own runtime safety checks. It has no place
    behind a web button. This replaces the phase C no-install-verb guard."""
    argv_dump = tmp_path / "argv.txt"
    binary = fake_binary(tmp_path, f'printf "%s\\n" "$@" > {argv_dump}\nexit 0\n')

    cli = FwupdCli(binary=binary)
    cli.install("f.cab", "d", allow_older=True, allow_reinstall=True)

    assert "--force" not in argv_dump.read_text()


def test_source_never_mentions_force():
    """Structural guard: no code path anywhere in cli.py builds --force."""
    import inspect

    import fwupd_webui.fwupd.cli as cli_module

    source = Path(inspect.getsourcefile(cli_module)).read_text()
    assert '"--force"' not in source
```

- [ ] **Step 2: Retire the phase C read-only guard, in its own commit**

Delete `test_no_install_verb_exists` from `tests/test_cli.py` (the last test in the file, plus its
now-unused `import inspect`). It asserts the string `"install"` never appears in `cli.py`, which
Phase A exists to violate.

Commit this **alone**, so removing a safety guard is visible in history rather than buried:

```bash
git add tests/test_cli.py
git commit -m "test: retire the read-only guard ahead of the flash path

test_no_install_verb_exists asserted cli.py never contains the string
'install'. Phase A adds exactly that, deliberately and behind four
independent controls.

Replaced in the next commit by a guard asserting --force is never
constructed, plus the policy tests. Removed on its own so that dropping a
safety assertion is visible in history rather than folded into a feature."
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run: `uv run pytest tests/test_cli_install.py -v`
Expected: FAIL — `FwupdCli` has no attribute `install`.

- [ ] **Step 4: Implement**

In `src/fwupd_webui/fwupd/cli.py`, add `from collections.abc import Callable` to the imports.

Change `__init__` to accept the separate install timeout:

```python
    def __init__(self, binary: str = "fwupdtool", timeout: int = 120, install_timeout: int = 1800):
        self._binary = binary
        self._timeout = timeout
        self._install_timeout = install_timeout
```

Add the method:

```python
    def install(
        self,
        target: str,
        device_id: str,
        *,
        allow_older: bool = False,
        allow_reinstall: bool = False,
        on_progress: Callable[[ProgressLine], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        """Flash `target` (a local cab path or an https URI) onto `device_id`.

        fwupd downloads and jcat-verifies a URI itself, so no download or
        signature handling belongs here.

        Unlike every other method this streams: `install` reports progress on
        stderr as it works, so it uses Popen and reads line by line rather than
        subprocess.run. Blocks until the process exits.

        Never constructs --force. It relaxes fwupd's own runtime safety checks.
        """
        argv = [self._binary, "--json", "install"]
        if allow_older:
            argv.append("--allow-older")
        if allow_reinstall:
            argv.append("--allow-reinstall")
        argv += [target, device_id]

        log.info("installing %s onto %s", target, device_id)
        tail: list[str] = []
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise FwupdNotFound(f"{self._binary} not found on PATH") from exc

        assert proc.stderr is not None
        try:
            for raw in proc.stderr:
                line = raw.rstrip("\n")
                progress = parse_progress_line(line)
                if progress is not None:
                    if on_progress is not None:
                        on_progress(progress)
                    continue
                if line.strip():
                    tail.append(line)
                    del tail[:-_LOG_TAIL_LINES]
                    if on_log is not None:
                        on_log(line)
            code = proc.wait(timeout=self._install_timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait()
            raise FwupdTimeout(
                f"{self._binary} install timed out after {self._install_timeout}s"
            ) from exc

        if code != 0:
            raise FwupdCommandFailed(code, "\n".join(tail))
```

Add near the other module constants:

```python
_LOG_TAIL_LINES = 50
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_cli_install.py tests/test_cli.py -v && make lint`
Expected: all pass.

- [ ] **Step 6: Fix the incorrect phase C comment**

The `FwupdCli` docstring claims fwupdtool's progress bar is drawn only when stderr is a TTY, so
piped output stays clean. That is false for `install`. Replace that sentence with:

```python
    fwupdtool writes JSON to stdout and diagnostics to stderr. Enumeration
    verbs produce no progress output through a pipe, but `install` does --
    it reports phase and percentage on stderr even when stderr is not a
    TTY, which is what makes streamed progress possible.
```

- [ ] **Step 7: Commit**

```bash
git add src/fwupd_webui/fwupd/cli.py tests/test_cli_install.py
git commit -m "feat: add streaming install to the fwupdtool client

install() takes a local cab path or an https URI; fwupd downloads and
jcat-verifies a URI itself, so no download or signature code lives here.

This is the one method that cannot use the blocking _run shape: progress
arrives on stderr while the process works, so it uses Popen and reads line
by line. Progress lines go to on_progress, everything else to a bounded
log tail kept for the failure view.

--force is never constructed, asserted both by argv inspection and by a
structural check on the source. This replaces the retired read-only guard.

Also corrects the class docstring, which claimed progress only appears on
a TTY. True for enumeration, false for install."
```

---

### Task 5: The flash job

**Files:**
- Create: `src/fwupd_webui/fwupd/flash.py`
- Test: `tests/test_flash.py`

**Interfaces:**
- Consumes: `ProgressLine`, `FwupdError` from `cli.py`.
- Produces: `FlashStatus` (str enum: `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`), `FlashJob`
  dataclass, and `FlashManager` with `active: bool`, `job: FlashJob | None`,
  `start(...) -> FlashJob`, `dismiss() -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_flash.py`:

```python
import pytest

from fwupd_webui.fwupd.cli import FwupdCommandFailed, ProgressLine
from fwupd_webui.fwupd.flash import FlashManager, FlashStatus


class FakeCli:
    """Stands in for FwupdCli.install, driving the progress callbacks."""

    def __init__(self, progress=(), logs=(), error=None):
        self._progress = progress
        self._logs = logs
        self._error = error
        self.calls = []

    def install(self, target, device_id, *, allow_older=False, allow_reinstall=False,
                on_progress=None, on_log=None):
        self.calls.append((target, device_id, allow_older, allow_reinstall))
        for phase, percent in self._progress:
            if on_progress:
                on_progress(ProgressLine(phase=phase, percent=percent))
        for line in self._logs:
            if on_log:
                on_log(line)
        if self._error:
            raise self._error


def manager(cli) -> FlashManager:
    return FlashManager(cli)


async def test_a_successful_flash_ends_succeeded():
    mgr = manager(FakeCli(progress=[("Writing", 50.0), ("Verifying", 100.0)]))
    job = await mgr.start("https://lvfs/f.cab", "dev-1", device_name="NVMe", version="2.0")

    assert job.status is FlashStatus.SUCCEEDED
    assert job.phase == "Verifying"
    assert job.percent == 100.0
    assert job.error is None


async def test_progress_is_visible_while_running():
    seen = []
    cli = FakeCli(progress=[("Writing", 10.0), ("Writing", 90.0)])
    mgr = manager(cli)

    original = cli.install

    def spy(*args, **kwargs):
        def record(p):
            kwargs["on_progress"](p)
            seen.append((mgr.job.phase, mgr.job.percent))

        return original(*args, **{**kwargs, "on_progress": record})

    cli.install = spy
    await mgr.start("f.cab", "d", device_name="N", version="1")

    assert seen == [("Writing", 10.0), ("Writing", 90.0)]


async def test_a_failed_flash_records_exit_code_and_last_phase():
    cli = FakeCli(
        progress=[("Writing", 40.0)],
        error=FwupdCommandFailed(3, "device is locked"),
    )
    job = await manager(cli).start("f.cab", "d", device_name="N", version="1")

    assert job.status is FlashStatus.FAILED
    assert job.exit_code == 3
    assert "device is locked" in job.error
    assert job.phase == "Writing", "the phase reached tells you where it broke"


async def test_log_lines_are_retained_for_the_failure_view():
    cli = FakeCli(logs=["WARNING: ESP not found"], error=FwupdCommandFailed(1, "boom"))
    job = await manager(cli).start("f.cab", "d", device_name="N", version="1")

    assert any("ESP not found" in line for line in job.log)


async def test_only_one_job_runs_at_a_time():
    mgr = manager(FakeCli())
    await mgr.start("f.cab", "d", device_name="N", version="1")
    mgr.job.status = FlashStatus.RUNNING  # simulate an in-flight job

    with pytest.raises(RuntimeError, match="already running"):
        await mgr.start("f.cab", "d2", device_name="N", version="1")


async def test_active_is_false_once_a_job_finishes():
    mgr = manager(FakeCli())
    await mgr.start("f.cab", "d", device_name="N", version="1")
    assert mgr.active is False
    assert mgr.job is not None, "a finished job is retained so the result stays readable"


async def test_dismiss_clears_the_finished_job():
    mgr = manager(FakeCli())
    await mgr.start("f.cab", "d", device_name="N", version="1")
    mgr.dismiss()
    assert mgr.job is None


async def test_operation_flags_reach_the_cli():
    cli = FakeCli()
    await manager(cli).start(
        "f.cab", "d", device_name="N", version="1", allow_older=True, allow_reinstall=True
    )
    assert cli.calls == [("f.cab", "d", True, True)]
```

- [ ] **Step 2: Run and verify they fail**

Run: `uv run pytest tests/test_flash.py -v`
Expected: FAIL — no module named `fwupd_webui.fwupd.flash`.

- [ ] **Step 3: Implement**

Create `src/fwupd_webui/fwupd/flash.py`:

```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum

from fwupd_webui.fwupd.cli import FwupdCommandFailed, FwupdError, ProgressLine

log = logging.getLogger(__name__)

LOG_TAIL_LINES = 50


class FlashStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class FlashJob:
    device_id: str
    device_name: str
    version: str
    status: FlashStatus = FlashStatus.PENDING
    phase: str = ""
    percent: float = 0.0
    exit_code: int | None = None
    error: str | None = None
    log: list[str] = field(default_factory=list)
    # Filled in by the service after a successful flash, from one
    # re-enumeration. None means "not determined".
    installed_version: str | None = None
    staged: bool | None = None

    @property
    def finished(self) -> bool:
        return self.status in (FlashStatus.SUCCEEDED, FlashStatus.FAILED)


class FlashManager:
    """Owns at most one flash job, globally.

    There is deliberately no cancel: killing fwupdtool between Writing and
    Verifying is a way to leave partially written firmware on a device. A job
    runs to completion or fails on its own.

    A finished job is retained so the result stays readable after a reconnect,
    and is replaced only when the next flash starts.
    """

    def __init__(self, cli):
        self._cli = cli
        self.job: FlashJob | None = None

    @property
    def active(self) -> bool:
        return self.job is not None and not self.job.finished

    def dismiss(self) -> None:
        if self.active:
            raise RuntimeError("cannot dismiss a running job")
        self.job = None

    async def start(
        self,
        target: str,
        device_id: str,
        *,
        device_name: str,
        version: str,
        allow_older: bool = False,
        allow_reinstall: bool = False,
    ) -> FlashJob:
        if self.active:
            raise RuntimeError("a flash is already running")

        job = FlashJob(device_id=device_id, device_name=device_name, version=version)
        self.job = job

        def on_progress(progress: ProgressLine) -> None:
            job.phase = progress.phase
            job.percent = progress.percent

        def on_log(line: str) -> None:
            job.log.append(line)
            del job.log[:-LOG_TAIL_LINES]

        job.status = FlashStatus.RUNNING
        try:
            await asyncio.to_thread(
                self._cli.install,
                target,
                device_id,
                allow_older=allow_older,
                allow_reinstall=allow_reinstall,
                on_progress=on_progress,
                on_log=on_log,
            )
        except FwupdCommandFailed as exc:
            job.status = FlashStatus.FAILED
            job.exit_code = exc.exit_code
            job.error = exc.stderr or str(exc)
            log.warning("flash of %s failed during %s: %s", device_id, job.phase, exc)
            return job
        except FwupdError as exc:
            job.status = FlashStatus.FAILED
            job.error = str(exc)
            log.warning("flash of %s failed during %s: %s", device_id, job.phase, exc)
            return job

        job.status = FlashStatus.SUCCEEDED
        log.info("flash of %s completed", device_id)
        return job
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_flash.py -v && make lint`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fwupd_webui/fwupd/flash.py tests/test_flash.py
git commit -m "feat: add the single-flight flash job

FlashManager owns at most one FlashJob globally. A finished job is retained
so its result stays readable after a reconnect, and is replaced only when
the next flash starts.

There is deliberately no cancel path: killing fwupdtool between Writing and
Verifying can leave partially written firmware. A job runs to completion or
fails on its own.

On failure the job records the exit code and the phase it reached --
failing during Verifying means something very different from failing
during Loading."
```

---

### Task 6: Service wiring

**Files:**
- Modify: `src/fwupd_webui/fwupd/service.py`
- Modify: `src/fwupd_webui/__main__.py`
- Test: `tests/test_service_flash.py`

**Interfaces:**
- Consumes: `FlashManager`, `FlashJob` from Task 5; `evaluate`, `check_override` from Task 2.
- Produces: `FwupdService.flash_manager`, `FwupdService.flashing_enabled: bool`,
  `FwupdService.start_flash(device_id, version, *, operation, typed_name) -> FlashJob`,
  and `FwupdService.permission_for(device) -> Permission`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_service_flash.py`:

```python
import pytest

from fwupd_webui.config import Config
from fwupd_webui.fwupd.flash import FlashStatus
from fwupd_webui.fwupd.service import FwupdService


class StubCli:
    def __init__(self, devices):
        self._devices = devices
        self.installs = []

    def get_devices(self):
        return self._devices

    def get_updates(self):
        return self._devices

    def version(self):
        return "2.1.7"

    def install(self, target, device_id, *, allow_older=False, allow_reinstall=False,
                on_progress=None, on_log=None):
        self.installs.append((target, device_id, allow_older, allow_reinstall))


def make_device(plugin="nvme", name="NVMe SSD", flags=("updatable",)):
    from fwupd_webui.fwupd.models import Device

    return Device.model_validate(
        {
            "DeviceId": "dev-1",
            "Name": name,
            "Plugin": plugin,
            "Flags": list(flags),
            "Releases": [{"Version": "2.0", "Uri": "https://lvfs/f.cab"}],
        }
    )


def service(tmp_path, *, enabled=True, plugin="nvme"):
    cli = StubCli([make_device(plugin=plugin)])
    config = Config.from_env(
        {"FWUPD_WEBUI_ENABLE_FLASHING": "true" if enabled else "false"}
    )
    svc = FwupdService(cli, config, tmp_path)
    svc._stub_cli = cli
    return svc


async def test_flashing_an_allowlisted_device_succeeds(tmp_path):
    svc = service(tmp_path)
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)

    assert job.status is FlashStatus.SUCCEEDED
    assert svc._stub_cli.installs == [("https://lvfs/f.cab", "dev-1", False, False)]


async def test_flashing_is_refused_when_disabled(tmp_path):
    svc = service(tmp_path, enabled=False)
    with pytest.raises(PermissionError, match="FWUPD_WEBUI_ENABLE_FLASHING"):
        await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)


async def test_blocked_plugin_is_refused_without_the_typed_name(tmp_path):
    svc = service(tmp_path, plugin="ata")
    with pytest.raises(PermissionError, match="type the device name"):
        await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)


async def test_blocked_plugin_proceeds_with_the_correct_typed_name(tmp_path):
    svc = service(tmp_path, plugin="ata")
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name="NVMe SSD")
    assert job.status is FlashStatus.SUCCEEDED


async def test_blocked_plugin_is_refused_on_a_near_miss(tmp_path):
    svc = service(tmp_path, plugin="ata")
    with pytest.raises(PermissionError):
        await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name="NVMe")


async def test_downgrade_sets_allow_older(tmp_path):
    svc = service(tmp_path)
    await svc.start_flash("dev-1", "2.0", operation="downgrade", typed_name=None)
    assert svc._stub_cli.installs[0][2] is True


async def test_reinstall_sets_allow_reinstall(tmp_path):
    svc = service(tmp_path)
    await svc.start_flash("dev-1", "2.0", operation="reinstall", typed_name=None)
    assert svc._stub_cli.installs[0][3] is True


async def test_unknown_device_is_rejected(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(LookupError):
        await svc.start_flash("nope", "2.0", operation="upgrade", typed_name=None)


async def test_unknown_version_is_rejected(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(LookupError, match="9.9"):
        await svc.start_flash("dev-1", "9.9", operation="upgrade", typed_name=None)


async def test_staged_firmware_is_detected(tmp_path):
    """The stub device still reports its old version after the flash, which is
    what every needs-reboot device on the deployed host does."""
    svc = service(tmp_path)
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)
    assert job.staged is True
    assert job.installed_version is None or job.installed_version != "2.0"


async def test_a_live_update_is_reported_as_live(tmp_path):
    svc = service(tmp_path)
    cli = svc._stub_cli

    original = cli.install

    def install_then_bump(*args, **kwargs):
        original(*args, **kwargs)
        cli._devices = [make_device()]
        cli._devices[0].version = "2.0"

    cli.install = install_then_bump
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)
    assert job.staged is False
    assert job.installed_version == "2.0"


async def test_a_failed_re_enumeration_does_not_fail_the_flash(tmp_path):
    from fwupd_webui.fwupd.cli import FwupdCommandFailed
    from fwupd_webui.fwupd.flash import FlashStatus

    svc = service(tmp_path)

    def boom():
        raise FwupdCommandFailed(1, "enumeration broke")

    svc._stub_cli.get_devices_after_flash = boom
    job = await svc.start_flash("dev-1", "2.0", operation="upgrade", typed_name=None)
    assert job.status is FlashStatus.SUCCEEDED
```

The last test needs `StubCli.get_devices` to fail only on the post-flash call. Add to `StubCli`:

```python
    def __init__(self, devices):
        self._devices = devices
        self.installs = []
        self.get_devices_after_flash = None

    def get_devices(self):
        if self.installs and self.get_devices_after_flash is not None:
            self.get_devices_after_flash()
        return self._devices
```

- [ ] **Step 2: Run and verify they fail**

Run: `uv run pytest tests/test_service_flash.py -v`
Expected: FAIL — `FwupdService` has no attribute `start_flash`.

- [ ] **Step 3: Implement**

In `src/fwupd_webui/fwupd/service.py`, add imports:

```python
from fwupd_webui.fwupd.flash import FlashJob, FlashManager
from fwupd_webui.fwupd.policy import Permission, check_override, evaluate
```

In `__init__`, after `self._lock = asyncio.Lock()`:

```python
        self.flash_manager = FlashManager(cli)
```

Add these methods:

```python
    @property
    def flashing_enabled(self) -> bool:
        return self._config.enable_flashing

    def permission_for(self, device) -> Permission:
        return evaluate(device, enabled=self._config.enable_flashing)

    async def start_flash(
        self,
        device_id: str,
        version: str,
        *,
        operation: str,
        typed_name: str | None,
    ) -> FlashJob:
        """Validate policy, then run the flash under the hardware lock.

        Raises PermissionError when policy refuses, LookupError when the device
        or release does not exist.
        """
        devices = await self._call(self._cli.get_devices)
        device = next((d for d in devices if d.device_id == device_id), None)
        if device is None:
            raise LookupError(f"no such device: {device_id}")

        permission = self.permission_for(device)
        if not permission.allowed:
            if not permission.needs_override:
                raise PermissionError(permission.reason)
            if not check_override(device, typed_name):
                raise PermissionError(
                    f"{permission.reason} Expected exactly: {device.display_name}"
                )

        release = next((r for r in device.releases if r.version == version), None)
        if release is None:
            raise LookupError(f"device {device_id} has no release {version}")
        if not release.uri:
            raise LookupError(f"release {version} has no download URI")

        async with self._lock:
            job = await self.flash_manager.start(
                release.uri,
                device_id,
                device_name=device.display_name,
                version=version,
                allow_older=operation == "downgrade",
                allow_reinstall=operation == "reinstall",
            )
            if job.status is FlashStatus.SUCCEEDED:
                await self._record_outcome(job, device_id, version)
            return job

    async def _record_outcome(self, job: FlashJob, device_id: str, version: str) -> None:
        """Re-enumerate once so the result view can say whether the firmware is
        live or merely staged.

        Uses asyncio.to_thread directly rather than self._call: we already hold
        the lock, and self._call would try to acquire it again and deadlock.

        Best-effort -- a failure here must not turn a successful flash into a
        reported failure.
        """
        try:
            devices = await asyncio.to_thread(self._cli.get_devices)
        except FwupdError:
            log.warning("post-flash re-enumeration failed", exc_info=True)
            return
        fresh = next((d for d in devices if d.device_id == device_id), None)
        if fresh is None:
            return
        job.installed_version = fresh.version
        job.staged = fresh.version != version
```

Add `FlashStatus` to the `flash` import line in `service.py`.

**Two things here are easy to get wrong.** `flash_manager.start` already uses `asyncio.to_thread`
internally, so holding the lock around it serializes against enumeration without double-threading.
And `_record_outcome` must use `asyncio.to_thread` directly — calling `self._call` would attempt to
re-acquire the lock we are already holding and deadlock.

In `src/fwupd_webui/__main__.py`, pass the install timeout when constructing the CLI:

```python
    cli = FwupdCli(timeout=config.timeout_seconds, install_timeout=config.install_timeout_seconds)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_service_flash.py -v && make lint`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fwupd_webui/fwupd/service.py src/fwupd_webui/__main__.py tests/test_service_flash.py
git commit -m "feat: wire flashing into the service layer

start_flash validates policy before doing anything: refuse when flashing is
disabled, refuse a non-allowlisted plugin unless the device name was typed
exactly, then resolve the release URI and run under the hardware lock.

Policy is enforced here rather than in the route, so it holds regardless of
what the HTML offered. A disabled button is a suggestion; this is the
control."
```

---

### Task 7: Flash routes, registered only when enabled

**Files:**
- Create: `src/fwupd_webui/web/flash_routes.py`
- Modify: `src/fwupd_webui/web/app.py`
- Create: `src/fwupd_webui/web/templates/flash_progress.html`
- Create: `src/fwupd_webui/web/templates/_flash_progress.html`
- Test: `tests/test_web_flash.py`

**Interfaces:**
- Consumes: `FwupdService.start_flash`, `flash_manager`, `flashing_enabled`.
- Produces: `flash_router` (an `APIRouter`), registered by `create_app` only when
  `config.enable_flashing` is true.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_flash.py`:

```python
import pytest
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


def test_flash_routes_do_not_exist_when_disabled():
    """Not registered at all -- a real 404, not a handler returning 403. A
    capability that does not exist cannot be reached by a bug above it."""
    client = client_for(FakeService(enabled=False), enabled=False)
    assert client.post("/flash", data={"device_id": "d", "version": "1"}).status_code == 404
    assert client.get("/flash").status_code == 404


def test_flash_routes_exist_when_enabled():
    client = client_for(FakeService())
    assert client.get("/flash").status_code in (200, 303)


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
    running = FlashJob(device_id="dev-1", device_name="NVMe SSD", version="2.0")
    running.status = FlashStatus.RUNNING
    client = client_for(FakeService(job=running))

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/flash"


def test_progress_fragment_reports_phase_and_percent():
    running = FlashJob(device_id="dev-1", device_name="NVMe SSD", version="2.0")
    running.status = FlashStatus.RUNNING
    running.phase = "Writing"
    running.percent = 42.5
    client = client_for(FakeService(job=running))

    body = client.get("/flash/progress").text
    assert "Writing" in body
    assert "42.5" in body


def test_progress_fragment_polls_itself_while_running():
    running = FlashJob(device_id="dev-1", device_name="NVMe SSD", version="2.0")
    running.status = FlashStatus.RUNNING
    body = client_for(FakeService(job=running)).get("/flash/progress").text
    assert "every 1s" in body


def test_progress_fragment_stops_polling_once_finished():
    done = FlashJob(device_id="dev-1", device_name="NVMe SSD", version="2.0")
    done.status = FlashStatus.SUCCEEDED
    body = client_for(FakeService(job=done)).get("/flash/progress").text
    assert "every 1s" not in body


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
```

- [ ] **Step 2: Run and verify they fail**

Run: `uv run pytest tests/test_web_flash.py -v`
Expected: FAIL — cannot import `flash_router`, and `/flash` 404s.

- [ ] **Step 3: Implement the routes**

Create `src/fwupd_webui/web/flash_routes.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

flash_router = APIRouter()


@flash_router.post("/flash")
async def start_flash(
    request: Request,
    device_id: str = Form(...),
    version: str = Form(...),
    operation: str = Form("upgrade"),
    confirm_name: str | None = Form(None),
):
    service = request.app.state.service
    templates = request.app.state.templates
    try:
        await service.start_flash(
            device_id, version, operation=operation, typed_name=confirm_name
        )
    except (PermissionError, LookupError, RuntimeError) as exc:
        # A refusal is an expected outcome, not a server error.
        return templates.TemplateResponse(
            request=request, name="_banner.html", context={"message": str(exc)}
        )
    return RedirectResponse("/flash", status_code=303)


@flash_router.get("/flash", response_class=HTMLResponse)
async def flash_page(request: Request):
    service = request.app.state.service
    templates = request.app.state.templates
    if service.flash_manager.job is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="flash_progress.html",
        context={"job": service.flash_manager.job, "inventory": await service.inventory()},
    )


@flash_router.get("/flash/progress", response_class=HTMLResponse)
async def flash_progress(request: Request):
    service = request.app.state.service
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_flash_progress.html",
        context={"job": service.flash_manager.job},
    )


@flash_router.post("/flash/dismiss")
async def dismiss(request: Request):
    request.app.state.service.flash_manager.dismiss()
    return RedirectResponse("/", status_code=303)
```

- [ ] **Step 4: Guard the existing routes and register conditionally**

In `src/fwupd_webui/web/routes.py`, add at the top of `index`, `device_detail` and `refresh`, right
after the `service = request.app.state.service` line:

```python
    manager = getattr(service, "flash_manager", None)
    if manager is not None and manager.active:
        # Checked before touching the lock. Awaiting it first would make every
        # page hang for the duration of the flash.
        return RedirectResponse("/flash", status_code=303)
```

Add `from fastapi.responses import RedirectResponse` to that file's imports.

In `src/fwupd_webui/web/app.py`, inside `create_app` after `app.include_router(router)`:

```python
    if config.enable_flashing:
        # Registered only when explicitly enabled, so POST /flash is a genuine
        # 404 otherwise rather than a handler that decided to refuse.
        from fwupd_webui.web.flash_routes import flash_router

        app.include_router(flash_router)
```

- [ ] **Step 5: Create the templates**

`src/fwupd_webui/web/templates/_flash_progress.html`:

```html
<div id="flash-progress"
     {% if not job.finished %}hx-get="/flash/progress" hx-trigger="every 1s" hx-swap="outerHTML"{% endif %}>
  {% if job.status.value == "running" or job.status.value == "pending" %}
    <p class="phase"><strong>{{ job.phase or "Starting" }}</strong> {{ job.percent }}%</p>
    <p class="muted">Do not power off the machine.</p>
  {% elif job.status.value == "succeeded" %}
    <p class="ok">Flashed {{ job.device_name }} to {{ job.version }}.</p>
    {% if job.staged %}
    <p class="warn">
      The device still reports version <code>{{ job.installed_version or "unknown" }}</code>,
      so the new firmware is <strong>staged but not yet live</strong>. It takes effect
      after a reboot. This tool never reboots the host — do it from the Unraid UI,
      stopping the array first.
    </p>
    {% elif job.staged is sameas false %}
    <p class="muted">The device now reports {{ job.installed_version }}. The update is live.</p>
    {% else %}
    <p class="muted">
      Could not re-read the device to confirm whether the firmware is live or staged.
    </p>
    {% endif %}
  {% else %}
    <p class="error">
      Flash failed during <strong>{{ job.phase or "startup" }}</strong>
      {% if job.exit_code is not none %}(exit {{ job.exit_code }}){% endif %}.
    </p>
    <pre class="log">{{ job.error }}</pre>
    {% if job.log %}<pre class="log">{{ job.log | join("\n") }}</pre>{% endif %}
  {% endif %}

  {% if job.finished %}
  <form method="post" action="/flash/dismiss">
    <button type="submit">Back to devices</button>
  </form>
  {% endif %}
</div>
```

`src/fwupd_webui/web/templates/flash_progress.html`:

```html
{% extends "base.html" %}
{% block content %}
<h2>Flashing {{ job.device_name }} → {{ job.version }}</h2>
{% include "_flash_progress.html" %}
{% endblock %}
```

Check `base.html` for the actual block name and match it; if it does not use `{% block content %}`,
follow whatever `index.html` does.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_web_flash.py tests/test_web_table.py -v && make lint`
Expected: all pass. `test_web_table.py` must still pass — its `FakeService` has no `flash_manager`,
which is why the guard uses `getattr`.

- [ ] **Step 7: Commit**

```bash
git add src/fwupd_webui/web/ tests/test_web_flash.py
git commit -m "feat: flash routes, registered only when flashing is enabled

The router is attached to the app only when FWUPD_WEBUI_ENABLE_FLASHING is
set, so POST /flash is a genuine 404 otherwise rather than a handler that
decided to refuse.

While a job runs every other page redirects to the progress view. That
check happens before the hardware lock is touched -- awaiting the lock
first would make every page hang for the duration of the flash, which is
exactly the behaviour the design rejects.

The progress fragment polls itself once a second while running and stops
polling once the job is finished. There is no cancel route, asserted by a
test so it is not added later as a missing feature."
```

---

### Task 8: Device table and confirm step

**Files:**
- Modify: `src/fwupd_webui/web/templates/_device_table.html`
- Modify: `src/fwupd_webui/web/templates/_device_detail.html`
- Create: `src/fwupd_webui/web/templates/_flash_confirm.html`
- Modify: `src/fwupd_webui/web/routes.py`
- Modify: `src/fwupd_webui/web/static/app.css`
- Test: `tests/test_web_confirm.py`

**Interfaces:**
- Consumes: `service.permission_for(device)`, `service.flashing_enabled`.
- Produces: `GET /devices/{device_id}/confirm?version=<v>&operation=<op>` rendering
  `_flash_confirm.html`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_confirm.py`, reusing the fakes from `tests/test_web_flash.py`:

```python
from fastapi.testclient import TestClient

from fwupd_webui.config import Config
from fwupd_webui.fwupd.models import Device, Release
from fwupd_webui.fwupd.service import DeviceView, Inventory, MetadataStatus
from fwupd_webui.web.app import create_app

from tests.test_web_flash import FakeService


def service_with(plugin="nvme", name="NVMe SSD", enabled=True):
    class S(FakeService):
        async def inventory(self):
            device = Device.model_validate(
                {
                    "DeviceId": "dev-1",
                    "Name": name,
                    "Plugin": plugin,
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


def test_allowlisted_device_gets_a_live_confirm_button():
    body = client_for(service_with(plugin="nvme")).get(
        "/devices/dev-1/confirm?version=2.0&operation=upgrade"
    ).text
    assert "confirm_name" not in body, "an allowlisted device needs no typed override"
    assert "2.0" in body


def test_blocked_device_requires_typing_the_name():
    body = client_for(service_with(plugin="ata", name="ST4000VN008-2DR166")).get(
        "/devices/dev-1/confirm?version=2.0&operation=upgrade"
    ).text
    assert 'name="confirm_name"' in body
    assert "ST4000VN008-2DR166" in body


def test_confirm_warns_about_needs_reboot():
    body = client_for(service_with()).get(
        "/devices/dev-1/confirm?version=2.0&operation=upgrade"
    ).text
    assert "needs-reboot" in body


def test_device_detail_explains_when_flashing_is_disabled():
    body = client_for(service_with(enabled=False), enabled=False).get("/devices/dev-1").text
    assert "FWUPD_WEBUI_ENABLE_FLASHING" in body


def test_device_detail_offers_no_flash_controls_when_disabled():
    body = client_for(service_with(enabled=False), enabled=False).get("/devices/dev-1").text
    assert "/flash" not in body
```

- [ ] **Step 2: Run and verify they fail**

Run: `uv run pytest tests/test_web_confirm.py -v`
Expected: FAIL — the confirm route does not exist.

- [ ] **Step 3: Add the confirm route**

In `src/fwupd_webui/web/routes.py`:

```python
@router.get("/devices/{device_id}/confirm", response_class=HTMLResponse)
async def flash_confirm(
    request: Request, device_id: str, version: str, operation: str = "upgrade"
) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates
    inventory = await service.inventory()
    for view in inventory.devices:
        if view.device.device_id == device_id:
            return templates.TemplateResponse(
                request=request,
                name="_flash_confirm.html",
                context={
                    "view": view,
                    "version": version,
                    "operation": operation,
                    "permission": service.permission_for(view.device),
                    "inventory": inventory,
                },
            )
    raise HTTPException(status_code=404, detail=f"no such device: {device_id}")
```

- [ ] **Step 4: Create `_flash_confirm.html`**

```html
<div class="confirm">
  <h3>{{ operation | capitalize }} {{ view.device.display_name }}</h3>
  <p>{{ view.device.version or "unknown" }} → <strong>{{ version }}</strong></p>

  {% if "needs-reboot" in view.device.flags %}
  <p class="warn">
    This device reports <code>needs-reboot</code>: the firmware will be staged
    and becomes live only after a reboot. This tool never reboots the host.
  </p>
  {% endif %}
  {% if "require-ac" in view.device.flags %}
  <p class="warn">This device requires AC power for the duration of the update.</p>
  {% endif %}

  {% if permission.allowed or permission.needs_override %}
  <form method="post" action="/flash">
    <input type="hidden" name="device_id" value="{{ view.device.device_id }}">
    <input type="hidden" name="version" value="{{ version }}">
    <input type="hidden" name="operation" value="{{ operation }}">
    {% if permission.needs_override %}
    <p class="warn">{{ permission.reason }}</p>
    <label>
      Type <code>{{ view.device.display_name }}</code> to confirm:
      <input type="text" name="confirm_name" autocomplete="off" required>
    </label>
    {% endif %}
    <button type="submit">Flash firmware</button>
  </form>
  {% else %}
  <p class="muted">{{ permission.reason }}</p>
  {% endif %}
</div>
```

- [ ] **Step 5: Add controls to the detail template**

In `_device_detail.html`, inside the `{% for release in view.available %}` loop, after the existing
`<li>` content:

```html
      {% if inventory.flashing_enabled %}
      <div>
        <button hx-get="/devices/{{ view.device.device_id }}/confirm?version={{ release.version }}&operation=upgrade"
                hx-target="closest li" hx-swap="beforeend">Update</button>
        <button hx-get="/devices/{{ view.device.device_id }}/confirm?version={{ release.version }}&operation=downgrade"
                hx-target="closest li" hx-swap="beforeend">Downgrade</button>
        <button hx-get="/devices/{{ view.device.device_id }}/confirm?version={{ release.version }}&operation=reinstall"
                hx-target="closest li" hx-swap="beforeend">Reinstall</button>
      </div>
      {% endif %}
```

And after the releases block:

```html
  {% if not inventory.flashing_enabled %}
  <p class="muted">
    Flashing is disabled. Set <code>FWUPD_WEBUI_ENABLE_FLASHING=true</code> to enable it.
  </p>
  {% endif %}
```

Add `flashing_enabled: bool = False` to the `Inventory` dataclass in `service.py`, and set it in
`FwupdService.inventory()`:

```python
        return Inventory(
            devices=views,
            metadata=self._status(error=update_error),
            fwupd_version=version,
            flashing_enabled=self._config.enable_flashing,
        )
```

- [ ] **Step 6: Add the CSS**

Append to `src/fwupd_webui/web/static/app.css`:

```css
.warn { color: #b45309; }
.error { color: #b91c1c; }
.ok { color: #15803d; }
.confirm { border: 1px solid #d4d4d8; padding: 0.75rem; margin-top: 0.5rem; }
.phase { font-size: 1.2rem; }
.log { background: #18181b; color: #e4e4e7; padding: 0.5rem; overflow-x: auto; }
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -v && make lint`
Expected: all pass, including every phase C test.

- [ ] **Step 8: Commit**

```bash
git add src/fwupd_webui/web/ src/fwupd_webui/fwupd/service.py tests/test_web_confirm.py
git commit -m "feat: flash controls and confirm step in the device detail

Each release gets Update, Downgrade and Reinstall, all routing through one
confirm fragment. Allowlisted devices confirm in a click; blocked devices
render an input demanding the device name typed exactly.

The confirm step surfaces needs-reboot and require-ac as plain warnings.
When flashing is disabled the detail view says so and names the variable,
so an operator who expected a button learns why there is none."
```

---

### Task 9: Integration test against real fwupd

**Files:**
- Modify: `tests/integration/test_real_fwupdtool.py`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Write the test**

Append to `tests/integration/test_real_fwupdtool.py`:

```python
def test_real_install_reports_the_expected_phases():
    """The test fixtures cannot catch fwupd changing its progress format.
    This can. Flashes the synthetic device inside the image for real."""
    import subprocess

    from fwupd_webui.fwupd.cli import FwupdCli

    subprocess.run(["fwupdtool", "enable-test-devices"], capture_output=True, check=False)

    phases: list[str] = []
    FwupdCli().install(
        "/usr/share/installed-tests/fwupd/fakedevice124.cab",
        "08d460be0f1f9f128413f816022a6439e0078018",
        on_progress=lambda p: phases.append(p.phase),
    )

    assert phases, "install produced no parseable progress at all"
    assert "Writing" in phases, f"no Writing phase in {sorted(set(phases))}"
    assert "Verifying" in phases, f"no Verifying phase in {sorted(set(phases))}"


def test_force_is_never_in_the_install_argv():
    import inspect

    import fwupd_webui.fwupd.cli as cli_module

    assert '"--force"' not in inspect.getsource(cli_module)
```

The device ID is the synthetic webcam's, stable across fwupd releases. If it changes, read it from
`fwupdtool --json get-devices` rather than hardcoding a new one blindly.

- [ ] **Step 2: Run it**

Run: `make integration`
Expected: 7 passed (5 existing + 2 new).

If the phase names differ from `Writing`/`Verifying`, **the real output wins** — update the
assertions and say so in the commit.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_real_fwupdtool.py
git commit -m "test: flash the synthetic device for real in the integration suite

Fixtures cannot catch fwupd renaming or reformatting its progress phases;
this can. It performs an actual install inside the image and asserts the
Writing and Verifying phases are parsed from live output."
```

---

### Task 10: Documentation and the Unraid template

**Files:**
- Modify: `README.md`
- Modify: `unraid/fwupd-webui.xml`
- Modify: `docker-compose.yml`

**Interfaces:** none.

- [ ] **Step 1: Update the README**

Replace the line `**It does not flash firmware.** This release is inventory only.` with:

```markdown
**Flashing is off by default.** Out of the box this is a read-only inventory. Setting
`FWUPD_WEBUI_ENABLE_FLASHING=true` enables the firmware write path; without it the
flash routes are not registered at all.
```

Add both variables to the configuration table:

```markdown
| `FWUPD_WEBUI_ENABLE_FLASHING` | `false` | Enables the firmware write path |
| `FWUPD_WEBUI_INSTALL_TIMEOUT_SECONDS` | `1800` | Hard timeout for a single flash |
```

Add a section after "Why privileged":

```markdown
### Flashing firmware

Disabled unless `FWUPD_WEBUI_ENABLE_FLASHING=true`. When it is off the routes do not
exist — `POST /flash` is a genuine 404, not a handler that refuses.

With it on, four things still stand between a click and a write:

1. Only `nvme` and `thunderbolt` devices flash directly. Everything else, including
   `ata`, requires typing the device name exactly to confirm.
2. Policy is enforced server-side. A disabled button is a suggestion; the server
   refusing the POST is the control.
3. There is no cancel. Killing a flash mid-write can leave partially written
   firmware, so a job runs to completion or fails on its own.
4. `uefi_capsule` remains disabled in the image, so BIOS updates stay unreachable.

**Array drives.** On a NAS the `ata` devices are usually the array. Rewriting drive
firmware under a live array risks the array, not just the drive. Stop the array first.

**Staged firmware.** Most devices report `needs-reboot`, meaning the firmware is
written but becomes live only after a reboot. This tool never reboots the host.
```

- [ ] **Step 2: Update the Unraid template**

In `unraid/fwupd-webui.xml`, add before the closing `</Container>`:

```xml
  <Config Name="Enable flashing"
          Target="FWUPD_WEBUI_ENABLE_FLASHING"
          Default="false"
          Mode=""
          Description="Set to true to allow writing firmware to devices. Leave false for a read-only inventory. Enabling this permits irreversible writes to hardware; array drives require typing the device name to confirm."
          Type="Variable"
          Display="always"
          Required="false"
          Mask="false">false</Config>
```

- [ ] **Step 3: Document the variable in compose**

In `docker-compose.yml`, under the service's `environment:` key (add the key if absent):

```yaml
    environment:
      # Read-only by default. Set to "true" to enable the firmware write path.
      FWUPD_WEBUI_ENABLE_FLASHING: "false"
```

- [ ] **Step 4: Verify the whole thing**

```bash
make test
make lint
make integration
docker build -t fwupd-webui:dev .
docker run --rm -d --name fw-test -p 8098:8099 fwupd-webui:dev
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8098/flash
docker rm -f fw-test
```

Expected: tests and lint pass, and the `curl` prints **404** — the shipped image has no flash route.

- [ ] **Step 5: Commit**

```bash
git add README.md unraid/fwupd-webui.xml docker-compose.yml
git commit -m "docs: document flashing and expose the enable flag on Unraid

The Community Applications template shows FWUPD_WEBUI_ENABLE_FLASHING with
a description stating plainly that enabling it permits irreversible writes.

README documents all four controls, the array-drive hazard specifically,
and that most devices stage firmware rather than applying it immediately."
```

---

## Verification

After Task 10 the following must all hold:

- `make test` — every unit test, phase C and phase A.
- `make lint` — ruff check and format.
- `make integration` — 7 tests against real `fwupdtool`, including a real flash.
- A container built with no environment variables returns **404** for `POST /flash`.
- A container built with `FWUPD_WEBUI_ENABLE_FLASHING=true` serves the confirm step, and an `ata`
  device refuses to flash without the typed name.

## Notes for whoever implements this

**Real output beats the plan.** Phase C's plan was wrong about `fwupdtool` four separate times, and
each time the captured output was right. If a fixture or a live run disagrees with anything written
here, the fixture wins — fix the plan's assumption and say so in the commit.

**Two commits are deliberately small.** Retiring the read-only guard (Task 4, Step 2) is its own
commit so that dropping a safety assertion is visible in history. Do not fold it into the feature.

**The lock ordering in Task 6 matters.** `flash_manager.start` already uses `asyncio.to_thread`;
`start_flash` holds the service lock around it. Do not add a second thread hop, and do not move the
`manager.active` check in the routes to after the lock acquisition.
