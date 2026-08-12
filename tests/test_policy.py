from fwupd_webui.fwupd.models import Device
from fwupd_webui.fwupd.policy import check_override, evaluate


def device(**kwargs) -> Device:
    payload = {"DeviceId": "d-1", "Name": "Test Device"}
    payload.update(kwargs)
    return Device.model_validate(payload)


def test_every_updatable_device_is_flashable_and_needs_the_typed_name():
    """One rule, no allowlist. Classifying plugins as safe or unsafe was a
    judgement this project got wrong twice; removing the category removes the
    bug class. Typing a name costs seconds on a machine flashed twice a year."""
    for plugin in ("logitech_hidpp", "thunderbolt", "nvme", "ata", "brand_new_plugin"):
        perm = evaluate(device(Plugin=plugin, Flags=["updatable"]), enabled=True)
        assert perm.flashable is True, plugin


def test_storage_devices_are_marked_as_such():
    for plugin in ("nvme", "ata", "scsi", "emmc"):
        perm = evaluate(device(Plugin=plugin, Flags=["updatable"]), enabled=True)
        assert perm.is_storage is True, plugin


def test_non_storage_devices_are_not_marked_as_storage():
    for plugin in ("logitech_hidpp", "thunderbolt", "wacom_usb", "brand_new_plugin"):
        perm = evaluate(device(Plugin=plugin, Flags=["updatable"]), enabled=True)
        assert perm.is_storage is False, plugin


def test_storage_reason_warns_about_data():
    perm = evaluate(device(Plugin="ata", Flags=["updatable"]), enabled=True)
    assert "data" in perm.reason.lower()


def test_non_storage_reason_does_not_cry_wolf_about_data():
    """A mouse receiver must not carry a data-loss warning, or the warning
    stops meaning anything on the devices that need it."""
    perm = evaluate(device(Plugin="logitech_hidpp", Flags=["updatable"]), enabled=True)
    assert "data" not in perm.reason.lower()


def test_device_without_updatable_flag_is_not_flashable():
    perm = evaluate(device(Plugin="logitech_hidpp", Flags=["internal"]), enabled=True)
    assert perm.flashable is False
    assert "not updatable" in perm.reason


def test_nothing_is_flashable_when_flashing_is_disabled():
    perm = evaluate(device(Plugin="nvme", Flags=["updatable"]), enabled=False)
    assert perm.flashable is False
    assert "FWUPD_WEBUI_ENABLE_FLASHING" in perm.reason


def test_needs_reboot_does_not_block():
    """Every updatable device on the deployed host reports needs-reboot.
    Blocking on it would disable the feature entirely."""
    perm = evaluate(device(Plugin="nvme", Flags=["updatable", "needs-reboot"]), enabled=True)
    assert perm.flashable is True


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


def test_there_is_no_allowlist_left_to_get_wrong():
    """Regression guard on the design decision, not just the behaviour."""
    import fwupd_webui.fwupd.policy as policy

    assert not hasattr(policy, "RUNTIME_SAFE_PLUGINS")


def test_esp_staging_plugins_are_never_flashable():
    """Independent of the Docker image's fwupd.conf, which does not exist on an
    LXC or bare-metal install where the host's fwupd config is live."""
    for plugin in ("uefi_capsule", "uefi_dbx"):
        perm = evaluate(device(Plugin=plugin, Flags=["updatable"]), enabled=True)
        assert perm.flashable is False, plugin
        assert "EFI system partition" in perm.reason


def test_esp_block_beats_the_updatable_flag():
    """A capsule device advertising itself as updatable must still be refused."""
    perm = evaluate(
        device(Plugin="uefi_capsule", Flags=["updatable", "needs-reboot"]), enabled=True
    )
    assert perm.flashable is False
