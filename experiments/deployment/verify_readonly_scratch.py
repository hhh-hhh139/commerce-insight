"""Reproduce the CLI scratch-directory failure with actual non-root permissions.

Only the module's source location is redirected to an isolated project fixture;
filesystem operations, snapshot validation, DuckDB and cleanup are real.
This is not a Docker test; verify_clean_ci.py supplies container evidence.
"""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from commerce_data import ledger_cli


def main():
    if os.name != "posix" or os.geteuid() == 0:
        raise SystemExit("Requires a non-root POSIX user for actual permission enforcement.")
    out = ROOT / ".local/ci-readonly-probe"
    out.mkdir(parents=True, exist_ok=True)
    args = ["ledger_cli", str(ROOT / "examples/ledger/snapshot.json"), "--confirm-contract",
            "--workspace", "demo-shop", "--metric", "paid_amount", "--start",
            "2026-10-05T00:00:00Z", "--end", "2026-10-12T00:00:00Z"]
    record = {"date": datetime.now(timezone.utc).date().isoformat(),
              "kind": "actual_filesystem_cli_temporary_path", "container_test": False,
              "real_model_calls": 0, "baseline_commit": "f190be01c792e25eb13faa8a52acdad9ed899c8e"}
    with tempfile.TemporaryDirectory(dir=out) as directory:
        fake = Path(directory)
        parent = fake / ".local"
        parent.mkdir(); parent.chmod(0o500)
        try:
            for fixed in (False, True):
                if fixed:
                    parent.chmod(0o700)
                    (parent / "ledger-runs").mkdir(mode=0o700)
                    parent.chmod(0o500)
                stdout, stderr = StringIO(), StringIO()
                with patch.object(ledger_cli, "__file__", str(fake / "commerce_data/ledger_cli.py")), \
                        patch.object(sys, "argv", args), redirect_stdout(stdout), redirect_stderr(stderr):
                    code = ledger_cli.main()
                if not fixed:
                    assert code == 2 and "Permission denied" in stderr.getvalue()
                    record["before"] = {"exit_code": code, "error": "PermissionError creating .local/ledger-runs"}
                else:
                    result = json.loads(stdout.getvalue())
                    assert code == 0 and result["value"] == 28000
                    assert not list((parent / "ledger-runs").iterdir())
                    record["after"] = {"exit_code": code, "value": result["value"],
                                       "root_parent_still_nonwritable": True, "temporary_database_cleaned": True}
        finally:
            parent.chmod(0o700)
    record["passed"] = True
    (out / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
