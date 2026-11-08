"""Generate fresh database-only secrets in an ignored, owner-only host directory."""
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]
directory = ROOT / ".local/deployment-secrets"
directory.mkdir(parents=True, mode=0o700, exist_ok=False)
directory.chmod(0o700)
for name in ("postgres_admin_password", "COMMERCE_DB_PASSWORD"):
    with (directory / name).open("x") as output:
        output.write(secrets.token_hex(32))
    # Compose file-backed secrets retain host uid; children can read the mounted file.
    # Other host users cannot traverse the 0700 parent. No model API key is generated/stored.
    (directory / name).chmod(0o444)
print("Fresh database secrets prepared; values not displayed. Existing secrets are never replaced.")
