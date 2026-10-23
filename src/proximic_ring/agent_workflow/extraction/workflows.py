"""Isolated extraction worker and CLI; no Agent RPC, desktop tools or screenshots."""
from __future__ import annotations
from proximic_ring.agent_workflow.task_metadata import COMPACT_FORMATS
from proximic_ring.agent_workflow import pipeline_policy

import argparse
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from proximic_ring.agent_workflow.recording.records import read_session, sessions, export_html
from proximic_ring.agent_workflow.extraction.workflow_evidence import prepare_evidence, validate_workflows
from proximic_ring.agent_workflow.extraction.workflow_model import extract, saved_settings, ModelOutputError
from proximic_ring.agent_workflow.extraction.workflow_prompt import PROMPT_VERSION
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore, TERMINAL, now, atomic_json
from proximic_ring.agent_workflow.extraction.workflow_view import export_workflows
from proximic_ring.agent_workflow.extraction.workflow_artifacts import save_flow
from proximic_ring.agent_workflow.extraction.workflow_retry import RetryableModelError
from proximic_ring.text_processing.model import LLMSettings
from proximic_ring.agent_workflow.recording.task_list import task_list


def source_packet(root, session_id, episode_id):
    if not re.fullmatch(r'[a-f0-9]{32}',session_id) or not re.fullmatch(r'[a-f0-9]{32}',episode_id):
        raise ValueError('任务标识格式不正确')
    snapshot=read_session(Path(root)/'sessions'/session_id)
    episode=next((e for e in snapshot['episodes'] if e['id']==episode_id),None)
    if not episode or episode['status'] not in TERMINAL: raise ValueError('请选择已结束的任务')
    return prepare_evidence(snapshot,episode_id)


def scan(store, since, cache):
    tasks=task_list(store.root)
    if not pipeline_policy.legacy_workflow_enabled(): return tasks, ''
    errors=[]
    for task in tasks:
        # Repeated A/B trials are not independent user habits, and should not
        # start paid extraction requests between the two measured runs.
        if task.get('source') == 'workflow_trial': continue
        # A brief settling window avoids projecting a partially committed ending.
        if task['status'] not in TERMINAL or task['created_at'] < since: continue
        from datetime import datetime, timezone
        if (datetime.now(timezone.utc)-datetime.fromisoformat(task['updated_at'])).total_seconds()<3: continue
        signature=(task['session_id'],task['id'],task['updated_at'])
        if signature in cache: continue
        try:
            store.enqueue(source_packet(store.root,task['session_id'],task['id']))
            cache.add(signature)
        except (ValueError,OSError,sqlite3.Error) as exc:
            errors.append(f"{task['id']}: {str(exc) if isinstance(exc,ValueError) else type(exc).__name__}")
    return tasks, '; '.join(errors)


def process_one(store, settings, *, model=extract):
    if not pipeline_policy.legacy_workflow_enabled(): return False
    job=store.next_job()
    if not job: return False
    store.status(); export_workflows(store)
    value=generation=None;packet=None;phase='preflight'
    try:
        packet=json.loads((store.directory/(job['id']+'.input.json')).read_text(encoding='utf-8'))
        if job['prompt_version'] != PROMPT_VERSION:
            raise ValueError('旧版排队任务需要重新提取；本次没有调用模型')
        # Also materialize the actual input for jobs queued before input dedup
        # was installed; preserve their original evidence snapshot/fingerprint.
        store.save_model_input(job['id'],packet)
        phase='model'
        if model is extract and job.get('parent_job_id'):
            previous=next(j for j in store.jobs() if j['id']==job['parent_job_id'])
            value,generation=model(packet,settings,retry_error=previous['error'])
        else:value,generation=model(packet,settings)
        phase='validation'
        from proximic_ring.agent_workflow.extraction.workflow_normalization import normalize
        normalized,repairs=normalize(value)
        if repairs:
            atomic_json(store.directory/(job['id']+'.model-output.json'),value)
            value=normalized
            generation['local_normalization']=repairs
        generation.update(prompt_version=PROMPT_VERSION,created_at=now(),source_fingerprint=packet['fingerprint'])
        validate_workflows(value,packet)
        if 'notes' in value and 'format' not in value:
            from proximic_ring.agent_workflow.workflow_content import FORMAT
            value = dict(value, format=FORMAT)
        phase='save'
        try: changed=source_packet(store.root,job['session_id'],job['episode_id'])['fingerprint']!=packet['fingerprint']
        except (OSError,ValueError,sqlite3.Error): changed=True
        result=dict(schema_version=4 if value.get('format')=='agent-workflow-v7' else 3 if value.get('format') in COMPACT_FORMATS else 2,status='draft',goal_verification='unknown',support_episode_count=1,
            validation={'structure':'passed','references':'passed','semantics':'not_verified'},
            source=packet['source'],source_changed_after_extraction=changed,generation=generation,
            evidence_index={e['id']:{k:v for k,v in e.items() if k!='content'} for e in packet['evidence']},**value)
        # Reusable content and audit/provenance are separate artifacts. The rich
        # internal shape stays compatible with previous extraction readers.
        result['workflow_file'],_=save_flow(store.directory,job['id'],result)
        filename=job['id']+'.audit.json'
        atomic_json(store.directory/filename,result)
        store.finish(job['id'],result_file=filename)
    except Exception as exc:
        # Only our ValueErrors have user-safe content. No arbitrary HTTP bodies,
        # stack traces, hidden reasoning or API config are written to records.
        message=str(exc) if type(exc) in (ValueError,ModelOutputError,RetryableModelError) else '提取失败：'+type(exc).__name__
        retryable=(phase=='validation' and isinstance(exc,ValueError) or
                   phase=='model' and isinstance(exc,(ModelOutputError,RetryableModelError)))
        if isinstance(exc,ModelOutputError):
            generation={**exc.generation, 'prompt_version':PROMPT_VERSION,
                        'created_at':now(), 'source_fingerprint':packet['fingerprint']}
            atomic_json(store.directory/(job['id']+'.rejected.json'),dict(status='rejected',
                validation_error=message[:500],generation=generation,model_final_text=exc.final_text))
        elif value is not None:
            # Final JSON only, never the provider's reasoning envelope. Preserve
            # a rejected response for audit, clearly outside the draft library.
            atomic_json(store.directory/(job['id']+'.rejected.json'),dict(status='rejected',
                validation_error=message[:500],generation=generation,model_final_json=value))
        store.finish(job['id'],error=message[:500],retry_packet=packet if retryable else None)
    store.status(); export_workflows(store)
    return True


@contextmanager
def worker_lock(store):
    """OS lock releases on crash; protects scanning/claiming and recovery."""
    with (store.directory/'worker.lock').open('a+b') as handle:
        try:
            if os.name=='nt':
                import msvcrt
                handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError('已有后台提取进程；任务已保留在队列中') from None
        yield


def serve(root, since, input_stream=sys.stdin):
    pipeline_policy.require_legacy_workflow()
    from proximic_ring.agent_workflow.extraction.workflow_model import configuration_error
    from proximic_ring.agent_workflow.operations.embedding_install import installed as embedding_ready
    from proximic_ring.agent_workflow.operations.operation_embeddings import publish_activity, agent_busy, activity_directory
    store=WorkflowStore(root)
    commands=queue.Queue(); stop=threading.Event()
    computing_blocked=threading.Event(); computing_blocked.set()
    def reader():
        try:
            for line in input_stream:
                try:
                    command=json.loads(line)
                    if command.get('type')=='configure':
                        if command.get('busy',False):computing_blocked.set()
                        else:computing_blocked.clear()
                        publish_activity(command.get('busy',False))
                    commands.put(command)
                except ValueError: continue
        finally: stop.set()
    threading.Thread(target=reader,daemon=True,name='WorkflowControl').start()
    config=None; automatic=True; busy=True; cache=set(); last_scan=0; last_catalog=0; tasks=[]; problem=''
    try:
        with worker_lock(store):
            store.recover(); since=store.activation(since); export_workflows(store)
            from proximic_ring.agent_workflow.recording import workbench_actions
            workbench_actions.recover(store)
            if hasattr(os,'nice'):
                try: os.nice(10)
                except OSError: pass  # Some app/sandbox policies disallow priority changes.
            while not stop.is_set():
                while True:
                    try: command=commands.get_nowait()
                    except queue.Empty: break
                    if command.get('type')=='configure':
                        config=LLMSettings(**command['settings']); automatic=command.get('automatic',True)
                        busy=command.get('busy',False)
                    elif command.get('type')=='extract':
                        try:
                            selected=(next((t for t in task_list(root) if t['status'] in TERMINAL),None)
                                      if command.get('latest') else command)
                            if not selected: raise ValueError('还没有已结束的任务')
                            store.enqueue(source_packet(root,selected['session_id'],selected.get('episode_id',selected.get('id'))),manual=True)
                            problem=''; export_workflows(store)
                        except (ValueError,OSError,sqlite3.Error) as exc: problem=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
                if time.monotonic()-last_scan>4:
                    if automatic:
                        tasks,scan_error=scan(store,since,cache)
                        if scan_error: problem=scan_error
                    else: tasks=task_list(root)
                    last_scan=time.monotonic()
                    store.status(error=problem or configuration_error(config),tasks=tasks[:50]); export_workflows(store)
                if config and not configuration_error(config) and not busy and not stop.is_set() and not computing_blocked.is_set() and not agent_busy():
                    if process_one(store,config):
                        last_catalog=0
                        # Raw evidence pages are generated in this process only.
                        try: export_html(root)
                        except (OSError,ValueError,sqlite3.Error): problem='草稿已保存，但原始记录页面刷新失败'
                        continue
                    if workbench_actions.process_one(store,config,
                            cancelled=lambda: computing_blocked.is_set() or stop.is_set()):
                        from proximic_ring.agent_workflow.operations.operation_store import OperationStore
                        from proximic_ring.agent_workflow.operations.operation_view import export_operations
                        operations=OperationStore(root)
                        try: export_operations(operations)
                        except (OSError,ValueError,sqlite3.Error): problem='处理结果已保存，导出页面暂不可刷新'
                        finally: operations.close()
                        continue
                    # A deferred explicit action must retain priority over automatic reviews.
                    if store.db.execute("SELECT 1 FROM workbench_actions WHERE status='queued'").fetchone():
                        stop.wait(1);continue
                    if time.monotonic()-last_catalog>10:
                        from proximic_ring.agent_workflow.operations.operation_service import update_catalog
                        update_catalog(root,cancelled=lambda: computing_blocked.is_set() or stop.is_set())
                        last_catalog=time.monotonic()
                    if computing_blocked.is_set() or stop.is_set():continue
                    if not embedding_ready():
                        stop.wait(1);continue  # Keep admissions pending, without repeated failed jobs.
                    from proximic_ring.agent_workflow.operations.operation_service import review_catalog
                    if review_catalog(root,config,cancelled=lambda: computing_blocked.is_set() or stop.is_set()):
                        last_catalog=0
                        continue
                stop.wait(.25)
    finally:
        store.close()
        (activity_directory()/(str(os.getpid())+'.json')).unlink(missing_ok=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description='后台提取 Workflow 草稿，不操作桌面。')
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--session'); parser.add_argument('--episode'); parser.add_argument('--latest',action='store_true')
    parser.add_argument('--run',action='store_true',help='使用当前文本模型处理队列，开启思考')
    parser.add_argument('--list',action='store_true',help='列出已记录任务及 session / episode 标识，不调用模型')
    parser.add_argument('--serve',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--since',default=None,help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.list:
        for task in task_list(args.root):
            print(f"{task['created_at']}  {task['status']}  {task['goal']}\n  --session {task['session_id']} --episode {task['id']}")
        return
    if not pipeline_policy.legacy_workflow_enabled() and (
            args.serve or args.run or args.latest or args.session or args.episode):
        parser.error(pipeline_policy.DISABLED_MESSAGE)
    if args.serve:
        serve(args.root,args.since or now()); return
    store=WorkflowStore(args.root)
    try:
        if args.latest:
            selected=next((t for t in task_list(args.root) if t['status'] in TERMINAL),None)
            if not selected: parser.error('没有已结束的任务')
            args.session,args.episode=selected['session_id'],selected['id']
        if args.session or args.episode:
            if not args.session or not args.episode: parser.error('--session 与 --episode 必须同时提供')
            job=store.enqueue(source_packet(args.root,args.session,args.episode),manual=True)
            print('queued:',job)
        ran_queue=False
        if args.run:
            from proximic_ring.agent_workflow.operations.operation_embeddings import agent_busy
            try:
                with worker_lock(store):
                    store.recover()
                    settings=saved_settings()
                    while True:
                        if store.retry_delay() is None:break
                        if agent_busy():
                            time.sleep(.25);continue
                        if process_one(store,settings):continue
                        delay=store.retry_delay()
                        if delay is None:break
                        time.sleep(min(delay,.25))
                    ran_queue=True
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
        export_html(args.root)
        print(export_workflows(store))
        from proximic_ring.agent_workflow.operations.operation_service import update_catalog
        update_catalog(args.root)
        if ran_queue:
            from proximic_ring.agent_workflow.operations.operation_service import review_catalog
            while True:
                if agent_busy():
                    time.sleep(.25);continue
                # If imports were deferred while Agent was busy, catch them up.
                update_catalog(args.root)
                if not review_catalog(args.root,settings):break
        print(json.dumps([{'id':j['id'],'status':j['status'],'error':j['error']} for j in store.jobs()],ensure_ascii=False))
    finally: store.close()


if __name__=='__main__': main()
