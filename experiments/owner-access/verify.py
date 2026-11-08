"""Actual Spring Security session/CSRF tests with ephemeral synthetic credentials."""
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.java_local import prepare,environment,server_command
from scripts.prepare_owner import password_hash
from commerce_worker.worker import Client,run_once,NoRedirect

QUERY={"dataset_id":"demo-ledger-v1","metric":"paid_amount","start":"2026-10-05T00:00:00Z","end":"2026-10-12T00:00:00Z"}


class OwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password=secrets.token_urlsafe(24);cls.encoded=password_hash(cls.password)

    def setUp(self):
        parent=ROOT/".local/owner-access/verification";parent.mkdir(parents=True,exist_ok=True)
        self.runtime=prepare(Path(tempfile.mkdtemp(dir=parent)))
        path=self.runtime/"owner.hash";path.write_text(self.encoded+"\n");path.chmod(0o600)
        with socket.socket() as s:s.bind(("127.0.0.1",0));self.port=s.getsockname()[1]
        self.url=f"http://127.0.0.1:{self.port}";self.cookies=http.cookiejar.CookieJar()
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(self.cookies),NoRedirect())
        self.worker=Client(self.url,(self.runtime/"worker.token").read_text())
        self.user=Client(self.url,(self.runtime/"user.token").read_text())
        self.log=(self.runtime/"java.log").open("ab");self.start()

    def start(self,extra=(),valid=True):
        settings={"commerce.demo.enabled":"true","commerce.owner.enabled":"true","commerce.owner.origin":self.url,"commerce.owner.login-per-minute":"4"}
        settings.update(dict(arg[2:].split("=",1) for arg in extra))
        cmd=server_command(self.runtime,self.port)+["--"+k+"="+v for k,v in settings.items()]
        self.server=subprocess.Popen(cmd,cwd=ROOT,env=environment(),stdout=self.log,stderr=self.log)
        for _ in range(350):
            if self.server.poll() is not None:
                if not valid:return
                raise RuntimeError("owner startup failed; "+str(self.runtime/"java.log"))
            try:
                if self.user.call("/health",method="GET")["status"]=="ok":
                    if not valid:self.fail("invalid owner configuration started")
                    return
            except OSError:pass
            time.sleep(.1)
        raise RuntimeError("owner startup timeout")

    def stop(self):
        if self.server.poll() is None:self.server.terminate();self.server.wait(timeout=10)
    def tearDown(self):self.stop();self.log.close()

    def http(self,path,body=None,headers=None,form=False):
        h={"Origin":self.url,"Sec-Fetch-Site":"same-origin","X-Commerce-Demo":"1",**(headers or {})}
        h={k:v for k,v in h.items() if v is not None}
        if body is not None:
            if form:raw=urllib.parse.urlencode(body).encode();h["Content-Type"]="application/x-www-form-urlencoded"
            else:raw=json.dumps(body).encode();h["Content-Type"]="application/json"
        else:raw=None
        request=urllib.request.Request(self.url+path,data=raw,headers=h)
        try:response=self.opener.open(request,timeout=5)
        except urllib.error.HTTPError as e:response=e
        with response:
            raw=response.read(2_000_001);typ=response.headers.get("Content-Type","")
            value=json.loads(raw) if "application/json" in typ and raw else raw.decode(errors="replace")
            return response.status,value,response.headers

    def csrf(self):
        code,value,_=self.http("/session/csrf");self.assertEqual(code,200);return value

    def login(self):
        csrf=self.csrf();before=next(c.value for c in self.cookies if c.name=="JSESSIONID")
        code,_,headers=self.http("/login",{"username":"owner","password":self.password,csrf["parameter_name"]:csrf["token"]},form=True)
        self.assertEqual(code,302);self.assertEqual(headers.get("Location"),"/demo/index.html")
        after=next(c.value for c in self.cookies if c.name=="JSESSIONID");self.assertTrue(before!=after,"session must rotate")
        code,value,_=self.http("/demo/api/config");self.assertEqual(code,200);self.assertTrue(value["owner_mode"])
        return {value["csrf_header"]:value["csrf_token"]}

    def test_anonymous_rejected_and_login_page_available(self):
        self.assertEqual(self.http("/demo/api/config")[0],401)
        self.assertEqual(self.http("/demo/index.html")[0],302)
        self.assertEqual(self.http("/demo/app.js")[0],302)
        code,html,headers=self.http("/login");self.assertEqual(code,200);self.assertIn('name="password"',html)
        self.assertIn("frame-ancestors 'none'",headers["Content-Security-Policy"])
        self.assertEqual(self.http("/internal/tasks/claim",{})[0],401)

    def test_login_rotation_csrf_real_task_and_role_isolation(self):
        headers=self.login()
        self.assertEqual(self.http("/demo/api/tasks",QUERY,{"Idempotency-Key":str(uuid.uuid4())})[0],403)
        self.assertEqual(self.http("/demo/api/tasks",QUERY,{"X-CSRF-TOKEN":"wrong","Idempotency-Key":str(uuid.uuid4())})[0],403)
        code,t,_=self.http("/demo/api/tasks",QUERY,{**headers,"Idempotency-Key":str(uuid.uuid4())});self.assertEqual(code,202)
        self.assertEqual(run_once(self.worker)["outcome"],"succeeded")
        self.assertEqual(self.http("/demo/api/tasks/"+t["id"])[1]["result"]["value"],28000)
        self.assertEqual(self.http("/internal/tasks/claim",{},headers)[0],401)
        self.assertEqual(self.http("/api/workspaces/demo-shop/tasks")[0],401)
        self.assertEqual(self.http("/demo/api/imports",{"name":"missing csrf"})[0],403)
        self.assertEqual(self.http("/demo/api/system")[0],200)

    def test_logout_requires_csrf_and_invalidates_session(self):
        headers=self.login()
        self.assertEqual(self.http("/logout",{},form=True)[0],403)
        self.assertEqual(self.http("/demo/api/config")[0],200)
        self.assertEqual(self.http("/logout",{},headers,form=True)[0],302)
        self.assertEqual(self.http("/demo/api/config")[0],401)
        self.assertEqual(self.http("/demo/api/tasks",QUERY,{**headers,"Idempotency-Key":str(uuid.uuid4())})[0],401)

    def test_restart_invalidates_old_session_but_preserves_task(self):
        headers=self.login();code,t,_=self.http("/demo/api/tasks",QUERY,{**headers,"Idempotency-Key":str(uuid.uuid4())});self.assertEqual(code,202)
        self.stop();self.start();self.assertEqual(self.http("/demo/api/config")[0],401)
        self.login();self.assertEqual(self.http("/demo/api/tasks/"+t["id"])[0],200)

    def test_wrong_password_and_bounded_login_window(self):
        csrf=self.csrf()
        for _ in range(4):
            code,_,headers=self.http("/login",{"username":"owner","password":"wrong",csrf["parameter_name"]:csrf["token"]},form=True)
            self.assertEqual(code,302);self.assertEqual(headers.get("Location"),"/login?error")
        code,_,headers=self.http("/login",{"username":"owner","password":"wrong",csrf["parameter_name"]:csrf["token"]},form=True)
        self.assertEqual(code,429);self.assertEqual(headers.get("Retry-After"),"60")
        self.assertEqual(self.http("/demo/api/config")[0],401)

    def test_host_origin_and_forwarded_identity_not_trusted(self):
        headers=self.login()
        self.assertEqual(self.http("/demo/api/config",headers={"Host":"foreign.example"})[0],403)
        self.assertEqual(self.http("/demo/api/tasks",QUERY,{**headers,"Idempotency-Key":str(uuid.uuid4()),"Origin":"https://foreign.example"})[0],403)
        self.assertEqual(self.http("/login",{}, {"Origin":"https://foreign.example","X-Forwarded-Host":"127.0.0.1"},form=True)[0],403)
        bearer="Bearer "+(self.runtime/"worker.token").read_text().strip()
        self.assertEqual(self.http("/internal/tasks/claim",{}, {"Authorization":bearer,"Host":"foreign.example","X-Forwarded-For":"127.0.0.1"})[0],403)
        self.assertEqual(self.http("/demo/api/config",headers={"Origin":"https://foreign.example"})[0],403)

    def test_login_requires_csrf_and_cookie_flags(self):
        code,_,headers=self.http("/session/csrf");self.assertEqual(code,200)
        cookie=headers.get("Set-Cookie","")
        self.assertIn("HttpOnly",cookie);self.assertIn("SameSite=Strict",cookie)
        self.assertEqual(self.http("/login",{"username":"owner","password":self.password},form=True)[0],403)
        self.assertEqual(self.http("/demo/api/config")[0],401)

    def test_bad_configuration_fails_closed_and_https_requires_secure_cookie(self):
        self.stop();self.start(["--commerce.owner.origin=https://personal.example"],valid=False)
        self.start(["--commerce.owner.origin=https://personal.example","--server.servlet.session.cookie.secure=true"])
        code,_,headers=self.http("/session/csrf",headers={"Host":"personal.example","Origin":"https://personal.example"})
        self.assertEqual(code,200);self.assertIn("Secure",headers.get("Set-Cookie",""))
        self.stop();(self.runtime/"owner.hash").write_text("invalid\n")
        self.start(valid=False)
        (self.runtime/"owner.hash").unlink();self.start(valid=False)


if __name__=="__main__":
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(OwnerTests);start=time.monotonic();r=unittest.TextTestRunner(verbosity=2).run(suite)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    report={"date":datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),"kind":"spring_security_owner_acceptance","tests":r.testsRun,"failures":len(r.failures),"errors":len(r.errors),"seconds":round(time.monotonic()-start,3),"real_model_calls":0,"credential_kind":"ephemeral_generated_synthetic","tls_end_to_end":False,"failure_details":[{"test":str(t),"trace":s} for t,s in r.failures+r.errors]}
    Path(sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');raise SystemExit(not r.wasSuccessful())
