import threading
from types import SimpleNamespace
import pytest

from proximic_ring.voice.ime.context_preparation import InputContextPreparation

ORIGIN = ('com.openai.codex', 42)


def fixture():
    state = dict(epoch='e', client_id='old', utterance_id='', application=ORIGIN[0],
                 ready=True, selection=[3, 0], context_key_supported=True)
    data = dict(state=state, front=ORIGIN, reads=0, refreshes=0)
    def read(**_):
        data['reads'] += 1
        return dict(data['state'])
    bridge = SimpleNamespace(epoch='e', connected=True, focus_state=read)
    def refresh(bridge, origin, **_):
        data['refreshes'] += 1
        data['state'] = {**state, 'client_id': 'fresh', 'selection': [6, 0]}
        return True
    return bridge, data, refresh


@pytest.mark.parametrize('selection', [[3, 0], [-1, -1]])
def test_prepare_rebinds_from_fresh_metadata_before_any_body_read(selection):
    bridge, data, refresh = fixture()
    data['state']['selection'] = selection
    prepare = InputContextPreparation(bridge, refresh=refresh)
    result = prepare.prepare(ORIGIN, foreground=lambda: data['front'], known_state=data['state'])
    assert result.outcome == 'refreshed'
    assert result.state['client_id'] == 'fresh' and result.state['selection'] == [6, 0]
    assert data['refreshes'] == 1 and data['reads'] == 1
    assert result.diagnostics['client_changed']


@pytest.mark.parametrize('change', ['foreground', 'epoch', 'disconnect', 'wrong_client_app'])
def test_prepare_never_returns_a_target_from_a_different_context(change):
    bridge, data, _ = fixture()
    def refresh(*args, **kwargs):
        if change == 'foreground': data['front'] = ('other', 43)
        elif change == 'epoch': bridge.epoch = 'next'
        elif change == 'disconnect': bridge.connected = False
        else: data['state']['application'] = 'other'
        return True
    result = InputContextPreparation(bridge, refresh=refresh).prepare(ORIGIN, foreground=lambda: data['front'])
    assert result.state is None


@pytest.mark.parametrize('raises', [False, True])
def test_failed_handshake_still_allows_fresh_unknown_range_client(raises):
    bridge, data, _ = fixture()
    data['state']['selection'] = [-1, -1]
    def refresh(*args, **kwargs):
        if raises: raise TimeoutError
        return False
    result = InputContextPreparation(bridge, refresh=refresh).prepare(ORIGIN, foreground=lambda: ORIGIN)
    assert result.outcome == 'continued_without_receipt' and result.state['selection'] == [-1, -1]
    assert data['reads'] == 2


def test_live_composition_metadata_skips_key_and_preserves_target():
    bridge, data, refresh = fixture()
    data['state'].update(utterance_id='live', context_key_supported=False)
    result = InputContextPreparation(bridge, refresh=refresh).prepare(ORIGIN, foreground=lambda: ORIGIN)
    assert result.outcome == 'not_permitted' and result.state['utterance_id'] == 'live'
    assert data['refreshes'] == 0


@pytest.mark.parametrize('succeeds', [False, True])
def test_overlapping_readers_coalesce_only_after_same_target_is_revalidated(succeeds):
    bridge, data, refresh = fixture()
    refreshing, release, second_seen = threading.Event(), threading.Event(), threading.Event()
    def delayed_refresh(*args, **kwargs):
        refreshing.set()
        assert release.wait(1)
        refresh(*args, **kwargs)
        return succeeds
    def foreground():
        if threading.current_thread().name == 'second-reader': second_seen.set()
        return ORIGIN
    preparation = InputContextPreparation(bridge, refresh=delayed_refresh)
    results = []
    first = threading.Thread(target=lambda: results.append(preparation.prepare(ORIGIN, foreground=foreground, timeout=2)))
    second = threading.Thread(name='second-reader', target=lambda: results.append(
        preparation.prepare(ORIGIN, foreground=foreground, timeout=2)))
    first.start(); assert refreshing.wait(1)
    second.start(); assert second_seen.wait(1)
    release.set(); first.join(2); second.join(2)
    expected = ['coalesced', 'refreshed'] if succeeds else ['already_attempted', 'continued_without_receipt']
    assert sorted(r.outcome for r in results) == expected
    assert all(r.state['client_id'] == 'fresh' for r in results) and data['refreshes'] == 1


def test_unanswered_handshake_has_short_budget_and_keeps_unknown_range():
    bridge, data, _ = fixture()
    data['state']['selection'] = [-1, -1]
    budgets = []
    def refresh(*_, timeout):
        budgets.append(timeout)
        return False
    result = InputContextPreparation(bridge, refresh=refresh).prepare(ORIGIN, foreground=lambda: ORIGIN)
    assert result.state['selection'] == [-1, -1]
    assert len(budgets) == 1 and budgets[0] <= .12
