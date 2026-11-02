from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fwupd_webui.fwupd.cli import FwupdError
from fwupd_webui.fwupd.service import Inventory
from fwupd_webui.web.routes import _flash_in_progress, _placeholder_inventory

# Registered on the app only when FWUPD_WEBUI_ENABLE_FLASHING is set, so every
# route here is a genuine 404 otherwise rather than a handler that refuses.
flash_router = APIRouter()


async def _page_inventory(service) -> Inventory:
    """base.html needs an inventory, and no flash page may wait on the hardware
    lock to get one, or the page would hang until the flash ended."""
    try:
        return await service.cached_inventory()
    except FwupdError as exc:
        return _placeholder_inventory(str(exc))


@flash_router.post("/flash")
async def start_flash(
    request: Request,
    device_id: str = Form(...),
    version: str = Form(...),
    operation: str = Form("upgrade"),
    confirm_name: str | None = Form(None),
    appstream_id: str | None = Form(None),
):
    service = request.app.state.service
    templates = request.app.state.templates
    # Before validation, which enumerates and would wait behind the running flash.
    if _flash_in_progress(service):
        return RedirectResponse("/flash", status_code=303)
    try:
        await service.start_flash(
            device_id,
            version,
            operation=operation,
            typed_name=confirm_name,
            appstream_id=appstream_id,
        )
    except (PermissionError, LookupError, RuntimeError, FwupdError) as exc:
        # A refusal is an expected outcome, not a server error.
        #
        # This is a full page, not the banner fragment. The confirm step is a
        # plain form POST, so the browser navigates -- returning a fragment left
        # the user on a blank white page with one unstyled line and no way back,
        # on the most likely error path there is: mistyping the device name.
        return templates.TemplateResponse(
            request=request,
            name="flash_refused.html",
            context={"message": str(exc), "inventory": await _page_inventory(service)},
            status_code=400,
        )
    return RedirectResponse("/flash", status_code=303)


@flash_router.get("/flash", response_class=HTMLResponse)
async def flash_page(request: Request):
    service = request.app.state.service
    templates = request.app.state.templates
    if service.flash_manager.job is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="flash_progress.html",
        context={"job": service.flash_manager.job, "inventory": await _page_inventory(service)},
    )


@flash_router.get("/flash/progress", response_class=HTMLResponse)
async def flash_progress(request: Request):
    service = request.app.state.service
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_flash_progress.html",
        context={"job": service.flash_manager.job},
    )


@flash_router.post("/flash/dismiss")
async def dismiss(request: Request):
    request.app.state.service.flash_manager.dismiss()
    return RedirectResponse("/", status_code=303)
