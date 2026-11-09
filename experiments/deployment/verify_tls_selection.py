"""Actual Caddy no-SNI selection probe. Own IPv4/IPv6 loopback only."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import socket
import ssl
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".local/ci-tls-probe"
HERE.mkdir(parents=True, exist_ok=True)
ENV = {"PATH": "/usr/bin:/bin", "XDG_CONFIG_HOME": str(HERE / "config"),
       "XDG_DATA_HOME": str(HERE / "data")}
cnf = HERE / "openssl.cnf"
cnf.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n")
subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-keyout", str(HERE / "key.pem"), "-out", str(HERE / "cert.pem"), "-config", str(cnf)],
               env=ENV, capture_output=True, check=True)
(HERE / "key.pem").chmod(0o600)
result = {"kind": "actual_caddy_loopback_destination_mismatch", "caddy": "2.11.9",
          "date": datetime.now(timezone.utc).date().isoformat(), "real_model_calls": 0, "system_trust_modified": False,
          "container_test": False, "cases": []}

def handshake(port, *, trusted=True, name="127.0.0.1", address="::1"):
    context = ssl.create_default_context(cafile=str(HERE / "cert.pem") if trusted else None)
    with socket.create_connection((address, port), timeout=3) as raw:
        with context.wrap_socket(raw, server_hostname=name) as tls:
            tls.sendall(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
            return tls.recv(4096).split(b"\r\n", 1)[0].decode()

for fixed in (False, True):
    with socket.socket(socket.AF_INET6) as reserve:
        reserve.bind(("::1", 0)); port = reserve.getsockname()[1]
    config = HERE / ("fixed.Caddyfile" if fixed else "baseline.Caddyfile")
    config.write_text("{\n admin off\n auto_https off\n debug\n" + (" default_sni 127.0.0.1\n" if fixed else "")
                     + "}\n" + f"https://127.0.0.1:{port} {{\n bind ::1\n"
                     + f' tls "{HERE}/cert.pem" "{HERE}/key.pem"\n respond "ok"\n}}\n')
    with (HERE / ("fixed.log" if fixed else "baseline.log")).open("w") as log:
        process = subprocess.Popen([str(ROOT / ".local/caddy-toolchain/caddy"), "run", "--config", str(config),
                                    "--adapter", "caddyfile"], env=ENV, cwd=ROOT, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 8
            while True:
                assert process.poll() is None
                try:
                    with socket.create_connection(("::1", port), timeout=.2): break
                except OSError:
                    if time.monotonic() > deadline: raise
                    time.sleep(.1)
            try:
                response = handshake(port)
                assert fixed and response == "HTTP/1.1 200 OK", response
                result["cases"].append({"case": "fixed_trusted", "result": response})
            except ssl.SSLError as error:
                assert not fixed and "TLSV1_ALERT_INTERNAL_ERROR" in str(error), str(error)
                result["cases"].append({"case": "baseline_mismatched_destination", "error": str(error)})
            if fixed:
                for label, kwargs in (("untrusted", {"trusted": False}), ("wrong_ip", {"name": "127.0.0.2"})):
                    try: handshake(port, **kwargs)
                    except ssl.SSLCertVerificationError as error:
                        result["cases"].append({"case": label, "error": str(error)})
                    else: raise AssertionError(label + " should reject certificate")
        finally:
            process.terminate(); process.wait(timeout=5)
result["passed"] = len(result["cases"]) == 4
result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(HERE / "result.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
