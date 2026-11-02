import logging
import os
import stat

import pytest
from fastapi.testclient import TestClient

from fwupd_webui.config import Config
from fwupd_webui.fwupd.flash import FlashJob, FlashStatus
from fwupd_webui.fwupd.models import Device
from fwupd_webui.fwupd.service import DeviceView, Inventory, MetadataStatus
from fwupd_webui.web.app import create_app
from fwupd_webui.web.auth import (
    LOCKOUT_SECONDS,
    MAX_FAILURES,
    PASSWORD_FILENAME,
    SESSION_COOKIE,
    SESSION_SECONDS,
    LoginThrottle,
    SessionStore,
    load_password,
)

PASSWORD = "correct horse"


class Manager:
    job = None
    active = False


class Service:
    def __init__(self):
        self.flash_manager = Manager()
        self.flashing_enabled = True
        self.started = []

    def _inventory(self):
        device = Device.model_validate({"DeviceId": "dev-1", "Name": "Receiver", "Plugin": "x"})
        return Inventory(
            devices=[DeviceView(device=device)],
            metadata=MetadataStatus(last_refresh=1.0, age_seconds=60.0, stale=False),
            fwupd_version="2.1.7",
            flashing_enabled=True,
        )

    async def inventory(self):
        return self._inventory()

    async def cached_inventory(self, max_age_seconds=None):
        return self._inventory()

    async def refresh(self):
        return self._inventory().metadata

    async def start_flash(self, *args, **kwargs):
        self.started.append(args)
        job = FlashJob(device_id="dev-1", device_name="Receiver", version="2.0")
        job.status = FlashStatus.SUCCEEDED
        self.flash_manager.job = job
        return job

    def permission_for(self, device):
        raise AssertionError("not reached by these tests")


@pytest.fixture
def service():
    return Service()


@pytest.fixture
def client(service):
    config = Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": "true"})
    return TestClient(create_app(service, config, password=PASSWORD), follow_redirects=False)


def log_in(client, password=PASSWORD):
    return client.post("/login", data={"password": password})


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/"),
        ("get", "/devices/dev-1"),
        ("get", "/devices/dev-1/confirm?version=2.0"),
        ("post", "/refresh"),
        ("get", "/flash"),
        ("post", "/flash"),
        ("get", "/flash/progress"),
        ("post", "/flash/dismiss"),
        ("post", "/logout"),
    ],
)
def test_every_page_sends_a_stranger_to_the_login(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_a_stranger_cannot_start_a_flash(client, service):
    client.post(
        "/flash",
        data={"device_id": "dev-1", "version": "2.0", "confirm_name": "Receiver"},
    )
    assert service.started == []


def test_the_api_refuses_a_stranger_without_redirecting(client):
    resp = client.get("/api/status")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert "devices" not in resp.json()


def test_htmx_is_told_to_go_to_the_login(client):
    resp = client.get("/flash/progress", headers={"HX-Request": "true"})
    assert resp.status_code == 401
    assert resp.headers["hx-redirect"] == "/login"


def test_the_login_page_and_static_files_are_open(client):
    assert client.get("/login").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_a_path_through_static_does_not_reach_a_protected_route(client, service):
    resp = client.post(
        "/static/../flash",
        data={"device_id": "dev-1", "version": "2.0", "confirm_name": "Receiver"},
    )
    assert service.started == []
    assert resp.status_code != 200


def test_the_right_password_logs_in(client):
    resp = log_in(client)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_the_cookie_cannot_be_read_by_scripts_or_sent_cross_site(client):
    cookie = log_in(client).headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert f"max-age={SESSION_SECONDS}" in cookie
    assert "secure" not in cookie, "plain HTTP would never send the cookie back"


def test_the_cookie_is_secure_behind_a_tls_proxy(client):
    resp = client.post(
        "/login", data={"password": PASSWORD}, headers={"X-Forwarded-Proto": "https"}
    )
    assert "secure" in resp.headers["set-cookie"].lower()


@pytest.mark.parametrize("password", ["wrong", "", "correct horse "])
def test_a_wrong_password_is_refused(client, password):
    resp = log_in(client, password)
    assert resp.status_code == 401
    assert "Wrong password" in resp.text
    assert SESSION_COOKIE not in resp.headers.get("set-cookie", "")
    assert client.get("/").status_code == 303


def test_a_made_up_session_is_refused(client):
    client.cookies.set(SESSION_COOKIE, "made-up")
    assert client.get("/").status_code == 303


def test_a_logged_in_visit_to_the_login_page_goes_home(client):
    log_in(client)
    assert client.get("/login").headers["location"] == "/"


def test_logging_out_ends_the_session_on_the_server(client):
    log_in(client)
    token = client.cookies[SESSION_COOKIE]
    assert client.post("/logout").headers["location"] == "/login"

    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/").status_code == 303


def test_pages_offer_a_way_to_log_out(client):
    log_in(client)
    assert 'action="/logout"' in client.get("/").text


def test_repeated_wrong_passwords_lock_out_even_the_right_one(client):
    for _ in range(MAX_FAILURES):
        assert log_in(client, "wrong").status_code == 401

    resp = log_in(client)
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == str(LOCKOUT_SECONDS)
    assert client.get("/").status_code == 303


def test_a_right_password_clears_earlier_failures(client):
    for _ in range(MAX_FAILURES - 1):
        log_in(client, "wrong")
    log_in(client)
    client.cookies.clear()
    assert log_in(client, "wrong").status_code == 401


def test_the_password_works_as_a_bearer_token_on_the_api(client):
    resp = client.get("/api/status", headers={"Authorization": f"Bearer {PASSWORD}"})
    assert resp.status_code == 200
    assert resp.json()["devices"]["total"] == 1


def test_a_wrong_bearer_token_is_refused(client):
    resp = client.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_bearer_guesses_are_locked_out_too(client):
    for _ in range(MAX_FAILURES):
        client.get("/api/status", headers={"Authorization": "Bearer wrong"})
    resp = client.get("/api/status", headers={"Authorization": f"Bearer {PASSWORD}"})
    assert resp.status_code == 429


def test_a_bearer_token_does_not_open_the_pages(client):
    resp = client.get("/", headers={"Authorization": f"Bearer {PASSWORD}"})
    assert resp.status_code == 303


def test_a_logged_in_session_can_read_the_api(client):
    log_in(client)
    assert client.get("/api/status").status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "http://testserver:9000", "null"])
def test_cross_site_posts_are_refused_even_when_logged_in(client, service, origin):
    log_in(client)
    resp = client.post(
        "/flash",
        data={"device_id": "dev-1", "version": "2.0", "confirm_name": "Receiver"},
        headers={"Origin": origin},
    )
    assert resp.status_code == 403
    assert service.started == []


def test_a_cross_site_login_is_refused(client):
    resp = client.post(
        "/login", data={"password": PASSWORD}, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403


def test_a_same_origin_post_is_allowed(client, service):
    log_in(client)
    resp = client.post(
        "/flash",
        data={"device_id": "dev-1", "version": "2.0", "confirm_name": "Receiver"},
        headers={"Origin": "http://testserver"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/flash"
    assert len(service.started) == 1


def test_create_app_cannot_be_built_without_a_password(service):
    with pytest.raises(TypeError):
        create_app(service, Config.from_env({}))


@pytest.mark.parametrize("empty", [None, ""])
def test_no_configured_password_generates_one_and_saves_it(tmp_path, caplog, empty):
    with caplog.at_level(logging.WARNING):
        password = load_password(empty, tmp_path)

    assert len(password) >= 20
    assert (tmp_path / PASSWORD_FILENAME).read_text() == password
    assert password in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_the_generated_password_is_readable_only_by_root(tmp_path):
    load_password(None, tmp_path)
    mode = stat.S_IMODE((tmp_path / PASSWORD_FILENAME).stat().st_mode)
    assert mode == 0o600


def test_the_saved_password_survives_a_restart_and_is_not_logged_again(tmp_path, caplog):
    first = load_password(None, tmp_path)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert load_password(None, tmp_path) == first
    assert first not in caplog.text


def test_an_empty_saved_file_does_not_mean_an_empty_password(tmp_path):
    (tmp_path / PASSWORD_FILENAME).write_text("")
    assert load_password(None, tmp_path)


def test_a_configured_password_wins_and_is_not_logged(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        assert load_password("mine", tmp_path) == "mine"
    assert "mine" not in caplog.text
    assert not (tmp_path / PASSWORD_FILENAME).exists()


def test_an_unwritable_state_dir_still_gives_a_password(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert load_password(None, blocker / "state")


def test_the_password_is_not_in_the_config_repr():
    config = Config.from_env({"FWUPD_WEBUI_PASSWORD": "secret-value"})
    assert config.password == "secret-value"
    assert "secret-value" not in repr(config)


def test_an_empty_password_setting_counts_as_unset():
    assert Config.from_env({"FWUPD_WEBUI_PASSWORD": ""}).password is None


def test_a_session_expires():
    now = [0.0]
    sessions = SessionStore(clock=lambda: now[0])
    token = sessions.create()
    assert sessions.valid(token)
    now[0] = SESSION_SECONDS + 1
    assert not sessions.valid(token)


def test_a_lockout_ends():
    now = [0.0]
    throttle = LoginThrottle(clock=lambda: now[0])
    for _ in range(MAX_FAILURES):
        throttle.record_failure("a")
    assert throttle.blocked("a")
    assert not throttle.blocked("b"), "one address must not lock out another"
    now[0] = LOCKOUT_SECONDS + 1
    assert not throttle.blocked("a")
