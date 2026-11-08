"""Recompute two untrusted report claims against one authorized immutable snapshot."""
import hashlib
import re
from pathlib import Path

from .core import ContractError, digest
from .ledger import utc_time

METRIC = "report_reconciliation"
VERSION = "report-reconciliation-v1"
CLAIM_VERSION = "commerce-report-claim-v1"
# Explicit metric semantics; no SQL or business definition is accepted from a report.
SEMANTICS = {
    "paid_amount": ("已支付订单金额", "paid_at", "paid", "order", "不扣退款"),
    "paid_order_count": ("已支付订单数", "paid_at", "paid", "distinct_order", "不扣退款"),
    "refund_amount": ("成功退款金额", "refunded_at", "succeeded", "refund", "退款发生期"),
    "event_net": ("期间支付减退款", "paid_at + refunded_at", "paid + succeeded", "payment_and_refund", "分别按支付/退款发生期"),
    "cohort_net": ("订单批次净额", "paid_at + as_of", "paid + succeeded", "order_cohort", "支付批次截至 as_of 的退款"),
    "cohort_refunded_order_count": ("订单批次退款订单数", "paid_at + as_of", "paid + succeeded", "distinct_order", "支付批次截至 as_of 的退款"),
    "paid_amount_for_product_orders": ("含指定商品的整单支付额", "paid_at", "paid", "order_containing_product", "不扣退款；不是商品明细金额"),
    "product_refund_amount": ("商品成功退款金额", "refunded_at", "succeeded", "refund_item", "仅限完整商品归属"),
}
ISSUES = {
    "source_unknown": "报告未声明原始数据版本。",
    "source_mismatch": "报告源数据与选定快照不同，不能用当前快照验证其他版本。",
    "metric_version_mismatch": "报告的指标版本不受当前重算器支持。",
    "currency_mismatch": "报告币种与选定快照不同。",
    "amount_scale_mismatch": "报告金额单位精度与当前契约不同。",
    "result_unit_mismatch": "报告的金额/订单数单位与指标定义不同。",
    "calculation_unavailable": "缺少必要数据、能力或有效查询条件；见计算错误代码。",
}
FIELDS = {"schema_version", "name", "query", "reported_value", "currency", "amount_scale", "result_unit", "metric_version", "source_sha256"}


def normalize_claim(raw):
    if not isinstance(raw, dict) or set(raw) != FIELDS or raw.get("schema_version") != CLAIM_VERSION:
        raise ContractError("invalid_report_claim")
    for key in ("name", "currency", "metric_version"):
        if not isinstance(raw[key], str) or not raw[key].strip() or len(raw[key]) > 160:
            raise ContractError("invalid_report_" + key)
    if type(raw["reported_value"]) is not int or abs(raw["reported_value"]) > 10**17:
        raise ContractError("invalid_report_value")
    if type(raw["amount_scale"]) is not int or not 0 <= raw["amount_scale"] <= 6 or raw["result_unit"] not in ("orders", "minor_units"):
        raise ContractError("invalid_report_unit")
    source = raw["source_sha256"]
    if source is not None and (not isinstance(source, str) or len(source) != 64 or any(c not in "0123456789abcdef" for c in source)):
        raise ContractError("invalid_report_source")
    q = raw["query"]
    if not isinstance(q, dict) or not {"metric", "start", "end"} <= set(q) or set(q) - {"metric", "start", "end", "as_of", "product_id"}:
        raise ContractError("invalid_report_query")
    if not isinstance(q["metric"], str) or q["metric"] not in SEMANTICS:
        raise ContractError("unsupported_report_metric")
    query = dict(q)
    for key in ("start", "end", "as_of"):
        if key in query:
            value = query[key]
            if not isinstance(value, str) or len(value) > 40:
                raise ContractError("invalid_report_period")
            fraction = re.search(r"T\d{2}:\d{2}:\d{2}[.,](\d+)", value)
            if fraction and any(c != "0" for c in fraction.group(1)[6:]):
                raise ContractError("invalid_report_time_precision")
            query[key] = utc_time(value)
    if query["start"] >= query["end"]:
        raise ContractError("invalid_report_period")
    if "product_id" in query and (not isinstance(query["product_id"], str) or not query["product_id"] or len(query["product_id"]) > 128):
        raise ContractError("invalid_report_product")
    return {**raw, "query": query}


def verify_claim(ledger, claim, workspace_id, allowed_metrics):
    issues = []
    source = claim["source_sha256"]
    if source is None: issues.append("source_unknown")
    elif source != ledger.evidence.get("source_sha256"): issues.append("source_mismatch")
    if claim["metric_version"] != "ledger-metrics-v1": issues.append("metric_version_mismatch")
    if claim["currency"] != ledger.data["currency"]: issues.append("currency_mismatch")
    if claim["amount_scale"] != ledger.data["amount_scale"]: issues.append("amount_scale_mismatch")
    expected_unit = "orders" if "count" in claim["query"]["metric"] else "minor_units"
    if claim["result_unit"] != expected_unit: issues.append("result_unit_mismatch")
    calculation, error = None, None
    try:
        if allowed_metrics is not None and claim["query"]["metric"] not in allowed_metrics:
            raise ContractError("dataset_metric_unavailable")
        calculation = ledger.query(**claim["query"], workspace_id=workspace_id)
    except ContractError as exc:
        issues.append("calculation_unavailable"); error = str(exc)
    verified = not issues
    return {"claim": claim, "claim_sha256": digest(claim), "verified": verified, "issues": issues,
            "calculation_error": error, "calculation": calculation,
            "recomputed_value": calculation["value"] if calculation is not None else None,
            "residual_value": claim["reported_value"] - calculation["value"] if verified else None}


def differences(left, right):
    rows = []
    for field in ("currency", "amount_scale", "result_unit", "metric_version", "source_sha256"):
        if left[field] != right[field]: rows.append({"field": field, "left": left[field], "right": right[field]})
    for field in ("metric", "start", "end", "as_of", "product_id"):
        a, b = left["query"].get(field), right["query"].get(field)
        if a != b: rows.append({"field": "query." + field, "left": a, "right": b})
    a, b = SEMANTICS[left["query"]["metric"]], SEMANTICS[right["query"]["metric"]]
    for i, field in enumerate(("definition", "event_time", "status_filter", "grain", "refund_policy")):
        if a[i] != b[i]: rows.append({"field": field, "left": a[i], "right": b[i]})
    return rows


def bridge_label(left, right):
    a, b = left["query"], right["query"]
    same_period = all(a.get(k) == b.get(k) for k in ("start", "end", "product_id"))
    if same_period and a["metric"] == "paid_amount" and b["metric"] == "cohort_net":
        return "按右报告截止时间扣除该支付批次的成功退款"
    if same_period and a["metric"] == "paid_amount" and b["metric"] == "event_net":
        return "扣除右报告期间发生的成功退款（可能来自历史订单）"
    if a == b: return "相同查询重算，口径差异为零"
    return "指标、时间和筛选条件的联合重算差异；不作独立因素归因"


def summary(result):
    labels = {"matched": "报告口径与金额一致", "explained": "声明金额与重算一致，差异来自已列明口径", "discrepancy": "报告声明与重算存在偏差，仍需核查", "needs_review": "证据或可比条件不足，暂不能完成核对"}
    lines = [labels[result["status"]] + "。"]
    for side, name in (("left", "左报告"), ("right", "右报告")):
        report = result["reports"][side]
        if report["verified"]:
            lines.append(f"{name}：声明 {report['claim']['reported_value']}，重算 {report['recomputed_value']}，声明减重算 {report['residual_value']}；单位 {report['claim']['result_unit']}，金额精度 {report['claim']['amount_scale']}，币种 {report['claim']['currency']}。")
        else:
            lines.append(name + "：" + "".join(ISSUES[code] for code in report["issues"]))
    if result["bridge"] is not None:
        lines.append("桥接：左声明 + 左侧纠偏 + 口径/筛选重算差异 + 右侧报告偏差 = 右声明。")
        lines.append("非零报告偏差仍是待核查项，不是业务原因。" if result["status"] == "discrepancy" else "两份报告声明值均与当前账本重算一致。")
    lines.append("上传报告仅为待核查声明；只使用选定授权快照重算，不执行报告中的 SQL，不证明到账、利润或因果。")
    return "\n".join(lines)


def analyze(ledger, left, right, *, workspace_id, confirmed, allowed_metrics=None):
    if confirmed is not True: raise ContractError("report_confirmation_required")
    if workspace_id != ledger.data["workspace_id"]: raise ContractError("workspace_scope_mismatch")
    left, right = normalize_claim(left), normalize_claim(right)
    a, b = [verify_claim(ledger, claim, workspace_id, allowed_metrics) for claim in (left, right)]
    diffs = differences(left, right)
    comparable = all(left[k] == right[k] for k in ("currency", "amount_scale", "result_unit"))
    observed = right["reported_value"] - left["reported_value"] if comparable else None
    bridge, recomputed_delta, unexplained = None, None, None
    status = "needs_review"
    if a["verified"] and b["verified"] and comparable:
        recomputed_delta = b["recomputed_value"] - a["recomputed_value"]
        unexplained = b["residual_value"] - a["residual_value"]
        components = [{"code": "left_correction", "label": "消除左报告偏差", "value": -a["residual_value"]},
                      {"code": "query_difference", "label": bridge_label(left, right), "value": recomputed_delta},
                      {"code": "right_residual", "label": "加入右报告偏差（待核查）" if b["residual_value"] else "右报告偏差（为零）", "value": b["residual_value"]}]
        end = left["reported_value"] + sum(row["value"] for row in components)
        if end != right["reported_value"]: raise ContractError("report_bridge_reconciliation_failed")
        bridge = {"start_value": left["reported_value"], "components": components, "end_value": end, "residual_value": 0}
        status = "discrepancy" if a["residual_value"] or b["residual_value"] else "explained" if diffs else "matched"
    result = {"metric": METRIC, "metric_version": VERSION, "workspace_id": workspace_id,
              "snapshot_id": ledger.data["snapshot_id"], "currency": ledger.data["currency"], "amount_scale": ledger.data["amount_scale"],
              "reports": {"left": a, "right": b}, "status": status, "differences": diffs,
              "observed_delta": observed, "recomputed_delta": recomputed_delta, "unexplained_delta": unexplained,
              "comparable_units": comparable, "bridge": bridge,
              "evidence": {**ledger.evidence, "canonical_sha256": digest(ledger.data),
                           "implementation_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                                       for name in ("reconciliation.py", "ledger.py", "ledger_db.py")}}}
    result["summary"] = summary(result)
    return result
