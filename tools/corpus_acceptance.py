"""Validate the exact finite corpus and portable comparison evidence in CI."""
import re
from apps.openpine.corpus_manifest import Corpus

COMPARISONS = ('source_submission_vs_saved_ir','history_vs_checkpoint_resume','history_final_native_state',
               'independent_oracle','replay_fresh_vs_resume','replay_final_native_state','history_vs_close_only_replay')


def validate(value):
    corpus=Corpus()
    assert value['manifest_sha256']==corpus.sha256,'Corpus manifest differs.'
    assert [row['case_id'] for row in value['cases']]==list(corpus.cases),'Corpus omitted/reordered cases.'
    for row in value['cases']:
        case=corpus.cases[row['case_id']]
        assert row['source_sha256']==case['files']['source']['sha256'] and row['version']==case['version']
        compiled=row['compilation']
        assert compiled['expected'] is True and compiled['status']==case['expected']['status'] and compiled['version']==case['version']
        if case['expected']['status']=='rejected':
            assert row['status']=='expected_rejection' and row['comparisons']==[] and row['tape_sha256'] is None
            diagnostic=compiled['diagnostics'][0]
            assert all(diagnostic[key]==case['expected'][key] for key in ('code','category'))
            assert diagnostic['span']['source']==case['files']['source']['path']
            continue
        assert row['status']=='passed' and re.fullmatch('[0-9a-f]{64}',row['tape_sha256'])
        assert [c['name'] for c in row['comparisons']]==list(COMPARISONS),'Missing corpus comparison.'
        for comparison in row['comparisons']:
            if comparison['name']==COMPARISONS[-1] and not case['history_replay']['equivalent']:
                assert comparison=={'name':COMPARISONS[-1],'status':'not_equivalent','reason':case['history_replay']['reason']}
            else:
                assert comparison['status']=='passed'
                assert comparison['left_sha256']==comparison['right_sha256'] and re.fullmatch('[0-9a-f]{64}',comparison['left_sha256'])
        assert row['actual_source_route'] in ('source','ir_with_source_provenance')
    return True


def audit_installed(installed,source):
    rows=[r['result']['corpus_evidence'] for r in installed if r['suite']=='economics']
    assert len(rows)==2 and all(validate(row) for row in rows)
    assert all(row['identity'][0]==source['implementation_sha256'] for row in rows)
    assert rows[0]==rows[1],'Windows/Linux corpus semantic evidence differs.'
