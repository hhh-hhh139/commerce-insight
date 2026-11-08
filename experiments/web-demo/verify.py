"""Real JVM/HTTP/DuckDB demo acceptance. No external model calls."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from commerce_worker.worker import Client, run_once
from scripts.java_local import environment, prepare, server_command
from scripts.package_demo_examples import package

QUERY = {"dataset_id": "demo-ledger-v1", "metric": "cohort_net", "start": "2026-10-05T00:00:00Z",
    "end": "2026-10-12T00:00:00Z", "as_of": "2026-10-19T00:00:00Z"}


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = ROOT / ".local/web-demo/verification"
        parent.mkdir(parents=True, exist_ok=True)
        cls.runtime = prepare(Path(tempfile.mkdtemp(prefix="run-", dir=parent)))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.url = "http://127.0.0.1:" + str(cls.port)
        cls.worker = Client(cls.url, (cls.runtime / "worker.token").read_text())
        cls.user = Client(cls.url, (cls.runtime / "user.token").read_text())
        cls.log = (cls.runtime / "java.log").open("ab")
        cls.start()

    @classmethod
    def start(cls, enabled=True, live=False):
        cmd = server_command(cls.runtime, cls.port) + ["--commerce.demo.enabled=" + str(enabled).lower(),
            "--commerce.demo.agent-enabled=" + str(live).lower()]
        cls.server = subprocess.Popen(cmd, cwd=ROOT, env=environment(), stdout=cls.log, stderr=cls.log)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError("demo Java startup failure")
            try:
                cls.user.call("/health", method="GET")
                return
            except OSError:
                time.sleep(.1)
        cls.stop()
        raise RuntimeError("demo Java startup timeout")

    @classmethod
    def stop(cls):
        cls.server.kill()
        cls.server.wait(timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.stop()
        cls.log.close()

    def setUp(self):
        self.ids = []

    def tearDown(self):
        for task_id in self.ids:
            path = "/api/workspaces/demo-shop/tasks/" + task_id
            if self.user.call(path, method="GET")["status"] in ("QUEUED", "RUNNING"):
                self.user.call(path + "/cancel", {})

    def http(self, path, body=None, headers=None, demo=True, method=None):
        base = {"X-Commerce-Demo": "1", "Sec-Fetch-Site": "same-origin", "Origin": self.url} if demo else {}
        base.update(headers or {})
        base = {k: v for k, v in base.items() if v is not None}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        if body is not None:
            base["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=body, headers=base, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            value = json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw.decode()
            return response.status, value, dict(response.headers)

    def create(self, query=None, key=None):
        code, task, _ = self.http("/demo/api/tasks", QUERY if query is None else query,
            {"Idempotency-Key": key or str(uuid.uuid4())})
        self.assertEqual(code, 202, task)
        self.ids.append(task["id"])
        return task

    def get(self, task):
        code, value, _ = self.http("/demo/api/tasks/" + task["id"])
        self.assertEqual(code, 200)
        return value

    def test_01_assets_csp_and_config(self):
        for name in ("index.html", "app.js", "app.css", "examples.json"):
            status, _, headers = self.http("/demo/" + name, demo=False)
            self.assertEqual(status, 200)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertEqual(headers["Cache-Control"], "no-store")
        code, config, _ = self.http("/demo/api/config")
        self.assertEqual(code, 200)
        self.assertFalse(config["agent_enabled"])
        self.assertEqual(config["data_kind"], "synthetic")
        self.assertEqual(config["source_sha256"], hashlib.sha256((ROOT / "examples/ledger/snapshot.json").read_bytes()).hexdigest())

    def test_02_origin_host_and_role_boundaries(self):
        for headers in ({"Origin": "https://attacker.invalid"}, {"Host": "attacker.invalid"},
                {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                {"X-Commerce-Demo": None}, {"Sec-Fetch-Site": None}, {"Origin": None}):
            self.assertEqual(self.http("/demo/api/tasks", QUERY, headers)[0], 403)
        self.assertEqual(self.http("/demo/api/tasks", demo=False)[0], 403)
        self.assertEqual(self.http("/demo/api/tasks", {}, method="OPTIONS", demo=False)[0], 403)
        self.assertEqual(self.http("/internal/tasks/claim", {})[0], 401)
        self.assertEqual(self.http("/api/workspaces/demo-shop/tasks")[0], 401)
        self.assertEqual(self.http("/demo/../internal/tasks/claim", {})[0], 403)

    def test_03_disabled_live_and_bounded_input(self):
        body = {"dataset_id": "demo-ledger-v1", "kind": "agent", "question": "test"}
        code, error, _ = self.http("/demo/api/tasks", body, {"Idempotency-Key": str(uuid.uuid4())})
        self.assertEqual((code, error["error"]), (409, "live_agent_disabled"))
        self.assertEqual(self.http("/demo/api/tasks", {**QUERY, "workspace_id": "other"}, {"Idempotency-Key": "unknown-field"})[0], 400)
        self.assertEqual(self.http("/demo/api/tasks", b"x" * 65537)[0], 413)
        self.assertEqual(self.http("/demo/api/tasks", b"{")[0], 400)

    def test_04_real_query_history_and_restart(self):
        task = self.create()
        self.assertEqual(run_once(self.worker)["outcome"], "succeeded")
        result = self.get(task)
        self.assertEqual(result["result"]["value"], 23000)
        self.assertEqual([e["kind"] for e in result["events"]], ["CREATED", "CLAIMED", "SUCCEEDED"])
        self.stop(); self.start()
        self.assertEqual(self.get(task), result)
        self.assertNotIn("lease_token", json.dumps(result))
        self.assertNotIn("dataset_base64", json.dumps(result))

    def test_05_cancel_fences_publication(self):
        task = self.create()
        claim = self.worker.call("/internal/tasks/claim", {})
        self.assertEqual(self.http("/demo/api/tasks/" + task["id"] + "/cancel", {})[1]["status"], "CANCELLED")
        from commerce_worker.worker import ApiFailure
        with self.assertRaises(ApiFailure) as error:
            self.worker.call("/internal/tasks/" + task["id"] + "/heartbeat", {"lease_token": claim["lease_token"]})
        self.assertEqual(error.exception.status, 409)
        self.assertIsNone(self.get(task)["result"])

    def test_06_history_keyset_pagination_and_idempotency(self):
        key = str(uuid.uuid4())
        a = self.create(key=key); b = self.create(key=key)
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(self.http("/demo/api/tasks", {**QUERY, "as_of": "2026-10-26T00:00:00Z"}, {"Idempotency-Key": key})[0], 409)
        for _ in range(21):
            self.create()
        page = self.http("/demo/api/tasks")[1]
        self.assertEqual(len(page["items"]), 20)
        page2 = self.http("/demo/api/tasks?before=" + page["next_cursor"])[1]
        first = {t["id"] for t in page["items"]}; second = {t["id"] for t in page2["items"]}
        self.assertFalse(first & second)
        self.assertTrue(set(self.ids).issubset(first | second))
        self.assertTrue(all("result" not in t for t in page["items"]))
        from commerce_worker.worker import ApiFailure
        with self.assertRaises(ApiFailure) as error:
            self.user.call("/api/workspaces/other/tasks", method="GET")
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.http("/demo/api/tasks?before=unknown")[0], 404)

    def test_07_missing_attribution_failure(self):
        task = self.create({"dataset_id": "demo-ledger-v1", "metric": "product_refund_amount",
            "start": "2026-10-12T00:00:00Z", "end": "2026-10-19T00:00:00Z", "product_id": "P1"})
        run_once(self.worker)
        saved = self.get(task)
        self.assertEqual(saved["status"], "FAILED")
        self.assertEqual(saved["error_code"], "product_refund_attribution_incomplete")
        self.assertIsNone(saved["result"])

    def test_08_historical_examples_are_unchanged_and_explicit(self):
        _, data, _ = self.http("/demo/examples.json", demo=False)
        self.assertEqual(data, package())
        self.assertEqual(data["kind"], "historical_live_examples")
        self.assertEqual(len(data["examples"]), 5)
        self.assertEqual(sum(not e["passed"] for e in data["examples"]), 1)
        comparison = data["examples"][0]["task"]["result"]
        self.assertEqual(comparison["comparisons"][0]["delta"], -13000)
        self.assertEqual(comparison["comparisons"][0]["change_percent"], "-46.43")
        for example in data["examples"]:
            self.assertEqual(example["task"]["result"]["provenance"]["mode"], "live")

    def test_09_server_default_disables_demo(self):
        self.stop(); self.start(enabled=False)
        try:
            for path in ("/demo/index.html", "/demo/api/config", "/demo/examples.json"):
                self.assertEqual(self.http(path)[0], 404)
        finally:
            self.stop(); self.start()

    def test_10_live_opt_in_accepts_task_without_calling_model(self):
        self.stop(); self.start(live=True)
        try:
            self.assertTrue(self.http("/demo/api/config")[1]["agent_enabled"])
            self.create({"dataset_id": "demo-ledger-v1", "kind": "agent", "question": "统计明确期间的支付订单数"})
            # No Worker is run: tests acceptance only, not live quality.
        finally:
            self.stop(); self.start()

    def test_11_demo_rejects_unreviewed_snapshot_at_startup(self):
        changed = json.loads((ROOT / "examples/ledger/snapshot.json").read_text())
        changed["snapshot_id"] = "unreviewed-snapshot"
        path = self.runtime / "unreviewed.json"
        path.write_text(json.dumps(changed))
        self.stop()
        try:
            cmd = server_command(self.runtime, self.port, snapshot=path) + ["--commerce.demo.enabled=true"]
            process = subprocess.Popen(cmd, cwd=ROOT, env=environment(), stdout=self.log, stderr=self.log)
            try:
                self.assertNotEqual(process.wait(timeout=30), 0)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=10)
        finally:
            self.start()


if __name__ == "__main__":
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WebTests))
    report = {"kind": "web_demo_engineering_verification", "date": "2026-11-06", "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "seconds": round(time.monotonic()-started, 3),
        "real_model_calls": 0, "failure_details": [{"test": str(t), "traceback": detail} for t, detail in result.failures+result.errors]}
    output = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT / "docs/probes/web-demo-2026-11-06.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    raise SystemExit(not result.wasSuccessful())
