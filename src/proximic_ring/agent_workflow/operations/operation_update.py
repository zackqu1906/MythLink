"""One synthesis worker for all compatible experiences; old queue IDs stay valid."""
import json
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.operations.operation_prototypes import heads, attach, add_revision
from proximic_ring.agent_workflow.operations.operation_generalization_prompt import PROMPT, VERSION
from proximic_ring.agent_workflow.operations.operation_synthesis import inputs, validate_result, VERSION as PIPELINE
from proximic_ring.agent_workflow.operations.operation_jobs import create_job, finish_job, snapshot, safe_error, failed_output
from proximic_ring.agent_workflow.operations.operation_embeddings import EmbeddingDeferred, agent_busy
from proximic_ring.agent_workflow.operations.operation_model import call


def process_update(store, settings, *, model=None, retry=False, cancelled=lambda: False, update_id=None):
    if cancelled() or agent_busy(): return False
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if any(j['status'] == 'running' for j in store.rows('operation_jobs')): return False
        pending = [u for u in store.rows('operation_updates')
                   if u['status'] in (('pending', 'failed') if retry else ('pending',))
                   and (update_id is None or u['id'] == update_id)]
        if not pending: return False
        update = min(pending, key=lambda u: (u['at'], u['id']))
        current = heads(store)
        try:
            packet, expected, refs, incoming = inputs(store, update, current)
        except (ValueError, StopIteration, KeyError):
            store.db.execute("UPDATE operation_updates SET status='superseded' WHERE id=?", (update['id'],))
            return True
        base = current[update['operation_id']]
        # Pin membership as well as heads; another admission can add a source
        # while this paid model request is in flight.
        from proximic_ring.agent_workflow.operations.operation_merge import membership_token
        members_before = membership_token(store, expected)
        head_snapshot = snapshot(store)
        try:
            jid = create_job(store, 'update', update['id'], packet)
        except ValueError as exc:
            detail = dict(json.loads(update['novelty']), preflight_error=str(exc))
            store.db.execute("UPDATE operation_updates SET status='failed',novelty=? WHERE id=?",
                             (encoded(detail), update['id']))
            return True
        store.db.execute("UPDATE operation_updates SET status='running' WHERE id=?", (update['id'],))
    value = {}; generation = {}
    try:
        value, generation = call(packet, settings, prompt=PROMPT, model=model, cancelled=cancelled)
        generation['prompt_version'] = VERSION
        validate_result(value, packet)
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            fresh = store.db.execute('SELECT version_id FROM samples WHERE id=?', (incoming['id'],)).fetchone()
            if (fresh is None or fresh[0] != incoming['version_id'] or snapshot(store) != head_snapshot
                    or membership_token(store, expected) != members_before):
                raise ValueError('泛化期间来源或版本发生变化；保留模型结果，请重新分类')
            rid = None
            if value['workflow'] is None:
                status = 'unresolved'
            elif value['workflow'] == packet['current_workflow'] and len(expected) == 1:
                # The synthesizer returned the exact already-active definition.
                # No new execution contract or content revision is introduced.
                attach(store, incoming, base['operation_id'], base['id'], update['admission_id'])
                status = 'no_change'
                store.db.execute("UPDATE operation_admissions SET status='accepted' WHERE id=?", (update['admission_id'],))
            else:
                audit = dict(generation, job_id=jid, pipeline=PIPELINE, expected_heads=expected,
                    membership_token=members_before, parameter_bindings=value['parameter_bindings'],
                    review_only=True)
                rid = add_revision(store, base['operation_id'], value['workflow'], parent=base['id'],
                    kind='generalization', reason=value['reason'], sources=refs, generation=audit)
                status = 'candidate'
            store.db.execute('UPDATE operation_updates SET status=?,revision_id=? WHERE id=?', (status, rid, update['id']))
            finish_job(store, jid, value, generation)
    except EmbeddingDeferred as exc:
        with store.db:
            store.db.execute("UPDATE operation_updates SET status='pending' WHERE id=?", (update['id'],))
            finish_job(store, jid, value, generation, status='deferred', error=str(exc))
        return False
    except Exception as exc:
        value, generation = failed_output(exc, value, generation)
        with store.db:
            store.db.execute("UPDATE operation_updates SET status='failed' WHERE id=?", (update['id'],))
            finish_job(store, jid, value, generation, status='failed', error=safe_error(exc))
    return True
