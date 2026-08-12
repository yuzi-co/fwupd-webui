from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

# Registered on the app only when FWUPD_WEBUI_ENABLE_FLASHING is set, so every
# route here is a genuine 404 otherwise rather than a handler that refuses.
flash_router = APIRouter()


@flash_router.post("/flash")
async def start_flash(
    request: Request,
    device_id: str = Form(...),
    version: str = Form(...),
    operation: str = Form("upgrade"),
    confirm_name: str | None = Form(None),
):
    service = request.app.state.service
    templates = request.app.state.templates
    try:
        await service.start_flash(device_id, version, operation=operation, typed_name=confirm_name)
    except (PermissionError, LookupError, RuntimeError) as exc:
        # A refusal is an expected outcome, not a server error.
        return templates.TemplateResponse(
            request=request, name="_banner.html", context={"message": str(exc)}
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
        context={"job": service.flash_manager.job, "inventory": await service.inventory()},
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
