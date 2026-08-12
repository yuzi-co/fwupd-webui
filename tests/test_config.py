import pytest

from fwupd_webui.config import Config


def test_defaults_when_env_empty():
    cfg = Config.from_env({})
    assert cfg.port == 8099
    assert cfg.refresh_interval_hours == 24
    assert cfg.timeout_seconds == 120
    assert cfg.lvfs_remote == "lvfs"
    assert cfg.log_level == "info"


def test_env_overrides_defaults():
    cfg = Config.from_env(
        {
            "FWUPD_WEBUI_PORT": "9000",
            "FWUPD_WEBUI_REFRESH_INTERVAL_HOURS": "6",
            "FWUPD_WEBUI_TIMEOUT_SECONDS": "30",
            "FWUPD_WEBUI_LVFS_REMOTE": "lvfs-testing",
            "FWUPD_WEBUI_LOG_LEVEL": "debug",
        }
    )
    assert cfg.port == 9000
    assert cfg.refresh_interval_hours == 6
    assert cfg.timeout_seconds == 30
    assert cfg.lvfs_remote == "lvfs-testing"
    assert cfg.log_level == "debug"


def test_unknown_remote_rejected():
    with pytest.raises(ValueError, match="FWUPD_WEBUI_LVFS_REMOTE"):
        Config.from_env({"FWUPD_WEBUI_LVFS_REMOTE": "lvfs-embargo-acme"})


def test_non_integer_port_rejected():
    with pytest.raises(ValueError, match="FWUPD_WEBUI_PORT"):
        Config.from_env({"FWUPD_WEBUI_PORT": "not-a-number"})


def test_flashing_is_disabled_by_default():
    assert Config.from_env({}).enable_flashing is False


def test_flashing_can_be_enabled():
    for raw in ("true", "TRUE", "1", "yes", "Yes"):
        assert Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": raw}).enable_flashing is True


def test_flashing_can_be_explicitly_disabled():
    for raw in ("false", "FALSE", "0", "no"):
        assert Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": raw}).enable_flashing is False


def test_unparseable_enable_flashing_is_a_startup_error():
    """A typo must not quietly resolve to either value. Enabling by accident
    grants firmware writes; disabling by accident silently drops the feature."""
    with pytest.raises(ValueError, match="FWUPD_WEBUI_ENABLE_FLASHING"):
        Config.from_env({"FWUPD_WEBUI_ENABLE_FLASHING": "maybe"})


def test_install_timeout_defaults_to_thirty_minutes():
    assert Config.from_env({}).install_timeout_seconds == 1800


def test_install_timeout_is_configurable():
    cfg = Config.from_env({"FWUPD_WEBUI_INSTALL_TIMEOUT_SECONDS": "600"})
    assert cfg.install_timeout_seconds == 600
