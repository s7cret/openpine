"""Read-only, publication-bound sides. No compiler admission or execution happens here."""
from dataclasses import asdict,fields,is_dataclass
from packages.contracts.wire import digest,loads
from packages.execution.artifacts import import_program
from packages.storage.comparison_inputs import prefix
from apps.openpine.corpus_compare import locations

def written_sites(program):
    result=[]
    def walk(node):
        if is_dataclass(node):
            operation=getattr(node,'operation','');site=getattr(node,'call_site_id',None)
            if site and operation.startswith('simulation.'):result.append({'call_site_id':site,'operation':operation})
            for field in fields(node):
                if field.name not in ('identity','language_policy','span'):walk(getattr(node,field.name))
        elif type(node) in (tuple,list):
            for child in node:walk(child)
    walk(program);return result

def capture(manager,selection):
    m=manager;kind,key=selection['kind'],selection['id']
    if kind=='job':
        state=m.repository.get(key)
        if state['status']!='succeeded' or not state.get('result_id'):raise ValueError('Select a committed successful job.')
        result=m.summary(key);publication=result['result'];plan=m.plan(key).payload();request=m.request(key)
        if result.get('run_plan_sha256')!=digest(plan):raise ValueError('Result differs from its pinned plan.')
        artifact=result.get('artifact_ref')
        if not artifact:raise ValueError('Legacy result has no pinned program artifact for comparison.')
        program=import_program(m.objects.get(artifact));input_ref=request['input_ref']
        side={**selection,'result_ref':state['result_id'],'publication':publication,'plan':plan,'request':request,
            'artifact_ref':artifact,'input_ref':input_ref,'snapshot_id':request['snapshot_id'],'scope':'completed_history'}
        model={'kind':'history','policy':(publication.get('simulation') or {}).get('policy'),'magnifier':plan.get('magnifier'),
            'evaluation_window':plan.get('evaluation_window')}
        market=m.market.manifest(request['snapshot_id'])['identity']
    else:
        from packages.storage.replay import ReplayRepository
        repo=ReplayRepository(m.db,m.objects);row=repo.row(key)
        if row[1] is None or row[2]<0:raise ValueError('Select a replay with a committed publication.')
        publication=loads(row[5]);config=repo.config(key);artifact=config['artifact_ref'];program=import_program(m.objects.get(artifact));input_ref=config['input_ref']
        side={**selection,'config_ref':row[0],'publication':publication,'replay_config':config,'artifact_ref':artifact,'input_ref':input_ref,
            'tape_ref':prefix(repo,key,row[2]+1),'sequence':row[2],'scope':'published_replay_prefix'}
        model={'kind':'replay','policy':(publication.get('simulation') or {}).get('policy'),
            **{k:config.get(k) for k in ('event_profile','calculation_profile','cycle_settings','lower_tf')}}
        market=next(c for c in config['contexts'] if c['context_id']==config['primary_id'])
    model['market']={k:market[k] for k in ('symbol','timeframe')}
    side['primary_context']={k:market.get(k) for k in ('context_id','symbol','timeframe')}
    identity=asdict(program.identity)
    side.update(schema_version='openpine.comparison-side.1',program_sha256=digest(asdict(program)),identity=identity,locations=locations(program),sites=written_sites(program),model=model,
        outputs=[{k:o.get(k) for k in ('output_id','title','value_type')} for o in publication['outputs']],
        bars=publication.get('observation_count',publication.get('confirmed_bars')),source_name=program.span.source if hasattr(program,'span') else program.statements[0].span.source)
    side['settings']=publication.get('settings',side.get('plan',{}).get('settings',side.get('replay_config',{}).get('settings')))
    side['contexts']=side.get('plan',{}).get('contexts',side.get('replay_config',{}).get('contexts'))
    # program_hash uses the same canonical representation, including numeric type spelling.
    from packages.runtime.engine import program_hash
    side['program_sha256']=program_hash(program)
    if kind=='job' and result.get('program_sha256')!=side['program_sha256']:raise ValueError('Result program differs from its pinned artifact.')
    ref=m.objects.put(side)
    return {**selection,'side_ref':ref,'binding_sha256':digest(side)}

def read(manager,descriptor):
    side=manager.objects.get(descriptor['side_ref'],max_bytes=32*1024*1024)
    if digest(side)!=descriptor['binding_sha256'] or any(side[k]!=descriptor[k] for k in ('kind','id')):raise ValueError('Comparison side checksum differs.')
    return side

def comparable(left,right):
    from apps.openpine.compare_values import small
    changes=[]
    for field in ('program_sha256','identity','input_ref','snapshot_id','sequence','model','settings','contexts'):
        if left.get(field)!=right.get(field):changes.append({'field':field,'left':small(left.get(field)),'right':small(right.get(field))})
    reasons=[]
    if left['model']!=right['model']:reasons.append('Execution models/settings differ; automatic equivalence is not defined.')
    declarations=[[{k:o[k] for k in ('title','value_type')} for o in s['outputs']] for s in (left,right)]
    if declarations[0]!=declarations[1]:changes.append({'field':'outputs','left':small(declarations[0]),'right':small(declarations[1])})
    # Parameters and source changes are valid comparison subjects, not automatic failures.
    return {'status':'comparable' if not reasons else 'not_comparable','reasons':reasons,'changes':changes,
        'identity_policy':'Verified program-derived order/fill/trade identities; output mapping by declared ordinal.'}
