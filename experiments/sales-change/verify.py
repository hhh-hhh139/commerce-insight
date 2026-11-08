"""Real Java/HTTP/Worker/DuckDB/LangGraph acceptance; scripted model, zero paid calls."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
imports=module("import_acceptance",ROOT/"experiments/data-import/verify.py")
unit=module("sales_unit",ROOT/"tests/test_sales_change.py")
agent=module("agent_acceptance",ROOT/"experiments/single-agent/verify.py")
from commerce_agent.models import ScriptedModel
from commerce_agent.runtime import execute_agent
from commerce_agent.storage import AgentError
from commerce_worker.worker import ApiFailure,command,run_once,child_environment

QUERY={"dataset_id":"demo-ledger-v1","metric":"sales_change_by_product",**{k:v for k,v in unit.PARAMS.items() if k!="workspace_id"},"top_n":5}
ARGS={k:v for k,v in QUERY.items() if k not in ("dataset_id","metric")}
SCRIPT=[agent.turn(agent.tool("query_sales_change",ARGS)),agent.decision(["$e0"])]


class SalesHttpTests(imports.ImportHttpTests):
    def finish_analysis(self,task):
        for _ in range(60):
            result=self.get(task)
            if result["status"] not in ("QUEUED","RUNNING"):return result
            run_once(self.worker)
        self.fail("analysis did not finish")

    def test_sales_01_full_task_and_idempotent_request(self):
        key=str(uuid.uuid4());a=self.create(QUERY,key);b=self.create(QUERY,key)
        self.assertEqual(a["id"],b["id"])
        task=self.finish_analysis(a);self.assertEqual(task["status"],"SUCCEEDED",task)
        r=task["result"];self.assertEqual(r["totals"]["delta_minor"],-13000)
        self.assertEqual(r["decreases"]["products"][0]["product_id"],"P3")
        self.assertEqual(r["evidence"]["source_sha256"],task["source_sha256"])
        small=self.finish_analysis(self.create({**QUERY,"top_n":1}))["result"]
        self.assertEqual(small["decreases"]["other"]["delta_minor"],-4000)

    def test_sales_02_two_confirmed_layouts_and_no_refund_capability(self):
        for layout,refunds in [("standard-minor",False),("renamed-major",True)]:
            d=self.publish(imports.adapter.fixture(layout,refunds=refunds));before=copy.deepcopy(d["dataset"])
            result=self.finish_analysis(self.create({**QUERY,"dataset_id":d["dataset_id"]}))
            self.assertEqual(result["status"],"SUCCEEDED",result)
            self.assertEqual(result["result"]["totals"]["delta_minor"],-13000)
            self.assertEqual(result["dataset"],before)
            with self.assertRaises(ApiFailure) as exc:self.user.call("/api/workspaces/demo-shop/tasks",{"kind":"agent","dataset_id":d["dataset_id"],"question":"test"},headers={"Idempotency-Key":str(uuid.uuid4())})
            self.assertEqual(exc.exception.status,403)

    def test_sales_03_request_and_workspace_boundaries(self):
        for change in ({"top_n":0},{"top_n":True},{"product_id":"P1"},{"as_of":QUERY["current_end"]},{"current_end":"2026-10-20T00:00:00Z"},{"current_start":"2026-10-11T00:00:00Z"},{"metric":"price_volume_cause"}):
            self.assertEqual(self.http("/demo/api/tasks",{**QUERY,**change},{"Idempotency-Key":str(uuid.uuid4())})[0],400)
        with self.assertRaises(ApiFailure) as exc:self.user.call("/api/workspaces/other/tasks",QUERY,headers={"Idempotency-Key":str(uuid.uuid4())})
        self.assertEqual(exc.exception.status,403)
        d=self.upload();self.assertEqual(self.http("/demo/api/tasks",{**QUERY,"dataset_id":"ds-"+d["id"]+"-r1"},{"Idempotency-Key":str(uuid.uuid4())})[0],409)

    def test_sales_04_publication_rejects_binding_and_arithmetic_tampering(self):
        t=self.create(QUERY);job=self.worker.call("/internal/tasks/claim",{});self.assertEqual(t["id"],job["id"])
        path=self.runtime/"tamper-source.json";path.write_bytes(base64.b64decode(job["dataset_base64"]))
        result=json.loads(subprocess.check_output(command(job,path),cwd=ROOT,env=child_environment()))
        mutations=[lambda r:r["evidence"].update(source_sha256="0"*64),lambda r:r["period"].update(current_end=QUERY["end"]),
            lambda r:r["totals"].update(delta_minor=-12000),lambda r:r["decreases"]["other"].update(delta_minor=-1),
            lambda r:r["decreases"]["products"][0].update(direction_share_percent="80.00"),
            lambda r:r["reconciliation"].update(baseline_residual_minor=1),lambda r:r["queries"][0]["params"].__setitem__(0,"wrong")]
        for mutate in mutations:
            bad=copy.deepcopy(result);mutate(bad)
            with self.assertRaises(ApiFailure) as exc:self.worker.call("/internal/tasks/"+job["id"]+"/complete",{"lease_token":job["lease_token"],"result":bad})
            self.assertEqual(exc.exception.status,422)
        self.worker.call("/internal/tasks/"+job["id"]+"/complete",{"lease_token":job["lease_token"],"result":result})
        self.assertEqual(self.get(t)["status"],"SUCCEEDED")

    def test_sales_05_confirmed_agent_uses_same_evidence_and_no_model_prose(self):
        d=self.publish(allowed=True)
        t=self.user.call("/api/workspaces/demo-shop/tasks",{"kind":"agent","dataset_id":d["dataset_id"],"question":"按 UTC 比较 2026-10-05 至 10-12 与 10-12 至 10-19 的已支付金额，不扣退款，变化主要来自哪些商品？"},headers={"Idempotency-Key":str(uuid.uuid4())});self.ids.append(t["id"])
        script=self.runtime/"sales-script.json";script.write_text(json.dumps(SCRIPT,ensure_ascii=False))
        def scripted(job,snapshot):return command(job,snapshot)+["--script",str(script)]
        outcome=run_once(self.worker,command_factory=scripted);self.assertEqual(outcome["outcome"],"succeeded",outcome)
        r=self.get(t)["result"];self.assertEqual(r["usage"]["tool_attempts"],1);self.assertEqual(r["usage"]["real_model_calls"],0)
        self.assertEqual(r["evidence"][0]["result"]["totals"]["delta_minor"],-13000)
        self.assertIn("71.43%",r["answer"]);self.assertIn("dataset_policy_sha256",r["provenance"])

    def test_sales_06_restart_archive_and_cancel(self):
        d=self.publish();t=self.create({**QUERY,"dataset_id":d["dataset_id"]})
        self.http("/demo/api/imports/"+d["id"]+"/archive",{"expected_revision":d["revision"]})
        saved=self.finish_analysis(t);self.assertEqual(saved["status"],"SUCCEEDED")
        self.stop();self.start();self.assertEqual(self.get(t),saved)
        cancelled=self.create(QUERY);self.http("/demo/api/tasks/"+cancelled["id"]+"/cancel",{})
        self.assertEqual(self.get(cancelled)["status"],"CANCELLED");self.assertIsNone(self.get(cancelled)["result"])


class SalesAgentTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/".local/sales-change/graph";parent.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(dir=parent))

    def execute(self,script,job=None):
        return execute_agent(job or agent.job(),agent.SNAPSHOT,ScriptedModel(script=script),state_root=self.root)

    def test_graph_error_then_correction_and_fabricated_text_ignored(self):
        bad=agent.turn(agent.tool("query_sales_change",{**ARGS,"current_end":"2026-10-20T00:00:00Z"}))
        script=copy.deepcopy(SCRIPT);script[-1]["content"]="999999 元；已证明是涨价导致"
        r=self.execute([bad,*script]);self.assertEqual(r["usage"]["tool_attempts"],2)
        self.assertNotIn("999999",r["answer"]);self.assertNotIn("已证明",r["answer"])
        self.assertEqual(r["evidence"][0]["result"]["totals"]["delta_minor"],-13000)

    def test_fabricated_reference_or_double_comparison_rejected(self):
        for final in [agent.decision(["ev_fake"]),agent.decision(["$e0"],[{"baseline_id":"$e0","current_id":"$e0"}])]:
            with self.assertRaises(AgentError):self.execute([SCRIPT[0],final])

    def test_capability_guard_denies_new_tool(self):
        from commerce_agent.tools import make_tools
        from commerce_agent.storage import Journal
        from commerce_data.ledger import load_snapshot
        from commerce_data.ledger_db import build_database
        data,evidence=load_snapshot(agent.SNAPSHOT);database=build_database(data,self.root/"data.duckdb")
        tools=make_tools(data,database,evidence,Journal(self.root/"journal.json"),allowed_metrics=["paid_order_count"])
        inspect=next(t for t in tools if t.name=="inspect_dataset").invoke({});self.assertEqual(inspect["analyses"],{})
        response=next(t for t in tools if t.name=="query_sales_change").invoke(ARGS)
        self.assertEqual(response,{"error":"dataset_metric_unavailable","is_evidence":False})


if __name__=="__main__":
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(unit.SalesChangeTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(SalesAgentTests),
        unittest.TestSuite(SalesHttpTests(name) for name in sorted(SalesHttpTests.__dict__) if name.startswith("test_sales_"))])
    start=time.monotonic();result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={"date":"2026-11-06","kind":"sales_change_engineering_scripted_model","tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"seconds":round(time.monotonic()-start,3),"real_model_calls":0,
        "failure_details":[{"test":str(t),"traceback":detail} for t,detail in result.failures+result.errors]}
    target=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"docs/probes/sales-change-2026-11-06.json"
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');raise SystemExit(not result.wasSuccessful())
