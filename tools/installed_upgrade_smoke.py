"""Installed CLI migration/rollback and HTTP replacement publication."""
import time
from pathlib import Path

from tools.specification import ROOT
from packages.storage.database import Database, SCHEMA_VERSION
from packages.storage.inspection import snapshot


def verify(http,cli,work):
    root=Path(work)/'upgrade-store';db=Database(root/'catalog.sqlite3')
    db.execute('DROP TABLE job_lineage');db.connection.pragma('user_version',15);db.close()
    result=cli('storage','upgrade','--state-dir',root,'--destination',Path(work)/'upgrade-backup')
    assert result['phase']=='complete' and result['from_schema']==15 and result['to_schema']==SCHEMA_VERSION
    cli('storage','restore','--preserve-schema','--source',Path(work)/'upgrade-backup','--state-dir',Path(work)/'rollback')
    with snapshot(Path(work)/'rollback') as old:assert old.version==15
    assert cli('doctor','--state-dir',root)['status']=='ok'
    bars=http('/api/snapshots',{'provider':'demo'})['sha256']
    created=http('/api/jobs',{'schema_version':'openpine.job-submission.1',
        'input':{'source':(ROOT/'examples/pipeline/v6.pine').read_text(encoding='utf-8'),'source_name':'upgrade.pine'},
        'snapshot_sha256':bars,'timeout_seconds':60})['job_id']
    def wait(identifier):
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            row=http('/api/jobs/'+identifier)
            if row['status'] in ('succeeded','failed','cancelled','timed_out'):break
            time.sleep(.05)
        assert row['status']=='succeeded',row
    wait(created)
    assert http('/api/jobs/'+created+'/diagnosis')['status']=='compatible'
    preview=http('/api/jobs/'+created+'/restart/preview',{})
    selected={'preview_id':preview['preview_id'],'request_id':'f'*32}
    receipt=http('/api/jobs/'+created+'/restart/apply',selected)
    assert receipt==http('/api/jobs/'+created+'/restart/apply',selected)
    assert not receipt['reuses_checkpoint'];wait(receipt['job_id'])
    assert http('/api/jobs/'+receipt['job_id']+'/result')['result']['outputs'][0]['values'][-1]==135.
    package=http('/api/maintenance/diagnostics')
    assert package['schema_version']=='openpine.diagnostic-package.1'
    return {'upgrade_checked':True,'rollback_schema':15,'replacement_idempotent':True,'replacement_last':135.}
