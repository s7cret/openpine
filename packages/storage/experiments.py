"""SQLite presets and immutable experiment composition; jobs own execution state."""
import time
import uuid
from contextlib import nullcontext
from packages.contracts.storage import RunPlan
from packages.contracts.wire import canonical,loads,digest


class ExperimentRepository:
    def __init__(self,db):self.db=db

    def create(self,manifest,variants,*,transaction=True):
        identifier=uuid.uuid4().hex
        with self.db.transaction() if transaction else nullcontext():
            self.db.execute('INSERT INTO experiments VALUES(?,?,?,?,?,NULL)',
                (identifier,time.time(),'running',canonical(manifest),digest(manifest)))
            for ordinal,(request,plan) in enumerate(variants):
                candidate=digest({'experiment':identifier,'variant':ordinal})[:32];payload=plan.payload()
                self.db.execute('INSERT INTO experiment_variants VALUES(?,?,?,?,?,?,NULL)',
                    (identifier,ordinal,candidate,canonical(request),canonical(payload),digest(payload)))
        return identifier

    def manifest(self,identifier):
        rows=self.db.execute('SELECT manifest,manifest_sha256 FROM experiments WHERE experiment_id=?',(identifier,))
        if not rows:raise ValueError('Unknown experiment.')
        value=loads(rows[0][0])
        if digest(value)!=rows[0][1]:raise ValueError('Experiment manifest checksum differs.')
        return value,rows[0][1]

    def groups(self,*,active=False,limit=50):
        sql="SELECT experiment_id,desired,error,created_at FROM experiments e"+(" WHERE desired='running' AND EXISTS(SELECT 1 FROM experiment_variants v LEFT JOIN jobs j ON j.job_id=v.job_id WHERE v.experiment_id=e.experiment_id AND (v.job_id IS NULL OR j.status IN ('queued','running','paused')))" if active else '')+(' ORDER BY created_at,experiment_id LIMIT ?' if active else ' ORDER BY created_at DESC,experiment_id LIMIT ?')
        return [{'experiment_id':identifier,'desired':desired,'error':error,'created_at':created} for identifier,desired,error,created in self.db.execute(sql,(limit,))]

    def group(self,identifier):
        rows=self.db.execute('SELECT desired,error,created_at FROM experiments WHERE experiment_id=?',(identifier,))
        if not rows:raise ValueError('Unknown experiment.')
        return dict(zip(('desired','error','created_at'),rows[0],strict=True))

    def control(self,identifier,desired,*,error=None):
        self.group(identifier)
        self.db.execute('UPDATE experiments SET desired=?,error=? WHERE experiment_id=?',(desired,error,identifier))

    def variants(self,identifier,*,start=0,limit=50):
        self.group(identifier)
        rows=self.db.execute('SELECT ordinal,candidate_id,request,plan,plan_sha256,job_id FROM experiment_variants WHERE experiment_id=? AND ordinal>=? ORDER BY ordinal LIMIT ?',
            (identifier,start,limit));result=[]
        for ordinal,candidate,request,raw,sha,job in rows:
            plan=loads(raw)
            if digest(plan)!=sha:raise ValueError('Experiment variant plan checksum differs.')
            result.append({'ordinal':ordinal,'candidate_id':candidate,'request':loads(request),'plan':RunPlan.from_payload(plan),'job_id':job})
        return result

    def counts(self,identifier):
        self.group(identifier)
        return {status:count for status,count in self.db.execute("SELECT COALESCE(j.status,'planned'),COUNT(*) FROM experiment_variants v LEFT JOIN jobs j ON j.job_id=v.job_id WHERE v.experiment_id=? GROUP BY COALESCE(j.status,'planned')",(identifier,))}

    def owned_active(self):
        return self.db.execute("SELECT COUNT(*) FROM experiment_variants v JOIN jobs j ON j.job_id=v.job_id WHERE j.status IN ('queued','running')")[0][0]

    def pending(self,identifier):
        rows=self.db.execute('SELECT ordinal FROM experiment_variants WHERE experiment_id=? AND job_id IS NULL ORDER BY ordinal LIMIT 1',(identifier,))
        return self.variants(identifier,start=rows[0][0],limit=1)[0] if rows else None

    def owned_jobs(self,identifier,statuses=('queued','running','paused')):
        marks=','.join('?' for _ in statuses)
        return [row[0] for row in self.db.execute(f'SELECT j.job_id FROM jobs j JOIN experiment_variants v ON v.job_id=j.job_id WHERE v.experiment_id=? AND j.status IN ({marks}) ORDER BY v.ordinal',(identifier,*statuses))]

    def presets(self,schema_sha):
        return [{'preset_id':identifier,'name':name,'settings':loads(settings),'revision':revision,'updated_at':updated}
            for identifier,name,settings,revision,updated in self.db.execute('SELECT preset_id,name,settings,revision,updated_at FROM input_presets WHERE schema_sha256=? ORDER BY name LIMIT 100',(schema_sha,))]

    def save_preset(self,schema_sha,name,input_ref,settings,expected_revision):
        with self.db.transaction():
            rows=self.db.execute('SELECT preset_id,revision FROM input_presets WHERE schema_sha256=? AND name=?',(schema_sha,name))
            if rows:
                identifier,revision=rows[0]
                if expected_revision!=revision:raise ValueError('Preset changed; reload before overwriting it.')
                self.db.execute('UPDATE input_presets SET input_ref=?,settings=?,revision=?,updated_at=? WHERE preset_id=?',
                    (input_ref,canonical(settings),revision+1,time.time(),identifier))
            else:
                if expected_revision is not None:raise ValueError('Preset revision no longer exists.')
                if self.db.execute('SELECT COUNT(*) FROM input_presets WHERE schema_sha256=?',(schema_sha,))[0][0]>=100:raise ValueError('At most 100 presets per input schema.')
                identifier=uuid.uuid4().hex;revision=0
                self.db.execute('INSERT INTO input_presets VALUES(?,?,?,?,?,?,?)',(identifier,schema_sha,name,input_ref,canonical(settings),1,time.time()))
        return {'preset_id':identifier,'name':name,'revision':revision+1,'settings':settings}
