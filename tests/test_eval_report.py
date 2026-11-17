"""Reporting invariants: evidence binding, truthful denominators and real filters."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.eval_report import JS, metrics, render, validate


def sample():
    cases=[]
    for i,status in enumerate(('pass','fail','uncertain','pending','observation','pass'),1):
        output='正文 <script>not executable</script>'
        cases.append({'id':str(i),'group':'test','instruction':'编辑','source_text':'原文',
            'output':output,'expected_result':'完成指定目标','expected_labels':['a'],'actual_labels':['b'] if i==6 else ['a'],
            'observation':status=='observation','latency_s':i/10,
            'review':{'status':status,'note':'具体复核说明',
                'output_sha256':hashlib.sha256(output.encode()).hexdigest(),
                'issues':[{'reason':'遗漏条件','expected':'保留条件','actual':'条件消失'}] if status=='fail' else []}})
    return {'schema_version':1,'title':'测试','labels':{'a':'甲','b':'乙'},'cases':cases,'summary':['样例特征']}


def test_observations_unreviewed_and_label_failures_remain_distinct():
    data=validate(sample());m=metrics(data['cases'])
    assert m['scored']==5 and m['observation']==1
    assert m['overall']=={'pass':1,'fail':2,'uncertain':1,'pending':1}
    assert m['route_pass']==4 and m['body']['pass']==2


@pytest.mark.parametrize('mutation', ['duplicate','changed_output','empty_failure','unknown_label'])
def test_inconsistent_evidence_is_rejected(mutation):
    data=sample()
    if mutation=='duplicate':data['cases'].append(copy.deepcopy(data['cases'][0]))
    elif mutation=='changed_output':data['cases'][0]['output']='changed'
    elif mutation=='empty_failure':data['cases'][1]['review']['issues']=[]
    else:data['cases'][0]['actual_labels']=['unknown']
    with pytest.raises(ValueError):validate(data)


def test_generated_report_escapes_evidence_and_rejects_executable_links():
    data=sample();page,_=render(data)
    assert '<script>not executable</script>' not in page
    assert '&lt;script&gt;not executable&lt;/script&gt;' in page
    assert page.count('<article class="case"')==6
    data['links']=[{'label':'bad','href':'javascript:alert(1)'}]
    with pytest.raises(ValueError):render(data)


def test_filter_controls_use_same_case_status_and_can_reset(tmp_path):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QJSEngine
    app=QCoreApplication.instance() or QCoreApplication([])
    engine=QJSEngine()
    data=validate(sample())
    rows=[{'dataset':{'status':c['overall'],'group':'test','tasks':'2' if c['id']=='6' else '1',
            'route':str(c['route_match']).lower(),'body':c['review']['status'],'mechanical':'true' if c['id']=='1' else 'false','search':'case '+c['id']},
            'textContent':'case '+c['id'],'hidden':False} for c in data['cases']]
    setup='''const assert={equal(a,b){if(a!==b)throw new Error(String(a)+' != '+String(b));},deepEqual(a,b){if(JSON.stringify(a)!==JSON.stringify(b))throw new Error(JSON.stringify(a)+' != '+JSON.stringify(b));}};
const rows=ROWS;
const els={};for(const id of ['result','group','tasks','search','reset','count','empty'])els[id]={value:id==='search'?'':'all',tagName:id==='search'?'INPUT':'SELECT',listeners:{},addEventListener(k,fn){this.listeners[k]=fn;}};
const document={querySelectorAll(){return rows;},getElementById(id){return els[id];}};
'''.replace('ROWS',json.dumps(rows))
    checks='''
const shown=()=>rows.filter(c=>!c.hidden).map(c=>c.textContent);
assert.equal(shown().length,6);
els.result.value='fail';applyFilters();assert.deepEqual(shown(),['case 2','case 6']);
els.tasks.value='multi';applyFilters();assert.deepEqual(shown(),['case 6']);
els.search.value='missing';applyFilters();assert.equal(shown().length,0);assert.equal(els.empty.hidden,false);
els.reset.listeners.click();assert.equal(shown().length,6);
els.result.value='routefail';applyFilters();assert.deepEqual(shown(),['case 6']);
els.result.value='bodyfail';applyFilters();assert.deepEqual(shown(),['case 2']);
els.result.value='mechanical';applyFilters();assert.deepEqual(shown(),['case 1']);
els.result.value='observation';applyFilters();assert.deepEqual(shown(),['case 5']);
els.result.value='pending';applyFilters();assert.deepEqual(shown(),['case 4']);
'''
    result=engine.evaluate(setup+JS+checks)
    assert not result.isError(),result.toString()


def generation_sample():
    data=sample();data.pop('labels');data['evaluation']={'classification':False}
    for c in data['cases']:
        c.pop('expected_labels');c.pop('actual_labels')
    return data


def test_generation_report_omits_inapplicable_dimensions_and_keeps_custom_evidence():
    data=generation_sample()
    data['cases'][0]['fields']=[{'label':'条件','value':'<full>'}]
    data['cases'][0]['assessments']=[{'label':'姓名','status':'pass','expected':'张玮','actual':'张玮'}]
    data['cases'][0]['details']=[{'title':'逐步执行','text':'<step>'}]
    data['analysis_tables']=[{'title':'条件对照','columns':['方式','结果'],'rows':[['拆分','3/3']]}]
    page,stats=render(data)
    assert 'route_pass' not in stats and stats['overall']['pass']==2
    for token in ('预期分类','实际分类','类别数','分类不匹配','id="tasks"','data-route=','data-tasks=','分类＋正文'):
        assert token not in page
    for token in ('条件对照','逐项要求','逐步执行','&lt;step&gt;','&lt;full&gt;'):
        assert token in page


def test_generation_report_rejects_fake_labels_and_contradictory_requirement_reviews():
    data=generation_sample();data['cases'][0]['expected_labels']=[]
    with pytest.raises(ValueError):render(data)
    data=generation_sample();data['cases'][0]['assessments']=[{'label':'姓名','status':'fail','expected':'张玮','actual':'王玮'}]
    with pytest.raises(ValueError):render(data)


def test_generation_filter_script_works_without_category_control():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QJSEngine
    app=QCoreApplication.instance() or QCoreApplication([])
    engine=QJSEngine()
    data=validate(generation_sample())
    rows=[{'dataset':{'status':c['overall'],'group':'test','body':c['review']['status'],'mechanical':'false','search':c['id']},'hidden':False} for c in data['cases']]
    script='''const rows=ROWS;const els={};for(const id of ['result','group','search','reset','count','empty'])els[id]={value:id==='search'?'':'all',tagName:id==='search'?'INPUT':'SELECT',listeners:{},addEventListener(k,fn){this.listeners[k]=fn;}};
const document={querySelectorAll(){return rows;},getElementById(id){return els[id]||null;}};
'''.replace('ROWS',json.dumps(rows))
    checks='''els.result.value='fail';applyFilters();if(rows.filter(c=>!c.hidden).length!==1)throw Error('failure count');els.search.value='missing';applyFilters();if(els.empty.hidden)throw Error('empty');els.reset.listeners.click();if(rows.filter(c=>!c.hidden).length!==6)throw Error('reset');'''
    result=engine.evaluate(script+JS+checks)
    assert not result.isError(),result.toString()
