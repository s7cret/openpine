"""Owned corpus available in the wheel through both CLI and HTTP."""
import time
import uuid
from packages.contracts.wire import digest, canonical
from apps.openpine.corpus import evidence


LEGACY_SHARDS=('economics','observed','cycle','intrabar','magnifier','contexts','numeric','targets','windows')
SHARD_SUITES=(*LEGACY_SHARDS,'oca')

# Run 43887647504 reached the 1200-second wait twice in Windows contexts.
# Numeric finished in 660 seconds. Exchange active-checkpoint and strategy
# cases for shorter runs while keeping 20 cases per shard on both platforms.
CORPUS_SUITE_OVERRIDES = {
    'pending-v4': 'numeric',
    'roundtrip-v6': 'numeric',
    'object-queue-strategy-v6': 'numeric',
    'scope-v1': 'contexts',
    'inputs-v1': 'contexts',
    'bands-routing-v3': 'contexts',
}


def selected(cases, shard):
    suite = SHARD_SUITES[shard]
    return [case for i, case in enumerate(cases)
            if ('oca' if case['family'] == 'dynamic-requests' else
                CORPUS_SUITE_OVERRIDES.get(case['id'], LEGACY_SHARDS[i % len(LEGACY_SHARDS)])) == suite]

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
    # Windows run 44940652025 reached the final economics case at 1200 s.
    wait_budget=1500 if SHARD_SUITES[shard]=='economics' else 1200
    started=time.monotonic();deadline=started+wait_budget
    while time.monotonic()<deadline:
        value=http('/api/corpus/runs/'+created['run_id'])
        if value['report']['status']!='running':break
        time.sleep(.5)
    report=value['report'];elapsed=round(time.monotonic()-started,3)
    if report['status']!='passed' or value['sha256']!=digest(report):raise AssertionError(report)
    executed=sum(c['expected']['status']=='compiled' for c in cases);rejected=len(cases)-executed
    if report['summary']['counts']!={'passed':executed,'expected_rejection':rejected,'failed':0,'cancelled':0,'running':0}:raise AssertionError(report['summary'])
    from tools.corpus_acceptance import validate
    validate(evidence(report),ids=ids)
    (work/'corpus-report.json').write_bytes(canonical(value))
    from tools.installed_bound_corpus import verify as verify_bound
    bound=verify_bound(http,cases,report)
    return {'corpus_cli_inventory':len(catalogue['cases']),'corpus_shard':shard,'corpus_shards':len(SHARD_SUITES),'corpus_compile_versions':sorted({c['version'] for c in cases}),
            'corpus_selected':len(cases),'corpus_wait_budget_seconds':wait_budget,'corpus_elapsed_seconds':elapsed,
            'corpus_http_worker_versions':sorted({c['version'] for c in report['cases'] if c['compilation']['status']=='compiled'}),'corpus_resume_and_ir':True,'corpus_expected_rejections_separate':True,
            'corpus_executed':executed,'corpus_rejected':rejected,'corpus_report_sha256':value['sha256'],'corpus_evidence':evidence(report),
            'bound_launch_evidence':bound}
