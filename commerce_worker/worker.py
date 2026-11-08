"""Pull one job at a time. Re-execution is possible; Java fences publication."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class ApiFailure(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect_denied", headers, fp)


class Client:
    def __init__(self, base_url, token, timeout=2):
        url = urllib.parse.urlsplit(base_url)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.path not in ("", "/") or url.query or url.fragment or url.username:
            raise ValueError("only_explicit_loopback_endpoint_supported")
        self.url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, path, body=None, method="POST", headers=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("invalid_api_path")
        data = None if body is None else json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json", **(headers or {})})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("response_too_large")
                return None if not raw else json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Never include credentials, headers or raw server errors in logs.
            raise ApiFailure(exc.code, "api_" + str(exc.code)) from None


def command(job, snapshot):
    query = job["request"]
    if query.get("metric") == "report_reconciliation":
        envelope = {key: job[key] for key in ("id", "attempt", "workspace_id", "source_sha256", "request")}
        if "dataset" in job: envelope["dataset"] = job["dataset"]
        path = snapshot.with_name("reconciliation-job.json")
        path.write_text(json.dumps(envelope, ensure_ascii=False))
        return [sys.executable, "-m", "commerce_data.reconciliation_cli", "--snapshot", str(snapshot), "--job", str(path)]
    if query.get("kind") in ("agent", "import_check"):
        envelope = {key: job[key] for key in ("id", "attempt", "workspace_id", "source_sha256", "request")}
        if "dataset" in job: envelope["dataset"] = job["dataset"]
        path = snapshot.with_name("agent-job.json")
        path.write_text(json.dumps(envelope, ensure_ascii=False))
        if query["kind"] == "import_check":
            return [sys.executable, "-m", "commerce_import.cli", "--job", str(path), "--input", str(snapshot)]
        return [sys.executable, "-m", "commerce_agent.cli", "--job", str(path), "--snapshot", str(snapshot)]
    if query.get("metric") in ("sales_change_by_product", "refund_change"):
        module = "commerce_data.refund_change_cli" if query["metric"] == "refund_change" else "commerce_data.sales_change_cli"
        args = [sys.executable, "-m", module, str(snapshot), "--workspace", job["workspace_id"]]
        for name in ("start", "end", "current_start", "current_end", "top_n"):
            args.extend(["--" + name.replace("_", "-"), str(query[name])])
        return args
    args = [sys.executable, "-m", "commerce_data.ledger_cli", str(snapshot), "--confirm-contract",
            "--workspace", job["workspace_id"], "--metric", query["metric"], "--start", query["start"], "--end", query["end"]]
    for name in ("as_of", "product_id"):
        if name in query:
            args.extend(["--" + name.replace("_", "-"), query[name]])
    return args


def child_environment():
    # No inherited Python path/startup injection or model/cloud credentials.
    env = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin", "LANG": "en_US.UTF-8",
           "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}
    # Only the explicit personal-session variable; never inherit default provider keys.
    personal = os.environ.get("COMMERCE_PERSONAL_DEEPSEEK_API_KEY")
    if personal: env["COMMERCE_PERSONAL_DEEPSEEK_API_KEY"] = personal
    return env


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def run_once(client, timeout=60, command_factory=command, on_start=None):
    """Test seams are Python callables, never remote-controlled executable paths."""
    job = client.call("/internal/tasks/claim", {})
    if job is None:
        return {"outcome": "idle"}
    prefix = "/internal/tasks/" + job["id"]
    lease = {"lease_token": job["lease_token"]}

    def fail(code, retryable):
        try:
            client.call(prefix + "/fail", {**lease, "error_code": code, "retryable": retryable})
        except ApiFailure as exc:
            if exc.status != 409:
                raise
        return {"id": job["id"], "outcome": code}

    try:
        raw = base64.b64decode(job["dataset_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != job["source_sha256"]:
            return fail("dataset_digest_mismatch", False)
    except (ValueError, KeyError):
        return fail("invalid_dataset_envelope", False)
    parent = ROOT / ".local/task-loop/worker-runs"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        directory = Path(directory)
        snapshot = directory / "snapshot.json"
        snapshot.write_bytes(raw)
        output, errors = directory / "result.json", directory / "stderr.log"
        process = None
        try:
            with output.open("wb") as stdout, errors.open("wb") as stderr:
                executor_env = child_environment()
                if job["request"].get("kind") != "agent":
                    executor_env.pop("COMMERCE_PERSONAL_DEEPSEEK_API_KEY", None)
                process = subprocess.Popen(command_factory(job, snapshot), cwd=ROOT,
                                           env=executor_env, stdout=stdout, stderr=stderr)
                if on_start:
                    on_start(process, job)
                deadline = time.monotonic() + timeout
                interval = min(1, job["lease_ms"] / 3000)
                next_heartbeat = time.monotonic()  # Assert ownership before doing more work.
                while process.poll() is None:
                    # Polling bounds normal executor failures; this is not an OS disk quota or a sandbox.
                    if output.stat().st_size > (1_800_000 if job["request"].get("kind") == "import_check" else 60000) or errors.stat().st_size > 65536:
                        stop(process)
                        return fail("executor_output_limit", False)
                    if time.monotonic() >= deadline:
                        stop(process)
                        return fail("execution_timeout", True)
                    if time.monotonic() >= next_heartbeat:
                        try:
                            client.call(prefix + "/heartbeat", lease)
                        except ApiFailure as exc:
                            if exc.status == 409:
                                return {"id": job["id"], "outcome": "lease_lost"}
                            raise
                        # On network failure we stop conservatively in finally.
                        next_heartbeat = time.monotonic() + interval
                    time.sleep(0.03)
            if process.returncode:
                code = "analysis_rejected" if process.returncode == 2 else "executor_failed"
                if process.returncode == 2 and errors.stat().st_size < 4096:
                    try:
                        reason = json.loads(errors.read_bytes()).get("reason", "")
                        # Preserve bounded machine-readable contract reasons, never raw tracebacks/data.
                        if isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,80}", reason):
                            code = reason
                    except (ValueError, AttributeError):
                        pass
                return fail(code, process.returncode != 2)
            if output.stat().st_size > (1_800_000 if job["request"].get("kind") == "import_check" else 60000):
                return fail("result_too_large", False)
            try:
                result = json.loads(output.read_bytes())
                if not isinstance(result, dict):
                    raise ValueError("not_an_object")
            except (ValueError, UnicodeError):
                return fail("invalid_executor_result", False)
            payload = {**lease, "result": result}
            for attempt in range(3):
                try:
                    client.call(prefix + "/complete", payload)
                    return {"id": job["id"], "outcome": "succeeded"}
                except ApiFailure as exc:
                    if exc.status == 409:
                        return {"id": job["id"], "outcome": "lease_lost"}
                    if exc.status == 422:
                        return fail("result_binding_rejected", False)
                    raise
                except (OSError, urllib.error.URLError):
                    if attempt == 2:
                        raise
                    time.sleep(0.1 * (attempt + 1))
        finally:
            if process is not None:
                stop(process)


def main():
    parser = argparse.ArgumentParser(description="本地只读分析任务 Worker")
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--token-file", type=Path, default=ROOT / ".local/task-loop/demo/worker.token")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if not 0 < args.timeout <= 300:
        parser.error("timeout must be in (0,300]")
    client = Client(args.url, args.token_file.read_text())
    while True:
        try:
            outcome = run_once(client, timeout=args.timeout)
            print(json.dumps(outcome), flush=True)
        except (OSError, urllib.error.URLError, ApiFailure) as exc:
            print(json.dumps({"outcome": "transport_failure", "kind": type(exc).__name__}), flush=True)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
