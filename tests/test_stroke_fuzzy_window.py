"""Exercise the independent test window without connecting to a Ring."""
import importlib.util
from pathlib import Path

from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext


def test_independent_window_fuzzy_toggle_context_undo_and_commit(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(['fuzzy-test', '-platform', 'offscreen'])
    spec = importlib.util.spec_from_file_location('fuzzy_sample_window',
        Path(__file__).resolve().parents[1]/'tools/collect_stroke_samples.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    window = module.Window('offline', collect=False, recognizer='dtw')
    try:
        window.dictionary = StrokeDictionary(dict(templates=[], ranks={'小': 0, '你': 0},
            entries=[['小', 'spn', 1], ['你', 'pspzspn', 1]]))
        window.predictor = StrokeContext(dict(maxContext=4, total=100,
            unigrams={'小': 50, '你': 50}, contexts={'爱': [100, '你', [100]]}))
        value = dict(result=dict(category='s', shape='竖', distance=.001,
            category_scores=[dict(category='s', distance=.001), dict(category='p', distance=.002)]),
            points=[(0, 0), (-20, 100)], recognition_ms=1.)
        window.accept_stroke(value)
        assert window.candidates[0] == '小' and '<sub' in window.composition.text()
        window.editor.setText('我爱')
        assert window.candidates[0] == '你'
        second = dict(result=dict(category='s', shape='竖', distance=0.),
            points=[(0, 0), (0, 100)], recognition_ms=1.)
        window.accept_stroke(second)
        assert window.candidates == ['你'] and len(window.evidence) == 2
        window.fuzzy_toggle.setChecked(False)
        assert not window.candidates and '<sub' not in window.composition.text()
        window.fuzzy_toggle.setChecked(True)
        assert window.candidates == ['你']
        window.undo()
        assert len(window.evidence) == 1
        window.commit(0)
        assert window.editor.text() == '我爱你' and not window.evidence and not window.code
        window.show()
        app.processEvents()
        assert window.grab().save(str(tmp_path/'independent-fuzzy.png'))
    finally:
        window.close()
        app.processEvents()


def test_independent_window_recovers_wo_with_weak_diagonal_hook(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(['hook-test', '-platform', 'offscreen'])
    spec = importlib.util.spec_from_file_location('hook_sample_window',
        Path(__file__).resolve().parents[1]/'tools/collect_stroke_samples.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    window = module.Window('offline', collect=False, recognizer='dtw')
    try:
        window.dictionary = StrokeDictionary(dict(templates=[], ranks={'我': 0},
            entries=[['我', 'phshzpn', 1]]))
        window.predictor = StrokeContext(dict(maxContext=4, total=1, unigrams={'我': 1}, contexts={}))
        window.code = 'phsh'
        from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
        window.evidence = [stroke_evidence(c) for c in window.code]
        # The best fold is not the diagonal hook; its own score is retained.
        window.accept_stroke(dict(result=dict(category='n', shape='捺', distance=.005,
            category_scores=[dict(category='n', shape='捺', distance=.005),
                             dict(category='z', shape='竖弯', distance=.048)],
            shape_scores=[dict(category='z', shape='斜钩', distance=.084)]),
            points=[(0, 0), (100, 150)], recognition_ms=1.))
        assert window.code == 'phshn' and window.candidates == ['我']
        assert '<sub' in window.composition.text() and window.matches[0]['corrections'] == [4]
        for category, shape in [('p', '撇'), ('n', '点')]:
            window.accept_stroke(dict(result=dict(category=category, shape=shape, distance=0.),
                points=[(0, 0), (10, 20)], recognition_ms=1.))
        assert window.code == 'phshnpn' and window.candidates == ['我']
        window.fuzzy_toggle.setChecked(False)
        assert not window.candidates and '<sub' not in window.composition.text()
        window.fuzzy_toggle.setChecked(True)
        assert window.candidates == ['我']
        window.commit(0)
        assert window.editor.text() == '我' and not window.evidence
    finally:
        window.close()
        app.processEvents()


def test_independent_window_left_dot_prefix_toggle_manual_undo_and_commit(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(['left-dot-test', '-platform', 'offscreen'])
    spec = importlib.util.spec_from_file_location('left_dot_sample_window',
        Path(__file__).resolve().parents[1]/'tools/collect_stroke_samples.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    window = module.Window('offline', collect=False, recognizer='dtw')
    try:
        window.dictionary = StrokeDictionary(dict(templates=[], ranks={'心': 0},
            entries=[['心', 'nznn', 1]]), left_dots=[dict(character='心', code='nznn', positions=[0])])
        window.predictor = StrokeContext(dict(maxContext=1, total=1, unigrams={'心': 1}, contexts={}))
        for category in 'pznn':
            window.accept_stroke(dict(result=dict(category=category, shape='撇', distance=.001),
                                     points=[(0,0),(-20,100)], recognition_ms=1.))
            assert window.candidates == ['心']
        assert window.code == 'pznn' and window.matches[0]['left_dot_corrections'] == [0]
        assert '<sub' in window.composition.text()
        window.fuzzy_toggle.setChecked(False)
        assert not window.candidates
        window.fuzzy_toggle.setChecked(True)
        assert window.candidates == ['心']
        window.undo()
        assert window.code == 'pzn'
        window.commit(0)
        assert window.editor.text() == '心' and not window.evidence
        from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
        window.code = 'p'
        window.evidence = [stroke_evidence('p')]
        window.refresh()
        assert not window.candidates
    finally:
        window.close()
        app.processEvents()


def test_slider_retunes_existing_ink_and_reset_toggle_undo(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
    app = QApplication.instance() or QApplication(['strength-test', '-platform', 'offscreen'])
    spec = importlib.util.spec_from_file_location('strength_sample_window',
        Path(__file__).resolve().parents[1]/'tools/collect_stroke_samples.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    window=module.Window('offline',collect=False,recognizer='dtw')
    try:
        window.dictionary=StrokeDictionary(dict(templates=[],ranks={'小':0,'你':1},
            entries=[['小','spn',1],['你','pspzspn',1]]))
        window.predictor=StrokeContext(dict(maxContext=1,total=2,unigrams={'小':1,'你':1},contexts={}))
        window.accept_stroke(dict(result=dict(category='s',shape='竖',distance=.001,
            category_scores=[dict(category='s',distance=.001),dict(category='p',distance=.071)]),
            points=[(0,0),(0,100)],recognition_ms=1.))
        window.fuzzy_strength_slider.setValue(0)
        assert window.code=='s' and window.candidates==['小']
        window.fuzzy_strength_slider.setValue(100)
        assert window.code=='s' and '你' in window.candidates
        window.fuzzy_strength_reset.click()
        assert window.fuzzy_policy.level==50 and window.fuzzy_strength_slider.value()==50
        window.fuzzy_toggle.setChecked(False)
        assert window.candidates==['小'] and not window.fuzzy_strength_slider.isEnabled()
        window.fuzzy_toggle.setChecked(True)
        assert window.fuzzy_strength_slider.isEnabled()
        window.undo()
        assert not window.code and not window.evidence
        window.code='s'; window.evidence=[stroke_evidence('s')]
        window.fuzzy_strength_slider.setValue(100)
        assert window.candidates==['小'] and window.evidence[0]['manual']
        import json
        events=[json.loads(line) for line in window.log_path.read_text(encoding='utf-8').splitlines()]
        assert any(row['kind']=='fuzzy_settings' and row['policy']['level']==100 for row in events)
    finally:
        window.close(); app.processEvents()
