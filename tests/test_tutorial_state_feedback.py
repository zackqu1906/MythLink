"""Live lesson guidance, microphone feedback and prepared-state presentation."""
import numpy as np
import pytest

from test_inline_ui import inline_ui
from test_voice_practice import practice
from test_voice_tutorial import start_lesson, begin_sentence
from test_ui_shell import visual_child


def test_pcm_waveform_shows_soft_speech_and_decays_without_invented_sound(practice):
    assert practice.start('audio')
    assert practice.levels == [0.] * 32
    practice.consume_audio(np.zeros(320))
    practice._tick()
    assert practice.levels[-1] == 0
    practice.consume_audio(np.full(320, .004, dtype=np.float32))
    practice._tick()
    quiet = practice.levels[-1]
    assert .1 < quiet < .5  # A visible signal from quiet, real input.
    practice.consume_audio(np.full(320, .12, dtype=np.float32))
    practice._tick()
    assert practice.levels[-1] > quiet
    # No incoming samples: new waveform bins are flat, never looping animation.
    for _ in range(32):
        practice._tick()
    assert practice.levels == [0.] * 32
    practice.finish()
    assert not practice.audioTestPassed  # Too short to count as a usable test.
    practice.start('audio')
    practice.consume_audio(np.full(8000, .04, dtype=np.float32))
    practice._tick(); practice.finish()
    assert practice.audioTestPassed and practice.audioAvailable
    practice.cancel()
    assert practice.levels == [0.] * 32 and not practice.audioTestPassed


@pytest.mark.parametrize('stage,action', [(0, 'tap'), (1, 'swipe-down'), (2, 'swipe-right')])
def test_each_lesson_waits_for_text_before_prompting_its_action(inline_ui, stage, action):
    controller, bridge, _, _, event = inline_ui
    tutorial = start_lesson(controller, event)
    tutorial._stage = stage
    assert tutorial.hint['gestures'] == ['double-tap']
    begin_sentence(controller, event)
    assert tutorial.hint['id'] == f'speak-{stage}'
    assert not tutorial.hint['gestures']
    event(phase='listening', ready=True, raw='   ')
    assert tutorial.hint['id'] == f'speak-{stage}'
    before = list(bridge.messages)
    event(phase='listening', ready=True, raw='识别到的文字')
    assert tutorial.hint['gestures'] == [action]
    stable_hint = tutorial.hint
    event(phase='listening', ready=True, raw='识别到的文字继续增加')
    assert tutorial.hint == stable_hint
    assert bridge.messages == before  # Observing never issues an input command.


def test_stopping_tutorial_detaches_input_observer(inline_ui):
    controller, _, _, _, event = inline_ui
    tutorial = start_lesson(controller, event)
    changes = []
    tutorial.changed.connect(lambda: changes.append(tutorial.hint['id']))
    tutorial.stop(); changes.clear()
    event(phase='listening', ready=True, raw='正常使用时的文字')
    assert changes == [] and tutorial._key is None
    assert tutorial.hint['id'] == 'inactive'
    event(phase='idle', ready=True)
    assert tutorial.start() and tutorial.start()  # No duplicate subscriptions.
    changes.clear()
    controller.inlineInput.changed.emit()
    assert len(changes) == 1


def open_preparation(root):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest
    from test_ui_figma_redesign import click
    root.show(); root.requestActivate()
    dialog = root.findChild(QObject, 'tutorialPreviewDialog')
    QMetaObject.invokeMethod(dialog, 'open')
    for _ in range(50):
        if dialog.property('opened'):
            break
        QTest.qWait(30)
    assert dialog.property('opened')
    click(root, 'tutorialPrepareButton')
    QTest.qWait(50)
    return dialog


def mark_prepared(controller, monkeypatch, ready=True):
    controller._connected = ready
    controller._recognition_enabled = ready
    monkeypatch.setattr(controller.inlineInput.permissions, 'permissionGranted', lambda _: ready)
    controller.inlineInput._input_method_version = dict(current=ready, state='current' if ready else 'not_installed', text='已更新' if ready else '未安装', detail='')
    controller.connectedChanged.emit(); controller.recognitionEnabledChanged.emit()
    controller.inlineInput.permissions.changed.emit(); controller.inlineInput.changed.emit()


@pytest.mark.parametrize('size', [(1200,780), (940,700)])
def test_ready_preparation_has_green_status_without_navigation_and_visible_waveform(inline_ui, monkeypatch, size):
    from PySide6.QtCore import QMetaObject
    from PySide6.QtTest import QTest
    from proximic_ring.ui.voice.practice_controller import PracticeConfiguration
    from proximic_ring.asr.factory import ASRBackendSettings
    from proximic_ring.gestures.gesture_settings import GestureBindings
    from proximic_ring.text_processing.model import LLMSettings
    controller, _, _, root, event = inline_ui
    event(phase='idle', ready=True)
    root.resize(*size)
    dialog = open_preparation(root)
    mark_prepared(controller, monkeypatch)
    QTest.qWait(100)
    for index in range(3):
        status = visual_child(dialog.property("contentItem"), f'tutorialPreparationStatus{index}')
        action = visual_child(dialog.property("contentItem"), f'tutorialPreparationAction{index}')
        assert status.property('text') == '已就绪，可以使用'
        assert status.property('color').name() == '#16703f'
        assert not action.property('visible')
    config = PracticeConfiguration('microphone', '', True, 'volcengine', ASRBackendSettings(), LLMSettings(), GestureBindings())
    controller.voicePractice._configuration = lambda: config
    QMetaObject.invokeMethod(visual_child(dialog.property("contentItem"), 'voiceAudioTestButton'), 'click')
    assert controller.voicePractice.recording
    controller.voicePractice._timer.stop()  # Drive exact PCM display ticks below.
    controller.voicePractice.consume_audio(np.full(320, .004, dtype=np.float32))
    controller.voicePractice._tick(); QTest.qWait(75)
    bar = visual_child(dialog.property("contentItem"), 'voiceAudioWaveformBar31')
    assert bar.height() > 7 and bar.width() > 0
    # Stable delegates animate live samples without recreating the waveform.
    controller.voicePractice.consume_audio(np.full(320, .15, dtype=np.float32))
    controller.voicePractice._tick(); QTest.qWait(75)
    assert visual_child(dialog.property("contentItem"), 'voiceAudioWaveformBar31') is bar
    assert bar.height() > 20
    controller.voicePractice.cancel()
    mark_prepared(controller, monkeypatch, False); QTest.qWait(50)
    for index in range(3):
        assert visual_child(dialog.property("contentItem"), f'tutorialPreparationAction{index}').property('visible')
    QMetaObject.invokeMethod(dialog, 'close')


def test_hint_transitions_are_fast_and_do_not_restart_for_each_partial(inline_ui):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest
    from test_ui_figma_redesign import click
    controller, _, _, root, event = inline_ui
    event(phase='idle', ready=True)
    controller._connected = True; controller.connectedChanged.emit()
    dialog = open_preparation(root)
    click(root, 'tutorialBeginButton'); QTest.qWait(160)
    begin_sentence(controller, event)
    title = root.findChild(QObject, 'tutorialHintTitle')
    motion = root.findChild(QObject, 'tutorialHintTransition')
    content = root.findChild(QObject, 'tutorialHintContent')
    assert title.property('text') == '请开始说话'
    assert root.findChild(QObject, 'tutorialPracticeEditor').property('placeholderText') == '正在听，请说出一句话……'
    assert motion.property('running')
    QTest.qWait(180)
    assert not motion.property('running') and content.property('opacity') == 1
    event(phase='listening', ready=True, raw='今天')
    assert title.property('text') == '确认听写结果' and motion.property('running')
    QTest.qWait(180)
    event(phase='listening', ready=True, raw='今天下午三点开会')
    assert not motion.property('running')
    # Rapid state changes always display the newest instruction, no queued hint.
    event(phase='finishing', ready=True)
    assert title.property('text') == '正在定稿'
    event(phase='dictated', ready=True)
    event('settled', phase='dictated', text='今天下午三点开会')
    assert title.property('text') == '本阶段已完成'
    QTest.qWait(180)
    assert content.property('opacity') == 1 and not motion.property('running')
    QMetaObject.invokeMethod(dialog, 'close')
