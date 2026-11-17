"""Verify complete evidence, paired inputs and the report's actual filter script."""
from collections import Counter
import json
import re
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.compare_astra_v4d import OUT, save
from tools.character_clarification_experiment import digest
from tools.validate_character_clarification_report import Parser


def main(interim=False):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QJSEngine
    prefix = 'current-report' if interim else 'report'
    html = (OUT/(prefix+'.html')).read_text()
    data = json.loads((OUT/(prefix+'.json')).read_text())
    m = json.loads((OUT/'manifest.json').read_text())
    rows = [json.loads(s) for s in (OUT/'generations.jsonl').read_text().splitlines()]
    by_key = {(r['case_id'],r['repeat'],r['variant']):r for r in rows}
    text_pairs = 0
    for b in m['blocks']:
        if interim and any((b['case_id'],b['repeat'],v) not in by_key for v in ['deepseek','astra_low']):
            continue
        a = by_key[(b['case_id'],b['repeat'],'deepseek')]
        z = by_key[(b['case_id'],b['repeat'],'astra_low')]
        assert a['model_input'] == z['model_input']
        if not a.get('error') and not z.get('error'):
            assert a['calls'][0]['request']['input'][1]['content'][0]['text'] == z['turn_request']['input'][0]['text']
            assert a['calls'][0]['request']['input'][0]['content'][0]['text'] == z['session_request']['baseInstructions']
            text_pairs += 1
    parser = Parser()
    parser.feed(html)
    assert len(parser.cards) == len(data['cases']) == len(rows)
    assert len(rows) <= m['planned_requests'] if interim else len(rows) == m['planned_requests']
    assert len({c['id'] for c in data['cases']}) == len(rows)
    assert set(parser.selects) == {'result','group','split','mechanism','change','phase','repeat'}
    for href in parser.links:
        if not href.startswith(('#','https:','http:')):
            assert (OUT/href).is_file(), href
    assert all(s not in html for s in ['data-route=','预期类别数量','分类不匹配'])
    for card,c in zip(parser.cards,data['cases']):
        for key in ['group','split','mechanism','change','phase','repeat']:
            assert card['dataset'][key] == c[key]
        assert c['review']['output_sha256'] == digest(c['output'])
        if not c.get('error'):
            assert c['review']['status'] != 'pending'
    if interim:
        audit = json.loads((OUT/'audit-candidates.json').read_text())
        manual = json.loads((OUT/'codex-new-output-reviews.json').read_text())
        assert audit['generations_sha256'] == digest((OUT/'generations.jsonl').read_text())
        assert all('historical_review' in c or c['candidate_id'] in manual for c in audit['candidates'])
        assert data['metadata']['provisional'] and not data['metadata']['new_blind_review_performed']
    else:
        audit = json.loads((OUT/'audit-overrides.json').read_text())
        assert not audit['new_outputs_without_codex_review']
    app = QCoreApplication.instance() or QCoreApplication([])
    engine = QJSEngine()
    setup = '''const rows=ROWS,els={};for(const id of ['result','group','split','mechanism','change','phase','repeat','search','reset','count','empty'])els[id]={value:id==='search'?'':'all',tagName:id==='search'?'INPUT':'SELECT',listeners:{},addEventListener(k,fn){this.listeners[k]=fn;}};
const document={querySelectorAll(){return rows;},getElementById(id){return els[id];}};const shown=()=>rows.filter(c=>!c.hidden).length;const assert=(x,m)=>{if(!x)throw Error(m);};
'''.replace('ROWS',json.dumps(parser.cards,ensure_ascii=False))
    js, = re.findall(r'<script>([\s\S]*?)</script>',html)
    checks = f'assert(shown()==={len(rows)},"all");'
    for key in ['group','split','mechanism','change','phase','repeat']:
        for value,count in Counter(c[key] for c in data['cases']).items():
            checks += f'els.reset.listeners.click();els[{json.dumps(key)}].value={json.dumps(value)};applyFilters();assert(shown()==={count},{json.dumps(key+value)});'
    for value,count in Counter(c['overall'] for c in data['cases']).items():
        checks += f'els.reset.listeners.click();els.result.value={json.dumps(value)};applyFilters();assert(shown()==={count},{json.dumps(value)});'
    checks += f'els.search.value="__no_such_case__";applyFilters();assert(shown()===0&&!els.empty.hidden,"empty");els.reset.listeners.click();assert(shown()==={len(rows)},"reset");'
    first = data['cases'][0]
    n = sum(c['group']==first['group'] and c['mechanism']==first['mechanism'] for c in data['cases'])
    checks += f'els.group.value={json.dumps(first["group"])};els.mechanism.value={json.dumps(first["mechanism"])};applyFilters();assert(shown()==={n},"intersection");'
    result = engine.evaluate(setup+js+checks)
    assert not result.isError(), result.toString()
    proof = {'cards':len(rows), 'identical_prompt_and_user_text_pairs':text_pairs,
             'review_hashes_valid':True, 'all_new_outputs_codex_reviewed':True,
             'all_local_links_valid':True, 'html_sha256':digest(html),
             'filter_engine':'Qt QJSEngine, actual report JS and card attributes',
             'filter_checks':['every model','every split','every mechanism','every paired change',
                              'every phase','every repeat','every result','intersection','empty state','reset'],
             'visual_preview':'Not performed; validation covers content, links, hashes and filter behavior.'}
    proof['interim'] = interim
    save(OUT/('current-validation.json' if interim else 'validation.json'),proof)
    print(json.dumps(proof,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main(interim='--interim' in sys.argv)
