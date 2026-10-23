"""Page entry feedback is independent from shortcut focus and per-page deduped."""
from dataclasses import replace
from types import SimpleNamespace
import sys
import pytest

from proximic_ring.scene_capabilities import BROWSERS
from test_scene_notice import notice, observe, entered
from test_browser_scene_unification import Node, read, page, player, scene
from test_presentation_portability import native_backend


@pytest.fixture
def browser_notice(notice):
    obj, state, logs, calls = notice
    state.front['value'] = 'com.apple.Safari'
    obj.catalog._apps = {'com.apple.Safari': {'label': 'Safari'}}
    obj.catalog.configured_scenes = lambda: {'com.apple.Safari': ['video', 'pdf', 'image', 'music']}
    return obj, state, logs, calls


def sample(notice, mode='video', token='page-a', **kwargs):
    obj, state, *_ = notice
    observe(obj, state, mode, bundle=state.front['value'], page_token=token, **kwargs)


@pytest.mark.parametrize('bundle', sorted(BROWSERS))
def test_switching_between_same_scene_pages_announces_each_entry(browser_notice, bundle):
    obj, state, logs, _ = browser_notice
    state.front['value'] = bundle
    obj.catalog._apps = {bundle: {'label': 'Browser'}}
    obj.catalog.configured_scenes = lambda: {bundle: ['video', 'pdf', 'image', 'music']}
    for token in ['page-a', 'page-b', 'page-a']:
        sample(browser_notice, token=token)
        assert not obj.visible
        sample(browser_notice, token=token)
        assert obj.visible
        obj.hide()
        sample(browser_notice, token=token)
        assert not obj.visible
    assert len(entered(logs)) == 3


@pytest.mark.parametrize('context', ['nontext', 'unknown', 'text'])
def test_confirmed_page_entry_does_not_wait_for_clicking_player(browser_notice, context):
    obj, _, logs, _ = browser_notice
    sample(browser_notice, input_context=context); sample(browser_notice, input_context=context)
    assert obj.visible and len(entered(logs)) == 1
    obj.hide()
    for context in ['text', 'unknown', 'nontext']:
        sample(browser_notice, input_context=context)
    assert not obj.visible and len(entered(logs)) == 1


def test_navigation_and_delayed_loading_do_not_combine_different_pages(browser_notice):
    obj, _, logs, _ = browser_notice
    sample(browser_notice, token='a')
    sample(browser_notice, token='b')
    assert not obj.visible
    sample(browser_notice, '', token='b', diagnostic={'reason': 'page_loading'})
    sample(browser_notice, token='b')
    assert not obj.visible
    sample(browser_notice, token='b')
    assert obj.visible and len(entered(logs)) == 1


def test_temporary_loading_or_hidden_controls_do_not_repeat_entry(browser_notice):
    obj, state, logs, _ = browser_notice
    sample(browser_notice); sample(browser_notice); obj.hide()
    for token, facts in [('', {}), ('page-a', {'reason': 'page_loading'}),
                        ('page-a', {'recognition': {'reason': 'no_web_player', 'player_count': 1}}),
                        ('page-a', {'recognition': {'reason': 'no_web_player', 'player_count': 0,
                                                   'scan_limited': False, 'scanned_nodes': 1}})]:
        for _ in range(4):
            state.now += .4
            sample(browser_notice, '', token=token, diagnostic=facts)
        sample(browser_notice); sample(browser_notice)
        assert not obj.visible and len(entered(logs)) == 1


def test_confirmed_same_page_scene_removal_and_new_scene_still_announce(browser_notice):
    obj, state, logs, _ = browser_notice
    sample(browser_notice); sample(browser_notice); obj.hide()
    no_content = {'recognition': {'reason': 'no_web_player', 'source': 'extension', 'authorized': True}}
    sample(browser_notice, '', diagnostic=no_content)
    state.now += 1.3
    sample(browser_notice, '', diagnostic=no_content)
    assert obj._active is None
    sample(browser_notice); sample(browser_notice)
    assert obj.visible and len(entered(logs)) == 2
    sample(browser_notice, 'pdf'); sample(browser_notice, 'pdf')
    assert obj.notice['scene'] == 'pdf' and len(entered(logs)) == 3


@pytest.mark.parametrize('case', ['blocked', 'missing_identity', 'disabled_scene'])
def test_feedback_never_announces_unverified_or_disabled_pages(browser_notice, case):
    obj, _, logs, _ = browser_notice
    kwargs = {'blocked': True} if case == 'blocked' else {'token': ''} if case == 'missing_identity' else {}
    if case == 'disabled_scene': obj.catalog.configured_scenes = lambda: {'com.apple.Safari': ['pdf']}
    sample(browser_notice, **kwargs); sample(browser_notice, **kwargs)
    assert not obj.visible and not entered(logs)


def test_browser_poll_is_faster_and_remains_single_flight(browser_notice, monkeypatch):
    import proximic_ring.ui.scenes.scene_notice_controller as module
    obj, state, _, _ = browser_notice
    pending = []
    monkeypatch.setattr(module.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda: pending.append(kw['target'])))
    obj.poll(); obj.poll()
    assert obj._poll_timer.interval() == 400 and len(pending) == 1
    state.front['value'] = 'test.native'
    obj.poll()
    assert obj._poll_timer.interval() == 1000


def test_worker_page_token_distinguishes_same_url_tabs_but_not_player_focus():
    from proximic_ring.platforms.macos.native_access_worker import Dispatcher
    from proximic_ring.desktop.app_shortcuts import ShortcutTarget
    worker = Dispatcher()
    target = ShortcutTarget('com.apple.Safari', 12, 'browser', window=object(),
                            web_area=object(), page_key='same-url-hash', player=object())
    first = worker._page_token(target)
    assert first == worker._page_token(replace(target, focus=object(), player=object()))
    assert worker._page_token(replace(target, page_key='')) == ''
    assert first == worker._page_token(target)
    second = worker._page_token(replace(target, web_area=object()))
    assert first != second
    assert worker._page_token(target) not in {first, second}
    native_pdf = replace(target, page_key='native-pdf')
    assert worker._page_token(native_pdf) != worker._page_token(replace(native_pdf, player=object()))
    assert not worker.targets
