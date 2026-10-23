"""Compare raw / dedup-only / selected+dedup input for one recorded task.

No real model call, no desktop action, no production queue writes. Validate the
public extraction entry and enqueue in an isolated temporary directory only.
Reports and prepared-input samples are the only persistent writes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import statistics
import tempfile
import time
from pathlib import Path

from proximic_ring.agent_workflow.extraction.workflow_compaction import compact_packet, encoded
from proximic_ring.agent_workflow.extraction.workflow_evidence import MAX_INPUT_CHARS, build_evidence
from proximic_ring.agent_workflow.extraction.workflow_input import prepare_model_input
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore, atomic_json, atomic_text, now
from proximic_ring.agent_workflow.recording.records import read_session, _page


def _at(value,path):
    for part in path:value=value[part]
    return value


def verify_selection(packet, prepared):
    """Check unchanged facts + every actual omission's original source.

Every preserved entry is compared with the source after putting back ONLY
explicitly declared documentation omissions. Every missing entry must belong
to a declared routine-permission pair. All action and observation entries must
match exactly, including arguments, full text, identity and provenance.
"""
    source={e['id']:e for e in packet['evidence']}
    restored=copy.deepcopy(prepared.trajectory['evidence'])
    destination={e['id']:e for e in restored}
    omitted=set()
    for change in prepared.selection['changes']:
        ids=change['evidence_ids']
        if change['rule']=='verified_tool_documentation':
            key=ids[0];path=change['content_path']
            body=_at(source[key]['content'],path)
            assert hashlib.sha256(body.encode()).hexdigest()==change['sha256']
            parent=_at(destination[key]['content'],path[:-1]);parent[path[-1]]=body
        elif change['rule']=='routine_automatic_approval':
            assert all(source[key]['kind']=='control_event' for key in ids)
            omitted.update(ids)
        else:raise AssertionError('Unknown selection rule')
    assert restored==[e for e in packet['evidence'] if e['id'] not in omitted]
    for kind in ('call','observation','message'):
        assert [e for e in prepared.trajectory['evidence'] if e['kind']==kind] == [e for e in packet['evidence'] if e['kind']==kind]
    return dict(all_calls_unchanged=True,all_observations_unchanged=True,all_messages_unchanged=True,
                all_omissions_traceable=True)


def benchmark_packet(packet,repeats=9):
    before=encoded(packet)
    baseline_times=[];selected_times=[]
    for _ in range(repeats):
        start=time.perf_counter();_,baseline=compact_packet(packet)
        baseline_times.append((time.perf_counter()-start)*1000)
        start=time.perf_counter();prepared=prepare_model_input(packet)
        selected_times.append((time.perf_counter()-start)*1000)
    checks=verify_selection(packet,prepared)
    # Independently validate exact dedup reconstruction using the existing
    # benchmark against the selected trajectory, not against raw audit data.
    from proximic_ring.agent_workflow.reporting.dedup_benchmark import benchmark_packet as check_dedup
    check_dedup(prepared.trajectory,repeats=1)
    assert encoded(packet)==before
    final_chars=prepared.stats['model_input_chars']
    report=dict(schema_version=1,created_at=now(),model_calls=0,production_queue_modified=False,
        session_id=packet['source']['session_id'],episode_id=packet['source']['episode_id'],
        goal=packet['source']['episode']['goal'],input_limit_chars=MAX_INPUT_CHARS,
        raw_chars=baseline['original_chars'],dedup_only_chars=baseline['model_input_chars'],
        selected_before_dedup_chars=prepared.stats['trajectory_chars'],final_chars=final_chars,
        reduction_vs_raw_percent=prepared.stats['reduction_percent'],
        reduction_vs_dedup_percent=round(100*(baseline['model_input_chars']-final_chars)/baseline['model_input_chars'],2),
        dedup_only_median_ms=round(statistics.median(baseline_times),3),
        selection_and_dedup_median_ms=round(statistics.median(selected_times),3),repeats=repeats,
        within_limit=final_chars<=MAX_INPUT_CHARS,remaining_chars=MAX_INPUT_CHARS-final_chars,
        selection=prepared.stats['trajectory_selection'],checks={**checks,'dedup_round_trip_verified':True,'source_snapshot_unchanged':True},
        scope='Local input processing only; excludes recording I/O and all model latency; counts characters, not tokens.')
    return report,prepared


def verify_entry(root,session_id,episode_id,prepared):
    from proximic_ring.agent_workflow.extraction.workflows import source_packet
    try:packet=source_packet(root,session_id,episode_id)
    except ValueError as exc:return dict(passed=False,error=str(exc),real_model_called=False)
    if prepare_model_input(packet).model_packet!=prepared.model_packet:
        return dict(passed=False,error='读取期间来源发生变化；请重新测量',real_model_called=False)
    with tempfile.TemporaryDirectory(prefix='workflow-trajectory-gate-') as directory:
        store=WorkflowStore(Path(directory))
        try:
            job=store.enqueue(packet)
            assert len(store.jobs())==1 and store.jobs()[0]['status']=='queued'
            assert json.loads((store.directory/(job+'.model-input.json')).read_text())==prepared.model_packet
            return dict(passed=True,source_packet_checked=True,isolated_enqueue_checked=True,real_model_called=False)
        finally:store.close()


def write_report(output,root,report,prepared):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    atomic_json(output/'report.json',report)
    atomic_json(output/'trajectory.json',prepared.trajectory)
    atomic_json(output/'trajectory-selection.json',prepared.selection)
    atomic_json(output/'model-input.json',prepared.model_packet)
    esc=html.escape
    body=(f"<p><a href='{esc((root/'overview.html').as_uri(),quote=True)}'>← 全流程总览</a></p>"
          f"<p>{esc(report['goal'])}</p><p>只读原始任务，检查真实提取入口并在临时目录验证入队。本报告不调用模型，不修改正式队列。</p>")
    body+='<table><thead><tr><th>输入方案</th><th>字符数</th><th>本地处理耗时</th><th>长度检查</th></tr></thead><tbody>'
    for label,chars,ms in [('原始证据包',report['raw_chars'],None),
        ('上一版：仅去重',report['dedup_only_chars'],report['dedup_only_median_ms']),
        ('本版：轨迹精简＋去重',report['final_chars'],report['selection_and_dedup_median_ms'])]:
        duration='—' if ms is None else f'{ms} ms'
        body+=f"<tr><td>{label}</td><td>{chars:,}</td><td>{duration}</td><td>{'通过' if chars<=report['input_limit_chars'] else '超过上限'}</td></tr>"
    body+='</tbody></table>'
    body+=(f"<p>相比仅去重再减少 {report['reduction_vs_dedup_percent']}%；相比原始证据减少 {report['reduction_vs_raw_percent']}%。"
           f"上限 {report['input_limit_chars']:,} 字符。耗时为 {report['repeats']} 次本地处理的中位数，不代表模型生成耗时或 token 数。</p>")
    selection=report['selection']
    body+=(f"<p>省略 {selection['omitted_documentation_blocks']} 个已核验的通用工具文档块，"
           f"将 {selection['condensed_control_events']} 条常规许可事件汇总为 {selection['automatic_allowed_requests']} 次自动批准。"
           '调用、参数、用户澄清和错误保留。v2 另外去掉非操作消息、空布局及已知能力装饰，'
           '同次结果与观察复用正文；原始观察完整保存在证据快照，具体省略见清单。</p>')
    gate=report.get('entry_check',{})
    body+='<p>'+('提取入口长度检查与隔离队列入队验证通过；尚未调用真实模型。' if gate.get('passed') else '入口检查未通过：'+esc(gate.get('error','未运行')))+'</p>'
    body+='<details><summary>轨迹、实际输入与省略来源</summary><p><a href="trajectory.json">精简轨迹 JSON</a> · <a href="model-input.json">实际模型输入 JSON</a> · <a href="trajectory-selection.json">省略清单与完整来源引用</a> · <a href="report.json">统计 JSON</a></p></details>'
    body+='<details><summary>保留内容与完整性检查</summary><pre>'+esc(json.dumps(report['checks'],ensure_ascii=False,indent=2))+'</pre></details>'
    atomic_text(output/'report.html',_page('Workflow 轨迹精简测试',body))
    return output/'report.html'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--session',required=True);parser.add_argument('--episode',required=True)
    parser.add_argument('--output',type=Path);parser.add_argument('--repeats',type=int,default=9)
    args=parser.parse_args(argv)
    if not 1<=args.repeats<=100:parser.error('--repeats 必须在 1–100 之间')
    # IDs are selectors, never filesystem paths supplied by an arbitrary page.
    import re
    if not all(re.fullmatch('[a-f0-9]{32}',s) for s in (args.session,args.episode)):parser.error('任务标识格式不正确')
    root=args.root.expanduser().resolve()
    packet=build_evidence(read_session(root/'sessions'/args.session),args.episode)
    report,prepared=benchmark_packet(packet,args.repeats)
    report['source_root']=str(root)
    report['entry_check']=verify_entry(root,args.session,args.episode,prepared)
    print(write_report(args.output or root/'workflow-trajectory',root,report,prepared))
    print(json.dumps({k:report[k] for k in ('raw_chars','dedup_only_chars','final_chars','reduction_vs_raw_percent',
        'reduction_vs_dedup_percent','dedup_only_median_ms','selection_and_dedup_median_ms','within_limit','entry_check')},ensure_ascii=False))


if __name__=='__main__':main()
