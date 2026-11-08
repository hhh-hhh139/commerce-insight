"""Verify the evaluator with real LangGraph/tools and scripted decisions, never a live model."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from commerce_agent.models import ScriptedModel
from commerce_agent.runtime import execute_agent
from scripts.evaluate_personal_agent import grade, grade_expectations, audit_publication

SNAPSHOT = ROOT / "examples/ledger/snapshot.json"
CASES = json.loads((ROOT / "experiments/sales-evaluation/cases.json").read_text())
LEGACY = json.loads((ROOT / "experiments/single-agent/cases.json").read_text())


def fixture(case, directory):
    calls = []
    for expected in case["expected"]:
        args = {k: v for k, v in expected.items() if k not in ("value", "sales_change", "refund_change")}
        metric = args["metric"]
        if metric in ("sales_change_by_product", "refund_change"):
            args.pop("metric")
        calls.append({"name": {"sales_change_by_product":"query_sales_change", "refund_change":"query_refund_change"}.get(metric,"query_metric"), "args": args})
    decision = {"outcome": case["outcome"], "issue": case.get("issue", "none"),
                "evidence_ids": [f"$e{i}" for i in range(len(calls))],
                "comparisons": [{"baseline_id": "$e0", "current_id": "$e1"}] if "delta" in case else []}
    script = ([{"tool_calls": calls}] if calls else []) + [{"tool_calls": [{"name": "AnalysisDecision", "args": decision}]}]
    job = {"id": str(uuid.uuid4()), "attempt": 1, "workspace_id": "demo-shop",
           "source_sha256": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(),
           "request": {"kind": "agent", "dataset_id": "demo-ledger-v1", "question": case["question"]}}
    return execute_agent(job, SNAPSHOT, ScriptedModel(script=script), state_root=directory)


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = ROOT / ".local/sales-evaluation/verification"
        parent.mkdir(parents=True, exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(dir=parent))
        cls.cases = CASES["development"] + CASES["heldout"]
        cls.reports = {c["id"]: fixture(c, cls.root) for c in cls.cases}
        cls.case = cls.cases[0]
        cls.report = cls.reports[cls.case["id"]]

    def test_01_nine_handwritten_oracles_and_recomputation(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                report = self.reports[case["id"]]
                self.assertEqual(grade_expectations(case, report), {"passed": True, "errors": []})
                self.assertEqual(audit_publication(case, report), [])

    def test_02_scripted_reports_cannot_pass_live_gate(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                report = self.reports[case["id"]]
                self.assertEqual(report["usage"]["real_model_calls"], 0)
                self.assertEqual(grade(case, report), {"passed": False, "errors": ["not_a_real_model_run"]})

    def test_03_legacy_ten_case_contracts_still_grade_and_audit(self):
        for case in LEGACY["development"] + LEGACY["heldout"]:
            with self.subTest(case=case["id"]):
                report = fixture(case, self.root)
                self.assertTrue(grade_expectations(case, report)["passed"])
                self.assertEqual(audit_publication(case, report), [])

    def test_04_detect_product_others_percentage_currency_and_reconciliation_mutations(self):
        mutations = [
            lambda r: r["totals"].update(delta_minor=-12999),
            lambda r: r["decreases"]["products"][0].update(product_id="P1"),
            lambda r: r["decreases"]["products"][0].update(direction_share_percent="76.92"),
            lambda r: r["decreases"]["other"].update(delta_minor=-1),
            lambda r: r["reconciliation"].update(delta_residual_minor=1),
            lambda r: r.update(currency="USD"),
            lambda r: r.update(unchanged_count=False),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                report = copy.deepcopy(self.report)
                mutate(report["evidence"][0]["result"])
                self.assertIn("wrong_query_or_value", grade_expectations(self.case, report)["errors"])
                self.assertTrue(audit_publication(self.case, report))

    def test_05_missing_and_unexpected_evidence_fail_without_crashing(self):
        for mode in ("missing_field", "extra_evidence", "no_evidence", "wrong_type"):
            with self.subTest(mode=mode):
                report = copy.deepcopy(self.report)
                if mode == "missing_field": del report["evidence"][0]["result"]["totals"]
                elif mode == "extra_evidence": report["evidence"].append(copy.deepcopy(report["evidence"][0]))
                elif mode == "no_evidence": report["evidence"] = []
                else: report["evidence"][0]["result"] = None
                self.assertFalse(grade_expectations(self.case, report)["passed"])

    def test_06_wrong_period_or_top_n_is_not_a_correct_answer(self):
        for key, value in (("top_n", 1), ("current_start", "2026-10-11T00:00:00.000000+00:00")):
            report = copy.deepcopy(self.report)
            report["evidence"][0]["query"][key] = value
            self.assertIn("wrong_query_or_value", grade_expectations(self.case, report)["errors"])

    def test_07_audit_rejects_forged_reference_text_or_source(self):
        mutations = [lambda r: r["evidence"][0].update(evidence_id="ev_fake"),
                     lambda r: r.update(answer="证明销量下降导致损失 999999 元"),
                     lambda r: r["evidence"][0]["result"]["evidence"].update(source_sha256="0" * 64)]
        for mutate in mutations:
            report = copy.deepcopy(self.report)
            mutate(report)
            self.assertTrue(audit_publication(self.case, report))

    def test_08_zero_baseline_does_not_accept_invented_percentage(self):
        case = next(c for c in self.cases if c["id"] == "hold_zero_baseline")
        report = copy.deepcopy(self.reports[case["id"]])
        report["evidence"][0]["result"]["totals"]["change_percent"] = "100.00"
        self.assertFalse(grade_expectations(case, report)["passed"])

    def test_09_issue_and_outcome_are_scored_separately(self):
        case = next(c for c in self.cases if c["id"] == "hold_price_quantity")
        report = copy.deepcopy(self.reports[case["id"]])
        report["issue"] = "unsupported_request"
        self.assertEqual(grade_expectations(case, report)["errors"], ["wrong_issue"])

    def test_10_structured_evidence_cannot_be_scalar_comparison(self):
        for comparisons in ([{"delta": -13000}], None):
            report = copy.deepcopy(self.report)
            report["comparisons"] = comparisons
            self.assertFalse(grade_expectations(self.case, report)["passed"])

    def test_11_business_families_disjoint_and_release_gate_fixed(self):
        dev = {c["family"] for c in CASES["development"]}
        held = {c["family"] for c in CASES["heldout"]}
        self.assertFalse(dev & held)
        self.assertEqual(CASES["acceptance"], {"heldout_min_success_rate": .8,
            "heldout_repeats": 2, "published_numeric_or_reference_errors_allowed": 0})


if __name__ == "__main__":
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(EvaluationTests))
    report = {"kind": "sales_evaluator_engineering_only", "date": "2026-11-06", "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "seconds": round(time.monotonic() - started, 3), "real_model_calls": 0,
              "new_oracle_cases": 9, "legacy_oracle_cases": 10, "real_model_quality": "not_measured",
              "failure_details": [{"test": str(test), "traceback": trace} for test, trace in result.failures + result.errors]}
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".local/sales-evaluation/verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(not result.wasSuccessful())
