"""Validate normalized order/item/refund snapshots before either query backend.

This is a deliberately narrow contract: one successful capture per paid order,
two-decimal currencies, no fees outside allocated item amounts, no chargebacks.
Platform adapters must establish these meanings before constructing a snapshot.
"""

import copy
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .core import ContractError, digest


SCHEMA = "commerce-ledger-v1"
FIELDS = {
    "orders": {"order_id", "status", "paid_minor", "paid_at", "currency"},
    "items": {"item_id", "order_id", "product_id", "line_minor"},
    "refunds": {"refund_id", "order_id", "item_id", "status", "amount_minor", "refunded_at", "currency"},
}
KEYS = {"orders": "order_id", "items": "item_id", "refunds": "refund_id"}


def identifier(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
        raise ContractError("invalid_identifier")
    return value


def utc_time(value):
    if not isinstance(value, str):
        raise ContractError("timestamp_must_be_string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ContractError("invalid_timestamp") from None
    if result.tzinfo is None:
        raise ContractError("timestamp_requires_offset")
    return result.astimezone(timezone.utc).isoformat(timespec="microseconds")


def minor(value, allow_zero=False):
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= 1_000_000_000_000:
        raise ContractError("amount_must_be_bounded_integer_minor_units")
    return value


def validate_snapshot(raw):
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "snapshot_id", "workspace_id", "currency", "amount_scale", "exported_at", "tables"}:
        raise ContractError("invalid_snapshot_metadata")
    if raw["schema_version"] != SCHEMA or type(raw["amount_scale"]) is not int or raw["amount_scale"] != 2:
        raise ContractError("unsupported_ledger_contract")
    if raw["currency"] not in ("CNY", "GBP", "USD", "EUR"):
        raise ContractError("unsupported_two_decimal_currency")
    identifier(raw["workspace_id"])
    identifier(raw["snapshot_id"])
    data = copy.deepcopy(raw)
    data["exported_at"] = utc_time(data["exported_at"])
    tables = data["tables"]
    if not isinstance(tables, dict) or not {"orders", "items"} <= set(tables) or set(tables) - set(FIELDS):
        raise ContractError("invalid_table_set")
    indexes = {}
    for name, rows in tables.items():
        if not isinstance(rows, list) or len(rows) > 100_000:
            raise ContractError("invalid_table_size:" + name)
        index = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != FIELDS[name]:
                raise ContractError("invalid_row_fields:" + name)
            key = identifier(row[KEYS[name]])
            if key in index:
                raise ContractError("duplicate_primary_key:" + name)
            index[key] = row
        indexes[name] = index
    orders, items = indexes["orders"], indexes["items"]
    for row in orders.values():
        if row["currency"] != data["currency"]:
            raise ContractError("mixed_currency")
        minor(row["paid_minor"], allow_zero=True)
        if row["status"] == "paid":
            minor(row["paid_minor"])
            row["paid_at"] = utc_time(row["paid_at"])
            if row["paid_at"] > data["exported_at"]:
                raise ContractError("payment_after_snapshot")
        elif row["status"] in ("pending", "cancelled"):
            if row["paid_minor"] != 0 or row["paid_at"] is not None:
                raise ContractError("unpaid_order_has_payment")
        else:
            raise ContractError("unknown_order_status")
    line_totals = defaultdict(int)
    for row in items.values():
        identifier(row["product_id"])
        order_id = identifier(row["order_id"])
        if order_id not in orders:
            raise ContractError("orphan_item")
        minor(row["line_minor"])
        line_totals[order_id] += row["line_minor"]
    for key, row in orders.items():
        if row["status"] == "paid" and line_totals[key] != row["paid_minor"]:
            raise ContractError("paid_item_amounts_do_not_reconcile")
    refund_totals, item_refunds = defaultdict(int), defaultdict(int)
    for row in indexes.get("refunds", {}).values():
        order_id = identifier(row["order_id"])
        if order_id not in orders:
            raise ContractError("orphan_refund")
        order = orders[order_id]
        if row["currency"] != data["currency"]:
            raise ContractError("mixed_currency")
        minor(row["amount_minor"])
        if row["item_id"] is not None:
            item_id = identifier(row["item_id"])
            if item_id not in items or items[item_id]["order_id"] != order_id:
                raise ContractError("refund_item_order_mismatch")
        if row["status"] == "succeeded":
            if order["status"] != "paid":
                raise ContractError("refund_without_payment")
            row["refunded_at"] = utc_time(row["refunded_at"])
            if row["refunded_at"] < order["paid_at"]:
                raise ContractError("refund_before_payment")
            if row["refunded_at"] > data["exported_at"]:
                raise ContractError("refund_after_snapshot")
            refund_totals[order_id] += row["amount_minor"]
            if row["item_id"] is not None:
                item_refunds[row["item_id"]] += row["amount_minor"]
        elif row["status"] in ("pending", "failed"):
            if row["refunded_at"] is not None:
                raise ContractError("unsuccessful_refund_has_success_time")
        else:
            raise ContractError("unknown_refund_status")
    if any(value > orders[key]["paid_minor"] for key, value in refund_totals.items()):
        raise ContractError("refund_exceeds_payment")
    if any(value > items[key]["line_minor"] for key, value in item_refunds.items()):
        raise ContractError("refund_exceeds_item_amount")
    for name in tables:
        tables[name].sort(key=lambda row: row[KEYS[name]])
    return data


def load_snapshot(path):
    path = Path(path)
    if path.stat().st_size > 50_000_000:
        raise ContractError("snapshot_file_too_large")
    content = path.read_bytes()
    data = validate_snapshot(json.loads(content))
    return data, {"source_sha256": hashlib.sha256(content).hexdigest(), "canonical_sha256": digest(data)}


def query_spec(data, metric, start, end, *, workspace_id, as_of=None, product_id=None):
    """Return a parameterized query, after enforcing metric capabilities.

    Workspace equality is a scope check, not authentication. Java must supply an
    already-authorized snapshot and workspace in the eventual application.
    """
    if workspace_id != data["workspace_id"]:
        raise ContractError("workspace_scope_mismatch")
    start, end = utc_time(start), utc_time(end)
    if not start < end <= data["exported_at"]:
        raise ContractError("invalid_period_or_after_snapshot")
    if metric not in {"paid_amount", "paid_order_count", "refund_amount", "event_net", "cohort_net", "cohort_refunded_order_count", "paid_amount_for_product_orders", "product_refund_amount"}:
        raise ContractError("unsupported_metric")
    uses_refunds = metric in {"refund_amount", "event_net", "cohort_net", "cohort_refunded_order_count", "product_refund_amount"}
    if uses_refunds and "refunds" not in data["tables"]:
        raise ContractError("refund_data_unavailable")
    cohort = metric.startswith("cohort_")
    if cohort:
        if as_of is None:
            raise ContractError("cohort_requires_explicit_as_of")
        as_of = utc_time(as_of)
        if not end <= as_of <= data["exported_at"]:
            raise ContractError("invalid_as_of")
    elif as_of is not None:
        raise ContractError("as_of_only_for_cohort_metric")
    if metric in {"paid_amount_for_product_orders", "product_refund_amount"}:
        identifier(product_id)
    elif product_id is not None:
        raise ContractError("product_filter_not_supported_for_metric")
    payment_filter = "o.status = 'paid' AND o.paid_at >= ? AND o.paid_at < ?"
    refund_filter = "r.status = 'succeeded' AND r.refunded_at >= ? AND r.refunded_at < ?"
    paid = "SELECT COALESCE(SUM(o.paid_minor), 0) FROM ledger_orders o WHERE " + payment_filter
    refund = "SELECT COALESCE(SUM(r.amount_minor), 0) FROM ledger_refunds r WHERE " + refund_filter
    params = [start, end]
    if metric == "paid_amount":
        sql = paid
    elif metric == "paid_order_count":
        sql = "SELECT COUNT(*) FROM ledger_orders o WHERE " + payment_filter
    elif metric == "refund_amount":
        sql = refund
    elif metric == "event_net":
        sql = "SELECT (" + paid + ") - (" + refund + ")"
        params *= 2
    elif metric == "paid_amount_for_product_orders":
        sql = paid + " AND EXISTS (SELECT 1 FROM ledger_items i WHERE i.order_id = o.order_id AND i.product_id = ?)"
        params.append(product_id)
    elif metric == "product_refund_amount":
        if any(r["status"] == "succeeded" and start <= r["refunded_at"] < end and r["item_id"] is None for r in data["tables"]["refunds"]):
            raise ContractError("product_refund_attribution_incomplete")
        sql = "SELECT COALESCE(SUM(r.amount_minor), 0) FROM ledger_refunds r JOIN ledger_items i ON r.item_id = i.item_id WHERE " + refund_filter + " AND i.product_id = ?"
        params.append(product_id)
    else:
        if metric == "cohort_net":
            sql = "WITH refunds_by_order AS (SELECT order_id, SUM(amount_minor) AS refunded FROM ledger_refunds WHERE status = 'succeeded' AND refunded_at < ? GROUP BY order_id) SELECT COALESCE(SUM(o.paid_minor - COALESCE(r.refunded, 0)), 0) FROM ledger_orders o LEFT JOIN refunds_by_order r ON o.order_id = r.order_id WHERE " + payment_filter
            params = [as_of, start, end]
        else:
            sql = "SELECT COUNT(*) FROM ledger_orders o WHERE " + payment_filter + " AND EXISTS (SELECT 1 FROM ledger_refunds r WHERE r.order_id = o.order_id AND r.status = 'succeeded' AND r.refunded_at < ?)"
            params.append(as_of)
    return {"metric": metric, "metric_version": "ledger-metrics-v1", "sql": sql,
            "params": params, "currency": data["currency"], "amount_scale": data["amount_scale"],
            "result_unit": "orders" if "count" in metric else "minor_units",
            "period": {"start": start, "end": end, "as_of_exclusive": as_of},
            "workspace_id": data["workspace_id"], "snapshot_id": data["snapshot_id"]}
