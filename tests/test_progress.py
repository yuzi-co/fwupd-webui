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


def test_ignores_a_phase_with_no_percentage():
    """fwupd emits a bare 'Loading…' before it starts reporting numbers."""
    assert parse_progress_line("Loading…") is None


def test_ignores_blank_lines():
    assert parse_progress_line("") is None
    assert parse_progress_line("   ") is None


def test_every_phase_in_the_real_capture_is_recognised():
    """Guards against fwupd renaming or reformatting a phase."""
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    parsed = [p for p in (parse_progress_line(line) for line in lines) if p]
    assert len(parsed) > 20, "fixture should contain many progress lines"

    phases = {p.phase for p in parsed}
    assert {"Loading", "Decompressing", "Writing", "Verifying"} <= phases

    assert all(0.0 <= p.percent <= 100.0 for p in parsed)


def test_unrecognised_lines_in_the_real_capture_are_not_progress():
    """Anything the parser rejects must genuinely not be a percentage line, so
    a format change shows up as a test failure rather than silent blankness."""
    lines = [line for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    rejected = [line for line in lines if parse_progress_line(line) is None]
    for line in rejected:
        assert not re.search(r":\s*\d+(\.\d+)?%\s*$", line), f"parser missed: {line!r}"
