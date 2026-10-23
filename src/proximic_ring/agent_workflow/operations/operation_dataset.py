"""Copy historical trials into an inspectable TEST dataset without replaying them."""
from __future__ import annotations
import json
import shutil
from pathlib import Path
from proximic_ring.agent_workflow.operations.operation_catalog import sample_from_audit
from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.extraction.workflow_artifacts import workflow_text
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json,atomic_text,now
from proximic_ring.agent_workflow.extraction.workflow_prompt import PROMPT_VERSION, COMPATIBLE_VERSIONS


def build_dataset(destination, roots, *, reextract=False, model=None, settings=None):
    from proximic_ring.agent_workflow.extraction.workflow_model import extract
    from proximic_ring.agent_workflow.extraction.workflow_evidence import validate_workflows
    from proximic_ring.agent_workflow.extraction.workflows import source_packet
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    originals=destination/'originals';originals.mkdir(exist_ok=True)
    audit_dir=destination/'extractions';audit_dir.mkdir(exist_ok=True)
    samples_dir=destination/'samples';samples_dir.mkdir(exist_ok=True)
    roots=[Path(r) for r in roots];trials={};audits={};errors=[]
    for root in roots:
        for path in (root/'workflows').glob('*.audit.json'):
            try:
                value=json.loads(path.read_text());src=value['source'];key=(src['session_id'],src['episode_id'])
                if src['episode'].get('source')!='workflow_seed':continue
                stamp=value['generation']['created_at']
                if key not in audits or stamp>audits[key][0]:audits[key]=(stamp,path,value)
            except (OSError,ValueError,KeyError):continue
        for path in (root/'workflow-tests').glob('*.run.json'):
            row=json.loads(path.read_text());case_path=path.with_name(row['case_id']+'.case.json')
            if not case_path.exists():continue
            if row['id'] in trials:continue
            case=json.loads(case_path.read_text());trials[row['id']]=(row,case,root)
            for src in (path,case_path,path.with_name(row.get('prompt_file','missing.prompt.txt'))):
                if src.exists():
                    target=originals/src.name
                    if target.exists() and target.read_bytes()!=src.read_bytes():raise ValueError('历史文件同名冲突：'+src.name)
                    if not target.exists():shutil.copy2(src,target)
    # Prefer current existing extractions. Select one independent baseline per
    # source task for re-extraction; remaining runs are retained as raw test records.
    selected={}
    for row,case,root in sorted(trials.values(),key=lambda r:r[0]['started_at']):
        src=case['source'];key=(src.get('session_id'),src.get('episode_id'))
        if key not in audits or row['mode']!='baseline' or row['status']!='completed' or row.get('interventions'):continue
        selected.setdefault(key,(row,case,root))
    results=[]
    def keep(value,label):
        name=label+'.audit.json';atomic_json(audit_dir/name,value)
        for i in range(len(value['workflows'])):
            sample=sample_from_audit(value,i,source_file='extractions/'+name,force_kind='test')
            target=samples_dir/(sample['id']+'.json');atomic_json(target,sample)
            atomic_text(samples_dir/(sample['id']+'.txt'),workflow_text(sample['flow']))
            results.append(dict(id=sample['id'],file='samples/'+target.name,sha256=digest(sample)))
    for key,(_,path,value) in audits.items():
        if value.get('generation',{}).get('prompt_version') in COMPATIBLE_VERSIONS:keep(value,path.stem.replace('.audit',''))
        else:errors.append('过时种子未作为候选：'+key[1])
    if reextract:
        for key,(row,case,root) in selected.items():
            cached=audit_dir/(row['id']+'.audit.json')
            if cached.exists():
                value=json.loads(cached.read_text())
                if value.get('generation',{}).get('prompt_version') in COMPATIBLE_VERSIONS:
                    keep(value,row['id']);continue
            try:
                packet=source_packet(root,row['session_id'],row['episode_id'])
                atomic_json(audit_dir/(row['id']+'.input.json'),packet)
                value,generation=(model or extract)(packet,settings)
                from proximic_ring.agent_workflow.extraction.workflow_normalization import normalize
                normalized,repairs=normalize(value)
                if repairs:
                    atomic_json(audit_dir/(row['id']+'.model-output.json'),value)
                    value=normalized;generation['local_normalization']=repairs
                validate_workflows(value,packet)
                generation.update(prompt_version=PROMPT_VERSION,created_at=now(),source_fingerprint=packet['fingerprint'])
                result=dict(schema_version=2,status='draft',source=packet['source'],generation=generation,
                    goal_verification='unknown',evidence_index={e['id']:{k:v for k,v in e.items() if k!='content'} for e in packet['evidence']},**value)
                keep(result,row['id'])
                print('extracted baseline',row['id'],flush=True)
            except Exception as exc:
                errors.append(row['id']+'：'+(str(exc) if type(exc) is ValueError else type(exc).__name__))
    manifest=dict(schema_version=1,kind='test',created_at=now(),samples=results,errors=errors,
        trials=[dict(run_id=r['id'],case_id=r['case_id'],mode=r['mode'],goal=c['goal'],status=r['status'],
                     initial_state=c['initial_state'],elapsed_seconds=r.get('elapsed_seconds'),tool_calls=r.get('tool_calls'),
                     original='originals/'+r['id']+'.run.json',
                     extraction='extractions/'+r['id']+'.audit.json' if (audit_dir/(r['id']+'.audit.json')).exists() else None)
                for r,c,_ in trials.values()],
        note='历史测试与种子；不计入真实使用频率。每个源任务选一条 baseline 独立提取，其余运行保留原记录；不是独立的用户习惯样本。')
    atomic_json(destination/'manifest.json',manifest)
    return manifest
