"""Fixed two-specialist research candidate; NOT exposed by the task API or CLI."""
from .contracts import digest
from .report import build_report
from .runtime import execute_agent
from .storage import AgentError


def execute_specialists(job, snapshot_path, models, *, state_root, budget_path=None):
    reports = []
    # Sequential execution preserves one-at-a-time shared monetary reservation.
    for role in ("sales", "refunds"):
        child = {**job, "id": "specialist-" + digest({"id": job["id"], "role": role})}
        reports.append(execute_agent(child, snapshot_path, models[role], state_root=state_root,
                                     budget_path=budget_path, context_mode="compact", specialization=role))
    modes = {r["provenance"]["mode"] for r in reports}
    if len(modes) != 1: raise AgentError("mixed_specialist_modes")
    evidence = {e["evidence_id"]: e for r in reports for e in r["evidence"]}
    failures = [r for r in reports if r["outcome"] != "answered"]
    if failures:
        # A partial answer never masquerades as completion of the composite request.
        failure = next((r for r in failures if r["outcome"] == "unsupported"), failures[0])
        decision = {"outcome": failure["outcome"], "issue": failure["issue"], "evidence_ids": [], "comparisons": []}
    else:
        decision = {"outcome": "answered", "issue": "none", "evidence_ids": list(evidence),
                    "comparisons": [{k: c[k] for k in ("baseline_id", "current_id")}
                                    for r in reports for c in r["comparisons"]]}
    usage = {k: sum(r["usage"][k] for r in reports) for k in reports[0]["usage"]}
    provenance = {"mode": modes.pop(), "flow": "experimental_fixed_specialists_v1",
                  "merge": "deterministic_all_or_clarify", "specialists": [r["provenance"] for r in reports]}
    return build_report(job, decision, evidence, provenance, usage)
