"""Durable extraction jobs and versioned drafts, separate from raw recording DBs."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .workflow_version import PROMPT_VERSION
from proximic_ring.agent_workflow.extraction.workflow_retry import RETRY_DELAYS, MAX_RETRIES, retry_message

TERMINAL = {'completed','failed','interrupted','unfinished','start_failed'}
JOB_SELECT = '''SELECT j.*, COALESCE(r.retry_number,0) AS retry_number,
    r.parent_job_id, COALESCE(r.root_job_id,j.id) AS root_job_id,
    COALESCE(r.available_at,j.created_at) AS available_at
    FROM jobs j LEFT JOIN job_retries r ON r.job_id=j.id'''


def now(): return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def atomic_text(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            os.chmod(temp,0o600)
            stream.write(value)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class WorkflowStore:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / 'workflows'
        self.directory.mkdir(parents=True,mode=0o700,exist_ok=True)
        self.db = sqlite3.connect(self.directory/'queue.sqlite3', timeout=3)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, episode_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, prompt_version TEXT NOT NULL, reason TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '', goal TEXT NOT NULL, result_file TEXT);
            CREATE INDEX IF NOT EXISTS job_source ON jobs(session_id,episode_id,fingerprint,prompt_version);
            CREATE TABLE IF NOT EXISTS job_retries(
                job_id TEXT PRIMARY KEY, parent_job_id TEXT NOT NULL UNIQUE,
                root_job_id TEXT NOT NULL, retry_number INTEGER NOT NULL,
                available_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workbench_actions(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, episode_id TEXT NOT NULL,
                step TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL,
                progress TEXT NOT NULL, result TEXT NOT NULL, error TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        ''')
        os.chmod(self.directory/'queue.sqlite3',0o600)

    def close(self): self.db.close()

    def activation(self, since):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',('activated_at',since))
        return self.db.execute('SELECT value FROM meta WHERE key=?',('activated_at',)).fetchone()[0]

    def enqueue(self, packet, *, manual=False):
        src = packet['source']; args=(src['session_id'],src['episode_id'],packet['fingerprint'],PROMPT_VERSION)
        # A manual request is a revision, unless an identical request is already pending.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if manual:
                pending = self.db.execute('''SELECT id FROM jobs WHERE session_id=? AND episode_id=?
                    AND (status='running' OR (status='queued' AND prompt_version=?))
                    ORDER BY created_at DESC,rowid DESC LIMIT 1''',(*args[:2],PROMPT_VERSION)).fetchone()
                if pending: return pending['id']
                self.db.execute("""UPDATE jobs SET status='failed',updated_at=?,error=?
                    WHERE session_id=? AND episode_id=? AND status='queued' AND prompt_version!=?""",
                    (now(),'旧版排队任务已由手动新版提取替代，未调用模型',*args[:2],PROMPT_VERSION))
            old = self.db.execute('''SELECT id,status FROM jobs WHERE session_id=? AND episode_id=?
                AND fingerprint=? AND prompt_version=? ORDER BY created_at DESC,rowid DESC LIMIT 1''',args).fetchone()
            if old and (not manual or old['status'] in ('queued','running')): return old['id']
            job_id=uuid.uuid4().hex; stamp=now()
            atomic_json(self.directory/(job_id+'.input.json'),packet)
            self.save_model_input(job_id, packet)
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (job_id,*args,'manual' if manual else 'automatic','queued',stamp,stamp,'',src['episode']['goal'],None))
        return job_id

    def save_model_input(self, job_id, packet):
        from .workflow_input import prepare_model_input
        prepared = prepare_model_input(packet)
        atomic_json(self.directory/(job_id+'.trajectory.json'), prepared.trajectory)
        atomic_json(self.directory/(job_id+'.trajectory-selection.json'), prepared.selection)
        atomic_json(self.directory/(job_id+'.model-input.json'), prepared.model_packet)
        atomic_json(self.directory/(job_id+'.compaction.json'), prepared.stats)

    def recover(self):
        # Called only while holding the worker lock. An interrupted HTTP request may
        # already have been charged; do not silently resend it after a restart.
        with self.db:
            self.db.execute("UPDATE jobs SET status='failed',updated_at=?,error=? WHERE status='running'",
                (now(),'提取进程中断，远端请求结果未知；请手动重新提取'))

    def next_job(self):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute(JOB_SELECT+" WHERE j.status='queued' AND COALESCE(r.available_at,j.created_at)<=? ORDER BY j.created_at,j.rowid LIMIT 1",(now(),)).fetchone()
            if row: self.db.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?",(now(),row['id']))
        return dict(row) if row else None

    def finish(self, job_id, *, result_file=None, error='', retry_packet=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            job=self.db.execute(JOB_SELECT+' WHERE j.id=?',(job_id,)).fetchone()
            self.db.execute('UPDATE jobs SET status=?,updated_at=?,result_file=?,error=? WHERE id=?',
                ('failed' if error else 'completed',now(),result_file,error,job_id))
            if error and retry_packet is not None and job and job['status']=='running':
                self._retry(dict(job),retry_packet)

    def _retry(self, job, packet):
        # In the same transaction as failure: a crash cannot lose the pending
        # retry or issue two children. A newer task revision supersedes this one.
        number=job['retry_number']
        latest=self.db.execute('''SELECT id FROM jobs WHERE session_id=? AND episode_id=?
            ORDER BY created_at DESC,rowid DESC LIMIT 1''',(job['session_id'],job['episode_id'])).fetchone()
        if number>=MAX_RETRIES or latest['id']!=job['id'] or job['prompt_version']!=PROMPT_VERSION:return
        jid=uuid.uuid4().hex;stamp=now()
        available=(datetime.fromisoformat(stamp)+timedelta(seconds=RETRY_DELAYS[number])).isoformat()
        atomic_json(self.directory/(jid+'.input.json'),packet)
        self.save_model_input(jid,packet)
        self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (jid,job['session_id'],job['episode_id'],job['fingerprint'],job['prompt_version'],
             'automatic_retry','queued',stamp,stamp,'',job['goal'],None))
        self.db.execute('INSERT INTO job_retries VALUES (?,?,?,?,?)',
            (jid,job['id'],job['root_job_id'],number+1,available))

    def retry_delay(self):
        """CLI can wait without prematurely abandoning a persisted delayed job."""
        row=self.db.execute("""SELECT MIN(COALESCE(r.available_at,j.created_at)) FROM jobs j
            LEFT JOIN job_retries r ON r.job_id=j.id WHERE j.status='queued'""").fetchone()
        return max(0,(datetime.fromisoformat(row[0])-datetime.fromisoformat(now())).total_seconds()) if row[0] else None

    def jobs(self):
        jobs=[dict(r) for r in self.db.execute(JOB_SELECT+' ORDER BY j.created_at DESC,j.rowid DESC')]
        return [dict(j,status_message=retry_message(j)) for j in jobs]

    def status(self, *, error='', tasks=None):
        atomic_json(self.directory/'status.json',dict(updated_at=now(),error=error,
            jobs=self.jobs()[:100],tasks=tasks or [],retry_policy=dict(max_retries=MAX_RETRIES,delays_seconds=RETRY_DELAYS)))
