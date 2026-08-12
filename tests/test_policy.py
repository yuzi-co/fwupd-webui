from fwupd_webui.fwupd.models import Device
from fwupd_webui.fwupd.policy import check_override, evaluate


def device(**kwargs) -> Device:
    payload = {"DeviceId": "d-1", "Name": "Test Device"}
    payload.update(kwargs)
    return Device.model_validate(payload)


def test_allowlisted_peripheral_is_permitted():
    perm = evaluate(device(Plugin="logitech_hidpp", Flags=["updatable"]), enabled=True)
    assert perm.allowed is True
    assert perm.needs_override is False
    assert perm.is_storage is False


def test_wacom_is_allowlisted():
    for plugin in ("wacom_usb", "wacom_raw"):
        assert evaluate(device(Plugin=plugin, Flags=["updatable"]), enabled=True).allowed is True


def test_thunderbolt_is_allowlisted():
    perm = evaluate(device(Plugin="thunderbolt", Flags=["updatable"]), enabled=True)
    assert perm.allowed is True


def test_every_storage_plugin_requires_an_override():
    """Storage is never one-click, whatever the allowlist says. A drive that
    holds data is dangerous to flash because something is using it, not
    because of which plugin drives it."""
    for plugin in ("nvme", "ata", "scsi", "emmc"):
        perm = evaluate(device(Plugin=plugin, Flags=["updatable"]), enabled=True)
        assert perm.allowed is False, plugin
        assert perm.needs_override is True, plugin
        assert perm.is_storage is True, plugin


def test_nvme_is_not_one_click_even_though_it_is_runtime_safe():
    """Regression. nvme was allowlisted, which made the deployed host's cache
    pool -- the drive Docker itself runs from -- the easiest thing in the UI
    to flash, while the array disks needed a typed name."""
    perm = evaluate(device(Plugin="nvme", Flags=["updatable"]), enabled=True)
    assert perm.allowed is False
    assert perm.is_storage is True


def test_storage_reason_warns_about_data():
    perm = evaluate(device(Plugin="ata", Flags=["updatable"]), enabled=True)
    assert "data" in perm.reason.lower()


def test_unknown_plugin_requires_an_override_but_is_not_storage():
    """Fail-safe: a plugin fwupd adds in a future release is not flashable
    by default. It is also not mislabelled as a storage hazard."""
    perm = evaluate(device(Plugin="brand_new_plugin", Flags=["updatable"]), enabled=True)
    assert perm.allowed is False
    assert perm.needs_override is True
    assert perm.is_storage is False


def test_device_without_updatable_flag_offers_nothing():
    perm = evaluate(device(Plugin="logitech_hidpp", Flags=["internal"]), enabled=True)
    assert perm.allowed is False
    assert perm.needs_override is False
    assert "not updatable" in perm.reason


def test_nothing_is_permitted_when_flashing_is_disabled():
    perm = evaluate(device(Plugin="logitech_hidpp", Flags=["updatable"]), enabled=False)
    assert perm.allowed is False
    assert perm.needs_override is False
    assert "FWUPD_WEBUI_ENABLE_FLASHING" in perm.reason


def test_needs_reboot_does_not_block():
    """Every updatable device on the deployed host reports needs-reboot.
    Blocking on it would disable the feature entirely."""
    perm = evaluate(
        device(Plugin="logitech_hidpp", Flags=["updatable", "needs-reboot"]), enabled=True
    )
    assert perm.allowed is True


def test_override_accepts_the_exact_device_name():
    assert check_override(device(Name="ST4000VN008-2DR166"), "ST4000VN008-2DR166") is True


def test_override_rejects_a_near_miss():
    assert check_override(device(Name="ST4000VN008-2DR166"), "ST4000VN008") is False


def test_override_rejects_absent_input():
    assert check_override(device(Name="ST4000VN008-2DR166"), None) is False
    assert check_override(device(Name="ST4000VN008-2DR166"), "") is False


def test_override_uses_display_name_for_unnamed_devices():
    unnamed = Device.model_validate({"DeviceId": "u", "Plugin": "linux_display"})
    assert check_override(unnamed, "Unknown linux_display device") is True
