"""Explain bound programs through existing preparation and execution admission."""
from dataclasses import asdict
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ReferenceIR,Diagnostic
from packages.contracts.context_ir import ir_nodes,request_nodes,request_expression
from packages.contracts.contexts import MarketContext
from packages.contracts.inputs import settings_for
from packages.contracts.wire import digest
from packages.execution.identity import implementation_hash
from packages.execution.session import Session
from packages.runtime.engine import program_hash
from packages.compiler.semantic.specification import SpecError

MODES=('history','replay','observed','intrabar','cycle','magnifier')
OPTIONS={'broker_settings','environments','lower_tf','cycle_settings','magnifier','lower_context',
    'execution_mode','event_profile','broker_profile','calculation_profile'}

def issue(error,stage):
    if isinstance(error,OpenPineError):
        diagnostic=asdict(error.diagnostic);code=diagnostic['code'];category=diagnostic['category']
        kind=('resources' if any(s in code for s in ('RESOURCE','MEMORY','DISK','COORDINATOR_BUSY')) else
              'version' if 'VERSION' in code else 'language' if category in ('LANGUAGE_INVALID','PROFILE_UNCOVERED') and stage=='compile' else 'mode_configuration')
    elif isinstance(error,SpecError):
        from packages.execution.service import rejection
        diagnostic=rejection(error)['diagnostics'][0];kind='specification'
    else:
        diagnostic={'category':'INPUT_INVALID','code':'PASSPORT_CONFIGURATION','message':str(error),'span':None}
        kind='data' if isinstance(error,OSError) else 'configuration'
    return {'kind':kind,'stage':stage,'diagnostic':diagnostic}

def rejected(error,stage):
    detail=issue(error,stage)
    return {'schema_version':'openpine.program-passport.1','status':'rejected','issues':[detail],
        'diagnostics':[detail['diagnostic']],'readiness':{'status':'blocked','jobs_submitted':False,'resources_reserved':False}}

def explain(program,**kwargs):
    try:return describe(program,**kwargs)
    except (OpenPineError,SpecError,ValueError,OSError) as error:return rejected(error,'preparation')

def validate_options(options):
    if options is None:return {}
    if type(options) is not dict or options.keys()-OPTIONS:raise ValueError('Unknown passport mode option.')
    if 'execution_mode' in options and options['execution_mode'] not in ('history','replay','magnifier'):raise ValueError('Unknown selected execution mode.')
    if len(str(options))>65536:raise ValueError('Passport mode options exceed 64 KiB.')
    return options

def contexts_for(requirements,primary):
    if primary is None:return ()
    selected=primary if isinstance(primary,MarketContext) else MarketContext.from_payload(primary)
    contexts=[selected];pairs={(selected.symbol,selected.timeframe)}
    for row in requirements['contexts']:
        pair=(row['symbol'],row['timeframe'])
        if pair in pairs:continue
        name='passport.request.'+str(len(contexts))
        while name==selected.context_id:name+='x'
        contexts.append(MarketContext(name,*pair,selected.source));pairs.add(pair)
    return tuple(contexts)

def mode_report(session,program,requirements,settings,contexts,mode,options,*,use_selection=False):
    result={'mode':mode,'status':'needs_configuration','data_checked':False,'resources_reserved':False,'issues':[],
        'option_scope':'Only applied_options were used for this mode; remaining options are not silently treated as validated.',
        'applied_options':[],'unused_options':sorted(options),
        'unvalidated':['snapshot_coverage','runtime_resource_admission','event_tape_or_historical_input']}
    if not contexts or requirements.get('needs_primary'):
        result['issues']=[{'kind':'data','stage':'metadata','code':'PRIMARY_CONTEXT_REQUIRED','message':'Select primary symbol and timeframe metadata.'}];return result
    try:
        if use_selection and mode in ('history','magnifier') and any(k in options for k in ('event_profile','broker_profile','calculation_profile','environments','lower_tf')):
            raise ValueError('Replay options require selected replay execution.')
        if use_selection and mode=='history' and options.get('cycle_settings'):
            raise ValueError('Historical cycle flags come from the program; external overrides require Magnifier.')
        if use_selection and mode=='replay' and options.get('magnifier'):
            raise ValueError('Magnifier requires historical execution.')
        from packages.contracts.strategy_ir import broker_policy
        from packages.contracts.trading import BrokerSettings,STRATEGY_DECLARATIONS
        from packages.contracts.magnifier import admit,minute_pair
        from packages.contracts.calendar import requestable
        from packages.contracts.lower_tf import requestable as lower_requestable
        selected=BrokerSettings.from_payload(options['broker_settings']) if options.get('broker_settings') is not None else None
        policy=broker_policy(program,selected)
        applied={'broker_settings'} if 'broker_settings' in options else set()
        if use_selection and 'execution_mode' in options:applied.add('execution_mode')
        if mode in ('history','magnifier'):
            from packages.contracts.strategy_cycle import configuration
            supplied=options.get('magnifier') if mode=='magnifier' else None
            requested_flags=supplied.get('cycle_settings') if type(supplied) is dict else options.get('cycle_settings') if mode=='history' else None
            calculation,flags=configuration(program,None,requested_flags)
            applied.update(({'cycle_settings'} if 'cycle_settings' in options else set()) if mode=='history' else ({'magnifier','lower_context'}&options.keys()))
            if mode=='magnifier' and program.statements[0].operation not in STRATEGY_DECLARATIONS:
                raise ValueError('Bar magnifier requires a strategy.')
            if mode=='magnifier' and supplied is None:
                result['issues']=[{'kind':'data','stage':'metadata','code':'LOWER_SNAPSHOT_REQUIRED','message':'Select a pinned lower snapshot and magnifier configuration.'}];return result
            admit(program,supplied)
            for row in requirements['contexts']:
                for request in row['requests']:
                    valid=lower_requestable(contexts[0].timeframe,row['timeframe']) if request.get('kind')=='lower_tf' else requestable(contexts[0].timeframe,row['timeframe'])
                    if not valid and not request.get('ignore_invalid_timeframe'):
                        node=next((n for n in request_nodes(program) if n.call_site_id==request['call_site_id']),None)
                        raise OpenPineError(Diagnostic('PROFILE_UNCOVERED','PASSPORT_CONTEXT_INTERVAL','Requested interval is incompatible with primary metadata.',node.span if node else None,language_version=program.identity.language_version))
            if mode=='magnifier':
                child=options.get('lower_context')
                if child is None:
                    result['issues']=[{'kind':'data','stage':'metadata','code':'LOWER_CONTEXT_REQUIRED','message':'Provide lower snapshot context metadata.'}];return result
                child=MarketContext.from_payload(child);minute_pair(contexts[0].timeframe,child.timeframe)
                result['unvalidated'].extend(['matching_parent_OHLCV','magnifier_requested_visibility'])
            result.update(status='configuration_admitted',execution_profiles={'broker':getattr(policy,'profile_id',None),'calculation':calculation},calculation_flags=flags)
            if mode=='magnifier':result['status']='requires_bound_plan';result['execution_profiles']['model']=supplied['schema_version']
        else:
            from packages.execution.realtime import ReplayEngine
            from packages.contracts.observed_broker import PROFILE as OBSERVED
            from packages.contracts.intrabar import PROFILE as INTRABAR
            from packages.contracts.strategy_cycle import PROFILE as CYCLE
            from apps.openpine.replay import EmptySink
            kwargs={k:options[k] for k in ('environments','lower_tf') if k in options}
            applied.update(kwargs)
            if mode=='observed':kwargs['broker_profile']=OBSERVED
            if mode in ('intrabar','cycle'):kwargs['calculation_profile']=INTRABAR if mode=='intrabar' else CYCLE
            if mode=='cycle' and 'cycle_settings' in options:kwargs['cycle_settings']=options['cycle_settings'];applied.add('cycle_settings')
            if use_selection:
                for key,target in (('event_profile','profile'),('broker_profile','broker_profile'),('calculation_profile','calculation_profile'),('cycle_settings','cycle_settings')):
                    if key in options:kwargs[target]=options[key];applied.add(key)
            engine=ReplayEngine(session,program,contexts,contexts[0].context_id,EmptySink(),broker_settings=selected,settings=settings,**kwargs)
            try:
                result.update(status='configuration_admitted',execution_profiles={'event':engine.profile,'broker':engine.broker_profile,'calculation':engine.calculation_profile},
                    qualification=engine.qualification())
                if engine.lower_config:result['execution_profiles']['lower']=engine.lower_config['schema_version']
            finally:
                if engine.simulation:engine.simulation.close()
        result.update(applied_options=sorted(applied),unused_options=sorted(options.keys()-applied))
        return result
    except (OpenPineError,ValueError) as error:
        result.update(status='rejected_configuration',issues=[issue(error,mode)]);return result

def describe(program,*,session=None,inputs=None,primary_context=None,options=None):
    options=validate_options(options);session=session or Session(profile_id=HISTORICAL_PROFILE_ID)
    session.validate_program(program)
    selected=settings_for(program,inputs);identity=program_hash(program)
    from .launches import requirements_for
    requirements=requirements_for(program,identity,inputs,primary_context=primary_context)
    contexts=contexts_for(requirements,primary_context)
    nodes=list(ir_nodes(program.statements))
    from .capability_operations import operations as operation_features
    operations=sorted(set().union(*(operation_features(n) for n in program.statements)))
    references=sorted({n.contract.slot for n in nodes if isinstance(n,ReferenceIR)})
    calls=[{'operation':getattr(n,'operation','function.call'),'call_site_id':n.call_site_id,'span':asdict(n.span)} for n in nodes if getattr(n,'call_site_id',None) and getattr(n,'span',None)]
    from .capability_inventory import profile_inventory,fixture_evidence,overlay_profiles
    from packages.contracts.history_storage import PROFILE as HISTORY
    from packages.contracts.request_environment import PROFILE as ENVIRONMENT,policy
    profiles=set(program.identity.semantic_profile_id.split('+'))|{HISTORY}|overlay_profiles(program)
    requested_slots=set()
    for request in request_nodes(program):
        expression=request.arguments[2].expression
        dependencies,_,_=request_expression(program,expression)
        requested_slots.update(n.contract.slot for n in ir_nodes((*dependencies,expression)) if isinstance(n,ReferenceIR))
    if requested_slots&{'bar_index',*(s for s in references if s.startswith('environment.'))}:profiles.add(ENVIRONMENT)
    implementation=implementation_hash()
    metadata=[s.removeprefix('environment.syminfo.') for s in references if s.startswith('environment.syminfo.') and s.split('.')[-1] in ('mintick','pointvalue','currency','basecurrency','type')]
    modes=[mode_report(session,program,requirements,selected,contexts,mode,options) for mode in MODES]
    selection=mode_report(session,program,requirements,selected,contexts,options['execution_mode'],options,use_selection=True) if 'execution_mode' in options else None
    execution_profiles={p for mode in modes for p in mode.get('execution_profiles',{}).values() if p}
    if selection:execution_profiles.update(p for p in selection.get('execution_profiles',{}).values() if p)
    return {'schema_version':'openpine.program-passport.1','status':'described',
        'program':{'sha256':identity,'language_version':program.identity.language_version,'semantic_profile_id':program.identity.semantic_profile_id,
            'implementation_sha256':implementation,'spec_lock_sha256':digest(session.snapshot.lock)},
        'features':{'operations':operations,'references':references,'call_sites':calls[:256],'call_sites_total':len(calls),'call_sites_truncated':len(calls)>256},
        'admission_profiles':sorted(profiles),'selected_execution_profiles':sorted(execution_profiles),
        'profile_inventory':[p for p in profile_inventory(session) if p['profile_id'] in profiles|execution_profiles],
        'profile_scope':'IR admission identities and selected execution contracts; not all catalogue features are used',
        'requirements':requirements,'settings':selected,'mode_options':options,'planning_contexts':[asdict(c) for c in contexts],
        'instrument_metadata_required':metadata,'instrument_metadata_checked':False,
        'modes':modes,'selection':selection,
        'evidence':{'admitted_by_current_compiler':True,'imported_reference_is_execution':False,
            'recorded_corpora':fixture_evidence(program.identity.language_version,operations+['context.'+s for s in references],implementation),'whole_program_execution_verified':False,
            'compatibility_notes':list(program.identity.compatibility_notes),
            'request_environment':policy(program.identity.language_version) if ENVIRONMENT in profiles else None,
            'tradingview_verified':False},
        'readiness':{'status':'data_not_checked','jobs_submitted':False,'resources_reserved':False}}
