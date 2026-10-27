from pathlib import Path
import copy

from proximic_ring.touchpad.stroke.stroke_examples import EXAMPLES, stroke_example, task_example


def test_examples_cover_every_collected_shape_and_distinguish_three_hooks(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    from selection_capture import SHAPES
    from clear_fold_collection import inventory
    assert {shape for _,shape,_ in SHAPES} <= EXAMPLES.keys()
    assert '戈' in stroke_example('斜钩')
    assert '家' in stroke_example('弯钩')
    assert '心' in stroke_example('卧钩')
    tasks=inventory()
    before=copy.deepcopy(tasks)
    for task in tasks:
        assert bool(task_example(task)) == (task['kind']=='stroke')
    assert tasks==before
    assert '戈' in task_example(dict(kind='stroke',title='斜钩'))
    assert task_example(dict(kind='clear',title='横'))==''
    assert stroke_example('未知笔画')==''


