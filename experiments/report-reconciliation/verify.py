"""Reconciliation through real Java/HTTP/Worker/DuckDB, no model or external API."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
imports=module("reconciliation_imports",ROOT/"experiments/data-import/verify.py")
unit=module("reconciliation_unit",ROOT/"tests/test_reconciliation.py")
from commerce_worker.worker import command,run_once,child_environment,ApiFailure

QUERY={"dataset_id":"demo-ledger-v1","metric":"report_reconciliation","left":unit.LEFT,"right":unit.RIGHT,"confirmed":True}


class ReconciliationHttpTests(imports.ImportHttpTests):
    def finish_analysis(self,task):
        for _ in range(60):
            result=self.get(task)
            if result["status"] not in ("QUEUED","RUNNING"):return result
            run_once(self.worker)
        self.fail("analysis did not finish")

    def result(self,query):
        t=self.finish_analysis(self.create(query));self.assertEqual(t["status"],"SUCCEEDED",t);return t["result"]

    def test_reconcile_01_task_and_idempotency(self):
        key=str(uuid.uuid4());a=self.create(QUERY,key);b=self.create(QUERY,key);self.assertEqual(a["id"],b["id"])
        t=self.finish_analysis(a);self.assertEqual(t["status"],"SUCCEEDED",t);r=t["result"]
        self.assertEqual((r["status"],r["observed_delta"],r["unexplained_delta"]),("explained",-5000,0))
        self.assertEqual(r["bridge"]["end_value"],23000)

    def test_reconcile_02_discrepancy_equal_errors_and_unverified_sources(self):
        r=self.result({**QUERY,"right":{**unit.RIGHT,"reported_value":24000}})
        self.assertEqual((r["status"],r["unexplained_delta"]),("discrepancy",1000))
        claim={**unit.LEFT,"reported_value":29000}
        r=self.result({**QUERY,"left":claim,"right":claim});self.assertEqual(r["status"],"discrepancy");self.assertEqual(r["observed_delta"],0)
        for source in (None,"0"*64):
            r=self.result({**QUERY,"right":{**unit.RIGHT,"source_sha256":source}})
            self.assertEqual(r["status"],"needs_review");self.assertIsNone(r["bridge"])

    def test_reconcile_03_units_and_unknown_metric_version(self):
        for change in ({"currency":"USD"},{"amount_scale":0},{"result_unit":"orders"},{"metric_version":"unknown-v2"}):
            r=self.result({**QUERY,"right":{**unit.RIGHT,**change}});self.assertEqual(r["status"],"needs_review");self.assertIsNone(r["bridge"])

    def test_reconcile_04_confirmed_import_with_and_without_refunds(self):
        for refunds in (True,False):
            d=self.publish(imports.adapter.fixture(refunds=refunds));source=d["dataset"]["source_sha256"]
            r=self.result({**QUERY,"dataset_id":d["dataset_id"],"left":{**unit.LEFT,"source_sha256":source},"right":{**unit.RIGHT,"source_sha256":source}})
            self.assertEqual(r["status"],"explained" if refunds else "needs_review")
            self.assertEqual(r["reports"]["left"]["recomputed_value"],28000)

    def test_reconcile_05_confirmation_schema_and_scope_boundaries(self):
        for change in ({"confirmed":False},{"confirmed":1},{"left":{**unit.LEFT,"reported_value":True}},
                       {"left":{**unit.LEFT,"sql":"SELECT 1"}},{"left":{**unit.LEFT,"query":{**unit.QUERY,"metric":"report_reconciliation"}}},
                       {"left":{**unit.LEFT,"query":{**unit.QUERY,"start":"2026-10-05T00:00:00.000000001Z"}}}):
            self.assertEqual(self.http("/demo/api/tasks",{**QUERY,**change},{"Idempotency-Key":str(uuid.uuid4())})[0],400)
        with self.assertRaises(ApiFailure) as exc:self.user.call("/api/workspaces/other/tasks",QUERY,headers={"Idempotency-Key":str(uuid.uuid4())})
        self.assertEqual(exc.exception.status,403)

    def test_reconcile_06_publication_rejects_forged_binding_and_bridge(self):
        t=self.create(QUERY);job=self.worker.call("/internal/tasks/claim",{});self.assertEqual(t["id"],job["id"])
        path=self.runtime/"reconcile-source.json";path.write_bytes(base64.b64decode(job["dataset_base64"]))
        r=json.loads(subprocess.check_output(command(job,path),cwd=ROOT,env=child_environment()))
        mutations=[lambda r:r.update(status="matched"),lambda r:r.update(unexplained_delta=1000),
            lambda r:r["bridge"]["components"][1].update(value=-4000),lambda r:r["reports"]["right"].update(claim_sha256="0"*64),
            lambda r:r["reports"]["right"]["claim"].update(reported_value=24000),lambda r:r["reports"]["left"].update(residual_value=100),
            lambda r:r["reports"]["right"]["calculation"].update(value=24000),lambda r:r["evidence"].update(source_sha256="0"*64),
            lambda r:r["differences"].clear()]
        for mutate in mutations:
            bad=copy.deepcopy(r);mutate(bad)
            with self.assertRaises(ApiFailure) as exc:self.worker.call("/internal/tasks/"+job["id"]+"/complete",{"lease_token":job["lease_token"],"result":bad})
            self.assertEqual(exc.exception.status,422)
        self.worker.call("/internal/tasks/"+job["id"]+"/complete",{"lease_token":job["lease_token"],"result":r});self.assertEqual(self.get(t)["status"],"SUCCEEDED")

    def test_reconcile_07_timezone_normalization_and_history_recovery(self):
        right={**unit.LEFT,"query":{**unit.QUERY,"start":"2026-10-05T08:00:00+08:00","end":"2026-10-12T08:00:00+08:00"}}
        t=self.finish_analysis(self.create({**QUERY,"right":right}));self.assertEqual(t["status"],"SUCCEEDED",t);self.assertEqual(t["result"]["status"],"matched")
        self.stop();self.start();self.assertEqual(self.get(t),t)
        cancelled=self.create(QUERY);self.http("/demo/api/tasks/"+cancelled["id"]+"/cancel",{})
        self.assertEqual(self.get(cancelled)["status"],"CANCELLED")


if __name__=="__main__":
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(unit.ReconciliationTests),
        unittest.TestSuite(ReconciliationHttpTests(name) for name in sorted(ReconciliationHttpTests.__dict__) if name.startswith("test_reconcile_"))])
    started=time.monotonic();result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={"date":"2026-11-06","kind":"report_reconciliation_engineering","tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
        "seconds":round(time.monotonic()-started,3),"real_model_calls":0,"failure_details":[{"test":str(t),"traceback":trace} for t,trace in result.failures+result.errors]}
    target=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/".local/reconciliation/check.json"
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n");raise SystemExit(not result.wasSuccessful())
