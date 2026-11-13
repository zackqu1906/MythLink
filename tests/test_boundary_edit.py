"""Production boundary variant: current prompt, one call, safe direct body, history."""
import io
import json
import threading
from types import SimpleNamespace
import pytest
from proximic_ring.text_processing.boundary_edit_prompt import BOUNDARY_EDIT_PROMPT, BOUNDARY_EDIT_PROMPT_VERSION
from proximic_ring.text_processing.model import ROUTED_EDIT_BOUNDARY, INPUT_ROUTING_ROUTED_EDIT, TextProcessingRequest
from proximic_ring.text_processing.worker import TextProcessingWorker
from proximic_ring.text_processing.llm import OpenAICompatibleTextProcessor
from test_routed_edit import settings,processor_returning
from proximic_ring.voice.modification_dataset import ModificationDatasetCollector

@pytest.mark.parametrize('target,body',[('原文','  新\n正文：内层标题  \n'),('原文',''),('原文',' \n '),('','新的通知。')])
def test_current_boundary_prompt_one_call_and_literal_body(target,body):
    processor,calls=processor_returning(body)
    result,raw,model_input,plan=processor.process_routed_edit_with_trace(
        '完成要求',settings(),target,{'topic':'上下文'},variant=ROUTED_EDIT_BOUNDARY)
    assert result==raw==body and plan is None and len(calls)==1
    assert calls[0]['input'][0]['content'][0]['text']==BOUNDARY_EDIT_PROMPT
    assert BOUNDARY_EDIT_PROMPT_VERSION=='boundary-only-v3-final-requirements'
    assert json.loads(calls[0]['input'][1]['content'][0]['text'])==model_input
    assert model_input=={'utterance':'完成要求','editable_text':target,'screen_context':{'topic':'上下文'}}
    assert 'tools' not in calls[0] and calls[0]['thinking']=={'type':'disabled'}

@pytest.mark.parametrize('payload',[
    {'status':'incomplete','output':[{'type':'message','content':[{'type':'output_text','text':'未完'}]}]},
    {'status':'completed','output':[]},
    {'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'拒绝'}]}]},
    {'status':'completed','output':[{'type':'function_call','arguments':'{"modified_text":""}'}]},
    {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':None}]}]},
])
def test_missing_or_incomplete_response_is_not_a_clear_instruction(payload):
    calls=[]
    def respond(request,**kwargs):calls.append(request);return io.BytesIO(json.dumps(payload).encode())
    processor=OpenAICompatibleTextProcessor(urlopen=respond)
    with pytest.raises(RuntimeError):
        processor.process_routed_edit_with_trace('修改',settings(),'原文',variant=ROUTED_EDIT_BOUNDARY)
    assert len(calls)==1

@pytest.mark.parametrize('status',['completed','incomplete'])
def test_worker_and_history_keep_selected_variant_without_inventing_classification(tmp_path,status):
    processor,calls=processor_returning('直接正文',status=status)
    results=[];done=threading.Event()
    worker=TextProcessingWorker(processor=processor,on_result=lambda r:(results.append(r),done.set()),
        intent_router=SimpleNamespace(classify=lambda *a,**k:pytest.fail('Called Clef'),close=lambda:None),
        context_provider=SimpleNamespace(close=lambda:None))
    request=TextProcessingRequest(1,7,'edit','写通知',settings(),'原文',routing_mode=INPUT_ROUTING_ROUTED_EDIT,
                                  routed_edit_variant=ROUTED_EDIT_BOUNDARY,screen_context={})
    store=ModificationDatasetCollector(tmp_path,'test');store.record_text_request(request)
    before=store.load_details(store.interaction_id_for_session(7))
    assert '正在分类' not in json.dumps(before,ensure_ascii=False)
    try:
        worker.submit(request);assert done.wait(5)
    finally:worker.close(wait=True)
    result=results[0];assert len(calls)==1 and result.edit_plan is None and result.routed_edit_variant==ROUTED_EDIT_BOUNDARY
    assert bool(result.error)==(status=='incomplete')
    store.record_llm_result(1,result)
    record=store._interaction_data(store.interaction_id_for_session(7));saved=record['llm']['requests'][0]
    assert saved['system_prompt']==BOUNDARY_EDIT_PROMPT and saved['prompt_version']==BOUNDARY_EDIT_PROMPT_VERSION
    assert saved['routed_edit_variant']==ROUTED_EDIT_BOUNDARY and saved['classification_type']=='not_applicable'
    assert saved['edit_plan'] is None and saved['request_input']==result.model_input
    details=store.load_details(store.interaction_id_for_session(7));readable=json.dumps(details,ensure_ascii=False)
    assert '直接生成正文' in readable and '未取得分类' not in readable and '未记录分类' not in readable
    assert details['sections'][1]['items'][0]['outputVisible']==(status=='completed')
