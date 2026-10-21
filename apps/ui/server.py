"""Loopback HTTP adapter. Calculation belongs to execution and isolated workers."""

import argparse
import hmac
import platform
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.application import CompileRequest
from packages.contracts.marketdata import DataQuery
from packages.contracts.wire import canonical, loads
from packages.execution.jobs import JobManager
from packages.execution.service import Application, rejection
from packages.marketdata.binance import BinanceProvider
from packages.marketdata.providers import FileProvider, SnapshotStore, input_snapshot, snapshot_payload

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"


def fields(payload, names):
    if type(payload) is not dict or payload.keys() != set(names):
        raise OpenPineError(Diagnostic("INPUT_INVALID", "REQUEST_INVALID", "Missing or extra request fields."))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, directory):
        if address[0] not in ("127.0.0.1", "localhost"):
            raise ValueError("The local application must bind to loopback.")
        super().__init__(address, Handler)
        self.token = secrets.token_urlsafe(32)
        self.app = Application()
        self.compile_lock = threading.Lock()
        self.store = SnapshotStore(Path(directory) / "snapshots")
        try:
            self.jobs = JobManager(Path(directory) / "jobs")
        except Exception:
            super().server_close()
            raise

    def server_close(self):
        if hasattr(self, "jobs"):
            self.jobs.close()
        super().server_close()


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
        except (BrokenPipeError, ConnectionResetError):
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
        except OpenPineError as error:
            code = error.diagnostic.code
            status = 404 if code == "JOB_NOT_FOUND" else 409 if code in ("RESULT_UNAVAILABLE", "JOB_STORE_BUSY", "JOB_QUEUE_FULL") else 400
            self.send(status, rejection(error))
        except (OSError, ValueError, TypeError, KeyError) as error:
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
            self.send(200, snapshot_payload(self.server.store.load(match[1])))
        elif path == "/api/jobs":
            self.send(200, self.server.jobs.list())
        elif match := re.fullmatch(r"/api/jobs/([0-9a-f]{32})(/(result|request))?", path):
            action = match[3]
            value = self.server.jobs.result(match[1]) if action == "result" else self.server.jobs.request(match[1]) if action == "request" else self.server.jobs.get(match[1])
            self.send(200, value)
        else:
            self.send(404, {"diagnostics": [{"code": "NOT_FOUND", "message": "Unknown route."}]})

    def post(self, path, payload):
        if path == "/api/compile":
            fields(payload, ("source", "source_name"))
            with self.server.compile_lock:
                self.send(200, self.server.app.handle(CompileRequest(payload["source"], payload["source_name"])))
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
                snapshot = BinanceProvider().fetch(DataQuery(**payload["query"]))
            else:
                raise ValueError("Unknown historical provider.")
            self.server.store.save(snapshot)
            self.send(201, {"sha256": snapshot.sha256, "bars": len(snapshot.data.bars)})
        elif path == "/api/jobs":
            fields(payload, ("schema_version", "input", "snapshot_sha256", "timeout_seconds"))
            if payload["schema_version"] != "openpine.job-submission.1":
                raise ValueError("Unknown job submission schema.")
            snapshot = self.server.store.load(payload["snapshot_sha256"])
            request = {"schema_version": "openpine.job-request.1", "input": payload["input"],
                       "snapshot": snapshot_payload(snapshot), "timeout_seconds": payload["timeout_seconds"]}
            self.send(202, self.server.jobs.submit(request))
        elif match := re.fullmatch(r"/api/jobs/([0-9a-f]{32})/cancel", path):
            fields(payload, ())
            self.send(200, self.server.jobs.cancel(match[1]))
        else:
            self.send(404, {"diagnostics": [{"code": "NOT_FOUND", "message": "Unknown route."}]})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="default: choose an available local port")
    parser.add_argument("--state-dir", type=Path, default=Path(".runtime"))
    args = parser.parse_args(argv)
    with Server(("127.0.0.1", args.port), args.state_dir) as server:
        print(f"OpenPine: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
