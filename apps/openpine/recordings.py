"""Portable, immutable live receipts and network-free replay from their published cut."""

import hashlib
import itertools
import tempfile
from pathlib import Path

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical, digest, immutable_json, loads, read_json
from packages.storage.backup import publish_directory
from packages.storage.lease import Lease
from packages.storage.live import LiveRepository
from packages.storage.objects import ObjectStore, identifier, sync_directory
from packages.storage.results import series_rows
from packages.storage.trades import event_rows
from apps.openpine.replay import Replays

SCHEMA = 'openpine.session-recording.1'
CONFIGURED_SCHEMA='openpine.session-recording.2'
CALCULATION_SCHEMA='openpine.session-recording.3'
CYCLE_SCHEMA='openpine.session-recording.4'
LOWER_SCHEMA='openpine.session-recording.5'

def schema_for(config,settings):
    return LOWER_SCHEMA if config.get('lower_tf') else CYCLE_SCHEMA if config.get('calculation_profile')=='openpine.strategy-cycle.1' else CALCULATION_SCHEMA if config.get('calculation_profile') else CONFIGURED_SCHEMA if settings else SCHEMA
CHUNK = 1000


def logical_result(manager, state):
    """File partitioning and replay IDs do not affect logical output equality."""
    def fingerprint(rows):
        h=hashlib.sha256();count=0
        for row in rows:h.update(canonical(row)+b'\n');count+=1
        return {'sha256':h.hexdigest(),'rows':count}
    journal=(state['simulation'] or {}).get('journal',{'records':0,'parts':[]})
    simulation={k:v for k,v in state['simulation'].items() if k!='journal'} if state['simulation'] else None
    return {'confirmed_bars':state['confirmed_bars'],'sequence':state['sequence'],
        'input_chain':state['input_chain'],'contexts':state['contexts'],'preview':state['preview'],
        'outputs':state['outputs'],'simulation':simulation,
        'series':fingerprint(series_rows(manager.objects,{'observation_count':state['confirmed_bars'],'series':state['series']},0,state['confirmed_bars'])),
        'ledger':fingerprint(event_rows(manager.objects,journal,0,journal['records'])),
        **({'settings':state['settings']} if state.get('settings') else {}),
        **({'calculation':state['calculation']} if state.get('calculation') else {}),
        **({'lower_tf':state['lower_tf']} if state.get('lower_tf') else {})}


def open_recording(path):
    root=Path(path).resolve();envelope=read_json(root/'recording.json')
    if (type(envelope) is not dict or envelope.keys()!={'sha256','recording'}
            or digest(envelope['recording'])!=envelope['sha256']):raise ValueError('Recording manifest checksum differs.')
    value=envelope['recording']
    names={'schema_version','configuration','session_configuration','session_id','published_events','accepted_events',
        'event_parts','raw_sources','datasets','objects','expected'}
    if type(value) is not dict or value.keys()!=names or value['schema_version'] not in (SCHEMA,CONFIGURED_SCHEMA,CALCULATION_SCHEMA,CYCLE_SCHEMA,LOWER_SCHEMA):raise ValueError('Unknown recording fields/schema.')
    if (type(value['published_events']) is not int or not 0<=value['published_events']<=value['accepted_events']<=2**63-1
            or type(value['objects']) is not dict or len(value['objects'])>100000
            or type(value['event_parts']) is not list):raise ValueError('Invalid recording bounds.')
    objects=ObjectStore(root/'objects')
    for sha,size in value['objects'].items():
        identifier(sha);file=objects.path(sha)
        if (type(size) is not int or size<0 or not file.resolve().is_relative_to(root)
                or file.is_symlink() or file.parent.is_symlink() or objects.root.is_symlink()
                or objects.verify(sha).stat().st_size!=size):raise ValueError('Recording object is unsafe, missing or corrupted.')
    def known(sha):
        identifier(sha)
        if sha not in value['objects']:raise ValueError('Recording references an undeclared object.')
    config=value['configuration']
    for key in ('input_ref','artifact_ref'):known(config[key])
    from packages.contracts.inputs import admitted_settings
    from packages.execution.artifacts import import_program
    settings=admitted_settings(import_program(objects.get(config['artifact_ref'])),config.get('settings'))
    expected_schema=schema_for(config,settings)
    if settings!=config.get('settings') or value['schema_version']!=expected_schema:raise ValueError('Recording settings/schema differ from the compiled inputs.')
    content=objects.get(config['input_ref'])
    for key in ('source_ref','artifact_ref'):
        if key in content:known(content[key])
    for manifest in value['datasets'].values():
        for part in manifest['parts']:known(part['object_id'])
    cursor=0
    for part in value['event_parts']:
        if type(part) is not dict or part.keys()!={'first','count','object_id'} or part['first']!=cursor or not 1<=part['count']<=CHUNK:
            raise ValueError('Invalid recording event partition.')
        known(part['object_id']);rows=objects.get(part['object_id'])
        if type(rows) is not list or len(rows)!=part['count']:raise ValueError('Incomplete recording event partition.')
        for row in rows:
            if MarketEvent.from_payload(row).sequence!=cursor:raise ValueError('Recording event sequence differs.')
            cursor+=1
    if cursor!=value['published_events']:raise ValueError('Recording published cut is incomplete.')
    for source in value['raw_sources']:
        cursor=0
        for part in source['parts']:
            known(part['object_id']);rows=objects.get(part['object_id'])
            if part['first']!=cursor or len(rows)!=part['count']:raise ValueError('Invalid raw recording range.')
            for sequence,record in rows:
                if sequence!=cursor:raise ValueError('Missing raw recording event.')
                if record.get('snapshot_id') and record['snapshot_id'] not in value['datasets']:raise ValueError('Missing recovery snapshot.')
                cursor+=1
        if cursor!=source['accepted']:raise ValueError('Raw recording cut is incomplete.')
    return value,objects,envelope['sha256']


class Recordings:
    def __init__(self,manager):self.manager=manager;self.replays=Replays(manager)

    def export(self,session_id,destination):
        manager=self.manager;live=LiveRepository(manager.db,manager.objects);session=live.session(session_id)
        if session['desired']!='stopped':raise ValueError('Stop the session before exporting its published cut.')
        destination=Path(destination).resolve()
        if destination.exists() or destination.is_relative_to(manager.directory):raise ValueError('Recording destination must be new and outside the owned store.')
        destination.parent.mkdir(parents=True,exist_ok=True)
        with manager.limiter.slot('read'),Lease(manager.directory/('.replay-'+session['replay_id']+'.lock'),wait_seconds=10):
            state=self.replays.status(session['replay_id']);config=self.replays.repository.config(session['replay_id'])
            settings=live.session_config(session_id);published=state['sequence']+1
            with tempfile.TemporaryDirectory(prefix='.recording-',dir=destination.parent) as directory:
                root=Path(directory);target=ObjectStore(root/'objects');copied={};datasets={}
                def copy(sha):
                    if sha not in copied:
                        if target.copy_file(manager.objects.verify(sha))!=sha:raise ValueError('Recording copy checksum differs.')
                        copied[sha]=target.verify(sha).stat().st_size
                    return sha
                def put(value):
                    sha=target.put(value);copied[sha]=target.verify(sha).stat().st_size;return sha
                def dataset(sha):
                    if not sha or sha in datasets:return
                    manifest=manager.market.manifest(sha);datasets[sha]=manifest
                    for part in manifest['parts']:copy(part['object_id'])
                for name in ('input_ref','artifact_ref'):copy(config[name])
                content=manager.objects.get(config['input_ref'])
                for name in ('source_ref','artifact_ref'):
                    if name in content:copy(content[name])
                parts=[];cursor=0;events=self.replays.repository.events(session['replay_id'],0,published)
                while batch:=list(itertools.islice(events,CHUNK)):
                    parts.append({'first':cursor,'count':len(batch),'object_id':put([e.payload() for e in batch])});cursor+=len(batch)
                sources=[]
                bindings=session.get('sources',{'primary':{'feed_id':session['feed_id'],'cursor':session['cursor'],
                    'warmup_snapshot':session['warmup_snapshot']}})
                for context_id,source in bindings.items():
                    feed=live.state(source['feed_id']);dataset(source.get('warmup_snapshot'));raw=[];cursor=0
                    while cursor<feed['accepted']:
                        batch=list(live.records(source['feed_id'],cursor,min(CHUNK,feed['accepted']-cursor)))
                        if not batch:raise ValueError('Missing recording raw prefix.')
                        for _,record in batch:dataset(record.get('snapshot_id'))
                        raw.append({'first':cursor,'count':len(batch),'object_id':put(batch)});cursor+=len(batch)
                    sources.append({'context_id':context_id,'feed_id':source['feed_id'],'accepted':feed['accepted'],
                        'projected_cursor':source['cursor'],'parts':raw})
                recording={'schema_version':schema_for(config,config.get('settings')),'configuration':config,'session_configuration':settings,'session_id':session_id,
                    'published_events':published,'accepted_events':state['accepted_events'],'event_parts':parts,
                    'raw_sources':sources,'datasets':datasets,'objects':copied,'expected':logical_result(manager,state)}
                receipt={'sha256':digest(recording),'recording':recording}
                immutable_json(root/'recording.json',receipt);open_recording(root)
                sync_directory(root);publish_directory(root,destination);sync_directory(destination.parent)
        return {'status':'exported','recording_id':receipt['sha256'],'published_events':published,
            'accepted_tail_excluded':state['accepted_events']-published,'objects':len(copied),'path':str(destination)}

    def replay(self,path,*,batch_size=1000):
        if type(batch_size) is not int or not 1<=batch_size<=10000:raise ValueError('Replay batch size requires 1..10000.')
        recording,objects,identity=open_recording(path)
        for sha in recording['objects']:
            if self.manager.objects.copy_file(objects.verify(sha))!=sha:raise ValueError('Imported recording object differs.')
        config=recording['configuration']
        created=self.replays.create({k:config[k] for k in ('input_ref','contexts','primary_id','broker_settings','timeout_seconds','event_profile','broker_profile','calculation_profile','cycle_settings','settings','environments','lower_tf') if k in config})
        replay_id=created['replay_id']
        if self.replays.repository.config(replay_id)['identity']!=config['identity']:raise ValueError('Recording execution identity is incompatible.')
        for part in recording['event_parts']:
            self.replays.append(replay_id,objects.get(part['object_id']))
        while self.replays.status(replay_id)['pending_events']:
            self.replays.advance(replay_id,max_events=batch_size,checkpoint_every=batch_size)
        actual=logical_result(self.manager,self.replays.status(replay_id))
        if actual!=recording['expected']:raise ValueError('Offline recording outputs differ from the published live cut.')
        return {'status':'passed','recording_id':identity,'replay_id':replay_id,'events':recording['published_events'],
            'result':actual,'network_required':False}
