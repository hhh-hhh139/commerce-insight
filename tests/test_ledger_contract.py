import copy
import json
import unittest
from pathlib import Path

from commerce_data.core import ContractError
from commerce_data.ledger import query_spec, validate_snapshot


SOURCE = Path(__file__).resolve().parents[1] / "examples/ledger/snapshot.json"
START, END = "2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z"


class LedgerContractTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(SOURCE.read_text())

    def assert_rejected(self, expected):
        with self.assertRaisesRegex(ContractError, expected):
            validate_snapshot(self.raw)

    def test_fixed_fixture_and_offset_normalization(self):
        original = copy.deepcopy(self.raw)
        self.raw["tables"]["orders"][0]["paid_at"] = "2026-10-06T16:00:00+08:00"
        self.assertEqual(validate_snapshot(self.raw), validate_snapshot(original))

    def test_duplicate_primary_keys_in_each_table(self):
        for name in ("orders", "items", "refunds"):
            with self.subTest(table=name):
                raw = copy.deepcopy(self.raw)
                raw["tables"][name].append(copy.deepcopy(raw["tables"][name][0]))
                with self.assertRaisesRegex(ContractError, "duplicate_primary_key"):
                    validate_snapshot(raw)

    def test_orphan_item(self):
        self.raw["tables"]["items"][0]["order_id"] = "missing"
        self.assert_rejected("orphan_item")

    def test_orphan_refund(self):
        self.raw["tables"]["refunds"][0]["order_id"] = "missing"
        self.assert_rejected("orphan_refund")

    def test_amounts_must_reconcile(self):
        self.raw["tables"]["items"][0]["line_minor"] = 6001
        self.assert_rejected("do_not_reconcile")

    def test_currency_must_match(self):
        self.raw["tables"]["refunds"][0]["currency"] = "USD"
        self.assert_rejected("mixed_currency")

    def test_status_must_be_known(self):
        self.raw["tables"]["refunds"][0]["status"] = "approved"
        self.assert_rejected("unknown_refund_status")

    def test_timestamp_requires_offset(self):
        self.raw["tables"]["orders"][0]["paid_at"] = "2026-10-06T08:00:00"
        self.assert_rejected("timestamp_requires_offset")

    def test_refund_before_payment(self):
        self.raw["tables"]["refunds"][0]["refunded_at"] = "2026-10-01T00:00:00Z"
        self.assert_rejected("refund_before_payment")

    def test_refund_exceeds_order(self):
        self.raw["tables"]["refunds"][2]["amount_minor"] = 8001
        self.assert_rejected("refund_exceeds_payment")

    def test_refund_exceeds_item(self):
        self.raw["tables"]["refunds"][0]["amount_minor"] = 6001
        self.assert_rejected("refund_exceeds_item_amount")

    def test_item_belongs_to_order(self):
        self.raw["tables"]["refunds"][0]["item_id"] = "B1"
        self.assert_rejected("refund_item_order_mismatch")

    def test_unpaid_order_cannot_have_payment(self):
        self.raw["tables"]["orders"][-1]["paid_minor"] = 1
        self.assert_rejected("unpaid_order_has_payment")

    def test_money_cannot_be_float_or_bool(self):
        for value in (10000.0, True, -1):
            with self.subTest(value=value):
                self.raw["tables"]["orders"][0]["paid_minor"] = value
                self.assert_rejected("integer_minor_units")

    def test_missing_refund_table_is_not_zero(self):
        del self.raw["tables"]["refunds"]
        data = validate_snapshot(self.raw)
        query_spec(data, "paid_amount", START, END, workspace_id="demo-shop")
        with self.assertRaisesRegex(ContractError, "refund_data_unavailable"):
            query_spec(data, "event_net", START, END, workspace_id="demo-shop")

    def test_scope_and_parameters_are_validated(self):
        data = validate_snapshot(self.raw)
        for metric, kwargs, reason in [
            ("paid_amount", {"workspace_id": "other"}, "workspace_scope_mismatch"),
            ("SELECT 1", {"workspace_id": "demo-shop"}, "unsupported_metric"),
            ("cohort_net", {"workspace_id": "demo-shop"}, "explicit_as_of"),
            ("paid_amount", {"workspace_id": "demo-shop", "as_of": END}, "as_of_only"),
            ("paid_amount", {"workspace_id": "demo-shop", "product_id": "P1"}, "product_filter_not_supported"),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ContractError, reason):
                query_spec(data, metric, START, END, **kwargs)

    def test_product_refund_needs_complete_attribution(self):
        data = validate_snapshot(self.raw)
        with self.assertRaisesRegex(ContractError, "attribution_incomplete"):
            query_spec(data, "product_refund_amount", END, "2026-10-19T00:00:00Z", workspace_id="demo-shop", product_id="P1")

    def test_success_time_does_not_leak_into_failed_status(self):
        self.raw["tables"]["refunds"][-2]["refunded_at"] = "2026-10-15T00:00:00Z"
        self.assert_rejected("unsuccessful_refund_has_success_time")

    def test_snapshot_cutoff(self):
        self.raw["tables"]["refunds"][0]["refunded_at"] = "2026-10-27T00:00:00Z"
        self.assert_rejected("refund_after_snapshot")


if __name__ == "__main__":
    unittest.main()
