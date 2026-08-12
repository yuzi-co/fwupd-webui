from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fwupd_webui.config import Config
from fwupd_webui.fwupd.service import MetadataStatus
from fwupd_webui.web.routes import router

WEB_DIR = Path(__file__).parent


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

    app.state.service = service
    app.state.config = config
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.include_router(router)

    if config.enable_flashing:
        # Registered only when explicitly enabled, so POST /flash is a genuine
        # 404 otherwise rather than a handler that decided to refuse. Same
        # reasoning as disabling the uefi_capsule plugin outright: a capability
        # that does not exist cannot be reached by a mistake above it.
        from fwupd_webui.web.flash_routes import flash_router

        app.include_router(flash_router)

    return app
