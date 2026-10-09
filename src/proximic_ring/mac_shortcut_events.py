"""Balanced, preallocated macOS shortcut events, pinned to one target process.

Some applications track modifier transitions separately from a key's flags.
Sending only Return with Command flags can therefore be treated as plain Return.
This module has no application profiles, scene policy or foreground activation.
"""
from .app_gestures import KEY_CODES, normalize_shortcut
from .scene_diagnostics import SceneActionError

# macOS virtual key codes and left-side NX_DEVICE* masks from IOLLEvent.h.
MODIFIER_KEYS = {'Cmd': (55, 0x08), 'Ctrl': (59, 0x01), 'Alt': (58, 0x20),
                 'Shift': (56, 0x02), 'Fn': (63, 0)}
EVENT_TAG = 0x50524F584147


def prepare_chord(quartz, shortcut):
    """Allocate every event before any is sent, including all release events."""
    parts = normalize_shortcut(shortcut).split('+')
    modifiers = parts[:-1]
    masks = {'Cmd': quartz.kCGEventFlagMaskCommand, 'Ctrl': quartz.kCGEventFlagMaskControl,
             'Alt': quartz.kCGEventFlagMaskAlternate, 'Shift': quartz.kCGEventFlagMaskShift}
    if 'Fn' in modifiers:
        masks['Fn'] = quartz.kCGEventFlagMaskSecondaryFn
    flags, plan, pressed = 0, [], []
    for modifier in modifiers:
        code, device_mask = MODIFIER_KEYS[modifier]
        pressed.append((code, flags))
        flags |= masks[modifier] | device_mask
        plan.append((code, True, flags))
    code = KEY_CODES[parts[-1]]
    plan.extend([(code, True, flags), (code, False, flags)])
    plan.extend((code, False, before) for code, before in reversed(pressed))
    events = []
    releases = {}
    for code, down, flags in plan:
        # CoreGraphics creates FlagsChanged for physical modifier key codes.
        event = quartz.CGEventCreateKeyboardEvent(None, code, down)
        if event is None:
            raise SceneActionError('key_allocation_failed')
        # CoreGraphics adds key-identity flags: F5 has SecondaryFn, arrows
        # also have NumericPad. Replacing them with just Shift/Cmd produces
        # a different native key event even though the virtual key is correct.
        # Preserve only these two bits; never inherit unrelated held modifiers.
        key_flags = (quartz.CGEventGetFlags(event)
                     & (quartz.kCGEventFlagMaskSecondaryFn | quartz.kCGEventFlagMaskNumericPad)
                     if code == KEY_CODES[parts[-1]] else 0)
        quartz.CGEventSetFlags(event, flags | key_flags)
        quartz.CGEventSetIntegerValueField(event, quartz.kCGEventSourceUserData, EVENT_TAG)
        events.append((code, down, event))
        if not down:
            releases[code] = event
    return events, releases, modifiers


def post_chord(quartz, pid, events, releases, diagnostic):
    """No retries. On a partial failure, release only possibly pressed keys."""
    held = []
    try:
        for code, down, event in events:
            if down:
                held.append(code)  # A throwing post may still have reached the app.
            quartz.CGEventPostToPid(pid, event)
            diagnostic['events_posted'] += 1
            if not down:
                held.remove(code)
    except Exception:
        diagnostic.update(cleanup_events_posted=0, cleanup_failures=0)
        for code in reversed(held):
            try:
                quartz.CGEventPostToPid(pid, releases[code])
                diagnostic['cleanup_events_posted'] += 1
            except Exception:
                diagnostic['cleanup_failures'] += 1
        raise
