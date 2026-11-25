"""Local-only review of the project's HTML evaluation reports. No model calls."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import quote, unquote, urlsplit

if __package__:
    from .voice_eval_dataset import fingerprint, validate_master
    from .eval_report import review_run_key
else:
    from voice_eval_dataset import fingerprint, validate_master
    from eval_report import review_run_key

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'docs/reports'
DATA = ROOT / 'tools/data/real_user_edits_20261123/dataset.json'
PORT = 18761
REVIEW_WRITE_LOCK = Lock()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def atomic(path, content):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as out:
        try:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            os.unlink(out.name)
            raise
    try:
        os.replace(out.name, path)
    finally:
        Path(out.name).unlink(missing_ok=True)


def save(path, data):
    atomic(path, json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def input_key(case):
    stored = case.get('evidence', {}).get('input_sha256')
    return stored or fingerprint([case['instruction'], case['source_text'], case.get('context')])


def normalized(html):
    html = html.resolve()
    candidates = [html.with_suffix('.report.json'), html.with_suffix('.json')]
    if html.name == 'index.html':
        candidates = [html.parent/'human-reviewed-report.json', html.parent/'report.json'] + candidates
    for file in candidates:
        if file.is_file() and file.resolve().parent == html.parent:
            doc = read(file)
            if doc.get('schema_version') == 1 and isinstance(doc.get('cases'), list):
                return doc
    raise ValueError('找不到这份报告配套的逐题数据。')


def report_path(path):
    file = (REPORTS / path.lstrip('/')).resolve()
    if not file.is_relative_to(REPORTS) or file.suffix != '.html' or not file.is_file():
        raise ValueError('报告不存在。')
    return file


def check_case(doc, entry, *, allow_error=False):
    if entry.get('run') != review_run_key(doc):
        raise ValueError('这份报告的模型输出已变，请刷新后重试。')
    case = next((c for c in doc['cases'] if c['id'] == entry['id']), None)
    if not case or case.get('observation') or (case.get('error') and not allow_error):
        raise ValueError('这条记录没有参加正文评分。')
    digest = hashlib.sha256(case['output'].encode()).hexdigest()
    if digest != case['review']['output_sha256'] or digest != entry['output_sha256'] or input_key(case) != entry['input_sha256']:
        raise ValueError('题目或输出已经改变，请刷新报告。')
    return case


def update_browser_index(directory, case=None, *, removed=(), remaining=None):
    page = directory/'index.html'
    if not page.is_file():
        return
    html = page.read_text()
    pattern = r'(<script id="records" type="application/json">)(.*?)(</script>)'
    def update(match):
        rows = json.loads(match[2])
        if case is not None:
            e = case['editing_expectations'][0]
            for row in rows:
                if row['id'] == case['id']:
                    row.update(t=case['input']['target'], u=case['input']['utterances'][0],
                               ref=e['reference_text'], req=e['requirements'], alts=e.get('accepted_alternatives', []))
                    break
        if removed:
            rows = [r for r in rows if r['status'] != 'ready' or r['id'] not in removed]
        data = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
        return match[1]+data+match[3]
    html, n = re.subn(pattern, update, html, count=1, flags=re.S)
    if not n:
        raise ValueError('题库浏览页缺少样例列表。')
    if remaining is not None:
        html = re.sub(r'\d+ 道正式题', f'{remaining} 道正式题', html, count=1)
    atomic(page, html)


def linked_input(data, case):
    current = next((c for c in data['cases'] if c['id'] == case['id']), None)
    if current and current['kind'] == 'single':
        previous = {a.get('before_input_sha256') for a in current['annotation']['input_amendments']}
        if input_key(case) in previous | {current['input_sha256']}:
            return current
    raise ValueError('当前题库里没有与这次输入对应的单条样例。')


def input_values(current):
    return {'editable_text':current['input']['target'], 'utterance':current['input']['utterances'][0],
            'version':current['input_sha256']}


def removed_entry(data, case):
    return next((e for e in reversed(data['change_log']) if e.get('action') == 'remove_case'
                 and e['case_id'] == case['id'] and input_key(case) in e['input_versions']), None)


def sync_removed_views(file, data):
    if not data.get('removal_views_pending'):
        return
    directory = file.parent
    removed = {e['case_id'] for e in data['change_log'] if e.get('action') == 'remove_case'}
    cases = data['cases']
    update_browser_index(directory, removed=removed, remaining=len(cases))
    path = directory/'selection_audit.json'
    if path.is_file():
        audit = read(path)
        audit.update(ready_cases=len(cases), ready_occurrences=sum(len(c['provenance']) for c in cases),
                     removed_by_user=sorted(set(audit.get('removed_by_user', [])) | removed))
        save(path, audit)
    path = directory/'local_merge_audit.json'
    if path.is_file():
        audit = read(path)
        counts = Counter()
        for record in audit['records_by_id'].values():
            for occ in record.get('occurrences', []):
                if occ['case_id'] in removed:
                    occ['disposition'] = 'removed_by_user'
                counts[occ['disposition']] += 1
        audit['occurrences'] = dict(counts)
        save(path, audit)
    del data['removal_views_pending']
    save(file, data)


def remove_case(file, case, item, report):
    data = read(file)
    previous = removed_entry(data, case)
    preview = item['action'] == 'prepare_delete'
    if previous:
        if not preview and item.get('version') != previous['version']:
            raise ValueError('删除确认已过期，请重新打开样例。')
        sync_removed_views(file, data)
        return {'removed': True}
    current = linked_input(data, case)
    version = fingerprint(current)
    if preview:
        return dict(id=current['id'], utterance=current['input']['utterances'][0], version=version)
    if item['action'] != 'delete_case' or item.get('version') != version:
        raise ValueError('题目已更新，请重新核对题号和口述后再删除。')
    cid = current['id']
    versions = {current['input_sha256'], *(a['before_input_sha256'] for a in current['annotation']['input_amendments'])}
    now = datetime.now(timezone.utc).isoformat()
    data['change_log'].append(dict(action='remove_case', revision=data['revision'], at=now,
        case_id=cid, version=version, input_versions=sorted(versions), report=report, run=item['run'],
        occurrence_keys=[p.get('occurrence_key', p['case_id']) for p in current['provenance']]))
    data['updated_at'] = now[:10]
    data['removal_views_pending'] = True
    data['cases'].remove(current)
    for other in data['cases']:
        related = other['relations']['related_case_ids']
        if cid in related:
            related.remove(cid)
    save(file, validate_master(data))
    sync_removed_views(file, data)
    return {'removed': True}


def edit_input(file, case, entry, report):
    field = entry.get('field')
    value = entry.get('value')
    if field not in ('editable_text','utterance') or not isinstance(value,str):
        raise ValueError('请选择原文或口述。')
    if field == 'utterance' and not value.strip():
        raise ValueError('口述不能为空。')
    data = read(file)
    current = linked_input(data,case)
    before = input_values(current)
    if before[field] == value:
        update_browser_index(file.parent,current)
        return before
    if entry.get('version') != before['version']:
        raise ValueError('题库输入已改变，请重新打开编辑框。')
    inp = current['input']
    if field == 'utterance':
        inp['utterances'][0] = value
    else:
        inp['target'] = value
    current['input_sha256'] = fingerprint(inp)
    annotation = current['annotation']
    now = datetime.now(timezone.utc).isoformat()
    amendment = dict(field=field, before=before[field], after=value, source='user_correction',
        before_input_sha256=before['version'],after_input_sha256=current['input_sha256'],date=now, report=report)
    # Retain past output approvals with the precise input to which they applied.
    old_alternatives = current['editing_expectations'][0].pop('accepted_alternatives', [])
    if old_alternatives:
        amendment['previous_accepted_alternatives'] = old_alternatives
    if 'semantic_review' in annotation:
        amendment['previous_semantic_review'] = annotation.pop('semantic_review')
    if 'grading_confirmations' in annotation:
        amendment['previous_grading_confirmations'] = annotation.pop('grading_confirmations')
    annotation['input_amendments'].append(amendment)
    data['updated_at'] = now[:10]
    data['change_log'].append(dict(revision=data['revision'],at=now,case_id=current['id'],field=field,
                                   reason='报告内人工修订输入', input_sha256=current['input_sha256']))
    save(file,validate_master(data))
    update_browser_index(file.parent,current)
    return input_values(current)


def write_rule(file, case, entry, report, now):
    data = read(file)
    current = linked_input(data, case)
    if entry.get('version',entry['input_sha256']) != current['input_sha256']:
        raise ValueError('题库输入已改变，请重新打开标准。')
    same_input = current['input_sha256'] == input_key(case)
    expectation = current['editing_expectations'][0]
    lines = entry['requirements']
    if not isinstance(lines, list) or not lines or not all(isinstance(line, str) and line.strip() for line in lines):
        raise ValueError('请写明本题可接受的标准。')
    lines = list(dict.fromkeys(line.strip() for line in lines))
    accepted = expectation.get('accepted_alternatives', [])
    matches = case['output'] == expectation['reference_text'] or case['output'] in accepted
    previous = entry.get('contract_sha256')
    confirmation = dict(report=report, run=entry['run'], previous_contract_sha256=previous,
                        input_sha256=current['input_sha256'], requirements=lines)
    if same_input:
        confirmation['output_sha256'] = entry['output_sha256']
    a = current['annotation']
    # A retry can arrive after the corpus was committed but before the report receipt was saved.
    last = (a.get('grading_confirmations') or [{}])[-1]
    replay = (lines == expectation['requirements'] and
              all(last.get(k) == v for k, v in confirmation.items()))
    if not replay:
        if previous != fingerprint(expectation):
            raise ValueError('本题标准已被更新，请重新打开标准后再保存。')
        if set(lines) == {line.strip() for line in expectation['requirements']}:
            raise ValueError('请先修改本题验收标准，再保存。')
        expectation['requirements'] = lines
        if same_input and not matches:
            expectation['accepted_alternatives'] = [*accepted,case['output']]
        if 'semantic_review' in a:
            a.setdefault('prior_semantic_reviews', []).append(a.pop('semantic_review'))
        a['user_confirmed'] = True
        a.setdefault('grading_confirmations', []).append(dict(confirmation, at=now, source='报告内人工审阅'))
        data['updated_at'] = now[:10]
        data['change_log'].append(dict(revision=data['revision'], at=now, case_id=case['id'], reason='报告内人工更新本题验收标准'))
        save(file, validate_master(data))
    update_browser_index(file.parent, current)
    return lines, same_input


def record(html, item, dataset=DATA):
    if item.get('action') != 'rule':
        raise ValueError('请通过「更新标准并判对」修改并保存本题标准。')
    doc = normalized(html)
    case = check_case(doc, item)
    path = html.with_suffix('.human-reviews.json')
    records = read(path) if path.exists() else {}
    key = fingerprint([item['run'],item['id'],item['input_sha256'],item['output_sha256']])
    now = datetime.now(timezone.utc).isoformat()
    entry = records.get(key) or {k:item[k] for k in ('run','id','input_sha256','output_sha256')}
    lines, same_input = write_rule(dataset,case,item,html.parent.name+'/'+html.name,now)
    if not same_input:
        return {'rule_only':True, 'requirements':lines}
    entry['requirements'] = lines
    entry.setdefault('accepted_at',now)
    records[key] = entry
    save(path, records)
    return entry


def reconcile_rule(html, item, dataset):
    """Update the next run's rubric without issuing a report verdict. Caller holds the write lock."""
    case = check_case(normalized(html), item)
    data = read(dataset)
    current = linked_input(data, case)
    if current['input_sha256'] != item['input_sha256']:
        raise ValueError('该题输入已修订，请基于新输入重新核对。')
    exp = current['editing_expectations'][0]
    original = {'scoring_rule': current['scoring_rule'], **exp}
    changes = item['changes']
    editable = {'requirements', 'checks', 'reference_text', 'scoring_rule'}
    basis = item.get('basis')
    if not isinstance(changes, dict) or not changes.keys() <= editable:
        raise ValueError('请仅提交本题需要修订的验收字段。')
    if basis == 'human_review':
        key = fingerprint([item[k] for k in ('run','id','input_sha256','output_sha256')])
        ledger = html.with_suffix('.human-reviews.json')
        receipt = read(ledger).get(key) if ledger.exists() else None
        if not receipt or any(receipt.get(k) != item[k] for k in ('run','id','input_sha256','output_sha256')):
            raise ValueError('无法核对这次输出的人工判对记录。')
        receipt_sha = fingerprint(receipt)
        if item.get('receipt_sha256') != receipt_sha:
            raise ValueError('人工标注已更新，请重新核对。')
    elif basis != 'user_policy' or changes.keys() != {'requirements'}:
        raise ValueError('同类题仅按用户给定的通用口径调整文字标准。')
    result = {**original, **changes}
    mode, ref = result['scoring_rule'], result['reference_text']
    if (mode not in ('clear','semantic','targeted','rubric_only') or
            (mode == 'rubric_only') != (ref is None) or (mode == 'clear' and ref != '')):
        raise ValueError('评分方式与参考正文不符。')
    if basis == 'human_review' and case['output'] != ref and case['output'] not in exp.get('accepted_alternatives', []):
        result['accepted_alternatives'] = [*exp.get('accepted_alternatives', []), case['output']]
    if mode == 'clear' and any(result.get('accepted_alternatives', [])):
        raise ValueError('清空题与已认可的非空正文冲突，请核对删除范围。')
    changed = {k:v for k,v in result.items() if v != original.get(k)}
    acknowledge = basis == 'human_review' and not any(
        e.get('receipt_key') == key and e.get('receipt_sha256') == receipt_sha for e in data['change_log'])
    if changed or acknowledge:
        if item.get('contract_sha256') != fingerprint(original):
            raise ValueError('标准在复核期间发生变更，请重新读取后再合并。')
        current['scoring_rule'] = mode
        exp.update({k:v for k,v in changed.items() if k != 'scoring_rule'})
        annotation = current['annotation']
        if changed and 'semantic_review' in annotation:
            annotation.setdefault('prior_semantic_reviews', []).append(annotation.pop('semantic_review'))
        now = datetime.now(timezone.utc).isoformat()
        event = dict(revision=data['revision'], at=now, case_id=current['id'], action='reconcile_rule',
                     basis=basis, input_sha256=current['input_sha256'], report=html.parent.name+'/'+html.name,
                     previous={k:original.get(k) for k in changed}, updated=changed)
        if basis == 'human_review':
            event.update(receipt_key=key, receipt_sha256=receipt_sha, run=item['run'], output_sha256=item['output_sha256'])
        data['updated_at'] = now[:10]
        data['change_log'].append(event)
        save(dataset, validate_master(data))
    update_browser_index(dataset.parent, current)
    return {'updated':bool(changed), 'id':current['id']}


class Handler(BaseHTTPRequestHandler):
    timeout = 30

    def reply(self, code, value, mime='application/json; charset=utf-8'):
        payload = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Cross-Origin-Resource-Policy','same-origin')
        self.end_headers()
        self.wfile.write(payload)

    def dispatch(self, post=False):
        host = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != host or (post and self.headers.get('Origin') != 'http://'+host):
            return self.reply(403, {'error':'仅允许本机审阅页操作。'})
        path = unquote(urlsplit(self.path).path)
        if path == '/api/review' and post:
            size = int(self.headers.get('Content-Length','0'))
            if self.headers.get_content_type() != 'application/json' or not 0 < size <= 65536:
                return self.reply(400, {'error':'审阅格式无效。'})
            item = json.loads(self.rfile.read(size))
            html = report_path(item['report'])
            if item['action'] == 'load':
                path = html.with_suffix('.human-reviews.json')
                return self.reply(200, list(read(path).values()) if path.exists() else [])
            if item['action'] == 'input_changes':
                doc = normalized(html)
                if item.get('run') != review_run_key(doc):
                    raise ValueError('报告已更新，请刷新。')
                with REVIEW_WRITE_LOCK:
                    data = read(DATA)
                    sync_removed_views(DATA, data)
                changed = {}
                for case in doc['cases']:
                    try:
                        current = linked_input(data,case)
                    except ValueError:
                        changed[case['id']] = {'removed': True} if removed_entry(data, case) else {'unlinked': True}
                        continue
                    if current['input_sha256'] != input_key(case):
                        changed[case['id']] = input_values(current)
                return self.reply(200, changed)
            if item['action'] in ('prepare_delete', 'delete_case'):
                case = check_case(normalized(html), item, allow_error=True)
                with REVIEW_WRITE_LOCK:
                    result = remove_case(DATA, case, item, str(html.relative_to(REPORTS)))
                return self.reply(200, result)
            if item['action'] in ('get_input','save_input'):
                case = check_case(normalized(html),item)
                if item['action'] == 'get_input':
                    return self.reply(200,input_values(linked_input(read(DATA),case)))
                with REVIEW_WRITE_LOCK:
                    result = edit_input(DATA,case,item,str(html.relative_to(REPORTS)))
                return self.reply(200,result)
            if item['action'] == 'get_rule':
                case = check_case(normalized(html), item)
                current = linked_input(read(DATA),case)
                exp = current['editing_expectations'][0]
                return self.reply(200,dict(requirements=exp['requirements'], contract_sha256=fingerprint(exp),
                    version=current['input_sha256'],same_input=current['input_sha256']==input_key(case)))
            if item['action'] == 'reconcile_rule':
                with REVIEW_WRITE_LOCK:
                    result = reconcile_rule(html, item, DATA)
                return self.reply(200, result)
            with REVIEW_WRITE_LOCK:
                decision = record(html,item)
            return self.reply(200, decision)
        if post:
            return self.reply(404, {})
        file = (REPORTS/path.lstrip('/')).resolve()
        if not file.is_relative_to(REPORTS) or file.suffix not in ('.html','.json','.jsonl','.txt','.md') or not file.is_file():
            return self.reply(404, {})
        mime = 'text/html' if file.suffix == '.html' else 'text/plain'
        return self.reply(200, file.read_bytes(), mime+'; charset=utf-8')

    def handle_request(self, post):
        try:
            self.dispatch(post)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.reply(400, {'error': str(exc)})

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open', action='store_true', help='open the most recently generated report')
    args = parser.parse_args()
    import webbrowser
    from urllib.request import urlopen
    url = f'http://127.0.0.1:{PORT}'
    latest = max((p for p in REPORTS.glob('*/index.html') if (p.parent/'report.json').is_file()), key=lambda p:p.stat().st_mtime, default=None)
    if args.open and latest:
        url += '/'+quote(str(latest.relative_to(REPORTS)))
    try:
        server = ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    except OSError:
        if args.open and latest and urlopen(url,timeout=2).status == 200:
            webbrowser.open(url)
        else:
            raise
    else:
        if args.open:
            webbrowser.open(url)
        server.serve_forever()
