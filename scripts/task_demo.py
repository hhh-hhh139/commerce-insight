"""One local, real Java -> Python worker -> DuckDB -> Java round trip."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commerce_worker.worker import Client
from scripts.java_local import environment, prepare, server_command


def main():
    runtime = prepare(ROOT / ".local/task-loop/demo")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = "http://127.0.0.1:" + str(port)
    user = Client(url, (runtime / "user.token").read_text())
    with (runtime / "java.log").open("ab") as log:
        server = subprocess.Popen(server_command(runtime, port), cwd=ROOT, env=environment(), stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 45
            while True:
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Java startup failed; inspect .local/task-loop/demo/java.log")
                try:
                    user.call("/health", method="GET")
                    break
                except OSError:
                    time.sleep(0.1)
            query = {"dataset_id": "demo-ledger-v1", "metric": "cohort_net", "start": "2026-10-05T00:00:00Z",
                     "end": "2026-10-12T00:00:00Z", "as_of": "2026-10-19T00:00:00Z"}
            task = user.call("/api/workspaces/demo-shop/tasks", query, headers={"Idempotency-Key": str(uuid.uuid4())})
            # A standalone worker OS process, rather than an in-process substitute.
            deadline = time.monotonic() + 90
            while True:
                subprocess.run([sys.executable, "-m", "commerce_worker.worker", "--url", url, "--token-file",
                                str(runtime / "worker.token"), "--once"], cwd=ROOT, check=True, timeout=65)
                result = user.call("/api/workspaces/demo-shop/tasks/" + task["id"], method="GET")
                if result["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Demo task deadline reached; persisted job can be recovered on next run")
                time.sleep(1)
            if result["status"] != "SUCCEEDED" or result["result"]["value"] != 23000:
                raise RuntimeError("Unexpected demo result: " + result["status"])
            path = runtime / "latest-result.json"
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"task_id": task["id"], "status": result["status"], "value": 23000, "currency": "CNY",
                              "unit": "minor_units", "evidence_file": str(path)}, ensure_ascii=False))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=5)


if __name__ == "__main__":
    main()
