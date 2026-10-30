"""Complete local qualification or separately attested CI specification/test stages."""
import argparse,json,shutil,subprocess,sys,time
from pathlib import Path
from tools.specification import ROOT,require_python
from packages.contracts.wire import read_json,atomic_json,digest
FAST_MODULES=('test_wire.py','test_optimizer.py','test_research_metrics.py','test_walkforward_contracts.py','test_protection.py','test_technical.py','test_composite.py','test_loops.py','test_arrays.py','test_environment.py','test_signals.py','test_context_graph.py','test_partial_broker.py','test_pending_entries.py','test_lots.py','test_order_groups.py','test_installed_suites.py','test_trailing.py','test_trailing_storage.py','test_release_shards.py','test_economics.py','test_risk.py','test_performance.py','test_resilience.py','test_ci_artifacts.py','test_observed_broker.py','test_intrabar.py','test_strategy_cycle.py','test_magnifier.py')
PRIORITY=('test_specification.py','test_wire.py','test_hardening.py','test_live_market.py')
GENERATORS=('language_spec','series_spec','function_spec','request_spec','strategy_spec','exit_spec','technical_spec','composite_spec','loop_spec','array_spec','environment_spec','environment_fixtures','signal_spec','signal_fixtures','context_graph_fixtures','context_graph_examples','partial_exit_spec','partial_exit_fixtures','pending_entry_spec','pending_entry_fixtures','lot_spec','lot_fixtures','order_group_spec','order_group_fixtures','trailing_spec','trailing_fixtures','economics_spec','economics_fixtures','risk_spec','risk_fixtures','performance_spec','performance_fixtures','input_spec','replay_spec','observed_broker_spec','intrabar_spec','strategy_cycle_spec','magnifier_spec')
GENERATORS += ('lower_tf_spec','lower_tf_fixtures','lower_events_spec','prepared_requests_spec','computed_targets_spec','primary_targets_spec')
FAST_MODULES += ('test_lower_tf.py','test_lower_events.py','test_prepared_requests.py')
FAST_MODULES += ('test_preparation.py',)
FAST_MODULES += ('test_computed_targets.py',)
FAST_MODULES += ('test_primary_targets.py',)
GENERATORS += ('function_targets_spec','scalar_targets_spec','numeric_targets_spec')
FAST_MODULES += ('test_function_targets.py',)
FAST_MODULES += ('test_target_classification.py','test_scalar_targets.py','test_numeric_targets.py')
FAST_MODULES += ('test_dynamic_windows.py',)
GENERATORS += ('dynamic_windows_spec',)
FAST_MODULES += ('test_dynamic_signals.py',)
GENERATORS += ('dynamic_signals_spec','request_environment_spec')
GENERATORS += ('semantic_core_spec','semantic_core_fixtures')
GENERATORS += ('function_overloads_spec','function_overloads_fixtures')
GENERATORS += ('methods_spec','methods_fixtures')
GENERATORS += ('objects_spec','objects_fixtures')
FAST_MODULES += ('test_objects.py',)
FAST_MODULES += ('test_methods.py',)
FAST_MODULES += ('test_function_overloads.py',)
FAST_MODULES += ('test_request_environment.py',)
FAST_MODULES += ('test_passport.py',)
FAST_MODULES += ('test_bound_passport.py',)
FAST_MODULES += ('test_trace.py','test_trace_modes.py')

def specification_checks():return [('specification',('-m','tools.verify_spec')),*[(name,('-m','tools.'+name,'--check')) for name in GENERATORS],('conformance',('-m','tools.conformance','--write-report'))]
def validate_qualification(value,source):
    assert value['schema_version']=='openpine.gate-receipt.2' and value['status']=='passed' and value['section']=='specification' and not value['fast'] and value['source']==source and value['assigned_modules']==[]
    assert [v['check'] for v in value['checks']]==['architecture',*[n for n,_ in specification_checks()]] and all(v['exit_code']==0 for v in value['checks'])
    assert all(value['conformance'][k]==source['conformance'][k] for k in ('cases','executed','rejected','corpora'))
def conformance_receipt(source,output):
    from tools.ci_artifacts import sha
    found={};reports=[]
    for path in sorted((ROOT/'conformance/results').glob('*.json')):
        value=read_json(path);key=value['cases_sha256'];assert key not in found
        assert value['python']=='3.14.8' and value['implementation_sha256']==source['implementation_sha256'] and all(c['passed'] for c in value['results'])
        found[key]=value;reports.append({'file':path.name,'sha256':sha(path)})
        if output:
            target=output/'conformance'/path.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    assert set(found)=={c['sha256'] for c in source['conformance']['corpora']}
    for c in source['conformance']['corpora']:
        v=found[c['sha256']];assert len(v['results'])==c['cases'] and v['executed_programs']==c['executed'] and v['expected_rejections']==c['rejected']
    return {**source['conformance'],'reports':reports}
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fast',action='store_true');p.add_argument('--section',choices=('all','specification','tests'),default='all');p.add_argument('--qualification',type=Path);p.add_argument('--timings',type=Path);p.add_argument('--shard-index',type=int,default=0);p.add_argument('--shard-count',type=int,default=1);args=p.parse_args(argv)
    if not 1<=args.shard_count<=16 or not 0<=args.shard_index<args.shard_count:p.error('Require 0 <= shard-index < shard-count <= 16.')
    if args.fast and (args.section!='all' or args.shard_count!=1):p.error('Fast feedback cannot qualify release sections.')
    if args.section=='specification' and args.shard_count!=1:p.error('Run the complete specification exactly once per platform.')
    if args.qualification and args.section!='tests':p.error('A parent qualification is valid only for test shards.')
    require_python()
    if sys.version_info[:3]!=(3,14,8):p.error('The R6 gate requires Python 3.14.8.')
    from tools.ci_artifacts import source_identity,modules,platform_id
    from tools.test_shards import partition
    source=source_identity();parent=read_json(args.qualification) if args.qualification else None
    if parent:
        validate_qualification(parent,source);assert parent['platform']==platform_id()
    assigned=list(FAST_MODULES) if args.fast else [] if args.section=='specification' else partition(modules(),args.shard_count)[args.shard_index]
    output=args.timings.resolve() if args.timings else None
    if output:output.mkdir(parents=True,exist_ok=True)
    checks=[('architecture',('-m','tools.verify_architecture'))]
    if not args.fast and args.section in ('all','specification'):checks+=specification_checks()
    for name in assigned:
        command=['-m','tools.test_runner','--module',name]
        if output:command+=['--output',str(output/(name+'.json'))]
        checks.append((name,tuple(command)))
    timings=[];code=0
    for name,command in checks:
        started=time.monotonic();result=subprocess.run([sys.executable,'-X','utf8',*command],cwd=ROOT)
        timings.append({'check':name,'seconds':round(time.monotonic()-started,6),'exit_code':result.returncode});print(f"Check timing {name}: {timings[-1]['seconds']:.3f}s, exit {result.returncode}",flush=True)
        if result.returncode:code=result.returncode;break
    assert source_identity()==source,'Source changed during qualification.'
    value={'schema_version':'openpine.gate-receipt.2','source':source,'platform':platform_id(),'fast':args.fast,'section':'fast' if args.fast else args.section,'shard_index':args.shard_index,'shard_count':args.shard_count,'assigned_modules':assigned,'status':'failed' if code else 'passed','checks':timings,'qualification_sha256':digest(parent) if parent else None}
    if not code and not args.fast and args.section in ('all','specification'):value['conformance']=conformance_receipt(source,output)
    if output:
        value['module_results']=[read_json(output/(name+'.json')) for name in assigned if (output/(name+'.json')).exists()];atomic_json(output/'gate.json',value)
    if not code:print('R6 fast feedback passed (not release qualification).' if args.fast else f'R6 {args.section} passed on Python 3.14.8 (shard {args.shard_index+1}/{args.shard_count}).')
    return code
if __name__=='__main__':raise SystemExit(main())
