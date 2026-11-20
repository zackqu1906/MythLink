"""Real Quartz -> AppKit conversion, without posting any desktop events."""
from types import SimpleNamespace
import threading
import pytest

from proximic_ring.voice.ime.input_context_key import post_context_key
from proximic_ring.voice.ime.input_method_install import BUNDLE_ID


@pytest.mark.mac_only
@pytest.mark.parametrize('worker_thread', [False, True])
def test_context_event_is_a_function_key_not_a_control_character(worker_thread):
    quartz = pytest.importorskip('Quartz')
    appkit = pytest.importorskip('AppKit')
    observed = []
    class CaptureQuartz:
        def __getattr__(self, name):
            return getattr(quartz, name)
        def CGPreflightPostEventAccess(self):
            return True
        def CGEventSourceFlagsState(self, source):
            return 0
        def CGEventPostToPid(self, pid, event):
            observed.append(appkit.NSEvent.eventWithCGEvent_(event))
    backend = SimpleNamespace(quartz=CaptureQuartz(), foreground=lambda: ('test', 42),
                              current=lambda **_: BUNDLE_ID + '.dictation')
    if worker_thread:
        errors = []
        def post():
            try:
                post_context_key(backend, ('test', 42), 123, timeout=1)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=post)
        worker.start(); worker.join(2)
        assert not worker.is_alive() and not errors
    else:
        post_context_key(backend, ('test', 42), 123, timeout=1)
    assert len(observed) == 2
    for event in observed:
        assert event.keyCode() == 90
        assert event.modifierFlags() & appkit.NSEventModifierFlagFunction
        assert event.characters() == appkit.NSF20FunctionKey
        assert event.charactersIgnoringModifiers() == appkit.NSF20FunctionKey
    assert observed[0].type() == appkit.NSEventTypeKeyDown
    assert observed[1].type() == appkit.NSEventTypeKeyUp
