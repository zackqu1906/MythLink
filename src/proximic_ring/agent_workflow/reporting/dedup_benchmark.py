"""Read-only extraction-input benchmark; no queue, LLM, embedding or desktop work.

Reads real recordings but only writes reports to --output. Size comparisons use
the SAME snapshot/serialization on both sides. Runtime is the median of local
compaction runs; it does not predict LLM latency or token/billing savings.
"""
from __future__ import annotations

import argparse
import copy
import html
import json
import statistics
import time
from pathlib import Path

from proximic_ring.agent_workflow.extraction.workflow_compaction import compact_packet, encoded, expand_model_packet
from proximic_ring.agent_workflow.extraction.workflow_evidence import build_evidence, MAX_INPUT_CHARS
from proximic_ring.agent_workflow.extraction.workflow_store import TERMINAL, atomic_json, atomic_text, now
from proximic_ring.agent_workflow.recording.records import read_session, _page


def benchmark_packet(packet, repeats=7):
    """Verify every reconstructed entry, including identity/order and content.

Only the full event refs and duplicate nested call refs are projected out. The
source packet is checked for mutation. No result, failed call, user correction,
or repeated action may disappear from the reconstructed model representation.
"""
    before = encoded(packet)
    timings = []
    for _ in range(repeats):
        start = time.perf_counter()
        compact, stats = compact_packet(packet)
        timings.append((time.perf_counter()-start)*1000)
    expanded = expand_model_packet(compact)
    expected = copy.deepcopy({k:v for k,v in packet.items() if k not in ('fingerprint','fingerprint_version')})
    if stats['mode'] != 'original':
        for entry in expected.get('evidence', []):
            refs = entry.pop('event_refs', [])
            seqs = [r['seq'] for r in refs if isinstance(r.get('seq'), int)]
            entry['event_seq_range'] = [min(seqs), max(seqs)] if seqs else None
            content = entry['content']
            if (entry['kind']=='call' and isinstance(content,dict) and content.get('event_refs')==refs):
                content.pop('event_refs',None)
    if expanded != expected or encoded(packet) != before:
        raise ValueError('去重完整性检查失败；未写入提取队列、未调用模型')
    counts = {}
    for entry in packet['evidence']:
        counts[entry['kind']] = counts.get(entry['kind'],0)+1
    return dict(**stats, input_limit_chars=MAX_INPUT_CHARS,
        within_limit=stats['model_input_chars']<=MAX_INPUT_CHARS,
        over_limit_chars=max(0,stats['model_input_chars']-MAX_INPUT_CHARS),
        compaction_median_ms=round(statistics.median(timings),3), repeats=repeats,
        evidence_counts=counts, round_trip_verified=True, source_snapshot_unchanged=True,
        previously_truncated_entries=sum(bool(e.get('truncated')) for e in packet['evidence']))


def write_report(output, root, rows):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    report = dict(schema_version=1, created_at=now(), source_root=str(root),
        model_calls=0, queue_modified=False, rows=rows,
        measurement='Compact JSON characters; not tokens. Timings cover local compaction only.')
    atomic_json(output/'report.json', report)
    esc = html.escape
    body=(f"<p><a href='{esc((root/'overview.html').as_uri(),quote=True)}'>← 全流程总览</a></p>"
          '<p>同一份任务证据去重前后对比。只读已有记录，不入队、不调用模型、不操作电脑。</p>'
          '<p>字符数按实际发送的紧凑 JSON 统计，不等于 token 或费用；耗时是本地去重的多次中位数，不是模型耗时。</p>'
          f'<p>当前模型输入限制：{MAX_INPUT_CHARS:,} 字符。</p>')
    body+='<table><thead><tr><th>任务</th><th>去重前</th><th>去重后</th><th>减少</th><th>本地耗时</th><th>能否进入提取</th></tr></thead><tbody>'
    for row in rows:
        outcome='满足长度限制' if row['within_limit'] else f"仍超出 {row['over_limit_chars']:,} 字符"
        body+=(f"<tr><td>{esc(row['goal'])}</td><td>{row['original_chars']:,}</td>"
               f"<td>{row['model_input_chars']:,}</td><td>{row['reduction_percent']}%</td>"
               f"<td>{row['compaction_median_ms']} ms</td><td>{outcome}</td></tr>")
    body+='</tbody></table><p>只共享完全相同的大段内容；每次调用与每项观察保留独立身份。完整事件引用保留在本地证据快照。</p>'
    for row in rows:
        source=root/'sessions'/row['session_id']/('episode-'+row['episode_id']+'.html')
        body+=f"<details><summary>{esc(row['goal'])} · 完整性与分项统计</summary>"
        if source.exists():body+=f"<p><a href='{esc(source.as_uri(),quote=True)}'>原始任务记录</a></p>"
        body+='<pre>'+esc(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></details>'
    body+='<p>完整性检查逐项还原共享内容，核对证据顺序、身份和正文；不会把既有的单条截断重新补齐或掩盖。</p>'
    atomic_text(output/'report.html', _page('Workflow 输入去重测试',body))
    return output/'report.html'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,help='默认写入 <root>/workflow-dedup；不修改业务数据')
    parser.add_argument('--session');parser.add_argument('--episode')
    parser.add_argument('--repeats',type=int,default=7)
    args=parser.parse_args(argv)
    if bool(args.session)!=bool(args.episode):parser.error('--session 与 --episode 必须同时指定')
    if not 1<=args.repeats<=100:parser.error('--repeats 必须在 1–100 之间')
    from proximic_ring.agent_workflow.extraction.workflows import task_list
    root=args.root.expanduser().resolve()
    tasks=[t for t in task_list(root) if t['status'] in TERMINAL]
    if args.session:
        tasks=[t for t in tasks if t['session_id']==args.session and t['id']==args.episode]
    if not tasks:parser.error('没有匹配的已结束任务')
    rows=[]
    for task in tasks:
        packet=build_evidence(read_session(root/'sessions'/task['session_id']),task['id'])
        row=benchmark_packet(packet,args.repeats)
        row.update(session_id=task['session_id'],episode_id=task['id'],goal=task['goal'])
        rows.append(row)
    print(write_report(args.output or root/'workflow-dedup',root,rows))
    for row in rows:
        print(json.dumps({k:row[k] for k in ('episode_id','original_chars','model_input_chars',
            'reduction_percent','compaction_median_ms','within_limit','round_trip_verified')},ensure_ascii=False))


if __name__=='__main__':main()
