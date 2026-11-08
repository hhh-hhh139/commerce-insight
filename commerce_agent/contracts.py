"""Finite tool/report contracts; model-generated amounts are never published."""
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

METRICS = {
    "paid_amount": "期间内已支付订单金额，不扣退款，不等于到账/利润",
    "paid_order_count": "期间内已支付订单数量",
    "refund_amount": "期间发生的成功退款金额；失败/待处理退款排除",
    "event_net": "期间发生的支付金额减期间发生的成功退款，不限原订单支付时间",
    "cohort_net": "期间支付的订单批次金额，减截至 as_of（不含）的该批订单成功退款",
    "cohort_refunded_order_count": "期间支付的订单中，截至 as_of（不含）发生成功退款的订单数量",
    "paid_amount_for_product_orders": "期间支付且包含指定商品的订单总支付金额，不是该商品销售额",
    "product_refund_amount": "期间成功退款中归属于指定商品的金额；商品归属缺失则拒绝",
}
ISSUES = {
    "none": "",
    "needs_time_range": "请补充明确的起止时间及时间基准；相对日期不能自行猜测。",
    "needs_metric_definition": "请确认指标口径，例如支付金额、期间支付减退款，还是订单批次净额。",
    "needs_refund_cutoff": "请明确这批订单的退款统计截至时间（不含该时刻）。",
    "missing_data": "现有数据不足以支持该分析；请补充所需数据或调整问题。",
    "unsupported_request": "本阶段只支持授权快照的有限指标，不支持利润、因果判断、任意 SQL 或外部操作。",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def question_sha(question):
    return hashlib.sha256(question.encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricQuery(StrictModel):
    metric: Literal["paid_amount", "paid_order_count", "refund_amount", "event_net", "cohort_net",
                    "cohort_refunded_order_count", "paid_amount_for_product_orders", "product_refund_amount"]
    start: str = Field(description="Inclusive ISO timestamp with timezone", max_length=40)
    end: str = Field(description="Exclusive ISO timestamp with timezone", max_length=40)
    as_of: str | None = Field(default=None, description="Required only for cohort metrics; exclusive refund cutoff", max_length=40)
    product_id: str | None = Field(default=None, description="Only for the two product metrics", max_length=100)


class Comparison(StrictModel):
    baseline_id: str = Field(max_length=70)
    current_id: str = Field(max_length=70)


class SalesChangeQuery(StrictModel):
    start: str = Field(description="Baseline inclusive timestamp with timezone", max_length=40)
    end: str = Field(description="Baseline exclusive timestamp with timezone", max_length=40)
    current_start: str = Field(description="Current inclusive timestamp; not before baseline end", max_length=40)
    current_end: str = Field(description="Current exclusive timestamp; equal duration to baseline", max_length=40)
    top_n: int = Field(default=5, ge=1, le=10, strict=True, description="Maximum products per direction; remaining contributions are retained")


class RefundChangeQuery(SalesChangeQuery):
    top_n: int = Field(default=5, ge=1, le=10, strict=True, description="Maximum known products; unknown attribution and remaining products are always retained")


class AnalysisDecision(StrictModel):
    """Finish using IDs returned by successful metric or sales-change tools; never invent amounts."""
    outcome: Literal["answered", "needs_clarification", "unsupported"]
    evidence_ids: list[str] = Field(max_length=4, description="Actual evidence_id values from successful tool results")
    comparisons: list[Comparison] = Field(max_length=2, description="Comparable equal-duration non-overlapping periods, baseline then current")
    issue: Literal["none", "needs_time_range", "needs_metric_definition", "needs_refund_cutoff", "missing_data", "unsupported_request"]
