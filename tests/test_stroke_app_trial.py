"""The standalone trial's left side inside the app: input task and candidate pipeline."""
import json

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_stroke_session import until
from test_touchpad_page_layout import open_trial


def start(inline_ui, size=(1280, 900)):
    c, bridge, window, tp, source = open_trial(inline_ui, size)
    window.findChild(QObject, 'strokeTestEditor').forceActiveFocus()
    tp.setLocalStrokeInput(True)
    tp.setLocalStrokeFocus(True)
    QTest.qWait(20)
    return window, tp


def write(tp, code, char):
    """Type a character's strokes by button and pick it from whichever page holds it."""
    for stroke in code:
        tp.addStroke(stroke)
    for _ in range(tp.candidatePageCount):
        if char in tp.strokeCandidates:
            tp.selectCandidate(tp.strokeCandidates.index(char))
            return
        tp.nextCandidates()
    raise AssertionError(char + ' is not a candidate for ' + code)


def test_tap_settings_and_local_candidate_plus_space(inline_ui, tmp_path):
    import os
    from pathlib import Path
    from PySide6.QtCore import Q_ARG, QMetaObject
    window, tp = start(inline_ui)
    editor = window.findChild(QObject, 'strokeTestEditor')
    single = window.findChild(QObject, 'strokeSingleTapAction')
    double = window.findChild(QObject, 'strokeTwoFingerTapAction')
    try:
        assert (tp.singleFingerAction, tp.twoFingerAction) == ('space', 'candidate')
        assert single.property('currentIndex') == 0 and double.property('currentIndex') == 1
        tp.addStroke('h')
        first = tp.strokeCandidates[0]
        tp._perform_tap(1)
        assert editor.property('text') == first + ' ' and not tp._code
        single.setProperty('currentIndex', 1)
        QMetaObject.invokeMethod(single, 'activated', Q_ARG(int, 1))
        double.setProperty('currentIndex', 0)
        QMetaObject.invokeMethod(double, 'activated', Q_ARG(int, 0))
        assert (tp.singleFingerAction, tp.twoFingerAction) == ('candidate', 'space')
        assert tp.owner._settings.value('touchpad/singleFingerAction') == 'candidate'
        assert tp.owner._settings.value('touchpad/twoFingerAction') == 'space'
        tp.singleFingerAction = 'invalid'
        assert tp.singleFingerAction == 'candidate'
        tp.addStroke('s')
        expected = first + ' ' + tp.strokeCandidates[0]
        tp._perform_tap(1)
        assert editor.property('text') == expected
        output = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
        output.mkdir(parents=True, exist_ok=True)
        QTest.qWait(30)
        assert window.grabWindow().save(str(output / 'tap-settings.png'))
    finally:
        tp.close()
        QTest.qWait(20)


def test_two_finger_press_and_delete_have_distinct_feedback(inline_ui, tmp_path):
    import os
    from pathlib import Path
    window, tp = start(inline_ui)
    output = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    menu = window.findChild(QObject, 'strokeSelectionMenu')
    pull = window.findChild(QObject, 'strokeClearPull')
    try:
        tp.addStroke('h')
        window.findChild(QObject, 'touchpadViewport').setProperty('contentY', 300)
        QTest.qWait(30)
        box = window.findChild(QObject, 'strokeCompositionPanel')
        assert tp.strokeGestureLabel + '退出' in window.findChild(QObject, 'strokeTrialHelp').property('text')
        geometry = (menu.mapToScene(QPointF()), menu.width(), menu.height())
        for phase, direction, clearing, progress, distance, color, name, hint in [
                ('selecting', None, False, 0, 0, '#dde8ff', 'two-finger', '已识别双指'),
                ('selecting', 'left', False, 0, 6, '#fbe4e2', 'delete', '继续外拉可清空'),
                ('selecting', 'left', False, .5, 24, '#fbe4e2', 'pulling', '继续外拉可清空'),
                ('selecting', 'left', True, 1, 30, '#f6c6c2', 'clear', '抬手清空'),
                ('selecting', 'left', False, 0, 18, '#fbe4e2', 'retracted', '继续外拉可清空'),
                ('idle', None, False, 0, 0, '#f3f6fc', 'writing', '双指滑向格子')]:
            tp._selection_state = dict(phase=phase, direction=direction, source='two-finger', clear_armed=clearing,
                clear_progress=progress, threshold=6, clear_distance=30,
                display=dict(origin=[0, 0], points=[[0, 0], [-distance, 0]]))
            tp.selectionChanged.emit()
            QTest.qWait(30)
            assert menu.property('selecting') == (phase == 'selecting')
            assert menu.property('color').name() == color
            assert pull.property('visible') == (phase == 'selecting' and direction == 'left')
            assert menu.property('clearProgress') == progress
            assert (menu.mapToScene(QPointF()), menu.width(), menu.height()) == geometry
            assert pull.mapToScene(QPointF()).x() >= 0
            assert hint in box.property('selectionHint')
            assert window.grabWindow().save(str(output / ('trial-' + name + '.png')))
    finally:
        tp.close()
        QTest.qWait(20)


def test_local_backspace_continues_before_inserted_text_and_removes_selection(inline_ui):
    from PySide6.QtCore import Q_ARG, QMetaObject
    window, tp = start(inline_ui)
    editor = window.findChild(QObject, 'strokeTestEditor')
    try:
        editor.setProperty('text', '旧🙂后')
        editor.setProperty('cursorPosition', 3)
        tp.localCharacterCommitted.emit('新')
        tp.addStroke('h')
        tp.deleteOne()
        assert editor.property('text') == '旧🙂新后' and not tp._code
        for expected in ['旧🙂后', '旧后', '后', '后']:
            tp.deleteOne()
            assert editor.property('text') == expected
        editor.setProperty('text', '前选中文字后')
        QMetaObject.invokeMethod(editor, 'select', Q_ARG(int, 1), Q_ARG(int, 5))
        tp.deleteOne()
        assert editor.property('text') == '前后'
    finally:
        tp.close()
        QTest.qWait(20)


def test_local_long_pull_clears_ink_then_deletes_prefix_preserving_suffix(inline_ui):
    from PySide6.QtCore import Q_ARG, QMetaObject
    window, tp = start(inline_ui)
    editor = window.findChild(QObject, 'strokeTestEditor')
    menu = window.findChild(QObject, 'strokeSelectionMenu')
    try:
        editor.setProperty('text', '前🙂文后面')
        editor.setProperty('cursorPosition', 4)
        tp.addStroke('h')
        assert menu.property('longPullAction') == '清空笔画'
        tp.clearStrokesOrDeleteBefore()
        assert editor.property('text') == '前🙂文后面' and not tp._code
        assert menu.property('longPullAction') == '删除前文'
        tp.clearStrokesOrDeleteBefore()
        assert editor.property('text') == '后面' and editor.property('cursorPosition') == 0
        tp.clearStrokesOrDeleteBefore()
        assert editor.property('text') == '后面'
        editor.setProperty('text', '前选中后')
        QMetaObject.invokeMethod(editor, 'select', Q_ARG(int, 1), Q_ARG(int, 3))
        tp.clearStrokesOrDeleteBefore()
        assert editor.property('text') == '后'
    finally:
        tp.close()
        QTest.qWait(20)


def test_sentence_trial_clears_the_editor_tracks_progress_and_saves_a_report(inline_ui, tmp_path):
    window, tp = start(inline_ui)
    editor = window.findChild(QObject, 'strokeTestEditor')
    try:
        tp.trial_report_dir = tmp_path
        codes = {char: code for char, code, *_ in tp._stroke_dictionary().entries if char in '一二'}
        tp.localCharacterCommitted.emit('旧')
        assert editor.property('text') == '旧' and not tp.trialActive
        assert '自由书写' in tp.trialPreview
        tp.setTrialTarget('一二。')
        tp.startTrial(); QTest.qWait(20)
        assert tp.trialActive and editor.property('text') == '' and editor.property('readOnly')
        assert '当前第 1 字：一' in tp.trialHint and tp.trialProgress == 0
        write(tp, codes['一'], '一')
        assert editor.property('text') == '一' and tp.trialActive
        assert '当前第 2 字：二' in tp.trialHint and tp.trialProgress == 33
        # The next expected character is highlighted; the typed one is green.
        assert 'color:#1a936f' in tp.trialPreview and 'background-color:#dbe5ff' in tp.trialPreview
        write(tp, codes['二'], '二')
        tp.insertPunctuation('。'); QTest.qWait(20)
        # Matching the target ends the trial by itself and freezes the report.
        assert not tp.trialActive and tp.trialFinished and tp.trialProgress == 100
        assert not editor.property('readOnly')
        reports = list(tmp_path.glob('trial-*.json'))
        assert len(reports) == 1 and reports[0].with_suffix('.csv').is_file()
        report = json.loads(reports[0].read_text(encoding='utf-8'))
        assert report['completed'] and report['finish_reason'] == 'completed' and report['actual'] == '一二。'
        assert report['contains_manual_strokes'] and report['delivered_commits'] == 3
        assert [e['character'] for e in report['events'] if e['kind'] == 'commit'] == ['一', '二', '。']
        assert '完成' in tp.trialHint and str(reports[0]) in tp.trialHint
        assert window.findChild(QObject, 'strokeTrialStart').property('text') == '重新开始实验'
        # Free writing continues after the trial without touching the frozen report.
        tp.localCharacterCommitted.emit('三')
        assert editor.property('text') == '一二。三' and tp.trialProgress == 100
    finally:
        tp.close()
        QTest.qWait(20)


def test_manual_finish_saves_an_incomplete_trial(inline_ui, tmp_path):
    window, tp = start(inline_ui)
    try:
        tp.trial_report_dir = tmp_path
        tp.setTrialTarget('一二')
        tp.startTrial()
        tp.addStroke('h'); tp.undoStroke()
        tp.finishTrial()
        report = json.loads(next(tmp_path.glob('trial-*.json')).read_text(encoding='utf-8'))
        assert not report['completed'] and report['finish_reason'] == 'manual'
        assert [e['kind'] for e in report['events']] == ['manual_stroke', 'undo_stroke']
        assert '已结束（未完成）' in tp.trialHint
    finally:
        tp.close()
        QTest.qWait(20)


def test_joined_fan_is_three_contacts_and_four_strokes_in_the_app(inline_ui, monkeypatch, tmp_path):
    c, _, window, tp, source = open_trial(inline_ui, (1280, 900))
    editor = window.findChild(QObject, 'strokeTestEditor')
    tp.setLocalStrokeInput(True)
    editor.forceActiveFocus()
    tp.setLocalStrokeFocus(True)
    tp.setInputMode('stroke')
    tp.start()
    until(lambda: bool(source.starts))
    source.future.set_result(None)
    until(lambda: tp.state == 'running')
    learn_calls = []
    monkeypatch.setattr(tp, '_learn_writer', lambda *args: learn_calls.append(args))
    try:
        tp.trial_report_dir = tmp_path
        tp.setTrialTarget('反')
        tp.startTrial()
        model = tp._joined_model
        index = model.labels.index('fan')
        points = (model.bank[index, :2].T * 40).tolist()

        def draw_joined():
            tp._run.output.options['on_stroke'](points)
            until(lambda: bool(tp._stroke_evidence))
            assert len(tp._stroke_evidence) == 1
            assert any(option['family'] == 'fan' for option in tp._stroke_evidence[0]['joined_options'])

        draw_joined()
        tp.undoStroke()
        assert not tp.strokeTokens and not tp._stroke_evidence
        draw_joined()
        tp.addStroke('z')
        tp.addStroke('n')
        assert len(tp.strokeTokens) == 3 and '反' in tp.strokeCandidates
        visual = next(v for v in tp.strokeCandidateVisuals if v['character'] == '反')
        assert visual['entered'] == 4 and visual['available']
        assert visual['states'][:2] == ['fuzzy', 'fuzzy'] and '连笔 1→2' in visual['status']
        tp.selectCandidate(tp.strokeCandidates.index('反'))
        assert editor.property('text') == '反' and not tp.strokeTokens and not learn_calls
        report = json.loads(next(tmp_path.glob('trial-*.json')).read_text(encoding='utf-8'))
        assert report['completed'] and report['joined_contacts_unscored'] == 2
        assert report['scored_strokes'] == 0
        event = next(e for e in report['events'] if e['kind'] == 'joined_stroke_commit')
        assert event['contacts'] == [0] and event['logical_prefix'] == 'ppzn'
    finally:
        tp.close()
        QTest.qWait(20)


def test_whole_character_repair_and_page_filling_guesses_reach_the_menu(inline_ui):
    from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
    window, tp = start(inline_ui)
    try:
        # 我 is p h s h z p n; the fifth stroke is written as the wrong class.
        for stroke in 'phshhpn':
            tp.addStroke(stroke)
        for _ in range(100):
            QTest.qWait(30)
            if any(row.get('match_type') == 'whole_character' for row in tp._candidate_matches):
                break
        repaired = [row['char'] for row in tp._candidate_matches if row.get('match_type') == 'whole_character']
        assert '我' in repaired and '我' in tp.strokeCandidates
        visual = tp.strokeCandidateVisuals[tp.strokeCandidates.index('我')]
        assert visual['status'].startswith('整字纠错')
        # A repair is not evidence of how this user writes.
        learned = []
        tp._learn_writer = lambda *args: learned.append(args)
        tp.selectCandidate(tp.strokeCandidates.index('我'))
        assert window.findChild(QObject, 'strokeTestEditor').property('text') == '我' and not learned
        # Recognized (not manual) strokes with too few matches are topped up to a full page.
        tp._code = 'zzzzzzzz'
        tp._stroke_evidence = [dict(stroke_evidence('z'), manual=False) for _ in tp._code]
        tp._refresh_candidates()
        assert len(tp.strokeCandidates) == tp.candidatePageSize
        guesses = [v for v in tp.strokeCandidateVisuals if v.get('guess')]
        assert guesses and all(v['status'].startswith('猜') for v in guesses)
        tokens = tp.strokeTokens
        assert len(tokens) == len(tp._code)
    finally:
        tp.close()
        QTest.qWait(20)


def test_next_page_wraps_and_context_uses_the_trial_models(inline_ui):
    window, tp = start(inline_ui)
    try:
        tp.addStroke('h')
        assert tp.candidatePageCount > 1
        for _ in range(tp.candidatePageCount - 1):
            tp.nextCandidates()
        assert not tp.moreCandidates and tp.candidatePage == tp.candidatePageCount - 1
        tp.nextCandidates()
        assert tp.candidatePage == 0
        assert tp._context_model().model == 'wanxiang'
        assert tp._stroke_dictionary().stroke_vectors.scope == 'prefix'
    finally:
        tp.close()
        QTest.qWait(20)


def test_selection_card_fits_the_viewport_at_each_window_proportion(inline_ui, tmp_path):
    import os
    from pathlib import Path
    window, tp = start(inline_ui, (1440, 940))
    output = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    try:
        tp.addStroke('p'); tp.addStroke('s')
        viewport = window.findChild(QObject, 'touchpadViewport')
        scrollbar = window.findChild(QObject, 'touchpadScrollBar')
        for width, height in [(1440, 940), (1280, 720), (1024, 600), (940, 1000), (940, 560)]:
            window.resize(width, height)
            QTest.qWait(150)
            menu = window.findChild(QObject, 'strokeSelectionMenu')
            card = window.findChild(QObject, 'strokeSelectionColumn')
            tutorial = window.findChild(QObject, 'strokeTutorial')
            assert abs(menu.width() - menu.height()) < 1  # Always square.
            assert menu.width() >= min(260, card.width() - 28)
            assert card.height() <= max(viewport.height(), menu.height() + 170), (width, height)
            assert tutorial.mapToScene(QPointF(tutorial.width(), 0)).x() < menu.mapToScene(QPointF()).x()
            grid = window.findChild(QObject, 'strokeCandidateRow')
            cells = {item.objectName(): item for item in grid.childItems()}
            for name in ('strokeCandidate0', 'strokeDeleteCell'):
                cell = cells[name]
                body = cell.property('contentItem')
                assert body.width() == cell.width() and body.height() == cell.height(), (name, width)
            scroll_left = scrollbar.mapToScene(QPointF()).x()
            for name in ('strokeTaskCard', 'strokeSelectionColumn', 'strokeSearchShell', 'strokeTrialTarget',
                         'strokeTrialStart', 'strokeTrialFinish', 'strokeNextPage', 'strokeSelectionMenu'):
                item = window.findChild(QObject, name)
                left = item.mapToScene(QPointF()).x()
                assert left >= 0 and left + item.width() <= scroll_left, (name, width)
            top = window.findChild(QObject, 'strokeStatusRow') if viewport.height() >= 600 else card
            top_y = top.mapToItem(viewport.property('contentItem'), QPointF()).y()
            viewport.setProperty('contentY', max(0, min(top_y, viewport.property('contentHeight') - viewport.height())))
            QTest.qWait(80)
            assert window.grabWindow().save(str(output / f'stroke-card-{width}x{height}.png'))
            viewport.setProperty('contentY', 0)
    finally:
        tp.close()
        QTest.qWait(20)


def test_tutorial_buttons_close_and_reopen_without_stealing_editor_focus(inline_ui):
    window, tp = start(inline_ui, (940, 560))
    try:
        tutorial = window.findChild(QObject, 'strokeTutorial')
        close_button = window.findChild(QObject, 'strokeTutorialClose')
        open_button = window.findChild(QObject, 'strokeTutorialOpen')
        editor = window.findChild(QObject, 'strokeTestEditor')
        card = window.findChild(QObject, 'strokeSelectionColumn')
        viewport = window.findChild(QObject, 'touchpadViewport')
        viewport.setProperty('contentY', card.mapToItem(viewport.property('contentItem'), QPointF()).y())
        QTest.qWait(80)

        def click(item):
            center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center)
            QTest.qWait(80)

        assert tutorial.property('visible') and not open_button.property('visible')
        for name in ('strokeTutorialWrite', 'strokeTutorialSelect', 'strokeTutorialClear', 'strokeTutorialZigzag'):
            assert window.findChild(QObject, name) is not None, name
        click(close_button)
        assert not tutorial.property('visible') and open_button.property('visible')
        assert tp.owner._settings.value('touchpad/strokeTutorialOpen', True, type=bool) is False
        assert editor.property('activeFocus')
        click(open_button)
        assert tutorial.property('visible') and not open_button.property('visible')
        assert tp.owner._settings.value('touchpad/strokeTutorialOpen', False, type=bool) is True
        assert editor.property('activeFocus')
    finally:
        tp.close()
        QTest.qWait(20)
