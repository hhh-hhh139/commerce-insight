"""Real JVM + H2 + queue + CSV adapter + DuckDB/LangGraph, zero real model calls."""
import base64
import concurrent.futures
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import unittest
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
web=module("web_acceptance",ROOT/"experiments/web-demo/verify.py")
adapter=module("adapter_acceptance",ROOT/"tests/test_import_adapter.py")
from commerce_worker.worker import ApiFailure, run_once, command


class ImportHttpTests(web.WebTests):
    def setUp(self):
        super().setUp();self.drafts=[]

    def tearDown(self):
        for id in self.drafts:
            d=self.http("/demo/api/imports/"+id)[1]
            self.http("/demo/api/imports/"+id+"/archive",{"expected_revision":d["revision"]})
        super().tearDown()

    def finish(self,draft):
        for _ in range(50):
            d=self.http("/demo/api/imports/"+draft["id"])[1]
            if d["state"]!="CHECKING":return d
            run_once(self.worker)
        self.fail("import did not finish")

    def upload(self,payload=None,key=None,finish=True):
        payload=payload or adapter.fixture()
        body={"name":"公开合成导入测试","source_declaration":"public_or_synthetic","files":payload["files"]}
        code,d,_=self.http("/demo/api/imports",body,{"Idempotency-Key":key or str(uuid.uuid4())})
        self.assertEqual(code,202,d);self.drafts.append(d["id"])
        return self.finish(d) if finish else d

    def check(self,draft,mapping,finish=True):
        code,d,_=self.http("/demo/api/imports/"+draft["id"]+"/check",{"expected_revision":draft["revision"],"mapping":mapping})
        self.assertEqual(code,202,d);return self.finish(d) if finish else d

    def confirmation(self,d,allowed=False):
        return {"revision":d["revision"],"check_task_id":d["check_task_id"],"review_sha256":d["report"]["review_sha256"],
            "acknowledge_contract":True,"allow_model_use":allowed}

    def publish(self,p=None,allowed=False):
        p=p or adapter.fixture();d=self.check(self.upload(p),p["mapping"])
        self.assertEqual(d["state"],"NEEDS_CONFIRMATION",d)
        code,ready,_=self.http("/demo/api/imports/"+d["id"]+"/confirm",self.confirmation(d,allowed))
        self.assertEqual(code,200,ready);self.assertEqual(ready["state"],"READY");return ready

    def query(self,d):
        t=self.create({**web.QUERY,"dataset_id":d["dataset_id"]})
        for _ in range(50):
            result=self.get(t)
            if result["status"] not in ("QUEUED","RUNNING"):return result
            run_once(self.worker)
        self.fail("query not complete")

    def test_import_01_two_layouts_publish_and_query_same_values(self):
        a=self.publish();b=self.publish(adapter.fixture("renamed-major"))
        for d in (a,b):
            t=self.query(d);self.assertEqual(t["status"],"SUCCEEDED");self.assertEqual(t["result"]["value"],23000)
            self.assertEqual(t["result"]["evidence"]["source_sha256"],d["dataset"]["source_sha256"])
            self.assertEqual(t["dataset"]["mapping_sha256"],d["report"]["mapping_sha256"])
        self.assertNotEqual(a["dataset"]["source_sha256"],b["dataset"]["source_sha256"])
        self.assertNotEqual(a["dataset"]["files"]["orders"]["sha256"],b["dataset"]["files"]["orders"]["sha256"])

    def test_import_02_confirmation_required_and_blocking_errors(self):
        p=adapter.fixture();adapter.change(p,"orders",lambda rs:rs[0].update(paid_minor="11000"))
        d=self.upload(p)
        self.assertEqual(d["state"],"PROFILED")
        body={**web.QUERY,"dataset_id":"ds-"+d["id"]+"-r1"}
        self.assertEqual(self.http("/demo/api/tasks",body,{"Idempotency-Key":str(uuid.uuid4())})[0],409)
        d=self.check(d,p["mapping"]);self.assertEqual(d["state"],"BLOCKED")
        self.assertEqual(d["report"]["errors"][0]["code"],"paid_item_amounts_do_not_reconcile")
        self.assertEqual(self.http("/demo/api/imports/"+d["id"]+"/confirm",self.confirmation(d))[0],422)
        self.assertNotIn("snapshot_base64",json.dumps(d))

    def test_import_03_latest_revision_and_digest_fence(self):
        p=adapter.fixture();d=self.upload(p)
        first=self.check(d,p["mapping"],finish=False)
        second=self.check(first,p["mapping"],finish=False)
        ready=self.finish(second);self.assertEqual(ready["revision"],2)
        old=self.http("/demo/api/tasks/"+first["check_task_id"])[1]
        stale={**self.confirmation(ready),"revision":1,"check_task_id":first["check_task_id"],"review_sha256":old["result"]["review_sha256"]}
        path="/demo/api/imports/"+d["id"]
        self.assertEqual(self.http(path+"/confirm",stale)[0],409)
        self.assertEqual(self.http(path+"/confirm",{**self.confirmation(ready),"review_sha256":"0"*64})[0],409)
        self.assertEqual(self.http(path+"/check",{"expected_revision":1,"mapping":p["mapping"]})[0],409)
        self.assertEqual(self.http(path+"/confirm",self.confirmation(ready))[0],200)
        self.assertEqual(self.http(path+"/check",{"expected_revision":2,"mapping":p["mapping"]})[0],409)

    def test_import_04_upload_and_confirmation_idempotency(self):
        p=adapter.fixture();key=str(uuid.uuid4());a=self.upload(p,key);b=self.upload(p,key)
        self.assertEqual(a["id"],b["id"])
        d=self.check(a,p["mapping"]);path="/demo/api/imports/"+d["id"]+"/confirm";body=self.confirmation(d)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies=list(pool.map(lambda _:self.http(path,body),range(4)))
        self.assertEqual({r[0] for r in replies},{200})
        self.assertEqual(len({r[1]["dataset_id"] for r in replies}),1)

    def test_import_05_capability_and_model_permission_enforced(self):
        d=self.publish(adapter.fixture(refunds=False));base={**web.QUERY,"dataset_id":d["dataset_id"]}
        self.assertEqual(self.http("/demo/api/tasks",base,{"Idempotency-Key":str(uuid.uuid4())})[0],422)
        for kind in ("agent",):
            with self.assertRaises(ApiFailure) as exc:
                self.user.call("/api/workspaces/demo-shop/tasks",{"kind":kind,"dataset_id":d["dataset_id"],"question":"test"},headers={"Idempotency-Key":str(uuid.uuid4())})
            self.assertEqual(exc.exception.status,403)
        task=self.create({k:v for k,v in {**base,"metric":"paid_amount"}.items() if k!="as_of"})
        run_once(self.worker);self.assertEqual(self.get(task)["result"]["value"],28000)
        full=self.publish()
        product={"dataset_id":full["dataset_id"],"metric":"product_refund_amount","product_id":"P1","start":"2026-10-12T00:00:00Z","end":"2026-10-19T00:00:00Z"}
        self.assertEqual(self.http("/demo/api/tasks",product,{"Idempotency-Key":str(uuid.uuid4())})[0],422)

    def test_import_06_archive_preserves_queued_snapshot_and_restart(self):
        d=self.publish();task=self.create({**web.QUERY,"dataset_id":d["dataset_id"]})
        self.assertEqual(self.http("/demo/api/imports/"+d["id"]+"/archive",{"expected_revision":d["revision"]})[0],200)
        run_once(self.worker);saved=self.get(task);self.assertEqual(saved["result"]["value"],23000)
        self.assertEqual(self.http("/demo/api/tasks",{**web.QUERY,"dataset_id":d["dataset_id"]},{"Idempotency-Key":str(uuid.uuid4())})[0],409)
        self.stop();self.start();self.assertEqual(self.get(task),saved)
        restored=self.http("/demo/api/imports/"+d["id"])[1];self.assertEqual(restored["state"],"ARCHIVED")
        self.assertEqual(restored["dataset"]["source_sha256"],saved["source_sha256"])

    def test_import_07_input_and_workspace_boundaries(self):
        p=adapter.fixture();body={"name":"test","source_declaration":"public_or_synthetic","files":p["files"]};headers={"Idempotency-Key":str(uuid.uuid4())}
        self.assertEqual(self.http("/demo/api/imports",{**body,"source_declaration":"private"},headers)[0],400)
        self.assertEqual(self.http("/demo/api/imports",body,{**headers,"Origin":"https://attacker.invalid"})[0],403)
        bad=copy.deepcopy(body);bad["files"]["orders"]["name"]="../../orders.csv"
        self.assertEqual(self.http("/demo/api/imports",bad,headers)[0],400)
        bad=copy.deepcopy(body);bad["files"]["orders"]["content_base64"]=base64.b64encode(b"x"*131073).decode()
        self.assertEqual(self.http("/demo/api/imports",bad,headers)[0],413)
        self.assertEqual(self.http("/demo/api/imports",b"x"*768001,headers)[0],413)
        with self.assertRaises(ApiFailure) as exc:self.user.call("/api/workspaces/other/imports",body,headers=headers)
        self.assertEqual(exc.exception.status,403)

    def test_import_08_offline_langgraph_on_confirmed_version(self):
        d=self.publish(allowed=True)
        t=self.user.call("/api/workspaces/demo-shop/tasks",{"kind":"agent","dataset_id":d["dataset_id"],"question":"按 UTC，计算 2026-10-05 至 10-12 订单批次，截至 2026-10-19 的净额。"},headers={"Idempotency-Key":str(uuid.uuid4())});self.ids.append(t["id"])
        def offline(job,snapshot):return command(job,snapshot)+["--script",str(ROOT/"experiments/single-agent/scripts/cohort.json")]
        self.assertEqual(run_once(self.worker,command_factory=offline)["outcome"],"succeeded")
        r=self.get(t)["result"];self.assertEqual(r["evidence"][0]["result"]["value"],23000)
        self.assertEqual(r["usage"]["real_model_calls"],0);self.assertIn("dataset_policy_sha256",r["provenance"])

    def test_import_09_stale_cancelled_check_cannot_publish(self):
        d=self.upload(finish=False);claim=self.worker.call("/internal/tasks/claim",{})
        self.http("/demo/api/imports/"+d["id"]+"/archive",{"expected_revision":0})
        with self.assertRaises(ApiFailure) as exc:self.worker.call("/internal/tasks/"+claim["id"]+"/heartbeat",{"lease_token":claim["lease_token"]})
        self.assertEqual(exc.exception.status,409)

    def test_import_10_new_source_keeps_old_report_unchanged(self):
        first=self.publish();old=self.query(first)
        p=adapter.fixture()
        adapter.change(p,"orders",lambda rows:rows[0].update(paid_minor="11000"))
        adapter.change(p,"items",lambda rows:rows[0].update(line_minor="7000"))
        second=self.publish(p);new=self.query(second)
        self.assertEqual(new["result"]["value"],24000)
        self.assertNotEqual(old["source_sha256"],new["source_sha256"])
        self.assertEqual(self.get(old),old)
        self.assertEqual(self.query(first)["result"]["value"],23000)

    def test_import_11_agent_tool_cannot_bypass_dataset_capabilities(self):
        from commerce_agent.models import ScriptedModel
        from commerce_agent.runtime import execute_agent
        d=self.publish(adapter.fixture(refunds=False),allowed=True)
        t=self.user.call("/api/workspaces/demo-shop/tasks",{"kind":"agent","dataset_id":d["dataset_id"],"question":"计算退款金额"},headers={"Idempotency-Key":str(uuid.uuid4())});self.ids.append(t["id"])
        claim=self.worker.call("/internal/tasks/claim",{})
        self.assertEqual(claim["id"],t["id"])
        snapshot=self.runtime/(t["id"]+".json");snapshot.write_bytes(base64.b64decode(claim["dataset_base64"]))
        script=[{"tool_calls":[{"name":"query_metric","args":{"metric":"refund_amount","start":"2026-10-05T00:00:00Z","end":"2026-10-12T00:00:00Z"}}]},
            {"tool_calls":[{"name":"AnalysisDecision","args":{"outcome":"unsupported","issue":"missing_data","evidence_ids":[],"comparisons":[]}}]}]
        root=self.runtime/"agent-policy-test"
        report=execute_agent(claim,snapshot,ScriptedModel(script=script),state_root=root)
        self.assertEqual(report["outcome"],"unsupported");self.assertEqual(report["evidence"],[])
        self.assertIn("dataset_metric_unavailable",(root/t["id"]/"journal.json").read_text())
        self.assertEqual(report["usage"]["real_model_calls"],0)
        self.worker.call("/internal/tasks/"+claim["id"]+"/complete",{"lease_token":claim["lease_token"],"result":report})

    def test_import_12_check_result_bound_to_exact_input_and_revision(self):
        from commerce_import.adapter import evaluate
        d=self.upload(finish=False);claim=self.worker.call("/internal/tasks/claim",{})
        source=json.loads(base64.b64decode(claim["dataset_base64"]))
        report=evaluate(source,task_id=claim["id"],input_sha256=claim["source_sha256"])
        path="/internal/tasks/"+claim["id"]+"/complete"
        for change in ({"revision":9},{"input_sha256":"0"*64},{"mapping_sha256":"0"*64},{"draft_id":str(uuid.uuid4())}):
            with self.assertRaises(ApiFailure) as exc:self.worker.call(path,{"lease_token":claim["lease_token"],"result":{**report,**change}})
            self.assertEqual(exc.exception.status,422)
        self.worker.call(path,{"lease_token":claim["lease_token"],"result":report})
        self.assertEqual(self.finish(d)["state"],"PROFILED")

    def test_import_13_parser_process_does_not_inherit_personal_model_key(self):
        d=self.upload(finish=False)
        def assert_environment(job,snapshot):
            args=command(job,snapshot)
            probe='import os,sys; assert "COMMERCE_PERSONAL_DEEPSEEK_API_KEY" not in os.environ; os.execv(sys.argv[1],sys.argv[1:])'
            return [sys.executable,"-c",probe,*args]
        with patch.dict(os.environ,{"COMMERCE_PERSONAL_DEEPSEEK_API_KEY":"nonsecret-test-sentinel"}):
            self.assertEqual(run_once(self.worker,command_factory=assert_environment)["outcome"],"succeeded")
        self.assertEqual(self.finish(d)["state"],"PROFILED")


if __name__=="__main__":
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(adapter.ImportAdapterTests),
        unittest.TestSuite(ImportHttpTests(name) for name in sorted(ImportHttpTests.__dict__) if name.startswith("test_import_"))])
    start=time.monotonic();result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={"date":"2026-11-06","kind":"external_data_import_engineering","tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
        "seconds":round(time.monotonic()-start,3),"real_model_calls":0,
        "failure_details":[{"test":str(test),"traceback":detail} for test,detail in result.failures+result.errors]}
    target=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"docs/probes/data-import-2026-11-06.json"
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n");raise SystemExit(not result.wasSuccessful())
