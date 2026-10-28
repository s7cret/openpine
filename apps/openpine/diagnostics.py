"""Application-owned compatibility decisions over read-only storage snapshots."""
import platform
import shutil
import re
from importlib.metadata import version
from pathlib import Path
import apsw

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.storage import RunPlan
from packages.contracts.wire import digest, loads, read_json, canonical
from packages.execution.identity import plan_identity, build_identity
from packages.storage.database import SCHEMA_VERSION, SQLITE_VERSION
from packages.storage.inspection import snapshot, schema_report, ReadObjects, lock_state

KINDS = ('jobs','programs','replays','live','research','holdouts','launches')


def current_identity():
    execution, spec = plan_identity()
    return {'execution_sha256':execution,'spec_lock_sha256':spec,'build':build_identity()}


def mapping_identity():
    import hashlib
    from packages.marketdata.live_mapping import implementation_identity
    return digest({'mapping':implementation_identity(),'application':{
        name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('live_service.py','live_feeds.py','live_contexts.py')}})


def doctor(root, *, full=False):
    root = Path(root).resolve()
    report = {'schema_version':'openpine.doctor.1','status':'ok','mode':'full' if full else 'quick',
              'python':platform.python_version(),'expected_python':'3.14.8',
              'dependencies':{name:version(name) for name in ('apsw','duckdb','jsonschema')},
              'identity':current_identity(),'issues':[],'writes_source':False}
    if report['python'] != '3.14.8': report['issues'].append({'code':'PYTHON_VERSION','action':'install_pinned_python'})
    if report['dependencies']['apsw'] != SQLITE_VERSION+'.0': report['issues'].append({'code':'SQLITE_VERSION','action':'install_pinned_dependencies'})
    if report['dependencies']['duckdb'] != '2.0.0.dev2610192346': report['issues'].append({'code':'DUCKDB_VERSION','action':'install_pinned_dependencies'})
    if not root.is_dir():
        report.update(status='attention'); report['issues'].append({'code':'STORE_MISSING','action':'choose_existing_store'}); return report
    report['disk_free_bytes'] = shutil.disk_usage(root).free
    report['ownership'] = lock_state(root/'.manager.lock')
    receipt = root/'.upgrade.json'
    if receipt.exists():
        value=read_json(receipt)
        report['upgrade']={k:value.get(k) for k in ('phase','from_schema','to_schema')}
        if value.get('phase') != 'complete': report['issues'].append({'code':'UPGRADE_INCOMPLETE','action':'resume_upgrade'})
    try:
        with snapshot(root) as db:
            report['catalog'] = schema_report(db)
            db.check(full=full); report['catalog']['integrity']='ok'
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            report['counts']={name:db.execute('SELECT count(*) FROM '+name)[0][0] for name in
                              ('jobs','datasets','results','checkpoints','replays','live_sessions','experiments','searches','walkforwards') if name in tables}
            if db.version != SCHEMA_VERSION:
                report['issues'].append({'code':'CATALOG_NEWER' if db.version > SCHEMA_VERSION else 'UPGRADE_REQUIRED','action':report['catalog']['action']})
            if full and db.version == SCHEMA_VERSION:
                from packages.storage.lifecycle import reachable
                report['verified_reachable_objects']=len(reachable(db,ReadObjects(root/'objects')))
            elif full:
                report['reference_audit']='requires_matching_schema_installation'
    except (ValueError,OSError,apsw.Error) as error:
        report['issues'].append({'code':'INSPECTION_FAILED','detail':str(error),'action':'inspect_storage'})
    if report['issues']: report['status']='attention'
    return report


def _source(objects, input_ref):
    content=objects.get(input_ref)
    source=objects.get(content['source_ref']) if 'source_ref' in content else content
    if type(source) is dict and source.keys()=={'source','source_name'}:
        from packages.execution.preparation import validate_input
        validate_input(source)
        if 'artifact_ref' in content:
            import hashlib
            artifact=objects.get(content['artifact_ref'])
            if artifact.get('ir',{}).get('identity',{}).get('source_sha256')!=hashlib.sha256(source['source'].encode('utf-8')).hexdigest():
                raise ValueError('SOURCE_IDENTITY: saved source differs from the compiled artifact.')
        return source
    return None


class Compatibility:
    def __init__(self, db, objects, *, identity=None):
        self.db,self.objects=db,objects
        self.identity=identity or current_identity()
        self._session=None

    def plan(self, raw, sha):
        value=loads(raw); plan=RunPlan.from_payload(value)
        if digest(value)!=sha: raise ValueError('Run plan hash mismatch.')
        changes=[{'field':key,'saved':value[key],'current':self.identity[key]} for key in
                 ('execution_sha256','spec_lock_sha256') if value[key]!=self.identity[key]]
        return plan,changes

    def program(self, ref):
        from packages.execution.artifacts import import_program
        from packages.execution.session import Session
        payload=self.objects.get(ref)
        program=import_program(payload)
        if self._session is None: self._session=Session(profile_id=HISTORICAL_PROFILE_ID)
        self._session.validate_program(program)
        return payload

    def replay(self, identifier):
        from apps.openpine.replay import EmptySink
        from packages.contracts.contexts import MarketContext
        from packages.contracts.trading import BrokerSettings
        from packages.execution.artifacts import import_program
        from packages.execution.realtime import ReplayEngine
        from packages.execution.session import Session
        ref,checkpoint=self.db.execute('SELECT config_ref,checkpoint_ref FROM replays WHERE replay_id=?',(identifier,))[0]
        config=self.objects.get(ref);payload=self.program(config['artifact_ref'])
        session=Session(profile_id=HISTORICAL_PROFILE_ID,memory_mb=config['memory_mb'])
        engine=ReplayEngine(session,import_program(payload),tuple(MarketContext.from_payload(c) for c in config['contexts']),config['primary_id'],EmptySink(),
            broker_settings=BrokerSettings.from_payload(config['broker_settings']) if config['broker_settings'] else None,
            profile=config['event_profile'],settings=config.get('settings'),environments=config.get('environments'),
            broker_profile=config.get('broker_profile'),calculation_profile=config.get('calculation_profile'),cycle_settings=config.get('cycle_settings'),lower_tf=config.get('lower_tf'))
        try:
            changed=config['identity']!=engine.identity or config['policy']!=engine.qualification()
            return {'status':'new_session_required' if changed else 'compatible','code':'REPLAY_POLICY_CHANGED' if changed else 'REPLAY_IDENTITY_MATCH',
                    'action':'new_replay' if changed else 'continue_replay','checkpoint_id':checkpoint,
                    'scope':'program_and_execution_policy; checkpoint payload is validated by replay worker'}
        finally:
            if engine.simulation:engine.simulation.close()

    def job(self, identifier):
        from packages.storage.jobs import JobRepository
        repo=JobRepository(self.db); state=repo.get(identifier); request=repo.request(identifier)
        rows=self.db.execute('SELECT plan,plan_id FROM run_plans WHERE job_id=?',(identifier,))
        if not rows: raise ValueError('Saved job has no pinned plan.')
        plan,changes=self.plan(*rows[0])
        if any(getattr(plan,k)!=request[k] for k in ('input_ref','snapshot_id','timeout_seconds')):
            raise ValueError('Saved request differs from plan.')
        source=_source(self.objects,request['input_ref'])
        checkpoint=self.db.execute('SELECT checkpoint_id,completed FROM checkpoints WHERE job_id=?',(identifier,))
        if checkpoint:
            from packages.storage.checkpoints import validate_manifest
            cp=validate_manifest(self.objects.get(checkpoint[0][0]),plan=plan)
            if cp['completed']!=checkpoint[0][1]:raise ValueError('Checkpoint cursor mismatch.')
        action='new_run' if changes and source else 'provide_source' if changes else 'resume' if state['status']=='paused' else 'wait' if state['status'] in ('queued','running') else 'view_result' if state['result_id'] else 'inspect_failure'
        error=state.get('error')
        if error is not None and type(error) is not dict:raise ValueError('Invalid saved job error.')
        error_code=(error or {}).get('code')
        if error_code is not None and (type(error_code) is not str or re.fullmatch('[A-Z][A-Z0-9_]{0,80}',error_code) is None):error_code='UNCLASSIFIED'
        return {'id':identifier,'kind':'jobs','status':'recompile_required' if changes else 'compatible',
                'code':'PLAN_CHANGED' if changes else 'IDENTITY_MATCH','changes':changes,'action':action,
                'job_status':state['status'],'source_available':source is not None,
                'checkpoint':{'id':checkpoint[0][0],'completed':checkpoint[0][1]} if checkpoint else None,
                'result_id':state['result_id'],'error_code':error_code,
                'scope':'identity_and_manifest; runtime restore is validated when executed'}

    def page(self, kind='jobs', start=0, limit=50):
        if kind not in KINDS or type(start) is not int or start<0 or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Invalid compatibility page.')
        sources={'jobs':('jobs','job_id'),'programs':('pins','object_id'),'replays':('replays','replay_id'),
                 'live':('live_sessions','session_id'),'research':('experiment_variants','candidate_id'),
                 'holdouts':('search_holdouts',"search_id || ':' || ordinal"),'launches':('launches','launch_id')}
        table,column=sources[kind]; where=" WHERE name LIKE 'input:%'" if kind=='programs' else ''
        total=self.db.execute('SELECT count(*) FROM '+table+where)[0][0]
        ids=[r[0] for r in self.db.execute('SELECT '+column+' FROM '+table+where+' ORDER BY '+column+' LIMIT ? OFFSET ?',(limit,start))]
        items=[]
        for identifier in ids:
            try:
                if kind=='jobs': row=self.job(identifier)
                elif kind=='programs':
                    content=self.objects.get(identifier)
                    if 'artifact_ref' in content: self.program(content['artifact_ref'])
                    row={'status':'compatible' if 'artifact_ref' in content else 'compile_on_run','code':'PROGRAM_ADMITTED' if 'artifact_ref' in content else 'SOURCE_ONLY','action':'compile','source_available':_source(self.objects,identifier) is not None}
                elif kind=='replays':
                    row=self.replay(identifier)
                elif kind=='live':
                    config_raw=self.db.execute('SELECT config FROM live_sessions WHERE session_id=?',(identifier,))[0][0]
                    config=loads(config_raw)
                    changed=config.get('mapping_sha256')!=mapping_identity()
                    row={'status':'new_session_required' if changed else 'compatible','code':'LIVE_MAPPING_CHANGED' if changed else 'LIVE_MAPPING_MATCH',
                         'action':'new_session' if changed else 'inspect_replay'}
                    replay=self.db.execute('SELECT replay_id FROM live_sessions WHERE session_id=?',(identifier,))[0][0]
                    if replay:
                        child=self.replay(replay)
                        if child['status']!='compatible':row.update(status='new_session_required',code=child['code'],action='new_session')
                elif kind in ('research','holdouts'):
                    if kind=='research': raw,sha=self.db.execute('SELECT plan,plan_sha256 FROM experiment_variants WHERE candidate_id=?',(identifier,))[0]
                    else:
                        key,ordinal=identifier.split(':');raw,sha=self.db.execute('SELECT plan,plan_sha256 FROM search_holdouts WHERE search_id=? AND ordinal=?',(key,int(ordinal)))[0]
                    _,changes=self.plan(raw,sha)
                    row={'status':'new_research_required' if changes else 'compatible','code':'PLAN_CHANGED' if changes else 'IDENTITY_MATCH','action':'new_research' if changes else 'inspect_research','changes':changes}
                else:
                    ref=self.db.execute('SELECT prepared_ref FROM launches WHERE launch_id=?',(identifier,))[0][0]
                    if not ref:row={'status':'prepare_required','code':'NOT_PREPARED','action':'prepare'}
                    else:
                        plan=self.objects.get(ref)['plan'];_,changes=self.plan(canonical(plan),digest(plan))
                        row={'status':'new_preparation_required' if changes else 'compatible','code':'PLAN_CHANGED' if changes else 'IDENTITY_MATCH','action':'prepare' if changes else 'inspect_preparation','changes':changes}
                items.append({'id':identifier,'kind':kind,**row})
            except (ValueError,OSError,KeyError,TypeError) as error:
                code=error.diagnostic.code if isinstance(error,OpenPineError) else 'SAVED_RECORD_INVALID'
                items.append({'id':identifier,'kind':kind,'status':'blocked','code':code,'action':'inspect_record'})
        return {'schema_version':'openpine.compatibility.1','kind':kind,'start':start,'next':start+len(ids),'total':total,'items':items,'identity':self.identity}


def compatibility(root, *, kind='jobs', start=0, limit=50):
    with snapshot(root) as db:
        if db.version!=SCHEMA_VERSION:
            return {'schema_version':'openpine.compatibility.1','status':'schema_upgrade_required','catalog':schema_report(db),'items':[]}
        return Compatibility(db,ReadObjects(Path(root)/'objects')).page(kind,start,limit)


def diagnostic_package(root, *, max_bytes=1024**2):
    """Allowlist only: no source, settings, paths, raw errors, logs or candles."""
    if type(max_bytes) is not int or not 4096<=max_bytes<=4*1024**2:raise ValueError('Diagnostic size must be 4 KiB–4 MiB.')
    report=doctor(root)
    for issue in report['issues']: issue.pop('detail',None)
    value={'schema_version':'openpine.diagnostic-package.1','doctor':report,'pages':[],
           'excluded':['source','inputs','paths','logs','raw_errors','candles','credentials'],'truncated':False}
    for kind in KINDS:
        try:page=compatibility(root,kind=kind,limit=20)
        except (ValueError,OSError,apsw.Error):
            page={'kind':kind,'status':'unavailable','code':'INSPECTION_FAILED','items':[]}
        for item in page['items']:
            if not re.fullmatch(r'[0-9a-f]{32}(?:[0-9a-f]{32}|:[0-9]{1,10})?',str(item.get('id',''))):item['id']='invalid'
            if item.get('job_status') not in (None,'queued','running','paused','succeeded','failed','cancelled','timed_out'):item['job_status']='invalid'
            for key in ('result_id','checkpoint_id'):
                if item.get(key) is not None and not re.fullmatch('[0-9a-f]{64}',str(item[key])):item[key]='invalid'
        if len(canonical({**value,'pages':[*value['pages'],page]}))>max_bytes-128:
            value['truncated']=True;break
        value['pages'].append(page)
        if page.get('next',0)<page.get('total',0):value['truncated']=True
    if len(canonical(value))>max_bytes:raise ValueError('Diagnostic header exceeds limit.')
    return value
