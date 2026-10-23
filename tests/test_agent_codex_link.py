import pytest

from test_agent_recording import recorder, snapshot
from proximic_ring.agent_workflow.recording.codex_link import task_links
from proximic_ring.agent_workflow.recording.records import export_html


FORMAL = '01a152f2-6362-78a2-8ce1-f7fad005ad7f'
TRIAL = '01a152f2-6362-78a2-8ce1-f7fad005ad70'


def thread(r, tid, ephemeral, request=1):
    r.rpc('outgoing', {'id':request, 'method':'thread/start', 'params':{'ephemeral':ephemeral}})
    metadata = {'id':tid}
    if ephemeral is not None:
        metadata['ephemeral'] = ephemeral
    # The outer recorder context may still refer to the previously active thread.
    r.rpc('incoming', {'id':request, 'result':{'thread':metadata}}, thread_id='previous-thread')


def turn(r, tid, request=10):
    episode = r.new_episode('本次目标', 'test')
    turn_id = f'turn-{request}'
    r.rpc('outgoing', {'id':request, 'method':'turn/start', 'params':{'threadId':tid}}, episode_id=episode)
    r.rpc('incoming', {'method':'turn/started', 'params':{'threadId':tid, 'turn':{'id':turn_id}}})
    r.rpc('incoming', {'id':request, 'result':{'turn':{'id':turn_id}}})
    r.rpc('incoming', {'method':'turn/completed', 'params':{
        'threadId':tid, 'turn':{'id':turn_id, 'status':'completed'}}})
    return episode


def test_html_links_each_task_to_its_formal_thread_and_does_not_link_trials(recorder):
    thread(recorder, FORMAL, False)
    first = turn(recorder, FORMAL)
    thread(recorder, TRIAL, True, request=2)
    trial = turn(recorder, TRIAL, request=11)
    second = turn(recorder, FORMAL, request=12)
    snapshot(recorder)
    original = (recorder.directory/'events.jsonl').read_bytes()
    index = export_html(recorder.root).read_text()
    href = f"href='codex://threads/{FORMAL}'"
    assert index.count(href) == 2
    for eid in (first, second):
        page = (recorder.directory/f'episode-{eid}.html').read_text()
        assert href in page and '在 Codex 中打开</a>' in page
        assert '<script' not in page and 'prompt=' not in page
    page = (recorder.directory/f'episode-{trial}.html').read_text()
    assert "href='codex:" not in page and '临时会话未保存到 Codex' in page
    assert (recorder.directory/'events.jsonl').read_bytes() == original


@pytest.mark.parametrize('tid,ephemeral,reason', [
    (FORMAL, None, '无法确认会话已持久化'),
    ('new', False, '会话 ID 缺失或格式不正确'),
    (FORMAL+'?prompt=run', False, '会话 ID 缺失或格式不正确'),
    ("javascript:alert('x')", False, '会话 ID 缺失或格式不正确'),
])
def test_missing_persistence_or_invalid_id_cannot_create_action_links(recorder,tid,ephemeral,reason):
    thread(recorder, tid, ephemeral)
    turn(recorder, tid)
    snapshot(recorder)
    page = export_html(recorder.root).read_text()
    assert "href='codex:" not in page and reason in page


def test_queued_task_never_inherits_another_tasks_thread(recorder):
    thread(recorder, FORMAL, False)
    turn(recorder, FORMAL)
    queued = recorder.new_episode('未开始的任务', 'test')
    data = snapshot(recorder)
    assert task_links(data)[queued]['url'] is None


def test_multiple_threads_or_conflicting_persistence_remain_unavailable(recorder):
    thread(recorder, FORMAL, False)
    eid = turn(recorder, FORMAL)
    recorder.rpc('incoming', {'method':'thread/started', 'params':{
        'thread':{'id':FORMAL, 'ephemeral':True}}})
    data = snapshot(recorder)
    assert task_links(data)[eid]['url'] is None
    data['events'].append({'direction':'incoming','payload':{},'episode_id':eid,'turn_id':'other','thread_id':TRIAL})
    link = task_links(data)[eid]
    assert link['url'] is None and '关联不唯一' in link['reason']


def test_thread_notification_supports_records_missing_start_response(recorder):
    recorder.rpc('incoming', {'method':'thread/started', 'params':{
        'thread':{'id':FORMAL, 'ephemeral':False}}})
    eid = turn(recorder, FORMAL)
    assert task_links(snapshot(recorder))[eid]['url'] == f'codex://threads/{FORMAL}'


def test_tool_result_cannot_supply_persistence_metadata(recorder):
    recorder.rpc('outgoing', {'id':1, 'method':'mcpServer/tool/call', 'params':{}})
    recorder.rpc('incoming', {'id':1, 'result':{'thread':{'id':FORMAL,'ephemeral':False}}})
    eid = turn(recorder, FORMAL)
    assert task_links(snapshot(recorder))[eid]['url'] is None
