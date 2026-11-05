import pytest

from proximic_ring.platforms.macos.shortcut_recording import SymbolicHotKeyGuard


class Carbon:
    def __init__(self):
        self.calls = []
        self.mode = 0
        self.token = 123

    def PushSymbolicHotKeyMode(self, mode):
        self.calls.append(('push', mode))
        self.previous, self.mode = self.mode, mode
        return self.token

    def PopSymbolicHotKeyMode(self, token):
        self.calls.append(('pop', token))
        self.mode = self.previous

    def GetSymbolicHotKeyMode(self):
        return self.mode


def test_recording_balances_one_mode_token_and_restores_previous_mode():
    carbon = Carbon()
    carbon.mode = 2
    guard = SymbolicHotKeyGuard(carbon=carbon, trusted=lambda: True, active=lambda: True)
    guard.start()
    guard.start()
    assert carbon.calls == [('push', 1)] and carbon.mode == 1
    guard.stop()
    guard.stop()
    assert carbon.calls == [('push', 1), ('pop', 123)] and carbon.mode == 2


def test_denied_permission_does_not_accept_a_misleading_native_token():
    carbon = Carbon()
    guard = SymbolicHotKeyGuard(carbon=carbon, trusted=lambda: False, active=lambda: True)
    with pytest.raises(RuntimeError, match='辅助功能权限'):
        guard.start()
    guard.stop()
    assert carbon.calls == []


def test_native_failure_does_not_leave_a_mode_request(monkeypatch):
    carbon = Carbon()
    guard = SymbolicHotKeyGuard(carbon=carbon, trusted=lambda: True, active=lambda: True)
    monkeypatch.setattr(carbon, 'GetSymbolicHotKeyMode', lambda: 0)
    with pytest.raises(RuntimeError, match='未确认'):
        guard.start()
    guard.stop()
    assert carbon.calls == [('push', 1), ('pop', 123)]


def test_null_token_is_not_reported_as_protected():
    carbon = Carbon()
    carbon.token = None
    guard = SymbolicHotKeyGuard(carbon=carbon, trusted=lambda: True, active=lambda: True)
    with pytest.raises(RuntimeError, match='无法暂停'):
        guard.start()
    guard.stop()
    assert carbon.calls == [('push', 1)]


def test_background_application_never_disables_system_hotkeys():
    carbon = Carbon()
    guard = SymbolicHotKeyGuard(carbon=carbon, trusted=lambda: True, active=lambda: False)
    with pytest.raises(RuntimeError, match='返回 Mythlink'):
        guard.start()
    assert carbon.calls == []
