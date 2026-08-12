from __future__ import annotations

import importlib.metadata
from functools import lru_cache


@lru_cache(maxsize=1)
def app_version() -> str:
    """This application's own version, as installed.

    Read from package metadata rather than hardcoded, so the UI can never
    report a version that disagrees with the artifact it is running from --
    which is the whole reason for showing it, now that `:latest` moves.

    Falls back to "unknown" when running from a source tree with no install,
    because a missing version is not a reason to fail a page render.
    """
    try:
        return importlib.metadata.version("fwupd-webui")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


__version__ = app_version()
