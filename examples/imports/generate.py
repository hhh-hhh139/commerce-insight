"""Derive two explicitly synthetic export layouts from the reviewed ledger fixture."""
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
LABELS={
    "orders":{"order_id":"订单编号","status":"支付状态","paid_minor":"实付金额","paid_at":"支付时间","currency":"币种"},
    "items":{"item_id":"明细编号","order_id":"所属订单","product_id":"商品编码","line_minor":"分摊金额"},
    "refunds":{"refund_id":"退款编号","order_id":"所属订单","item_id":"关联明细","status":"售后状态","amount_minor":"退款金额","refunded_at":"退款成功时间","currency":"币种"}}
STATES={"orders":{"paid":"已支付","pending":"待支付","cancelled":"已取消"},
        "refunds":{"succeeded":"退款成功","pending":"处理中","failed":"退款失败"}}


def main():
    data=json.loads((ROOT/"examples/ledger/snapshot.json").read_text())
    for layout in ("standard-minor","renamed-major"):
        renamed=layout=="renamed-major";dest=Path(__file__).parent/layout;dest.mkdir(parents=True,exist_ok=True)
        mapping={"schema_version":"ledger-csv-map-v1","currency":"CNY","time_basis":"UTC+08:00" if renamed else "explicit_offset",
            "exported_at":"2026-10-26T08:00:00" if renamed else "2026-10-26T00:00:00Z","tables":{}}
        for table,rows in data["tables"].items():
            columns={k:LABELS[table][k] if renamed else k for k in rows[0]}
            with (dest/(table+".csv")).open("w",encoding="utf-8",newline="") as f:
                writer=csv.DictWriter(f,fieldnames=list(columns.values()));writer.writeheader()
                for row in rows:
                    converted={}
                    for k,v in row.items():
                        if renamed and k.endswith("_minor"):
                            v=f"{v//100}.{v%100:02d}"
                        if renamed and k in {"paid_at","refunded_at"} and v:
                            v=datetime.fromisoformat(v.replace("Z","+00:00")).astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat()
                        if renamed and k=="status":v=STATES[table][v]
                        converted[columns[k]]=v
                    writer.writerow(converted)
            spec={"columns":columns,"amount_unit":"major" if renamed else "minor"}
            if table in STATES:spec["statuses"]={v if renamed else k:k for k,v in STATES[table].items()}
            mapping["tables"][table]=spec
        (dest/"mapping.json").write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+"\n")


if __name__=="__main__":main()
