"""Build a checksum-pinned PostgreSQL ONLY under this personal project.

Local verification toolchain: no system install/service, no existing databases.
TLS/ICU/compression are omitted here; deployment images are a separate artifact.
"""
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "18.6"
SHA256 = "555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f"
URL = f"https://ftp.postgresql.org/pub/source/v{VERSION}/postgresql-{VERSION}.tar.bz2"


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("This source-build recipe is verified on macOS ARM64 only; use the deployment image on Linux.")
    base = ROOT / ".local/postgres-toolchain"; base.mkdir(parents=True, exist_ok=True)
    archive = base / f"postgresql-{VERSION}.tar.bz2"
    if not archive.exists():
        partial = archive.with_name(archive.name + ".part")
        subprocess.run(["/usr/bin/curl", "--fail", "--location", "--connect-timeout", "20",
                        "--max-time", "3600", "--silent", "--show-error", "--continue-at", "-",
                        "--retry", "2", "--output", str(partial), URL], check=True)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != SHA256:
            raise SystemExit("PostgreSQL partial download checksum mismatch; archive was not installed.")
        partial.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("PostgreSQL source SHA mismatch; inspect the project-local download before retrying.")
    source = base / f"postgresql-{VERSION}"
    if not source.exists():
        with tarfile.open(archive) as tar: tar.extractall(base, filter="data")
    prefix = base / "installed"
    configure = [str(source / "configure"), "--prefix=" + str(prefix), "--cache-file=/dev/null",
                 "--without-icu", "--without-readline", "--without-zlib", "--without-lz4", "--without-zstd"]
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C", "CC": "/usr/bin/clang"}
    with (base / "build.log").open("w") as log:
        for command in (configure, ["/usr/bin/make", "-j4"], ["/usr/bin/make", "install"]):
            print("Building project-local PostgreSQL: " + Path(command[0]).name, flush=True)
            subprocess.run(command, cwd=source, env=environment, stdout=log, stderr=log, check=True)
    version = subprocess.check_output([str(prefix / "bin/postgres"), "--version"], env=environment, text=True).strip()
    (base / "manifest.json").write_text(json.dumps({"url": URL, "source_sha256": SHA256, "version": version,
        "configure": configure, "scope": "project-local loopback verification only, no system services"}, indent=2) + "\n")
    print(version + " installed in " + str(prefix))


if __name__ == "__main__":
    main()
