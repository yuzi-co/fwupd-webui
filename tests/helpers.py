"""A logged-in client, for tests about something other than the login."""

from fastapi.testclient import TestClient as _TestClient

from fwupd_webui.web.app import create_app as _create_app

PASSWORD = "test-password"


def create_app(service, config):
    return _create_app(service, config, password=PASSWORD)


class TestClient(_TestClient):
    __test__ = False

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        response = self.post("/login", data={"password": PASSWORD}, follow_redirects=False)
        assert response.status_code == 303
