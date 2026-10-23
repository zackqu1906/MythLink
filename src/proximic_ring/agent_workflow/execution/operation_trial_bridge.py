"""手动验证候选通用 Workflow：只准备现有 Trials 快照，不启动电脑操作。"""
from proximic_ring.agent_workflow.operations.operation_prototypes import revision
from proximic_ring.agent_workflow.execution.workflow_trials import make_case,TrialStore
from proximic_ring.agent_workflow.extraction.workflow_artifacts import workflow_steps


def prepare_trial(store,rid,parameters,initial_state):
    rev=revision(store,rid)
    case=make_case(dict(flow=rev['flow'],source=dict(operation_id=rev['operation_id'],operation_revision=rid),
                        natural_language_workflow=workflow_steps(rev['flow'])),
                   rev['flow']['goal'],parameters,initial_state)
    TrialStore(store.root).case(case)
    return store.root/'workflow-tests'/(case['id']+'.case.json')
