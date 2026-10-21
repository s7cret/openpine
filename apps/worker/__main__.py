"""One job, one fresh process. No network access is needed during evaluation."""

import argparse
import os
import threading
import time
from pathlib import Path

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.application import CompileRequest
from packages.contracts.wire import atomic_json, canonical, read_json
from packages.execution.artifacts import import_program
from packages.execution.jobs import MAX_RESULT_BYTES, validate_request
from packages.execution.service import Application, rejection
from packages.marketdata.providers import snapshot_from_payload


def watch_parent(parent_pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        handle = api.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            os._exit(74)
        api.WaitForSingleObject(handle, 0xFFFFFFFF)
        os._exit(74)
    while True:
        if os.getppid() != parent_pid:
            os._exit(74)
        time.sleep(0.2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args(argv)
    threading.Thread(target=watch_parent, args=(args.parent_pid,), daemon=True).start()
    path = args.directory

    def progress(done, total, phase="executing"):
        atomic_json(path / "progress.json", {"phase": phase, "completed": done, "total": total})

    try:
        request = read_json(path / "request.json")
        validate_request(request)
        snapshot = snapshot_from_payload(request["snapshot"])
        app = Application()
        content = request["input"]
        if "source" in content:
            progress(0, len(snapshot.data.bars), "compiling")
            compiled = app.handle(CompileRequest(content["source"], content["source_name"], HISTORICAL_PROFILE_ID))
            if compiled["status"] == "rejected":
                response = compiled
            else:
                program = import_program(compiled["artifact"])
                progress(0, len(snapshot.data.bars))
                response = app.execute(program, data=snapshot.data, progress=progress)
        else:
            progress(0, len(snapshot.data.bars))
            response = app.execute(import_program(content["artifact"]), data=snapshot.data, progress=progress)
        response["snapshot_sha256"] = snapshot.sha256
        if len(canonical(response)) > MAX_RESULT_BYTES:
            response = {"status": "rejected", "diagnostics": [{"category": "PROFILE_UNCOVERED", "code": "RESULT_LIMIT", "message": "Result exceeds 32 MiB."}]}
    except Exception as error:
        try:
            response = rejection(error)
        except Exception:
            response = {"status": "rejected", "diagnostics": [{"category": "INTERNAL_ERROR", "code": "WORKER_ERROR", "message": f"Unexpected worker failure: {type(error).__name__}"}]}
    atomic_json(path / "result.json", response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
