"""阶段 7：把版本化通用 Workflow 和已准入经验组成推荐库；纯本地读取。"""
import json
from proximic_ring.agent_workflow.operations.operation_identity import app_tags
from proximic_ring.agent_workflow.operations.operation_prototypes import heads,revision
from proximic_ring.agent_workflow.operations.operation_display import read_display,presentation


def library(store):
    with store.db:
        if not store.db.in_transaction:store.db.execute('BEGIN')
        versions=store.versions();current=heads(store);groups={}
        admitted={(m['sample_id'],m['version_id']):m for m in store.rows('operation_memberships')}
        for sample in store.rows('samples'):
            if not sample['operation_id']:continue
            oid=store.canonical(sample['operation_id'])
            member=admitted.get((sample['id'],sample['version_id']))
            if current and (not member or member['operation_id']!=oid):continue
            groups.setdefault(oid,[]).append(sample)
        revisions=store.rows('operation_revisions')
        result=[]
        for oid in sorted(set(groups)|set(current)):
            samples=groups.get(oid,[]);vids=sorted({s['version_id'] for s in samples})
            active=current.get(oid)
            # Read-only fallback for historical reports/tests; normal prepare()
            # always creates a real prototype before new admission/recommendation.
            flow=active['flow'] if active else versions[vids[0]]
            display=read_display(store,active['id']) if active else None
            result.append(dict(operation_id=oid,name=flow['name'],goal=flow['goal'],
                display=presentation(flow,(display or {}).get('content')),display_record=display,
                workflow=flow,active_revision=active,
                revision_history=[revision(store,r['id']) for r in revisions if store.canonical(r['operation_id'])==oid],
                merged_operation_ids=[r['id'] for r in store.rows('operations') if r['id']!=oid and store.canonical(r['id'])==oid],
                apps=app_tags(flow),variants=[dict(version_id=v,flow=versions[v]) for v in vids],
                samples=[dict(s,source=json.loads(s['source']),context=json.loads(s['context'])) for s in samples]))
    return result
