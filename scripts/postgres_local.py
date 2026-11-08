"""Owned, temporary loopback PostgreSQL fixture. Never discovers existing servers."""
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".local/postgres-toolchain/installed/bin"


class PostgresFixture:
    def __init__(self):
        if not (BIN / "postgres").is_file():
            raise RuntimeError("Build the project-local PostgreSQL toolchain first")
        parent = ROOT / ".local/postgres-verification"; parent.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="cluster-", dir=parent))
        self.data = self.root / "data"
        self.user = "commerce_test"
        self.password = secrets.token_urlsafe(32)
        self.environment = {"PATH": str(BIN) + ":/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
        self.started = False
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); self.port = probe.getsockname()[1]
        password_file = self.root / "init-password"
        with password_file.open("x") as output:
            password_file.chmod(0o600); output.write(self.password + "\n")
        try:
            with (self.root / "init.log").open("wb") as output:
                subprocess.run([str(BIN / "initdb"), "-D", str(self.data), "-U", self.user,
                    "--pwfile=" + str(password_file), "--auth-host=scram-sha-256", "--auth-local=reject",
                    "--locale=C", "--encoding=UTF8"], env=self.environment, stdout=output, stderr=output, check=True)
        finally:
            password_file.unlink(missing_ok=True)
        with (self.data / "postgresql.conf").open("a") as output:
            output.write(f"\nlisten_addresses='127.0.0.1'\nport={self.port}\nunix_socket_directories=''\nmax_connections=32\nshared_buffers='16MB'\n")
        try:
            self.start()
        except Exception:
            # A timeout is not proof that our server stopped. Check only this data directory.
            self.started = subprocess.run([str(BIN / "pg_ctl"), "-D", str(self.data), "status"],
                env=self.environment, capture_output=True).returncode == 0
            self.close()
            raise

    def start(self):
        subprocess.run([str(BIN / "pg_ctl"), "-D", str(self.data), "-l", str(self.root / "postgres.log"),
                        "-w", "-t", "30", "start"], env=self.environment, capture_output=True, check=True)
        self.started = True

    def close(self):
        if self.started:
            subprocess.run([str(BIN / "pg_ctl"), "-D", str(self.data), "-m", "fast", "-w", "-t", "30", "stop"],
                           env=self.environment, capture_output=True, check=True)
            self.started = False

    def query(self, database, statement, *, check=True):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database): raise ValueError("invalid_test_database")
        process = subprocess.run([str(BIN / "psql"), "-X", "-h", "127.0.0.1", "-p", str(self.port), "-U", self.user,
                                  "-d", database, "-v", "ON_ERROR_STOP=1", "-At", "-c", statement],
                                 env={**self.environment, "PGPASSWORD": self.password}, capture_output=True, text=True)
        if check and process.returncode:
            raise RuntimeError("project_postgres_query_failed")  # Never echo credential-bearing driver diagnostics.
        return process

    def database(self):
        name = "test_" + uuid.uuid4().hex
        self.query("postgres", "CREATE DATABASE " + name)
        return name

    def java_environment(self, database, base):
        return {**base, "COMMERCE_DB_URL": f"jdbc:postgresql://127.0.0.1:{self.port}/{database}",
                "COMMERCE_DB_USER": self.user, "COMMERCE_DB_PASSWORD": self.password}
