from __future__ import annotations

import html
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fwupd_webui import app_version
from fwupd_webui.config import Config
from fwupd_webui.fwupd.service import MetadataStatus
from fwupd_webui.web.api_routes import api_router
from fwupd_webui.web.routes import router

WEB_DIR = Path(__file__).parent


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</(p|li|ul|ol|div)\s*>", re.IGNORECASE)
_ITEM_RE = re.compile(r"<li\s*>", re.IGNORECASE)


def plain_text(markup: str | None) -> str:
    """Flatten fwupd's XHTML release descriptions into plain text.

    Vendor-supplied `Description` fields contain real markup (<p>, <ul>, <li>).
    Rendering them escaped shows the tags to the user; rendering them with
    `| safe` would hand vendor strings straight into the page. Stripping to
    text keeps them readable and keeps the autoescaping guarantee intact --
    whatever comes out of here is still escaped by Jinja on the way in.
    """
    if not markup:
        return ""
    text = _ITEM_RE.sub("\n• ", markup)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


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
    templates.env.filters["plain_text"] = plain_text
    # Global rather than per-route: base.html renders it on every page,
    # including the error and empty-state pages that have no inventory.
    templates.env.globals["app_version"] = app_version()

    app.state.service = service
    app.state.config = config
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.include_router(router)

    # Always registered. Reporting is read-only and has nothing to do with the
    # write path, so monitoring works whether or not flashing is enabled.
    app.include_router(api_router)

    if config.enable_flashing:
        # Registered only when explicitly enabled, so POST /flash is a genuine
        # 404 otherwise rather than a handler that decided to refuse. Same
        # reasoning as disabling the uefi_capsule plugin outright: a capability
        # that does not exist cannot be reached by a mistake above it.
        from fwupd_webui.web.flash_routes import flash_router

        app.include_router(flash_router)

    return app
