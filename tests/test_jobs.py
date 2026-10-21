import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from packages.compiler.semantic.specification import ROOT
from packages.contracts import OpenPineError
from packages.contracts.application import TERMINAL_STATUSES, CompileRequest
from packages.contracts.marketdata import DataQuery
from packages.contracts.wire import atomic_json
from apps.openpine.bootstrap import local_manager as JobManager
from packages.execution.service import Application
from packages.marketdata.providers import FileProvider, snapshot_payload


def request(version=6, *, timeout=30):
    snapshot = FileProvider(ROOT / "examples/pipeline/bars.json").fetch(DataQuery("TEST:PIPELINE", "1", 0, 240000))
    return {"schema_version": "openpine.job-request.1",
            "input": {"source": (ROOT / f"examples/pipeline/v{version}.pine").read_text(), "source_name": f"v{version}.pine"},
            "snapshot": snapshot_payload(snapshot), "timeout_seconds": timeout}


def wait(manager, job_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.get(job_id)
        if state["status"] in TERMINAL_STATUSES:
            return state
        time.sleep(0.03)
    raise AssertionError(f"Job did not finish: {manager.get(job_id)}")


class JobTests(unittest.TestCase):
    def test_actual_workers_all_versions_and_persisted_results(self):
        with tempfile.TemporaryDirectory() as directory:
            with JobManager(directory) as manager:
                ids = [manager.submit(request(v))["job_id"] for v in range(1, 7)]
                pids = []
                for job_id in ids:
                    state = wait(manager, job_id)
                    self.assertEqual(state["status"], "succeeded", state)
                    pids.append(state["worker_pid"])
                    result = manager.result(job_id)
                    self.assertEqual(result["result"]["outputs"][0]["values"], [10., 40., 85., 135.])
                    self.assertEqual(state["completed"], 4)
                self.assertEqual(len(set(pids)), 6)
            with JobManager(directory) as restored:
                self.assertEqual(restored.result(ids[0])["status"], "executed")
                self.assertEqual(len(restored.list()), 6)

    def test_compiled_artifact_uses_same_worker_path(self):
        payload = request()
        compiled = Application().handle(CompileRequest(payload["input"]["source"], payload["input"]["source_name"]))
        payload["input"] = {"artifact": compiled["artifact"]}
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            job = manager.submit(payload)["job_id"]
            self.assertEqual(wait(manager, job)["status"], "succeeded")
            self.assertEqual(manager.result(job)["program_sha256"], compiled["program_sha256"])

    def test_source_error_is_a_failed_job_with_diagnostic_position(self):
        payload = request()
        payload["input"]["source"] = '//@version=6\nindicator("bad")\nplot(missing)'
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            job = manager.submit(payload)["job_id"]
            self.assertEqual(wait(manager, job)["status"], "failed")
            self.assertEqual(manager.result(job)["diagnostics"][0]["span"]["line"], 3)

    def test_timeout_cancel_queue_capacity_and_single_owner(self):
        with tempfile.TemporaryDirectory() as directory, JobManager(directory, max_workers=1, capacity=2) as manager:
            with self.assertRaises(OpenPineError):
                JobManager(directory)
            # Keep the consumer from finishing the short-timeout first job
            # while this test is still constructing the queue-capacity case.
            with manager.lock:
                first = manager.submit(request(timeout=.001))["job_id"]
                second = manager.submit(request())["job_id"]
                with self.assertRaises(OpenPineError):
                    manager.submit(request())
            self.assertEqual(manager.cancel(second)["status"], "cancelled")
            self.assertEqual(wait(manager, first)["status"], "timed_out")
            for job in (first, second):
                with self.assertRaises(OpenPineError):
                    manager.result(job)
            running = manager.submit(request())["job_id"]
            deadline = time.monotonic() + 10
            while manager.get(running)["status"] == "queued" and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(manager.cancel(running)["status"], "cancelled")

    def test_killed_worker_is_failure_and_does_not_publish_result(self):
        with tempfile.TemporaryDirectory() as directory, JobManager(directory) as manager:
            job = manager.submit(request())["job_id"]
            deadline = time.monotonic() + 10
            while manager.get(job)["status"] == "queued" and time.monotonic() < deadline:
                time.sleep(.02)
            with manager.lock:
                manager.children[job][0].kill()
            state = wait(manager, job)
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["error"]["code"], "WORKER_CRASH")
            with self.assertRaises(OpenPineError):
                manager.result(job)

    def test_interrupted_status_recovered_and_result_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with JobManager(directory) as manager:
                job = manager.submit(request())["job_id"]
                self.assertEqual(wait(manager, job)["status"], "succeeded")
                result_path = manager.objects.path(manager.get(job)["result_id"])
                result = json.loads(result_path.read_text())
                result["result"]["outputs"][0]["last"] = -999
                atomic_json(result_path, result)
                with self.assertRaises(OpenPineError):
                    manager.result(job)
                state = manager.get(job)
            from packages.storage.database import Database
            database = Database(Path(directory) / "catalog.sqlite3")
            try:
                state["status"] = "running"
                database.save(state)
            finally:
                database.close()
            with JobManager(directory) as restored:
                self.assertEqual(restored.get(job)["error"]["code"], "HOST_RESTARTED")

    def test_host_process_crash_terminates_its_worker_and_recovers_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            atomic_json(path / "input.json", request())
            script = """
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from apps.openpine.bootstrap import local_manager as JobManager
from packages.contracts.wire import read_json,atomic_json
p=Path(sys.argv[2]); m=JobManager(p/'jobs',max_workers=1)
j=m.submit(read_json(p/'input.json'))['job_id']
while not m.get(j)['worker_pid']: time.sleep(.01)
ready=m.get(j); ready['queued_job']=m.submit(read_json(p/'input.json'))['job_id']
atomic_json(p/'ready.json',ready)
while True: time.sleep(1)
"""
            host = subprocess.Popen([sys.executable, "-X", "utf8", "-c", script, str(ROOT), str(path)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            handle = None
            try:
                deadline = time.monotonic() + 15
                while not (path / "ready.json").exists() and host.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                ready = json.loads((path / "ready.json").read_text())
                pid = ready["worker_pid"]
                if os.name == "nt":
                    import ctypes
                    from ctypes import wintypes
                    api = ctypes.WinDLL("kernel32")
                    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                    api.OpenProcess.restype = wintypes.HANDLE
                    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                    api.CloseHandle.argtypes = [wintypes.HANDLE]
                    handle = api.OpenProcess(0x00100000, False, pid)
                host.kill(); host.wait(timeout=5)
                deadline = time.monotonic() + 10
                def alive():
                    if os.name == "nt":
                        return bool(handle) and api.WaitForSingleObject(handle, 0) == 258
                    try:
                        os.kill(pid, 0)
                        stat = Path(f"/proc/{pid}/stat")
                        return not (stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z"))
                    except ProcessLookupError:
                        return False
                while alive() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertFalse(alive(), "Worker survived its owning process")
                with JobManager(path / "jobs") as restored:
                    self.assertEqual(restored.get(ready["job_id"])["error"]["code"], "HOST_RESTARTED")
                    self.assertEqual(wait(restored, ready["queued_job"])["status"], "succeeded")
            finally:
                if host.poll() is None:
                    host.kill(); host.wait(timeout=5)
                if handle:
                    api.CloseHandle(handle)
