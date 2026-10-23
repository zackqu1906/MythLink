"""通用版本旁的展示数据：只改读法，不改目标、参数键、向量输入或执行步骤。

一条不可变通用 revision 对应一份当前 display。文案仅显式重新生成时替换并保留历史。
新版候选的文案随候选保存，
只有对应 revision 启用后才被推荐读取。展示字数/参数对应由本地检查；语义仍由
生成 prompt 约束，不声称本地规则可以证明简写含义完全等价。
"""
import json
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.extraction.workflow_store import now, atomic_json


def validate_display(value, flow):
    if not isinstance(value, dict) or set(value) not in ({'title', 'summary', 'parameters'}, {'title', 'summary', 'parameters', 'sentence'}):
        raise ValueError('展示文案字段错误')
    def check(text, maximum):
        if (not isinstance(text, str) or not text.strip() or text != text.strip() or len(text) > maximum
            or any(ord(c) < 32 for c in text) or any(c in text for c in ('<', '>', '{', '}', '`'))):
            raise ValueError('展示文案须为长度范围内的单行纯文本')
    check(value['title'], 16); check(value['summary'], 48)
    parameters = value['parameters']
    if not isinstance(parameters, dict) or set(parameters) != {p['name'] for p in flow['parameters']}:
        raise ValueError('展示参数必须完整对应原始参数名，不允许重命名或新增')
    for item in parameters.values():
        if not isinstance(item, dict) or set(item) != {'label', 'placeholder'}:
            raise ValueError('参数展示字段错误')
        check(item['label'], 12); check(item['placeholder'], 36)
    if 'sentence' in value:
        sentence=value['sentence']
        if not isinstance(sentence,list) or not 1<=len(sentence)<=25:
            raise ValueError('目标句式必须为有序文本和原参数片段')
        seen=[];length=0
        for part in sentence:
            if not isinstance(part,dict):raise ValueError('句式片段格式错误')
            if set(part)=={'text'}:
                check(part['text'],80);length+=len(part['text'])
            elif set(part)=={'parameter'} and part['parameter'] in parameters:
                seen.append(part['parameter'])
            else:raise ValueError('句式只能包含固定文本或已有参数名')
        if len(seen)!=len(set(seen)) or set(seen)!=set(parameters) or length>120:
            raise ValueError('句式须将每个参数列出且只列出一次，固定文本不超过120字符')
    return value


def read_display(store, revision_id):
    row = store.db.execute('SELECT * FROM operation_displays WHERE revision_id=?', (revision_id,)).fetchone()
    if not row: return None
    return dict(row, content=json.loads(row['content']), generation=json.loads(row['generation']))


def save_display(store, revision_id, value, generation, *, replace=False):
    """Caller owns transaction; ordinary ingestion never rewrites saved copy.

    Explicit copy refresh is allowed without a new executable workflow version.
    Both old and new copy remain in history; frozen recommendations keep their
    original display snapshot. This cannot change a parameter name or step.
    """
    from proximic_ring.agent_workflow.operations.operation_identity import digest
    row = store.db.execute('SELECT flow FROM operation_revisions WHERE id=?', (revision_id,)).fetchone()
    if not row: raise ValueError('展示文案对应的通用版本不存在')
    validate_display(value, json.loads(row['flow']))
    previous=read_display(store,revision_id)
    if previous and not replace:return previous
    record=dict(revision_id=revision_id,content=value,generation=generation,created_at=now())
    for item in ([previous] if previous else [])+[record]:
        store.db.execute('INSERT OR IGNORE INTO operation_display_history VALUES (?,?,?,?,?)',
            (digest(item),revision_id,encoded(item['content']),encoded(item['generation']),item['created_at']))
    store.db.execute('INSERT INTO operation_displays VALUES (?,?,?,?) ON CONFLICT(revision_id) DO UPDATE SET '
                     'content=excluded.content,generation=excluded.generation,created_at=excluded.created_at',
                     (revision_id, encoded(value), encoded(generation), record['created_at']))
    record = read_display(store, revision_id)
    (store.directory/'displays').mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(store.directory/'displays'/(revision_id+'.json'), record)
    return record


def presentation(flow, display=None):
    """Frozen UI snapshot; safe legacy fallback while background generation is pending.

    The fallback never guesses parameter meanings from snake_case keys. It retains
    existing descriptions until a valid model-authored display exists. Execution
    must continue to read workflow, never this dictionary.
    """
    if display is not None:
        try: return validate_display(display, flow)
        except ValueError: pass
    return dict(title=flow['name'], summary=flow['goal'], parameters={
        p['name']: dict(label=p['description'], placeholder=p['description']) for p in flow['parameters']})


def save_inline_display(store, revision_id, value, generation):
    """A bad optional display cannot reject an otherwise valid admission/update.

    Keep the rejected final text as a failed display job. Automatic backfill skips
    this revision until explicit retry, preventing repeated paid repair requests.
    Missing fields from legacy results remain eligible for first-time backfill.
    """
    if value is None: return
    try: save_display(store, revision_id, value, generation)
    except ValueError as exc:
        from proximic_ring.agent_workflow.operations.operation_jobs import create_job, finish_job
        jid = create_job(store, 'display', revision_id, dict(revision_id=revision_id, origin_job=generation.get('job_id')))
        finish_job(store, jid, dict(display=value), generation, status='failed', error=str(exc))
