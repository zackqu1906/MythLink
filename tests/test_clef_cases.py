import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.clef_cases import DEFAULT_CASES, load_cases


@pytest.fixture
def sample():
    return {"id": "custom-case", "utterance": "把周二改成周三", "target": "周二开会。",
            "context": {"messages": [{"speaker": "other", "text": "不要改变地点。"}]},
            "expected": {"task": "edit", "tasks": ["modify"]}}


def save(tmp_path, cases):
    path = tmp_path / "my-cases.json"
    path.write_text(json.dumps(cases, ensure_ascii=False))
    return path


def test_custom_file_preserves_all_model_inputs_and_selects_by_id(tmp_path, sample):
    another = copy.deepcopy(sample)
    another["id"] = "another-case"
    path = save(tmp_path, [sample, another])
    assert load_cases(path, ["another-case"], require_plan=True) == [another]
    with pytest.raises(ValueError, match="未知样例"):
        load_cases(path, ["missing"])


@pytest.mark.parametrize("mutation", [
    lambda c: c.pop("utterance"),
    lambda c: c.pop("target"),
    lambda c: c.pop("context"),
    lambda c: c.update(context={"messages": "not a list"}),
    lambda c: c.update(context={"messages": [{"text": "missing speaker"}]}),
    lambda c: c["expected"].update(task="unknown"),
    lambda c: c["expected"].update(tasks=["typo"]),
    lambda c: c["expected"].update(tasks=["modify", "modify"]),
    lambda c: c["expected"].update(task="dictation"),
])
def test_invalid_cases_fail_before_any_model_request(tmp_path, sample, mutation):
    mutation(sample)
    with pytest.raises(ValueError):
        load_cases(save(tmp_path, [sample]))


def test_duplicates_and_empty_suite_are_rejected(tmp_path, sample):
    with pytest.raises(ValueError, match="唯一"):
        load_cases(save(tmp_path, [sample, sample]))
    with pytest.raises(ValueError, match="非空"):
        load_cases(save(tmp_path, []))


def test_primary_only_labels_are_not_silently_scored_as_empty_edit_plan(tmp_path, sample):
    sample["expected"] = {"task": "edit"}
    path = save(tmp_path, [sample])
    assert load_cases(path) == [sample]
    with pytest.raises(ValueError, match="缺少具体任务标签"):
        load_cases(path, require_plan=True)


@pytest.mark.parametrize("script", ["check_clef_primary.py", "check_clef_router.py"])
def test_custom_case_request_export_uses_inputs_without_leaking_expected_label(tmp_path, sample, script):
    path = save(tmp_path, [sample])
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "tools" / script), "--cases", str(path),
                             "--case", sample["id"], "--dump-request"], capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    bodies = [data[0]["request"]] if isinstance(data, list) else [r["body"] for r in data["requests"]]
    for body in bodies:
        assert set(body) == {"model", "state", "questions"}
        assert sample["utterance"] in body["state"] and sample["target"] in body["state"]
        assert sample["context"]["messages"][0]["text"] in body["state"]
        assert "expected" not in body["state"]


def test_bundled_case_files_are_valid():
    assert load_cases(DEFAULT_CASES.with_name("clef_joint_holdout_cases.json"), require_plan=True)
    assert load_cases(DEFAULT_CASES, require_plan=True)
    assert load_cases(DEFAULT_CASES.with_name("clef_primary_validation_cases.json"))
    assert load_cases(DEFAULT_CASES.with_name("clef_primary_unseen_cases.json"))
    assert load_cases(DEFAULT_CASES.with_name("clef_cases.template.json"), require_plan=True)


def test_removed_hints_cannot_silently_enter_new_expected_contract(tmp_path, sample):
    sample['expected']['hints'] = ['character_clarification']
    with pytest.raises(ValueError, match='hints 已移除'):
        load_cases(save(tmp_path, [sample]), require_plan=True)


def test_generation_checks_are_optional_and_preserve_information():
    from tools.clef_cases import generation_checks
    assert generation_checks({}, '任意正文')['passed'] is None
    assert generation_checks({'modified_text':'A'}, 'B')['passed'] is False
    expected={'preserve_text':['周三九点','周五十一点']}
    assert generation_checks(expected, '1.周三九点 2.周五十一点')['passed'] is True
    assert generation_checks(expected, '周三开会')['passed'] is False


@pytest.mark.parametrize('field,value', [('modified_text',[]), ('preserve_text','文字'), ('preserve_text',[3])])
def test_invalid_generation_expectations_fail_offline(tmp_path, sample, field, value):
    sample['expected'][field]=value
    with pytest.raises(ValueError):
        load_cases(save(tmp_path,[sample]))
