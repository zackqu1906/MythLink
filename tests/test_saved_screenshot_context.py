"""Saved crop processing, replacement races, request waiting and durable evidence."""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import json
import threading
from types import SimpleNamespace
import zipfile

import pytest

from proximic_ring.screenshots.context import (
    CLEANUP_PROMPT, SavedScreenshotContext, resolve_saved_context, validate_cleanup)
from proximic_ring.text_processing.model import (
    INPUT_ROUTING_ROUTED_EDIT, ROUTED_EDIT_BOUNDARY, TextProcessingRequest)
from proximic_ring.text_processing.processing_dispatch import process_text_request
from proximic_ring.ui.voice.edit_replay import frozen_edit_request
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector
from test_routed_edit import settings, processor_returning


@pytest.fixture
def saved(tmp_path):
    stores = []
    def make(*, ocr=lambda png: png.decode(), processor=None):
        store = SavedScreenshotContext(tmp_path / 'slot', processor=processor or SimpleNamespace(
            _request_chat=lambda settings, **kw: kw['user_content']), ocr=ocr)
        stores.append(store)
        return store
    yield make
    for store in stores:
        store.close()


def submit(store, tmp_path, text):
    path = tmp_path / 'confirmed.png'
    path.write_bytes(text.encode())
    return store.submit(path, settings())


def edit_request(context):
    return TextProcessingRequest(1, 7, 'edit', '把刚才保存的内容整理成回复', settings(), '原文',
        routing_mode=INPUT_ROUTING_ROUTED_EDIT, routed_edit_variant=ROUTED_EDIT_BOUNDARY,
        screen_context={'status': 'ok', 'context': '当前屏幕'}, saved_context=context)


def test_cleanup_keeps_verbatim_content_and_separate_evidence(saved, tmp_path):
    processor, calls = processor_returning('完整内容：周二 10:00 开会。\n确认后再发布。')
    store = saved(processor=processor)
    value = submit(store, tmp_path, '……被截断的\n完整内容：周二 10:00 开会。\n确认后再发布。').result(3)
    assert value['status'] == 'ok' and value['text'] == '完整内容：周二 10:00 开会。\n确认后再发布。'
    assert len(calls) == 1 and calls[0]['thinking'] == {'type': 'disabled'}
    assert calls[0]['input'][0]['content'][0]['text'] == CLEANUP_PROMPT
    assert value.evidence['saved_screenshot']['ocr_text'].startswith('……被截断的')
    assert 'screenshot_png' not in json.dumps(value) and 'cleanup' not in json.dumps(value)
    persisted = json.loads((store.root / 'current.json').read_text())
    assert persisted['text'] == value['text'] and 'api_key' not in json.dumps(persisted)


@pytest.mark.parametrize('changed', ['时间改成周三。', '周二开会。', '周二 不开会。\n标题', '```\n标题\n```'])
def test_rewrite_or_reordering_is_rejected(changed):
    with pytest.raises(ValueError, match='改动了原文'):
        validate_cleanup('标题\n周二 不开会。', changed)


def test_newest_capture_wins_when_old_processing_finishes_last(saved, tmp_path):
    entered, release = threading.Event(), threading.Event()
    def ocr(png):
        if png == b'old':
            entered.set()
            assert release.wait(3)
        return png.decode()
    store = saved(ocr=ocr)
    first = submit(store, tmp_path, 'old')
    try:
        assert entered.wait(2)
        latest = submit(store, tmp_path, 'new')
        new = latest.result(3)
        assert store.snapshot() is latest and new['text'] == 'new'
    finally:
        release.set()
    old = first.result(3)
    assert old['text'] == 'old' and old.evidence['screenshot_png'] == b'old'
    assert store.record()['text'] == 'new'
    assert len(list(store.root.glob('*.png'))) == 1
    assert next(store.root.glob('*.png')).read_bytes() == b'new'
    restored = saved()
    assert restored.snapshot().result()['text'] == 'new'
    assert restored.snapshot().result().evidence['screenshot_png'] == b'new'


@pytest.mark.parametrize('failure', ['ocr', 'llm', 'rewrite', 'empty'])
def test_new_failure_or_empty_never_reuses_old(saved, tmp_path, failure):
    store = saved()
    assert submit(store, tmp_path, '旧内容').result(3)['status'] == 'ok'
    def fail(*args, **kwargs):
        raise RuntimeError('测试失败')
    if failure == 'ocr':
        store._ocr = fail
    else:
        store._processor = SimpleNamespace(_request_chat=(fail if failure == 'llm' else
            lambda *a, **kw: '改写后的内容' if failure == 'rewrite' else ''))
    value = submit(store, tmp_path, '新内容').result(3)
    assert value['status'] == ('empty' if failure == 'empty' else 'unavailable')
    assert value['text'] == '' and value['reason']
    assert saved().snapshot().result()['status'] == value['status']
    assert saved().snapshot().result()['text'] == ''


def test_interrupted_capture_is_unavailable_after_restart(saved, tmp_path):
    store = saved()
    value = submit(store, tmp_path, '完整内容').result(3)
    record = store.record()
    record.update(status='processing', text='')
    (store.root / 'current.json').write_text(json.dumps(record))
    restored = saved().snapshot().result()
    assert restored['status'] == 'unavailable' and '中断' in restored['reason']
    assert restored['capture_id'] == value['capture_id']


def test_missing_saved_image_is_reported_as_unavailable(saved, tmp_path):
    store = saved()
    submit(store, tmp_path, '完整内容').result(3)
    next(store.root.glob('*.png')).unlink()
    value = saved().snapshot().result()
    assert value['status'] == 'unavailable' and value['text'] == ''
    assert '文件已不可用' in value['reason']


def test_failed_replacement_does_not_reload_previous_slot(saved, tmp_path, monkeypatch):
    store = saved()
    submit(store, tmp_path, '旧内容').result(3)
    def fail(record):
        raise OSError('磁盘不可用')
    monkeypatch.setattr(store, '_persist', fail)
    value = submit(store, tmp_path, '新内容').result(3)
    assert value['status'] == 'unavailable' and value['text'] == ''
    assert saved().snapshot().result() == {}


def test_edit_waits_for_pinned_context_and_retry_keeps_it(saved, tmp_path):
    pending = Future()
    request = edit_request(pending)
    processor, calls = processor_returning('编辑结果')
    started = threading.Event()
    def process():
        started.set()
        return process_text_request(processor, request, on_trace=lambda *a: None)
    store = saved()
    value = submit(store, tmp_path, '当时保存的文字').result(3)
    with ThreadPoolExecutor() as pool:
        job = pool.submit(process)
        assert started.wait(2)
        assert not job.done() and not calls
        pending.set_result(value)
        result = job.result(3)
    assert result.model_input['saved_context'] == '当时保存的文字'
    assert result.model_input['screen_context']['context'] == '当前屏幕'
    assert len(calls) == 1 and 'screenshot_png' not in json.dumps(result.model_input)
    frozen = frozen_edit_request(request, SimpleNamespace(error=None, model_input=result.model_input))
    submit(store, tmp_path, '后来保存的文字').result(3)
    assert frozen.saved_context['text'] == '当时保存的文字'
    assert frozen.saved_context.evidence['screenshot_png'] == '当时保存的文字'.encode()
    assert process_text_request(processor, frozen, on_trace=lambda *a: None).model_input == result.model_input


def test_cancelled_wait_does_not_send_edit_request():
    processor, calls = processor_returning('结果')
    with pytest.raises(RuntimeError, match='取消'):
        process_text_request(processor, edit_request(Future()), on_trace=lambda *a: None, cancelled=lambda: True)
    assert not calls


def test_history_keeps_used_image_and_text_after_replacement_and_exports(saved, tmp_path):
    from proximic_ring.voice.history_export import export_history
    store = saved()
    first = submit(store, tmp_path, '当时保存的资料')
    value = first.result(3)
    history = ModificationDatasetCollector(tmp_path / 'history', 'test')
    history.record_text_request(edit_request(first))
    identity = history.interaction_id_for_session(7)
    submit(store, tmp_path, '新截图').result(3)
    record = history._interaction_data(identity)
    snap = record['llm']['requests'][0]['saved_context_snapshot']
    image = history.interactions_root / identity / snap['screenshot_file']
    assert image.read_bytes() == '当时保存的资料'.encode()
    assert snap['text'] == '当时保存的资料' and snap['capture_id'] == value['capture_id']
    details = history.load_details(identity)
    sections = {s['key']: s for s in details['sections']}
    assert sections['saved_context_1_image']['imageUrl'] == image.as_uri()
    assert sections['saved_context_1_text']['items'][0]['text'] == snap['text']
    exported = export_history(history, tmp_path / 'exports')
    with zipfile.ZipFile(exported['path']) as archive:
        assert any(name.endswith(snap['screenshot_file']) for name in archive.namelist())


def test_late_saved_context_does_not_recreate_cleared_history(tmp_path):
    history = ModificationDatasetCollector(tmp_path / 'history', 'test')
    pending = Future()
    history.record_text_request(edit_request(pending))
    history.clear()
    pending.set_result({'status': 'ok', 'text': '晚到的结果'})
    assert not list(history.interactions_root.glob('*/record.json'))


def test_copied_text_replaces_pending_image_without_ocr_or_llm(saved, tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def ocr(png):
        entered.set()
        assert release.wait(3)
        calls.append(png)
        return '截图原文'
    store = saved(ocr=ocr)
    pending = submit(store, tmp_path, 'image')
    try:
        assert entered.wait(2)
        copied = store.save_text('  Agent 回复的原文\n\n代码 `x` 与格式保持不变。\n')
        value = copied.result(1)
        assert value['source'] == 'copied_text' and value['text'].startswith('  Agent')
        assert value['text'].endswith('。\n')
        assert not list(store.root.glob('*.png')) and not calls
        assert value.evidence['screenshot_png'] == b''
        assert 'cleanup' not in value.evidence['saved_screenshot']
    finally:
        release.set()
    pending.result(3)
    assert store.snapshot() is copied and store.record()['source'] == 'copied_text'
    restored = saved().snapshot().result()
    assert restored == value and restored.evidence['screenshot_png'] == b''
    assert saved().record()['image_file'] == ''
    store._ocr = lambda png: '新截图'
    assert submit(store, tmp_path, 'new image').result(3)['source'] == 'saved_screenshot'
    assert len(list(store.root.glob('*.png'))) == 1


def test_copied_text_request_and_history_contain_no_image(saved, tmp_path):
    store = saved()
    future = store.save_text('完整复制原文\n第二段。')
    processor, calls = processor_returning('编辑结果')
    request = edit_request(future)
    result = process_text_request(processor, request, on_trace=lambda *a: None)
    assert result.model_input['saved_context'] == '完整复制原文\n第二段。'
    assert set(result.model_input) == {'utterance', 'editable_text', 'screen_context', 'saved_context'}
    assert isinstance(result.model_input['saved_context'], str) and len(calls) == 1
    history = ModificationDatasetCollector(tmp_path / 'history', 'test')
    history.record_text_request(request)
    details = history.load_details(history.interaction_id_for_session(7))
    sections = {s['key']: s for s in details['sections']}
    assert 'saved_context_1_image' not in sections
    assert sections['saved_context_1_text']['title'] == '本次编辑的复制文本参考'
    assert sections['saved_context_1_text']['items'][0]['text'] == '完整复制原文\n第二段。'


def test_unavailable_saved_text_is_not_sent_as_reference():
    from proximic_ring.text_processing.routed_edit_processing import routed_edit_model_input
    data = routed_edit_model_input('编辑', '原文', saved_context={
        'status': 'unavailable', 'text': '旧内容', 'image_file': 'local.png'})
    assert data['saved_context'] == ''
