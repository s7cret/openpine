"""Installed contour only: ordinary Python, no source bootstrap."""
import argparse
import hashlib
import importlib
import importlib.metadata as md
import json
import os
from pathlib import Path
import sys

MODULES = {'openpine': 'openpine', 'openpine-contracts': 'openpine_contracts', 'pine2ast': 'pine2ast', 'ast2python': 'ast2python', 'pinelib': 'pinelib', 'backtest_engine': 'backtest_engine', 'marketdata-provider': 'marketdata_provider', 'optimizer': 'optimizer'}

def origins():
    rows = {}
    for name, module in MODULES.items():
        obj = importlib.import_module(module)
        origin = Path(obj.__file__).resolve()
        dist = md.distribution(name.replace('_', '-'))
        location = Path(dist.locate_file('')).resolve()
        assert origin.is_relative_to(Path(sys.prefix).resolve()), ('source-shadowing', module, str(origin))
        assert origin.is_relative_to(location), ('distribution-shadowing', module)
        direct = dist.read_text('direct_url.json')
        assert not direct or not json.loads(direct).get('dir_info', {}).get('editable'), ('editable', name)
        file_hashes = {}
        for entry in dist.files or []:
            relative = str(entry)
            if relative.startswith(module + '/') and not relative.endswith('.pyc'):
                installed = Path(dist.locate_file(entry)).resolve()
                assert installed.is_relative_to(Path(sys.prefix).resolve())
                file_hashes[str(installed.relative_to(Path(sys.prefix)))] = 'sha256:' + hashlib.sha256(installed.read_bytes()).hexdigest()
        rows[name] = {'files': file_hashes, 'editable': False, 'module': module, 'origin': str(origin), 'distribution_origin': str(location), 'version': dist.version, 'origin_sha256': hashlib.sha256(origin.read_bytes()).hexdigest(), 'direct_url': json.loads(direct) if direct else None}
    return rows

def resource(mode):
    from importlib.resources import files
    from pine2ast.catalog import CatalogRepository
    from openpine.verification.stage2_catalog import _rc6_catalog_source_check
    path = Path(str(files('openpine.verification').joinpath('rc6_catalog_source_pin.json')))
    old = path.read_bytes()
    try:
        if mode == 'missing':
            path.unlink()
        elif mode == 'tampered':
            value = json.loads(old)
            value['pine2ast_ref'] = '0' * 40
            path.write_text(json.dumps(value))
        packs = {v: CatalogRepository().pack(v) for v in range(1, 7)}
        result = _rc6_catalog_source_check(packs)
        assert result['ok'] is (mode == 'positive'), result
        return {'mode': mode, 'result': result, 'path': str(path), 'sha256': hashlib.sha256(old).hexdigest()}
    finally:
        if mode != 'positive':
            path.write_bytes(old)

def runtime(commits, library=False):
    from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
    from openpine.runtime.rc6_executor import RC6RuntimeExecutor
    from pine2ast.libraries import LibraryStore
    from pinelib import RuntimeLanguageContext
    from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext
    source = '//@version=6\nindicator("package")\nx=close+1\nplot(x)\n'
    kwargs = {}
    if library:
        source = '//@version=6\nindicator("package library")\nimport qa/Increment/1 as inc\nx=inc.add(close)\nplot(x)\n'
        kwargs['library_store'] = LibraryStore.create({'qa/Increment/1': '//@version=6\nlibrary("Increment")\nexport add(float value)=>value+1\n'})
    result = NativeRC6CompilerAdapter().compile(source, module_name='package_generated', source_name='package.pine', producer_commits=commits, **kwargs)
    assert result.success, result.errors
    artifact = {k: getattr(result, k) for k in ('generated_artifact', 'python_code', 'consumer_bundle', 'source_map', 'compile_meta')}
    executor = RC6RuntimeExecutor(artifact=artifact, language=RuntimeLanguageContext(6, '2026-09-01', 'pine-v6', 'sha256:' + hashlib.sha256(source.encode()).hexdigest(), 'compiler_annotation'), instrument=InstrumentContext(ticker='SOLUSDT', tickerid='BINANCE:SOLUSDT', prefix='BINANCE', currency='USDT', basecurrency='SOL', timezone='UTC', instrument_type='crypto', mintick=0.01), timeframe=TimeframeContext.parse('1'))
    observed = []
    for i in range(5):
        close = 10.0 + i
        opened = 1700000000000 + i * 60000
        out = executor.execute_bar(BarValues(open=close, high=close+1, low=close-1, close=close, volume=1000, time=opened, time_close=opened+60000), bar_index=i, last_bar_index=4)
        assert out.committed and not out.aborted
        outputs = [key for key in executor.session.series if key.startswith('series:')]
        assert len(outputs) == 1, outputs
        observed.append(executor.session.series[outputs[0]].read(0))
    assert observed == [11.0, 12.0, 13.0, 14.0, 15.0], observed
    if library:
        assert 'qa/Increment/1' in result.generated_artifact['external_library_dependency_hashes']
    return {'bars': 5, 'observed': observed, 'library': library, 'generated_artifact_hash': result.generated_artifact['content_hash'], 'producer_commits': commits}

def api(strict=True):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from openpine.gateway.routes import version
    from openpine.gateway.deps import get_state
    app = FastAPI()
    app.include_router(version.router, prefix='/api')
    app.dependency_overrides[get_state] = lambda: SimpleNamespace(config=SimpleNamespace(data_dir=str(Path.cwd())))
    with TestClient(app) as client:
        response = client.get('/api/version')
    assert response.status_code == 200
    value = response.json()
    print(json.dumps({'api_observed': value}, sort_keys=True), flush=True)
    if strict:
        assert value['stack_conforms'] is True, 'API HTTP200 is not stack conformance'
        assert len(value['modules']) == 8
        assert all(m['installed'] and m['conforms_to_lock'] and m['identity_conforms'] for m in value['modules'])
    return value

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['origins', 'compile', 'runtime', 'library', 'api', 'positive', 'missing', 'tampered'])
    parser.add_argument('--commits', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert 'PYTHONPATH' not in os.environ
    rows = origins()
    from openpine.verification.execution_identity import environment_snapshot
    value = {'schema_id': 'openpine.installed_origins.v1', 'environment': environment_snapshot(), 'origins': rows, 'components': rows, 'python': '.'.join(map(str, sys.version_info[:3])), 'isolated': bool(sys.flags.isolated), 'pythonpath': os.environ.get('PYTHONPATH'), 'cwd': str(Path.cwd()), 'prefix': sys.prefix}
    value['executable_sha256'] = value['environment']['executable_sha256']
    if args.mode in ('compile', 'runtime', 'library'):
        value['semantic'] = runtime(json.loads(args.commits.read_text()), args.mode == 'library')
    elif args.mode == 'api':
        value['api'] = api()
    elif args.mode != 'origins':
        value['resource'] = resource(args.mode)
    args.output.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    if args.mode in ('compile', 'runtime', 'library'):
        print(json.dumps({'bars': value['semantic']['bars'], 'observed': value['semantic']['observed'], 'library': args.mode == 'library'}, sort_keys=True))
    elif args.mode in ('positive', 'missing', 'tampered'):
        print(json.dumps({'mode': args.mode, 'resource_valid': value['resource']['result']['ok'], 'changed_versions': value['resource']['result']['changed_versions']}, sort_keys=True))
    else:
        print(json.dumps(value, sort_keys=True))
