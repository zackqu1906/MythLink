"""The complete-input trial (scripts/test-complete-stroke-input.cmd) in two-finger mode."""
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from proximic_ring.touchpad.stroke.stroke_context import StrokeContext
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_menu import TWO_FINGER_SELECTION, two_finger_selection
from ring_python_sdk.touchpad.events import Raw2PointerMove

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


@pytest.fixture
def trial(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(['two-finger-trial', '-platform', 'offscreen'])
    app.setStyle('Fusion')
    monkeypatch.syspath_prepend(str(TOOLS))
    import collect_stroke_samples
    import stroke_trial_menu
    monkeypatch.setattr(collect_stroke_samples, 'ROOT', tmp_path)
    stroke_trial_menu.use('two-finger')
    spec = importlib.util.spec_from_file_location('complete_trial_two_finger', TOOLS / 'test_complete_stroke_input.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    window = module.Window('offline', context_model='legacy')
    window.dictionary = StrokeDictionary(dict(templates=[], ranks={'小': 0, '你': 0},
        entries=[['小', 'spn', 1], ['你', 'pspzspn', 1]]))
    window.dictionary.recognize = lambda points: dict(category='s', shape='竖', distance=.001,
        category_scores=[dict(category='s', distance=.001)])
    window.predictor = StrokeContext(dict(maxContext=4, total=100, unigrams={'小': 50, '你': 50}, contexts={}))
    window.fill_toggle.setChecked(False)
    try:
        yield app, module, window
    finally:
        window.close()
        app.processEvents()
        stroke_trial_menu.use('hold')


def swipe(angle):
    distance = TWO_FINGER_SELECTION['selectDistance'] * 2
    return [(math.cos(math.radians(angle)) * distance * i / 20, math.sin(math.radians(angle)) * distance * i / 20)
            for i in range(21)]


def select(window, angle):
    points = swipe(angle)
    window.accept_selection(dict(points=points, decision=two_finger_selection(points, finished=True)))


def test_window_uses_product_menu_with_delete_cell(trial):
    app, module, window = trial
    assert len(window.buttons) == 5 and module.trial_menu.PAGE_SIZE == 5
    assert set(window.direction_buttons) == {'center', 'right', 'down-left', 'down', 'down-right',
                                             'up-left', 'up', 'up-right', 'left'}
    assert window.direction_buttons['left'].objectName() == 'deleteAction'
    assert '双指' in window.windowTitle() and window.selection_hint.text() == '双指滑向格子选字 · 双指左滑删除'
    assert '五' in window.fill_toggle.text()


def test_worker_runs_raw2_and_forwards_only_two_finger_moves(trial):
    _, module, _ = trial
    worker = module.RingWorker('offline', None)
    assert worker.protocol == 'raw2'
    output = SimpleNamespace(input_epoch=0, collector=None)
    worker.configure_output(output)
    assert output.two_finger_classifier is two_finger_selection
    steps = []
    worker.output = SimpleNamespace(submit_pointer=lambda event: steps.append(event.step))
    worker.on_pointer(Raw2PointerMove(1., 0., 0., 7, 0., mode='TWO_FINGER'))
    worker.on_pointer(SimpleNamespace(kind='click', step=8))
    assert steps == [7]


def test_two_finger_left_deletes_stroke_then_character_and_right_selects(trial):
    app, _, window = trial
    window.show(); app.processEvents()
    window.add_manual('s')
    assert window.code == 's'
    select(window, 180)
    assert window.code == '' and window.result.text() == '已退一笔'
    window.editor.setText('爱你')
    select(window, 180)
    assert window.editor.text() == '爱' and window.result.text() == '已删除'
    for category in 'spn':
        window.add_manual(category)
    assert window.candidates[0] == '小'
    select(window, 270)  # up → 。, discards the pending strokes
    assert window.editor.text() == '爱。' and window.code == ''
    window.add_manual('s')
    select(window, 90)  # down → fourth candidate, absent on this toy page
    assert window.code == 's' and '没有候选' in window.result.text()
    points = swipe(0)[:3]  # too short: releasing in place cancels
    window.accept_selection(dict(points=points, decision=two_finger_selection(points, finished=True)))
    assert window.code == 's' and '取消' in window.result.text()


def test_live_two_finger_state_highlights_cell_and_progress(trial):
    app, _, window = trial
    window.show(); app.processEvents()
    window.show_selection_state(two_finger_selection(swipe(180)))
    assert window.direction_buttons['left'].property('directionActive')
    assert window.selection_hint.text() == '双指左滑 · 抬手删除'
    assert window.selection_progress.value() == 100
    path, origin = window.trace_view.selection_geometry()
    left = window.direction_buttons['left']
    assert path and left.rect().contains(left.mapFromGlobal(window.trace_view.mapToGlobal(path[-1].toPoint())))
    window.show_selection_state(two_finger_selection(swipe(0)[:3]))
    assert window.selection_hint.text() == '双指滑向目标格子 · 原地抬手取消'
    assert not any(button.property('directionActive') for name, button in window.direction_buttons.items()
                   if name != 'center')
