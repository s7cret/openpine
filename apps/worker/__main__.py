"""One job, one fresh process. No network access is needed during evaluation."""

import argparse
import os
import platform
import hashlib
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
from packages.storage.checkpoints import CheckpointStore
from packages.runtime.engine import program_hash


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
    plan = RunPlan.from_payload(request["run_plan"])
    if any(getattr(plan,k) != request[k] for k in ("input_ref","snapshot_id","timeout_seconds")):
        raise ValueError("Attempt request differs from its immutable run plan.")
    if (request.get("primary_context")!=(asdict(plan.primary_context) if plan.primary_context else None)
            or request.get("contexts",[])!=[asdict(c) for c in plan.contexts]):
        raise ValueError("Attempt context inputs differ from the saved run plan.")
    if request.get('settings')!=plan.settings:raise ValueError('Attempt execution settings differ from saved run plan.')
    if request.get('magnifier')!=plan.magnifier:raise ValueError('Attempt magnifier differs from saved run plan.')
    if request.get('evaluation_window')!=plan.evaluation_window:raise ValueError('Attempt evaluation window differs from saved run plan.')
    objects = ObjectStore(args.store / "objects", quota_bytes=request["storage_quota_bytes"])
    stopped, failed = threading.Event(), []
    pause_requested=threading.Event()
    paused_checkpoint=None
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
                if (path/"control.json").is_file():
                    control=read_json(path/"control.json",max_bytes=4096)
                    if control.keys()!={"attempt_id","pause"} or control["attempt_id"]!=request["attempt_id"] or control["pause"] is not True:
                        raise ValueError("Invalid pause control message.")
                    pause_requested.set()
            except (OSError,ValueError) as error:
                failed.append(error)
                return
            stopped.wait(.5)

    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    # Distinct lower history limits create separate readers even for one dataset.
    # Preserve the existing budget for jobs without context execution.
    from packages.contracts.lower_tf import io_budget
    budget_program=None
    try:
        budget_content=objects.get(request['input_ref'])
        if 'artifact_ref' in budget_content:budget_program=import_program(objects.get(budget_content['artifact_ref']))
    except (ValueError,KeyError,OpenPineError):pass  # Normal artifact diagnostics run below.
    io_mb=io_budget(budget_program,plan.memory_mb,len(plan.contexts),bool(plan.magnifier),plan.settings,primary=plan.primary_context) if plan.primary_context or plan.magnifier else min(128,plan.memory_mb//2)
    sink = ResultSink(objects, path, threads=plan.threads,
        memory_mb=io_mb)
    try:
        if io_mb<8:raise OpenPineError(Diagnostic("INPUT_INVALID","RESOURCE_POLICY_INCOMPATIBLE","Context readers require a larger worker memory budget."))
        if (plan.execution_sha256,plan.spec_lock_sha256) != plan_identity():
            raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE","PLAN_IMPLEMENTATION",
                "Saved run plan requires another computation implementation or specification; submit a new job."))
        manifest = objects.get(request["dataset_manifest_ref"])
        if manifest["snapshot_id"] != request["snapshot_id"]:
            raise ValueError("Worker dataset identity mismatch.")
        if plan.primary_context is not None and manifest["provenance"]["provider"]!=plan.primary_context.source:
            raise ValueError("Primary context source differs from saved run plan.")
        source = CandleSource(objects, manifest, threads=plan.threads,
            memory_mb=io_mb)
        refs=request.get("context_manifest_refs",[])
        if type(refs) is not list or len(refs)!=len(plan.contexts):raise ValueError("Missing requested dataset manifests.")
        context_sources=[]
        for binding,ref in zip(plan.contexts,refs,strict=True):
            selected=objects.get(ref)
            if selected["snapshot_id"]!=binding.snapshot_id or selected["provenance"]["provider"]!=binding.context.source:
                raise ValueError("Requested dataset/source differs from the saved run plan.")
            context_sources.append((binding,CandleSource(objects,selected,threads=plan.threads,memory_mb=io_mb)))
        lower_source=None
        if plan.magnifier:
            lower_manifest=objects.get(request['magnifier_manifest_ref'])
            if lower_manifest['snapshot_id']!=plan.magnifier['snapshot_id']:raise ValueError('Lower dataset differs from run plan.')
            lower_source=CandleSource(objects,lower_manifest,threads=plan.threads,memory_mb=io_mb)
        app = Application()
        content = objects.get(request["input_ref"])
        checkpoints=CheckpointStore(objects)
        restored=None
        if request["checkpoint_id"]:
            progress(0,source.identity.count,"restoring")
            try:
                saved,runtime_state,output_state=checkpoints.load(request["checkpoint_id"],plan=plan,source=source)
                artifact_ref=saved["artifact_ref"]
                program=import_program(objects.get(artifact_ref))
                if program_hash(program)!=saved["program_sha256"]:raise ValueError("Checkpoint program hash differs.")
                if "artifact_ref" in content and content["artifact_ref"]!=artifact_ref:raise ValueError("Checkpoint compiled input differs.")
                if "source" in content and hashlib.sha256(content["source"].encode("utf-8")).hexdigest()!=program.identity.source_sha256:
                    raise ValueError("Checkpoint source input differs.")
                restored=(runtime_state,output_state)
            except (ValueError,KeyError,TypeError,OSError) as error:
                raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE","CHECKPOINT_INVALID",str(error))) from error
        elif "source" in content:
            progress(0, source.identity.count, "compiling")
            compiled = app.handle(CompileRequest(content["source"], content["source_name"], HISTORICAL_PROFILE_ID))
            if compiled["status"] == "rejected":
                response = compiled
            else:
                program = import_program(compiled["artifact"])
        else:
            program = import_program(objects.get(content["artifact_ref"]))
        if restored is not None or "source" not in content or compiled["status"] != "rejected":
            if program.identity.implementation_profile_id != plan.profile_id:
                raise ValueError("Artifact profile differs from saved run plan.")
            artifact_ref=artifact_ref if restored is not None else content.get("artifact_ref") or objects.put(export_program(program))
            session=app.session(plan.profile_id)
            session.memory_mb=plan.memory_mb
            driver=session.stream_driver(program,source,sink,
                primary_context=plan.primary_context,context_sources=context_sources,simulation_policy=plan.simulation_policy,settings=plan.settings,evaluation_window=plan.evaluation_window,magnifier=plan.magnifier,lower_source=lower_source)
            if (driver.simulation is not None)!=(plan.simulation_policy is not None):raise ValueError('Strategy requires a pinned simulation plan.')
            if restored is not None:
                try:
                    if plan.magnifier or plan.simulation_policy and plan.simulation_policy.profile_id=='openpine.performance-broker.1':
                        sink.restore(restored[1],restored[0]['count']);driver.restore(restored[0])
                    else:driver.restore(restored[0]);sink.restore(restored[1],driver.evaluation.count)
                    if driver.simulation is not None:sink.validate_simulation(driver.simulation.broker,observation=driver.magnifier.active if driver.magnifier else None)
                    if list(sink.outputs)!=list(driver.evaluation.outputs):raise ValueError("Checkpoint output sites differ.")
                    for site,(title,t,_) in driver.evaluation.outputs.items():
                        if sink.outputs[site]["title"]!=title or sink.outputs[site]["value_type"]!=asdict(t):
                            raise ValueError("Checkpoint output contracts differ.")
                        if not driver.evaluation.matches(sink.outputs[site]["last"],t):
                            raise ValueError("Checkpoint last output value differs from its type.")
                except (ValueError,KeyError,TypeError,OSError) as error:
                    raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE","CHECKPOINT_INVALID",str(error))) from error
                restored=None  # Release the decoded envelope, keeping the restored buffers themselves.
            progress(driver.evaluation.count,source.identity.count)
            def save(evaluation):
                nonlocal paused_checkpoint
                progress(evaluation.count,source.identity.count,"checkpointing")
                paused_checkpoint=checkpoints.save(evaluation,sink,plan=plan,source=source,
                    artifact_ref=artifact_ref,program_sha256=program_hash(program))
                atomic_json(path/"checkpoint.json",{"attempt_id":request["attempt_id"],
                    "checkpoint_id":paused_checkpoint,"completed":evaluation.count})
                progress(evaluation.count,source.identity.count)
            complete=driver.run(progress=progress,cancelled=lambda:bool(failed),checkpoint=save,
                interval=plan.checkpoint_interval_bars,pause=pause_requested.is_set,pause_after=request["pause_after_bars"])
            if complete:
                summary=driver.finish_stream(source.identity)
                progress(source.identity.count, source.identity.count, "publishing")
                response = {"status": "executed", "python": platform.python_version(),
                    "artifact_ref":artifact_ref,"result":sink.finish(summary),
                    "program_sha256":summary["program_sha256"],"identity":summary["identity"],
                    "snapshot_sha256":request["snapshot_id"]}
            else:
                response={"status":"paused"}
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
    if response["status"]=="paused":
        atomic_json(path/"completion.json",{"attempt_id":request["attempt_id"],"status":"paused","checkpoint_id":paused_checkpoint})
        return 0
    response["run_plan_sha256"] = digest(plan.payload())
    response["build"] = build_identity()
    result_id = objects.put(response)
    atomic_json(path / "completion.json", {"attempt_id": request["attempt_id"], "result_id": result_id})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
