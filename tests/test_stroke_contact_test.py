"""SDK observations must preserve main's detector results, with no extra gate."""
import subprocess
import ast
import inspect
import pytest
from proximic_ring.stroke_input import StrokeCollector
from ring_python_sdk.touchpad import TouchpadMove, TouchpadContact
from ring_python_sdk.touchpad.core import ClickDetector


def test_main_detector_rules_are_identical_apart_from_publishing_contact_edges():
    main = subprocess.check_output(['git', 'show', '27c773d:src/ring_python_sdk/touchpad/core.py'],
                                   text=True, encoding='utf-8')
    main_class = next(node for node in ast.parse(main).body
                      if isinstance(node, ast.ClassDef) and node.name == 'ClickDetector')
    local_class = ast.parse(inspect.getsource(ClickDetector)).body[0]
    class RemoveObservations(ast.NodeTransformer):
        def visit_Assign(self, node):
            if any((isinstance(target, ast.Attribute) and target.attr in {'contact_edges','contact_verdicts'})
                   or (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute)
                       and target.value.attr == 'contact_verdicts')
                   for target in node.targets):
                return None
            return self.generic_visit(node)
        def visit_Expr(self, node):
            call = node.value
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Attribute)
                    and call.func.value.attr in {'contact_edges','contact_verdicts'}):
                return None
            return self.generic_visit(node)
    assert ast.dump(RemoveObservations().visit(local_class)) == ast.dump(main_class)


@pytest.mark.parametrize('duration', [12,35,100,250])
def test_main_detector_clicks_and_transition_frames_are_unchanged(duration):
    source = subprocess.check_output(['git','show','27c773d:src/ring_python_sdk/touchpad/core.py'],
                                     text=True,encoding='utf-8')
    namespace = {}; exec(compile(source,'main-core','exec'),namespace)
    main, local = namespace['ClickDetector'](), ClickDetector()
    for step, probability in enumerate([.01]*25+[.99]*duration+[.01]*25):
        assert main.movement(step,(0.,0.)) == local.movement(step,(0.,0.))
        assert main.probability(step,probability) == local.probability(step,probability)
        assert (main.start,main.transition) == (local.start,local.transition)
        local.contact_edges.clear()


def test_high_probability_without_confirmed_contact_does_not_start_a_stroke():
    collector=StrokeCollector()
    for step in range(100): collector.feed(TouchpadMove(5,10,.999,step,0))
    assert not collector.points and not collector.touching


def test_confirmed_stroke_is_not_split_by_probability_noise():
    collector=StrokeCollector()
    collector.feed(TouchpadContact('down',0,0))
    for step in range(20): collector.feed(TouchpadMove(1,0,.001,step,0))
    result=collector.feed(TouchpadContact('up',20,0))
    assert result[-1] == (19.,0.)


def test_gap_or_reset_discards_incomplete_stroke():
    collector=StrokeCollector()
    collector.feed(TouchpadContact('down',0,0))
    collector.feed(TouchpadMove(1,0,.99,0,0))
    collector.feed(TouchpadMove(1,0,.99,2,0))
    assert not collector.points and not collector.touching
    collector.feed(TouchpadContact('down',3,0))
    collector.feed(TouchpadMove(1,0,.99,3,0))
    collector.feed(TouchpadContact('reset',3,0))
    assert not collector.points and not collector.touching
