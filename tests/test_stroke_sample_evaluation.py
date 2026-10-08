import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('stroke_evaluation',Path(__file__).resolve().parents[1]/'tools/evaluate_stroke_samples.py')
evaluation=importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_structural_features_preserve_small_real_turn_and_absolute_length():
    line=evaluation.path_features([(i,0) for i in range(101)])
    folded=evaluation.path_features([(i,0) for i in range(101)]+[(100,i) for i in range(1,9)])
    assert not line['turns']
    assert len(folded['turns'])==1 and folded['turns'][0]['angle_deg']==90
    assert folded['length']==108


def test_old_orientation_and_synthetic_labels_are_excluded(tmp_path):
    for name,version,synthetic in [('old',2,False),('smoke',3,True),('fresh',3,False)]:
        rows=[dict(kind='session',version=version,synthetic=synthetic),dict(kind='annotation',rating=1)]
        (tmp_path/(name+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
    labels,excluded=evaluation.load_labels(tmp_path)
    assert len(labels)==1 and labels[0]['file'].endswith('fresh.jsonl')
    assert {r['reason'] for r in excluded}=={'synthetic','before_orientation_fix'}


def test_explicit_label_correction_preserves_original_record(tmp_path):
    path=tmp_path/'fresh.jsonl'
    target=dict(shape='横折',variant='小折横折',category='z')
    rows=[dict(kind='session',version=3,synthetic=False),dict(kind='annotation',rating=2,target=target)]
    path.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows),encoding='utf-8')
    path.with_suffix('.labels.json').write_text(json.dumps(dict(shape_aliases={'横折':'横钩'},
        variant_aliases={'小折横折':'小钩横钩'}),ensure_ascii=False),encoding='utf-8')
    labels,_=evaluation.load_labels(tmp_path)
    assert labels[0]['target']['shape']=='横钩'
    assert labels[0]['target']['variant']=='小钩横钩'
    assert labels[0]['original_target']==target
    assert json.loads(path.read_text(encoding='utf-8').splitlines()[1])['target']==target
