"""Real JVM + file H2 + HTTP + Python/DuckDB fault tests; no model or cloud calls."""
import base64
import concurrent.futures
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from commerce_worker.worker import ApiFailure, Client, command, run_once
from scripts.java_local import environment, prepare, server_command

QUERY = {"dataset_id": "demo-ledger-v1", "metric": "cohort_net", "start": "2026-10-05T00:00:00Z",
         "end": "2026-10-12T00:00:00Z", "as_of": "2026-10-19T00:00:00Z"}
GOLDEN = json.loads((ROOT / "docs/probes/ledger-cohort-2026-11-06.json").read_text())
LEASE_MS = 1500


class LoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = ROOT / ".local/task-loop/verification"
        parent.mkdir(parents=True, exist_ok=True)
        cls.runtime = prepare(Path(tempfile.mkdtemp(prefix="run-", dir=parent)))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.url = "http://127.0.0.1:" + str(cls.port)
        cls.user = Client(cls.url, (cls.runtime / "user.token").read_text())
        cls.worker = Client(cls.url, (cls.runtime / "worker.token").read_text())
        cls.server_log = (cls.runtime / "java.log").open("ab")
        try:
            cls.start_server()
        except Exception:
            cls.stop_server()
            cls.server_log.close()
            raise

    @classmethod
    def start_server(cls, snapshot=None):
        cls.server = subprocess.Popen(server_command(cls.runtime, cls.port, LEASE_MS, 100, snapshot), cwd=ROOT,
                                      env=environment(), stdout=cls.server_log, stderr=cls.server_log)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError("Java startup failed; inspect " + str(cls.runtime / "java.log"))
            try:
                if cls.user.call("/health", method="GET")["status"] == "ok":
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("Java startup timeout")

    @classmethod
    def stop_server(cls):
        if hasattr(cls, "server") and cls.server.poll() is None:
            cls.server.kill()  # Deliberate SIGKILL, not a graceful H2 close.
            cls.server.wait(timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.stop_server()
        cls.server_log.close()

    def setUp(self):
        self.ids = set()

    def tearDown(self):
        for task_id in self.ids:
            if self.get(task_id)["status"] in ("QUEUED", "RUNNING"):
                self.user.call(self.path(task_id) + "/cancel", {})

    def path(self, task_id):
        return "/api/workspaces/demo-shop/tasks/" + task_id

    def create(self, query=None, key=None):
        result = self.user.call("/api/workspaces/demo-shop/tasks", query or QUERY,
                                headers={"Idempotency-Key": key or str(uuid.uuid4())})
        self.ids.add(result["id"])
        return result

    def get(self, task_id):
        return self.user.call(self.path(task_id), method="GET")

    def claim(self):
        return self.worker.call("/internal/tasks/claim", {})

    def post(self, task, action, **extra):
        return self.worker.call("/internal/tasks/" + task["id"] + "/" + action,
                                {"lease_token": task["lease_token"], **extra})

    def error(self, status, function, *args, **kwargs):
        with self.assertRaises(ApiFailure) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def publications(self, task_id):
        return sum(e["kind"] == "SUCCEEDED" for e in self.get(task_id)["events"])

    def test_01_real_python_duckdb_result_and_evidence(self):
        task = self.create()
        result = run_once(self.worker)
        self.assertEqual(result["outcome"], "succeeded")
        saved = self.get(task["id"])
        self.assertEqual(saved["result"], GOLDEN)
        self.assertEqual(saved["result"]["value"], 23000)
        self.assertNotIn("lease_token", saved)
        self.assertNotIn("dataset_base64", saved)
        self.assertEqual(self.publications(task["id"]), 1)

    def test_02_idempotent_creation_concurrent_and_conflict(self):
        key = str(uuid.uuid4())
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            tasks = list(pool.map(lambda _: self.create(key=key), range(8)))
        self.assertEqual(len({t["id"] for t in tasks}), 1)
        self.assertEqual(len(self.get(tasks[0]["id"])["events"]), 1)
        self.error(409, self.create, {**QUERY, "as_of": "2026-10-26T00:00:00Z"}, key)
        equivalent = {**QUERY, "start": "2026-10-05T08:00:00+08:00"}
        self.assertEqual(self.create(equivalent, key)["id"], tasks[0]["id"])

    def test_03_concurrent_claim_one_owner(self):
        task = self.create()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: self.claim(), range(8)))
        claims = [c for c in claims if c]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["id"], task["id"])
        self.assertEqual(self.get(task["id"])["attempt"], 1)

    def test_04_expired_claim_fenced_before_and_after_reclaim(self):
        self.create()
        old = self.claim()
        time.sleep(LEASE_MS / 1000 + 0.08)
        self.error(409, self.post, old, "complete", result=GOLDEN)
        current = self.claim()
        self.assertEqual(current["id"], old["id"])
        self.assertEqual(current["attempt"], 2)
        self.assertNotEqual(current["lease_token"], old["lease_token"])
        for action, extra in (("heartbeat", {}), ("complete", {"result": GOLDEN}),
                              ("fail", {"retryable": False, "error_code": "old_failure"})):
            self.error(409, self.post, old, action, **extra)
        self.post(current, "complete", result=GOLDEN)
        self.assertEqual(self.publications(old["id"]), 1)

    def test_05_heartbeats_keep_slow_execution_owned(self):
        task = self.create()
        def slow(job, snapshot):
            return [sys.executable, "-c", "import os,sys,time; time.sleep(2.2); os.execv(sys.executable,[sys.executable,*sys.argv[1:]])", *command(job, snapshot)[1:]]
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_once, self.worker, 10, slow)
            time.sleep(1.9)
            self.assertIsNone(self.claim())
            self.assertEqual(future.result(timeout=15)["outcome"], "succeeded")
        self.assertEqual(self.get(task["id"])["attempt"], 1)

    def test_06_lost_completion_response_retried_without_duplicate(self):
        task = self.create()
        worker = self.worker
        class LoseFirstResponse:
            dropped = False
            def call(inner, path, body):
                result = worker.call(path, body)
                if path.endswith("/complete") and not inner.dropped:
                    inner.dropped = True
                    raise OSError("injected_response_loss_after_commit")
                return result
        self.assertEqual(run_once(LoseFirstResponse())["outcome"], "succeeded")
        self.assertEqual(self.publications(task["id"]), 1)

    def test_07_repeated_completion_rejects_changed_content(self):
        self.create()
        claim = self.claim()
        self.post(claim, "complete", result=GOLDEN)
        self.post(claim, "complete", result=dict(reversed(list(GOLDEN.items()))))
        self.error(409, self.post, claim, "complete", result={**GOLDEN, "value": 1})
        time.sleep(LEASE_MS / 1000 + 0.08)
        self.post(claim, "complete", result=GOLDEN)
        self.assertEqual(self.publications(claim["id"]), 1)

    def test_08_cancel_stops_child_and_cannot_resurrect(self):
        task = self.create()
        children, claims = [], []
        def started(process, job):
            children.append(process); claims.append(job)
            self.user.call(self.path(task["id"]) + "/cancel", {})
        result = run_once(self.worker, 10, lambda *_: [sys.executable, "-c", "import time; time.sleep(30)"], started)
        self.assertEqual(result["outcome"], "lease_lost")
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(self.get(task["id"])["status"], "CANCELLED")
        self.error(409, self.post, claims[0], "complete", result=GOLDEN)
        self.user.call(self.path(task["id"]) + "/cancel", {})
        self.assertEqual(self.publications(task["id"]), 0)

    def test_09_backoff_and_retry_budget(self):
        task = self.create()
        for attempt in range(1, 4):
            claim = self.claim()
            self.assertEqual(claim["attempt"], attempt)
            self.post(claim, "fail", retryable=True, error_code="temporary_unavailable")
            self.assertIsNone(self.claim())
            time.sleep(0.1 * attempt + 0.04)
        self.assertEqual(self.get(task["id"])["status"], "FAILED")
        self.assertEqual(self.get(task["id"])["attempt"], 3)

    def test_10_unsupported_product_refund_is_terminal(self):
        query = {"dataset_id": "demo-ledger-v1", "metric": "product_refund_amount", "start": "2026-10-12T00:00:00Z",
                 "end": "2026-10-19T00:00:00Z", "product_id": "P1"}
        task = self.create(query)
        self.assertEqual(run_once(self.worker)["outcome"], "product_refund_attribution_incomplete")
        saved = self.get(task["id"])
        self.assertEqual((saved["status"], saved["attempt"]), ("FAILED", 1))
        self.assertEqual(saved["error_code"], "product_refund_attribution_incomplete")
        self.assertIsNone(self.claim())

    def test_11_java_sigkill_restart_persists_all_states(self):
        running = self.create()
        stale = self.claim()
        completed = self.create()
        self.assertEqual(run_once(self.worker)["outcome"], "succeeded")
        queued = self.create()
        self.stop_server()
        self.start_server()
        self.assertEqual(self.get(queued["id"])["status"], "QUEUED")
        self.assertEqual(self.get(completed["id"])["result"], GOLDEN)
        self.assertEqual(self.get(running["id"])["status"], "RUNNING")
        time.sleep(LEASE_MS / 1000 + 0.08)
        recovered = self.claim()
        self.assertEqual(recovered["id"], running["id"])
        self.error(409, self.post, stale, "complete", result=GOLDEN)
        self.post(recovered, "complete", result=GOLDEN)
        self.assertEqual(run_once(self.worker)["outcome"], "succeeded")
        self.assertEqual(self.publications(running["id"]), 1)

    def test_12_roles_and_workspace_are_enforced(self):
        task = self.create()
        self.error(401, self.user.call, "/internal/tasks/claim", {})
        self.error(401, self.worker.call, self.path(task["id"]), method="GET")
        self.error(401, Client(self.url, "invalid").call, self.path(task["id"]), method="GET")
        self.error(403, self.user.call, self.path(task["id"]).replace("demo-shop", "other-shop"), method="GET")
        self.error(400, self.create, {**QUERY, "snapshot_path": "/arbitrary/path"})
        self.error(413, self.user.call, "/api/workspaces/demo-shop/tasks", {"large": "x" * 70000})

    def test_13_evidence_binding_rejects_wrong_source_metric_period(self):
        self.create()
        claim = self.claim()
        for mutate in (lambda r: r["evidence"].update(source_sha256="0" * 64),
                       lambda r: r.update(metric="paid_amount"),
                       lambda r: r["period"].update(start="2026-10-06T00:00:00Z"),
                       lambda r: r.update(currency="USD"), lambda r: r.update(value=1.5)):
            result = copy.deepcopy(GOLDEN); mutate(result)
            self.error(422, self.post, claim, "complete", result=result)
        self.post(claim, "complete", result=GOLDEN)
        self.assertEqual(self.publications(claim["id"]), 1)

    def test_14_corrupted_input_rejected_without_execution(self):
        task = self.create()
        worker = self.worker
        class Corrupt:
            def call(inner, path, body):
                result = worker.call(path, body)
                if path.endswith("/claim"):
                    result["dataset_base64"] = base64.b64encode(b"{}").decode()
                return result
        def forbidden(*_):
            self.fail("corrupted input must not reach executor")
        result = run_once(Corrupt(), command_factory=forbidden)
        self.assertEqual(result["outcome"], "dataset_digest_mismatch")
        self.assertEqual(self.get(task["id"])["status"], "FAILED")

    def test_15_execution_timeout_kills_child_and_exhausts_budget(self):
        task = self.create()
        children = []
        for attempt in range(1, 4):
            result = run_once(self.worker, 0.15, lambda *_: [sys.executable, "-c", "import time; time.sleep(30)"],
                              lambda process, _: children.append(process))
            self.assertEqual(result["outcome"], "execution_timeout")
            self.assertIsNotNone(children[-1].poll())
            time.sleep(0.1 * attempt + 0.04)
        self.assertEqual(self.get(task["id"])["status"], "FAILED")
        self.assertEqual(self.get(task["id"])["attempt"], 3)

    def test_16_repeated_worker_crashes_exhaust_budget(self):
        task = self.create()
        for attempt in range(1, 4):
            # Separate OS process claims, then receives SIGKILL before heartbeat/result.
            source = "from commerce_worker.worker import Client; from pathlib import Path; import sys,time; Client(sys.argv[1],Path(sys.argv[2]).read_text()).call('/internal/tasks/claim',{}); print('claimed',flush=True); time.sleep(30)"
            process = subprocess.Popen([sys.executable, "-c", source, self.url, str(self.runtime / "worker.token")], cwd=ROOT,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                self.assertEqual(process.stdout.readline(), b"claimed\n")
            finally:
                process.kill(); process.wait(timeout=5); process.stdout.close()
            self.assertEqual(process.returncode, -9)
            self.assertEqual(self.get(task["id"])["attempt"], attempt)
            time.sleep(LEASE_MS / 1000 + 0.08)
        self.assertIsNone(self.claim())
        saved = self.get(task["id"])
        self.assertEqual((saved["status"], saved["error_code"]), ("FAILED", "attempts_exhausted"))

    def test_17_cancel_completion_race_has_one_terminal_outcome(self):
        for _ in range(4):
            task = self.create(); claim = self.claim()
            def operation(action):
                try:
                    if action == "cancel":
                        self.user.call(self.path(task["id"]) + "/cancel", {})
                    else:
                        self.post(claim, "complete", result=GOLDEN)
                    return 200
                except ApiFailure as exc:
                    return exc.status
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(operation, ("cancel", "complete")))
            self.assertEqual(sorted(statuses), [200, 409])
            saved = self.get(task["id"])
            self.assertIn(saved["status"], ("CANCELLED", "SUCCEEDED"))
            self.assertEqual(self.publications(task["id"]), int(saved["status"] == "SUCCEEDED"))

    def test_18_queued_input_frozen_across_registry_change(self):
        queued = self.create()
        revised = json.loads((ROOT / "examples/ledger/snapshot.json").read_bytes())
        revised["snapshot_id"] = "synthetic-ledger-revised-v2"
        variant = self.runtime / "revised-snapshot.json"
        variant.write_text(json.dumps(revised))
        changed_sha = hashlib.sha256(variant.read_bytes()).hexdigest()
        self.stop_server()
        try:
            self.start_server(snapshot=variant)
            self.assertEqual(run_once(self.worker)["outcome"], "succeeded")
            self.assertEqual(self.get(queued["id"])["result"], GOLDEN)
            new_task = self.create()
            self.assertEqual(new_task["source_sha256"], changed_sha)
            self.assertEqual(run_once(self.worker)["outcome"], "succeeded")
            current = self.get(new_task["id"])["result"]
            self.assertEqual(current["snapshot_id"], "synthetic-ledger-revised-v2")
            self.assertEqual(current["evidence"]["source_sha256"], changed_sha)
            self.assertEqual(current["value"], 23000)
        finally:
            self.stop_server()
            self.start_server()


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
    def startTest(self, test):
        self.started = time.monotonic(); super().startTest(test)
    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({"name": test._testMethodName, "passed": True, "seconds": round(time.monotonic() - self.started, 3)})
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.cases.append({"name": str(test), "passed": False, "kind": "assertion"})
    def addError(self, test, err):
        super().addError(test, err)
        self.cases.append({"name": str(test), "passed": False, "kind": "exception"})


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(unittest.defaultTestLoader.loadTestsFromTestCase(LoopTests))
    report = {"date": "2026-11-06", "kind": "real_local_process_fault_tests", "tests_run": result.testsRun,
              "success": result.wasSuccessful(), "cases": result.cases, "model_calls": 0,
              "snapshot_sha256": hashlib.sha256((ROOT / "examples/ledger/snapshot.json").read_bytes()).hexdigest(),
              "lease_ms": LEASE_MS, "retry_ms": 100, "max_attempts": 3,
              "limitations": ["single Java instance", "H2 file database", "synthetic ledger", "no real-model or PostgreSQL tests"]}
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".local/task-loop/verification/latest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
