"""Offline engineering verification: real LangGraph/JVM/tools, scripted model decisions."""
import concurrent.futures
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from commerce_agent.budget import Budget, CAP, RESERVATION
from commerce_agent.contracts import digest
from commerce_agent.models import ScriptedModel
from commerce_agent.runtime import execute_agent
from commerce_agent.storage import AgentError, Journal, lock
from commerce_worker.worker import command, run_once

spec = importlib.util.spec_from_file_location("task_loop_tests", ROOT / "experiments/task-loop/verify.py")
old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
SNAPSHOT = ROOT / "examples/ledger/snapshot.json"
SHA = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
COHORT = json.loads((ROOT / "experiments/single-agent/scripts/cohort.json").read_text())
CASES = json.loads((ROOT / "experiments/single-agent/cases.json").read_text())
QUERY = {"metric": "paid_amount", "start": "2026-10-05T00:00:00Z", "end": "2026-10-12T00:00:00Z"}


def tool(name, args): return {"name": name, "args": args}
def turn(*calls): return {"tool_calls": list(calls)}
def decision(ids=None, comparisons=None, outcome="answered", issue="none"):
    return turn(tool("AnalysisDecision", {"outcome": outcome, "evidence_ids": ids or [], "comparisons": comparisons or [], "issue": issue}))
def job(task_id=None, question=None):
    return {"id": task_id or str(uuid.uuid4()), "attempt": 1, "workspace_id": "demo-shop", "source_sha256": SHA,
            "request": {"kind": "agent", "dataset_id": "demo-ledger-v1", "question": question or CASES["development"][0]["question"]}}


class AgentTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".local/single-agent/verification"
        parent.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="test-", dir=parent))

    def execute(self, script=COHORT, request=None, **kwargs):
        return execute_agent(request or job(), SNAPSHOT, ScriptedModel(script=script), state_root=self.directory, **kwargs)

    def reject(self, code, function, *args, **kwargs):
        with self.assertRaises(AgentError) as caught: function(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)

    def test_actual_graph_tools_and_numeric_evidence(self):
        report = self.execute()
        self.assertEqual(report["evidence"][0]["result"], old.GOLDEN)
        self.assertEqual(report["provenance"]["mode"], "scripted")
        self.assertGreater(report["provenance"]["checkpoint_count"], 2)
        self.assertEqual(report["usage"], {"model_attempts": 2, "tool_attempts": 1, "input_tokens": 0, "output_tokens": 0,
                                           "unknown_model_responses": 0, "real_model_calls": 0})

    def test_dynamic_tool_error_then_parameter_correction(self):
        bad = {**QUERY, "as_of": "2026-10-19T00:00:00Z"}
        report = self.execute([turn(tool("query_metric", bad)), turn(tool("query_metric", QUERY)), decision(["$e0"])])
        self.assertEqual(report["evidence"][0]["result"]["value"], 28000)
        self.assertEqual(report["usage"]["model_attempts"], 3)
        self.assertEqual(report["usage"]["tool_attempts"], 2)

    def test_unknown_tool_never_executes_sql(self):
        report = self.execute([turn(tool("execute_sql", {"sql": "DELETE FROM orders"})), *COHORT])
        self.assertEqual(report["evidence"][0]["result"]["value"], 23000)
        self.assertEqual(report["usage"]["tool_attempts"], 1)

    def test_workspace_argument_cannot_override_bound_data(self):
        report = self.execute([turn(tool("query_metric", {**QUERY, "workspace_id": "other-shop"})), *COHORT])
        self.assertEqual(report["evidence"][0]["result"]["workspace_id"], "demo-shop")
        self.assertEqual(report["usage"]["tool_attempts"], 1)

    def test_model_amount_field_rejected_then_corrected(self):
        bad = decision(["$e0"]); bad["tool_calls"][0]["args"]["amount"] = 999999
        report = self.execute([COHORT[0], bad, decision(["$e0"])])
        self.assertNotIn("999999", report["answer"])
        self.assertEqual(report["usage"]["model_attempts"], 3)

    def test_fabricated_reference_rejected(self):
        self.reject("invalid_report_reference", self.execute, [COHORT[0], decision(["ev_fabricated"])])

    def test_comparison_computed_by_code(self):
        later = {**QUERY, "start": "2026-10-12T00:00:00Z", "end": "2026-10-19T00:00:00Z"}
        script = [turn(tool("query_metric", QUERY), tool("query_metric", later)),
                  decision(["$e0", "$e1"], [{"baseline_id": "$e0", "current_id": "$e1"}])]
        report = self.execute(script)
        self.assertEqual(report["comparisons"][0]["delta"], -13000)
        self.assertEqual(report["comparisons"][0]["change_percent"], "-46.43")

    def test_different_metrics_not_comparable(self):
        later = {**QUERY, "metric": "paid_order_count", "start": "2026-10-12T00:00:00Z", "end": "2026-10-19T00:00:00Z"}
        script = [turn(tool("query_metric", QUERY), tool("query_metric", later)), decision(["$e0", "$e1"], [{"baseline_id": "$e0", "current_id": "$e1"}])]
        self.reject("incomparable_evidence", self.execute, script)

    def test_zero_baseline_does_not_divide(self):
        empty = {**QUERY, "start": "2026-10-01T00:00:00Z", "end": "2026-10-05T00:00:00Z"}
        later = {**QUERY, "start": "2026-10-05T00:00:00Z", "end": "2026-10-09T00:00:00Z"}
        report = self.execute([turn(tool("query_metric", empty), tool("query_metric", later)), decision(["$e0", "$e1"], [{"baseline_id": "$e0", "current_id": "$e1"}])])
        self.assertIsNone(report["comparisons"][0]["change_percent"])

    def test_explicit_clarification_is_not_fabricated_answer(self):
        report = self.execute([decision(outcome="needs_clarification", issue="needs_time_range")])
        self.assertEqual(report["evidence"], [])
        self.assertEqual(report["usage"]["tool_attempts"], 0)
        self.assertIn("补充", report["answer"])

    def test_incomplete_refund_data_is_unsupported(self):
        query = {"metric": "product_refund_amount", "product_id": "P1", "start": "2026-10-12T00:00:00Z", "end": "2026-10-19T00:00:00Z"}
        report = self.execute([turn(tool("query_metric", query)), decision(outcome="unsupported", issue="missing_data")])
        self.assertEqual(report["outcome"], "unsupported")
        self.assertEqual(report["evidence"], [])

    def test_model_and_tool_attempt_limits_are_durable(self):
        request = job()
        repeated = [turn(tool("nonexistent", {}))] * 6
        with self.assertRaises(Exception): self.execute(repeated, request)
        journal = Journal(self.directory / request["id"] / "journal.json").read()
        self.assertEqual(journal["model_attempts"], 5)
        with self.assertRaises(Exception): self.execute(repeated, request)
        self.assertEqual(Journal(self.directory / request["id"] / "journal.json").read()["model_attempts"], 5)
        request2 = job()
        with self.assertRaises(Exception): self.execute([turn(*[tool("query_metric", QUERY) for _ in range(5)])], request2)
        self.assertLessEqual(Journal(self.directory / request2["id"] / "journal.json").read()["tool_attempts"], 4)

    def test_restart_resumes_saved_tool_results(self):
        request = job()
        interrupted = self.execute(request=request, interrupt_after=["tools"])
        self.assertTrue(interrupted["interrupted"])
        path = self.directory / "job.json"; path.write_text(json.dumps({**request, "attempt": 2}))
        result = subprocess.run([sys.executable, "-m", "commerce_agent.cli", "--job", str(path), "--snapshot", str(SNAPSHOT),
                                 "--script", str(ROOT / "experiments/single-agent/scripts/cohort.json"), "--state-root", str(self.directory)],
                                cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        report = json.loads(result.stdout)
        self.assertTrue(report["provenance"]["resumed"])
        self.assertEqual(report["usage"]["tool_attempts"], 1)
        self.assertEqual(report["usage"]["model_attempts"], 2)

    def test_checkpoint_identity_and_task_isolation(self):
        request = job(); self.execute(request=request)
        altered = copy.deepcopy(request); altered["request"]["question"] = "different question"
        self.reject("checkpoint_identity_mismatch", self.execute, COHORT, altered)
        separate = self.execute(request=job())
        self.assertFalse(separate["provenance"]["resumed"])

    def test_checkpoint_single_writer_lock(self):
        request = job()
        with lock(self.directory / request["id"] / "run.lock"):
            self.reject("agent_checkpoint_busy", self.execute, COHORT, request)

    def test_input_hash_and_workspace_binding(self):
        self.reject("agent_input_binding_mismatch", self.execute, COHORT, {**job(), "workspace_id": "other-shop"})
        self.reject("agent_input_binding_mismatch", self.execute, COHORT, {**job(), "source_sha256": "0" * 64})

    def test_budget_unknown_requests_stay_reserved_after_restart(self):
        path = self.directory / "budget.json"; budget = Budget(path, today="2026-11-06")
        a = budget.reserve("a"); budget.reserve("b")
        self.reject("personal_budget_exhausted", Budget(path, today="2026-11-06").reserve, "c")
        self.assertEqual(sum(c["charged_micro_cny"] for c in budget.read()["calls"]), 2 * RESERVATION)
        budget.settle(a, {"input_tokens": 1000, "output_tokens": 100})
        budget.reserve("c")
        self.assertLessEqual(sum(c["charged_micro_cny"] for c in budget.read()["calls"]), CAP)
        self.reject("budget_already_settled", budget.settle, a, {"input_tokens": 1, "output_tokens": 1})

    def test_budget_concurrent_reservations_and_missing_usage(self):
        budget = Budget(self.directory / "budget.json", today="2026-11-06")
        def reserve(_):
            try: return budget.reserve("task")
            except AgentError: return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            reservations = [p for p in pool.map(reserve, range(8)) if p is not None]
        self.assertEqual(len(reservations), 2)
        budget.settle(reservations[0], None)
        self.assertEqual(sum(c["charged_micro_cny"] for c in budget.read()["calls"]), 2 * RESERVATION)
        self.reject("model_pricing_needs_revalidation", Budget, self.directory / "next-day.json", today="2026-11-07")

    @patch("commerce_agent.budget.datetime")
    def test_deepseek_sdk_wire_contract_without_external_requests(self, pricing_clock):
        # Offline HTTP mocks use the recorded pricing fixture, not the wall-clock date.
        pricing_clock.now.return_value.date.return_value.isoformat.return_value = "2026-11-08"
        import asyncio
        import httpx
        from commerce_agent.models import personal_deepseek
        requests = []
        marker = "offline-marker-never-a-real-personal-key"
        def respond(request):
            self.assertEqual(request.url.host, "api.deepseek.com")
            self.assertEqual(request.headers["authorization"], "Bearer " + marker)
            body = json.loads(request.content); requests.append(body)
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["max_tokens"], 1024)
            self.assertEqual(body["thinking"], {"type": "disabled"})
            if len(requests) == 1:
                call = COHORT[0]["tool_calls"][0]
            else:
                evidence = next(json.loads(m["content"])["evidence_id"] for m in body["messages"]
                                if m["role"] == "tool" and "evidence_id" in m["content"])
                call = decision([evidence])["tool_calls"][0]
            message = {"role": "assistant", "content": "", "tool_calls": [{"id": "mock-"+str(len(requests)), "type": "function",
                       "function": {"name": call["name"], "arguments": json.dumps(call["args"])}}]}
            return httpx.Response(200, json={"id": "mock-completion", "object": "chat.completion", "created": 0, "model": "wire-mock-no-network",
                                  "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                                  "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        sync = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        asynchronous = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
        try:
            model = personal_deepseek(marker, http_client=sync, http_async_client=asynchronous)
            request = job()
            report = execute_agent(request, SNAPSHOT, model, state_root=self.directory, budget_path=self.directory / "wire-budget.json")
            self.assertEqual(len(requests), 2)
            self.assertEqual(report["evidence"][0]["result"]["value"], 23000)
            self.assertEqual(report["usage"]["unknown_model_responses"], 0)
            calls = Budget(self.directory / "wire-budget.json", today="2026-11-08").read()["calls"]
            self.assertEqual(sum(c["charged_micro_cny"] for c in calls), 720)
            for path in (self.directory / request["id"]).glob("*"):
                if path.is_file(): self.assertNotIn(marker.encode(), path.read_bytes())
        finally:
            sync.close(); asyncio.run(asynchronous.aclose())


class AgentHttpTests(old.LoopTests):
    """Also inherits all 18 previously accepted task-loop fault tests."""
    def agent_task(self, question=None): return self.create(job(question=question)["request"])

    def scripted_command(self, request, snapshot):
        return command(request, snapshot) + ["--script", str(ROOT / "experiments/single-agent/scripts/cohort.json")]

    def test_19_agent_worker_java_publication(self):
        task = self.agent_task()
        self.assertEqual(run_once(self.worker, command_factory=self.scripted_command)["outcome"], "succeeded")
        saved = self.get(task["id"])
        self.assertEqual(saved["result"]["evidence"][0]["result"]["value"], 23000)
        self.assertEqual(saved["result"]["usage"]["real_model_calls"], 0)
        self.assertEqual(self.publications(task["id"]), 1)

    def test_20_agent_request_forbids_model_configuration(self):
        self.error(400, self.create, {**job()["request"], "model": "arbitrary-provider"})
        self.error(400, self.create, {**job()["request"], "script": "/path"})
        self.error(400, self.create, {**job()["request"], "question": " "})

    def test_21_agent_report_binding_and_idempotency(self):
        self.agent_task(); claim = self.claim()
        # Keep the test's short lease alive while importing frameworks in the child-equivalent runtime.
        self.post(claim, "heartbeat")
        report = execute_agent(claim, SNAPSHOT, ScriptedModel(script=COHORT))
        for mutate in (lambda r: r.update(question_sha256="0" * 64),
                       lambda r: r["provenance"].update(task_id="other"),
                       lambda r: r["provenance"].update(attempt=99),
                       lambda r: r["evidence"][0].update(evidence_id="ev_forged")):
            wrong = copy.deepcopy(report); mutate(wrong)
            self.error(422, self.post, claim, "complete", result=wrong)
        self.post(claim, "complete", result=report)
        self.post(claim, "complete", result=report)
        self.assertEqual(self.publications(claim["id"]), 1)

    def test_22_cancelled_agent_cannot_publish(self):
        task = self.agent_task(); claim = self.claim()
        report = execute_agent(claim, SNAPSHOT, ScriptedModel(script=COHORT))
        self.user.call(self.path(task["id"]) + "/cancel", {})
        self.error(409, self.post, claim, "complete", result=report)
        self.assertEqual(self.get(task["id"])["status"], "CANCELLED")

    def test_23_missing_personal_key_fails_without_remote_request(self):
        task = self.agent_task()
        self.assertEqual(run_once(self.worker)["outcome"], "personal_model_not_configured")
        self.assertEqual(self.get(task["id"])["attempt"], 1)


if __name__ == "__main__":
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(AgentTests),
                                unittest.defaultTestLoader.loadTestsFromTestCase(AgentHttpTests)])
    result = unittest.TextTestRunner(verbosity=2, resultclass=old.RecordedResult).run(suite)
    report = {"date": "2026-11-06", "kind": "offline_engineering_real_langgraph_scripted_model", "real_model_calls": 0,
              "tests_run": result.testsRun, "passed": result.wasSuccessful(), "cases": result.cases,
              "includes_previous_task_loop_tests": 18, "case_set_sha256": digest(CASES)}
    target = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT / ".local/single-agent/verification/latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
