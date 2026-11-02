"""The login: one operator, one password, every route.

There is no way to run without a password. This app runs privileged and can write
firmware, so a forgotten setting must not leave it open: load_password generates a
password when none is configured.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

log = logging.getLogger(__name__)

PASSWORD_FILENAME = ".webui-password"
SESSION_COOKIE = "fwupd_webui_session"
SESSION_SECONDS = 12 * 3600
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
_MAX_TRACKED_CLIENTS = 10_000
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _create_password_file(path: Path, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(password)


def load_password(configured: str | None, state_dir: Path) -> str:
    """The configured password, else the saved one, else a new one.

    A new password is logged once, because the log is the one place every deployment
    target gives its operator. After that it stays in the root-only file.
    """
    if configured:
        return configured

    path = Path(state_dir) / PASSWORD_FILENAME
    try:
        saved = path.read_text().strip()
    except FileNotFoundError:
        saved = ""
    except OSError:
        log.warning("could not read %s", path, exc_info=True)
        saved = ""
    if saved:
        log.info("using the saved password in %s", path)
        return saved

    password = secrets.token_urlsafe(18)
    try:
        _create_password_file(path, password)
    except OSError:
        log.warning(
            "could not save a password to %s, so it will change on restart", path, exc_info=True
        )
    log.warning(
        "FWUPD_WEBUI_PASSWORD is not set. Generated password: %s (saved in %s)", password, path
    )
    return password


def password_matches(candidate: str, password: str) -> bool:
    return hmac.compare_digest(candidate.encode(), password.encode())


class SessionStore:
    """Sessions held in memory, so a restart logs everyone out."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._expiry: dict[str, float] = {}

    def create(self) -> str:
        now = self._clock()
        self._expiry = {t: e for t, e in self._expiry.items() if e > now}
        token = secrets.token_urlsafe(32)
        self._expiry[token] = now + SESSION_SECONDS
        return token

    def valid(self, token: str | None) -> bool:
        return token is not None and self._expiry.get(token, 0) > self._clock()

    def revoke(self, token: str | None) -> None:
        if token is not None:
            self._expiry.pop(token, None)


class LoginThrottle:
    """Locks an address out for a short time after repeated wrong passwords.

    Per address, so one machine guessing does not lock the operator out. Behind a
    reverse proxy every client shares an address, which only costs a short wait.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def blocked(self, client: str) -> bool:
        until = self._locked_until.get(client)
        if until is None:
            return False
        if until > self._clock():
            return True
        del self._locked_until[client]
        return False

    def record_failure(self, client: str) -> None:
        if len(self._failures) >= _MAX_TRACKED_CLIENTS:
            self._failures.clear()
            self._locked_until.clear()
        failures = self._failures.get(client, 0) + 1
        if failures >= MAX_FAILURES:
            self._failures.pop(client, None)
            self._locked_until[client] = self._clock() + LOCKOUT_SECONDS
        else:
            self._failures[client] = failures

    def record_success(self, client: str) -> None:
        self._failures.pop(client, None)


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_cross_site(request: Request) -> bool:
    """True for a state-changing request sent from another origin.

    A request with no Origin header is allowed: browsers send one on every POST, so
    only non-browser clients lack it, and those still have to log in. SameSite does
    not cover this alone, because it ignores ports, and another app on the same host
    counts as the same site.
    """
    if request.method in _SAFE_METHODS:
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return False
    return urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower()


class AuthMiddleware:
    """Refuses every request that is not logged in.

    Plain ASGI rather than BaseHTTPMiddleware, which runs the app in its own task
    group: a flash is a background task that has to outlive the request starting it.
    """

    def __init__(self, app, *, password: str, sessions: SessionStore, throttle: LoginThrottle):
        self._app = app
        self._password = password
        self._sessions = sessions
        self._throttle = throttle

    def _bearer_response(self, request: Request) -> Response | None:
        """None when the request carries the right password as a Bearer token."""
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return JSONResponse(
                {"error": "authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        client = _client_address(request)
        if self._throttle.blocked(client):
            return JSONResponse(
                {"error": "too many attempts"},
                status_code=429,
                headers={"Retry-After": str(LOCKOUT_SECONDS)},
            )
        if not password_matches(token.strip(), self._password):
            self._throttle.record_failure(client)
            return JSONResponse(
                {"error": "authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        self._throttle.record_success(client)
        return None

    def _refusal(self, request: Request) -> Response | None:
        """The response to send instead of the app, or None to let it through."""
        path = request.url.path
        if _is_cross_site(request):
            return JSONResponse({"error": "cross-site request refused"}, status_code=403)
        if path == "/login" or path.startswith("/static/"):
            return None
        if self._sessions.valid(request.cookies.get(SESSION_COOKIE)):
            return None
        if path.startswith("/api/"):
            return self._bearer_response(request)
        if "hx-request" in request.headers:
            # htmx does not follow a redirect into a whole page. This covers a
            # session expiring while the progress page polls.
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        refusal = self._refusal(Request(scope, receive))
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self._app(scope, receive, send)


auth_router = APIRouter()


def _login_page(request: Request, *, error: str | None = None, status_code: int = 200):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error},
        status_code=status_code,
    )


@auth_router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if request.app.state.sessions.valid(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/", status_code=303)
    return _login_page(request)


@auth_router.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form("")):
    throttle = request.app.state.throttle
    client = _client_address(request)
    if throttle.blocked(client):
        response = _login_page(
            request, error="Too many attempts. Wait a minute and try again.", status_code=429
        )
        response.headers["Retry-After"] = str(LOCKOUT_SECONDS)
        return response
    if not password_matches(password, request.app.state.password):
        throttle.record_failure(client)
        log.warning("failed login from %s", client)
        return _login_page(request, error="Wrong password.", status_code=401)

    throttle.record_success(client)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        request.app.state.sessions.create(),
        max_age=SESSION_SECONDS,
        path="/",
        httponly=True,
        samesite="strict",
        secure=(
            request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        ),
    )
    return response


@auth_router.post("/logout")
async def logout(request: Request):
    request.app.state.sessions.revoke(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
