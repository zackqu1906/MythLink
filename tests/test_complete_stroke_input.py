import importlib.util
import json
import math
from pathlib import Path
import sys

import pytest

from proximic_ring.touchpad.stroke.stroke_test_gestures import recognize
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_context import StrokeContext


def trace(*vectors):
    points = [(0., 0.)]
    for dx, dy in vectors:
        points.append((points[-1][0]+dx, points[-1][1]+dy))
    return points


@pytest.mark.parametrize('vector,direction,target', [
    ((1,0),'right',4), ((1,1),'down-right',2), ((0,1),'down',0),
    ((-1,1),'down-left',1), ((-1,0),'left',3),
    ((-1,-1),'up-left','，'), ((1,-1),'up-right','。')])
def test_seven_directions_resolve_before_deletion_and_keep_candidate_ranks(vector, direction, target):
    points = trace(*([(0,-2)]*20+[vector]*40))
    result = recognize(points)
    assert result['target'] == target and result['direction'] == direction
    assert result['action'] == ('select' if isinstance(target,int) else 'punctuation')


def test_reserved_up_pause_straight_strokes_and_two_segment_clear_boundary():
    assert recognize(trace(*([(0,-1)]*40+[(0,0)]*60+[(0,-1)]*40)))['action'] == 'ignore'
    for vector in [(1,0),(0,1),(-1,1),(1,1)]:
        assert recognize(trace(*([vector]*30)))['action'] == 'stroke'
    assert recognize(trace(*([(-1,0)]*30)))['action'] == 'stroke'
    assert recognize(trace(*([(-1,0)]*30+[(1,0)]*30)))['action'] == 'delete-one'
    assert recognize(trace(*([(-1,0)]*30+[(1,0)]*30+[(-1,0)]*30)))['action'] == 'clear-all'
    assert recognize([(0,0),(float('nan'),0)])['action'] == 'ignore'


@pytest.mark.parametrize('rotation', [-25., 0., 25.])
def test_left_right_delete_allows_tilt_pause_jitter_and_partial_return(rotation):
    # Returning all the way to the starting point is not required.
    points = trace(*([(-1,0)]*30+[(0,0)]*20+[(.1,0),(-.1,0)]+[(1,0)]*20))
    angle = math.radians(rotation)
    points = [(100+x*math.cos(angle)-y*math.sin(angle),
               80+x*math.sin(angle)+y*math.cos(angle)) for x,y in points]
    decision = recognize(points)
    assert decision['action'] == 'delete-one'
    assert decision['deleteGesture']['direction'] == 'left-right'
    assert decision['clearReview'] is None


@pytest.mark.parametrize('points', [
    trace(*([(1,0)]*30+[(-1,0)]*30)),  # Reverse order is not delete.
    trace(*([(-1,0)]*30+[(1,0)]*4)),  # Tiny trailing hook.
    trace(*([(-1,1)]*30+[(1,0)]*30)),  # A diagonal fold is handwriting.
    trace(*([(-1,0)]*100+[(1,0)]*10)),  # Return is too small relative to the body.
    [(0,0),(-10,0),(-20,20),(-30,0),(0,0)],  # Strong vertical excursion.
])
def test_left_right_delete_rejects_reverse_short_hook_and_fold(points):
    assert recognize(points)['action'] == 'stroke'


@pytest.mark.parametrize('first,second', [(16.,0.), (20.,0.), (25.,10.), (12.,25.), (0.,-15.)])
def test_shallow_pie_fold_with_full_return_is_handwriting(first,second):
    # These two-pass folds satisfied v1; a full right return must not hide
    # the difference between the outward and returning axes.
    a,b = math.radians(first), math.radians(second)
    points=trace(*([(-math.cos(a),math.sin(a))]*40+
                    [(math.cos(b),-math.sin(b))]*40))
    assert recognize(points)['action']=='stroke'
    assert recognize([p for point in points for p in [point]*3])['action']=='stroke'


@pytest.mark.parametrize('first,second', [(20.,20.), (25.,25.), (22.,15.), (5.,12.)])
def test_inclined_delete_checks_retrace_angle_instead_of_absolute_flatness(first,second):
    a,b=math.radians(first),math.radians(second)
    decision=recognize(trace(*([(-math.cos(a),math.sin(a))]*40+
                              [(math.cos(b),-math.sin(b))]*40)))
    assert decision['action']=='delete-one'
    assert decision['deleteGesture']['retraceAngleError']==pytest.approx(abs(first-second))


def test_shallow_pie_fold_returning_downward_is_not_a_retrace():
    # Absolute angles are equal (12 degrees), but the axes differ by 24.
    angle=math.radians(12.)
    points=trace(*([(-math.cos(angle),math.sin(angle))]*40+
                    [(math.cos(angle),math.sin(angle))]*40))
    assert recognize(points)['action']=='stroke'


def test_old_language_assist_cannot_restore_single_left_delete():
    class OldAssist:
        def decide(self, *args, **kwargs):
            raise AssertionError('single-left deletion assist must not be consulted')
    assert recognize(trace(*([(-1,0)]*30)), context_assist=OldAssist(),
                     before_text='什么', stroke_code='ps')['action'] == 'stroke'


@pytest.mark.parametrize('first,second', [(-74.7,-18.4),(-81.5,-15.6),(-90.,-45.)])
def test_right_up_uses_first_up_run_as_reference(first, second):
    def vector(angle): return (math.cos(math.radians(angle))*2, math.sin(math.radians(angle))*2)
    result = recognize(trace(*([vector(first)]*30+[vector(second)]*45)))
    assert result['direction'] == 'up-right' and result['target'] == '。'


@pytest.mark.parametrize('rotation', [-15.,0.,15.])
def test_reference_correction_keeps_straight_right_distinct(rotation):
    def vector(angle): return (math.cos(math.radians(angle))*2, math.sin(math.radians(angle))*2)
    result = recognize(trace(*([vector(-90+rotation)]*30+[vector(rotation)]*45)))
    assert result['direction'] == 'right' and result['target'] == 4


def load_window(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(['complete-input-test','-platform','offscreen'])
    app.setStyle('Fusion')
    tools = Path(__file__).resolve().parents[1]/'tools'
    monkeypatch.syspath_prepend(str(tools))
    import collect_stroke_samples
    monkeypatch.setattr(collect_stroke_samples, 'ROOT', tmp_path)
    spec = importlib.util.spec_from_file_location('complete_trial',tools/'test_complete_stroke_input.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Existing expectations were written against the legacy context counts.
    return app, module.Window('offline', context_model='legacy')


def test_live_trial_strength_changes_candidates_and_records_history(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.dictionary.recognize = lambda points: dict(category='s',shape='竖',distance=.001,
            category_scores=[dict(category='s',distance=.001),dict(category='p',distance=.071)])
        window.target_input.setPlainText('你')
        window.start_trial()
        assert not window.fuzzy_toggle.isEnabled() and window.fuzzy_strength_slider.isEnabled()
        window.accept_stroke(dict(points=trace(*([(0,2)]*30))))
        window.fuzzy_strength_slider.setValue(0)
        assert window.code=='s' and window.candidates==['小']
        window.fuzzy_strength_slider.setValue(100)
        assert window.code=='s' and '你' in window.candidates
        assert window.trial.events[0]['recognized']=='s'
        window.finish_trial()
        report=json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['fuzzy_policy']['level']==100 and report['left_dot_policy']['cost']==.25
        assert report['fuzzy_policy_initial']['level']==50
        changes=[event['level'] for event in report['events'] if event['kind']=='fuzzy_settings']
        assert changes==[0,100]
        window.show(); app.processEvents()
        assert window.fuzzy_strength_slider.isVisible()
        assert window.grab().save(str(tmp_path/'fuzzy-strength-complete.png'))
    finally:
        window.close(); app.processEvents()


def prepare(window):
    window.dictionary = StrokeDictionary(dict(templates=[], ranks={'小':0,'你':0},
        entries=[['小','spn',1],['你','pspzspn',1]]))
    window.dictionary.recognize = lambda points: dict(category='s',shape='竖',distance=.001,
        category_scores=[dict(category='s',distance=.001),dict(category='p',distance=.002)])
    window.predictor = StrokeContext(dict(maxContext=4,total=100,unigrams={'小':50,'你':50},
        contexts={'爱':[100,'你',[100]]}))
    # The toy dictionary pins exact lists; page filling has its own tests.
    window.fill_toggle.setChecked(False)


def test_expanded_default_scope_commits_ni_and_niao(tmp_path, monkeypatch):
    from proximic_ring.touchpad.stroke.stroke_glyphs import glyph_library
    app, window = load_window(tmp_path, monkeypatch)
    try:
        assert window.common_toggle.isChecked() and '6500' in window.common_toggle.text()
        window.correction_toggle.setChecked(False)
        window.target_input.setPlainText('霓袅')
        window.start_trial()
        for character in '霓袅':
            for category in glyph_library()[character]['code']:
                window.add_manual(category)
            assert character in window.candidates
            window.commit(window.candidates.index(character))
        assert window.editor.text() == '霓袅' and window.trial_finished()
        report = json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['completed'] and report['common_only']
    finally:
        window.close()
        app.processEvents()


def select_after_hold(window, vector):
    from proximic_ring.touchpad.stroke.stroke_contact_intent import classify_contact
    points = [(0,0)]*68 + [(vector[0]*i, vector[1]*i) for i in range(1,21)]
    window.accept_selection(dict(points=points, decision=classify_contact(points, finished=True)))


def test_adaptive_ink_reaches_candidates_and_log_then_retunes_and_undoes(tmp_path, monkeypatch):
    from proximic_ring.touchpad.stroke.stroke_kinematics import measure_stroke
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.dictionary.recognize = lambda points: dict(category='s', shape='竖', distance=.001,
            category_scores=[dict(category='s', distance=.001), dict(category='p', distance=.091)])
        points = trace(*([(0, .5)]*30))
        window.target_input.setPlainText('小')
        window.start_trial()
        window.accept_stroke(dict(points=points, kinematics=measure_stroke(points, 150)))
        assert window.code == 's' and set(window.candidates) == {'小', '你'}
        assert window.evidence[-1]['adaptation']['effectiveLevel'] == 65
        assert '50 → 65' in window.adaptive_status.text()
        window.fuzzy_strength_slider.setValue(0)
        assert window.code == 's' and window.candidates == ['小']
        assert '0 → 15' in window.adaptive_status.text()
        window.fuzzy_strength_slider.setValue(50)
        assert '你' in window.candidates
        window.show(); app.processEvents()
        assert window.adaptive_status.isVisible()
        assert window.adaptive_status.geometry().bottom() < window.adaptive_status.parentWidget().height()
        assert window.grab().save(str(tmp_path/'adaptive-complete.png'))
        window.log.flush()
        rows = [json.loads(line) for line in window.log_path.read_text(encoding='utf-8').splitlines()]
        ink = next(row for row in rows if row['kind'] == 'stroke')
        assert ink['kinematics']['duration_ms'] == 150
        assert ink['effective_fuzzy_policy']['level'] == 65 and ink['fuzzy_policy']['level'] == 50
        assert ink['adaptation']['boost'] == 15
        window.undo()
        assert not window.evidence and '最高增加30档' in window.adaptive_status.text()
        window.add_manual('s')
        assert window.candidates == ['小'] and 'adaptation' not in window.evidence[-1]
        window.finish_trial()
        report = json.loads(window.report_path.read_text(encoding='utf-8'))
        assert report['adaptive_fuzzy_policy']['maxBoost'] == 30
        event = next(event for event in report['events'] if event['kind'] == 'fuzzy_adaptation')
        assert event['base_level'] == 50 and event['effective_level'] == 65 and event['speed'] == 100
        window.fuzzy_toggle.setChecked(False)
        assert window.candidates == ['小'] and '已关闭' in window.adaptive_status.text()
        window.fuzzy_toggle.setChecked(True)
    finally:
        window.close(); app.processEvents()


def test_complete_window_common_scope_toggle_punctuation_and_prediction(tmp_path,monkeypatch):
    app,window=load_window(tmp_path,monkeypatch)
    try:
        prepare(window)
        window.dictionary=StrokeDictionary(dict(templates=[],ranks={'龘':0,'小':1},
            entries=[['龘','s',1],['小','spn',1]]))
        window.predictor=StrokeContext(dict(maxContext=1,total=1000,unigrams={'龘':999,'好':1},
            contexts={'你':[1000,'龘好',[999,1]]}))
        assert window.common_toggle.isChecked()
        window.add_manual('s')
        assert window.code=='s' and window.candidates==['小']
        window.fuzzy_toggle.setChecked(False)
        assert window.candidates==['小']
        window.common_toggle.setChecked(False)
        assert window.code=='s' and window.candidates[0]=='龘'
        window.common_toggle.setChecked(True)
        assert window.candidates==['小'] and window.code=='s'
        window.punctuate('，')
        assert window.editor.text()=='，' and not window.code and not window.evidence
        window.editor.setText('你');window.editor.setCursorPosition(1)
        assert window.candidates==['好']
        window.common_toggle.setChecked(False)
        assert window.candidates[0]=='龘'
        window.common_toggle.setChecked(True)
        assert window.candidates==['好']
        window.dictionary=StrokeDictionary(dict(templates=[],ranks={'龘':0},entries=[['龘','h',1]]))
        window.add_manual('h')
        assert not window.candidates and '无常用字匹配' in window.page_label.text()
        window.punctuate('。')
        assert not window.code and not window.evidence and window.editor.text()=='你。'
        window.log.flush()
        events=[json.loads(line)for line in window.log_path.read_text(encoding='utf-8').splitlines()]
        assert any(event.get('candidateScope',{}).get('mode')=='common-6500'for event in events)
        assert any(event.get('candidateScope',{}).get('mode')=='all-characters'for event in events)
    finally:
        window.close();app.processEvents()


def test_complete_window_short_curved_hook_candidates_toggle_and_examples(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QLabel
    app,window=load_window(tmp_path,monkeypatch)
    try:
        prepare(window)
        window.dictionary=StrokeDictionary(dict(templates=[],ranks={'了':0,'子':1},
            entries=[['了','zz',1],['子','zsh',1]]))
        window.dictionary.recognize=lambda points:dict(category='s',shape='竖钩',distance=.003,
            category_scores=[dict(category='s',shape='竖钩',distance=.003),
                             dict(category='z',shape='弯钩',distance=.04)])
        window.add_manual('z')
        window.accept_stroke(dict(points=trace(*([(0,2)]*30))))
        assert window.code=='zs' and '了' in window.candidates
        assert next(r for r in window.matches if r['char']=='了')['corrections']==[1]
        assert '小' in window.result.text() and '只写这一笔' in window.result.text()
        assert any('笔画对照' in label.text() and '戈' in label.text() and '心' in label.text()
                   for label in window.findChildren(QLabel))
        window.fuzzy_toggle.setChecked(False)
        assert '了' not in window.candidates
        window.fuzzy_toggle.setChecked(True)
        assert '了' in window.candidates
        window.commit(window.candidates.index('了'))
        assert window.editor.text()=='了' and not window.evidence
    finally:
        window.close();app.processEvents()


def test_complete_window_reviews_clear_reuses_result_and_keeps_real_clear(tmp_path,monkeypatch):
    app,window=load_window(tmp_path,monkeypatch)
    cases=json.loads((Path(__file__).parent/'fixtures/clear_review_regression.json').read_text())['cases']
    fold=next(row for row in cases if row['sampleNumber']==57)
    clear=next(row for row in cases if row['kind']=='clear')
    calls=[]
    try:
        window.dictionary=StrokeDictionary(dict(templates=[],ranks={'九':0},entries=[['九','pz',1]]))
        window.fill_toggle.setChecked(False)
        def recognize_once(points):
            calls.append(points)
            return dict(category='z',shape='横折弯钩',distance=.03,
                        category_scores=[dict(category='z',shape='横折弯钩',distance=.03)])
        window.dictionary.recognize=recognize_once
        window.editor.setText('正文')
        window.add_manual('p')
        window.accept_stroke(dict(points=fold['points']))
        assert window.code=='pz' and window.candidates==['九']
        assert len(calls)==1 and '按笔画保留' in window.result.text()
        window.accept_stroke(dict(points=clear['points']))
        assert not window.code and not window.evidence
        assert window.editor.text()=='正文' and len(calls)==1
    finally:
        window.close();app.processEvents()


def test_complete_window_failed_review_keeps_composition_without_retrying_model(tmp_path,monkeypatch):
    app,window=load_window(tmp_path,monkeypatch)
    cases=json.loads((Path(__file__).parent/'fixtures/clear_review_regression.json').read_text())['cases']
    fold=next(row for row in cases if row['sampleNumber']==57)
    calls=[]
    try:
        window.add_manual('p')
        def fail(points):
            calls.append(points)
            raise RuntimeError('test failed review')
        window.dictionary.recognize=fail
        window.accept_stroke(dict(points=fold['points']))
        assert window.code=='p' and len(window.evidence)==1 and len(calls)==1
        assert '未清空' in window.result.text()
    finally:
        window.close();app.processEvents()


def test_complete_window_fuzzy_context_delete_commit_punctuation_and_clear(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        down = trace(*([(0,2)]*30))
        window.accept_stroke(dict(points=down))
        assert window.code == 's' and window.candidates[0] == '小'
        assert '<sub' in window.composition.text()
        window.editor.setText('我爱')
        window.editor.setCursorPosition(2)
        assert window.candidates[0] == '你'
        window.fuzzy_toggle.setChecked(False)
        assert window.candidates == ['小']
        window.fuzzy_toggle.setChecked(True)
        assert window.candidates[0] == '你'
        # One left-right contact removes one pending stroke before committed text.
        window.accept_stroke(dict(points=trace(*([(-2,0)]*20+[(2,0)]*20))))
        assert not window.code and window.editor.text() == '我爱'
        window.accept_stroke(dict(points=down))
        select_after_hold(window, (-1,-1))
        assert window.editor.text() == '我爱，' and not window.evidence
        window.accept_stroke(dict(points=trace(*([(-2,0)]*20+[(2,0)]*20))))
        assert window.editor.text() == '我爱'
        window.add_manual('h')
        window.accept_stroke(dict(points=trace(*([(-1,0)]*30+[(1,0)]*30+[(-1,0)]*30))))
        assert not window.code and window.editor.text() == '我爱'
        select_after_hold(window, (0,0))
        assert not window.code and window.editor.text() == '我爱你'
        window.punctuate('。')
        assert window.editor.text() == '我爱你。'
        window.show()
        app.processEvents()
        assert window.grab().save(str(tmp_path/'complete-input.png'))
    finally:
        window.close()
        app.processEvents()


def test_candidate_six_and_missing_target_preserve_composition_and_text(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.dictionary.entries = [[c,'h',1] for c in '一二三四五六七']
        window.dictionary.by_initial['h'] = window.dictionary.entries
        window.dictionary.ranks = {c:i for i,c in enumerate('一二三四五六七')}
        window.add_manual('h')
        select_after_hold(window, (1,1))
        assert window.editor.text() == '六' and not window.code
        window.add_manual('h')
        window.page(6)
        assert window.selected == 6
        select_after_hold(window, (1,0))
        assert window.code == 'h' and window.editor.text() == '六'
        window.punctuate('，')
        assert window.editor.text() == '六，' and not window.code and not window.evidence
        window.add_manual('h')
        window.add_manual('z')
        assert not window.candidates
        window.punctuate('。')
        assert not window.code and not window.evidence and window.editor.text() == '六，。'
    finally:
        window.close()
        app.processEvents()


def test_hold_menu_feedback_six_page_wrap_center_and_old_prefix_is_handwriting(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.dictionary = StrokeDictionary(dict(templates=[], ranks={c:i for i,c in enumerate('一二三四五六七')},
            entries=[[c,'h',1] for c in '一二三四五六七']))
        window.dictionary.recognize = lambda points: dict(category='s', shape='竖', distance=.001,
            category_scores=[dict(category='s', distance=.001)])
        window.add_manual('h')
        window.show_selection_state(dict(phase='holding', progress=50, direction=None))
        assert window.selection_progress.value() == 50 and '保持不动' in window.selection_hint.text()
        window.show_selection_state(dict(phase='selecting', progress=100, direction='right'))
        assert '选字模式' in window.selection_hint.text()
        assert window.direction_buttons['right'].property('directionActive')
        assert not window.direction_buttons['down'].property('directionActive')
        window.resize(1280,650); window.show(); app.processEvents()
        assert window.grab().save(str(tmp_path/'hold-selecting.png'))
        assert len(window.direction_buttons) == 9
        for button in window.direction_buttons.values():
            assert button.isVisible() and button.width() > 0 and button.height() > 0
        assert not window.previous_page_button.isEnabled()
        window.next_page_button.click()
        assert window.selected == 6 and window.code == 'h' and window.editor.text() == ''
        assert window.previous_page_button.isEnabled()
        window.next_page_button.click()
        assert window.selected == 0
        select_after_hold(window, (0,0))
        assert not window.code and window.editor.text() == '一'
        assert '已选第 1 候选' in window.result.text()
        window.add_manual('h')
        # No stationary prefix: the old up-right gesture cannot commit anymore.
        window.accept_stroke(dict(points=trace(*([(0,-2)]*20+[(2,0)]*20))))
        assert window.code == 'hs' and window.editor.text() == '一'
        window.clear()
        window.accept_stroke(dict(points=trace(*([(0,-2)]*40))))
        assert window.code == 's'  # Up no longer disappears as a reserved gesture.
    finally:
        window.close(); app.processEvents()


def test_single_left_enters_handwriting_and_left_right_deletes_once(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QCheckBox, QLabel
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.editor.setText('正文')
        window.editor.setCursorPosition(2)
        window.add_manual('h')
        window.accept_stroke(dict(points=trace(*([(-1,0)]*30))))
        assert window.code == 'hs' and window.editor.text() == '正文'
        window.accept_stroke(dict(points=trace(*([(-1,0)]*30+[(1,0)]*30))))
        assert window.code == 'h' and len(window.evidence) == 1
        assert window.editor.text() == '正文'
        window.clear()
        # Separate left and right contacts are two handwriting strokes.
        for direction in [-1, 1]:
            window.accept_stroke(dict(points=trace(*([(direction,0)]*30))))
        assert window.code == 'ss' and window.editor.text() == '正文'
        window.clear()
        window.editor.setSelection(0, 2)
        window.accept_stroke(dict(points=trace(*([(-1,0)]*30+[(1,0)]*30))))
        assert window.editor.text() == ''
        assert not any('删除语境' in box.text() for box in window.findChildren(QCheckBox))
        assert any('左→右' in label.text() for label in window.findChildren(QLabel))
        window.log.flush()
        events = [json.loads(line) for line in window.log_path.read_text(encoding='utf-8').splitlines()]
        assert sum(event.get('kind') == 'delete_one' for event in events) == 2
        assert any(event.get('deletionSource') == 'left-right-geometric-v3' for event in events)
    finally:
        window.close(); app.processEvents()


@pytest.mark.parametrize('vector,target', [
    ((0, 0), 0), ((-1, 0), 1), ((1, 0), 2), ((-1, 1), 3), ((0, 1), 4), ((1, 1), 5),
    ((-1, -1), '，'), ((0, -1), '。'), ((1, -1), '？'),
])
def test_six_candidate_menu_all_targets_on_both_pages(tmp_path, monkeypatch, vector, target):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        characters = '一二三四五六七八九十百千'
        window.dictionary = StrokeDictionary(dict(templates=[], ranks={c:i for i,c in enumerate(characters)},
            entries=[[c, 'h', 1] for c in characters]))
        for page in (0, 1):
            window.editor.clear()
            window.add_manual('h')
            if page:
                window.page(6)
            assert len(window.buttons) == 6
            assert [b.visual['character'] for b in window.buttons] == list(characters[page*6:page*6+6])
            select_after_hold(window, vector)
            expected = characters[page*6+target] if isinstance(target, int) else target
            assert window.editor.text() == expected and not window.code
            window.log.flush()
            records = [json.loads(line) for line in window.log_path.read_text('utf-8').splitlines()]
            contact = next(row for row in reversed(records) if row['kind'] == 'hold_selection')
            assert contact['decision']['target'] == target
            if vector == (0, -1):
                assert contact['source_decision']['action'] == 'page'
                assert contact['decision']['action'] == 'punctuation'
    finally:
        window.close(); app.processEvents()


def test_six_cell_positions_center_highlight_question_mark_and_report(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.show(); app.processEvents()
        grid = window.selection_square.child.layout()
        assert grid.itemAtPosition(1, 1).widget() is window.buttons[0]
        assert [grid.itemAtPosition(r, c).widget() for r, c in ((1,0), (1,2), (2,0), (2,1), (2,2))] == window.buttons[1:]
        assert [grid.itemAtPosition(0, c).widget().symbol for c in range(3)] == ['，', '。', '？']
        window.show_selection_state(dict(phase='selecting', progress=100, direction=None))
        assert window.buttons[0].property('directionActive')
        assert not any(b.property('directionActive') for b in window.buttons[1:])
        window.target_input.setPlainText('一？')
        window.start_trial()
        assert '未支持字符' not in window.trial_hint.text()
        window.add_manual('h')
        select_after_hold(window, (0, 0))
        select_after_hold(window, (1, -1))
        assert window.editor.text() == '一？' and window.trial_finished()
        report = json.loads(window.report_path.read_text('utf-8'))
        assert report['completed'] and report['selection_policy']['pageSize'] == 6
        assert report['selection_policy']['candidateDirections']['center'] == 0
        assert report['selection_policy']['punctuationDirections']['up'] == '。'
    finally:
        window.close(); app.processEvents()


def test_sdk_tap_clears_preview_and_confirms_page_first_candidate(tmp_path,monkeypatch):
    app,window=load_window(tmp_path,monkeypatch)
    try:
        window.dictionary=StrokeDictionary(dict(templates=[],ranks={c:i for i,c in enumerate('一二三四五六七')},
            entries=[[c,'h',1] for c in '一二三四五六七']))
        window.add_manual('h')
        window.page(6)
        window.selected=6
        window.trace_view.show_trace([(0,0),(.2,.1),(.3,-.1)])
        window.accept_sdk_tap()
        assert window.trace_view.points==[]
        assert window.editor.text()=='七' and not window.code
        # Empty candidate lists still clear the tap preview without inserting text.
        window.predictor=StrokeContext(dict(maxContext=1,total=0,unigrams={},contexts={}))
        window.refresh()
        window.trace_view.show_trace([(0,0),(.1,0)])
        window.accept_sdk_tap()
        assert not window.trace_view.points and window.editor.text()=='七'
    finally:
        window.close();app.processEvents()


@pytest.mark.parametrize('size,minimum_side', [((1440, 900), 480), ((1440, 810), 400),
                                            ((1280, 720), 300), ((1100, 680), 260),
                                            ((1280, 640), 330), ((1100, 600), 280)])
def test_large_direction_menu_renders_glyphs_without_clipping(tmp_path, monkeypatch, size, minimum_side):
    from PySide6.QtCore import QPoint
    from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.code = 'ps'
        window.evidence = [stroke_evidence('p'), stroke_evidence('s', dict(category='s', shape='竖',
            category_scores=[dict(category='s', distance=.001), dict(category='p', distance=.002)]))]
        window.refresh()
        window.resize(*size)
        window.show()
        app.processEvents()
        from PySide6.QtTest import QTest
        QTest.qWait(80)  # Wait for the compact layout's second geometry pass.
        menu = window.selection_square.child
        assert menu.width() == menu.height() and menu.width() >= minimum_side
        for button in window.direction_buttons.values():
            point = button.mapTo(window, QPoint())
            assert button.isVisible() and point.x() >= 0 and point.y() >= 0
            assert point.x() + button.width() <= window.width()
            assert point.y() + button.height() <= window.height()
        assert len(window.buttons) == 6
        for button, character in zip(window.buttons, window.candidates[:6]):
            assert button.visual['character'] == character
            assert button.renderer.isValid() and 'fuzzy' in button.visual['states']
        assert any('remaining' in b.visual['states'] and 'next' in b.visual['states'] for b in window.buttons)
        assert window.page_label.width() >= 88
        window.show_selection_state(dict(phase='selecting', progress=100, direction='down-left'))
        assert window.buttons[3].property('directionActive')
        assert not window.buttons[0].property('directionActive')
        assert window.buttons[3].visual['states'][1] == 'fuzzy'
        app.processEvents()
        assert window.grab().save(str(tmp_path / f'eight-glyphs-{size[0]}-{size[1]}.png'))
    finally:
        window.close(); app.processEvents()


def test_short_screen_reflows_back_without_losing_input_or_selection(tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.add_manual('p')
        window.show_selection_state(dict(phase='selecting', progress=100, direction='down-left'))
        before = window.code, list(window.candidates), list(window.evidence)
        window.metric_values['总用时'].setText('12.3 秒')
        window.show()
        for size in [(1280, 640), (1440, 900), (1280, 640)]:
            window.resize(*size)
            QTest.qWait(80)
            compact = size[1] < 780
            assert window.property('compactTrial') == compact
            assert (window.trial_hint.parentWidget() is window) == compact
            assert window.minimumSizeHint().height() <= window.height()
            assert window.selection_square.child.height() >= (330 if compact else 480)
            assert (window.code, window.candidates, window.evidence) == before
            assert window.buttons[3].property('directionActive')
            assert window.metric_values['总用时'].text() == '12.3 秒'
        window.status.setText('连接状态说明' * 50)
        QTest.qWait(30)
        assert window.height() == 640 and window.selection_square.child.height() >= 330
    finally:
        window.close(); app.processEvents()


@pytest.mark.parametrize('platform,scale', [('offscreen', '1'), ('offscreen', '1.5'),
                                            ('offscreen', '2'), ('windows', None)])
def test_short_work_area_at_real_device_pixel_ratios(tmp_path, platform, scale):
    """Use fresh Qt processes: a running QApplication cannot change its DPI."""
    import os
    import subprocess
    if platform == 'windows' and sys.platform != 'win32':
        pytest.skip('Native Windows rendering')
    root = Path(__file__).resolve().parents[1]
    script = r'''
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'tools'))
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import collect_stroke_samples
collect_stroke_samples.ROOT = Path(sys.argv[2])
from test_complete_stroke_input import Window, show_for_screen
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
app = QApplication([])
app.setStyle('Fusion')
window = Window('offline')
window.setAttribute(Qt.WA_DontShowOnScreen, True)
if app.platformName() == 'windows':
    show_for_screen(window)
    work = window.screen().availableGeometry()
    assert window.width() <= work.width() and window.height() <= work.height()
else:
    window.resize(1280, 640)
    window.show()
QTest.qWait(150)
assert window.minimumHeight() <= 640
assert window.property('compactTrial') == (window.height() < 780)
menu = window.selection_square.child
assert menu.width() == menu.height() and menu.height() >= 330
empty = window.grab()
assert empty.width() == round(window.width() * window.devicePixelRatioF())
assert empty.save(str(Path(sys.argv[2]) / 'empty.png'))
window.code = 'ps'
window.evidence = [stroke_evidence('p'), stroke_evidence('s', dict(category='s', shape='竖',
    category_scores=[dict(category='s', distance=.001), dict(category='p', distance=.002)]))]
window.refresh()
QTest.qWait(80)
for button in window.direction_buttons.values():
    point = button.mapTo(window, QPoint())
    assert point.y() >= 0 and point.y() + button.height() <= window.height()
assert all(b.renderer.isValid() for b in window.buttons)
assert window.grab().save(str(Path(sys.argv[2]) / 'glyphs.png'))
print('DPR', window.devicePixelRatioF(), 'client', window.size().toTuple(), 'menu', menu.size().toTuple())
window.close()
app.processEvents()
'''
    env = {**os.environ, 'QT_QPA_PLATFORM': platform, 'PYTHONIOENCODING': 'utf-8'}
    if scale is None:
        env.pop('QT_SCALE_FACTOR', None)
    else:
        env['QT_SCALE_FACTOR'] = scale
    result = subprocess.run([sys.executable, '-c', script, str(root), str(tmp_path)],
                            env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_trial_glyphs_follow_page_undo_strict_matching_commit_and_prediction(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.show(); app.processEvents()
        window.code = 'ps'
        window.evidence = [stroke_evidence('p'), stroke_evidence('s', dict(category='s', shape='竖',
            category_scores=[dict(category='s', distance=.001), dict(category='p', distance=.002)]))]
        window.refresh()
        first = [b.visual for b in window.buttons]
        window.page(6)
        assert [b.visual['character'] for b in window.buttons if b.visual] == window.candidates[6:12]
        window.page(-6)
        assert [b.visual for b in window.buttons] == first
        window.fuzzy_toggle.setChecked(False)
        assert all('fuzzy' not in b.visual['states'] for b in window.buttons if b.visual)
        window.fuzzy_toggle.setChecked(True)
        assert [b.visual for b in window.buttons] == first
        window.undo()
        assert window.code == 'p'
        assert all(v['entered'] == 1 for b in window.buttons if (v := b.visual))
        chosen = window.candidates[2]
        QTest.mouseClick(window.buttons[2], Qt.LeftButton)
        assert window.editor.text() == chosen and not window.code
        assert all(b.visual['status'] == '联想' for b in window.buttons if b.visual)
        window.editor.clear()
        assert all(b.visual is None and not b.isEnabled() for b in window.buttons)
    finally:
        window.close(); app.processEvents()


def test_trial_recovers_nearby_wrong_stroke_and_renders_red_then_toggle_undo_commit(tmp_path, monkeypatch):
    import base64
    from PySide6.QtGui import QImage
    from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
    from proximic_ring.ui.touchpad.stroke_glyph_images import native_glyph_images
    app, window = load_window(tmp_path, monkeypatch)
    try:
        # Isolate the legacy empty-candidate fallback; default 6500 scope now
        # has an exact 犄 for this sequence. Whole-character repair is separate.
        window.allowed_characters = frozenset('我')
        window.correction_toggle.setChecked(False)
        window.code = 'phshhpn'
        window.evidence = [stroke_evidence(c) for c in window.code]
        window.evidence[4] = stroke_evidence('h', dict(category='h', shape='横', category_scores=[
            dict(category='h', distance=.001), dict(category='z', shape='横折', distance=.03),
            dict(category='s', distance=.04), dict(category='p', distance=.2), dict(category='n', distance=.3)]))
        # Only the text already entered supplies context; the task text is unrelated.
        window.target_input.setPlainText('这是无关的待测文本')
        window.editor.setText('雨下整夜')
        window.refresh()
        assert '我' in window.candidates and window.code == 'phshhpn'
        visual = next(button.visual for button in window.buttons if button.visual and button.visual['character'] == '我')
        assert visual['states'][4] == 'correction' and '第5笔' in visual['status']
        assert '#DC2626' in window.composition.text()
        window.show(); app.processEvents()
        assert window.grab().save(str(tmp_path / 'single-stroke-correction.png'))
        image = QImage.fromData(base64.b64decode(native_glyph_images([visual])[0]['png']), 'PNG')
        assert any(image.pixelColor(x, y).name() == '#dc2626'
                   for y in range(image.height()) for x in range(image.width()))
        window.fuzzy_toggle.setChecked(False)
        assert not window.candidates and '#DC2626' not in window.composition.text()
        window.fuzzy_toggle.setChecked(True)
        assert '我' in window.candidates
        window.undo()
        assert window.code == 'phshhp' and len(window.evidence) == 6
        assert any(button.visual and button.visual['states'][4] == 'correction' for button in window.buttons)
        window.commit(window.candidates.index('我'))
        assert window.editor.text() == '雨下整夜我' and not window.code and not window.evidence
        assert all('correction' not in button.visual['states'] for button in window.buttons if button.visual)
    finally:
        window.close(); app.processEvents()


def test_status_log_shows_each_recognition_without_taking_input_focus(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    app, window = load_window(tmp_path, monkeypatch)
    from stroke_trial_status_log import StatusLogWindow
    log = StatusLogWindow(window)
    window.status_log = log
    try:
        # The trial pauses ring input when it loses focus, so the log must never take it.
        assert log.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert log.testAttribute(Qt.WA_ShowWithoutActivating)
        window.record('stats', packets=1)
        window.record('contact_samples', samples=[])
        assert log.view.toPlainText() == ''
        window.record('contact_intent', sdk_is_click=True, intent_decision=dict(
            action='tap', reason='stationary-release', duration_ms=60., path_length=.4))
        window.record('contact_intent', sdk_is_click=False, intent_decision=dict(
            source='two-finger', phase='selecting', direction='right'))
        window.record('contact_intent_state', phase='idle', progress=0, direction=None)
        window.record('contact_intent_state', phase='writing', progress=0, direction=None)
        window.record('stroke', result=dict(shape='横折', distance=.0123), recognition_ms=2.5, code='hz',
                      fuzzy_candidates=[dict(char='口'), dict(char='日')])
        window.record('contact_decision', decision=dict(action='delete-one'))
        window.record('contact_decision', decision=dict(action='stroke', baselineAction='stroke'))
        window.record('hold_selection', decision=dict(action='select', target=1, direction='right', source='two-finger'))
        window.record('tap_confirmation', allowed=False, reason='commit-cooldown')
        window.record('commit', character='我', code='hz')
        lines = log.view.toPlainText().splitlines()
        assert [line.split('  ', 1)[1] for line in lines] == [
            '[接触] 单指 · 轻触 · 静止抬手 · 60 ms · 路径 0.4 · SDK点击',
            '[接触] 双指 · 选字手势 · 右 · SDK非点击',
            '[阶段] 单指 · 书写中',
            '[笔画] 横折 · 距离 0.0123 · 2.5 ms · 笔序 横折 · 候选 口 日',
            '[手势] 退一笔',
            '[选字] 双指 · 选字 · 右 · 第2候选',
            '[轻触] 已拦截 · 出字后冷却中',
            '[出字] 「我」 · 笔序 横折',
        ]
        # A malformed event is shown; it must not interrupt the input flow calling record().
        window.record('stroke', result=None)
        assert '[错误] stroke 无法显示' in log.view.toPlainText().splitlines()[-1]
    finally:
        window.close(); app.processEvents()


@pytest.mark.parametrize('original_dictionary', [False, True], ids=['filtered', 'original'])
def test_synthetic_sdk_release_runs_complete_window_and_shutdown(tmp_path, monkeypatch, original_dictionary):
    import time
    if original_dictionary:
        import proximic_ring.touchpad.stroke.stroke_dictionary_orders as orders
        monkeypatch.setattr(orders, 'filter_order_variants', lambda entries: entries)
    app, window = load_window(tmp_path, monkeypatch)
    import collect_stroke_samples
    outputs = []
    create_output = collect_stroke_samples.RingWorker.create_output
    def capture_output(worker, ended):
        output = create_output(worker, ended)
        outputs.append(output)
        return output
    def unexpected_network(*args, **kwargs):
        raise AssertionError('synthetic SDK playback must not create an event loop or BLE session')
    monkeypatch.setattr(collect_stroke_samples.RingWorker, 'create_output', capture_output)
    monkeypatch.setattr(collect_stroke_samples.asyncio, 'run', unexpected_network)
    monkeypatch.setattr(collect_stroke_samples, 'RingSession', unexpected_network)
    assert ('hhsszh' in {row[1] for row in window.dictionary.by_character['舌']}) == original_dictionary
    window.synthetic = True
    try:
        window.show()
        app.setActiveWindow(window)
        app.processEvents()
        window.toggle()
        deadline = time.monotonic()+5
        while not window.code and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.01)
        assert window.code == 'h' and window.candidates[0] == '一'
        assert window.evidence and len(window.trace_view.points) >= 5
        assert window.evidence[-1]['kinematics']['timing'] == 'confirmed_contact_frames'
        assert window.evidence[-1]['kinematics']['duration_ms'] > 0
        assert window.evidence[-1]['adaptation']['baseLevel'] == 50
    finally:
        window.close()
        deadline = time.monotonic()+5
        while window.worker is not None and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.01)
        assert window.worker is None
        assert len(outputs) == 1 and not outputs[0].thread.is_alive()
        assert outputs[0].stopped.is_set()
        diagnostics = window.contact_diagnostics
        assert diagnostics.closed and not diagnostics.thread.is_alive()
        assert not diagnostics.error and not diagnostics.dropped
        rows = [json.loads(line) for line in diagnostics.path.read_text('utf-8').splitlines()]
        assert any(row['kind'] == 'processor_attached' for row in rows)
        assert any(row['kind'] == 'collector_result' and row['accepted'] for row in rows)
        assert rows[-1]['kind'] == 'session_end'
        window.close()
        app.processEvents()


def test_trial_focus_blocks_background_and_stale_callbacks_preserving_composition(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QWidget
    app, window = load_window(tmp_path, monkeypatch)
    other = QWidget()
    try:
        prepare(window)
        window.show()
        app.setActiveWindow(window)
        app.processEvents()
        first_epoch = window._focus_since
        assert first_epoch is not None
        window.editor.setText('正文')
        window.add_manual('s')
        window.show_selection_state(dict(phase='selecting', progress=100, direction='right'))
        window.trace_view.show_trace([(0, 0), (10, 0)])
        other.show()
        app.setActiveWindow(other)
        app.processEvents()
        assert window._focus_since is None
        assert window.selection_state['phase'] == 'idle'
        assert not window.trace_view.points
        for kind, value in [('tap', {}), ('stroke', dict(points=trace(*([(1, 0)]*20)))),
                            ('selection', dict(decision=dict(action='punctuation', target='，'))),
                            ('selection_state', dict(phase='selecting', progress=100, direction='right')),
                            ('trace', [(0, 0), (10, 0)])]:
            window.receive_focused_input((first_epoch, kind, value))
        assert window.code == 's' and window.editor.text() == '正文'
        assert window.selection_state['phase'] == 'idle' and not window.trace_view.points
        app.setActiveWindow(window)
        app.processEvents()
        new_epoch = window._focus_since
        assert new_epoch is not None and new_epoch != first_epoch
        window.receive_focused_input((first_epoch, 'tap', {}))
        assert window.code == 's' and window.editor.text() == '正文'
        window.editor.setFocus()
        app.processEvents()
        assert window._focus_since == new_epoch
        window.receive_focused_input((new_epoch, 'tap', {}))
        assert not window.code and window.editor.text().endswith('小')
        window.add_manual('s')
        before = window.editor.text()
        window.showMinimized()
        app.processEvents()
        assert window._focus_since is None
        window.receive_focused_input((new_epoch, 'tap', {}))
        assert window.code == 's' and window.editor.text() == before
        window.showNormal()
        app.setActiveWindow(window)
        app.processEvents()
        assert window._focus_since is not None and window._focus_since != new_epoch
        window.hide()
        assert window._focus_since is None
    finally:
        other.close()
        window.close()
        app.processEvents()


def test_short_first_page_is_filled_with_marked_guesses(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        prepare(window)
        window.fill_toggle.setChecked(True)
        window.accept_stroke(dict(points=trace(*([(0,2)]*30))))
        assert window.code == 's' and window.candidates[0] == '小' and len(window.candidates) >= 2
        guesses = [r for r in window.matches if r.get('match_type') == 'tolerant']
        assert all(r['char'] in window.candidates for r in guesses)
        window.fuzzy_toggle.setChecked(False)
        assert window.candidates == ['小']  # strict mode is never filled
        window.fuzzy_toggle.setChecked(True)
        window.fill_toggle.setChecked(False)
        assert not any(r.get('match_type') == 'tolerant' for r in window.matches)
    finally:
        window.close()
        app.processEvents()


def test_guess_is_drawn_marked_and_not_learned(tmp_path, monkeypatch):
    app, window = load_window(tmp_path, monkeypatch)
    try:
        window.dictionary.recognize = lambda points: dict(category='z', shape='横折', distance=.001,
            category_scores=[dict(category='z', distance=.001), dict(category='h', distance=.5)])
        for _ in range(9):
            window.accept_stroke(dict(points=trace(*([(2,0)]*15+[(0,2)]*15))))
        assert len(window.candidates) >= 6
        guesses = {r['char'] for r in window.matches if r.get('match_type') == 'tolerant'}
        assert guesses and guesses <= set(window.candidates)
        index = next(i for i, c in enumerate(window.candidates[:6]) if c in guesses)
        window.selected = index
        window.render_candidates()
        visual = window.buttons[index].visual
        assert visual['guess'] and visual['status'].startswith('猜')
        before = window.personal_model.snapshot()
        window.commit(index)
        assert window.personal_model.snapshot() == before
    finally:
        window.close()
        app.processEvents()
