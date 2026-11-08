"""Model failure diagnostics without network, credentials or personal ledger changes."""
import asyncio
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import json
import hashlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
import httpx
from langchain_core.messages import HumanMessage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from commerce_agent.budget import Budget, RESERVATION
from commerce_agent.models import personal_deepseek
from commerce_agent.runtime import Guard
from commerce_agent.storage import AgentError, Journal


class FailureTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".local/model-failure-tests"; parent.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=parent))
        self.budget = Budget(self.root / "budget.json", today="2026-11-08")
        self.journal = Journal(self.root / "journal.json")
        self.guard = Guard(self.journal, "test", self.budget)
        self.request = SimpleNamespace(messages=[HumanMessage(content="synthetic")])

    def exercise(self, status, code):
        calls = []
        marker = "FAKE_BODY_AND_HEADER_MUST_NOT_BE_LOGGED"
        def response(request):
            calls.append(1)
            if status is None: raise httpx.ConnectError(marker, request=request)
            return httpx.Response(status, json={"error": {"message": marker, "type": "test_error"}})
        sync = httpx.Client(transport=httpx.MockTransport(response), trust_env=False)
        asynchronous = httpx.AsyncClient(transport=httpx.MockTransport(response), trust_env=False)
        try:
            model = personal_deepseek(marker, http_client=sync, http_async_client=asynchronous)
            handler = lambda request: model.invoke(request.messages)
            with self.assertRaisesRegex(AgentError, "^" + code + "$"):
                self.guard.wrap_model_call(self.request, handler)
            event = self.journal.read()["events"][0]
            self.assertEqual(event["failure_code"], code)
            self.assertEqual(event["provider_status"], status)
            self.assertTrue(event["provider_dispatched"])
            self.assertEqual(len(calls), 1)
            with self.assertRaisesRegex(AgentError, "model_failure_requires_review"):
                Guard(self.journal, "test", self.budget).wrap_model_call(self.request, handler)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(self.budget.read()["calls"]), 1)
            self.assertEqual(self.budget.read()["calls"][0]["charged_micro_cny"], RESERVATION)
            self.assertNotIn(marker, (self.root / "journal.json").read_text())
        finally:
            sync.close(); asyncio.run(asynchronous.aclose())

    def test_authentication(self): self.exercise(401, "model_authentication_failed")
    def test_balance(self): self.exercise(402, "model_balance_insufficient")
    def test_format(self): self.exercise(422, "model_request_invalid")
    def test_rate_limit(self): self.exercise(429, "model_rate_limited")
    def test_provider_unavailable(self): self.exercise(503, "model_provider_unavailable")
    def test_connection(self): self.exercise(None, "model_connection_failed")

    def test_interrupted_request_not_blindly_replayed(self):
        self.journal.reserve("model", 5); self.budget.reserve("test")
        with self.assertRaisesRegex(AgentError, "model_failure_requires_review"):
            self.guard.wrap_model_call(self.request, lambda _: self.fail("must not dispatch"))
        self.assertEqual(len(self.budget.read()["calls"]), 1)

    def test_budget_rejection_not_recorded_as_provider_request(self):
        self.budget.reserve("other-a"); self.budget.reserve("other-b")
        with self.assertRaisesRegex(AgentError, "personal_budget_exhausted"):
            self.guard.wrap_model_call(self.request, lambda _: self.fail("must not dispatch"))
        event = self.journal.read()["events"][0]
        self.assertFalse(event["provider_dispatched"])
        self.assertEqual(event["admission_error"], "personal_budget_exhausted")

    @patch("commerce_agent.budget.datetime")
    def test_cli_marks_model_failure_non_retryable_and_preserves_diagnostics(self, pricing_clock):
        pricing_clock.now.return_value.date.return_value.isoformat.return_value = "2026-11-08"
        import commerce_agent.cli as cli
        calls = []
        def response(request):
            calls.append(1)
            return httpx.Response(401, json={"error": {"message": "FAKE_SECRET_DO_NOT_LOG"}})
        sync = httpx.Client(transport=httpx.MockTransport(response), trust_env=False)
        asynchronous = httpx.AsyncClient(transport=httpx.MockTransport(response), trust_env=False)
        try:
            model = personal_deepseek("offline-test-never-real-key", http_client=sync, http_async_client=asynchronous)
            snapshot = ROOT / "examples/ledger/snapshot.json"
            job = {"id": "cli-failure", "attempt": 1, "workspace_id": "demo-shop", "source_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                   "request": {"kind": "agent", "dataset_id": "demo-ledger-v1", "question": "合成问题"}}
            path = self.root / "job.json"; path.write_text(json.dumps(job))
            args = ["cli", "--job", str(path), "--snapshot", str(snapshot), "--state-root", str(self.root / "runs")]
            for expected in ("model_authentication_failed", "model_failure_requires_review"):
                stderr = io.StringIO()
                with patch.object(cli, "ROOT", self.root), patch.object(cli, "personal_deepseek", return_value=model), \
                     patch.object(sys, "argv", args), redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                    code = cli.main()
                self.assertEqual(code, 2)  # Worker treats exit 2 as non-retryable.
                self.assertEqual(json.loads(stderr.getvalue())["reason"], expected)
                self.assertNotIn("FAKE_SECRET", stderr.getvalue())
            self.assertEqual(len(calls), 1)
            stored = json.loads((self.root / "runs/cli-failure/journal.json").read_text())
            self.assertEqual(stored["events"][0]["provider_status"], 401)
        finally:
            sync.close(); asyncio.run(asynchronous.aclose())


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FailureTests))
    report = {"date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(), "kind": "model_failure_engineering_only",
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "real_model_calls": 0,
              "failure_details": [{"test": str(t), "traceback": trace} for t, trace in result.failures + result.errors]}
    Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(not result.wasSuccessful())
