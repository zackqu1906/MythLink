"""Refresh an IMK context without inserting text, moving the caret or taking focus."""
import time

from .input_method_install import BUNDLE_ID
from .system_input_source import SystemInputSources


def _function_key_event(down):
    from AppKit import NSEvent, NSEventTypeKeyDown, NSEventTypeKeyUp, NSEventModifierFlagFunction, NSF20FunctionKey
    event = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        NSEventTypeKeyDown if down else NSEventTypeKeyUp, (0, 0), NSEventModifierFlagFunction,
        time.monotonic(), 0, None, NSF20FunctionKey, NSF20FunctionKey, False, 90)
    return event.CGEvent() if event is not None else None


def post_context_key(backend, origin, tag, *, timeout):
    deadline = time.monotonic() + timeout
    q = backend.quartz
    if not q.CGPreflightPostEventAccess():
        raise RuntimeError('输入事件权限不可用')
    if q.CGEventSourceFlagsState(q.kCGEventSourceStateHIDSystemState) & 0x1e0000:
        raise RuntimeError('修饰键仍按住')
    # Construct both phases in AppKit. Quartz's bare F20 keycode translates to
    # U+0010; overriding only its Unicode field does not fix the key-up event.
    events = [_function_key_event(down) for down in (True, False)]
    if any(event is None for event in events):
        raise RuntimeError('无法创建输入事件')
    for event in events:
        q.CGEventSetFlags(event, q.kCGEventFlagMaskSecondaryFn)
        q.CGEventSetIntegerValueField(event, q.kCGEventSourceUserData, tag)
        # Set characters last: changing Quartz flags/fields can regenerate
        # the keyboard-layout-derived U+0010 payload.
        q.CGEventKeyboardSetUnicodeString(event, 1, '\uf717')
    if (backend.current(timeout=max(0, deadline - time.monotonic())) != BUNDLE_ID + '.dictation'
            or backend.foreground() != origin or time.monotonic() >= deadline
            or q.CGEventSourceFlagsState(q.kCGEventSourceStateHIDSystemState) & 0x1e0000):
        raise RuntimeError('输入目标已变化')
    try:
        q.CGEventPostToPid(origin[1], events[0])
    finally:
        q.CGEventPostToPid(origin[1], events[1])


def refresh_input_context(bridge, origin, *, timeout):
    backend = SystemInputSources()
    return bridge.refresh_input_context(
        origin, lambda tag, **options: post_context_key(backend, origin, tag, **options), timeout=timeout)
