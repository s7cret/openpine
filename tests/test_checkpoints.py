"""Resume every supported historical corpus, then verify real process boundaries."""

import copy
import errno
import json
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager,local_services
from packages.compiler.semantic.specification import ROOT
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalBar,HistoricalData,OpenPineError
from packages.contracts.storage import ResourcePolicy,RunPlan
from packages.contracts.wire import canonical,loads,read_json,digest,atomic_json
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.session import Session
from packages.marketdata import read_bars,data_identity
from packages.marketdata.providers import make_snapshot,snapshot_payload
from packages.storage.backup import backup,restore
from packages.storage.results import ResultSink
from test_jobs import request,wait


def paused(manager,job,timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=manager.get(job)
        if state["status"]=="paused":return state
        if state["status"] in ("succeeded","failed","cancelled","timed_out"):
            raise AssertionError(state)
        time.sleep(.02)
    raise AssertionError(manager.get(job))


class RuntimeCheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_every_executable_historical_case_resumes_at_every_bar_v1_to_v6(self):
        cases=cuts=0;versions=set()
        for corpus in ("historical-bars","versions","language","series","functions"):
            directory=ROOT/"conformance"/corpus
            for case in read_json(directory/"cases.json")["cases"]:
                if case["expected"]["status"]!="executed":continue
                source=(directory/case["source"]).read_text(encoding="utf-8")
                data=read_bars(directory/case["bars"])
                program=self.session.compile(source,case["source"])
                expected=self.session.execute(program,data=data)
                self.assertEqual([list(o.values) for o in expected.outputs],[o["values"] for o in case["expected"]["outputs"]])
                versions.add(case["language_version"]);cases+=1
                for cut in range(1,len(data.bars)+1):
                    with self.subTest(corpus=corpus,case=case["case_id"],cut=cut):
                        runtime=self.session.validate_program(program)
                        original=HistoricalDriver(runtime.prepare(program),MemorySource(data));saved=[]
                        self.assertFalse(original.run(pause_after=cut,checkpoint=lambda ev:saved.append(loads(canonical(ev.checkpoint()),max_bytes=256*1024*1024))))
                        resumed=HistoricalDriver(runtime.prepare(program),MemorySource(data))
                        resumed.restore(saved[-1]);self.assertTrue(resumed.run())
                        self.assertEqual(resumed.evaluation.finish(data_identity(data)),expected)
                        cuts+=1
        self.assertEqual(versions,set(range(1,7)))
        self.assertEqual(cases,203)
        self.assertGreaterEqual(cuts,800)

    def test_failed_bar_emits_nothing_and_cannot_be_checkpointed(self):
        program=self.session.compile('//@version=6\nindicator("failure")\nplot(close)\nplot(ta.ema(close,0))')
        class Sink:
            rows=[]
            def emit(self,*row):self.rows.append(row)
        sink=Sink();evaluation=self.session.validate_program(program).prepare(program,output_sink=sink)
        data=HistoricalData("failure","TEST:F","1",(HistoricalBar(0,1.),))
        with self.assertRaises(OpenPineError):HistoricalDriver(evaluation,MemorySource(data)).run()
        self.assertEqual(sink.rows,[])
        self.assertEqual(evaluation.count,0)
        with self.assertRaises(ValueError):evaluation.checkpoint()

    def test_invalid_state_is_rejected_before_restored_effects(self):
        program=self.session.compile((ROOT/"examples/pipeline/v6.pine").read_text())
        data=read_bars(ROOT/"examples/pipeline/bars.json")
        runtime=self.session.validate_program(program)
        driver=HistoricalDriver(runtime.prepare(program),MemorySource(data));saved=[]
        driver.run(pause_after=2,checkpoint=lambda ev:saved.append(loads(canonical(ev.checkpoint()))))
        changes=(lambda s:s.update(count=True),lambda s:s.update(mode="sink"),lambda s:s.update(budget_cells=999),
            lambda s:s.update(outputs=[]),lambda s:s.update(extra=True),lambda s:s.update(persistent={"unknown":1}),
            lambda s:next(iter(s["history"].values())).update(count=0),
            lambda s:s["states"][0].update(kind="os.system"))
        for change in changes:
            value=copy.deepcopy(saved[-1]);change(value)
            evaluation=runtime.prepare(program)
            with self.assertRaises((ValueError,TypeError,KeyError)):evaluation.restore(value)
            self.assertEqual(evaluation.count,0)
            self.assertEqual(evaluation.outputs,{})

    def test_same_layout_different_program_cannot_restore(self):
        source='//@version=6\nindicator("identity")\nvar float x=1\nx:=x+close\nplot(x)'
        program=self.session.compile(source,"identity.pine")
        other=self.session.compile(source.replace("x=1","x=2"),"identity.pine")
        data=HistoricalData("identity","TEST:I","1",(HistoricalBar(0,1.),))
        evaluation=self.session.validate_program(program).prepare(program)
        HistoricalDriver(evaluation,MemorySource(data)).run()
        saved=loads(canonical(evaluation.checkpoint()))
        fresh=self.session.validate_program(other).prepare(other)
        with self.assertRaisesRegex(ValueError,"different program"):fresh.restore(saved)
        self.assertEqual(fresh.count,0)
        self.assertEqual(fresh.persistent,{})

    def test_wrapped_history_and_exact_sma_prefixes_survive(self):
        program=self.session.compile('//@version=6\nindicator("wrap")\nplot(ta.sma(close,3))\nplot(close[2])')
        data=HistoricalData("wrap","TEST:W","1",tuple(HistoricalBar(i,float(i%97)) for i in range(100005)))
        runtime=self.session.validate_program(program)
        driver=HistoricalDriver(runtime.prepare(program),MemorySource(data));saved=[]
        driver.run(pause_after=100002,checkpoint=lambda ev:saved.append(loads(canonical(ev.checkpoint()),max_bytes=256*1024*1024)))
        resumed=HistoricalDriver(runtime.prepare(program),MemorySource(data));resumed.restore(saved[0]);resumed.run()
        for index in range(100002,100005):
            self.assertEqual(resumed.evaluation.outputs[next(iter(resumed.evaluation.outputs))][2][index],sum(float(i%97) for i in range(index-2,index+1))/3)
        self.assertEqual(resumed.evaluation.count,100005)

    def test_old_run_plan_payload_and_identity_are_preserved(self):
        value={"input_ref":"1"*64,"snapshot_id":"2"*64,"timeout_seconds":30,
            "execution_sha256":"3"*64,"spec_lock_sha256":"4"*64,"profile_id":HISTORICAL_PROFILE_ID,
            "threads":1,"memory_mb":512,"max_attempts":1,"heartbeat_timeout":15.,
            "output_schema":"openpine.run-summary.3","schema_version":"openpine.run-plan.1"}
        plan=RunPlan.from_payload(value)
        self.assertEqual(plan.checkpoint_interval_bars,0)
        self.assertEqual(plan.payload(),value)
        self.assertEqual(digest(plan.payload()),digest(value))


class ManagedCheckpointTests(unittest.TestCase):
    def test_new_process_resume_all_versions_has_identical_result_and_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            baselines={};jobs={}
            with local_manager(directory) as manager:
                for version in range(1,7):
                    original=manager.submit(request(version))["job_id"];self.assertEqual(wait(manager,original)["status"],"succeeded")
                    job=manager.submit(request(version),checkpoint_interval_bars=2,pause_after=2)["job_id"]
                    state=paused(manager,job);self.assertEqual(state["completed"],2)
                    baselines[version]=(manager.result(original),manager.summary(original)["result"]["series"])
                    jobs[version]=(job,state["worker_pid"])
                    with self.assertRaises(OpenPineError):manager.result(job)
            with local_manager(directory) as manager:
                for version,(job,_) in jobs.items():
                    self.assertEqual(manager.get(job)["status"],"paused")
                    manager.resume(job);state=wait(manager,job)
                    self.assertEqual(state["status"],"succeeded",state)
                    self.assertEqual(state["resumed_from_bar"],2)
                    self.assertEqual(state["failure_count"],0)
                    self.assertEqual(manager.result(job)["result"],baselines[version][0]["result"])
                    self.assertEqual(manager.summary(job)["result"]["series"],baselines[version][1])

    def test_worker_crash_restores_last_durable_cursor(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,
                policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2)) as manager:
            # This verifies recovery and exact output equality, not runner throughput.
            payload=request(timeout=120);data=HistoricalData("large","TEST:L","1",tuple(HistoricalBar(i,float(i%97)) for i in range(100000)))
            payload["input"]["source"]='//@version=6\nindicator("crash")\nvar float total=0\ntotal:=total+nz(close)\nplot(total)\nplot(ta.ema(close,3))\nplot(close[1])'
            payload["snapshot"]=snapshot_payload(make_snapshot(data,{"provider":"test.1"}))
            baseline=manager.submit(payload,checkpoint_interval_bars=0)["job_id"]
            self.assertEqual(wait(manager,baseline,timeout=180)["status"],"succeeded")
            job=manager.submit(payload,checkpoint_interval_bars=10000)["job_id"]
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                state=manager.get(job)
                if state.get("checkpoint_id") and state["status"]=="running":break
                time.sleep(.01)
            with manager.lock:
                state=manager.get(job);self.assertTrue(state.get("checkpoint_id"),state)
                manager.children[job][0].kill()
            state=wait(manager,job,timeout=180)
            self.assertEqual(state["status"],"succeeded",state)
            self.assertGreaterEqual(state["resumed_from_bar"],10000)
            self.assertEqual(state["attempt_count"],2)
            self.assertEqual(manager.result(job)["result"],manager.result(baseline)["result"])
            self.assertEqual(manager.summary(job)["result"]["series"],manager.summary(baseline)["result"]["series"])

    def test_running_pause_command_is_acknowledged_at_a_completed_bar(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            with manager.lock:
                job=manager.submit(request())["job_id"]
                manager._start(manager.get(job));manager.pause(job)
            state=paused(manager,job)
            self.assertGreaterEqual(state["completed"],1)
            self.assertTrue(state["checkpoint_id"])
            manager.resume(job)
            self.assertEqual(wait(manager,job)["status"],"succeeded")
            self.assertEqual(manager.result(job)["result"]["outputs"][0]["values"],[10.,40.,85.,135.])

    def test_killed_coordinator_recovers_checkpoint_with_saved_retry_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            script='''
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from apps.openpine.bootstrap import local_manager
from packages.contracts import HistoricalBar
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json
p=Path(sys.argv[2]);m=local_manager(p/'jobs',policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2))
snapshot=m.market.ingest({'context_id':'host','symbol':'TEST:H','timeframe':'1','bar_schema':'openpine.historical-bars.1'},
    (HistoricalBar(i,float(i%97)) for i in range(200000)),{'provider':'test.1'})
ref=m.freeze_input({'source':'//@version=6\\nindicator("host")\\nvar float total=0\\ntotal:=total+nz(close)\\nplot(total,"total")\\nplot(ta.ema(close,3),"ema")','source_name':'host.pine'})
j=m.submit({'schema_version':'openpine.job-request.2','input_ref':ref,'snapshot_id':snapshot,'timeout_seconds':120},checkpoint_interval_bars=2000)['job_id']
while not m.get(j).get('checkpoint_id'):time.sleep(.01)
atomic_json(p/'ready.json',m.get(j))
while True:time.sleep(1)
'''
            host=subprocess.Popen([sys.executable,"-X","utf8","-c",script,str(ROOT),str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+30
                while not (root/"ready.json").exists() and host.poll() is None and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue((root/"ready.json").exists())
                old=read_json(root/"ready.json")
                host.kill();host.communicate(timeout=5)
                time.sleep(.3)
                with local_manager(root/"jobs",max_workers=1) as manager:
                    state=wait(manager,old["job_id"],timeout=90)
                    self.assertEqual(state["status"],"succeeded",state)
                    self.assertGreaterEqual(state["resumed_from_bar"],2000)
                    self.assertEqual(state["attempt_count"],2)
                    summary=manager.summary(old["job_id"])["result"]
                    expected=float((200000//97)*sum(range(97))+sum(range(200000%97)))
                    self.assertEqual(summary["outputs"][0]["last"],expected)
                    self.assertEqual(summary["outputs"][0]["count"],200000)
                    self.assertEqual(sum(p["rows"] for p in summary["series"]),400000)
            finally:
                if host.poll() is None:host.kill();host.communicate(timeout=5)

    def test_checkpoint_publication_failure_keeps_previous_root(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            job=manager.submit(request(),pause_after=2)["job_id"];state=paused(manager,job)
            current=state["checkpoint_id"]
            manifest=manager.services.checkpoints.manifest(current)
            source=manager.market.source(manager.plan(job).snapshot_id)
            from packages.execution.artifacts import import_program
            program=import_program(manager.objects.get(manifest["artifact_ref"]))
            session=Session(profile_id=HISTORICAL_PROFILE_ID)
            with tempfile.TemporaryDirectory() as work:
                sink=ResultSink(manager.objects,work)
                driver=session.stream_driver(program,source,sink)
                saved,runtime_state,prefix=manager.services.checkpoints.load(current,plan=manager.plan(job),source=source)
                driver.restore(runtime_state);sink.restore(prefix,2)
                try:
                    with patch.object(manager.objects,"copy_file",side_effect=OSError(errno.ENOSPC,"disk full")):
                        with self.assertRaises(OSError):
                            manager.services.checkpoints.save(driver.evaluation,sink,plan=manager.plan(job),source=source,
                                artifact_ref=saved["artifact_ref"],program_sha256=saved["program_sha256"])
                    self.assertEqual(manager.get(job)["checkpoint_id"],current)
                finally:sink.close()

    def test_foreign_plan_checkpoint_is_rejected_before_resume(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            a=manager.submit(request(5),pause_after=2)["job_id"];pa=paused(manager,a)
            b=manager.submit(request(6),pause_after=2)["job_id"];paused(manager,b)
            with self.assertRaises(ValueError):
                manager.services.checkpoints.load(pa["checkpoint_id"],plan=manager.plan(b),
                    source=manager.market.source(manager.plan(b).snapshot_id))

    def test_paused_backup_gc_and_restore_keep_output_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/"live") as manager:
                baseline=manager.submit(request())["job_id"];wait(manager,baseline);expected=manager.result(baseline)
                job=manager.submit(request(),pause_after=3)["job_id"];state=paused(manager,job)
                checkpoint=manager.services.checkpoints.manifest(state["checkpoint_id"])
                prefix=manager.objects.get(checkpoint["output_ref"])
                pending=prefix["pending"]["object_id"]
                backup(manager.db,manager.directory,root/"backup")
            with local_services(root/"live") as services:
                report=services.lifecycle.collect(apply=True,grace_seconds=0)
                self.assertNotIn(pending,report["candidates"]);services.objects.verify(pending)
            restore(root/"backup",root/"restored")
            with local_manager(root/"restored") as manager:
                manager.resume(job);self.assertEqual(wait(manager,job)["status"],"succeeded")
                self.assertEqual(manager.result(job)["result"],expected["result"])

    def test_corrupt_runtime_and_foreign_checkpoint_cannot_resume(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            job=manager.submit(request(),pause_after=2)["job_id"];state=paused(manager,job)
            manifest=manager.services.checkpoints.manifest(state["checkpoint_id"])
            manager.objects.path(manifest["runtime_ref"]).write_bytes(b"corrupt")
            manager.resume(job);state=wait(manager,job)
            self.assertEqual(state["status"],"failed")
            self.assertIsNone(state["result_id"])

    def test_pause_queued_does_not_spawn_and_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            with manager.lock:
                job=manager.submit(request())["job_id"]
                self.assertEqual(manager.pause(job)["status"],"paused")
            self.assertIsNone(manager.get(job)["worker_pid"])
            self.assertEqual(manager.cancel(job)["status"],"cancelled")

    def test_paused_attempt_cannot_replace_checkpoint_root(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            job=manager.submit(request(),pause_after=2)["job_id"];state=paused(manager,job)
            saved=state["checkpoint_id"]
            with self.assertRaisesRegex(ValueError,"running attempt"):
                manager.repository.checkpoint(state,"1"*64,2)
            self.assertEqual(manager.get(job)["checkpoint_id"],saved)

    def test_pause_resume_cli_uses_saved_job_without_source_or_data_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            args=[sys.executable,"-X","utf8","-m","apps.openpine","job"]
            first=subprocess.run([*args,"--source",str(ROOT/"examples/pipeline/v6.pine"),"--bars",str(ROOT/"examples/pipeline/bars.json"),
                "--pause-after","2","--state-dir",directory],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=30)
            self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            state=json.loads(first.stdout)["job"];self.assertEqual(state["status"],"paused")
            second=subprocess.run([*args,"--resume",state["job_id"],"--state-dir",directory],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=30)
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            result=json.loads(second.stdout)
            self.assertEqual(result["job"]["resumed_from_bar"],2)
            self.assertEqual(result["response"]["result"]["outputs"][0]["values"],[10.,40.,85.,135.])
