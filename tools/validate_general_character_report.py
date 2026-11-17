"""Check paired coverage, input-first presentation, evidence, and real filter JS."""
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.general_character_experiment import OUT, SOURCE
from tools.character_clarification_experiment import save, digest


class Parser(HTMLParser):
    def __init__(self):
        super().__init__(); self.cards = []; self.links = []; self.selects = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'article' and attrs.get('class') == 'case':
            self.cards.append({'dataset': {k[5:]: v for k, v in attrs.items() if k.startswith('data-')}, 'hidden': False, 'id': attrs['id']})
        if tag == 'a': self.links.append(attrs['href'])
        if tag == 'select': self.selects.append(attrs.get('id'))


def validate():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QJSEngine
    from proximic_ring.text_processing.boundary_edit_prompt import BOUNDARY_EDIT_PROMPT
    html = (OUT / 'report.html').read_text()
    data = json.loads((OUT / 'report.json').read_text())
    pairs = json.loads((OUT / 'paired-results.json').read_text())
    freeze = json.loads((OUT / 'freeze.json').read_text())
    p = Parser(); p.feed(html)
    assert len(p.cards) == len(pairs) == 1038
    assert len(data['cases']) == 2076 and len({c['id'] for c in data['cases']}) == 2076
    assert len({c['id'] for c in p.cards}) == 1038
    assert all((OUT / href).is_file() for href in p.links if not href.startswith(('#', 'https:', 'http:')))
    assert set(p.selects) == {'result', 'split', 'mechanism', 'repeat'}
    assert all(s not in html for s in ('data-route=', 'data-tasks=', '预期类别数量', '分类不匹配'))
    consistent = {}
    for c in data['cases']:
        assert c['review']['output_sha256'] == digest(c['output'])
        assert c['review']['status'] != 'pending'
        key = (c['evidence']['input_sha256'], digest(c['output']))
        assert key not in consistent or consistent[key] == c['review']['status']
        consistent[key] = c['review']['status']
        if c['review']['status'] == 'fail': assert c['review']['issues']
    for card, pair in zip(p.cards, pairs):
        assert card['dataset']['change'] == pair['change']
        assert card['dataset']['repeat'] == str(pair['repeat'])
        assert card['dataset']['split'] == pair['case']['split']
        assert set(pair['results']) == {'v4d', 'general_char_v1'}
    for article in re.findall(r'<article class="case"[\s\S]*?</article>', html):
        source = article.index('可编辑原文</div>')
        utterance = article.index('本次口述</div>')
        result = article.index('comparison-heading')
        assert source < utterance < result
        assert article.count('class="result-block"') == 2
    assert digest((OUT / 'dataset.json').read_text()) == freeze['dataset_sha256']
    assert (OUT / 'dataset.json').read_bytes() == (SOURCE / 'dataset.json').read_bytes()
    assert (OUT / 'v4d.txt').read_text() == BOUNDARY_EDIT_PROMPT
    assert digest(BOUNDARY_EDIT_PROMPT) == freeze['baseline']['sha256']
    assert digest((OUT / 'general_char_v1.txt').read_text()) == freeze['candidate']['sha256']
    assert not json.loads((OUT / 'paired/run-status.json').read_text())['source_drift']
    app = QCoreApplication.instance() or QCoreApplication([])
    engine = QJSEngine()
    setup = '''const rows=ROWS,els={};for(const id of ['result','split','mechanism','repeat','search','reset','count','empty'])els[id]={value:id==='search'?'':'all',tagName:id==='search'?'INPUT':'SELECT',listeners:{},addEventListener(k,fn){this.listeners[k]=fn;}};
const document={querySelectorAll(){return rows;},getElementById(id){return els[id];}};const shown=()=>rows.filter(c=>!c.hidden).length;const assert=(x,m)=>{if(!x)throw Error(m);};
'''.replace('ROWS', json.dumps(p.cards, ensure_ascii=False))
    js, = re.findall(r'<script>([\s\S]*?)</script>', html)
    checks = 'assert(shown()===1038,"all");'
    for key in ('split', 'mechanism', 'repeat'):
        for value, count in Counter(c['dataset'][key] for c in p.cards).items():
            checks += f'els.reset.listeners.click();els[{json.dumps(key)}].value={json.dumps(value)};applyFilters();assert(shown()==={count},{json.dumps(key+value)});'
    filters = {'any_fail': lambda d: d['base'] == 'fail' or d['new'] == 'fail',
               'new_fail': lambda d: d['new'] == 'fail', 'base_fail': lambda d: d['base'] == 'fail'}
    for value in dict.fromkeys(c['dataset']['change'] for c in p.cards):
        filters[value] = lambda d, v=value: d['change'] == v
    for value, pred in filters.items():
        count = sum(pred(c['dataset']) for c in p.cards)
        checks += f'els.reset.listeners.click();els.result.value={json.dumps(value)};applyFilters();assert(shown()==={count},{json.dumps(value)});'
    count = sum(c['dataset']['split'] == 'development' and c['dataset']['repeat'] == '1' for c in p.cards)
    checks += f'els.reset.listeners.click();els.split.value="development";els.repeat.value="1";applyFilters();assert(shown()==={count},"intersection");'
    checks += 'els.search.value="__no_such_case__";applyFilters();assert(shown()===0&&!els.empty.hidden,"empty");els.reset.listeners.click();assert(shown()===1038,"reset");'
    result = engine.evaluate(setup+js+checks)
    assert not result.isError(), result.toString()
    proof = {'paired_cards': len(p.cards), 'generation_records': len(data['cases']),
             'input_first_on_every_card': True, 'all_local_links_valid': len(p.links),
             'review_hashes_and_consistency_valid': True, 'baseline_source_unchanged': True,
             'dataset_unchanged': True, 'generation_source_drift': [],
             'filters_tested': ['all', 'each result', 'each split', 'each mechanism', 'each repeat', 'intersection', 'empty', 'reset'],
             'filter_engine': 'Qt QJSEngine, actual report JavaScript and card attributes',
             'html_sha256': digest(html),
             'visual_preview': 'Not performed. Content, source order, and filter logic validated; no browser-policy workaround.'}
    save(OUT / 'validation.json', proof)
    print(json.dumps(proof, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    validate()
