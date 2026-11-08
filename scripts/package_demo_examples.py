"""Package only the reviewed synthetic-data reports; preserve the originals byte-semantically."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/probes/single-agent-live-2026-11-06.json"
OUTPUT = ROOT / "services/business-api/src/main/resources/static/demo/examples.json"
SELECTED = [
    ("dev_comparison", "两周销售额如何变化？", "同口径比较、金额差额与变化率。"),
    ("dev_cohort", "这批订单最终留下多少钱？", "订单批次与跨期退款，口径明确到截止日。"),
    ("dev_missing_attribution", "能算商品 P1 的退款吗？", "数据不完整时，保留边界并说明无法计算。"),
    ("hold_relative_time", "时间不明确，先澄清", "只说“上周”不足以确定分析区间。"),
    ("hold_profit", "利润问题：一项保留的失败", "正确拒算，但拒答分类未通过原评测。"),
]


def package():
    raw = SOURCE.read_bytes()
    source = json.loads(raw)
    examples = []
    for case_id, title, description in SELECTED:
        record = next(r for r in source["results"] if r["case_id"] == case_id and r["repeat"] == 1)
        examples.append({"case_id": case_id, "title": title, "description": description,
            "task": record["task"], "passed": record["passed"], "errors": record["errors"]})
    return {"kind": "historical_live_examples", "date": "2026-11-06", "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(raw).hexdigest(), "examples": examples}


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(package(), ensure_ascii=False, indent=2) + "\n")
    print("Packaged five historical reports, including one original evaluation failure. No model calls.")
