"""The unchanged corpus also travels through preparation, input checks and receipts."""
import time,uuid
from apps.openpine.corpus_manifest import Corpus
from apps.openpine.corpus_compare import historical,tagged
from packages.contracts.wire import digest,loads

def verify(http,cases,report):
    corpus=Corpus();records=[]
    old={r['case_id']:r for r in report['cases']}
    for case in cases:
        files=corpus.files[case['id']]
        compilation=http('/api/compile',{'source':files['source'].decode('utf-8'),'source_name':case['files']['source']['path']})
        if case['expected']['status']!='compiled':
            diagnostic=compilation['diagnostics'][0]
            assert compilation['status']=='rejected' and all(diagnostic[k]==case['expected'][k] for k in ('code','category'))
            records.append({'case_id':case['id'],'status':'expected_rejection'});continue
        snapshot=http('/api/snapshots',{'provider':'upload','raw':files['bars'].decode('utf-8')})['sha256'];contexts=[]
        if 'higher' in files:contexts.append({'kind':'snapshot','snapshot_id':http('/api/snapshots',{'provider':'upload','raw':files['higher'].decode('utf-8')})['sha256']})
        key=uuid.uuid4().hex
        http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':key,'input_ref':compilation['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':contexts,'inputs':case['inputs'],
            'timeout_seconds':120,'gap_policy':'allow_with_metadata'})
        saved=http(f'/api/launches/{key}/passport')['passport'];binding=saved['binding']['sha256']
        checked=http(f'/api/launches/{key}/validate',{'binding_sha256':binding})
        assert checked['validation']['status']=='validated',checked['validation']
        http(f'/api/launches/{key}/start',{'binding_sha256':binding})
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            state=http('/api/jobs/'+key)
            if state['status'] in ('succeeded','failed','timed_out','cancelled'):break
            time.sleep(.1)
        assert state['status']=='succeeded',state
        receipt=http(f'/api/launches/{key}/execution')['execution']
        assert receipt['whole_program_execution_verified'] and receipt['binding']['sha256']==binding
        actual=historical(http(f'/api/jobs/{key}/result'),loads(files['bars'])['bars'])
        semantic=digest(tagged(actual))
        expected=next(c['left_sha256'] for c in old[case['id']]['comparisons'] if c['name']=='source_submission_vs_saved_ir')
        assert semantic==expected,case['id']
        replay=old[case['id']]['replays'][0]['replay_id']
        rp=http(f'/api/replays/{replay}/validate',{})['passport']
        assert rp['replay_validation']['status']=='tape_verified',rp
        assert rp['replay_execution']['prefix_recorded'] and rp['replay_execution']['pending_events']==0
        assert rp['evidence']['whole_program_execution_verified'] is False
        records.append({'case_id':case['id'],'status':'passed','semantic_sha256':semantic,
            'input_validation':True,'bound_execution_receipt':True,'replay_tape_verified':True,'replay_prefix_only':True})
    return records
