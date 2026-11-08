import copy
import json
from pathlib import Path
import tempfile
import unittest

from commerce_data.core import ContractError
from commerce_data.ledger import load_snapshot
from commerce_data.ledger_db import build_database,ControlledLedger
from commerce_data.reconciliation import analyze,normalize_claim

ROOT=Path(__file__).resolve().parents[1]
DATA,SOURCE=load_snapshot(ROOT/"examples/ledger/snapshot.json")
QUERY={"metric":"paid_amount","start":"2026-10-05T00:00:00Z","end":"2026-10-12T00:00:00Z"}
LEFT={"schema_version":"commerce-report-claim-v1","name":"已支付金额报表","query":QUERY,"reported_value":28000,
      "currency":"CNY","amount_scale":2,"result_unit":"minor_units","metric_version":"ledger-metrics-v1","source_sha256":SOURCE["source_sha256"]}
RIGHT={**LEFT,"name":"订单批次净额报表","query":{**QUERY,"metric":"cohort_net","as_of":"2026-10-19T00:00:00Z"},"reported_value":23000}


class ReconciliationTests(unittest.TestCase):
    def run_query(self,left=None,right=None,data=None,**kwargs):
        data=copy.deepcopy(DATA if data is None else data)
        parent=ROOT/".local/reconciliation/unit";parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            database=build_database(data,Path(directory)/"snapshot.duckdb")
            with ControlledLedger(data,database,SOURCE) as ledger:
                return analyze(ledger,left or LEFT,right or RIGHT,**{"workspace_id":"demo-shop","confirmed":True,**kwargs})

    def test_different_definitions_explained_by_verified_queries(self):
        r=self.run_query();self.assertEqual(r["status"],"explained")
        self.assertEqual((r["observed_delta"],r["recomputed_delta"],r["unexplained_delta"]),(-5000,-5000,0))
        self.assertEqual([c["value"] for c in r["bridge"]["components"]],[0,-5000,0])
        self.assertIn("退款",r["bridge"]["components"][1]["label"])
        self.assertIn("refund_policy",[d["field"] for d in r["differences"]])

    def test_partial_explanation_preserves_report_residual(self):
        r=self.run_query(right={**RIGHT,"reported_value":24000});self.assertEqual(r["status"],"discrepancy")
        self.assertEqual((r["observed_delta"],r["recomputed_delta"],r["unexplained_delta"]),(-4000,-5000,1000))
        self.assertEqual([c["value"] for c in r["bridge"]["components"]],[0,-5000,1000])

    def test_equal_wrong_values_do_not_hide_individual_errors(self):
        r=self.run_query(left={**LEFT,"reported_value":29000},right={**LEFT,"reported_value":29000})
        self.assertEqual(r["status"],"discrepancy");self.assertEqual(r["observed_delta"],0)
        self.assertEqual(r["reports"]["left"]["residual_value"],1000)
        self.assertEqual([c["value"] for c in r["bridge"]["components"]],[-1000,0,1000])

    def test_equivalent_timezones_match(self):
        r=self.run_query(right={**LEFT,"name":"另一名称","query":{**QUERY,"start":"2026-10-05T08:00:00+08:00","end":"2026-10-12T08:00:00+08:00"}})
        self.assertEqual(r["status"],"matched");self.assertEqual(r["differences"],[])

    def test_unknown_or_different_data_versions_do_not_claim_verification(self):
        for source,code in ((None,"source_unknown"),("0"*64,"source_mismatch")):
            r=self.run_query(right={**RIGHT,"source_sha256":source});self.assertEqual(r["status"],"needs_review")
            self.assertIsNone(r["bridge"]);self.assertIsNone(r["reports"]["right"]["residual_value"])
            self.assertEqual(r["reports"]["right"]["recomputed_value"],23000)
            self.assertIn(code,r["reports"]["right"]["issues"])

    def test_cross_currency_scale_or_count_blocks_arithmetic(self):
        for change in ({"currency":"USD"},{"amount_scale":0},{"result_unit":"orders"}):
            r=self.run_query(right={**RIGHT,**change});self.assertIsNone(r["observed_delta"]);self.assertIsNone(r["bridge"])
            self.assertEqual(r["status"],"needs_review")
        count={**LEFT,"query":{**QUERY,"metric":"paid_order_count"},"reported_value":3,"result_unit":"orders"}
        r=self.run_query(right=count);self.assertTrue(r["reports"]["right"]["verified"])
        self.assertFalse(r["comparable_units"]);self.assertIsNone(r["bridge"])

    def test_unknown_metric_version_is_review(self):
        r=self.run_query(right={**RIGHT,"metric_version":"unknown-v2"})
        self.assertEqual(r["reports"]["right"]["issues"],["metric_version_mismatch"])
        self.assertEqual(r["status"],"needs_review")

    def test_missing_refunds_and_capability_do_not_invent_numbers(self):
        data=copy.deepcopy(DATA);data["tables"].pop("refunds")
        r=self.run_query(data=data);self.assertEqual(r["status"],"needs_review")
        self.assertIsNone(r["reports"]["right"]["recomputed_value"])
        self.assertEqual(r["reports"]["right"]["calculation_error"],"refund_data_unavailable")
        r=self.run_query(allowed_metrics=["paid_amount"])
        self.assertEqual(r["reports"]["right"]["calculation_error"],"dataset_metric_unavailable")

    def test_cohort_cutoff_required_and_incomplete_product_attribution(self):
        r=self.run_query(right={**RIGHT,"query":{**QUERY,"metric":"cohort_net"}})
        self.assertEqual(r["reports"]["right"]["calculation_error"],"cohort_requires_explicit_as_of")
        q={"metric":"product_refund_amount","start":"2026-10-12T00:00:00Z","end":"2026-10-19T00:00:00Z","product_id":"P1"}
        r=self.run_query(right={**LEFT,"query":q})
        self.assertEqual(r["reports"]["right"]["calculation_error"],"product_refund_attribution_incomplete")

    def test_confirmation_and_scope_cannot_be_bypassed(self):
        for change in ({"confirmed":False},{"confirmed":1},{"workspace_id":"other"}):
            with self.assertRaises(ContractError):self.run_query(**change)

    def test_untrusted_report_cannot_supply_sql_paths_or_invalid_numbers(self):
        for change in ({"sql":"DROP TABLE ledger_orders"},{"reported_value":True},{"reported_value":1.5},{"reported_value":10**18},
                       {"query":{**QUERY,"metric":{}}},{"query":{**QUERY,"sql":"SELECT 1"}},{"query":{**QUERY,"metric":"refund_change"}},
                       {"source_sha256":"/etc/passwd"},{"query":{**QUERY,"start":"2026-10-05"}},
                       {"query":{**QUERY,"start":"2026-10-05T00:00:00.000000001Z"}}):
            with self.subTest(change=change),self.assertRaises(ContractError):normalize_claim({**LEFT,**change})

    def test_multi_condition_differences_not_invented_as_single_cause(self):
        r=self.run_query(right={**RIGHT,"query":{"metric":"event_net","start":"2026-10-12T00:00:00Z","end":"2026-10-19T00:00:00Z"},"reported_value":10000})
        self.assertEqual(r["status"],"explained")
        self.assertIn("联合重算",r["bridge"]["components"][1]["label"])
        self.assertEqual(r["bridge"]["end_value"],10000)


if __name__=="__main__":unittest.main()
