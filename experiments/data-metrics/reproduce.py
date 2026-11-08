"""Reproduce SQL metric pitfalls on synthetic data, without network or models."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sqlite3


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent

AGGREGATIONS = {
    "naive_join": """
        SELECT SUM(o.paid_minor)
        FROM orders o LEFT JOIN items i ON i.order_id = o.order_id
    """,
    "amount_distinct": """
        SELECT SUM(DISTINCT o.paid_minor)
        FROM orders o LEFT JOIN items i ON i.order_id = o.order_id
    """,
    "order_identity": """
        SELECT SUM(paid_minor) FROM (
            SELECT DISTINCT o.order_id, o.paid_minor
            FROM orders o LEFT JOIN items i ON i.order_id = o.order_id
        )
    """,
}

EVENT_NET = """
    SELECT COALESCE(SUM(amount_minor), 0) FROM (
        SELECT paid_minor AS amount_minor, paid_at AS event_at FROM orders
        UNION ALL
        SELECT -amount_minor, posted_at FROM refunds
    ) WHERE event_at >= :start AND event_at < :end
"""

COHORT_NET = """
    WITH refunds_as_of AS (
        SELECT order_id, SUM(amount_minor) AS refund_minor
        FROM refunds WHERE posted_at < :as_of
        GROUP BY order_id
    )
    SELECT COALESCE(SUM(o.paid_minor - COALESCE(r.refund_minor, 0)), 0)
    FROM orders o LEFT JOIN refunds_as_of r ON r.order_id = o.order_id
    WHERE o.paid_at >= :start AND o.paid_at < :end
"""


def database(fixture, extra_order=False):
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            paid_minor INTEGER NOT NULL CHECK(paid_minor >= 0),
            paid_at TEXT NOT NULL
        );
        CREATE TABLE items (
            item_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(order_id)
        );
        CREATE TABLE refunds (
            refund_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
            posted_at TEXT NOT NULL
        );
    """)
    orders = list(fixture["orders"])
    items = list(fixture["items"])
    if extra_order:
        orders.append(fixture["equal_amount_order"])
        items.append(fixture["equal_amount_item"])
    db.executemany(
        "INSERT INTO orders VALUES (:order_id, :paid_minor, :paid_at)", orders
    )
    db.executemany("INSERT INTO items VALUES (:item_id, :order_id)", items)
    db.executemany(
        "INSERT INTO refunds VALUES (:refund_id, :order_id, :amount_minor, :posted_at)",
        fixture["refunds"],
    )
    return db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="JSON report inside this project")
    args = parser.parse_args()
    fixture_path = HERE / "fixture.json"
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    observations = {}

    def record(db, name, sql, parameters=None):
        parameters = parameters or {}
        actual = db.execute(sql, parameters).fetchone()[0]
        expected = fixture["expected_observations_minor"][name]
        observations[name] = {
            "actual_minor": actual,
            "expected_observation_minor": expected,
            "matches_expected_observation": actual == expected,
            "sql": sql.strip(),
            "parameters": parameters,
        }

    for prefix, extra in (("base", False), ("equal", True)):
        db = database(fixture, extra_order=extra)
        try:
            for method, sql in AGGREGATIONS.items():
                record(db, prefix + "_" + method, sql)
        finally:
            db.close()

    periods = fixture["periods"]
    db = database(fixture)
    try:
        record(db, "week1_event_net", EVENT_NET, {
            "start": periods["week1_start"], "end": periods["week2_start"]
        })
        record(db, "week2_event_net", EVENT_NET, {
            "start": periods["week2_start"], "end": periods["as_of_exclusive"]
        })
        record(db, "week1_paid_cohort_net_as_of", COHORT_NET, {
            "start": periods["week1_start"], "end": periods["week2_start"],
            "as_of": periods["as_of_exclusive"],
        })
    finally:
        db.close()

    complete = set(observations) == set(fixture["expected_observations_minor"])
    reproduced = complete and all(
        item["matches_expected_observation"] for item in observations.values()
    )
    report = {
        "kind": "synthetic_sql_probe_not_agent_evaluation",
        "fixture_version": fixture["version"],
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runtime": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version},
        "amount_unit": fixture["amount_unit"],
        "currency": fixture["currency"],
        "timezone": fixture["timezone"],
        "observations": observations,
        "all_expected_observations_reproduced": reproduced,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.resolve()
        if not output.is_relative_to(PROJECT):
            parser.error("Output must be inside this project")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print("Saved:", output.relative_to(PROJECT))
    else:
        print(rendered, end="")
    return 0 if reproduced else 1


if __name__ == "__main__":
    raise SystemExit(main())
