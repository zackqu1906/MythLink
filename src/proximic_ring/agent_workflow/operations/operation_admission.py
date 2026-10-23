"""Retrieve → binary SAME/DIFFERENT → unified synthesis, without a novelty gate."""
import json
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.operations.operation_prototypes import heads, attach, seed
from proximic_ring.agent_workflow.operations.operation_retrieval import retrieve
from proximic_ring.agent_workflow.operations.operation_admission_prompt import PROMPT, VERSION
from proximic_ring.agent_workflow.operations.operation_synthesis import validate_decisions, queue
from proximic_ring.agent_workflow.operations.operation_jobs import create_job, finish_job, snapshot, safe_error, failed_output
from proximic_ring.agent_workflow.operations.operation_embeddings import EmbeddingDeferred, agent_busy
from proximic_ring.agent_workflow.operations.operation_model import call
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json
from proximic_ring.agent_workflow.workflow_content import plain_flow


def process_admission(store, settings, *, model=None, retry=False, cancelled=lambda: False, admission_id=None):
    if cancelled() or agent_busy(): return False
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if any(j['status'] == 'running' for j in store.rows('operation_jobs')): return False
        samples = {s['id']: s for s in store.rows('samples')}
        pending = []
        for a in store.rows('operation_admissions'):
            if admission_id is not None and a['id'] != admission_id: continue
            s = samples.get(a['sample_id'])
            if not s or s['version_id'] != a['version_id']:
                if a['status'] in ('pending', 'failed', 'uncertain'):
                    store.db.execute("UPDATE operation_admissions SET status='superseded' WHERE id=?", (a['id'],))
                continue
            if a['status'] in (('pending', 'failed', 'uncertain') if retry else ('pending',)): pending.append(a)
        if not pending: return False
        admission = min(pending, key=lambda a: (samples[a['sample_id']]['occurred_at'], a['id']))
        sample = samples[admission['sample_id']]
        flow = plain_flow(store.versions()[sample['version_id']])
        prototypes = heads(store); head_snapshot = snapshot(store)
        packet = dict(new_workflow=flow, source_version=sample['version_id'], candidates=[])
        jid = create_job(store, 'admission', admission['id'], packet)
        store.db.execute("UPDATE operation_admissions SET status='running' WHERE id=?", (admission['id'],))
    value = {}; generation = {}; detail = {}; decisions = []; known = set()
    try:
        if not json.loads(admission['detail']).get('force_review'):
            known = {m['operation_id'] for m in store.rows('operation_memberships')
                     if m['version_id'] == sample['version_id'] and m['operation_id'] in prototypes}
        if len(known) == 1:
            value = dict(exact_version_reuse=sample['version_id'])
        else:
            selected, ranked = retrieve(store.directory, flow, prototypes, cancelled=cancelled)
            detail['retrieval'] = ranked
            packet['candidates'] = [dict(operation_id=c['operation_id'], revision_id=c['revision_id'],
                workflow=plain_flow(prototypes[c['operation_id']]['flow'])) for c in selected]
            with store.db:
                store.db.execute('UPDATE operation_jobs SET input=? WHERE id=?', (encoded(packet), jid))
                atomic_json(store.directory / (jid + '.operation-input.json'), packet)
            if selected:
                value, generation = call(packet, settings, prompt=PROMPT, model=model, cancelled=cancelled)
                generation['prompt_version'] = VERSION
                decisions = validate_decisions(value, packet)
                detail['decisions'] = decisions
            else: value = dict(no_candidates=True)
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            fresh = store.db.execute('SELECT version_id FROM samples WHERE id=?', (sample['id'],)).fetchone()
            if fresh is None or fresh[0] != sample['version_id'] or snapshot(store) != head_snapshot:
                raise ValueError('复核期间来源或通用版本发生变化，结果已保留；请显式重试')
            same = [d for d in decisions if d['relation'] == 'same']
            if len(known) == 1:
                oid = next(iter(known)); rid = prototypes[oid]['id']
                attach(store, sample, oid, rid, admission['id'])
                status = 'accepted'; detail.update(operation_id=oid, revision_id=rid)
            elif same:
                uid = queue(store, sample, admission, same, prototypes)
                status = 'generalization_pending'; detail['update_id'] = uid
                # SAME permits a new parameter contract; don't execute the old
                # template on this task before the common workflow is available.
            else:
                oid, rid = seed(store, sample, admission_id=admission['id'])
                status = 'new_operation'; detail.update(operation_id=oid, revision_id=rid)
            detail['job_id'] = jid
            store.db.execute('UPDATE operation_admissions SET status=?,detail=? WHERE id=?',
                             (status, encoded(detail), admission['id']))
            finish_job(store, jid, value, generation)
    except EmbeddingDeferred as exc:
        with store.db:
            store.db.execute("UPDATE operation_admissions SET status='pending',detail=? WHERE id=?",
                             (encoded(dict(detail, deferred=str(exc))), admission['id']))
            finish_job(store, jid, value, generation, status='deferred', error=str(exc))
        return False
    except Exception as exc:
        value, generation = failed_output(exc, value, generation)
        with store.db:
            store.db.execute("UPDATE operation_admissions SET status='failed',detail=? WHERE id=?",
                             (encoded(dict(detail, error=safe_error(exc), job_id=jid)), admission['id']))
            finish_job(store, jid, value, generation, status='failed', error=safe_error(exc))
    return True
