"""不可变参数实例与当前可用投影；原始任务、通用版本和推荐分数均不改。

一个实例绑定 sample/version/当前通用 revision/来源指纹。重新归组、重新提取
或启用新版后旧映射不再用于候选，历史仍保留。重复模型调用不算多次使用；
候选聚合按 session + episode 去重，而不是按提取或 job 数计数。
"""
import json
import uuid
from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.extraction.workflow_store import now, atomic_json
from .parameter_prompt import VERSION


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS operation_parameter_history(
        id TEXT PRIMARY KEY,subject_id TEXT NOT NULL,sample_id TEXT NOT NULL,
        version_id TEXT NOT NULL,operation_id TEXT NOT NULL,revision_id TEXT NOT NULL,
        scope TEXT NOT NULL,source_key TEXT NOT NULL,source TEXT NOT NULL,
        status TEXT NOT NULL,parameters TEXT NOT NULL,generation TEXT NOT NULL,
        job_id TEXT,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS parameter_subject ON operation_parameter_history(subject_id);
        CREATE INDEX IF NOT EXISTS parameter_operation ON operation_parameter_history(operation_id,revision_id,scope);
    ''')


def eligible_samples(store):
    rows=store.db.execute('''SELECT s.*,h.active_revision FROM samples s
        JOIN operation_memberships m ON m.sample_id=s.id AND m.version_id=s.version_id
            AND m.operation_id=s.operation_id
        JOIN operation_heads h ON h.operation_id=m.operation_id
        WHERE s.kind IN ('user','test') ORDER BY s.occurred_at,s.id''')
    for row in rows:
        source=json.loads(row['source'])
        if source.get('outcome')=='completed' and source.get('source_file'):
            yield dict(row)


def current_records(store, *, operation_id=None, scope=None):
    """Local read only. Latest attempt per current sample/contract, not old success.

    No raw files or model are touched on the Hub request path. If a paid attempt
    fails or is uncertain, an older contradictory mapping cannot silently return.
    """
    samples={s['id']:s for s in eligible_samples(store)}; seen=set()
    for row in store.db.execute('SELECT * FROM operation_parameter_history ORDER BY rowid DESC'):
        s=samples.get(row['sample_id'])
        if not s or (operation_id and row['operation_id']!=operation_id) or (scope and row['scope']!=scope):continue
        if (row['version_id'],row['operation_id'],row['revision_id'],row['scope'],row['source_key'])!=(
                s['version_id'],s['operation_id'],s['active_revision'],s['kind'],digest(json.loads(s['source']))):continue
        if s['id'] in seen:continue
        seen.add(s['id']); result=dict(row)
        for k in ('source','parameters','generation'):result[k]=json.loads(result[k])
        result['occurred_at']=s['occurred_at']
        yield result


def save_record(store,sample,subject,parameters,generation,*,job_id=None,status='completed',export=True):
    identifier=uuid.uuid4().hex; source=json.loads(sample['source']); stamp=now()
    record=dict(id=identifier,subject_id=subject,sample_id=sample['id'],version_id=sample['version_id'],
        operation_id=sample['operation_id'],revision_id=sample['active_revision'],scope=sample['kind'],
        source_key=digest(source),source=source,status=status,parameters=parameters,
        generation=dict({'prompt_version':VERSION},**generation),job_id=job_id,created_at=stamp)
    directory=store.directory/'parameter-history';directory.mkdir(exist_ok=True,mode=0o700)
    if export:atomic_json(directory/(identifier+'.json'),record)
    store.db.execute('INSERT INTO operation_parameter_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',tuple(
        encoded(v) if isinstance(v,(dict,list)) else v for v in record.values()))
    return identifier
