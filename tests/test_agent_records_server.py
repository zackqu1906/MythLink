import copy
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from test_agent_recording import recorder, start
from test_agent_workflows import finished, answer
from proximic_ring.agent_workflow.recording.records_server import RecordsServer
from proximic_ring.agent_workflow.recording.records import export_html
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflows import process_one
from proximic_ring.agent_workflow.extraction.workflow_view import export_workflows


@pytest.fixture
def server(recorder):
    store=WorkflowStore(recorder.root)
    export_workflows(store);store.close()
    export_html(recorder.root)
    viewer=RecordsServer(recorder.root,on_automatic=lambda value:None)
    yield viewer
    viewer.close()
    viewer.thread.join(timeout=2)
    assert not viewer.thread.is_alive()


def post(server,action,body=None,**headers):
    request=Request(server.origin+server.prefix+'api/'+action,data=json.dumps(body or {}).encode(),
        headers={'Content-Type':'application/json','Origin':server.origin,**headers})
    with urlopen(request,timeout=5) as response: return json.load(response)


def test_each_task_can_extract_and_reextract_success_without_changing_raw_records(recorder,server):
    first,packet=finished(recorder)
    second=start(recorder,goal='另一个任务',request=2,turn='t2')
    recorder.rpc('incoming',{'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'t2','status':'completed'}}})
    assert recorder.flush()
    original=(recorder.directory/'events.jsonl').read_bytes()
    post(server,'refresh')
    with urlopen(server.url,timeout=3) as response: page=response.read().decode()
    assert f"data-episode='{first}'" in page and f"data-episode='{second}'" in page
    assert 'controls.js' in page and "script-src 'self'" in page
    assert '<script' not in (recorder.root/'index.html').read_text()
    request=dict(session_id=recorder.session_id,episode_id=first)
    job=post(server,'extract',request)['jobs'][0]
    # Concurrent clicks and changed source evidence reuse the pending task job.
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(r['jobs']==[job] for r in pool.map(lambda _:post(server,'extract',request),range(4)))
    store=WorkflowStore(recorder.root)
    try:
        changed=copy.deepcopy(packet);changed['fingerprint']='changed'
        assert store.enqueue(changed,manual=True)==job
        assert process_one(store,None,model=lambda *args:(answer(packet),{}))
        old_result=(store.directory/(job+'.audit.json')).read_bytes()
        revision=post(server,'extract',request)['jobs'][0]
        assert revision!=job and len(store.jobs())==2
        assert (store.directory/(job+'.audit.json')).read_bytes()==old_result
        assert (recorder.directory/'events.jsonl').read_bytes()==original
        jobs=server.status()['jobs']
        assert jobs[0]['episode_id']==first and jobs[0]['status']=='queued'
        assert all(j['episode_id']!=second for j in jobs)
    finally:store.close()


def test_failed_retry_only_targets_latest_failed_attempt_per_task(recorder,server):
    episode,packet=finished(recorder)
    store=WorkflowStore(recorder.root)
    try:
        old=store.enqueue(packet);store.finish(old,error='old failure')
        job=post(server,'retry-failed')['jobs'][0]
        assert job!=old
        assert post(server,'retry-failed')['jobs']==[]  # Latest attempt is already queued.
        store.finish(job,error='new failure')
        retry=post(server,'extract',dict(session_id=recorder.session_id,episode_id=episode))['jobs'][0]
        process_one(store,None,model=lambda *args:(answer(packet),{}))
        assert store.jobs()[0]['id']==retry and store.jobs()[0]['status']=='completed'
        assert post(server,'retry-failed')['jobs']==[]  # Historic errors do not requeue success.
    finally:store.close()


def test_unfinished_missing_and_cross_session_tasks_are_rejected(recorder,server):
    episode=start(recorder)
    assert recorder.flush()
    for sid,eid in [(recorder.session_id,episode),(recorder.session_id,'a'*32),('../outside',episode)]:
        with pytest.raises(HTTPError) as error:
            post(server,'extract',dict(session_id=sid,episode_id=eid))
        assert error.value.code in (400,503)
    assert server.status()['jobs']==[]


def test_loopback_requires_capability_origin_and_valid_routes(recorder,server):
    finished(recorder)
    for headers in ({'Origin':'https://other.example'},{'Origin':'null'},
                    {'Content-Type':'text/plain'},{'Host':'other.example'}):
        with pytest.raises(HTTPError) as error:post(server,'latest',**headers)
        assert error.value.code==403
    for path,code in [('index.html',403),(server.prefix[1:]+'../private.json',404),
                      (server.prefix[1:]+'workflows/queue.sqlite3',404),
                      (server.prefix[1:]+'api/latest',404)]:
        with pytest.raises(HTTPError) as error:urlopen(server.origin+'/'+path,timeout=3)
        assert error.value.code==code
    # Symlinks cannot escape the selected record directory.
    secret=recorder.root.parent/'private.json';secret.write_text('private')
    (recorder.root/'escape.json').symlink_to(secret)
    with pytest.raises(HTTPError) as error:urlopen(server.origin+server.prefix+'escape.json',timeout=3)
    assert error.value.code==404
    assert server.status()['jobs']==[]


def test_natural_workflow_and_actual_prompt_text_are_readable(recorder,server):
    _,packet=finished(recorder);store=WorkflowStore(recorder.root)
    try:
        job=store.enqueue(packet)
        process_one(store,None,model=lambda *args:(answer(packet),{}))
        with urlopen(server.origin+server.prefix+'workflows/'+job+'.flow.txt',timeout=3) as response:
            assert response.headers['Content-Type']=='text/plain; charset=utf-8'
            assert '执行方法：' in response.read().decode('utf-8')
        directory=recorder.root/'workflow-tests';directory.mkdir()
        (directory/'example.prompt.txt').write_text('打开音乐',encoding='utf-8')
        with urlopen(server.origin+server.prefix+'workflow-tests/example.prompt.txt',timeout=3) as response:
            assert response.read().decode('utf-8')=='打开音乐'
    finally:store.close()
