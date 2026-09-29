"""Download and merge Nimble for local inference. Run explicitly, once.

Uses the published adapter contract and the upstream scorer's prompt hash.
Install upstream requirements/training.txt and torch before running.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1] / "pretrained_models/nimble")
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    prompt = repo / "nimble/scoring/parallel_schema.py"
    if not prompt.is_file():
        parser.error(f"请先下载 Nimble 官方源码到 {repo}")
    config_path = repo / ".cache/nimble-model.json"
    if config_path.exists():
        parser.error(f"配置已存在：{config_path}；请直接运行测试，或为重新准备模型指定另一个 --repo")
    from huggingface_hub import snapshot_download
    import torch
    from peft import PeftModel
    from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration

    model_id = "bespokelabs/Bespoke-Nimble-9B"
    cache = repo / ".cache/huggingface/hub"
    print("下载 Nimble 适配器，然后下载并合并 9B 基础模型；需较多内存和磁盘空间。", flush=True)
    adapter_path = Path(snapshot_download(model_id, cache_dir=str(cache)))
    contract = json.loads((adapter_path / "schema_config.json").read_text())
    if (contract["task"] != "schema_candidate_classification_v1" or
            contract["prompt_code_sha256"] != hashlib.sha256(prompt.read_bytes()).hexdigest()):
        raise ValueError("适配器的 prompt 合约与 Nimble 源码不一致，请使用匹配的源码版本")
    base = Qwen3_5ForConditionalGeneration.from_pretrained(
        contract["model"], revision=contract["revision"], cache_dir=str(cache),
        dtype=torch.bfloat16, device_map="cpu",
    )
    model = PeftModel.from_pretrained(base, str(adapter_path)).merge_and_unload(safe_merge=True)
    output = repo / ".cache/models" / f"nimble-9b-{adapter_path.name}"
    model.save_pretrained(output)
    AutoTokenizer.from_pretrained(str(adapter_path)).save_pretrained(output)
    config_path.write_text(json.dumps({
        "model_path": str(output), "model_id": model_id,
        "revision": adapter_path.name, "max_input_tokens": min(contract.get("max_length", 2048), 2048),
    }, indent=2) + "\n")
    print(f"模型准备完成：{config_path}")


if __name__ == "__main__":
    main()
