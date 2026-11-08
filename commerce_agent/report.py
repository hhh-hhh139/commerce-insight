"""Publish only facts reconstructed from actual tool evidence, never model prose."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from .contracts import AnalysisDecision, ISSUES, METRICS, question_sha
from .storage import AgentError
from commerce_data.sales_change import METRIC as SALES_CHANGE, summary as sales_summary
from commerce_data.refund_change import METRIC as REFUND_CHANGE, summary as refund_summary


def numeric(result):
    if result["result_unit"] == "orders":
        return str(result["value"]) + " 单"
    return format(Decimal(result["value"]) / Decimal(10) ** result["amount_scale"], ".2f") + " " + result["currency"]


def comparison(a, b):
    x, y = a["result"], b["result"]
    if x["metric"] in (SALES_CHANGE, REFUND_CHANGE) or y["metric"] in (SALES_CHANGE, REFUND_CHANGE):
        raise AgentError("incomparable_evidence")
    for field in ("metric", "currency", "amount_scale", "result_unit", "workspace_id", "snapshot_id"):
        if x[field] != y[field]: raise AgentError("incomparable_evidence")
    if a["query"].get("product_id") != b["query"].get("product_id") or x["period"]["as_of_exclusive"] != y["period"]["as_of_exclusive"]:
        raise AgentError("incomparable_evidence")
    ax, ay, bx, by = [datetime.fromisoformat(v.replace("Z", "+00:00")) for v in
                      (x["period"]["start"], x["period"]["end"], y["period"]["start"], y["period"]["end"])]
    if ay > bx or ay - ax != by - bx: raise AgentError("incomparable_periods")
    delta = y["value"] - x["value"]
    percentage = None if x["value"] == 0 else str((Decimal(delta) * 100 / Decimal(x["value"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if percentage == "-0.00": percentage = "0.00"
    return {"baseline_id": a["evidence_id"], "current_id": b["evidence_id"], "delta": delta,
            "change_percent": percentage, "result_unit": x["result_unit"]}


def build_report(job, decision, evidence, provenance, usage):
    decision = AnalysisDecision.model_validate(decision)
    ids = decision.evidence_ids
    if len(ids) != len(set(ids)) or any(key not in evidence for key in ids):
        raise AgentError("invalid_report_reference")
    answered = decision.outcome == "answered"
    if answered:
        if not ids or decision.issue != "none": raise AgentError("invalid_answer_contract")
    elif ids or decision.comparisons or decision.issue == "none":
        raise AgentError("invalid_clarification_contract")
    if decision.outcome == "needs_clarification" and not decision.issue.startswith("needs_"):
        raise AgentError("invalid_clarification_contract")
    if decision.outcome == "unsupported" and decision.issue not in ("missing_data", "unsupported_request"):
        raise AgentError("invalid_clarification_contract")
    selected = [evidence[key] for key in ids]
    comparisons = []
    for c in decision.comparisons:
        if c.baseline_id not in ids or c.current_id not in ids:
            raise AgentError("invalid_report_reference")
        comparisons.append(comparison(evidence[c.baseline_id], evidence[c.current_id]))
    lines = []
    for item in selected:
        result = item["result"]; p = result["period"]
        if result["metric"] == REFUND_CHANGE:
            lines.append("退款变化分析：基期 [" + p["start"] + ", " + p["end"] + ")，对比期 [" + p["current_start"] + ", " + p["current_end"] + ")。")
            lines.append(refund_summary(result))
            continue
        if result["metric"] == SALES_CHANGE:
            lines.append("商品贡献分析：基期 [" + p["start"] + ", " + p["end"] + ")，对比期 [" + p["current_start"] + ", " + p["current_end"] + ")。")
            lines.append(sales_summary(result))
            continue
        lines.append(f"{METRICS[result['metric']]}：{numeric(result)}；期间 [{p['start']}, {p['end']})。")
        if p["as_of_exclusive"]:
            lines.append("退款截止时间（不含）：" + p["as_of_exclusive"] + "。")
    for c in comparisons:
        base = evidence[c["baseline_id"]]["result"]
        lines.append("后一期减前一期：" + numeric({**base, "value": c["delta"]}) +
                     ("；基期为零，不计算变化率。" if c["change_percent"] is None else "；变化率 " + c["change_percent"] + "%。"))
    if not answered: lines.append(ISSUES[decision.issue])
    lines.append("这是指定快照和口径下的描述性计算，不等于利润、到账或因果解释。")
    return {"kind": "agent_report_v1", "outcome": decision.outcome, "issue": decision.issue,
            "workspace_id": job["workspace_id"], "source_sha256": job["source_sha256"],
            "question_sha256": question_sha(job["request"]["question"]), "evidence": selected,
            "comparisons": comparisons, "answer": "\n".join(lines), "provenance": provenance, "usage": usage}
