"""Same data/metrics, controlled DuckDB vs explicit Wren SDK. No model calls."""

import argparse
import base64
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
from decimal import Decimal
from importlib.metadata import distributions, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["WREN_HOME"] = str(ROOT / ".local/query-layer/wren-home")
os.environ["WREN_PROJECT_HOME"] = str(ROOT / "experiments/query-layer")

from commerce_data.ledger import load_snapshot, query_spec
from commerce_data.ledger_db import ControlledLedger, build_database


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_value(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else format(value, "f")
    raise TypeError("Unsupported report value: " + type(value).__name__)


def wren_sql(spec):
    from sqlglot import exp
    template = spec["sql"]
    for physical, model in [("ledger_orders", "Orders"), ("ledger_items", "Items"), ("ledger_refunds", "Refunds")]:
        template = template.replace(physical, '"' + model + '"')
    pieces = template.split("?")
    assert len(pieces) == len(spec["params"]) + 1
    # Templates are internal; parameter text never participates in splitting.
    # Wren 0.17.1's query API has no bind-parameters argument.
    result = pieces[0]
    for value, piece in zip(spec["params"], pieces[1:]):
        result += exp.Literal.string(value).sql(dialect="duckdb") + piece
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from wren import WrenEngine, DataSource
    from wren.config import WrenConfig

    source = ROOT / "examples/ledger/snapshot.json"
    data, evidence = load_snapshot(source)
    mdl_path = ROOT / "experiments/query-layer/mdl.json"
    mdl = base64.b64encode(mdl_path.read_bytes()).decode()
    cases_path = ROOT / "experiments/query-layer/cases.json"
    cases = json.loads(cases_path.read_text())
    negative_sql = [
        ("naive_join", 'SELECT SUM(o.paid_minor) AS value FROM "Orders" o JOIN "Items" i ON o.order_id = i.order_id WHERE o.paid_at >= \'2026-10-05T00:00:00Z\' AND o.paid_at < \'2026-10-12T00:00:00Z\''),
        ("distinct_amount", 'SELECT SUM(DISTINCT o.paid_minor) AS value FROM "Orders" o JOIN "Items" i ON o.order_id = i.order_id WHERE o.paid_at >= \'2026-10-05T00:00:00Z\' AND o.paid_at < \'2026-10-12T00:00:00Z\''),
    ]
    temp_root = ROOT / ".local/query-layer"
    temp_root.mkdir(parents=True, exist_ok=True)
    result = {"experiment": "controlled-vs-wren-v1", "environment": {
        "python": platform.python_version(),
        "packages": {dist.metadata["Name"]: dist.version for dist in distributions()},
    }, "evidence": {**evidence, "mdl_sha256": sha(mdl_path), "cases_sha256": sha(cases_path),
                    "script_sha256": sha(__file__), "lock_sha256": sha(ROOT / "experiments/query-layer/requirements.lock")},
        "configuration": {"wren_strict_mode": True, "memory_or_llm": False,
                          "wren_connection": "explicit dedicated fixture directory, no global profile"},
        "cases": [], "relationship_examples": [], "negative_aggregation_examples": [], "boundary_checks": []}
    with tempfile.TemporaryDirectory(dir=temp_root, prefix="comparison-") as directory:
        run = Path(directory)
        warehouse = run / "warehouse"
        database = build_database(data, warehouse / "fixture.duckdb")
        database_before = sha(database)
        sentinel = run / "sentinel.csv"
        sentinel.write_text("value\nSYNTHETIC_SENTINEL\n")
        with ControlledLedger(data, database, evidence) as controlled:
            # Close this connection before SDK attach: DuckDB requires compatible
            # per-database configuration when several handles share a process.
            for case in cases:
                params = {k: v for k, v in case.items() if k not in {"id", "expected"}}
                before = time.perf_counter()
                answer = controlled.query(**params, workspace_id="demo-shop")
                result["cases"].append({"id": case["id"], "expected": case["expected"],
                                        "controlled": answer, "controlled_ms": (time.perf_counter() - before) * 1000,
                                        "controlled_correct": answer["value"] == case["expected"]})
            for name, sql in negative_sql:
                direct_sql = sql.replace('"Orders"', 'ledger_orders').replace('"Items"', 'ledger_items')
                result["negative_aggregation_examples"].append({
                    "case": name, "sql": sql, "business_expected": 28000,
                    "direct_duckdb_value": int(controlled.connection.execute(direct_sql).fetchone()[0]),
                    "note": "Internal direct SQL comparison; named-metric API does not expose this SQL input",
                })
            for name, sql in [
                ("database_write", "UPDATE ledger_orders SET paid_minor = 1"),
                ("external_file", "SELECT * FROM read_csv('" + str(sentinel).replace("'", "''") + "')"),
            ]:
                try:
                    controlled.connection.execute(sql).fetchall()
                    result["boundary_checks"].append({"backend": "controlled_database", "case": name, "rejected": False})
                except Exception as exc:
                    result["boundary_checks"].append({"backend": "controlled_database", "case": name, "rejected": True, "error_type": type(exc).__name__})
        with WrenEngine(mdl, DataSource.duckdb, {"url": str(warehouse), "format": "duckdb"}, config=WrenConfig(strict_mode=True)) as engine:
            for case, row in zip(cases, result["cases"]):
                spec = row["controlled"]
                sql = wren_sql(spec)
                row["wren_sql"] = sql
                before = time.perf_counter()
                try:
                    row["wren_plan"] = engine.dry_plan(sql)
                    values = engine.query(sql).to_pylist()
                    if len(values) != 1 or len(values[0]) != 1:
                        raise ValueError("Expected exactly one scalar result")
                    value = next(iter(values[0].values()))
                    if value is None or value != int(value):
                        raise ValueError("Expected exact integer minor units or count")
                    row["wren_value"] = int(value)
                    row["wren_correct"] = row["wren_value"] == case["expected"]
                except Exception as exc:
                    row["wren_correct"] = False
                    row["wren_error"] = str(exc).replace(str(ROOT), "<project>")
                row["wren_plan_plus_query_ms"] = (time.perf_counter() - before) * 1000
            examples = [
                ("relationship_item_sum", 'SELECT order_id, item_total_minor FROM "Orders" WHERE order_id = \'A\'', [{"order_id": "A", "item_total_minor": 10000}]),
                ("relationship_successful_refunds", 'SELECT order_id, successful_refunds_all_snapshot_minor FROM "Orders" WHERE order_id = \'A\'', [{"order_id": "A", "successful_refunds_all_snapshot_minor": 3000}]),
            ]
            for name, sql, expected in examples:
                entry = {"case": name, "sql": sql, "expected": expected}
                try:
                    entry["plan"] = engine.dry_plan(sql)
                    entry["actual"] = engine.query(sql).to_pylist()
                    entry["correct"] = entry["actual"] == expected
                except Exception as exc:
                    entry.update({"correct": False, "error": str(exc).replace(str(ROOT), "<project>")})
                result["relationship_examples"].append(entry)
            for entry in result["negative_aggregation_examples"]:
                sql = entry["sql"]
                try:
                    entry["actual"] = engine.query(sql).to_pylist()
                    entry["plan"] = engine.dry_plan(sql)
                except Exception as exc:
                    entry["error"] = str(exc).replace(str(ROOT), "<project>")
            for name, sql in [
                ("write", 'DELETE FROM "Orders"'),
                ("unmodeled_table", "SELECT * FROM fixture.main.snapshot_identity"),
                ("external_file", "SELECT * FROM read_csv('" + str(sentinel).replace("'", "''") + "')"),
                ("multi_statement", 'SELECT * FROM "Orders"; DELETE FROM "Orders"'),
                ("source_generator", "SELECT * FROM range(5)"),
            ]:
                try:
                    engine.query(sql)
                    result["boundary_checks"].append({"backend": "wren_strict_sdk", "case": name, "rejected": False})
                except Exception as exc:
                    result["boundary_checks"].append({"backend": "wren_strict_sdk", "case": name, "rejected": True, "error_type": type(exc).__name__, "error": str(exc).replace(str(run), "<run>")})
        result["database_unchanged"] = sha(database) == database_before
    result["summary"] = {"controlled_correct": sum(r["controlled_correct"] for r in result["cases"]),
                         "wren_correct": sum(r["wren_correct"] for r in result["cases"]),
                         "case_count": len(cases), "all_boundary_checks_rejected": all(r["rejected"] for r in result["boundary_checks"])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=json_value) + "\n")
    print(json.dumps({"output": str(args.output), "summary": result["summary"], "database_unchanged": result["database_unchanged"]}))


if __name__ == "__main__":
    main()
