"""One terminal key entry, original cumulative budget, regressions + controlled research."""
import argparse
import asyncio
from datetime import datetime
import getpass
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commerce_agent.budget import Budget, CAP, PRICE_DATE, RESERVATION
from commerce_agent.contracts import digest
from commerce_agent.experimental import execute_specialists
from commerce_agent.models import personal_deepseek
from commerce_agent.runtime import execute_agent, implementation_hashes
from commerce_agent.storage import AgentError, write_json
from scripts.evaluate_personal_agent import audit_publication, grade, main as regression_main

VARIANTS = ("full", "compact", "specialists")
CASES_PATH = ROOT / "experiments/context-evaluation/cases.json"
SNAPSHOT = ROOT / "examples/ledger/snapshot.json"
BUDGET_PATH = ROOT / (".local/single-agent/personal-budget-" + PRICE_DATE + ".json")


def journal_usage(root):
    journals = [json.loads(p.read_text()) for p in root.glob("*/journal.json")]
    events = [e for j in journals for e in j["events"] if e["kind"] == "model"]
    return {"model_attempts": sum(j["model_attempts"] for j in journals),
            "tool_attempts": sum(j["tool_attempts"] for j in journals),
            "input_tokens": sum(e.get("usage", {}).get("input_tokens", 0) for e in events if e.get("usage")),
            "output_tokens": sum(e.get("usage", {}).get("output_tokens", 0) for e in events if e.get("usage")),
            "unknown_model_responses": sum(e.get("provider_dispatched") is not False and (e.get("status") != "completed" or not e.get("usage")) for e in events),
            "response_models": sorted({m for e in events for m in e.get("response_models", [])})}


def compare_decisions(rows, cases):
    """Fixed gates; incomplete or unaccounted usage cannot count as an efficiency win."""
    expected = {(c["id"], r) for c in cases["heldout"] for r in range(1, cases["acceptance"]["heldout_repeats"] + 1)}
    summary = {}
    for variant in VARIANTS:
        dev = [r for r in rows if r["variant"] == variant and r["split"] == "development"]
        held = [r for r in rows if r["variant"] == variant and r["split"] == "heldout"]
        summary[variant] = {"complete": len(dev) == len(cases["development"]) and all(r["passed"] for r in dev)
                              and len(held) == len(expected) and {(r["case_id"], r["repeat"]) for r in held} == expected,
                            "successes": sum(r["passed"] for r in held), "attempts": len(held),
                            "publication_errors": sum(len(r["publication_errors"]) for r in dev + held),
                            "input_tokens": sum(r["usage"]["input_tokens"] for r in held),
                            "charged_micro_cny": sum(r["charged_micro_cny"] for r in held),
                            "unknown_model_responses": sum(r["usage"]["unknown_model_responses"] for r in held),
                            "median_seconds": statistics.median(r["seconds"] for r in held) if held else None,
                            "response_models": sorted({m for r in dev + held for m in r["usage"]["response_models"]})}
    a, b, c = (summary[v] for v in VARIANTS)
    def comparable(candidate):
        return (a["complete"] and candidate["complete"] and a["publication_errors"] == candidate["publication_errors"] == 0
                and a["unknown_model_responses"] == candidate["unknown_model_responses"] == 0
                and a["response_models"] == candidate["response_models"] and len(a["response_models"]) == 1
                and a["input_tokens"] > 0 and a["charged_micro_cny"] > 0)
    thresholds = cases["acceptance"]
    compact_pass = (comparable(b) and b["successes"] >= a["successes"]
                    and b["input_tokens"] <= a["input_tokens"] * (1 - thresholds["compact_min_input_token_reduction"])
                    and b["charged_micro_cny"] <= a["charged_micro_cny"])
    base_passes = {(r["case_id"], r["repeat"]) for r in rows if r["variant"] == "full" and r["split"] == "heldout" and r["passed"]}
    candidate_passes = {(r["case_id"], r["repeat"]) for r in rows if r["variant"] == "specialists" and r["split"] == "heldout" and r["passed"]}
    specialists_pass = (comparable(c) and c["successes"] >= a["successes"] + thresholds["specialists_min_extra_successes"]
                        and base_passes <= candidate_passes
                        and c["charged_micro_cny"] <= a["charged_micro_cny"] * thresholds["specialists_max_cost_ratio"]
                        and c["median_seconds"] <= a["median_seconds"] * thresholds["specialists_max_median_time_ratio"])
    return {"summary": summary, "compact_research_gate": bool(compact_pass),
            "specialists_research_gate": bool(specialists_pass), "default_changed": False,
            "note": "Research gates only; compact still requires legacy/sales/refund non-regression. API default is full."}


def run_comparison(key, destination):
    import httpx
    cases = json.loads(CASES_PATH.read_text())
    budget = Budget(BUDGET_PATH)
    data = {"kind": "live_context_comparison", "date": budget.today, "price_policy_date": PRICE_DATE,
            "price_verified_date": budget.today, "cases_sha256": hashlib.sha256(CASES_PATH.read_bytes()).hexdigest(),
            "implementation_sha256": implementation_hashes(),
            "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "model_requested": "deepseek-flash", "provider_version_statement": "DeepSeek-V4.1-Flash-1017; rolling alias, verified 2026-11-08",
            "temperature": 0, "thinking": "disabled", "max_output_tokens": 1024, "sdk_retries": 0,
            "same_execution_path": "direct Python LangGraph for all three candidates; separate API regressions",
            "cases": cases, "rows": [], "completed": False}
    sequence = [("development", 1, c) for c in cases["development"]]
    sequence += [("heldout", r, c) for r in range(1, cases["acceptance"]["heldout_repeats"] + 1) for c in cases["heldout"]]
    synchronous = httpx.Client(trust_env=False, follow_redirects=False, timeout=25)
    asynchronous = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=25)
    try:
        model = personal_deepseek(key, http_client=synchronous, http_async_client=asynchronous)
        for index, (split, repeat, case) in enumerate(sequence):
            order = VARIANTS[index % 3:] + VARIANTS[:index % 3]
            for variant in order:
                if any(c["status"] == "unknown" for c in budget.read()["calls"]):
                    data["stop_reason"] = "unknown_model_request_requires_review"; return data
                dev = [r for r in data["rows"] if r["variant"] == variant and r["split"] == "development"]
                if split == "heldout" and (len(dev) != len(cases["development"]) or not all(r["passed"] for r in dev)):
                    continue
                if sum(c["charged_micro_cny"] for c in budget.read()["calls"]) + RESERVATION > CAP:
                    data["stop_reason"] = "conservative_budget_limit"
                    return data
                request = {"id": str(uuid.uuid4()), "attempt": 1, "workspace_id": "demo-shop",
                           "source_sha256": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(),
                           "request": {"kind": "agent", "dataset_id": "demo-ledger-v1", "question": case["question"]}}
                trial = destination / request["id"]; trial.mkdir()
                before = len(budget.read()["calls"]); started = time.monotonic(); report = None
                publication_errors = []
                try:
                    if variant == "specialists":
                        report = execute_specialists(request, SNAPSHOT, {r: model for r in ("sales", "refunds")},
                                                     state_root=trial, budget_path=BUDGET_PATH)
                    else:
                        report = execute_agent(request, SNAPSHOT, model, state_root=trial,
                                               budget_path=BUDGET_PATH, context_mode=variant)
                    score = grade(case, report)
                    publication_errors = audit_publication(case, report)
                    if publication_errors: score = {"passed": False, "errors": score["errors"] + publication_errors}
                except Exception as exc:
                    # Provider errors can carry credentials: no exception text or traceback.
                    score = {"passed": False, "errors": [str(exc) if isinstance(exc, AgentError) else type(exc).__name__]}
                ids = {request["id"]} if variant != "specialists" else {
                    "specialist-" + digest({"id": request["id"], "role": r}) for r in ("sales", "refunds")}
                charged = sum(c["charged_micro_cny"] for c in budget.read()["calls"][before:] if c["task_id"] in ids)
                data["rows"].append({"variant": variant, "case_id": case["id"], "split": split, "repeat": repeat,
                                     **score, "seconds": round(time.monotonic() - started, 3), "report": report,
                                     "publication_errors": publication_errors, "charged_micro_cny": charged,
                                     "usage": journal_usage(trial)})
                write_json(destination / "comparison.json", data)
                print(f"comparison/{variant}/{split}/{repeat}/{case['id']}: {'PASS' if score['passed'] else 'FAIL'}", flush=True)
        data["completed"] = True  # Schedule finished; candidate completeness is checked separately.
    finally:
        synchronous.close(); asyncio.run(asynchronous.aclose())
        data["decision"] = compare_decisions(data["rows"], cases)
        data["budget"] = budget.read()
        write_json(destination / "comparison.json", data)
    return data


def run_compact_regressions(key, destination, references):
    """Only after B's preregistered gate; compare exact cases to this release's A runs."""
    import httpx
    result = {"kind": "live_compact_non_regression", "rows": [], "completed": False, "passed": False}
    budget = Budget(BUDGET_PATH)
    synchronous = httpx.Client(trust_env=False, follow_redirects=False, timeout=25)
    asynchronous = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=25)
    try:
        model = personal_deepseek(key, http_client=synchronous, http_async_client=asynchronous)
        for reference in references:
            baseline = json.loads(Path(reference["report"]).read_text())
            if not baseline["completed"]:
                result["stop_reason"] = "baseline_regression_incomplete"; return result
            directory = {"legacy": "single-agent", "sales-change": "sales-evaluation", "refund-change": "refund-evaluation"}[reference["suite"]]
            cases = json.loads((ROOT / "experiments" / directory / "cases.json").read_text())
            indexed = {c["id"]: c for c in cases["development"] + cases["heldout"]}
            for prior in baseline["results"]:
                if any(c["status"] == "unknown" for c in budget.read()["calls"]):
                    result["stop_reason"] = "unknown_model_request_requires_review"; return result
                if sum(c["charged_micro_cny"] for c in budget.read()["calls"]) + RESERVATION > CAP:
                    result["stop_reason"] = "conservative_budget_limit"; return result
                case = indexed[prior["case_id"]]
                request = {"id": str(uuid.uuid4()), "attempt": 1, "workspace_id": "demo-shop",
                           "source_sha256": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(),
                           "request": {"kind": "agent", "dataset_id": "demo-ledger-v1", "question": case["question"]}}
                started = time.monotonic(); report = None; errors = []
                try:
                    report = execute_agent(request, SNAPSHOT, model, state_root=destination / "compact-regressions",
                                           budget_path=BUDGET_PATH, context_mode="compact")
                    score = grade(case, report); errors = audit_publication(case, report)
                    if errors: score = {"passed": False, "errors": score["errors"] + errors}
                except Exception as exc:
                    score = {"passed": False, "errors": [str(exc) if isinstance(exc, AgentError) else type(exc).__name__]}
                result["rows"].append({"suite": reference["suite"], "case_id": case["id"], "split": prior["split"],
                                       "repeat": prior["repeat"], "baseline_passed": prior["passed"], **score,
                                       "new_failure": prior["passed"] and not score["passed"], "publication_errors": errors,
                                       "seconds": round(time.monotonic() - started, 3), "report": report})
                write_json(destination / "compact-regressions.json", result)
                print(f"compact-regression/{reference['suite']}/{prior['repeat']}/{case['id']}: {'PASS' if score['passed'] else 'FAIL'}", flush=True)
        result["completed"] = len(references) == 3
        result["passed"] = result["completed"] and not any(r["new_failure"] or r["publication_errors"] for r in result["rows"])
    finally:
        synchronous.close(); asyncio.run(asynchronous.aclose())
        write_json(destination / "compact-regressions.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description="个人发布前合并验收，共用原累计 5 元预算")
    parser.add_argument("--only-comparison", action="store_true", help="只重跑研究对照；不替代完整发布验收")
    args = parser.parse_args()
    if not sys.stdin.isatty(): raise SystemExit("请由本人在个人终端运行；Key 仅隐藏输入。")
    budget = Budget(BUDGET_PATH)
    if any(c["status"] == "unknown" for c in budget.read()["calls"]):
        raise SystemExit("旧模型调用的用量仍未知，暂停新付费验收；请保留原预算账本。")
    previous = sum(c["charged_micro_cny"] for c in budget.read()["calls"])
    if previous + RESERVATION > CAP: raise SystemExit("原共享预算无法预占下一请求，请勿重置账本。")
    print(f"共享预算原占用上界 {previous / 1_000_000:.6f} 元，总上限 5 元；不会清空历史。")
    print("仅发送合成题及聚合结果。一次隐藏输入，依次验收原指标/销售/退款与上下文对照；失败记录保留。")
    key = getpass.getpass("个人 DeepSeek API Key（不要发送到聊天）: ").strip()
    if len(key) < 10: raise SystemExit("Key 未配置，未发起请求。")
    parent = ROOT / ".local/context-evaluation/live-runs"; parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="release-", dir=parent))
    release = {"kind": "release_live_validation", "date": budget.today, "starting_budget_micro_cny": previous,
               "regressions": [], "completed": False}
    try:
        if not args.only_comparison:
            for suite in ("legacy", "sales-change", "refund-change"):
                release["active_stage"] = "regression:" + suite
                code = regression_main(suite=suite, personal_key=key)
                pointer = "latest-live.json" if suite == "legacy" else f"latest-{suite}-live.json"
                release["regressions"].append({"suite": suite, "exit_code": code,
                    "report": json.loads((ROOT / ".local/single-agent" / pointer).read_text())["report"]})
                write_json(destination / "release.json", release)
        release["active_stage"] = "comparison"
        comparison = run_comparison(key, destination)
        release["comparison"] = str(destination / "comparison.json")
        release["research_decision"] = comparison["decision"]
        release["completed"] = comparison["completed"]
        if comparison["decision"]["compact_research_gate"] and not args.only_comparison:
            compact_regression = run_compact_regressions(key, destination, release["regressions"])
            release["compact_non_regression"] = str(destination / "compact-regressions.json")
            release["compact_eligible_for_review"] = compact_regression["passed"]
            release["completed"] = release["completed"] and compact_regression["completed"]
        release["note"] = "完成运行不等于通过；评估候选门槛、回归失败与实际模型后再决定，默认流程不自动改变。"
    except (Exception, SystemExit) as exc:
        release["stop_reason"] = ("unknown_model_request_requires_review" if any(c["status"] == "unknown" for c in budget.read()["calls"])
                                  else type(exc).__name__)
    finally:
        key = None
        release["budget"] = budget.read()
        write_json(destination / "release.json", release)
        write_json(ROOT / ".local/context-evaluation/latest-live.json", {"report": str(destination / "release.json")})
        print("验收记录：" + str(destination / "release.json"))
        print("累计预算保守占用：" + str(sum(c["charged_micro_cny"] for c in budget.read()["calls"]) / 1_000_000) + " 元")
    return 0 if release["completed"] and all(r["exit_code"] == 0 for r in release["regressions"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
