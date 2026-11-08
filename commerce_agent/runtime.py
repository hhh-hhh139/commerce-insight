"""LangGraph-backed create_agent, durable local state and evidence-only publication."""
import hashlib
from contextlib import closing
import importlib.metadata
import json
from pathlib import Path
import sqlite3
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.exceptions import ModelConnectionError, ModelTimeoutError
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langsmith import tracing_context
from commerce_data.ledger import load_snapshot
from commerce_data.ledger_db import build_database, ControlledLedger
from .budget import Budget, MODEL
from .contracts import AnalysisDecision, METRICS, digest
from .models import ScriptedModel
from .report import build_report
from .storage import AgentError, Journal, ROOT, local_path, lock, write_json
from .tools import collected_evidence, make_tools

PROMPT = Path(__file__).with_name("prompt.txt")
SPECIALIZATIONS = {
    "sales": "你是固定复合流程中的销售专家，只处理原问题中已支付金额和商品销售变化部分。退款部分由另一专家处理，不要重复计算。若你的部分缺口径或日期，正常澄清；若要求缺失数据或外部操作，正常拒答。不要声称已完成另一专家的任务。",
    "refunds": "你是固定复合流程中的退款专家，只处理原问题中的成功退款、退款来源和归属部分。销售部分由另一专家处理，不要重复计算。若你的部分缺口径或日期，正常澄清；若要求缺失数据或外部操作，正常拒答。不要声称已完成另一专家的任务。",
}


class Guard(AgentMiddleware):
    def __init__(self, journal, task_id, budget=None, model_limit=5):
        self.journal, self.task_id, self.budget = journal, task_id, budget
        self.model_limit = model_limit

    def wrap_model_call(self, request, handler):
        if self.budget and any(e["kind"] == "model" and e.get("provider_dispatched") is not False
                               and (e["status"] != "completed" or not e.get("usage") or e.get("failure_code"))
                               for e in self.journal.read()["events"]):
            raise AgentError("model_failure_requires_review")
        # Bound context growth independently of provider limits. Never log full requests.
        size = sum(len(str(m.content).encode()) + len(str(getattr(m, "tool_calls", [])).encode()) for m in request.messages)
        if size > 60000: raise AgentError("agent_context_limit")
        position = self.journal.reserve("model", self.model_limit)
        try:
            reservation = self.budget.reserve(self.task_id) if self.budget else None
        except AgentError as exc:
            self.journal.finish(position, provider_dispatched=False, admission_error=str(exc))
            raise
        try:
            response = handler(request)
        except Exception as exc:
            if self.budget is None: raise  # Preserve scripted engineering exceptions.
            status = getattr(exc, "status_code", None)
            if type(status) is not int or not 100 <= status <= 599: status = None
            category = type(exc).__name__
            codes = {400: "model_request_invalid", 401: "model_authentication_failed", 402: "model_balance_insufficient",
                     422: "model_request_invalid", 429: "model_rate_limited", 500: "model_provider_unavailable", 503: "model_provider_unavailable"}
            code = codes.get(status, "model_connection_failed" if isinstance(exc, (ModelConnectionError, ModelTimeoutError)) else "model_response_unknown")
            # Do not persist exception text, response bodies, request headers or credentials.
            self.journal.finish(position, provider_dispatched=True, provider_status=status,
                                error_type=category[:80], failure_code=code)
            raise AgentError(code) from None
        ai_messages = [m for m in response.result if m.type == "ai"]
        usages = [getattr(m, "usage_metadata", None) for m in ai_messages]
        usage = None
        if usages and all(u and type(u.get("input_tokens")) is int and type(u.get("output_tokens")) is int for u in usages):
            usage = {k: sum(u[k] for u in usages) for k in ("input_tokens", "output_tokens")}
        if self.budget: self.budget.settle(reservation, usage)
        models = [str(m.response_metadata.get("model_name", m.response_metadata.get("model", "unspecified")))[:160] for m in ai_messages]
        self.journal.finish(position, usage=usage, response_models=models, provider_dispatched=self.budget is not None)
        return response


def implementation_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))}


from scripts.state_guard import uses_state


@uses_state
def execute_agent(job, snapshot_path, model, *, state_root=None, budget_path=None, interrupt_after=None,
                  context_mode="full", specialization=None):
    # Operator-only experiment options; never accepted by the public task API.
    if context_mode not in ("full", "compact") or specialization not in (None, *SPECIALIZATIONS):
        raise AgentError("invalid_agent_experiment")
    model_limit, tool_limit = (2, 2) if specialization else (5, 4)
    prompt = PROMPT.read_text() + ("\n" + SPECIALIZATIONS[specialization] if specialization else "")
    if specialization:
        prompt = prompt.replace("只有一个 Agent", "你是固定双专家流程的一个组成部分")
        prompt = prompt.replace("最多 4 次业务工具调用、5 次模型调用", "最多 2 次业务工具调用、2 次模型调用")
    import re
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", job["id"]): raise AgentError("invalid_agent_task_id")
    data, source_evidence = load_snapshot(snapshot_path)
    if data["workspace_id"] != job["workspace_id"] or source_evidence["source_sha256"] != job["source_sha256"]:
        raise AgentError("agent_input_binding_mismatch")
    policy = None
    if job["request"].get("dataset_id", "").startswith("ds-"):
        dataset = job.get("dataset", {})
        if dataset.get("id") != job["request"]["dataset_id"] or dataset.get("source_sha256") != job["source_sha256"] or dataset.get("model_allowed") is not True:
            raise AgentError("dataset_policy_binding_mismatch")
        if not isinstance(dataset.get("available_metrics"), list) or not all(isinstance(m, str) for m in dataset["available_metrics"]):
            raise AgentError("invalid_dataset_capabilities")
        policy = {k: dataset[k] for k in ("id", "source_sha256", "available_metrics", "mapping_sha256")}
    question = job["request"].get("question")
    if job["request"].get("kind") != "agent" or not isinstance(question, str) or not 1 <= len(question) <= 2000:
        raise AgentError("invalid_agent_question")
    mode = "scripted" if isinstance(model, ScriptedModel) else "live"
    if mode == "live" and budget_path is None: raise AgentError("personal_budget_required")
    root = local_path((state_root or ROOT / ".local/single-agent/runs") / Path(job["id"]))
    if (root.parent.parent / "RESTORE_INCOMPLETE").exists(): raise AgentError("state_restore_incomplete")
    root.mkdir(parents=True, exist_ok=True)
    versions = {name: importlib.metadata.version(name) for name in ("langchain", "langgraph", "langgraph-checkpoint-sqlite", "langchain-deepseek")}
    identity = {"task_id": job["id"], "source_sha256": job["source_sha256"], "workspace_id": job["workspace_id"],
                "request_sha256": digest(job["request"]), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "context_mode": context_mode, "specialization": specialization,
                "model_limit": model_limit, "tool_limit": tool_limit,
                "versions": versions, "implementation_sha256": implementation_hashes(), "mode": mode,
                "model": "scripted-engineering-test" if mode == "scripted" else MODEL,
                "script_sha256": digest(model.script) if mode == "scripted" else None}
    if policy is not None: identity["dataset_policy_sha256"] = digest(policy)
    with lock(root / "run.lock", blocking=False):
        identity_path = root / "identity.json"
        if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
            raise AgentError("checkpoint_identity_mismatch")
        write_json(identity_path, identity)
        journal = Journal(root / "journal.json")
        database = root / "snapshot.duckdb"
        if database.exists():
            # A process can die during first DB creation. Rebuild only our derived file.
            try:
                with ControlledLedger(data, database, source_evidence): pass
            except Exception:
                database.unlink()
                wal = database.with_suffix(database.suffix + ".wal")
                if wal.exists(): wal.unlink()
        if not database.exists(): build_database(data, database)
        budget = Budget(budget_path) if budget_path else None
        serde = JsonPlusSerializer(pickle_fallback=False,
                                   allowed_msgpack_modules=[("commerce_agent.contracts", "AnalysisDecision"),
                                                            ("commerce_agent.contracts", "Comparison")])
        with closing(sqlite3.connect(root / "checkpoints.sqlite", check_same_thread=False)) as connection:
            saver = SqliteSaver(connection, serde=serde)
            allowed = set(policy["available_metrics"]) if policy else set(METRICS)
            if specialization:
                sales = {m for m in METRICS if m.startswith("paid_")}
                allowed &= sales if specialization == "sales" else set(METRICS) - sales
            tools = make_tools(data, database, source_evidence, journal, sorted(allowed),
                               context_mode=context_mode, tool_limit=tool_limit)
            if specialization:
                excluded = "query_refund_change" if specialization == "sales" else "query_sales_change"
                tools = [t for t in tools if t.name != excluded]
            agent = create_agent(model=model, tools=tools,
                                 system_prompt=prompt,
                                 response_format=ToolStrategy(AnalysisDecision, handle_errors="Return exactly one valid AnalysisDecision. Do not add numbers or unknown fields."),
                                 middleware=[Guard(journal, job["id"], budget, model_limit),
                                             ModelCallLimitMiddleware(thread_limit=model_limit, run_limit=model_limit, exit_behavior="error"),
                                             ToolCallLimitMiddleware(thread_limit=tool_limit, run_limit=tool_limit, exit_behavior="error")],
                                 checkpointer=saver, interrupt_after=interrupt_after, name="commerce_single_agent")
            config = {"configurable": {"thread_id": digest(identity)}, "recursion_limit": 50, "max_concurrency": 1}
            saved = agent.get_state(config)
            resumed = bool(saved.values)
            if resumed and not saved.next and saved.values.get("structured_response") is not None:
                state = saved.values
            else:
                initial = None if resumed else {"messages": [{"role": "user", "content": question}]}
                with tracing_context(enabled=False):
                    state = agent.invoke(initial, config, durability="sync")
            checkpoint_count = sum(1 for _ in agent.get_state_history(config))
            if state.get("structured_response") is None:
                if interrupt_after: return {"interrupted": True, "checkpoint_count": checkpoint_count}
                raise AgentError("agent_missing_structured_decision")
            saved_usage = journal.read()
            events = saved_usage["events"]
            known = [event["usage"] for event in events if event.get("usage")]
            usage = {"model_attempts": saved_usage["model_attempts"], "tool_attempts": saved_usage["tool_attempts"],
                     "input_tokens": sum(u["input_tokens"] for u in known), "output_tokens": sum(u["output_tokens"] for u in known),
                     "unknown_model_responses": sum(e["kind"] == "model" and e.get("provider_dispatched") is not False and (e["status"] != "completed" or e.get("usage") is None) for e in events),
                     "real_model_calls": sum(e["kind"] == "model" and e.get("provider_dispatched") is not False for e in events) if mode == "live" else 0}
            provenance = {**identity, "attempt": job["attempt"], "resumed": resumed,
                          "checkpoint_count": checkpoint_count,
                          "response_models": sorted({m for e in events for m in e.get("response_models", [])})}
            evidence = collected_evidence(state["messages"], source_sha256=job["source_sha256"],
                                          workspace_id=job["workspace_id"], snapshot_id=data["snapshot_id"])
            result = build_report(job, state["structured_response"], evidence, provenance, usage)
            write_json(root / "report.json", result)
            write_json(root / "trace.json", {"identity": identity, "events": events})
            return result
