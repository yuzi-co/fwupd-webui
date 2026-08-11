from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

VALID_REMOTES = frozenset({"lvfs", "lvfs-testing"})


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    port: int = 8099
    refresh_interval_hours: int = 24
    timeout_seconds: int = 120
    lvfs_remote: str = "lvfs"
    log_level: str = "info"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        e = os.environ if env is None else env
        remote = e.get("FWUPD_WEBUI_LVFS_REMOTE", "lvfs")
        if remote not in VALID_REMOTES:
            raise ValueError(
                f"FWUPD_WEBUI_LVFS_REMOTE must be one of {sorted(VALID_REMOTES)}, got {remote!r}"
            )
        return cls(
            port=_int_env(e, "FWUPD_WEBUI_PORT", 8099),
            refresh_interval_hours=_int_env(e, "FWUPD_WEBUI_REFRESH_INTERVAL_HOURS", 24),
            timeout_seconds=_int_env(e, "FWUPD_WEBUI_TIMEOUT_SECONDS", 120),
            lvfs_remote=remote,
            log_level=e.get("FWUPD_WEBUI_LOG_LEVEL", "info"),
        )
