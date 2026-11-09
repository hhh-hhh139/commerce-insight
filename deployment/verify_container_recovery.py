"""Called only by the guarded personal hosted-runner verifier; no host discovery."""
import json


def verify_recovery(*, run, docker, compose, root, private, results, report, request, login, task, completed):
    record = {"passed": False, "real_model_calls": 0, "checks": []}
    report["recovery"] = record
    source_project = "commerce-personal-ci"
    target_project = "commerce-personal-recovery-ci"
    assert compose[compose.index("--project-name") + 1] == source_project
    shared = private / "recovery-compose.yaml"
    shared.write_text("volumes:\n  backups:\n    external: true\n    name: commerce-personal-ci_backups\n")
    target = list(compose)
    target[target.index("--project-name") + 1] = target_project
    target += ["--file", str(shared)]

    def maintenance(config, action, name, *, error=None):
        output = run([*config, "run", "--rm", "--no-deps", "-T", "maintenance", action, name],
                     capture=True, combine=True, expected=2 if error else 0)
        values = [json.loads(line) for line in output.splitlines() if line.startswith('{"passed":')]
        assert len(values) == 1, "maintenance_result_missing"
        value = values[0]
        if error: assert value == {"passed": False, "error": error}, value
        else: assert value["passed"] and value["action"] == action
        return value

    def fixture(project, action):
        args = [*docker, "run", "--rm", "--network", project + "_business", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "1536m", "--pids-limit", "128",
            "--tmpfs", "/tmp:mode=1777,size=128m",
            "--tmpfs", "/app/.local/task-loop:uid=1000,gid=1000,mode=0700,size=128m",
            "--tmpfs", "/app/.local/ledger-runs:uid=1000,gid=1000,mode=0700,size=128m"]
        for name, path in (("owner", "owner-demo"), ("agent", "single-agent"), ("control", "state-control")):
            args += ["--mount", "type=volume,source=" + project + "_" + name + ",target=/app/.local/" + path]
        args += ["--mount", "type=bind,source=" + str(root / ".local/deployment-secrets/COMMERCE_DB_PASSWORD")
                 + ",target=/run/secrets/COMMERCE_DB_PASSWORD,readonly",
                 "commerce-insight:verification", "experiments/deployment/recovery_fixture.py", action]
        return json.loads(run(args, capture=True))

    try:
        versions = run([*compose, "run", "--rm", "--no-deps", "-T", "--entrypoint", "/usr/bin/python3",
            "maintenance", "-c", "import json,platform,subprocess; print(json.dumps({'python':platform.python_version(),"
            "'pg_dump':subprocess.check_output(['/usr/lib/postgresql/18/bin/pg_dump','--version'],text=True).strip()}))"], capture=True)
        record["maintenance_versions"] = json.loads(versions)
        assert record["maintenance_versions"]["pg_dump"].startswith("pg_dump (PostgreSQL) 18.6")
        maintenance(compose, "backup", "must-not-backup-live", error="application_state_in_use")
        record["checks"].append("live_application_backup_refused")
        run([*compose, "stop", "proxy", "app"])
        record["seed"] = fixture(source_project, "seed")
        assert record["seed"]["passed"] and record["seed"]["pending_status"] == "QUEUED"
        record["backup"] = maintenance(compose, "backup", "ci-snapshot")

        # Keep the valid original bundle intact. Corrupt copies are CI fixtures only.
        corrupt = """from pathlib import Path
import json,shutil
root=Path('/app/.local/backups')
source=root/'ci-snapshot'
shutil.copytree(source,root/'ci-corrupted')
with (root/'ci-corrupted/database.dump').open('ab') as stream: stream.write(b'broken')
shutil.copytree(source,root/'ci-wrong-version')
p=root/'ci-wrong-version/manifest.json'
value=json.loads(p.read_text());value['implementation']={};p.write_text(json.dumps(value))
"""
        run([*compose, "run", "--rm", "--no-deps", "-T", "--entrypoint", "/usr/bin/python3", "maintenance", "-c", corrupt])
        run([*target, "up", "--detach", "--no-build", "--wait", "--wait-timeout", "120", "db"])
        maintenance(target, "restore", "ci-corrupted", error="backup_checksum_mismatch")
        maintenance(target, "restore", "ci-wrong-version", error="backup_implementation_mismatch")
        record["checks"].append("corruption_and_implementation_mismatch_refused")
        restored = maintenance(target, "restore", "ci-snapshot")
        assert restored["manifest_sha256"] == record["backup"]["manifest_sha256"]

        verify_files = """from pathlib import Path
import json
from scripts.backup_state import sha
root=Path('/app/.local')
value=json.loads((root/'backups/ci-snapshot/manifest.json').read_text())
targets={'runtime':root/'owner-demo/offline','agent':root/'single-agent'}
count=0
for name,digest in value['files'].items():
    if name=='database.dump': continue
    relative=Path(name);target=targets[relative.parts[0]].joinpath(*relative.parts[1:])
    assert sha(target)==digest and target.stat().st_mode & 0o777==0o600
    count+=1
for path in targets.values():
    assert not (path/'RESTORE_INCOMPLETE').exists() and path.stat().st_mode & 0o777==0o700
print(json.dumps({'passed':True,'state_files_verified':count}))
"""
        check = [*target, "run", "--rm", "--no-deps", "-T", "--entrypoint", "/usr/bin/python3", "maintenance", "-c", verify_files]
        record["files"] = json.loads(run(check, capture=True))
        maintenance(target, "restore", "ci-snapshot", error="restore_state_target_exists")
        assert json.loads(run(check, capture=True)) == record["files"]
        record["checks"].append("all_state_hashes_modes_markers_and_nonempty_target_refusal")

        # Empty state targets with an already-restored DB must still be refused.
        reject_db = """from pathlib import Path
from scripts.backup_state import Database,restore
root=Path('/app/.local')
db=Database('db',5432,'commerce','commerce_app',Path('/run/secrets/COMMERCE_DB_PASSWORD').read_text().strip(),'/usr/lib/postgresql/18/bin')
runtime=root/'owner-demo/must-not-create';agent=root/'single-agent/must-not-create'
try: restore(db,root/'backups/ci-snapshot',runtime,agent,allow_empty_targets=True)
except RuntimeError as error: assert str(error)=='restore_database_not_empty'
else: raise AssertionError('nonempty_database_accepted')
assert not runtime.exists() and not agent.exists()
"""
        run([*target, "run", "--rm", "--no-deps", "-T", "--entrypoint", "/usr/bin/python3", "maintenance", "-c", reject_db])
        record["checks"].append("nonempty_database_refused_before_creating_state")
        record["resume"] = fixture(target_project, "verify")
        assert record["resume"]["passed"]
        record["checks"].append("import_report_pending_task_checkpoint_replay_budget_preserved")
        run([*target, "up", "--detach", "--no-build", "--wait", "--wait-timeout", "120"])
        assert request("/demo/api/config")[0] == 401
        csrf = login()  # The original application password must still work.
        assert request("/demo/api/tasks/" + task["id"])[1]["result"] == completed["result"]
        assert request("/logout", {}, form=True, headers=csrf)[0] == 302
        record["checks"].append("restored_owner_tls_login_and_original_report")
        record["passed"] = True
    finally:
        try:
            try: run([*target, "logs", "--no-color"], timeout=30)
            finally: run([*target, "down", "--volumes", "--remove-orphans"], timeout=60)
        except Exception:
            record["passed"] = False
            record["cleanup_failed"] = True
            raise
        finally:
            (results / "container-recovery.json").write_text(json.dumps(record, indent=2) + "\n")
