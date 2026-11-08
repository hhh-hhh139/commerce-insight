"""Conservative shared CNY reservations, written before any paid request."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo
from .storage import AgentError, local_path, lock, write_json

PRICE_DATE = "2026-11-06"
# PRICE_DATE identifies the original cumulative ledger, NOT today's date.
# Never change it to open a fresh budget. Same rates/model rechecked on Nov 8.
VERIFIED_DATES = frozenset((PRICE_DATE, "2026-11-08"))
MODEL = "deepseek-flash"
MAX_OUTPUT = 1024
# Official 1M context, rounded UP to 2**20; reserve all input at peak cache-miss rate.
# Integer micro-CNY: input token 2, output token 8. Unknown responses keep full reserve.
RESERVATION = 1_048_576 * 2 + MAX_OUTPUT * 8
CAP = 5_000_000


class Budget:
    def __init__(self, path, today=None):
        self.path = local_path(path)
        self.today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        if self.today not in VERIFIED_DATES:
            raise AgentError("model_pricing_needs_revalidation")

    def read(self):
        if not self.path.exists():
            return {"price_date": PRICE_DATE, "model": MODEL, "cap_micro_cny": CAP, "calls": []}
        value = json.loads(self.path.read_text())
        if value.get("price_date") != PRICE_DATE or value.get("model") != MODEL or value.get("cap_micro_cny") != CAP:
            raise AgentError("budget_configuration_mismatch")
        return value

    def reserve(self, task_id):
        with lock(str(self.path) + ".lock"):
            value = self.read()
            charged = sum(c["charged_micro_cny"] for c in value["calls"])
            if charged + RESERVATION > CAP:
                raise AgentError("personal_budget_exhausted")
            position = len(value["calls"])
            value["calls"].append({"task_id": task_id, "status": "unknown", "charged_micro_cny": RESERVATION,
                                   "price_verified_date": self.today})
            write_json(self.path, value)
            return position

    def settle(self, position, usage):
        if not usage or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")):
            return  # Keep the entire reservation if usage is missing/untrusted.
        if usage["input_tokens"] > 1_048_576 or usage["output_tokens"] > MAX_OUTPUT:
            raise AgentError("provider_usage_exceeds_reserved_limit")
        with lock(str(self.path) + ".lock"):
            value = self.read(); item = value["calls"][position]
            if item["status"] != "unknown":
                raise AgentError("budget_already_settled")
            item.update(status="usage_received", input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                        charged_micro_cny=usage["input_tokens"] * 2 + usage["output_tokens"] * 8)
            write_json(self.path, value)
