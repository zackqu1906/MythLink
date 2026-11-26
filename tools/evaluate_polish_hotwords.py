"""Measure swipe-down corrections with one preloaded personal vocabulary per suite."""
import argparse
from collections import Counter
from contextlib import ExitStack
from dataclasses import replace
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import unicodedata

from PySide6.QtCore import QSettings

from proximic_ring.text_processing import OpenAICompatibleTextProcessor
from proximic_ring.text_processing.prompts import polish_model_input
from proximic_ring.text_processing.service_profiles import load_service
from proximic_ring.voice.hotwords import HotwordStore
from proximic_ring.voice.polish_vocabulary import preserve_spelled_forms


def count_term(text, term):
    left = r"(?<![a-zA-Z0-9_])" if term[0].isascii() and term[0].isalnum() else ""
    right = r"(?![a-zA-Z0-9_])" if term[-1].isascii() and term[-1].isalnum() else ""
    return len(re.findall(left + re.escape(term) + right, text))


def score(case, output, words):
    before, after = case["before"], case["after"]
    expected = case["raw"].replace(before, after, 1)
    if count_term(output, after) < count_term(expected, after):
        return False, "missing target"
    if before != after and count_term(output, before) > count_term(expected, before):
        return False, "incorrect form retained"
    if any(count_term(output, w) and not count_term(expected, w) for w in words):
        return False, "unexpected vocabulary insertion"
    reference = "".join(c for c in expected.lower() if c.isalnum())
    actual = "".join(c for c in output.lower() if c.isalnum())
    if SequenceMatcher(None, reference, actual).ratio() < 0.65:
        return False, "extensive rewrite"
    return True, ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="all")
    parser.add_argument("--engine", choices=("configured", "offline", "shortlist"), default="configured")
    parser.add_argument("--offline-runner", type=Path)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--min-correction", type=float, help="require the correction rate to exceed this fraction")
    args = parser.parse_args()
    if args.min_correction is not None and (args.engine == "shortlist" or not 0 <= args.min_correction < 1):
        parser.error("--min-correction requires model outputs and a value in [0,1)")
    if args.engine == "offline" and not (args.offline_runner and args.weights):
        parser.error("offline mode requires --offline-runner and --weights")
    suite_bytes = args.suite.read_bytes()
    suite = json.loads(suite_bytes)
    cases = [c for c in suite["cases"] if args.split in ("all", c["split"])]
    if not cases:
        parser.error("split is empty")
    stats = Counter()
    recalled = Counter()
    with ExitStack() as stack:
        temp = stack.enter_context(TemporaryDirectory())
        store = HotwordStore(Path(temp) / "hotwords.json")
        for word in reversed(suite["words"]):
            store.add(word)
        model = None
        if args.engine == "configured":
            settings = load_service(QSettings("ProxiMic", "ProxiMic Voice"))
            processor = OpenAICompatibleTextProcessor()
            model = settings.model
        if args.engine == "offline":
            model = args.weights.name
            worker = stack.enter_context(subprocess.Popen(
                [args.offline_runner, args.weights], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, encoding="utf-8", bufsize=1))
        results = stack.enter_context(args.results.open("x", encoding="utf-8"))
        for case in cases:
            raw = case["raw"]
            candidates, glossary = store.manual_polish_hints(raw)
            prompt = polish_model_input(raw, candidates, glossary)
            row = dict(id=case["id"], split=case["split"], kind=case["kind"],
                       source=case["source"], engine=args.engine, model=model,
                       suite_sha256=hashlib.sha256(suite_bytes).hexdigest(),
                       prompt_sha256=hashlib.sha256(prompt["system_prompt"].encode()).hexdigest(),
                       input_sha256=hashlib.sha256(prompt["user_content"].encode()).hexdigest(),
                       candidates=[w for w, _ in candidates],
                       glossary=list(glossary), recalled=case["after"] in {w for w, _ in candidates})
            if case["kind"] == "correct":
                recalled[row["recalled"]] += 1
            if args.engine != "shortlist":
                try:
                    if args.engine == "configured":
                        output, model_output = processor.polish_with_trace(raw, replace(settings, enabled=True, timeout_s=45), candidates, glossary)
                    else:
                        worker.stdin.write(json.dumps(prompt, ensure_ascii=False) + "\n")
                        worker.stdin.flush()
                        model_output = json.loads(worker.stdout.readline())["output"]
                        output = preserve_spelled_forms(raw, model_output.strip(), candidates)
                except (OSError, RuntimeError, ValueError) as error:
                    row["error"] = str(error)
                    results.write(json.dumps(row, ensure_ascii=False) + "\n")
                    raise SystemExit(f"Stopped at {case['id']}: {type(error).__name__}: {error}") from error
                row["model_output"] = model_output
                row["output"] = unicodedata.normalize("NFC", output.strip())
                row["passed"], row["reason"] = score(case, row["output"], suite["words"])
                stats[(case["kind"], row["passed"])] += 1
            results.write(json.dumps(row, ensure_ascii=False) + "\n")
            results.flush()
        if args.engine == "offline":
            worker.stdin.close()
            if worker.wait():
                raise SystemExit("Offline inference process failed")
    print(f"{len(cases)} cases")
    if sum(recalled.values()):
        print(f"Initial shortlist recall: {recalled[True]}/{sum(recalled.values())}")
    for kind in ("correct", "keep"):
        total = stats[kind, True] + stats[kind, False]
        if total:
            print(f"{kind}: {stats[kind, True]}/{total} ({stats[kind, True] / total:.1%})")
    if args.min_correction is not None:
        positives = stats["correct", True] + stats["correct", False]
        if not positives or stats["correct", True] <= args.min_correction * positives:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
