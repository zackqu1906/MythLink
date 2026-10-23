"""Report a fresh parameter-stage replay against frozen workflows; no model calls."""
import argparse
import hashlib
import html
import json
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.parameters.parameter_history import current_records
from proximic_ring.agent_workflow.recording.records import _page
from proximic_ring.agent_workflow.reporting.overview import export_overview


def stats(jobs):
    generations=[j['generation'] for j in jobs if j['generation'].get('thinking_requested')]
    return dict(calls=len(generations),failed=sum(j['status']=='failed' for j in jobs),
        input_tokens=sum(g.get('usage',{}).get('input_tokens',0) for g in generations),
        output_tokens=sum(g.get('usage',{}).get('output_tokens',0) for g in generations),
        total_tokens=sum(g.get('usage',{}).get('total_tokens',0) for g in generations),
        seconds=round(sum(g.get('elapsed_seconds',0) for g in generations),3))


def main(baseline,root):
    previous=json.loads((baseline/'measurements.json').read_text())
    old=[j for j in previous['jobs'] if j['kind']=='parameters']
    store=OperationStore(root)
    try:
        jobs=[dict(j,input=json.loads(j['input']),result=json.loads(j['result']),generation=json.loads(j['generation']))
              for j in store.rows('operation_jobs') if j['kind']=='parameters']
        records=list(current_records(store,scope='user'))
        heads=[dict(r) for r in store.db.execute('SELECT * FROM operation_heads')]
        assert not store.rows('recommendation_executions') and not store.rows('recommendation_exposures')
        export_operations(store)
    finally:store.close()
    export_overview(root)
    manifest=json.loads((root/'manifest.json').read_text())
    origin=json.loads((baseline/'manifest.json').read_text())
    source=Path(origin['source'])
    hashes={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((source/'sessions').rglob('*')) if p.is_file() and p.name in ('events.jsonl','session.json','index.sqlite3')}
    before=stats(old);after=stats(jobs);first=[];seen=set()
    for j in old:
        sid=j['input']['sample_id']
        if sid not in seen:first.append(j);seen.add(sid)
    verification=root/'verification';verification.mkdir(exist_ok=True)
    tests={}
    for name in ('final','ui','native','baseline-ui-test'):
        filename=f'proximic-parameter-{name}-tests.xml' if name!='baseline-ui-test' else 'proximic-parameter-baseline-ui-test.xml'
        path=Path('/tmp')/filename
        if path.exists():
            shutil.copy2(path,verification/path.name)
            tests[name]=[s.attrib for s in ET.parse(path).getroot().findall('testsuite')]
    handoff=json.loads((root/'handoff-check.json').read_text())
    result=dict(scope='same frozen upstream outputs; fresh parameters, recommendation and test-only command assembly',
        baseline=before,baseline_first_attempts=stats(first),updated=after,
        latest_statuses=dict(Counter(r['status'] for r in records)),records=records,jobs=jobs,
        handoff=handoff,source_unchanged=hashes==origin['source_hashes'],heads_unchanged=heads==manifest['fixed_heads'],tests=tests,
        token_reduction_percent=round((1-after['total_tokens']/before['total_tokens'])*100,2),
        latency_reduction_percent=round((1-after['seconds']/before['seconds'])*100,2))
    assert result['source_unchanged'] and result['heads_unchanged']
    assert len(records)==4 and all(r['status']=='completed' for r in records)
    assert all(c['status']=='command_assembled_without_execution' for c in handoff['checks'])
    (root/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    implementation=root/'implementation';implementation.mkdir(exist_ok=True)
    backup=Path('/tmp/proximic-parameter-baseline-20261020')
    changes=[]
    for path in Path('src/proximic_ring/agent_workflow').rglob('*.py'):
        old_path=backup/path
        if not old_path.exists() or old_path.read_bytes()!=path.read_bytes():
            dest=implementation/path.relative_to('src/proximic_ring/agent_workflow');dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,dest);changes.append(dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (root/'implementation.json').write_text(json.dumps(changes,indent=2))
    e=html.escape
    body='<p><strong>四条有效任务首次全部通过；学校和运动设施的可选参数留空后，均可完成推荐到 Agent 命令的组装。</strong></p>'
    body+='<p>模型保持 deepseek-v4-1-flash-260910、火山方舟、思考开启。使用原来的六条记录及已冻结的提取/合并结果；两条中断任务没有有效 workflow，不新增参数调用。此次重跑参数提取、推荐和命令组装，没有重跑随机提取/泛化，也没有在桌面执行历史操作。</p>'
    body+='<p><a href="comparison.json">全部测量与来源</a> · <a href="recommendation-global.json">推荐及参数候选</a> · <a href="handoff-check.json">命令组装结果</a> · <a href="operations/index.html">通用流程与参数审计</a> · <a href="../'+baseline.name+'/index.html">上一轮报告</a></p>'
    body+='<h2>前后比较：仅参数阶段</h2><table><tr><th>指标</th><th>旧版（含重试）</th><th>新版</th></tr>'
    rows=[('首次通过','2 / 4','4 / 4'),('最终通过','3 / 4','4 / 4'),('模型调用',before['calls'],after['calls']),('失败响应',before['failed'],after['failed']),('输入 token',before['input_tokens'],after['input_tokens']),('输出 token（含思考）',before['output_tokens'],after['output_tokens']),('总 token',before['total_tokens'],after['total_tokens']),('API 累计秒数',before['seconds'],after['seconds'])]
    for row in rows:body+='<tr>'+''.join('<td>'+e(str(v))+'</td>' for v in row)+'</tr>'
    body+='</table><p>总 token 降低 '+str(result['token_reduction_percent'])+'%，API 耗时降低 '+str(result['latency_reduction_percent'])+'%。旧版首次四次调用共 '+str(stats(first)['total_tokens'])+' token，新版仍为四次首次调用；调用次数减少来自无需重试。这批样本有两个字段由规则解决，但没有任务完全免去 LLM。耗时是服务端请求累计时间，不含人工审阅、测试与本地处理；单批结果不代表稳定收益。</p>'
    body+='<h2>实际参数结果</h2><table><tr><th>任务</th><th>规则已确定</th><th>最终参数</th></tr>'
    labels={'257a2a963e184dc286f69c14a45344bc':'图片存入备忘录','6298cdffd4454572af2ecc4d5fc2fb9c':'附近餐厅','903bffa0f7ff4e86b699f83708d8b88f':'附近学校','1619f4531fd14a9c84d8d6a75c80ad66':'附近运动设施'}
    for r in reversed(records):
        values='\n'.join(p['name']+' = '+(p['value'] if p['status']=='observed' else '未指定') for p in r['parameters'])
        body+='<tr><td>'+labels[r['source']['episode_id']]+'</td><td>'+e('、'.join(r['generation'].get('rule_parameters',{})) or '交给 LLM 语义映射')+'</td><td><pre>'+e(values)+'</pre></td></tr>'
    body+='</table><p>返回数量“五”规范为“5”，原文和转换规则保留。学校/运动设施的筛选词、数量未指定，保持空值，没有为了填表编造默认值。四条命令均在 test 副本组装并检查重复派发保护，没有发送给 Agent 执行。</p>'
    body+='<h2>已实现的改动</h2><ul><li>规则先处理可信历史实例、明确字段、无冲突路径及明确数量；模型只判断剩余参数。规则完成全部参数时不调用模型。</li><li>提示词缩短为语义映射、来源和不确定处理；移除完整 workflow 步骤的重复输入。</li><li>missing/uncertain 的空字段由解析器补齐；observed 仍须有有效引用。数量转换保留原值，未知字段或无依据值仍被拒绝。</li><li>参数支持 required:false；兼容旧描述中明确的可选/未指定处理。推荐、原生表单、执行票据和 Trials 使用相同契约。</li><li>可选参数在表单中默认留空，可以从历史值切回留空；空选项不作为历史使用。票据保留空字符串，Agent 收到明确缺省说明。</li></ul>'
    body+='<h2>验证及边界</h2><p>最后一组相关回归：142 通过、1 跳过。扩展 UI 检查中，本机服务测试放开沙箱端口后通过；菜单焦点测试在改动前代码上也能复现失败，保留为已有问题，未混入本次参数改动。<a href="verification/proximic-parameter-final-tests.xml">最终回归结果</a> · <a href="verification/proximic-parameter-baseline-ui-test.xml">改动前菜单测试</a></p>'
    body+='<p>原始会话关键文件 SHA-256 一致；通用组版本与上一轮一致。最终代码加严冲突处理后，重新生成的四份模型输入与实测发送内容逐项相同，prompt 哈希也相同。<a href="final-input-verification.json">输入一致性核对</a> · <a href="implementation.json">最终代码文件清单</a></p>'
    body+='<p>仍需 LLM 判断含义不明确的值；本地引用校验只能证明值出现在来源中，不能独立证明语义归属。未对新任务泛化准确率、真实电脑执行成功率或关闭思考模式作结论。</p>'
    (root/'index.html').write_text(_page('参数规则优先与可选输入 · 真实模型复测 · 2026-10-20',body))
    print(json.dumps({k:result[k] for k in ('baseline','updated','token_reduction_percent','latency_reduction_percent','source_unchanged','heads_unchanged')},ensure_ascii=False))
    print(root/'index.html')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',required=True,type=Path);p.add_argument('--root',required=True,type=Path)
    a=p.parse_args();main(a.baseline.resolve(),a.root.resolve())
