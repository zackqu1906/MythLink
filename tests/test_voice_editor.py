"""The voice-page editor is always an ordinary input-method target."""
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
    assert c.voicePractice.text == '' and not c.voicePractice.active
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
    c, bridge, _, root, event, editor = open_editor(inline_ui)
    editor.setProperty('text', '普通听写')
    event(phase=phase, ready=True, raw='普通听写', revision=1, polished=False,
          has_composition=phase == 'listening', edit_requested=False, awaiting_readback=False)
    monkeypatch.setattr(c.voicePractice, '_configuration', lambda: pytest.fail('Regular input entered practice'))
    assert editor.property('activeFocus')
    before = len(bridge.messages)
    if action == 'Undo':
        c.undoLastApplied()
    elif action == 'Polish':
        c.inlineInput.polish()
    else:
        c.dispatchVoiceAction('edit')
    assert editor.property('activeFocus')
    assert any(m.get('type') == command for m in bridge.messages[before:])
    assert not c.voicePractice.active


def test_editor_has_no_practice_controls(inline_ui):
    from PySide6.QtCore import QObject
    c, _, _, root, event, editor = open_editor(inline_ui)
    for name in ('voiceEditorPolishButton', 'voiceEditorEditButton',
                 'voiceEditorUndoButton', 'voicePracticeStartButton', 'voicePracticeStatus'):
        assert root.findChild(QObject, name) is None
    assert not c.voicePractice.active


def test_practice_results_do_not_overwrite_editor_or_undo_history(inline_ui):
    from PySide6.QtCore import QMetaObject
    from PySide6.QtTest import QTest
    c, _, _, root, event, editor = open_editor(inline_ui)
    event(phase='interrupted', ready=True, has_composition=False)
    assert not c.inlineInput.canUndo
    for char in 'draft': QTest.keyClick(root, char)
    c.voicePractice.setText('测试结果')
    assert editor.property('text') == 'draft'
    QMetaObject.invokeMethod(editor, 'undo')
    assert editor.property('text') == ''
    QMetaObject.invokeMethod(editor, 'redo')
    assert editor.property('text') == 'draft'


def test_ime_composition_replacement_and_undo_use_the_editor_document(inline_ui):
    from PySide6.QtCore import QCoreApplication, QMetaObject
    from PySide6.QtGui import QInputMethodEvent
    c, _, _, root, event, editor = open_editor(inline_ui)
    event(phase='listening', ready=True, raw='修改这句话', has_composition=True,
          edit_requested=False)
    QCoreApplication.sendEvent(editor, QInputMethodEvent('修改这句话', []))
    assert editor.property('text') == '' and editor.property('preeditText') == '修改这句话'
    commit = QInputMethodEvent()
    commit.setCommitString('修改这句话')
    QCoreApplication.sendEvent(editor, commit)
    replacement = QInputMethodEvent()
    replacement.setCommitString('已经修改', -5, 5)
    QCoreApplication.sendEvent(editor, replacement)
    assert editor.property('text') == '已经修改'
    assert editor.property('preeditText') == ''
    QMetaObject.invokeMethod(editor, 'undo')
    assert editor.property('text') == '修改这句话'
    QMetaObject.invokeMethod(editor, 'redo')
    assert editor.property('text') == '已经修改'
    assert not c.voicePractice.active


@pytest.mark.parametrize('mode', ['audio', 'dictation'])
def test_practice_recording_does_not_block_editor_undo(inline_ui, monkeypatch, mode):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from proximic_ring.asr.factory import ASRBackendSettings
    from proximic_ring.text_processing.model import LLMSettings
    from proximic_ring.ui.voice.practice_controller import PracticeConfiguration

    c, _, _, root, event, editor = open_editor(inline_ui)
    event(phase='interrupted', ready=True, has_composition=False)
    config = PracticeConfiguration('ring', '', True, 'volcengine',
                                   ASRBackendSettings(), LLMSettings(), c._gesture_bindings)
    monkeypatch.setattr(c.voicePractice, '_configuration', lambda: config)
    for char in 'draft':
        QTest.keyClick(root, char)
    assert c.voicePractice.start(mode)
    QTest.keyClick(root, Qt.Key_Z, Qt.ControlModifier)
    assert editor.property('text') == ''
    QTest.keyClick(root, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert editor.property('text') == 'draft'
    QTest.keyClick(root, '!')
    assert editor.property('text') == 'draft!'
