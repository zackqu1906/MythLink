import io
import json
import queue
from urllib.request import Request

from tools.clef_pipeline_report import attribution, pipeline_metrics, recording_opener, write_pipeline_report


def case(route=True,review='pass'):
    return dict(id='example',utterance='<只记录>',target='',context={'messages':[]},
                expected={'task':'dictation','tasks':[]},actual_task='dictation',edit_plan=None,
                exact_match=route,root_correct=route,raw_exact_match=False,
                routing_error=None,generation_error=None,final_text='只记录',
                routing_latency_s=2.,generation_latency_s=1.,total_latency_s=3.01,
                routing_trace={'warning':'听写任务被忽略','stages':[{'stage':'joint','answers':{
                    'voice_intent':{'choice':'dictation','probabilities':{'dictation':.8,'edit':.2}},
                    'compose':{'choice':'apply','probabilities':{'apply':.6,'skip':.4}}}}],
                    'task_availability':{'excluded_tasks':{'modify':'no_editable_source'}}},
                generation_checks={'passed':None,'checks':[]},generation_review={'status':review,'note':'复核'})


def test_route_and_generation_are_scored_independently():
    a,b,c=case(True,'fail'),case(False,'pass'),case(False,'fail')
    assert attribution(a)=='路由正确，生成正文有问题'
    assert attribution(b)=='路由不匹配，但正文满足要求'
    assert attribution(c)=='路由不匹配且正文不符，原因待区分'
    m=pipeline_metrics([a,b,c])
    assert m['final_route_correct']==1 and m['generation_review_passed']==1
    assert m['route_correct_generation_failed']==1 and m['both_passed']==0
    assert m['constraint_scored']==0 and m['constraint_passed']==0


def test_failed_generation_and_unreviewed_output_are_not_counted_as_acceptance():
    a,b=case(True,'pending'),case(True,'pending')
    b['generation_error']='invalid tool result'
    m=pipeline_metrics([a,b])
    assert m['completed']==1 and m['generation_errors']==1
    assert m['generation_review_passed']==0 and m['generation_review_pending']==2
    assert m['timing_successful']['total']['n']==1


def test_invalid_route_blocked_before_http_is_not_a_generated_answer():
    c=case(False,'blocked')
    c.update(generation_error='修改模式需要已有的目标文本',generation_calls=[],
             final_text='帮我回复下老板，语气好点')
    assert attribution(c)=='路由不匹配，生成前校验拦截（未调用正文模型）'
    m=pipeline_metrics([c])
    assert m['generation_review_blocked']==1 and m['generation_review_failed']==0
    assert m['generation_review_passed']==0 and m['completed']==0


def test_http_evidence_preserves_read_limit_and_does_not_capture_auth():
    calls=queue.Queue();body=b'{"answers":{},"usage":{"input_tokens":10}}'
    class Response(io.BytesIO):
        status=200
        headers={'Content-Type':'application/json','Set-Cookie':'private','x-request-id':'test'}
        def read(self,size=-1):
            assert size==1024
            return super().read(size)
    response=Response(body)
    def opener(request,timeout):
        assert timeout==15
        assert request.get_header('Authorization')=='Bearer secret'
        return response
    request=Request('http://localhost/v1/systemone',data=b'{"state":"synthetic"}',headers={'Authorization':'Bearer secret'})
    with recording_opener(opener,calls)(request,timeout=15) as wrapped:
        assert wrapped.read(1024)==body
    record=calls.get_nowait()
    assert record['response']==json.loads(body) and response.closed
    assert record['http_latency_s']>=0
    assert 'secret' not in json.dumps(record) and 'private' not in json.dumps(record)


def test_report_keeps_raw_warnings_probabilities_and_semantic_failure_separate(tmp_path):
    r={'scope':'test','prompt_version':'v19','generation_prompt_version':'v8','llm_model':'test','cases':[case(True,'fail')]}
    p=tmp_path/'report.json';write_pipeline_report(r,p)
    html=p.with_suffix('.html').read_text()
    assert '&lt;只记录&gt;' in html and 'dictation: 80.00%' in html and 'skip: 40.00%' in html
    assert '未请求，无模型概率' in html and '路由正确，生成正文有问题' in html
    assert r['metrics']['final_route_correct']==1 and r['metrics']['raw_exact_match']==0
