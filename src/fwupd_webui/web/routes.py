from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from fwupd_webui.fwupd.cli import FwupdError
from fwupd_webui.fwupd.diagnostics import check_mounts
from fwupd_webui.fwupd.service import Inventory, MetadataStatus

router = APIRouter()


def _placeholder_inventory(message: str) -> Inventory:
    """base.html reads inventory.fwupd_version and inventory.metadata, so the
    error page needs an Inventory even though there is nothing to report."""
    return Inventory(
        devices=[],
        metadata=MetadataStatus(error=message),
        fwupd_version="unknown",
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates

    try:
        inventory = await service.inventory()
    except FwupdError as exc:
        # A failure to reach fwupdtool is a diagnosable condition, not a crash.
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"message": str(exc), "inventory": _placeholder_inventory(str(exc))},
        )

    if not inventory.devices:
        return templates.TemplateResponse(
            request=request,
            name="empty_state.html",
            context={"inventory": inventory, "mounts": check_mounts()},
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"inventory": inventory},
    )


@router.get("/devices/{device_id}", response_class=HTMLResponse)
async def device_detail(request: Request, device_id: str) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates
    inventory = await service.inventory()
    for view in inventory.devices:
        if view.device.device_id == device_id:
            return templates.TemplateResponse(
                request=request,
                name="_device_detail.html",
                context={"view": view, "inventory": inventory},
            )
    raise HTTPException(status_code=404, detail=f"no such device: {device_id}")


@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates
    try:
        await service.refresh()
        inventory = await service.inventory()
    except FwupdError as exc:
        return templates.TemplateResponse(
            request=request,
            name="_banner.html",
            context={"message": str(exc)},
        )
    return templates.TemplateResponse(
        request=request,
        name="_device_table.html",
        context={"inventory": inventory},
    )
