import base64
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import unittest
from commerce_import.adapter import evaluate, amount, compact
from commerce_data.core import ContractError, digest

ROOT=Path(__file__).resolve().parents[1]


def fixture(layout="standard-minor", refunds=True):
    directory=ROOT/"examples/imports"/layout
    mapping=json.loads((directory/"mapping.json").read_text())
    tables=["orders","items"]+(["refunds"] if refunds else [])
    files={t:{"name":t+".csv","content_base64":base64.b64encode((directory/(t+".csv")).read_bytes()).decode()} for t in tables}
    if not refunds:mapping["tables"].pop("refunds")
    return {"workspace_id":"demo-shop","draft_id":"test-import","revision":1,"files":files,
        "mapping":mapping,"mapping_sha256":digest(mapping)}


def run(payload):
    payload["mapping_sha256"]=digest(payload["mapping"])
    return evaluate(payload,task_id="test-task",input_sha256=hashlib.sha256(compact(payload)).hexdigest())


def change(payload, table, mutate):
    file=payload["files"][table]
    reader=csv.DictReader(io.StringIO(base64.b64decode(file["content_base64"]).decode()))
    headers=reader.fieldnames;rows=list(reader);mutate(rows)
    stream=io.StringIO(newline="");writer=csv.DictWriter(stream,fieldnames=headers);writer.writeheader();writer.writerows(rows)
    file["content_base64"]=base64.b64encode(stream.getvalue().encode()).decode()


class ImportAdapterTests(unittest.TestCase):
    def test_two_layouts_same_tables_and_exact_amounts(self):
        a=run(fixture());b=run(fixture("renamed-major"));self.assertTrue(a["valid"],a);self.assertTrue(b["valid"],b)
        x=json.loads(base64.b64decode(a["snapshot_base64"]));y=json.loads(base64.b64decode(b["snapshot_base64"]))
        self.assertEqual(x,y)
        self.assertEqual(sum(o["paid_minor"] for o in x["tables"]["orders"] if o["order_id"] in "ABC"),28000)

    def test_profile_never_ready_for_confirmation(self):
        p=fixture();p["mapping"]=None;r=run(p)
        self.assertEqual(r["phase"],"profile");self.assertFalse(r["valid"]);self.assertNotIn("snapshot_base64",r)
        self.assertEqual(r["profiles"]["orders"]["row_count"],5)

    def test_no_refunds_reduces_capabilities(self):
        r=run(fixture(refunds=False));self.assertTrue(r["valid"])
        self.assertEqual([m["metric"] for m in r["capabilities"] if m["available"]],["paid_amount","paid_order_count","paid_amount_for_product_orders"])

    def test_missing_item_attribution_warning(self):
        r=run(fixture());self.assertTrue(r["valid"])
        self.assertFalse(next(m for m in r["capabilities"] if m["metric"]=="product_refund_amount")["available"])

    def test_decimal_precision_never_rounded(self):
        for value in ("1.001","1.000000000000000000000000000001","NaN","1e2","=1+1"):
            with self.subTest(value=value),self.assertRaises(ContractError):amount(value,"major")
        self.assertEqual(amount("1.01","major"),101)

    def test_row_failures_block_whole_batch_and_preserve_counts(self):
        p=fixture();change(p,"orders",lambda rows:rows[0].update(paid_minor="1.5"));r=run(p)
        self.assertFalse(r["valid"]);self.assertEqual(r["normalized_rows"]["orders"],{"input":5,"valid_rows":4,"invalid_rows":1})
        self.assertEqual(r["errors"][0]["row"],2);self.assertNotIn("snapshot_base64",r)

    def test_duplicate_unknown_status_and_time(self):
        for mutate,expected in ((lambda rs:rs.append(copy.deepcopy(rs[0])),"duplicate_primary_key"),
                (lambda rs:rs[0].update(status="done"),"unmapped_status"),
                (lambda rs:rs[0].update(paid_at="2026-10-06T08:00:00"),"timestamp_requires_offset")):
            p=fixture();change(p,"orders",mutate);r=run(p)
            self.assertFalse(r["valid"]);self.assertEqual(r["errors"][0]["code"],expected)

    def test_relations_currency_and_reconciliation(self):
        for table,mutate,expected in (("items",lambda rs:rs[0].update(order_id="orphan"),"orphan_item"),
                ("orders",lambda rs:rs[0].update(currency="USD"),"mixed_currency"),
                ("orders",lambda rs:rs[0].update(paid_minor="11000"),"paid_item_amounts_do_not_reconcile")):
            p=fixture();change(p,table,mutate);r=run(p)
            self.assertFalse(r["valid"]);self.assertEqual(r["errors"][0]["code"],expected)

    def test_wrong_units_and_missing_columns(self):
        p=fixture();p["mapping"]["tables"]["orders"]["amount_unit"]="major";self.assertFalse(run(p)["valid"])
        p=fixture();p["mapping"]["tables"]["orders"]["columns"]["paid_minor"]="missing";r=run(p)
        self.assertFalse(r["valid"]);self.assertIn("mapped_column_missing",r["errors"][0]["code"])

    def test_id_leading_zero_preserved(self):
        p=fixture();change(p,"items",lambda rs:rs[4].update(product_id="00123"));r=run(p)
        self.assertTrue(r["valid"]);self.assertEqual(json.loads(base64.b64decode(r["snapshot_base64"]))["tables"]["items"][-1]["product_id"],"00123")

    def test_csv_structure_and_limits(self):
        for content in (b"a,a\n1,2\n",b"a,b\n1,2,3\n",b"a,b\n\"unterminated",b"\xff",b"a\n"+b"x"*4097,b"a\n"+b"x\n"*2001):
            p=fixture();p["files"]["orders"]["content_base64"]=base64.b64encode(content).decode();r=run(p)
            self.assertFalse(r["valid"]);self.assertGreater(r["error_count"],0)

    def test_mapping_cannot_execute_expressions(self):
        p=fixture();p["mapping"]["tables"]["orders"]["expression"]="DROP TABLE orders";r=run(p)
        self.assertFalse(r["valid"]);self.assertIn("invalid_table_mapping",r["errors"][0]["code"])


if __name__=="__main__":unittest.main()
