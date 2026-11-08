import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from commerce_data.core import ContractError, analyze
from commerce_data.files import analyze_file, open_rows


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/data/invoice-lines.csv"
MAPPING = ROOT / "examples/mappings/uci-invoice.json"
START, SPLIT, END = "2026-10-05", "2026-10-12", "2026-10-19"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.mapping = json.loads(MAPPING.read_text())
        reader = csv.DictReader(io.StringIO(SOURCE.read_text()))
        self.rows, self.headers = list(reader), reader.fieldnames

    def run_rows(self, rows=None, mapping=None, **kwargs):
        return analyze(enumerate(self.rows if rows is None else rows, 2), self.headers,
                       mapping or self.mapping, START, SPLIT, END, confirmed=True, **kwargs)

    def test_hand_calculated_periods_and_same_price_invoices(self):
        result = self.run_rows()
        comparison = result["comparison"]
        self.assertEqual(comparison["baseline"]["positive_record_amount"], "50")
        self.assertEqual(comparison["baseline"]["distinct_positive_invoice_ids"], 2)
        self.assertEqual(comparison["current"]["positive_record_amount"], "30")
        self.assertEqual(comparison["current"]["cancellation_record_amount"], "10")
        self.assertEqual(comparison["current"]["record_net_amount"], "20")
        self.assertEqual(comparison["positive_amount_change"], "-20")
        self.assertEqual(comparison["positive_amount_change_rate"], "-0.4")

    def test_second_mapping_changes_columns_status_and_units(self):
        first = analyze_file(SOURCE, MAPPING, START, SPLIT, END, confirmed=True)
        second = analyze_file(ROOT / "examples/data/renamed-invoice-lines.csv",
                              ROOT / "examples/mappings/renamed-invoice.json",
                              START, SPLIT, END, confirmed=True)
        self.assertEqual(first["comparison"], second["comparison"])
        self.assertEqual(first["all_accepted_records"], second["all_accepted_records"])

    def test_same_input_same_evidence(self):
        first = analyze_file(SOURCE, MAPPING, START, SPLIT, END, confirmed=True)
        second = analyze_file(SOURCE, MAPPING, START, SPLIT, END, confirmed=True)
        self.assertEqual(first, second)

    def test_missing_customer_is_not_a_sales_filter(self):
        result = self.run_rows()
        self.assertEqual(result["quality"]["missing_by_column"]["CustomerID"], 1)
        self.assertEqual(result["quality"]["accepted_rows"], 5)

    def test_duplicate_keep_is_explicit_and_does_not_deduplicate_amount(self):
        result = self.run_rows(self.rows + [self.rows[0]])
        self.assertEqual(result["quality"]["duplicate_extra_rows"], 1)
        self.assertEqual(result["comparison"]["baseline"]["positive_record_amount"], "70")
        self.assertEqual(result["analysis_status"], "requires_quality_review")

    def test_duplicate_reject_policy(self):
        self.mapping["duplicate_policy"] = "reject"
        with self.assertRaisesRegex(ContractError, "duplicate_source_row"):
            self.run_rows(self.rows + [self.rows[0]])

    def test_confirmation_required(self):
        with self.assertRaisesRegex(ContractError, "mapping_confirmation_required"):
            analyze(enumerate(self.rows, 2), self.headers, self.mapping, START, SPLIT, END)

    def test_absent_required_column_rejects_batch(self):
        self.mapping["fields"]["unit_price"] = "missing"
        with self.assertRaisesRegex(ContractError, "missing_mapped_columns"):
            self.run_rows()

    def test_unknown_status_quarantined(self):
        self.mapping["event_rule"] = {"mode": "status_column", "column": "InvoiceNo", "values": {"A": "sale"}}
        result = self.run_rows([self.rows[0], self.rows[2]])
        self.assertEqual(result["quality"]["invalid_by_reason"], {"unknown_status": 1})
        self.assertEqual(result["quality"]["accepted_rows"], 1)

    def test_no_refund_cash_or_profit_claim(self):
        for capability in ["refund_rate", "cash_collected", "profit", "product_refund_attribution"]:
            with self.subTest(capability=capability), self.assertRaisesRegex(ContractError, "unsupported_capability"):
                self.run_rows(required_capability=capability)

    def test_sub_cent_price_keeps_source_precision(self):
        row = dict(self.rows[0], Quantity="3", UnitPrice="0.001")
        result = self.run_rows([row])
        self.assertEqual(result["all_accepted_records"]["positive_record_amount"], "0.003")

    def test_invalid_numeric_and_status_values_quarantined(self):
        changes = [
            ({"UnitPrice": "NaN"}, "non_finite_number"),
            ({"UnitPrice": "0"}, "non_positive_price_or_zero_quantity"),
            ({"UnitPrice": "-2"}, "non_positive_price_or_zero_quantity"),
            ({"Quantity": "-1"}, "status_quantity_conflict"),
            ({"Quantity": "1.5"}, "non_integer_quantity"),
            ({"UnitPrice": "=1+2"}, "invalid_number"),
            ({"UnitPrice": "0.0000001"}, "number_out_of_contract"),
            ({"StockCode": ""}, "missing_required_value"),
        ]
        for change, reason in changes:
            with self.subTest(change=change):
                result = self.run_rows([dict(self.rows[0], **change)])
                self.assertEqual(result["quality"]["quarantined_rows"], 1)
                self.assertEqual(result["quality"]["invalid_by_reason"], {reason: 1})
                self.assertEqual(result["analysis_status"], "no_usable_data")

    def test_half_open_intervals(self):
        rows = [dict(self.rows[0], InvoiceNo=str(i), InvoiceDate=date, Quantity="1", UnitPrice="10")
                for i, date in enumerate([START, SPLIT, END])]
        result = self.run_rows(rows)
        self.assertEqual(result["comparison"]["baseline"]["positive_record_amount"], "10")
        self.assertEqual(result["comparison"]["current"]["positive_record_amount"], "10")
        self.assertEqual(result["all_accepted_records"]["positive_record_amount"], "30")

    def test_timezone_is_not_guessed(self):
        row = dict(self.rows[0], InvoiceDate="2026-10-06T01:00:00+08:00")
        result = self.run_rows([row])
        self.assertEqual(result["quality"]["invalid_by_reason"], {"timezone_not_supported": 1})

    def test_zero_baseline_has_no_rate(self):
        result = self.run_rows([dict(self.rows[0], InvoiceDate=SPLIT)])
        self.assertIsNone(result["comparison"]["positive_amount_change_rate"])
        self.assertEqual(result["comparison"]["rate_unavailable_reason"], "zero_baseline")

    def test_unknown_country_separate_from_literal_unknown(self):
        rows = [dict(self.rows[0], Country=""), dict(self.rows[0], Country="unknown")]
        result = self.run_rows(rows)
        self.assertEqual({r["country"] for r in result["comparison"]["by_country"]}, {None, "unknown"})

    def test_invalid_period_rejected(self):
        with self.assertRaisesRegex(ContractError, "periods_must_be"):
            analyze(enumerate(self.rows, 2), self.headers, self.mapping, END, SPLIT, START, confirmed=True)

    def test_csv_shape_and_xlsx_parity(self):
        from openpyxl import Workbook
        temp_root = ROOT / ".local/test-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            path = Path(directory)
            (path / "bad.csv").write_text("A,B\n1,2,3\n")
            with self.assertRaisesRegex(ContractError, "column_count_mismatch"):
                with open_rows(path / "bad.csv") as (_, rows, _):
                    list(rows)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Data"
            sheet.append(self.headers)
            for row in self.rows:
                sheet.append([row[h] for h in self.headers])
            workbook.save(path / "sample.xlsx")
            workbook.close()
            result = analyze_file(path / "sample.xlsx", MAPPING, START, SPLIT, END, "Data", True)
            self.assertEqual(result["comparison"], self.run_rows()["comparison"])


if __name__ == "__main__":
    unittest.main()
