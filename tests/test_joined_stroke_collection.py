import copy
import json
from pathlib import Path
import tkinter as tk

import pytest


@pytest.fixture
def study(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    import joined_stroke_collection
    return joined_stroke_collection


@pytest.fixture(scope='module')
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_plan_is_balanced_and_preserves_valid_code_variants(study):
    tasks = study.inventory()
    assert len(tasks) == 13
    joined = [t for t in tasks if t['kind'] == 'joined']
    assert len(joined) == 9
    assert {t['target'] for t in joined} == {'walk'}
    for task in tasks:
        assert all(len(code) == task['expectedLogicalStrokes'] for code in task['expectedCodes'])
    assert all(t['expectedCodes']==['zn'] and t['expectedLogicalStrokes']==2 for t in joined)
    assert {t['contextCharacter'] for t in joined} == {'过','近','这'}
    for phase in ('train', 'evaluation'):
        session = study.new_session('P01', phase=phase)
        assert session['version'] == 1 and session['planVersion'] == 3
        assert len(session['tasks']) == 26
        for offset in (0, 13):
            assert {t['label'] for t in session['tasks'][offset:offset+13]} == {t['label'] for t in tasks}
        assert study.validate_session(session) is session
    for rounds in (0, 31, True, 1.5):
        with pytest.raises(ValueError):
            study.new_session('P01', rounds=rounds)


def test_pie_dian_keeps_one_and_two_stroke_labels_and_legacy_resume(study):
    from check_selection_capture import trace
    tasks = study.inventory(2)
    yi = [t for t in tasks if t.get('target') == 'yi-pie-dian']
    assert len(yi) == 3 and all(t['expectedCodes'] == ['pn'] for t in yi)
    assert next(t for t in tasks if t.get('target') == 'zi')['expectedCodes'] == ['zs', 'zz']
    controls = [t for t in tasks if t.get('shape') == '撇点']
    assert len(controls) == 2
    assert all(t['expectedLogicalStrokes'] == 1 and t['expectedCodes'] == ['z'] for t in controls)
    assert any(t.get('variant') == 'small' for t in controls)
    # No unconfirmed radical families were silently added.
    assert {t['target'] for t in tasks if t.get('target')} == {'fan', 'de', 'le', 'zi', 'yi-pie-dian'}
    legacy = study.new_session('P01', rounds=1)
    legacy['planVersion'] = 1
    legacy['tasks'] = [{**t, 'round': 1} for t in study.inventory(1)]
    assert len(legacy['tasks']) == 24
    assert len([t for t in legacy['tasks'] if t['kind'] == 'joined']) == 9
    legacy['samples'] = [{**trace(), 'taskIndex': 0, 'task': legacy['tasks'][0]}]
    legacy['draft'] = trace()
    original = copy.deepcopy(legacy)
    study.validate_session(legacy)
    assert legacy == original
    changed = copy.deepcopy(legacy)
    changed['planVersion'] = 2
    with pytest.raises(ValueError):
        study.validate_session(changed)
    legacy['planVersion']=2
    legacy['tasks']=[{**t,'round':1} for t in study.inventory(2)]
    legacy['samples']=[]
    assert len(study.validate_session(legacy)['tasks'])==35


def test_resume_rejects_relabeling_repetition_and_wrong_samples(study):
    from check_selection_capture import trace
    session = study.new_session('P01', rounds=1)
    session['draft'] = trace()
    before = copy.deepcopy(session)
    study.validate_session(session)
    assert session == before
    for mutate in (
        lambda s: s['tasks'][0].update(expectedCodes=['h']),
        lambda s: s['tasks'].__setitem__(1, copy.deepcopy(s['tasks'][0])),
        lambda s: s['samples'].append({**trace(), 'taskIndex': 1, 'task': s['tasks'][0]}),
    ):
        broken = copy.deepcopy(before)
        mutate(broken)
        with pytest.raises(ValueError):
            study.validate_session(broken)


def test_predictions_do_not_use_prompt_and_stationary_contacts_survive(study):
    from check_selection_capture import trace
    sample = trace()
    joined = next(t for t in study.inventory() if t['kind'] == 'joined')
    control = next(t for t in study.inventory() if t['kind'] == 'fold-control')
    assert study.sample_annotations(sample, joined) == study.sample_annotations(sample, control)
    stationary = copy.deepcopy(sample)
    for point in stationary['points']:
        point['x'] = point['y'] = 0
    assert study.sample_annotations(stationary, joined)['singleStrokeBaseline'] is None


def test_targeted_plans_have_full_silk_controls_and_replay_examples(study):
    from build_joined_stroke_model import replay_example, training_task
    from proximic_ring.touchpad.stroke.stroke_joined import sample_family
    from proximic_ring.touchpad.stroke.stroke_joined_components import component_registry
    silk = study.collection_profile('silk')
    session = silk.new_session('P01', rounds=silk.TRAIN_ROUNDS)
    assert study.validate_session(session) is session
    assert len(session['tasks']) == 48
    full = [t for t in session['tasks'] if t.get('target') == 'silk-whole']
    assert len(full) == 42 and {tuple(t['expectedCodes']) for t in full} == {('zzh',)}
    assert sum(t['kind'] == 'fold-control' for t in session['tasks']) == 6
    additional = study.inventory(7)
    hou = [t for t in additional if t.get('contextCharacter') == '后']
    assert len(additional) == 12 and len(hou) == 2
    assert all(t['target'] == 'fan' and t['expectedCodes'] == ['pp'] for t in hou)
    for task in study.inventory(6) + additional:
        if task['kind'] == 'joined':
            family = sample_family({'task': task})
            assert replay_example(task, family, component_registry()['positions'])[0] == task['contextCharacter']

    old_silk = next(t for t in study.inventory(4) if t.get('target') == 'silk-head')
    old_hou = next(t for t in study.inventory(5) if t.get('target') == 'hou-middle')
    assert training_task(old_silk)['expectedLogicalStrokes'] == 3 and old_silk['expectedCodes'] == ['zz']
    assert training_task(old_hou) is None
    assert training_task(old_hou, hou_head_confirmed=True)['expectedCodes'] == ['pp']
    assert old_hou['expectedCodes'] == ['ph']


@pytest.mark.parametrize('plan,version,total', [('silk', 6, 48), ('additional', 7, 24)])
def test_joined_collector_uses_raw2_and_saves_full_sdk_contact(study, tmp_path, monkeypatch, tk_root, plan, version, total):
    import asyncio
    from types import SimpleNamespace
    import ring_python_sdk
    from ring_python_sdk import ble
    from ring_python_sdk.touchpad.events import Raw2Reset, TouchpadContact, TouchpadMove, TouchpadStats
    from ring_selection_collector import CollectionWindow, RingWorker
    from selection_capture import integration_error

    calls = []
    session = SimpleNamespace(
        target_name='Ringo test', target_address='offline', rx_uuid='rx',
        _sdk_transport=SimpleNamespace(device_info=SimpleNamespace(
            firmware_version='2.1.6-dev.touchpad.15', hardware_revision=1)),
        touchpad_active=False, client=object())
    args = {}

    async def connect(device):
        calls.append(('connect', device))
        return True

    async def mic_off(client, rx_uuid, *, on):
        calls.append(('mic', on))

    async def swipe_off(client, rx_uuid):
        calls.append(('swipe', 'stop'))

    async def touchpad_on(**kwargs):
        args.update(kwargs)
        session.touchpad_active = True
        args['on_event'](Raw2Reset('reset', 0, 0., generation=1))
        args['on_stats'](TouchpadStats(ready=True, packets=1, warmup_frames=0))
        worker.stop.set()

    async def touchpad_off():
        calls.append('touchpad_off')
        session.touchpad_active = False

    async def disconnect():
        calls.append('disconnect')

    def ring_session(device, timeout, *, connection_backend):
        assert connection_backend == 'sdk'
        return session

    session.connect_target = connect
    session.touchpad_on = touchpad_on
    session.touchpad_off = touchpad_off
    session.disconnect = disconnect
    monkeypatch.setattr(ring_python_sdk, 'RingSession', ring_session)
    monkeypatch.setattr(ble, 'send_mic_control', mic_off)
    monkeypatch.setattr(ble, 'send_swipe_stop', swipe_off)

    root = tk.Toplevel(tk_root)
    root.withdraw()
    try:
        profile = study.collection_profile(plan)
        window = CollectionWindow(root, tmp_path, profile=profile, protocol='raw2')
        window.install(profile.new_session('P01', rounds=profile.TRAIN_ROUNDS, device='Ringo test'))
        assert window.session['planVersion'] == version
        assert len(window.session['tasks']) == total
        worker = RingWorker('Ringo test', tmp_path, window.messages, protocol='raw2')
        asyncio.run(worker.acquire())
        assert calls == [('connect', 'Ringo test'), ('mic', False), ('swipe', 'stop'),
                         'touchpad_off', 'disconnect']
        assert args['protocol'] == 'raw2' and args['on_tokens'] is None
        window.poll()
        assert window.warm and window.connected and window.token_stream is None
        assert 'Raw2 就绪' in window.stats.get()
        assert window.session['deviceInfo']['protocol'] == 'raw2'
        window.arm()
        # Down and up are confirmed after their original boundary frame.
        for step in range(1, 34):
            if step == 8:
                args['on_event'](TouchpadContact('down', 6, .04, 8))
            args['on_event'](TouchpadMove(1. if step < 30 else 100., -.5, .9, step, step*.005))
        args['on_event'](TouchpadContact('up', 30, .17, 34))
        window.poll()
        window.save_sample()
        restored = profile.validate_session(json.loads(window.path.read_text(encoding='utf-8')))
        saved = restored['samples'][0]
        assert saved['touchpadProtocol'] == 'raw2'
        assert (saved['startStep'], saved['endStep']) == (6, 30)
        assert saved['points'][-1]['x'] == 23.
        assert integration_error(saved) == 0.
        window.install(restored)
        assert len(window.session['samples']) == 1 and window.session['tasks'][0] == saved['task']
        window.raw_stream.close()
    finally:
        root.destroy()


@pytest.mark.parametrize('plan_version', (3, 4, 5))
def test_capture_save_retry_resume_and_audit(study, tmp_path, tk_root, plan_version):
    from ring_selection_collector import CollectionWindow
    from check_selection_capture import contact, move
    from analyze_joined_strokes import summarize
    root = tk.Toplevel(tk_root)
    root.withdraw()
    try:
        window = CollectionWindow(root, tmp_path, profile=study)
        session = study.new_session('P01', rounds=1, plan_version=plan_version)
        window.install(session)
        window.connected = window.warm = True
        window.arm()
        window.event(contact('down', 10))
        for step in range(10, 40):
            window.event(move(step, -1 if step < 24 else 1, 1))
        window.event(contact('up', 40))
        assert window.session['draft'] is not None
        assert len(window.session['draft']['rawEvents']) == 32
        window.event(dict(kind='click_verdict', start_step=10, step=40, timestamp=.205, is_click=True))
        window.save_sample()
        restored = study.validate_session(json.loads(window.path.read_text(encoding='utf-8')))
        saved = restored['samples'][0]
        assert saved['singleStrokeBaseline']['recognizer'] == 'dtw_v1'
        assert saved['sdkClickVerdict'] is True
        assert 'strokes.json.gz' in saved['baselineSource']['assetSha256']
        assert restored['tasks'] == session['tasks']
        report = summarize([window.path])
        assert report['sessions'][0]['saved'] == 1
        assert report['groups'][0]['count'] == 1
        with pytest.raises(ValueError, match='sessionId'):
            summarize([window.path, window.path])
        window.install(restored)
        assert len(window.session['samples']) == 1
        window.arm()
        window.event(contact('down', 100))
        for step in range(100, 110):
            window.event(move(step, 0, 0))
        window.event(contact('up', 110))
        assert window.armed and window.session['draft'] is None
        assert '没有位移' in window.status.get()
        window.event(contact('down', 110))
        for step in range(110, 120):
            window.event(move(step, 1, 1))
        window.event(contact('up', 120))
        assert window.session['draft']['pathLength'] > 0
        window.retry()
        assert len(window.session['rejected']) == 1 and window.armed and window.session['draft'] is None
        legacy = study.new_session('P01', rounds=1)
        legacy['planVersion'] = 1
        legacy['tasks'] = [{**t, 'round': 1} for t in study.inventory(1)]
        window.install(study.validate_session(legacy))
        assert '每轮 24 笔' in window.plan_help.get()
        assert len(window.session['tasks']) == 24
        window.raw_stream.close()
    finally:
        root.destroy()
