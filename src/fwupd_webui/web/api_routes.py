from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from fwupd_webui import app_version
from fwupd_webui.fwupd.cli import FwupdError
from fwupd_webui.fwupd.service import Inventory

# Read-only machine-readable status, for monitoring tools. Always registered:
# reporting has nothing to do with the write path, so this exists whether or not
# flashing is enabled.
#
# Unlike the HTML routes, nothing here redirects while a flash runs -- a monitor
# needs JSON and needs to see that a write is in flight.
api_router = APIRouter(prefix="/api")


def _flash_payload(job) -> dict | None:
    if job is None:
        return None
    return {
        "active": not job.finished,
        "status": job.status.value,
        "device_id": job.device_id,
        "device_name": job.device_name,
        "target_version": job.version,
        "phase": job.phase or None,
        "percent": job.percent,
        "exit_code": job.exit_code,
        "error": job.error,
        "installed_version": job.installed_version,
        "staged": job.staged,
    }


def _updates_payload(inventory: Inventory) -> list[dict]:
    updates = []
    for view in inventory.devices:
        if not view.available:
            continue
        latest = view.available[0]
        updates.append(
            {
                "device_id": view.device.device_id,
                "name": view.device.display_name,
                "vendor": view.device.vendor,
                "plugin": view.device.plugin,
                "current_version": view.device.version,
                "available_version": latest.version,
                "urgency": latest.urgency,
                "needs_reboot": "needs-reboot" in view.device.flags,
            }
        )
    return updates


@api_router.get("/status")
async def status(request: Request) -> JSONResponse:
    """Everything a monitoring check needs, in one request.

    HTTP 200 for ok and degraded, 503 for error, so a plain uptime probe
    catches a broken fwupd without anyone having to configure a JSON path.
    """
    service = request.app.state.service
    job = getattr(service, "flash_manager", None)
    job = job.job if job is not None else None

    try:
        inventory = await service.cached_inventory()
    except FwupdError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "version": app_version(),
                "error": str(exc),
                "flashing_enabled": bool(getattr(service, "flashing_enabled", False)),
                "flash": _flash_payload(job),
            },
        )

    updates = _updates_payload(inventory)
    updatable = sum(1 for v in inventory.devices if v.device.updatable)

    state = "ok"
    if inventory.metadata.stale or inventory.metadata.error:
        state = "degraded"
    if job is not None and job.status.value == "failed":
        state = "error"

    return JSONResponse(
        content={
            "status": state,
            "version": app_version(),
            "fwupd_version": inventory.fwupd_version,
            "flashing_enabled": bool(getattr(service, "flashing_enabled", False)),
            "devices": {
                "total": len(inventory.devices),
                "updatable": updatable,
                "with_updates": len(updates),
            },
            "updates": updates,
            "metadata": {
                "last_refresh": inventory.metadata.last_refresh,
                "age_seconds": inventory.metadata.age_seconds,
                "stale": inventory.metadata.stale,
                "error": inventory.metadata.error,
            },
            "flash": _flash_payload(job),
        }
    )
