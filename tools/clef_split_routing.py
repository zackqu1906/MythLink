"""Opt-in split-routing experiment. Production continues to use joint v19."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time

from proximic_ring.text_processing.router_prompts import (
    CLEF_ROUTING_PROMPT, EDIT_TASK_DESCRIPTIONS, JOINT_ROUTING_RULES,
    ROUTER_PROMPT_VERSION, joint_router_input, routing_schema,
)
from proximic_ring.text_processing.task_routing import (
    RoutingDecision, available_edit_tasks, resolve_joint_decision, router_input,
    select_multiclass_task, task_availability,
)

VERSION = "voice-intent-split-v20-probe-from-v19"


def requests_for(case):
    """Reuse the active definitions/criteria/gate; only change question grouping."""
    target = case["target"]
    state = router_input(case["utterance"], target, case["context"])
    schema = routing_schema(target=target)
    root = {"voice_intent": {**schema["voice_intent"], "instructions":
        "依据 state 中的共同意图边界及可用编辑任务定义，判断当前用户是在提供正文，还是要求系统执行文字处理。"}}
    definitions = "\n".join(EDIT_TASK_DESCRIPTIONS[k] for k in available_edit_tasks(target))
    gate = ("原文为空：修改任务不可用，口述与 OCR 都不能充当已有原文。\n"
            if not target.strip() else "")
    root_state = ("共同意图边界：\n" + CLEF_ROUTING_PROMPT
                  + "\n\n可用编辑任务参考定义（本请求只输出总体意图）：\n" + gate + definitions
                  + "\n\n待分类输入（仅下列口述代表本次请求）：\n" + state)
    # Keep the exact task-combination rules, replacing only the two joint-only lines.
    combination_rules = "\n".join(JOINT_ROUTING_RULES.splitlines()[2:])
    details_state = ("共同意图边界：\n" + CLEF_ROUTING_PROMPT
        + "\n\n编辑细分规则：\n本分支是假设总体意图为编辑的预计算，尚未得到总体判断结果。"
          "只判断实际委托的可用任务；若口述只是正文、转述、计划或照录，应全部 skip，不能强行选一项。\n"
        + gate + combination_rules
        + "\n\n待分类输入（仅下列口述代表本次请求）：\n" + state)
    details = {k: {**v, "instructions": v["instructions"].replace("联合分类规则", "编辑细分规则")}
               for k, v in schema.items() if k != "voice_intent"}
    return {"joint": {"state": joint_router_input(state), "questions": schema},
            "root": {"state": root_state, "questions": root},
            "details": {"state": details_state, "questions": details}}


def invoke(router, request, started, barrier=None):
    if barrier:
        barrier.wait(timeout=10)
    dispatched = time.perf_counter() - started
    try:
        result = router._classify_stage(request["state"], request["questions"], lambda: False)
        return {**result, "dispatched_s": dispatched, "completed_s": time.perf_counter() - started}
    except Exception as exc:
        return {"error": str(exc), "dispatched_s": dispatched,
                "completed_s": time.perf_counter() - started}


@dataclass
class SplitRun:
    """Decision can finish before details; audit() explicitly drains the probe."""
    root_future: object
    details_future: object
    target: str
    started: float
    root: dict | None = None
    details: dict | None = None
    decision_ready_s: float | None = None

    def decision(self):
        self.root = self.root_future.result()
        if "error" in self.root:
            raise RuntimeError(self.root["error"])
        task, _, source = select_multiclass_task(self.root["answers"]["voice_intent"]["probabilities"])
        if task == "dictation":
            decision = RoutingDecision("dictation")
            evidence = {"decision_source": source, "details_used": False}
        else:
            self.details = self.details_future.result()
            if "error" in self.details:
                raise RuntimeError(self.details["error"])
            decision, evidence = resolve_joint_decision(
                {**self.root["answers"], **self.details["answers"]}, target=self.target)
            evidence["details_used"] = True
        self.decision_ready_s = time.perf_counter() - self.started
        return decision, evidence

    def audit(self):
        # Offline experiment only: drain before the next case so abandoned work
        # cannot contaminate its latency. A live caller would log asynchronously.
        self.root = self.root_future.result()
        self.details = self.details_future.result()
        audit = {"drain_s": time.perf_counter() - self.started,
                 "root_ready_s": self.root["completed_s"],
                 "details_ready_s": self.details["completed_s"],
                 "completion_order": "root_first" if self.root["completed_s"] < self.details["completed_s"] else "details_first"}
        if "error" not in self.root and "error" not in self.details:
            _, policy = resolve_joint_decision({**self.root["answers"], **self.details["answers"]}, target=self.target)
            audit["policy_after_details"] = policy
        if "error" in self.details:
            audit["details_error"] = self.details["error"]
        return audit


def start_split(pool: ThreadPoolExecutor, root_router, details_router, requests, target, *, details_first=False):
    started = time.perf_counter()
    barrier = threading.Barrier(2)
    jobs = [("root", root_router), ("details", details_router)]
    if details_first:
        jobs.reverse()
    futures = {name: pool.submit(invoke, router, requests[name], started, barrier) for name, router in jobs}
    return SplitRun(futures["root"], futures["details"], target, started)


def run_case(routers, pool, case, mode, *, details_first=False):
    requests = requests_for(case)
    record = {**case, "mode": mode, "prompt_version": VERSION,
              "baseline_prompt_version": ROUTER_PROMPT_VERSION,
              "task_availability": task_availability(case["target"]),
              "requests": {k: requests[k] for k in (("root", "details") if mode == "parallel" else (mode,))},
              "stages": {}, "root_correct": False, "exact_match": None if mode == "root" else False}
    started = time.perf_counter()
    run = None
    try:
        if mode == "parallel":
            run = start_split(pool, *routers, requests, case["target"], details_first=details_first)
            decision, evidence = run.decision()
            record.update(decision_ready_s=run.decision_ready_s, policy=evidence)
            record["actual"] = {"task": decision.task, "tasks": list(decision.edit_plan.tasks) if decision.edit_plan else []}
        else:
            stage = invoke(routers[0], requests[mode], started)
            record["stages"][mode] = stage
            if "error" in stage:
                raise RuntimeError(stage["error"])
            record["root_ready_s"] = stage["completed_s"]
            if mode == "joint":
                decision, evidence = resolve_joint_decision(stage["answers"], target=case["target"])
                record.update(actual={"task": decision.task, "tasks": list(decision.edit_plan.tasks) if decision.edit_plan else []}, policy=evidence)
                record["decision_ready_s"] = time.perf_counter() - started
            else:
                task, _, source = select_multiclass_task(stage["answers"]["voice_intent"]["probabilities"])
                record.update(root_actual=task, decision_source=source)
                # Root edit is not an executable edit plan: do not fabricate one.
                record["decision_ready_s"] = None
            record["drain_s"] = time.perf_counter() - started
    except Exception as exc:
        record["error"] = str(exc)
    finally:
        if run is not None:
            record.update(run.audit())
            record["stages"] = {"root": run.root, "details": run.details}
    root = record["stages"].get("joint", record["stages"].get("root", {}))
    if "answers" in root:
        answer = root["answers"]["voice_intent"]
        task, _, _ = select_multiclass_task(answer["probabilities"])
        record.update(root_actual=task, root_correct=task == case["expected"]["task"],
                      root_probabilities=answer["probabilities"])
    if "actual" in record:
        record["exact_match"] = (record["actual"]["task"] == case["expected"]["task"]
                                and set(record["actual"]["tasks"]) == set(case["expected"].get("tasks", [])))
    return record
