from fwupd_webui.fwupd.models import Device
from fwupd_webui.fwupd.policy import check_override, evaluate


def device(**kwargs) -> Device:
    payload = {"DeviceId": "d-1", "Name": "Test Device"}
    payload.update(kwargs)
    return Device.model_validate(payload)


def test_allowlisted_updatable_device_is_permitted():
    perm = evaluate(device(Plugin="nvme", Flags=["updatable"]), enabled=True)
    assert perm.allowed is True
    assert perm.needs_override is False


def test_thunderbolt_is_allowlisted():
    perm = evaluate(device(Plugin="thunderbolt", Flags=["updatable"]), enabled=True)
    assert perm.allowed is True


def test_ata_requires_an_override():
    """The four array drives on the deployed host are ata. Flashing drive
    firmware under a live array risks the array, not just the drive."""
    perm = evaluate(device(Plugin="ata", Flags=["updatable"]), enabled=True)
    assert perm.allowed is False
    assert perm.needs_override is True
    assert "ata" in perm.reason


def test_device_without_updatable_flag_offers_nothing():
    perm = evaluate(device(Plugin="nvme", Flags=["internal"]), enabled=True)
    assert perm.allowed is False
    assert perm.needs_override is False
    assert "not updatable" in perm.reason


def test_nothing_is_permitted_when_flashing_is_disabled():
    perm = evaluate(device(Plugin="nvme", Flags=["updatable"]), enabled=False)
    assert perm.allowed is False
    assert perm.needs_override is False
    assert "FWUPD_WEBUI_ENABLE_FLASHING" in perm.reason


def test_needs_reboot_does_not_block():
    """Every updatable device on the deployed host reports needs-reboot.
    Blocking on it would disable the feature entirely."""
    perm = evaluate(device(Plugin="nvme", Flags=["updatable", "needs-reboot"]), enabled=True)
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
