"""Real component fixture for CI recovery. Synthetic data; no provider calls.

Run inside the verification image after stopping the normal app. A temporary JVM
with no polling worker makes the queued-task boundary deterministic, without
editing database rows or racing a live worker. Its API listens on loopback only.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.backup_state import sha
from scripts.java_local import environment, server_command
from scripts.state_guard import state_access
from commerce_agent.budget import Budget, PRICE_DATE, RESERVATION
from commerce_agent.models import ScriptedModel
from commerce_agent.runtime import execute_agent
from commerce_worker.worker import Client, run_once

AGENT = ROOT / ".local/single-agent"
RUNTIME = ROOT / ".local/owner-demo/offline"
FIXTURE = AGENT / "runs/recovery-fixture.json"
LEDGER = AGENT / "personal-budget-ci-fixture.json"


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def exercise(action):
    context = module("recovery_context", "experiments/context-evaluation/verify.py")
    adapter = module("recovery_adapter", "tests/test_import_adapter.py")
    client = Client("http://127.0.0.1:18080", (RUNTIME / "user.token").read_text())
    worker = Client("http://127.0.0.1:18080", (RUNTIME / "worker.token").read_text())
    prefix = "/api/workspaces/demo-shop"

    def get(path): return client.call(prefix + path, method="GET")
    def post(path, body):
        return client.call(prefix + path, body, headers={"Idempotency-Key": str(uuid.uuid4())})

    env = environment()
    env.update(COMMERCE_DB_URL="jdbc:postgresql://db:5432/commerce", COMMERCE_DB_USER="commerce_app")
    command = server_command(RUNTIME, 18080) + ["--server.address=127.0.0.1",
        "--spring.profiles.active=postgres", "--spring.config.import=configtree:/run/secrets/"]
    with open("/tmp/recovery-jvm.log", "wb") as output:
        server = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                assert server.poll() is None, "recovery_fixture_jvm_stopped"
                try:
                    client.call("/health", method="GET")
                    break
                except OSError: time.sleep(.15)
            else: raise RuntimeError("recovery_fixture_jvm_timeout")

            def finish_import(draft):
                for _ in range(8):
                    draft = get("/imports/" + draft["id"])
                    if draft["state"] != "CHECKING": return draft
                    run_once(worker)
                raise AssertionError("import_did_not_finish")

            if action == "seed":
                assert not FIXTURE.exists() and not LEDGER.exists()
                payload = adapter.fixture()
                draft = finish_import(post("/imports", {"name": "容器恢复合成样例",
                    "source_declaration": "public_or_synthetic", "files": payload["files"]}))
                draft = finish_import(post("/imports/" + draft["id"] + "/check",
                    {"expected_revision": draft["revision"], "mapping": payload["mapping"]}))
                assert draft["state"] == "NEEDS_CONFIRMATION"
                ready = post("/imports/" + draft["id"] + "/confirm", {"revision": draft["revision"],
                    "check_task_id": draft["check_task_id"], "review_sha256": draft["report"]["review_sha256"],
                    "acknowledge_contract": True, "allow_model_use": False})
                assert ready["state"] == "READY" and not ready["dataset"]["model_allowed"]
                query = {"dataset_id": ready["dataset_id"], "metric": "cohort_net",
                    "start": "2026-10-05T00:00:00Z", "end": "2026-10-12T00:00:00Z", "as_of": "2026-10-19T00:00:00Z"}
                completed = post("/tasks", query)
                assert run_once(worker)["outcome"] == "succeeded"
                completed = get("/tasks/" + completed["id"])
                assert completed["status"] == "SUCCEEDED" and completed["result"]["value"] == 23000
                pending = post("/tasks", query)
                assert pending["status"] == "QUEUED"
                case = context.CASES["development"][0]
                request = context.job(case)
                interrupted = execute_agent(request, context.SNAPSHOT, ScriptedModel(script=context.script(case)),
                    state_root=AGENT / "runs", interrupt_after=["tools"])
                assert interrupted["interrupted"]
                # Explicit historical date for a synthetic offline ledger, not a
                # current price validation or a reset of the personal budget.
                budget = Budget(LEDGER, today=PRICE_DATE)
                index = budget.reserve("synthetic-known")
                budget.settle(index, {"input_tokens": 100, "output_tokens": 20})
                budget.reserve("synthetic-unknown")
                fixture = {"kind": "synthetic_container_recovery_fixture", "completed": completed,
                    "pending_id": pending["id"], "dataset": ready, "agent_request": request,
                    "ledger_sha256": sha(LEDGER)}
                FIXTURE.write_text(json.dumps(fixture, ensure_ascii=False) + "\n")
                FIXTURE.chmod(0o600)
                return {"passed": True, "action": action, "report_value_minor": 23000,
                    "pending_status": get("/tasks/" + pending["id"])["status"], "real_model_calls": 0}

            fixture = json.loads(FIXTURE.read_text())
            assert sha(LEDGER) == fixture["ledger_sha256"]
            budget = Budget(LEDGER, today=PRICE_DATE).read()
            assert [c["status"] for c in budget["calls"]] == ["usage_received", "unknown"]
            assert sum(c["charged_micro_cny"] for c in budget["calls"]) == 360 + RESERVATION
            saved = get("/tasks/" + fixture["completed"]["id"])
            assert saved == fixture["completed"]
            dataset = get("/imports/" + fixture["dataset"]["id"])
            assert dataset["dataset"] == fixture["dataset"]["dataset"] and not dataset["dataset"]["model_allowed"]
            assert get("/tasks/" + fixture["pending_id"])["status"] == "QUEUED"
            assert run_once(worker)["outcome"] == "succeeded"
            pending = get("/tasks/" + fixture["pending_id"])
            assert pending["status"] == "SUCCEEDED" and pending["result"]["value"] == 23000
            assert sum(e["kind"] == "SUCCEEDED" for e in pending["events"]) == 1
            case = context.CASES["development"][0]
            resumed = execute_agent(fixture["agent_request"], context.SNAPSHOT,
                ScriptedModel(script=context.script(case)), state_root=AGENT / "runs")
            replayed = execute_agent(fixture["agent_request"], context.SNAPSHOT,
                ScriptedModel(script=context.script(case)), state_root=AGENT / "runs")
            assert resumed["provenance"]["resumed"] and resumed["usage"] == replayed["usage"]
            assert resumed["usage"]["model_attempts"] == 2 and resumed["usage"]["tool_attempts"] == 2
            assert context.audit_publication(case, resumed) == []
            assert sha(LEDGER) == fixture["ledger_sha256"]
            return {"passed": True, "action": action, "report_value_minor": saved["result"]["value"],
                "pending_value_minor": pending["result"]["value"], "pending_success_events": 1,
                "checkpoint_resumed": True, "replay_extra_calls": 0, "model_attempts": 2, "tool_attempts": 2,
                "synthetic_budget_preserved": True, "unknown_reservation_preserved": True,
                "real_model_calls": 0}
        finally:
            server.terminate()
            try: server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=5)


def main():
    if ROOT != Path("/app") or os.getuid() != 1000 or sys.argv[1:] not in (["seed"], ["verify"]):
        raise SystemExit("Only the explicit synthetic container recovery fixture is supported.")
    with state_access():
        result = exercise(sys.argv[1])
    print(json.dumps(result))


if __name__ == "__main__": main()
