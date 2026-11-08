"""Same bounded refund-change tool as the Agent, exposed to the existing Worker."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from .core import ContractError
from .ledger import load_snapshot
from .ledger_db import ControlledLedger, build_database
from .refund_change import analyze


def main():
    parser = argparse.ArgumentParser(description="成功退款变化与归属分析")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--workspace", required=True)
    for field in ("start", "end", "current-start", "current-end"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()
    try:
        data, evidence = load_snapshot(args.snapshot)
        parent = Path(__file__).resolve().parents[1] / ".local/ledger-runs"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            db = build_database(data, Path(directory) / "snapshot.duckdb")
            with ControlledLedger(data, db, evidence) as ledger:
                result = analyze(ledger, args.start, args.end, args.current_start, args.current_end,
                                 workspace_id=args.workspace, top_n=args.top_n)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError):
        # No raw input or traceback in Worker logs.
        print(json.dumps({"reason": "refund_change_rejected"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
