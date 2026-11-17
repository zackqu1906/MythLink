from dataclasses import replace
from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.platforms.macos.clipboard_suggestion import ClipboardField
from proximic_ring.gestures.gesture_settings import GestureBindings
from test_app_gestures import route
from test_ring_gestures import request


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.001)
    assert predicate()


@pytest.fixture
def suggestion(route, monkeypatch):
    owner, _, inline, *_ = route
    controller = owner.ringGestures._clipboard_suggestion
    controller._enabled = True
    revision = [7]
    messages, pastes, voices = [], [], []
    native = SimpleNamespace(connected=True, epoch='e', close=lambda: None,
                             send=lambda message: messages.append(message) or True)
    monkeypatch.setattr(inline, '_bridge', native)
    class Backend:
        focused = ClipboardField('editor-a', SimpleNamespace(pid=42), (30, 40, 100, 20))
        def capture(self):
            return self.focused
        def paste(self, field, rev, cancelled):
            if cancelled.is_set() or revision[0] != rev or self.focused != field:
                return False
            pastes.append((field.key, rev))
            return True
    backend = Backend()
    controller._backend = backend
    controller._revision = lambda: revision[0]
    owner._recognition_event.set()
    owner._utterance_commands = SimpleNamespace(connection=owner._disconnect_event,
        request=lambda kind, prepare, done: voices.append((kind, prepare, done)) or True)
    yield controller, owner, backend, revision, messages, pastes, voices
    controller.close()


def show(suggestion):
    c, owner, *_ = suggestion
    c.poll()
    wait_for(lambda: c._offer is not None)
    owner._inline_input._accept(dict(type='clipboard_offer_state', epoch='e',
                                    token=c._offer.token, visible=True))
    assert c._offer.shown
    return c._offer


def settled_poll(c):
    c.poll()
    wait_for(lambda: not c._polling)


def test_recommends_once_per_field_and_clipboard_revision(suggestion):
    c, _, backend, revision, messages, *_ = suggestion
    first = show(suggestion)
    assert messages[0] == dict(type='clipboard_offer', token=first.token, revision=7,
                              foreground_pid=42, rect=[30, 40, 100, 20])
    c.dismiss()
    settled_poll(c)
    assert c._offer is None
    backend.focused = None
    settled_poll(c)
    backend.focused = first.field
    settled_poll(c)
    assert c._offer is None
    backend.focused = replace(first.field, key='editor-b')
    second = show(suggestion)
    assert second.field.key == 'editor-b'
    revision[0] += 1
    third = show(suggestion)
    assert third.revision == 8 and third.token != second.token


@pytest.mark.parametrize('reason', ['typing', 'timeout', 'blur', 'voice', 'disconnect', 'clipboard_empty'])
def test_recommendation_dismissal(suggestion, reason):
    c, owner, backend, revision, *_ = suggestion
    offer = show(suggestion)
    if reason == 'typing': c._user_input()
    elif reason == 'timeout': offer.expires = time.monotonic() - 1
    elif reason == 'blur': backend.focused = None
    elif reason == 'voice':
        owner._inline_input._view['phase'] = 'listening'
        owner._inline_input.changed.emit()
    elif reason == 'disconnect': owner._disconnect_event.set()
    elif reason == 'clipboard_empty': revision[0] = None
    settled_poll(c)
    assert c._offer is None and offer.cancelled.is_set()


def test_native_palette_ack_is_required_before_consuming_gestures(suggestion):
    c, owner, *_ = suggestion
    c.poll(); wait_for(lambda: c._offer is not None)
    assert not c.consume(SimpleNamespace(name='tap'), owner._disconnect_event)
    c._native_event(dict(epoch='old', token=c._offer.token, visible=True))
    assert not c._offer.shown
    c._native_event(dict(epoch='e', token='old', visible=True))
    assert not c._offer.shown
    c._native_event(dict(epoch='e', token=c._offer.token, visible=False))
    assert c._offer is None


def test_right_swipe_accepts_once_and_never_uses_configured_edit_or_app_action(suggestion):
    c, owner, _, _, _, pastes, voices = suggestion
    offer = show(suggestion)
    assert not request(owner, 'swipe-right', deliver=False)
    assert not request(owner, 'swipe-right', deliver=False)
    # Native acknowledgement of our hide must not cancel the pending paste.
    c._native_event(dict(epoch='e', token=offer.token, visible=False))
    wait_for(lambda: c._offer is None)
    assert pastes == [('editor-a', 7)] and not voices


def test_tap_preempts_queued_right_swipe_even_with_custom_voice_binding(suggestion):
    c, owner, _, _, _, pastes, voices = suggestion
    owner._gesture_bindings = GestureBindings(confirm=('snap', ''), end=('double-tap', ''))
    offer = show(suggestion)
    assert not request(owner, 'swipe-right', deliver=False)
    assert not request(owner, 'tap', deliver=False)
    assert offer.cancelled.is_set()
    wait_for(lambda: len(voices) == 1)
    assert voices[0][0] == 'start' and not pastes
    assert c._offer is None


def test_tap_cancels_during_slow_paste_focus_validation(suggestion, monkeypatch):
    c, owner, backend, _, _, pastes, voices = suggestion
    entered, release = threading.Event(), threading.Event()
    paste = backend.paste
    def delayed(*args):
        entered.set()
        release.wait(2)
        return paste(*args)
    monkeypatch.setattr(backend, 'paste', delayed)
    show(suggestion)
    request(owner, 'swipe-right')
    wait_for(entered.is_set)
    request(owner, 'tap')
    release.set()
    wait_for(lambda: bool(voices))
    wait_for(lambda: c._queue._active is None)
    assert not pastes


@pytest.mark.parametrize('changed', ['focus', 'clipboard', 'connection'])
def test_accept_never_pastes_after_target_or_content_changed(suggestion, changed):
    c, owner, backend, revision, _, pastes, *_ = suggestion
    show(suggestion)
    request(owner, 'swipe-right', deliver=False)
    if changed == 'focus': backend.focused = replace(backend.focused, key='another-editor')
    elif changed == 'clipboard': revision[0] += 1
    else: owner._disconnect_event.set()
    QCoreApplication.processEvents()
    wait_for(lambda: c._queue._active is None)
    assert not pastes


def test_old_native_hide_cannot_dismiss_new_offer(suggestion):
    c, _, _, revision, *_ = suggestion
    old = show(suggestion)
    revision[0] += 1
    new = show(suggestion)
    c._native_event(dict(epoch='e', token=old.token, visible=False))
    assert c._offer is new and new.shown


def test_queued_focus_read_cannot_reopen_after_typing(suggestion, monkeypatch):
    c, _, backend, *_ = suggestion
    entered, release = threading.Event(), threading.Event()
    def capture():
        entered.set(); release.wait(2); return backend.focused
    monkeypatch.setattr(backend, 'capture', capture)
    c.poll(); wait_for(entered.is_set)
    c._user_input(); release.set()
    wait_for(lambda: not c._polling)
    assert c._offer is None
    c._quiet_until = 0
    settled_poll(c)
    assert c._offer is None


def test_tap_guard_suppresses_sdk_click_during_voice_handoff(suggestion):
    c, owner, *_ = suggestion
    show(suggestion)
    assert c.pointer_context[1] == 'clipboard'
    request(owner, 'tap')
    assert c._offer is None and c.pointer_context[1] == 'clipboard'
    c._pointer_guard = (c._pointer_guard[0], time.monotonic() - 1)
    assert c.pointer_context is None
