"""Real Caddy TLS -> Java owner session -> loopback Worker, temporary local trust only."""
from datetime import datetime
import http.cookiejar
import importlib.util
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from commerce_worker.worker import Client, NoRedirect, run_once
from scripts.java_local import environment, prepare, server_command

spec = importlib.util.spec_from_file_location("tls_owner_base", Path(__file__).with_name("verify.py"))
owner = importlib.util.module_from_spec(spec); spec.loader.exec_module(owner)


def port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); return probe.getsockname()[1]


class TlsTests(owner.OwnerTests):
    def setUp(self):
        parent = ROOT / ".local/owner-access/tls"; parent.mkdir(parents=True, exist_ok=True)
        self.runtime = prepare(Path(tempfile.mkdtemp(dir=parent)))
        (self.runtime / "owner.hash").write_text(self.encoded + "\n")
        self.port, self.tls_port = port(), port()
        self.url = f"https://127.0.0.1:{self.tls_port}"
        direct = f"http://127.0.0.1:{self.port}"
        self.worker = Client(direct, (self.runtime / "worker.token").read_text())
        self.user = Client(direct, (self.runtime / "user.token").read_text())
        self.log = (self.runtime / "java.log").open("ab")
        self.proxy_log = (self.runtime / "caddy.log").open("ab")
        self.proxy = self.server = None
        try:
            self.certificate()
            context = ssl.create_default_context(cafile=str(self.runtime / "cert.pem"))
            self.cookies = http.cookiejar.CookieJar()
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=context), urllib.request.HTTPCookieProcessor(self.cookies), NoRedirect())
            self.start()
            config = self.runtime / "Caddyfile"
            config.write_text("{\n admin off\n auto_https off\n}\n" + self.url + " {\n bind 127.0.0.1\n"
                + f' tls "{self.runtime}/cert.pem" "{self.runtime}/key.pem"\n'
                + f' import "{ROOT}/deployment/routes.caddy"\n' + "}\n")
            env = {"PATH": "/usr/bin:/bin", "XDG_CONFIG_HOME": str(self.runtime / "caddy-config"),
                   "XDG_DATA_HOME": str(self.runtime / "caddy-data"), "COMMERCE_UPSTREAM": direct}
            self.proxy = subprocess.Popen([str(ROOT / ".local/caddy-toolchain/caddy"), "run", "--config", str(config),
                "--adapter", "caddyfile"], cwd=ROOT, env=env, stdout=self.proxy_log, stderr=self.proxy_log)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.assertIsNone(self.proxy.poll())
                try:
                    if self.http("/login")[0] == 200: break
                except OSError: pass
                time.sleep(.1)
            else: self.fail("TLS proxy startup timeout")
        except BaseException:
            self.tearDown(); raise

    def certificate(self):
        config = self.runtime / "certificate.cnf"
        config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n")
        subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(self.runtime / "key.pem"), "-out", str(self.runtime / "cert.pem"), "-config", str(config)],
            env={"PATH": "/usr/bin:/bin"}, capture_output=True, check=True)
        (self.runtime / "key.pem").chmod(0o600)

    def start(self, extra=(), valid=True):
        self.server = subprocess.Popen(server_command(self.runtime, self.port) + ["--commerce.demo.enabled=true",
            "--commerce.owner.enabled=true", "--commerce.owner.origin=" + self.url,
            "--server.servlet.session.cookie.secure=true"], cwd=ROOT, env=environment(), stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            self.assertIsNone(self.server.poll())
            try:
                self.user.call("/health", method="GET"); return
            except OSError: time.sleep(.1)
        self.fail("TLS application startup timeout")

    def tearDown(self):
        for process in (self.proxy, self.server):
            if process is not None and process.poll() is None:
                process.terminate(); process.wait(timeout=10)
        self.log.close(); self.proxy_log.close()

    def test_tls_01_login_task_csrf_and_logout(self):
        self.assertEqual(self.http("/demo/api/config")[0], 401)
        code, _, headers = self.http("/session/csrf")
        self.assertEqual(code, 200)
        for flag in ("Secure", "HttpOnly", "SameSite=Strict"): self.assertIn(flag, headers.get("Set-Cookie"))
        headers = self.login()  # Includes actual session ID rotation over verified TLS.
        self.assertEqual(self.http("/demo/api/tasks", owner.QUERY, {"Idempotency-Key": str(uuid.uuid4())})[0], 403)
        self.assertEqual(self.http("/demo/api/tasks", owner.QUERY, {**headers, "Origin": "https://foreign.invalid", "Idempotency-Key": str(uuid.uuid4())})[0], 403)
        code, task, _ = self.http("/demo/api/tasks", owner.QUERY, {**headers, "Idempotency-Key": str(uuid.uuid4())})
        self.assertEqual(code, 202)
        self.assertEqual(run_once(self.worker)["outcome"], "succeeded")
        self.assertEqual(self.http("/demo/api/tasks/" + task["id"])[1]["result"]["value"], 28000)
        self.assertEqual(self.http("/logout", {}, headers, form=True)[0], 302)
        self.assertEqual(self.http("/demo/api/config")[0], 401)

    def test_tls_02_internal_bearer_routes_not_forwarded(self):
        self.login()
        bearer = "Bearer " + (self.runtime / "worker.token").read_text().strip()
        for path in ("/health", "/internal/tasks/claim", "/api/workspaces/demo-shop/tasks", "/actuator/health"):
            with self.subTest(path=path):
                self.assertEqual(self.http(path, headers={"Authorization": bearer, "X-Forwarded-For": "127.0.0.1"})[0], 404)
        self.assertEqual(self.user.call("/health", method="GET")["status"], "ok")

    def test_tls_03_untrusted_certificate_rejected(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaisesRegex(OSError, "CERTIFICATE_VERIFY_FAILED"):
            opener.open(self.url + "/login", timeout=5)


if __name__ == "__main__":
    started = time.monotonic()
    suite = unittest.TestSuite(TlsTests(name) for name in sorted(n for n in dir(TlsTests) if n.startswith("test_tls_")))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    Path(sys.argv[1]).write_text(json.dumps({"date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "kind": "actual_caddy_tls_owner_session", "caddy": "2.11.9", "tests": result.testsRun, "passed": result.wasSuccessful(),
        "seconds": round(time.monotonic() - started, 3), "real_model_calls": 0, "system_trust_modified": False,
        "public_acme_tested": False, "failures": [{"test": str(t), "trace": s} for t,s in result.failures + result.errors]},
        ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(not result.wasSuccessful())
