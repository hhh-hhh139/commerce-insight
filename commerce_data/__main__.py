import argparse
import json
import sys
from pathlib import Path

from .core import ContractError
from .files import analyze_file


def main():
    parser = argparse.ArgumentParser(description="本地销售记录分析原型；不调用模型或外部服务")
    parser.add_argument("source", type=Path)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--sheet")
    parser.add_argument("--start", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--confirm-mapping", action="store_true")
    parser.add_argument("--require-capability", default="record_sales")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() in {args.source.resolve(), args.mapping.resolve()}:
        parser.error("output must not overwrite input or mapping")
    try:
        result = analyze_file(args.source, args.mapping, args.start, args.split, args.end,
                              args.sheet, args.confirm_mapping, args.require_capability)
    except (ContractError, OSError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    output = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "status": result["analysis_status"], "quality": result["quality"]}, ensure_ascii=False))
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
