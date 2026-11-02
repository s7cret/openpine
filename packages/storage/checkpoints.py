"""Immutable checkpoint roots; runtime and output payloads are stored separately."""

from dataclasses import asdict
from time import perf_counter

from packages.contracts.wire import digest
from .objects import identifier
from . import checkpoint_records as checkpoint_tree

SCHEMA="openpine.execution-checkpoint.1"


def validate_manifest(value,*,plan=None,total=None):
    names={"schema_version","run_plan_sha256","snapshot_id","dataset","artifact_ref",
        "program_sha256","runtime_ref","output_ref","completed","last_time"}
    if type(value) is not dict or value.keys()!=names or value["schema_version"] not in (SCHEMA,'openpine.execution-checkpoint.2'):
        raise ValueError("Unknown execution checkpoint schema.")
    if value['schema_version']=='openpine.execution-checkpoint.2' and plan is not None and plan.magnifier is None:raise ValueError('Active checkpoint requires a magnifier plan.')
    for key in ("run_plan_sha256","snapshot_id","artifact_ref","program_sha256","runtime_ref","output_ref"):
        identifier(value[key])
    if (type(value["completed"]) is not int or not (0 if value['schema_version']=='openpine.execution-checkpoint.2' else 1)<=value["completed"]<=10_000_000
            or not (value['last_time'] is None if value['completed']==0 else type(value['last_time']) is int and 0<=value['last_time']<2**63)
            or type(value["dataset"]) is not dict):raise ValueError("Invalid checkpoint cursor.")
    if total is not None and value["completed"]>total:raise ValueError("Checkpoint is beyond the input.")
    if plan is not None and (value["run_plan_sha256"]!=digest(plan.payload()) or value["snapshot_id"]!=plan.snapshot_id):
        raise ValueError("Checkpoint belongs to another run plan.")
    return value


class CheckpointStore:
    def __init__(self,objects):self.objects=objects

    def save(self,evaluation,sink,*,plan,source,artifact_ref,program_sha256,timings=None):
        if timings is None:
            return self._save(evaluation,sink,plan=plan,source=source,artifact_ref=artifact_ref,program_sha256=program_sha256)
        timings.update(state_capture_seconds=0.,output_capture_seconds=0.,object_encode_seconds=0.,object_write_seconds=0.,traversal_validation_seconds=0.,manifest_seconds=0.)
        class MeasuredObjects:
            def __getattr__(proxy,name):return getattr(self.objects,name)
            def put(proxy,value):return self.objects.put(value,timings=timings)
        measured=CheckpointStore(MeasuredObjects());started=perf_counter()
        try:return measured._save(evaluation,sink,plan=plan,source=source,artifact_ref=artifact_ref,program_sha256=program_sha256,timings=timings)
        finally:timings['total_seconds']=perf_counter()-started

    def _save(self,evaluation,sink,*,plan,source,artifact_ref,program_sha256,timings=None):
        if plan.magnifier:
            # Child checkpoints may be much more frequent than parent commits.
            # Seal immutable blocks instead of repeatedly copying a growing CSV.
            sink.flush()
            sink.trade_sink.flush()
        from packages.contracts.memory import job_memory
        limit=job_memory(plan.memory_mb)['restore_reserve_bytes']
        started=perf_counter()
        runtime=evaluation.checkpoint(streaming=True)
        captured=perf_counter()
        runtime_ref=checkpoint_tree.put(self.objects,runtime,limit_bytes=limit*3//4)
        del runtime
        output_started=perf_counter()
        output=evaluation.output_checkpoint(sink) if plan.magnifier else sink.checkpoint(evaluation.count)
        output_captured=perf_counter()
        output_ref=checkpoint_tree.put(self.objects,output,limit_bytes=limit//4)
        del output
        if timings is not None:
            timings['state_capture_seconds']=captured-started
            timings['output_capture_seconds']=output_captured-output_started
            timings['traversal_validation_seconds']=max(0.,perf_counter()-captured-output_captured+output_started-timings['object_encode_seconds']-timings['object_write_seconds'])
        manifest={"schema_version":'openpine.execution-checkpoint.2' if plan.magnifier else SCHEMA,"run_plan_sha256":digest(plan.payload()),
            "snapshot_id":plan.snapshot_id,"dataset":asdict(source.identity),"artifact_ref":artifact_ref,
            "program_sha256":program_sha256,"runtime_ref":runtime_ref,"output_ref":output_ref,
            "completed":evaluation.count,"last_time":evaluation.last_time}
        started=perf_counter()
        result=self.objects.put(manifest)
        if timings is not None:timings['manifest_seconds']=perf_counter()-started
        return result

    def manifest(self,checkpoint_id,*,plan=None,total=None):
        value=validate_manifest(self.objects.get(checkpoint_id),plan=plan,total=total)
        for key in ("runtime_ref","output_ref","artifact_ref"):self.objects.verify(value[key])
        return value

    def encoded_bytes(self,checkpoint_id):
        value=self.manifest(checkpoint_id);refs={checkpoint_id,value['runtime_ref'],value['output_ref']}
        for key in ('runtime_ref','output_ref'):checkpoint_tree.references(self.objects,value[key],refs.add)
        return sum(self.objects.path(sha).stat().st_size for sha in refs)

    def load(self,checkpoint_id,*,plan,source):
        value=self.manifest(checkpoint_id,plan=plan,total=source.identity.count)
        if value["dataset"]!=asdict(source.identity):raise ValueError("Checkpoint dataset identity differs.")
        from packages.contracts.memory import job_memory
        limit=job_memory(plan.memory_mb)['restore_reserve_bytes']
        runtime=checkpoint_tree.get(self.objects,value['runtime_ref'],limit_bytes=limit*3//4)
        # Runtime and output envelopes coexist until their atomic consumers run.
        output=checkpoint_tree.get(self.objects,value['output_ref'],limit_bytes=limit//4)
        if runtime["count"]!=value["completed"] or runtime["last_time"]!=value["last_time"]:
            raise ValueError("Checkpoint state cursor differs from its manifest.")
        return value,runtime,output
