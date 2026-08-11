from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates
    inventory = await service.inventory()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"inventory": inventory},
    )


@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates
    await service.refresh()
    inventory = await service.inventory()
    return templates.TemplateResponse(
        request=request,
        name="_device_table.html",
        context={"inventory": inventory},
    )
