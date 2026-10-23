"""Lightweight embedding cache and cancellable subprocess client. No torch imports."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json, now
from proximic_ring.agent_workflow.task_metadata import profile_text

MODEL = 'BAAI/bge-m3'
REVISION = '5617a9f61b028005a4858fdac845db406aefb181'
ENCODER_VERSION = 'tf5.15.0-torch2.11.0-cpu-fp32-cls-2048-v2'
TEXT_VERSION = 'whole-workflow-goal-steps-1'
DIMENSION = 1024
MAX_TOKENS = 2048


class EmbeddingDeferred(Exception):
    """Busy Agent or another embedding worker; retry when idle, not a failure."""


def runtime_directory():
    default = (Path.home() / 'Library/Application Support/ProxiMic Voice/workflow-embedding'
               if sys.platform == 'darwin' else Path.home() / '.cache/proximic/workflow-embedding')
    if os.environ.get('PROXIMIC_DATA_HOME'):
        default=Path(os.environ['PROXIMIC_DATA_HOME'])/'workflow-embedding'
    return Path(os.environ.get('PROXIMIC_EMBEDDING_RUNTIME', default)).expanduser()


def runtime_python():
    return Path(sys.executable)


def model_directory():
    return runtime_directory() / 'models/bge-m3'


def activity_directory():
    # Shared by checkouts: a CLI benchmark must also yield to a running app.
    identity = str(os.getuid()) if hasattr(os, 'getuid') else digest(str(Path.home()))[:12]
    path = Path(os.environ.get('PROXIMIC_EMBEDDING_ACTIVITY',
                               str(Path(tempfile.gettempdir()) / ('proximic-workflow-activity-' + identity))))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def publish_activity(busy):
    path = activity_directory() / (str(os.getpid()) + '.json')
    atomic_json(path, dict(pid=os.getpid(), busy=bool(busy), at=time.time()))
    return path


def agent_busy():
    for path in activity_directory().glob('*.json'):
        try:
            value = json.loads(path.read_text())
            if not value['busy'] or not 0 <= time.time() - value['at'] < 15:
                continue
            os.kill(int(value['pid']), 0)
            return True
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return False


def workflow_texts(flow):
    """Use existing natural-language content; no extra summarization or observations."""
    # Keep parameter roles from the extracted flow; exclude raw evidence logs.
    goal = flow['name'] + '\n目标：' + flow['goal']
    if flow.get('task_profile'):goal += '\n' + profile_text(flow['task_profile'])
    if flow.get('parameters'):
        goal += '\n输入：' + '\n'.join(p['name'] + '：' + p['description'] for p in flow['parameters'])
    from proximic_ring.agent_workflow.workflow_content import is_plain
    if is_plain(flow):
        return {'goal': goal.strip(), 'steps': '\n'.join(f'{i}. {s}' for i, s in enumerate(flow['steps'], 1))}
    lines = [f"{i}. {s['action']}\n执行：{s.get('method', s.get('target', ''))}\n完成条件：{s.get('check', '')}"
             for i, s in enumerate(flow['steps'], 1)]
    if flow.get('conditions'):
        lines.append('前提：' + '\n'.join(flow['conditions']))
    if flow.get('completion'):
        # New final criterion is part of the exact cache text; old cache text
        # stays byte-identical, so legacy workflows are not re-embedded.
        lines = [f"{i}. {s['action']}"+ ('\n关键判断：'+s['check'] if s.get('check') else '')
                 for i,s in enumerate(flow['steps'],1)]
        lines.append('最终完成判断：'+flow['completion'])
        if flow.get('conditions'):lines.append('前提：'+'\n'.join(flow['conditions']))
    return {'goal': goal.strip(), 'steps': '\n'.join(lines).strip()}


def cache_key(text):
    return digest([MODEL, REVISION, ENCODER_VERSION, TEXT_VERSION, text])


def valid_vector(vector):
    return (isinstance(vector, list) and len(vector) == DIMENSION
            and all(type(x) in (float, int) and math.isfinite(x) for x in vector)
            and abs(sum(x*x for x in vector) - 1) < .002)


@contextmanager
def compute_lock():
    """Nonblocking machine-wide lease; do not run two copies of this large model."""
    with (activity_directory() / 'embedding.lock').open('a+b') as handle:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise EmbeddingDeferred('另一个向量进程正在运行，空闲后继续') from None
        yield


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=2)


def run_encoder(requests, directory, *, cancelled=lambda: False):
    """Offline child writes incremental results so interrupted work can be reused."""
    python = runtime_python()
    from .embedding_install import installed
    if not installed():
        raise EmbeddingDeferred('召回模型尚未就绪，请在“设置 → Agent”下载或校验模型；任务记录已保留')
    with compute_lock(), tempfile.TemporaryDirectory(prefix='embedding-', dir=directory) as temp:
        source, target = Path(temp)/'input.json', Path(temp)/'output.json'
        atomic_json(source, dict(requests=requests, model_path=str(model_directory())))
        env = dict(os.environ)
        env.update(OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2',
                   VECLIB_MAXIMUM_THREADS='2', TOKENIZERS_PARALLELISM='false',
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        worker = Path(__file__).with_name('operation_embedding_worker.py')
        command=([str(python),'--workflow-embedding-worker'] if getattr(sys,'frozen',False)
                 else [str(python),str(worker)])
        process = subprocess.Popen(command+[str(source),str(target)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        start = time.monotonic(); interrupted = False
        try:
            while process.poll() is None:
                if cancelled() or agent_busy():
                    interrupted = True; break
                if time.monotonic() - start > 1200:
                    raise ValueError('本地向量计算超过20分钟，已停止；可稍后重试')
                time.sleep(.1)
        finally:
            stop_process(process)
        result = json.loads(target.read_text()) if target.exists() else {'vectors': {}, 'metrics': {}}
        result['interrupted'] = interrupted
        result['metrics']['subprocess_seconds'] = time.monotonic() - start
        if not interrupted and (process.returncode or result.get('error')):
            raise ValueError(result.get('error') or '本地向量进程异常退出；现有操作库保留')
        return result


def ensure_embeddings(directory, versions, *, cancelled=lambda: False, runner=None):
    """Cache by text/model/renderer, outside catalog write transactions."""
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    texts = {vid: workflow_texts(flow) for vid, flow in versions.items()}
    requests = {cache_key(text): text for fields in texts.values() for text in fields.values()}
    path = directory / 'embeddings.sqlite3'
    with sqlite3.connect(path, timeout=2) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY, vector TEXT, tokens INTEGER, created_at TEXT)')
        cached = {}
        for key in requests:
            row = db.execute('SELECT vector FROM vectors WHERE key=?', (key,)).fetchone()
            if row:
                try:
                    vector = json.loads(row[0])
                    if valid_vector(vector): cached[key] = vector
                except (TypeError, ValueError): pass
        os.chmod(path, 0o600)
        missing = {k: t for k, t in requests.items() if k not in cached}
        metrics = dict(model=MODEL, revision=REVISION, encoder_version=ENCODER_VERSION,
                       text_version=TEXT_VERSION, device='cpu', dtype='float32', threads=2,
                       dimension=DIMENSION, requested=len(requests), cache_hits=len(cached),
                       cache_misses=len(missing), created_at=now())
        try:
            if missing:
                if cancelled() or agent_busy():
                    raise EmbeddingDeferred('Agent/文本编辑忙碌，向量计算等待空闲')
                atomic_json(directory/'embedding-status.json', dict(status='running', **metrics))
                result = (runner or run_encoder)(missing, directory, cancelled=cancelled)
                for key, item in result['vectors'].items():
                    if key not in missing or not valid_vector(item.get('vector')):
                        raise ValueError('向量进程返回无效或不匹配的向量')
                    cached[key] = item['vector']
                    db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)',
                               (key, json.dumps(item['vector']), item['tokens'], now()))
                db.commit()
                metrics.update(result['metrics'])
                metrics['total_seconds'] = time.monotonic() - started
                atomic_json(directory/'embedding-last-compute.json', metrics)
                if result['interrupted']:
                    raise EmbeddingDeferred('Agent 已开始工作，向量进程已结束；完成的向量已缓存')
                if set(cached) != set(requests):
                    raise ValueError('向量结果不完整，未更新候选或操作归并')
            metrics['total_seconds'] = time.monotonic() - started
            atomic_json(directory/'embedding-status.json', dict(status='ready', **metrics))
        except (EmbeddingDeferred, ValueError, OSError) as exc:
            metrics['total_seconds'] = time.monotonic() - started
            atomic_json(directory/'embedding-status.json', dict(status='deferred' if isinstance(exc, EmbeddingDeferred) else 'failed',
                        error=str(exc), **metrics))
            raise
    vectors = {vid: {field: cached[cache_key(text)] for field, text in fields.items()}
               for vid, fields in texts.items()}
    return vectors, metrics
