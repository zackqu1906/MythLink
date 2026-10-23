"""Best-effort background integration; never runs on the Agent command path."""
from proximic_ring.agent_workflow.operations.operation_catalog import sync_catalog, review_one
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json, now
from proximic_ring.agent_workflow.operations.operation_embeddings import EmbeddingDeferred
from proximic_ring.agent_workflow import pipeline_policy


def update_catalog(root, *, cancelled=lambda: False):
    if not pipeline_policy.legacy_workflow_enabled(): return False
    store=OperationStore(root)
    try:
        warnings=sync_catalog(root,cancelled=cancelled)
        export_operations(store)
        atomic_json(store.directory/'status.json',dict(updated_at=now(),warnings=warnings))
    except EmbeddingDeferred as exc:
        atomic_json(store.directory/'status.json',dict(updated_at=now(),deferred=str(exc)))
    except Exception as exc:
        atomic_json(store.directory/'status.json',dict(updated_at=now(),error=str(exc) if type(exc) is ValueError else type(exc).__name__))
    finally:store.close()


def review_catalog(root,settings,*,cancelled=lambda:False):
    if not pipeline_policy.legacy_workflow_enabled(): return False
    store=OperationStore(root)
    try:
        changed=review_one(store,settings,cancelled=cancelled)
        if changed:export_operations(store)
        return changed
    except Exception as exc:
        atomic_json(store.directory/'status.json',dict(updated_at=now(),error=type(exc).__name__))
        return False
    finally:store.close()
