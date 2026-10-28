"""Immutable checkpoint roots; runtime and output payloads are stored separately."""

from dataclasses import asdict

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

    def save(self,evaluation,sink,*,plan,source,artifact_ref,program_sha256):
        if plan.magnifier:
            # Child checkpoints may be much more frequent than parent commits.
            # Seal immutable blocks instead of repeatedly copying a growing CSV.
            sink.flush()
            sink.trade_sink.flush()
        from packages.contracts.memory import job_memory
        limit=job_memory(plan.memory_mb)['restore_reserve_bytes']
        runtime_ref=checkpoint_tree.put(self.objects,evaluation.checkpoint(streaming=True),limit_bytes=limit*3//4)
        output_ref=checkpoint_tree.put(self.objects,evaluation.output_checkpoint(sink) if plan.magnifier else sink.checkpoint(evaluation.count),limit_bytes=limit//4)
        manifest={"schema_version":'openpine.execution-checkpoint.2' if plan.magnifier else SCHEMA,"run_plan_sha256":digest(plan.payload()),
            "snapshot_id":plan.snapshot_id,"dataset":asdict(source.identity),"artifact_ref":artifact_ref,
            "program_sha256":program_sha256,"runtime_ref":runtime_ref,"output_ref":output_ref,
            "completed":evaluation.count,"last_time":evaluation.last_time}
        return self.objects.put(manifest)

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
