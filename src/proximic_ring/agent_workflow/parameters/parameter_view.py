"""分组页的历史参数回查：候选、真实/测试范围、来源、缺失与失败原因。"""
import html
import json
from .parameter_history import current_records
from proximic_ring.agent_workflow.recommendation.recommendation_candidates import parameter_candidates


def render_parameters(store,group):
    names=[p['name'] for p in group['workflow']['parameters']]
    if not names:return ''
    oid=group['operation_id']; esc=html.escape
    records=list(current_records(store,operation_id=oid))
    body="<details class='group-section'><summary>Hub 历史参数候选与来源</summary>"
    body+='<p>只展示有记录依据的实际值，按不同任务的使用次数排序。上下滑切换；打开的 Hub 冻结候选，重开后更新。任务结束不代表业务结果已验证。</p>'
    for scope,label in (('user','真实用户任务'),('test','测试任务（不进入真实 Hub）')):
        options=parameter_candidates(store,oid,names,scope=scope)
        body+=f'<h3>{label}</h3>'
        for name in names:
            body+=f'<h4>{esc(group["display"]["parameters"].get(name,{}).get("label",name))} <code>{esc(name)}</code></h4>'
            if not options[name]:body+='<p>暂无有来源的候选，保留待填。</p>'
            for item in options[name]:
                preview=item['value'][:160]+('…' if len(item['value'])>160 else '')
                body+=f"<details><summary>{esc(preview)} · {item['count']} 次任务</summary><pre>{esc(item['value'])}</pre>"
                for src in item['sources']:
                    if src['kind']=='agent_task':
                        if src.get('value_status')=='mapped':body+='<p>此值按审阅后的参数契约由历史值组合，非用户逐字原文；映射与原值见来源记录。</p>'
                        if src.get('normalization'):
                            body+='<p>数量格式由本地规则统一；原文值：'+esc(src['normalization']['raw_value'])+'。原始引用保留。</p>'
                        body+=f"<p><a href='parameter-history/{src['record_id']}.json'>参数实例与原始事件引用</a> · 任务 {esc(src['episode_id'])}</p>"
                    else:body+=f"<p>Hub 执行票据：{esc(src['execution_id'])}</p>"
                body+='</details>'
        for record in records:
            if record['scope']!=scope:continue
            missing=[dict(name=p['name'],status=p['status'],reason=p['reason']) for p in record['parameters'] if p['status'] not in ('observed','mapped')]
            if missing or record['status']!='completed':
                note=dict(status=record['status'],missing=missing,error=record['generation'].get('error',''))
                body+=f"<details><summary>未生成候选的原因 · {esc(record['sample_id'][:8])}</summary><pre>{esc(json.dumps(note,ensure_ascii=False,indent=2))}</pre><a href='parameter-history/{record['id']}.json'>查看来源记录</a></details>"
    return body+'</details>'
