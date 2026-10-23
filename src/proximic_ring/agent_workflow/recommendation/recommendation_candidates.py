"""真实使用值候选：展示冻结时读取，绝不让模型编造参数默认值。

同一操作、当前通用版本、同一 scope 的完成票据和已核对的普通任务实例参与。
completed 只表示 Agent turn 完成，不宣称业务结果验证；这些是历史输入而非正确答案。
按任务出现次数降序，最近使用时间破同分；最多八项。相同 episode 不因重新
提取、多条来源流程或同时有 Hub 票据而重复计数；直接票据优先于模型映射。
同一次普通任务映射出互相冲突的值时不推荐该参数；保留历史供回查。
"""
import json
from collections import defaultdict


def parameter_candidates(store,operation_id,names,*,scope='user',limit=8,revision_id=None):
    from proximic_ring.agent_workflow.parameters.parameter_history import current_records
    if revision_id is None:
        head=store.db.execute('SELECT active_revision FROM operation_heads WHERE operation_id=?',(operation_id,)).fetchone()
        revision_id=head[0] if head else None
    values={name:defaultdict(lambda:dict(count=0,last_used='',execution_ids=[],sources=[])) for name in names}
    occurrences={}; direct=set()
    def add(name,value,episode,stamp,source,*,ticket=False):
        if name not in values or not isinstance(value,str) or not value.strip():return
        key=(episode,name)
        if key in direct and not ticket:return
        if ticket:direct.add(key)
        group=occurrences.setdefault(key,{})
        entry=group.setdefault(value,dict(last_used=stamp,sources=[]))
        entry['last_used']=max(entry['last_used'],stamp);entry['sources'].append(source)
    rows=store.db.execute("SELECT * FROM recommendation_executions "
        "WHERE operation_id=? AND revision_id=? AND scope=? AND state='completed' AND error='' ORDER BY updated_at DESC",
        (operation_id,revision_id,scope))
    for row in rows:
        episode=(row['session_id'],row['episode_id']) if row['session_id'] and row['episode_id'] else ('execution',row['id'])
        for name,value in json.loads(row['parameters']).items():
            add(name,value,episode,row['updated_at'],dict(kind='hub_execution',execution_id=row['id'],
                session_id=row['session_id'],episode_id=row['episode_id']),ticket=True)
    for record in current_records(store,operation_id=operation_id,scope=scope):
        if record['status']!='completed' or record['revision_id']!=revision_id:continue
        src=record['source']; episode=(src.get('session_id'),src.get('episode_id'))
        if not all(episode):continue
        for parameter in record['parameters']:
            if parameter['status'] not in ('observed','mapped'):continue
            add(parameter['name'],parameter['value'],episode,record['occurred_at'],dict(kind='agent_task',
                record_id=record['id'],sample_id=record['sample_id'],session_id=episode[0],episode_id=episode[1],
                source_file=src.get('source_file'),evidence=parameter['evidence'],
                value_status=parameter['status'],migration=parameter.get('migration'),
                normalization=parameter.get('normalization')))
    for (_,name),options in occurrences.items():
        if len(options)!=1:continue
        value,occurrence=next(iter(options.items())); item=values[name][value]; item['count']+=1
        item['last_used']=max(item['last_used'],occurrence['last_used']);item['sources'].extend(occurrence['sources'])
        item['execution_ids'].extend(s['execution_id'] for s in occurrence['sources'] if s['kind']=='hub_execution')
    return {name:[dict(value=value,**info) for value,info in
        sorted(options.items(),key=lambda pair:(pair[1]['count'],pair[1]['last_used'],pair[0]),reverse=True)[:limit]]
        for name,options in values.items()}
