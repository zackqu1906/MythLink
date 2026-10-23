"""Explicit, reviewed consolidation of whole operations; never a background shortcut.

The plan pins heads, members, source coverage, branch review and parameter moves.
One SQLite transaction switches membership, aliases and the active definition.
Original definitions, events, decisions, parameter records and tickets stay intact.
This records a review, NOT a successful desktop replay or a semantic proof.
"""
import copy
import json

from .operation_identity import digest
from .operation_prototypes import heads, revision, attach
from .operation_store import encoded
from .operation_revision_validation import validate_update
from proximic_ring.agent_workflow.extraction.workflow_store import now


def membership_token(store, operation_ids):
    samples=sorted((s for s in store.rows('samples') if s['operation_id'] in operation_ids),key=lambda s:s['id'])
    keys={(s['id'],s['version_id']) for s in samples}
    members=sorted((m for m in store.rows('operation_memberships')
                    if (m['sample_id'],m['version_id']) in keys),key=lambda m:m['sample_id'])
    return digest([samples,members])


def _parameters(store, samples, rev, rules, merge_id, previous):
    from proximic_ring.agent_workflow.parameters.parameter_history import save_record
    from proximic_ring.agent_workflow.parameters.parameter_evidence import signature
    from proximic_ring.agent_workflow.parameters.parameter_prompt import VERSION
    record_ids=[]
    for sample in samples:
        old=previous.get(sample['id'])
        # Missing prior evidence remains eligible for normal background extraction.
        if not old or old['status']!='completed':continue
        values={p['name']:p for p in old['parameters']}; output=[]
        moves=rules[sample['version_id']]
        for p in rev['flow']['parameters']:
            name=p['name']; rule=moves.get(name); entry=values.get((rule or {}).get('source'))
            item=dict(name=name,status='missing',value='',evidence=[],
                      reason='合并没有为此输入补造实际值；可选输入可以留空，必填输入需确认。')
            if entry and entry['status'] in ('observed','mapped') and entry['value'].strip():
                item=copy.deepcopy(entry);item['name']=name
                if 'prefix' in rule or 'suffix' in rule:
                    item.update(status='mapped',value=rule.get('prefix','')+entry['value']+rule.get('suffix',''),
                                reason='按已审阅的参数契约组合旧值，非用户逐字原文；原值及事件证据保留。')
                item['migration']=dict(record_id=old['id'],parameter=rule['source'],rule=rule,merge_id=merge_id)
            elif entry:
                item['reason']='原参数 '+rule['source']+' 未确定：'+entry['reason']
            output.append(item)
        current=dict(sample,operation_id=rev['operation_id'],active_revision=rev['id'])
        source=json.loads(sample['source'])
        subject=digest([VERSION,sample['id'],sample['version_id'],rev['operation_id'],rev['id'],source,signature(store,source)])
        record_ids.append(save_record(store,current,subject,output,dict(method='reviewed_contract_mapping',
            prompt_version=None,merge_id=merge_id,previous_record_id=old['id']),export=False))
    return record_ids


def merge_reviewed(store, plan):
    """Apply a locally reviewed plan. Callers must obtain explicit merge authorization.

    Authorization is descriptive audit evidence, not inferred from an LLM decision.
    Semantic branch review is performed by the reviewer; code checks its scope and
    references and cannot prove the natural-language claims.
    """
    required={'revision_id','expected_heads','membership_token','review','parameter_moves'}
    if not isinstance(plan,dict) or set(plan)!=required:raise ValueError('合并计划字段错误')
    mid=digest(['reviewed-operation-merge-1',plan])
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        prior=store.db.execute('SELECT * FROM operation_merges WHERE id=?',(mid,)).fetchone()
        if prior:return dict(prior,detail=json.loads(prior['detail']))
        expected=plan['expected_heads'];current=heads(store)
        rev=revision(store,plan['revision_id']);generation=rev['generation']
        from .operation_synthesis import VERSION as SYNTHESIS_VERSION, validate_result
        from proximic_ring.agent_workflow.workflow_content import plain_flow
        unified=generation.get('pipeline')==SYNTHESIS_VERSION
        if not isinstance(expected,dict) or len(expected)<(1 if unified else 2):raise ValueError('合并需要完整的当前组')
        if unified and expected!=generation.get('expected_heads'):raise ValueError('合并范围与泛化输入不一致')
        if unified and plan['membership_token']!=generation.get('membership_token'):
            raise ValueError('泛化后组成员或来源发生变化，需要重新泛化')
        if any(oid not in current or current[oid]['id']!=rid or store.canonical(oid)!=oid for oid,rid in expected.items()):
            raise ValueError('组的当前版本已变化，需要重新审阅合并计划')
        if membership_token(store,expected)!=plan['membership_token']:raise ValueError('组成员或来源已变化，需要重新审阅')
        if store.db.execute("SELECT 1 FROM operation_jobs WHERE status='running'").fetchone():
            raise ValueError('后台处理仍在运行，请待其结束再应用合并计划')
        rev=revision(store,plan['revision_id']);oid=rev['operation_id']
        if (oid not in expected or rev['parent_id']!=expected[oid]
            or rev['status']!='candidate' or rev['change_kind']!='generalization'):
            raise ValueError('必须是本次目标组当前版本上的泛化候选')
        source_keys={(r['sample_id'],r['version_id']) for r in rev['sources']}
        samples=[s for s in store.rows('samples') if s['operation_id'] in expected or
                 (unified and (s['id'],s['version_id']) in source_keys)]
        members={(m['sample_id'],m['version_id']):m for m in store.rows('operation_memberships')}
        pending={}
        for s in samples:
            current_member = members.get((s['id'], s['version_id']))
            if unified and (not current_member or current_member['operation_id'] != s['operation_id']):
                a=store.db.execute("SELECT * FROM operation_admissions WHERE sample_id=? AND version_id=? AND status='generalization_pending'",
                                   (s['id'],s['version_id'])).fetchone()
                if a:pending[s['id']]=a['id']
        if any(s['id'] not in pending and ((s['id'],s['version_id']) not in members or
               members[s['id'],s['version_id']]['operation_id']!=s['operation_id']) for s in samples):
            raise ValueError('合并组存在尚未准入的来源，不能跳过复核')
        if unified and any(s['operation_id'] is not None and s['operation_id'] not in expected for s in samples):
            raise ValueError('来源已经转入其它组，需要重新泛化')
        refs={(r['sample_id'],r['version_id']) for r in rev['sources']}
        if refs!={(s['id'],s['version_id']) for s in samples}:raise ValueError('候选来源必须完整且精确覆盖全部待合并成员')
        versions=store.versions();vids={s['version_id'] for s in samples};generation=rev['generation']
        packet=dict(current_workflow=current[oid]['flow'],material_information={'kind':'generalization'},
                    sources=[dict(version_id=v,workflow=versions[v]) for v in sorted(vids)])
        value=dict(outcome='candidate',reason=rev['reason'],workflow=rev['flow'],
                   **{k:generation.get(k) for k in ('changes','coverage','parameter_bindings')})
        if unified:
            validate_result(dict(workflow=rev['flow'],reason=rev['reason'],parameter_bindings=generation['parameter_bindings']),
                            dict(current_workflow=plain_flow(current[oid]['flow']),
                                 sources=[dict(version_id=v,workflow=plain_flow(versions[v])) for v in sorted(vids)]))
        else:validate_update(value,packet)
        review=plan['review']
        if (not isinstance(review,dict) or not isinstance(review.get('authorization'),str) or not review['authorization'].strip()
            or review.get('execution_verified') is not False or not isinstance(review.get('branches'),dict)
            or set(review['branches'])!=vids):raise ValueError('需要明确授权及全部来源分支审阅；不得冒充执行核验')
        for branch in review['branches'].values():
            if (not isinstance(branch,dict) or not all(isinstance(branch.get(k),str) and branch[k].strip()
                for k in ('condition','verification','parameter_notes')) or not isinstance(branch.get('steps'),list)
                or not branch['steps'] or any(type(i)!=int or not 1<=i<=len(rev['flow']['steps']) for i in branch['steps'])):
                raise ValueError('每个分支须有触发条件、步骤、核验要求和参数审阅说明')
        rules=plan['parameter_moves'];names={p['name'] for p in rev['flow']['parameters']}
        if not isinstance(rules,dict) or set(rules)!=vids:raise ValueError('参数迁移必须覆盖全部来源版本')
        for vid,moves in rules.items():
            if not isinstance(moves,dict) or set(moves)-names:raise ValueError('参数迁移目标不存在')
            for target,rule in moves.items():
                source=rule.get('source') if isinstance(rule,dict) else None
                binding=generation['parameter_bindings'][vid].get(source) if isinstance(source,str) else None
                matches=(target in binding if isinstance(binding,list) else binding==target)
                if (not isinstance(rule,dict) or set(rule)-{'source','prefix','suffix'} or 'source' not in rule
                    or not all(isinstance(v,str) for v in rule.values())
                    or not matches):
                    raise ValueError('参数迁移须遵循候选的来源参数对应，不允许编造常量')
        from proximic_ring.agent_workflow.parameters.parameter_history import current_records
        previous={r['sample_id']:r for r in current_records(store) if r['operation_id'] in expected}
        for sample in samples:
            store.db.execute("UPDATE operation_admissions SET status='superseded' WHERE sample_id=? AND version_id=? AND status IN ('pending','uncertain','generalization_pending')",
                             (sample['id'],sample['version_id']))
            aid=digest([mid,sample['id'],sample['version_id']])
            store.db.execute('INSERT INTO operation_admissions VALUES (?,?,?,?,?,?)',
                (aid,sample['id'],sample['version_id'],'merged',encoded(dict(merge_id=mid,revision_id=rev['id'],
                 previous_operation=sample['operation_id'],branch=review['branches'][sample['version_id']])),now()))
            attach(store,sample,oid,rev['id'],aid)
        for old,rid in expected.items():
            store.db.execute("UPDATE operation_revisions SET status='historical' WHERE id=?",(rid,))
            store.db.execute("UPDATE operation_revisions SET status='superseded' WHERE operation_id=? AND status='candidate' AND id!=?",(old,rev['id']))
            store.db.execute("UPDATE operation_updates SET status='superseded' WHERE operation_id=? AND status IN ('pending','candidate')",(old,))
            if old!=oid:
                store.db.execute('UPDATE operations SET alias_of=? WHERE id=?',(oid,old))
                store.db.execute('DELETE FROM operation_heads WHERE operation_id=?',(old,))
        validation=dict(state='reviewed_merge_not_replayed',merge_id=mid,execution_verified=False,
                        review_notes=[],review=review,at=now())
        store.db.execute("UPDATE operation_revisions SET status='active',validation=? WHERE id=?",(encoded(validation),rev['id']))
        store.db.execute('UPDATE operation_heads SET active_revision=? WHERE operation_id=?',(rev['id'],oid))
        ids=_parameters(store,samples,rev,rules,mid,previous)
        detail=dict(plan=plan,parameter_record_ids=ids,previous_memberships=[members[s['id'],s['version_id']] for s in samples
                                                                          if (s['id'],s['version_id']) in members])
        store.db.execute('INSERT INTO operation_merges VALUES (?,?,?,?,?)',(mid,oid,rev['id'],encoded(detail),now()))
    return dict(id=mid,operation_id=oid,revision_id=rev['id'],detail=detail)
