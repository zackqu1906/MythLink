"""Offline integration checks; these do not assert model accuracy or speed."""
import json
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock

from tools import jev_intent_tester as tester
from tools.nimble_intent_backend import NimbleClient


def client_with(scores=None):
    client = NimbleClient.__new__(NimbleClient)
    client.scorer = Mock()
    client.scorer.score.return_value = {
        "model": "bespokelabs/Bespoke-Nimble-9B",
        "fields": {
            "intent": {"scores": scores or {"听写": 0.1, "编辑": 0.9}},
            "information_sufficient": {"scores": {"false": 0.2, "true": 0.8}},
        },
        "metrics": {"total_seconds": 0.125},
    }
    return client


def test_nimble_preserves_prompt_and_maps_results():
    client = client_with()
    case = tester.Case(id="correction", document="我在咖啡店和牛奶", utterance="喝牛奶")
    result = tester.evaluate_case(case, client, include_uncertainty=True)
    state, schema = client.scorer.score.call_args.args
    assert state == "已有文本：\n我在咖啡店和牛奶\n当前语句：\n喝牛奶"
    assert schema["intent"]["description"] == tester.split_prompt(tester.PROMPT_TEMPLATE)[0]
    assert schema["intent"]["choice_descriptions"] == tester.PROMPT_CRITERIA
    assert schema["intent"]["choices"] == ["听写", "编辑"]
    assert schema["information_sufficient"]["type"] == "boolean"
    assert result.predicted == "编辑"
    assert result.probabilities == {"听写": 0.1, "编辑": 0.9}
    assert result.confidence is None
    assert result.information_sufficient == 0.8
    assert result.model_compute_ms == 125
    with tempfile.TemporaryDirectory() as tmp:
        path, _ = tester.save_results([result], Path(tmp), "test")
        assert json.loads(path.read_text())["results"][0]["model_compute_ms"] == 125


def test_nimble_rejects_invalid_probabilities():
    for scores in ({"听写": float("nan"), "编辑": 0.9}, {"听写": 0.1},
                   {"听写": 0.8, "编辑": 0.9}):
        result = tester.evaluate_case(tester.Case(id="bad", utterance="你好"), client_with(scores))
        assert result.error
        assert result.predicted is None


def test_nimble_surfaces_token_limit_without_truncation():
    client = client_with()
    client.scorer.score.side_effect = ValueError("Longest prompt has 2049 tokens; limit is 2048")
    result = tester.evaluate_case(tester.Case(id="long", utterance="长文本"), client)
    assert "2049" in result.error


def test_nimble_missing_setup_exits_cleanly():
    stderr = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, redirect_stderr(stderr):
        code = tester.main(["--backend", "nimble", "--nimble-repo", tmp,
                            "--once", "-u", "喝牛奶", "--no-save"])
    assert code == 2
    assert "Nimble 初始化失败" in stderr.getvalue()


def test_default_still_uses_jev_and_jev_confidence():
    assert tester.build_parser().parse_args([]).backend == "jev"
    client = Mock()
    client.call.return_value = ({"model": "jev-latest", "answers": {"intent": {
        "choice": "编辑", "probabilities": {"听写": 0.01, "编辑": 0.99}, "confidence": 0.98,
    }}}, None)
    result = tester.evaluate_case(tester.Case(id="jev", utterance="喝牛奶"), client)
    assert result.confidence == 0.98
    assert result.model_compute_ms is None


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items()
                              if name.startswith("test_") and callable(value))
