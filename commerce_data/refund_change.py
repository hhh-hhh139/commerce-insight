"""Refund events, original payment age and partial product attribution; never causal."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

from .core import ContractError, digest
from .ledger import query_spec

METRIC = "refund_change"
VERSION = "refund-change-v1"
PERIODS_SQL = "WITH periods(label, start_at, end_at) AS (VALUES ('baseline', CAST(? AS TIMESTAMPTZ), CAST(? AS TIMESTAMPTZ)), ('current', CAST(? AS TIMESTAMPTZ), CAST(? AS TIMESTAMPTZ))) "
GROUP_SQL = PERIODS_SQL + """SELECT p.label,
 CASE WHEN o.paid_at < p.start_at THEN 'prior_orders' ELSE 'same_period_orders' END AS origin,
 i.product_id, SUM(r.amount_minor)
FROM periods p JOIN ledger_refunds r ON r.status = 'succeeded' AND r.refunded_at >= p.start_at AND r.refunded_at < p.end_at
JOIN ledger_orders o ON r.order_id = o.order_id
LEFT JOIN ledger_items i ON r.item_id = i.item_id
GROUP BY p.label, origin, i.product_id ORDER BY p.label, origin, i.product_id"""
COUNT_SQL = PERIODS_SQL + """SELECT p.label, COUNT(r.refund_id), COUNT(DISTINCT r.order_id)
FROM periods p LEFT JOIN ledger_refunds r ON r.status = 'succeeded' AND r.refunded_at >= p.start_at AND r.refunded_at < p.end_at
GROUP BY p.label ORDER BY p.label"""


def percent(value, base):
    if base == 0:
        return None
    rounded = (Decimal(value) * 100 / Decimal(base)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # BigDecimal has no negative zero; use one cross-language canonical spelling.
    return str(abs(rounded) if rounded == 0 else rounded)


def amounts(a=0, b=0):
    return {"baseline_minor": a, "current_minor": b, "delta_minor": b-a}


def summary(result):
    def money(value):
        return format(Decimal(value) / 100, ".2f") + " " + result["currency"]
    t, c, a = result["totals"], result["counts"], result["attribution"]
    lines = ["按退款发生时间统计成功退款：" + money(t["baseline_minor"]) + " → " + money(t["current_minor"]) + "，变化 " + money(t["delta_minor"]) + "。",
             "基期为零，不计算金额变化率。" if t["change_percent"] is None else "金额变化率 " + t["change_percent"] + "% 。",
             f"退款记录 {c['baseline_refunds']} → {c['current_refunds']} 笔；去重订单 {c['baseline_orders']} → {c['current_orders']} 单。"]
    for row in result["origins"]:
        label = "各期开始前支付的订单" if row["origin"] == "prior_orders" else "各期内支付的订单"
        lines.append(label + "的退款：" + money(row["baseline_minor"]) + " → " + money(row["current_minor"]) + "。")
    lines.append("未关联商品的退款：" + money(a["baseline_unattributed_minor"]) + " → " + money(a["current_unattributed_minor"]) + "；不进行推测分摊。")
    lines.append("商品归属金额覆盖率：" + (a["baseline_coverage_percent"] or "无退款") + " → " + (a["current_coverage_percent"] or "无退款") + "（数值单位 %）。")
    for row in result["products"]["rows"]:
        lines.append("已关联商品 " + json.dumps(row["product_id"], ensure_ascii=False) + "：" + money(row["baseline_minor"]) + " → " + money(row["current_minor"]) + "，变化 " + money(row["delta_minor"]) + "。")
    rest = result["products"]["other"]
    if rest["product_count"]:
        lines.append(f"其他 {rest['product_count']} 个已关联商品合计变化 " + money(rest["delta_minor"]) + "。")
    lines.append("分项与成功退款总额已核对一致。仅定位变化，不代表退款原因或统计异常；历史订单退款不能作为本期支付订单的退款率，商品归属缺失会限制商品比较。")
    return "\n".join(lines)


def analyze(ledger, start, end, current_start, current_end, *, workspace_id, top_n=5):
    if type(top_n) is not int or not 1 <= top_n <= 10:
        raise ContractError("invalid_top_n")
    specs = [query_spec(ledger.data, "refund_amount", x, y, workspace_id=workspace_id)
             for x, y in ((start, end), (current_start, current_end))]
    bounds = [v for spec in specs for v in spec["params"]]
    ax, ay, bx, by = map(datetime.fromisoformat, bounds)
    if ay > bx or ay-ax != by-bx:
        raise ContractError("incomparable_periods")
    p = dict(zip(("start", "end", "current_start", "current_end"), bounds))
    origins = {k: amounts() for k in ("prior_orders", "same_period_orders")}
    products, unattributed = {}, amounts()
    for label, origin, product, amount in ledger.connection.execute(GROUP_SQL, bounds).fetchall():
        field = label + "_minor"
        origins[origin][field] += int(amount)
        target = unattributed if product is None else products.setdefault(product, amounts())
        target[field] += int(amount)
    for row in [*origins.values(), *products.values(), unattributed]:
        row["delta_minor"] = row["current_minor"] - row["baseline_minor"]
    counts = {}
    for label, refunds, orders in ledger.connection.execute(COUNT_SQL, bounds).fetchall():
        counts[label + "_refunds"] = int(refunds)
        counts[label + "_orders"] = int(orders)
    a, b = [ledger.query("refund_amount", *pair, workspace_id=workspace_id) for pair in (bounds[:2], bounds[2:])]
    totals = amounts(a["value"], b["value"])
    linked = {key: sum(row[key] for row in products.values()) for key in totals}
    grouped = {key: sum(row[key] for row in origins.values()) for key in totals}
    if grouped != totals or any(linked[k] + unattributed[k] != totals[k] for k in totals):
        raise ContractError("refund_change_reconciliation_failed")
    ranked = sorted(({"product_id": key, **row, "change_percent": percent(row["delta_minor"], row["baseline_minor"])}
                     for key, row in products.items()), key=lambda row: (-abs(row["delta_minor"]), row["product_id"]))
    rest = ranked[top_n:]
    attribution = {"delta_linked_minor": linked["delta_minor"], "delta_unattributed_minor": unattributed["delta_minor"]}
    for label in ("baseline", "current"):
        key = label + "_minor"
        attribution[label + "_linked_minor"] = linked[key]
        attribution[label + "_unattributed_minor"] = unattributed[key]
        attribution[label + "_coverage_percent"] = percent(linked[key], totals[key])
    result = {"metric": METRIC, "metric_version": VERSION, "workspace_id": workspace_id,
              "snapshot_id": ledger.data["snapshot_id"], "currency": ledger.data["currency"], "amount_scale": 2,
              "result_unit": "minor_units", "period": p, "top_n": top_n,
              "totals": {**totals, "change_percent": percent(totals["delta_minor"], totals["baseline_minor"])},
              "counts": counts, "origins": [{"origin": k, **v} for k, v in origins.items()], "attribution": attribution,
              "products": {"product_count": len(ranked), "rows": ranked[:top_n],
                           "other": {"product_count": len(rest), **{k: sum(row[k] for row in rest) for k in totals}}},
              "reconciliation": {**grouped, "baseline_residual_minor": 0, "current_residual_minor": 0, "delta_residual_minor": 0},
              "queries": [{"role": "refund_groups", "sql": GROUP_SQL, "params": bounds},
                          {"role": "refund_counts", "sql": COUNT_SQL, "params": bounds},
                          *[{"role": role, "sql": r["sql"], "params": r["params"]} for role, r in (("baseline_total", a), ("current_total", b))]],
              "evidence": {**ledger.evidence, "canonical_sha256": digest(ledger.data),
                           "implementation_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                                       for name in ("refund_change.py", "ledger.py", "ledger_db.py")}}}
    result["summary"] = summary(result)
    return result
