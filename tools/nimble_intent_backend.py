"""Adapter for the upstream Nimble local scorers (no model download on import)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path


class NimbleClient:
    def __init__(self, repo: str, config_file: str | None, backend: str = "mlx"):
        repo_path = Path(repo).expanduser().resolve()
        if not (repo_path / "nimble/scoring/parallel_scorer.py").is_file():
            raise ValueError(f"找不到 Nimble 源码：{repo_path}；参见 tools/NIMBLE_TESTING.md")
        config_path = (Path(config_file).expanduser().resolve() if config_file else
                       repo_path / ".cache/nimble-model.json")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("model_id") != "bespokelabs/Bespoke-Nimble-9B":
            raise ValueError("配置必须指向 Bespoke-Nimble-9B，不能用未微调的 Qwen 冒充 Nimble")
        model_path = Path(config["model_path"]).expanduser()
        if not model_path.is_absolute():
            model_path = config_path.parent / model_path
        if not model_path.is_dir():
            raise ValueError(f"找不到本地模型：{model_path}")
        if (model_path / "adapter_config.json").exists():
            raise ValueError("请先合并基础模型和适配器，不能将适配器目录作为完整模型加载")
        sys.path.insert(0, str(repo_path))
        if backend == "mlx":
            from nimble.scoring.parallel_scorer import ParallelScorer as Scorer
        else:
            from nimble.scoring.cuda_scorer import CudaCandidateScorer as Scorer
        self.scorer = Scorer(
            model_path=str(model_path), model_id=config["model_id"],
            revision=config["revision"],
            max_input_tokens=min(int(config.get("max_input_tokens", 2048)), 2048),
        )

    def call(self, state: str, questions: dict) -> tuple[dict | None, str | None]:
        try:
            schema = {}
            for name, question in questions.items():
                if question["type"] == "choice":
                    schema[name] = {
                        "type": "enum", "choices": list(question["criteria"]),
                        "description": question["instructions"],
                        "choice_descriptions": question["criteria"],
                    }
                elif question["type"] == "noul":
                    schema[name] = {"type": "boolean", "description": question["instructions"]}
                else:
                    raise ValueError(f"不支持的问题类型：{question['type']}")
            raw = self.scorer.score(state, schema)
            answers = {}
            for name, field in schema.items():
                scores = raw["fields"][name]["scores"]
                expected = field.get("choices", ["false", "true"])
                probabilities = {key: float(scores[key]) for key in expected}
                if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
                    raise ValueError("Nimble 返回无效概率")
                if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-4):
                    raise ValueError("Nimble 概率之和不为 1")
                if field["type"] == "enum":
                    answers[name] = {"choice": max(probabilities, key=probabilities.get),
                                     "probabilities": probabilities}
                    # Nimble has no Jev-equivalent calibrated confidence statistic.
                else:
                    answers[name] = {"noul": probabilities["true"]}
            return {"model": raw["model"], "answers": answers, "nimble": raw}, None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"
