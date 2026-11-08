"""Local named-metric entry point for a confirmed normalized ledger snapshot."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .core import ContractError
from .ledger import load_snapshot
from .ledger_db import ControlledLedger, build_database


def main():
    parser = argparse.ArgumentParser(description="订单／明细／成功退款的有限指标工具")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--product-id")
    parser.add_argument("--confirm-contract", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.snapshot.resolve():
        parser.error("output must not overwrite snapshot")
    try:
        if not args.confirm_contract:
            raise ContractError("ledger_contract_confirmation_required")
        data, evidence = load_snapshot(args.snapshot)
        parent = Path(__file__).resolve().parents[1] / ".local/ledger-runs"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            db = build_database(data, Path(directory) / "snapshot.duckdb")
            with ControlledLedger(data, db, evidence) as tool:
                result = tool.query(args.metric, args.start, args.end, workspace_id=args.workspace,
                                    as_of=args.as_of, product_id=args.product_id)
        output = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
            print(json.dumps({"output": str(args.output), "value": result["value"], "unit": result["result_unit"]}))
        else:
            print(output, end="")
    except (ContractError, OSError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
