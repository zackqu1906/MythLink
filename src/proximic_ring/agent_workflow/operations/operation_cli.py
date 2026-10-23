"""Local whole-workflow catalog, review queue, recommendation and feedback CLI."""
from __future__ import annotations
import argparse
import json
import shutil
import threading
from pathlib import Path
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_catalog import sync_catalog, ingest_dataset, regroup, review_one, recover_abandoned_reviews
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.operations.operation_embeddings import EmbeddingDeferred, agent_busy
from proximic_ring.agent_workflow import pipeline_policy


def import_dataset(store,path):
    path=Path(path).resolve()
    # Validate before copying. The dataset has only explicit exported task evidence.
    count=ingest_dataset(store,path)
    target=store.directory/'dataset'
    if target.resolve()!=path:shutil.copytree(path,target,dirs_exist_ok=True)
    # Relocatable provenance links for both offline reports and the loopback viewer.
    with store.db:
        for row in store.rows('samples'):
            source=json.loads(row['source'])
            if row['kind']=='test' and source.get('source_file','').startswith('extractions/'):
                source['source_file']='dataset/'+source['source_file']
                store.db.execute('UPDATE samples SET source=? WHERE id=?',(json.dumps(source,ensure_ascii=False),row['id']))
    regroup(store)
    return count


def main(argv=None):
    parser=argparse.ArgumentParser(description='完整操作归并与本地推荐；不会执行桌面任务')
    parser.add_argument('command',choices=['sync','import-dataset','review','display','parameters','recommend','expose','feedback','feedback-data','report','serve','benchmark','prepare-trial','activate','recheck','merge-reviewed'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--dataset',type=Path,default=Path('experiments/workflow_recommendations/dataset'))
    parser.add_argument('--context',type=Path,help='场景 JSON 文件；模拟场景只允许 --scope test')
    parser.add_argument('--scope',choices=['user','test'],default='user')
    parser.add_argument('--record',action='store_true',help='保存推荐请求快照；不自动计曝光，后续用 expose 显式确认')
    parser.add_argument('--retry',action='store_true',help='明确重试一批失败复核（默认失败不自动重试）')
    parser.add_argument('--impression',help='旧参数，仅保留以提示迁移')
    parser.add_argument('--request');parser.add_argument('--exposure')
    parser.add_argument('--limit',type=int,default=4,help='主动请求返回的前 K 项，1 到 20')
    parser.add_argument('--operation');parser.add_argument('--kind')
    parser.add_argument('--refresh-display',action='store_true',help='仅 display：指定 --operation，重写展示文案并保留历史，不改执行定义')
    parser.add_argument('--revision',help='通用 Workflow 版本 ID')
    parser.add_argument('--sample',help='recheck / parameters 指定历史样本 ID，不覆盖旧结论')
    parser.add_argument('--parameters',type=Path,help='prepare-trial 使用的参数 JSON 文件')
    parser.add_argument('--initial-state',help='prepare-trial 的明确初始界面状态')
    parser.add_argument('--run-file',type=Path,help='activate 所需的已核验成功的 Trials .run.json')
    parser.add_argument('--merge-plan',type=Path,help='merge-reviewed：经明确批准的分支审阅及参数迁移计划')
    args=parser.parse_args(argv)
    if not pipeline_policy.legacy_workflow_enabled() and args.command not in ('report','serve','feedback-data'):
        parser.error(pipeline_policy.DISABLED_MESSAGE)
    store=OperationStore(args.root)
    try:
        if args.command=='merge-reviewed':
            if not args.merge_plan:raise ValueError('需要 --merge-plan；此命令实际合并已审阅的组')
            from proximic_ring.agent_workflow.operations.operation_merge import merge_reviewed
            result=merge_reviewed(store,json.loads(args.merge_plan.read_text()))
            print('已完成经审阅的合并（未重放桌面执行）：',result['id'])
        elif args.command=='recheck':
            if not args.sample:raise ValueError('需要 --sample')
            from proximic_ring.agent_workflow.operations.operation_prototypes import recheck
            print('已排队：',recheck(store,args.sample))
        elif args.command=='prepare-trial':
            if not args.revision or not args.parameters or not args.initial_state:raise ValueError('需要 --revision、--parameters 和 --initial-state')
            from proximic_ring.agent_workflow.execution.operation_trial_bridge import prepare_trial
            print(prepare_trial(store,args.revision,json.loads(args.parameters.read_text()),args.initial_state));return
        elif args.command=='activate':
            if not args.revision or not args.run_file:raise ValueError('需要 --revision 和 --run-file')
            from proximic_ring.agent_workflow.operations.operation_prototypes import activate
            activate(store,args.revision,run_file=args.run_file)
            print('已启用经过执行核验的通用 Workflow 版本')
        elif args.command=='benchmark':
            import runpy
            script=Path(__file__).resolve().parents[4]/'experiments/workflow_recommendations/embedding_benchmark.py'
            runpy.run_path(str(script))['run'](args.root,args.dataset)
        elif args.command=='sync':
            print(json.dumps({'warnings':sync_catalog(args.root)},ensure_ascii=False))
        elif args.command=='import-dataset':print('导入任务样本：',import_dataset(store,args.dataset))
        elif args.command=='display':
            from proximic_ring.agent_workflow.operations.operation_display_jobs import process_display
            from proximic_ring.agent_workflow.extraction.workflow_model import saved_settings
            if args.retry:recover_abandoned_reviews(store)
            # An explicit retry attempts at most once, never loops on a failure.
            if args.refresh_display and not args.operation:raise ValueError('--refresh-display 需要 --operation')
            if args.operation:
                process_display(store,saved_settings(),retry=args.retry,operation_id=args.operation,refresh=args.refresh_display)
            else:
                if args.retry:process_display(store,saved_settings(),retry=True)
                while not agent_busy() and process_display(store,saved_settings()):pass
            print(json.dumps({'ready':len(store.rows('operation_displays')),'jobs':[
                {'status':j['status'],'error':j['error']} for j in store.rows('operation_jobs') if j['kind']=='display']},ensure_ascii=False))
        elif args.command=='parameters':
            from proximic_ring.agent_workflow.parameters.parameter_jobs import process_parameters
            from proximic_ring.agent_workflow.extraction.workflow_model import saved_settings
            if args.retry:
                recover_abandoned_reviews(store)
                process_parameters(store,saved_settings(),retry=True,sample_id=args.sample,scope=args.scope)
            else:
                while not agent_busy() and process_parameters(store,saved_settings(),sample_id=args.sample,scope=args.scope):pass
            print(json.dumps([dict(sample_id=r['sample_id'],status=r['status'])
                for r in store.rows('operation_parameter_history')],ensure_ascii=False))
        elif args.command=='review':
            if agent_busy():raise EmbeddingDeferred('Agent/文本编辑忙碌，稍后运行 review')
            if args.retry:recover_abandoned_reviews(store)
            regroup(store)
            from proximic_ring.agent_workflow.extraction.workflow_model import saved_settings
            if args.retry:review_one(store,saved_settings(),retry=True)
            else:
                while not agent_busy() and review_one(store,saved_settings()):pass
            print(json.dumps([{'kind':j['kind'],'status':j['status'],'error':j['error']} for j in store.rows('operation_jobs')],ensure_ascii=False))
        elif args.command=='recommend':
            context=json.loads(args.context.read_text()) if args.context else {}
            if args.scope=='user' and context:raise ValueError('手动场景只允许测试范围，避免污染真实反馈')
            if context:context['source']='manual_demo'
            if not 1<=args.limit<=20:raise ValueError('--limit 必须为 1 到 20')
            if args.record:
                from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
                result=request_recommendations(store,context,scope=args.scope,limit=args.limit,trigger='cli',surface='cli')
            else:result=recommend(store,context,scope=args.scope,limit=args.limit)
            print(json.dumps(result,ensure_ascii=False,indent=2));return
        elif args.command=='expose':
            if not args.request or not args.operation:raise ValueError('需要 --request 和 --operation；仅在已查看该候选后确认')
            print(store.expose(args.request,args.operation,method='cli_confirmed'));return
        elif args.command=='feedback':
            if not args.exposure:raise ValueError('请先 expose 确认已查看，再使用 --exposure；旧 impression 不是实际曝光')
            store.feedback(args.exposure,args.operation,args.kind);print('反馈已记录，不影响本版统计分数');return
        elif args.command=='feedback-data':
            from proximic_ring.agent_workflow.recommendation.recommendation_feedback import training_examples
            print(json.dumps(list(training_examples(store,scope=args.scope)),ensure_ascii=False,indent=2));return
        page=export_operations(store);print(page)
        if args.command=='serve':
            from proximic_ring.agent_workflow.recording.records_server import RecordsServer
            server=RecordsServer(args.root,on_automatic=lambda enabled:None,automatic=False)
            print(server.origin+server.prefix+'operations/index.html',flush=True)
            try:threading.Event().wait()
            except KeyboardInterrupt:pass
            finally:server.close()
    except EmbeddingDeferred as exc:
        print('等待空闲：'+str(exc))
    except (ValueError,OSError) as exc:parser.error(str(exc))
    finally:store.close()


if __name__=='__main__':main()
