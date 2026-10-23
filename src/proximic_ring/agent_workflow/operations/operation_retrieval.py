"""阶段 3：只召回组的当前通用 Workflow，不召回组内每一个成员。"""
from proximic_ring.agent_workflow.operations.operation_embeddings import ensure_embeddings
from proximic_ring.agent_workflow.operations.operation_identity import application_conflict

TOP_K=3
GOAL_THRESHOLD=.50
SCORE_THRESHOLD=.55
VERSION='bge-m3-operation-prototype-1'


def retrieve(directory, flow, prototypes, *, cancelled=lambda:False):
    """s=.75*cos(goal,input roles)+.25*cos(steps,conditions).

    Exact search is O(number_of_operations * 1024), not O(number_of_members²).
    Goal and steps are each a normalized BGE-M3 vector; existing text cache is reused.
    Top-3 and thresholds are conservative inherited heuristics, NOT calibrated
    probabilities or proof of SAME. Every non-identical admission still needs LLM.
    A low-score/no-candidate case may create a duplicate group; never force a match.
    """
    if not prototypes:return [],[]
    flows={'incoming':flow,**{oid:r['flow'] for oid,r in prototypes.items()}}
    vectors,_=ensure_embeddings(directory,flows,cancelled=cancelled)
    ranked=[]
    for oid,rev in prototypes.items():
        values={field:max(-1.,min(1.,sum(a*b for a,b in zip(vectors['incoming'][field],vectors[oid][field]))))
                for field in ('goal','steps')}
        score=.75*values['goal']+.25*values['steps']
        blocked=application_conflict(flow,rev['flow'])
        ranked.append(dict(operation_id=oid,revision_id=rev['id'],score=round(score,6),
            goal_similarity=round(values['goal'],6),step_similarity=round(values['steps'],6),
            eligible=not blocked and values['goal']>=GOAL_THRESHOLD and score>=SCORE_THRESHOLD,
            reason='应用不一致' if blocked else '仅为召回分数，需语义复核',algorithm=VERSION))
    ranked.sort(key=lambda r:(-r['score'],r['operation_id']))
    selected=[r for r in ranked if r['eligible']][:TOP_K]
    return selected,ranked
