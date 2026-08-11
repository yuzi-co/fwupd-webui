from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import uvicorn

from fwupd_webui.config import Config
from fwupd_webui.fwupd.cli import FwupdCli
from fwupd_webui.fwupd.service import FwupdService
from fwupd_webui.web.app import create_app

STATE_DIR = Path("/var/lib/fwupd")


def main() -> None:
    config = Config.from_env()
    logging.basicConfig(level=config.log_level.upper())

    cli = FwupdCli(timeout=config.timeout_seconds)
    service = FwupdService(cli, config, STATE_DIR)

    # A refresh failure at boot must never stop the UI from coming up; the
    # inventory is useful without update metadata.
    try:
        asyncio.run(service.refresh_if_stale())
    except Exception:
        logging.getLogger(__name__).warning("startup metadata refresh failed", exc_info=True)

    app = create_app(service, config)
    uvicorn.run(app, host="0.0.0.0", port=config.port, log_level=config.log_level)


if __name__ == "__main__":
    main()
