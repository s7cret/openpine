"""Durable application preparations, immutable configurations and one job identity."""
import time
from packages.contracts.launch import hex_id,validate_launch
from packages.contracts.wire import canonical,loads,digest


class LaunchRepository:
    def __init__(self,db):self.db=db

    def create(self,config):
        validate_launch(config);key=config['request_id'];sha=digest(config)
        with self.db.transaction():
            rows=self.db.execute('SELECT config_sha256 FROM launches WHERE launch_id=?',(key,))
            if rows:
                if rows[0][0]!=sha:raise ValueError('Preparation idempotency key was reused with another configuration.')
            else:
                state={'schema_version':'openpine.launch-status.1','launch_id':key,'status':'preparing',
                    'sources':[{'snapshot_id':s.get('snapshot_id'),'task_id':None} for s in [config['primary'],*config['contexts']]],
                    'error':None,'job_id':None,'prepared_ref':None,'created_at':time.time()}
                self.db.execute('INSERT INTO launches VALUES(?,?,?,?,?,?)',
                    (key,state['created_at'],canonical(config),sha,canonical(state),None))
        return self.get(key)

    def get(self,key):
        hex_id(key,32);rows=self.db.execute('SELECT config,config_sha256,state,prepared_ref FROM launches WHERE launch_id=?',(key,))
        if not rows:raise ValueError('Unknown preparation.')
        config,sha,raw,prepared=rows[0];config=loads(config);state=loads(raw);validate_launch(config)
        if digest(config)!=sha or config['request_id']!=key or state['launch_id']!=key or state['prepared_ref']!=prepared:
            raise ValueError('Preparation integrity differs.')
        if (state.keys()!={'schema_version','launch_id','status','sources','error','job_id','prepared_ref','created_at'}
                or state['schema_version']!='openpine.launch-status.1'
                or state['status'] not in ('preparing','ready','submitted','cancelled','failed')
                or type(state['sources']) is not list or len(state['sources'])!=1+len(config['contexts'])):
            raise ValueError('Invalid preparation state.')
        for source in state['sources']:
            if type(source) is not dict or source.keys()!={'snapshot_id','task_id'}:raise ValueError('Invalid prepared source.')
            if source['snapshot_id'] is not None:hex_id(source['snapshot_id'])
            if source['task_id'] is not None:hex_id(source['task_id'],32)
        if state['status'] in ('ready','submitted') and prepared is None:raise ValueError('Missing prepared inputs.')
        if state['status']=='submitted' and state['job_id']!=key:raise ValueError('Invalid prepared job link.')
        if prepared is not None:hex_id(prepared)
        return {'config':config,'state':state}

    def save(self,state):
        self.db.execute('UPDATE launches SET state=?,prepared_ref=? WHERE launch_id=?',
            (canonical(state),state['prepared_ref'],state['launch_id']))

    def list(self,limit=50):
        return [self.get(row[0]) for row in self.db.execute('SELECT launch_id FROM launches ORDER BY created_at DESC,launch_id LIMIT ?',(limit,))]
