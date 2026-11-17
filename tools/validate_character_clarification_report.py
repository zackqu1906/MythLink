"""Validate report records, local links, and the actual filter JavaScript offline."""
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.character_clarification_experiment import OUT,save,digest

class Parser(HTMLParser):
    def __init__(self):super().__init__();self.cards=[];self.links=[];self.selects=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='article' and attrs.get('class')=='case':self.cards.append({'dataset':{k[5:]:v for k,v in attrs.items() if k.startswith('data-')},'hidden':False})
        if tag=='a':self.links.append(attrs['href'])
        if tag=='select':self.selects.append(attrs.get('id'))

def main():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QJSEngine
    html=(OUT/'report.html').read_text();data=json.loads((OUT/'report.json').read_text());p=Parser();p.feed(html)
    assert len(p.cards)==len(data['cases']) and len({c['id'] for c in data['cases']})==len(data['cases'])
    assert all((OUT/href).is_file() for href in p.links if not href.startswith(('#','https:','http:')))
    assert set(p.selects)=={'result','group','split','mechanism','change'}
    assert all(s not in html for s in ('data-route=','data-tasks=','预期类别数量','分类不匹配'))
    for card,case in zip(p.cards,data['cases']):
        for key in ('group','split','mechanism','change'):assert card['dataset'][key]==case[key]
        assert case['review']['output_sha256']==digest(case['output'])
    app=QCoreApplication.instance() or QCoreApplication([]);engine=QJSEngine()
    setup='''const rows=ROWS,els={};for(const id of ['result','group','split','mechanism','change','search','reset','count','empty'])els[id]={value:id==='search'?'':'all',tagName:id==='search'?'INPUT':'SELECT',listeners:{},addEventListener(k,fn){this.listeners[k]=fn;}};
const document={querySelectorAll(){return rows;},getElementById(id){return els[id];}};const shown=()=>rows.filter(c=>!c.hidden).length;const assert=(x,m)=>{if(!x)throw Error(m);};
'''.replace('ROWS',json.dumps(p.cards,ensure_ascii=False))
    js,=re.findall(r'<script>([\s\S]*?)</script>',html)
    n=len(p.cards);checks=f'assert(shown()==={n},"all");'
    for key in ('group','split','mechanism','change'):
        for value,count in Counter(c[key] for c in data['cases']).items():
            checks+=f'els.reset.listeners.click();els[{json.dumps(key)}].value={json.dumps(value)};applyFilters();assert(shown()==={count},{json.dumps(key+value)});'
    for value,count in Counter(c['overall'] for c in data['cases']).items():
        checks+=f'els.reset.listeners.click();els.result.value={json.dumps(value)};applyFilters();assert(shown()==={count},{json.dumps(value)});'
    checks+=f'els.search.value="__no_such_case__";applyFilters();assert(shown()===0&&!els.empty.hidden,"empty");els.reset.listeners.click();assert(shown()==={n},"reset");'
    first=data['cases'][0];combination=sum(c['group']==first['group'] and c['mechanism']==first['mechanism'] for c in data['cases'])
    checks+=f'els.group.value={json.dumps(first["group"])};els.mechanism.value={json.dumps(first["mechanism"])};applyFilters();assert(shown()==={combination},"intersection");els.reset.listeners.click();'
    result=engine.evaluate(setup+js+checks);assert not result.isError(),result.toString()
    proof={'html_sha256':digest(html),'cards':n,'all_local_links_valid':len(p.links),'review_hashes_valid':True,
      'filter_engine':'Qt QJSEngine; actual report JavaScript and actual card attributes',
      'tested':['all rows','each round/variant','each dataset split','each clarification mechanism','each paired change','each result','combined filters','empty state','reset'],
      'visual_preview':'Not performed; file URL preview was denied by browser security policy in this chat. No workaround attempted.'}
    save(OUT/'validation.json',proof);print(json.dumps(proof,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
