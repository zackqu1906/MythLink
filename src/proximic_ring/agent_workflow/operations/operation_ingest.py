"""阶段 1：导入完整 Workflow 和来源；不做分类、概括或推荐。"""
import json
from pathlib import Path
from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.extraction.workflow_artifacts import compact_workflows
from proximic_ring.agent_workflow.extraction.workflow_prompt import PROMPT_VERSION, COMPATIBLE_VERSIONS

USER_SOURCES={'agent_editor','ring_voice','workflow_recommendation'}
TEST_SOURCES={'workflow_seed','workflow_trial','workflow_test','workflow_recommendation_test','test',
              'general_agent_check','persistence_check'}


def sample_from_audit(result,index=0, *, source_file='', force_kind=None):
    if result.get('generation',{}).get('prompt_version') not in COMPATIBLE_VERSIONS:
        raise ValueError('旧版 Workflow，需要先重新提取')
    if result.get('source_changed_after_extraction'):raise ValueError('提取来源已变化，需重新提取')
    flow=compact_workflows(result)['workflows'][index]
    src=result['source'];ep=src['episode'];origin=ep.get('source','')
    kind=force_kind or ('test' if origin in TEST_SOURCES else 'user' if origin in USER_SOURCES else 'unknown')
    if kind=='user' and origin not in USER_SOURCES:raise ValueError('来源不足以证明真实使用')
    context=ep.get('context') or {}
    if isinstance(context,str):context=json.loads(context)
    return dict(id=digest([src['session_id'],src['episode_id'],index])[:32],kind=kind,flow=flow,
        occurred_at=ep['created_at'],context=context,
        source=dict(session_id=src['session_id'],episode_id=src['episode_id'],workflow_index=index,
                    episode_source=origin,extraction_version=result['generation']['prompt_version'],source_file=source_file,
                    **({'execution_profile':src['execution_profile']} if 'execution_profile' in src else {}),
                    **({'task_profile':result['task_profile']} if 'task_profile' in result else {}),
                    outcome=ep['status'],goal_verification=result.get('goal_verification','unknown')))


def ingest_audits(store):
    latest={};errors=[];changed=0
    for path in (store.root/'workflows').glob('*.audit.json'):
        try:
            value=json.loads(path.read_text());src=value['source']
            key=(src['session_id'],src['episode_id'])
            stamp=value['generation']['created_at']
            if key not in latest or stamp>latest[key][0]:latest[key]=(stamp,path,value)
        except (OSError,ValueError,KeyError,TypeError):errors.append(path.name+'：无法读取')
    for _,path,value in latest.values():
        try:
            for i in range(len(value['workflows'])):
                changed+=store.ingest(sample_from_audit(value,i,source_file='../workflows/'+path.name))
        except (ValueError,KeyError,TypeError) as exc:errors.append(path.name+'：'+str(exc))
    return changed,errors


def ingest_dataset(store,path):
    path=Path(path);manifest=json.loads((path/'manifest.json').read_text())
    count=0
    for entry in manifest['samples']:
        file=(path/entry['file']).resolve()
        if not file.is_relative_to(path.resolve()):raise ValueError('数据集路径越界')
        sample=json.loads(file.read_text())
        if digest(sample)!=entry['sha256']:raise ValueError('数据集样本校验失败；修改后需更新 manifest')
        if sample['kind']!='test':raise ValueError('测试数据集不能导入真实使用频次')
        count+=store.ingest(sample)
    return count

