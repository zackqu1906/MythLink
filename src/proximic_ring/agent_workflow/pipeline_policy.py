"""Release gate for the retired workflow-derived recommendation pipeline.

Keep saved evidence and pure readers available during the Computer History
migration. Persisted workflowAuto preferences cannot override this gate.
"""

LEGACY_WORKFLOW_ENABLED = False
DISABLED_MESSAGE = 'Workflow 提取、归组、参数生成及旧推荐流程已停用；历史记录仍可查看。'


def legacy_workflow_enabled():
    return LEGACY_WORKFLOW_ENABLED


def require_legacy_workflow():
    if not legacy_workflow_enabled():
        raise ValueError(DISABLED_MESSAGE)
