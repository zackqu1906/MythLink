"""General-purpose Agent, driven by text, voice and deliberate ring utterances.

Cancellation rejects pending/future requests and interrupts the active turn.
All UI operations remain inside cua_repl. No shell or Accessibility automation.
"""
from __future__ import annotations
import json
import copy
import plistlib
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from proximic_ring.runtime_paths import resource_root, cache_root
REPO = resource_root()
ROOT = cache_root()/'ring-agent-demo'
from proximic_ring.agent_workflow.agent.rpc import RPC
from proximic_ring.agent_workflow.agent import requests_ui as req
from proximic_ring.agent_workflow.agent.interaction import Interaction
from proximic_ring.agent_workflow.agent.connection import ConnectionIssue, verify_login, verify_computer_use
from proximic_ring.agent_workflow.agent.computer_control import release_turn
from proximic_ring.agent_workflow.agent.attachments import turn_input
from proximic_ring.agent_workflow.recording.recording import ExperienceRecorder, redact

UI_TOOLS = {'get_app_state', 'get_app_screenshot', 'get_app_state_and_screenshot',
            'click', 'press_key', 'type_text', 'paste', 'set_value', 'scroll', 'select_text',
            'perform_secondary_action', 'drag'}

class Flow:
    native_execution = False
    def __init__(self, *, output_dir=None, codex_path=None, with_ring=True, duration=0, ui_process=None,
                 runtime_dir=None, on_state=None, recording_dir=None):
        self.root=Path(runtime_dir) if runtime_dir else ROOT
        self.on_state=on_state
        self.output_dir = Path(output_dir).expanduser().resolve() if output_dir else None
        self.codex_path = codex_path
        self.with_ring = with_ring
        self.duration = duration
        self.ui_process = ui_process
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root/'commands').mkdir(exist_ok=True)
        self.events = (self.root/'events.jsonl').open('a')
        self.inputs = queue.Queue()
        self.stop = threading.Event()
        self.listening = threading.Event(); self.listening.set()
        self.rpc = None
        self.codex_bridge = None
        self.recorder = None
        self.recording_dir = recording_dir
        self.episode_id = None
        self._episode_turns = {}
        self.tid = self.turn = self.start_id = None
        self._completed_turns = set()
        self._released_turns = set()
        self._computer_use_ready = None
        self._computer_use_turns = set()
        self.trial = None
        self._trial_previous_thread = None
        self.pending = {}
        self.history = []
        self.always_apps=set()
        grant_file=self.root/'app-grants.json'
        if grant_file.exists():
            self.always_apps={app for app in json.loads(grant_file.read_text()).get('apps',[])
                              if isinstance(app,str) and app.strip()}
        self.apps=set(self.always_apps)
        self.task_queue=[]
        self.cancel_utterance=threading.Event()
        self.interaction=Interaction(self.inputs.put,lambda:self.state)
        self.steer_requests=set()
        self.voice_active = False
        self.cancelled = False
        self.auto_approve = False
        self.auto_approved_count = 0
        self.epoch = 0
        self.cancel_at = None
        self.interrupt_sent_at = None
        self.interrupt_waiting_for = set()
        self.question = None
        self.last_scan = 0
        self.state = dict(status='准备中', detail='正在启动 Agent', ring='尚未连接',
                          transcript='', agent='', approval='', question='',
                          turnId='', active=True, revision=0, request=None, requestCount=0, history=[], grantedApps=[], taskQueue=[], voicePhase='idle', lastSubmission='', outputDirectory=str(self.output_dir) if self.output_dir else None,
                          autoApprove=False, autoApprovedCount=0, answerToken='', answerTranscript='',
                          connectionState='checking', connectionMessage='正在连接本地 Codex…',
                          computerControlState='idle', taskOutcome='')
        self.publish()

    def event(self, kind, **fields):
        if self.recorder:
            episode = fields.get('episodeId', self._episode_turns.get(fields.get('turnId'), self.episode_id))
            self.recorder.local(kind, fields, episode_id=episode, thread_id=self.tid)
        row = redact({'at': datetime.now(timezone.utc).isoformat(), 'kind': kind, **fields})
        self.events.write(json.dumps(row, ensure_ascii=False)+'\n'); self.events.flush()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def publish(self, **fields):
        self.state.update(fields)
        if self.recorder:self.state.update(self.recorder.health)
        self.state['revision'] += 1
        tmp = self.root/'state.tmp'
        tmp.write_text(json.dumps(self.state, ensure_ascii=False))
        tmp.replace(self.root/'state.json')
        if self.on_state:self.on_state(copy.deepcopy(self.state))

    def instructions(self):
        save_preference = (
            f'The user configured an optional default save location: {self.output_dir}. '
            'Use it only when the task does not specify another location. It is a preference, not a boundary.'
            if self.output_dir else
            'There is no configured default save directory. If the user does not specify a location, '
            'use an appropriate working or save location for the task, or ask when the choice matters.'
        )
        return f'''You are Mythlink, the user's general-purpose agent on this Mac.
Help with questions, explanations, research, writing, calculation, coding, files,
and application or browser tasks. Computer Use is one available capability, not the
purpose or required method of every task. Answer directly when no tools are needed.
Choose the available tools that best fit the task. Prefer purpose-built search,
connectors, APIs, file tools or code execution when suitable; use Computer Use when
the task calls for interacting with an application or browser UI. Do not open an app,
take a screenshot or manipulate the desktop just to answer a question or process data.
Use only capabilities actually available in this session. If one is unavailable,
explain the limitation for the affected part and continue with suitable available methods.

Work with the files and directories needed for the user's task, respecting the actual
sandbox, operating-system and service permissions. The client's default save location
is a preference, not an additional directory restriction.
{save_preference}
Follow the user's requested path and file format. If a chosen location is unavailable,
report the actual permission or application problem and ask rather than silently substituting
a different directory. Verify results with evidence appropriate to the task: calculations,
tests, file contents, source material or UI observations. When a file is created, report its
actual saved path; use native application save flows only when the task requires them.

Treat user text or speech as a question, task, answer or update, not dictation to insert
unless asked. Preserve unfinished work on updates and apply the user's latest instruction.
Use ask_ring_user when necessary information, a consequential choice or an actual permission
request needs the user's input. Await the returned answer. Proceed with reasonable defaults
when sufficient information and authorization are already available.
After interruption or a declined tool, check the relevant current state before continuing.
Do not retry an unchanged declined action or treat an app grant as unrelated authorization.

When using Computer Use, follow its documented entry points and use current UI observations
to locate targets. Observe again when the next action depends on changed or stale UI state.
Mark user-requested browser result tabs as deliverables with markDeliverable when supported.
The client releases this turn's Computer Use resources and resets cua_repl after completion;
later UI tasks must initialize it again and reacquire current app/tab handles.
Keep Chinese progress updates short and final output concise. The user's latest update wins.'''

    def check_computer_use(self):
        self.publish(connectionMessage='正在检查可选的电脑操作能力…')
        try:
            verify_computer_use(self.rpc, self.tid)
        except ConnectionIssue as exc:
            if exc.state != 'tools':raise
            self._computer_use_ready = False
            self.event('computer_use_unavailable', message=str(exc))
            self.publish(computerUseState='unavailable', computerUseMessage=str(exc))
            return 'Agent 已连接，可进行问答及其他可用工具任务。电脑操作能力暂不可用：'+str(exc)
        self._computer_use_ready = True
        self.publish(computerUseState='ready', computerUseMessage='电脑操作能力已就绪。')
        return 'Agent 已连接，可按任务使用问答、文件、检索及电脑操作等可用能力。'

    def setup(self):
        from importlib.metadata import version, PackageNotFoundError
        try: application_version=version('mythlink')
        except PackageNotFoundError: application_version='unknown'
        self.recorder = ExperienceRecorder(self.recording_dir, metadata={
            'application_version':application_version, 'codex_path':str(self.codex_path),
            'runtime_directory':str(self.root), 'initial_context':{'availability':'not_captured'}})
        self.rpc = RPC(runtime_dir=self.root, codex_path=self.codex_path, observer=self._record_rpc)
        self.rpc.initialize()
        self.publish(connectionMessage='正在检查登录状态…')
        verify_login(self.rpc)
        self.new_thread()
        from .codex_ipc import desktop_supported
        if desktop_supported(self.codex_path):
            from .codex_bridge import CodexBridge
            self.codex_bridge = CodexBridge(self, self._thread_result)
            try:
                deadline=time.monotonic()+12
                while True:
                    try:
                        self.codex_bridge.connect()
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if time.monotonic() >= deadline or self.stop.wait(.25):raise
            except (OSError, ValueError, RuntimeError) as exc:
                self.codex_bridge.close()
                self.codex_bridge = None
                raise ConnectionIssue('error', '无法连接 Codex 实时同步，请先打开 Codex 再重试。') from exc
        else:
            self.publish(codexSyncState='unsupported',
                         codexSyncMessage='当前 Codex 版本尚未验证实时同步；会话仍保存到 Codex 历史')
        connection_message = self.check_computer_use()
        self.event('agent_ready', threadId=self.tid, outputDirectory=self.state['outputDirectory'])
        self.publish(status='等待戒指连接' if self.with_ring else '等待文字指令',
                     connectionState='ready', connectionMessage=connection_message,
                     ring='尚未连接' if self.with_ring else '文字模式（未连接戒指）',
                     detail='双击说出问题或任务；tap 提交，录音时右滑修改当前任务' if self.with_ring else '在窗口输入问题或任务即可使用 Agent')

    def new_thread(self, *, ephemeral=False):
        # Formal conversations keep native Codex history alongside ExperienceRecorder.
        # Comparison trials explicitly opt out of native history below.
        result = self.rpc.call('thread/start', {'cwd': str(self.root), 'ephemeral': ephemeral,
            'developerInstructions': self.instructions(),
            'dynamicTools': [{'type': 'function', 'name': 'ask_ring_user',
                'description': 'Ask the ring wearer a concise question. Provide only the relevant options, or omit options for a free answer. Await the returned user answer.',
                'inputSchema': {'type':'object','properties':{'question':{'type':'string'},'options':{'type':'array','items':{'type':'string'}}},
                                'required':['question'],'additionalProperties':False}}]
        })
        self.tid = result['thread']['id']
        self._thread_result = result
        if not ephemeral: self.publish(codexThreadId=self.tid)
        return result

    def start_workflow_trial(self, data):
        from proximic_ring.agent_workflow import pipeline_policy
        if not pipeline_policy.legacy_workflow_enabled():
            self.publish(workflowTrialError=pipeline_policy.DISABLED_MESSAGE)
            return
        from proximic_ring.agent_workflow.execution.workflow_trials import Trial, TrialStore, validate_case, task_prompt, digest
        if self.turn or self.start_id or self.pending or self.task_queue or self.trial or self.cancelled:
            self.publish(workflowTrialError='请等待当前任务和队列结束后再运行测试。'); return
        if not data.get('initialStateConfirmed'):
            self.publish(workflowTrialError='请先恢复并确认相同起始状态。'); return
        try:
            case = validate_case(data['case'])
            prompt = task_prompt(case, data['mode'])
            store = TrialStore(data.get('recordsRoot') or (self.recorder.root if self.recorder else self.recording_dir))
            self.trial = Trial(store, case, data['mode'])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.publish(workflowTrialError=str(exc) if isinstance(exc, ValueError) else '无法准备测试记录。'); return
        self._trial_previous_thread = self.tid
        self.publish(status='启动任务', workflowTrialError='', workflowTrial=dict(self.trial.row))
        try:
            result = self.new_thread(ephemeral=True)  # Neither group inherits previous conversation steps.
            context = dict(trial_id=self.trial.row['id'], case_id=case['id'], mode=data['mode'])
            self.episode_id = self.recorder.new_episode(case['goal'], 'workflow_trial', context) if self.recorder else None
            self.trial.row.update(thread_id=self.tid, episode_id=self.episode_id,
                session_id=self.recorder.session_id if self.recorder else None,
                records_root=str(self.recorder.root) if self.recorder else None,
                environment={**{k:result.get(k) for k in ('model','modelProvider','reasoningEffort','serviceTier')},
                             'instructions_hash':digest(self.instructions()), 'auto_approve':self.auto_approve})
            self.trial.store.save(self.trial.row)
            self.event('workflow_trial_started', trial=self.trial.row, caseId=case['id'])
            self.publish(transcript=case['goal'], lastSubmission='手动 Workflow 测试')
            self.start_task(prompt, 'workflow_trial', self.episode_id)
        except Exception:
            self.finish_workflow_trial('start_failed', error='测试会话或任务启动失败')
            self.restore_trial_thread()
            self.episode_id = None
            self.publish(status='测试启动失败', workflowTrialError='测试会话或任务启动失败，请检查 Agent 连接。')
            raise

    def finish_workflow_trial(self, status, *, error=''):
        if self.trial:
            try:
                result = self.trial.finish(status, error=error)
            except OSError:
                result = dict(self.trial.row, recording_error='计时记录未能保存')
                self.publish(workflowTrialError='任务已结束，但计时记录保存失败；请检查记录目录。')
            self.event('workflow_trial_finished', trial=result)
            self.publish(workflowTrial=dict(result))
            self.trial = None

    def restore_trial_thread(self):
        if self._trial_previous_thread is not None:
            self.tid, self._trial_previous_thread = self._trial_previous_thread, None

    def finish_recommendation(self, state, error=''):
        # Ordinary native conversations must work without retired workflow modules.
        if not getattr(self, 'recommendation_id', None): return
        from proximic_ring.agent_workflow.execution.recommendation_agent import finish_recommendation
        finish_recommendation(self, state, error)

    def _record_rpc(self, direction, message):
        if direction == 'outgoing' and self.codex_bridge:
            self.codex_bridge.outgoing(message)
        if self.recorder:
            self.recorder.rpc(direction, message, episode_id=self.episode_id, thread_id=self.tid)

    def close_recording(self):
        if self.codex_bridge:self.codex_bridge.close()
        if self.recorder:self.recorder.close()

    def release_computer_control(self, turn_id):
        if not turn_id or turn_id in self._released_turns:return
        if self._computer_use_ready is False and turn_id not in self._computer_use_turns:
            self._released_turns.add(turn_id)
            self.publish(computerControlState='idle')
            self.event('computer_control_not_used', turnId=turn_id)
            return
        self.publish(status='正在释放电脑控制', computerControlState='releasing',
                     detail='正在结束本轮电脑操作会话…')
        self.event('computer_control_release_started', turnId=turn_id)
        try:
            release_turn(self.rpc, self.tid, turn_id)
        except ConnectionIssue:
            self.publish(computerControlState='error')
            self.event('computer_control_release_failed', turnId=turn_id)
            raise
        self._released_turns.add(turn_id)
        self.publish(computerControlState='released')
        self.event('computer_control_released', turnId=turn_id)

    def send_interrupt(self):
        if self.turn and self.interrupt_sent_at is None:
            self.rpc.send('turn/interrupt', {'threadId':self.tid,'turnId':self.turn})
            self.interrupt_sent_at=time.monotonic()
            self.event('interrupt_sent', turnId=self.turn)

    def remember(self, r, disposition):
        self.history.append(f"#{r['id']} {r['title']} — {disposition}")
        self.history=self.history[-8:]

    def reply(self, rid, result, disposition, *, quiet=False):
        r=self.pending.pop(rid)
        if result is None:
            self.rpc.write({'id':rid,'error':{'code':-32601,'message':'Unsupported client request'}})
        else:self.rpc.write({'id':rid,'result':result})
        if not quiet:self.remember(r,disposition)
        self.event('request_decision',requestId=rid,method=r['method'],disposition=disposition,
                   app=r['app'],tool=r['tool'],epoch=self.epoch)
        self.show_pending()

    def show_pending(self):
        visible=[r for r in self.pending.values() if r.get('needsDecision',True)]
        r=visible[0] if visible else None
        answer_token=r['token'] if r and r['kind']=='question' else ''
        answer_fields={'answerToken':answer_token}
        if answer_token!=self.state.get('answerToken'):answer_fields['answerTranscript']=''
        self.publish(request=req.card(r) if r else None,
                     requests=[req.card(item) for item in visible],
                     requestCount=len(visible),history=self.history,
                     grantedApps=[req.app_label(a)+('（始终允许）' if a in self.always_apps else '（此对话）') for a in sorted(self.apps)],taskQueue=self.task_queue,**answer_fields)

    def release(self):
        if self.cancelled or self.stop.is_set():return
        # Existing app consent covers routine operations, but never stale actions.
        for rid,r in list(self.pending.items()):
            if r['epoch']!=self.epoch and r['kind']=='permission':
                self.reply(rid,req.decline_result(r),'已失效：用户修改了要求')
            elif r['routine'] and r['app'] in self.apps:
                self.reply(rid,{'action':'accept','content':{}},'按已有应用授权执行',quiet=True)
            elif self.auto_approve:
                result=req.automatic_approval(r)
                if result is not None:
                    self.auto_approved_count+=1
                    self.event('automatic_approval',requestId=rid,method=r['method'],
                               app=r['app'],count=self.auto_approved_count)
                    self.reply(rid,result,'自动允许（开关已开启）')
                    self.publish(autoApprovedCount=self.auto_approved_count,
                                 detail='授权已自动处理；Agent 的追问仍会等待你的回答')
        self.show_pending()
        visible=self.current_request()
        if visible:self.publish(status='等待回答' if visible['kind']=='question' else '等待选择')
        elif self.turn:self.publish(status='执行中')

    def set_auto_approve(self,enabled):
        if not isinstance(enabled,bool):return
        if self.trial and enabled != self.auto_approve: self.trial.row['interventions'].append('approval_mode_changed')
        self.auto_approve=enabled
        self.event('auto_approval_mode',enabled=enabled)
        self.publish(autoApprove=enabled,
                     detail='自动允许已开启；Agent 追问仍需你回答' if enabled else '自动允许已关闭；恢复按请求确认')
        if enabled:self.release()

    def command(self,data):
        command_episode=self.episode_id
        recorded_command=data
        if data.get('type')=='answer':
            target=self.current_request(data.get('questionToken')) if data.get('questionToken') else None
            if target and target.get('secret'):
                recorded_command={**data,'text':'[OMITTED_SECRET_ANSWER]'}
        if data.get('type')=='text':command_episode=None
        elif data.get('type')=='steer':
            target=data.get('targetTurn',self.turn)
            command_episode=self._episode_turns.get(target,self.episode_id if target==self.turn else None)
        self.event('client_command',command=recorded_command,
                   episodeId=command_episode)
        if data.get('type') == 'check_connection':
            if self.turn or self.start_id or self.pending:
                self.publish(connectionState='ready', connectionMessage='任务正在进行，完成后可检查连接。')
                return
            self.publish(connectionState='checking', connectionMessage='正在检查本地连接…')
            verify_login(self.rpc)
            connection_message = self.check_computer_use()
            self.publish(connectionState='ready', connectionMessage=connection_message)
            return
        kind=data.get('type')
        if kind=='record_event':self.event(data.get('event','client_event'),**data.get('payload',{}))
        elif kind=='workflow_trial':self.start_workflow_trial(data)
        elif kind=='workflow_recommendation':
            from proximic_ring.agent_workflow import pipeline_policy
            if not pipeline_policy.legacy_workflow_enabled():
                self.publish(error=pipeline_policy.DISABLED_MESSAGE)
                return
            from proximic_ring.agent_workflow.execution.recommendation_agent import start_recommendation
            start_recommendation(self,data)
        elif kind=='set_auto_approve':self.set_auto_approve(data.get('enabled'))
        elif kind=='answer':self.answer_text(data.get('questionToken'),data.get('text',''),data.get('source','answer_console'))
        elif kind in ('text','steer'):
            self.text(data.get('text',''),data.get('source','test_console'),mode='steer' if kind=='steer' else 'normal',target_turn=data.get('targetTurn',self.turn),context=data.get('context'),attachments=data.get('attachments',()))
        elif kind=='choose':self.choose(data.get('token'),data.get('index'),data.get('source','test_console'))
        elif kind:self.control(kind,data.get('source','test_console'))

    def current_request(self, token=None):
        return next((r for r in self.pending.values() if r.get('needsDecision',True)
                     and (token is None or r['token']==token)),None)

    def answer_text(self,token,text,source):
        text=text.strip()
        if not text:return
        r=self.current_request(token) if token else None
        if not r or r['kind']!='question' or self.cancelled:
            self.event('stale_answer_ignored',source=source)
            self.publish(detail='刚才的问题已失效，本句没有作为新任务发送。');return
        if r.get('secret'):
            self.publish(detail=r['blocked']);return
        self.publish(answerTranscript=text,lastSubmission='回答 Agent')
        self.event('answer_input',requestId=r['id'],source=source,text=text)
        self.answer(r,text)

    def transcript_partial(self,frame):
        token=frame.get('question_token')
        if token:
            r=self.current_request(token)
            if r and r['kind']=='question' and not r.get('secret'):
                self.publish(answerTranscript=frame['text'])
        else:self.publish(transcript=frame['text'])

    def answer(self,r,text):
        if r.get('secret'):
            self.publish(detail=r['blocked']);return
        if self.trial: self.trial.row['interventions'].append('answer')
        if r['method']=='item/tool/requestUserInput':
            q=r['questions'][r['questionIndex']]
            r['answers'][q['id']]={'answers':[text]};r['questionIndex']+=1
            if r['questionIndex']<len(r['questions']):
                req.configure_question(r);self.show_pending();return
            result={'answers':r['answers']}
        else:result={'success':True,'contentItems':[{'type':'inputText','text':text}]}
        self.reply(r['id'],result,'已回答')
        self.publish(status='执行中',detail='回答已发送给 Agent')
        self.event('question_answered',requestId=r['id'])

    def choose(self,token,index,source):
        r=self.current_request(token)
        if not r or self.cancelled:
            self.event('stale_choice_ignored',source=source);return
        if not isinstance(index,int) or not 0<=index<len(r['options']):return
        o=r['options'][index]
        if not o['enabled']:
            self.publish(detail=o['reason']);return
        if o['action']=='answer':self.answer(r,o['result']);return
        if r['epoch']!=self.epoch:
            self.release();return
        result=o['result']
        scope=(result.get('_meta') or {}).get('persist') if isinstance(result,dict) else None
        if r.get('appPermission') and scope in ('session','always'):
            self.apps.add(r['app'])
            if scope=='always':
                self.always_apps.add(r['app'])
                (self.root/'app-grants.json').write_text(json.dumps({'apps':sorted(self.always_apps)}))
            self.event('app_granted',app=r['app'],scope=scope,source=source,persistenceOwner='ring_client')
            # The client owns the offered app grant so callbacks still reach the
            # cancellation/steer gate. Sending server-side persistence would remove
            # those action-boundary callbacks. Scope and choices match the offer.
            result={k:v for k,v in result.items() if k!='_meta'}
        self.reply(r['id'],result,o['label'])
        self.publish(status='执行中',detail='已提交选择；已授权应用内的普通操作会自动执行')

    def move_option(self,token,delta):
        r=self.current_request(token)
        if not r or not r['options']:return
        selectable=[i for i,o in enumerate(r['options']) if o['enabled']]
        if not selectable:return
        position=selectable.index(r['selected']) if r['selected'] in selectable else 0
        r['selected']=selectable[max(0,min(len(selectable)-1,position+delta))]
        self.show_pending()
        self.event('option_selected',requestId=r['id'],index=r['selected'])

    def start_task(self,text,source,episode_id=None,attachments=(),visual_context=None):
        self.cancelled=False;self.epoch+=1
        self.episode_id=episode_id or (self.recorder.new_episode(text,source) if self.recorder else None)
        self.event('episode/starting',episodeId=self.episode_id,
                   executionContext={'availability':'not_captured','reason':'awaiting_agent_observation'})
        self.start_id=self.rpc.send('turn/start',{'threadId':self.tid,'input':turn_input(text,attachments,visual_context)})
        self.event('start_sent',requestId=self.start_id,text=text,source=source)
        self.publish(status='启动任务',agent='',detail='正在理解任务',taskQueue=self.task_queue,taskOutcome='',
                     computerControlState='active')

    def dispatch_queue(self):
        if self.turn or self.start_id or self.cancelled or not self.task_queue:return
        task=self.task_queue.pop(0)
        self.start_task(task['text'],task['source'],task.get('episodeId'),task.get('attachments',()),task.get('visualContext'))

    def text(self,text,source='ring_voice',mode='normal',target_turn=None,question_token=None,context=None,attachments=()):
        text=text.strip()
        attachments=copy.deepcopy(attachments)
        visual_context=copy.deepcopy((context or {}).get('visual_context'))
        if not text and not attachments:return
        # A bound answer is never a new task or a spoken global control command.
        if mode=='normal' and question_token:
            self.answer_text(question_token,text,source);return
        normalized=''.join(c for c in text if c not in ' ，。！？,.!?\n\t')
        input_fields=dict(text=text,source=source,mode=mode,targetTurn=target_turn,context=context)
        steer_episode=self._episode_turns.get(target_turn,self.episode_id if target_turn==self.turn else None)
        self.publish(transcript=text,lastSubmission='右滑：steer' if mode=='steer' else 'tap：普通提交')
        # Validate the captured turn before even processing spoken control words.
        if mode=='steer' and (not target_turn or target_turn!=self.turn or self.cancelled):
            self.event('input',**input_fields,episodeId=steer_episode)
            self.event('steer_not_sent',reason='target_turn_not_active',episodeId=steer_episode)
            self.publish(detail='原任务已结束，steer 未发送。文字已保留，不会误改下一条任务。');return
        commands={'取消':'cancel','取消任务':'cancel','停止':'cancel','结束测试':'exit','退出测试':'exit',
                  '允许':'approve','同意':'approve','拒绝':'decline'}
        if not attachments and normalized in commands:
            self.event('input',**input_fields)
            self.control(commands[normalized],source);return
        if source=='ring_voice' and normalized.startswith('语音测试') and not self.turn:
            self.event('input',**input_fields,episodeId=None)
            self.event('audio_selftest_passed',text=text)
            self.publish(status='语音入口正常',detail='已收到戒指识别结果。现在可以说桌面应用操作指令。');return
        if mode=='steer':
            if self.trial: self.trial.row['interventions'].append('steer')
            self.event('input',**input_fields,episodeId=steer_episode)
            self.epoch+=1
            request=self.rpc.send('turn/steer',{'threadId':self.tid,'expectedTurnId':target_turn,
                'input':turn_input(text,attachments,visual_context)})
            self.steer_requests.add(request)
            self.event('steer_sent',turnId=target_turn,requestId=request,text=text,epoch=self.epoch)
            self.publish(detail='已提交修改，当前动作可能先完成；尚未执行的旧动作会失效')
            self.release()
            return
        episode_id=self.recorder.new_episode(text,source,context) if self.recorder else None
        self.event('input',**input_fields,episodeId=episode_id)
        if self.turn or self.start_id or self.cancelled:
            task={'text':text,'source':source,'episodeId':episode_id}
            if attachments:task['attachments']=attachments
            if visual_context:task['visualContext']=visual_context
            self.task_queue.append(task)
            self.event('task_queued',text=text,position=len(self.task_queue),episodeId=episode_id)
            self.publish(detail=f'已加入队列第 {len(self.task_queue)} 条；当前任务完成后执行',taskQueue=self.task_queue)
        else:self.start_task(text,source,episode_id,attachments,visual_context)

    def clear_task_queue(self, reason):
        for task in self.task_queue:
            self.event('episode/cancelled',episodeId=task.get('episodeId'),reason=reason)
        self.task_queue.clear()
        self.publish(taskQueue=[])

    def control(self, action, source):
        if action not in ('exit','approve','decline','cancel'):
            self.event('unsupported_control_ignored',action=action,source=source)
            return
        self.event('control',action=action,source=source,turnId=self.turn)
        if action=='exit': self.stop.set(); return
        r=self.current_request()
        if action in ('approve','decline') and r:
            for i,o in enumerate(r['options']):
                result=o.get('result')
                if isinstance(result,dict) and (result.get('action') or result.get('decision'))==('accept' if action=='approve' else 'decline'):
                    self.choose(r['token'],i,source);return
        if action in ('approve','decline'):return
        self.clear_task_queue('queue_cleared')
        self.cancel_utterance.set()
        if not self.turn and not self.start_id and not self.pending:
            self.publish(detail='当前没有执行中的任务'); return
        if self.cancelled: return  # Keep an outstanding cancellation acknowledgement intact.
        if self.trial: self.trial.row['interventions'].append('cancel')
        self.cancelled=True; self.cancel_at=time.monotonic()
        self.publish(status='正在取消',approval='',question='',detail='拒绝后续动作并等待任务停止')
        # Resolve an outstanding UI request before interrupting, avoiding the
        # ordering that hung in the earlier experiment.
        self.interrupt_waiting_for={rid for rid,r in self.pending.items() if r['method']=='mcpServer/elicitation/request'}
        for rid,r in list(self.pending.items()):self.reply(rid,req.cancel_result(r),'用户取消')
        if not self.interrupt_waiting_for: self.send_interrupt()
        self.show_pending()

    def handle(self,e):
        if self.codex_bridge:
            self.codex_bridge.incoming(e)
        m=e.get('method',''); p=e.get('params',{})
        if p.get('threadId') and p['threadId'] != self.tid: return
        item = p.get('item') or {}
        if ((m in ('item/started','item/completed') and item.get('type')=='mcpToolCall'
                and item.get('server')=='cua_repl') or
                (m=='mcpServer/elicitation/request' and p.get('serverName')=='cua_repl')):
            if turn_id := p.get('turnId') or self.turn:
                self._computer_use_turns.add(turn_id)
        if self.trial: self.trial.observe(e)
        if 'transportClosed' in e:
            self.publish(status='Agent 连接已关闭'); self.stop.set(); return
        if not m and 'id' in e:
            if e['id'] in self.steer_requests:
                self.steer_requests.discard(e['id'])
                if e.get('error'):self.publish(detail='steer 未被接受：'+str(e['error'].get('message','未知错误')))
                else:self.event('steer_acknowledged',requestId=e['id'])
            if e['id']==self.start_id:
                self.start_id=None
                if 'result' in e:self.turn=e['result']['turn']['id']
                elif e.get('error'):
                    self.event('task_start_failed',episodeId=self.episode_id,error=e['error'])
                    self.finish_workflow_trial('start_failed', error='Agent 拒绝启动任务')
                    self.finish_recommendation('not_started','Agent 拒绝启动任务')
                    self.restore_trial_thread()
                    self.episode_id=None
                    self.clear_task_queue('previous_task_start_failed')
                    self.cancelled=False; self.cancel_at=None
                    self.interrupt_waiting_for.clear(); self.interrupt_sent_at=None
                    self.publish(status='任务启动失败',detail=str(e['error'].get('message','未知错误'))+'；其余排队任务已取消，需要执行请重新提交。',
                                 computerControlState='idle',turnId='',taskOutcome='failed')
            self.event('rpc_response',requestId=e['id'],error=e.get('error'),result=e.get('result') if 'turn' not in (e.get('result') or {}) else {'turnId':e['result']['turn']['id']})
        if m=='turn/started':
            self.turn=p['turn']['id']; self.publish(status='执行中',turnId=self.turn,taskOutcome='')
            if self.codex_bridge and self.codex_bridge.titled and not self.state.get('codexOpenThreadId'):
                self.rpc.send('thread/name/set', {'threadId':self.tid,
                    'name':self.codex_bridge.model.state['title']})
                self.publish(codexOpenThreadId=self.tid)
            if self.trial: self.trial.row['turn_id'] = self.turn
            self._episode_turns[self.turn]=self.episode_id
            self.event('turn_started',turnId=self.turn)
            if self.cancelled:self.send_interrupt()
        if m=='serverRequest/resolved':
            rid=p.get('requestId'); self.event('request_resolved',requestId=rid)
            old=self.pending.pop(rid,None)
            if old:self.remember(old,'服务端已结束，此请求失效')
            self.show_pending()
            self.interrupt_waiting_for.discard(rid)
            if self.cancelled and self.cancel_at and not self.interrupt_waiting_for:
                self.send_interrupt(); self.cancel_at=None
        if 'id' in e and m:
            rid=e['id'];meta=p.get('_meta') or {}
            app=(meta.get('tool_params') or {}).get('app');tool=meta.get('tool_name')
            supported_ui=(p.get('serverName')=='cua_repl' and isinstance(app,str) and bool(app.strip()))
            routine_ui=supported_ui and meta.get('riskLevel')=='low' and tool in UI_TOOLS
            r=req.build(rid,m,p,epoch=self.epoch,supported_ui=supported_ui,routine_ui=routine_ui)
            r['needsDecision']=not (r['routine'] and app in self.apps)
            self.pending[rid]=r
            self.event('request_received',requestId=rid,method=m,app=app,tool=tool,
                       optionCount=len(r['options']),blocked=bool(r['blocked']),
                       riskLevel=meta.get('riskLevel'),appConsent=r['appConsent'],
                       existingAppGrant=app in self.apps)
            if self.cancelled or self.stop.is_set():self.reply(rid,req.cancel_result(r),'任务已取消，拒绝后续请求')
            else:
                self.release()
                if rid in self.pending:
                    if self.trial: self.trial.row['human_requests'] += 1
                    self.publish(status='等待回答' if r['kind']=='question' else '等待选择',
                                 detail=('上下滑选择，tap 确认；Agent 追问可以双击开始语音回答。' if self.with_ring else
                                         '输入编号或直接回答，上滑发送。' if r['kind']=='question' else '请用鼠标选择权限选项。'))
        if m=='item/started' and p.get('item',{}).get('type')=='mcpToolCall':
            self.event('tool_started',tool=p['item'].get('tool'))
            self.publish(detail='正在操作应用…' if item.get('server')=='cua_repl' else '正在使用工具…')
        if m=='item/agentMessage/delta':
            item_id=p.get('itemId')
            prior=self.state.get('agent','') if getattr(self,'_agent_item',None)==item_id else ''
            self._agent_item=item_id
            self.publish(agent=prior+p.get('delta',''))
        if m=='item/completed':
            item=p.get('item',{})
            if item.get('type')=='agentMessage':
                self.publish(agent=item.get('text',''))
                self.event('agent_message',text=item.get('text',''))
            elif item.get('type')=='mcpToolCall':
                self.event('tool_completed',tool=item.get('tool'),status=item.get('status'))
        if m=='turn/completed':
            turn_id=p['turn']['id']
            if (p.get('threadId',self.tid)!=self.tid or turn_id!=self.turn
                    or turn_id in self._completed_turns):
                self.event('stale_turn_completed_ignored',turnId=turn_id)
                return
            self._completed_turns.add(turn_id)
            if self.codex_bridge:self.codex_bridge.turn_completed(turn_id)
            status=p['turn']['status']
            self.event('turn_completed',turnId=turn_id,status=status)
            self.finish_workflow_trial(status)
            self.finish_recommendation(status)
            self.turn=self.start_id=None
            for r in self.pending.values():self.remember(r,'本轮已结束，此请求失效')
            self.pending.clear();self.question=None
            self.show_pending()
            was_cancelled=self.cancelled
            self.cancelled=False;self.cancel_at=None;self.interrupt_waiting_for.clear()
            self.interrupt_sent_at=None
            # Wait for the scoped cleanup acknowledgement before declaring the
            # task idle or starting another queued turn. UI runs on another thread.
            self.release_computer_control(turn_id)
            self.restore_trial_thread()
            self.episode_id=None
            self.publish(status={'completed':'本轮结束','interrupted':'已取消','failed':'任务失败'}.get(status,status),
                         detail=('本轮电脑控制已释放，可以继续下一条指令' if self.state['computerControlState']=='released'
                                 else '本轮任务已结束，可以继续下一条指令'),approval='',question='',turnId='',
                         taskOutcome='interrupted' if was_cancelled else status)
            if status=='completed' and not was_cancelled:self.dispatch_queue()
            elif self.task_queue:
                self.clear_task_queue('previous_task_not_completed')
                self.publish(detail='上一任务未正常完成，其余排队任务已取消；需要执行请重新提交。')

    def ring(self):
        from proximic_ring.voice.app_runtime import RecognitionRuntime, RuntimeSettings
        from proximic_ring.gestures.gesture_settings import GestureBindings
        prefs=plistlib.loads((Path.home()/'Library/Preferences/com.ProxiMic.ProxiMic Voice.plist').read_bytes())
        if prefs.get('asr.backend')!='volcengine':
            self.inputs.put(('error','本次独立测试需要当前已配置的 volcengine ASR')); return
        settings=RuntimeSettings(ring_name=prefs.get('ring.name','Ringo'),ring_selector=prefs.get('ring.selector'),
            encoding='adpcm',data_dir=self.root/'audio',audio_source='ring',speech_control_mode='gesture',
            asr_backend='volcengine',asr_model=prefs.get('asr.model','seedasr-streaming'),asr_device='cpu',
            asr_language=prefs.get('asr.language','zh'),asr_api_key=prefs.get('asr.volcengineApiKey',''),
            asr_gain_db=float(prefs.get('asr.gainDb',0)),asr_end_on_tap=True,
            desktop_output=False,push_to_talk=False,gesture_bindings=GestureBindings(end=('tap','swipe-right'),undo=('',''),switch_mode=('','')))
        def update(u):
            session=getattr(u,'session_id',0)
            if getattr(u,'error',None):
                if self.interaction.fail(session):self.inputs.put(('error','ASR: '+str(u.error)))
            elif getattr(u,'is_final',False):
                frame=self.interaction.finished(session,u.text or '')
                if frame and frame['text']:self.inputs.put(('utterance',frame))
            elif getattr(u,'text',None):
                frame=self.interaction.partial(session,u.text)
                if frame:self.inputs.put(('partial',frame))
        def gesture(g,busy):
            return self.interaction.gesture(getattr(g,'name',''),busy)
        def status(s):
            if '[ASR] CANCELLED' in s:self.interaction.fail()
            elif '[ASR AUDIO]' in s:self.inputs.put(('audio',s))
            elif s.startswith('[AUDIO_INPUT_ERROR]'):self.inputs.put(('error',s))
            elif s.startswith('[GESTURE_RECOGNIZED] '):
                try:self.inputs.put(('gesture_seen',json.loads(s.split('] ',1)[1])['name']))
                except Exception:pass
        try:
            RecognitionRuntime(settings).run(self.stop,self.listening,on_update=update,on_state=status,
                on_connected=lambda:self.inputs.put(('ring','已连接')),
                on_disconnected=lambda:self.inputs.put(('ring','已断开')),
                on_started=lambda:self.inputs.put(('ready','戒指就绪')),
                on_session_started=self.interaction.started,cancel_utterance_event=self.cancel_utterance,
                gesture_filter=gesture,
                ime_context_provider=lambda:{'status':'unavailable','reason':'isolated_agent_test'},
                asr_hotwords_provider=lambda:['取消','计算器','文本编辑','TextEdit','结束测试'])
        except Exception as exc:self.inputs.put(('error',f'戒指连接失败：{type(exc).__name__}: {exc}'))

    def poll_transport(self):
        if self.codex_bridge:self.codex_bridge.poll()

    def poll_pending_actions(self):
        pass

    def run(self):
        worker=None
        failure=None
        deadline=time.monotonic()+self.duration if self.duration else float('inf')
        try:
            self.setup()
            if self.with_ring:
                worker=threading.Thread(target=self.ring,daemon=True);worker.start()
            while not self.stop.is_set() and time.monotonic()<deadline:
                self.poll_transport()
                if self.stop.is_set():break
                if self.ui_process is not None and self.ui_process.poll() is not None:
                    self.stop.set();break
                if self.cancelled and self.cancel_at and not self.interrupt_sent_at and time.monotonic()-self.cancel_at>3:
                    self.event('resolve_timeout_interrupting',turnId=self.turn)
                    self.send_interrupt()
                if self.cancelled and self.interrupt_sent_at and time.monotonic()-self.interrupt_sent_at>10:
                    self.event('interrupt_timeout',turnId=self.turn)
                    self.publish(status='取消回执超时',detail='将关闭本次 Agent 进程，后续操作保持拒绝')
                    break
                while not self.inputs.empty():
                    kind,value=self.inputs.get()
                    if kind=='command':self.command(value)
                    elif kind=='text':self.text(value)
                    elif kind=='utterance':self.text(source='ring_voice',**value)
                    elif kind=='option_move':self.move_option(value[0],value[1])
                    elif kind=='voice_phase':
                        self.publish(voicePhase=value,ring={'idle':'戒指就绪','starting':'准备录音','recording':'正在收听：tap 普通提交 / 右滑 steer','finishing':'正在识别','cancelling':'取消录音中'}.get(value,value))
                    elif kind=='voice_submit_mode':
                        self.event('voice_endpoint',mode=value)
                        self.publish(lastSubmission='右滑：steer' if value=='steer' else 'tap：普通提交')
                    elif kind=='partial':self.transcript_partial(value)
                    elif kind in ('cancel','approve'):self.control(kind,value)
                    elif kind=='choice':self.choose(value[0],value[1],'ring_gesture')
                    elif kind=='ring':self.publish(ring=value)
                    elif kind=='ready':self.publish(ring='戒指就绪',status='等待语音指令',detail='双击说话；tap 提交为下一任务，右滑提交为 steer。上下滑选项，tap 确认。');self.event('ring_ready')
                    elif kind=='error':self.publish(status='需要处理',detail=value);self.event('runtime_error',message=value)
                    elif kind=='listening':self.publish(ring=value)
                    elif kind=='audio':
                        self.event('audio_health',summary=value)
                        if 'all_zero=True' in value:self.publish(ring='音频异常：戒指传入全零音频',detail='本句没有可用声音。先结束测试并重新连接戒指；此时无需重复给 Agent 下指令。')
                    elif kind=='gesture_seen':self.event('ring_gesture',name=value)
                for path in sorted((self.root/'commands').glob('*.json')):
                    data=json.loads(path.read_text());path.unlink()
                    self.command(data)
                try:self.handle(self.rpc.inbox.get(timeout=.05))
                except queue.Empty:pass
                self.poll_pending_actions()
                if self.recorder:
                    health=self.recorder.health
                    if any(self.state.get(k)!=health[k] for k in ('recordingState','recordingError','recordingDroppedEvents')):
                        self.publish(**health)
        except Exception as exc:
            failure=exc
            raise
        finally:
            self.stop.set()
            self.finish_workflow_trial('unfinished', error='连接关闭或执行中断，未收到本轮完成事件')
            self.finish_recommendation('unfinished','连接关闭，未收到本轮完成事件')
            if self.rpc:
                try:
                    if self.turn and not self.native_execution:
                        for rid,r in list(self.pending.items()):self.reply(rid,req.cancel_result(r),'测试结束')
                        self.send_interrupt()
                        self.release_computer_control(self.turn)
                except (BrokenPipeError, OSError, ConnectionIssue) as exc:
                    failure=failure or exc
                finally:self.rpc.close()
            if worker:worker.join(timeout=8)
            connection_state=(failure.state if isinstance(failure,ConnectionIssue) else 'error') if failure else 'disconnected'
            connection_message=(str(failure) if isinstance(failure,ConnectionIssue) else '连接失败，请打开本地 Codex 确认可用后重试。') if failure else '连接已断开，请重新连接。'
            if self.codex_bridge and self.codex_bridge.failed:
                connection_state='error'
                connection_message=self.state['codexSyncMessage']
            self.publish(status='连接失败' if failure else '测试已结束',ring='已断开' if self.with_ring else '文字模式已结束',active=False,approval='',question='',request=None,requestCount=0,detail=connection_message if failure else getattr(self, 'disconnect_message', ('输入连接已关闭；已提交的任务请在 Codex 中继续' if self.native_execution else '戒指监听与本次 Agent 已关闭')),
                         connectionState=connection_state, connectionMessage=connection_message, turnId='')
            self.event('closed');self.close_recording();self.events.close()
