from fwupd_webui import __version__, app_version


def test_version_is_a_string():
    assert isinstance(app_version(), str)
    assert app_version()


def test_version_matches_package_metadata():
    """The UI must report the version that was actually installed, not one
    hardcoded in source that can drift from the built artifact."""
    from importlib.metadata import version

    assert app_version() == version("fwupd-webui")


def test_module_level_alias_is_the_same_value():
    assert __version__ == app_version()


def test_falls_back_when_metadata_is_missing(monkeypatch):
    """Running from a source tree with no install must not crash the header."""
    import importlib.metadata

    import fwupd_webui

    def boom(_name):
        raise importlib.metadata.PackageNotFoundError("fwupd-webui")

    monkeypatch.setattr(importlib.metadata, "version", boom)
    fwupd_webui.app_version.cache_clear()
    try:
        assert fwupd_webui.app_version() == "unknown"
    finally:
        fwupd_webui.app_version.cache_clear()
