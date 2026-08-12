from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fwupd_webui.fwupd.cli import FwupdError
from fwupd_webui.fwupd.diagnostics import check_mounts
from fwupd_webui.fwupd.service import Inventory, MetadataStatus

router = APIRouter()


def _flash_in_progress(service) -> bool:
    """True when a flash is running and the caller should be sent to /flash.

    Deliberately checked before anything touches the hardware lock: awaiting
    the lock first would make every page hang for the duration of the flash,
    which is indistinguishable from a crashed container.

    getattr because the phase C tests use a service double with no manager.
    """
    manager = getattr(service, "flash_manager", None)
    return manager is not None and manager.active


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

    if _flash_in_progress(service):
        return RedirectResponse("/flash", status_code=303)

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

    if _flash_in_progress(service):
        return RedirectResponse("/flash", status_code=303)

    inventory = await service.inventory()
    for view in inventory.devices:
        if view.device.device_id == device_id:
            return templates.TemplateResponse(
                request=request,
                name="_device_detail.html",
                context={"view": view, "inventory": inventory},
            )
    raise HTTPException(status_code=404, detail=f"no such device: {device_id}")


@router.get("/devices/{device_id}/confirm", response_class=HTMLResponse)
async def flash_confirm(
    request: Request, device_id: str, version: str, operation: str = "upgrade"
) -> HTMLResponse:
    """Render the confirm step. Always available -- it renders the reason when
    policy refuses, which is how an operator learns why there is no button.
    The POST it submits to only exists when flashing is enabled."""
    service = request.app.state.service
    templates = request.app.state.templates

    if _flash_in_progress(service):
        return RedirectResponse("/flash", status_code=303)

    inventory = await service.inventory()
    for view in inventory.devices:
        if view.device.device_id == device_id:
            return templates.TemplateResponse(
                request=request,
                name="_flash_confirm.html",
                context={
                    "view": view,
                    "version": version,
                    "operation": operation,
                    "permission": service.permission_for(view.device),
                    "inventory": inventory,
                },
            )
    raise HTTPException(status_code=404, detail=f"no such device: {device_id}")


@router.post("/refresh", response_class=HTMLResponse)
async def refresh(request: Request) -> HTMLResponse:
    service = request.app.state.service
    templates = request.app.state.templates

    if _flash_in_progress(service):
        return RedirectResponse("/flash", status_code=303)

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
