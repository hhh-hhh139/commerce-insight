"""Check Compose's resolved model without contacting any Docker daemon.

This probe uses the SHA-pinned official macOS ARM64 Compose 5.7.0 binary already
downloaded to this project's .local directory. It does not install or start Docker.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
CLI_SHA256 = "54495ff81dd8cae11861455f0308a293a530149beba8f1d90ff3f295b11cf035"


def main():
    cli = ROOT / ".local/compose-toolchain/docker-compose"
    if hashlib.sha256(cli.read_bytes()).hexdigest() != CLI_SHA256:
        raise SystemExit("Expected the verified macOS ARM64 Compose 5.7.0 binary.")
    parent = ROOT / ".local/compose-config-runs"
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    (out / "docker-config").mkdir()
    (out / "empty.env").touch()
    (out / "Caddyfile").write_text("# Configuration-only mount fixture; not started.\n")
    (out / "override.yaml").write_text(
        'services:\n  proxy:\n    ports: !override ["127.0.0.1:18443:18443"]\n'
        f'    volumes:\n      - {out}/Caddyfile:/etc/caddy/Caddyfile:ro\n'
        f'      - {out}:/test-cert:ro\n'
    )
    env = {"PATH": "/usr/bin:/bin", "DOCKER_CONFIG": str(out / "docker-config"),
           "DOCKER_HOST": "unix://" + str(out / "no-daemon.sock"),
           "COMMERCE_ORIGIN": "https://127.0.0.1:18443"}
    record = {"started_at": datetime.now(timezone.utc).isoformat(), "passed": False,
              "kind": "official_compose_configuration_only", "binary_sha256": CLI_SHA256,
              "daemon_target": "nonexistent_project_local_socket", "real_model_calls": 0,
              "container_runtime_verified": False, "checks": [], "source_sha256": {}}
    for name in ("deployment/compose.yaml", "deployment/verify_clean_ci.py",
                 "experiments/deployment/verify_compose_config.py"):
        record["source_sha256"][name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    start = time.monotonic()

    def run(args):
        p = subprocess.run([str(cli), *args], cwd=ROOT, env=env, capture_output=True,
                           text=True, timeout=20, check=True)
        return p.stdout

    try:
        record["version"] = run(["version"]).strip()
        base = ["--project-name", "commerce-config-review", "--env-file", str(out / "empty.env"),
                "-f", str(ROOT / "deployment/compose.yaml")]
        for name, extra in (("base", []), ("ci", ["-f", str(out / "override.yaml")])):
            data = json.loads(run([*base, *extra, "--profile", "public", "config", "--format", "json"]))
            (out / (name + ".json")).write_text(json.dumps(data, indent=2) + "\n")
            app, db, proxy = (data["services"][key] for key in ("app", "db", "proxy"))
            assert app["read_only"] and app["cap_drop"] == ["ALL"] and app["init"]
            assert set(app["tmpfs"]) == {
                "/tmp:mode=1777,size=128m",
                "/app/.local/task-loop:uid=1000,gid=1000,mode=0700,size=128m",
                "/app/.local/ledger-runs:uid=1000,gid=1000,mode=0700,size=128m"}
            assert not app.get("ports") and not db.get("ports")
            assert data["networks"]["business"]["internal"] and list(app["networks"]) == ["business"]
            assert {v["target"] for v in app["volumes"]} == {
                "/app/.local/owner-demo", "/app/.local/single-agent", "/app/.local/state-control"}
            # Compose 5.7.0 serializes memory bytes as a JSON string.
            assert int(app["mem_limit"]) == 1536 * 1024 * 1024 and app["pids_limit"] == 128
            assert {s["source"] for s in app["secrets"]} == {"COMMERCE_DB_PASSWORD"}
            assert "COMMERCE_DB_PASSWORD" not in app["environment"]
            ports = proxy["ports"]
            if name == "base":
                assert {(p["target"], p["published"], p["protocol"]) for p in ports} == {
                    (80, "80", "tcp"), (443, "443", "tcp"), (443, "443", "udp")}
            else:
                assert len(ports) == 1
                assert (ports[0]["host_ip"], str(ports[0]["published"]), ports[0]["target"]) == (
                    "127.0.0.1", "18443", 18443)
                mounts = {v["target"]: v for v in proxy["volumes"]}
                assert mounts["/etc/caddy/Caddyfile"]["source"] == str(out / "Caddyfile")
            record["checks"].append(name)
        record["passed"] = True
    except Exception as error:
        record["error_type"] = type(error).__name__
        raise
    finally:
        record["seconds"] = round(time.monotonic() - start, 3)
        (out / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"result": str(out / "result.json"), **record}, indent=2))


if __name__ == "__main__":
    main()
