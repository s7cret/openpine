"""Owned corpus available in the wheel through both CLI and HTTP."""
import time
import uuid
from packages.contracts.wire import digest, canonical
from apps.openpine.corpus import evidence


SHARD_SUITES=('economics','observed','cycle','intrabar','magnifier')

def selected(cases,shard):return [case for i,case in enumerate(cases) if i%len(SHARD_SUITES)==shard]

def verify(http,cli,work,*,shard=0):
    catalogue=cli('corpus','catalogue')
    from apps.openpine.corpus_manifest import Corpus
    if catalogue!=http('/api/corpus') or catalogue['manifest_sha256']!=Corpus().sha256:raise AssertionError('Installed corpus inventory differs.')
    cases=selected(catalogue['cases'],shard);ids=[case['id'] for case in cases]
    compilation=cli('corpus','compile',*[arg for id in ids for arg in ('--case',id)])
    if compilation['status']!='passed' or len(compilation['cases'])!=len(cases):raise AssertionError(compilation)
    payload={'request_id':uuid.uuid4().hex,'case_ids':ids}
    created=http('/api/corpus/runs',payload)
    if http('/api/corpus/runs',payload)['run_id']!=created['run_id']:raise AssertionError('Corpus duplicate was resubmitted.')
    deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        value=http('/api/corpus/runs/'+created['run_id'])
        if value['report']['status']!='running':break
        time.sleep(.5)
    report=value['report']
    if report['status']!='passed' or value['sha256']!=digest(report):raise AssertionError(report)
    executed=sum(c['expected']['status']=='compiled' for c in cases);rejected=len(cases)-executed
    if report['summary']['counts']!={'passed':executed,'expected_rejection':rejected,'failed':0,'cancelled':0,'running':0}:raise AssertionError(report['summary'])
    from tools.corpus_acceptance import validate
    validate(evidence(report),ids=ids)
    (work/'corpus-report.json').write_bytes(canonical(value))
    from tools.installed_bound_corpus import verify as verify_bound
    bound=verify_bound(http,cases,report)
    return {'corpus_cli_inventory':len(catalogue['cases']),'corpus_shard':shard,'corpus_shards':len(SHARD_SUITES),'corpus_compile_versions':sorted({c['version'] for c in cases}),
            'corpus_http_worker_versions':[1,2,3,4,5,6],'corpus_resume_and_ir':True,'corpus_expected_rejections_separate':True,
            'corpus_executed':executed,'corpus_rejected':rejected,'corpus_report_sha256':value['sha256'],'corpus_evidence':evidence(report),
            'bound_launch_evidence':bound}
