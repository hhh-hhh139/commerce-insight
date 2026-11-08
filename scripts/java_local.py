"""Build/run only with project-local tools, Maven settings, cache and demo tokens."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".local/java-toolchain"


def environment():
    # Do not load user Maven/Java startup hooks, settings or repository credentials.
    allowed = ("HOME", "TMPDIR", "LANG", "LC_ALL", "SystemRoot", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "no_proxy")
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    # Project-owned macOS bundles and Linux unpacked/image toolchains.
    jdks = sorted(p for p in [*TOOLS.glob("jdk-*/Contents/Home"), *TOOLS.glob("jdk-*")]
                  if (p / "bin/java").is_file() and p.resolve().is_relative_to(TOOLS.resolve()))
    if len(jdks) != 1:
        raise SystemExit("Install exactly one verified JDK 21 under .local/java-toolchain; see deployment instructions")
    env.update(JAVA_HOME=str(jdks[0]), PATH=str(jdks[0] / "bin") + ":/usr/bin:/bin", MAVEN_SKIP_RC="true")
    return env


def java():
    return str(Path(environment()["JAVA_HOME"]) / "bin/java")


def prepare(runtime):
    runtime = Path(runtime).resolve()
    if not runtime.is_relative_to(ROOT / ".local"):
        raise ValueError("runtime_must_be_project_local")
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    for role in ("user", "worker"):
        path = runtime / (role + ".token")
        if not path.exists():
            with path.open("x") as f:
                os.chmod(path, 0o600)
                f.write(secrets.token_urlsafe(32) + "\n")
    return runtime


def server_command(runtime, port=18080, lease_ms=15000, retry_ms=1000, snapshot=None):
    runtime = prepare(runtime)
    return [java(), "-jar", str(ROOT / "services/business-api/target/business-api-0.1.0.jar"),
            "--commerce.runtime=" + str(runtime), "--commerce.snapshot=" + str(snapshot or ROOT / "examples/ledger/snapshot.json"),
            "--server.port=" + str(port), "--commerce.lease-ms=" + str(lease_ms), "--commerce.retry-ms=" + str(retry_ms)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("build", "serve", "prepare"))
    parser.add_argument("--runtime", type=Path, default=ROOT / ".local/task-loop/demo")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.runtime)
        print("Local demo credentials prepared (values not displayed).")
        return 0
    env = environment()
    if args.action == "build":
        settings = TOOLS / "settings.xml"
        settings.write_text('<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"><mirrors><mirror><id>public-central-only</id><mirrorOf>*</mirrorOf><url>https://repo.maven.apache.org/maven2</url></mirror></mirrors></settings>\n')
        global_settings = TOOLS / "global-settings.xml"
        global_settings.write_text('<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"/>\n')
        command = [str(TOOLS / "apache-maven-3.9.16/bin/mvn"), "-B", "-ntp", "-s", str(settings), "-gs", str(global_settings),
                   "-Dmaven.repo.local=" + str(ROOT / ".local/m2"), "-f", str(ROOT / "services/business-api/pom.xml"), "package"]
    else:
        command = server_command(args.runtime, args.port)
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
