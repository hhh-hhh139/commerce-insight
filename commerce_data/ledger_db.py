"""A bounded, named-metric DuckDB executor. Does not accept user SQL."""

import hashlib
from pathlib import Path

from .core import ContractError, digest
from .ledger import FIELDS, query_spec, validate_snapshot


TABLE_COLUMNS = {
    "orders": [("order_id", "VARCHAR PRIMARY KEY"), ("status", "VARCHAR"), ("paid_minor", "BIGINT"), ("paid_at", "TIMESTAMPTZ"), ("currency", "VARCHAR")],
    "items": [("item_id", "VARCHAR PRIMARY KEY"), ("order_id", "VARCHAR"), ("product_id", "VARCHAR"), ("line_minor", "BIGINT")],
    "refunds": [("refund_id", "VARCHAR PRIMARY KEY"), ("order_id", "VARCHAR"), ("item_id", "VARCHAR"), ("status", "VARCHAR"), ("amount_minor", "BIGINT"), ("refunded_at", "TIMESTAMPTZ"), ("currency", "VARCHAR")],
}
DB_CONFIG = {"autoload_known_extensions": "false",
             "autoinstall_known_extensions": "false", "threads": "1",
             "memory_limit": "256MB", "max_temp_directory_size": "0B"}


def build_database(snapshot, path):
    import duckdb
    data = validate_snapshot(snapshot)
    path = Path(path)
    if path.exists():
        raise ContractError("database_already_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path), config=DB_CONFIG)
    try:
        connection.execute("BEGIN")
        for table, columns in TABLE_COLUMNS.items():
            if table not in data["tables"]:
                continue
            assert {name for name, _ in columns} == FIELDS[table]
            connection.execute("CREATE TABLE ledger_" + table + " (" + ", ".join(name + " " + kind for name, kind in columns) + ")")
            rows = [[row[name] for name, _ in columns] for row in data["tables"][table]]
            if rows:
                connection.executemany("INSERT INTO ledger_" + table + " VALUES (" + ", ".join("?" for _ in columns) + ")", rows)
        connection.execute("CREATE TABLE snapshot_identity (canonical_sha256 VARCHAR)")
        connection.execute("INSERT INTO snapshot_identity VALUES (?)", [digest(data)])
        connection.execute("COMMIT")
    finally:
        connection.close()
    return path


class ControlledLedger:
    def __init__(self, snapshot, path, source_evidence=None):
        import duckdb
        self.data = validate_snapshot(snapshot)
        self.path = Path(path)
        self.evidence = dict(source_evidence or {})
        self.connection = duckdb.connect(str(path), read_only=True, config=DB_CONFIG)
        # DuckDB must open its own database/recovery files before we disable
        # external readers. No caller SQL executes during this initialization.
        self.connection.execute("SET enable_external_access = false")
        identity = self.connection.execute("SELECT canonical_sha256 FROM snapshot_identity").fetchall()
        if identity != [(digest(self.data),)]:
            self.connection.close()
            raise ContractError("database_snapshot_mismatch")
        self.connection.execute("SET lock_configuration = true")

    def query(self, metric, start, end, *, workspace_id, as_of=None, product_id=None):
        spec = query_spec(self.data, metric, start, end, workspace_id=workspace_id, as_of=as_of, product_id=product_id)
        result = self.connection.execute(spec["sql"], spec["params"]).fetchone()[0]
        source_dir = Path(__file__).parent
        return {**spec, "value": int(result), "evidence": {
            **self.evidence, "canonical_sha256": digest(self.data),
            "implementation_sha256": {name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
                                      for name in ["ledger.py", "ledger_db.py"]},
        }}

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
