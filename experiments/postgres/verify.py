"""Run existing behavioral contracts against actual isolated PostgreSQL, not H2 mode."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.java_local import environment, server_command
from scripts.postgres_local import PostgresFixture


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    loaded = importlib.util.module_from_spec(spec); spec.loader.exec_module(loaded)
    return loaded


loop = module("postgres_loop_contracts", "experiments/task-loop/verify.py")
imports = module("postgres_import_contracts", "experiments/data-import/verify.py")
PG = None


def launch(cls, command, log):
    if "database" not in cls.__dict__: cls.database = PG.database()
    cls.server = subprocess.Popen(command + ["--spring.profiles.active=postgres"], cwd=ROOT,
        env=PG.java_environment(cls.database, environment()), stdout=log, stderr=log)
    end = time.monotonic() + 45
    while time.monotonic() < end:
        if cls.server.poll() is not None: raise RuntimeError("postgres_api_startup_failed")
        try:
            cls.user.call("/health", method="GET"); return
        except OSError: time.sleep(.1)
    cls.server.terminate(); cls.server.wait(timeout=10)
    raise RuntimeError("postgres_api_startup_timeout")


class PostgresTasks(loop.LoopTests):
    @classmethod
    def start_server(cls, snapshot=None):
        launch(cls, server_command(cls.runtime, cls.port, loop.LEASE_MS, 100, snapshot), cls.server_log)

    def test_19_flyway_version_and_repeat_startup(self):
        before = PG.query(self.database, "SELECT version||':'||success FROM flyway_schema_history WHERE version IS NOT NULL").stdout.strip()
        self.assertEqual(before, "1:true")
        self.stop_server(); self.start_server()
        self.assertEqual(PG.query(self.database, "SELECT version||':'||success FROM flyway_schema_history WHERE version IS NOT NULL").stdout.strip(), before)

    def test_20_h2_schema_is_not_a_postgres_migration(self):
        scratch = PG.database()
        original = (ROOT / "services/business-api/src/main/resources/schema.sql").read_text()
        rejected = PG.query(scratch, original, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('syntax error', rejected.stderr.lower())
        self.assertIn('merge into', rejected.stderr.lower())
        first_statement = PG.query(scratch, original.split(';', 1)[0] + ';', check=False)
        self.assertNotEqual(first_statement.returncode, 0)
        self.assertIn('type "clob" does not exist', first_statement.stderr.lower())

    def test_21_modified_migration_refuses_startup(self):
        original = int(PG.query(self.database, "SELECT checksum FROM flyway_schema_history WHERE version='1'").stdout.strip())
        self.stop_server()
        try:
            PG.query(self.database, "UPDATE flyway_schema_history SET checksum=" + str(original ^ 1) + " WHERE version='1'")
            with self.assertRaisesRegex(RuntimeError, "postgres_api_startup_failed"):
                self.start_server()
        finally:
            self.stop_server()
            PG.query(self.database, "UPDATE flyway_schema_history SET checksum=" + str(original) + " WHERE version='1'")
            self.start_server()

    def test_22_failed_upgrade_rolls_back_schema(self):
        directory = self.runtime / "test-migrations"; directory.mkdir()
        name = "V1__business_tasks_and_datasets.sql"
        (directory / name).write_bytes((ROOT / "services/business-api/src/main/resources/db/migration/postgresql" / name).read_bytes())
        (directory / "V2__intentionally_invalid.sql").write_text("CREATE TABLE migration_probe(id INTEGER);\nSELECT 1 / 0;\n")
        self.stop_server()
        try:
            with self.assertRaisesRegex(RuntimeError, "postgres_api_startup_failed"):
                launch(type(self), server_command(self.runtime, self.port, loop.LEASE_MS, 100)
                    + ["--spring.flyway.locations=filesystem:" + str(directory)], self.server_log)
            self.assertEqual(PG.query(self.database, "SELECT to_regclass('public.migration_probe')").stdout.strip(), "")
            self.assertEqual(PG.query(self.database, "SELECT COUNT(*) FROM flyway_schema_history WHERE success").stdout.strip(), "1")
        finally:
            self.stop_server(); self.start_server()


class PostgresImports(imports.ImportHttpTests):
    @classmethod
    def start(cls, enabled=True, live=False):
        launch(cls, server_command(cls.runtime, cls.port) + ["--commerce.demo.enabled=" + str(enabled).lower(),
               "--commerce.demo.agent-enabled=" + str(live).lower()], cls.log)


if __name__ == "__main__":
    started = time.monotonic()
    PG = PostgresFixture()
    try:
        version = PG.query("postgres", "SHOW server_version").stdout.strip()
        migration_only = "--migrations-only" in sys.argv
        suite = (unittest.TestSuite(PostgresTasks(name) for name in (
                    "test_20_h2_schema_is_not_a_postgres_migration", "test_21_modified_migration_refuses_startup", "test_22_failed_upgrade_rolls_back_schema"))
                 if migration_only else unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(PostgresTasks),
                                   unittest.defaultTestLoader.loadTestsFromTestCase(PostgresImports)]))
        result = unittest.TextTestRunner(verbosity=2, resultclass=loop.RecordedResult).run(suite)
        report = {"date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(), "kind": "actual_postgres_contract_regression",
                  "postgres_version": version, "tests": result.testsRun, "passed": result.wasSuccessful(),
                  "seconds": round(time.monotonic() - started, 3), "cases": result.cases, "real_model_calls": 0,
                  "selection": "migration_failures" if migration_only else "all_contracts",
                  "scope": "project-owned temporary PostgreSQL cluster, no system services or existing databases"}
        Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    finally:
        PG.close()
    raise SystemExit(not result.wasSuccessful())
