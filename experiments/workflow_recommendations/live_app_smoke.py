"""Opt-in real Computer Use + background model smoke test, in an isolated root.

Runs the production AppController, AgentOverlay, Flow, extraction subprocess,
prototype admission, native recommendation panel and execution dispatch. It does
NOT connect a second BLE device. Two firmware index-pinch frames are injected
through the real FirmwareGestureWorker; physical BLE delivery remains untested.
It starts music twice and incurs configured text-model calls. Run explicitly.
"""
import argparse,json,os,sys,time,threading
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=720)
    args=parser.parse_args();output=args.output.resolve()
    if output.exists():parser.error('--output must be a new directory')
    output.mkdir(parents=True)
    os.environ['PROXIMIC_DATA_HOME']=str(output)
    os.environ['PROXIMIC_EMBEDDING_ACTIVITY']=str(output/'activity')
    os.environ.setdefault('QT_QUICK_CONTROLS_STYLE','Basic')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer,Qt
    from PySide6.QtTest import QTest
    from proximic_ring.ui.controller import AppController
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    from proximic_ring.agent_workflow.operations.operation_store import OperationStore
    from proximic_ring.agent_workflow.operations.operation_library import library
    from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
    from proximic_ring.gestures.firmware_gestures import FirmwareGestureWorker
    from ring_python_sdk.swipe import SwipeResult
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False)
    owner=AppController();agent=owner.agent;overlay=AgentOverlay(agent,owner,app)
    agent._workflow_auto=True;agent.recommendations.scope='test'
    root=output/'agent-experience';agent.workspace.state['recordsRoot']=str(root)
    result=dict(started_at=time.time(),recording_root=str(root),transport='injected firmware frames, physical BLE untested',events=[])
    stage='baseline';began=time.monotonic();worker=None;before=None;done=False
    def event(name,**data):
        value=dict(event=name,elapsed=round(time.monotonic()-began,3),**data)
        result['events'].append(value);print(json.dumps(value,ensure_ascii=False),flush=True)
        (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    def finish(error=None):
        nonlocal done
        if done:return
        done=True;timer.stop();result['error']=error;result['passed']=error is None;event('finished',error=error)
        if worker:worker.close()
        overlay.close();agent.close()
        # Other owner resources were never started; normal controller cleanup is
        # intentionally not used to stop unrelated system input-method services.
        QTimer.singleShot(200,app.quit)
    def poll():
        nonlocal stage,worker,before
        if done:return
        if time.monotonic()-began>args.timeout:finish('timed out at '+stage);return
        try:
            if agent.connectionNeedsHelp:finish(agent.connectionMessage);return
            store=OperationStore(root)
            try:
                groups=library(store);executions=store.rows('recommendation_executions')
                failures=[j for j in store.rows('operation_jobs') if j['status']=='failed']
                if failures:finish('background admission failed: '+failures[0]['error']);return
                if stage=='baseline' and groups and not agent.busy:
                    before=sum(len(g['samples']) for g in groups)
                    event('baseline_extracted_and_admitted',groups=len(groups),members=before)
                    stage='gesture'
                    # The real Agent editor owns this event; no BLE connection is opened.
                    agent._focused=True;agent.workspace.focus='task'
                    if owner._disconnect_event.is_set():owner._disconnect_event=threading.Event()
                    worker=FirmwareGestureWorker(on_gesture=lambda e:owner.ringGestures.filter(e,False,owner._disconnect_event))
                    worker.start()
                    worker.submit(SwipeResult(2,'trigger',1,8,1000,center_uptime_ms=1000,confirmed_confidence=.9))
                    worker.submit(SwipeResult(2,'trigger',2,8,1400,center_uptime_ms=1400,confirmed_confidence=.9))
                elif stage=='gesture' and agent.recommendations.snapshot:
                    runtime=agent.recommendations;dialog=runtime.dialog
                    item=next(i for i in runtime.snapshot['items'] if 'Music' in i['name'] or '音乐' in i['name'])
                    card=dialog.cards[item['position']-1]
                    if card['exposure']:
                        event('gesture_panel_exposed',context=runtime.snapshot['context'],operation_id=item['operation_id'],revision=item['workflow_revision'])
                        dialog.grab().save(str(output/'recommendations.png'))
                        if item['workflow']['parameters']:finish('music workflow unexpectedly requires parameters');return
                        stage='execute';card['choose'].click()
                elif stage=='execute' and executions:
                    row=executions[-1]
                    if row['state'] in ('failed','not_started','unfinished','interrupted'):finish('execution: '+row['state']+' '+row['error']);return
                    if row['state']=='completed':
                        event('recommended_task_completed',execution_id=row['id'],episode_id=row['episode_id'])
                        stage='updated'
                elif stage=='updated' and sum(len(g['samples']) for g in groups)>before:
                    event('recommended_task_extracted_and_admitted',groups=len(groups),members=sum(len(g['samples']) for g in groups))
                    stage='rerank';agent.recommendations.open('manual')
                elif stage=='rerank' and agent.recommendations.snapshot:
                    snapshot=agent.recommendations.snapshot
                    if snapshot['evidence']['tasks']>=2:
                        event('updated_ranking',tasks=snapshot['evidence']['tasks'],scores=[dict(name=i['name'],score=i['score']) for i in snapshot['items']])
                        finish()
            finally:store.close()
            queue=WorkflowStore(root)
            try:
                failed=[j for j in queue.jobs() if j['status']=='failed']
                if failed:finish('extraction failed: '+failed[0]['error'])
            finally:queue.close()
        except Exception as exc:finish(type(exc).__name__+': '+str(exc))
    timer=QTimer();timer.setInterval(300);timer.timeout.connect(poll);timer.start()
    event('baseline_submitted')
    agent._send(dict(type='text',source='workflow_test',text='打开 Apple Music，随机播放一首音乐，并确认当前正在播放的曲名。'))
    agent._visible=True;agent.focusRequested.emit('task')
    app.exec()
    return 0 if result.get('passed') else 1


if __name__=='__main__':raise SystemExit(main())
