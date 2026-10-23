import plistlib

from proximic_ring.ui.shared.startup import LoginRegistration, StartupController


def test_mac_login_registration_round_trip_uses_app_bundle(tmp_path):
    registration = LoginRegistration(home=tmp_path, platform="darwin", frozen=True,
                                     executable="/Applications/Mythlink Test.app/Contents/MacOS/Mythlink")
    startup = StartupController(registration=registration)
    assert not startup.enabled and not registration.path.exists()
    startup.setEnabled(True)
    value = plistlib.loads(registration.path.read_bytes())
    assert value["ProgramArguments"] == ["/usr/bin/open", "-a", "/Applications/Mythlink Test.app"]
    assert value["RunAtLoad"] is True and value["LimitLoadToSessionType"] == "Aqua"
    assert StartupController(registration=registration).enabled
    startup.setEnabled(False)
    assert not startup.enabled and not registration.path.exists()


def test_source_login_keeps_venv_interpreter_and_import_path(tmp_path):
    executable = tmp_path / "project with spaces/.runtime/venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to("/usr/bin/python3")
    registration = LoginRegistration(home=tmp_path, platform="darwin", frozen=False, executable=str(executable))
    registration.set_enabled(True)
    value = plistlib.loads(registration.path.read_bytes())
    assert value["ProgramArguments"] == [str(executable), "-m", "proximic_ring.ui.main"]
    assert value["EnvironmentVariables"]["PYTHONPATH"].endswith("/src")
    assert "KeepAlive" not in value


def test_failed_login_registration_reports_failure_without_claiming_success(tmp_path, monkeypatch):
    registration = LoginRegistration(home=tmp_path, platform="darwin", frozen=False)
    startup = StartupController(registration=registration)
    def fail(_enabled):
        raise PermissionError("permission denied")
    monkeypatch.setattr(registration, "set_enabled", fail)
    startup.setEnabled(True)
    assert not startup.enabled
    assert "无法修改" in startup.error
    assert not registration.path.exists()


def test_corrupt_login_file_does_not_break_settings(tmp_path):
    registration = LoginRegistration(home=tmp_path, platform="darwin", frozen=False)
    registration.path.parent.mkdir(parents=True)
    registration.path.write_bytes(b"invalid plist")
    startup = StartupController(registration=registration)
    assert not startup.enabled and "无法读取" in startup.error
    startup.setEnabled(True)
    assert startup.enabled and not startup.error
