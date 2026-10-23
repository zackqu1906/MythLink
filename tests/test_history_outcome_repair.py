"""Session attribution and evidence-based recovery of false failure labels."""
import json

import pytest

from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_history_management import add_record
from proximic_ring.text_processing import LLMSettings, TextProcessingRequest


def legacy_record(tmp_path, *, action='applied', error='输入法未连接到当前文本框，请重新点入文本框'):
    store = ModificationDatasetCollector(tmp_path, 'test')
    identity = add_record(store, 1, '已成功输入的原文')
    store.record_application(session_id=1, action=action, mode='dictation',
                             method='input_method', final_text='已成功输入的结果')
    store.record_application(session_id=1, action='apply_failed', mode='dictation',
                             method='input_method', error=error, final_text='')
    return store, identity


@pytest.mark.parametrize('action', ['applied', 'undone', 'cancelled', 'native_undo_sent'])
def test_proven_terminal_outcome_survives_old_unrelated_startup_errors(tmp_path, action):
    store, identity = legacy_record(tmp_path, action=action)
    folder = store._interaction_dir(identity)
    files = {name: (folder/name).read_bytes() for name in ('audio.wav', 'events.jsonl', 'asr_updates.jsonl')}
    recovered = ModificationDatasetCollector(tmp_path, 'test')
    entry = recovered.load_entries()[0]
    assert entry['outcome'] == action and not entry['error']
    assert entry['candidateText'] == ('已成功输入的结果' if action == 'applied' else '')
    record = json.loads((folder/'record.json').read_text())
    assert record['history_repairs'][0]['previous_outcome']['status'] == 'apply_failed'
    assert record['mode']['final_applied'] == ('dictation' if action == 'applied' else '')
    assert all((folder/name).read_bytes() == data for name, data in files.items())
    saved = (folder/'record.json').read_bytes()
    assert ModificationDatasetCollector(tmp_path, 'test').load_entries() == [entry]
    assert (folder/'record.json').read_bytes() == saved  # Idempotent on restart.


@pytest.mark.parametrize('error', ['输入框未确认选区或替换结果，已停止本次操作',
                                  '模型请求超时', '输入法未确认定稿，已停止本句后续处理'])
def test_real_application_and_model_failures_are_preserved(tmp_path, error):
    store, identity = legacy_record(tmp_path, error=error)
    before = store._interaction_path(identity).read_bytes()
    assert ModificationDatasetCollector(tmp_path, 'test').load_entries()[0]['outcome'] == 'apply_failed'
    assert store._interaction_path(identity).read_bytes() == before


def test_a_new_edit_after_success_does_not_inherit_that_success(tmp_path):
    store, identity = legacy_record(tmp_path)
    events = store._read_jsonl(store._interaction_dir(identity)/'events.jsonl')
    events.insert(-1, dict(type='mode_intent', mode='edit'))
    store._write_jsonl(store._interaction_dir(identity)/'events.jsonl', events)
    assert ModificationDatasetCollector(tmp_path, 'test').load_entries()[0]['outcome'] == 'apply_failed'


def test_missing_success_evidence_never_invents_success(tmp_path):
    store, identity = legacy_record(tmp_path)
    store._write_jsonl(store._interaction_dir(identity)/'events.jsonl', [])
    assert ModificationDatasetCollector(tmp_path, 'test').load_entries()[0]['outcome'] == 'apply_failed'
    (store._interaction_dir(identity)/'events.jsonl').write_text('{invalid-json')
    assert ModificationDatasetCollector(tmp_path, 'test').load_entries()[0]['outcome'] == 'apply_failed'


@pytest.mark.parametrize('action', ['cancelled', 'undone', 'native_undo_sent'])
def test_model_job_cleanup_cannot_replace_a_cancelled_or_undone_input(tmp_path, action):
    store = ModificationDatasetCollector(tmp_path, 'test')
    identity = add_record(store, 1)
    store.record_text_request(TextProcessingRequest(request_id=10, session_id=1,
        mode='edit', raw_text='改写', settings=LLMSettings()))
    store.record_application(session_id=1, action=action, mode='edit')
    store.abandon_request(10, 'utterance cancelled by user')
    assert store.load_entries()[0]['outcome'] == action
    events = store._read_jsonl(store._interaction_dir(identity)/'events.jsonl')
    assert events[-1]['preserves_application_outcome']
    # Emulate the old summary, then verify startup recovery uses the log proof.
    record = store._interaction_data(identity)
    record['outcome']['status'] = 'abandoned'
    store._write_json(store._interaction_path(identity), record)
    assert ModificationDatasetCollector(tmp_path, 'test').load_entries()[0]['outcome'] == action
