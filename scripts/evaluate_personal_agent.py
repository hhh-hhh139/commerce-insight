"""User-run, in-memory personal key entry; real Java/Worker/DeepSeek evaluation."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commerce_agent.budget import Budget, CAP, PRICE_DATE, RESERVATION
from commerce_agent.contracts import digest
from commerce_agent.runtime import implementation_hashes
from commerce_agent.storage import AgentError, write_json
from commerce_data.ledger import load_snapshot, utc_time
from commerce_data.ledger_db import ControlledLedger, build_database
from commerce_data.sales_change import METRIC as SALES_CHANGE, analyze
from commerce_data.refund_change import METRIC as REFUND_CHANGE, analyze as analyze_refunds
from commerce_agent.report import build_report
from commerce_worker.worker import Client, child_environment
from scripts.java_local import environment, prepare, server_command


SALES_FIELDS = ("currency", "amount_scale", "result_unit", "totals", "product_count",
                "unchanged_count", "decreases", "increases", "reconciliation")
REFUND_FIELDS = ("currency", "amount_scale", "result_unit", "totals", "counts", "origins", "attribution", "products", "reconciliation")


def grade_expectations(case, result):
    """Score the public outcome, referenced queries and independent expected values.

    This function alone is NOT a real-model acceptance check; grade() adds that gate.
    It does not require a fixed sequence of intermediate inspect/tool calls.
    """
    errors = []
    if result.get("outcome") != case["outcome"]: errors.append("wrong_outcome")
    if "issue" in case and result.get("issue") != case["issue"]: errors.append("wrong_issue")
    expected = []
    for entry in case["expected"]:
        query = {k: v for k, v in entry.items() if k not in ("value", "sales_change", "refund_change")}
        for key in ("start", "end", "as_of", "current_start", "current_end"):
            if key in query: query[key] = utc_time(query[key])
        key = "sales_change" if query["metric"] == SALES_CHANGE else "refund_change" if query["metric"] == REFUND_CHANGE else "value"
        expected.append({"query": query, key: entry[key]})
    try:
        actual = []
        for item in result.get("evidence", []):
            value = item["result"]
            if item["query"]["metric"] == REFUND_CHANGE:
                actual.append({"query": item["query"], "refund_change": {k: value[k] for k in REFUND_FIELDS}})
            elif item["query"]["metric"] == SALES_CHANGE:
                actual.append({"query": item["query"], "sales_change": {k: value[k] for k in SALES_FIELDS}})
            else:
                actual.append({"query": item["query"], "value": value["value"]})
        # Canonical JSON distinguishes booleans from integers as well as values.
        if sorted(map(digest, expected)) != sorted(map(digest, actual)):
            errors.append("wrong_query_or_value")
    except (KeyError, TypeError, ValueError):
        errors.append("malformed_public_evidence")
    comparisons = result.get("comparisons", [])
    if not isinstance(comparisons, list) or any(not isinstance(c, dict) for c in comparisons):
        errors.append("malformed_public_comparisons")
        comparisons = []
    if "delta" in case and (len(comparisons) != 1 or comparisons[0].get("delta") != case["delta"]):
        errors.append("wrong_comparison")
    if "delta" not in case and comparisons:
        errors.append("unexpected_scalar_comparison")
    return {"passed": not errors, "errors": errors}


def grade(case, result):
    errors = grade_expectations(case, result)["errors"]
    if result.get("provenance", {}).get("mode") != "live": errors.append("not_a_real_model_run")
    return {"passed": not errors, "errors": errors}


def audit_publication(case, report):
    """Recompute published evidence independently of the model's conversation."""
    try:
        data, source = load_snapshot(ROOT / "examples/ledger/snapshot.json")
        parent = ROOT / ".local/single-agent/audit"
        parent.mkdir(parents=True, exist_ok=True)
        evidence = {}
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            database = build_database(data, Path(directory) / "audit.duckdb")
            with ControlledLedger(data, database, source) as ledger:
                for item in report["evidence"]:
                    if item["query"]["metric"] in (SALES_CHANGE, REFUND_CHANGE):
                        args = {k: v for k, v in item["query"].items() if k != "metric"}
                        analysis = analyze_refunds if item["query"]["metric"] == REFUND_CHANGE else analyze
                        recomputed = analysis(ledger, **args, workspace_id=data["workspace_id"])
                    else:
                        recomputed = ledger.query(**item["query"], workspace_id=data["workspace_id"])
                    if digest(recomputed) != digest(item["result"]): return ["published_evidence_not_reproducible"]
                    expected_id = "ev_" + digest({"query": item["query"], "result": recomputed})
                    if item["evidence_id"] != expected_id: return ["published_reference_invalid"]
                    evidence[expected_id] = item
        decision = {"outcome": report["outcome"], "issue": report["issue"], "evidence_ids": list(evidence),
                    "comparisons": [{k: c[k] for k in ("baseline_id", "current_id")} for c in report["comparisons"]]}
        binding = {"workspace_id": data["workspace_id"], "source_sha256": source["source_sha256"], "request": {"question": case["question"]}}
        expected = build_report(binding, decision, evidence, report["provenance"], report["usage"])
        if digest(expected) != digest(report): return ["published_report_binding_or_render_invalid"]
        return []
    except Exception:
        return ["published_report_audit_failed"]


def main(*, suite=None, personal_key=None):
    parser = argparse.ArgumentParser(description="个人 DeepSeek 评测；共用既有累计 5 元上限")
    parser.add_argument("--suite", choices=("legacy", "sales-change", "refund-change"), default="legacy",
                        help="legacy 为原单 Agent 题集；sales-change 为商品贡献专项；refund-change 为退款变化专项")
    args = parser.parse_args() if suite is None else argparse.Namespace(suite=suite)
    if not sys.stdin.isatty():
        raise SystemExit("Run this command yourself in a local terminal; key entry requires a TTY.")
    cases_path = ROOT / {"legacy":"experiments/single-agent/cases.json", "sales-change":"experiments/sales-evaluation/cases.json", "refund-change":"experiments/refund-evaluation/cases.json"}[args.suite]
    cases = json.loads(cases_path.read_text())
    budget = Budget(ROOT / (".local/single-agent/personal-budget-" + PRICE_DATE + ".json"))
    if any(c["status"] == "unknown" for c in budget.read()["calls"]):
        raise SystemExit("Unknown prior model usage requires review; do not reset the shared ledger.")
    previous = sum(c["charged_micro_cny"] for c in budget.read()["calls"])
    if previous + RESERVATION > CAP: raise SystemExit("Shared 5 CNY budget cannot admit another request; do not reset the ledger.")
    print("仅调用个人 DeepSeek 官方 API；共享总预算上限 5 元（含之前本入口的调用）。")
    print(f"题集：{cases['version']}；开发 {len(cases['development'])} 题，保留 {len(cases['heldout'])} 题 × {cases['acceptance']['heldout_repeats']} 轮。")
    print("Key 不回显、不落盘；只发送仓库中的合成题与聚合工具结果。先跑开发集，通过后跑保留集两轮。")
    key = personal_key if personal_key is not None else getpass.getpass("请输入个人 DeepSeek API Key（不要粘贴到聊天）: ").strip()
    if len(key) < 10: raise SystemExit("Key 未配置，未发起模型请求。")
    parent = ROOT / ".local/single-agent/live-runs"
    parent.mkdir(parents=True, exist_ok=True)
    runtime = prepare(Path(tempfile.mkdtemp(prefix="run-", dir=parent)))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    user = Client("http://127.0.0.1:" + str(port), (runtime / "user.token").read_text())
    worker_env = child_environment()
    worker_env["COMMERCE_PERSONAL_DEEPSEEK_API_KEY"] = key
    report = {"kind": "live_personal_deepseek", "date": budget.today, "price_policy_date": PRICE_DATE,
              "price_verified_date": budget.today, "model_requested": "deepseek-flash",
              "suite": args.suite, "cases_version": cases["version"],
              "provider_version_statement": "DeepSeek-V4.1-Flash-1017 (official pricing page, 2026-11-08; alias may change)",
              "thinking": "disabled", "temperature": 0, "max_output_tokens": 1024, "sdk_retries": 0,
              "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
              "prompt_sha256": hashlib.sha256((ROOT / "commerce_agent/prompt.txt").read_bytes()).hexdigest(),
              "implementation_sha256": implementation_hashes(), "results": [], "completed": False,
              "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "starting_budget_micro_cny": previous}
    result_path = runtime / "evaluation.json"
    with (runtime / "java.log").open("ab") as log:
        server = subprocess.Popen(server_command(runtime, port), cwd=ROOT, env=environment(), stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 45
            while True:
                if server.poll() is not None or time.monotonic() > deadline: raise RuntimeError("local_java_startup_failed")
                try:
                    user.call("/health", method="GET"); break
                except OSError: time.sleep(0.1)
            sequence = [("development", 1, case) for case in cases["development"]]
            sequence += [("heldout", repeat, case) for repeat in range(1, cases["acceptance"]["heldout_repeats"] + 1) for case in cases["heldout"]]
            for split, repeat, case in sequence:
                if any(c["status"] == "unknown" for c in budget.read()["calls"]):
                    report["stop_reason"] = "unknown_model_request_requires_review"; break
                if split == "heldout" and any(not r["passed"] for r in report["results"] if r["split"] == "development"):
                    report["stop_reason"] = "development_failed_holdout_not_opened"; break
                if sum(c["charged_micro_cny"] for c in budget.read()["calls"]) + RESERVATION > CAP:
                    report["stop_reason"] = "conservative_budget_limit"; break
                started = time.monotonic()
                task = user.call("/api/workspaces/demo-shop/tasks", {"kind": "agent", "dataset_id": "demo-ledger-v1", "question": case["question"]},
                                 headers={"Idempotency-Key": str(uuid.uuid4())})
                while True:
                    with (runtime / "worker.log").open("ab") as worker_log:
                        subprocess.run([sys.executable, "-m", "commerce_worker.worker", "--url", user.url, "--token-file",
                                        str(runtime / "worker.token"), "--once", "--timeout", "180"], cwd=ROOT, env=worker_env,
                                       stdout=worker_log, stderr=worker_log, timeout=190, check=False)
                    saved = user.call("/api/workspaces/demo-shop/tasks/" + task["id"], method="GET")
                    if saved["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"): break
                    if time.monotonic() - started > 240:
                        user.call("/api/workspaces/demo-shop/tasks/" + task["id"] + "/cancel", {})
                        saved = user.call("/api/workspaces/demo-shop/tasks/" + task["id"], method="GET"); break
                    time.sleep(1.1)
                score = grade(case, saved["result"]) if saved["status"] == "SUCCEEDED" else {"passed": False, "errors": [saved.get("error_code") or saved["status"]]}
                publication_errors = audit_publication(case, saved["result"]) if saved["status"] == "SUCCEEDED" else []
                if publication_errors: score = {"passed": False, "errors": score["errors"] + publication_errors}
                report["results"].append({"case_id": case["id"], "split": split, "repeat": repeat, **score,
                                           "publication_errors": publication_errors,
                                           "seconds": round(time.monotonic() - started, 3), "task": saved})
                write_json(result_path, report)
                print(f"{split}/{repeat}/{case['id']}: {'PASS' if score['passed'] else 'FAIL'}", flush=True)
            report["completed"] = len(report["results"]) == len(sequence)
            heldout = [r for r in report["results"] if r["split"] == "heldout"]
            report["heldout_success_rate"] = sum(r["passed"] for r in heldout) / len(heldout) if heldout else None
            report["publication_error_count"] = sum(len(r["publication_errors"]) for r in report["results"])
            report["heldout_passed"] = report["completed"] and report["publication_error_count"] == 0 and all(
                sum(r["passed"] for r in heldout if r["repeat"] == repeat) / len(cases["heldout"]) >= cases["acceptance"]["heldout_min_success_rate"]
                for repeat in range(1, cases["acceptance"]["heldout_repeats"] + 1))
        except Exception as exc:
            report["stop_reason"] = type(exc).__name__  # Never print exception objects that could contain credentials.
        finally:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
            worker_env.pop("COMMERCE_PERSONAL_DEEPSEEK_API_KEY", None)
            key = None
            report["budget"] = budget.read()
            report["budget_upper_cny"] = str(sum(c["charged_micro_cny"] for c in budget.read()["calls"]) / 1_000_000)
            write_json(result_path, report)
            pointer = "latest-live.json" if args.suite == "legacy" else "latest-" + args.suite + "-live.json"
            write_json(ROOT / ".local/single-agent" / pointer, {"report": str(result_path)})
            print("评测记录：" + str(result_path))
            print("共享预算占用上界（不等于实际账单）：" + report["budget_upper_cny"] + " 元")
    return 0 if report.get("heldout_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
