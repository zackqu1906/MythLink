"""Offline release check for Computer History and retained recording readers."""
from importlib import import_module, util


def verify_runtime(*, frozen=False):
    from .. import pipeline_policy
    if pipeline_policy.legacy_workflow_enabled():
        raise RuntimeError('发行版必须使用 Computer History 推荐')
    for module in (
        'history.history_backend', 'history.history_service',
        'ui.history_recommendation_controller', 'ui.history_recommendation_overlay',
        'recording.records_server', 'recording.workbench_data', 'recording.workbench_view',
    ):
        import_module('proximic_ring.agent_workflow.' + module)
    if frozen:
        for module in (
            'proximic_ring.agent_workflow.extraction.workflows',
            'proximic_ring.agent_workflow.operations.operation_embedding_worker',
            'proximic_ring.agent_workflow.recommendation.recommendation_service',
            'transformers.models.xlm_roberta',
        ):
            try:
                spec = util.find_spec(module)
            except ModuleNotFoundError:
                spec = None
            if spec is not None:
                raise RuntimeError('安装包仍包含旧推荐组件：' + module)
