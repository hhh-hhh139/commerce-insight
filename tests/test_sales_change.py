import copy
from datetime import datetime
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from commerce_data.core import ContractError
from commerce_data.ledger import validate_snapshot
from commerce_data.ledger_db import build_database, ControlledLedger
from commerce_data.sales_change import analyze

ROOT=Path(__file__).resolve().parents[1]
SAMPLE=json.loads((ROOT/"examples/ledger/snapshot.json").read_text())
PARAMS={"start":"2026-10-05T00:00:00Z","end":"2026-10-12T00:00:00Z",
        "current_start":"2026-10-12T00:00:00Z","current_end":"2026-10-19T00:00:00Z","workspace_id":"demo-shop"}


def dataset(entries):
    data=copy.deepcopy(SAMPLE);data["tables"]={"orders":[],"items":[]}
    for i,(time,lines) in enumerate(entries):
        order=str(i)
        data["tables"]["orders"].append({"order_id":order,"status":"paid","paid_minor":sum(v for _,v in lines),"paid_at":time,"currency":"CNY"})
        data["tables"]["items"].extend({"item_id":f"{i}-{j}","order_id":order,"product_id":product,"line_minor":value} for j,(product,value) in enumerate(lines))
    return data


class SalesChangeTests(unittest.TestCase):
    def run_query(self,data=None,**kwargs):
        data=copy.deepcopy(SAMPLE if data is None else data)
        parent=ROOT/".local/sales-change/unit";parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            path=build_database(data,Path(directory)/"data.duckdb")
            with ControlledLedger(data,path) as ledger:
                return analyze(ledger,**{**PARAMS,**kwargs})

    def test_fixed_answer_and_independent_order_reconciliation(self):
        r=self.run_query()
        self.assertEqual(r["totals"],{"baseline_minor":28000,"current_minor":15000,"delta_minor":-13000,"change_percent":"-46.43"})
        self.assertEqual([(p["product_id"],p["delta_minor"]) for p in r["decreases"]["products"]],[("P3",-10000),("P2",-4000)])
        self.assertEqual(r["increases"]["products"][0]["delta_minor"],1000)
        self.assertEqual(r["decreases"]["products"][0]["direction_share_percent"],"71.43")
        self.assertEqual(r["reconciliation"]["delta_residual_minor"],0)
        self.assertEqual(len(r["queries"]),3)

    def test_top_n_retains_other_contributions(self):
        r=self.run_query(top_n=1)
        self.assertEqual(r["decreases"]["other"],{"product_count":1,"delta_minor":-4000})
        self.assertEqual(r["decreases"]["delta_minor"]+r["increases"]["delta_minor"],r["totals"]["delta_minor"])

    def test_zero_baseline_and_empty_periods(self):
        r=self.run_query(dataset([(PARAMS["current_start"],[("NEW",100)])]))
        self.assertIsNone(r["totals"]["change_percent"])
        p=r["increases"]["products"][0];self.assertIsNone(p["change_percent"]);self.assertEqual(p["period_presence"],"current_only")
        empty=self.run_query(dataset([]));self.assertEqual(empty["product_count"],0)
        self.assertIsNone(empty["totals"]["change_percent"]);self.assertEqual(empty["increases"]["products"],[])

    def test_offsetting_changes_with_zero_net(self):
        d=dataset([(PARAMS["start"],[("A",100),("B",100)]),(PARAMS["current_start"],[("A",150),("B",50)])])
        r=self.run_query(d);self.assertEqual(r["totals"]["delta_minor"],0)
        self.assertEqual(r["increases"]["delta_minor"],50);self.assertEqual(r["decreases"]["delta_minor"],-50)
        self.assertEqual(r["increases"]["products"][0]["direction_share_percent"],"100.00")

    def test_repeated_product_lines_sum_without_order_duplication(self):
        d=dataset([(PARAMS["start"],[("A",60),("A",40),("B",20)]),(PARAMS["current_start"],[("A",50)])])
        r=self.run_query(d);self.assertEqual(r["totals"]["baseline_minor"],120)
        self.assertEqual(r["decreases"]["products"][0]["baseline_minor"],100)

    def test_refunds_do_not_multiply_or_subtract_paid_sales(self):
        a=self.run_query();d=copy.deepcopy(SAMPLE);d["tables"].pop("refunds");b=self.run_query(d)
        for k in ("totals","increases","decreases","reconciliation"):self.assertEqual(a[k],b[k])

    def test_half_open_boundaries_offsets_and_cancelled_order(self):
        d=dataset([(PARAMS["start"],[("A",100)]),(PARAMS["current_start"],[("A",200)]),(PARAMS["current_end"],[("A",900)])])
        d["tables"]["orders"].append({"order_id":"U","status":"cancelled","paid_minor":0,"paid_at":None,"currency":"CNY"})
        d["tables"]["items"].append({"item_id":"U1","order_id":"U","product_id":"IGNORE","line_minor":999})
        r=self.run_query(d,start="2026-10-05T08:00:00+08:00")
        self.assertEqual(r["totals"]["baseline_minor"],100);self.assertEqual(r["totals"]["current_minor"],200)
        self.assertEqual(r["product_count"],1)

    def test_invalid_periods_scope_and_parameters(self):
        cases=[{"end":PARAMS["start"]},{"current_start":"2026-10-11T00:00:00Z"},
               {"current_end":"2026-10-20T00:00:00Z"},{"current_start":"2026-10-26T00:00:00Z","current_end":"2026-11-02T00:00:00Z"},
               {"start":"2026-10-05"},{"workspace_id":"other"},*({"top_n":n} for n in [0,11,True,"5",1.5])]
        for change in cases:
            with self.subTest(change=change),self.assertRaises(ContractError):self.run_query(**change)

    def test_stable_ties_unchanged_and_period_presence(self):
        d=dataset([(PARAMS["start"],[("B",100),("A",100),("SAME",20)]),(PARAMS["current_start"],[("SAME",20)])])
        r=self.run_query(d);self.assertEqual([p["product_id"] for p in r["decreases"]["products"]],["A","B"])
        self.assertEqual(r["unchanged_count"],1);self.assertEqual(r["decreases"]["products"][0]["period_presence"],"baseline_only")

    def test_large_amounts_remain_exact_and_ids_are_data(self):
        product="001'; DROP TABLE ledger_orders;--"
        d=dataset([(PARAMS["start"],[(product,10**12)]),(PARAMS["current_start"],[(product,10**12-1)])])
        r=self.run_query(d);self.assertEqual(r["totals"]["delta_minor"],-1)
        self.assertEqual(r["decreases"]["products"][0]["product_id"],product)
        self.assertNotIn(product,r["queries"][0]["sql"])

    def test_independent_python_oracle_on_seeded_datasets(self):
        rng=random.Random(20261106)
        for case in range(15):
            entries=[(rng.choice([PARAMS["start"],PARAMS["current_start"]]),[(f"P{rng.randrange(12)}",rng.randrange(1,10000)) for _ in range(rng.randrange(1,5))]) for _ in range(20)]
            d=dataset(entries);r=self.run_query(d,top_n=10);oracle={}
            orders={o["order_id"]:o for o in d["tables"]["orders"]}
            for item in d["tables"]["items"]:
                values=oracle.setdefault(item["product_id"],[0,0]);values[int(orders[item["order_id"]]["paid_at"]==PARAMS["current_start"])]+=item["line_minor"]
            self.assertEqual(r["totals"]["baseline_minor"],sum(v[0] for v in oracle.values()))
            self.assertEqual(r["totals"]["current_minor"],sum(v[1] for v in oracle.values()))
            for direction,sign in [("decreases",-1),("increases",1)]:
                expected=sorted(((p,v[0],v[1],v[1]-v[0]) for p,v in oracle.items() if (v[1]-v[0])*sign>0),key=lambda x:(-abs(x[3]),x[0]))
                actual=[(v["product_id"],v["baseline_minor"],v["current_minor"],v["delta_minor"]) for v in r[direction]["products"]]
                self.assertEqual(actual,expected[:10]);self.assertEqual(r[direction]["other"]["delta_minor"],sum(v[3] for v in expected[10:]))

    def test_mismatched_independent_total_fails_closed(self):
        parent=ROOT/".local/sales-change/unit";parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            path=build_database(SAMPLE,Path(directory)/"db.duckdb")
            with ControlledLedger(SAMPLE,path) as ledger,patch.object(ledger,"query",return_value={"value":0}):
                with self.assertRaisesRegex(ContractError,"sales_change_reconciliation_failed"):analyze(ledger,**PARAMS)


if __name__=="__main__":unittest.main()
