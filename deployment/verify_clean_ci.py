"""Run ONLY on this personal repository's fresh hosted GitHub runner.

Never discovers or connects to a workstation Docker daemon. No API model calls.
"""
from datetime import datetime, timezone
import http.cookiejar
import json
import os
from pathlib import Path
import platform
import secrets
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def main():
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("GITHUB_REPOSITORY_OWNER") != "hhh-hhh139"
            or os.environ.get("GITHUB_REPOSITORY") != "hhh-hhh139/commerce-insight"
            or platform.system() != "Linux"):
        raise SystemExit("This verifier is restricted to the verified personal GitHub-hosted Linux runner.")
    results = ROOT / ".local/ci-results"; results.mkdir(parents=True, exist_ok=False); results.chmod(0o777)
    private = ROOT / ".local/ci-runtime"; private.mkdir(mode=0o700)
    config = ROOT / ".local/ci-docker"; config.mkdir(mode=0o700)
    environment = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    environment["DOCKER_CONFIG"] = str(config)
    docker = ["docker", "--config", str(config), "--context", "default"]
    log = (results / "commands.log").open("w")

    def run(command, *, content=None, capture=False, timeout=1200):
        # Log commands only; all generated passwords travel via input/file, never argv.
        log.write(json.dumps(command) + "\n"); log.flush()
        process = subprocess.run(command, cwd=ROOT, env=environment, input=content, text=True,
            stdout=subprocess.PIPE if capture else log, stderr=log, timeout=timeout)
        if process.returncode: raise RuntimeError("command_failed:" + command[0])
        return process.stdout.strip() if capture else ""

    report = {"started_at": datetime.now(timezone.utc).isoformat(), "source_commit": os.environ.get("GITHUB_SHA"),
              "repository": os.environ.get("GITHUB_REPOSITORY"), "real_model_calls": 0, "passed": False,
              "stage": "image_resolution", "images": {}, "checks": []}
    compose = None
    try:
        images = {"python": "python:3.12.16-slim-bookworm", "java": "eclipse-temurin:21.0.13_9-jdk-jammy",
                  "postgres": "postgres:18.6-bookworm", "caddy": "caddy:2.11.9-alpine"}
        for name, tag in images.items():
            run([*docker, "pull", tag])
            resolved = run([*docker, "image", "inspect", tag, "--format", "{{index .RepoDigests 0}}"], capture=True)
            if "@sha256:" not in resolved: raise RuntimeError("image_digest_missing")
            report["images"][name] = {"tag": tag, "resolved": resolved}
        (results / "images.json").write_text(json.dumps(report["images"], indent=2) + "\n")
        build = [*docker, "build", "--build-arg", "PYTHON_IMAGE=" + report["images"]["python"]["resolved"],
                 "--build-arg", "JAVA_IMAGE=" + report["images"]["java"]["resolved"]]
        report["stage"] = "build"
        run([*build, "--target", "verification", "--tag", "commerce-insight:verification", "."])
        run([*build, "--target", "application", "--tag", "commerce-insight:local", "."])
        report["stage"] = "offline_contracts"
        for name, script in (("agent", "experiments/single-agent/verify.py"), ("imports", "experiments/data-import/verify.py"),
                             ("context", "experiments/context-evaluation/verify.py"), ("model-failures", "experiments/context-evaluation/verify_failures.py")):
            run([*docker, "run", "--rm", "--network", "none", "--memory", "2g", "--pids-limit", "256",
                 "--mount", "type=bind,source=" + str(results) + ",target=/app/.local/ci-results",
                 "commerce-insight:verification", script, ".local/ci-results/" + name + ".json"])
            report["checks"].append("linux_" + name)
        run([*docker, "run", "--rm", "--network", "none", "--memory", "2g", "--pids-limit", "256",
             "commerce-insight:verification", "-m", "unittest", "discover", "-s", "tests",
             "-p", "test_backup_paths.py", "-v"])
        report["checks"].append("linux_backup_path_boundaries")
        report["stage"] = "compose_setup"
        run([sys.executable, "deployment/prepare_secrets.py"])
        openssl = private / "openssl.cnf"
        openssl.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n")
        run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(private / "key.pem"),
             "-out", str(private / "cert.pem"), "-config", str(openssl)])
        (private / "key.pem").chmod(0o600)
        origin = "https://127.0.0.1:18443"
        # IP clients omit SNI; Docker changes the local destination IP seen by Caddy.
        # Select the test certificate explicitly without disabling client verification.
        (private / "Caddyfile").write_text("{\n admin off\n auto_https off\n default_sni 127.0.0.1\n}\n" + origin
            + " {\n tls /test-cert/cert.pem /test-cert/key.pem\n import /etc/caddy/routes.caddy\n}\n")
        override = private / "compose.yaml"
        override.write_text("services:\n  proxy:\n    ports: !override [\"127.0.0.1:18443:18443\"]\n    volumes:\n"
            + f"      - {private}/Caddyfile:/etc/caddy/Caddyfile:ro\n      - {private}:/test-cert:ro\n")
        env_file = private / "compose.env"
        env_file.write_text("COMMERCE_ORIGIN=" + origin + "\nCOMMERCE_DOMAIN=127.0.0.1\nPOSTGRES_IMAGE="
            + report["images"]["postgres"]["resolved"] + "\nCADDY_IMAGE=" + report["images"]["caddy"]["resolved"] + "\n")
        compose = [*docker, "compose", "--project-name", "commerce-personal-ci", "--env-file", str(env_file),
                   "--file", "deployment/compose.yaml", "--file", str(override), "--profile", "public"]
        (results / "compose-config.json").write_text(run([*compose, "config", "--format", "json"], capture=True) + "\n")
        # This is a generated test-only app password, not any personal account credential.
        password = secrets.token_urlsafe(24)
        provision = "from pathlib import Path; import sys; from scripts.java_local import prepare; from scripts.prepare_owner import password_hash; p=prepare(Path('.local/owner-demo/offline'))/'owner.hash'; p.write_text(password_hash(sys.stdin.read())+'\\n'); p.chmod(0o600)"
        run([*compose, "run", "--rm", "--no-deps", "-T", "--entrypoint", "/opt/agent-venv/bin/python", "app", "-c", provision], content=password)
        run([*compose, "up", "--detach", "--no-build", "--wait", "--wait-timeout", "120"])
        report["stage"] = "tls_application"
        cookies = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(private / "cert.pem"))),
            urllib.request.HTTPCookieProcessor(cookies), NoRedirect())

        def request(path, body=None, *, form=False, headers=None):
            h = {"Origin": origin, "Sec-Fetch-Site": "same-origin", "X-Commerce-Demo": "1", **(headers or {})}
            raw = None
            if body is not None:
                raw = (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
                h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            try: response = opener.open(urllib.request.Request(origin + path, data=raw, headers=h), timeout=10)
            except urllib.error.HTTPError as error: response = error
            with response:
                content = response.read()
                value = json.loads(content) if "application/json" in response.headers.get("Content-Type", "") else content.decode()
                return response.status, value, response.headers

        def login():
            code, csrf, headers = request("/session/csrf"); assert code == 200
            assert all(flag in headers.get("Set-Cookie", "") for flag in ("Secure", "HttpOnly", "SameSite=Strict"))
            before = next(c.value for c in cookies if c.name == "JSESSIONID")
            code, _, headers = request("/login", {"username": "owner", "password": password, csrf["parameter_name"]: csrf["token"]}, form=True)
            assert code == 302 and headers.get("Location") == "/demo/index.html"
            assert next(c.value for c in cookies if c.name == "JSESSIONID") != before
            code, value, _ = request("/demo/api/config"); assert code == 200 and value["owner_mode"] and not value["agent_enabled"]
            return {value["csrf_header"]: value["csrf_token"]}

        assert request("/demo/api/config")[0] == 401
        csrf = login()
        query = {"dataset_id": "demo-ledger-v1", "metric": "paid_amount", "start": "2026-10-05T00:00:00Z", "end": "2026-10-12T00:00:00Z"}
        assert request("/demo/api/tasks", query, headers={"Idempotency-Key": str(uuid.uuid4())})[0] == 403
        code, task, _ = request("/demo/api/tasks", query, headers={**csrf, "Idempotency-Key": str(uuid.uuid4())}); assert code == 202
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            code, completed, _ = request("/demo/api/tasks/" + task["id"])
            if completed["status"] not in ("QUEUED", "RUNNING"): break
            time.sleep(.2)
        assert completed["status"] == "SUCCEEDED" and completed["result"]["value"] == 28000
        assert request("/demo/api/system")[1]["storage"] == "postgres_single_instance"
        for path in ("/health", "/api/workspaces/demo-shop/tasks", "/internal/tasks/claim"):
            assert request(path)[0] == 404
        report["checks"].append("tls_owner_csrf_worker_result_internal_routes")
        app_id = run([*compose, "ps", "--quiet", "app"], capture=True)
        inspect = json.loads(run([*docker, "inspect", app_id], capture=True))[0]
        assert inspect["Config"]["User"] == "1000:1000" and inspect["HostConfig"]["ReadonlyRootfs"]
        assert not inspect["HostConfig"]["PortBindings"]
        report["checks"].append("nonroot_readonly_no_app_host_port")
        report["stage"] = "restart_persistence"
        run([*compose, "restart", "app"])
        run([*compose, "up", "--detach", "--no-build", "--wait", "--wait-timeout", "120"])
        assert request("/demo/api/config")[0] == 401
        csrf = login()
        assert request("/demo/api/tasks/" + task["id"])[1]["result"] == completed["result"]
        assert request("/logout", {}, form=True, headers=csrf)[0] == 302
        assert request("/demo/api/config")[0] == 401
        report["checks"].append("restart_keeps_report_invalidates_session_and_logout")
        report.update(passed=True, stage="complete")
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        try:
            if compose:
                try: run([*compose, "logs", "--no-color"], timeout=30)
                finally: run([*compose, "down", "--volumes", "--remove-orphans"], timeout=60)
        except Exception as cleanup_error:
            report.update(passed=False, cleanup_error_type=type(cleanup_error).__name__)
            raise
        finally:
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            (results / "release.json").write_text(json.dumps(report, indent=2) + "\n")
            log.close()


if __name__ == "__main__": main()
