"""Explicit, offline maintenance for this deployment's state and database only."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.backup_state import Database, backup, restore


def main():
    parser = argparse.ArgumentParser(description="停写备份或恢复可信备份到空数据库/空状态卷")
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("bundle", help="备份卷内的目录名称，不接受路径")
    args = parser.parse_args()
    if ROOT != Path("/app") or os.getuid() != 1000:
        parser.error("仅在本项目 UID 1000 的维护容器中使用")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", args.bundle):
        parser.error("备份名称只接受小写字母、数字、下划线和连字符，最长 64 字符")
    try:
        password = Path("/run/secrets/COMMERCE_DB_PASSWORD").read_text().strip()
        db = Database("db", 5432, "commerce", "commerce_app", password, "/usr/lib/postgresql/18/bin")
        runtime = ROOT / ".local/owner-demo/offline"
        agent = ROOT / ".local/single-agent"
        bundle = ROOT / ".local/backups" / args.bundle
        result = (backup(db, runtime, agent, bundle) if args.action == "backup" else
                  restore(db, bundle, runtime, agent, allow_empty_targets=True))
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        # Only known internal codes are exposed; never log database/file contents.
        code = str(error)
        if not re.fullmatch(r"(?:backup|restore|database|application|explicit_database)_[a-z_]+", code):
            code = "maintenance_failed"
        print(json.dumps({"passed": False, "error": code}), file=sys.stderr)
        return 2
    print(json.dumps({"passed": True, "action": args.action, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
