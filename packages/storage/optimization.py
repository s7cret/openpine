"""Durable search composition, precomputed holdout plans, and immutable decisions."""
import time
import uuid
from packages.contracts.storage import RunPlan
from packages.contracts.wire import canonical, loads, digest


class SearchRepository:
    def __init__(self,db):self.db=db

    def insert(self,manifest,selection_id,holdouts):
        identifier=uuid.uuid4().hex
        self.db.execute('INSERT INTO searches VALUES(?,?,?,?,?,?,NULL,NULL,NULL,NULL)',
            (identifier,time.time(),'running',canonical(manifest),digest(manifest),selection_id))
        for ordinal,(request,plan) in enumerate(holdouts):
            raw=plan.payload()
            self.db.execute('INSERT INTO search_holdouts VALUES(?,?,?,?,?)',
                (identifier,ordinal,canonical(request),canonical(raw),digest(raw)))
        return identifier

    def get(self,identifier):
        rows=self.db.execute('SELECT manifest,manifest_sha256,desired,selection_id,holdout_id,decision,decision_sha256,error FROM searches WHERE search_id=?',(identifier,))
        if not rows:raise ValueError('Unknown search.')
        raw,sha,desired,selection,holdout,decision,decision_sha,error=rows[0];manifest=loads(raw)
        if digest(manifest)!=sha:raise ValueError('Search manifest checksum differs.')
        decision=loads(decision) if decision is not None else None
        if decision is not None and digest(decision)!=decision_sha:raise ValueError('Search decision checksum differs.')
        return {'search_id':identifier,'manifest':manifest,'manifest_sha256':sha,'desired':desired,
                'selection_id':selection,'holdout_id':holdout,'decision':decision,'error':error}

    def list(self,*,active=False,limit=50):
        return [row[0] for row in self.db.execute("SELECT search_id FROM searches"+
            (" WHERE desired='running' AND (decision IS NULL OR holdout_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM search_assessments a WHERE a.search_id=searches.search_id AND a.phase='holdout'))" if active else '')+
            ' ORDER BY created_at DESC,search_id LIMIT ?',(limit,))]

    def holdout(self,identifier,ordinal):
        rows=self.db.execute('SELECT request,plan,plan_sha256 FROM search_holdouts WHERE search_id=? AND ordinal=?',(identifier,ordinal))
        if not rows:raise ValueError('Missing frozen holdout plan.')
        request,plan,sha=rows[0];request=loads(request);plan=loads(plan)
        expected=self.get(identifier)['manifest']['holdout_composition'][ordinal]
        if digest(plan)!=sha or expected!={'request_sha256':digest(request),'plan_sha256':sha}:
            raise ValueError('Holdout composition differs from its manifest.')
        return request,RunPlan.from_payload(plan)

    def assessments(self,identifier,phase='selection'):
        result=[]
        for ordinal,job_id,result_id,raw,sha in self.db.execute('SELECT ordinal,job_id,result_id,assessment,assessment_sha256 FROM search_assessments WHERE search_id=? AND phase=? ORDER BY ordinal',(identifier,phase)):
            value=loads(raw)
            if digest(value)!=sha:raise ValueError('Search assessment checksum differs.')
            result.append({'ordinal':ordinal,'job_id':job_id,'result_id':result_id,'assessment':value})
        return result

    def assess(self,identifier,phase,ordinal,job,assessment):
        self.db.execute('INSERT INTO search_assessments VALUES(?,?,?,?,?,?,?)',
            (identifier,phase,ordinal,job['job_id'],job['result_id'],canonical(assessment),digest(assessment)))

    def decide(self,identifier,decision,holdout_id):
        self.db.execute('UPDATE searches SET decision=?,decision_sha256=?,holdout_id=? WHERE search_id=? AND decision IS NULL',
            (canonical(decision),digest(decision),holdout_id,identifier))

    def control(self,identifier,desired,*,error=None):
        self.db.execute('UPDATE searches SET desired=?,error=? WHERE search_id=?',(desired,error,identifier))
