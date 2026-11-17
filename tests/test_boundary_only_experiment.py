"""Verify the experimental request contract without network access."""
import io
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.check_boundary_only_edit import frozen_cases,run_case,MODEL,ENDPOINT,prompt
from proximic_ring.text_processing.model import LLMSettings

@pytest.mark.parametrize('body',['  甲\n乙  \n',''])
def test_one_request_direct_body_and_no_annotation_leak(body):
    requests=[]
    def respond(request,**kwargs):
        requests.append(json.loads(request.data))
        response=io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':body}]}]}).encode())
        response.status=200
        return response
    case=frozen_cases()[0]
    settings=LLMSettings(enabled=True,provider='volcengine',model=MODEL,base_url=ENDPOINT.removesuffix('/responses'),api_key='test-secret',api_key_env='')
    result=run_case(case,settings,respond)
    assert 'error' not in result and result['final_text']==body
    assert len(requests)==1
    assert set(json.loads(requests[0]['input'][1]['content'][0]['text']))=={'utterance','editable_text','screen_context'}
    assert 'test-secret' not in json.dumps(result)
    assert 'actual_tasks' not in result and result['classification_applicable'] is False
    assert not any(x in prompt() for x in ['分类','类别','模块','edit_plan'])
