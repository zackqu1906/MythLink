"""Missing system shortcuts must not block voice or bypass fresh IMK proof."""
import threading
import time
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal

from proximic_ring.ui.voice.gesture_source_activation import GestureSourceActivation
from proximic_ring.voice.ime import system_input_source as system
from test_inline_state import component


ORIGIN = ('test.editor', 42)


@pytest.fixture(autouse=True)
def isolate_native_permissions(monkeypatch):
    # These tests replace Foundation to model input-source shortcuts. A queued
    # real Qt permission probe must not load native plugins against that stub.
    import proximic_ring.ui.voice.inline_controller as inline
    permissions = inline.MacPermissionsController
    monkeypatch.setattr(inline, 'MacPermissionsController',
                        lambda parent, **_: permissions(parent, enabled=False))


class Source(QObject):
    event = Signal(object)

    def __init__(self, parent, *, timeout_ms):
        super().__init__(parent)
        self.timeout_ms = timeout_ms
        self.calls = []
        self.cancelled = False

    def start(self, origin): self.calls.append(('start', origin))
    def recover(self, origin): self.calls.append(('recover', origin))
    def cancel(self): self.cancelled = True


def worker(call):
    result = {}
    def run():
        try: result['value'] = call()
        except Exception as exc: result['error'] = exc
    thread = threading.Thread(target=run)
    thread.start()
    return thread, result


def until(condition, timeout=1):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.001)
    assert condition()


@pytest.fixture
def activation():
    data = {'front': ORIGIN, 'sources': []}
    def factory(parent, **options):
        source = Source(parent, **options)
        data['sources'].append(source)
        return source
    gate = GestureSourceActivation(foreground=lambda: data['front'], factory=factory)
    yield gate, data
    gate.close()
    QCoreApplication.processEvents()


def test_native_selection_keeps_gui_responsive_and_releases_helper(activation):
    gate, data = activation
    thread, result = worker(lambda: gate.activate(ORIGIN, timeout=.5))
    until(lambda: bool(data['sources']))
    source = data['sources'][0]
    ticks = []
    timer = QTimer(); timer.setInterval(2); timer.timeout.connect(lambda: ticks.append(True)); timer.start()
    QTimer.singleShot(25, lambda: source.event.emit({'event': 'selected', 'focus_restored': True}))
    until(lambda: not thread.is_alive())
    timer.stop(); thread.join()
    assert result == {'value': None} and ticks
    assert source.calls == [('start', ORIGIN)] and source.cancelled
    assert not gate._pending


def test_already_selected_inactive_client_recovers_once_with_remaining_budget(activation):
    gate, data = activation
    thread, result = worker(lambda: gate.activate(ORIGIN, timeout=.5))
    until(lambda: bool(data['sources']))
    first = data['sources'][0]
    time.sleep(.015)
    first.event.emit({'event': 'selected', 'already_selected': True})
    second = data['sources'][1]
    assert first.cancelled and second.calls == [('recover', ORIGIN)]
    assert second.timeout_ms < first.timeout_ms
    # A delayed event from the retired selector cannot finish the new stage.
    first.event.emit({'event': 'error', 'error': 'stale'})
    assert thread.is_alive()
    second.event.emit({'event': 'selected', 'focus_restored': True})
    thread.join(1)
    assert result == {'value': None} and second.cancelled


def test_expired_queued_request_never_starts_native_selection(activation):
    gate, data = activation
    thread, result = worker(lambda: gate.activate(ORIGIN, timeout=.02))
    thread.join(1)  # Deliberately withhold GUI dispatch until the deadline.
    QCoreApplication.processEvents()
    assert isinstance(result.get('error'), TimeoutError)
    assert not data['sources'] and not gate._pending


def test_timeout_cancels_helper_and_discards_late_success(activation):
    gate, data = activation
    thread, result = worker(lambda: gate.activate(ORIGIN, timeout=.05))
    until(lambda: bool(data['sources']))
    source = data['sources'][0]
    until(lambda: not thread.is_alive())
    until(lambda: source.cancelled)
    source.event.emit({'event': 'selected', 'focus_restored': True})
    assert isinstance(result.get('error'), TimeoutError) and not gate._pending


@pytest.mark.parametrize('when', ['queued', 'selected', 'close', 'helper_error'])
def test_native_activation_cannot_succeed_after_context_changes(activation, when):
    gate, data = activation
    thread, result = worker(lambda: gate.activate(ORIGIN, timeout=.5))
    if when == 'queued': data['front'] = ('other', 99)
    else:
        until(lambda: bool(data['sources']))
        source = data['sources'][0]
        if when == 'close': gate.close()
        elif when == 'helper_error': source.event.emit({'event': 'error', 'error': 'helper failed'})
        else:
            data['front'] = ('other', 99)
            source.event.emit({'event': 'selected', 'focus_restored': True})
    until(lambda: not thread.is_alive())
    assert isinstance(result.get('error'), RuntimeError)
    assert all(source.cancelled for source in data['sources'])
    assert not gate._pending


def test_gui_thread_never_waits_for_its_own_helper(activation):
    gate, data = activation
    with pytest.raises(RuntimeError, match='手势线程'):
        gate.activate(ORIGIN, timeout=.5)
    assert not data['sources']


@pytest.mark.parametrize('selection,status', [([0, 0], 'text'), ([-1, -1], 'nontext'), (None, 'unavailable')])
@pytest.mark.parametrize('binding', [None, {'enabled': False},
    {'enabled': True, 'value': {'type': 'standard', 'parameters': [32, 49, 0]}}])
def test_first_tap_without_system_shortcut_still_requires_live_imk_focus(component, monkeypatch, selection, status, binding):
    inline, _ = component
    monkeypatch.setattr(inline, '_foreground_identity', lambda: ORIGIN)
    state = {'reply': {'ready': False}, 'source': 'abc'}
    bridge = inline._bridge
    bridge.epoch = inline._epoch
    bridge.connected = True
    bridge.focus_state = lambda **_: state['reply']
    target = system.BUNDLE_ID + '.dictation'
    defaults = SimpleNamespace(persistentDomainForName_=lambda _: {'AppleSymbolicHotKeys': {'61': binding}})
    monkeypatch.setitem(sys.modules, 'Foundation', SimpleNamespace(
        NSUserDefaults=SimpleNamespace(standardUserDefaults=lambda: defaults)))
    class Backend:
        shortcut = staticmethod(system.SystemInputSources.shortcut)
        def foreground(self): return ORIGIN
        def sources(self, **_): return {'abc', target}
        def current(self, **_): return state['source']
        def advance(self, *_): pytest.fail('native fallback posted a keyboard shortcut')
    monkeypatch.setattr(system, 'SystemInputSources', Backend)
    helpers = []
    def factory(parent, **options):
        source = Source(parent, **options)
        helpers.append(source)
        def selected():
            state['source'] = target
            state['reply'] = dict(epoch=bridge.epoch, client_id='fresh-client', utterance_id='',
                                  application=ORIGIN[0], ready=selection == [0, 0], selection=selection)
            source.event.emit({'event': 'selected', 'focus_restored': True})
        QTimer.singleShot(10, selected)
        return source
    inline._gesture_source_activation._factory = factory
    thread, result = worker(inline.capture_start_focus)
    until(lambda: not thread.is_alive())
    assert result['value'].status == status
    assert len(helpers) == 1 and helpers[0].cancelled
    if status == 'text':
        assert result['value'].target == (bridge.epoch, 'fresh-client', '', *ORIGIN, (0, 0))
