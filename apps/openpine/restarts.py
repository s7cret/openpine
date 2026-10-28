"""Explicit recompilation: a new immutable job, never a migrated checkpoint."""
import re
import uuid
from dataclasses import replace

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import canonical, digest, loads
from packages.execution.artifacts import export_program
from packages.execution.session import Session
from apps.openpine.diagnostics import _source, current_identity


def preview(manager, predecessor):
    with manager.lock, manager.limiter.slot('prepare'):
        state=manager.get(predecessor)
        if state['status'] in ('queued','running'):raise ValueError('Stop or pause the previous job before creating a replacement.')
        request=manager.request(predecessor);old=manager.plan(predecessor)
        source=_source(manager.objects,request['input_ref'])
        if source is None:raise ValueError('SOURCE_REQUIRED: the saved artifact has no original source.')
        session=Session(profile_id=HISTORICAL_PROFILE_ID,memory_mb=old.memory_mb)
        program=session.compile(source['source'],source['source_name']);session.validate_program(program)
        ref=manager.freeze_input({'artifact_ref':manager.objects.put(export_program(program)),
                                  'source_ref':manager.objects.put(source)})
        selected={**request,'input_ref':ref}
        if request.get('settings'):
            from packages.contracts.inputs import settings_for
            selected['settings']=settings_for(program,request['settings']['values'])
        selected=manager.preparation.freeze(selected)
        policy=ResourcePolicy(workers=1,threads_per_worker=old.threads,memory_mb_per_worker=old.memory_mb,
                              total_memory_mb=old.memory_mb,max_attempts=old.max_attempts,heartbeat_timeout=old.heartbeat_timeout)
        plan=replace(manager.preparation.build_plan(selected,policy),checkpoint_interval_bars=old.checkpoint_interval_bars)
        if plan.memory_mb>manager.policy.memory_mb_per_worker or plan.threads>manager.policy.threads_per_worker:
            raise ValueError('RESOURCE_POLICY_INCOMPATIBLE: configure the host for the saved run policy.')
        changes=[{'field':key,'saved':old.payload().get(key),'current':plan.payload().get(key)}
                 for key in sorted(set(old.payload())|set(plan.payload())) if old.payload().get(key)!=plan.payload().get(key)]
        value={'schema_version':'openpine.restart-preview.1','predecessor_id':predecessor,
               'previous_request_sha256':digest(request),'previous_plan_sha256':digest(old.payload()),
               'request':selected,'plan':plan.payload(),'changes':changes,'identity':current_identity(),
               'reuses_checkpoint':False,'starts_at_bar':0}
        return {**value,'preview_id':digest(value)}


def apply(manager, predecessor, *, preview_id, request_id):
    if type(request_id) is not str or not re.fullmatch('[0-9a-f]{32}',request_id):raise ValueError('Use a 32-hex restart request ID.')
    if type(preview_id) is not str or not re.fullmatch('[0-9a-f]{64}',preview_id):raise ValueError('A preview ID is required.')
    with manager.lock:
        rows=manager.db.execute('SELECT receipt,receipt_sha256,deleted FROM job_lineage WHERE predecessor_id=? AND request_id=?',(predecessor,request_id))
        if rows:
            value=loads(rows[0][0])
            if digest(value)!=rows[0][1] or value['preview_id']!=preview_id:raise ValueError('Restart key conflicts with its saved receipt.')
            if bool(manager.db.execute('SELECT 1 FROM jobs WHERE job_id=?',(value['job_id'],)))==bool(rows[0][2]):
                raise ValueError('Restart target differs from its deletion marker.')
            if rows[0][2]:raise ValueError('RESTART_GONE: the replacement was explicitly pruned; its request ID remains reserved.')
            return value
        value=preview(manager,predecessor)
        if value['preview_id']!=preview_id:raise ValueError('RESTART_PREVIEW_STALE: obtain a new preview.')
        from packages.contracts.storage import RunPlan
        identifier=uuid.uuid4().hex
        receipt={'schema_version':'openpine.restart-receipt.1','job_id':identifier,'predecessor_id':predecessor,
                 'request_id':request_id,'preview_id':preview_id,'changes':value['changes'],
                 'previous_request_sha256':value['previous_request_sha256'],'previous_plan_sha256':value['previous_plan_sha256'],
                 'request_sha256':digest(value['request']),'plan_sha256':digest(value['plan']),
                 'identity':value['identity'],'reuses_checkpoint':False}
        with manager.db.transaction():
            # Repository.insert nests a savepoint; job, plan and lineage are
            # published by this one outer commit.
            manager.enqueue_prepared(value['request'],RunPlan.from_payload(value['plan']),job_id=identifier)
            manager.db.execute('INSERT INTO job_lineage(job_id,predecessor_id,request_id,preview_id,receipt,receipt_sha256) VALUES(?,?,?,?,?,?)',
                               (identifier,predecessor,request_id,preview_id,canonical(receipt),digest(receipt)))
        return receipt
