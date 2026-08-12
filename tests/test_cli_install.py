import inspect
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

    assert argv_dump.read_text().splitlines() == [
        "install",
        "https://lvfs/f.cab",
        "dev-1",
    ]


def test_install_does_not_pass_json(tmp_path):
    """--json suppresses install progress entirely: 1 stderr line and empty
    stdout, against 77 lines with full phases without it."""
    argv_dump = tmp_path / "argv.txt"
    binary = fake_binary(tmp_path, f'printf "%s\\n" "$@" > {argv_dump}\nexit 0\n')

    FwupdCli(binary=binary).install("f.cab", "dev-1")

    assert "--json" not in argv_dump.read_text()


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
        'echo "WARNING: ESP not found" >&2\necho "Writing…: 50.0%" >&2\nexit 0\n',
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


def test_install_source_never_mentions_force():
    """Structural guard on the write path specifically.

    Scoped to install() rather than the module: refresh() legitimately passes
    --force to bypass fwupd's own metadata-freshness short-circuit, which is a
    read operation and unrelated to relaxing device safety checks.
    """
    source = inspect.getsource(FwupdCli.install)
    assert "--force" not in source
