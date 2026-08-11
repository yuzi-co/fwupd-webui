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
    return app
