"""Installation evidence must not be confused with a live native connection."""
import json
import plistlib

import pytest
from proximic_ring.browser_bridge import install


def preferences(home, profile="Default", filename="Secure Preferences", **entry):
    target = home / "Library/Application Support/Google/Chrome" / profile / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"extensions": {"settings": {install.chrome_id(): entry}}}))
    return target


@pytest.mark.parametrize("entry,expected", [
    ({"path": "/extension", "state": 1}, "enabled"),
    ({"path": "/extension"}, "installed"),  # Current Chrome omits state.
    ({"path": "/extension", "state": 0}, "disabled"),
    ({"path": "/extension", "disable_reasons": [1]}, "disabled"),
    ({"path": "/extension", "disable_reasons": 1}, "disabled"),
    ({"path": "/extension", "state": 2}, "not_installed"),
    ({"last_update_time": "old"}, "unknown"),
])
def test_chrome_own_install_metadata(tmp_path, entry, expected):
    preferences(tmp_path, **entry)
    assert install.chrome_extension_state(tmp_path) == expected


def test_multiple_profiles_secure_precedence_and_no_native_host_false_positive(tmp_path):
    assert install.chrome_extension_state(tmp_path) == "not_installed"
    install.install_chrome(tmp_path)
    assert install.chrome_extension_state(tmp_path) == "not_installed"
    preferences(tmp_path, filename="Preferences", path="/extension", state=1)
    preferences(tmp_path, path="/extension", state=0)
    assert install.chrome_extension_state(tmp_path) == "disabled"
    preferences(tmp_path, profile="Profile 2", path="/extension")
    assert install.chrome_extension_state(tmp_path) == "installed"


def test_invalid_or_unreadable_preferences_are_not_reported_as_uninstalled(tmp_path, monkeypatch):
    path = preferences(tmp_path, path="/extension")
    path.write_text('{')
    assert install.chrome_extension_state(tmp_path) == "unknown"
    original = type(path).read_text
    def read(self, *args, **kwargs):
        if self == path:
            raise PermissionError()
        return original(self, *args, **kwargs)
    monkeypatch.setattr(type(path), "read_text", read)
    assert install.chrome_extension_state(tmp_path) == "unknown"


def test_other_extensions_do_not_count_as_ours(tmp_path):
    path = preferences(tmp_path, path="/extension")
    path.write_text(json.dumps({"extensions": {"settings": {"a"*32: {"path": "/other", "state": 1}}}}))
    assert install.chrome_extension_state(tmp_path) == "not_installed"


def test_safari_settings_direct_command_and_legacy_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "safari_helper_available", lambda _: True)
    info = tmp_path / 'Contents/Info.plist'
    info.parent.mkdir()
    calls=[]
    monkeypatch.setattr(install.subprocess, "run", lambda argv, **kw: calls.append(argv))
    info.write_bytes(plistlib.dumps({"MythlinkSupportsDirectSettings": True}))
    install.open_safari_settings(tmp_path)
    assert calls.pop() == [str(tmp_path/'Contents/MacOS/MythlinkBrowser'), '--open-settings']
    info.write_bytes(plistlib.dumps({}))
    install.open_safari_settings(tmp_path)
    assert calls.pop() == ['/usr/bin/open', str(tmp_path), '--args', '--open-settings']


def test_safari_settings_errors_are_propagated(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "safari_helper_available", lambda _: False)
    with pytest.raises(FileNotFoundError):
        install.open_safari_settings(tmp_path)
