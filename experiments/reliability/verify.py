"""Resource-control counterexamples and acceptance with real Java/Worker. No model calls."""
import concurrent.futures
import copy
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from commerce_worker.worker import Client, ApiFailure, run_once, command
from scripts.java_local import prepare, environment, server_command, java
from commerce_data.sales_change import percent
from commerce_agent.report import comparison

QUERY = {"dataset_id":"demo-ledger-v1", "metric":"paid_amount", "start":"2026-10-05T00:00:00Z", "end":"2026-10-12T00:00:00Z"}


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/".local/reliability/runs";parent.mkdir(parents=True,exist_ok=True)
        self.runtime=prepare(Path(tempfile.mkdtemp(dir=parent)))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0));self.port=sock.getsockname()[1]
        self.url=f"http://127.0.0.1:{self.port}"
        self.user=Client(self.url,(self.runtime/"user.token").read_text())
        self.worker=Client(self.url,(self.runtime/"worker.token").read_text())
        self.log=(self.runtime/"server.log").open("ab")
        self.lifetime=2000 if "deadline" in self._testMethodName else 15000
        self.start()

    def start(self):
        command=server_command(self.runtime,self.port,1500,100,getattr(self,"snapshot",None))+[
            "--commerce.limits.total=5","--commerce.limits.active=3","--commerce.limits.running=1",
            f"--commerce.limits.lifetime-ms={self.lifetime}","--commerce.expire-scan-ms=100"]
        self.server=subprocess.Popen(command,cwd=ROOT,env=environment(),stdout=self.log,stderr=self.log)
        for _ in range(450):
            if self.server.poll() is not None:raise RuntimeError("server_failed: "+str(self.runtime/"server.log"))
            try:
                if self.user.call("/health",method="GET")["status"]=="ok":return
            except OSError:pass
            time.sleep(.1)
        raise RuntimeError("startup_timeout")

    def stop(self):
        if self.server.poll() is None:self.server.terminate();self.server.wait(timeout=10)

    def tearDown(self):
        self.stop();self.log.close()

    def create(self,key=None,query=None):
        return self.user.call("/api/workspaces/demo-shop/tasks",query or QUERY,headers={"Idempotency-Key":key or str(uuid.uuid4())})

    def get(self,t):return self.user.call("/api/workspaces/demo-shop/tasks/"+t["id"],method="GET")
    def cancel(self,t):return self.user.call("/api/workspaces/demo-shop/tasks/"+t["id"]+"/cancel",{})
    def stats(self):return self.user.call("/api/workspaces/demo-shop/system",method="GET")
    def error(self,status,fn,*args,**kwargs):
        with self.assertRaises(ApiFailure) as caught:fn(*args,**kwargs)
        self.assertEqual(caught.exception.status,status)

    def test_concurrent_admission_and_idempotent_replay(self):
        key=str(uuid.uuid4());first=self.create(key)
        def attempt(_):
            try:return self.create()
            except ApiFailure as e:return e.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as p:out=list(p.map(attempt,range(10)))
        self.assertEqual(sum(isinstance(v,dict) for v in out),2,out)
        self.assertTrue(all(isinstance(v,dict) or v==429 for v in out))
        self.assertEqual(self.create(key)["id"],first["id"])
        self.error(409,self.create,key,{**QUERY,"metric":"paid_order_count"})
        self.cancel(first);self.create()
        self.assertEqual(self.stats()["counts"]["QUEUED"],3)

    def test_running_cap_and_reclaim(self):
        self.create();self.create()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as p:
            claims=[c for c in p.map(lambda _:self.worker.call("/internal/tasks/claim",{}),range(6)) if c]
        self.assertEqual(len(claims),1);first=claims[0]
        self.assertTrue(self.worker.call("/internal/tasks/claim",{}) is None)
        time.sleep(1.6)
        new=self.worker.call("/internal/tasks/claim",{})
        self.assertEqual(new["id"],first["id"]);self.assertEqual(new["attempt"],2)
        self.error(409,self.worker.call,"/internal/tasks/"+first["id"]+"/heartbeat",{"lease_token":first["lease_token"]})
        self.cancel(new);self.assertIsNotNone(self.worker.call("/internal/tasks/claim",{}))

    def test_deadline_survives_heartbeats_and_restart(self):
        t=self.create();old=self.worker.call("/internal/tasks/claim",{})
        deadline=t.get("expires_at",int(time.time()*1000)+2000)
        self.stop();self.start()
        while int(time.time()*1000)<deadline+200:
            try:self.worker.call("/internal/tasks/"+old["id"]+"/heartbeat",{"lease_token":old["lease_token"]})
            except ApiFailure as e:self.assertEqual(e.status,409)
            time.sleep(.15)
        saved=self.get(t);self.assertEqual(saved["status"],"FAILED",saved)
        self.assertEqual(saved["error_code"],"task_deadline_exceeded")
        self.assertEqual(saved["expires_at"],deadline)
        self.assertEqual(sum(e["kind"]=="DEADLINE_EXCEEDED" for e in saved["events"]),1)
        self.error(409,self.worker.call,"/internal/tasks/"+old["id"]+"/heartbeat",{"lease_token":old["lease_token"]})
        self.error(409,self.worker.call,"/internal/tasks/"+old["id"]+"/complete",{"lease_token":old["lease_token"],"result":{}})

    def test_deadline_without_worker(self):
        t=self.create();time.sleep(2.2)
        self.assertEqual(self.get(t)["status"],"FAILED")
        self.assertIsNone(self.worker.call("/internal/tasks/claim",{}))

    def test_deadline_continuous_heartbeats_cannot_extend(self):
        t=self.create();owned=self.worker.call("/internal/tasks/claim",{});accepted=0
        end=time.monotonic()+2.3
        while time.monotonic()<end:
            try:
                self.worker.call("/internal/tasks/"+owned["id"]+"/heartbeat",{"lease_token":owned["lease_token"]});accepted+=1
            except ApiFailure as e:self.assertEqual(e.status,409)
            time.sleep(.15)
        self.assertGreater(accepted,3)
        self.assertEqual(self.get(t)["status"],"FAILED")
        self.assertEqual(self.get(t)["expires_at"],t["expires_at"])

    def test_import_admission_rolls_back_upload(self):
        pending=[self.create() for _ in range(3)]
        spec=importlib.util.spec_from_file_location("import_fixture",ROOT/"tests/test_import_adapter.py")
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        body={"name":"quota test","source_declaration":"public_or_synthetic","files":fixture.fixture()["files"]}
        key=str(uuid.uuid4());path="/api/workspaces/demo-shop/imports"
        self.error(429,self.user.call,path,body,headers={"Idempotency-Key":key})
        self.assertEqual(self.user.call(path,method="GET")["items"],[])
        self.cancel(pending[0]);draft=self.user.call(path,body,headers={"Idempotency-Key":key})
        self.assertEqual(draft["state"],"CHECKING")
        self.assertEqual(self.user.call(path,body,headers={"Idempotency-Key":key})["id"],draft["id"])

    def test_retention_quota_and_protected_observation(self):
        for _ in range(5):self.cancel(self.create())
        self.error(429,self.create)
        info=self.stats();self.assertEqual(info["counts"]["CANCELLED"],5)
        self.assertEqual(info["limits"]["total"],5)
        self.assertNotIn("request",info);self.assertNotIn("token",json.dumps(info))
        self.error(401,self.worker.call,"/api/workspaces/demo-shop/system",method="GET")
        self.error(403,self.user.call,"/api/workspaces/other/system",method="GET")
        self.worker.call("/internal/tasks/claim",{})
        self.assertIsInstance(self.stats()["worker_last_poll_at"],int)

    def test_executor_output_stopped_before_timeout(self):
        t=self.create()
        def flood(job,path):return [sys.executable,"-c","import sys,time;sys.stdout.write('x'*2100000);sys.stdout.flush();time.sleep(10)"]
        started=time.monotonic();out=run_once(self.worker,timeout=4,command_factory=flood)
        self.assertEqual(out["outcome"],"executor_output_limit",out)
        self.assertLess(time.monotonic()-started,3)
        self.assertEqual(self.get(t)["status"],"FAILED")

    def test_negative_zero_real_java_sales_and_agent_publication(self):
        spec=importlib.util.spec_from_file_location("sales_fixture",ROOT/"tests/test_sales_change.py")
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        self.snapshot=self.runtime/"negative-zero.json"
        self.snapshot.write_text(json.dumps(fixture.dataset([(QUERY["start"],[("P1",1000000)]),(QUERY["end"],[("P1",999999)])])))
        self.stop();self.start()
        t=self.create(query={"dataset_id":"demo-ledger-v1","metric":"sales_change_by_product",**{k:v for k,v in fixture.PARAMS.items() if k!="workspace_id"},"top_n":5})
        self.assertEqual(run_once(self.worker)["outcome"],"succeeded")
        self.assertEqual(self.get(t)["result"]["totals"]["change_percent"],"0.00")
        t=self.create(query={"kind":"agent","dataset_id":"demo-ledger-v1","question":"比较两期支付金额"})
        q={k:v for k,v in QUERY.items() if k!="dataset_id"}
        script=[{"tool_calls":[{"name":"query_metric","args":q},{"name":"query_metric","args":{**q,"start":QUERY["end"],"end":"2026-10-19T00:00:00Z"}}]},
            {"tool_calls":[{"name":"AnalysisDecision","args":{"outcome":"answered","issue":"none","evidence_ids":["$e0","$e1"],"comparisons":[{"baseline_id":"$e0","current_id":"$e1"}]}}]}]
        script_path=self.runtime/"script.json";script_path.write_text(json.dumps(script))
        self.assertEqual(run_once(self.worker,command_factory=lambda j,p:command(j,p)+["--script",str(script_path)])["outcome"],"succeeded")
        self.assertEqual(self.get(t)["result"]["comparisons"][0]["change_percent"],"0.00")

    def test_legacy_schema_migration_preserves_task_and_data(self):
        t=self.create();self.stop()
        # Reconstruct the exact pre-module columns in this isolated test DB only.
        with zipfile.ZipFile(ROOT/"services/business-api/target/business-api-0.1.0.jar") as jar:
            name=next(n for n in jar.namelist() if n.startswith("BOOT-INF/lib/h2-"))
            driver=self.runtime/"h2.jar";driver.write_bytes(jar.read(name))
        sql=self.runtime/"legacy.sql";sql.write_text("DROP INDEX task_expiry; ALTER TABLE analysis_task DROP COLUMN expires_at; ALTER TABLE dataset_import_guard DROP COLUMN worker_last_poll_at;")
        subprocess.run([java(),"-cp",str(driver),"org.h2.tools.RunScript","-url","jdbc:h2:file:"+str(self.runtime/"tasks"),"-user","sa","-script",str(sql)],cwd=ROOT,env=environment(),stdout=self.log,stderr=self.log,check=True)
        self.start();saved=self.get(t)
        for key in ("id","request","source_sha256","created_at"):self.assertEqual(saved[key],t[key])
        self.assertEqual(saved["expires_at"],t["created_at"]+900000)
        self.assertEqual(run_once(self.worker)["outcome"],"succeeded")
        self.assertEqual(self.get(t)["result"]["value"],28000)


class NumericTests(unittest.TestCase):
    def test_sales_negative_zero(self):self.assertEqual(percent(-1,1000000),"0.00")
    def test_comparison_negative_zero(self):
        raw=json.loads((ROOT/"docs/probes/ledger-cohort-2026-11-06.json").read_text())
        raw.update(metric="paid_amount",value=1000000)
        raw["period"].update(start=QUERY["start"],end=QUERY["end"],as_of_exclusive=None)
        a={"evidence_id":"a","query":{},"result":raw};b=copy.deepcopy(a);b["evidence_id"]="b"
        b["result"].update(value=999999);b["result"]["period"].update(start=QUERY["end"],end="2026-10-19T00:00:00Z")
        self.assertEqual(comparison(a,b)["change_percent"],"0.00")


if __name__=="__main__":
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (NumericTests,ReliabilityTests)])
    start=time.monotonic();r=unittest.TextTestRunner(verbosity=2).run(suite)
    report={"date":"2026-11-06","kind":"reliability_controls","tests":r.testsRun,"failures":len(r.failures),"errors":len(r.errors),"seconds":round(time.monotonic()-start,3),"real_model_calls":0,"test_limits":{"total":5,"active":3,"running":1,"lifetime_ms":[2000,15000]},"failure_details":[{"test":str(t),"trace":s} for t,s in r.failures+r.errors]}
    Path(sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    raise SystemExit(not r.wasSuccessful())
