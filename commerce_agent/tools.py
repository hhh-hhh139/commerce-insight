"""The model receives no workspace selector, SQL executor, path or URL tool."""
import json
from langchain_core.tools import tool
from commerce_data.core import ContractError
from commerce_data.ledger_db import ControlledLedger
from .contracts import METRICS, MetricQuery, SalesChangeQuery, RefundChangeQuery, digest
from commerce_data.sales_change import METRIC as SALES_CHANGE, analyze
from commerce_data.refund_change import METRIC as REFUND_CHANGE, analyze as analyze_refunds
from commerce_data.sales_change import summary as sales_summary
from commerce_data.refund_change import summary as refund_summary
from .storage import AgentError


def compact_evidence(evidence):
    """Model-visible receipt; full immutable calculation stays in ToolMessage.artifact."""
    result = evidence["result"]
    visible = {k: result[k] for k in ("metric", "metric_version", "currency", "amount_scale", "result_unit")}
    if result["metric"] in (SALES_CHANGE, REFUND_CHANGE):
        visible["totals"] = result["totals"]
        visible["summary"] = (sales_summary if result["metric"] == SALES_CHANGE else refund_summary)(result)
    else:
        visible["value"] = result["value"]
    return {"kind": "compact_evidence_v1", "evidence_id": evidence["evidence_id"], "query": evidence["query"], "result": visible}


def make_tools(snapshot, database, source_evidence, journal, allowed_metrics=None, *, context_mode="full", tool_limit=4):
    def output(value):
        return (compact_evidence(value), value) if context_mode == "compact" and "evidence_id" in value else (value, None)

    @tool
    def inspect_dataset() -> dict:
        """Inspect available metrics, capabilities and snapshot metadata; returns no raw rows."""
        position = journal.reserve("tool", tool_limit)
        result = {"snapshot_id": snapshot["snapshot_id"], "currency": snapshot["currency"],
                  "amount_scale": snapshot["amount_scale"], "exported_at": snapshot["exported_at"],
                  "time_basis": "UTC, explicit inclusive start and exclusive end required",
                  "has_refunds": "refunds" in snapshot["tables"], "metrics": {k:v for k,v in METRICS.items() if allowed_metrics is None or k in allowed_metrics},
                  "analyses": {SALES_CHANGE: "已支付金额（不扣退款）的等长两期商品贡献分解；使用 query_sales_change"} if allowed_metrics is None or "paid_amount" in allowed_metrics else {},
                  "limits": ["No costs/profit", "No causal inference", "No external actions", "No arbitrary SQL"]}
        if "refunds" in snapshot["tables"] and (allowed_metrics is None or "refund_amount" in allowed_metrics):
            result["analyses"][REFUND_CHANGE] = "成功退款发生期的等长两期比较；订单支付来源、已关联商品与未归属金额；使用 query_refund_change"
        journal.finish(position, name="inspect_dataset")
        return result

    @tool(args_schema=MetricQuery, response_format="content_and_artifact")
    def query_metric(metric: str, start: str, end: str, as_of: str | None = None, product_id: str | None = None) -> dict:
        """Calculate one named metric with deterministic SQL. Return evidence_id for the final answer; a tool error is not numeric evidence."""
        position = journal.reserve("tool", tool_limit)
        try:
            if allowed_metrics is not None and metric not in allowed_metrics:
                raise ContractError("dataset_metric_unavailable")
            with ControlledLedger(snapshot, database, source_evidence) as ledger:
                result = ledger.query(metric, start, end, workspace_id=snapshot["workspace_id"], as_of=as_of, product_id=product_id)
            query = {"metric": metric, "start": result["period"]["start"], "end": result["period"]["end"]}
            if as_of is not None: query["as_of"] = result["period"]["as_of_exclusive"]
            if product_id is not None: query["product_id"] = product_id
            evidence = {"query": query, "result": result}
            evidence["evidence_id"] = "ev_" + digest(evidence)
            journal.finish(position, name="query_metric", evidence_id=evidence["evidence_id"])
            return output(evidence)
        except ContractError as exc:
            journal.finish(position, name="query_metric", error=str(exc))
            return output({"error": str(exc), "is_evidence": False})

    @tool(args_schema=SalesChangeQuery, response_format="content_and_artifact")
    def query_sales_change(start: str, end: str, current_start: str, current_end: str, top_n: int = 5) -> dict:
        """Explain where paid amount changed by allocated product amounts, excluding refunds. Equal-duration ordered periods only; never infer causal reasons. Returns one complete evidence_id; do not put this evidence in comparisons."""
        position = journal.reserve("tool", tool_limit)
        try:
            if allowed_metrics is not None and "paid_amount" not in allowed_metrics:
                raise ContractError("dataset_metric_unavailable")
            with ControlledLedger(snapshot, database, source_evidence) as ledger:
                result = analyze(ledger, start, end, current_start, current_end, workspace_id=snapshot["workspace_id"], top_n=top_n)
            query = {"metric": SALES_CHANGE, **result["period"], "top_n": top_n}
            evidence = {"query": query, "result": result}
            evidence["evidence_id"] = "ev_" + digest(evidence)
            journal.finish(position, name="query_sales_change", evidence_id=evidence["evidence_id"])
            return output(evidence)
        except ContractError as exc:
            journal.finish(position, name="query_sales_change", error=str(exc))
            return output({"error": str(exc), "is_evidence": False})

    @tool(args_schema=RefundChangeQuery, response_format="content_and_artifact")
    def query_refund_change(start: str, end: str, current_start: str, current_end: str, top_n: int = 5) -> dict:
        """Compare successful refund events in equal-duration ordered periods. Reports original-payment age, known products, unknown attribution and distinct order counts; not refund rate or causes. Returns one complete evidence_id; do not put this evidence in comparisons."""
        position = journal.reserve("tool", tool_limit)
        try:
            if allowed_metrics is not None and "refund_amount" not in allowed_metrics:
                raise ContractError("dataset_metric_unavailable")
            with ControlledLedger(snapshot, database, source_evidence) as ledger:
                result = analyze_refunds(ledger, start, end, current_start, current_end, workspace_id=snapshot["workspace_id"], top_n=top_n)
            query = {"metric": REFUND_CHANGE, **result["period"], "top_n": top_n}
            evidence = {"query": query, "result": result}
            evidence["evidence_id"] = "ev_" + digest(evidence)
            journal.finish(position, name="query_refund_change", evidence_id=evidence["evidence_id"])
            return output(evidence)
        except ContractError as exc:
            journal.finish(position, name="query_refund_change", error=str(exc))
            return output({"error": str(exc), "is_evidence": False})

    return [inspect_dataset, query_metric, query_sales_change, query_refund_change]


def collected_evidence(messages, *, source_sha256=None, workspace_id=None, snapshot_id=None):
    found = {}
    for message in messages:
        if message.type != "tool" or message.name not in ("query_metric", "query_sales_change", "query_refund_change") or not isinstance(message.content, str):
            continue
        if getattr(message, "status", None) == "error": continue
        try: value = json.loads(message.content)
        except (ValueError, TypeError): continue
        if not isinstance(value, dict): continue
        if value.get("kind") == "compact_evidence_v1":
            artifact = getattr(message, "artifact", None)
            try:
                if digest(compact_evidence(artifact)) != digest(value): raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise AgentError("invalid_evidence_artifact") from None
            value = artifact
        if "evidence_id" in value:
            expected = "ev_" + digest({"query": value["query"], "result": value["result"]})
            if value["evidence_id"] != expected: raise AgentError("invalid_evidence_digest")
            result = value["result"]
            if ((source_sha256 is not None and result.get("evidence", {}).get("source_sha256") != source_sha256)
                or (workspace_id is not None and result.get("workspace_id") != workspace_id)
                or (snapshot_id is not None and result.get("snapshot_id") != snapshot_id)):
                raise AgentError("evidence_source_binding_mismatch")
            found[expected] = value
    return found
