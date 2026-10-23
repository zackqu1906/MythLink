"""Historical pairwise retrieval and complete-linkage, retained for offline regression only.

No network, desktop observations, generated steps, or common-subchain mining.
"""
from __future__ import annotations

VERSION = 'whole-workflow-bge-m3-complete-1'
# Review identity describes equivalence, not the retrieval model that found it.
EQUIVALENCE_VERSION = 'whole-operation-equivalence-2'
RETRIEVAL_THRESHOLD = .55
GOAL_THRESHOLD = .50
TOP_K = 3
from proximic_ring.agent_workflow.operations.operation_identity import APP_ALIASES,digest,normalize,app_tags,application_conflict


def pair_key(a,b):return digest([EQUIVALENCE_VERSION,*sorted((a,b))])


def candidates(versions, embeddings=None, *, required_pairs=()):
    """Two-channel cosine Top-K union; scores alone never authorize a merge.

    Cached normalized vectors need no torch/model import. For <=120 workflows an
    exact local dot product is sufficient; no approximate index is necessary.
    """
    ids=sorted(versions)
    if len(ids)<2:return []
    if embeddings is None:raise ValueError('缺少 BGE-M3 向量，不回退到 TF-IDF')
    scores={}
    for i,left in enumerate(ids):
        for right in ids[i+1:]:
            scores[left,right]=tuple(max(-1.,min(1.,sum(a*b for a,b in zip(embeddings[left][field],embeddings[right][field]))))
                                     for field in ('goal','steps'))
    nearest=set()
    for left in ids:
        eligible=[right for right in ids if right!=left and not application_conflict(versions[left],versions[right])]
        for channel in (0,1):
            ranked=sorted(eligible,key=lambda right:(-scores[tuple(sorted((left,right)))][channel],right))
            nearest.update(tuple(sorted((left,right))) for right in ranked[:TOP_K])
    required=set(required_pairs)
    pairs=[]
    for i,left in enumerate(ids):
        for j in range(i+1,len(ids)):
            right=ids[j];a,b=versions[left],versions[right]
            goal,method=scores[left,right]
            score=.75*goal+.25*method
            reason='';decision='pending'
            if application_conflict(a,b):
                reason='应用集合不同，不能仅凭通用步骤归并';decision='rejected'
            elif pair_key(left,right) in required:
                reason='已复核同类关系的完整组内补查；仍须逐对复核'
            elif (left,right) not in nearest:
                reason='未进入目标/步骤任一通道的 Top-K';decision='rejected'
            elif goal<GOAL_THRESHOLD or score<RETRIEVAL_THRESHOLD:
                reason='BGE-M3 目标/综合相似度低于候选阈值';decision='rejected'
            else:reason='BGE-M3 语义候选，等待完整目标与参数的后台复核'
            pairs.append(dict(key=pair_key(left,right),left=left,right=right,score=round(score,6),
                              goal_similarity=round(goal,6),method_similarity=round(method,6),
                              decision=decision,reason=reason,algorithm=VERSION,top_k=TOP_K,
                              goal_threshold=GOAL_THRESHOLD,threshold=RETRIEVAL_THRESHOLD))
    return pairs



def review_closure(versions, reviewed_same):
    """Recall missing cross-pairs in verified components before complete linkage."""
    components=[{vid} for vid in sorted(versions)]
    for left,right in reviewed_same:
        a=next((c for c in components if left in c),None)
        b=next((c for c in components if right in c),None)
        if a is not None and b is not None and a is not b:
            a.update(b);components.remove(b)
    return {pair_key(a,b) for group in components for a in group for b in group if a<b}


def complete_linkage(sample_versions, accepted, scores=None):
    """Merge only when EVERY cross-cluster pair is identical or verified equivalent.

    Prevents A≈B and B≈C from silently forcing A≈C. Deterministic tie breaking.
    """
    groups=[{s} for s in sorted(sample_versions)]
    scores=scores or {}
    def compatible(a,b):
        x,y=sample_versions[a],sample_versions[b]
        return x==y or pair_key(x,y) in accepted
    while True:
        possible=[(i,j) for i in range(len(groups)) for j in range(i+1,len(groups))
                  if all(compatible(a,b) for a in groups[i] for b in groups[j])]
        def similarity(pair):
            return min(1. if sample_versions[a]==sample_versions[b] else
                       scores.get(pair_key(sample_versions[a],sample_versions[b]),0.)
                       for a in groups[pair[0]] for b in groups[pair[1]])
        match=max(possible,key=similarity) if possible else None
        if match is None:return groups
        i,j=match;groups[i]|=groups.pop(j)
