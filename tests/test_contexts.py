"""Multi-context ordering, version semantics, joint checkpoints and real workers."""

import copy
import json
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import asdict,replace
from pathlib import Path

from apps.openpine.bootstrap import local_manager,local_services
from packages.compiler.semantic.specification import ROOT
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalData,HistoricalBar,OpenPineError
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.contracts.storage import ResourcePolicy,RunPlan
from packages.contracts.wire import read_json,canonical,loads,digest
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.execution.session import Session
from packages.marketdata import read_bars,data_identity
from packages.marketdata.providers import input_snapshot,make_snapshot
from packages.storage.backup import backup,restore
from tools.conformance import context_inputs,run_cases,CONTEXT_CASES
from test_jobs import wait
from test_checkpoints import paused

EXAMPLE=ROOT/"examples/contexts"


def request(manager,version=6):
    value=read_json(EXAMPLE/"inputs.json")
    primary=manager.market.save(input_snapshot(read_json(EXAMPLE/"bars.json")))
    secondary=manager.market.save(input_snapshot(read_json(EXAMPLE/"hourly.json")))
    ref=manager.freeze_input({"source":(EXAMPLE/f"v{version}.pine").read_text(encoding="utf-8"),"source_name":f"v{version}.pine"})
    return {"schema_version":"openpine.job-request.4","input_ref":ref,"snapshot_id":primary,"timeout_seconds":60,
        "primary_context":value["primary_context"],"contexts":[{"context":value["contexts"][0]["context"],"snapshot_id":secondary}]}


class ContextRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data=read_bars(EXAMPLE/"bars.json")
        cls.expected=read_json(EXAMPLE/"expected.json")

    def driver(self,program,inputs=None,data=None):
        inputs=inputs or context_inputs(EXAMPLE/"inputs.json");source=MemorySource(data or self.data)
        runtime=self.session.validate_program(program)
        contexts=self.session.context_scheduler(program,runtime,source,inputs["primary_context"],inputs["context_sources"])
        return HistoricalDriver(runtime.prepare(program),source,contexts)

    def test_independent_request_corpus_all_versions(self):
        report=run_cases(self.session,cases_path=CONTEXT_CASES)
        self.assertEqual(report["executed_programs"],32);self.assertEqual(report["expected_rejections"],6)

    def test_all_versions_resume_at_every_primary_boundary(self):
        cuts=0
        for version in range(1,7):
            program=self.session.compile((EXAMPLE/f"v{version}.pine").read_text(encoding="utf-8"),f"v{version}.pine")
            baseline=self.driver(program);baseline.run();expected=baseline.finish(data_identity(self.data))
            self.assertEqual([list(o.values) for o in expected.outputs],self.expected[str(version)])
            for cut in range(1,49):
                with self.subTest(version=version,cut=cut):
                    driver=self.driver(program);saved=[]
                    driver.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                    self.assertEqual(saved[0]["schema_version"],"openpine.context-state.1")
                    resumed=self.driver(program);resumed.restore(saved[0]);resumed.run()
                    self.assertEqual(resumed.finish(data_identity(self.data)),expected);cuts+=1
        self.assertEqual(cuts,288)

    def test_global_event_order_after_primary_gaps_and_equal_times(self):
        program=self.session.compile('//@version=6\nindicator("order")\nplot(request.security("TEST:MTF","60",close))\nplot(request.security("TEST:MTF","120",close))')
        inputs=context_inputs(EXAMPLE/"inputs.json")
        second=HistoricalData("mtf.twohour","TEST:MTF","120",(HistoricalBar(0,100.),HistoricalBar(7200000,200.)))
        context=MarketContext("mtf.twohour","TEST:MTF","120","uploaded-file.1")
        inputs["context_sources"].append((ContextBinding(context,make_snapshot(second,{"provider":context.source}).sha256),MemorySource(second)))
        data=replace(self.data,bars=(HistoricalBar(0,1.),HistoricalBar(11700000,2.)))
        driver=self.driver(program,inputs,data);events=[];driver.contexts.event_hook=events.append
        driver.run();result=driver.finish(data_identity(data))
        self.assertEqual([e[0] for e in events],[3600000,7200000,7200000,10800000])
        self.assertEqual(events,sorted(events))
        self.assertEqual([o.values for o in result.outputs],[(None,30.),(None,100.)])

    def test_requested_missing_bars_na_and_ema_do_not_fill_or_repeat_updates(self):
        inputs=context_inputs(EXAMPLE/"inputs.json");binding,source=inputs["context_sources"][0]
        data=replace(source.data,bars=(HistoricalBar(0,10.),HistoricalBar(7200000,30.),HistoricalBar(10800000,None)))
        inputs["context_sources"]=[(replace(binding,snapshot_id=make_snapshot(data,{"provider":binding.context.source}).sha256),MemorySource(data))]
        program=self.session.compile((EXAMPLE/"v6.pine").read_text(encoding="utf-8"))
        driver=self.driver(program,inputs);driver.run();result=driver.finish(data_identity(self.data))
        self.assertEqual(list(result.outputs[0].values),[None]*11+[10.]*24+[30.]*12+[None])
        self.assertEqual(list(result.outputs[1].values),[None]*11+[10.]*24+[20.]*13)
        expected=[None]*48;expected[11]=10.;expected[35]=30.
        self.assertEqual(list(result.outputs[2].values),expected)

    def test_future_data_changes_cannot_affect_off_prefix(self):
        inputs=context_inputs(EXAMPLE/"inputs.json");binding,source=inputs["context_sources"][0]
        program=self.session.compile((EXAMPLE/"v6.pine").read_text(encoding="utf-8"))
        a=self.driver(program,inputs);a.run();first=a.finish(data_identity(self.data))
        changed=replace(source.data,bars=(*source.data.bars[:-1],HistoricalBar(10800000,999.)))
        inputs["context_sources"]=[(replace(binding,snapshot_id=make_snapshot(changed,{"provider":binding.context.source}).sha256),MemorySource(changed))]
        b=self.driver(program,inputs);b.run();second=b.finish(data_identity(self.data))
        for left,right in zip(first.outputs,second.outputs):self.assertEqual(left.values[:47],right.values[:47])
        self.assertNotEqual(first.contexts,second.contexts)
        self.assertTrue(all(not r.historical_future for r in first.requests))

    def test_same_timeframe_reads_the_current_confirmed_bar(self):
        inputs=context_inputs(EXAMPLE/"inputs.json");inputs["context_sources"]=[]
        program=self.session.compile('//@version=6\nindicator("same")\nplot(request.security("TEST:MTF","5",close))')
        driver=self.driver(program,inputs);driver.run()
        self.assertEqual(driver.finish(data_identity(self.data)).outputs[0].values,tuple(float(i+1) for i in range(48)))

    def test_primary_declaration_function_is_not_a_requested_expression_dependency(self):
        for version in range(1,7):
            declaration="study" if version<5 else "indicator";call="security" if version<5 else "request.security"
            program=self.session.compile(f'//@version={version}\ntitle() => "helper"\n{declaration}(title())\nplot({call}("TEST:MTF","60",close))')
            driver=self.driver(program);saved=[];driver.run(pause_after=17,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
            resumed=self.driver(program);resumed.restore(saved[0]);resumed.run();result=resumed.finish(data_identity(self.data))
            self.assertEqual(result.declaration.title,"helper")
            self.assertEqual(list(result.outputs[0].values),self.expected[str(version)][0])

    def test_joint_restore_preserves_different_visibility_cursors_for_one_snapshot(self):
        program=self.session.compile('//@version=6\nindicator("modes")\nplot(request.security("TEST:MTF","60",close))\nplot(request.security("TEST:MTF","60",close,lookahead=barmerge.lookahead_on))')
        baseline=self.driver(program);baseline.run();expected=baseline.finish(data_identity(self.data))
        self.assertEqual(list(expected.outputs[0].values),[None]*11+[10.]*12+[20.]*12+[30.]*12+[40.])
        self.assertEqual(list(expected.outputs[1].values),[10.]*12+[20.]*12+[30.]*12+[40.]*12)
        for cut in (1,11,12,17,24,47,48):
            driver=self.driver(program);saved=[];driver.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
            resumed=self.driver(program);resumed.restore(saved[0]);resumed.run()
            self.assertEqual(resumed.finish(data_identity(self.data)),expected)

    def test_changed_input_identity_and_impossible_joint_cursor_are_rejected(self):
        program=self.session.compile((EXAMPLE/"v6.pine").read_text(encoding="utf-8"))
        driver=self.driver(program);saved=[];driver.run(pause_after=17,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
        inputs=context_inputs(EXAMPLE/"inputs.json");binding,source=inputs["context_sources"][0]
        changed=replace(source.data,bars=(*source.data.bars[:-1],HistoricalBar(10800000,999.)))
        inputs["context_sources"]=[(replace(binding,snapshot_id=make_snapshot(changed,{"provider":binding.context.source}).sha256),MemorySource(changed))]
        with self.assertRaisesRegex(ValueError,"input set"):self.driver(program,inputs).restore(saved[0])
        final=self.driver(program);later=[];final.run(pause_after=48,checkpoint=lambda d:later.append(loads(canonical(d.checkpoint()))))
        corrupt=copy.deepcopy(saved[0]);corrupt["groups"]=later[0]["groups"]
        with self.assertRaisesRegex(ValueError,"future bar"):self.driver(program).restore(corrupt)
        corrupt=copy.deepcopy(saved[0]);group=next(iter(corrupt["groups"].values()));group["cursor"]=0;group["last_time"]=None
        for row in group["records"].values():row.update(runtime=None,last=None)
        with self.assertRaisesRegex(ValueError,"skipped an available"):self.driver(program).restore(corrupt)
        first=self.driver(program);initial=[];first.run(pause_after=1,checkpoint=lambda d:initial.append(loads(canonical(d.checkpoint()))))
        for key,value in (("count",True),("last_time",False)):
            invalid=copy.deepcopy(initial[0]);invalid[key]=value
            with self.assertRaisesRegex(ValueError,"field types"):self.driver(program).restore(invalid)

    def test_unsupported_source_shapes_and_version_flags_are_explicit(self):
        for expression in ('request.security("TEST:MTF","60",close+1)','request.security("TEST:MTF","60",request.security("TEST:MTF","60",close))',
                'true ? request.security("TEST:MTF","60",close) : close','request.security("TEST:MTF","60",close,gaps=true)',
                'request.security("TEST:MTF","2D",close)','request.security("TEST:MTF","60",close,calc_bars_count=10)'):
            with self.subTest(expression=expression),self.assertRaises(OpenPineError):self.session.compile('//@version=6\nindicator("bad")\nplot('+expression+')')
        with self.assertRaises(OpenPineError):self.session.compile('//@version=2\nstudy("bad")\nplot(security("TEST:MTF","60",close,barmerge.gaps_on))')
        with self.assertRaises(ValueError):MarketContext("id","TEST:A","5","source",timezone="Europe/Moscow")
        program=self.session.compile((EXAMPLE/"v6.pine").read_text(encoding="utf-8"))
        with self.assertRaises(OpenPineError):self.session.execute(program,data=self.data)

    def test_lower_or_non_multiple_requested_timeframe_is_rejected(self):
        inputs=context_inputs(EXAMPLE/"inputs.json");binding,source=inputs["context_sources"][0]
        for tf in ("1","7"):
            data=replace(source.data,timeframe=tf)
            selected=replace(binding,context=replace(binding.context,timeframe=tf))
            current={**inputs,"context_sources":[(selected,MemorySource(data))]}
            program=self.session.compile(f'//@version=6\nindicator("bad")\nplot(request.security("TEST:MTF","{tf}",close))')
            with self.assertRaisesRegex(OpenPineError,"same/higher multiple"):self.driver(program,current)


class ManagedContextTests(unittest.TestCase):
    def test_maximum_context_and_request_counts_share_one_worker_budget(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,
                policy=ResourcePolicy(workers=1,total_memory_mb=512)) as manager:
            req=request(manager);req["contexts"]=[];plots=[]
            for i in range(8):
                symbol="TEST:MTF" if i==0 else f"TEST:C{i}";tf="5" if i==0 else "60"
                if i:
                    data=HistoricalData(f"context.{i}",symbol,tf,tuple(HistoricalBar(j*3600000,float(10*(j+1))) for j in range(4)))
                    sha=manager.market.save(make_snapshot(data,{"provider":"uploaded-file.1"}))
                    req["contexts"].append(asdict(ContextBinding(MarketContext(f"context.{i}",symbol,tf,"uploaded-file.1"),sha)))
                for mode in ("off","on"):plots.append(f'plot(request.security("{symbol}","{tf}",close,lookahead=barmerge.lookahead_{mode}))')
            req["input_ref"]=manager.freeze_input({"source":'//@version=6\nindicator("maximum")\n'+"\n".join(plots),"source_name":"maximum.pine"})
            job=manager.submit(req)["job_id"];state=wait(manager,job,timeout=60)
            self.assertEqual(state["status"],"succeeded",state)
            result=manager.result(job)["result"]
            self.assertEqual(len(result["requests"]),16);self.assertEqual(len(result["contexts"]),7)
            self.assertEqual(result["outputs"][0]["values"],list(map(float,range(1,49))))

    def test_workers_pause_reopen_and_resume_all_versions_equal_results_and_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            baselines={};jobs={}
            with local_manager(directory) as manager:
                for version in range(1,7):
                    req=request(manager,version);base=manager.submit(req,checkpoint_interval_bars=0)["job_id"]
                    self.assertEqual(wait(manager,base)["status"],"succeeded")
                    job=manager.submit(req,pause_after=17,checkpoint_interval_bars=11)["job_id"];paused(manager,job)
                    baselines[version]=(manager.result(base)["result"],manager.summary(base)["result"]["series"]);jobs[version]=job
            with local_manager(directory) as manager:
                for version,job in jobs.items():
                    self.assertEqual(manager.plan(job).schema_version,"openpine.run-plan.4")
                    manager.resume(job);state=wait(manager,job);self.assertEqual(state["status"],"succeeded",state)
                    self.assertEqual(state["resumed_from_bar"],17)
                    self.assertEqual(manager.result(job)["result"],baselines[version][0])
                    self.assertEqual(manager.summary(job)["result"]["series"],baselines[version][1])

    def test_snapshot_source_mismatch_and_duplicate_bindings_fail_before_queue(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            req=request(manager);bad=copy.deepcopy(req);bad["contexts"][0]["context"]["source"]="other"
            with self.assertRaisesRegex(OpenPineError,"source differs"):manager.submit(bad)
            bad=copy.deepcopy(req);bad["contexts"]*=2
            with self.assertRaisesRegex(ValueError,"ambiguous"):manager.submit(bad)
            self.assertEqual(manager.list(),[])

    def test_joint_checkpoint_backup_restore_and_prune_preserve_requested_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/"live") as manager:
                req=request(manager);job=manager.submit(req,pause_after=17)["job_id"];paused(manager,job)
                secondary=req["contexts"][0]["snapshot_id"]
                backup(manager.db,manager.directory,root/"backup")
            with local_services(root/"live") as services:
                report=services.lifecycle.prune(time.time(),expire_inputs=True,expire_coverage=True,apply=True)
                self.assertNotIn(secondary,report["datasets"])
                services.lifecycle.collect(apply=True,grace_seconds=0)
            restore(root/"backup",root/"restored")
            with local_manager(root/"restored") as manager:
                manager.resume(job);self.assertEqual(wait(manager,job)["status"],"succeeded")
                expected=read_json(EXAMPLE/"expected.json")["6"]
                self.assertEqual([o["values"] for o in manager.result(job)["result"]["outputs"]],expected)

    def test_actual_worker_crash_resumes_joint_graph(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,
                policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2)) as manager:
            req=request(manager)
            req["snapshot_id"]=manager.market.ingest({"context_id":"mtf.primary","symbol":"TEST:MTF","timeframe":"5","bar_schema":"openpine.historical-bars.1"},
                (HistoricalBar(i*300000,float(i%97)) for i in range(100000)),{"provider":"uploaded-file.1"})
            req["contexts"][0]["snapshot_id"]=manager.market.ingest({"context_id":"mtf.hourly","symbol":"TEST:MTF","timeframe":"60","bar_schema":"openpine.historical-bars.1"},
                (HistoricalBar(i*3600000,float(i%97)) for i in range(8334)),{"provider":"uploaded-file.1"})
            base=manager.submit(req,checkpoint_interval_bars=0)["job_id"];self.assertEqual(wait(manager,base)["status"],"succeeded")
            job=manager.submit(req,checkpoint_interval_bars=2000)["job_id"]
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                state=manager.get(job)
                if state.get("checkpoint_id") and state["status"]=="running":break
                time.sleep(.01)
            with manager.lock:
                state=manager.get(job);self.assertEqual(state["status"],"running",state);self.assertTrue(state["checkpoint_id"])
                manager.children[job][0].kill()
            state=wait(manager,job,timeout=90);self.assertEqual(state["status"],"succeeded",state)
            self.assertGreaterEqual(state["resumed_from_bar"],2000)
            self.assertEqual(manager.summary(job)["result"],manager.summary(base)["result"])

    def test_cli_file_inputs_and_resume_use_saved_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            command=[sys.executable,"-X","utf8","-m","apps.openpine","job"]
            first=subprocess.run([*command,"--source",str(EXAMPLE/"v6.pine"),"--bars",str(EXAMPLE/"bars.json"),
                "--contexts",str(EXAMPLE/"inputs.json"),"--pause-after","17","--state-dir",directory],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=40)
            self.assertEqual(first.returncode,0,first.stdout+first.stderr);state=json.loads(first.stdout)["job"]
            self.assertEqual(state["status"],"paused")
            second=subprocess.run([*command,"--resume",state["job_id"],"--state-dir",directory],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=40)
            self.assertEqual(second.returncode,0,second.stdout+second.stderr);response=json.loads(second.stdout)
            self.assertEqual(response["job"]["resumed_from_bar"],17)
            self.assertEqual([o["values"] for o in response["response"]["result"]["outputs"]],read_json(EXAMPLE/"expected.json")["6"])

    def test_actual_coordinator_crash_recovers_all_contexts_with_saved_policy(self):
        script='''
import sys,time
from pathlib import Path
sys.path[:0]=[sys.argv[1],str(Path(sys.argv[1])/'tests')]
from test_contexts import request
from apps.openpine.bootstrap import local_manager
from packages.contracts import HistoricalBar
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json
root=Path(sys.argv[2]);m=local_manager(root/'live',policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2))
req=request(m)
# This is a 200k-bar recovery scenario, not the small transport fixture.
# Its immutable timeout must include checkpoint I/O on hosted Windows runners.
req['timeout_seconds']=180
req['snapshot_id']=m.market.ingest({'context_id':'mtf.primary','symbol':'TEST:MTF','timeframe':'5','bar_schema':'openpine.historical-bars.1'},(HistoricalBar(i*300000,float(i%97)) for i in range(200000)),{'provider':'uploaded-file.1'})
req['contexts'][0]['snapshot_id']=m.market.ingest({'context_id':'mtf.hourly','symbol':'TEST:MTF','timeframe':'60','bar_schema':'openpine.historical-bars.1'},(HistoricalBar(i*3600000,float(i%97)) for i in range(16667)),{'provider':'uploaded-file.1'})
job=m.submit(req,checkpoint_interval_bars=2000)['job_id']
while not m.get(job).get('checkpoint_id'):time.sleep(.01)
atomic_json(root/'ready.json',m.get(job))
while True:time.sleep(.2)
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            host=subprocess.Popen([sys.executable,"-X","utf8","-c",script,str(ROOT),str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+60
                while not (root/"ready.json").exists() and host.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue((root/"ready.json").exists());old=read_json(root/"ready.json")
                host.kill();host.communicate(timeout=5);time.sleep(.3)
                with local_manager(root/"live",max_workers=1) as manager:
                    state=wait(manager,old["job_id"],timeout=240);self.assertEqual(state["status"],"succeeded",state)
                    self.assertGreaterEqual(state["resumed_from_bar"],2000);self.assertEqual(state["attempt_count"],2)
                    self.assertEqual(manager.plan(old["job_id"]).max_attempts,2)
                    outputs=manager.summary(old["job_id"])["result"]["outputs"]
                    self.assertEqual(outputs[0]["last"],float((200000//12-1)%97))
                    ema=None
                    for i in range(200000//12):ema=float(i%97) if ema is None else .5*float(i%97)+.5*ema
                    self.assertEqual(outputs[1]["last"],ema)
                    self.assertEqual(outputs[0]["count"],200000)
            finally:
                if host.poll() is None:host.kill();host.communicate(timeout=5)
