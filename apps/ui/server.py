"""Loopback HTTP adapter. Calculation belongs to execution and isolated workers."""

import argparse
import errno
from contextlib import nullcontext
from dataclasses import asdict
import hmac
import platform
import re
import secrets
import threading
import apsw
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.application import CompileRequest
from packages.contracts.marketdata import DataQuery
from packages.contracts.wire import canonical, loads
from apps.openpine.bootstrap import local_manager as JobManager
from packages.execution.service import Application, rejection
from packages.marketdata.binance import BinanceProvider
from packages.marketdata.providers import FileProvider, input_snapshot, snapshot_from_payload

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"


def fields(payload, names):
    if type(payload) is not dict or payload.keys() != set(names):
        raise OpenPineError(Diagnostic("INPUT_INVALID", "REQUEST_INVALID", "Missing or extra request fields."))


class Server(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, address, directory, *, policy=None, coordinator_policy=None):
        if address[0] not in ("127.0.0.1", "localhost"):
            raise ValueError("The local application must bind to loopback.")
        super().__init__(address, Handler)
        self.token = secrets.token_urlsafe(32)
        self.app = Application()
        self.compile_lock = threading.Lock()
        try:
            self.jobs = JobManager(Path(directory) / "jobs", policy=policy, coordinator_policy=coordinator_policy)
            self.connections = threading.BoundedSemaphore(self.jobs.limiter.policy.connections)
            self.store = self.jobs.market
            # Import the old standalone snapshot directory once; keep all sources.
            if not self.jobs.repository.scanned("__snapshots_v1__"):
                from packages.contracts.wire import read_json
                for path in (Path(directory) / "snapshots").glob("*.json"):
                    try:
                        self.store.save(snapshot_from_payload(read_json(path)))
                        self.jobs.repository.record_import(str(path))
                    except (OSError, ValueError) as error:
                        self.jobs.repository.record_import(str(path), error=str(error))
                self.jobs.repository.record_import("__snapshots_v1__")
        except Exception:
            if hasattr(self, "jobs"):
                self.jobs.close()
            super().server_close()
            raise

    def server_close(self):
        super().server_close()  # Drain bounded handlers before closing their services.
        if hasattr(self, "jobs"):
            self.jobs.close()

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 1\r\nConnection: close\r\n\r\n")
            except ConnectionError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request,client_address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self,request,client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            self.connections.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenPine/6"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass

    def send(self, status, value, *, content_type="application/json; charset=utf-8"):
        raw = value if type(value) is bytes else canonical(value)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(raw)
        except ConnectionError:
            pass

    def validate_host(self):
        port = self.server.server_port
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self.send(403, {"diagnostics": [{"code": "HOST_INVALID", "message": "Unknown local host."}]})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            self.send(403, {"diagnostics": [{"code": "ORIGIN_INVALID", "message": "Use the application's own origin."}]})
            return False
        return True

    def dispatch(self, write=False):
        if not self.validate_host():
            return
        try:
            path = urlsplit(self.path).path
            heavy = write and not path.endswith(("/cancel","/pause","/resume"))
            heavy = heavy or (not write and (path.startswith("/api/snapshots/") or path.endswith(("/chart","/result")) or "/series/" in path or "/parts/" in path))
            reservation = self.server.jobs.limiter.slot("prepare" if write else "read") if heavy else nullcontext()
            with reservation:
                if write:
                    supplied = self.headers.get("X-OpenPine-Token", "")
                    if not hmac.compare_digest(supplied, self.server.token):
                        self.send(403, {"diagnostics": [{"code": "TOKEN_INVALID", "message": "Reload the application before submitting."}]})
                        return
                    if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json" or self.headers.get("Transfer-Encoding"):
                        self.send(415, {"diagnostics": [{"code": "CONTENT_TYPE", "message": "Use finite UTF-8 JSON with Content-Length."}]})
                        return
                    length = int(self.headers.get("Content-Length", "-1"))
                    if not 0 <= length <= 16 * 1024 * 1024:
                        self.send(413, {"diagnostics": [{"code": "REQUEST_LIMIT", "message": "Request must be at most 16 MiB."}]})
                        return
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("Incomplete request body.")
                    payload = loads(raw)
                    self.post(path, payload)
                else:
                    self.get(path)
        except ConnectionError:
            return
        except OpenPineError as error:
            code = error.diagnostic.code
            status = 404 if code == "JOB_NOT_FOUND" else 503 if code in ("STORAGE_UNAVAILABLE","COORDINATOR_BUSY") else 409 if code in ("RESULT_UNAVAILABLE", "JOB_STORE_BUSY", "JOB_QUEUE_FULL") else 400
            self.send(status, rejection(error))
        except apsw.Error as error:
            self.send(507 if isinstance(error, apsw.FullError) else 503,
                {"status": "rejected", "diagnostics": [{"code": "STORAGE_UNAVAILABLE", "message": str(error)}]})
        except OSError as error:
            self.send(507 if error.errno == errno.ENOSPC else 400,
                {"status":"rejected","diagnostics":[{"code":"STORAGE_FULL" if error.errno == errno.ENOSPC else "INPUT_IO","message":str(error)}]})
        except (ValueError, TypeError, KeyError) as error:
            self.send(400, {"status": "rejected", "diagnostics": [{"category": "INPUT_INVALID", "code": "REQUEST_INVALID", "message": str(error)}]})
        except Exception:
            self.send(500, {"status": "rejected", "diagnostics": [{"code": "SERVER_ERROR", "message": "Unexpected application error."}]})

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(True)

    def get(self, path):
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8")}
        if path in assets:
            name, content_type = assets[path]
            self.send(200, (STATIC / name).read_bytes(), content_type=content_type)
        elif path == "/api/config":
            self.send(200, {"token": self.server.token, "python": platform.python_version(), "versions": list(range(1, 7))})
        elif match := re.fullmatch(r"/api/examples/([1-6])", path):
            self.send(200, {"source": (ROOT / "examples/pipeline" / ("v" + match[1] + ".pine")).read_text(encoding="utf-8")})
        elif match := re.fullmatch(r"/api/snapshots/([0-9a-f]{64})", path):
            self.send(200, self.server.store.export(match[1]))
        elif match := re.fullmatch(r"/api/datasets/([0-9a-f]{64})", path):
            self.send(200, self.server.store.manifest(match[1]))
        elif match := re.fullmatch(r"/api/(jobs|datasets)/([0-9a-f]{32}|[0-9a-f]{64})/(series|parts)/([0-9a-f]{64})", path):
            if match[1] == "jobs" and match[3] == "series":
                parts = self.server.jobs.summary(match[2])["result"]["series"]
            elif match[1] == "datasets" and match[3] == "parts":
                parts = self.server.store.manifest(match[2])["parts"]
            else:
                raise ValueError("Unknown object route.")
            if not any(part["object_id"] == match[4] for part in parts):
                raise ValueError("Object does not belong to this manifest.")
            file = self.server.jobs.objects.verify(match[4])
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apache.parquet")
            self.send_header("Content-Length", str(file.stat().st_size))
            self.send_header("Content-Disposition", 'attachment; filename="' + match[4] + '.parquet"')
            self.send_header("Connection", "close")
            self.end_headers(); self.close_connection = True
            with file.open("rb") as stream:
                while chunk := stream.read(65536):
                    self.wfile.write(chunk)
        elif path == "/api/jobs":
            self.send(200, self.server.jobs.list())
        elif match := re.fullmatch(r"/api/jobs/([0-9a-f]{32})(/(result|request|summary|chart|plan))?", path):
            action = match[3]
            if action == "plan":
                value = self.server.jobs.plan(match[1]).payload()
            elif action == "chart":
                query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
                if set(query) - {"start", "limit"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError("Unknown or duplicate chart range parameter.")
                value = self.server.jobs.chart(match[1], int(query.get("start", [0])[0]), int(query.get("limit", [2000])[0]))
            else:
                value = {"result": self.server.jobs.result, "request": self.server.jobs.request,
                    "summary": self.server.jobs.summary}.get(action, self.server.jobs.get)(match[1])
            self.send(200, value)
        else:
            self.send(404, {"diagnostics": [{"code": "NOT_FOUND", "message": "Unknown route."}]})

    def post(self, path, payload):
        if path == "/api/compile":
            fields(payload, ("source", "source_name"))
            with self.server.compile_lock:
                response = self.server.app.handle(CompileRequest(payload["source"], payload["source_name"]))
                if response["status"] == "compiled":
                    response["source_ref"] = self.server.jobs.objects.put(payload)
                    response["artifact_ref"] = self.server.jobs.objects.put(response["artifact"])
                    response["input_ref"] = self.server.jobs.freeze_input({"artifact_ref": response["artifact_ref"],
                        "source_ref": response["source_ref"]})
                self.send(200, response)
        elif path == "/api/snapshots":
            mode = payload.get("provider") if type(payload) is dict else None
            if mode == "demo":
                fields(payload, ("provider",))
                snapshot = FileProvider(ROOT / "examples/pipeline/bars.json").fetch(DataQuery("TEST:PIPELINE", "1", 0, 240000))
            elif mode == "upload":
                if payload.keys() == {"provider", "raw"}:
                    snapshot = input_snapshot(loads(payload["raw"]))
                else:
                    fields(payload, ("provider", "payload"))
                    snapshot = input_snapshot(payload["payload"])
            elif mode == "binance":
                fields(payload, ("provider", "query"))
                fields(payload["query"], ("symbol", "timeframe", "start_ms", "end_ms"))
                snapshot_id = self.server.store.ensure(BinanceProvider(), DataQuery(**payload["query"]))
                self.send(201, {"sha256": snapshot_id, "bars": self.server.store.manifest(snapshot_id)["identity"]["count"]})
                return
            else:
                raise ValueError("Unknown historical provider.")
            self.server.store.save(snapshot)
            self.send(201, {"sha256": snapshot.sha256, "bars": len(snapshot.data.bars)})
        elif path == "/api/jobs":
            if type(payload) is dict and payload.get("schema_version")=="openpine.job-request.3":
                fields(payload,("schema_version","input_ref","snapshot_id","timeout_seconds","checkpoint_interval_bars","pause_after_bars"))
                request={key:payload[key] for key in ("input_ref","snapshot_id","timeout_seconds")}
                request["schema_version"]="openpine.job-request.2"
                self.send(202,self.server.jobs.submit(request,checkpoint_interval_bars=payload["checkpoint_interval_bars"],pause_after=payload["pause_after_bars"]))
                return
            if type(payload) is dict and payload.get("schema_version") == "openpine.job-request.2":
                self.send(202, self.server.jobs.submit(payload))
                return
            fields(payload, ("schema_version", "input", "snapshot_sha256", "timeout_seconds"))
            if payload["schema_version"] != "openpine.job-submission.1":
                raise ValueError("Unknown job submission schema.")
            from packages.execution.preparation import validate_input
            validate_input(payload["input"])
            request = {"schema_version": "openpine.job-request.2", "input_ref": self.server.jobs.freeze_input(payload["input"]),
                       "snapshot_id": payload["snapshot_sha256"], "timeout_seconds": payload["timeout_seconds"]}
            self.send(202, self.server.jobs.submit(request))
        elif match := re.fullmatch(r"/api/jobs/([0-9a-f]{32})/cancel", path):
            fields(payload, ())
            self.send(200, self.server.jobs.cancel(match[1]))
        elif match := re.fullmatch(r"/api/jobs/([0-9a-f]{32})/(pause|resume)",path):
            fields(payload,())
            self.send(200,getattr(self.server.jobs,match[2])(match[1]))
        else:
            self.send(404, {"diagnostics": [{"code": "NOT_FOUND", "message": "Unknown route."}]})


def main(argv=None):
    from apps.openpine.resources import arguments, policy, coordinator_policy
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="default: choose an available local port")
    parser.add_argument("--state-dir", type=Path, default=Path(".runtime"))
    arguments(parser)
    args = parser.parse_args(argv)
    with Server(("127.0.0.1", args.port), args.state_dir, policy=policy(args), coordinator_policy=coordinator_policy(args)) as server:
        print(f"OpenPine: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
