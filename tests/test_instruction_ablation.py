import io
import json
from dataclasses import replace
from tools.check_edit_instruction_ablation import load_trials, run_trial, MODEL, BOUNDARY_EDIT_PROMPT
from test_routed_edit import settings


def test_pipeline_forwards_actual_output_and_keeps_expectations_off_wire():
    _,trials=load_trials()
    trial=next(t for t in trials if t['condition_id']=='S11')
    sent=[]
    outputs=iter(['王玮，你必须今天把文件发我。','王玮，请今天务必把文件发我。'])
    def respond(request,**kwargs):
        payload=json.loads(request.data);sent.append(payload)
        result=io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':next(outputs)}]}]},ensure_ascii=False).encode())
        result.status=200
        return result
    result=run_trial(trial,replace(settings(),model=MODEL,base_url='https://ark.cn-beijing.volces.com/api/v3'),respond)
    assert not result.get('error') and len(sent)==2
    inputs=[json.loads(p['input'][1]['content'][0]['text']) for p in sent]
    assert inputs[1]['editable_text']=='王玮，你必须今天把文件发我。'
    assert all(set(i)=={'utterance','editable_text','screen_context'} for i in inputs)
    assert all(p['input'][0]['content'][0]['text']==BOUNDARY_EDIT_PROMPT for p in sent)
    assert result['final_text']=='王玮，请今天务必把文件发我。'


def test_frozen_blocks_and_request_budget():
    data,trials=load_trials()
    assert len(trials)==84 and sum(len(t['steps']) for t in trials)==99
    assert [t['id'] for t in trials]==[t['id'] for t in load_trials()[1]]
    for block in range(3):
        assert {t['condition_id'] for t in trials[block*28:(block+1)*28]}=={c['id'] for c in data['cases']}
