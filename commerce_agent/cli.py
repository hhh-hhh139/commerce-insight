"""Child-process entry point. Never emits keys or provider exception bodies."""
import argparse
import json
import os
from pathlib import Path
import sys

# Explicitly disable inherited cloud tracing before importing framework clients.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from .budget import PRICE_DATE
from .models import ScriptedModel, personal_deepseek
from .runtime import execute_agent
from .storage import AgentError, ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--script", type=Path, help="Operator-only offline test input, never an API parameter")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--interrupt-after-tools", action="store_true", help="Offline recovery test only")
    args = parser.parse_args()
    try:
        job = json.loads(args.job.read_text())
        if args.script:
            model = ScriptedModel(script=json.loads(args.script.read_text()))
            budget = None
        else:
            if args.interrupt_after_tools: raise AgentError("test_interrupt_requires_script")
            model = personal_deepseek(os.environ.get("COMMERCE_PERSONAL_DEEPSEEK_API_KEY"))
            budget = ROOT / (".local/single-agent/personal-budget-" + PRICE_DATE + ".json")
        result = execute_agent(job, args.snapshot, model, state_root=args.state_root, budget_path=budget,
                               interrupt_after=["tools"] if args.interrupt_after_tools else None)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except AgentError as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}), file=sys.stderr)
        return 3 if str(exc) == "agent_checkpoint_busy" else 2
    except Exception as exc:
        # Network/SDK exceptions may contain request headers. Record only the class.
        print(json.dumps({"status": "rejected", "reason": "agent_execution_failed", "kind": type(exc).__name__}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
