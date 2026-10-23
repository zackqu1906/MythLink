"""Release excludes for retired workflow inference; retain saved-record readers."""
from pathlib import Path

READERS = {
    'extraction.workflow_artifacts', 'extraction.workflow_limits',
    'extraction.workflow_retry', 'extraction.workflow_store',
    'extraction.workflow_version', 'extraction.workflow_view',
    'operations.operation_identity',
    'recommendation.recommendation_context', 'recommendation.recommendation_view',
}
LEGACY_UI = (
    'hub_interaction', 'hub_pointer', 'hub_sentence', 'hub_voice',
    'recommendation_dialog', 'recommendation_input',
    'recommendation_wheel', 'workflow_trial_dialog',
)


def excluded_modules(source_root):
    root = Path(source_root) / 'proximic_ring/agent_workflow'
    modules = []
    for group in ('extraction', 'operations', 'parameters', 'recommendation', 'execution'):
        for path in sorted((root / group).glob('*.py')):
            relative = group + '.' + path.stem
            if path.stem != '__init__' and relative not in READERS:
                modules.append('proximic_ring.agent_workflow.' + relative)
    modules += ['proximic_ring.agent_workflow.ui.' + name for name in LEGACY_UI]
    modules += ['proximic_ring.agent_workflow.recording.workbench_actions',
                'proximic_ring.agent_workflow.reporting.trajectory_benchmark',
                'proximic_ring.agent_workflow.reporting.dedup_benchmark',
                'transformers.models.xlm_roberta', 'transformers.models.xlm_roberta_xl']
    return modules


def include_data(path):
    # BGE weights were always downloaded separately; only their manifest was bundled.
    return '/agent_workflow/operations/' not in ('/' + str(path).replace('\\', '/'))
