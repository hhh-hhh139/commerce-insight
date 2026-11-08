"""Additive product allocation of paid amounts; descriptive, never causal."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

from .core import ContractError, digest
from .ledger import query_spec

METRIC = "sales_change_by_product"
VERSION = "sales-change-v1"
PRODUCT_SQL = """SELECT i.product_id,
 SUM(CASE WHEN o.paid_at >= ? AND o.paid_at < ? THEN i.line_minor ELSE 0 END) AS baseline_minor,
 SUM(CASE WHEN o.paid_at >= ? AND o.paid_at < ? THEN i.line_minor ELSE 0 END) AS current_minor
FROM ledger_items i JOIN ledger_orders o ON i.order_id = o.order_id
WHERE o.status = 'paid' AND ((o.paid_at >= ? AND o.paid_at < ?) OR (o.paid_at >= ? AND o.paid_at < ?))
GROUP BY i.product_id ORDER BY i.product_id"""


def percent(numerator, denominator):
    if denominator == 0:
        return None
    value = (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return "0.00" if value == 0 else str(value)


def request_spec(data, start, end, current_start, current_end, *, workspace_id, top_n=5):
    if type(top_n) is not int or not 1 <= top_n <= 10:
        raise ContractError("invalid_top_n")
    a = query_spec(data, "paid_amount", start, end, workspace_id=workspace_id)
    b = query_spec(data, "paid_amount", current_start, current_end, workspace_id=workspace_id)
    ax, ay, bx, by = [datetime.fromisoformat(v) for v in (a["period"]["start"], a["period"]["end"], b["period"]["start"], b["period"]["end"])]
    if ay > bx or ay - ax != by - bx:
        raise ContractError("incomparable_periods")
    return {"start": a["period"]["start"], "end": a["period"]["end"],
            "current_start": b["period"]["start"], "current_end": b["period"]["end"]}


def summary(result):
    def money(value):
        return format(Decimal(value) / 100, ".2f") + " " + result["currency"]
    t = result["totals"]
    lines = ["已支付金额（不扣退款）：基期 " + money(t["baseline_minor"]) + "，对比期 " + money(t["current_minor"]) + "，变化 " + money(t["delta_minor"]) + "。",
             "基期为零，不计算总体变化率。" if t["change_percent"] is None else "总体变化率 " + t["change_percent"] + "%。"]
    for direction, label in (("decreases", "下降"), ("increases", "增长")):
        group = result[direction]
        lines.append(label + "商品的金额变化合计 " + money(group["delta_minor"]) + "。")
        for row in group["products"]:
            product = json.dumps(row["product_id"], ensure_ascii=False)
            lines.append("商品 " + product + "：" + money(row["baseline_minor"]) + " → " + money(row["current_minor"]) +
                         "，变化 " + money(row["delta_minor"]) + "，占全部" + label + "金额的 " + row["direction_share_percent"] + "% 。")
        if group["other"]["product_count"]:
            lines.append("其他 " + str(group["other"]["product_count"]) + " 个" + label + "商品合计变化 " + money(group["other"]["delta_minor"]) + "。")
    if t["delta_minor"] == 0:
        lines.append("总额没有变化；仍需查看增长与下降是否相互抵消。")
    lines.append("商品明细金额与订单总额、商品变化与总变化均已核对一致。占比按增长/下降方向分别计算，非占净变化；四舍五入后不保证合计恰为 100%。")
    lines.append("按商品编号展示已分摊的支付金额；期间无额不等于商品新上架或下架。这里只定位变化集中在哪里，不能证明价格、销量、缺货或投放等原因；不等于净销售额、到账或利润。")
    return "\n".join(lines)


def analyze(ledger, start, end, current_start, current_end, *, workspace_id, top_n=5):
    p = request_spec(ledger.data, start, end, current_start, current_end, workspace_id=workspace_id, top_n=top_n)
    bounds = [p[k] for k in ("start", "end", "current_start", "current_end")]
    rows = ledger.connection.execute(PRODUCT_SQL, bounds * 2).fetchall()
    products = [{"product_id": product, "baseline_minor": int(a), "current_minor": int(b),
                 "delta_minor": int(b-a), "change_percent": percent(int(b-a), int(a)),
                 "period_presence": "current_only" if a == 0 else "baseline_only" if b == 0 else "both"}
                for product, a, b in rows]
    a = ledger.query("paid_amount", p["start"], p["end"], workspace_id=workspace_id)
    b = ledger.query("paid_amount", p["current_start"], p["current_end"], workspace_id=workspace_id)
    sums = {k: sum(row[k] for row in products) for k in ("baseline_minor", "current_minor", "delta_minor")}
    totals = {"baseline_minor": a["value"], "current_minor": b["value"], "delta_minor": b["value"]-a["value"]}
    if sums != totals:
        raise ContractError("sales_change_reconciliation_failed")
    totals["change_percent"] = percent(totals["delta_minor"], totals["baseline_minor"])
    result = {"metric": METRIC, "metric_version": VERSION, "workspace_id": workspace_id,
              "snapshot_id": ledger.data["snapshot_id"], "currency": ledger.data["currency"], "amount_scale": 2,
              "result_unit": "minor_units", "period": p, "top_n": top_n, "totals": totals,
              "product_count": len(products), "unchanged_count": sum(row["delta_minor"] == 0 for row in products),
              "reconciliation": {**sums, "baseline_residual_minor": 0, "current_residual_minor": 0, "delta_residual_minor": 0}}
    for direction, sign in (("decreases", -1), ("increases", 1)):
        ranked = sorted((r for r in products if r["delta_minor"] * sign > 0), key=lambda r: (-abs(r["delta_minor"]), r["product_id"]))
        total = sum(row["delta_minor"] for row in ranked)
        for row in ranked[:top_n]:
            row["direction_share_percent"] = percent(abs(row["delta_minor"]), abs(total))
        rest = ranked[top_n:]
        result[direction] = {"product_count": len(ranked), "delta_minor": total, "products": ranked[:top_n],
                             "other": {"product_count": len(rest), "delta_minor": sum(r["delta_minor"] for r in rest)}}
    result["queries"] = [{"role": "product_totals", "sql": PRODUCT_SQL, "params": bounds * 2},
                         *[{"role": role, "sql": r["sql"], "params": r["params"]} for role, r in (("baseline_total", a), ("current_total", b))]]
    result["evidence"] = {**ledger.evidence, "canonical_sha256": digest(ledger.data),
                          "implementation_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                                      for name in ("sales_change.py", "ledger.py", "ledger_db.py")}}
    result["summary"] = summary(result)
    return result
