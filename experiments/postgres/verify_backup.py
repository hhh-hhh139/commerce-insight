"""Actual limited-role PostgreSQL, cross-store recovery, LangGraph resume; no API billing."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.backup_state import Database, backup, restore, sha
from scripts.postgres_local import BIN, PostgresFixture
from scripts.prepare_owner import password_hash
from scripts.java_local import environment, prepare
from scripts.state_guard import state_access
from commerce_agent.models import ScriptedModel
from commerce_agent.runtime import execute_agent
from commerce_worker.worker import Client, run_once


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result); return result


contracts = module("backup_postgres_contracts", "experiments/postgres/verify.py")
context = module("backup_context_contracts", "experiments/context-evaluation/verify.py")
PG = None


class LimitedPostgres(PostgresFixture):
    def database(self):
        name = super().database()
        self.query(name, "REVOKE ALL ON DATABASE " + name + " FROM PUBLIC; GRANT CONNECT ON DATABASE " + name
                   + " TO commerce_app; REVOKE ALL ON SCHEMA public FROM PUBLIC; ALTER SCHEMA public OWNER TO commerce_app;")
        return name

    def java_environment(self, database, base):
        return {**super().java_environment(database, base), "COMMERCE_DB_USER": "commerce_app",
                "COMMERCE_DB_PASSWORD": self.app_password}

    def connection(self, name):
        return Database("127.0.0.1", self.port, name, "commerce_app", self.app_password, BIN)


class BackupTests(contracts.PostgresImports):
    # Inherited acceptance methods are not selected by this standalone runner.
    def test_backup_01_end_to_end_restore(self):
        original_runtime = self.runtime
        original_database = self.database
        parent = Path(tempfile.mkdtemp(dir=ROOT / ".local/postgres-verification", prefix="recovery-"))
        agent = parent / "agent"; agent.mkdir()
        (original_runtime / "owner.hash").write_text(password_hash(secrets.token_urlsafe(24)) + "\n")
        # Copy the real ledger byte-for-byte into a test fixture; never mutate its original.
        ledger = ROOT / ".local/single-agent/personal-budget-2026-11-06.json"
        ledger_before = sha(ledger)
        shutil.copyfile(ledger, agent / ledger.name)
        dataset = self.publish()
        completed = self.query(dataset)
        pending = self.create({**contracts.imports.web.QUERY, "dataset_id": dataset["dataset_id"]})
        self.assertEqual(pending["status"], "QUEUED")
        case = context.CASES["development"][0]; request = context.job(case)
        stopped = execute_agent(request, context.SNAPSHOT, ScriptedModel(script=context.script(case)),
                                state_root=agent / "runs", interrupt_after=["tools"])
        self.assertTrue(stopped["interrupted"])
        db = PG.connection(self.database)
        with self.assertRaisesRegex(RuntimeError, "database_has_other_connections"):
            backup(db, original_runtime, agent, parent / "while-running")
        self.stop()
        try:
            bundle = parent / "backup"
            backup(db, original_runtime, agent, bundle)
            target_db = PG.database(); new_runtime = parent / "restored-runtime"; new_agent = parent / "restored-agent"
            restore(PG.connection(target_db), bundle, new_runtime, new_agent)
            self.assertEqual(sha(new_agent / ledger.name), ledger_before)
            self.assertEqual(sha(ledger), ledger_before)
            for name in ("owner.hash", "user.token", "worker.token"):
                self.assertEqual(sha(original_runtime / name), sha(new_runtime / name))
            # Switch this test's server to the restored DB and the copied application credentials.
            type(self).runtime = new_runtime; type(self).database = target_db
            type(self).user = Client(self.url, (new_runtime / "user.token").read_text())
            type(self).worker = Client(self.url, (new_runtime / "worker.token").read_text())
            self.start()
            self.assertEqual(self.get(completed)["result"], completed["result"])
            restored_dataset = self.http("/demo/api/imports/" + dataset["id"])[1]
            self.assertEqual(restored_dataset["dataset"], dataset["dataset"])
            self.assertEqual(restored_dataset["dataset"]["model_allowed"], False)
            run_once(self.worker)
            self.assertEqual(self.get(pending)["result"]["value"], 23000)
            self.assertEqual(sum(e["kind"] == "SUCCEEDED" for e in self.get(pending)["events"]), 1)
            resumed = execute_agent(request, context.SNAPSHOT, ScriptedModel(script=context.script(case)), state_root=new_agent / "runs")
            self.assertTrue(resumed["provenance"]["resumed"])
            self.assertEqual(resumed["usage"]["model_attempts"], 2)
            self.assertEqual(resumed["usage"]["tool_attempts"], 2)
            self.assertEqual(context.audit_publication(case, resumed), [])
            self.assertEqual(self.http("/demo/api/system")[1]["storage"], "postgres_single_instance")
            self.stop()
            # Both a used state target and nonempty database are refused.
            with self.assertRaisesRegex(ValueError, "restore_state_target_exists"):
                restore(PG.connection(target_db), bundle, new_runtime, new_agent)
            with self.assertRaisesRegex(RuntimeError, "restore_database_not_empty"):
                restore(PG.connection(target_db), bundle, parent / "unused-runtime", parent / "unused-agent")
            self.assertFalse((parent / "unused-runtime").exists())
            corrupted = parent / "corrupted"; shutil.copytree(bundle, corrupted)
            with (corrupted / "database.dump").open("ab") as stream: stream.write(b"broken")
            with self.assertRaisesRegex(ValueError, "backup_checksum_mismatch"):
                restore(PG.connection(PG.database()), corrupted, parent / "bad-runtime", parent / "bad-agent")
            self.assertFalse((parent / "bad-runtime").exists())
            (corrupted / "runtime/link").symlink_to(ledger)
            with self.assertRaisesRegex(ValueError, "backup_symlink_rejected"):
                restore(PG.connection(PG.database()), corrupted, parent / "link-runtime", parent / "link-agent")
            # Named Docker volumes expose an existing empty mount root. The opt-in
            # must restore it without allowing an existing budget to be replaced.
            mount_agent = parent / "empty-agent-mount"; mount_agent.mkdir()
            mount_runtime = parent / "mount-runtime"
            mount_db = PG.database()
            with self.assertRaisesRegex(ValueError, "restore_state_target_exists"):
                restore(PG.connection(mount_db), bundle, mount_runtime, mount_agent)
            self.assertFalse(mount_runtime.exists())
            restore(PG.connection(mount_db), bundle, mount_runtime, mount_agent, allow_empty_targets=True)
            self.assertEqual(sha(mount_agent / ledger.name), ledger_before)
            self.assertEqual(mount_agent.stat().st_mode & 0o777, 0o700)
            self.assertFalse((mount_agent / "RESTORE_INCOMPLETE").exists())
            with self.assertRaisesRegex(ValueError, "restore_state_target_exists"):
                restore(PG.connection(mount_db), bundle, parent / "must-not-create", mount_agent,
                        allow_empty_targets=True)
            self.assertFalse((parent / "must-not-create").exists())
            self.assertEqual(sha(mount_agent / ledger.name), ledger_before)
        finally:
            self.stop()
            type(self).runtime = original_runtime; type(self).database = original_database
            type(self).user = Client(self.url, (original_runtime / "user.token").read_text())
            type(self).worker = Client(self.url, (original_runtime / "worker.token").read_text())
            self.start()
        self.assertEqual(sha(ledger), ledger_before)

    def test_backup_02_schema_owner_has_no_cluster_admin_rights(self):
        db = PG.connection(self.database)
        self.assertEqual(db.query("SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname=current_user"), "f")
        for statement in ("CREATE DATABASE forbidden_database", "CREATE ROLE forbidden_role",
                          "COPY (SELECT 1) TO PROGRAM 'false'"):
            with self.subTest(statement=statement), self.assertRaisesRegex(RuntimeError, "database_psql_failed"):
                db.query(statement)
        self.assertEqual(db.query("SELECT has_schema_privilege(current_user,'public','CREATE')"), "t")

    def test_backup_03_maintenance_and_runtime_exclude_each_other(self):
        with state_access():
            with self.assertRaisesRegex(RuntimeError, "application_state_in_use"):
                with state_access(maintenance=True): pass
        with state_access(maintenance=True):
            with self.assertRaisesRegex(RuntimeError, "application_state_in_use"):
                with state_access(): pass

    def test_backup_04_postgres_launcher_stops_both_children(self):
        self.launcher_check(configtree=False)

    def test_backup_05_configtree_password_and_container_logs(self):
        self.launcher_check(configtree=True)

    def launcher_check(self, configtree):
        parent = Path(tempfile.mkdtemp(dir=ROOT / ".local/postgres-verification", prefix="launcher-"))
        runtime = prepare(parent / "runtime")
        (runtime / "owner.hash").write_text(password_hash(secrets.token_urlsafe(24)) + "\n")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        database = PG.database()
        env = PG.java_environment(database, environment())
        extra = []
        if configtree:
            directory = parent / "secrets"; directory.mkdir(mode=0o700)
            (directory / "COMMERCE_DB_PASSWORD").write_text(env.pop("COMMERCE_DB_PASSWORD"))
            (directory / "COMMERCE_DB_PASSWORD").chmod(0o600)
            extra = ["--db-secrets", str(directory), "--console-logs"]
        user = Client(f"http://127.0.0.1:{port}", (runtime / "user.token").read_text())
        with (parent / "launcher.log").open("wb") as output:
            process = subprocess.Popen([sys.executable, "scripts/web_demo.py", "--owner", "--postgres",
                "--port", str(port), "--runtime", str(runtime), *extra], cwd=ROOT,
                env=env, stdout=output, stderr=output)
            try:
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    self.assertIsNone(process.poll())
                    try:
                        status = user.call("/api/workspaces/demo-shop/system", method="GET")
                        if status["worker_last_poll_at"]: break
                    except OSError: pass
                    time.sleep(.15)
                else: self.fail("managed launcher not ready")
                self.assertEqual(status["storage"], "postgres_single_instance")
                with self.assertRaisesRegex(RuntimeError, "application_state_in_use"):
                    with state_access(maintenance=True): pass
            finally:
                process.terminate(); process.wait(timeout=15)
            self.assertEqual(process.returncode, 0)
        # The launcher stopped only its own process groups; no residual DB connection remains.
        PG.connection(database).idle()
        with self.assertRaises(OSError): user.call("/health", method="GET")
        with state_access(maintenance=True): pass
        if configtree:
            self.assertFalse((runtime / "java.log").exists())
            self.assertFalse((runtime / "worker.log").exists())
            self.assertNotIn(PG.app_password, (parent / "launcher.log").read_text())


if __name__ == "__main__":
    started = time.monotonic(); PG = LimitedPostgres(); contracts.PG = PG
    PG.app_password = secrets.token_urlsafe(32)
    PG.query("postgres", "CREATE ROLE commerce_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '" + PG.app_password + "'")
    try:
        suite = unittest.TestSuite(BackupTests(name) for name in sorted(n for n in dir(BackupTests) if n.startswith("test_backup_")))
        result = unittest.TextTestRunner(verbosity=2, resultclass=contracts.loop.RecordedResult).run(suite)
        Path(sys.argv[1]).write_text(json.dumps({"date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "kind": "actual_postgres_state_recovery", "tests": result.testsRun, "passed": result.wasSuccessful(),
            "seconds": round(time.monotonic() - started, 3), "cases": result.cases, "real_model_calls": 0,
            "postgres_role": "schema owner without cluster administration", "original_budget_modified": False}, ensure_ascii=False, indent=2) + "\n")
    finally: PG.close()
    raise SystemExit(not result.wasSuccessful())
