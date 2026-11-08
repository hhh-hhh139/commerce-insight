"""Explicit personal DeepSeek config and opt-in scripted models for engineering tests."""
import json
from pathlib import Path
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from .budget import MAX_OUTPUT, MODEL
from .storage import AgentError


class ScriptedModel(BaseChatModel):
    """Replays tool decisions; does not understand language and makes no API calls."""
    script: list[dict] = Field(exclude=True)

    @property
    def _llm_type(self): return "scripted-engineering-test"

    def bind_tools(self, tools, **kwargs): return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        index = sum(message.type == "ai" for message in messages)
        if index >= len(self.script): raise AgentError("script_exhausted")
        response = json.loads(json.dumps(self.script[index]))
        refs = []
        for message in messages:
            if message.type == "tool" and message.name in ("query_metric", "query_sales_change", "query_refund_change"):
                if getattr(message, "status", None) == "error": continue
                try: data = json.loads(message.content)
                except (ValueError, TypeError): continue
                if "evidence_id" in data: refs.append(data["evidence_id"])
        def resolve(value):
            if isinstance(value, str) and value.startswith("$e") and value[2:].isdigit():
                return refs[int(value[2:])]
            if isinstance(value, list): return [resolve(v) for v in value]
            if isinstance(value, dict): return {k: resolve(v) for k, v in value.items()}
            return value
        calls = [{"name": t["name"], "args": resolve(t["args"]), "id": f"script-{index}-{i}", "type": "tool_call"}
                 for i, t in enumerate(response.get("tool_calls", []))]
        msg = AIMessage(content=response.get("content", ""), tool_calls=calls,
                        usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        response_metadata={"model_name": "scripted-engineering-test"})
        return ChatResult(generations=[ChatGeneration(message=msg)])


def personal_deepseek(key, *, http_client=None, http_async_client=None):
    if not key or len(key.strip()) < 10: raise AgentError("personal_model_not_configured")
    import httpx
    from langchain_deepseek import ChatDeepSeek
    # Explicit credentials and endpoint. Never fall back to default key/proxy/tracing config.
    return ChatDeepSeek(model=MODEL, api_key=key, api_base="https://api.deepseek.com",
                        max_tokens=MAX_OUTPUT, temperature=0, timeout=25, max_retries=0,
                        extra_body={"thinking": {"type": "disabled"}},
                        http_client=http_client or httpx.Client(trust_env=False, follow_redirects=False, timeout=25),
                        http_async_client=http_async_client or httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=25))
