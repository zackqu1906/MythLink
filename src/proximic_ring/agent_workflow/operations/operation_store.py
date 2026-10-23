"""Persistent operation identities, versioned full workflows and separate feedback."""
from __future__ import annotations
import json
import os
import sqlite3
import uuid
from pathlib import Path
from proximic_ring.agent_workflow.extraction.workflow_store import now
from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.operations.operation_flow import validate_flow


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True)


class OperationStore:
    def __init__(self, root):
        self.root=Path(root);self.directory=self.root/'operations'
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=sqlite3.connect(self.directory/'catalog.sqlite3',timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY,flow TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,alias_of TEXT);
          CREATE TABLE IF NOT EXISTS samples(id TEXT PRIMARY KEY,version_id TEXT NOT NULL,operation_id TEXT,
            kind TEXT NOT NULL,source TEXT NOT NULL,context TEXT NOT NULL,occurred_at TEXT NOT NULL,updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS sample_versions(sample_id TEXT,version_id TEXT,source TEXT,at TEXT,
            PRIMARY KEY(sample_id,version_id));
          CREATE TABLE IF NOT EXISTS pairs(id TEXT PRIMARY KEY,left_id TEXT,right_id TEXT,status TEXT,detail TEXT);
          CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY,sample_id TEXT,old_id TEXT,new_id TEXT,at TEXT);
          CREATE TABLE IF NOT EXISTS impressions(id TEXT PRIMARY KEY,scope TEXT,context TEXT,items TEXT,at TEXT,policy TEXT);
          CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY,impression_id TEXT,operation_id TEXT,kind TEXT,at TEXT,
            UNIQUE(impression_id,operation_id,kind));
          CREATE TABLE IF NOT EXISTS review_jobs(id TEXT PRIMARY KEY,status TEXT,pairs TEXT,generation TEXT,error TEXT,at TEXT);
        ''')
        from proximic_ring.agent_workflow.operations.operation_schema import initialize
        initialize(self.db)
        from proximic_ring.agent_workflow.recommendation.recommendation_feedback import initialize as initialize_recommendations
        initialize_recommendations(self.db)
        from proximic_ring.agent_workflow.parameters.parameter_history import initialize as initialize_parameters
        initialize_parameters(self.db)
        os.chmod(self.directory/'catalog.sqlite3',0o600)

    def close(self):self.db.close()
    def rows(self,table):
        if table not in {'samples','operations','pairs','impressions','feedback','review_jobs','assignments',
                         'operation_heads','operation_revisions','operation_memberships','operation_admissions','operation_merges',
                         'operation_jobs','operation_updates','operation_membership_events','operation_displays','operation_display_history',
                         'recommendation_invocations','recommendation_requests','recommendation_exposures','recommendation_feedback',
                         'recommendation_executions','operation_parameter_history'}:raise ValueError('未知表')
        return [dict(r) for r in self.db.execute('SELECT * FROM '+table)]
    def versions(self):return {r['id']:json.loads(r['flow']) for r in self.db.execute('SELECT * FROM versions')}

    def ingest(self, sample):
        flow=sample['flow'];kind=sample['kind']
        if kind not in ('user','test','unknown'):raise ValueError('未知样本来源')
        validate_flow(flow)
        vid=digest(flow);sid=sample['id'];stamp=now()
        with self.db:
            old=self.db.execute('SELECT * FROM samples WHERE id=?',(sid,)).fetchone()
            if old and old['kind']!=kind:raise ValueError('不能将测试样本改为真实使用')
            self.db.execute('INSERT OR IGNORE INTO versions VALUES (?,?)',(vid,encoded(flow)))
            self.db.execute('INSERT OR IGNORE INTO sample_versions VALUES (?,?,?,?)',(sid,vid,encoded(sample['source']),stamp))
            args=(vid,kind,encoded(sample['source']),encoded(sample.get('context',{})),sample['occurred_at'])
            if old:
                if all(old[k]==v for k,v in zip(('version_id','kind','source','context','occurred_at'),args)):return False
                self.db.execute('UPDATE samples SET version_id=?,kind=?,source=?,context=?,occurred_at=?,updated_at=? WHERE id=?',(*args,stamp,sid))
            else:self.db.execute('INSERT INTO samples VALUES (?,?,NULL,?,?,?,?,?)',(sid,*args,stamp))
        return True

    def canonical(self, oid):
        seen=set()
        while oid not in seen:
            seen.add(oid);row=self.db.execute('SELECT alias_of FROM operations WHERE id=?',(oid,)).fetchone()
            if row is None:raise ValueError('操作 ID 不存在')
            if not row['alias_of']:return oid
            oid=row['alias_of']
        raise ValueError('操作别名循环')

    def assign(self, groups):
        samples={r['id']:r for r in self.rows('samples')}
        created={r['id']:r['created_at'] for r in self.rows('operations')}
        # Largest existing membership wins if a revised group splits. Never reuse
        # a retired alias; old links and feedback continue to resolve to the survivor.
        groups=sorted(groups,key=lambda g:(-len(g),sorted(g)))
        claimed=set();stamp=now()
        with self.db:
            for group in groups:
                prior={self.canonical(samples[s]['operation_id']) for s in group if samples[s]['operation_id']}
                choices=sorted(prior-claimed,key=lambda x:(created[x],x))
                oid=choices[0] if choices else uuid.uuid4().hex
                if not choices:self.db.execute('INSERT INTO operations VALUES (?,?,NULL)',(oid,stamp))
                claimed.add(oid)
                for sid in sorted(group):
                    old=samples[sid]['operation_id']
                    if old!=oid:
                        self.db.execute('INSERT INTO assignments(sample_id,old_id,new_id,at) VALUES (?,?,?,?)',(sid,old,oid,stamp))
                        self.db.execute('UPDATE samples SET operation_id=? WHERE id=?',(oid,sid))
            occupied={r[0] for r in self.db.execute('SELECT DISTINCT operation_id FROM samples')}
            for old in created.keys()-occupied:
                # An operation merged away points to its members' current operation.
                moved=[s for s in samples.values() if s['operation_id']==old]
                destinations={self.db.execute('SELECT operation_id FROM samples WHERE id=?',(s['id'],)).fetchone()[0] for s in moved}
                if len(destinations)==1:
                    self.db.execute('UPDATE operations SET alias_of=? WHERE id=?',(destinations.pop(),old))

    def remember(self, result, scope, *, surface='web'):
        """Compatibility name: persist a generated request, never infer an exposure."""
        from proximic_ring.agent_workflow.recommendation.recommendation_feedback import create_request
        return create_request(self, result, scope, surface=surface)

    def expose(self, request_id, operation_id, *, method='web_visible_500ms'):
        from proximic_ring.agent_workflow.recommendation.recommendation_feedback import expose
        return expose(self, request_id, operation_id, method=method)

    def feedback(self, exposure_id, operation, kind):
        from proximic_ring.agent_workflow.recommendation.recommendation_feedback import feedback
        feedback(self, exposure_id, operation, kind)
