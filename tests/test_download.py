import hashlib
import io
import os
import stat

import pytest

from fwupd_webui.fwupd import download as download_module
from fwupd_webui.fwupd.download import (
    FirmwareDownloadError,
    _HttpsOnlyRedirects,
    download_verified,
)

URL = "https://fwupd.org/downloads/f.cab"
BODY = b"firmware bytes"
SHA = hashlib.sha256(BODY).hexdigest()


@pytest.fixture
def serve(monkeypatch):
    def install(body=BODY):
        monkeypatch.setattr(
            download_module._opener, "open", lambda request, timeout: io.BytesIO(body)
        )

    return install


def test_a_matching_file_is_returned(tmp_path, serve):
    serve()
    path = download_verified(URL, SHA, tmp_path)
    assert path.read_bytes() == BODY


def test_an_uppercase_checksum_still_matches(tmp_path, serve):
    serve()
    assert download_verified(URL, SHA.upper(), tmp_path).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_the_file_is_private(tmp_path, serve):
    serve()
    path = download_verified(URL, SHA, tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_mismatch_raises_and_leaves_no_file(tmp_path, serve):
    serve(b"tampered")
    with pytest.raises(FirmwareDownloadError, match="checksum mismatch"):
        download_verified(URL, SHA, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "url", ["http://fwupd.org/f.cab", "file:///etc/passwd", "ftp://fwupd.org/f.cab", "./f.cab"]
)
def test_only_https_is_fetched(tmp_path, monkeypatch, url):
    monkeypatch.setattr(download_module._opener, "open", lambda *a, **k: pytest.fail("fetched"))
    with pytest.raises(FirmwareDownloadError, match="non-HTTPS"):
        download_verified(url, SHA, tmp_path)


def test_a_redirect_off_https_is_refused():
    with pytest.raises(FirmwareDownloadError, match="non-HTTPS"):
        _HttpsOnlyRedirects().redirect_request(None, None, 302, "", {}, "http://evil/f.cab")


def test_a_network_failure_is_reported_as_a_download_error(tmp_path, monkeypatch):
    def refuse(request, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(download_module._opener, "open", refuse)
    with pytest.raises(FirmwareDownloadError, match="connection refused"):
        download_verified(URL, SHA, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_oversized_download_stops_and_leaves_no_file(tmp_path, serve, monkeypatch):
    monkeypatch.setattr(download_module, "_MAX_BYTES", 4)
    serve()
    with pytest.raises(FirmwareDownloadError, match="exceeded"):
        download_verified(URL, SHA, tmp_path)
    assert list(tmp_path.iterdir()) == []
