"""Reproducible offline retrieval benchmark; never executes a desktop workflow."""
from __future__ import annotations

import html
import json
import math
import platform
import statistics
import time
import uuid
from collections import Counter
from pathlib import Path

from proximic_ring.agent_workflow.operations.operation_embeddings import ensure_embeddings, workflow_texts
from proximic_ring.agent_workflow.operations.operation_matching import candidates, digest, normalize, app_tags
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json, now
from proximic_ring.agent_workflow.recording.records import _page, _write


def legacy_tfidf(texts):
    """Frozen former production baseline; retained only in this experiment."""
    counts=[];df=Counter()
    for text in texts:
        text=normalize(text)
        count=Counter(text[i:i+n] for n in (2,3,4) for i in range(max(0,len(text)-n+1)))
        counts.append(count);df.update(count.keys())
    result=[]
    for count in counts:
        value={t:(1+math.log(c))*(1+math.log((1+len(texts))/(1+df[t]))) for t,c in count.items()}
        norm=math.sqrt(sum(v*v for v in value.values())) or 1
        result.append({t:v/norm for t,v in value.items()})
    return result


def legacy_pairs(versions):
    ids=sorted(versions);flows=[versions[v] for v in ids]
    goals=legacy_tfidf([f['name']+' '+f['goal'] for f in flows])
    steps=legacy_tfidf([' '.join(s['action']+' '+s.get('method',s.get('target','')) for s in f['steps']) for f in flows])
    def sim(a,b):return sum(v*b.get(k,0) for k,v in a.items())
    pairs=[]
    for i,left in enumerate(ids):
        for j in range(i+1,len(ids)):
            goal=sim(goals[i],goals[j]);method=sim(steps[i],steps[j]);score=.75*goal+.25*method
            a,b=app_tags(flows[i]),app_tags(flows[j]);conflict=bool(a and b and a!=b)
            pairs.append(dict(left=left,right=ids[j],score=score,goal_similarity=goal,method_similarity=method,
                              decision='pending' if not conflict and goal>=.18 and score>=.30 else 'rejected'))
    return pairs


def evaluate_pairs(pairs,labels):
    positive=[p for p in pairs if labels[p['left']]==labels[p['right']]]
    selected=[p for p in pairs if p['decision']=='pending']
    hits=sum(p in selected for p in positive)
    top1=0
    for vid in labels:
        ranked=sorted([p for p in pairs if vid in (p['left'],p['right'])],key=lambda p:-p['score'])
        best=ranked[0];other=best['right'] if best['left']==vid else best['left']
        top1+=labels[other]==labels[vid]
    return dict(positive_pairs=len(positive),recalled_pairs=hits,candidate_pairs=len(selected),
                candidate_precision=hits/len(selected) if selected else None,
                candidate_recall=hits/len(positive),leave_one_out_top1=top1/len(labels))


def timed(call,repeats):
    values=[];result=None
    for _ in range(repeats):
        start=time.perf_counter();result=call();values.append(time.perf_counter()-start)
    return result,dict(repeats=repeats,median_seconds=statistics.median(values),min_seconds=min(values),max_seconds=max(values))


def run(root,dataset):
    root,dataset=Path(root),Path(dataset)
    spec=json.loads(Path(__file__).with_name('embedding_cases.json').read_text())
    versions={};labels={};names={};sources={}
    for path in sorted((dataset/'samples').glob('*.json')):
        sample=json.loads(path.read_text());flow=sample['flow'];vid=digest(flow)
        versions[vid]=flow;labels[vid]=spec['labels'][sample['id']];names[vid]=flow['name'];sources[vid]=sample['id']
    directory=root/'operations/embedding-benchmark'
    run_dir=directory/'runs'/uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    print('编码已有六条 Workflow（CPU，两线程，独立进程）…',flush=True)
    vectors,cold=ensure_embeddings(run_dir/'cache',versions)
    _,warm=timed(lambda:ensure_embeddings(run_dir/'cache',versions),10)
    dense,dense_time=timed(lambda:candidates(versions,vectors),20)
    legacy,legacy_time=timed(lambda:legacy_pairs(versions),20)
    query_versions={}
    for q in spec['queries']:
        query_versions[q['id']]=dict(name=q['name'],goal=q['goal'],parameters=[],conditions=[],fallbacks=[],
                                    steps=[dict(action=q['name'],method=q['method'],check='核验目标完成')])
    print('编码手写同义查询与近义反例（不写入操作库）…',flush=True)
    qvectors,query_metrics=ensure_embeddings(run_dir/'cache',query_versions)
    query_results=[]
    for query in spec['queries']:
        qid=query['id'];combined={**versions,qid:query_versions[qid]}
        dense_q=[p for p in candidates(combined,{**vectors,qid:qvectors[qid]}) if qid in (p['left'],p['right'])]
        legacy_q=[p for p in legacy_pairs(combined) if qid in (p['left'],p['right'])]
        entry=dict(query=query)
        for algorithm,pairs in [('bge_m3',dense_q),('tfidf',legacy_q)]:
            ranked=sorted(pairs,key=lambda p:-p['score'])
            items=[]
            for p in ranked:
                other=p['right'] if p['left']==qid else p['left']
                items.append(dict(name=names[other],version_id=other,label=labels[other],score=p['score'],candidate=p['decision']=='pending'))
            entry[algorithm]=dict(ranked=items,top1_correct=bool(query['label'] and items[0]['label']==query['label']),
                relevant_recalled=any(i['candidate'] and i['label']==query['label'] for i in items) if query['label'] else None,
                candidates=sum(i['candidate'] for i in items))
        query_results.append(entry)
    summaries={}
    for algorithm in ('bge_m3','tfidf'):
        positives=[q for q in query_results if q['query']['label']]
        negatives=[q for q in query_results if not q['query']['label']]
        summaries[algorithm]=dict(paraphrase_top1=sum(q[algorithm]['top1_correct'] for q in positives),
            paraphrase_recalled=sum(q[algorithm]['relevant_recalled'] for q in positives),paraphrase_total=len(positives),
            negative_queries_with_candidates=sum(q[algorithm]['candidates']>0 for q in negatives),negative_total=len(negatives))
    report=dict(created_at=now(),platform=platform.platform(),cpu=platform.machine(),
        model=cold,query_encoding=query_metrics,workflow_count=len(versions),
        definitions=dict(cold='新进程且无向量缓存；不清空操作系统文件缓存；下载/安装不计入',
                         memory='向量子进程 RSS，每20毫秒采样；不含其他应用、文件磁盘占用或整机统一内存',
                         labels='现有6条测试Workflow人工分为3类；另12条手写查询只作诊断，不是真实用户样本'),
        source_ids=sources,names=names,
        bge_m3=dict(quality=evaluate_pairs(dense,labels),retrieval=dense_time,cache_read=warm,pairs=dense),
        tfidf=dict(quality=evaluate_pairs(legacy,labels),retrieval=legacy_time,pairs=legacy),
        query_summary=summaries,queries=query_results,
        limitations=['只有6条实际流程、3类操作，不能证明泛化或个性化推荐准确率。',
                     '召回只是候选；近义反例仍可能高分，必须保留后台模型复核。',
                     '本次不执行桌面任务，不修改真实使用计数或推荐反馈。'])
    probe=directory/'initial-import-probe.json'
    if probe.exists():report['initial_import_probe']=json.loads(probe.read_text())
    atomic_json(run_dir/'report.json',report);atomic_json(directory/'report.json',report)
    render_report(report,directory,run_dir)
    print(json.dumps({k:report[k] for k in ('workflow_count','query_summary')},ensure_ascii=False,indent=2),flush=True)
    print(directory/'index.html',flush=True)
    return report


def render_report(report,directory,run_dir):
    """Render saved measurements without rerunning inference or changing timings."""
    directory,run_dir=Path(directory),Path(run_dir)
    cold=report['model'];warm=report['bge_m3']['cache_read']
    dense_time=report['bge_m3']['retrieval'];legacy_time=report['tfidf']['retrieval']
    summaries=report['query_summary'];query_results=report['queries']
    dense=report['bge_m3']['pairs'];names=report['names']
    esc=html.escape
    body='<p><a href="../index.html">返回操作库</a> · <a href="report.json">完整原始测量 JSON</a></p>'
    body+='<p>实际六条 Workflow 留一检索 + 手写同义改写/近义反例。没有重放桌面操作，没有调用 LLM 评判这些测试标签。</p>'
    body+=f"<p>模型：BGE-M3 · CPU 两线程 · FP32 · {report['workflow_count']} 条流程 / {cold['requested']} 个向量</p>"
    body+='<table><tr><th>指标</th><th>TF-IDF</th><th>BGE-M3</th></tr>'
    for label,key in [('正例对召回率','candidate_recall'),('候选精确率','candidate_precision'),('留一 Top-1 命中率','leave_one_out_top1')]:
        body+=f"<tr><td>{label}</td><td>{report['tfidf']['quality'][key]:.1%}</td><td>{report['bge_m3']['quality'][key]:.1%}</td></tr>"
    body+=f"<tr><td>召回计算中位数（不含编码）</td><td>{legacy_time['median_seconds']*1000:.2f} ms</td><td>{dense_time['median_seconds']*1000:.2f} ms</td></tr></table>"
    body+=f"<p>无向量缓存总耗时 {cold['total_seconds']:.2f} 秒；导入库 {cold.get('import_seconds',0):.2f} 秒；加载模型 {cold.get('load_seconds',0):.2f} 秒；实际编码 {cold.get('encoding_seconds',0):.2f} 秒。</p>"
    body+=f"<p>向量进程峰值 RSS：{cold.get('peak_rss_bytes',0)/1024**3:.2f} GiB；缓存读取中位数：{warm['median_seconds']*1000:.2f} ms。完成后进程退出释放模型。</p>"
    if 'initial_import_probe' in report:
        probe=report['initial_import_probe']
        body+=f"<p>独立记录的首次依赖导入探测：共 {probe['total_seconds']:.2f} 秒，其中导入库 {probe['import_seconds']:.2f} 秒、编码 {probe['encoding_seconds']:.2f} 秒。首次安装后的启动可能明显更慢；此探测不计入上面的正式运行，原始数据保存在报告 JSON 的 initial_import_probe。</p>"
    body+='<p>这些数字不包含 LLM 复核；后台进程隔离不能证明完全没有资源竞争。</p>'
    body+='<h2>同义改写与反例</h2><pre>'+esc(json.dumps(summaries,ensure_ascii=False,indent=2))+'</pre>'
    for q in query_results:
        body+=f"<details><summary>{esc(q['query']['name'])} · {'同义正例' if q['query']['label'] else '不同操作反例'}</summary><pre>{esc(json.dumps(q,ensure_ascii=False,indent=2))}</pre></details>"
    body+='<h2>现有流程的逐对分数</h2>'
    for p in dense:
        body+=f"<details><summary>{esc(names[p['left']])} ↔ {esc(names[p['right']])}：{p['score']:.3f} / {p['decision']}</summary><pre>{esc(json.dumps(p,ensure_ascii=False,indent=2))}</pre></details>"
    body+='<p>'+esc(' '.join(report['limitations']))+'</p>'
    _write(directory/'index.html',_page('BGE-M3 召回与资源实测',body))
    _write(run_dir/'index.html',_page('BGE-M3 历史实测',body.replace('../index.html','../../../index.html')))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--dataset',type=Path,default=Path(__file__).with_name('dataset'))
    args=parser.parse_args();run(args.root,args.dataset)
