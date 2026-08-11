from __future__ import annotations

import json
import logging
import subprocess

from fwupd_webui.fwupd.models import Device, parse_devices

log = logging.getLogger(__name__)

_STDERR_TAIL_CHARS = 2000
_RAW_SNIPPET_CHARS = 500

# FwupdError code 9, NOTHING_TO_DO. `refresh` reports it with a non-zero exit
# when the cached metadata is already current, which is a success condition.
FWUPD_ERROR_NOTHING_TO_DO = 9

_FWUPD_APPSTREAM_ID = "org.freedesktop.fwupd"


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

    fwupdtool writes JSON to stdout and diagnostics to stderr. Its progress bar
    is drawn only when stderr is a TTY, so output captured through a pipe stays
    clean and needs no filtering.
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
        # fwupd 2.0 exits 0 with an empty Devices array when nothing needs
        # updating, so no special-casing is required here.
        return parse_devices(self._run_json("get-updates"))

    @staticmethod
    def _error_code(stdout: str) -> int | None:
        """Extract the FwupdError code from a JSON error payload, if there is one."""
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        error = payload.get("Error") if isinstance(payload, dict) else None
        return error.get("Code") if isinstance(error, dict) else None

    def refresh(self) -> None:
        # --force skips fwupd's own "metadata is recent enough" short-circuit;
        # the refresh cadence is our policy decision, made in FwupdService.
        code, stdout, stderr = self._run("refresh", "--force")
        if code == 0:
            return
        if self._error_code(stdout) == FWUPD_ERROR_NOTHING_TO_DO:
            log.info("metadata already current")
            return
        raise FwupdCommandFailed(code, stderr)

    def version(self) -> str:
        code, stdout, _ = self._run("--version")
        if code != 0:
            return "unknown"
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return "unknown"
        for entry in payload.get("Versions", []):
            if entry.get("Type") == "runtime" and entry.get("AppstreamId") == _FWUPD_APPSTREAM_ID:
                return str(entry.get("Version", "unknown"))
        return "unknown"
