"""The voice-page editor is an ordinary IME target unless a test is armed."""
import pytest
from test_inline_ui import inline_ui


def open_editor(inline_ui):
    from PySide6.QtCore import QObject, Qt
    from PySide6.QtTest import QTest
    controller, bridge, requests, root, event = inline_ui
    root.show()
    root.setProperty('currentPage', 1)
    editor = root.findChild(QObject, 'voiceTrialEditor')
    editor.forceActiveFocus(Qt.OtherFocusReason)
    QTest.qWait(20)
    return controller, bridge, requests, root, event, editor


def test_typing_and_ime_commit_keep_native_undo_without_starting_test(inline_ui):
    from PySide6.QtCore import QCoreApplication, QMetaObject, Qt
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtTest import QTest
    c, bridge, _, root, event, editor = open_editor(inline_ui)
    event(phase='interrupted', ready=True, has_composition=False)
    assert not c.inlineInput.canUndo
    for char in 'hello': QTest.keyClick(root, char)
    commit = QInputMethodEvent()
    commit.setCommitString('世界')
    QCoreApplication.sendEvent(editor, commit)
    assert editor.property('text') == 'hello世界'
    assert c.voicePractice.text == 'hello世界' and not c.voicePractice.active
    assert editor.property('canUndo')
    QMetaObject.invokeMethod(editor, 'undo')
    assert editor.property('text') == 'hello'
    QMetaObject.invokeMethod(editor, 'redo')
    assert editor.property('text') == 'hello世界'
    QTest.keyClick(root, Qt.Key_Z, Qt.ControlModifier)
    assert editor.property('text') == 'hello'
    assert not c.voicePractice.active


@pytest.mark.parametrize('action, phase, command', [
    ('Undo', 'dictated', 'cancel'),
    ('Polish', 'dictated', 'polish'),
    ('Edit', 'listening', 'convert'),
])
def test_regular_voice_actions_use_ime_without_practice(inline_ui, monkeypatch, action, phase, command):
    from PySide6.QtCore import QObject, QPointF, Qt
    from PySide6.QtTest import QTest
    c, bridge, _, root, event, editor = open_editor(inline_ui)
    editor.setProperty('text', '普通听写')
    event(phase=phase, ready=True, raw='普通听写', revision=1, polished=False,
          has_composition=phase == 'listening', edit_requested=False, awaiting_readback=False)
    monkeypatch.setattr(c.voicePractice, '_configuration', lambda: pytest.fail('Regular input entered practice'))
    button = root.findChild(QObject, 'voiceEditor' + action + 'Button')
    QTest.qWait(20)
    assert button.property('enabled') and editor.property('activeFocus')
    before = len(bridge.messages)
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier,
                    button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint())
    QTest.qWait(20)
    assert editor.property('activeFocus')
    assert any(m.get('type') == command for m in bridge.messages[before:])
    assert not c.voicePractice.active
    if action == 'Polish':
        assert root.grabWindow().save('/private/tmp/mythlink-voice-editor-20261104.png')


def test_undo_button_uses_editor_history_for_ordinary_typing(inline_ui):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest
    c, _, _, root, event, editor = open_editor(inline_ui)
    event(phase='interrupted', ready=True, has_composition=False)
    assert not c.inlineInput.canUndo
    for char in 'draft': QTest.keyClick(root, char)
    button = root.findChild(QObject, 'voiceEditorUndoButton')
    assert button.property('enabled')
    QMetaObject.invokeMethod(button, 'click')
    assert editor.property('text') == '' and not c.voicePractice.active


def test_practice_result_is_a_single_native_undo_step(inline_ui):
    from PySide6.QtCore import QMetaObject
    from PySide6.QtTest import QTest
    c, _, _, root, event, editor = open_editor(inline_ui)
    event(phase='interrupted', ready=True, has_composition=False)
    assert not c.inlineInput.canUndo
    for char in 'draft': QTest.keyClick(root, char)
    c.voicePractice.setText('测试结果')
    assert editor.property('text') == '测试结果'
    QMetaObject.invokeMethod(editor, 'undo')
    assert editor.property('text') == 'draft'
    QMetaObject.invokeMethod(editor, 'redo')
    assert editor.property('text') == '测试结果'


def test_normal_composition_offers_edit_before_text_is_committed(inline_ui):
    from PySide6.QtCore import QCoreApplication, QObject
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtTest import QTest
    c, _, _, root, event, editor = open_editor(inline_ui)
    event(phase='listening', ready=True, raw='修改这句话', has_composition=True,
          edit_requested=False)
    QCoreApplication.sendEvent(editor, QInputMethodEvent('修改这句话', []))
    QTest.qWait(20)
    button = root.findChild(QObject, 'voiceEditorEditButton')
    assert editor.property('text') == '' and editor.property('preeditText') == '修改这句话'
    assert button.property('visible') and button.property('enabled')
    assert not c.voicePractice.active
