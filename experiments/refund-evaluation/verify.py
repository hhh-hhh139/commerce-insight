"""Verify hand-calculated refund oracles and publication audit, without API calls."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location("sales_evaluator",ROOT/"experiments/sales-evaluation/verify.py")
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
from scripts.evaluate_personal_agent import grade,grade_expectations,audit_publication

CASES=json.loads((ROOT/"experiments/refund-evaluation/cases.json").read_text())


class RefundEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent=ROOT/".local/refund-evaluation";parent.mkdir(parents=True,exist_ok=True)
        cls.root=Path(tempfile.mkdtemp(dir=parent))
        cls.cases=CASES["development"]+CASES["heldout"]
        cls.reports={c["id"]:helper.fixture(c,cls.root) for c in cls.cases}
        cls.case=cls.cases[0];cls.report=cls.reports[cls.case["id"]]

    def test_handwritten_oracles_and_independent_publication_recomputation(self):
        for c in self.cases:
            with self.subTest(case=c["id"]):
                self.assertEqual(grade_expectations(c,self.reports[c["id"]]),{"passed":True,"errors":[]})
                self.assertEqual(audit_publication(c,self.reports[c["id"]]),[])

    def test_scripted_reports_cannot_pass_live_gate(self):
        for c in self.cases:self.assertEqual(grade(c,self.reports[c["id"]])["errors"],["not_a_real_model_run"])

    def test_important_business_mutations_are_detected(self):
        mutations=[lambda r:r["counts"].update(baseline_orders=3),
            lambda r:r["origins"][0].update(baseline_minor=0),
            lambda r:r["attribution"].update(baseline_unattributed_minor=0),
            lambda r:r["attribution"].update(baseline_coverage_percent="100.00"),
            lambda r:r["attribution"].update(delta_unattributed_minor=False),
            lambda r:r["products"]["rows"][0].update(product_id="P2"),
            lambda r:r["products"]["other"].update(current_minor=900),
            lambda r:r["reconciliation"].update(delta_residual_minor=1)]
        for mutate in mutations:
            report=copy.deepcopy(self.report);mutate(report["evidence"][0]["result"])
            self.assertFalse(grade_expectations(self.case,report)["passed"])
            self.assertTrue(audit_publication(self.case,report))

    def test_forged_text_reference_or_period_rejected(self):
        for mutate in (lambda r:r.update(answer="退款是商品质量导致的"),
            lambda r:r["evidence"][0].update(evidence_id="ev_fake"),
            lambda r:r["evidence"][0]["query"].update(current_end="2026-10-25T00:00:00Z")):
            report=copy.deepcopy(self.report);mutate(report);self.assertTrue(audit_publication(self.case,report))

    def test_zero_denominator_and_missing_evidence(self):
        case=next(c for c in self.cases if c["id"]=="hold_zero_base")
        report=copy.deepcopy(self.reports[case["id"]]);report["evidence"][0]["result"]["totals"]["change_percent"]="100.00"
        self.assertFalse(grade_expectations(case,report)["passed"])
        report["evidence"][0]["result"]={};self.assertFalse(grade_expectations(case,report)["passed"])

    def test_gate_fixed_and_business_families_disjoint(self):
        self.assertFalse({c["family"] for c in CASES["development"]}&{c["family"] for c in CASES["heldout"]})
        self.assertEqual(CASES["acceptance"],{"heldout_min_success_rate":.8,"heldout_repeats":2,"published_numeric_or_reference_errors_allowed":0})


if __name__=="__main__":
    started=time.monotonic()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RefundEvaluationTests))
    report={"kind":"refund_evaluator_engineering_only","date":"2026-11-06","tests":result.testsRun,
        "failures":len(result.failures),"errors":len(result.errors),"seconds":round(time.monotonic()-started,3),
        "real_model_calls":0,"new_oracle_cases":8,"real_model_quality":"not_measured",
        "failure_details":[{"test":str(test),"traceback":trace} for test,trace in result.failures+result.errors]}
    target=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/".local/refund-evaluation/verification.json"
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    raise SystemExit(not result.wasSuccessful())
