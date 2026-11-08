"""Deliberate counterexamples, not failures observed in a deployed system."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory() as directory:
        log = Path(directory) / "published.jsonl"
        child = "import json; queue=['task-1']; print(json.dumps(queue))"
        before = json.loads(subprocess.check_output([sys.executable, "-c", child]))
        after = json.loads(subprocess.check_output([
            sys.executable, "-c", "import json; queue=[]; print(json.dumps(queue))"
        ]))
        # Server commits; client loses response and resubmits the same operation.
        append = "import sys; open(sys.argv[1], 'a').write('task-1:23000\\n')"
        for _ in range(2):
            subprocess.run([sys.executable, "-c", append, str(log)], check=True)
        print(json.dumps({"kind": "constructed_counterexample", "queued_before_restart": len(before),
                          "queued_after_restart": len(after), "unprotected_publications": len(log.read_text().splitlines()),
                          "real_model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
