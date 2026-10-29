"""Read a pinned replay and verify its recorded tape without applying events."""
import time
from dataclasses import asdict
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.inputs import transport_values
from packages.contracts.contexts import MarketContext
from packages.contracts.trading import BrokerSettings
from packages.contracts.wire import digest,canonical
from packages.execution.artifacts import import_program
from packages.execution.session import Session
from packages.execution.realtime import ReplayEngine
from apps.openpine.replay import EmptySink
from apps.openpine.passport import describe,issue,rejected

def inspect(service,key,*,check=False,expected=None):
    repo=service.repository;row=repo.row(key);config=repo.config(key);state=repo.status(key)
    bound={'schema_version':'openpine.replay-binding.1','replay_id':key,'config_ref':row[0],
        'accepted_events':row[3],'input_chain':row[4],'input_ref':config['input_ref'],'artifact_ref':config['artifact_ref']}
    bound['sha256']=digest(bound);engine=None
    validation={'status':'not_checked','tape_bytes_checked':False,'resources_reserved':False,'jobs_submitted':False,'issues':[]}
    try:
        if expected is not None and expected!=bound['sha256']:raise ValueError('Replay configuration or accepted tape changed; check it again.')
        program=import_program(service.manager.objects.get(config['artifact_ref']))
        contexts=tuple(MarketContext.from_payload(c) for c in config['contexts'])
        primary=next(c for c in contexts if c.context_id==config['primary_id'])
        session=Session(profile_id=HISTORICAL_PROFILE_ID,memory_mb=config['memory_mb'])
        options={'execution_mode':'replay',**{k:config[k] for k in ('broker_settings','environments','event_profile','broker_profile','calculation_profile','cycle_settings','lower_tf') if k in config}}
        inputs=transport_values(config['settings']['values']) if config.get('settings') else {}
        report=describe(program,session=session,inputs=inputs,primary_context=asdict(primary),options=options)
        engine=ReplayEngine(session,program,contexts,config['primary_id'],EmptySink(),
            broker_settings=BrokerSettings.from_payload(config['broker_settings']) if config['broker_settings'] else None,
            profile=config['event_profile'],settings=config.get('settings'),environments=config.get('environments'),
            broker_profile=config.get('broker_profile'),calculation_profile=config.get('calculation_profile'),cycle_settings=config.get('cycle_settings'),lower_tf=config.get('lower_tf'))
        if engine.identity!=config['identity'] or engine.qualification()!=config['policy']:raise ValueError('Saved replay requires another implementation, specification or execution policy.')
        report['selection'].update(status='configuration_admitted',issues=[],execution_profiles={
            'event':engine.profile,'broker':engine.broker_profile,'calculation':engine.calculation_profile})
        saved=repo.checkpoint(key)
        execution={'scope':'committed_replay_prefix_only','applied_events':row[2]+1,'accepted_events':row[3],
            'checkpoint_ref':row[1],'prefix_recorded':saved is not None,'whole_program_execution_verified':False,
            'pending_events':row[3]-row[2]-1,'state_sha256':digest(state)}
        if check:
            chain=config['identity'];count=0;deadline=time.monotonic()+120
            for event in repo.events(key,0,row[3]):
                if time.monotonic()>deadline:raise ValueError('Tape verification exceeded its 120 second budget.')
                chain=digest({'previous':chain,'event':event.payload()});count+=1
            if count!=row[3] or chain!=row[4]:raise ValueError('Accepted tape differs from its recorded input chain.')
            if repo.row(key)!=row:raise ValueError('Replay advanced or accepted new events during inspection; repeat the check.')
            validation.update(status='tape_verified',tape_bytes_checked=True,events_verified=count,checked_at=time.time(),
                scope='recorded_event_bytes_sequence_and_chain; event transitions are checked during execution')
        report.update(replay_binding=bound,replay_validation=validation,replay_execution=execution)
        if repo.row(key)!=row:raise ValueError('Replay changed while its passport was being read; repeat the check.')
        report['readiness']['status']='replay_'+validation['status']
    except (OpenPineError,ValueError,OSError,KeyError,TypeError,StopIteration) as error:
        report=rejected(error,'replay_binding');validation.update(status='blocked',issues=[issue(error,'replay_binding')])
        report.update(replay_binding=bound,replay_validation=validation)
    finally:
        if engine is not None and engine.simulation:engine.simulation.close()
    report['replay_validation']['receipt_sha256']=digest(validation)
    return {'passport':report,'passport_json':canonical(report).decode('utf-8')}
