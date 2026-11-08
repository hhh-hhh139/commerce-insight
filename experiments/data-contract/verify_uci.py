"""Independent UCI-specific aggregate check, not a reusable ingestion path.

Uses signed subtotals by month and integer millionths, not commerce_data's
normalizer or country buckets. Shares the XLSX reader, so it cannot independently
establish that the reader decoded every original cell correctly.
"""

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import openpyxl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workbook = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=False, keep_links=False)
    sums, counts, missing = Counter(), Counter(), Counter()
    total = rejected = 0
    try:
        rows = workbook["Online Retail"].iter_rows(values_only=True)
        headers = next(rows)
        assert headers == ("InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate", "UnitPrice", "CustomerID", "Country")
        for row in rows:
            total += 1
            for name, value in zip(headers, row):
                if value is None:
                    missing[name] += 1
            invoice, product, _, quantity, date, price, _, _ = row
            # This fixture is already typed by openpyxl; these preconditions
            # keep a source change from silently invalidating this reference.
            assert isinstance(quantity, int)
            price = Decimal(str(price))
            assert price.is_finite() and price.as_tuple().exponent >= -6
            cancellation = str(invoice).lower().startswith("c")
            accepted = invoice is not None and product is not None and date is not None and price > 0 and (
                cancellation and quantity < 0 or not cancellation and quantity > 0)
            if not accepted:
                rejected += 1
                continue
            signed_units = price * quantity * 1_000_000
            assert signed_units == signed_units.to_integral_value()
            kind = "cancellation" if cancellation else "sale"
            sums[(date.strftime("%Y-%m"), kind)] += int(signed_units)
            counts[kind] += 1
    finally:
        workbook.close()
    actual = json.loads(args.report.read_text())
    expected = {
        "accepted_rows": total - rejected,
        "quarantined_rows": rejected,
        "all_positive_amount": str(Decimal(sum(v for (_, kind), v in sums.items() if kind == "sale")) / 1_000_000),
        "all_cancellation_amount": str(-Decimal(sum(v for (_, kind), v in sums.items() if kind == "cancellation")) / 1_000_000),
    }
    checks = {
        "input_count": total == actual["quality"]["input_rows"],
        "accepted_count": total - rejected == actual["quality"]["accepted_rows"],
        "quarantined_count": rejected == actual["quality"]["quarantined_rows"],
        "missing_counts": dict(missing) == actual["quality"]["missing_by_column"],
        "positive_total": Decimal(expected["all_positive_amount"]) == Decimal(actual["all_accepted_records"]["positive_record_amount"]),
        "cancellation_total": Decimal(expected["all_cancellation_amount"]) == Decimal(actual["all_accepted_records"]["cancellation_record_amount"]),
    }
    for month, key in [("2011-10", "baseline"), ("2011-11", "current")]:
        for kind, field in [("sale", "positive_record_amount"), ("cancellation", "cancellation_record_amount")]:
            checks[month + "_" + kind] = abs(Decimal(sums[(month, kind)]) / 1_000_000) == Decimal(actual["comparison"][key][field])
    checks["fixed_comparison_dates"] = (
        actual["comparison"]["baseline_start"] == "2011-10-01T00:00:00"
        and actual["comparison"]["split"] == "2011-11-01T00:00:00"
        and actual["comparison"]["current_end"] == "2011-12-01T00:00:00")
    output = {
        "reference_method": "UCI-specific monthly signed integer millionths; shared openpyxl reader",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(args.xlsx.read_bytes()).hexdigest(),
        "compared_report_sha256": hashlib.sha256(args.report.read_bytes()).hexdigest(),
        "input_rows": total, "expected": expected, "checks": checks,
    }
    checks["same_source"] = output["source_sha256"] == actual["evidence"]["source_sha256"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
