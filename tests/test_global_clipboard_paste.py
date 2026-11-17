"""Global paste preserves clipboard data and existing gesture arbitration."""
from dataclasses import replace
import json
import sys

import pytest
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QGuiApplication, QImage

from proximic_ring.gestures.gesture_settings import GESTURE_LABELS, GlobalGestureBindings
from test_app_gestures import route
from test_ring_gestures import request, deliver_gestures
from test_gesture_ownership import outside
from test_application_menus import configure, BUNDLE, ACTION


@pytest.fixture(autouse=True)
def isolated_clipboard(route):
    # Clipboard routing uses fake targets; no native Ring monitor is needed.
    owner = route[0]
    owner._proximity.close()
    yield
    owner.ringGestures.close()
    # Release Python-owned MIME data before QApplication is destroyed.
    QGuiApplication.clipboard().clear()


def test_paste_available_for_all_gestures_without_changing_old_bindings(route):
    owner, *_ = route
    old = json.loads(GlobalGestureBindings().to_json())
    del old['clipboard_paste']
    restored = GlobalGestureBindings.from_json(json.dumps(old))
    assert restored.clipboard_paste == ''
    assert {key: value for key, value in restored.as_dict().items() if key != 'clipboard_paste'} == {
        key: value for key, value in old.items() if key in restored.as_dict()}
    for gesture in GESTURE_LABELS:
        options = owner.ringGestures.globalActionOptions(gesture)
        assert any(item['id'] == 'clipboard_paste' and item['label'] == '粘贴' for item in options)
        bindings = restored.with_action(gesture, 'clipboard_paste')
        assert GlobalGestureBindings.from_json(bindings.to_json()).action_for(gesture) == 'clipboard_paste'


@pytest.mark.parametrize('context', ['text', 'nontext', 'unknown'])
@pytest.mark.parametrize('content', ['image', 'text'])
def test_paste_posts_standard_shortcut_and_keeps_current_clipboard(route, context, content):
    owner, _, _, backend, sent, _, _ = route
    backend.target = replace(backend.target, bundle='com.example.Mail', profile='mail', input_context=context)
    assert owner.ringGestures.setGlobalGestureAction('snap', 'clipboard_paste')
    clipboard = QGuiApplication.clipboard()
    mime = QMimeData()
    image = QImage(20, 16, QImage.Format_ARGB32); image.fill(Qt.blue)
    if content == 'image': mime.setImageData(image)
    else: mime.setText('刚刚复制的新内容')
    mime.setData('application/x-test-original', b'keep-this-payload')
    clipboard.setMimeData(mime)
    before = {kind: bytes(clipboard.mimeData().data(kind)) for kind in clipboard.mimeData().formats()}
    request(owner, 'snap')
    assert sent == [('mail', 'Cmd+V' if sys.platform == 'darwin' else 'Ctrl+V')]
    assert {kind: bytes(clipboard.mimeData().data(kind)) for kind in clipboard.mimeData().formats()} == before
    if content == 'image': assert clipboard.image() == image
    else: assert clipboard.text() == '刚刚复制的新内容'


@pytest.mark.parametrize('blocked', ['target_changed', 'busy'])
def test_paste_does_not_cross_target_change_or_replay_after_voice(route, blocked):
    owner, _, inline, backend, sent, _, _ = route
    outside(backend)
    assert owner.ringGestures.setGlobalGestureAction('snap', 'clipboard_paste')
    if blocked == 'busy': inline._view['phase'] = 'listening'
    request(owner, 'snap', deliver=False)
    if blocked == 'target_changed': backend.target = replace(backend.target, window='another-window')
    inline._view['phase'] = 'idle'
    deliver_gestures(owner)
    assert not sent


def test_application_binding_keeps_priority_over_global_paste(route, monkeypatch):
    owner, service, _, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    assert owner.ringGestures.setGlobalGestureAction('snap', 'clipboard_paste')
    assert catalog.setBinding(BUNDLE, 'snap', ACTION['id'])
    outside(backend)
    request(owner, 'snap')
    assert sent == [(BUNDLE, ACTION['shortcut'])]
