from fwupd_webui.fwupd.diagnostics import REQUIRED_MOUNTS, check_mounts


def test_reports_every_required_mount(tmp_path):
    checks = check_mounts(root=tmp_path)
    assert {c.path for c in checks} == set(REQUIRED_MOUNTS)


def test_missing_mount_is_flagged(tmp_path):
    checks = {c.path: c for c in check_mounts(root=tmp_path)}
    assert checks["/run/udev"].present is False


def test_present_mount_is_detected(tmp_path):
    (tmp_path / "run" / "udev").mkdir(parents=True)
    checks = {c.path: c for c in check_mounts(root=tmp_path)}
    assert checks["/run/udev"].present is True


def test_every_mount_explains_its_purpose():
    for check in check_mounts():
        assert check.purpose
