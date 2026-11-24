#!/usr/bin/env python3
"""Validate, maintain and select the canonical voice evaluation corpus (offline)."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MASTER = ROOT / 'tools/data/voice_eval_master.json'
DEFAULT_SUBSETS = ROOT / 'tools/data/voice_eval_subsets.json'
TASKS = ('modify', 'insert', 'polish', 'format', 'translate', 'compose')
PURPOSES = ('binary', 'six_class', 'editing', 'fine_22')
ROLES = ('none', 'positive', 'negative', 'no_change')


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()


def semantic_fingerprint(case):
    """Bind a review to the actual question, labels, tags and grading contract."""
    return fingerprint({k: case[k] for k in ('input', 'labels', 'applicability',
        'exclusion_reasons', 'special_tags', 'special_annotations', 'editing_expectations')})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def string_list(value, *, nonempty=False):
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(isinstance(v, str) and v.strip() for v in value)
            and len(set(value)) == len(value))


def validate_master(data):
    """Reject incomplete labels, duplicate inputs and unsafe evaluation applicability."""
    require(data.get('schema_version') == 'voice-eval-master-v1', 'Unknown master schema')
    require(isinstance(data.get('dataset_id'), str) and data['dataset_id'], 'Missing dataset ID')
    require(type(data.get('revision')) is int and data['revision'] > 0, 'Invalid revision')
    require(isinstance(data.get('updated_at'), str) and data['updated_at'], 'Missing revision date')
    require(data.get('policy', {}).get('dictation_in_editing') is False
            and data['policy'].get('six_class_is_multilabel') is True, 'Invalid evaluation policy')
    require(string_list(data['policy'].get('editing_rubric'), nonempty=True), 'Missing editing rubric')
    require(set(data.get('task_labels', {})) == set(TASKS), 'Six-task catalogue differs')
    require(isinstance(data.get('tag_catalog'), dict), 'Missing tag catalogue')
    require(all(isinstance(k, str) and k and isinstance(v, str) and v for k,v in data['tag_catalog'].items()), 'Invalid tag definitions')
    require(len(set(data['tag_catalog'].values())) == len(data['tag_catalog']), 'Ambiguous translated tag names')
    require(isinstance(data.get('sources'), list) and data['sources'], 'Missing source manifest')
    require(isinstance(data.get('cases'), list) and data['cases'], 'Empty master')
    fine_parents = {t['id']: t['parent'] for t in data['fine_task_catalog']}
    seen_ids, seen_inputs, seen_aliases = set(), set(), {}
    for c in data['cases']:
        cid = c.get('id')
        require(isinstance(cid, str) and cid.strip() and cid not in seen_ids, 'Duplicate/empty ID')
        seen_ids.add(cid)
        try:
            require(c['kind'] in ('single', 'sequence'), 'Invalid kind')
            inp = c['input']
            require({'target', 'context', 'utterances'} <= set(inp)
                    <= {'target', 'context', 'utterances', 'saved_context'}, 'Unexpected model input fields')
            require('saved_context' not in inp or isinstance(inp['saved_context'], str),
                    'saved_context must be text')
            require(isinstance(inp['target'], str), 'Target must be text')
            require(isinstance(inp['context'], dict), 'Context must be an object')
            require(isinstance(inp['context'].get('messages'), list), 'context.messages must be a list')
            for m in inp['context']['messages']:
                require(m.get('speaker') in ('self', 'other', 'unknown') and isinstance(m.get('text'), str), 'Invalid OCR message')
            require(isinstance(inp['utterances'], list) and inp['utterances']
                    and all(isinstance(u, str) and u.strip() for u in inp['utterances']), 'Missing utterances')
            sequence = c['kind'] == 'sequence'
            require((len(inp['utterances']) > 1) == sequence, 'Kind/turn-count mismatch')
            digest = fingerprint(inp)
            require(c['input_sha256'] == digest, 'Stale input fingerprint')
            require(digest not in seen_inputs, 'Duplicate input: merge provenance instead')
            seen_inputs.add(digest)
            require(string_list(c['aliases'], nonempty=True), 'Missing aliases')
            for alias in set(c['aliases']) | {cid}:
                require(alias not in seen_aliases or seen_aliases[alias] == cid, 'Ambiguous ID/alias: '+alias)
                seen_aliases[alias] = cid
            labels = c['labels']
            require(set(labels) == {'intent', 'edit_tasks', 'fine_edit_tasks'}, 'Unexpected label fields')
            intent, tasks, fine = (labels[k] for k in ('intent', 'edit_tasks', 'fine_edit_tasks'))
            scope = c.get('evaluation_scope')
            require(scope in (None, 'asr_final'), 'Unknown evaluation scope')
            preprocessor_only = scope == 'asr_final'
            if preprocessor_only:
                contracts = c['special_annotations'].get('asr_intent_recovery', [])
                require(not sequence and 'asr_intent_recovery' in c['special_tags']
                        and isinstance(contracts, list) and contracts, 'Missing ASR recovery contracts')
                for contract in contracts:
                    require(contract['kind'] in ('修复', '保护', '观察') and contract['criterion']
                            and contract['id'] and contract['gold'], 'Invalid ASR recovery contract')
            if sequence or preprocessor_only:
                require(intent is None and tasks is None and fine is None, 'Case has no downstream routing labels')
            else:
                require(intent in ('dictation', 'edit'), 'Missing binary label')
                require(string_list(tasks) and set(tasks) <= set(TASKS), 'Invalid six-task labels')
                require(tasks == [t for t in TASKS if t in tasks], 'Task order must follow catalogue')
                require(bool(tasks) == (intent == 'edit'), 'Intent/task mismatch')
                require('modify' not in tasks or bool(inp['target'].strip()), 'Modify requires editable source')
            if fine is not None:
                require(string_list(fine, nonempty=True) and set(fine) <= fine_parents.keys(), 'Invalid fine labels')
                require({fine_parents[t] for t in fine} == set(tasks or []), 'Fine/coarse labels disagree')
            applicable = c['applicability']
            require(set(applicable) == set(PURPOSES) and all(type(x) is bool for x in applicable.values()), 'Invalid applicability flags')
            expected_flags = {'binary': not sequence, 'six_class': not sequence and intent == 'edit',
                              'editing': sequence or intent == 'edit', 'fine_22': bool(fine)}
            if preprocessor_only:
                expected_flags = dict.fromkeys(PURPOSES, False)
            require(applicable == expected_flags, 'Applicability conflicts with labels/kind')
            require(set(c['exclusion_reasons']) == {p for p in PURPOSES if not applicable[p]}, 'Every disabled task needs an exclusion reason')
            require(all(isinstance(s, str) and s.strip() for s in c['exclusion_reasons'].values()), 'Empty exclusion reason')
            require(string_list(c['special_tags']) and set(c['special_tags']) <= data['tag_catalog'].keys(), 'Unknown/duplicate special tag')
            char = c['special_annotations']['character_clarification']
            require(type(char['present']) is bool and char['role'] in ROLES, 'Invalid clarification annotation')
            require(char['present'] == (char['role'] != 'none'), 'Clarification role/presence mismatch')
            require(char['present'] == ('character_clarification' in c['special_tags']), 'Clarification tag missing/inconsistent')
            if char['present']:
                require('character_clarification_'+char['role'] in c['special_tags'], 'Clarification role tag missing')
                require(isinstance(char['mechanism'], str) and char['mechanism'], 'Missing clarification mechanism')
            role_tags = {t for t in c['special_tags'] if t.startswith('character_clarification_')}
            require(role_tags == ({'character_clarification_'+char['role']} if char['present'] else set()),
                    'Contradictory clarification role tags')
            if char['role'] == 'positive':
                require(applicable['editing'] and (sequence or 'modify' in tasks),
                        'Positive clarification needs an effective correction task')
            require(c['split'] in ('development', 'holdout', 'regression'), 'Invalid split')
            annotation = c['annotation']
            require(annotation['status'] == 'curated' and annotation['author'] and annotation['date']
                    and annotation['label_reason'], 'Missing reviewed annotation')
            require(type(annotation['independent_human_review']) is bool, 'Missing review provenance')
            require(isinstance(annotation['input_amendments'], list), 'Missing input amendment history')
            require(isinstance(c['provenance'], list) and c['provenance'], 'Missing source provenance')
            for source in c['provenance']:
                require(isinstance(source.get('file'), str) and source['file']
                        and isinstance(source.get('case_id'), str) and source['case_id'], 'Invalid source reference')
            relations = c['relations']
            require(set(relations) == {'contrast_pair_ids', 'related_case_ids'}
                    and all(string_list(v) for v in relations.values()), 'Invalid relationships')
            expectations = c['editing_expectations']
            require(isinstance(expectations, list), 'Expectations must be a list')
            require(len(expectations) == (len(inp['utterances']) if applicable['editing'] else 0), 'Missing editing expectations or dictation leaked into editing')
            for i, e in enumerate(expectations):
                require(e['output_scope'] == 'full_text' and e['grading'] == 'semantic_with_checks', 'Invalid editing contract')
                require(e['input_source'] == ('initial_target' if i == 0 else 'previous_actual_output'), 'Sequence must use actual previous output')
                require(e['reference_text'] is None or isinstance(e['reference_text'], str), 'Invalid reference')
                require(string_list(e['requirements'], nonempty=True), 'Missing semantic requirements')
                require(isinstance(e['checks'], dict) and set(e['checks']) <= {'contains', 'contains_ci', 'excludes', 'max_chars'}, 'Invalid mechanical checks')
                for key, value in e['checks'].items():
                    require((type(value) is int and value > 0) if key == 'max_chars' else
                            isinstance(value, list) and all(isinstance(v, str) and v for v in value)
                            and len(set(value)) == len(value), 'Invalid '+key)
            review = annotation.get('semantic_review')
            if review is not None:
                require(review.get('status') == 'reviewed' and review.get('reviewer') and review.get('date'), 'Invalid semantic review')
                require(review.get('semantic_sha256') == semantic_fingerprint(c), 'Semantic review is stale')
        except (KeyError, TypeError) as exc:
            raise ValueError(f'{cid}: incomplete schema ({exc})') from exc
        except ValueError as exc:
            raise ValueError(f'{cid}: {exc}') from exc
    for c in data['cases']:
        require(set(c['relations']['related_case_ids']) <= seen_ids, c['id']+': dangling related case')
    return data


def load_master(path=DEFAULT_MASTER):
    return validate_master(json.loads(Path(path).read_text(encoding='utf-8')))


def resolve_tag(data, tag):
    if tag in data['tag_catalog']:
        return tag
    matches = [k for k, label in data['tag_catalog'].items() if label == tag]
    require(len(matches) == 1, 'Unknown tag: '+tag)
    return matches[0]


def select_cases(data, *, tasks=(), tags=(), any_tags=(), exclude_tags=(), intent=None, kind=None,
                 split=None, clarification_role=None, ids=(), source=None):
    require(set(tasks) <= set(PURPOSES), 'Unknown evaluation task')
    tags = {resolve_tag(data, t) for t in tags}
    any_tags = {resolve_tag(data, t) for t in any_tags}
    excluded = {resolve_tag(data, t) for t in exclude_tags}
    require(not tags & excluded, 'A tag cannot be required and excluded')
    require(intent in (None, 'dictation', 'edit'), 'Unknown intent')
    require(kind in (None, 'single', 'sequence'), 'Unknown kind')
    require(split in (None, 'development', 'holdout', 'regression'), 'Unknown split')
    require(clarification_role in (None, *ROLES), 'Unknown clarification role')
    known = {a for c in data['cases'] for a in [c['id'], *c['aliases']]}
    require(set(ids) <= known, 'Unknown IDs: '+', '.join(sorted(set(ids)-known)))
    if source is not None:
        require(any(p['file'] == source for c in data['cases'] for p in c['provenance']), 'Unknown source path')
    return [c for c in data['cases'] if all(c['applicability'][p] for p in tasks)
            and tags <= set(c['special_tags']) and not excluded & set(c['special_tags'])
            and (not any_tags or any_tags.intersection(c['special_tags']))
            and (intent is None or c['labels']['intent'] == intent)
            and (kind is None or c['kind'] == kind) and (split is None or c['split'] == split)
            and (clarification_role is None or c['special_annotations']['character_clarification']['role'] == clarification_role)
            and (not ids or set(ids) & {c['id'], *c['aliases']})
            and (source is None or any(p['file'] == source for p in c['provenance']))]


def model_input(case, step=0, previous_output=None):
    """Only model-visible fields. Ground truth, tags and source metadata never enter."""
    require(type(step) is int and 0 <= step < len(case['input']['utterances']), 'Invalid step')
    require(step == 0 or isinstance(previous_output, str), 'Later turns require actual previous output')
    result = {'utterance': case['input']['utterances'][step],
            'editable_text': case['input']['target'] if step == 0 else previous_output,
            'screen_context': deepcopy(case['input']['context'])}
    if 'saved_context' in case['input']:
        result['saved_context'] = case['input']['saved_context']
    return result


def summary(cases):
    return {'cases': len(cases), 'applicability': {p: sum(c['applicability'][p] for c in cases) for p in PURPOSES},
            'intent': dict(Counter(c['labels']['intent'] or c.get('evaluation_scope') or 'sequence' for c in cases)),
            'kind': dict(Counter(c['kind'] for c in cases)),
            'edit_tasks': dict(Counter(t for c in cases for t in c['labels']['edit_tasks'] or [])),
            'special_tags': dict(Counter(t for c in cases for t in c['special_tags'])),
            'clarification_roles': dict(Counter(c['special_annotations']['character_clarification']['role'] for c in cases))}


def subset_document(data, filters):
    cases = select_cases(data, **filters)
    require(bool(cases), 'Selection is empty; check the filters')
    return {'schema_version': 'voice-eval-subset-v1', 'master_dataset_id': data['dataset_id'],
            'master_revision': data['revision'], 'master_sha256': fingerprint(data),
            'filters': filters, 'count': len(cases), 'policy': data['policy'],
            'cases': deepcopy(cases)}


def legacy_cases(cases, output_format):
    rows = []
    for c in cases:
        require(c['kind'] == 'single', 'Legacy export requires --kind single; sequences must remain explicit')
        inp = model_input(c)
        row = {'id': c['id'], 'utterance': inp['utterance'], 'target': inp['editable_text'],
               'context': inp['screen_context'], 'expected_tasks': c['labels']['edit_tasks'],
               'master_case_id': c['id'], 'special_tags': c['special_tags']}
        if 'saved_context' in inp:
            row['saved_context'] = inp['saved_context']
        if output_format == 'routing':
            require(c['applicability']['binary'], 'Case has no downstream routing labels')
            row['expected'] = {'task': c['labels']['intent'], 'tasks': c['labels']['edit_tasks']}
        else:
            require(c['applicability']['editing'], 'Dictation cannot be exported for editing')
            e = c['editing_expectations'][0]
            row.update(edit_plan={'tasks': c['labels']['edit_tasks']},
                       expected={**e['checks'], 'reference_text': e['reference_text'], 'semantic_review_required': True},
                       reference=e['reference_text'], generation_expected=e['checks'],
                       review_points='；'.join(e['requirements']), generate=True)
        rows.append(row)
    return rows


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def maintain(path, incoming, *, update, note):
    path = Path(path)
    before = path.read_bytes()
    data = validate_master(json.loads(before))
    require(isinstance(incoming, list) and incoming, 'Expected a nonempty array of canonical cases')
    require(isinstance(note, str) and bool(note.strip()), 'A maintenance note is required')
    lookup = {c['id']: i for i, c in enumerate(data['cases'])}
    require(len({c['id'] for c in incoming}) == len(incoming), 'Duplicate incoming IDs')
    changes = []
    for c in deepcopy(incoming):
        exists = c['id'] in lookup
        require(exists == update, 'Update requires existing IDs; add requires new IDs: '+c['id'])
        c['input_sha256'] = fingerprint(c['input'])
        review = c['annotation'].get('semantic_review')
        if review is not None and review.get('semantic_sha256') != semantic_fingerprint(c):
            # A copied old review is not evidence for changed inputs or annotations.
            c['annotation'].pop('semantic_review')
            c['annotation']['review_note'] = '输入、标签或评分预期已变更，原逐条复核不再适用于本版本。'
        if update:
            old = data['cases'][lookup[c['id']]]
            changes.append({'case_id': c['id'], 'previous_record_sha256': fingerprint(old),
                            'previous_labels': old['labels'], 'new_labels': c['labels']})
            c['aliases'] = list(dict.fromkeys([*old['aliases'], *c['aliases']]))
            provenance = {fingerprint(p): p for p in [*old['provenance'], *c['provenance']]}
            c['provenance'] = list(provenance.values())
            amendments = {fingerprint(a): a for a in [*old['annotation']['input_amendments'], *c['annotation']['input_amendments']]}
            c['annotation']['input_amendments'] = list(amendments.values())
            if old['input'] != c['input']:
                c['annotation']['input_amendments'].append({'field': 'input', 'before': old['input'],
                    'after': c['input'], 'reason': note})
            data['cases'][lookup[c['id']]] = c
        else:
            data['cases'].append(c)
    data['cases'].sort(key=lambda c: c['id'])
    data['revision'] += 1
    data['updated_at'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    data.setdefault('change_log', []).append({'revision': data['revision'], 'at': data['updated_at'],
        'action': 'update' if update else 'add', 'case_ids': [c['id'] for c in incoming], 'note': note,
        'annotation_changes': changes})
    validate_master(data)
    require(path.read_bytes() == before, 'Master changed concurrently; reload before writing')
    write_json(path, data)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master', type=Path, default=DEFAULT_MASTER)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('validate')
    sub.add_parser('stats')
    export = sub.add_parser('export')
    export.add_argument('--task', dest='tasks', action='append', choices=PURPOSES, default=[])
    export.add_argument('--tag', dest='tags', action='append', default=[])
    export.add_argument('--any-tag', dest='any_tags', action='append', default=[])
    export.add_argument('--exclude-tag', dest='exclude_tags', action='append', default=[])
    export.add_argument('--intent', choices=['dictation', 'edit'])
    export.add_argument('--kind', choices=['single', 'sequence'])
    export.add_argument('--split', choices=['development', 'holdout', 'regression'])
    export.add_argument('--clarification-role', choices=ROLES)
    export.add_argument('--id', dest='ids', action='append', default=[])
    export.add_argument('--source')
    export.add_argument('--format', choices=['canonical', 'routing', 'editing'], default='canonical')
    export.add_argument('--output', type=Path, required=True)
    refresh = sub.add_parser('refresh')
    refresh.add_argument('--definitions', type=Path, default=DEFAULT_SUBSETS)
    refresh.add_argument('--output-dir', type=Path, required=True)
    for command in ('add', 'update'):
        p = sub.add_parser(command)
        p.add_argument('--input', type=Path, required=True)
        p.add_argument('--note', required=True)
    args = parser.parse_args(argv)
    try:
        data = load_master(args.master)
        if args.command in ('validate', 'stats'):
            print(json.dumps(summary(data['cases']), ensure_ascii=False, indent=2))
        elif args.command == 'export':
            filters = {k: getattr(args, k) for k in ('tasks', 'tags', 'any_tags', 'exclude_tags', 'intent', 'kind', 'split', 'clarification_role', 'ids', 'source')}
            doc = subset_document(data, filters)
            protected = {args.master.resolve(), DEFAULT_SUBSETS.resolve(), *(ROOT/s['file'] for s in data['sources'])}
            require(args.output.resolve() not in protected, 'Export cannot overwrite master or historical sources')
            require(args.output.with_suffix('.manifest.json').resolve() not in protected, 'Manifest path is protected')
            if args.format == 'canonical':
                write_json(args.output, doc)
            else:
                write_json(args.output, legacy_cases(doc['cases'], args.format))
                write_json(args.output.with_suffix('.manifest.json'), {k:v for k,v in doc.items() if k!='cases'} | {'format': args.format, 'case_ids': [c['id'] for c in doc['cases']]})
            print(json.dumps({'output': str(args.output), 'cases': doc['count']}, ensure_ascii=False))
        elif args.command == 'refresh':
            definitions = json.loads(args.definitions.read_text())
            require(definitions.get('schema_version') == 'voice-eval-subset-definitions-v1'
                    and isinstance(definitions.get('subsets'), dict) and definitions['subsets'], 'Invalid subset definitions')
            prepared = {name: subset_document(data, filters) for name, filters in definitions['subsets'].items()}
            protected = {args.master.resolve(), DEFAULT_SUBSETS.resolve(), args.definitions.resolve(),
                         *(ROOT/s['file'] for s in data['sources'])}
            for name in prepared:
                require(name and all(x.isalnum() or x in '_-' for x in name), 'Invalid subset filename')
                output = args.output_dir / (name+'.json')
                require(output.resolve() not in protected, 'Refresh cannot overwrite master or historical sources')
            for name, doc in prepared.items():
                write_json(args.output_dir/(name+'.json'), doc)
            print(json.dumps({name: doc['count'] for name, doc in prepared.items()}, ensure_ascii=False, indent=2))
        else:
            incoming = json.loads(args.input.read_text())
            if isinstance(incoming, dict):
                incoming = incoming.get('cases', [incoming])
            result = maintain(args.master, incoming, update=args.command=='update', note=args.note)
            print(json.dumps({'revision': result['revision'], 'cases': len(result['cases'])}))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
