"""Reproduce recording semantics with synthetic data in a disposable directory."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import numpy as np
from proximic_ring.modification_dataset import ModificationDatasetCollector

with TemporaryDirectory(prefix='voice-record-audit-') as tmp:
    c=ModificationDatasetCollector(tmp,'audit_only')
    c.begin_session(1)
    c.record_audio(1,np.zeros(16000,dtype=np.float32))
    update=SimpleNamespace(session_id=1,text='扩写到一百字。',is_final=True,backend='audit',model='audit',latency_s=0,audio_duration_s=1,error=None)
    c.record_asr_update(update)
    c.record_text_request(SimpleNamespace(request_id=1,session_id=1,mode='edit',raw_text=update.text,target_text='原文',settings=None))
    c.record_llm_failure(1,'模拟编辑失败')
    c.record_application(session_id=1,action='applied',mode='dictation',final_text=update.text,method='input_method',error='模拟编辑失败，保留听写')
    p=c.interactions_root/c.interaction_id_for_session(1)/'record.json'
    r=json.loads(p.read_text())
    fallback={'selected':r['mode']['selected'],'training_target':r['mode']['training_target'],'outcome':r['outcome']['status']}
    assert fallback['training_target']=='dictation'
    c.record_application(session_id=1,action='undone',mode='dictation',final_text='',method='input_method')
    before=json.loads(p.read_text())['outcome']['status']
    c.record_asr_update(update)
    r=json.loads(p.read_text())
    late={'before':before,'after':r['outcome']['status']}
    assert late=={'before':'undone','after':'recognized'}
    # A silent, empty utterance can still receive a binary mode target.
    c.begin_session(2)
    c.record_audio(2,np.zeros(16000,dtype=np.float32))
    update.session_id=2;update.text=''
    c.record_asr_update(update)
    c.record_application(session_id=2,action='applied',mode='dictation',final_text='',method='input_method')
    p=c.interactions_root/c.interaction_id_for_session(2)/'record.json'
    empty=json.loads(p.read_text())
    report={'failed_edit_fallback':fallback,'late_asr_overwrites_terminal_outcome':late,
            'empty_asr_training_target':empty['mode']['training_target']}
    Path(__file__).with_name('reproductions.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
