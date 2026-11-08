"""Runs inside the existing bounded Worker process; no model/provider dependencies."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from .adapter import evaluate


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--job",type=Path,required=True)
    parser.add_argument("--input",type=Path,required=True)
    args=parser.parse_args()
    try:
        job=json.loads(args.job.read_text());raw=args.input.read_bytes();payload=json.loads(raw)
        sha=hashlib.sha256(raw).hexdigest()
        if sha!=job["source_sha256"] or payload["workspace_id"]!=job["workspace_id"] or any(payload[k]!=job["request"][k] for k in ("draft_id","revision")):
            raise ValueError("binding")
        result=evaluate(payload,task_id=job["id"],input_sha256=sha)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"reason":"import_executor_failed"}),file=sys.stderr)
        return 2


if __name__=="__main__": raise SystemExit(main())
