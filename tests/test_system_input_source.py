"""Native source-cycle boundaries, independent of a user's keyboard settings."""
from types import SimpleNamespace
import sys
import threading

import pytest

from proximic_ring.voice.ime.system_input_source import cycle_to_source, SystemInputSources
from proximic_ring.voice.ime.system_input_source import _read_on_main_queue


ORIGIN = ('editor', 42)


class Sources:
    def __init__(self, sequence):
        self.sequence = iter(sequence)
        self.selected = next(self.sequence)
        self.front = ORIGIN
        self.advances = 0

    def current(self, *, timeout): return self.selected
    def foreground(self): return self.front
    def sources(self, *, timeout): return {'abc', 'pinyin', 'voice'}
    def shortcut(self): return (49, 786432)
    def advance(self, shortcut, origin):
        self.advances += 1
        self.selected = next(self.sequence)


@pytest.mark.parametrize('sequence', [('abc', 'pinyin', 'voice'), ('voice', 'abc', 'pinyin', 'voice')])
def test_switch_or_refresh_finishes_one_native_cycle(sequence):
    sources = Sources(sequence)
    cycle_to_source(sources, ORIGIN, 'voice', timeout=.1)
    assert sources.selected == 'voice' and sources.advances == len(sequence) - 1


def test_repeated_source_stops_instead_of_looping_or_claiming_success():
    sources = Sources(['abc', 'pinyin', 'abc'])
    with pytest.raises(RuntimeError, match='未找到'):
        cycle_to_source(sources, ORIGIN, 'voice', timeout=.1)
    assert sources.advances == 2


def test_disabled_target_cannot_emit_a_shortcut():
    sources = Sources(['abc'])
    with pytest.raises(RuntimeError, match='尚未启用'):
        cycle_to_source(sources, ORIGIN, 'absent', timeout=.1)
    assert sources.advances == 0


def test_changed_foreground_cannot_emit_another_shortcut():
    sources = Sources(['abc'])
    def advance(*_):
        sources.advances += 1
        sources.front = ('other', 43)
    sources.advance = advance
    with pytest.raises(RuntimeError, match='前台应用已变化'):
        cycle_to_source(sources, ORIGIN, 'voice', timeout=.1)
    assert sources.advances == 1


def test_missing_source_change_times_out_without_retry():
    sources = Sources(['abc'])
    sources.advance = lambda *_: setattr(sources, 'advances', sources.advances + 1)
    with pytest.raises(TimeoutError):
        cycle_to_source(sources, ORIGIN, 'voice', timeout=.01)
    assert sources.advances == 1


def test_expired_deadline_never_emits_a_shortcut():
    sources = Sources(['abc'])
    with pytest.raises(TimeoutError):
        cycle_to_source(sources, ORIGIN, 'voice', timeout=0)
    assert sources.advances == 0


@pytest.mark.parametrize('failure', ['foreground', 'key_down'])
def test_interrupted_shortcut_releases_all_pressed_modifiers(monkeypatch, failure):
    backend = object.__new__(SystemInputSources)
    posted = []
    backend.foreground = lambda: ORIGIN if not posted or failure != 'foreground' else ('other', 43)
    backend.shortcut = lambda: (49, 786432)
    def post(_, event):
        posted.append(dict(event))
        if failure == 'key_down' and event['code'] == 49 and event['down']:
            raise RuntimeError('injected failure')
    backend.quartz = SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1, kCGEventSourceUserData=2, kCGHIDEventTap=3,
        CGPreflightPostEventAccess=lambda: True, CGEventSourceFlagsState=lambda _: 0,
        CGEventSourceCreate=lambda _: object(),
        CGEventCreateKeyboardEvent=lambda _, code, down: dict(code=code, down=down),
        CGEventSetFlags=lambda event, flags: event.update(flags=flags),
        CGEventSetIntegerValueField=lambda *_: None, CGEventPost=post)
    monkeypatch.setattr('proximic_ring.voice.ime.system_input_source.time.sleep', lambda _: None)
    with pytest.raises(RuntimeError):
        backend.advance((49, 786432), ORIGIN)
    assert posted[-1] == {'code': 59, 'down': False, 'flags': 0}
    assert {p['code'] for p in posted if p['down']} <= {p['code'] for p in posted if not p['down']}
    if failure == 'foreground':
        assert not any(p['code'] == 49 for p in posted)


@pytest.mark.parametrize('binding', [None, {'enabled': False},
    {'enabled': True, 'value': {'type': 'standard', 'parameters': [32,49,0]}},
    {'enabled': True, 'value': {'type': 'standard', 'parameters': [32,49,0x20000]}}])
def test_disabled_or_printable_shortcut_is_rejected(monkeypatch, binding):
    import sys
    defaults = SimpleNamespace(persistentDomainForName_=lambda _: {'AppleSymbolicHotKeys': {'61': binding}})
    monkeypatch.setitem(sys.modules, 'Foundation', SimpleNamespace(
        NSUserDefaults=SimpleNamespace(standardUserDefaults=lambda: defaults)))
    with pytest.raises(RuntimeError):
        SystemInputSources.shortcut()


@pytest.fixture
def main_queue(monkeypatch):
    queued, ready = [], threading.Event()
    def submit(operation):
        queued.append(operation)
        ready.set()
    monkeypatch.setitem(sys.modules, 'Foundation', SimpleNamespace(
        NSThread=SimpleNamespace(isMainThread=lambda: threading.current_thread() is threading.main_thread()),
        NSOperationQueue=SimpleNamespace(mainQueue=lambda: SimpleNamespace(addOperationWithBlock_=submit))))
    return queued, ready


def test_main_queue_read_does_not_wait_on_itself(main_queue):
    assert _read_on_main_queue(lambda: 'source', timeout=.1) == 'source'
    assert not main_queue[0]


@pytest.mark.parametrize('failure', [False, True])
def test_worker_reads_and_releases_native_values_on_main_queue(main_queue, failure):
    reply, threads = {}, []
    def native_read():
        threads.append(threading.current_thread())
        if failure:
            raise ValueError('native read failed')
        return {'abc', 'voice'}
    def worker():
        try:
            reply['value'] = _read_on_main_queue(native_read, timeout=1)
        except Exception as exc:
            reply['error'] = exc
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert main_queue[1].wait(1)
    assert not threads
    main_queue[0].pop()()
    thread.join(1)
    assert not thread.is_alive() and threads == [threading.main_thread()]
    if failure:
        assert isinstance(reply['error'], ValueError)
    else:
        assert reply == {'value': {'abc', 'voice'}}


def test_timed_out_read_is_not_executed_when_main_queue_resumes(main_queue):
    reads, errors = [], []
    def worker():
        try:
            _read_on_main_queue(lambda: reads.append(True), timeout=.02)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(1)
    assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], TimeoutError)
    main_queue[0].pop()()
    assert not reads


def test_queue_wait_uses_remaining_source_cycle_budget():
    sources = Sources(['abc', 'voice'])
    budgets = []
    def unavailable(*, timeout):
        budgets.append(timeout)
        raise TimeoutError('main queue busy')
    sources.current = unavailable
    with pytest.raises(TimeoutError):
        cycle_to_source(sources, ORIGIN, 'voice', timeout=.03)
    assert 0 < budgets[0] <= .03 and sources.advances == 0
