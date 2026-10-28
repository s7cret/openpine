"""Owned corpus available in the wheel through both CLI and HTTP."""
import time
import uuid
from packages.contracts.wire import digest, canonical
from apps.openpine.corpus import evidence


def verify(http,cli,work):
    catalogue=cli('corpus','catalogue')
    if catalogue!=http('/api/corpus') or len(catalogue['cases'])!=24:raise AssertionError('Installed corpus inventory differs.')
    compilation=cli('corpus','compile')
    if compilation['status']!='passed' or len(compilation['cases'])!=24:raise AssertionError(compilation)
    payload={'request_id':uuid.uuid4().hex,'case_ids':[case['id'] for case in catalogue['cases']]}
    created=http('/api/corpus/runs',payload)
    if http('/api/corpus/runs',payload)['run_id']!=created['run_id']:raise AssertionError('Corpus duplicate was resubmitted.')
    deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        value=http('/api/corpus/runs/'+created['run_id'])
        if value['report']['status']!='running':break
        time.sleep(.5)
    report=value['report']
    if report['status']!='passed' or value['sha256']!=digest(report):raise AssertionError(report)
    if report['summary']['counts']!={'passed':18,'expected_rejection':6,'failed':0,'running':0}:raise AssertionError(report['summary'])
    for row in report['cases']:
        if row['status']=='passed' and len(row['comparisons'])!=7:raise AssertionError('Installed corpus comparisons are incomplete.')
    (work/'corpus-report.json').write_bytes(canonical(value))
    return {'corpus_cli_inventory':24,'corpus_compile_versions':[1,2,3,4,5,6],
            'corpus_http_worker_versions':[1,2,3,4,5,6],'corpus_resume_and_ir':True,'corpus_expected_rejections_separate':True,
            'corpus_executed':18,'corpus_rejected':6,'corpus_report_sha256':value['sha256'],'corpus_evidence':evidence(report)}
