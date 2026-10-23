"""Opt-in live acceptance check, also runnable from the signed frozen app.

Creates one isolated Codex conversation. Never run as a startup self-check.
Catalog reads here are evidence only; the production transport has no DB access.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import threading
import time

from .native_flow import NativeFlow
from .native_codex import NativeCodex
from .native_codex import canonical_turns
from .attachments import local_attachment


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', required=True)
    parser.add_argument('--codex', required=True)
    parser.add_argument('--label', default='修复后原生入口验收')
    args=parser.parse_args(argv)
    root=Path(args.runtime_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root/'acceptance.json').exists(): raise ValueError('Use a fresh acceptance directory')
    from PySide6.QtGui import QImage, QColor
    img=QImage(96,96,QImage.Format_RGB32);img.fill(QColor('#ff0000'))
    image_path=root/'red-fixture.png'
    if not img.save(str(image_path)): raise RuntimeError('Cannot create image fixture')
    result={'status':'running','startedAt':time.time(),'checks':{}}
    flow=NativeFlow(with_ring=False,runtime_dir=root/'runtime',recording_dir=root/'records',codex_path=args.codex)
    errors=[]
    def run():
        try:flow.run()
        except Exception as exc:errors.append(str(exc))
    worker=threading.Thread(target=run,daemon=True);worker.start()
    db=sqlite3.connect('file:'+str(Path.home()/'.codex/sqlite/codex-dev.db')+'?mode=ro',uri=True)
    def save(): (root/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    def wait(predicate, timeout=180):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate(): return
            if errors: raise RuntimeError(errors[0])
            if not worker.is_alive(): raise RuntimeError('Input flow disconnected before acceptance completed')
            time.sleep(.1)
        raise TimeoutError('Acceptance condition timed out')
    follower=None
    try:
        wait(lambda:flow.state.get('connectionState')=='ready',30)
        sent=time.monotonic()
        flow.inputs.put(('command',{'type':'text','source':'native_acceptance','attachments':[local_attachment(image_path)],
            'text':args.label+'\n请先说“原生入口验收进行中”，然后调用 clock.sleep 等待 60 秒，最后回答“原始结果”及附件的颜色。不要操作应用、修改文件或发送其他任务。'}))
        def in_sidebar_while_active():
            if not flow.tid or not flow.turn or not flow.rpc.active_turn():return False
            row=db.execute('select display_title from local_thread_catalog where host_id=? and thread_id=? and missing_candidate=0',('local',flow.tid)).fetchone()
            if not row:return False
            result.update(threadId=flow.tid)
            result['checks']['sidebarWhileRunning']={'secondsAfterSubmit':round(time.monotonic()-sent,3),
                'title':row[0], 'turnId':flow.turn,'runtime':flow.rpc.state.get('threadRuntimeStatus')}
            save();print('SIDEBAR_WHILE_RUNNING',json.dumps(result['checks']['sidebarWhileRunning'],ensure_ascii=False),flush=True)
            return True
        wait(in_sidebar_while_active)
        # Leave a visible interval for direct user observation before steering.
        wait(lambda:time.monotonic()-sent>25)
        flow.inputs.put(('command',{'type':'steer','targetTurn':flow.turn,'source':'native_acceptance',
            'text':'修改当前任务：继续等满原定时间；结束时只回答“STEER_VERIFIED”和附件的颜色，不要回答原始结果。'}))
        wait(lambda:flow.state.get('taskOutcome')=='completed')
        answer=flow.state.get('agent','')
        if 'STEER_VERIFIED' not in answer or '红' not in answer:raise AssertionError('Steer or image delivery failed: '+answer)
        result['checks']['steerAndImage']={'answer':answer};save()
        print('STEER_AND_IMAGE_VERIFIED',flush=True)
        flow.inputs.put(('text','取消接口验收：请先说“等待取消”，然后调用 clock.sleep 等待 60 秒，最后回答结束。不要修改文件。'))
        wait(lambda:bool(flow.turn))
        cancel_turn=flow.turn
        wait(lambda:flow.state.get('agent','').strip()!='',60)
        flow.inputs.put(('command',{'type':'cancel','source':'native_acceptance'}))
        wait(lambda:flow.state.get('taskOutcome')=='interrupted',20)
        result['checks']['cancel']={'turnId':cancel_turn,'status':'interrupted'};save()
        print('CANCEL_VERIFIED',flush=True)
        flow.inputs.put(('text','断开入口验收：调用 clock.sleep 等待 10 秒，然后只回答“DETACH_VERIFIED”。不要修改文件。'))
        wait(lambda:bool(flow.turn))
        detach_turn=flow.turn
        # Subscribe read-only before closing the production Flow, then observe the
        # task's native completion. No resume/start call is made by the observer.
        follower=NativeCodex(runtime_dir=root, codex_path=args.codex)
        follower.tid=flow.tid;follower.ipc.connect();follower._follow()
        flow.stop.set();worker.join(10)
        if worker.is_alive():raise AssertionError('Input flow failed to disconnect')
        deadline=time.monotonic()+180
        detached_answer='';completed=False
        while time.monotonic()<deadline:
            follower.poll()
            while not follower.inbox.empty():
                event=follower.inbox.get_nowait();p=event.get('params',{})
                if p.get('turnId')==detach_turn and event.get('method')=='item/completed' and p.get('item',{}).get('type')=='agentMessage':detached_answer=p['item'].get('text','')
                if event.get('method')=='turn/completed' and p.get('turn',{}).get('id')==detach_turn:
                    completed=p['turn']['status']=='completed'
            if completed:break
            time.sleep(.1)
        if not completed or 'DETACH_VERIFIED' not in detached_answer:raise AssertionError('Native task did not finish after input disconnect')
        result['checks']['continuesAfterDisconnect']={'turnId':detach_turn,'answer':detached_answer}
        for turn in canonical_turns(follower.state):
            inputs=list(turn.get('params',{}).get('input',[]))
            for item in turn.get('items',[]):
                if item.get('type')=='userMessage':inputs.extend(item.get('content',[]))
            if any(i.get('type')=='text' and not isinstance(i.get('text_elements'),list) for i in inputs):
                raise AssertionError('Native history has text input that crashes the renderer')
        result['checks']['rendererInputShape']='passed'
        # Exercise navigation after all follow-up requests, not only the initial
        # automatic open. Human visual confirmation is still required separately.
        follower.opener(flow.tid)
        result['checks']['reopenedAfterCompletion']=True
        result['status']='passed';save();print('NATIVE_ACCEPTANCE_PASSED',flush=True)
        return 0
    except Exception as exc:
        result.update(status='failed',error=str(exc));save();print('NATIVE_ACCEPTANCE_FAILED',str(exc),flush=True)
        return 1
    finally:
        flow.stop.set();worker.join(10)
        if follower:follower.close()
        db.close()


if __name__=='__main__':raise SystemExit(main())
