"""CSV structure is observable; business meaning must be explicitly mapped."""
import base64
import csv
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
import re
from pathlib import Path

from commerce_data.core import ContractError, digest
from commerce_data.files import check_headers
from commerce_data.ledger import FIELDS, KEYS, validate_snapshot, utc_time, identifier, minor

MAX_FILE = 131072
MAX_ROWS = 2000
METRICS = ["paid_amount", "paid_order_count", "paid_amount_for_product_orders", "refund_amount",
           "event_net", "cohort_net", "cohort_refunded_order_count", "product_refund_amount"]
AMOUNTS = {"orders": "paid_minor", "items": "line_minor", "refunds": "amount_minor"}
STATUSES = {"orders": {"paid", "pending", "cancelled"}, "refunds": {"succeeded", "pending", "failed"}}


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def timestamp(value, basis):
    if not isinstance(value, str) or len(value)>40:
        raise ContractError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ContractError("invalid_timestamp") from None
    if basis == "explicit_offset":
        return utc_time(value)
    if parsed.tzinfo is not None:
        raise ContractError("timestamp_offset_conflicts_with_mapping")
    offset = 0 if basis == "UTC" else 8
    return parsed.replace(tzinfo=timezone(timedelta(hours=offset))).astimezone(timezone.utc).isoformat(timespec="microseconds")


def amount(value, unit, allow_zero=False):
    if not isinstance(value,str) or not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value.strip()) or len(value)>40:
        raise ContractError("invalid_decimal_amount")
    try:
        with localcontext() as context:
            context.prec=80
            number = Decimal(value.strip()) * (100 if unit=="major" else 1)
        if number != number.to_integral_value():
            raise ContractError("amount_precision_exceeds_minor_unit")
        return minor(int(number), allow_zero=allow_zero)
    except InvalidOperation:
        raise ContractError("invalid_decimal_amount") from None


def evaluate(payload, *, task_id, input_sha256):
    report = {"kind": "import_check_v1", "task_id": task_id, "workspace_id": payload["workspace_id"],
        "draft_id": payload["draft_id"], "revision": payload["revision"], "input_sha256": input_sha256,
        "mapping_sha256": payload["mapping_sha256"], "phase": "profile" if payload["mapping"] is None else "validation",
        "valid": False, "profiles": {}, "files": {}, "error_count": 0, "errors": [], "warnings": [],
        "capabilities": [], "normalized_rows": {}, "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    def error(code, table=None, row=None, field=None):
        report["error_count"] += 1
        if len(report["errors"]) < 50:
            report["errors"].append({"code": code, "table": table, "row": row, "field": field})

    raw_tables = {}
    csv.field_size_limit(4096)
    for table, file in payload["files"].items():
        raw = base64.b64decode(file["content_base64"], validate=True)
        report["files"][table] = {"name": file["name"], "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if len(raw) > MAX_FILE:
            error("file_size_limit", table); continue
        profile = {"headers": [], "row_count": 0, "samples": {}, "distinct_values": {}, "distinct_truncated": []}
        report["profiles"][table] = profile
        try:
            reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
            headers = check_headers(next(reader, []))
            if len(headers)>40 or any(len(h)>128 for h in headers):
                raise ContractError("column_limit_exceeded")
            profile["headers"] = headers
            profile["samples"] = {h: [] for h in headers}
            profile["distinct_values"] = {h: [] for h in headers}
            rows=[]
            for ordinal, values in enumerate(reader, start=2):
                profile["row_count"] += 1
                if profile["row_count"] > MAX_ROWS:
                    raise ContractError("row_limit_exceeded")
                if len(values)!=len(headers):
                    error("column_count_mismatch",table,ordinal); continue
                record=dict(zip(headers,values));rows.append((ordinal,record))
                for h,v in record.items():
                    if len(profile["samples"][h])<3: profile["samples"][h].append(v[:80])
                    distinct=profile["distinct_values"][h]
                    if v not in distinct:
                        if len(distinct)<20 and len(v)<=128: distinct.append(v)
                        elif h not in profile["distinct_truncated"]: profile["distinct_truncated"].append(h)
            raw_tables[table]=rows
            if not rows: report["warnings"].append({"code":"empty_table","table":table})
        except (ContractError, UnicodeError, csv.Error) as exc:
            code = str(exc) if isinstance(exc, ContractError) else "csv_parse_or_encoding_error"
            error(code,table)
    mapping=payload["mapping"]
    if mapping is None or report["error_count"]:
        return report
    try:
        if not isinstance(mapping,dict) or set(mapping)!={"schema_version","currency","time_basis","exported_at","tables"}:
            raise ContractError("invalid_mapping_fields")
        if mapping["schema_version"]!="ledger-csv-map-v1" or mapping["currency"] not in {"CNY","GBP","USD","EUR"}:
            raise ContractError("unsupported_mapping_contract")
        if mapping["time_basis"] not in {"explicit_offset","UTC","UTC+08:00"}:
            raise ContractError("explicit_time_basis_required")
        exported=timestamp(mapping["exported_at"],mapping["time_basis"])
        if not isinstance(mapping["tables"],dict) or set(mapping["tables"])!=set(raw_tables):
            raise ContractError("mapping_table_set_mismatch")
        for table,spec in mapping["tables"].items():
            expected={"columns","amount_unit"} | ({"statuses"} if table in STATUSES else set())
            if not isinstance(spec,dict) or set(spec)!=expected or spec["amount_unit"] not in {"major","minor"}:
                raise ContractError("invalid_table_mapping:"+table)
            cols=spec["columns"]
            if not isinstance(cols,dict) or set(cols)!=FIELDS[table]:
                raise ContractError("mapping_fields_mismatch:"+table)
            chosen=[]
            for target,source in cols.items():
                if source is None and (target=="currency" or (table=="refunds" and target=="item_id")): continue
                if not isinstance(source,str) or source not in report["profiles"][table]["headers"]:
                    raise ContractError("mapped_column_missing:"+table+":"+target)
                chosen.append(source)
            if len(set(chosen))!=len(chosen): raise ContractError("mapped_column_reused:"+table)
            if table in STATUSES:
                states=spec["statuses"]
                if not isinstance(states,dict) or not states or len(states)>30 or any(not isinstance(k,str) or not k or len(k)>128 or v not in STATUSES[table] for k,v in states.items()):
                    raise ContractError("invalid_status_mapping:"+table)
            unused=[h for h in report["profiles"][table]["headers"] if h not in chosen]
            if unused: report["warnings"].append({"code":"unused_columns","table":table,"columns":unused})
    except (ContractError, TypeError) as exc:
        error(str(exc) if isinstance(exc,ContractError) else "invalid_mapping_type");return report

    tables={}
    for table,rows in raw_tables.items():
        spec=mapping["tables"][table];converted=[];seen=set()
        for ordinal,raw in rows:
            field=None
            try:
                value={}
                for field,source in spec["columns"].items():
                    cell=raw[source] if source is not None else None
                    if field=="currency": value[field]=cell if source else mapping["currency"]
                    elif field=="status":
                        if cell not in spec["statuses"]: raise ContractError("unmapped_status")
                        value[field]=spec["statuses"][cell]
                    elif field==AMOUNTS[table]: value[field]=amount(cell,spec["amount_unit"],table=="orders")
                    elif field in {"paid_at","refunded_at"}: value[field]=None if cell=="" else timestamp(cell,mapping["time_basis"])
                    elif table=="refunds" and field=="item_id" and cell in (None,""): value[field]=None
                    else: value[field]=identifier(cell)
                field=KEYS[table]
                if value[field] in seen: raise ContractError("duplicate_primary_key")
                seen.add(value[field]);converted.append(value)
            except ContractError as exc:
                error(str(exc),table,ordinal,field)
        tables[table]=converted
        report["normalized_rows"][table]={"input":report["profiles"][table]["row_count"],"valid_rows":len(converted),
            "invalid_rows":report["profiles"][table]["row_count"]-len(converted)}
    if report["error_count"]: return report
    snapshot={"schema_version":"commerce-ledger-v1","snapshot_id":f"import-{payload['draft_id']}-r{payload['revision']}",
        "workspace_id":payload["workspace_id"],"currency":mapping["currency"],"amount_scale":2,"exported_at":exported,"tables":tables}
    try:
        snapshot=validate_snapshot(snapshot)
    except ContractError as exc:
        error(str(exc));return report
    encoded=compact(snapshot)
    if len(encoded)>1_000_000:
        error("normalized_snapshot_size_limit");return report
    has_refunds="refunds" in tables
    missing_attribution=has_refunds and any(r["status"]=="succeeded" and r["item_id"] is None for r in tables["refunds"])
    if not has_refunds: report["warnings"].append({"code":"refunds_not_supplied"})
    if missing_attribution: report["warnings"].append({"code":"product_refund_attribution_incomplete"})
    for metric in METRICS:
        reason="refunds_not_supplied" if not has_refunds and metric in METRICS[3:] else "product_refund_attribution_incomplete" if missing_attribution and metric=="product_refund_amount" else None
        report["capabilities"].append({"metric":metric,"available":reason is None,"reason":reason})
    report.update(valid=True,snapshot_base64=base64.b64encode(encoded).decode(),snapshot_sha256=hashlib.sha256(encoded).hexdigest(),
        snapshot_id=snapshot["snapshot_id"],currency=snapshot["currency"],exported_at=exported)
    return report
