"""Bind explanations to immutable preparations and verify inputs without execution."""
import time
from dataclasses import asdict
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.storage import RunPlan
from packages.contracts.wire import digest,canonical
from packages.execution.identity import plan_identity
from packages.execution.session import Session
from packages.runtime.engine import program_hash
from apps.openpine.passport import issue
from apps.openpine.replay import EmptySink


def instrument_metadata(evaluation,source):
    """Ask the existing pure reference reader in each actual bound context."""
    from types import SimpleNamespace
    from packages.contracts import ReferenceIR,BarFrame
    from packages.contracts.context_ir import ir_nodes,request_nodes
    from packages.runtime.environment import reference
    excluded={id(n) for request in request_nodes(evaluation.program) for n in ir_nodes((request.arguments[2].expression,))}
    nodes=[n for n in ir_nodes(evaluation.program.statements) if id(n) not in excluded and isinstance(n,ReferenceIR) and n.contract.slot.startswith('environment.syminfo.')]
    if not nodes:return
    bar=next(iter(source.rows(limit=1)))
    context=SimpleNamespace(environment=evaluation.environment,environment_phase='historical',program=evaluation.program,
        frame=BarFrame(source.identity.context_id,0,bar.time,bar.close,bar.open,bar.high,bar.low,bar.volume))
    for node in nodes:reference(context,node)


def binding(service,key):
    row=service.repository.get(key);ref=row['state']['prepared_ref']
    if ref is None:raise ValueError('Prepare pinned inputs before validating the launch.')
    prepared=service.manager.objects.get(ref);plan=RunPlan.from_payload(prepared['plan'])
    request=prepared['request']
    for name in ('input_ref','snapshot_id','timeout_seconds','settings','evaluation_window','magnifier'):
        if request.get(name)!=getattr(plan,name):raise ValueError('Prepared request differs from its immutable plan: '+name)
    if request.get('primary_context')!=(asdict(plan.primary_context) if plan.primary_context else None) or request.get('contexts',[])!=[asdict(b) for b in plan.contexts]:
        raise ValueError('Prepared context bindings differ from the plan.')
    value={'schema_version':'openpine.launch-binding.1','launch_id':key,'configuration_sha256':digest(row['config']),
        'prepared_ref':ref,'request_sha256':digest(request),'plan_sha256':digest(plan.payload()),
        'input_ref':plan.input_ref,'snapshot_ids':[plan.snapshot_id,*[b.snapshot_id for b in plan.contexts],*([plan.magnifier['snapshot_id']] if plan.magnifier else [])],
        'implementation_sha256':plan.execution_sha256,'spec_lock_sha256':plan.spec_lock_sha256}
    value['sha256']=digest(value)
    return value,prepared,plan


def validate(service,key,expected=None):
    value={'schema_version':'openpine.launch-validation.1','status':'blocked','checked_at':time.time(),
        'data_checked_now':False,'resources_reserved':False,'jobs_submitted':False,'issues':[],'datasets':[]}
    driver=None;phase='binding'
    try:
        pinned,prepared,plan=binding(service,key);value['binding']=pinned
        if expected is not None and expected!=pinned['sha256']:raise ValueError('Launch binding changed; prepare and check the selected configuration again.')
        if (plan.execution_sha256,plan.spec_lock_sha256)!=plan_identity():raise ValueError('Prepared plan requires another execution implementation or specification.')
        manager=service.manager;program=service.program(plan.input_ref);value['program_sha256']=program_hash(program)
        config=service.repository.get(key)['config']
        from packages.contracts.inputs import settings_for
        if (config['input_ref']!=plan.input_ref or config['timeout_seconds']!=plan.timeout_seconds
                or settings_for(program,config['inputs'])!=plan.settings
                or config.get('broker_settings')!=prepared['request'].get('broker_settings')
                or config.get('magnifier')!=plan.magnifier):raise ValueError('Selected launch configuration differs from the prepared inputs.')
        # Normalization already happened once in Preparation. Reuse its closed
        # request validator and the actual worker driver on these frozen inputs.
        from packages.execution.preparation import validate_request
        validate_request(prepared['request'])
        deadline=time.monotonic()+120
        def budget():
            if time.monotonic()>deadline:raise TimeoutError('Input validation exceeded its 120 second streaming budget; select a smaller range.')
        def checked_rows(rows):
            for i,bar in enumerate(rows):
                if i%1024==0:budget()
                yield bar
        phase='data_integrity'
        sources={}
        for sha in pinned['snapshot_ids']:
            if sha in sources:continue
            source=manager.market.source(sha,threads=1,memory_mb=32);sources[sha]=source
            count=0
            for count,_ in enumerate(checked_rows(source),1):pass
            value['datasets'].append({'snapshot_id':sha,'identity':asdict(source.identity),'rows_verified':count,'logical_identity_verified':True})
        from packages.contracts.marketdata import DataQuery
        from packages.marketdata.coverage import coverage_report
        phase='data_coverage'
        for row in prepared['datasets']:
            a,b=row['required_start_ms'],row['required_end_ms'];report=None
            if a<b:
                query=DataQuery(row['context']['symbol'],row['context']['timeframe'],a,b)
                report=coverage_report(query,checked_rows(sources[row['snapshot_id']].rows(a,b)),as_of=b)
                if not report['requested_complete'] and prepared['gap_policy']=='fail':raise ValueError('Pinned data coverage is incomplete.')
            next(d for d in value['datasets'] if d['snapshot_id']==row['snapshot_id'])['coverage']=report
        phase='driver_admission';session=Session(profile_id=HISTORICAL_PROFILE_ID,memory_mb=plan.memory_mb)
        driver=session.stream_driver(program,sources[plan.snapshot_id],EmptySink(),primary_context=plan.primary_context,
            context_sources=[(b,sources[b.snapshot_id]) for b in plan.contexts],simulation_policy=plan.simulation_policy,
            settings=plan.settings,evaluation_window=plan.evaluation_window,magnifier=plan.magnifier,
            lower_source=sources[plan.magnifier['snapshot_id']] if plan.magnifier else None)
        if (driver.simulation is not None)!=(plan.simulation_policy is not None):raise ValueError('Prepared strategy has no matching broker policy.')
        phase='instrument_metadata';instrument_metadata(driver.evaluation,sources[plan.snapshot_id])
        if driver.contexts:
            for group in driver.contexts.groups.values():
                for _,evaluation,_ in group.records.values():instrument_metadata(evaluation,group.source)
        phase='binding'
        if binding(service,key)[0]!=pinned or (plan.execution_sha256,plan.spec_lock_sha256)!=plan_identity():raise ValueError('Launch changed while inputs were being checked.')
        value.update(status='validated',data_checked_now=True,mode='magnifier' if plan.magnifier else 'history',
            instrument_metadata_checked=True,worker_admission='checked_by_supervisor_at_dispatch',
            validation_scope='pinned_input_bytes_logical_rows_coverage_and_actual_driver_constructor; no candle execution')
        from packages.contracts.strategy_cycle import PROFILE as CYCLE
        value['execution_profiles']={'broker':driver.simulation.broker.policy.profile_id if driver.simulation else None,
            'calculation':CYCLE if driver.cycle else None,'model':plan.magnifier['schema_version'] if plan.magnifier else None}
        if driver.magnifier:
            from packages.execution.magnifier_feed import coverage
            phase='magnifier_coverage'
            value['magnifier']=coverage(sources[plan.snapshot_id],sources[plan.magnifier['snapshot_id']],plan.magnifier['missing'],cancelled=lambda:budget() or False)
    except (OpenPineError,ValueError,OSError,KeyError,TypeError) as error:
        detail=issue(error,phase)
        if phase in ('data_integrity','data_coverage','instrument_metadata','magnifier_coverage'):detail['kind']='data'
        if isinstance(error,TimeoutError):detail['kind']='resources'
        value.update(status='blocked',data_checked_now=False,failure_phase=phase,issues=[detail])
    finally:
        if driver is not None:
            if driver.simulation:driver.simulation.close()
            if driver.contexts:driver.contexts.close()
    value['receipt_sha256']=digest(value)
    return value


def execution(service,key):
    """Read committed execution evidence; never poll or advance a worker."""
    pinned,prepared,plan=binding(service,key);manager=service.manager
    value={'schema_version':'openpine.launch-execution.1','binding':pinned,'whole_program_execution_verified':False,'issues':[]}
    try:state=manager.repository.get(key)
    except KeyError:return {**value,'status':'not_submitted'}
    value.update(status=state['status'],job_id=key,completed=state['completed'],total=state['total'],attempts=state['attempt_count'],error=state.get('error'))
    if state['status']!='succeeded':return value
    try:
        if manager.repository.plan(key)!=plan or canonical(manager.repository.request(key))!=canonical(prepared['request']):raise ValueError('Execution differs from the prepared binding.')
        result=manager.objects.get(state['result_id'],max_bytes=32*1024*1024)
        program=service.program(plan.input_ref)
        if (result['status']!='executed' or result['run_plan_sha256']!=pinned['plan_sha256']
                or result['program_sha256']!=program_hash(program) or result['snapshot_sha256']!=plan.snapshot_id
                or state['completed']!=state['total']):raise ValueError('Committed result does not match this complete program execution.')
        from packages.storage.results import series_rows
        from packages.storage.trades import event_rows
        summary=result['result'];deadline=time.monotonic()+120
        if summary['observation_count']!=state['total'] or summary['program_sha256']!=result['program_sha256']:raise ValueError('Result summary belongs to another program or completed range.')
        rows=0
        for i,_ in enumerate(series_rows(manager.objects,summary)):
            rows+=1
            if i%1024==0 and time.monotonic()>deadline:raise ValueError('Result validation exceeded its 120 second budget.')
        if rows!=state['total']*len(summary['outputs']):raise ValueError('Committed output rows do not cover the completed program.')
        simulation=summary.get('simulation')
        if simulation:
            for _ in event_rows(manager.objects,simulation['journal']):
                if time.monotonic()>deadline:raise ValueError('Result validation exceeded its 120 second budget.')
        value.update(whole_program_execution_verified=True,result_id=state['result_id'],result_sha256=state['result_sha256'],
            program_sha256=result['program_sha256'],build=result.get('build'),checked_at=time.time(),
            scope='this_committed_program_plan_inputs_and_result; not TradingView equivalence')
    except (OpenPineError,ValueError,OSError,KeyError,TypeError) as error:
        value.update(status='invalid_result',issues=[issue(error,'execution_receipt')])
    value['receipt_sha256']=digest(value)
    return value
