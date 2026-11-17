"""Text-only evaluation through the installed, ChatGPT-authenticated app server.

Uses the public stdio protocol, never reads or exports authentication tokens.
Each generation uses a fresh ephemeral session. This measures the local client
route, not the public OpenAI Responses API or the ChatGPT graphical interface.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import threading
import time


class LocalChatGPTClient:
    def __init__(self, workdir: Path, log_path: Path, timeout_s: float = 120,
                 memory_limit_mb: int = 768):
        self.timeout_s = timeout_s
        self.workdir = workdir.resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.sequence = 0
        self.notifications = []
        self.inbox = queue.Queue()
        self.log = log_path.open('w')
        binary = shutil.which('codex')
        if not binary:
            raise RuntimeError('The installed Codex CLI was not found')
        # These are per-process settings, not changes to the user's config.
        self.config = {
            'model_reasoning_effort': 'low',
            'project_doc_max_bytes': 0,
            'web_search': 'disabled',
            'features.apps': False,
            'features.plugins': False,
            'features.hooks': False,
            'features.shell_tool': False,
            'features.unified_exec': False,
            'features.computer_use': False,
            'features.browser_use': False,
            'features.image_generation': False,
            'features.view_image': False,
            'features.goals': False,
            'features.sleep_tool': False,
            'features.chronicle': False,
            'features.memories': False,
            'features.skill_search': False,
            'features.skip_host_skill_discovery': True,
        }
        args = [binary, 'app-server', '--stdio']
        for key, value in self.config.items():
            args += ['-c', f'{key}={json.dumps(value)}']
        self.process = subprocess.Popen(args, cwd=self.workdir, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1,
            start_new_session=True)
        self.memory_limit_mb = memory_limit_mb
        self.peak_tree_rss_mb = 0.0
        self.resource_error = None
        self.monitor_done = threading.Event()
        def monitor():
            while not self.monitor_done.is_set() and self.process.poll() is None:
                try:
                    listing = subprocess.check_output(
                        ['ps','-axo','pid=,ppid=,pgid=,rss='],text=True,timeout=3)
                    entries = [tuple(map(int,line.split())) for line in listing.splitlines() if line.strip()]
                    owned = {self.process.pid}
                    while True:
                        expanded = owned | {pid for pid,ppid,pgid,rss in entries if ppid in owned or pgid==self.process.pid}
                        if expanded == owned:
                            break
                        owned = expanded
                    rss_mb = sum(rss for pid,ppid,pgid,rss in entries if pid in owned or pid==os.getpid())/1024
                    self.peak_tree_rss_mb = max(self.peak_tree_rss_mb,rss_mb)
                    if rss_mb > self.memory_limit_mb:
                        self.resource_error = f'Evaluation RSS {rss_mb:.1f} MiB exceeded {self.memory_limit_mb} MiB cap'
                        self.terminate_group()
                        break
                except (OSError,subprocess.SubprocessError,ValueError) as exc:
                    self.resource_error = 'Memory monitor unavailable: '+str(exc)
                    self.terminate_group()
                    break
                self.monitor_done.wait(0.5)
        self.monitor = threading.Thread(target=monitor, daemon=True)
        self.monitor.start()
        def reader():
            for line in self.process.stdout:
                try:
                    self.inbox.put(json.loads(line))
                except json.JSONDecodeError:
                    self.inbox.put({'unparsed': line})
            self.inbox.put({'eof': True})
        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()
        try:
            self.initialization = self.call('initialize', {
                'clientInfo': {'name': 'proximic_text_evaluation', 'version': '1.0.0'},
                'capabilities': {'experimentalApi': True},
            })
            self.send({'method': 'initialized', 'params': {}})
        except BaseException:
            self.close()
            raise

    def send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
        self.process.stdin.flush()

    def receive(self, deadline):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError('Local ChatGPT request timed out')
        try:
            message = self.inbox.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError('Local ChatGPT request timed out') from exc
        if message.get('eof'):
            raise RuntimeError(self.resource_error or f'App server exited: {self.process.poll()}')
        if 'method' in message and 'id' in message:
            # An editing benchmark must not grant tools, permissions, or auth.
            self.send({'id': message['id'], 'error': {
                'code': -32601, 'message': 'Interactive actions are unavailable in text evaluation'}})
            raise RuntimeError('Unexpected interactive server request: ' + message['method'])
        return message

    def call(self, method, params):
        self.sequence += 1
        request = {'id': self.sequence, 'method': method, 'params': params}
        self.send(request)
        deadline = time.perf_counter() + self.timeout_s
        while True:
            message = self.receive(deadline)
            if message.get('id') == request['id']:
                if 'error' in message:
                    raise RuntimeError(json.dumps(message['error'], ensure_ascii=False))
                return message['result']
            self.notifications.append(message)

    def models(self):
        return self.call('model/list', {'limit': 100, 'includeHidden': True})

    def generate(self, prompt, user_text, model='gpt-6-astra', effort='low'):
        started = time.perf_counter()
        request = {'model': model, 'cwd': str(self.workdir), 'ephemeral': True,
                   'sandbox': 'read-only', 'approvalPolicy': 'never',
                   'baseInstructions': prompt, 'developerInstructions': '',
                   'config': self.config, 'serviceName': 'proximic-v4d-eval'}
        session = self.call('thread/start', request)
        if session.get('model') != model:
            raise RuntimeError('App server selected a different model')
        if session.get('instructionSources'):
            raise RuntimeError('Unexpected instruction files loaded')
        thread_id = session['thread']['id']
        self.notifications.clear()
        turn_request = {'threadId': thread_id, 'model': model, 'effort': effort,
                        'input': [{'type': 'text', 'text': user_text}]}
        turn_started = time.perf_counter()
        turn = self.call('turn/start', turn_request)
        events = list(self.notifications)
        self.notifications.clear()
        first_text_s = None
        deadline = turn_started + self.timeout_s
        while not any(e.get('method') == 'turn/completed' and
                      e.get('params', {}).get('threadId') == thread_id for e in events):
            event = self.receive(deadline)
            events.append(event)
            if event.get('method') == 'item/agentMessage/delta' and first_text_s is None:
                first_text_s = time.perf_counter() - turn_started
        completed_at = time.perf_counter()
        texts = []
        tool_items = []
        for event in events:
            if event.get('method') == 'item/completed':
                item = event['params']['item']
                if item.get('type') == 'agentMessage':
                    texts.append(item.get('text', ''))
                elif item.get('type') not in ('userMessage', 'reasoning'):
                    tool_items.append(item)
        completed = next(e['params']['turn'] for e in reversed(events)
                         if e.get('method') == 'turn/completed')
        result = {'session_request': request, 'session_response': session,
                  'turn_request': turn_request, 'turn_response': turn,
                  'events': events, 'final_text': '\n'.join(texts),
                  'latency_s': completed_at - started,
                  'generation_latency_s': completed_at - turn_started,
                  'session_setup_s': turn_started - started,
                  'first_text_s': first_text_s, 'tool_items': tool_items,
                  'turn_status': completed.get('status')}
        if completed.get('status') != 'completed' or tool_items or not texts:
            result['error'] = json.dumps(completed.get('error') or {
                'status': completed.get('status'), 'unexpected_tools': bool(tool_items),
                'empty_output': not texts}, ensure_ascii=False)
        return result

    def live_group_members(self):
        listing = subprocess.check_output(['ps','-axo','pid=,pgid=,stat='],text=True,timeout=3)
        members = []
        for line in listing.splitlines():
            fields = line.split()
            if len(fields)==3 and int(fields[1])==self.process.pid and not fields[2].startswith('Z'):
                members.append(int(fields[0]))
        return members

    def signal_group(self, sig):
        # macOS can report EPERM for an already-empty process group. Confirm
        # membership first instead of signaling a dead leader's group blindly.
        if not self.live_group_members():
            return
        try:
            os.killpg(self.process.pid,sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            if self.live_group_members():
                raise

    def terminate_group(self):
        self.signal_group(signal.SIGTERM)

    def close(self):
        self.monitor_done.set()
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.terminate_group()
        # Also stop any child helper that outlived the app-server leader.
        self.terminate_group()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.signal_group(signal.SIGKILL)
            self.process.wait(timeout=3)
        self.signal_group(signal.SIGKILL)
        self.reader.join(timeout=1)
        self.monitor.join(timeout=1)
        self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
