"""Explicit container entrypoints. Offline by default; never inherits a model key."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    os.environ.pop("COMMERCE_PERSONAL_DEEPSEEK_API_KEY", None)
    action = sys.argv[1] if len(sys.argv) > 1 else "serve"
    runtime = str(ROOT / ".local/owner-demo/offline")
    if action == "owner":
        from scripts.prepare_owner import main as owner
        sys.argv = [sys.argv[0], "--runtime", runtime, *sys.argv[2:]]
        return owner()
    if action != "serve": raise SystemExit("Supported actions: owner, serve")
    if len(sys.argv) > 2: raise SystemExit("No implicit command overrides; configure explicit deployment variables")
    origin = os.environ.get("COMMERCE_ORIGIN", "")
    if not origin.startswith("https://"): raise SystemExit("Explicit HTTPS COMMERCE_ORIGIN is required")
    from scripts.web_demo import main as serve
    sys.argv = [sys.argv[0], "--owner", "--postgres", "--db-secrets", "/run/secrets",
                "--runtime", runtime, "--bind", "0.0.0.0", "--port", "18080", "--origin", origin, "--console-logs"]
    return serve()


if __name__ == "__main__": main()
