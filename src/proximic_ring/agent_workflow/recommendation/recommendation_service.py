"""主动请求入口：排序所有组、返回 Top-K，供网页和以后手势入口共用。

调用本函数不计曝光、不执行 Workflow、不主动弹窗。调用方展示后需确认曝光，
选择后拿到冻结的通用版本及缺失参数；原生执行层负责参数收集和用户启动动作。
"""
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow import pipeline_policy


def request_recommendations(store, context=None, *, scope='user', limit=4, trigger='manual', surface='web'):
    pipeline_policy.require_legacy_workflow()
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('展示数量必须为 1 到 20 的整数')
    if trigger not in ('manual', 'gesture', 'cli'):
        raise ValueError('未知推荐触发来源')
    result = recommend(store, context, scope=scope, limit=limit)
    from proximic_ring.agent_workflow.recording.recording import redact
    from proximic_ring.agent_workflow.recommendation.recommendation_catalog import catalog_state
    from .recommendation_candidates import parameter_candidates
    for item in result['items']:
        item['parameter_candidates']=parameter_candidates(store,item['operation_id'],
            [p['name'] for p in item['workflow']['parameters']],scope=scope,revision_id=item['workflow_revision'])
    result['invocation_context'] = redact(context or {})
    result['catalog'] = catalog_state(store)
    result['trigger'] = trigger
    result['request_id'] = store.remember(result, scope, surface=surface)
    return result


def selection(store, exposure_id):
    import json
    row = store.db.execute('''SELECT e.operation_id,e.position,r.snapshot FROM recommendation_exposures e
        JOIN recommendation_requests r ON r.id=e.request_id WHERE e.id=?''', (exposure_id,)).fetchone()
    if not row:
        raise ValueError('没有对应曝光')
    item = next(i for i in json.loads(row['snapshot'])['items'] if i['operation_id'] == row['operation_id'])
    return dict(position=row['position'], **{key: item[key] for key in
                ('operation_id', 'workflow_revision', 'workflow', 'missing_parameters')})
