from types import SimpleNamespace
import pytest

from proximic_ring.voice.ime.input_context_key import post_context_key
from proximic_ring.voice.ime.input_method_install import BUNDLE_ID


@pytest.fixture(autouse=True)
def fake_native_event_factory(monkeypatch):
    monkeypatch.setattr('proximic_ring.voice.ime.input_context_key._function_key_event',
                        lambda down: dict(code=90, down=down))


def backend():
    data = dict(front=('mail', 504), source=BUNDLE_ID + '.dictation', flags=0, posts=[])
    q = SimpleNamespace(
        CGPreflightPostEventAccess=lambda: True,
        kCGEventSourceStateHIDSystemState=1, kCGEventSourceUserData=42,
        kCGEventFlagMaskSecondaryFn=0x800000,
        CGEventSourceFlagsState=lambda _: data['flags'],
        CGEventSetFlags=lambda event, flags: event.update(flags=flags),
        CGEventKeyboardSetUnicodeString=lambda event, length, text: event.update(unicode=text),
        CGEventSetIntegerValueField=lambda event, field, tag: event.update(tag=tag),
        CGEventPostToPid=lambda pid, event: data['posts'].append((pid, event)),
    )
    return SimpleNamespace(quartz=q, foreground=lambda: data['front'],
                           current=lambda **_: data['source']), data


def test_nonprinting_key_is_tagged_balanced_and_pinned_to_original_pid():
    b, data = backend()
    post_context_key(b, ('mail', 504), 123, timeout=.2)
    assert data['posts'] == [(504, dict(code=90, down=down, flags=0x800000, tag=123, unicode='\uf717'))
                             for down in (True, False)]


@pytest.mark.parametrize('change', ['front', 'source', 'flags', 'permission'])
def test_changed_target_or_held_modifiers_prevent_any_key(change):
    b, data = backend()
    if change == 'permission':
        b.quartz.CGPreflightPostEventAccess = lambda: False
    else:
        data[change] = {'front': ('other', 505), 'source': 'ABC', 'flags': 0x20000}[change]
    with pytest.raises(RuntimeError):
        post_context_key(b, ('mail', 504), 123, timeout=.2)
    assert data['posts'] == []


def test_posting_failure_still_releases_key_to_same_pid():
    b, data = backend()
    def post(pid, event):
        data['posts'].append((pid, event))
        if event['down']:
            data['front'] = ('other', 505)
            raise RuntimeError('post failed')
    b.quartz.CGEventPostToPid = post
    with pytest.raises(RuntimeError):
        post_context_key(b, ('mail', 504), 123, timeout=.2)
    assert [(pid, event['down']) for pid, event in data['posts']] == [(504, True), (504, False)]
