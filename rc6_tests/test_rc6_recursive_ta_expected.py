"""Independent recursive TA trajectories through native admission and runtime lifecycle."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import importlib
import json
import math
import os
from pathlib import Path

import pytest
from ast2python.lowering import load_pinelib_target_manifest
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession, na
from pinelib.runtime.metadata import BarValues
from pinelib.state.checkpoint import from_portable
from pinelib.ta.types import MacdResult

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report
from openpine.verification.conformance import first_difference, load_corpus
from openpine.verification.identity import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'verification/builtin-recursive-ta-v1/manifest.json'
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / 'lock.json')
PATHS = ('abi', 'compiled_historical', 'compiled_realtime', 'compiled_rollback', 'compiled_checkpoint')
assert len(MANIFEST['cases']) == LOCK['case_count'] == 40
assert MANIFEST['content_hash'] == LOCK['content_hash']


def source_identity():
    pins = read_json(ROOT / 'docs/RC6_LIFECYCLE_SOURCES.json')
    return {'mode': 'declared_dependency_pins', **{name:pins[name] for name in ('pine2ast','ast2python','pinelib')}}


def record(value):
    if value is na:
        return {'kind':'na'}
    if type(value) is MacdResult:
        return [record(value.macd), record(value.signal), record(value.histogram)]
    if type(value) in (tuple,list):
        return [record(item) for item in value]
    assert type(value) is float and math.isfinite(value)
    return {'kind':'value','value':value}


@lru_cache(maxsize=None)
def prepare(case_id):
    case = next(row for row in MANIFEST['cases'] if row['id'] == case_id)
    settings = read_json(CORPUS.parent / case['settings']['path'])
    source = (CORPUS.parent / case['source']['path']).read_text(encoding='utf-8')
    identity = source_identity()
    compiled = NativeRC6CompilerAdapter().compile(source, source_name=settings['source_name'],
        producer_commits={name:identity[name] for name in ('pine2ast','ast2python')})
    assert compiled.success, compiled.errors
    assert compiled.compile_meta['adapter'] == 'native-rc6-python-library'
    bundle = compiled.consumer_bundle
    nodes = {row['node_id'] for row in bundle['node_index'] if row['kind']=='CallExpr'
             and row['span']['start_line']==settings['primary_source_line']}
    calls = [row for row in bundle['semantic_facts']['calls'] if row['node_id'] in nodes]
    assert len(calls)==1
    key = tuple(calls[0][field] for field in ('symbol_id','overload_id','call_form'))
    assert list(key)==settings['declared_binding']
    assert calls[0]['return_type']==('tuple<float,float,float>' if settings['result_arity']==3 else 'float')
    target = load_pinelib_target_manifest()
    binding = target.call_bindings[key]
    assert case['pine_version'] in binding.supported_pine_versions
    assert binding.python_module+'.'+binding.python_name == settings['abi_callable']
    assert compiled.compile_meta['target_manifest_hash']==target.content_hash
    namespace = {}
    exec(compile(compiled.python_code,settings['source_name']+'.py','exec'),namespace)
    function = getattr(importlib.import_module(binding.python_module),binding.python_name)
    return settings,compiled,bundle,key,target,namespace,function,identity


def execute_case(case,path,compact):
    settings,compiled,bundle,key,target,namespace,function,identity=prepare(case['id'])
    closes=read_json(CORPUS.parent/case['data']['path'])

    def make_runtime():
        runtime=RuntimeSession(RuntimeLanguageContext(case['pine_version'],'recursive-ta-manual',
            f"pine-v{case['pine_version']}",compiled.generated_artifact['source_hash'],'compiler_annotation'))
        runtime.commit_full_identity=not compact
        return runtime

    runtime=make_runtime()
    sequence=0
    trials=[]
    restores=[]

    def restore(bar,reason):
        nonlocal runtime
        saved=runtime.checkpoint().to_dict()
        clone=make_runtime()
        clone.restore(json.loads(json.dumps(saved)))
        assert clone.checkpoint().to_dict()==saved
        runtime=clone
        restores.append({'bar':bar,'reason':reason,'whole_checkpoint_preserved':True})

    def callback(bar,trial=False):
        nonlocal sequence
        close=closes[bar]+(100.0 if trial and path=='compiled_rollback' else 0.0)
        realtime=bar>0 and path in {'compiled_realtime','compiled_rollback'}
        tx=runtime.begin(CallbackFrame('REALTIME_TICK' if realtime else 'HISTORICAL_EVAL',sequence,
            bar_index=bar,realtime=realtime,final_tick=not trial),
            values=BarValues(close,close+1,close-1,close,1,bar*60000,(bar+1)*60000-1))
        if path=='abi':
            sample=na if bar in settings['missing_source_bars'] else close
            value=record(function(tx,'independent-recursive-ta',sample,*settings['parameters']))
        else:
            namespace['GeneratedScript'](tx).run()
            plots=runtime.visuals.working[-settings['result_arity']:]
            assert len(plots)==settings['result_arity']
            values=[record(from_portable(plot.payload['series'])) for plot in plots]
            value=values if settings['result_arity']==3 else values[0]
        if trial and path=='compiled_rollback':
            tx.abort()
        else:
            tx.commit()
            sequence+=1
        return value

    events=[]
    for bar in range(len(closes)):
        if bar>0 and path in {'compiled_realtime','compiled_rollback'}:
            before=deepcopy(runtime.checkpoint().to_dict())
            attempted=sequence
            trial=callback(bar,True)
            trials.append({'bar':bar,'value':trial,'attempt_sequence':attempted,
                           'independent_numeric_expectation':path=='compiled_realtime'})
            if path=='compiled_rollback':
                assert sequence==attempted and runtime.checkpoint().to_dict()==before
                restore(bar,'aborted_same_sequence_retry')
        value=callback(bar)
        if bar>0 and path=='compiled_realtime':
            assert trial==value
        events.append({'bar':bar,'value':value})
        if path=='compiled_checkpoint':
            restore(bar,'committed_before_continuation')
    return {'status':'completed','compile':True,'events':events,'execution_path':path,
            'transcript_mode':'compact' if compact else 'full','trials':trials,'restores':restores,
            'executed_bindings':[[case['pine_version'],*key]],'target_manifest_hash':target.content_hash,
            'catalog_hash':bundle['version_context']['catalog_hash'],'source_identity':identity,
            'semantic_authority':settings['authority'],'manual_row_id':settings['manual_row_id'],
            'generated_module_sha256':hashlib.sha256(compiled.python_code.encode()).hexdigest(),
            'artifact_hash':compiled.generated_artifact['content_hash'],
            **{field+'_sha256':case[field]['sha256'] for field in ('source','data','settings')}}


@pytest.mark.parametrize('case',MANIFEST['cases'],ids=lambda case:case['id'])
@pytest.mark.parametrize('path',PATHS)
@pytest.mark.parametrize('compact',[False,True],ids=['full','compact'])
def test_independent_recursive_trajectory(case,path,compact,observations):
    actual=execute_case(case,path,compact)
    expected=read_json(CORPUS.parent/case['expected']['path'])
    assert first_difference(expected,{'compile':actual['compile'],'events':actual['events']},case['tolerance']) is None
    observations.setdefault((compact,path),{})[case['id']]=actual


@pytest.fixture(scope='module')
def observations():
    rows={}
    yield rows
    if not (output:=os.environ.get('OPENPINE_STAGE1_EVIDENCE')):
        return
    surface=build_builtin_surface()
    indexed={(r['pine_version'],r['symbol_id'],r['overload_id'],r['call_form']):r for r in surface['rows']}
    write_json(Path(output)/'builtin-recursive-ta-surface.json',surface)
    for (compact,path),values in rows.items():
        assert len(values)==40
        assignments=[]
        for case_id,value in values.items():
            assert value['semantic_authority']=='PRIMARY_FORMULA_DERIVATION'
            assert len(value['executed_bindings'])==1
            row=indexed[tuple(value['executed_bindings'][0])]
            assert row['status']=='RUNTIME_DIRECT'
            assignments.append({**{field:row[field] for field in ('pine_version','symbol_id','overload_id','call_form','contract_hash')},
                                'case_id':case_id,'path':path})
        report=builtin_evidence_report(surface,CORPUS,values,corpus_hash=LOCK['content_hash'],assignments=assignments)
        assert report['execution_evidence']['all_assigned_passed']
        assert report['full_builtin_expected_accepted'] is False
        directory=Path(output)/'builtin-recursive-ta-reports'/('compact' if compact else 'full')/path
        write_json(directory/'observations.json',values)
        write_json(directory/'assignments.json',assignments)
        write_json(directory/'report.json',report)


def test_original_uncertain_rows_remain_unassigned():
    manual=read_json(CORPUS.parent/LOCK['manual_table']['path'])
    assert hashlib.sha256((CORPUS.parent/LOCK['manual_table']['path']).read_bytes()).hexdigest()==LOCK['manual_table']['sha256']
    assert len(manual['rows'])==33
    assert LOCK['unverified_ids']==[r['id'] for r in manual['rows'] if r['authority']=='UNVERIFIED']
    assert len(LOCK['unverified_ids'])==13
    assert not any(case['id']==ident+f'-v{version}' for case in MANIFEST['cases'] for ident in LOCK['unverified_ids'] for version in (5,6))
