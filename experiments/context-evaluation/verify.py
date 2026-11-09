"""Real LangGraph/checkpoints/provider serialization; scripted or HTTP-mocked decisions."""
import asyncio
import copy
from contextlib import redirect_stdout
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from langchain_core.messages import ToolMessage
from commerce_agent.budget import Budget, CAP, PRICE_DATE, RESERVATION
from commerce_agent.contracts import digest
from commerce_agent.experimental import execute_specialists
from commerce_agent.models import ScriptedModel, personal_deepseek
from commerce_agent.runtime import execute_agent
from commerce_agent.storage import AgentError
from commerce_agent.tools import collected_evidence, compact_evidence
from scripts.evaluate_personal_agent import audit_publication, grade, grade_expectations
from scripts.evaluate_release import compare_decisions

SNAPSHOT = ROOT / "examples/ledger/snapshot.json"
CASES = json.loads(Path(__file__).with_name("cases.json").read_text())


def job(case):
    return {"id": str(uuid.uuid4()), "attempt": 1, "workspace_id": "demo-shop",
            "source_sha256": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(),
            "request": {"kind": "agent", "dataset_id": "demo-ledger-v1", "question": case["question"]}}


def script(case):
    calls = []
    for expected in case["expected"]:
        args = {k: v for k, v in expected.items() if k not in ("value", "sales_change", "refund_change")}
        metric = args["metric"]
        name = {"sales_change_by_product": "query_sales_change", "refund_change": "query_refund_change"}.get(metric, "query_metric")
        if name != "query_metric": args.pop("metric")
        calls.append({"name": name, "args": args})
    decision = {"outcome": case["outcome"], "issue": case.get("issue", "none"),
                "evidence_ids": [f"$e{i}" for i in range(len(calls))],
                "comparisons": [{"baseline_id": "$e0", "current_id": "$e1"}] if "delta" in case else []}
    return ([{"tool_calls": calls}] if calls else []) + [{"tool_calls": [{"name": "AnalysisDecision", "args": decision}]}]


class ContextTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".local/context-evaluation/verification"
        parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=parent))
        self.case = CASES["development"][0]

    def run_case(self, case=None, mode="compact", request=None, **kwargs):
        case = case or self.case
        return execute_agent(request or job(case), SNAPSHOT, ScriptedModel(script=script(case)),
                             state_root=self.root, context_mode=mode, **kwargs)

    def test_01_original_and_composite_oracles_keep_complete_evidence(self):
        cases = list(CASES["development"] + CASES["heldout"])
        for name in ("single-agent", "sales-evaluation", "refund-evaluation"):
            data = json.loads((ROOT / "experiments" / name / "cases.json").read_text())
            cases += data["development"] + data["heldout"]
        for case in cases:
            with self.subTest(case=case["id"], question=case["question"][:20]):
                compact = self.run_case(case)
                self.assertTrue(grade_expectations(case, compact)["passed"])
                self.assertEqual(audit_publication(case, compact), [])
                self.assertIn("not_a_real_model_run", grade(case, compact)["errors"])
        full = self.run_case(mode="full")
        compact = self.run_case()
        for field in ("answer", "evidence", "comparisons", "outcome"):
            self.assertEqual(full[field], compact[field])

    def test_02_compact_checkpoint_interrupt_resume_and_completed_replay(self):
        request = job(self.case)
        stopped = self.run_case(request=request, interrupt_after=["tools"])
        self.assertTrue(stopped["interrupted"])
        resumed = self.run_case(request=request)
        replayed = self.run_case(request=request)
        self.assertTrue(resumed["provenance"]["resumed"])
        self.assertEqual(resumed["usage"], replayed["usage"])
        self.assertEqual(resumed["usage"]["model_attempts"], 2)
        self.assertEqual(resumed["usage"]["tool_attempts"], 2)
        self.assertEqual(audit_publication(self.case, replayed), [])

    def test_03_mode_and_specialization_are_checkpoint_identity(self):
        request = job(self.case)
        self.run_case(request=request)
        for options in ({"mode": "full"}, {"specialization": "sales"}):
            with self.assertRaisesRegex(AgentError, "checkpoint_identity_mismatch"):
                self.run_case(request=request, **options)
        with self.assertRaisesRegex(AgentError, "invalid_agent_experiment"):
            self.run_case(mode="arbitrary")

    def test_04_missing_changed_and_rehashed_foreign_artifacts_rejected(self):
        evidence = self.run_case()["evidence"][0]
        def message(artifact, content=None):
            return ToolMessage(content=json.dumps(content or compact_evidence(evidence)), artifact=artifact,
                               name="query_sales_change", tool_call_id="test")
        self.assertEqual(collected_evidence([message(evidence)]), {evidence["evidence_id"]: evidence})
        with self.assertRaisesRegex(AgentError, "invalid_evidence_artifact"):
            collected_evidence([message(None)])
        changed = copy.deepcopy(evidence); changed["result"]["totals"]["delta_minor"] += 1
        with self.assertRaisesRegex(AgentError, "invalid_evidence_artifact"):
            collected_evidence([message(changed)])
        changed = copy.deepcopy(evidence); changed["result"]["evidence"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(AgentError, "invalid_evidence_digest"):
            collected_evidence([message(changed)])
        changed["evidence_id"] = "ev_" + digest({k: changed[k] for k in ("query", "result")})
        with self.assertRaisesRegex(AgentError, "evidence_source_binding_mismatch"):
            collected_evidence([message(changed, compact_evidence(changed))], source_sha256=job(self.case)["source_sha256"])

    @patch("commerce_agent.budget.datetime")
    def test_05_actual_sdk_request_excludes_artifact_but_report_retains_it(self, pricing_clock):
        # HTTP is mocked; keep this serialization test independent of the real billing date.
        pricing_clock.now.return_value.date.return_value.isoformat.return_value = "2026-11-08"
        import httpx
        requests = []
        def respond(request):
            body = json.loads(request.content); requests.append(body)
            self.assertEqual(request.url.host, "api.deepseek.com")
            if len(requests) == 1:
                calls = script(self.case)[0]["tool_calls"]
            else:
                receipts = [json.loads(m["content"]) for m in body["messages"] if m["role"] == "tool"]
                self.assertEqual(len(receipts), 2)
                for receipt in receipts:
                    self.assertEqual(receipt["kind"], "compact_evidence_v1")
                    self.assertNotIn("sql", json.dumps(receipt))
                self.assertNotIn("artifact", json.dumps(body))
                self.assertNotIn("canonical_sha256", json.dumps(body))
                calls = [{"name": "AnalysisDecision", "args": {"outcome": "answered", "issue": "none",
                         "evidence_ids": [r["evidence_id"] for r in receipts], "comparisons": []}}]
            message = {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"mock-{len(requests)}-{i}", "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                for i, c in enumerate(calls)]}
            return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0,
                "model": "mock-no-network", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        sync = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        async_client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
        try:
            model = personal_deepseek("offline-test-not-a-real-key", http_client=sync, http_async_client=async_client)
            report = execute_agent(job(self.case), SNAPSHOT, model, state_root=self.root,
                                   budget_path=self.root / "mock-budget.json", context_mode="compact")
            self.assertEqual(len(requests), 2)
            self.assertEqual(audit_publication(self.case, report), [])
            self.assertIn("evidence", report["evidence"][0]["result"])
        finally:
            sync.close(); asyncio.run(async_client.aclose())

    def specialist_models(self, case):
        models = {}
        for role, metric in (("sales", "sales_change_by_product"), ("refunds", "refund_change")):
            portion = {**case, "expected": [e for e in case["expected"] if e["metric"] == metric]}
            models[role] = ScriptedModel(script=script(portion))
        return models

    def test_06_specialists_merge_full_evidence_without_extra_model(self):
        for case in CASES["development"] + CASES["heldout"]:
            with self.subTest(case=case["id"]):
                result = execute_specialists(job(case), SNAPSHOT, self.specialist_models(case), state_root=self.root)
                self.assertTrue(grade_expectations(case, result)["passed"])
                self.assertEqual(audit_publication(case, result), [])
                self.assertLessEqual(result["usage"]["model_attempts"], 4)
                self.assertLessEqual(result["usage"]["tool_attempts"], 4)
                self.assertEqual(result["usage"]["real_model_calls"], 0)

    def test_07_one_success_one_refusal_cannot_publish_partial_success(self):
        case = next(c for c in CASES["heldout"] if c["id"] == "hold_missing_reason")
        models = self.specialist_models(self.case)
        models["refunds"] = ScriptedModel(script=script(case))
        report = execute_specialists(job(case), SNAPSHOT, models, state_root=self.root)
        self.assertEqual(report["outcome"], "unsupported")
        self.assertEqual(report["evidence"], [])
        self.assertEqual(audit_publication(case, report), [])
        self.assertEqual(report["usage"]["model_attempts"], 3)

    def test_08_specialist_metric_and_call_limits_enforced_in_code(self):
        refused = {"question": "test", "expected": [], "outcome": "unsupported", "issue": "missing_data"}
        wrong = {"tool_calls": [{"name": "query_metric", "args": {"metric": "refund_amount",
                  "start": "2026-10-05T00:00:00Z", "end": "2026-10-12T00:00:00Z"}}]}
        model = ScriptedModel(script=[wrong, *script(refused)])
        request = job(refused)
        report = execute_agent(request, SNAPSHOT, model, state_root=self.root, specialization="sales")
        journal = json.loads((self.root / request["id"] / "journal.json").read_text())
        self.assertIn("dataset_metric_unavailable", [e.get("error") for e in journal["events"]])
        self.assertEqual(report["evidence"], [])
        sales = {**self.case, "expected": [self.case["expected"][0]]}
        model = ScriptedModel(script=[{"tool_calls": [{"name": "inspect_dataset", "args": {}}]}, *script(sales)])
        with self.assertRaises(Exception) as caught:
            execute_agent(job(sales), SNAPSHOT, model, state_root=self.root, specialization="sales")
        self.assertTrue("limit" in str(caught.exception).lower())

    def test_09_revalidation_preserves_original_shared_ledger(self):
        # Synthetic legacy ledger: reproducible without the owner's private runtime.
        original = self.root / ("personal-budget-" + PRICE_DATE + ".json")
        original_value = {"price_date": PRICE_DATE, "model": "deepseek-flash", "cap_micro_cny": CAP,
                          "calls": [{"task_id": f"synthetic-history-{i}", "status": "usage_received",
                                     "charged_micro_cny": 4800 if i < 64 else 7530} for i in range(65)]}
        original.write_text(json.dumps(original_value)); raw = original.read_bytes()
        copy_path = self.root / "budget-copy.json"; copy_path.write_bytes(raw)
        budget = Budget(copy_path, today="2026-11-08")
        self.assertEqual(budget.read(), original_value)
        slot = budget.reserve("new-test")
        self.assertEqual(slot, len(original_value["calls"]))
        self.assertEqual(budget.read()["calls"][:-1], original_value["calls"])
        self.assertEqual(budget.read()["calls"][-1]["price_verified_date"], "2026-11-08")
        self.assertEqual(budget.read()["price_date"], "2026-11-06")
        budget.settle(slot, None)
        self.assertEqual(budget.read()["calls"][-1]["charged_micro_cny"], RESERVATION)
        self.assertEqual(original.read_bytes(), raw)
        with self.assertRaisesRegex(AgentError, "model_pricing_needs_revalidation"):
            Budget(copy_path, today="2026-11-09")

    def gate_rows(self):
        rows = []
        for variant in ("full", "compact", "specialists"):
            for split in ("development", "heldout"):
                for repeat in range(1, 2 if split == "development" else 3):
                    for case in CASES[split]:
                        rows.append({"variant": variant, "split": split, "repeat": repeat, "case_id": case["id"],
                                     "passed": True, "publication_errors": [], "charged_micro_cny": 100,
                                     "seconds": 1, "usage": {"input_tokens": 100 if variant == "full" else 70,
                                      "unknown_model_responses": 0, "response_models": ["same-model"]}})
        return rows

    def test_10_gates_reject_incomplete_unknown_and_model_changes(self):
        rows = self.gate_rows()
        self.assertTrue(compare_decisions(rows, CASES)["compact_research_gate"])
        # Perfect single baseline leaves no quality headroom for a costly second agent.
        self.assertFalse(compare_decisions(rows, CASES)["specialists_research_gate"])
        for field, value in (("unknown_model_responses", 1), ("response_models", ["changed-model"]), ("input_tokens", 10000)):
            changed = copy.deepcopy(rows)
            next(r for r in changed if r["variant"] == "compact" and r["split"] == "heldout")["usage"][field] = value
            self.assertFalse(compare_decisions(changed, CASES)["compact_research_gate"])
        self.assertFalse(compare_decisions([r for r in rows if not (r["variant"] == "compact" and r["repeat"] == 2)], CASES)["compact_research_gate"])

    def test_11_specialist_improvement_cannot_trade_existing_successes_or_cost(self):
        rows = self.gate_rows()
        failed = [r for r in rows if r["variant"] == "full" and r["split"] == "heldout"][:2]
        for row in failed: row["passed"] = False
        self.assertTrue(compare_decisions(rows, CASES)["specialists_research_gate"])
        changed = copy.deepcopy(rows)
        next(r for r in changed if r["variant"] == "specialists" and r["split"] == "heldout")["charged_micro_cny"] = 10000
        self.assertFalse(compare_decisions(changed, CASES)["specialists_research_gate"])
        changed = copy.deepcopy(rows)
        # Preserve the +2 total while introducing one loss on a baseline success.
        base = [r for r in changed if r["variant"] == "full" and r["split"] == "heldout"]
        base[2]["passed"] = False
        candidate = [r for r in changed if r["variant"] == "specialists" and r["split"] == "heldout"]
        candidate[3]["passed"] = False
        self.assertFalse(compare_decisions(changed, CASES)["specialists_research_gate"])

    @patch("commerce_agent.budget.datetime")
    def test_12_whole_comparison_schedule_with_sdk_mock_and_separate_budget(self, pricing_clock):
        pricing_clock.now.return_value.date.return_value.isoformat.return_value = "2026-11-08"
        import httpx
        import scripts.evaluate_release as evaluator
        cases = {c["question"]: c for c in CASES["development"] + CASES["heldout"]}
        requests = []
        def respond(request):
            body = json.loads(request.content); requests.append(body)
            question = next(m["content"] for m in body["messages"] if m["role"] == "user")
            case = copy.deepcopy(cases[question])
            system = next(m["content"] for m in body["messages"] if m["role"] == "system")
            for marker, metric in (("你是固定复合流程中的销售专家", "sales_change_by_product"), ("你是固定复合流程中的退款专家", "refund_change")):
                if marker in system: case["expected"] = [e for e in case["expected"] if e["metric"] == metric]
            turn = sum(m["role"] == "assistant" for m in body["messages"])
            calls = script(case)[turn]["tool_calls"]
            refs = [json.loads(m["content"])["evidence_id"] for m in body["messages"] if m["role"] == "tool"]
            def resolve(value):
                if isinstance(value, str) and value.startswith("$e"): return refs[int(value[2:])]
                if isinstance(value, list): return [resolve(v) for v in value]
                if isinstance(value, dict): return {k: resolve(v) for k, v in value.items()}
                return value
            message = {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"mock-{len(requests)}-{i}", "type": "function", "function": {"name": c["name"], "arguments": json.dumps(resolve(c["args"]))}}
                for i, c in enumerate(calls)]}
            return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0, "model": "mock-no-network",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        sync = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        async_client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
        model = personal_deepseek("offline-test-not-a-real-key", http_client=sync, http_async_client=async_client)
        try:
            with patch.object(evaluator, "BUDGET_PATH", self.root / "mock-budget.json"), \
                 patch.object(evaluator, "personal_deepseek", return_value=model), redirect_stdout(io.StringIO()):
                report = evaluator.run_comparison("offline-test-not-a-real-key", self.root)
            self.assertTrue(report["completed"])
            self.assertEqual(len(report["rows"]), 42)
            self.assertTrue(all(r["passed"] for r in report["rows"]))
            self.assertTrue(all(r["usage"]["unknown_model_responses"] == 0 for r in report["rows"]))
            self.assertEqual(len(report["budget"]["calls"]), len(requests))
            self.assertEqual(sum(r["charged_micro_cny"] for r in report["rows"]), sum(c["charged_micro_cny"] for c in report["budget"]["calls"]))
            self.assertEqual([r["variant"] for r in report["rows"][:6]], ["full", "compact", "specialists", "compact", "specialists", "full"])
            self.assertFalse(report["decision"]["compact_research_gate"])
            self.assertFalse(report["decision"]["specialists_research_gate"])
        finally:
            sync.close(); asyncio.run(async_client.aclose())


if __name__ == "__main__":
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ContextTests))
    report = {"kind": "context_engineering_only", "date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "seconds": round(time.monotonic() - started, 3), "real_model_calls": 0,
              "model_quality": "not_measured", "case_set_sha256": digest(CASES),
              "failure_details": [{"test": str(t), "traceback": trace} for t, trace in result.failures + result.errors]}
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".local/context-evaluation/verification.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(not result.wasSuccessful())
