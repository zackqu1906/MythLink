from tools.clef_routing_report import write_routing_html
from proximic_ring.text_processing.task_routing import EDIT_TASK_LABELS, LEGACY_EDIT_HINT_LABELS


def test_report_includes_every_secondary_probability_and_marks_skipped_stage(tmp_path):
    names = [*EDIT_TASK_LABELS, *LEGACY_EDIT_HINT_LABELS]
    answers = {name: {"choice": "apply" if i == 0 else "skip",
                      "probabilities": {"apply": .9234 if i == 0 else .1234 + i * .01,
                                        "skip": .0766 if i == 0 else .8766 - i * .01}}
               for i, name in enumerate(names)}
    # Use a distinct primary pair so absence of secondary values is observable.
    primary = {"stage": "intent", "answers": {"voice_intent": {"probabilities": {"dictation": .01, "edit": .99}}}, "latency_s": .5}
    edit = {"id": "edit", "utterance": "<script>alert(1)</script>", "target": "旧内容", "context": {"messages": []},
            "expected": {"task": "edit", "tasks": ["modify"], "hints": []},
            "actual": {"task": "edit", "tasks": ["modify"], "hints": []}, "exact_match": True,
            "trace": {"task": "edit", "input_state": "<script>source</script>", "latency_s": 1.5,
                      "stages": [primary, {"stage": "edit_details", "answers": answers, "latency_s": 1.0}]}}
    dictation = {"id": "dictation", "utterance": "你好", "target": "", "context": {"messages": []},
                 "expected": {"task": "dictation"}, "actual": {"task": "dictation"}, "exact_match": True,
                 "trace": {"task": "dictation", "latency_s": .5, "stages": [
                     {"stage": "intent", "answers": {"voice_intent": {"probabilities": {"dictation": .99, "edit": .01}}}, "latency_s": .5}]}}
    report = {"cases": [edit, dictation], "metrics": {"samples": 2, "root_correct": 2, "exact_plan_correct": 2,
              "edit_samples": 1, "secondary_called": 1, "secondary_plan_correct": 1, "errors": 0,
              "latency_median_s": 1, "latency_p95_s": 1.5}}
    path = tmp_path / "report.html"
    write_routing_html(report, path)
    html = path.read_text()
    edit_section, dictation_section = html.split('<section>')[1:]
    for name, answer in answers.items():
        assert name in edit_section
        for p in answer["probabilities"].values():
            assert f"{p * 100:.2f}%" in edit_section
    assert "按项目流程跳过二级" in dictation_section
    assert "92.34%" not in dictation_section
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_secondary_error_is_not_rendered_as_skip_probabilities(tmp_path):
    case = {"id": "failed", "utterance": "删掉末句", "target": "末句", "expected": {"task": "edit", "tasks": ["modify"]},
            "exact_match": False, "error": "超时", "trace": {"task": "edit", "stages": []}}
    report = {"cases": [case], "metrics": {"samples": 1, "root_correct": 1, "exact_plan_correct": 0,
              "edit_samples": 1, "secondary_called": 1, "secondary_plan_correct": 0, "errors": 1,
              "latency_median_s": None, "latency_p95_s": None}}
    path = tmp_path / "report.html"
    write_routing_html(report, path)
    html = path.read_text()
    assert "未获得有效二级响应" in html and "超时" in html
    assert "0.00%" not in html and "100.00%" not in html


def test_joint_dictation_and_rejected_plan_show_all_task_probabilities(tmp_path):
    from tools.clef_routing_report import write_routing_html
    answers = {k: {'choice': 'skip', 'probabilities': {'apply': .1234, 'skip': .8766}} for k in EDIT_TASK_LABELS}
    answers['voice_intent'] = {'choice': 'dictation', 'probabilities': {'dictation': .9, 'edit': .1}}
    case = {'id': 'joint-literal', 'utterance': '<照录命令>', 'target': '', 'context': {'messages': []},
            'expected': {'task': 'dictation', 'tasks': []}, 'exact_match': False,
            'error': '联合分类矛盾', 'trace': {'classification_type': 'joint',
            'raw_model_task': 'dictation', 'stages': [{'stage': 'joint', 'answers': answers, 'latency_s': .2}], 'latency_s': .2}}
    report = {'cases': [case], 'metrics': {'root_correct': 1, 'samples': 1, 'exact_plan_correct': 0,
              'edit_samples': 0, 'edit_plan_correct': 0, 'requests': 1, 'contradictions': 1,
              'errors': 1, 'latency_median_s': .2, 'latency_p95_s': .2}}
    path = tmp_path/'joint.html'; write_routing_html(report, path)
    html=path.read_text()
    assert html.count('12.34%') == 6 and html.count('87.66%') == 6
    assert '90.00%' in html and '10.00%' in html
    assert '联合分类矛盾' in html and '&lt;照录命令&gt;' in html
    assert 'character_clarification' not in html and 'redictation' not in html
    # New policy displays a yellow final dictation while retaining the raw task.
    answers['modify'] = {'choice': 'apply', 'probabilities': {'apply': .8766, 'skip': .1234}}
    case.pop('error')
    case.update(actual={'task': 'dictation'}, exact_match=True, raw_exact_match=False)
    case['trace'].update(task='dictation', warning='听写优先，修改未执行', warning_level='yellow')
    report['metrics'].update(exact_plan_correct=1, raw_exact_plan_correct=0, errors=0, warnings=1)
    write_routing_html(report, path)
    html = path.read_text()
    assert 'class="warn"' in html and '校验提示（标黄）' in html
    assert '原始完整分类匹配 0/1' in html and '最终处理计划匹配 1/1' in html
    assert '原始 apply 任务：修改' in html
    assert html.count('12.34%') == 6 and html.count('87.66%') == 6
