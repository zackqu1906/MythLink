#!/usr/bin/env python3
"""Offline report integrity and executable filter checks, with no browser/network."""
import argparse
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.check_general_edit_ablation import save,sha

class Parser(HTMLParser):
    def __init__(self):
        super().__init__();self.cards=[];self.links=[];self.selects=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='article' and a.get('class')=='case':
            self.cards.append({'dataset':{k[5:]:v for k,v in a.items() if k.startswith('data-')},'hidden':False})
        if tag=='a':self.links.append(a['href'])
        if tag=='select':self.selects.append(a.get('id'))

def main(out):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QJSEngine
    html=(out/'report.html').read_text();data=json.loads((out/'report.json').read_text())
    manifest=json.loads((out/'manifest.json').read_text())
    p=Parser();p.feed(html)
    assert len(p.cards)==manifest['planned_requests']==len(data['cases'])
    assert len({c['id'] for c in data['cases']})==len(data['cases'])
    assert all((out/href).is_file() for href in p.links if not href.startswith(('#','http:','https:')))
    assert {'result','group','missing','change','repeat'}.issubset(p.selects)
    assert 'tasks' not in p.selects
    for forbidden in ('分类不匹配','预期类别数量','data-route=','data-tasks='):
        assert forbidden not in html
    for card,c in zip(p.cards,data['cases']):
        assert card['dataset']['group']==c['group']
        assert card['dataset']['missing']==','.join(c['missing'])
        assert card['dataset']['repeat']==str(c['repeat'])
    counts=Counter()
    for c in data['cases']:
        for missing in c['missing']:counts[missing]+=1
    app=QCoreApplication.instance() or QCoreApplication([])
    engine=QJSEngine()
    setup='''const rows=ROWS;const els={};for(const id of ['result','group','missing','change','repeat','search','reset','count','empty'])els[id]={value:id==='search'?'':'all',tagName:id==='search'?'INPUT':'SELECT',listeners:{},addEventListener(k,fn){this.listeners[k]=fn;}};const document={querySelectorAll(){return rows;},getElementById(id){return els[id];}};
const assert=(v,m)=>{if(!v)throw Error(m);};const shown=()=>rows.filter(c=>!c.hidden).length;
'''.replace('ROWS',json.dumps(p.cards,ensure_ascii=False))
    js=re.findall(r'<script>(.*?)</script>',html,re.S)
    assert len(js)==1
    checks=f"assert(shown()==={len(data['cases'])},'all');\n"
    for key,num in counts.items():
        checks+=f"els.missing.value='{key}';els.missing.listeners.change();assert(shown()==={num},'missing {key}');\n"
    checks+="els.reset.listeners.click();assert(shown()===1848,'reset');els.repeat.value='main';applyFilters();assert(shown()===1512,'first run');\n"
    checks+="els.group.value='general_no_spelling_v1';applyFilters();assert(shown()===216,'exact arm');els.missing.value='polish';applyFilters();assert(shown()===0&&!els.empty.hidden,'intersection empty');\n"
    checks+="els.reset.listeners.click();els.group.value='general_only_v1';els.missing.value='spelling';els.repeat.value='main';applyFilters();assert(shown()===216,'general contains all missing');\n"
    checks+="els.search.value='__nonexistent_test__';els.search.listeners.input();assert(shown()===0&&!els.empty.hidden,'search empty');els.reset.listeners.click();assert(shown()===1848,'reset all');\n"
    expected_worse=sum(c['change']=='worse' for c in data['cases'])
    expected_fail=sum(c['overall']=='fail' for c in data['cases'])
    checks+=f"els.change.value='worse';applyFilters();assert(shown()==={expected_worse},'paired worse');els.reset.listeners.click();els.result.value='fail';applyFilters();assert(shown()==={expected_fail},'failure filter');els.reset.listeners.click();"
    result=engine.evaluate(setup+js[0]+checks)
    assert not result.isError(),result.toString()
    proof={'html_sha256':sha(out/'report.html'),'cases':len(p.cards),'unique_ids':len(data['cases']),
           'link_count':len(p.links),'all_local_links_exist':True,'missing_rule_filter_counts':dict(counts),
           'checks':['all rows','individual missing rule includes cumulative arms','exact missing combination',
                     'first run vs repeats','intersection empty state','search','result','paired worse','reset'],
           'filter_engine':'Qt QJSEngine with actual report script and HTML card attributes',
           'visual_preview':'Not performed: browser URL security policy disallowed file://. No browser workaround attempted.'}
    save(out/'validation.json',proof);print(json.dumps(proof,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output_dir',type=Path)
    main(p.parse_args().output_dir)
