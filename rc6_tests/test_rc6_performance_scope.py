"""Coverage acceptance and untraced timings share obligations, not instrumentation."""
import copy
import pytest
from openpine.verification import execution_performance as owner
from openpine.verification.identity import digest
from rc6_tests.test_rc6_execution_platform import tiny_plan, reseal


def _instrumented(plan):
    traced=copy.deepcopy(plan)
    for task in traced['tasks']:
        task['coverage']=True
        for shard in task['shards']:
            shard['coverage']=True
    return reseal(traced)


def test_untraced_performance_matches_traced_acceptance_denominator(tmp_path):
    sample,_=tiny_plan(tmp_path)
    candidate=_instrumented(sample)
    assert owner.workload_identity(candidate)!=owner.workload_identity(sample)
    validate=getattr(owner,'validate_performance_scope',None)
    assert callable(validate), 'missing explicit untraced-to-coverage obligation adapter'
    validate(candidate,sample)
    assert all(t['coverage'] for t in candidate['tasks'])
    assert all(not t['coverage'] for t in sample['tasks'])


def test_traced_performance_cannot_be_admitted(tmp_path):
    sample,_=tiny_plan(tmp_path)
    candidate=_instrumented(sample)
    validate=getattr(owner,'validate_performance_scope',None)
    assert callable(validate), 'missing performance instrumentation guard'
    with pytest.raises(ValueError,match='untraced'):
        validate(candidate,candidate)


@pytest.mark.parametrize('mutation',['scope','policy','source','python','nodes','plugin','variant','execution_path','mode'])
def test_performance_scope_rejects_noninstrumentation_drift(tmp_path,mutation):
    sample,_=tiny_plan(tmp_path)
    candidate=_instrumented(sample)
    changed=copy.deepcopy(sample)
    if mutation=='scope':
        changed['profile']='smoke'
    elif mutation=='policy':
        changed['policy_hash']=digest({'changed':'policy'})
    elif mutation=='source':
        changed['source']['policy']='different-source-policy'
        changed['source']=reseal(changed['source'])
    elif mutation=='python':
        env=changed['environments']['py']['identity']
        env['python']='3.99.0'
        changed['environments']['py']['identity']=reseal(env)
    elif mutation=='plugin':
        changed['tasks'][0]['plugins']=['unexpected_plugin']
    elif mutation in {'variant','execution_path','mode'}:
        changed['tasks'][0][mutation]='unexpected'
    else:
        task=changed['tasks'][0]
        task['nodeids']=[task['nodeids'][0]]
        task['nodeids_hash']=digest(task['nodeids'])
        task['full_inventory_hash']=task['nodeids_hash']
        task['node_markers']={n:task['node_markers'][n] for n in task['nodeids']}
        task['shards']=[s for s in task['shards'] if s['nodeids']==task['nodeids']]
    changed=reseal(changed)
    validate=getattr(owner,'validate_performance_scope',None)
    assert callable(validate), 'missing exact scope guard'
    with pytest.raises(ValueError):
        validate(candidate,changed)
