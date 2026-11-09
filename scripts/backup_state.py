"""Offline, project-scoped PostgreSQL + application-state backup/restore.

Only restore trusted backups made by this deployment. SHA-256 detects corruption,
not a malicious author. PostgreSQL dumps contain executable SQL.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.state_guard import state_access


def local(path):
    path = Path(path).absolute()
    # is_relative_to is lexical: .local/../elsewhere would otherwise pass.
    # Reject traversal before inspecting any filesystem component.
    if ".." in path.parts or not path.is_relative_to(ROOT / ".local") or path == ROOT / ".local":
        raise ValueError("backup_paths_must_be_project_local")
    for part in (path, *path.parents):
        if part == ROOT: break
        if part.is_symlink(): raise ValueError("backup_symlink_rejected")
    return path


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint():
    files = [ROOT / "services/business-api/target/business-api-0.1.0.jar",
             ROOT / "requirements-agent.lock", ROOT / "examples/ledger/snapshot.json"]
    for name in ("commerce_agent", "commerce_data", "commerce_import", "commerce_worker", "scripts"):
        files += [p for p in (ROOT / name).rglob("*") if p.is_file() and p.suffix in (".py", ".txt", ".json")]
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}


class Database:
    def __init__(self, host, port, name, user, password, binary):
        if not host or host.startswith("-") or not 1 <= port <= 65535:
            raise ValueError("invalid_database_endpoint")
        if not all(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", s) for s in (name, user)):
            raise ValueError("invalid_database_identifier")
        if not password: raise ValueError("explicit_database_password_required")
        self.host, self.port, self.name, self.user = host, port, name, user
        self.binary = Path(binary).resolve()
        # No inherited service files, user .psqlrc, .pgpass, tracing, proxies or cloud credentials.
        self.env = {"PATH": str(self.binary) + ":/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                    "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5", "PGAPPNAME": "commerce-backup"}

    def run(self, program, options, *, sql=None):
        args = [str(self.binary / program), "-h", self.host, "-p", str(self.port), "-U", self.user,
                "-d", self.name, "--no-password", *options]
        result = subprocess.run(args, env=self.env, input=sql, text=True, capture_output=True, timeout=180)
        if result.returncode: raise RuntimeError("database_" + program + "_failed")
        return result.stdout.strip()

    def query(self, sql):
        return self.run("psql", ["-X", "-At", "-v", "ON_ERROR_STOP=1"], sql=sql)

    def idle(self):
        if self.query("SELECT COUNT(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()") != "0":
            raise RuntimeError("database_has_other_connections_stop_application_first")

    def empty(self):
        if self.query("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')") != "0":
            raise RuntimeError("restore_database_not_empty")


def files_under(path):
    if not path.exists(): return []
    result = []
    for item in sorted(path.rglob("*")):
        local(item)
        if item.is_dir(): continue
        if not item.is_file(): raise ValueError("backup_special_file_rejected")
        if item.name.endswith(".lock"): continue
        result.append(item)
    return result


def write_manifest(path, value):
    with path.open("x") as output:
        os.chmod(path, 0o600)
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n"); output.flush(); os.fsync(output.fileno())


def backup(db, runtime, agent, destination):
    runtime, agent, destination = map(local, (runtime, agent, destination))
    if any(destination.is_relative_to(p) or p.is_relative_to(destination) for p in (runtime, agent)):
        raise ValueError("backup_paths_overlap")
    with state_access(maintenance=True):
        db.idle()
        version = db.query("SELECT version FROM flyway_schema_history WHERE success ORDER BY installed_rank DESC LIMIT 1")
        if version != "1": raise ValueError("backup_schema_version_unsupported")
        implementation = fingerprint()
        destination.mkdir(parents=True, mode=0o700, exist_ok=False)
        # An incomplete directory intentionally remains for diagnosis; no manifest means unusable.
        database = destination / "database.dump"
        db.run("pg_dump", ["--format=custom", "--compress=0", "--no-acl", "--schema=public", "--file=" + str(database)])
        database.chmod(0o600)
        sources = [(runtime / name, "runtime/" + name) for name in ("owner.hash", "user.token", "worker.token")]
        if any(not path.is_file() for path, _ in sources): raise ValueError("owner_runtime_incomplete")
        # Include all cumulative ledgers, not only the currently validated pricing date.
        sources += [(p, "agent/" + str(p.relative_to(agent))) for p in files_under(agent / "runs")]
        sources += [(p, "agent/" + p.name) for p in sorted(agent.glob("personal-budget-*.json"))]
        hashes = {"database.dump": sha(database)}
        for path, name in sources:
            local(path)
            target = destination / name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(path, target); target.chmod(0o600); hashes[name] = sha(target)
        write_manifest(destination / "manifest.json", {"format": "commerce-backup-v1", "schema_version": version,
            "created_at": datetime.now(timezone.utc).isoformat(), "implementation": implementation, "files": hashes,
            "model_calls": 0, "scope": "stopped single-instance application; original cumulative budget retained"})
        return {"files": len(hashes), "manifest_sha256": sha(destination / "manifest.json")}


def restore(db, source, runtime, agent, *, allow_empty_targets=False):
    source, runtime, agent = map(local, (source, runtime, agent))
    paths = (source, runtime, agent)
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(paths) for b in paths[i+1:]):
        raise ValueError("restore_paths_overlap")
    with state_access(maintenance=True):
        # Never merges or replaces an existing budget, owner credential, run or report.
        if type(allow_empty_targets) is not bool:
            raise ValueError("invalid_restore_target_mode")
        for target in (runtime, agent):
            if target.exists() and not (allow_empty_targets and target.is_dir() and not any(target.iterdir())):
                raise ValueError("restore_state_target_exists")
        # Validate the manifest itself before its first read, not only when later
        # walking the bundle. A symlink must never be followed for metadata either.
        value = json.loads(local(source / "manifest.json").read_text())
        if value.get("format") != "commerce-backup-v1" or value.get("schema_version") != "1":
            raise ValueError("backup_manifest_invalid")
        if value.get("implementation") != fingerprint(): raise ValueError("backup_implementation_mismatch")
        expected = value["files"]
        required = {"database.dump", "runtime/owner.hash", "runtime/user.token", "runtime/worker.token"}
        if not required.issubset(expected): raise ValueError("backup_missing_required_files")
        actual = {str(p.relative_to(source)) for p in files_under(source)} - {"manifest.json"}
        if actual != set(expected): raise ValueError("backup_file_set_mismatch")
        for name, digest in expected.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or str(relative) != name:
                raise ValueError("backup_path_invalid")
            if name != "database.dump" and relative.parts[0] not in ("runtime", "agent"):
                raise ValueError("backup_path_invalid")
            if sha(local(source / name)) != digest: raise ValueError("backup_checksum_mismatch")
        db.idle(); db.empty()
        # Stage files first. On database failure they remain marked incomplete; no app should start.
        for target in (runtime, agent):
            target.mkdir(parents=True, mode=0o700, exist_ok=allow_empty_targets)
            # Recheck an existing mount root under the same maintenance lock.
            if any(target.iterdir()): raise ValueError("restore_state_target_exists")
            target.chmod(0o700)
            (target / "RESTORE_INCOMPLETE").touch(mode=0o600)
        for name in expected:
            if name == "database.dump": continue
            relative = Path(name)
            target = (runtime if relative.parts[0] == "runtime" else agent).joinpath(*relative.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source / name, target); target.chmod(0o600)
        # The administrator already created/restricted public and assigned it to this role.
        # Keep that boundary; the dump's CREATE SCHEMA would collide with the empty schema.
        listing = db.run("pg_restore", ["--list", str(source / "database.dump")])
        selected = []
        schema_entries = 0
        for line in listing.splitlines():
            if re.fullmatch(r"\d+; \d+ \d+ SCHEMA - public .*", line):
                schema_entries += 1
                continue
            if re.fullmatch(r"\d+; \d+ \d+ COMMENT - SCHEMA public .*", line): continue
            selected.append(line)
        if schema_entries != 1: raise ValueError("backup_schema_entry_invalid")
        selection = runtime / ".restore-selection"
        selection.write_text("\n".join(selected) + "\n"); selection.chmod(0o600)
        try:
            db.run("pg_restore", ["--single-transaction", "--exit-on-error", "--no-owner", "--no-acl",
                "--use-list=" + str(selection), str(source / "database.dump")])
        finally:
            selection.unlink(missing_ok=True)
        for target in (runtime, agent): (target / "RESTORE_INCOMPLETE").unlink()
        return {"files": len(expected), "manifest_sha256": sha(source / "manifest.json")}


def main():
    parser = argparse.ArgumentParser(description="停写备份/恢复；只接受本项目可信备份，绝不覆盖已有状态")
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--agent", type=Path, default=ROOT / ".local/single-agent")
    parser.add_argument("--host", required=True); parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True); parser.add_argument("--user", required=True)
    parser.add_argument("--pg-bin", type=Path, default=ROOT / ".local/postgres-toolchain/installed/bin")
    args = parser.parse_args()
    db = Database(args.host, args.port, args.database, args.user, os.environ.get("COMMERCE_DB_PASSWORD"), args.pg_bin)
    try:
        result = (backup(db, args.runtime, args.agent, args.bundle) if args.action == "backup"
                  else restore(db, args.bundle, args.runtime, args.agent))
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        # Do not print driver output, file contents or connection strings.
        print("备份/恢复未完成；已拒绝继续，检查停写、空目标、版本及备份完整性。", file=sys.stderr)
        return 2
    print(json.dumps(result)); return 0


if __name__ == "__main__": raise SystemExit(main())
