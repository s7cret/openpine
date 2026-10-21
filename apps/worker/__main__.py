"""One job, one fresh process. No network access is needed during evaluation."""

import argparse
import os
import platform
import threading
import time
from pathlib import Path

from packages.contracts import HISTORICAL_PROFILE_ID, Diagnostic, OpenPineError
from packages.contracts.application import CompileRequest
from dataclasses import asdict
from packages.contracts.storage import RunPlan
from packages.contracts.wire import digest
from packages.execution.identity import build_identity, plan_identity
from packages.contracts.wire import atomic_json, read_json
from packages.execution.artifacts import import_program, export_program
from packages.execution.service import Application, rejection
from packages.storage.objects import ObjectStore
from packages.storage.candles import CandleSource
from packages.storage.results import ResultSink


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
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args(argv)
    threading.Thread(target=watch_parent, args=(args.parent_pid,), daemon=True).start()
    path = args.directory

    request = read_json(path / "request.json")
    plan = RunPlan(**request["run_plan"])
    if any(getattr(plan,k) != request[k] for k in ("input_ref","snapshot_id","timeout_seconds")):
        raise ValueError("Attempt request differs from its immutable run plan.")
    objects = ObjectStore(args.store / "objects", quota_bytes=request["storage_quota_bytes"])
    stopped, failed = threading.Event(), []
    state_lock = threading.Lock()
    state = {"attempt_id": request["attempt_id"], "phase": "starting", "completed": 0}

    def progress(done, total, phase="executing"):
        with state_lock:
            state.update(completed=done, phase=phase)

    def heartbeat():
        while not stopped.is_set():
            try:
                with state_lock:
                    value = {**state, "heartbeat": time.time(), "tick": time.monotonic()}
                atomic_json(path / "heartbeat.json", value)
            except OSError as error:
                failed.append(error)
                return
            stopped.wait(.5)

    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    sink = ResultSink(objects, path, threads=plan.threads,
        memory_mb=min(128, plan.memory_mb // 2))
    try:
        if (plan.execution_sha256,plan.spec_lock_sha256) != plan_identity():
            raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE","PLAN_IMPLEMENTATION",
                "Saved run plan requires another computation implementation or specification; submit a new job."))
        manifest = objects.get(request["dataset_manifest_ref"])
        if manifest["snapshot_id"] != request["snapshot_id"]:
            raise ValueError("Worker dataset identity mismatch.")
        source = CandleSource(objects, manifest, threads=plan.threads,
            memory_mb=min(128, plan.memory_mb // 2))
        app = Application()
        content = objects.get(request["input_ref"])
        if "source" in content:
            progress(0, source.identity.count, "compiling")
            compiled = app.handle(CompileRequest(content["source"], content["source_name"], HISTORICAL_PROFILE_ID))
            if compiled["status"] == "rejected":
                response = compiled
            else:
                program = import_program(compiled["artifact"])
        else:
            program = import_program(objects.get(content["artifact_ref"]))
        if "source" not in content or compiled["status"] != "rejected":
            progress(0, source.identity.count)
            if program.identity.implementation_profile_id != plan.profile_id:
                raise ValueError("Artifact profile differs from saved run plan.")
            summary = app.session(plan.profile_id).execute_stream(
                program, source, sink, progress=progress, cancelled=lambda: bool(failed))
            progress(source.identity.count, source.identity.count, "publishing")
            response = {"status": "executed", "python": platform.python_version(),
                "artifact_ref": content.get("artifact_ref") or objects.put(export_program(program)), "result": sink.finish(summary),
                "program_sha256": summary["program_sha256"], "identity": summary["identity"],
                "snapshot_sha256": request["snapshot_id"]}
    except Exception as error:
        try:
            response = rejection(error)
        except Exception:
            response = {"status": "rejected", "diagnostics": [{"category": "INTERNAL_ERROR", "code": "WORKER_ERROR", "message": f"Unexpected worker failure: {type(error).__name__}"}]}
    finally:
        sink.close()
        stopped.set(); pulse.join(timeout=5)
    if failed:
        raise failed[0]
    response["run_plan_sha256"] = digest(asdict(plan))
    response["build"] = build_identity()
    result_id = objects.put(response)
    atomic_json(path / "completion.json", {"attempt_id": request["attempt_id"], "result_id": result_id})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
