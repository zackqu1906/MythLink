"""Finite intent families are auditable drafts, never silent membership changes."""
import copy
import json
import io

import pytest

from test_operation_prototypes import store, sample
from proximic_ring.agent_workflow.operations import operation_catalog as catalog
from proximic_ring.agent_workflow.operations.operation_generalization import source_requirements,normalize_generalization
from proximic_ring.agent_workflow.operations.operation_novelty import validate_decisions
from proximic_ring.agent_workflow.operations.operation_prototypes import heads, revision, recheck, activate
from proximic_ring.agent_workflow.operations.operation_revision_validation import leaves, validate_update
from proximic_ring.agent_workflow.operations.operation_library import library
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.operations.operation_group_view import group_filename
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow.operations import operation_model


def nearby(kind):
    label,category,extra={
        'food':('餐饮','category','top_n'),
        'school':('学校','school_type_filter',None),
        'sports':('运动设施','facility_keyword',None),
    }[kind]
    parameters=[dict(name='location',description='基准地点'),dict(name=category,description=label+'类型')]
    if extra:parameters.append(dict(name=extra,description='按评分返回的结果数量'))
    detail={'food':'按评分排序，取 {{top_n}} 项，逐家核验评分并记录人均。',
            'school':'通过网页和官方来源核验学校类型和地址。',
            'sports':'读取地图显示距离和位置，不能声称是步行距离。'}[kind]
    return dict(name='查找附近'+label,goal='查找 {{location}} 附近的'+label,
        parameters=parameters,steps=[dict(action='定位 {{location}}，查找 {{'+category+'}}。'),dict(action=detail)],
        completion='结果与地点和类别相符，包含位置及可核验的来源。',conditions=[],fallbacks=[])


def proposal(packet,oid):
    out=[]
    for c in packet['candidates']:
        d=dict(operation_id=c['operation_id'],relation='different',reason='用户目的不相容',parameter_map={},
               implementation_parameters=dict(new=[],general=[]),novelty=dict(kind='none',reason='不做普通增量更新',evidence=[]))
        if c['operation_id']==oid:
            d.update(relation='generalizable',reason='共同目标是附近场所检索；类别、排序数量、字段及核验通过参数和分支保留',
                     parameter_map={'location':'location'},generalization=dict(goal='查找指定地点附近的场所',
                         new=[dict(path='/goal',quote=packet['new_workflow']['goal'])],
                         general=[dict(path='/goal',quote=c['workflow']['goal'])]))
        out.append(d)
    return dict(decisions=out,new_operation_display=None)


def candidate(packet):
    """A fixture response for the structural boundary, not an LLM quality test."""
    flow=copy.deepcopy(packet['current_workflow'])
    flow.update(name='查找附近场所',goal='查找 {{location}} 附近的 {{place_type}}，本次要求为 {{requirements}}',
        parameters=[dict(name='location',description='基准地点'),dict(name='place_type',description='场所类别'),
                    dict(name='requirements',description='筛选、排序、数量和输出要求；没有附加要求时填写无')],
        steps=[dict(action='确认 {{location}} 和 {{place_type}}，按 {{requirements}} 检索并核验场所。'),
               dict(action='需要餐饮评分排名时按评分排序并逐家核验评分、人均及数量；学校通过网页和官方来源核验类型与地址；运动设施读取地图距离和位置，不当作步行距离。')])
    flow['completion']='返回符合本次地点、类别和要求的结果及核验来源。'
    old=leaves(packet['current_workflow']);new=leaves(flow)
    first=packet['sources'][0]['version_id']
    changes=[dict(target=p,sources=[dict(version_id=first,path='/goal')]) for p,v in new.items() if old.get(p)!=v]
    bindings={};coverage=[]
    for s in packet['sources']:
        vid=s['version_id'];bindings[vid]={p['name']:('location' if p['name']=='location' else
            'requirements' if p['name'] in ('school_type_filter','top_n') else 'place_type') for p in s['workflow']['parameters']}
        for path in source_requirements(s['workflow']):
            coverage.append(dict(version_id=vid,path=path,targets=['/steps/1/action'],handling='conditional',reason='按类别和本次要求保留'))
    return dict(outcome='candidate',reason='参数化附近场所检索并保留条件分支',workflow=flow,changes=changes,
                coverage=coverage,parameter_bindings=bindings,display=None)


def compact_candidate(packet):
    value=candidate(packet);value.pop('changes');value['coverage']=[]
    for source in packet['sources']:
        value['coverage'].append(dict(version_id=source['version_id'],mappings=[
            dict(paths=[path],targets=[target],handling=kind,reason='保留原来源的共享内容或条件分支')
            for path,target,kind in [('/goal','/goal','shared'),('/steps/0','/steps/0','shared'),
                                    ('/steps/1','/steps/1','conditional'),('/completion','/completion','shared')]]))
    return value


@pytest.mark.parametrize('fault',['invented_quote','missing_goal','unknown_parameter','fake_implementation','missing_proposal'])
def test_generalizable_requires_grounded_goals_and_keeps_unmapped_constraints(fault):
    packet=dict(new_workflow=nearby('school'),candidates=[dict(operation_id='op',workflow=nearby('food'))])
    value=proposal(packet,'op');d=value['decisions'][0]
    if fault=='invented_quote':d['generalization']['new'][0]['quote']='编造'
    elif fault=='missing_goal':d['generalization']['general']=[]
    elif fault=='unknown_parameter':d['parameter_map']['madeup']='category'
    elif fault=='fake_implementation':d['implementation_parameters']['general']=['top_n']
    else:del d['generalization']
    with pytest.raises(ValueError):validate_decisions(value,packet)


def test_semantic_family_is_not_accepted_as_same_without_parameter_contract():
    packet=dict(new_workflow=nearby('school'),candidates=[dict(operation_id='op',workflow=nearby('food'))])
    value=proposal(packet,'op')
    assert validate_decisions(value,packet)[0]['relation']=='generalizable'
    value['decisions'][0]['relation']='same';del value['decisions'][0]['generalization']
    with pytest.raises(ValueError):validate_decisions(value,packet)


@pytest.mark.parametrize('generalizing',[False,True])
def test_larger_budget_only_applies_to_background_generalization(monkeypatch,generalizing):
    from proximic_ring.agent_workflow.extraction import workflow_model
    from proximic_ring.text_processing.model import LLMSettings
    seen=[]
    def http(req,timeout):
        seen.append((json.loads(req.data),timeout))
        return io.BytesIO(json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',
            content=[dict(type='output_text',text='{}')])])).encode())
    original=workflow_model.request_json
    monkeypatch.setattr(workflow_model,'request_json',lambda *a,**kw:original(*a,**kw,urlopen=http))
    packet={'material_information':{'kind':'generalization' if generalizing else 'path'}}
    _,generation=operation_model.call(packet,LLMSettings(provider='volcengine',model='fixture',api_key='fixture'),prompt='fixture')
    assert seen[0][0]['max_output_tokens']==(32768 if generalizing else 16000)
    assert seen[0][1]==(300 if generalizing else 180)
    assert seen[0][0]['thinking']['type']=='enabled'
    assert generation['max_output_tokens']==seen[0][0]['max_output_tokens']




def queue(store):
    """One incoming task joins the existing two source groups only after review."""
    from proximic_ring.agent_workflow.operations.operation_prototypes import seed
    for sid,kind in [('restaurant','food'),('school','school')]:
        store.ingest(sample(sid,nearby(kind)))
        with store.db:seed(store,next(s for s in store.rows('samples') if s['id']==sid))
    oid=store.rows('samples')[0]['operation_id']
    store.ingest(sample('sports',nearby('sports')))
    from test_operation_prototypes import same
    assert catalog.review_one(store,None,model=same)
    return oid


def unified_candidate(packet):
    flow=dict(name='查找附近场所',goal='查找 {{location}} 附近的 {{place_type}}，本次要求为 {{requirements}}',
        parameters=[dict(name='location',description='基准地点'),dict(name='place_type',description='场所类别'),
                    dict(name='requirements',description='筛选、排序、数量和输出要求；没有附加要求时填写无')],
        steps=['确认 {{location}} 和 {{place_type}}，按 {{requirements}} 检索并核验场所。',
               '需要餐饮评分排名时按评分排序并逐家核验评分、人均及数量；学校通过网页和官方来源核验类型与地址；运动设施读取地图距离和位置，不当作步行距离。'])
    bindings={s['version_id']:{p['name']:[('location' if p['name']=='location' else
        'requirements' if p['name'] in ('school_type_filter','top_n') else 'place_type')]
        for p in s['workflow']['parameters']} for s in packet['sources']}
    return dict(workflow=flow,parameter_bindings=bindings,reason='参数化共同任务，按类别保留必要要求')


def test_binary_same_synthesizes_all_groups_and_pending_source_without_auto_activation(store):
    queue(store);before=heads(store);members=store.rows('operation_memberships')
    original_versions=store.versions()
    def model(packet,*a,**kw):
        assert len(packet['sources'])==3
        assert set(packet)=={'current_workflow','sources','classification_reason'}
        assert all(isinstance(s['workflow']['steps'][0],str) for s in packet['sources'])
        assert 'source_requirements' not in packet
        return unified_candidate(packet),{}
    catalog.review_one(store,None,model=model)
    assert heads(store)==before and store.rows('operation_memberships')==members
    assert store.versions()==original_versions
    update=store.rows('operation_updates')[0];rev=revision(store,update['revision_id'])
    assert update['status']=='candidate' and rev['generation']['review_only']
    assert len(rev['sources'])==3 and len(rev['generation']['expected_heads'])==2
    assert 'coverage' not in rev['generation'] and 'changes' not in rev['generation']
    assert {i['workflow']['name'] for i in recommend(store,scope='test')['items']}=={r['flow']['name'] for r in before.values()}
    export_operations(store)
    assert (store.directory/'definitions'/rev['operation_id']/(rev['id']+'.txt')).exists()
    run=store.root/'dummy.run.json';run.write_text('{}')
    with pytest.raises(ValueError,match='泛化候选不能通过单次 Trials 启用'):activate(store,rev['id'],run_file=run)


@pytest.mark.parametrize('fault',['missing_source','missing_parameter','unknown_target','empty_steps','undefined_parameter'])
def test_synthesis_rejects_invalid_contract_without_changing_active_groups(store,fault):
    queue(store);before=heads(store)
    def model(packet,*a,**kw):
        value=unified_candidate(packet)
        if fault=='missing_source':value['parameter_bindings'].pop(next(iter(value['parameter_bindings'])))
        elif fault=='missing_parameter':next(iter(value['parameter_bindings'].values())).pop('location')
        elif fault=='unknown_target':next(iter(value['parameter_bindings'].values()))['location']=['invented']
        elif fault=='empty_steps':value['workflow']['steps']=[]
        else:value['workflow']['steps'].append('填入 {{secret}}')
        return value,{}
    catalog.review_one(store,None,model=model)
    assert heads(store)==before and store.rows('operation_updates')[0]['status']=='failed'
    assert len(store.rows('operation_revisions'))==2


def test_joint_incompatibility_is_retained_without_forced_merge(store):
    queue(store);before=heads(store);members=store.rows('operation_memberships')
    catalog.review_one(store,None,model=lambda *a,**k:(dict(workflow=None,parameter_bindings={},reason='各组的输出要求无法合理统一'),{}))
    assert store.rows('operation_updates')[0]['status']=='unresolved'
    assert heads(store)==before and store.rows('operation_memberships')==members
    assert json.loads(store.rows('operation_jobs')[-1]['result'])['reason']


def test_synthesis_accepts_rewriting_goal_reducing_steps_and_splitting_parameters():
    from proximic_ring.agent_workflow.operations.operation_synthesis import validate_result
    source=dict(name='旧',goal='整理 {{input}}',parameters=[dict(name='input',description='待整理资料')],steps=['读取 {{input}}','整理','输出'])
    value=dict(workflow=dict(name='整理文件',goal='将 {{path}} 整理为 {{format}}',
        parameters=[dict(name='path',description='文件路径'),dict(name='format',description='输出格式')],
        steps=['读取 {{path}} 并按 {{format}} 整理输出。']),parameter_bindings={'v':{'input':['path','format']}},reason='拆分文件与输出要求并合并步骤')
    validate_result(value,dict(current_workflow=source,sources=[dict(version_id='v',workflow=source)]))


def test_stale_source_during_synthesis_preserves_paid_output_but_does_not_apply(store):
    queue(store);before=heads(store)
    def model(packet,*a,**kw):
        changed=nearby('sports');changed['name']+='更正'
        store.ingest(sample('sports',changed))
        return unified_candidate(packet),{}
    catalog.review_one(store,None,model=model)
    assert heads(store)==before and store.rows('operation_updates')[0]['status']=='failed'
    assert json.loads(store.rows('operation_jobs')[-1]['result'])['workflow']


def test_old_pending_queue_uses_new_synthesis_contract(store):
    queue(store)
    update=store.rows('operation_updates')[0]
    # A legacy queued proposal uses its stable admission/source IDs and has no pipeline tag.
    with store.db:
        store.db.execute('UPDATE operation_updates SET novelty=? WHERE id=?',
            (json.dumps(dict(kind='generalization',proposals=[dict(sample_id='school')],reason='历史待处理建议')),update['id']))
    catalog.review_one(store,None,model=lambda p,*a,**k:(unified_candidate(p),{}))
    assert store.rows('operation_updates')[0]['status']=='candidate'
    rev=revision(store,store.rows('operation_updates')[0]['revision_id'])
    assert rev['generation']['pipeline']=='workflow-synthesis-1' and len(rev['sources'])==3


def test_synthesis_input_limit_records_failure_without_model_call(store,monkeypatch):
    queue(store);before=heads(store)
    from proximic_ring.agent_workflow.operations import operation_jobs
    monkeypatch.setattr(operation_jobs,'MAX_INPUT_CHARS',10)
    assert catalog.review_one(store,None,model=lambda *a,**k:pytest.fail('oversized input sent'))
    assert heads(store)==before and store.rows('operation_updates')[0]['status']=='failed'
    assert '超过' in json.loads(store.rows('operation_updates')[0]['novelty'])['preflight_error']
    assert len(store.rows('operation_jobs'))==1  # only the completed admission
