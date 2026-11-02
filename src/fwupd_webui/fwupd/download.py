from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from fwupd_webui.fwupd.cli import FwupdError

log = logging.getLogger(__name__)

_CHUNK_BYTES = 1024 * 1024
_SOCKET_TIMEOUT_SECONDS = 60
_TOTAL_TIMEOUT_SECONDS = 900
_MAX_BYTES = 1024 * 1024 * 1024
_FILENAME = "firmware.cab"
_USER_AGENT = "fwupd-webui"


class FirmwareDownloadError(FwupdError):
    """The firmware file could not be fetched, or was not the file expected."""


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """LVFS redirects to its storage host, so redirects have to work, but one
    must not move the request off HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise FirmwareDownloadError(f"refusing redirect to a non-HTTPS URL: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnlyRedirects)


def download_verified(url: str, sha256: str, dest_dir: Path) -> Path:
    """Download `url` into `dest_dir` and return its path once its hash matches.

    `sha256` comes from fwupd's signed metadata. It is what ties this file to the
    release the user confirmed: `fwupdtool install` does not check a file against
    it, and it installed an unsigned cab on a test device, so nothing later in the
    flash would catch the wrong file.

    A file that fails any check is deleted before this raises.
    """
    if urlsplit(url).scheme != "https":
        raise FirmwareDownloadError(f"refusing to download from a non-HTTPS URL: {url}")

    path = Path(dest_dir) / _FILENAME
    digest = hashlib.sha256()
    deadline = time.monotonic() + _TOTAL_TIMEOUT_SECONDS
    received = 0
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    log.info("downloading %s", url)
    try:
        with (
            _opener.open(request, timeout=_SOCKET_TIMEOUT_SECONDS) as response,
            os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out,
        ):
            while chunk := response.read(_CHUNK_BYTES):
                received += len(chunk)
                if received > _MAX_BYTES:
                    raise FirmwareDownloadError(f"download exceeded {_MAX_BYTES} bytes: {url}")
                if time.monotonic() > deadline:
                    raise FirmwareDownloadError(
                        f"download took longer than {_TOTAL_TIMEOUT_SECONDS}s: {url}"
                    )
                digest.update(chunk)
                out.write(chunk)
        actual = digest.hexdigest()
        if actual != sha256.lower():
            raise FirmwareDownloadError(
                f"checksum mismatch for {url}: expected {sha256.lower()}, got {actual}"
            )
    except FirmwareDownloadError:
        path.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        path.unlink(missing_ok=True)
        raise FirmwareDownloadError(f"could not download {url}: {exc}") from exc
    return path
