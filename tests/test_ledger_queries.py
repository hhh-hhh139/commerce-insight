import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from commerce_data.core import ContractError
from commerce_data.ledger import load_snapshot
from commerce_data.ledger_db import ControlledLedger, build_database


ROOT = Path(__file__).resolve().parents[1]
START, SPLIT, END = "2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z", "2026-10-19T00:00:00Z"


@unittest.skipUnless(importlib.util.find_spec("duckdb"), "requires query-layer environment")
class LedgerQueryTests(unittest.TestCase):
    def setUp(self):
        self.data, self.evidence = load_snapshot(ROOT / "examples/ledger/snapshot.json")
        parent = ROOT / ".local/test-ledger"
        parent.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=parent)
        self.path = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def tool(self, data=None):
        data = data or self.data
        path = build_database(data, self.path / "fixture.duckdb")
        return ControlledLedger(data, path, self.evidence)

    def test_fixed_business_answers(self):
        cases = json.loads((ROOT / "experiments/query-layer/cases.json").read_text())
        with self.tool() as tool:
            for case in cases:
                with self.subTest(case=case["id"]):
                    params = {k: v for k, v in case.items() if k not in {"id", "expected"}}
                    result = tool.query(**params, workspace_id="demo-shop")
                    self.assertEqual(result["value"], case["expected"])
                    self.assertEqual(result["evidence"]["source_sha256"], self.evidence["source_sha256"])

    def test_no_arbitrary_sql_or_wrong_scope(self):
        with self.tool() as tool:
            for metric, workspace, reason in [("SELECT 1", "demo-shop", "unsupported_metric"), ("paid_amount", "other", "scope_mismatch")]:
                with self.assertRaisesRegex(ContractError, reason):
                    tool.query(metric, START, END, workspace_id=workspace)

    def test_database_snapshot_pair_must_match(self):
        path = build_database(self.data, self.path / "fixture.duckdb")
        changed = copy.deepcopy(self.data)
        changed["snapshot_id"] = "different"
        with self.assertRaisesRegex(ContractError, "database_snapshot_mismatch"):
            ControlledLedger(changed, path)

    def test_missing_refunds_stays_unknown(self):
        del self.data["tables"]["refunds"]
        with self.tool() as tool:
            self.assertEqual(tool.query("paid_amount", START, SPLIT, workspace_id="demo-shop")["value"], 28000)
            with self.assertRaisesRegex(ContractError, "refund_data_unavailable"):
                tool.query("event_net", START, SPLIT, workspace_id="demo-shop")

    def test_explicit_empty_refund_table(self):
        self.data["tables"]["refunds"] = []
        with self.tool() as tool:
            self.assertEqual(tool.query("event_net", START, SPLIT, workspace_id="demo-shop")["value"], 28000)

    def test_product_attribution_after_explicit_link(self):
        next(r for r in self.data["tables"]["refunds"] if r["refund_id"] == "R3")["item_id"] = "B1"
        with self.tool() as tool:
            self.assertEqual(tool.query("product_refund_amount", SPLIT, END, workspace_id="demo-shop", product_id="P1")["value"], 4000)

    def test_repeated_product_in_order_does_not_duplicate_order_amount(self):
        self.data["tables"]["items"][1]["product_id"] = "P1"
        with self.tool() as tool:
            self.assertEqual(tool.query("paid_amount_for_product_orders", START, SPLIT, workspace_id="demo-shop", product_id="P1")["value"], 18000)

    def test_start_inclusive_end_exclusive(self):
        next(o for o in self.data["tables"]["orders"] if o["order_id"] == "D")["paid_at"] = SPLIT
        with self.tool() as tool:
            self.assertEqual(tool.query("paid_amount", START, SPLIT, workspace_id="demo-shop")["value"], 28000)
            self.assertEqual(tool.query("paid_amount", SPLIT, END, workspace_id="demo-shop")["value"], 15000)

    def test_storage_rejects_mutation_and_external_file(self):
        sentinel = self.path / "sentinel.csv"
        sentinel.write_text("value\nSYNTHETIC\n")
        with self.tool() as tool:
            with self.assertRaises(Exception):
                tool.connection.execute("DELETE FROM ledger_orders")
            with self.assertRaises(Exception):
                tool.connection.execute("SELECT * FROM read_csv(?)", [str(sentinel)])
            self.assertEqual(tool.query("paid_order_count", START, SPLIT, workspace_id="demo-shop")["value"], 3)


if __name__ == "__main__":
    unittest.main()
