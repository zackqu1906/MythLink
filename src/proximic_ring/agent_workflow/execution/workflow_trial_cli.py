"""Reproduce frozen workflow trials through the existing desktop Agent."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json
from proximic_ring.agent_workflow.execution.workflow_trials import TrialStore, export_trials, task_prompt, validate_case, available_workflows
from proximic_ring.agent_workflow.execution.workflow_task_files import load_task, prepare_task

REPO = Path(__file__).resolve().parents[4]
CASES = REPO / 'experiments/workflow_trials/cases'
TASKS = REPO / 'experiments/workflow_trials/tasks'
MODES = {'baseline': 'baseline', 'steps': 'natural_language'}
APPS = {'notes': 'com.apple.Notes', 'music': 'com.apple.Music'}
MODULE = 'proximic_ring.agent_workflow.execution.workflow_trial_cli'


def parser():
    p = argparse.ArgumentParser(description='本地 Workflow 耗时对比；通过现有 Agent 真实操作电脑。')
    p.add_argument('command', choices=['list', 'workflows', 'prepare', 'preview', 'run', 'report'])
    p.add_argument('--workflow', help='prepare 时选择 workflows 列出的流程标识（job:index）')
    p.add_argument('--task', help='任务目录名称，例如 notes 或 music')
    p.add_argument('--tasks-dir', type=Path, default=TASKS, help='可编辑任务目录')
    p.add_argument('--mode', choices=list(MODES))
    p.add_argument('--case', type=Path, help='使用已有的不可变 .case.json 快照')
    p.add_argument('--root', type=Path, default=REPO/'agent-experience', help='记录根目录')
    p.add_argument('--codex', help='可选 Codex 可执行文件路径')
    p.add_argument('--initial-state-confirmed', action='store_true', help='已人工恢复快照指定的起点')
    p.add_argument('--skip-verification', action='store_true', help='完成后暂不核验；目标状态保持 unknown')
    p.add_argument('--timeout', type=float, default=1200, help='单次执行等待秒数，默认1200')
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--runtime', type=Path, help=argparse.SUPPRESS)
    return p


def load_case(task=None, path=None, tasks_dir=None):
    if path is None:
        if not task or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', task):
            raise ValueError('请指定 --task notes、music 或任务目录名（小写字母、数字、下划线、连字符）。')
        return load_task((tasks_dir or TASKS)/task)
    return validate_case(json.loads(Path(path).expanduser().read_text(encoding='utf-8')))


def suite_cases(args):
    if args.case: return [load_case(path=args.case)]
    names = [args.task] if args.task else sorted(p.parent.name for p in args.tasks_dir.glob('*/task.json'))
    if not names: raise ValueError('没有找到测试任务：'+str(args.tasks_dir))
    return [load_case(name, tasks_dir=args.tasks_dir) for name in names]


def read_state(runtime):
    try: return json.loads((runtime/'state.json').read_text(encoding='utf-8'))
    except FileNotFoundError: return {}


def send(runtime, command):
    atomic_json(runtime/'commands'/f'{uuid.uuid4().hex}.json', command)


def ask(prompt):
    if not sys.stdin.isatty():
        raise ValueError('需要终端交互。请在终端运行；无人值守时须明确确认起点并跳过人工核验。')
    return input(prompt).strip()


def decision(request, answer):
    """Bind every answer to the request token, never to a later request."""
    if answer.lower() == 'q': raise KeyboardInterrupt
    if answer.isdecimal():
        option = next((o for o in request.get('options', []) if o['index']+1 == int(answer)), None)
        if option and option['enabled']:
            return dict(type='choose', token=request['token'], index=option['index'], source='workflow_trial_cli')
    if request['kind'] == 'question' and not request.get('blocked') and answer:
        return dict(type='answer', questionToken=request['token'], text=answer, source='workflow_trial_cli')
    raise ValueError('请输入可用选项编号；问题也可直接输入回答，q 取消测试。')


def answer_request(runtime, request):
    print('\n'+request.get('title', 'Agent 请求')+'\n'+request.get('message', ''), flush=True)
    if request.get('blocked'): print(request['blocked'])
    for option in request.get('options', []):
        print(f"  {option['index']+1}. {option['label']}" + ('' if option['enabled'] else '（不可选）'))
    while True:
        answer = ask('选择编号或输入回答（q 取消）：')
        try: command = decision(request, answer)
        except ValueError as exc:
            print(exc); continue
        current = read_state(runtime).get('request') or {}
        if current.get('token') != request['token']:
            print('请求已变化，未提交旧回答。'); return
        send(runtime, command)
        return


def report(args, cases):
    store = TrialStore(args.root)
    for case in cases: store.case(case)
    # One stable report contains current cases AND legacy history, in separate sections.
    return export_trials(args.root, output_name='comparison.html')


def worker(args):
    from proximic_ring.agent_workflow.agent.flow import Flow
    app = Flow(with_ring=False, codex_path=args.codex, runtime_dir=args.runtime, recording_dir=args.root)
    # Presets only grant their one app for this process, matching the prior tests.
    # Action confirmations and requests for broader access still use normal rules.
    if args.task in APPS: app.apps.add(APPS[args.task])
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.stop.set())
    app.run()
    return 0


def stop_worker(proc, runtime):
    if proc.poll() is not None: return
    send(runtime, dict(type='exit', source='workflow_trial_cli'))
    try: proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        # Only this CLI's new process group, including its app-server, is stopped.
        os.killpg(proc.pid, signal.SIGTERM)
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL); proc.wait(timeout=5)


def run(args, case):
    if sys.platform != 'darwin': raise ValueError('桌面实测目前仅支持 macOS。')
    if args.timeout <= 0: raise ValueError('--timeout 必须大于0。')
    from proximic_ring.agent_workflow.ui.agent_controller import find_codex
    codex = args.codex or find_codex()
    if not codex or not os.access(codex, os.X_OK): raise ValueError('未找到 Codex；请用 --codex 指定可执行文件。')
    mode = MODES[args.mode]
    task_prompt(case, mode)  # Reject invalid combinations before starting anything.
    print('测试：'+case['workflow']['name']+' / '+args.mode)
    print('任务版本：'+case['id'])
    print('请手动恢复共同起点：\n'+case['initial_state'])
    if args.task in APPS and not args.case:
        print('本次进程允许该应用内普通操作：'+APPS[args.task]+'；额外请求仍询问。')
    print('仅运行所选一组；不会自动复位、删除测试笔记或运行下一组。')
    if not args.initial_state_confirmed:
        while True:
            confirmation = ask('起点恢复后按 Enter 开始，q 退出：').lower()
            if confirmation == 'q': return 0
            if not confirmation: break
            print('请恢复起点后按 Enter，或输入 q 退出。')
    cache = REPO/'.cache/workflow-trials-cli'
    cache.mkdir(parents=True, exist_ok=True)
    with (cache/'runner.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('已有命令行 Workflow 测试运行，请先结束它。') from None
        runtime = cache/uuid.uuid4().hex
        (runtime/'commands').mkdir(parents=True)
        print('运行日志：'+str(runtime/'agent.log'), flush=True)
        cmd = [sys.executable, '-m', MODULE, 'run', '--worker', '--runtime', str(runtime),
               '--root', str(args.root), '--codex', str(codex)]
        if args.task in APPS and not args.case: cmd += ['--task', args.task]
        with (runtime/'agent.log').open('w') as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
            try:
                deadline = time.monotonic()+60
                while True:
                    state = read_state(runtime)
                    if proc.poll() is not None: raise RuntimeError('Agent 启动失败，请查看上面的 agent.log。')
                    if state.get('active') is False: raise RuntimeError(state.get('connectionMessage') or 'Agent 连接失败。')
                    if state.get('connectionState') == 'ready': break
                    if time.monotonic()>deadline: raise TimeoutError('等待 Agent 就绪超时。')
                    time.sleep(.2)
                send(runtime, dict(type='workflow_trial', case=case, mode=mode, initialStateConfirmed=True))
                deadline = time.monotonic()+args.timeout
                last_status = last_agent = answered = None
                while True:
                    state = read_state(runtime)
                    row = state.get('workflowTrial') or {}
                    if state.get('status') != last_status:
                        last_status = state.get('status'); print(last_status, flush=True)
                    if state.get('agent') and state['agent'] != last_agent:
                        last_agent = state['agent']; print(last_agent, flush=True)
                    request = state.get('request')
                    if request and request['token'] != answered:
                        answer_request(runtime, request); answered = request['token']
                    if row.get('status') not in (None, 'running') and state.get('computerControlState') != 'active': break
                    if state.get('workflowTrialError'): raise RuntimeError(state['workflowTrialError'])
                    if proc.poll() is not None: raise RuntimeError('Agent 已断开，请查看日志和运行记录。')
                    if time.monotonic()>deadline: raise TimeoutError('本轮测试超时，将停止该测试进程。')
                    time.sleep(.2)
            finally:
                stop_worker(proc, runtime)
    print(f"本轮：{row['status']}，{row['elapsed_seconds']:.2f} 秒，{row['tool_calls']} 次工具调用。")
    if row['status'] == 'completed' and not args.skip_verification:
        while True:
            answer = ask('请查看实际界面：目标完成？y=成功，n=失败，Enter=暂不核验：').lower()
            if answer in ('y', 'n', ''): break
        if answer:
            TrialStore(args.root).verify(row['id'], answer=='y', verified_by='user_cli', note='用户在CLI查看实际界面后核验。')
    page = report(args, [case])  # Use the exact case accepted before the run, even if files changed.
    print('运行记录：'+str(args.root/'workflow-tests'/(row['id']+'.run.json')))
    print('报告：'+str(page))
    return 0 if row['status']=='completed' else 1


def main(argv=None):
    p = parser(); args = p.parse_args(argv)
    from proximic_ring.agent_workflow import pipeline_policy
    if not pipeline_policy.legacy_workflow_enabled() and (args.worker or args.command in ('prepare','run')):
        print(pipeline_policy.DISABLED_MESSAGE, file=sys.stderr)
        return 1
    args.root = args.root.expanduser().resolve()
    args.tasks_dir = args.tasks_dir.expanduser().resolve()
    try:
        if args.worker: return worker(args)
        if args.command == 'workflows':
            for entry in available_workflows(args.root):
                print(entry['key']+' — '+entry['name']+' ('+entry['source']['prompt_version']+')')
            return 0
        if args.command == 'prepare':
            if not args.task or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.task):
                raise ValueError('请用 --task 指定新的任务目录名（小写字母、数字、下划线、连字符）。')
            entry = next((e for e in available_workflows(args.root) if e['key']==args.workflow), None)
            if entry is None: raise ValueError('请用 --workflow 指定 workflows 命令列出的流程标识。')
            directory = prepare_task(args.tasks_dir/args.task, entry)
            print(str(directory)+'\n请编辑 task.json，填写参数值和共同起点；baseline.txt / steps.txt 可直接查看或修改。')
            return 0
        if args.command == 'list':
            for case in suite_cases(args):
                source = case['source']
                name = source.get('task_key', '快照')
                print(name+' — '+case['workflow']['name']+'\n  版本：'+case['id'])
                print('  输入：'+str(args.case or args.tasks_dir/name))
                print('  原任务：'+case['goal'].replace('\n', ' '))
            print('mode=baseline / steps；两组均使用独立新会话，只发送各自的文本。')
            print('记录目录：'+str(args.root/'workflow-tests'))
            return 0
        if args.command == 'report':
            try: cases = suite_cases(args)
            except (ValueError, OSError) as exc:
                print('当前任务未就绪，仍可查看历史记录：'+str(exc), file=sys.stderr); cases = []
            print(report(args, cases)); return 0
        if not args.mode: p.error('preview/run 需要 --mode baseline 或 steps。')
        case = load_case(args.task, args.case, args.tasks_dir)
        prompt = task_prompt(case, MODES[args.mode])
        if args.command == 'preview': print(prompt); return 0
        return run(args, case)
    except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr); return 1
    except (KeyboardInterrupt, EOFError):
        print('\n测试已停止；中断的运行不算成功。', file=sys.stderr); return 130


if __name__ == '__main__':
    raise SystemExit(main())
