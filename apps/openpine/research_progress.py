"""Small catalog-only progress; never reads IR, candle files or result objects."""
import re
from packages.storage.operations import TABLES
from packages.contracts.application import TERMINAL_STATUSES

def _counts(db,identifier):
    return {status:count for status,count in db.execute("SELECT COALESCE(j.status,'planned'),COUNT(*) FROM experiment_variants v LEFT JOIN jobs j ON j.job_id=v.job_id WHERE experiment_id=? GROUP BY COALESCE(j.status,'planned')",(identifier,))}

def progress(manager,kind,identifier):
    if kind not in TABLES or type(identifier) is not str or re.fullmatch('[0-9a-f]{32}',identifier) is None:raise ValueError('Invalid research identity.')
    table,field=TABLES[kind];db=manager.db
    with manager.limiter.slot('metadata'),db.lock:
        saved=db.execute(f"SELECT desired,json_extract(CAST(manifest AS TEXT),'$.name'),error FROM {table} WHERE {field}=?",(identifier,))
        if not saved:raise ValueError('Unknown research.')
        desired,name,error=saved[0];counts={};total=completed=0
        if kind=='experiment':
            counts=_counts(db,identifier);total=sum(counts.values());completed=sum(counts.get(s,0) for s in TERMINAL_STATUSES)
            state='succeeded' if counts.get('succeeded',0)==total else 'completed_with_errors' if completed==total else 'running'
            if desired=='paused':state='pausing' if counts.get('running',0) else 'paused'
        elif kind=='search':
            selection,decided,winner=db.execute("SELECT selection_id,decision IS NOT NULL,json_extract(CAST(decision AS TEXT),'$.winner_ordinal') FROM searches WHERE search_id=?",(identifier,))[0]
            counts=_counts(db,selection);total=sum(counts.values());completed=sum(counts.get(s,0) for s in TERMINAL_STATUSES)
            holdout=db.execute("SELECT json_extract(CAST(assessment AS TEXT),'$.status') FROM search_assessments WHERE search_id=? AND phase='holdout'",(identifier,))
            state='selecting' if not decided else 'no_eligible_candidate' if winner is None else 'checking_holdout' if not holdout else 'succeeded' if holdout[0][0]=='eligible' else 'holdout_failed'
            if desired=='paused':state='paused'
        else:
            values=db.execute("SELECT json_extract(CAST(receipt AS TEXT),'$.status') FROM walkforward_folds WHERE walkforward_id=? ORDER BY ordinal",(identifier,))
            total=len(values);completed=sum(v is not None for v, in values)
            state='running' if completed<total else 'succeeded' if all(v=='succeeded' for v, in values) else 'completed_with_errors'
            if desired=='paused':state='paused'
        if desired=='cancelled':state='cancelled'
        return {'schema_version':'openpine.research-progress.1',kind+'_id':identifier,'name':name,'desired':desired,'status':state,'completed':completed,'total':total,'counts':counts,'error':error[:512] if error else None}
