import copy
from datetime import datetime
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from commerce_data.core import ContractError
from commerce_data.ledger_db import build_database, ControlledLedger
from commerce_data.refund_change import analyze

ROOT=Path(__file__).resolve().parents[1]
SAMPLE=json.loads((ROOT/"examples/ledger/snapshot.json").read_text())
PARAMS={"start":"2026-10-12T00:00:00Z","end":"2026-10-19T00:00:00Z",
        "current_start":"2026-10-19T00:00:00Z","current_end":"2026-10-26T00:00:00Z","workspace_id":"demo-shop"}


class RefundChangeTests(unittest.TestCase):
    def run_query(self,data=None,**kwargs):
        data=copy.deepcopy(SAMPLE if data is None else data)
        parent=ROOT/".local/refund-change/unit";parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            path=build_database(data,Path(directory)/"data.duckdb")
            with ControlledLedger(data,path) as ledger:
                return analyze(ledger,**{**PARAMS,**kwargs})

    def test_known_totals_partial_refunds_and_unknown_product(self):
        r=self.run_query()
        self.assertEqual(r["totals"],{"baseline_minor":5000,"current_minor":1000,"delta_minor":-4000,"change_percent":"-80.00"})
        self.assertEqual(r["counts"],{"baseline_refunds":3,"baseline_orders":2,"current_refunds":1,"current_orders":1})
        self.assertEqual(r["attribution"],{"baseline_linked_minor":3000,"current_linked_minor":1000,"delta_linked_minor":-2000,"delta_unattributed_minor":-2000,
            "baseline_unattributed_minor":2000,"current_unattributed_minor":0,"baseline_coverage_percent":"60.00","current_coverage_percent":"100.00"})
        self.assertEqual([(p["product_id"],p["delta_minor"]) for p in r["products"]["rows"]],[("P1",-2000),("P2",-1000),("P3",1000)])
        self.assertEqual(r["origins"][0]["baseline_minor"],5000)
        self.assertEqual(r["origins"][1]["baseline_minor"],0)

    def test_top_n_keeps_offsetting_other_amounts(self):
        r=self.run_query(top_n=1)
        self.assertEqual(r["products"]["other"],{"product_count":2,"baseline_minor":1000,"current_minor":1000,"delta_minor":0})
        self.assertEqual(r["attribution"]["baseline_unattributed_minor"],2000)

    def test_zero_baseline_and_empty_period(self):
        r=self.run_query(start="2026-10-05T00:00:00Z",end=PARAMS["start"],current_start=PARAMS["start"],current_end=PARAMS["end"])
        self.assertIsNone(r["totals"]["change_percent"])
        self.assertIsNone(r["attribution"]["baseline_coverage_percent"])
        d=copy.deepcopy(SAMPLE);d["tables"]["refunds"]=[]
        r=self.run_query(d);self.assertEqual(r["products"]["rows"],[])
        self.assertEqual(r["counts"]["baseline_refunds"],0)

    def test_payment_age_and_half_open_refund_events(self):
        d=copy.deepcopy(SAMPLE)
        # D paid within baseline; event exactly at current start belongs only to current.
        d["tables"]["refunds"].extend([
            {"refund_id":"D0","order_id":"D","item_id":"D1","status":"succeeded","amount_minor":500,"refunded_at":"2026-10-13T12:00:00Z","currency":"CNY"},
            {"refund_id":"D1","order_id":"D","item_id":"D1","status":"succeeded","amount_minor":800,"refunded_at":PARAMS["current_start"],"currency":"CNY"},
            {"refund_id":"D2","order_id":"D","item_id":"D1","status":"succeeded","amount_minor":900,"refunded_at":PARAMS["current_end"],"currency":"CNY"}])
        r=self.run_query(d,start="2026-10-12T08:00:00+08:00")
        self.assertEqual(r["totals"]["baseline_minor"],5500);self.assertEqual(r["totals"]["current_minor"],1800)
        self.assertEqual(r["origins"][1]["baseline_minor"],500);self.assertEqual(r["origins"][0]["current_minor"],1800)

    def test_missing_refunds_refuses_but_all_unknown_is_reported(self):
        d=copy.deepcopy(SAMPLE);d["tables"].pop("refunds")
        with self.assertRaisesRegex(ContractError,"refund_data_unavailable"):self.run_query(d)
        d=copy.deepcopy(SAMPLE)
        for row in d["tables"]["refunds"]:row["item_id"]=None
        r=self.run_query(d);self.assertEqual(r["products"]["product_count"],0)
        self.assertEqual(r["attribution"]["baseline_coverage_percent"],"0.00")
        self.assertEqual(r["attribution"]["baseline_unattributed_minor"],5000)

    def test_invalid_periods_scope_and_top_n(self):
        for change in ({"end":PARAMS["start"]},{"current_start":"2026-10-18T00:00:00Z"},
                       {"current_end":"2026-10-25T00:00:00Z"},{"current_end":"2026-10-27T00:00:00Z"},
                       {"start":"2026-10-12"},{"workspace_id":"other"},*({"top_n":v} for v in (0,11,True,"5",1.5))):
            with self.subTest(change=change),self.assertRaises(ContractError):self.run_query(**change)

    def test_failed_and_pending_do_not_affect_totals(self):
        a=self.run_query();d=copy.deepcopy(SAMPLE)
        d["tables"]["refunds"]=[r for r in d["tables"]["refunds"] if r["status"]=="succeeded"]
        b=self.run_query(d)
        for key in ("totals","counts","origins","attribution","products"):self.assertEqual(a[key],b[key])

    def test_distinct_orders_not_summed_across_products(self):
        d=copy.deepcopy(SAMPLE);d["tables"]["items"][1]["product_id"]=d["tables"]["items"][0]["product_id"]
        r=self.run_query(d)
        self.assertEqual(r["counts"]["baseline_orders"],2)
        self.assertEqual(r["products"]["rows"][0]["baseline_minor"],3000)

    def test_seeded_independent_python_oracle(self):
        rng=random.Random(20261106)
        for case in range(12):
            d=copy.deepcopy(SAMPLE)
            for row in d["tables"]["refunds"]:
                if row["status"]=="succeeded":
                    row["amount_minor"]=rng.randrange(1,1000)
                    row["refunded_at"]=rng.choice(["2026-10-13T00:00:00Z","2026-10-19T00:00:00Z","2026-10-26T00:00:00Z"])
                    if rng.choice([True,False]):row["item_id"]=None
            r=self.run_query(d,top_n=1)
            for label,lo,hi in (("baseline",PARAMS["start"],PARAMS["end"]),("current",PARAMS["current_start"],PARAMS["current_end"])):
                rows=[v for v in d["tables"]["refunds"] if v["status"]=="succeeded" and lo<=v["refunded_at"]<hi]
                self.assertEqual(r["totals"][label+"_minor"],sum(v["amount_minor"] for v in rows))
                self.assertEqual(r["counts"][label+"_orders"],len({v["order_id"] for v in rows}))
                self.assertEqual(r["attribution"][label+"_unattributed_minor"],sum(v["amount_minor"] for v in rows if v["item_id"] is None))

    def test_mismatched_independent_total_fails_closed(self):
        parent=ROOT/".local/refund-change/unit";parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            path=build_database(SAMPLE,Path(directory)/"db.duckdb")
            with ControlledLedger(SAMPLE,path) as ledger,patch.object(ledger,"query",return_value={"value":0}):
                with self.assertRaisesRegex(ContractError,"refund_change_reconciliation_failed"):analyze(ledger,**PARAMS)


if __name__=="__main__":unittest.main()
