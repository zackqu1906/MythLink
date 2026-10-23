"""后台管线入口：导入 → 通用流程准备 → 增量入组 → 按需修订。

每个步骤的规则在独立模块内；这里不实现相似度、prompt 或 SQL 归并逻辑。
历史 complete-linkage 实现在 operation_legacy，仅供旧结果回归对比。
"""
from proximic_ring.agent_workflow.operations.operation_ingest import sample_from_audit,ingest_audits,ingest_dataset
from proximic_ring.agent_workflow.operations.operation_prototypes import prepare
from proximic_ring.agent_workflow.operations.operation_admission import process_admission
from proximic_ring.agent_workflow.operations.operation_update import process_update
from proximic_ring.agent_workflow.operations.operation_display_jobs import process_display
from proximic_ring.agent_workflow.parameters.parameter_jobs import process_parameters
from proximic_ring.agent_workflow.operations.operation_jobs import recover as recover_abandoned_reviews
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_embeddings import agent_busy,EmbeddingDeferred


def regroup(store,*,cancelled=lambda:False):
    """Compatibility name for callers; now prepares an incremental admission queue."""
    if cancelled() or agent_busy():raise EmbeddingDeferred('Agent 忙碌，推迟操作库准备')
    prepare(store)
    return store.rows('operation_admissions')


def review_one(store,settings,*,model=None,retry=False,cancelled=lambda:False):
    if cancelled() or agent_busy():return False
    if retry:recover_abandoned_reviews(store)
    prepare(store)
    if process_admission(store,settings,model=model,retry=retry,cancelled=cancelled):return True
    if process_update(store,settings,model=model,retry=retry,cancelled=cancelled):return True
    if process_display(store,settings,model=model,retry=retry,cancelled=cancelled):return True
    return process_parameters(store,settings,model=model,retry=retry,cancelled=cancelled)


def sync_catalog(root,*,cancelled=lambda:False):
    store=OperationStore(root)
    try:
        _,errors=ingest_audits(store)
        regroup(store,cancelled=cancelled)
        return errors
    finally:store.close()
