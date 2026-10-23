"""Rule/model boundaries, real failure shapes and optional execution contracts."""
import copy
import json
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from test_parameter_history import history,task,model
from test_recommendation_execution import catalog,displayed
from proximic_ring.agent_workflow.parameters.parameter_evidence import validate
from proximic_ring.agent_workflow.parameters.parameter_rules import resolve,model_packet
from proximic_ring.agent_workflow.parameters.parameter_jobs import process_parameters
from proximic_ring.agent_workflow.parameters.parameter_history import current_records
from proximic_ring.agent_workflow.parameters.parameter_contract import required,bind_parameters
from proximic_ring.agent_workflow.operations.operation_prototypes import add_revision
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution,dispatch_command
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.workflow_content import validate_plain


def block(text,identifier='B1',role='user_input',call_id=None):
    return dict(id=identifier,text=text,role=role,field='/arguments/code',
                source=dict(call_id=call_id,file='input.json',sha256='hash',json_pointer='/text',transform=None))


def packet(params,blocks):
    flow=dict(name='任务',goal='当前任务',parameters=[dict(name=p,description=p) for p in params])
    return dict(general_workflow=flow,source_workflow=flow,evidence=blocks,warnings=[])


@pytest.mark.parametrize('missing',[{},dict(value=None,evidence=None)])
def test_missing_empty_fields_are_adapted_without_accepting_observed_fabrications(missing):
    flow=dict(parameters=[dict(name='filter',description='可选')])
    original=dict(parameters=[dict(name='filter',status='missing',**missing)])
    saved=copy.deepcopy(original)
    assert validate(original,flow,[])[0]['value']==''
    assert original==saved
    with pytest.raises(ValueError):
        validate(dict(parameters=[dict(name='filter',status='observed',value='invented')]),flow,[])
    with pytest.raises(ValueError):
        validate(dict(parameters=[dict(name='filter',status='missing',value='invented')]),flow,[])


def test_quantity_normalization_keeps_exact_evidence_and_rejects_wrong_value():
    b=block('返回评分前五家餐厅')
    flow=dict(parameters=[dict(name='result_count',description='数量')])
    p=dict(name='result_count',status='observed',value=5,evidence=[dict(block_id='B1',quote=b['text'])])
    result=validate(dict(parameters=[p]),flow,[b])[0]
    assert result['value']=='5' and result['normalization']['raw_value']=='五'
    p['value']='6'
    with pytest.raises(ValueError):validate(dict(parameters=[p]),flow,[b])
    p.update(name='phone_number',value='5')
    with pytest.raises(ValueError):validate(dict(parameters=[p]),dict(parameters=[dict(name='phone_number')]),[b])


def test_rules_abstain_on_conflicts_examples_and_metadata():
    blocks=[block('{"location":"北京"}'),block('{"location":"上海"}','B2')]
    assert resolve(packet(['location'],blocks),blocks)[0]=={}
    b=block('别的文件',role='tool_input');b['field']='/arguments/title'
    assert resolve(packet(['title'],[b]),[b])[0]=={}
    p=packet(['location'],[]);p['general_workflow']['parameters'][0]['description']='例如北京'
    assert resolve(p,[])[0]=={}
    blocks=[block('返回前五家'),block('改成十家','B2')]
    assert resolve(packet(['result_count'],blocks),blocks)[0]=={}
    blocks=[block('前五分钟查找餐厅')]
    assert resolve(packet(['result_count'],blocks),blocks)[0]=={}
    blocks=[block('返回前五家'),block('要十家','B2')]
    assert resolve(packet(['result_count'],blocks),blocks)[0]=={}


def test_unique_input_path_and_explicit_count_leave_only_semantic_roles_for_model():
    blocks=[block('await app.paste("/tmp/photo.png")','B1','tool_input','call1'),
            block('await app.setValue(12,"/tmp/photo.png")','B2','tool_input','call2'),
            block('返回前五项','B3')]
    p=packet(['image_file_path','result_count','note_title'],blocks)
    found,methods=resolve(p,blocks)
    assert found['image_file_path']['value']=='/tmp/photo.png'
    assert found['result_count']['value']=='五'
    assert [v['name'] for v in model_packet(p,found)['general_workflow']['parameters']]==['note_title']
    blocks.append(block('await app.paste("/tmp/other.png")','B4','tool_input','call3'))
    assert 'image_file_path' not in resolve(packet(['image_file_path'],blocks),blocks)[0]


def test_complete_structured_task_avoids_model_and_partial_task_uses_only_remaining(history):
    sid=task(history,'structured',goal=json.dumps(dict(title='明确标题',body='明确正文'),ensure_ascii=False))
    assert process_parameters(history,None,sample_id=sid,model=lambda *a,**k:pytest.fail('rules should finish'))
    rec=next(current_records(history))
    assert rec['generation']['method']=='rules' and {p['value'] for p in rec['parameters']}=={'明确标题','明确正文'}
    sid=task(history,'partial',goal=json.dumps(dict(title='明确标题'),ensure_ascii=False))
    def remaining(p,*a,**kw):
        assert [x['name'] for x in p['general_workflow']['parameters']]==['body']
        b=next(b for b in p['evidence'] if '第一行\n第二行' in b['text'])
        return dict(parameters=[dict(name='body',status='observed',value='第一行\n第二行',
            evidence=[dict(block_id=b['id'],quote=b['text'])])]),dict(thinking_requested='enabled')
    assert process_parameters(history,None,sample_id=sid,model=remaining)
    rec=next(current_records(history))
    assert rec['generation']['model_parameters']==['body'] and rec['status']=='completed'


def test_reuse_verified_mapping_checks_source_hash(history):
    assert process_parameters(history,None,model=model)
    (history.root/'workflows/one.input.json').touch()
    assert process_parameters(history,None,model=lambda *a,**k:pytest.fail('same evidence reuses values'))
    assert next(current_records(history))['generation']['method']=='rules'


@pytest.mark.parametrize('parameter,expected',[
    (dict(description='可选的筛选词'),False),
    (dict(description='数量；未指定时询问用户'),False),
    (dict(description='可填写文件路径'),True),
    (dict(description='必须提供；未指定时不能执行'),True),
    (dict(description='不是可选项'),True),
    (dict(description='可选的筛选词',required=True),True),
    (dict(description='输出范围',required=False),False),
])
def test_required_compatibility_is_explicit(parameter,expected):
    assert required(parameter)==expected


def test_optional_contract_ranking_and_execution_preserve_empty_history(catalog):
    _,item,_=displayed(catalog)
    flow=dict(name='查学校',goal='查找 {{location}} 的学校',parameters=[
        dict(name='location',description='中心地点'),
        dict(name='filter',description='学校类型',required=False),
        dict(name='count',description='数量；未指定时按代表性数量返回')],
        steps=['查找 {{location}}，用 {{filter}} 筛选，返回 {{count}} 个结果。'])
    validate_plain(flow)
    with catalog.db:
        rid=add_revision(catalog,item['operation_id'],flow,parent=item['workflow_revision'])
        catalog.db.execute('UPDATE operation_heads SET active_revision=?',(rid,))
    r=request_recommendations(catalog,surface='cli');i=r['items'][0]
    assert i['missing_parameters']==['location'] and i['optional_parameters']==['filter','count']
    exposure=catalog.expose(r['request_id'],i['operation_id'],method='cli_confirmed')
    with pytest.raises(ValueError):prepare_execution(catalog,exposure,dict(location=''))
    command=dispatch_command(catalog,prepare_execution(catalog,exposure,dict(location='原点大厦')))
    saved=catalog.rows('recommendation_executions')[0]
    assert json.loads(saved['parameters'])==dict(location='原点大厦',filter='',count='')
    assert '{{' not in command['prompt'] and '未指定' in command['prompt']
    assert '不是实际输入' in command['prompt']
    with pytest.raises(ValueError):prepare_execution(catalog,exposure,dict(location='原点大厦'))
    with pytest.raises(ValueError):bind_parameters(flow,dict(location='原点大厦',extra='x'))
