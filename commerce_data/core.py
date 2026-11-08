"""Explicit invoice semantics, validation, and deterministic period comparison."""

import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext


CONTRACT_VERSION = "invoice-lines-v1"
METRIC_VERSION = "invoice-record-amount-v1"
REQUIRED = {"invoice_id", "product_id", "quantity", "unit_price", "event_time"}
OPTIONAL = {"country", "customer_id", "description"}
CAPABILITIES = {
    "record_sales": True,
    "cancellation_records": True,
    "cash_collected": False,
    "refund_rate": False,
    "product_refund_attribution": False,
    "profit": False,
    "causal_explanation": False,
}


class ContractError(ValueError):
    """The file or requested interpretation violates the declared contract."""


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text(value):
    return "" if value is None else str(value).strip()


def decimal_value(value):
    if len(text(value)) > 64:
        raise ContractError("number_out_of_contract")
    try:
        result = Decimal(text(value))
    except InvalidOperation:
        raise ContractError("invalid_number") from None
    if not result.is_finite():
        raise ContractError("non_finite_number")
    # Bound precision and magnitude so arithmetic stays exact in the context below.
    if not -6 <= result.as_tuple().exponent <= 9 or len(result.as_tuple().digits) > 16 or abs(result) > Decimal("1000000000"):
        raise ContractError("number_out_of_contract")
    return result


def amount_text(value):
    value = format(value, "f")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return "0" if value in ("", "-0") else value


def local_time(value):
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(text(value))
        except ValueError:
            raise ContractError("invalid_local_datetime") from None
    if result.tzinfo is not None:
        raise ContractError("timezone_not_supported")
    return result


def validate_mapping(mapping, headers):
    expected = {"contract_version", "dataset_name", "fields", "currency",
                "unit_price_multiplier", "time_basis", "event_rule", "duplicate_policy"}
    if not isinstance(mapping, dict) or set(mapping) != expected:
        raise ContractError("mapping_keys_mismatch")
    string_keys = expected - {"fields", "event_rule"}
    if not all(isinstance(mapping[k], str) and mapping[k] for k in string_keys):
        raise ContractError("mapping_metadata_must_be_nonempty_strings")
    if mapping["contract_version"] != CONTRACT_VERSION:
        raise ContractError("unsupported_contract_version")
    if mapping["time_basis"] != "source_local_unspecified_zone":
        raise ContractError("unsupported_time_basis")
    if mapping["duplicate_policy"] not in {"keep_and_flag", "reject"}:
        raise ContractError("unsupported_duplicate_policy")
    currency = mapping["currency"]
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isupper() or not currency.isalpha():
        raise ContractError("currency_must_be_explicit_three_letter_code")
    if mapping["unit_price_multiplier"] not in {"1", "0.01", "0.001"}:
        raise ContractError("unsupported_unit_price_multiplier")
    fields = mapping["fields"]
    if not isinstance(fields, dict) or not REQUIRED <= set(fields) or set(fields) - REQUIRED - OPTIONAL:
        raise ContractError("invalid_field_mapping")
    if not all(isinstance(v, str) and v for v in fields.values()):
        raise ContractError("invalid_source_column")
    if len(set(fields.values())) != len(fields):
        raise ContractError("source_column_reused")
    if not set(fields.values()) <= set(headers):
        raise ContractError("missing_mapped_columns")
    rule = mapping["event_rule"]
    if not isinstance(rule, dict):
        raise ContractError("invalid_event_rule")
    if rule.get("mode") == "invoice_prefix":
        if set(rule) != {"mode", "cancellation_prefix"} or not isinstance(rule["cancellation_prefix"], str) or not rule["cancellation_prefix"]:
            raise ContractError("invalid_prefix_rule")
    elif rule.get("mode") == "status_column":
        if set(rule) != {"mode", "column", "values"} or not isinstance(rule["column"], str) or rule["column"] not in headers:
            raise ContractError("invalid_status_rule")
        values = rule["values"]
        if not isinstance(values, dict) or not values or not all(isinstance(k, str) and k and isinstance(v, str) and v in {"sale", "cancellation"} for k, v in values.items()):
            raise ContractError("invalid_status_values")
    else:
        raise ContractError("unsupported_event_rule")


def normalize(row, mapping):
    fields = mapping["fields"]
    record = {name: row[column] for name, column in fields.items()}
    if any(not text(record[k]) for k in REQUIRED):
        raise ContractError("missing_required_value")
    quantity = decimal_value(record["quantity"])
    price = decimal_value(record["unit_price"])
    if quantity != quantity.to_integral_value():
        raise ContractError("non_integer_quantity")
    if quantity == 0 or price <= 0:
        raise ContractError("non_positive_price_or_zero_quantity")
    when = local_time(record["event_time"])
    rule = mapping["event_rule"]
    if rule["mode"] == "invoice_prefix":
        cancelled = text(record["invoice_id"]).casefold().startswith(rule["cancellation_prefix"].casefold())
        event = "cancellation" if cancelled else "sale"
    else:
        event = rule["values"].get(text(row[rule["column"]]))
        if event is None:
            raise ContractError("unknown_status")
    if (event == "sale" and quantity < 0) or (event == "cancellation" and quantity > 0):
        raise ContractError("status_quantity_conflict")
    return {
        "invoice_id": text(record["invoice_id"]),
        "event_time": when,
        "event": event,
        "country": text(record.get("country")) or None,
        "amount": abs(quantity * price * Decimal(mapping["unit_price_multiplier"])),
    }


def bucket():
    return {"sale": Decimal(0), "cancellation": Decimal(0), "sale_ids": set(), "rows": 0}


def add(target, record):
    target[record["event"]] += record["amount"]
    target["rows"] += 1
    if record["event"] == "sale":
        target["sale_ids"].add(record["invoice_id"])


def describe(target):
    return {
        "positive_record_amount": amount_text(target["sale"]),
        "cancellation_record_amount": amount_text(target["cancellation"]),
        "record_net_amount": amount_text(target["sale"] - target["cancellation"]),
        "distinct_positive_invoice_ids": len(target["sale_ids"]),
        "accepted_lines": target["rows"],
    }


def analyze(rows, headers, mapping, start, split, end, confirmed=False,
            required_capability="record_sales"):
    if not confirmed:
        raise ContractError("mapping_confirmation_required")
    validate_mapping(mapping, headers)
    if not CAPABILITIES.get(required_capability, False):
        raise ContractError("unsupported_capability:" + required_capability)
    bounds = [local_time(value) for value in (start, split, end)]
    if not bounds[0] < bounds[1] < bounds[2]:
        raise ContractError("periods_must_be_ordered_and_nonempty")
    # Up to one million input rows, 1e9 quantity/price and six decimal places.
    # 50 digits leaves ample room for exact sums; ratios are explicitly rounded.
    with localcontext() as context:
        context.prec = 50
        return _analyze(rows, headers, mapping, bounds)


def _analyze(rows, headers, mapping, bounds):
    total, periods = bucket(), [bucket(), bucket()]
    by_country = {}
    missing, invalid = Counter(), Counter()
    seen, duplicates, row_count, invalid_examples = set(), 0, 0, []
    earliest = latest = None
    for row_number, row in rows:
        row_count += 1
        if row_count > 1_000_000:
            raise ContractError("row_limit_exceeded")
        for column in headers:
            if not text(row[column]):
                missing[column] += 1
        # All source cells participate; same invoice/product alone is not a key.
        signature = bytes.fromhex(digest([text(row[c]) for c in headers]))
        if signature in seen:
            duplicates += 1
            if mapping["duplicate_policy"] == "reject":
                raise ContractError("duplicate_source_row")
        seen.add(signature)
        try:
            record = normalize(row, mapping)
        except ContractError as exc:
            reason = str(exc)
            invalid[reason] += 1
            if len(invalid_examples) < 20:
                invalid_examples.append({"source_row": row_number, "reason": reason})
            continue
        when = record["event_time"]
        earliest = min(earliest, when) if earliest else when
        latest = max(latest, when) if latest else when
        add(total, record)
        period = 0 if bounds[0] <= when < bounds[1] else 1 if bounds[1] <= when < bounds[2] else None
        if period is not None:
            add(periods[period], record)
            groups = by_country.setdefault(record["country"], [bucket(), bucket()])
            add(groups[period], record)
    delta = periods[1]["sale"] - periods[0]["sale"]
    country_changes = []
    for country, values in by_country.items():
        country_changes.append({
            "country": country,
            "baseline": describe(values[0]),
            "current": describe(values[1]),
            "positive_amount_change": amount_text(values[1]["sale"] - values[0]["sale"]),
        })
    country_changes.sort(key=lambda item: (-abs(Decimal(item["positive_amount_change"])), item["country"] or ""))
    assert sum((Decimal(item["positive_amount_change"]) for item in country_changes), Decimal(0)) == delta
    assert total["rows"] + sum(invalid.values()) == row_count
    warnings = [
        "invoice_records_are_not_cash_payments_or_accounting_revenue",
        "cancellations_are_not_linked_refund_cashflows",
        "source_timezone_unknown_no_cross_zone_comparison",
        "line_amounts_may_include_non_merchandise_items",
    ]
    if duplicates:
        warnings.append("duplicate_source_rows_kept_by_confirmed_policy")
    if invalid:
        warnings.append("invalid_rows_excluded_results_describe_accepted_subset")
    if not row_count or not total["rows"]:
        warnings.append("no_usable_data")
    if bounds[1] - bounds[0] != bounds[2] - bounds[1]:
        warnings.append("period_durations_differ_totals_not_normalized_per_day")
    if latest is None or bounds[2].date() > latest.date():
        warnings.append("coverage_must_be_confirmed_source_max_time_is_not_a_completeness_guarantee")
    return {
        "contract_version": CONTRACT_VERSION,
        "metric_version": METRIC_VERSION,
        "mapping": mapping,
        "capabilities": CAPABILITIES,
        "quality": {
            "input_rows": row_count, "accepted_rows": total["rows"],
            "quarantined_rows": sum(invalid.values()),
            "invalid_by_reason": dict(sorted(invalid.items())),
            "invalid_examples": invalid_examples,
            "missing_by_column": dict(sorted(missing.items())),
            "duplicate_extra_rows": duplicates,
            "accepted_time_min": earliest.isoformat() if earliest else None,
            "accepted_time_max": latest.isoformat() if latest else None,
        },
        "all_accepted_records": describe(total),
        "comparison": {
            "interval_convention": "[start,end)",
            "baseline_start": bounds[0].isoformat(), "split": bounds[1].isoformat(),
            "current_end": bounds[2].isoformat(),
            "baseline": describe(periods[0]), "current": describe(periods[1]),
            "positive_amount_change": amount_text(delta),
            "positive_amount_change_rate": amount_text((delta / periods[0]["sale"]).quantize(Decimal("0.000001"))) if periods[0]["sale"] else None,
            "rate_unavailable_reason": None if periods[0]["sale"] else "zero_baseline",
            "by_country": country_changes,
        },
        "warnings": warnings,
        "analysis_status": "no_usable_data" if not total["rows"] else "requires_quality_review" if invalid or duplicates else "computed",
    }
