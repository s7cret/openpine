"""Literal event history through Native admission and existing runtime owners."""
import ast
from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
import hashlib
import importlib
import json
import math
import os
from pathlib import Path

import pytest
from ast2python.artifacts import verify_generated_artifact_v3
from ast2python.lowering import load_pinelib_target_manifest
from pinelib import CallbackFrame, RuntimeLanguageContext, RuntimeSession, na
from pinelib.runtime.metadata import BarValues
from pinelib.state.checkpoint import from_portable

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report
from openpine.verification.conformance import first_difference, load_corpus
from openpine.verification.identity import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'verification/builtin-event-history-v1/manifest.json'
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / 'lock.json')
PATHS = ('abi', 'compiled_historical', 'compiled_realtime', 'compiled_rollback', 'compiled_checkpoint')
assert len(MANIFEST['cases']) == LOCK['case_count'] == 20
assert MANIFEST['content_hash'] == LOCK['content_hash']


def source_identity():
    """Formal pins are verified by coordinated CI; local controls label snapshots."""
    pins = read_json(ROOT / 'docs/RC6_LIFECYCLE_SOURCES.json')
    return {'mode': 'declared_dependency_pins', **{
        name: pins[name] for name in ('pine2ast', 'ast2python', 'pinelib')}}


def record(value, dtype):
    if value is na:
        return {'kind': 'na'}
    assert dtype in ('int', 'float')
    assert type(value) is (int if dtype == 'int' else float)
    assert math.isfinite(value)
    return {'kind': 'value', 'type': dtype, 'value': value}


def declaration_storage(compiled, declaration_line):
    """Read an actual emitted literal selected by the admitted source map.

    This observation adapter neither guesses a storage ID nor evaluates code.
    The genuine emitted module executes unchanged.
    """
    nodes = [row['node_id'] for row in compiled.consumer_bundle['node_index']
             if row['kind'] == 'VarDeclaration' and row['span']['start_line'] == declaration_line]
    assert len(nodes) == 1
    locations = [(entry['python_start']['line'], entry['python_end']['line'])
                 for entry in compiled.source_map['entries'] if entry['source_node_id'] == nodes[0]]
    tree = ast.parse(compiled.python_code)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == 'declare_scalar_v1'
             and any(start <= node.lineno <= end for start, end in locations)]
    assert len(calls) == 1
    literal = calls[0].args[0]
    assert isinstance(literal, ast.Constant) and type(literal.value) is str
    return literal.value


def admit_generated(case, settings, compiled):
    """Validate the actual artifact/binding and build only its observation data."""
    bundle = compiled.consumer_bundle
    verify_generated_artifact_v3(compiled.generated_artifact)
    target = load_pinelib_target_manifest()
    nodes = {row['node_id'] for row in bundle['node_index'] if row['kind'] == 'CallExpr'
             and row['span']['start_line'] == settings['primary_source_line']}
    calls = [row for row in bundle['semantic_facts']['calls'] if row['node_id'] in nodes]
    assert len(calls) == 1
    call = calls[0]
    key = tuple(call[field] for field in ('symbol_id', 'overload_id', 'call_form'))
    assert list(key) == settings['declared_binding']
    assert call['return_type'] == settings['result_type']
    arguments = sorted(call['arguments'], key=lambda row: row['parameter_index'])
    assert arguments[0]['actual_type'] == 'bool' and arguments[0]['actual_qualifier'] == 'series'
    if settings['operation'] == 'valuewhen':
        assert len(arguments) == 3 and settings['parameters'] == [0]
        assert arguments[1]['actual_type'] == 'float' and arguments[1]['actual_qualifier'] == 'series'
        assert arguments[2]['actual_type'] == 'int' and arguments[2]['actual_qualifier'] == 'const'
    else:
        assert len(arguments) == 1 and settings['parameters'] == []
    binding = target.call_bindings[key]
    assert case['pine_version'] in binding.supported_pine_versions
    assert binding.python_module + '.' + binding.python_name == settings['abi_callable']
    storage = {'condition': declaration_storage(compiled, 3),
               'result': declaration_storage(compiled, settings['result_declaration_line'])}
    assert len(set(storage.values())) == 2
    namespace = {}
    exec(compile(compiled.python_code, settings['source_name'] + '.py', 'exec'), namespace)
    function = getattr(importlib.import_module(binding.python_module), binding.python_name)
    return bundle, key, target, storage, namespace, function


@lru_cache(maxsize=None)
def prepare(case_id):
    case = next(row for row in MANIFEST['cases'] if row['id'] == case_id)
    settings = read_json(CORPUS.parent / case['settings']['path'])
    source = (CORPUS.parent / case['source']['path']).read_text(encoding='utf-8')
    identity = source_identity()
    compiled = NativeRC6CompilerAdapter().compile(source, source_name=settings['source_name'],
        producer_commits={name: identity[name] for name in ('pine2ast', 'ast2python')})
    assert compiled.success, compiled.errors
    assert compiled.compile_meta['adapter'] == 'native-rc6-python-library'
    admitted = admit_generated(case, settings, compiled)
    assert compiled.compile_meta['target_manifest_hash'] == admitted[2].content_hash
    return settings, compiled, *admitted, identity


def execute_case(case, path, compact):
    settings, compiled, bundle, key, target, storage, namespace, function, identity = prepare(case['id'])
    data = read_json(CORPUS.parent / case['data']['path'])

    def make_runtime():
        runtime = RuntimeSession(RuntimeLanguageContext(case['pine_version'], 'event-history-manual',
            f"pine-v{case['pine_version']}", compiled.generated_artifact['source_hash'], 'compiler_annotation'))
        runtime.commit_full_identity = not compact
        return runtime

    runtime = make_runtime()
    sequence = 0
    trials, restores, events = [], [], []

    def restore(bar, reason):
        nonlocal runtime
        saved = runtime.checkpoint().to_dict()
        clone = make_runtime()
        clone.restore(json.loads(json.dumps(saved)))
        assert clone.checkpoint().to_dict() == saved
        runtime = clone
        restores.append({'bar': bar, 'reason': reason, 'whole_checkpoint_preserved': True})

    def callback(bar, trial=False, attempt=0):
        nonlocal sequence
        condition, sample = data[bar]['condition'], data[bar]['source']
        assert type(condition) is bool and type(sample) is float
        if trial and path == 'compiled_rollback':
            condition = not condition
            sample += 100.0 if attempt == 0 else -200.0
        opening = 1.0 if condition else -1.0
        realtime = bar > 0 and path in {'compiled_realtime', 'compiled_rollback'}
        tx = runtime.begin(CallbackFrame('REALTIME_TICK' if realtime else 'HISTORICAL_EVAL', sequence,
            bar_index=bar, realtime=realtime, final_tick=not trial), values=BarValues(
                opening, max(opening, sample) + 1, min(opening, sample) - 1, sample, 1,
                bar * 60000, (bar + 1) * 60000 - 1))
        if path == 'abi':
            args = [condition] if settings['operation'] == 'barssince' else [condition, sample, 0]
            value = record(function(tx, 'independent-event-history', *args), settings['result_type'])
        else:
            namespace['GeneratedScript'](tx).run()
            actual_condition = from_portable(runtime.series[storage['condition']].read())
            assert type(actual_condition) is bool and actual_condition is condition
            value = record(from_portable(runtime.series[storage['result']].read()), settings['result_type'])
        if trial and path == 'compiled_rollback':
            tx.abort()
        else:
            tx.commit()
            sequence += 1
        return value

    for bar in range(len(data)):
        if bar > 0 and path in {'compiled_realtime', 'compiled_rollback'}:
            before = deepcopy(runtime.checkpoint().to_dict())
            # Two consecutive aborts independently change condition and source.
            for attempt in range(2 if path == 'compiled_rollback' else 1):
                attempted_sequence = sequence
                trial = callback(bar, True, attempt)
                trials.append({'bar': bar, 'attempt': attempt, 'attempt_sequence': attempted_sequence,
                               'value': trial, 'independent_numeric_expectation': path == 'compiled_realtime'})
                if path == 'compiled_rollback':
                    assert sequence == attempted_sequence and runtime.checkpoint().to_dict() == before
                    restore(bar, 'aborted_same_sequence_retry')
        value = callback(bar)
        if bar > 0 and path == 'compiled_realtime':
            assert trial == value
        events.append({'bar': bar, 'value': value})
        if path == 'compiled_checkpoint':
            restore(bar, 'committed_before_continuation')
    return {'status': 'completed', 'compile': True, 'events': events, 'execution_path': path,
            'transcript_mode': 'compact' if compact else 'full', 'trials': trials, 'restores': restores,
            'executed_bindings': [[case['pine_version'], *key]], 'target_manifest_hash': target.content_hash,
            'catalog_hash': bundle['version_context']['catalog_hash'], 'source_identity': identity,
            'semantic_authority': settings['authority'], 'manual_row_id': settings['manual_row_id'],
            'generated_module_sha256': hashlib.sha256(compiled.python_code.encode()).hexdigest(),
            'artifact_hash': compiled.generated_artifact['content_hash'], 'observation_storage': storage,
            **{field + '_sha256': case[field]['sha256'] for field in ('source', 'data', 'settings')}}


@pytest.mark.parametrize('case', MANIFEST['cases'], ids=lambda case: case['id'])
@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('compact', [False, True], ids=['full', 'compact'])
def test_independent_event_history(case, path, compact, observations):
    actual = execute_case(case, path, compact)
    expected = read_json(CORPUS.parent / case['expected']['path'])
    assert first_difference(expected, {'compile': actual['compile'], 'events': actual['events']}, case['tolerance']) is None
    observations.setdefault((compact, path), {})[case['id']] = actual


@pytest.fixture(scope='module')
def observations():
    rows = {}
    yield rows
    if not (output := os.environ.get('OPENPINE_STAGE1_EVIDENCE')):
        return
    surface = build_builtin_surface()
    indexed = {(r['pine_version'], r['symbol_id'], r['overload_id'], r['call_form']): r for r in surface['rows']}
    write_json(Path(output) / 'builtin-event-history-surface.json', surface)
    for (compact, path), values in rows.items():
        assert len(values) == 20
        assignments = []
        for case_id, value in values.items():
            assert value['semantic_authority'] == 'PRIMARY_DEFINITION_DERIVATION'
            assert len(value['executed_bindings']) == 1
            row = indexed[tuple(value['executed_bindings'][0])]
            assert row['status'] == 'RUNTIME_DIRECT'
            assignments.append({**{field: row[field] for field in
                ('pine_version', 'symbol_id', 'overload_id', 'call_form', 'contract_hash')},
                'case_id': case_id, 'path': path})
        report = builtin_evidence_report(surface, CORPUS, values, corpus_hash=LOCK['content_hash'], assignments=assignments)
        assert report['execution_evidence']['all_assigned_passed']
        assert report['full_builtin_expected_accepted'] is False
        directory = Path(output) / 'builtin-event-history-reports' / ('compact' if compact else 'full') / path
        write_json(directory / 'observations.json', values)
        write_json(directory / 'assignments.json', assignments)
        write_json(directory / 'report.json', report)


def test_original_uncertain_rows_remain_unassigned():
    manual = read_json(CORPUS.parent / LOCK['manual_table']['path'])
    assert hashlib.sha256((CORPUS.parent / LOCK['manual_table']['path']).read_bytes()).hexdigest() == LOCK['manual_table']['sha256']
    gap_path = CORPUS.parent / LOCK['authority_gaps']['path']
    assert hashlib.sha256(gap_path.read_bytes()).hexdigest() == LOCK['authority_gaps']['sha256']
    gaps = read_json(gap_path)
    assert set(gaps) == {'schema_id', 'manual_table', 'original_row_count', 'original_version_cases',
        'original_event_count', 'assigned_row_count', 'assigned_version_cases', 'assigned_events',
        'unverified_rows', 'unverified_version_cases', 'unverified_events', 'preserved_before',
        'numerical_agreement_promotes_authority', 'valuewhen_profile', 'excluded_overloads', 'full_stage2_accepted'}
    assert len(manual['rows']) == gaps['original_row_count'] == 14
    assert gaps['unverified_rows'] == [row for row in manual['rows'] if row['authority'] == 'UNVERIFIED']
    assert len(gaps['unverified_rows']) == 4
    assert gaps['unverified_version_cases'] == 8 and gaps['unverified_events'] == 64
    assert gaps['assigned_version_cases'] == 20 and gaps['assigned_events'] == 160
    assert not gaps['numerical_agreement_promotes_authority'] and not gaps['full_stage2_accepted']
    assert all(row['expected'] is None for row in gaps['unverified_rows'])
    assert not any(case['id'] == row['id'] + f'-v{version}' for case in MANIFEST['cases']
                   for row in gaps['unverified_rows'] for version in (5, 6))
    for descriptor in LOCK['evidence']:
        assert hashlib.sha256((CORPUS.parent / descriptor['path']).read_bytes()).hexdigest() == descriptor['sha256']
    for descriptor in gaps['preserved_before']:
        before = read_json(CORPUS.parent / descriptor['path'])
        assert len(before['records']) == 28
        assert not before['numerical_agreement_promotes_authority']


def test_exact_literal_source_data_and_result_types():
    manual = read_json(CORPUS.parent / LOCK['manual_table']['path'])
    original = {row['id']: row for row in manual['rows']}
    for case in MANIFEST['cases']:
        settings = read_json(CORPUS.parent / case['settings']['path'])
        row = original[settings['manual_row_id']]
        data = read_json(CORPUS.parent / case['data']['path'])
        expected = read_json(CORPUS.parent / case['expected']['path'])
        assert [bar['condition'] for bar in data] == row['condition']
        assert all(type(bar['condition']) is bool and type(bar['source']) is float for bar in data)
        assert len(expected['events']) == len(row['expected']) == 8
        for item, frozen in zip(expected['events'], row['expected']):
            if frozen['kind'] == 'na':
                assert item['value'] == {'kind': 'na'}
            else:
                assert item['value']['type'] == frozen['type'] == settings['result_type']
                assert type(item['value']['value']) is (int if frozen['type'] == 'int' else float)
                assert Fraction(str(item['value']['value'])) == Fraction(frozen['rational'])


@pytest.mark.parametrize('dtype,value', [('int', True), ('int', 1.0), ('float', 1), ('float', False),
    ('int', None), ('float', None), ('float', float('nan')), ('float', float('inf'))])
def test_typed_observer_rejects_wrong_values(dtype, value):
    with pytest.raises(AssertionError):
        record(value, dtype)
