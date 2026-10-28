"""OHLCV, history, missing values and numerical/TA behavior in all six profiles."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from packages.compiler.semantic.specification import ROOT, SpecError, encode, load_snapshot, read_json, sha
from packages.contracts import HISTORICAL_PROFILE_ID, HistoryIR, LiteralIR, OpenPineError, ValueType
from packages.execution.session import Session
from packages.marketdata import data_identity, from_payload, read_bars
from packages.runtime.engine import Runtime
from packages.runtime.history import StorageBudget
from tools.conformance import SERIES_CASES, run_cases
from tools.series_spec import prepare


def source(version, body):
    return f'//@version={version}\n' + ('study' if version < 5 else 'indicator') + f'("Series")\n{body}\n'


def bars(values):
    return from_payload({"schema_version": "openpine.historical-bars.1", "context_id": "unit.series",
        "symbol": "TEST:SERIES", "timeframe": "1",
        "bars": [{"time": i * 60000, "close": value} for i, value in enumerate(values)]})


class SeriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.fixture = read_bars(ROOT / 'conformance/series/ohlcv.json')

    def execute(self, version, body, values=None):
        return self.session.run(source(version, body), 'series.pine',
            data=self.fixture if values is None else bars(values))

    def reject(self, version, body, code):
        with self.assertRaises(OpenPineError) as caught:
            self.execute(version, body)
        self.assertEqual((caught.exception.diagnostic.code, caught.exception.diagnostic.language_version), (code, version))

    def test_independent_corpus_covers_all_five_points_in_every_version(self):
        report = run_cases(self.session, cases_path=SERIES_CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (54, 42))
        required = {'context.open', 'context.high', 'context.low', 'context.volume', 'series.history',
            'numeric.na', 'numeric.nz', 'numeric.min', 'numeric.max', 'numeric.round', 'numeric.int',
            'numeric.float', 'series.sma', 'series.change', 'series.cross', 'series.crossover',
            'series.crossunder', 'operator.binary.%'}
        for version in range(1, 7):
            self.assertTrue(required <= set(report['operations_by_version'][str(version)]))

    def test_close_only_remains_usable_and_missing_market_slots_are_na(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'plot(close)\nplot(open)\nplot(nz(volume))', [10, 20])
            self.assertEqual([o.values for o in result.outputs], [(10.0, 20.0), (None, None), (0.0, 0.0)])

    def test_ohlcv_identity_normalizes_numbers_and_includes_every_slot(self):
        payload = json.loads((ROOT / 'conformance/series/ohlcv.json').read_text())
        same = copy.deepcopy(payload)
        same['bars'][0]['open'] = float(same['bars'][0]['open'])
        self.assertEqual(data_identity(from_payload(payload)), data_identity(from_payload(same)))
        for slot in ('open', 'high', 'low', 'volume'):
            changed = copy.deepcopy(payload)
            changed['bars'][0][slot] += 0.5
            self.assertNotEqual(data_identity(from_payload(changed)).sha256, data_identity(self.fixture).sha256)

    def test_bad_ohlcv_types_ranges_and_price_order_are_rejected(self):
        payload = json.loads((ROOT / 'conformance/series/ohlcv.json').read_text())
        for slot, value in [('open', True), ('high', float('inf')), ('low', '1'), ('volume', -1),
                            ('high', 1), ('low', 50)]:
            changed = copy.deepcopy(payload)
            changed['bars'][0][slot] = value
            with self.subTest(slot=slot, value=value), self.assertRaises(OpenPineError):
                from_payload(changed)
        changed = copy.deepcopy(payload)
        del changed['bars'][0]['volume']
        with self.assertRaises(OpenPineError):
            from_payload(changed)

    def test_variable_history_commits_final_values_and_supports_recurrence(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'var count = 0\ncount := nz(count[1]) + 1\nplot(count)')
            self.assertEqual(result.outputs[0].values, (1, 2, 3, 4, 5))
            _, result = self.execute(version, 'x = close\nprevious = x[1]\nx := x + 1\nplot(nz(previous))\nplot(x[0])')
            self.assertEqual([o.values for o in result.outputs], [(0.0, 11.0, 21.0, 0.0, 31.0), (11.0, 21.0, None, 31.0, 41.0)])
            _, result = self.execute(version, 'x = 5\nplot(nz(x[1]))')
            self.assertEqual(result.outputs[0].values, (0, 5, 5, 5, 5))

    def test_skipped_expressions_have_holes_but_context_history_keeps_all_bars(self):
        for version in range(1, 7):
            _, result = self.execute(version, '''var float seen = 0
var float reference = 0
if bar_index % 2 == 0
    seen := nz((close * 2)[2])
    reference := nz(close[1])
plot(seen)
plot(reference)''')
            self.assertEqual([o.values for o in result.outputs], [(0.0, 0.0, 20.0, 20.0, 0.0), (0.0, 0.0, 20.0, 20.0, 30.0)])

    def test_local_bar_and_persistent_variable_histories_remain_distinct(self):
        for version in range(1, 7):
            _, result = self.execute(version, '''var float ordinary_seen = 0
var float persistent_seen = 0
if bar_index % 2 == 0
    ordinary = close
    var float persistent = close
    persistent := persistent + 1
    ordinary_seen := nz(ordinary[1])
    persistent_seen := nz(persistent[1])
plot(ordinary_seen)
plot(persistent_seen)''')
            self.assertEqual([o.values for o in result.outputs], [(0.0,)*5, (0.0, 0.0, 11.0, 11.0, 12.0)])

    def test_history_of_stateful_calls_does_not_repeat_them(self):
        for version in range(1, 7):
            sma = 'sma' if version < 5 else 'ta.sma'
            _, result = self.execute(version, f'average = {sma}(close, 2)\nplot(average)\nplot(average[1])\nplot({sma}(close, 2)[1])')
            self.assertEqual([o.values for o in result.outputs], [(None, 15.0, 15.0, 25.0, 35.0), (None, None, 15.0, 15.0, 25.0), (None, None, 15.0, 15.0, 25.0)])

    def test_history_offset_zero_dynamic_offsets_and_out_of_history(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'plot(close[0])\nplot(close[int(bar_index % 2)])\nplot(close[100000])')
            self.assertEqual([o.values for o in result.outputs], [(10.0, 20.0, None, 30.0, 40.0), (10.0, 10.0, None, None, 40.0), (None,)*5])
            for body, code in [('plot(close[-1])', 'HISTORY_OFFSET'), ('plot(close[100001])', 'HISTORY_OFFSET'),
                               ('plot(close[1.1])', 'HISTORY_OFFSET_TYPE'), ('plot(close[true])', 'HISTORY_OFFSET_TYPE'),
                               ('plot(close[1][1])', 'HISTORY_CHAIN'), ('plot(1[1])', 'HISTORY_SOURCE'),
                               ('int offset = na\nplot(close[offset])', 'HISTORY_OFFSET')]:
                self.reject(version, body, code)

    def test_missing_bool_history_and_na_nz_follow_the_version_profile(self):
        for version in range(1, 6):
            _, result = self.execute(version, 'flag = close > 15\nplot(na(flag[1]) ? 1 : 0)\nplot(nz(flag[1]) ? 1 : 0)')
            self.assertEqual([o.values for o in result.outputs], [(1, 0, 0, 0, 0), (0, 0, 1, 0, 1)])
        _, result = self.execute(6, 'flag = close > 15\nplot(flag[1] ? 1 : 0)')
        self.assertEqual(result.outputs[0].values, (0, 0, 1, 0, 1))
        self.reject(6, 'plot(na(true) ? 1 : 0)', 'ARGUMENT_TYPE')
        self.reject(6, 'plot(nz(true) ? 1 : 0)', 'ARGUMENT_TYPE')
        self.reject(6, 'bool flag = na\nplot(flag ? 1 : 0)', 'ASSIGNMENT_TYPE')

    def test_legacy_bool_na_logic_uses_three_valued_truth_tables(self):
        for version in range(1, 6):
            _, result = self.execute(version, '''bool missing = na
plot(na(missing and true) ? 1 : 0)
plot((missing and false) ? 1 : 0)
plot((missing or true) ? 1 : 0)
plot(na(not missing) ? 1 : 0)''')
            self.assertEqual([o.values for o in result.outputs], [(1,)*5, (0,)*5, (1,)*5, (1,)*5])

    def test_nz_promotion_defaults_and_eager_replacement(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'plot(nz(na, 3))\nplot(nz(1, 2.5))\nplot(nz(close, 7))\nplot(na(float(na)) ? 1 : 0)')
            self.assertEqual([o.value_type.base for o in result.outputs], ['int', 'float', 'float', 'int'])
            self.assertEqual([o.values for o in result.outputs], [(3,)*5, (1.0,)*5, (10.0,20.0,7.0,30.0,40.0), (1,)*5])
            sma = 'sma' if version < 5 else 'ta.sma'
            _, result = self.execute(version, f'plot(nz(close, {sma}(close, 2)))')
            self.assertEqual(result.outputs[0].values, (10.0,20.0,15.0,30.0,40.0))

    def test_modulo_uses_floor_quotient_and_propagates_missing_values(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'plot(-5 % 3)\nplot(5 % -3)\nplot(-5 % -3)\nplot(5.5 % 2)\nplot(1 % 0)\nplot(close % 3)')
            self.assertEqual([o.values for o in result.outputs], [(1,)*5,(-1,)*5,(-2,)*5,(1.5,)*5,(None,)*5,(1.0,2.0,None,0.0,1.0)])

    def test_rounding_ties_precision_and_integer_exactness(self):
        for version in range(1, 7):
            name = 'round' if version < 5 else 'math.round'
            _, result = self.execute(version, f'plot({name}(-2.5))\nplot({name}(2.5))\nplot({name}(-1.25, 1))\nplot({name}(125, -1))\nplot({name}(9007199254740993))')
            self.assertEqual([o.values for o in result.outputs], [(-2,)*5,(3,)*5,(-1.2,)*5,(130.0,)*5,(9007199254740993,)*5])
            self.reject(version, f'plot({name}(1, 16))', 'ROUND_PRECISION')

    def test_min_max_common_types_and_bounded_arity(self):
        for version in range(1, 7):
            name = '' if version < 5 else 'math.'
            args = ', '.join(str(i) for i in range(32))
            _, result = self.execute(version, f'plot({name}min(3, 1, 2.5))\nplot({name}max({args}))\nplot({name}min(close, 15))')
            self.assertEqual([o.values for o in result.outputs], [(1.0,)*5,(31,)*5,(10.0,15.0,None,15.0,15.0)])
            self.reject(version, f'plot({name}max(1))', 'NO_MATCHING_OVERLOAD')

    def test_casts_preserve_na_and_qualifiers_and_diagnose_range(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'plot(int(-2.9))\nplot(float(3))\nplot(int(close))\nplot(int(na))')
            self.assertEqual([o.values for o in result.outputs], [(-2,)*5,(3.0,)*5,(10,20,None,30,40),(None,)*5])
            self.assertEqual(result.outputs[2].value_type, ValueType('int','series'))
            self.reject(version, 'plot(int(1e30))', 'VALUE_RANGE')

    def test_sma_matches_an_independent_fraction_oracle_with_gaps_and_dynamic_length(self):
        values = [1e16,1,-1e16,None,1,3,5,None,7]
        for version in range(1, 7):
            name = 'sma' if version < 5 else 'ta.sma'
            expected, seen = [], []
            for i, value in enumerate(values):
                if value is not None:
                    seen.append(Fraction(value))
                length = i % 3 + 1
                expected.append(None if len(seen)<length else float(sum(seen[-length:],Fraction())/length))
            _, result = self.execute(version, f'plot({name}(close, int(bar_index % 3 + 1)))', values)
            self.assertEqual(result.outputs[0].values, tuple(expected))
        _, result = self.execute(6, 'plot(ta.sma(close, 2))', [sys.float_info.max,sys.float_info.max])
        self.assertEqual(result.outputs[0].values, (None,sys.float_info.max))

    def test_change_numeric_bool_length_zero_and_included_na(self):
        for version in range(1, 7):
            name = 'change' if version < 5 else 'ta.change'
            _, result = self.execute(version, f'plot({name}(int(close), 2))\nplot({name}(close, 0))\nplot({name}(close > 15) ? 1 : 0)')
            self.assertEqual([o.values for o in result.outputs], [(None,None,None,10,None),(0.0,0.0,None,0.0,0.0),(0,1,1,1,0)])

    def test_crossings_handle_equalities_missing_values_and_first_execution(self):
        values = [10,20,30,20,10,None,30,10]
        for version in range(1, 7):
            prefix = '' if version < 5 else 'ta.'
            _, result = self.execute(version, f'plot({prefix}cross(close, 20) ? 1 : 0)\nplot({prefix}crossover(close, 20) ? 1 : 0)\nplot({prefix}crossunder(close, 20) ? 1 : 0)',values)
            self.assertEqual([o.values for o in result.outputs], [(0,0,1,0,1,0,0,1),(0,0,1,0,0,0,0,0),(0,0,0,0,1,0,0,1)])

    def test_skipped_ta_calls_and_var_initializers_do_not_advance_state(self):
        for version in range(1, 7):
            prefix = '' if version < 5 else 'ta.'
            _, result = self.execute(version, f'''var float seen = 0
if bar_index % 2 == 0
    seen := {prefix}sma(close, 2)
plot(seen)''',[10,20,30,40,50])
            self.assertEqual(result.outputs[0].values,(None,None,20.0,20.0,40.0))

    def test_runs_contexts_and_versions_have_independent_history(self):
        for version in range(1, 7):
            prefix = '' if version < 5 else 'ta.'
            body = f'plot(close[1])\nplot({prefix}sma(close, 2))\nplot({prefix}change(close))'
            _, first = self.execute(version,body)
            _, second = self.execute(version,body)
            self.assertEqual(first,second)
            _, other = self.execute(version,body,[100,200])
            self.assertEqual([o.values for o in other.outputs],[(None,100.0),(None,150.0),(None,100.0)])

    def test_future_data_cannot_change_a_completed_prefix(self):
        body = 'plot(close[1])\nplot(ta.sma(close, 2))\nplot(ta.change(close))'
        _, small = self.execute(6,body,[10,20,30])
        _, large = self.execute(6,body,[10,20,30,1e10,0])
        self.assertEqual([o.values for o in small.outputs],[o.values[:3] for o in large.outputs])

    def test_history_storage_limit_is_diagnostic_and_does_not_poison_next_run(self):
        with patch('packages.runtime.engine.StorageBudget', lambda **kw: StorageBudget(3,**kw)):
            self.reject(6,'plot(close[1])','HISTORY_STORAGE')
        _, result = self.execute(6,'plot(close[1])')
        self.assertEqual(result.outputs[0].values,(None,10.0,20.0,None,30.0))

    def test_new_call_names_and_named_arguments_follow_each_version(self):
        for version in range(1, 7):
            prefix = '' if version < 5 else 'ta.'
            if version < 3:
                self.reject(version, f'plot({prefix}sma(x=close, y=2))', 'NAMED_ARGUMENT_UNVERIFIED')
            else:
                _, result = self.execute(version, f'plot({prefix}sma(length=2, source=close))')
                self.assertEqual(result.outputs[0].values, (None,15.0,15.0,25.0,35.0))
            wrong = 'ta.sma' if version < 5 else 'sma'
            self.reject(version, f'plot({wrong}(close, 2))', 'SIGNATURE_UNAVAILABLE')

    def test_api_symbols_distinguish_functions_and_share_only_overloads(self):
        for version in range(1, 7):
            catalog = self.session.catalog_for(version)
            symbols = {}
            for api in self.session.snapshot.series['versions'][str(version)]['apis']:
                contract = catalog.contracts['overload.' + api['id']]
                symbols.setdefault(api['name'], set()).add(contract.symbol_id)
            self.assertTrue(all(len(ids) == 1 for ids in symbols.values()))
            self.assertEqual(len(set().union(*symbols.values())), len(symbols))

    def test_forged_history_identity_and_type_are_rejected_before_effects(self):
        program,_ = self.execute(6,'plot(close[1])')
        call = program.statements[1]
        history = call.arguments[0].expression
        self.assertIsInstance(history,HistoryIR)
        catalog = self.session.catalog_for(6)
        runtime = Runtime(program.identity,catalog.contracts.values(),catalog.references.values())
        for bad in [replace(history,series_id='reference:open'),replace(history,value_type=ValueType('int','series')),
                    replace(history,offset=LiteralIR(1.0,ValueType('float'),history.offset.span)),
                    replace(history,offset=LiteralIR(-1,ValueType('int'),history.offset.span))]:
            forged = replace(program,statements=(program.statements[0],replace(call,arguments=(replace(call.arguments[0],expression=bad),*call.arguments[1:]))))
            with self.assertRaises(OpenPineError) as caught:
                runtime.prepare(forged)
            self.assertEqual(caught.exception.diagnostic.code,'IR_HISTORY')

    def test_series_bundle_is_reproducible_and_old_bundles_are_unchanged(self):
        directory = ROOT/self.session.snapshot.lock['series_release']['path']
        for name, raw in prepare().items():
            self.assertEqual((directory/name).read_bytes(),raw)
        for name,digest in [('pine-spec-20261020.1','531f85386978d805de3756d2d1c70d748a4cd392c0caad3c4173e8a9d9c6fa06'),
                            ('pine-versions-20261020.1','019cb2a538398fce686c2475860106997959c32095001bb068b4b2c0158b8aca'),
                            ('pine-language-20261021.1','08a8a13a55bf96b1d2a3ddc32d5d1c945c1a9a57dea8e17a194a83ea58a1cb14')]:
            self.assertEqual(sha((ROOT/'spec/bundles'/name/'manifest.json').read_bytes()),digest)

    def changed_spec(self, change):
        parent=ROOT/'.runtime/series-tests'
        parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        shutil.copytree(ROOT/'spec',root/'spec')
        lock=read_json(root/'spec/lock.json')
        directory=root/lock['series_release']['path']
        data=read_json(directory/'series.json')
        change(data)
        (directory/'series.json').write_bytes(encode(data))
        manifest=read_json(directory/'manifest.json')
        manifest['files']['series.json']=sha((directory/'series.json').read_bytes())
        (directory/'manifest.json').write_bytes(encode(manifest))
        lock['series_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if "function_release" in lock:
            function_directory = root / lock["function_release"]["path"]
            function_data = read_json(function_directory / "functions.json")
            function_data.update(base_manifest_sha256=lock["manifest_sha256"],
                version_review_manifest_sha256=lock["version_review"]["manifest_sha256"],
                language_manifest_sha256=lock["language_release"]["manifest_sha256"],
                series_manifest_sha256=lock["series_release"]["manifest_sha256"])
            (function_directory / "functions.json").write_bytes(encode(function_data))
            function_manifest = read_json(function_directory / "manifest.json")
            function_manifest["files"]["functions.json"] = sha((function_directory / "functions.json").read_bytes())
            (function_directory / "manifest.json").write_bytes(encode(function_manifest))
            lock["function_release"]["manifest_sha256"] = sha((function_directory / "manifest.json").read_bytes())
        (root/'spec/lock.json').write_bytes(encode(lock))
        return root

    def test_repinned_wrong_evidence_cannot_become_an_executable_contract(self):
        def change(data):
            apis=data['versions']['6']['apis']
            apis[0]['evidence']=copy.deepcopy(apis[1]['evidence'])
        root=self.changed_spec(change)
        with self.assertRaises(SpecError) as caught:
            load_snapshot(root)
        self.assertEqual(caught.exception.code,'SPEC_PROVENANCE')

    def test_runtime_refuses_a_different_valid_series_policy(self):
        root=self.changed_spec(lambda d:d['versions']['6']['policy'].__setitem__('missing_bool','na'))
        with self.assertRaises(SpecError) as caught:
            Session(load_snapshot(root),profile_id=HISTORICAL_PROFILE_ID)
        self.assertEqual(caught.exception.code,'IMPLEMENTATION_MISSING')

    def test_actual_cli_runs_ohlcv_and_history_on_v1_and_v6(self):
        for version in (1,6):
            process=subprocess.run([sys.executable,'-X','utf8','-m','apps.openpine','run',
                '--source',f'conformance/series/v{version}-history.pine','--bars','conformance/series/ohlcv.json'],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(process.returncode,0,process.stderr+process.stdout)
            payload=json.loads(process.stdout)
            self.assertEqual(payload['result']['outputs'][0]['values'],[0.0,10.0,20.0,0.0,30.0])


if __name__=='__main__':
    unittest.main()
