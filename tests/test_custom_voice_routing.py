"""Configured voice gestures retain focus priority and one-shot finalization."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.gestures.gesture_settings import GESTURE_LABELS
from test_app_gestures import route
from test_application_menus import configure, BUNDLE, ACTION
from test_ring_gestures import request


@pytest.mark.parametrize('gesture', list(GESTURE_LABELS))
@pytest.mark.parametrize('operation', ['send', 'polish'])
def test_every_configured_send_or_polish_gesture_finalizes_once(route, gesture, operation):
    c, service, inline, backend, sent, messages, emit = route
    assert c.setVoiceGestureAction(gesture, operation)
    inline._view.update(phase='listening', has_composition=True)
    emit(gesture)
    emit(gesture)
    assert messages == [('finish', {})] and not sent
    inline._view.update(phase='dictated', has_composition=False)
    inline.settled.emit('dictated', '完整句子')
    inline.settled.emit('dictated', '完整句子')
    if operation == 'send':
        assert sent == [('codex', 'Return')]
        assert messages[-1] == ('end_input', {'outcome': 'sent'})
    else:
        assert not sent and messages[-1] == ('polish', {'restore_raw_on_undo': False})
    assert service._pending is None


@pytest.mark.parametrize('operation,old', [('send', 'swipe-up'), ('polish', 'swipe-down')])
def test_unbound_old_swipe_uses_app_action_and_rebound_voice_wins_only_in_input(route, monkeypatch, operation, old):
    c, service, inline, backend, sent, messages, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, old, ACTION['id'])
    assert catalog.setBinding(BUNDLE, 'snap', ACTION['id'])
    assert c.setVoiceGestureAction(old, '')
    assert c.setVoiceGestureAction('snap', operation)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    inline._view.update(application=BUNDLE, phase='dictated')
    request(c, old)
    assert sent == [(BUNDLE, 'Ctrl+Tab')]
    service._last_dispatch = None
    inline._utterance_id = 'second-sentence'
    inline._view.update(application=BUNDLE, phase='dictated', raw='hello', ready=True)
    request(c, 'snap')
    if operation == 'send':
        assert sent[-1] == (BUNDLE, 'Return')
    else:
        assert sent == [(BUNDLE, 'Ctrl+Tab')] and messages[-1][0] == 'polish'
    backend.target = replace(backend.target, input_context='nontext', focus='page', role='AXGroup')
    inline._action_pending = False
    service._last_dispatch = None
    request(c, 'snap')
    assert sent[-1] == (BUNDLE, 'Ctrl+Tab')


@pytest.mark.parametrize('gesture,operation', [('snap', 'send'), ('swipe-down', 'send'), ('clench', 'polish'), ('swipe-up', 'polish')])
def test_agent_editor_uses_configured_send_and_polish_for_queued_completion(route, monkeypatch, gesture, operation):
    c, service, inline, _, sent, messages, _ = route
    assert c.setVoiceGestureAction(gesture, operation)
    agent = c.agent
    calls = []
    monkeypatch.setattr(agent, '_input_context_valid', lambda *args: True)
    monkeypatch.setattr(agent, '_submit_from_gesture', lambda key, name: calls.append((key, name)))
    monkeypatch.setattr(agent.auto_context, 'prepare', lambda key: None)
    context = service.sentence(), c._gesture_bindings
    inline._view.update(phase='listening', has_composition=True)
    agent._input_gesture(gesture, 'task', c._disconnect_event, time.monotonic(), context)
    assert agent._pending_input_action and messages == [('finish', {})]
    inline._view.update(phase='dictated', has_composition=False)
    agent._input_settled('dictated', '完整句子')
    QCoreApplication.processEvents()
    assert calls == ([('task', gesture)] if operation == 'send' else [])
    assert not sent
    if operation == 'polish':
        assert messages[-1] == ('polish', {'restore_raw_on_undo': False})


@pytest.mark.parametrize('gesture', list(GESTURE_LABELS))
@pytest.mark.parametrize('operation', ['confirm', 'end', 'start_end'])
def test_arbitrary_voice_endpoints_reach_audio_gate_and_keep_outside_app_binding(route, monkeypatch, gesture, operation):
    c, service, inline, backend, sent, _, _ = route
    catalog = configure(service, monkeypatch)
    assert catalog.setBinding(BUNDLE, gesture, ACTION['id'])
    assert c.setVoiceGestureAction(gesture, operation)
    backend.target = replace(backend.target, bundle=BUNDLE, profile=BUNDLE)
    assert request(c, gesture)
    assert not sent
    backend.target = replace(backend.target, input_context='nontext', focus='page', role='AXGroup')
    assert not request(c, gesture)
    assert sent == [(BUNDLE, 'Ctrl+Tab')]
