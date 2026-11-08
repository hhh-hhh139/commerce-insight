"""Worker adapter for confirmed report claims; no model calls."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from .core import ContractError
from .ledger import load_snapshot
from .ledger_db import ControlledLedger, build_database
from .reconciliation import analyze


def main():
    parser=argparse.ArgumentParser(description="两份结构化报告与授权账本核对")
    parser.add_argument("--snapshot",type=Path,required=True)
    parser.add_argument("--job",type=Path,required=True)
    args=parser.parse_args()
    try:
        data,evidence=load_snapshot(args.snapshot)
        if args.job.stat().st_size>100_000:raise ContractError("report_request_too_large")
        job=json.loads(args.job.read_text());q=job["request"]
        if evidence["source_sha256"]!=job["source_sha256"]:raise ContractError("source_mismatch")
        parent=Path(__file__).resolve().parents[1]/".local/ledger-runs";parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            database=build_database(data,Path(directory)/"snapshot.duckdb")
            with ControlledLedger(data,database,evidence) as ledger:
                result=analyze(ledger,q["left"],q["right"],workspace_id=job["workspace_id"],confirmed=q["confirmed"],allowed_metrics=job.get("dataset",{}).get("available_metrics"))
        print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
    except (ContractError,OSError,ValueError,KeyError,TypeError):
        print(json.dumps({"reason":"report_reconciliation_rejected"}),file=sys.stderr);return 2


if __name__=="__main__":raise SystemExit(main())
