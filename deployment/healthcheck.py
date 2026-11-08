"""Loopback readiness without credentials on argv, in output, or proxy defaults."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commerce_worker.worker import Client

try:
    token = (ROOT / ".local/owner-demo/offline/user.token").read_text()
    result = Client("http://127.0.0.1:18080", token).call("/api/workspaces/demo-shop/system", method="GET")
    import time
    healthy = result["storage"] == "postgres_single_instance" and result["worker_last_poll_at"] is not None \
        and time.time() * 1000 - result["worker_last_poll_at"] < 30_000
except Exception:
    healthy = False
raise SystemExit(0 if healthy else 1)
