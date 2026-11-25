"""List human acceptances or apply reviewed rubric changes through the local review service."""
import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

if __package__:
    from . import eval_human_review as review
else:
    import eval_human_review as review


def annotations():
    data = review.read(review.DATA)
    cases = {c['id']:c for c in data['cases']}
    done = {(e['receipt_key'], e['receipt_sha256']) for e in data['change_log'] if 'receipt_sha256' in e}
    for ledger in sorted(review.REPORTS.rglob('*.human-reviews.json')):
        html = ledger.with_suffix('').with_suffix('.html')
        doc = review.normalized(html)
        for key, item in review.read(ledger).items():
            row = {k:item[k] for k in ('id','run','input_sha256','output_sha256')}
            row.update(report=str(html.relative_to(review.REPORTS)), receipt_sha256=review.fingerprint(item),
                       basis='human_review')
            if key != review.fingerprint([row[k] for k in ('run','id','input_sha256','output_sha256')]):
                raise ValueError(f'{ledger}: 人工记录标识不匹配')
            frozen = review.check_case(doc, item)
            current = cases.get(row['id'])
            if not current or current['input_sha256'] != row['input_sha256']:
                yield {**row, 'state':'已删除或输入已变'}
                continue
            exp = current['editing_expectations'][0]
            row.update(contract_sha256=review.fingerprint({'scoring_rule':current['scoring_rule'], **exp}),
                       state='已整理' if (key,row['receipt_sha256']) in done else '待整理',
                       original=current['input'], accepted_output=frozen['output'],
                       initial_note=frozen['review']['note'], annotation=item.get('requirements') or item.get('rule'),
                       current_rule=exp)
            yield row


def apply(plan):
    origin = f'http://127.0.0.1:{review.PORT}'
    client = build_opener(ProxyHandler({}))
    for entry in plan:
        payload = {**entry, 'action':'reconcile_rule'}
        request = Request(origin+'/api/review', data=json.dumps(payload, ensure_ascii=False).encode(),
                          headers={'Content-Type':'application/json', 'Origin':origin})
        try:
            with client.open(request,timeout=30) as response:
                print(response.read().decode(), flush=True)
        except HTTPError as exc:
            raise ValueError(f"{entry['id']}: {json.load(exc).get('error',exc.reason)}") from exc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', type=Path, metavar='PLAN', help='将逐题核对过的 JSON 数组写回题库')
    args = parser.parse_args()
    if args.apply:
        apply(review.read(args.apply))
    else:
        for row in annotations():
            print(json.dumps(row, ensure_ascii=False))
