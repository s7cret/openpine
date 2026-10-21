import copy
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from apps.ui.server import Server
from packages.compiler.semantic.specification import ROOT
from packages.contracts.application import TERMINAL_STATUSES
from packages.execution.artifacts import import_program
from packages.execution.service import Application
from packages.marketdata.providers import snapshot_from_payload


class HTTPTests(unittest.TestCase):
    def test_checkpoint_pause_and_resume_transport(self):
        compiled=self.call("/api/compile",{"source":self.call("/api/examples/6")[1]["source"],"source_name":"checkpoint.pine"})[1]
        code,state=self.call("/api/jobs",{"schema_version":"openpine.job-request.3","input_ref":compiled["input_ref"],
            "snapshot_id":self.snapshot["sha256"],"timeout_seconds":30,"checkpoint_interval_bars":2,"pause_after_bars":2})
        self.assertEqual(code,202,state);job=state["job_id"]
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            state=self.call("/api/jobs/"+job)[1]
            if state["status"]=="paused":break
            time.sleep(.03)
        self.assertEqual(state["status"],"paused",state)
        self.assertEqual(self.call(f"/api/jobs/{job}/result")[0],409)
        self.assertEqual(self.call(f"/api/jobs/{job}/pause",{})[1]["status"],"paused")
        self.assertEqual(self.call(f"/api/jobs/{job}/resume",{})[0],200)
        self.assertEqual(self.finish(job)["status"],"succeeded")
        result=self.call(f"/api/jobs/{job}/result")[1]
        self.assertEqual(result["result"]["outputs"][0]["values"],[10.,40.,85.,135.])

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = Server(("127.0.0.1", 0), cls.temp.name)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.token = cls.call("/api/config")[1]["token"]
        sha = cls.call("/api/snapshots", {"provider": "demo"})[1]["sha256"]
        cls.snapshot = cls.call("/api/snapshots/" + sha)[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.thread.join(timeout=10); cls.server.server_close()
        cls.temp.cleanup()

    @classmethod
    def call(cls, path, payload=None, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(cls.base + path, data=body, headers={
            "Content-Type": "application/json", "X-OpenPine-Token": getattr(cls, "token", ""), **(headers or {})})
        try:
            response = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if "application/json" in response.headers["Content-Type"] else raw

    def finish(self, job_id):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = self.call("/api/jobs/" + job_id)[1]
            if state["status"] in TERMINAL_STATUSES:
                return state
            time.sleep(.03)
        self.fail(f"HTTP job did not finish: {state}")

    def submit(self, content, timeout=30):
        status, result = self.call("/api/jobs", {"schema_version": "openpine.job-submission.1", "input": content,
                    "snapshot_sha256": self.snapshot["sha256"], "timeout_seconds": timeout})
        self.assertEqual(status, 202, result)
        return result["job_id"]

    def test_all_versions_http_and_direct_service_are_equal(self):
        app = Application()
        for version in range(1, 7):
            with self.subTest(version=version):
                source = self.call(f"/api/examples/{version}")[1]["source"]
                compiled = self.call("/api/compile", {"source": source, "source_name": f"v{version}.pine"})[1]
                self.assertEqual(compiled["status"], "compiled")
                job = self.submit({"artifact": compiled["artifact"]})
                state = self.finish(job)
                self.assertEqual(state["status"], "succeeded", state)
                response = self.call(f"/api/jobs/{job}/result")[1]
                direct = app.execute(import_program(compiled["artifact"]), data=snapshot_from_payload(self.snapshot).data)
                self.assertEqual(response["result"], json.loads(json.dumps(direct["result"])))
                self.assertEqual(response["result"]["outputs"][0]["values"], [10., 40., 85., 135.])

    def test_source_diagnostic_and_invalid_data_requests(self):
        source = '//@version=6\nindicator("bad")\nplot(missing)'
        response = self.call("/api/compile", {"source": source, "source_name": "bad.pine"})[1]
        self.assertEqual(response["diagnostics"][0]["span"]["line"], 3)
        job = self.submit({"source": source, "source_name": "bad.pine"})
        self.assertEqual(self.finish(job)["status"], "failed")
        self.assertEqual(self.call(f"/api/jobs/{job}/result")[1]["diagnostics"][0]["span"]["line"], 3)
        invalid = copy.deepcopy(self.snapshot)
        invalid["data"]["bars"][1]["time"] = 0
        self.assertEqual(self.call("/api/snapshots", {"provider": "upload", "payload": invalid})[0], 400)
        self.assertEqual(self.call("/api/compile", {"source": source, "source_name": "x", "extra": True})[0], 400)

    def test_cancel_timeout_and_incompatible_artifact(self):
        source = self.call("/api/examples/6")[1]["source"]
        job = self.submit({"source": source, "source_name": "x"})
        state = self.call(f"/api/jobs/{job}/cancel", {})[1]
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(self.call(f"/api/jobs/{job}/result")[0], 409)
        job = self.submit({"source": source, "source_name": "x"}, timeout=.001)
        self.assertEqual(self.finish(job)["status"], "timed_out")
        artifact = self.call("/api/compile", {"source": source, "source_name": "x"})[1]["artifact"]
        artifact["ir"]["identity"]["implementation_sha256"] = "0" * 64
        job = self.submit({"artifact": artifact})
        self.assertEqual(self.finish(job)["status"], "failed")

    def test_static_assets_and_request_boundaries(self):
        self.assertIn(b"OpenPine", self.call("/")[1])
        self.assertIn(b"canvas", self.call("/app.js")[1])
        self.assertEqual(self.call("/../spec/lock.json")[0], 404)
        self.assertEqual(self.call("/api/snapshots", {"provider": "demo"}, {"X-OpenPine-Token": "wrong"})[0], 403)
        self.assertEqual(self.call("/api/config", headers={"Origin": "https://other.example"})[0], 403)
        self.assertEqual(self.call("/api/config", headers={"Host": "other.example"})[0], 403)
        self.assertEqual(self.call("/api/compile", raw=b'{"source":"a","source":"b","source_name":"x"}')[0], 400)
        self.assertEqual(self.call("/api/compile", raw=b'{"source":NaN,"source_name":"x"}')[0], 400)

    def test_reference_submission_and_bounded_chart_protocol(self):
        source = self.call("/api/examples/6")[1]["source"]
        compiled = self.call("/api/compile", {"source": source, "source_name": "bounded.pine"})[1]
        status, submitted = self.call("/api/jobs", {"schema_version": "openpine.job-request.2",
            "input_ref": compiled["input_ref"], "snapshot_id": self.snapshot["sha256"], "timeout_seconds": 30})
        self.assertEqual(status, 202)
        job = submitted["job_id"]
        self.assertEqual(self.finish(job)["status"], "succeeded")
        request = self.call(f"/api/jobs/{job}/request")[1]
        self.assertNotIn("input", request)
        self.assertNotIn("snapshot", request)
        saved_input = self.server.jobs.objects.get(request["input_ref"])
        self.assertEqual(saved_input["artifact_ref"], compiled["artifact_ref"])
        self.assertEqual(self.server.jobs.objects.get(saved_input["source_ref"]),
            {"source": source, "source_name": "bounded.pine"})
        summary = self.call(f"/api/jobs/{job}/summary")[1]
        self.assertEqual(summary["artifact_ref"], compiled["artifact_ref"])
        self.assertNotIn("values", summary["result"]["outputs"][0])
        window = self.call(f"/api/jobs/{job}/chart?start=1&limit=2")[1]
        self.assertEqual(window["outputs"][0]["values"], [40.,85.])
        self.assertEqual(len(window["bars"]), 2)
        self.assertEqual(self.call(f"/api/jobs/{job}/chart?limit=5001")[0], 400)
        self.assertEqual(self.call(f"/api/jobs/{job}/chart?start=0&start=1")[0], 400)
        manifest = self.call("/api/datasets/" + self.snapshot["sha256"])[1]
        self.assertNotIn("bars", manifest)
        self.assertEqual(manifest["identity"]["count"], 4)
